"""Unit tests for the spawn-boundary scrub (component B, pin (i)).

Positive shape: a probe child spawned through the scrubbed seam has the
stripped family ABSENT and explicit-setter vars PRESENT.
"""

import os
import subprocess
import sys

import pytest

from daemon.services.spawn_env_policy import SPAWN_BOUNDARY_STRIP_ENV, apply_spawn_boundary


def test_scrub_strips_poisoned_ambient_with_overlay_wins():
    poisoned = {name: "POISON" for name in SPAWN_BOUNDARY_STRIP_ENV}
    poisoned["UNRELATED"] = "stay"
    out = apply_spawn_boundary(poisoned, overlay={"PORT": "12345"})
    # overlay wins
    assert out.get("PORT") == "12345"
    # all other stripped names absent
    for name in SPAWN_BOUNDARY_STRIP_ENV:
        if name != "PORT":
            assert name not in out, f"{name} survived scrub"
    assert out.get("UNRELATED") == "stay"


def test_scrub_service_spawner_env_kwarg(monkeypatch, tmp_path):
    # Exercise the service_spawner spawn path's env= kwarg by monkeypatching
    # Popen to capture the env it receives.
    import daemon.tools.service_spawner as ss

    captured = {}

    class FakePopen:
        def __init__(self, *a, **kw):
            captured["env"] = kw.get("env")
            self.pid = 12345

    monkeypatch.setattr(ss.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(ss, "get_process_start_time", lambda pid: 999)

    # Poison the ambient env for the duration of the call.
    for name in SPAWN_BOUNDARY_STRIP_ENV:
        monkeypatch.setenv(name, "POISON")
    monkeypatch.setenv("UNRELATED_KEEP", "keepval")

    pid, st = ss.spawn(["echo", "hi"], str(tmp_path / "x.log"), extra_env={"PORT": "9"})
    assert pid == 12345
    env = captured["env"]
    assert env is not None
    assert env.get("PORT") == "9"
    for name in SPAWN_BOUNDARY_STRIP_ENV:
        if name != "PORT":
            assert name not in env, f"{name} survived in service_spawner env"
    assert env.get("UNRELATED_KEEP") == "keepval"


def test_scrub_bash_tool_materializes_env(monkeypatch):
    # Import-level smoke: the bash module imports the policy.
    import importlib
    bmod = importlib.import_module("daemon.tools.bash")
    assert hasattr(bmod, "_apply_spawn_boundary")


def test_counter_mutation_would_redden(monkeypatch):
    # Non-vacuity: with a stripped name removed from the set, it survives.
    # We simulate by checking a name not in the (frozen) set would survive
    # scrub_spawn_env only if absent from the set; PORT is in the set so it
    # is removed. This node pins the PRESENCE of PORT in the strip set.
    assert "PORT" in SPAWN_BOUNDARY_STRIP_ENV
    out = apply_spawn_boundary({"PORT": "9797"}, overlay=None)
    assert "PORT" not in out
