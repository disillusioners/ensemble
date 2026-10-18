"""``ViewPasscodeService`` — Phase 1 of live-view-passcode (plan f11965039).

Module size rationale (M7 tidier convention, 2026-10-17):
this module is ``concentrated by design``. It owns the
in-process :class:`ViewPasscodeService` + the colocated
:class:`ViewPasscodeCipher` (Fernet wrapper + the domain-
separated HMAC key derivation) + the FROZEN passcode primitives
(:func:`generate_passcode`, :func:`normalize_entry`,
:func:`codes_match`, :func:`compute_ip_hmac`). Splitting the
cipher out (e.g. into ``_view_passcode_cipher.py``) would re-
fragment the ``SYSTEM_ENCRYPTION_KEY`` rotation-coupling seam
(plan §3 D5 — the cipher is the explicit anchor for the rotation
coupling note). Splitting the primitives out would re-fragment
the FROZEN passcode alphabet (ADR D4 — the alphabet is the load-
bearing pin for the 29.7-bit entropy claim).

Phase 1 ships the durable Fernet-encrypted passcode substrate +
the in-process service. The HTTP surface (the public lane, the
interstitial, the verify POST, the grant cookie) is P2
(``.agents/shared/planning/live-view-passcode/phase2-plan.md``).
Phase 3 ships the deployment note (two-state canonical gate).
Phase 4 ships the security review pack. Phase 5 ships the merge
+ stage RC.

This module exposes:

* :class:`ViewPasscodeCipher` — the colocated Fernet + IP-HMAC
  helper that reads ``SYSTEM_ENCRYPTION_KEY`` at construction
  time. Construction RAISES on missing/invalid key (the binding
  spec's "no lazy half-initialized failures" guidance — the
  agent-secrets P1 ``SecretCipher`` is lazy because that service
  is wired unconditionally at :class:`InstanceManager.__init__`
  time and a missing key must not brick boot; this service is
  invoked from the P2 verify POST lane where a missing cipher
  would surface as a 500 on every request, so fail-fast at
  construction is the right posture).
* :class:`ViewPasscodeService` — the in-process service that
  owns the cipher + the SQLAlchemy-backed store. Public methods
  are the P1 surface consumed by the P2 route family
  (``daemon/routers/live_views.py``) and the P2 ``view_link``
  tool (``daemon/tools/live_views.py``). The service is engine-
  bound (a single ``ViewPasscodeService`` instance per process;
  the manager is the wiring site, mirroring the
  ``SecretService`` / ``SkillStoreService`` /
  ``PromptVersionService`` pattern at
  ``daemon/manager.py:1185-1195, 1241-1255``).
* :func:`generate_passcode` / :func:`normalize_entry` /
  :func:`codes_match` / :func:`compute_ip_hmac` — the FROZEN
  primitives the service composes (ADR D4 alphabet + ADR D9
  IP-HMAC shape).

Rotation coupling (plan §3 D5 + §7.1, user Q1 ruling 2026-10-17,
architecture-recommendation.md D4:59 + D9:117):

  The Fernet key is ``SYSTEM_ENCRYPTION_KEY`` — the same env the
  existing ``daemon/sources/credentials.py`` +
  ``daemon/services/kms_lite.py`` paths consume. Rotation
  coupling: a future ``SYSTEM_ENCRYPTION_KEY`` rotation MUST
  re-encrypt ``live_view_passcodes.wrapped_passcode`` AND
  ``secrets.wrapped_secret`` AND the ``db_connections`` Fernet
  credentials AND invalidate ALL outstanding grant cookies
  (the P2 stateless cookie + ADR D3 revocation-via-generation
  makes the cookie invalidation automatic on the next mint —
  the generation bump kills the cookie even if the new key
  permits a fresh ciphertext). Every one of those registries
  breaks otherwise. The coupling is documented in code at the
  cipher seam below — see the :class:`ViewPasscodeCipher`
  constructor for the explicit rotation-coupling note.

  The keyed-HMAC IP domain-separation key
  (``SHA256(SYSTEM_ENCRYPTION_KEY || "live-view-audit-ip-v1")``)
  is derived once at construction time. Rotation changes the
  derived key → historical ``ip_hmac`` rows remain but cross-
  period IP correlation breaks by design (raw IPs are NEVER
  stored; re-computation is impossible). The rotation ceremony
  note in ``.agents/shared/planning/agent-secrets/`` extends
  to cover the live-view walk; a single maintenance window
  walks all four registries.

Trust model
-----------

The trust ruling (plan §3 D5 + architecture-recommendation.md
V1) is unconditional for the privileged ``mint`` / ``refresh``
paths: agent-driven calls reach the service through the
``view-views`` tool category (``daemon/tools/live_views.py``)
whose privilege is the existing allow-list (ari / leader /
designer). The P2 verify POST on the unauthenticated lane has
NO privilege — the service is invoked directly from the router,
not through a tool. That lane consumes only the constant-time
``verify`` + ``record_failure`` methods; the ``mint`` /
``refresh`` methods are privileged-category-gated by the route
surface (P2 task T2.8 — out of P1 scope).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Mapping, Optional, Protocol

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import case
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, select

from daemon.repositories.view_passcodes.models import (
    LIVE_VIEW_AUDIT_ACTIONS,
    LIVE_VIEW_AUDIT_IP_KDF_INFO,
    LIVE_VIEW_PASSCODE_ALPHABET,
    LIVE_VIEW_PASSCODE_LENGTH,
    LIVE_VIEW_PASSCODE_STRIKE_CEILING,
    LiveViewPasscode,
    LiveViewPasscodeAudit,
)


# ─────────────────────────────────────────────────────────────────────
# Verify outcome enum
# ─────────────────────────────────────────────────────────────────────


class VerifyOutcome(str, Enum):
    """The result of a constant-time compare on a passcode row.

    The P2 verify POST lane maps each outcome to an HTTP response
    (per architecture-recommendation.md D2 + D5):
    ``OK`` → issue grant cookie + serve the view;
    ``WRONG`` → record_failure + uniform 404-or-retry on the
    interstitial (no remaining-attempts disclosure — ADR D2
    "no remaining-attempt disclosure pre-lock");
    ``LOCKED`` → render the locked-notice page (HTTP 200, "ask
    for a fresh link" — ADR V3 / D2 architect sign-off).

    String enum so the value is JSON-serializable for the P2
    verify endpoint response envelope.
    """

    OK = "ok"
    WRONG = "wrong"
    LOCKED = "locked"


# ─────────────────────────────────────────────────────────────────────
# Encryption + IP-HMAC seam (architecture-recommendation.md D4 + D9)
# ─────────────────────────────────────────────────────────────────────


class ViewPasscodeCipherUnavailable(RuntimeError):
    """Raised when :class:`ViewPasscodeCipher` cannot construct.

    Distinct from :class:`cryptography.fernet.InvalidToken`
    (which is the malformed-ciphertext case). The P2 router
    catches both and returns a structured error envelope — the
    caller never sees a raw exception.
    """


class ViewPasscodeRotationRequired(RuntimeError):
    """Raised when a stored ciphertext cannot be decrypted — the
    canonical rotation-fail primitive (architecture-recommendation.md
    D4:59 + D9:117).

    Two triggers:
      1. ``SYSTEM_ENCRYPTION_KEY`` was rotated without re-encrypting
         the ``live_view_passcodes.wrapped_passcode`` column — the
         decrypt step raises ``InvalidToken`` and the service
         translates to this typed error.
      2. A ciphertext was tampered with — the Fernet MAC fails.
    Both trigger the same P2 UX: render "regeneration required"
    on the locked-or-interstitial page (NEVER a 500, NEVER
    plaintext). The Phase 4 review-pack W1 pin tests this
    primitive at the integration level; the unit tests pin it
    here at the cipher boundary.
    """


class ViewPasscodeCipher:
    """Fernet wrapper + domain-separated HMAC key for IP hashing.

    Colocated under the same package as :class:`ViewPasscodeService`
    per the secret-service precedent (the cipher is the explicit
    anchor for the rotation-coupling note; splitting it would re-
    fragment the seam).

    Construction posture (BINDING — differs from SecretCipher):

      **Raised on missing/invalid key.** The binding spec mandates
      "no lazy half-initialized failures" (the P1 commission brief;
      architecture-recommendation.md D4:59 implies fail-closed on
      the cipher). The agent-secrets P1 ``SecretCipher`` is lazy
      because that service is wired at :class:`InstanceManager
      .__init__` unconditionally and a missing key must not brick
      boot (manager no-key tolerance — ``daemon/manager.py:1268``).
      This service is invoked from the P2 verify POST lane where
      a missing cipher would surface as a 500 on every request,
      so fail-fast at construction is the right posture.

    Attributes:
        fernet: The constructed :class:`Fernet` instance.
            ALWAYS present after construction (no lazy-init).
        ip_hmac_key: The domain-separated HMAC key for the audit
            ``ip_hmac`` field (ADR D9:116). Derived once at
            construction: ``SHA256(SYSTEM_ENCRYPTION_KEY ||
            "live-view-audit-ip-v1")``. Domain-separated from
            the Fernet key — rotation changes this derived key
            and unlinks historical IP correlation by design
            (raw IPs are NEVER stored).
    """

    #: The Fernet-key env var name (same as
    #: :class:`daemon.services.secret_service.SecretCipher.KEY_ENV`
    #: but redeclared here so the dependency is unidirectional —
    #: this service does NOT import the secrets layer).
    KEY_ENV: str = "SYSTEM_ENCRYPTION_KEY"

    def __init__(self, encryption_key: bytes | str | None = None) -> None:
        key = encryption_key
        if key is None:
            key = os.environ.get(self.KEY_ENV)
        if not key:
            # Fail-CLOSED with a typed error — the binding
            # spec's "no lazy half-initialized failures"
            # guidance. The P2 router will surface this as a
            # structured error envelope (NEVER a 500).
            raise ViewPasscodeCipherUnavailable(
                f"ViewPasscodeCipher unavailable: {self.KEY_ENV} "
                f"is not set. The live-view passcode substrate "
                f"is unavailable until the env var is set."
            )
        if isinstance(key, str):
            key = key.encode("utf-8")
        try:
            self._fernet = Fernet(key)
        except (ValueError, TypeError) as exc:
            # Fernet raises ValueError on a malformed key
            # (e.g. wrong length / invalid base64) and
            # TypeError on a non-bytes input. Other
            # exception types propagate so the caller can
            # surface the real diagnostic.
            raise ViewPasscodeCipherUnavailable(
                f"ViewPasscodeCipher unavailable: {self.KEY_ENV} "
                f"is invalid ({exc!r})."
            ) from exc
        # Domain-separated HMAC key for the audit ``ip_hmac``
        # field. NEVER reuse the Fernet key as the HMAC key
        # (the ADR D9:116 ruling — the Fernet key has
        # application-specific construction; domain-separation
        # is the cheap correctness guard). The domain string
        # is ``LIVE_VIEW_AUDIT_IP_KDF_INFO`` (the canonical
        # source of truth; matches the migration comment +
        # the model docstring + this seam).
        self._ip_hmac_key: bytes = hashlib.sha256(
            key + LIVE_VIEW_AUDIT_IP_KDF_INFO.encode("utf-8")
        ).digest()

    @property
    def fernet(self) -> Fernet:
        """The constructed Fernet instance (always present)."""
        return self._fernet

    @property
    def ip_hmac_key(self) -> bytes:
        """The domain-separated HMAC key for audit IP hashing."""
        return self._ip_hmac_key

    def wrap(self, plaintext: bytes) -> bytes:
        """Encrypt plaintext → Fernet ciphertext (bytes).

        Args:
            plaintext: The bytes to encrypt.

        Returns:
            The Fernet ciphertext (URL-safe base64 + HMAC + IV).
        """
        return self._fernet.encrypt(plaintext)

    def unwrap(self, ciphertext: bytes) -> bytes:
        """Decrypt Fernet ciphertext → plaintext (bytes).

        Args:
            ciphertext: The Fernet ciphertext (bytes).

        Returns:
            The decrypted plaintext.

        Raises:
            ViewPasscodeRotationRequired: When the ciphertext
                cannot be decrypted (rotation-fail primitive,
                architecture-recommendation.md D4:59). The P2
                router maps this to a "regeneration required"
                notice — NEVER a 500, NEVER plaintext.
        """
        try:
            return self._fernet.decrypt(ciphertext)
        except InvalidToken as exc:
            # The canonical rotation-fail primitive. Two
            # triggers: (1) SYSTEM_ENCRYPTION_KEY was rotated
            # without re-encrypting wrapped_passcode; (2) the
            # ciphertext was tampered with. Both map to the
            # same P2 UX.
            raise ViewPasscodeRotationRequired(
                "Stored passcode ciphertext cannot be decrypted. "
                "The SYSTEM_ENCRYPTION_KEY may have been rotated "
                "without re-encrypting the live_view_passcodes "
                "table, or the stored ciphertext was tampered "
                "with. The owner must call view_link(refresh=True) "
                "to regenerate the passcode."
            ) from exc


# ─────────────────────────────────────────────────────────────────────
# Passcode primitives (ADR D4 — FROZEN alphabet / shape)
# ─────────────────────────────────────────────────────────────────────


def generate_passcode() -> str:
    """Generate a fresh 6-char passcode from the FROZEN D4 alphabet.

    Uses :func:`secrets.choice` (stdlib CSPRNG, the FROZEN RNG).
    Never :mod:`random` — the cryptographic posture is part of
    the v1 contract (a non-CSPRNG passcode defeats the
    brute-force-resistant 29.7-bit entropy claim).

    Returns:
        A 6-char passcode string from the alphabet
        ``23456789abcdefghjkmnpqrstuvwxyz`` (31 symbols, drops
        0/O/1/i/l). 31⁶ ≈ 8.9e8 ≈ 29.7 bits of entropy —
        acceptable under the bounded-online-attack model (10
        durable strikes + per-IP token bucket ⇒ online success
        ≤ 10/31⁶ ≈ 1.1×10⁻⁸ per view per generation).
    """
    return "".join(
        secrets.choice(LIVE_VIEW_PASSCODE_ALPHABET)
        for _ in range(LIVE_VIEW_PASSCODE_LENGTH)
    )


def normalize_entry(raw: str) -> str:
    """Normalize customer-entered code for constant-time compare.

    Strips leading/trailing whitespace + casefolds. The
    alphabet is lowercase-only; customer entry can be upper or
    lower (a phone keyboard defaults to one, a desktop the
    other). The compare path uses :func:`codes_match` which
    casefolds again — the double-casefold is idempotent.

    Args:
        raw: The customer-entered passcode string.

    Returns:
        The normalized form (whitespace-stripped + casefolded).
    """
    return raw.strip().casefold()


def codes_match(presented: str, actual: str) -> bool:
    """Constant-time compare two passcode strings.

    The :func:`hmac.compare_digest` primitive guarantees the
    compare time depends on the inputs' LENGTH, not on the
    number of leading-matching characters. Both inputs are
    UTF-8-encoded bytes BEFORE the compare (casefolding is
    applied first so the customer can type upper or lower) —
    the byte-level compare accepts arbitrary bytes, so
    non-ASCII customer input (e.g. accented characters
    accidentally typed via a phone IME) cannot raise a
    :class:`UnicodeEncodeError` mid-compare (the ASCII-only
    ``str`` form of ``compare_digest`` would surface ``TypeError``
    on non-ASCII bytes — the public verify POST will receive
    arbitrary UTF-8 in P2).

    Args:
        presented: The customer-entered passcode string.
        actual: The stored passcode plaintext (decrypted from
            ``wrapped_passcode``).

    Returns:
        ``True`` iff the two strings are equal after
        normalization.
    """
    return hmac.compare_digest(
        normalize_entry(presented).encode("utf-8"),
        normalize_entry(actual).encode("utf-8"),
    )


def compute_ip_hmac(cipher: ViewPasscodeCipher, ip: str) -> str:
    """Compute the 16-hex-char IP-HMAC for the audit ``ip_hmac`` column.

    Algorithm (ADR D9:116):
      ``ip_hmac = HMAC-SHA256(ip, cipher.ip_hmac_key)[:16 hex]``

    The domain-separated key is derived from
    ``SYSTEM_ENCRYPTION_KEY`` at :class:`ViewPasscodeCipher`
    construction time. Rotation changes the derived key →
    cross-period IP correlation breaks by design (raw IPs are
    NEVER stored; re-computation is impossible).

    Args:
        cipher: The cipher whose domain-separated HMAC key
            provides the HMAC key.
        ip: The request IP string (IPv4 or IPv6 form).

    Returns:
        The 16-hex-char (64-bit) HMAC tag.
    """
    digest = hmac.new(
        cipher.ip_hmac_key,
        ip.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return digest[:8].hex()  # 8 bytes = 16 hex chars


# ─────────────────────────────────────────────────────────────────────
# MintResult dataclass — the service boundary return shape for
# mint / refresh.
# ─────────────────────────────────────────────────────────────────────


@dataclass
class MintResult:
    """The service boundary return shape for mint / refresh.

    Attributes:
        passcode: The plaintext passcode. PLAINTEXT (D6
            deviation, forced by requirement 2 — "view_link
            ALWAYS includes the passcode"). The plaintext is
            held only in memory for the duration of the
            function call; the caller (the P2 ``view_link``
            tool) embeds it in the tool-result string. After
            the function returns, the caller MUST NOT persist
            the plaintext; the canonical durable form is the
            Fernet ciphertext on the row.
        generation: The row's generation AFTER the call.
            Increments by 1 on ``refresh`` (silently revokes
            outstanding grant cookies).
        created: ``True`` iff the call INSERTed a new row.
            ``False`` iff the call hit an existing row (the
            idempotent re-display path; ADR D5 sharpening —
            does NOT clear an active lock).
        locked_status: The row's ``locked`` flag AFTER the
            call. ``True`` if the view is currently locked
            (10+ wrong verifies); the P2 ``view_link`` tool
            surfaces the "use refresh=True" hint in this case.
    """

    passcode: str
    generation: int
    created: bool
    locked_status: bool


# ─────────────────────────────────────────────────────────────────────
# Storage port — thin protocol + SQLAlchemy backend. Mirrors the
# ``_SecretStorePort`` pattern at ``daemon/services/secret_service.py``
# (port-shaped interface, NOT a full repository abstraction).
# ─────────────────────────────────────────────────────────────────────


class _ViewPasscodeStorePort(Protocol):
    """The storage seam the :class:`ViewPasscodeService` composes.

    The interface is the future door for an alternative store
    (extracted into a unit of its own by the future port-
    wrapping work, or replaced by a resource-only plugin
    snapshot — per the plugin direction, but explicitly OUT
    of P1 scope per the binding constraints).

    The M1 atomicity contract (the secrets precedent +
    governor pre-promote fix 2026-10-17) requires every
    mutation + audit-row INSERT to commit in ONE
    ``engine.begin()`` transaction — a crash between the two
    commits is the regression the fix closes. The interface
    carries that contract in its method names
    (``*_with_audit``); the in-memory separation between
    "read" and "mutate" is intentional, so a future remote-
    store port (Vault / OpenBao / KMS) MUST replicate the
    atomic audit emission.
    """

    def get_row(self, root_name: str, rel_path: str) -> Optional[LiveViewPasscode]:
        ...

    def insert_with_audit(
        self,
        row: LiveViewPasscode,
        *,
        actor: Optional[str],
    ) -> LiveViewPasscode:
        ...

    def refresh_with_audit(
        self,
        root_name: str,
        rel_path: str,
        new_wrapped_passcode: bytes,
        new_generation: int,
        *,
        actor: Optional[str],
    ) -> Optional[LiveViewPasscode]:
        ...

    def record_failure_with_audit(
        self,
        root_name: str,
        rel_path: str,
        *,
        actor: Optional[str],
        ip_hmac: Optional[str],
    ) -> tuple[int, bool]:
        ...

    def append_audit(
        self,
        *,
        root_name: str,
        rel_path: str,
        generation: int,
        action: str,
        actor: Optional[str],
        ip_hmac: Optional[str],
    ) -> None:
        ...


def _append_audit_in_conn(
    conn: Any,
    *,
    action: str,
    root_name: str,
    rel_path: str,
    generation: int,
    actor: Optional[str],
    ip_hmac: Optional[str],
) -> None:
    """Insert one :class:`LiveViewPasscodeAudit` row inside the
    caller's ``engine.begin()`` transaction.

    Mirrors the
    ``daemon/repositories/prompt_version/repository.py``
    ``_append_audit_in_conn`` helper (the named precedent —
    same shape, same purpose, same M1 atomicity guarantee).

    Action validation: ``LIVE_VIEW_AUDIT_ACTIONS`` (the
    canonical closed set; per ADR D9:115). The caller MUST
    have checked the action lives in the set before invoking
    the helper — the M1 callers do. A future caller that
    bypasses the guard gets an immediate ``ValueError`` at the
    audit-insert site rather than a later IntegrityError.
    """
    if action not in LIVE_VIEW_AUDIT_ACTIONS:
        raise ValueError(
            f"_append_audit_in_conn: action {action!r} not in "
            f"LIVE_VIEW_AUDIT_ACTIONS"
        )
    conn.execute(
        LiveViewPasscodeAudit.__table__.insert().values(
            action=action,
            root_name=root_name,
            rel_path=rel_path,
            generation=generation,
            actor=actor,
            ip_hmac=ip_hmac,
            at=datetime.now(timezone.utc).isoformat(),
        )
    )


def _row_to_passcode(row: Any) -> LiveViewPasscode:
    """Map a Core ``select(LiveViewPasscode)`` row to a
    :class:`LiveViewPasscode` ORM dataclass.

    Uses ``row._mapping`` (the SQLAlchemy ``RowMapping``
    view) to bind by column name — kills the column-order
    fragility that positional ``row[0]..row[N]`` carries
    when the SQLModel column order changes. The
    ``SecretAuditEvent`` precedent at
    ``daemon/repositories/prompt_version/repository.py`` uses
    the same ``_mapping`` view for the same robustness
    reason.
    """
    m = row._mapping
    return LiveViewPasscode(
        id=m["id"],
        root_name=m["root_name"],
        rel_path=m["rel_path"],
        wrapped_passcode=m["wrapped_passcode"],
        generation=m["generation"],
        failed_attempts=m["failed_attempts"],
        locked=bool(m["locked"]),
        created_at=m["created_at"],
        last_attempt_at=m["last_attempt_at"],
    )


class _SqlViewPasscodeStore:
    """SQLAlchemy-backed implementation of the storage seam.

    Mirrors the ``_SqlSecretStore`` pattern at
    ``daemon/services/secret_service.py``. Every mutation
    goes through ``engine.begin()`` so the mutation + the
    audit row commit atomically (M1 atomicity contract).
    """

    def __init__(
        self, engine: Any, *, strike_ceiling: int,
    ) -> None:
        self._engine = engine
        # C4 wiring (D14): honor the operator-configured
        # ceiling from ``ViewPasscodeService`` (which itself
        # receives it from
        # ``config.live_views.passcode_lockout_threshold`` via
        # ``daemon/api.py``). Stored locally so the atomic
        # UPDATE in ``record_failure_with_audit`` uses the
        # CONFIGURED value rather than the module-level spec
        # constant — re-binding a class-level alias inside
        # ``__init__`` would race with shared default-arg
        # reads.
        if not isinstance(strike_ceiling, int) or strike_ceiling < 1:
            raise ValueError(
                f"_SqlViewPasscodeStore.strike_ceiling must be "
                f"a positive int; got {strike_ceiling!r}"
            )
        self._strike_ceiling = strike_ceiling

    def get_row(
        self, root_name: str, rel_path: str
    ) -> Optional[LiveViewPasscode]:
        """Read one passcode row by the UNIQUE key."""
        with Session(self._engine) as session:
            stmt = select(LiveViewPasscode).where(
                LiveViewPasscode.root_name == root_name,
                LiveViewPasscode.rel_path == rel_path,
            )
            row = session.exec(stmt).first()
            if row is None:
                return None
            return row

    # ─────────────────────────────────────────────────────
    # M1 atomic variants — mutation + audit row inside ONE
    # ``engine.begin()`` transaction (mirrors the
    # ``_SqlSecretStore`` M1 idiom).
    # ─────────────────────────────────────────────────────

    def insert_with_audit(
        self,
        row: LiveViewPasscode,
        *,
        actor: Optional[str],
    ) -> LiveViewPasscode:
        """INSERT the passcode row + the ``mint`` audit row
        in one ``engine.begin()`` transaction.

        On UNIQUE(root_name, rel_path) collision this raises
        ``sqlalchemy.exc.IntegrityError`` — the service
        translates to a re-read (the row-gating idempotency
        posture; ADR D5 sharpening: does NOT clear an active
        lock). Any other failure rolls back BOTH writes so a
        crash between the mutation and the audit row cannot
        leave the registry in an inconsistent state (M1
        atomicity contract).
        """
        with self._engine.begin() as conn:
            conn.execute(
                LiveViewPasscode.__table__.insert().values(
                    id=row.id,
                    root_name=row.root_name,
                    rel_path=row.rel_path,
                    wrapped_passcode=row.wrapped_passcode,
                    generation=row.generation,
                    failed_attempts=row.failed_attempts,
                    locked=int(row.locked),
                    created_at=row.created_at,
                    last_attempt_at=row.last_attempt_at,
                )
            )
            _append_audit_in_conn(
                conn,
                action="mint",
                root_name=row.root_name,
                rel_path=row.rel_path,
                generation=row.generation,
                actor=actor,
                ip_hmac=None,
            )
            read_back = conn.execute(
                select(LiveViewPasscode).where(
                    LiveViewPasscode.id == row.id
                )
            ).first()
            if read_back is None:
                # Should be unreachable — we just inserted.
                raise RuntimeError(
                    "insert_with_audit: row not visible after "
                    "INSERT — internal invariant violated"
                )
            return _row_to_passcode(read_back)

    def refresh_with_audit(
        self,
        root_name: str,
        rel_path: str,
        new_wrapped_passcode: bytes,
        new_generation: int,
        *,
        actor: Optional[str],
    ) -> Optional[LiveViewPasscode]:
        """UPDATE passcode + INSERT the ``regenerate`` audit row
        in one ``engine.begin()`` transaction.

        The UPDATE bumps ``generation``, resets ``failed_attempts``
        to 0, clears ``locked`` to 0 — and silently revokes ALL
        outstanding grant cookies (the generation bump kills
        them; ADR D3 revocation-via-generation). Returns the
        updated row, or ``None`` if the (root_name, rel_path)
        pair is unknown (NOT-found does NOT audit; the
        caller decides whether to insert a fresh row first or
        raise a structured error).
        """
        with self._engine.begin() as conn:
            existing = conn.execute(
                select(LiveViewPasscode).where(
                    LiveViewPasscode.root_name == root_name,
                    LiveViewPasscode.rel_path == rel_path,
                )
            ).first()
            if existing is None:
                return None
            conn.execute(
                LiveViewPasscode.__table__.update()
                .where(
                    LiveViewPasscode.root_name == root_name,
                    LiveViewPasscode.rel_path == rel_path,
                )
                .values(
                    wrapped_passcode=new_wrapped_passcode,
                    generation=new_generation,
                    failed_attempts=0,
                    locked=0,
                    last_attempt_at=None,
                )
            )
            _append_audit_in_conn(
                conn,
                action="regenerate",
                root_name=root_name,
                rel_path=rel_path,
                generation=new_generation,
                actor=actor,
                ip_hmac=None,
            )
            read_back = conn.execute(
                select(LiveViewPasscode).where(
                    LiveViewPasscode.root_name == root_name,
                    LiveViewPasscode.rel_path == rel_path,
                )
            ).first()
            assert read_back is not None
            return _row_to_passcode(read_back)

    def record_failure_with_audit(
        self,
        root_name: str,
        rel_path: str,
        *,
        actor: Optional[str],
        ip_hmac: Optional[str],
    ) -> tuple[int, bool]:
        """Atomic ``failed_attempts += 1`` UPDATE. The
        ``LIVE_VIEW_PASSCODE_STRIKE_CEILING``-th failure flips
        ``locked`` AND emits the ``lockout`` audit row IN THE
        SAME ``engine.begin()`` transaction (architecture-
        recommendation.md D5:65 — "atomic UPDATE... inside
        ``engine.begin()``; the 10th failure flips
        ``locked=true`` + emits the ``lockout`` audit row in
        the same transaction").

        The increment + lock flip happens in ONE atomic SQL
        statement (``UPDATE ... SET failed_attempts =
        failed_attempts + 1, locked = (failed_attempts + 1 >=
        :ceiling) WHERE root_name = :r AND rel_path = :p AND
        locked = 0``). The ``WHERE locked = 0`` guard ensures
        the lock flip + ``lockout`` audit row are emitted ONLY
        on the FIRST crossing — concurrent 9th/10th failures
        cannot both cross the ceiling. The read-then-write
        Python shape would race: two concurrent increments
        could both observe ``current_attempts == 9`` and both
        decide to emit a ``lockout`` audit row, plus one of
        the two increments would be lost.

        Returns ``(attempts, locked_now)``. ``attempts`` is the
        post-increment value (or the row's current value if
        already locked — the ``WHERE locked = 0`` guard skips
        the increment). ``locked_now`` is the row's ``locked``
        flag AFTER the call. Both are needed for the P2 verify
        response surface (the locked short-circuit consults
        ``locked_now``; the rate-limiter meter consults
        ``attempts``).

        Returns ``(-1, False)`` for an unknown
        ``(root_name, rel_path)`` pair — the verify POST on
        the unauthenticated lane maps every unknown path to
        uniform 404 (architecture-recommendation.md D2); the
        service caller MUST check the row exists BEFORE
        invoking this method, or accept the sentinel.
        """
        ceiling = self._strike_ceiling
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._engine.begin() as conn:
            # Existence probe — the atomic UPDATE below cannot
            # distinguish "row missing" from "row exists but
            # already locked" (both yield 0 rows-affected).
            existing = conn.execute(
                select(LiveViewPasscode).where(
                    LiveViewPasscode.root_name == root_name,
                    LiveViewPasscode.rel_path == rel_path,
                )
            ).first()
            if existing is None:
                # Sentinel — caller maps to uniform 404.
                return (-1, False)
            existing_mapping = existing._mapping
            already_locked = bool(existing_mapping["locked"])
            existing_generation = existing_mapping["generation"]

            # Atomic increment + conditional lock flip in ONE
            # SQL statement. The ``WHERE locked = 0`` guard
            # short-circuits on an already-locked row (the
            # post-commit locked check — defense-in-depth
            # against a caller that bypasses the service-layer
            # locked short-circuit). The ``CASE`` expression
            # evaluates ``(failed_attempts + 1) >= ceiling`` in
            # SQL so the lock flip happens in the same
            # statement as the increment — no Python read-
            # then-write race window. SQLAlchemy compiles the
            # CASE booleans to PG ``true``/``false`` and
            # SQLite ``1``/``0`` (driver-appropriate), so the
            # ``locked`` column stays BOOLEAN on PG and INTEGER
            # on SQLite.
            result = conn.execute(
                LiveViewPasscode.__table__.update()
                .where(
                    LiveViewPasscode.root_name == root_name,
                    LiveViewPasscode.rel_path == rel_path,
                    LiveViewPasscode.locked == False,  # noqa: E712
                )
                .values(
                    failed_attempts=(
                        LiveViewPasscode.failed_attempts + 1
                    ),
                    locked=case(
                        (
                            (
                                LiveViewPasscode.failed_attempts + 1
                            ) >= ceiling,
                            True,
                        ),
                        else_=False,
                    ),
                    last_attempt_at=now_iso,
                )
                .execution_options(
                    synchronize_session=False
                ),
            )
            rowcount = result.rowcount
            if rowcount == 0:
                # Either the row vanished mid-call (rare — the
                # caller mapped to 404 from the probe), or the
                # row was already locked. The probe above
                # confirmed existence, so this branch is the
                # ``already locked`` defense-in-depth case:
                # return the existing count + True.
                if already_locked:
                    return (
                        existing_mapping["failed_attempts"],
                        True,
                    )
                # Defensive: row vanished. Return the sentinel
                # so the caller maps to 404.
                return (-1, False)

            # Read the post-increment values back inside the
            # SAME transaction so we have the canonical
            # ``attempts`` + ``locked_now`` return values and
            # the ``generation`` to stamp on the audit row.
            updated = conn.execute(
                select(LiveViewPasscode).where(
                    LiveViewPasscode.root_name == root_name,
                    LiveViewPasscode.rel_path == rel_path,
                )
            ).first()
            assert updated is not None
            updated_mapping = updated._mapping
            new_attempts = updated_mapping["failed_attempts"]
            new_locked = bool(updated_mapping["locked"])

            if new_locked and not already_locked:
                # The strike-ceiling flip — emit the lockout
                # audit row IN THE SAME transaction (ADR D5:65).
                # The ``WHERE locked = 0`` guard above guarantees
                # this branch fires on AT MOST one concurrent
                # caller — the loser sees ``rowcount == 0`` and
                # returns ``(existing_count, True)`` without
                # emitting a duplicate audit row.
                _append_audit_in_conn(
                    conn,
                    action="lockout",
                    root_name=root_name,
                    rel_path=rel_path,
                    generation=existing_generation,
                    actor=actor,
                    ip_hmac=ip_hmac,
                )
            return (new_attempts, new_locked)

    def append_audit(
        self,
        *,
        root_name: str,
        rel_path: str,
        generation: int,
        action: str,
        actor: Optional[str],
        ip_hmac: Optional[str],
    ) -> None:
        """Append a single audit row (P2 grant / deny emitters).

        The P1 surface emits ``mint`` / ``regenerate`` /
        ``lockout`` via the atomic ``*_with_audit`` paths. P2
        adds ``grant`` (cookie issuance) + ``deny`` (verify
        short-circuit on wrong code) — those do NOT mutate the
        passcode row, only append an audit row. The single-
        audit helper exists so the P2 path does not have to
        open an ``engine.begin()`` block of its own.
        """
        with self._engine.begin() as conn:
            _append_audit_in_conn(
                conn,
                action=action,
                root_name=root_name,
                rel_path=rel_path,
                generation=generation,
                actor=actor,
                ip_hmac=ip_hmac,
            )


# ─────────────────────────────────────────────────────────────────────
# Service dataclass — the P1 service surface.
# ─────────────────────────────────────────────────────────────────────


class ViewPasscodeService:
    """The in-process live-view passcode service (P1 scope).

    Composes the cipher + the store + the passcode primitives.
    Public methods are the P1 service surface consumed by the
    P2 route family + ``view_link`` tool:

    * ``mint(root, rel, actor)`` — idempotent passcode creation.
      New rows write the ``mint`` audit row; existing rows
      return the current plaintext + locked status (does NOT
      clear an active lock — ADR D5 sharpening).
    * ``verify(view_row, presented)`` — constant-time compare.
      Returns ``VerifyOutcome.OK`` / ``.WRONG`` / ``.LOCKED``.
      Locked short-circuits with NO compare + NO increment.
    * ``record_failure(view_row, ip=None)`` — atomic
      ``failed_attempts += 1``. The 10th failure flips
      ``locked`` AND emits the ``lockout`` audit row IN THE
      SAME ``engine.begin()`` transaction.
    * ``refresh(root, rel, actor)`` — new passcode, generation+1,
      counters reset, audit ``regenerate``. The generation bump
      silently revokes ALL outstanding grant cookies (ADR D3).
    * ``get_row(root, rel)`` — read the durable row.
    * ``append_audit(root, rel, generation, action, actor, ip=None)``
      — single audit row (the P2 grant / deny emitter surface).

    Construction posture (BINDING):

      The :class:`ViewPasscodeCipher` raises
      :class:`ViewPasscodeCipherUnavailable` on missing/invalid
      key (the "no lazy half-initialized failures" guidance).
      The service therefore propagates that exception on
      construction — the caller (the P2 router) maps it to a
      structured error envelope. The service is NOT wired
      unconditionally at :class:`InstanceManager.__init__` (a
      future commission); the P2 wiring site decides whether
      to construct it at lifespan time (with the env var
      present) or at first-use time (fail-fast per request).
    """

    #: Strike ceiling — the Nth wrong verify flips ``locked``
    #: (ADR D5). Mirrors ``LIVE_VIEW_PASSCODE_STRIKE_CEILING``
    #: from the models module; the class-level alias makes
    #: ``self.STRIKE_CEILING`` resolvable from the service
    #: boundary. The module-level constant is the canonical
    #: source of truth.
    #: Strike ceiling — the Nth wrong verify flips ``locked``
    #: (ADR D5). The class-level alias mirrors the spec
    #: constant from ``daemon/repositories/view_passcodes/
    #: models.py`` (``LIVE_VIEW_PASSCODE_STRIKE_CEILING``) and
    #: is REPLACED at instance construction time when the
    #: caller passes ``strike_ceiling=...`` (the C4 wiring;
    #: ``daemon/api.py`` forwards
    #: ``config.live_views.passcode_lockout_threshold``).
    STRIKE_CEILING: int = LIVE_VIEW_PASSCODE_STRIKE_CEILING

    def __init__(
        self,
        engine: Any,
        cipher: ViewPasscodeCipher,
        *,
        strike_ceiling: int = LIVE_VIEW_PASSCODE_STRIKE_CEILING,
    ) -> None:
        self._engine = engine
        self._cipher = cipher
        # C4 wiring (D14): honor the operator-configured
        # ceiling when present, fall back to the spec default
        # for unconfigured callers (tests + the legacy
        # ``ViewPasscodeService(engine, cipher)`` shape). The
        # service-level instance attribute overrides the
        # class-level alias so ``self.STRIKE_CEILING``
        # resolves to the configured value at request time.
        if not isinstance(strike_ceiling, int) or strike_ceiling < 1:
            raise ValueError(
                f"ViewPasscodeService.strike_ceiling must be a "
                f"positive int; got {strike_ceiling!r}"
            )
        self.STRIKE_CEILING = strike_ceiling
        self._strike_ceiling = strike_ceiling
        self._store: _ViewPasscodeStorePort = _SqlViewPasscodeStore(
            engine, strike_ceiling=strike_ceiling,
        )

    @property
    def is_available(self) -> bool:
        """Whether the service is operational (cipher key was set)."""
        # The cipher raises on construction without a key, so
        # ``is_available`` is always ``True`` for a constructed
        # service. The property exists for surface parity with
        # SecretService (the same call pattern in the
        # manager's no-key boot-tolerance probe) and for future
        # use if a soft-fail mode is ever added.
        return True

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _assert_root_name(value: object) -> str:
        """Validate ``root_name`` parameter.

        Isinstance-first ordering (the SecretService tidier pass
        #2 convention): wrong-type → ``TypeError``; empty
        string → :class:`ViewPasscodeValidationError`.
        """
        if not isinstance(value, str):
            raise TypeError(
                f"root_name must be a string; got "
                f"{type(value).__name__}"
            )
        if not value:
            raise ViewPasscodeValidationError(
                "root_name must be a non-empty string"
            )
        return value

    @staticmethod
    def _assert_rel_path(value: object) -> str:
        """Validate ``rel_path`` parameter.

        Isinstance-first ordering (mirror of
        :meth:`_assert_root_name`): wrong-type →
        ``TypeError``; empty string →
        :class:`ViewPasscodeValidationError`.
        """
        if not isinstance(value, str):
            raise TypeError(
                f"rel_path must be a string; got "
                f"{type(value).__name__}"
            )
        if not value:
            raise ViewPasscodeValidationError(
                "rel_path must be a non-empty string"
            )
        return value

    @staticmethod
    def _assert_actor(value: object) -> Optional[str]:
        """Validate ``actor`` parameter (Cardinal #2).

        Optional for the P2 verify POST (the public lane has
        no agent provenance); REQUIRED for the privileged
        ``mint`` / ``refresh`` paths (the calling agent is
        Cardinal #2 source discipline).

        Isinstance-first ordering (mirror of
        :meth:`_assert_root_name`): wrong-type →
        ``TypeError``; empty string →
        :class:`ViewPasscodeValidationError`.
        """
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError(
                f"actor must be a string or None; got "
                f"{type(value).__name__}"
            )
        if not value:
            raise ViewPasscodeValidationError(
                "actor must be a non-empty string or None"
            )
        return value

    # ─────────────────────────────────────────────────────────
    # mint — idempotent
    # ─────────────────────────────────────────────────────────

    def mint(
        self, root: str, rel: str, actor: Optional[str]
    ) -> MintResult:
        """Mint (or re-display) the passcode for ``(root, rel)``.

        Idempotent (ADR D5 sharpening): a re-call on an
        existing row returns the current plaintext +
        generation + locked_status + ``created=False``,
        WITHOUT clearing the lock or generating a new code.

        Args:
            root: The view root name (validated).
            rel: The view path relative to ``root`` (validated).
            actor: The calling agent id (Cardinal #2); NULL
                is permitted but the privileged ``view_link``
                tool always carries the calling agent.

        Returns:
            :class:`MintResult` with the plaintext passcode,
            the row's generation, ``created`` flag, and the
            row's ``locked`` status.

        Raises:
            ViewPasscodeRotationRequired: When the
            stored ciphertext cannot be decrypted (the rotation-
            fail primitive, architecture-recommendation.md
            D4:59). The P2 tool maps this to a structured
            "regeneration required" envelope.
        """
        root = self._assert_root_name(root)
        rel = self._assert_rel_path(rel)
        actor = self._assert_actor(actor)

        existing = self._store.get_row(root, rel)
        if existing is not None:
            # Idempotent re-display path. Does NOT clear the
            # lock; the caller decides whether to invoke
            # ``refresh`` (ADR D5 sharpening).
            plaintext = self._cipher.unwrap(
                existing.wrapped_passcode
            ).decode("ascii")
            return MintResult(
                passcode=plaintext,
                generation=existing.generation,
                created=False,
                locked_status=existing.locked,
            )

        # First-mint path: generate a fresh passcode, wrap,
        # insert row + ``mint`` audit row atomically. A
        # concurrent caller can race this path and INSERT the
        # same ``(root_name, rel_path)`` row first — the UNIQUE
        # constraint surfaces as ``IntegrityError`` (the
        # ``secrets_service.insert_with_audit`` precedent). We
        # translate that collision to the SAME idempotent
        # re-display result the existing-row branch above
        # returns: re-read the winner's row + return the
        # winning plaintext + ``created=False``. The audit-row
        # that the winner emitted is preserved (the Integrity
        # Error rolls back our own INSERT + audit pair, not the
        # winner's).
        plaintext = generate_passcode()
        wrapped = self._cipher.wrap(plaintext.encode("ascii"))
        now_iso = self._now_iso()
        row = LiveViewPasscode(
            id=str(uuid.uuid4()),
            root_name=root,
            rel_path=rel,
            wrapped_passcode=wrapped,
            generation=1,
            failed_attempts=0,
            locked=False,
            created_at=now_iso,
            last_attempt_at=None,
        )
        try:
            stored = self._store.insert_with_audit(row, actor=actor)
        except IntegrityError as exc:
            # UNIQUE(root_name, rel_path) collision — re-read
            # the winner's row + return the idempotent
            # re-display shape. The winner's mint audit row
            # stays intact (rolled back = our pair only).
            del wrapped  # winner is the durable carrier; drop ours
            winner = self._store.get_row(root, rel)
            if winner is None:
                # Defensive: the row vanished between the
                # INSERT-collision and the re-read. The UNIQUE
                # contract guarantees a row exists; this branch
                # is unreachable in practice but a typed raise
                # beats silent corruption.
                raise RuntimeError(
                    f"mint: UNIQUE collision on (root={root!r}, "
                    f"rel={rel!r}) but re-read returned no row"
                ) from exc
            winner_plaintext = self._cipher.unwrap(
                winner.wrapped_passcode
            ).decode("ascii")
            return MintResult(
                passcode=winner_plaintext,
                generation=winner.generation,
                created=False,
                locked_status=winner.locked,
            )
        del wrapped  # defense-in-depth; the only ref is in the row
        return MintResult(
            passcode=plaintext,
            generation=stored.generation,
            created=True,
            locked_status=stored.locked,
        )

    # ─────────────────────────────────────────────────────────
    # verify — constant-time compare
    # ─────────────────────────────────────────────────────────

    def verify(
        self, view_row: LiveViewPasscode, presented: str
    ) -> VerifyOutcome:
        """Constant-time compare ``presented`` against the
        decrypted passcode of ``view_row``.

        Locked short-circuits with NO compare + NO increment
        (ADR D5 sharpening). The ``record_failure`` method
        owns the increment path; this method NEVER touches the
        database.

        Args:
            view_row: The durable passcode row (the caller
                fetched via :meth:`get_row`).
            presented: The customer-entered passcode string.
                MUST be a ``str``; non-str input (None, int,
                bytes, ...) raises :class:`TypeError` — the P2
                router translates to a structured 400 envelope.
                The compare itself never raises on arbitrary
                UTF-8 because the bytes form of
                ``hmac.compare_digest`` accepts non-ASCII bytes
                (the public verify POST will receive arbitrary
                customer input; the typed guard here closes
                the None-AttributeError hole the bytes form
                alone does not).

        Returns:
            :class:`VerifyOutcome.OK` if the codes match,
            :class:`VerifyOutcome.WRONG` if they do not,
            :class:`VerifyOutcome.LOCKED` if the row is
            currently locked.

        Raises:
            TypeError: When ``presented`` is not a ``str``
                (None / int / bytes / ...).
            ViewPasscodeRotationRequired: When the stored
            ciphertext cannot be decrypted (the rotation-fail
            primitive). The P2 router maps this to the
            "regeneration required" interstitial — NEVER a 500.
        """
        if view_row.locked:
            return VerifyOutcome.LOCKED
        if not isinstance(presented, str):
            # Public-input hardening: the verify POST feeds
            # arbitrary customer input. A non-str ``presented``
            # (None, int, bytes, ...) MUST raise a typed error
            # rather than crash with ``AttributeError`` deep
            # inside the compare (P2 router maps TypeError to
            # 400). The TypeError-vs-WRONG choice: WRONG would
            # mask type confusion as a wrong-code attempt;
            # TypeError is the honest typed signal.
            raise TypeError(
                f"verify: presented must be a str; got "
                f"{type(presented).__name__}"
            )
        plaintext = self._cipher.unwrap(
            view_row.wrapped_passcode
        ).decode("ascii")
        if codes_match(presented, plaintext):
            return VerifyOutcome.OK
        return VerifyOutcome.WRONG

    # ─────────────────────────────────────────────────────────
    # record_failure — atomic increment + lockout audit
    # ─────────────────────────────────────────────────────────

    def record_failure(
        self,
        root: str,
        rel: str,
        *,
        ip: Optional[str] = None,
        actor: Optional[str] = None,
    ) -> tuple[int, bool]:
        """Atomic ``failed_attempts += 1``. The
        ``STRIKE_CEILING``-th failure flips ``locked`` AND
        emits the ``lockout`` audit row IN THE SAME
        ``engine.begin()`` transaction.

        Concurrent wrong-verify race at the lock boundary
        (architecture-recommendation.md R9): the increment
        and lock flip happen in ONE atomic ``UPDATE``
        statement (``SET failed_attempts = failed_attempts + 1,
        locked = CASE WHEN (failed_attempts + 1) >=
        :ceiling ...``) with a ``WHERE locked = 0`` guard — so
        concurrent 9th/10th failures cannot both cross the
        ceiling (the loser sees ``rowcount == 0`` and returns
        the existing count without emitting a duplicate
        ``lockout`` audit row). Test pins at
        ``test_record_failure_9_to_10_emits_exactly_one_lockout_audit``
        and ``test_record_failure_already_locked_does_not_emit_duplicate``
        in the P1 unit suite (the M1 atomicity contract; the
        PG row-lock / SQLite single-writer-model are belt-and-
        suspenders on top of the SQL-level guard).

        Args:
            root: The view root name (validated).
            rel: The view path relative to ``root`` (validated).
            ip: The request IP (optional; ``compute_ip_hmac``
                derives the 16-hex-char column value). None
                stores NULL.
            actor: The calling agent id (NULL for the public
                lane). None stores NULL.

        Returns:
            ``(attempts, locked_now)``. ``attempts`` is the
            post-increment value. ``locked_now`` is the row's
            ``locked`` flag AFTER the increment. ``(-1, False)``
            for an unknown ``(root, rel)`` pair — the P2
            router maps every unknown path to uniform 404
            (architecture-recommendation.md D2) and never
            increments a strike counter on it.
        """
        root = self._assert_root_name(root)
        rel = self._assert_rel_path(rel)
        actor = self._assert_actor(actor)

        ip_hmac: Optional[str] = None
        if ip is not None:
            if not isinstance(ip, str):
                raise TypeError(
                    f"ip must be a string or None; got "
                    f"{type(ip).__name__}"
                )
            ip_hmac = compute_ip_hmac(self._cipher, ip)

        return self._store.record_failure_with_audit(
            root_name=root,
            rel_path=rel,
            actor=actor,
            ip_hmac=ip_hmac,
        )

    # ─────────────────────────────────────────────────────────
    # refresh — regenerate + unlock + generation bump
    # ─────────────────────────────────────────────────────────

    def refresh(
        self, root: str, rel: str, actor: Optional[str]
    ) -> MintResult:
        """Regenerate the passcode + bump generation + clear
        lock + audit ``regenerate``.

        The generation bump silently revokes ALL outstanding
        grant cookies (the P2 stateless cookie + ADR D3
        revocation-via-generation). The lock is cleared (the
        strike counter is reset to 0). A new passcode is
        generated via the CSPRNG primitive.

        Args:
            root: The view root name (validated).
            rel: The view path relative to ``root`` (validated).
            actor: The calling agent id (Cardinal #2).

        Returns:
            :class:`MintResult` with the NEW plaintext
            passcode, the new generation (incremented by 1),
            ``created=False`` (refresh is not a fresh mint),
            and ``locked_status=False`` (refresh always
            clears the lock).

        Raises:
            ViewPasscodeNotFoundError: When ``(root, rel)``
                has no row. The caller (``view_link`` with
                ``refresh=True``) may invoke ``mint`` first
                (lazy-mint posture — architecture-
                recommendation.md D11) and then ``refresh``,
                or treat the not-found as a "first mint"
                short-circuit.
        """
        root = self._assert_root_name(root)
        rel = self._assert_rel_path(rel)
        actor = self._assert_actor(actor)

        existing = self._store.get_row(root, rel)
        if existing is None:
            raise ViewPasscodeNotFoundError(
                f"no passcode row for (root={root!r}, "
                f"rel={rel!r}); call mint() first"
            )
        plaintext = generate_passcode()
        new_wrapped = self._cipher.wrap(plaintext.encode("ascii"))
        new_generation = existing.generation + 1
        stored = self._store.refresh_with_audit(
            root_name=root,
            rel_path=rel,
            new_wrapped_passcode=new_wrapped,
            new_generation=new_generation,
            actor=actor,
        )
        del new_wrapped
        if stored is None:
            # Should be unreachable — we just verified the
            # row exists. Defensive raise.
            raise ViewPasscodeNotFoundError(
                f"refresh: row vanished mid-call for "
                f"(root={root!r}, rel={rel!r})"
            )
        return MintResult(
            passcode=plaintext,
            generation=stored.generation,
            created=False,
            locked_status=stored.locked,
        )

    # ─────────────────────────────────────────────────────────
    # get_row — read the durable row
    # ─────────────────────────────────────────────────────────

    def get_row(
        self, root: str, rel: str
    ) -> Optional[LiveViewPasscode]:
        """Read one passcode row by the UNIQUE key.

        Returns ``None`` for an unknown pair. The P2 verify
        POST maps every ``None`` to uniform 404 (architecture-
        recommendation.md D2 — no interactive verify oracle
        on garbage paths).
        """
        root = self._assert_root_name(root)
        rel = self._assert_rel_path(rel)
        return self._store.get_row(root, rel)

    # ─────────────────────────────────────────────────────────
    # append_audit — single audit row (P2 grant / deny emitters)
    # ─────────────────────────────────────────────────────────

    def append_audit(
        self,
        root: str,
        rel: str,
        generation: int,
        action: str,
        actor: Optional[str] = None,
        ip: Optional[str] = None,
    ) -> None:
        """Append a single audit row.

        Used by the P2 grant cookie issuance (``grant``) and
        the P2 verify short-circuit on wrong code (``deny``).
        Does NOT mutate the passcode row — only appends an
        audit row in a fresh ``engine.begin()`` transaction.

        Action validation: the action MUST be in
        :data:`LIVE_VIEW_AUDIT_ACTIONS`. P2 emits ``grant``
        and ``deny`` only — the existing ``mint`` /
        ``regenerate`` / ``lockout`` emitters use the atomic
        ``*_with_audit`` paths, not this helper.
        """
        root = self._assert_root_name(root)
        rel = self._assert_rel_path(rel)
        actor = self._assert_actor(actor)
        if not isinstance(generation, int) or isinstance(generation, bool):
            raise TypeError(
                f"generation must be an int; got "
                f"{type(generation).__name__}"
            )
        if generation < 1:
            raise ViewPasscodeValidationError(
                f"generation must be >= 1; got {generation}"
            )
        if action not in LIVE_VIEW_AUDIT_ACTIONS:
            raise ViewPasscodeValidationError(
                f"action {action!r} not in "
                f"LIVE_VIEW_AUDIT_ACTIONS"
            )
        ip_hmac: Optional[str] = None
        if ip is not None:
            if not isinstance(ip, str):
                raise TypeError(
                    f"ip must be a string or None; got "
                    f"{type(ip).__name__}"
                )
            ip_hmac = compute_ip_hmac(self._cipher, ip)
        self._store.append_audit(
            root_name=root,
            rel_path=rel,
            generation=generation,
            action=action,
            actor=actor,
            ip_hmac=ip_hmac,
        )


# ─────────────────────────────────────────────────────────────────────
# Service boundary errors — typed errors the P2 router maps to
# structured envelopes (NEVER raw exceptions).
# ─────────────────────────────────────────────────────────────────────


class ViewPasscodeValidationError(RuntimeError):
    """Raised on invalid service-boundary arguments."""


class ViewPasscodeNotFoundError(RuntimeError):
    """Raised when an operation requires an existing passcode
    row but ``(root, rel)`` has no row."""


# ─────────────────────────────────────────────────────────────────────
# Grant cookie codec (P2 of live-view-passcode; architecture-
# recommendation.md D6:71-79; phase2-plan.md T2.5 + D12).
# Single home, colocated with the cipher so the
# ``SYSTEM_ENCRYPTION_KEY`` rotation-coupling seam is intact (the
# same posture the cipher takes — the cookie is the explicit
# anchor for the rotation-coupling note).
# ─────────────────────────────────────────────────────────────────────


#: Cookie name — the canonical, schema-stable identifier the
#: P2 router sets + reads on the public lane. Stable across the
#: binding-spec v1 lifetime (any change is a breaking migration;
#: the value is the explicit anchor in the cookie contract).
GRANT_COOKIE_NAME: str = "view_grant"


@dataclass(frozen=True)
class GrantCookieClaims:
    """The plaintext claims encoded in a grant cookie token.

    Architecture-recommendation.md D6:75 — view identity is
    carried as ``(root_name, rel_path)`` claims (NOT a row id);
    the cookie is path-scoped to the EXACT public view URL
    (``Path`` = the request URL), so the cookie's content needs
    to match the request's view identity on validation — not
    carry a database-side id.

    Attributes:
        root_name: The view root (validated against the request
            URL's root segment).
        rel_path: The view relative path (validated against the
            request URL's path segment).
        generation: The passcode row's generation AT ISSUANCE.
            Validation requires ``cookie.generation ==
            row.generation`` so a refresh silently revokes ALL
            outstanding grants (architecture-recommendation.md
            D6:76; the ADR D3 revocation-via-generation ruling).
        exp: UNIX seconds. ``exp > now`` on validation
            (``time.time()``).
    """

    root_name: str
    rel_path: str
    generation: int
    exp: float

    def to_json_bytes(self) -> bytes:
        return json.dumps(
            {
                "root_name": self.root_name,
                "rel_path": self.rel_path,
                "generation": self.generation,
                "exp": self.exp,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    @classmethod
    def from_json_bytes(cls, raw: bytes) -> "GrantCookieClaims":
        obj = json.loads(raw.decode("utf-8"))
        return cls(
            root_name=str(obj["root_name"]),
            rel_path=str(obj["rel_path"]),
            generation=int(obj["generation"]),
            exp=float(obj["exp"]),
        )


def issue_grant_cookie(
    *,
    cipher: "ViewPasscodeCipher",
    root_name: str,
    rel_path: str,
    generation: int,
    ttl_seconds: int,
    now: float | None = None,
) -> bytes:
    """Issue a Fernet-encrypted grant cookie token.

    Args:
        cipher: The cipher (carries the Fernet instance + the
            same ``SYSTEM_ENCRYPTION_KEY``-derived key material
            used at rest for the passcode column).
        root_name: The view root (must be non-empty + the same
            ``is_well_formed_root_name`` gates the router
            enforces).
        rel_path: The view relative path (must be non-empty +
            pass ``is_well_formed_rel_path``).
        generation: The passcode row's generation AT ISSUANCE.
        ttl_seconds: Cookie lifetime in seconds (the
            ``passcode_grant_ttl_seconds`` config knob, default
            ``86400``).
        now: ``time.time()`` injection for tests; defaults to
            wall-clock.

    Returns:
        The Fernet ciphertext bytes (URL-safe base64 — set
        directly as the cookie value).

    Raises:
        ViewPasscodeValidationError: On invalid inputs.
    """
    if not isinstance(root_name, str) or not root_name:
        raise ViewPasscodeValidationError(
            "issue_grant_cookie: root_name must be a non-empty string"
        )
    if not isinstance(rel_path, str) or not rel_path:
        raise ViewPasscodeValidationError(
            "issue_grant_cookie: rel_path must be a non-empty string"
        )
    if (
        not isinstance(generation, int)
        or isinstance(generation, bool)
        or generation < 1
    ):
        raise ViewPasscodeValidationError(
            f"issue_grant_cookie: generation must be a positive int; "
            f"got {generation!r}"
        )
    if (
        not isinstance(ttl_seconds, int)
        or isinstance(ttl_seconds, bool)
        or ttl_seconds < 1
    ):
        raise ViewPasscodeValidationError(
            f"issue_grant_cookie: ttl_seconds must be a positive int; "
            f"got {ttl_seconds!r}"
        )
    current = time.time() if now is None else float(now)
    claims = GrantCookieClaims(
        root_name=root_name,
        rel_path=rel_path,
        generation=generation,
        exp=current + ttl_seconds,
    )
    return cipher.wrap(claims.to_json_bytes())


def parse_grant_cookie(
    *,
    cipher: "ViewPasscodeCipher",
    token: bytes | str,
    expected_root: str,
    expected_rel: str,
    expected_generation: int,
    now: float | None = None,
) -> GrantCookieClaims:
    """Validate a Fernet-encrypted grant cookie token.

    Validation chain (architecture-recommendation.md D6:76):

      1. decrypt (``InvalidToken`` → raises
         :class:`ViewPasscodeRotationRequired` — caller treats
         as no-grant, never 500)
      2. claims match THIS request's ``(root, rel)``
      3. ``cookie.generation == row.generation``
      4. ``exp > now``

    Any failure raises :class:`ViewPasscodeValidationError`
    with a message that DOES NOT include the offending
    ciphertext or any cookie internals — the caller renders
    the interstitial on the failure path.

    The P2 router DOES NOT distinguish between failure modes
    to the client — every failure collapses to the same
    interstitial envelope (architecture-recommendation.md D6:76
    "any failure → interstitial"). The typed-error distinction
    is for the caller + the test matrix only.

    Args:
        cipher: The cipher (same one that issued the cookie).
        token: The cookie bytes (or the URL-safe string the
            browser sent — both work for ``Fernet.decrypt``).
        expected_root: The view root from the request URL.
        expected_rel: The view relative path from the request
            URL.
        expected_generation: The passcode row's generation
            (post-refresh this is what kills old grants).
        now: ``time.time()`` injection for tests; defaults to
            wall-clock.

    Returns:
        The decoded :class:`GrantCookieClaims` on success.

    Raises:
        ViewPasscodeRotationRequired: On decryption failure
            (rotation-fail primitive — caller treats as
            no-grant, never 500).
        ViewPasscodeValidationError: On any other validation
            failure (root mismatch, rel mismatch, generation
            mismatch, expiry). The P2 router maps all of these
            to the interstitial.
    """
    if isinstance(token, str):
        token_bytes = token.encode("ascii")
    elif isinstance(token, (bytes, bytearray)):
        token_bytes = bytes(token)
    else:
        raise ViewPasscodeValidationError(
            "parse_grant_cookie: token must be bytes or str"
        )
    # Decrypt step — raises ViewPasscodeRotationRequired on
    # ciphertext failure (the canonical primitive per
    # architecture-recommendation.md D4:59 + D9:117).
    plaintext = cipher.unwrap(token_bytes)
    try:
        claims = GrantCookieClaims.from_json_bytes(plaintext)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ViewPasscodeValidationError(
            "parse_grant_cookie: malformed claims payload"
        ) from exc
    current = time.time() if now is None else float(now)
    if claims.exp <= current:
        raise ViewPasscodeValidationError(
            "parse_grant_cookie: cookie expired"
        )
    if claims.root_name != expected_root:
        raise ViewPasscodeValidationError(
            "parse_grant_cookie: root mismatch"
        )
    if claims.rel_path != expected_rel:
        raise ViewPasscodeValidationError(
            "parse_grant_cookie: rel mismatch"
        )
    if claims.generation != expected_generation:
        raise ViewPasscodeValidationError(
            "parse_grant_cookie: generation mismatch"
        )
    return claims


# ─────────────────────────────────────────────────────────────────────
# Per-IP token-bucket rate limiter (P2 of live-view-passcode;
# architecture-recommendation.md D7 + V6; phase2-plan.md T2.6 +
# D14). Adapts the TokenBucketLimiter pattern
# (daemon/sources/rate_limiter.py:25) into a per-IP map with
# LRU eviction; keying = rightmost X-Forwarded-For entry else
# socket peer (ADR D7:83-86). Fails OPEN on limiter-internal
# error (ADR V6) with a loud log — the durable strike lock is
# the backstop.
# ─────────────────────────────────────────────────────────────────────


@dataclass
class VerifyRateLimit:
    """Rate limit knobs (P2 of live-view-passcode)."""
    capacity: int
    refill_per_minute: int


#: LRU cap — bound memory at a fixed map size; the oldest
#: entry is evicted on overflow (the prior "delete random
#: entry" shape was the regression vector the LRU replaces).
#: ~50k entries × ~100 bytes/IP ≈ 5 MiB worst-case — well
#: below any reasonable memory ceiling.
_PER_IP_LRU_CAP: int = 50_000


def _rightmost_xff_ip(headers: "Mapping[str, str] | None") -> str | None:
    """Return the rightmost X-Forwarded-For entry (architecture-
    recommendation.md D7:83-86).

    TRUST MODEL — read before changing the bucket key
    ---------------------------------------------------
    Rightmost-XFF keying is sound ONLY behind a trusted-proxy front
    that *appends* the real client IP as the rightmost entry. Under
    that topology:

        proxy appends → rightmost == the address our proxy saw
                       == the real client (correct keying).

    Under any other topology the whole XFF header is
    client-supplied (rightmost included) and trivially spoofable.
    In particular, under DIRECT exposure (no fronting proxy) every
    XFF value is whatever the client put on the wire — rightmost
    keying still picks the spoofed entry, and the bucket degrades
    from per-IP throttle to a spoofable-key throttle. The router
    in this code-path runs at the daemon edge, which sits behind
    Envoy + oauth2-proxy in State A and behind the in-house OAuth2
    module in State B — for those topologies the
    rightmost-keying choice holds. Under direct exposure the
    bucket is NOT a security control; the
    **durable 10-strike lock (PG-backed row column, ``locked``
    bit, locked-page semantics in ADR V3) remains the
    authoritative control regardless**. The bucket is the soft
    front (fail-open, ADR V6); over-throttle is the worst
    degradation direction — never bypass (see also §3.4-X in
    docs/runbooks/live-view-passcode-deployment.md for the
    three-outcome XFF topology drill that empirically settles
    whether State A appends or overwrites — pin at ceremony,
    don't assume).

    FOLLOW-UP TICKET (NOT IMPLEMENTED HERE)
    ----------------------------------------
    The XFF trust knob (a config flag asserting "the daemon sits
    behind a known-append topology; trust rightmost") is a
    separate commission. Wiring it requires a config field +
    a startup topology probe or operator declaration +
    integration into the §3.4-X drill + retirement of the
    direct-exposure code path. Out of scope for the pre-promote
    C-fix sweep; do not introduce it inline.

    Header is comma-separated per RFC 7239; whitespace is trimmed
    before returning. Empty / whitespace-only entries are skipped
    (a header that is all commas / spaces returns None).
    """
    if headers is None:
        return None
    raw = headers.get("x-forwarded-for") or headers.get(
        "X-Forwarded-For"
    )
    if not raw:
        return None
    candidates = [
        segment.strip() for segment in raw.split(",") if segment.strip()
    ]
    if not candidates:
        return None
    return candidates[-1]


class _TokenBucket:
    """One IP's token-bucket state (the per-IP slot).

    Inline (not the public :class:`daemon.sources.rate_limiter.
    TokenBucketLimiter`) because the source-layer class uses an
    ``asyncio.Lock`` for cross-task safety — this lane is
    request-time only and the GIL covers the simple int/float
    mutations here. The shape (capacity, refill_per_minute →
    messages_per_second = refill_per_minute / 60) is the
    adapted parameter pair.
    """

    __slots__ = ("tokens", "last_refill")

    def __init__(self, capacity: int) -> None:
        self.tokens: float = float(capacity)
        self.last_refill: float = time.monotonic()

    def try_acquire(
        self,
        *,
        capacity: int,
        refill_per_minute: int,
        now: float,
    ) -> bool:
        """Return True iff a token was acquired.

        Side effect: updates ``tokens`` + ``last_refill``.
        """
        elapsed = now - self.last_refill
        if elapsed > 0:
            self.tokens = min(
                float(capacity),
                self.tokens
                + elapsed * (refill_per_minute / 60.0),
            )
            self.last_refill = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


class VerifyRateLimiter:
    """Per-IP token-bucket limiter for the public verify endpoint.

    Keying = rightmost XFF else socket peer (architecture-
    recommendation.md D7). LRU eviction caps the map at
    :data:`_PER_IP_LRU_CAP`. **Fails OPEN** on limiter-internal
    error (architecture-recommendation.md V6 — the durable
    strike lock is the authoritative control; a limiter bug
    must not brick the lane).

    Construction posture (BINDING):

    * ``capacity`` / ``refill_per_minute`` are operator-tunable
      via the ``ENSEMBLE_LIVE_VIEWS_VERIFY_RATE_LIMIT_*`` env
      knobs (LiveViewsConfig); defaults are 5 / 5
      (``phase2-plan.md`` D14).
    * No I/O at construction. State is in-memory; restart
      resets the map (one fresh burst per attacker — the
      durable strike lock backstops).
    """

    def __init__(
        self,
        *,
        capacity: int,
        refill_per_minute: int,
        clock: "Callable[[], float] | None" = None,
    ) -> None:
        if (
            not isinstance(capacity, int)
            or isinstance(capacity, bool)
            or capacity < 1
        ):
            raise ViewPasscodeValidationError(
                f"VerifyRateLimiter: capacity must be a "
                f"positive int; got {capacity!r}"
            )
        if (
            not isinstance(refill_per_minute, int)
            or isinstance(refill_per_minute, bool)
            or refill_per_minute < 1
        ):
            raise ViewPasscodeValidationError(
                f"VerifyRateLimiter: refill_per_minute must "
                f"be a positive int; got {refill_per_minute!r}"
            )
        self._capacity = capacity
        self._refill_per_minute = refill_per_minute
        self._clock = clock or time.monotonic
        # Insertion-ordered dict (Python 3.7+) = LRU eviction
        # by re-insertion on hit (move-to-end semantics).
        self._buckets: dict[str, _TokenBucket] = {}

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def refill_per_minute(self) -> int:
        return self._refill_per_minute

    def check(
        self,
        ip: str,
    ) -> bool:
        """Return True iff a token was acquired for ``ip``.

        Adapter pattern: callers pass the resolved IP (rightmost
        XFF or socket peer; the resolver is the P2 router's
        job — the limiter only consults the key). On an empty
        bucket the call returns False; on an internal exception
        the call returns True (fail-open, ADR V6) and emits a
        loud WARNING log.
        """
        try:
            return self._check_unlocked(ip)
        except Exception as exc:
            logger.warning(
                "[LiveViews] verify-rate-limiter internal "
                "error; failing OPEN (durable strike lock is "
                "the backstop): %s: %s",
                type(exc).__name__,
                exc,
            )
            return True

    def _check_unlocked(self, ip: str) -> bool:
        if not isinstance(ip, str) or not ip:
            # A bad key must not brick the lane — fall back
            # to the shared bucket (over-throttle, never
            # bypass) — pass this single malformed hit but
            # with a noisy log so the operator notices.
            logger.warning(
                "[LiveViews] verify-rate-limiter got a "
                "non-string / empty IP; counting as a miss "
                "without consuming a token"
            )
            return True
        bucket = self._buckets.get(ip)
        now = self._clock()
        if bucket is None:
            # New IP — establish the bucket and consume one
            # token from a full capacity. LRU eviction at
            # overflow.
            if len(self._buckets) >= _PER_IP_LRU_CAP:
                # Pop the oldest (insertion-ordered dict,
                # first key = oldest).
                oldest = next(iter(self._buckets))
                self._buckets.pop(oldest, None)
            bucket = _TokenBucket(self._capacity)
            self._buckets[ip] = bucket
            return bucket.try_acquire(
                capacity=self._capacity,
                refill_per_minute=self._refill_per_minute,
                now=now,
            )
        # Existing IP — refresh the LRU position by
        # re-inserting (Python 3.7+ dicts preserve
        # insertion order; re-insert moves to the end).
        self._buckets[ip] = bucket
        return bucket.try_acquire(
            capacity=self._capacity,
            refill_per_minute=self._refill_per_minute,
            now=now,
        )