"""Conservative strict-schema preflight. Reject unsupported rules; never rewrite them."""

from pydantic import JsonValue

from app.llm.errors import LLMError, LLMErrorCode

_KEYWORDS = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "const",
        "anyOf",
        "$defs",
        "$ref",
        "title",
        "description",
    }
)
_TYPES = frozenset({"object", "array", "string", "number", "integer", "boolean", "null"})


def validate_schema(schema: dict[str, JsonValue]) -> None:
    """Support a deliberate subset, including local definitions and nullable unions.

    Provider validation remains authoritative for model-specific limits. References
    are checked without expanding recursive types. No defaults/constraints are dropped.
    """

    def reject() -> None:
        raise LLMError(LLMErrorCode.INVALID_REQUEST)

    properties_count = 0
    enum_count = 0
    string_count = 0

    def visit(node: JsonValue, depth: int) -> None:
        nonlocal properties_count, enum_count, string_count
        if not isinstance(node, dict) or depth > 10 or set(node) - _KEYWORDS:
            reject()
        assert isinstance(node, dict)
        for annotation in ("title", "description"):
            if annotation in node and not isinstance(node[annotation], str):
                reject()
        kind = node.get("type")
        kinds = kind if isinstance(kind, list) else [kind]
        if kind is not None and (
            not kinds or any(not isinstance(k, str) or k not in _TYPES for k in kinds)
        ):
            reject()
        if kind is None and "$ref" not in node and "anyOf" not in node:
            reject()
        if "$ref" in node:
            ref = node["$ref"]
            if not isinstance(ref, str) or (ref != "#" and not ref.startswith("#/$defs/")):
                reject()
            if ref != "#":
                assert isinstance(ref, str)
                definitions = schema.get("$defs")
                name = ref.removeprefix("#/$defs/").replace("~1", "/").replace("~0", "~")
                if not isinstance(definitions, dict) or name not in definitions:
                    reject()
        if "object" in kinds:
            props, required = node.get("properties"), node.get("required")
            if not isinstance(props, dict) or not isinstance(required, list):
                reject()
            assert isinstance(props, dict) and isinstance(required, list)
            if (
                node.get("additionalProperties") is not False
                or any(not isinstance(key, str) for key in required)
                or len(required) != len(props)
                or set(required) != set(props)
            ):
                reject()
            properties_count += len(props)
            string_count += sum(len(key) for key in props)
            for child in props.values():
                visit(child, depth + 1)
        elif set(node) & {"properties", "required", "additionalProperties"}:
            reject()
        if "array" in kinds:
            visit(node.get("items"), depth + 1)
        elif "items" in node:
            reject()
        for key in ("anyOf",):
            if key in node:
                variants = node[key]
                if not isinstance(variants, list) or not variants:
                    reject()
                assert isinstance(variants, list)
                for variant in variants:
                    visit(variant, depth)
        if "$defs" in node:
            definitions = node["$defs"]
            if not isinstance(definitions, dict):
                reject()
            assert isinstance(definitions, dict)
            string_count += sum(len(key) for key in definitions)
            for definition in definitions.values():
                visit(definition, depth)
        if "enum" in node:
            values = node["enum"]
            if (
                not isinstance(values, list)
                or not values
                or any(isinstance(value, (dict, list)) for value in values)
            ):
                reject()
            assert isinstance(values, list)
            enum_count += len(values)
            length = sum(len(value) for value in values if isinstance(value, str))
            if len(values) > 250 and length > 7500:
                reject()
            string_count += length
        if "const" in node:
            constant = node["const"]
            if isinstance(constant, (dict, list)):
                reject()
            if isinstance(constant, str):
                string_count += len(constant)

    if schema.get("type") != "object" or "anyOf" in schema or "$ref" in schema:
        reject()
    visit(schema, 1)
    if properties_count > 5000 or enum_count > 1000 or string_count > 120_000:
        reject()
