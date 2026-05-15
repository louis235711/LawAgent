"""ReAct Agent — autonomous thinking + tool_use loop.

Inspired by Claude Code architecture:
- Each iteration: LLM thinks (explicit thinking block) → decides tool_use or text
- Thinking is visible to user (collapsible in frontend)
- Full ReAct trajectory saved in short-term memory per turn
"""

import json
from collections.abc import AsyncGenerator
from typing import Union

from src.agents.base import BaseAgent, AgentResponse
from src.llm.client import chat_completion_react, chat_completion_react_stream
from src.tools.registry import TOOLS, execute_tool, make_tool_result
from loguru import logger

MAX_ITERATIONS = 8
THINKING_BUDGET = 2048

SYSTEM_PROMPT = """你是专业法律AI助手。你可以自主思考（thinking）、使用工具、综合回答。

## 工作方式
1. 先 thinking 分析用户需求，规划步骤：需要什么信息？需要调用哪些工具？
2. 调用必要的工具获取信息
3. 评估工具结果 → 不够继续查，够了立即给出最终回答

## 工具使用原则
- 法律条文咨询 → 必须先 search_laws 再回答
- 案情分析 → search_laws + search_cases
- 文书撰写/合同起草/文档生成 → 必须调用 generate_document，不要自己直接输出文档内容
- 文档提问 → 先 search_documents（片段检索）
- 合同审查 → read_document_full（全文读取后逐条分析）
- 寒暄/简单追问/闲聊 → 直接回答，不调工具
- 即使生成的是 Markdown 格式也必须用 generate_document 工具
- 工具结果充分后立即给出最终回答，不要无意义循环

## 回答要求
- 引用法条必须标注名称和条款号（格式：《民法典》第XXX条）
- 不确定时诚实告知，不得编造法条或案例
- 用通俗语言解释法律概念
- 给出具体可操作的建议

## 当前会话状态
{session_state}"""


def _build_system_prompt(has_document: bool, doc_name: str = "") -> str:
    """Build system prompt with session state injected."""
    if has_document:
        state = f"用户已上传文档: {doc_name or '是'}。可以使用 search_documents 检索文档内容，或使用 read_document_full 读取全文进行审查。"
    else:
        state = "用户未上传文档。文档相关工具（search_documents, read_document_full）不可用。"
    return SYSTEM_PROMPT.format(session_state=state)


