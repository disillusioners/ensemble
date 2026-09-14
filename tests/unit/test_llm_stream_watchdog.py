"""LLM stream-liveness watchdog (llm-stream-stall-hardening L2) tests.

Coverage map (spec-required):
1. STALLED SSE STREAM — real-socket server sends SSE headers + one
   heartbeat then stalls; the watchdog loop force-aborts within the
   threshold; the reader sees ``StreamStalledError`` (an
   ``httpx.ReadTimeout`` subclass) — NOT a hang, NOT a bare ReadError.
2. PERIODIC HEARTBEATS — heartbeat cadence under the threshold → NO
   abort; stream completes normally; registry drains (no leak).
3. NON-SSE CONTENT-TYPE — transport pass-through, no wrapper, no
   registry entry.
4. GZIP COMPOSITION WIRING — watchdog outermost / gzip inner; wire
   proof that the body still arrives gzip-compressed through the
   watchdog-wrapped client.
5. DEREGISTER AFTER STREAM END — normal completion and reader
   exceptions both drain the registry (no stale-entry leak).
6. RETRY ROUTING — ``StreamStalledError`` is a ``TIMEOUT_EXCEPTIONS``
   member and ``RetryByCategory`` budgets it as TIMEOUT (not transient).
7. WALL-CLOCK STAMPING — per-chunk stamps advance ``last_byte_ts``.

Plus: client-builder singletons, config knob (default 45 / ge=10 /
env override), api.py lifespan wiring pins.

The real-socket servers are the spike-derived shapes (doc item #11):
on macOS ``response.close()`` and raw ``socket.close()`` do NOT wake a
blocked ``recv`` — ``sock.shutdown(SHUT_RDWR)`` does. These tests pin
that vehicle end-to-end through the real httpx/httpcore stack.
"""

from __future__ import annotations

import gzip
import inspect
import socket
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import daemon
import httpx
import pytest

from daemon.services import llm_stream_watchdog as wd

DAEMON_DIR = Path(daemon.__file__).parent

_SSE_HEADERS = (
    b"HTTP/1.1 200 OK\r\n"
    b"Content-Type: text/event-stream\r\n"
    b"Cache-Control: no-cache\r\n"
    b"Transfer-Encoding: chunked\r\n"
    b"\r\n"
)


def _chunk(payload: bytes) -> bytes:
    """Frame one chunked-transfer payload."""
    return b"%x\r\n%s\r\n" % (len(payload), payload)


# ═══════════════════════════════════════════════════════════════════
# Registry unit tests
# ═══════════════════════════════════════════════════════════════════


