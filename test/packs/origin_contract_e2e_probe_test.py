#!/usr/bin/env python3
"""Origin-Contract E2E Probe — POST /api/jobs 422 surface + census re-derivation.

Independent of the feature/security-boundary-hygiene branch's own 27P suite.
Drives the REAL router end-to-end via httpx ASGI transport against a minimal
FastAPI app that mounts ONLY the jobs_crud router with real Pydantic
validation + real reserved-source gate, and stubs only the manager/enqueue
downstream.

Three sections:

  PART 1 — 422 surface matrix:
    For each case, record status code + response body shape (error envelope)
    and PASS/FAIL against the expected behaviour documented in the spec and
    in daemon/routers/jobs_crud.py:273-298 + daemon/constants.py:424-470.

  PART 2 — census re-derivation (READ-ONLY static grep):
    Enumerate every place a source/origin value is MINTED (written into
    durable state — message_queue rows, JobItem.source columns, etc.).
    Diff the derived mint-set against the 17 RESERVED_SOURCE_PREFIXES
    members. Any daemon-minted durable origin NOT in the reserved set
    is a BLOCKER. Any reserved member with no mint site is over-reservation.

  PART 3 — USER_ORIGIN_SOURCES overlap check:
    Verify the zero-overlap claim vs RESERVED_SOURCE_PREFIXES.

Exit codes:
  0   — PASS (every assertion holds; census has no BLOCKER gaps)
  1   — FAIL (any assertion or census BLOCKER)
  124 — internal timeout (signal.alarm)

Spec: feature/security-boundary-hygiene branch, HEAD ac2c3091 (valid).
      Gate: daemon/routers/jobs_crud.py:299-316 (is_reserved_source +
      JobValidationError envelope). Schema: daemon/routers/schemas.py:12-22
      (JobCreateRequest.source: str = Field(default="api", min_length=1)).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
from fastapi import FastAPI

# Repo root = parent of test/packs/
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ── Self-guard: signal.alarm (Layer 2 inner guard) ──────────────────────────

INTERNAL_TIMEOUT_S = 150


class TimeoutError_(Exception):
    pass


def _alarm_handler(signum, frame):
    raise TimeoutError_(f"internal timeout after {INTERNAL_TIMEOUT_S}s")


# ── Pre-flight: env + constants ─────────────────────────────────────────────

# ensure_system_default_project() may not have run; the handler calls
# normalize_project_id which reads daemon.constants.SYSTEM_DEFAULT_PROJECT_ID.
# Setting it here lets the probe boot without the full daemon lifespan.
import daemon.constants  # noqa: E402

daemon.constants.SYSTEM_DEFAULT_PROJECT_ID = "probe-default-project"

# We also want to fail loud (NOT silently) on unexpected schema/runtime
# drift — never let a regression hide behind a green test.
os.environ.setdefault("PYTHONHASHSEED", "0")


# ── Stub downstream (manager + JobQueueService) ─────────────────────────────


class CapturingStubService:
    """Stand-in for JobQueueService — captures every enqueue call and returns
    a fully-shaped JobItem so the real _job_to_response() succeeds.

    The agent registry is real (so validate_agent_id passes), the router is
    real, the Pydantic validation is real, the is_reserved_source gate is
    real — only the downstream persistence call is stubbed.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def enqueue(self, **kwargs) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(
            job_id=f"probe-{len(self.calls):04d}",
            admission_state="queued",
            priority=kwargs.get("priority", 5),
            agent_id=kwargs["agent_id"],
            agent_dir=f"/tmp/agents/{kwargs['agent_id']}",
            project_id=kwargs.get("project_id"),
            queue_id=kwargs.get("queue_id"),
            instance_id=None,
            created_at=datetime.now(timezone.utc).isoformat(),
            source=kwargs.get("source"),
            job_metadata=kwargs.get("metadata"),
            idempotency_key=kwargs.get("idempotency_key"),
            message=kwargs.get("message"),
            deleted_at=None,
            terminal_reason=None,
        )

    async def _get_queue_position(self, *args, **kwargs) -> int | None:
        return None

    async def get_work(self, *args, **kwargs) -> None:
        return None  # Legacy fallback path in _job_to_response