class ReActAgent(BaseAgent):
    def __init__(self):
        super().__init__("react_agent")

    # ── Non-streaming execute ────────────────────────────────

    async def execute(
        self,
        session_id: str,
        user_input: str,
        system: str,
        messages: list[dict],
        turn_id: str,
    ) -> AgentResponse:
        logger.info(f"[REACT] execute session={session_id} turn={turn_id}")

        all_refs = []
        tool_results_for_memory = []
        doc_gen_meta: dict[str, str] = {}

        for iteration in range(1, MAX_ITERATIONS + 1):
            logger.info(f"[REACT] iteration {iteration}/{MAX_ITERATIONS}")

            try:
                response = await chat_completion_react(
                    messages=messages,
                    system=system,
                    tools=TOOLS,
                    thinking_budget=THINKING_BUDGET,
                )
            except Exception as e:
                logger.error(f"ReAct LLM failed at iteration {iteration}: {e}")
                return AgentResponse(
                    content="抱歉，AI推理服务暂时不可用，请稍后重试。",
                    metadata={"error": str(e), "message_type": "其他"},
                )

            content_blocks = response.content
            tool_use_blocks = [b for b in content_blocks if b.type == "tool_use"]
            text_blocks = [b for b in content_blocks if b.type == "text"]

            if tool_use_blocks:
                # Collect assistant blocks for messages
                assistant_content = _serialize_content_blocks(content_blocks)

                # Execute tools sequentially
                tool_result_blocks = []
                for tb in tool_use_blocks:
                    tool_name = tb.name
                    tool_input = tb.input if isinstance(tb.input, dict) else {}

                    logger.info(f"[REACT] tool_call: {tool_name}({_truncate_args(tool_input)})")

                    result_text = await execute_tool(tool_name, tool_input, session_id)

                    # Parse generate_document structured result
                    if tool_name == "generate_document":
                        try:
                            parsed = json.loads(result_text)
                            doc_gen_meta["download_url"] = parsed.get("download_url", "")
                            doc_gen_meta["filename"] = parsed.get("filename", "")
                            result_text = parsed.get("display_text", result_text)
                        except (json.JSONDecodeError, TypeError):
                            pass

                    summary = _summarize_result(tool_name, result_text)
                    logger.info(f"[REACT] tool_result: {tool_name} → {summary}")

                    tr = make_tool_result(tb.id, result_text)
                    tool_result_blocks.append(tr)
                    tool_results_for_memory.append({
                        "tool_name": tool_name,
                        "input": tool_input,
                        "result_summary": summary,
                    })

                    # Extract references
                    if tool_name == "search_laws":
                        all_refs.extend(_extract_law_refs(result_text))
                    elif tool_name == "search_cases":
                        all_refs.extend(_extract_case_refs(result_text))

                # Append to message history for next iteration
                messages.append({"role": "assistant", "content": assistant_content})
                messages.append({"role": "user", "content": tool_result_blocks})
                continue

            # No tool_use → final answer
            final_text = "\n".join(b.text for b in text_blocks if hasattr(b, "text"))
            if not final_text:
                final_text = "抱歉，我无法生成有效回答，请换个方式提问。"

            logger.info(f"[REACT] final_answer: {len(final_text)} chars after {iteration} iterations")
            return AgentResponse(
                content=final_text,
                references=all_refs,
                metadata={
                    "message_type": _infer_message_type(all_refs, tool_results_for_memory),
                    "iterations": iteration,
                    "tools_used": [t["tool_name"] for t in tool_results_for_memory],
                    **doc_gen_meta,
                },
            )

        # MAX_ITERATIONS reached — force final answer
        logger.warning(f"[REACT] max iterations ({MAX_ITERATIONS}) reached, forcing final answer")
        messages.append({
            "role": "user",
            "content": "请基于以上所有检索结果，给出最终的法律意见回答。不要再调用工具。",
        })
        try:
            response = await chat_completion_react(
                messages=messages,
                system=system,
                tools=[],  # no tools → force text
                thinking_budget=1024,
            )
            text_blocks = [b for b in response.content if b.type == "text"]
            final_text = "\n".join(b.text for b in text_blocks if hasattr(b, "text"))
        except Exception as e:
            logger.error(f"ReAct force-final failed: {e}")
            final_text = "抱歉，处理超时。请简化您的问题或稍后重试。"

        return AgentResponse(
            content=final_text or "抱歉，无法完成处理。",
            references=all_refs,
            metadata={
                "message_type": _infer_message_type(all_refs, tool_results_for_memory),
                "iterations": MAX_ITERATIONS,
                "forced": True,
                "tools_used": [t["tool_name"] for t in tool_results_for_memory],
                **doc_gen_meta,
            },
        )

    # ── Streaming execute ────────────────────────────────────

    async def stream_execute(
        self,
        session_id: str,
        user_input: str,
        system: str,
        messages: list[dict],
        turn_id: str,
    ) -> AsyncGenerator[Union[str, dict, AgentResponse], None]:
        logger.info(f"[REACT] stream_execute session={session_id} turn={turn_id}")

        all_refs = []
        tool_results_for_memory = []
        doc_gen_meta: dict[str, str] = {}

        for iteration in range(1, MAX_ITERATIONS + 1):
            logger.info(f"[REACT] stream iteration {iteration}/{MAX_ITERATIONS}")

            try:
                stream = chat_completion_react_stream(
                    messages=messages,
                    system=system,
                    tools=TOOLS,
                    thinking_budget=THINKING_BUDGET,
                )
            except Exception as e:
                logger.error(f"ReAct LLM stream failed at iteration {iteration}: {e}")
                yield AgentResponse(
                    content="抱歉，AI推理服务暂时不可用，请稍后重试。",
                    metadata={"error": str(e), "message_type": "其他"},
                )
                return

            content_blocks = []
            tool_use_blocks = []
            has_text = False

            async for event in stream:
                etype = event.get("type")

                if etype == "thinking_delta":
                    yield event  # → frontend thinking fold
                elif etype == "text_delta":
                    has_text = True
                    yield event["text"]  # → frontend text chunk
                elif etype == "tool_use":
                    tool_use_blocks.append(event)
                    # Yield tool_call status
                    yield {
                        "status": "tool_call",
                        "tool": event["name"],
                        "input": event.get("input", {}),
                    }
                elif etype == "done":
                    content_blocks = event.get("content_blocks", [])

            if has_text and not tool_use_blocks:
                # This was the final answer
                text_blocks = [b for b in content_blocks if b.get("type") == "text"]
                final_text = "\n".join(b.get("text", "") for b in text_blocks)
                if not final_text:
                    final_text = "抱歉，我无法生成有效回答，请换个方式提问。"

                logger.info(f"[REACT] stream final_answer: {len(final_text)} chars after {iteration} iterations")
                yield AgentResponse(
                    content=final_text,
                    references=all_refs,
                    metadata={
                        "message_type": _infer_message_type(all_refs, tool_results_for_memory),
                        "iterations": iteration,
                        "tools_used": [t["tool_name"] for t in tool_results_for_memory],
                        **doc_gen_meta,
                    },
                )
                return

            # Has tool_use blocks — execute them
            if tool_use_blocks:
                # Build assistant content from collected blocks
                assistant_content = _serialize_content_blocks_plain(content_blocks)

                tool_result_blocks = []
                for tb in tool_use_blocks:
                    tool_name = tb["name"]
                    tool_input = tb.get("input", {})

                    logger.info(f"[REACT] tool_call: {tool_name}({_truncate_args(tool_input)})")

                    result_text = await execute_tool(tool_name, tool_input, session_id)

                    # Parse generate_document structured result
                    if tool_name == "generate_document":
                        try:
                            parsed = json.loads(result_text)
                            doc_gen_meta["download_url"] = parsed.get("download_url", "")
                            doc_gen_meta["filename"] = parsed.get("filename", "")
                            result_text = parsed.get("display_text", result_text)
                        except (json.JSONDecodeError, TypeError):
                            pass

                    summary = _summarize_result(tool_name, result_text)
                    logger.info(f"[REACT] tool_result: {tool_name} → {summary}")

                    tr = make_tool_result(tb["id"], result_text)
                    tool_result_blocks.append(tr)
                    tool_results_for_memory.append({
                        "tool_name": tool_name,
                        "input": tool_input,
                        "result_summary": summary,
                    })

                    # Yield tool_result status
                    yield {
                        "status": "tool_result",
                        "tool": tool_name,
                        "summary": summary,
                    }

                    # Extract references
                    if tool_name == "search_laws":
                        all_refs.extend(_extract_law_refs(result_text))
                    elif tool_name == "search_cases":
                        all_refs.extend(_extract_case_refs(result_text))

                # Append to message history
                messages.append({"role": "assistant", "content": assistant_content})
                messages.append({"role": "user", "content": tool_result_blocks})
                continue

            # No tool_use, no text — shouldn't happen, but handle gracefully
            logger.warning(f"[REACT] iteration {iteration}: no tool_use and no text, breaking")
            break

        # MAX_ITERATIONS reached
        logger.warning(f"[REACT] max iterations ({MAX_ITERATIONS}) forced final")
        messages.append({
            "role": "user",
            "content": "请基于以上所有检索结果，给出最终的法律意见回答。不要再调用工具。",
        })
        try:
            async for event in chat_completion_react_stream(
                messages=messages, system=system, tools=[], thinking_budget=1024,
            ):
                if event.get("type") == "text_delta":
                    yield event["text"]
                elif event.get("type") == "thinking_delta":
                    yield event
        except Exception as e:
            logger.error(f"ReAct force-final stream failed: {e}")
            yield "抱歉，处理超时。请简化您的问题或稍后重试。"

        yield AgentResponse(
            content="",  # content was already streamed
            references=all_refs,
            metadata={
                "message_type": _infer_message_type(all_refs, tool_results_for_memory),
                "iterations": MAX_ITERATIONS,
                "forced": True,
                "tools_used": [t["tool_name"] for t in tool_results_for_memory],
                **doc_gen_meta,
            },
        )


