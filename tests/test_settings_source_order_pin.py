"""db-backed-settings Phase 4 (t5) — source-order pin (architect Ruling 6).

Text/byte-offset pin on ``daemon/api.py``: the boot-apply call
``apply_db_settings_overrides(`` must appear STRICTLY BETWEEN the
lifespan-scoped ``load_config()`` call and the ``InstanceManager(``
construction — the load-order invariant that makes the DB/env override
effective for the semaphore the manager builds at
``daemon/manager.py:555`` with zero downstream changes.

Disambiguation (the reason this is a byte-offset pin and not a bare
grep): ``load_config()`` ALSO appears at module scope near the bottom
of the file (``auth_config = load_config().auth``, ~:3674 — the
auth-sub-app builder) and in comments. Anchoring on the wrong
occurrence would make the pin vacuous. The anchors:

* lifespan occurrence — ``"\\n    config = load_config()"`` (4-space
  indented, function-body form; the module-level form has NO leading
  indentation, so this byte string cannot match it);
* module-level occurrence — ``"auth_config = load_config()"`` (must
  sit AFTER the manager construction — proves the lifespan anchor is
  the early one);
* the pin call — ``"apply_db_settings_overrides("`` (open-paren
  disambiguates the CALL from the import line and comment mentions,
  which carry no paren);
* the manager construction — ``"InstanceManager("``.

Fails loud if the call moves outside the window, disappears, is
duplicated, or any anchor drifts (find() → -1 breaks the ordering
assertions).
"""

from __future__ import annotations

from pathlib import Path

import daemon

API_PY = Path(daemon.__file__).parent / "api.py"

LIFESPAN_LOAD_CONFIG = "\n    config = load_config()"
MODULE_LEVEL_LOAD_CONFIG = "auth_config = load_config()"
PIN_CALL = "apply_db_settings_overrides("
MANAGER_CONSTRUCTION = "InstanceManager("


def _api_source() -> str:
    return API_PY.read_text(encoding="utf-8")


def test_boot_apply_sits_between_load_config_and_instance_manager():
    src = _api_source()

    lifespan_pos = src.find(LIFESPAN_LOAD_CONFIG)
    module_level_pos = src.find(MODULE_LEVEL_LOAD_CONFIG)
    pin_pos = src.find(PIN_CALL)
    manager_pos = src.find(MANAGER_CONSTRUCTION)

    # All four anchors resolve — a drifted anchor fails loud, not vacuous.
    assert lifespan_pos != -1, "lifespan `config = load_config()` anchor not found"
    assert module_level_pos != -1, "module-level `auth_config = load_config()` anchor not found"
    assert pin_pos != -1, "apply_db_settings_overrides( call not found in api.py"
    assert manager_pos != -1, "InstanceManager( construction not found in api.py"

    # THE pin: lifespan load_config  <  boot-apply  <  InstanceManager.
    assert (
        lifespan_pos < pin_pos
    ), "apply_db_settings_overrides must run AFTER load_config() in the lifespan"
    assert (
        pin_pos < manager_pos
    ), "apply_db_settings_overrides must run BEFORE InstanceManager(...) — "
    "the manager reads config.limits.llm_concurrency at __init__ (manager.py:555)"

    # Disambiguation: the OTHER load_config() occurrence (module-level,
    # auth sub-app) sits AFTER the manager construction — so the pin's
    # lower bound above is provably the LIFESPAN occurrence, and a pin
    # anchored against the module-level occurrence (which would sit
    # after InstanceManager) cannot pass this file.
    assert (
        module_level_pos > manager_pos
    ), "module-level load_config occurrence expected after InstanceManager (anchor drift?)"


def test_boot_apply_call_site_exactly_once():
    """One wiring, one call — a second call site (double-apply) fails loud."""
    src = _api_source()
    assert src.count(PIN_CALL) == 1, (
        f"expected exactly one {PIN_CALL!r} call in daemon/api.py, "
        f"found {src.count(PIN_CALL)}"
    )