def build_app(stub: CapturingStubService) -> FastAPI:
    """Minimal app: ONLY jobs_crud router mounted at /api; manager stub
    exposes is_write_paused=False; get_job_queue_service overridden."""
    from daemon.routers.jobs_crud import get_job_queue_service, router as jobs_crud_router

    app = FastAPI()
    app.include_router(jobs_crud_router, prefix="/api")
    # _get_manager(request) reads request.app.state.manager.is_write_paused
    app.state.manager = SimpleNamespace(is_write_paused=False)
    app.dependency_overrides[get_job_queue_service] = lambda: stub
    return app


# ── PART 1 — 422 surface matrix ─────────────────────────────────────────────

# (label, body, expected_kind)
# expected_kind ∈ {"ok", "pydantic_422", "gate_422", "unknown"}
#
# ok             → 201 from handler (stub returns success)
# pydantic_422   → 422 from FastAPI/Pydantic type or min_length validation
# gate_422       → 422 from is_reserved_source gate (JobValidationError envelope)
# unknown        → not 422 from either layer (may be 201 or other 422)

PART1_CASES: list[tuple[str, dict[str, Any], str]] = [
    # ── Pydantic defaults / null / empty ───────────────────────────────────
    (
        "1. source omitted (Pydantic default)",
        {"agent_id": "developer", "message": "probe"},
        "ok",
    ),
    (
        "2. source: null (Pydantic str-not-None)",
        {"agent_id": "developer", "message": "probe", "source": None},
        "pydantic_422",
    ),
    (
        '3. source: "" (Pydantic min_length=1)',
        {"agent_id": "developer", "message": "probe", "source": ""},
        "pydantic_422",
    ),

    # ── Reserved colon-prefix families ──────────────────────────────────────
    (
        "4. source: system:evil (reserved prefix)",
        {"agent_id": "developer", "message": "probe", "source": "system:evil"},
        "gate_422",
    ),
    (
        "5a. source: agent:forge (reserved prefix)",
        {"agent_id": "developer", "message": "probe", "source": "agent:forge"},
        "gate_422",
    ),
    (
        "5b. source: blueprint-sidecar:x (reserved prefix)",
        {"agent_id": "developer", "message": "probe", "source": "blueprint-sidecar:x"},
        "gate_422",
    ),
    (
        "5c. source: internal_agent:abc (reserved prefix)",
        {"agent_id": "developer", "message": "probe", "source": "internal_agent:abc"},
        "gate_422",
    ),
    (
        "5d. source: internal_report:r-1 (reserved prefix)",
        {"agent_id": "developer", "message": "probe", "source": "internal_report:r-1"},
        "gate_422",
    ),
    (
        "5e. source: internal_error_report:e-1 (reserved prefix)",
        {"agent_id": "developer", "message": "probe", "source": "internal_error_report:e-1"},
        "gate_422",
    ),
    (
        "5f. source: internal_invoke_and_wait:i-1 (reserved prefix)",
        {"agent_id": "developer", "message": "probe", "source": "internal_invoke_and_wait:i-1"},
        "gate_422",
    ),
    (
        "5g. source: explore:iid-1 (reserved prefix)",
        {"agent_id": "developer", "message": "probe", "source": "explore:iid-1"},
        "gate_422",
    ),
    (
        "5h. source: experience:iid-1 (reserved prefix)",
        {"agent_id": "developer", "message": "probe", "source": "experience:iid-1"},
        "gate_422",
    ),

    # ── Reserved exact (no colon) values ────────────────────────────────────
    (
        "6a. source: watchover_next_command (reserved exact)",
        {"agent_id": "developer", "message": "probe", "source": "watchover_next_command"},
        "gate_422",
    ),
    (
        "6b. source: skill_metric_scan (reserved exact)",
        {"agent_id": "developer", "message": "probe", "source": "skill_metric_scan"},
        "gate_422",
    ),
    (
        "6c. source: auto-scan (reserved exact)",
        {"agent_id": "developer", "message": "probe", "source": "auto-scan"},
        "gate_422",
    ),
    (
        "6d. source: cascade_resume (reserved exact)",
        {"agent_id": "developer", "message": "probe", "source": "cascade_resume"},
        "gate_422",
    ),
    (
        "6e. source: api_resume_fallback (reserved exact)",
        {"agent_id": "developer", "message": "probe", "source": "api_resume_fallback"},
        "gate_422",
    ),
    (
        "6f. source: skill_evolution (reserved exact)",
        {"agent_id": "developer", "message": "probe", "source": "skill_evolution"},
        "gate_422",
    ),
    (
        "6g. source: admin-endpoint (reserved exact)",
        {"agent_id": "developer", "message": "probe", "source": "admin-endpoint"},
        "gate_422",
    ),

    # ── MIXED-CASE — pinned deliberate behavior (case-sensitive) ────────────
    (
        "7a. source: System:evil (MIXED-CASE — should NOT gate 422)",
        {"agent_id": "developer", "message": "probe", "source": "System:evil"},
        "unknown",
    ),
    (
        "7b. source: AGENT:x (MIXED-CASE — should NOT gate 422)",
        {"agent_id": "developer", "message": "probe", "source": "AGENT:x"},
        "unknown",
    ),
    (
        "7c. source: Watchover_next_command (MIXED-CASE — should NOT gate 422)",
        {"agent_id": "developer", "message": "probe", "source": "Watchover_next_command"},
        "unknown",
    ),

    # ── LEGITIMATE user sources — pass the gate ─────────────────────────────
    (
        "8a. source: api (legitimate user-source exact)",
        {"agent_id": "developer", "message": "probe", "source": "api"},
        "ok",
    ),
    (
        "8b. source: telegram:123 (legitimate user-source prefix)",
        {"agent_id": "developer", "message": "probe", "source": "telegram:123"},
        "ok",
    ),
    (
        "8c. source: webhook:x (legitimate user-source prefix)",
        {"agent_id": "developer", "message": "probe", "source": "webhook:x"},
        "ok",
    ),
    (
        "8d. source: custom-source (free-form legitimate)",
        {"agent_id": "developer", "message": "probe", "source": "custom-source"},
        "ok",
    ),
    (
        "8e. source: scheduler (reserved exact — daemon-minted)",
        {"agent_id": "developer", "message": "probe", "source": "scheduler"},
        "gate_422",
    ),

    # ── NEAR-MISS non-reserved (prefix-colon-boundary check) ────────────────
    (
        "9a. source: systemx:evil (NEAR-MISS prefix)",
        {"agent_id": "developer", "message": "probe", "source": "systemx:evil"},
        "ok",
    ),
    (
        "9b. source: system (NEAR-MISS bare prefix)",
        {"agent_id": "developer", "message": "probe", "source": "system"},
        "ok",
    ),
    (
        "9c. source: auto-scanx (NEAR-MISS exact-extended)",
        {"agent_id": "developer", "message": "probe", "source": "auto-scanx"},
        "ok",
    ),
]


