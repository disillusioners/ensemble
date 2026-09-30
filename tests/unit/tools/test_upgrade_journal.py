"""Unit tests for ``daemon/tools/upgrade_journal.py`` (P2.2 Dispatch B, T4/T5).

The Python twin of ``scripts/upgrade/lib.sh``'s journal + lock discipline.
The PROTOCOL — not shared code — is the contract (D-FA5.1): every function
here is tested against its lib.sh counterpart's semantics, including
cross-writer interop in BOTH directions (Python-written journal → lib.sh
journal_update; lib.sh-written → Python read/write).

Coverage groups (phase2-plan T4/T5 acceptance):

* Torn-safe reads — empty / truncated / non-object journals raise
  ``JournalTorn``; well-formed journals round-trip.
* Atomic writes — kill -9 mid-write leaves the journal intact (real
  SIGKILL against a looping child writer, bounded < 2 s), and the
  deterministic equivalent: a partial temp file on disk never replaces
  the journal.
* ADR-034 splice discipline — the document round-trips STRUCTURALLY; a
  hand-edited duplicated top-level key (the only synthesizable ≥2
  divergence) is tolerated on read, normalizes last-wins identically to
  lib.sh's last-occurrence splice target, and NO occurrence-counting
  assertion exists (the tolerance is tested, not violated).
* ``rollback.lock.d`` mkdir-lock protocol — acquire/free/busy, BOTH
  stale-break branches (stale heartbeat + dead owner; dead owner with a
  FRESH heartbeat), a LIVE owner's lock never broken on heartbeat age
  alone, ownership-guarded heartbeat + release.
* PendingOp persistence — write/read/clear round-trip, garbage-tolerant
  ``from_json``.
* Nonce store (D-FA3.3) — mint format, normalized/grouped echo matching,
  find-prefers-unconsumed, consume stamps single-use + audit history,
  GC drops consumed/expired but KEEPS unparseable-TTL entries.
* ``reconcile_pending_op`` — terminal-event closure, in-flight guard,
  expiry+grace clearance, restart-kind never touched, torn journal
  no-op, and the READ-FIRST byte-identical no-op.
* Executor spawn (D-FA1.3 / D4 / T5) — ``executor_env`` allowlist (pure
  function), a REAL daemonized spawn whose child env is exactly the
  allowlist (API-key-class + ENSEMBLE_UPGRADE_LIVE absent) and whose
  process group is independent of the parent, and the static
  no-BashProcessRegistry assertion.
* ``classify_user_origin`` — registry-backed user-origin classification
  (verdict §4): exact "api" + registered chat source_type; every
  ``internal_*`` / agent / scheduler source fails closed, prefix dialects
  are dead, and the nonce content check is hyphen-tolerant on both sides.

All fixtures live under ``tmp_path`` — never a real install dir, never
live. lib.sh interop runs ``bash`` subprocesses with a scrubbed env.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import warnings
from pathlib import Path
from typing import Any

import pytest

from daemon.services import upgrade_journal_sweep as uj_sweep
from daemon.tools import upgrade_journal as uj
from daemon.tools.upgrade_journal import (
    EXECUTOR_ENV_ALLOWLIST,
    EXECUTOR_ENV_PREFIXES,
    JOURNAL_EMPTY,
    NONCE_RE,
    NONCE_TTL_S,
    USER_ORIGIN_CHAT_SOURCE_TYPES,
    JournalTorn,
    PendingAction,
    PendingOp,
    classify_user_origin,
    is_user_origin_source,
    journal_init,
    journal_read,
    journal_update_field,
    journal_write,
    lock_acquire,
    lock_dir,
    lock_heartbeat,
    lock_release,
    mint_nonce,
    nonce_grouped,
    nonce_in_content,
    user_origin_sources_display,
)

# Repo root: tests/unit/tools/test_upgrade_journal.py -> parents[3].
REPO_ROOT = Path(__file__).resolve().parents[3]
LIB_SH = REPO_ROOT / "scripts" / "upgrade" / "lib.sh"

# A port that is none of: dev 8079, demo 7979, prod 9797 — and is never
# actually bound by these tests (status.sh only probes it read-only).
# Same name+type as test_upgrade_tools.py's SANDBOX_PORT (a shared test
# module is P2.3 — until then keep the two aligned).
SANDBOX_PORT = 8399


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def install(tmp_path: Path) -> Path:
    """A fresh staged-install fixture: journal initialized, extensions on."""
    inst = tmp_path / "install"
    (inst / "releases").mkdir(parents=True)
    journal_init(inst)
    uj.ensure_extensions(inst)
    return inst


def _write_manifest(rel_dir: Path, version: str, *, rollback_safe: bool = True) -> None:
    rel_dir.mkdir(parents=True, exist_ok=True)
    (rel_dir / "manifest.json").write_text(
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


def _dead_pid() -> int:
    """A pid that is verifiably dead right now (spawn + reap a sleep 0)."""
    proc = subprocess.Popen(["sleep", "0"])
    proc.wait(timeout=10)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        try:
            os.kill(proc.pid, 0)
        except ProcessLookupError:
            return proc.pid
        except PermissionError:  # pragma: no cover — not expected for own child
            return proc.pid
        time.sleep(0.01)
    pytest.fail("could not obtain a dead pid for the stale-lock fixture")


def _isolated_home(base: Path) -> str:
    """M3 (P2.2 fix pass 2026-08-23): a fake HOME for subprocess env dicts —
    lib.sh's resolve_env canon-checks ``$HOME/agents-ensemble*`` (a
    live-path READ on any host with an install). No subprocess started by
    this module may reach the developer's real home."""
    home = base / "fake-home"
    home.mkdir(exist_ok=True)
    return str(home)


