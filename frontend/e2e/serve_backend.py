"""E2E-only entry point: bind exclusively, migrate an owned database, then serve."""

import os
import socket
import sys
import tempfile
from pathlib import Path


def main() -> None:
    database = Path(sys.argv[1]).resolve()
    temporary_root = Path(tempfile.gettempdir()).resolve()
    if (
        database.parent.parent != temporary_root
        or not database.parent.name.startswith("promptforge-e2e-")
        or database.name != "e2e.sqlite3"
        or database.exists()
    ):
        raise RuntimeError("E2E requires a new database in its own temporary directory")

    # Bind before migrations. Never attach to another process on the test port.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        if os.name == "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            # Rebind after POSIX TIME_WAIT without sharing an active listening port.
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 18080))
        listener.listen(128)

        backend = Path(__file__).resolve().parents[2] / "backend"
        sys.path.insert(0, str(backend))

        import uvicorn
        from alembic import command

        from app.core.config import Settings, build_sqlite_url, get_settings
        from app.db.migrations import build_alembic_config

        database_url = build_sqlite_url(database)
        # Explicit settings override both inherited environment and backend/.env.
        settings = Settings(
            _env_file=None,
            database_url=database_url,
            api_v1_prefix="/api/v1",
            app_name=sys.argv[2],
            database_echo=False,
        )
        get_settings.cache_clear()
        # app.main creates its module-level app through this cached provider too.
        os.environ["DATABASE_URL"] = database_url
        os.environ["APP_NAME"] = settings.app_name
        os.environ["API_V1_PREFIX"] = settings.api_v1_prefix

        command.upgrade(build_alembic_config(database_url), "head")

        from app.main import create_app

        application = create_app(settings)
        # Health calls the provider directly; its environment has the same run identity.
        get_settings.cache_clear()
        config = uvicorn.Config(application, host="127.0.0.1", port=18080, log_level="warning")
        uvicorn.Server(config).run(sockets=[listener])


if __name__ == "__main__":
    main()
