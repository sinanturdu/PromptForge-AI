"""Alembic migration environment supporting SQLAlchemy 2.0 Async, SQLite, and PostgreSQL."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection, make_url

import app.models  # noqa: F401
from app.core.config import Settings
from app.db.base import Base
from app.db.session import create_db_engine, is_sqlite_url

config = context.config

if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _resolve_database_url() -> str:
    """Return an explicit Alembic URL override if set, otherwise load application Settings."""
    configured_url = config.get_main_option("sqlalchemy.url")
    if configured_url and configured_url.strip():
        return configured_url.strip()
    return Settings().database_url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode without establishing a live DB connection."""
    database_url = _resolve_database_url()
    parsed_url = make_url(database_url)
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        render_as_batch=is_sqlite_url(parsed_url),
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Configure MigrationContext for the active connection and run migrations."""
    if connection.dialect.name == "sqlite":
        _run_sqlite_migrations(connection)
        return
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        render_as_batch=connection.dialect.name == "sqlite",
    )

    with context.begin_transaction():
        context.run_migrations()


def _run_sqlite_migrations(connection: Connection) -> None:
    """Rebuild referenced tables atomically, only on this dedicated migration connection.

    PRAGMA foreign_keys cannot change inside a transaction. Never silently commit a
    caller's transaction; injected SQLite connections must be idle on entry.
    Explicit BEGIN includes DDL and the Alembic version stamp despite legacy driver
    transaction control. Runtime session/engine configuration is unchanged.
    """
    if connection.in_transaction():
        raise RuntimeError("SQLite migrations require an idle dedicated connection.")
    enforce_foreign_keys = bool(connection.exec_driver_sql("PRAGMA foreign_keys").scalar())
    connection.commit()  # End only the inspection's SQLAlchemy autobegin.
    try:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
        with connection.begin():
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                compare_server_default=True,
                render_as_batch=True,
                transactional_ddl=True,
            )
            with context.begin_transaction():
                context.run_migrations()
            if connection.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
                raise RuntimeError("Migration failed foreign-key integrity verification.")
    finally:
        if connection.in_transaction():
            connection.rollback()
        connection.exec_driver_sql(
            "PRAGMA foreign_keys=ON" if enforce_foreign_keys else "PRAGMA foreign_keys=OFF"
        )
        connection.commit()


async def run_async_migrations(database_url: str) -> None:
    """Execute online migrations using the application's async engine factory."""
    connectable = create_db_engine(database_url)
    try:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode via an injected connection or async engine."""
    injected_connection: Connection | None = config.attributes.get("connection")
    if injected_connection is not None:
        do_run_migrations(injected_connection)
        return

    database_url = _resolve_database_url()
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(run_async_migrations(database_url))
    else:
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(asyncio.run, run_async_migrations(database_url)).result()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
