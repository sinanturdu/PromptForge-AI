"""Explicit local execution startup. Ordinary Uvicorn/app construction stays read-only."""

import argparse
import asyncio
import os
import socket
from contextlib import AsyncExitStack
from dataclasses import replace
from pathlib import Path
from types import FrameType
from uuid import UUID

import uvicorn
from fastapi import FastAPI
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import DEFAULT_SQLITE_DB_PATH, Settings, build_sqlite_url
from app.core.execution_environment import PreparedLocalExecutionServer, qualify_execution_runtime
from app.core.execution_supervisor import WorkflowExecutionSupervisor
from app.db.write_transactions import create_write_engine
from app.llm.contracts import LLMProvider
from app.llm.schemas import ModelSettings
from app.modules.prompts.workflow_api_schemas import WorkflowAPIModel
from app.modules.prompts.workflow_persistence import WorkflowPersistenceService
from app.modules.prompts.workflow_readback import WorkflowReadbackService


class ExecutionModels(WorkflowAPIModel):
    architect: ModelSettings
    critic: ModelSettings


class ApplicationExecutionOwner:
    """Borrows lifespan provider/reader, owns the supervisor and explicit writer."""

    def __init__(
        self,
        settings: Settings,
        provider: LLMProvider,
        readback: WorkflowReadbackService,
        models: ExecutionModels,
    ) -> None:
        self._settings, self._provider, self._readback, self._models = (
            settings,
            provider,
            readback,
            models,
        )
        self.supervisor: WorkflowExecutionSupervisor | None = None
        self.persistence: WorkflowPersistenceService | None = None
        self._writer: AsyncEngine | None = None
        self._closed = False
        self._activation_lock = asyncio.Lock()

    async def activate(self, transport: PreparedLocalExecutionServer) -> None:
        async with self._activation_lock:
            await self._activate(transport)

    async def _activate(self, transport: PreparedLocalExecutionServer) -> None:
        settings = self._settings
        if (
            self._closed
            or self.supervisor is not None
            or not (
                settings.workflow_api_enabled
                and settings.workflow_execution_enabled
                and transport.is_qualified()
            )
        ):
            raise RuntimeError("Local workflow execution environment is not qualified.")
        url = make_url(settings.database_url)
        path = None if url.database is None else Path(url.database).resolve()
        if (
            path is None
            or not path.is_file()
            or path == DEFAULT_SQLITE_DB_PATH.resolve()
            or (DEFAULT_SQLITE_DB_PATH.exists() and os.path.samefile(path, DEFAULT_SQLITE_DB_PATH))
        ):
            raise RuntimeError("Execution requires an explicit existing non-development database.")
        runtime = await qualify_execution_runtime()
        await self._readback.list_workflows(UUID(int=0), limit=1)
        if self._closed or not transport.is_qualified():
            raise RuntimeError("Local workflow execution environment is not qualified.")
        writer = create_write_engine(settings.database_url)
        try:
            persistence = WorkflowPersistenceService(writer)
            self.supervisor = WorkflowExecutionSupervisor(
                settings=settings,
                transport=transport,
                runtime=runtime,
                readback=self._readback,
                persistence=persistence,
                provider=self._provider,
                architect_settings=self._models.architect,
                critic_settings=self._models.critic,
            )
            self._writer = writer
            self.persistence = persistence
        except BaseException:
            await writer.dispose()
            raise

    async def shutdown(self) -> bool:
        self._closed = True
        if self.supervisor is not None and not (await self.supervisor.shutdown()).stopped:
            return False
        if self._writer is not None:
            await self._writer.dispose()
            self._writer = None
        self.persistence = None
        return True

    def retain_resources(self, resources: AsyncExitStack) -> None:
        if self.supervisor is None:
            raise RuntimeError("Cannot retain execution resources without a supervisor.")
        if self._writer is not None:
            resources.push_async_callback(self._writer.dispose)
            self._writer = None
        self.supervisor.retain_resources(resources)


class _ExecutionUvicornServer(uvicorn.Server):
    preparation: PreparedLocalExecutionServer | None = None

    def _close_admission(self) -> None:
        prepared = self.preparation
        if prepared is not None and isinstance(prepared.application, FastAPI):
            owner = getattr(prepared.application.state, "workflow_execution_owner", None)
            if isinstance(owner, ApplicationExecutionOwner) and owner.supervisor is not None:
                owner.supervisor.begin_shutdown()

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        # Close dispatch before should_exit invalidates the serving proof. Existing
        # in-flight calls can drain without racing the environment watchdog.
        self._close_admission()
        super().handle_exit(sig, frame)

    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        self._close_admission()
        await super().shutdown(sockets=sockets)

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets=sockets)
        prepared = self.preparation
        if (
            prepared is None
            or not prepared.is_qualified()
            or not isinstance(prepared.application, FastAPI)
        ):
            self.should_exit = True
            await self.shutdown(sockets=sockets)
            raise RuntimeError("Owned execution listener handoff failed.")
        owner = getattr(prepared.application.state, "workflow_execution_owner", None)
        if not isinstance(owner, ApplicationExecutionOwner):
            self.should_exit = True
            await self.shutdown(sockets=sockets)
            raise RuntimeError("Execution requires explicit application ownership.")
        try:
            await owner.activate(prepared)
        except BaseException:
            self.should_exit = True
            await self.shutdown(sockets=sockets)
            raise


def prepare_execution_startup(
    prepared: PreparedLocalExecutionServer,
) -> PreparedLocalExecutionServer:
    """Activate only after the same owned socket/config has actually been handed off."""
    server = _ExecutionUvicornServer(prepared.server.config)
    server.config.timeout_graceful_shutdown = 30
    result = replace(prepared, server=server)
    server.preparation = result
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Explicit loopback-only internal workflow runner")
    parser.add_argument(
        "--database", type=Path, required=True, help="Existing migrated non-development SQLite file"
    )
    parser.add_argument("--architect-model", required=True)
    parser.add_argument("--critic-model", required=True)
    parser.add_argument("--host", choices=("127.0.0.1", "::1"), default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    settings = Settings(database_url=build_sqlite_url(args.database))
    if not settings.workflow_api_enabled or not settings.workflow_execution_enabled:
        parser.error(
            "Explicit WORKFLOW_API_ENABLED=true and WORKFLOW_EXECUTION_ENABLED=true are required."
        )
    from app.core.execution_environment import prepare_local_execution_server
    from app.main import create_app

    application = create_app(
        settings,
        execution_models=ExecutionModels(
            architect=ModelSettings(model=args.architect_model),
            critic=ModelSettings(model=args.critic_model),
        ),
    )
    with prepare_local_execution_server(application, host=args.host, port=args.port) as original:
        prepared = prepare_execution_startup(original)
        prepared.server.run(sockets=[prepared.socket])


if __name__ == "__main__":
    # App lifespan imports this module by its canonical name. Keep ownership types
    # identical when Python first executes the command as __main__.
    from app.core.execution_startup import main as run_local_execution

    run_local_execution()
