"""``SecretService`` — Phase 1 of agent-secrets (plan f11965039).

Module size rationale (M7 tidier convention, 2026-10-17):
this module is ``concentrated by design``. It owns the
in-process SecretService + the thin port seam
(:class:`_SecretStorePort` Protocol + :class:`_SqlSecretStore`
backend) + the Fernet :class:`SecretCipher` helper + the
FROZEN ``mint_marker`` / ``parse_marker`` shape. Splitting
the cipher out (e.g. into ``_cipher.py``) would re-fragment
the ``SYSTEM_ENCRYPTION_KEY`` rotation-coupling seam (plan
§7.1 — the cipher is the explicit anchor for the rotation
coupling note). Splitting the port out would re-fragment the
M1 ``engine.begin()`` atomicity guarantee (``plan §1.3 +
governor pre-promote approval 2026-10-17`` keeps the four
``*_with_audit`` methods together with their caller side so
a future remote-store port (Vault / OpenBao / KMS) MUST
replicate the atomic audit emission — the contract is
documented at each method, but the surface is small enough
that the methods next to each other make the contract
memorable).

~1600 lines today is well past the soft 1-2k band the rest
of the daemon observes. The rationale is the audit-trail
benefit (the thin-port Promise is the plan's deliberate
choice), not a missing abstraction.

Candidate splits NOT taken:

  * :class:`SecretCipher` → ``daemon/services/_secret_cipher.py``
    (would split the rotation-coupling note from the
    cipher — defeats the purpose of the inline note);
  * the ``*_with_audit`` store methods →
    ``daemon/services/_sql_secret_store.py`` (would split
    the port surface from the M1 atomicity contract —
    the contract is the design point).

Phase 1 ships the durable Fernet-encrypted secret store + the
``secrets`` tool category (#7 in ``PRIVILEGED_TOOL_CATEGORIES``,
sextet → septet per the Q1/Q2/Q3 decisions 2026-10-17; ``cu``
joined 2026-10-16 making the count 6 before this entry). Phase 2
will add the marker resolver and the consumption seams (bash
``env`` overlay, MCP spawn, tool params, exact-match transcript
scrub).

This module exposes:

* :class:`SecretCipher` — a colocated Fernet helper that
  reads ``SYSTEM_ENCRYPTION_KEY`` at construction time
  (plan §7.2: same package as ``SecretService``, NOT a new
  cross-cutting util; today no shared encrypt helper exists —
  ``credentials.py:74`` and ``kms_lite.py:221`` each roll
  their own Fernet, and we leave them alone per the
  binding constraints).
* :class:`SecretService` — the in-process service that owns
  the cipher + a thin engine-bound store seam. The
  storage seam is a *port-shaped interface* (plan §1.3
  + §5.2): a thin in-process Port with a process-boundary-
  clean data contract — NOT a tier-2/3 adapter, NOT
  plugin ceremony. Today the only port implementation is
  the SQLAlchemy-backed one we ship here; the interface
  is the future door for an alternative store (extracted
  into a unit of its own by the future port-wrapping
  work, or replaced by a resource-only plugin snapshot —
  per the plugin direction, but explicitly OUT of P1
  scope per the binding constraints).
* :func:`mint_marker` — Q3 marker format
  ``__ENS_SECRET__<secret_id>__`` (FROZEN 2026-10-17).
  The shape is the only P1-side surface that touches the
  marker scheme: the marker is minted on the create /
  generate_* / store response, the regex is the canonical
  form for any future P2 resolver. P1 mints handles; P2
  resolves them.

Encryption coupling
-------------------

The Fernet key is ``SYSTEM_ENCRYPTION_KEY`` — the same env the
existing ``daemon/sources/credentials.py`` +
``daemon/services/kms_lite.py`` paths consume (user Q1 ruling,
2026-10-17). Rotation coupling: a future
``SYSTEM_ENCRYPTION_KEY`` rotation MUST re-encrypt
``secrets.wrapped_secret`` AND the ``db_connections`` Fernet
credentials, or both registries break. The coupling is
documented at the cipher seam below — see the ``SecretCipher``
constructor for the explicit rotation-coupling note.

Trust model
-----------

The trust ruling (plan §1.2) is unconditional:
"secret-given = trusted for autonomous use AND read by any
agent holding the secrets tool category." There is no per-secret
grant, no sensitivity flag, no ask-user pause. The only access
boundary is the per-agent ``tools.allow`` category gate. This
service implements that boundary at the tool-factory level (the
secrets tools are gated by the privileged-category strip —
PRIVILEGED_TOOL_CATEGORIES — not by any per-call check inside
this service).
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Protocol

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from sqlalchemy import text as sql_text
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
from sqlmodel import Session, SQLModel, and_, select

from daemon.repositories.secrets.models import (
    SECRET_AUDIT_ACTIONS,
    Secret,
    SecretAuditEvent,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Marker format (Q3 — FROZEN 2026-10-17)
# ─────────────────────────────────────────────────────────────────────
#
# Visual / semantic distinction from the KMS-Lite lanes is the
# binding constraint (plan §5.4 do-not-touch seams +
# §13 binding design constraint #1):
#
#   * KMS-Lite LANE-1: ``__KMS_REF__KMS_HANDLE_<uuid>__`` — transient,
#     drains on daemon restart (kms_lite.py:75).
#   * KMS-Lite LANE-2: ``__KMS_ENV__<VAR>__`` — restart-durable
#     env-ref bridge (kms_lite.py:85,116-122).
#   * Agent-secrets P1: ``__ENS_SECRET__<secret_id>__`` — durable
#     secret handle (this module).
#
# The shape is the only P1-side surface that touches the marker
# scheme: the regex is the canonical form for any future P2
# resolver (the FROZEN constant). Keep this in lockstep with
# the audit log column on the SQLModel side — the model is a
# dumb store, the marker regex is the validator at mint time.

#: Marker namespace prefix (FROZEN — Q3 2026-10-17).
ENS_SECRET_MARKER_PREFIX: str = "__ENS_SECRET__"
#: Marker namespace suffix (FROZEN — Q3 2026-10-17). The
#: pair ``__ENS_SECRET__...__`` is the visual fence.
ENS_SECRET_MARKER_SUFFIX: str = "__"

#: Marker regex — fullmatch over a marker value. The UUID4
#: shape is enforced so a future P2 resolver can pass the
#: matched id straight to a SQLAlchemy ``WHERE id = :id`` query
#: without re-parsing. The regex is the canonical source of
#: truth for the marker shape; the ``mint_marker`` helper
#: generates strings that match it.
ENS_SECRET_MARKER_RE: str = (
    "^"
    + re.escape(ENS_SECRET_MARKER_PREFIX)
    + r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    + re.escape(ENS_SECRET_MARKER_SUFFIX)
    + "$"
)

#: Compiled regex (cached). Consumers re-compile on their side
#: (the kms_resolver pattern — ``daemon/services/kms_resolver.py``);
#: this is the canonical compiled form for callers that
#: want to do a ``.fullmatch(s)`` on a string.
ENS_SECRET_MARKER_RE_COMPILED: re.Pattern[str] = re.compile(ENS_SECRET_MARKER_RE)


def mint_marker(secret_id: str) -> str:
    """Mint a marker string for the given secret id.

    Args:
        secret_id: The UUID4 string. Must match the
            ``ENS_SECRET_MARKER_RE`` inner-group pattern; an
            invalid id is a programmer error and raises
            ``ValueError`` (NOT silently mangled).

    Returns:
        The marker string ``__ENS_SECRET__<secret_id>__``.

    Raises:
        ValueError: When ``secret_id`` does not match the
            UUID4 shape (FROZEN regex inner-group).
    """
    if not ENS_SECRET_MARKER_RE_COMPILED.fullmatch(
        ENS_SECRET_MARKER_PREFIX + secret_id + ENS_SECRET_MARKER_SUFFIX
    ):
        raise ValueError(
            f"secret_id must be a UUID4 string; got {secret_id!r}"
        )
    return ENS_SECRET_MARKER_PREFIX + secret_id + ENS_SECRET_MARKER_SUFFIX


def parse_marker(marker: str) -> Optional[str]:
    """Parse a marker string back to its inner secret_id.

    Convenience helper for the P2 marker resolver (out of P1
    scope, but the helper is colocated so the regex stays
    the single source of truth on both sides — mint and
    parse). Returns ``None`` if ``marker`` is not a
    well-formed marker.

    Args:
        marker: The marker string.

    Returns:
        The inner secret_id (UUID4 string) or ``None`` when
        ``marker`` is not a well-formed marker.
    """
    m = ENS_SECRET_MARKER_RE_COMPILED.fullmatch(marker)
    if m is None:
        return None
    return m.group(1)


# ─────────────────────────────────────────────────────────────────────
# Encryption helper (plan §7.2)
# ─────────────────────────────────────────────────────────────────────


class SecretCipher:
    """Fernet wrapper that reads ``SYSTEM_ENCRYPTION_KEY`` at
    construction time.

    Colocated under the same package as :class:`SecretService`
    per plan §7.2 (NOT a new cross-cutting util — today no
    shared encrypt helper exists; ``credentials.py:74`` and
    ``kms_lite.py:221`` each roll their own Fernet, and we
    leave them alone per the binding constraints).

    Rotation coupling (plan §7.1 + §10, user Q1 ruling,
    2026-10-17):

      The Fernet key is ``SYSTEM_ENCRYPTION_KEY`` — the same env
      the existing ``daemon/sources/credentials.py`` +
      ``daemon/services/kms_lite.py`` paths consume. Rotation
      coupling: a future ``SYSTEM_ENCRYPTION_KEY`` rotation
      MUST re-encrypt ``secrets.wrapped_secret`` AND the
      ``db_connections`` Fernet credentials, or BOTH
      registries break (linearly extends the existing
      rotation coupling — plan §10).

      Operationally: a rotation ceremony that walks the
      ``secrets`` table (decrypt all rows → re-encrypt under
      the new key → UPDATE) MUST happen in the same
      maintenance window as the ``db_connections`` walk. The
      ``secrets`` table is additive to the existing coupling
      set; the ceremony is the operator's responsibility
      (per the deferred-key-rotation note, rotation is
      deferred regardless of this coupling — the coupling is
      a paper risk today).

    Attributes:
        fernet: The constructed :class:`Fernet` instance, or
            ``None`` if the key was unavailable at construction
            time (a logged warning is emitted — the service
            then refuses any wrap/unwrap call rather than
            silently storing plaintext).
    """

    #: The env var name — same as
    #: :data:`daemon.sources.credentials.SYSTEM_ENCRYPTION_KEY_ENV`
    #: but redeclared here so the dependency is unidirectional
    #: (the secret service does NOT import the sources layer).
    KEY_ENV: str = "SYSTEM_ENCRYPTION_KEY"

    def __init__(self, encryption_key: bytes | str | None = None) -> None:
        self._fernet: Fernet | None = None
        key = encryption_key
        if key is None:
            key = os.environ.get(self.KEY_ENV)
        if not key:
            # Match the kms_lite pattern (kms_lite.py:131 + 215-221):
            # fail-CLOSED with a clear error rather than silently
            # storing plaintext. The service raises on any
            # wrap/unwrap call; the tool boundary surfaces the
            # error as a structured JSON envelope.
            logger.warning(
                "[SecretCipher] %s is not set — SecretService will "
                "refuse all wrap/unwrap calls. Set the env var to "
                "enable secret storage.",
                self.KEY_ENV,
            )
            return
        if isinstance(key, str):
            key = key.encode("utf-8")
        try:
            self._fernet = Fernet(key)
        except (ValueError, TypeError) as exc:
            # Fernet raises ValueError on a malformed key
            # (e.g. wrong length / invalid base64) and
            # TypeError on a non-bytes input. The bare
            # ``except Exception`` was overly broad — minor
            # ⑧ governor pre-promote fix (2026-10-17).
            # Other exception types should propagate so the
            # caller can surface the real diagnostic.
            logger.error(
                "[SecretCipher] invalid %s: %s — SecretService will "
                "refuse all wrap/unwrap calls.",
                self.KEY_ENV,
                exc,
            )
            self._fernet = None

    @property
    def is_available(self) -> bool:
        """Whether the cipher is operational (key was set + valid)."""
        return self._fernet is not None

    def wrap(self, plaintext: bytes) -> bytes:
        """Encrypt plaintext → Fernet ciphertext (bytes).

        Args:
            plaintext: The bytes to encrypt.

        Returns:
            The Fernet ciphertext (URL-safe base64 + HMAC + IV).

        Raises:
            SecretCipherUnavailable: When the cipher is
                unavailable (no key, or key was invalid at
                construction time).
        """
        if self._fernet is None:
            raise SecretCipherUnavailable(
                f"SecretCipher unavailable: {self.KEY_ENV} is not "
                f"set or is invalid"
            )
        return self._fernet.encrypt(plaintext)

    def unwrap(self, ciphertext: bytes) -> bytes:
        """Decrypt Fernet ciphertext → plaintext (bytes).

        Args:
            ciphertext: The Fernet ciphertext (bytes).

        Returns:
            The decrypted plaintext.

        Raises:
            SecretCipherUnavailable: When the cipher is
                unavailable.
            InvalidToken: When the ciphertext is malformed or
                was wrapped under a different key.
        """
        if self._fernet is None:
            raise SecretCipherUnavailable(
                f"SecretCipher unavailable: {self.KEY_ENV} is not "
                f"set or is invalid"
            )
        # Fernet returns bytes; the InvalidToken exception type
        # is re-exported by Fernet and caught at the service
        # boundary.
        return self._fernet.decrypt(ciphertext)


class SecretCipherUnavailable(RuntimeError):
    """Raised when :class:`SecretCipher` cannot encrypt or decrypt.

    Distinct from :class:`cryptography.fernet.InvalidToken`
    (which is the malformed-ciphertext case). The tool boundary
    catches both and returns a structured JSON envelope — the
    caller never sees a raw exception.
    """


# ─────────────────────────────────────────────────────────────────────
# Secret material dataclasses — the data contracts the tool
# boundary returns. PRIVATE material is NEVER in the contract
# (the v1 pubkey-only rule).
# ─────────────────────────────────────────────────────────────────────


@dataclass
class SecretPublicView:
    """The view the tool boundary returns to the agent.

    NEVER carries the wrapped_secret, the decrypted plaintext,
    or any private-side material. P1 has no value-read tool —
    the only material exposed via a read tool is the public
    material (SSH public key) for ``ssh_keypair`` rows.

    Attributes:
        id: UUID4 string.
        name: Human-readable label.
        kind: Enum tag.
        public_material: Public-side material when applicable
            (SSH public key); ``None`` for token / generic
            kinds.
    """

    id: str
    name: str
    kind: str
    public_material: Optional[str]

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable dict (P1 tool return shape)."""
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "public_material": self.public_material,
        }


