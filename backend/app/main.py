"""FastAPI application factory and ASGI entry point."""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from app.api.error_handlers import register_exception_handlers
from app.api.middleware import RequestBodySizeLimitMiddleware, RequestTracingMiddleware
from app.api.v1.router import api_router
from app.api.v1.routes.workflows import router as workflow_router
from app.api.workflow_security import WorkflowSecurityMiddleware
from app.core.config import Settings, get_settings
from app.core.errors import PROBLEM_JSON_MEDIA_TYPE, ProblemDetails
from app.core.execution_startup import ApplicationExecutionOwner, ExecutionModels
from app.core.logging import configure_logging, get_logger
from app.db.read_transactions import create_read_engine
from app.db.session import DatabaseSessionManager
from app.llm.composition import create_provider
from app.llm.gateway import LLMGateway
from app.llm.schemas import GatewayPolicy
from app.modules.prompts.workflow_readback import WorkflowReadbackService
from app.modules.prompts.workflow_recovery import WorkflowRecoveryAssessmentService

app_logger = get_logger("app")


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Own one provider and database manager, including during partial startup."""
    settings: Settings = application.state.settings
    async with AsyncExitStack() as resources:
        db_manager = DatabaseSessionManager(
            database_url=settings.database_url,
            connect_timeout=settings.database_connect_timeout,
            echo=settings.database_echo,
        )
        resources.push_async_callback(db_manager.dispose)
        read_engine = None
        if settings.workflow_api_enabled:
            # Lazy mode=ro engine only; no connection, migration or writer startup.
            read_engine = create_read_engine(settings.database_url)
            resources.push_async_callback(read_engine.dispose)
        provider = create_provider(settings)
        resources.push_async_callback(provider.aclose)
        gateway = LLMGateway(
            provider,
            provider_name=settings.llm_provider,
            policy=GatewayPolicy(
                request_timeout_seconds=settings.llm_request_timeout_seconds,
                call_deadline_seconds=settings.llm_call_deadline_seconds,
                max_retries=settings.llm_max_retries,
                json_repair_attempts=settings.llm_json_repair_attempts,
            ),
        )
        application.state.db_manager = db_manager
        application.state.llm_gateway = gateway
        execution_owner = None
        if read_engine is not None:
            application.state.workflow_read_engine = read_engine
            application.state.workflow_readback = WorkflowReadbackService(read_engine)
            application.state.workflow_recovery = WorkflowRecoveryAssessmentService(
                application.state.workflow_readback
            )
            models: ExecutionModels | None = application.state.execution_models
            if settings.workflow_execution_enabled and models is not None:
                execution_owner = ApplicationExecutionOwner(
                    settings, provider, application.state.workflow_readback, models
                )
                application.state.workflow_execution_owner = execution_owner
        app_logger.info("Application startup completed")
        try:
            yield
        finally:
            if execution_owner is not None:
                try:
                    stopped = await execution_owner.shutdown()
                except BaseException:
                    execution_owner.retain_resources(resources.pop_all())
                    raise
                if not stopped:
                    execution_owner.retain_resources(resources.pop_all())
                    raise RuntimeError(
                        "Workflow task did not stop; resources retained. "
                        "Process termination is required."
                    )
                del application.state.workflow_execution_owner
            del application.state.llm_gateway
            if read_engine is not None:
                del application.state.workflow_recovery
                del application.state.workflow_readback
                del application.state.workflow_read_engine
    app_logger.info("Application shutdown completed")


def _build_custom_openapi(application: FastAPI) -> dict[str, Any]:
    """Generate OpenAPI schema advertising RFC 9457 Problem Details for validation errors."""
    if application.openapi_schema is not None:
        return application.openapi_schema

    schema = get_openapi(
        title=application.title,
        version=application.version,
        description=application.description,
        routes=application.routes,
    )

    components = schema.setdefault("components", {})
    schemas = components.setdefault("schemas", {})
    problem_json_schema = ProblemDetails.model_json_schema(
        ref_template="#/components/schemas/{model}"
    )
    defs = problem_json_schema.pop("$defs", {})
    for def_name, def_schema in defs.items():
        schemas.setdefault(def_name, def_schema)
    schemas["ProblemDetails"] = problem_json_schema

    for path_item in schema.get("paths", {}).values():
        if not isinstance(path_item, dict):
            continue
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            responses = operation.get("responses")
            if not isinstance(responses, dict):
                continue
            if "422" in responses:
                responses["422"] = {
                    "description": "Request validation failed (RFC 9457 Problem Details).",
                    "content": {
                        PROBLEM_JSON_MEDIA_TYPE: {
                            "schema": {"$ref": "#/components/schemas/ProblemDetails"}
                        }
                    },
                }

    schemas.pop("HTTPValidationError", None)
    schemas.pop("ValidationError", None)

    application.openapi_schema = schema
    return schema


class _PromptForgeFastAPI(FastAPI):
    """FastAPI subclass overriding `.openapi()` to publish Problem Details schemas."""

    def openapi(self) -> dict[str, Any]:
        return _build_custom_openapi(self)


def create_app(
    settings: Settings | None = None, *, execution_models: ExecutionModels | None = None
) -> FastAPI:
    """Create and configure the FastAPI application."""
    resolved_settings = settings if settings is not None else get_settings()
    configure_logging(level=resolved_settings.log_level)

    application = _PromptForgeFastAPI(
        title=resolved_settings.app_name,
        version=resolved_settings.app_version,
        description="Multi-agent prompt optimization platform.",
        openapi_url=f"{resolved_settings.api_v1_prefix}/openapi.json",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )
    application.state.settings = resolved_settings
    application.state.execution_models = execution_models

    # Register inner middleware first so RequestTracingMiddleware wraps the full stack.
    application.add_middleware(
        RequestBodySizeLimitMiddleware,
        max_body_bytes=resolved_settings.max_request_body_bytes,
    )
    application.add_middleware(
        WorkflowSecurityMiddleware,
        api_prefix=resolved_settings.api_v1_prefix,
        enabled=resolved_settings.workflow_api_enabled,
    )
    application.add_middleware(RequestTracingMiddleware)

    register_exception_handlers(application)
    application.include_router(api_router, prefix=resolved_settings.api_v1_prefix)
    if resolved_settings.workflow_api_enabled:
        application.include_router(workflow_router, prefix=resolved_settings.api_v1_prefix)
    return application


app = create_app()
