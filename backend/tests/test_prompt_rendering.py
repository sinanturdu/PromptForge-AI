"""Sparse rendering, exact authored text and local schema-compatible validation."""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from app.agents.architect_schemas import ArchitectOutput
from app.llm.providers._openai_schema import validate_schema
from app.modules.prompts.canonical import MAX_CANONICAL_CHARS, CanonicalPrompt
from app.modules.prompts.rendering import GenericPromptRenderer


def content(**updates: Any) -> CanonicalPrompt:
    values = CanonicalPrompt.from_task("Explain the result.").model_dump(mode="json")
    return CanonicalPrompt.model_validate_json(json.dumps({**values, **updates}))


def test_sparse_task_only_is_exact_with_no_wrapper() -> None:
    task = "  Açıkla / explain.\n\n```python\n  x = 1\n```\n"  # noqa: RUF001 -- Turkish text
    renderer = GenericPromptRenderer()
    assert renderer.render(CanonicalPrompt.from_task(task)) == task
    assert renderer.render(CanonicalPrompt.from_task(task)) == task
    assert renderer.name == "GenericPromptRenderer" and renderer.version == "1.0.0"


def test_renderer_preserves_all_authored_content_and_order() -> None:
    prompt = content(
        task="Deliver the requested result.",
        role="Use the supplied editorial role.",
        context="Relevant context only.",
        input_data="```python\ndef f():\n    pass\n```",
        requirements=["Keep Python 3.12."],
        constraints=["Preserve behavior."],
        prohibitions=["Do not access the network."],
        output_format="Return only the patch.",
        examples=["Input: X\nOutput: Y"],
        variables=["Use the provided {name} variable."],
        assumptions=["Use the user's stated assumption: inputs are UTF-8."],
    )
    expected = (
        "Deliver the requested result.\n\nUse the supplied editorial role.\n\n"
        "Relevant context only.\n\n```python\ndef f():\n    pass\n```\n\n"
        "Keep Python 3.12.\n\nPreserve behavior.\n\nDo not access the network.\n\n"
        "Return only the patch.\n\nInput: X\nOutput: Y\n\nUse the provided {name} variable.\n\n"
        "Use the user's stated assumption: inputs are UTF-8."
    )
    before = prompt.model_dump()
    renderer = GenericPromptRenderer()
    assert renderer.render(prompt) == expected
    assert prompt.model_dump() == before
    assert renderer.render(prompt) == expected


@pytest.mark.parametrize("field", ["role", "context", "input_data", "output_format"])
def test_optional_scalar_sections_omit_cleanly(field: str) -> None:
    rendered = GenericPromptRenderer().render(content(**{field: "Only supplied wording."}))
    assert rendered == "Explain the result.\n\nOnly supplied wording."


@pytest.mark.parametrize(
    "field", ["requirements", "constraints", "prohibitions", "examples", "variables", "assumptions"]
)
def test_optional_collections_add_no_headings_or_bullets(field: str) -> None:
    assert GenericPromptRenderer().render(content(**{field: ["First.", "Second."]})) == (
        "Explain the result.\n\nFirst." + ("\n\n" if field == "examples" else "\n") + "Second."
    )


@pytest.mark.parametrize(
    "updates",
    [
        {"task": " "},
        {"role": ""},
        {"context": "\n"},
        {"input_data": " "},
        {"output_format": ""},
        {"requirements": [""]},
        {"constraints": [" "]},
        {"prohibitions": [3]},
        {"task": 3},
        {"unknown": "PRIVATE_SENTINEL"},
        {"task": "x" * (MAX_CANONICAL_CHARS + 1)},
        {"examples": ["x"] * 129},
        {"task": "x" * 25000, "context": "x" * 20000},
    ],
)
def test_local_content_validation(updates: dict[str, Any]) -> None:
    with pytest.raises(ValidationError) as caught:
        content(**updates)
    assert "PRIVATE_SENTINEL" not in str(caught.value)


def test_frozen_canonical_and_immutable_collections() -> None:
    prompt = content(requirements=["Keep this requirement."])
    assert isinstance(prompt.requirements, tuple)
    with pytest.raises(ValidationError):
        prompt.task = "Mutated"
    assert "Keep this requirement." not in repr(prompt)


def test_nullable_fields_still_required_on_wire() -> None:
    with pytest.raises(ValidationError):
        CanonicalPrompt.model_validate_json('{"task":"Valid task"}')
    validate_schema(ArchitectOutput.model_json_schema())
    schema = CanonicalPrompt.model_json_schema()
    assert set(schema["required"]) == set(schema["properties"])
    for name in ("task", "role", "requirements"):
        assert "default" not in schema["properties"][name]