@dataclass
class SecretListView:
    """The list view the tool boundary returns to the agent.

    Mirrors :class:`SecretPublicView` per-row but adds
    ``marker`` only for the P1 mint flow (the create / generate_*
    / store tool returns the marker; the list tool does NOT —
    plan §5.2). The list view's contract: id + name + kind +
    public_material; no marker, no wrapped_secret, no plaintext.

    Attributes:
        id: UUID4 string.
        name: Human-readable label.
        kind: Enum tag.
        public_material: Public-side material when applicable;
            ``None`` for token / generic kinds.
    """

    id: str
    name: str
    kind: str
    public_material: Optional[str]

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable dict (P1 tool return shape)."""
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "public_material": self.public_material,
        }


@dataclass
class SecretSshKeypairMaterial:
    """The crypto output of an ed25519 keypair generation.

    Returned by ``_generate_ssh_keypair``; the private side is
    serialized to OpenSSH PEM (no password) and Fernet-wrapped
    by the service before storage. The public side is the
    OpenSSH ``authorized_keys`` form.

    Attributes:
        public_openssh: ``ssh-ed25519 AAAA...`` line (the
            ``authorized_keys`` form), public-only.
        fingerprint: SHA256 fingerprint of the public key, in
            the ``SHA256:xxxx`` colon-delimited form
            (matches what ``ssh-keygen -lf`` prints).
        private_openssh_pem: OpenSSH private-key PEM
            (no password). Serialized via
            :class:`cryptography.hazmat.primitives.serialization`.
        raw_private_bytes: The 32-byte ed25519 seed (the
            raw key material, before OpenSSH encoding). Held
            only in-memory, never logged, never returned to
            the tool boundary.
    """

    public_openssh: str
    fingerprint: str
    private_openssh_pem: bytes
    raw_private_bytes: bytes


# ─────────────────────────────────────────────────────────────────────
# Secret material generators
# ─────────────────────────────────────────────────────────────────────
#
# Pure functions — no I/O, no DB access, no service state. The
# service composes the generator output with the cipher and the
# store; the test suite pins the crypto shape via these helpers.


def generate_ssh_keypair_material() -> SecretSshKeypairMaterial:
    """Generate an ed25519 keypair using the ``cryptography``
    primitives directly (no paramiko — plan §7.3: paramiko NOT
    declared; keygen needs nothing new beyond
    ``cryptography>=42.0.0``).

    The public-side output is the OpenSSH ``authorized_keys``
    form (the same shape ``ssh-keygen -lf`` parses). The
    private-side output is the OpenSSH PEM (no password) —
    the same shape ``ssh-add`` accepts.

    The fingerprint is SHA256 over the OpenSSH 51-byte wire
    blob (the ``ssh-keygen -lf`` parity — M3 governor pre-
    promote fix, 2026-10-17). The wire blob is the
    length-prefixed ``"ssh-ed25519"`` (4-byte big-endian
    length + 11 bytes ASCII) followed by the length-prefixed
    32-byte raw key (4-byte big-endian length + 32 bytes
    raw), total 51 bytes. This is the byte sequence
    ``ssh-keygen`` hashes; hashing the raw 32-byte key
    alone produced a non-matching fingerprint and broke
    the ``ssh-keygen -lf`` parity claim.

    Returns:
        The :class:`SecretSshKeypairMaterial` with all four
        fields populated. The private-side bytes are held in
        memory only; the service must wrap them via
        :class:`SecretCipher` and drop the in-memory copy
        ASAP (the dataclass is short-lived; the service
        does NOT keep a reference past the wrap call —
        see :func:`SecretService.generate_ssh_keypair`
        where ``del material, wrapped`` drops the
        references after the atomic insert commits).

    Memory hygiene (minor ⑨ — governor pre-promote fix,
    2026-10-17):

      * ``private_openssh_pem`` and ``raw_private_bytes``
        are plaintext bytes — no in-process cache, no
        persisted cache. They live only as fields on the
        :class:`SecretSshKeypairMaterial` dataclass, which
        is short-lived (single method scope).
      * The dataclass's only consumer is
        :meth:`SecretService.generate_ssh_keypair`, which
        calls ``cipher.wrap(material.private_openssh_pem)``
        inside the ``engine.begin()`` block. The wrap
        produces the Fernet ciphertext that is stored in
        ``secrets.wrapped_secret``.
      * The service explicitly ``del``s the references
        (``del material, wrapped``) after the atomic
        insert commits, dropping both the dataclass and
        the wrapped-form local variable. The plaintext
        bytes become unreachable from the service's
        scope; the cipher's Fernet-key wrapper is the
        sole remaining on-disk form (the row's
        ``wrapped_secret`` column).
      * The docstring does NOT promise any persistence
        layer's secure-zero — ``bytes`` objects in CPython
        are immutable and not reliably wiped from memory.
        The security claim is "in-memory bytes are dropped
        after wrap within the call", not "memory is
        securely erased".
    """
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    # Public side — OpenSSH ``authorized_keys`` form.
    public_openssh_bytes: bytes = public_key.public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    )
    public_openssh: str = public_openssh_bytes.decode("ascii").strip()

    # Fingerprint — SHA256 of the OpenSSH 51-byte wire blob
    # (M3 parity with ``ssh-keygen -lf``). The wire blob is
    # the binary form of the pubkey body — the ASCII art
    # encodes two length-prefixed strings:
    #     ``len("ssh-ed25519") || "ssh-ed25519" ||
    #       len(raw_key)      || raw_key``
    # which is 4+11+4+32 = 51 bytes for an ed25519 key.
    # ``cryptography`` exposes the public bytes in the
    # ``OpenSSH`` encoding as the whole
    # ``"ssh-ed25519 <b64> [comment]\n"`` line; we strip the
    # prefix / suffix and base64-decode the body to recover
    # the 51-byte wire blob (the
    # ``serialization.PublicFormat.OpenSSH`` form).
    import base64
    import hashlib
    import struct

    raw_public_bytes: bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    algo_name: bytes = b"ssh-ed25519"
    wire_blob: bytes = (
        struct.pack(">I", len(algo_name))
        + algo_name
        + struct.pack(">I", len(raw_public_bytes))
        + raw_public_bytes
    )
    assert len(wire_blob) == 51, (
        f"unexpected wire-blob length for ed25519: "
        f"{len(wire_blob)} (expected 51)"
    )
    digest = hashlib.sha256(wire_blob).digest()
    # SSH fingerprint format: ``SHA256:<base64-no-padding>``
    # (matches ``ssh-keygen -lf`` output verbatim).
    b64 = base64.b64encode(digest).decode("ascii").rstrip("=")
    fingerprint: str = f"SHA256:{b64}"

    # Private side — OpenSSH PEM (no password).
    private_pem: bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    )
    # Raw 32-byte seed — held only in-memory.
    raw_private_bytes: bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )

    return SecretSshKeypairMaterial(
        public_openssh=public_openssh,
        fingerprint=fingerprint,
        private_openssh_pem=private_pem,
        raw_private_bytes=raw_private_bytes,
    )


# Maximum token length in bytes (the upper bound on
    # ``secrets.token_urlsafe(nbytes)`` — minor ② governor
    # pre-promote fix, 2026-10-17). 256 bytes = 2048 bits
    # of entropy, well beyond any reasonable API token use
    # case; a higher cap risks resource exhaustion
    # (CWE-770). The cap is enforced at the service
    # boundary AND inside ``generate_api_token_material``
    # (defense-in-depth — the service is the only entry
    # point, but the helper is module-public for tests).
MAX_API_TOKEN_LENGTH_BYTES: int = 256

# Minimum token length in bytes (NF-1 governor pre-promote
    # fix, 2026-10-17, re-verification round). 4 bytes →
    # 6-char URL-safe string → safe last-4-char preview
    # (any smaller and ``secrets.token_urlsafe(n)`` returns
    # a string whose length is ≤ 4 chars, so the v1
    # last-4-char preview REVEALS the entire token). The
    # floor is enforced at the service boundary (request-
    # level + post-generation length-of-string guards) AND
    # inside ``generate_api_token_material`` so a direct
    # helper call cannot produce an unsafe-length token
    # even when the service-level request validation is
    # bypassed.
MIN_API_TOKEN_LENGTH_BYTES: int = 4


def generate_api_token_material(length: int = 32) -> bytes:
    """Generate a URL-safe token via ``secrets.token_urlsafe``.

    The default length is 32 bytes (256 bits) — the
    ``secrets.generate_api_token`` tool's default. The
    returned bytes are the raw token material (Fernet-wrapped
    by the service before storage; the tool response carries
    only the last-4-char preview).

    The length is capped at
    :data:`SecretService.MAX_API_TOKEN_LENGTH_BYTES`
    (256 bytes = 2048 bits). Lengths above the cap raise
    ``ValueError`` — minor ② governor pre-promote fix
    (2026-10-17, CWE-770 resource-exhaustion guard).

    The length is BOTTOM-clamped at
    :data:`MIN_API_TOKEN_LENGTH_BYTES` (4 bytes) — NF-1
    governor pre-promote fix (2026-10-17, M3-round-2
    re-verification): ``secrets.token_urlsafe(3)`` produces
    a 4-char URL-safe string, and the service-level
    ``token_preview`` is the last 4 chars. A length below 4
    would let the caller reconstruct the entire token from
    the preview, defeating the v1 pubkey-only contract. The
    helper guard mirrors the service-level ``<= 4``-char
    rejection so a direct helper call cannot produce an
    unsafe-length token even when the service-level request
    validation is bypassed (defense-in-depth — NF-1 ruling
    2026-10-17).

    Raising ``ValueError`` from the helper means the
    service-level ``SecretValidationError`` is bypassed when
    callers go through the helper directly — the test packs
    that do so MUST catch ``ValueError``.
    """
    if length < MIN_API_TOKEN_LENGTH_BYTES:
        raise ValueError(
            f"length must be at least {MIN_API_TOKEN_LENGTH_BYTES} "
            f"bytes (the v1 last-4-char preview floor); got {length}"
        )
    if length > MAX_API_TOKEN_LENGTH_BYTES:
        raise ValueError(
            f"length {length} exceeds the {MAX_API_TOKEN_LENGTH_BYTES}-byte cap; "
            f"see SecretService.MAX_API_TOKEN_LENGTH_BYTES"
        )
    return secrets.token_urlsafe(length).encode("ascii")


# ─────────────────────────────────────────────────────────────────────
# SecretService — the in-process service
# ─────────────────────────────────────────────────────────────────────


class _SecretStorePort(Protocol):
    """The thin port-shaped storage seam (plan §1.3 + §5.2).

    A process-boundary-clean data contract — NOT a tier-2/3
    adapter, NOT plugin ceremony. Today the only port
    implementation is the SQLAlchemy-backed one we ship via
    :class:`SecretService`; the interface is the future door
    for an alternative store.

    **Atomic-audit-in-port decision (plan §1.3; governor
    pre-promote approval, 2026-10-17):** the store is
    responsible for stamping the audit row alongside the
    mutation in ONE ``engine.begin()`` transaction. The
    earlier "thin-data-only" port was clean in principle
    but left a crash window between the mutation's commit
    and the audit row's commit (the M1 pre-promote
    regression). Re-adding the audit-stamping
    responsibility to the port — i.e. shipping the four
    LIVE ``*_with_audit`` methods as the entire port
    surface — closes the window by construction. A future
    remote-store port (Vault / OpenBao / KMS-backed) MUST
    replicate the atomic-audit emission; the contract is
    documented at each method below.

    The four LIVE calls (all sync; all returning
    ``Secret`` or ``None``):

    * :meth:`insert_with_audit` — INSERT the secret row
      AND the ``create`` audit row inside ONE
      ``engine.begin()`` transaction. Raises
      ``IntegrityError`` on UNIQUE(name) collision (caller
      translates to
      :class:`SecretNameConflictError`).
    * :meth:`mark_retired_with_audit` — UPDATE
      ``retired_at`` AND INSERT the ``retire`` audit row
      inside ONE ``engine.begin()`` transaction. Returns
      ``None`` for an unknown id (the v1 contract does NOT
      audit NOT-found reads; mirrors the read path).
    * :meth:`get_by_id_with_audit` — SELECT the row AND
      INSERT the ``read_public`` audit row inside ONE
      ``engine.begin()`` transaction (audit row only
      written for hits; the v1 contract audits successful
      reads only).
    * :meth:`list_active` — SELECT all non-retired rows
      (used by ``secrets.list``). Returns the public views
      only; does NOT audit (the list path is metadata-only
      and P1 does not emit a ``list`` audit row).
    """

    def insert_with_audit(
        self,
        secret: Secret,
        *,
        actor: str,
    ) -> Secret: ...
    def mark_retired_with_audit(
        self,
        id: str,
        at_iso: str,
        *,
        actor: str,
    ) -> Optional[Secret]: ...
    def get_by_id_with_audit(
        self,
        id: str,
        *,
        actor: str,
    ) -> Optional[Secret]: ...
    def list_active(self) -> list[Secret]: ...


class _SqlSecretStore:
    """SQLAlchemy-backed port implementation.

    Engine-only constructor — the store holds no state
    besides the engine. All methods are sync; callers
    bridge via ``asyncio.to_thread`` (the pattern from
    prompt_version/repository.py). Per-method short-lived
    connection — every public method opens its own
    ``engine.begin()`` block and commits; no instance
    state.

    M1 (governor pre-promote fix, 2026-10-17): the three
    mutation-path methods (``insert_with_audit``,
    ``mark_retired_with_audit``, ``get_by_id_with_audit``)
    pair the SQL mutation AND the audit row in ONE
    ``engine.begin()`` transaction. The earlier
    session-per-call layout left a crash window between
    the mutation's commit and the audit row's commit —
    a crash there committed the mutation without its
    audit row, contradicting the plan's promise of
    prompt_versions-style same-engine.begin() atomicity.
    The atomic variants mirror the
    ``daemon/repositories/prompt_version/repository.py``
    pattern (``atomic_transition`` + ``_append_audit_in_conn``).

    The store surface mirrors the
    :class:`_SecretStorePort` Protocol — see that
    docstring for the contract.

    Earlier non-atomic helpers (``insert``, ``get_by_id``,
    ``mark_retired``, ``append_audit``) were removed in
    the tidier pass — re-add at P2 need if a separate
    audit emission path becomes real. P1 only has the
    atomic ``*_with_audit`` family.
    """

    def __init__(self, engine: Any) -> None:
        self._engine = engine

    def list_active(self) -> list[Secret]:
        with Session(self._engine) as session:
            stmt = select(Secret).where(Secret.retired_at.is_(None))
            return list(session.exec(stmt).all())

    # ─────────────────────────────────────────────────────
    # M1 atomic variants — mutation + audit row inside ONE
    # ``engine.begin()`` transaction (mirrors the
    # ``prompt_version/repository.py`` ``atomic_transition``
    # + ``_append_audit_in_conn`` pattern).
    # ─────────────────────────────────────────────────────

    def insert_with_audit(
        self,
        secret: Secret,
        *,
        actor: str,
    ) -> Secret:
        """INSERT the secret row + the ``create`` audit row
        in one ``engine.begin()`` transaction.

        Raises ``sqlalchemy.exc.IntegrityError`` on
        UNIQUE(name) collision — the service translates
        this to :class:`SecretNameConflictError`. Any
        other failure rolls back BOTH writes so a crash
        between the mutation and the audit row cannot
        leave the registry in an inconsistent state
        (M1 — governor pre-promote fix 2026-10-17).

        Returns the persisted row read back inside the
        same ``engine.begin()`` transaction so the
        returned ORM object reflects the stored state.
        Using ``Session.get`` from a separate connection
        would race against an outer transaction that has
        not yet committed — the row read happens on the
        SAME ``conn`` so it sees the just-inserted values.
        """
        if "create" not in SECRET_AUDIT_ACTIONS:
            raise ValueError(
                "internal: 'create' missing from SECRET_AUDIT_ACTIONS"
            )
        with self._engine.begin() as conn:
            conn.execute(
                Secret.__table__.insert().values(
                    id=secret.id,
                    name=secret.name,
                    kind=secret.kind,
                    public_material=secret.public_material,
                    wrapped_secret=secret.wrapped_secret,
                    created_by=secret.created_by,
                    created_at=secret.created_at,
                    retired_at=secret.retired_at,
                )
            )
            _append_audit_in_conn(
                conn,
                action="create",
                secret_id=secret.id,
                actor=actor,
            )
            # Read back via the SAME connection so the
            # just-inserted row is visible. Returns the
            # values in the same shape the caller expects
            # (the existing ``insert()`` ORM-refresh contract).
            row = conn.execute(
                select(Secret).where(Secret.id == secret.id)
            ).first()
            if row is None:
                # Should be unreachable — we just inserted.
                raise RuntimeError(
                    "insert_with_audit: row not visible after "
                    "INSERT — internal invariant violated"
                )
            # Map Core row → ORM dataclass via the shared
            # ``_row_to_secret`` helper. ``wrapped_secret``
            # carries LargeBinary bytes, the rest are scalars.
            return _row_to_secret(row)

    def mark_retired_with_audit(
        self,
        id: str,
        at_iso: str,
        *,
        actor: str,
    ) -> Optional[Secret]:
        """UPDATE retired_at + INSERT the ``retire`` audit
        row in one ``engine.begin()`` transaction.

        Returns the updated row, or ``None`` if the id
        is unknown. The mutation AND the audit row are
        committed atomically — a crash between the two
        cannot leave the registry retired without its
        audit trail (M1 — governor pre-promote fix
        2026-10-17).

        Returns ``None`` for an unknown id WITHOUT
        inserting an audit row (mirrors the
        ``get_by_id_with_audit`` NOT-found contract —
        the v1 contract audits successful writes only).
        """
        if "retire" not in SECRET_AUDIT_ACTIONS:
            raise ValueError(
                "internal: 'retire' missing from SECRET_AUDIT_ACTIONS"
            )
        with self._engine.begin() as conn:
            existing = conn.execute(
                select(Secret).where(Secret.id == id)
            ).first()
            if existing is None:
                # NOT-found does NOT audit (the v1 contract —
                # the service raises ``SecretNotFoundError``).
                return None
            conn.execute(
                Secret.__table__.update()
                .where(Secret.id == id)
                .values(retired_at=at_iso)
            )
            _append_audit_in_conn(
                conn,
                action="retire",
                secret_id=id,
                actor=actor,
            )
            # Read back inside the same transaction so
            # the returned ORM row reflects the just-set
            # retired_at.
            row = conn.execute(
                select(Secret).where(Secret.id == id)
            ).first()
            assert row is not None
            return _row_to_secret(row)

    def get_by_id_with_audit(
        self,
        id: str,
        *,
        actor: str,
    ) -> Optional[Secret]:
        """SELECT the row + INSERT the ``read_public`` audit
        row in one ``engine.begin()`` transaction.

        Returns the row, or ``None`` if the id is unknown
        (NOT-found reads do NOT audit — the v1 contract
        audits successful reads only, the P2 marker-
        resolver audit will follow the same pattern).
        The read AND the audit row commit atomically;
        a crash between the two leaves neither side
        visible (M1 — governor pre-promote fix
        2026-10-17).
        """
        if "read_public" not in SECRET_AUDIT_ACTIONS:
            raise ValueError(
                "internal: 'read_public' missing from SECRET_AUDIT_ACTIONS"
            )
        with self._engine.begin() as conn:
            row = conn.execute(
                select(Secret).where(Secret.id == id)
            ).first()
            if row is None:
                # NOT-found does NOT audit (mirrors the
                # previous behavior — the v1 contract).
                return None
            _append_audit_in_conn(
                conn,
                action="read_public",
                secret_id=id,
                actor=actor,
            )
            return _row_to_secret(row)

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()


def _row_to_secret(row: Any) -> Secret:
    """Map a Core ``select(Secret)`` row to a :class:`Secret`
    ORM dataclass.

    Uses ``row._mapping`` (the SQLAlchemy ``RowMapping``
    view) to bind by column name — kills the column-order
    fragility that positional ``row[0]..row[7]`` carries
    when the SQLModel column order changes (the
    ``SecretAuditEvent`` precedent at
    ``daemon/repositories/prompt_version/repository.py``
    uses the same ``_mapping`` view for the same
    robustness reason). The ``at`` field is NOT on
    :class:`Secret` (it lives on ``SecretAuditEvent``) so
    the eight-column bound is the exact match for the
    current schema; a column-add later than ``created_at``
    would update this helper alongside the SQLModel.

    Module-level (NOT a method) because the three call
    sites live inside ``engine.begin()`` blocks where the
    ``self`` of ``_SqlSecretStore`` is irrelevant — the
    helper takes the row directly.
    """
    m = row._mapping
    return Secret(
        id=m["id"],
        name=m["name"],
        kind=m["kind"],
        public_material=m["public_material"],
        wrapped_secret=m["wrapped_secret"],
        created_by=m["created_by"],
        created_at=m["created_at"],
        retired_at=m["retired_at"],
    )


def _append_audit_in_conn(
    conn: Any,
    *,
    action: str,
    secret_id: Optional[str],
    actor: str,
    context: Optional[str] = None,
) -> None:
    """Insert one :class:`SecretAuditEvent` row inside the
    caller's ``engine.begin()`` transaction.

    The M1 atomicity contract (governor pre-promote fix,
    2026-10-17) requires the audit row to share the
    transaction with the mutation it audits — a crash
    between the two commits is the regression the fix
    closes. The helper takes the open ``conn`` from the
    caller (NEVER opens its own) so the transaction
    boundary stays where the caller defined it.

    Action validation: ``SECRET_AUDIT_ACTIONS`` (the
    canonical full-set; P1 emitters only use the P1
    subset ``SECRET_AUDIT_ACTIONS_P1`` per tests). The
    caller MUST have checked the action lives in the set
    before invoking the helper — the M1 callers do (the
    ``"create"/"retire"/"read_public" not in
    SECRET_AUDIT_ACTIONS`` pre-guards stay at the public
    method entry, NOT here, so a future caller that
    bypasses the guard gets an immediate ``ValueError``
    at the audit-insert site rather than a later
    IntegrityError).

    Context (P2, design §3.4): when ``context`` is non-None
    it MUST live in :data:`SECRET_AUDIT_RESOLVE_CONTEXTS`.
    P1 callers pass ``context=None`` (default) and skip
    the validation entirely — the additive value set stays
    additive without a CHECK constraint (design §4: the
    set is validated at the SERVICE boundary in a later
    P2 phase so future values stay additive).

    Mirrors the
    ``daemon/repositories/prompt_version/repository.py``
    ``_append_audit_in_conn`` helper (the named
    precedent — same shape, same purpose, same M1
    atomicity guarantee).
    """
    if action not in SECRET_AUDIT_ACTIONS:
        raise ValueError(
            f"_append_audit_in_conn: action {action!r} not "
            f"in SECRET_AUDIT_ACTIONS"
        )
    # Defense-in-depth: the public resolve_marker entry
    # already validates the additive set, but a future
    # caller that bypasses the public boundary gets an
    # immediate ValueError rather than a silently-bad
    # audit row. P1 callers never hit this branch
    # (context=None).
    if context is not None and context not in SECRET_AUDIT_RESOLVE_CONTEXTS:
        raise ValueError(
            f"_append_audit_in_conn: context {context!r} "
            f"not in SECRET_AUDIT_RESOLVE_CONTEXTS "
            f"({sorted(SECRET_AUDIT_RESOLVE_CONTEXTS)})"
        )
    conn.execute(
        SecretAuditEvent.__table__.insert().values(
            action=action,
            secret_id=secret_id,
            actor=actor,
            at=datetime.now(timezone.utc).isoformat(),
            context=context,
        )
    )


def _assert_nonempty_str(
    value: object, *, label: str
) -> None:
    """Isinstance-first guard for non-empty ``str`` values.

    Order matters: a wrong TYPE reports as ``TypeError``
    (the caller passed something other than a string),
    while an empty STRING reports as
    :class:`SecretValidationError` (the caller passed a
    string that was empty). The split matches the
    tmp_image_bridge.py:278 fold-isinstance-first
    precedent. Per the tidy pass convention, prefer
    ``TypeError`` for shape violations and
    ``SecretValidationError`` for content violations so
    the two failure modes stay diagnostic-distinct.
    """
    if not isinstance(value, str):
        raise TypeError(
            f"{label} must be a string; got "
            f"{type(value).__name__}"
        )
    if not value:
        raise SecretValidationError(
            f"{label} must be a non-empty string; got "
            f"{value!r}"
        )


class SecretService:
    """The in-process agent-secrets service (P1 scope).

    Composes the cipher, the store, and the public/private
    material split. Public methods are the P1 tool surface
    (one per P1 tool — see :mod:`daemon.tools.secrets_tools`).
    The service is engine-bound (a single ``SecretService``
    instance per process; today the manager is the wiring
    site, mirroring the
    ``SkillStoreService`` / ``PromptVersionService`` pattern).

    The P1 tool surface (six tools — plan §5.2):

    * ``generate_ssh_keypair(name)`` — mint a fresh ed25519
      keypair, wrap the private side, store it. Returns
      ``{id, name, public_key, fingerprint, marker}``.
    * ``generate_api_token(name, length=32)`` — mint a
      URL-safe token, wrap, store. Returns
      ``{id, name, token_preview, marker}`` (last-4-char
      preview only).
    * ``store(name, kind, value)`` — wrap an arbitrary
      pre-declared value, store it. Returns
      ``{id, name, marker}`` (the marker is the P1 read-
      reference; P2 will resolve it).
    * ``list()`` — list active rows. Returns
      ``[{id, name, kind, public_material}, ...]``. No
      marker, no wrapped_secret, no plaintext.
    * ``read_public(id)`` — read one row. Returns
      ``{id, name, kind, public_material}``. NEVER
      ``wrapped_secret`` (pubkey-only rule — plan §5.2
      + §5.2 pubkey-only rule is FINAL).
    * ``retire(id)`` — soft-retire. Returns
      ``{id, retired_at}``.

    Trust-model boundary (plan §1.2 + §5.1):

    The service implements NO per-call access check. The
    only access boundary is the per-agent ``tools.allow``
    category gate (the ``secrets`` privileged-category
    strip + the day-one allow-list on worker / tester /
    leader / ari meta.json). The service assumes any
    caller reaching the tool surface is authorized.
    """

    #: P1-emittable kinds. The forward-compatible set
    #: supports the ``store`` tool's ``kind`` parameter.
    KIND_SSH_KEYPAIR: str = "ssh_keypair"
    KIND_API_TOKEN: str = "api_token"
    KIND_GENERIC: str = "generic"
    SUPPORTED_KINDS: frozenset[str] = frozenset({
        KIND_SSH_KEYPAIR,
        KIND_API_TOKEN,
        KIND_GENERIC,
    })

    #: Maximum token length in bytes (the upper bound on
    #: ``secrets.token_urlsafe(nbytes)`` — minor ② governor
    #: pre-promote fix, 2026-10-17). The class-level alias
    #: makes ``self.MAX_API_TOKEN_LENGTH_BYTES`` resolvable
    #: from the service boundary; the module-level
    #: :data:`MAX_API_TOKEN_LENGTH_BYTES` is the canonical
    #: source of truth (also enforced inside
    #: ``generate_api_token_material``).
    MAX_API_TOKEN_LENGTH_BYTES: int = MAX_API_TOKEN_LENGTH_BYTES

    #: Minimum token length in bytes (NF-1 governor
    #: pre-promote fix, 2026-10-17, re-verification round).
    #: 4 bytes → 6-char URL-safe string → the
    #: ``token_preview = token_str[-4:]`` field is strictly
    #: shorter than the full token. A smaller length would
    #: let the caller reconstruct the entire token from the
    #: preview. Class-level alias mirrors
    #: :data:`MAX_API_TOKEN_LENGTH_BYTES`; the module-level
    #: constant is the canonical source of truth (also
    #: enforced inside ``generate_api_token_material``).
    MIN_API_TOKEN_LENGTH_BYTES: int = MIN_API_TOKEN_LENGTH_BYTES

    def __init__(
        self,
        engine: Any,
        cipher: Optional[SecretCipher] = None,
    ) -> None:
        self._engine = engine
        self._cipher = cipher or SecretCipher()
        self._store: _SecretStorePort = _SqlSecretStore(engine)

    @property
    def is_available(self) -> bool:
        """Whether the service is operational (cipher key was set)."""
        return self._cipher.is_available

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _assert_kind(self, kind: str) -> None:
        """Validate the ``kind`` parameter.

        Forward-compatible — new kinds can be added by
        extending :data:`SUPPORTED_KINDS`. The validator is
        the only place the kind set is consulted; the model
        is a dumb store.
        """
        if kind not in self.SUPPORTED_KINDS:
            raise SecretValidationError(
                f"unsupported kind {kind!r}; supported: "
                f"{sorted(self.SUPPORTED_KINDS)}"
            )

    @staticmethod
    def _assert_name(name: object) -> None:
        """Validate the ``name`` parameter.

        Isinstance-first ordering (per tidier pass #2, M7
        convention): a wrong TYPE reports as ``TypeError``;
        an empty string reports as
        :class:`SecretValidationError`. The split lets
        callers distinguish "you passed the wrong shape"
        (TypeError) from "you passed a string that was
        empty" (SecretValidationError) — a single-class
        union would obscure the difference.
        """
        _assert_nonempty_str(name, label="name")

    @staticmethod
    def _assert_actor(actor: object) -> None:
        """Validate the ``actor`` parameter (Cardinal #2).

        Isinstance-first ordering (mirror of
        :meth:`_assert_name`): wrong-type → ``TypeError``;
        empty string → :class:`SecretValidationError`.
        """
        _assert_nonempty_str(actor, label="actor")

    def _wrap_required(self) -> SecretCipher:
        """Return the cipher or raise (the service refuses to
        operate when the key is missing)."""
        if not self._cipher.is_available:
            raise SecretCipherUnavailable(
                f"SecretService unavailable: {SecretCipher.KEY_ENV} "
                f"is not set or is invalid"
            )
        return self._cipher

    # ─────────────────────────────────────────────────────────
    # generate_ssh_keypair
    # ─────────────────────────────────────────────────────────

    def generate_ssh_keypair(
        self, name: str, actor: str
    ) -> dict[str, Any]:
        """Generate a fresh ed25519 keypair, wrap the private
        side, store the row, audit.

        Args:
            name: Human-readable label (UNIQUE).
            actor: Agent id (Cardinal #2 — the calling
                agent, not the HTTP subject).

        Returns:
            ``{id, name, public_key, fingerprint, marker}`` —
            the P1 tool response shape.
        """
        self._assert_name(name)
        self._assert_actor(actor)

        cipher = self._wrap_required()
        material = generate_ssh_keypair_material()

        # Wrap the OpenSSH private PEM (the canonical form
        # the user would copy into ``~/.ssh/id_ed25519``).
        # The raw seed is dropped on the floor once the
        # cipher.encrypt call returns — the service holds
        # no reference to the raw bytes past the wrap call.
        wrapped = cipher.wrap(material.private_openssh_pem)

        secret_id = str(uuid.uuid4())
        marker = mint_marker(secret_id)
        now_iso = self._now_iso()

        row = Secret(
            id=secret_id,
            name=name,
            kind=self.KIND_SSH_KEYPAIR,
            public_material=material.public_openssh,
            wrapped_secret=wrapped,
            created_by=actor,
            created_at=now_iso,
            retired_at=None,
        )
        # M1 atomic path: secret row + create audit row
        # commit in ONE ``engine.begin()`` (the governor
        # pre-promote fix, 2026-10-17 — mirrors
        # ``prompt_version/repository.py:atomic_transition``).
        # On UNIQUE(name) collision, the IntegrityError
        # surfaces and we translate to a structured service
        # error.
        try:
            stored = self._store.insert_with_audit(row, actor=actor)
        except IntegrityError as exc:
            # UNIQUE(name) collision surfaces as a
            # SQLAlchemy IntegrityError — translate to a
            # structured service error. Other DB errors
            # propagate as their real type so the tool
            # envelope surfaces them verbatim (M2).
            raise SecretNameConflictError(
                f"secret name {name!r} already exists"
            ) from exc

        # Capture the public-side values before we drop
        # the in-memory ``material`` reference — the
        # return dict needs them.
        public_key = material.public_openssh
        fingerprint = material.fingerprint

        # Discard in-memory private bytes now that the
        # wrap is committed (defense-in-depth — the
        # ``SecretSshKeypairMaterial`` dataclass is the
        # sole in-memory carrier; once ``stored`` returns
        # the only reference is the wrapped bytes in the
        # row, the plain bytes are unreachable). The
        # dataclass goes out of scope on return.
        del material, wrapped

        # Use the pre-insert ``secret_id`` + ``marker`` (the
        # row was inserted with this id; ``stored.id`` is
        # always equal unless a UNIQUE collision diverted
        # the control flow, in which case we never reach
        # here — the IntegrityError raised
        # SecretNameConflictError before the ``del``). The
        # sibling creators (``generate_api_token``,
        # ``store``) follow the same pattern.

        return {
            "id": secret_id,
            "name": name,
            "public_key": public_key,
            "fingerprint": fingerprint,
            "marker": marker,
        }

    # ─────────────────────────────────────────────────────────
    # generate_api_token
    # ─────────────────────────────────────────────────────────

    def generate_api_token(
        self, name: str, length: int = 32, actor: str = ""
    ) -> dict[str, Any]:
        """Generate a URL-safe token, wrap, store the row, audit.

        Args:
            name: Human-readable label (UNIQUE).
            length: Token length in bytes (the
                ``secrets.token_urlsafe`` argument; the
                resulting string is longer in chars). Must
                be ``>= MIN_API_TOKEN_LENGTH_BYTES`` (4)
                and ``<= MAX_API_TOKEN_LENGTH_BYTES``
                (256). Default 32.
            actor: Agent id (Cardinal #2).

        Returns:
            ``{id, name, token_preview, marker}`` — the P1
            tool response shape. ``token_preview`` is the
            LAST 4 CHARS of the URL-safe token string —
            NEVER the full token, NEVER the wrapped form,
            NEVER the raw bytes. The full token is held
            only in memory until the cipher.encrypt call
            returns; the tool response carries the
            last-4-char preview only (the v1 read tool
            does NOT exist — agents re-derive the token
            from the marker once P2 ships).
        """
        self._assert_name(name)
        self._assert_actor(actor)
        if (
            not isinstance(length, int)
            or isinstance(length, bool)
            or length < MIN_API_TOKEN_LENGTH_BYTES
        ):
            # Bool subclass-int guard: ``bool`` is a subclass
            # of ``int`` in Python — a True/False passes
            # ``isinstance(True, int)``. Reject booleans
            # explicitly so the caller cannot smuggle a
            # 0/1 as a length arg. Floor at MIN_* so the
            # URL-safe string is at least 6 chars (NF-1
            # governor pre-promote fix, 2026-10-17).
            raise SecretValidationError(
                f"length must be an int >= "
                f"{MIN_API_TOKEN_LENGTH_BYTES}; got {length!r}"
            )
        # Cap (CWE-770 resource-exhaustion guard — minor ②).
        # The cap is also enforced inside
        # ``generate_api_token_material`` (defense-in-depth),
        # but the service is the canonical boundary check.
        if length > self.MAX_API_TOKEN_LENGTH_BYTES:
            raise SecretValidationError(
                f"length {length} exceeds the "
                f"{self.MAX_API_TOKEN_LENGTH_BYTES}-byte cap; "
                f"see SecretService.MAX_API_TOKEN_LENGTH_BYTES"
            )

        cipher = self._wrap_required()
        token_bytes = generate_api_token_material(length=length)
        wrapped = cipher.wrap(token_bytes)
        token_str = token_bytes.decode("ascii")
        # Preview = last 4 chars. The full token NEVER
        # crosses the tool boundary in v1. Refuse any
        # token whose URL-safe string is <= 4 chars — NF-1
        # governor pre-promote fix (2026-10-17). ``secrets
        # .token_urlsafe(3)`` yields EXACTLY 4 chars, and
        # ``token_str[-4:]`` on a 4-char string returns the
        # entire token, defeating the v1 last-4-char-only
        # contract. The helper now also enforces a 4-byte
        # floor (``MIN_API_TOKEN_LENGTH_BYTES``), but this
        # post-generation guard remains as defense-in-depth
        # so any future regression in the helper cannot
        # weaken the v1 contract.
        if len(token_str) <= 4:
            raise SecretValidationError(
                f"token length {length} produces a "
                f"{len(token_str)}-char URL-safe string; "
                f"the minimum is > 4 chars (the v1 "
                f"last-4-char preview contract)"
            )
        token_preview = token_str[-4:]

        secret_id = str(uuid.uuid4())
        marker = mint_marker(secret_id)
        now_iso = self._now_iso()

        row = Secret(
            id=secret_id,
            name=name,
            kind=self.KIND_API_TOKEN,
            public_material=None,
            wrapped_secret=wrapped,
            created_by=actor,
            created_at=now_iso,
            retired_at=None,
        )
        # M1 atomic path — see ``generate_ssh_keypair``
        # for the rationale.
        try:
            self._store.insert_with_audit(row, actor=actor)
        except IntegrityError as exc:
            raise SecretNameConflictError(
                f"secret name {name!r} already exists"
            ) from exc

        # Drop in-memory token bytes (defense-in-depth — the
        # wrap is committed; the only remaining reference is
        # the wrapped form in the row).
        del token_bytes, wrapped

        return {
            "id": secret_id,
            "name": name,
            "token_preview": token_preview,
            "marker": marker,
        }

    # ─────────────────────────────────────────────────────────
    # store
    # ─────────────────────────────────────────────────────────

    def store(
        self, name: str, kind: str, value: str, actor: str
    ) -> dict[str, Any]:
        """Wrap-and-store an arbitrary value.

        Args:
            name: Human-readable label (UNIQUE).
            kind: Forward-compatible kind tag (must be in
                :data:`SUPPORTED_KINDS`).
            value: The plaintext value to wrap. The caller
                owns any pre-validation (length / format);
                the service wraps and stores the raw
                bytes (UTF-8 encoded).
            actor: Agent id (Cardinal #2).

        Returns:
            ``{id, name, marker}`` — the P1 tool response
            shape. The plaintext value NEVER crosses the
            tool boundary (the response is just the
            metadata + the marker; the value is held only
            in memory until the cipher.encrypt call
            returns, then dropped on the floor).
        """
        self._assert_name(name)
        self._assert_actor(actor)
        # value allows empty strings (the original check was
        # type-only — wrapping an empty payload is a no-op
        # but not an error).
        if not isinstance(value, str):
            raise SecretValidationError(
                f"value must be a string; got {type(value).__name__}"
            )
        self._assert_kind(kind)

        cipher = self._wrap_required()
        wrapped = cipher.wrap(value.encode("utf-8"))

        secret_id = str(uuid.uuid4())
        marker = mint_marker(secret_id)
        now_iso = self._now_iso()

        row = Secret(
            id=secret_id,
            name=name,
            kind=kind,
            public_material=None,
            wrapped_secret=wrapped,
            created_by=actor,
            created_at=now_iso,
            retired_at=None,
        )
        # M1 atomic path — see ``generate_ssh_keypair``
        # for the rationale.
        try:
            self._store.insert_with_audit(row, actor=actor)
        except IntegrityError as exc:
            raise SecretNameConflictError(
                f"secret name {name!r} already exists"
            ) from exc

        # Drop the in-memory plaintext/wrapped pair (the
        # wrap is committed; the only remaining reference
        # is the wrapped form in the row).
        del wrapped

        return {
            "id": secret_id,
            "name": name,
            "marker": marker,
        }

    # ─────────────────────────────────────────────────────────
    # list
    # ─────────────────────────────────────────────────────────

    def list(self, actor: str) -> list[dict[str, Any]]:
        """List active rows (no secrets, no markers, no plaintext).

        Returns a list of :class:`SecretListView` dicts:
        ``{id, name, kind, public_material}``. The list
        tool does NOT return markers (plan §5.2: only the
        create-time response carries the marker; the list
        view is a metadata scan).

        Args:
            actor: Agent id (audit attribution).
        """
        self._assert_actor(actor)
        rows = self._store.list_active()
        return [
            SecretListView(
                id=r.id,
                name=r.name,
                kind=r.kind,
                public_material=r.public_material,
            ).to_dict()
            for r in rows
        ]

    # ─────────────────────────────────────────────────────────
    # read_public
    # ─────────────────────────────────────────────────────────

    def read_public(
        self, id: str, actor: str
    ) -> dict[str, Any]:
        """Read one row's public material.

        P1 contract: the ONLY read path. NEVER returns
        ``wrapped_secret``, NEVER returns the decrypted
        plaintext, NEVER returns the private key bits.
        Returns ``{id, name, kind, public_material}`` —
        the pubkey-only rule (plan §5.2, retained from I2).

        Args:
            id: UUID4 string.
            actor: Agent id (audit attribution).

        Raises:
            SecretNotFoundError: When the id is unknown.
        """
        _assert_nonempty_str(id, label="id")
        self._assert_actor(actor)
        row = self._store.get_by_id_with_audit(id, actor=actor)
        if row is None:
            raise SecretNotFoundError(
                f"secret id {id!r} not found"
            )
        # Audit row stamped INSIDE the same
        # ``engine.begin()`` as the SELECT (M1 — see
        # ``_SqlSecretStore.get_by_id_with_audit`` for
        # the rationale). NOT-found reads do NOT audit
        # (the v1 contract — successful reads only;
        # the P2 marker-resolver audit follows the
        # same pattern).
        return SecretPublicView(
            id=row.id,
            name=row.name,
            kind=row.kind,
            public_material=row.public_material,
        ).to_dict()

    # ─────────────────────────────────────────────────────────
    # retire
    # ─────────────────────────────────────────────────────────

    def retire(self, id: str, actor: str) -> dict[str, Any]:
        """Soft-retire a row.

        Sets ``retired_at`` to the current ISO timestamp; the
        row stays queryable for audit. The active-registry
        partial index skips retired rows.

        Args:
            id: UUID4 string.
            actor: Agent id (audit attribution).

        Returns:
            ``{id, retired_at}`` — the P1 tool response
            shape.

        Raises:
            SecretNotFoundError: When the id is unknown.
        """
        _assert_nonempty_str(id, label="id")
        self._assert_actor(actor)
        now_iso = self._now_iso()
        # M1 atomic path — see ``generate_ssh_keypair``
        # for the rationale. The UPDATE + audit row commit
        # in one ``engine.begin()``.
        row = self._store.mark_retired_with_audit(
            id, now_iso, actor=actor
        )
        if row is None:
            raise SecretNotFoundError(
                f"secret id {id!r} not found"
            )
        return {
            "id": id,
            "retired_at": now_iso,
        }

    # ─────────────────────────────────────────────────────────
    # resolve_marker (P2 — design §1, §3.1, §3.2, §3.4)
    # ─────────────────────────────────────────────────────────

    def resolve_marker(
        self,
        actor: str,
        secret_id: str,
        context: str,
        *,
        surface: str = "tool_param",
    ) -> str:
        """Resolve a marker ``secret_id`` to its plaintext value.

        ONE ``engine.begin()`` transaction (design §1 + §3.1):

          1. ``SELECT id, wrapped_secret, retired_at FROM secrets
             WHERE id = :id`` — re-read per resolution (no RAM
             plaintext cache anywhere in the resolver stack;
             the LANE-2 mirror — restart-durable by construction).
          2. ``SecretCipher.unwrap`` — Fernet decrypt the
             ``wrapped_secret`` bytes.
          3. Stamp one ``use_marker`` audit row INSIDE the same
             transaction — on success AND on rejection
             (``secret_id`` stamped for hits; ``NULL`` for
             not-found per design §3.4).
          4. Return plaintext decoded as UTF-8.

        Failure mapping (design §3.2 — fail-closed at every
        branch; ``secret_retired`` is RESERVED and NEVER emitted
        while D1 soft-retire semantics hold):

          * missing id → ``secret_not_found``
          * malformed marker (pre-DB) → ``secret_marker_malformed``
            — detected at the resolver wrapper, not here; the
            service only sees a UUID4 string
          * retired id → CONTINUES TO RESOLVE (D1 leader-ratified
            semantics; soft-retire = delist + audit, not
            revocation). The ``retired_at`` column is selected
            but NEVER causes a branch — its presence is ignored.
          * SQLAlchemy ``OperationalError`` on the SELECT →
            ``secret_resolver_store_unavailable`` (one-shot,
            no retry inside the resolver; retry belongs to the
            agent loop — design §3.2)
          * ``Fernet.InvalidToken`` at unwrap →
            ``secret_fernet_key_mismatch`` (names the
            SYSTEM_ENCRYPTION_KEY rotation coupling)
          * ``SecretCipher.is_available()`` ``False`` at the
            service boundary → ``secret_cipher_unavailable``

        Args:
            actor: Agent id (Cardinal #2). NON-EMPTY — enforced
                at the service boundary.
            secret_id: UUID4 string (the inner part of the
                marker — parsed by the resolver wrapper).
                NON-EMPTY — enforced here.
            context: Audit-context tag, MUST be one of
                :data:`SECRET_AUDIT_RESOLVE_CONTEXTS`.
                Validated at the service boundary so future
                values stay additive without a CHECK constraint
                (design §4).
            surface: One of :data:`SECRET_ENVELOPE_SURFACES` —
                stamped on the raised
                :class:`SecretMarkerResolutionError` so the
                seam can format the JSON envelope without
                re-deriving it. Defaults to ``"tool_param"``
                because the single-value resolver form is the
                tool-param pre-invoke call site (design §1d);
                the dict form's wrapper overrides to
                ``"bash.env"`` / ``"mcp_spawn"`` per seam.

        Returns:
            The decrypted plaintext as a UTF-8 ``str``. NEVER
            cached at any layer — the caller holds the only
            live reference; the function returns and the
            reference leaves the service scope immediately.

        Raises:
            SecretValidationError: ``actor`` empty,
                ``secret_id`` empty, or ``context`` not in
                :data:`SECRET_AUDIT_RESOLVE_CONTEXTS`.
            SecretCipherUnavailable: The cipher refused to
                operate (``SYSTEM_ENCRYPTION_KEY`` absent or
                invalid). Mapped to
                ``secret_cipher_unavailable`` envelope — the
                resolver wrapper raises the typed error with
                ``code=SECRET_CODE_CIPHER_UNAVAILABLE``.
            SecretMarkerResolutionError: Typed envelope for
                the DB-layer failure modes — ``code`` carries
                the envelope code, ``surface`` carries the
                seam tag, ``secret_id`` carries the inner id
                (never the value). Raised AFTER the audit
                row commits (design §3.4: stamp on rejection).
        """
        self._assert_actor(actor)
        _assert_nonempty_str(secret_id, label="secret_id")
        # Validate context at the service boundary (design §3.4 +
        # §4: no CHECK constraint, additive value set validated
        # here so future values stay additive).
        if context not in SECRET_AUDIT_RESOLVE_CONTEXTS:
            raise SecretValidationError(
                f"context {context!r} not in "
                f"SECRET_AUDIT_RESOLVE_CONTEXTS "
                f"({sorted(SECRET_AUDIT_RESOLVE_CONTEXTS)})"
            )

        # Cipher check — fail-CLOSED before any DB work.
        # The resolver wrapper maps the raised
        # ``SecretCipherUnavailable`` to the typed envelope.
        cipher = self._wrap_required()

        # The resolver wrapper passes a validated surface
        # from the seam; double-check here as defense-in-
        # depth so a future caller cannot sneak an unknown
        # surface onto a raised envelope.
        if surface not in SECRET_ENVELOPE_SURFACES:
            raise SecretValidationError(
                f"surface {surface!r} not in "
                f"SECRET_ENVELOPE_SURFACES "
                f"({sorted(SECRET_ENVELOPE_SURFACES)})"
            )

        # Outcome flags — populated inside the transaction,
        # read OUTSIDE the with block (so the audit row
        # commits before any exception propagates).
        _not_found = False
        _fernet_failure = False
        _cipher_failure = False
        _plaintext_str: Optional[str] = None

        try:
            with self._engine.begin() as conn:
                row = conn.execute(
                    select(
                        Secret.id,
                        Secret.wrapped_secret,
                        Secret.retired_at,
                    ).where(Secret.id == secret_id)
                ).first()
                if row is None:
                    # NOT-found — stamp use_marker audit row
                    # with NULL secret_id (design §3.4). The
                    # audit row commits WITH the transaction;
                    # the typed raise happens AFTER the with
                    # block so the row is visible.
                    _append_audit_in_conn(
                        conn,
                        action="use_marker",
                        secret_id=None,
                        actor=actor,
                        context=context,
                    )
                    _not_found = True
                else:
                    # Row exists. ``retired_at`` is read but
                    # NEVER branched on — D1 keeps retired
                    # markers resolving (design §3.2).
                    try:
                        plaintext_bytes = cipher.unwrap(
                            row.wrapped_secret
                        )
                    except SecretCipherUnavailable:
                        # Defensive — ``_wrap_required()``
                        # checked at construction; this is a
                        # mid-flight env change. Stamp a
                        # use_marker audit row carrying the
                        # secret_id (we found the row, just
                        # couldn't decrypt) and raise.
                        _append_audit_in_conn(
                            conn,
                            action="use_marker",
                            secret_id=secret_id,
                            actor=actor,
                            context=context,
                        )
                        _cipher_failure = True
                    except InvalidToken:
                        # SYSTEM_ENCRYPTION_KEY rotated
                        # without re-encrypting the row
                        # (design §3.2 + §7.1 — rotation
                        # coupling). Stamp the use_marker
                        # row with the secret_id (we know
                        # the id; the row exists; the
                        # ciphertext is wrong).
                        _append_audit_in_conn(
                            conn,
                            action="use_marker",
                            secret_id=secret_id,
                            actor=actor,
                            context=context,
                        )
                        _fernet_failure = True
                    else:
                        # Success path — design §1: SELECT +
                        # unwrap + audit-stamp in ONE
                        # transaction. The plaintext is
                        # returned via the flag; the
                        # reference leaves the service
                        # scope on return (no in-RAM
                        # plaintext cache at this layer).
                        _append_audit_in_conn(
                            conn,
                            action="use_marker",
                            secret_id=secret_id,
                            actor=actor,
                            context=context,
                        )
                        _plaintext_str = (
                            plaintext_bytes.decode("utf-8")
                        )
        except DBAPIError as exc:
            # DB unavailable / flapping (design §3.2) is
            # broader than ``OperationalError`` alone — sibling
            # DBAPIError subclasses (``InterfaceError`` for
            # connection-level dropouts, ``DatabaseError`` for
            # driver-side faults) land here too. One-shot
            # fail-closed: NO retry at this layer — retry
            # belongs to the agent loop. The audit row never
            # committed (the transaction was rolled back by
            # the DBAPIError); that's acceptable — the
            # design's "use_marker on success AND rejection"
            # rule applies to resolution-time failures, not
            # to DB-completely-down conditions where we
            # cannot write anything.
            #
            # ``IntegrityError`` is a ``DBAPIError`` subclass
            # but a PROGRAMMER-error class (UNIQUE collision /
            # FK violation / NOT NULL etc.) — NOT a "DB
            # unavailable" condition. Re-raise so the envelope
            # contract does NOT paper over real bugs; the
            # typed envelope is reserved for genuine flapping
            # / down conditions.
            if isinstance(exc, IntegrityError):
                raise
            raise SecretMarkerResolutionError(
                secret_id=secret_id,
                code=SECRET_CODE_STORE_UNAVAILABLE,
                surface=surface,
            ) from exc

        # Raise AFTER the transaction commits so the audit
        # row is visible to subsequent reads in the same
        # agent loop. Order: typed envelope carries
        # ``secret_id`` (never the value), ``code``, and
        # ``surface`` — PB-F1 clean (design §3.3).
        if _not_found:
            raise SecretMarkerResolutionError(
                secret_id=secret_id,
                code=SECRET_CODE_NOT_FOUND,
                surface=surface,
            )
        if _cipher_failure:
            raise SecretMarkerResolutionError(
                secret_id=secret_id,
                code=SECRET_CODE_CIPHER_UNAVAILABLE,
                surface=surface,
            )
        if _fernet_failure:
            raise SecretMarkerResolutionError(
                secret_id=secret_id,
                code=SECRET_CODE_FERNET_KEY_MISMATCH,
                surface=surface,
            )

        # ``_plaintext_str`` is non-None on the success
        # branch (the decode() is unconditional inside the
        # ``else`` arm). Type-narrowing for the linter.
        assert _plaintext_str is not None, (
            "resolve_marker: success branch produced no "
            "plaintext — internal invariant violated"
        )
        return _plaintext_str
#
# All errors are structured; the tool boundary catches each one
# and returns a JSON envelope. The tool boundary NEVER raises a
# raw exception — every failure mode is a JSON error message.


class SecretServiceError(RuntimeError):
    """Base class for all SecretService errors."""


class SecretValidationError(SecretServiceError, ValueError):
    """Raised on input validation failures (bad name, bad kind,
    bad length, etc.)."""


class SecretNameConflictError(SecretServiceError):
    """Raised when ``name`` already exists (UNIQUE collision)."""


class SecretNotFoundError(SecretServiceError, LookupError):
    """Raised when ``id`` is unknown (read_public / retire)."""


# ─────────────────────────────────────────────────────────────────────
# P2 resolution envelope vocabulary
#   (p2-architecture-recommendation §3.3 + §3.4)
# ─────────────────────────────────────────────────────────────────────
#
# Every ``SecretService.resolve_marker`` failure maps to ONE
# of the typed envelope codes below. The codes are the SINGLE
# SOURCE OF TRUTH for the JSON envelope the seams format; the
# error envelope NEVER carries the value (PB-F1 family).
#
# Anti-differential phrasing (design §3.3): the
# ``secret_not_found`` / ``secret_marker_malformed`` /
# ``secret_retired`` family shares the IDENTICAL message
# template ``"secret resolution failed: <code>"`` — only the
# ``code`` attribute distinguishes them. The PRIMARY
# enumeration control is UUID4 entropy (2^122 is infeasible
# to guess); identical phrasing is the SECONDARY hardening
# (R7, 🟡). Which id is live stays in the audit log only.
#
# Markers minted in v0.21.0 MUST keep resolving in P2 — the
# marker format is FROZEN and no re-mint happens (design §3.1).

#: Typed envelope code — the SELECT returned zero rows for the
#: requested ``secret_id``. Fail-closed: child/server NOT spawned.
SECRET_CODE_NOT_FOUND: str = "secret_not_found"
#: Typed envelope code — the marker string fullmatch failed
#: (e.g. marker prefix without a valid UUID4 inside). Detected
#: at the resolver wrapper, pre-DB (design §3.2).
SECRET_CODE_MARKER_MALFORMED: str = "secret_marker_malformed"
#: Typed envelope code — **RESERVED**. Soft-retire semantics in
#: v1 keep retired markers RESOLVABLE (D1 leader-ratified;
#: ``secret_retired`` envelope is NEVER emitted while this
#: ruling holds). Flipping to revocation semantics is a one-
#: branch change at the resolver — see ``p2-architecture-
#: recommendation.md §8 D1``. Retired VALUES remain in the
#: scrub set regardless of resolve policy (design §2.4).
SECRET_CODE_RETIRED: str = "secret_retired"
#: Typed envelope code — SQLAlchemy ``OperationalError`` (or
#: sibling DB-unavailable exception) on the SELECT. One-shot
#: fail-closed; the resolver NEVER retries — retry belongs to
#: the agent loop (design §3.2). The seams format this into a
#: typed envelope; partial env / partial spawn is impossible.
SECRET_CODE_STORE_UNAVAILABLE: str = "secret_resolver_store_unavailable"
#: Typed envelope code — ``cryptography.fernet.InvalidToken``
#: on the unwrap (the Fernet key was rotated without
#: re-encrypting ``secrets.wrapped_secret``). The code names
#: the SYSTEM_ENCRYPTION_KEY rotation coupling explicitly so
#: operators can find it in the runbook (design §3.2).
SECRET_CODE_FERNET_KEY_MISMATCH: str = "secret_fernet_key_mismatch"
#: Typed envelope code — ``SecretCipher.is_available()`` was
#: ``False`` at the service boundary (mirror of
#: ``KMSUnavailableError`` at ``kms_lite.py:130-141``). The
#: service refused to operate — fail-closed (design §3.2).
SECRET_CODE_CIPHER_UNAVAILABLE: str = "secret_cipher_unavailable"

#: Frozen set of all envelope codes. ``SecretMarkerResolutionError``
#: validates against this set at construction so a future caller
#: that mistypes a code fails loud (defense-in-depth). ``secret_retired``
#: lives here for forward compatibility — emitting it is a
#: programmer error while D1 soft-retire semantics hold.
SECRET_ENVELOPE_CODES: frozenset[str] = frozenset({
    SECRET_CODE_NOT_FOUND,
    SECRET_CODE_MARKER_MALFORMED,
    SECRET_CODE_RETIRED,
    SECRET_CODE_STORE_UNAVAILABLE,
    SECRET_CODE_FERNET_KEY_MISMATCH,
    SECRET_CODE_CIPHER_UNAVAILABLE,
})

#: Audit-context values the service boundary accepts for
#: ``resolve_marker`` (``context`` column on ``secrets_audit``).
#: The set is ADDITIVE — a future P2 phase can extend it
#: without a migration (no CHECK constraint, design §4).
#: P1 paths (``create`` / ``read_public`` / ``retire``) keep
#: ``context=NULL``; only ``use_marker`` (this phase) and
#: ``scrub`` (Phase 4) populate it.
SECRET_AUDIT_RESOLVE_CONTEXTS: frozenset[str] = frozenset({
    "bash_env",
    "mcp_spawn",
    "tool_param",
    # ``scrub`` is emitted by the Phase-4 persistence-scrub
    # paths (design §3.4 + §2.5); the constant lives here so
    # the additive set is canonical today and Phase 4 needs
    # no schema review.
    "scrub",
})

#: Envelope ``surface`` strings — the per-seam tag the seams
#: stamp on the JSON error envelope (``{ok:false, code,
#: surface, message}``). Note ``bash.env`` carries a DOT
#: while the audit context is the underscore form ``bash_env``;
#: the other two surfaces are byte-identical between envelope
#: and audit forms. The mapping lives at the resolver wrapper.
SECRET_ENVELOPE_SURFACES: frozenset[str] = frozenset({
    "bash.env",
    "mcp_spawn",
    "tool_param",
})


class SecretMarkerResolutionError(RuntimeError):
    """Typed envelope raised when ``SecretService.resolve_marker``
    fails — the canonical error class for P2 marker resolution
    (design §1 + §3).

    Carries:

    * ``secret_id``: the UUID4 string the caller asked for
      (NEVER the value — PB-F1 family).
    * ``code``: one of :data:`SECRET_ENVELOPE_CODES`.
    * ``surface``: one of :data:`SECRET_ENVELOPE_SURFACES`,
      stamped by the resolver wrapper from the seam caller
      so the seam can format the JSON envelope without
      re-deriving the surface.

    ``secret_id`` is **operator-diagnostics only**. It is held
    on the instance attribute (``self.secret_id``) so a
    postmortem operator can correlate the failure with the
    stored row, but it MUST NEVER be serialized into any
    envelope, log line, or ``str(exc)`` / ``repr(exc)`` form
    that crosses an agent / tool / consumer boundary. The
    ``secret_id`` field that does cross the boundary lives on
    the JSON envelope and is structurally shaped by the seam
    as a separate column / key, not derived from this
    attribute — the convention is enforced by the
    ``test_resolve_marker_malformed_raw_payload_never_in_envelope``
    pin in :mod:`tests.unit.test_secret_resolver`.

    Anti-differential message phrasing (design §3.3):
    ``str(exc)`` is the literal ``"secret resolution failed:
    <code>"`` — the SAME template across the
    ``secret_not_found`` / ``secret_marker_malformed`` /
    ``secret_retired`` family. The ``code`` attribute is the
    only struct field that distinguishes them; the primary
    enumeration control is UUID4 entropy (R7, 🟡).

    Mirrors the KMS resolver shape (``kms_resolver.py:82-98``):
    a ``RuntimeError`` subclass carrying ``handle``/``secret_id``
    (never the value) plus the failure code.
    """

    def __init__(
        self,
        *,
        secret_id: str,
        code: str,
        surface: str,
    ) -> None:
        if code not in SECRET_ENVELOPE_CODES:
            # Defensive — a mistyped code is a programmer error.
            raise ValueError(
                f"SecretMarkerResolutionError: code {code!r} "
                f"not in SECRET_ENVELOPE_CODES "
                f"({sorted(SECRET_ENVELOPE_CODES)})"
            )
        if surface not in SECRET_ENVELOPE_SURFACES:
            # Same defense — surface must be a known seam.
            raise ValueError(
                f"SecretMarkerResolutionError: surface {surface!r} "
                f"not in SECRET_ENVELOPE_SURFACES "
                f"({sorted(SECRET_ENVELOPE_SURFACES)})"
            )
        self.secret_id = secret_id
        self.code = code
        self.surface = surface
        # Anti-differential phrasing — identical template across
        # the family; only the ``code`` attribute differs (which
        # a structured-envelope reader uses). PB-F1 rule: NEVER
        # include the value or the marker literal here.
        super().__init__(f"secret resolution failed: {code}")
