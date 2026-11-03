"""FastAPI application factory and ASGI entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.db.session import DatabaseSessionManager


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Initialize lazy database infrastructure and dispose resources on shutdown."""
    settings: Settings = application.state.settings
    db_manager = DatabaseSessionManager(
        database_url=settings.database_url,
        connect_timeout=settings.database_connect_timeout,
        echo=settings.database_echo,
    )
    application.state.db_manager = db_manager
    try:
        yield
    finally:
        await db_manager.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    resolved_settings = settings if settings is not None else get_settings()
    application = FastAPI(
        title=resolved_settings.app_name,
        version=resolved_settings.app_version,
        description="Multi-agent prompt optimization platform.",
        openapi_url=f"{resolved_settings.api_v1_prefix}/openapi.json",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )
    application.state.settings = resolved_settings
    application.include_router(api_router, prefix=resolved_settings.api_v1_prefix)
    return application


app = create_app()
