"""REST API routes for creating and retrieving user prompt requests."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.modules.prompts.schemas import (
    DEFAULT_PAGE,
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    PromptRequestCreate,
    PromptRequestListResponse,
    PromptRequestRead,
)
from app.modules.prompts.service import PromptRequestNotFoundError, PromptRequestService

router = APIRouter(prefix="/prompt-requests", tags=["prompt-requests"])


def get_prompt_request_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> PromptRequestService:
    """Construct a request-scoped PromptRequestService using the active AsyncSession."""
    return PromptRequestService(session=session)


@router.post(
    "",
    response_model=PromptRequestRead,
    status_code=status.HTTP_201_CREATED,
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
        status.HTTP_404_NOT_FOUND: {"description": "Prompt request not found."},
    },
    summary="Get a prompt request by UUID",
)
async def get_prompt_request(
    id: UUID,
    service: Annotated[PromptRequestService, Depends(get_prompt_request_service)],
) -> PromptRequestRead:
    """Retrieve a single persisted prompt request by its UUID."""
    try:
        return await service.get_prompt_request(id)
    except PromptRequestNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Prompt request not found.",
        ) from exc
