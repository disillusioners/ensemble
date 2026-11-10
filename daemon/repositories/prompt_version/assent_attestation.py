"""The typed ``AssentAttestation`` — the assent-relay lane's authority artifact.

Distinctly named (``AssentAttestation``) so it does NOT collide with the
promote-lane attestation (the Leader Completion Attestation feature —
``tests/unit/tools/test_attestation_registration.py`` exercises that
other thing entirely).

Field set: architect §2.3 @ ``df8da01a6`` (the BINDING attestation field
set — NEVER ``decisions.md``, whose inline D-8 field set is pre-ruling).
The attestation is MINTED ONLY by the assent service (Phase 3), post-gate
— never from tool arguments (the anti-forgery analogue of the repo's
R3: an agent-presented attestation argument is a forgery attempt). The
repository entrypoints (``approve_assent`` / ``reject_assent``) require
a COMPLETE instance of this type; completeness is enforced here, at
construction — a missing/empty mandatory field raises immediately.

The type is the PHASE SEAM (plan §Coupling): Phase 1 defines it, Phase 3
produces it, the P2 tool gate consumes its token_fingerprint /
render_sha256 surfaces.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_FINGERPRINT_HEX = re.compile(r"^[0-9a-f]{12}$")


def _require_non_empty(value: Optional[str], what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"AssentAttestation: {what} is REQUIRED (fail-closed — an "
            f"incomplete attestation is not an attestation)"
        )
    return value


@dataclass(frozen=True)
class AssentItemBinding:
    """The pending item the token was minted for (§2.3 ``item_binding``).

    Denormalized item key — MUST match the ``prompt_versions`` row the
    relay decision transitions; ``version_id`` is the authoritative
    binding, the rest are audit-legibility copies.
    """

    version_id: str
    agent_id: str
    version_tag: str
    artifact: str
    version: int

    def __post_init__(self) -> None:
        _require_non_empty(self.version_id, "item_binding.version_id")
        _require_non_empty(self.agent_id, "item_binding.agent_id")
        _require_non_empty(self.artifact, "item_binding.artifact")
        if not isinstance(self.version, int) or isinstance(
            self.version, bool
        ):
            raise ValueError(
                "AssentAttestation: item_binding.version must be an int"
            )


@dataclass(frozen=True)
class AssentHumanIdentity:
    """The daemon-derived human identity (§2.3 ``human_identity``).

    Carried verbatim from the NESTED ``metadata["discord"]`` of the
    approving turn's message row (D-5 extraction contract — REQUIRED,
    fail-closed ``extraction-incomplete``). ``user_name`` is cosmetic
    and may be ``None``; ``guild_id`` is ``None`` in DMs. Every
    authority-bearing field is REQUIRED and non-empty.
    """

    source_id: str
    user_id: str
    channel_id: str
    channel_type: str
    is_dm: bool
    external_user_id: str
    user_name: Optional[str] = None
    guild_id: Optional[str] = None

    def __post_init__(self) -> None:
        _require_non_empty(self.source_id, "human_identity.source_id")
        _require_non_empty(self.user_id, "human_identity.user_id")
        _require_non_empty(self.channel_id, "human_identity.channel_id")
        _require_non_empty(
            self.channel_type, "human_identity.channel_type"
        )
        _require_non_empty(
            self.external_user_id, "human_identity.external_user_id"
        )
        if not isinstance(self.is_dm, bool):
            raise ValueError(
                "AssentAttestation: human_identity.is_dm must be a bool"
            )


@dataclass(frozen=True)
class AssentAttestation:
    """The typed attestation the assent-relay lane requires (architect §2.3).

    Attributes:
        item_binding: The pending item (see :class:`AssentItemBinding`).
        render_sha256: sha256 of the named canonical render (J-3/K-7) —
            ONE name everywhere: token-table column, mint response,
            attestation, audit payload.
        human_message_id: Daemon-minted message id of the approving turn.
        relay_source_message_id: PLATFORM-side (Discord) message id — the
            daemon queue id is ephemeral/wiped at boot; without the
            platform id the forensic chain breaks (ruling §2.3).
        human_identity: The daemon-derived human identity (D-5).
        approver_subject: ``f"{source_id}:{user_id}"`` — the HUMAN. Must
            equal the identity's composition (validated); the audit
            payload's ``approver_subject`` and the row column carry this,
            while ``actor_*`` carries the relay identity (§2.4 split).
        token_id: The consumed token's PK.
        token_fingerprint: ``sha256(token)[:12]`` — NEVER the raw token.
        window_stamped_at / window_expires_at: The factor-2 window
            stamps the gate evaluated.
        relay_agent: The relaying agent (``"ari"``).
        relay_instance_id: The relay instance (the actor subject).
        attested_at: When the attestation was minted (ISO-8601 UTC).
    """

    item_binding: AssentItemBinding
    render_sha256: str
    human_message_id: str
    relay_source_message_id: str
    human_identity: AssentHumanIdentity
    approver_subject: str
    token_id: str
    token_fingerprint: str
    window_stamped_at: str
    window_expires_at: str
    relay_agent: str
    relay_instance_id: str
    attested_at: str

    def __post_init__(self) -> None:
        if not isinstance(self.item_binding, AssentItemBinding):
            raise ValueError(
                "AssentAttestation: item_binding must be a typed "
                "AssentItemBinding"
            )
        if not isinstance(self.human_identity, AssentHumanIdentity):
            raise ValueError(
                "AssentAttestation: human_identity must be a typed "
                "AssentHumanIdentity"
            )
        if not isinstance(self.render_sha256, str) or not _SHA256_HEX.match(
            self.render_sha256
        ):
            raise ValueError(
                "AssentAttestation: render_sha256 must be lower-case "
                "sha256 hex (64 chars) of the canonical render (J-3/K-7)"
            )
        if not isinstance(self.token_fingerprint, str) or (
            not _FINGERPRINT_HEX.match(self.token_fingerprint)
        ):
            raise ValueError(
                "AssentAttestation: token_fingerprint must be "
                "sha256(token)[:12] (12 lower-case hex chars) — NEVER the "
                "raw token"
            )
        _require_non_empty(self.human_message_id, "human_message_id")
        _require_non_empty(
            self.relay_source_message_id, "relay_source_message_id"
        )
        _require_non_empty(self.token_id, "token_id")
        _require_non_empty(self.window_stamped_at, "window_stamped_at")
        _require_non_empty(self.window_expires_at, "window_expires_at")
        _require_non_empty(self.relay_agent, "relay_agent")
        _require_non_empty(self.relay_instance_id, "relay_instance_id")
        _require_non_empty(self.attested_at, "attested_at")
        subject = _require_non_empty(self.approver_subject, "approver_subject")
        expected = (
            f"{self.human_identity.source_id}:{self.human_identity.user_id}"
        )
        if subject != expected:
            raise ValueError(
                "AssentAttestation: approver_subject must be "
                "f\"{source_id}:{user_id}\" (D-5) — got "
                f"{subject!r}, expected {expected!r}"
            )
