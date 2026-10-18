"""agent-secrets P2 phase 4 — E2E exit-criterion integration tests
(design §6 phase-5 row; plan §8 Phase-2 exit criterion).

The commissioned end-to-end flow, exercised against REAL PG and
REAL tool seams:

    mint (``secret_store`` tool, the P1 surface) →
    ``bash`` tool call with ``env={"VAR": "__ENS_SECRET__<id>__"}`` →
    child process sees PLAINTEXT (echo assertion) →
    transcript/checkpoint shows the MARKER not plaintext
    (S1 results-side + S2 saver-proxy both-sides scrub) →
    ``use_marker`` audit row with ``context='bash_env'`` →
    persistence roundtrip (S2-scrubbed snapshot reloads clean) →
    restart-durability leg (fresh ``SecretService`` instance → same
    marker still resolves — LANE-2 re-read semantics, design §3.1).

Plus the commission coverage gaps:

* (a) **v0.21.0-minted-secret compatibility** — mint via the EXACT
  P1 mint paths (``store`` / ``generate_api_token``, mint logic
  unchanged since the P1 merge ``e4e4f304``) and resolve through
  EVERY seam's resolver entry (``tool_param`` / ``bash.env`` /
  ``mcp_spawn``) — proves the marker-format freeze end-to-end
  (design §3.1: "markers minted in v0.21.0 MUST keep resolving in
  P2 — re-mint is not an option").
* (c, seam leg) **retired secret resolves at the resolver ENTRIES**
  (D1, design §3.2) + the retired value REMAINS in the scrub set
  (§2.4) — complements the service-level pins (the wrapper entries
  were never exercised with a retired id before this file).

Test execution is fenced per the project critical notes:

* PG scratch fence: ``PG_TEST_*`` env-driven (P1 sibling
  convention — ambient ``POSTGRES_*`` never consulted), a
  DEDICATED scratch database per test (created + dropped per
  test — never ``ensemble_prod``; hard-abort set guards the
  resolved maintenance DB);
* run only the named file (``--tb=short -q``) with
  ``--override-ini="addopts=" -m integration``;
* mktemp-style scratch paths only — no live-shaped HOME paths.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from types import SimpleNamespace
from typing import Iterator

import pytest
from cryptography.fernet import Fernet
from langchain_core.messages import AIMessage, ToolMessage
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel

# Register every SQLModel table BEFORE create_all runs.
from daemon.repositories.secrets.models import (  # noqa: F401
    Secret,
    SecretAuditEvent,
)
from daemon.services.secret_persistence_scrub import (
    get_process_scrubber,
    register_secret_service,
    reset_process_registry,
    scrub_tool_results_inplace,
)
from daemon.services.secret_resolver import (
    resolve_marker,
    resolve_secret_markers,
)
from daemon.services.secret_service import (
    ENS_SECRET_MARKER_RE_COMPILED,
    SecretCipher,
    SecretService,
    mint_marker,
    parse_marker,
)


# ─────────────────────────────────────────────────────────────────────
# PG scratch-DB convention — PG_TEST_* env-driven, byte-mirroring
# tests/integration/test_secrets_v1_basic.py:80-97 (the P1 sibling).
# Defaults match the dev role; the secrets_pg_integration pack
# contract (PACKS.md) provisions its OWN scratch role/db and points
# the file at it via PG_TEST_USER / PG_TEST_PASSWORD — no
# host-specific role is baked into the file. Ambient POSTGRES_* is
# never consulted (the fixture's config namespace is PG_TEST_* only).
# ─────────────────────────────────────────────────────────────────────

_PG_HOST = os.environ.get("PG_TEST_HOST", "127.0.0.1")
_PG_PORT = int(os.environ.get("PG_TEST_PORT", "5432"))
_PG_USER = os.environ.get("PG_TEST_USER", "ensemble")
_PG_PASSWORD = os.environ.get("PG_TEST_PASSWORD", "ensemble_dev")
_PG_MAINT_DB = os.environ.get("PG_TEST_MAINT_DB", "ensemble_dev")
_PG_SCRATCH_PREFIX = "ensemble_secrets_p2_e2e_"

# Hard-abort sets — a resolved target in either set means the
# fixture is pointed at a shared database; fail the whole session
# instead of writing test rows into it (env-poison 3-guard
# convention). The MAINTENANCE connection only ever CREATEs/DROPs
# dedicated scratch DBs, so it is checked against the live set
# (P1 sibling semantics — ``ensemble_dev`` maintenance is the
# normal path); every SCRATCH name is checked against the broad
# shared-database set.
_PG_MAINT_FORBIDDEN_DBS = frozenset({"ensemble_prod"})
_PG_FORBIDDEN_DBS = frozenset(
    {"ensemble_prod", "ensemble_dev", "ensemble", "ensemble_test"}
)


def _pg_admin_engine() -> tuple[Engine | None, str | None]:
    """Maintenance engine (AUTOCOMMIT) for CREATE/DROP DATABASE.

    Probes connectivity first and distinguishes auth failures from
    "PG is down" so the skip reason is diagnostic instead of
    misleading (P1 sibling pattern). Returns ``(None, reason)``
    when PG is unreachable — the caller skips loudly. Hard-aborts
    when the resolved maintenance DB is in the forbidden set.
    """
    if _PG_MAINT_DB in _PG_MAINT_FORBIDDEN_DBS:
        pytest.exit(
            f"HARD ABORT: PG scratch fixture resolved maintenance "
            f"DB {_PG_MAINT_DB!r} — refuse to point secrets E2E "
            f"tests at a shared database (env-poison 3-guard "
            f"convention).",
            returncode=1,
        )
    from sqlalchemy.exc import DBAPIError, OperationalError

    engine = create_engine(
        f"postgresql+psycopg://{_PG_USER}:{_PG_PASSWORD}"
        f"@{_PG_HOST}:{_PG_PORT}/{_PG_MAINT_DB}",
        isolation_level="AUTOCOMMIT",
        pool_pre_ping=True,
    )
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError as exc:
        engine.dispose()
        orig = getattr(exc, "orig", exc)
        sqlstate = getattr(getattr(orig, "diag", None), "sqlstate", None)
        kind = (
            "auth failure (check PG_TEST_USER / PG_TEST_PASSWORD)"
            if sqlstate in {"28000", "28P01"}
            else "unreachable"
        )
        return None, (
            f"PostgreSQL {kind} at {_PG_HOST}:{_PG_PORT}/"
            f"{_PG_MAINT_DB}: {exc}"
        )
    except DBAPIError as exc:
        engine.dispose()
        return None, (
            f"PostgreSQL DBAPI error at {_PG_HOST}:{_PG_PORT}: {exc}"
        )
    return engine, None


def _postgres_scratch_engine() -> Iterator[tuple[Engine, str]]:
    """Yield (engine, url) for a DEDICATED scratch DB.

    Lifecycle: probe maintenance connection (loud skip when PG is
    unreachable — CI portability, P1 sibling pattern) → CREATE
    DATABASE ``ensemble_secrets_p2_e2e_<8hex>`` →
    ``SQLModel.metadata.create_all`` (the PG truth path) →
    yield → dispose → terminate stray backends → DROP DATABASE.
    """
    admin, skip_reason = _pg_admin_engine()
    if admin is None:
        pytest.skip(skip_reason)

    scratch_db = f"{_PG_SCRATCH_PREFIX}{uuid.uuid4().hex[:8]}"
    assert scratch_db not in _PG_FORBIDDEN_DBS

    engine: Engine | None = None
    scratch_url = (
        f"postgresql+psycopg://{_PG_USER}:{_PG_PASSWORD}"
        f"@{_PG_HOST}:{_PG_PORT}/{scratch_db}"
    )
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{scratch_db}"'))
        engine = create_engine(scratch_url, pool_pre_ping=True)
        SQLModel.metadata.create_all(engine)
        yield engine, scratch_url
    finally:
        if engine is not None:
            engine.dispose()
        try:
            with admin.connect() as conn:
                conn.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM "
                        "pg_stat_activity WHERE datname = :d "
                        "AND pid <> pg_backend_pid()"
                    ),
                    {"d": scratch_db},
                )
                conn.execute(
                    text(f'DROP DATABASE IF EXISTS "{scratch_db}"')
                )
        finally:
            admin.dispose()


@pytest.fixture
def pg_bundle() -> Iterator[tuple[Engine, str]]:
    """Yield (engine, scratch_url) for a dedicated scratch DB."""
    yield from _postgres_scratch_engine()


@pytest.fixture
def fernet_key() -> str:
    return Fernet.generate_key().decode()


@pytest.fixture
def service(pg_bundle, fernet_key: str) -> Iterator[SecretService]:
    """Yield a SecretService backed by the scratch PG engine and
    wire the process-wide scrub registry (mirrors manager wiring).
    """
    engine, _url = pg_bundle
    svc = SecretService(
        engine=engine,
        cipher=SecretCipher(encryption_key=fernet_key),
    )
    register_secret_service(svc)
    try:
        yield svc
    finally:
        reset_process_registry()


@pytest.fixture
def manager_stub(service: SecretService) -> SimpleNamespace:
    """Manager stand-in the real ``secret_store`` tool dereferences.

    ``_instance_repository`` is left ``None`` so
    ``_resolve_actor`` falls back to the instance id — the same
    degenerate-embedding fallback the tool boundary documents.
    """
    return SimpleNamespace(_secret_service=service, _instance_repository=None)


# ─────────────────────────────────────────────────────────────────────
# Real-tool invocation helpers
# ─────────────────────────────────────────────────────────────────────


def _secret_store_tool(manager: SimpleNamespace, instance_id: str):
    """Return the REAL ``secret_store`` tool bound to the stub
    manager (the P1 tool surface — ``create_secret_tools``)."""
    from daemon.tools.secrets_tools import create_secret_tools

    for t in create_secret_tools(manager, instance_id):
        if t.name == "secret_store":
            return t
    raise AssertionError("secret_store tool not found in factory output")


def _call_secret_store(
    manager: SimpleNamespace, instance_id: str, name: str, value: str
) -> dict:
    """Invoke the real ``secret_store`` tool (a SYNC tool body —
    ``.invoke`` is the seam the tool executor uses); return the
    parsed JSON response (``{id, name, marker, ...}``)."""
    tool = _secret_store_tool(manager, instance_id)
    raw = tool.invoke({"name": name, "kind": "generic", "value": value})
    return json.loads(raw)


async def _bash_echo_env(env: dict, instance_id: str, probe: str) -> str:
    """Invoke the REAL ``bash`` tool with a marker env overlay; the
    child prints ONE probed env var. Returns the raw tool-result
    text (``STDOUT:...\n\nEXIT CODE: N``) — i.e. the exact string
    that becomes the ToolMessage content (the pre-S1 transcript)."""
    from daemon.tools.bash import bash

    cmd = f'printf "%s" "${{{probe}:-<unset>}}"'
    raw = await bash.coroutine(
        command=["bash", "-c", cmd],
        timeout=30,
        env=env,
        instance_id=instance_id,
    )
    return raw


def _stdout_of(raw_tool_result: str) -> str:
    """Extract the child stdout from the bash tool-result text."""
    if not raw_tool_result.startswith("STDOUT:"):
        raise AssertionError(
            "expected STDOUT-shaped tool result, got: "
            f"{raw_tool_result[:200]!r}"
        )
    body = raw_tool_result.split("\n", 1)[1]
    return body.split("\n\nEXIT CODE:")[0]


def _audit_rows(engine: Engine, where: str, params: dict) -> list:
    """Query ``secrets_audit`` on the scratch DB (the read path the
    operator-facing audit queries use)."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT action, actor, secret_id, context FROM "
                "secrets_audit WHERE " + where + " ORDER BY at, id"
            ),
            params,
        ).fetchall()
    return [tuple(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────
# The commissioned exit-criterion flow (design §6 phase-5 row)
# ─────────────────────────────────────────────────────────────────────


def _persist_through_real_saver_proxy(
    plain_value: str, marker: str, probe: str
) -> tuple[AIMessage, ToolMessage, dict]:
    """Push a representative post-turn transcript through the REAL
    saver proxy (the seam installed at ``graph.compile(checkpoint=...)``)
    and return (live_ai, live_tm, persisted_checkpoint)."""
    from daemon.checkpoint_adapter import _wrap_saver_with_connection_retry

    recorded: list[dict] = []

    class _RecordingSaver:
        async def aput(self, config, checkpoint, metadata, new_versions):
            recorded.append(checkpoint)
            return {"configurable": {"checkpoint_id": checkpoint["id"]}}

    live_ai = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "bash",
                "args": {
                    "command": "printf %s",
                    "env": {probe: marker},
                },
                "id": "call-e2e-1",
                "type": "tool_call",
            }
        ],
    )
    live_tm = ToolMessage(
        content=f"STDOUT:\n{plain_value}\n\nEXIT CODE: 0",
        tool_call_id="call-e2e-1",
    )
    checkpoint = {
        "v": 1,
        "id": "ckpt-e2e-1",
        "ts": "2026-10-18T00:00:00",
        "channel_values": {"messages": [live_ai, live_tm]},
        "channel_versions": {"messages": 1},
        "versions_seen": {},
        "pending_sends": [],
    }
    proxy = _wrap_saver_with_connection_retry(_RecordingSaver())
    asyncio.run(proxy.aput({}, checkpoint, {}, {}))
    assert len(recorded) == 1
    return live_ai, live_tm, recorded[0]


