"""D3c — the ONE audit projection helper, pinned across BOTH sinks.

``audit_row_to_wire`` (``daemon/services/mcps_gateway/audit.py``) is the
single normalization point between the two audit read lanes and the FE
wire contract (``GatewayAuditEntry``). It is installed at the ROUTER
ONLY; these tests pin WHY that is safe:

* **byte-identical cross-lane projection** — ONE logical record pushed
  through ``MemoryAuditSink.list_recent()`` AND
  ``McpGatewayAuditRepository.list_recent()`` projects to the SAME wire
  dict. This is what pins the two REAL divergences of Rev-2 B1:

  - **(a) id-absence** — the memory dict has no ``id``; the repo
    ``model_dump()`` does. The wire never emits ``id`` (or ``detail``),
    so both lanes meet at the same shape.
  - **(b) empty-vs-synthetic stamp** — the memory sink exposes ``""``
    verbatim when the producer left ``created_at`` unset, while the DB
    WRITE path substitutes ``now_utc_iso()`` at write time
    (``repository.py`` — that substitution is CORRECT for production
    rows, whose column is NOT NULL). The unset-stamp DB row is
    therefore inserted via RAW SQL through the engine — going through
    ``record()`` would silently stamp it and prove nothing.

* **(c) the double-projection hazard** — the helper is IDEMPOTENT:
  feeding an already-projected wire dict back in is a no-op, so the
  single install point cannot accidentally re-project.

Harness: in-memory SQLite + ``StaticPool`` (the pattern of
``tests/unit/repositories/test_mcps_gateway_audit_repository.py``). The
table is dialect-neutral TEXT, so the projection behaves the same on
PostgreSQL.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

# Register the table models so create_all() picks them up.
import daemon.repositories.mcps_gateway.models  # noqa: F401

from daemon.repositories.mcps_gateway.repository import McpGatewayAuditRepository
from daemon.services.mcps_gateway.audit import (
    AUDIT_WIRE_KEYS,
    AuditRecord,
    MemoryAuditSink,
    audit_row_to_wire,
)

#: One logical record with a REAL stamp — the both-sinks byte-identical
#: case that also covers the healthy path.
_STAMP = "2026-10-22T07:02:00.000000+00:00"


def _logical_record(*, created_at: str = _STAMP) -> AuditRecord:
    """The ONE logical record both sinks receive."""
    return AuditRecord(
        agent_id="leader",
        machine_id="mac-studio-01",
        tool="computer_click",
        args_hash="ab" * 32,
        outcome="ok",
        instance_id="inst-1",
        session_id="sess-abc",
        error_code="",
        detail="advisory text that must never reach the wire",
        created_at=created_at,
    )


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


# ── the helper's own contract ──────────────────────────────────────────────


class TestAuditRowToWireContract:
    def test_the_output_key_set_is_exactly_the_wire_keys(self):
        wire = audit_row_to_wire(_logical_record())
        assert set(wire) == set(AUDIT_WIRE_KEYS)

    def test_the_key_set_holds_for_the_repo_row_shape_too(self, engine):
        repository = McpGatewayAuditRepository(engine)
        repository.record(_logical_record())
        repo_row = repository.list_recent(limit=1, offset=0)[0]
        assert set(audit_row_to_wire(repo_row)) == set(AUDIT_WIRE_KEYS)

    def test_never_emits_id_or_detail(self, engine):
        repository = McpGatewayAuditRepository(engine)
        repository.record(_logical_record())
        repo_row = repository.list_recent(limit=1, offset=0)[0]
        assert "id" in repo_row and "detail" in repo_row  # the superset
        wire = audit_row_to_wire(repo_row)
        assert "id" not in wire
        assert "detail" not in wire
        assert "advisory text" not in str(wire)

    def test_empty_stamp_and_ids_project_to_none(self):
        wire = audit_row_to_wire(_logical_record(created_at=""))
        assert wire["timestamp"] is None
        assert wire["error_code"] is None  # "" → None

    def test_text_none_projects_to_empty_string(self):
        row: dict[str, Any] = {
            "created_at": _STAMP,
            "agent_id": None,
            "instance_id": None,
            "machine_id": None,
            "tool": None,
            "outcome": None,
            "session_id": None,
            "error_code": None,
            "args_hash": None,
        }
        wire = audit_row_to_wire(row)
        assert wire["agent_id"] == ""
        assert wire["machine_id"] == ""
        assert wire["tool"] == ""
        assert wire["outcome"] == ""
        assert wire["args_hash"] == ""

    def test_accepts_created_at_or_timestamp(self):
        by_storage = audit_row_to_wire({"created_at": _STAMP})
        by_wire = audit_row_to_wire({"timestamp": _STAMP})
        assert by_storage["timestamp"] == _STAMP
        assert by_wire["timestamp"] == _STAMP

    def test_a_missing_stamp_is_never_synthesized(self):
        wire = audit_row_to_wire({"agent_id": "leader"})
        assert wire["timestamp"] is None

    def test_feeding_an_already_projected_dict_is_a_no_op(self):
        once = audit_row_to_wire(_logical_record())
        twice = audit_row_to_wire(once)
        assert twice == once


# ── the cross-lane byte-identical proof ────────────────────────────────────


class TestBothSinksProjectIdentically:
    def test_memory_sink_row_and_repo_row_project_to_the_same_wire_dict(
        self, engine
    ):
        """D3c's headline acceptance: ONE logical record through BOTH
        sinks ⇒ byte-identical wire dicts (with a real stamp)."""
        sink = MemoryAuditSink()
        sink.record(_logical_record())
        repository = McpGatewayAuditRepository(engine)
        repository.record(_logical_record())

        memory_row = sink.list_recent(limit=1, offset=0)[0]
        repo_row = repository.list_recent(limit=1, offset=0)[0]

        assert "id" not in memory_row  # divergence (a): memory lacks id
        assert "id" in repo_row  # …the repo superset carries it
        assert audit_row_to_wire(memory_row) == audit_row_to_wire(repo_row)

    def test_unset_stamp_projects_to_none_on_both_lanes(self, engine):
        """Divergence (b): the DB write path substitutes a stamp
        (``record()`` → ``now_utc_iso()``), so the unset-stamp row is
        inserted via RAW SQL — bypassing the substitution — to prove
        both lanes project the EMPTY stamp to wire ``None``
        identically. Through ``record()`` the unset case would silently
        get a stamp and prove nothing (watch-list #3)."""
        sink = MemoryAuditSink()
        sink.record(_logical_record(created_at=""))  # memory exposes ""
        repository = McpGatewayAuditRepository(engine)
        row = _logical_record(created_at="")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO mcp_gateway_audit "
                    "(id, created_at, agent_id, instance_id, machine_id, tool, "
                    "args_hash, outcome, session_id) "
                    "VALUES (:id, '', :agent_id, :instance_id, :machine_id, "
                    ":tool, :args_hash, :outcome, :session_id)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "agent_id": row.agent_id,
                    "instance_id": row.instance_id or None,
                    "machine_id": row.machine_id,
                    "tool": row.tool,
                    "args_hash": row.args_hash,
                    "outcome": row.outcome,
                    "session_id": row.session_id or None,
                },
            )

        memory_wire = audit_row_to_wire(sink.list_recent(limit=1, offset=0)[0])
        repo_wire = audit_row_to_wire(repository.list_recent(limit=1, offset=0)[0])

        assert memory_wire["timestamp"] is None  # "" → None
        assert repo_wire["timestamp"] is None  # '' (raw SQL) → None
        assert memory_wire == repo_wire


