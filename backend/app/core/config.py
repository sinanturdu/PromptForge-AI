"""Core application settings.

Milestone 2A keeps configuration intentionally static. Environment-based
configuration (pydantic-settings, `.env` loading, agent model settings) is
introduced in Milestone 3 as defined in docs/architecture.md.
"""

from functools import lru_cache

from pydantic import BaseModel, ConfigDict


class Settings(BaseModel):
    """Application-wide settings."""

    model_config = ConfigDict(frozen=True)

    app_name: str = "PromptForge AI"
    app_version: str = "0.0.1"
    api_v1_prefix: str = "/api/v1"


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings instance."""
    return Settings()
