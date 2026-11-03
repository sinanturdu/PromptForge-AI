"""Pure ASGI middlewares for server-side request tracing and HTTP body size enforcement."""

import time
from collections.abc import MutableMapping
from typing import Any, Final
from uuid import uuid4

from fastapi import Request
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.error_handlers import build_problem_response
from app.core.errors import RequestBodyTooLargeError
from app.core.logging import (
    REQUEST_ID_HEADER,
    get_logger,
    reset_request_id,
    set_request_id,
)

http_logger = get_logger("http")

BODY_LIMITED_METHODS: Final[frozenset[str]] = frozenset({"POST", "PUT", "PATCH"})


def _ensure_scope_state(scope: Scope) -> MutableMapping[str, Any]:
    state = scope.get("state")
    if isinstance(state, MutableMapping):
        return state
    created_state: dict[str, Any] = {}
    scope["state"] = created_state
    return created_state


class RequestTracingMiddleware:
    """Assign a server-generated UUIDv4 to every HTTP request and emit structured logs."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = str(uuid4())
        state = _ensure_scope_state(scope)
        state["request_id"] = request_id
        token = set_request_id(request_id)

        method = str(scope.get("method", "UNKNOWN"))
        # Log only the decoded route path; never log raw query strings or headers.
        path = str(scope.get("path", "/"))
        start_perf = time.perf_counter()
        status_code = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            duration_ms = round((time.perf_counter() - start_perf) * 1000, 3)
            http_logger.info(
                "HTTP request completed",
                extra={
                    "request_id": request_id,
                    "method": method,
                    "path": path,
                    "status_code": 500,
                    "duration_ms": duration_ms,
                    "error_code": "internal_server_error",
                },
            )
            raise
        else:
            duration_ms = round((time.perf_counter() - start_perf) * 1000, 3)
            error_code = state.get("error_code")
            http_logger.info(
                "HTTP request completed",
                extra={
                    "request_id": request_id,
                    "method": method,
                    "path": path,
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                    "error_code": error_code if isinstance(error_code, str) else None,
                },
            )
        finally:
            reset_request_id(token)


class RequestBodySizeLimitMiddleware:
    """Enforce MAX_REQUEST_BODY_BYTES via Content-Length and streamed byte counting."""

    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        if max_body_bytes < 1:
            raise ValueError("max_body_bytes must be a positive integer.")
        self.app = app
        self._max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method", "")).upper()
        if method not in BODY_LIMITED_METHODS:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        content_length_values = headers.getlist("content-length")
        if len(content_length_values) > 1 and len(set(content_length_values)) > 1:
            bad_resp = build_problem_response(
                Request(scope),
                status_code=400,
                error_code="invalid_content_length",
                detail="Conflicting Content-Length headers are not permitted.",
            )
            await bad_resp(scope, receive, send)
            return

        if content_length_values:
            raw_length = content_length_values[0].strip()
            if not raw_length.isdigit():
                bad_resp = build_problem_response(
                    Request(scope),
                    status_code=400,
                    error_code="invalid_content_length",
                    detail="Invalid Content-Length header value.",
                )
                await bad_resp(scope, receive, send)
                return

            declared_bytes = int(raw_length)
            if declared_bytes > self._max_body_bytes:
                too_large = RequestBodyTooLargeError(max_body_bytes=self._max_body_bytes)
                resp = build_problem_response(
                    Request(scope),
                    status_code=too_large.status_code,
                    error_code=too_large.error_code,
                    title=too_large.title,
                    detail=too_large.detail,
                )
                await resp(scope, receive, send)
                return

        received_bytes = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                chunk = message.get("body", b"")
                if isinstance(chunk, (bytes, bytearray)):
                    received_bytes += len(chunk)
                if received_bytes > self._max_body_bytes:
                    raise RequestBodyTooLargeError(max_body_bytes=self._max_body_bytes)
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except RequestBodyTooLargeError as exc:
            if response_started:
                raise
            resp = build_problem_response(
                Request(scope),
                status_code=exc.status_code,
                error_code=exc.error_code,
                title=exc.title,
                detail=exc.detail,
            )
            await resp(scope, receive, tracking_send)
