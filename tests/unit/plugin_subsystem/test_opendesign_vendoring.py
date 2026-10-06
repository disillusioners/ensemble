"""OQ3 / slice ② byte-fidelity round-trip tests (offline).

Proves the vendored ``plugins/opendesign/copy_freely/`` tree is intact
by re-hashing every file and comparing to the recorded manifest at
``plugins/opendesign/copy_freely/HASHES.sha256`` (the file lives INSIDE
``copy_freely/`` so the obvious ``cd copy_freely && sha256sum -c HASHES.sha256``
works; see CURATION.md §5).  No network; the
recorded manifest is the fixture.

Also asserts the class-entry counts (154 / 115 / 13 / 106) against the
vendored tree to lock the OQ3 record.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "opendesign"
COPY_FREELY = PLUGIN_ROOT / "copy_freely"
HASHES_FILE = PLUGIN_ROOT / "copy_freely" / "HASHES.sha256"

# Class-entry counts per the slice ② dispatch.
EXPECTED_DS_COUNT = 154
EXPECTED_DT_COUNT = 115
EXPECTED_CRAFT_COUNT = 13
EXPECTED_PT_COUNT = 106


def _parse_sha256sum_file(path: Path) -> dict[str, str]:
    """Parse a sha256sum-format file: ``<sha>  <relpath>`` per line."""
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        sha, _, relpath = line.partition("  ")
        out[relpath] = sha
    return out


@pytest.mark.skipif(not COPY_FREELY.is_dir(), reason="vendored tree not present (run od_vendor.py)")
class TestRecordedHashManifest:
    """The recorded hash manifest is the offline round-trip fixture."""

    def test_hashes_file_exists(self):
        assert HASHES_FILE.is_file(), (
            f"hash manifest missing: {HASHES_FILE}. Run tools/vendor/od_vendor.py."
        )

    def test_hashes_file_format_is_sha256sum(self):
        # Every line: <64-hex-chars><two-spaces><relpath>
        sha_re = re.compile(r"^[0-9a-f]{64}  \S+$")
        bad = [
            line
            for line in HASHES_FILE.read_text(encoding="utf-8").splitlines()
            if line.strip() and not sha_re.match(line)
        ]
        assert bad == [], f"malformed lines in {HASHES_FILE.name}: {bad[:3]}"

    def test_hashes_file_lines_sorted_by_relpath(self):
        # The script writes sorted for stability; pin it as a property.
        lines = [
            line
            for line in HASHES_FILE.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        rels = [line.partition("  ")[2] for line in lines]
        assert rels == sorted(rels), "hash manifest lines are not sorted by relpath"


@pytest.mark.skipif(not COPY_FREELY.is_dir(), reason="vendored tree not present (run od_vendor.py)")
class TestVendoredTreeByteFidelity:
    """Re-hash every vendored file and compare to the recorded manifest."""

    def test_vendored_files_match_recorded_hashes(self):
        recorded = _parse_sha256sum_file(HASHES_FILE)
        mismatches = []
        missing = []
        extra = []
        checked = 0

        for f in sorted(COPY_FREELY.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(COPY_FREELY).as_posix()
            # HASHES.sha256 is a locally-owned file (slice ③ resolution
            # for the carry-forward 1 relocation: it lives INSIDE
            # copy_freely/ for the obvious ``cd copy_freely && sha256sum
            # -c HASHES.sha256`` audit; the sync-runner treats it as
            # invisible to the vendor set).  Skip it here too — it is
            # NOT a vendored file and must not appear in the recorded
            # hash manifest.
            if rel == "HASHES.sha256":
                continue
            data = f.read_bytes()
            sha = hashlib.sha256(data).hexdigest()
            checked += 1
            if rel not in recorded:
                extra.append(rel)
                continue
            if recorded[rel] != sha:
                mismatches.append((rel, recorded[rel], sha))

        for rel in recorded:
            full = COPY_FREELY / rel
            if not full.is_file():
                missing.append(rel)

        assert mismatches == [], f"sha256 mismatches: {mismatches[:3]}"
        assert missing == [], f"recorded entries with no file: {missing[:3]}"
        assert extra == [], f"vendored files not in manifest: {extra[:3]}"
        assert checked == len(recorded), (
            f"file count {checked} != manifest entries {len(recorded)}"
        )

    def test_hashes_file_is_not_listed_in_its_own_manifest(self):
        # The HASHES.sha256 file lives inside copy_freely/ (slice ③
        # relocation).  It is a locally-owned file and must NOT appear
        # as a vendored entry in the manifest.  This guards against
        # accidental self-listing (which would make `sha256sum -c`
        # chown-mismatch on a re-run).
        recorded = _parse_sha256sum_file(HASHES_FILE)
        assert "HASHES.sha256" not in recorded, (
            "HASHES.sha256 is locally-owned; it must not appear in its own manifest"
        )


@pytest.mark.skipif(not COPY_FREELY.is_dir(), reason="vendored tree not present (run od_vendor.py)")
class TestClassEntryCounts:
    """Assert the OQ3 expected class-entry counts against the vendored tree."""

    def test_design_systems_top_level_count(self):
        # 154 = 152 brand DIRS + 1 _schema/ + 1 README.md (per CURATION.md
        # F-1 / F-2 — the planning doc's "154 dirs" counts top-level entries).
        entries = sorted(p.name for p in COPY_FREELY.iterdir() if p.name == "design-systems")
        assert entries == ["design-systems"]
        ds_root = COPY_FREELY / "design-systems"
        top = sorted(p.name for p in ds_root.iterdir())
        assert len(top) == EXPECTED_DS_COUNT, (
            f"design-systems/ top-level entries: {len(top)} != {EXPECTED_DS_COUNT}; "
            f"first few: {top[:5]}; last few: {top[-3:]}"
        )
        # Sanity: _schema/ and README.md are present
        assert (ds_root / "_schema").is_dir()
        assert (ds_root / "README.md").is_file()

    def test_design_templates_top_level_count(self):
        entries = sorted(p.name for p in COPY_FREELY.iterdir() if p.name == "design-templates")
        assert entries == ["design-templates"]
        dt_root = COPY_FREELY / "design-templates"
        top = sorted(p.name for p in dt_root.iterdir())
        assert len(top) == EXPECTED_DT_COUNT, (
            f"design-templates/ top-level entries: {len(top)} != {EXPECTED_DT_COUNT}"
        )
        # The non-template file is AGENTS.md (not README.md) at upstream v0.23.0.
        assert (dt_root / "AGENTS.md").is_file()

    def test_craft_top_level_count(self):
        craft_root = COPY_FREELY / "craft"
        assert craft_root.is_dir()
        top = sorted(p.name for p in craft_root.iterdir())
        assert len(top) == EXPECTED_CRAFT_COUNT, (
            f"craft/ top-level: {len(top)} != {EXPECTED_CRAFT_COUNT}; entries: {top}"
        )
        # Every entry is a .md file (planning doc: 11 guidance docs + README + FUTURE_SECTIONS).
        for name in top:
            assert name.endswith(".md"), f"craft/{name} is not .md"

    def test_prompt_templates_json_count(self):
        pt_root = COPY_FREELY / "prompt-templates"
        assert pt_root.is_dir()
        jsons = sorted(pt_root.rglob("*.json"))
        assert len(jsons) == EXPECTED_PT_COUNT, (
            f"prompt-templates/ JSON count: {len(jsons)} != {EXPECTED_PT_COUNT}"
        )
        # 48 image + 58 video
        image = [j for j in jsons if "/image/" in j.as_posix()]
        video = [j for j in jsons if "/video/" in j.as_posix()]
        assert len(image) == 48, f"image JSONs: {len(image)} != 48"
        assert len(video) == 58, f"video JSONs: {len(video)} != 58"

    def test_no_non_json_in_prompt_templates(self):
        # F-3: 1 PNG was excluded by construction; verify the vendored
        # tree has no non-JSON files in prompt-templates/.
        pt_root = COPY_FREELY / "prompt-templates"
        non_json = [p for p in pt_root.rglob("*") if p.is_file() and not p.name.endswith(".json")]
        assert non_json == [], f"non-JSON files in prompt-templates/: {non_json}"


@pytest.mark.skipif(not COPY_FREELY.is_dir(), reason="vendored tree not present (run od_vendor.py)")
class TestPluginLicenseCarry:
    """SPDX license carry per the slice ② dispatch."""

    def test_plugin_root_license_exists(self):
        assert (PLUGIN_ROOT / "LICENSE").is_file(), "LICENSE missing at plugins/opendesign/"

    def test_license_contains_apache_2_0_text(self):
        text = (PLUGIN_ROOT / "LICENSE").read_text(encoding="utf-8")
        assert "Apache License" in text
        assert "Version 2.0" in text

    def test_license_has_attribution_and_change_statement(self):
        # Per od-resource-layer §0: "license-copy + attribution + change-statement"
        text = (PLUGIN_ROOT / "LICENSE").read_text(encoding="utf-8")
        assert "Attribution" in text
        assert "Change-statement" in text

    @pytest.mark.skipif(
        not Path("/home/nea/opt/open-design").is_dir()
        or not (Path("/home/nea/opt/open-design") / ".git").exists(),
        reason="upstream OD checkout absent (set OD_UPSTREAM_DIR or check out "
        "/home/nea/opt/open-design for byte-match verification — slice ③ carry-forward 3)",
    )
    def test_license_body_byte_matches_upstream(self):
        # The Apache body is vendored byte-for-byte from upstream
        # /home/nea/opt/open-design:LICENSE @ open-design-v0.23.0.
        # The vendored file has a 15-line ensemble-side header prepended
        # (attribution + change-statement); everything after the "---"
        # separator should be byte-identical to upstream.
        import subprocess

        upstream_dir = os.environ.get("OD_UPSTREAM_DIR", "/home/nea/opt/open-design")
        upstream = subprocess.run(
            [
                "git",
                "-C",
                upstream_dir,
                "show",
                "open-design-v0.23.0:LICENSE",
            ],
            capture_output=True,
            check=True,
        ).stdout
        body = (PLUGIN_ROOT / "LICENSE").read_bytes()
        marker = b"\n---\n"
        # Find the LAST "---" separator; everything after is the body.
        idx = body.rfind(marker)
        assert idx > 0, "no '---' separator found in vendored LICENSE"
        body_after = body[idx + len(marker):]
        assert body_after == upstream, "vendored LICENSE body diverges from upstream"
