#!/usr/bin/env python3
"""The metrics-agent-tools Phase 5 MUTATION MATRIX.

Every mutation below is planted INDEPENDENTLY on the production source,
the intended pack is run, and the result must be RED; the file is then
restored BYTE-IDENTICALLY and the pack is re-run and must be GREEN.

The RED surface of each mutation is DIFFERENT by design, and that is the
point — a mutation that only one pack can see is a mutation whose
protection is a single point of failure.

Restore discipline (NO ``git checkout --``):

  * each mutation is restored from an IN-MEMORY BYTE COPY of the file
    taken immediately before the mutation is planted;
  * BEFORE the first mutation the harness RECORDS a pre-run manifest —
    the HEAD sha + the sha256 of EVERY file the matrix will touch — to
    ``LOG_DIR/pre_run_manifest.json``, and journals the pre-mutation
    BYTES to ``LOG_DIR/recovery/<label>.blob`` while a mutation is in
    flight, so a run killed mid-mutation leaves the tree DIRTY but
    DETECTABLE and ATTRIBUTABLE:
      - ``--verify`` re-hashes every manifest file, reports each mismatch
        and names the mutation that was in flight (the ``inflight.json``
        journal);
      - ``--recover`` rewrites the journaled bytes back (the pinned
        interrupted-run path);
  * after the LAST restore the whole manifest is re-verified — a
    post-run mismatch is a loud FAIL, never a silent pass.

The working tree MUST be clean when the matrix starts (the pre-run
manifest shas are recorded against a committed tree, so a dirty tree is
attributable to the matrix alone) — the script refuses to run on a dirty
tree.

Invocation (from the repo root):

    .venv/bin/python test/packs/metrics_read_mutation_matrix_test.py
    .venv/bin/python test/packs/metrics_read_mutation_matrix_test.py --verify
    .venv/bin/python test/packs/metrics_read_mutation_matrix_test.py --recover
    .venv/bin/python test/packs/metrics_read_mutation_matrix_test.py --selftest
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = Path(os.environ.get("METRICS_READ_MUTATION_LOG_DIR", "/tmp/metrics-read-mutation"))
RECOVERY_DIR = LOG_DIR / "recovery"
PYTHON = str(REPO_ROOT / ".venv" / "bin" / "python")

#: The per-variable scrub (the ``POSTGRES_*`` glob form is a SILENT
#: NO-OP — every variable is scrubbed BY NAME).
SCRUB = [
    "env",
    "-u",
    "POSTGRES_USER",
    "-u",
    "POSTGRES_DB",
    "-u",
    "POSTGRES_HOST",
    "-u",
    "POSTGRES_PORT",
    "-u",
    "POSTGRES_PASSWORD",
    "-u",
    "DATABASE_URL",
    "-u",
    "PORT",
]

REGISTRATION_PACK = "tests/unit/test_metrics_read_registration.py"
TOOLS_PACK = "tests/unit/metrics/test_metrics_read_tools.py"
CONTRACT_PACK = "tests/unit/metrics/test_central_metrics_contract_doc.py"

#: The mutation table.  Every entry is
#: ``(label, relative path, find, replace, pack, red marker)``.
#:
#: The ``red marker`` is a substring that MUST appear in the RED run's
#: output — it proves the mutation was caught by the INTENDED assertion
#: and not by an unrelated crash.
MUTATIONS: list[tuple[str, str, str, str, str, str]] = [
    (
        "MT-01-count-pin",
        "tests/unit/test_board_registration.py",
        "assert len(PRIVILEGED_TOOL_CATEGORIES) == 17",
        "assert len(PRIVILEGED_TOOL_CATEGORIES) == 16",
        "tests/unit/test_board_registration.py",
        "test_board_is_the_ninth_entry",
    ),
    (
        "MT-02-availability-re-derived",
        "daemon/tools/metrics_read.py",
        '            "availability": availability,',
        '            "availability": {"state": "live", "reason": "census_gated"},',
        TOOLS_PACK,
        "test_the_availability_block_is_carried_verbatim",
    ),
    (
        "MT-03-sort-before-cut-removed",
        "daemon/tools/metrics_read.py",
        "    projected.sort(key=_sort_key)",
        "    pass  # MUTATION: the sort-before-cut was removed",
        TOOLS_PACK,
        "test_the_rows_sort_before_the_cut",
    ),
    (
        "MT-04-stale-rule-dropped",
        "daemon/tools/metrics_read.py",
        "        out[\"stale\"] = age > STALE_INTERVAL_MULTIPLIER * interval",
        "        out[\"stale\"] = age > 1e9 * interval  # MUTATION: the 3x rule",
        TOOLS_PACK,
        "test_the_derived_members_are_present",
    ),
    (
        "MT-05-budget-clamp-removed",
        "daemon/tools/metrics_read.py",
        "        max(MIN_MAX_POINTS, MAX_TOTAL_POINTS // (top_n + 1)),",
        "        max(MIN_MAX_POINTS, int(max_points)),  # MUTATION: the clamp",
        TOOLS_PACK,
        "test_the_budget_clamp_is_not_silent",
    ),
    (
        "MT-06-selector-cap-names-only",
        "daemon/tools/metrics_read.py",
        "    if len(set(resolved_names)) + len(requested_series) > MAX_NAMES:",
        "    if len(set(resolved_names)) > MAX_NAMES:  # MUTATION: names-only",
        TOOLS_PACK,
        "test_the_combined_selector_cap_is_enforced",
    ),
    (
        "MT-07-gate-demoted",
        "daemon/tools/metrics_read.py",
        """        if not _is_metrics_authorized(caller_agent_id):
            if not isinstance(caller_agent_id, str) or not caller_agent_id:
                return _refusal(
                    "metrics_families",""",
        """        _gate_probe = _provider_or_refuse(manager, "metrics_families")  # MUTATION
        if not _is_metrics_authorized(caller_agent_id):
            if not isinstance(caller_agent_id, str) or not caller_agent_id:
                return _refusal(
                    "metrics_families",""",
        REGISTRATION_PACK,
        "test_the_gate_order_is_body_gate_then_substrate_then_read",
    ),
    (
        "MT-08-decorator-order-flipped",
        "daemon/tools/metrics_read.py",
        """    @register_tool_category(METRICS_CATEGORY_KEY)
    @tool(args_schema=MetricsFamiliesInput)""",
        """    @tool(args_schema=MetricsFamiliesInput)  # MUTATION: the order flipped
    @register_tool_category(METRICS_CATEGORY_KEY)""",
        REGISTRATION_PACK,
        "test_the_decorator_order_is_register_then_tool",
    ),
    (
        "MT-09-delta-fabricated",
        "daemon/tools/metrics_read.py",
        """    if len(points) < 2:
        return None, "insufficient_points\"""",
        """    if len(points) < 2:
        return 0.0, None  # MUTATION: a fabricated zero delta""",
        TOOLS_PACK,
        "test_an_insufficient_window_yields_null_never_zero",
    ),
    (
        "MT-10-cursor-member-added",
        "daemon/tools/metrics_read.py",
        """        compare: Annotated[
            bool,""",
        """        cursor: Annotated[
            str | None,
            Field(default=None, description="MUTATION: a cursor member"),
        ]
        compare: Annotated[
            bool,""",
        TOOLS_PACK,
        "test_no_cursor_is_accepted_or_returned",
    ),
    (
        "MT-11-floor-recheck-removed",
        "daemon/tools/metrics_read.py",
        """    if not resolved_names and not requested_series:
        raise InvalidLabelFilter(""",
        """    if False:  # MUTATION: the floor RE-CHECK was removed
        raise InvalidLabelFilter(""",
        TOOLS_PACK,
        "test_an_unknown_family_is_refused_not_unbounded",
    ),
    (
        "MT-12-contract-substring-perturbed",
        "docs/architecture/metrics-query-contract.md",
        "\ncapture_off\n",
        "\ncapture_offX  # MUTATION: a pinned substring was perturbed\n",
        CONTRACT_PACK,
        "test_the_availability_set_is_closed_and_exact",
    ),
    # ── THE COUNCIL FIX CYCLE (2026-11-03): the two BINDING legs the
    #    first cut silently dropped — the exact legs that would have
    #    caught C1 (the byte ceiling was dead code) and C2 (the false
    #    FOLDED record). ──
    (
        "MT-13-byte-ceiling-trim-removed",
        "daemon/tools/metrics_read.py",
        """from daemon.services.metrics_query.selectors import (
    InvalidLabelFilter,
    parse_series_selector,
)""",
        """from daemon.services.metrics_query.selectors import (
    InvalidLabelFilter,
    parse_series_selector,
)

# MUTATION: the byte ceiling was removed
RESPONSE_BYTE_CEILING = 10**9""",
        TOOLS_PACK,
        "test_a_fat_catalog_is_trimmed_to_the_byte_ceiling",
    ),
    # ── MT-13's EXPRESSION was re-cut by the C1 fix (the delta
    #    re-review, 2026-11-03). The leg's INTENT is unchanged — "the
    #    byte ceiling is ENFORCED" — but the first expression (the
    #    trim's default target) no longer bites: the door's
    #    final-envelope re-check (the second pass) trims to the plain
    #    ceiling, so removing the DEFAULT target alone left the ceiling
    #    enforced (the leg went GREEN — a vacuous leg, caught by the
    #    matrix run). The expression now removes the CEILING ITSELF
    #    (the module-level binding), which is what the leg claims. ──
    # ── the legs the first cut dropped that this fix cycle FOLDS (each
    #    one has an existing behavioural pin to bite). ──
    (
        "MT-14-truncated-hardwired-false",
        "daemon/tools/metrics_read.py",
        """    truncated = bool(more_available) or (
        dropped_count is not None and dropped_count > 0
    )""",
        """    truncated = False  # MUTATION: the truncated flag is hardwired""",
        TOOLS_PACK,
        "test_the_top_n_cut_reports_the_drop",
    ),
    (
        "MT-15-narrow-hint-emptied",
        "daemon/tools/metrics_read.py",
        """        narrow_hint = (
            f"no family with id {family!r}; known ids: {', '.join(known)}"
        )""",
        """        narrow_hint = ""  # MUTATION: the hint was emptied""",
        TOOLS_PACK,
        "test_an_unknown_family_is_not_an_error",
    ),
    (
        "MT-16-caveat-attribute-token",
        "daemon/tools/metrics_read.py",
        '"not an absence of data — the series below are real."',
        '"not an absence of data (R-ii) — the series below are real."',
        TOOLS_PACK,
        "test_the_dark_with_live_caveat_is_generic",
    ),
    (
        "MT-17-multi-family-filter-dropped",
        "daemon/tools/metrics_read.py",
        "        if requested_families and row_family not in requested_families:",
        "        if False:  # MUTATION: the multi-family filter was dropped",
        TOOLS_PACK,
        "test_one_provider_call_for_a_multi_family_filter",
    ),
    (
        "MT-18-ring-state-signal-removed",
        "daemon/tools/metrics_read.py",
        "    ring_empty = bool(age == 0.0)",
        "    ring_empty = False  # MUTATION: the ring-state signal was removed",
        TOOLS_PACK,
        "test_an_empty_ring_reports_the_ring_state",
    ),
    (
        "MT-19-per-series-freshness-dropped",
        "daemon/tools/metrics_read.py",
        '        out["points_span_s"] = _points_span(points)',
        '        out["points_span_s"] = 0.0  # MUTATION: the freshness was dropped',
        TOOLS_PACK,
        "test_the_per_series_freshness_is_present",
    ),
    (
        "MT-20-racy-bool-doctrine-removed",
        "daemon/tools/metrics_read.py",
        '        "doctrine": READ_STATUS_DOCTRINE,',
        '        "doctrine": "",  # MUTATION: the doctrine statement was removed',
        TOOLS_PACK,
        "test_the_doctrine_states_the_racy_bool_semantics",
    ),
    (
        "MT-21-clamp-applied-silently",
        "daemon/tools/metrics_read.py",
        """        payload["caveats"].append(
            "max_points was clamped from "
            f"{int(max_points)} to {effective_max_points} so the returned "
            "leg stays within the daemon's total-point budget."
        )""",
        """        pass  # MUTATION: the clamp is now SILENT""",
        TOOLS_PACK,
        "test_the_budget_clamp_is_not_silent",
    ),
]

