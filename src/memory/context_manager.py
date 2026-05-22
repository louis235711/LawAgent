import uuid
import time
from src.config import settings
from src.database.redis import get_session, update_session, create_session
from src.database.message_repo import save_message as pg_save_message
from src.llm.client import chat_completion
from src.utils.token_counter import count_tokens, count_message_tokens
from loguru import logger

SUMMARY_MAX_LEN = 20
SUMMARY_OUTPUT_TOKENS = 2048
KEEP_RECENT_TURNS = 3  # tier 1: keep last N turns intact (full ReAct trajectory)
MAX_TURNS = 20          # tier 3+: compress turns beyond this

SUMMARY_PROMPT = """请对以下对话轮次进行摘要。每轮包含用户问题、AI思考过程、工具调用和结果、最终回答。

## 摘要要求
1. 用户核心诉求和关键事实
2. AI 思考的主要方向（从 thinking 提炼思路要点，不需要原文）
3. 使用的工具和获取的关键信息（保留法条名+条款号）
4. AI 最终给出的核心结论和建议

注意：thinking 只提炼思路要点，不需要原文照录。但法条引用必须保留具体名称和条款号。

{history}
"""

AGGREGATE_PROMPT = """请将以下连续对话轮次按主题聚合为一个精简轮次。

## 要求
1. 用户输入：合并所有用户问题为一段连贯描述
2. 最终回答：合并所有AI回答的关键结论，去除重复，保留法条引用（法律名+条款号）
3. 不输出thinking、工具调用细节

## 原始轮次
{turns_data}

## 输出格式
第一行：用户问题汇总（一段话）
第二行：---
第三行：AI回答汇总（保留法条引用的完整段落）"""

CONTEXT_INSTRUCTION = (
    "【重要提示】以下消息分为多个部分：\n"
    "1. 历史摘要（各阶段对话要点的压缩回顾）\n"
    "2. 近期对话回顾（最近的对话轮次原文）\n"
    "3. 当前最新问题（这是你需要回答的核心）\n"
    "请以「当前最新问题」为首要任务展开回答，历史信息仅作为背景补充。"
)


# ── Turn ID generation ───────────────────────────────────────

def new_turn_id() -> str:
    return uuid.uuid4().hex[:8]


# ── Memory entry (enhanced structure) ────────────────────────

async def add_memory_entry(
    user_id: int,
    session_id: str,
    role: str,
    content: str,
    turn_id: str,
    step_type: str,
    tool_name: str = "",
    message_type: str = "咨询",
) -> int:
    """Append a structured memory entry to short-term memory and PostgreSQL."""
    data = await get_session(user_id, session_id)
    if data is None:
        data = await create_session(user_id, session_id)

    token_count = count_tokens(content)
    msg = {
        "role": role,
        "content": content,
        "token_count": token_count,
        "turn_id": turn_id,
        "step_type": step_type,
    }
    if tool_name:
        msg["tool_name"] = tool_name

    data["short_term_memory"].append(msg)
    data["window_token_count"] = _calc_window_tokens(data)
    data["last_active_at"] = int(time.time())
    await update_session(user_id, session_id, **data)

    pg_save_message(session_id, role, content, token_count, message_type)
    return token_count


async def save_turn(
    user_id: int,
    session_id: str,
    turn_id: str,
    entries: list[dict],
):
    """Save a complete turn trajectory to memory at once.

    entries: list of {role, content, step_type, tool_name?}
    """
    data = await get_session(user_id, session_id)
    if data is None:
        data = await create_session(user_id, session_id)

    for entry in entries:
        # user_input already saved to Redis by add_memory_entry — skip to avoid duplicates
        if entry["step_type"] == "user_input":
            continue
        token_count = count_tokens(entry.get("content", ""))
        msg = {
            "role": entry["role"],
            "content": entry.get("content", ""),
            "token_count": token_count,
            "turn_id": turn_id,
            "step_type": entry["step_type"],
        }
        if entry.get("tool_name"):
            msg["tool_name"] = entry["tool_name"]
        data["short_term_memory"].append(msg)

    data["window_token_count"] = _calc_window_tokens(data)
    await update_session(user_id, session_id, **data)

    # Persist final answer to PostgreSQL (user_input already saved by add_memory_entry)
    for entry in entries:
        if entry["step_type"] == "final_answer":
            pg_save_message(
                session_id,
                entry["role"],
                entry.get("content", ""),
                count_tokens(entry.get("content", "")),
                _step_type_to_message_type(entry["step_type"], entry.get("tool_name", "")),
                references=entry.get("references", []),
                metadata=entry.get("metadata", {}),
            )

    logger.debug(f"[MEM] turn {turn_id} saved: {len(entries)} entries")


