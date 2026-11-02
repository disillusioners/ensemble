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

The working tree MUST be clean (the mutations are restored with
`git checkout --`, which restores from the index) — the script refuses
to run on a dirty tree.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
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
        command=["jest"] + PACK_BINDINGS,
        extra_red=["metrics-fe-page (the overview spec reads the constant)"],
    ),
    Mutation(
        label="P5-M2",
        intent="the clearInterval teardown is removed",
        file="frontend/src/app/pages/metrics/overview/overview-tab.component.ts",
        old="      clearInterval(this.pollHandle);\n",
        new="      // MUTATION P5-M2: the teardown removed\n",
        command=["jest"] + PACK_BINDINGS,
        extra_red=["metrics-fe-page (the runtime teardown assertion)"],
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
        new='    timeout 900 ./node_modules/.bin/ng build --configuration production 2>&1 | tee "$BUILD_PROD_LOG"\n    # MUTATION P5-M11: the production-config build leg dropped from the gate',
        command=["jest"] + PACK_BINDINGS,
    ),
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


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
        print("REFUSING TO RUN: the working tree is dirty — the mutations are restored")
        print("from the index, so the tree must be clean (commit first).")
        print(dirty)
        return 2

    results = []
    failures = 0
    for mutation in MUTATIONS:
        path = REPO_ROOT / mutation.file
        before_sha = sha256(path)
        if not apply_mutation(mutation):
            print(f"{mutation.label}: FAIL — the mutation pattern was NOT FOUND in {mutation.file}")
            failures += 1
            continue

        red_exit, red_output = run_command(mutation.command)
        red_nodes = failing_nodes(red_output)

        git("checkout", "--", mutation.file)
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
    }
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    (LOG_DIR / "summary.json").write_text(json.dumps(summary, indent=2))
    print("")
    print(f"MUTATION MATRIX: {len(results) - failures}/{len(results)} PASS")
    print(f"summary: {LOG_DIR / 'summary.json'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
