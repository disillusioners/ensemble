"""Unit pack: ProjectFileSkill field contract + identity spine + A16.

Phase-1 task 1 acceptance: the field set satisfies every verified
``getattr`` the pipeline renders (tokenization ``:720-723``, stage-3
``:901-912``, formatter ``:1019``/``:639``); the exact field contract is
pinned; the A16 metadata-only cache invariant is enforced structurally.
"""

from __future__ import annotations

import dataclasses

import pytest

from daemon.services.project_skill_source.model import (
    ProjectFileSkill,
    SkillIndexMeta,
    is_pf_address,
    mint_address,
    mint_pseudo_id,
    parse_pf_address,
    valid_component,
)

from tests.helpers.project_skill_source_fixtures import TEST_PROJECT_ID


def _skill(**overrides) -> ProjectFileSkill:
    defaults = dict(
        id=mint_pseudo_id("/root", TEST_PROJECT_ID, "tester", "demo"),
        address=mint_address("tester", "demo"),
        name="demo",
        description="demo description",
        content="demo body",
        namespace="tester",
        project_id=TEST_PROJECT_ID,
        body_path="demo.md",
    )
    defaults.update(overrides)
    return ProjectFileSkill(**defaults)


class TestFieldContract:
    """The duck-typed field set — pinned EXACTLY (any addition updates
    this pin in the same commit per the coupling-map police rule)."""

    EXPECTED_FIELDS = {
        # pipeline-consumed (verified getattr sites)
        "id",           # formatter :1019 (skill_id render)
        "name",         # tokenizer :720-723; stage-3 :901-912; formatter :1019
        "description",  # tokenizer :720-723; stage-3 :901-912
        "content",      # tokenizer :720-723; formatter :1019
        "category",
        "status",       # A/B fast path :639
        "ab_test_group",  # A/B fast path :639 — always None for file skills (P5/P7)
        # identity + provenance (D-Q5 + §4.1 contract)
        "address",
        "source",
        "namespace",
        "project_id",
        "root_path",    # S5: the Phase-3 provenance header renders root_path
        "body_path",
        "body_char_len",
        "manifest",
        "digest",
        "shadows",
        # DB-parity fields the renderer/pipeline may read (FR-P5-3 parity)
        "is_active",
        "generation",
    }

    def test_exact_field_set(self):
        actual = {f.name for f in dataclasses.fields(ProjectFileSkill)}
        assert actual == self.EXPECTED_FIELDS

    def test_db_parity_defaults(self):
        skill = _skill()
        assert skill.status == "active"
        assert skill.is_active is True
        assert skill.generation == 0
        assert skill.ab_test_group is None  # A/B branch skipped by construction
        assert skill.source == "project-file"
        assert skill.shadows == []

    def test_pipeline_getattr_surface(self):
        skill = _skill()
        # every verified getattr site renders without error
        assert (getattr(skill, "name", "") or "") == "demo"
        assert (getattr(skill, "description", "") or "") == "demo description"
        assert (getattr(skill, "content", "") or "") == "demo body"
        assert getattr(skill, "ab_test_group", None) is None
        assert getattr(skill, "status", "") == "active"
        assert getattr(skill, "id", None)

    def test_id_is_not_uuid_shaped(self):
        skill = _skill()
        assert skill.id.startswith("pf-")
        assert len(skill.id) == len("pf-") + 16

    def test_version_property_reads_manifest(self):
        skill = _skill(manifest={"version": "1.2.3"})
        assert skill.version == "1.2.3"
        assert _skill().version == ""


class TestIdentitySpine:
    """Single-definition mint functions (A3)."""

    def test_mint_deterministic(self):
        a = mint_pseudo_id("/root", "p", "ns", "name")
        b = mint_pseudo_id("/root", "p", "ns", "name")
        assert a == b and a.startswith("pf-")

    def test_a3_root_path_enters_hash(self):
        """Two roots bound to one project NEVER share a pseudo-id —
        A3's whole point (no shared rows/vectors)."""
        assert mint_pseudo_id("/root-a", "p", "ns", "n") != mint_pseudo_id(
            "/root-b", "p", "ns", "n"
        )

    def test_every_component_enters_hash(self):
        base = ("/root", "p1", "ns", "n")
        assert len({mint_pseudo_id(*base), mint_pseudo_id("/root", "p2", "ns", "n"),
                    mint_pseudo_id("/root", "p1", "ns2", "n"),
                    mint_pseudo_id("/root", "p1", "ns", "n2")}) == 4

    def test_address_round_trip(self):
        assert mint_address("tester", "demo") == "pf:tester:demo"
        assert is_pf_address("pf:tester:demo")
        assert not is_pf_address("pf:tester")
        assert not is_pf_address("pf:tester:demo:extra")
        assert not is_pf_address("pf::demo")
        assert parse_pf_address("pf:tester:demo") == ("tester", "demo")
        assert parse_pf_address("not-an-address") is None

    def test_component_regex(self):
        assert valid_component("tester")
        assert valid_component("a.b-c_d")
        assert valid_component("shared")
        assert not valid_component(".hidden")
        assert not valid_component("..")
        assert not valid_component("a/b")
        assert not valid_component("")
        assert not valid_component("a b")


class TestA16MetadataOnlyCache:
    """A16: the in-process index cache stores METADATA ONLY, never body
    bytes — enforced structurally (no content field) and asserted for
    the pin."""

    def test_skill_index_meta_has_no_content_field(self):
        field_names = {f.name for f in dataclasses.fields(SkillIndexMeta)}
        assert "content" not in field_names
        # belt: no field whose name suggests body payload
        assert not any("body_bytes" in n or "payload" in n for n in field_names)

    def test_meta_to_row_carries_no_body_text(self):
        meta = SkillIndexMeta(
            pseudo_id="pf-" + "0" * 16,
            address="pf:tester:x",
            name="x",
            description="desc",
            category=None,
            namespace="tester",
            project_id="p",
            body_path="x.md",
            body_char_len=5,
            manifest={"version": "1.0.0", "body": "x.md"},
            manifest_sha256="m",
            body_sha256="b",
            digest="d",
            manifest_mtime_ns=1,
            manifest_size=1,
        )
        row = meta.to_row("/root", "2026-10-31T00:00:00+00:00")
        blob = str(row)
        # manifest metadata IS present (permitted derived metadata)
        assert "1.0.0" in blob
        # body-path + digests present; body TEXT is not (it was never stored)
        assert "x.md" in blob
        # manifest_json is metadata-only BY CONSTRUCTION — the cached meta
        # has no field a body could be in, so the serialized row carries
        # exactly the manifest metadata keys
        import json as _json

        assert _json.loads(row["manifest_json"]) == {"version": "1.0.0", "body": "x.md"}