#: The FOLLOW legs — a mutation whose EXPECTED result is NOT a red but a
#: VALUE that MOVES with the mutated source (the drift class: a
#: duplicated literal would keep the consumer green while the producer
#: moved).  The council's binding AmA-7/PL-3 leg is this shape: the SEAM
#: constant changes and the TOOL's clamp must FOLLOW it.
FOLLOW_LEGS: list[tuple[str, str, str, str, str]] = [
    (
        "MT-22-seam-constant-single-sourcing",
        "daemon/services/metrics_query/constants.py",
        "MAX_TOTAL_POINTS: int = 12_000",
        "MAX_TOTAL_POINTS: int = 11_000",
        "daemon/tools/metrics_read.py",
    ),
]

#: The probe that reads the tool's effective clamp (the FOLLOW leg's
#: observation point).
_CLAMP_PROBE = r"""
import asyncio, json
from daemon.tools.metrics_read import create_metrics_read_tools


class _P:
    listener_enabled = True
    census_enabled = True
    registry_read_ok = True
    effective_interval_s = 15.0
    last_sample_age_s = 3.0

    def families(self):
        return [{"id": "F1", "role": "stat", "source_idiom": "registry",
                 "durability": "process", "live_series_count": 1,
                 "cardinality_contribution": 1, "availability": {"state": "live"},
                 "names": [{"name": "m", "kind": "gauge",
                             "cardinality_class": "none", "labels": None,
                             "monotonic": False, "chart_role": "line",
                             "buckets": None, "help": "h"}]}]

    def snapshot(self, **kw):
        return []

    def history(self, **kw):
        return [{"name": "m", "labels": {}, "kind": "gauge", "monotonic": False,
                 "points": [[1, 1.0], [2, 2.0]]}]


class _M:
    metrics_query_provider = _P()


async def main():
    tools = create_metrics_read_tools(
        _M(), current_instance_id="", agent_id="maintenancer", agent_tag=None
    )
    by_name = {getattr(x, "name", ""): x for x in tools}
    out = json.loads(
        await by_name["metrics_history"].ainvoke(
            {"names": ["m"], "top_n": 50, "max_points": 240}
        )
    )
    print(out["query"]["max_points"])


asyncio.run(main())
"""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(*args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args], capture_output=True, text=True, check=False
    )
    return out.stdout.strip()


