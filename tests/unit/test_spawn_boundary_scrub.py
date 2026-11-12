"""Pins for the spawn-boundary scrub (component B).

Fix-round 2:
  * B2 — the proc_run seam (_build_child_env / start_process) is now covered;
    the old ``hasattr`` smoke (which stayed green when the bash seam was
    reverted) is replaced by REAL materialized-child-env probes across every
    spawn channel: bash shell string, bash argv list, bash ``input=``,
    proc_run.
  * B6 — the pkill fence is ambient-only; an overlay attempt is ignored and
    stripped from the child env.
  * N4 — PG* prefix family (with the PG_TEST_* exemption), ENSEMBLE_CONFIG,
    DATABASE_URL.
"""

import asyncio
import json
import os
import sys
import textwrap

import pytest

from daemon.services.spawn_env_policy import (
    SPAWN_BOUNDARY_STRIP_ENV,
    apply_spawn_boundary,
)

PROBE_KEYS = [
    "PORT",
    "DAEMON_PORT",
    "HOST",
    "LIMITS_METRICS_LISTENER_PORT",
    "POSTGRES_HOST",
    "POSTGRES_DB",
    "PGHOST",
    "PGPASSWORD",
    "DATABASE_URL",
    "ENSEMBLE_CONFIG",
    "ENSEMBLE_UPGRADE_LIVE",
    "PG_TEST_HOST",          # exemption — must SURVIVE
    "UNRELATED_KEEP",        # ordinary passthrough — must SURVIVE
    "ENSEMBLE_FENCED_TEST_CONTEXT",  # overlay attempt — must be ABSENT
]


def _write_probe(tmp_path, stdin: bool = False):
    body = textwrap.dedent(
        """
        import json, os, sys
        %s
        keys = %r
        print(json.dumps({k: os.environ.get(k) for k in keys}))
        """
        % ("sys.stdin.read()" if stdin else "", PROBE_KEYS)
    )
    path = tmp_path / ("probe_stdin.py" if stdin else "probe.py")
    path.write_text(body)
    return path


def _poison(monkeypatch):
    for name in SPAWN_BOUNDARY_STRIP_ENV:
        monkeypatch.setenv(name, "POISON")
    for name in ("PGHOST", "PGPASSWORD"):
        monkeypatch.setenv(name, "POISON")
    monkeypatch.setenv("PG_TEST_HOST", "scratch-ok")
    monkeypatch.setenv("UNRELATED_KEEP", "keepval")
    # B6: an overlay attempt must not survive into the child env.
    monkeypatch.setenv("ENSEMBLE_FENCED_TEST_CONTEXT", "1")


def _parse_probe_json(output: str) -> dict:
    start = output.find("{")
    end = output.rfind("}")
    assert start >= 0 and end > start, output
    return json.loads(output[start : end + 1])


def _assert_scrubbed(env: dict):
    for name in SPAWN_BOUNDARY_STRIP_ENV:
        assert env.get(name) is None, f"{name} survived the scrub: {env.get(name)!r}"
    assert env.get("PGHOST") is None
    assert env.get("PGPASSWORD") is None
    assert env.get("PG_TEST_HOST") == "scratch-ok"  # exemption pinned
    assert env.get("UNRELATED_KEEP") == "keepval"


# ---------------------------------------------------------------------------
# Policy-level
# ---------------------------------------------------------------------------


def test_scrub_strips_poisoned_ambient_with_overlay_wins():
    poisoned = {name: "POISON" for name in SPAWN_BOUNDARY_STRIP_ENV}
    poisoned["UNRELATED"] = "stay"
    out = apply_spawn_boundary(poisoned, overlay={"PORT": "12345"})
    assert out.get("PORT") == "12345"
    for name in SPAWN_BOUNDARY_STRIP_ENV:
        if name != "PORT":
            assert name not in out, f"{name} survived scrub"
    assert out.get("UNRELATED") == "stay"


def test_counter_mutation_would_redden():
    assert "PORT" in SPAWN_BOUNDARY_STRIP_ENV
    out = apply_spawn_boundary({"PORT": "9797"}, overlay=None)
    assert "PORT" not in out


# ---------------------------------------------------------------------------
# service_spawner (Popen) channel
# ---------------------------------------------------------------------------