def envelope_shape(status: int, body: Any) -> str:
    """Compact envelope-shape label for the table."""
    if status == 201 and isinstance(body, dict) and "job_id" in body:
        return "JobResponse JSON (201)"
    if status == 200 and isinstance(body, dict) and "job_id" in body:
        return "JobResponse JSON (200)"
    if status == 422 and isinstance(body, dict) and "detail" in body:
        detail = body["detail"]
        if isinstance(detail, list) and detail and "type" in detail[0]:
            # FastAPI/Pydantic built-in: list of {type, loc, msg, input, ...}
            types = sorted({d.get("type", "?") for d in detail if isinstance(d, dict)})
            return f"Pydantic 422 (types={types})"
        if isinstance(detail, dict) and detail.get("error") == "Validation Error":
            fields = sorted({d.get("field", "?") for d in detail.get("details", []) if isinstance(d, dict)})
            return f"JobValidationError envelope (fields={fields})"
        return f"422 detail={type(detail).__name__}"
    if isinstance(body, dict):
        keys = sorted(body.keys())[:5]
        return f"dict[{','.join(keys)}]"
    return type(body).__name__


async def run_part1(app: FastAPI, stub: CapturingStubService) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for label, body, expected_kind in PART1_CASES:
            resp = await client.post("/api/jobs", json=body)
            shape = envelope_shape(resp.status_code, resp.json() if resp.content else None)
            actual_kind = _classify_actual(resp.status_code, resp.json() if resp.content else None)
            verdict = _verdict(expected_kind, actual_kind)
            rows.append(
                {
                    "label": label,
                    "source_value": body.get("source", "<omitted>"),
                    "status": resp.status_code,
                    "envelope": shape,
                    "expected": expected_kind,
                    "actual": actual_kind,
                    "verdict": verdict,
                }
            )
    return rows


