"""POK D4 — the design-calibrated Related-Project metadata payload fixture.

PROVENANCE (read before trusting the numbers downstream):
  The design's economics bases are LIVE measurements (arch §2 row 3 /
  §4.8): metadata dict = 69 keys / 279,810 B; whole project JSON =
  408,809 B; ratio 68.4 %. The live row is UNREACHABLE from this lane
  (zero-live-contact fence; prod NOT probed — discharge round-1 N3
  already disclosed the dev probe returns 0 rows). The dev DB
  (``ensemble_dev``) carries only ``__system_default__`` with three
  tiny keys.

  This fixture therefore RECONSTRUCTS a payload whose two bases are
  calibrated, at runtime, to EXACTLY the design-recorded figures
  (279,810 B / 408,809 B / 68.4 %). It is faithful in the dimensions
  that drive the cap mechanism — key count, family composition, size
  distribution — and synthetic in content. AC12's LIVE re-measure
  remains gated on Phase 1.5; until then this fixture is the offline
  economics stand-in and every number produced from it must be read
  as fixture-based.

Basis method (stated once, used by the harness):
  * basis-1 (metadata dict) = ``len(json.dumps(metadata, ensure_ascii=True))``
    — ASCII ⇒ chars == bytes; default separators reproduce PG
    ``jsonb::text`` shape.
  * basis-2 (whole project JSON) = ``len(json.dumps(project_dict,
    indent=2, ensure_ascii=True))`` — the exact serialization the
    renderer ships into context.
  * All fixture values avoid ``&`` ``<`` ``>`` so
    ``escape_for_context_block`` adds zero inflation (asserted).

Composition mirrors the design's family map:
  * the 11 exact allowlist names of §4.8 (+ ``plugin_*`` prefix keys),
  * the ``staged_release_*_composition`` records as the cap TARGET
    (they are the bulk being cut — commission's decided interpretation;
    NOT in the allowlist),
  * filler keys carrying the remaining recorded bytes.
"""

from __future__ import annotations

import json

# ── design-recorded bases (arch §2 row 3 / §4.8 / discharge round-2) ─────────
POK_DESIGN_METADATA_BASIS_BYTES = 279_810
POK_DESIGN_WHOLE_JSON_BASIS_BYTES = 408_809
POK_DESIGN_RATIO_PERCENT = 68.4  # 279810 / 408809 = 68.42 %


def _policy_object(lines: list[str]) -> dict:
    """A policy-shaped metadata value (dict of short directive strings)."""
    return {"policy": lines}