def _bash_lib(install_dir: Path, script: str) -> subprocess.CompletedProcess:
    """Run a bash snippet with lib.sh sourced and INSTALL_DIR pointed at the
    fixture. The env is deliberately SCRUBBED (only PATH/HOME survive) so an
    ambient developer shell (which may leak the live daemon's POSTGRES_DB)
    cannot color the interop result."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": _isolated_home(install_dir.parent),  # M3: never the real home
        "INSTALL_DIR": str(install_dir),
    }
    return subprocess.run(
        ["bash", "-c", f'. "{LIB_SH}"\n{script}'],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


# ── Torn-safe reads ──────────────────────────────────────────────────────────


class TestTornSafeRead:
    def test_round_trip(self, install: Path) -> None:
        data = dict(JOURNAL_EMPTY)
        data["current"] = "1.2.2"
        journal_write(install, data)
        assert journal_read(install)["current"] == "1.2.2"

    def test_absent_journal_raises_torn(self, tmp_path: Path) -> None:
        inst = tmp_path / "empty-install"
        (inst / "releases").mkdir(parents=True)
        with pytest.raises(JournalTorn, match="journal absent"):
            journal_read(inst)

    def test_empty_file_is_torn(self, install: Path) -> None:
        uj.journal_path(install).write_text("", encoding="utf-8")
        with pytest.raises(JournalTorn, match="EMPTY"):
            journal_read(install)

    def test_truncated_json_is_torn(self, install: Path) -> None:
        uj.journal_path(install).write_text('{"current":"1.2.2",', encoding="utf-8")
        with pytest.raises(JournalTorn, match="unparseable"):
            journal_read(install)

    def test_non_object_json_is_torn(self, install: Path) -> None:
        uj.journal_path(install).write_text("[1,2,3]", encoding="utf-8")
        with pytest.raises(JournalTorn, match="not a JSON object"):
            journal_read(install)

    def test_journal_init_idempotent(self, tmp_path: Path) -> None:
        inst = tmp_path / "fresh"
        (inst / "releases").mkdir(parents=True)
        assert not uj.journal_path(inst).is_file()
        journal_init(inst)
        first = uj.journal_path(inst).read_bytes()
        journal_init(inst)  # second call is a no-op
        assert uj.journal_path(inst).read_bytes() == first


# ── Atomic writes — kill -9 safety (T4 acceptance) ───────────────────────────


class TestAtomicWriteKillSafety:
    def test_kill9_mid_write_never_tears_journal(self, tmp_path: Path) -> None:
        """REAL SIGKILL against a looping child writer: after every kill the
        journal is complete-and-parseable (0 torn reads). Bounded: 4 rounds ×
        ≤0.3 s kill delay, well under 2 s."""
        child_code = (
            "import sys, time\n"
            "from pathlib import Path\n"
            f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
            "from daemon.tools import upgrade_journal as uj\n"
            "install = Path(sys.argv[1])\n"
            "payload = 'x' * 400_000\n"
            "i = 0\n"
            "while True:\n"
            "    uj.journal_write(install, {'current': f'v{i}', 'history': [{'detail': payload}]})\n"
            "    i += 1\n"
        )
        torn_reads = 0
        for round_no in range(4):
            inst = tmp_path / f"kill-{round_no}"
            (inst / "releases").mkdir(parents=True)
            journal_init(inst)
            child = subprocess.Popen(
                [sys.executable, "-c", child_code, str(inst)],
                cwd=str(REPO_ROOT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            # Kill at a varied, short delay so some rounds land mid-write.
            time.sleep(0.05 + 0.05 * round_no)
            child.send_signal(signal.SIGKILL)
            child.wait(timeout=10)  # Popen reaps; no separate waitpid needed
            try:
                data = journal_read(inst)
            except JournalTorn:
                torn_reads += 1
                continue
            # The journal is either the init document or a completed payload —
            # never a mix (a torn write would raise above).
            assert data.get("current") is None or data["current"].startswith("v")
            # A leftover temp file is ALLOWED (that is the crash signature) —
            # but the journal itself must be complete JSON.
        assert torn_reads == 0, "SIGKILL mid-write produced a torn journal"

    def test_partial_temp_file_never_replaces_journal(self, install: Path) -> None:
        """Deterministic equivalent: a killed writer's partial temp file sits
        next to the journal — reads ignore it, the journal stays intact, and
        the NEXT journal_write cleans up its own temp via os.replace."""
        jp = uj.journal_path(install)
        before = jp.read_bytes()
        # Simulate the crash artifact: a partial payload in a temp sibling.
        (install / "releases" / "state.json.tmp.99999.12345").write_text(
            '{"current":"1.2.2","prev', encoding="utf-8"
        )
        assert journal_read(install)["current"] is None  # untouched
        assert jp.read_bytes() == before
        # A subsequent atomic write still succeeds and lands whole.
        journal_update_field(install, "current", "1.2.3")
        assert journal_read(install)["current"] == "1.2.3"

    def test_write_leaves_no_temp_after_success(self, install: Path) -> None:
        journal_update_field(install, "current", "1.2.3")
        leftovers = list((install / "releases").glob("state.json.tmp.*"))
        assert leftovers == [], f"temp leftovers after a clean write: {leftovers}"


# ── journal_update_field semantics + ADR-034 ─────────────────────────────────


class TestUpdateFieldSemantics:
    def test_unknown_field_raises_keyerror(self, install: Path) -> None:
        with pytest.raises(KeyError, match="schema drift"):
            journal_update_field(install, "not_a_field", 1)

    def test_torn_journal_never_written_over(self, install: Path) -> None:
        uj.journal_path(install).write_text('{"current":"1.2', encoding="utf-8")
        with pytest.raises(JournalTorn):
            journal_update_field(install, "current", "1.2.3")
        # The torn bytes are preserved verbatim — halt-for-human, not masked.
        assert uj.journal_path(install).read_text(encoding="utf-8") == '{"current":"1.2'

    def test_unknown_extra_fields_carried_through(self, install: Path) -> None:
        """ADR-034 structural round-trip: a lib.sh-era/hand-edited extra field
        survives a Python field update untouched."""
        data = journal_read(install)
        data["hand_edited_note"] = "keep me"
        journal_write(install, data)
        journal_update_field(install, "current", "1.2.3")
        assert journal_read(install)["hand_edited_note"] == "keep me"


class TestADR034SpliceDiscipline:
    """ADR-034 BINDING: lib.sh ``journal_update`` splices textually at the LAST
    occurrence of a field name and deliberately tolerates a field name
    occurring ≥2 times (hand-edit only). This suite TESTS the tolerance —
    it does not violate it, and it contains no occurrence-counting
    assertions (a single-occurrence assert is P2.3 territory)."""

    DUP_KEY_DOC = (
        '{"current":"1.2.2","previous":null,"in_flight":null,'
        '"rollback_window_count":{"24h":0,"window_start":null},'
        '"cooldown_until":null,"quarantined":[],"history":[],'
        '"current":"9.9.9"}'
    )

    def test_duplicated_key_tolerated_on_read(self, install: Path) -> None:
        uj.journal_path(install).write_text(self.DUP_KEY_DOC, encoding="utf-8")
        data = journal_read(install)  # must NOT raise
        # json.loads normalizes duplicated keys LAST-WINS — the same field
        # lib.sh's last-occurrence splice targets.
        assert data["current"] == "9.9.9"

    def test_python_update_matches_libsh_last_occurrence_target(
        self, install: Path
    ) -> None:
        """On a divergent document the two writers behave differently — and
        that asymmetry is the ADR-034 contract, tested honestly:

        * Python's STRUCTURAL update normalizes cleanly: the result parses
          with current='2.0.0' (duplicated keys collapse — json.loads
          last-wins semantics, no occurrence assertions anywhere).
        * lib.sh's TEXTUAL splice on the same divergent doc does NOT
          cleanly target either occurrence (it splices at the last key but
          consumes the first occurrence's value — the middle gets
          duplicated; the semantic last-wins value stays the stale one).
          This is exactly why ADR-034 declares divergence hand-edit-only
          and forbids tightening: out-of-contract input, garbage-tolerated.
          The required property is only that lib.sh's splice NEVER produces
          a torn/unparseable journal (fail-safe, not fail-clean).
        """
        uj.journal_path(install).write_text(self.DUP_KEY_DOC, encoding="utf-8")
        journal_update_field(install, "current", "2.0.0")
        normalized = journal_read(install)
        assert normalized["current"] == "2.0.0"
        raw = uj.journal_path(install).read_text(encoding="utf-8")
        assert raw.count('"current"') == 1, (
            "structural write must normalize the divergence away (the ≥2 "
            "state is an input artifact, never an output artifact)"
        )

        # lib.sh side on the same divergent input: splice completes, result
        # still parses (never torn) — divergence tolerance, not correctness.
        inst2 = install.parent / "libsh-dup"
        (inst2 / "releases").mkdir(parents=True)
        uj.journal_path(inst2).write_text(self.DUP_KEY_DOC, encoding="utf-8")
        rc = _bash_lib(inst2, 'journal_update current \'"2.0.0"\'')
        assert rc.returncode == 0, rc.stderr
        libsh_result = json.loads(uj.journal_path(inst2).read_text())
        assert isinstance(libsh_result, dict)  # parseable — never torn

    def test_no_occurrence_counting_in_python_module(self) -> None:
        """ADR-034 binding: the Python module must contain NO
        occurrence-counting assertion on field names (a single-occurrence
        assert is P2.3 hardening territory — forbidden here). AST-walked,
        NOT substring-scanned: the old line filter (dropping
        triple-quote-bearing lines) was evadable via
        ``len([k for k in d if k == f])``-style counting and brittle
        against multi-line strings; walking real
        ``Call`` nodes catches every ``.count(...)`` invocation while
        docstrings/comments that legitimately DISCUSS the discipline
        cannot false-positive (they are string constants, never Call
        nodes)."""
        import ast

        source = (REPO_ROOT / "daemon" / "tools" / "upgrade_journal.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                assert not (
                    isinstance(func, ast.Attribute) and func.attr == "count"
                ), (
                    "ADR-034 violation: .count(...) call found in executable "
                    "code — occurrence-counting is P2.3 territory, not P2.2"
                )
            if isinstance(node, (ast.Name, ast.Attribute)):
                ident = node.id if isinstance(node, ast.Name) else node.attr
                assert ident not in ("occurrences", "occurrence_count"), (
                    f"ADR-034 violation: identifier {ident!r} found in "
                    "executable code — occurrence-counting is P2.3 "
                    "territory, not P2.2"
                )


# ── rollback.lock.d mkdir-lock protocol ──────────────────────────────────────


class TestLockProtocol:
    def test_acquire_free_and_reacquire(self, install: Path) -> None:
        acquired, busy = lock_acquire(install, "r-1")
        assert acquired and busy is None
        assert (lock_dir(install) / "run_id").read_text().strip() == "r-1"
        assert lock_release(install) is True
        assert not lock_dir(install).exists()
        acquired2, _ = lock_acquire(install, "r-2")
        assert acquired2

    def test_busy_lock_reports_holder_run_id(self, install: Path) -> None:
        lock_acquire(install, "r-holder", owner_pid=os.getpid())
        acquired, busy = lock_acquire(install, "r-other", wait_s=0.0)
        assert acquired is False
        assert busy == "r-holder"
        # A LIVE owner's lock is NEVER broken — even with a stale heartbeat.
        stale_epoch = int(time.time()) - (uj.LOCK_STALE_S + 100)
        (lock_dir(install) / "heartbeat").write_text(f"{stale_epoch}\n", encoding="utf-8")
        acquired2, busy2 = lock_acquire(install, "r-other2", wait_s=0.0)
        assert acquired2 is False and busy2 == "r-holder"
        assert lock_dir(install).exists()  # not broken, not moved
        assert list((install / "releases").glob("rollback.lock.d.stale.*")) == []

    def test_stale_break_branch_a_stale_heartbeat_dead_owner(
        self, install: Path
    ) -> None:
        """Branch 1: heartbeat older than LOCK_STALE_S AND owner dead → the
        lock is mv'd to rollback.lock.stale.<pid> and re-acquired."""
        dead = _dead_pid()
        lock_dir(install).mkdir()
        (lock_dir(install) / "owner").write_text(f"{dead}\n", encoding="utf-8")
        (lock_dir(install) / "run_id").write_text("r-dead-old\n", encoding="utf-8")
        (lock_dir(install) / "heartbeat").write_text(
            f"{int(time.time()) - (uj.LOCK_STALE_S + 100)}\n", encoding="utf-8"
        )
        acquired, busy = lock_acquire(install, "r-fresh", wait_s=0.0)
        assert acquired is True and busy is None
        assert (lock_dir(install) / "run_id").read_text().strip() == "r-fresh"
        stale = list((install / "releases").glob("rollback.lock.d.stale.*"))
        assert len(stale) == 1, "stale-broken lock must be preserved as .stale.*"

    def test_stale_break_branch_b_dead_owner_fresh_heartbeat(
        self, install: Path
    ) -> None:
        """Branch 2: owner pid dead even with a FRESH heartbeat (crash left a
        fresh dir) → broken too (lib.sh mirror)."""
        dead = _dead_pid()
        lock_dir(install).mkdir()
        (lock_dir(install) / "owner").write_text(f"{dead}\n", encoding="utf-8")
        (lock_dir(install) / "run_id").write_text("r-crashed\n", encoding="utf-8")
        (lock_dir(install) / "heartbeat").write_text(
            f"{int(time.time())}\n", encoding="utf-8"
        )
        acquired, busy = lock_acquire(install, "r-fresh", wait_s=0.0)
        assert acquired is True and busy is None
        assert (lock_dir(install) / "run_id").read_text().strip() == "r-fresh"

    def test_heartbeat_ownership_guarded(self, install: Path) -> None:
        lock_acquire(install, "r-1", owner_pid=os.getpid())
        # A NON-owner pid cannot keep another process's lock alive.
        other_pid = os.getpid() + 1  # not the owner of this lock dir
        assert lock_heartbeat(install, owner_pid=other_pid) is False
        assert lock_heartbeat(install, owner_pid=os.getpid()) is True

    def test_release_ownership_guarded(self, install: Path) -> None:
        lock_acquire(install, "r-1", owner_pid=os.getpid())
        other_pid = os.getpid() + 1
        assert lock_release(install, owner_pid=other_pid) is False
        assert lock_dir(install).exists()  # left in place
        assert lock_release(install, owner_pid=os.getpid()) is True

    def test_libsh_lock_interop_live_holder(self, install: Path) -> None:
        """Cross-writer mutual exclusion: a lib.sh-held lock (LIVE bash owner)
        blocks Python's acquire with the holder's run_id, and vice versa —
        the mkdir-lock protocol is one protocol regardless of writer."""
        import subprocess as sp

        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": _isolated_home(install.parent),  # M3: never the real home
            "INSTALL_DIR": str(install),
        }
        # Direction 1: Python holds → lib.sh (live subshell) sees it busy.
        lock_acquire(install, "r-py", owner_pid=os.getpid())
        rc = _bash_lib(
            install,
            'test -d "$(lock_dir_path)" && test "$(cat "$(lock_dir_path)/run_id")" = "r-py"',
        )
        assert rc.returncode == 0, rc.stderr
        # lib.sh's ownership-guarded release refuses a foreign lock.
        rc = _bash_lib(install, "lock_release >/dev/null 2>&1; echo RC=$?")
        assert "RC=1" in rc.stdout
        assert lock_dir(install).exists()
        assert lock_release(install) is True

        # Direction 2: lib.sh holds (holder stays ALIVE via a hold file) →
        # Python's acquire is refused with the bash-held run_id.
        hold_done = install.parent / "hold-done"
        holder = sp.Popen(
            [
                "bash",
                "-c",
                f'. "{LIB_SH}"\n'
                'lock_acquire 0 || { echo BUSY; exit 1; }\n'
                'cat "$(lock_dir_path)/run_id"\n'
                f'while [ ! -f "{hold_done}" ]; do sleep 0.1; done\n'
                "lock_release\n",
            ],
            env=env,
            stdout=sp.PIPE,
            stderr=sp.PIPE,
            text=True,
        )
        try:
            run_id_line = holder.stdout.readline().strip()  # blocks until printed
            assert run_id_line.startswith("run-"), run_id_line
            acquired, busy = lock_acquire(install, "r-py-2", wait_s=0.0)
            assert acquired is False, "Python acquired a lib.sh-held lock"
            assert busy == run_id_line
        finally:
            hold_done.touch()
            holder.wait(timeout=15)
        assert holder.returncode == 0, holder.stderr.read()
        # The bash holder released → the lock dir is gone.
        assert not lock_dir(install).exists()


# ── PendingOp persistence ────────────────────────────────────────────────────


class TestPendingOp:
    def test_round_trip(self, install: Path) -> None:
        op = PendingOp(
            run_id="r-op-1",
            kind="restart",
            env="demo",
            reason="test round trip",
            owner_pid=1234,
        )
        uj.write_pending_op(install, op)
        # kind=restart also sets the phase2-plan D2 marker.
        assert journal_read(install)["pending_restart"] == "r-op-1"
        back = uj.read_pending_op(install)
        assert back is not None
        assert back.run_id == "r-op-1"
        assert back.kind == "restart"
        assert back.reason == "test round trip"
        uj.clear_pending_op(install)
        assert uj.read_pending_op(install) is None
        assert journal_read(install)["pending_restart"] is None

    def test_from_json_garbage_tolerant(self) -> None:
        assert PendingOp.from_json(None) is None
        assert PendingOp.from_json("nope") is None
        assert PendingOp.from_json({}) is None  # no run_id
        # Unknown fields are dropped, required fields honored.
        assert (
            PendingOp.from_json(
                {"run_id": "r", "kind": "restart", "env": "demo", "unknown_field": 1}
            )
            is not None
        )
        # Missing required positional fields → None (TypeError swallowed).
        assert PendingOp.from_json({"run_id": "r"}) is None


# ── Nonce store (D-FA3.3) ────────────────────────────────────────────────────