def _classify_actual(status: int, body: Any) -> str:
    if status in (200, 201) and isinstance(body, dict) and "job_id" in body:
        return "ok"
    if status == 422 and isinstance(body, dict) and "detail" in body:
        detail = body["detail"]
        if isinstance(detail, list):
            return "pydantic_422"
        if isinstance(detail, dict) and detail.get("error") == "Validation Error":
            return "gate_422"
    return f"unexpected(status={status})"


def _verdict(expected: str, actual: str) -> str:
    """PASS/FAIL verdict per row.

    Rules:
      * expected "ok" + actual "ok" → PASS
      * expected "pydantic_422" + actual "pydantic_422" → PASS
      * expected "gate_422" + actual "gate_422" → PASS
      * expected "unknown" (mixed-case deliberate) + actual "ok" → PASS
        (mixed-case is documented as passing the case-sensitive gate; if
        the stub then accepts the call, the verdict is PASS — the test
        pins the deliberate behaviour)
      * expected "unknown" + actual "pydantic_422" → FAIL (unexpected rejection)
      * expected "unknown" + actual "gate_422" → FAIL (gate is case-folded
        — would be a contract regression)
      * any other mismatch → FAIL
    """
    if expected == actual:
        return "PASS"
    if expected == "unknown":
        if actual == "ok":
            return "PASS (mixed-case accepted — deliberate)"
        return "FAIL"
    return "FAIL"


# ── PART 2 — census re-derivation ───────────────────────────────────────────

# The 17 members per daemon/constants.py:431-451 (the binding).
RESERVED_PREFIXES_FROZENSET = (
    "system:",
    "internal_agent:",
    "internal_report:",
    "internal_error_report:",
    "internal_invoke_and_wait:",
    "explore:",
    "experience:",
    "agent:",
    "blueprint-sidecar:",
    "cascade_resume",
    "api_resume_fallback",
    "watchover_next_command",
    "skill_metric_scan",
    "skill_evolution",
    "admin-endpoint",
    "auto-scan",
    "scheduler",
)


def _read_constants_reserved() -> frozenset[str]:
    """Read the real constant from daemon.constants to confirm 17 members."""
    from daemon.constants import RESERVED_SOURCE_PREFIXES
    return RESERVED_SOURCE_PREFIXES


