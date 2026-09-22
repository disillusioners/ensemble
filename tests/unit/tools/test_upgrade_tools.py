"""Unit tests for ``daemon/tools/upgrade_tools.py`` — the P2.2 tool surface
(all 4 tools: release_info, upgrade_status, system_restart, system_upgrade).

Covers the phase2-plan T6/T7/T8 acceptance translated from session-smoke to
committed, reproducible-from-tree tests (review minor #3):

* **Read-pair parity (T6)** — ``release_info`` output fields 1:1 against the
  REAL ``scripts/upgrade/status.sh`` field set, on a /tmp fixture install
  tree with the REAL P2.1 journal schema (written by lib.sh itself:
  releases/current symlink, manifest identity fields, in_flight txn,
  history, quarantine, lock, .launcher-state). ``upgrade_status``
  round-trips a run: run_id from an armed pending_op → in-flight read →
  terminal read keyed by the SAME run_id (the cross-death join).
* **Refusal matrix** — EVERY distinct ``reason=<token>`` asserted by name,
  each in its own test (tool-api-design §2 error vocabulary).
* **3-factor LIVE gate (§4.3)** — each factor missing ALONE refuses;
  spoofed origin sources stamp NO window; fabricated user_confirmed alone
  refuses; the PASS case runs ONLY under a FAKE live marker with a /tmp
  fixture install dir (never a real live anything).
* **Nonce lifecycle through the tool** — mint on dry_run → consume on armed
  → replay refused → TTL expiry.
* **Sequencing (D2/D3)** — armed tools return with NO process spawned
  inside the call (Popen recorder + marker + journal assertions); the
  restart banner reads RESTART SCHEDULED, the upgrade banner UPGRADE
  ARMED (N5 wording, P2.3 — armed/deferred, not scheduled); a second
  arm while active is pipeline-busy naming the run_id.
* **dry_run default TRUE** — the default call mutates nothing (journal
  byte-identical, no lock, no marker, zero spawns), except the labeled
  live-nonce mint on a fake-live dry_run.

Live-safety: no test touches ~/agents-ensemble, port 9797, prod DB, or any
real install. ``ENSEMBLE_SELF_ENV`` is monkeypatched; install dirs are
redirected to ``tmp_path`` fixtures; the only subprocess invocations are
read-only status.sh runs and restart.sh refusal probes (the deliberately-
mismatched --run-id preflight, and the M6 no-arg/explicit-target checks —
every one exits 78 BEFORE any stop step). Subprocess env dicts carry a
fake tmp HOME (M3): no test can reach a real ~/agents-ensemble* path.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

import daemon.tools.upgrade_tools as ut
from daemon.tools import upgrade_journal as uj
from daemon.tools.job_queue import create_job_tools
from daemon.tools.upgrade_tools import create_upgrade_tools

# Repo root: tests/unit/tools/test_upgrade_tools.py -> parents[3].
REPO_ROOT = Path(__file__).resolve().parents[3]
LIB_SH = REPO_ROOT / "scripts" / "upgrade" / "lib.sh"

UPGRADE_TOOL_NAMES = {"release_info", "upgrade_status", "system_restart", "system_upgrade"}

# A sandbox port for the status.sh parity + executor-env fixtures: none of
# dev 8079 / demo 7979 / prod 9797, and never bound by these tests. Same
# name+type as test_upgrade_journal.py's SANDBOX_PORT (a shared test module
# is P2.3 — until then keep the two aligned).
SANDBOX_PORT = 8399

INSTANCE_ID = "inst-upgrade-tests-1"


# ── Fixture builders ─────────────────────────────────────────────────────────


def _write_manifest(install: Path, version: str, *, rollback_safe: bool = True) -> None:
    rel = install / "releases" / version
    rel.mkdir(parents=True, exist_ok=True)
    (rel / "manifest.json").write_text(
        json.dumps(
            {
                "version": version,
                "binary_version": f"v{version}",
                "staged_at": "2026-08-22T09:00:00Z",
                "rollback_safe": rollback_safe,
                "known_schema_gen": 14,
            }
        ),
        encoding="utf-8",
    )


def _isolated_home(base: Path) -> str:
    """M3 (P2.2 fix pass 2026-08-23): a fake HOME for subprocess env dicts.

    Sandbox ``resolve_env`` canon-checks the REAL ``$HOME/agents-ensemble*``
    paths (``_canon_dir "$HOME/agents-ensemble"`` — a live-path READ on any
    host with an install), and a ``live`` target would read the install's
    ``.env`` directly. Pointing HOME at a throwaway tmp dir removes the last
    real-home dependency from these tests — no subprocess here can reach a
    real ``~/agents-ensemble*`` path."""
    home = base / "fake-home"
    home.mkdir(exist_ok=True)
    return str(home)


@pytest.fixture
def install(tmp_path: Path) -> Path:
    """Staged-install fixture: journal (Python writer), two releases with
    manifests, current symlink → 1.2.2."""
    inst = tmp_path / "install"
    (inst / "releases").mkdir(parents=True)
    uj.journal_init(inst)
    uj.ensure_extensions(inst)
    _write_manifest(inst, "1.2.2")
    _write_manifest(inst, "1.2.3")
    (inst / "current").symlink_to("releases/1.2.2")
    return inst


@pytest.fixture
def unresolved_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Force ``_self_env_marker()`` to return None (auto-derive signals all
    silenced) — the new ``env-marker-absent`` contract requires explicit
    setup because the auto-derive path would otherwise pick up ambient
    ``POSTGRES_DB`` / install-dir topology from the host (e.g. this dev
    host's ``~/agents-ensemble/.env`` carries POSTGRES_DB=ensemble_prod
    and would auto-resolve live).

    Silences every signal the resolver reads:
    * ``ENSEMBLE_SELF_ENV`` unset
    * ``POSTGRES_DB`` unset
    * ``Path.home()`` redirected to ``<tmp>/fake-home`` — no
      ``agents-ensemble`` / ``agents-ensemble-demo`` subdirs
    * ``sys.frozen = False`` — disables the frozen-binary topology path

    Returns ``tmp_path`` so callers can write install fixtures there.
    Tests that need an install-dir pair the auto-derive-routed resolver
    with an explicit ``monkeypatch.setattr(ut, "_resolve_install_dir",
    ...)`` — the resolver receives the same path but the env string is
    forced to None.
    """
    monkeypatch.delenv("ENSEMBLE_SELF_ENV", raising=False)
    monkeypatch.delenv("POSTGRES_DB", raising=False)
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir(exist_ok=True)
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.setattr(ut.sys, "frozen", False, raising=False)
    return tmp_path


@pytest.fixture
def no_spawn(monkeypatch: pytest.MonkeyPatch) -> list:
    """Recorder replacing the executor-spawn seam — any spawn attempt inside
    a tool call lands here and fails the zero-spawn asserts (D2/D3: the
    actor tools return BEFORE any execution; the daemonized executor fires
    post-turn). Patches the spawn_executor seam (NOT subprocess.Popen —
    subprocess is a shared module and these tests run their own status.sh
    subprocesses)."""
    calls: list = []

    def _recorder(*args: Any, **kwargs: Any) -> int:
        calls.append((args, kwargs))
        raise AssertionError(
            f"spawn_executor fired inside the tool call: {args!r} — actor "
            "tools must return BEFORE any execution (D2/D3 arm→return→poll)"
        )

    monkeypatch.setattr(ut, "spawn_executor", _recorder)
    monkeypatch.setattr(uj, "spawn_executor", _recorder)
    return calls


def test_static_no_direct_process_spawn_in_tools_module() -> None:
    """Static companion to the ``no_spawn`` recorder above (D2/D3): the
    tools module itself must contain NO direct process-spawn reference —
    not ``subprocess``, ``Popen``, ``system``, nor ``posix_spawn`` (it
    imports none today; this pins the absence so a future direct-spawn
    regression in a tool body fails the suite). All execution flows
    through the daemonized ``spawn_executor`` seam. AST walk (Names /
    Attributes / import modules + aliases) so docstrings and comments
    that DISCUSS the discipline cannot false-positive; mirrors the
    no-BashProcessRegistry pin in test_upgrade_journal.py."""
    import ast

    source = (REPO_ROOT / "daemon" / "tools" / "upgrade_tools.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    code_names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    attr_names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    imported = {
        a.name
        for n in ast.walk(tree)
        if isinstance(n, (ast.Import, ast.ImportFrom))
        for a in n.names
    }
    from_modules = {
        n.module
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom) and n.module
    }
    for forbidden in ("subprocess", "Popen", "system", "posix_spawn"):
        assert forbidden not in code_names | attr_names | imported | from_modules, (
            f"upgrade_tools.py executable code references {forbidden!r} — "
            "direct spawning is forbidden; ALL execution must flow through "
            "the daemonized spawn_executor seam (D2/D3 arm→return→post-turn)"
        )
    # The positive half of the contract: the seam itself stays imported.
    assert "spawn_executor" in imported


def test_opt_out_falses_pinned_to_proactive_false_bools() -> None:
    """S2 pin: ``_OPT_OUT_FALSES`` (in upgrade_tools.py) is hand-copied
    from ``daemon.config._PROACTIVE_FALSE_BOOLS``. A drift between the two
    would silently broaden or narrow the opt-out vocabulary — operators
    who rely on the documented ``0|false|no|off`` set would get a
    different result than ``ENSEMBLE_EMPTY_RESPONSE_GUARD`` does on the
    same switch. We chose the test-pinned approach over an import because
    ``daemon.config`` has heavy module-load cost and is not on the
    critical path of the tool layer; the test imports it just-in-time
    and asserts set equality.

    If the canonical ``_PROACTIVE_FALSE_BOOLS`` ever changes, this test
    fires — update ``_OPT_OUT_FALSES`` to match (the commit message
    should call out the vocabulary change so operators know).
    """
    from daemon.config import _PROACTIVE_FALSE_BOOLS

    assert set(ut._OPT_OUT_FALSES) == set(_PROACTIVE_FALSE_BOOLS), (
        f"_OPT_OUT_FALSES drift: upgrade_tools={sorted(ut._OPT_OUT_FALSES)} "
        f"vs daemon.config._PROACTIVE_FALSE_BOOLS={sorted(_PROACTIVE_FALSE_BOOLS)}"
    )


