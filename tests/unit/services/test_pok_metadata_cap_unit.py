"""POK metadata-cap unit pack (Phase 1 T8 — the §4.8/A-14 ALLOWLIST + caps).

D1 legs:
  AC6 — a named allowlisted key (``ari_plane_tracking_policy``) renders
        VERBATIM untruncated (FAILS if absent/truncated); all 11 exact
        families + ``plugin_*`` verbatim; non-allowlisted capped 2,000
        chars/key (explicit remainder marker, value stays a JSON string);
        short non-allowlisted values pass through untouched; the block
        cap holds at 16,384 chars measured on the FULL rendered section
        incl. wrapper; the omitted-count tail renders and counts
        exactly; no tail when nothing is omitted.
  AC15 — allowlist = the recovery path (under block-cap pressure the
        allowlisted keys stay verbatim while non-allowlisted keys are
        dropped whole); NO dead pointers (dropped key names are absent
        from the section; the tail is count-only); truncated keys carry
        the marker (partial content + honest marker, never silence).
  AC12 mechanism — the two-basis recompute on the design-calibrated
        fixture lands EXACTLY on the recorded live anchors (279,810 B /
        408,809 B / 68.4 %); render determinism (same payload → same
        section twice).

Regression/robustness legs: no-metadata and small-metadata payloads
render BYTE-IDENTICAL to the pre-cap renderer (vendored copy); the
input dict is never mutated (vars() live-state safety); the
``plugin_`` prefix boundary (``plugins_*`` without the underscore is
NOT allowlisted); ``staged_release_*_composition`` capped vs
``staged_release_v0290_ceremony_notes`` verbatim (the decided
interpretation — pinning makes widening a deliberate one-line change);
``critical_notes`` still dropped pre-dump; non-dict ``metadata``
ignored; the cap constants themselves pinned.

AsyncMock census: N/A — the capped renderer and every helper it calls
are synchronous; no new ``await`` sites were introduced (census = 0).
"""

from __future__ import annotations

import copy
import json

import pytest

from daemon.services.context_messages import (
    POK_METADATA_ALLOWLIST,
    POK_METADATA_KEY_CAP_CHARS,
    POK_METADATA_OMITTED_TAIL_KEY,
    POK_METADATA_SECTION_CAP_CHARS,
    _format_project_json_section,
    _is_pok_allowlisted_metadata_key,
)
from tests.measurements.pok_d4_harness import render_precap
from tests.measurements.pok_d4_payload_fixture import (
    POK_DESIGN_METADATA_BASIS_BYTES,
    POK_DESIGN_WHOLE_JSON_BASIS_BYTES,
    build_project_dict,
    build_project_dict_visible,
    recompute_bases,
)

WRAPPER = "## Related Project\n\n```json\n"
WRAPPER_TAIL = "\n```\n"


def _project(metadata: dict | None = None, **extra: object) -> dict:
    project: dict[str, object] = {
        "project_id": "p1",
        "name": "Test",
        "project_type": "general",
        "status": "active",
        "job_queue_paused": False,
        "tags": [],
        "shortnames": [],
        "relationships": {},
        "created_at": "2026-11-07T00:00:00+00:00",
        "updated_at": "2026-11-07T00:00:00+00:00",
    }
    if metadata is not None:
        project["metadata"] = metadata
    project.update(extra)
    return project


def _section_body(section: str) -> dict:
    """The fenced JSON payload, parsed — proves the dump stays valid JSON."""
    body = section.split("```json\n", 1)[1].rsplit("\n```", 1)[0]
    return json.loads(body)


def _marker(omitted: int) -> str:
    return f"…[truncated +{omitted}c]"


# The §4.8 design-verbatim list — FROZEN, deliberately independent of the
# implementation constant. The test pins the constant AGAINST this literal
# (a membership removal or a drift edit fails BOTH the equality pin and the
# verbatim pins below).
DESIGN_LITERAL_EXACT_NAMES: frozenset[str] = frozenset(
    {
        "ari_plane_tracking_policy",
        "plane_workspace_discipline",
        "deferred_key_rotation",
        "deferred_key_rotation_f7_addition",
        "upgrade_policy",
        "upgrade_workflow_preference",
        "blueprint_active",
        "dependency_self_install_philosophy",
        "self_provisioning_directive",
        "opendesign_decisions",
        "staged_release_v0290_ceremony_notes",
    }
)


