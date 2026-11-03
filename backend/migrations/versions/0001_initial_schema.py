"""Create core Milestone 3B tables: prompt_requests, workflow_runs, prompt_versions, agent_runs.

Revision ID: 0001_initial_schema
Revises: None
Create Date: 2026-11-03 11:50:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial_schema"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "prompt_requests",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("raw_prompt", sa.Text(), nullable=False),
        sa.Column("user_context", sa.Text(), nullable=True),
        sa.Column("output_language", sa.String(length=32), nullable=True),
        sa.Column("target_mode", sa.String(length=32), nullable=False),
        sa.Column("task_profile", sa.String(length=64), nullable=True),
        sa.Column("prompt_detail_level", sa.String(length=16), nullable=False),
        sa.Column("response_detail_preference", sa.String(length=24), nullable=False),
        sa.Column("reasoning_preference", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(trim(raw_prompt)) > 0",
            name=op.f("ck_prompt_requests_raw_prompt_not_empty"),
        ),
        sa.CheckConstraint(
            "target_mode IN ('generic', 'openai', 'anthropic', 'google')",
            name=op.f("ck_prompt_requests_target_mode_valid"),
        ),
        sa.CheckConstraint(
            "prompt_detail_level IN ('low', 'medium', 'high')",
            name=op.f("ck_prompt_requests_prompt_detail_level_valid"),
        ),
        sa.CheckConstraint(
            "response_detail_preference IN ('minimal', 'balanced', 'comprehensive')",
            name=op.f("ck_prompt_requests_response_detail_preference_valid"),
        ),
        sa.CheckConstraint(
            "reasoning_preference IN ('auto', 'low', 'medium', 'high')",
            name=op.f("ck_prompt_requests_reasoning_preference_valid"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prompt_requests")),
    )
    op.create_index(
        "ix_prompt_requests_created_at",
        "prompt_requests",
        ["created_at"],
        unique=False,
    )

    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("prompt_request_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("max_evaluation_rounds", sa.Integer(), nullable=False),
        sa.Column("current_round", sa.Integer(), nullable=False),
        sa.Column("completed_rounds", sa.Integer(), nullable=False),
        sa.Column("config_snapshot", sa.JSON(), nullable=True),
        sa.Column("final_version_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("final_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("qualified", sa.Boolean(), nullable=True),
        sa.Column("stop_reason", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('PENDING', 'GENERATING', 'EVALUATING', 'REVISING', 'COMPLETED', 'FAILED')",
            name=op.f("ck_workflow_runs_status_valid"),
        ),
        sa.CheckConstraint(
            "max_evaluation_rounds BETWEEN 1 AND 3",
            name=op.f("ck_workflow_runs_max_evaluation_rounds_range"),
        ),
        sa.CheckConstraint(
            "current_round >= 0 AND current_round <= max_evaluation_rounds",
            name=op.f("ck_workflow_runs_current_round_range"),
        ),
        sa.CheckConstraint(
            "completed_rounds >= 0 "
            "AND completed_rounds <= max_evaluation_rounds "
            "AND completed_rounds <= current_round",
            name=op.f("ck_workflow_runs_completed_rounds_range"),
        ),
        sa.CheckConstraint(
            "final_score IS NULL OR (final_score >= 0 AND final_score <= 100)",
            name=op.f("ck_workflow_runs_final_score_range"),
        ),
        sa.ForeignKeyConstraint(
            ["prompt_request_id"],
            ["prompt_requests.id"],
            name=op.f("fk_workflow_runs_prompt_request_id_prompt_requests"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflow_runs")),
    )
    op.create_index(
        "ix_workflow_runs_prompt_request_id",
        "workflow_runs",
        ["prompt_request_id"],
        unique=False,
    )
    op.create_index(
        "ix_workflow_runs_status",
        "workflow_runs",
        ["status"],
        unique=False,
    )

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("agent_name", sa.String(length=64), nullable=False),
        sa.Column("agent_version", sa.String(length=32), nullable=False),
        sa.Column("instruction_version", sa.String(length=32), nullable=False),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("evaluation_round", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("repair_attempted", sa.Boolean(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED')",
            name=op.f("ck_agent_runs_status_valid"),
        ),
        sa.CheckConstraint(
            "evaluation_round IS NULL OR (evaluation_round BETWEEN 1 AND 3)",
            name=op.f("ck_agent_runs_evaluation_round_range"),
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name=op.f("ck_agent_runs_attempt_count_non_negative"),
        ),
        sa.CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name=op.f("ck_agent_runs_input_tokens_non_negative"),
        ),
        sa.CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name=op.f("ck_agent_runs_output_tokens_non_negative"),
        ),
        sa.CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name=op.f("ck_agent_runs_latency_ms_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"],
            ["workflow_runs.id"],
            name=op.f("fk_agent_runs_workflow_run_id_workflow_runs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_runs")),
    )
    op.create_index(
        "ix_agent_runs_workflow_run_id",
        "agent_runs",
        ["workflow_run_id"],
        unique=False,
    )
    op.create_index(
        "ix_agent_runs_agent_name",
        "agent_runs",
        ["agent_name"],
        unique=False,
    )

    op.create_table(
        "prompt_versions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("prompt_request_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("parent_version_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("agent_run_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("evaluation_round", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("target_mode", sa.String(length=32), nullable=False),
        sa.Column("renderer_name", sa.String(length=64), nullable=False),
        sa.Column("renderer_version", sa.String(length=32), nullable=False),
        sa.Column("canonical_prompt", sa.JSON(), nullable=False),
        sa.Column("rendered_prompt", sa.Text(), nullable=False),
        sa.Column("analysis", sa.JSON(), nullable=True),
        sa.Column("change_summary", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "version_number >= 1",
            name=op.f("ck_prompt_versions_version_number_positive"),
        ),
        sa.CheckConstraint(
            "evaluation_round IS NULL OR (evaluation_round BETWEEN 1 AND 3)",
            name=op.f("ck_prompt_versions_evaluation_round_range"),
        ),
        sa.CheckConstraint(
            "source IN ('engineer_create', 'engineer_revise', 'user_edit')",
            name=op.f("ck_prompt_versions_source_valid"),
        ),
        sa.CheckConstraint(
            "target_mode IN ('generic', 'openai', 'anthropic', 'google')",
            name=op.f("ck_prompt_versions_target_mode_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_runs.id"],
            name=op.f("fk_prompt_versions_agent_run_id_agent_runs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parent_version_id"],
            ["prompt_versions.id"],
            name=op.f("fk_prompt_versions_parent_version_id_prompt_versions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["prompt_request_id"],
            ["prompt_requests.id"],
            name=op.f("fk_prompt_versions_prompt_request_id_prompt_requests"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"],
            ["workflow_runs.id"],
            name=op.f("fk_prompt_versions_workflow_run_id_workflow_runs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prompt_versions")),
        sa.UniqueConstraint(
            "agent_run_id",
            name="uq_prompt_versions_agent_run_id",
        ),
        sa.UniqueConstraint(
            "prompt_request_id",
            "version_number",
            name="uq_prompt_versions_request_version_number",
        ),
    )
    op.create_index(
        "ix_prompt_versions_prompt_request_id",
        "prompt_versions",
        ["prompt_request_id"],
        unique=False,
    )
    op.create_index(
        "ix_prompt_versions_workflow_run_id",
        "prompt_versions",
        ["workflow_run_id"],
        unique=False,
    )
    op.create_index(
        "ix_prompt_versions_parent_version_id",
        "prompt_versions",
        ["parent_version_id"],
        unique=False,
    )

    # Deferred foreign key resolving the WorkflowRun.final_version_id <-> PromptVersion cycle.
    with op.batch_alter_table("workflow_runs") as batch_op:
        batch_op.create_foreign_key(
            "fk_workflow_runs_final_version_id_prompt_versions",
            "prompt_versions",
            ["final_version_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    # Clear any circular row references before dropping prompt_versions while keeping FK checks ON.
    op.execute(
        sa.text(
            "UPDATE workflow_runs SET final_version_id = NULL WHERE final_version_id IS NOT NULL"
        )
    )

    # PostgreSQL requires dropping the dependent catalog FK constraint prior to DROP TABLE.
    # On SQLite, rebuilding workflow_runs via batch_alter_table while child rows exist in
    # prompt_versions or agent_runs would fail DROP TABLE workflow_runs under foreign_keys=ON;
    # clearing final_version_id above already allows SQLite to drop prompt_versions safely.
    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint(
            "fk_workflow_runs_final_version_id_prompt_versions",
            "workflow_runs",
            type_="foreignkey",
        )

    op.drop_index("ix_prompt_versions_parent_version_id", table_name="prompt_versions")
    op.drop_index("ix_prompt_versions_workflow_run_id", table_name="prompt_versions")
    op.drop_index("ix_prompt_versions_prompt_request_id", table_name="prompt_versions")
    op.drop_table("prompt_versions")

    op.drop_index("ix_agent_runs_agent_name", table_name="agent_runs")
    op.drop_index("ix_agent_runs_workflow_run_id", table_name="agent_runs")
    op.drop_table("agent_runs")

    op.drop_index("ix_workflow_runs_status", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_prompt_request_id", table_name="workflow_runs")
    op.drop_table("workflow_runs")

    op.drop_index("ix_prompt_requests_created_at", table_name="prompt_requests")
    op.drop_table("prompt_requests")
