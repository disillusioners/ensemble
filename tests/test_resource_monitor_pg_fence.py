"""The SHARED PostgreSQL scratch-DB fence — the differential proofs.

The shared ``tests/postgres/conftest.py`` ``pg_engine`` fixture runs
``SQLModel.metadata.drop_all(engine)`` on the TARGET database at
session setup. The fence that protects it used to live inside ONE
module (the resource-monitor bind-path module), so every OTHER module
of the lane ran the ``drop_all`` with NO guard at all. The fence now
lives in the shared conftest as a session-scoped DEPENDENCY of
``pg_engine``.

This module pins the fence's RULES (pure — no database is touched;
the ownership lookup is stubbed):

  * the LIVE names (``ensemble_prod`` / ``ensemble_dev`` /
    ``ensemble_prod_test``) are REFUSED — the differential proof;
  * the allowlist is EXACT-name membership: a name that merely looks
    like scratch (``ensemble_prod_test``) is NOT accepted, and a
    sanctioned scratch name IS;
  * OWNERSHIP is a REJECTION-side signal only: a scratch database
    co-owned with a live database is refused — and ownership is NEVER
    an acceptance rule (the ``ensemble`` role owns the live databases
    in this deployment, so an ownership-based acceptance rule would
    admit production);
  * an AMBIENT ``PG_TEST_DB`` pointing at a live database is refused
    (the env is never trusted);
  * the pack script asserts its own target's allowlist membership IN
    THE SCRIPT (m11) and the documented throwaway name is in the
    shared allowlist.

FENCE DISCIPLINE: no database connection, no DDL, no live ``/proc``.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.postgres import conftest as pg_conftest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PACK_SCRIPT = (
    _REPO_ROOT / "test" / "packs" / "resource_monitor_flusher_bind_pg_test.sh"
)

#: The live database names the fence must refuse.
LIVE_DB_NAMES = (
    "ensemble_prod",
    "ensemble_dev",
    "ensemble_prod_test",
)


@pytest.fixture(autouse=True)
def _no_ambient_env(monkeypatch):
    """The fence's env inputs are controlled by every test here."""
    monkeypatch.delenv("PG_TEST_DB", raising=False)
    monkeypatch.delenv(pg_conftest.ALLOWED_DBS_ENV, raising=False)


class TestLiveNamesAreRefused:
    @pytest.mark.parametrize("db_name", LIVE_DB_NAMES)
    def test_the_live_names_are_refused(self, monkeypatch, db_name):
        """The differential proof — each live name is refused."""
        monkeypatch.setenv("PG_TEST_DB", db_name)
        with pytest.raises(RuntimeError) as exc:
            pg_conftest.check_scratch_db_fence()
        assert "refused" in str(exc.value)

    @pytest.mark.parametrize("db_name", LIVE_DB_NAMES)
    def test_a_live_name_cannot_be_declared_into_the_allowlist(
        self, monkeypatch, db_name
    ):
        """Even an explicit declaration cannot smuggle a live name in."""
        monkeypatch.setenv("PG_TEST_DB", db_name)
        monkeypatch.setenv(pg_conftest.ALLOWED_DBS_ENV, db_name)
        with pytest.raises(RuntimeError) as exc:
            pg_conftest.check_scratch_db_fence()
        assert "refused" in str(exc.value)

    def test_a_name_that_merely_looks_like_scratch_is_refused(
        self, monkeypatch
    ):
        """EXACT-name membership — no substring / prefix heuristic."""
        monkeypatch.setenv("PG_TEST_DB", "ensemble_prod_test_only")
        with pytest.raises(RuntimeError) as exc:
            pg_conftest.check_scratch_db_fence()
        assert "refused" in str(exc.value)

    def test_an_ambient_env_pointing_at_a_live_database_is_refused(
        self, monkeypatch
    ):
        """The env is never trusted — a live target is refused."""
        monkeypatch.setenv("PG_TEST_DB", "ensemble_prod")
        with pytest.raises(RuntimeError):
            pg_conftest.check_scratch_db_fence()


