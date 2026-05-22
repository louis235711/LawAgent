"""Background scheduler — scans for idle sessions and triggers memory extraction."""

import asyncio
import time
from src.database.redis import list_idle_sessions, delete_session
from src.memory.long_term import update_long_term_memory
from loguru import logger

IDLE_TIMEOUT = 900  # 15 minutes in seconds
SCAN_INTERVAL = 60  # check every 60 seconds

_running = False


async def _process_idle_session(user_id: int, session_id: str, data: dict):
    """Extract preferences and structured summary from an idle session, then delete it."""
    memory = data.get("short_term_memory", [])
    if not memory:
        await delete_session(user_id, session_id)
        return

    # Build conversation text for preference extraction
    lines = []
    for msg in memory:
        role = "用户" if msg.get("role") == "user" else "AI"
        content = msg.get("content", "")
        if content:
            lines.append(f"{role}: {content}")

    conversation_text = "\n".join(lines)

    try:
        await update_long_term_memory(conversation_text, user_id)
        logger.info(f"[SCHED] preferences extracted for user={user_id} session={session_id}")
    except Exception as e:
        logger.error(f"[SCHED] preference extraction failed: {e}")

    # Generate structured summary for Milvus
    try:
        await _generate_session_summary(user_id, session_id, memory)
    except Exception as e:
        logger.error(f"[SCHED] summary generation failed: {e}")

    # Delete the idle session
    await delete_session(user_id, session_id)
    logger.info(f"[SCHED] idle session deleted: user={user_id} session={session_id}")


async def _generate_session_summary(user_id: int, session_id: str, memory: list[dict]):
    """Generate a structured summary of the session and store in Milvus."""
    import time as _time
    from src.llm.client import chat_completion
    from src.vector_db.milvus_client import get_collection, SESSION_MEMORY_COLLECTION
    from src.llm.embedding import embed_text

    # Build conversation text (truncate to avoid token limits)
    lines = []
    total_chars = 0
    for msg in memory:
        step = msg.get("step_type", "")
        if step == "thinking":
            continue  # skip thinking for summary
        content = msg.get("content", "")
        if not content:
            continue
        role = "用户" if msg.get("role") == "user" else "AI"
        entry = f"{role}: {content[:500]}"
        lines.append(entry)
        total_chars += len(entry)
        if total_chars > 8000:
            break

    conversation = "\n".join(lines)

    prompt = f"""请对以下对话生成一段结构化摘要（200-400字），用于跨会话长期记忆。

## 要求
1. 用户核心诉求
2. 涉及的法律领域和关键法条（保留法律名+条款号）
3. AI给出的核心结论和建议
4. 用户的关键事实信息

## 对话内容
{conversation}

## 结构化摘要"""

    summary = await chat_completion(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=512,
    )

    if not summary.strip():
        return

    # Embed and store to Milvus
    vec = await embed_text(summary[:2000])
    coll = get_collection(SESSION_MEMORY_COLLECTION)
    coll.insert([{
        "user_id": user_id,
        "session_id": session_id,
        "summary_text": summary[:65000],
        "topic": "",
        "turn_range": "",
        "created_at": int(_time.time()),
        "vector": vec,
    }])
    coll.flush()
    logger.info(f"[SCHED] session summary stored to Milvus: user={user_id} session={session_id}")


async def _scan_loop():
    """Background loop: scan for idle sessions every SCAN_INTERVAL seconds."""
    global _running
    _running = True
    logger.info(f"[SCHED] idle session scanner started (timeout={IDLE_TIMEOUT}s, interval={SCAN_INTERVAL}s)")

    while _running:
        try:
            idle_sessions = await list_idle_sessions(IDLE_TIMEOUT)
            for user_id, session_id, data in idle_sessions:
                logger.info(f"[SCHED] processing idle session: user={user_id} session={session_id}")
                asyncio.create_task(_process_idle_session(user_id, session_id, data))
        except Exception as e:
            logger.error(f"[SCHED] scan error: {e}")

        await asyncio.sleep(SCAN_INTERVAL)


def start_scheduler():
    """Start the background idle session scanner."""
    asyncio.create_task(_scan_loop())
    logger.info("[SCHED] scheduler started")


def stop_scheduler():
    """Stop the background scanner."""
    global _running
    _running = False
    logger.info("[SCHED] scheduler stopped")
