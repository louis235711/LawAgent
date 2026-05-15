from pydantic import BaseModel
from datetime import datetime


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    session_id: str
    content: str
    references: list[dict] = []
    metadata: dict = {}


class SessionResponse(BaseModel):
    session_id: str
    created: bool = True


class UploadResponse(BaseModel):
    session_id: str
    filename: str
    chunks: int
    total_tokens: int
    file_size: int = 0
    use_rag: bool = False


class HistoryMessage(BaseModel):
    role: str
    content: str
    token_count: int
    create_time: str
    message_type: str
    references: list[dict] = []
    metadata: dict = {}
    thinking: str = ""
    tools: list[dict] = []


class HealthResponse(BaseModel):
    status: str
    postgres: str
    redis: str
    milvus: str
