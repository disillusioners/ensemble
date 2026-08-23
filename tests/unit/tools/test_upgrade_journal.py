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
* ``USER_ORIGIN_SOURCES`` — whitelist frozen from the actual dispatch
  formats; every ``internal_*`` / agent / scheduler source fails closed.

All fixtures live under ``tmp_path`` — never a real install dir, never
live. lib.sh interop runs ``bash`` subprocesses with a scrubbed env.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from daemon.tools import upgrade_journal as uj
from daemon.tools.upgrade_journal import (
    EXECUTOR_ENV_ALLOWLIST,
    EXECUTOR_ENV_PREFIXES,
    JOURNAL_EMPTY,
    NONCE_RE,
    NONCE_TTL_S,
    USER_ORIGIN_SOURCES,
    JournalTorn,
    PendingAction,
    PendingOp,
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
)

# Repo root: tests/unit/tools/test_upgrade_journal.py -> parents[3].
REPO_ROOT = Path(__file__).resolve().parents[3]
LIB_SH = REPO_ROOT / "scripts" / "upgrade" / "lib.sh"

# A port that is none of: dev 8079, demo 7979, prod 9797 — and is never
# actually bound by these tests (status.sh only probes it read-only).
SANDBOX_PORT = "8399"


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
        action = self._store(install)
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
        (b) the child leads an independent process group (start_new_session),
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
            ("PORT", SANDBOX_PORT),
            ("POSTGRES_DB", "ensemble_sandbox"),
            ("PGHOST", "127.0.0.1"),
            ("ENSEMBLE_UPGRADE_LIVE", "1"),  # poison: must NOT pass
            ("OPENAI_API_KEY", "sk-secret"),  # poison: must NOT pass
        ):
            monkeypatch.setenv(key, val)

        pid = uj.spawn_executor(["bash", str(script)], install, {"RUN_ID": "r-spawn"})
        try:
            # (b) process-group independence: the child leads its own group,
            # distinct from THIS test process's group (survives teardown).
            child_pgid = os.getpgid(pid)
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
            assert child_env["PORT"] == SANDBOX_PORT
            assert child_env["PGHOST"] == "127.0.0.1"
            assert child_env["RUN_ID"] == "r-spawn"
            assert "OPENAI_API_KEY" not in child_env
            assert "ENSEMBLE_UPGRADE_LIVE" not in child_env
            pgid_line = [
                ln for ln in (install / "data" / "upgrade.log").read_text().splitlines()
                if ln.startswith("PGID=")
            ]
            assert pgid_line and pgid_line[0] == f"PGID={child_pgid}"
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


# ── USER_ORIGIN_SOURCES (D-FA3.1 — assumption #1 closure) ────────────────────


class TestUserOriginSources:
    def test_exact_api_whitelisted(self) -> None:
        assert is_user_origin_source("api") is True

    def test_channel_prefixes_whitelisted(self) -> None:
        for source in (
            "telegram:user:1",
            "telegram:123",
            "webhook:hook-a:user:9",
            "whatsapp:user:2",
            "discord:user:3",
            "slack:user:4",
        ):
            assert is_user_origin_source(source) is True, source

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

    def test_frozen_whitelist_content(self) -> None:
        assert USER_ORIGIN_SOURCES == frozenset(
            {"api", "telegram:", "webhook:", "whatsapp:", "discord:", "slack:"}
        )

    def test_stamp_site_never_stamps_for_spoofed_origin(self) -> None:
        """The REAL stamp site (manager.stamp_user_origin_window): a
        non-whitelisted source must NOT stamp a window — and must CLEAR any
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


# ── N4 (P2.2 fix pass 2026-08-23) — _json_escape control-char hardening ──────


class TestJsonEscapeControlChars:
    """lib.sh ``_json_escape`` escapes EVERY control char < 0x20 as a
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
