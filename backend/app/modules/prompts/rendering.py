"""Deterministic, versioned rendering of authored canonical content."""

from typing import Final

from app.models.enums import TargetMode
from app.modules.prompts.canonical import MAX_CANONICAL_CHARS, MAX_CANONICAL_ITEMS, CanonicalPrompt

MAX_RENDERED_CHARS: Final = MAX_CANONICAL_CHARS + 8 * MAX_CANONICAL_ITEMS + 32
_OPENAI_HEADINGS: Final = tuple(
    f"## {label}\n\n"
    for label in (
        "Task",
        "Role",
        "Context",
        "Input",
        "Requirements",
        "Constraints",
        "Prohibitions",
        "Output format",
        "Examples",
        "Variables",
        "Assumptions",
    )
)


class GenericPromptRenderer:
    """Join authored content without labels, wrappers, expansion or metadata.

    Preserve each block's exact contents (including indentation and code). Empty
    sections do not exist in the output. Content order is part of the version.
    """

    name: Final[str] = "GenericPromptRenderer"
    version: Final[str] = "1.0.0"
    max_rendered_chars: Final[int] = MAX_RENDERED_CHARS

    def render(self, prompt: CanonicalPrompt) -> str:
        blocks: list[str | None] = [prompt.task, prompt.role, prompt.context, prompt.input_data]
        for items in (prompt.requirements, prompt.constraints, prompt.prohibitions):
            if items:
                blocks.append("\n".join(items))
        blocks.append(prompt.output_format)
        blocks.extend(prompt.examples)
        if prompt.variables:
            blocks.append("\n".join(prompt.variables))
        if prompt.assumptions:
            blocks.append("\n".join(prompt.assumptions))
        return "\n\n".join(block for block in blocks if block is not None)


class OpenAIPromptRenderer:
    """Add Markdown headings to populated groups while retaining authored text."""

    name: Final[str] = "OpenAIPromptRenderer"
    version: Final[str] = "1.0.0"
    # Group and item separators fit the generic allowance. Add only fixed headings.
    max_rendered_chars: Final[int] = MAX_RENDERED_CHARS + sum(map(len, _OPENAI_HEADINGS))

    def render(self, prompt: CanonicalPrompt) -> str:
        blocks = (
            prompt.task,
            prompt.role,
            prompt.context,
            prompt.input_data,
            "\n".join(prompt.requirements) or None,
            "\n".join(prompt.constraints) or None,
            "\n".join(prompt.prohibitions) or None,
            prompt.output_format,
            "\n\n".join(prompt.examples) or None,
            "\n".join(prompt.variables) or None,
            "\n".join(prompt.assumptions) or None,
        )
        if all(block is None for block in blocks[1:]):
            return prompt.task
        return "\n\n".join(
            heading + block
            for heading, block in zip(_OPENAI_HEADINGS, blocks, strict=True)
            if block is not None
        )


type PromptRenderer = GenericPromptRenderer | OpenAIPromptRenderer


def renderer_for_target(target_mode: TargetMode, *, legacy_generic: bool = False) -> PromptRenderer:
    """Select the initial renderer for a validated target and workflow policy."""
    if target_mode is TargetMode.OPENAI and not legacy_generic:
        return OpenAIPromptRenderer()
    return GenericPromptRenderer()


def resolve_renderer(
    name: object, version: object, *, target_mode: TargetMode | None = None
) -> PromptRenderer:
    """Resolve an explicit renderer identity from a candidate or stored record."""
    if type(name) is not str or type(version) is not str:
        raise ValueError("Unsupported renderer identity.")
    if (name, version) == (GenericPromptRenderer.name, GenericPromptRenderer.version):
        return GenericPromptRenderer()
    if (name, version) == (OpenAIPromptRenderer.name, OpenAIPromptRenderer.version):
        if target_mode is None or target_mode is TargetMode.OPENAI:
            return OpenAIPromptRenderer()
        raise ValueError("Renderer is incompatible with the target mode.")
    raise ValueError("Unsupported renderer identity.")
