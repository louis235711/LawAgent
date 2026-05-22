import bcrypt
from fastapi import APIRouter, HTTPException, Depends, Request

from src.api.schemas import RegisterRequest, LoginRequest, AuthResponse
from src.database.postgres import get_conn, put_conn
from src.security.auth import generate_token, save_token, delete_token, get_current_user
from loguru import logger

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), hash.encode("utf-8"))


@router.post("/register", response_model=AuthResponse)
async def register(req: RegisterRequest):
    username = req.username.strip()
    password = req.password

    if len(username) < 2 or len(username) > 50:
        raise HTTPException(status_code=400, detail="用户名长度需在 2-50 个字符之间")
    if len(password) < 4 or len(password) > 64:
        raise HTTPException(status_code=400, detail="密码长度需在 4-64 个字符之间")
    # Only allow alphanumeric + underscore + hyphen
    for c in username:
        if not (c.isalnum() or c in "_-"):
            raise HTTPException(status_code=400, detail="用户名只能包含字母、数字、下划线和连字符")

    password_hash = _hash_password(password)

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (username, password_hash) VALUES (%s, %s) RETURNING id",
                (username, password_hash),
            )
            user_id = cur.fetchone()[0]
        conn.commit()
    except Exception as e:
        err_str = str(e).lower()
        if "unique" in err_str or "duplicate" in err_str:
            raise HTTPException(status_code=409, detail="用户名已被注册")
        logger.error(f"Register failed: {e}")
        raise HTTPException(status_code=500, detail="注册失败")
    finally:
        put_conn(conn)

    token = generate_token()
    await save_token(token, user_id, username)
    logger.info(f"User registered: {username} (id={user_id})")
    return AuthResponse(token=token, user_id=user_id, username=username)


@router.post("/login", response_model=AuthResponse)
async def login(req: LoginRequest):
    username = req.username.strip()
    password = req.password

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, password_hash FROM users WHERE username = %s",
                (username,),
            )
            row = cur.fetchone()
    finally:
        put_conn(conn)

    if row is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    user_id, password_hash = row
    if not _verify_password(password, password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    token = generate_token()
    await save_token(token, user_id, username)
    logger.info(f"User logged in: {username} (id={user_id})")
    return AuthResponse(token=token, user_id=user_id, username=username)


@router.post("/logout")
async def logout(request: Request, current_user: dict = Depends(get_current_user)):
    """Logout: delete token from Redis."""
    auth_header = request.headers.get("Authorization", "")
    token = auth_header[7:].strip() if auth_header.startswith("Bearer ") else ""
    if token:
        await delete_token(token)
        logger.info(f"Token deleted for user {current_user['username']}")
    return {"status": "ok", "message": "已登出"}


@router.get("/me")
async def get_me(current_user: dict = Depends(get_current_user)):
    return {
        "user_id": current_user["user_id"],
        "username": current_user["username"],
    }
