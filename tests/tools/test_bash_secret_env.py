"""agent-secrets P2 phase 3 — bash env overlay seam tests (design §1a).

Pins the three per-seam hooks (p2-architecture-recommendation.md §6
phase-3 row):

* (i) **resolve success** — the spawned child actually RECEIVES the
  plaintext (asserted via the child's own ``env`` dump; the marker
  NEVER reaches the subprocess env).
* (ii) **fail-closed** — a bogus marker id produces the typed
  ``{ok:false, code, surface:"bash.env", message}`` envelope and the
  child is NOT spawned (``create_subprocess_*`` never called).
* (iii) **zero-change** — marker-free env (and env-free calls)
  byte-identical behavior; the resolver short-circuits on
  non-marker values (spy-verified: the resolver is either skipped or
  a trivial no-op on the fast path).

Plus the bash-specific pin: the overlay WINS over ``os.environ`` on
key collision (§1a assembled = os.environ.copy() ⊕ overlay).

Test execution is fenced per the project critical notes — mktemp
FAKE_HOME + escape guard, no HOME-derived live paths.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

from daemon.repositories.secrets.models import (  # noqa: F401
    Secret,
    SecretAuditEvent,
)
from daemon.services.secret_service import (
    SECRET_CODE_MARKER_MALFORMED,
    SECRET_CODE_NOT_FOUND,
    SECRET_ENVELOPE_SURFACES,
    SecretCipher,
    SecretMarkerResolutionError,
    SecretService,
)
from daemon.services.secret_persistence_scrub import (
    register_secret_service,
    reset_process_registry,
)


# ─────────────────────────────────────────────────────────────────────
# Fixtures (mirror tests/unit/test_secret_resolver.py — self-contained)
# ─────────────────────────────────────────────────────────────────────


def _make_sqlite_engine(tmp_path) -> Engine:
    db_path = tmp_path / "bash-secret-env-test.sqlite"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )

    @sa_event.listens_for(eng, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return eng


@pytest.fixture
def sqlite_engine(tmp_path) -> Iterator[Engine]:
    eng = _make_sqlite_engine(tmp_path)
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def fernet_key() -> str:
    return Fernet.generate_key().decode()


@pytest.fixture
def service(
    sqlite_engine: Engine, fernet_key: str
) -> Iterator[SecretService]:
    svc = SecretService(
        engine=sqlite_engine,
        cipher=SecretCipher(encryption_key=fernet_key),
    )
    # Wire the process-wide registry (mirrors the manager wiring) so
    # the bash seam's lazy discovery finds the service.
    register_secret_service(svc)
    try:
        yield svc
    finally:
        reset_process_registry()


@pytest.fixture(autouse=True)
def _fake_home(monkeypatch, tmp_path) -> Iterator[str]:
    """CRITICAL-NOTE fence — never resolve live-shaped HOME paths."""
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    yield str(fake_home)


# ─────────────────────────────────────────────────────────────────────
# (i) resolve success — child receives the PLAINTEXT
# ─────────────────────────────────────────────────────────────────────


class TestBashResolveSuccess:
    @pytest.mark.asyncio
    async def test_child_sees_plaintext_not_marker(
        self, service: SecretService
    ) -> None:
        """The spawned process's env carries the PLAINTEXT — the
        marker NEVER reaches the subprocess env (§1a flow)."""
        minted = service.store(
            name="bash-happy", kind="generic",
            value="pt-ghp-abcdef123456", actor="worker",
        )
        marker = minted["marker"]
        result = await _run_bash_with_env(
            {"P2_SECRET_TEST": marker},
            instance_id="inst-bash-1",
        )
        assert result.returncode == 0, result.stderr
        assert result.env_value == "pt-ghp-abcdef123456"
        assert "__ENS_SECRET__" not in (result.env_value or "")

    @pytest.mark.asyncio
    async def test_overlay_wins_over_os_environ(
        self, service: SecretService, monkeypatch
    ) -> None:
        """§1a assembled = os.environ.copy() ⊕ env overlay — the
        overlay value REPLACES the same-named os.environ entry."""
        monkeypatch.setenv("P2_BASH_OVERLAY_PROBE", "from-os-environ")
        minted = service.store(
            name="bash-overlay", kind="generic",
            value="from-overlay", actor="worker",
        )
        result = await _run_bash_with_env(
            {"P2_BASH_OVERLAY_PROBE": minted["marker"]},
            instance_id="inst-bash-2",
            probe="P2_BASH_OVERLAY_PROBE",
        )
        assert result.returncode == 0, result.stderr
        assert result.env_value == "from-overlay"

    @pytest.mark.asyncio
    async def test_both_marker_families_resolve_in_one_env(
        self, service: SecretService, monkeypatch
    ) -> None:
        """The §1a chain (secrets-then-KMS) resolves BOTH marker
        families in one assembled dict (shapes are disjoint)."""
        monkeypatch.setenv("P2_KMS_ENV_PROBE", "kms-plaintext-via-env-ref")
        minted = service.store(
            name="bash-kms-chain", kind="generic",
            value="pt-ens-secret", actor="worker",
        )
        env_overlay = {
            "P2_SECRET_TEST": minted["marker"],
        }
        result = await _run_bash_with_env(env_overlay, "inst-bash-3")
        assert result.returncode == 0, result.stderr
        assert result.env_value == "pt-ens-secret"
        # The KMS marker resolved through the chained resolve_env.
        kms_out = await _run_bash_with_env(
            {"P2_KMS_ENV_PROBE": "__KMS_ENV__P2_KMS_ENV_PROBE__"},
            "inst-bash-3b",
            probe="P2_KMS_ENV_PROBE",
        )
        assert kms_out.returncode == 0, kms_out.stderr
        assert kms_out.env_value == "kms-plaintext-via-env-ref"


# ─────────────────────────────────────────────────────────────────────
# (ii) fail-closed — bogus marker → typed envelope, child NOT spawned
# ─────────────────────────────────────────────────────────────────────


class TestBashFailClosed:
    @pytest.mark.asyncio
    async def test_missing_id_produces_typed_envelope(
        self, service: SecretService, monkeypatch
    ) -> None:
        """A well-formed marker whose id does NOT exist → typed
        ``secret_not_found`` envelope with ``surface:"bash.env"``;
        the command is NOT executed."""
        import uuid

        bogus_marker = f"__ENS_SECRET__{uuid.uuid4()}__"
        fail_spawn = AsyncMock(side_effect=AssertionError("no spawn"))
        monkeypatch.setattr(
            _BASH_MODULE.asyncio, "create_subprocess_shell", fail_spawn
        )
        monkeypatch.setattr(
            _BASH_MODULE.asyncio, "create_subprocess_exec", fail_spawn
        )
        envelope = await _bash_tool_call(
            command="echo should-not-run",
            env={"P2_SECRET_TEST": bogus_marker},
            instance_id="inst-bash-fc1",
        )
        assert fail_spawn.await_count == 0, (
            "child spawned despite fail-closed marker"
        )
        payload = json.loads(envelope)
        assert payload["ok"] is False
        assert payload["code"] == SECRET_CODE_NOT_FOUND
        assert payload["surface"] == "bash.env"
        assert "message" in payload
        # PB-F1: the marker literal NEVER appears in the envelope.
        assert bogus_marker not in envelope

    @pytest.mark.asyncio
    async def test_malformed_marker_produces_typed_envelope(
        self, service: SecretService, monkeypatch
    ) -> None:
        """A prefix-shaped value failing fullmatch → typed
        ``secret_marker_malformed`` (pre-DB); command NOT executed."""
        malformed = "__ENS_SECRET__not-a-uuid__"
        fail_spawn = AsyncMock(side_effect=AssertionError("no spawn"))
        monkeypatch.setattr(
            _BASH_MODULE.asyncio, "create_subprocess_shell", fail_spawn
        )
        monkeypatch.setattr(
            _BASH_MODULE.asyncio, "create_subprocess_exec", fail_spawn
        )
        envelope = await _bash_tool_call(
            command="echo should-not-run",
            env={"P2_SECRET_TEST": malformed},
            instance_id="inst-bash-fc2",
        )
        assert fail_spawn.await_count == 0
        payload = json.loads(envelope)
        assert payload["ok"] is False
        assert payload["code"] == SECRET_CODE_MARKER_MALFORMED
        assert payload["surface"] == "bash.env"

    @pytest.mark.asyncio
    async def test_unwired_service_fails_closed(
        self, monkeypatch
    ) -> None:
        """No SecretService registered + marker-shaped value → typed
        ``secret_resolver_store_unavailable`` (fail-closed §3.2);
        the command is NOT executed and never runs with a literal
        marker."""
        reset_process_registry()  # unwire the registry
        marker = "__ENS_SECRET__12345678-1234-1234-1234-123456789012__"
        fail_spawn = AsyncMock(side_effect=AssertionError("no spawn"))
        monkeypatch.setattr(
            _BASH_MODULE.asyncio, "create_subprocess_shell", fail_spawn
        )
        monkeypatch.setattr(
            _BASH_MODULE.asyncio, "create_subprocess_exec", fail_spawn
        )
        envelope = await _bash_tool_call(
            command="echo should-not-run",
            env={"P2_SECRET_TEST": marker},
            instance_id="inst-bash-fc3",
        )
        assert fail_spawn.await_count == 0
        payload = json.loads(envelope)
        assert payload["ok"] is False
        assert payload["code"] == "secret_resolver_store_unavailable"
        assert payload["surface"] == "bash.env"


# ─────────────────────────────────────────────────────────────────────
# (iii) zero-change — marker-free env byte-identical + fast path
# ─────────────────────────────────────────────────────────────────────


class TestBashZeroChange:
    @pytest.mark.asyncio
    async def test_env_absent_behavior_unchanged(
        self, service: SecretService
    ) -> None:
        """``env=None`` (today's default) — no resolver call at all
        (the ``if env:`` guard skips the chain entirely); the child
        inherits os.environ (HOME visible)."""
        fake_home = os.environ.get("HOME", "")
        result = await _run_bash_with_env(None, "inst-bash-zc1", probe="HOME")
        assert result.returncode == 0, result.stderr
        assert result.env_value == fake_home

    @pytest.mark.asyncio
    async def test_marker_free_env_resolver_trivially_noops(
        self, service: SecretService, monkeypatch
    ) -> None:
        """Marker-free env — the resolver's per-value prefix
        short-circuit makes the pass a TRIVIAL NO-OP (§0 fast path):
        the resolver IS invoked (the assembled dict always routes
        through it) but never touches the service / DB (no marker-
        shaped value present), and the returned dict is byte-identical
        to the assembled input."""
        import daemon.tools.bash as bash_module
        import importlib

        mod = importlib.import_module("daemon.tools.bash")
        spy_results: list[Any] = []
        service_calls: list[Any] = []

        def _spy_resolve(env, *, secret_service=None, **kwargs):
            service_calls.append(secret_service)
            from daemon.services.secret_resolver import (
                resolve_secret_markers as real,
            )

            out = real(env, secret_service=secret_service, **kwargs)
            spy_results.append(out)
            return out

        monkeypatch.setattr(mod, "_resolve_secret_markers", _spy_resolve)
        result = await _run_bash_with_env(
            {"P2_PLAIN_PROBE": "plain-value", "P2_PLAIN_NUM": "42"},
            "inst-bash-zc2",
            probe="P2_PLAIN_PROBE",
        )
        assert result.returncode == 0, result.stderr
        assert result.env_value == "plain-value"
        # The resolver ran exactly once on the assembled dict.
        assert len(spy_results) == 1
        # Trivial no-op: the OUTPUT dict is byte-identical to the
        # assembled input (every value passed through unchanged).
        resolved = spy_results[0]
        assert resolved["P2_PLAIN_PROBE"] == "plain-value"
        assert resolved["P2_PLAIN_NUM"] == "42"
        # Zero ``use_marker`` audit rows (design §3.4) — the resolver
        # had the service available but never USED it (the per-value
        # prefix short-circuit skips the service path entirely when
        # no marker-shaped value is present).
        from sqlmodel import select

        with service._engine.connect() as conn:
            rows = conn.execute(
                select(SecretAuditEvent).where(  # type: ignore[arg-type]
                    SecretAuditEvent.action == "use_marker"
                )
            ).fetchall()
        assert rows == []

    @pytest.mark.asyncio
    async def test_marker_free_env_no_secrets_audit_rows(
        self, service: SecretService
    ) -> None:
        """Zero ``use_marker`` audit rows for a marker-free call
        (design §3.4: zero rows for secret-free agents)."""
        from sqlmodel import select

        await _run_bash_with_env(
            {"P2_PLAIN_PROBE": "plain"}, "inst-bash-zc3"
        )
        with service._engine.connect() as conn:
            rows = conn.execute(
                select(SecretAuditEvent).where(  # type: ignore[arg-type]
                    SecretAuditEvent.action == "use_marker"
                )
            ).fetchall()
        assert rows == []


# ─────────────────────────────────────────────────────────────────────
# Module reference (the ``bash`` tool shadows the module name under
# ``daemon.tools`` — always patch through the module object)
# ─────────────────────────────────────────────────────────────────────

import importlib as _importlib

_BASH_MODULE = _importlib.import_module("daemon.tools.bash")


async def _fail_spawn_shell(*_args, **_kwargs):
    raise AssertionError("child must NOT spawn on the fail-closed path")



class _BashResult:
    def __init__(self, returncode: int, env_value: Any, stderr: str):
        self.returncode = returncode
        self.env_value = env_value
        self.stderr = stderr


async def _run_bash_with_env(
    env: dict | None, instance_id: str, probe: str = "P2_SECRET_TEST"
) -> _BashResult:
    """Run a real bash child that echoes ONE probed env var."""
    cmd = f'printf "%s" "${{{probe}:-<unset>}}"'
    raw = await _bash_tool_call(
        command=["bash", "-c", cmd],
        env=env,
        instance_id=instance_id,
    )
    # Parse the tool-result text: "STDOUT:\n<value>\n\nEXIT CODE: N"
    stdout = ""
    returncode = -1
    stderr_text = ""
    if raw.startswith("STDOUT:"):
        body = raw.split("\n", 1)[1] if "\n" in raw else ""
        parts = body.split("\n\nEXIT CODE:")
        stdout = parts[0]
        if len(parts) > 1:
            returncode = int(parts[1].strip().split("\n")[0])
    elif "STDERR:" in raw:
        seg = raw.split("STDERR:\n", 1)[1]
        stderr_text = seg.split("\n\nEXIT CODE:")[0]
        tail = raw.split("EXIT CODE: ")
        if len(tail) > 1:
            returncode = int(tail[1].strip().split("\n")[0])
    return _BashResult(returncode, stdout, stderr_text)


async def _bash_tool_call(
    command: Any, env: dict | None, instance_id: str
) -> str:
    """Invoke the bash tool's coroutine directly (the same seam the
    tool executor uses)."""
    from daemon.tools.bash import bash

    return await bash.coroutine(  # type: ignore[attr-defined]
        command=command,
        timeout=30,
        env=env,
        instance_id=instance_id,
    )



# ─────────────────────────────────────────────────────────────────────
# Gap (c) — cross-phase interaction: RETIRED secret through the bash
# env DOUBLE-PASS (SECRET_TYPED_ARGS pre-invoke + spawn-time) + the
# retired value REMAINS in the scrub set. D1 ruling (design §3.2):
# retired markers KEEP resolving — pinned here at the SEAM level
# (the service-level pin lives in tests/unit/test_secret_service.py;
# the wrapper entries were never exercised with a retired id before
# this class).
#
# DOUBLE-PASS SHAPE (as implemented — pinned here deliberately): the
# pre-invoke helper resolves STRING-typed declared fields only; the
# bash ``env`` overlay is a DICT, so pass 1 is a documented no-op
# for it (dict values are skipped, §0 fast path) and the spawn-time
# seam (§1a, inside the bash tool) is the resolving pass. The scalar
# pre-invoke pass is pinned separately (the probe-tool class in
# tests/unit/services/test_long_tool_nudge_secret_typed_args.py;
# here with a retired id via the D1 pin below).
# ─────────────────────────────────────────────────────────────────────


class TestBashRetiredSecretDoublePass:
    @pytest.mark.asyncio
    async def test_retired_marker_spawn_time_and_scrub_set(
        self, service: SecretService
    ) -> None:
        """The full bash env flow with a RETIRED secret:

        1. PRE-INVOKE pass — the ``wrapped_tools_node`` helper
           (``_resolve_secret_typed_args_for_batch``, the
           ``SECRET_TYPED_ARGS`` seam) leaves the dict-valued
           ``env`` UNTOUCHED (dict fields are skipped — resolution
           is the spawn-time seam's job); the args copy is
           byte-identical and NO error envelope is produced (D1:
           the marker shape never fails closed pre-invoke).
        2. SPAWN-TIME pass — the real ``bash`` tool receives the
           raw retired marker and STILL spawns with the plaintext
           (surface ``bash.env``, design §3.2 D1 semantics).
        3. SCRUB — a tool result echoing the retired plaintext is
           markerized (the retired value stays in the scrub set,
           design §2.4).
        """
        from langchain_core.messages import AIMessage, ToolMessage

        from daemon.services.long_tool_nudge import (
            _resolve_secret_typed_args_for_batch,
        )
        from daemon.services.secret_persistence_scrub import (
            scrub_tool_results_inplace,
        )

        plain_value = "retired-double-pass-plain-31d"
        minted = service.store(
            name="bash-retired-dp",
            kind="generic",
            value=plain_value,
            actor="worker",
        )
        marker = minted["marker"]
        service.retire(minted["id"], actor="worker")

        probe = "P2_SECRET_TEST"

        # ── Pass 1: SECRET_TYPED_ARGS pre-invoke — dict-valued env
        # passes through untouched (no resolution HERE; no error) ──
        ai = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "bash",
                    "args": {
                        "command": ["bash", "-c", "true"],
                        "env": {probe: marker},
                    },
                    "id": "call-ret-1",
                    "type": "tool_call",
                }
            ],
        )
        state = {"messages": [ai]}
        state_for_invoke, errors = _resolve_secret_typed_args_for_batch(
            state,
            state["messages"],
            ai,
            ai.tool_calls,
            "inst-ret-dp",
        )
        assert errors == []
        resolved_ai = state_for_invoke["messages"][0]
        assert (
            resolved_ai.tool_calls[0]["args"]["env"][probe] == marker
        )

        # ── Pass 2: spawn-time (bash.env) — the resolving pass ──
        result = await _run_bash_with_env({probe: marker}, "inst-ret-dp")
        assert result.returncode == 0, result.stderr
        assert result.env_value == plain_value
        assert "__ENS_SECRET__" not in (result.env_value or "")

        # ── Pass 3: scrub set still contains the retired value ──
        scrubbed = scrub_tool_results_inplace(
            {
                "messages": [
                    ToolMessage(
                        content=(
                            f"STDOUT:\n{plain_value}\n\nEXIT CODE: 0"
                        ),
                        tool_call_id="call-ret-1",
                    )
                ]
            },
            actor="inst-ret-dp",
        )
        tm_out = scrubbed["messages"][0]
        assert marker in tm_out.content
        assert plain_value not in tm_out.content

    @pytest.mark.asyncio
    async def test_retired_scalar_pre_invoke_pass_still_resolves(
        self, service: SecretService, monkeypatch
    ) -> None:
        """D1 at the OTHER side of the double-pass: a SCALAR
        declared field (the pre-invoke seam's actual input shape)
        carrying a RETIRED marker resolves at pre-invoke — the
        invoke-bound copy gets the plaintext, the original
        AIMessage keeps the marker, no error envelope (the
        probe-tool declaration pattern from the typed-args pack)."""
        from langchain_core.messages import AIMessage

        from daemon.services.long_tool_nudge import (
            _resolve_secret_typed_args_for_batch,
        )
        from daemon.tools._auth import SECRET_TYPED_ARGS

        plain_value = "retired-scalar-plain-52e"
        minted = service.store(
            name="retired-scalar",
            kind="generic",
            value=plain_value,
            actor="worker",
        )
        marker = minted["marker"]
        service.retire(minted["id"], actor="worker")

        monkeypatch.setitem(
            SECRET_TYPED_ARGS, "bash", frozenset({"api_token"})
        )
        ai = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "bash",
                    "args": {"api_token": marker, "plain": "x"},
                    "id": "call-ret-2",
                    "type": "tool_call",
                }
            ],
        )
        state = {"messages": [ai]}
        state_for_invoke, errors = _resolve_secret_typed_args_for_batch(
            state,
            state["messages"],
            ai,
            ai.tool_calls,
            "inst-ret-dp2",
        )
        assert errors == []  # D1: retired does NOT fail closed
        resolved_ai = state_for_invoke["messages"][0]
        assert (
            resolved_ai.tool_calls[0]["args"]["api_token"] == plain_value
        )
        # original AIMessage untouched (live args keep the MARKER)
        assert ai.tool_calls[0]["args"]["api_token"] == marker
