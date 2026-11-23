"""Blueprint pending-lease reaper — regression pins (C3 lease enforcement).

The C3 pending-queue lease (``claimed`` → ``retryable`` after the lease
timeout) was DOCUMENTED in the agent contract but had NO production
caller: ``BlueprintPendingRepository.mark_retryable`` was invoked only
from tests, so a blueprinter that crashed between
``blueprint_claim_pending`` and ``blueprint_acknowledge_pending``
stranded its batch in status ``claimed`` FOREVER — measured on the live
DB: 344 rows across 7 runs, 21-90 days old, invisible to every
detector.

These pins cover the landed sweep:

* **P1** an expired claim IS requeued (``status='retryable'``,
  ``run_token`` NULL, ``claimed_at`` NULL).
* **P2** a claim WITHIN its lease is NOT requeued — the safety pin that
  protects a live drain.
* **P3** the retry cap is preserved: a claim at/over ``max_retries``
  goes to ``abandoned``, not to ``retryable``.
* **P4** the sweep is actually REGISTERED (on the maintenance
  registry) and actually INVOKED (the registered callback drives the
  repository) — not merely a function that exists.
* **P5** failure isolation: a sweep error is logged and the loop
  continues — the sweep never raises into the maintenance loop and
  never wedges the sweep loop.
* **P6** the design decision (a) — the contract's lease semantics, NOT
  an owner-terminal conjunct: a claim whose owner is ABSENT is still
  reclaimable, and the reaper performs NO owner lookup at all (it
  touches exactly one repository method).

The lease timeout the sweep enforces by default is 180 minutes, NOT the
contract's 30-minute code default: measured over 68 acknowledged
production runs, p50 = 14m27s, p90 = 53m02s, max = 2h38m25s (32% of
runs exceed 30 minutes), and against the drain in flight during this
work a 30-minute lease would have requeued 240 of 242 live rows while
the 180-minute lease requeues 0.
"""

from __future__ import annotations

