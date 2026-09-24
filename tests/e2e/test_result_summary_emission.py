"""Intent5 — result_summary / job_completed emission-surface regression test.

Commission: v0.13.9 result_summary / job-completed arm (the
``commit`` ``Phase 5 Batch 2 commit-41633433`` JobItem mirror columns were
dropped, but the producer→consumer→event-row pipeline was never re-wired
on the new ``Task.result`` durable home). This file exercises the FULL
real-daemon emission surface — it does NOT mock the publisher or the
observer. Judge trigger is execution-lane intersection (per
``tests/e2e/test_e2e_workflows.py`` precedent and
``.agents/tester/rules/ensure.md``).

Asserts on FOUR surfaces:

  (a) /api/jobs/{job_id}/events SSE → terminal ``event: completed`` →
      ``data.result_summary`` is non-null (the agent's last assistant
      message content, not the fallback marker
      ``"Job completed (no agent response captured)"``).
  (b) GET /api/jobs/{job_id} → ``result_summary`` is non-null (same
      shape as (a) — sourced via the resolver).
  (c) Read-only DB query: event table row with ``kind='job_completed'``
      for this job_id with ``data->>'result_summary'`` non-null.
  (d) /api/notifications/stream SSE opened BEFORE terminal → at least
      one notification for this instance carries ``result_summary``.

F6a (2026-09-22; updated 2026-09-24): on surfaces (a) and (b),
``result_summary`` is CLEAN TEXT — the resolver helper
(``_parse_task_result_summary``) extracts the producer envelope's
``content`` key since the DEFECT-1 fix (commit 1e12944a) instead of
dumping the whole envelope. The test pins the clean-text shape:
non-empty, not the fallback marker, and NOT a ``{``-prefixed JSON
dump (a regression to the pre-fix whole-envelope ``json.dumps``
fails). Literal content is NOT asserted (canned mock LLM). Payload
shapes are NOT unified across surfaces (F6b DEFERRED — the legacy
``GET /messages/{id}/status`` route still dumps the whole envelope).

Run against the dev daemon (``./dev.sh`` / ``./dev_with_mock.sh`` on
port 8079). Skips when the daemon is unreachable. The mock LLM server
(``tests/mock_llm_server.py``) returns deterministic canned responses,
so the test asserts NON-NULL on the surfaces (not literal content) —
the production seam ``manager._get_last_assistant_message_raw`` is
exercised end-to-end.

Sibling-drift hazard: per the commission, this file MUST ship in the
same commit set as the production-code Items 1-4 + the two flipped
contract tests in ``test_work_resolver.py`` and
``test_job_result_summary_and_gate.py`` — see the Dev Report.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any

import pytest
import requests
from sqlalchemy import create_engine, text
from sqlmodel import Session

# Daemon imports are guarded so non-E2E collection doesn't break.
try:  # pragma: no cover - importability guard
    from daemon.repositories.event.models import Event, EventKind
except ImportError:  # pragma: no cover - environment-specific
    Event = None  # type: ignore[assignment]
    EventKind = None  # type: ignore[assignment]


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s.%(msecs)03d - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
BASE_URL = os.environ.get("E2E_BASE_URL", "http://localhost:8079")
API_BASE = f"{BASE_URL}/api"

# The DB the daemon was launched with — must match the running daemon's
# POSTGRES_DB. ``./dev.sh`` defaults to ``ensemble_dev``. The test does
# NOT override; the daemon's actual DB is the only correct target.
E2E_PG_HOST = os.environ.get("E2E_PG_HOST", os.environ.get("POSTGRES_HOST", "localhost"))
E2E_PG_PORT = int(os.environ.get("E2E_PG_PORT", os.environ.get("POSTGRES_PORT", "5432")))
E2E_PG_DB = os.environ.get("E2E_PG_DB", os.environ.get("POSTGRES_DB", "ensemble_dev"))
E2E_PG_USER = os.environ.get("E2E_PG_USER", os.environ.get("POSTGRES_USER", "ensemble"))
E2E_PG_PASSWORD = os.environ.get("E2E_PG_PASSWORD", os.environ.get("POSTGRES_PASSWORD", "testpw"))


# --------------------------------------------------------------------------- #
# F1 — env foot-gun guard (fix/job-completed-result-arm, 2026-09-22)
# --------------------------------------------------------------------------- #
# The ambient-POSTGRES_* fallback above re-normalized the exact foot-gun
# that caused the 2026-09-21 live-DB incident: a host with
# ``POSTGRES_DB=ensemble_prod`` exported makes the read-only event-table
# query below resolve against the LIVE database. Refuse to run when the
# resolved DB is prod-like, unless an explicit ``E2E_PG_DB`` override is
# set (the override wins — an operator who explicitly names a DB has
# made the dev intent unambiguous).
PROD_LIKE_DBS = frozenset({"ensemble_prod", "ensemble_live", "ensemble_demo"})
_E2E_PG_DB_EXPLICIT = bool(os.environ.get("E2E_PG_DB"))


def _pg_env_refusal_reason() -> str | None:
    """Return the F1 refusal message, or ``None`` when the env is safe.

    Fail-fast contract: evaluated at import/collection time (before the
    ``_pg_reachable()`` skipif probe below, which opens a real DB
    connection during collection) so a prod-like resolution is caught
    before ANY connection attempt is made.
    """
    if _E2E_PG_DB_EXPLICIT:
        return None
    if E2E_PG_DB in PROD_LIKE_DBS:
        return (
            f"REFUSED (F1 env guard): resolved POSTGRES_DB={E2E_PG_DB!r} is a "
            f"prod-like database ({', '.join(sorted(PROD_LIKE_DBS))}). This "
            f"ambient-POSTGRES_* fallback caused the 2026-09-21 live-DB "
            f"incident. Set an explicit E2E_PG_DB (override wins) or scrub "
            f"POSTGRES_* from the environment before running this test."
        )
    return None


_PG_ENV_REFUSAL = _pg_env_refusal_reason()


# Real-daemon timeouts — generous, mock LLM is fast.
COMPLETION_TIMEOUT = 90
POLL_INTERVAL = 2
NOTIFICATIONS_OPEN_TIMEOUT = 5

# Deterministic agent + prompt. The mock LLM returns canned responses
# (cycles through 5 strings); we assert result_summary is NON-NULL and
# NOT the fallback marker. We do NOT assert literal content because the
# production seam returns whatever the LLM produced.
DETERMINISTIC_AGENT = "leader"
DETERMINISTIC_PROMPT = "Respond with a short acknowledgment."

# Fallback marker the observer stamps when no agent response is found
# (job_feedback_observer.py:1546 — pre-fix). Post-fix, this marker is
# reachable only on true seam failures; the Intent5 path goes through
# WorkerPool.on_success with a populated content payload, so this
# marker MUST NOT appear on the wire.
FALLBACK_MARKER = "Job completed (no agent response captured)"


# --------------------------------------------------------------------------- #
# Skip-if-daemon-down guard
# --------------------------------------------------------------------------- #
def _daemon_running() -> bool:
    try:
        response = requests.get(f"{API_BASE}/health", timeout=5)
        return response.status_code == 200
    except requests.exceptions.RequestException:
        return False
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"_daemon_running: unexpected error: {exc!r}")
        return False


def _pg_url() -> str:
    """Return a sync postgresql+psycopg:// URL for the E2E test DB."""
    # Belt-and-suspenders behind the F1 pytestmark guard: a direct
    # programmatic call can never build a prod-like URL either.
    if _PG_ENV_REFUSAL is not None:
        raise RuntimeError(_PG_ENV_REFUSAL)
    return (
        f"postgresql+psycopg://{E2E_PG_USER}:{E2E_PG_PASSWORD}"
        f"@{E2E_PG_HOST}:{E2E_PG_PORT}/{E2E_PG_DB}"
    )


