import json
import redis.asyncio as aioredis
from src.config import settings

_client = None

SESSION_KEY_PREFIX = "legal_agent:session"


def _session_key(session_id: str) -> str:
    return f"{SESSION_KEY_PREFIX}:{session_id}"


async def get_client():
    global _client
    if _client is None:
        _client = aioredis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password or None,
            db=settings.redis_db,
            decode_responses=True,
        )
    return _client


def _default_session() -> dict:
    return {
        "short_term_memory": [],
        "summary_memory": "",
        "window_token_count": 0,
        "state": "idle",
        "has_document": False,
        "document_name": None,
    }


async def create_session(session_id: str) -> dict:
    client = await get_client()
    key = _session_key(session_id)
    data = _default_session()
    await client.set(key, json.dumps(data, ensure_ascii=False))
    return data


async def get_session(session_id: str) -> dict | None:
    client = await get_client()
    key = _session_key(session_id)
    raw = await client.get(key)
    if raw is None:
        return None
    return json.loads(raw)


async def update_session(session_id: str, **fields) -> dict:
    client = await get_client()
    key = _session_key(session_id)
    data = await get_session(session_id)
    if data is None:
        data = _default_session()
    data.update(fields)
    await client.set(key, json.dumps(data, ensure_ascii=False))
    return data


async def delete_session(session_id: str):
    client = await get_client()
    key = _session_key(session_id)
    await client.delete(key)
