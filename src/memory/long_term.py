import os
import asyncio
from src.config import settings
from src.llm.client import chat_completion
from loguru import logger

EXTRACT_PROMPT = """你是一个用户偏好分析助手。请从以下对话中提炼用户的偏好和背景信息。

## 提炼维度

请在输出中分两个部分：

### feedback_style（用户反馈与风格偏好）
需记录：输出格式偏好（表格/列表/简洁/详细/Markdown等）、用户明确纠正过你的错误行为、用户对交互方式的要求。
格式每条一行：`- 描述`

### user_role（用户角色与知识背景）
需记录：用户身份/角色、关注的法律领域、专业知识背景、常用文书类型等。
格式每条一行：`- 描述`

## 重要规则
- **仅记录对话中用户明确体现的信息**，不得推测
- **禁止**输出"用户没有…""用户未…""用户未提出…"等否定或空值描述 — 无信息时该部分只输出"无"
- 每部分至多 5 条，简洁即可
- 不要输出任何解释性文字

## 对话内容
{conversation}

## 提取结果"""

MERGE_PROMPT = """你是用户偏好记录管理助手。请将新旧记录合并为一份无冲突、无冗余的列表。

## 现有记录
{existing}

## 新追加内容
{new_items}

## 合并规则（严格执行）
1. **冲突检测与替换**：如果新旧记录描述同一类信息但内容矛盾（如旧"网络安全学院学生" vs 新"文学院学生"），以**新内容为准**，删除旧记录
2. **去重**：字面相同或表述高度相似的条目只保留一条
3. **保留规则**：旧记录中与新内容不冲突、不重复的所有条目**必须完整保留**，不得删除
4. **合并**：同一话题的多条记录可合并为一条更完整的描述
5. 每条一行：`- 描述`，至多 20 条
6. 不要输出任何解释性文字

## 合并后的记录"""


def load_long_term_memory(user_id: int) -> str:
    """Read both feedback_style and user_role for a user, return concatenated for system prompt."""
    parts = []
    for path in settings.memory_paths_for_user(user_id):
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                if content:
                    parts.append(content)
            except Exception as e:
                logger.warning(f"Failed to load memory {path}: {e}")
    return "\n\n".join(parts)


def _load_file(path: str) -> str:
    """Read a single memory file, return empty string if missing."""
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def _save_file(path: str, content: str):
    """Write merged content to a memory file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content.strip() + "\n")


def _parse_extraction(raw: str) -> tuple[str, str]:
    """Parse LLM extraction output into (feedback_style, user_role)."""
    feedback = ""
    role = ""
    current_section = None
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        lower = line.lower()
        if lower.startswith("### feedback") or lower.startswith("## feedback"):
            current_section = "feedback"
            continue
        elif lower.startswith("### user") or lower.startswith("## user"):
            current_section = "role"
            continue
        elif lower.startswith("###") or lower.startswith("##"):
            current_section = None
            continue

        if current_section == "feedback" and line.startswith("-"):
            feedback += line + "\n"
        elif current_section == "role" and line.startswith("-"):
            role += line + "\n"
    return feedback.strip(), role.strip()


async def _extract_preferences(conversation_text: str) -> tuple[str, str]:
    """Call LLM to extract user preferences from conversation.
    Returns (feedback_style, user_role) — each can be empty string.
    """
    prompt = EXTRACT_PROMPT.format(conversation=conversation_text)
    try:
        result = await chat_completion(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=512,
        )
        return _parse_extraction(result.strip())
    except Exception as e:
        logger.error(f"Preference extraction failed: {e}")
        return "", ""


async def _append_and_dedup(file_path: str, new_items: str):
    """Append new items and immediately merge-conflict-resolve with existing."""
    existing = _load_file(file_path)

    if not new_items.strip():
        return

    if not existing:
        _save_file(file_path, new_items)
        logger.info(f"Memory file created: {file_path}")
        return

    # Always merge with LLM to detect and resolve conflicts
    try:
        prompt = MERGE_PROMPT.format(existing=existing, new_items=new_items)
        result = await chat_completion(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=512,
        )
        merged = result.strip()
        if merged:
            _save_file(file_path, merged)
            logger.info(f"Memory merged ({file_path})")
        else:
            combined = existing + "\n" + new_items
            _save_file(file_path, combined)
    except Exception as e:
        logger.error(f"Memory merge failed: {e}, falling back to raw append")
        combined = existing + "\n" + new_items
        _save_file(file_path, combined)


async def update_long_term_memory(conversation_text: str, user_id: int):
    """Extract preferences from conversation and append to user-specific memory files.

    Append strategy: new items are added to existing files.
    LLM deduplication only triggers when a file exceeds the threshold.
    Old content is never removed unless explicitly conflicting with new.
    """
    if not conversation_text.strip():
        return

    fb_path, role_path = settings.memory_paths_for_user(user_id)

    try:
        feedback, role = await _extract_preferences(conversation_text)

        # Filter out "无" — the LLM's way of saying "no info"
        if feedback and feedback != "无":
            await _append_and_dedup(fb_path, feedback)
        if role and role != "无":
            await _append_and_dedup(role_path, role)
    except Exception as e:
        logger.error(f"Long-term memory update failed for user {user_id}: {e}")


def schedule_memory_update(conversation_text: str, user_id: int):
    """Fire-and-forget wrapper: schedule memory update without blocking the caller."""
    if not conversation_text.strip():
        return

    async def _run():
        await update_long_term_memory(conversation_text, user_id)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_run())
    except RuntimeError:
        try:
            asyncio.run(_run())
        except Exception:
            pass
