"""F1 — the boot posture line must tell the TRUTH about the USE_ENGINE family.

The finding (F1): ``daemon/api.py``'s boot posture line printed a STATIC
``USE_ENGINE all unset`` claim even when a ``SERVICES_*_USE_ENGINE`` flag was
actually set — a boot log that lied about the estate's execution ownership.

``describe_use_engine_flags`` (``daemon/services/engine/boot.py``) renders the
REAL state: the distinct ``kill_switch_key`` names carried by the registered
observer specs, and which of those carry a value right now. It is a pure
function over an injected ``environ`` so both postures are provable without
touching the ambient environment.

Shapes:

* every key unset ⇒ ``"all unset (0/N)"`` (the shipped dormant posture)
* some keys set ⇒ ``"SERVICES_A_USE_ENGINE=1, SERVICES_B_USE_ENGINE=0 (2/N)"``
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from daemon.services.engine.boot import build_observer_specs, describe_use_engine_flags

API = Path(__file__).resolve().parents[4] / "daemon" / "api.py"
API_SRC = API.read_text(encoding="utf-8")


class _Services:
    watch_reconcile_sweep_interval_seconds = 300
    mission_strand_heal_interval_seconds = 300
    report_delivery_recovery_interval_seconds = 300
    sources_gc_interval_seconds = 3600
    eligible_pending_sweep_interval_seconds = 90
    orphan_watcher_sweep_interval_seconds = 90
    job_lock_sweep_interval_seconds = 90
    tmp_image_cleanup_interval_seconds = 3600
    plane_sync_watchdog_interval_seconds = 300
    stale_pause_sweep_interval_seconds = 300
    waiting_children_watchdog_interval_seconds = 3600
    drift_reconcile_interval_seconds = 300


class _Cfg:
    services = _Services()


def _specs() -> tuple[Any, ...]:
    return build_observer_specs(_Cfg())


def _keys() -> list[str]:
    return sorted({s.kill_switch_key for s in _specs() if s.kill_switch_key})


# ── the dormant posture (CS-1 / AMD-1(b): all unset) ─────────────────────

def test_dormant_posture_reports_all_unset():
    """The shipped posture renders truthfully: every flag unset."""
    keys = _keys()
    assert len(keys) >= 1
    rendered = describe_use_engine_flags(_specs(), environ={})
    assert rendered == f"all unset (0/{len(keys)})"


# ── a flipped posture must be reported, not denied ───────────────────────

def test_set_flag_is_named_in_the_posture():
    """A flag that IS set must appear in the rendered posture."""
    key = _keys()[0]
    rendered = describe_use_engine_flags(_specs(), environ={key: "1"})
    assert rendered == f"{key}=1 (1/{len(_keys())})"
    # The lie is gone: the rendering no longer says "all unset".
    assert "all unset" not in rendered


def test_set_flag_that_is_off_is_still_named():
    """An explicitly OFF flag is still SET — the posture must not hide it."""
    key = _keys()[0]
    rendered = describe_use_engine_flags(_specs(), environ={key: "0"})
    assert rendered == f"{key}=0 (1/{len(_keys())})"


def test_multiple_set_flags_are_sorted_and_counted():
    keys = _keys()
    rendered = describe_use_engine_flags(
        _specs(), environ={keys[1]: "1", keys[0]: "0"}
    )
    assert rendered == f"{keys[0]}=0, {keys[1]}=1 (2/{len(keys)})"


def test_absent_and_empty_values_are_distinguished():
    """An EMPTY value is still a SET variable (present, empty)."""
    key = _keys()[0]
    rendered = describe_use_engine_flags(_specs(), environ={key: ""})
    assert rendered == f"{key}= (1/{len(_keys())})"


def test_value_is_collapsed_and_truncated():
    """Operator-supplied text must never break the log line."""
    key = _keys()[0]
    rendered = describe_use_engine_flags(
        _specs(), environ={key: "a\nb   c" + "x" * 80}
    )
    assert "\n" not in rendered
    assert rendered.startswith(f"{key}=a b c")
    assert len(rendered) < len(key) + 60


# ── the PRODUCTION seam: the default (NO injected environ) ──────────────

def test_production_seam_reads_the_ambient_environment(monkeypatch):
    """The PRODUCTION default seam must read ``os.environ`` — not a stub.

    Every pin above injects ``environ=`` EXPLICITLY, so a mutation of the
    default seam in ``daemon/services/engine/boot.py``
    (``env = os.environ if environ is None else environ`` → a dead
    ``env = {}``) would leave all of them GREEN while the real boot
    posture line lied again — the exact defect class F1 closed. This pin
    calls the function the way PRODUCTION calls it (NO ``environ``
    argument) with the flag set in the AMBIENT environment.
    """
    key = "SERVICES_ORPHAN_WATCHER_SWEEP_USE_ENGINE"
    assert key in _keys()
    # Deterministic count: every OTHER flag of the family is absent.
    for other in _keys():
        monkeypatch.delenv(other, raising=False)
    monkeypatch.setenv(key, "1")

    rendered = describe_use_engine_flags(_specs())  # production default seam

    assert key in rendered
    assert rendered == f"{key}=1 (1/{len(_keys())})"
    assert "all unset" not in rendered


# ── the wiring: the boot line must USE the dynamic rendering ───────────

def test_boot_posture_line_uses_the_dynamic_rendering():
    """The line carries the dynamic field and the STATIC claim is gone."""
    assert "use_engine_flags=%s;" in API_SRC
    assert "describe_use_engine_flags(_observer_specs)" in API_SRC
    # The static lie must not come back.
    assert "USE_ENGINE all unset" not in API_SRC


def test_exactly_one_posture_line_survives():
    """The 'exactly ONE posture INFO line' property (AC4) is preserved."""
    assert API_SRC.count('"engine boot posture: enabled=%s shadow=%s registrations=%d') == 1
