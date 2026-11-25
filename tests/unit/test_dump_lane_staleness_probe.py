"""Dump-lane health probe — the READ-ONLY operator probe pins.

WHAT IS PINNED HERE
-------------------
* the THREE STATES and their exit codes — ``FRESH``/0, ``STALE``/76,
  ``NO_MARKER``/75 — plus the ``CONFIG``/78 refusal class;
* the THRESHOLD BOUNDARY — exactly AT the threshold is FRESH, exactly one
  second past it is STALE (and the REPORTED age IS the COMPARED age: one
  integer, no rounding drift between the verdict and the receipt);
* the REFUSAL TO GUESS A CONNECTION — no ``--db-url``, no matter what the
  ambient environment says, is exit 78;
* the NON-FINITE THRESHOLD — ``argparse``'s ``type=float`` ACCEPTS
  ``nan``/``inf``/``+inf``/``infinity``; every one of them must land on the
  house exit-78 refusal path, never a bare traceback (exit 1);
* the DRIVER-vs-DATABASE DISTINCTION — a missing psycopg/sqlmodel is
  ``driver_unavailable`` (run the repo venv's python), NOT
  ``db_unreachable`` (check the LB and the credential);
* the DSN REDACTION in the error path — the password must not survive into
  stderr, not even when the driver exception itself echoes it back;
* the NO-WRITE ASSERTION — the probe issues no ``INSERT``/``UPDATE``/
  ``DELETE``/DDL (structural, by AST: the only SQL it can execute is a
  read-only session ``SET`` plus one bound-parameter ``SELECT``), it opens no
  write path on the filesystem, and a provider failure leaves NO side effect;
* the RUNBOOK COVERAGE — every reason slug the probe can emit has EXACTLY ONE
  action row in ``docs/runbooks/dump-lane-health-probe.md``, and the runbook
  documents no slug the probe cannot emit.

HERMETICITY (hard requirement)
------------------------------
This file NEVER connects to a database and NEVER writes outside ``tmp_path``:

* the ``pg`` provider is exercised through a FAKE engine injected at the
  ``_open_pg_engine`` seam — no socket is ever opened;
* the driver-missing leg blocks the lazy ``sqlmodel`` import BEFORE any
  socket is opened;
* the ``file`` provider reads and writes only inside ``tmp_path``;
* the credential path is exercised by making the fake engine RAISE, so the
  real error path (and its redaction) runs without any network at all.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "audit" / "dump_lane_staleness.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("dump_lane_staleness", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Register BEFORE exec: ``@dataclass`` resolves its own module through
    # ``sys.modules`` (Python 3.14), so an unregistered module fails at
    # decoration time.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe = _load_module()

SOURCE_TEXT = SCRIPT_PATH.read_text(encoding="utf-8")
SOURCE_TREE = ast.parse(SOURCE_TEXT)

# The two SQL statements the probe is ALLOWED to execute. Anything else that
# reaches a driver is a defect, and the AST pins below say so.
ALLOWED_SQL_CONSTANTS = {"MARKER_SELECT_SQL", "READ_ONLY_SESSION_SQL"}

SQL_WRITE_VERBS = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "MERGE",
    "CREATE",
    "DROP",
    "ALTER",
    "TRUNCATE",
    "GRANT",
    "REVOKE",
    "COPY",
    "CALL",
    "DO",
)

DESTRUCTIVE_FS_NAMES = {
    "write_text",
    "write_bytes",
    "mkdir",
    "makedirs",
    "unlink",
    "remove",
    "rmdir",
    "removedirs",
    "truncate",
    "touch",
    "symlink_to",
    "hardlink_to",
    "chmod",
    "chown",
    "copyfile",
    "copy2",
    "copytree",
    "move",
    "rmtree",
}

#: The ONLY filesystem surface the probe may touch. Everything the probe does
#: on disk is a READ.
READ_ONLY_FS_ATTRS = {
    "stat",
    "getmtime",
    "getctime",
    "exists",
    "isfile",
    "isdir",
    "read_text",
    "read_bytes",
    "readlink",
}


# ── helpers ─────────────────────────────────────────────────────────────────


def _parse_result(stdout: str) -> dict[str, str]:
    """Parse the ONE-LINE structured RESULT the receipt carries."""
    lines = [line for line in stdout.splitlines() if line.strip()]
    assert len(lines) == 1, f"expected exactly one stdout line, got {lines!r}"
    assert lines[0].startswith("RESULT: "), lines[0]
    fields: dict[str, str] = {}
    for token in lines[0][len("RESULT: ") :].split():
        key, _, value = token.partition("=")
        fields[key] = value
    return fields


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class _FakeResult:
    def __init__(self, row: Any) -> None:
        self._row = row

    def first(self) -> Any:
        return self._row


class _FakeConn:
    """A connection that RECORDS what it was asked to run."""

    def __init__(self, row: Any, log: list[tuple[str, Any]]) -> None:
        self._row = row
        self._log = log

    def execute(self, statement: Any, params: Any = None) -> _FakeResult:
        self._log.append(("execute", str(statement), params))
        if "select" in str(statement).lower():
            return _FakeResult(self._row)
        return _FakeResult(None)

    def commit(self) -> None:
        self._log.append(("commit", None, None))

    def __enter__(self) -> "_FakeConn":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class _FakeEngine:
    def __init__(self, row: Any) -> None:
        self._row = row
        self.log: list[tuple[str, Any]] = []
        self.disposed = False

    def connect(self) -> _FakeConn:
        return _FakeConn(self._row, self.log)

    def dispose(self) -> None:
        self.disposed = True


def _install_fake_engine(monkeypatch: pytest.MonkeyPatch, row: Any) -> _FakeEngine:
    engine = _FakeEngine(row)
    monkeypatch.setattr(probe, "_open_pg_engine", lambda dsn: engine)
    return engine


def _module_string_constants() -> dict[str, str]:
    out: dict[str, str] = {}
    for node in SOURCE_TREE.body:
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out[target.id] = node.value.value
    return out


def _docstring_nodes() -> set[int]:
    out: set[int] = set()
    for node in ast.walk(SOURCE_TREE):
        if isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                out.add(id(body[0].value))
    return out


def _resolved_sql_constant(node: ast.AST) -> str | None:
    """Resolve the SQL an execution call would run, if it is a known constant."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Call):
        args = node.args
        if args and isinstance(args[0], ast.Name):
            return args[0].id
    return None


