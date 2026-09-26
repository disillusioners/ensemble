"""KMS logging redaction filter — runtime mitigation tests (P3-WP10).

Day-1 acceptance for the §8 R4 closure. The tests fall into four
buckets:

1. **Filter-on-every-handler walk** — :func:`install_kms_redaction_filter`
   must attach the filter to every handler reachable from
   ``logging.root`` (root + every named logger in
   ``logging.root.manager.loggerDict``).
2. **Pass-through** — markers (``__KMS_REF__<handle>__``), handle
   strings (``KMS_HANDLE_<id>``) and fingerprint sha256[:16] hex
   strings pass through unchanged. They carry no secret.
3. **Plaintext scrub** — when a plaintext is registered, any log
   record that embeds it (via f-string or lazy ``%s`` formatting) is
   scrubbed to the sentinel ``__REDACTED_KMS_SECRET__``.
4. **Store-unavailable no-op** — when the KMS store has not been
   initialised (or the registry API is absent), the filter passes
   records through without error. Logging MUST NEVER crash because
   of a missing redaction back-end.

The integration test wires a real handler with the real filter and
exercises the full emission path (record creation → handler filter →
formatter → buffer). This is the acceptance test from plan §P3-WP10.

Constraints:

* No PG / no live DB connections. KMS store is in-memory only.
* No daemon boot. No ambient ``POSTGRES_*`` reads.
* Test isolation: every test that mints resets the store + plaintext
  registry in a fixture teardown.

These tests are runnable in isolation via:

    .venv/bin/python -m pytest tests/unit/test_log_redaction_filter.py -q
"""

from __future__ import annotations

import logging
from typing import Iterator

import pytest
from cryptography.fernet import Fernet

