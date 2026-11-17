"""Defect D (2026-11-17) — the psycopg2 gap in the skill audit scripts.

ROOT CAUSE (decision package): a bare ``postgresql://`` URL makes
SQLAlchemy default to the psycopg2 driver at ``create_engine`` —
psycopg2 is NOT in pyproject; the daemon uses ``postgresql+psycopg``
(``daemon/repositories/factory.py``). Both audit scripts built engines
from the operator-supplied ``--db-url`` verbatim:
``scripts/audit/skill_counter_reconcile.py`` failed inside its guarded
probe (confusing error), and ``scripts/audit/skill_stale_deactivate.py``
died with a RAW traceback (unguarded). The runbook prescribed the bare
form three times.

THE FIX: normalize ``postgresql://`` → ``postgresql+psycopg://`` before
``create_engine`` in both scripts (the 4-line idiom from
``tests/integration/test_maintenance_checkpoint_cleanup_api.py``).

THE PIN (this file): an e2e proof against a REAL PostgreSQL — calling
``module.main(["--db-url", …])`` with BOTH the explicit ``+psycopg``
form AND the historically-broken bare form. Pre-fix, the bare form
exited non-zero with the psycopg2 gap; post-fix both measure cleanly
(``--measure`` is read-only).

DB FENCE: this test resolves the DEV database ONLY (default
``ensemble_dev`` @ 127.0.0.1:5432, per the worktree .env fence) and
HARD-SKIPS if the resolved database name is anything else — it must
never touch a non-dev database, and it never writes (measure-only).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO_ROOT / "scripts" / "audit"


def _dev_dsn() -> tuple[str, str] | None:
    """Resolve the dev DSN from the env fence; None ⇒ skip."""
    db = os.environ.get("POSTGRES_DB", "ensemble_dev")
    if db != "ensemble_dev":
        # Fail-closed: NEVER point the audit script at a non-dev DB.
        return None
    host = os.environ.get("POSTGRES_HOST", "127.0.0.1")
    port = os.environ.get("POSTGRES_PORT", "5432")
    user = os.environ.get("POSTGRES_USER", "postgres")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    base = f"{host}:{port}/{db}"
    auth = f"{user}:{password}@" if password else f"{user}@"
    return f"postgresql+psycopg://{auth}{base}", f"postgresql://{auth}{base}"


@pytest.fixture(scope="module")
def dev_dsn() -> tuple[str, str]:
    resolved = _dev_dsn()
    if resolved is None:
        pytest.skip("POSTGRES_DB is not the dev database — refusing")
    psycopg_dsn, bare_dsn = resolved
    try:
        import psycopg  # noqa: F401 — probe the driver the URL selects
    except ImportError:
        pytest.skip("psycopg3 not installed in this venv")
    try:
        import sqlalchemy

        engine = sqlalchemy.create_engine(psycopg_dsn, future=True)
        from sqlalchemy import text

        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
    except Exception:
        pytest.skip("dev PostgreSQL unreachable — e2e pin is opt-in")
    return psycopg_dsn, bare_dsn


def test_counter_reconcile_explicit_psycopg_url(dev_dsn):
    """The explicit ``postgresql+psycopg://`` form measures cleanly."""
    sys.path.insert(0, str(_SCRIPTS))
    try:
        import importlib

        reconcile = importlib.import_module("skill_counter_reconcile")
        rc = reconcile.main(["--db-url", dev_dsn[0], "--measure", "--json"])
    finally:
        sys.path.remove(str(_SCRIPTS))
    assert rc == 0


def test_counter_reconcile_bare_postgresql_url_normalized(dev_dsn):
    """The historically-BROKEN bare ``postgresql://`` form now works —
    the rewrite normalizes it to the psycopg3 driver (pre-fix:
    ModuleNotFoundError inside the guarded probe, non-zero exit)."""
    sys.path.insert(0, str(_SCRIPTS))
    try:
        import importlib

        reconcile = importlib.import_module("skill_counter_reconcile")
        rc = reconcile.main(["--db-url", dev_dsn[1], "--measure", "--json"])
    finally:
        sys.path.remove(str(_SCRIPTS))
    assert rc == 0


def test_stale_deactivate_bare_postgresql_url_guards_cleanly(dev_dsn):
    """The UNGUARDED script no longer tracebacks on a bad/bare URL:
    the engine build + a liveness probe are guarded and the bare form
    is normalized. An unreachable host gets a clean stderr message +
    exit 1 — never a raw traceback (the pre-defect-D behavior)."""
    sys.path.insert(0, str(_SCRIPTS))
    try:
        import importlib

        stale = importlib.import_module("skill_stale_deactivate")
        # A syntactically valid but UNREACHABLE host proves the guard.
        rc = stale.main(
            [
                "--db-url",
                "postgresql://nosuchuser@127.0.0.1:1/ensemble_dev",
            ]
        )
    finally:
        sys.path.remove(str(_SCRIPTS))
    assert rc == 1
