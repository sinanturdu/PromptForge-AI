"""Outcome-first production rubric; separate from benchmark human verdicts."""

from decimal import Decimal
from typing import Final, Literal

CriterionKey = Literal[
    "intent_fidelity",
    "clarity",
    "completeness",
    "structure",
    "context_and_role",
    "constraints_and_scope",
    "output_format",
    "specificity",
    "consistency",
]

RUBRIC_VERSION: Final = "2.0.0"
MIN_TOTAL_SCORE: Final = Decimal("85")
MIN_INTENT_FIDELITY: Final = 7

# Immutable tuples keep weights/definitions shared by instructions and arithmetic.
CRITERIA: Final[tuple[tuple[CriterionKey, int, str], ...]] = (
    ("intent_fidelity", 25, "Preserve the intended task, outcome and creative freedom."),
    ("clarity", 10, "Make the actual deliverable clear without ambiguity or repetition."),
    ("completeness", 15, "Retain necessary supplied requirements and inputs, not extra sections."),
    ("structure", 5, "Use only organization that helps execution; a short paragraph can suffice."),
    (
        "context_and_role",
        5,
        "Use relevant grounded context; roles are optional, never bonus points.",
    ),
    (
        "constraints_and_scope",
        20,
        "Preserve hard constraints; invent no requirements, facts, technologies or scope.",
    ),
    ("output_format", 5, "Respect requested delivery format without forcing any template."),
    ("specificity", 10, "Use task-appropriate detail without padding or over-prescription."),
    ("consistency", 5, "Avoid contradictory instructions and conflicts with the original request."),
)
