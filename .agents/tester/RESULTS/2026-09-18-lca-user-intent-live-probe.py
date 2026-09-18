"""LCA user-intent LIVE-LLM probe (4dfded83 reconstruction).

Bespoke infrastructure probe (no skill loaded). Builds THREE fused
bundles via the REAL :func:`assemble_fused_bundle` from the worktree's
incident-shaped inputs and calls the REAL
:func:`judge_fused_bundle_async` ONCE per bundle (built-in single
retry on unparsable may fire — reported in summary).

Bundles:
  (i)  4dfded83 SHAPE — user Q + leader's real answer as final message +
       delegation present + nothing pending → expect U PRESENT
       (user_message_included=True, u_chars>0) + verdict "complete".
  (ii) INTENT-MISMATCH — same user Q + a formal-looking final report
       that does NOT answer it → U present + verdict "not_complete".
  (iii) U-ABSENT — transcript with no real user message (anchor absent)
       → user_message_included=False, u_chars=0, U section omitted.

Probe budget: ≤1 LLM call per bundle (built-in retry-on-unparsable is
mechanical, not budget). Wall-clock cap per bundle = 240s (the
``timeout_s`` arg is forwarded into the judge). Outer
``subprocess.run(timeout=300)`` wraps the probe.

Crash discipline: any exception is captured + reported; partial
evidence beats none.

Output: stdout JSON at the end + returncode 0 (PASS) / 2 (FAIL).
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from typing import Any

# Unset SSL env vars per the lca2 probe docstring note
for _v in ("SSL_CERT_FILE", "SSL_CERT_DIR"):
    os.environ.pop(_v, None)

# Mirror the precedent probe's imports + load_config
from daemon.config import load_config  # noqa: E402
from daemon.services.attestation_report_judge import (  # noqa: E402
    FusedJudgeResult,
    judge_fused_bundle_async,
)
from daemon.services.attestation_resolver_activation import (  # noqa: E402
    BUNDLE_A_SECTION_MAX,
    BUNDLE_B_SECTION_MAX,
    BUNDLE_C_SECTION_MAX,
    BUNDLE_TOTAL_MAX,
    BUNDLE_U_SECTION_MAX,
    ChildReportCheckEvidence,
    SourceASignals,
    SourceBSignals,
    SourceCSignals,
    assemble_fused_bundle,
)
from daemon.services.attestation_scanner import is_real_user_message  # noqa: E402

from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402


# Reconstructed from prod PG (checkpoint_blobs at step 297, idx=2):
# user question: HumanMessage idx=207 (verbatim 62 chars)
# leader's answer: AIMessage idx=214 (verbatim 3029 chars)
USER_QUESTION_VERBATIM = (
    "what is current service tool description that agents will see?"
)
# Decoded leader's actual answer at idx=214 — the 3029-char report
# that addresses the user's question
LEADER_ANSWER_VERBATIM = """Here's exactly what agents see, pulled verbatim from the code (note: the worktree has since moved to a foreign branch `v0.13.3` @ `a6442bff` — the service files are unchanged from what we shipped, all override wording intact):

## 1. In tool listings (what agents see by default — first docstring line each)

