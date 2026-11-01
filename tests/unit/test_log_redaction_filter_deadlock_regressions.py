"""KMS redaction deadlock regressions — v0.28.3 fix (2026-11-01).

Regression coverage for the v0.28.3 whole-process freeze (63-thread
py-spy dump, pid 518745). VERIFIED mechanism — single-thread
RE-ENTRANCY, not ABBA (the dump shows no second lock cycle; thread
519948 holds AND waits on the same ``_registry_lock``):

* ``kms_lite._KMSStore._iter_registered_plaintexts`` acquired a
  NON-reentrant ``threading.Lock`` inside the logging emit path on
  EVERY record (the lock+copy ran before any empty-registry early
  return). On thread 519948 the GC fired mid-snapshot
  (``list(self._plaintext_registry)``): an httpx client ``__del__``
  closed its connections, their trace hook emitted a debug record, the
  handler-level ``KMSRedactionFilter`` re-entered the SAME read, and
  the read re-acquired the lock it already held → self-deadlock. The
  11 threads queued behind it took the whole process down (main event
  loop included → ``accept()`` stopped → 486-conn backlog).
* ``KMSRedactionFilter._lock`` (around ``record.getMessage()``) was a
  second, identical re-entrancy site.

The fix contract implemented here:

1. **Lock-free read path** — copy-on-write immutable-tuple registry;
   the write-side lock wraps only the read-merge-publish.
2. **``KMSRedactionFilter._lock`` removed.**
3. **Per-record hygiene** — no blocking-lock acquire, no import
   machinery, no ``sys.modules`` mutation on the per-record path of
   BOTH files; pinned statically (AST, content-anchored — NEVER
   lineno-exact, per repo test conventions) and behaviorally.

Test buckets:

1. **Re-entrancy** — nested emission from inside the snapshot read
   (the dump's exact mechanism, made deterministic) and from inside
   ``record.getMessage()``. Each emission runs in a worker thread
   guarded by join-with-timeout: on unpatched code the guard fires
   BOUNDED (fast, loud fail) instead of hanging the suite; the global
   pytest-timeout (30s, thread) is the backstop. These must FAIL
   pre-fix and PASS post-fix.
2. **Hygiene pins** — static AST pins against both files + a
   behavioral ``sys.modules``-stability probe + a once-latch probe of
   ``_resolve_get_store``.
3. **COW correctness** — concurrent mint + iterate + emit smoke: no
   lost entries, snapshot consistency, monotone growth, no exceptions.

Constraints:

* No PG / no live DB connections. The deadlock mechanism needs no
  database. No daemon boot. No ambient ``POSTGRES_*`` reads.
* Logging convention 2026-10-31: tests emit DEBUG-level records on
  throwaway loggers only — no per-event INFO anywhere.

Runnable in isolation via:

    uv run python -m pytest tests/unit/test_log_redaction_filter_deadlock_regressions.py -q
"""

from __future__ import annotations

import ast
import logging
import sys
import threading
from pathlib import Path
from typing import Iterator

import pytest
from cryptography.fernet import Fernet

from daemon.services import kms_lite
from daemon.services.kms_lite import reset_store_for_tests
from daemon.util import log_redaction_filter as lrf
from daemon.util.log_redaction_filter import (
    KMSRedactionFilter,
    SENTINEL,
    iter_registered_plaintexts,
)

#: Seconds before a re-entrancy probe declares a deadlock. Must stay
#: WELL under the global pytest-timeout (30s) so an unpatched code
#: path fails fast and bounded instead of hanging the suite.
_DEADLOCK_PROBE_TIMEOUT_S = 10.0

_REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Fixtures — test isolation for store + registry + logging handlers
# ---------------------------------------------------------------------------


