"""REST API routes for creating and retrieving user prompt requests."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import PROBLEM_JSON_MEDIA_TYPE, ProblemDetails
from app.db.session import get_db_session
from app.modules.prompts.schemas import (
    DEFAULT_PAGE,
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    PromptRequestCreate,
    PromptRequestListResponse,
    PromptRequestRead,
)
from app.modules.prompts.service import PromptRequestService


def _problem_response_spec(description: str) -> dict[str, Any]:
    return {
        "model": ProblemDetails,
        "description": description,
        "content": {
            PROBLEM_JSON_MEDIA_TYPE: {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}
        },
    }


router = APIRouter(
    prefix="/prompt-requests",
    tags=["prompt-requests"],
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: _problem_response_spec(
            "Request validation failed (RFC 9457 Problem Details)."
        ),
        status.HTTP_500_INTERNAL_SERVER_ERROR: _problem_response_spec(
            "Unexpected internal server error (RFC 9457 Problem Details)."
        ),
    },
)


def get_prompt_request_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> PromptRequestService:
    """Construct a request-scoped PromptRequestService using the active AsyncSession."""
    return PromptRequestService(session=session)


@router.post(
    "",
    response_model=PromptRequestRead,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_400_BAD_REQUEST: _problem_response_spec(
            "Malformed HTTP headers or request framing (RFC 9457 Problem Details)."
        ),
        status.HTTP_413_CONTENT_TOO_LARGE: _problem_response_spec(
            "HTTP request body exceeds MAX_REQUEST_BODY_BYTES (RFC 9457 Problem Details)."
        ),
    },
    summary="Create a new prompt request",
)
async def create_prompt_request(
    payload: PromptRequestCreate,
    request: Request,
    response: Response,
    service: Annotated[PromptRequestService, Depends(get_prompt_request_service)],
) -> PromptRequestRead:
    """Validate and store a raw user prompt request with optimization preferences."""
    created = await service.create_prompt_request(payload)
    response.headers["Location"] = request.app.url_path_for(
        "get_prompt_request",
        id=str(created.id),
    )
    return created


@router.get(
    "",
    response_model=PromptRequestListResponse,
    summary="List saved prompt requests",
)
async def list_prompt_requests(
    service: Annotated[PromptRequestService, Depends(get_prompt_request_service)],
    page: Annotated[
        int,
        Query(ge=1, description="1-based page number."),
    ] = DEFAULT_PAGE,
    page_size: Annotated[
        int,
        Query(
            ge=1,
            le=MAX_PAGE_SIZE,
            description=f"Number of records per page (1..{MAX_PAGE_SIZE}).",
        ),
    ] = DEFAULT_PAGE_SIZE,
) -> PromptRequestListResponse:
    """Return a paginated list of saved prompt requests ordered newest first."""
    return await service.list_prompt_requests(page=page, page_size=page_size)


@router.get(
    "/{id}",
    name="get_prompt_request",
    response_model=PromptRequestRead,
    responses={
        status.HTTP_404_NOT_FOUND: _problem_response_spec(
            "Prompt request not found (RFC 9457 Problem Details)."
        ),
    },
    summary="Get a prompt request by UUID",
)
async def get_prompt_request(
    id: UUID,
    service: Annotated[PromptRequestService, Depends(get_prompt_request_service)],
) -> PromptRequestRead:
    """Retrieve a single persisted prompt request by its UUID."""
    return await service.get_prompt_request(id)
