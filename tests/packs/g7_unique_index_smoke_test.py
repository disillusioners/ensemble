#!/usr/bin/env python3
"""G7 Unique Index Startup Smoke Test.

Regression test for the bug where
``InstanceManager._ensure_blueprint_g7_unique_index`` in daemon/manager.py
referenced ``project.id`` instead of ``project.project_id``. The Project
SQLModel has ``project_id`` as primary key and no ``id`` attribute, so the
old code raised ``AttributeError: 'Project' object has no attribute 'id'``
at daemon startup, wrapping the whole startup path in an except block.

This test:
1. Builds fake Project objects (MagicMock spec=['project_id']) that have
   ``project_id`` set and explicitly NO ``id`` attribute — exactly the shape
   of a real Project row.
2. Binds the *actual* InstanceManager._ensure_blueprint_g7_unique_index method
   to a fully-mocked manager object so we exercise the real production code
   without instantiating the heavy __init__.
3. Calls it with a non-empty project list and asserts:
   - No AttributeError is raised.
   - auto_dedup_cores is called once per project with the correct project_id.
4. Calls it with an empty project list and asserts graceful handling
   (no calls to auto_dedup_cores, no exception).

Dual-layer timeout:
- Layer 1: outer `timeout 300 bash tests/packs/g7_unique_index_smoke_test.sh`
- Layer 2: signal.alarm(120) below interrupts hung tests at 2 minutes
"""
import signal
import sys
import time
import traceback
from unittest.mock import MagicMock

# ── Inner guard: 2-minute hard cap for a unit-sized pack ─────────────
def _on_timeout(signum, frame):
    print("RESULT: TIMEOUT", flush=True)
    sys.exit(124)

signal.signal(signal.SIGALRM, _on_timeout)
signal.alarm(120)

START = time.monotonic()

try:
    from daemon.manager import InstanceManager

    # When accessed via the class, this is already an unbound function
    # in Python 3. We pass our mock as `self` to exercise the real code.
    g7_fn = InstanceManager._ensure_blueprint_g7_unique_index

    # ── Test 1: non-empty project list ────────────────────────────────
    # MagicMock(spec=['project_id']) restricts attribute access to the
    # names listed — accessing .id will raise AttributeError. This is
    # the exact shape of the real Project SQLModel (project_id primary
    # key, no id attribute).
    fake_proj_a = MagicMock(spec=["project_id"])
    fake_proj_a.project_id = "proj-aaa"
    fake_proj_b = MagicMock(spec=["project_id"])
    fake_proj_b.project_id = "proj-bbb"

    manager_a = MagicMock()
    manager_a._blueprint_repo = MagicMock()
    manager_a._blueprint_repo.auto_dedup_cores = MagicMock(return_value=0)
    manager_a._project_repository = MagicMock()
    manager_a._project_repository.list_projects = MagicMock(
        return_value=[fake_proj_a, fake_proj_b]
    )
    # _engine.begin() is used to run the DDL; needs a context-manager.
    engine_cm = MagicMock()
    engine_cm.__enter__ = MagicMock(return_value=MagicMock())
    engine_cm.__exit__ = MagicMock(return_value=False)
    manager_a._engine = MagicMock()
    manager_a._engine.begin.return_value = engine_cm

    g7_fn(manager_a)  # would raise AttributeError pre-fix

    assert manager_a._blueprint_repo.auto_dedup_cores.call_count == 2, (
        f"expected 2 dedup calls, got "
        f"{manager_a._blueprint_repo.auto_dedup_cores.call_count}"
    )
    called_with = [
        c.args[0] for c in manager_a._blueprint_repo.auto_dedup_cores.call_args_list
    ]
    assert called_with == ["proj-aaa", "proj-bbb"], (
        f"dedup called with wrong project_ids: {called_with}"
    )

    # ── Test 2: empty project list (graceful no-op) ──────────────────
    manager_b = MagicMock()
    manager_b._blueprint_repo = MagicMock()
    manager_b._blueprint_repo.auto_dedup_cores = MagicMock(return_value=0)
    manager_b._project_repository = MagicMock()
    manager_b._project_repository.list_projects = MagicMock(return_value=[])
    engine_cm2 = MagicMock()
    engine_cm2.__enter__ = MagicMock(return_value=MagicMock())
    engine_cm2.__exit__ = MagicMock(return_value=False)
    manager_b._engine = MagicMock()
    manager_b._engine.begin.return_value = engine_cm2

    g7_fn(manager_b)  # must not raise

    assert manager_b._blueprint_repo.auto_dedup_cores.call_count == 0, (
        "auto_dedup_cores should not be called when there are no projects"
    )

    # ── Test 3: missing repos (defensive paths) ──────────────────────
    # If _blueprint_repo is None or _project_repository is missing, the
    # function must skip the dedup step without crashing. Real startup
    # sometimes hits these states during bootstrap.
    manager_c = MagicMock(spec=["_blueprint_repo", "_engine"])
    manager_c._blueprint_repo = None
    manager_c._engine = MagicMock()
    manager_c._engine.begin.return_value = engine_cm
    g7_fn(manager_c)  # must not raise

    runtime = time.monotonic() - START
    print(f"=== Test Pack: G7 Unique Index Startup Smoke ===", flush=True)
    print(f"RESULT: PASS", flush=True)
    print(f"Tests run: 3 | Passed: 3 | Failed: 0", flush=True)
    print(f"Actual runtime: {runtime:.2f}s", flush=True)
    sys.exit(0)

except AssertionError as e:
    runtime = time.monotonic() - START
    print(f"=== Test Pack: G7 Unique Index Startup Smoke ===", flush=True)
    print(f"RESULT: FAIL", flush=True)
    print(f"Assertion failed: {e}", flush=True)
    print(f"Tests run: 3 | Passed: 0 | Failed: 1+", flush=True)
    print(f"Actual runtime: {runtime:.2f}s", flush=True)
    traceback.print_exc()
    sys.exit(1)
except AttributeError as e:
    runtime = time.monotonic() - START
    print(f"=== Test Pack: G7 Unique Index Startup Smoke ===", flush=True)
    print(f"RESULT: FAIL — AttributeError regression: {e}", flush=True)
    print(f"The fix appears to be reverted or missing.", flush=True)
    print(f"Actual runtime: {runtime:.2f}s", flush=True)
    traceback.print_exc()
    sys.exit(1)
except Exception as e:  # noqa: BLE001 — surface any unexpected failure
    runtime = time.monotonic() - START
    print(f"=== Test Pack: G7 Unique Index Startup Smoke ===", flush=True)
    print(f"RESULT: FAIL — unexpected {type(e).__name__}: {e}", flush=True)
    print(f"Actual runtime: {runtime:.2f}s", flush=True)
    traceback.print_exc()
    sys.exit(1)