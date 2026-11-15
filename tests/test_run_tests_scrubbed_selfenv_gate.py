"""Pin test for the ENSEMBLE_SELF_ENV entry gate in scripts/run_tests_scrubbed.sh.

The scrubbed test runner must REFUSE (exit 78, loud message) when an ambient
ENSEMBLE_SELF_ENV holds a non-sanctioned value (live/prod/garbage/wrong case),
and must still pass through sanctioned values (unset/dev/demo/sandbox) unchanged.

The refusal leg runs before .venv/bin/pytest is exec'd, so it needs no venv.
The clean legs need the venv; they skip gracefully when it is absent.
"""

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "run_tests_scrubbed.sh"
VENV_PYTEST = REPO_ROOT / ".venv" / "bin" / "pytest"


def _run(env):
    return subprocess.run(
        ["bash", str(SCRIPT), "--version"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _combined(r):
    return (r.stdout or "") + (r.stderr or "")


@pytest.mark.parametrize("value", ["live", "prod", "garbage", "DEV", "dev ", "sandboxX"])
def test_refusal_exits_78_with_message_and_no_pytest(value):
    env = os.environ.copy()
    env["ENSEMBLE_SELF_ENV"] = value
    r = _run(env)
    combined = _combined(r)
    assert r.returncode == 78, (
        "refusal leg must exit 78; got %d; output=%r" % (r.returncode, combined)
    )
    assert ("ENV-SCRUB FAILURE: ambient ENSEMBLE_SELF_ENV='%s'" % value) in combined, (
        "failure message missing for value %r; output=%r" % (value, combined)
    )
    assert "unset ENSEMBLE_SELF_ENV or run with ENSEMBLE_SELF_ENV=dev" in combined, (
        "remediation line missing; output=%r" % combined
    )
    assert "pytest" not in combined.lower(), (
        "pytest was reached despite refusal; output=%r" % combined
    )


@pytest.mark.parametrize("value", ["dev", "demo", "sandbox"])
def test_clean_sanctioned_value_exits_0(value):
    if not VENV_PYTEST.exists():
        pytest.skip("venv missing; clean leg requires .venv/bin/pytest")
    env = os.environ.copy()
    env["ENSEMBLE_SELF_ENV"] = value
    r = _run(env)
    combined = _combined(r)
    assert r.returncode == 0, (
        "sanctioned value %r must exit 0; got %d; output=%r"
        % (value, r.returncode, combined)
    )


def test_clean_unset_exits_0():
    if not VENV_PYTEST.exists():
        pytest.skip("venv missing; clean leg requires .venv/bin/pytest")
    env = os.environ.copy()
    env.pop("ENSEMBLE_SELF_ENV", None)
    r = _run(env)
    combined = _combined(r)
    assert r.returncode == 0, (
        "unset ENSEMBLE_SELF_ENV must exit 0; got %d; output=%r"
        % (r.returncode, combined)
    )