@pytest.mark.integration
class TestP2ExitCriterionEndToEnd:
    """Plan §8 Phase-2 exit criterion, end-to-end on real PG +
    real ``secret_store`` + real ``bash`` + both scrub sides.

    Flow legs (one test — the criterion is the CHAIN, not the
    individual links; each link is asserted in order):

      1. mint via the real ``secret_store`` tool (P1 surface);
      2. real ``bash`` call with ``env={"P2_E2E_TOKEN": marker}`` →
         child sees PLAINTEXT, never the marker;
      3. S1 results-side: the ToolMessage that joins live state
         carries the MARKER, not the echoed plaintext;
      4. ``use_marker`` audit row with ``context='bash_env'``;
      5. S2 persistence: the saver-proxy snapshot carries markers
         only — ``json.dumps`` of the whole persisted checkpoint
         contains NO plaintext; parse_marker round-trips;
      6. restart-durability: a FRESH ``SecretService`` on a fresh
         engine (fresh-process simulation) resolves the same
         marker — LANE-2 re-read, no checkpoint-carried state.
    """

    def test_mint_bash_child_plaintext_transcript_marker_audit_and_restart(
        self, pg_bundle, service, fernet_key, manager_stub
    ) -> None:
        plain_value = "e2e-plain-sk-live-9f27c"
        probe = "P2_E2E_TOKEN"
        instance_id = "inst-e2e-worker"
        engine, scratch_url = pg_bundle

        # ── Leg 1: mint via the REAL secret_store tool ──
        stored = _call_secret_store(
            manager_stub, instance_id, "e2e-github-token", plain_value
        )
        secret_id = stored["id"]
        marker = stored["marker"]
        assert marker == mint_marker(secret_id)
        assert ENS_SECRET_MARKER_RE_COMPILED.fullmatch(marker)
        assert plain_value not in json.dumps(stored)  # pubkey-only rule

        # ── Leg 2: real bash child sees PLAINTEXT, never the marker ──
        raw = asyncio.run(_bash_echo_env({probe: marker}, instance_id, probe))
        stdout = _stdout_of(raw)
        assert stdout == plain_value
        assert "__ENS_SECRET__" not in stdout

        # ── Leg 3: S1 — the ToolMessage joining live state carries
        # the MARKER (the raw result text IS the pre-scrub content:
        # the child legitimately echoed the plaintext it received —
        # the scrub replaces it BEFORE the result joins state). ──
        scrubbed = scrub_tool_results_inplace(
            {
                "messages": [
                    ToolMessage(content=raw, tool_call_id="call-e2e-1")
                ]
            },
            actor=instance_id,
        )
        tm_out = scrubbed["messages"][0]
        assert isinstance(tm_out, ToolMessage)
        assert marker in tm_out.content
        assert plain_value not in tm_out.content

        # ── Leg 4: use_marker audit row with context='bash_env' ──
        rows = _audit_rows(
            engine,
            "action = 'use_marker' AND secret_id = :sid "
            "AND context = 'bash_env'",
            {"sid": secret_id},
        )
        assert len(rows) >= 1
        assert all(r[0] == "use_marker" and r[3] == "bash_env" for r in rows)

        # ── Leg 5: S2 — persisted snapshot is marker-only ──
        live_ai, live_tm, persisted = _persist_through_real_saver_proxy(
            plain_value, marker, probe
        )
        persisted_json = json.dumps(persisted, default=str)
        assert plain_value not in persisted_json
        assert marker in persisted_json
        p_ai, p_tm = persisted["channel_values"]["messages"]
        assert p_ai.tool_calls[0]["args"]["env"][probe] == marker
        assert (
            parse_marker(p_ai.tool_calls[0]["args"]["env"][probe])
            == secret_id
        )
        assert marker in p_tm.content
        # live objects intact (the S2 contract — scrub the serialized
        # copy only)
        assert live_ai.tool_calls[0]["args"]["env"][probe] == marker
        assert plain_value in live_tm.content

        # ── Leg 6: restart-durability — fresh service, fresh engine,
        # same DB, same key → the marker STILL resolves (LANE-2
        # re-read; resolution never comes from checkpoint state). ──
        engine.dispose()
        fresh_engine = create_engine(scratch_url, pool_pre_ping=True)
        try:
            fresh_svc = SecretService(
                engine=fresh_engine,
                cipher=SecretCipher(encryption_key=fernet_key),
            )
            # Fresh process simulation: the process registry now
            # points at the new service (the fixture teardown resets
            # it again).
            reset_process_registry()
            register_secret_service(fresh_svc)
            assert get_process_scrubber() is not None
            resolved_env = resolve_secret_markers(
                {probe: marker},
                secret_service=fresh_svc,
                actor=instance_id,
                surface="bash.env",
            )
            assert resolved_env == {probe: plain_value}
            # And the single-value form (tool_param seam) too.
            assert (
                resolve_marker(
                    marker,
                    secret_service=fresh_svc,
                    actor=instance_id,
                    surface="tool_param",
                )
                == plain_value
            )
        finally:
            fresh_engine.dispose()
            reset_process_registry()