# ── Backward-compatible add_message ──────────────────────────

async def add_message(
    user_id: int,
    session_id: str,
    role: str,
    content: str,
    message_type: str = "咨询",
    references: list[dict] | None = None,
    metadata: dict | None = None,
) -> int:
    """Legacy wrapper — used by old agent paths."""
    turn_id = new_turn_id()
    step_type = "user_input" if role == "user" else "final_answer"
    return await add_memory_entry(
        user_id, session_id, role, content, turn_id, step_type,
        message_type=message_type,
    )


# ── Anthropic context assembly ───────────────────────────────

async def assemble_anthropic_context(
    user_id: int,
    session_id: str,
    system_prompt: str,
    current_input: str,
) -> tuple[str, list[dict]]:
    """Assemble (system, messages) for Anthropic messages.create().

    Converts Redis memory entries to Anthropic content-block format.
    Preserves thinking blocks for round-tripping (required by DeepSeek).
    """
    data = await get_session(user_id, session_id)
    if data is None:
        data = await create_session(user_id, session_id)

    system = system_prompt + "\n\n" + CONTEXT_INSTRUCTION

    # Inject summaries as system content
    summary_list = data.get("summary_list", [])
    for i, s in enumerate(summary_list, 1):
        system += f"\n\n【历史摘要—第{i}阶段】\n{s}"

    messages = []
    turns = _group_by_turn(data.get("short_term_memory", []))

    if not turns:
        messages.append({"role": "user", "content": f"【当前问题】\n{current_input}"})
        return system, messages

    # All completed turns → Anthropic messages
    for turn in turns:
        turn_msgs = _turn_to_anthropic_messages(turn)
        messages.extend(turn_msgs)

    # Append current user input
    messages.append({"role": "user", "content": f"【当前问题】\n{current_input}"})

    # Token window truncation
    total = _estimate_anthropic_tokens(system, messages)
    max_tokens = settings.max_context_tokens

    if total > max_tokens:
        logger.warning(f"[MEM] context overflow: {total} > {max_tokens}, truncating")
        # Drop oldest turns until within limit
        while len(messages) >= 2 and _estimate_anthropic_tokens(system, messages) > max_tokens:
            # Remove the oldest turn (user + assistant pair)
            if messages[0]["role"] == "user" and len(messages) > 1:
                messages.pop(0)  # user
                if messages and messages[0]["role"] == "assistant":
                    messages.pop(0)  # assistant
                # Also pop the tool_result user message
                if messages and messages[0]["role"] == "user" and _is_tool_result(messages[0]):
                    messages.pop(0)
            else:
                messages.pop(0)

    return system, messages


def _is_tool_result(msg: dict) -> bool:
    """Check if a user-role message is actually tool results."""
    content = msg.get("content", [])
    if isinstance(content, list):
        return any(
            (isinstance(b, dict) and b.get("type") == "tool_result")
            for b in content
        )
    return False


