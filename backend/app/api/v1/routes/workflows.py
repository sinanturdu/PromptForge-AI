"""Gated workflow inspection only; no writer, provider or optimizer dependencies."""

from collections.abc import Callable, Coroutine
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.routing import APIRoute
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.errors import PROBLEM_JSON_MEDIA_TYPE, AppError, ProblemDetails
from app.db.read_transactions import read_snapshot
from app.llm.errors import LLMError
from app.modules.prompts.repository import PromptRequestRepository
from app.modules.prompts.service import PromptRequestNotFoundError
from app.modules.prompts.workflow_api_schemas import (
    WorkflowContentHistoryResponse,
    WorkflowHistoryPageResponse,
    WorkflowHistoryQuery,
    WorkflowMetadataHistoryResponse,
    WorkflowProgressResponse,
    WorkflowRecoveryResponse,
    WorkflowResultResponse,
)
from app.modules.prompts.workflow_persistence_contracts import PersistenceError
from app.modules.prompts.workflow_readback import WorkflowReadbackService
from app.modules.prompts.workflow_recovery import WorkflowRecoveryAssessmentService


def _problem(code: str, status: int, detail: str) -> AppError:
    return AppError(status_code=status, error_code=code, detail=detail)


class _WorkflowReadRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            failure = None
            try:
                return await original(request)
            except PersistenceError as error:
                code, status, detail = {
                    "not_found": ("workflow_not_found", 404, "Workflow not found."),
                    "conflict": (
                        "workflow_conflict",
                        409,
                        "Workflow inspection conflicts with stored state.",
                    ),
                    "invalid": (
                        "workflow_integrity_inconsistent",
                        409,
                        "Workflow records cannot be trusted as a consistent result.",
                    ),
                    "unavailable": (
                        "workflow_unavailable",
                        503,
                        "Workflow inspection is temporarily unavailable.",
                    ),
                }[error.code]
                failure = _problem(code, status, detail)
            except SQLAlchemyError:
                failure = _problem(
                    "workflow_unavailable", 503, "Workflow inspection is temporarily unavailable."
                )
            except (ValidationError, LLMError, ValueError):
                failure = _problem(
                    "workflow_integrity_inconsistent",
                    409,
                    "Workflow records cannot be trusted as a consistent result.",
                )
            raise failure

        return handler


router = APIRouter(
    tags=["workflows"],
    route_class=_WorkflowReadRoute,
    responses={
        code: {
            "model": ProblemDetails,
            "description": description,
            "content": {
                PROBLEM_JSON_MEDIA_TYPE: {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}
            },
        }
        for code, description in {
            403: "Direct loopback/same-origin access is required.",
            404: "Workflow or owning prompt request not found.",
            409: "Result not ready or stored integrity validation failed.",
            422: "Request validation failed.",
            503: "Read-only workflow storage unavailable.",
            500: "Unexpected internal error.",
        }.items()
    },
)


def get_workflow_read_engine(request: Request) -> AsyncEngine:
    engine = getattr(request.app.state, "workflow_read_engine", None)
    if not isinstance(engine, AsyncEngine):
        raise _problem(
            "workflow_unavailable", 503, "Workflow inspection is temporarily unavailable."
        )
    return engine


def get_workflow_readback(request: Request) -> WorkflowReadbackService:
    service = getattr(request.app.state, "workflow_readback", None)
    if not isinstance(service, WorkflowReadbackService):
        raise _problem(
            "workflow_unavailable", 503, "Workflow inspection is temporarily unavailable."
        )
    return service


def get_workflow_recovery(request: Request) -> WorkflowRecoveryAssessmentService:
    service = getattr(request.app.state, "workflow_recovery", None)
    if not isinstance(service, WorkflowRecoveryAssessmentService):
        raise _problem(
            "workflow_unavailable", 503, "Workflow inspection is temporarily unavailable."
        )
    return service


Readback = Annotated[WorkflowReadbackService, Depends(get_workflow_readback)]
ParentScope = Annotated[
    UUID | None,
    Query(
        description=(
            "Optional owning PromptRequest assertion; mismatch returns 404. "
            "This is not authorization."
        )
    ),
]


