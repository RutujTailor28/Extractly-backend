from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "Extractly API"
    APP_VERSION: str = "0.1.0"
    ENVIRONMENT: str = "development"

    SUPABASE_URL: str
    SUPABASE_KEY: str = Field(
        validation_alias=AliasChoices("SUPABASE_ANON_KEY", "SUPABASE_KEY"),
    )
    SUPABASE_SERVICE_ROLE_KEY: str | None = None

    MAX_FILE_SIZE_MB: int = 10
    SUPABASE_DOCUMENTS_BUCKET: str = "documents"
    PROCESSED_DOCUMENTS_BUCKET: str = "processed-documents"
    FILE_URL_EXPIRY_SECONDS: int = 3600

    PDF_RENDER_DPI: int = 180
    MAX_IMAGE_WIDTH: int = 2500
    MAX_IMAGE_HEIGHT: int = 3500
    MAX_DESKEW_ANGLE: float = 10.0

    AI_PROVIDER: str = "mock"
    AI_MODEL: str = "gpt-4o"
    AI_API_KEY: str | None = None
    AI_TIMEOUT_SECONDS: int = 60
    AI_MAX_RETRIES: int = 2

    # Gemini (foundation / future extraction)
    GEMINI_API_KEY: str | None = None
    GEMINI_MODEL: str = "gemini-2.5-flash"

    EXPORT_MAX_DOCUMENTS: int = 1000
    EXPORT_MAX_DATE_RANGE_DAYS: int = 365

    # Public API / API keys
    API_KEY_PEPPER: str = "extractly-dev-pepper-change-me"
    API_KEY_DEFAULT_RATE_LIMIT: int = 60
    API_KEY_MAX_RATE_LIMIT: int = 1000
    PUBLIC_API_MAX_FILE_SIZE_MB: int = 25
    PUBLIC_SYNC_MAX_BYTES: int = 10 * 1024 * 1024
    EXTRACTION_JOB_TIMEOUT_SECONDS: int = 300
    WEBHOOK_TIMEOUT_SECONDS: int = 10
    WEBHOOK_ALLOW_LOCALHOST: bool = True
    API_KEY_LAST_USED_MIN_INTERVAL_SECONDS: int = 300

    @property
    def public_api_max_file_size_bytes(self) -> int:
        return self.PUBLIC_API_MAX_FILE_SIZE_MB * 1024 * 1024

    @property
    def max_file_size_bytes(self) -> int:
        return self.MAX_FILE_SIZE_MB * 1024 * 1024

    @property
    def ai_provider_name(self) -> str:
        return (self.AI_PROVIDER or "mock").strip().lower()

    def validate_ai_configuration(self) -> None:
        """
        Validate AI settings without calling external APIs.
        Raises ValueError with a safe message (never includes secrets).
        """
        provider = self.ai_provider_name
        allowed = {"mock", "fake", "test", "openai", "gpt", "gemini"}
        if provider not in allowed:
            raise ValueError(
                f"Unsupported AI_PROVIDER '{self.AI_PROVIDER}'. "
                "Use 'mock', 'openai', or 'gemini'."
            )
        if provider == "gemini":
            if not (self.GEMINI_API_KEY or "").strip():
                raise ValueError(
                    "GEMINI_API_KEY is required when AI_PROVIDER=gemini"
                )
            if not (self.GEMINI_MODEL or "").strip():
                raise ValueError(
                    "GEMINI_MODEL is required when AI_PROVIDER=gemini"
                )
        if provider in {"openai", "gpt"}:
            if not (self.AI_API_KEY or "").strip():
                raise ValueError(
                    "AI_API_KEY is required when AI_PROVIDER=openai"
                )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
