"""Add evaluation storage and durable workflow identity, without activating execution.

Revision ID: 0002_workflow_persistence
Revises: 0001_initial_schema
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_workflow_persistence"
down_revision: str | Sequence[str] | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTIVE = "status IN ('PENDING', 'GENERATING', 'EVALUATING', 'REVISING')"
OLD_STATUS = "status IN ('PENDING', 'GENERATING', 'EVALUATING', 'REVISING', 'COMPLETED', 'FAILED')"
NEW_STATUS = (
    "status IN ('PENDING', 'GENERATING', 'EVALUATING', 'REVISING', "
    "'COMPLETED', 'FAILED', 'CLARIFICATION_REQUIRED')"
)
WORKFLOW_CHECKS = {
    "state_revision_non_negative": "state_revision >= 0",
    "request_digest_length": (
        "request_content_digest IS NULL OR length(request_content_digest) = 64"
    ),
    "idempotency_digest_length": (
        "client_idempotency_key_digest IS NULL OR length(client_idempotency_key_digest) = 64"
    ),
    "claim_lease_pair": (
        "(execution_token IS NULL AND lease_expires_at IS NULL) OR "
        "(execution_token IS NOT NULL AND lease_expires_at IS NOT NULL)"
    ),
}
AGENT_CHECKS = {
    "operation_key_length": (
        "operation_key IS NULL OR (length(operation_key) > 0 AND length(operation_key) <= 64)"
    ),
    "revision_key_length": "revision_attempt_key IS NULL OR length(revision_attempt_key) = 64",
    "feedback_digest_length": (
        "feedback_projection_digest IS NULL OR length(feedback_projection_digest) = 64"
    ),
    "result_kind_valid": (
        "result_kind IS NULL OR result_kind IN "
        "('candidate', 'unchanged', 'clarification', 'evaluation', 'failed')"
    ),
}
WORKFLOW_FIELDS = (
    "request_snapshot",
    "request_content_digest",
    "state_revision",
    "execution_token",
    "lease_expires_at",
    "client_idempotency_key_digest",
    "terminal_details",
)
AGENT_FIELDS = (
    "operation_key",
    "revision_attempt_key",
    "baseline_evaluation_id",
    "result_kind",
    "error_code",
    "binding_data",
    "result_data",
    "feedback_projection_digest",
    "call_metadata",
)


def _preflight_active_workflows() -> None:
    duplicate = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM workflow_runs WHERE "
                + ACTIVE
                + " GROUP BY prompt_request_id HAVING count(*) > 1"
            )
        )
        .first()
    )
    if duplicate is not None:
        raise RuntimeError(
            "Migration refused: multiple active workflows require explicit resolution."
        )


def upgrade() -> None:
    _preflight_active_workflows()
    with op.batch_alter_table("workflow_runs") as batch:
        batch.drop_constraint(op.f("ck_workflow_runs_status_valid"), type_="check")
        batch.create_check_constraint(op.f("ck_workflow_runs_status_valid"), NEW_STATUS)
        batch.add_column(sa.Column("request_snapshot", sa.JSON(none_as_null=True), nullable=True))
        batch.add_column(sa.Column("request_content_digest", sa.String(64), nullable=True))
        batch.add_column(
            sa.Column("state_revision", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(sa.Column("execution_token", sa.Uuid(as_uuid=True), nullable=True))
        batch.add_column(sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("client_idempotency_key_digest", sa.String(64), nullable=True))
        batch.add_column(sa.Column("terminal_details", sa.JSON(none_as_null=True), nullable=True))
        batch.create_unique_constraint(
            "uq_workflow_runs_request_idempotency",
            ["prompt_request_id", "client_idempotency_key_digest"],
        )
        for name, expression in WORKFLOW_CHECKS.items():
            batch.create_check_constraint(op.f("ck_workflow_runs_" + name), expression)
    op.create_index(
        "uq_workflow_runs_active_prompt_request",
        "workflow_runs",
        ["prompt_request_id"],
        unique=True,
        sqlite_where=sa.text(ACTIVE),
        postgresql_where=sa.text(ACTIVE),
    )

    with op.batch_alter_table("agent_runs") as batch:
        for name, field_type in (
            ("provider", sa.String(64)),
            ("model", sa.String(128)),
            ("attempt_count", sa.Integer()),
            ("repair_attempted", sa.Boolean()),
        ):
            batch.alter_column(name, existing_type=field_type, nullable=True)
        for name, new_field_type in (
            ("operation_key", sa.String(64)),
            ("revision_attempt_key", sa.String(64)),
            ("baseline_evaluation_id", sa.Uuid(as_uuid=True)),
            ("result_kind", sa.String(32)),
            ("error_code", sa.String(64)),
            ("binding_data", sa.JSON(none_as_null=True)),
            ("result_data", sa.JSON(none_as_null=True)),
            ("feedback_projection_digest", sa.String(64)),
            ("call_metadata", sa.JSON(none_as_null=True)),
        ):
            batch.add_column(sa.Column(name, new_field_type, nullable=True))
        batch.create_unique_constraint("uq_agent_runs_id_workflow", ["id", "workflow_run_id"])
        batch.create_unique_constraint(
            "uq_agent_runs_workflow_operation", ["workflow_run_id", "operation_key"]
        )
        batch.create_unique_constraint(
            "uq_agent_runs_workflow_revision_attempt", ["workflow_run_id", "revision_attempt_key"]
        )
        for name, expression in AGENT_CHECKS.items():
            batch.create_check_constraint(op.f("ck_agent_runs_" + name), expression)
    with op.batch_alter_table("prompt_versions") as batch:
        batch.create_unique_constraint("uq_prompt_versions_id_workflow", ["id", "workflow_run_id"])

    op.create_table(
        "evaluations",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("prompt_version_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("critic_agent_run_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("evaluation_round", sa.Integer(), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("evaluated_target_mode", sa.String(32), nullable=False),
        sa.Column("evaluated_renderer_name", sa.String(64), nullable=False),
        sa.Column("evaluated_renderer_version", sa.String(32), nullable=False),
        sa.Column("rubric_version", sa.String(32), nullable=False),
        sa.Column("scoring_version", sa.String(32), nullable=False),
        sa.Column("assessment_schema_version", sa.String(16), nullable=False),
        sa.Column("assessment", sa.JSON(), nullable=False),
        sa.Column("quality_status", sa.String(32), nullable=False),
        sa.Column("total_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("gate_results", sa.JSON(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluations")),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"],
            ["workflow_runs.id"],
            ondelete="RESTRICT",
            name=op.f("fk_evaluations_workflow_run_id_workflow_runs"),
        ),
        sa.ForeignKeyConstraint(
            ["prompt_version_id", "workflow_run_id"],
            ["prompt_versions.id", "prompt_versions.workflow_run_id"],
            ondelete="RESTRICT",
            name="fk_evaluations_version_workflow_prompt_versions",
        ),
        sa.ForeignKeyConstraint(
            ["critic_agent_run_id", "workflow_run_id"],
            ["agent_runs.id", "agent_runs.workflow_run_id"],
            ondelete="RESTRICT",
            name="fk_evaluations_critic_workflow_agent_runs",
        ),
        sa.UniqueConstraint(
            "workflow_run_id", "evaluation_round", name="uq_evaluations_workflow_round"
        ),
        sa.UniqueConstraint(
            "workflow_run_id", "prompt_version_id", name="uq_evaluations_workflow_version"
        ),
        sa.UniqueConstraint(
            "workflow_run_id", "input_fingerprint", name="uq_evaluations_workflow_fingerprint"
        ),
        sa.UniqueConstraint("critic_agent_run_id", name="uq_evaluations_critic_agent_run"),
        sa.CheckConstraint(
            "evaluation_round IN (1, 2, 3)", name=op.f("ck_evaluations_evaluation_round_range")
        ),
        sa.CheckConstraint(
            "length(input_fingerprint) = 64", name=op.f("ck_evaluations_fingerprint_length")
        ),
        sa.CheckConstraint(
            "evaluated_target_mode IN ('generic', 'openai', 'anthropic', 'google')",
            name=op.f("ck_evaluations_target_mode_valid"),
        ),
        sa.CheckConstraint(
            "total_score IS NULL OR (total_score >= 0 AND total_score <= 100)",
            name=op.f("ck_evaluations_total_score_range"),
        ),
        sa.CheckConstraint(
            "(quality_status = 'unassessable' AND total_score IS NULL AND passed = false) OR "
            "(quality_status = 'qualified' AND total_score IS NOT NULL AND passed = true) OR "
            "(quality_status = 'not_qualified' AND total_score IS NOT NULL AND passed = false)",
            name=op.f("ck_evaluations_quality_state_valid"),
        ),
    )
    op.create_index("ix_evaluations_prompt_version_id", "evaluations", ["prompt_version_id"])
    # This unavoidable baseline/evaluation dependency is added only after both tables exist.
    with op.batch_alter_table("agent_runs") as batch:
        batch.create_foreign_key(
            "fk_agent_runs_baseline_evaluation_id_evaluations",
            "evaluations",
            ["baseline_evaluation_id"],
            ["id"],
            ondelete="RESTRICT",
        )


def _guard_downgrade() -> None:
    connection = op.get_bind()
    workflow_data = " OR ".join(
        name + " IS NOT NULL" for name in WORKFLOW_FIELDS if name != "state_revision"
    )
    agent_data = " OR ".join(name + " IS NOT NULL" for name in AGENT_FIELDS)
    incompatible = (
        connection.execute(sa.text("SELECT 1 FROM evaluations LIMIT 1")).first() is not None
        or connection.execute(
            sa.text(
                "SELECT 1 FROM workflow_runs WHERE "
                + workflow_data
                + " OR state_revision <> 0 OR status = 'CLARIFICATION_REQUIRED' LIMIT 1"
            )
        ).first()
        is not None
        or connection.execute(
            sa.text(
                "SELECT 1 FROM agent_runs WHERE "
                + agent_data
                + " OR provider IS NULL OR model IS NULL OR attempt_count IS NULL "
                "OR repair_attempted IS NULL LIMIT 1"
            )
        ).first()
        is not None
    )
    if incompatible:
        raise RuntimeError(
            "Downgrade refused: persistence data cannot be represented by the legacy schema."
        )


def downgrade() -> None:
    _guard_downgrade()  # Before ANY schema/data mutation.
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_constraint(
            "fk_agent_runs_baseline_evaluation_id_evaluations", type_="foreignkey"
        )
    op.drop_index("ix_evaluations_prompt_version_id", table_name="evaluations")
    op.drop_table("evaluations")
    with op.batch_alter_table("agent_runs") as batch:
        for name in (
            "uq_agent_runs_id_workflow",
            "uq_agent_runs_workflow_operation",
            "uq_agent_runs_workflow_revision_attempt",
        ):
            batch.drop_constraint(name, type_="unique")
        for name in AGENT_CHECKS:
            batch.drop_constraint(op.f("ck_agent_runs_" + name), type_="check")
        for name in AGENT_FIELDS:
            batch.drop_column(name)
        for name, field_type in (
            ("provider", sa.String(64)),
            ("model", sa.String(128)),
            ("attempt_count", sa.Integer()),
            ("repair_attempted", sa.Boolean()),
        ):
            batch.alter_column(name, existing_type=field_type, nullable=False)
    with op.batch_alter_table("prompt_versions") as batch:
        batch.drop_constraint("uq_prompt_versions_id_workflow", type_="unique")
    op.drop_index("uq_workflow_runs_active_prompt_request", table_name="workflow_runs")
    with op.batch_alter_table("workflow_runs") as batch:
        batch.drop_constraint("uq_workflow_runs_request_idempotency", type_="unique")
        batch.drop_constraint(op.f("ck_workflow_runs_status_valid"), type_="check")
        batch.create_check_constraint(op.f("ck_workflow_runs_status_valid"), OLD_STATUS)
        for name in WORKFLOW_CHECKS:
            batch.drop_constraint(op.f("ck_workflow_runs_" + name), type_="check")
        for name in WORKFLOW_FIELDS:
            batch.drop_column(name)