def _pg_reachable() -> bool:
    if _PG_ENV_REFUSAL is not None:
        # The F1 refusal skipif (first in pytestmark) already names the
        # reason; never open a connection to a refused target.
        return False
    try:
        eng = create_engine(_pg_url(), pool_pre_ping=True)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
        return True
    except Exception as exc:
        logger.warning(f"_pg_reachable: {exc!r}")
        return False


pytestmark = [
    pytest.mark.integration,
    # F1 env guard: refusal is eager at module-import via
    # ``_PG_ENV_REFUSAL = _pg_env_refusal_reason()`` (line 125) — runs
    # before any skipif probe opens a connection. The pytestmark ORDER
    # is cosmetic; the real fence is the guard function itself.
    pytest.mark.skipif(
        _PG_ENV_REFUSAL is not None,
        reason=_PG_ENV_REFUSAL or "PG env guard passed",
    ),
    pytest.mark.skipif(
        not _daemon_running(),
        reason="Daemon not running at localhost:8079 — start with ./dev.sh",
    ),
    pytest.mark.skipif(
        not _pg_reachable(),
        reason=(
            f"Postgres {E2E_PG_USER}@{E2E_PG_HOST}:{E2E_PG_PORT}/{E2E_PG_DB} "
            "not reachable — set E2E_PG_* envs to override"
        ),
    ),
]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _create_job(agent_id: str, message: str, timeout: int = 30) -> str:
    """POST ``/api/jobs`` and return the new ``job_id``."""
    body: dict[str, Any] = {
        "agent_id": agent_id,
        "message": message,
        "priority": 5,
    }
    response = requests.post(
        f"{API_BASE}/jobs", json=body, timeout=timeout
    )
    response.raise_for_status()
    data = response.json()
    job_id = data.get("job_id") or data.get("id")
    if not job_id:
        raise RuntimeError(f"Job create response missing job_id: {data}")
    return job_id


