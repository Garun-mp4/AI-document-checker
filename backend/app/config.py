from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://document_checker:document-checker-local@localhost:5432/document_checker"
    upload_dir: str = "./data/uploads"
    embedding_cache_dir: str = "./data/embeddings"
    codex_home: str = "./data/codex"
    codex_model: str = "gpt-6-luna"
    codex_reasoning_effort: str = "medium"
    max_upload_bytes: int = 25 * 1024 * 1024
    model_context_chars: int = 42_000
    markdown_max_chars: int = 5_000_000
    markdown_timeout_seconds: int = 120


settings = Settings()
