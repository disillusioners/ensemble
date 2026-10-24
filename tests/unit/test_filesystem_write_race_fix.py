"""Unit tests for the edit_file / write_file concurrency fix (2026-10-22 race).

Incident: LangGraph executes one message's tool calls in a THREAD POOL. Two
``edit_file`` calls on the SAME file in one block interleaved at the
read -> replace -> write seams: both threads read the same original content,
and the last whole-file ``write_text`` discarded the other thread's
replacement — while BOTH tools reported SUCCESS (phantom success). Reproduced
across 4 instances on 2026-10-22.

The fix (``daemon/tools/filesystem.py``, race-fix banner) has three layers:

1. Per-canonical-path lock registry — same-file writes serialize across
   ``edit_file`` AND ``write_file``; different files stay parallel. The
   registry key is case-normalized on case-insensitive filesystems (macOS
   default volumes) so two spellings of ONE file ("Config.txt" /
   "config.txt") share ONE lock instead of racing into a lost write.
2. Atomic write — unique temp file in the target's own directory +
   ``os.replace`` inode swap; mode preserved; temp cleaned up on failure.
   The temp is created 0o600 (never 0o666) and the final mode is applied
   BEFORE the content fsync, so the write window never widens a secret
   target's permissions and a crash cannot revert the mode.
3. Read-back verification — the tool re-reads the file after the write and
   returns an honest ERROR on any mismatch, never a phantom SUCCESS. The
   re-read is BYTE-EXACT (``newline=""``), so a byte-correct CRLF write is
   not reported as a false failure.

These tests pin all three layers plus the unchanged success/error shapes.

Determinism — measured, not assumed. The choreography tests are
deterministic POST-FIX (they synchronize on events, not sleeps). On the
pre-fix BASE leg they are only PARTLY deterministic: the failures raised as
``AttributeError`` on branch-only symbols (``fs._atomic_write_text``) are
scheduler-independent, but the named barrier tests can PASS on the base leg
by scheduling luck. The scheduler-dependent one is
``TestConcurrentSameFileEdits::test_barrier_two_threads_both_replacements_present``.

Base-leg figures (measured 2026-10-23, READ-ONLY runs against the TRUE
pre-fix release base — this branch's merge base, "chore(release): bump version
to 0.27.5"):

* This file as shipped on the branch, against the true base: 11-13 failed /
  19-21 passed / 1 skipped (scheduler-dependent). Two independent
  measurements: the tester recorded 11-13F / 19-21P / 1S; the reviewer
  recorded 12F / 20P / 1S over 7 of 8 runs. Quote the RANGE, never a fixed
  denominator.
* HISTORICAL — the ORPHANED pre-review 27-test revision of this file
  measured 9-11 failed / 15-17 passed / 1 skipped over 12 runs (partly
  deterministic). That revision is GONE (it was amended); its numbers are
  NOT current and must never be quoted as such.

Non-vacuity anchor: against the ORPHANED pre-review revision's source, the
expanded file measured 5 failed / 28 passed (3/3 runs, byte-identical failed
set).

The mid-branch pins (the edit_file CRLF read-back false-ERROR and the
case-variant lock key) are REGRESSION GUARDS for defects introduced by this
branch — NOT base-leg discriminators. Each is mutation-proved non-vacuous (a
targeted mutation kills it), and 4 of the 6 are green on the true base BY
DESIGN: the base has no read-back verification at all, so the defect class
does not exist there.

This file at this commit: 42/42 (green). Against the true base the FINAL
42-test file measures 19 failed / 22 passed / 1 skipped (3/3 runs, stable) —
the additional reds are the temp-permission pins, the two extra fault-
injection pins, the forced-casefold twin and the lock-leak meta-pin, which
are implementation-coupled (the base has no atomic write and no lock
registry at all). Only this file's own node count may be quoted as current;
the figures above are scope-bound measurements of OTHER revisions.
"""

import os
import stat
import threading
from pathlib import Path

import pytest

import daemon.tools.filesystem as fs
from daemon.tools.filesystem import edit_file, write_file

# Generous choreography timeouts: the test fails (rather than hangs) if an
# expected transition never happens. No sleeps anywhere — events only.
_TIMEOUT = 10.0

# Probe window for the case-variant choreography: how long the main thread
# waits to see whether the second writer COMPLETED before releasing the
# first one. In the locked world the second writer is blocked on the shared
# lock and cannot complete — the window expires and the first writer is
# released. In the un-locked world the second writer completes within
# milliseconds (a local tmpdir write), so the window is never the deciding
# factor there.
_CASE_PROBE_WINDOW = 1.5


# ---------------------------------------------------------------------------
# Thread helpers — no swallowed exceptions, no silent non-termination
# ---------------------------------------------------------------------------


def _spawn_worker(name: str, fn, *args) -> threading.Thread:
    """Start a worker thread that RECORDS any exception it raises.

    ``threading`` SWALLOWS exceptions raised inside a thread body: they are
    printed to stderr and the thread simply dies, so the test then fails on
    a missing result (``KeyError``) or on a wrong assertion — the real
    cause is lost. The recorded exception is re-raised on the main thread by
    ``_join_workers``.

    The thread is a DAEMON: a leaked-lock probe that parks forever must not
    keep the pytest process alive after the failure is reported.
    """
    def runner() -> None:
        try:
            fn(*args)
        except BaseException as exc:  # noqa: BLE001 - re-raised on the main thread
            thread.exception = exc

    thread = threading.Thread(target=runner, name=name, daemon=True)
    thread.exception = None  # type: ignore[attr-defined]
    thread.start()
    return thread


def _join_workers(threads: list[threading.Thread], timeout: float = _TIMEOUT) -> None:
    """Join the workers, assert they finished, and re-raise what they swallowed."""
    for thread in threads:
        thread.join(timeout=timeout)
    still_running = [t.name for t in threads if t.is_alive()]
    assert not still_running, (
        "worker threads did not finish within the timeout: "
        f"{still_running}"
    )
    for thread in threads:
        exc = getattr(thread, "exception", None)
        if exc is not None:
            raise AssertionError(
                f"worker thread {thread.name} raised: {exc!r}"
            ) from exc


