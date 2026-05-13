import json
import redis.asyncio as aioredis
from src.config import settings
from src.database.session_repo import save_session_memory, get_session_memory, delete_session_memory
from loguru import logger

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
        "summary_list": [],
        "window_token_count": 0,
        "state": "idle",
        "has_document": False,
        "document_name": None,
        "file_size": 0,
    }


async def create_session(session_id: str) -> dict:
    client = await get_client()
    key = _session_key(session_id)
    data = _default_session()
    await client.set(key, json.dumps(data, ensure_ascii=False))
    _sync_to_pg(session_id, data)
    return data


async def get_session(session_id: str) -> dict | None:
    client = await get_client()
    key = _session_key(session_id)
    raw = await client.get(key)
    if raw is not None:
        return json.loads(raw)

    # Redis 缺失，尝试从 PostgreSQL 恢复
    pg_data = get_session_memory(session_id)
    if pg_data is not None:
        logger.info(f"Session {session_id}: recovered from PostgreSQL")
        data = {
            "short_term_memory": pg_data["short_term_memory"],
            "summary_list": pg_data["summary_list"],
            "window_token_count": pg_data["window_token_count"],
            "state": pg_data["state"],
            "has_document": pg_data["has_document"],
            "document_name": pg_data["document_name"],
            "file_size": 0,
        }
        await client.set(key, json.dumps(data, ensure_ascii=False))
        return data
    return None


async def update_session(session_id: str, **fields) -> dict:
    client = await get_client()
    key = _session_key(session_id)
    data = await get_session(session_id)
    if data is None:
        data = _default_session()
    data.update(fields)
    await client.set(key, json.dumps(data, ensure_ascii=False))
    _sync_to_pg(session_id, data)
    return data


async def delete_session(session_id: str):
    client = await get_client()
    key = _session_key(session_id)
    await client.delete(key)
    delete_session_memory(session_id)


def _sync_to_pg(session_id: str, data: dict):
    """Mirror Redis session state to PostgreSQL for durability."""
    try:
        save_session_memory(
            session_id=session_id,
            short_term_memory=data.get("short_term_memory", []),
            summary_list=data.get("summary_list", []),
            window_token_count=data.get("window_token_count", 0),
            state=data.get("state", "idle"),
            has_document=data.get("has_document", False),
            document_name=data.get("document_name"),
        )
    except Exception as e:
        logger.warning(f"PG sync failed for session {session_id}: {e}")
