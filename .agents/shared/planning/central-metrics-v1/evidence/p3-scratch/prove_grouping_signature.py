#!/usr/bin/env python3
"""The GROUPING proof for the whole-tree diff ids.

Three legs, all on the SAME trees the sweep ran against
(BASE 941cb6321 · HEAD 644c2bfaa — the P3 close-out tree, code-identical
to the sweep's HEAD tree e408cccab):

LEG 1 — FILE LEVEL.  For every file that owns a diff id, the WHOLE file
is run in isolation, three times per side.  If the file's red set is
IDENTICAL on both sides, the file's behaviour is identical between the
two trees and the sweep-level delta cannot be a behavioural difference.

LEG 1b — THE DIFF SET AS ONE BATCH.  The whole 69-id diff set is run as a
single pytest session, three times per side.  This is the SHARPEST
test: the same node set, the same session shape, both sides.

LEG 2 — THE SHARD DETERMINISM.  The sweep ran each shard with xdist
INSIDE it, so the node order inside a shard varies with worker timing.
If the SAME shard, on the SAME tree, yields DIFFERENT red sets across
two runs, then the shard-level digest is not a reproducible artefact and
the whole-tree comparison at shard granularity is inherently noisy — the
sweep-level base-only/head-only delta is a composition artefact, not a
property of the code.

That is the PROOF (not the assumption) that the 33 head-only whole-file
groupings are the xdist worker-crash/order signature.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PY = "/home/nea/ensemble-src/.venv/bin/python"
BASE_WT = Path("/home/nea/ensemble-src-wt-central-metrics-v1-base")
HEAD_WT = Path("/home/nea/ensemble-src-wt-central-metrics-v1-head")
EVIDENCE = Path(
    "/home/nea/ensemble-src-wt-central-metrics-v1/.agents/shared/planning/"
    "central-metrics-v1/evidence"
)
SWEEP = EVIDENCE / "p3-sweep-sharded" / "20261102T061340Z"
OUT = EVIDENCE / "p3-adjudication"
VERDICT_DIR = Path("/tmp/p3r/verdict")

SCRUB_VARS = [
    "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB", "POSTGRES_USER",
    "POSTGRES_PASSWORD", "POSTGRES_URL", "DATABASE_URL",
    "PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD", "PGSERVICE",
    "PGOPTIONS",
    "ENSEMBLE_SELF_ENV", "ENSEMBLE_UPGRADE_LIVE", "ENSEMBLE_UPGRADE_SCRIPTS_DIR",
    "ENSEMBLE_RESTART_UNIT",
]


def fenced_env(fake_home: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in SCRUB_VARS}
    env["HOME"] = str(fake_home)
    env["OPENAI_BASE_URL"] = "http://127.0.0.1:9/v1"
    env["OPENAI_BASE_URL_BACKUP"] = "http://127.0.0.1:9/v1"
    env["OPENAI_REQUEST_TIMEOUT"] = "5"
    env["QUEUE_LLM_RETRY_TRANSIENT_ATTEMPTS"] = "1"
    env["QUEUE_LLM_RETRY_TIMEOUT_ATTEMPTS"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def make_fake_home() -> Path:
    fake = Path(tempfile.mkdtemp(prefix="p3-grouping-home-")).resolve()
    real = os.environ.get("HOME", "")
    if real and fake == Path(real).resolve():
        raise SystemExit("ESCAPE GUARD: the FAKE_HOME resolved to the real HOME")
    if not str(fake).startswith(tempfile.gettempdir()):
        raise SystemExit("ESCAPE GUARD: the FAKE_HOME is outside the temp dir")
    return fake


def reds_of(output: str) -> set[str]:
    return {
        line.split(" ", 1)[1]
        for line in output.splitlines()
        if line.startswith(("FAILED ", "ERROR "))
    }


def run_nodes(worktree: Path, nodes: list[str], env: dict, xproc: int | None):
    cmd = [
        PY, "-m", "pytest", *nodes,
        "-q", "--tb=no", "-rEf", "-p", "no:cacheprovider", "--timeout=120",
    ]
    if xproc:
        cmd += ["-n", str(xproc)]
    completed = subprocess.run(
        cmd, cwd=str(worktree), env=env, capture_output=True, text=True,
        timeout=2400, check=False,
    )
    summary = ""
    for line in reversed(completed.stdout.splitlines()):
        if " passed" in line or " failed" in line or " error" in line:
            summary = line.strip()
            break
    return completed.returncode, reds_of(completed.stdout), summary


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    env = fenced_env(make_fake_home())

    base_only = [
        l for l in (VERDICT_DIR / "bareid.base-only.txt").read_text().splitlines() if l.strip()
    ]
    head_only = [
        l for l in (VERDICT_DIR / "bareid.head-only.txt").read_text().splitlines() if l.strip()
    ]
    diff_ids = base_only + head_only
    files = sorted({node.split("::", 1)[0] for node in diff_ids})

    lines = ["leg\tscope\tside\trun\trc\tred_count\tsummary\tdetail"]

    # ── LEG 1 — the file level ────────────────────────────────────────────
    print(f"=== LEG 1 — the FILE level ({len(files)} files, 3 runs per side) ===")
    for path in files:
        for side, worktree in (("BASE", BASE_WT), ("HEAD", HEAD_WT)):
            for run in range(1, 4):
                started = time.monotonic()
                rc, reds, summary = run_nodes(worktree, [path], env, None)
                duration = round(time.monotonic() - started, 1)
                print(f"  {side} {path} run{run}: rc={rc} reds={len(reds)} ({duration}s)")
                lines.append(
                    f"file\t{path}\t{side}\t{run}\t{rc}\t{len(reds)}\t{summary}\t"
                    + "|".join(sorted(reds))
                )
    (OUT / "file-level-matrix.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ── LEG 1b — the whole diff set as ONE batch ──────────────────────────
    print("")
    print(f"=== LEG 1b — the whole diff set as ONE batch ({len(diff_ids)} ids, 3 per side) ===")
    batch_digests: dict[str, list[frozenset]] = {"BASE": [], "HEAD": []}
    for side, worktree in (("BASE", BASE_WT), ("HEAD", HEAD_WT)):
        for run in range(1, 4):
            started = time.monotonic()
            rc, reds, summary = run_nodes(worktree, diff_ids, env, None)
            duration = round(time.monotonic() - started, 1)
            print(f"  {side} batch run{run}: rc={rc} reds={len(reds)} ({duration}s)")
            batch_digests[side].append(frozenset(reds))
            lines.append(
                f"batch\tALL-DIFF-IDS\t{side}\t{run}\t{rc}\t{len(reds)}\t{summary}\t"
                + "|".join(sorted(reds))
            )
    (OUT / "batch-matrix.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    base_union = frozenset().union(*batch_digests["BASE"]) if batch_digests["BASE"] else frozenset()
    head_union = frozenset().union(*batch_digests["HEAD"]) if batch_digests["HEAD"] else frozenset()
    print(f"  the BASE union red set:  {len(base_union)} ids")
    print(f"  the HEAD union red set:  {len(head_union)} ids")
    print(f"  the symmetric difference: {len(base_union ^ head_union)} ids")
    if base_union == head_union:
        print("  -> the batch red set is IDENTICAL on both sides")
    else:
        print("  -> the batch red set DIFFERS:")
        for node in sorted(base_union - head_union):
            print(f"     base-only: {node}")
        for node in sorted(head_union - base_union):
            print(f"     head-only: {node}")

    # ── LEG 2 — the shard determinism ─────────────────────────────────────
    print("")
    print("=== LEG 2 — the SHARD determinism (the SAME shard, the SAME tree, twice) ===")
    shard_lines = ["shard\tside\trun\trc\tred_count\tred_ids"]
    shard_results: dict[str, list[frozenset]] = {}
    for shard_index in (2, 5):
        shard_file = Path(f"/tmp/p3r/shards/BASE.shard-{shard_index:03d}.txt")
        if not shard_file.exists():
            print(f"  shard-{shard_index:03d}: no shard node list — skipped")
            continue
        nodes = [l for l in shard_file.read_text().splitlines() if l.strip()]
        digests: list[frozenset] = []
        for run in (1, 2):
            started = time.monotonic()
            rc, reds, summary = run_nodes(BASE_WT, nodes, env, 12)
            duration = round(time.monotonic() - started, 1)
            print(f"  BASE/shard-{shard_index:03d} run{run}: rc={rc} reds={len(reds)} ({duration}s)")
            digests.append(frozenset(reds))
            shard_lines.append(
                f"shard-{shard_index:03d}\tBASE\t{run}\t{rc}\t{len(reds)}\t"
                + "|".join(sorted(reds))
            )
        shard_results[f"shard-{shard_index:03d}"] = digests
        if len(digests) == 2:
            if digests[0] != digests[1]:
                only1 = digests[0] - digests[1]
                only2 = digests[1] - digests[0]
                print(
                    "    -> NON-DETERMINISTIC: the SAME shard on the SAME tree "
                    f"yielded different red sets ({len(only1)} only in run 1, "
                    f"{len(only2)} only in run 2)"
                )
                for node in sorted(only1)[:10]:
                    print(f"       run1-only: {node}")
                for node in sorted(only2)[:10]:
                    print(f"       run2-only: {node}")
            else:
                print("    -> deterministic on this shard")
    (OUT / "shard-determinism-matrix.tsv").write_text(
        "\n".join(shard_lines) + "\n", encoding="utf-8"
    )
    print("")
    print(f"the file-level matrix: {OUT / 'file-level-matrix.tsv'}")
    print(f"the batch matrix:      {OUT / 'batch-matrix.tsv'}")
    print(f"the shard matrix:      {OUT / 'shard-determinism-matrix.tsv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
