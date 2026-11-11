"""Test-only composition: migrated disposable SQLite, scripted fake, real owned SPA listener."""

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch


def main() -> None:
    os.environ["LLM_PROVIDER"] = "fake"
    os.environ.pop("OPENAI_API_KEY", None)
    database = Path(sys.argv[1]).resolve()
    frontend = Path(__file__).resolve().parents[1]
    backend = frontend.parent / "backend"
    if (
        database.parent.parent != Path(tempfile.gettempdir()).resolve()
        or not database.parent.name.startswith("promptforge-spa-e2e-")
        or database.name != "e2e.sqlite3"
        or database.exists()
    ):
        raise RuntimeError("SPA E2E requires a new owned temporary database.")
    os.environ.update(
        DATABASE_URL=f"sqlite+aiosqlite:///{database.as_posix()}",
        APP_NAME=sys.argv[2],
        WORKFLOW_API_ENABLED="true",
        WORKFLOW_EXECUTION_ENABLED="true",
    )
    sys.path.insert(0, str(backend))
    sys.path.insert(0, str(backend / "tests"))
    from alembic import command
    from pydantic_settings import SettingsConfigDict

    import app.main as application
    from app.core.config import Settings, build_sqlite_url
    from app.core.execution_environment import prepare_local_execution_server
    from app.core.execution_startup import ExecutionModels, prepare_execution_startup
    from app.core.local_spa import install_local_spa
    from app.db.migrations import build_alembic_config
    from app.llm.errors import LLMError, LLMErrorCode
    from app.llm.providers.fake import FakeLLMProvider
    from app.llm.schemas import LLMRequest, LLMResponse, ModelSettings
    from workflow_read_fixtures import architect, script

    scenario = sys.argv[3] if len(sys.argv) > 3 else "early"
    controlled = len(sys.argv) > 4 and sys.argv[4] == "controlled"
    if scenario not in {
        "early",
        "three-round",
        "failed",
        "clarify",
        "not-qualified",
        "unassessable",
    }:
        raise RuntimeError("Invalid offline SPA scenario.")
    values = (
        [LLMErrorCode.AUTHENTICATION]
        if scenario == "failed"
        else [architect("clarify")]
        if scenario == "clarify"
        else script(
            ["bad", "critical", "qualified"]
            if scenario == "three-round"
            else ["critical", "critical", "critical"]
            if scenario == "not-qualified"
            else ["no_feedback"]
            if scenario == "unassessable"
            else ["qualified"]
        )
    )

    class ControlledFake(FakeLLMProvider):
        started: int = 0

        async def generate(self, request: LLMRequest) -> LLMResponse:
            self.started += 1
            print(f"PROMPTFORGE_SPA_E2E_CALL={self.started}", flush=True)
            if controlled:
                # Parent-owned stdin barrier, never an HTTP control endpoint.
                command_line = await asyncio.to_thread(sys.stdin.readline)
                if command_line.strip() != "release":
                    raise LLMError(LLMErrorCode.SCRIPT_EXHAUSTED)
            return await super().generate(request)

    class E2ESettings(Settings):
        model_config = SettingsConfigDict(env_file=None)

    settings = E2ESettings(
        database_url=build_sqlite_url(database),
        app_name=sys.argv[2],
        workflow_api_enabled=True,
        workflow_execution_enabled=True,
        llm_provider="fake",
        llm_max_retries=0,
        llm_json_repair_attempts=0,
    )
    app = application.create_app(
        settings,
        execution_models=ExecutionModels(
            architect=ModelSettings(model="fake"), critic=ModelSettings(model="fake")
        ),
    )
    with (
        patch("app.main.create_provider", return_value=ControlledFake(values)),
        prepare_local_execution_server(app, port=0) as original,
    ):
        print(f"PROMPTFORGE_SPA_E2E_PORT={original.socket.getsockname()[1]}", flush=True)
        command.upgrade(build_alembic_config(settings.database_url), "head")
        prepared = prepare_execution_startup(original)
        install_local_spa(app, frontend / "dist", transport=prepared)
        prepared.server.run(sockets=[prepared.socket])


if __name__ == "__main__":
    main()
