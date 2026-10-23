"""A small subset of JSON Schema: enough to check LLM replies, tool arguments and trace events.

Supported keywords: type, enum, properties, required, additionalProperties (false only),
items, minItems, minLength, minimum, maximum.
"""
from __future__ import annotations

_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool,
          "integer": int, "number": (int, float), "null": type(None)}


def _is(value, name: str) -> bool:
    if name in ("integer", "number") and isinstance(value, bool):
        return False
    return isinstance(value, _TYPES[name])


def errors(value, schema: dict, path: str = "$") -> list[str]:
    """Return human-readable problems; an empty list means the value is valid."""
    types = schema.get("type")
    if types is not None:
        types = types if isinstance(types, list) else [types]
        if not any(_is(value, t) for t in types):
            return [f"{path}: expected {' or '.join(types)}, got {type(value).__name__}"]
    out = []
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{path}: must be one of {schema['enum']}")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        out += [f"{path}: missing required key '{k}'" for k in schema.get("required", []) if k not in value]
        for key, item in value.items():
            if key in props:
                out += errors(item, props[key], f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                out.append(f"{path}: unexpected key '{key}'")
    elif isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            out.append(f"{path}: needs at least {schema['minItems']} item(s)")
        for i, item in enumerate(value):
            out += errors(item, schema.get("items", {}), f"{path}[{i}]")
    elif isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            out.append(f"{path}: must not be empty" if schema["minLength"] == 1
                       else f"{path}: needs at least {schema['minLength']} characters")
    elif _is(value, "number"):
        if "minimum" in schema and value < schema["minimum"]:
            out.append(f"{path}: must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            out.append(f"{path}: must be <= {schema['maximum']}")
    return out
