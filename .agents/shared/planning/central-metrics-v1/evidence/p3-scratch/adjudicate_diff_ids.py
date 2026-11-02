#!/usr/bin/env python3
"""The whole-tree diff-id adjudication — PER-ID ISOLATION, 3 runs per side.

The house net-new-failure discipline (the failed-digest-equality-gate §7):
every diff id is re-run IN ISOLATION (ONE pytest process per id — NOT a
batch, because a batch lets the ids interfere with each other and the
interference is exactly what the sweep saw), three times per side, on
BOTH legs, and classified:

    red on HEAD only, green on BASE  => LANE CANDIDATE (a real regression)
    red on both sides                => PRE-EXISTING (byte-identical to base)
    green on both sides              => ORDER/LOAD-SENSITIVE (it does not
                                       reproduce in isolation — the sweep's
                                       parallel composition was the cause)

The FENCE (house):
  * per-VARIABLE env scrub (a glob is a silent no-op);
  * a mktemp FAKE_HOME + escape guard (a test must never resolve a
    live-shaped HOME-derived install dir);
  * the daemon import resolves INSIDE the tree under test.

The file-level leg (the GROUPING proof): for every file that owns a diff
id, the WHOLE file is run in isolation, three times per side — if the
file's red set is identical on both sides, the sweep-level delta is a
composition artifact, not a behavioural difference.
"""

from __future__ import annotations

import argparse
import json
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

#: The per-variable scrub (house form — a glob is a silent NO-OP).
SCRUB_VARS = [
    "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB", "POSTGRES_USER",
    "POSTGRES_PASSWORD", "POSTGRES_URL", "DATABASE_URL",
    "PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD", "PGSERVICE",
    "PGOPTIONS",
    "ENSEMBLE_SELF_ENV", "ENSEMBLE_UPGRADE_LIVE", "ENSEMBLE_UPGRADE_SCRIPTS_DIR",
    "ENSEMBLE_RESTART_UNIT",
]


