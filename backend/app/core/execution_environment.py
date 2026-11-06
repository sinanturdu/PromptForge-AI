"""Explicit local startup/runtime qualification; never used by ordinary app startup."""

import os
import socket
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, field

import aiosqlite
import uvicorn
from starlette.types import ASGIApp

from app.db.sqlite_runtime import SQLiteRuntimeSafetyError, require_safe_sqlite_wal


@dataclass(frozen=True, repr=False)
class ExecutionRuntimeQualification:
    """Internal connected observations, not an HTTP input or a write authorization."""

    python_version: tuple[int, int, int]
    sqlite3_version: str | None
    aiosqlite_version: str | None
    process_id: int = field(default_factory=os.getpid)

    def is_qualified(self) -> bool:
        if self.process_id != os.getpid() or self.python_version != (3, 12, 16):
            return False
        try:
            require_safe_sqlite_wal(self.sqlite3_version)
            require_safe_sqlite_wal(self.aiosqlite_version)
        except SQLiteRuntimeSafetyError:
            return False
        return True


async def qualify_execution_runtime() -> ExecutionRuntimeQualification:
    """Observe real memory-only connections; never open a configured application DB."""
    with closing(sqlite3.connect(":memory:")) as connection:
        sync_row = connection.execute("SELECT sqlite_version()").fetchone()
    async with (
        aiosqlite.connect(":memory:") as async_connection,
        async_connection.execute("SELECT sqlite_version()") as cursor,
    ):
        async_row = await cursor.fetchone()
    result = ExecutionRuntimeQualification(
        (sys.version_info.major, sys.version_info.minor, sys.version_info.micro),
        None if sync_row is None else sync_row[0],
        None if async_row is None else async_row[0],
    )
    if not result.is_qualified():
        raise RuntimeError("Persistent execution runtime qualification failed.")
    return result


@dataclass(frozen=True, repr=False)
class PreparedLocalExecutionServer:
    """Caller-owned socket/server. Preparing alone never proves Uvicorn is serving it.

    Future startup must use this exact server and pass this exact socket. Never
    infer the listener from ASGI scope['server'], Host, Origin or configuration alone.
    """

    application: ASGIApp
    socket: socket.socket
    server: uvicorn.Server
    process_id: int = field(default_factory=os.getpid)

    def is_qualified(self) -> bool:
        config = self.server.config
        if (
            self.process_id != os.getpid()
            or not self.server.started
            or self.server.should_exit
            or self.server.force_exit
            or config.app is not self.application
            or config.workers != 1
            or config.reload
            or config.proxy_headers
            or config.forwarded_allow_ips != ""
            or config.root_path
            or config.uds is not None
            or config.fd is not None
            or config.is_ssl
        ):
            return False
        try:
            address = self.socket.getsockname()
            if (
                address[0] not in {"127.0.0.1", "::1"}
                or config.host != address[0]
                or config.port != address[1]
                or not self.socket.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN)
            ):
                return False
            servers = getattr(self.server, "servers", [])
            return bool(
                len(servers) == 1
                and servers[0].is_serving()
                and len(servers[0].sockets) == 1
                and servers[0].sockets[0].fileno() == self.socket.fileno()
                and servers[0].sockets[0].getsockname() == address
            )
        except OSError:
            return False


@contextmanager
def prepare_local_execution_server(
    application: ASGIApp, *, host: str = "127.0.0.1", port: int = 8000
) -> Iterator[PreparedLocalExecutionServer]:
    """Reserve an exclusive loopback socket; do not start HTTP or workflow tasks.

    Port zero supports isolated qualification tests. A future production entry point
    must own this context through HTTP shutdown. No command is activated by M5F-3A.
    """
    if host not in {"127.0.0.1", "::1"} or type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("Local execution requires an explicit loopback address and valid port.")
    family = socket.AF_INET6 if host == "::1" else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as listener:
        if os.name == "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        if family == socket.AF_INET6:
            listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        listener.bind((host, port))
        listener.listen(16)
        actual_port = listener.getsockname()[1]
        config = uvicorn.Config(
            application,
            host=host,
            port=actual_port,
            workers=1,
            reload=False,
            proxy_headers=False,
            forwarded_allow_ips="",
            lifespan="on",
            log_config=None,
            access_log=False,
        )
        yield PreparedLocalExecutionServer(application, listener, uvicorn.Server(config))
