from src.config import settings
from src.database.redis import get_session, update_session, create_session
from src.database.message_repo import save_message as pg_save_message
from src.llm.client import chat_completion
from src.utils.token_counter import count_tokens, count_message_tokens
from loguru import logger


SUMMARY_PROMPT = """请对以下对话历史进行简洁摘要，保留关键法律信息（案件要素、引用法条、核心观点等）。
限制在 {max_tokens} 个 Token 以内。

对话历史：
{history}
"""


def _estimate_message_tokens(msg: dict) -> int:
    content = msg.get("content", "")
    return count_tokens(content) if content else 0


async def add_message(session_id: str, role: str, content: str, message_type: str = "咨询") -> int:
    """Append a message to short-term memory and persist to PostgreSQL."""
    data = await get_session(session_id)
    if data is None:
        data = await create_session(session_id)

    token_count = count_tokens(content)
    msg = {
        "role": role,
        "content": content,
        "token_count": token_count,
    }
    data["short_term_memory"].append(msg)
    data["window_token_count"] = _calc_window_tokens(data)
    await update_session(session_id, **data)

    # Persist to PostgreSQL
    pg_save_message(session_id, role, content, token_count, message_type)
    return token_count


async def get_short_term_memory(session_id: str) -> list[dict]:
    data = await get_session(session_id)
    if data is None:
        return []
    return data.get("short_term_memory", [])


def _calc_window_tokens(data: dict) -> int:
    total = count_tokens(data.get("summary_memory", "") or "")
    for msg in data.get("short_term_memory", []):
        total += msg.get("token_count", 0)
    return total


async def check_and_summarize(session_id: str) -> bool:
    """Check if summary is needed. Returns True if summarization occurred."""
    data = await get_session(session_id)
    if data is None:
        return False

    window_tokens = data.get("window_token_count", 0)
    max_tokens = settings.max_context_tokens
    threshold = int(max_tokens * settings.summary_trigger_ratio)

    if window_tokens < threshold:
        return False

    memory = data.get("short_term_memory", [])
    if len(memory) < 2:
        return False

    summary_rounds = settings.summary_rounds
    # Take first N user+ai message pairs
    messages_to_summarize = memory[: summary_rounds * 2]
    remaining = memory[summary_rounds * 2 :]

    if not messages_to_summarize:
        return False

    history_text = _format_messages(messages_to_summarize)
    # Dynamic token limit: larger for longer conversations,在512-2048之间
    summary_max_tokens = min(2048, max(512, int(window_tokens * 0.1)))
    prompt = SUMMARY_PROMPT.format(max_tokens=summary_max_tokens, history=history_text)

    try:
        summary = await chat_completion(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=summary_max_tokens,
        )
    except Exception as e:
        logger.error(f"Summary generation failed: {e}")
        return False

    data["summary_memory"] = summary
    data["short_term_memory"] = remaining
    data["window_token_count"] = _calc_window_tokens(data)
    await update_session(session_id, **data)
    logger.info(f"Session {session_id}: summarized {len(messages_to_summarize)} messages, "
                f"remaining {len(remaining)}, window={data['window_token_count']} tokens")
    return True


CONTEXT_INSTRUCTION = (
    "【重要提示】以下消息分为两部分：\n"
    "1. 历史记忆（仅供回顾参考）\n"
    "2. 当前最新问题（这是你需要回答的核心）\n"
    "请以「当前最新问题」为首要任务展开回答，历史记忆仅作为背景补充，不要喧宾夺主。"
)


async def assemble_context(
    session_id: str,
    system_prompt: str,
    current_prompt: str,
) -> list[dict]:
    """Assemble the full context for LLM call, respecting max token window.

    Context hierarchy (priority desc):
      1. Current user prompt — the primary task
      2. Recent short-term memory — conversation review
      3. Summary memory (if any) — older conversation recap
      4. System prompt — role instruction
    """
    data = await get_session(session_id)
    if data is None:
        data = await create_session(session_id)

    # Build system prompt with role + context instructions
    system_content = system_prompt + "\n\n" + CONTEXT_INSTRUCTION
    messages = [{"role": "system", "content": system_content}]

    # Add summary as supplementary memory
    summary = data.get("summary_memory", "")
    if summary:
        messages.append({"role": "system", "content": f"【历史记忆—早期对话摘要】\n{summary}"})

    # Add recent short-term memory as review
    short_term = data.get("short_term_memory", [])
    if short_term:
        memory_block = "【历史记忆—近期对话回顾】\n"
        for msg in short_term:
            role_label = "用户" if msg["role"] == "user" else "AI"
            memory_block += f"{role_label}: {msg['content']}\n"
        messages.append({"role": "system", "content": memory_block})

    # Current user prompt — the primary task
    messages.append({"role": "user", "content": f"【当前最新问题】\n{current_prompt}"})

    # Truncate if over max tokens (sliding window: keep system prompts, trim oldest memory first)
    total = count_message_tokens(messages)
    max_tokens = settings.max_context_tokens

    if total > max_tokens:
        # Keep first system message (role + instruction) and current user prompt
        # Trim from the middle (summary + short-term memory)
        system_count = 2 if summary else 1
        kept = messages[:system_count]  # system role + summary
        conversation = messages[system_count:-1]  # short-term memory (exclude current prompt)
        current_msg = messages[-1]  # current user prompt

        while conversation and count_message_tokens(kept + conversation + [current_msg]) > max_tokens:
            if len(conversation) >= 2:
                conversation = conversation[2:]
            else:
                conversation = conversation[1:]

        messages = kept + conversation + [current_msg]

    return messages


def _format_messages(msgs: list[dict]) -> str:
    lines = []
    for m in msgs:
        role = "用户" if m["role"] == "user" else "AI"
        lines.append(f"{role}: {m['content']}")
    return "\n".join(lines)