# ─── AC6 ──────────────────────────────────────────────────────────────────────


class TestAC6AllowlistVerbatim:
    def test_named_allowlisted_key_renders_verbatim_untruncated(self) -> None:
        """THE AC6 pin: ari_plane_tracking_policy renders whole — fails if
        absent or truncated. 50,000 chars of policy text must survive."""
        big = "ari-plane-tracking-rule " * 2_000  # 50,000 chars
        section = _format_project_json_section(
            _project({"ari_plane_tracking_policy": big}), []
        )
        assert "ari_plane_tracking_policy" in section
        body = _section_body(section)["metadata"]["ari_plane_tracking_policy"]
        assert body == big, "allowlisted value was truncated or altered"

    def test_all_exact_families_render_verbatim(self) -> None:
        """Every exact name of §4.8 renders whole in one payload.

        Values exceed the per-key cap deliberately: if any name were
        removed from the allowlist, its value would come back truncated
        (this pin catches membership removal, not just value alteration)."""
        values = {
            name: f"policy-body-for-{name} " * 100 for name in DESIGN_LITERAL_EXACT_NAMES
        }
        section = _format_project_json_section(_project(dict(values)), [])
        body = _section_body(section)["metadata"]
        for name, value in values.items():
            assert body[name] == value, f"allowlisted family altered: {name}"

    def test_plugin_prefix_family_verbatim(self) -> None:
        """plugin_* prefix keys are allowlisted by PREFIX."""
        value = "plugin-policy " * 500
        section = _format_project_json_section(
            _project({"plugin_update_policy": value}), []
        )
        assert _section_body(section)["metadata"]["plugin_update_policy"] == value


class TestAC6PerKeyCap:
    def test_non_allowlisted_capped_at_2000_with_marker(self) -> None:
        raw = "x" * 5_000
        section = _format_project_json_section(_project({"bulk_notes": raw}), [])
        value = _section_body(section)["metadata"]["bulk_notes"]
        assert isinstance(value, str)
        # The cap is measured on the SERIALIZED form — the truncated value
        # keeps the serialized head (a str value's opening quote included:
        # that quote is structure, honestly part of the 2,000c budget).
        assert value.startswith('"' + "x" * 10)
        expected_marker = _marker(len(json.dumps(raw)) - POK_METADATA_KEY_CAP_CHARS)
        assert value.endswith(expected_marker)
        assert len(value) == POK_METADATA_KEY_CAP_CHARS + len(expected_marker)

    def test_short_non_allowlisted_passes_through(self) -> None:
        section = _format_project_json_section(_project({"small": "tiny"}), [])
        assert _section_body(section)["metadata"]["small"] == "tiny"

    def test_truncated_value_stays_a_json_string(self) -> None:
        section = _format_project_json_section(
            _project({"deep": {"a": ["b" * 100] * 100}}), []
        )
        # must not raise — the truncated value parses as a plain string
        value = _section_body(section)["metadata"]["deep"]
        # a dict value truncates on its serialized form — structural head kept
        assert isinstance(value, str) and value.startswith("{")