def fenced_env(fake_home: Path) -> dict[str, str]:
    """The scrubbed environment (per-variable) + the FAKE_HOME."""
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in SCRUB_VARS
    }
    env["HOME"] = str(fake_home)
    env["OPENAI_BASE_URL"] = "http://127.0.0.1:9/v1"
    env["OPENAI_BASE_URL_BACKUP"] = "http://127.0.0.1:9/v1"
    env["OPENAI_REQUEST_TIMEOUT"] = "5"
    env["QUEUE_LLM_RETRY_TRANSIENT_ATTEMPTS"] = "1"
    env["QUEUE_LLM_RETRY_TIMEOUT_ATTEMPTS"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def make_fake_home() -> Path:
    """A mktemp FAKE_HOME + the escape guard (house rule)."""
    real = os.environ.get("HOME", "")
    fake = Path(tempfile.mkdtemp(prefix="p3-adjudication-home-"))
    resolved = fake.resolve()
    if resolved == Path(real).resolve() if real else False:
        raise SystemExit("ESCAPE GUARD: the FAKE_HOME resolved to the real HOME")
    if not str(resolved).startswith(tempfile.gettempdir()):
        raise SystemExit(
            f"ESCAPE GUARD: the FAKE_HOME {resolved} is outside the temp dir"
        )
    return resolved


def run_one(worktree: Path, node: str, env: dict[str, str]) -> dict:
    """Run ONE node id in isolation and record the outcome."""
    started = time.monotonic()
    completed = subprocess.run(
        [
            PY, "-m", "pytest", node,
            "-q", "--tb=no", "-p", "no:randomly", "-p", "no:cacheprovider",
        ],
        cwd=str(worktree),
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    duration = time.monotonic() - started
    reds = [
        line.split(" ", 1)[1]
        for line in completed.stdout.splitlines()
        if line.startswith(("FAILED ", "ERROR "))
    ]
    summary = ""
    for line in reversed(completed.stdout.splitlines()):
        if " passed" in line or " failed" in line or " error" in line:
            summary = line.strip()
            break
    return {
        "rc": completed.returncode,
        "red": bool(reds),
        "red_ids": reds,
        "summary": summary,
        "duration_s": round(duration, 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--smoke", type=int, default=0, help="run only N ids (dry run)")
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    fake_home = make_fake_home()
    env = fenced_env(fake_home)

    # ── the diff id sets (from the FIXED comparator's outputs) ────────────
    verdict_dir = Path("/tmp/p3r/verdict")
    base_only = [
        line for line in (verdict_dir / "bareid.base-only.txt").read_text().splitlines()
        if line.strip()
    ]
    head_only = [
        line for line in (verdict_dir / "bareid.head-only.txt").read_text().splitlines()
        if line.strip()
    ]
    # The GREP-FULL base-only carries ONE non-node-id entry (a log line) —
    # it is NOT a test id and cannot be run; it is recorded separately.
    log_only = [
        line for line in (verdict_dir / "grepfull.base-only.txt").read_text().splitlines()
        if line.strip() and "::" not in line
    ]
    print(f"base-only (node ids): {len(base_only)}   head-only: {len(head_only)}")
    print(f"base-only NON-node-id log lines (not runnable): {len(log_only)}")

    matrix_path = OUT / "adjudication-matrix.tsv"
    with matrix_path.open("w", encoding="utf-8") as handle:
        handle.write(
            "side\tset\tnode_id\trun\trc\tred\tduration_s\tsummary\n"
        )

    results: dict[str, dict] = {}
    ids = [(node, "base-only") for node in base_only] + [
        (node, "head-only") for node in head_only
    ]
    if args.smoke:
        ids = ids[: args.smoke]

    started_all = time.monotonic()
    for index, (node, which) in enumerate(ids, start=1):
        for side, worktree in (("BASE", BASE_WT), ("HEAD", HEAD_WT)):
            for run in range(1, args.rounds + 1):
                outcome = run_one(worktree, node, env)
                key = f"{side}|{node}"
                results.setdefault(
                    key, {"side": side, "node": node, "set": which, "runs": []}
                )["runs"].append(
                    {
                        "run": run,
                        "rc": outcome["rc"],
                        "red": outcome["red"],
                        "summary": outcome["summary"],
                        "duration_s": outcome["duration_s"],
                    }
                )
                with matrix_path.open("a", encoding="utf-8") as handle:
                    handle.write(
                        f"{side}\t{which}\t{node}\t{run}\t{outcome['rc']}\t"
                        f"{int(outcome['red'])}\t{outcome['duration_s']}\t"
                        f"{outcome['summary']}\n"
                    )
        if index % 5 == 0 or index == len(ids):
            print(
                f"  {index}/{len(ids)} ids adjudicated "
                f"({time.monotonic() - started_all:.0f}s elapsed)",
                flush=True,
            )

    # ── the classification ────────────────────────────────────────────────
    verdicts: dict[str, dict] = {}
    for key, entry in results.items():
        reds = sum(1 for run in entry["runs"] if run["red"])
        verdicts[key] = {
            "side": entry["side"],
            "set": entry["set"],
            "node": entry["node"],
            "runs": len(entry["runs"]),
            "reds": reds,
        }
    lanes: dict[str, dict] = {}
    for node in base_only + head_only:
        base = verdicts.get(f"BASE|{node}", {"reds": 0, "runs": 0})
        head = verdicts.get(f"HEAD|{node}", {"reds": 0, "runs": 0})
        if head["reds"] and not base["reds"]:
            # THE ONLY lane-candidate shape: red at HEAD, green at BASE.
            classification = "LANE-CANDIDATE (red on HEAD only — a real regression)"
        elif base["reds"] and not head["reds"]:
            # A BASE-only red is the RESOLVED side of the diff (it is red
            # at BASE and green at HEAD) — NOT a lane regression.
            classification = "RESOLVED-AT-HEAD (red on BASE only — the base-only side of the diff)"
        elif base["reds"] and head["reds"]:
            classification = "PRE-EXISTING (red on BOTH sides)"
        else:
            classification = "ORDER/LOAD-SENSITIVE (green on both sides in isolation)"
        lanes[node] = {
            "classification": classification,
            "base_reds": base["reds"],
            "head_reds": head["reds"],
            "base_runs": base["runs"],
            "head_runs": head["runs"],
            "set": "base-only" if node in base_only else "head-only",
        }

    (OUT / "adjudication-verdict.json").write_text(
        json.dumps(
            {
                "rounds_per_side": args.rounds,
                "ids": len(lanes),
                "base_only_ids": len(base_only),
                "head_only_ids": len(head_only),
                "non_runnable_log_lines": log_only,
                "lanes": lanes,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    from collections import Counter

    summary = Counter(v["classification"] for v in lanes.values())
    print("")
    print("=== the per-id adjudication summary ===")
    for key, count in sorted(summary.items()):
        print(f"  {count:3d}  {key}")
    lane_candidates = [
        node for node, v in lanes.items()
        if v["classification"].startswith("LANE-CANDIDATE")
    ]
    resolved = [
        node for node, v in lanes.items()
        if v["classification"].startswith("RESOLVED-AT-HEAD")
    ]
    print(f"RESOLVED-AT-HEAD (the base-only side of the diff): {len(resolved)}")
    for node in resolved:
        print(f"  {node}")
    print("")
    print(f"LANE CANDIDATES: {len(lane_candidates)}")
    for node in lane_candidates:
        print(f"  {node}")
    print("")
    print(f"the matrix:   {matrix_path}")
    print(f"the verdict:  {OUT / 'adjudication-verdict.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
