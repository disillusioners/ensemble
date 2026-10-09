"""opendesign-instance Port declarations (slice ⑤; the FIRST real Ports).

This module is the single source of truth for the opendesign plugin's
Port declarations. The plugin's ``MANIFEST.yaml`` does NOT carry the Port
blocks at slice ⑤; instead, the Port registry reads the in-process
declarations from this module at boot time. The manifest's
``own_outright`` section already documents the four Ports by name
(``od.generate``, ``od.compose_brief``, ``od.save``, ``od.lint``); this
module is the data that backs the documentation.

The four Ports split the per-capability generation flow per REC §1.2
component 9 (the B-element for plugin #1) — never monolithic, per the
recurring "Component 9 becomes an OD monolith" risk in REC §7.

Provider mode (CON §3 + REC §4.2): all four Ports are MODE-P
(Python-native compose; the Mode-P decision is recorded at
``.agents/shared/planning/plugin-subsystem/2026-10-06-slice-5-probes.md``).
The Provider internals are EVOLVABLE behind the frozen Port contract —
the same ``od.generate`` Port could be re-implemented as Mode-T (TS
sidecar / lifted symbol) without breaking any consumer; only the
``provider.adapter_id`` + ``provider.path`` fields would change.

Why declarations, not data-shaped JSON in the manifest? The manifest is
already over the budget for a YAML vocab (the slice ③ manifest is ~250
lines; adding Port schemas in YAML would inflate it past the 64 KB
hard-cap by the time we have 4 ports × 4 large string fields). Port
schemas live in code as Python dicts (the same shape that the JSON
Schema validator would consume at the wire); the manifest carries only
the Port IDs + consumer names (a thin reference). The data shape is
preserved verbatim; the validation gate is unchanged.

**Order of declaration is load-bearing.** The port_registry iterates
the returned sequence in order; the schema-CI report iterates in order.
Keep ``od.generate`` first because it is the long pole (the gate in
the field incident 2026-10-06); keep the others after.

**Adding a Port.** Author the Python dict in this module following the
``_PORT_GENERATE`` shape; ensure ``port_id`` is dotted-kebab, the
inputs/outputs schemas declare every required field (CON §3), the
errors envelope declares the three required keys with ``ok: {"const":
false}``, and the consumer list is named only. The schema-CI runner
validates on every test-pack run.
"""

from __future__ import annotations

from typing import Any, Dict, List

__all__ = ["declared_opendesign_ports"]


# ---------------------------------------------------------------------------
# Port 1: od.generate
# ---------------------------------------------------------------------------
#
# One HTML artifact per call; the long pole. Inputs shape is the slice-⑤
# probe's parameter list (probes.md §A1.5); outputs expose the project's
# completeness-gate surface (finish_reason + usage + truncated) so the
# caller can act on the gate verdict rather than re-implementing heuristic.

_PORT_GENERATE: Dict[str, Any] = {
    "port_id": "od.generate",
    "version": 1,
    "definition": {
        # All inputs JSON-serializable; the schema-CI runner walks each
        # node to refuse non-serializable shapes.
        "inputs_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["prompt", "kind"],
            "properties": {
                "prompt": {"type": "string", "minLength": 1},
                "kind": {
                    "type": "string",
                    "enum": [
                        "prototype",
                        "deck",
                        "template",
                        "other",
                        "image",
                        "video",
                        "audio",
                    ],
                    "default": "prototype",
                },
                "user_instructions": {"type": "string"},
                "project_instructions": {"type": "string"},
                "max_tokens": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200000,
                    "default": 64000,
                },
                "skip_discovery_brief": {"type": "boolean", "default": False},
                "design_system": {"type": "string"},
                "skill_id": {"type": "string"},
                "memory_body": {"type": "string"},
                "audio_voice_options": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        },
        # Outputs expose the project's completeness-gate surface:
        # finish_reason (the live lane's blind spot per od-generation-engine.md
        # §4) + usage (incl. reasoning_tokens if present) + truncated (the
        # gate verdict that maps the finish_reason + html structural check).
        "outputs_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["html", "finish_reason", "usage", "model", "truncated", "error"],
            "properties": {
                "html": {"type": "string"},
                "finish_reason": {
                    "type": "string",
                    "enum": ["stop", "length", "content_filter", "tool_calls", "other"],
                },
                "usage": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "prompt_tokens": {"type": "integer", "minimum": 0},
                        "completion_tokens": {"type": "integer", "minimum": 0},
                        "total_tokens": {"type": "integer", "minimum": 0},
                        "reasoning_tokens": {"type": "integer", "minimum": 0},
                    },
                },
                "model": {"type": "string"},
                "truncated": {"type": "boolean"},
                "error": {"type": ["string", "null"]},
            },
        },
        "errors": {
            "envelope": {
                "ok": {"const": False},
                "code": {
                    "type": "string",
                    "enum": [
                        "byok_not_configured",
                        "prompt_composition_failed",
                        "upstream_http_error",
                        "upstream_bad_request",
                        "context_length_exceeded",
                        "upstream_stream_closed",
                        "truncation_detected",
                        "missing_artifact_marker",
                        "empty_response",
                        "adapter_internal_error",
                    ],
                },
                "message": {"type": "string"},
                "details": {"type": "object"},
            },
        },
        # capability_tags are CATALOG ONLY, not routing (CON §3).
        # Catalog queries (e.g. "which Ports match 'generation'?") land
        # here; actual routing always uses port_id by named consumer.
        "capability_tags": ["generation", "design", "long-running"],
    },
    "provider": {
        "adapter_id": "opendesign.generate.v1",
        "path": "B",
    },
    "consumer": [
        "designer.generate_draft",
        "designer.regenerate_after_lint_fail",
        "job.generate_mockup",
    ],
}


