"""Isolated real SQLite migrations/constraints; never run migrations on development data."""

import json
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Session, selectinload

from app.core.config import build_sqlite_url
from app.db.base import Base
from app.db.migrations import build_alembic_config
from app.models import AgentRun, Evaluation, PromptRequest, PromptVersion, WorkflowRun

HEAD = "0002_workflow_persistence"
TABLES = ("prompt_requests", "workflow_runs", "agent_runs", "prompt_versions")
STAMP = "2026-11-05 12:00:00.000000"
PRIVATE = '  Keep Python 3.12 and "quotes".\n```python\ndef f():\n    return 1\n```\n'


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "schema.db"
    command.upgrade(build_alembic_config(build_sqlite_url(path)), "head")
    return path


def uid(value: int) -> str:
    return UUID(int=value).hex


def insert(connection: sqlite3.Connection, table: str, values: dict[str, Any]) -> None:
    connection.execute(
        "INSERT INTO "
        + table
        + " ("
        + ",".join(values)
        + ") VALUES ("
        + ",".join("?" for _ in values)
        + ")",
        tuple(values.values()),
    )


def request(connection: sqlite3.Connection, identity: int = 1) -> None:
    connection.execute(
        "INSERT INTO prompt_requests VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            uid(identity),
            PRIVATE,
            "Private context",
            "tr",
            "generic",
            None,
            "medium",
            "balanced",
            "auto",
            STAMP,
        ),
    )


def workflow(
    connection: sqlite3.Connection,
    identity: int = 2,
    owner: int = 1,
    status: str = "COMPLETED",
    **fields: Any,
) -> None:
    insert(
        connection,
        "workflow_runs",
        dict(
            id=uid(identity),
            prompt_request_id=uid(owner),
            status=status,
            max_evaluation_rounds=3,
            current_round=1,
            completed_rounds=0,
            created_at=STAMP,
            **fields,
        ),
    )


def agent(connection: sqlite3.Connection, identity: int = 3, owner: int = 2, **fields: Any) -> None:
    insert(
        connection,
        "agent_runs",
        dict(
            id=uid(identity),
            workflow_run_id=uid(owner),
            agent_name="scripted",
            agent_version="1.0.0",
            instruction_version="1.0.0",
            operation="evaluate",
            status="COMPLETED",
            started_at=STAMP,
            **fields,
        ),
    )


def version(
    connection: sqlite3.Connection,
    identity: int = 5,
    owner: int = 2,
    source: int = 1,
    number: int = 1,
    **fields: Any,
) -> None:
    insert(
        connection,
        "prompt_versions",
        dict(
            id=uid(identity),
            prompt_request_id=uid(source),
            workflow_run_id=uid(owner),
            version_number=number,
            evaluation_round=1,
            source="engineer_create",
            target_mode="generic",
            renderer_name="GenericPromptRenderer",
            renderer_version="1.0.0",
            canonical_prompt=json.dumps({"task": PRIVATE}),
            rendered_prompt=PRIVATE,
            created_at=STAMP,
            **fields,
        ),
    )


def evaluation(connection: sqlite3.Connection, identity: int = 6, **overrides: Any) -> None:
    fields: dict[str, Any] = dict(
        id=uid(identity),
        workflow_run_id=uid(2),
        prompt_version_id=uid(5),
        critic_agent_run_id=uid(4),
        evaluation_round=1,
        input_fingerprint="a" * 64,
        evaluated_target_mode="generic",
        evaluated_renderer_name="GenericPromptRenderer",
        evaluated_renderer_version="1.0.0",
        rubric_version="2.0.0",
        scoring_version="1.0.0",
        assessment_schema_version="1.0.0",
        assessment="{}",
        quality_status="qualified",
        total_score=90,
        passed=True,
        gate_results="{}",
        completed_at=STAMP,
        created_at=STAMP,
    )
    fields.update(overrides)
    insert(connection, "evaluations", fields)