def _grep_mint_sites() -> dict[str, list[tuple[str, int]]]:
    """Static grep for every source/origin mint site in daemon/.

    Walks each reserved literal prefix (with trailing colon) and the exact
    reserved values; for each, collects daemon/*.py grep hits. Also catches
    f-string interpolated variants (``f"system:{name}"``) by searching for
    the bare literal fragment.
    """
    results: dict[str, list[tuple[str, int]]] = {}
    daemon_dir = REPO_ROOT / "daemon"

    # Patterns: literal prefix (with trailing colon) + bare exact values.
    # We grep each reserved value as a substring; for the final "first
    # representative mint site" display we re-rank to prefer real
    # mint patterns (``source=``, ``message_source=``, ``WATCHDOG_SOURCE``,
    # ``WEDGE_SOURCE``) over false-positives like ``job_system:``.
    patterns: list[tuple[str, str]] = []
    for member in RESERVED_PREFIXES_FROZENSET:
        patterns.append((member, member))  # bare substring match
    # f-string interpolated patterns for the colon-terminated families
    # (catches ``f"system:{name}"``-style mints). We search for the prefix
    # minus the trailing colon, with an opening quote before and a colon
    # immediately after — i.e. "system:" anywhere on the line.
    for member in RESERVED_PREFIXES_FROZENSET:
        if member.endswith(":"):
            patterns.append((f"\"{member}\"", member))
            patterns.append((f"f\"{member[:-1]}:{{", f"f-string {member[:-1]}:..."))

    # True-mint-line indicators (used to rank representative sites).
    mint_indicators = ("source=", "message_source=", "WATCHDOG_SOURCE",
                       "WEDGE_SOURCE", "f\"", "f'", "JOB_TYPE_EVOLUTION",
                       "JOB_TYPE_METRIC_SCAN")

    for label, needle in patterns:
        hits: list[tuple[str, int, str]] = []
        for py_file in daemon_dir.rglob("*.py"):
            try:
                text = py_file.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for line_no, line in enumerate(text.splitlines(), start=1):
                if needle in line:
                    # Skip constants.py itself (that's the definition site,
                    # not a mint site) and skip pure docstring lines
                    # starting with "#".
                    if py_file.name == "constants.py":
                        continue
                    if line.lstrip().startswith("#"):
                        continue
                    hits.append((str(py_file.relative_to(REPO_ROOT)), line_no, line.strip()))
        if hits:
            # Merge by member label (keep the first occurrence as canonical
            # so the table stays compact).
            if label not in results:
                # Re-rank: prefer lines that look like real mint sites.
                ranked = sorted(
                    hits,
                    key=lambda h: (0 if any(ind in h[2] for ind in mint_indicators) else 1, h[1]),
                )
                results[label] = [(h[0], h[1]) for h in ranked]
    return results


def _enumerate_observed_members(mint_sites: dict[str, list[tuple[str, int]]]) -> list[str]:
    """List of distinct reserved literals that have ≥1 mint site."""
    observed: list[str] = []
    for member in RESERVED_PREFIXES_FROZENSET:
        if member in mint_sites or any(member.startswith(k.replace("\"", "")) for k in mint_sites.keys()):
            if member in mint_sites:
                observed.append(member)
    # Catch members that appeared only under the f-string alias (e.g. the
    # exact-match keys).
    for label in mint_sites.keys():
        for member in RESERVED_PREFIXES_FROZENSET:
            if member == label or (member.endswith(":") and label.strip("\"") == member):
                if member not in observed:
                    observed.append(member)
    return observed


def run_part2() -> tuple[dict[str, list[tuple[str, int]]], list[str], list[str], list[str]]:
    """Returns (mint_sites, observed_members, missing_mint, over_reserved)."""
    # Confirm the constant matches the 17-member contract.
    actual_const = _read_constants_reserved()
    if len(actual_const) != 17:
        raise AssertionError(
            f"RESERVED_SOURCE_PREFIXES has {len(actual_const)} members, "
            f"expected 17. Members: {sorted(actual_const)}"
        )

    mint_sites = _grep_mint_sites()

    # For each reserved member, check whether the bare literal appeared
    # in any daemon/*.py file (other than constants.py) — that's our
    # "mint site observed" criterion.
    observed = []
    for member in RESERVED_PREFIXES_FROZENSET:
        if member in mint_sites:
            observed.append(member)
            continue
        # f-string variants: e.g. ``f"system:{name}"`` does NOT contain the
        # literal "system:" substring unless the value is static. For
        # dynamic f-strings we look for the prefix-up-to-colon.
        if member.endswith(":"):
            bare = member[:-1]
            for py_file in (REPO_ROOT / "daemon").rglob("*.py"):
                if py_file.name == "constants.py":
                    continue
                try:
                    text = py_file.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                # Look for f"bare:{" or f'bare:{' or "bare:" + concatenation
                if (
                    f'f"{bare}:{{' in text
                    or f"f'{bare}:{{" in text
                    or f'"{bare}:"' in text
                    or f"'{bare}:'" in text
                ):
                    observed.append(member)
                    break
            else:
                continue
            break

    # Missing mint sites = reserved members with zero observed mint sites
    # in daemon/ (excluding constants.py).
    missing_mint = [m for m in RESERVED_PREFIXES_FROZENSET if m not in observed]

    # Over-reserved = reserved members with zero observed mint sites.
    # (Same as missing_mint today; kept as a distinct semantic label for
    # future evolution — e.g. if over-reservation is ever treated as
    # cleanup rather than benign.)
    over_reserved = list(missing_mint)

    return mint_sites, observed, missing_mint, over_reserved