def _touched_files() -> list[str]:
    seen: list[str] = []
    for _label, rel, *_rest in MUTATIONS:
        if rel not in seen:
            seen.append(rel)
    for _label, rel, *_rest in FOLLOW_LEGS:
        if rel not in seen:
            seen.append(rel)
    return seen


def _dirty_paths() -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    return [line for line in out.stdout.splitlines() if line.strip()]


def _record_manifest() -> dict:
    manifest = {
        "head_sha": _git("rev-parse", "HEAD"),
        "files": {rel: _sha256(REPO_ROOT / rel) for rel in _touched_files()},
    }
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    (LOG_DIR / "pre_run_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return manifest


def _verify_manifest(manifest: dict) -> list[str]:
    mismatches: list[str] = []
    for rel, sha in manifest["files"].items():
        path = REPO_ROOT / rel
        if not path.exists():
            mismatches.append(f"{rel}: MISSING")
            continue
        if _sha256(path) != sha:
            mismatches.append(f"{rel}: sha mismatch")
    return mismatches


def _run_pack(pack: str) -> tuple[int, str]:
    proc = subprocess.run(
        [*SCRUB, PYTHON, "-m", "pytest", pack, "-q", "--tb=line", "-p", "no:randomly",
         "--timeout=120"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _journal(label: str, rel: str, blob: bytes) -> None:
    RECOVERY_DIR.mkdir(parents=True, exist_ok=True)
    (RECOVERY_DIR / f"{label}.blob").write_bytes(blob)
    (LOG_DIR / "inflight.json").write_text(
        json.dumps({"label": label, "path": rel}, indent=2), encoding="utf-8"
    )


def _unjournal(label: str) -> None:
    (RECOVERY_DIR / f"{label}.blob").unlink(missing_ok=True)
    inflight = LOG_DIR / "inflight.json"
    if inflight.exists():
        try:
            data = json.loads(inflight.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if data.get("label") == label:
            inflight.unlink(missing_ok=True)


def _run_probe() -> int:
    """Run the clamp probe and return the tool's effective max_points."""
    proc = subprocess.run(
        [*SCRUB, PYTHON, "-c", _CLAMP_PROBE],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return -1
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if line.isdigit():
            return int(line)
    return -1


def _run_follow_legs() -> int:
    """The FOLLOW legs — the consumer's value must MOVE with the source."""
    failures = 0
    for label, rel, find, replace, consumer in FOLLOW_LEGS:
        path = REPO_ROOT / rel
        original = path.read_bytes()
        text = original.decode("utf-8")
        if text.count(find) != 1:
            print(f"{label}: SKIPPED (the find-string is not unique: {text.count(find)})")
            failures += 1
            continue
        _journal(label, rel, original)
        try:
            before = _run_probe()
            path.write_text(text.replace(find, replace), encoding="utf-8")
            after = _run_probe()
            path.write_bytes(original)
            if before < 0 or after < 0:
                print(f"{label}: FAIL — the probe did not run")
                failures += 1
            elif after == before:
                print(
                    f"{label}: FAIL — the consumer's value did NOT follow the "
                    f"mutated source ({before} -> {after}); a DUPLICATED "
                    "literal would look exactly like this"
                )
                failures += 1
            else:
                print(
                    f"{label}: OK (the consumer followed the source: "
                    f"{before} -> {after})"
                )
        finally:
            path.write_bytes(original)
            _unjournal(label)
    return failures


def run_matrix() -> int:
    dirty = _dirty_paths()
    if dirty:
        print("RESULT: FAIL (DIRTY TREE) — the matrix refuses to run:")
        for line in dirty:
            print(f"  {line}")
        print("  (commit or stash the tree first; a dirty tree is not attributable)")
        return 1

    manifest = _record_manifest()
    print(f"pre-run manifest: {manifest['head_sha']} over {len(manifest['files'])} files")

    failures = 0
    for label, rel, find, replace, pack, marker in MUTATIONS:
        path = REPO_ROOT / rel
        original = path.read_bytes()
        text = original.decode("utf-8")
        if text.count(find) != 1:
            print(f"{label}: SKIPPED (the find-string is not unique: {text.count(find)})")
            failures += 1
            continue
        _journal(label, rel, original)
        try:
            path.write_text(text.replace(find, replace), encoding="utf-8")
            rc, out = _run_pack(pack)
            if rc == 0:
                print(f"{label}: FAIL — the mutation did NOT turn the pack RED")
                failures += 1
            elif marker not in out:
                print(
                    f"{label}: FAIL — the pack went RED but the INTENDED assertion "
                    f"({marker!r}) is not in the output"
                )
                failures += 1
            else:
                print(f"{label}: OK (RED via {marker})")
            path.write_bytes(original)
            rc2, out2 = _run_pack(pack)
            if rc2 != 0:
                print(f"{label}: FAIL — the restore did NOT turn the pack GREEN")
                failures += 1
            elif path.read_bytes() != original:
                print(f"{label}: FAIL — the restore is NOT byte-identical")
                failures += 1
            else:
                print(f"{label}: OK (restored byte-identically, pack GREEN)")
        finally:
            path.write_bytes(original)
            _unjournal(label)

    failures += _run_follow_legs()

    mismatches = _verify_manifest(manifest)
    if mismatches:
        print("RESULT: FAIL (post-run manifest mismatch)")
        for line in mismatches:
            print(f"  {line}")
        failures += 1
    else:
        print("post-run manifest: every touched file is byte-identical")

    if failures:
        print(f"RESULT: FAIL ({failures} leg(s) failed)")
        return 1
    print(
        f"RESULT: PASS ({len(MUTATIONS)} RED/GREEN legs + "
        f"{len(FOLLOW_LEGS)} FOLLOW leg(s))"
    )
    return 0


def verify() -> int:
    manifest_path = LOG_DIR / "pre_run_manifest.json"
    if not manifest_path.exists():
        print("no pre-run manifest — nothing to verify")
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mismatches = _verify_manifest(manifest)
    inflight = LOG_DIR / "inflight.json"
    if inflight.exists():
        print(f"IN-FLIGHT mutation: {inflight.read_text(encoding='utf-8').strip()}")
    if mismatches:
        print("RESULT: FAIL (dirty tree)")
        for line in mismatches:
            print(f"  {line}")
        return 1
    print("RESULT: CLEAN (every manifest file is byte-identical)")
    return 0


def recover() -> int:
    inflight = LOG_DIR / "inflight.json"
    if not inflight.exists():
        print("no in-flight mutation — nothing to recover")
        return 0
    data = json.loads(inflight.read_text(encoding="utf-8"))
    label = data.get("label")
    rel = data.get("path")
    blob = RECOVERY_DIR / f"{label}.blob"
    if not blob.exists():
        print(f"RESULT: FAIL (no recovery blob for {label})")
        return 1
    shutil.copyfile(blob, REPO_ROOT / rel)
    _unjournal(label)
    print(f"RESULT: RECOVERED {rel} from the journaled bytes of {label}")
    return 0


def selftest() -> int:
    """Exercise the restore/interruption pins."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    probe = REPO_ROOT / "daemon" / "tools" / "metrics_read.py"
    original = probe.read_bytes()
    try:
        probe.write_bytes(b"# MUTATION: the selftest byte\n")
        (LOG_DIR / "pre_run_manifest.json").write_text(
            json.dumps({"head_sha": "selftest", "files": {"daemon/tools/metrics_read.py": _sha256(probe)}}),
            encoding="utf-8",
        )
        if verify() != 1:
            print("RESULT: FAIL (the verifier did not detect the planted byte)")
            return 1
        probe.write_bytes(original)
        if verify() != 0:
            print("RESULT: FAIL (the verifier did not confirm the restored byte)")
            return 1
    finally:
        probe.write_bytes(original)
        (LOG_DIR / "pre_run_manifest.json").unlink(missing_ok=True)
    print("RESULT: PASS (the restore/interruption pins hold)")
    return 0


def main(argv: list[str]) -> int:
    if "--verify" in argv:
        return verify()
    if "--recover" in argv:
        return recover()
    if "--selftest" in argv:
        return selftest()
    return run_matrix()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