# ── allowlisted exact names (§4.8 — the 12 families, prefix family below) ────
_ALLOWLISTED_VALUES: dict[str, object] = {
    "ari_plane_tracking_policy": _policy_object(
        [
            "Track the ari plane via nest-report only; never hand-relay.",
            "Ari dispatch context is assembled fresh per dispatch.",
        ]
        + [f"ari-tracking-rule-{i:02d}: keep the lane observable" for i in range(1, 9)]
    ),
    "plane_workspace_discipline": (
        "One plane workspace per lane; the worktree is the unit of isolation. "
        "Never commit on the shared checkout; never merge from the coder role. "
        + "Workspace discipline: fence env, scrub variables, assert the target. " * 2
    ),
    "deferred_key_rotation": _policy_object(
        [
            "Rotate deferred keys on the next promote window.",
            "Rotation is ledgered under upgrade_policy; never ad-hoc.",
        ]
        + [f"deferred-key-{i:03d}: staged for rotation" for i in range(1, 9)]
    ),
    "deferred_key_rotation_f7_addition": (
        "F7 addition: deferred rotation now covers the proxy credential set. "
        "The F7 batch rides the same promote window as the main deferred keys "
        "and is verified by the same post-promote health probe."
    ),
    "upgrade_policy": _policy_object(
        [
            "Upgrades land via promote, never in-place on live.",
            "Every upgrade carries a rollback adjudication note.",
            "Schema changes are additive; FKs are rollback-inert omissions.",
        ]
        + [f"upgrade-rule-{i:02d}: promote-gated" for i in range(1, 11)]
    ),
    "upgrade_workflow_preference": (
        "Preferred workflow: stage → gate pack → Tier-2 dual-side → promote. "
        "The worktree lane cuts its branch from origin/latest; upstream stays "
        "rebased until promote. Prefer mechanical re-derivation over stale counts."
    ),
    "blueprint_active": _policy_object(
        [
            "Active blueprint: ensemble core surface v3.4.",
            "Blueprint file refs are render-time hints, never pointers.",
        ]
        + [f"blueprint-section-{i:02d}: tracked" for i in range(1, 9)]
    ),
    "dependency_self_install_philosophy": (
        "Agents may self-install dev-scoped dependencies inside their worktree "
        "venv only; global installs require leader sign-off. Lazy uv sync is "
        "the default; bare python/npx is banned by house discipline."
    ),
    "self_provisioning_directive": (
        "Self-provisioning: agents spin worktrees, fences, and scratch DBs "
        "without asking; anything touching live (prod DB, live daemon, "
        "shared checkout) requires an explicit commission line."
    ),
    "opendesign_decisions": _policy_object(
        [
            "OpenDesign decisions are recorded as ADR-style entries.",
            "ADR-11 merged the project context message shape.",
            "ADR-7 dropped XML fences; escaping is defense-in-depth.",
        ]
        + [f"opendesign-decision-{i:03d}: recorded in the decision log" for i in range(1, 13)]
    ),
    "staged_release_v0290_ceremony_notes": (
        "v0290 ceremony notes (ALLOWLISTED recovery path — render verbatim). "
        + "Ceremony step: gate → pack → dual-side tier-2 → promote → probe. " * 12
    ),
}

# ── plugin_* PREFIX family (policy keys only, per §4.8) ──────────────────────
_PLUGIN_VALUES: dict[str, object] = {
    "plugin_update_policy": _policy_object(
        [
            "Plugin updates ride the stage/promote ceremony.",
            "Staleness is detected at promote, not at boot.",
        ]
    ),
    "plugin_install_allowlist": [f"plugin-allow-{i:02d}" for i in range(1, 9)],
    "plugin_channel_pin": "canary",
}

# ── the cap TARGET: staged_release_*_composition records (NOT allowlisted) ───
def _composition(release: str, sections: int, per: int) -> dict:
    """A big release-composition record — the bulk the cap exists to cut."""
    return {
        "release": release,
        "sections": [
            {
                "section": f"{release}-s{i:03d}",
                "digest": f"{release} section {i} digest line — composition body",
                "body": f"{release} s{i} " + "composition payload text " * per,
            }
            for i in range(1, sections + 1)
        ],
    }


_COMPOSITION_VALUES: dict[str, object] = {
    "staged_release_v0290_composition": _composition("v0290", 60, 12),
    "staged_release_v0288_composition": _composition("v0288", 48, 12),
    "staged_release_v0287_composition": _composition("v0287", 40, 12),
    "staged_release_v0286_composition": _composition("v0286", 30, 12),
    "staged_release_v0285_composition": _composition("v0285", 24, 12),
}

# ── misc operational metadata (bulk remainder; NOT allowlisted) ──────────────
def _ledger(name: str, rows: int) -> dict:
    return {
        "ledger": name,
        "rows": [
            {"id": f"{name}-{i:04d}", "note": f"{name} row {i} — operational history line"}
            for i in range(1, rows + 1)
        ],
    }


