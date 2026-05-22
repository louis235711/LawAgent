from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # DeepSeek
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://token-plan-cn.xiaomimimo.com/anthropic"

    # DashScope
    dashscope_api_key: str = ""
    embedding_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    embedding_model: str = "text-embedding-v4"
    rerank_base_url: str = "https://dashscope.aliyuncs.com/api/v1/services"
    rerank_endpoint: str = "/rerank/text-rerank/text-rerank"
    rerank_model: str = "gte-rerank-v2"

    # Tavily
    tavily_api_key: str = ""

    # PostgreSQL
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "law_agent"
    postgres_user: str = "law_agent"
    postgres_password: str = "law_agent_pwd"

    @property
    def postgres_dsn(self) -> str:
        return (
            f"dbname={self.postgres_db} user={self.postgres_user} "
            f"password={self.postgres_password} host={self.postgres_host} "
            f"port={self.postgres_port}"
        )

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str = ""
    redis_db: int = 0

    # Milvus
    milvus_host: str = "localhost"
    milvus_port: int = 19530

    # External MCP Servers (stdio transport — command to start each server)
    mcp_time_server_cmd: str = ""
    mcp_calculator_server_cmd: str = ""
    mcp_fetch_server_cmd: str = ""

    # Context window
    max_context_tokens: int = 200_000
    summary_trigger_ratio: float = 0.70
    min_batch_tokens: int = 40_000

    # OCR service (PaddleOCR Docker)
    ocr_service_url: str = "http://localhost:8001"

    # App
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    # File storage
    data_dir: str = "data"
    uploads_dir: str = "data/uploads"
    generated_dir: str = "data/generated"
    templates_dir: str = "data/templates"
    laws_dir: str = "data/laws"

    # Auth
    session_token_ttl: int = 172_800  # 2 days

    # Long-term memory (paths are templates; actual paths include user_id)
    memory_dir: str = "data/memory"
    feedback_style_path: str = "data/memory/feedback_style.md"
    user_role_path: str = "data/memory/user_role.md"

    @staticmethod
    def memory_paths_for_user(user_id: int) -> tuple[str, str]:
        """Return (feedback_style_path, user_role_path) for a given user."""
        import os
        base = os.path.join("data", "memory", str(user_id))
        return (os.path.join(base, "feedback_style.md"),
                os.path.join(base, "user_role.md"))

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