# ─────────────────────────────────────────────────────────────────────
# Gap (a) — v0.21.0-minted-secret compatibility (marker-format freeze)
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.integration
class TestV021MintedSecretCompatibility:
    """Mint via the EXACT P1 code paths (``store`` /
    ``generate_api_token`` — mint logic unchanged since the P1 merge
    ``e4e4f304``), then resolve through EVERY seam's resolver entry.

    Proves the design §3.1 freeze: markers minted in v0.21.0 keep
    resolving in P2 at every consumer — no re-mint, no format drift.
    """

    def test_store_minted_marker_resolves_at_every_seam(
        self, pg_bundle, service, manager_stub
    ) -> None:
        plain_value = "compat-store-plain-4a1"
        stored = _call_secret_store(
            manager_stub, "inst-compat", "compat-store", plain_value
        )
        marker = stored["marker"]
        # The frozen format (v0.21.0 contract).
        assert ENS_SECRET_MARKER_RE_COMPILED.fullmatch(marker)

        # Seam 1 — tool-param pre-invoke entry (design §1d).
        assert (
            resolve_marker(
                marker,
                secret_service=service,
                actor="inst-compat",
                surface="tool_param",
            )
            == plain_value
        )
        # Seam 2 — bash env overlay entry (design §1a).
        assert resolve_secret_markers(
            {"K": marker},
            secret_service=service,
            actor="inst-compat",
            surface="bash.env",
        ) == {"K": plain_value}
        # Seam 3 — MCP spawn entry (design §1c).
        assert resolve_secret_markers(
            {"K": marker},
            secret_service=service,
            actor="inst-compat",
            surface="mcp_spawn",
        ) == {"K": plain_value}

        # Every resolve stamped its own audit context.
        engine, _url = pg_bundle
        contexts = {
            r[3]
            for r in _audit_rows(
                engine,
                "action = 'use_marker' AND secret_id = :sid",
                {"sid": stored["id"]},
            )
        }
        assert {"tool_param", "bash_env", "mcp_spawn"} <= contexts

    def test_api_token_minted_marker_resolves_at_every_seam(
        self, service
    ) -> None:
        """``generate_api_token`` — the OTHER P1 mint path (the
        generated value never crosses the tool boundary; only its
        marker does). All three seam entries must agree byte-exact,
        and the token stays in the scrub set."""
        result = service.generate_api_token(
            name="compat-token", length=32, actor="inst-compat"
        )
        marker = result["marker"]
        assert ENS_SECRET_MARKER_RE_COMPILED.fullmatch(marker)
        secret_id = result["id"]
        via_tool_param = resolve_marker(
            marker,
            secret_service=service,
            actor="inst-compat",
            surface="tool_param",
        )
        via_bash = resolve_secret_markers(
            {"K": marker},
            secret_service=service,
            actor="inst-compat",
            surface="bash.env",
        )["K"]
        via_mcp = resolve_secret_markers(
            {"K": marker},
            secret_service=service,
            actor="inst-compat",
            surface="mcp_spawn",
        )["K"]
        assert via_tool_param == via_bash == via_mcp
        # token_urlsafe(32 bytes) → ≥32 chars of URL-safe material.
        assert len(via_tool_param) >= 32
        # The minted value round-trips the scrub set too (the token
        # never legitimately re-appears in a transcript).
        scrubbed, subs = get_process_scrubber().scrub_text(
            f"token was {via_tool_param} ok"
        )
        assert subs == [secret_id]
        assert scrubbed == f"token was {mint_marker(secret_id)} ok"


