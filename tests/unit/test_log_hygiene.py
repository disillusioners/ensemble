"""Log-noise-hygiene tests.

Covers the four new surfaces:

(a) ``daemon/services/log_sumup.py`` — the generic windowed sumup
    utility (count/window/sum/last fields, idle silence, fail-open).
(b) the LLM heartbeat (``daemon/graph.py``) — emit format + idle variant.
(c) the dual-level handler wiring (``daemon/logging_setup.py``) —
    console drops DEBUG while the file keeps it; ANSI stripped in the
    file; back-compat logger-gate composition (explicit ``info`` is NOT
    a ceiling — shipped .env files carry it; ``warning`` is).
(d) the CheckpointPerf read lane — the per-read line is GONE at BOTH
    INFO and DEBUG, the gate still silences the whole family, metrics
    keep recording, and the windowed sumup carries the aggregate.

Mirrors the ``reset_for_tests`` conventions of the metrics registry.
"""
from __future__ import annotations

import importlib
import io
import logging
from types import SimpleNamespace

import pytest

from daemon.checkpoint_metrics import (
    checkpoint_read_bytes_total,
    checkpoint_read_ops_total,
    read_size_bucket_label,
)
from daemon.checkpoint_perf import (
    log_cache_hit,
    log_checkpoint_read,
)
from daemon.services import log_sumup


@pytest.fixture(autouse=True)
def _clean_sumup_registry():
    """Every test starts and ends with an EMPTY sumup registry (no timer)."""
    log_sumup.reset_for_tests()
    yield
    log_sumup.reset_for_tests()


# ─────────────────────────────────────────────────────────────────────────────
# (a) log_sumup utility
# ─────────────────────────────────────────────────────────────────────────────


class TestLogSumupUtility:
    def _register(self, *, emit_idle=False, window_s=None):
        lines: list[str] = []

        def render(snapshot):
            line = (
                f"sumup family={snapshot['family']} window_s={snapshot['window_s']} "
                f"count={snapshot['count']} sums={snapshot['sums']} "
                f"lasts={snapshot['lasts']} sets={snapshot['sets']}"
            )
            lines.append(line)
            return line

        log_sumup.ensure_family(
            "t-family", render=render, logger=logging.getLogger("daemon.test"),
            window_s=window_s, emit_idle=emit_idle,
        )
        return lines

    def test_record_and_flush_render_fields(self):
        lines = self._register()
        log_sumup.record(
            "t-family",
            count=1,
            sums={"ops": 1, "bytes": 128},
            lasts={"op": "aget"},
            sets={"model": "m-1"},
        )
        log_sumup.record("t-family", count=1, sums={"ops": 1, "bytes": 64}, lasts={"op": "aget_tuple"})
        emitted = log_sumup.flush("t-family", force=True)
        assert emitted is not None
        assert lines == [emitted]
        assert "count=2" in emitted
        assert "window_s=180" in emitted  # DEFAULT_WINDOW_S
        assert "'ops': 2" in emitted
        assert "'bytes': 192" in emitted
        assert "'op': 'aget_tuple'" in emitted  # last wins
        assert "'model': 'm-1'" in emitted

    def test_window_gating_without_force(self, monkeypatch):
        self._register()
        fake_clock = {"t": 1000.0}
        monkeypatch.setattr(
            log_sumup, "time", SimpleNamespace(monotonic=lambda: fake_clock["t"])
        )
        log_sumup.ensure_family(
            "t-window", render=lambda s: f"w={s['count']}",
            logger=logging.getLogger("daemon.test"), window_s=5,
        )
        log_sumup.record("t-window", sums={"ops": 1})
        # Not due: the window started "now" (t=1000), only 2s elapsed.
        fake_clock["t"] = 1002.0
        assert log_sumup.flush("t-window", force=False) is None
        # Due: 6s >= 5s window.
        fake_clock["t"] = 1006.0
        assert log_sumup.flush("t-window", force=False) == "w=1"

    def test_fail_open_on_bad_input(self):
        """Bad field values / unknown families / bad counts NEVER raise."""
        self._register()
        log_sumup.record("no-such-family", sums={"ops": 1})  # unknown family: no-op
        log_sumup.record("t-family", sums={"ops": "not-a-number"})  # bad fold skipped
        log_sumup.record("t-family", sums={"ops": 1}, lasts=None, sets=None)
        log_sumup.record("t-family", count="garbage")  # count falls back to +1
        emitted = log_sumup.flush("t-family", force=True)
        assert emitted is not None  # flush survived
        assert "count=3" in emitted  # 1 (bad count fallback) + 1 + 1
        assert "'ops': 1" in emitted  # the bad string field skipped, the good one folded

    def test_flush_unknown_family_is_none(self):
        assert log_sumup.flush("never-registered", force=True) is None

    def test_quiet_family_silent_without_emit_idle(self):
        self._register(emit_idle=False)
        assert log_sumup.flush("t-family", force=True) is None  # zero records ⇒ no line

    def test_idle_family_emits_when_emit_idle_true(self):
        self._register(emit_idle=True)
        assert log_sumup.flush("t-family", force=True) is not None  # zero records ⇒ idle line

    def test_reset_for_tests_clears_registry(self):
        self._register()
        log_sumup.reset_for_tests()
        assert log_sumup.flush("t-family", force=True) is None

    def test_render_exception_is_swallowed(self):
        log_sumup.ensure_family(
            "t-bad", render=lambda s: 1 / 0, logger=logging.getLogger("daemon.test"),
        )
        log_sumup.record("t-bad")
        assert log_sumup.flush("t-bad", force=True) is None  # fail-open, no raise


