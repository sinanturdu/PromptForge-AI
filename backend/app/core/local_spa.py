"""Opt-in built SPA, served only by the existing verified local execution listener."""

from pathlib import Path

from fastapi import FastAPI, Request
from starlette.responses import FileResponse

from app.api.workflow_security import workflow_request_is_local
from app.core.errors import AppError
from app.core.execution_environment import PreparedLocalExecutionServer

_ASSET_SUFFIXES = {
    ".js",
    ".css",
    ".svg",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".ico",
    ".woff",
    ".woff2",
}


def install_local_spa(
    application: FastAPI, directory: Path, *, transport: PreparedLocalExecutionServer
) -> None:
    """No catch-all mount: API/docs/errors and unknown files keep their real responses.

    The operator supplies a trusted build directory, never source, uploads or secrets.
    A preparation alone is insufficient; authority is checked on every HTTP request.
    """
    root = directory.resolve(strict=True)
    if (
        transport.application is not application
        or application.state.settings.api_v1_prefix != "/api/v1"
        or not application.state.settings.workflow_api_enabled
        or not application.state.settings.workflow_execution_enabled
        or not (root / "index.html").is_file()
        or not (root / "assets").is_dir()
    ):
        raise ValueError(
            "Local SPA serving requires a built frontend and explicit local execution."
        )

    def authorize(request: Request) -> None:
        if not workflow_request_is_local(request.scope):
            raise AppError(
                status_code=403,
                error_code="workflow_access_denied",
                detail="Local application access requires a direct same-origin loopback request.",
                headers={"Cache-Control": "no-store"},
            )
        if request.app is not application or not transport.is_qualified():
            raise AppError(
                status_code=503,
                error_code="workflow_execution_environment_unsafe",
                detail="Local application listener is not qualified.",
                headers={"Cache-Control": "no-store"},
            )

    def file(path: Path, *, html: bool = False) -> FileResponse:
        resolved = path.resolve()
        if (
            not resolved.is_relative_to(root)
            or not resolved.is_file()
            or any(part.startswith(".") for part in path.relative_to(root).parts)
            or (not html and resolved.suffix not in _ASSET_SUFFIXES)
        ):
            raise AppError(
                status_code=404,
                error_code="not_found",
                detail="Static resource not found.",
                headers={"Cache-Control": "no-store"},
            )
        return FileResponse(
            resolved,
            headers={
                "Cache-Control": "no-store" if html else "no-cache",
                "X-Content-Type-Options": "nosniff",
                "Cross-Origin-Resource-Policy": "same-origin",
                "X-Frame-Options": "DENY",
            },
        )

    async def page(request: Request) -> FileResponse:
        authorize(request)
        return file(root / "index.html", html=True)

    async def asset(request: Request, asset_path: str) -> FileResponse:
        authorize(request)
        return file(root / "assets" / asset_path)

    async def favicon(request: Request) -> FileResponse:
        authorize(request)
        return file(root / "favicon.svg")

    # Explicit current React Router paths, not /{path:path}. Future pages must be
    # intentionally registered here. No API/OpenAPI/docs/health prefix can fall back.
    for path in ("/", "/history", "/prompt-requests/{id}"):
        application.add_api_route(path, page, methods=["GET"], include_in_schema=False)
    application.add_api_route(
        "/assets/{asset_path:path}", asset, methods=["GET"], include_in_schema=False
    )
    application.add_api_route("/favicon.svg", favicon, methods=["GET"], include_in_schema=False)
