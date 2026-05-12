import asyncio
from openai import AsyncOpenAI, OpenAIError
from src.config import settings
from loguru import logger

_client: AsyncOpenAI | None = None


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
        )
    return _client


def _normalize_role(role: str) -> str:
    """Convert internal role names to OpenAI API format."""
    if role == "ai":
        return "assistant"
    return role


async def chat_completion(
    messages: list[dict],
    model: str = "deepseek-v4-flash",
    temperature: float = 0.7,
    max_tokens: int = 4096,
    stream: bool = False,
    max_retries: int = 3,
    **kwargs,
) -> str:
    client = get_client()
    # Normalize roles for API compatibility
    normalized = [
        {"role": _normalize_role(m["role"]), "content": m["content"]}
        for m in messages
    ]
    last_error = None

    for attempt in range(max_retries):
        try:
            if stream:
                chunks = []
                response = await client.chat.completions.create(
                    model=model,
                    messages=normalized,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=True,
                    **kwargs,
                )
                async for chunk in response:
                    if chunk.choices[0].delta.content:
                        chunks.append(chunk.choices[0].delta.content)
                return "".join(chunks)
            else:
                response = await client.chat.completions.create(
                    model=model,
                    messages=normalized,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=False,
                    **kwargs,
                )
                return response.choices[0].message.content
        except OpenAIError as e:
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
    model: str = "deepseek-v4-flash",
    temperature: float = 0.7,
    max_tokens: int = 4096,
    max_retries: int = 3,
    **kwargs,
):
    """Yield text chunks as they arrive from the LLM. Use for SSE streaming."""
    client = get_client()
    normalized = [
        {"role": _normalize_role(m["role"]), "content": m["content"]}
        for m in messages
    ]
    last_error = None

    for attempt in range(max_retries):
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=normalized,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
                **kwargs,
            )
            async for chunk in response:
                if chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
            return
        except OpenAIError as e:
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