def test_wire_keys_exactly_match_the_gateway_audit_entry_fields() -> None:
    """The wire-contract cross-check (QA C15): ``AUDIT_WIRE_KEYS`` ≡ the FE model.

    The router materializes ``GatewayAuditEntry(**audit_row_to_wire(row))``
    — key drift between the helper's output contract and the pydantic
    model surfaces as an UNHANDLED 500 at that call site, not as a
    degraded body. The sets are pinned equal here so drift goes red in
    THIS test instead of in production.
    """
    from daemon.routers.mcps_gateway_schemas import GatewayAuditEntry

    assert set(AUDIT_WIRE_KEYS) == set(GatewayAuditEntry.model_fields), (
        "AUDIT_WIRE_KEYS and GatewayAuditEntry.model_fields have drifted — "
        "the router's GatewayAuditEntry(**...) would now raise a 500."
    )


def test_audit_row_to_wire_has_exactly_one_production_call_site() -> None:
    """§1.7's ONE-install-site claim, pinned as a source census (QA A1).

    ``audit_row_to_wire`` must be CALLED at exactly one production site —
    the wire router. A second call site (a sink, the write path) would
    double-project or silently drop/rename keys. The census counts
    call syntax only (``audit_row_to_wire(``) minus the ``def`` line, so
    docstring/comment mentions do not trip it) across ``daemon/``.
    """
    repo_root = Path(__file__).resolve().parents[2]
    hits: list[str] = []
    for path in sorted((repo_root / "daemon").rglob("*.py")):
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            if "audit_row_to_wire(" not in line:
                continue
            if line.lstrip().startswith("def "):
                continue  # the definition itself (audit.py)
            hits.append(f"{path.relative_to(repo_root)}:{lineno}")
    assert len(hits) == 1 and hits[0].startswith(
        "daemon/routers/mcps_gateway_read.py"
    ), (
        "expected exactly ONE production call site of audit_row_to_wire "
        f"(the wire router) — the §1.7 single-install-point contract. Got: {hits}"
    )
