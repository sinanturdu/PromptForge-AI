"""System endpoints such as service health."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import get_settings

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    """Response body of the health endpoint."""

    status: Literal["ok"]
    service: str


@router.get("/health", response_model=HealthResponse, summary="Service health check")
def get_health() -> HealthResponse:
    """Report that the API process is running."""
    return HealthResponse(status="ok", service=get_settings().app_name)
