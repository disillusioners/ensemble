"""KMS-Lite (P3-WP7 + P3-WP9 — designer-agent mission).

Day-1 mint-only KMS primitive. No policy layer, no third-party brokering,
no rotation/revocation surface (§7.5a deferred). Self-hosted OpenDesign
mints its own random credentials; the plaintext NEVER leaves the
encrypted store except via the spawn-time resolver
(:mod:`daemon.services.kms_resolver`) which substitutes markers to
plaintext in-RAM immediately before the MCP subprocess env is built.

Architecture invariants (closed risks from arch §8):

* **R2 fail-soft CredentialManager (WP9)** — :class:`KMSUnavailableError`
  is raised whenever :data:`SYSTEM_ENCRYPTION_KEY` is absent or invalid.
  NO plaintext fallback, NO fail-soft path. The store is initialised
  once per process; re-initialisation is not part of the day-1 contract.
* **Handles-not-secrets** — :func:`kms_request` returns
  ``{handle, fingerprint}`` only. Plaintext is held in the encrypted
  store and revealed only through :func:`kms_resolve_handle` (spawn-time
  resolver only) and :func:`kms_attach` (intermediate path that binds
  a handle to an MCP server's config env key).
* **Marker format (WP6 contract)** — markers in stored config are
  :data:`KMS_MARKER_PREFIX` + handle + :data:`KMS_MARKER_SUFFIX`. The
  regex in :mod:`daemon.services.kms_resolver` matches the same shape
  via :data:`KMS_MARKER_RE`.

Home verification (arch doc §7.5):
- **infra tool category** — mint tools surface as ``infra`` tools
  (registered in :mod:`daemon.tools.infra` under
  ``create_infra_tools`` factory — sibling addition via
  :func:`create_kms_tools` wired into
  :func:`daemon.tools.instance.create_instance_tools`).
- **No third-party keys** — plaintext is :func:`secrets.token_urlsafe`
  per call. No external vault / no AWS / no Vault.

Things this module deliberately does NOT do (deferred to §7.5a):

* rotate / revoke
* audit-log policy (the ``kms_issue`` emission is a single best-effort
  line per mint via :mod:`daemon.services.install_audit`; the audit
  lane's storage/policy is the writer module's concern)
* policy store / TTL sweeps / budget caps
* persistence across restart (day-1 in-memory; revisit if restart-loss
  becomes a day-1-correctness bug)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import threading
import uuid
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from daemon.services.install_audit import EVENT_KMS_ISSUE, append_install_audit
from daemon.util.key_hardening import (
    SYSTEM_ENCRYPTION_KEY_FILE_ENV,
    validate_key_source,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Marker format (shared contract between WP6 resolver and WP7 mint/attach)
# ---------------------------------------------------------------------------

#: Prefix used in stored MCP config env/headers values to mark a slot whose
#: plaintext must be resolved from the KMS store at spawn time. Must
#: byte-match the regex below.
KMS_MARKER_PREFIX = "__KMS_REF__"

#: Suffix closing a marker. Symmetric to :data:`KMS_MARKER_PREFIX`.
KMS_MARKER_SUFFIX = "__"

#: Handle namespace — emitted by :func:`kms_request` and embedded in
#: :data:`KMS_MARKER_PREFIX` + handle + :data:`KMS_MARKER_SUFFIX` markers.
KMS_HANDLE_PREFIX = "KMS_HANDLE_"

#: Regex matching a full marker value. Used by
#: :func:`daemon.services.kms_resolver.resolve_env` to split marker
#: payloads from non-marker plaintext values.
KMS_MARKER_RE = (
    "^"
    + __import__("re").escape(KMS_MARKER_PREFIX)
    + r"(" + __import__("re").escape(KMS_HANDLE_PREFIX) + r"[A-Za-z0-9_-]+)"
    + __import__("re").escape(KMS_MARKER_SUFFIX)
    + "$"
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class KMSUnavailableError(RuntimeError):
    """Raised when KMS-Lite cannot operate because encryption is unavailable.

    Per WP9: when :data:`SYSTEM_ENCRYPTION_KEY` is absent or invalid,
    :func:`kms_request` MUST raise this — NEVER the historical
    fail-soft plaintext fallback at
    ``daemon/sources/credentials.py:75-97``.

    The escalation envelope (sibling WP) recognises this as
    ``kind=installed_but_unconfigured`` /
    ``detection_evidence=kms_key_absent``.
    """


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class _KMSStore:
    """Day-1 in-memory KMS-Lite store.

    Holds a ``handle → encrypted blob`` mapping. The encrypted blob is a
    Fernet-encrypted JSON document carrying the plaintext secret plus
    metadata. Plaintext never leaves this class except via the
    :meth:`reveal` accessor, which is intentionally narrow: the only
    legitimate call site is the spawn-time resolver.
    """

    def __init__(self, encryption_key: bytes | str | None = None) -> None:
        self._lock = threading.Lock()
        # handle -> {"service", "reason", "actor", "fingerprint", "encrypted"}
        self._handles: dict[str, dict[str, Any]] = {}
        self._fernet = self._build_fernet(encryption_key)
        # P3-WP10 redaction-use registry. Holds a thread-safe set of the
        # plaintexts currently held by this store, so the logging
        # redaction filter (daemon.util.log_redaction_filter) can scrub
        # any plaintext that accidentally reaches a log record.
        #
        # Internal/redaction-use ONLY. NEVER exposed to tool callers or
        # to agent-facing surfaces. Snapshot semantics on read — callers
        # never see live mutations.
        self._plaintext_registry: set[str] = set()
        self._registry_lock = threading.Lock()

    @staticmethod
    def _build_fernet(encryption_key: bytes | str | None) -> Fernet:
        """Build a :class:`Fernet` from an explicit key or the environment.

        Raises:
            KMSUnavailableError: when no key is available, the key is
                malformed, or the ``cryptography`` package could not be
                imported at interpreter startup. NEVER falls back to a
                plaintext-only mode (WP9 invariant).
        """
        key: bytes | str | None = encryption_key
        if key is None:
            # P3-WP13a: the file source is the hardened custody path and
            # is read BEFORE the inline env fallback. Trailing newline /
            # CR (editor artifacts) are stripped; an empty file is a
            # refusal, never a silent fall-through to the inline var.
            key_file = os.environ.get(SYSTEM_ENCRYPTION_KEY_FILE_ENV, "").strip()
            if key_file:
                # P3 review F5: wrap the file read in try/except OSError
                # so a 0o000 perms file or a missing file surfaces as
                # the typed ``KMSUnavailableError`` (the documented
                # ``kms_request`` / ``kms_resolve_handle`` exception),
                # not a raw ``PermissionError`` / ``OSError`` leaking
                # from the resolve path.
                try:
                    with open(key_file, "r", encoding="utf-8") as fh:
                        key = fh.read().rstrip("\r\n")
                except OSError as exc:
                    raise KMSUnavailableError(
                        f"KMS-Lite unavailable: cannot read "
                        f"SYSTEM_ENCRYPTION_KEY_FILE: {exc}"
                    ) from exc
                if not key:
                    raise KMSUnavailableError(
                        "KMS-Lite unavailable: SYSTEM_ENCRYPTION_KEY_FILE "
                        "is set but the file is empty"
                    )
        if key is None:
            key = os.environ.get("SYSTEM_ENCRYPTION_KEY")
        if not key:
            raise KMSUnavailableError(
                "KMS-Lite unavailable: SYSTEM_ENCRYPTION_KEY is not set"
            )
        if isinstance(key, str):
            key = key.encode()
        try:
            return Fernet(key)
        except Exception as exc:  # noqa: BLE001 — Fernet raises ValueError on bad key
            raise KMSUnavailableError(
                f"KMS-Lite unavailable: invalid SYSTEM_ENCRYPTION_KEY ({exc})"
            ) from exc

    def mint(
        self,
        service: str,
        reason: str,
        actor: str = "system",
    ) -> dict[str, str]:
        """Mint a new KMS handle for ``service``.

        Returns:
            ``{"handle": "KMS_HANDLE_<uuid-hex>", "fingerprint": "<sha256[:16] hex>"}``.

        Raises:
            KMSUnavailableError: only if the store was constructed without
                a valid key (constructor already raised in that case).
        """
        if not service:
            raise ValueError("kms_request: service must be a non-empty string")
        if not reason:
            raise ValueError("kms_request: reason must be a non-empty string")

        handle = f"{KMS_HANDLE_PREFIX}{uuid.uuid4().hex}"
        plaintext = secrets.token_urlsafe(32)
        # P3-WP10: register the plaintext with the redaction filter
        # registry before any other state is touched, so a concurrent
        # log record emitted during this turn cannot leak the plaintext.
        self._register_plaintext(plaintext)
        fingerprint = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()[:16]
        payload = {
            "service": service,
            "reason": reason,
            "actor": actor,
            "plaintext": plaintext,
            "fingerprint": fingerprint,
        }
        encrypted = self._fernet.encrypt(json.dumps(payload).encode("utf-8")).decode(
            "utf-8"
        )

        with self._lock:
            self._handles[handle] = {
                "service": service,
                "reason": reason,
                "actor": actor,
                "fingerprint": fingerprint,
                "encrypted": encrypted,
            }
        return {"handle": handle, "fingerprint": fingerprint}

    def reveal(self, handle: str) -> str | None:
        """Resolve a handle to its plaintext.

        Returns:
            The plaintext, or ``None`` if the handle is unknown.

        Warning:
            Reserved for the spawn-time resolver
            (:func:`daemon.services.kms_resolver.resolve_env`). This
            method is NOT exposed to agent tools.
        """
        with self._lock:
            entry = self._handles.get(handle)
        if entry is None:
            return None
        try:
            payload = json.loads(self._fernet.decrypt(entry["encrypted"].encode()).decode())
        except (InvalidToken, ValueError, KeyError) as exc:
            # Tampered / corrupted blob. We refuse to surface plaintext
            # rather than risk an unverified fallback.
            logger.error(
                "KMS-Lite reveal failed: handle=%s err=%s",
                handle,
                exc.__class__.__name__,
            )
            return None
        return payload.get("plaintext")

    def fingerprint(self, handle: str) -> str | None:
        """Return the recorded fingerprint for ``handle`` (or ``None``)."""
        with self._lock:
            entry = self._handles.get(handle)
        if entry is None:
            return None
        return entry["fingerprint"]

    def has_handle(self, handle: str) -> bool:
        with self._lock:
            return handle in self._handles

    # For tests ----------------------------------------------------------

    def size(self) -> int:
        with self._lock:
            return len(self._handles)

    # P3-WP10 — internal redaction-use registry -----------------------
    # Thread-safe accessors used ONLY by
    # ``daemon.util.log_redaction_filter``. Not exported to agent tools.

    def _register_plaintext(self, plaintext: str) -> None:
        """Register ``plaintext`` with the redaction filter registry.

        Internal/redaction-use ONLY. Called from :meth:`mint` immediately
        after the plaintext is generated. Safe to call multiple times —
        the registry is a set.
        """
        with self._registry_lock:
            self._plaintext_registry.add(plaintext)

    def _iter_registered_plaintexts(self) -> list[str]:
        """Return a snapshot list of all registered plaintexts.

        Internal/redaction-use ONLY. The list is a per-call copy so the
        redaction filter sees a stable view even if a concurrent mint
        mutates the registry mid-scrub.
        """
        with self._registry_lock:
            return list(self._plaintext_registry)


# ---------------------------------------------------------------------------
# Module-level singleton (lazy, process-wide)
# ---------------------------------------------------------------------------


_store: _KMSStore | None = None
_store_lock = threading.Lock()


def _get_store() -> _KMSStore:
    """Return the process-wide :class:`_KMSStore`, initialising on first call.

    The constructor reads ``SYSTEM_ENCRYPTION_KEY`` from the environment at
    first call. Subsequent calls return the cached store.
    """
    global _store
    if _store is not None:
        return _store
    with _store_lock:
        if _store is None:
            _store = _KMSStore()
    return _store


def reset_store_for_tests() -> None:
    """Drop the process-wide store.

    Test-only. Production code MUST NOT call this — the day-1 store is
    intentionally process-singleton so the spawn-time resolver and the
    mint caller share state.
    """
    global _store
    with _store_lock:
        _store = None


def reset_plaintext_registry_for_tests() -> None:
    """Drop the process-wide redaction-use plaintext registry.

    Test-only. Production code MUST NOT call this. The store reset
    above already drops the registry (the registry lives on the store
    instance), but tests that want to scrub the registry WITHOUT
    dropping the store can use this helper — for example, to confirm
    the filter is a no-op pass-through when no plaintexts are
    registered.

    Safe to call before the store is initialised.
    """
    global _store
    with _store_lock:
        store = _store
    if store is None:
        return
    with store._registry_lock:
        store._plaintext_registry.clear()


# ---------------------------------------------------------------------------
# Public API — what the WP7 mint primitive and the resolver expose
# ---------------------------------------------------------------------------


def kms_request(
    service: str,
    reason: str,
    actor: str = "system",
) -> dict[str, str]:
    """Mint a new KMS handle for ``service``.

    Args:
        service: Logical service the credential is bound to (e.g.
            ``"opendesign"``).
        reason: Free-form reason for the mint (audit-side field).
        actor: Identity performing the mint. Defaults to ``"system"``
            because tool callers pass ``current_instance_id`` explicitly
            via the tool wrapper.

    Returns:
        ``{"handle": "KMS_HANDLE_<uuid>", "fingerprint": "<sha256[:16] hex>"}``

    Raises:
        KMSUnavailableError:
            * when ``SYSTEM_ENCRYPTION_KEY`` is absent or invalid
              (WP9 fail-closed invariant),
            * OR when the WP13a hardening gate refuses the key source
              (file perms > 0o600, stale key file, no key configured),
            * OR when the file source's ``open()`` raises ``OSError``
              (e.g. 0o000 perms, missing file) — the resolve path
              surfaces this as the typed ``KMSUnavailableError`` rather
              than leaking the raw ``OSError`` (P3 review F5).

            Partial mint states are impossible because the store writes
            are atomic under the per-instance lock — on refusal, no
            handle is minted and no audit line is emitted.
        ValueError: when ``service`` or ``reason`` is empty.

    Invariant:
        The plaintext NEVER appears in the return value or in any
        observable side effect of this function (no log line, no audit
        line — those are the responsibility of the caller). The only
        legitimate plaintext surface is :func:`kms_resolve_handle`.
    """
    # P3-WP13a mint-time gate: refuse to issue when the key source
    # violates the hardening contract (bad file perms, stale key file,
    # no key at all). Fail-closed — this is the refusal path the WP13a
    # acceptance tests pin (0o644 file ⇒ KMSUnavailableError from
    # kms_request, not merely from the standalone validator).
    gate = validate_key_source()
    if not gate.ok:
        msg = gate.message
        if gate.source == "none":
            # Preserve the WP9 message's operator-actionability: the
            # bare gate summary ("no key source configured") loses the
            # env-var pointer the historical unset path carried.
            msg = (
                f"{msg} (SYSTEM_ENCRYPTION_KEY is not set; set it "
                "inline or via SYSTEM_ENCRYPTION_KEY_FILE)"
            )
        raise KMSUnavailableError(msg)
    store = _get_store()
    record = store.mint(service=service, reason=reason, actor=actor)
    # P3-WP12 fold — §7.4 audit lane: every mint emits ONE ``kms_issue``
    # line. ``secret_ref`` carries the HANDLE only (handles-not-secrets).
    # Best-effort: an audit-lane failure NEVER fails a mint (log warning,
    # proceed) — the store write above is the authoritative outcome.
    audit = append_install_audit(
        event=EVENT_KMS_ISSUE,
        name=service,
        actor=actor,
        parent=None,
        secret_ref=record["handle"],
        idempotency_key="",
        trace_id=uuid.uuid4().hex,
    )
    if not audit.written:
        logger.warning(
            "kms_issue audit line not written (mint succeeded): %s",
            audit.error,
        )
    return record


def kms_resolve_handle(handle: str) -> str | None:
    """Resolve ``handle`` to its plaintext.

    Reserved for the spawn-time resolver. Tool callers MUST NOT call
    this directly — the marker format in stored config means the
    resolver layer is the only legitimate plaintext surface.
    """
    store = _get_store()
    return store.reveal(handle)


def kms_fingerprint(handle: str) -> str | None:
    """Return the recorded fingerprint for ``handle`` (or ``None``).

    Exposed for the WP7 ``kms_lookup_handle`` tool — agents hold
    ``{handle, fingerprint}`` and may want to verify that the
    fingerprint matches the one they recorded at mint time.
    """
    store = _get_store()
    return store.fingerprint(handle)


def build_marker(handle: str) -> str:
    """Build the marker string for a given handle.

    Convenience for ``kms_attach`` and tests. The marker is the value
    that should be stored in ``mcp_servers.config.env[<KEY>]`` and
    :attr:`daemon.repositories.mcp_server.models.McpServer.instance_metadata`
    for marker-aware display layers.
    """
    if not handle.startswith(KMS_HANDLE_PREFIX):
        raise ValueError(
            f"build_marker: handle must start with {KMS_HANDLE_PREFIX!r}"
        )
    return f"{KMS_MARKER_PREFIX}{handle}{KMS_MARKER_SUFFIX}"