@pytest.fixture
def fernet_key(monkeypatch: pytest.MonkeyPatch) -> str:
    """Provision a valid Fernet key in the env for the test duration."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("SYSTEM_ENCRYPTION_KEY", key)
    return key


@pytest.fixture(autouse=True)
def _reset_kms_state() -> Iterator[None]:
    """Reset KMS store + plaintext registry between tests.

    Mirrors tests/unit/test_log_redaction_filter.py. A pre-fix A/B run
    that deadlocked a worker thread poisons the OLD store's registry
    lock; resetting the store swaps in a fresh instance so later tests
    are unaffected (the poisoned thread is a daemon thread and dies
    with the process).
    """
    reset_store_for_tests()
    yield
    reset_store_for_tests()


@pytest.fixture(autouse=True)
def _detach_added_root_handlers() -> Iterator[None]:
    """Remove + close any handlers a test added (state isolation).

    Covers the root logger AND every named logger in the manager dict
    (emission tests attach their buffer handler to dedicated named
    loggers, non-propagating, so an abandoned pre-fix worker's poisoned
    lock never reaches another test's handler chain).
    """
    root = logging.getLogger()

    def _handlers_now() -> dict[logging.Logger, set[logging.Handler]]:
        snapshot: dict[logging.Logger, set[logging.Handler]] = {
            root: set(root.handlers)
        }
        manager_dict = getattr(root.manager, "loggerDict", {})
        for logger_obj in manager_dict.values():
            if isinstance(logger_obj, logging.Logger):
                snapshot[logger_obj] = set(logger_obj.handlers)
        return snapshot

    before = _handlers_now()
    yield
    for logger_obj, current in _handlers_now().items():
        for handler in current - before.get(logger_obj, set()):
            try:
                handler.close()
            except Exception:
                pass
            try:
                logger_obj.removeHandler(handler)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _BufferHandler(logging.Handler):
    """Capture emitted records into a string buffer for assertions."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self._buffer: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self._buffer.append(self.format(record))

    @property
    def captured(self) -> str:
        return "".join(self._buffer)

    @property
    def record_count(self) -> int:
        return len(self._buffer)


def _attach_buffer_with_private_filter(
    handler: _BufferHandler, logger_: logging.Logger
) -> None:
    """Attach ``handler`` to ``logger_`` with a PRIVATE filter instance.

    Three deliberate isolation properties:

    * The daemon ``addHandler`` patch auto-installs the filter
      SINGLETON on every new handler — asserted here (coverage live),
      then swapped out for a fresh ``KMSRedactionFilter``. The
      mechanism under test is the filter CLASS's per-record path;
      instance identity is irrelevant to it, and the filter still runs
      at handler level inside ``handle()``.
    * The handler goes on a DEDICATED named logger (not root), so the
      emission path exercises this test's handler only.
    * ``propagate`` is turned OFF: a pre-fix (A/B) deadlock abandons a
      worker thread holding its private filter's lock — but with
      propagation the abandoned worker's nested re-emission (and every
      later test's emission) also traverses ROOT handlers, which
      include pytest-internal handlers carrying the filter SINGLETON.
      One abandoned worker would then poison the shared singleton
      ``_lock`` for the rest of the session (probe-proven during
      authoring). Non-propagation keeps every poisoned lock
      instance-scoped to the test that deadlocked it.
    """
    logger_.addHandler(handler)
    flt_names = [f.name for f in handler.filters]
    assert "kms_redaction_filter" in flt_names, (
        f"KMS redaction filter missing on handler (addHandler patch "
        f"regression?): {flt_names!r}"
    )
    singleton = lrf._get_filter_singleton()
    handler.filters[:] = [f for f in handler.filters if f is not singleton]
    assert singleton not in handler.filters, "singleton swap failed"
    handler.addFilter(KMSRedactionFilter())
    logger_.setLevel(logging.DEBUG)
    logger_.propagate = False


def _run_bounded(fn, timeout_s: float = _DEADLOCK_PROBE_TIMEOUT_S):
    """Run ``fn`` in a daemon worker thread bounded by join-with-timeout.

    Returns ``(hung, error)``: ``hung`` is True when the thread was
    still alive after ``timeout_s`` — i.e. the unpatched deadlock
    reproduced. The abandoned thread holds only locks local to this
    test's handler/store, and the autouse store reset plus process
    lifetime contain it.
    """
    box: dict[str, str | None] = {"error": None}

    def _target() -> None:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 — probe must see anything
            box["error"] = f"{type(exc).__name__}: {exc}"

    worker = threading.Thread(
        target=_target, name="kms-deadlock-regression-probe", daemon=True
    )
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        return True, None
    return False, box["error"]


class _NestedEmitOnceOnIterate:
    """Duck-typed registry container that emits one log from ``__iter__``.

    Deterministic stand-in for the production trigger: on v0.28.3 a GC
    ``__del__`` finalizer logged from INSIDE the locked snapshot copy
    (py-spy thread 519948: ``__del__ (_base_client.py:838)`` framed
    between ``kms_lite.py:343`` and the nested ``filter`` frames).
    Real GC timing is nondeterministic; this hook is not. Emits exactly
    once so the post-fix nested re-entry terminates.
    """

    def __init__(self, logger_: logging.Logger, message: str) -> None:
        self._logger = logger_
        self._message = message
        self._fired = False

    def __iter__(self):
        if not self._fired:
            self._fired = True
            self._logger.debug(self._message)
        return iter(())


class _StrEmittingArg:
    """``str()`` emits one log record.

    Exercises the second (removed) re-entrancy site: on unpatched code
    the filter called ``record.getMessage()`` — which renders this arg
    — while holding ``KMSRedactionFilter._lock``, so the nested record
    re-entered ``filter()`` and re-acquired that lock on the same
    thread.
    """

    def __init__(self, logger_: logging.Logger, message: str) -> None:
        self._logger = logger_
        self._message = message

    def __str__(self) -> str:
        self._logger.debug(self._message)
        return "inner"


# ---------------------------------------------------------------------------
# AST pin helpers (content-anchored — NO lineno-exact pins, per repo
# test conventions: lineno pins self-invalidate on multi-commit
# branches)
# ---------------------------------------------------------------------------


def _module_ast(relpath: str) -> ast.Module:
    return ast.parse(
        (_REPO_ROOT / relpath).read_text(encoding="utf-8"), filename=relpath
    )


def _find_class(tree: ast.Module, name: str) -> ast.ClassDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"class {name!r} not found in module")