# ---------------------------------------------------------------------------
# Port 2: od.compose_brief
# ---------------------------------------------------------------------------
#
# Pure formatter: assembles [form answers — discovery] + [brand spec] +
# [page brief] sections into a Turn-3 prompt. No network. Ported from
# open-design-mcp@0.16.1 (Apache-2.0, attribution preserved per OQ4
# disposition in the slice-⑤ probe artifact). The own_outright
# orchestration lives at plugins/opendesign/own_outright/turn3_orchestration/
# per MANIFEST.yaml.

_PORT_Compose_Brief: Dict[str, Any] = {
    "port_id": "od.compose_brief",
    "version": 1,
    "definition": {
        "inputs_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["page_prompt"],
            "properties": {
                "page_prompt": {"type": "string", "minLength": 1},
                "brief_answers": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "output": {"type": "string"},
                        "platform": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "audience": {"type": "string"},
                        "tone": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "brand": {
                            "type": "string",
                            "enum": ["pick_direction", "brand_spec", "reference_match"],
                        },
                        "scale": {"type": "string"},
                        "constraints": {"type": "string"},
                    },
                },
                "brand_spec": {"type": "string"},
                "sibling_artifact_slugs": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        },
        "outputs_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["prompt"],
            "properties": {
                "prompt": {"type": "string"},
            },
        },
        "errors": {
            "envelope": {
                "ok": {"const": False},
                "code": {
                    "type": "string",
                    "enum": [
                        "missing_page_prompt",
                        "compose_invalid_input",
                        "adapter_internal_error",
                    ],
                },
                "message": {"type": "string"},
                "details": {"type": "object"},
            },
        },
        "capability_tags": ["brief-composition", "pure-function"],
    },
    "provider": {
        "adapter_id": "opendesign.compose_brief.v1",
        "path": "B",
    },
    "consumer": [
        "designer.compose_brief",
        "designer.compose_brief_per_page",
    ],
}


# ---------------------------------------------------------------------------
# Port 3: od.save
# ---------------------------------------------------------------------------
#
# Captures one HTML artifact to disk at the canonical mockup path
# (CON §3: provider writes to the repo-relative path the consumer
# declares). The repo copy is the developer deliverable; OD-UI
# provenance is a separate reference-only concern that retired at ⑦.

_PORT_SAVE: Dict[str, Any] = {
    "port_id": "od.save",
    "version": 1,
    "definition": {
        "inputs_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["html", "feature_slug", "page_slug"],
            "properties": {
                "html": {"type": "string", "minLength": 1},
                "feature_slug": {"type": "string", "minLength": 1},
                "page_slug": {"type": "string", "minLength": 1},
                "od_url": {"type": "string"},
            },
        },
        "outputs_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["path"],
            "properties": {
                "path": {"type": "string"},
                "bytes_written": {"type": "integer", "minimum": 0},
                "sha256": {"type": "string"},
            },
        },
        "errors": {
            "envelope": {
                "ok": {"const": False},
                "code": {
                    "type": "string",
                    "enum": [
                        "path_invalid",
                        "write_failed",
                        "empty_html",
                        "adapter_internal_error",
                    ],
                },
                "message": {"type": "string"},
                "details": {"type": "object"},
            },
        },
        "capability_tags": ["persistence", "repo-write"],
    },
    "provider": {
        "adapter_id": "opendesign.save.v1",
        "path": "B",
    },
    "consumer": [
        "designer.capture_mockup",
        "designer.write_through",
    ],
}


# ---------------------------------------------------------------------------
# Port 4: od.lint
# ---------------------------------------------------------------------------
#
# The 16-regex family + parse5 EOF gate (CON §3: own_outright ported into
# the generate adapter). Returns pass / fail-N; a fail verdict never
# rides into the developer's brief (per the live designer workflow).

_PORT_LINT: Dict[str, Any] = {
    "port_id": "od.lint",
    "version": 1,
    "definition": {
        "inputs_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["html"],
            "properties": {
                "html": {"type": "string", "minLength": 1},
                "kind": {
                    "type": "string",
                    "enum": [
                        "prototype",
                        "deck",
                        "template",
                        "other",
                        "image",
                        "video",
                        "audio",
                    ],
                },
            },
        },
        "outputs_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["verdict", "fail_count"],
            "properties": {
                "verdict": {
                    "type": "string",
                    "enum": ["pass", "fail-1", "fail-2", "fail-3", "fail-4"],
                },
                "fail_count": {"type": "integer", "minimum": 0},
                "failures": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "rule_id": {"type": "string"},
                            "message": {"type": "string"},
                            "line": {"type": "integer", "minimum": 0},
                        },
                    },
                },
            },
        },
        "errors": {
            "envelope": {
                "ok": {"const": False},
                "code": {
                    "type": "string",
                    "enum": [
                        "parse5_failed",
                        "empty_html",
                        "adapter_internal_error",
                    ],
                },
                "message": {"type": "string"},
                "details": {"type": "object"},
            },
        },
        "capability_tags": ["quality-gate", "lint"],
    },
    "provider": {
        "adapter_id": "opendesign.lint.v1",
        "path": "B",
    },
    "consumer": [
        "designer.lint_artifact",
        "designer.regenerate_after_lint_fail",
    ],
}


def declared_opendesign_ports() -> List[Dict[str, Any]]:
    """Return the slice-⑤ opendesign Port declarations in declaration order.

    The order is load-bearing: the schema-CI report iterates in order; the
    register entries' pinning tests reference Port IDs in this order. Keep
    the long pole first.
    """
    return [
        _PORT_GENERATE,
        _PORT_Compose_Brief,
        _PORT_SAVE,
        _PORT_LINT,
    ]