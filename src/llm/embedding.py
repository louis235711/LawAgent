import asyncio
from openai import AsyncOpenAI, OpenAIError
from src.config import settings
from loguru import logger

_client: AsyncOpenAI | None = None


def get_embedding_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=settings.dashscope_api_key,
            base_url=settings.embedding_base_url,
        )
    return _client


EMBEDDING_MAX_BATCH = 10


def _normalize(vec: list[float]) -> list[float]:
    """L2 normalize vector so IP = cosine similarity."""
    norm = sum(v * v for v in vec) ** 0.5
    return [v / norm for v in vec] if norm > 0 else vec


async def _embed_batch(texts: list[str], max_retries: int = 3) -> list[list[float]]:
    client = get_embedding_client()
    for attempt in range(max_retries):
        try:
            response = await client.embeddings.create(
                model=settings.embedding_model,
                input=texts,
            )
            return [_normalize(item.embedding) for item in response.data]
        except OpenAIError as e:
            logger.warning(f"Embedding attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)
        except Exception as e:
            logger.error(f"Embedding unexpected error: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)
    raise RuntimeError(f"Embedding failed after {max_retries} attempts")


async def embed_texts(texts: list[str], max_retries: int = 3) -> list[list[float]]:
    if len(texts) <= EMBEDDING_MAX_BATCH:
        return await _embed_batch(texts, max_retries)

    all_embeddings = []
    for i in range(0, len(texts), EMBEDDING_MAX_BATCH):
        batch = texts[i:i + EMBEDDING_MAX_BATCH]
        batch_embeddings = await _embed_batch(batch, max_retries)
        all_embeddings.extend(batch_embeddings)
    return all_embeddings


async def embed_text(text: str) -> list[float]:
    results = await embed_texts([text])
    return results[0]
