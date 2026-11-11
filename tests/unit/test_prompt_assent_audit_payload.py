"""Phase-1 audit-payload tests — the relay provenance + append-only pins.

Covers phase1-plan task 1.8 acceptance + risks.md Part 3 SP-2:

* a relayed decision writes ONE NEW event row carrying EVERY §2.4
  provenance key, with the event type still ``APPROVED``/``REJECTED``
  (D-9) and ``approval_path`` discriminating the lane;
* the FE payload shape is byte-identical to the base (a non-relay
  approve carries exactly the four keys it always did);
* NO existing event row is modified (append-only — the relay path
  only ever INSERTs);
* the raw token appears NOWHERE in the payload (``token_fingerprint``
  is ``sha256(token)[:12]``);
* ``reject_reason`` is TOP-LEVEL and ≤500 chars (DECIDED-1);
* SP-2 structural pin: nothing in the assent/prompt-version lane
  issues an UPDATE or DELETE against ``prompt_audit_events``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from sqlmodel import Session, col, select

from daemon.repositories.prompt_version.models import PromptAuditEvent
from daemon.repositories.prompt_version.repository import (
    ActorContext,
    AUTH_METHOD_SESSION,
    actor_from_instance,
)
from tests.helpers.prompt_assent_fixture import (  # noqa: F401 — fixtures
    build_attestation,
    build_relay_actor,
    pending_row,
    pv_repo,
    sha256_hex,
    sqlite_engine,
    token_repo,
)

_EXPECTED_RELAY_KEYS = {
    "from_state",
    "to_state",
    "approver_subject",
    "approver_auth_method",
    "approval_path",
    "relay_agent",
    "relay_instance_id",
    "relay_source_id",
    "relay_external_user_id",
    "relay_user_id",
    "relay_guild_id",
    "relay_channel_id",
    "relay_channel_type",
    "relay_message_id",
    "relay_source_message_id",
    "relay_window_stamped_at",
    "relay_window_expires_at",
    "assented_at",
    "token_id",
    "token_fingerprint",
    "render_sha256",
}


def _last_event(pv_repo, engine) -> PromptAuditEvent:
    with Session(engine) as session:
        rows = session.exec(
            select(PromptAuditEvent)
            .order_by(col(PromptAuditEvent.created_at))
        ).all()
        return rows[-1]


def _noop_hook(conn, version_id):  # payload-focused tests: no consume
    return None


def _approve_relayed(pv_repo, pending_row, token_repo, raw_token="CONFIRM-TEST0001"):  # noqa: F811
    att = build_attestation(
        version_id=pending_row.version_id,
        token_id="tok-audit-1",
        raw_token=raw_token,
    )
    relay = build_relay_actor(att)
    return pv_repo.approve_assent(
        pending_row.version_id, relay, att, _consume_hook=_noop_hook
    ), att, relay


class TestRelayAuditPayload:
    def test_relayed_approve_writes_one_event_with_full_provenance(
        self, pv_repo, pending_row, token_repo, sqlite_engine  # noqa: F811
    ):
        before = self._event_count(sqlite_engine)
        _approve_relayed(pv_repo, pending_row, token_repo)
        after = self._event_count(sqlite_engine)
        assert after == before + 1, "exactly ONE new event row"

        event = _last_event(pv_repo, sqlite_engine)
        assert event.event_type == "APPROVED"  # D-9 — types unchanged
        assert event.actor_auth_method == "assent-relay"
        payload = json.loads(
            event.payload
            if isinstance(event.payload, str)
            else json.dumps(event.payload)
        )
        assert _EXPECTED_RELAY_KEYS <= set(payload.keys())
        assert payload["approval_path"] == "chat-assent"
        assert payload["from_state"] == "pending_approval"
        assert payload["to_state"] == "approved"
        # §2.4 actor/authority split: approver_subject = the HUMAN.
        assert payload["approver_subject"] == "my-discord-bot:112233445566"
        assert payload["relay_agent"] == "ari"
        assert payload["relay_instance_id"] == "inst-relay-1"
        assert payload["relay_source_id"] == "my-discord-bot"
        assert payload["relay_user_id"] == "112233445566"
        assert payload["relay_channel_id"] == "999888777"
        assert payload["relay_channel_type"] == "dm"
        assert payload["relay_guild_id"] is None
        assert payload["relay_message_id"] == "msg-daemon-1"
        assert payload["relay_source_message_id"] == "discord-platform-msg-1"
        assert payload["token_fingerprint"] == sha256_hex("CONFIRM-TEST0001")[:12]
        assert payload["render_sha256"] == sha256_hex(
            "render:" + pending_row.version_id + ":tok-audit-1"
        )
        # The row columns carry the human + the relay lane distinctly
        # in the ledger (task 1.7 acceptance).
        assert event.actor_subject == "inst-relay-1"
        row = pv_repo.get_version(pending_row.version_id)
        assert row.approver_auth_method == "assent-relay"
        assert row.approver_subject == "my-discord-bot:112233445566"

    def test_raw_token_never_in_payload(
        self, pv_repo, pending_row, token_repo, sqlite_engine  # noqa: F811
    ):
        raw = "CONFIRM-SECRET99"
        _approve_relayed(pv_repo, pending_row, token_repo, raw_token=raw)
        event = _last_event(pv_repo, sqlite_engine)
        blob = json.dumps(event.payload)
        assert raw not in blob
        assert raw.replace("-", "") not in blob

    @staticmethod
    def _event_count(engine) -> int:
        with Session(engine) as session:
            return len(
                session.exec(select(col(PromptAuditEvent.event_id))).all()
            )

    def test_relayed_reject_carries_top_level_reason(
        self, pv_repo, pending_row, token_repo, sqlite_engine  # noqa: F811
    ):
        att = build_attestation(version_id=pending_row.version_id)
        relay = build_relay_actor(att)
        pv_repo.reject_assent(
            pending_row.version_id, relay, att, "not this wording",
            _consume_hook=_noop_hook,
        )
        event = _last_event(pv_repo, sqlite_engine)
        assert event.event_type == "REJECTED"  # D-9
        payload = event.payload
        assert payload["approval_path"] == "chat-assent"
        assert payload["reject_reason"] == "not this wording"  # TOP-LEVEL
        row = pv_repo.get_version(pending_row.version_id)
        assert row.state == "rejected"

    def test_fe_payload_shape_byte_identical(
        self, pv_repo, sqlite_engine  # noqa: F811
    ):
        """A plain-session approve carries EXACTLY the four base keys."""
        from sqlmodel import Session as _S

        submitter = actor_from_instance("inst-1", "ari")
        row = pv_repo.create_draft(
            agent_id="dev",
            version_tag="",
            artifact="workflow",
            content="wf body\n",
            actor=submitter,
        )
        row = pv_repo.submit_for_approval(row.version_id, submitter)
        human = ActorContext(subject="emperor", auth_method=AUTH_METHOD_SESSION)
        pv_repo.approve(row.version_id, human)
        with _S(sqlite_engine) as session:
            event = session.exec(
                select(PromptAuditEvent).where(
                    col(PromptAuditEvent.version_id) == row.version_id,
                    col(PromptAuditEvent.event_type) == "APPROVED",
                )
            ).first()
        payload = event.payload
        assert set(payload.keys()) == {
            "from_state",
            "to_state",
            "approver_subject",
            "approver_auth_method",
        }, "the FE payload shape gained keys — NOT byte-identical"
        assert payload["approver_subject"] == "emperor"
        assert payload["approver_auth_method"] == "session"

    def test_no_existing_event_row_modified(
        self, pv_repo, pending_row, token_repo, sqlite_engine  # noqa: F811
    ):
        # A SUBMITTED event exists (from submit_for_approval).
        with Session(sqlite_engine) as session:
            submitted = session.exec(
                select(PromptAuditEvent).where(
                    col(PromptAuditEvent.event_type) == "SUBMITTED"
                )
            ).first()
        snapshot = (submitted.event_id, json.dumps(submitted.payload, sort_keys=True))
        _approve_relayed(pv_repo, pending_row, token_repo)
        with Session(sqlite_engine) as session:
            again = session.get(PromptAuditEvent, snapshot[0])
        assert (again.event_id, json.dumps(again.payload, sort_keys=True)) == snapshot, (
            "an existing event row was modified — append-only violated"
        )

    def test_sp2_no_update_delete_against_audit_events(self):
        """SP-2 structural pin: the lane never UPDATEs/DELETEs audit rows.

        NON-VACUOUS: the scan covers the assent lane's modules AND the
        prompt-version repository (the write path); the regex targets
        real statement shapes, and the counter-mutation (appending a
        synthetic ``UPDATE prompt_audit_events`` line to a scan target)
        reddens it (proven in the Phase-1 mutation run).
        """
        targets = [
            Path("daemon/repositories/prompt_assent/repository.py"),
            Path("daemon/repositories/prompt_assent/consume_hook.py"),
            Path("daemon/repositories/prompt_assent/models.py"),
            Path("daemon/repositories/prompt_version/repository.py"),
            Path("daemon/services/prompt_version_diff.py"),
        ]
        pattern = re.compile(
            r"(update|delete)\s*\(\s*\)?[^)]*prompt_audit_events|"
            r"prompt_audit_events[^;]{0,80}(update|delete)",
            re.IGNORECASE,
        )
        for path in targets:
            src = path.read_text(encoding="utf-8")
            # The legit INSERT path writes THROUGH PromptAuditEvent
            # ORM inserts; forbid update/delete statement shapes.
            bad = [
                line
                for line in src.splitlines()
                if "prompt_audit_events" in line.lower()
                and re.search(
                    r"\.(update|delete)\s*\(|UPDATE\s+prompt_audit_events"
                    r"|DELETE\s+FROM\s+prompt_audit_events",
                    line,
                    re.IGNORECASE,
                )
            ]
            assert not bad, f"SP-2 violation in {path}: {bad}"