- **`service_start`** — *Start a long-lived service. Survives instance + daemon restart. Use tool_help(\'service_start\') for details.*
- **`service_stop`** — *Stop a service by name. SIGTERM by default; SIGKILL when force=True. Use tool_help(\'service_stop\') for details.*
- **`service_status`** — *Live status of one service (reconciles pid liveness on call). …*
- **`service_list`** — *List every tracked service (any status) ordered by created_at DESC. Reconciles pid liveness inline. …*
- **`service_logs`** — *Tail the log file for a service. No reaping; stdio is owned by the OS. …*

## 2. Category doc — `tool_help(category="service")`

```text
Long-lived detached processes (dev servers, databases, watchers) that
survive instance lifecycle and daemon restart. NOT an OS service —
no launchd / systemd / launchctl integration; the daemon simply
tracks the process and reaps it on explicit service_stop.

When to use service_* vs proc_*:
- proc_*: instance-scoped background processes (killed on
  instance cleanup); tracked in-memory; per-instance cap of 10.
- service_*: daemon-scoped detached processes (survives instance
  termination AND daemon restart); tracked in the
  service_tracking DB table; daemon-global cap of 10.
```

## 3. Full docs — `tool_help("service_<x>")`, on demand

Each of the 5 tools carries a detailed `_full_doc_` (60–80 lines each; full verbatim text in the wanderer report above). Highlights agents learn there:

- **`service_start`**: argv array only (no shell), name regex `^[a-zA-Z0-9_-]+$`, `cwd` omitted → inherits daemon cwd, all error shapes (`cap_exceeded`, `name_in_use`, `spawn_failed`, `disabled`), setsid/pgid semantics, PID-reuse defense, the F15 grandchild-setsid escape warning, `exit_code: int | None` semantics
- **`service_stop`**: full F1 ownership re-verify chain (`pid_dead`, `pid_recycled`, `pid_recycled_during_grace`, `pid_recycled_pre_kill`), async grace poll, idempotency, **cross-instance semantics** (name-keyed, daemon-global, no `started_by` gate — any agent that knows the name can stop it)
- **`service_status` / `service_list`**: inline liveness reconciliation, zombie-excluding liveness check
- **`service_logs`**: reads `log_path` from the row only, 10 MB OOM-safe tail, never returns a fake-empty on disabled

One wording nit I noticed while reviewing: `service_stop`\'s cross-instance block still says the privilege is "gated via the per-agent meta-grant IFF (bash OR proc → service in tools.allow)" — that was the pre-override framing and is now slightly stale (post-override, default-universe agents get it without a meta entry). Functionally harmless (the kill-switch mention is accurate), but if you want I can dispatch a one-line doc-truth fix."""


# A formal-looking non-answer for bundle (ii) — answer-shape without content
INTENT_MISMATCH_ANSWER_VERBATIM = """## Service tool description — final report

All delegated work has completed and the merged artifacts have been recorded in the project history. The mission is complete.

### Summary of work performed

A new service tool category has been added to the agents-ensemble daemon. Implementation was carried out across the registry, the per-instance tool factories, the help/discovery layer, and the corresponding tests. All five tools in the category are present, registered, and reachable via the standard tool-help surface.

### Test + verification posture

The verifier child reported PASS-WITH-NOTE (C.5): targeted families 217P/0F/0E, harness IFF invariant held (24+5/13), known-red families node-identical. The reconciliation service is explicitly DISABLED in the verification environment by design (call-time runtime-disable path proven on a flag-OFF call). One outstanding item remains: adjudication-1, after which final aggregation proceeds.

### Outstanding items

- One residual pre-existing red in a quarantined family (not branch-caused; matches the QUARANTINE ledger).
- Doc-truth nit: `service_stop._full_doc_` carries pre-override framing. Deferred per repo convention; functional behavior is correct.

Report delivered above; mission complete."""


def _real_user_msg(content: str) -> HumanMessage:
    """Build a HumanMessage that passes the real-user predicate."""
    return HumanMessage(content=content)


def _ai_msg(content: str) -> AIMessage:
    return AIMessage(content=content)


def _build_bundle_for_real_user_test(
    *,
    user_msg: HumanMessage | None,
    final_ai: AIMessage,
    a_signals: SourceASignals | None = None,
    b_signals: SourceBSignals | None = None,
    c_signals: SourceCSignals | None = None,
    c_tree_rows: list[dict[str, Any]] | None = None,
    prior_ai_msgs: list[AIMessage] | None = None,
) -> Any:
    """Build via the REAL assemble_fused_bundle (worktree HEAD code).

    The AI tail passed to assemble_fused_bundle is ``prior_ai_msgs + [final_ai]``
    so the B-section renders the last 3 AIMessages with the FINAL AI message
    as the newest. assemble_fused_bundle picks via reversed() — the final
    AI message renders as message [1] (newest).
    """
    ai_tail = list(prior_ai_msgs or []) + [final_ai]
    return assemble_fused_bundle(
        a_signals=a_signals,
        b_signals=b_signals,
        c_signals=c_signals,
        c_tree_rows=c_tree_rows or [],
        ai_tail_messages=ai_tail,
        user_intent_message=user_msg,
    )


def _summarize_bundle(bundle: Any) -> dict[str, Any]:
    return {
        "total_chars": bundle.total_chars,
        "a_chars": bundle.a_chars,
        "b_chars": bundle.b_chars,
        "c_chars": bundle.c_chars,
        "u_chars": bundle.u_chars,
        "user_message_included": bundle.user_message_included,
        "sha256_prefix": bundle.sha256[:16],
    }


def _summarize_judge(result: FusedJudgeResult) -> dict[str, Any]:
    return {
        "verdict": result.verdict,
        "is_complete": result.is_complete,
        "model": result.model,
        "latency_ms": result.latency_ms,
        "attempt": result.attempt,
        "invoked": result.invoked,
        "error_class": result.error_class,
        "evidence_cited": list(result.evidence_cited),
        "advisory_note_text": result.advisory_note_text,
        "rationale": result.rationale,
        "first_unparsable_excerpt": result.first_unparsable_excerpt,
    }


async def _call_judge(
    bundle_text: str, config: Any, timeout_s: float
) -> tuple[dict[str, Any], FusedJudgeResult]:
    wall_start = time.monotonic()
    result = await judge_fused_bundle_async(
        bundle_text, config=config, timeout_s=timeout_s
    )
    wall_ms = int((time.monotonic() - wall_start) * 1000)
    summary = _summarize_judge(result)
    summary["wall_clock_ms"] = wall_ms
    summary["raw_output_first_500"] = (
        (result.rationale or "") + " | " + (result.advisory_note_text or "")
    )[:500]
    return summary, result


async def main() -> int:
    # Credential presence check (names only)
    keys = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL")
    creds = {k: bool(os.environ.get(k)) for k in keys}
    print(f"creds: {creds}", flush=True)
    if not creds["OPENAI_API_KEY"]:
        print("RESULT: SKIP (no OPENAI_API_KEY)", flush=True)
        return 0

    config = load_config()
    judge_model_hint = (
        (config.llm.model_keywords or "").strip() or config.llm.model
    )
    print(f"judge_model={judge_model_hint} base_url={bool(config.llm.base_url)} "
          f"request_timeout={config.llm.request_timeout}s", flush=True)

    # Sanity-check the bundle caps (witness)
    print(f"caps: A={BUNDLE_A_SECTION_MAX} B={BUNDLE_B_SECTION_MAX} "
          f"C={BUNDLE_C_SECTION_MAX} U={BUNDLE_U_SECTION_MAX} "
          f"TOTAL={BUNDLE_TOTAL_MAX}", flush=True)

    # Sanity-check the real-user predicate on the 62-char question
    user_h = _real_user_msg(USER_QUESTION_VERBATIM)
    user_real = is_real_user_message(user_h)
    print(f"is_real_user_message(62-char q)={user_real}", flush=True)

    # Build the incident-shape inputs
    c_signals = SourceCSignals(
        pending_children=0,
        queued_or_expected_wakeups=0,
        live_descendants=0,
        busy_descendants=0,
        user_answer_pending=False,
    )
    # The 3029-char final AI answer — assembly renders it as B-section
    # message [1] (newest). We pass empty A advisories + empty tree rows
    # + quiet C signals — the incident-shaped inputs the gate assembled.
    final_ai = _ai_msg(LEADER_ANSWER_VERBATIM)
    final_ai_mismatch = _ai_msg(INTENT_MISMATCH_ANSWER_VERBATIM)

    b_signals_quiet = SourceBSignals(
        marker_hit=False,
        marker_terms=(),
        length_trigger=False,
        final_word_count=len(LEADER_ANSWER_VERBATIM.split()),
        attested=True,
    )

    # Bundles
    bundle_i = _build_bundle_for_real_user_test(
        user_msg=user_h,
        final_ai=final_ai,
        a_signals=None,
        b_signals=b_signals_quiet,
        c_signals=c_signals,
        c_tree_rows=[],
    )
    bundle_ii = _build_bundle_for_real_user_test(
        user_msg=user_h,
        final_ai=final_ai_mismatch,
        a_signals=None,
        b_signals=b_signals_quiet,
        c_signals=c_signals,
        c_tree_rows=[],
    )
    bundle_iii = _build_bundle_for_real_user_test(
        user_msg=None,  # anchor absent → U section omitted
        final_ai=final_ai,
        a_signals=None,
        b_signals=b_signals_quiet,
        c_signals=c_signals,
        c_tree_rows=[],
    )

    bundles_meta = {
        "(i)  4dfded83 SHAPE — real Q + real answer": bundle_i,
        "(ii) INTENT-MISMATCH — real Q + non-answer": bundle_ii,
        "(iii) U-ABSENT — no anchor message": bundle_iii,
    }
    expected_verdicts = {
        "(i)  4dfded83 SHAPE — real Q + real answer": "complete",
        "(ii) INTENT-MISMATCH — real Q + non-answer": "not_complete",
        "(iii) U-ABSENT — no anchor message": "complete",
    }

    summaries: list[dict[str, Any]] = []
    overall_pass = True
    for label, bundle in bundles_meta.items():
        bs = _summarize_bundle(bundle)
        bs["label"] = label
        bs["expected_verdict"] = expected_verdicts[label]
        try:
            jsummary, jresult = await _call_judge(
                bundle.text, config=config, timeout_s=180.0
            )
        except Exception as exc:
            bs["judge_error"] = f"{type(exc).__name__}: {exc}"
            bs["verdict"] = None
            bs["verdict_matches_expected"] = False
            summaries.append(bs)
            overall_pass = False
            continue
        bs.update(jsummary)
        bs["verdict_matches_expected"] = (
            bs["verdict"] == expected_verdicts[label]
        )
        if not bs["verdict_matches_expected"]:
            overall_pass = False
        # Capture witnesses for the report
        bs["bundle_text_preview_first_300"] = bundle.text[:300]
        bs["bundle_text_preview_last_300"] = bundle.text[-300:]
        summaries.append(bs)
        print(f"--- {label} ---", flush=True)
        print(f"  bundle witnesses: total={bs['total_chars']} u={bs['u_chars']} "
              f"user_included={bs['user_message_included']} sha={bs['sha256_prefix']}", flush=True)
        print(f"  verdict={bs['verdict']} expected={bs['expected_verdict']} "
              f"matches={bs['verdict_matches_expected']} latency_ms={bs['latency_ms']} "
              f"attempt={bs['attempt']}", flush=True)
        if bs.get("rationale"):
            print(f"  rationale: {bs['rationale']!r}", flush=True)
        if bs.get("advisory_note_text"):
            print(f"  advisory: {bs['advisory_note_text']!r}", flush=True)

    # Final JSON dump
    final = {
        "probe": "2026-09-18-lca-user-intent-live-probe",
        "judge_model_hint": judge_model_hint,
        "caps": {
            "A": BUNDLE_A_SECTION_MAX,
            "B": BUNDLE_B_SECTION_MAX,
            "C": BUNDLE_C_SECTION_MAX,
            "U": BUNDLE_U_SECTION_MAX,
            "TOTAL": BUNDLE_TOTAL_MAX,
        },
        "user_question_verbatim": USER_QUESTION_VERBATIM,
        "user_question_len": len(USER_QUESTION_VERBATIM),
        "leader_answer_len": len(LEADER_ANSWER_VERBATIM),
        "intent_mismatch_answer_len": len(INTENT_MISMATCH_ANSWER_VERBATIM),
        "is_real_user_message(62-char)": user_real,
        "summaries": summaries,
        "overall_pass": overall_pass,
    }
    print("\n=== FINAL JSON ===", flush=True)
    print(json.dumps(final, indent=2, default=str), flush=True)
    print(f"\nRESULT: {'PASS' if overall_pass else 'FAIL'}", flush=True)
    return 0 if overall_pass else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))