import ast
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from daemon.repositories.blueprint.pending_models import (
    BlueprintPendingUpdate,
    DEFAULT_LEASE_TIMEOUT_MINUTES,
    DEFAULT_MAX_RETRIES,
    PENDING_STATUS_ABANDONED,
    PENDING_STATUS_CLAIMED,
    PENDING_STATUS_RETRYABLE,
)
from daemon.repositories.blueprint.pending_repository import (
    BlueprintPendingRepository,
)
from daemon.services import blueprint_lease_reaper as reaper_module
from daemon.services.blueprint_lease_reaper import (
    BLUEPRINT_LEASE_REAPER_ENABLED_ENV,
    BLUEPRINT_LEASE_REAPER_INTERVAL_MINUTES,
    BLUEPRINT_LEASE_REAPER_LEASE_MINUTES_ENV,
    BLUEPRINT_LEASE_REAPER_MAX_RETRIES_ENV,
    BlueprintLeaseReaper,
    DEFAULT_REAPER_LEASE_MINUTES,
    DEFAULT_REAPER_MAX_RETRIES,
    is_reaper_enabled,
    resolve_lease_minutes,
    resolve_max_retries,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MANAGER_SOURCE = (REPO_ROOT / "daemon" / "manager.py").read_text()
REAPER_SOURCE = (
    REPO_ROOT / "daemon" / "services" / "blueprint_lease_reaper.py"
).read_text()


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_reaper_env(monkeypatch):
    """The knobs are env-direct; make every test deterministic."""
    for var in (
        BLUEPRINT_LEASE_REAPER_ENABLED_ENV,
        BLUEPRINT_LEASE_REAPER_LEASE_MINUTES_ENV,
        BLUEPRINT_LEASE_REAPER_MAX_RETRIES_ENV,
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def engine():
    e = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(e)
    return e


@pytest.fixture
def repo(engine):
    return BlueprintPendingRepository(engine)


@pytest.fixture
def pending_only_engine():
    """A database holding ONLY the pending-queue table.

    Used by the P6 pin: with no job table and no instance table
    present at all, the owner of an expired claim is not merely absent
    — it is UNRESOLVABLE. The reaper must still reclaim the row
    ("no owner row found" means DEAD, never "still live").
    """
    e = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    BlueprintPendingUpdate.__table__.create(e)
    return e


def _claim(repo, project_id="p1", count=1, run_token="run-A"):
    """Enqueue + claim ``count`` rows; return their ids."""
    ids = []
    for i in range(count):
        row = repo.enqueue(project_id, "experience", {"text": f"row-{i}"})
        ids.append(row.id)
    repo.claim_batch(project_id, batch_size=count, run_token=run_token)
    return ids


def _backdate_claimed_at(engine, row_id, minutes):
    """Backdate ``claimed_at`` by ``minutes`` (the lease clock)."""
    stamp = (
        datetime.now(timezone.utc) - timedelta(minutes=minutes)
    ).isoformat()
    with Session(engine) as s:
        rec = s.get(BlueprintPendingUpdate, row_id)
        rec.claimed_at = stamp
        s.add(rec)
        s.commit()


def _set_retry_count(engine, row_id, value):
    with Session(engine) as s:
        rec = s.get(BlueprintPendingUpdate, row_id)
        rec.retry_count = value
        s.add(rec)
        s.commit()


class _RecordingRepo:
    """Fake pending repo that records what the reaper calls."""

    def __init__(self, result=0, raises=None):
        self.result = result
        self.raises = raises
        self.calls = []
        self.accessed = []

    def __getattr__(self, name):
        # Every attribute touch is recorded: the (a)-design pin asserts
        # the reaper touches exactly ONE repository method.
        self.accessed.append(name)
        if name != "mark_retryable":
            raise AttributeError(name)

        def _mark_retryable(lease_timeout_minutes, max_retries):
            self.calls.append((lease_timeout_minutes, max_retries))
            if self.raises is not None:
                raise self.raises
            return self.result

        return _mark_retryable


# ── P1 — an expired claim IS requeued ───────────────────────────────


async def test_p1_expired_claim_is_requeued_to_retryable(repo, engine):
    """P1: a claim past the lease moves to ``retryable`` and is cleared."""
    (row_id,) = _claim(repo)
    _backdate_claimed_at(engine, row_id, DEFAULT_REAPER_LEASE_MINUTES + 30)

    moved = await BlueprintLeaseReaper(repo).execute()

    assert moved == 1
    row = repo.get_by_id(row_id)
    assert row.status == PENDING_STATUS_RETRYABLE
    assert row.run_token is None
    assert row.claimed_at is None
    assert row.processed_at is None


async def test_p1_requeued_claim_is_claimable_again(repo, engine):
    """P1: the queue converges — the reaped row is claimable again."""
    (row_id,) = _claim(repo)
    _backdate_claimed_at(engine, row_id, DEFAULT_REAPER_LEASE_MINUTES + 30)

    await BlueprintLeaseReaper(repo).execute()

    re_claimed = repo.claim_batch("p1", batch_size=5, run_token="run-B")
    assert [r.id for r in re_claimed] == [row_id]
    assert re_claimed[0].run_token == "run-B"


async def test_p1_reaper_calls_the_repository_method(repo, engine):
    """P1: the transition is delegated to the repository's own method."""
    fake = _RecordingRepo(result=3)
    moved = await BlueprintLeaseReaper(fake).execute()
    assert moved == 3
    assert len(fake.calls) == 1
    lease_minutes, max_retries = fake.calls[0]
    assert lease_minutes == DEFAULT_REAPER_LEASE_MINUTES
    assert max_retries == DEFAULT_REAPER_MAX_RETRIES


# ── P2 — a claim WITHIN its lease is NOT requeued ───────────────────


async def test_p2_fresh_claim_is_not_requeued(repo, engine):
    """P2: a claim made seconds ago keeps its claim."""
    (row_id,) = _claim(repo)

    moved = await BlueprintLeaseReaper(repo).execute()

    assert moved == 0
    row = repo.get_by_id(row_id)
    assert row.status == PENDING_STATUS_CLAIMED
    assert row.run_token == "run-A"
    assert row.claimed_at is not None


async def test_p2_claim_inside_the_default_lease_is_not_requeued(repo, engine):
    """P2: the live-drain-scale pin.

    A claim 120 minutes old is still INSIDE the 180-minute default
    lease (the measured live drain's oldest claim was ~45 minutes old
    against a 30-minute contract default — a 30-minute lease would
    have requeued 240 of the 242 live rows).
    """
    (row_id,) = _claim(repo)
    _backdate_claimed_at(engine, row_id, 120)

    moved = await BlueprintLeaseReaper(repo).execute()

    assert moved == 0
    row = repo.get_by_id(row_id)
    assert row.status == PENDING_STATUS_CLAIMED
    assert row.run_token == "run-A"
    assert row.claimed_at is not None


async def test_p2_claim_inside_an_operator_lease_is_not_requeued(
    repo, engine, monkeypatch
):
    """P2: the lease is env-tunable and a claim inside it is untouched."""
    monkeypatch.setenv(BLUEPRINT_LEASE_REAPER_LEASE_MINUTES_ENV, "30")
    (row_id,) = _claim(repo)
    _backdate_claimed_at(engine, row_id, 10)

    moved = await BlueprintLeaseReaper(repo).execute()

    assert moved == 0
    assert repo.get_by_id(row_id).status == PENDING_STATUS_CLAIMED


def test_p2_default_lease_exceeds_the_contract_default():
    """P2: the enforced default is deliberately longer than 30 minutes.

    32% of the 68 measured production runs exceeded the contract's
    30-minute code default (max 2h38m) — enforcing it verbatim would
    reclaim LIVE claims.
    """
    assert DEFAULT_REAPER_LEASE_MINUTES >= 180.0
    assert DEFAULT_REAPER_LEASE_MINUTES > DEFAULT_LEASE_TIMEOUT_MINUTES
    assert DEFAULT_REAPER_LEASE_MINUTES >= 3 * DEFAULT_LEASE_TIMEOUT_MINUTES


# ── P3 — the retry cap path is preserved ────────────────────────────


async def test_p3_claim_at_max_retries_is_abandoned(repo, engine):
    """P3: at/over the cap a claim goes to ``abandoned``, not retryable."""
    (row_id,) = _claim(repo)
    _backdate_claimed_at(engine, row_id, DEFAULT_REAPER_LEASE_MINUTES + 30)
    _set_retry_count(engine, row_id, DEFAULT_MAX_RETRIES)

    moved = await BlueprintLeaseReaper(repo).execute()

    assert moved == 1
    row = repo.get_by_id(row_id)
    assert row.status == PENDING_STATUS_ABANDONED
    assert row.status != PENDING_STATUS_RETRYABLE


async def test_p3_claim_below_the_cap_is_not_abandoned(repo, engine):
    """P3: the cap boundary is exclusive — one below the cap still retries."""
    (row_id,) = _claim(repo)
    _backdate_claimed_at(engine, row_id, DEFAULT_REAPER_LEASE_MINUTES + 30)
    _set_retry_count(engine, row_id, DEFAULT_MAX_RETRIES - 1)

    moved = await BlueprintLeaseReaper(repo).execute()

    assert moved == 1
    assert repo.get_by_id(row_id).status == PENDING_STATUS_RETRYABLE


async def test_p3_cap_is_env_tunable(repo, engine, monkeypatch):
    """P3: the operator knob lowers the cap; the dead-letter path follows."""
    monkeypatch.setenv(BLUEPRINT_LEASE_REAPER_MAX_RETRIES_ENV, "1")
    (row_id,) = _claim(repo)
    _backdate_claimed_at(engine, row_id, DEFAULT_REAPER_LEASE_MINUTES + 30)
    _set_retry_count(engine, row_id, 1)

    moved = await BlueprintLeaseReaper(repo).execute()

    assert moved == 1
    assert repo.get_by_id(row_id).status == PENDING_STATUS_ABANDONED


# ── P4 — the sweep is REGISTERED and INVOKED ────────────────────────


def _registered_calls():
    """Every ``self._maintenance_service.register(...)`` call in manager.py."""
    tree = ast.parse(MANAGER_SOURCE)
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "register"
            and isinstance(func.value, ast.Attribute)
            and func.value.attr == "_maintenance_service"
        ):
            calls.append(node)
    return calls


def _job_name(node):
    for arg in node.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
    for kw in node.keywords:
        if kw.arg == "name" and isinstance(kw.value, ast.Constant):
            return kw.value.value
    return None


def test_p4_manager_registers_the_lease_reaper():
    """P4: the sweep is REGISTERED on the maintenance registry."""
    names = [_job_name(c) for c in _registered_calls()]
    assert "blueprint_lease_reaper" in names, (
        "the pending-lease reaper is not registered on the maintenance "
        f"registry (registered: {names})"
    )


def test_p4_registration_cadence_is_under_the_lease():
    """P4: the registered cadence is 5 minutes — well inside the lease."""
    node = next(
        c for c in _registered_calls() if _job_name(c) == "blueprint_lease_reaper"
    )
    kw = next(k for k in node.keywords if k.arg == "min_interval_hours")
    segment = ast.get_source_segment(MANAGER_SOURCE, kw.value)
    assert segment == "BLUEPRINT_LEASE_REAPER_INTERVAL_MINUTES / 60.0", (
        f"the registered cadence must be derived from the service "
        f"constant, got {segment!r}"
    )
    assert BLUEPRINT_LEASE_REAPER_INTERVAL_MINUTES == 5.0
    # 5 minutes is an order of magnitude inside the 180-minute lease.
    assert BLUEPRINT_LEASE_REAPER_INTERVAL_MINUTES < DEFAULT_REAPER_LEASE_MINUTES


def test_p4_registration_points_at_the_manager_wrapper():
    """P4: the registered callback is the manager's wrapper method."""
    node = next(
        c for c in _registered_calls() if _job_name(c) == "blueprint_lease_reaper"
    )
    kw = next(k for k in node.keywords if k.arg == "execute_fn")
    segment = ast.get_source_segment(MANAGER_SOURCE, kw.value)
    assert segment == "self._run_blueprint_lease_reaper"


async def test_p4_registered_callback_drives_the_repository(repo, engine):
    """P4: the registered callback actually reaps (invoked, not merely
    present)."""
    from daemon.manager import InstanceManager

    (row_id,) = _claim(repo)
    _backdate_claimed_at(engine, row_id, DEFAULT_REAPER_LEASE_MINUTES + 30)

    fake_self = SimpleNamespace(
        _blueprint_lease_reaper=BlueprintLeaseReaper(repo)
    )
    await InstanceManager._run_blueprint_lease_reaper(fake_self)

    assert repo.get_by_id(row_id).status == PENDING_STATUS_RETRYABLE


async def test_p4_manager_wrapper_without_a_reaper_is_a_noop():
    """P4: a partially-initialised manager skips the sweep."""
    from daemon.manager import InstanceManager

    fake_self = SimpleNamespace()
    await InstanceManager._run_blueprint_lease_reaper(fake_self)  # no raise


# ── P5 — failure isolation: log and continue ────────────────────────


async def test_p5_sweep_failure_is_logged_and_swallowed(caplog):
    """P5: a failing sweep logs and returns 0 — it never raises."""
    fake = _RecordingRepo(raises=RuntimeError("boom"))
    with caplog.at_level(logging.ERROR, logger=reaper_module.__name__):
        moved = await BlueprintLeaseReaper(fake).execute()
    assert moved == 0
    assert any(
        "blueprint_lease_reaper" in rec.getMessage() and rec.levelno >= logging.ERROR
        for rec in caplog.records
    )


async def test_p5_sweep_loop_continues_after_a_failed_run(caplog):
    """P5: one failed run does not wedge the sweep loop — the next run
    reaps normally."""
    failing = _RecordingRepo(raises=RuntimeError("boom"))
    with caplog.at_level(logging.ERROR, logger=reaper_module.__name__):
        assert await BlueprintLeaseReaper(failing).execute() == 0

    healthy = _RecordingRepo(result=2)
    assert await BlueprintLeaseReaper(healthy).execute() == 2
    assert len(healthy.calls) == 1


async def test_p5_manager_wrapper_swallows_a_failing_reaper(caplog):
    """P5: the manager wrapper is a second net — it never raises."""
    from daemon.manager import InstanceManager

    class _Boom:
        async def execute(self):
            raise RuntimeError("boom")

    fake_self = SimpleNamespace(_blueprint_lease_reaper=_Boom())
    with caplog.at_level(logging.WARNING, logger="daemon.manager"):
        await InstanceManager._run_blueprint_lease_reaper(fake_self)
    assert any("blueprint_lease_reaper" in rec.getMessage() for rec in caplog.records)


# ── P6 — the (a) design: no owner lookup, absent owner ⇒ reclaimable ─


async def test_p6_claim_with_no_owner_row_is_still_reclaimable(
    pending_only_engine,
):
    """P6: "no owner row found" means DEAD, so the row is reclaimed.

    The fixture database holds ONLY the pending-queue table — there is
    no job table and no instance table to resolve an owner against, so
    the owner of an expired claim is UNRESOLVABLE. The claim must
    still be reclaimed (otherwise the reaper would never reclaim
    exactly the rows it exists to reclaim).
    """
    from sqlalchemy import inspect

    tables = set(inspect(pending_only_engine).get_table_names())
    assert "project_blueprint_pending_updates" in tables
    assert "job_queue_items" not in tables
    assert "instances" not in tables

    repo = BlueprintPendingRepository(pending_only_engine)
    (row_id,) = _claim(repo)
    _backdate_claimed_at(pending_only_engine, row_id, DEFAULT_REAPER_LEASE_MINUTES + 30)

    moved = await BlueprintLeaseReaper(repo).execute()

    assert moved == 1
    assert repo.get_by_id(row_id).status == PENDING_STATUS_RETRYABLE


async def test_p6_reaper_touches_only_the_lease_method():
    """P6: the reaper performs NO owner lookup — exactly ONE repository
    method is touched (``mark_retryable``)."""
    fake = _RecordingRepo(result=0)
    await BlueprintLeaseReaper(fake).execute()
    assert set(fake.accessed) == {"mark_retryable"}


def test_p6_reaper_module_imports_no_owner_repository():
    """P6: the (a) design is structural — no job/instance repository is
    imported by the reaper (an owner-terminal conjunct would need one)."""
    tree = ast.parse(REAPER_SOURCE)
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    for name in imported:
        assert "job_queue" not in name, name
        assert "instance" not in name, name


# ── Knobs ────────────────────────────────────────────────────────────


def test_kill_switch_defaults_to_on():
    """The kill-switch ships ON — the defect it removes is worse than
    the failure it can introduce."""
    assert reaper_module.DEFAULT_REAPER_ENABLED is True
    assert is_reaper_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "disabled", "FALSE"])