def base(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys=ON")
    request(connection)
    workflow(connection)
    agent(connection)
    agent(connection, 4)
    version(connection, agent_run_id=uid(3))


def seed_legacy(path: Path) -> None:
    ids = [UUID(int=index).hex for index in range(1, 5)]
    request, workflow, agent, version = ids
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO prompt_requests VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                request,
                PRIVATE,
                "Private context",
                "tr",
                "generic",
                None,
                "medium",
                "balanced",
                "auto",
                STAMP,
            ),
        )
        connection.execute(
            "INSERT INTO workflow_runs (id,prompt_request_id,status,max_evaluation_rounds,"
            "current_round,completed_rounds,config_snapshot,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (workflow, request, "COMPLETED", 3, 1, 1, '{"legacy":true}', STAMP),
        )
        connection.execute(
            "INSERT INTO agent_runs (id,workflow_run_id,agent_name,agent_version,"
            "instruction_version,operation,evaluation_round,provider,model,status,"
            "attempt_count,repair_attempted,started_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                agent,
                workflow,
                "prompt_engineer",
                "1.0.0",
                "create.v1",
                "create",
                1,
                "fake",
                "legacy",
                "COMPLETED",
                1,
                False,
                STAMP,
            ),
        )
        connection.execute(
            "INSERT INTO prompt_versions (id,prompt_request_id,workflow_run_id,agent_run_id,"
            "version_number,evaluation_round,source,target_mode,renderer_name,renderer_version,"
            "canonical_prompt,rendered_prompt,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                version,
                request,
                workflow,
                agent,
                1,
                1,
                "engineer_create",
                "generic",
                "GenericPromptRenderer",
                "1.0.0",
                json.dumps({"task": PRIVATE}),
                PRIVATE,
                STAMP,
            ),
        )
        connection.execute(
            "UPDATE workflow_runs SET final_version_id=? WHERE id=?", (version, workflow)
        )


def test_populated_legacy_upgrade_preserves_all_columns_and_circular_references(
    tmp_path: Path,
) -> None:
    path = tmp_path / "populated.db"
    config = build_alembic_config(build_sqlite_url(path))
    command.upgrade(config, "0001_initial_schema")
    seed_legacy(path)
    with sqlite3.connect(path) as connection:
        columns = {
            table: [row[1] for row in connection.execute('PRAGMA table_info("' + table + '")')]
            for table in TABLES
        }
        before = {
            table: connection.execute('SELECT * FROM "' + table + '"').fetchall()
            for table in TABLES
        }
    command.upgrade(config, "head")
    with sqlite3.connect(path) as connection:
        for table in TABLES:
            selected = ",".join('"' + column + '"' for column in columns[table])
            assert (
                connection.execute("SELECT " + selected + ' FROM "' + table + '"').fetchall()
                == before[table]
            )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (HEAD,)


@pytest.fixture
def db(database: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(database)
    base(connection)
    connection.commit()
    try:
        yield connection
    finally:
        connection.close()


@pytest.mark.parametrize("status", ["PENDING", "GENERATING", "EVALUATING", "REVISING"])
def test_active_uniqueness_across_connections(
    db: sqlite3.Connection, database: Path, status: str
) -> None:
    workflow(db, 10, status=status)
    db.commit()
    with sqlite3.connect(database) as second:
        second.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError):
            workflow(second, 11, status="PENDING")
    assert db.execute(
        "SELECT count(*) FROM workflow_runs WHERE status=?", (status,)
    ).fetchone() == (1,)


@pytest.mark.parametrize("status", ["COMPLETED", "FAILED", "CLARIFICATION_REQUIRED"])
def test_terminal_history_allows_new_run(db: sqlite3.Connection, status: str) -> None:
    workflow(db, 10, status=status)
    workflow(db, 11, status=status)
    workflow(db, 12, status="PENDING")
    assert db.execute("SELECT count(*) FROM workflow_runs").fetchone() == (4,)


@pytest.mark.parametrize("field", ["operation_key", "revision_attempt_key"])
def test_scoped_operation_uniqueness(db: sqlite3.Connection, field: str) -> None:
    key = "architect.create.1" if field == "operation_key" else "b" * 64
    keys: dict[str, Any] = {field: key}
    agent(db, 10, **keys)
    workflow(db, 12)
    agent(db, 11, owner=12, **keys)
    with pytest.raises(sqlite3.IntegrityError):
        agent(db, 13, **keys)


