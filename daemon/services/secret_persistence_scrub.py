"""Agent-secrets P2 — both-sides persistence scrub (design §2 + §3.4).

ONE shared module, ONE scrubber class, used by all THREE wiring sites:

* **S1 — results side** (``daemon/services/long_tool_nudge.py``
  ``wrapped_tools_node``, post-ToolNode / pre-state-commit): scrubs
  REAL ``ToolMessage.content`` BEFORE the result joins live graph
  state. Protects agent-visible context AND everything downstream
  (checkpoints, snapshots, dispatch) — the R1 designed-out.
* **S2 — args + belt** (``daemon/checkpoint_adapter.py`` saver proxy
  ``aput`` / ``aput_writes`` (+ sync ``put`` / ``put_writes``)): scrubs
  a COPY of the checkpoint snapshot — ``AIMessage.tool_calls[*].args``
  (+ the ``additional_kwargs["tool_calls"]`` OpenAI-flavor fallback) +
  ``ToolMessage.content`` — before the wrapped saver serializes it.
  Live objects stay intact (the ``redact_exc_str`` pattern).
* **S3 — defense-in-depth** (``daemon/services/instance_messaging.py``
  ``_prepare_enqueued_message``): scrubs the persisted queue-row body
  before the ``MessageQueue`` INSERT (closes the user-paste echo into
  ``message_queue``).

Semantics (design §2.4 — binding):

* **Exact-match only** against stored plaintext VALUES. No regex /
  shape redaction (the I0 spine is deferred).
* **Replacement token = the marker itself** ``__ENS_SECRET__<id>__``:
  format-stable, self-documenting in transcripts, idempotent under
  multi-pass scrubbing, round-trippable via ``parse_marker``.
* **Markers are NEVER scrubbed** — candidate strings that fullmatch
  the marker regex are skipped (safe by construction; pin (c)).
* **Marker-template substring exclusion** — a stored plaintext that
  is a substring of the marker template ``__ENS_SECRET__<uuid>__``
  is skipped. Agent-controlled values can reach this case (e.g.
  ``"SECRET"``, ``"ENS_"``, ``"S"`` — all substrings of the
  template); substituting such a plaintext inside an already-minted
  marker would break the marker's ``fullmatch`` and grow the string
  on every scrub pass, violating the §2.4 idempotency-under-multi-
  pass guarantee. Such pathological values are NEVER scrubbed
  (documented exclusion — a partner that legitimately needs such a
  value must rename it).
* **Collision determinism**: multiple secrets sharing one plaintext →
  the FIRST id by ``created_at`` ordering wins. The value-set query
  orders by ``created_at`` explicitly (the design §2.5 anchor
  ``list_active()`` at ``secret_service.py:805`` carries NO explicit
  ORDER BY — the scrub set therefore adds its own deterministic
  ``ORDER BY created_at``, which is the realization the anchor
  points at). A secondary stable sort (longest plaintext first,
  created_at tiebreak) prevents a shorter secret's value from
  partially scrubbing a longer secret's value when one is a strict
  substring of the other; the tiebreak keeps the collision ruling
  byte-exact for the identical-plaintext case.
* **Retired secrets stay in the scrub set** (§2.4 binding ruling —
  see the §2.4-vs-§2.5 reconciliation note below).
* **``AIMessage.content`` is NEVER scrubbed** (LLM-authored prose;
  R8 accepted residual risk). Args stay intact in LIVE state (S2
  scrubs only the persisted snapshot copy).

§2.4-vs-§2.5 reconciliation (recorded per commission instruction)
------------------------------------------------------------------

§2.4 rules "Retired secrets stay in the scrub set — their values must
never leak from old transcripts regardless of resolve policy" while
§2.5's sourcing sentence says "loaded at init from ``secrets WHERE
retired_at IS NULL``". **§2.4 is the binding requirement**: the value
set is loaded from ALL rows (no ``retired_at`` filter). Retire is a
presentation-layer delist (D1 soft-retire), not revocation — an old
transcript carrying a retired plaintext must still collapse to its
marker. The §2.5 WHERE-clause wording is the one point overridden;
everything else in §2.5 (decrypt-on-scrub, no plaintext cache, empty-
set fast path, TTL) is implemented verbatim.

Decrypt-on-scrub (§2.5 — binding)
---------------------------------

The scrub set holds METADATA ONLY (``id`` / ``created_at`` /
``wrapped_secret`` bytes). Plaintext exists ONLY inside the scope of
a single scrub call: per call, each candidate row's Fernet ciphertext
is unwrapped ONCE, compared against the payload, and the unwrapped
value is dropped when the call returns. **No plaintext cache at any
layer, ever** (the KMS-Lite LANE-1 drain anti-pattern). Cost is
O(N) unwraps per scrub call — the design's accepted <1% turn-latency
budget at 50 secrets (§2.5).

Id-set staleness — R3 (§2.5)
----------------------------

The id/name/kind index is cached in-RAM (metadata only). Two
refresh triggers:

1. **In-process invalidation event (primary defense)** — fired by
   ``SecretService`` whenever a secret row is created (``store`` /
   ``generate_ssh_keypair`` / ``generate_api_token`` — any row
   creation, since all three mint rows the scrub set must see).
   Mechanism: a simple in-process callback list owned by
   :class:`SecretService` (the repo convention for v1 pub/sub —
   "a simple callback list is acceptable for v1, document it").
   The scrubber subscribes when it binds to the service; the
   callback drops the cached set so the next scrub re-queries.
   A retire does NOT fire the event (retired rows REMAIN in the
   scrub set per §2.4 — the set is unchanged by retire).
2. **60 s TTL backstop** — a ``time.monotonic``-based freshness
   window; a stale set re-queries lazily on the next scrub. The TTL
   is the BACKSTOP, not a substitute: a newly-minted secret's
   plaintext appearing in a tool result before the event fires is
   the accepted leakage window (≤60 s) the design calls out.

Empty-set fast path (§2.5 — autonomy-first): when the id set is
empty, every scrub entry returns the payload UNCHANGED after one
``len()`` check (sub-µs; no unwrap, no DB, no allocation).

Audit (design §3.4)
-------------------

Per-SUBSTITUTION only (never per-write): when a scrub call actually
replaced a secret's value, ONE audit row per distinct ``secret_id``
per scrub call is stamped — ``action='use_marker'`` (the enum entry
"the marker was (re)created in place of the value"; the ``scrub``
action does not exist in :data:`SECRET_AUDIT_ACTIONS` and adding one
would touch the P1 model file) with ``context='scrub'``. A scrub
that substitutes nothing writes ZERO rows. The audit write lives on
the :class:`SecretService` (``record_scrub_substitution``) so the
M1 atomic-audit contract stays in one module; failures are logged
and NEVER fail the scrub (scrubbing correctness is the primary
goal; audit is observability).

Zero behavior change for secret-free agents: until a
:class:`~daemon.services.secret_service.SecretService` exists in the
process, :func:`get_process_scrubber` returns ``None`` and every
wiring helper is an identity function. This module is import-safe
and carries NO module-level side effects (no DB, no threads, no
file IO at import time).

Do-not-touch lanes untouched: ``kms_lite.py`` / ``kms_resolver.py``
/ ``ens_env_tools.py`` / ``warmup_pool.py`` / ``build_pooled_stdio_config``
/ ``db_connections`` Fernet are NOT referenced here.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Optional

from daemon.services.secret_service import (
    ENS_SECRET_MARKER_RE_COMPILED,
    SCRUB_AUDIT_CONTEXT,
    SecretService,
    mint_marker,
)

logger = logging.getLogger(__name__)

#: Default freshness window (seconds) for the cached id set — design
#: §2.5's TTL backstop. The in-process invalidation event is the
#: PRIMARY defense; this window bounds the worst-case staleness when
#: the event path is somehow missed (e.g. a second process writing
#: rows — multi-process writes are out of scope for the in-process
#: daemon model).
DEFAULT_ID_SET_TTL_SECONDS: float = 60.0

#: Actor stamped on scrub audit rows when the wiring site cannot
#: resolve the calling instance id (S2 subgraph namespaces, S3
#: degenerate callers). Operator-diagnostic sentinel — the audit
#: row's ``secret_id`` is the forensic anchor regardless.
SCRUB_ACTOR_FALLBACK: str = "<scrub>"


# ─────────────────────────────────────────────────────────────────────
# Process-wide service registry + lazy scrubber
# ─────────────────────────────────────────────────────────────────────
#
# The daemon constructs ONE SecretService per process (the manager
# wiring at ``daemon/manager.py`` — ``self._secret_service``). The
# three wiring sites (S1 node / S2 saver proxy / S3 enqueue prelude)
# resolve the shared scrubber lazily at call time through
# :func:`get_process_scrubber`; before any SecretService exists the
# registry is empty and every helper is an identity function (the
# zero-behavior-change invariant — import-safe, no module-level
# side effects).
#
# Registration is fired from ``SecretService.__init__`` (a lazy
# in-method import — the repo's cycle-break convention) so the
# wiring sites need NO new constructor plumbing (the commission
# forbids touching persistence.py / manager.py / graph.py).

_PROCESS_LOCK = threading.Lock()
_PROCESS_SECRET_SERVICE: Optional[SecretService] = None
_PROCESS_SCRUBBER: Optional["SecretPersistenceScrubber"] = None


def register_secret_service(service: SecretService) -> None:
    """Register the process-wide :class:`SecretService` (called by
    ``SecretService.__init__``; lazy import, never raises).

    The FIRST registration wins for the scrubber binding (the daemon
    constructs exactly one service; a second registration would be a
    test artifact). Subscribes the shared scrubber's invalidation
    callback to the service's store-event list so R3's primary
    defense is wired at registration time.
    """
    global _PROCESS_SECRET_SERVICE, _PROCESS_SCRUBBER
    with _PROCESS_LOCK:
        if _PROCESS_SECRET_SERVICE is not None:
            # Idempotent re-registration (module re-import in tests)
            # is a no-op unless the service object changed.
            if _PROCESS_SECRET_SERVICE is service:
                return
        _PROCESS_SECRET_SERVICE = service
        _PROCESS_SCRUBBER = SecretPersistenceScrubber(service)
        _PROCESS_SCRUBBER._subscribe_to_service()
        logger.debug(
            "[SecretScrub] process scrubber registered "
            "(service id=%s)",
            id(service),
        )


def get_process_secret_service() -> Optional["SecretService"]:
    """Return the process-wide :class:`SecretService` or ``None``.

    agent-secrets P2 — Gap-2 consolidation: THE public discovery
    helper for every resolution seam (bash ``env`` overlay §1a,
    proc ``_build_child_env`` §1b, MCP spawn points §1c, the
    ``SECRET_TYPED_ARGS`` pre-invoke pass §1d). The manager
    constructs ONE ``SecretService`` per process and its
    ``__init__`` self-registers via :func:`register_secret_service`;
    seams read it LAZILY at call time. No import-time side effects
    (design §0) — ``None`` service + a marker-shaped value raises
    the typed ``secret_resolver_store_unavailable`` envelope at the
    resolver (fail-closed; design §3.2).

    This replaces the per-seam ``_get_process_secret_service``
    copies (byte-identical logic ×5). Seam LOGIC stays deliberately
    separate per §1b/§1c ("separate edits, share ONLY
    ``resolve_secret_markers``") — only the service DISCOVERY is
    shared, exactly like ``get_process_scrubber``.
    """
    with _PROCESS_LOCK:
        return _PROCESS_SECRET_SERVICE


def get_process_scrubber() -> Optional["SecretPersistenceScrubber"]:
    """Return the shared process scrubber, or ``None`` before any
    :class:`SecretService` exists (secret-free agents — the
    empty-set fast path one level up: an identity function).

    NEVER constructs a scrubber from nothing — the only source is a
    registered service (the manager wiring). Import-safe; no DB.
    """
    with _PROCESS_LOCK:
        return _PROCESS_SCRUBBER


def reset_process_registry() -> None:
    """Test-only: drop the process registry + scrubber.

    The registry is process-global; the secrets-scoped tests use
    this in a try/finally so a registered service can never leak
    into unrelated tests in the same pytest process.
    """
    global _PROCESS_SECRET_SERVICE, _PROCESS_SCRUBBER
    with _PROCESS_LOCK:
        _PROCESS_SECRET_SERVICE = None
        _PROCESS_SCRUBBER = None


# ─────────────────────────────────────────────────────────────────────
# The scrubber
# ─────────────────────────────────────────────────────────────────────


class SecretPersistenceScrubber:
    """The shared both-sides scrub kernel (design §2).

    One instance per process in production (bound to the manager's
    ``SecretService`` via :func:`register_secret_service`). Tests
    construct instances directly with an injected clock for the
    TTL backstop (test hook (g)).

    Args:
        secret_service: The :class:`SecretService` providing the
            row set (decrypt-on-scrub) + the audit sink.
        ttl_seconds: Freshness window for the cached id set
            (design §2.5 backstop; default 60 s).
        clock: Monotonic clock callable (injection seam for tests;
            default :func:`time.monotonic`).
    """

    def __init__(
        self,
        secret_service: SecretService,
        *,
        ttl_seconds: float = DEFAULT_ID_SET_TTL_SECONDS,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self._service = secret_service
        self._ttl_seconds = float(ttl_seconds)
        self._clock = clock or time.monotonic
        # Cached METADATA rows: list of (secret_id, created_at,
        # wrapped_bytes). NEVER carries plaintext. ``None`` means
        # "needs (re)load"; an empty list is a LEGITIMATE loaded
        # state (the secret-free fast path — distinct from the
        # never-loaded sentinel so the fast path doesn't re-query
        # per call).
        self._rows: Optional[list[tuple[str, str, bytes]]] = None
        self._loaded_at: float = -float("inf")
        # Wall-clock stamp of the LAST ATTEMPTED load — success
        # OR failure. Distinct from ``_loaded_at`` (which only
        # stamps successful loads): gates the retry on the FAILURE
        # path so a never-loaded / broken-secrets-schema / DB-down
        # case cannot spam one exception per scrub call (S1
        # results + S2 writes + S3 enqueues each call
        # ``_current_rows``). One re-query attempt per TTL window
        # is the rule — holds for both the stale-refresh case
        # AND the never-loaded case.
        self._last_attempt_at: float = -float("inf")
        self._lock = threading.Lock()

    # ── R3 invalidation ─────────────────────────────────────────

    def _subscribe_to_service(self) -> None:
        """Subscribe to the service's store-event callback list.

        The event design (commission E): a small in-process pub/sub
        — a plain callback list on :class:`SecretService` guarded by
        a lock (stdlib, zero deps). The scrubber is the single
        subscriber in production; the list shape keeps the door open
        for future subscribers without re-shaping the service.
        """
        try:
            self._service.add_store_listener(self._on_secret_stored)
        except Exception:  # pragma: no cover — defensive
            logger.exception(
                "[SecretScrub] failed to subscribe to store events; "
                "falling back to TTL-only staleness (R3 backstop)"
            )

    def _on_secret_stored(self) -> None:
        """Store-event callback — drop the cached set (R3 primary).

        The next scrub re-queries the row set, so a newly-minted
        secret is visible to the scrubber WITHIN THE SAME PROCESS
        immediately after its insert commits (no 60 s wait).
        Resets ``_last_attempt_at`` too — the next call MUST re-
        query (the invalidate event is the primary defense; the
        TTL is the backstop; reset both so the first call after
        invalidation is the one attempt, not the cached gate).
        """
        with self._lock:
            self._rows = None
            self._loaded_at = -float("inf")
            self._last_attempt_at = -float("inf")

    #: Public alias — the wiring sites and tests may force a refresh
    #: explicitly (mirrors the "single subscriber swaps the id-set"
    #: design sentence).
    invalidate = _on_secret_stored

    # ── Id-set (metadata index) ─────────────────────────────────

    def _load_rows(self) -> list[tuple[str, str, bytes]]:
        """Re-query the metadata row set (design §2.5 sourcing).

        ALL rows — retired INCLUDED (§2.4 binding; see module
        docstring for the §2.4-vs-§2.5 reconciliation). Ordered by
        ``created_at`` (collision determinism). Only id / created_at
        / wrapped_secret are read — the index NEVER holds plaintext.

        Routed through ``SecretService.iter_scrub_rows`` so the
        scrub module does NOT reach into ``self._service._engine``
        directly — the read seam lives on the service (reviewer
        finding #6).
        """
        return self._service.iter_scrub_rows()

    def _current_rows(self) -> list[tuple[str, str, bytes]]:
        """Return the cached metadata rows, refreshing when stale.

        Refresh triggers: never-loaded, store-event invalidation,
        or TTL expiry (60 s backstop). One small SELECT per refresh.

        Failure-path gating — design §2.5 backstop (fail-open +
        TTL-throttle):

        * A SUCCESSFUL load refreshes ``_loaded_at`` (stale-refresh
          path).
        * A FAILED load (DB down / flapping since process start, or
          broken ``secrets`` schema) must NOT re-attempt on EVERY
          scrub call — otherwise S1 + S2 + S3 each retry the connect
          per call and the log file fills with one exception per
          call. Instead the failed path refreshes ``_last_attempt_at``
          so the next call within the TTL window returns the cached
          ``[]`` WITHOUT touching the engine — one re-query attempt
          per TTL window for the never-loaded case (mirrors the
          stale-refresh case where a failed re-requery is also
          throttled by the same TTL).
        """
        now = self._clock()
        with self._lock:
            # Fast path: cached, fresh → return without DB IO.
            if (
                self._rows is not None
                and (now - self._loaded_at) < self._ttl_seconds
            ):
                return self._rows
            # Never-loaded OR stale → gate the ATTEMPT by TTL.
            # When the last attempt was within the TTL window we
            # skip the connect entirely (no log spam, no engine
            # pressure) and return the cached empty set if we have
            # one, else ``[]``. One re-query attempt per TTL window
            # regardless of whether the previous attempt loaded a
            # set, failed to load, or was the never-loaded case.
            if (now - self._last_attempt_at) < self._ttl_seconds:
                return self._rows if self._rows is not None else []
        # Load OUTSIDE the lock (a DB read while another thread
        # scrubs must not serialize scrubs; a duplicate concurrent
        # load is harmless — last writer wins, both read-valid).
        try:
            rows = self._load_rows()
        except Exception:
            # DB unavailable: fail OPEN for THIS call (the scrub
            # cannot know the values → cannot substitute) and stamp
            # BOTH ``_last_attempt_at`` (gates the retry; the next
            # call within the TTL returns ``[]`` without querying)
            # AND ``_loaded_at`` (legacy stamp — kept so any future
            # fast-path predicate that consults it stays consistent).
            # The TTL gate holds for BOTH the stale-refresh case
            # AND the never-loaded case: a persistently-unavailable
            # store, e.g. a disposed test engine, logs ONE exception
            # per TTL window instead of one per scrub call; a genuine
            # transient flap delays scrub freshness by ≤ one TTL
            # (the same trust class as the TTL backstop itself).
            # Fail-open here is the same trust class as the
            # resolver's store-unavailable envelope: the scrub is
            # defense-in-depth BEHIND S1, and S1's own source of
            # truth is the same DB (if the DB is down, no mint /
            # resolve happened either).
            logger.exception(
                "[SecretScrub] id-set re-query failed; scrubbing "
                "skipped for this call and retry is TTL-gated "
                "(fail-open)"
            )
            with self._lock:
                self._last_attempt_at = now
                self._loaded_at = now
                if self._rows is not None:
                    return self._rows
                return []
        with self._lock:
            self._rows = rows
            self._loaded_at = now
            self._last_attempt_at = now
            return self._rows

    # ── Decrypt-on-scrub value pairs ────────────────────────────

    def _plaintext_pairs(
        self, rows: list[tuple[str, str, bytes]]
    ) -> list[tuple[str, str]]:
        """Unwrap each row ONCE and return ``(secret_id, plaintext)``
        pairs ordered for deterministic replacement.

        Ordering: longest plaintext first (prevents a shorter
        secret's value from partially scrubbing a longer secret's
        value when one strictly contains the other), with the
        ``created_at`` tiebreak preserved from the row query so the
        §2.4 identical-plaintext collision ruling (first id by
        created_at) holds byte-exactly (Python's sort is stable).

        Rows that fail to unwrap (``InvalidToken`` — key rotated
        without re-encrypt; or cipher unavailable mid-flight) are
        SKIPPED with a WARN: the value cannot be compared, and —
        per the rotation coupling — a rotated key breaks resolution
        louder than it breaks scrubbing. Never raises.

        Marker-template substring exclusion (§2.4 idempotency): a
        stored plaintext that is a SUBSTRING of any well-formed
        marker (``__ENS_SECRET__<uuid4-hex>__``) is skipped. Reason:
        ``text.replace(plaintext, marker)`` would fire INSIDE every
        already-minted marker in the payload, breaking their
        ``fullmatch`` (resolution fails) and growing the string on
        every scrub pass — the §2.4 idempotency-under-multi-pass
        guarantee would be violated. Agent-controlled values make
        this reachable (a worker CAN store a value like ``"ENS_"``
        or ``"SECRET"``), so the guard sits here at the only
        place plaintext is ever held in scope. Documented exclusion:
        such pathological values are NEVER scrubbed (a partner that
        legitimately holds such a value can rename it).
        """
        # Build the marker template the scrubber MUST defend — one
        # canonical UUID-shape payload. The exact payload is
        # irrelevant: the marker regex's inner-group is the fixed
        # UUID4 hex/dash pattern, and ANY 32-hex + 4-dash payload
        # fullmatches it (the 14th char is fixed to ``4`` but the
        # prefix test only needs the marker shape — 0..9a-f covers
        # all valid character positions).
        _marker_template = mint_marker("00000000-0000-0000-0000-000000000000")
        pairs: list[tuple[str, str]] = []
        for secret_id, created_at, wrapped in rows:
            try:
                plaintext_bytes = self._service._cipher.unwrap(wrapped)
            except Exception as exc:
                logger.warning(
                    "[SecretScrub] secret %s could not be unwrapped "
                    "(%s); its value is OUTSIDE the scrub set for "
                    "this call",
                    secret_id,
                    type(exc).__name__,
                )
                continue
            try:
                plaintext = plaintext_bytes.decode("utf-8")
            except UnicodeDecodeError:  # pragma: no cover — defensive
                logger.warning(
                    "[SecretScrub] secret %s plaintext is not UTF-8; "
                    "skipped from the scrub set",
                    secret_id,
                )
                continue
            if not plaintext:
                continue
            # Markers are NEVER scrubbed (§2.4): a stored value that
            # IS itself a marker is skipped — replacing a marker with
            # a marker is at best confusing, and the fullmatch skip
            # is the pin-(c) guarantee.
            if ENS_SECRET_MARKER_RE_COMPILED.fullmatch(plaintext):
                continue
            # Marker-template SUBSTRING exclusion (§2.4
            # idempotency-under-multi-pass — documented exclusion;
            # pathological values like ``"SECRET"``, ``"ENS_"``,
            # ``"S"`` are reachable via agent-controlled mint
            # calls). Cheap membership check; the marker template
            # is short (~40 chars) and the per-pair skip is one
            # ``in`` test.
            if plaintext in _marker_template:
                logger.warning(
                    "[SecretScrub] secret %s plaintext is a "
                    "substring of the marker template (%d chars); "
                    "excluded from the scrub set (documented §2.4 "
                    "idempotency exclusion — multi-pass scrubbing "
                    "would corrupt already-minted markers)",
                    secret_id,
                    len(plaintext),
                )
                continue
            pairs.append((secret_id, plaintext))
        # Stable sort: length-desc primary, query-order (created_at)
        # tiebreak.
        pairs.sort(key=lambda p: -len(p[1]))
        return pairs

    # ── Core string substitution ────────────────────────────────

    def scrub_text(
        self, text: str, *, _pairs: Optional[list[tuple[str, str]]] = None
    ) -> tuple[str, list[str]]:
        """Exact-match scrub of one string. Returns
        ``(new_text, substituted_secret_ids)``.

        Empty-set fast path: when the metadata index has no rows,
        returns ``(text, [])`` after one ``len()`` check — the
        autonomy-first no-op (§2.5).
        """
        if not text:
            return text, []
        if _pairs is None:
            rows = self._current_rows()
            if not rows:
                return text, []
            _pairs = self._plaintext_pairs(rows)
            if not _pairs:
                return text, []
        substituted: list[str] = []
        for secret_id, plaintext in _pairs:
            if plaintext in text:
                marker = mint_marker(secret_id)
                text = text.replace(plaintext, marker)
                substituted.append(secret_id)
        return text, substituted

    def _audit_substitutions(
        self,
        substituted_ids: list[str],
        *,
        actor: str,
    ) -> None:
        """Stamp one ``use_marker``/``context='scrub'`` audit row per
        distinct substituted secret (design §3.4 — per-substitution
        only; a no-substitution scrub writes ZERO rows).

        Best-effort: an audit failure NEVER fails the scrub (the
        row-stamping is observability; scrubbing correctness is the
        security control). Logged on failure.
        """
        if not substituted_ids:
            return
        for secret_id in substituted_ids:
            try:
                self._service.record_scrub_substitution(
                    secret_id=secret_id,
                    actor=actor or SCRUB_ACTOR_FALLBACK,
                )
            except Exception:
                logger.exception(
                    "[SecretScrub] audit row emission failed for "
                    "secret %s (scrub already applied)",
                    secret_id,
                )

    # ── S1 — message-content scrub (in place, id() preserved) ──

    def scrub_message_inplace(
        self,
        message: Any,
        *,
        actor: str = SCRUB_ACTOR_FALLBACK,
        _pairs: Optional[list[tuple[str, str]]] = None,
    ) -> list[str]:
        """Scrub ONE LangChain message's secret-bearing content IN
        PLACE (S1 contract: the message object keeps its ``id()``,
        its ``.content`` is replaced — test pin (a)).

        Covers ONLY ``ToolMessage`` content (str or content-block
        list). ``AIMessage.content`` is NEVER scrubbed (§2.4 R8) and
        args are NEVER scrubbed in live state (they must stay intact
        for tool execution and same-turn loop repair — S2 owns the
        persisted copy). Other message types are untouched.

        Returns the list of substituted secret ids (audit is the
        CALLER's responsibility here — batch sites pass ``_pairs``
        and audit once per batch via :meth:`audit_substitutions`).
        """
        rows = self._current_rows()
        if not rows:
            return []
        if _pairs is None:
            _pairs = self._plaintext_pairs(rows)
            if not _pairs:
                return []
        if not self._is_tool_message(message):
            return []
        substituted: list[str] = []
        content = message.content
        if isinstance(content, str):
            new_text, subs = self.scrub_text(content, _pairs=_pairs)
            if subs:
                message.content = new_text
                substituted.extend(subs)
        elif isinstance(content, list):
            # Multimodal content blocks — scrub "text" fields only.
            changed = False
            new_blocks: list[Any] = []
            for block in content:
                if (
                    isinstance(block, dict)
                    and isinstance(block.get("text"), str)
                ):
                    new_text, subs = self.scrub_text(
                        block["text"], _pairs=_pairs
                    )
                    if subs:
                        block = {**block, "text": new_text}
                        changed = True
                        substituted.extend(subs)
                new_blocks.append(block)
            if changed:
                message.content = new_blocks
        return substituted

    def audit_substitutions(
        self, substituted_ids: list[str], *, actor: str
    ) -> None:
        """Public audit entry for batch sites (S1/S2 audit ONCE per
        batch, per distinct secret)."""
        self._audit_substitutions(substituted_ids, actor=actor)

    # ── S2 — persisted-snapshot copy scrub (live objects intact) ──

    def scrub_message_copy(self, message: Any) -> Any:
        """Return a SCRUBBED COPY of one message (S2 contract).

        The input message object is NEVER mutated (the snapshot at
        the saver boundary carries LIVE state objects — the
        ``chan.checkpoint()`` value is the live list). The copy is a
        ``model_copy(deep=True)`` (pydantic v2 — langchain messages)
        with the secret-bearing fields replaced:

        * ``ToolMessage``: ``content`` (str or blocks).
        * ``AIMessage``: ``tool_calls[*].args`` (dict walk) +
          ``additional_kwargs["tool_calls"]`` OpenAI-flavor
          ``function.arguments`` strings. ``AIMessage.content``
          NOT scrubbed (§2.4 R8).

        Non-message values pass through UNCHANGED (same object).
        Idempotent under re-save: already-marker fields fullmatch
        the skip guard and compare clean (pin (c)).
        """
        if not self._is_base_message(message):
            return message
        rows = self._current_rows()
        if not rows:
            return message
        pairs = self._plaintext_pairs(rows)
        if not pairs:
            return message
        copied = message.model_copy(deep=True)
        substituted: list[str] = []
        if self._is_tool_message(copied):
            substituted.extend(
                self._scrub_tool_message_fields(copied, pairs)
            )
        elif self._is_ai_message(copied):
            substituted.extend(self._scrub_ai_message_fields(copied, pairs))
        self._audit_substitutions(
            substituted, actor=self._actor_from_message(copied)
        )
        return copied

    def scrub_messages_copy(self, values: list[Any]) -> list[Any]:
        """Scrub a LIST of snapshot values (S2 — one unwrap set per
        put call; audit once per call). Returns the ORIGINAL list
        object when nothing changed (so the caller's ``is not``
        change-detection stays honest), else a NEW list; live
        objects are shared UNCHANGED where no copy was needed.
        """
        rows = self._current_rows()
        if not rows:
            return values
        pairs = self._plaintext_pairs(rows)
        if not pairs:
            return values
        out: list[Any] = []
        changed = False
        for value in values:
            if self._is_base_message(value):
                copied = value.model_copy(deep=True)
                substituted: list[str] = []
                if self._is_tool_message(copied):
                    substituted.extend(
                        self._scrub_tool_message_fields(copied, pairs)
                    )
                elif self._is_ai_message(copied):
                    substituted.extend(
                        self._scrub_ai_message_fields(copied, pairs)
                    )
                if substituted:
                    changed = True
                    self._audit_substitutions(
                        substituted,
                        actor=self._actor_from_message(copied),
                    )
                out.append(copied)
            else:
                out.append(value)
        return out if changed else values

    # ── Field-level internals ───────────────────────────────────

    def _scrub_tool_message_fields(
        self, message: Any, pairs: list[tuple[str, str]]
    ) -> list[str]:
        """Scrub a (copied) ToolMessage's content in place on the
        copy. Returns substituted ids."""
        substituted: list[str] = []
        content = message.content
        if isinstance(content, str):
            new_text, subs = self.scrub_text(content, _pairs=pairs)
            if subs:
                message.content = new_text
                substituted.extend(subs)
        elif isinstance(content, list):
            new_blocks: list[Any] = []
            changed = False
            for block in content:
                if (
                    isinstance(block, dict)
                    and isinstance(block.get("text"), str)
                ):
                    new_text, subs = self.scrub_text(
                        block["text"], _pairs=pairs
                    )
                    if subs:
                        block = {**block, "text": new_text}
                        changed = True
                        substituted.extend(subs)
                new_blocks.append(block)
            if changed:
                message.content = new_blocks
        return substituted

    def _scrub_ai_message_fields(
        self, message: Any, pairs: list[tuple[str, str]]
    ) -> list[str]:
        """Scrub a (copied) AIMessage's tool-call args in place on
        the copy — the P2 persisted-args contract (design §2.2 S2).

        Covers:

        * ``tool_calls[*].args`` — the LangChain canonical shape
          (list of dicts ``{name, args, id, type}``).
        * ``additional_kwargs["tool_calls"][*]["function"]
          ["arguments"]`` — the OpenAI-flavor fallback where
          arguments ride as a JSON STRING. Exact-match replacement
          inside the JSON string is JSON-safe: when the raw
          plaintext matches literally, the marker replacement is
          also literal (marker chars are JSON-inert); when the
          plaintext contains JSON-escape characters it appears
          escaped in the string and the raw exact-match simply
          doesn't fire (no corruption either way).

        ``AIMessage.content`` NOT scrubbed (§2.4 R8).
        """
        substituted: list[str] = []
        tool_calls = getattr(message, "tool_calls", None)
        if isinstance(tool_calls, list):
            new_calls: list[Any] = []
            changed = False
            for call in tool_calls:
                if isinstance(call, dict) and isinstance(
                    call.get("args"), dict
                ):
                    new_args, subs = self._scrub_json_like(
                        call["args"], pairs
                    )
                    if subs:
                        call = {**call, "args": new_args}
                        changed = True
                        substituted.extend(subs)
                new_calls.append(call)
            if changed:
                message.tool_calls = new_calls
        additional = getattr(message, "additional_kwargs", None)
        if isinstance(additional, dict):
            openai_calls = additional.get("tool_calls")
            if isinstance(openai_calls, list):
                new_openai: list[Any] = []
                changed = False
                for call in openai_calls:
                    if isinstance(call, dict):
                        fn = call.get("function")
                        if (
                            isinstance(fn, dict)
                            and isinstance(fn.get("arguments"), str)
                        ):
                            new_args_str, subs = self.scrub_text(
                                fn["arguments"], _pairs=pairs
                            )
                            if subs:
                                call = {
                                    **call,
                                    "function": {
                                        **fn,
                                        "arguments": new_args_str,
                                    },
                                }
                                changed = True
                                substituted.extend(subs)
                    new_openai.append(call)
                if changed:
                    additional["tool_calls"] = new_openai
        return substituted

    def _scrub_json_like(
        self, value: Any, pairs: list[tuple[str, str]]
    ) -> tuple[Any, list[str]]:
        """Recursively scrub strings inside dict / list structures
        (tool-call args walk). Returns ``(new_value, substituted)``.
        Non-string scalars pass through; the walk copies containers
        ONLY when a substitution actually happened inside them."""
        if isinstance(value, str):
            new_text, subs = self.scrub_text(value, _pairs=pairs)
            return new_text, subs
        if isinstance(value, dict):
            new_dict: dict[Any, Any] = {}
            changed = False
            subs: list[str] = []
            for key, item in value.items():
                new_item, item_subs = self._scrub_json_like(item, pairs)
                if item_subs:
                    changed = True
                    subs.extend(item_subs)
                new_dict[key] = new_item
            return (new_dict if changed else value), subs
        if isinstance(value, list):
            new_list: list[Any] = []
            changed = False
            subs: list[str] = []
            for item in value:
                new_item, item_subs = self._scrub_json_like(item, pairs)
                if item_subs:
                    changed = True
                    subs.extend(item_subs)
                new_list.append(new_item)
            return (new_list if changed else value), subs
        return value, []

    # ── Type predicates (duck-typed; lazy imports) ──────────────

    @staticmethod
    def _is_base_message(value: Any) -> bool:
        """Whether ``value`` is a LangChain ``BaseMessage`` (duck-
        typed on ``model_copy`` + ``content`` + ``type`` — avoids a
        module-level langchain import; the concrete classes are
        resolved lazily where needed)."""
        return (
            hasattr(value, "model_copy")
            and hasattr(value, "content")
            and hasattr(value, "type")
        )

    @staticmethod
    def _is_tool_message(value: Any) -> bool:
        """Whether ``value`` is a ``ToolMessage`` (lazy class check
        with a ``type``-tag fallback for exotic ToolNode subclasses —
        design §9 residual)."""
        try:
            from langchain_core.messages import ToolMessage

            if isinstance(value, ToolMessage):
                return True
        except Exception:  # pragma: no cover — defensive
            pass
        return getattr(value, "type", None) == "tool"

    @staticmethod
    def _is_ai_message(value: Any) -> bool:
        """Whether ``value`` is an ``AIMessage`` (lazy class check
        with a ``type``-tag fallback)."""
        try:
            from langchain_core.messages import AIMessage

            if isinstance(value, AIMessage):
                return True
        except Exception:  # pragma: no cover — defensive
            pass
        return getattr(value, "type", None) == "ai"

    @staticmethod
    def _actor_from_message(message: Any) -> str:
        """Best-effort actor for S2 audit rows (the snapshot itself
        carries no actor; the wiring site's config does). Falls to
        the :data:`SCRUB_ACTOR_FALLBACK` sentinel."""
        return SCRUB_ACTOR_FALLBACK


# ─────────────────────────────────────────────────────────────────────
# Wiring-site helpers (the three call sites call THESE)
# ─────────────────────────────────────────────────────────────────────
#
# Each helper resolves the process scrubber (None → identity) and
# swallows EVERY internal error (the scrub is defense-in-depth — it
# must never break the tools node, the checkpoint write, or the
# enqueue). The swallow is deliberate and logged.


def scrub_tool_results_inplace(
    messages: Any, *, actor: str = SCRUB_ACTOR_FALLBACK
) -> Any:
    """S1 entry — scrub REAL ``ToolMessage.content`` IN PLACE for a
    ToolNode result payload, BEFORE it joins live graph state.

    ``messages`` is the ``bare.ainvoke`` return — one of (pinned
    langgraph-prebuilt 1.0.8 ``_combine_tool_outputs``):

    * ``{"messages": [ToolMessage, ...]}`` — the canonical
      dict-state shape (the common case);
    * ``[ToolMessage | Command, ...]`` — the list-state shape,
      possibly with interleaved ``Command`` updates;
    * a bare ``Command`` (``.update`` carries the messages dict).

    All shapes are scrubbed in place (the payload is the node's own
    return value — nobody else holds it yet) with the message
    OBJECTS preserved (``id()`` unchanged — pin (a)); ``AIMessage``
    objects are NEVER touched here (content per §2.4 R8; args stay
    intact in live state per §2.2 S2 — the persisted copy is S2's
    job). Non-payload shapes (None / scalars) pass through.

    Synthetic placeholder ToolMessages (§2.1 site 5) are NOT
    special-cased: they carry fixed placeholder text that can never
    contain a stored value, so scrubbing them is a guaranteed no-op
    and skipping them would require maintaining a placeholder-text
    list that drifts. Documented choice (commission B).

    Errors are swallowed (logged) — the tools node must never fail
    because of the scrub.
    """
    scrubber = get_process_scrubber()
    if scrubber is None:
        return messages
    try:
        rows = scrubber._current_rows()
        if not rows:
            return messages
        pairs = scrubber._plaintext_pairs(rows)
        if not pairs:
            return messages
        all_subs: list[str] = []

        def _scrub_msg_list(msg_list: Any) -> None:
            for message in msg_list:
                if _is_langgraph_command(message):
                    update = getattr(message, "update", None)
                    if isinstance(update, dict):
                        inner = update.get("messages")
                        if isinstance(inner, list):
                            _scrub_msg_list(inner)
                    continue
                all_subs.extend(
                    scrubber.scrub_message_inplace(
                        message, actor=actor, _pairs=pairs
                    )
                )

        if isinstance(messages, dict):
            inner = messages.get("messages")
            if isinstance(inner, list):
                _scrub_msg_list(inner)
        elif isinstance(messages, list):
            _scrub_msg_list(messages)
        elif _is_langgraph_command(messages):
            update = getattr(messages, "update", None)
            if isinstance(update, dict):
                inner = update.get("messages")
                if isinstance(inner, list):
                    _scrub_msg_list(inner)
        scrubber.audit_substitutions(all_subs, actor=actor)
    except Exception:
        logger.exception(
            "[SecretScrub] S1 results scrub failed; returning "
            "unscrubbed tool result (defense-in-depth must not "
            "break the tools node)"
        )
    return messages


def _is_langgraph_command(value: Any) -> bool:
    """Duck-type a langgraph ``Command`` without a module-level
    langgraph import (the repo conftest installs global langgraph
    mocks; the concrete class is resolved lazily)."""
    type_name = type(value).__name__
    if type_name != "Command":
        return False
    return hasattr(value, "update")


def scrub_saver_write_args(
    method_name: str,
    args: tuple,
    kwargs: dict,
) -> tuple[tuple, dict]:
    """S2 entry — scrub the PERSISTED SNAPSHOT inside the saver-proxy
    write methods (``aput`` / ``aput_writes`` / ``put`` / ``put_writes``).

    Transforms COPIES only:

    * ``aput`` / ``put`` — ``args = (config, checkpoint, metadata,
      new_versions)``: a shallow-copied checkpoint dict with a copied
      ``channel_values`` dict whose message lists are scrubbed deep
      copies (mirrors the saver's own ``copy = checkpoint.copy()``
      discipline — live objects untouched).
    * ``aput_writes`` / ``put_writes`` — ``args = (config, writes,
      task_id[, task_path])``: a new writes list whose message-list
      values are scrubbed deep copies.

    Live state objects are NEVER mutated. Idempotent under re-save
    (markers fullmatch the skip guard). Before any SecretService
    exists this is an identity function (zero behavior change);
    errors are swallowed (logged) — a scrub failure must never fail
    a checkpoint write.
    """
    scrubber = get_process_scrubber()
    if scrubber is None:
        return args, kwargs
    try:
        if method_name in ("aput", "put"):
            if not args or not isinstance(args[1], dict):
                return args, kwargs
            checkpoint = args[1]
            channel_values = checkpoint.get("channel_values")
            if not isinstance(channel_values, dict):
                return args, kwargs
            new_channel_values: dict[str, Any] = {}
            changed = False
            for channel, value in channel_values.items():
                if isinstance(value, list) and any(
                    SecretPersistenceScrubber._is_base_message(item)
                    for item in value
                ):
                    new_value = scrubber.scrub_messages_copy(value)
                    if new_value is not value:
                        changed = True
                        new_channel_values[channel] = new_value
                    else:
                        new_channel_values[channel] = value
                else:
                    new_channel_values[channel] = value
            if not changed:
                return args, kwargs
            checkpoint_copy = dict(checkpoint)
            checkpoint_copy["channel_values"] = new_channel_values
            return (args[0], checkpoint_copy) + tuple(args[2:]), kwargs
        if method_name in ("aput_writes", "put_writes"):
            if len(args) < 2 or not isinstance(args[1], (list, tuple)):
                return args, kwargs
            writes = args[1]
            new_writes: list[Any] = []
            changed = False
            for write in writes:
                if (
                    isinstance(write, tuple)
                    and len(write) == 2
                    and isinstance(write[1], list)
                    and any(
                        SecretPersistenceScrubber._is_base_message(item)
                        for item in write[1]
                    )
                ):
                    new_value = scrubber.scrub_messages_copy(write[1])
                    if new_value is not write[1]:
                        changed = True
                        new_writes.append((write[0], new_value))
                        continue
                new_writes.append(write)
            if not changed:
                return args, kwargs
            return (args[0], new_writes) + tuple(args[2:]), kwargs
    except Exception:
        logger.exception(
            "[SecretScrub] S2 saver-snapshot scrub failed for %s; "
            "persisting unscrubbed snapshot (defense-in-depth must "
            "not break the checkpoint write)",
            method_name,
        )
    return args, kwargs


def scrub_enqueued_body(message: str, *, actor: str) -> str:
    """S3 entry — scrub the persisted queue-row body before the
    ``MessageQueue`` INSERT (defense-in-depth, design §2.1 site 2).

    Covers the queue body string ONLY (human-prompt text +
    tool-args-adjacent paste echoes; tool args do NOT pass through
    this path per §2.1). ``metadata`` is out of scope (the design
    pins the body; the metadata dict is structured caller data, not
    free text).

    Sync by contract — ``_prepare_enqueued_message`` runs inside
    ``asyncio.to_thread`` already (the caller's sync-write lane).
    Identity before any SecretService exists; errors swallowed.
    """
    scrubber = get_process_scrubber()
    if scrubber is None:
        return message
    try:
        rows = scrubber._current_rows()
        if not rows:
            return message
        pairs = scrubber._plaintext_pairs(rows)
        if not pairs:
            return message
        new_text, substituted = scrubber.scrub_text(message, _pairs=pairs)
        scrubber.audit_substitutions(substituted, actor=actor)
        return new_text
    except Exception:
        logger.exception(
            "[SecretScrub] S3 enqueue-body scrub failed; persisting "
            "unscrubbed body (defense-in-depth must not break the "
            "enqueue)"
        )
        return message


__all__ = [
    "DEFAULT_ID_SET_TTL_SECONDS",
    "SCRUB_ACTOR_FALLBACK",
    "SCRUB_AUDIT_CONTEXT",
    "SecretPersistenceScrubber",
    "get_process_scrubber",
    "register_secret_service",
    "reset_process_registry",
    "scrub_enqueued_body",
    "scrub_saver_write_args",
    "scrub_tool_results_inplace",
]