def _get_job(job_id: str, timeout: int = 10) -> dict[str, Any]:
    response = requests.get(f"{API_BASE}/jobs/{job_id}", timeout=timeout)
    response.raise_for_status()
    return response.json()


def _consume_sse_until_terminal(
    job_id: str, timeout: int = COMPLETION_TIMEOUT
) -> list[dict[str, Any]]:
    """Subscribe to SSE job events; collect until terminal or timeout.

    Returns a list of parsed event dicts. Each dict has ``event`` (the
    SSE named event) and ``data`` (the parsed JSON payload).
    """
    events: list[dict[str, Any]] = []
    deadline = time.monotonic() + timeout
    try:
        response = requests.get(
            f"{API_BASE}/jobs/{job_id}/events",
            stream=True,
            timeout=timeout,
            headers={"Accept": "text/event-stream"},
        )
        # Track named-event lines (`event: foo`) so the test can assert
        # on `event: completed` specifically (the parser does NOT
        # surface the named event for free).
        current_event: str | None = None
        for raw_line in response.iter_lines(decode_unicode=True):
            if time.monotonic() >= deadline:
                break
            if not raw_line:
                continue
            if raw_line.startswith("event:"):
                current_event = raw_line[len("event:"):].strip()
                continue
            if not raw_line.startswith("data:"):
                continue
            try:
                payload = json.loads(raw_line[len("data:"):].strip())
            except (json.JSONDecodeError, ValueError):
                continue
            events.append({"event": current_event or "data", "data": payload})
            # Early-exit once we see the terminal `completed` named event
            # with non-None data.
            if current_event == "completed":
                break
            current_event = None
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
        pass
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"[SSE] unexpected error on {job_id[:8]}...: {exc}")
    return events


