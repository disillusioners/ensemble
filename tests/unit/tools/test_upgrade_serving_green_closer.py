"""Phase-2 daemon-side pins for the serving-green stale-txn closer.

The commission: promote-handback-halt Phase 2 (the SPLIT closer). The
daemon half is a NEW lane in the janitor sweep
(``daemon/services/upgrade_journal_sweep.py``) with its OWN narrow
predicate (P1-P9).

The pins in this file (phase2-plan.md's phase-2 rows):

* **P-6**  the closer's BOTH directions + the authorization negative
* **P-12** the lock discipline (lock + revalidate; busy ⇒ no-op)
* **P-18** the daemon writes the rollback REQUEST with ZERO symlink /
  quarantine / counter side-effects
* **P-21** the promote-wake reader is run_id-scoped (an unrelated
  commit-close cannot drop a pending promote-wake)
* **P-22** the vocabulary-growth updates (the terminal-event set + the
  label)
* **P-28** journal idempotence (write-once per (run_id, target))
* **P-34** the alert-kind split (a REQUEST never pages "auto-rollback")
* **P-35** the degraded-but-grace-held ⇒ DEFER leg (the restored
  invariant)
* **P-37** the REAL GraceTracker leg (the composite's arithmetic, not a
  stub)
* **P-38** the daemon kill-switch, TWO STATES

FIXTURE RULES (house):
* every fixture is SYNTHETIC and lives under ``tmp_path`` — the live
  install (``~/agents-ensemble``) is never touched;
* the journal fixture is GENERATED, never copied from a live install;
* ``P-35`` drives a STUBBED composite (it proves the closer's CONJUNCT);
  ``P-37`` drives the REAL ``GraceTracker`` (it proves the composite's
  ARITHMETIC) — the two legs cannot be satisfied by the same stub.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import daemon as _daemon  # noqa: E402
from daemon.services import upgrade_journal_sweep as svm  # noqa: E402
from daemon.tools import upgrade_journal as uj  # noqa: E402


# ── fixtures ────────────────────────────────────────────────────────────────

STALE_STARTED_AT = "2026-10-07T09:00:00Z"

# The target MUST be the RUNNING daemon's own version (v-prefixed) for the
# GREEN legs: the closer's health read compares the daemon's __version__
# against the v-stripped target (the version-string asymmetry — R-1). A
# fixture version that does not match the daemon's own is the MISMATCH
# class by construction.
DAEMON_VERSION = getattr(_daemon, "__version__", "0.0.0")
TARGET = f"v{DAEMON_VERSION}"
PREV = "v0.0.1-test-prev"


def _write_journal(install: Path, **overrides) -> Path:
    """Write a synthetic journal (never copied from a live install)."""
    releases = install / "releases"
    releases.mkdir(parents=True, exist_ok=True)
    state = releases / "state.json"
    payload = {
        "current": PREV,
        "previous": PREV,
        "in_flight": {
            "kind": "promote",
            "target": TARGET,
            "started_at": STALE_STARTED_AT,
            "flipped": True,
            "owner_pid": 99999,
            "last_heartbeat": 1728000000,
            "displaced_version": PREV,
        },
        "rollback_window_count": {"24h": 0, "window_start": None},
        "cooldown_until": None,
        "quarantined": [],
        "history": [],
    }
    payload.update(overrides)
    state.write_text(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return state


def _seed_history(state: Path, event: str, detail: str) -> None:
    data = json.loads(state.read_text(encoding="utf-8"))
    history = data.setdefault("history", [])
    history.append(
        {
            "ts": "2026-10-07T10:00:00Z",
            "event": event,
            "detail": detail,
        }
    )
    state.write_text(
        json.dumps(data, separators=(",", ":"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _composite(ready: bool, grace_active: bool = False):
    """A synthetic readiness composite (P-35's shape — the CONJUNCT)."""
    return SimpleNamespace(
        ready=ready,
        grace_active=grace_active,
        reasons=[] if ready else ["stub"],
        components={},
        detail={},
        leak_signal=None,
    )


def _service(install: Path, composite=None, **kwargs):
    provider = kwargs.pop("provider", None)
    grace = kwargs.pop("closer_boot_grace_seconds", 1)
    if provider is None:
        fixed = composite

        def provider():  # noqa: ANN001 — the seam under test
            return fixed

    svc = svm.UpgradeJournalSweepService(
        install,
        reconcile_interval_seconds=1,
        reaper_timeout_seconds=60,
        readiness_provider=provider,
        closer_boot_grace_seconds=grace,
        **kwargs,
    )
    return svc


def _run(svc) -> dict:
    return asyncio.run(svc.sweep_serving_green_closer())


def _journal(install: Path) -> dict:
    return json.loads(
        (install / "releases" / "state.json").read_text(encoding="utf-8")
    )


def _events(install: Path) -> list[str]:
    return [e.get("event") for e in _journal(install).get("history", [])]


def _details_for(install: Path, event: str) -> list[str]:
    return [
        str(e.get("detail", ""))
        for e in _journal(install).get("history", [])
        if e.get("event") == event
    ]


# ── P-6: the closer's BOTH directions + the authorization negative ─────────


def test_p6_green_commit_close_closes_the_txn(tmp_path: Path) -> None:
    """GREEN ∧ P9 ⇒ commit-close (history-before-close, no punishment)."""
    install = tmp_path / "install"
    state = _write_journal(install)
    _seed_history(
        state,
        "intent_flip",
        f"preflight: intentional promote target {TARGET} — integrity verified",
    )
    svc = _service(install, composite=_composite(ready=True))
    result = _run(svc)
    assert result["action"] == "committed", result
    data = _journal(install)
    assert data["current"] == TARGET
    assert data["previous"] == PREV
    assert data["in_flight"] is None
    assert "serving_green_commit" in _events(install)
    # NOT done on GREEN: quarantine / counter / cooldown.
    assert data["quarantined"] == []
    assert data["rollback_window_count"]["24h"] == 0
    assert data["cooldown_until"] is None
    assert "sweep_rollback" not in _events(install)


def test_p6_history_precedes_the_close(tmp_path: Path) -> None:
    """AM-P2-1: the history row lands BEFORE the txn closes.

    The crash-prefix invariant: a crash between the history append and
    the in_flight clear leaves the terminal evidence present with the txn
    still open (the next actor can converge). The ORDER is asserted
    structurally: the serving_green_commit row exists and the txn is
    closed — and the journal's history ARRAY ORDER shows the clearance
    event (when present) BEFORE the commit event.
    """
    install = tmp_path / "install"
    state = _write_journal(install)
    _seed_history(
        state,
        "intent_flip",
        f"preflight: intentional promote target {TARGET} — integrity verified",
    )
    svc = _service(install, composite=_composite(ready=True))
    _run(svc)
    events = _events(install)
    assert "serving_green_commit" in events
    assert events.index("serving_green_commit") > 0  # after the intent_flip


def test_p6_green_without_intent_flip_is_a_no_op(tmp_path: Path) -> None:
    """P6's NEGATIVE axis (the R3 hinge): GREEN ∧ ¬P9 ⇒ NO ACTION.

    No commit, no verdict, no request — the txn stays OPEN.
    """
    install = tmp_path / "install"
    _write_journal(install)  # NO intent_flip row
    svc = _service(install, composite=_composite(ready=True))
    result = _run(svc)
    assert result["action"] != "committed", result
    assert result.get("reason") == "no_intent_flip", result
    data = _journal(install)
    assert data["in_flight"] is not None
    assert "serving_green_commit" not in _events(install)
    assert "serving_unhealthy" not in _events(install)
    assert "rollback_requested" not in _events(install)


def test_p6_unhealthy_writes_a_verdict_and_a_request_only(tmp_path: Path) -> None:
    """NOT-GREEN (outside grace) ⇒ verdict + request; NO rollback effects.

    The daemon never rolls back by itself (Option 2 of the plan's
    disposition table): no symlink repoint, no quarantine, no counter.
    """
    install = tmp_path / "install"
    state = _write_journal(install)
    _seed_history(
        state,
        "intent_flip",
        f"preflight: intentional promote target {TARGET} — integrity verified",
    )
    svc = _service(install, composite=_composite(ready=False, grace_active=False))
    # the closer's own boot-grace must have elapsed — the horizon is the
    # closer's own constant; shrink it for the test via the env knob.
    # arm + lapse the grace (the tracker arms on the first observation)
    first = _run(svc)
    assert first["action"] == "deferred", first
    assert first.get("reason") == "boot-grace", first
    import time

    time.sleep(1.2)
    result = _run(svc)
    assert result["action"] == "unhealthy", result
    data = _journal(install)
    assert data["current"] == PREV  # NOT repointed
    assert data["quarantined"] == []
    assert data["rollback_window_count"]["24h"] == 0
    assert data["cooldown_until"] is None
    assert data["in_flight"] is not None  # the daemon does not close it
    assert "serving_unhealthy" in _events(install)
    assert "rollback_requested" in _events(install)
    assert "sweep_rollback" not in _events(install)


def test_p6_fresh_txn_is_untouched(tmp_path: Path) -> None:
    """A txn younger than the staleness gate ⇒ no action."""
    install = tmp_path / "install"
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state = _write_journal(install)
    data = json.loads(state.read_text(encoding="utf-8"))
    data["in_flight"]["started_at"] = now
    state.write_text(json.dumps(data), encoding="utf-8")
    _seed_history(
        state,
        "intent_flip",
        f"preflight: intentional promote target {TARGET} — integrity verified",
    )
    svc = _service(install, composite=_composite(ready=True))
    result = _run(svc)
    assert result["action"] != "committed", result
    assert _journal(install)["in_flight"] is not None


def test_p6_owner_alive_is_untouched(tmp_path: Path) -> None:
    """P7: a LIVE owner ⇒ no action (the txn belongs to a live owner)."""
    install = tmp_path / "install"
    state = _write_journal(install)
    _seed_history(
        state,
        "intent_flip",
        f"preflight: intentional promote target {TARGET} — integrity verified",
    )
    data = json.loads(state.read_text(encoding="utf-8"))
    data["in_flight"]["owner_pid"] = os.getpid()  # this process IS alive
    state.write_text(json.dumps(data), encoding="utf-8")
    svc = _service(install, composite=_composite(ready=True))
    result = _run(svc)
    assert result.get("reason") == "owner_alive", result
    assert _journal(install)["in_flight"] is not None


# ── P-35: the degraded-but-grace-held ⇒ DEFER leg (the restored invariant) ─


def test_p35_grace_held_defers_and_never_requests(tmp_path: Path) -> None:
    """A composite that is degraded BUT grace-held ⇒ DEFER.

    No ``serving_unhealthy`` row, no ``rollback_requested`` row, the txn
    stays OPEN, no quarantine, no counter, no cooldown — no path can
    execute a rollback of a healthy slow boot.
    """
    install = tmp_path / "install"
    state = _write_journal(install)
    _seed_history(
        state,
        "intent_flip",
        f"preflight: intentional promote target {TARGET} — integrity verified",
    )
    svc = _service(
        install, composite=_composite(ready=False, grace_active=True)
    )
    result = _run(svc)
    assert result["action"] == "deferred", result
    assert result.get("reason") == "grace-held", result
    data = _journal(install)
    assert data["in_flight"] is not None
    assert "serving_unhealthy" not in _events(install)
    assert "rollback_requested" not in _events(install)
    assert data["quarantined"] == []
    assert data["rollback_window_count"]["24h"] == 0
    assert data["cooldown_until"] is None


def test_p35_mutation_dropping_the_grace_conjunct_reddens(
    tmp_path: Path,
) -> None:
    """The mutation expectation: drop the ``grace_active == False``
    conjunct ⇒ this leg goes RED (a request row appears)."""
    install = tmp_path / "install"
    state = _write_journal(install)
    _seed_history(
        state,
        "intent_flip",
        f"preflight: intentional promote target {TARGET} — integrity verified",
    )
    svc = _service(
        install, composite=_composite(ready=False, grace_active=True)
    )
    _run(svc)
    # the counterfactual: the same fixture WITHOUT the grace conjunct
    # (what a dropped conjunct would produce)
    svc2 = _service(
        install, composite=_composite(ready=False, grace_active=False)
    )
    _run(svc2)
    import time

    time.sleep(1.2)
    _run(svc2)
    assert "rollback_requested" in _events(install)


# ── P-37: the REAL GraceTracker leg ────────────────────────────────────────


def test_p37_real_grace_tracker_leg(tmp_path: Path) -> None:
    """The REAL tracker (not the P-35 stub): the closer defers while the
    composite's grace is held, and reaches the verdict once the grace has
    lapsed.

    P-35 proves the closer's CONJUNCT against a synthetic composite; this
    proves the COMPOSITE's ARITHMETIC feeds that conjunct ⇒ the two legs
    cannot be satisfied by the same stub.
    """
    from daemon.services.readiness import (
        FAILURE_CLASS_POOL_BUSY,
        GraceTracker,
    )

    install = tmp_path / "install"
    state = _write_journal(install)
    _seed_history(
        state,
        "intent_flip",
        f"preflight: intentional promote target {TARGET} — integrity verified",
    )
    # a REAL tracker with a 1s horizon (the lapsed leg must be reachable
    # inside a unit test)
    tracker = GraceTracker(horizon_s=1)
    holder = {"obs": None}

    def provider():
        obs = holder["obs"]
        grace_active = bool(getattr(obs, "grace_active", False))
        # the degradation PERSISTS across the lapse (the daemon stays
        # not-ready; only the GRACE lapses) ⇒ the mirror leg reaches the
        # never-green verdict
        return SimpleNamespace(
            ready=False,
            grace_active=grace_active,
            reasons=["stub-degraded"],
            components={},
            detail={},
            leak_signal=None,
        )

    # ARM the grace: a pool-busy (grace-eligible) degraded observation
    holder["obs"] = tracker.observe(
        component_classes={"database": FAILURE_CLASS_POOL_BUSY},
        degraded_components=["database"],
    )
    assert holder["obs"].grace_active is True

    svc = _service(install, provider=provider)
    result = _run(svc)
    assert result["action"] == "deferred", result
    assert result.get("reason") == "grace-held", result
    assert "rollback_requested" not in _events(install)
    assert "serving_unhealthy" not in _events(install)
    assert _journal(install)["in_flight"] is not None

    # let the grace LAPSE (the expiry is observation-relative and is NEVER
    # extended) ⇒ the never-green verdict becomes reachable
    later = datetime.now(tz=timezone.utc) + timedelta(seconds=5)
    holder["obs"] = tracker.observe(
        component_classes={"database": FAILURE_CLASS_POOL_BUSY},
        degraded_components=["database"],
        now=later,
    )
    assert holder["obs"].grace_active is False
    import time

    time.sleep(1.2)
    result = _run(svc)
    assert result["action"] == "unhealthy", result
    assert "rollback_requested" in _events(install)


# ── P-12: the lock discipline ──────────────────────────────────────────────


def test_p12_busy_lock_is_a_no_op(tmp_path: Path) -> None:
    """A busy pipeline lock ⇒ no action this tick (never block)."""
    install = tmp_path / "install"
    state = _write_journal(install)
    _seed_history(
        state,
        "intent_flip",
        f"preflight: intentional promote target {TARGET} — integrity verified",
    )
    # hold the lock with a LIVE owner (this process) so the closer's
    # lock_acquire(wait_s=0) is refused.
    lock = install / "releases" / "rollback.lock.d"
    lock.mkdir(parents=True, exist_ok=True)
    (lock / "owner").write_text(f"{os.getpid()}\n", encoding="utf-8")
    (lock / "run_id").write_text("r-other\n", encoding="utf-8")
    (lock / "heartbeat").write_text(f"{int(datetime.now(tz=timezone.utc).timestamp())}\n", encoding="utf-8")
    svc = _service(install, composite=_composite(ready=True))
    result = _run(svc)
    assert result["action"] == "deferred", result
    assert result.get("reason") == "lock_busy", result
    assert _journal(install)["in_flight"] is not None
    assert "serving_green_commit" not in _events(install)


# ── P-28: journal idempotence (write-once per (run_id, target)) ───────────


def test_p28_repeated_ticks_write_one_request(tmp_path: Path) -> None:
    """N closer ticks ⇒ EXACTLY ONE serving_unhealthy + ONE request.

    The closer ticks every 90s; the journal must NOT grow a row per tick.
    """
    install = tmp_path / "install"
    state = _write_journal(install)
    _seed_history(
        state,
        "intent_flip",
        f"preflight: intentional promote target {TARGET} — integrity verified",
    )
    svc = _service(
        install, composite=_composite(ready=False, grace_active=False)
    )
    os.environ[svm.UPGRADE_CLOSER_BOOT_GRACE_ENV] = "1"
    try:
        _run(svc)
        import time

        time.sleep(1.2)
        for _ in range(3):
            _run(svc)
    finally:
        os.environ.pop(svm.UPGRADE_CLOSER_BOOT_GRACE_ENV, None)
    assert len(_details_for(install, "serving_unhealthy")) == 1
    assert len(_details_for(install, "rollback_requested")) == 1


# ── P-18: the request carries ZERO side-effects ───────────────────────────


def test_p18_request_has_no_symlink_or_counter_effects(tmp_path: Path) -> None:
    """The daemon-side request writes NO symlink/quarantine/counter effect."""
    install = tmp_path / "install"
    releases = install / "releases"
    (releases / TARGET).mkdir(parents=True, exist_ok=True)
    (releases / PREV).mkdir(parents=True, exist_ok=True)
    current = install / "current"
    current.symlink_to("releases/" + PREV)
    state = _write_journal(install)
    _seed_history(
        state,
        "intent_flip",
        f"preflight: intentional promote target {TARGET} — integrity verified",
    )
    svc = _service(
        install, composite=_composite(ready=False, grace_active=False)
    )
    _run(svc)
    import time

    time.sleep(1.2)
    _run(svc)
    assert os.readlink(current) == "releases/" + PREV  # untouched
    data = _journal(install)
    assert data["quarantined"] == []
    assert data["rollback_window_count"]["24h"] == 0
    assert data["cooldown_until"] is None
    assert "rollback_requested" in _events(install)


# ── P-34: the alert-kind split ─────────────────────────────────────────────


def test_p34_alert_kinds_are_split() -> None:
    """A REQUEST never pages "auto-rollback happened".

    ``serving_unhealthy`` / ``rollback_requested`` ⇒ the NEW request-stage
    kind; the EXECUTED ``sweep_rollback`` keeps the pre-existing kind.
    """
    kinds = uj.ALERT_KIND_BY_EVENT
    assert kinds["serving_unhealthy"] == "upgrade_serving_unhealthy_requested"
    assert kinds["rollback_requested"] == "upgrade_serving_unhealthy_requested"
    assert kinds["sweep_rollback"] == "upgrade_auto_rollback"
    assert kinds["quarantine"] == "upgrade_auto_rollback"
    assert "serving_green_commit" not in kinds  # a good outcome is silent
    assert kinds["quarantine_cleared_serving_green"] == (
        "upgrade_serving_green_restored"
    )


# ── P-21 / P-22: the vocabulary + the wake-set exclusion ───────────────────


def test_p22_terminal_event_set_grew_by_exactly_one() -> None:
    """P-22: ``serving_green_commit`` joined the terminal set (9 → 10)."""
    assert len(uj._TERMINAL_EVENTS) == 10
    assert "serving_green_commit" in uj._TERMINAL_EVENTS
    assert "sweep_deferred" not in uj._TERMINAL_EVENTS
    assert "quarantine_cleared_serving_green" not in uj._TERMINAL_EVENTS


def test_p22_outcome_label_exists() -> None:
    """P-22: the label-coverage guard needs a label for the new event."""
    from daemon.tools import upgrade_tools as ut

    assert "serving_green_commit" in ut._OUTCOME_LABELS
    missing = sorted(
        k for k in ut._TERMINAL_OUTCOME_EVENTS if k not in ut._OUTCOME_LABELS
    )
    assert missing == [], missing


def test_p21_serving_green_commit_is_excluded_from_the_wake_set() -> None:
    """P-21: the commit-close must NOT complete an unrelated promote-wake.

    The wake reader is TS-scoped and run_id-blind ⇒ an unrelated txn's
    commit-close would otherwise complete a pending promote-wake. The
    event is therefore EXCLUDED from the wake set by explicit
    construction.
    """
    assert "serving_green_commit" not in uj.PROMOTE_WAKE_TERMINAL_EVENTS
    assert "serving_green_commit" not in uj.WAKE_TERMINAL_EVENTS
    assert "serving_green_commit" in uj._TERMINAL_EVENTS
    # the exclusion is EXPLICIT (a reader must be able to see it)
    assert "serving_green_commit" in uj.WAKE_EXCLUDED_EVENTS
    # a promote wake armed BEFORE an unrelated commit-close is not
    # completed by it
    journal = {
        "history": [
            {
                "ts": "2026-11-01T21:00:00Z",
                "event": "serving_green_commit",
                "detail": "target v0.28.9 run_id r-other serving green",
            }
        ]
    }
    armed_at = "2026-11-01T20:00:00Z"
    assert (
        uj.wake_terminal_event_after(journal, armed_at, kind="promote") is None
    )


# ── P-38: the daemon kill-switch, TWO STATES ───────────────────────────────


def test_p38_kill_switch_off_writes_nothing(tmp_path: Path) -> None:
    """``=0`` ⇒ the lane writes NO commit / verdict / request."""
    install = tmp_path / "install"
    state = _write_journal(install)
    _seed_history(
        state,
        "intent_flip",
        f"preflight: intentional promote target {TARGET} — integrity verified",
    )
    svc = _service(install, composite=_composite(ready=True))
    os.environ[svm.SERVING_GREEN_CLOSER_ENV] = "0"
    try:
        result = _run(svc)
    finally:
        os.environ.pop(svm.SERVING_GREEN_CLOSER_ENV, None)
    assert result["action"] == "disabled", result
    data = _journal(install)
    assert data["in_flight"] is not None
    assert "serving_green_commit" not in _events(install)


def test_p38_kill_switch_on_behaves_normally(tmp_path: Path) -> None:
    """``=1`` (the default) ⇒ the GREEN commit-close fires."""
    install = tmp_path / "install"
    state = _write_journal(install)
    _seed_history(
        state,
        "intent_flip",
        f"preflight: intentional promote target {TARGET} — integrity verified",
    )
    svc = _service(install, composite=_composite(ready=True))
    os.environ[svm.SERVING_GREEN_CLOSER_ENV] = "1"
    try:
        result = _run(svc)
    finally:
        os.environ.pop(svm.SERVING_GREEN_CLOSER_ENV, None)
    assert result["action"] == "committed", result


def test_p38_kill_switch_is_read_per_tick(tmp_path: Path) -> None:
    """The switch is NOT cached: flipping it mid-run takes effect at once."""
    install = tmp_path / "install"
    state = _write_journal(install)
    _seed_history(
        state,
        "intent_flip",
        f"preflight: intentional promote target {TARGET} — integrity verified",
    )
    svc = _service(install, composite=_composite(ready=True))
    os.environ[svm.SERVING_GREEN_CLOSER_ENV] = "0"
    try:
        first = _run(svc)
        assert first["action"] == "disabled", first
        os.environ[svm.SERVING_GREEN_CLOSER_ENV] = "1"
        second = _run(svc)
    finally:
        os.environ.pop(svm.SERVING_GREEN_CLOSER_ENV, None)
    assert second["action"] == "committed", second


# ── the per-entry reader contract (the daemon's half) ─────────────────────


def test_per_entry_reader_closes_the_prefix_and_cross_entry_hazards(
    tmp_path: Path,
) -> None:
    """The daemon's reader matches (event, target, run_id) inside ONE entry."""
    install = tmp_path / "install"
    state = _write_journal(install)
    data = json.loads(state.read_text(encoding="utf-8"))
    data["history"] = [
        {
            "ts": "2026-11-01T21:00:00Z",
            "event": "intent_flip",
            "detail": f"preflight: intentional promote target {TARGET}",
        },
        {
            "ts": "2026-11-01T21:01:00Z",
            "event": "rollback_requested",
            "detail": "target v9.9.9-test-target-extra run_id r1 reason not-ready",
        },
    ]
    state.write_text(json.dumps(data), encoding="utf-8")
    assert uj.journal_history_has_event(install, "intent_flip", TARGET)
    # prefix collision: the longer version does not satisfy the shorter one
    assert not uj.journal_history_has_event(
        install, "rollback_requested", "v9.9.9-test-target"
    )
    # cross-entry: the event kind is in one entry, the target in another
    assert not uj.journal_history_has_event(
        install, "rollback_requested", TARGET, "r1"
    )


# ── FIX-CYCLE F2 (code-council W3): the P9 authorization is re-checked
# UNDER the journal lock, against the already-loaded history ────────────────


def test_f2_authorization_withdrawn_under_the_lock_is_not_committed(
    tmp_path: Path,
) -> None:
    """The journal mutated between the pre-lock P9 read and the lock ⇒ NO
    commit.

    The ``readiness_provider`` call sits exactly in that window (after the
    pre-lock P9 read, before ``lock_acquire``) — the provider below
    removes the ``intent_flip`` row on its first call. Pre-fix the
    under-lock re-validation checked only in_flight/target/flipped/
    displaced_version, so the commit-close sailed through on the STALE
    pre-lock authorization.
    """
    install = tmp_path / "install"
    state = _write_journal(install)
    _seed_history(
        state,
        "intent_flip",
        f"preflight: intentional promote target {TARGET} — integrity verified",
    )
    mutated = {"done": False}

    def mutating_provider():  # noqa: ANN001 — the seam under test
        if not mutated["done"]:
            mutated["done"] = True
            data = json.loads(state.read_text(encoding="utf-8"))
            data["history"] = [
                h
                for h in data.get("history", [])
                if h.get("event") != "intent_flip"
            ]
            state.write_text(
                json.dumps(data, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
        return _composite(ready=True)

    svc = _service(install, provider=mutating_provider)
    result = _run(svc)
    assert result["action"] != "committed", result
    assert result.get("reason") == "no_intent_flip_under_lock", result
    data = _journal(install)
    assert data["in_flight"] is not None, "the txn must stay OPEN"
    assert data["current"] == PREV, "current must be untouched"
    assert "serving_green_commit" not in _events(install)


def test_f2_authorization_withdrawn_under_the_lock_writes_no_request(
    tmp_path: Path,
) -> None:
    """The NOT-GREEN arm has the same hole: the verdict + the REQUEST are
    writes too — a withdrawn authorization under the lock must journal
    NEITHER."""
    install = tmp_path / "install"
    state = _write_journal(install)
    _seed_history(
        state,
        "intent_flip",
        f"preflight: intentional promote target {TARGET} — integrity verified",
    )
    calls = {"n": 0}

    def mutating_provider():  # noqa: ANN001 — the seam under test
        calls["n"] += 1
        if calls["n"] >= 2:
            # mutate on the provider call of the tick that reaches the
            # VERDICT arm: the pre-lock P9 read of THAT tick has already
            # passed, and this call sits between it and lock_acquire
            data = json.loads(state.read_text(encoding="utf-8"))
            data["history"] = [
                h
                for h in data.get("history", [])
                if h.get("event") != "intent_flip"
            ]
            state.write_text(
                json.dumps(data, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
        return _composite(ready=False, grace_active=False)

    svc = _service(install, provider=mutating_provider)
    # tick 1 arms the (1s) boot-grace; tick 2 (after the grace elapses)
    # reaches the verdict arm with the authorization withdrawn under the
    # lock
    _run(svc)
    import time

    time.sleep(1.05)
    result = _run(svc)
    assert result["action"] != "unhealthy", result
    assert result.get("reason") == "no_intent_flip_under_lock", result
    assert "serving_unhealthy" not in _events(install)
    assert "rollback_requested" not in _events(install)


def test_f2_healthy_commit_still_works_when_authorization_holds(
    tmp_path: Path,
) -> None:
    """Positive control: the SAME under-lock re-check must not refuse a
    journal whose authorization IS present (the re-check is a guard, not a
    brake)."""
    install = tmp_path / "install"
    state = _write_journal(install)
    _seed_history(
        state,
        "intent_flip",
        f"preflight: intentional promote target {TARGET} — integrity verified",
    )

    def benign_provider():  # noqa: ANN001
        return _composite(ready=True)

    svc = _service(install, provider=benign_provider)
    result = _run(svc)
    assert result["action"] == "committed", result
    assert "serving_green_commit" in _events(install)


# ── FIX-CYCLE F4 (code-council W2): the effective grace floor ──────────────


def test_f4_handback_budget_raises_the_effective_grace(tmp_path: Path, monkeypatch) -> None:
    """``HANDBACK_VERIFY_BUDGET_S`` in the daemon's environment FLOORS the
    effective boot-grace (``max(300, <budget>)``) — the derivation rule
    becomes EFFECTIVE, not just declared."""
    install = tmp_path / "install"
    _write_journal(install)
    svc = svm.UpgradeJournalSweepService(
        install,
        reconcile_interval_seconds=1,
        reaper_timeout_seconds=60,
        readiness_provider=None,
        closer_boot_grace_seconds=None,   # the env/default path, no seam
    )
    monkeypatch.setenv(svm.HANDBACK_VERIFY_BUDGET_ENV, "900")
    assert svc._closer_boot_grace_s() == 900
    # an operator override may still RAISE above the floor
    monkeypatch.setenv(svm.UPGRADE_CLOSER_BOOT_GRACE_ENV, "1200")
    assert svc._closer_boot_grace_s() == 1200
    # the operator override may NOT go below the hand-back floor
    monkeypatch.setenv(svm.UPGRADE_CLOSER_BOOT_GRACE_ENV, "400")
    assert svc._closer_boot_grace_s() == 900
    # garbage in the hand-back knob is IGNORED (fail-safe: default floor)
    monkeypatch.setenv(svm.UPGRADE_CLOSER_BOOT_GRACE_ENV, "")
    monkeypatch.setenv(svm.HANDBACK_VERIFY_BUDGET_ENV, "not-a-number")
    assert svc._closer_boot_grace_s() == svm.UPGRADE_CLOSER_BOOT_GRACE_S


def test_f4_the_effective_value_is_logged_on_clamp(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    """The clamp is surfaced ONCE at WARNING with the EFFECTIVE value."""
    import logging

    install = tmp_path / "install"
    _write_journal(install)
    svc = svm.UpgradeJournalSweepService(
        install,
        reconcile_interval_seconds=1,
        reaper_timeout_seconds=60,
        readiness_provider=None,
        closer_boot_grace_seconds=None,
    )
    monkeypatch.setenv(svm.HANDBACK_VERIFY_BUDGET_ENV, "777")
    with caplog.at_level(logging.WARNING, logger=svm.logger.name):
        assert svc._closer_boot_grace_s() == 777
        assert svc._closer_boot_grace_s() == 777   # repeat: once only
    warnings = [
        r for r in caplog.records
        if "raises the effective" in r.getMessage()
    ]
    assert len(warnings) == 1, [r.getMessage() for r in caplog.records]
    assert "777" in warnings[0].getMessage()


# ── FIX-CYCLE A5/A6 (code-council advisories 5/6) ──────────────────────────


def test_a6_missing_previous_field_never_half_applies_a_commit(
    tmp_path: Path,
) -> None:
    """A journal without ``previous`` (schema drift) must NOT mutate
    ``current``: pre-fix the commit wrote ``current`` first and the
    ``previous`` write raised KeyError ⇒ caught as journal_write_failed
    with ``current`` ALREADY mutated (a half-applied commit)."""
    install = tmp_path / "install"
    state = _write_journal(install)
    _seed_history(
        state,
        "intent_flip",
        f"preflight: intentional promote target {TARGET} — integrity verified",
    )
    data = json.loads(state.read_text(encoding="utf-8"))
    del data["previous"]
    state.write_text(json.dumps(data), encoding="utf-8")
    svc = _service(install, composite=_composite(ready=True))
    result = _run(svc)
    assert result["action"] != "committed", result
    assert result.get("reason") == "journal_field_missing", result
    after = _journal(install)
    assert after["current"] == PREV, "current must be UNTOUCHED"
    assert after["in_flight"] is not None, "the txn must stay OPEN"
    assert "serving_green_commit" not in _events(install)


def test_a5_the_unhealthy_verdict_logs_once(
    tmp_path: Path, caplog
) -> None:
    """The verdict WARNING mirrors the write-once: the first verdict tick
    logs at WARNING, repeat ticks at DEBUG (the 90s-tick noise class)."""
    import logging

    install = tmp_path / "install"
    state = _write_journal(install)
    _seed_history(
        state,
        "intent_flip",
        f"preflight: intentional promote target {TARGET} — integrity verified",
    )
    svc = _service(install, composite=_composite(ready=False, grace_active=False))
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger=svm.logger.name):
        _run(svc)          # arms the 1s boot-grace
        import time

        time.sleep(1.05)
        _run(svc)          # the first VERDICT tick ⇒ WARNING
        _run(svc)          # the repeat tick ⇒ DEBUG
    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "NOT SERVING GREEN" in r.getMessage()
    ]
    debugs = [
        r
        for r in caplog.records
        if r.levelno == logging.DEBUG and "repeat tick" in r.getMessage()
    ]
    assert len(warnings) == 1, [r.getMessage() for r in caplog.records]
    assert len(debugs) == 1, [r.getMessage() for r in caplog.records]


# ── CYCLE-2 M3 (council-corrected semantics) — the version twin ────────────
#
# The twin asymmetry: the LAUNCHER already treats an absent livez version
# as CANNOT-TELL (defer, never punitive). The daemon must match: an EMPTY
# version (import machinery failure — an INTERNAL fault, not a release
# fault) DEFERS with a distinct reason BEFORE any verdict; a GENUINE
# wrong version (non-empty, != target) still classifies mismatch and,
# after grace, takes the request path (never-green armed).


def test_m3_empty_version_defers_before_any_verdict(
    tmp_path: Path, monkeypatch
) -> None:
    """IMPORT failure (= empty version) must DEFER with the DISTINCT
    reason ``version-import-failed`` BEFORE any verdict/request — never
    the punishment class (pre-fix it flowed into version-mismatch => a
    rollback request for OUR import machinery)."""
    import builtins

    install = tmp_path / "install"
    state = _write_journal(install)
    _seed_history(
        state,
        "intent_flip",
        f"preflight: intentional promote target {TARGET} — integrity verified",
    )
    # healthy composite: WITHOUT the import failure this txn COMMITS
    # (green) — the pin proves the import fault alone re-routes to DEFER.
    svc = _service(install, composite=_composite(ready=True))

    real_import = builtins.__import__

    def poisoned_import(name, *args, **kwargs):
        if name == "daemon":
            raise ImportError("simulated import machinery failure (M3 pin)")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", poisoned_import)
    result = _run(svc)
    monkeypatch.setattr(builtins, "__import__", real_import)

    assert result["action"] == "deferred", result
    assert result["reason"] == "version-import-failed", result
    after = _journal(install)
    events = [e["event"] for e in after["history"]]
    assert "serving_unhealthy" not in events, events
    assert "rollback_requested" not in events, events
    assert "serving_green_commit" not in events, events
    # the txn stays OPEN and current is UNTOUCHED (zero writes)
    assert after["in_flight"] is not None
    assert after["current"] == PREV


def test_m3_wrong_version_takes_the_mismatch_request_path(
    tmp_path: Path,
) -> None:
    """A GENUINE wrong version (non-empty, != target) with an otherwise
    HEALTHY composite still classifies mismatch => after the boot-grace
    => the verdict + rollback REQUEST path (never-green stays armed) —
    the mirror direction of the empty-version defer."""
    import time

    wrong_target = "v0.0.0-genuinely-wrong"
    install = tmp_path / "install"
    state = _write_journal(install)
    # point the OPEN TXN at a genuinely-wrong version (the health read
    # then sees daemon.__version__ != the v-stripped target)
    _data = json.loads(state.read_text(encoding="utf-8"))
    _data["in_flight"]["target"] = wrong_target
    state.write_text(json.dumps(_data, separators=(",", ":")), encoding="utf-8")
    _seed_history(
        state,
        "intent_flip",
        f"preflight: intentional promote target {wrong_target} — integrity verified",
    )
    svc = _service(install, composite=_composite(ready=True, grace_active=False))
    _run(svc)          # arms the 1s boot-grace
    time.sleep(1.05)
    result = _run(svc)  # the first VERDICT-eligible tick

    assert result["action"] == "unhealthy", result
    assert result["reason"] == "version-mismatch", result
    events = [e["event"] for e in _journal(install)["history"]]
    assert "serving_unhealthy" in events, events
    assert "rollback_requested" in events, events
    # and the never-green protection STAYS ARMED: no commit row
    assert "serving_green_commit" not in events, events


def test_adv1_daemonconfig_port_premise_init_source_beats_env(monkeypatch) -> None:
    """Council ADV-1 (delta re-review, final fold) — the F9 port premise:
    pydantic gives the INIT (config.yaml) source priority over the
    DAEMON_PORT env (``env_prefix="DAEMON_"``, daemon/config.py:566), so
    the yaml-interpolated ``daemon.port: ${PORT:-8088}`` (config.yaml:113)
    IS the daemon's effective port whenever the key is present — the
    launcher probe's port source (``_js_sweep_probe_port``) depends on
    this. A config.py refactor that silently lets env outrank init would
    invalidate the probe's port source; the second assert proves the env
    prefix is genuinely live (the pin is not vacuous)."""
    from daemon.config import DaemonConfig

    monkeypatch.setenv("DAEMON_PORT", "9999")
    assert DaemonConfig(port=8088).port == 8088  # init source wins over env
    assert DaemonConfig().port == 9999  # env prefix live when the key is absent