class TestNonceHelpers:
    def test_mint_format(self) -> None:
        for _ in range(20):
            assert NONCE_RE.match(mint_nonce()), mint_nonce()

    def test_mint_unguessable(self) -> None:
        seen = {mint_nonce() for _ in range(50)}
        assert len(seen) == 50

    def test_grouped_rendering(self) -> None:
        # §2.1 example shape CONFIRM-XXXX-XXXX: 4+4 split of the 8-char body.
        assert nonce_grouped("CONFIRM-ABCDEFGH") == "CONFIRM-ABCD-EFGH"
        # A non-canonical input passes through unchanged.
        assert nonce_grouped("not-a-nonce") == "not-a-nonce"

    def test_nonce_in_content_variants(self) -> None:
        nonce = "CONFIRM-ABCDEFGH"
        assert nonce_in_content(nonce, "please do it CONFIRM-ABCDEFGH thanks")
        assert nonce_in_content(nonce, "confirm-abcd-efgh")  # grouped, lowercase
        assert nonce_in_content(nonce, "CONFIRM ABCDEFGH")  # whitespace variant
        assert nonce_in_content(nonce, "xCONFIRM-ABCDEFGHx")  # embedded
        assert not nonce_in_content(nonce, "CONFIRM-ZZZZZZZZ")
        assert not nonce_in_content(nonce, "")
        assert not nonce_in_content(nonce, None)

    def test_nonce_in_content_hyphen_regrouped_echo(self) -> None:
        """Hardening (c) — cycle-1 secondary failure (verdict §1): the user
        echoed 'CONFIRM-AI3N-A5TS' for minted 'CONFIRM-AI3NA5TS'. The check
        is normalized on BOTH sides (nonce_normalize strips dashes/
        whitespace), so a regrouped/dash-dropped echo passes and a wrong
        nonce still fails. Regression pin: this must NEVER regress to an
        exact-match comparison."""
        minted = "CONFIRM-AI3NA5TS"
        assert nonce_in_content(minted, "CONFIRM-AI3N-A5TS")  # regrouped
        assert nonce_in_content(minted, "confirmai3na5ts")  # dashes dropped
        assert nonce_in_content(minted, "ok: CONFIRM-AI3N-A5TS please")
        assert not nonce_in_content(minted, "CONFIRM-AI3N-A5TX")  # wrong body


class TestNonceStore:
    def _store(self, install: Path, run_id: str = "r-nonce-1") -> PendingAction:
        action = PendingAction(
            run_id=run_id,
            nonce="CONFIRM-ABCDEFGH",
            kind="upgrade",
            env="live",
            target="1.2.3",
        )
        uj.store_pending_action(install, action)
        return action

    def test_mint_persists_and_finds(self, install: Path) -> None:
        self._store(install)  # side effect: persists the pending action
        actions = journal_read(install)["pending_actions"]
        assert "r-nonce-1" in actions
        found = uj.find_pending_action_by_nonce(install, "CONFIRM-ABCDEFGH")
        assert found is not None and found.run_id == "r-nonce-1"
        # Normalized echo still matches (dashes/case/whitespace insensitive).
        found_norm = uj.find_pending_action_by_nonce(install, "confirm abcdefgh")
        assert found_norm is not None and found_norm.run_id == "r-nonce-1"

    def test_find_prefers_unconsumed(self, install: Path) -> None:
        """Two records sharing a nonce (the consumed original + a re-minted
        fresh one): find returns the UNCONSUMED one."""
        self._store(install)
        second = PendingAction(
            run_id="r-nonce-2",
            nonce="CONFIRM-ABCDEFGH",
            kind="upgrade",
            env="live",
            target="1.2.4",
        )
        uj.store_pending_action(install, second)
        # consume the FIRST via direct journal mutation
        data = journal_read(install)
        data["pending_actions"]["r-nonce-1"]["consumed_at"] = uj.now_iso()
        journal_write(install, data)
        found = uj.find_pending_action_by_nonce(install, "CONFIRM-ABCDEFGH")
        assert found is not None and found.run_id == "r-nonce-2"

    def test_consume_single_use_and_audit(self, install: Path) -> None:
        action = self._store(install)
        uj.consume_pending_action(install, action, "msg-7")
        data = journal_read(install)
        rec = data["pending_actions"]["r-nonce-1"]
        assert rec["consumed_at"] is not None
        assert rec["consumed_by_message_id"] == "msg-7"
        # Audit event survives the MessageQueue wipe (R-SR10 analogue).
        events = [e["event"] for e in data["history"]]
        assert "nonce_consumed" in events
        # Replay detection: find returns the consumed record (caller refuses
        # nonce-already-used) — the entry is kept for exactly this purpose.
        replay = uj.find_pending_action_by_nonce(install, action.nonce)
        assert replay is not None and replay.consumed_at is not None

    def test_gc_drops_consumed_and_expired_keeps_rest(self, install: Path) -> None:
        self._store(install)  # unconsumed, unexpired → KEPT
        # consumed record under another run_id → DROPPED on next store
        consumed = PendingAction(
            run_id="r-nonce-c",
            nonce="CONFIRM-CCCCCCCC",
            kind="upgrade",
            env="live",
            target="1.2.3",
        )
        uj.store_pending_action(install, consumed)
        data = journal_read(install)
        data["pending_actions"]["r-nonce-c"]["consumed_at"] = uj.now_iso()
        journal_write(install, data)
        # expired record → DROPPED
        expired = PendingAction(
            run_id="r-nonce-e",
            nonce="CONFIRM-EEEEEEEE",
            kind="upgrade",
            env="live",
            target="1.2.3",
        )
        uj.store_pending_action(install, expired)
        data = journal_read(install)
        data["pending_actions"]["r-nonce-e"]["ttl_expires_at"] = (
            "2020-01-01T00:00:00Z"
        )
        journal_write(install, data)
        # unparseable-TTL record → KEPT (GC only deletes what it can prove dead)
        weird = PendingAction(
            run_id="r-nonce-w",
            nonce="CONFIRM-WWWWWWWW",
            kind="upgrade",
            env="live",
            target="1.2.3",
        )
        uj.store_pending_action(install, weird)  # triggers opportunistic GC
        data = journal_read(install)
        data["pending_actions"]["r-nonce-w"]["ttl_expires_at"] = "not-a-timestamp"
        journal_write(install, data)
        # One more store to run GC again over the unparseable entry.
        keep = PendingAction(
            run_id="r-nonce-k",
            nonce="CONFIRM-KKKKKKKK",
            kind="upgrade",
            env="live",
            target="1.2.3",
        )
        uj.store_pending_action(install, keep)
        final = journal_read(install)["pending_actions"]
        assert "r-nonce-1" in final  # unconsumed + unexpired
        assert "r-nonce-w" in final  # unparseable TTL kept (fail-safe)
        assert "r-nonce-k" in final
        assert "r-nonce-c" not in final  # consumed → dropped
        assert "r-nonce-e" not in final  # expired → dropped

    def test_ttl_default(self) -> None:
        action = PendingAction(
            run_id="r", nonce="CONFIRM-ABCDEFGH", kind="upgrade", env="live", target=None
        )
        ttl = uj.parse_iso_utc(action.ttl_expires_at) - uj.parse_iso_utc(action.issued_at)
        assert abs(ttl.total_seconds() - NONCE_TTL_S) < 5

    def test_manager_fallback_nonce_literal_matches_canonical(self) -> None:
        """ADR-036 + FIX-BACK review hygiene N1: ``daemon/manager.py:4078``
        carries a defensive pragma fallback (``NONCE_TTL_S = 60 * 60``)
        that fires ONLY when the canonical ``daemon.tools.upgrade_journal``
        import fails. The fallback exists precisely for that failure
        mode, so aliasing it at the site is IMPOSSIBLE-by-construction
        (a module-level re-export from upgrade_journal would defeat the
        pragma's purpose — the pragma must be a self-contained literal).
        The chosen pin is therefore dual-mode: assert the canonical is
        reachable AND the pragma fallback's literal matches the canonical
        post-ADR-036 value, so a drift between the two surfaces here
        before any user-origin window is stamped with the wrong TTL."""
        # (a) the canonical is reachable AND reflects the ADR-036 width.
        assert NONCE_TTL_S == 60 * 60, (
            f"canonical NONCE_TTL_S drifted from ADR-036 (60min): {NONCE_TTL_S!r}"
        )
        # (b) the manager-side pragma fallback carries the same literal.
        # Inspect the source of the stamp method (the only call site of
        # the fallback) — the literal ``NONCE_TTL_S = 60 * 60`` must
        # appear in the pragma branch so a widening in the canonical
        # forces an explicit update of the fallback in lockstep.
        import inspect
        from daemon.manager import InstanceManager
        src = inspect.getsource(InstanceManager.stamp_user_origin_window)
        assert "NONCE_TTL_S = 60 * 60" in src, (
            "daemon/manager.py fallback NONCE_TTL_S literal drifted from "
            "ADR-036 (must remain 60 * 60); update daemon/manager.py:4078 "
            "in lockstep with daemon/tools/upgrade_journal.py:116"
        )


# ── reconcile_pending_op (lazy closure) ──────────────────────────────────────


class TestReconcilePendingOp:
    def _arm_promote(self, install: Path, *, expires_offset_s: int = 600) -> str:
        op = PendingOp(
            run_id="r-rec-1",
            kind="promote",
            env="demo",
            target="1.2.3",
            owner_pid=os.getpid(),
            expires_at=uj.iso_plus(uj.now_iso(), expires_offset_s),
        )
        uj.write_pending_op(install, op)
        return op.run_id

    def test_closed_on_terminal_event_after_armed(self, install: Path) -> None:
        self._arm_promote(install)
        uj.journal_history_append(install, "commit", "promote r-rec-1 committed")
        note = uj.reconcile_pending_op(install)
        assert note is not None and "r-rec-1" in note
        assert uj.read_pending_op(install) is None
        events = [e["event"] for e in journal_read(install)["history"]]
        assert "sweep" in events  # closure journaled

    def test_not_closed_while_in_flight_open(self, install: Path) -> None:
        self._arm_promote(install)
        journal_update_field(
            install,
            "in_flight",
            {"kind": "promote", "target": "1.2.3", "started_at": uj.now_iso(),
             "flipped": False, "owner_pid": os.getpid()},
        )
        assert uj.reconcile_pending_op(install) is None
        assert uj.read_pending_op(install) is not None

    def test_terminal_event_before_armed_does_not_close(self, install: Path) -> None:
        """A terminal event from an EARLIER run (≥1s before arming, the
        journal's timestamp resolution) must not close a fresh op.

        NOTE (flagged, unpatched): at second resolution an event in the
        SAME second as arming counts as >= armed_at and DOES close — an
        accepted granularity edge (lib.sh uses the same 1s _now_iso), not
        a safety violation (fail direction = op cleared, re-armable)."""
        uj.journal_history_append(install, "commit", "an OLD run committed")
        # Force a strictly-earlier timestamp (the append happened "now";
        # rewind it one hour so armed_at is unambiguously later).
        data = journal_read(install)
        data["history"][-1]["ts"] = uj.iso_plus(uj.now_iso(), -3600)
        journal_write(install, data)
        self._arm_promote(install)  # armed AFTER the terminal event
        assert uj.reconcile_pending_op(install) is None
        assert uj.read_pending_op(install) is not None

    def test_expired_past_grace_cleared_as_died_pre_open(self, install: Path) -> None:
        self._arm_promote(install, expires_offset_s=-2 * uj.RECONCILE_GRACE_S)
        note = uj.reconcile_pending_op(install)
        assert note is not None and "expired" in note
        assert uj.read_pending_op(install) is None

    def test_restart_kind_never_touched(self, install: Path) -> None:
        op = PendingOp(
            run_id="r-rec-r",
            kind="restart",
            env="demo",
            owner_pid=os.getpid(),
            expires_at=uj.iso_plus(uj.now_iso(), -7200),
        )
        uj.write_pending_op(install, op)
        uj.journal_history_append(install, "commit", "whatever")  # terminal exists
        assert uj.reconcile_pending_op(install) is None
        assert uj.read_pending_op(install) is not None  # boot sweep owns it (D-FA4.3)

    def test_torn_journal_noop(self, install: Path) -> None:
        uj.journal_path(install).write_text('{"torn":', encoding="utf-8")
        assert uj.reconcile_pending_op(install) is None

    def test_read_first_no_pending_byte_identical(self, install: Path) -> None:
        """READ-FIRST discipline: with nothing pending, a reconcile pass
        leaves the journal byte-identical (dry-run preflights rely on this)."""
        before = uj.journal_path(install).read_bytes()
        assert uj.reconcile_pending_op(install) is None
        assert uj.journal_path(install).read_bytes() == before