class _NotificationsCollector:
    """Open ``/api/notifications/stream`` SSE, collect up to deadline.

    Used by assertion (d) — open the global stream BEFORE the terminal
    fires, then look for the matching instance_id notification with a
    ``result_summary`` field.
    """

    def __init__(self, deadline_seconds: float = COMPLETION_TIMEOUT + 5) -> None:
        self._deadline = time.monotonic() + deadline_seconds
        self._stop = threading.Event()
        self._notifications: list[dict[str, Any]] = []
        self._error: BaseException | None = None
        self._thread = threading.Thread(
            target=self._run, name="Intent5-NotificationsCollector",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout: float = 10) -> None:
        if self._thread.is_alive():
            self._thread.join(timeout=timeout)

    @property
    def notifications(self) -> list[dict[str, Any]]:
        return list(self._notifications)

    def _run(self) -> None:
        try:
            response = requests.get(
                f"{API_BASE}/notifications/stream",
                stream=True,
                timeout=int(self._deadline - time.monotonic()),
                headers={"Accept": "text/event-stream"},
            )
            current_event: str | None = None
            for raw_line in response.iter_lines(decode_unicode=True):
                if self._stop.is_set() or time.monotonic() >= self._deadline:
                    break
                if not raw_line:
                    continue
                if raw_line.startswith("event:"):
                    current_event = raw_line[len("event:"):].strip()
                    continue
                if not raw_line.startswith("data:"):
                    continue
                try:
                    payload = json.loads(raw_line[len("data:"):].strip())
                except (json.JSONDecodeError, ValueError):
                    continue
                self._notifications.append(
                    {"event": current_event or "data", "data": payload}
                )
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            pass
        except Exception as exc:  # pragma: no cover - defensive
            self._error = exc
            logger.warning(f"[NotificationsCollector] {exc!r}")


def _query_event_table(job_id: str) -> list[dict[str, Any]]:
    """Return event rows whose ``data->>'job_id' == job_id`` AND
    ``kind='job_completed'``. Read-only — the test never writes to the
    event table (the daemon owns writes).
    """
    eng = create_engine(_pg_url(), pool_pre_ping=True)
    try:
        with Session(eng) as session:
            # Cast text JSON-data column to JSONB-equivalent in PG via
            # jsonb extraction. ``data`` is TEXT in this codebase so we
            # parse in Python rather than using ``->>`` to keep the
            # query dialect-agnostic.
            stmt = text(
                "SELECT id, instance_id, kind, data, created_at "
                "FROM event WHERE kind = 'job_completed' ORDER BY id ASC"
            )
            rows = session.execute(stmt).mappings().all()
        matched: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(row["data"]) if row["data"] else {}
            except (json.JSONDecodeError, ValueError, TypeError):
                payload = {}
            if payload.get("job_id") == job_id:
                matched.append({
                    "id": row["id"],
                    "instance_id": row["instance_id"],
                    "kind": row["kind"],
                    "data": payload,
                    "created_at": row["created_at"],
                })
        return matched
    finally:
        eng.dispose()


