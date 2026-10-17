"""``SecretService`` — Phase 1 of agent-secrets (plan f11965039).

Phase 1 ships the durable Fernet-encrypted secret store + the
``secrets`` tool category (#6 in ``PRIVILEGED_TOOL_CATEGORIES``,
quintet → sextet per the Q1/Q2/Q3 decisions 2026-10-17). Phase 2 will
add the marker resolver and the consumption seams (bash ``env``
overlay, MCP spawn, tool params, exact-match transcript scrub).

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
from sqlmodel import Session, SQLModel, and_, select

from daemon.repositories.secrets.models import (
    SECRET_AUDIT_ACTIONS,
    SECRET_AUDIT_ACTIONS_P1,
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
        except Exception as exc:  # noqa: BLE001 — Fernet raises ValueError on bad key
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

    Returns:
        The :class:`SecretSshKeypairMaterial` with all four
        fields populated. The private-side bytes are held in
        memory only; the service must wrap them via
        :class:`SecretCipher` and drop the in-memory copy
        ASAP (the dataclass is short-lived; the service
        does NOT keep a reference past the wrap call).
    """
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    # Public side — OpenSSH ``authorized_keys`` form.
    public_openssh_bytes: bytes = public_key.public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    )
    public_openssh: str = public_openssh_bytes.decode("ascii").strip()

    # Fingerprint — SHA256 of the public key bytes, base64-
    # decoded from the OpenSSH wire form (matches
    # ``ssh-keygen -lf`` output, ``SHA256:xxxx``).
    import base64
    import hashlib

    raw_public_bytes: bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    digest = hashlib.sha256(raw_public_bytes).digest()
    # SSH fingerprint format: ``SHA256:<base64-no-padding>``
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