def _methods_in(cls: ast.ClassDef) -> dict[str, ast.FunctionDef]:
    return {
        node.name: node
        for node in cls.body
        if isinstance(node, ast.FunctionDef)
    }


def _module_function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"module-level function {name!r} not found")


def _withs(node: ast.AST) -> list[ast.With]:
    return [n for n in ast.walk(node) if isinstance(n, ast.With)]


def _imports(node: ast.AST) -> list[ast.AST]:
    return [
        n
        for n in ast.walk(node)
        if isinstance(n, (ast.Import, ast.ImportFrom))
    ]


def _sys_modules_refs(node: ast.AST) -> list[ast.Attribute]:
    return [
        n
        for n in ast.walk(node)
        if isinstance(n, ast.Attribute)
        and n.attr == "modules"
        and isinstance(n.value, ast.Name)
        and n.value.id == "sys"
    ]


def _assert_per_record_hygiene(fn: ast.FunctionDef, label: str) -> None:
    """Assert a per-record function: no blocking lock, no import, no sys.modules."""
    withs = _withs(fn)
    imports = _imports(fn)
    sysmods = _sys_modules_refs(fn)
    assert not withs, (
        f"{label}: per-record path must NEVER acquire a blocking lock "
        f"(with-stmt found at lines {[n.lineno for n in withs]}) — this "
        f"is the v0.28.3 self-deadlock class"
    )
    assert not imports, (
        f"{label}: per-record path must not run import machinery "
        f"(import found at lines {[n.lineno for n in imports]})"
    )
    assert not sysmods, (
        f"{label}: per-record path must not touch sys.modules"
    )


# ---------------------------------------------------------------------------
# 1) Re-entrancy — the dump mechanism, deterministic + timeout-guarded
# ---------------------------------------------------------------------------


