"""Tests for Milestone 3B SQLAlchemy ORM models and Alembic migrations."""

from collections.abc import Callable
from datetime import UTC
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.core.config import build_sqlite_url
from app.db.base import Base
from app.db.migrations import ALEMBIC_INI_PATH, build_alembic_config
from app.db.session import DatabaseSessionManager
from app.models import (
    AgentRun,
    AgentRunStatus,
    PromptDetailLevel,
    PromptRequest,
    PromptVersion,
    PromptVersionSource,
    ReasoningPreference,
    ResponseDetailPreference,
    TargetMode,
    WorkflowRun,
    WorkflowRunStatus,
)

EXPECTED_APPLICATION_TABLES = {
    "prompt_requests",
    "workflow_runs",
    "prompt_versions",
    "agent_runs",
    "evaluations",
}


@pytest.fixture
def anyio_backend() -> str:
    """Run async ORM tests exclusively on the asyncio backend."""
    return "asyncio"


@pytest.fixture
def migrated_sqlite_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Create and migrate an isolated temporary SQLite database independent of CWD."""
    unrelated_cwd = tmp_path / "arbitrary_working_dir"
    unrelated_cwd.mkdir()
    monkeypatch.chdir(unrelated_cwd)

    db_file = tmp_path / "db" / "m3b_test.db"
    db_url = build_sqlite_url(db_file)
    alembic_cfg = build_alembic_config(db_url)
    command.upgrade(alembic_cfg, "head")
    return db_url


@pytest.mark.anyio
async def test_initial_migration_tables_primary_keys_foreign_keys_and_metadata_sync(
    migrated_sqlite_url: str,
) -> None:
    manager = DatabaseSessionManager(migrated_sqlite_url)
    try:
        async with manager.engine.connect() as async_conn:

            def _inspect_schema(
                sync_conn: Connection,
            ) -> tuple[
                set[str],
                dict[str, list[str]],
                dict[str, set[tuple[tuple[str, ...], str, tuple[str, ...]]]],
                list[object],
            ]:
                inspector = inspect(sync_conn)
                tables = set(inspector.get_table_names())
                pks = {
                    table: inspector.get_pk_constraint(table)["constrained_columns"]
                    for table in EXPECTED_APPLICATION_TABLES
                }
                fks = {
                    table: {
                        (
                            tuple(fk["constrained_columns"]),
                            str(fk["referred_table"]),
                            tuple(fk["referred_columns"]),
                        )
                        for fk in inspector.get_foreign_keys(table)
                    }
                    for table in EXPECTED_APPLICATION_TABLES
                }
                migration_ctx = MigrationContext.configure(
                    sync_conn,
                    opts={"compare_type": True, "render_as_batch": True},
                )
                diffs = list(compare_metadata(migration_ctx, Base.metadata))
                return tables, pks, fks, diffs

            tables, pks, fks, schema_diffs = await async_conn.run_sync(_inspect_schema)

        assert EXPECTED_APPLICATION_TABLES.issubset(tables)
        assert tables == EXPECTED_APPLICATION_TABLES | {"alembic_version"}

        for table_name in EXPECTED_APPLICATION_TABLES:
            assert pks[table_name] == ["id"]

        assert fks["prompt_requests"] == set()
        assert fks["workflow_runs"] == {
            (("prompt_request_id",), "prompt_requests", ("id",)),
            (("final_version_id",), "prompt_versions", ("id",)),
        }
        assert fks["agent_runs"] == {
            (("workflow_run_id",), "workflow_runs", ("id",)),
            (("baseline_evaluation_id",), "evaluations", ("id",)),
        }
        assert fks["prompt_versions"] == {
            (("prompt_request_id",), "prompt_requests", ("id",)),
            (("workflow_run_id",), "workflow_runs", ("id",)),
            (("parent_version_id",), "prompt_versions", ("id",)),
            (("agent_run_id",), "agent_runs", ("id",)),
        }
        assert fks["evaluations"] == {
            (("workflow_run_id",), "workflow_runs", ("id",)),
            (
                ("prompt_version_id", "workflow_run_id"),
                "prompt_versions",
                ("id", "workflow_run_id"),
            ),
            (("critic_agent_run_id", "workflow_run_id"), "agent_runs", ("id", "workflow_run_id")),
        }
        assert schema_diffs == []
    finally:
        await manager.dispose()


@pytest.mark.anyio
async def test_orm_relationships_circular_final_version_and_historical_delete_protection(
    migrated_sqlite_url: str,
) -> None:
    manager = DatabaseSessionManager(migrated_sqlite_url)
    try:
        async with manager.session() as session:
            prompt_request = PromptRequest(
                raw_prompt="Design an inventory service for an online game.",
                user_context="Python FastAPI backend.",
                output_language="en",
                target_mode=TargetMode.ANTHROPIC,
                task_profile="architecture_design",
                prompt_detail_level=PromptDetailLevel.HIGH,
                response_detail_preference=ResponseDetailPreference.COMPREHENSIVE,
                reasoning_preference=ReasoningPreference.HIGH,
            )
            workflow_run = WorkflowRun(
                prompt_request=prompt_request,
                status=WorkflowRunStatus.GENERATING,
                max_evaluation_rounds=3,
                current_round=2,
                completed_rounds=2,
                config_snapshot={"pass_threshold": 85},
            )
            agent_run_r1 = AgentRun(
                workflow_run=workflow_run,
                agent_name="prompt_engineer",
                agent_version="1.0.0",
                instruction_version="create.v1",
                operation="create",
                evaluation_round=1,
                provider="fake",
                model="fake-engineer-v1",
                parameters={"temperature": 0.4},
                status=AgentRunStatus.COMPLETED,
                attempt_count=1,
                repair_attempted=False,
                input_tokens=180,
                output_tokens=420,
                latency_ms=310,
            )
            version_1 = PromptVersion(
                prompt_request=prompt_request,
                workflow_run=workflow_run,
                agent_run=agent_run_r1,
                version_number=1,
                evaluation_round=1,
                source=PromptVersionSource.ENGINEER_CREATE,
                target_mode=TargetMode.ANTHROPIC,
                renderer_name="AnthropicPromptRenderer",
                renderer_version="1.0.0",
                canonical_prompt={"role": "Senior Backend Architect", "task": "Design inventory"},
                rendered_prompt=(
                    "<role>Senior Backend Architect</role><task>Design inventory</task>"
                ),
                analysis={"confidence": 0.88},
                change_summary=["Initial structured prompt"],
            )
            agent_run_r2 = AgentRun(
                workflow_run=workflow_run,
                agent_name="prompt_engineer",
                agent_version="1.0.0",
                instruction_version="revise.v1",
                operation="revise",
                evaluation_round=2,
                provider="fake",
                model="fake-engineer-v1",
                status=AgentRunStatus.COMPLETED,
            )
            version_2 = PromptVersion(
                prompt_request=prompt_request,
                workflow_run=workflow_run,
                parent_version=version_1,
                agent_run=agent_run_r2,
                version_number=2,
                evaluation_round=2,
                source=PromptVersionSource.ENGINEER_REVISE,
                target_mode=TargetMode.ANTHROPIC,
                renderer_name="AnthropicPromptRenderer",
                renderer_version="1.0.0",
                canonical_prompt={
                    "role": "Senior Backend Architect",
                    "task": "Design concurrent inventory",
                },
                rendered_prompt=(
                    "<role>Senior Backend Architect</role><task>Design concurrent inventory</task>"
                ),
                change_summary=["Added concurrency constraints"],
            )
            workflow_run.status = WorkflowRunStatus.COMPLETED
            workflow_run.final_version = version_2
            workflow_run.final_score = Decimal("91.50")
            workflow_run.qualified = True
            workflow_run.stop_reason = "passed"

            session.add(prompt_request)
            await session.commit()
            request_id = prompt_request.id
            run_id = workflow_run.id

        async with manager.session() as read_session:
            loaded_request = (
                await read_session.execute(
                    select(PromptRequest)
                    .where(PromptRequest.id == request_id)
                    .options(
                        selectinload(PromptRequest.workflow_runs).selectinload(
                            WorkflowRun.final_version
                        ),
                        selectinload(PromptRequest.workflow_runs).selectinload(
                            WorkflowRun.prompt_versions
                        ),
                        selectinload(PromptRequest.workflow_runs)
                        .selectinload(WorkflowRun.agent_runs)
                        .selectinload(AgentRun.prompt_version),
                        selectinload(PromptRequest.prompt_versions).selectinload(
                            PromptVersion.parent_version
                        ),
                        selectinload(PromptRequest.prompt_versions).selectinload(
                            PromptVersion.child_versions
                        ),
                        selectinload(PromptRequest.prompt_versions).selectinload(
                            PromptVersion.agent_run
                        ),
                    )
                )
            ).scalar_one()

            assert loaded_request.created_at.tzinfo == UTC
            assert len(loaded_request.workflow_runs) == 1
            assert len(loaded_request.prompt_versions) == 2

            loaded_run = loaded_request.workflow_runs[0]
            assert loaded_run.id == run_id
            assert loaded_run.final_score == Decimal("91.50")
            assert loaded_run.final_version is not None
            assert loaded_run.final_version.version_number == 2
            assert len(loaded_run.prompt_versions) == 2
            assert len(loaded_run.agent_runs) == 2
            assert {
                ar.prompt_version.version_number
                for ar in loaded_run.agent_runs
                if ar.prompt_version is not None
            } == {1, 2}

            loaded_v1, loaded_v2 = loaded_request.prompt_versions
            assert loaded_v2.parent_version is not None
            assert loaded_v2.parent_version.id == loaded_v1.id
            assert [child.id for child in loaded_v1.child_versions] == [loaded_v2.id]
            assert loaded_v1.agent_run is not None
            assert loaded_v1.agent_run.operation == "create"

        # Historical execution records must not be cascade-deleted when deleting parent request.
        with pytest.raises(IntegrityError):
            async with manager.session() as delete_session:
                target_req = await delete_session.get(PromptRequest, request_id)
                assert target_req is not None
                await delete_session.delete(target_req)
                await delete_session.commit()
    finally:
        await manager.dispose()


@pytest.mark.anyio
async def test_unique_and_foreign_key_constraints_reject_invalid_records(
    migrated_sqlite_url: str,
) -> None:
    manager = DatabaseSessionManager(migrated_sqlite_url)
    try:
        async with manager.session() as session:
            req_a = PromptRequest(raw_prompt="Request A")
            req_b = PromptRequest(raw_prompt="Request B")
            session.add_all([req_a, req_b])
            await session.flush()

            v1_a = PromptVersion(
                prompt_request_id=req_a.id,
                version_number=1,
                evaluation_round=1,
                source=PromptVersionSource.ENGINEER_CREATE,
                target_mode=TargetMode.GENERIC,
                renderer_name="GenericPromptRenderer",
                renderer_version="1.0.0",
                canonical_prompt={"task": "A"},
                rendered_prompt="# Task\nA",
            )
            # Same version_number=1 on a different PromptRequest is valid.
            v1_b = PromptVersion(
                prompt_request_id=req_b.id,
                version_number=1,
                evaluation_round=1,
                source=PromptVersionSource.ENGINEER_CREATE,
                target_mode=TargetMode.GENERIC,
                renderer_name="GenericPromptRenderer",
                renderer_version="1.0.0",
                canonical_prompt={"task": "B"},
                rendered_prompt="# Task\nB",
            )
            session.add_all([v1_a, v1_b])
            await session.commit()
            req_a_id = req_a.id

        # Duplicate (prompt_request_id, version_number) must fail.
        with pytest.raises(IntegrityError):
            async with manager.session() as dup_session:
                dup_version = PromptVersion(
                    prompt_request_id=req_a_id,
                    version_number=1,
                    evaluation_round=2,
                    source=PromptVersionSource.ENGINEER_REVISE,
                    target_mode=TargetMode.GENERIC,
                    renderer_name="GenericPromptRenderer",
                    renderer_version="1.0.0",
                    canonical_prompt={"task": "Duplicate"},
                    rendered_prompt="# Task\nDuplicate",
                )
                dup_session.add(dup_version)
                await dup_session.commit()

        # Duplicate agent_run_id across two PromptVersions must fail.
        async with manager.session() as ar_session:
            wf_run = WorkflowRun(prompt_request_id=req_a_id)
            ar_one = AgentRun(
                workflow_run=wf_run,
                agent_name="prompt_engineer",
                agent_version="1.0.0",
                instruction_version="revise.v1",
                operation="revise",
                evaluation_round=2,
                provider="fake",
                model="fake-model",
                status=AgentRunStatus.COMPLETED,
            )
            pv_with_ar = PromptVersion(
                prompt_request_id=req_a_id,
                workflow_run=wf_run,
                agent_run=ar_one,
                version_number=2,
                evaluation_round=2,
                source=PromptVersionSource.ENGINEER_REVISE,
                target_mode=TargetMode.GENERIC,
                renderer_name="GenericPromptRenderer",
                renderer_version="1.0.0",
                canonical_prompt={"task": "V2"},
                rendered_prompt="# Task\nV2",
            )
            ar_session.add_all([wf_run, ar_one, pv_with_ar])
            await ar_session.commit()
            shared_ar_id = ar_one.id

        with pytest.raises(IntegrityError):
            async with manager.session() as dup_ar_session:
                dup_ar_pv = PromptVersion(
                    prompt_request_id=req_a_id,
                    agent_run_id=shared_ar_id,
                    version_number=3,
                    evaluation_round=3,
                    source=PromptVersionSource.ENGINEER_REVISE,
                    target_mode=TargetMode.GENERIC,
                    renderer_name="GenericPromptRenderer",
                    renderer_version="1.0.0",
                    canonical_prompt={"task": "V3 reusing same agent_run_id"},
                    rendered_prompt="# Task\nV3",
                )
                dup_ar_session.add(dup_ar_pv)
                await dup_ar_session.commit()

        # Non-existent foreign key must fail.
        with pytest.raises(IntegrityError):
            async with manager.session() as bad_fk_session:
                orphan_run = WorkflowRun(prompt_request_id=uuid4())
                bad_fk_session.add(orphan_run)
                await bad_fk_session.commit()
    finally:
        await manager.dispose()


@pytest.mark.anyio
async def test_evaluation_rounds_and_enum_check_constraints_are_enforced(
    migrated_sqlite_url: str,
) -> None:
    manager = DatabaseSessionManager(migrated_sqlite_url)
    try:
        async with manager.session() as session:
            req = PromptRequest(raw_prompt="Check constraints validation request.")
            run = WorkflowRun(prompt_request=req, max_evaluation_rounds=3)
            session.add_all([req, run])
            await session.commit()
            req_id = req.id
            run_id = run.id

        # WorkflowRun: max_evaluation_rounds cannot be 0 or 4, and round bounds must hold.
        for bad_max, cur_round, comp_round in (
            (0, 0, 0),
            (4, 0, 0),
            (3, 4, 0),
            (2, 3, 0),
            (3, 1, 2),
            (3, -1, 0),
        ):
            with pytest.raises(IntegrityError):
                async with manager.session() as bad_run_session:
                    bad_run_session.add(
                        WorkflowRun(
                            prompt_request_id=req_id,
                            max_evaluation_rounds=bad_max,
                            current_round=cur_round,
                            completed_rounds=comp_round,
                        )
                    )
                    await bad_run_session.commit()

        # PromptVersion: evaluation_round cannot be 0 or 4.
        for invalid_round in (0, 4, -1):
            with pytest.raises(IntegrityError):
                async with manager.session() as bad_pv_session:
                    bad_pv_session.add(
                        PromptVersion(
                            prompt_request_id=req_id,
                            workflow_run_id=run_id,
                            version_number=10,
                            evaluation_round=invalid_round,
                            source=PromptVersionSource.ENGINEER_CREATE,
                            target_mode=TargetMode.GENERIC,
                            renderer_name="GenericPromptRenderer",
                            renderer_version="1.0.0",
                            canonical_prompt={"task": "Invalid round"},
                            rendered_prompt="Invalid round",
                        )
                    )
                    await bad_pv_session.commit()

        # AgentRun: evaluation_round cannot be 0 or 4.
        for invalid_round in (0, 4):
            with pytest.raises(IntegrityError):
                async with manager.session() as bad_ar_session:
                    bad_ar_session.add(
                        AgentRun(
                            workflow_run_id=run_id,
                            agent_name="prompt_critic",
                            agent_version="1.0.0",
                            instruction_version="evaluate.v1",
                            operation="evaluate",
                            evaluation_round=invalid_round,
                            provider="fake",
                            model="fake-critic",
                            status=AgentRunStatus.FAILED,
                            error_message="Should fail check constraint",
                        )
                    )
                    await bad_ar_session.commit()

        # PromptRequest: invalid target_mode is rejected.
        with pytest.raises(IntegrityError):
            async with manager.session() as bad_mode_session:
                bad_mode_session.add(
                    PromptRequest(raw_prompt="Valid text", target_mode="unsupported_vendor")
                )
                await bad_mode_session.commit()
    finally:
        await manager.dispose()


@pytest.mark.anyio
async def test_migration_downgrade_and_re_upgrade_succeed_with_circular_rows(
    migrated_sqlite_url: str,
    default_db_guard: Callable[[], None],
) -> None:
    alembic_cfg = build_alembic_config(migrated_sqlite_url)

    # Populate all four tables including the circular WorkflowRun.final_version_id reference.
    manager = DatabaseSessionManager(migrated_sqlite_url)
    try:
        async with manager.session() as session:
            req = PromptRequest(raw_prompt="Circular downgrade verification prompt.")
            run = WorkflowRun(
                prompt_request=req,
                status=WorkflowRunStatus.COMPLETED,
                max_evaluation_rounds=3,
                current_round=1,
                completed_rounds=1,
            )
            agent_run = AgentRun(
                workflow_run=run,
                agent_name="prompt_engineer",
                agent_version="1.0.0",
                instruction_version="create.v1",
                operation="create",
                evaluation_round=1,
                provider="fake",
                model="fake-model",
                status=AgentRunStatus.COMPLETED,
                attempt_count=1,
                repair_attempted=False,
            )
            version = PromptVersion(
                # This fixture intentionally remains representable by 0001.
                prompt_request=req,
                workflow_run=run,
                agent_run=agent_run,
                version_number=1,
                evaluation_round=1,
                source=PromptVersionSource.ENGINEER_CREATE,
                target_mode=TargetMode.OPENAI,
                renderer_name="OpenAIPromptRenderer",
                renderer_version="1.0.0",
                canonical_prompt={"task": "Verify reversible migration"},
                rendered_prompt="# Task\nVerify reversible migration",
            )
            run.final_version = version
            session.add(req)
            await session.commit()
    finally:
        await manager.dispose()

    # Downgrade to base while circular data exists and PRAGMA foreign_keys=ON is active.
    command.downgrade(alembic_cfg, "base")

    post_downgrade_manager = DatabaseSessionManager(migrated_sqlite_url)
    try:
        async with post_downgrade_manager.engine.connect() as conn:
            remaining_tables = await conn.run_sync(
                lambda sync_conn: set(inspect(sync_conn).get_table_names())
            )
        assert EXPECTED_APPLICATION_TABLES.isdisjoint(remaining_tables)
    finally:
        await post_downgrade_manager.dispose()

    # Upgrade to head again and confirm ORM persistence works cleanly.
    command.upgrade(alembic_cfg, "head")

    reupgraded_manager = DatabaseSessionManager(migrated_sqlite_url)
    try:
        async with reupgraded_manager.engine.connect() as conn:
            restored_tables = await conn.run_sync(
                lambda sync_conn: set(inspect(sync_conn).get_table_names())
            )
        assert EXPECTED_APPLICATION_TABLES.issubset(restored_tables)

        async with reupgraded_manager.session() as session:
            fresh_req = PromptRequest(raw_prompt="Post re-upgrade request.")
            session.add(fresh_req)
            await session.commit()
            assert fresh_req.id is not None
    finally:
        await reupgraded_manager.dispose()

    default_db_guard()


@pytest.mark.anyio
async def test_alembic_ini_resolves_settings_database_url_from_any_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    default_db_guard: Callable[[], None],
) -> None:
    outside_dir = tmp_path / "external_caller_dir"
    outside_dir.mkdir()
    monkeypatch.chdir(outside_dir)

    env_db_path = tmp_path / "env_configured.db"
    env_db_url = build_sqlite_url(env_db_path)
    monkeypatch.setenv("DATABASE_URL", env_db_url)

    cli_like_cfg = Config(str(ALEMBIC_INI_PATH))
    cli_like_cfg.attributes["configure_logger"] = False
    command.upgrade(cli_like_cfg, "head")

    manager = DatabaseSessionManager(env_db_url)
    try:
        async with manager.engine.connect() as conn:
            tables = await conn.run_sync(
                lambda sync_conn: set(inspect(sync_conn).get_table_names())
            )
        assert EXPECTED_APPLICATION_TABLES.issubset(tables)
    finally:
        await manager.dispose()

    command.downgrade(cli_like_cfg, "base")
    default_db_guard()