_MISC_VALUES: dict[str, object] = {
    "build_manifest": _ledger("build_manifest", 60),
    "deploy_history": _ledger("deploy_history", 70),
    "prompt_override_ledger": _ledger("prompt_override_ledger", 55),
    "agent_grant_audit": _ledger("agent_grant_audit", 65),
    "gate_ledger": _ledger("gate_ledger", 40),
    "migration_ledger": _ledger("migration_ledger", 35),
    "perf_baseline": _ledger("perf_baseline", 45),
    "incident_log": _ledger("incident_log", 30),
    "doctrine_appendix": _ledger("doctrine_appendix", 50),
    "ops_runbook": _ledger("ops_runbook", 55),
    "checklist_corpus": _ledger("checklist_corpus", 60),
    "workflow_recipes": _ledger("workflow_recipes", 40),
    "ci_matrix": _ledger("ci_matrix", 45),
    "release_calendar": _ledger("release_calendar", 35),
    "snapshot_retention_policy": _ledger("snapshot_retention_policy", 30),
    "watchdog_sweep_history": _ledger("watchdog_sweep_history", 40),
    "pack_registry_history": _ledger("pack_registry_history", 45),
    "skill_evolution_ledger": _ledger("skill_evolution_ledger", 35),
    "kb_curation_ledger": _ledger("kb_curation_ledger", 30),
    "worktree_claim_history": _ledger("worktree_claim_history", 40),
    "probe_capture_history": _ledger("probe_capture_history", 35),
    "baseline_red_evidence": _ledger("baseline_red_evidence", 30),
    "mutation_matrix_history": _ledger("mutation_matrix_history", 30),
    "vocabulary_amendment_history": _ledger("vocabulary_amendment_history", 25),
    "grant_sweep_history": _ledger("grant_sweep_history", 30),
    "ari_dispatch_history": _ledger("ari_dispatch_history", 35),
    "audience_view_history": _ledger("audience_view_history", 30),
    "budget_arithmetic_ledger": _ledger("budget_arithmetic_ledger", 25),
    "fence_token_ledger": _ledger("fence_token_ledger", 25),
    "escape_inflation_ledger": _ledger("escape_inflation_ledger", 25),
    "ladder_policy_notes": _ledger("ladder_policy_notes", 30),
    "cgroup_survivorship_notes": _ledger("cgroup_survivorship_notes", 25),
    "plugin_staleness_history": _ledger("plugin_staleness_history", 25),
    "pipeline_stamp_ledger": _ledger("pipeline_stamp_ledger", 30),
    "journal_splice_history": _ledger("journal_splice_history", 30),
    "supervision_classify_ledger": _ledger("supervision_classify_ledger", 25),
    "terminality_ledger": _ledger("terminality_ledger", 25),
    "ordering_invariant_ledger": _ledger("ordering_invariant_ledger", 30),
    "disputed_row_ledger": _ledger("disputed_row_ledger", 20),
    "archive_forward_ledger": _ledger("archive_forward_ledger", 20),
    "sizing_constant_ledger": _ledger("sizing_constant_ledger", 25),
    "decay_ledger": _ledger("decay_ledger", 25),
    "stage_rank_ledger": _ledger("stage_rank_ledger", 25),
    "pin_ledger": _ledger("pin_ledger", 20),
    "family_cap_ledger": _ledger("family_cap_ledger", 20),
    "active_cap_ledger": _ledger("active_cap_ledger", 20),
        }

# ── tiny keys (realistic operational stubs seen on real rows) ────────────────
_TINY_VALUES: dict[str, object] = {
    "blueprint_scan_last_run": "2026-11-05T03:50:10.139037+00:00",
    "is_system": True,
    "snapshot_create_enabled": "on",
}

# ── the exact-size calibration filler (closes basis-1 at runtime) ────────────
_FILLER_METADATA_KEY = "metadata_basis_calibration_filler"

def _filler_string_value(n: int) -> str:
    return "calibration-filler " * (n // len("calibration-filler ")) + "x" * (
        n % len("calibration-filler ")
    )


