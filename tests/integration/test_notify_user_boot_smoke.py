"""Isolated boot smoke — the ``notify_user`` end-to-end (B1, RULED).

TWO GATES, ONE LANE
-------------------
The whole smoke is OPT-IN (it starts a daemon, a scratch PG database,
an aiohttp sink and a mock LLM — none of it belongs in a default suite
run, and the TESTER lane owns executing it; this file is the prepared
harness):

    ENSEMBLE_NOTIFY_SMOKE=1        # the module gate
    ENSEMBLE_NOTIFY_SMOKE_UNIT=1   # + the transient-unit gate (an
                                   #   ADDITIONAL gate — it does NOT
                                   #   imply the module gate; BOTH are
                                   #   required)

It additionally needs a scratch PG16 server reachable via ``PG_TEST_*``
(the repo's test-DB convention) — the fresh-SQLite boot is BLOCKED by
the ``20260714_000001`` migration on tip (re-verified present at
7af1d2632d), so the smoke is PG-only by construction.

THE PINNED SEQUENCE (approver B1, folded verbatim)
--------------------------------------------------
scratch TELEGRAM ``source_configs`` row (real adapter code, real
registry build) → the scratch aiohttp sink answers ``getMe`` (the
handshake) → the source reaches ``SourceStatus.RUNNING`` → the
pre-RUNNING refusal is asserted first as the NEGATIVE case → the
``source="scheduler"`` alarm-shaped wake rides the REAL
``manager.enqueue_message_job`` primitive → the Ari turn runs on the
scripted mock LLM and emits the ``notify_user`` tool call (no
real-LLM) → the tool executes through the REAL resolution chain and
adapter send → the sink receives EXACTLY ONE ``sendMessage`` with the
expected payload → ``notify_audit`` rows land ``accepted`` →
``dispatched`` → teardown clean (SIGTERM-only), the live daemon's
identity is unchanged, and the scratch PG database is DROPPED.

The base-URL redirection happens WITHOUT production-code change: a
scratch ``sitecustomize.py`` shim (prepended via PYTHONPATH in the
transient unit ONLY) patches the Telegram adapter's ``TELEGRAM_API_BASE``
module constant at import time — the smallest seam (B1: NOT a
whole-file shadow copy, which drifts).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from tests.support import notify_smoke as smoke
from tests.support import wedge_smoke as wsmoke


def smoke_enabled() -> bool:
    return os.environ.get("ENSEMBLE_NOTIFY_SMOKE") == "1"


def unit_enabled() -> bool:
    return os.environ.get("ENSEMBLE_NOTIFY_SMOKE_UNIT") == "1"


pytestmark = pytest.mark.skipif(
    not smoke_enabled(),
    reason=(
        "the notify_user boot smoke is OPT-IN (set ENSEMBLE_NOTIFY_SMOKE=1 "
        "for the module gate; the transient-unit leg additionally needs "
        "ENSEMBLE_NOTIFY_SMOKE_UNIT=1 — it does NOT imply the module gate); "
        "the tester lane runs it — it starts a daemon, a scratch PG "
        "database, an aiohttp sink and a mock LLM"
    ),
)


@pytest.fixture()
def _pg_env() -> dict[str, str]:
    """The scratch-PG connection env (``PG_TEST_*``), translated to the
    daemon's ``POSTGRES_*`` names — the wedge-smoke F1 translation."""
    env: dict[str, str] = {}
    for test_key, daemon_key in wsmoke.PG_TEST_TO_POSTGRES:
        value = os.environ.get(test_key)
        if value:
            env[daemon_key] = value
    parts_missing = [
        key
        for key in ("PG_TEST_HOST", "PG_TEST_PORT", "PG_TEST_DB", "PG_TEST_USER", "PG_TEST_PASSWORD")
        if not os.environ.get(key)
    ]
    if parts_missing and not os.environ.get("PG_TEST_URL"):
        pytest.skip(
            "the notify boot smoke needs a scratch PG16 via PG_TEST_* — "
            f"missing: {', '.join(parts_missing)}"
        )
    return env


