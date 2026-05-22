import os
import sys
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from loguru import logger

from src.config import settings
from src.database.postgres import init_db
from src.vector_db.milvus_client import init_collections
from src.api.routes import router as chat_router
from src.api.auth import router as auth_router
from src.mcp.server import mcp_server


# ── Logging setup ──────────────────────────────────────────
logger.remove()
logger.configure(extra={"request_id": "--------"})

logger.add(
    sys.stderr,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{extra[request_id]}</cyan> | <level>{message}</level>",
    level="INFO",
)

os.makedirs("logs", exist_ok=True)
os.makedirs(settings.generated_dir, exist_ok=True)
logger.add(
    "logs/lawagent_{time:YYYY-MM-DD}.log",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {extra[request_id]} | {name}:{function}:{line} | {message}",
    level="DEBUG",
    rotation="10 MB",
    retention="7 days",
    encoding="utf-8",
)


def _register_agents():
    # ReActAgent is the single autonomous agent — no registry needed.
    # Old agents (legal_consultation, case_analysis, document_qa,
    # document_writing, follow_up) are archived as reference.
    logger.info("ReActAgent ready (single-agent architecture)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting LawAgent...")
    init_db()
    init_collections()
    _register_agents()
    from src.rag.pipeline import build_bm25_from_collection
    n = await build_bm25_from_collection()
    logger.info(f"BM25 index built: {n} documents")

    # Connect to external MCP servers
    from src.mcp.client import get_mcp_client
    mcp_client = get_mcp_client()
    await mcp_client.connect_all()
    ext_tools = mcp_client.list_external_tools()
    if ext_tools:
        logger.info(f"External MCP tools: {len(ext_tools)} — {[t['name'] for t in ext_tools]}")

    # Start background idle session scanner
    from src.memory.scheduler import start_scheduler, stop_scheduler
    start_scheduler()

    logger.info(f"LawAgent ready on {settings.app_host}:{settings.app_port}")
    yield

    logger.info("LawAgent shutting down")
    stop_scheduler()
    await mcp_client.disconnect_all()


app = FastAPI(
    title="LawAgent",
    description="多智能体架构法务智能服务系统",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = uuid.uuid4().hex[:8]
    with logger.contextualize(request_id=request_id):
        path = request.url.path
        logger.info(f"[REQ] {request.method} {path}")
        response = await call_next(request)
        is_stream = "stream" in path
        if is_stream:
            logger.debug(f"[REQ] stream opened ({response.status_code})")
        else:
            logger.info(f"[REQ] → {response.status_code}")
        return response


app.include_router(chat_router)
app.include_router(auth_router)

# MCP Server — expose tools to external MCP clients
mcp_app = mcp_server.sse_app(mount_path="/messages")
app.mount("/mcp", mcp_app)

# Static files — serve frontend
static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
if os.path.isdir(static_dir):
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.main:app", host=settings.app_host, port=settings.app_port)
