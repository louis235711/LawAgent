import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from loguru import logger

from src.config import settings
from src.database.postgres import init_db
from src.vector_db.milvus_client import init_collections
from src.agents.dispatcher import register_agent
from src.agents.legal_consultation import LegalConsultationAgent
from src.agents.case_analysis import CaseAnalysisAgent
from src.agents.document_qa import DocumentQAAgent
from src.agents.document_writing import DocumentWritingAgent
from src.agents.follow_up import FollowUpAgent
from src.api.routes import router


def _register_agents():
    register_agent("法律咨询", LegalConsultationAgent())
    register_agent("案情分析", CaseAnalysisAgent())
    register_agent("文档提问", DocumentQAAgent())
    register_agent("合同审查", DocumentQAAgent())
    register_agent("文书撰写", DocumentWritingAgent())
    register_agent("追问/聊天", FollowUpAgent())
    logger.info("All agents registered")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting LawAgent...")
    init_db()
    init_collections()
    _register_agents()
    logger.info(f"LawAgent ready on {settings.app_host}:{settings.app_port}")
    yield
    logger.info("LawAgent shutting down")


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
async def log_requests(request: Request, call_next):
    logger.info(f"{request.method} {request.url.path}")
    response = await call_next(request)
    return response


app.include_router(router)

# Static files — serve frontend
static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
if os.path.isdir(static_dir):
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.main:app", host=settings.app_host, port=settings.app_port, reload=True)