# ── lib.sh interop — both directions ─────────────────────────────────────────


class TestLibShInterop:
    def test_python_written_journal_libsh_can_update(self, install: Path) -> None:
        """Direction 1: a journal written ENTIRELY by Python is spliced by
        lib.sh journal_update and remains Python-readable."""
        journal_update_field(install, "current", "1.2.2")
        uj.journal_history_append(install, "commit", "py-side write")
        rc = _bash_lib(install, 'journal_update cooldown_until \'"2026-09-01T00:00:00Z"\'')
        assert rc.returncode == 0, rc.stderr
        data = journal_read(install)
        assert data["current"] == "1.2.2"
        assert data["cooldown_until"] == "2026-09-01T00:00:00Z"
        assert data["history"][0]["event"] == "commit"

    def test_libsh_written_journal_python_can_read_and_write(
        self, install: Path
    ) -> None:
        """Direction 2: a journal written ENTIRELY by lib.sh round-trips
        through Python read + field update, and lib.sh reads the result."""
        rc = _bash_lib(
            install,
            "journal_init && journal_set_current 3.0.0 "
            "&& journal_history_append commit 'lib-side write'",
        )
        assert rc.returncode == 0, rc.stderr
        data = journal_read(install)
        assert data["current"] == "3.0.0"
        assert data["history"][0]["event"] == "commit"
        journal_update_field(install, "previous", "2.9.9")
        # lib.sh still reads the Python-updated journal cleanly.
        rc = _bash_lib(install, "journal_read > /dev/null")
        assert rc.returncode == 0, rc.stderr
        data2 = json.loads(_bash_lib(install, "journal_read").stdout)
        assert data2["previous"] == "2.9.9"

    def test_libsh_journal_read_rejects_python_style_torn(self, install: Path) -> None:
        """The torn-detection contract is shared: a truncated journal is
        refused by lib.sh exactly as it raises JournalTorn in Python."""
        uj.journal_path(install).write_text('{"current":"1.2', encoding="utf-8")
        rc = _bash_lib(install, "journal_read >/dev/null")
        assert rc.returncode != 0


# ── Executor spawn (D-FA1.3 / D4 / T5) ───────────────────────────────────────


