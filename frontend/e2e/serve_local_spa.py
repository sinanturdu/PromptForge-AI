"""Test-only composition: migrated disposable SQLite, scripted fake, real owned SPA listener."""

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
    from app.llm.providers.fake import FakeLLMProvider
    from app.llm.schemas import ModelSettings
    from workflow_read_fixtures import script

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
        patch("app.main.create_provider", return_value=FakeLLMProvider(script(["qualified"]))),
        prepare_local_execution_server(app, port=0) as original,
    ):
        print(f"PROMPTFORGE_SPA_E2E_PORT={original.socket.getsockname()[1]}", flush=True)
        command.upgrade(build_alembic_config(settings.database_url), "head")
        prepared = prepare_execution_startup(original)
        install_local_spa(app, frontend / "dist", transport=prepared)
        prepared.server.run(sockets=[prepared.socket])


if __name__ == "__main__":
    main()