# ── Helpers ──────────────────────────────────────────────────

def _serialize_content_blocks(blocks) -> list[dict]:
    """Convert Anthropic SDK content block objects to plain dicts for message history."""
    result = []
    for b in blocks:
        d = {"type": b.type}
        if b.type == "thinking":
            d["thinking"] = b.thinking
        elif b.type == "text":
            d["text"] = b.text
        elif b.type == "tool_use":
            d["id"] = b.id
            d["name"] = b.name
            d["input"] = b.input
        result.append(d)
    return result


def _serialize_content_blocks_plain(blocks: list[dict]) -> list[dict]:
    """Already-plain-dict blocks (from stream collection), just ensure proper format."""
    result = []
    for b in blocks:
        d = {"type": b.get("type")}
        if b.get("type") == "thinking":
            d["thinking"] = b.get("thinking", "")
        elif b.get("type") == "text":
            d["text"] = b.get("text", "")
        elif b.get("type") == "tool_use":
            d["id"] = b.get("id", "")
            d["name"] = b.get("name", "")
            d["input"] = b.get("input", {})
        result.append(d)
    return result


def _truncate_args(input_: dict) -> str:
    s = str(input_)
    return s[:100] + "..." if len(s) > 100 else s


def _summarize_result(tool_name: str, result: str) -> str:
    """Short summary for logging and frontend display."""
    if tool_name in ("search_laws", "search_documents"):
        count = result.count("### 法条") or result.count("### 文档")
        if count == 0 and result:
            count = 1
        return f"找到 {count} 条结果"
    elif tool_name == "search_cases":
        count = result.count("### 案例")
        if count == 0 and result:
            count = 1
        return f"找到 {count} 个案例"
    elif tool_name == "read_document_full":
        return f"读取完成（{len(result)} 字符）"
    elif tool_name == "generate_document":
        return "文书已生成"
    return "完成"


def _extract_law_refs(result_text: str) -> list[dict]:
    """Extract law references from search_laws result for AgentResponse."""
    refs = []
    import re
    for m in re.finditer(r'《(.+?)》\s*第(.+?)条', result_text):
        refs.append({
            "type": "law",
            "law_name": m.group(1),
            "article_number": m.group(2),
            "text": result_text[max(0, m.start()-20):m.end()+100],
        })
    return refs[:10]


def _extract_case_refs(result_text: str) -> list[dict]:
    """Extract case references from search_cases result."""
    refs = []
    import re
    for m in re.finditer(r'来源:\s*(https?://\S+)', result_text):
        refs.append({"type": "case", "url": m.group(1)})
    return refs[:5]


def _infer_message_type(refs: list[dict], tools_used: list[dict]) -> str:
    tool_names = [t["tool_name"] for t in tools_used]
    if "generate_document" in tool_names:
        return "文书"
    if "search_cases" in tool_names:
        return "案情"
    if "search_documents" in tool_names or "read_document_full" in tool_names:
        return "文档"
    if "search_laws" in tool_names:
        return "咨询"
    return "其他"
