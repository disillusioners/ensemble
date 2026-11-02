#!/usr/bin/env python3
"""The central-metrics-v1 Phase 5 (FE charts page) MUTATION MATRIX.

Every mutation in the plan's table (P5-M1 .. P5-M11) is planted
INDEPENDENTLY on the production source, the intended pack is run, and
the result must be RED; the file is then restored BYTE-IDENTICALLY and
the pack is re-run and must be GREEN.

The RED surface of each mutation is DIFFERENT by design, and that is the
point:

  * the jest corpus catches the behavioural mutations (P5-M1 .. P5-M8,
    P5-M10, P5-M11);
  * the SCOPED TYPE-CHECK leg (`tsc --noEmit -p tsconfig.metrics-fe.json`)
    catches P5-M9 — the availability union is a TYPE-level property and
    jest does not type-check, so a narrowed union is invisible to the
    jest corpus. That is exactly why the type leg exists.

Invocation (from the repo root):

    python3 test/packs/metrics_fe_mutation_matrix_test.py
    python3 test/packs/metrics_fe_mutation_matrix_test.py --verify    # detect + attribute a dirty tree
    python3 test/packs/metrics_fe_mutation_matrix_test.py --recover   # restore from the recovery journal
    python3 test/packs/metrics_fe_mutation_matrix_test.py --selftest  # the restore/interruption pins

Restore discipline (fix cycle 2, A-7 — NO `git checkout --`):

  * each mutation is restored from an IN-MEMORY BYTE COPY of the file
    taken immediately before the mutation is planted;
  * BEFORE the first mutation the harness RECORDS a pre-run manifest —
    the HEAD sha + the sha256 of EVERY file the matrix will touch — to
    `LOG_DIR/pre_run_manifest.json`, and journals the pre-mutation
    BYTES to `LOG_DIR/recovery/<label>.blob` while a mutation is in
    flight; so a run killed mid-mutation leaves the tree DIRTY but
    DETECTABLE and ATTRIBUTABLE:
      - `--verify` re-hashes every manifest file, reports each mismatch
        and names the mutation that was in flight (the `inflight.json`
        journal);
      - `--recover` rewrites the journaled bytes back (the pinned
        interrupted-run path — exercised by `--selftest`);
  * after the LAST restore the whole manifest is re-verified — a
    post-run mismatch is a loud FAIL, never a silent pass.

The RED surface of each mutation is DIFFERENT by design, and that is the
point:

  * the jest corpus catches the behavioural mutations (P5-M1 .. P5-M8,
    P5-M10, P5-M11, FIX2-A5);
  * the SCOPED TYPE-CHECK leg (`tsc --noEmit -p tsconfig.metrics-fe.json`)
    catches P5-M9 and FIX2-W6 — the availability union and the
    declared/concrete label VALUE-TYPE split are TYPE-level properties
    and jest does not type-check, so a re-merged value type is invisible
    to the jest corpus. That is exactly why the type leg exists.

The working tree MUST be clean when the matrix starts (the pre-run
manifest shas are recorded against a committed tree, so a dirty tree is
attributable to the matrix alone) — the script refuses to run on a dirty
tree.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND = REPO_ROOT / "frontend"
LOG_DIR = Path(os.environ.get("METRICS_FE_MUTATION_LOG_DIR", "/tmp/metrics-fe-mutations"))

JEST = FRONTEND / "node_modules" / ".bin" / "jest"
TSC = FRONTEND / "node_modules" / ".bin" / "tsc"

# ── The pack file sets (the D1 pack ids of the plan) ───────────────────
PACK_MODELS = ["src/app/models/metrics.model.spec.ts"]
PACK_SERVICE = ["src/app/services/metrics.service.spec.ts"]
PACK_SVG = ["src/app/components/charts/svg-line-chart/svg-line-chart.component.spec.ts"]
PACK_PAGE = [
    "src/app/pages/metrics/metrics.component.spec.ts",
    "src/app/pages/metrics/metrics-gear-menu.spec.ts",
    "src/app/pages/metrics/metrics.bindings.pins.spec.ts",
    "src/app/pages/metrics/overview/overview-tab.component.spec.ts",
    "src/app/pages/metrics/charts/charts-tab.component.spec.ts",
    "src/app/pages/metrics/families/families-tab.component.spec.ts",
]
PACK_BINDINGS = ["src/app/pages/metrics/metrics.bindings.pins.spec.ts"]
PACK_ORDERING = ["src/app/pages/resources/resources-gear-menu.spec.ts"]


@dataclass
class Mutation:
    label: str
    intent: str
    file: str
    old: str
    new: str
    command: list[str]
    kind: str = "jest"  # jest | tsc
    extra_red: list[str] = field(default_factory=list)


MUTATIONS: list[Mutation] = [
    Mutation(
        label="P5-M1",
        intent="the named POLL_INTERVAL_MS is renamed",
        file="frontend/src/app/pages/metrics/overview/overview-tab.component.ts",
        old="readonly POLL_INTERVAL_MS = 10_000;",
        new="readonly POLL_CADENCE_MS = 10_000;",
        command=["jest"] + PACK_PAGE,
        extra_red=["the overview spec (it reads the constant)"],
    ),
    Mutation(
        label="P5-M2",
        intent="the clearInterval teardown is removed",
        file="frontend/src/app/pages/metrics/overview/overview-tab.component.ts",
        old="      clearInterval(this.pollHandle);\n",
        new="      // MUTATION P5-M2: the teardown removed\n",
        command=["jest"] + PACK_PAGE,
        extra_red=["the overview spec (the runtime teardown assertion)"],
    ),
    Mutation(
        label="P5-M3",
        intent="the supersede token check is inverted (the older response wins)",
        file="frontend/src/app/pages/metrics/overview/overview-tab.component.ts",
        old="          if (token !== this.requestSeq) {\n            return;\n          }",
        new="          if (token === this.requestSeq) {\n            return;\n          }",
        command=["jest"] + PACK_PAGE,
    ),
    Mutation(
        label="P5-M4",
        intent="the empty-state condition switches from 'no family live' to 'no families at all'",
        file="frontend/src/app/pages/metrics/metrics.component.ts",
        old="() => this.catalogLoaded() && this.catalogError() === null && !this.anyFamilyLive(),",
        new="() => this.catalogLoaded() && this.catalogError() === null && this.families().length === 0,",
        command=["jest"] + PACK_PAGE,
    ),
    Mutation(
        label="P5-M5",
        intent="the model mirror's field name drifts from the wire",
        file="frontend/src/app/models/metrics.model.ts",
        old="  readonly live_series_count: number;",
        new="  readonly liveSeriesCount: number;",
        command=["jest"] + PACK_MODELS,
    ),
    Mutation(
        label="P5-M6",
        intent="the SVG scale function's normalisation is removed",
        file="frontend/src/app/components/charts/svg-line-chart/svg-line-chart.component.ts",
        old="  return CHART_PLOT_BOTTOM - clamp01((value - min) / (max - min)) * CHART_PLOT_HEIGHT;",
        new="  return CHART_PLOT_BOTTOM - clamp01(value / (max || 1)) * CHART_PLOT_HEIGHT;",
        command=["jest"] + PACK_SVG,
    ),
    Mutation(
        label="P5-M7",
        intent="the gear-menu ordering pin's expected position changes",
        file="frontend/src/app/pages/resources/resources-gear-menu.spec.ts",
        old="    expect(gatewayIdx).toBeLessThan(metricsIdx);\n    expect(metricsIdx).toBeLessThan(dbUpdateIdx);",
        new="    expect(metricsIdx).toBeLessThan(gatewayIdx);\n    expect(metricsIdx).toBeLessThan(dbUpdateIdx);",
        command=["jest"] + PACK_ORDERING,
    ),
    Mutation(
        label="P5-M8",
        intent="the JSON_HEADERS are dropped from the service call",
        file="frontend/src/app/services/metrics.service.ts",
        old="    return this.http.get<MetricsSnapshotPayload>(MetricsService.SNAPSHOT_URL, {\n      headers: MetricsService.JSON_HEADERS,\n    });",
        new="    return this.http.get<MetricsSnapshotPayload>(MetricsService.SNAPSHOT_URL);",
        command=["jest"] + PACK_SERVICE,
    ),
    Mutation(
        label="P5-M9",
        intent="the availability union is narrowed to a closed literal set",
        file="frontend/src/app/models/metrics.model.ts",
        old="export type MetricsAvailabilityReason =\n  | MetricsKnownAvailabilityReason\n  | (string & {});",
        new="export type MetricsAvailabilityReason = MetricsKnownAvailabilityReason;",
        command=["tsc", "-p", "tsconfig.metrics-fe.json"],
        kind="tsc",
    ),
    Mutation(
        label="P5-M10",
        intent="the panel copy reverts to 'metrics disabled'",
        file="frontend/src/app/pages/metrics/metrics.component.ts",
        old="export const METRICS_EMPTY_PANEL_COPY = 'No metrics available yet';",
        new="export const METRICS_EMPTY_PANEL_COPY = 'Metrics disabled';",
        command=["jest"] + PACK_PAGE,
    ),
    Mutation(
        label="P5-M11",
        intent="the production-config build leg is dropped from the gate",
        file="test/packs/metrics_fe_dual_side_test.sh",
        old='    timeout 900 ./node_modules/.bin/ng build --configuration production 2>&1 | tee "$BUILD_PROD_LOG"',
        new='    :  # MUTATION P5-M11: the production-config build leg dropped from the gate',
        command=["jest"] + PACK_BINDINGS,
    ),
    Mutation(
        label="FIX2-W6",
        intent="the declared/concrete label VALUE-TYPE split is re-merged (the W-6 defect: one string-valued labels type serves both the DECLARED sets and the CONCRETE series)",
        file="frontend/src/app/models/metrics.model.ts",
        old="  readonly labels: MetricsDeclaredLabels | null;",
        new="  readonly labels: MetricsLabels | null;",
        command=["tsc", "-p", "tsconfig.metrics-fe.json"],
        kind="tsc",
        extra_red=["the model pack (the source-shape pin asserts the declared type)"],
    ),
    Mutation(
        label="FIX2-A5",
        intent="the error-copy branches collapse back to ONE copy (the A-5 defect: every failure wears the unreachable copy)",
        file="frontend/src/app/models/metrics.model.ts",
        old=(
            "  scope_insufficient:\n"
            "    'The metrics door rejected this session — the signed-in token lacks the maintenance:read scope.',\n"
            "  auth_not_configured:\n"
            "    'The metrics door is fail-closed — auth is not configured on the daemon, so it serves no metrics.',\n"
            "  registry_unavailable:\n"
            "    'The metrics door has no provider wired — the metrics service is unavailable on this daemon.',"
        ),
        new=(
            "  scope_insufficient: 'The metrics door is unreachable.',\n"
            "  auth_not_configured: 'The metrics door is unreachable.',\n"
            "  registry_unavailable: 'The metrics door is unreachable.',"
        ),
        command=["jest"] + PACK_MODELS,
        extra_red=["the page pack (the shell banners branch on the same decoder)"],
    ),
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_bytes(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


# ── A-7: the restore / manifest / journal machinery ────────────────────

MANIFEST_PATH = LOG_DIR / "pre_run_manifest.json"
INFLIGHT_PATH = LOG_DIR / "inflight.json"
RECOVERY_DIR = LOG_DIR / "recovery"


def restore_bytes(path: Path, blob: bytes) -> None:
    """Restore a file to an exact byte copy (A-7 — never `git checkout --`)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(blob)
        handle.flush()
        os.fsync(handle.fileno())