# ─────────────────────────────────────────────────────────────────────────────
# (b) LLM heartbeat
# ─────────────────────────────────────────────────────────────────────────────


class TestLLMHeartbeat:
    def test_render_idle_variant(self):
        from daemon import graph

        snapshot = {"window_s": 180, "count": 0, "sums": {}, "lasts": {},
                    "sets": {}, "last_event_age_s": None}
        assert graph._render_llm_heartbeat(snapshot) == \
            "[LLM] heartbeat 180s: 0 invocations (idle)"

    def test_render_active_variant(self):
        from daemon import graph

        snapshot = {
            "window_s": 180, "count": 3,
            "sums": {"invocations": 2, "tool_calls": 1},
            "lasts": {}, "sets": {"model": "gpt-x"},
            "last_event_age_s": 7.4,
        }
        assert graph._render_llm_heartbeat(snapshot) == (
            "[LLM] heartbeat 180s: invocations=2 tool_calls=1 "
            "last=7s ago model=gpt-x"
        )

    def test_heartbeat_emit_via_sumup(self, caplog):
        from daemon import graph

        graph._llm_heartbeat(sums={"invocations": 1}, sets={"model": "gpt-x"})
        graph._llm_heartbeat(sums={"invocations": 1}, sets={"model": "gpt-y"})
        graph._llm_heartbeat(sums={"tool_calls": 1})
        with caplog.at_level(logging.INFO, logger="daemon.graph"):
            emitted = log_sumup.flush("llm", force=True)
        assert emitted is not None
        assert "invocations=2" in emitted
        assert "tool_calls=1" in emitted
        assert "last=" in emitted and "ago" in emitted
        assert "model=gpt-y" in emitted  # last set wins
        assert emitted.startswith("[LLM] heartbeat ")
        # The heartbeat is an INFO record on the daemon.graph logger.
        assert any(rec.levelno == logging.INFO and "[LLM] heartbeat" in rec.message
                   for rec in caplog.records)

    def test_heartbeat_idle_variant_emitted_with_zero_records(self, caplog):
        from daemon import graph

        graph._ensure_llm_heartbeat_family()  # emit_idle=True family, ZERO records
        with caplog.at_level(logging.INFO, logger="daemon.graph"):
            emitted = log_sumup.flush("llm", force=True)
        assert emitted == "[LLM] heartbeat 180s: 0 invocations (idle)"

    def test_heartbeat_record_never_logs_per_event(self, caplog):
        from daemon import graph

        with caplog.at_level(logging.DEBUG, logger="daemon.graph"):
            graph._llm_heartbeat(sums={"invocations": 1}, sets={"model": "m"})
        assert not any("[LLM] heartbeat" in r.message for r in caplog.records)


# ─────────────────────────────────────────────────────────────────────────────
# (c) dual-level handlers
# ─────────────────────────────────────────────────────────────────────────────


_LOG_ENV_VARS = (
    "LOG_LEVEL", "LOG_LEVEL_DAEMON",
    "ENSEMBLE_LOG_CONSOLE_LEVEL", "ENSEMBLE_LOG_FILE_LEVEL", "DAEMON_LOG_DIR",
)


