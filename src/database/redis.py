import json
import time
import redis.asyncio as aioredis
from src.config import settings
from src.database.session_repo import save_session_memory, get_session_memory, delete_session_memory
from loguru import logger

_client = None

SESSION_KEY_PREFIX = "legal_agent:session"
TOKEN_KEY_PREFIX = "legal_agent:token"


def _session_key(user_id: int, session_id: str) -> str:
    return f"{SESSION_KEY_PREFIX}:{user_id}:{session_id}"


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
        "last_active_at": int(time.time()),
    }


async def create_session(user_id: int, session_id: str) -> dict:
    client = await get_client()
    key = _session_key(user_id, session_id)
    data = _default_session()
    await client.set(key, json.dumps(data, ensure_ascii=False))
    _sync_to_pg(user_id, session_id, data)
    return data


async def get_session(user_id: int, session_id: str) -> dict | None:
    client = await get_client()
    key = _session_key(user_id, session_id)
    raw = await client.get(key)
    if raw is not None:
        return json.loads(raw)

    # Redis miss — try PostgreSQL recovery
    pg_data = get_session_memory(session_id, user_id)
    if pg_data is not None:
        logger.info(f"Session {user_id}:{session_id}: recovered from PostgreSQL")
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


async def update_session(user_id: int, session_id: str, **fields) -> dict:
    client = await get_client()
    key = _session_key(user_id, session_id)
    data = await get_session(user_id, session_id)
    if data is None:
        data = _default_session()
    data.update(fields)
    await client.set(key, json.dumps(data, ensure_ascii=False))
    _sync_to_pg(user_id, session_id, data)
    return data


async def delete_session(user_id: int, session_id: str):
    client = await get_client()
    key = _session_key(user_id, session_id)
    await client.delete(key)
    delete_session_memory(session_id)


async def touch_session(user_id: int, session_id: str):
    """Update last_active_at timestamp for a session."""
    data = await get_session(user_id, session_id)
    if data is not None:
        data["last_active_at"] = int(time.time())
        await update_session(user_id, session_id, **data)


async def list_idle_sessions(idle_seconds: int = 900) -> list[tuple[int, str, dict]]:
    """Find sessions idle for more than idle_seconds (default 15 min).

    Returns list of (user_id, session_id, session_data).
    """
    client = await get_client()
    cutoff = int(time.time()) - idle_seconds
    results = []

    cursor = 0
    while True:
        cursor, keys = await client.scan(cursor, match=f"{SESSION_KEY_PREFIX}:*", count=100)
        for key in keys:
            try:
                raw = await client.get(key)
                if raw is None:
                    continue
                data = json.loads(raw)
                last_active = data.get("last_active_at", 0)
                if last_active < cutoff:
                    # Parse user_id and session_id from key
                    parts = key.split(":")
                    # key format: legal_agent:session:{user_id}:{session_id}
                    if len(parts) >= 4:
                        uid = int(parts[-2])
                        sid = parts[-1]
                        results.append((uid, sid, data))
            except Exception:
                continue
        if cursor == 0:
            break

    return results


def _sync_to_pg(user_id: int, session_id: str, data: dict):
    """Mirror Redis session state to PostgreSQL for durability."""
    try:
        save_session_memory(
            session_id=session_id,
            user_id=user_id,
            short_term_memory=data.get("short_term_memory", []),
            summary_list=data.get("summary_list", []),
            window_token_count=data.get("window_token_count", 0),
            state=data.get("state", "idle"),
            has_document=data.get("has_document", False),
            document_name=data.get("document_name"),
        )
    except Exception as e:
        logger.warning(f"PG sync failed for session {user_id}:{session_id}: {e}")
