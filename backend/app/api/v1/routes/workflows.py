"""Gated workflow inspection and explicitly qualified local submission."""

from collections.abc import Callable, Coroutine
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.routing import APIRoute
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.execution_admission import (
    EXECUTION_DEADLINE_SECONDS,
    AdmissionDecision,
    AdmissionRequest,
)
from app.api.workflow_security import execution_request_is_local
from app.core.errors import PROBLEM_JSON_MEDIA_TYPE, AppError, ProblemDetails
from app.core.execution_startup import ApplicationExecutionOwner
from app.db.read_transactions import read_snapshot
from app.llm.errors import LLMError
from app.modules.prompts.persistence_contracts import STORAGE_VERSION
from app.modules.prompts.repository import PromptRequestRepository
from app.modules.prompts.service import PromptRequestNotFoundError
from app.modules.prompts.workflow_api_schemas import (
    WorkflowContentHistoryResponse,
    WorkflowCreateRequest,
    WorkflowCreateResponse,
    WorkflowHistoryPageResponse,
    WorkflowHistoryQuery,
    WorkflowIdentityResponse,
    WorkflowMetadataHistoryResponse,
    WorkflowProgressResponse,
    WorkflowRecoveryResponse,
    WorkflowResultResponse,
    WorkflowStartRequest,
    WorkflowStartResponse,
)
from app.modules.prompts.workflow_persistence_contracts import (
    PersistenceError,
    WorkflowConfiguration,
)
from app.modules.prompts.workflow_readback import WorkflowReadbackService
from app.modules.prompts.workflow_recovery import WorkflowRecoveryAssessmentService


def _problem(code: str, status: int, detail: str) -> AppError:
    return AppError(status_code=status, error_code=code, detail=detail)


class _WorkflowRoute(APIRoute):
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
    route_class=_WorkflowRoute,
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

# Registered only when both flags are enabled. Registration is not execution
# qualification: every request still needs the live application-owned listener.
execution_router = APIRouter(
    tags=["workflows"],
    route_class=_WorkflowRoute,
    responses={
        **router.responses,
        503: {
            "model": ProblemDetails,
            "description": "Execution startup is unqualified, draining, or storage unavailable.",
            "content": {
                PROBLEM_JSON_MEDIA_TYPE: {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}
            },
        },
        429: {
            "model": ProblemDetails,
            "description": "The single local execution slot is occupied; no queue.",
            "content": {
                PROBLEM_JSON_MEDIA_TYPE: {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}
            },
        },
    },
)


async def get_workflow_execution_owner(request: Request) -> ApplicationExecutionOwner:
    if not execution_request_is_local(request.scope):
        raise _problem(
            "workflow_access_denied",
            403,
            "Workflow execution requires a direct same-origin loopback JSON request.",
        )
    owner = getattr(request.app.state, "workflow_execution_owner", None)
    if (
        not isinstance(owner, ApplicationExecutionOwner)
        or owner.supervisor is None
        or owner.persistence is None
    ):
        raise _problem(
            "workflow_execution_environment_unsafe",
            503,
            "Workflow execution transport is not qualified.",
        )
    outcome = owner.supervisor.check_environment(request.scope)
    if outcome is not None:
        error = AdmissionDecision(outcome=outcome).to_error()
        assert error is not None
        raise error
    return owner


ExecutionOwner = Annotated[ApplicationExecutionOwner, Depends(get_workflow_execution_owner)]


def get_workflow_readback(request: Request) -> WorkflowReadbackService:
    service = getattr(request.app.state, "workflow_readback", None)
    if not isinstance(service, WorkflowReadbackService):
        raise _problem(
            "workflow_unavailable", 503, "Workflow inspection is temporarily unavailable."
        )
    return service


@execution_router.post(
    "/prompt-requests/{id}/runs",
    response_model=WorkflowCreateResponse,
    status_code=201,
    responses={200: {"model": WorkflowCreateResponse, "description": "Exact idempotent replay."}},
    summary="Create a workflow without executing it (verified local startup required)",
    description=(
        "Requires both feature flags and the dedicated owned-loopback startup. "
        "The required body client_idempotency_key protects retries; no header key is used. "
        "Ordinary startup returns 503. Creation never dispatches a provider."
    ),
)
async def create_workflow(
    id: UUID,
    payload: WorkflowCreateRequest,
    request: Request,
    response: Response,
    owner: ExecutionOwner,
) -> WorkflowCreateResponse:
    assert owner.persistence is not None
    created = await owner.persistence.create_workflow(
        id,
        WorkflowConfiguration(
            schema_version=STORAGE_VERSION,
            max_evaluation_rounds=payload.max_evaluation_rounds,
            timeout_seconds=EXECUTION_DEADLINE_SECONDS,
        ),
        client_idempotency_key=payload.client_idempotency_key,
    )
    response.status_code = 200 if created.replayed else 201
    response.headers["Location"] = (
        f"{request.app.state.settings.api_v1_prefix}/runs/{created.workflow.id}"
    )
    return WorkflowCreateResponse(
        workflow=WorkflowIdentityResponse.model_validate(
            dict(
                id=created.workflow.id,
                prompt_request_id=created.workflow.prompt_request_id,
                status=created.workflow.status,
            )
        ),
        replayed=created.replayed,
    )


@execution_router.post(
    "/runs/{id}/execution",
    response_model=WorkflowStartResponse,
    status_code=202,
    summary="Accept one supervised execution (verified local startup required)",
    description=(
        "202 acknowledges local task ownership, not a durable claim or completed result. "
        "Poll Location for authoritative persisted progress. Ordinary startup returns 503."
    ),
)
async def start_workflow(
    id: UUID,
    payload: WorkflowStartRequest,
    request: Request,
    response: Response,
    owner: ExecutionOwner,
    service: Annotated[WorkflowReadbackService, Depends(get_workflow_readback)],
    prompt_request_id: Annotated[UUID | None, Query()] = None,
) -> WorkflowStartResponse:
    # Resolve ownership from real readback, never from client-supplied fencing data.
    summary = await service.read(id, prompt_request_id=prompt_request_id)
    row = summary.workflow
    identity = WorkflowIdentityResponse.model_validate(
        dict(
            id=row.id,
            prompt_request_id=row.prompt_request_id,
            status=row.status,
        )
    )
    assert owner.supervisor is not None
    submitted = await owner.supervisor.submit(
        AdmissionRequest(
            prompt_request_id=row.prompt_request_id, workflow_run_id=id, start=payload
        ),
        scope=request.scope,
    )
    if not submitted.accepted:
        error = submitted.decision.to_error()
        assert error is not None
        raise error
    # No await after acceptance: worker ownership is independent of HTTP delivery.
    response.headers["Location"] = f"{request.app.state.settings.api_v1_prefix}/runs/{id}"
    return WorkflowStartResponse(workflow=identity)


def get_workflow_read_engine(request: Request) -> AsyncEngine:
    engine = getattr(request.app.state, "workflow_read_engine", None)
    if not isinstance(engine, AsyncEngine):
        raise _problem(
            "workflow_unavailable", 503, "Workflow inspection is temporarily unavailable."
        )
    return engine


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
