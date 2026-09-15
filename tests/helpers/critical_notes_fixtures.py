"""Shared fixtures and builders for critical-notes test files.

Three test files duplicate the same autouse module-state reset pattern,
and ``test_critical_notes_lifecycle.py`` repeats a 4-line note-add
invocation ~16 times. This module consolidates both:

* :func:`reset_module_state_fixture` — autouse fixture that resets BOTH
  module states (tool config + render config) before AND after each
  test, replacing the 3 autouse copies across:

    * ``tests/unit/tools/test_critical_notes_lifecycle.py``
    * ``tests/unit/test_critical_notes_config.py``
    * ``tests/unit/services/test_critical_notes_render_phase1.py``

* :func:`make_note` — small builder used by the lifecycle tests to
  invoke ``project_cn_add`` with sensible defaults and override hooks.

The render config reset is necessary because the ``critical_notes``
section boots TWO module-level caches (the tool-side ``_CORE_CAP /
_REFERENCE_MAX / _STALE_DAYS`` and the render-side ``_STALE_DAYS /
_CORE_CAP``); tests that touch only one side still leak into the other
when run in the same process. Resetting both keeps every test
deterministic regardless of run order.

Importable from all three test trees via the ``tests.helpers`` package —
no ``sys.path`` manipulation needed (pytest adds the repo root to
``sys.path`` during test discovery, and ``tests/helpers/__init__.py``
declares the package; mirrors the ``send_message_fixtures`` /
``pause_report_orphan_scenarios`` / ``fake_instance_repo`` precedent).
"""

from __future__ import annotations

from typing import Any

import pytest


def reset_critical_notes_module_state() -> None:
    """Reset BOTH critical-notes module caches to documented defaults.

    Both modules expose a ``reset_*`` helper; calling both here keeps
    every test deterministic regardless of which side it actually
    exercises (a test on the tool side that does not touch the render
    cache still leaks if the previous test did, and vice versa).
    """
    from daemon.tools.critical_notes import reset_critical_notes_config

    # Lazy import — the render cache lives in context_messages (no
    # dedicated render module); if the symbol is missing (older code
    # line) we still reset the tool side.
    try:
        from daemon.services.context_messages import (
            reset_critical_notes_render_config,
        )
        reset_critical_notes_render_config()
    except ImportError:
        pass

    reset_critical_notes_config()


@pytest.fixture(autouse=True)
def reset_module_state():
    """Autouse fixture: reset critical-notes module state pre+post test.

    Drop-in replacement for the 3 autouse copies previously scattered
    across the lifecycle / config / render test files. Tests that need
    to install non-default config install it AFTER this fixture has
    reset; the post-test reset cleans up.
    """
    reset_critical_notes_module_state()
    yield
    reset_critical_notes_module_state()


def make_note(
    tools: dict[str, Any],
    *,
    project_id: str,
    category: str = "risk",
    priority: str = "medium",
    summary: str = "Note added via make_note",
    reference: str | None = None,
    detail_ref: str | None = None,
    entry_id: str | None = None,
) -> dict[str, Any]:
    """Invoke ``project_cn_add`` with sensible defaults; return the result dict.

    Builder for the ~16 repeated 4-line note-add invocations in
    ``tests/unit/tools/test_critical_notes_lifecycle.py``. Tests pass
    only the fields they care about (override via kwargs); everything
    else uses a default that produces a valid ACTIVE row. Test
    assertions are unchanged — they still inspect the returned dict.
    """
    payload: dict[str, Any] = {
        "project_id": project_id,
        "category": category,
        "priority": priority,
        "summary": summary,
    }
    if reference is not None:
        payload["reference"] = reference
    if detail_ref is not None:
        payload["detail_ref"] = detail_ref
    if entry_id is not None:
        payload["entry_id"] = entry_id
    return tools["project_cn_add"].invoke(payload)