def _psql(env: dict[str, str], db: str, sql: str) -> str:
    """One psql statement against the scratch server (per-var env only
    — never a glob)."""
    passthrough = {
        "PGHOST": env.get("POSTGRES_HOST", ""),
        "PGPORT": env.get("POSTGRES_PORT", ""),
        "PGUSER": env.get("POSTGRES_USER", ""),
        "PGPASSWORD": env.get("POSTGRES_PASSWORD", ""),
    }
    completed = subprocess.run(
        ["psql", "-d", db, "-Atc", sql],
        env={**os.environ, **{k: v for k, v in passthrough.items() if v}},
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.fail(f"psql failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


@pytest.fixture()
def scratch_pg(_pg_env: dict[str, str]):
    """A uniquely-named scratch database, dropped at teardown (B1:
    'teardown clean, scratch PG dropped')."""
    db = f"notify_smoke_{uuid.uuid4().hex[:10]}"
    _psql(_pg_env, "postgres", f'CREATE DATABASE "{db}"')
    yield {**_pg_env, "POSTGRES_DB": db}
    _psql(_pg_env, "postgres", f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')


@pytest.mark.skipif(
    not unit_enabled(),
    reason="the transient-unit leg additionally needs ENSEMBLE_NOTIFY_SMOKE_UNIT=1",
)
class TestBootSmokeUnit:
    # The pinned sequence boots a REAL daemon (scratch PG migrations +
    # worker pools + a scheduler 5s tick) — minutes, not seconds. The
    # 30s default pytest-timeout must not fire mid-boot.
    @pytest.mark.timeout(560)
    def test_the_pinned_sequence_end_to_end(self, tmp_path: Path, scratch_pg: dict[str, str]) -> None:
        """The B1 pinned sequence, inside an isolated transient unit."""
        repo_root = Path(__file__).resolve().parents[2]
        port = smoke.free_port()
        sink_port = smoke.free_port()
        mock_port = smoke.free_port()

        observations_path = tmp_path / "observations.json"
        sink_log = tmp_path / "sink_log.jsonl"
        mock_log = tmp_path / "mock_llm.jsonl"
        unit_log = tmp_path / "unit.log"

        # (the source_configs row is seeded IN-UNIT by the driver via
        #  the source repository — post-boot, pre-start, so the
        #  autostart race cannot bring the adapter up early)

        # ── the PYTHONPATH shim (smallest seam — the API-base constant
        #    patched at import time, NOT a whole-file shadow) ─────────
        shim_dir = tmp_path / "shim"
        smoke.write_shim(shim_dir, sink_port)

        # ── the transient unit env (the wedge-smoke scratch_env shape:
        #    FAKE_HOME, scratch data dir, explicit per-var PG env) ────
        env = wsmoke.scratch_env(
            tmp_path=tmp_path,
            port=port,
            pg_env=[(k, v) for k, v in scratch_pg.items()],
        )
        # The api.py log dir reads DAEMON_LOG_DIR (default ./data/logs —
        # a REPO-WORKTREE relative path). Without this, the transient
        # unit's daemon (running as root inside the unit) leaves a
        # root-owned data/ dir in the worktree (observed live; the
        # residue then breaks unrelated CWD-relative unit tests).
        env["DAEMON_LOG_DIR"] = str(tmp_path / "daemon-logs")
        env.update(
            {
                # the mock-LLM lane (no real LLM anywhere in the smoke)
                "OPENAI_BASE_URL": f"http://127.0.0.1:{mock_port}/v1",
                "OPENAI_API_KEY": "sk-notify-smoke",
                "OPENAI_MODEL": "notify-smoke-mock",
                # the PYTHONPATH shim (sitecustomize patches the base URL)
                "PYTHONPATH": str(shim_dir),
                # NOTE: SYSTEM_ENCRYPTION_KEY is deliberately NOT set —
                # the unset-key path stores the seed credentials as
                # plain JSON, which is exactly what the seed SQL wrote.
                "ENSEMBLE_NOTIFY_USER_ENABLED": "true",
            }
        )

        driver = smoke.driver_script(observations_path)
        sink = smoke.sink_script(sink_port, sink_log)
        mock = smoke.mock_llm_script(mock_port, mock_log)
        combined = (
            "import subprocess, sys, time, urllib.request\n"
            f"sink = subprocess.Popen([sys.executable, '-c', {sink!r}])\n"
            f"mock = subprocess.Popen([sys.executable, '-c', {mock!r}])\n"
            "try:\n"
            "    deadline = time.time() + 20\n"
            "    while time.time() < deadline:\n"
            "        try:\n"
            f"            urllib.request.urlopen('http://127.0.0.1:{sink_port}/health', timeout=1)\n"
            "            break\n"
            "        except Exception:\n"
            "            time.sleep(0.3)\n"
            "    else:\n"
            "        raise SystemExit('the scratch sink never came up')\n"
            f"    driver = subprocess.run([sys.executable, '-c', {driver!r}])\n"
            "    raise SystemExit(driver.returncode)\n"
            "finally:\n"
            "    for child in (sink, mock):\n"
            "        child.terminate()\n"
            "    for child in (sink, mock):\n"
            "        try:\n"
            "            child.wait(timeout=10)\n"
            "        except Exception:\n"
            "            child.kill()\n"
        )

        unit = f"notify-smoke-{uuid.uuid4().hex[:8]}"
        fence_before = wsmoke.live_daemon_fence()
        argv = wsmoke.transient_unit_argv(
            unit=unit,
            workdir=repo_root,
            argv=[sys.executable, "-c", combined],
            env=env,
        )
        try:
            completed = subprocess.run(
                argv, capture_output=True, text=True, check=False, timeout=600
            )
        finally:
            fence_after = wsmoke.live_daemon_fence()
        unit_log.write_text(
            completed.stdout + "\n=== STDERR ===\n" + completed.stderr,
            encoding="utf-8",
        )
        wsmoke.assert_live_fence_unchanged(fence_before, fence_after)

        assert completed.returncode == 0, (
            "the smoke driver failed inside the unit — full log:\n"
            + unit_log.read_text(encoding="utf-8")[-4000:]
        )
        assert observations_path.exists(), (
            f"no observations file — log tail:\n{completed.stderr[-2000:]}"
        )
        observations = json.loads(observations_path.read_text(encoding="utf-8"))

        # ── the pinned-sequence assertions ───────────────────────────
        smoke.assert_pinned_sequence(observations)
        # the RUNNING precondition + the negative case, explicitly
        assert observations["running_precondition"] == "SourceStatus.RUNNING"
        assert "notify_source_disabled" in observations["precheck_refusal"]
        # cgroup isolation was proven INSIDE the unit
        assert "ensemble-main" not in observations["cgroup"]