class TestReEntrancyNoDeadlock:
    """Nested emission must never deadlock the emitting thread.

    Pre-fix both tests deadlock the worker thread on a non-reentrant
    lock held by the same thread; the bounded guard fires (~10s) and
    the test FAILS LOUDLY. Post-fix both pass in milliseconds.
    """

    def test_nested_log_inside_snapshot_read_does_not_deadlock(
        self, fernet_key: str
    ) -> None:
        """The dump's exact mechanism: a finalizer-style nested log
        emission from inside ``list(self._plaintext_registry)``."""
        store = kms_lite._get_store()
        store._register_plaintext("reentrant-snapshot-token")

        handler = _BufferHandler()
        log = logging.getLogger("kms.test.deadlock.snapshot")
        _attach_buffer_with_private_filter(handler, log)

        # Swap the COW snapshot container for one that emits a log from
        # inside iteration — the deterministic equivalent of the GC
        # ``__del__`` finalizer firing mid-``list()`` on v0.28.3.
        store._plaintext_registry = _NestedEmitOnceOnIterate(
            log, "nested emission from inside the snapshot read"
        )

        hung, error = _run_bounded(lambda: log.debug("outer snapshot record"))
        assert not hung, (
            "DEADLOCK REPRODUCED: emitting a log whose filter pass "
            "iterates the registry snapshot while a nested record is "
            "emitted from INSIDE that iteration blocked the emitting "
            f"thread for >{_DEADLOCK_PROBE_TIMEOUT_S}s — the read path "
            "is re-entering a non-reentrant lock again"
        )
        assert error is None, f"emission raised: {error}"

        captured = handler.captured
        assert (
            "nested emission from inside the snapshot read" in captured
        ), "nested re-entry did not cross the full filter path"
        assert "outer snapshot record" in captured

    def test_nested_log_inside_getmessage_does_not_deadlock(
        self, fernet_key: str
    ) -> None:
        """The removed ``KMSRedactionFilter._lock`` site: a nested
        emission from inside ``record.getMessage()`` (an arg whose
        ``__str__`` logs) must not re-block on the filter's own lock."""
        store = kms_lite._get_store()
        store._register_plaintext("reentrant-getmessage-token")

        handler = _BufferHandler()
        log = logging.getLogger("kms.test.deadlock.getmessage")
        _attach_buffer_with_private_filter(handler, log)

        hung, error = _run_bounded(
            lambda: log.warning(
                "outer %s",
                _StrEmittingArg(log, "nested emission from inside getMessage"),
            )
        )
        assert not hung, (
            "DEADLOCK REPRODUCED: a nested emission from inside "
            "record.getMessage() blocked the emitting thread for "
            f">{_DEADLOCK_PROBE_TIMEOUT_S}s — a per-record filter lock "
            "(the removed _lock class) is being re-acquired on the "
            "same thread"
        )
        assert error is None, f"emission raised: {error}"

        captured = handler.captured
        assert "nested emission from inside getMessage" in captured
        assert "outer inner" in captured


# ---------------------------------------------------------------------------
# 2) Hygiene pins — static AST (content-anchored) + behavioral
# ---------------------------------------------------------------------------


class TestFilterFileHygienePins:
    """Static pins on daemon/util/log_redaction_filter.py."""

    def test_filter_class_has_no_lock_attribute(self) -> None:
        tree = _module_ast("daemon/util/log_redaction_filter.py")
        cls = _find_class(tree, "KMSRedactionFilter")
        locks = [
            n for n in ast.walk(cls)
            if isinstance(n, ast.Attribute) and n.attr == "_lock"
        ]
        assert not locks, (
            "KMSRedactionFilter must not carry a per-record _lock — it "
            "is a re-entrancy deadlock site (LogRecords are per-call, "
            "the replacement is idempotent)"
        )

    def test_filter_method_per_record_path_is_lock_and_import_free(
        self,
    ) -> None:
        tree = _module_ast("daemon/util/log_redaction_filter.py")
        cls = _find_class(tree, "KMSRedactionFilter")
        fn = _methods_in(cls)["filter"]
        _assert_per_record_hygiene(
            fn, "KMSRedactionFilter.filter"
        )
        # Positive anchor: the scrub still reads the registry snapshot.
        calls_iter = any(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "iter_registered_plaintexts"
            for n in ast.walk(fn)
        )
        assert calls_iter, "filter must still call iter_registered_plaintexts"

    def test_registry_accessor_is_import_free_per_call(self) -> None:
        tree = _module_ast("daemon/util/log_redaction_filter.py")
        fn = _module_function(tree, "iter_registered_plaintexts")
        _assert_per_record_hygiene(fn, "iter_registered_plaintexts")
        # Positive anchor: the getter comes from the first-use latch.
        resolves = any(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "_resolve_get_store"
            for n in ast.walk(fn)
        )
        assert resolves, (
            "iter_registered_plaintexts must resolve the store getter "
            "via the _resolve_get_store once-latch"
        )

    def test_getter_resolution_is_a_guarded_first_use_import(self) -> None:
        """The ONLY import on the registry path lives inside the
        once-guard of _resolve_get_store (guarded-body content anchor)."""
        tree = _module_ast("daemon/util/log_redaction_filter.py")
        fn = _module_function(tree, "_resolve_get_store")
        # The import must sit inside a first-use guard: an `if` on the
        # module-level cache, containing exactly the kms_lite import.
        guarded_imports: list[ast.ImportFrom] = []
        for node in fn.body:
            if not isinstance(node, ast.If):
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.ImportFrom):
                    guarded_imports.append(inner)
        assert len(guarded_imports) == 1, (
            "exactly one first-use import expected inside the guard, "
            f"got {len(guarded_imports)}"
        )
        imp = guarded_imports[0]
        assert imp.module == "daemon.services.kms_lite"
        assert [a.name for a in imp.names] == ["_get_store"]
        # Nothing OUTSIDE the first-use guard imports.
        outside_imports: list[ast.AST] = []
        for node in fn.body:
            if isinstance(node, ast.If):
                continue  # the first-use guard itself (checked above)
            outside_imports.extend(_imports(node))
        assert not outside_imports, (
            "_resolve_get_store must import nothing outside the "
            f"first-use guard (found {len(outside_imports)})"
        )


