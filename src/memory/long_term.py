import os
import asyncio
from src.config import settings
from src.llm.client import chat_completion
from loguru import logger

EXTRACT_PROMPT = """你是一个用户偏好分析助手。请从以下对话中提炼用户的偏好和背景信息，要求简洁。

## 提炼维度
1. 用户身份/角色（如：律师、法务、学生、普通用户等）
2. 关注的法律领域（如：合同法、劳动法、知识产权、婚姻法等）
3. 使用习惯（如：偏好简洁回答/详细分析、偏好表格/列表呈现、常用文书类型等）
4. 其他值得记录的偏好或背景信息

## 规则
- 仅记录对话中明确体现的信息，不得推测
- 每条一行，格式：`- 描述`
- 如果对话中没有体现某个维度的信息，直接跳过
- 不要输出任何解释性文字，只输出偏好列表

## 对话内容
{conversation}

## 用户偏好
"""

MERGE_PROMPT = """你是一个用户偏好管理助手。请将新旧偏好整合为一份简洁的记录。

## 现有偏好记录
{existing}

## 新提炼的偏好
{new_prefs}

## 整合规则
- 新旧偏好冲突时，以新内容为准（替换旧内容）
- 相同领域的偏好合并为一条
- 保持简洁，每条一行，格式：`- 描述`
- 不要输出任何解释性文字，只输出整合后的偏好列表
- 如果现有记录为空，直接输出新偏好

## 整合后的偏好
"""


def load_long_term_memory() -> str:
    """Read the persisted user preferences memory file."""
    path = settings.memory_path
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception as e:
        logger.warning(f"Failed to load long-term memory: {e}")
        return ""


def _save_long_term_memory(content: str):
    """Write merged preferences to disk."""
    path = settings.memory_path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content.strip() + "\n")
        logger.info(f"Long-term memory saved ({len(content)} chars)")
    except Exception as e:
        logger.error(f"Failed to save long-term memory: {e}")


async def _extract_preferences(conversation_text: str) -> str:
    """Call LLM to extract user preferences from conversation."""
    prompt = EXTRACT_PROMPT.format(conversation=conversation_text)
    try:
        result = await chat_completion(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=512,
        )
        return result.strip()
    except Exception as e:
        logger.error(f"Preference extraction failed: {e}")
        return ""


async def _merge_with_existing(new_prefs: str) -> str:
    """Merge newly extracted preferences with existing memory.md."""
    existing = load_long_term_memory()

    if not new_prefs:
        return existing

    if not existing:
        return new_prefs

    prompt = MERGE_PROMPT.format(existing=existing, new_prefs=new_prefs)
    try:
        result = await chat_completion(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=512,
        )
        return result.strip()
    except Exception as e:
        logger.error(f"Memory merge failed: {e}")
        # Fallback: keep existing unchanged
        return existing


async def update_long_term_memory(conversation_text: str):
    """Full pipeline: extract preferences from conversation, merge with existing, save.

    This is best-effort — failures are logged but never raised.
    """
    if not conversation_text.strip():
        return

    try:
        new_prefs = await _extract_preferences(conversation_text)
        if not new_prefs:
            return
        merged = await _merge_with_existing(new_prefs)
        if merged:
            _save_long_term_memory(merged)
    except Exception as e:
        logger.error(f"Long-term memory update failed: {e}")


def schedule_memory_update(conversation_text: str):
    """Fire-and-forget wrapper: schedule memory update without blocking the caller."""
    if not conversation_text.strip():
        return

    async def _run():
        await update_long_term_memory(conversation_text)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_run())
    except RuntimeError:
        # No running event loop — run synchronously in a new one
        try:
            asyncio.run(_run())
        except Exception:
            pass