# ─────────────────────────────────────────────────────────────────────
# Gap (c) — seam-level D1 pin: RETIRED secret still resolves at the
# resolver ENTRIES (complements the service-level pin in
# tests/unit/test_secret_service.py, which never exercises the
# seam wrappers) + the retired value stays in the scrub set.
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.integration
class TestRetiredSecretSeamLevel:
    def test_retired_marker_resolves_at_all_seam_entries(self, service):
        plain_value = "retired-seam-plain-77b"
        stored = service.store(
            name="retired-seam",
            kind="generic",
            value=plain_value,
            actor="inst-ret",
        )
        service.retire(stored["id"], actor="inst-ret")
        marker = stored["marker"]

        # D1 (design §3.2): retired CONTINUES TO RESOLVE — at the
        # wrapper level too, not just the service method.
        assert (
            resolve_marker(
                marker,
                secret_service=service,
                actor="inst-ret",
                surface="tool_param",
            )
            == plain_value
        )
        assert resolve_secret_markers(
            {"K": marker},
            secret_service=service,
            actor="inst-ret",
            surface="bash.env",
        ) == {"K": plain_value}
        assert resolve_secret_markers(
            {"K": marker},
            secret_service=service,
            actor="inst-ret",
            surface="mcp_spawn",
        ) == {"K": plain_value}

        # ...and the retired value REMAINS in the scrub set (§2.4):
        # a transcript echo of the retired plaintext still gets
        # markerized.
        scrubbed, subs = get_process_scrubber().scrub_text(
            f"echo {plain_value}"
        )
        assert subs == [stored["id"]]
        assert scrubbed == f"echo {mint_marker(stored['id'])}"


