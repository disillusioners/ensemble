#!/usr/bin/env python3
"""W1 Pollution Probe — wc-wake phase-1 gate, resolver matrix + W1
pollution vectors.

Two sections:

  SECTION 1 — Resolver matrix (the W2 truthy/falsy spelling contract):
    For env ``ENSEMBLE_WC_WAKE_ENQUEUE`` in
    {unset, "", "0", "1", "true", "false", "off", "on", "garbage",
     "yes", "no"} → drive ``_resolve_wc_wake_enqueue_enabled()`` and
    capture ON/OFF resolution + WARN emission (for blank / unknown
    values per the resolver's documented contract at
    ``daemon/services/instance_messaging.py:108-155``). Each row is
    compared against the encoded truth table; any mismatch is a
    REGRESSION of W2.

    Truth table (READ from the resolver code at HEAD):

      unset / "0" / "" / "false" / "no" / "off" → OFF
      "1" / "true" / "yes" / "on"              → ON
      "garbage" / any other non-blank unknown  → OFF + WARN

  SECTION 2 — W1 pollution vectors (the council repro):
    The two vectors the W1 council identified during the 2026-08-30
    pre-flip batch:

      Vector A — flag-state leaking across tests in one process
        (council repro: ``assert 200 == 202`` on a flag-implicit test
        after a flag-set test ran earlier in the same pytest process).

      Vector B — module-identity pollution across files (file-level
        ``sys.modules`` mutation by one file leaving the daemon module
        pointing at a stale ``_WC_WAKE_ENQUEUE_ENABLED`` value).

    Both vectors manifest when running flag-set + flag-implicit tests
    in shared pytest processes with different orderings. We probe by:

      * Run A: ``pytest tests/unit/services/test_wc_wake_flag_resolver.py``
        (flag-implicit → flag-set) in isolation.
      * Run B: ``pytest tests/unit/services/test_wc_wake_flag_resolver.py
        tests/unit/tools/test_instance_tools.py`` in one process,
        resolver-first order.
      * Run C: same as B, instance-tools-first order.
      * Run D: same as B, both files, isolation markers respected.

    Assert: every Run produces identical PASS results (same test
    counts). Any diff is a W1 pollution regression.

TEST-ENV ONLY. No production code changes, no daemon boot, no ports.

Output: a single JSON document on stdout with both sections. The
shell wrapper aggregates, prints the truth table verbatim, and the W1
section per-run summaries.

Exit codes:
  0   PASS (every truth-table row matches AND every W1 vector is identical)
  1   FAIL (any mismatch / vector divergence)
  124 internal timeout (signal.alarm)
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import subprocess
import sys


# ── Layer 2 inner guard ─────────────────────────────────────────────────────

INTERNAL_TIMEOUT_S = 180


class TimeoutError_(Exception):
    pass


def _alarm_handler(signum, frame):
    raise TimeoutError_(f"internal timeout after {INTERNAL_TIMEOUT_S}s")


# ── SECTION 1: resolver matrix ──────────────────────────────────────────────

# Truth table per the resolver code at HEAD (read from
# ``daemon/services/instance_messaging.py:108-155``). Each row is
# (raw_value, expected_on_off, expect_warn).
#   * ``raw=None``       → env left unset (``os.environ.get(..., "0")``).
#   * ``raw=""``         → env set to empty string (W2 instant-revert).
#   * truthy: 1/true/yes/on → True (no warn).
#   * falsy:  0/false/no/off → False (no warn).
#   * unknown non-blank (garbage, "maybe") → False + WARN (W2 contract).
RESOLVER_MATRIX = [
    ("unset",    None,      False, False),
    ("blank",    "",        False, True),    # W2: blank = WARN
    ("zero",     "0",       False, False),
    ("false",    "false",   False, False),
    ("no",       "no",      False, False),
    ("off",      "off",     False, False),
    ("one",      "1",       True,  False),
    ("true",     "true",    True,  False),
    ("yes",      "yes",     True,  False),
    ("on",       "on",      True,  False),
    ("garbage",  "garbage", False, True),    # W2: unknown = WARN
    ("maybe",    "maybe",   False, True),    # same class as garbage
]


def resolver_matrix() -> dict:
    """Run each matrix row through the resolver and capture ON/OFF + WARN."""
    # IMPORTANT: import the resolver lazily so we can fully isolate the
    # module-global cache per row. The probe runs as a standalone
    # script — the venv's editable install already makes daemon
    # importable; we don't need PYTHONPATH gymnastics.
    from daemon.services.instance_messaging import (
        _WC_WAKE_ENQUEUE_ENV,
        _reset_wc_wake_enqueue_for_tests,
        _resolve_wc_wake_enqueue_enabled,
    )

    rows = []
    failures = []
    for label, raw, expected_on, expect_warn in RESOLVER_MATRIX:
        # Reset the cache + clear the env, then re-set per row.
        _reset_wc_wake_enqueue_for_tests()
        if raw is None:
            os.environ.pop(_WC_WAKE_ENQUEUE_ENV, None)
        else:
            os.environ[_WC_WAKE_ENQUEUE_ENV] = raw

        # Capture WARNs from the resolver's logger. The resolver only
        # emits a WARN on the unknown path. Use a custom handler.
        captured_warns: list[str] = []
        import logging
        handler = logging.Handler()
        handler.emit = lambda record: captured_warns.append(
            record.getMessage()
        )
        resolver_logger = logging.getLogger(
            "daemon.services.instance_messaging"
        )
        prev_level = resolver_logger.level
        resolver_logger.setLevel(logging.WARNING)
        resolver_logger.addHandler(handler)
        try:
            actual_on = _resolve_wc_wake_enqueue_enabled()
        finally:
            resolver_logger.removeHandler(handler)
            resolver_logger.setLevel(prev_level)

        warn_seen = any(
            _WC_WAKE_ENQUEUE_ENV in msg and "is not a recognized" in msg
            for msg in captured_warns
        )

        row = {
            "label": label,
            "raw": raw,
            "expected_on": expected_on,
            "actual_on": actual_on,
            "expected_warn": expect_warn,
            "actual_warn": warn_seen,
            "pass": actual_on == expected_on and warn_seen == expect_warn,
        }
        rows.append(row)
        if not row["pass"]:
            failures.append(label)

    return {
        "matrix": rows,
        "ok": not failures,
        "failures": failures,
    }


# ── SECTION 2: W1 pollution vectors ─────────────────────────────────────────

# The two files we cross-order. The resolver file mutates the env
# via monkeypatch and calls ``_reset_wc_wake_enqueue_for_tests()`` —
# the module-identity cache is the W1 vector. The instance_tools file
# has the autouse ``_reset_wc_wake_enqueue_flag_cache`` fixture which
# is the COUNTERMEASURE (it resets the cache around every test).
RESOLVER_TEST = "tests/unit/services/test_wc_wake_flag_resolver.py"
INSTANCE_TOOLS_TEST = "tests/unit/tools/test_instance_tools.py"

# Use the SAME pytest invocation shape that triggered the W1 council
# repro: combined process, two files, no cache provider, terse output.
PYTEST_BASE_ARGS = [
    "-q", "-ra", "-p", "no:cacheprovider", "--tb=line",
]


def _run_pytest(collected_args: list[str], cwd: str, log_path: str,
                python_exe: str) -> dict:
    """Run pytest with the given file list; return parsed PASS/FAIL summary.

    Uses the SAME .venv/bin/python that runs this probe to drive
    pytest (so the venv's pytest resolves via ``-m pytest``, avoiding
    the system-wide ``/opt/homebrew/bin/pytest`` if PATH happens to
    list it first — the venv pytest is the project's own).
    """
    cmd = [python_exe, "-m", "pytest"] + PYTEST_BASE_ARGS + collected_args
    print(f"  $ {' '.join(cmd)}", file=sys.stderr, flush=True)
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=150,
        check=False,
    )
    combined = proc.stdout + "\n" + proc.stderr
    with open(log_path, "w") as fh:
        fh.write(combined)

    # Parse the LAST non-empty pytest summary line for ``N passed`` /
    # ``N failed`` style. We capture both the count and the rc.
    summary_line = ""
    for line in reversed(combined.splitlines()):
        s = line.strip()
        if s and ("passed" in s or "failed" in s or "error" in s):
            summary_line = s
            break

    return {
        "cmd": cmd,
        "rc": proc.returncode,
        "summary": summary_line,
        "log": log_path,
    }


def w1_pollution_vectors(repo_root: str, python_exe: str) -> dict:
    """Cross-order the resolver + instance_tools files; assert identical
    PASS results in every ordering.

    Vectors run:
      A. resolver only       — baseline (flag-implicit + flag-set, 15 tests)
      B. resolver + inst     — resolver-first order (combined process)
      C. inst + resolver     — instance-tools-first order (combined process)

    Pass criterion (W1 pollution contract):
      * Every run is rc=0 (no failures, no errors).
      * Run B and Run C produce the SAME ``passed`` count — order-
        independence is the W1 pollution proof. A divergent count
        would indicate module-identity cache leakage across files
        (the council repro: a flag-implicit test failing with
        ``assert 200 == 202`` because a previous file left the
        cache=True).
    """
    runs: dict[str, dict] = {}
    failures: list[str] = []

    runs["A_resolver_only"] = _run_pytest(
        [RESOLVER_TEST],
        cwd=repo_root,
        log_path="/tmp/wc-wake-w1-A.log",
        python_exe=python_exe,
    )

    runs["B_resolver_then_inst"] = _run_pytest(
        [RESOLVER_TEST, INSTANCE_TOOLS_TEST],
        cwd=repo_root,
        log_path="/tmp/wc-wake-w1-B.log",
        python_exe=python_exe,
    )

    runs["C_inst_then_resolver"] = _run_pytest(
        [INSTANCE_TOOLS_TEST, RESOLVER_TEST],
        cwd=repo_root,
        log_path="/tmp/wc-wake-w1-C.log",
        python_exe=python_exe,
    )

    # Every run must be green.
    for name, result in runs.items():
        if result["rc"] != 0:
            failures.append(f"{name}: rc={result['rc']} != 0")

    # Order-independence: B and C must produce identical pass counts.
    m_b = re.search(r"(\d+) passed", runs["B_resolver_then_inst"]["summary"])
    m_c = re.search(r"(\d+) passed", runs["C_inst_then_resolver"]["summary"])
    n_b = int(m_b.group(1)) if m_b else None
    n_c = int(m_c.group(1)) if m_c else None
    if n_b is not None and n_c is not None and n_b != n_c:
        failures.append(
            f"W1 pollution: B passed={n_b} != C passed={n_c} "
            f"(order-dependent counts → module-identity cache leak)"
        )

    return {
        "baseline_summary": runs["A_resolver_only"]["summary"],
        "baseline_passed": (
            int(re.search(r"(\d+) passed", runs["A_resolver_only"]["summary"]).group(1))
            if re.search(r"(\d+) passed", runs["A_resolver_only"]["summary"])
            else None
        ),
        "B_passed": n_b,
        "C_passed": n_c,
        "order_independent": (n_b == n_c),
        "runs": runs,
        "ok": not failures,
        "failures": failures,
    }


# ── Main ────────────────────────────────────────────────────────────────────


def main() -> int:
    signal.signal(signal.SIGALRM, _alarm_handler)
    signal.alarm(INTERNAL_TIMEOUT_S)

    repo_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    # Use the venv's own python to drive subprocess pytest runs (PATH
    # may resolve to /opt/homebrew/bin/pytest first — the venv pytest
    # is the project's own and must win).
    python_exe = os.path.join(repo_root, ".venv", "bin", "python")

    record: dict = {
        "repo_root": repo_root,
        "python_exe": python_exe,
        "sections": {},
        "ok": True,
    }

    try:
        record["sections"]["resolver_matrix"] = resolver_matrix()
        record["sections"]["w1_pollution_vectors"] = w1_pollution_vectors(
            repo_root, python_exe
        )
    except TimeoutError_ as exc:
        record["ok"] = False
        record["error"] = f"TIMEOUT: {exc}"
        print(json.dumps(record, indent=2, default=str))
        return 124
    except Exception as exc:
        record["ok"] = False
        record["error"] = f"{type(exc).__name__}: {exc}"
        print(json.dumps(record, indent=2, default=str))
        return 1

    record["ok"] = (
        record["sections"]["resolver_matrix"]["ok"]
        and record["sections"]["w1_pollution_vectors"]["ok"]
    )

    print(json.dumps(record, indent=2, default=str))
    return 0 if record["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
