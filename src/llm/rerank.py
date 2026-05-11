import asyncio
import httpx
from src.config import settings
from loguru import logger


def _rerank_url() -> str:
    return f"{settings.rerank_base_url}{settings.rerank_endpoint}"


async def rerank(
    query: str,
    documents: list[str],
    top_k: int | None = None,
    max_retries: int = 3,
) -> list[dict]:
    """Rerank documents by relevance to query. Returns list of {index, relevance_score}."""
    headers = {
        "Authorization": f"Bearer {settings.dashscope_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.rerank_model,
        "input": {
            "query": query,
            "documents": documents,
        },
    }
    url = _rerank_url()

    async with httpx.AsyncClient(timeout=30) as client:
        for attempt in range(max_retries):
            try:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
                results = data.get("output", {}).get("results", [])

                sorted_results = sorted(results, key=lambda x: x["relevance_score"], reverse=True)
                if top_k:
                    sorted_results = sorted_results[:top_k]
                return sorted_results
            except httpx.HTTPError as e:
                logger.warning(f"Rerank attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
            except Exception as e:
                logger.error(f"Rerank unexpected error: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)

    raise RuntimeError(f"Rerank failed after {max_retries} attempts")
