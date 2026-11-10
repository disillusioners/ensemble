"""FIX-WAVE-1 pins — the config/route/dialect wiring (M1, M2, M3).

Registered pack: ``skill_evolution_unit_test`` (EXTENDED) — see
``.agents/tester/PACKS.md``.

Three findings, three pins. Each pin names its OWN mutation — a pin
that cannot go RED under a named mutation is vacuous, and a vacuous
pin is worse than no pin (it is a green light wired to nothing).

* **M1** — the ``SkillEvolutionConfig.fix_burst_cap`` /
  ``fix_burst_window_seconds`` knobs (and their documented env alias)
  were DEAD: ``daemon/manager.py`` constructed the
  ``SkillJobDispatcher`` WITHOUT them, so the dispatcher silently used
  its module defaults. The pin proves the CONFIGURED value REACHES the
  dispatcher — including the explicit-0 case (the config contract:
  "0 disables the cap"), which must SURVIVE and must NOT fall back to
  the default 3.
* **M2** — a designed refusal (``SkillFixBurstCapRefused``) escaped
  the admin route ``POST /api/skills/{id}/fix`` as an HTTP **500 +
  traceback**, while every OTHER lane refusal on the SAME route
  returned a truthful 503. The pin drives the ROUTE (not the
  dispatcher) and asserts the refusal is a non-5xx status carrying the
  refusal reason.
* **M3** — the PostgreSQL branch of the E1 dialect split
  (``metadata->>'skill_id'``) had ZERO coverage: every pin runs on
  SQLite, so swapping (or deleting) the PG branch left the whole pin
  set green — while PostgreSQL is the PRODUCTION dialect. The pin
  compiles the statement for BOTH dialects with NO database contact
  and asserts the operators are not swapped.

The M3 pin is STATIC (no DB contact) on purpose: this lane has no
``PG_TEST_*`` scratch DSN provisioned, and the ambient
``POSTGRES_*`` environment points at the LIVE ``ensemble_prod``
database, so a real-PG leg here would risk live contact for no extra
signal — the compiled SQL is the same artifact the driver sends.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from sqlalchemy.dialects import postgresql, sqlite
from starlette.testclient import TestClient

from daemon.repositories.job_queue.repository import (
    build_fix_admission_count_statement,
)
# The lane's SAFE sink redirect + the shared-path sentinel (the
# order-dependent leak fix: a teardown may never restore None).
from tests.helpers.skill_sink_redirect import (
    lane_test_end,
    lane_test_start,
    restore_safe_sink_redirect,
    snapshot_real_sink_dir,
)

from daemon.services import skill_capture_observability as observability
from daemon.services.skill_job_dispatcher import (
    DEFAULT_FIX_BURST_CAP,
    DEFAULT_FIX_BURST_WINDOW_SECONDS,
    SkillFixBurstCapRefused,
)

#: The repository root — the subprocess pins below import THIS tree.
REPO_ROOT = Path(__file__).resolve().parents[2]


# ── M1 — the config knob REACHES the dispatcher ─────────────────────────


class _StubQueueRepo:
    """A queue repo (the dispatcher's routing lookup finds nothing)."""

    def get_by_name(self, project_id, queue_name):
        return None


class _StubJobService:
    """A job service double (the manager only passes it through)."""

    _queue_repo = _StubQueueRepo()


def _manager_with_config(skill_evolution_cfg):
    """Build an InstanceManager shell carrying only the wiring inputs.

    ``InstanceManager.set_job_queue_service`` is the production site
    that constructs the ``SkillJobDispatcher``; the manager is built
    with ``__new__`` (no boot) so the pin exercises the REAL wiring
    method without constructing the whole daemon.
    """
    from daemon.manager import InstanceManager

    manager = InstanceManager.__new__(InstanceManager)
    manager.config = SimpleNamespace(skill_evolution=skill_evolution_cfg)
    # The wiring method touches a few optional collaborators; the
    # ``getattr`` guards mean "absent" is a supported shape.
    manager._skill_metrics_service = None
    return manager


@pytest.fixture(autouse=True)
def _observability_sandbox(tmp_path, request):
    """Point the sink at a SCRATCH dir for every test in this file.

    The teardown NEVER restores the override to ``None``: that
    re-exposed the checkout's REAL data dir for every later write (the
    order-dependent sink leak).  It restores the SAFE session redirect
    and runs the shared-path sentinel.
    """
    lane_test_start(request.node.nodeid)
    before = snapshot_real_sink_dir()
    observability.configure_sink(tmp_path)
    observability.reset_counters()
    try:
        yield tmp_path
    finally:
        restore_safe_sink_redirect()
        observability.reset_counters()
        lane_test_end(request.node.nodeid, before)


def test_m1_pin_manager_passes_the_configured_burst_cap_to_the_dispatcher():
    """M1 — a CONFIGURED cap must REACH the dispatcher (not the default).

    Named mutation: drop the ``fix_burst_cap=`` /
    ``fix_burst_window_seconds=`` arguments at the
    ``SkillJobDispatcher(...)`` construction in ``daemon/manager.py``
    (the pre-fix shape) → the dispatcher falls back to its module
    defaults (3 / 86400) and this pin goes RED.
    """
    manager = _manager_with_config(
        SimpleNamespace(
            evolution_lane_enabled=False,
            fix_cooldown_seconds=1800,
            fix_burst_cap=2,
            fix_burst_window_seconds=3600,
        )
    )

    manager.set_job_queue_service(_StubJobService())

    dispatcher = manager._skill_job_dispatcher
    assert dispatcher is not None, (
        "the manager did not wire a SkillJobDispatcher at all"
    )
    assert dispatcher._fix_burst_cap == 2, (
        "the CONFIGURED fix_burst_cap never reached the dispatcher — "
        f"got {dispatcher._fix_burst_cap!r}, expected 2 (the config "
        "knob and its env alias are dead)"
    )
    assert dispatcher._fix_burst_window_seconds == 3600, (
        "the CONFIGURED fix_burst_window_seconds never reached the "
        f"dispatcher — got {dispatcher._fix_burst_window_seconds!r}, "
        "expected 3600"
    )


def test_m1_pin_an_explicit_zero_cap_survives_the_wiring():
    """M1 — an EXPLICIT 0 must SURVIVE (the config: "0 disables the cap").

    Named mutation: coerce the value with a truthiness fallback
    (``int(x) if x else DEFAULT``) — the bug class the
    ``fix_cooldown_seconds`` wiring already had → a configured 0 turns
    back into the default 3 and this pin goes RED.
    """
    manager = _manager_with_config(
        SimpleNamespace(
            evolution_lane_enabled=False,
            fix_cooldown_seconds=1800,
            fix_burst_cap=0,
            fix_burst_window_seconds=0,
        )
    )

    manager.set_job_queue_service(_StubJobService())

    dispatcher = manager._skill_job_dispatcher
    assert dispatcher._fix_burst_cap == 0, (
        "a configured 0 must DISABLE the cap — the truthiness coercion "
        f"turned it back into {dispatcher._fix_burst_cap!r} (expected 0)"
    )
    assert dispatcher._fix_burst_window_seconds == 0


def test_m1_pin_absent_config_values_fall_back_to_the_dispatcher_defaults():
    """M1 — an ABSENT value falls back to the dispatcher's defaults.

    The explicit-0 rule must not become "0 when absent": a config
    object WITHOUT the fields (a hand-rolled test double, or an older
    config) must still produce the documented defaults.
    """
    manager = _manager_with_config(
        SimpleNamespace(
            evolution_lane_enabled=False,
            fix_cooldown_seconds=1800,
        )
    )

    manager.set_job_queue_service(_StubJobService())

    dispatcher = manager._skill_job_dispatcher
    assert dispatcher._fix_burst_cap == DEFAULT_FIX_BURST_CAP
    assert (
        dispatcher._fix_burst_window_seconds
        == DEFAULT_FIX_BURST_WINDOW_SECONDS
    )


# ── M2 — a designed refusal must not be an HTTP 500 ─────────────────────


class _CapRefusingDispatcher:
    """A dispatcher double whose ``dispatch_fix`` raises the REAL refusal."""

    async def dispatch_fix(self, **kwargs):
        raise SkillFixBurstCapRefused(
            "refused: burst cap — 3 FIX admissions for skill abc12345 "
            "within 86400s (cap 3). NO skill-keeper job was created."
        )


@pytest.fixture
def cap_refusal_client():
    """A TestClient over the REAL skills router with a refusing dispatcher.

    The router is imported INSIDE the fixture (not at module level): the
    router pulls the whole services package, and this pin file must not
    put an import-time side effect on the test session (see the
    M-1b import-purity pin below).
    """
    from daemon.routers.skills import _require_skill_dispatcher
    from daemon.routers.skills import router as skills_router

    app = FastAPI()
    app.include_router(skills_router, prefix="/api")
    app.dependency_overrides[_require_skill_dispatcher] = (
        lambda: _CapRefusingDispatcher()
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def test_m2_pin_burst_cap_refusal_is_not_a_5xx(cap_refusal_client):
    """M2 — the cap refusal must be a truthful non-5xx status.

    Named mutation: delete the ``except SkillFixBurstCapRefused``
    branch from ``post_fix`` (the pre-fix shape) → the exception falls
    into the generic ``except Exception`` → HTTP 500 + a traceback,
    and this pin goes RED.
    """
    response = cap_refusal_client.post(
        "/api/skills/skill-abc12345/fix",
        json={"issue_description": "the skill misleads the agent"},
    )

    assert response.status_code < 500, (
        "a DESIGNED refusal (the durable burst cap) must not be an "
        f"HTTP 5xx — got {response.status_code}: {response.text[:400]}"
    )
    assert response.status_code == 429, (
        "the burst-cap refusal must carry the truthful 429 status "
        f"(the lane is ON, the CAP refused) — got {response.status_code}"
    )
    body = response.json()
    detail = body.get("detail", body)
    assert "burst cap" in str(detail).lower(), (
        "the refusal response must carry the refusal REASON — got "
        f"{detail!r}"
    )


def test_m2_pin_burst_cap_refusal_does_not_leak_a_traceback(cap_refusal_client):
    """M2 — the refusal is a normal answer, not an internal error.

    Named mutation: restore the generic ``except Exception`` path for
    this refusal → the response body carries the 500 "Internal error"
    envelope → this pin goes RED.
    """
    response = cap_refusal_client.post(
        "/api/skills/skill-abc12345/fix",
        json={"issue_description": "the skill misleads the agent"},
    )

    text = response.text.lower()
    assert "internal error" not in text, (
        "the cap refusal must not be rendered as an internal error — "
        f"got {response.text[:400]}"
    )
    assert "traceback" not in text


# ── M3 — the dialect split of the E1 count must be PINNED ───────────────


def _compiled(is_postgres: bool) -> str:
    """Compile the E1 count statement for one dialect (NO DB contact).

    ``literal_binds=True`` inlines the bound values, so the compiled
    SQL shows the ACTUAL literals (``skill_evolution``, ``FIX``,
    ``$.skill_id``) instead of bind-parameter placeholders.
    """
    statement = build_fix_admission_count_statement(
        is_postgres=is_postgres,
        skill_id="skill-abc12345",
        since_iso="2026-11-01T00:00:00+00:00",
    )
    dialect = postgresql.dialect() if is_postgres else sqlite.dialect()
    return str(
        statement.compile(
            dialect=dialect, compile_kwargs={"literal_binds": True}
        )
    )


def test_m3_pin_postgresql_branch_uses_the_arrow_operator():
    """M3 — the PG branch must use ``->>`` (PostgreSQL is PRODUCTION).

    Named mutation: swap the two dialect branches (or delete the PG
    one) → the PG statement no longer contains ``->>`` and this pin
    goes RED.
    """
    sql = _compiled(is_postgres=True)

    assert "->>" in sql, (
        "the PostgreSQL branch of the E1 count must use the `->>` "
        f"operator — got: {sql}"
    )
    assert "json_extract" not in sql.lower(), (
        "the PostgreSQL branch must NOT use the SQLite spelling "
        f"`json_extract` — got: {sql}"
    )


def test_m3_pin_sqlite_branch_uses_json_extract():
    """M3 — the SQLite branch must use ``json_extract``.

    Named mutation: swap the two dialect branches (or delete the
    SQLite one) → the SQLite statement no longer contains
    ``json_extract`` and this pin goes RED.
    """
    sql = _compiled(is_postgres=False)

    assert "json_extract" in sql.lower(), (
        "the SQLite branch of the E1 count must use `json_extract` — "
        f"got: {sql}"
    )
    assert "->>" not in sql, (
        "the SQLite branch must NOT use the PostgreSQL `->>` operator "
        f"— got: {sql}"
    )


def test_m3_pin_both_branches_keep_the_same_filters():
    """M3 — the two branches differ ONLY in the JSON accessor.

    Named mutation: drop a filter from ONE branch only (e.g. the
    window filter) → the two statements no longer carry the same
    predicates and this pin goes RED.
    """
    pg_sql = _compiled(is_postgres=True).lower()
    sqlite_sql = _compiled(is_postgres=False).lower()

    for needle in (
        "skill_evolution",
        "fix",
        "created_at",
        "deleted_at",
    ):
        assert needle in pg_sql, f"the PG branch lost the {needle!r} filter"
        assert needle in sqlite_sql, (
            f"the SQLite branch lost the {needle!r} filter"
        )


# ── M-1b — the observability module must have NO import-time side effect ──
#
# The regression (fix wave 1, item 1): the module-level import of the
# metrics wiring vocabulary registered ~52 metric FAMILIES in the
# process-wide registry at import time (the wiring package's
# ``__init__`` calls ``register_w1_adapters`` / ``register_w2_adapters``
# / ``register_w3_adapters``). The exposition artifact is captured in a
# FRESH interpreter that imports the metrics modules — and this module
# sat on that import path — so the registry measured 18 families at
# base and 70 with the eager import, and the committed exposition
# artifact diverged.
#
# Named mutations: restore the module-level
# ``from daemon.services.metrics_query.wiring.vocab import
# ANOMALY_CATEGORIES`` → the subprocess reports the wiring package as
# imported → this pin goes RED.

def _run_in_fresh_interpreter(code: str) -> str:
    """Run ``code`` in a FRESH interpreter rooted at THIS tree."""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=120,
    )
    assert completed.returncode == 0, (
        "the subprocess probe failed:\n"
        f"stdout: {completed.stdout[-2000:]}\n"
        f"stderr: {completed.stderr[-2000:]}"
    )
    return completed.stdout.strip()


def test_m1b_pin_importing_the_observability_module_registers_no_families():
    """M-1b — importing the module must register NOTHING.

    Named mutation: restore the module-level wiring-vocabulary import
    → the probe prints True → this pin goes RED.
    """
    output = _run_in_fresh_interpreter(
        "import sys\n"
        "import daemon.services.skill_capture_observability\n"
        "print('daemon.services.metrics_query.wiring' in sys.modules)\n"
    )

    assert output.splitlines()[-1] == "False", (
        "importing daemon.services.skill_capture_observability pulled in "
        "the metrics wiring package — its __init__ REGISTERS the phase's "
        "metric families at import time, which diverged the committed "
        "exposition artifact (registry 18 -> 70)"
    )


def test_m1b_pin_the_fallback_vocabulary_matches_the_canonical_one():
    """M-1b — the frozen fallback must equal the canonical vocabulary.

    The vocabulary is resolved lazily (the canonical list lives in the
    METRICS lane); the local fallback is what the module uses when the
    metrics package is unavailable. A drift between the two would fold
    a valid category into ``other``.

    Named mutation: add/remove a category in EITHER list → this pin
    goes RED.
    """
    output = _run_in_fresh_interpreter(
        "from daemon.services.skill_capture_observability import (\n"
        "    _ANOMALY_CATEGORIES_FALLBACK,\n"
        ")\n"
        "from daemon.services.metrics_query.wiring.vocab import (\n"
        "    ANOMALY_CATEGORIES,\n"
        ")\n"
        "print(tuple(ANOMALY_CATEGORIES) == tuple(_ANOMALY_CATEGORIES_FALLBACK))\n"
    )

    assert output.splitlines()[-1] == "True", (
        "the local fallback vocabulary has DRIFTED from the canonical "
        "one (daemon/services/metrics_query/wiring/vocab.py) — a valid "
        "category would be folded into 'other'"
    )
