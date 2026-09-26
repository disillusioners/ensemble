"""P3-WP8 — KMS-Lite raw-row migration script tests.

Closes arch §8 R3 ("DB stores env RAW today"). Tests prove the
properties called out in the dispatch spec:

* fresh-migrate (N>0 plaintext → 0 plaintext)
* idempotent-rerun (zero additional mints/audits — assert via counters)
* mixed-rows (some migrated, some marker, some clean)
* audit-line-shape (field set + secret_ref is handle never plaintext)
* dry-run touches nothing
* per-row failure isolation (one poisoned row → others still migrate,
  poisoned row untouched)

All tests use SQLite in-memory engines (NO PG, NO live daemon) per the
worktree contract. KMS-Lite is exercised via a fresh module-level store
per-test fixture so the ``SYSTEM_ENCRYPTION_KEY`` env is honored cleanly.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel

# Repo-root import path — the migration script lives under
# scripts/migrations/ and the test lives under tests/unit/services/.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from daemon.repositories.mcp_server.models import McpServer  # noqa: E402
from daemon.services import kms_lite as kms_lite_mod  # noqa: E402
from daemon.services.kms_lite import (  # noqa: E402
    reset_store_for_tests,
)
from daemon.services.kms_resolver import is_marker  # noqa: E402
from scripts.migrations import kms_lite_raw_row_migrate as migrate  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_kms_store():
    """Each test sees a fresh module-level KMS store.

    Critical: must run BEFORE any test that exercises the singleton,
    otherwise the previous test's ``SYSTEM_ENCRYPTION_KEY`` env-var
    state leaks into the next test's store constructor.
    """
    reset_store_for_tests()
    yield
    reset_store_for_tests()


@pytest.fixture
def fernet_key(monkeypatch: pytest.MonkeyPatch) -> str:
    """Provision a valid Fernet key in the env for the test duration."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("SYSTEM_ENCRYPTION_KEY", key)
    return key


@pytest.fixture
def sqlite_engine() -> Engine:
    """In-memory SQLite engine with the ``mcp_servers`` schema."""
    engine = create_engine("sqlite:///:memory:", future=True)
    SQLModel.metadata.create_all(engine)
    return engine


def _make_row(engine: Engine, *, name: str, env: dict[str, str], schema_version: str = "0") -> McpServer:
    """Insert one ``mcp_servers`` row and return the loaded model."""
    with Session(engine) as s:
        row = McpServer(
            name=name,
            config={"env": env, "command": "noop", "args": []},
            instance_metadata={"bound_handles": []},
            config_schema_version=schema_version,
        )
        s.add(row)
        s.commit()
        s.refresh(row)
        # Detach from the session so the caller can use it independently.
        s.expunge(row)
    return row


def _read_row(engine: Engine, row_id: str) -> McpServer | None:
    with Session(engine) as s:
        row = s.get(McpServer, row_id)
        if row is None:
            return None
        s.expunge(row)
        return row


