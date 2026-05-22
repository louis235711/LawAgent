"""Dispatcher — thin pass-through to ReActAgent.

No more intent classification. The ReActAgent decides what to do.
"""

from src.agents.base import AgentResponse
from src.agents.react_agent import ReActAgent, _build_system_prompt
from src.database.redis import get_session, touch_session
from src.memory.context_manager import (
    add_memory_entry, save_turn, new_turn_id,
    check_and_summarize, assemble_anthropic_context,
)
from src.memory.long_term import load_long_term_memory
from loguru import logger

SYSTEM_BASE = """你是一个专业的法律AI助手，服务于中国法律体系，为用户提供法律咨询、案情分析、文书撰写、合同审查等服务。

## 全局约束

- 仅基于中国现行有效法律法规回答。
- 绝对禁止编造：不得虚构法条名称、条文编号、司法解释文号、案例名称、判决结果。
- 引用法条时必须标注具体法律名称及条款号（格式：《民法典》第XXX条），便于用户核实。
- 对不确定或超出知识范围的问题，必须明确告知"此问题建议咨询持证律师"或"当前未检索到相关法条"，不得猜测或编造。
- 对已废止或可能不再适用的法规，需主动提示用户注意时效性。"""


def _build_full_system_prompt(user_id: int, has_document: bool, doc_name: str = "") -> str:
    """Build the complete system prompt with long-term memory and ReAct instructions."""
    base = SYSTEM_BASE
    memory_md = load_long_term_memory(user_id)
    if memory_md:
        base += "\n\n## 用户偏好（长期记忆）\n" + memory_md

    react_part = _build_system_prompt(has_document, doc_name)
    return base + "\n\n" + react_part


class DispatcherAgent:
    """Routes user input to ReActAgent."""

    def __init__(self):
        self.react_agent = ReActAgent()

    async def dispatch(
        self,
        user_id: int,
        session_id: str,
        user_input: str,
    ) -> AgentResponse:
        turn_id = new_turn_id()

        # 1. Persist user message
        await add_memory_entry(user_id, session_id, "user", user_input, turn_id, "user_input")

        # 2. Check session document state + heartbeat
        session = await get_session(user_id, session_id)
        has_document = session.get("has_document", False) if session else False
        doc_name = session.get("document_name", "") if session else ""

        # 3. Build system prompt
        system = _build_full_system_prompt(user_id, has_document, doc_name)
        logger.info(f"[DISPATCH] user={user_id} session={session_id} has_doc={has_document}")

        # 4. Check summarization before context assembly
        await check_and_summarize(user_id, session_id)

        # 5. Assemble Anthropic context
        system, messages = await assemble_anthropic_context(user_id, session_id, system, user_input)
        logger.debug(f"[DISPATCH] context: system={len(system)} chars, {len(messages)} messages")

        # 6. Execute ReAct
        response = await self.react_agent.execute(
            session_id, user_input, system, messages, turn_id, user_id,
        )

        # 7. Save turn trajectory
        await save_turn(user_id, session_id, turn_id, _build_turn_entries(user_input, response))

        response.metadata["agent"] = "react_agent"
        return response

    async def dispatch_stream(
        self,
        user_id: int,
        session_id: str,
        user_input: str,
    ):
        """Streaming dispatch. Yields text chunks, status dicts, and AgentResponse."""
        turn_id = new_turn_id()

        # 1. Persist user message
        await add_memory_entry(user_id, session_id, "user", user_input, turn_id, "user_input")

        # 2. Check session document state
        session = await get_session(user_id, session_id)
        has_document = session.get("has_document", False) if session else False
        doc_name = session.get("document_name", "") if session else ""

        # 3. Build system prompt
        system = _build_full_system_prompt(user_id, has_document, doc_name)
        logger.info(f"[DISPATCH] user={user_id} session={session_id} has_doc={has_document} stream=true")

        # 4. Check summarization
        if await check_and_summarize(user_id, session_id):
            yield {"status": "summarizing"}

        # 5. Assemble Anthropic context
        system, messages = await assemble_anthropic_context(user_id, session_id, system, user_input)

        # 6. Stream from ReAct
        thinking_parts = []
        tool_events = []
        full_text_parts = []
        final_response = None

        async for item in self.react_agent.stream_execute(
            session_id, user_input, system, messages, turn_id, user_id,
        ):
            if isinstance(item, AgentResponse):
                final_response = item
            elif isinstance(item, dict):
                if item.get("type") == "thinking_delta":
                    thinking_parts.append(item.get("thinking", ""))
                elif item.get("status") in ("tool_call", "tool_result"):
                    tool_events.append(item)
                yield item
            else:
                full_text_parts.append(item)
                yield item

        if final_response is None:
            final_response = AgentResponse(
                content="".join(full_text_parts),
                metadata={"message_type": "其他"},
            )

        # 7. Save turn trajectory
        entries = _build_turn_entries_stream(
            user_input, thinking_parts, tool_events, final_response,
        )
        await save_turn(user_id, session_id, turn_id, entries)

        final_response.metadata["agent"] = "react_agent"
        yield final_response


def _build_turn_entries(user_input: str, response: AgentResponse) -> list[dict]:
    """Build memory entries for a non-streaming turn."""
    entries = [
        {"role": "user", "content": user_input, "step_type": "user_input"},
        {
            "role": "ai",
            "content": response.content,
            "step_type": "final_answer",
            "references": response.references,
            "metadata": response.metadata,
        },
    ]
    return entries


def _build_turn_entries_stream(
    user_input: str,
    thinking_parts: list[str],
    tool_events: list[dict],
    response: AgentResponse,
) -> list[dict]:
    """Build memory entries for a streaming turn with full ReAct trajectory."""
    entries = [
        {"role": "user", "content": user_input, "step_type": "user_input"},
    ]

    thinking_full = "".join(thinking_parts) if thinking_parts else ""

    if thinking_full:
        entries.append({
            "role": "ai",
            "content": thinking_full,
            "step_type": "thinking",
        })

    tool_names = []
    for evt in tool_events:
        if evt.get("status") == "tool_call":
            tool_name = evt.get("tool", "")
            inp = evt.get("input", {})
            if tool_name not in tool_names:
                tool_names.append(tool_name)
            entries.append({
                "role": "ai",
                "content": f"{tool_name}({_format_args(inp)})",
                "step_type": "tool_call",
                "tool_name": tool_name,
            })
        elif evt.get("status") == "tool_result":
            tool_name = evt.get("tool", "")
            entries.append({
                "role": "tool",
                "content": evt.get("summary", ""),
                "step_type": "observation",
                "tool_name": tool_name,
            })

    # Embed ReAct trajectory into metadata for frontend persistence
    enhanced_meta = dict(response.metadata) if response.metadata else {}
    enhanced_meta["thinking"] = thinking_full
    enhanced_meta["tools_used"] = enhanced_meta.get("tools_used", tool_names)

    if response.content:
        entries.append({
            "role": "ai",
            "content": response.content,
            "step_type": "final_answer",
            "references": response.references,
            "metadata": enhanced_meta,
        })

    return entries


def _format_args(d: dict) -> str:
    parts = []
    for k, v in d.items():
        parts.append(f"{k}={repr(v)}")
    return ", ".join(parts)