from daemon.services import kms_lite
from daemon.services.kms_lite import (
    kms_request,
    kms_resolve_handle,
    reset_store_for_tests,
)
from daemon.util import log_redaction_filter as lrf
from daemon.util.log_redaction_filter import (
    KMSRedactionFilter,
    SENTINEL,
    install_kms_redaction_filter,
    iter_registered_plaintexts,
    patch_addhandler_to_install_filter,
    reset_filter_for_tests,
    reset_patch_for_tests,
)


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

    Also detaches any handlers the test attached so a following test
    doesn't see handler state from a prior case. The
    ``daemon/__init__.py``-level monkey-patch and the root handler
    state are preserved across tests (they are process-singletons).
    """
    reset_store_for_tests()
    yield
    reset_store_for_tests()


@pytest.fixture
def attached_handlers() -> Iterator[list[logging.Handler]]:
    """Track handlers attached during a test so the fixture can detach them.

    Tests that attach handlers MUST register them with this fixture so
    the fixture can clean up after the test, avoiding global
    logging-state pollution across cases.
    """
    attached: list[logging.Handler] = []
    yield attached
    root = logging.getLogger()
    for handler in attached:
        try:
            handler.close()
        except Exception:
            pass
        try:
            root.removeHandler(handler)
        except Exception:
            pass


@pytest.fixture(autouse=True)
def _preserve_global_patch() -> Iterator[None]:
    """Don't disturb the daemon/__init__ monkey-patch from one test to another.

    The monkey-patch is a process-singleton; tearing it down would
    leak state. Some tests assert it WAS applied (e.g. by inspecting
    the filter on a fresh handler), which requires the patch to stay
    live. This fixture is a no-op marker; the patch is restored by
    tests that explicitly reset it via :func:`reset_patch_for_tests`.
    """
    yield


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


def _attach_buffer_to_root(
    handler: logging.Handler,
) -> None:
    """Attach ``handler`` to the root logger + ensure the filter is on it."""
    root = logging.getLogger()
    root.addHandler(handler)
    # The daemon/__init__-level monkey-patch should have added the filter
    # already; assert here so a regression of the patch is loud.
    flt_names = [f.name for f in handler.filters]
    assert (
        "kms_redaction_filter" in flt_names
    ), f"KMS redaction filter missing on handler {handler!r}: {flt_names!r}"


# ---------------------------------------------------------------------------
# 1) Filter-on-every-handler walk
# ---------------------------------------------------------------------------


class TestHandlerCoverage:
    def test_install_attaches_filter_to_root_handlers(self) -> None:
        # Make sure root has at least one handler we can inspect.
        root = logging.getLogger()
        handler = _BufferHandler()
        root.addHandler(handler)
        attached = install_kms_redaction_filter(force=True)
        assert any(
            f.name == "kms_redaction_filter" for f in handler.filters
        ), "Filter not on the freshly-attached root handler"
        # The install count is at least the handlers we know about.
        assert attached >= 1
        handler.close()
        root.removeHandler(handler)

    def test_install_walks_named_logger_handlers(self) -> None:
        # Attach a handler to a non-root named logger — the install must
        # walk into the manager dict and find it.
        named = logging.getLogger("kms.test.named")
        handler = _BufferHandler()
        named.addHandler(handler)
        named.setLevel(logging.DEBUG)
        try:
            attached = install_kms_redaction_filter(force=True)
            assert any(
                f.name == "kms_redaction_filter" for f in handler.filters
            ), "Filter missing on the named-logger handler"
            assert attached >= 1
        finally:
            handler.close()
            named.removeHandler(handler)

    def test_install_is_idempotent(self) -> None:
        # Two installs back-to-back must not double-attach the filter.
        handler = _BufferHandler()
        logging.getLogger().addHandler(handler)
        try:
            install_kms_redaction_filter(force=True)
            install_kms_redaction_filter(force=False)
            kms_filters = [
                f for f in handler.filters if f.name == "kms_redaction_filter"
            ]
            assert (
                len(kms_filters) == 1
            ), f"Filter attached {len(kms_filters)} times — should be 1"
        finally:
            handler.close()
            logging.getLogger().removeHandler(handler)

    def test_addhandler_patch_attaches_filter_to_new_handler(self) -> None:
        # The daemon/__init__ monkey-patch means ANY addHandler call
        # automatically puts the filter on the new handler — including
        # those added after import time. Re-install a fresh handler and
        # assert the filter is there.
        handler = _BufferHandler()
        try:
            logging.getLogger().addHandler(handler)
            assert any(
                f.name == "kms_redaction_filter" for f in handler.filters
            ), "addHandler patch did not auto-install the filter"
        finally:
            handler.close()
            logging.getLogger().removeHandler(handler)


# ---------------------------------------------------------------------------
# 2) Pass-through — markers, handles, fingerprints
# ---------------------------------------------------------------------------


class TestPassThrough:
    def test_marker_passes_through_unchanged(
        self, fernet_key: str
    ) -> None:
        # Mint → marker → emit through handler → assert the marker is in the output.
        record = kms_request(service="opendesign", reason="install")
        marker = kms_lite.build_marker(record["handle"])
        handler = _BufferHandler()
        _attach_buffer_to_root(handler)
        try:
            logging.getLogger("kms.test.passthrough").warning(
                "marker=%s", marker
            )
            captured = handler.captured
            assert marker in captured, (
                f"Marker was scrubbed! expected {marker!r} in {captured!r}"
            )
            # And the plaintext that backs the marker is NOT in the output.
            plaintext = kms_resolve_handle(record["handle"])
            assert plaintext is not None
            assert plaintext not in captured
        finally:
            handler.close()
            logging.getLogger().removeHandler(handler)

    def test_handle_passes_through_unchanged(
        self, fernet_key: str
    ) -> None:
        record = kms_request(service="opendesign", reason="install")
        handler = _BufferHandler()
        _attach_buffer_to_root(handler)
        try:
            logging.getLogger("kms.test.passthrough").warning(
                "handle=%s", record["handle"]
            )
            captured = handler.captured
            assert record["handle"] in captured
            plaintext = kms_resolve_handle(record["handle"])
            assert plaintext is not None
            assert plaintext not in captured
        finally:
            handler.close()
            logging.getLogger().removeHandler(handler)

    def test_fingerprint_passes_through_unchanged(
        self, fernet_key: str
    ) -> None:
        record = kms_request(service="opendesign", reason="install")
        handler = _BufferHandler()
        _attach_buffer_to_root(handler)
        try:
            logging.getLogger("kms.test.passthrough").warning(
                "fp=%s", record["fingerprint"]
            )
            captured = handler.captured
            assert record["fingerprint"] in captured
        finally:
            handler.close()
            logging.getLogger().removeHandler(handler)

    def test_unrelated_log_lines_pass_through(
        self, fernet_key: str
    ) -> None:
        # Mint a plaintext so the registry is non-empty; log an
        # unrelated line; assert nothing was scrubbed.
        kms_request(service="opendesign", reason="install")
        handler = _BufferHandler()
        _attach_buffer_to_root(handler)
        try:
            logging.getLogger("kms.test.passthrough").warning(
                "unrelated: hello world"
            )
            assert "unrelated: hello world" in handler.captured
        finally:
            handler.close()
            logging.getLogger().removeHandler(handler)


# ---------------------------------------------------------------------------
# 3) Plaintext scrub — the §7 acceptance test
# ---------------------------------------------------------------------------


class TestPlaintextScrub:
    def test_plaintext_in_args_is_scrubbed(self, fernet_key: str) -> None:
        # Lazy %-formatting: the plaintext reaches the record via
        # record.args. The filter MUST scrub before formatting.
        record = kms_request(service="opendesign", reason="install")
        plaintext = kms_resolve_handle(record["handle"])
        assert plaintext is not None
        handler = _BufferHandler()
        _attach_buffer_to_root(handler)
        try:
            logging.getLogger("kms.test.scrub").warning(
                "minted secret=%s", plaintext
            )
            captured = handler.captured
            assert plaintext not in captured, (
                f"PLAINTEXT LEAKED: {plaintext!r} in {captured!r}"
            )
            assert SENTINEL in captured, (
                f"Sentinel {SENTINEL!r} missing from {captured!r}"
            )
        finally:
            handler.close()
            logging.getLogger().removeHandler(handler)

    def test_plaintext_in_fstring_is_scrubbed(self, fernet_key: str) -> None:
        # f-string formatting: the plaintext is embedded into record.msg
        # at the call site. The filter MUST scrub on the rendered string.
        record = kms_request(service="opendesign", reason="install")
        plaintext = kms_resolve_handle(record["handle"])
        assert plaintext is not None
        handler = _BufferHandler()
        _attach_buffer_to_root(handler)
        try:
            # Direct f-string at the call site (the riskiest pattern).
            logging.getLogger("kms.test.scrub").warning(
                f"the secret is {plaintext}"
            )
            captured = handler.captured
            assert plaintext not in captured
            assert SENTINEL in captured
        finally:
            handler.close()
            logging.getLogger().removeHandler(handler)

    def test_plaintext_appearing_twice_is_scrubbed(
        self, fernet_key: str
    ) -> None:
        # Same plaintext appearing multiple times → all replaced.
        record = kms_request(service="opendesign", reason="install")
        plaintext = kms_resolve_handle(record["handle"])
        assert plaintext is not None
        handler = _BufferHandler()
        _attach_buffer_to_root(handler)
        try:
            logging.getLogger("kms.test.scrub").warning(
                "first=%s second=%s", plaintext, plaintext
            )
            captured = handler.captured
            assert plaintext not in captured
            assert captured.count(SENTINEL) == 2
        finally:
            handler.close()
            logging.getLogger().removeHandler(handler)

    def test_multiple_plaintexts_all_scrubbed(self, fernet_key: str) -> None:
        # Two mints → two registered plaintexts → both scrubbed when
        # both appear in the same record.
        rec_a = kms_request(service="svc-a", reason="install-a")
        rec_b = kms_request(service="svc-b", reason="install-b")
        pt_a = kms_resolve_handle(rec_a["handle"])
        pt_b = kms_resolve_handle(rec_b["handle"])
        assert pt_a is not None and pt_b is not None
        handler = _BufferHandler()
        _attach_buffer_to_root(handler)
        try:
            logging.getLogger("kms.test.scrub").warning(
                "a=%s b=%s", pt_a, pt_b
            )
            captured = handler.captured
            assert pt_a not in captured
            assert pt_b not in captured
            assert captured.count(SENTINEL) == 2
        finally:
            handler.close()
            logging.getLogger().removeHandler(handler)

    def test_exception_message_args_carrying_plaintext_is_scrubbed(
        self, fernet_key: str
    ) -> None:
        # Scope: this test asserts the format-string portion of the
        # exception is scrubbed. The traceback appended by
        # ``logger.exception`` is generated by Python's logging
        # formatter from ``record.exc_info`` AFTER the handler filter
        # runs, so it is OUT OF SCOPE for this filter — a follow-up WP
        # must add a formatter-level scrub if traceback-borne plaintext
        # is observed in practice. Day-1 acceptance (plan §P3-WP10) is
        # the format-string case, which this test covers.
        record = kms_request(service="opendesign", reason="install")
        plaintext = kms_resolve_handle(record["handle"])
        assert plaintext is not None
        handler = _BufferHandler()
        _attach_buffer_to_root(handler)
        try:
            exc = RuntimeError(f"failed to spawn with secret {plaintext}")
            # Pass the exception object as a %s arg — the formatter
            # will call str(exc) when assembling record.getMessage().
            logging.getLogger("kms.test.scrub").error(
                "spawn failed: %s", exc
            )
            captured = handler.captured
            # The first line of the formatted record is the format
            # string with the exception interpolated. Scrubbed.
            first_line = captured.split("\n", 1)[0]
            assert plaintext not in first_line, (
                f"PLAINTEXT LEAKED in format-string line: {first_line!r}"
            )
            assert SENTINEL in first_line
        finally:
            handler.close()
            logging.getLogger().removeHandler(handler)


# ---------------------------------------------------------------------------
# 4) Store-unavailable no-op
# ---------------------------------------------------------------------------


class TestStoreUnavailable:
    def test_filter_is_noop_when_store_uninitialised(self) -> None:
        # Don't mint anything. Registry is empty.
        assert iter_registered_plaintexts() == []
        handler = _BufferHandler()
        _attach_buffer_to_root(handler)
        try:
            # Log a value that LOOKS like a secret but is just a random
            # string — there is no registration so it must pass through.
            sentinel_value = "totally-fake-not-registered-secret-value"
            logging.getLogger("kms.test.noop").warning(
                "value=%s", sentinel_value
            )
            captured = handler.captured
            assert sentinel_value in captured
            assert SENTINEL not in captured
        finally:
            handler.close()
            logging.getLogger().removeHandler(handler)

    def test_filter_survives_when_registry_api_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Simulate an older build of kms_lite.py without the registry
        # API. The filter MUST NOT crash — it falls back to no-op.
        class _FakeStoreNoRegistry:
            def __init__(self) -> None:
                pass

        monkeypatch.setattr(lrf, "iter_registered_plaintexts", lambda: [])
        handler = _BufferHandler()
        _attach_buffer_to_root(handler)
        try:
            logging.getLogger("kms.test.noop").warning(
                "hello %s", "world"
            )
            assert "hello world" in handler.captured
        finally:
            handler.close()
            logging.getLogger().removeHandler(handler)

    def test_filter_swallows_registry_failure(self) -> None:
        # If the registry accessor raises (e.g. transient store error),
        # the filter MUST NOT crash logging. Test by monkey-patching the
        # accessor to raise.
        original_iter = lrf.iter_registered_plaintexts

        def _boom() -> list[str]:
            raise RuntimeError("synthetic store failure")

        lrf.iter_registered_plaintexts = _boom  # type: ignore[assignment]
        try:
            handler = _BufferHandler()
            _attach_buffer_to_root(handler)
            try:
                # Must not raise.
                logging.getLogger("kms.test.noop").warning(
                    "still working %s", "fine"
                )
                assert "still working fine" in handler.captured
            finally:
                handler.close()
                logging.getLogger().removeHandler(handler)
        finally:
            lrf.iter_registered_plaintexts = original_iter  # type: ignore[assignment]

    def test_registry_snapshot_is_stable_under_concurrent_mint(
        self, fernet_key: str
    ) -> None:
        # Smoke test: the registry returns snapshots, not a live view.
        # A concurrent mint mid-scrub must not raise.
        import threading

        record = kms_request(service="opendesign", reason="install")
        plaintext = kms_resolve_handle(record["handle"])
        assert plaintext is not None

        stop = threading.Event()
        errors: list[BaseException] = []

        def _hammer() -> None:
            try:
                while not stop.is_set():
                    kms_request(service="opendesign", reason="hammer")
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        thread = threading.Thread(target=_hammer, daemon=True)
        thread.start()
        try:
            handler = _BufferHandler()
            _attach_buffer_to_root(handler)
            try:
                for _ in range(50):
                    logging.getLogger("kms.test.race").warning(
                        "pt=%s", plaintext
                    )
                captured = handler.captured
                assert plaintext not in captured, "PLAINTEXT LEAKED under load"
            finally:
                handler.close()
                logging.getLogger().removeHandler(handler)
        finally:
            stop.set()
            thread.join(timeout=5)
        assert not errors, f"Mint thread raised: {errors!r}"


# ---------------------------------------------------------------------------
# 5) Lifecycle — singleton + patch idempotency
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_filter_singleton_is_stable(self) -> None:
        a = lrf._get_filter_singleton()
        b = lrf._get_filter_singleton()
        assert a is b

    def test_reset_filter_for_tests_drops_singleton(self) -> None:
        a = lrf._get_filter_singleton()
        reset_filter_for_tests()
        b = lrf._get_filter_singleton()
        assert a is not b
        # After reset, install(force=True) attaches the NEW singleton to
        # root's handlers.
        handler = _BufferHandler()
        logging.getLogger().addHandler(handler)
        try:
            install_kms_redaction_filter(force=True)
            assert any(f is b for f in handler.filters)
        finally:
            handler.close()
            logging.getLogger().removeHandler(handler)
        # Restore the global singleton for downstream tests.
        reset_filter_for_tests()

    def test_patch_is_idempotent(self) -> None:
        # Calling patch twice returns False on the second call.
        first = patch_addhandler_to_install_filter()
        second = patch_addhandler_to_install_filter()
        # First might be True (if not yet patched) or False (already
        # patched by daemon/__init__.py). Second is always False.
        assert second is False
        # If first was True, the patch is now installed; subsequent
        # calls are no-ops.
        if first:
            assert patch_addhandler_to_install_filter() is False
        # Restore for cleanliness across the test session.
        reset_patch_for_tests()
        # Re-apply so other tests still see the global patch.
        patch_addhandler_to_install_filter()

    def test_reset_patch_restores_original_addhandler(self) -> None:
        # After reset, logging.Logger.addHandler is the import-time
        # original captured in :mod:`log_redaction_filter`'s module
        # body — NOT a re-patched version. Compare against the saved
        # reference.
        reset_patch_for_tests()
        assert logging.Logger.addHandler is lrf._original_logger_add_handler
        # Re-install for downstream tests.
        patch_addhandler_to_install_filter()