@router.get(
    "/runs/{id}", response_model=WorkflowProgressResponse, summary="Inspect workflow progress"
)
async def get_workflow_progress(
    id: UUID, service: Readback, prompt_request_id: ParentScope = None
) -> WorkflowProgressResponse:
    return WorkflowProgressResponse.from_readback(
        await service.read(id, prompt_request_id=prompt_request_id)
    )


@router.get(
    "/prompt-requests/{id}/runs",
    response_model=WorkflowHistoryPageResponse,
    summary="List workflow metadata for an existing prompt request",
)
async def list_workflows(
    id: UUID,
    query: Annotated[WorkflowHistoryQuery, Query()],
    service: Readback,
    engine: Annotated[AsyncEngine, Depends(get_workflow_read_engine)],
) -> WorkflowHistoryPageResponse:
    # The internal list intentionally treats an absent parent as an empty page.
    # Check existence using the same read-only engine and existing repository;
    # never borrow the ordinary API session (which can initialize WAL).
    async with read_snapshot(engine) as session:
        if await PromptRequestRepository(session).get_by_id(id) is None:
            raise PromptRequestNotFoundError(id)
    page = await service.list_workflows(id, limit=query.limit, cursor=query.to_cursor())
    return WorkflowHistoryPageResponse.from_readback(page)


@router.get(
    "/runs/{id}/history",
    response_model=WorkflowMetadataHistoryResponse,
    summary="Inspect metadata without prompt content",
)
async def get_workflow_history(
    id: UUID, service: Readback, prompt_request_id: ParentScope = None
) -> WorkflowMetadataHistoryResponse:
    return WorkflowMetadataHistoryResponse.from_readback(
        await service.read(id, prompt_request_id=prompt_request_id)
    )


@router.get(
    "/runs/{id}/history/content",
    response_model=WorkflowContentHistoryResponse,
    summary="Explicitly retrieve sensitive prompt content and feedback",
)
async def get_workflow_content(
    id: UUID, service: Readback, prompt_request_id: ParentScope = None
) -> WorkflowContentHistoryResponse:
    return WorkflowContentHistoryResponse.from_readback(
        await service.read_content(id, prompt_request_id=prompt_request_id)
    )


@router.get(
    "/runs/{id}/result",
    response_model=WorkflowResultResponse,
    summary="Retrieve an integrity-validated terminal result; completion is not qualification",
)
async def get_workflow_result(
    id: UUID, service: Readback, prompt_request_id: ParentScope = None
) -> WorkflowResultResponse:
    value = await service.read_content(id, prompt_request_id=prompt_request_id)
    summary = value.summary
    if summary.findings or summary.records_truncated or not summary.history_valid:
        raise _problem(
            "workflow_integrity_inconsistent",
            409,
            "Workflow records cannot be trusted as a consistent result.",
        )
    if summary.workflow.status not in {"COMPLETED", "FAILED", "CLARIFICATION_REQUIRED"}:
        raise _problem(
            "workflow_not_terminal", 409, "Workflow has not reached a terminal execution state."
        )
    result = WorkflowResultResponse.from_readback(value)
    if summary.selected is not None and (
        result.candidate is None or result.evaluation is None or result.assessment is None
    ):
        raise _problem(
            "workflow_integrity_inconsistent",
            409,
            "Workflow records cannot be trusted as a consistent result.",
        )
    return result


@router.get(
    "/runs/{id}/recovery",
    response_model=WorkflowRecoveryResponse,
    summary="Inspect internal recovery diagnostics without authorizing replay or mutation",
)
async def get_workflow_recovery_diagnostics(
    id: UUID,
    service: Annotated[WorkflowRecoveryAssessmentService, Depends(get_workflow_recovery)],
    prompt_request_id: ParentScope = None,
) -> WorkflowRecoveryResponse:
    return WorkflowRecoveryResponse.from_readback(
        await service.assess(id, prompt_request_id=prompt_request_id)
    )