class TestKmsLiteHygienePins:
    """Static pins on daemon/services/kms_lite.py."""

    def test_registry_read_is_lock_free(self) -> None:
        tree = _module_ast("daemon/services/kms_lite.py")
        cls = _find_class(tree, "_KMSStore")
        fn = _methods_in(cls)["_iter_registered_plaintexts"]
        withs = _withs(fn)
        assert not withs, (
            "_iter_registered_plaintexts must be LOCK-FREE (with-stmt "
            f"found at lines {[n.lineno for n in withs]}) — the read "
            "runs inside the logging emit path on every record; a "
            "blocking acquire here self-deadlocked v0.28.3 on GC "
            "finalizer re-entry"
        )
        imports = _imports(fn)
        assert not imports, (
            "_iter_registered_plaintexts must not run import machinery"
        )
        # Positive anchor: reads the COW snapshot attribute via list().
        reads_snapshot = any(
            isinstance(n, ast.Attribute) and n.attr == "_plaintext_registry"
            for n in ast.walk(fn)
        )
        assert reads_snapshot, (
            "_iter_registered_plaintexts must read the "
            "_plaintext_registry copy-on-write snapshot"
        )

    def test_registry_write_lock_is_small(self) -> None:
        """The write-side lock wraps ONLY the read-merge-publish: no
        calls at all inside the with-body (no logging, no crypto, no
        allocation-heavy region)."""
        tree = _module_ast("daemon/services/kms_lite.py")
        cls = _find_class(tree, "_KMSStore")
        fn = _methods_in(cls)["_register_plaintext"]
        withs = _withs(fn)
        assert len(withs) == 1, (
            f"_register_plaintext must use exactly one with-stmt "
            f"(the small write lock), got {len(withs)}"
        )
        inside = list(ast.walk(withs[0]))
        calls = [n for n in inside if isinstance(n, ast.Call)]
        imports = [
            n
            for n in inside
            if isinstance(n, (ast.Import, ast.ImportFrom))
        ]
        assert not calls, (
            "the write-side critical section must contain NO calls "
            "(no logging, no crypto, no allocation-heavy work)"
        )
        assert not imports, (
            "the write-side critical section must not import"
        )


class TestPerRecordPathBehavioralHygiene:
    def test_repeated_records_do_not_mutate_sys_modules(
        self, fernet_key: str
    ) -> None:
        """After the first-use warm-up, steady-state emission performs
        no import machinery: ``sys.modules`` stays byte-stable and the
        scrub still works."""
        store = kms_lite._get_store()
        store._register_plaintext("behavioral-hygiene-token")

        handler = _BufferHandler()
        log = logging.getLogger("kms.test.hygiene")
        _attach_buffer_with_private_filter(handler, log)
        log.debug("warm-up record")  # first call resolves the getter once
        log.debug("second warm-up record")

        before = frozenset(sys.modules)
        for i in range(25):
            log.debug("steady-state record %d", i)
        after = frozenset(sys.modules)
        assert before == after, (
            "per-record emission mutated sys.modules — import machinery "
            f"returned to the hot path (before−after="
            f"{sorted(before - after)[:5]}, after−before="
            f"{sorted(after - before)[:5]})"
        )

        log.debug("leak behavioral-hygiene-token here")
        captured = handler.captured
        assert "behavioral-hygiene-token" not in captured
        assert SENTINEL in captured, "steady-state scrub regressed"

    def test_resolve_get_store_is_a_once_latch(self) -> None:
        """Direct-call probe of the new once/latch helper: identity is
        cached across calls and matches ``kms_lite._get_store``."""
        first = lrf._resolve_get_store()
        assert callable(first)
        assert first is kms_lite._get_store
        second = lrf._resolve_get_store()
        assert second is first, (
            "_resolve_get_store must cache the resolved getter (once-latch)"
        )
        snapshot = iter_registered_plaintexts()
        assert isinstance(snapshot, list)


