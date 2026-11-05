"""Deterministic content-only rendering shared by the initial target modes."""

from typing import Final

from app.modules.prompts.canonical import CanonicalPrompt


class GenericPromptRenderer:
    """Join authored content without labels, wrappers, expansion or metadata.

    Preserve each block's exact contents (including indentation and code). Empty
    sections do not exist in the output. Content order is part of the version.
    """

    name: Final[str] = "GenericPromptRenderer"
    version: Final[str] = "1.0.0"

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