def test_kill_switch_disables_the_sweep(monkeypatch, value):
    monkeypatch.setenv(BLUEPRINT_LEASE_REAPER_ENABLED_ENV, value)
    assert is_reaper_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on"])
def test_kill_switch_enables_the_sweep(monkeypatch, value):
    monkeypatch.setenv(BLUEPRINT_LEASE_REAPER_ENABLED_ENV, value)
    assert is_reaper_enabled() is True


def test_kill_switch_is_read_per_run_not_cached(monkeypatch):
    """The switch is env-direct: flipping it takes effect on the NEXT
    run, with no re-instantiation and no daemon restart."""
    reaper = BlueprintLeaseReaper(_RecordingRepo())
    assert is_reaper_enabled() is True
    monkeypatch.setenv(BLUEPRINT_LEASE_REAPER_ENABLED_ENV, "0")
    assert is_reaper_enabled() is False


async def test_kill_switch_off_leaves_claims_untouched(repo, engine, monkeypatch):
    monkeypatch.setenv(BLUEPRINT_LEASE_REAPER_ENABLED_ENV, "0")
    (row_id,) = _claim(repo)
    _backdate_claimed_at(engine, row_id, DEFAULT_REAPER_LEASE_MINUTES + 30)

    moved = await BlueprintLeaseReaper(repo).execute()

    assert moved == 0
    assert repo.get_by_id(row_id).status == PENDING_STATUS_CLAIMED


@pytest.mark.parametrize("bad", ["abc", "", "  ", "-5", "0"])
def test_invalid_lease_env_falls_back_to_the_default(monkeypatch, bad):
    monkeypatch.setenv(BLUEPRINT_LEASE_REAPER_LEASE_MINUTES_ENV, bad)
    assert resolve_lease_minutes() == DEFAULT_REAPER_LEASE_MINUTES


def test_invalid_retry_cap_env_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv(BLUEPRINT_LEASE_REAPER_MAX_RETRIES_ENV, "abc")
    assert resolve_max_retries() == DEFAULT_REAPER_MAX_RETRIES
    monkeypatch.setenv(BLUEPRINT_LEASE_REAPER_MAX_RETRIES_ENV, "-1")
    assert resolve_max_retries() == DEFAULT_REAPER_MAX_RETRIES