# --------------------------------------------------------------------------- #
# Test
# --------------------------------------------------------------------------- #
def test_result_summary_emission_surface_intent5():
    """Intent5 — result_summary / job_completed end-to-end emission.

    Exercises the FOUR emission surfaces against a REAL running daemon
    (mock LLM is fine — the seam under test is the daemon's, not the
    LLM's). The test asserts NON-NULL result_summary on each surface;
    literal content is NOT asserted because the mock LLM is canned.

    Pre-fix (v0.13.9 base): result_summary is null on all four surfaces
    and the ``job_completed`` event kind does not exist. Test FAILS.

    Post-fix (this commit set): result_summary is populated on all four
    surfaces and a ``job_completed`` event row is persisted. Test
    PASSES.
    """
    # Open the notifications stream BEFORE creating the job so the
    # root-completion notification lands in the queue.
    notif_collector = _NotificationsCollector()
    notif_collector.start()
    time.sleep(0.5)  # let SSE handshake settle

    try:
        # 1. Create a job. Mock LLM cycles canned responses; the agent's
        #    final assistant message becomes the production-seam
        #    ``_get_last_assistant_message_raw`` content.
        job_id = _create_job(DETERMINISTIC_AGENT, DETERMINISTIC_PROMPT)
        logger.info(f"[Intent5] job created: {job_id}")

        # 2. (a) Open the per-job SSE stream and wait for terminal
        #    ``event: completed``. Assert ``data.result_summary`` is
        #    non-null and not the pre-fix fallback marker.
        events = _consume_sse_until_terminal(job_id)
        completed_events = [
            e for e in events if e.get("event") == "completed"
        ]
        assert len(completed_events) >= 1, (
            f"[Intent5(a)] expected ≥1 `event: completed` SSE frame; "
            f"got {len(completed_events)}/{len(events)} events. "
            f"events={events[:3]}"
        )
        completed_payload = completed_events[-1]["data"]
        sse_result = completed_payload.get("result_summary")
        assert sse_result is not None, (
            f"[Intent5(a)] SSE completed event data.result_summary is "
            f"None. Pre-fix v0.13.9 defect: producer passes content-less "
            f"payload; resolver surfaces None on dual-backed rows. "
            f"payload={completed_payload}"
        )
        assert sse_result != FALLBACK_MARKER, (
            f"[Intent5(a)] SSE completed event result_summary is the "
            f"pre-fix fallback marker — content path was not exercised. "
            f"got={sse_result!r}"
        )
        # F6a (2026-09-22; updated 2026-09-24): result_summary on this
        # surface is now CLEAN TEXT — since the DEFECT-1 fix
        # (commit 1e12944a) ``_parse_task_result_summary`` extracts the
        # producer envelope's ``content`` key instead of json.dumps-ing
        # the whole dict. Pin the clean-text shape: non-empty AND not a
        # ``{``-prefixed JSON dump — so a regression to empty/None (the
        # pre-v0.13.9 defect), the fallback marker, or the pre-fix
        # whole-envelope dump ALL fail here. Literal content is NOT
        # asserted (canned mock LLM). Shapes are NOT unified across
        # surfaces (F6b DEFERRED).
        assert isinstance(sse_result, str) and sse_result.strip(), (
            f"[Intent5(a/F6a)] SSE result_summary is empty or "
            f"non-string — clean-text contract regressed. "
            f"result_summary={sse_result!r}"
        )
        assert not sse_result.lstrip().startswith("{"), (
            f"[Intent5(a/F6a)] SSE result_summary is a JSON dump — "
            f"regressed to the pre-fix whole-envelope json.dumps "
            f"(clean-text contract). result_summary={sse_result!r}"
        )

        # 3. (b) GET /api/jobs/{job_id} — resolver surfaces
        #    result_summary sourced from Task.result via
        #    _parse_task_result_summary.
        job = _get_job(job_id)
        get_result = job.get("result_summary")
        assert get_result is not None, (
            f"[Intent5(b)] GET /api/jobs result_summary is None. "
            f"Pre-fix v0.13.9 defect: _job_to_record dual-backed path "
            f"passes only task_timing, not task; resolver surfaces None "
            f"for JobItem-backed rows. job={job}"
        )
        assert get_result != FALLBACK_MARKER, (
            f"[Intent5(b)] GET result_summary is the fallback marker — "
            f"got={get_result!r}"
        )
        # F6a (2026-09-22; updated 2026-09-24): same clean-text contract
        # as (a) — the GET body's result_summary is sourced via the
        # resolver (``_parse_task_result_summary``), which since the
        # DEFECT-1 fix (commit 1e12944a) surfaces the envelope's
        # ``content`` text verbatim. Pin non-empty AND not a
        # ``{``-prefixed JSON dump: empty/None, the fallback marker, or
        # the pre-fix whole-envelope dump all fail here.
        assert isinstance(get_result, str) and get_result.strip(), (
            f"[Intent5(b/F6a)] GET result_summary is empty or "
            f"non-string — clean-text contract regressed. "
            f"result_summary={get_result!r}"
        )
        assert not get_result.lstrip().startswith("{"), (
            f"[Intent5(b/F6a)] GET result_summary is a JSON dump — "
            f"regressed to the pre-fix whole-envelope json.dumps "
            f"(clean-text contract). result_summary={get_result!r}"
        )

        # 4. (c) Read-only DB query — event row kind='job_completed'
        #    for this job_id, with data.result_summary non-null.
        #    The Intent5 fix adds JOB_COMPLETED to EventKind
        #    (daemon/repositories/event/models.py:12-23) and the
        #    observer's Step 4 publishes it (job_feedback_observer.py
        #    sibling publish at :3029-3042).
        #
        # v0.13.9 fix follow-up (2026-09-22): the JOB_COMPLETED sibling
        # publish runs in the OBSERVER's ``_finalize_job`` block AFTER
        # ``_finalize_job_db_sync`` commits JobItem=done. The SSE
        # consumer's polling loop fires the completed event based on
        # the JobItem=done commit, so the test's per-job SSE event
        # can land in the brief window where the JOB_COMPLETED event
        # row hasn't been written yet (the observer's sibling publish
        # runs on the event loop right after the sync DB half
        # returns — microsecond-scale race). Retry the read-only
        # query with brief backoff to drain the race window before
        # asserting. Bounded so a permanent seam failure does NOT
        # block the test forever.
        #
        # 30 × 200ms = 6s ceiling. The retry count is generous
        # because the daemon has multiple JobItem-transition paths
        # (observer via lifecycle event, JobProcessor's natural
        # completion, bus callback) and the JOB_COMPLETED sibling
        # publish only fires on the observer path. On a path that
        # transitions the JobItem to done WITHOUT firing the
        # observer, the JOB_COMPLETED row never appears — but those
        # paths are the same paths that miss the entire lifecycle
        # event chain (a deeper multi-path race that the commission
        # doesn't address). The 6s ceiling gives the observer enough
        # time to fire on the common path.
        matched: list[dict[str, Any]] = []
        for _retry in range(30):
            matched = _query_event_table(job_id)
            if matched:
                break
            time.sleep(0.2)
        assert EventKind is not None and hasattr(
            EventKind, "JOB_COMPLETED"
        ), (
            "[Intent5(c)] EventKind.JOB_COMPLETED is not defined on "
            "this lineage (post-fix should add it). Pre-fix: 10 members."
        )
        assert len(matched) >= 1, (
            f"[Intent5(c)] expected ≥1 job_completed event row for "
            f"job_id={job_id}; got {len(matched)}. "
            f"Pre-fix: observer Step 4 does not publish JOB_COMPLETED."
        )
        evt_summary = matched[0]["data"].get("result_summary")
        assert evt_summary is not None, (
            f"[Intent5(c)] job_completed event row data.result_summary "
            f"is None. Pre-fix: producer passes content-less payload; "
            f"sibling publish receives None. row={matched[0]}"
        )

        # 5. (d) /api/notifications/stream — open BEFORE terminal, look
        #    for notification carrying result_summary. The Intent5 fix
        #    threads result_summary through
        #    broadcaster.emit_root_completion (notification_broadcaster
        #    .py:127-147) so it appears in the broadcast data dict.
        #    Wait briefly for the notification to land after the SSE
        #    completed event has fired.
        instance_id = job.get("instance_id")
        assert instance_id is not None, (
            f"[Intent5(d)] job missing instance_id; can't correlate "
            f"notifications. job={job}"
        )
        deadline = time.monotonic() + 5
        matched_notifs: list[dict[str, Any]] = []
        while time.monotonic() < deadline and not matched_notifs:
            notifs = notif_collector.notifications
            matched_notifs = [
                n for n in notifs
                if n.get("data", {}).get("instance_id") == instance_id
                and n.get("data", {}).get("result_summary") is not None
            ]
            if matched_notifs:
                break
            time.sleep(0.2)
        assert matched_notifs, (
            f"[Intent5(d)] no notification on /api/notifications/stream "
            f"for instance_id={instance_id} carried result_summary. "
            f"Pre-fix: event_publisher._publish_instance_lifecycle_event "
            f"does not thread result_summary through "
            f"broadcaster.emit_root_completion; the field is absent "
            f"from the broadcast dict. total_notifs={len(notif_collector.notifications)}"
        )

        # Surface check (d) succeeded — at least one notification has
        # the field. We don't assert literal content (mock LLM canned).

    finally:
        notif_collector.stop()
        notif_collector.join(timeout=3)