# ─────────────────────────────────────────────────────────────────────
# Commission coverage: fail-closed typed envelopes at the REAL bash
# seam (design §3.2/§3.3) — missing id / malformed marker /
# store-unavailable. The contract under proof: the child is NEVER
# spawned (the tool result is the typed JSON envelope, never a
# STDOUT-shaped string — the spawn-happened shape), the envelope
# is anti-differential (message == the shared template, ``code`` is
# the only distinction), and NO secret material rides the payload
# (PB-F1). Complements the unit-level envelope pins
# (tests/unit/test_secret_resolver.py) by proving the REAL tool
# boundary formats and returns them.
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.integration
class TestFailClosedTypedEnvelopes:
    """``SecretMarkerResolutionError`` → typed JSON envelope at the
    real ``bash`` tool boundary; spawn aborted before
    ``create_subprocess_*`` (never partial env, design §1a)."""

    def _bash_env_result(self, env: dict, instance_id: str) -> str:
        """Invoke the REAL bash tool with a (broken) marker overlay;
        return the raw result string (the would-be ToolMessage)."""
        return asyncio.run(
            _bash_echo_env(env, instance_id, next(iter(env)))
        )

    def _assert_typed_envelope(
        self, raw: str, code: str
    ) -> None:
        """The raw tool result IS the typed envelope: JSON with
        ``{ok: false, code, surface: "bash.env"}``, the shared
        anti-differential message, and zero secret material."""
        assert not raw.startswith("STDOUT:"), (
            "child was spawned despite a failing marker — the "
            f"fail-closed contract broke; got: {raw[:200]!r}"
        )
        env_json = json.loads(raw)
        assert env_json["ok"] is False
        assert env_json["code"] == code
        assert env_json["surface"] == "bash.env"
        # Anti-differential template (§3.3): the code is the only
        # distinguishing field the agent sees.
        assert env_json["message"] == (
            f"secret resolution failed: {code}"
        )
        # PB-F1: no marker literal anywhere in the payload.
        assert "__ENS_SECRET__" not in raw

    def test_missing_id_envelope_child_not_spawned(
        self, pg_bundle, service, fernet_key
    ) -> None:
        """Well-formed marker pointing at a NONEXISTENT id →
        ``secret_not_found`` at the real bash seam + a rejection
        ``use_marker`` audit row with NULL ``secret_id``."""
        engine, _url = pg_bundle
        instance_id = "inst-env-missing"
        unknown = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
        marker = mint_marker(unknown)
        assert parse_marker(marker) == unknown

        raw = self._bash_env_result(
            {"P2_ENV_PROBE": marker}, instance_id
        )
        self._assert_typed_envelope(raw, "secret_not_found")

        # The rejection row (design §3.4): success AND rejection
        # stamp ``use_marker``; not-found rows carry NULL secret_id.
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT actor, secret_id, context FROM "
                    "secrets_audit WHERE action = 'use_marker' "
                    "AND secret_id IS NULL AND actor = :a "
                    "AND context = 'bash_env'"
                ),
                {"a": instance_id},
            ).fetchall()
        assert len(rows) >= 1, (
            "expected a bash_env rejection audit row for the "
            "not-found marker"
        )

    def test_malformed_marker_envelope_child_not_spawned(
        self, service
    ) -> None:
        """Marker-SHAPED but UUID-invalid value →
        ``secret_marker_malformed`` BEFORE any DB access (the
        resolver's fullmatch gate); the child is not spawned."""
        bad = "__ENS_SECRET__not-a-real-uuid__"
        assert not ENS_SECRET_MARKER_RE_COMPILED.fullmatch(bad)
        raw = self._bash_env_result(
            {"P2_ENV_PROBE": bad}, "inst-env-malformed"
        )
        self._assert_typed_envelope(raw, "secret_marker_malformed")

    def test_store_unavailable_envelope_child_not_spawned(
        self, service
    ) -> None:
        """Valid minted marker but NO registered service (the
        un-wired-process trust class — manager never constructed
        SecretService) → ``secret_resolver_store_unavailable``;
        never a literal-marker spawn, never silent passthrough."""
        stored = service.store(
            name="env-store-down",
            kind="generic",
            value="store-down-plain-c41",
            actor="inst-env-down",
        )
        marker = stored["marker"]
        # Un-wire the process registry (the fixture re-registers
        # nothing — teardown resets anyway; re-register here so
        # THIS test stays hermetic either way).
        reset_process_registry()
        try:
            raw = self._bash_env_result(
                {"P2_ENV_PROBE": marker}, "inst-env-down"
            )
        finally:
            register_secret_service(service)
        self._assert_typed_envelope(
            raw, "secret_resolver_store_unavailable"
        )


