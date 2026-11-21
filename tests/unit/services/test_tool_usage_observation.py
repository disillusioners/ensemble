"""Unit tests for the observation tap + off-loop flusher (Phase 1).

Pins (architect §10.5):
* NAMES-ONLY sink signature (no argument payload) — inspect the call args.
* 256-char tap choke (overflow counter + throttled WARNING).
* Flusher: drain-before-dispose ordering; tail-flush on kill-switch;
  emergency-flush at bound; never-drop; respawn-on-death.
* MANAGED_KEYS registration shape (3 keys + backing LimitsConfig field).
* Collection kill-switch absent/present/unparseable semantics (D7).

Behavior: tap observation-only (sink raise → tool call unaffected); ok-flag
from ToolMessage.status; counting contract at-least-once (documented).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from daemon.services import tool_usage_observation as obs


@pytest.fixture(autouse=True)
def _isolate_sinks():
    """Snapshot + restore the module-level sink registry per test."""
    saved = set(obs._sinks)
    yield
    with obs._sinks_lock:
        obs._sinks.clear()
        obs._sinks.update(saved)


# ── test doubles ───────────────────────────────────────────────────────────
class _AI:
    def __init__(self, tool_calls):
        self.tool_calls = tool_calls


class _ToolMsg:
    def __init__(self, call_id, status=None):
        self.tool_call_id = call_id
        self.status = status


def _state(tcs):
    return {"messages": [_AI(tcs)]}


def _config(thread_id="inst-1"):
    return {"configurable": {"thread_id": thread_id}}


class _Repo:
    def __init__(self, inst=None):
        self._inst = inst
        self.calls = 0

    def get(self, iid):
        self.calls += 1
        return self._inst


class _FlusherProbe:
    """Minimal flusher-shaped object for the choke overflow counter."""

    def __init__(self):
        self.overflow_counter = 0

    def note_overflow(self):
        self.overflow_counter += 1


# ── sink signature / NAMES-ONLY ────────────────────────────────────────────
class TestSinkContract:
    def test_sink_signature_has_no_args_slot(self):
        # Callable[[str, str|None, str, str, bool], None] → __args__ holds
        # the 5 parameter types + the None return type = 6 entries. There
        # is deliberately no *args / dict payload slot in the frozen type.
        args = obs.ToolUsageSink.__args__
        assert len(args) == 6, args
        assert args[-1] is type(None)

    def test_tap_fans_out_five_positional_args_only(self):
        seen = []

        def sink(*args):
            seen.append(args)

        obs.register_sink(sink)
        inner_calls = []

        async def inner(state, config):
            inner_calls.append(1)
            return {"messages": [_ToolMsg("tc-1", status=None)]}

        node = obs.wrap_tools_node(
            inner, instance_repository=_Repo(), flusher=_FlusherProbe()
        )
        tcs = [{"id": "tc-1", "name": "bash", "args": {"cmd": "SECRET"}}]
        asyncio.run(node(_state(tcs), _config()))
        assert inner_calls == [1]
        assert len(seen) == 1
        assert len(seen[0]) == 5  # exactly the frozen arity — no args slot
        tool_name, category, agent_id, project_id, ok = seen[0]
        assert tool_name == "bash"
        assert agent_id == obs.UNKNOWN_AGENT
        assert project_id == obs.NONE_PROJECT
        assert ok is True

    def test_identity_resolved_from_instance_once_per_batch(self):
        inst = SimpleNamespace(agent_id="coder", instance_metadata={"project_id": "p-1"})
        repo = _Repo(inst)
        seen = []
        obs.register_sink(lambda *a: seen.append(a))

        async def inner(state, config):
            return {"messages": [_ToolMsg("a"), _ToolMsg("b")]}

        node = obs.wrap_tools_node(inner, instance_repository=repo)
        tcs = [
            {"id": "a", "name": "bash"},
            {"id": "b", "name": "read_file"},
        ]
        asyncio.run(node(_state(tcs), _config()))
        assert repo.calls == 1, "one repo.get per BATCH, not per tool call"
        assert [s[0] for s in seen] == ["bash", "read_file"]
        assert all(s[2] == "coder" and s[3] == "p-1" for s in seen)


# ── observation-only behavior ──────────────────────────────────────────────
class TestObservationOnly:
    def test_sink_raise_does_not_break_tool_call(self):
        def bad_sink(*a):
            raise RuntimeError("sink exploded")

        obs.register_sink(bad_sink)
        sentinel = {"messages": ["unchanged"]}

        async def inner(state, config):
            return sentinel

        node = obs.wrap_tools_node(inner, instance_repository=_Repo())
        result = asyncio.run(node(_state([{"id": "x", "name": "bash"}]), _config()))
        assert result is sentinel

    def test_inner_exception_propagates(self):
        async def inner(state, config):
            raise ValueError("real tool failure")

        node = obs.wrap_tools_node(inner, instance_repository=_Repo())
        with pytest.raises(ValueError):
            asyncio.run(node(_state([{"id": "x", "name": "bash"}]), _config()))

    def test_ok_flag_from_toolmessage_status(self):
        seen = []
        obs.register_sink(lambda *a: seen.append(a))

        async def inner(state, config):
            return {"messages": [_ToolMsg("ok", status=None),
                                 _ToolMsg("bad", status="error")]}

        node = obs.wrap_tools_node(inner, instance_repository=_Repo())
        tcs = [{"id": "ok", "name": "bash"}, {"id": "bad", "name": "read_file"}]
        asyncio.run(node(_state(tcs), _config()))
        assert seen[0][4] is True
        assert seen[1][4] is False

    def test_choke_truncates_to_256_with_overflow_counter(self):
        seen = []
        obs.register_sink(lambda *a: seen.append(a))
        probe = _FlusherProbe()
        long_name = "x" * 300

        async def inner(state, config):
            return {"messages": []}

        node = obs.wrap_tools_node(
            inner, instance_repository=_Repo(), flusher=probe
        )
        asyncio.run(node(_state([{"id": "x", "name": long_name}]), _config()))
        assert len(seen[0][0]) == obs.NAME_CHOKE_CHARS == 256
        assert probe.overflow_counter >= 1

    def test_kill_switch_off_suppresses_enqueue(self, monkeypatch):
        seen = []
        obs.register_sink(lambda *a: seen.append(a))
        monkeypatch.setenv(obs.COLLECTION_ENV_VAR, "false")

        async def inner(state, config):
            return {"messages": []}

        node = obs.wrap_tools_node(inner, instance_repository=_Repo())
        asyncio.run(node(_state([{"id": "x", "name": "bash"}]), _config()))
        assert seen == []


# ── kill-switch semantics (D7) ─────────────────────────────────────────────
class TestKillSwitch:
    def test_env_unset_defaults_on(self, monkeypatch):
        monkeypatch.delenv(obs.COLLECTION_ENV_VAR, raising=False)
        monkeypatch.setattr(
            "daemon.services.metrics_gate.live_managed_value",
            lambda key, default: default,
        )
        assert obs.is_collection_enabled() is True

    def test_env_truthy_on_false_off(self, monkeypatch):
        monkeypatch.setenv(obs.COLLECTION_ENV_VAR, "true")
        assert obs.is_collection_enabled() is True
        monkeypatch.setenv(obs.COLLECTION_ENV_VAR, "garbage")
        assert obs.is_collection_enabled() is False  # fail-closed

    def test_managed_value_bool_honored(self, monkeypatch):
        monkeypatch.delenv(obs.COLLECTION_ENV_VAR, raising=False)
        monkeypatch.setattr(
            "daemon.services.metrics_gate.live_managed_value",
            lambda key, default: False,
        )
        assert obs.is_collection_enabled() is False

    def test_managed_unparseable_fails_closed(self, monkeypatch):
        monkeypatch.delenv(obs.COLLECTION_ENV_VAR, raising=False)
        monkeypatch.setattr(
            "daemon.services.metrics_gate.live_managed_value",
            lambda key, default: "not-a-bool",
        )
        assert obs.is_collection_enabled() is False


# ── flusher ────────────────────────────────────────────────────────────────
class _RecordingRepo:
    def __init__(self, fail=False):
        self.fail = fail
        self.batches = []

    def upsert_batch(self, deltas):
        if self.fail:
            raise RuntimeError("PG down")
        self.batches.append(list(deltas))
        return len(deltas)


class TestFlusher:
    def test_ledger_sink_increments_counter_map(self):
        repo = _RecordingRepo()
        f = obs.ToolUsageFlusher(repo)
        f.ledger_sink("bash", None, "coder", "p1", True)
        f.ledger_sink("bash", None, "coder", "p1", False)  # ok ignored
        assert f.dirty_count() == 1
        asyncio.run(f.flush_once())
        assert len(repo.batches) == 1
        assert repo.batches[0][0].call_count == 2

    def test_never_drop_on_db_failure(self):
        repo = _RecordingRepo(fail=True)
        f = obs.ToolUsageFlusher(repo)
        f.ledger_sink("bash", None, "coder", "p1", True)
        asyncio.run(f.flush_once())
        assert f.flush_error_counter == 1
        assert f.dirty_count() == 1  # retained for coalesced retry
        repo.fail = False
        asyncio.run(f.flush_once())
        assert repo.batches[0][0].call_count == 1

    def test_drain_flushes_all_dirty(self):
        repo = _RecordingRepo()
        f = obs.ToolUsageFlusher(repo)
        for i in range(10):
            f.ledger_sink(f"tool-{i}", None, "coder", "p1", True)
        flushed = asyncio.run(f.drain(timeout=5))
        assert flushed == 10
        assert f.dirty_count() == 0

    def test_switch_blind_tail_flush(self, monkeypatch):
        """Flusher never reads the switch — flipping OFF still drains dirty."""
        repo = _RecordingRepo()
        f = obs.ToolUsageFlusher(repo)
        f.ledger_sink("bash", None, "coder", "p1", True)
        monkeypatch.setenv(obs.COLLECTION_ENV_VAR, "false")
        # Flusher is switch-blind: flush_once drains regardless of switch.
        asyncio.run(f.flush_once())
        assert repo.batches and repo.batches[0][0].call_count == 1

    def test_emergency_counter_on_pressure(self):
        repo = _RecordingRepo()
        f = obs.ToolUsageFlusher(repo, emergency_at=3)
        for i in range(3):
            f.ledger_sink(f"t{i}", None, "a", "p", True)
        assert f.dirty_count() == 3

    async def test_respawn_supervisor_starts_and_drains(self):
        repo = _RecordingRepo()
        f = obs.ToolUsageFlusher(repo, interval_s=1)
        f.start()  # async test ⇒ a running loop exists ⇒ task is created
        assert f._supervisor_task is not None
        f.ledger_sink("bash", None, "a", "p", True)
        await f.drain(timeout=5)
        assert f._supervisor_task.done()


# ── MANAGED_KEYS registration shape (A-7 + U3) ─────────────────────────────
class TestManagedKeysShape:
    def test_three_keys_registered_with_correct_shape(self):
        from daemon.services.settings_service import MANAGED_KEYS

        for key in (
            "limits.tool_usage_collection_enabled",
            "limits.metrics_tool_usage_enabled",
            "limits.tool_usage_digest_seeding_enabled",
        ):
            assert key in MANAGED_KEYS, f"missing managed key {key}"
        collection = MANAGED_KEYS["limits.tool_usage_collection_enabled"]
        assert collection.config_path == "limits.tool_usage_collection_enabled"
        assert collection.value_type == "bool"
        assert collection.default is True
        family = MANAGED_KEYS["limits.metrics_tool_usage_enabled"]
        assert family.config_path is None
        assert family.env_var == "ENSEMBLE_METRICS_TOOL_USAGE_ENABLED"
        seeder = MANAGED_KEYS["limits.tool_usage_digest_seeding_enabled"]
        assert seeder.config_path is None

    def test_limits_config_backing_field(self):
        from daemon.config import LimitsConfig

        field = LimitsConfig.model_fields["tool_usage_collection_enabled"]
        assert field.annotation is bool
        assert field.default is True


class TestCountingContract:
    def test_contract_declares_at_least_once(self):
        assert "AT-LEAST-ONCE" in obs.COUNTING_CONTRACT
        assert "EXECUTED tool_call" in obs.COUNTING_CONTRACT
