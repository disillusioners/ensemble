"""Unit pack: manifest validation matrix + A5 containment + A12 glob.

Phase-1 task 2 acceptance: each validation rule red/green; the legacy
fixture ``.agents/project-skills/bump-version`` fails BOTH dual-leg
exclusion legs; the A12 fixture set (depth-exact glob, shared-namespace
positive ingest, ``skills``-as-file, symlinked dirs, skills-template
non-ingestion, warn-once schema gate); the A5 containment reasons.
"""

from __future__ import annotations

import json
import os

import pytest

from daemon.services.project_skill_source.manifest import (
    BODY_MAX_BYTES,
    MANIFEST_MAX_BYTES,
    REASON_BAD_COMPONENT,
    REASON_BAD_SUPERSEDES,
    REASON_BODY_EMPTY,
    REASON_BODY_MISSING,
    REASON_BODY_TOO_LARGE,
    REASON_INVALID_JSON,
    REASON_MANIFEST_TOO_LARGE,
    REASON_MISSING_ASSET,
    REASON_MISSING_FIELD,
    REASON_NAME_MISMATCH,
    REASON_PATH_ESCAPE,
    REASON_SYMLINK_ESCAPE,
    REASON_UNRESOLVED_LOAD,
    REASON_UNKNOWN_SCHEMA,
    discover_skill_dirs,
    parse_and_validate,
    validate_containment,
)

from tests.helpers.project_skill_source_fixtures import (
    TEST_PROJECT_ID,
    VALID_MANIFEST,
    write_legacy_bump_version,
    write_skill,
)


