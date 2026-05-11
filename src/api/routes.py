import json
import uuid
from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse
from src.api.schemas import (
    ChatRequest, ChatResponse, SessionResponse,
    UploadResponse, HistoryMessage, HealthResponse,
)
from src.agents.base import AgentResponse
from src.database.redis import create_session, get_session, delete_session
from src.database.message_repo import get_messages
from src.security.guard import check_safety, get_block_response
from src.agents.dispatcher import DispatcherAgent
from src.document.processor import process_upload
from loguru import logger

router = APIRouter(prefix="/api")
dispatcher = DispatcherAgent()


@router.get("/health", response_model=HealthResponse)
async def health_check():
    checks = {"postgres": "ok", "redis": "ok", "milvus": "ok"}

    try:
        from src.database.postgres import get_conn, put_conn
        conn = get_conn()
        conn.cursor().execute("SELECT 1")
        put_conn(conn)
    except Exception:
        checks["postgres"] = "error"

    try:
        from src.database.redis import get_client
        r = await get_client()
        await r.ping()
    except Exception:
        checks["redis"] = "error"

    try:
        from src.vector_db.milvus_client import connect
        from pymilvus import utility
        connect()
        utility.list_collections()
    except Exception:
        checks["milvus"] = "error"

    all_ok = all(v == "ok" for v in checks.values())
    return HealthResponse(status="ok" if all_ok else "degraded", **checks)


@router.post("/session", response_model=SessionResponse)
async def new_session():
    session_id = uuid.uuid4().hex[:16]
    await create_session(session_id)
    return SessionResponse(session_id=session_id)


@router.post("/chat/{session_id}", response_model=ChatResponse)
async def chat(session_id: str, req: ChatRequest):
    user_input = req.message

    # Security check
    result, _ = await check_safety(user_input)
    if result != "合法":
        block_msg = get_block_response(result)
        return ChatResponse(
            session_id=session_id,
            content=block_msg,
            metadata={"blocked": True, "reason": result},
        )

    # Dispatch to agents
    try:
        response = await dispatcher.dispatch(session_id, user_input)
    except Exception as e:
        logger.error(f"Dispatch error: {e}")
        raise HTTPException(status_code=500, detail="处理请求时出现内部错误")

    return ChatResponse(
        session_id=session_id,
        content=response.content,
        references=response.references,
        metadata=response.metadata,
    )


@router.post("/chat/{session_id}/stream")
async def chat_stream(session_id: str, req: ChatRequest):
    user_input = req.message

    # Security check
    result, _ = await check_safety(user_input)
    if result != "合法":
        block_msg = get_block_response(result)
        block_json = json.dumps(
            {"session_id": session_id, "content": block_msg, "metadata": {"blocked": True, "reason": result}},
            ensure_ascii=False,
        )
        return StreamingResponse(
            iter([f"data: {block_json}\n\n"]),
            media_type="text/event-stream",
        )

    async def generate():
        try:
            async for item in dispatcher.dispatch_stream(session_id, user_input):
                if isinstance(item, AgentResponse):
                    done_data = json.dumps({
                        "done": True,
                        "session_id": session_id,
                        "references": item.references,
                        "metadata": item.metadata,
                    }, ensure_ascii=False)
                    yield f"data: {done_data}\n\n"
                else:
                    chunk_data = json.dumps({"delta": item}, ensure_ascii=False)
                    yield f"data: {chunk_data}\n\n"
        except Exception as e:
            logger.error(f"SSE stream error: {e}")
            error_data = json.dumps(
                {"error": True, "message": "处理请求时出现内部错误"},
                ensure_ascii=False,
            )
            yield f"data: {error_data}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/upload/{session_id}", response_model=UploadResponse)
async def upload_pdf(session_id: str, file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="仅支持 PDF 格式")

    content = await file.read()
    if len(content) > 50 * 1024 * 1024:  # 50MB limit
        raise HTTPException(status_code=400, detail="文件大小不能超过 50MB")

    result = await process_upload(session_id, content, file.filename)
    return UploadResponse(**result)


@router.get("/session/{session_id}/history")
async def session_history(session_id: str):
    session = await get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    pg_messages = get_messages(session_id, limit=50)
    return {
        "session_id": session_id,
        "state": session.get("state"),
        "has_document": session.get("has_document"),
        "document_name": session.get("document_name"),
        "messages": [
            HistoryMessage(**m) for m in pg_messages
        ],
    }


@router.delete("/session/{session_id}")
async def remove_session(session_id: str):
    await delete_session(session_id)
    try:
        from src.database.postgres import get_conn, put_conn
        conn = get_conn()
        conn.cursor().execute("DELETE FROM conversation_messages WHERE session_id = %s", (session_id,))
        conn.commit()
        put_conn(conn)
    except Exception:
        pass
    return {"status": "deleted", "session_id": session_id}
