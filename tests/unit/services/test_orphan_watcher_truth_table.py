"""The OWNERSHIP TRUTH TABLE for the OrphanWatcher engine pilot.

Every row is resolved by calling the REAL production predicates — not a
re-implementation:

* ``engine_owns_execution(environ, engine_config)``
  (``daemon/services/orphan_watcher_sweep.py``) — the ONE ownership
  conjunct. BOTH api.py sites consult it: the legacy construct site
  (``daemon/api.py`` — "legacy loop SUPPRESSED") and the engine boot block
  (``daemon/api.py`` — ``set_executor``).
* ``EngineExecutionLoop._resolve_switch(spec)`` — the engine's REAL
  kill-switch ladder (Tier 1 per-job key → Tier 2 ``ENSEMBLE_ENGINE_ENABLED``).
* ``EngineExecutionLoop.shadow_active`` — Tier 3 (``ENSEMBLE_ENGINE_SHADOW_MODE``).

WHAT THIS REFUTES
-----------------
The Phase-0 study asserted: *"flag ON + shadow ON ⇒ dead sweep lane (legacy
suppressed while the engine refuses to execute it)"*. That claim is
**REFUTED by the landed code**: the legacy suppression is gated on the FULL
conjunct (flag ∧ enabled ∧ ¬shadow), so with shadow ON the conjunct is
FALSE — the legacy loop is NOT suppressed, and the executor is NOT attached.
Both sites use the SAME predicate, so there is no window in which the
legacy loop is suppressed while the engine refuses to execute.

The rows below make that machine-checkable: no row resolves to BOTH owners
and no row resolves to NEITHER.

FLAG POLARITY — READ THIS BEFORE USING THE TABLE
-------------------------------------------------
"flag OFF" is ambiguous in the environment and the two readings DIFFER at
the engine's dispatch layer (they agree at the OWNER layer):

* flag UNSET (the SHIPPED default) ⇒ ``job_killed`` is False (an ABSENT
  per-job key is "no switch active", ``killswitch.py:103-105``) ⇒ the job
  reaches the shadow gate ⇒ ``SKIPPED_SHADOW`` — the SAME stamp the other
  11 observer specs record.
* flag explicitly "0" ⇒ ``job_killed`` is True (``killswitch.py:108-110``)
  ⇒ ``SKIPPED_KILL_SWITCHED`` (Tier 1 fires BEFORE the shadow gate).

Both readings resolve to the same OWNER (legacy). The table carries both,
because the flip ceremony must know which one it is reading.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from daemon.services.engine.boot import build_observer_specs
from daemon.services.engine.execution import EngineExecutionLoop
from daemon.services.engine.killswitch import EngineSwitchResolver
from daemon.services.orphan_watcher_sweep import (
    ORPHAN_WATCHER_ENGINE_FLAG,
    ORPHAN_WATCHER_ENGINE_JOB_ID,
    engine_owns_execution,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
API_SRC = (REPO_ROOT / "daemon" / "api.py").read_text()


# ── real objects ────────────────────────────────────────────────────────


def _engine_cfg(enabled: bool = True, shadow_mode: bool = True) -> Any:
    """A stand-in for ``config.engine`` (the real ``EngineConfig`` fields)."""
    return SimpleNamespace(enabled=enabled, shadow_mode=shadow_mode)


def _environ(
    flag: str | None,
    *,
    shadow: bool,
    enabled: bool,
) -> dict[str, str]:
    """The environment for one row. ``flag=None`` means ABSENT (unset)."""
    env: dict[str, str] = {
        "ENSEMBLE_ENGINE_SHADOW_MODE": "1" if shadow else "0",
        "ENSEMBLE_ENGINE_ENABLED": "1" if enabled else "0",
    }
    if flag is not None:
        env[ORPHAN_WATCHER_ENGINE_FLAG] = flag
    return env


def _orphan_spec() -> Any:
    """The REAL orphan-watcher spec built by ``build_observer_specs``."""

    class _Services:
        orphan_watcher_sweep_interval_seconds = 90

    class _EngineCfg:
        enabled = True
        shadow_mode = True

    cfg = SimpleNamespace(services=_Services(), engine=_EngineCfg())
    return next(
        s
        for s in build_observer_specs(cfg)
        if s.id == ORPHAN_WATCHER_ENGINE_JOB_ID
    )


def _resolve_row(flag: str | None, *, shadow: bool, enabled: bool) -> dict:
    """Resolve one row using ONLY the real production predicates.

    Returns the resolved owner, the engine's dispatch outcome, and the two
    api.py site decisions.
    """
    env = _environ(flag, shadow=shadow, enabled=enabled)
    cfg = _engine_cfg(enabled=enabled, shadow_mode=shadow)

    # ── the ONE ownership conjunct (both api.py sites) ──────────────
    owns = engine_owns_execution(environ=env, engine_config=cfg)

    # ── api.py site 1: the legacy construct site ────────────────────
    legacy_suppressed = owns
    legacy_runs = not legacy_suppressed

    # ── api.py site 2: the executor attachment ──────────────────────
    executor_attached = owns

    # ── the engine's dispatch ladder (Tier 1 + Tier 2 + Tier 3) ─────
    resolver = EngineSwitchResolver(resolver=env.get)
    fake_engine = SimpleNamespace(shadow_mode=shadow, _switch_resolver=resolver)
    loop = EngineExecutionLoop(fake_engine)
    spec = _orphan_spec()
    should_run, kill_reason = loop._resolve_switch(spec)
    if not should_run:
        outcome = "SKIPPED_KILL_SWITCHED"
    elif loop.shadow_active or not executor_attached:
        outcome = "SKIPPED_SHADOW"
    else:
        outcome = "SUCCEEDED"

    # ── the resolved owner ───────────────────────────────────────────
    engine_executes = outcome == "SUCCEEDED"
    if legacy_runs and engine_executes:
        owner = "BOTH"
    elif legacy_runs:
        owner = "legacy"
    elif engine_executes:
        owner = "engine"
    else:
        owner = "NEITHER"

    return {
        "owns": owns,
        "legacy_runs": legacy_runs,
        "legacy_suppressed": legacy_suppressed,
        "executor_attached": executor_attached,
        "outcome": outcome,
        "owner": owner,
        "kill_reason": kill_reason,
    }


# ── THE TABLE (8 combinations: flag × shadow × engine-enabled) ─────────

TRUTH_TABLE = [
    # (flag, shadow, enabled, expected owner, expected engine outcome)
    ("1", True, True, "legacy", "SKIPPED_SHADOW"),
    ("1", True, False, "legacy", "SKIPPED_KILL_SWITCHED"),
    ("1", False, True, "engine", "SUCCEEDED"),
    ("1", False, False, "legacy", "SKIPPED_KILL_SWITCHED"),
    ("0", True, True, "legacy", "SKIPPED_KILL_SWITCHED"),
    ("0", True, False, "legacy", "SKIPPED_KILL_SWITCHED"),
    ("0", False, True, "legacy", "SKIPPED_KILL_SWITCHED"),
    ("0", False, False, "legacy", "SKIPPED_KILL_SWITCHED"),
]


@pytest.mark.parametrize(
    "flag,shadow,enabled,expected_owner,expected_outcome",
    TRUTH_TABLE,
    ids=[
        f"flag{flag}-shadow{'on' if s else 'off'}-engine{'on' if e else 'off'}"
        for flag, s, e, _o, _x in TRUTH_TABLE
    ],
)
def test_truth_table_row(
    flag, shadow, enabled, expected_owner, expected_outcome
):
    """EVERY row of the 8-combination table, resolved by the real code."""
    row = _resolve_row(flag, shadow=shadow, enabled=enabled)
    assert row["owner"] == expected_owner, (
        f"row mismatch for flag={flag} shadow={shadow} enabled={enabled}: "
        f"owner={row['owner']} expected={expected_owner}"
    )
    assert row["outcome"] == expected_outcome, (
        f"engine outcome mismatch for flag={flag} shadow={shadow} "
        f"enabled={enabled}: {row['outcome']} != {expected_outcome}"
    )
    # The two api.py sites are the SAME predicate — never divergent.
    assert row["legacy_suppressed"] == row["executor_attached"]


def test_truth_table_never_both_and_never_neither():
    """THE INVARIANT: exactly one owner in every row. No dead lane."""
    owners = []
    for flag, shadow, enabled, _o, _x in TRUTH_TABLE:
        row = _resolve_row(flag, shadow=shadow, enabled=enabled)
        owners.append((flag, shadow, enabled, row["owner"]))
        assert row["owner"] in ("legacy", "engine"), (
            "a row resolved to BOTH or NEITHER — that is a dead lane or a "
            f"double execution: {owners[-1]}"
        )
    # Exactly one row is engine-owned: flag ON ∧ shadow OFF ∧ engine enabled.
    engine_rows = [o for o in owners if o[3] == "engine"]
    assert len(engine_rows) == 1
    assert engine_rows[0][:3] == ("1", False, True)


def test_the_studys_dead_lane_claim_is_refuted():
    """flag ON + shadow ON ⇒ the legacy loop KEEPS RUNNING (no dead lane).

    This is the contested claim from the Phase-0 study. The landed code
    suppresses the legacy loop ONLY on the full conjunct, so shadow ON
    leaves the legacy loop running — and the engine merely observes.
    """
    row = _resolve_row("1", shadow=True, enabled=True)
    assert row["owns"] is False
    assert row["legacy_suppressed"] is False, (
        "the legacy loop was suppressed with shadow ON — the study's dead-lane "
        "claim would be TRUE and this would be a BLOCKING defect"
    )
    assert row["legacy_runs"] is True
    assert row["executor_attached"] is False
    assert row["owner"] == "legacy"
    assert row["outcome"] == "SKIPPED_SHADOW"


def test_flag_unset_is_the_shipped_default_and_differs_from_explicit_zero():
    """UNSET ≠ explicit "0" at the dispatch layer (same owner, different stamp).

    The shipped default is UNSET (the flag family is never set by the
    product). At UNSET the orphan spec records ``SKIPPED_SHADOW`` — the
    SAME stamp as the other 11 observer specs. At an explicit "0" it
    records ``SKIPPED_KILL_SWITCHED`` (Tier 1 fires before the shadow gate).
    """
    unset = _resolve_row(None, shadow=True, enabled=True)
    explicit_zero = _resolve_row("0", shadow=True, enabled=True)

    assert unset["owner"] == explicit_zero["owner"] == "legacy"
    assert unset["outcome"] == "SKIPPED_SHADOW"
    assert explicit_zero["outcome"] == "SKIPPED_KILL_SWITCHED"
    assert unset["executor_attached"] is False
    assert explicit_zero["executor_attached"] is False


def test_shipped_default_posture_is_legacy_owns():
    """The shipped posture (flag unset + engine enabled + shadow on)."""
    row = _resolve_row(None, shadow=True, enabled=True)
    assert row["owner"] == "legacy"
    assert row["legacy_runs"] is True
    assert row["executor_attached"] is False


def test_ownership_conjunct_fails_to_legacy_without_an_engine_config():
    """No engine posture ⇒ no ownership claim (fail-to-legacy)."""
    assert engine_owns_execution(environ={"x": "1"}, engine_config=None) is False


def test_both_api_sites_consult_the_same_ownership_predicate():
    """SOURCE pin: the construct site and the boot block share ONE predicate.

    If either site re-implemented the decision, the two could diverge and
    the "exactly one owner" invariant would be an assumption, not a fact.
    """
    calls = API_SRC.count("engine_owns_execution(")
    # EXACTLY TWO call sites: the legacy construct site and the engine boot
    # block (the import line has no parenthesis, so it is not counted). A
    # THIRD call site would be a new, potentially divergent decision point
    # and must RED this pin.
    assert calls == 2, (
        "expected exactly two engine_owns_execution( call sites in "
        f"daemon/api.py (legacy construct site + engine boot block) — found "
        f"{calls}; a third site would be a divergent re-implementation"
    )
    # The construct site (legacy suppression) and the boot block (executor
    # attachment) both call it with the engine's own config object.
    assert "elif engine_owns_execution(engine_config=getattr(config, \"engine\", None)):" in API_SRC
    assert "if engine_owns_execution(engine_config=_engine_cfg):" in API_SRC


def test_the_flag_is_the_only_switch_and_the_default_is_off():
    """The per-job flag is the ONE family member; unset ⇒ legacy owns."""
    assert ORPHAN_WATCHER_ENGINE_FLAG == "SERVICES_ORPHAN_WATCHER_SWEEP_USE_ENGINE"
    spec = _orphan_spec()
    assert spec.kill_switch_key == ORPHAN_WATCHER_ENGINE_FLAG
    # Unset ⇒ the flag family reports OFF (dualpath default is OFF).
    from daemon.services.engine.dualpath import ownership_for

    assert ownership_for("orphan_watcher_sweep", {}).engine_owns is False