class TestValidationMatrix:
    """Each rule RED (with its frozen reason) / GREEN."""

    def test_green_path(self, tmp_path):
        write_skill(tmp_path, "tester", "demo-skill")
        parsed = parse_and_validate(str(tmp_path), "tester", "demo-skill", TEST_PROJECT_ID)
        assert parsed.ok, parsed.detail
        assert parsed.meta.name == "demo-skill"
        assert parsed.meta.namespace == "tester"
        assert parsed.meta.address == "pf:tester:demo-skill"
        assert parsed.meta.body_path == "demo-skill.md"
        assert parsed.meta.body_char_len == len("Demo body content.\n")
        assert parsed.meta.digest and parsed.meta.manifest_sha256 and parsed.meta.body_sha256

    def test_bad_component_namespace(self, tmp_path):
        parsed = parse_and_validate(str(tmp_path), "..", "x", TEST_PROJECT_ID)
        assert not parsed.ok and parsed.reason == REASON_BAD_COMPONENT

    def test_bad_component_name(self, tmp_path):
        parsed = parse_and_validate(str(tmp_path), "tester", "a/b", TEST_PROJECT_ID)
        assert not parsed.ok and parsed.reason == REASON_BAD_COMPONENT

    def test_invalid_json(self, tmp_path):
        write_skill(tmp_path, "tester", "x", manifest_text="{not json")
        parsed = parse_and_validate(str(tmp_path), "tester", "x", TEST_PROJECT_ID)
        assert not parsed.ok and parsed.reason == REASON_INVALID_JSON

    def test_manifest_not_object(self, tmp_path):
        write_skill(tmp_path, "tester", "x", manifest_text='["array"]')
        parsed = parse_and_validate(str(tmp_path), "tester", "x", TEST_PROJECT_ID)
        assert not parsed.ok and parsed.reason == REASON_INVALID_JSON

    @pytest.mark.parametrize("version", [None, 2, "1", True])
    def test_unknown_schema_versions(self, tmp_path, version):
        manifest = {**VALID_MANIFEST, "name": "x"}
        if version is None:
            manifest.pop("schema_version")
        else:
            manifest["schema_version"] = version
        write_skill(tmp_path, "tester", "x", manifest=manifest)
        parsed = parse_and_validate(str(tmp_path), "tester", "x", TEST_PROJECT_ID)
        assert not parsed.ok and parsed.reason == REASON_UNKNOWN_SCHEMA

    @pytest.mark.parametrize("missing", ["name", "description", "body"])
    def test_missing_field(self, tmp_path, missing):
        manifest = {k: v for k, v in VALID_MANIFEST.items() if k != missing}
        # manifest_text bypasses the helper's identity fill — the missing
        # key must STAY missing
        payload = {**manifest, "name": "x", "body": "x.md"}
        payload.pop(missing, None)
        write_skill(tmp_path, "tester", "x", manifest_text=json.dumps(payload))
        parsed = parse_and_validate(str(tmp_path), "tester", "x", TEST_PROJECT_ID)
        assert not parsed.ok and parsed.reason == REASON_MISSING_FIELD
        assert missing in parsed.detail
        parsed = parse_and_validate(str(tmp_path), "tester", "x", TEST_PROJECT_ID)
        assert not parsed.ok and parsed.reason == REASON_MISSING_FIELD
        assert missing in parsed.detail

    def test_empty_string_field_counts_as_missing(self, tmp_path):
        manifest = {**VALID_MANIFEST, "name": "x", "description": "   "}
        write_skill(tmp_path, "tester", "x", manifest=manifest)
        parsed = parse_and_validate(str(tmp_path), "tester", "x", TEST_PROJECT_ID)
        assert not parsed.ok and parsed.reason == REASON_MISSING_FIELD

    def test_name_mismatch(self, tmp_path):
        write_skill(tmp_path, "tester", "x", manifest={**VALID_MANIFEST, "name": "y"})
        parsed = parse_and_validate(str(tmp_path), "tester", "x", TEST_PROJECT_ID)
        assert not parsed.ok and parsed.reason == REASON_NAME_MISMATCH

    def test_manifest_too_large(self, tmp_path):
        write_skill(
            tmp_path,
            "tester",
            "x",
            manifest={**VALID_MANIFEST, "description": "x" * (MANIFEST_MAX_BYTES + 10)},
        )
        parsed = parse_and_validate(str(tmp_path), "tester", "x", TEST_PROJECT_ID)
        assert not parsed.ok and parsed.reason == REASON_MANIFEST_TOO_LARGE

    def test_body_missing(self, tmp_path):
        write_skill(tmp_path, "tester", "x", with_body=False)
        parsed = parse_and_validate(str(tmp_path), "tester", "x", TEST_PROJECT_ID)
        assert not parsed.ok and parsed.reason == REASON_BODY_MISSING

    def test_body_empty(self, tmp_path):
        write_skill(tmp_path, "tester", "x", body_text="")
        parsed = parse_and_validate(str(tmp_path), "tester", "x", TEST_PROJECT_ID)
        assert not parsed.ok and parsed.reason == REASON_BODY_EMPTY

    def test_body_too_large(self, tmp_path):
        write_skill(tmp_path, "tester", "x", body_text="a" * (BODY_MAX_BYTES + 1))
        parsed = parse_and_validate(str(tmp_path), "tester", "x", TEST_PROJECT_ID)
        assert not parsed.ok and parsed.reason == REASON_BODY_TOO_LARGE

    def test_unresolved_load(self, tmp_path):
        write_skill(
            tmp_path,
            "tester",
            "x",
            manifest={**VALID_MANIFEST, "name": "x", "loads": ["nope-skill"]},
        )
        parsed = parse_and_validate(str(tmp_path), "tester", "x", TEST_PROJECT_ID)
        assert not parsed.ok and parsed.reason == REASON_UNRESOLVED_LOAD

    def test_resolved_load_green(self, tmp_path):
        write_skill(tmp_path, "tester", "dep")
        write_skill(
            tmp_path, "tester", "x", manifest={**VALID_MANIFEST, "name": "x", "loads": ["dep"]}
        )
        parsed = parse_and_validate(str(tmp_path), "tester", "x", TEST_PROJECT_ID)
        assert parsed.ok, parsed.detail

    def test_load_outside_namespace_not_resolved(self, tmp_path):
        write_skill(tmp_path, "other", "dep")
        write_skill(
            tmp_path, "tester", "x", manifest={**VALID_MANIFEST, "name": "x", "loads": ["dep"]}
        )
        parsed = parse_and_validate(str(tmp_path), "tester", "x", TEST_PROJECT_ID)
        # loads resolve IN-NAMESPACE: a dep living in another namespace is unresolved
        assert not parsed.ok and parsed.reason == REASON_UNRESOLVED_LOAD

    def test_missing_asset(self, tmp_path):
        write_skill(
            tmp_path,
            "tester",
            "x",
            manifest={**VALID_MANIFEST, "name": "x", "assets": ["nope.txt"]},
        )
        parsed = parse_and_validate(str(tmp_path), "tester", "x", TEST_PROJECT_ID)
        assert not parsed.ok and parsed.reason == REASON_MISSING_ASSET

    def test_existing_asset_green(self, tmp_path):
        skill_dir = write_skill(tmp_path, "tester", "x")
        with open(os.path.join(skill_dir, "data.txt"), "w") as fh:
            fh.write("asset")
        parsed = parse_and_validate(
            str(tmp_path),
            "tester",
            "x",
            TEST_PROJECT_ID,
        )
        assert parsed.ok  # assets: [] in the default manifest — rewrite with asset
        write_skill(
            tmp_path,
            "tester",
            "y",
            manifest={**VALID_MANIFEST, "name": "y", "assets": ["data.txt"]},
        )
        with open(os.path.join(tmp_path, ".agents", "tester", "skills", "y", "data.txt"), "w") as fh:
            fh.write("asset")
        parsed = parse_and_validate(str(tmp_path), "tester", "y", TEST_PROJECT_ID)
        assert parsed.ok, parsed.detail

    @pytest.mark.parametrize(
        "supersedes",
        ["not-an-object", {"skill_id": "x"}, {"store": "dynamic-skill"}, 42],
    )
    def test_bad_supersedes(self, tmp_path, supersedes):
        write_skill(
            tmp_path,
            "tester",
            "x",
            manifest={**VALID_MANIFEST, "name": "x", "supersedes": supersedes},
        )
        parsed = parse_and_validate(str(tmp_path), "tester", "x", TEST_PROJECT_ID)
        assert not parsed.ok and parsed.reason == REASON_BAD_SUPERSEDES

    def test_good_supersedes_green(self, tmp_path):
        write_skill(
            tmp_path,
            "tester",
            "x",
            manifest={
                **VALID_MANIFEST,
                "name": "x",
                "supersedes": {"store": "dynamic-skill", "skill_id": "abc"},
            },
        )
        parsed = parse_and_validate(str(tmp_path), "tester", "x", TEST_PROJECT_ID)
        assert parsed.ok

    def test_unknown_extra_fields_tolerated(self, tmp_path):
        write_skill(
            tmp_path,
            "tester",
            "x",
            manifest={**VALID_MANIFEST, "name": "x", "future_field": {"a": 1}},
        )
        parsed = parse_and_validate(str(tmp_path), "tester", "x", TEST_PROJECT_ID)
        assert parsed.ok
        assert any("future_field" in note for note in parsed.debug_notes)