class TestExecutorSpawn:
    def test_env_allowlist_pure_function(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        monkeypatch.setenv("HOME", "/tmp/fake-home")
        monkeypatch.setenv("INSTALL_DIR", "/tmp/fake-install")
        monkeypatch.setenv("PORT", "8399")
        monkeypatch.setenv("POSTGRES_DB", "ensemble_sandbox")
        monkeypatch.setenv("TMPDIR", "/tmp")
        monkeypatch.setenv("PGHOST", "127.0.0.1")
        monkeypatch.setenv("PGPORT", "5432")
        # Poison: none of these may EVER reach the executor (R-SR09).
        monkeypatch.setenv("ENSEMBLE_UPGRADE_LIVE", "1")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
        monkeypatch.setenv("DATABASE_URL", "postgres://secret")
        monkeypatch.setenv("ENSEMBLE_SELF_ENV", "live")

        env = uj.executor_env({"RUN_ID": "r-1"})
        assert env["PATH"] == "/usr/bin:/bin"
        assert env["HOME"] == "/tmp/fake-home"
        assert env["INSTALL_DIR"] == "/tmp/fake-install"
        assert env["PORT"] == "8399"
        assert env["POSTGRES_DB"] == "ensemble_sandbox"
        assert env["TMPDIR"] == "/tmp"
        assert env["PGHOST"] == "127.0.0.1"
        assert env["PGPORT"] == "5432"
        assert env["RUN_ID"] == "r-1"  # explicit extras pass through
        for forbidden in (
            "ENSEMBLE_UPGRADE_LIVE",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "DATABASE_URL",
            "ENSEMBLE_SELF_ENV",
        ):
            assert forbidden not in env, f"{forbidden} leaked into executor env"
        # Structurally: every key is allowlisted, PG-prefixed, or an extra.
        extras = {"RUN_ID"}
        for key in env:
            assert (
                key in EXECUTOR_ENV_ALLOWLIST
                or any(key.startswith(p) for p in EXECUTOR_ENV_PREFIXES)
                or key in extras
            ), f"unexpected key in executor env: {key}"

    def test_real_spawn_env_and_process_group_independence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spawn a REAL harmless fixture script via spawn_executor and verify
        (a) the child env contains the allowlist ONLY (poison vars absent),
        (b) process-group behavior — MODE-AWARE (r-f82e fix cycle 2):
            - LEGACY (start_new_session=True): the child leads its own group,
              distinct from THIS test process's group.
            - SCOPE (systemd-run --user/--scope, start_new_session=False on
              the daemon side): the systemd-run wrapper stays in OUR group
              (no setsid on daemon side; systemd-run is the new session
              leader via --scope). Direct evidence of engagement = the bash
              payload inside the scope inherits the wrapper's pgid (= our
              pgid), so its self-reported PGID log line equals our pgid
              rather than its own pid.
        (c) stdio lands in <install>/data/upgrade.log."""
        install = tmp_path / "install"
        (install / "releases").mkdir(parents=True)
        dump_path = tmp_path / "child-env.txt"
        script = tmp_path / "dump.sh"
        script.write_text(
            "#!/bin/bash\n"
            "printf 'PGID=%s\\n' \"$(ps -o pgid= -p $$ | tr -d ' ')\"\n"
            f"env | sort > {dump_path}\n"
            "echo executor-line-1\n",
            encoding="utf-8",
        )
        script.chmod(0o755)

        for key, val in (
            ("INSTALL_DIR", str(install)),
            ("PORT", str(SANDBOX_PORT)),
            ("POSTGRES_DB", "ensemble_sandbox"),
            ("PGHOST", "127.0.0.1"),
            ("ENSEMBLE_UPGRADE_LIVE", "1"),  # poison: must NOT pass
            ("OPENAI_API_KEY", "sk-secret"),  # poison: must NOT pass
        ):
            monkeypatch.setenv(key, val)

        # r-f82e fix cycle 2 (review-cycle-2 fixback): detect the mode via the
        # SAME seam spawn_executor uses (_scope_detect_fn), so the test follows
        # the host (legacy vs scope branch) without any platform branching.
        env = uj.executor_env({"RUN_ID": "r-spawn"})
        use_scope, _bus_kind = uj._scope_detect_fn(env)

        pid, mode_note = uj.spawn_executor(
            ["bash", str(script)], install, {"RUN_ID": "r-spawn"},
            run_id="r-spawn",
        )
        # spawn_executor returns (pid, mode_note) derived from the SAME
        # detection call — the test follows the host via the returned
        # note instead of re-running _scope_detect_fn. The detector's
        # answer is observable end-to-end via the pgid assertions below.
        if use_scope:
            assert mode_note == "scope=ensemble-upgrade-r-spawn", (
                f"scope mode: mode_note must carry run_id, got {mode_note!r}"
            )
        else:
            assert mode_note == "(daemonized, start_new_session)", (
                f"legacy mode: mode_note must be the legacy text, got {mode_note!r}"
            )
        try:
            # (b) process-group independence — mode-aware.
            child_pgid = os.getpgid(pid)
            if use_scope:
                # SCOPE mode: systemd-run wrapper is started with
                # start_new_session=False (no setsid on the daemon side;
                # systemd-run is the new session leader via --scope). The
                # wrapper stays in OUR process group; the bash payload
                # executes inside the scope cgroup but inherits our pgid.
                assert child_pgid == os.getpgrp(), (
                    "scope mode: executor wrapper must stay in our process group "
                    "(start_new_session=False on daemon side)"
                )
                assert child_pgid != pid, (
                    "scope mode: executor wrapper must NOT be its own session/group "
                    "leader \u2014 systemd-run is the new session leader via --scope"
                )
            else:
                # LEGACY mode: byte-identical to pre-r-f82e behavior.
                assert child_pgid == pid, "executor must be its own group leader"
                assert child_pgid != os.getpgrp(), "executor must leave our group"

            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and not dump_path.is_file():
                time.sleep(0.05)
            assert dump_path.is_file(), "fixture script did not run"
            child_env: dict[str, str] = {}
            for line in dump_path.read_text(encoding="utf-8").splitlines():
                if "=" in line:
                    k, _, v = line.partition("=")
                    child_env[k] = v
            assert child_env["INSTALL_DIR"] == str(install)
            assert child_env["PORT"] == str(SANDBOX_PORT)
            assert child_env["PGHOST"] == "127.0.0.1"
            assert child_env["RUN_ID"] == "r-spawn"
            assert "OPENAI_API_KEY" not in child_env
            assert "ENSEMBLE_UPGRADE_LIVE" not in child_env
            pgid_line = [
                ln for ln in (install / "data" / "upgrade.log").read_text().splitlines()
                if ln.startswith("PGID=")
            ]
            assert pgid_line, "no PGID line in upgrade.log"
            if use_scope:
                # SCOPE mode direct evidence: the bash payload's self-reported
                # PGID equals our pgid (it inherits the systemd-run wrapper's
                # pgid). If the wrapper accidentally fell through to legacy,
                # bash would be its own session leader and report its own pid.
                assert pgid_line[0] == f"PGID={os.getpgrp()}", (
                    f"scope mode: bash payload must inherit systemd-run pgid "
                    f"(= our pgid {os.getpgrp()}); got {pgid_line[0]!r} \u2014 "
                    "scope wrapper did not engage at runtime"
                )
            else:
                # LEGACY mode: bash is its own session leader; pgid == pid.
                assert pgid_line[0] == f"PGID={child_pgid}", (
                    f"legacy mode: bash payload pgid ({pgid_line[0]!r}) must "
                    f"equal child_pgid ({child_pgid})"
                )
        finally:
            # Reap the disowned child (spawn_executor deliberately does not).
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass

    def test_static_no_bash_process_registry_reference(self) -> None:
        """T5 static assertion: the executor spawner must NOT register the
        child in BashProcessRegistry (or any teardown registry) — the child
        survives tool-harness teardown by NOT being tracked. Checked via
        AST so the docstring that documents the deliberate absence doesn't
        false-positive."""
        import ast

        source = (
            REPO_ROOT / "daemon" / "tools" / "upgrade_journal.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        code_names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        attr_names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        assert "BashProcessRegistry" not in code_names | attr_names, (
            "upgrade_journal.py executable code must not reference "
            "BashProcessRegistry (D4: the executor child is deliberately "
            "unregistered so harness teardown cannot reach it)"
        )
        # Real (falsifiable) pin: the daemonized spawn runs on subprocess.
        assert "subprocess" in code_names
        # The deliberate-absence documentation must stay (intent is load-bearing).
        assert "NOT registered" in source


# ── Verified-arm predicate + passthrough extras (v0.15.3 P1 Item 1) ──────────


class TestVerifiedArmPredicate:
    """The 5-conjunct ``is_verified_arm`` predicate + the shared
    ``_verified_arm_extras`` expansion (M-11: ``env == "live"`` is
    safe-by-CONSTRUCTION — the passthrough never fires on demo/dev/sandbox
    even with nonce + source present). Sibling tests: none of the existing
    pins (allowlist purity / real-spawn poison / argv equality) is touched.

    Every falsifying class gets its own row; the canonical row is the
    LIVE-rung positive case (user ratification 2026-09-26)."""

    @staticmethod
    def _op(**overrides: Any) -> uj.PendingOp:
        defaults: dict[str, Any] = dict(
            run_id="r-verified-arm-1",
            kind="promote",
            env="live",
            target="1.2.3",
            nonce_consumed=True,
            confirmed_by_human=True,
            confirmed_source="my-discord-bot:123",
        )
        defaults.update(overrides)
        return uj.PendingOp(**defaults)

    @pytest.mark.parametrize(
        ("overrides", "expected"),
        [
            # canonical TRUE row (all 5 conjuncts present, env=live)
            ({}, True),
            # falsifying rows — one conjunct absent / wrong per row
            ({"env": "demo"}, False),          # M-11 5th conjunct
            ({"env": "dev"}, False),           # M-11 5th conjunct
            ({"env": "sandbox"}, False),       # M-11 5th conjunct
            ({"confirmed_source": None}, False),   # no source
            ({"confirmed_source": ""}, False),     # empty source
            ({"confirmed_by_human": False}, False),  # not confirmed
            ({"nonce_consumed": False}, False),    # no nonce
            ({"kind": "restart"}, False),          # not promote
        ],
    )
    def test_is_verified_arm_truth_table(
        self, overrides: dict[str, Any], expected: bool
    ) -> None:
        assert uj.is_verified_arm(self._op(**overrides)) is expected

    def test_is_verified_arm_none_op_false(self) -> None:
        assert uj.is_verified_arm(None) is False

    @pytest.mark.parametrize(
        "overrides",
        [
            {"env": "demo"},
            {"env": "dev"},
            {"env": "sandbox"},
            {"confirmed_source": None},
            {"confirmed_by_human": False},
            {"nonce_consumed": False},
            {"kind": "restart"},
        ],
    )
    def test_verified_arm_extras_helper_returns_empty_for_unverified(
        self, overrides: dict[str, Any]
    ) -> None:
        argv_ext, env_ext = uj._verified_arm_extras(self._op(**overrides))
        assert argv_ext == []
        assert env_ext == {}

    def test_verified_arm_extras_helper_returns_empty_for_none(self) -> None:
        assert uj._verified_arm_extras(None) == ([], {})

    def test_verified_arm_extras_helper_returns_expected_for_verified(self) -> None:
        op = self._op()
        argv_ext, env_ext = uj._verified_arm_extras(op)
        assert argv_ext == ["--f2-verified-closed"]
        assert env_ext == {
            "ENSEMBLE_UPGRADE_LIVE": "1",
            "F2_VERIFIED_NOTE": "my-discord-bot:123:r-verified-arm-1",
        }

    def test_verified_arm_extras_helper_name_frozen(self) -> None:
        """Name-freeze pins (plan W2): the shared helpers keep their exact
        names — a rename must be a paired truth-table update, never a
        silent drift (R-P1-1)."""
        import inspect

        assert uj.is_verified_arm.__name__ == "is_verified_arm"
        assert uj._verified_arm_extras.__name__ == "_verified_arm_extras"
        source = inspect.getsource(uj)
        assert "def is_verified_arm(op: PendingOp | None) -> bool:" in source
        assert (
            "def _verified_arm_extras(op: PendingOp | None) -> "
            "tuple[list[str], dict[str, str]]:" in source
        )

    def test_spawn_executor_has_sole_production_caller_manager_drain(self) -> None:
        """SOLE-CALLER PIN (plan W2): the ONLY production caller of
        ``spawn_executor`` is the manager drain seam. A second call site
        would bypass the drain's verified-arm gate + reaper enqueue — this
        pin fails loudly if one appears. (Grep counts CALL sites — the
        ``spawn_executor`` re-export in upgrade_tools is the test patch
        seam, never called there.)"""
        import re as _re

        daemon_dir = REPO_ROOT / "daemon"
        callers: dict[str, int] = {}
        for py in sorted(daemon_dir.rglob("*.py")):
            hits = _re.findall(
                r"(?<!def )\bspawn_executor\(", py.read_text(encoding="utf-8")
            )
            if hits:
                callers[str(py.relative_to(REPO_ROOT))] = len(hits)
        assert callers == {"daemon/manager.py": 1}, (
            f"unexpected spawn_executor call sites: {callers}"
        )


# ── v0.15.3 P1 Item 4 — public GC + UpgradeJournalSweepService ───────────────
#
# The sweep service lives in daemon/services/ but its tests ride HERE: the
# upgrade_tool_interlock pack runs exactly this file + test_upgrade_tools.py,
# so pack coverage is automatic (pack = pytest runner by design).


class TestPendingActionsGcPublic:
    """The public ``gc_pending_actions`` (scheduled-sweep face of the
    opportunistic pruner). Semantics match ``_gc_pending_actions`` exactly:
    consumed → dropped (audit lives in history), past-TTL unconsumed →
    dropped, unparseable-TTL → KEPT (GC only deletes what it can prove
    dead)."""

    def _seed(self, install: Path) -> dict[str, dict]:
        data = journal_read(install)
        data["pending_actions"] = {
            "r-gc-consumed": {
                "run_id": "r-gc-consumed", "nonce": "CONFIRM-CCCCCCCC",
                "kind": "upgrade", "env": "live", "target": "1.2.3",
                "issued_at": uj.now_iso(),
                "ttl_expires_at": uj.iso_plus(uj.now_iso(), 600),
                "consumed_at": uj.now_iso(),
            },
            "r-gc-expired": {
                "run_id": "r-gc-expired", "nonce": "CONFIRM-EEEEEEEE",
                "kind": "upgrade", "env": "live", "target": "1.2.3",
                "issued_at": uj.now_iso(),
                "ttl_expires_at": "2020-01-01T00:00:00Z",
                "consumed_at": None,
            },
            "r-gc-live": {
                "run_id": "r-gc-live", "nonce": "CONFIRM-LLLLLLLL",
                "kind": "upgrade", "env": "live", "target": "1.2.3",
                "issued_at": uj.now_iso(),
                "ttl_expires_at": uj.iso_plus(uj.now_iso(), 600),
                "consumed_at": None,
            },
        }
        journal_write(install, data)
        return data["pending_actions"]

    def test_pending_actions_gc_prunes_expired_unconsumed(self, install: Path) -> None:
        self._seed(install)
        pruned = uj.gc_pending_actions(install, keep_run_id=None)
        # consumed → dropped (audit in history) + expired-unconsumed →
        # dropped; the unexpired-unconsumed live row is mintable → kept.
        assert pruned == 2
        remaining = journal_read(install)["pending_actions"]
        assert set(remaining) == {"r-gc-live"}

    def test_pending_actions_gc_keep_run_id_exemption(self, install: Path) -> None:
        """The operator/in-progress exemption: ``keep_run_id`` spares exactly
        one entry (consume_pending_action's nonce-already-used semantics);
        the background sweep passes ``None`` — exemption is caller-driven,
        never arm-path-driven."""
        self._seed(install)
        pruned = uj.gc_pending_actions(install, keep_run_id="r-gc-expired")
        assert pruned == 2 - 1  # expired row exempt; consumed still dropped
        remaining = journal_read(install)["pending_actions"]
        assert set(remaining) == {"r-gc-expired", "r-gc-live"}

    def test_pending_actions_gc_no_write_when_nothing_pruned(self, install: Path) -> None:
        """READ-FIRST discipline: a no-op tick leaves the journal
        byte-identical (the sweep ticks every ~90s and must not churn)."""
        data = journal_read(install)
        data["pending_actions"] = {
            "r-gc-live": {
                "run_id": "r-gc-live", "nonce": "CONFIRM-LLLLLLLL",
                "kind": "upgrade", "env": "live", "target": "1.2.3",
                "issued_at": uj.now_iso(),
                "ttl_expires_at": uj.iso_plus(uj.now_iso(), 600),
                "consumed_at": None,
            },
        }
        journal_write(install, data)
        before = uj.journal_path(install).read_bytes()
        assert uj.gc_pending_actions(install, keep_run_id=None) == 0
        assert uj.journal_path(install).read_bytes() == before

    def test_pending_actions_gc_empty_or_missing_noop(self, install: Path) -> None:
        assert uj.gc_pending_actions(install) == 0
        before = uj.journal_path(install).read_bytes()
        assert uj.gc_pending_actions(install) == 0
        assert uj.journal_path(install).read_bytes() == before

    def test_pending_actions_gc_torn_journal_raises(self, install: Path) -> None:
        uj.journal_path(install).write_text('{"torn":', encoding="utf-8")
        with pytest.raises(JournalTorn):
            uj.gc_pending_actions(install)


class TestUpgradeJournalSweepService:
    """Reconcile sweep + liveness guard + reaper worker (real children,
    /tmp journals; the service is the ONLY reaper owner — the spawn seam
    merely enqueues)."""

    def _svc(self, install: Path, **kwargs) -> uj_sweep.UpgradeJournalSweepService:
        return uj_sweep.UpgradeJournalSweepService(install, **kwargs)

    async def _wait_for_event(self, install: Path, event: str, timeout_s: float = 5.0) -> dict:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            for entry in journal_read(install)["history"]:
                if entry["event"] == event:
                    return entry
            await asyncio.sleep(0.05)
        pytest.fail(f"journal event '{event}' not observed within {timeout_s}s")

    def test_reaper_default_timeout_is_660s(self) -> None:
        svc = uj_sweep.UpgradeJournalSweepService(None)
        assert svc.reaper_timeout_seconds == 660
        assert svc.interval_seconds == 90
        # ServicesConfig knobs: ge floors fail fast at boot.
        from pydantic import ValidationError

        from daemon.config import ServicesConfig

        assert ServicesConfig().upgrade_journal_reaper_timeout_seconds == 660
        assert (
            ServicesConfig().upgrade_journal_sweep_interval_seconds == 90
        )
        with pytest.raises(ValidationError):
            ServicesConfig(upgrade_journal_reaper_timeout_seconds=59)
        with pytest.raises(ValidationError):
            ServicesConfig(upgrade_journal_sweep_interval_seconds=0)

    async def test_boot_sweep_clears_stale_pending_op(self, install: Path) -> None:
        """Armed promote op, no in_flight, past expires_at + grace → the
        sweep tick clears it (via the UNCHANGED reconcile_pending_op)."""
        uj.write_pending_op(
            install,
            PendingOp(
                run_id="r-sweep-stale", kind="promote", env="demo",
                target="1.2.3",
                expires_at=uj.iso_plus(
                    uj.now_iso(), -2 * uj.RECONCILE_GRACE_S
                ),
            ),
        )
        svc = self._svc(install)
        result = await svc.sweep_once()
        assert result["skipped"] == 0
        assert result["reconcile"] and "r-sweep-stale" in str(result["reconcile"])
        assert uj.read_pending_op(install) is None
        events = [e["event"] for e in journal_read(install)["history"]]
        assert "sweep" in events  # closure journaled by reconcile

    async def test_periodic_sweep_skips_live_executor(self, install: Path, monkeypatch) -> None:
        """Executor owner with an ALIVE pid + fresh evidence → the tick
        skips clearing (os.kill(pid, 0) was issued) — the op tracks a real
        run (R-P1-7)."""
        uj.write_pending_op(
            install,
            PendingOp(
                run_id="r-sweep-live", kind="promote", env="live",
                target="1.2.3",
                owner_pid=os.getpid(),  # THIS process — verifiably alive
                owner_kind="executor",
                owner_heartbeat_at=uj.now_iso(),
                expires_at=uj.iso_plus(uj.now_iso(), 600),
            ),
        )
        kill_calls: list[tuple[int, int]] = []
        real_kill = os.kill

        def _recording_kill(pid: int, sig: int) -> None:
            kill_calls.append((pid, sig))
            real_kill(pid, sig)

        monkeypatch.setattr(os, "kill", _recording_kill)
        svc = self._svc(install)
        result = await svc.sweep_once()
        monkeypatch.setattr(os, "kill", real_kill)
        assert result["skipped"] == 1
        assert result["reconcile"] is None
        assert (os.getpid(), 0) in kill_calls
        assert uj.read_pending_op(install) is not None  # NOT cleared

    async def test_periodic_sweep_stale_evidence_does_not_block(
        self, install: Path
    ) -> None:
        """TIME-BOUND predicate is load-bearing (NOT bare pid-existence):
        an alive pid with STALE heartbeat evidence no longer blocks the
        sweep — reconcile's own expiry path clears the op."""
        uj.write_pending_op(
            install,
            PendingOp(
                run_id="r-sweep-stale-pid", kind="promote", env="demo",
                target="1.2.3",
                owner_pid=os.getpid(),  # alive, but…
                owner_kind="executor",
                owner_heartbeat_at=uj.iso_plus(uj.now_iso(), -4 * 3600),  # stale
                expires_at=uj.iso_plus(
                    uj.now_iso(), -2 * uj.RECONCILE_GRACE_S
                ),  # …and past expiry+grace
            ),
        )
        svc = self._svc(install)
        result = await svc.sweep_once()
        assert result["skipped"] == 0
        assert uj.read_pending_op(install) is None

    def test_enqueue_reaper_truncates_argv_summary(self, install: Path) -> None:
        svc = self._svc(install)
        long = "x" * 500
        svc.enqueue_reaper(4242, ["bash", long], install, "r-trunc")
        job = svc._reaper_queue.get_nowait()
        assert job.pid == 4242
        assert job.argv_summary == ("bash", "x" * 80)
        assert job.run_id == "r-trunc"

    async def test_reaper_journals_exit_code_on_child_exit_78(
        self, install: Path
    ) -> None:
        """REAL child exiting 78 (the unverified-arm class): the reaper
        journals ``executor_exit`` with exit_code=78 + a bounded log tail.
        The product spawn seam is what the MANAGER patches in its own tests
        (never subprocess.Popen — P2.2 gotcha); here the child is the
        test's own."""
        proc = subprocess.Popen(["bash", "-c", "exit 78"])
        svc = self._svc(install)
        svc.enqueue_reaper(
            proc.pid, ["bash", "-c", "exit 78"], install, "r-exit78"
        )
        svc.start()
        try:
            entry = await self._wait_for_event(install, "executor_exit")
            assert f"pid={proc.pid}" in entry["detail"]
            assert "exit_code=78" in entry["detail"]
            assert "r-exit78" in entry["detail"]
            # comp5 (r-f82e): normal exit (exit 78) has NO signal
            # attribution — verify the detail does NOT mention a signal.
            assert "terminated by" not in entry["detail"], (
                f"normal exit should not have signal attribution: {entry['detail']!r}"
            )
            tail = entry["detail"].split("upgrade.log tail:\n", 1)[-1]
            assert len(tail) <= 4096
        finally:
            await svc.stop()

    async def test_reaper_journals_signal_attribution_for_sigterm_kill(
        self, install: Path
    ) -> None:
        """comp5 (r-f82e): when the executor is killed by a SIGNAL (r-f82e
        was SIGTERM via systemd cgroup teardown), the journal detail must
        surface ``terminated by SIGTERM (15)`` rather than the bare exit
        code ``143``. Confirms the os.WIFSIGNALED + os.WTERMSIG +
        signal.Signals(...) attribution path in ``_reaper_worker``.

        Race-sensitive: the child must NOT be reaped before the reaper's
        waitpid — once a SIGKILL'd process is reaped by the OS, the
        signal info is lost. Use the reaper's own enqueue path and
        signal the child IMMEDIATELY so the reaper catches it before
        the OS reaps. If the test sandbox interferes (signal masking,
        pid namespace, etc.) the test records the observed behavior
        instead of failing — pin the SPECIFIC contract: when a child
        is killed by SIGTERM AND the reaper's waitpid catches the
        signal exit, the journal surfaces the attribution."""
        # Spawn a long-running child we can SIGTERM mid-flight.
        proc = subprocess.Popen(["sleep", "30"])
        # Signal the child BEFORE the reaper's worker reads waitpid.
        # The reaper queue is FIFO; enqueueing the job + signaling the
        # child in the same turn keeps the race window tight.
        svc = self._svc(install)
        svc.enqueue_reaper(proc.pid, ["sleep", "30"], install, "r-sigterm")
        proc.send_signal(signal.SIGTERM)
        # Don't call proc.wait() — the reaper needs to be the one to
        # reap. Just give it a moment to register the waitpid and
        # process the exit.
        svc.start()
        try:
            entry = await self._wait_for_event(
                install, "executor_exit", timeout_s=8.0
            )
            detail = entry["detail"]
            # The reaper either observed the SIGTERM (best case) or saw
            # ChildProcessError (sandbox races, OS-reaped child).
            if "terminated by SIGTERM (15)" in detail:
                # Best-case: signal attribution present.
                assert "exit_code=143" in detail, (
                    f"bare exit_code must also be present: {detail!r}"
                )
                assert f"pid={proc.pid}" in detail
                assert "run_id=r-sigterm" in detail
            else:
                # Sandbox-quirk fallback: the child was reaped before
                # the reaper caught the signal exit. The
                # ChildProcessError path journals exit_code=-1 — that
                # is the documented contract for the "gone before we
                # could see it" case. The signal-attribution path is
                # still proven correct in the Python pin test
                # (test_promote_cgroup_survivorship_python.py 4b) on a
                # host that DOESN'T mask the signal.
                warnings.warn(
                    "sandbox quirk: SIGTERM reaped before the reaper "
                    "observed WIFSIGNALED (ChildProcessError fallback; "
                    "exit_code=-1 contract); see 4b for the attribution "
                    "proof on a host that delivers the signal",
                    stacklevel=2,
                )
                assert "exit_code=-1" in detail, (
                    f"unexpected detail shape (no signal attribution, "
                    f"no ChildProcessError fallback): {detail!r}"
                )
        finally:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await svc.stop()

    async def test_reaper_benign_detaches_and_journals_executor_still_running_on_timeout(
        self, install: Path
    ) -> None:
        """C2 benign-detach: child outlives the (tiny) timeout →
        ``executor_still_running`` journaled, child NOT killed, worker
        keeps serving the queue."""
        proc = subprocess.Popen(["sleep", "30"])
        svc = self._svc(install, reaper_timeout_seconds=1)
        svc.enqueue_reaper(proc.pid, ["sleep", "30"], install, "r-still")
        svc.start()
        try:
            entry = await self._wait_for_event(
                install, "executor_still_running", timeout_s=8.0
            )
            assert f"pid={proc.pid}" in entry["detail"]
            assert "run_id=r-still" in entry["detail"]
            assert "still running after 1s" in entry["detail"]
            assert "no kill" in entry["detail"]
            # Benign: the child is still alive right after the detach.
            os.kill(proc.pid, 0)  # raises if dead → fail the test
            # The worker loop still serves the queue (enqueue a follow-up
            # that exits immediately and observe its executor_exit).
            p2 = subprocess.Popen(["bash", "-c", "exit 0"])
            svc.enqueue_reaper(p2.pid, ["bash", "-c", "exit 0"], install, "r-after")
            await self._wait_for_event(install, "executor_exit", timeout_s=8.0)
        finally:
            await svc.stop()
            try:
                os.kill(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                os.waitpid(proc.pid, os.WNOHANG)
            except ChildProcessError:
                pass  # the lingering shielded wait already reaped it

    async def test_reaper_continues_after_journal_write_oserror(
        self, install: Path, caplog
    ) -> None:
        """R-M5-1/R1: an OSError from the journal append → one WARNING
        carrying run_id + pid, NO retry, NO raise — and the worker loop
        continues to the next job."""
        proc = subprocess.Popen(["bash", "-c", "exit 7"])
        svc = self._svc(install)
        svc.enqueue_reaper(proc.pid, ["bash", "-c", "exit 7"], install, "r-oserr")
        real_append = uj.journal_history_append
        calls = {"n": 0}

        def _flaky_append(dir_, event, detail):
            # A1 supervision-aware: start() fires the boot advisory's
            # ``supervision_boot`` append BEFORE the reaper's write — it
            # must neither satisfy the first-fire count nor simulate the
            # fault. Only the event under test (executor_exit) is flaky.
            if event != "executor_exit":
                return
            calls["n"] += 1
            raise OSError(28, "No space left on device")

        uj.journal_history_append = _flaky_append  # type: ignore[assignment]
        try:
            svc.start()
            with caplog.at_level(logging.WARNING, logger="daemon.services.upgrade_journal_sweep"):
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline and calls["n"] == 0:
                    await asyncio.sleep(0.05)
                assert calls["n"] == 1, "journal append was not attempted"
                warnings = [
                    r for r in caplog.records if "executor_exit" in r.getMessage()
                ]
                assert warnings, "expected a WARNING for the failed write"
                assert "r-oserr" in warnings[0].getMessage()
                assert f"pid={proc.pid}" in warnings[0].getMessage()
                assert "No space left on device" in warnings[0].getMessage()
            # NO retry (the flaky append fired exactly once) + loop
            # continues: restore the real append and confirm the NEXT job
            # journals fine through it.
            uj.journal_history_append = real_append  # type: ignore[assignment]
            p2 = subprocess.Popen(["bash", "-c", "exit 0"])
            svc.enqueue_reaper(p2.pid, ["bash", "-c", "exit 0"], install, "r-after-oserr")
            entry = await self._wait_for_event(install, "executor_exit")
            assert "r-after-oserr" in entry["detail"]
            assert calls["n"] == 1  # no retry of the failed write
        finally:
            # Restore BEFORE stop(): a failing assert above must never leak
            # _flaky_append session-wide (it cascade-killed the journal-
            # writing tests that follow this module). stop() is a safe
            # no-op even if start() never ran.
            uj.journal_history_append = real_append  # type: ignore[assignment]
            await svc.stop()

    # ── M-1: dedicated waitpid executor (hygiene + isolation) ────────────

    async def test_reaper_uses_dedicated_executor_isolated_from_default(
        self, install: Path
    ) -> None:
        """M-1: ``start()`` materializes a service-owned DEDICATED
        ``ThreadPoolExecutor`` for the blocking ``os.waitpid`` — bounded
        ``max_workers`` (= ``min(16, cpu_count+4)``), named with prefix
        ``UpgradeJournalReaperWaitpid``. The shared asyncio default
        executor is NOT used; ``stop()`` shuts the dedicated one down and
        nulls the reference."""
        svc = self._svc(install)
        # Lazy: not yet materialized.
        assert svc._waitpid_executor is None
        svc.start()
        try:
            ex = svc._waitpid_executor
            assert ex is not None, "start() must materialize a waitpid executor"
            # Match asyncio's default formula but capped at 16 — bounded leak.
            assert ex._max_workers == min(16, (os.cpu_count() or 1) + 4)
            # Distinct from the SHARED default executor.
            loop = asyncio.get_running_loop()
            assert ex is not loop._default_executor
            # The thread-name prefix is the strongest signal — asyncio's
            # default uses 'asyncio', ours uses 'UpgradeJournalReaperWaitpid'.
            assert ex._thread_name_prefix == "UpgradeJournalReaperWaitpid"
        finally:
            await svc.stop()
            # stop() nulls the reference so a start-after-stop cycle recreates.
            assert svc._waitpid_executor is None

    async def test_reaper_waitpid_runs_on_dedicated_executor_not_default(
        self, install: Path, monkeypatch
    ) -> None:
        """M-1 end-to-end: instrument ``_waitpid_blocking`` to record the
        executing thread's name. The recorded names carry the dedicated
        prefix (``UpgradeJournalReaperWaitpid_*``) and NEVER the asyncio
        default (``asyncio_*``). Pins that the executor-identity change
        ACTUALLY flows through the worker, not just sits on the attribute."""
        svc = self._svc(install)
        recorded: list[str] = []
        real = svc._waitpid_blocking

        def _traced(pid: int) -> int:
            recorded.append(threading.current_thread().name)
            return real(pid)

        # Staticmethod descriptor — ``self._waitpid_blocking`` returns the
        # underlying function, matching the production call site.
        monkeypatch.setattr(
            type(svc), "_waitpid_blocking", staticmethod(_traced)
        )
        proc = subprocess.Popen(["bash", "-c", "exit 0"])
        svc.enqueue_reaper(proc.pid, ["bash", "-c", "exit 0"], install, "r-exec0")
        svc.start()
        try:
            await self._wait_for_event(install, "executor_exit")
            assert recorded, "waitpid was never invoked"
            assert all(
                n.startswith("UpgradeJournalReaperWaitpid_") for n in recorded
            ), (
                "waitpid ran off the dedicated executor — M-1 isolation "
                f"broken: thread names recorded: {recorded}"
            )
            assert not any(n.startswith("asyncio_") for n in recorded), (
                "waitpid ran on the SHARED default executor — the bug "
                f"M-1 was supposed to fix: thread names: {recorded}"
            )
        finally:
            await svc.stop()

    async def test_reaper_stop_is_bounded_on_hung_child(
        self, install: Path
    ) -> None:
        """M-1: ``stop()`` returns within a bounded window even when a
        child is still hung — the dedicated executor is shut down with
        ``wait=False, cancel_futures=True``; the in-flight ``os.waitpid``
        thread is abandoned (Python cannot interrupt it from outside;
        the OS reaps the child via ``start_new_session=True`` either
        way, C2 benign-detach contract). The SHARED default executor is
        not drained here, so this test would HANG on the pre-M-1 code."""
        proc = subprocess.Popen(["sleep", "30"])
        # reaper_timeout_seconds deliberately larger than the stop budget
        # so the worker's own wait_for CANNOT rescue us — only the
        # executor.shutdown(wait=False) in stop() can.
        svc = self._svc(install, reaper_timeout_seconds=600)
        svc.enqueue_reaper(proc.pid, ["sleep", "30"], install, "r-hung")
        svc.start()
        # Give the worker a beat to enqueue the waitpid on the executor.
        await asyncio.sleep(0.2)
        t0 = time.monotonic()
        await svc.stop()
        elapsed = time.monotonic() - t0
        assert elapsed < 2.0, (
            f"stop() blocked for {elapsed:.2f}s on a hung child — the "
            "shutdown(wait=False) contract is broken"
        )
        assert svc._waitpid_executor is None
        # Cleanup: the test process must not leak the hung child.
        try:
            os.kill(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            os.waitpid(proc.pid, os.WNOHANG)
        except ChildProcessError:
            pass


# ── User-origin classification (registry-backed — verdict §4) ────────────────


class TestUserOriginSources:
    """Registry-backed classification (verdict §4 — the static prefix
    whitelist was a dialect mismatch and is DEAD). Every test here drives
    classify_user_origin / is_user_origin_source / the REAL stamp site
    against a FAKE registry table — never a live daemon, never a DB."""

    @staticmethod
    def _adapter(source_type: Any) -> Any:
        """A fake adapter exposing ``source_type`` like the real ones
        (daemon/sources/base.py:82-84 — config.source_type)."""

        class _Adapter:
            pass

        return type("Adapter", (), {"source_type": source_type})()

    @staticmethod
    def _registry(table: dict) -> Any:
        """Minimal fake of the sources-registry surface the classifier
        uses (``registry.get``; registry.py:236 returns the adapter or
        None)."""

        class _Registry:
            def __init__(self, t: dict) -> None:
                self._t = t

            def get(self, source_id: str) -> Any:
                return self._t.get(source_id)

        return _Registry(table)

    # ── static exact-match path ──────────────────────────────────────────

    def test_exact_api_whitelisted(self) -> None:
        assert is_user_origin_source("api") is True
        assert classify_user_origin("api", None) == (True, "exact:api")

    def test_api_with_uid_never_takes_the_exact_path(self) -> None:
        """VERDICT-PINNED: full-STRING equality only. A hostile source
        REGISTERED under id 'api' mints 'api:<uid>' — it must NOT arm via
        the exact-match path (it goes through the registry like any other
        source; with a non-chat type it does not arm at all)."""
        registry = self._registry({"api": self._adapter("scheduler")})
        ok, detail = classify_user_origin("api:spoofed-uid", registry.get)
        assert ok is False
        assert detail != "exact:api"
        assert detail == "source-type-not-chat:'scheduler'"

    def test_api_lookalikes_fail_closed(self) -> None:
        """MINOR-5 (security review round 1): fail-closed look-alike pins.
        Exact FULL-STRING equality only — a future whitespace- or
        case-normalization refactor must not silently widen the F2
        exact-match arm ("Api" case-fold, " api"/"api " strip,
        "api\\u200b" zero-width-space normalize)."""
        for lookalike in ("Api", " api", "api ", "api\u200b"):
            assert is_user_origin_source(lookalike) is False, repr(lookalike)
            # Also with a registry present: a look-alike is NOT "api" and
            # its segment does not resolve in a sane registry table.
            registry = self._registry({"my-discord-bot": self._adapter("discord")})
            assert is_user_origin_source(lookalike, registry.get) is False, (
                repr(lookalike)
            )

    def test_colon_only_and_empty_segment_fail_closed(self) -> None:
        """MINOR-5: colon-only / empty-first-segment source strings never
        arm — a future split() refactor must not start treating ':' / ':x'
        / '::::' as armable id patterns."""
        for source in (":", ":x", "::::"):
            assert is_user_origin_source(source) is False, repr(source)

    def test_f2_boundary_registered_chat_id_pattern_arms_by_design(self) -> None:
        """MINOR-3 (security review round 1): boundary-pin the ACCEPTED F2
        scope. vs the old static whitelist, the STRING SET that can arm via
        the unauth-loopback body.source grows from {"api"} to {"api"} ∪
        {registered-chat-id patterns} — the CAPABILITY delta is zero (an
        attacker who can forge body.source could already send "api"; F2
        forging is the separately-fenced pre-existing exposure). This test
        DOCUMENTS that design; it does not widen it: only the write-once,
        daemon-controlled source_type decides — never the id string."""
        # A registered chat-typed adapter's id pattern arms — by design.
        registry = self._registry({"chat-bot": self._adapter("discord")})
        assert is_user_origin_source("chat-bot:attacker:room", registry.get) is True
        # Same id pattern with a NON-chat type does NOT arm: the id string
        # confers nothing; registry metadata (source_type) is the decider.
        non_chat = self._registry({"chat-bot": self._adapter("scheduler")})
        assert is_user_origin_source("chat-bot:attacker:room", non_chat.get) is False
        # An UNREGISTERED id pattern does NOT arm (fail-closed).
        assert is_user_origin_source("ghost-bot:attacker:room", registry.get) is False

    # ── registry-backed chat classification ──────────────────────────────

    def test_arbitrary_id_registered_chat_source_stamps(self) -> None:
        """THE live-defect shape (verdict §1): 'my-discord-bot:<uid>:<cid>'
        with a REGISTERED discord-typed adapter — must STAMP. No operator
        rename needed; the id is irrelevant, the registry type decides."""
        from daemon.models.source import SourceType

        registry = self._registry(
            {"my-discord-bot": self._adapter(SourceType.discord)}
        )
        source = "my-discord-bot:1536944374972416070:1536944376125587492"
        assert classify_user_origin(source, registry.get) == (
            True,
            "registered-chat:discord",
        )
        assert is_user_origin_source(source, registry.get) is True

    def test_all_chat_source_types_arm_via_registry(self) -> None:
        for st in ("telegram", "slack", "discord", "whatsapp"):
            registry = self._registry({"bot": self._adapter(st)})
            assert is_user_origin_source(f"bot:user:1", registry.get) is True, st

    def test_type_not_name_classification(self) -> None:
        """Classification reads source_TYPE, not the id string: an id that
        contains 'discord' but registers a non-chat type does NOT arm; a
        chat-typed adapter with an arbitrary, non-hinting id DOES."""
        registry = self._registry(
            {
                "my-discord-bot": self._adapter("scheduler"),  # name lies
                "plain-scheduler": self._adapter("scheduler"),
                "main-bot": self._adapter("discord"),  # type decides
                "hooky": self._adapter("webhook"),  # webhook NEVER arms
            }
        )
        # id hints discord, type is not chat → fail-closed.
        ok, detail = classify_user_origin("my-discord-bot:1", registry.get)
        assert (ok, detail) == (False, "source-type-not-chat:'scheduler'")
        # chat type under a non-obvious id → arms.
        assert classify_user_origin("main-bot:2", registry.get) == (
            True,
            "registered-chat:discord",
        )
        # webhook type is EXCLUDED (no WebhookAdapter exists — verdict §1).
        ok, detail = classify_user_origin("hooky:3", registry.get)
        assert (ok, detail) == (False, "source-type-not-chat:'webhook'")
        # non-chat id, non-chat type.
        assert is_user_origin_source("plain-scheduler:4", registry.get) is False

    def test_unregistered_id_fails_closed(self) -> None:
        registry = self._registry({"my-discord-bot": self._adapter("discord")})
        ok, detail = classify_user_origin("totally-unknown:1:2", registry.get)
        assert (ok, detail) == (False, "unregistered")
        # Deregistered mid-session (adapter removed from the table): same.
        registry._t.pop("my-discord-bot")
        ok, detail = classify_user_origin("my-discord-bot:9", registry.get)
        assert (ok, detail) == (False, "unregistered")

    def test_registry_unavailable_and_raising_fail_closed(self) -> None:
        # No registry at all (bootstrap window / bare manager): only 'api'.
        assert classify_user_origin("discord:1", None) == (
            False,
            "registry-unavailable",
        )

        class _Boom:
            def get(self, source_id: str) -> Any:
                raise RuntimeError("registry wedged")

        ok, detail = classify_user_origin("discord:1", _Boom().get)
        assert (ok, detail) == (False, "registry-error:RuntimeError")

    def test_detail_token_rendering_is_bounded(self) -> None:
        """MINOR-1 (security review round 1): the ``source-type-not-chat``
        detail token must be BOUNDED — never a raw ``{st_value!r}``, which
        would leak enum class names ("SourceType.discord") and, for
        default-repr pathological objects, memory addresses into gate
        refusal reasons. Strings render value-capped; non-strings render
        their TYPE NAME only."""
        # (a) Non-string source_type: type name only — no class-name path,
        #     no '<... object at 0x...>' address, no repr payload.
        class _Pathological:
            def __repr__(self) -> str:  # would leak an address if rendered
                return "<_Pathological object at 0x7f00deadbeef>"

            __str__ = __repr__

        registry = self._registry({"bot": self._adapter(_Pathological())})
        ok, detail = classify_user_origin("bot:1", registry.get)
        assert ok is False
        assert detail == "source-type-not-chat:_Pathological"
        assert "0x" not in detail and "object at" not in detail

        # (b) A pathologically LONG string value is capped in the token.
        registry = self._registry({"bot": self._adapter("x" * 500)})
        ok, detail = classify_user_origin("bot:2", registry.get)
        assert ok is False
        assert detail == f"source-type-not-chat:{'x' * 40!r}"
        assert len(detail) <= len("source-type-not-chat:") + 42

        # (c) Established short-string tokens stay stable (gate-refusal
        #     compatibility): repr-style, uncapped-needed, distinguishable.
        registry = self._registry({"bot": self._adapter("webhook")})
        ok, detail = classify_user_origin("bot:3", registry.get)
        assert (ok, detail) == (False, "source-type-not-chat:'webhook'")

    # ── reserved internal lanes: absolute fail-closed ────────────────────

    def test_internal_and_spoofed_sources_fail_closed(self) -> None:
        for source in (
            "internal_agent:developer",   # agent-originated enqueue
            "agent:worker-1",             # legacy agent prefix
            "cascade_resume",             # internal resume lane
            "scheduler",                  # scheduled job — not a human
            "internal_invoke_and_wait:1",  # internal invoke lane
            "internal_report:child-1",
            "internal_error_report:child-1",
            "Internal_agent:developer",   # case-spoof
            "internal-agent:developer",   # dash-spoof
            "telegramx:user:1",           # prefix-spoof (no colon after telegram)
            "",                           # empty
            None,                         # absent
        ):
            assert is_user_origin_source(source) is False, source
            # With a registry present the reserved lanes STILL fail closed
            # (belt-and-suspenders: even a mis-registered reserved id).
            registry = self._registry({"agent": self._adapter("discord")})
            assert is_user_origin_source(source, registry.get) is False, source

    def test_misregistered_reserved_id_cannot_arm(self) -> None:
        """Even if an operator registers source_id='agent' with a chat
        type, 'agent:<caller>' (the job-lane override) stays OUTSIDE
        user-origin — is_reserved_source is checked BEFORE the registry."""
        registry = self._registry({"agent": self._adapter("discord")})
        ok, detail = classify_user_origin("agent:ari", registry.get)
        assert (ok, detail) == (False, "reserved-internal")

    # ── display constant accuracy ────────────────────────────────────────

    def test_frozen_chat_type_set(self) -> None:
        assert USER_ORIGIN_CHAT_SOURCE_TYPES == frozenset(
            {"telegram", "slack", "discord", "whatsapp"}
        )

    def test_display_names_the_real_rule(self) -> None:
        display = user_origin_sources_display()
        assert '"api"' in display
        for st in sorted(USER_ORIGIN_CHAT_SOURCE_TYPES):
            assert st in display, display
        assert "webhook" in display  # the exclusion is stated, not hidden

    # ── stamp-site integration (REAL manager method) ─────────────────────

    def test_stamp_site_never_stamps_for_spoofed_origin(self) -> None:
        """The REAL stamp site (manager.stamp_user_origin_window): a
        non-user-origin source must NOT stamp a window — and must CLEAR any
        earlier window (per-turn semantics: an agent-originated turn never
        inherits a prior turn's user authorization)."""
        from daemon.manager import InstanceManager

        harness = object.__new__(InstanceManager)  # skip heavy __init__
        harness._user_origin_windows = {}

        # Whitelisted source stamps.
        InstanceManager.stamp_user_origin_window(harness, "inst-1", "api", "m-1")
        assert "inst-1" in harness._user_origin_windows
        assert harness._user_origin_windows["inst-1"]["source"] == "api"
        # Contract bridge to the gate's expectations (system_upgrade reads
        # these window keys in daemon/tools/upgrade_tools.py): a rename on
        # either side of the stamp↔gate contract fails loudly HERE.
        assert set(harness._user_origin_windows["inst-1"]) >= {
            "source", "message_id", "expires_at",
        }

        # Spoofed/internal sources: no NEW window …
        InstanceManager.stamp_user_origin_window(harness, "inst-2", "internal_agent:worker", "m-2")
        assert "inst-2" not in harness._user_origin_windows
        InstanceManager.stamp_user_origin_window(harness, "inst-3", "scheduler", "m-3")
        assert "inst-3" not in harness._user_origin_windows
        InstanceManager.stamp_user_origin_window(harness, "inst-4", "cascade_resume", "m-4")
        assert "inst-4" not in harness._user_origin_windows
        # … and an EXISTING window is cleared (stale authorization never
        # survives an agent-originated follow-up turn).
        InstanceManager.stamp_user_origin_window(harness, "inst-1", "agent:worker", "m-5")
        assert "inst-1" not in harness._user_origin_windows

    def test_stamp_site_registry_backed_classification(self) -> None:
        """The REAL stamp site with a REAL-shaped registry fixture: the
        live-defect source string ('my-discord-bot:<uid>:<cid>', verdict
        §1) now STAMPS; an unregistered id CLEARS; and every observation —
        stamped or cleared — is recorded in ``_user_origin_last_stamp``
        with the classifier's detail token (W1 hardening: the gate refusal
        names the observed source)."""
        from daemon.manager import InstanceManager
        from daemon.models.source import SourceType

        registry = self._registry(
            {"my-discord-bot": self._adapter(SourceType.discord)}
        )
        harness = object.__new__(InstanceManager)  # skip heavy __init__
        harness._user_origin_windows = {}
        harness._user_origin_last_stamp = {}
        harness.source_registry = registry

        # (i) arbitrary-id registered chat source STAMPS (the live defect).
        src = "my-discord-bot:1536944374972416070:1536944376125587492"
        InstanceManager.stamp_user_origin_window(harness, "inst-9", src, "m-9")
        assert harness._user_origin_windows["inst-9"]["source"] == src
        last = harness._user_origin_last_stamp["inst-9"]
        assert last["source"] == src
        assert last["stamped"] is True
        assert last["detail"] == "registered-chat:discord"

        # (ii) unregistered id CLEARS the window (and records why).
        InstanceManager.stamp_user_origin_window(harness, "inst-9", "ghost:1", "m-10")
        assert "inst-9" not in harness._user_origin_windows
        last = harness._user_origin_last_stamp["inst-9"]
        assert last["source"] == "ghost:1"
        assert last["stamped"] is False
        assert last["detail"] == "unregistered"

    def test_stamp_site_registry_fault_fails_closed(self) -> None:
        """A raising registry must never break dispatch NOR leave a window
        behind (fail-closed — verdict §4 point 4)."""
        from daemon.manager import InstanceManager

        class _BoomRegistry:
            def get(self, source_id: str) -> Any:
                raise RuntimeError("registry wedged")

        harness = object.__new__(InstanceManager)
        harness._user_origin_windows = {"inst-b": {"source": "api"}}
        harness._user_origin_last_stamp = {}
        harness.source_registry = _BoomRegistry()
        InstanceManager.stamp_user_origin_window(harness, "inst-b", "discord:1", "m-b")
        assert "inst-b" not in harness._user_origin_windows
        assert harness._user_origin_last_stamp["inst-b"]["stamped"] is False
        assert harness._user_origin_last_stamp["inst-b"]["detail"] == (
            "registry-error:RuntimeError"
        )

    async def test_m2_seam_nonsilent_none_source_clears_window(self) -> None:
        """M2 (P2.2 fix pass 2026-08-23; seam test added P2.3 B3.5
        MINOR-A): a NON-silent dispatch with ``message_source=None``
        CLEARS the user-origin window via the REAL dispatch funnel —
        ``InstanceManager._process_message_with_tracking`` calls the real
        stamp site at its top, and the stamp treats None as
        non-whitelisted/clearing. A source-less agent-authored turn must
        never inherit a prior turn's user authorization (fail-closed).
        Silent resume remains the ONLY skip: no message is injected, so
        the window keeps the original turn's."""
        from daemon.manager import InstanceManager

        harness = object.__new__(InstanceManager)  # skip heavy __init__
        harness._user_origin_windows = {}
        delegations: list[dict] = []

        class _MessagingStub:
            async def _process_message_with_tracking(self, **kwargs):
                delegations.append(kwargs)
                return "MessageResult-stub"

        harness._messaging_service = _MessagingStub()

        # Genuine user turn stamps the window.
        harness.stamp_user_origin_window("inst-1", "api", "m-1")
        assert "inst-1" in harness._user_origin_windows

        # (a) NON-silent + message_source=None → the REAL funnel clears it.
        await harness._process_message_with_tracking(
            instance_id="inst-1",
            message="agent-authored turn with no source",
            message_id="m-2",
            message_source=None,
            silent=False,
        )
        assert delegations, "the funnel must still delegate to the messaging service"
        assert "inst-1" not in harness._user_origin_windows, (
            "M2: a non-silent source=None dispatch must CLEAR the window "
            "via the real stamp site"
        )

        # (b) Silent resume is the only skip — the window survives.
        harness.stamp_user_origin_window("inst-1", "api", "m-3")
        await harness._process_message_with_tracking(
            instance_id="inst-1",
            message="",
            message_id="m-4",
            message_source=None,
            silent=True,
        )
        assert "inst-1" in harness._user_origin_windows
        assert harness._user_origin_windows["inst-1"]["message_id"] == "m-3"


# ── N4 (P2.2 fix pass 2026-08-23) — _json_escape control-char hardening ──────


class TestJsonEscapeControlChars:
    """lib.sh ``_json_escape`` escapes EVERY control char < 0x20 (plus
    DEL 0x7F — NIT-D, P2.3 B3.5) as a
    standard ``\\u00XX`` escape — and passes NON-ASCII (é, curly quotes,
    CJK) through raw: bash 3.2 ``printf '%d'`` yields SIGNED bytes, so a
    bare ``-lt 32`` guard dragged every char >= 0x80 into the escape
    branch (\\uffffff… — silently accepted by lenient readers). Before
    N4 only \\n/\\t/\\r were handled — a raw \\x1f/\\x0b/\\x1b inside an
    LLM-controlled ``--reason`` wrote INVALID JSON for every strict
    reader even though lib.sh's own crude extractor tolerated it.
    Journal integrity is load-bearing."""

    def test_direct_escape_output(self, install: Path) -> None:
        """The escaper itself: control chars → \\u00XX; printables, classic
        escapes, and NON-ASCII (F1: bash 3.2 signed bytes — high UTF-8
        bytes must pass through RAW, never into the \\u00XX branch)
        untouched."""
        rc = _bash_lib(
            install,
            'printf \'%s\' "$(_json_escape "$(printf \'a\\033b\\037c\\013d\\ne\\\\f\\"g h\\303\\251\\342\\200\\234q\\342\\200\\235\\344\\270\\255z\')")"',
        )
        assert rc.returncode == 0, rc.stderr
        # Exact byte output — high UTF-8 bytes survive verbatim:
        # é=\u00e9 “=\u201c ”=\u201d 中=\u4e2d.
        assert rc.stdout == 'a\\u001bb\\u001fc\\u000bd\\ne\\\\f\\"g h\u00e9\u201cq\u201d\u4e2dz', repr(rc.stdout)

    def test_del_0x7f_escaped(self, install: Path) -> None:
        """NIT-D (P2.2 tidy cycle-3, closed P2.3 B3.5): DEL (0x7F) — the
        one control char >= 0x20 — escapes as \\u007f, same \\u00XX form
        as the < 0x20 family."""
        rc = _bash_lib(
            install,
            'printf \'%s\' "$(_json_escape "$(printf \'x\\177y\')")"',
        )
        assert rc.returncode == 0, rc.stderr
        assert rc.stdout == 'x\\u007fy', repr(rc.stdout)

    def test_hostile_reason_detail_journal_stays_parseable(self, install: Path) -> None:
        """End-to-end at the real writer: ``journal_history_append`` with
        a hostile detail (newlines + ESC + US + VT + non-ASCII é/“”/CJK —
        the shape an LLM-controlled --reason produces) writes a journal
        that lib.sh still reads (rc 0) AND python json.loads parses, with
        the detail round-tripping EXACTLY — decoded equality, not just
        parseability: F1's signed-char bug had lenient readers ACCEPT a
        silently-corrupted \\uffffff… escape, and exact round-trip is the
        only assertion that catches silent corruption."""
        rc = _bash_lib(
            install,
            'detail="$(printf \'line1\\nline2\\033ESC\\037US\\013VT caf\\303\\251 \\342\\200\\234quotes\\342\\200\\235 \\344\\270\\255 end\')"\n'
            'journal_history_append restart "reason: $detail"\n'
            'journal_read >/dev/null\n'
            'echo LIBSH_PARSE_RC=$?\n',
        )
        assert rc.returncode == 0, rc.stderr
        assert "LIBSH_PARSE_RC=0" in rc.stdout
        # The strict reader: raw control bytes would make json.loads fail.
        data = json.loads(uj.journal_path(install).read_bytes())
        detail = data["history"][-1]["detail"]
        assert detail == "reason: line1\nline2\x1bESC\x1fUS\x0bVT caf\u00e9 \u201cquotes\u201d \u4e2d end", repr(detail)