# ─────────────────────────────────────────────────────────────────────
# Commission coverage: D2 — ``secret_store`` marker-passthrough at the
# REAL tool boundary on real PG (design §8 D2 ruling). Complements the
# unit-level pin (tests/unit/test_secret_persistence_scrub.py::
# TestD2MarkerPassthrough) with the full P1 mint path: a pause/resume
# retry that re-invokes ``secret_store`` with the S2-scrubbed
# (markerized) value is an IDEMPOTENT SUCCESS, not a
# secret-of-a-marker mint.
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.integration
class TestD2StoreMarkerPassthroughEndToEnd:
    def test_retry_with_marker_returns_existing_idempotent(
        self, pg_bundle, service, manager_stub
    ) -> None:
        plain_value = "d2-e2e-plain-8b3"
        instance_id = "inst-d2-e2e"

        first = _call_secret_store(
            manager_stub, instance_id, "d2-e2e-token", plain_value
        )
        secret_id, marker = first["id"], first["marker"]

        # RETRY: the S2-scrubbed arg shape (marker as value).
        retry_tool = _secret_store_tool(manager_stub, instance_id)
        retry = json.loads(
            retry_tool.invoke(
                {"name": "d2-e2e-token", "kind": "generic", "value": marker}
            )
        )
        # Idempotent success: SAME {id, name}, no re-wrap, no new row.
        assert retry == {"id": secret_id, "name": "d2-e2e-token"}
        rows = service.list(actor=instance_id)
        assert len([r for r in rows if r["id"] == secret_id]) == 1

        # And the marker STILL resolves to the ORIGINAL plaintext
        # (D2 + D1 interplay: passthrough must not disturb resolution).
        assert (
            resolve_marker(
                marker,
                secret_service=service,
                actor=instance_id,
                surface="tool_param",
            )
            == plain_value
        )