def test_unknown_telemetry_and_null_keys(db: sqlite3.Connection) -> None:
    assert db.execute(
        "SELECT provider,model,attempt_count,repair_attempted,operation_key "
        "FROM agent_runs WHERE id=?",
        (uid(3),),
    ).fetchone() == (None, None, None, None, None)
    agent(db, 10)
    assert db.execute("SELECT count(*) FROM agent_runs WHERE operation_key IS NULL").fetchone() == (
        3,
    )


def test_client_idempotency_scope(db: sqlite3.Connection) -> None:
    workflow(db, 10, client_idempotency_key_digest="b" * 64)
    request(db, 12)
    workflow(db, 13, owner=12, client_idempotency_key_digest="b" * 64)
    with pytest.raises(sqlite3.IntegrityError):
        workflow(db, 11, client_idempotency_key_digest="b" * 64)


@pytest.mark.parametrize("duplicate", ["round", "version", "fingerprint", "critic"])
def test_evaluation_uniqueness(db: sqlite3.Connection, duplicate: str) -> None:
    evaluation(db)
    agent(db, 8)
    agent(db, 9)
    version(db, 7, number=2, agent_run_id=uid(8))
    fields: dict[str, Any] = dict(
        evaluation_round=2,
        prompt_version_id=uid(7),
        critic_agent_run_id=uid(9),
        input_fingerprint="b" * 64,
    )
    key, value = {
        "round": ("evaluation_round", 1),
        "version": ("prompt_version_id", uid(5)),
        "fingerprint": ("input_fingerprint", "a" * 64),
        "critic": ("critic_agent_run_id", uid(4)),
    }[duplicate]
    fields[key] = value
    with pytest.raises(sqlite3.IntegrityError):
        evaluation(db, 10, **fields)


@pytest.mark.parametrize(
    "fields",
    [
        dict(evaluation_round=0),
        dict(evaluation_round=4),
        dict(evaluation_round=1.5),
        dict(input_fingerprint="short"),
        dict(evaluated_target_mode="unknown"),
        dict(total_score=101),
        dict(total_score=None),
        dict(passed=False),
        dict(quality_status="unknown"),
        dict(completed_at=None),
    ],
)
def test_evaluation_structural_checks(db: sqlite3.Connection, fields: dict[str, Any]) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        evaluation(db, **fields)


def test_unassessable_null_total(db: sqlite3.Connection) -> None:
    evaluation(db, quality_status="unassessable", total_score=None, passed=False)
    assert db.execute("SELECT total_score,passed FROM evaluations").fetchone() == (None, 0)


@pytest.mark.parametrize("reference", ["version", "critic", "missing_workflow"])
def test_evaluation_database_ancestry(db: sqlite3.Connection, reference: str) -> None:
    request(db, 11)
    workflow(db, 12, owner=11)
    agent(db, 13, owner=12)
    version(db, 15, owner=12, source=11, agent_run_id=uid(13))
    fields: dict[str, Any] = (
        {"prompt_version_id": uid(15)}
        if reference == "version"
        else {"critic_agent_run_id": uid(13)}
        if reference == "critic"
        else {"workflow_run_id": uid(99)}
    )
    with pytest.raises(sqlite3.IntegrityError):
        evaluation(db, **fields)


@pytest.mark.parametrize(
    "table,identity",
    [
        ("workflow_runs", 2),
        ("prompt_requests", 1),
        ("prompt_versions", 5),
        ("agent_runs", 4),
        ("evaluations", 6),
    ],
)
def test_historical_delete_restrictions(db: sqlite3.Connection, table: str, identity: int) -> None:
    evaluation(db)
    agent(db, 10, baseline_evaluation_id=uid(6))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("DELETE FROM " + table + " WHERE id=?", (uid(identity),))