class TestA5Containment:
    """Path containment: components, pre-realpath rejection, skill-dir
    confinement, symlink refusal."""

    def test_absolute_body_rejected_pre_realpath(self, tmp_path):
        write_skill(
            tmp_path,
            "tester",
            "x",
            manifest={**VALID_MANIFEST, "name": "x", "body": "/etc/passwd"},
        )
        parsed = parse_and_validate(str(tmp_path), "tester", "x", TEST_PROJECT_ID)
        assert not parsed.ok and parsed.reason == REASON_PATH_ESCAPE

    def test_dotdot_body_rejected_pre_realpath(self, tmp_path):
        write_skill(
            tmp_path,
            "tester",
            "x",
            manifest={**VALID_MANIFEST, "name": "x", "body": "../../secrets.md"},
        )
        parsed = parse_and_validate(str(tmp_path), "tester", "x", TEST_PROJECT_ID)
        assert not parsed.ok and parsed.reason == REASON_PATH_ESCAPE

    def test_dotdot_asset_rejected(self, tmp_path):
        write_skill(
            tmp_path,
            "tester",
            "x",
            manifest={**VALID_MANIFEST, "name": "x", "assets": ["../../x"]},
        )
        parsed = parse_and_validate(str(tmp_path), "tester", "x", TEST_PROJECT_ID)
        assert not parsed.ok and parsed.reason == REASON_PATH_ESCAPE

    def test_symlinked_skill_dir_outside_root_refused(self, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        write_skill(str(outside), "tester", "evil")
        link = tmp_path / ".agents" / "tester" / "skills" / "evil"
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(outside / ".agents" / "tester" / "skills" / "evil")
        parsed = parse_and_validate(str(tmp_path), "tester", "evil", TEST_PROJECT_ID)
        assert not parsed.ok and parsed.reason == REASON_SYMLINK_ESCAPE

    def test_symlinked_body_outside_skill_dir_refused(self, tmp_path):
        outside_file = tmp_path / "outside-body.md"
        outside_file.write_text("evil body")
        write_skill(tmp_path, "tester", "x", body_name="linked.md", body_text="place")
        link = tmp_path / ".agents" / "tester" / "skills" / "x" / "linked.md"
        link.unlink()
        link.symlink_to(outside_file)
        parsed = parse_and_validate(str(tmp_path), "tester", "x", TEST_PROJECT_ID)
        assert not parsed.ok and parsed.reason == REASON_SYMLINK_ESCAPE

    def test_symlinked_ns_dir_outside_root_refused(self, tmp_path):
        outside = tmp_path / "outside-agents"
        (outside / "skills" / "x").mkdir(parents=True)
        with open(outside / "skills" / "x" / "skill.json", "w") as fh:
            json.dump({**VALID_MANIFEST, "name": "x"}, fh)
        with open(outside / "skills" / "x" / "x.md", "w") as fh:
            fh.write("body")
        ns_link = tmp_path / ".agents" / "evil-ns"
        ns_link.parent.mkdir(parents=True, exist_ok=True)
        ns_link.symlink_to(outside, target_is_directory=True)
        parsed = parse_and_validate(str(tmp_path), "evil-ns", "x", TEST_PROJECT_ID)
        assert not parsed.ok and parsed.reason == REASON_SYMLINK_ESCAPE

    def test_containment_recheck_helper(self, tmp_path):
        write_skill(tmp_path, "tester", "x")
        assert validate_containment(str(tmp_path), "tester", "x") is None
        assert (
            validate_containment(str(tmp_path), "..", "x") or ""
        ).startswith(REASON_BAD_COMPONENT)
        assert (
            validate_containment(str(tmp_path), "tester", "../tester") or ""
        ).startswith(REASON_BAD_COMPONENT)


class TestA12ScannerGlob:
    def test_depth_exact_discovers_canon(self, tmp_path):
        write_skill(tmp_path, "tester", "x")
        write_skill(tmp_path, "shared", "y")
        candidates, notes = discover_skill_dirs(str(tmp_path))
        assert ("tester", "x") in candidates and ("shared", "y") in candidates
        assert notes == []

    def test_nested_name_sub_rejected(self, tmp_path):
        write_skill(tmp_path, "tester", "x")
        nested = tmp_path / ".agents" / "tester" / "skills" / "x" / "sub"
        nested.mkdir()
        with open(nested / "skill.json", "w") as fh:
            json.dump({**VALID_MANIFEST, "name": "sub"}, fh)
        candidates, _ = discover_skill_dirs(str(tmp_path))
        # depth-exact: only the top-level skill dir is a candidate; the
        # nested sub/skill.json is never matched
        assert candidates == [("tester", "x")]

    def test_shared_namespace_is_positive_ingest(self, tmp_path):
        """A12/P6: the 'namespace must be an agent dir' leg is DELETED —
        .agents/shared/skills/<name>/ is a legitimate positive fixture."""
        write_skill(tmp_path, "shared", "org-skill")
        candidates, _ = discover_skill_dirs(str(tmp_path))
        assert candidates == [("shared", "org-skill")]

    def test_skills_as_file_no_crash(self, tmp_path):
        (tmp_path / ".agents" / "weird" / "skills").mkdir(parents=True)
        skills_file = tmp_path / ".agents" / "weirdns" / "skills"
        skills_file.parent.mkdir(parents=True)
        skills_file.write_text("i am a file")
        candidates, _ = discover_skill_dirs(str(tmp_path))
        assert candidates == []

    def test_symlinked_skill_dir_candidate_refused_downstream(self, tmp_path):
        """The glob may list a symlinked dir whose target is INSIDE the
        root; the validator's containment gate refuses escapes — the
        glob itself stays structural."""
        write_skill(tmp_path, "tester", "real")
        link = tmp_path / ".agents" / "tester" / "skills" / "alias"
        link.symlink_to(tmp_path / ".agents" / "tester" / "skills" / "real")
        candidates, _ = discover_skill_dirs(str(tmp_path))
        assert ("tester", "alias") in candidates  # structural discovery
        parsed = parse_and_validate(str(tmp_path), "tester", "alias", TEST_PROJECT_ID)
        # realpath(alias) == realpath(real) != canonical alias path → escape refusal
        assert not parsed.ok and parsed.reason == REASON_SYMLINK_ESCAPE

    def test_agent_skills_template_not_ingested(self, tmp_path):
        """The innate tier lives under ``agents/`` (singular, repo root) —
        structurally outside ``<root>/.agents/``."""
        tpl = tmp_path / "agents" / "tester" / "skills-template" / "some-skill"
        tpl.mkdir(parents=True)
        with open(tpl / "skill.json", "w") as fh:
            json.dump({**VALID_MANIFEST, "name": "some-skill"}, fh)
        candidates, _ = discover_skill_dirs(str(tmp_path))
        assert candidates == []

    def test_no_agents_tree(self, tmp_path):
        candidates, notes = discover_skill_dirs(str(tmp_path / "nonexistent"))
        assert candidates == [] and notes

    def test_caps(self, tmp_path):
        for i in range(130):
            write_skill(tmp_path, "tester", f"s{i:03d}")
        candidates, notes = discover_skill_dirs(str(tmp_path))
        assert len(candidates) == 128  # sorted truncation at the cap
        assert any("cap hit" in note for note in notes)


class TestLegacyExclusion:
    """Dual-leg legacy exclusion — ``bump-version`` fails BOTH legs."""

    def test_leg1_structural_glob_never_matches(self, tmp_path):
        write_legacy_bump_version(tmp_path)
        candidates, _ = discover_skill_dirs(str(tmp_path))
        assert candidates == []  # no skills/ segment → the glob never sees it

    def test_leg2_schema_gate_rejects_even_when_fed_directly(self, tmp_path):
        """Leg 2 in isolation: the LEGACY SCHEMA at a canonical canon path
        is refused by the schema gate (no schema_version → unknown)."""
        legacy_manifest = {
            "id": "legacy-bump-version",
            "triggers": ["bump", "version"],
            "default_bump": "patch",
            "supported_project_types": ["software"],
            "created_at": "2026-01-01T00:00:00+00:00",
        }
        write_skill(
            tmp_path,
            "project-skills",
            "bump-version",
            body_text="Legacy bump-version instructions.\n",
            manifest=legacy_manifest,
        )
        parsed = parse_and_validate(
            str(tmp_path), "project-skills", "bump-version", TEST_PROJECT_ID
        )
        assert not parsed.ok and parsed.reason == REASON_UNKNOWN_SCHEMA

    def test_leg1_canonical_path_shape_refuses_legacy_layout(self, tmp_path):
        """Leg 1 through the validator itself: the legacy layout (no
        ``skills/`` segment) has NO canonical skill.json — the validator
        reports the path as unreadable, and the glob never matches it."""
        write_legacy_bump_version(tmp_path)
        parsed = parse_and_validate(
            str(tmp_path), "project-skills", "bump-version", TEST_PROJECT_ID
        )
        assert not parsed.ok  # canonical path absent — the legacy layout is invisible

    def test_anti_conflation_scan(self, tmp_path):
        """FR-VAL-1: canon + legacy tree together → only canon indexed,
        bump-version absent, no crash."""
        write_skill(tmp_path, "tester", "real-skill")
        write_legacy_bump_version(tmp_path)
        candidates, _ = discover_skill_dirs(str(tmp_path))
        assert candidates == [("tester", "real-skill")]
        parsed = parse_and_validate(str(tmp_path), "tester", "real-skill", TEST_PROJECT_ID)
        assert parsed.ok
