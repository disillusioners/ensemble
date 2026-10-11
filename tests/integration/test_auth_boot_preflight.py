"""Subprocess harness: the OAuth2 boot-config preflight exits 78 PRE-BIND.

phase1-plan §4 row 4 (T1.3; AMEND v1.1 / C4). Spawning the daemon entry
(``python -m daemon``) with ``auth.enabled=true`` plus a misconfigured
``ENSEMBLE_AUTH_*`` section must:

* exit with code **78** (EX_CONFIG) — the launcher's no-retry contract;
* emit a structured ERROR naming each offending env var exactly;
* happen BEFORE any port bind (no ``Uvicorn running`` — the refusal is
  the preflight site, NOT the lifespan site).

Env-poison guard: the child env is SCRUBBED of ``POSTGRES_*`` /
``DATABASE_URL`` (nothing may boot against a live DB) and re-pointed at
a tmp ``ENSEMBLE_CONFIG`` + tmp data dir — mirroring the
``_boot_db_preflight`` test pattern's env handling
(``tests/test_main_entry.py``). The child exits at the auth preflight —
before the DB probe, before uvicorn — so the scrub is belt-and-braces,
not the only line of defense.

No ``integration`` marker: this harness needs no live OpenCode server —
it must run (and gate) with the default ``pytest`` invocation.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Generous: the child imports uvicorn + the config stack before main();
# the refusal itself is static and fast.
SUBPROCESS_TIMEOUT_S = 120

_SCRUBBED_PREFIXES = ("POSTGRES_", "DATABASE_URL", "ENSEMBLE_AUTH_")


def _scrubbed_env(tmp_path: Path, config_file: Path, overrides: dict[str, str]) -> dict[str, str]:
    """Child env: scrub poison prefixes, re-point config + data, apply overrides."""
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(_SCRUBBED_PREFIXES) and k != "ENSEMBLE_CONFIG"
    }
    # Belt-and-braces: even if the child got past the auth preflight,
    # ensemble.json / data land in tmp — never near a real deployment.
    env["ENSEMBLE_DATA_DIR"] = str(tmp_path / "data")
    env["DATA_DIR"] = str(tmp_path / "data")
    env["ENSEMBLE_CONFIG"] = str(config_file)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(overrides)
    return env


def _write_minimal_yaml(tmp_path: Path) -> Path:
    config_file = tmp_path / "config.yaml"
    config_file.write_text("daemon:\n  host: 127.0.0.1\n  port: 8088\n")
    return config_file


def _run_daemon(tmp_path: Path, overrides: dict[str, str]) -> subprocess.CompletedProcess:
    config_file = _write_minimal_yaml(tmp_path)
    return subprocess.run(  # noqa: S603 — argv is fully controlled by the test
        [sys.executable, "-m", "daemon"],
        cwd=str(REPO_ROOT),
        env=_scrubbed_env(tmp_path, config_file, overrides),
        capture_output=True,
        timeout=SUBPROCESS_TIMEOUT_S,
        check=False,
    )


def _assert_pre_bind(proc: subprocess.CompletedProcess) -> None:
    """The refusal must fire at the preflight — the server never starts."""
    combined = proc.stdout + proc.stderr
    assert b"Uvicorn running" not in combined, (
        "daemon bound a port before the auth preflight refusal"
    )
    assert b"Application startup complete" not in combined


class TestExit78Preflight:
    def test_enabled_true_missing_client_id_exits_78(self, tmp_path):
        """G1.4 canonical scenario: auth.enabled=true + missing client_id
        → exit 78 with a structured ERROR naming ENSEMBLE_AUTH_CLIENT_ID."""
        proc = _run_daemon(
            tmp_path,
            {"ENSEMBLE_AUTH_ENABLED": "true"},
        )
        assert proc.returncode == 78, (
            f"expected 78, got {proc.returncode}\n"
            f"stdout:\n{proc.stdout.decode(errors='replace')[-2000:]}\n"
            f"stderr:\n{proc.stderr.decode(errors='replace')[-2000:]}"
        )
        stderr = proc.stderr.decode(errors="replace")
        assert "Auth config misconfig" in stderr
        assert "ENSEMBLE_AUTH_CLIENT_ID" in stderr
        assert "ENSEMBLE_AUTH_CLIENT_SECRET" in stderr
        assert "ENSEMBLE_AUTH_ISSUER" in stderr
        assert "78" in stderr
        _assert_pre_bind(proc)

    def test_enabled_true_http_issuer_exits_78(self, tmp_path):
        """§6.3 row 4 through the subprocess lane: a validator-refused
        section (not just missing fields) also converts to exit 78 —
        the preflight owns the exit for the WHOLE matrix."""
        proc = _run_daemon(
            tmp_path,
            {
                "ENSEMBLE_AUTH_ENABLED": "true",
                "ENSEMBLE_AUTH_CLIENT_ID": "my-app",
                "ENSEMBLE_AUTH_CLIENT_SECRET": "s3cret",
                "ENSEMBLE_AUTH_ISSUER": "http://idp.example.com",
                "ENSEMBLE_AUTH_ALLOWED_ORIGINS": "https://ensemble.example.com",
            },
        )
        assert proc.returncode == 78, proc.stderr.decode(errors="replace")[-2000:]
        stderr = proc.stderr.decode(errors="replace")
        assert "auth.issuer must be https://" in stderr
        assert "ENSEMBLE_AUTH_ISSUER" in stderr
        _assert_pre_bind(proc)

    def test_enabled_true_typo_env_var_exits_78(self, tmp_path):
        """D1.3 through the subprocess lane: an unknown ENSEMBLE_AUTH_*
        var is refused loudly (named) when auth is ON."""
        proc = _run_daemon(
            tmp_path,
            {
                "ENSEMBLE_AUTH_ENABLED": "true",
                "ENSEMBLE_AUTH_CLIENT_ID": "my-app",
                "ENSEMBLE_AUTH_CLIENT_SECRET": "s3cret",
                "ENSEMBLE_AUTH_ISSUER": "https://idp.example.com",
                "ENSEMBLE_AUTH_ALLOWED_ORIGINS": "https://ensemble.example.com",
                "ENSEMBLE_AUTH_TYPO_X": "1",
            },
        )
        assert proc.returncode == 78, proc.stderr.decode(errors="replace")[-2000:]
        assert b"ENSEMBLE_AUTH_TYPO_X" in proc.stderr
        _assert_pre_bind(proc)

    def test_db_preflight_never_runs_on_auth_refusal(self, tmp_path):
        """Ordering proof: the auth refusal precedes the DB probe — no PG
        connectivity attempt appears (the auth lane exits first)."""
        proc = _run_daemon(
            tmp_path,
            {"ENSEMBLE_AUTH_ENABLED": "true"},
        )
        assert proc.returncode == 78
        combined = proc.stdout + proc.stderr
        assert b"Boot preflight: PostgreSQL" not in combined
        assert b"exiting 75" not in combined
