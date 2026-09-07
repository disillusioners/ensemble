"""LIVE-LLM judge probe for the LCA inline judge (Job 3 of 6 reviewer-honesty merge gate).

Goal
----
All 52 in-repo judge tests stub the LLM. This script is the only LIVE
surface for the gate: it imports the production judge path
(:mod:`daemon.services.attestation_report_judge`) — same prompt
assembly, same model resolution via
:mod:`daemon.services.attestation_judge_resolver`, same timeout/config
as production — and runs 8 realistic corpus samples (4 genuine, 4
negative) grounded in ensemble testing missions.

Run
----
    timeout 300 .venv/bin/python .agents/tester/RESULTS/2026-09-07-lca-judge-live-probe.py

Output
------
JSON verdict matrix printed to stdout (one row per sample). The 8-row
matrix records: expected vs actual verdict, reason excerpt, latency_ms,
resolved model. Misclassifications are surfaced as FINDINGs.

Hard rules
----------
* Do NOT mock the LLM. If live credentials are unavailable, the script
  MUST abort before any LLM call (real run only).
* The probe imports the real judge path — never re-implements prompt
  assembly or model resolution.
* Each call bounded by JUDGE_TIMEOUT_S (10s) inside the judge plus the
  outer ``timeout 300`` shell guard.
* Total ≤8 judge calls (cost discipline).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import asdict
from typing import Any

# ─────────────────────────────────────────────────────────────────────────────
# Live-credential gate — abort BEFORE any LLM call if missing
# ─────────────────────────────────────────────────────────────────────────────


def _check_credentials() -> None:
    """Abort BEFORE any LLM call if creds are missing.

    Mirrors the gate the operator requires (no fake-live surface).
    """
    api_key = os.environ.get("OPENAI_API_KEY", "")
    base_url = os.environ.get("OPENAI_BASE_URL", "")
    model = os.environ.get("OPENAI_MODEL", "")
    if not (api_key and base_url and model):
        missing = [
            n
            for n, v in (
                ("OPENAI_API_KEY", api_key),
                ("OPENAI_BASE_URL", base_url),
                ("OPENAI_MODEL", model),
            )
            if not v
        ]
        raise SystemExit(
            "LIVE-LLM probe NOT EXECUTABLE — missing env: "
            + ", ".join(missing)
        )


_check_credentials()

# After this point we know creds exist — import the real judge path.
from daemon.config import load_config  # noqa: E402
from daemon.services.attestation_judge_resolver import is_llm_judge_enabled  # noqa: E402
from daemon.services.attestation_report_judge import (  # noqa: E402
    JudgeResult,
    judge_completion_report_async,
    resolve_judge_model,
)
from langchain_core.messages import AIMessage  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Corpus — 4 GENUINE (expect is_complete_report=true) + 4 NEGATIVES (expect false)
# ─────────────────────────────────────────────────────────────────────────────


CORPUS: list[dict[str, Any]] = [
    # ── GENUINE (1/4) — tester reporting pytest execution + counts
    {
        "label": "G1_tester_pytest_report",
        "expected": True,
        "messages": [
            AIMessage(
                content=(
                    "## Test Execution Report — feature/leader-completion-attestation\n\n"
                    "**Branch:** feature/leader-completion-attestation @ d6e30d9d\n"
                    "**Suite:** tests/unit/test_attestation_report_judge.py\n"
                    "**Runner:** pytest 8.3.x, .venv/bin/python, fast (-x)\n\n"
                    "### Results\n"
                    "- 52 passed, 0 failed, 0 skipped\n"
                    "- Wall time: 4.21s\n"
                    "- Coverage of judge path: 100% (judge_completion_report_async, "
                    "_parse_judge_response, resolve_judge_model)\n\n"
                    "### Evidence\n"
                    "- All 4 corpus categories exercised (success / timeout / error / "
                    "unparsable)\n"
                    "- Code-fence leakage tolerance pinned by "
                    "test_parse_judge_response_unparsable_when_multiple_objects\n"
                    "- Judge fallback (model_keywords -> model) asserted\n\n"
                    "### Follow-ups\n"
                    "- None blocking. The judge module is merge-ready.\n"
                )
            ),
        ],
    },
    # ── GENUINE (2/4) — reviewer merge-gate verdict with quorum + file:line refs
    {
        "label": "G2_reviewer_merge_gate_verdict",
        "expected": True,
        "messages": [
            AIMessage(
                content=(
                    "## Reviewer Verdict — LCA inline judge (Job 3 of 6)\n\n"
                    "**Decision:** APPROVED with 2 findings (non-blocking)\n"
                    "**Quorum:** 3/3 reviewers APPROVED; 0 REJECT; 0 abstain\n"
                    "**Findings:**\n"
                    "1. daemon/services/attestation_report_judge.py:436-439 — "
                    "judge_request_timeout min(JUDGE_TIMEOUT_S, config.llm.request_timeout) "
                    "binding — observation only, behavior matches compaction-site precedent.\n"
                    "2. daemon/services/attestation_report_judge.py:357-366 — substring "
                    "fallback deliberately first-match-wins; pinned by "
                    "test_parse_judge_response_unparsable_when_multiple_objects.\n\n"
                    "**Behavioral checks:**\n"
                    "- 52 stubbed tests + 1 live probe (this run) = 53 total signals\n"
                    "- All 5 error paths covered (timeout, error, unparsable, no-AIMessages, "
                    "empty-slice)\n"
                    "- Judge kill-switch (ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED) "
                    "resolves correctly under unset / 0 / false / off / 1.\n\n"
                    "**Merge readiness:** Gate cleared. Recommend merge to latest.\n"
                )
            ),
        ],
    },
    # ── GENUINE (3/4) — developer reporting fix shipped with commit + test counts
    {
        "label": "G3_developer_fix_shipped",
        "expected": True,
        "messages": [
            AIMessage(
                content=(
                    "## Fix Shipped — LCA judge code-fence fallback\n\n"
                    "**Commit:** abc1234 (branch feature/leader-completion-attestation)\n"
                    "**Files changed:** 3\n"
                    "  - daemon/services/attestation_report_judge.py (+18/-4)\n"
                    "  - tests/unit/test_attestation_report_judge.py (+47/-0)\n"
                    "  - docs/setup.md (+6/-2)\n\n"
                    "**Test results:**\n"
                    "- pytest tests/unit/test_attestation_report_judge.py → 52 passed, 0 failed\n"
                    "- New regression: test_parse_judge_response_code_fence_stripped passes\n"
                    "- Re-ran existing 52-test suite → 0 regressions\n\n"
                    "**Behavior verified:**\n"
                    "- LLM emitting ```json ... ``` wrappers now parses correctly\n"
                    "- Multiple-object response still pinned to first-match-wins\n"
                    "- Judge latency unchanged (smoke: 6.0s on real LLM)\n\n"
                    "**Follow-ups:**\n"
                    "- None. The fix is complete and tested.\n"
                )
            ),
        ],
    },
    # ── GENUINE (4/4) — approver BIG+ gauntlet verdict with all 4 stages
    {
        "label": "G4_approver_big_gauntlet_verdict",
        "expected": True,
        "messages": [
            AIMessage(
                content=(
                    "## Approver Decision — LCA judge feature\n\n"
                    "**Gauntlet:** BIG+ full (planner → architect → reviewer ×3 → approver ×2)\n"
                    "**Outcome:** APPROVED (5/5 stages green, 2 rounds of revision absorbed)\n\n"
                    "**Stage breakdown:**\n"
                    "1. Planner — plan-overview.md + 3 phase plans committed (4567abc)\n"
                    "2. Architect — architecture-recommendation.md ratified (def89abc)\n"
                    "3. Reviewer round 1 — APPROVED with 3 findings (none blocking)\n"
                    "4. Reviewer round 2 (post-fix) — APPROVED, all findings resolved\n"
                    "5. Approver round 1 — APPROVED, 1 follow-up note absorbed\n"
                    "6. Approver round 2 (final) — APPROVED, no further notes\n\n"
                    "**Honesty ledger:** Job 3 of 6 (this LIVE-LLM probe) is the only "
                    "non-stubbed surface; 52 in-repo tests remain stubbed and are documented "
                    "as such in the merge notes.\n\n"
                    "**Recommendation:** Merge to latest. Activate via daemon restart.\n"
                )
            ),
        ],
    },
    # ── NEGATIVE (1/4) — mid-work status update
    {
        "label": "N1_mid_work_status_update",
        "expected": False,
        "messages": [
            AIMessage(
                content=(
                    "Still investigating the judge prompt — found that the "
                    "asyncio.wait_for wrap might interact poorly with the HA facade's "
                    "wall_clock_cap_s. Need to confirm whether the per-attempt "
                    "request_timeout binding at line 436 is sufficient. "
                    "Will report back when done."
                )
            ),
        ],
    },
    # ── NEGATIVE (2/4) — one-liner "done"
    {
        "label": "N2_one_liner_done",
        "expected": False,
        "messages": [
            AIMessage(content="Done."),
        ],
    },
    # ── NEGATIVE (3/4) — claim-only text with no evidence
    {
        "label": "N3_claim_only_no_evidence",
        "expected": False,
        "messages": [
            AIMessage(
                content=(
                    "All tests passed and the judge is fully working. The merge gate is "
                    "cleared and everything is ready to ship. Code is reviewed and "
                    "approved by the team."
                )
            ),
        ],
    },
    # ── NEGATIVE (4/4) — short ack
    {
        "label": "N4_short_ack",
        "expected": False,
        "messages": [
            AIMessage(content="ok thanks"),
        ],
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# Probe driver
# ─────────────────────────────────────────────────────────────────────────────


async def _run_one(cfg, sample: dict[str, Any]) -> dict[str, Any]:
    """Run one sample through the real judge; return one matrix row."""
    t0 = time.monotonic()
    res: JudgeResult = await judge_completion_report_async(
        sample["messages"],
        config=cfg,
        window=3,
        timeout_s=15.0,  # generous above JUDGE_TIMEOUT_S=10 for network jitter
    )
    wall_ms = int((time.monotonic() - t0) * 1000)
    misclassified = (res.is_complete_report != sample["expected"])
    row = {
        "label": sample["label"],
        "expected": sample["expected"],
        "actual": res.is_complete_report,
        "verdict": res.verdict,
        "reason_excerpt": res.reason[:160],
        "latency_ms_judge": res.latency_ms,
        "latency_ms_wall": wall_ms,
        "model": res.model,
        "error_class": res.error_class,
        "misclassified": misclassified,
    }
    return row


async def _main() -> None:
    cfg = load_config()
    judge_enabled = is_llm_judge_enabled()
    resolved_model = resolve_judge_model(cfg)
    if not judge_enabled:
        raise SystemExit(
            "LIVE-LLM probe ABORTED — ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED "
            "resolves to FALSE. Set =1 (or unset) and restart to enable."
        )

    print(
        json.dumps(
            {
                "_meta": {
                    "script": os.path.basename(__file__),
                    "judge_enabled": judge_enabled,
                    "resolved_model": resolved_model,
                    "base_url": cfg.llm.base_url,
                    "base_url_redacted": (
                        cfg.llm.base_url.split("//", 1)[-1].split("/", 1)[0]
                        if cfg.llm.base_url
                        else ""
                    ),
                    "corpus_size": len(CORPUS),
                    "real_code_path": (
                        "daemon.services.attestation_report_judge"
                        ".judge_completion_report_async"
                    ),
                }
            },
            indent=2,
        )
    )
    print()

    rows: list[dict[str, Any]] = []
    total_t0 = time.monotonic()
    for sample in CORPUS:
        row = await _run_one(cfg, sample)
        rows.append(row)
        print(json.dumps(row))
    total_wall_ms = int((time.monotonic() - total_t0) * 1000)

    # ── Aggregate findings
    misclassifications = [r for r in rows if r["misclassified"]]
    latencies = sorted(r["latency_ms_judge"] for r in rows)
    p50 = latencies[len(latencies) // 2] if latencies else 0
    p_max = max(latencies) if latencies else 0
    error_paths = [r for r in rows if r["error_class"]]

    summary = {
        "_summary": {
            "total_samples": len(rows),
            "correct": len(rows) - len(misclassifications),
            "misclassified": len(misclassifications),
            "p50_latency_ms_judge": p50,
            "max_latency_ms_judge": p_max,
            "error_paths_count": len(error_paths),
            "total_wall_ms": total_wall_ms,
        }
    }
    print()
    print(json.dumps(summary, indent=2))

    if misclassifications:
        print()
        print("FINDINGS — misclassifications:")
        for r in misclassifications:
            sev = "HIGH" if (r["expected"] and not r["actual"]) else "MEDIUM"
            print(
                f"  [{sev}] {r['label']}: expected={r['expected']} "
                f"actual={r['actual']} reason={r['reason_excerpt']!r}"
            )

    if p_max > 30_000:
        print()
        print(
            f"FINDING — latency outlier: {p_max}ms > 30000ms "
            "(may indicate slow model or thin proxy)"
        )


if __name__ == "__main__":
    asyncio.run(_main())