# ── the three states + their exit codes ─────────────────────────────────────


class TestStatesAndExitCodes:
    def test_fresh_marker_exits_zero_and_reports_fresh(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        marker = tmp_path / "dump-marker"
        marker.write_text("dump ok\n", encoding="utf-8")
        now = datetime(2026, 11, 25, 0, 0, 0, tzinfo=timezone.utc)
        age = timedelta(seconds=3600)
        os.utime(marker, (now.timestamp() - age.total_seconds(),) * 2)

        rc = probe.main(
            [
                "--provider",
                "file",
                "--marker-file",
                str(marker),
                "--now",
                _iso(now),
            ]
        )
        assert rc == 0, "a fresh marker must exit 0"
        captured = capsys.readouterr()
        fields = _parse_result(captured.out)
        assert fields["state"] == "FRESH"
        assert fields["reason"] == "within_threshold"
        assert fields["lane"] == "pgdumpall"
        assert fields["provider"] == "file"
        assert fields["age_seconds"] == "3600"
        assert fields["max_age_seconds"] == "691200"

    def test_stale_marker_exits_76_and_reports_stale(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        marker = tmp_path / "dump-marker"
        marker.write_text("dump ok\n", encoding="utf-8")
        now = datetime(2026, 11, 25, 0, 0, 0, tzinfo=timezone.utc)
        age = timedelta(days=9)
        os.utime(marker, (now.timestamp() - age.total_seconds(),) * 2)

        rc = probe.main(
            [
                "--provider",
                "file",
                "--marker-file",
                str(marker),
                "--now",
                _iso(now),
            ]
        )
        assert rc == 76, "a stale marker must exit 76"
        fields = _parse_result(capsys.readouterr().out)
        assert fields["state"] == "STALE"
        assert fields["reason"] == "older_than_threshold"
        assert fields["age_seconds"] == str(9 * 86400)

    def test_absent_marker_exits_75_and_reports_no_marker(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        missing = tmp_path / "definitely-absent-marker"
        rc = probe.main(["--provider", "file", "--marker-file", str(missing)])
        assert rc == 75, "an absent marker must exit 75 (absence is a FAILURE)"
        fields = _parse_result(capsys.readouterr().out)
        assert fields["state"] == "NO_MARKER"
        assert fields["reason"] == "marker_file_missing"
        assert fields["last_success_at"] == "none"
        assert fields["age_seconds"] == "none"

    def test_no_marker_never_reports_fresh(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The fail-closed invariant, stated as a pin."""
        rc = probe.main(
            ["--provider", "file", "--marker-file", str(tmp_path / "nope")]
        )
        out = capsys.readouterr().out
        assert rc != 0
        assert "state=FRESH" not in out

    def test_unreadable_marker_directory_is_no_marker(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        directory = tmp_path / "a-directory"
        directory.mkdir()
        rc = probe.main(["--provider", "file", "--marker-file", str(directory)])
        assert rc == 75
        fields = _parse_result(capsys.readouterr().out)
        assert fields["reason"] == "marker_file_unreadable"


class TestThresholdBoundary:
    def test_exactly_at_the_threshold_is_fresh(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        now = datetime(2026, 11, 25, 0, 0, 0, tzinfo=timezone.utc)
        last = now - timedelta(seconds=probe.DEFAULT_MAX_AGE_SECONDS)
        observation = probe.MarkerObservation(last_success_at=last)
        verdict = probe.evaluate(
            observation,
            now=now,
            max_age_seconds=probe.DEFAULT_MAX_AGE_SECONDS,
            lane="pgdumpall",
            provider="file",
        )
        assert verdict.state == "FRESH"
        assert verdict.exit_code == 0
        assert verdict.age_seconds == probe.DEFAULT_MAX_AGE_SECONDS
        assert verdict.age_seconds == 691200
        # And through the CLI, with the marker file mtime at the boundary.
        marker = tmp_path / "marker"
        marker.write_text("x\n", encoding="utf-8")
        os.utime(marker, (last.timestamp(), last.timestamp()))
        rc = probe.main(
            [
                "--provider",
                "file",
                "--marker-file",
                str(marker),
                "--now",
                _iso(now),
            ]
        )
        assert rc == 0
        assert _parse_result(capsys.readouterr().out)["state"] == "FRESH"

    def test_one_second_past_the_threshold_is_stale(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        now = datetime(2026, 11, 25, 0, 0, 0, tzinfo=timezone.utc)
        last = now - timedelta(seconds=probe.DEFAULT_MAX_AGE_SECONDS + 1)
        observation = probe.MarkerObservation(last_success_at=last)
        verdict = probe.evaluate(
            observation,
            now=now,
            max_age_seconds=probe.DEFAULT_MAX_AGE_SECONDS,
            lane="pgdumpall",
            provider="file",
        )
        assert verdict.state == "STALE"
        assert verdict.exit_code == 76
        assert verdict.age_seconds == probe.DEFAULT_MAX_AGE_SECONDS + 1

        marker = tmp_path / "marker"
        marker.write_text("x\n", encoding="utf-8")
        os.utime(marker, (last.timestamp(), last.timestamp()))
        rc = probe.main(
            [
                "--provider",
                "file",
                "--marker-file",
                str(marker),
                "--now",
                _iso(now),
            ]
        )
        assert rc == 76
        assert _parse_result(capsys.readouterr().out)["state"] == "STALE"

    def test_default_threshold_is_eight_days(self) -> None:
        assert probe.DEFAULT_MAX_AGE_SECONDS == 691_200
        assert probe.DEFAULT_MAX_AGE_SECONDS == 8 * 86400
        parser = probe.build_parser()
        args = parser.parse_args([])
        assert args.max_age_hours == 192.0

    def test_max_age_hours_override_is_honoured(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        marker = tmp_path / "marker"
        marker.write_text("x\n", encoding="utf-8")
        now = datetime(2026, 11, 25, 0, 0, 0, tzinfo=timezone.utc)
        last = now - timedelta(hours=50)
        os.utime(marker, (last.timestamp(), last.timestamp()))
        rc = probe.main(
            [
                "--provider",
                "file",
                "--marker-file",
                str(marker),
                "--now",
                _iso(now),
                "--max-age-hours",
                "48",
            ]
        )
        assert rc == 76
        fields = _parse_result(capsys.readouterr().out)
        assert fields["max_age_seconds"] == str(48 * 3600)


class TestConfigRefusals:
    def test_missing_db_url_refuses_with_exit_78(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = probe.main([])
        assert rc == 78, "no explicit connection must refuse with exit 78"
        fields = _parse_result(capsys.readouterr().out)
        assert fields["state"] == "CONFIG_REFUSED"
        assert fields["reason"] == "missing_db_url"

    def test_never_guesses_a_connection_from_the_environment(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Ambient credentials must NOT become the connection."""
        monkeypatch.setenv("POSTGRES_URL", "postgresql://u:p@host/db")
        monkeypatch.setenv("POSTGRES_DB", "ensemble_prod")
        monkeypatch.setenv("POSTGRES_HOST", "10.44.0.2")
        monkeypatch.setenv("DATABASE_URL_POSTGRES", "postgresql://u:p@host/db")
        monkeypatch.setenv("PGHOST", "10.44.0.2")
        monkeypatch.setenv("PGDATABASE", "ensemble_prod")
        rc = probe.main([])
        assert rc == 78
        fields = _parse_result(capsys.readouterr().out)
        assert fields["reason"] == "missing_db_url"

    def test_file_provider_without_marker_file_refuses_with_exit_78(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = probe.main(["--provider", "file"])
        assert rc == 78
        fields = _parse_result(capsys.readouterr().out)
        assert fields["reason"] == "missing_marker_file"

    def test_non_positive_max_age_refuses_with_exit_78(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = probe.main(["--db-url", "postgresql://x@127.0.0.1:1/x", "--max-age-hours", "0"])
        assert rc == 78
        fields = _parse_result(capsys.readouterr().out)
        assert fields["reason"] == "bad_max_age_hours"

    def test_non_finite_max_age_refuses_with_exit_78(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``argparse``'s ``type=float`` ACCEPTS the non-finite spellings.

        A NaN threshold never trips the ``<= 0`` comparison and an infinite
        one OVERFLOWS the conversion to seconds — at base both escaped as a
        bare traceback (exit 1), OUTSIDE the 78 family. A FINITE-but-huge
        value ("1e305") passes ``math.isfinite`` but its x3600 multiply
        OVERFLOWS to inf, hitting the same OverflowError. Every spelling must
        land on the house exit-78 refusal path instead.
        """
        marker = tmp_path / "dump-marker"
        marker.write_text("dump ok\n", encoding="utf-8")
        for spelling in ("nan", "inf", "+inf", "infinity", "1e305"):
            rc = probe.main(
                [
                    "--provider",
                    "file",
                    "--marker-file",
                    str(marker),
                    "--max-age-hours",
                    spelling,
                ]
            )
            captured = capsys.readouterr()
            assert rc == 78, f"--max-age-hours {spelling!r} must refuse with exit 78"
            fields = _parse_result(captured.out)
            assert fields["reason"] == "bad_max_age_hours", spelling
            assert "Traceback" not in captured.err, spelling

    def test_unparseable_now_refuses_with_exit_78(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = probe.main(
            ["--provider", "file", "--marker-file", "/dev/null", "--now", "not-a-date"]
        )
        assert rc == 78
        fields = _parse_result(capsys.readouterr().out)
        assert fields["reason"] == "bad_now"

    def test_unknown_argument_refuses_with_exit_78(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """argparse's default failure is exit 2 — the house idiom is 78."""
        rc = probe.main(["--not-a-flag"])
        assert rc == 78
        fields = _parse_result(capsys.readouterr().out)
        assert fields["state"] == "CONFIG_REFUSED"


class TestDsnRedaction:
    SECRET = "sup3rs3cret-pw"

    def _dsn(self) -> str:
        return f"postgresql+psycopg://ensemble:{self.SECRET}@10.44.0.2:5432/ensemble_prod"

    def test_password_is_redacted_in_the_error_path(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def _boom(dsn: str) -> Any:
            raise RuntimeError(
                f"could not connect using {dsn} (password={self.SECRET})"
            )

        monkeypatch.setattr(probe, "_open_pg_engine", _boom)
        rc = probe.main(["--db-url", self._dsn()])
        captured = capsys.readouterr()
        assert rc == 75, "an unreachable database is NO-MARKER, never FRESH"
        assert self.SECRET not in captured.err
        assert self.SECRET not in captured.out
        assert "***" in captured.err
        fields = _parse_result(captured.out)
        assert fields["state"] == "NO_MARKER"
        assert fields["reason"] == "db_unreachable"

    def test_redact_dsn_masks_the_url_password(self) -> None:
        masked = probe.redact_dsn(self._dsn())
        assert self.SECRET not in masked
        assert "***" in masked

    def test_redact_dsn_masks_the_keyword_password(self) -> None:
        masked = probe.redact_dsn(
            "host=10.44.0.2 port=5432 dbname=ensemble_prod user=ensemble "
            f"password={self.SECRET}"
        )
        assert self.SECRET not in masked
        assert "***" in masked

    def test_scrub_secrets_removes_the_password_from_free_text(self) -> None:
        scrubbed = probe.scrub_secrets(
            f"driver said: password={self.SECRET} rejected", self._dsn()
        )
        assert self.SECRET not in scrubbed
        assert "***" in scrubbed

    def test_dsn_secrets_extracts_both_shapes(self) -> None:
        assert probe.dsn_secrets(self._dsn()) == [self.SECRET]
        assert probe.dsn_secrets(f"password={self.SECRET}") == [self.SECRET]
        assert probe.dsn_secrets("postgresql://ensemble@h/db") == []


class TestNoWritePath:
    def test_the_only_sql_constants_carry_no_write_verb(self) -> None:
        constants = _module_string_constants()
        for name in ALLOWED_SQL_CONSTANTS:
            assert name in constants, f"{name} must exist as a module constant"
            upper = constants[name].upper()
            for verb in SQL_WRITE_VERBS:
                assert not re.search(
                    rf"\b{verb}\b", upper
                ), f"{name} must not contain the write verb {verb}"

    def test_the_select_reads_the_documented_heartbeat_table(self) -> None:
        """No drift between the table constant and the statement."""
        constants = _module_string_constants()
        assert constants["HEARTBEAT_TABLE"] in constants["MARKER_SELECT_SQL"]
        assert constants["MARKER_SELECT_SQL"].upper().startswith("SELECT ")
        # The lane is a BOUND parameter, never interpolated into the SQL.
        assert ":lane" in constants["MARKER_SELECT_SQL"]
        assert "pgdumpall" not in constants["MARKER_SELECT_SQL"]

    def test_every_executed_statement_is_one_of_the_allowed_constants(self) -> None:
        """The precise, non-vacuous no-write pin: only two statements can run."""
        seen: list[str] = []
        for node in ast.walk(SOURCE_TREE):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in (
                    "execute",
                    "exec_driver_sql",
                    "executemany",
                    "executescript",
                    "scalar",
                ):
                    assert node.args, f"a call at line {node.lineno} has no SQL"
                    resolved = _resolved_sql_constant(node.args[0])
                    assert resolved in ALLOWED_SQL_CONSTANTS, (
                        f"line {node.lineno} executes an unapproved statement: "
                        f"{resolved!r}"
                    )
                    seen.append(resolved)
        assert seen, "the probe must still issue its read-only statements"

    def test_no_string_constant_anywhere_carries_a_write_verb(self) -> None:
        """A belt-and-braces scan over every non-docstring string literal."""
        docstrings = _docstring_nodes()
        offenders: list[tuple[int, str]] = []
        for node in ast.walk(SOURCE_TREE):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
            ):
                upper = node.value.upper()
                for verb in SQL_WRITE_VERBS:
                    if re.search(rf"\b{verb}\b", upper):
                        offenders.append((node.lineno, node.value[:120]))
        assert not offenders, f"write verbs found in string literals: {offenders}"

    def test_the_probe_opens_no_write_path_on_the_filesystem(self) -> None:
        offenders: list[str] = []
        for node in ast.walk(SOURCE_TREE):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    if node.func.attr in DESTRUCTIVE_FS_NAMES:
                        offenders.append(f"line {node.lineno}: {node.func.attr}")
                    if node.func.attr == "open":
                        mode = ""
                        if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                            mode = str(node.args[1].value)
                        for keyword in node.keywords:
                            if keyword.arg == "mode" and isinstance(
                                keyword.value, ast.Constant
                            ):
                                mode = str(keyword.value.value)
                        if any(flag in mode for flag in ("w", "a", "x", "+")):
                            offenders.append(f"line {node.lineno}: open(mode={mode!r})")
                elif isinstance(node.func, ast.Name) and node.func.id in {
                    "open",
                    "remove",
                    "unlink",
                    "makedirs",
                    "mkdir",
                }:
                    offenders.append(f"line {node.lineno}: {node.func.id}()")
        assert not offenders, f"the probe must not write: {offenders}"

    def test_every_filesystem_call_the_probe_makes_is_a_read(self) -> None:
        """The positive half of the no-write pin: reads only, nothing else."""
        fs_roots = {"os", "Path", "pathlib", "PurePath", "shutil"}
        offenders: list[str] = []
        seen_reads: list[str] = []
        for node in ast.walk(SOURCE_TREE):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                if node.value.id in fs_roots:
                    if node.attr not in READ_ONLY_FS_ATTRS:
                        offenders.append(
                            f"line {node.lineno}: {node.value.id}.{node.attr}"
                        )
                    else:
                        seen_reads.append(f"{node.value.id}.{node.attr}")
        assert not offenders, f"non-read filesystem surface: {offenders}"
        assert seen_reads, "the probe must read the marker it is asked about"

    def test_a_provider_failure_leaves_no_side_effect(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed provider must not create, move, or touch anything."""
        marker = tmp_path / "marker"
        marker.write_text("payload\n", encoding="utf-8")
        before_stat = marker.stat()
        before_listing = sorted(p.name for p in tmp_path.iterdir())

        def _boom(dsn: str) -> Any:
            raise RuntimeError("connection refused")

        monkeypatch.setattr(probe, "_open_pg_engine", _boom)
        rc = probe.main(
            ["--db-url", "postgresql://ensemble@127.0.0.1:1/ensemble_prod"]
        )
        assert rc == 75

        after_stat = marker.stat()
        after_listing = sorted(p.name for p in tmp_path.iterdir())
        assert after_listing == before_listing, "the probe created a file"
        assert after_stat.st_size == before_stat.st_size
        assert after_stat.st_mtime == before_stat.st_mtime
        assert marker.read_text(encoding="utf-8") == "payload\n"


class TestPgProvider:
    def test_the_pg_provider_issues_read_only_session_then_one_bound_select(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        last = datetime(2026, 11, 24, 2, 0, 0, tzinfo=timezone.utc)
        engine = _install_fake_engine(monkeypatch, (last,))

        rc = probe.main(
            [
                "--db-url",
                "postgresql://ensemble@10.44.0.2:5432/ensemble_prod",
                "--now",
                "2026-11-25T00:00:00Z",
            ]
        )
        assert rc == 0
        fields = _parse_result(capsys.readouterr().out)
        assert fields["state"] == "FRESH"
        assert fields["provider"] == "pg"
        assert fields["last_success_at"] == "2026-11-24T02:00:00Z"

        # Exactly: the read-only session SET, a COMMIT (so the SET is durable),
        # then ONE bound-parameter SELECT. Nothing else.
        assert [entry[0] for entry in engine.log] == ["execute", "commit", "execute"]
        assert engine.log[0][1] == probe.READ_ONLY_SESSION_SQL
        assert engine.log[2][1] == probe.MARKER_SELECT_SQL
        # The lane is a BOUND parameter, never interpolated into the SQL.
        assert engine.log[2][2] == {"lane": "pgdumpall"}
        assert "pgdumpall" not in engine.log[2][1]
        assert engine.disposed is True

    def test_a_missing_heartbeat_row_is_no_marker(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _install_fake_engine(monkeypatch, None)
        rc = probe.main(
            ["--db-url", "postgresql://ensemble@10.44.0.2:5432/ensemble_prod"]
        )
        assert rc == 75
        fields = _parse_result(capsys.readouterr().out)
        assert fields["reason"] == "marker_row_missing"

    def test_a_query_failure_is_no_marker_not_a_crash(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        class _ExplodingEngine(_FakeEngine):
            def connect(self) -> Any:
                raise RuntimeError("relation does not exist")

        monkeypatch.setattr(probe, "_open_pg_engine", lambda dsn: _ExplodingEngine(None))
        rc = probe.main(
            ["--db-url", "postgresql://ensemble@10.44.0.2:5432/ensemble_prod"]
        )
        assert rc == 75
        fields = _parse_result(capsys.readouterr().out)
        assert fields["reason"] == "db_query_error"

    def test_a_missing_driver_is_driver_unavailable_not_db_unreachable(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A missing psycopg/sqlmodel is an OBSERVABILITY gap, NOT a dead DB.

        The operator's fix for ``driver_unavailable`` is "run the repo venv's
        python"; the fix for ``db_unreachable`` is "check the LB and the
        credential". Collapsing the two sends the operator to the wrong place,
        so the two reasons must never share a code path.
        """
        real_open = probe._open_pg_engine

        def _no_driver(dsn: str) -> Any:
            raise ModuleNotFoundError("No module named 'psycopg'")

        monkeypatch.setattr(probe, "_open_pg_engine", _no_driver)
        rc = probe.main(
            ["--db-url", "postgresql://ensemble@10.44.0.2:5432/ensemble_prod"]
        )
        captured = capsys.readouterr()
        assert rc == 75, "an unobservable marker is NO-MARKER, never FRESH"
        fields = _parse_result(captured.out)
        assert fields["state"] == "NO_MARKER"
        assert fields["reason"] == "driver_unavailable"

        # Second leg: the REAL ``_open_pg_engine`` with the lazy import
        # blocked. The ImportError is raised BEFORE any socket is opened, so
        # this leg stays hermetic — no database, no network.
        monkeypatch.setattr(probe, "_open_pg_engine", real_open)
        monkeypatch.setitem(sys.modules, "sqlmodel", None)
        rc = probe.main(
            ["--db-url", "postgresql://ensemble@10.44.0.2:5432/ensemble_prod"]
        )
        captured = capsys.readouterr()
        assert rc == 75
        fields = _parse_result(captured.out)
        assert fields["state"] == "NO_MARKER"
        assert fields["reason"] == "driver_unavailable"

    def test_both_providers_feed_the_same_state_machine(self) -> None:
        """One state machine: the verdict shape is provider-independent."""
        now = datetime(2026, 11, 25, 0, 0, 0, tzinfo=timezone.utc)
        last = now - timedelta(days=9)
        shared = {
            "now": now,
            "max_age_seconds": probe.DEFAULT_MAX_AGE_SECONDS,
            "lane": "pgdumpall",
        }
        from_pg = probe.evaluate(
            probe.MarkerObservation(last_success_at=last), provider="pg", **shared
        )
        from_file = probe.evaluate(
            probe.MarkerObservation(last_success_at=last), provider="file", **shared
        )
        assert from_pg.state == from_file.state == "STALE"
        assert from_pg.exit_code == from_file.exit_code == 76
        assert from_pg.age_seconds == from_file.age_seconds
        assert set(vars(from_pg)) == set(vars(from_file))


class TestReportShape:
    def test_stdout_is_exactly_one_result_line(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        marker = tmp_path / "marker"
        marker.write_text("x\n", encoding="utf-8")
        probe.main(["--provider", "file", "--marker-file", str(marker)])
        captured = capsys.readouterr()
        assert len(captured.out.strip().splitlines()) == 1

    def test_the_result_line_carries_every_receipt_field(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        marker = tmp_path / "marker"
        marker.write_text("x\n", encoding="utf-8")
        probe.main(["--provider", "file", "--marker-file", str(marker)])
        fields = _parse_result(capsys.readouterr().out)
        for key in (
            "state",
            "lane",
            "provider",
            "last_success_at",
            "age_seconds",
            "max_age_seconds",
            "evaluated_at",
            "reason",
        ):
            assert key in fields, f"the receipt must carry {key}"

    def test_the_stderr_banner_is_loud_for_a_stale_lane(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        marker = tmp_path / "marker"
        marker.write_text("x\n", encoding="utf-8")
        now = datetime(2026, 11, 25, 0, 0, 0, tzinfo=timezone.utc)
        last = now - timedelta(days=9)
        os.utime(marker, (last.timestamp(), last.timestamp()))
        probe.main(
            [
                "--provider",
                "file",
                "--marker-file",
                str(marker),
                "--now",
                _iso(now),
            ]
        )
        err = capsys.readouterr().err
        assert "*** STALE ***" in err
        assert "needs_emperor_word" in err
        assert err.strip().startswith("=")

    def test_the_stderr_banner_is_loud_for_an_unobservable_lane(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        probe.main(["--provider", "file", "--marker-file", str(tmp_path / "nope")])
        err = capsys.readouterr().err
        assert "*** NO-MARKER ***" in err
        assert "Absence of evidence is a FAILURE" in err

    def test_a_fresh_lane_is_not_loud(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        marker = tmp_path / "marker"
        marker.write_text("x\n", encoding="utf-8")
        probe.main(["--provider", "file", "--marker-file", str(marker)])
        err = capsys.readouterr().err
        assert "***" not in err
        assert "[DUMP-LANE] state=FRESH" in err


class TestRunbookCoverage:
    """The reason vocabulary and the runbook action table must AGREE.

    The probe publishes a CLOSED reason vocabulary; the runbook maps every
    slug to an operator action. A slug the probe can emit but the runbook
    does not document is an operator dead-end — the exact failure the
    ``driver_unavailable`` slug had (documented, never emitted). This pin
    closes the loop in BOTH directions: no undocumented slug, and no
    runbook row for a slug the probe cannot emit.
    """

    RUNBOOK_PATH = REPO_ROOT / "docs" / "runbooks" / "dump-lane-health-probe.md"

    #: The FIRST cell of a table row: a backticked, lowercase slug.
    ROW_SLUG_RE = re.compile(r"^\|\s*`([a-z_]+)`\s*\|", re.MULTILINE)

    def test_every_reason_slug_has_exactly_one_runbook_action_row(self) -> None:
        vocabulary = sorted(
            value
            for name, value in vars(probe).items()
            if name.startswith("REASON_") and isinstance(value, str)
        )
        assert vocabulary, "the probe must publish a reason vocabulary"

        text = self.RUNBOOK_PATH.read_text(encoding="utf-8")
        row_slugs = self.ROW_SLUG_RE.findall(text)

        missing = [slug for slug in vocabulary if row_slugs.count(slug) != 1]
        assert not missing, (
            "every reason slug needs EXACTLY ONE runbook action row; "
            f"missing or duplicated: {missing} — vocabulary: {vocabulary}, "
            f"runbook rows: {sorted(set(row_slugs))}"
        )
        unknown = sorted(set(row_slugs) - set(vocabulary))
        assert not unknown, (
            f"the runbook documents reason slugs the probe cannot emit: {unknown}"
        )