def build_metadata() -> dict:
    """The 69-key metadata dict, basis-1 calibrated to exactly 279,810 bytes."""
    ordered: dict[str, object] = {}
    ordered.update(_TINY_VALUES)
    ordered.update(_ALLOWLISTED_VALUES)
    ordered.update(_PLUGIN_VALUES)
    ordered.update(_COMPOSITION_VALUES)
    ordered.update(_MISC_VALUES)
    # Runtime calibration: size the filler so basis-1 lands EXACTLY on the
    # design-recorded 279,810 B (default separators == PG jsonb::text shape).
    # Empirical probe costing — exact by construction.
    base = len(json.dumps(ordered, ensure_ascii=True))
    headroom = POK_DESIGN_METADATA_BASIS_BYTES - base
    if headroom < len(_FILLER_METADATA_KEY) + 8:
        raise ValueError(f"fixture fixed content exceeds the design basis: {base}")
    probe = dict(ordered)
    probe[_FILLER_METADATA_KEY] = ""
    cost_empty = len(json.dumps(probe, ensure_ascii=True)) - base
    probe[_FILLER_METADATA_KEY] = "x" * 1000
    cost_per_x = len(json.dumps(probe, ensure_ascii=True)) - base - cost_empty
    assert cost_per_x == 1000, "filler cost model broken"
    n = headroom - cost_empty
    ordered[_FILLER_METADATA_KEY] = _filler_string_value(n)
    metadata = ordered
    got = len(json.dumps(metadata, ensure_ascii=True))
    assert got == POK_DESIGN_METADATA_BASIS_BYTES, f"basis-1 calibration failed: {got}"
    assert len(metadata) == 69, f"key count drifted: {len(metadata)}"
    for ch in ("&", "<", ">"):
        assert ch not in json.dumps(metadata, ensure_ascii=True), "escape-inflating char in fixture"
    return metadata


# ── the structural (non-metadata) project record ─────────────────────────────
#
# DESIGN-FAITHFUL BASIS MAPPING (stated, and why it changed once):
# The design records the whole-project-JSON basis at 408,809 B against a
# metadata basis of 279,810 B (68.4 % head-share). The remainder
# (~129 KB) is carried here as ``critical_notes`` — the known-big content
# of the live llmproxy row — NOT as structural prose: the renderer
# (pre-existing ADR-11 behavior) drops ``critical_notes`` before dumping,
# so basis-2 = the FULL record dump incl. notes, while the RENDERER-
# VISIBLE payload is metadata + modest structural fields. On that
# visible payload the §4.8 block cap is actually reachable, which is the
# design's arithmetic. The first fixture draft put the remainder into
# ``description`` — structurally faithful to "big row" but unfaithful to
# the cap arithmetic; corrected (see lane report).
#
# RIDER (leader ruling #2, 2026-11-07): the ``/home/nea/agents-ensemble``
# paths below (main_directory + related_directories) are INERT FENCED
# FIXTURE DATA — the string is never contacted, resolved, stat'ed, or
# walked by anything in this module or its harness; it exists only so
# the basis calibration lands EXACTLY on the pinned 408,809 B whole-
# project JSON record (POK_DESIGN_WHOLE_JSON_BASIS_BYTES). The lane
# that produced this fixture ran under the zero-live-contact fence
# (9797 / /home/nea/agents-ensemble / ensemble_prod are LIVE); the
# path literal is calibration payload, NOT a probe target. Change ZERO
# payload bytes when editing — comment/docstring only.

_STRUCTURAL: dict[str, object] = {
    "project_id": "83da04de-0000-4000-8000-000000000001",
    "name": "llmproxy",
    "project_type": "general",
    "status": "active",
    "main_directory": "/home/nea/agents-ensemble",
    "related_directories": [
        "/home/nea/agents-ensemble/daemon",
        "/home/nea/agents-ensemble/tests",
        "/home/nea/agents-ensemble/docs",
    ],
    "job_queue_paused": False,
    "tags": ["live", "proxy", "llm"],
    "shortnames": ["llm"],
    "relationships": {"instances": [], "agents": ["leader", "devops", "ari"]},
    "creator_instance_id": "00000000-0000-4000-8000-000000000000",
    "creator_agent_id": "leader",
    "created_at": "2026-09-01T00:00:00.000000+00:00",
    "updated_at": "2026-11-07T00:00:00.000000+00:00",
}


