"""The remote-command audit — who / machine / tool / args-hash (§8.4).

SPEC.md §8.4: "Every forwarded command should be logged by the center
with **who / machine / tool / args-hash**. Args are **hashed**, not stored
verbatim, by default: a typed password should not end up in a log file
because it was the argument to a tool."

The hash is SHA-256 over the CANONICALISED arguments (the JSON is decoded
and re-encoded with sorted keys, so two calls with the same arguments
hash the same whatever the key order), in lower-case hex. That is the
same shape the shipped client's audit uses (``internal/audit.HashArgs``,
recorded in §6.1 of the Phase-A interpretations).

WHY THE SINK IS AN INTERFACE
----------------------------

The repo's audit convention is DB TABLES (``secrets_audit`` behind the
``SecretService``), not JSONL — so the DB is the sink of this phase. The
sink is behind an interface anyway so that the unit tests can use a fake
and so that a file/JSONL sink can be added later without touching the
service.

WHY AN AUDIT FAILURE DOES NOT ABORT A COMMAND
----------------------------------------------

The secrets lineage writes its audit row INSIDE the same transaction as
the mutation. That is right for a LOCAL mutation: the row and the thing
it describes commit together or not at all.

This audit describes a REMOTE side effect — a command that was forwarded
to a machine on ANOTHER host. There is no transaction to join: the
command is already done (or already failed) by the time the row could be
written, and refusing to return the command's result because the AUDIT
write failed would turn an observability problem into an availability
problem on a path that drives a user's desktop. So the audit write is
BEST-EFFORT: it is logged loudly, counted, and the command proceeds. The
divergence from the secrets pattern is deliberate and is recorded here.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from .protocol import canonical_json

logger = logging.getLogger(__name__)

#: Audit outcomes.
OUTCOME_OK = "ok"
OUTCOME_ERROR = "error"
OUTCOME_DENIED = "denied"
OUTCOME_TIMEOUT = "timeout"


def hash_args(arguments: Any) -> str:
    """The SHA-256 of the canonicalised arguments, in lower-case hex.

    The RAW arguments never leave this function: a typed password must not
    end up in a log row because it was the argument to a tool.
    """
    canonical = canonical_json(arguments if arguments is not None else {})
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class AuditRecord:
    """One audited remote command.

    Attributes:
        agent_id: WHO asked (the agent identity).
        instance_id: The instance of that agent.
        machine_id: The machine the command ran on.
        tool: The name of the remote tool.
        args_hash: The SHA-256 of the canonicalised arguments — NEVER the
            arguments themselves.
        outcome: ``ok`` / ``error`` / ``denied`` / ``timeout``.
        session_id: The session the command ran in.
        error_code: The typed error code, when the command failed.
        detail: Advisory human text (never the raw arguments).
        created_at: The ISO-8601 stamp of the record.
    """

    agent_id: str
    machine_id: str
    tool: str
    args_hash: str
    outcome: str = OUTCOME_OK
    instance_id: str = ""
    session_id: str = ""
    error_code: str = ""
    detail: str = ""
    created_at: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class AuditSink(Protocol):
    """The audit sink interface."""

    def record(self, record: AuditRecord) -> None:
        """Persist one audit record.

        Raises on failure — the CALLER decides what a failure means (the
        service logs it, counts it, and does NOT abort the command).
        """


class NullAuditSink:
    """A sink that drops everything (audit disabled)."""

    def record(self, record: AuditRecord) -> None:  # noqa: D102 - trivial
        return None


class MemoryAuditSink:
    """An in-memory sink — the fake the unit tests use."""

    def __init__(self) -> None:
        self.records: list[AuditRecord] = []

    def record(self, record: AuditRecord) -> None:
        self.records.append(record)

    def clear(self) -> None:
        self.records.clear()


__all__ = [
    "AuditRecord",
    "AuditSink",
    "MemoryAuditSink",
    "NullAuditSink",
    "OUTCOME_DENIED",
    "OUTCOME_ERROR",
    "OUTCOME_OK",
    "OUTCOME_TIMEOUT",
    "hash_args",
]