def _estimate_anthropic_tokens(system: str, messages: list[dict]) -> int:
    """Rough token estimate for Anthropic-format messages."""
    total = count_tokens(system)
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += count_tokens(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    text = block.get("text") or block.get("thinking") or ""
                    total += count_tokens(text)
                    if block.get("type") == "tool_use":
                        total += count_tokens(str(block.get("input", {})))
                    if block.get("type") == "tool_result":
                        total += count_tokens(str(block.get("content", "")))
    return total


def _group_by_turn(memory: list[dict]) -> list[list[dict]]:
    """Group memory entries by turn_id, preserving order."""
    turns: list[list[dict]] = []
    current_turn: list[dict] = []
    current_tid = None

    for msg in memory:
        tid = msg.get("turn_id", "")
        if tid != current_tid:
            if current_turn:
                turns.append(current_turn)
            current_turn = [msg]
            current_tid = tid
        else:
            current_turn.append(msg)

    if current_turn:
        turns.append(current_turn)

    return turns


def _turn_to_anthropic_messages(turn: list[dict]) -> list[dict]:
    """Convert a turn's Redis memory entries to Anthropic message format.

    IMPORTANT: thinking blocks MUST be preserved for round-tripping.
    DeepSeek Anthropic endpoint returns 400 if they are stripped.
    """
    messages = []
    user_input_text = ""
    assistant_content_blocks = []
    tool_result_blocks = []

    # Track unmatched tool_use blocks by name for proper pairing
    unmatched_tool_uses: list[dict] = []

    for entry in turn:
        step = entry.get("step_type", "")
        role = entry.get("role", "")
        content = entry.get("content", "")

        if step == "user_input":
            user_input_text = content
        elif step == "thinking":
            assistant_content_blocks.append({
                "type": "thinking",
                "thinking": content,
            })
        elif step == "tool_call":
            tool_name = entry.get("tool_name", "")
            input_parsed = {}
            if content:
                try:
                    import re
                    inner = content.split("(", 1)
                    if len(inner) == 2:
                        args_str = inner[1].rstrip(")")
                        for pair in re.findall(r"(\w+)=('[^']*'|\"[^\"]*\"|\S+)", args_str):
                            k, v = pair
                            input_parsed[k] = v.strip("'\"")
                except Exception:
                    pass
            block = {
                "type": "tool_use",
                "id": f"toolu_{entry.get('turn_id', 'x')}_{len(assistant_content_blocks)}",
                "name": tool_name,
                "input": input_parsed,
            }
            assistant_content_blocks.append(block)
            unmatched_tool_uses.append(block)
        elif step == "observation":
            tool_name = entry.get("tool_name", "")
            # Match by order: first unmatched tool_use with matching name
            matching_id = ""
            for i, tu in enumerate(unmatched_tool_uses):
                if tu.get("name") == tool_name:
                    matching_id = tu["id"]
                    unmatched_tool_uses.pop(i)
                    break
            tool_result_blocks.append({
                "type": "tool_result",
                "tool_use_id": matching_id,
                "content": content,
            })
        elif step == "final_answer":
            assistant_content_blocks.append({
                "type": "text",
                "text": content,
            })

    # Cleanup: remove orphan tool_use blocks that have no matching tool_result
    # (can happen with data saved before FIFO matching was fixed)
    if unmatched_tool_uses:
        orphan_ids = {tu["id"] for tu in unmatched_tool_uses}
        logger.warning(f"[MEM] cleaning {len(orphan_ids)} orphan tool_use blocks: {orphan_ids}")
        assistant_content_blocks = [
            b for b in assistant_content_blocks
            if not (b.get("type") == "tool_use" and b["id"] in orphan_ids)
        ]
    # Also cleanup orphan tool_result blocks (no matching tool_use)
    valid_ids = {b["id"] for b in assistant_content_blocks if b.get("type") == "tool_use"}
    orphan_results = [tr for tr in tool_result_blocks if tr.get("tool_use_id") not in valid_ids]
    if orphan_results:
        logger.warning(f"[MEM] cleaning {len(orphan_results)} orphan tool_result blocks")
    tool_result_blocks = [
        tr for tr in tool_result_blocks
        if tr.get("tool_use_id") in valid_ids
    ]

    # Build message sequence
    if user_input_text:
        messages.append({"role": "user", "content": user_input_text})

    if not assistant_content_blocks:
        return messages

    # Separate blocks by type for structural validation
    thinking_blocks = [b for b in assistant_content_blocks if b.get("type") == "thinking"]
    tool_use_blocks_in_msg = [b for b in assistant_content_blocks if b.get("type") == "tool_use"]
    text_blocks = [b for b in assistant_content_blocks if b.get("type") == "text"]

    # DeepSeek requires: tool_use → tool_result → text (three separate messages).
    # If we mix tool_use and text in one assistant message, the API returns 400.
    if tool_use_blocks_in_msg and text_blocks:
        messages.append({"role": "assistant", "content": thinking_blocks + tool_use_blocks_in_msg})
        if tool_result_blocks:
            messages.append({"role": "user", "content": tool_result_blocks})
        messages.append({"role": "assistant", "content": text_blocks})
    else:
        messages.append({"role": "assistant", "content": assistant_content_blocks})
        if tool_result_blocks:
            messages.append({"role": "user", "content": tool_result_blocks})

    return messages


# ── Multi-tier turn compression ──────────────────────────────

async def check_and_summarize(user_id: int, session_id: str) -> bool:
    """Multi-tier turn compression after each turn.

    Tier 1 (recent 3 turns): full ReAct trajectory
    Tier 2 (turns 4-10):     strip thinking entries
    Tier 3 (turns 10-20):    LLM summary per turn
    Tier 4 (turns 20+):      aggregate 10 oldest turns into 1
    """
    data = await get_session(user_id, session_id)
    if data is None:
        return False

    memory = data.get("short_term_memory", [])
    turns = _group_by_turn(memory)
    total = len(turns)

    if total <= KEEP_RECENT_TURNS:
        return False

    changed = False

    # ── Tier 4: aggregate oldest 10 turns into 1 ──
    if total > MAX_TURNS:
        oldest = turns[:10]
        remaining = turns[10:]
        formatted = _format_turns_for_summary(oldest)
        try:
            result = await chat_completion(
                messages=[{"role": "user", "content": AGGREGATE_PROMPT.format(turns_data=formatted)}],
                temperature=0.3,
                max_tokens=1024,
            )
            agg_turn_id = new_turn_id()
            parts = result.strip().split("\n---\n", 1)
            user_text = parts[0].strip() if parts else result.strip()
            answer_text = parts[1].strip() if len(parts) > 1 else ""
            agg_turn = [
                {"role": "user", "content": user_text, "token_count": count_tokens(user_text),
                 "turn_id": agg_turn_id, "step_type": "user_input"},
            ]
            if answer_text:
                agg_turn.append(
                    {"role": "ai", "content": answer_text, "token_count": count_tokens(answer_text),
                     "turn_id": agg_turn_id, "step_type": "final_answer"},
                )
            turns = agg_turn if not remaining else [agg_turn] + remaining
            changed = True
            logger.info(f"[MEM] aggregated 10 oldest turns → 1 (session={session_id})")
        except Exception as e:
            logger.error(f"Turn aggregation failed: {e}")

    # ── Tier 3: summarize turns 10-20 from end ──
    total = len(turns)
    if total > MAX_TURNS:
        to_summarize = turns[:total - MAX_TURNS]
        to_keep = turns[total - MAX_TURNS:]
        formatted = _format_turns_for_summary(to_summarize)
        try:
            summary = await chat_completion(
                messages=[{"role": "user", "content": SUMMARY_PROMPT.format(history=formatted)}],
                temperature=0.3,
                max_tokens=SUMMARY_OUTPUT_TOKENS,
            )
            summary_list = data.get("summary_list", [])
            summary_list.append(summary)
            if len(summary_list) > SUMMARY_MAX_LEN:
                summary_list.pop(0)
            data["summary_list"] = summary_list

            # Store to Milvus (fire-and-forget)
            _store_summary_to_milvus(user_id, session_id, summary)

            turns = to_keep
            changed = True
            logger.info(f"[MEM] summarized {len(to_summarize)} turns → summary #{len(summary_list)} (session={session_id})")
        except Exception as e:
            logger.error(f"Turn summarization failed: {e}")

    # ── Tier 2: strip thinking from turns 4-10 from end ──
    total = len(turns)
    if total > KEEP_RECENT_TURNS:
        tier2_end = max(KEEP_RECENT_TURNS, total - 10)
        for i in range(KEEP_RECENT_TURNS, tier2_end):
            original_len = len(turns[i])
            turns[i] = [m for m in turns[i] if m.get("step_type") != "thinking"]
            if len(turns[i]) < original_len:
                changed = True

    if changed:
        new_memory = []
        for turn in turns:
            new_memory.extend(turn)
        data["short_term_memory"] = new_memory
        data["window_token_count"] = _calc_window_tokens(data)
        await update_session(user_id, session_id, **data)

    return changed


def _store_summary_to_milvus(user_id: int, session_id: str, summary: str):
    """Store structured summary to Milvus session_memory (non-blocking)."""
    try:
        import asyncio
        from src.vector_db.milvus_client import get_collection, SESSION_MEMORY_COLLECTION
        from src.llm.embedding import embed_text

        async def _do_store():
            vec = await embed_text(summary[:2000])
            coll = get_collection(SESSION_MEMORY_COLLECTION)
            coll.insert([{
                "user_id": user_id,
                "session_id": session_id,
                "summary_text": summary[:65000],
                "topic": "",
                "turn_range": "",
                "created_at": int(time.time()),
                "vector": vec,
            }])
            coll.flush()

        loop = asyncio.get_running_loop()
        loop.create_task(_do_store())
    except Exception as e:
        logger.warning(f"[MEM] failed to store summary to Milvus: {e}")


def _format_turns_for_summary(turns: list[list[dict]]) -> str:
    """Format turns for the summary prompt. Thinking is reduced to key points."""
    parts = []
    for i, turn in enumerate(turns, 1):
        lines = [f"## 轮次 {i}"]
        for msg in turn:
            step = msg.get("step_type", "")
            role = msg.get("role", "")
            content = msg.get("content", "")
            tool_name = msg.get("tool_name", "")

            if step == "user_input":
                lines.append(f"用户问题: {content}")
            elif step == "thinking":
                # Truncate thinking for summary — keep max 200 chars
                truncated = content[:200] + "..." if len(content) > 200 else content
                lines.append(f"AI思考: {truncated}")
            elif step == "tool_call":
                lines.append(f"调用工具 {tool_name}")
            elif step == "observation":
                # Truncate long tool results
                truncated = content[:500] + "..." if len(content) > 500 else content
                lines.append(f"工具结果: {truncated}")
            elif step == "final_answer":
                truncated = content[:300] + "..." if len(content) > 300 else content
                lines.append(f"最终回答: {truncated}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


# ── Legacy context assembly (backward compat) ────────────────

async def assemble_context(
    user_id: int,
    session_id: str,
    system_prompt: str,
    current_prompt: str,
) -> list[dict]:
    """Legacy context assembly for old agents. Returns OpenAI-format messages."""
    data = await get_session(user_id, session_id)
    if data is None:
        data = await create_session(user_id, session_id)

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
            content = msg.get("content", "")
            memory_block += f"{role_label}: {content}\n"
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


# ── Helpers ──────────────────────────────────────────────────

def _calc_window_tokens(data: dict) -> int:
    total = 0
    for s in data.get("summary_list", []):
        total += count_tokens(s)
    for msg in data.get("short_term_memory", []):
        total += msg.get("token_count", 0)
    return total


def _step_type_to_message_type(step_type: str, tool_name: str) -> str:
    if step_type == "final_answer":
        if "generate" in tool_name:
            return "文书"
        if "case" in tool_name:
            return "案情"
        if "document" in tool_name or "read_document" in tool_name:
            return "文档"
        return "咨询"
    if step_type == "user_input":
        return "咨询"
    return "其他"


def get_short_term_memory(user_id: int, session_id: str) -> list[dict]:
    """Legacy compatibility."""
    import asyncio
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(_get_memory_async(user_id, session_id))


async def _get_memory_async(user_id: int, session_id: str) -> list[dict]:
    data = await get_session(user_id, session_id)
    if data is None:
        return []
    return data.get("short_term_memory", [])


def _format_messages(msgs: list[dict]) -> str:
    lines = []
    for m in msgs:
        role = "用户" if m["role"] == "user" else "AI"
        lines.append(f"{role}: {m['content']}")
    return "\n".join(lines)
