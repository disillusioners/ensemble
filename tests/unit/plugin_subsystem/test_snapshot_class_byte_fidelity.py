"""OQ3 / slice ③ pinning teeth for the snapshot_with_drift_alarm divergence register.

The four seeded ``divergence_register`` entries in
``plugins/opendesign/MANIFEST.yaml`` cite
``tests/unit/plugin_subsystem/test_snapshot_class_byte_fidelity.py::TestSnapshotClassByteFidelity``
as their pinning_test pointer.  This module supplies the real tests
under that class — they were declared in the manifest before the
class existed, leaving the register's pinning teeth dangling
pointers.  The dispatch's HONESTY REQUIREMENT clause for S3-7e
records the constraint: the ③ review found the vendoring
byte-faithful to upstream ``open-design-v0.23.0`` (zero on-disk
divergence), so the literal "must NOT be byte-identical to upstream"
assertion shape CANNOT pass today.  The honest interpretation per
``od-resource-layer.md`` §1.B is that each entry documents an
UPSTREAM-INTERNAL asymmetry (the daemon-tree blob differs from the
contracts-tree blob in upstream, or the contracts tree lacks a file
the daemon tree has).  The per-entry tests therefore assert the
upstream two-tree shape the entry actually claims, NOT a
vendored-vs-upstream byte mismatch.

OFFLINE teeth (always run):
    - re-hash every file listed in
      ``plugins/opendesign/snapshot_with_drift_alarm/HASHES.sha256``
      against the vendored bytes (consumes the otherwise-dead
      snapshot-class hash manifest).

UPSTREAM teeth (skip-guard like the existing
``test_opendesign_vendoring::test_license_body_byte_matches_upstream``):
    - per-entry vendored-equals-upstream-daemon + upstream two-tree
      asymmetry assertions.  These depend on the
      ``/home/nea/opt/open-design`` checkout (overridable via
      ``OD_UPSTREAM_DIR``); CI without the checkout still gets the
      offline hash-manifest teeth.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "opendesign"
SNAPSHOT_ROOT = PLUGIN_ROOT / "snapshot_with_drift_alarm"
HASHES_FILE = SNAPSHOT_ROOT / "HASHES.sha256"

UPSTREAM_DIR = Path(os.environ.get("OD_UPSTREAM_DIR", "/home/nea/opt/open-design"))
UPSTREAM_TAG = "open-design-v0.23.0"

# Daemon / contracts upstream roots — the two-tree pair the
# divergence register documents drift between.
UPSTREAM_DAEMON = f"apps/daemon/src/prompts"
UPSTREAM_CONTRACTS = f"packages/contracts/src/prompts"


def _parse_sha256sum_file(path: Path) -> dict[str, str]:
    """Parse a sha256sum-format file: ``<sha>  <relpath>`` per line."""
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        sha, _, relpath = line.partition("  ")
        out[relpath] = sha
    return out


def _upstream_blob(upstream_dir: Path, relpath: str) -> bytes:
    """Read an upstream tag blob read-only (no checkout mutation)."""
    return subprocess.run(
        ["git", "-C", str(upstream_dir), "cat-file", "blob", f"{UPSTREAM_TAG}:{relpath}"],
        capture_output=True,
        check=True,
    ).stdout


def _upstream_has_path(upstream_dir: Path, relpath: str) -> bool:
    """True iff the upstream tag tree contains ``relpath`` (any blob)."""
    res = subprocess.run(
        [
            "git",
            "-C",
            str(upstream_dir),
            "ls-tree",
            UPSTREAM_TAG,
            relpath,
        ],
        capture_output=True,
        check=True,
    )
    return bool(res.stdout.strip())


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ─── Skip guard for upstream-dependent tests ───────────────────────────────────


_real_upstream_available = (
    UPSTREAM_DIR.is_dir() and (UPSTREAM_DIR / ".git").exists()
)


requires_real_upstream = pytest.mark.skipif(
    not _real_upstream_available,
    reason=(
        "real OD upstream checkout absent; set OD_UPSTREAM_DIR or check out "
        "/home/nea/opt/open-design for per-register-entry drift assertions "
        "(offline hash-manifest teeth still run; see class TestSnapshotClassByteFidelity)"
    ),
)


# ─── Offline teeth — HASHES.sha256 consumption ────────────────────────────────


@pytest.mark.skipif(
    not SNAPSHOT_ROOT.is_dir(),
    reason="snapshot_with_drift_alarm tree not present (run od_vendor_snapshot.py)",
)
class TestSnapshotClassByteFidelity:
    """Pinning teeth for the ``snapshot_with_drift_alarm`` divergence register.

    The MANIFEST.yaml register entries (id 1-4) cite this class as
    their pinning_test pointer.  Offline teeth (hash-manifest
    consumption) run unconditionally; upstream-dependent teeth
    (per-entry two-tree asymmetry proofs) are skip-guarded by
    ``@requires_real_upstream``.
    """

    # ─── Offline: HASHES.sha256 manifest consumption ─────────────────────

    def test_hashes_file_exists(self):
        assert HASHES_FILE.is_file(), (
            f"hash manifest missing: {HASHES_FILE}. "
            "Run tools/vendor/od_vendor_snapshot.py."
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
        # The vendor script writes sorted for stability; pin it as a property.
        lines = [
            line
            for line in HASHES_FILE.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        rels = [line.partition("  ")[2] for line in lines]
        assert rels == sorted(rels), "hash manifest lines are not sorted by relpath"

    def test_hashes_file_count_matches_directory_entries(self):
        # The manifest at slice ③ records 27 vendored files (10 daemon
        # composer modules + 17 contracts mirror files); the vendored
        # tree minus the locally-owned HASHES.sha256 itself must have
        # the same count.
        recorded = _parse_sha256sum_file(HASHES_FILE)
        vendored = [
            f.relative_to(SNAPSHOT_ROOT).as_posix()
            for f in sorted(SNAPSHOT_ROOT.rglob("*"))
            if f.is_file() and f.name != "HASHES.sha256"
        ]
        assert len(vendored) == len(recorded), (
            f"vendored file count {len(vendored)} != manifest entries {len(recorded)}"
        )
        assert len(recorded) == 27, (
            f"snapshot-class hash manifest must carry 27 entries "
            f"(10 daemon + 17 contracts per slice ③ manifest); got {len(recorded)}"
        )

    def test_vendored_files_match_recorded_hashes(self):
        # Re-hash every vendored file under snapshot_with_drift_alarm/
        # (excluding the locally-owned HASHES.sha256) and compare to
        # the recorded manifest.  This is the offline pinning tooth
        # that consumes the otherwise-dead snapshot-class hash file.
        recorded = _parse_sha256sum_file(HASHES_FILE)
        mismatches = []
        missing = []
        extra = []
        checked = 0

        for f in sorted(SNAPSHOT_ROOT.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(SNAPSHOT_ROOT).as_posix()
            if rel == "HASHES.sha256":
                continue
            data = f.read_bytes()
            sha = _sha256(data)
            checked += 1
            if rel not in recorded:
                extra.append(rel)
                continue
            if recorded[rel] != sha:
                mismatches.append((rel, recorded[rel], sha))

        for rel in recorded:
            full = SNAPSHOT_ROOT / rel
            if not full.is_file():
                missing.append(rel)

        assert mismatches == [], f"sha256 mismatches: {mismatches[:3]}"
        assert missing == [], f"recorded entries with no file: {missing[:3]}"
        assert extra == [], f"vendored files not in manifest: {extra[:3]}"
        assert checked == len(recorded), (
            f"file count {checked} != manifest entries {len(recorded)}"
        )

    def test_hashes_file_is_not_listed_in_its_own_manifest(self):
        # The HASHES.sha256 file lives inside snapshot_with_drift_alarm/
        # (parallel to copy_freely/); it is a locally-owned file and
        # must NOT appear as a vendored entry in the manifest.
        recorded = _parse_sha256sum_file(HASHES_FILE)
        assert "HASHES.sha256" not in recorded, (
            "HASHES.sha256 is locally-owned; it must not appear in its own manifest"
        )

    # ─── Upstream-dependent: per-register-entry two-tree asymmetry proofs

    @requires_real_upstream
    def test_register_entry_1_daemon_system_two_tree_drift(self):
        # Register entry id=1: daemon/system.ts.  Per od-resource-layer
        # §1.B the divergence is upstream two-tree (daemon blob ≠
        # contracts blob); the vendored byte is BYTE-IDENTICAL to the
        # upstream daemon blob (the slice ③ vendoring is byte-faithful).
        # The pinning tooth therefore asserts:
        #   (a) vendored equals upstream daemon blob (frozen)
        #   (b) upstream daemon blob ≠ upstream contracts blob
        #       (the two-tree drift the entry actually documents)
        # This is the honest interpretation: today the vendored byte
        # DOES mirror the upstream daemon byte, so a naive
        # "vendored must differ from upstream" assertion would
        # falsify reality.  The no-mirror tooth is armed at first
        # byte divergence (CON §5) and is the only assertion that
        # travels when the vendored vs upstream daemon relationship
        # inverts.
        vendored_rel = "prompts/daemon/system.ts"
        vendored_bytes = (SNAPSHOT_ROOT / vendored_rel).read_bytes()
        daemon_bytes = _upstream_blob(UPSTREAM_DIR, f"{UPSTREAM_DAEMON}/system.ts")
        contracts_bytes = _upstream_blob(UPSTREAM_DIR, f"{UPSTREAM_CONTRACTS}/system.ts")

        # (a) frozen: vendored equals upstream daemon.
        assert vendored_bytes == daemon_bytes, (
            f"vendored {vendored_rel} has diverged from upstream daemon "
            f"({UPSTREAM_TAG}): the divergence register entry id=1 must be updated "
            "with the actual delta"
        )
        # (b) the two-tree asymmetry the entry documents is real.
        assert daemon_bytes != contracts_bytes, (
            "upstream daemon blob and contracts blob for system.ts are "
            "byte-identical; the two-tree drift entry id=1 documents is no "
            "longer present in upstream — the entry should be retired"
        )

    @requires_real_upstream
    def test_register_entry_2_daemon_media_contract_two_tree_drift(self):
        # Register entry id=2: daemon/media-contract.ts.  Same shape as
        # id=1 — upstream-internal daemon/contracts asymmetry; vendored
        # byte equals upstream daemon blob (frozen); the no-mirror
        # tooth travels with the entry.
        vendored_rel = "prompts/daemon/media-contract.ts"
        vendored_bytes = (SNAPSHOT_ROOT / vendored_rel).read_bytes()
        daemon_bytes = _upstream_blob(UPSTREAM_DIR, f"{UPSTREAM_DAEMON}/media-contract.ts")
        contracts_bytes = _upstream_blob(UPSTREAM_DIR, f"{UPSTREAM_CONTRACTS}/media-contract.ts")

        assert vendored_bytes == daemon_bytes, (
            f"vendored {vendored_rel} has diverged from upstream daemon "
            f"({UPSTREAM_TAG}): the divergence register entry id=2 must be updated "
            "with the actual delta"
        )
        assert daemon_bytes != contracts_bytes, (
            "upstream daemon blob and contracts blob for media-contract.ts are "
            "byte-identical; the two-tree drift entry id=2 documents is no "
            "longer present in upstream — the entry should be retired"
        )

    @requires_real_upstream
    def test_register_entry_3_daemon_core_slim_contracts_mirror_absence(self):
        # Register entry id=3: daemon/core-slim.ts.  Per od-resource-layer
        # §1.B core-slim is daemon-ONLY — the contracts tree has NO
        # corresponding file.  The asymmetry the entry documents is
        # the ABSENCE in upstream contracts tree, not a byte difference
        # in a paired file.  The pinning tooth therefore asserts:
        #   (a) vendored equals upstream daemon blob (frozen)
        #   (b) upstream contracts tree has no core-slim.ts
        #       (the absence asymmetry the entry actually documents)
        vendored_rel = "prompts/daemon/core-slim.ts"
        vendored_bytes = (SNAPSHOT_ROOT / vendored_rel).read_bytes()
        daemon_bytes = _upstream_blob(UPSTREAM_DIR, f"{UPSTREAM_DAEMON}/core-slim.ts")

        assert vendored_bytes == daemon_bytes, (
            f"vendored {vendored_rel} has diverged from upstream daemon "
            f"({UPSTREAM_TAG}): the divergence register entry id=3 must be updated "
            "with the actual delta"
        )
        # (b) the contracts mirror asymmetry the entry documents is real.
        assert not _upstream_has_path(UPSTREAM_DIR, f"{UPSTREAM_CONTRACTS}/core-slim.ts"), (
            "upstream contracts tree now contains core-slim.ts; the asymmetry "
            "entry id=3 documents is no longer present in upstream — the entry "
            "should be retired (core-slim is no longer daemon-only)"
        )

    @requires_real_upstream
    def test_register_entry_4_three_files_two_tree_drift(self):
        # Register entry id=4: three daemon files (directions.ts,
        # discovery.ts, official-system.ts).  Per od-resource-layer
        # §1.B all three exhibit upstream two-tree drift (daemon blob
        # ≠ contracts blob).  Same honest shape as id=1/id=2:
        # vendored equals upstream daemon (frozen) AND upstream
        # daemon ≠ upstream contracts for each of the three files.
        for filename in ("directions.ts", "discovery.ts", "official-system.ts"):
            vendored_rel = f"prompts/daemon/{filename}"
            vendored_bytes = (SNAPSHOT_ROOT / vendored_rel).read_bytes()
            daemon_bytes = _upstream_blob(UPSTREAM_DIR, f"{UPSTREAM_DAEMON}/{filename}")
            contracts_bytes = _upstream_blob(UPSTREAM_DIR, f"{UPSTREAM_CONTRACTS}/{filename}")

            assert vendored_bytes == daemon_bytes, (
                f"vendored {vendored_rel} has diverged from upstream daemon "
                f"({UPSTREAM_TAG}): the divergence register entry id=4 must be updated "
                "with the actual delta"
            )
            assert daemon_bytes != contracts_bytes, (
                f"upstream daemon blob and contracts blob for {filename} are "
                "byte-identical; the two-tree drift entry id=4 documents (one of its "
                "three files) is no longer present in upstream — the entry should be retired"
            )