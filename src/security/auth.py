import uuid
import asyncio
from fastapi import Request, HTTPException
from src.database.redis import get_client
from src.config import settings
from loguru import logger

TOKEN_KEY_PREFIX = "legal_agent:token"


async def _get_token_data(token: str) -> dict | None:
    """Retrieve token payload from Redis. Returns None if expired or missing."""
    client = await get_client()
    raw = await client.get(f"{TOKEN_KEY_PREFIX}:{token}")
    if raw is None:
        return None
    import json
    return json.loads(raw)


async def save_token(token: str, user_id: int, username: str):
    """Store token in Redis with 2-day TTL."""
    client = await get_client()
    import json
    data = json.dumps({"user_id": user_id, "username": username}, ensure_ascii=False)
    await client.set(f"{TOKEN_KEY_PREFIX}:{token}", data, ex=settings.session_token_ttl)


async def refresh_token_ttl(token: str):
    """Reset TTL on each authenticated request."""
    client = await get_client()
    await client.expire(f"{TOKEN_KEY_PREFIX}:{token}", settings.session_token_ttl)


async def delete_token(token: str):
    """Remove token from Redis on logout."""
    client = await get_client()
    await client.delete(f"{TOKEN_KEY_PREFIX}:{token}")


def generate_token() -> str:
    """Generate a unique session token."""
    return uuid.uuid4().hex


async def get_current_user(request: Request) -> dict:
    """FastAPI dependency: extract Bearer token, validate, return user info.
    Raises 401 if missing, expired, or invalid.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未提供认证令牌")

    token = auth_header[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="令牌为空")

    token_data = await _get_token_data(token)
    if token_data is None:
        raise HTTPException(status_code=401, detail="令牌无效或已过期")

    # Fire-and-forget TTL refresh — don't block on it
    async def _refresh():
        try:
            await refresh_token_ttl(token)
        except Exception:
            pass
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_refresh())
    except RuntimeError:
        pass

    return token_data


# Optional dependency for routes that work both with and without auth
async def get_optional_user(request: Request) -> dict | None:
    """Like get_current_user but returns None instead of 401 when no token."""
    try:
        return await get_current_user(request)
    except HTTPException:
        return None
