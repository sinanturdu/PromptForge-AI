"""Local-only workflow boundary; socket identity is required, headers never grant trust."""

import re
from ipaddress import IPv6Address, ip_address

from fastapi import Request
from starlette._utils import get_route_path
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.error_handlers import build_problem_response, unhandled_exception_handler


def execution_request_is_local(scope: Scope) -> bool:
    """Additional JSON/POST checks for future mutation; this proves no listener binding."""
    if (
        scope.get("type") != "http"
        or scope.get("method") != "POST"
        or not workflow_request_is_local(scope)
    ):
        return False
    content_types = [v for k, v in scope.get("headers", []) if k.lower() == b"content-type"]
    if len(content_types) != 1:
        return False
    return content_types[0].lower() in {b"application/json", b"application/json; charset=utf-8"}


def _authority(value: str, scheme: str) -> tuple[str, int] | None:
    matched = re.fullmatch(r"(localhost|127\.0\.0\.1|\[::1\])(?::([0-9]{1,5}))?", value.lower())
    if matched is None:
        return None
    port = int(matched[2]) if matched[2] is not None else 443 if scheme == "https" else 80
    return (matched[1], port) if 1 <= port <= 65535 else None


def workflow_request_is_local(scope: Scope) -> bool:
    client = scope.get("client")
    if client is None or "%" in client[0]:
        return False
    try:
        peer = ip_address(client[0])
    except ValueError:
        return False
    if isinstance(peer, IPv6Address) and peer.ipv4_mapped is not None:
        peer = peer.ipv4_mapped
    if not peer.is_loopback:
        return False

    values: dict[bytes, list[str]] = {}
    for key, raw in scope.get("headers", []):
        key = key.lower()
        if key in {b"forwarded", b"x-real-ip"} or key.startswith(b"x-forwarded-"):
            return False
        if key in {b"host", b"origin", b"sec-fetch-site"}:
            try:
                values.setdefault(key, []).append(raw.decode("ascii"))
            except UnicodeDecodeError:
                return False
    if len(values.get(b"host", [])) != 1 or any(len(v) != 1 for v in values.values()):
        return False
    scheme = scope.get("scheme", "")
    if scheme not in {"http", "https"}:
        return False
    host = _authority(values[b"host"][0], scheme)
    if host is None:
        return False
    if b"sec-fetch-site" in values and values[b"sec-fetch-site"][0] not in {"same-origin", "none"}:
        return False
    if b"origin" in values:
        origin = re.fullmatch(r"(https?)://([^/?#]+)", values[b"origin"][0])
        if origin is None or origin[1] != scheme or _authority(origin[2], scheme) != host:
            return False
    return True


class WorkflowSecurityMiddleware:
    """Guard only workflow paths and add no-store even to validation/500/disabled errors."""

    def __init__(self, app: ASGIApp, *, api_prefix: str, enabled: bool) -> None:
        self.app, self._prefix, self._enabled = app, api_prefix, enabled

    def _workflow_path(self, path: str) -> bool:
        if not path.startswith(self._prefix + "/"):
            return False
        relative = path[len(self._prefix) :]
        return (
            relative == "/runs"
            or relative.startswith("/runs/")
            or re.fullmatch(r"/prompt-requests/[^/]+/runs(?:/.*)?", relative) is not None
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Use exactly the path Starlette routes, including when ASGI root_path is set.
        if scope["type"] != "http" or not self._workflow_path(get_route_path(scope)):
            await self.app(scope, receive, send)
            return
        started = False

        async def protected_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "no-store"
                headers.add_vary_header("Origin, Sec-Fetch-Site")
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Cross-Origin-Resource-Policy"] = "same-origin"
                headers["X-Frame-Options"] = "DENY"
            await send(message)

        request = Request(scope)
        if self._enabled and not workflow_request_is_local(scope):
            response = build_problem_response(
                request,
                status_code=403,
                error_code="workflow_access_denied",
                detail="Workflow inspection requires a direct same-origin loopback request.",
            )
            await response(scope, receive, protected_send)
            return
        try:
            await self.app(scope, receive, protected_send)
        except Exception as error:
            if started:
                raise
            # Handle before the outer server-error middleware so even unexpected
            # failures have safe headers and the existing sanitized diagnostics.
            response = await unhandled_exception_handler(request, error)
            await response(scope, receive, protected_send)