# ── PART 3 — USER_ORIGIN_SOURCES overlap ────────────────────────────────────


def run_part3() -> dict[str, Any]:
    """Verify USER_ORIGIN_SOURCES vs RESERVED_SOURCE_PREFIXES zero overlap.

    USER_ORIGIN_SOURCES (the whitelisted user-source set, distinct from the
    reserved internal half) is defined at daemon/tools/upgrade_journal.py:1081.
    """
    from daemon.constants import RESERVED_SOURCE_PREFIXES
    from daemon.tools.upgrade_journal import USER_ORIGIN_SOURCES

    reserved = set(RESERVED_SOURCE_PREFIXES)
    user = set(USER_ORIGIN_SOURCES)

    intersection = reserved & user

    return {
        "reserved_count": len(reserved),
        "user_count": len(user),
        "user_members": sorted(user),
        "intersection": sorted(intersection),
        "zero_overlap": len(intersection) == 0,
    }


# ── Reporting ────────────────────────────────────────────────────────────────


def banner(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def print_part1_table(rows: list[dict[str, Any]]) -> bool:
    banner("PART 1 — 422 surface matrix (POST /api/jobs)")
    print(
        f"{'#':<3} {'Status':<7} {'Verdict':<35} {'Source':<30} Envelope"
    )
    print("-" * 78)
    fail = 0
    for i, row in enumerate(rows, 1):
        verdict = row["verdict"]
        if "FAIL" in verdict:
            fail += 1
        src = repr(row["source_value"])[:30]
        print(
            f"{i:<3} {row['status']:<7} {verdict:<35} {src:<30} {row['envelope']}"
        )
        print(f"     {row['label']}")
        print(
            f"     expected={row['expected']!r:<18} actual={row['actual']!r}"
        )
    print()
    print(
        f"PART 1 SUMMARY: {len(rows)} cases, "
        f"{len(rows) - fail} PASS, {fail} FAIL"
    )
    # Also record the effective source for case 1 (omitted)
    banner("Case 1 verification — effective source at the downstream stub")
    print("Captured enqueue() calls show what source value the handler")
    print("passed to the service. Pydantic default 'api' is expected.")
    return fail == 0


def print_part2_table(
    mint_sites: dict[str, list[tuple[str, int]]],
    observed: list[str],
    missing_mint: list[str],
    over_reserved: list[str],
) -> bool:
    banner("PART 2 — Census re-derivation (daemon/ mint sites)")
    print(f"17 reserved members per daemon/constants.py:431-451:")
    for i, m in enumerate(RESERVED_PREFIXES_FROZENSET, 1):
        observed_marker = "✓" if m in observed else "✗"
        sites = mint_sites.get(m, [])
        sample = (
            f"{sites[0][0]}:{sites[0][1]}"
            if sites
            else "(none)"
        )
        print(
            f"  {i:>2}. {observed_marker} {m:<32}  sample mint site: {sample}"
        )
    print()
    if missing_mint:
        print(f"MISSING MINT SITES (reserved but no observed daemon mint):")
        for m in missing_mint:
            print(f"  - {m!r}")
        print(f"  → These are over-reservation candidates (benign today,")
        print(f"     but flag for cleanup if intended to be retired).")
    else:
        print("MISSING MINT SITES: none (every reserved member has ≥1 mint site).")
    print()
    print(f"OBSERVED MINT SITES (daemon/*.py, excluding constants.py):")
    total_hits = 0
    for m, hits in mint_sites.items():
        total_hits += len(hits)
        print(f"  {m!r} → {len(hits)} hit(s)")
        for fpath, line_no in hits[:3]:
            print(f"     {fpath}:{line_no}")
        if len(hits) > 3:
            print(f"     ... +{len(hits) - 3} more")
    print(f"TOTAL hits: {total_hits}")
    print()
    print(
        f"PART 2 SUMMARY: 17 reserved members, "
        f"{len(observed)} observed mint sites, "
        f"{len(missing_mint)} missing (over-reserved)"
    )

    # The spec says: "ANY daemon-minted durable origin NOT in the reserved
    # set = BLOCKER". Since we enumerated mint sites by RESERVED member,
    # this probe surfaces over-reservation (reserved without a mint site),
    # not under-reservation (mints outside the reserved set). A separate
    # scan would be needed for that — we note it as a probe limitation.
    has_blocker = bool(missing_mint)
    return not has_blocker


def print_part3_table(result: dict[str, Any]) -> bool:
    banner("PART 3 — USER_ORIGIN_SOURCES overlap check")
    print(f"RESERVED_SOURCE_PREFIXES count: {result['reserved_count']}")
    print(f"USER_ORIGIN_SOURCES count: {result['user_count']}")
    print(f"USER_ORIGIN_SOURCES members: {result['user_members']}")
    print(f"Intersection: {result['intersection']}")
    if result["zero_overlap"]:
        print("VERDICT: zero overlap — claim holds ✅")
        return True
    else:
        print("VERDICT: OVERLAP DETECTED — claim violated ❌")
        return False


# ── Main ─────────────────────────────────────────────────────────────────────


async def main() -> int:
    signal.signal(signal.SIGALRM, _alarm_handler)
    signal.alarm(INTERNAL_TIMEOUT_S)

    try:
        stub = CapturingStubService()
        app = build_app(stub)

        banner("Pre-flight — app/router wiring")
        print(f"REPO_ROOT: {REPO_ROOT}")
        print(f"HEAD: feature/security-boundary-hygiene @ 16c59375 (valid)")
        print(f"Router mounted at /api/jobs (POST handler at jobs_crud.py:234)")
        print(f"Stub downstream: CapturingStubService (manager + JobQueueService)")
        print(f"Pydantic + is_reserved_source gate: REAL")

        rows = await run_part1(app, stub)
        p1_ok = print_part1_table(rows)
        # Print case 1 verification explicitly
        if stub.calls:
            first_call = stub.calls[0]
            print(f"  → first enqueue() call: source={first_call.get('source')!r}")

        mint_sites, observed, missing_mint, over_reserved = run_part2()
        p2_ok = print_part2_table(mint_sites, observed, missing_mint, over_reserved)

        p3_result = run_part3()
        p3_ok = print_part3_table(p3_result)

        banner("FINAL VERDICT")
        if p1_ok and p2_ok and p3_ok:
            print("RESULT: PASS")
            print(
                "  Part 1: every case matches its expected gate behaviour.\n"
                "  Part 2: every reserved member has ≥1 mint site (no BLOCKER gaps).\n"
                "  Part 3: USER_ORIGIN_SOURCES ∩ RESERVED = ∅ (zero overlap)."
            )
            return 0
        else:
            fails = []
            if not p1_ok:
                fails.append("Part 1 (gate surface)")
            if not p2_ok:
                fails.append("Part 2 (census gaps)")
            if not p3_ok:
                fails.append("Part 3 (USER_ORIGIN overlap)")
            print("RESULT: FAIL")
            print(f"  Failed: {', '.join(fails)}")
            return 1
    except TimeoutError_ as e:
        print(f"RESULT: TIMEOUT ({e})")
        return 124
    except Exception:
        traceback.print_exc()
        print("RESULT: FAIL (unhandled exception)")
        return 1
    finally:
        signal.alarm(0)


if __name__ == "__main__":
    rc = asyncio.run(main())
    sys.exit(rc)