class TestReadEnvValue:
    """Direct coverage of ``_read_env_value`` (S4 completion — reviewer
    APPROVED-WITH-NOTES commissioned follow-up on commit ``beac30b9``).

    The function is the shell-style .env parser used by
    ``_auto_derive_env`` to cross-check POSTGRES_DB in the install-dir
    ``.env`` files. Reviewer case shapes (a)-(f) all map cleanly to
    real production branches in ``daemon/tools/upgrade_tools.py:303-333``
    (none of the suggested cases were imagined — every shape is a
    real code path):

    * (a) ``export POSTGRES_DB=ensemble_prod\\n`` → tests ``export ``
      prefix strip (line 320-321) + value trim (line 327) + return.
    * (b) ``POSTGRES_DB="ensemble_prod"\\n`` (double quotes) → tests
      quote-strip (line 328-329) + return.
    * (c) ``POSTGRES_DB='ensemble_prod'\\n`` (single quotes) → tests
      quote-strip single (line 328-329) + return.
    * (d) ``# comment\\nPOSTGRES_DB=ensemble_prod\\n`` → tests comment
      skip (line 318).
    * (e) ``INVALID_LINE_NO_EQUALS\\nPOSTGRES_DB=ensemble_prod\\n`` →
      tests malformed-line skip (line 322-323).
    * (f) ``POSTGRES_DB= ensemble_prod  \\n`` (unquoted, padded) → tests
      value trim (line 327).

    Plus the error/refusal branches the reviewer's list implicitly
    relies on: missing file, wrong key, OSError, blank-line skip,
    mismatched quotes (NOT stripped — quote-strip is a matched-pair
    check, single-side quotes pass through verbatim), and combined
    shapes (export + quote; export + padded).
    """

    def test_returns_none_for_missing_file(self, tmp_path: Path) -> None:
        """Missing file branch (line 314-315): ``not env_file.is_file()``
        short-circuits to ``None`` — never raises. The resolver relies
        on this for the "no install-dir .env yet" path."""
        missing = tmp_path / "no-such.env"
        assert ut._read_env_value(missing, "POSTGRES_DB") is None

    def test_returns_none_when_key_absent(self, tmp_path: Path) -> None:
        """Wrong-key skip branch (line 325-326): a file with OTHER
        keys but not the queried one returns ``None`` (not the last
        seen value). The resolver relies on this when cross-checking
        POSTGRES_DB."""
        env_file = tmp_path / "install.env"
        env_file.write_text(
            "PATH=/usr/bin\nOTHER_KEY=other_value\n", encoding="utf-8"
        )
        assert ut._read_env_value(env_file, "POSTGRES_DB") is None

    def test_returns_none_when_file_unreadable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OSError branch (line 331-332): the parser never raises on a
        read error — a corrupt .env must not crash the resolver.
        ``read_text`` raises ``PermissionError`` (a subclass of
        ``OSError``); we patch the file path resolution to force an
        OSError on the read without depending on filesystem
        permissions."""
        env_file = tmp_path / "install.env"
        env_file.write_text("POSTGRES_DB=ensemble_prod\n", encoding="utf-8")

        def _boom_read_text(*_args, **_kwargs):
            raise PermissionError("read denied (test)")

        monkeypatch.setattr(Path, "read_text", _boom_read_text)
        assert ut._read_env_value(env_file, "POSTGRES_DB") is None

    @pytest.mark.parametrize(
        "raw,expected",
        [
            # Reviewer case (a) — 'export ' prefix strip + trim
            ("export POSTGRES_DB=ensemble_prod\n", "ensemble_prod"),
            # Reviewer case (b) — double-quote strip
            ('POSTGRES_DB="ensemble_prod"\n', "ensemble_prod"),
            # Reviewer case (c) — single-quote strip
            ("POSTGRES_DB='ensemble_prod'\n", "ensemble_prod"),
            # Reviewer case (d) — comment skip (leading # line)
            ("# this is a comment\nPOSTGRES_DB=ensemble_prod\n", "ensemble_prod"),
            # Reviewer case (e) — malformed-line skip (no '=')
            (
                "INVALID_LINE_NO_EQUALS\nPOSTGRES_DB=ensemble_prod\n",
                "ensemble_prod",
            ),
            # Reviewer case (f) — value trim (unquoted, padded)
            ("POSTGRES_DB=  ensemble_prod  \n", "ensemble_prod"),
        ],
    )
    def test_reviewer_case_shapes(
        self, raw: str, expected: str, tmp_path: Path
    ) -> None:
        """S4-COMPLETION: pins every reviewer-suggested case shape to
        the real production branch it exercises. Each parametrize case
        is a self-contained .env file; the assertion checks the
        returned value matches the expected (post-parse) result."""
        env_file = tmp_path / "install.env"
        env_file.write_text(raw, encoding="utf-8")
        assert ut._read_env_value(env_file, "POSTGRES_DB") == expected

    def test_blank_lines_skipped(
        self, tmp_path: Path
    ) -> None:
        """Blank-line skip branch (line 318: ``if not line``). The
        parser must skip blank lines between entries — common in
        hand-edited .env files."""
        env_file = tmp_path / "install.env"
        env_file.write_text(
            "\n"
            "\n"
            "POSTGRES_DB=ensemble_prod\n"
            "\n",
            encoding="utf-8",
        )
        assert ut._read_env_value(env_file, "POSTGRES_DB") == "ensemble_prod"

    def test_mismatched_quotes_not_stripped(self, tmp_path: Path) -> None:
        """Quote-strip is a matched-pair check (line 328:
        ``v[0] == v[-1] and v[0] in ("'", '"')``). A single-side quote
        (``ensemble_prod"`` or ``"ensemble_prod``) does NOT match the
        pair-check, so the quote(s) pass through verbatim. This pins
        the parser to NOT over-eagerly strip."""
        env_file = tmp_path / "install.env"
        env_file.write_text(
            'POSTGRES_DB=ensemble_prod"\n', encoding="utf-8"
        )
        # Single trailing double-quote is NOT a matched pair → returned
        # verbatim (with the trailing quote preserved).
        assert ut._read_env_value(env_file, "POSTGRES_DB") == 'ensemble_prod"'

    def test_export_prefix_with_quoted_value(self, tmp_path: Path) -> None:
        """Combined: ``export `` prefix + quoted value. Both
        transforms apply — the prefix is stripped (line 320-321)
        and the surrounding quotes are stripped (line 328-329)."""
        env_file = tmp_path / "install.env"
        env_file.write_text(
            "export POSTGRES_DB=\"ensemble_prod\"\n", encoding="utf-8"
        )
        assert ut._read_env_value(env_file, "POSTGRES_DB") == "ensemble_prod"

    def test_export_prefix_with_leading_whitespace(self, tmp_path: Path) -> None:
        """``export `` prefix with leading whitespace in the line —
        the parser strips the prefix, then ``lstrip()`` removes any
        whitespace between the prefix and the key (line 321).
        """
        env_file = tmp_path / "install.env"
        env_file.write_text(
            "    export   POSTGRES_DB=ensemble_prod\n", encoding="utf-8"
        )
        assert ut._read_env_value(env_file, "POSTGRES_DB") == "ensemble_prod"

    def test_quote_must_be_matching_not_just_single_side(
        self, tmp_path: Path
    ) -> None:
        """Defensive: ``'ensemble_prod"`` (single+double mixed) does
        NOT match the matched-pair check (line 328: ``v[0] == v[-1]``
        — single quote != double quote). The value is returned
        verbatim (NOT stripped)."""
        env_file = tmp_path / "install.env"
        env_file.write_text(
            "POSTGRES_DB='ensemble_prod\"\n", encoding="utf-8"
        )
        # Mixed quotes → no strip.
        assert (
            ut._read_env_value(env_file, "POSTGRES_DB")
            == "'ensemble_prod\""
        )

    def test_inline_whitespace_around_equals_is_tolerated(
        self, tmp_path: Path
    ) -> None:
        """``POSTGRES_DB = ensemble_prod`` (spaces around ``=``) is
        parsed as: ``k = "POSTGRES_DB "``, ``v = " ensemble_prod"``;
        the key-side strip (``k.strip() != key`` on line 325)
        removes the trailing space before the match, and the value-
        side strip (line 327) removes the leading space from the
        value. Result: the value is returned trimmed. This pins the
        parser's actual lenient-on-whitespace behavior so a future
        tightening doesn't surprise callers.
        """
        env_file = tmp_path / "install.env"
        env_file.write_text(
            "POSTGRES_DB = ensemble_prod\n", encoding="utf-8"
        )
        # Tolerates spaces around `=`: value returned trimmed.
        assert ut._read_env_value(env_file, "POSTGRES_DB") == "ensemble_prod"


@pytest.fixture
def parity_port_free() -> None:
    """Pre-flight guard for the parity fixture: status.sh probes
    ``http://localhost:$PORT/livez`` and the parity test asserts the
    "not answering" verdict — if something ALREADY listens on SANDBOX_PORT
    that assertion is unreliable (a foreign listener answering /livez is
    not a parity failure). Skip loudly instead. Fail-loud semantics stay:
    this guard only fires when the port is occupied, which is itself the
    anomaly worth surfacing. Bind-check only — no waits, no connects."""
    import socket

    for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((host, SANDBOX_PORT))
        except OSError:
            pytest.skip(
                f"SANDBOX_PORT {SANDBOX_PORT} already bound on {host} — the "
                "/livez 'not answering' parity assertions would be unreliable"
            )
        except (AttributeError, ValueError):  # pragma: no cover — no IPv6 here
            continue


@pytest.fixture
def harness(install: Path, monkeypatch: pytest.MonkeyPatch, no_spawn: list):
    """A wired tool harness aimed at the /tmp fixture:

    * ENSEMBLE_SELF_ENV=demo (fake staged marker)
    * _resolve_install_dir → the fixture dir (never a real install)
    * a mock manager with: no-op task repo (busy-check=idle), a marker
      recorder for set_pending_system_execution, port 0 (probes degrade
      without network), and NO queue repository by default.
    Returns (tools dict, markers list, install).
    """
    monkeypatch.setenv("ENSEMBLE_SELF_ENV", "demo")
    monkeypatch.setattr(ut, "_resolve_install_dir", lambda self_env: install)

    manager = _base_manager()  # no-network port, idle task repo, no queue repo
    markers: list[dict] = []
    manager.set_pending_system_execution = MagicMock(
        side_effect=lambda iid, spec: markers.append(dict(spec))
    )

    tools = _build_tools(manager)
    assert set(tools) >= UPGRADE_TOOL_NAMES
    return tools, markers, install


def _journal_bytes(install: Path) -> bytes:
    return uj.journal_path(install).read_bytes()


def _build_tools(manager) -> dict:
    """create_upgrade_tools → {name: tool} dict (the 4 upgrade tools +
    whatever else the mock manager yields)."""
    return {t.name: t for t in create_upgrade_tools(manager, INSTANCE_ID, agent_id="ari")}


def _base_manager() -> MagicMock:
    """Mock manager: no-network port, idle task repo, no queue repo."""
    manager = MagicMock(name="InstanceManager")
    manager.config.daemon.port = 0  # falsy → _self_port None → no network
    task_repo = MagicMock(name="task_repo")
    task_repo.has_instance_busy = MagicMock(return_value=False)
    manager._task_repo = task_repo
    manager._queue_repository = None
    return manager


def _refusal_reason(result: str) -> str | None:
    m = re.search(r"REFUSED — reason=([a-z0-9-]+)", result)
    return m.group(1) if m else None


async def _arm_restart(harness) -> str:
    """Armed demo restart; returns the run_id (fails loudly if refused)."""
    tools, markers, install = harness
    result = await tools["system_restart"].ainvoke(
        {"target_env": "demo", "reason": "test arm", "dry_run": False}
    )
    assert "RESTART SCHEDULED" in result, result
    return markers[-1]["run_id"]


# ── Read-pair parity vs status.sh (T6) ───────────────────────────────────────


class TestReadPairParity:
    """``release_info`` field set vs ``scripts/upgrade/status.sh`` on the SAME
    /tmp fixture install tree — the journal, releases, symlink, lock, and
    launcher state are written by the REAL P2.1 tooling (lib.sh), so the
    parity is against ground truth, not a mirrored fixture."""

    @pytest.fixture
    def parity_fixture(self, tmp_path: Path) -> Path:
        """A rich fixture written ENTIRELY by lib.sh: journal with
        current/previous, an open promote txn, a quarantined release,
        four release dirs, a held lock, and a .launcher-state."""
        inst = tmp_path / "parity-install"
        (inst / "releases").mkdir(parents=True)
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": _isolated_home(tmp_path),  # M3: never the real home
            "INSTALL_DIR": str(inst),
            "POSTGRES_DB": "ensemble_sandbox",
        }
        script = (
            ". lib.sh\n"
            "journal_init\n"
            'journal_set_current "1.2.2"\n'
            'journal_set_previous "1.2.1"\n'
            'journal_open_txn promote "1.2.3"\n'
            'journal_quarantine "1.2.0"\n'
            "journal_history_append commit 'prior run committed'\n"
            'mkdir -p "$INSTALL_DIR/releases/1.2.0" "$INSTALL_DIR/releases/1.2.1"\n'
            'ln -sfn releases/1.2.2 "$INSTALL_DIR/current"\n'
            'mkdir -p "$INSTALL_DIR/releases/rollback.lock.d"\n'
            'printf \'4242\\n\' > "$INSTALL_DIR/releases/rollback.lock.d/owner"\n'
            'printf \'r-parity-lock\\n\' > "$INSTALL_DIR/releases/rollback.lock.d/run_id"\n'
            'printf \'%s\\n\' "$(date +%s)" > "$INSTALL_DIR/releases/rollback.lock.d/heartbeat"\n'
            "cat > \"$INSTALL_DIR/.launcher-state\" <<'LAUNCHER_STATE'\n"
            "last_exit=0\n"
            "crash_count=0\n"
            "window_start=1\n"
            "last_backoff=0\n"
            "notified_75=0\n"
            "last_uptime=600\n"
            "LAUNCHER_STATE\n"
        )
        rc = subprocess.run(
            ["bash", "-c", script],
            env=env,
            cwd=str(REPO_ROOT / "scripts" / "upgrade"),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert rc.returncode == 0, rc.stderr
        # Guard: the fixture must live ONLY under tmp_path (relative-path
        # pollution of the repo tree is a hard failure).
        assert (inst / "current").is_symlink()
        assert not (REPO_ROOT / "scripts" / "upgrade" / "current").exists()
        assert not (REPO_ROOT / "scripts" / "upgrade" / "releases").exists()
        for ver, safe in (("1.2.2", True), ("1.2.3", True), ("1.2.0", False)):
            _write_manifest(inst, ver, rollback_safe=safe)
        return inst

    def _run_status_sh(self, inst: Path) -> str:
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": _isolated_home(inst.parent),  # M3: never the real home
            "TARGET": "sandbox",
            "INSTALL_DIR": str(inst),
            "PORT": str(SANDBOX_PORT),
            "POSTGRES_DB": "ensemble_sandbox",
        }
        rc = subprocess.run(
            ["bash", str(REPO_ROOT / "scripts" / "upgrade" / "status.sh"), "sandbox"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert rc.returncode == 0, rc.stderr
        return rc.stdout

    async def _run_release_info(self, inst: Path, monkeypatch) -> str:
        monkeypatch.setenv("ENSEMBLE_SELF_ENV", "sandbox")
        monkeypatch.setattr(ut, "_resolve_install_dir", lambda self_env: inst)
        manager = MagicMock()
        manager.config.daemon.port = SANDBOX_PORT
        manager._task_repo = None
        tools = _build_tools(manager)
        return await tools["release_info"].ainvoke({})

    @staticmethod
    def _parse_status_sh(out: str) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        for raw in out.splitlines():
            line = raw.split("]: ", 1)[-1]  # strip the upgrade-status[sbx]: tag
            if m := re.match(
                r"^resolved env: target=(\S+) dir=(\S+) port=(\S+) db=(\S+)$", line
            ):
                fields["env_triple"] = m.groups()
            elif m := re.match(r"^current -> (\S+)$", line):
                fields["current"] = m.group(1)
            elif line.startswith("pipeline lock:"):
                fields["lock"] = (
                    "free"
                    if line.endswith("free")
                    else re.search(r"owner=(\S+) run_id=(\S+) heartbeat=(\d+)", line).groups()
                )
            elif m := re.match(r"^daemon :(\S+) /livez: (.+)$", line):
                fields["livez"] = (m.group(1), m.group(2))
            elif m := re.match(r"^\s{4}(\S+)\s+(.*)$", line):
                name, rest = m.groups()
                if "rollback_safe=" in rest or "protocol artifact" in rest:
                    rb_m = re.search(r"rollback_safe=(\S+)", rest)
                    fields.setdefault("releases", {})[name] = {
                        "rollback_safe": rb_m.group(1) if rb_m else "?",
                        "quarantined": "QUARANTINED" in rest,
                        "artifact": "protocol artifact" in rest,
                    }
            elif line.startswith('{"current"'):
                fields["journal_raw"] = json.loads(line)
        return fields

    @staticmethod
    def _parse_release_info(out: str) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        in_releases_block = False
        for line in out.splitlines():
            if line == "releases:":
                in_releases_block = True
                continue
            if line.startswith("changelog:") or not line.startswith(" "):
                in_releases_block = False
            if m := re.match(
                r"^resolved env: target=(\S+) dir=(\S+) port=(\S+) db=(\S+)$", line
            ):
                fields["env_triple"] = m.groups()
            elif m := re.match(r"^current=(\S+) \(via releases/current → (.+)\)$", line):
                fields["current"] = m.group(2)
            elif line.startswith("pipeline lock:"):
                if line.endswith("free"):
                    fields["lock"] = "free"
                else:
                    m = re.search(r"owner=(\S+) run_id=(\S+) heartbeat=(\d+)", line)
                    fields["lock"] = m.groups()
            elif m := re.match(r"^daemon :(\S+) /livez: (.+)$", line):
                fields["livez"] = (m.group(1), m.group(2))
            elif line.startswith("journal: current="):
                m = re.match(
                    r"^journal: current=(\S+) previous=(\S+) in-flight=(.+?) "
                    r"rollbacks_24h=(\d+)/(\d+) cooldown=(\S+) quarantine=(\[.*\])$",
                    line,
                )
                if m:
                    cur, prev, inflight, rb, cap, cooldown, quar = m.groups()
                    fields["journal_summary"] = {
                        "current": None if cur == "None" else cur,
                        "previous": None if prev == "None" else prev,
                        "in_flight": inflight,
                        "rollback_24h": int(rb),
                        "rollback_cap": int(cap),
                        "quarantine": json.loads(quar.replace("'", '"')),
                    }
            elif in_releases_block and (m := re.match(r"^\s{2}(\S+)\s+(.*)$", line)):
                name, rest = m.groups()
                if "rollback_safe=" in rest or "protocol artifact" in rest:
                    rb_m = re.search(r"rollback_safe=(\S+)", rest)
                    fields.setdefault("releases", {})[name] = {
                        "rollback_safe": rb_m.group(1) if rb_m else "?",
                        "quarantined": "QUARANTINED" in rest,
                        "artifact": "protocol artifact" in rest,
                    }
        return fields

    async def test_release_info_field_set_matches_status_sh(
        self, parity_port_free: None, parity_fixture: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        status_out = self._run_status_sh(parity_fixture)
        info_out = await self._run_release_info(parity_fixture, monkeypatch)
        s = self._parse_status_sh(status_out)
        i = self._parse_release_info(info_out)

        # 1. Resolved env triple — exact (target/dir/port/db).
        assert i["env_triple"] == s["env_triple"], (
            f"env triple diverged:\n status.sh={s['env_triple']}\n "
            f"release_info={i['env_triple']}"
        )

        # 2. current symlink target.
        assert i["current"] == s["current"]

        # 3. Journal identity: current/previous/in-flight/quarantine.
        assert i["journal_summary"]["current"] == s["journal_raw"]["current"]
        assert i["journal_summary"]["previous"] == s["journal_raw"]["previous"]
        assert i["journal_summary"]["quarantine"] == s["journal_raw"]["quarantined"]
        inf = s["journal_raw"]["in_flight"]
        # The in-flight fragment renders '<kind> target=<t> started_at=…'.
        assert i["journal_summary"]["in_flight"].startswith(f"{inf['kind']} ")
        assert f"target={inf['target']}" in i["journal_summary"]["in_flight"]
        # Rollback counters with the cap denominator.
        assert (
            i["journal_summary"]["rollback_24h"]
            == s["journal_raw"]["rollback_window_count"]["24h"]
        )
        assert i["journal_summary"]["rollback_cap"] == 3

        # 4. Releases inventory: same names, same rollback_safe value, same
        #    quarantine tag, same protocol-artifact labelling.
        assert set(i["releases"]) == set(s["releases"]), (
            f"release names diverged: {set(i['releases']) ^ set(s['releases'])}"
        )
        for name in s["releases"]:
            assert i["releases"][name] == s["releases"][name], (
                f"release {name} diverged: status.sh={s['releases'][name]} "
                f"release_info={i['releases'][name]}"
            )

        # 5. Pipeline lock: same state, same owner/run_id (heartbeat value
        #    compared positionally; release_info appends an age note).
        assert i["lock"][0] == s["lock"][0]  # owner
        assert i["lock"][1] == s["lock"][1]  # run_id
        assert i["lock"][2] == s["lock"][2]  # heartbeat epoch

        # 6. /livez probe line: same port, same not-answering verdict.
        assert i["livez"][0] == s["livez"][0]
        assert "not answering" in i["livez"][1]
        assert "not answering" in s["livez"][1]

        # 7. release_info carries the additional §7 observability surface
        #    (launcher state, upgrade.log tail) — additive, never missing.
        assert "launcher" in info_out
        assert "upgrade.log" in info_out


class TestUpgradeStatusRoundTrip:
    """run_id from an armed pending_op → in-flight read → terminal read keyed
    by the SAME run_id — the cross-death join (§2.4 / D-FA1.1)."""

    async def test_round_trip_same_run_id(self, harness) -> None:
        tools, markers, install = harness
        run_id = await _arm_restart(harness)

        # In-flight read, keyed by the armed run_id.
        in_flight = await tools["upgrade_status"].ainvoke({"run_id": run_id})
        assert "txn=IN-FLIGHT kind=restart" in in_flight
        assert f"run: {run_id}" in in_flight
        assert f"run_id={run_id} matches the active pipeline run" in in_flight
        assert f"pending-op: run_id={run_id} kind=restart" in in_flight

        # Simulate the executor completing (restart.sh's journal semantics).
        uj.journal_history_append(
            install, "restart", f"intentional restart run_id={run_id} completed"
        )
        uj.journal_update_field(install, "in_flight", None)
        uj.clear_pending_op(install)
        uj.lock_release(install)

        # Terminal read, SAME run_id (the post-restart poll).
        terminal = await tools["upgrade_status"].ainvoke({"run_id": run_id})
        assert "TERMINAL" in terminal
        assert "outcome=restarted (intentional)" in terminal
        assert run_id in terminal  # the run's history detail names the join key
        assert "not active — terminal events in tail below" in terminal

    async def test_unknown_run_id_error_with_fallback(self, harness) -> None:
        tools, _, _ = harness
        result = await tools["upgrade_status"].ainvoke({"run_id": "r-bogus-9999"})
        assert "Error: upgrade_status — unknown run_id=r-bogus-9999" in result
        assert "UPGRADE STATUS" in result  # latest self-env state follows

    async def test_terminal_upgrade_outcome_labels(self, harness) -> None:
        tools, _, install = harness
        uj.journal_history_append(install, "commit", "promote committed")
        result = await tools["upgrade_status"].ainvoke({})
        assert "TERMINAL" in result
        assert "outcome=committed" in result

    async def test_tail_clamped(self, harness) -> None:
        tools, _, install = harness
        uj.journal_update_field(
            install,
            "in_flight",
            {"kind": "promote", "target": "1.2.3", "started_at": uj.now_iso(),
             "flipped": False, "owner_pid": 999},
        )
        for n in range(150):
            uj.journal_history_append(install, "sweep", f"event {n}")
        result = await tools["upgrade_status"].ainvoke({"tail": 500})
        # tail clamps to 100
        assert "journal tail (last 100)" in result


# ── Read-pair refusals + fail-open ───────────────────────────────────────────


class TestReadPairRefusals:
    async def test_env_self_match_read_both_directions(self, harness) -> None:
        tools, _, _ = harness  # self-env=demo
        out = await tools["release_info"].ainvoke({"target_env": "live"})
        assert _refusal_reason(out) == "env-self-match"
        out2 = await tools["upgrade_status"].ainvoke({"target_env": "live"})
        assert _refusal_reason(out2) == "env-self-match"

    async def test_env_self_match_read_live_self_refuses_demo(
        self, install: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A (fake) live-resident daemon cannot address demo either — the
        self-match rule is symmetric (§3.2)."""
        monkeypatch.setenv("ENSEMBLE_SELF_ENV", "live")  # FAKE live marker
        monkeypatch.setattr(ut, "_resolve_install_dir", lambda self_env: install)
        manager = MagicMock()
        manager.config.daemon.port = 0
        manager._task_repo = None
        tools = _build_tools(manager)
        out = await tools["release_info"].ainvoke({"target_env": "demo"})
        assert _refusal_reason(out) == "env-self-match"

    async def test_invalid_target_env_read_schema_fail_closed(self, harness) -> None:
        """Read tools carry Literal-typed target_env — an invalid enum value
        is rejected at the SCHEMA layer (pydantic ValidationError), which is
        the fail-closed analogue of the actor tools' invalid-target-env
        refusal string (str-typed params, refusal in the body)."""
        from pydantic import ValidationError

        tools, _, _ = harness
        with pytest.raises(ValidationError):
            await tools["release_info"].ainvoke({"target_env": "prod"})
        with pytest.raises(ValidationError):
            await tools["upgrade_status"].ainvoke({"target_env": "prod"})

    async def test_marker_absent_reads_fail_open(
        self, unresolved_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Marker absent + no auto-derive signal: reads STILL answer (S-31
        fail-open) — omitted target and target=dev are both self-consistent
        for the unresolved (dev-context) representation.

        New contract (D-FA2.3 supersession 2026-09-22): the
        ``unresolved_env`` fixture forces the resolver to return None by
        silencing every signal the auto-derivation path reads. Without
        that silencing, an ambient ``POSTGRES_DB=ensemble_prod`` (or a
        canonical install-dir .env under ``$HOME``) would auto-derive
        ``live``/``demo``/``dev`` instead.
        """
        monkeypatch.setattr(ut, "_resolve_install_dir", lambda self_env: None)
        manager = MagicMock()
        manager.config.daemon.port = 0
        manager._task_repo = None
        tools = _build_tools(manager)
        out = await tools["release_info"].ainvoke({})
        assert "RELEASE INFO" in out and "auto-derivation found no signal" in out
        assert "REFUSED" not in out
        out_dev = await tools["release_info"].ainvoke({"target_env": "dev"})
        assert "REFUSED" not in out_dev
        # …but a cross-env read is still refused while unresolved.
        out_live = await tools["release_info"].ainvoke({"target_env": "live"})
        assert _refusal_reason(out_live) == "env-self-match"

    async def test_no_install_dir_reads_render_honestly(
        self, harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cycle-3 MAJOR-1 regression: ``install_dir=None`` (dev repo
        checkout / unresolved env) must render the honest "no staged
        install dir" lines — the read pair RENDERS, never TypeErrors into
        an ``Error:`` prefix. The b9bee5cd twin-helper reroute deleted the
        local ``_lock_run_id``'s ``Path | None`` guard; journal
        ``lock_run_id`` is None-hardened (Option A) to restore parity."""
        tools, _, _ = harness  # self-env=demo, resolver overridden below
        monkeypatch.setattr(ut, "_resolve_install_dir", lambda self_env: None)

        status = await tools["upgrade_status"].ainvoke({})
        assert (
            "journal: none (no staged install dir — dev repo checkout or "
            "unresolved env; no pipeline runs to poll)" in status
        )
        assert "pipeline lock: unknown (no install dir)" in status
        assert "Error:" not in status  # TypeError path renders as Error:, never here

        info = await tools["release_info"].ainvoke({})
        assert (
            "releases: none (no staged install dir — dev repo checkout or "
            "unresolved env)" in info
        )
        assert (
            "journal: none (no staged install dir — dev repo checkout or "
            "unresolved env)" in info
        )
        assert "Error:" not in info

    async def test_release_info_sections_and_errors(self, harness) -> None:
        from pydantic import ValidationError

        tools, _, install = harness
        all_out = await tools["release_info"].ainvoke({})
        assert "RELEASE INFO" in all_out
        releases_out = await tools["release_info"].ainvoke({"section": "releases"})
        assert "releases:" in releases_out
        journal_out = await tools["release_info"].ainvoke({"section": "journal"})
        assert "journal (raw" in journal_out
        # section is Literal-typed: an unknown section is rejected at the
        # schema layer (fail-closed before the body's defensive branch).
        with pytest.raises(ValidationError):
            await tools["release_info"].ainvoke({"section": "bogus"})
        # version is free-form str: unknown version reaches the body's
        # structured error.
        unknown_ver = await tools["release_info"].ainvoke({"version": "9.9.9"})
        assert "unknown version '9.9.9'" in unknown_ver
        known_ver = await tools["release_info"].ainvoke(
            {"section": "changelog", "version": "1.2.3"}
        )
        assert "1.2.3" in known_ver and "binary_version=v1.2.3" in known_ver

    async def test_reads_never_mutate(self, harness) -> None:
        tools, _, install = harness
        before = _journal_bytes(install)
        await tools["release_info"].ainvoke({})
        await tools["upgrade_status"].ainvoke({})
        await tools["release_info"].ainvoke({"section": "journal"})
        assert _journal_bytes(install) == before
        assert not (install / "releases" / "rollback.lock.d").exists()


# ── Actor env gates (both tools) ─────────────────────────────────────────────


class TestActorEnvGates:
    async def test_invalid_target_env_actor(self, harness) -> None:
        tools, _, _ = harness
        for name, extra in (
            ("system_restart", {"reason": "x"}),
            ("system_upgrade", {}),
        ):
            out = await tools[name].ainvoke(dict(extra, target_env="production"))
            assert _refusal_reason(out) == "invalid-target-env", (name, out)
        # Omitted target_env is schema-required for ACTOR tools — the
        # fail-closed analogue at the validation layer.
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            await tools["system_restart"].ainvoke({"reason": "x"})

    async def test_env_marker_absent_actor_fail_closed(
        self, unresolved_env: Path, install: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """New contract: actor tools refuse env-marker-absent only when
        ``_self_env_marker()`` returns None — i.e. when no explicit
        marker AND no auto-derive signal resolves. The
        ``unresolved_env`` fixture silences every signal the resolver
        reads so the contract is exercised cleanly (without the fixture
        the test host's ambient ``POSTGRES_DB`` / install-dir topology
        would auto-derive the env and the actor tool would not refuse).

        The D-FA2.3 supersession keeps the SAME refusal token
        (``env-marker-absent``) and the SAME fail-closed posture —
        today's contract is preserved verbatim when the operator sets
        ``ENSEMBLE_SELF_ENV=false`` (or has no launch evidence).
        """
        monkeypatch.setattr(ut, "_resolve_install_dir", lambda self_env: install)
        manager = MagicMock()
        manager.config.daemon.port = 0
        manager._task_repo = None
        tools = _build_tools(manager)
        for name, kwargs in (
            ("system_restart", {"reason": "x"}),
            ("system_upgrade", {}),
        ):
            out = await tools[name].ainvoke(dict(kwargs, target_env="demo"))
            assert _refusal_reason(out) == "env-marker-absent", (name, out)

    async def test_env_self_match_actor_both_directions(self, harness) -> None:
        tools, _, _ = harness  # self=demo
        out = await tools["system_restart"].ainvoke(
            {"target_env": "live", "reason": "x"}
        )
        assert _refusal_reason(out) == "env-self-match"
        out2 = await tools["system_upgrade"].ainvoke({"target_env": "sandbox"})
        assert _refusal_reason(out2) == "env-self-match"

    @pytest.mark.parametrize(
        "spoof",
        ["Live", "LIVE", "LIVE ", "livé", "production", "demo;live", "l i v e"],
    )
    async def test_spoofed_env_marker_unresolved(
        self,
        unresolved_env: Path,
        install: Path,
        monkeypatch: pytest.MonkeyPatch,
        spoof: str,
    ) -> None:
        """Case/unicode/punct-spoofed markers resolve to UNRESOLVED (the
        marker is a non-normalizing exact enum match) → actor tools refuse
        env-marker-absent, never treating the spoof as live.

        New contract (D-FA2.3 supersession 2026-09-22): the spoofed value
        is NOT in ``VALID_ENVS`` so ``_explicit_marker()`` returns None;
        the resolver then falls through to auto-derive. The
        ``unresolved_env`` fixture silences every signal the auto-derive
        path reads so the spoof → unresolved → fail-closed contract is
        exercised cleanly. Without the silencing, a host whose ambient
        ``POSTGRES_DB=ensemble_prod`` would resolve live through the
        auto-derive path — that is INTENTIONAL behavior (the env is
        independently live-shaped) and is covered by the new auto-
        derive tests.
        """
        monkeypatch.setenv("ENSEMBLE_SELF_ENV", spoof)
        monkeypatch.setattr(ut, "_resolve_install_dir", lambda self_env: install)
        manager = MagicMock()
        manager.config.daemon.port = 0
        manager._task_repo = None
        tools = _build_tools(manager)
        out = await tools["system_restart"].ainvoke(
            {"target_env": "live", "reason": "x"}
        )
        assert _refusal_reason(out) == "env-marker-absent", (spoof, out)
        out2 = await tools["system_upgrade"].ainvoke({"target_env": "live"})
        assert _refusal_reason(out2) == "env-marker-absent", (spoof, out2)


# ── Auto-resolution (D-FA2.3 supersession, 2026-09-22 user directive) ────────


class TestAutoResolution:
    """The ENSEMBLE_SELF_ENV marker is OPTIONAL with explicit opt-out. The
    resolver auto-derives the env from launch evidence when the marker is
    absent and the operator did not opt out via a false-value marker.
    Auto-derived envs apply the same gates as explicit-marker envs
    (env-self-match, live 3-factor, live restart refusal).

    These tests exercise the contract on a clean tmp_path home (no real
    ``~/agents-ensemble*`` paths involved — the only thing being checked is
    the resolver's classification of the configured launch evidence).
    """

    def test_explicit_marker_wins_over_auto_derive(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The highest-priority signal is the explicit marker. Even when
        POSTGRES_DB would auto-derive a different env (here live), an
        explicit ``ENSEMBLE_SELF_ENV=demo`` marker WINS — the operator's
        staging intent overrides the daemon's ambient evidence.
        """
        fake_home = tmp_path / "fake-home"
        fake_home.mkdir()
        live_env = fake_home / "agents-ensemble" / ".env"
        live_env.parent.mkdir()
        live_env.write_text("POSTGRES_DB=ensemble_prod\nPORT=9797\n", encoding="utf-8")
        monkeypatch.delenv("ENSEMBLE_SELF_ENV", raising=False)
        monkeypatch.setenv("POSTGRES_DB", "ensemble_prod")
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        monkeypatch.setattr(ut.sys, "frozen", False, raising=False)
        # Sanity: without the marker, auto-derive WOULD resolve live.
        assert ut._auto_derive_env() == "live"
        # With an explicit demo marker, demo wins.
        monkeypatch.setenv("ENSEMBLE_SELF_ENV", "demo")
        assert ut._self_env_marker() == "demo"
        assert ut._self_env_source() == "explicit"

    @pytest.mark.parametrize("opt_out", ["0", "false", "no", "off", "FALSE", "  off  "])
    def test_opt_out_falses_preserve_fail_closed(
        self, opt_out: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """``ENSEMBLE_SELF_ENV=0|false|no|off`` (case-insensitive,
        whitespace-trimmed — ``_PROACTIVE_FALSE_BOOLS`` vocabulary)
        preserves today's fail-closed ``unresolved`` behavior verbatim.
        The resolver returns None and ``_self_env_source() == "opted-out"``
        so the marker-line display tells the operator the env was
        deliberately disabled.
        """
        fake_home = tmp_path / "fake-home"
        fake_home.mkdir()
        live_env = fake_home / "agents-ensemble" / ".env"
        live_env.parent.mkdir()
        live_env.write_text("POSTGRES_DB=ensemble_prod\n", encoding="utf-8")
        monkeypatch.setenv("ENSEMBLE_SELF_ENV", opt_out)
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        monkeypatch.setattr(ut.sys, "frozen", False, raising=False)
        # Sanity: without the opt-out, auto-derive WOULD resolve live.
        monkeypatch.delenv("ENSEMBLE_SELF_ENV", raising=False)
        assert ut._auto_derive_env() == "live"
        # With the opt-out, fail-closed.
        monkeypatch.setenv("ENSEMBLE_SELF_ENV", opt_out)
        assert ut._self_env_marker() is None
        assert ut._self_env_source() == "opted-out"

    def test_auto_derive_live_via_install_dir_topology(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Auto-derive live when ``~/agents-ensemble/.env`` exists AND
        POSTGRES_DB (either in the .env or in the daemon's env) is
        ``ensemble_prod``. The cross-check is the reason a leftover .env
        from a prior install cannot falsely label a dev checkout as live.
        """
        fake_home = tmp_path / "fake-home"
        fake_home.mkdir()
        live_env = fake_home / "agents-ensemble" / ".env"
        live_env.parent.mkdir()
        live_env.write_text(
            "POSTGRES_DB=ensemble_prod\nPORT=9797\n",
            encoding="utf-8",
        )
        monkeypatch.delenv("ENSEMBLE_SELF_ENV", raising=False)
        monkeypatch.delenv("POSTGRES_DB", raising=False)
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        monkeypatch.setattr(ut.sys, "frozen", False, raising=False)
        assert ut._self_env_marker() == "live"
        assert ut._self_env_source() == "auto"

    def test_auto_derive_live_via_ambient_postgres_db(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Auto-derive live when the daemon's own ``$POSTGRES_DB`` is
        ``ensemble_prod`` AND ``~/agents-ensemble/.env`` exists with a
        matching POSTGRES_DB. The install-dir topology + DB cross-check
        is what enables the derivation.
        """
        fake_home = tmp_path / "fake-home"
        fake_home.mkdir()
        live_env = fake_home / "agents-ensemble" / ".env"
        live_env.parent.mkdir()
        live_env.write_text("POSTGRES_DB=ensemble_prod\n", encoding="utf-8")
        monkeypatch.delenv("ENSEMBLE_SELF_ENV", raising=False)
        monkeypatch.setenv("POSTGRES_DB", "ensemble_prod")
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        monkeypatch.setattr(ut.sys, "frozen", False, raising=False)
        assert ut._self_env_marker() == "live"

    def test_auto_derive_demo_via_install_dir_topology(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Auto-derive demo when ``~/agents-ensemble-demo/.env`` exists
        AND POSTGRES_DB (either source) is ``ensemble_demo``.
        """
        fake_home = tmp_path / "fake-home"
        fake_home.mkdir()
        demo_env = fake_home / "agents-ensemble-demo" / ".env"
        demo_env.parent.mkdir()
        demo_env.write_text(
            "POSTGRES_DB=ensemble_demo\nPORT=7979\n",
            encoding="utf-8",
        )
        monkeypatch.delenv("ENSEMBLE_SELF_ENV", raising=False)
        monkeypatch.delenv("POSTGRES_DB", raising=False)
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        monkeypatch.setattr(ut.sys, "frozen", False, raising=False)
        assert ut._self_env_marker() == "demo"

    def test_auto_derive_dev_via_postgres_db(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Auto-derive dev when no install-dir topology resolves AND
        ``$POSTGRES_DB=ensemble_dev``. Repo checkouts that point at the
        docker-compose local PG resolve dev this way (matches the v0.13.9
        demo launch flow that staged POSTGRES_DB for the dev checkout).
        """
        fake_home = tmp_path / "fake-home"
        fake_home.mkdir()
        monkeypatch.delenv("ENSEMBLE_SELF_ENV", raising=False)
        monkeypatch.setenv("POSTGRES_DB", "ensemble_dev")
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        monkeypatch.setattr(ut.sys, "frozen", False, raising=False)
        assert ut._self_env_marker() == "dev"
        assert ut._self_env_source() == "auto"

    def test_auto_derive_sandbox_via_frozen_binary(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Auto-derive sandbox when running as a frozen PyInstaller
        binary AND ``<install>/releases/`` exists AND the install dir is
        NOT the canonical live/demo home. The frozen-binary + releases/
        pair is the most reliable signal because the staged payload
        carries no ``INSTALL_DIR`` env.

        Stage.sh stages the binary at
        ``<install>/releases/<ver>/ensemble-prod`` (stage.sh:5-17), so
        the test creates exactly that layout: install/releases/v0/ensemble-prod.
        """
        fake_home = tmp_path / "fake-home"
        fake_home.mkdir()
        sandbox_root = tmp_path / "sandbox-install"
        sandbox_release = sandbox_root / "releases" / "v0.13.0"
        sandbox_release.mkdir(parents=True)
        binary = sandbox_release / "ensemble-prod"
        binary.touch()
        monkeypatch.delenv("ENSEMBLE_SELF_ENV", raising=False)
        monkeypatch.delenv("POSTGRES_DB", raising=False)
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        monkeypatch.setattr(ut.sys, "frozen", True, raising=False)
        monkeypatch.setattr(ut.sys, "executable", str(binary), raising=False)
        assert ut._self_env_marker() == "sandbox"
        # And _resolve_install_dir must also return the install dir.
        assert ut._resolve_install_dir("sandbox") == sandbox_root

    def test_auto_derive_sandbox_falls_back_when_install_matches_live_or_demo(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Frozen binary at ``<live-install>/releases/<ver>/ensemble-prod``
        does NOT auto-derive sandbox — the install-dir match takes
        precedence and the resolver continues the topology chain. The
        frozen-binary sandbox requires the install dir to be UNCANONICAL.
        """
        fake_home = tmp_path / "fake-home"
        fake_home.mkdir()
        # Frozen binary at the canonical live install dir.
        live_root = fake_home / "agents-ensemble"
        live_release = live_root / "releases" / "v0.13.0"
        live_release.mkdir(parents=True)
        binary = live_release / "ensemble-prod"
        binary.touch()
        # Canonical live install .env matches.
        live_env = fake_home / "agents-ensemble" / ".env"
        live_env.write_text("POSTGRES_DB=ensemble_prod\n", encoding="utf-8")
        monkeypatch.delenv("ENSEMBLE_SELF_ENV", raising=False)
        monkeypatch.delenv("POSTGRES_DB", raising=False)
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        monkeypatch.setattr(ut.sys, "frozen", True, raising=False)
        monkeypatch.setattr(ut.sys, "executable", str(binary), raising=False)
        # Live topology wins (install-dir .env POSTGRES_DB=ensemble_prod).
        assert ut._self_env_marker() == "live"

    def test_auto_derive_unresolved_when_no_signal(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """No signal → ``unresolved`` (today's env-marker-absent contract
        preserved). The marker-line display shows
        ``auto-derivation found no signal`` so the operator can diagnose.
        """
        fake_home = tmp_path / "fake-home"
        fake_home.mkdir()
        monkeypatch.delenv("ENSEMBLE_SELF_ENV", raising=False)
        monkeypatch.delenv("POSTGRES_DB", raising=False)
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        monkeypatch.setattr(ut.sys, "frozen", False, raising=False)
        assert ut._self_env_marker() is None
        assert ut._self_env_source() == "auto-unresolved"

    def test_auto_derive_ignores_unrelated_postgres_db(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """An unrelated ``$POSTGRES_DB`` value (e.g. ``somedb``) does NOT
        resolve any env — only the canonical DB names do. The single-
        signal POSTGRES_DB fallback was rejected by D-FA2.3.
        """
        fake_home = tmp_path / "fake-home"
        fake_home.mkdir()
        monkeypatch.delenv("ENSEMBLE_SELF_ENV", raising=False)
        monkeypatch.setenv("POSTGRES_DB", "somedb")
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        monkeypatch.setattr(ut.sys, "frozen", False, raising=False)
        assert ut._self_env_marker() is None

    async def test_release_info_shows_auto_resolved_env_no_marker(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """``release_info`` with no marker set but unambiguous
        auto-derive evidence reports the resolved env (the operator can
        see exactly which path produced the resolution). The marker line
        shows ``auto-derived self-env=live`` so a misconfiguration is
        visible immediately — not silently attributed to the staged
        marker.
        """
        fake_home = tmp_path / "fake-home"
        fake_home.mkdir()
        live_install = fake_home / "agents-ensemble"
        live_install.mkdir()
        (live_install / "releases").mkdir()
        (live_install / ".env").write_text(
            "POSTGRES_DB=ensemble_prod\nPORT=9797\n", encoding="utf-8"
        )
        monkeypatch.delenv("ENSEMBLE_SELF_ENV", raising=False)
        monkeypatch.delenv("POSTGRES_DB", raising=False)
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        monkeypatch.setattr(ut.sys, "frozen", False, raising=False)
        monkeypatch.setattr(ut, "_resolve_install_dir", lambda self_env: live_install)
        manager = MagicMock()
        manager.config.daemon.port = 9797
        manager._task_repo = None
        tools = _build_tools(manager)
        out = await tools["release_info"].ainvoke({})
        assert "RELEASE INFO" in out
        # The auto-derived env line is the operator-visible signal.
        assert "auto-derived self-env=live" in out
        assert "REFUSED" not in out

    async def test_auto_resolved_live_still_requires_three_factor_gate(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """An auto-resolved live daemon still goes through the 3-factor
        live gate. This is the HARD SAFETY INVARIANT: auto-resolution
        changes identity DETECTION, NEVER the gates. A live-resident
        daemon whose env was auto-derived (no staged marker) still must
        pass ``user_confirmed`` + user-origin window + nonce content
        match BEFORE any live action.

        Two scenarios pinned with concrete tokens (S3 strengthening — the
        old assertion ``"REFUSED" in out or "CONFIRMATION" in out`` was
        too weak; both tokens can fire for the wrong reasons and a
        regression that flips one for the other would not have been
        caught):

        * **dry_run on auto-resolved live**: gate is NOT evaluated
          (dry_run is a preflight that mints the nonce for live) — the
          concrete token is ``CONFIRMATION REQUIRED (live)`` and the
          mint persists exactly one ``pending_actions`` nonce record.
          The env DID resolve as live (NOT as unresolved), so the gate
          path is reachable on the armed follow-up.
        * **armed on auto-resolved live WITHOUT a window**: factor 2
          fails — the concrete token is ``user-confirmation-missing``
          (and the marker-line shows ``auto-derived self-env=live``).
          This is the user-direction invariant: auto-derive changes
          identity DETECTION, NEVER the gate.
        """
        from daemon.tools import upgrade_journal as uj

        fake_home = tmp_path / "fake-home"
        fake_home.mkdir()
        live_install = fake_home / "agents-ensemble"
        live_install.mkdir()
        (live_install / "releases").mkdir()
        # Real journal so the dry_run nonce mint can persist.
        uj.journal_init(live_install)
        uj.ensure_extensions(live_install)
        (live_install / "releases" / "1.2.3").mkdir(parents=True, exist_ok=True)
        (live_install / "releases" / "1.2.3" / "manifest.json").write_text(
            json.dumps(
                {
                    "version": "1.2.3",
                    "binary_version": "v1.2.3",
                    "staged_at": "2026-08-22T09:00:00Z",
                    "rollback_safe": True,
                    "known_schema_gen": 14,
                }
            ),
            encoding="utf-8",
        )
        (live_install / ".env").write_text(
            "POSTGRES_DB=ensemble_prod\nPORT=9797\n", encoding="utf-8"
        )
        monkeypatch.delenv("ENSEMBLE_SELF_ENV", raising=False)
        monkeypatch.delenv("POSTGRES_DB", raising=False)
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        monkeypatch.setattr(ut.sys, "frozen", False, raising=False)
        monkeypatch.setattr(ut, "_resolve_install_dir", lambda self_env: live_install)
        manager = MagicMock(name="InstanceManager")
        manager.config.daemon.port = 9797
        manager._task_repo = None
        manager._user_origin_windows = {}  # NO window → factor 2 fails on armed
        manager.set_pending_system_execution = MagicMock()
        tools = _build_tools(manager)

        # Scenario A: dry_run on auto-resolved live. Env resolved (NOT
        # unresolved) — concrete token is the live-nonce CONFIRMATION
        # REQUIRED line. (The ``release_info`` marker-line that shows
        # ``auto-derived self-env=live`` is exercised separately by
        # ``test_release_info_shows_auto_resolved_env_no_marker``.)
        out_dry = await tools["system_upgrade"].ainvoke(
            {"target_env": "live", "version": "1.2.3"}
        )
        assert "CONFIRMATION REQUIRED (live): nonce CONFIRM-" in out_dry, out_dry
        assert "env-marker-absent" not in out_dry
        # The dry_run mint persisted exactly one pending_actions nonce
        # record (the labeled mutation).
        journal = uj.journal_read(live_install)
        assert len(journal.get("pending_actions", {})) == 1

        # Scenario B: armed on auto-resolved live WITHOUT a window.
        # 3-factor gate fires — concrete token is user-confirmation-missing
        # (factor 2 binding). The nonce record from scenario A is
        # irrelevant to the gate (no window stamped on this turn).
        # We fabricate a nonce string to prove the gate cannot be
        # bypassed by supplying ANY nonce.
        out_armed = await tools["system_upgrade"].ainvoke(
            {
                "target_env": "live",
                "version": "1.2.3",
                "dry_run": False,
                "user_confirmed": True,
                "nonce": "CONFIRM-FABRICATED-BYPASS-PROBE",
            }
        )
        assert _refusal_reason(out_armed) == "user-confirmation-missing", out_armed
        assert "whitelisted user-origin" in out_armed
        # Nothing armed; no markers; no spawn.
        assert manager.set_pending_system_execution.call_count == 0

    async def test_auto_resolved_live_refuses_restart_outright(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """An auto-resolved live daemon refuses ``system_restart``
        outright (A2/§3.1), matching the explicit-marker live contract.
        Auto-detection does NOT weaken the restart-refused invariant.
        """
        fake_home = tmp_path / "fake-home"
        fake_home.mkdir()
        live_install = fake_home / "agents-ensemble"
        live_install.mkdir()
        (live_install / "releases").mkdir()
        (live_install / ".env").write_text(
            "POSTGRES_DB=ensemble_prod\nPORT=9797\n", encoding="utf-8"
        )
        monkeypatch.delenv("ENSEMBLE_SELF_ENV", raising=False)
        monkeypatch.delenv("POSTGRES_DB", raising=False)
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        monkeypatch.setattr(ut.sys, "frozen", False, raising=False)
        monkeypatch.setattr(ut, "_resolve_install_dir", lambda self_env: live_install)
        manager = MagicMock()
        manager.config.daemon.port = 9797
        manager._task_repo = None
        tools = _build_tools(manager)
        # Every dry_run mode + every confirmation shape — still refused.
        for dry in (True, False):
            out = await tools["system_restart"].ainvoke(
                {
                    "target_env": "live",
                    "reason": "x",
                    "dry_run": dry,
                    "user_confirmed": True,
                    "nonce": "CONFIRM-ABCDEFGH",
                }
            )
            assert _refusal_reason(out) == "live-restart-refused", (dry, out)

    async def test_auto_derive_env_self_match_refuses_cross_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """An auto-resolved demo daemon refuses a cross-env target
        (live) with ``env-self-match``. Self-match applies to auto-
        derived envs the same way it applies to explicit-marker envs —
        the operator UX is identical from the tool surface's POV.
        """
        fake_home = tmp_path / "fake-home"
        fake_home.mkdir()
        demo_install = fake_home / "agents-ensemble-demo"
        demo_install.mkdir()
        (demo_install / "releases").mkdir()
        (demo_install / ".env").write_text(
            "POSTGRES_DB=ensemble_demo\nPORT=7979\n", encoding="utf-8"
        )
        monkeypatch.delenv("ENSEMBLE_SELF_ENV", raising=False)
        monkeypatch.delenv("POSTGRES_DB", raising=False)
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        monkeypatch.setattr(ut.sys, "frozen", False, raising=False)
        monkeypatch.setattr(ut, "_resolve_install_dir", lambda self_env: demo_install)
        manager = MagicMock()
        manager.config.daemon.port = 7979
        manager._task_repo = None
        tools = _build_tools(manager)
        out = await tools["release_info"].ainvoke({"target_env": "live"})
        assert _refusal_reason(out) == "env-self-match", out

    # ── W1: garbage marker handling ──────────────────────────────────────────
    # Garbage markers (typo / wrong case / punct-spoof / unicode look-alike)
    # are silently discarded by the resolver — the operator sees the
    # auto-derive result, but the marker-line and refusal-text surface
    # WHY their marker was not honored (reviewer APPROVED-WITH-NOTES
    # commissioned item W1). Two outcomes are tested: garbage + auto-
    # derive succeeds (self-env resolved, marker-line shows IGNORED);
    # garbage + auto-derive fails (self-env unresolved, marker-line
    # shows IGNORED + auto-derivation found no signal). Each emits a
    # WARNING log.

    def test_garbage_marker_falls_through_to_auto_derive(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """W1: ``ENSEMBLE_SELF_ENV=prod`` (typo / unrecognized value)
        falls through to auto-derive. The resolver does NOT refuse; the
        marker is silently IGNORED — but a WARNING is logged and the
        marker-line surfaces the ignored state so the operator
        understands WHY their marker was not honored.
        """
        fake_home = tmp_path / "fake-home"
        fake_home.mkdir()
        live_env = fake_home / "agents-ensemble" / ".env"
        live_env.parent.mkdir()
        live_env.write_text(
            "POSTGRES_DB=ensemble_prod\nPORT=9797\n", encoding="utf-8"
        )
        monkeypatch.setenv("ENSEMBLE_SELF_ENV", "prod")  # garbage
        monkeypatch.delenv("POSTGRES_DB", raising=False)
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        monkeypatch.setattr(ut.sys, "frozen", False, raising=False)
        # Resolver falls through to auto-derive; the IGNORED marker
        # does not prevent resolution.
        assert ut._self_env_marker() == "live"
        assert ut._self_env_source() == "ignored-garbage"

    @pytest.mark.parametrize("garbage", ["prod", "flase", "production", "demo;live"])
    def test_garbage_marker_wrong_case_or_typo(
        self, garbage: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """W1 parametrize: every garbage shape (typo / wrong case /
        punct-spoof) is detected and logged. Explicit enum match is
        case-sensitive (the marker is a non-normalizing exact enum
        match — wrong-case values like 'Live' / 'LIVE' are NOT
        treated as 'live'; they are garbage). Punct-spoofs and typos
        are garbage. The opt-out check is the only case-insensitive
        comparison.
        """
        fake_home = tmp_path / "fake-home"
        fake_home.mkdir()
        live_env = fake_home / "agents-ensemble" / ".env"
        live_env.parent.mkdir()
        live_env.write_text(
            "POSTGRES_DB=ensemble_prod\nPORT=9797\n", encoding="utf-8"
        )
        monkeypatch.setenv("ENSEMBLE_SELF_ENV", garbage)
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        monkeypatch.setattr(ut.sys, "frozen", False, raising=False)
        assert ut._is_garbage_marker() is True
        # Resolver still resolves (auto-derive succeeds): the IGNORED
        # marker does not block resolution.
        assert ut._self_env_marker() == "live"
        assert ut._self_env_source() == "ignored-garbage"

    def test_garbage_marker_with_no_signal_logs_and_unresolved(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog
    ) -> None:
        """W1: garbage marker + no auto-derive signal → unresolved,
        AND a WARNING log captures the ignored raw value so the
        operator can see the typo in the daemon log. This is the
        operator-facing signal that closes the silent fall-through
        hole.
        """
        # Use the unresolved_env silencing pattern inline (the fixture
        # already deletes ENSEMBLE_SELF_ENV; here we set it to
        # garbage AFTER the silencing).
        monkeypatch.setenv("ENSEMBLE_SELF_ENV", "flase")  # typo of 'false'
        monkeypatch.delenv("POSTGRES_DB", raising=False)
        fake_home = tmp_path / "fake-home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        monkeypatch.setattr(ut.sys, "frozen", False, raising=False)
        # Resolver returns None (auto-derive found no signal), but the
        # source is "ignored-garbage" — NOT "auto-unresolved".
        with caplog.at_level("WARNING", logger="daemon.tools.upgrade_tools"):
            assert ut._self_env_marker() is None
            assert ut._self_env_source() == "ignored-garbage"
        # WARNING was logged with the raw value. The log message uses
        # ``%r`` so the raw value is repr'd (single-quoted) — the
        # assertion checks for the rendered message fragment.
        assert any(
            "ENSEMBLE_SELF_ENV='flase'" in rec.getMessage()
            for rec in caplog.records
        ), [rec.getMessage() for rec in caplog.records]

    def test_garbage_marker_line_reports_ignored_value_when_resolved(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """W1: when the marker is garbage AND auto-derive resolves,
        the marker-line prints the IGNORED raw value AND the
        auto-derived env (so the operator sees both: their typo was
        ignored AND the resolver fell back to multi-signal
        auto-derivation).
        """
        fake_home = tmp_path / "fake-home"
        fake_home.mkdir()
        live_env = fake_home / "agents-ensemble" / ".env"
        live_env.parent.mkdir()
        live_env.write_text(
            "POSTGRES_DB=ensemble_prod\nPORT=9797\n", encoding="utf-8"
        )
        monkeypatch.setenv("ENSEMBLE_SELF_ENV", "prod")
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        monkeypatch.setattr(ut.sys, "frozen", False, raising=False)
        line = ut._marker_line(ut._self_env_marker())
        # Does NOT claim ABSENT — the marker IS set (just ignored).
        assert "ABSENT" not in line, line
        # Shows the raw value with an IGNORED annotation.
        assert "ENSEMBLE_SELF_ENV=<prod>" in line, line
        assert "IGNORED" in line, line
        # Shows the auto-derived env with the multi-signal rationale.
        assert "auto-derived self-env=live" in line, line

    def test_garbage_marker_line_reports_ignored_when_unresolved(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """W1: when the marker is garbage AND auto-derive finds no
        signal, the marker-line prints the IGNORED raw value AND
        the auto-derivation-failed message — the operator sees the
        typo, not a misleading ABSENT.
        """
        monkeypatch.setenv("ENSEMBLE_SELF_ENV", "production")
        monkeypatch.delenv("POSTGRES_DB", raising=False)
        fake_home = tmp_path / "fake-home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        monkeypatch.setattr(ut.sys, "frozen", False, raising=False)
        line = ut._marker_line(ut._self_env_marker())
        # Marker IS set, so the line must NOT say ABSENT — it must say
        # IGNORED with the raw value.
        assert "ABSENT" not in line, line
        assert "ENSEMBLE_SELF_ENV=<production>" in line, line
        assert "IGNORED" in line, line
        # And the auto-derivation-failed clause is present so the
        # operator knows the resolver also tried to auto-derive.
        assert "auto-derivation found no signal" in line, line

    # ── S1: refusal-text accuracy per source state ────────────────────────────
    # The ``env-marker-absent`` refusal token is the SAME in every case
    # (the fail-closed contract is preserved verbatim — the tool cannot
    # resolve the env), but the remediation hint differs because the
    # operator's next step differs. Three states are tested: explicit
    # opt-out, ignored-garbage, and auto-unresolved. Without this
    # branching the refusal would say "no opt-out via false" even when
    # the operator DID opt out, misdirecting them.

    async def test_refusal_text_accurate_when_operator_opted_out(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, install: Path
    ) -> None:
        """S1: opt-out (ENSEMBLE_SELF_ENV=false) → refusal text
        tells the operator they explicitly opted out, and points them
        at removing the opt-out to re-enable. NEVER claims "no opt-out
        via false" — that wording is wrong here.
        """
        monkeypatch.setenv("ENSEMBLE_SELF_ENV", "false")
        monkeypatch.setattr(ut, "_resolve_install_dir", lambda self_env: install)
        manager = MagicMock()
        manager.config.daemon.port = 0
        manager._task_repo = None
        tools = _build_tools(manager)
        out = await tools["system_restart"].ainvoke(
            {"target_env": "demo", "reason": "x"}
        )
        assert _refusal_reason(out) == "env-marker-absent", out
        # Operator DID opt out — the hint must reflect that.
        assert "explicitly opted out" in out, out
        assert "no opt-out via false" not in out, out
        # And it points at the remediation (remove opt-out OR set
        # explicit marker OR unset to auto-derive).
        assert "remove the opt-out" in out, out

    async def test_refusal_text_accurate_when_garbage_marker(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, install: Path
    ) -> None:
        """S1: garbage marker (ENSEMBLE_SELF_ENV=prod) → refusal text
        tells the operator the value was IGNORED, surfaces the valid
        forms, and distinguishes from the opt-out / auto-unresolved
        cases. Uses the ``unresolved_env``-equivalent silencing
        (no ENSEMBLE_SELF_ENV, no POSTGRES_DB, no install-dir
        evidence, sys.frozen=False) so the auto-derive path returns
        None and the resolver falls through to the refused path.
        """
        monkeypatch.setenv("ENSEMBLE_SELF_ENV", "prod")  # garbage
        monkeypatch.delenv("POSTGRES_DB", raising=False)
        fake_home = tmp_path / "fake-home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        monkeypatch.setattr(ut.sys, "frozen", False, raising=False)
        monkeypatch.setattr(ut, "_resolve_install_dir", lambda self_env: install)
        manager = MagicMock()
        manager.config.daemon.port = 0
        manager._task_repo = None
        tools = _build_tools(manager)
        out = await tools["system_restart"].ainvoke(
            {"target_env": "demo", "reason": "x"}
        )
        assert _refusal_reason(out) == "env-marker-absent", out
        # Operator set a typo — the hint reflects that.
        assert "IGNORED" in out, out
        assert "warning" in out.lower() or "WARNING" in out, out
        # And it lists the valid forms.
        assert "dev|demo|live|sandbox" in out, out
        # It does NOT conflate with the opt-out case.
        assert "explicitly opted out" not in out, out
        # It does NOT conflate with the auto-unresolved case either
        # (which would say "no ENSEMBLE_SELF_ENV marker, no opt-out").
        assert "no ENSEMBLE_SELF_ENV marker, no opt-out" not in out, out

    async def test_refusal_text_accurate_when_no_signal(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, install: Path
    ) -> None:
        """S1: no marker, no opt-out, no auto-derive signal → refusal
        text tells the operator no marker is set and points at setting
        one OR providing unambiguous launch evidence.
        """
        # unresolved_env-equivalent setup inline (fixture deletes
        # ENSEMBLE_SELF_ENV; here we want the same effect without
        # the fixture's home redirect interfering with install path).
        monkeypatch.delenv("ENSEMBLE_SELF_ENV", raising=False)
        monkeypatch.delenv("POSTGRES_DB", raising=False)
        fake_home = tmp_path / "fake-home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        monkeypatch.setattr(ut.sys, "frozen", False, raising=False)
        monkeypatch.setattr(ut, "_resolve_install_dir", lambda self_env: install)
        manager = MagicMock()
        manager.config.daemon.port = 0
        manager._task_repo = None
        tools = _build_tools(manager)
        out = await tools["system_restart"].ainvoke(
            {"target_env": "demo", "reason": "x"}
        )
        assert _refusal_reason(out) == "env-marker-absent", out
        # No marker, no opt-out — the hint reflects that.
        assert "no ENSEMBLE_SELF_ENV marker" in out, out
        # And points at the remediation.
        assert "auto-derivation" in out, out
        # Does NOT claim the operator opted out.
        assert "explicitly opted out" not in out, out
        # Does NOT claim the operator set garbage.
        assert "IGNORED" not in out, out

    # ── S4: empty / whitespace-only marker → auto-derive ─────────────────────
    # The marker parser (``_read_marker_value``) trims whitespace and
    # treats empty / whitespace-only as ABSENT (post-trim = empty → None).
    # These tests cover the parsing branches end-to-end through
    # ``_self_env_marker()`` so the contract is locked at the
    # observable surface.

    @pytest.mark.parametrize("empty_marker", ["", " ", "   ", "\t", "\n", "  \t  "])
    def test_empty_or_whitespace_marker_means_absent(
        self, empty_marker: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """S4: an empty-string OR whitespace-only marker is treated
        as ABSENT — NOT as garbage — so the resolver falls through
        to auto-derivation without logging a garbage WARNING. The
        ``_read_marker_value`` parser trims and returns None for
        these cases (the resolved-env source is ``auto`` or
        ``auto-unresolved``, never ``ignored-garbage``).
        """
        monkeypatch.setenv("ENSEMBLE_SELF_ENV", empty_marker)
        monkeypatch.delenv("POSTGRES_DB", raising=False)
        fake_home = tmp_path / "fake-home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        monkeypatch.setattr(ut.sys, "frozen", False, raising=False)
        # _read_marker_value collapses these to None.
        assert ut._read_marker_value() is None
        # NOT garbage.
        assert ut._is_garbage_marker() is False
        # Source is auto / auto-unresolved — never ignored-garbage.
        source = ut._self_env_source()
        assert source in ("auto", "auto-unresolved"), source

    # ── S5: parametrized explicit-wins over contradictory auto-evidence ──────
    # The explicit marker is the highest-priority signal. The existing
    # ``test_explicit_marker_wins_over_auto_derive`` covers ONE combo
    # (marker='demo' + POSTGRES_DB=ensemble_prod). This parametrized
    # test covers the four valid (marker, conflicting-POSTGRES_DB)
    # combos so a regression that flips the precedence for any one
    # env is caught.

    @pytest.mark.parametrize(
        "marker,planted_dir,planted_db,conflicting_auto",
        [
            # Each case plants install-dir evidence for one env (the
            # auto-derive target) and sets the marker to a DIFFERENT
            # env. The marker must win over the contradictory
            # install-dir evidence. The 4 cases cover all 4 valid
            # marker values.
            ("demo", "agents-ensemble", "ensemble_prod", "live"),
            ("live", "agents-ensemble-demo", "ensemble_demo", "demo"),
            ("dev", "agents-ensemble", "ensemble_prod", "live"),
            ("sandbox", "agents-ensemble", "ensemble_prod", "live"),
        ],
    )
    def test_explicit_marker_wins_over_any_conflicting_auto_signal(
        self,
        marker: str,
        planted_dir: str,
        planted_db: str,
        conflicting_auto: str,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """S5: the explicit marker wins over any contradictory auto-
        evidence — the operator's staging intent overrides ambient
        signals. Parametrized across the 4 valid marker values (one
        case per value) so a regression that flipped precedence for
        ONE env (e.g. dev losing to live) is caught.

        Each case plants install-dir .env evidence for one env (the
        conflicting auto target) and sets the marker to a DIFFERENT
        env. Without the marker, auto-derive resolves to the planted
        env; with the marker set, the marker wins.
        """
        fake_home = tmp_path / "fake-home"
        fake_home.mkdir()
        # Plant install-dir evidence for the conflicting auto target.
        planted_env = fake_home / planted_dir / ".env"
        planted_env.parent.mkdir()
        planted_env.write_text(f"POSTGRES_DB={planted_db}\n", encoding="utf-8")
        monkeypatch.setenv("ENSEMBLE_SELF_ENV", marker)
        # Ambient POSTGRES_DB unset — the install-dir evidence is the
        # sole signal for the auto-derive path (matches the live / demo
        # branches in _auto_derive_env).
        monkeypatch.delenv("POSTGRES_DB", raising=False)
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        monkeypatch.setattr(ut.sys, "frozen", False, raising=False)
        # Sanity check: WITHOUT the marker, auto-derive resolves to
        # conflicting_auto (the planted env). This proves the test
        # scenario is meaningful — there IS contradictory evidence.
        monkeypatch.delenv("ENSEMBLE_SELF_ENV", raising=False)
        assert ut._auto_derive_env() == conflicting_auto, (
            f"test sanity: planted {planted_dir}/{planted_db} must "
            f"auto-derive to {conflicting_auto!r} for the explicit-wins "
            f"assertion to be meaningful"
        )
        # With the marker set, the marker wins — auto-derive is bypassed.
        monkeypatch.setenv("ENSEMBLE_SELF_ENV", marker)
        assert ut._self_env_marker() == marker, (
            f"explicit marker {marker} must win over "
            f"conflicting auto={conflicting_auto} "
            f"(planted {planted_dir}/{planted_db})"
        )
        assert ut._self_env_source() == "explicit"


# ── system_restart matrix ────────────────────────────────────────────────────


class TestSystemRestartMatrix:
    async def test_unknown_mode(self, harness) -> None:
        tools, _, _ = harness
        out = await tools["system_restart"].ainvoke(
            {"target_env": "demo", "reason": "x", "mode": "force"}
        )
        assert _refusal_reason(out) == "unknown-mode"

    async def test_live_restart_refused_unconditional(
        self, install: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A2: live restart refused outright — no gate, no override, no
        dry-run exception (FAKE live marker; /tmp fixture install)."""
        monkeypatch.setenv("ENSEMBLE_SELF_ENV", "live")
        monkeypatch.setattr(ut, "_resolve_install_dir", lambda self_env: install)
        manager = MagicMock()
        manager.config.daemon.port = 0
        manager._task_repo = None
        tools = _build_tools(manager)
        for dry in (True, False):
            out = await tools["system_restart"].ainvoke(
                {
                    "target_env": "live",
                    "reason": "x",
                    "dry_run": dry,
                    "user_confirmed": True,  # even with every confirm factor
                    "nonce": "CONFIRM-ABCDEFGH",
                }
            )
            assert _refusal_reason(out) == "live-restart-refused", (dry, out)
            assert "manual" in out.lower()

    async def test_no_staged_install(self, harness, monkeypatch) -> None:
        tools, _, _ = harness
        monkeypatch.setattr(ut, "_resolve_install_dir", lambda self_env: None)
        out = await tools["system_restart"].ainvoke(
            {"target_env": "demo", "reason": "x"}
        )
        assert _refusal_reason(out) == "no-staged-install"

    async def test_journal_unavailable_absent(self, harness, install) -> None:
        tools, _, _ = harness
        uj.journal_path(install).unlink()
        out = await tools["system_restart"].ainvoke(
            {"target_env": "demo", "reason": "x", "dry_run": False}
        )
        assert _refusal_reason(out) == "journal-unavailable"
        assert "absent" in out  # distinct from no-staged-install (nit #11)

    async def test_journal_unavailable_torn(self, harness, install) -> None:
        tools, _, _ = harness
        uj.journal_path(install).write_text('{"current":', encoding="utf-8")
        out = await tools["system_restart"].ainvoke(
            {"target_env": "demo", "reason": "x", "dry_run": False}
        )
        assert _refusal_reason(out) == "journal-unavailable"
        assert "torn" in out

    async def test_arm_failure_unwinds_in_flight_wedge(
        self, harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """P2.2 fix pass: arming throws BETWEEN the in_flight write and the
        pending_op write (disk full) — the except path must best-effort
        clear in_flight so the journal never claims a live txn with no
        pending_op (the partial-arm wedge), and the error carries a
        remediation hint."""
        tools, _, install = harness

        def _boom(install_dir, op):
            raise OSError("disk full (test)")

        monkeypatch.setattr(uj, "write_pending_op", _boom)
        out = await tools["system_restart"].ainvoke(
            {"target_env": "demo", "reason": "x", "dry_run": False}
        )
        assert out.startswith("Error: system_restart failed while arming")
        assert "unwound" in out  # remediation hint present
        journal = uj.journal_read(install)
        assert journal["in_flight"] is None, "wedge: in_flight must be cleared"
        assert uj.read_pending_op(install) is None
        assert uj.lock_run_id(install) is None  # lock released too

    async def test_reconcile_failure_does_not_gate_journal_read(
        self, harness, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        """P2.2 fix pass: reconcile_pending_op raising must never blind the
        tool — the journal READ status alone decides (journal-unavailable
        only when the read says torn/absent), and the failure is logged."""
        tools, _, install = harness

        def _boom(install_dir):
            raise RuntimeError("reconcile exploded (test)")

        monkeypatch.setattr(uj, "reconcile_pending_op", _boom)
        out = await tools["system_restart"].ainvoke(
            {"target_env": "demo", "reason": "x", "dry_run": True}
        )
        assert _refusal_reason(out) is None, out
        assert "RESTART PREVIEW" in out  # healthy journal read → preview
        assert any(
            "reconcile_pending_op failed" in str(r.message) for r in caplog.records
        )

    async def test_pipeline_busy_in_flight_txn_names_run_id(self, harness) -> None:
        tools, _, install = harness
        uj.journal_update_field(
            install,
            "in_flight",
            {"kind": "promote", "target": "1.2.3", "started_at": uj.now_iso(),
             "flipped": False, "owner_pid": 999, "run_id": "r-busy-1"},
        )
        out = await tools["system_restart"].ainvoke(
            {"target_env": "demo", "reason": "x", "dry_run": False}
        )
        assert _refusal_reason(out) == "pipeline-busy"
        assert "r-busy-1" in out

    async def test_pipeline_busy_pending_op_names_run_id(self, harness) -> None:
        tools, _, install = harness
        op = uj.PendingOp(
            run_id="r-pending-1", kind="restart", env="demo", owner_pid=999
        )
        uj.write_pending_op(install, op)
        out = await tools["system_restart"].ainvoke(
            {"target_env": "demo", "reason": "x", "dry_run": False}
        )
        assert _refusal_reason(out) == "pipeline-busy"
        assert "r-pending-1" in out

    async def test_pipeline_busy_lock_held_names_run_id(self, harness) -> None:
        tools, _, install = harness
        uj.lock_acquire(install, "r-lock-1", owner_pid=os.getpid())
        try:
            out = await tools["system_restart"].ainvoke(
                {"target_env": "demo", "reason": "x", "dry_run": False}
            )
            assert _refusal_reason(out) == "pipeline-busy"
            assert "r-lock-1" in out
        finally:
            uj.lock_release(install)

    async def test_restart_under_burst_abort(self, harness, install) -> None:
        tools, _, _ = harness
        (install / ".launcher-state").write_text(
            "last_exit=1\n"
            f"crash_count=6\n"
            f"window_start={int(__import__('time').time())}\n"
            "last_backoff=300\nnotified_75=0\nlast_uptime=5\n",
            encoding="utf-8",
        )
        out = await tools["system_restart"].ainvoke(
            {"target_env": "demo", "reason": "x", "dry_run": False}
        )
        assert _refusal_reason(out) == "restart-under-burst-abort"

    async def test_burst_abort_not_latched_when_window_aged(
        self, harness, install
    ) -> None:
        """Same crash budget but the 600s window aged out → NOT latched (the
        refusal must not fire on a stale burst signature)."""
        tools, markers, _ = harness
        (install / ".launcher-state").write_text(
            "last_exit=1\ncrash_count=9\n"
            f"window_start={int(__import__('time').time()) - 700}\n"
            "last_backoff=300\nnotified_75=0\nlast_uptime=5\n",
            encoding="utf-8",
        )
        out = await tools["system_restart"].ainvoke(
            {"target_env": "demo", "reason": "x", "dry_run": False}
        )
        assert "RESTART SCHEDULED" in out, out

    async def test_executor_scripts_unavailable(self, harness, monkeypatch) -> None:
        tools, _, _ = harness
        monkeypatch.setattr(ut, "_resolve_scripts_dir", lambda install_dir: None)
        out = await tools["system_restart"].ainvoke(
            {"target_env": "demo", "reason": "x", "dry_run": False}
        )
        assert _refusal_reason(out) == "executor-scripts-unavailable"

    async def test_dry_run_default_true_zero_mutation(self, harness, no_spawn) -> None:
        tools, markers, install = harness
        before = _journal_bytes(install)
        out = await tools["system_restart"].ainvoke(
            {"target_env": "demo", "reason": "preview"}
        )  # dry_run defaults TRUE
        assert "RESTART PREVIEW (dry-run)" in out
        assert "NO mutation happened" in out
        assert _journal_bytes(install) == before
        assert not (install / "releases" / "rollback.lock.d").exists()
        assert markers == []
        assert no_spawn == []

    async def test_armed_demo_returns_scheduled_no_spawn(self, harness, no_spawn) -> None:
        """D2 sequencing: armed restart returns BEFORE any stop signal — no
        process spawned by the tool itself; marker + journal set instead."""
        tools, markers, install = harness
        before_lock_free = not (install / "releases" / "rollback.lock.d").exists()
        assert before_lock_free
        out = await tools["system_restart"].ainvoke(
            {"target_env": "demo", "reason": "sequencing test", "dry_run": False}
        )
        assert "RESTART SCHEDULED" in out
        m = re.search(r"run_id=(r-\S+)", out)
        assert m, out
        run_id = m.group(1)
        # No execution inside the call (the recorder raises on any Popen).
        assert no_spawn == []
        # The durable state IS set: journal txn + pending op + D2 marker.
        data = uj.journal_read(install)
        assert data["in_flight"]["kind"] == "restart"
        assert data["in_flight"]["run_id"] == run_id
        assert data["pending_op"]["run_id"] == run_id
        assert data["pending_restart"] == run_id
        assert (install / "releases" / "rollback.lock.d" / "run_id").read_text().strip() == run_id
        # The in-memory post-turn trigger is armed (execution happens later).
        assert len(markers) == 1
        assert markers[0]["kind"] == "restart"
        assert markers[0]["run_id"] == run_id
        assert markers[0]["install_dir"] == str(install)

    async def test_second_arm_while_active_pipeline_busy(self, harness) -> None:
        tools, markers, install = harness
        first = await _arm_restart(harness)
        out = await tools["system_restart"].ainvoke(
            {"target_env": "demo", "reason": "second", "dry_run": False}
        )
        assert _refusal_reason(out) == "pipeline-busy"
        assert first in out  # the ACTIVE run_id is named

    async def test_armed_journal_readable_by_restart_sh_preflight(
        self, harness
    ) -> None:
        """Cross-writer handoff: the tool-armed journal is parseable by
        restart.sh's preflight. Probed SAFELY — a deliberately MISMATCHED
        --run-id makes restart.sh exit 78 at preflight, BEFORE any stop
        step (its own run-id interference guard)."""
        tools, markers, install = harness
        await _arm_restart(harness)
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": _isolated_home(install.parent),  # M3: never the real home
            "INSTALL_DIR": str(install),
            "PORT": str(SANDBOX_PORT),
            "POSTGRES_DB": "ensemble_sandbox",
        }
        rc = subprocess.run(
            [
                "bash",
                str(REPO_ROOT / "scripts" / "upgrade" / "restart.sh"),
                "sandbox",
                "--run-id",
                "r-deliberately-wrong",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert rc.returncode == 78, (rc.returncode, rc.stdout, rc.stderr)
        assert "does not match --run-id" in (rc.stdout + rc.stderr)

    async def test_restart_sh_requires_explicit_target(self, install: Path) -> None:
        """M6 (P2.2 fix pass): a no-arg restart.sh invocation refuses
        exit 78 — the silent ``${TARGET:-demo}`` default is GONE (it used
        to aim a no-arg call at the REAL demo install). ``TARGET`` env
        remains an accepted explicit channel and gets PAST the target
        check (failing later on the deliberately missing --run-id)."""
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": _isolated_home(install.parent),  # M3: never the real home
            "INSTALL_DIR": str(install),
            "PORT": str(SANDBOX_PORT),
            "POSTGRES_DB": "ensemble_sandbox",
        }
        rc = subprocess.run(
            ["bash", str(REPO_ROOT / "scripts" / "upgrade" / "restart.sh")],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert rc.returncode == 78, (rc.returncode, rc.stdout, rc.stderr)
        assert "explicit target required" in (rc.stdout + rc.stderr)
        # TARGET env = explicit channel → passes the target check, then
        # refuses on the (deliberately) absent --run-id instead.
        rc2 = subprocess.run(
            ["bash", str(REPO_ROOT / "scripts" / "upgrade" / "restart.sh")],
            env={**env, "TARGET": "sandbox"},
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert rc2.returncode == 78, (rc2.returncode, rc2.stdout, rc2.stderr)
        assert "explicit --run-id required" in (rc2.stdout + rc2.stderr)


# ── system_upgrade matrix ────────────────────────────────────────────────────


class TestSystemUpgradeMatrix:
    async def test_no_staged_install(self, harness, monkeypatch) -> None:
        tools, _, _ = harness
        monkeypatch.setattr(ut, "_resolve_install_dir", lambda self_env: None)
        out = await tools["system_upgrade"].ainvoke({"target_env": "demo"})
        assert _refusal_reason(out) == "no-staged-install"

    async def test_journal_unavailable(self, harness, install) -> None:
        tools, _, _ = harness
        uj.journal_path(install).unlink()
        out = await tools["system_upgrade"].ainvoke({"target_env": "demo"})
        assert _refusal_reason(out) == "journal-unavailable"

    async def test_reconcile_failure_does_not_gate_journal_read(
        self, harness, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        """P2.2 fix pass (system_upgrade site): a reconcile exception is
        logged and never gates — the journal READ status decides alone."""
        tools, _, _ = harness

        def _boom(install_dir):
            raise RuntimeError("reconcile exploded (test)")

        monkeypatch.setattr(uj, "reconcile_pending_op", _boom)
        out = await tools["system_upgrade"].ainvoke(
            {"target_env": "demo", "dry_run": True}
        )
        assert _refusal_reason(out) is None, out
        assert "UPGRADE PREFLIGHT" in out  # healthy journal read → preflight
        assert any(
            "reconcile_pending_op failed" in str(r.message) for r in caplog.records
        )

    async def test_target_not_staged_no_default(self, harness) -> None:
        """No version given and no staged-but-not-current release (only the
        current release exists) → target-not-staged."""
        tools, _, install = harness
        # remove 1.2.3 → only current 1.2.2 remains
        import shutil

        shutil.rmtree(install / "releases" / "1.2.3")
        out = await tools["system_upgrade"].ainvoke({"target_env": "demo"})
        assert _refusal_reason(out) == "target-not-staged"

    async def test_target_not_staged_explicit_version(self, harness) -> None:
        tools, _, _ = harness
        out = await tools["system_upgrade"].ainvoke(
            {"target_env": "demo", "version": "9.9.9"}
        )
        assert _refusal_reason(out) == "target-not-staged"

    async def test_target_quarantined(self, harness, install) -> None:
        tools, _, _ = harness
        uj.journal_update_field(install, "quarantined", ["1.2.3"])
        out = await tools["system_upgrade"].ainvoke(
            {"target_env": "demo", "version": "1.2.3"}
        )
        assert _refusal_reason(out) == "target-quarantined"

    async def test_manifest_unsafe_rollback_safe_false(self, harness, tmp_path) -> None:
        tools, _, install = harness
        _write_manifest(install, "1.9.9", rollback_safe=False)
        out = await tools["system_upgrade"].ainvoke(
            {"target_env": "demo", "version": "1.9.9"}
        )
        assert _refusal_reason(out) == "manifest-unsafe"
        assert "rollback_safe=false" in out

    async def test_manifest_unsafe_missing_manifest(self, harness, install) -> None:
        tools, _, _ = harness
        (install / "releases" / "1.8.8").mkdir()
        out = await tools["system_upgrade"].ainvoke(
            {"target_env": "demo", "version": "1.8.8"}
        )
        assert _refusal_reason(out) == "manifest-unsafe"

    async def test_rollback_cap_exceeded(self, harness, install) -> None:
        tools, _, _ = harness
        uj.journal_update_field(
            install,
            "rollback_window_count",
            {"24h": 3, "window_start": uj.now_iso()},
        )
        out = await tools["system_upgrade"].ainvoke(
            {"target_env": "demo", "version": "1.2.3", "dry_run": False}
        )
        assert _refusal_reason(out) == "rollback-cap-exceeded"

    async def test_rollback_cap_boundary_two_allowed(self, harness) -> None:
        tools, markers, install = harness
        uj.journal_update_field(
            install,
            "rollback_window_count",
            {"24h": 2, "window_start": uj.now_iso()},
        )
        out = await tools["system_upgrade"].ainvoke(
            {"target_env": "demo", "version": "1.2.3", "dry_run": False}
        )
        assert "UPGRADE ARMED" in out, out  # 2 < 3 → proceeds

    async def test_rollback_cap_window_aged_resets(self, harness) -> None:
        tools, _, install = harness
        aged = uj.iso_plus(uj.now_iso(), -90000)  # 25h ago
        uj.journal_update_field(
            install, "rollback_window_count", {"24h": 3, "window_start": aged}
        )
        out = await tools["system_upgrade"].ainvoke(
            {"target_env": "demo", "version": "1.2.3", "dry_run": False}
        )
        assert "UPGRADE ARMED" in out, out  # aged window → count 0

    async def test_cooldown_active_armed_refused(self, harness, install) -> None:
        tools, _, _ = harness
        uj.journal_update_field(
            install, "cooldown_until", uj.iso_plus(uj.now_iso(), 600)
        )
        out = await tools["system_upgrade"].ainvoke(
            {"target_env": "demo", "version": "1.2.3", "dry_run": False}
        )
        assert _refusal_reason(out) == "cooldown-active"
        # dry_run only REPORTS the cooldown (preview, not refusal) —
        # D-FA4.2 entry-side semantics.
        preview = await tools["system_upgrade"].ainvoke(
            {"target_env": "demo", "version": "1.2.3"}
        )
        assert "dry-run" in preview and "cooldown=active-until=" in preview
        assert "REFUSED" not in preview

    async def test_layout_divergence_dangling_current(self, harness, install) -> None:
        tools, _, _ = harness
        (install / "current").unlink()
        (install / "current").symlink_to("releases/nonexistent")
        out = await tools["system_upgrade"].ainvoke({"target_env": "demo"})
        assert _refusal_reason(out) == "layout-divergence"

    async def test_executor_scripts_unavailable(self, harness, monkeypatch) -> None:
        tools, _, _ = harness
        monkeypatch.setattr(ut, "_resolve_scripts_dir", lambda install_dir: None)
        out = await tools["system_upgrade"].ainvoke(
            {"target_env": "demo", "version": "1.2.3", "dry_run": False}
        )
        assert _refusal_reason(out) == "executor-scripts-unavailable"

    async def test_pipeline_busy_armed(self, harness, install) -> None:
        tools, _, _ = harness
        op = uj.PendingOp(
            run_id="r-up-busy", kind="promote", env="demo", owner_pid=999,
            target="1.2.3",
        )
        uj.write_pending_op(install, op)
        out = await tools["system_upgrade"].ainvoke(
            {"target_env": "demo", "version": "1.2.3", "dry_run": False}
        )
        assert _refusal_reason(out) == "pipeline-busy"
        assert "r-up-busy" in out

    async def test_dry_run_default_true_zero_mutation(self, harness, no_spawn) -> None:
        tools, markers, install = harness
        before = _journal_bytes(install)
        out = await tools["system_upgrade"].ainvoke({"target_env": "demo"})
        assert "UPGRADE PREFLIGHT (dry-run)" in out
        assert "NO mutation happened" in out
        assert "target=1.2.3" in out  # latest staged-not-current default
        assert _journal_bytes(install) == before
        assert markers == []
        assert no_spawn == []
        # demo dry_run issues NO nonce (nonce is live-only)
        assert "CONFIRMATION REQUIRED" not in out
        assert "CONFIRM-" not in out

    async def test_armed_demo_returns_scheduled_no_spawn(self, harness, no_spawn) -> None:
        """D3 sequencing: armed upgrade returns before any promote execution."""
        tools, markers, install = harness
        out = await tools["system_upgrade"].ainvoke(
            {"target_env": "demo", "version": "1.2.3", "dry_run": False}
        )
        assert "UPGRADE ARMED" in out
        m = re.search(r"run_id=(r-\S+)", out)
        assert m, out
        run_id = m.group(1)
        assert no_spawn == []
        data = uj.journal_read(install)
        assert data["pending_op"]["kind"] == "promote"
        assert data["pending_op"]["run_id"] == run_id
        assert data["pending_op"]["target"] == "1.2.3"
        # Non-live: no confirmation bookkeeping.
        assert data["pending_op"]["nonce"] is None
        assert data["pending_op"]["confirmed_by_human"] is False
        assert markers[0]["kind"] == "promote"
        assert markers[0]["run_id"] == run_id

    async def test_second_arm_while_active_pipeline_busy(self, harness) -> None:
        tools, _, install = harness
        first_out = await tools["system_upgrade"].ainvoke(
            {"target_env": "demo", "version": "1.2.3", "dry_run": False}
        )
        first_run = re.search(r"run_id=(r-\S+)", first_out).group(1)
        out = await tools["system_upgrade"].ainvoke(
            {"target_env": "demo", "version": "1.2.3", "dry_run": False}
        )
        assert _refusal_reason(out) == "pipeline-busy"
        assert first_run in out


# ── LIVE 3-factor gate (§4.3) — FAKE live marker + /tmp fixture ONLY ─────────


@pytest.fixture
def live_harness(install: Path, monkeypatch: pytest.MonkeyPatch):
    """FAKE live marker + /tmp fixture install, mock manager carrying the
    REAL window dict + marker recorder. Module-level (hoisted from
    TestLiveThreeFactorGate in the P2.2 fix pass) so the fix-pass classes
    share the exact same harness shape."""
    monkeypatch.setenv("ENSEMBLE_SELF_ENV", "live")  # FAKE
    monkeypatch.setattr(ut, "_resolve_install_dir", lambda self_env: install)
    manager = _base_manager()  # no-network port, idle task repo, no queue repo
    windows: dict[str, dict] = {}
    manager._user_origin_windows = windows
    markers: list[dict] = []
    manager.set_pending_system_execution = MagicMock(
        side_effect=lambda iid, spec: markers.append(dict(spec))
    )
    tools = _build_tools(manager)
    return {
        "tools": tools,
        "markers": markers,
        "install": install,
        "manager": manager,
        "windows": windows,
        "monkeypatch": monkeypatch,
    }


class TestLiveThreeFactorGate:
    """self-env is a FAKE live marker aimed at a /tmp fixture install — the
    ONLY live-shaped surface these tests may touch (hard constraint). Every
    armed-live call that is not a full 3-factor PASS must refuse."""

    @staticmethod
    def _stamp_window(live, *, source="api", msg_id="m-1", content=None):
        """Stamp a valid user-origin window (the server-side marker the
        REAL stamp site produces) + a queue row for the message."""
        live["windows"][INSTANCE_ID] = {
            "source": source,
            "message_id": msg_id,
            "stamped_at": uj.now_iso(),
            "expires_at": uj.iso_plus(uj.now_iso(), 600),
        }
        if content is not None:
            row = MagicMock(name=f"MessageRow[{msg_id}]")
            row.content = content
            repo = MagicMock(name="queue_repo")
            repo.get = MagicMock(return_value=row)
            live["manager"]._queue_repository = repo

    @staticmethod
    async def _mint_nonce(live) -> tuple[str, str, str]:
        """dry_run on fake-live issues the nonce; returns (run_id, nonce,
        grouped_rendering)."""
        _out, run_id, nonce, grouped = await TestLiveThreeFactorGate._mint_nonce_full(live)
        return run_id, nonce, grouped

    async def test_dry_run_issues_nonce_labeled_mutation_only(
        self, live_harness, no_spawn
    ) -> None:
        """Mint: dry_run on live issues a nonce, persists it, and the ONLY
        journal delta is the labeled pending_actions nonce record."""
        before = json.loads(_journal_bytes(live_harness["install"]))
        mint_out, run_id, nonce, grouped = await self._mint_nonce_full(live_harness)
        assert "NOTE: this preflight persisted ONLY the nonce" in mint_out
        assert f"run_id={run_id}" in mint_out
        after = json.loads(_journal_bytes(live_harness["install"]))
        # Only pending_actions changed (the labeled nonce mint).
        delta = {
            k: (before.get(k), after.get(k))
            for k in set(before) | set(after)
            if before.get(k) != after.get(k)
        }
        assert set(delta) == {"pending_actions"}, f"unexpected delta: {delta.keys()}"
        # No lock, no marker, no spawn on the mint path.
        assert not (live_harness["install"] / "releases" / "rollback.lock.d").exists()
        assert live_harness["markers"] == []
        assert no_spawn == []

    @staticmethod
    async def _mint_nonce_full(live) -> tuple[str, str, str, str]:
        """dry_run on fake-live issues the nonce; returns
        (output, run_id, nonce, grouped_rendering)."""
        out = await live["tools"]["system_upgrade"].ainvoke(
            {"target_env": "live", "version": "1.2.3"}
        )
        assert "CONFIRMATION REQUIRED (live): nonce CONFIRM-" in out, out
        m = re.search(r"nonce (CONFIRM-[A-Z2-7-]+)", out)
        nonce_grouped = m.group(1)
        actions = uj.journal_read(live["install"])["pending_actions"]
        assert len(actions) == 1, "dry_run must persist exactly one nonce record"
        (run_id, rec), = actions.items()
        return out, run_id, rec["nonce"], nonce_grouped

    async def test_factor1_missing_param_false(self, live_harness) -> None:
        live = live_harness
        run_id, nonce, grouped = await self._mint_nonce(live)
        self._stamp_window(live, content=f"yes please {grouped}")
        out = await live["tools"]["system_upgrade"].ainvoke(
            {"target_env": "live", "version": "1.2.3", "dry_run": False,
             "nonce": grouped}  # user_confirmed defaults False
        )
        assert _refusal_reason(out) == "user-confirmation-missing"
        assert "user_confirmed" in out

    async def test_factor2_missing_spoofed_origin_no_window(self, live_harness) -> None:
        """Spoofed origin (internal_agent:/agent:/cascade_resume/scheduler
        never stamp a window) → refusal even with param + valid nonce."""
        live = live_harness
        run_id, nonce, grouped = await self._mint_nonce(live)
        # NO window stamped (exactly what a spoofed-origin turn looks like).
        self._stamp_window(live, content=grouped)
        live["windows"].clear()  # simulate internal-origin turn: window absent
        out = await live["tools"]["system_upgrade"].ainvoke(
            {"target_env": "live", "version": "1.2.3", "dry_run": False,
             "user_confirmed": True, "nonce": grouped}
        )
        assert _refusal_reason(out) == "user-confirmation-missing"
        assert "whitelisted user-origin" in out

    async def test_factor2_window_expired(self, live_harness) -> None:
        live = live_harness
        _, _, grouped = await self._mint_nonce(live)
        self._stamp_window(live, content=grouped)
        live["windows"][INSTANCE_ID]["expires_at"] = uj.iso_plus(uj.now_iso(), -60)
        out = await live["tools"]["system_upgrade"].ainvoke(
            {"target_env": "live", "version": "1.2.3", "dry_run": False,
             "user_confirmed": True, "nonce": grouped}
        )
        assert _refusal_reason(out) == "user-confirmation-missing"
        assert "expired" in out

    async def test_factor3_row_lacks_nonce(self, live_harness) -> None:
        """Valid window + matching nonce param, but the triggering human
        message CONTENT does not carry the nonce → factor 3 refuses."""
        live = live_harness
        _, nonce, grouped = await self._mint_nonce(live)
        self._stamp_window(live, content="yes go ahead please")  # no nonce echo
        out = await live["tools"]["system_upgrade"].ainvoke(
            {"target_env": "live", "version": "1.2.3", "dry_run": False,
             "user_confirmed": True, "nonce": grouped}
        )
        assert _refusal_reason(out) == "user-confirmation-missing"
        assert "carrying nonce" in out

    async def test_fabricated_user_confirmed_alone_refuses(self, live_harness) -> None:
        """user_confirmed=true with NO window and NO nonce — the fabricated
        param never unlocks live."""
        live = live_harness
        out = await live["tools"]["system_upgrade"].ainvoke(
            {"target_env": "live", "version": "1.2.3", "dry_run": False,
             "user_confirmed": True}
        )
        assert _refusal_reason(out) == "user-confirmation-missing"
        assert "whitelisted user-origin" in out  # factor 2 is the binding one
        # Nothing armed by the fabricated call.
        assert live["markers"] == []

    async def test_fabricated_nonce_with_no_window_refuses(self, live_harness) -> None:
        """S3 armed-spoof case: user_confirmed=true with a FABRICATED
        nonce string + NO genuine user-origin window → REFUSED. This is
        the user-direction hard invariant: the gate cannot be bypassed
        by supplying ANY nonce string. Without factor 2 (the server-
        side window the dispatch seam stamps on whitelisted user-
        origin turns), factor 1 (user_confirmed param) and factor 3
        (nonce content match) cannot unlock live.

        The threat model: an attacker / spoofed caller fabricates a
        nonce string and sets user_confirmed=True, hoping the gate
        will look at factors 1+3 and bypass factor 2. The gate's
        factor-2 check fires first (window is None → fail), so the
        fabricated nonce never gets evaluated — exactly the
        invariant this test pins.
        """
        live = live_harness
        # No window stamped on this turn (forged origin path).
        assert live["windows"] == {}
        # A fabricated nonce string — does NOT exist in the journal's
        # pending_actions. Also NOT echoed in any human message (no
        # window, no queue row).
        out = await live["tools"]["system_upgrade"].ainvoke(
            {
                "target_env": "live",
                "version": "1.2.3",
                "dry_run": False,
                "user_confirmed": True,
                "nonce": "CONFIRM-FAKEFABFABFABFAB",  # arbitrary forged string
            }
        )
        # The gate cannot be bypassed — factor 2 binds (no window).
        assert _refusal_reason(out) == "user-confirmation-missing", out
        assert "whitelisted user-origin" in out, out
        # And the fabricated nonce is not in the journal's pending_actions
        # (the gate never reached the nonce-content check).
        data = uj.journal_read(live["install"])
        assert (
            "CONFIRM-FAKEFABFABFABFAB" not in data.get("pending_actions", {})
        )
        # Nothing armed.
        assert live["markers"] == []

    async def test_nonce_mismatch(self, live_harness) -> None:
        live = live_harness
        await self._mint_nonce(live)
        self._stamp_window(live, content="CONFIRM-ZZZZZZZZ")
        out = await live["tools"]["system_upgrade"].ainvoke(
            {"target_env": "live", "version": "1.2.3", "dry_run": False,
             "user_confirmed": True, "nonce": "CONFIRM-ZZZZZZZZ"}
        )
        assert _refusal_reason(out) == "nonce-mismatch"

    async def test_nonce_expired(self, live_harness) -> None:
        live = live_harness
        run_id, nonce, grouped = await self._mint_nonce(live)
        self._stamp_window(live, content=grouped)
        # Force the TTL into the past.
        data = uj.journal_read(live["install"])
        data["pending_actions"][run_id]["ttl_expires_at"] = uj.iso_plus(
            uj.now_iso(), -60
        )
        uj.journal_write(live["install"], data)
        out = await live["tools"]["system_upgrade"].ainvoke(
            {"target_env": "live", "version": "1.2.3", "dry_run": False,
             "user_confirmed": True, "nonce": grouped}
        )
        assert _refusal_reason(out) == "nonce-expired"

    async def test_nonce_verification_unavailable(self, live_harness) -> None:
        """The MessageQueue row is unreadable (daemon restarted — rows wiped
        at boot) → fail-closed nonce-verification-unavailable (D-FA3.3)."""
        live = live_harness
        _, nonce, grouped = await self._mint_nonce(live)
        self._stamp_window(live, content=grouped)
        repo = MagicMock()
        repo.get = MagicMock(side_effect=RuntimeError("row wiped"))
        live["manager"]._queue_repository = repo
        out = await live["tools"]["system_upgrade"].ainvoke(
            {"target_env": "live", "version": "1.2.3", "dry_run": False,
             "user_confirmed": True, "nonce": grouped}
        )
        assert _refusal_reason(out) == "nonce-verification-unavailable"

    async def test_full_pass_consumes_nonce_and_arms(
        self, live_harness, no_spawn
    ) -> None:
        """The 3-factor PASS case (FAKE live marker, /tmp fixture): the
        armed call carries the nonce record's run_id (the mint and the arm
        share one cross-death join key) and burns the nonce single-use."""
        live = live_harness
        run_id, nonce, grouped = await self._mint_nonce(live)
        self._stamp_window(live, source="api", msg_id="m-confirm", content=grouped)
        out = await live["tools"]["system_upgrade"].ainvoke(
            {"target_env": "live", "version": "1.2.3", "dry_run": False,
             "user_confirmed": True, "nonce": grouped}
        )
        assert "UPGRADE ARMED" in out, out
        assert f"run_id={run_id}" in out, (
            f"armed run_id must equal the nonce record's run_id ({run_id}): {out}"
        )
        assert "nonce consumed (confirmed_source=api)" in out
        # Journal: nonce burned single-use + op carries the confirmation.
        data = uj.journal_read(live["install"])
        assert data["pending_actions"][run_id]["consumed_at"] is not None
        assert data["pending_op"]["nonce"] == nonce
        assert data["pending_op"]["nonce_consumed"] is True
        assert data["pending_op"]["confirmed_by_human"] is True
        assert data["pending_op"]["confirmed_source"] == "api"
        assert any(
            e["event"] == "nonce_consumed" for e in data["history"]
        )
        # Sequencing holds on live too: no spawn inside the call.
        assert no_spawn == []
        assert live["markers"][0]["kind"] == "promote"
        assert live["markers"][0]["run_id"] == run_id

    async def test_replay_after_consume_refused(self, live_harness) -> None:
        """Single-use: after a successful armed pass, replaying the same
        nonce (even with a fresh valid window + content) → nonce-already-
        used."""
        live = live_harness
        run_id, nonce, grouped = await self._mint_nonce(live)
        self._stamp_window(live, content=grouped)
        out = await live["tools"]["system_upgrade"].ainvoke(
            {"target_env": "live", "version": "1.2.3", "dry_run": False,
             "user_confirmed": True, "nonce": grouped}
        )
        assert "UPGRADE ARMED" in out
        # Simulate the promote completing so the pipeline is free again.
        uj.journal_history_append(live["install"], "commit", f"run {run_id} committed")
        uj.clear_pending_op(live["install"])
        uj.lock_release(live["install"])
        # Fresh window + the same nonce echo.
        self._stamp_window(live, msg_id="m-replay", content=grouped)
        replay = await live["tools"]["system_upgrade"].ainvoke(
            {"target_env": "live", "version": "1.2.3", "dry_run": False,
             "user_confirmed": True, "nonce": grouped}
        )
        assert _refusal_reason(replay) == "nonce-already-used", replay

    async def test_live_refusals_never_mutate_pipeline(
        self, live_harness, no_spawn
    ) -> None:
        """Every live armed refusal leaves the pipeline untouched (no lock,
        no pending_op) — refusal paths are read-only."""
        live = live_harness
        _, _, grouped = await self._mint_nonce(live)
        before = _journal_bytes(live["install"])
        live["windows"].clear()
        out = await live["tools"]["system_upgrade"].ainvoke(
            {"target_env": "live", "version": "1.2.3", "dry_run": False,
             "user_confirmed": True, "nonce": grouped}
        )
        assert _refusal_reason(out) == "user-confirmation-missing"
        assert _journal_bytes(live["install"]) == before
        assert not (live["install"] / "releases" / "rollback.lock.d").exists()
        assert live["markers"] == []
        assert no_spawn == []


# ── P2.2 fix pass (2026-08-23) — gate hardening + the forged-source seam ────


class TestGateHardeningFixPass:
    """MAJOR-1(b) / MAJOR-2 / M1 of the independent-reviewer fix list: the
    nonce is INSTANCE-bound and ACTION-bound (kind+env+target, §4.2(b)),
    and an unparseable TTL fails CLOSED. Same fake-live marker + /tmp
    fixture discipline as TestLiveThreeFactorGate."""

    OTHER_INSTANCE = "inst-other-4242"

    def _stamp_window_for(
        self, live, instance: str, *, msg_id: str, content: str
    ) -> None:
        """A valid user-origin window + matching human content row for an
        ARBITRARY instance id (the class helper stamps INSTANCE_ID only)."""
        live["windows"][instance] = {
            "source": "api",
            "message_id": msg_id,
            "stamped_at": uj.now_iso(),
            "expires_at": uj.iso_plus(uj.now_iso(), 600),
        }
        row = MagicMock(name=f"MessageRow[{msg_id}]")
        row.content = content
        repo = MagicMock(name="queue_repo")
        repo.get = MagicMock(return_value=row)
        live["manager"]._queue_repository = repo

    async def test_nonce_minted_for_other_instance_refused(
        self, live_harness
    ) -> None:
        """MAJOR-1(b): a nonce minted by instance A must not arm from
        instance B — ``issued_to_instance`` (recorded at mint,
        upgrade_journal.PendingAction) is enforced at the gate. The field
        existed but was never checked before this fix (also closes
        reviewer N2). The refusal must NOT consume the nonce."""
        live = live_harness
        run_id, nonce, grouped = await TestLiveThreeFactorGate._mint_nonce(live)
        # A SECOND real toolset with a DIFFERENT current_instance_id, same
        # manager + install dir (the nonce store on disk is shared).
        tools_b = {
            t.name: t
            for t in create_upgrade_tools(
                live["manager"], self.OTHER_INSTANCE, agent_id="ari"
            )
        }
        # Every OTHER factor green: param, valid window, human content
        # carrying the nonce, TTL fresh.
        self._stamp_window_for(live, self.OTHER_INSTANCE, msg_id="m-b", content=grouped)
        out = await tools_b["system_upgrade"].ainvoke(
            {"target_env": "live", "version": "1.2.3", "dry_run": False,
             "user_confirmed": True, "nonce": grouped}
        )
        assert _refusal_reason(out) == "nonce-instance-mismatch", out
        assert live["markers"] == []  # nothing armed
        data = uj.journal_read(live["install"])
        assert data["pending_actions"][run_id]["consumed_at"] is None

    async def test_nonce_action_binding_version_mismatch_refused(
        self, live_harness, no_spawn
    ) -> None:
        """MAJOR-2: mint-for-1.2.3 → arm-with-1.2.2 → refused with the new
        nonce-action-mismatch token (the nonce authorizes exactly the
        kind/env/target it was minted for, §4.2(b)/§4.3); arming with the
        MATCHING version still passes — both directions in one flow."""
        live = live_harness
        run_id, nonce, grouped = await TestLiveThreeFactorGate._mint_nonce(live)
        TestLiveThreeFactorGate._stamp_window(live, source="api", msg_id="m-x", content=grouped)
        out = await live["tools"]["system_upgrade"].ainvoke(
            {"target_env": "live", "version": "1.2.2", "dry_run": False,
             "user_confirmed": True, "nonce": grouped}
        )
        assert _refusal_reason(out) == "nonce-action-mismatch", out
        assert "1.2.3" in out and "1.2.2" in out  # minted-vs-armed named
        assert live["markers"] == []
        # Positive direction: the SAME still-unconsumed nonce + the
        # MATCHING version passes the full 3-factor gate and arms.
        ok = await live["tools"]["system_upgrade"].ainvoke(
            {"target_env": "live", "version": "1.2.3", "dry_run": False,
             "user_confirmed": True, "nonce": grouped}
        )
        assert "UPGRADE ARMED" in ok, ok
        assert f"run_id={run_id}" in ok

    async def test_nonce_unparseable_ttl_fails_closed(self, live_harness) -> None:
        """M1: an unparseable ``ttl_expires_at`` counts as EXPIRED. Before
        the fix the check was ``ttl is not None and now > ttl`` — a corrupt
        (or hand-edited) nonce record failed OPEN and lived forever."""
        live = live_harness
        run_id, nonce, grouped = await TestLiveThreeFactorGate._mint_nonce(live)
        TestLiveThreeFactorGate._stamp_window(live, content=grouped)
        data = uj.journal_read(live["install"])
        data["pending_actions"][run_id]["ttl_expires_at"] = "not-a-timestamp"
        uj.journal_write(live["install"], data)
        out = await live["tools"]["system_upgrade"].ainvoke(
            {"target_env": "live", "version": "1.2.3", "dry_run": False,
             "user_confirmed": True, "nonce": grouped}
        )
        assert _refusal_reason(out) == "nonce-expired", out
        assert live["markers"] == []


class TestForgedSourceDispatchSeam:
    """MAJOR-1(c): the dispatch→stamp→gate seam, exercised piecewise-real.

    The verified attack chain: an agent caller passes
    ``source="telegram:attacker"`` to ``job_create``; the old override
    only fired on the exact default ``"api"`` so the forged source was
    accepted verbatim, threaded through dispatch
    (job_processor → instance_messaging → task_processor → manager), and
    the prefix whitelist stamped a VALID user-origin window — factor 2
    forged with zero human involvement. This chain drives each REAL seam
    in turn:

    (1) the REAL ``job_create`` tool function (agent caller + hostile
        source) → the enqueued source is forced to ``agent:<caller>``;
    (2) the REAL ``InstanceManager.stamp_user_origin_window`` with that
        forced source → NO window (a pre-existing user window is CLEARED);
    (3) the REAL 3-factor gate fed the REAL stamped (empty) window state
        → ``user-confirmation-missing``.

    The fully-wired funnel (real job_processor → instance_messaging →
    task_processor against a live daemon + DB) is integration scope —
    FLAGGED as a proposed tester/P2.3 integration case rather than faked
    here with mocks that re-implement the seam."""

    async def test_forged_source_never_yields_a_user_origin_window(
        self, live_harness
    ) -> None:
        live = live_harness
        # (1) REAL job_create tool: agent caller + hostile source param.
        captured: dict[str, Any] = {}
        job_item = MagicMock(name="JobItem")
        job_item.job_id = "job-forge-1"
        job_item.to_dict.return_value = {"job_id": "job-forge-1", "status": "QUEUED"}

        async def _enqueue(**kwargs: Any) -> MagicMock:
            captured.update(kwargs)
            return job_item

        job_service = MagicMock(name="JobQueueService")
        job_service.enqueue = _enqueue
        job_tools = create_job_tools(
            job_service=job_service,
            queue_mgmt_service=MagicMock(),
            dead_letter_service=MagicMock(),
            current_instance_id=INSTANCE_ID,
            agent_id="ari",
        )
        job_create = next(t for t in job_tools if t.name == "job_create")
        # NIT-7 (P2.3 review cycle 1): the dead ``source`` param is
        # deprecated-and-ignored BY SCHEMA CONTRACT — the description must
        # say so (server derives unconditionally; removal deferred for
        # schema compat). The hostile→derived assertion below is the
        # behavioral enforcement of the same ignore.
        src_field = job_create.args_schema.model_fields["source"]
        assert "DEPRECATED" in (src_field.description or ""), src_field.description
        assert "IGNORED" in (src_field.description or ""), src_field.description
        result = await job_create.ainvoke(
            {
                "agent_id": "ari",
                "message": "relay the upgrade nonce",
                "project_id": "proj-fixpass",
                "source": "telegram:attacker",  # hostile forge attempt
            }
        )
        assert "error" not in result, result
        assert captured.get("source") == "agent:ari", (
            "agent caller's hostile source must be forced to agent:ari, "
            f"got {captured.get('source')!r}"
        )
        # (2) REAL stamp site: the forced source is NOT user-origin — and
        # it must CLEAR a window left by a prior genuine user turn
        # (per-turn semantics — stale authorization never survives).
        from daemon.manager import InstanceManager

        mgr = object.__new__(InstanceManager)  # skip heavy __init__
        mgr._user_origin_windows = {}
        mgr.stamp_user_origin_window(INSTANCE_ID, "api", "m-user-turn")
        assert INSTANCE_ID in mgr._user_origin_windows  # genuine user turn
        mgr.stamp_user_origin_window(INSTANCE_ID, captured["source"], "m-agent-turn")
        assert INSTANCE_ID not in mgr._user_origin_windows  # forced → cleared
        # (3) REAL gate on the REAL stamped window state (chained from (2):
        # the dict the stamp site actually produced) — no user-origin
        # window ⇒ the 3-factor gate cannot pass, whatever the params.
        live["windows"].clear()
        live["windows"].update(mgr._user_origin_windows)
        run_id, nonce, grouped = await TestLiveThreeFactorGate._mint_nonce(live)
        out = await live["tools"]["system_upgrade"].ainvoke(
            {"target_env": "live", "version": "1.2.3", "dry_run": False,
             "user_confirmed": True, "nonce": grouped}
        )
        assert _refusal_reason(out) == "user-confirmation-missing", out
        assert "whitelisted user-origin" in out
        assert live["markers"] == []  # nothing armed

    async def test_empty_caller_hostile_source_never_verbatim(
        self, live_harness
    ) -> None:
        """MINOR-B (P2.2 carry-over, closed P2.3 B3.5): the source
        derivation is UNCONDITIONAL on the job_create path — the
        empty-caller fallback must not trust the LLM-authored ``source``
        param verbatim either, and must never mint a whitelisted source
        ("api" is in USER_ORIGIN_SOURCES). Mirrors job_continue's F3
        fallback: ``internal_agent:unknown`` — non-whitelisted, no window."""
        live = live_harness
        captured: dict[str, Any] = {}
        job_item = MagicMock(name="JobItem")
        job_item.job_id = "job-forge-2"
        job_item.to_dict.return_value = {"job_id": "job-forge-2", "status": "QUEUED"}

        async def _enqueue(**kwargs: Any) -> MagicMock:
            captured.update(kwargs)
            return job_item

        job_service = MagicMock(name="JobQueueService")
        job_service.enqueue = _enqueue
        job_tools = create_job_tools(
            job_service=job_service,
            queue_mgmt_service=MagicMock(),
            dead_letter_service=MagicMock(),
            current_instance_id=INSTANCE_ID,
            agent_id="",  # empty caller — the fallback branch
        )
        job_create = next(t for t in job_tools if t.name == "job_create")
        result = await job_create.ainvoke(
            {
                "agent_id": "ari",
                "message": "relay the upgrade nonce",
                "project_id": "proj-fixpass",
                "source": "api",  # hostile: whitelisted source from a tool param
            }
        )
        assert "error" not in result, result
        assert captured.get("source") == "internal_agent:unknown", (
            "empty-caller fallback must never mint/trust a whitelisted "
            f"source, got {captured.get('source')!r}"
        )
        # The REAL stamp site agrees: the fallback source is not user-origin.
        from daemon.manager import InstanceManager
        from daemon.tools.upgrade_journal import is_user_origin_source

        assert is_user_origin_source(captured["source"]) is False
        mgr = object.__new__(InstanceManager)  # skip heavy __init__
        mgr._user_origin_windows = {}
        mgr.stamp_user_origin_window(INSTANCE_ID, "api", "m-user-turn")
        assert INSTANCE_ID in mgr._user_origin_windows
        mgr.stamp_user_origin_window(INSTANCE_ID, captured["source"], "m-agent-turn")
        assert INSTANCE_ID not in mgr._user_origin_windows  # fallback → cleared
        assert live["markers"] == []  # nothing armed by this seam check


class TestDefaultVersionPick:
    """N6 (P2.3 B3.5): the default-version pick is semver-aware — the old
    ``sorted()[-1]`` lexical pick made 1.2.9 beat 1.2.10. Explicit
    ``version=`` selection is unaffected (covered by the staged-target
    matrix above)."""

    def test_key_numeric_ordering(self) -> None:
        # The exact ledger bug: 1.2.10 must outrank 1.2.9.
        assert ut._version_sort_key("1.2.10") > ut._version_sort_key("1.2.9")
        # Two-segment form of the same class: 1.10 > 1.9.
        assert ut._version_sort_key("1.10") > ut._version_sort_key("1.9")
        # Major/minor/patch monotonicity.
        assert ut._version_sort_key("2.0.0") > ut._version_sort_key("1.99.99")
        assert ut._version_sort_key("1.2.0") > ut._version_sort_key("1.2")

    def test_key_equal_tags(self) -> None:
        assert ut._version_sort_key("1.2.10") == ut._version_sort_key("1.2.10")
        assert ut._version_sort_key("dev-snapshot") == ut._version_sort_key("dev-snapshot")

    def test_key_non_numeric_fallback_below_numeric(self) -> None:
        # Non-numeric tags never outrank a plain numeric release and fall
        # back to lexical order within their own rank.
        assert ut._version_sort_key("1.2.3") > ut._version_sort_key("v1.2.3")
        assert ut._version_sort_key("1.2.3") > ut._version_sort_key("1.2.3-rc1")
        assert ut._version_sort_key("alpha") > ut._version_sort_key("aardvark")

    async def test_default_pick_semver_not_lexical(self, harness) -> None:
        """Through the REAL tool: staged 1.2.9 + 1.2.10 with no explicit
        version → the dry-run preflight targets 1.2.10 (a lexical pick
        would have chosen 1.2.9)."""
        tools, _markers, install = harness
        for v in ("1.2.9", "1.2.10"):
            rel = install / "releases" / v
            rel.mkdir(parents=True, exist_ok=True)
            (rel / "manifest.json").write_text(
                json.dumps(
                    {
                        "version": v,
                        "binary_version": f"v{v}",
                        "staged_at": "2026-08-23T09:00:00Z",
                        "rollback_safe": True,
                        "known_schema_gen": 14,
                    }
                ),
                encoding="utf-8",
            )
        out = await tools["system_upgrade"].ainvoke({"target_env": "demo"})
        assert "target=1.2.10" in out, out
        assert "1.2.10" in out and "releases/1.2.10" in out

class TestRefusalTaxonomy:
    """The documented refusal vocabulary (system_upgrade._full_doc_ +
    system_restart._full_doc_) matches the tokens the tests exercise — no
    token silently unimplemented, none asserted that the docs don't name."""

    UPGRADE_TOKENS = {
        "invalid-target-env",
        "env-marker-absent",
        "env-self-match",
        "target-not-staged",
        "target-quarantined",
        "manifest-unsafe",
        "rollback-cap-exceeded",
        "cooldown-active",
        "pipeline-busy",
        "user-confirmation-missing",
        "nonce-mismatch",
        "nonce-expired",
        "nonce-instance-mismatch",
        "nonce-action-mismatch",
        "nonce-already-used",
        "nonce-verification-unavailable",
        "executor-scripts-unavailable",
        "no-staged-install",
        "journal-unavailable",
        "layout-divergence",
    }
    RESTART_TOKENS = {
        "unknown-mode",
        "invalid-target-env",
        "env-marker-absent",
        "env-self-match",
        "live-restart-refused",
        "pipeline-busy",
        "restart-under-burst-abort",
        "executor-scripts-unavailable",
        "no-staged-install",
        "journal-unavailable",
    }

    def test_documented_tokens_cover_the_exercised_matrix(self) -> None:
        """Every token the refusal matrix above asserts appears verbatim as
        ``reason=<token>`` in the corresponding tool's _full_doc_ — the doc
        is the machine-readable taxonomy (D-FA2.2). Whitespace-normalized
        so line-wrapped tokens still match."""
        manager = MagicMock()
        tools = _build_tools(manager)

        def _norm(doc: str) -> str:
            # Un-wrap hyphenated line breaks, then collapse whitespace.
            unwrapped = re.sub(r"-\s*\n\s*", "-", doc)
            return " ".join(unwrapped.split())

        up_doc = _norm(tools["system_upgrade"]._full_doc_)
        rs_doc = _norm(tools["system_restart"]._full_doc_)
        for token in self.UPGRADE_TOKENS:
            assert f"reason={token}" in up_doc or token in up_doc, (
                f"system_upgrade docs do not name refusal token {token!r}"
            )
        for token in self.RESTART_TOKENS:
            assert f"reason={token}" in rs_doc or token in rs_doc, (
                f"system_restart docs do not name refusal token {token!r}"
            )

    async def test_refusal_strings_carry_machine_readable_token(
        self, harness
    ) -> None:
        """Meta-guard (NIT-E rename, P2.3 B3.5 — was misleadingly
        ``test_every_matrix_token_is_asserted_somewhere_above``, which
        overclaimed a coverage sweep it never performed): sampled REAL
        invocations pin the harness contract the matrix relies on —
        refusal strings really render the machine-readable
        ``Error: <TOOL> REFUSED — reason=<token>:`` form (one restart
        token, one upgrade token; per-token coverage lives in the matrix
        tests above, not here)."""
        tools, _, _ = harness
        out = await tools["system_restart"].ainvoke(
            {"target_env": "demo", "reason": "x", "mode": "force"}
        )
        assert out.startswith("Error: RESTART REFUSED — reason=unknown-mode: ")
        out2 = await tools["system_upgrade"].ainvoke({"target_env": "prod"})
        assert out2.startswith("Error: UPGRADE REFUSED — reason=invalid-target-env: ")


# ── Manager drain — the post-turn executor consumer (D-FA1.4 / T5) ──────────


class TestManagerDrainPendingExecution:
    """The REAL ``InstanceManager.drain_pending_system_execution`` with the
    executor-spawn seam mocked (monkeypatched on ``daemon.tools.
    upgrade_journal`` — the method imports that module at call time and
    reads the attribute off it, so the patch lands). Manager built via
    ``object.__new__`` (precedent: the stamp-site test in
    test_upgrade_journal.py) — the method only touches
    ``_pending_system_executions`` plus the journal module, so no heavy
    manager state is needed. All fixtures /tmp-only; the spawn stub never
    executes anything; no network, no live."""

    EXECUTOR_PID = 424242

    @pytest.fixture
    def drain_mgr(self):
        from daemon.manager import InstanceManager

        mgr = object.__new__(InstanceManager)  # skip heavy __init__
        mgr._pending_system_executions = {}
        return mgr

    @pytest.fixture
    def spawn_calls(self, monkeypatch: pytest.MonkeyPatch) -> list:
        """Recorder replacing the REAL spawn seam. Captures argv / install
        dir / extra_env AND whether the arm lock was still held AT SPAWN
        TIME — the probe that proves the promote handoff ordering
        (lock released BEFORE spawn) and the restart adoption (lock still
        held when the executor takes over)."""
        calls: list = []

        def _rec(argv, install_dir, extra_env=None) -> int:
            inst = Path(str(install_dir))
            calls.append(
                {
                    "argv": list(argv),
                    "install": inst,
                    "extra_env": dict(extra_env or {}),
                    "lock_held_at_spawn": uj.lock_dir(inst).exists(),
                }
            )
            return self.EXECUTOR_PID

        monkeypatch.setattr(uj, "spawn_executor", _rec)
        return calls

    @pytest.fixture
    def scripts_dir(self, tmp_path: Path) -> Path:
        """Stub pipeline scripts — path-composed only, NEVER executed
        (the spawn seam is mocked); they document the argv contract."""
        sd = tmp_path / "scripts" / "upgrade"
        sd.mkdir(parents=True)
        for name in ("restart.sh", "promote.sh"):
            (sd / name).write_text(
                "#!/usr/bin/env bash\n# fixture stub — never executed (spawn seam mocked)\n",
                encoding="utf-8",
            )
        return sd

    def _arm(
        self, mgr, install: Path, scripts_dir: Path, spec: dict
    ) -> str:
        """Plant a marker mirroring the REAL arm-path spec shape
        (daemon/tools/upgrade_tools.py _set_execution_marker payloads)."""
        iid = spec.pop("instance_id")
        mgr._pending_system_executions[iid] = {
            "install_dir": str(install),
            "scripts_dir": str(scripts_dir),
            "port": 7979,
            **spec,
        }
        return iid

    async def test_restart_kind_argv_lock_adoption_and_owner_flip(
        self, drain_mgr, install, scripts_dir, spawn_calls
    ) -> None:
        run_id = "r-drain-restart-1"
        uj.lock_acquire(install, run_id)  # the tool-acquired arm lock
        uj.write_pending_op(
            install,
            uj.PendingOp(
                run_id=run_id, kind="restart", env="demo", reason="nightly",
                armed_by_instance="inst-drain-1",
            ),
        )
        iid = self._arm(
            drain_mgr, install, scripts_dir,
            {"instance_id": "inst-drain-1", "kind": "restart", "env": "demo",
             "run_id": run_id, "target": "1.2.2", "reason": "nightly"},
        )
        assert await drain_mgr.drain_pending_system_execution(iid) is True

        [call] = spawn_calls
        assert call["argv"] == [
            "bash", str(scripts_dir / "restart.sh"), "demo",
            "--run-id", run_id, "--reason", "nightly",
        ]
        assert call["install"] == install
        assert call["extra_env"]["INSTALL_DIR"] == str(install)
        assert call["extra_env"]["PORT"] == "7979"
        # Adoption: the arm lock is NOT released — restart.sh owns it from
        # here (its own preflight closes the txn + pending_op).
        assert call["lock_held_at_spawn"] is True
        assert uj.lock_dir(install).exists()
        # One shot: the marker is consumed.
        assert iid not in drain_mgr._pending_system_executions
        # The journal pending_op flips to the spawned executor's identity.
        op = uj.read_pending_op(install)
        assert op is not None and op.run_id == run_id
        assert op.owner_pid == self.EXECUTOR_PID
        assert op.owner_kind == "executor"
        assert op.trigger == "post-turn-callback"

    async def test_promote_kind_lock_handoff_before_spawn(
        self, drain_mgr, install, scripts_dir, spawn_calls
    ) -> None:
        run_id = "r-drain-promote-1"
        uj.lock_acquire(install, run_id)  # the arm-time lock
        uj.write_pending_op(
            install,
            uj.PendingOp(
                run_id=run_id, kind="promote", env="demo", target="1.2.3",
                armed_by_instance="inst-drain-2",
            ),
        )
        iid = self._arm(
            drain_mgr, install, scripts_dir,
            {"instance_id": "inst-drain-2", "kind": "promote", "env": "demo",
             "run_id": run_id, "target": "1.2.3"},
        )
        assert await drain_mgr.drain_pending_system_execution(iid) is True

        [call] = spawn_calls
        assert call["argv"] == [
            "bash", str(scripts_dir / "promote.sh"), "demo",
            "--version", "1.2.3",
        ]
        # Handoff: the arm-time lock is released BEFORE spawn (observed at
        # spawn time) — promote.sh re-acquires it at its own preflight, so
        # exactly one lock holder exists at any moment.
        assert call["lock_held_at_spawn"] is False
        assert not uj.lock_dir(install).exists()
        assert iid not in drain_mgr._pending_system_executions

    async def test_unknown_kind_refused_no_spawn(
        self, drain_mgr, install, scripts_dir, spawn_calls
    ) -> None:
        uj.lock_acquire(install, "r-drain-unknown")
        iid = self._arm(
            drain_mgr, install, scripts_dir,
            {"instance_id": "inst-drain-3", "kind": "hibernate", "env": "demo",
             "run_id": "r-drain-unknown"},
        )
        assert await drain_mgr.drain_pending_system_execution(iid) is False
        assert spawn_calls == []  # refused — nothing spawned
        assert uj.lock_dir(install).exists()  # journal/lock untouched
        # … and the marker is still consumed (never retried blindly).
        assert iid not in drain_mgr._pending_system_executions

    async def test_no_marker_returns_false(self, drain_mgr) -> None:
        assert await drain_mgr.drain_pending_system_execution("inst-none") is False

    async def test_spawn_extra_env_composes_to_allowlist_only(
        self, drain_mgr, install, scripts_dir, spawn_calls, monkeypatch
    ) -> None:
        """Bridge from drain to the REAL executor env: the extra_env the
        drain passes is exactly what spawn_executor merges over the
        allowlist — poison the ambient env and confirm the composition a
        real child would receive excludes every non-allowlisted var."""
        monkeypatch.setenv("ENSEMBLE_UPGRADE_LIVE", "1")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-do-not-leak")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-do-not-leak")
        iid = self._arm(
            drain_mgr, install, scripts_dir,
            {"instance_id": "inst-drain-4", "kind": "restart", "env": "demo",
             "run_id": "r-drain-env-1", "reason": "env"},
        )
        assert await drain_mgr.drain_pending_system_execution(iid) is True
        [call] = spawn_calls
        composed = uj.executor_env(call["extra_env"])
        assert "ENSEMBLE_UPGRADE_LIVE" not in composed
        assert "OPENAI_API_KEY" not in composed
        assert "ANTHROPIC_API_KEY" not in composed
        assert composed["INSTALL_DIR"] == str(install)
        assert composed["PORT"] == "7979"
        allowed = (
            set(uj.EXECUTOR_ENV_ALLOWLIST)
            | {"INSTALL_DIR", "PORT"}
            | {k for k in os.environ if k.startswith(uj.EXECUTOR_ENV_PREFIXES)}
        )
        assert set(composed) <= allowed
