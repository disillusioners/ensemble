"""Shared fixtures for the prompt-assent Phase-1 test files.

Builds valid :class:`AssentAttestation` instances + assent-relay actors
over an in-memory SQLite engine, so each test file pins its OWN slice
without re-deriving the §2.3 field set four times.

NOTE: the attestation field set here mirrors the BINDING ruling
(``architecture-recommendation.md`` @ ``df8da01a6`` §2.3) — never
``decisions.md`` (pre-ruling field set, missing render_sha256 /
relay_source_message_id / approver_subject).
"""

from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.repositories.prompt_assent.models import PromptAssentToken
from daemon.repositories.prompt_assent.repository import (
    PromptAssentTokenRepository,
)
from daemon.repositories.prompt_version.assent_attestation import (
    AssentAttestation,
    AssentHumanIdentity,
    AssentItemBinding,
)
from daemon.repositories.prompt_version.repository import (
    ActorContext,
    PromptVersionRepository,
    actor_from_assent_relay,
    actor_from_instance,
)

AGENT_ID = "ari"
VERSION_TAG = ""
ARTIFACT = "rule"
PENDING_CONTENT = "do not run destructive commands without a human yes\n"
EFFECTIVE_CONTENT = "no guardrail yet\n"


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture()
def sqlite_engine():
    """Fresh in-memory SQLite with BOTH assent surfaces registered."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Importing the model modules registers the tables on the metadata
    # (same convention as the migration parity tests).
    from daemon.repositories.prompt_version.models import (  # noqa: F401
        PromptAuditEvent,
        PromptVersion,
    )

    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture()
def pv_repo(sqlite_engine):
    return PromptVersionRepository(sqlite_engine)


@pytest.fixture()
def token_repo(sqlite_engine):
    return PromptAssentTokenRepository(sqlite_engine)


@pytest.fixture()
def pending_row(pv_repo) -> object:
    """A CORE row in ``pending_approval`` (the assentable item)."""
    submitter = actor_from_instance(
        instance_id="inst-submitter-1", agent_id=AGENT_ID
    )
    row = pv_repo.create_draft(
        agent_id=AGENT_ID,
        version_tag=VERSION_TAG,
        artifact=ARTIFACT,
        content=PENDING_CONTENT,
        actor=submitter,
    )
    return pv_repo.submit_for_approval(row.version_id, submitter)


def build_attestation(
    *,
    version_id: str = "vid-1",
    token_id: str = "tok-1",
    raw_token: str = "CONFIRM-AAAAAAAA",
    render_sha256: str | None = None,
    relay_instance_id: str = "inst-relay-1",
) -> AssentAttestation:
    """A VALID §2.3 attestation (ruling field set — not decisions.md)."""
    token_hash = sha256_hex(raw_token)
    if render_sha256 is None:
        # A deterministic stand-in hash — tests that care about the
        # REAL canonical-render hash pass render_sha256 explicitly.
        render_sha256 = sha256_hex(f"render:{version_id}:{token_id}")
    return AssentAttestation(
        item_binding=AssentItemBinding(
            version_id=version_id,
            agent_id=AGENT_ID,
            version_tag=VERSION_TAG,
            artifact=ARTIFACT,
            version=3,
        ),
        render_sha256=render_sha256,
        human_message_id="msg-daemon-1",
        relay_source_message_id="discord-platform-msg-1",
        human_identity=AssentHumanIdentity(
            source_id="my-discord-bot",
            user_id="112233445566",
            channel_id="999888777",
            channel_type="dm",
            is_dm=True,
            external_user_id="dm:112233445566",
            user_name="The Emperor",
            guild_id=None,
        ),
        approver_subject="my-discord-bot:112233445566",
        token_id=token_id,
        token_fingerprint=token_hash[:12],
        window_stamped_at="2026-11-10T21:30:00Z",
        window_expires_at="2026-11-10T21:40:00Z",
        relay_agent="ari",
        relay_instance_id=relay_instance_id,
        attested_at="2026-11-10T21:31:00Z",
    )


def build_relay_actor(attestation: AssentAttestation) -> ActorContext:
    return actor_from_assent_relay(attestation)