def _audit_lines(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    out: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        out.append(json.loads(line))
    return out


# ---------------------------------------------------------------------------
# Matchers: parity with redact_secrets
# ---------------------------------------------------------------------------


class TestSecretKeyMatcher:
    """The migration must match the SAME secret-key list as redact_secrets."""

    def test_secret_key_matcher_matches_redact_secrets(self) -> None:
        from daemon.routers.mcp_servers import redact_secrets

        # A mixed set — every key redact_secrets redacts, our matcher
        # also considers secret; and every key redact_secrets passes
        # through, our matcher also passes through.
        test_keys = [
            "LOG_LEVEL",
            "MY_MCP_TRANSPORT",
            "DEBUG",
            "OPEN_DESIGN_API_KEY",
            "MY_TOKEN",
            "PASSWORD",
            "AUTH_HEADER",
            "API_BASE",
            "EXTRA_HEADERS",
            "CLIENT_SECRET",
            "MYAPP_BASE_URL",
            "X_AUTH_TOKEN",
            "OS_ENV_VAR",
            "TRANSPORT",
            "PATH",
            "HOME",
        ]
        for k in test_keys:
            c = {"env": {k: "value"}}
            redacted = redact_secrets(c)
            upstream_says_secret = redacted["env"][k] == "[REDACTED]"
            local_says_secret = migrate._is_secret_env_key(k)
            assert local_says_secret == upstream_says_secret, (
                f"matcher divergence on key={k!r}: "
                f"redact_secrets={upstream_says_secret}, "
                f"_is_secret_env_key={local_says_secret}"
            )

    def test_marker_value_is_recognized(self) -> None:
        # Spot-check the import we rely on for idempotency.
        assert is_marker("__KMS_REF__KMS_HANDLE_abc123__") is True
        assert is_marker("plain-secret") is False
        assert is_marker("__KMS_REF__BAD__") is False  # wrong handle prefix


# ---------------------------------------------------------------------------
# Fresh-migrate
# ---------------------------------------------------------------------------


class TestFreshMigrate:
    def test_no_rows_is_noop(
        self, sqlite_engine: Engine, fernet_key: str, tmp_path: Path
    ) -> None:
        jsonl = tmp_path / "audit.jsonl"
        summary = migrate.run_migration(
            sqlite_engine, jsonl_path=jsonl, dry_run=False
        )
        assert summary.pre_count == 0
        assert summary.migrated == 0
        assert summary.skipped_marker == 0
        assert summary.skipped_clean == 0
        assert summary.audit_lines_emitted == 0
        assert summary.exit_code == 0
        assert _audit_lines(jsonl) == []

    def test_no_plaintext_is_noop(
        self, sqlite_engine: Engine, fernet_key: str, tmp_path: Path
    ) -> None:
        row = _make_row(
            sqlite_engine,
            name="opendesign",
            env={"LOG_LEVEL": "info", "MY_MCP_TRANSPORT": "stdio"},
        )
        jsonl = tmp_path / "audit.jsonl"
        summary = migrate.run_migration(
            sqlite_engine, jsonl_path=jsonl, dry_run=False
        )
        # Row had no secret keys at all → counted as skipped_clean.
        assert summary.migrated == 0
        assert summary.skipped_clean == 1
        assert summary.skipped_marker == 0
        assert summary.exit_code == 0
        # Original row unchanged.
        reread = _read_row(sqlite_engine, row.id)
        assert reread is not None
        assert reread.config["env"]["LOG_LEVEL"] == "info"
        assert reread.config["env"]["MY_MCP_TRANSPORT"] == "stdio"
        assert _audit_lines(jsonl) == []

    def test_one_secret_value_gets_marker(
        self, sqlite_engine: Engine, fernet_key: str, tmp_path: Path
    ) -> None:
        row = _make_row(
            sqlite_engine,
            name="opendesign",
            env={"OPEN_DESIGN_API_KEY": "sk-test-plain"},
        )
        jsonl = tmp_path / "audit.jsonl"
        summary = migrate.run_migration(
            sqlite_engine, jsonl_path=jsonl, dry_run=False
        )
        assert summary.migrated == 1
        assert summary.audit_lines_emitted == 1
        assert summary.exit_code == 1
        # Row carries marker now; plaintext is gone.
        reread = _read_row(sqlite_engine, row.id)
        assert reread is not None
        new_value = reread.config["env"]["OPEN_DESIGN_API_KEY"]
        assert is_marker(new_value), f"expected marker, got {new_value!r}"
        assert "sk-test-plain" not in new_value
        # bound_handles captured the binding.
        bindings = reread.instance_metadata["bound_handles"]
        assert len(bindings) == 1
        b = bindings[0]
        assert b["env_key"] == "OPEN_DESIGN_API_KEY"
        assert b["actor"] == migrate.DEFAULT_ACTOR
        assert b["handle"].startswith("KMS_HANDLE_")
        assert len(b["fingerprint"]) == 16
        # Audit line shape.
        lines = _audit_lines(jsonl)
        assert len(lines) == 1
        line = lines[0]
        assert set(line.keys()) >= {
            "ts",
            "event",
            "name",
            "actor",
            "secret_ref",
            "idempotency_key",
            "trace_id",
        }
        assert line["event"] == "migration_rewrite"
        assert line["name"] == "opendesign"
        assert line["secret_ref"] == b["handle"]
        assert "sk-test-plain" not in json.dumps(line)

    def test_multiple_secrets_in_one_row(
        self, sqlite_engine: Engine, fernet_key: str, tmp_path: Path
    ) -> None:
        row = _make_row(
            sqlite_engine,
            name="multi",
            env={
                "OPEN_DESIGN_API_KEY": "k1-plain",
                "OPENDESIGN_TOKEN": "t1-plain",
                "MY_PASSWORD": "p1-plain",
                "LOG_LEVEL": "info",  # not a secret
            },
        )
        jsonl = tmp_path / "audit.jsonl"
        summary = migrate.run_migration(
            sqlite_engine, jsonl_path=jsonl, dry_run=False
        )
        assert summary.migrated == 1
        # Three secret-key env entries were rewritten; one preserved.
        reread = _read_row(sqlite_engine, row.id)
        assert reread is not None
        env = reread.config["env"]
        assert is_marker(env["OPEN_DESIGN_API_KEY"])
        assert is_marker(env["OPENDESIGN_TOKEN"])
        assert is_marker(env["MY_PASSWORD"])
        assert env["LOG_LEVEL"] == "info"
        # Three bindings persisted.
        bindings = reread.instance_metadata["bound_handles"]
        assert len(bindings) == 3
        env_keys = {b["env_key"] for b in bindings}
        assert env_keys == {"OPEN_DESIGN_API_KEY", "OPENDESIGN_TOKEN", "MY_PASSWORD"}
        # One audit line per row rewritten.
        assert len(_audit_lines(jsonl)) == 1


# ---------------------------------------------------------------------------
# Idempotent rerun
# ---------------------------------------------------------------------------


class TestIdempotentRerun:
    def test_rerun_produces_zero_new_mints_and_audits(
        self, sqlite_engine: Engine, fernet_key: str, tmp_path: Path
    ) -> None:
        _make_row(
            sqlite_engine,
            name="opendesign",
            env={"OPEN_DESIGN_API_KEY": "sk-test-plain"},
        )
        jsonl = tmp_path / "audit.jsonl"

        # First pass: live run, emits one audit line.
        s1 = migrate.run_migration(
            sqlite_engine, jsonl_path=jsonl, dry_run=False
        )
        assert s1.migrated == 1
        assert s1.audit_lines_emitted == 1

        # Snapshot store size and audit line count before the rerun.
        before_store_size = kms_lite_mod._get_store().size()
        before_lines = _audit_lines(jsonl)
        assert before_store_size == 1
        assert len(before_lines) == 1

        # Second pass: idempotent — zero new mints, zero new audit lines.
        s2 = migrate.run_migration(
            sqlite_engine, jsonl_path=jsonl, dry_run=False
        )
        assert s2.migrated == 0
        assert s2.skipped_marker == 1  # the row's secret is now a marker
        assert s2.audit_lines_emitted == 0
        after_store_size = kms_lite_mod._get_store().size()
        after_lines = _audit_lines(jsonl)
        # Counter assertions — the dispatch spec explicitly calls these
        # out ("idempotent-rerun (zero additional mints/audits — assert
        # via counters)").
        assert after_store_size == before_store_size, (
            "rerun must NOT mint new handles — store size grew"
        )
        assert len(after_lines) == len(before_lines), (
            "rerun must NOT emit new audit lines"
        )
        # Exit code 0 on the no-op rerun.
        assert s2.exit_code == 0


# ---------------------------------------------------------------------------
# Mixed rows
# ---------------------------------------------------------------------------


class TestMixedRows:
    def test_mixed_plaintext_marker_clean(
        self, sqlite_engine: Engine, fernet_key: str, tmp_path: Path
    ) -> None:
        plain_row = _make_row(
            sqlite_engine,
            name="needs-migration",
            env={"OPEN_DESIGN_API_KEY": "plain-secret"},
        )
        marker_row = _make_row(
            sqlite_engine,
            name="already-migrated",
            env={
                "OPEN_DESIGN_API_KEY": "__KMS_REF__KMS_HANDLE_pre123__"
            },
        )
        clean_row = _make_row(
            sqlite_engine,
            name="no-secrets",
            env={"LOG_LEVEL": "info"},
        )

        jsonl = tmp_path / "audit.jsonl"
        summary = migrate.run_migration(
            sqlite_engine, jsonl_path=jsonl, dry_run=False
        )

        # Only the plaintext row should have been rewritten.
        assert summary.pre_count == 3
        assert summary.migrated == 1
        assert summary.skipped_clean == 1
        assert summary.skipped_marker == 1  # the marker-only row
        assert summary.audit_lines_emitted == 1

        # Plaintext row is now a marker (different handle from the
        # pre-existing one on marker_row).
        plain_reread = _read_row(sqlite_engine, plain_row.id)
        assert plain_reread is not None
        assert is_marker(plain_reread.config["env"]["OPEN_DESIGN_API_KEY"])
        # Marker row untouched.
        marker_reread = _read_row(sqlite_engine, marker_row.id)
        assert marker_reread is not None
        assert (
            marker_reread.config["env"]["OPEN_DESIGN_API_KEY"]
            == "__KMS_REF__KMS_HANDLE_pre123__"
        )
        # Clean row untouched.
        clean_reread = _read_row(sqlite_engine, clean_row.id)
        assert clean_reread is not None
        assert clean_reread.config["env"]["LOG_LEVEL"] == "info"


# ---------------------------------------------------------------------------
# Audit-line shape
# ---------------------------------------------------------------------------


class TestAuditLineShape:
    def test_audit_line_has_exact_field_set(
        self, sqlite_engine: Engine, fernet_key: str, tmp_path: Path
    ) -> None:
        row = _make_row(
            sqlite_engine,
            name="opendesign",
            env={"OPEN_DESIGN_API_KEY": "plain-XYZ"},
        )
        jsonl = tmp_path / "audit.jsonl"
        migrate.run_migration(sqlite_engine, jsonl_path=jsonl, dry_run=False)
        lines = _audit_lines(jsonl)
        assert len(lines) == 1
        line = lines[0]
        # Field set exactly per dispatch spec.
        assert set(line.keys()) == {
            "ts",
            "event",
            "name",
            "actor",
            "secret_ref",
            "idempotency_key",
            "trace_id",
        }
        assert line["event"] == "migration_rewrite"
        assert line["name"] == "opendesign"
        assert line["actor"] == migrate.DEFAULT_ACTOR
        assert isinstance(line["secret_ref"], str)
        assert line["secret_ref"].startswith("KMS_HANDLE_")
        assert len(line["idempotency_key"]) == 64  # sha256 hex
        # trace_id is uuid4 hex (32 chars).
        assert len(line["trace_id"]) == 32
        uuid.UUID(hex=line["trace_id"])  # raises if malformed
        # ts is ISO-8601.
        assert "T" in line["ts"]

    def test_secret_ref_is_handle_never_plaintext(
        self, sqlite_engine: Engine, fernet_key: str, tmp_path: Path
    ) -> None:
        plaintext_marker = "VERY-UNIQUE-PLAINTEXT-MARKER-XYZQ-12345"
        _make_row(
            sqlite_engine,
            name="opendesign",
            env={"OPEN_DESIGN_API_KEY": plaintext_marker},
        )
        jsonl = tmp_path / "audit.jsonl"
        migrate.run_migration(sqlite_engine, jsonl_path=jsonl, dry_run=False)
        line = _audit_lines(jsonl)[0]
        # The plaintext MUST NOT appear anywhere in the audit line.
        assert plaintext_marker not in json.dumps(line)
        # secret_ref is a handle — never a marker (the dispatch spec
        # says "secret_ref=<handle ONLY>", and the marker contains the
        # handle plus the marker prefix/suffix — using the bare handle
        # keeps the audit line robust even if the marker format changes).
        assert "__KMS_REF__" not in line["secret_ref"]
        assert line["secret_ref"].startswith("KMS_HANDLE_")


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_touches_nothing(
        self, sqlite_engine: Engine, fernet_key: str, tmp_path: Path
    ) -> None:
        original_env = {"OPEN_DESIGN_API_KEY": "sk-dryrun-plain"}
        row = _make_row(
            sqlite_engine, name="opendesign", env=original_env
        )
        jsonl = tmp_path / "audit.jsonl"

        before_size = kms_lite_mod._get_store().size()
        summary = migrate.run_migration(
            sqlite_engine, jsonl_path=jsonl, dry_run=True
        )

        # Summary still counts what WOULD happen.
        assert summary.dry_run is True
        assert summary.migrated == 1
        # But: NO audit line emitted.
        assert summary.audit_lines_emitted == 0
        assert _audit_lines(jsonl) == []
        # And: NO new mints.
        after_size = kms_lite_mod._get_store().size()
        assert after_size == before_size, (
            "dry-run must NOT call kms_request — store size grew"
        )
        # Row on disk is unchanged.
        reread = _read_row(sqlite_engine, row.id)
        assert reread is not None
        assert reread.config["env"]["OPEN_DESIGN_API_KEY"] == "sk-dryrun-plain"


# ---------------------------------------------------------------------------
# Per-row failure isolation (PR6)
# ---------------------------------------------------------------------------


class TestPerRowFailureIsolation:
    def test_one_poisoned_row_does_not_block_others(
        self, sqlite_engine: Engine, fernet_key: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If one row's rewrite raises, the others MUST still migrate."""
        good_row_a = _make_row(
            sqlite_engine,
            name="good-a",
            env={"OPEN_DESIGN_API_KEY": "plain-A"},
        )
        poisoned_row = _make_row(
            sqlite_engine,
            name="poisoned",
            env={"OPEN_DESIGN_API_KEY": "plain-P"},
        )
        good_row_b = _make_row(
            sqlite_engine,
            name="good-b",
            env={"OPEN_DESIGN_API_KEY": "plain-B"},
        )

        # Monkey-patch kms_request to raise ONLY for the poisoned name.
        real_kms_request = migrate.kms_request

        def selective_kms_request(service: str, reason: str, actor: str = "system"):
            if service == "poisoned":
                raise RuntimeError("simulated KMS mint failure")
            return real_kms_request(service=service, reason=reason, actor=actor)

        monkeypatch.setattr(migrate, "kms_request", selective_kms_request)

        jsonl = tmp_path / "audit.jsonl"
        summary = migrate.run_migration(
            sqlite_engine, jsonl_path=jsonl, dry_run=False, fail_fast=False
        )

        # Two good rows migrated; one failure recorded.
        assert summary.pre_count == 3
        assert summary.migrated == 2
        assert summary.total_failures == 1
        assert summary.failures[0].name == "poisoned"
        assert "simulated KMS mint failure" in (summary.failures[0].error or "")
        # Exit code 2 because there were failures.
        assert summary.exit_code == 2
        # Two audit lines emitted (one per migrated row), NOT three.
        assert len(_audit_lines(jsonl)) == 2
        # The poisoned row is UNTOUCHED on disk — the per-row
        # transaction rolled back, leaving the original plaintext.
        poisoned_reread = _read_row(sqlite_engine, poisoned_row.id)
        assert poisoned_reread is not None
        assert (
            poisoned_reread.config["env"]["OPEN_DESIGN_API_KEY"] == "plain-P"
        )
        # Good rows carry markers.
        for good in (good_row_a, good_row_b):
            reread = _read_row(sqlite_engine, good.id)
            assert reread is not None
            assert is_marker(reread.config["env"]["OPEN_DESIGN_API_KEY"])

    def test_fail_fast_aborts_on_first_failure(
        self, sqlite_engine: Engine, fernet_key: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Three rows, the FIRST one is poisoned. With --fail-fast the
        # script aborts at the first failure.
        first_row = _make_row(
            sqlite_engine,
            name="first",
            env={"OPEN_DESIGN_API_KEY": "plain-first"},
        )
        _make_row(
            sqlite_engine,
            name="second",
            env={"OPEN_DESIGN_API_KEY": "plain-second"},
        )
        _make_row(
            sqlite_engine,
            name="third",
            env={"OPEN_DESIGN_API_KEY": "plain-third"},
        )

        real_kms_request = migrate.kms_request

        def selective_kms_request(service: str, reason: str, actor: str = "system"):
            if service == "first":
                raise RuntimeError("simulated first-row failure")
            return real_kms_request(service=service, reason=reason, actor=actor)

        monkeypatch.setattr(migrate, "kms_request", selective_kms_request)

        jsonl = tmp_path / "audit.jsonl"
        summary = migrate.run_migration(
            sqlite_engine,
            jsonl_path=jsonl,
            dry_run=False,
            fail_fast=True,
        )

        assert summary.total_failures == 1
        assert summary.failures[0].name == "first"
        # Second and third were NOT migrated (fail-fast aborted).
        assert summary.migrated == 0
        # The first row is untouched (per-row transaction rolled back).
        first_reread = _read_row(sqlite_engine, first_row.id)
        assert first_reread is not None
        assert (
            first_reread.config["env"]["OPEN_DESIGN_API_KEY"] == "plain-first"
        )


# ---------------------------------------------------------------------------
# Misc: KMS unavailable path
# ---------------------------------------------------------------------------


class TestKMSUnavailable:
    def test_no_key_records_failure_per_row(
        self, sqlite_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No fernet_key fixture — explicit clear of the env var so
        # kms_request raises KMSUnavailableError.
        monkeypatch.delenv("SYSTEM_ENCRYPTION_KEY", raising=False)
        monkeypatch.delenv("SOURCE_CREDENTIAL_KEY", raising=False)
        reset_store_for_tests()

        _make_row(
            sqlite_engine,
            name="opendesign",
            env={"OPEN_DESIGN_API_KEY": "plain-secret"},
        )
        jsonl = tmp_path / "audit.jsonl"
        summary = migrate.run_migration(
            sqlite_engine, jsonl_path=jsonl, dry_run=False
        )

        # Row NOT migrated; failure recorded.
        assert summary.migrated == 0
        assert summary.total_failures == 1
        assert "KMSUnavailableError" in (summary.failures[0].error or "")
        assert summary.exit_code == 2
        # No audit line emitted.
        assert _audit_lines(jsonl) == []


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


class TestCLISurface:
    def test_help_lists_required_flags(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as excinfo:
            migrate.main(["--help"])
        # argparse exits with 0 on --help.
        assert excinfo.value.code == 0
        captured = capsys.readouterr().out
        assert "--dry-run" in captured
        assert "--jsonl-path" in captured
        assert "--fail-fast" in captured
        assert "--actor" in captured
        assert "--db-url" in captured