class TestAllowlist:
    @pytest.mark.parametrize(
        "db_name",
        [
            "ensemble_test",
            "ensemble_dev_test_only",
            "ensemble_test_only",
            "ensemble_test_only_rmflusher",
        ],
    )
    def test_the_sanctioned_scratch_names_are_accepted(
        self, monkeypatch, db_name
    ):
        monkeypatch.setenv("PG_TEST_DB", db_name)
        # The ownership lookup is stubbed: this module must not touch a
        # database. The owner is unknown → the ownership rejection is
        # skipped (it can only REFUSE, never accept).
        monkeypatch.setattr(
            pg_conftest, "_database_owner", lambda _name: None
        )
        pg_conftest.check_scratch_db_fence()  # must not raise

    def test_an_unknown_database_is_refused(self, monkeypatch):
        monkeypatch.setenv("PG_TEST_DB", "ensemble_some_other_scratch")
        monkeypatch.setattr(
            pg_conftest, "_database_owner", lambda _name: None
        )
        with pytest.raises(RuntimeError) as exc:
            pg_conftest.check_scratch_db_fence()
        assert "allowlist" in str(exc.value)

    def test_an_exactly_declared_name_is_accepted(self, monkeypatch):
        """A pack that owns a throwaway with a generated name declares it."""
        monkeypatch.setenv("PG_TEST_DB", "ensemble_retire_pack_ab12cd34")
        monkeypatch.setenv(
            pg_conftest.ALLOWED_DBS_ENV, "ensemble_retire_pack_ab12cd34"
        )
        monkeypatch.setattr(
            pg_conftest, "_database_owner", lambda _name: None
        )
        pg_conftest.check_scratch_db_fence()  # must not raise

    def test_the_allowlist_holds_no_pattern(self):
        """EXACT names only — no wildcard / regex in the allowlist."""
        for name in pg_conftest.ALLOWED_SCRATCH_DBS:
            assert not re.search(r"[*?%]", name), (
                f"{name!r} carries a pattern character"
            )
            assert name == name.strip().lower()


class TestOwnershipIsARejectionOnlySignal:
    def test_a_production_owned_scratch_database_is_refused(
        self, monkeypatch
    ):
        """Ownership REFUSES — it never accepts.

        In this deployment the ``ensemble`` role owns the live
        databases, so a scratch database co-owned with one of them is
        refused. (Acceptance stays EXACT-name membership.)
        """
        monkeypatch.setenv("PG_TEST_DB", "ensemble_demo")
        monkeypatch.setenv(pg_conftest.ALLOWED_DBS_ENV, "ensemble_demo")

        def _owner(name: str) -> str | None:
            return "ensemble" if name == "ensemble_demo" else "ensemble"

        monkeypatch.setattr(pg_conftest, "_database_owner", _owner)
        with pytest.raises(RuntimeError) as exc:
            pg_conftest.check_scratch_db_fence()
        assert "co-owned" in str(exc.value)

    def test_a_dedicated_owner_is_accepted(self, monkeypatch):
        """The pack's dedicated throwaway role owns NOTHING else."""
        monkeypatch.setenv("PG_TEST_DB", "ensemble_test_only_rmflusher")

        def _owner(name: str) -> str | None:
            if name == "ensemble_test_only_rmflusher":
                return "ensemble_rmflusher_pack"
            return "ensemble"

        monkeypatch.setattr(pg_conftest, "_database_owner", _owner)
        pg_conftest.check_scratch_db_fence()  # must not raise


class TestThePackScript:
    def _script(self) -> str:
        return _PACK_SCRIPT.read_text(encoding="utf-8")

    def test_the_pack_script_asserts_its_own_allowlist_membership(self):
        """m11 — the script checks the allowlist ITSELF (not only the
        fixture), and it never trusts an ambient ``PG_TEST_DB``."""
        script = self._script()
        assert "PG_TEST_ALLOWED_DBS" in script or (
            "ALLOWED_DBS=" in script
        )
        assert 'case " ${ALLOWED_DBS} " in' in script
        # The target is the pack's OWN database, never the ambient one.
        assert 'PG_TEST_DB="$SCRATCH_DB"' in script
        assert "PG_TEST_DB:-" not in script, (
            "the pack still falls back to an ambient PG_TEST_DB"
        )

    def test_the_pack_script_checks_the_toolchain(self):
        """m11 — psql / createdb / dropdb must exist."""
        script = self._script()
        for binary in ("psql", "createdb", "dropdb"):
            assert binary in script

    def test_the_pack_script_leaves_no_residue(self):
        """The pack drops its database AND its role in the EXIT trap."""
        script = self._script()
        assert "trap on_exit EXIT" in script
        body = script.split("on_exit() {", 1)[1].split("\n}", 1)[0]
        assert "cleanup" in body
        assert "DROP ROLE IF EXISTS" in script
        assert "dropdb" in script

    def test_the_documented_throwaway_name_is_in_the_shared_allowlist(
        self,
    ):
        script = self._script()
        match = re.search(r'^SCRATCH_DB="([^"]+)"', script, re.MULTILINE)
        assert match, "the pack script does not declare SCRATCH_DB"
        assert match.group(1) in pg_conftest.ALLOWED_SCRATCH_DBS, (
            "the pack's throwaway database is not in the shared allowlist "
            "— the shared fence would refuse it"
        )


@pytest.mark.timeout(120)
def test_a_module_level_pg_engine_override_still_triggers_the_fence():
    """A module that SHADOWS ``pg_engine`` cannot opt out of the fence."""
    proc = subprocess.run(
        f"PG_TEST_DB=ensemble_prod PG_TEST_PORT=5499 \"{sys.executable}\" -m pytest "
        "tests/postgres/test_attestation_live_descendants_pg_lca.py --override-ini=addopts= -m postgres -q",
        shell=True, cwd=str(_REPO_ROOT), capture_output=True, text=True, timeout=120,
    )
    assert "refused" in proc.stdout, proc.stdout[-2000:]
