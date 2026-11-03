"""Aggregates all version 1 route modules into a single router."""

from fastapi import APIRouter

from app.api.v1.routes import prompt_requests, system

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(prompt_requests.router)