def _critical_notes_payload(pad_to: int) -> list[dict]:
    """Critical-notes list sized to carry the full-record remainder."""
    head = (
        "Live llmproxy critical-note reconstruction (renderer DROPS this "
        "key pre-dump — ADR-11). Carries the design-recorded whole-row "
        "remainder so basis-2 lands exactly at 408,809 B."
    )
    note_fixed = {
        "id": "note-0001",
        "created_at": "2026-10-01T00:00:00.000000+00:00",
        "updated_at": "2026-10-01T00:00:00.000000+00:00",
        "source_agent": "leader",
        "category": "convention",
        "priority": "high",
        "reference": "docs/runbook.md",
        "pinned": False,
        "pinned_at": None,
        "pinned_by": None,
        "superseded_by_id": None,
        "last_reviewed_at": None,
        "detail_ref": None,
    }
    notes = [dict(note_fixed, summary=head)]
    # Probe-costing: measure the exact serialized cost of one padded note,
    # then size the pad so the FULL record hits the design basis exactly.
    # Basis method is PG-text style (json.dumps, ensure_ascii, default
    # separators) for BOTH bases — consistent with basis-1 and with the
    # design's octet_length(...::text) measurements (indent=2 would
    # double-count nesting indentation).
    probe = dict(_STRUCTURAL)
    probe["metadata"] = build_metadata()
    probe["critical_notes"] = [dict(note_fixed, summary=head)]
    base = len(json.dumps(probe, ensure_ascii=True))
    probe["critical_notes"] = [dict(note_fixed, summary=head + "x" * 1000)]
    cost_per_x = (len(json.dumps(probe, ensure_ascii=True)) - base) / 1000
    assert cost_per_x == 1, "critical-note pad cost model broken"
    n = pad_to - base
    if n < 0:
        raise ValueError(f"fixed fixture content exceeds the design basis: {base}")
    notes[0]["summary"] = head + "x" * n
    return notes


def build_project_dict() -> dict:
    """The FULL project record (basis-2 calibrated to exactly 408,809 B).

    ``critical_notes`` is INCLUDED here (basis-2 = full-record dump, the
    design's head-share denominator). Use :func:`build_project_dict_visible`
    for the renderer-input shape (notes dropped).
    """
    project: dict[str, object] = dict(_STRUCTURAL)
    project["metadata"] = build_metadata()
    project["critical_notes"] = _critical_notes_payload(POK_DESIGN_WHOLE_JSON_BASIS_BYTES)
    got = len(json.dumps(project, ensure_ascii=True))
    assert got == POK_DESIGN_WHOLE_JSON_BASIS_BYTES, f"basis-2 calibration failed: {got}"
    for ch in ("&", "<", ">"):
        assert ch not in json.dumps(project, ensure_ascii=True), "escape-inflating char"
    return project


def build_project_dict_visible() -> dict:
    """The RENDERER-INPUT shape: full record minus ``critical_notes``.

    The pre-existing renderer drops ``critical_notes`` before dumping;
    this is the payload the §4.8 caps actually see. Its whole-JSON size
    is recorded as ``RENDERER_VISIBLE_BASIS_BYTES`` by :func:`recompute_bases`.
    """
    project = build_project_dict()
    project.pop("critical_notes", None)
    return project


def recompute_bases(project: dict) -> dict:
    """AC12 two-basis recompute on a payload (method per the module docstring).

    Both design bases are PG-text style (``json.dumps(..., ensure_ascii=True)``,
    default separators ≈ ``octet_length(...::text)``) so they are mutually
    consistent and anchor-exact. ``renderer_visible_basis_bytes`` is the
    notes-dropped payload in the renderer's own ``indent=2`` form — what
    actually ships pre-cap (informational, not a design anchor).
    """
    meta_basis = len(json.dumps(project.get("metadata", {}), ensure_ascii=True))
    json_basis = len(json.dumps(project, ensure_ascii=True))
    visible = {k: v for k, v in project.items() if k != "critical_notes"}
    visible_basis = len(json.dumps(visible, indent=2, ensure_ascii=True))
    return {
        "metadata_basis_bytes": meta_basis,
        "whole_json_basis_bytes": json_basis,
        "renderer_visible_basis_bytes": visible_basis,
        "ratio_percent": round(meta_basis / json_basis * 100, 2),
        "design_metadata_basis_bytes": POK_DESIGN_METADATA_BASIS_BYTES,
        "design_whole_json_basis_bytes": POK_DESIGN_WHOLE_JSON_BASIS_BYTES,
        "design_ratio_percent": POK_DESIGN_RATIO_PERCENT,
    }