class TestAC6BlockCap:
    def test_block_cap_holds_and_tail_counts(self) -> None:
        """20 × 2,100c non-allowlisted keys cannot fit — cap holds, tail counts."""
        metadata = {f"bulk_{i:02d}": "y" * 2_100 for i in range(20)}
        section = _format_project_json_section(_project(metadata), [])
        assert len(section) <= POK_METADATA_SECTION_CAP_CHARS, (
            f"block cap exceeded: {len(section)} > {POK_METADATA_SECTION_CAP_CHARS}"
        )
        body = _section_body(section)
        omitted = body.get(POK_METADATA_OMITTED_TAIL_KEY)
        assert isinstance(omitted, int) and omitted > 0
        assert omitted == 20 - len(body["metadata"])

    def test_no_tail_when_everything_fits(self) -> None:
        section = _format_project_json_section(_project({"small": "tiny"}), [])
        assert POK_METADATA_OMITTED_TAIL_KEY not in _section_body(section)

    def test_wrapper_included_in_cap_measurement(self) -> None:
        """The 16,384 budget covers the FULL render (header + fence + tail).

        Craft a payload that fits ONLY when measured payload-only: shrink
        one non-allowlisted value until the payload-only render would fit
        with one more key, but the wrapped render does not — the wrapped
        measurement must win (that key is dropped).
        """
        # 10 big keys (2,000c each, capped) + structural. Bisect the last
        # key's size so the payload-only render fits at 16,384 but the
        # full section (wrapper = 33c) does not.
        big = {f"bulk_{i:02d}": "z" * 2_100 for i in range(7)}
        probe_payload_only = None
        for n in range(1_400, 2_100, 4):
            metadata = dict(big)
            metadata["edge_key"] = "e" * n
            project = _project(metadata)
            section = _format_project_json_section(project, [])
            body = _section_body(section)
            if POK_METADATA_OMITTED_TAIL_KEY in body:
                # this n already drops the edge key — record and stop
                probe_payload_only = n
                break
        assert probe_payload_only is not None
        # payload-only render WITH the edge key at (probe - 4) must fit under
        # the cap while the wrapped render is within 33c of the payload —
        # i.e. dropping was decided by the wrapped measurement, not the
        # payload-only one. Verify: at n = probe - 8 the key fits AND the
        # section length leaves less than wrapper headroom.
        metadata = dict(big)
        metadata["edge_key"] = "e" * (probe_payload_only - 8)
        section = _format_project_json_section(_project(metadata), [])
        body = _section_body(section)
        assert POK_METADATA_OMITTED_TAIL_KEY not in body  # fits whole
        assert POK_METADATA_SECTION_CAP_CHARS - len(section) < len(WRAPPER) + len(WRAPPER_TAIL)


# ─── AC15 ─────────────────────────────────────────────────────────────────────


class TestAC15RecoveryPath:
    def test_allowlist_survives_block_cap_pressure(self) -> None:
        """Under pressure the allowlisted keys stay verbatim; the bulk drops."""
        ceremony = "ceremony-note " * 2_000  # ~28,000c — bigger than the cap alone
        bulk = {f"bulk_{i:02d}": "b" * 2_100 for i in range(20)}
        section = _format_project_json_section(
            _project({"staged_release_v0290_ceremony_notes": ceremony, **bulk}), []
        )
        body = _section_body(section)
        assert body["metadata"]["staged_release_v0290_ceremony_notes"] == ceremony
        assert body[POK_METADATA_OMITTED_TAIL_KEY] > 0
        # the whole section may exceed the cap here — AC6 binds over the cap
        assert "ceremony-note " in section

    def test_no_dead_pointers_dropped_names_absent(self) -> None:
        """Dropped keys' NAMES never render — the tail is count-only."""
        metadata = {"keeper": "k" * 10}
        metadata.update({f"dropped_key_{i:02d}": "d" * 2_100 for i in range(15)})
        section = _format_project_json_section(_project(metadata), [])
        body = _section_body(section)
        omitted = body[POK_METADATA_OMITTED_TAIL_KEY]
        assert omitted > 0
        for i in range(15):
            name = f"dropped_key_{i:02d}"
            if name not in body["metadata"]:
                assert name not in section, f"dead pointer rendered: {name}"
        assert f'"keeper"' in section

    def test_truncated_keys_carry_marker(self) -> None:
        """A capped key shows partial content + explicit remainder (not silence)."""
        section = _format_project_json_section(_project({"ledger": "L" * 9_000}), [])
        value = _section_body(section)["metadata"]["ledger"]
        assert value.startswith('"L')
        assert "…[truncated +" in value


# ─── AC12 mechanism ───────────────────────────────────────────────────────────