def test_orm_relationships_uuid_utc_and_metadata(database: Path) -> None:
    engine = create_engine("sqlite:///" + database.as_posix())

    @event.listens_for(engine, "connect")
    def foreign_keys(connection: Any, _record: Any) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    try:
        with engine.connect() as connection:
            assert set(inspect(connection).get_table_names()) == set(TABLES) | {
                "evaluations",
                "alembic_version",
            }
            context = MigrationContext.configure(
                connection, opts={"compare_type": True, "compare_server_default": True}
            )
            assert compare_metadata(context, Base.metadata) == []
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        with Session(engine) as session:
            source = PromptRequest(raw_prompt=PRIVATE)
            run = WorkflowRun(prompt_request=source)
            creator = AgentRun(
                workflow_run=run,
                agent_name="architect",
                agent_version="1.0.0",
                instruction_version="1.0.0",
                operation="create",
            )
            critic = AgentRun(
                workflow_run=run,
                agent_name="critic",
                agent_version="1.0.0",
                instruction_version="1.0.0",
                operation="evaluate",
            )
            candidate = PromptVersion(
                prompt_request=source,
                workflow_run=run,
                agent_run=creator,
                version_number=1,
                evaluation_round=1,
                source="engineer_create",
                target_mode="generic",
                renderer_name="GenericPromptRenderer",
                renderer_version="1.0.0",
                canonical_prompt={"task": PRIVATE},
                rendered_prompt=PRIVATE,
            )
            session.add(source)
            session.flush()
            result = Evaluation(
                workflow_run_id=run.id,
                prompt_version_id=candidate.id,
                critic_agent_run_id=critic.id,
                evaluation_round=1,
                input_fingerprint="a" * 64,
                evaluated_target_mode="generic",
                evaluated_renderer_name="GenericPromptRenderer",
                evaluated_renderer_version="1.0.0",
                rubric_version="2.0.0",
                scoring_version="1.0.0",
                assessment_schema_version="1.0.0",
                assessment={},
                quality_status="unassessable",
                total_score=None,
                passed=False,
                gate_results={},
                completed_at=source.created_at,
            )
            session.add(result)
            session.commit()
            assert isinstance(result.id, UUID) and result.created_at.tzinfo is not None
            assert result.workflow_run.id == run.id and result.prompt_version.id == candidate.id
            assert result.critic_agent_run.id == critic.id and critic.evaluation == result
            identity = result.id
        with Session(engine) as session:
            loaded = session.scalars(
                select(Evaluation).options(selectinload(Evaluation.prompt_version))
            ).one()
            assert loaded.id == identity and loaded.completed_at.tzinfo is not None
            assert loaded.prompt_version.rendered_prompt == PRIVATE
    finally:
        engine.dispose()


def test_safe_populated_downgrade_reupgrade(tmp_path: Path) -> None:
    path = tmp_path / "safe.db"
    config = build_alembic_config(build_sqlite_url(path))
    command.upgrade(config, "0001_initial_schema")
    seed_legacy(path)
    with sqlite3.connect(path) as connection:
        before = {
            table: connection.execute('SELECT * FROM "' + table + '"').fetchall()
            for table in TABLES
        }
    command.upgrade(config, "head")
    command.downgrade(config, "0001_initial_schema")
    with sqlite3.connect(path) as connection:
        for table in TABLES:
            assert connection.execute('SELECT * FROM "' + table + '"').fetchall() == before[table]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    command.upgrade(config, "head")