# ---------------------------------------------------------------------------
# 3) COW correctness — concurrent mint + iterate + emit smoke
# ---------------------------------------------------------------------------


class TestCopyOnWriteConcurrencySmoke:
    def test_concurrent_mint_and_iterate_no_lost_entries(
        self, fernet_key: str
    ) -> None:
        """Concurrent mints publish COW snapshots while other threads
        iterate and emit through the filter: no lost entries, snapshot
        consistency, monotone growth, zero exceptions, and the emit
        path never stalls."""
        store = kms_lite._get_store()
        mint_threads = 4
        mints_per_thread = 50
        iter_threads = 3
        iter_rounds = 300
        emitted_records = 200

        errors: list[str] = []
        snapshots: dict[int, list[frozenset[str]]] = {
            i: [] for i in range(iter_threads)
        }
        observed_lens: dict[int, list[int]] = {
            i: [] for i in range(iter_threads)
        }

        handler = _BufferHandler()
        log = logging.getLogger("kms.test.cow")
        _attach_buffer_with_private_filter(handler, log)

        def minter(idx: int) -> None:
            try:
                for i in range(mints_per_thread):
                    store.mint(service=f"svc-{idx}-{i}", reason="cow-smoke")
            except Exception as exc:  # noqa: BLE001 — collected below
                errors.append(f"minter {idx}: {type(exc).__name__}: {exc}")

        def iterator(idx: int) -> None:
            try:
                prev = -1
                for _ in range(iter_rounds):
                    snap = store._iter_registered_plaintexts()
                    observed_lens[idx].append(len(snap))
                    if len(snap) < prev:
                        errors.append(
                            f"iterator {idx}: snapshot regressed "
                            f"{prev} -> {len(snap)}"
                        )
                    prev = len(snap)
                    snapshots[idx].append(frozenset(snap))
            except Exception as exc:  # noqa: BLE001 — collected below
                errors.append(f"iterator {idx}: {type(exc).__name__}: {exc}")

        def emitter() -> None:
            try:
                for i in range(emitted_records):
                    log.debug("cow-smoke record %d", i)
            except Exception as exc:  # noqa: BLE001 — collected below
                errors.append(f"emitter: {type(exc).__name__}: {exc}")

        workers = [
            threading.Thread(target=minter, args=(i,), daemon=True)
            for i in range(mint_threads)
        ] + [
            threading.Thread(target=iterator, args=(i,), daemon=True)
            for i in range(iter_threads)
        ] + [threading.Thread(target=emitter, daemon=True)]
        for worker in workers:
            worker.start()
        join_budget = 60.0
        for worker in workers:
            worker.join(join_budget)
            assert not worker.is_alive(), (
                "COW smoke stalled: a worker thread did not finish "
                f"within {join_budget}s — the emit path is blocking "
                "again"
            )

        assert not errors, f"concurrency errors: {errors}"

        total_mints = mint_threads * mints_per_thread
        final = store._iter_registered_plaintexts()
        assert len(final) == total_mints, (
            f"LOST COW PUBLISHES: expected {total_mints} registered "
            f"plaintexts, got {len(final)}"
        )
        assert len(set(final)) == len(final), (
            "registry snapshot has duplicates — set semantics lost"
        )
        flat_observed: set[str] = set()
        for snaps in snapshots.values():
            for snap in snaps:
                flat_observed |= snap
        assert flat_observed <= set(final), (
            "observed snapshot contains entries absent from the final "
            "registry — inconsistent COW publication"
        )
        for idx, lens in observed_lens.items():
            assert lens == sorted(lens), (
                f"iterator {idx} observed non-monotone snapshot sizes — "
                "registry content regressed mid-flight"
            )
        assert handler.record_count == emitted_records, (
            f"expected {emitted_records} emitted records to reach the "
            f"handler, got {handler.record_count}"
        )
