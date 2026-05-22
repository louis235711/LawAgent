import asyncio
from anthropic import AsyncAnthropic, APIError
from src.config import settings
from loguru import logger

_client: AsyncAnthropic | None = None


def get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
        )
    return _client


def _extract_system(messages: list[dict]) -> tuple[str, list[dict]]:
    """Extract system messages from the list, return (system_text, remaining_messages)."""
    system_parts = []
    remaining = []
    for m in messages:
        if m["role"] == "system":
            system_parts.append(m["content"])
        else:
            remaining.append(m)
    return "\n\n".join(system_parts), remaining


def _normalize_messages(messages: list[dict]) -> list[dict]:
    """Convert internal role names to Anthropic format. Only user/assistant allowed."""
    result = []
    for m in messages:
        role = m["role"]
        if role == "ai":
            role = "assistant"
        elif role == "system":
            continue  # system extracted separately
        result.append({"role": role, "content": m["content"]})
    return result


async def chat_completion(
    messages: list[dict],
    model: str = "mimo-v2.5-pro",
    temperature: float = 0.7,
    max_tokens: int = 4096,
    stream: bool = False,
    max_retries: int = 3,
    **kwargs,
) -> str:
    client = get_client()
    system_text, remaining = _extract_system(messages)
    normalized = _normalize_messages(remaining)
    last_error = None

    for attempt in range(max_retries):
        try:
            response = await client.messages.create(
                model=model,
                messages=normalized,
                system=system_text or None,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            )
            text_blocks = [b.text for b in response.content if b.type == "text"]
            content = "\n".join(text_blocks)
            if not content:
                logger.warning(f"LLM returned empty content (model={model}, "
                               f"stop_reason={response.stop_reason})")
            return content
        except APIError as e:
            last_error = e
            logger.warning(f"LLM call attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)
        except Exception as e:
            last_error = e
            logger.error(f"LLM call unexpected error: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)

    raise RuntimeError(f"LLM call failed after {max_retries} attempts: {last_error}")


async def chat_completion_stream(
    messages: list[dict],
    model: str = "mimo-v2.5-pro",
    temperature: float = 0.7,
    max_tokens: int = 4096,
    max_retries: int = 3,
    **kwargs,
):
    """Yield text chunks as they arrive from the LLM. Use for SSE streaming."""
    client = get_client()
    system_text, remaining = _extract_system(messages)
    normalized = _normalize_messages(remaining)
    last_error = None

    for attempt in range(max_retries):
        try:
            async with client.messages.stream(
                model=model,
                messages=normalized,
                system=system_text or None,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            ) as stream:
                async for event in stream:
                    if event.type == "content_block_delta" and event.delta.type == "text_delta":
                        yield event.delta.text
                return
        except APIError as e:
            last_error = e
            logger.warning(f"LLM stream attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)
        except Exception as e:
            last_error = e
            logger.error(f"LLM stream unexpected error: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)

    raise RuntimeError(f"LLM stream failed after {max_retries} attempts: {last_error}")


# ═══════════════════════════════════════════════════════════════
# Anthropic SDK — ReAct agent (thinking + tool_use)
# ═══════════════════════════════════════════════════════════════

async def chat_completion_react(
    messages: list[dict],
    system: str,
    tools: list[dict],
    model: str = "mimo-v2.5-pro",
    max_tokens: int = 4096,
    thinking_budget: int = 2048,
    max_retries: int = 3,
):
    """Non-streaming call with thinking blocks for ReAct loop.

    messages: Anthropic-format list of message dicts
    system:   system prompt (Anthropic top-level parameter)
    tools:    Anthropic tool definitions

    Returns the raw Anthropic Message object with content blocks
    (thinking, tool_use, text, etc.)
    """
    client = get_client()
    last_error = None

    for attempt in range(max_retries):
        try:
            response = await client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                tools=tools,
                thinking={"type": "enabled", "budget_tokens": thinking_budget},
            )
            return response
        except Exception as e:
            last_error = e
            logger.warning(f"ReAct LLM call attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)

    raise RuntimeError(f"ReAct LLM call failed after {max_retries} attempts: {last_error}")


async def chat_completion_react_stream(
    messages: list[dict],
    system: str,
    tools: list[dict],
    model: str = "mimo-v2.5-pro",
    max_tokens: int = 4096,
    thinking_budget: int = 2048,
    max_retries: int = 3,
):
    """Streaming call with thinking blocks for ReAct loop.

    Yields event dicts:
    - {"type": "thinking_delta", "thinking": "..."}
    - {"type": "text_delta", "text": "..."}
    - {"type": "tool_use", "id": "...", "name": "...", "input": {...}}
    - {"type": "done"}

    Also yields final message object as last non-done event for
    callers that need the full content_blocks for memory storage.
    """
    client = get_client()
    last_error = None

    for attempt in range(max_retries):
        try:
            async with client.messages.stream(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                tools=tools,
                thinking={"type": "enabled", "budget_tokens": thinking_budget},
            ) as stream:
                # Collect content blocks for final message reconstruction
                content_blocks = []
                current_block = None
                current_block_type = None

                async for event in stream:
                    if event.type == "content_block_start":
                        current_block_type = event.content_block.type
                        current_block = {
                            "type": current_block_type,
                            "index": event.index,
                        }
                        if current_block_type == "thinking":
                            current_block["thinking"] = ""
                        elif current_block_type == "text":
                            current_block["text"] = ""
                        elif current_block_type == "tool_use":
                            current_block["id"] = event.content_block.id
                            current_block["name"] = event.content_block.name
                            current_block["input"] = ""

                    elif event.type == "content_block_delta":
                        if event.delta.type == "thinking_delta":
                            chunk = event.delta.thinking
                            current_block["thinking"] += chunk
                            yield {"type": "thinking_delta", "thinking": chunk}
                        elif event.delta.type == "text_delta":
                            chunk = event.delta.text
                            current_block["text"] += chunk
                            yield {"type": "text_delta", "text": chunk}
                        elif event.delta.type == "input_json_delta":
                            current_block["input"] += event.delta.partial_json

                    elif event.type == "content_block_stop":
                        if current_block:
                            if current_block_type == "tool_use":
                                try:
                                    import json
                                    current_block["input"] = json.loads(current_block["input"])
                                except json.JSONDecodeError:
                                    pass
                                yield {
                                    "type": "tool_use",
                                    "id": current_block["id"],
                                    "name": current_block["name"],
                                    "input": current_block["input"],
                                }
                            content_blocks.append(current_block)
                            current_block = None
                            current_block_type = None

                    elif event.type == "message_stop":
                        yield {"type": "done", "content_blocks": content_blocks}
                        return

            return  # stream completed normally
        except Exception as e:
            last_error = e
            logger.warning(f"ReAct LLM stream attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)

    raise RuntimeError(f"ReAct LLM stream failed after {max_retries} attempts: {last_error}")
