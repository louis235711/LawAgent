from src.config import settings
from src.database.redis import get_session, update_session, create_session
from src.database.message_repo import save_message as pg_save_message
from src.llm.client import chat_completion
from src.utils.token_counter import count_tokens, count_message_tokens
from loguru import logger

SUMMARY_MAX_LEN = 20
SUMMARY_OUTPUT_TOKENS = 2048
KEEP_RECENT_MESSAGES = 4  # 至少保留最后 2 轮（4条）原文

SUMMARY_PROMPT = """请对以下对话进行摘要，需包含：
1. 用户在这几轮的主要内容（核心诉求、关键事实、案件背景等）
2. AI 给出的核心法律意见（引用法条、裁判观点、处理建议等）

{history}
"""


def _estimate_message_tokens(msg: dict) -> int:
    content = msg.get("content", "")
    return count_tokens(content) if content else 0


async def add_message(session_id: str, role: str, content: str, message_type: str = "咨询", references: list[dict] | None = None) -> int:
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

    pg_save_message(session_id, role, content, token_count, message_type, references)
    return token_count


async def get_short_term_memory(session_id: str) -> list[dict]:
    data = await get_session(session_id)
    if data is None:
        return []
    return data.get("short_term_memory", [])


def _calc_window_tokens(data: dict) -> int:
    total = 0
    for s in data.get("summary_list", []):
        total += count_tokens(s)
    for msg in data.get("short_term_memory", []):
        total += msg.get("token_count", 0)
    return total


async def check_and_summarize(session_id: str) -> bool:
    """Check if summary is needed. Returns True if summarization occurred."""
    data = await get_session(session_id)
    if data is None:
        return False

    window_tokens = data.get("window_token_count", 0)
    threshold = int(settings.max_context_tokens * settings.summary_trigger_ratio)

    if window_tokens < threshold:
        return False

    memory = data.get("short_term_memory", [])
    if len(memory) <= KEEP_RECENT_MESSAGES:
        return False

    # 按 token 量取消息：从头部累计直到达到 MIN_BATCH_TOKENS，或取到剩 KEEP_RECENT_MESSAGES 条
    min_batch = settings.min_batch_tokens
    cumulative = 0
    take = 0
    max_take = len(memory) - KEEP_RECENT_MESSAGES

    for i, msg in enumerate(memory):
        if cumulative >= min_batch:
            break
        cumulative += msg.get("token_count", 0)
        take = i + 1
        if take >= max_take:
            break

    if take == 0:
        return False

    messages_to_summarize = memory[:take]
    remaining = memory[take:]

    history_text = _format_messages(messages_to_summarize)
    prompt = SUMMARY_PROMPT.format(history=history_text)

    try:
        summary = await chat_completion(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=SUMMARY_OUTPUT_TOKENS,
        )
    except Exception as e:
        logger.error(f"Summary generation failed: {e}")
        return False

    summary_list = data.get("summary_list", [])
    summary_list.append(summary)
    if len(summary_list) > SUMMARY_MAX_LEN:
        summary_list.pop(0)
        logger.info(f"Session {session_id}: summary list full, dropped oldest")

    data["summary_list"] = summary_list
    data["short_term_memory"] = remaining
    data["window_token_count"] = _calc_window_tokens(data)
    await update_session(session_id, **data)
    logger.info(f"Session {session_id}: summarized {take} messages ({cumulative} tokens) → "
                f"summary #{len(summary_list)}, remaining {len(remaining)} msgs, "
                f"window={data['window_token_count']} tokens")
    return True


CONTEXT_INSTRUCTION = (
    "【重要提示】以下消息分为多个部分：\n"
    "1. 历史摘要（各阶段对话要点的压缩回顾）\n"
    "2. 近期对话回顾（最近的对话原文）\n"
    "3. 当前最新问题（这是你需要回答的核心）\n"
    "请以「当前最新问题」为首要任务展开回答，历史信息仅作为背景补充，不要喧宾夺主。"
)


async def assemble_context(
    session_id: str,
    system_prompt: str,
    current_prompt: str,
) -> list[dict]:
    """Assemble the full context for LLM call, respecting max token window."""
    data = await get_session(session_id)
    if data is None:
        data = await create_session(session_id)

    system_content = system_prompt + "\n\n" + CONTEXT_INSTRUCTION
    messages = [{"role": "system", "content": system_content}]

    summary_list = data.get("summary_list", [])
    for i, s in enumerate(summary_list, 1):
        messages.append({"role": "system", "content": f"【历史摘要—第{i}阶段】\n{s}"})

    short_term = data.get("short_term_memory", [])
    if short_term:
        memory_block = "【近期对话回顾】\n"
        for msg in short_term:
            role_label = "用户" if msg["role"] == "user" else "AI"
            memory_block += f"{role_label}: {msg['content']}\n"
        messages.append({"role": "system", "content": memory_block})

    messages.append({"role": "user", "content": f"【当前最新问题】\n{current_prompt}"})

    total = count_message_tokens(messages)
    max_tokens = settings.max_context_tokens

    if total > max_tokens:
        system_count = 1 + len(summary_list)
        kept = messages[:system_count]
        conversation = messages[system_count:-1]
        current_msg = messages[-1]

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