def _probe_lock_not_leaked(call, timeout: float = _TIMEOUT) -> str:
    """Run ``call`` on a HELPER THREAD and prove the per-path lock was released.

    The per-path lock is a ``threading.RLock``: a retry on the SAME thread
    that held it re-acquires it SILENTLY, so a same-thread retry cannot
    distinguish "released" from "leaked" — it passes either way. The probe
    therefore crosses a thread boundary: a leaked lock parks the helper
    thread forever, which the timeout detects.
    """
    holder: dict[str, object] = {}

    def runner() -> None:
        try:
            holder["result"] = call()
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            holder["exception"] = exc

    thread = threading.Thread(target=runner, name="lock-leak-probe", daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    assert not thread.is_alive(), "per-path lock leaked"
    if "exception" in holder:
        raise AssertionError(f"lock-leak probe raised: {holder['exception']!r}")
    return str(holder.get("result", ""))


# ---------------------------------------------------------------------------
# 1. Same-file concurrency (the incident class)
# ---------------------------------------------------------------------------


class TestConcurrentSameFileEdits:
    def test_barrier_two_threads_both_replacements_present(self, tmp_path):
        """Two threads race two edit_file calls on ONE file (barrier start).

        Post-fix contract: BOTH replacements are in the final file and BOTH
        tools report SUCCESS. On the pre-fix base leg this exact scenario
        lost one replacement (demonstrated 2026-10-23 with a barrier placed
        after the read step: both tools returned SUCCESS while the final
        file contained only one of the two replacements).
        """
        target = tmp_path / "config.txt"
        target.write_text("alpha=1\nbeta=2\n", encoding="utf-8")

        start = threading.Barrier(2, timeout=_TIMEOUT)
        results: dict[int, str] = {}

        def call(idx: int, old: str, new: str) -> None:
            start.wait()
            results[idx] = edit_file.invoke(
                {"path": str(target), "old_string": old, "new_string": new}
            )

        t1 = _spawn_worker("edit-1", call, 1, "alpha=1", "alpha=ONE")
        t2 = _spawn_worker("edit-2", call, 2, "beta=2", "beta=TWO")
        _join_workers([t1, t2])

        # Positive needles: every replacement provably landed.
        assert results[1].startswith("SUCCESS"), results[1]
        assert results[2].startswith("SUCCESS"), results[2]
        final = target.read_text(encoding="utf-8")
        assert "alpha=ONE" in final, f"lost edit from thread 1: {final!r}"
        assert "beta=TWO" in final, f"lost edit from thread 2: {final!r}"

    def test_no_interleaved_read_during_another_threads_write(self, tmp_path, monkeypatch):
        """Serialization pin: a reader cannot slip in mid-write of another thread.

        Choreography (deterministic POST-FIX; base leg partly
        scheduler-dependent):
        - Thread 1 parks INSIDE its atomic write (lock held).
        - Thread 2 starts its edit_file while thread 1 is parked.
        - No-lock world: thread 2 reads the PRE-edit content, completes, and
          its later whole-file write erases thread 1's replacement.
        - Locked world (the fix): thread 2 blocks on the per-path lock until
          thread 1 finishes, then reads the post-edit content.
        """
        target = tmp_path / "serial.txt"
        target.write_text("one=1\ntwo=2\n", encoding="utf-8")

        in_write = threading.Event()
        release_write = threading.Event()
        real_atomic = fs._atomic_write_text

        def parked_atomic(write_target: Path, content: str) -> None:
            in_write.set()
            assert release_write.wait(timeout=_TIMEOUT), "choreography stalled"
            real_atomic(write_target, content)

        monkeypatch.setattr(fs, "_atomic_write_text", parked_atomic, raising=False)

        results: dict[str, str] = {}

        def edit1() -> None:
            results["t1"] = edit_file.invoke(
                {"path": str(target), "old_string": "one=1", "new_string": "one=ONE"}
            )

        def edit2() -> None:
            results["t2"] = edit_file.invoke(
                {"path": str(target), "old_string": "two=2", "new_string": "two=TWO"}
            )

        t1 = _spawn_worker("edit-1", edit1)
        assert in_write.wait(timeout=_TIMEOUT), "thread 1 never reached its write"

        t2 = _spawn_worker("edit-2", edit2)
        # Thread 2 is now either blocked on the lock (fixed world) or racing
        # ahead (base-leg world). Release thread 1 either way.
        release_write.set()
        _join_workers([t1, t2])

        assert results["t1"].startswith("SUCCESS"), results["t1"]
        assert results["t2"].startswith("SUCCESS"), results["t2"]
        final = target.read_text(encoding="utf-8")
        assert "one=ONE" in final, f"thread 1's edit lost: {final!r}"
        assert "two=TWO" in final, f"thread 2's edit lost: {final!r}"

    def test_thread_hammer_same_file_all_replacements_present(self, tmp_path):
        """12 threads, 12 disjoint single-occurrence needles, one file.

        Every SUCCESS must correspond to a replacement that is actually in
        the file afterwards — the phantom-success anti-contract.
        """
        target = tmp_path / "hammer.txt"
        n = 12
        target.write_text(
            "".join(f"line{i}=<v/>\n" for i in range(n)), encoding="utf-8"
        )

        start = threading.Barrier(n, timeout=_TIMEOUT)
        results: list[str] = [None] * n  # type: ignore[list-item]

        def hammer(i: int) -> None:
            start.wait()
            results[i] = edit_file.invoke(
                {
                    "path": str(target),
                    "old_string": f"line{i}=<v/>",
                    "new_string": f"line{i}=<fixed/>",
                }
            )

        threads = [
            _spawn_worker(f"hammer-{i}", hammer, i) for i in range(n)
        ]
        _join_workers(threads)

        final = target.read_text(encoding="utf-8")
        for i in range(n):
            assert results[i].startswith("SUCCESS"), (i, results[i])
            assert f"line{i}=<fixed/>" in final, f"thread {i}'s edit lost"
        assert "<v/>" not in final, "stale pre-edit content survived"


# ---------------------------------------------------------------------------
# 2. Atomic write + fault injection
# ---------------------------------------------------------------------------


class TestAtomicWrite:
    def test_fault_at_rename_preserves_original_and_cleans_temp(
        self, tmp_path, monkeypatch
    ):
        """Crash/exception AFTER temp write, BEFORE os.replace.

        The original file must be byte-identical afterwards and no temp
        residue may remain. Fault point: os.replace raises.
        """

        def boom(src: object, dst: object) -> None:
            raise OSError("injected: rename failed")

        monkeypatch.setattr(os, "replace", boom)

        target = tmp_path / "atomic.txt"
        original = "original content\n"
        target.write_text(original, encoding="utf-8")

        result = edit_file.invoke(
            {"path": str(target), "old_string": "original", "new_string": "edited"}
        )

        # Negative needle: no success was reported for a failed write.
        assert result.startswith("ERROR"), result
        assert "SUCCESS" not in result
        # The original is untouched.
        assert target.read_text(encoding="utf-8") == original
        # No temp residue.
        assert list(tmp_path.glob(".*.tmp-*")) == []

        # Lock-release pin: the per-path lock is held by a `with` block, so
        # the fault path must have released it. The probe runs on a HELPER
        # THREAD on purpose: the per-path lock is an RLock, so a retry on
        # the SAME thread that held it would silently re-acquire it and
        # could not tell "released" from "leaked" — it would pass either
        # way. A helper thread that cannot finish within the timeout proves
        # the lock leaked.
        monkeypatch.undo()
        retry = _probe_lock_not_leaked(
            lambda: edit_file.invoke(
                {"path": str(target), "old_string": "original", "new_string": "edited"}
            )
        )
        assert retry.startswith("SUCCESS"), (
            f"a subsequent write to the same path failed — the lock leaked: {retry}"
        )

    def test_fault_during_temp_write_preserves_original_and_cleans_temp(
        self, tmp_path, monkeypatch
    ):
        """Crash DURING the temp-file write (fsync raises before rename).

        Same contract: honest ERROR, original byte-identical, no residue.
        """

        def boom(fd: int) -> None:
            raise OSError("injected: fsync failed")

        monkeypatch.setattr(os, "fsync", boom)

        target = tmp_path / "atomic2.txt"
        original = "keep me intact\n"
        target.write_text(original, encoding="utf-8")

        result = edit_file.invoke(
            {"path": str(target), "old_string": "keep me", "new_string": "changed"}
        )

        assert result.startswith("ERROR"), result
        assert target.read_text(encoding="utf-8") == original
        assert list(tmp_path.glob(".*.tmp-*")) == []

        # Lock-release pin (see the rename-fault test above): the fault
        # path must have released the per-path lock. The probe runs on a
        # HELPER THREAD — an RLock re-acquires silently on the thread that
        # held it, so a same-thread retry cannot detect a leak.
        monkeypatch.undo()
        retry = _probe_lock_not_leaked(
            lambda: edit_file.invoke(
                {"path": str(target), "old_string": "keep me", "new_string": "changed"}
            )
        )
        assert retry.startswith("SUCCESS"), (
            f"a subsequent write to the same path failed — the lock leaked: {retry}"
        )

    def test_fault_at_temp_create_preserves_original_and_leaves_no_temp(
        self, tmp_path, monkeypatch
    ):
        """Fault BEFORE the temp file exists (the create raises).

        Cleanup branch A: the temp was NEVER created, so the cleanup unlink
        must tolerate its absence — and the original must be byte-identical.
        """
        target = tmp_path / "atomic_open.txt"
        original = "original content\n"
        target.write_text(original, encoding="utf-8")

        real_open = os.open

        def boom_open(path, flags, mode=0o777, **kwargs):
            if ".tmp-" in str(path):
                raise OSError("injected: temp create failed")
            return real_open(path, flags, mode, **kwargs)

        monkeypatch.setattr(os, "open", boom_open)

        result = edit_file.invoke(
            {"path": str(target), "old_string": "original", "new_string": "edited"}
        )

        assert result.startswith("ERROR"), result
        assert "SUCCESS" not in result
        assert target.read_text(encoding="utf-8") == original
        # The temp never existed — nothing to clean up, and the glob is empty.
        assert list(tmp_path.glob(".*.tmp-*")) == []

        # Lock-release pin (helper thread — an RLock re-acquires silently on
        # the thread that held it, so a same-thread retry cannot detect a
        # leak).
        monkeypatch.undo()
        retry = _probe_lock_not_leaked(
            lambda: edit_file.invoke(
                {"path": str(target), "old_string": "original", "new_string": "edited"}
            )
        )
        assert retry.startswith("SUCCESS"), (
            f"a subsequent write to the same path failed — the lock leaked: {retry}"
        )

    def test_fault_at_chmod_preserves_original_and_cleans_temp(
        self, tmp_path, monkeypatch
    ):
        """Fault at the final chmod (the temp EXISTS but is never swapped in).

        Cleanup branch B: the temp file was created, so the cleanup must
        REMOVE it — and the original must be byte-identical.
        """
        target = tmp_path / "atomic_chmod.txt"
        original = "keep me intact\n"
        target.write_text(original, encoding="utf-8")
        os.chmod(target, 0o600)

        real_chmod = os.chmod

        def boom_chmod(path, mode, **kwargs):
            if ".tmp-" in str(path):
                raise OSError("injected: chmod failed")
            return real_chmod(path, mode, **kwargs)

        monkeypatch.setattr(os, "chmod", boom_chmod)

        result = edit_file.invoke(
            {"path": str(target), "old_string": "keep me", "new_string": "changed"}
        )

        assert result.startswith("ERROR"), result
        assert "SUCCESS" not in result
        assert target.read_text(encoding="utf-8") == original
        # The temp existed — the cleanup must have removed it.
        assert list(tmp_path.glob(".*.tmp-*")) == []
        # The original's mode was not touched by the failed write.
        assert stat.S_IMODE(target.stat().st_mode) == 0o600

        # Lock-release pin (helper thread — see the rename-fault test).
        monkeypatch.undo()
        retry = _probe_lock_not_leaked(
            lambda: edit_file.invoke(
                {"path": str(target), "old_string": "keep me", "new_string": "changed"}
            )
        )
        assert retry.startswith("SUCCESS"), (
            f"a subsequent write to the same path failed — the lock leaked: {retry}"
        )

    def test_lock_leak_probe_detects_a_genuinely_held_lock(self, tmp_path):
        """Meta-pin: the leak probe is DISCRIMINATING.

        It holds the per-path lock on the MAIN thread and proves the
        helper-thread probe reports "per-path lock leaked". A same-thread
        retry could not do this — the RLock would be re-acquired silently
        and the retry would succeed whether the lock was released or not.
        """
        target = tmp_path / "leak_probe.txt"
        target.write_text("x=1\n", encoding="utf-8")

        _, lock = fs._canonical_path_lock(target)
        lock.acquire()
        try:
            with pytest.raises(AssertionError, match="per-path lock leaked"):
                # Short timeout: the lock is deliberately held, so the probe
                # is EXPECTED to time out — it must not wait the full
                # choreography timeout to report the leak.
                _probe_lock_not_leaked(
                    lambda: edit_file.invoke(
                        {
                            "path": str(target),
                            "old_string": "x=1",
                            "new_string": "x=2",
                        }
                    ),
                    timeout=1.0,
                )
        finally:
            lock.release()

    def test_successful_edit_leaves_no_temp_residue(self, tmp_path):
        target = tmp_path / "clean.txt"
        target.write_text("a=1\n", encoding="utf-8")

        result = edit_file.invoke(
            {"path": str(target), "old_string": "a=1", "new_string": "a=2"}
        )

        assert result.startswith("SUCCESS"), result
        assert list(tmp_path.glob(".*.tmp-*")) == []

    def test_edit_preserves_file_mode(self, tmp_path):
        target = tmp_path / "script.sh"
        target.write_text("echo hello\n", encoding="utf-8")
        os.chmod(target, 0o754)

        result = edit_file.invoke(
            {"path": str(target), "old_string": "hello", "new_string": "world"}
        )

        assert result.startswith("SUCCESS"), result
        assert stat.S_IMODE(target.stat().st_mode) == 0o754

    def test_write_file_new_file_mode_matches_open_default(self, tmp_path):
        """A NEW file's mode matches what open(path, 'w') would produce
        (0o666 & ~umask) — the os.open(0o666) creation applies the process
        umask in-kernel, no chmod needed on the new-file path."""
        target = tmp_path / "fresh.txt"
        mask = os.umask(0)
        os.umask(mask)  # read the umask without leaving it zeroed

        result = write_file.invoke({"content": "x\n", "path": str(target)})

        assert result.startswith("SUCCESS"), result
        assert stat.S_IMODE(target.stat().st_mode) == 0o666 & ~mask

    def test_edit_through_symlink_writes_target_not_the_link(self, tmp_path):
        """Write-through-symlink semantics are preserved: the atomic replace
        swaps the symlink TARGET's inode; the link itself survives as a link."""
        real = tmp_path / "real.txt"
        real.write_text("via link\n", encoding="utf-8")
        link = tmp_path / "link.txt"
        os.symlink(real, link)

        result = edit_file.invoke(
            {"path": str(link), "old_string": "via link", "new_string": "edited"}
        )

        assert result.startswith("SUCCESS"), result
        assert link.is_symlink(), "symlink was replaced by a regular file"
        assert real.read_text(encoding="utf-8") == "edited\n"


# ---------------------------------------------------------------------------
# 2b. Temp-file permissions (CWE-732 — never widen a secret target)
# ---------------------------------------------------------------------------


class TestAtomicWriteFileMode:
    """The temp file must never widen the target's permissions.

    Root cause (2026-10-23 review): the temp file was created with mode
    0o666 (=> 0o644 under the usual umask) and the preserved-mode chmod ran
    only AFTER the write + fsync. For a 0o600 target the new content was
    therefore group/world-readable in the target's own directory for the
    whole write+fsync window — a WIDENING of the baseline ``open(path, "w")``
    behaviour, which kept the target's 0o600.

    Fix: create the temp 0o600 (never 0o666) and apply the FINAL mode
    BEFORE the content fsync (chmod -> fsync -> replace), so the mode is
    durable with the content and a crash cannot revert it.
    """

    def test_temp_file_is_created_with_mode_0600(self, tmp_path, monkeypatch):
        """The CREATE mode is 0o600 — never 0o666 — and the file on disk
        carries no group/other permission bit while it exists."""
        target = tmp_path / "secret.txt"
        target.write_text("classified\n", encoding="utf-8")
        os.chmod(target, 0o600)

        real_open = os.open
        seen: dict[str, int] = {}

        def capturing_open(path, flags, mode=0o777, **kwargs):
            fd = real_open(path, flags, mode, **kwargs)
            if ".tmp-" in str(path):
                seen["create_mode_arg"] = mode
                seen["on_disk_mode"] = stat.S_IMODE(os.stat(path).st_mode)
            return fd

        monkeypatch.setattr(os, "open", capturing_open)

        result = edit_file.invoke(
            {
                "path": str(target),
                "old_string": "classified",
                "new_string": "declassified",
            }
        )

        assert result.startswith("SUCCESS"), result
        assert seen.get("create_mode_arg") == 0o600, (
            "temp file was created with mode "
            f"{seen.get('create_mode_arg')!r}, not 0o600 — the write window "
            "would expose a secret target to every user on the machine"
        )
        on_disk = seen.get("on_disk_mode", 0o777)
        assert on_disk & 0o077 == 0, (
            f"temp file was group/world accessible while it existed: {on_disk:#o}"
        )

    def test_temp_file_is_not_group_or_world_readable_during_the_write(
        self, tmp_path, monkeypatch
    ):
        """While the write is parked (before the swap), the temp file of a
        0o600 target must carry NO group/other permission bit.

        The write is parked at the content fsync — inside the fixed
        ordering the final chmod has already run there, so the mode
        observed is the FINAL mode of the temp file. On the pre-fix code
        the chmod ran AFTER the fsync, so the temp was still 0o644 at this
        point and this pin is RED.
        """
        target = tmp_path / "secret2.txt"
        target.write_text("classified\n", encoding="utf-8")
        os.chmod(target, 0o600)

        in_write = threading.Event()
        release_write = threading.Event()
        observed: dict[str, int] = {}
        real_fsync = os.fsync

        def parking_fsync(fd: int) -> None:
            # Only the FIRST fsync is the content fsync (the temp still
            # exists there); the second one is the best-effort DIRECTORY
            # fsync, which runs after the swap — the temp is gone by then.
            if not observed:
                temps = list(tmp_path.glob(".*.tmp-*"))
                assert len(temps) == 1, f"expected exactly one temp file: {temps}"
                observed["mode"] = stat.S_IMODE(temps[0].stat().st_mode)
                in_write.set()
                assert release_write.wait(timeout=_TIMEOUT), "choreography stalled"
            real_fsync(fd)

        monkeypatch.setattr(os, "fsync", parking_fsync)

        results: dict[str, str] = {}

        def do_edit() -> None:
            results["edit"] = edit_file.invoke(
                {
                    "path": str(target),
                    "old_string": "classified",
                    "new_string": "declassified",
                }
            )

        worker = _spawn_worker("mode-probe", do_edit)
        assert in_write.wait(timeout=_TIMEOUT), "the write never reached its fsync"
        release_write.set()
        _join_workers([worker])

        assert results["edit"].startswith("SUCCESS"), results["edit"]
        mode = observed.get("mode", 0o777)
        assert mode & 0o077 == 0, (
            "the temp file was group/world readable during the write "
            f"window: {mode:#o}"
        )

    def test_edit_preserves_0600_mode_on_the_final_file(self, tmp_path):
        """The swapped-in file keeps the 0o600 target's mode."""
        target = tmp_path / "secret3.txt"
        target.write_text("classified\n", encoding="utf-8")
        os.chmod(target, 0o600)

        result = edit_file.invoke(
            {
                "path": str(target),
                "old_string": "classified",
                "new_string": "declassified",
            }
        )

        assert result.startswith("SUCCESS"), result
        assert stat.S_IMODE(target.stat().st_mode) == 0o600

    def test_new_file_final_mode_is_umask_derived_not_the_temp_mode(
        self, tmp_path
    ):
        """A NEW file ends at the BASELINE mode (0o666 & ~umask).

        The 0o600 temp-creation mode is an INTERNAL step, not the file's
        final mode: the new file must land exactly where the baseline
        ``open(path, "w")`` would have put it.
        """
        target = tmp_path / "fresh_mode.txt"
        mask = os.umask(0)
        try:
            os.umask(mask)  # read the umask without leaving it zeroed
        finally:
            os.umask(mask)

        result = write_file.invoke({"content": "x\n", "path": str(target)})

        assert result.startswith("SUCCESS"), result
        final = stat.S_IMODE(target.stat().st_mode)
        assert final == 0o666 & ~mask, (
            f"new-file mode is {final:#o}, expected {0o666 & ~mask:#o}"
        )


# ---------------------------------------------------------------------------
# 3. Read-back verification (never phantom success)
# ---------------------------------------------------------------------------


class TestReadBackVerify:
    def test_edit_file_mismatch_reports_failure_not_success(self, tmp_path, monkeypatch):
        """Force the on-disk content to diverge from the intended post-edit
        content (simulating an out-of-process writer winning the race): the
        tool must return an honest failure, never SUCCESS."""

        def divergent_atomic(target: Path, content: str) -> None:
            # Land something OTHER than the intended content.
            target.write_text("DIVERGENT\n", encoding="utf-8")

        monkeypatch.setattr(fs, "_atomic_write_text", divergent_atomic, raising=False)

        target = tmp_path / "verify.txt"
        target.write_text("needle\n", encoding="utf-8")

        result = edit_file.invoke(
            {"path": str(target), "old_string": "needle", "new_string": "replacement"}
        )

        # Negative needle split from positive: the specific verify message…
        assert "Read-back verification failed" in result
        # …and definitively no success.
        assert not result.startswith("SUCCESS")
        assert "SUCCESS" not in result

    def test_write_file_mismatch_reports_failure_not_success(
        self, tmp_path, monkeypatch
    ):
        def divergent_atomic(target: Path, content: str) -> None:
            target.write_text("DIVERGENT\n", encoding="utf-8")

        monkeypatch.setattr(fs, "_atomic_write_text", divergent_atomic, raising=False)

        target = tmp_path / "verify2.txt"
        result = write_file.invoke({"content": "intended\n", "path": str(target)})

        assert "Read-back verification failed" in result
        assert "SUCCESS" not in result

    def test_append_mismatch_reports_failure_not_success(self, tmp_path, monkeypatch):
        """Append verify: the appended content must be the file's tail; a
        divergent read-back is an honest ERROR."""

        def lying_read(self: Path, *args: object, **kwargs: object) -> str:
            return ""  # tail does not match the appended content

        monkeypatch.setattr(Path, "read_text", lying_read)

        target = tmp_path / "verify3.txt"
        target.write_text("base\n", encoding="utf-8")

        result = write_file.invoke(
            {"content": "appended\n", "path": str(target), "append": True}
        )

        assert "Read-back verification failed" in result
        assert "SUCCESS" not in result


# ---------------------------------------------------------------------------
# 3b. Byte-exact read-back verification (CRLF)
# ---------------------------------------------------------------------------


class TestReadBackVerifyByteExact:
    """Read-back verification must be BYTE-exact, not newline-normalized.

    Root cause (2026-10-23): the verification re-read the file with
    ``Path.read_text(encoding="utf-8")`` — whose default ``newline=None``
    applies UNIVERSAL newlines and rewrites ``"\\r\\n"`` to ``"\\n"`` — while
    the comparison target was the EXACT string that had just been written.
    The write itself was byte-correct, but the normalized read no longer
    matched it, so a successful CRLF write returned a spurious
    "Read-back verification failed" ERROR (an honest-failure false negative).

    The fix reads back with ``newline=""`` (no translation), so the
    comparison is byte-exact for both the whole-file equality verify and the
    append tail-verify.
    """

    def test_write_file_crlf_content_succeeds(self, tmp_path):
        """Whole-file write of CRLF content: SUCCESS (the write was always
        byte-correct; only the verify was wrong)."""
        target = tmp_path / "crlf.txt"
        content = "alpha=1\r\nbeta=2\r\n"

        result = write_file.invoke({"content": content, "path": str(target)})

        assert result.startswith("SUCCESS"), result
        # Byte-exact on disk.
        assert target.read_bytes() == content.encode("utf-8")

    def test_append_crlf_tail_succeeds(self, tmp_path):
        """Append whose appended text ends with CRLF: SUCCESS (tail-verify)."""
        target = tmp_path / "crlf_append.txt"
        target.write_bytes(b"base\n")

        result = write_file.invoke(
            {"content": "x\r\ny\r\n", "path": str(target), "append": True}
        )

        assert result.startswith("SUCCESS"), result
        assert target.read_bytes() == b"base\nx\r\ny\r\n"

    def test_crlf_on_disk_against_lf_intent_reports_honest_failure(
        self, tmp_path, monkeypatch
    ):
        """Byte-exactness cuts both ways: a CR/LF drift on disk is a REAL
        mismatch and must fail honestly.

        This is the discriminating pin for the honest-failure direction — the
        OLD newline-normalizing compare read ``b"alpha=1\\r\\n"`` back as
        ``"alpha=1\\n"``, called it equal to the LF-intended content, and
        reported a phantom SUCCESS; the byte-exact compare catches the drift.
        """

        def crlf_on_disk_atomic(target: Path, content: str) -> None:
            target.write_bytes(b"alpha=1\r\n")

        monkeypatch.setattr(
            fs, "_atomic_write_text", crlf_on_disk_atomic, raising=False
        )

        target = tmp_path / "drift.txt"
        result = write_file.invoke({"content": "alpha=1\n", "path": str(target)})

        assert "Read-back verification failed" in result
        assert "SUCCESS" not in result


# ---------------------------------------------------------------------------
# 3c. edit_file CRLF semantics (2026-10-23 reviewer fix)
# ---------------------------------------------------------------------------


class TestEditFileCrlfSemantics:
    """edit_file must not report a false ERROR on a byte-correct CRLF write.

    Root cause (2026-10-23): the post-write verification re-read the file
    with the default universal-newline translation (``newline=None`` folds
    ``"\r\n"`` -> ``"\n"``) while the comparison target was the EXACT
    string that had just been written. The write was byte-correct, but the
    normalized re-read no longer matched it, so a correct CRLF edit
    returned a spurious "Read-back verification failed" ERROR — an
    honest-failure FALSE NEGATIVE.

    The recovery trap that made the false ERROR expensive: the write DID
    land, so the caller's retry found the needle already replaced and
    reported "String not found" — an error pointing at the WRONG cause.
    The first call must therefore report SUCCESS.
    """

    def test_edit_file_crlf_new_string_succeeds_and_is_byte_exact(self, tmp_path):
        """CRLF-only new_string: SUCCESS on the first call and byte-exact
        on-disk bytes."""
        target = tmp_path / "crlf_edit.txt"
        target.write_bytes(b"alpha=1")

        new_string = "ALPHA=1\r\nX=9\r\n"
        result = edit_file.invoke(
            {"path": str(target), "old_string": "alpha=1", "new_string": new_string}
        )

        assert result.startswith("SUCCESS"), result
        assert target.read_bytes() == new_string.encode("utf-8")

    def test_edit_file_crlf_new_string_on_crlf_file_is_byte_exact(self, tmp_path):
        """CRLF file + CRLF-only new_string: SUCCESS, and the bytes on disk
        are the intended post-edit content.

        NOTE the untouched line: the whole-file rewrite folds the file's
        pre-existing CRLF to LF (universal-newline read — the historical
        semantics the pre-fix baseline had). The pin states that behaviour
        explicitly so a future "preserve CRLF" change is a DELIBERATE,
        visible change rather than a silent one.
        """
        target = tmp_path / "crlf_edit2.txt"
        target.write_bytes(b"alpha=1\r\nbeta=2\r\n")

        new_string = "ALPHA=1\r\nX=9\r\n"
        result = edit_file.invoke(
            {"path": str(target), "old_string": "alpha=1", "new_string": new_string}
        )

        assert result.startswith("SUCCESS"), result
        assert target.read_bytes() == b"ALPHA=1\r\nX=9\r\n\nbeta=2\n"

    def test_edit_file_crlf_recovery_trap_is_not_reachable(self, tmp_path):
        """The false-ERROR-then-retry trap must be unreachable.

        First call: SUCCESS (the write landed). Identical retry: the
        needle is gone, so the PRE-EXISTING "String not found" semantics
        apply — never a second "Read-back verification failed".
        """
        target = tmp_path / "crlf_edit3.txt"
        target.write_bytes(b"alpha=1\r\nbeta=2\r\n")

        args = {
            "path": str(target),
            "old_string": "alpha=1",
            "new_string": "ALPHA=1\r\nX=9\r\n",
        }
        first = edit_file.invoke(dict(args))
        assert first.startswith("SUCCESS"), first
        assert target.read_bytes() == b"ALPHA=1\r\nX=9\r\n\nbeta=2\n"

        retry = edit_file.invoke(dict(args))
        assert retry == "ERROR: String not found in file: alpha=1", retry
        assert "Read-back verification failed" not in retry

    def test_edit_file_lf_needle_still_matches_a_crlf_file(self, tmp_path):
        """Needle-matching semantics are UNCHANGED by the verify fix.

        The pre-edit read keeps the universal-newline translation, so an
        ``old_string`` of ``"alpha=1\n"`` still matches a CRLF file (the
        historical behaviour). Reading the needle buffer raw instead would
        turn this into "String not found" — a measured regression, which
        the fix deliberately avoids.
        """
        target = tmp_path / "crlf_edit4.txt"
        target.write_bytes(b"alpha=1\r\nbeta=2\r\n")

        result = edit_file.invoke(
            {"path": str(target), "old_string": "alpha=1\n", "new_string": "ALPHA=1\n"}
        )

        assert result.startswith("SUCCESS"), result
        assert target.read_bytes() == b"ALPHA=1\nbeta=2\n"

    def test_crlf_needle_on_crlf_file_is_not_found_and_leaves_bytes_unchanged(
        self, tmp_path
    ):
        """Complement of the LF-needle pin: a CRLF-bearing needle against a
        CRLF file does NOT match — the needle buffer is FOLDED (universal
        newlines), so "\\r\\n" is not in the folded text — and the file's
        bytes are left UNCHANGED (the edit returns before any write).

        This is the INHERITED fold semantics (the pre-fix baseline folded
        the same way). The pin exists so that a future "match raw" change
        is a DELIBERATE, visible change rather than a silent one.
        """
        target = tmp_path / "crlf_edit5.txt"
        original = b"alpha=1\r\nbeta=2\r\n"
        target.write_bytes(original)

        result = edit_file.invoke(
            {
                "path": str(target),
                "old_string": "alpha=1\r\n",
                "new_string": "ALPHA=1\r\n",
            }
        )

        assert result.startswith("ERROR: String not found in file:"), result
        assert "SUCCESS" not in result
        assert target.read_bytes() == original


# ---------------------------------------------------------------------------
# 4. Different files stay parallel (no over-serialization)
# ---------------------------------------------------------------------------


class TestParallelDifferentFiles:
    def test_different_files_edit_concurrently_both_succeed(self, tmp_path):
        file_a = tmp_path / "a.txt"
        file_b = tmp_path / "b.txt"
        file_a.write_text("fa=1\n", encoding="utf-8")
        file_b.write_text("fb=1\n", encoding="utf-8")

        start = threading.Barrier(2, timeout=_TIMEOUT)
        results: dict[str, str] = {}

        def edit(key: str, target: Path, old: str, new: str) -> None:
            start.wait()
            results[key] = edit_file.invoke(
                {"path": str(target), "old_string": old, "new_string": new}
            )

        t1 = _spawn_worker("edit-a", edit, "a", file_a, "fa=1", "fa=2")
        t2 = _spawn_worker("edit-b", edit, "b", file_b, "fb=1", "fb=2")
        _join_workers([t1, t2])

        assert results["a"].startswith("SUCCESS"), results["a"]
        assert results["b"].startswith("SUCCESS"), results["b"]
        assert file_a.read_text(encoding="utf-8") == "fa=2\n"
        assert file_b.read_text(encoding="utf-8") == "fb=2\n"

    def test_different_files_are_not_globally_serialized(self, tmp_path, monkeypatch):
        """Throughput pin: file B's write must be able to ENTER while file A's
        write is parked. With a global lock this choreography would time out
        waiting for B; with per-path locks both proceed. Deterministic."""
        file_a = tmp_path / "pa.txt"
        file_b = tmp_path / "pb.txt"
        file_a.write_text("a\n", encoding="utf-8")
        file_b.write_text("b\n", encoding="utf-8")

        a_entered = threading.Event()
        b_entered = threading.Event()
        release_a = threading.Event()
        real_atomic = fs._atomic_write_text

        def choreographed_atomic(target: Path, content: str) -> None:
            if target.name == "pa.txt":
                a_entered.set()
                assert release_a.wait(timeout=_TIMEOUT), "choreography stalled"
            else:
                b_entered.set()
            real_atomic(target, content)

        monkeypatch.setattr(fs, "_atomic_write_text", choreographed_atomic, raising=False)

        results: dict[str, str] = {}

        def edit(key: str, target: Path, old: str, new: str) -> None:
            results[key] = edit_file.invoke(
                {"path": str(target), "old_string": old, "new_string": new}
            )

        t1 = _spawn_worker("edit-a", edit, "a", file_a, "a", "a2")
        assert a_entered.wait(timeout=_TIMEOUT), "thread A never reached its write"

        t2 = _spawn_worker("edit-b", edit, "b", file_b, "b", "b2")
        # The pin: B reaches its write while A is still parked.
        entered_b = b_entered.wait(timeout=_TIMEOUT)
        release_a.set()
        _join_workers([t1, t2])

        assert entered_b, (
            "file B's write never entered while file A's write was parked — "
            "writes appear globally serialized (over-serialization regression)"
        )
        assert results["a"].startswith("SUCCESS"), results["a"]
        assert results["b"].startswith("SUCCESS"), results["b"]

    def test_lock_registry_keys_on_canonical_path(self, tmp_path):
        """Two spellings of the same file (via a symlink) share ONE lock —
        the registry keys on realpath, not on the caller's spelling."""
        registry = getattr(fs, "_PATH_WRITE_LOCKS", None)
        if registry is None:
            pytest.skip("lock registry not present on the pre-fix base leg")
        real = tmp_path / "canon.txt"
        real.write_text("x=1\n", encoding="utf-8")
        link = tmp_path / "alias.txt"
        os.symlink(real, link)

        _, lock_via_real = fs._canonical_path_lock(real)
        _, lock_via_link = fs._canonical_path_lock(link)

        assert lock_via_real is lock_via_link, (
            "symlinked spelling of the same file got a different lock"
        )
        # Bounded-registry sanity: one entry for this file.
        keys = [k for k in registry if k.endswith("canon.txt")]
        assert len(keys) == 1


# ---------------------------------------------------------------------------
# 5. write_file same-class discipline
# ---------------------------------------------------------------------------


class TestWriteFileConcurrency:
    def test_concurrent_whole_writes_final_content_is_one_writers_content(
        self, tmp_path
    ):
        """INVARIANCE pin (NOT a regression detector): two whole-file writes
        race the same path and the final content must be EXACTLY one
        writer's content — never a torn interleaving of the two.

        What it really proves: the on-disk file is never a byte-level
        mixture of two writers. It does NOT prove the race is fixed — it
        passes on the un-fixed base too (8/8 runs), because the pre-fix
        baseline's last-writer-wins whole-file write also produced one
        writer's content. The lost-update class is pinned by the
        read-modify-write tests (edit_file), not by this one.
        """
        target = tmp_path / "whole.txt"
        content_a = "AAAA\n" * 100
        content_b = "BBBB\n" * 100

        start = threading.Barrier(2, timeout=_TIMEOUT)
        results: dict[str, str] = {}

        def write(key: str, content: str) -> None:
            start.wait()
            results[key] = write_file.invoke({"content": content, "path": str(target)})

        t1 = _spawn_worker("write-a", write, "a", content_a)
        t2 = _spawn_worker("write-b", write, "b", content_b)
        _join_workers([t1, t2])

        assert results["a"].startswith("SUCCESS"), results["a"]
        assert results["b"].startswith("SUCCESS"), results["b"]
        final = target.read_text(encoding="utf-8")
        assert final in (content_a, content_b), "torn write: mixed content on disk"

    def test_edit_and_append_same_file_both_effects_present(self, tmp_path, monkeypatch):
        """Cross-tool race: edit_file + write_file(append=True) on ONE file.

        The append must not build on a pre-edit snapshot, and must not be
        clobbered by a concurrent edit (same lost-update class).

        Choreography (deterministic POST-FIX; base leg partly
        scheduler-dependent):
        - Thread A (the edit) parks RIGHT AFTER its pre-edit read — it
          holds the per-path lock (fixed leg) and has already read the
          pre-edit content.
        - Thread B (the append) runs while A is parked. In the locked
          world B blocks on the shared lock; in the un-locked world B
          appends to the pre-edit snapshot.
        - A is released and writes its whole-file content. In the locked
          world B then appends AFTER A (both effects present); in the
          un-locked world A's whole-file write CLOBBERS B's append (the
          append is lost) while BOTH calls report SUCCESS.

        The park point is the edit's pre-edit READ — the only seam that
        exists in both the fixed and the pre-fix implementation (the
        pre-fix code had no ``_atomic_write_text`` to park inside).
        """
        target = tmp_path / "mix.txt"
        target.write_text("head\n", encoding="utf-8")

        after_read = threading.Event()
        release_edit = threading.Event()
        append_done = threading.Event()
        real_read_text = Path.read_text
        parked: dict[str, str] = {}

        def parked_read_text(self: Path, *args: object, **kwargs: object) -> str:
            content = real_read_text(self, *args, **kwargs)
            if (
                threading.current_thread().name == "edit-writer"
                and "content" not in parked
            ):
                parked["content"] = content
                after_read.set()
                assert release_edit.wait(timeout=_TIMEOUT), "choreography stalled"
            return content

        monkeypatch.setattr(Path, "read_text", parked_read_text, raising=False)

        results: dict[str, str] = {}

        def do_edit() -> None:
            results["edit"] = edit_file.invoke(
                {"path": str(target), "old_string": "head", "new_string": "HEAD"}
            )

        def do_append() -> None:
            results["append"] = write_file.invoke(
                {"content": "tail-appended\n", "path": str(target), "append": True}
            )
            append_done.set()

        ta = _spawn_worker("edit-writer", do_edit)
        assert after_read.wait(timeout=_TIMEOUT), "the edit never reached its read"
        # The edit has read the pre-edit content; it is parked holding the
        # lock (fixed leg) or about to write over it (pre-fix leg).
        assert parked["content"] == "head\n", parked

        tb = _spawn_worker("appender", do_append)
        # Probe: the append can only COMPLETE if it was not blocked on the
        # edit's lock. In the locked world it cannot, so the window expires
        # and the edit is released.
        append_done.wait(timeout=_CASE_PROBE_WINDOW)
        release_edit.set()
        _join_workers([ta, tb])

        assert results["edit"].startswith("SUCCESS"), results["edit"]
        assert results["append"].startswith("SUCCESS"), results["append"]
        final = target.read_text(encoding="utf-8")
        assert "HEAD" in final, f"edit lost to concurrent append: {final!r}"
        assert "tail-appended" in final, f"append lost to concurrent edit: {final!r}"

    def test_append_creates_missing_file(self, tmp_path):
        target = tmp_path / "new-append.txt"
        result = write_file.invoke(
            {"content": "first\n", "path": str(target), "append": True}
        )
        assert result.startswith("SUCCESS"), result
        assert target.read_text(encoding="utf-8") == "first\n"


# ---------------------------------------------------------------------------
# 7. Case-variant spellings share ONE lock (2026-10-23 reviewer fix)
# ---------------------------------------------------------------------------


def _filesystem_is_case_insensitive(directory: Path) -> bool:
    """True when the filesystem under ``directory`` resolves differently
    cased names to the SAME file (macOS APFS / HFS+ default volumes)."""
    probe = directory / "CaseProbe.txt"
    probe.write_text("probe\n", encoding="utf-8")
    return (directory / "caseprobe.txt").exists()


class TestCaseVariantLocking:
    """Two spellings of one file must share ONE per-path lock.

    Root cause (2026-10-23, live-reproduced on Darwin): the lock registry
    keyed on ``str(Path(os.path.realpath(path)))``. ``realpath`` resolves
    symlinks but does NOT normalize case, so on a case-insensitive volume
    "Config.txt" and "config.txt" — ONE inode, two spellings — received
    TWO locks, ran concurrently, and one edit was silently lost while
    BOTH calls reported SUCCESS (170/400 live reproductions).
    """

    def test_case_variant_spellings_share_one_lock(self, tmp_path):
        """Twin of the symlink pin: two spellings of one file, ONE lock."""
        if not _filesystem_is_case_insensitive(tmp_path):
            pytest.skip(
                "filesystem is case-sensitive — the case-normalized lock key "
                "does not apply here"
            )

        target = tmp_path / "Config.txt"
        target.write_text("alpha=1\nbeta=2\n", encoding="utf-8")
        variant = tmp_path / "config.txt"

        _, lock_via_upper = fs._canonical_path_lock(target)
        _, lock_via_lower = fs._canonical_path_lock(variant)

        assert lock_via_upper is lock_via_lower, (
            "case-variant spellings of the same file got different locks"
        )

    def test_casefold_lock_key_shared_when_case_insensitivity_is_forced(
        self, tmp_path, monkeypatch
    ):
        """Cross-platform twin of the pin above (Linux CI coverage).

        The production constant is ``sys.platform == "darwin"`` — an OS-level
        PROXY for a filesystem capability. On a case-SENSITIVE filesystem the
        two live case-variant pins SKIP, so the case-folded lock key had
        ZERO coverage on Linux. This twin forces the flag ON and proves the
        registry key IS case-folded: the upper and the lower spelling map to
        the SAME lock object.

        The constant is read at CALL time inside ``_lock_key`` (it is not
        cached at import), which is exactly what makes this monkeypatch
        effective — the pin would FAIL if the value were captured at module
        import time.

        Note: on a case-SENSITIVE filesystem the two spellings are two
        DISTINCT files, so sharing one lock is over-serialization
        (throughput loss only, no correctness loss) — the accepted
        trade-off of the case-folded key.
        """
        monkeypatch.setattr(fs, "_LOCK_KEY_CASE_INSENSITIVE", True)

        upper = tmp_path / "Config.txt"
        lower = tmp_path / "config.txt"

        _, lock_via_upper = fs._canonical_path_lock(upper)
        _, lock_via_lower = fs._canonical_path_lock(lower)

        assert lock_via_upper is lock_via_lower, (
            "the case-folded lock key did not map the two spellings to one "
            "lock — _lock_key is not reading _LOCK_KEY_CASE_INSENSITIVE at "
            "call time"
        )

    def test_case_variant_edits_do_not_lose_updates(self, tmp_path, monkeypatch):
        """Two barrier-synchronized edits through the two spellings of ONE
        file: both edits must land, and both calls must report honestly.

        Choreography (deterministic POST-FIX; base leg partly
        scheduler-dependent):
        - Thread A parks INSIDE its atomic write — it has already READ the
          pre-edit content and holds the lock.
        - Thread B (the other spelling) starts only after that park, so B
          necessarily reads the PRE-edit snapshot.
        - Locked world (the fix): B blocks on the SHARED lock until A
          finishes, then reads the post-edit content — both edits land.
        - Un-locked world (the bug): B reads the same pre-edit snapshot,
          writes its own version, and A's later write clobbers B's edit —
          a lost update with both calls reporting SUCCESS.
        """
        if not _filesystem_is_case_insensitive(tmp_path):
            pytest.skip(
                "filesystem is case-sensitive — the case-normalized lock key "
                "does not apply here"
            )

        upper = tmp_path / "Config.txt"
        lower = tmp_path / "config.txt"
        upper.write_text("alpha=1\nbeta=2\n", encoding="utf-8")

        start = threading.Barrier(2, timeout=_TIMEOUT)
        in_write = threading.Event()
        release_write = threading.Event()
        done_b = threading.Event()
        real_atomic = fs._atomic_write_text

        def parking_atomic(write_target: Path, content: str) -> None:
            # Park ONLY thread A's write (identified by its content) so the
            # choreography cannot depend on which thread reaches the write
            # first.
            if "alpha=ONE" in content:
                in_write.set()
                assert release_write.wait(timeout=_TIMEOUT), "choreography stalled"
            real_atomic(write_target, content)

        monkeypatch.setattr(fs, "_atomic_write_text", parking_atomic, raising=False)

        results: dict[str, str] = {}

        def edit_upper() -> None:
            start.wait()
            results["upper"] = edit_file.invoke(
                {
                    "path": str(upper),
                    "old_string": "alpha=1",
                    "new_string": "alpha=ONE",
                }
            )

        def edit_lower() -> None:
            start.wait()
            # Start only once thread A is parked INSIDE its write: A has
            # then read the pre-edit content, so the lost update is
            # deterministic in the un-locked world.
            assert in_write.wait(timeout=_TIMEOUT), "thread A never reached its write"
            results["lower"] = edit_file.invoke(
                {
                    "path": str(lower),
                    "old_string": "beta=2",
                    "new_string": "beta=TWO",
                }
            )
            done_b.set()

        ta = _spawn_worker("case-upper", edit_upper)
        tb = _spawn_worker("case-lower", edit_lower)
        # B may complete only if it was NOT blocked on A's lock; in the
        # locked world it cannot, so the probe window expires and A is
        # released.
        done_b.wait(timeout=_CASE_PROBE_WINDOW)
        release_write.set()
        _join_workers([ta, tb])

        # Both calls must report an honest result shape.
        assert results["upper"].startswith("SUCCESS"), results["upper"]
        assert results["lower"].startswith("SUCCESS"), results["lower"]
        final = upper.read_text(encoding="utf-8")
        assert "alpha=ONE" in final, f"thread A's edit lost: {final!r}"
        assert "beta=TWO" in final, f"thread B's edit lost: {final!r}"


# ---------------------------------------------------------------------------
# 6. Unchanged tool semantics (shapes, errors)
# ---------------------------------------------------------------------------


class TestSemanticsUnchanged:
    def test_edit_file_success_shape_unchanged(self, tmp_path):
        target = tmp_path / "shape.txt"
        target.write_text("v=1\n", encoding="utf-8")
        result = edit_file.invoke(
            {"path": str(target), "old_string": "v=1", "new_string": "v=2"}
        )
        assert result == f"SUCCESS: Replaced 1 occurrence in {target}"

    def test_edit_file_replace_all_success_shape_unchanged(self, tmp_path):
        target = tmp_path / "shape2.txt"
        target.write_text("x\nx\nx\n", encoding="utf-8")
        result = edit_file.invoke(
            {
                "path": str(target),
                "old_string": "x",
                "new_string": "y",
                "replace_all": True,
            }
        )
        assert result == f"SUCCESS: Replaced all 3 occurrences in {target}"
        assert target.read_text(encoding="utf-8") == "y\ny\ny\n"

    def test_edit_file_not_found_error_unchanged(self, tmp_path):
        target = tmp_path / "shape3.txt"
        target.write_text("content\n", encoding="utf-8")
        result = edit_file.invoke(
            {"path": str(target), "old_string": "missing-needle", "new_string": "z"}
        )
        assert result == "ERROR: String not found in file: missing-needle"

    def test_edit_file_multiple_occurrence_error_unchanged(self, tmp_path):
        target = tmp_path / "shape4.txt"
        target.write_text("dup\ndup\n", encoding="utf-8")
        result = edit_file.invoke(
            {"path": str(target), "old_string": "dup", "new_string": "unique"}
        )
        assert result == (
            "ERROR: String found 2 times. Use replace_all=True to replace all occurrences."
        )

    def test_write_file_success_shapes_unchanged(self, tmp_path):
        target = tmp_path / "shape5.txt"
        result = write_file.invoke({"content": "a\n", "path": str(target)})
        assert result == f"SUCCESS: Written to {target}"

        result = write_file.invoke(
            {"content": "b\n", "path": str(target), "append": True}
        )
        assert result == f"SUCCESS: Appended to {target}"
        assert target.read_text(encoding="utf-8") == "a\nb\n"

    def test_edit_file_missing_file_error_unchanged(self, tmp_path):
        result = edit_file.invoke(
            {
                "path": str(tmp_path / "nope.txt"),
                "old_string": "a",
                "new_string": "b",
            }
        )
        assert result.startswith("ERROR: File does not exist:")