@pytest.mark.parametrize(
    "mutation",
    [
        "evaluation",
        "snapshot",
        "revision",
        "claim",
        "terminal",
        "idempotency",
        "digest",
        "operation",
        "binding",
        "result",
        "metadata",
        "feedback",
        "result_kind",
        "error_code",
        "unknown_telemetry",
    ],
)
def test_downgrade_guard_preserves_all_data(database: Path, mutation: str) -> None:
    config = build_alembic_config(build_sqlite_url(database))
    seed_legacy(database)
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        if mutation == "evaluation":
            agent(connection, 8)
            evaluation(connection, prompt_version_id=uid(4), critic_agent_run_id=uid(8))
        elif mutation in {"snapshot", "revision", "claim", "terminal", "idempotency", "digest"}:
            sql, values = {
                "snapshot": ("request_snapshot=?", ('{"schema_version":"1.0.0"}',)),
                "revision": ("state_revision=?", (1,)),
                "claim": ("execution_token=?,lease_expires_at=?", (uid(99), STAMP)),
                "terminal": ("status='CLARIFICATION_REQUIRED'", ()),
                "idempotency": ("client_idempotency_key_digest=?", ("b" * 64,)),
                "digest": ("request_content_digest=?", ("b" * 64,)),
            }[mutation]
            connection.execute("UPDATE workflow_runs SET " + sql, values)
        else:
            field, value = {
                "operation": ("operation_key", "architect.create.1"),
                "binding": ("binding_data", "{}"),
                "result": ("result_data", "{}"),
                "metadata": ("call_metadata", "{}"),
                "feedback": ("feedback_projection_digest", "b" * 64),
                "result_kind": ("result_kind", "candidate"),
                "error_code": ("error_code", "interrupted"),
                "unknown_telemetry": ("attempt_count", None),
            }[mutation]
            connection.execute("UPDATE agent_runs SET " + field + "=?", (value,))
        connection.commit()
        before = "\n".join(connection.iterdump())
    with pytest.raises(RuntimeError, match="Downgrade refused"):
        command.downgrade(config, "0001_initial_schema")
    with sqlite3.connect(database) as connection:
        assert "\n".join(connection.iterdump()) == before
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_duplicate_legacy_active_runs_refuse_upgrade(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.db"
    config = build_alembic_config(build_sqlite_url(path))
    command.upgrade(config, "0001_initial_schema")
    seed_legacy(path)
    with sqlite3.connect(path) as connection:
        workflow(connection, 10, status="PENDING")
        workflow(connection, 11, status="REVISING")
        connection.commit()
        before = "\n".join(connection.iterdump())
    with pytest.raises(RuntimeError, match="multiple active workflows"):
        command.upgrade(config, "head")
    with sqlite3.connect(path) as connection:
        assert "\n".join(connection.iterdump()) == before


def test_ddl_failure_rolls_back_rebuilds_and_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alembic.operations import Operations

    path = tmp_path / "rollback.db"
    config = build_alembic_config(build_sqlite_url(path))
    command.upgrade(config, "0001_initial_schema")
    seed_legacy(path)
    with sqlite3.connect(path) as connection:
        before = "\n".join(connection.iterdump())
    original = Operations.create_table

    def fail(self: Operations, name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "evaluations":
            raise RuntimeError("Injected DDL failure")
        return original(self, name, *args, **kwargs)

    monkeypatch.setattr(Operations, "create_table", fail)
    with pytest.raises(RuntimeError, match="Injected DDL failure"):
        command.upgrade(config, "head")
    with sqlite3.connect(path) as connection:
        assert "\n".join(connection.iterdump()) == before


@pytest.mark.parametrize("enabled", [True, False])
def test_migration_restores_connection_foreign_keys(tmp_path: Path, enabled: bool) -> None:
    path = tmp_path / "connection.db"
    engine = create_engine("sqlite:///" + path.as_posix())
    config = build_alembic_config(build_sqlite_url(path))
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql(
                "PRAGMA foreign_keys=ON" if enabled else "PRAGMA foreign_keys=OFF"
            )
            connection.commit()
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            assert not connection.in_transaction()
            assert bool(connection.exec_driver_sql("PRAGMA foreign_keys").scalar()) == enabled
            connection.rollback()
            command.downgrade(config, "0001_initial_schema")
            assert not connection.in_transaction()
            assert bool(connection.exec_driver_sql("PRAGMA foreign_keys").scalar()) == enabled
            connection.rollback()
            with connection.begin():
                with pytest.raises(RuntimeError, match="idle dedicated connection"):
                    command.upgrade(config, "head")
                assert connection.in_transaction()
    finally:
        engine.dispose()


def test_postgresql_ddl_compilation() -> None:
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateIndex, CreateTable

    dialect_factory: Callable[[], Dialect] = postgresql.dialect
    dialect = dialect_factory()
    sql = str(CreateTable(Base.metadata.tables["evaluations"]).compile(dialect=dialect))
    assert "UUID" in sql and "JSON" in sql and "NUMERIC(5, 2)" in sql
    index = next(
        item
        for item in Base.metadata.tables["workflow_runs"].indexes
        if item.name == "uq_workflow_runs_active_prompt_request"
    )
    ddl = str(CreateIndex(index).compile(dialect=dialect))
    assert "UNIQUE INDEX" in ddl and "WHERE status IN" in ddl
    assert "CLARIFICATION_REQUIRED" not in ddl
