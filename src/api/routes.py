import json
import os
import uuid
from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse, FileResponse
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
    logger.info(f"[SECURITY] checking: {user_input[:80]}...")
    logger.info(f"[ROUTE] session={session_id} input_len={len(user_input)}")

    # Security check
    try:
        result, _ = await check_safety(user_input)
    except Exception:
        logger.warning("Safety check failed, defaulting to allow")
        result = "合法"
    logger.info(f"[SECURITY] result={result}")

    if result != "合法":
        logger.info(f"[ROUTE] blocked: {result}")
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

    logger.info(
        f"[ROUTE] done agent={response.metadata.get('agent', '?')} "
        f"intent={response.metadata.get('intent', '?')} "
        f"refs={len(response.references)} content_len={len(response.content)}"
    )
    return ChatResponse(
        session_id=session_id,
        content=response.content,
        references=response.references,
        metadata=response.metadata,
    )


@router.post("/chat/{session_id}/stream")
async def chat_stream(session_id: str, req: ChatRequest):
    user_input = req.message
    logger.info(f"[SECURITY] checking: {user_input[:80]}...")
    logger.info(f"[ROUTE] session={session_id} input_len={len(user_input)} stream=true")

    # Security check
    try:
        result, _ = await check_safety(user_input)
    except Exception:
        logger.warning("Safety check failed, defaulting to allow")
        result = "合法"
    logger.info(f"[SECURITY] result={result}")

    if result != "合法":
        logger.info(f"[ROUTE] blocked: {result}")
        block_msg = get_block_response(result)

        async def blocked_stream():
            yield f"data: {json.dumps({'delta': block_msg}, ensure_ascii=False)}\n\n"
            done = json.dumps({
                "done": True,
                "session_id": session_id,
                "content": block_msg,
                "references": [],
                "metadata": {"blocked": True, "reason": result},
            }, ensure_ascii=False)
            yield f"data: {done}\n\n"

        return StreamingResponse(blocked_stream(), media_type="text/event-stream")

    async def generate():
        try:
            async for item in dispatcher.dispatch_stream(session_id, user_input):
                if isinstance(item, AgentResponse):
                    done_data = json.dumps({
                        "done": True,
                        "content": item.content,
                        "session_id": session_id,
                        "references": item.references,
                        "metadata": item.metadata,
                    }, ensure_ascii=False)
                    yield f"data: {done_data}\n\n"
                elif isinstance(item, dict):
                    # status event (e.g. "summarizing") or refs event
                    yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
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


SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".png", ".jpg", ".jpeg", ".md", ".txt"}


@router.post("/upload/{session_id}", response_model=UploadResponse)
async def upload_file(session_id: str, file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="未提供文件")

    ext = file.filename.lower().rsplit(".", 1)
    if len(ext) != 2 or f".{ext[1]}" not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式，支持: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
        )

    content = await file.read()
    file_size = len(content)

    if file_size > 1 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件大小不能超过 1MB，请压缩后重试")

    try:
        result = await process_upload(session_id, content, file.filename, file_size)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"Upload processing failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"文档处理失败: {e}")

    return UploadResponse(**result)


@router.get("/download/{session_id}/{filename}")
async def download_file(session_id: str, filename: str):
    """Download a generated document file."""
    from src.config import settings
    # Check generated_dir first, then uploads_dir
    for base in [settings.generated_dir, settings.uploads_dir]:
        file_path = os.path.join(base, session_id, filename)
        if os.path.isfile(file_path):
            return FileResponse(file_path, filename=filename, media_type="application/octet-stream")
    raise HTTPException(status_code=404, detail="文件不存在或已过期")


@router.delete("/session/{session_id}/document")
async def remove_session_document(session_id: str):
    """Mark session document as removed (does not delete files/vectors)."""
    from src.database.redis import update_session
    from src.rag.bm25 import remove_session_bm25
    await update_session(session_id, has_document=False, document_name="", file_size=0)
    remove_session_bm25(session_id)
    return {"status": "removed", "session_id": session_id}


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
        "file_size": session.get("file_size", 0),
        "chunk_count": session.get("chunk_count", 0),
        "use_rag": session.get("use_rag", False),
        "messages": [
            HistoryMessage(**m) for m in pg_messages
        ],
    }


@router.delete("/session/{session_id}")
async def remove_session(session_id: str):
    # Extract long-term memory from conversation before deletion
    try:
        pg_msgs = get_messages(session_id, limit=100)
        if pg_msgs:
            lines = []
            for m in pg_msgs:
                role = "用户" if m["role"] == "user" else "AI"
                lines.append(f"{role}: {m['content']}")
            from src.memory.long_term import schedule_memory_update
            schedule_memory_update("\n".join(lines))
    except Exception:
        pass

    await delete_session(session_id)
    try:
        from src.database.postgres import get_conn, put_conn
        conn = get_conn()
        conn.cursor().execute("DELETE FROM conversation_messages WHERE session_id = %s", (session_id,))
        conn.commit()
        put_conn(conn)
    except Exception:
        pass

    # Clean up Milvus session document vectors
    try:
        from src.vector_db.milvus_client import get_collection, SESSION_DOCUMENTS_COLLECTION
        coll = get_collection(SESSION_DOCUMENTS_COLLECTION)
        coll.delete(f'session_id == "{session_id}"')
    except Exception:
        pass

    # Clean up session BM25 index
    try:
        from src.rag.bm25 import remove_session_bm25
        remove_session_bm25(session_id)
    except Exception:
        pass

    # Clean up uploaded files on disk
    import shutil
    from src.config import settings
    upload_dir = os.path.join(settings.uploads_dir, session_id)
    if os.path.isdir(upload_dir):
        shutil.rmtree(upload_dir)

    return {"status": "deleted", "session_id": session_id}