def write_inflight(label: str, file: str) -> None:
    """Journal the mutation IN FLIGHT — the attribution record a kill leaves behind."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    INFLIGHT_PATH.write_text(
        json.dumps({"label": label, "file": file, "recorded_at": _now()}, indent=2)
    )


def clear_inflight() -> None:
    try:
        INFLIGHT_PATH.unlink()
    except FileNotFoundError:
        pass


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def record_pre_run_manifest() -> dict:
    """Record HEAD + the sha256 of EVERY file the matrix will touch (A-7)."""
    head = git("rev-parse", "HEAD").stdout.strip() or "UNKNOWN"
    files = {}
    for mutation in MUTATIONS:
        rel = mutation.file
        if rel not in files:
            path = REPO_ROOT / rel
            files[rel] = sha256(path) if path.exists() else "ABSENT"
    manifest = {"head": head, "recorded_at": _now(), "files": files}
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))
    return manifest


def dirty_against_manifest(manifest: dict, root: Path = REPO_ROOT) -> list[str]:
    """Return one line per manifest file whose current sha differs (or is missing)."""
    lines = []
    for rel, recorded in manifest.get("files", {}).items():
        path = root / rel
        current = sha256(path) if path.exists() else "ABSENT"
        if current != recorded:
            lines.append(f"{rel}: recorded={recorded[:12]} current={current[:12]}")
    return lines


def verify_tree(manifest: dict, root: Path = REPO_ROOT) -> tuple[list[str], str | None]:
    """Detect a dirty tree against the manifest + attribute it (A-7).

    Attribution: the `inflight.json` journal names the mutation that was
    in flight when the run died; `git status` names anything else.
    """
    dirty = dirty_against_manifest(manifest)
    if not dirty:
        return [], None
    inflight = None
    try:
        inflight = json.loads(INFLIGHT_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    note = None
    if inflight:
        note = (
            f"ATTRIBUTION: the mutation {inflight.get('label')} was IN FLIGHT on "
            f"{inflight.get('file')} when the run was interrupted — run --recover to restore it."
        )
    else:
        untracked = git("status", "--porcelain").stdout.strip()
        note = f"ATTRIBUTION: no inflight journal — the dirt is NOT attributable to this matrix. git status:\n{untracked}"
    return dirty, note


def recover(manifest: dict, root: Path = REPO_ROOT) -> int:
    """Restore journaled files from the recovery blobs (the interrupted-run path)."""
    restored = 0
    for blob_path in sorted(RECOVERY_DIR.glob("*.blob")):
        payload = json.loads(blob_path.read_text())
        rel = payload["file"]
        blob = base64.b64decode(payload["bytes_b64"])
        restore_bytes(root / rel, blob)
        print(f"recovered {rel} from {blob_path.name} (sha {sha256_bytes(blob)[:12]})")
        restored += 1
    clear_inflight()
    still_dirty = dirty_against_manifest(manifest, root)
    if still_dirty:
        print("RECOVER INCOMPLETE — still dirty against the manifest:")
        for line in still_dirty:
            print(f"  {line}")
        return 1
    print("RECOVER OK — every manifest file matches its pre-run sha")
    return 0


def selftest() -> int:
    """The restore/interruption pins (A-7) — no production file is touched.

    P1: the restore function reproduces an exact byte copy (sha-equal).
    P2: a SIMULATED interruption — mutate, journal, skip the restore
        (the crash) — is DETECTED and ATTRIBUTED by verify_tree, and the
        journaled bytes RECOVER the file byte-identically.
    """
    failures = 0
    scratch = Path(
        tempfile.mkdtemp(prefix="metrics-fe-mutation-selftest-")
    )  # noqa: S108 — a throwaway scratch dir, cleaned below
    target = scratch / "prod.ts"
    target.write_text("export const ANSWER = 42;\n", encoding="utf-8")

    # ── P1: the in-memory restore roundtrip ──
    blob = target.read_bytes()
    target.write_text("export const ANSWER = 43;  // MUTATED\n", encoding="utf-8")
    restore_bytes(target, blob)
    p1_ok = target.read_bytes() == blob
    print(f"P1 restore_roundtrip: {'PASS' if p1_ok else 'FAIL'}")
    if not p1_ok:
        failures += 1

    # ── P2: the simulated interruption — detect, attribute, recover ──
    global RECOVERY_DIR, INFLIGHT_PATH, MANIFEST_PATH
    saved = (RECOVERY_DIR, INFLIGHT_PATH, MANIFEST_PATH)
    RECOVERY_DIR = scratch / "recovery"
    INFLIGHT_PATH = scratch / "inflight.json"
    MANIFEST_PATH = scratch / "pre_run_manifest.json"
    try:
        RECOVERY_DIR.mkdir(parents=True, exist_ok=True)
        # The manifest/journal paths are RELATIVE TO THE ROOT the
        # verify/recover calls resolve against (here: the scratch root).
        manifest = {"head": "selftest", "files": {"prod.ts": sha256(target)}}
        blob = target.read_bytes()
        # journal + mutate + CRASH (no restore)
        write_inflight("SELFTEST-MUTATION", "prod.ts")
        (RECOVERY_DIR / "SELFTEST-MUTATION.blob").write_text(
            json.dumps(
                {
                    "file": "prod.ts",
                    "bytes_b64": base64.b64encode(blob).decode("ascii"),
                }
            )
        )
        target.write_text("export const ANSWER = 44;  // MUTATED AGAIN\n", encoding="utf-8")

        dirty, note = verify_tree(manifest, root=scratch)
        detected = len(dirty) == 1 and "prod.ts" in dirty[0]
        attributed = note is not None and "SELFTEST-MUTATION" in note
        print(f"P2a interruption_detected: {'PASS' if detected else 'FAIL'}")
        print(f"P2b interruption_attributed: {'PASS' if attributed else 'FAIL'}")
        if not detected:
            failures += 1
        if not attributed:
            failures += 1

        rc = recover(manifest, root=scratch)
        recovered = rc == 0 and target.read_bytes() == blob
        print(f"P2c interruption_recovered: {'PASS' if recovered else 'FAIL'}")
        if not recovered:
            failures += 1
    finally:
        RECOVERY_DIR, INFLIGHT_PATH, MANIFEST_PATH = saved
        shutil.rmtree(scratch, ignore_errors=True)

    print("")
    print(f"SELFTEST: {'PASS' if failures == 0 else 'FAIL'} ({failures} failure(s))")
    return 0 if failures == 0 else 1


def run_command(command: list[str]) -> tuple[int, str]:
    """Run a leg inside frontend/ and return (exit code, output)."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if command[0] == "jest":
        argv = [str(JEST), "--ci", "--silent", "--runInBand", *command[1:]]
    elif command[0] == "tsc":
        argv = [str(TSC), "--noEmit", *command[1:]]
    else:  # pragma: no cover — the table never uses another kind
        raise ValueError(f"unknown leg kind: {command[0]}")
    proc = subprocess.run(
        argv,
        cwd=str(FRONTEND),
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def failing_nodes(output: str) -> list[str]:
    nodes = []
    for line in output.splitlines():
        if line.startswith("FAILED ") or line.startswith("ERROR "):
            nodes.append(line.split(" ", 1)[1].strip())
        elif " error TS" in line:
            nodes.append(line.strip())
    return nodes


def apply_mutation(mutation: Mutation) -> bool:
    path = REPO_ROOT / mutation.file
    text = path.read_text()
    if mutation.old not in text:
        return False
    path.write_text(text.replace(mutation.old, mutation.new, 1))
    return True


def main() -> int:
    dirty = git("status", "--porcelain").stdout.strip()
    if dirty:
        print("REFUSING TO RUN: the working tree is dirty — the pre-run manifest records")
        print("shas against a committed tree, so a dirty tree must be attributed first")
        print("(run --verify against the recorded manifest).")
        print(dirty)
        return 2

    manifest = record_pre_run_manifest()
    RECOVERY_DIR.mkdir(parents=True, exist_ok=True)
    print(f"pre-run manifest: HEAD={manifest['head'][:12]} files={len(manifest['files'])}")
    print(f"  -> {MANIFEST_PATH}")

    results = []
    failures = 0
    for mutation in MUTATIONS:
        path = REPO_ROOT / mutation.file
        blob = path.read_bytes()
        before_sha = sha256_bytes(blob)
        if not apply_mutation(mutation):
            print(f"{mutation.label}: FAIL — the mutation pattern was NOT FOUND in {mutation.file}")
            failures += 1
            continue

        # Journal the IN-FLIGHT mutation + the recovery bytes (A-7) so a
        # kill mid-mutation is detectable, attributable AND recoverable.
        RECOVERY_DIR.mkdir(parents=True, exist_ok=True)
        (RECOVERY_DIR / f"{mutation.label}.blob").write_text(
            json.dumps(
                {
                    "file": mutation.file,
                    "bytes_b64": base64.b64encode(blob).decode("ascii"),
                }
            )
        )
        write_inflight(mutation.label, mutation.file)

        red_exit, red_output = run_command(mutation.command)
        red_nodes = failing_nodes(red_output)

        restore_bytes(path, blob)  # the IN-MEMORY byte copy — never git
        clear_inflight()
        try:
            (RECOVERY_DIR / f"{mutation.label}.blob").unlink()
        except FileNotFoundError:
            pass
        after_sha = sha256(path)
        restored = before_sha == after_sha

        green_exit, green_output = run_command(mutation.command)
        green_nodes = failing_nodes(green_output)

        status = "PASS"
        detail = ""
        if red_exit == 0:
            status = "FAIL"
            detail = "the mutation did NOT go RED (exit 0)"
        elif not restored:
            status = "FAIL"
            detail = "the restore was NOT byte-identical"
        elif green_exit != 0:
            status = "FAIL"
            detail = f"the restore did NOT go GREEN (exit {green_exit})"
        if status == "FAIL":
            failures += 1

        log_path = LOG_DIR / f"{mutation.label}.log"
        log_path.write_text(red_output)
        green_log = LOG_DIR / f"{mutation.label}.green.log"
        green_log.write_text(green_output)

        results.append(
            {
                "label": mutation.label,
                "intent": mutation.intent,
                "file": mutation.file,
                "kind": mutation.kind,
                "red_exit": red_exit,
                "green_exit": green_exit,
                "red_nodes": red_nodes[:12],
                "restored_byte_identical": restored,
                "extra_red_expected": mutation.extra_red,
                "status": status,
                "detail": detail,
                "log": str(log_path),
            }
        )
        print(
            f"{mutation.label}: {status} — red_exit={red_exit} green_exit={green_exit} "
            f"restored={restored} nodes={red_nodes[:4]}"
        )
        if status == "FAIL":
            print(f"    DETAIL: {detail}")

    summary = {
        "mutations": results,
        "failures": failures,
        "total": len(results),
        "manifest": str(MANIFEST_PATH),
        "head": manifest["head"],
    }
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    (LOG_DIR / "summary.json").write_text(json.dumps(summary, indent=2))

    # ── The post-run guard: EVERY manifest file must match its pre-run sha.
    final_dirty, final_note = verify_tree(manifest)
    if final_dirty:
        failures += 1
        print("")
        print("POST-RUN GUARD: FAIL — the tree is dirty against the pre-run manifest:")
        for line in final_dirty:
            print(f"  {line}")
        if final_note:
            print(f"  {final_note}")
    else:
        print("")
        print("POST-RUN GUARD: OK — every touched file matches its pre-run sha")

    print("")
    print(f"MUTATION MATRIX: {len(results) - failures}/{len(results)} PASS")
    print(f"summary: {LOG_DIR / 'summary.json'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if "--recover" in sys.argv:
        try:
            manifest = json.loads(MANIFEST_PATH.read_text())
        except FileNotFoundError:
            print(f"RECOVER REFUSED: no manifest at {MANIFEST_PATH} — nothing to recover against.")
            sys.exit(2)
        sys.exit(recover(manifest))
    if "--verify" in sys.argv:
        try:
            manifest = json.loads(MANIFEST_PATH.read_text())
        except FileNotFoundError:
            print(f"VERIFY REFUSED: no manifest at {MANIFEST_PATH} — run the matrix first.")
            sys.exit(2)
        dirty, note = verify_tree(manifest)
        if dirty:
            print("VERIFY: DIRTY against the manifest:")
            for line in dirty:
                print(f"  {line}")
            if note:
                print(note)
            sys.exit(1)
        print("VERIFY: CLEAN — every manifest file matches its pre-run sha")
        sys.exit(0)
    sys.exit(main())