class _FreshInstall:
    """Install a FRESH logging_setup wiring under controlled env; the
    fixture teardown removes exactly what was added and restores the
    root level."""

    def __init__(self, monkeypatch, tmp_path):
        self._monkeypatch = monkeypatch
        self._tmp_path = tmp_path
        self._old_level = logging.getLogger().level
        self._known: list[logging.Handler] = []
        self.module = None

    def mark_known_handlers(self):
        """Snapshot the pre-install root handlers (collection-time wiring)."""
        self._known = list(logging.getLogger().handlers)

    def install(self, **env_overrides) -> object:
        for var in _LOG_ENV_VARS:
            self._monkeypatch.delenv(var, raising=False)
        for var, value in env_overrides.items():
            self._monkeypatch.setenv(var, value)
        self._monkeypatch.setenv("DAEMON_LOG_DIR", str(self._tmp_path))
        from daemon import logging_setup

        self.module = importlib.reload(logging_setup)
        self.module.install_root_logging()
        return self.module

    def added_handlers(self) -> list[logging.Handler]:
        known = set(self._known)
        return [h for h in logging.getLogger().handlers if h not in known]

    def cleanup(self):
        root = logging.getLogger()
        for handler in self.added_handlers():
            root.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass
        root.setLevel(self._old_level)


@pytest.fixture()
def fresh_logging(monkeypatch, tmp_path):
    tracker = _FreshInstall(monkeypatch, tmp_path)
    tracker.mark_known_handlers()
    yield tracker
    tracker.cleanup()


class TestDualLevelHandlers:
    def test_console_info_file_debug_split(self, fresh_logging, tmp_path):
        """THE LANDMINE: root/daemon loggers open to min(handler levels)
        (DEBUG under defaults) so the console=INFO / file=DEBUG split
        actually works — a DEBUG record reaches the file handler and is
        dropped by the console handler."""
        fresh_logging.install()
        root = logging.getLogger()
        # Both loggers sit at the MIN of the two handler levels (DEBUG).
        assert root.level == logging.DEBUG
        assert logging.getLogger("daemon").level == logging.DEBUG

        console = next(h for h in fresh_logging.added_handlers()
                       if isinstance(h, logging.StreamHandler)
                       and not isinstance(h, logging.FileHandler))
        file_h = next(h for h in fresh_logging.added_handlers()
                      if isinstance(h, logging.FileHandler))
        assert console.level == logging.INFO
        assert file_h.level == logging.DEBUG

        proof = logging.getLogger("daemon.split-proof")
        captured = io.StringIO()
        old_stream = console.stream
        console.stream = captured
        try:
            proof.debug("debug-only-payload-12345")
            proof.info("info-payload-67890")
        finally:
            console.stream = old_stream

        file_text = (tmp_path / "ensemble.log").read_text()
        assert "debug-only-payload-12345" in file_text   # file keeps DEBUG
        assert "info-payload-67890" in file_text
        assert "debug-only-payload-12345" not in captured.getvalue()  # console drops DEBUG
        assert "info-payload-67890" in captured.getvalue()            # console keeps INFO

    def test_ansi_stripped_in_file(self, fresh_logging, tmp_path):
        fresh_logging.install()
        logging.getLogger("daemon.ansi-proof").info("\x1b[92mGREEN\x1b[0m payload")
        file_text = (tmp_path / "ensemble.log").read_text()
        assert "GREEN payload" in file_text
        assert "\x1b[" not in file_text

    def test_explicit_info_is_not_a_ceiling(self, fresh_logging):
        """Shipped .env files carry LOG_LEVEL=info EXPLICITLY — it must
        not pin the loggers at INFO (that would kill the file=DEBUG
        split). Default-band values open to the handler minimum."""
        fresh_logging.install(LOG_LEVEL="info", LOG_LEVEL_DAEMON="info")
        assert logging.getLogger().level == logging.DEBUG
        assert logging.getLogger("daemon").level == logging.DEBUG

    def test_daemon_debug_env_stays_debug(self, fresh_logging):
        fresh_logging.install(LOG_LEVEL="info", LOG_LEVEL_DAEMON="debug")
        assert logging.getLogger().level == logging.DEBUG
        assert logging.getLogger("daemon").level == logging.DEBUG

    def test_warning_env_is_a_hard_ceiling(self, fresh_logging):
        """Back-compat: a TIGHTER env value quiets ALL surfaces exactly
        as before the split (the logger gate wins)."""
        fresh_logging.install(LOG_LEVEL="warning", LOG_LEVEL_DAEMON="warning")
        assert logging.getLogger().level == logging.WARNING
        assert logging.getLogger("daemon").level == logging.WARNING

    def test_handler_level_env_vars(self, fresh_logging, tmp_path):
        fresh_logging.install(
            ENSEMBLE_LOG_CONSOLE_LEVEL="warning",
            ENSEMBLE_LOG_FILE_LEVEL="error",
        )
        handlers = fresh_logging.added_handlers()
        console = next(h for h in handlers
                       if isinstance(h, logging.StreamHandler)
                       and not isinstance(h, logging.FileHandler))
        file_h = next(h for h in handlers if isinstance(h, logging.FileHandler))
        assert console.level == logging.WARNING
        assert file_h.level == logging.ERROR
        # The default-band logger gate opens to min(handlers_min, env=INFO)
        # = INFO; the tightened handler levels then filter the surfaces.
        assert logging.getLogger().level == logging.INFO
        # The rotation policy is UNCHANGED (10 MB × 5).
        assert file_h.maxBytes == 10 * 1024 * 1024
        assert file_h.backupCount == 5


