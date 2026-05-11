from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # DeepSeek
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"

    # DashScope
    dashscope_api_key: str = ""
    embedding_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    embedding_model: str = "text-embedding-v4"
    rerank_base_url: str = "https://dashscope.aliyuncs.com/api/v1/services"
    rerank_endpoint: str = "/rerank/text-rerank/text-rerank"
    rerank_model: str = "qwen3-rerank"

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

    # Context window
    max_context_tokens: int = 200_000
    summary_trigger_ratio: float = 0.65
    summary_rounds: int = 5

    # App
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    # File storage
    data_dir: str = "data"
    uploads_dir: str = "data/uploads"
    templates_dir: str = "data/templates"
    laws_dir: str = "data/laws"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
