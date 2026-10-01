from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://document_checker:document-checker-local@localhost:5432/document_checker"
    upload_dir: str = "./data/uploads"
    embedding_cache_dir: str = "./data/embeddings"
    codex_home: str = "./data/codex"
    codex_model: str = "gpt-6-luna"
    codex_reasoning_effort: str = "medium"
    local_ui_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    max_upload_bytes: int = 25 * 1024 * 1024
    model_context_chars: int = 42_000
    markdown_max_chars: int = 5_000_000
    markdown_timeout_seconds: int = 120
    ocr_enabled: bool = True
    ocr_languages: str = "rus+eng"
    ocr_dpi: int = 200
    ocr_max_pages: int = 100
    ocr_timeout_seconds: int = 90
    ocr_max_chars: int = 5_000_000
    archive_max_bytes: int = Field(default=100 * 1024 * 1024, ge=1024)
    archive_member_max_bytes: int = Field(default=32 * 1024 * 1024, ge=1024)
    archive_max_entries: int = Field(default=5000, ge=1)
    document_max_pages: int = Field(default=500, ge=1)
    document_max_chars: int = Field(default=5_000_000, ge=1)
    document_worker_memory_mb: int = Field(default=1536, ge=256)
    document_worker_cpu_seconds: int = Field(default=90, ge=1)
    document_worker_timeout_seconds: int = Field(default=180, ge=1)
    document_worker_max_output_bytes: int = Field(default=64 * 1024 * 1024, ge=1024)


settings = Settings()
