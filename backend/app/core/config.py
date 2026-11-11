"""Core application settings powered by pydantic-settings."""

from functools import lru_cache
from pathlib import Path
from typing import Final, Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

BACKEND_DIR: Final[Path] = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR: Final[Path] = BACKEND_DIR / "data"
DEFAULT_SQLITE_DB_PATH: Final[Path] = DEFAULT_DATA_DIR / "promptforge.db"

LogLevelName = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
VALID_LOG_LEVELS: Final[frozenset[str]] = frozenset(
    {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
)
DEFAULT_LOG_LEVEL: Final[LogLevelName] = "INFO"

# 256 KiB comfortably accommodates 20,000-char UTF-8 `raw_prompt` + 20,000-char UTF-8
# `user_context` + JSON escaping and request metadata while bounding memory usage.
DEFAULT_MAX_REQUEST_BODY_BYTES: Final[int] = 262_144


def build_sqlite_url(db_path: Path) -> str:
    """Build an async SQLite SQLAlchemy URL from a filesystem path."""
    resolved_path = db_path.resolve()
    return URL.create(
        drivername="sqlite+aiosqlite",
        database=resolved_path.as_posix(),
    ).render_as_string(hide_password=False)


def _default_database_url() -> str:
    return build_sqlite_url(DEFAULT_SQLITE_DB_PATH)


class Settings(BaseSettings):
    """Application-wide configuration loaded from environment variables or `.env`."""

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
        hide_input_in_errors=True,
    )

    app_name: str = "PromptForge AI"
    app_version: str = "0.0.1"
    api_v1_prefix: str = "/api/v1"
    llm_provider: Literal["fake", "openai"] = "fake"
    openai_api_key: SecretStr | None = Field(default=None, repr=False)
    llm_request_timeout_seconds: float = Field(default=20.0, gt=0, le=300, allow_inf_nan=False)
    llm_call_deadline_seconds: float = Field(default=90.0, gt=0, le=900, allow_inf_nan=False)
    llm_max_retries: int = Field(default=2, ge=0, le=2)
    llm_json_repair_attempts: int = Field(default=1, ge=0, le=1)

    log_level: LogLevelName = DEFAULT_LOG_LEVEL
    max_request_body_bytes: int = Field(default=DEFAULT_MAX_REQUEST_BODY_BYTES, gt=0)

    database_url: str = Field(default_factory=_default_database_url)
    database_connect_timeout: float = Field(default=30.0, gt=0.0)
    database_echo: bool = False

    # API activation registers guarded read routes only. Execution remains reserved;
    # neither flag provisions a database or constructs a persistent workflow writer.
    workflow_api_enabled: bool = False
    workflow_execution_enabled: bool = False

    @field_validator("workflow_api_enabled", "workflow_execution_enabled", mode="before")
    @classmethod
    def validate_workflow_flags(cls, value: object) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in {"true", "false"}:
            return value.lower() == "true"
        raise ValueError("Workflow feature flags must be true or false.")

    @model_validator(mode="after")
    def validate_workflow_activation(self) -> Self:
        if self.workflow_execution_enabled and not self.workflow_api_enabled:
            raise ValueError("Workflow execution requires explicit workflow API activation.")
        return self

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> object:
        """Normalize string log levels to uppercase before Literal validation."""
        if isinstance(value, str):
            stripped = value.strip().upper()
            if stripped not in VALID_LOG_LEVELS:
                allowed = ", ".join(sorted(VALID_LOG_LEVELS))
                raise ValueError(f"LOG_LEVEL must be one of: {allowed}.")
            return stripped
        return value

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        """Ensure DATABASE_URL is syntactically valid and specifies an async driver."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("DATABASE_URL must not be empty.")

        try:
            parsed_url = make_url(stripped)
        except ArgumentError as exc:
            raise ValueError(f"Invalid DATABASE_URL: {exc}") from exc

        backend_name = parsed_url.get_backend_name()
        driver_name = parsed_url.get_driver_name()

        if backend_name == "sqlite" and driver_name != "aiosqlite":
            raise ValueError("SQLite DATABASE_URL must use the async 'sqlite+aiosqlite' driver.")

        if backend_name == "postgresql" and (
            "+" not in parsed_url.drivername or driver_name in {"psycopg2", "pg8000"}
        ):
            raise ValueError(
                "PostgreSQL DATABASE_URL must specify an async driver such as 'postgresql+asyncpg'."
            )

        if "+" not in parsed_url.drivername:
            raise ValueError(
                f"DATABASE_URL '{parsed_url.drivername}' must specify an explicit async driver."
            )

        return stripped


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings instance."""
    return Settings()
