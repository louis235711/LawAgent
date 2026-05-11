from tavily import TavilyClient
from src.config import settings
from loguru import logger

_client: TavilyClient | None = None


def get_tavily_client() -> TavilyClient:
    global _client
    if _client is None:
        _client = TavilyClient(api_key=settings.tavily_api_key)
    return _client


async def search_web(query: str, max_results: int = 5) -> list[dict]:
    """Search web for similar cases. Returns [{title, url, content}]."""
    try:
        client = get_tavily_client()
        response = client.search(
            query=query,
            max_results=max_results,
            search_depth="advanced",
            include_answer=True,
        )
        results = []
        for r in response.get("results", []):
            results.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": r.get("content", ""),
            })
        return results
    except Exception as e:
        logger.warning(f"Tavily search failed: {e}")
        return []
