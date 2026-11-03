"""Core application settings powered by pydantic-settings."""

from functools import lru_cache
from pathlib import Path
from typing import Final

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

BACKEND_DIR: Final[Path] = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR: Final[Path] = BACKEND_DIR / "data"
DEFAULT_SQLITE_DB_PATH: Final[Path] = DEFAULT_DATA_DIR / "promptforge.db"


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
    )

    app_name: str = "PromptForge AI"
    app_version: str = "0.0.1"
    api_v1_prefix: str = "/api/v1"

    database_url: str = Field(default_factory=_default_database_url)
    database_connect_timeout: float = Field(default=30.0, gt=0.0)
    database_echo: bool = False

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