def test_scrub_service_spawner_env_kwarg(monkeypatch, tmp_path):
    import daemon.tools.service_spawner as ss

    captured = {}

    class FakePopen:
        def __init__(self, *a, **kw):
            captured["env"] = kw.get("env")
            self.pid = 12345

    monkeypatch.setattr(ss.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(ss, "get_process_start_time", lambda pid: 999)
    _poison(monkeypatch)

    pid, st = ss.spawn(["echo", "hi"], str(tmp_path / "x.log"), extra_env={"PORT": "9"})
    assert pid == 12345
    env = captured["env"]
    assert env is not None
    # deliberate (extra_env) is the sanctioned explicit-setter channel
    assert env.get("PORT") == "9"
    for name in SPAWN_BOUNDARY_STRIP_ENV:
        if name != "PORT":
            assert name not in env, f"{name} survived in service_spawner env"
    assert env.get("PGHOST") is None
    assert env.get("PG_TEST_HOST") == "scratch-ok"
    assert env.get("UNRELATED_KEEP") == "keepval"


# ---------------------------------------------------------------------------
# REAL bash tool channel (B7(i) — the materialized child env, not hasattr)
# ---------------------------------------------------------------------------


def _bash_tool():
    from daemon.tools.bash import bash

    return bash


def test_bash_real_spawn_shell_string_channel(monkeypatch, tmp_path):
    _poison(monkeypatch)
    script = _write_probe(tmp_path)
    out = asyncio.run(_bash_tool().ainvoke({
        "command": f"{sys.executable} {script}",
        "timeout": 20,
    }))
    _assert_scrubbed(_parse_probe_json(str(out)))


def test_bash_real_spawn_argv_list_channel(monkeypatch, tmp_path):
    _poison(monkeypatch)
    script = _write_probe(tmp_path)
    out = asyncio.run(_bash_tool().ainvoke({
        "command": [sys.executable, str(script)],
        "timeout": 20,
    }))
    _assert_scrubbed(_parse_probe_json(str(out)))


def test_bash_real_spawn_input_channel(monkeypatch, tmp_path):
    _poison(monkeypatch)
    script = _write_probe(tmp_path, stdin=True)
    out = asyncio.run(_bash_tool().ainvoke({
        "command": [sys.executable, str(script)],
        "input": "hello-stdin",
        "timeout": 20,
    }))
    _assert_scrubbed(_parse_probe_json(str(out)))


def test_bash_overlay_fence_key_is_stripped_from_child(monkeypatch, tmp_path):
    """B6: the fence key cannot be laundered into a child process."""
    monkeypatch.delenv("ENSEMBLE_FENCED_TEST_CONTEXT", raising=False)
    script = _write_probe(tmp_path)
    out = asyncio.run(_bash_tool().ainvoke({
        "command": [sys.executable, str(script)],
        "env": {"ENSEMBLE_FENCED_TEST_CONTEXT": "1", "UNRELATED_KEEP": "x"},
        "timeout": 20,
    }))
    env = _parse_probe_json(str(out))
    assert env.get("ENSEMBLE_FENCED_TEST_CONTEXT") is None


# ---------------------------------------------------------------------------
# proc_run channel (B2 — the incident's delivery vector)
# ---------------------------------------------------------------------------


def test_proc_run_build_child_env_is_scrubbed(monkeypatch):
    from daemon.tools.proc_tools import _TRACKING_ENV_VAR, _build_child_env

    _poison(monkeypatch)
    env = _build_child_env("proc-deadbeef")
    for name in SPAWN_BOUNDARY_STRIP_ENV:
        assert env.get(name) is None, f"{name} survived proc_run scrub"
    assert env.get("PGHOST") is None
    assert env.get("PG_TEST_HOST") == "scratch-ok"
    assert env.get("UNRELATED_KEEP") == "keepval"
    # The tracking id is re-stamped AFTER the strip.
    assert env.get(_TRACKING_ENV_VAR) == "proc-deadbeef"


def test_proc_run_real_spawn_channel(monkeypatch, tmp_path):
    """End-to-end: the actual background-process spawn carries the scrub."""
    from daemon.tools import proc_tools as pt

    _poison(monkeypatch)
    script = _write_probe(tmp_path)
    instance_id = f"test-scrub-proc-{os.getpid()}"

    async def _run():
        pid, err = await pt._background_process_manager.start_process(
            instance_id=instance_id,
            command=[sys.executable, str(script)],
            workdir=None,
        )
        assert err is None, err
        info = pt._background_process_manager._processes[instance_id][pid]
        for _ in range(200):
            if b"{" in bytes(info.memory_buffer):
                break
            await asyncio.sleep(0.05)
        body = bytes(info.memory_buffer).decode("utf-8", "replace")
        await pt._background_process_manager.cleanup_instance(instance_id)
        return body, info

    body, info = asyncio.run(_run())
    env = _parse_probe_json(body)
    _assert_scrubbed(env)
    # NOTE: an AMBIENT fence marker is inherited by children BY DESIGN (a
    # fenced mirror keeps its descendants fenced). Only the tool-call
    # OVERLAY channel is stripped (pinned in the bash test above).


def test_proc_run_guard_import_failure_is_loud_fail_open(monkeypatch, caplog):
    """N9: the overlay-guard import fallback must log at ERROR (fail-open)."""
    import sys

    from daemon.tools.proc_tools import create_proc_tools

    # NOTE: ``daemon.tools.bash`` is the decorated TOOL on the package; the
    # MODULE lives in sys.modules and is what ``from ... import`` resolves.
    bash_mod = sys.modules["daemon.tools.bash"]
    monkeypatch.delattr(bash_mod, "_pkill_overlay_violation", raising=False)
    tools = create_proc_tools("test-loud-fallback")
    proc_run = next(t for t in tools if getattr(t, "name", "") == "proc_run")

    with caplog.at_level("ERROR", logger="daemon.tools.proc_tools"):
        # timeout=-1 returns before any spawn, AFTER the guard block.
        out = asyncio.run(proc_run.ainvoke({"command": "echo hi", "timeout": -1}))
    assert "Error: timeout" in str(out)
    assert "overlay guard UNAVAILABLE" in caplog.text
