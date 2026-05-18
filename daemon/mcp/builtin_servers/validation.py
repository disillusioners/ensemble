"""Validation helper for built-in MCP server configuration values."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class McpConfigValidationError(Exception):
    """Raised when config validation fails."""

    def __init__(self, errors: list[dict[str, str]]):
        self.errors = errors
        super().__init__(f"Config validation failed: {errors}")


def validate_config_values(
    schema: list[dict[str, Any]],
    values: dict[str, Any],
) -> None:
    """
    Validate user-provided config values against a schema.

    Checks:
    - Required fields are present (and not None/empty)
    - Type matches (text→str, number→int/float, boolean→bool, select→one of options)
    - Number min/max bounds
    - Select values are in options list

    Raises McpConfigValidationError with field-specific error messages.
    """
    errors: list[dict[str, str]] = []
    schema_map = {field["key"]: field for field in schema}

    # Check required fields (missing + no default = error)
    for field in schema:
        key = field["key"]
        if field.get("required", True):
            if key not in values:
                if field.get("default") is None:
                    errors.append({"field": key, "error": f"Required field '{field.get('label', key)}' is missing"})
                    continue
            elif values[key] is None or values[key] == "":
                errors.append({"field": key, "error": f"Required field '{field.get('label', key)}' is missing"})
                continue

    # Validate present values
    for key, value in values.items():
        if key not in schema_map:
            continue  # Unknown keys are ignored
        if value is None or value == "":
            continue  # Empty values skip validation

        field = schema_map[key]
        field_type = field.get("type", "text")
        label = field.get("label", key)

        # Type checking
        if field_type == "number":
            if not isinstance(value, (int, float)):
                errors.append({"field": key, "error": f"'{label}' must be a number"})
                continue
            if field.get("min") is not None and value < field["min"]:
                errors.append({"field": key, "error": f"'{label}' must be at least {field['min']}"})
            if field.get("max") is not None and value > field["max"]:
                errors.append({"field": key, "error": f"'{label}' must be at most {field['max']}"})
        elif field_type == "boolean":
            if not isinstance(value, bool):
                errors.append({"field": key, "error": f"'{label}' must be a boolean"})
        elif field_type == "select":
            options = field.get("options", [])
            if options and value not in options:
                errors.append({"field": key, "error": f"'{label}' must be one of: {', '.join(options)}"})
        # text type - no additional validation

    if errors:
        raise McpConfigValidationError(errors)