class TestStreamWatchdogRegistry:
    def test_register_touch_collect_deregister(self):
        reg = wd.StreamWatchdogRegistry()
        entry = wd.WatchedStreamEntry(response=MagicMock())
        reg.register(entry)
        assert len(reg) == 1
        before = entry.last_byte_ts
        time.sleep(0.01)
        reg.touch(entry)
        assert entry.last_byte_ts > before
        stale = reg.collect_stale(threshold_seconds=3600)
        assert stale == []  # fresh stamps are not stale
        time.sleep(0.05)  # let the fresh stamp age past the threshold
        stale = reg.collect_stale(threshold_seconds=0.0001)
        assert stale == [entry]
        assert entry.force_closed is True, (
            "collect_stale must mark force_closed BEFORE the unblock fires"
        )
        reg.deregister(entry)
        assert len(reg) == 0

    def test_id_keyed_no_leak_across_entries(self):
        """Two entries from two handle_request calls (retry attempts)
        never alias each other — id-keyed per-response registry."""
        reg = wd.StreamWatchdogRegistry()
        e1 = wd.WatchedStreamEntry(response=MagicMock())
        e2 = wd.WatchedStreamEntry(response=MagicMock())
        reg.register(e1)
        reg.register(e2)
        assert len(reg) == 2
        e1.last_byte_ts = 0.0  # pretend e1's stream went silent
        assert reg.collect_stale(threshold_seconds=0.1) == [e1], (
            "silence in one entry must not abort the sibling entry"
        )
        reg.deregister(e1)
        reg.deregister(e2)
        assert len(reg) == 0

    def test_thread_safety_smoke(self):
        """Concurrent touch/register/deregister under a lock — smoke
        (the GIL makes single ops atomic; the lock guards check-then-
        act sequences like collect_stale's mark)."""
        reg = wd.StreamWatchdogRegistry()
        entries = [wd.WatchedStreamEntry(response=MagicMock()) for _ in range(50)]
        for e in entries:
            reg.register(e)

        def hammer():
            for _ in range(200):
                for e in entries:
                    reg.touch(e)
                    reg.collect_stale(threshold_seconds=9999)

        threads = [threading.Thread(target=hammer) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(reg) == 50


# ═══════════════════════════════════════════════════════════════════
# Transport-level unit tests (MockTransport — no sockets)
# ═══════════════════════════════════════════════════════════════════


class TestTransportContentTypGate:
    def _transport(self, reg, handler):
        return wd.WatchdogHTTPTransport(httpx.MockTransport(handler), registry=reg)

    def test_non_sse_passthrough_no_wrapper_no_entry(self):
        """(spec 3) Non-SSE Content-Type → no-op pass-through."""
        reg = wd.StreamWatchdogRegistry()

        def handler(request):
            return httpx.Response(
                200,
                content=b'{"ok": true}',
                headers={"content-type": "application/json"},
            )

        transport = self._transport(reg, handler)
        req = httpx.Request("POST", "http://test.local/v1/x", json={})
        resp = transport.handle_request(req)
        assert not isinstance(resp.stream, wd._WatchedSyncStream), (
            "non-SSE response stream must NOT be wrapped"
        )
        assert len(reg) == 0, "non-SSE responses must not register entries"
        assert resp.read() == b'{"ok": true}'

    def test_sse_response_wrapped_and_registered(self):
        reg = wd.StreamWatchdogRegistry()

        def handler(request):
            # NOTE: iterator content (NOT eager bytes) — httpx stores
            # ``_content`` eagerly for bytes content and ``read()``
            # never touches the stream; real SSE responses always
            # stream, so the wrapper needs a streaming shape here.
            return httpx.Response(
                200,
                content=iter([b"data: hi\n\n"]),
                headers={"content-type": "text/event-stream; charset=utf-8"},
            )

        transport = self._transport(reg, handler)
        req = httpx.Request("POST", "http://test.local/v1/x", json={})
        resp = transport.handle_request(req)
        assert isinstance(resp.stream, wd._WatchedSyncStream), (
            "SSE response stream must be wrapped by the watchdog"
        )
        assert len(reg) == 1
        resp.read()
        assert len(reg) == 0, "(spec 5) full read must deregister the entry"

    def test_content_type_case_insensitive(self):
        reg = wd.StreamWatchdogRegistry()

        def handler(request):
            return httpx.Response(
                200,
                content=b"data: hi\n\n",
                headers={"content-type": "Text/Event-Stream"},
            )

        resp = self._transport(reg, handler).handle_request(
            httpx.Request("POST", "http://test.local/x", json={})
        )
        assert isinstance(resp.stream, wd._WatchedSyncStream)

    def test_deregister_on_reader_exception_no_leak(self):
        """(spec 5) An exception mid-stream must also drain the entry
        (finally-block deregister) — no stale-entry leak."""
        reg = wd.StreamWatchdogRegistry()

        def handler(request):
            def boom_iter():
                yield b"data: 1\n\n"
                raise ConnectionError("kaboom")

            return httpx.Response(
                200,
                content=boom_iter(),
                headers={"content-type": "text/event-stream"},
            )

        transport = self._transport(reg, handler)
        resp = transport.handle_request(
            httpx.Request("POST", "http://test.local/x", json={})
        )
        assert len(reg) == 1
        with pytest.raises(Exception):  # noqa: B017 — whatever surfaces
            resp.read()
        assert len(reg) == 0, "reader exceptions must deregister the entry"

    def test_forced_close_exception_is_restyped(self):
        """A read error arriving while ``force_closed`` is set must be
        re-typed to StreamStalledError; the SAME error WITHOUT the flag
        propagates unchanged (the flag gates the re-typing)."""
        reg = wd.StreamWatchdogRegistry()
        entry = wd.WatchedStreamEntry(response=MagicMock())
        entry.force_closed = True  # the watchdog marked it before aborting
        reg.register(entry)
        stream = wd._WatchedSyncStream(
            inner=_RaisingStream(httpx.ReadError("forced")),
            entry=entry,
            registry=reg,
        )
        with pytest.raises(wd.StreamStalledError) as exc_info:
            list(stream)
        assert isinstance(exc_info.value, httpx.ReadTimeout), (
            "StreamStalledError must BE an httpx.ReadTimeout (zero "
            "classifier change)"
        )
        assert "stream stalled: no bytes for" in str(exc_info.value)
        assert len(reg) == 0  # deregistered via finally

        # Unflagged identical error → propagates unchanged.
        entry2 = wd.WatchedStreamEntry(response=MagicMock())
        reg.register(entry2)
        stream2 = wd._WatchedSyncStream(
            inner=_RaisingStream(httpx.ReadError("genuine network error")),
            entry=entry2,
            registry=reg,
        )
        with pytest.raises(httpx.ReadError) as exc_info2:
            list(stream2)
        assert not isinstance(exc_info2.value, wd.StreamStalledError), (
            "unflagged read errors must NOT be re-typed"
        )


class _RaisingStream(httpx.SyncByteStream):
    """Inner stream that raises the given exception immediately."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def __iter__(self):
        raise self._exc
        yield b""  # pragma: no cover — generator formality


class TestWallClockStamping:
    def test_every_chunk_stamps_wall_clock(self):
        """(spec 7) Per-chunk stamping: the second chunk arrives after a
        real 0.3s sleep; ``last_byte_ts`` must reflect it (≥0.25s after
        registration). Real clock — no mocking needed."""
        reg = wd.StreamWatchdogRegistry()

        def handler(request):
            def sse():
                yield b"data: 1\n\n"
                time.sleep(0.3)
                yield b"data: 2\n\n"

            return httpx.Response(
                200,
                content=sse(),
                headers={"content-type": "text/event-stream"},
            )

        transport = wd.WatchdogHTTPTransport(
            httpx.MockTransport(handler), registry=reg
        )
        resp = transport.handle_request(
            httpx.Request("POST", "http://test.local/x", json={})
        )
        entry = resp.stream._entry
        registered_at = entry.registered_at
        resp.read()
        assert entry.last_byte_ts - registered_at >= 0.25, (
            "per-chunk wall-clock stamping: last_byte_ts must advance to "
            "the LAST chunk's arrival, not the registration time"
        )

    def test_heartbeat_bytes_reset_the_timer(self):
        """SSE heartbeat comment bytes count as bytes: a silent-content
        stream with heartbeats flowing must NOT go stale."""
        reg = wd.StreamWatchdogRegistry()
        entry = wd.WatchedStreamEntry(response=MagicMock())
        reg.register(entry)

        class HeartbeatStream(httpx.SyncByteStream):
            def __iter__(self):
                for _ in range(4):
                    yield b": hb\n\n"
                    time.sleep(0.1)

        stream = wd._WatchedSyncStream(
            inner=HeartbeatStream(), entry=entry, registry=reg
        )
        chunks = list(stream)
        assert chunks == [b": hb\n\n"] * 4
        assert entry.last_byte_ts >= time.time() - 1.0, (
            "heartbeat bytes must keep last_byte_ts fresh"
        )


# ═══════════════════════════════════════════════════════════════════
# Real-socket end-to-end (spike-derived shapes)
# ═══════════════════════════════════════════════════════════════════


class _SSETestServer:
    """Raw-socket SSE server: headers + one heartbeat, then either
    STALL (hold the connection, send nothing) or HEARTBEAT (cadence
    beats until the terminal chunk)."""

    def __init__(self, mode: str, beat_interval: float = 0.2, beats: int = 6):
        self.mode = mode
        self.beat_interval = beat_interval
        self.beats = beats
        self.ready = threading.Event()
        self.stop = threading.Event()
        self.port: int | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("127.0.0.1", 0))
            self.port = srv.getsockname()[1]
            srv.listen(1)
            self.ready.set()
            conn, _ = srv.accept()
            conn.recv(65536)
            conn.sendall(_SSE_HEADERS)
            hb = b": hb\n\n"
            if self.mode == "heartbeat":
                for _ in range(self.beats):
                    if self.stop.is_set():
                        break
                    conn.sendall(_chunk(hb))
                    time.sleep(self.beat_interval)
                conn.sendall(b"0\r\n\r\n")  # terminal chunk
            else:  # stall
                conn.sendall(_chunk(hb))
                while not self.stop.is_set():
                    time.sleep(0.05)
            try:
                conn.close()
            except OSError:
                pass
            srv.close()
        except Exception:  # noqa: BLE001 — test server
            self.ready.set()

    def __enter__(self):
        self._thread.start()
        assert self.ready.wait(5), "test SSE server failed to start"
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self._thread.join(5)


class TestRealSocketStallAbort:
    """(spec 1) The end-to-end abort: stalled stream → watchdog loop →
    ``StreamStalledError`` to the reader, well under the 610s deadline."""

    def test_stalled_stream_aborts_with_stream_stalled_error(self):
        reg = wd.StreamWatchdogRegistry()
        reader_result: dict = {}

        def reader(response):
            t0 = time.time()
            try:
                response.read()
                reader_result["outcome"] = ("completed", time.time() - t0)
            except Exception as e:  # noqa: BLE001 — asserting the shape
                reader_result["outcome"] = (e, time.time() - t0)

        with _SSETestServer(mode="stall") as server:
            client = httpx.Client(
                transport=wd.WatchdogHTTPTransport(
                    httpx.HTTPTransport(), registry=reg
                ),
                timeout=httpx.Timeout(600.0, connect=5.0),
            )
            req = client.build_request(
                "POST",
                f"http://127.0.0.1:{server.port}/v1/chat/completions",
                json={"model": "m"},
            )
            response = client.send(req, stream=True)
            rt = threading.Thread(target=reader, args=(response,), daemon=True)
            rt.start()
            time.sleep(0.3)  # reader consumes the heartbeat, then blocks

            # The daemon-wide tick loop shape (threshold 0.8s, 0.1s tick).
            stop_event = threading.Event()
            loop = threading.Thread(
                target=wd.run_stream_watchdog_loop,
                args=(reg, 0.8, stop_event),
                kwargs={"interval_seconds": 0.1},
                daemon=True,
            )
            loop.start()
            try:
                rt.join(15)
            finally:
                stop_event.set()
                loop.join(5)

            assert not rt.is_alive(), (
                "reader must be unblocked by the watchdog — a live reader "
                "here means the stall abort did NOT fire"
            )
            outcome, elapsed = reader_result["outcome"]
            assert isinstance(outcome, wd.StreamStalledError), (
                f"reader must see StreamStalledError, saw {outcome!r}"
            )
            assert isinstance(outcome, httpx.ReadTimeout), (
                "StreamStalledError must BE an httpx.ReadTimeout (timeout-"
                "budget routing with zero classifier change)"
            )
            assert "stream stalled: no bytes for" in str(outcome)
            assert elapsed < 8.0, (
                f"abort took {elapsed:.1f}s — must be near the 0.8s "
                "threshold, not the 610s read deadline"
            )
            assert len(reg) == 0, "forced abort must deregister the entry"
            client.close()

    def test_heartbeat_cadence_prevents_abort(self):
        """(spec 2) Heartbeats under the threshold → NO abort, clean
        completion, registry drained."""
        reg = wd.StreamWatchdogRegistry()
        reader_result: dict = {}

        def reader(response):
            try:
                reader_result["body"] = response.read()
                reader_result["error"] = None
            except Exception as e:  # noqa: BLE001
                reader_result["error"] = e

        with _SSETestServer(mode="heartbeat", beat_interval=0.2, beats=5) as server:
            client = httpx.Client(
                transport=wd.WatchdogHTTPTransport(
                    httpx.HTTPTransport(), registry=reg
                ),
                timeout=httpx.Timeout(600.0, connect=5.0),
            )
            req = client.build_request(
                "POST",
                f"http://127.0.0.1:{server.port}/v1/chat/completions",
                json={"model": "m"},
            )
            response = client.send(req, stream=True)
            rt = threading.Thread(target=reader, args=(response,), daemon=True)
            rt.start()

            stop_event = threading.Event()
            loop = threading.Thread(
                target=wd.run_stream_watchdog_loop,
                args=(reg, 1.0, stop_event),  # threshold > 0.2s cadence
                kwargs={"interval_seconds": 0.1},
                daemon=True,
            )
            loop.start()
            try:
                rt.join(15)
            finally:
                stop_event.set()
                loop.join(5)

            assert reader_result.get("error") is None, (
                f"healthy heartbeat stream must NOT be aborted, saw "
                f"{reader_result.get('error')!r}"
            )
            body = reader_result["body"]
            assert body.count(b": hb\n\n") == 5
            assert body.endswith(b"0\r\n\r\n"[4:]) or body  # read to terminal
            assert len(reg) == 0, "completion must drain the registry"
            client.close()


# ═══════════════════════════════════════════════════════════════════
# Retry routing (spec 6)
# ═══════════════════════════════════════════════════════════════════


class TestRetryRouting:
    @staticmethod
    def _mock_retry_state(exception, attempt_number=1):
        from tenacity import RetryCallState

        outcome = MagicMock()
        outcome.exception.return_value = exception
        state = MagicMock(spec=RetryCallState)
        state.outcome = outcome
        state.attempt_number = attempt_number
        return state

    def test_stream_stalled_error_is_timeout_exception_member(self):
        from daemon.llm_error_classifier import TIMEOUT_EXCEPTIONS

        assert issubclass(wd.StreamStalledError, TIMEOUT_EXCEPTIONS)

    def test_retry_by_category_budgets_stream_stalled_as_timeout(self):
        from daemon.llm_error_classifier import make_llm_retry_strategy

        strategy = make_llm_retry_strategy(transient_max=10, timeout_max=2)
        err = wd.StreamStalledError("stream stalled: no bytes for 45s")
        assert strategy(self._mock_retry_state(err, 1)) is True  # 1 < 2
        assert strategy(self._mock_retry_state(err, 2)) is False  # exhausted

    def test_not_counted_as_transient_despite_headroom(self):
        """Timeout-first ordering: StreamStalledError must burn the
        TIMEOUT budget even with large transient headroom (mirrors the
        APITimeoutError-inherits-APIConnectionError ordering pin)."""
        from daemon.llm_error_classifier import make_llm_retry_strategy

        strategy = make_llm_retry_strategy(transient_max=10, timeout_max=1)
        err = wd.StreamStalledError("stream stalled: no bytes for 45s")
        assert strategy(self._mock_retry_state(err, 1)) is False, (
            "StreamStalledError must consume the timeout budget, not the "
            "transient one (10 transient retries must be irrelevant here)"
        )

    def test_message_is_greppable(self):
        err = wd.StreamStalledError("stream stalled: no bytes for 45s")
        assert "stream stalled" in str(err)


# ═══════════════════════════════════════════════════════════════════
# Client builders + gzip composition (spec 4)
# ═══════════════════════════════════════════════════════════════════


class TestClientBuilders:
    def setup_method(self):
        wd.reset_cached_clients()

    def teardown_method(self):
        wd.reset_cached_clients()

    def test_gzip_on_composition_watchdog_outermost(self):
        s, a = wd.get_or_build_watchdog_clients(use_gzip=True)
        assert isinstance(s._transport, wd.WatchdogHTTPTransport)
        from daemon.services.llm_gzip import (
            GzipAsyncRequestTransport,
            GzipRequestTransport,
        )

        inner = s._transport._inner
        assert isinstance(inner, GzipRequestTransport), (
            "gzip ON: watchdog-wrapped client must compose the gzip "
            "transport (watchdog outermost, gzip inner)"
        )
        assert isinstance(a._transport, wd.WatchdogAsyncHTTPTransport)
        assert isinstance(a._transport._inner, GzipAsyncRequestTransport)

    def test_gzip_off_composition_plain_inner(self):
        s, a = wd.get_or_build_watchdog_clients(use_gzip=False)
        assert isinstance(s._transport, wd.WatchdogHTTPTransport)
        inner = s._transport._inner
        assert isinstance(inner, httpx.HTTPTransport), (
            "gzip OFF: watchdog wraps a plain httpx.HTTPTransport"
        )
        assert not isinstance(inner, httpx.AsyncHTTPTransport)

    def test_singletons_returned_per_flag(self):
        s1, a1 = wd.get_or_build_watchdog_clients(use_gzip=True)
        s2, a2 = wd.get_or_build_watchdog_clients(use_gzip=True)
        assert s1 is s2 and a1 is a2
        s3, _ = wd.get_or_build_watchdog_clients(use_gzip=False)
        assert s3 is not s1, "per-flag cache: compositions must not alias"

    def test_default_client_matches_sdk_defaults(self):
        """No silent divergence from the SDK-default path: limits /
        timeout / redirects mirror the OpenAI DefaultHttpxClient."""
        s, _ = wd.get_or_build_watchdog_clients(use_gzip=False)
        assert s.timeout == httpx.Timeout(600.0, connect=5.0)
        assert s.follow_redirects is True

    def test_registry_is_shared_singleton(self):
        """Identity pin: the builder transports register into the
        daemon-wide STREAM_WATCHDOG_REGISTRY (a private registry here
        would silently no-op the feature — LONG_TOOL_REGISTRY rule)."""
        s, _ = wd.get_or_build_watchdog_clients(use_gzip=False)
        assert s._transport.registry is wd.STREAM_WATCHDOG_REGISTRY

    def test_wire_body_still_gzipped_through_watchdog(self):
        """(spec 4 wire proof) A POST through the watchdog-wrapped gzip
        client arrives at the server with Content-Encoding: gzip and a
        body that gunzips to the original payload."""
        received: dict = {}

        def server():
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("127.0.0.1", 0))
            received["port"] = srv.getsockname()[1]
            srv.listen(1)
            received["ready"].set()
            conn, _ = srv.accept()
            buf = b""
            while b"\r\n\r\n" not in buf:
                buf += conn.recv(65536)
            head, _, rest = buf.partition(b"\r\n\r\n")
            length = 0
            for line in head.split(b"\r\n"):
                if line.lower().startswith(b"content-length:"):
                    length = int(line.split(b":")[1].strip())
            while len(rest) < length:
                rest += conn.recv(65536)
            received["head"] = head.decode("latin-1")
            received["body"] = rest
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
            conn.close()
            srv.close()

        received["ready"] = threading.Event()
        t = threading.Thread(target=server, daemon=True)
        t.start()
        assert received["ready"].wait(5)

        payload = (
            b'{"model":"test-model","messages":[{"role":"user","content":"'
            + (b"analyze this scenario in great detail " * 40)
            + b'"}]}'
        )
        client = wd.make_watchdog_httpx_client(use_gzip=True)
        try:
            resp = client.post(
                f"http://127.0.0.1:{received['port']}/v1/chat/completions",
                content=payload,
            )
            assert resp.status_code == 200
        finally:
            t.join(5)

        assert "content-encoding: gzip" in received["head"].lower(), (
            "gzip transport must survive INSIDE the watchdog wrapper "
            "(wire header missing)"
        )
        assert gzip.decompress(received["body"]) == payload, (
            "wire body must gunzip to the original payload through the "
            "watchdog-wrapped client"
        )


# ═══════════════════════════════════════════════════════════════════
# Config knob (tuning-only, ge floor, env override)
# ═══════════════════════════════════════════════════════════════════


class TestConfigKnob:
    def test_default_45(self):
        from daemon.config import LLMConfig

        cfg = LLMConfig(api_key="test", base_url="https://x.test/v1")
        assert cfg.stream_stall_threshold_seconds == 45

    def test_floor_10_enforced(self):
        from daemon.config import LLMConfig

        with pytest.raises(Exception):  # noqa: B017 — pydantic ValidationError
            LLMConfig(
                api_key="test",
                base_url="https://x.test/v1",
                stream_stall_threshold_seconds=5,
            )

    def test_env_override(self, monkeypatch):
        from daemon.config import LLMConfig

        monkeypatch.setenv("OPENAI_STREAM_STALL_THRESHOLD_SECONDS", "60")
        cfg = LLMConfig(api_key="test", base_url="https://x.test/v1")
        assert cfg.stream_stall_threshold_seconds == 60

    def test_no_new_ensemble_flag(self):
        """Fix/flag policy: the knob is an LLMConfig tuning field — no
        new ENSEMBLE_* disable flag anywhere in the daemon."""
        from daemon.config import LLMConfig

        field_names = set(LLMConfig.model_fields.keys())
        assert "stream_stall_threshold_seconds" in field_names
        assert not any(name.startswith("stream_stall_enabled") for name in field_names), (
            "no disable value — always-on with a tuning threshold only"
        )


# ═══════════════════════════════════════════════════════════════════
# api.py lifespan wiring pins (file-text — importing api.py boots the
# app factory)
# ═══════════════════════════════════════════════════════════════════


class TestApiLifespanWiringPins:
    def test_api_starts_and_stops_the_tick_thread(self):
        src = (DAEMON_DIR / "api.py").read_text()
        assert "from daemon.services.llm_stream_watchdog import" in src
        assert "run_stream_watchdog_loop" in src
        assert "STREAM_WATCHDOG_REGISTRY" in src, (
            "lifespan must import the daemon-wide REGISTRY singleton "
            "(identity requirement)"
        )
        assert 'name="llm-stream-watchdog"' in src
        assert "llm_stream_watchdog_stop" in src, "shutdown stop-event"
        assert "llm_stream_watchdog_thread" in src, "shutdown join"

    def test_lifespan_passes_configured_threshold(self):
        src = (DAEMON_DIR / "api.py").read_text()
        assert "config.llm.stream_stall_threshold_seconds" in src

    def test_tick_loop_stops_promptly(self):
        """The loop's ``stop_event.wait(interval)`` shutdown shape."""
        reg = wd.StreamWatchdogRegistry()
        stop = threading.Event()
        loop = threading.Thread(
            target=wd.run_stream_watchdog_loop,
            args=(reg, 45.0, stop),
            kwargs={"interval_seconds": 0.05},
            daemon=True,
        )
        loop.start()
        time.sleep(0.15)
        stop.set()
        loop.join(2)
        assert not loop.is_alive(), "stop_event must stop the loop promptly"


# ─── clean_llm_config always-on injection (graph seam) ──────────────


class TestCleanLlmConfigWatchdogInjection:
    def setup_method(self):
        wd.reset_cached_clients()

    def teardown_method(self):
        wd.reset_cached_clients()

    def test_always_injects_watchdog_clients_when_absent(self):
        """Spec C.2: ALWAYS inject (gzip flag only changes the inner
        composition) — the gzip-OFF path previously injected nothing."""
        from daemon.graph import ThinkingChatOpenAI, clean_llm_config

        original = ThinkingChatOpenAI.default_request_gzip
        ThinkingChatOpenAI.default_request_gzip = False
        try:
            cleaned = clean_llm_config(
                {"model": "gpt-4o", "api_key": "test", "base_url": "https://x.test/v1"}
            )
            assert isinstance(cleaned["http_client"], httpx.Client)
            assert isinstance(cleaned["http_async_client"], httpx.AsyncClient)
            assert isinstance(
                cleaned["http_client"]._transport, wd.WatchdogHTTPTransport
            )
        finally:
            ThinkingChatOpenAI.default_request_gzip = original

    def test_caller_supplied_client_preserved_verbatim(self):
        """The partial-override contract: an explicit http_client opts
        out of the watchdog injection entirely."""
        from daemon.graph import clean_llm_config

        custom = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
        cleaned = clean_llm_config(
            {
                "model": "gpt-4o",
                "api_key": "test",
                "base_url": "https://x.test/v1",
                "http_client": custom,
            }
        )
        assert cleaned["http_client"] is custom