def generate_api_token_material(length: int = 32) -> bytes:
    """Generate a URL-safe token via ``secrets.token_urlsafe``.

    The default length is 32 bytes (256 bits) — the
    ``secrets.generate_api_token`` tool's default. The
    returned bytes are the raw token material (Fernet-wrapped
    by the service before storage; the tool response carries
    only the last-4-char preview).
    """
    if length <= 0:
        raise ValueError(f"length must be positive; got {length}")
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

    The interface is intentionally narrow: four calls, all
    sync, all returning frozen dataclasses. The P1
    implementation backs onto ``secrets`` +
    ``secrets_audit`` (the SQLModel tables). The store
    functions NEVER touch the cipher or the marker scheme
    — those are the service's job. Splitting store from
    cipher is what makes the contract process-boundary-
    clean: a future port-wrapping layer would replace
    the store with a remote / Vault / OpenBao backend
    without touching the cipher (or vice versa — a future
    KMS-backed cipher would replace the cipher without
    touching the store).

    The four calls:

    * ``insert(secret)`` — INSERT a new row, RETURNING the
      stored row. Raises on UNIQUE(name) collision.
    * ``get_by_id(id)`` — SELECT by id, including retired
      rows. Returns ``None`` if missing.
    * ``list_active()`` — SELECT all non-retired rows (used
      by ``secrets.list``). Returns the public views only.
    * ``mark_retired(id, at_iso)`` — UPDATE retired_at;
      returns the updated row or ``None`` if not found.
    * ``append_audit(action, secret_id, actor)`` — INSERT
      an audit row. The action MUST be in
      :data:`SECRET_AUDIT_ACTIONS`; the actor is the
      agent id.

    The ``callable`` annotation on the constructor's
    ``engine_factory`` is a hint that the port takes a
    callable returning an engine, NOT a connection
    directly — the SQLAlchemy backend opens its own
    short-lived sessions per call (the prompt_version
    repository pattern).
    """

    def insert(self, secret: Secret) -> Secret: ...
    def get_by_id(self, id: str) -> Optional[Secret]: ...
    def list_active(self) -> list[Secret]: ...
    def mark_retired(self, id: str, at_iso: str) -> Optional[Secret]: ...
    def append_audit(
        self,
        action: str,
        secret_id: Optional[str],
        actor: str,
    ) -> None: ...


class _SqlSecretStore:
    """SQLAlchemy-backed port implementation.

    Engine-only constructor — the store holds no state
    besides the engine. All methods are sync; callers
    bridge via ``asyncio.to_thread`` (the pattern from
    prompt_version/repository.py). Per-method short-lived
    ``Session`` — every public method opens its own
    session and commits; no instance state.
    """

    def __init__(self, engine: Any) -> None:
        self._engine = engine

    def insert(self, secret: Secret) -> Secret:
        with Session(self._engine) as session:
            session.add(secret)
            session.commit()
            session.refresh(secret)
            return secret

    def get_by_id(self, id: str) -> Optional[Secret]:
        with Session(self._engine) as session:
            stmt = select(Secret).where(Secret.id == id)
            row = session.exec(stmt).first()
            return row

    def list_active(self) -> list[Secret]:
        with Session(self._engine) as session:
            stmt = select(Secret).where(Secret.retired_at.is_(None))
            return list(session.exec(stmt).all())

    def mark_retired(
        self, id: str, at_iso: str
    ) -> Optional[Secret]:
        with Session(self._engine) as session:
            stmt = select(Secret).where(Secret.id == id)
            row = session.exec(stmt).first()
            if row is None:
                return None
            # The model carries a frozen ``retired_at`` column;
            # SQLAlchemy tracks the assignment, no need for a
            # separate UPDATE.
            row.retired_at = at_iso
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def append_audit(
        self,
        action: str,
        secret_id: Optional[str],
        actor: str,
    ) -> None:
        # Action validation is the service boundary's job; the
        # store is a dumb writer.
        with Session(self._engine) as session:
            event = SecretAuditEvent(
                action=action,
                secret_id=secret_id,
                actor=actor,
            )
            session.add(event)
            session.commit()


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
        if not name or not isinstance(name, str):
            raise SecretValidationError(
                f"name must be a non-empty string; got {name!r}"
            )
        if not actor or not isinstance(actor, str):
            raise SecretValidationError(
                f"actor must be a non-empty string; got {actor!r}"
            )

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
        try:
            self._store.insert(row)
        except Exception as exc:
            # UNIQUE(name) collision surfaces as a SQLAlchemy
            # IntegrityError — translate to a structured
            # service error.
            raise SecretNameConflictError(
                f"secret name {name!r} already exists"
            ) from exc

        # Audit — stamped after the row commits (the
        # P1 path always links to an existing row).
        self._store.append_audit(
            action="create",
            secret_id=secret_id,
            actor=actor,
        )

        return {
            "id": secret_id,
            "name": name,
            "public_key": material.public_openssh,
            "fingerprint": material.fingerprint,
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
                be positive. Default 32.
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
        if not name or not isinstance(name, str):
            raise SecretValidationError(
                f"name must be a non-empty string; got {name!r}"
            )
        if not actor or not isinstance(actor, str):
            raise SecretValidationError(
                f"actor must be a non-empty string; got {actor!r}"
            )
        if not isinstance(length, int) or length <= 0:
            raise SecretValidationError(
                f"length must be a positive int; got {length!r}"
            )

        cipher = self._wrap_required()
        token_bytes = generate_api_token_material(length=length)
        wrapped = cipher.wrap(token_bytes)
        token_str = token_bytes.decode("ascii")
        # Preview = last 4 chars. The full token NEVER
        # crosses the tool boundary in v1.
        token_preview = token_str[-4:] if len(token_str) >= 4 else token_str

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
        try:
            self._store.insert(row)
        except Exception as exc:
            raise SecretNameConflictError(
                f"secret name {name!r} already exists"
            ) from exc

        self._store.append_audit(
            action="create",
            secret_id=secret_id,
            actor=actor,
        )

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
        if not name or not isinstance(name, str):
            raise SecretValidationError(
                f"name must be a non-empty string; got {name!r}"
            )
        if not actor or not isinstance(actor, str):
            raise SecretValidationError(
                f"actor must be a non-empty string; got {actor!r}"
            )
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
        try:
            self._store.insert(row)
        except Exception as exc:
            raise SecretNameConflictError(
                f"secret name {name!r} already exists"
            ) from exc

        self._store.append_audit(
            action="create",
            secret_id=secret_id,
            actor=actor,
        )

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
        if not actor or not isinstance(actor, str):
            raise SecretValidationError(
                f"actor must be a non-empty string; got {actor!r}"
            )
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
        if not id or not isinstance(id, str):
            raise SecretValidationError(
                f"id must be a non-empty string; got {id!r}"
            )
        if not actor or not isinstance(actor, str):
            raise SecretValidationError(
                f"actor must be a non-empty string; got {actor!r}"
            )
        row = self._store.get_by_id(id)
        if row is None:
            raise SecretNotFoundError(
                f"secret id {id!r} not found"
            )
        # Audit — stamped AFTER the read so the row is
        # present in the log even on a not-found. Not-found
        # does NOT audit (the row lookup failed first; the
        # v1 contract audits successful reads only — the
        # P2 marker-resolver audit will follow the same
        # pattern).
        self._store.append_audit(
            action="read_public",
            secret_id=id,
            actor=actor,
        )
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
        if not id or not isinstance(id, str):
            raise SecretValidationError(
                f"id must be a non-empty string; got {id!r}"
            )
        if not actor or not isinstance(actor, str):
            raise SecretValidationError(
                f"actor must be a non-empty string; got {actor!r}"
            )
        now_iso = self._now_iso()
        row = self._store.mark_retired(id, now_iso)
        if row is None:
            raise SecretNotFoundError(
                f"secret id {id!r} not found"
            )
        self._store.append_audit(
            action="retire",
            secret_id=id,
            actor=actor,
        )
        return {
            "id": id,
            "retired_at": now_iso,
        }


# ─────────────────────────────────────────────────────────────────────
# Service-layer error types
# ─────────────────────────────────────────────────────────────────────
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