class TestAC12Mechanism:
    def test_two_basis_recompute_lands_on_design_anchors(self) -> None:
        """The fixture recompute hits the recorded live anchors exactly."""
        full = build_project_dict()
        bases = recompute_bases(full)
        assert bases["metadata_basis_bytes"] == POK_DESIGN_METADATA_BASIS_BYTES == 279_810
        assert bases["whole_json_basis_bytes"] == POK_DESIGN_WHOLE_JSON_BASIS_BYTES == 408_809
        assert bases["ratio_percent"] == pytest.approx(68.45, abs=0.01)

    def test_render_determinism(self) -> None:
        project = _project({"bulk": "q" * 3_000, "ari_plane_tracking_policy": "p" * 100})
        first = _format_project_json_section(project, [])
        second = _format_project_json_section(project, [])
        assert first == second


# ─── regression / robustness ──────────────────────────────────────────────────


class TestRegressionPaths:
    def test_no_metadata_byte_identical_to_precap(self) -> None:
        project = _project(None, description="plain project")
        assert _format_project_json_section(project, []) == render_precap(project)

    def test_small_metadata_byte_identical_to_precap(self) -> None:
        project = _project({"tiny_key": "v", "ari_plane_tracking_policy": "policy"})
        assert _format_project_json_section(project, []) == render_precap(project)

    def test_input_dict_never_mutated(self) -> None:
        project = _project({"bulk": "m" * 5_000})
        snapshot = copy.deepcopy(project)
        _format_project_json_section(project, [])
        assert project == snapshot

    def test_plugin_prefix_boundary(self) -> None:
        """plugin_* (with underscore) allowlisted; plugins_* NOT."""
        assert _is_pok_allowlisted_metadata_key("plugin_upgrade_policy")
        assert not _is_pok_allowlisted_metadata_key("plugins_installed")
        big = "pp " * 2_000
        section = _format_project_json_section(
            _project({"plugins_installed": big, "plugin_real": "small"}), []
        )
        value = _section_body(section)["metadata"]["plugins_installed"]
        assert "…[truncated +" in value  # capped — not allowlisted
        assert _section_body(section)["metadata"]["plugin_real"] == "small"

    def test_staged_release_composition_is_the_cap_target(self) -> None:
        """The DECIDED INTERPRETATION pin: composition records are capped;
        only ceremony_notes is allowlisted. Widening = one deliberate line."""
        assert _is_pok_allowlisted_metadata_key("staged_release_v0290_ceremony_notes")
        assert not _is_pok_allowlisted_metadata_key("staged_release_v0290_composition")
        big = "c " * 3_000
        section = _format_project_json_section(
            _project({"staged_release_v0290_composition": big}), []
        )
        assert "…[truncated +" in _section_body(section)["metadata"]["staged_release_v0290_composition"]

    def test_critical_notes_still_dropped(self) -> None:
        section = _format_project_json_section(
            _project({"small": "v"}, critical_notes=[{"summary": "note"}]), []
        )
        assert "critical_notes" not in section
        assert "note" not in section

    def test_non_dict_metadata_ignored(self) -> None:
        project = _project(None)
        project["metadata"] = "not-a-dict"
        assert _format_project_json_section(project, []) == render_precap(project)

    def test_cap_constants_pinned(self) -> None:
        assert POK_METADATA_KEY_CAP_CHARS == 2_000
        assert POK_METADATA_SECTION_CAP_CHARS == 16_384
        # the allowlist constant is EXACTLY the frozen §4.8 literal — no
        # fewer, no more, no renames (membership drift fails here first)
        assert POK_METADATA_ALLOWLIST == DESIGN_LITERAL_EXACT_NAMES

    def test_fixture_based_cap_engagement(self) -> None:
        """The design-calibrated payload: pre-cap 352,991 B → post-cap ≤16,384."""
        project = build_project_dict_visible()
        precap = render_precap(project)
        postcap = _format_project_json_section(project, [])
        assert len(precap) == 352_991
        assert len(postcap) <= POK_METADATA_SECTION_CAP_CHARS
        body = _section_body(postcap)
        assert body[POK_METADATA_OMITTED_TAIL_KEY] == 49
        assert body["metadata"]["ari_plane_tracking_policy"]["policy"][0].startswith(
            "Track the ari plane"
        )
        assert "Ceremony step: gate" in body["metadata"]["staged_release_v0290_ceremony_notes"]