# ─────────────────────────────────────────────────────────────────────────────
# (d) CheckpointPerf read lane
# ─────────────────────────────────────────────────────────────────────────────


class TestCheckpointPerfReadSumup:
    def test_per_read_line_gone_at_info_and_debug(self, caplog):
        """NOTHING may be logged per read — at EITHER level."""
        with caplog.at_level(logging.INFO, logger="daemon.checkpoint_perf"):
            log_checkpoint_read("aget", "t-1", bytes_=1234, rows=2)
        with caplog.at_level(logging.DEBUG, logger="daemon.checkpoint_perf"):
            log_checkpoint_read("aget_tuple", "t-1", bytes_=2048, rows=4)
            log_cache_hit("t-1", 1, bytes_=4096)
        msgs = [r.message for r in caplog.records]
        assert not any("op=read_size" in m for m in msgs)
        assert not any("op=cache_hit" in m for m in msgs)
        # No per-read line of ANY level escaped: only a window sumup may exist.
        assert msgs == [] or all("read sumup" in m for m in msgs)

    def test_sumup_window_line_format(self, caplog):
        """ONE INFO line per window carrying the spec'd aggregate fields."""
        log_checkpoint_read("aget", "t-1", bytes_=1234, rows=2)
        log_checkpoint_read("aget_tuple", "t-2", bytes_=100 * 1024, rows=4)
        log_cache_hit("t-3", 1, bytes_=2048)
        with caplog.at_level(logging.INFO, logger="daemon.checkpoint_perf"):
            emitted = log_sumup.flush("checkpoint_read", force=True)
        assert emitted is not None
        assert any(
            r.levelno == logging.INFO and r.message == emitted for r in caplog.records
        ), "the sumup must be an INFO record"
        # ops=3 (2 reads + 1 hit), hits=1, misses=2, bytes_total=1234+102400+2048.
        assert "ops=3" in emitted
        assert "hits=1" in emitted
        assert "misses=2" in emitted
        assert f"bytes_total={1234 + 100 * 1024 + 2048}" in emitted
        assert "last_op=cache_hit" in emitted
        assert "last_bytes=2048" in emitted
        assert "bucket=65536" in emitted  # 2048 falls in the first bucket
        assert read_size_bucket_label(2048) == "65536"  # and the label helper agrees

    def test_gate_off_silences_the_whole_family(self, caplog, monkeypatch):
        """CHECKPOINT_PERF_LOGS=0 ⇒ no per-read fold, NO sumup either."""
        monkeypatch.setenv("CHECKPOINT_PERF_LOGS", "0")
        with caplog.at_level(logging.DEBUG, logger="daemon.checkpoint_perf"):
            log_checkpoint_read("aget", "t-1", bytes_=1234, rows=2)
            log_cache_hit("t-1", 1, bytes_=2048)
            assert log_sumup.flush("checkpoint_read", force=True) is None
        assert not any("[CheckpointPerf]" in r.message for r in caplog.records)

    def test_metrics_still_record_every_read(self):
        """Byte-identical metric behavior: the gate-ON read still moves
        the series (delta-based spot-check on ops + bytes)."""
        before_ops = checkpoint_read_ops_total.get(op="aget")
        before_bytes = checkpoint_read_bytes_total.get(op="aget")
        log_checkpoint_read("aget", "t-1", bytes_=1234, rows=2)
        assert checkpoint_read_ops_total.get(op="aget") == before_ops + 1
        assert checkpoint_read_bytes_total.get(op="aget") == before_bytes + 1234
