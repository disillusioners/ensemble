"""Live-view subsystem registry (Phase 1).

Owns the per-name → disk-path / policy mapping for the
``/views/<root>/<rel>`` route family. The registry is a thin
wrapper over the config-tree section ``live_views.roots`` plus a
``LiveViewsService`` that resolves a name + relative path into an
absolute on-disk path (and an optional content-type) WITH the full
path-traversal guard surface.

The registry is **read-only at request time** — it is populated
once at lifespan start from ``config.live_views.roots`` and
immutable thereafter. Adding a fourth root = config edit + restart
(``restart to flip`` matches the house ``live_views.*`` config
precedent set by ``tmp_image_store_max_bytes`` /
``tmp_image_cleanup_*``).

SECURITY HARD REQUIREMENTS (architect ruling, 2026-10-07):

* Path-traversal: reject ``..``, encoded ``%2e``, double-encoded
  ``%252e``, absolute path segments, null bytes, backslash tricks;
  enforce ``os.path.realpath`` containment within the resolved
  root dir (symlink escapes included).
* Read-only: ``GET/HEAD`` only — write/delete/list endpoints are
  NOT provided (the router exposes GET/HEAD exclusively).
* Content-Type via explicit extension→MIME map; NEVER sniff
  content; ``tmp-images`` MUST use sidecar MIME (not extension)
  per the architect ruling that already gates
  ``/api/tmp_images/<id>``.
* Unknown root-name / disabled / removed roots → uniform 404 with
  no path disclosure (no message that says "root exists but path
  missing" vs "root missing" — the caller can probe neither).

Three seed roots ship by default (config-driven; operator-editable):

* ``designer-artifact`` (project_scoped, REWORK 2026-10-07 M2) —
  the canonical
  ``<workdir>/.agents/shared/planning/<feature>/design/mockups/``
  subtree of the project named in the URL. URL shape
  ``/views/designer-artifact/<project_shortname>/<feature>/design/mockups/<file>``:
  the first URL segment after the root is the project shortname
  (anonymous-resolvable, exactly like ``planning``), and the
  rel must include the ``design/mockups`` subpath as a
  contiguous subsequence (REWORK M3, structural enforcement
  via the new ``required_rel_subpath`` config field). The
  pre-M2 design anchored the path on the calling instance's
  project workdir via ``request.state.instance_id``; nothing in
  the daemon ever set that attribute, so every anonymous
  browser hit on ``/views/designer-artifact/<rel>`` was a
  permanent 404. The project_scoped re-shape closes that gap
  by making the URL pattern carry the project locator.
* ``planning`` (project_scoped) — URL pattern
  ``/views/planning/<project_shortname>/<rel>`` resolves to
  ``<project_workdir>/.agents/shared/planning/<rel>``. This is
  the cross-project, project-aware reader; the first URL segment
  after the root is a project shortname, not a path component.
* ``tmp-images`` (tmp_images) — thin shim over the existing
  ``TmpImageStore`` substrate. MIME comes from the sidecar
  record, NOT the extension (architect risk #7 ruling).
"""

from __future__ import annotations

import html
import logging
import os
import re
import secrets
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Callable, Literal

if TYPE_CHECKING:
    from daemon.config import LiveViewsConfig, LiveViewsRootConfig
    from daemon.services.tmp_image_store import TmpImageStore

logger = logging.getLogger(__name__)


# Root-name charset: kebab-case identifier, length 1..64, no
# leading/trailing hyphen, no underscores (hyphen-only matches the
# URL path convention for first-class daemon routes). The router
# also rejects anything that does not match this regex before any
# filesystem call.
_ROOT_NAME_REGEX = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")

# Maximum file size we'll ever serve. Soft cap — anything above
# this is 404'd to avoid serving large binaries through the daemon
# (the architect note: the route is for artifacts and short-lived
# text/markdown, not a generic file server). PUBLIC name — the
# router imports this directly (was module-private ``_MAX_SERVED_BYTES``
# pre-hygiene round; both A and C workers flagged the import as
# reaching into the private surface). The leading underscore
# signaled "implementation detail" but the router is the canonical
# consumer, so the public name is the right call.
MAX_SERVED_BYTES: int = 32 * 1024 * 1024  # 32 MiB

# Minimal extension→MIME map for the Phase 1 surface. Anything
# not in this map returns ``application/octet-stream`` (the safe
# "I don't know" answer — the browser still renders the bytes
# correctly when the caller already knows the file shape, e.g. an
# agent that already fetched and previewed the artifact). Sniffing
# is FORBIDDEN (architect ruling — same as the tmp-images route).
_EXT_TO_MIME: dict[str, str] = {
    "html": "text/html; charset=utf-8",
    "htm": "text/html; charset=utf-8",
    "css": "text/css; charset=utf-8",
    "js": "application/javascript; charset=utf-8",
    "mjs": "application/javascript; charset=utf-8",
    "json": "application/json; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "markdown": "text/markdown; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
    "svg": "image/svg+xml; charset=utf-8",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "pdf": "application/pdf",
    "asc": "text/plain; charset=utf-8",  # designer's text-native wireframe
    "mmd": "text/plain; charset=utf-8",  # designer's text-native mermaid
    "xml": "application/xml; charset=utf-8",
    "yml": "application/yaml; charset=utf-8",
    "yaml": "application/yaml; charset=utf-8",
}


def _normalize_text(text: str) -> str:
    """NFKC-normalize + strip control chars before any regex check.

    Defense against unicode lookalikes that survive a 0x2E = '.'
    sanity check (e.g. fullwidth period '．' = U+FF0E). Returns
    the text in a form that ``str.lower()`` and the
    ``_ROOT_NAME_REGEX`` check both accept.
    """
    return unicodedata.normalize("NFKC", text).strip()


def is_well_formed_root_name(name: str) -> bool:
    """Return True iff ``name`` is a legal root-name token.

    Used by both the router (rejects bad names with a uniform 404
    before any resolver call) and the tool (rejects bad names with
    a typed error so the agent gets feedback).
    """
    if not name:
        return False
    normalized = _normalize_text(name)
    if normalized != name:
        # Any leading/trailing whitespace or unicode-folding
        # difference is a hard reject.
        return False
    return _ROOT_NAME_REGEX.match(name) is not None


def is_well_formed_rel_path(rel: str) -> bool:
    """Return True iff ``rel`` is a legal relative path token.

    A legal token has:

    * no null bytes
    * no control chars
    * no backslashes
    * no absolute segments (must not start with ``/``)
    * no encoded forms (``%2e``, ``%2f``, ``%00`` — the URL
      framework will decode percent-escapes before this check,
      so we only see the decoded form)
    * no ``..`` segments (POSIX and Windows both honor
      ``os.path.normpath`` semantics; we forbid them at the
      SEMENT level so a future encoder that re-encodes
      ``..`` cannot bypass the check)
    """
    if not rel:
        return False
    # Null bytes / control chars — REWORK 2026-10-07 (m3):
    # the C0 range (ord < 0x20) is rejected as before. The
    # DEL char (0x7F) and the C1 range (U+0080–U+009F) are
    # now ALSO rejected — they are control chars in disguise
    # that survive a regex check on printable ASCII but a
    # terminal or a misconfigured HTTP middlebox can render
    # them as escape sequences (e.g. CSI 0x9B). The
    # architectural rule is the same: the rel must be
    # printable, no control chars at all.
    if any(
        (ord(c) < 0x20)
        or (ord(c) == 0x7F)
        or (0x80 <= ord(c) <= 0x9F)
        for c in rel
    ):
        return False
    if "\\" in rel:
        return False
    if rel.startswith("/"):
        return False
    # The URL framework decodes percent-escapes before we see the
    # string, so the form below is the post-decode form. The
    # decoded form is what we validate. (A future router that
    # chose not to decode MUST apply this check to the encoded
    # form, but that is a router-level concern, not this one.)
    if ".." in PurePosixPath(rel).parts:
        return False
    return True


def extension_for(rel: str) -> str:
    """Return the lowercase extension WITHOUT leading dot, or ``""``.

    Examples::

        "foo/bar.html" -> "html"
        "foo/bar.HTML" -> "html"
        "foo/bar"      -> ""
        "foo/.hidden"  -> ""  (we treat dotfiles as no-ext)
        "foo/bar."     -> ""
    """
    name = os.path.basename(rel)
    _, ext = os.path.splitext(name)
    if not ext or ext == ".":
        return ""
    return ext.lstrip(".").lower()


def mime_for_extension(ext: str) -> str:
    """Return the explicit MIME for ``ext`` or the safe fallback.

    The map is small and CLOSED — anything not in ``_EXT_TO_MIME``
    returns ``application/octet-stream``. We never sniff content
    (architect ruling, mirrors the tmp-images path) AND we never
    consult the stdlib ``mimetypes`` registry: a host
    ``/etc/mime.types`` could map an agent-authored extension to
    ``text/html`` and the browser would execute the result on the
    daemon origin. The safe default for any unknown extension is
    ``application/octet-stream`` (the spec's allowed default).
    """
    if not ext:
        return "application/octet-stream"
    return _EXT_TO_MIME.get(ext, "application/octet-stream")


# ─────────────────────────────────────────────────────────────────
# Base-URL resolution chain (Phase 2: Host-capture auto-detect)
# ─────────────────────────────────────────────────────────────────

# Syntactic Host-header validation. Accepts the post-parse shape of
# a syntactically valid HTTP ``Host`` header value (RFC 7230 §5.4):
# a hostname, an IPv4 dotted-quad, or a bracketed IPv6 literal,
# optionally followed by ``:port``. Path-bearing values, userinfo,
# whitespace, and CR/LF (header smuggling) are rejected. The
# pattern is intentionally tight — it accepts only what is
# structurally a Host and refuses everything else.
#
# RFC 1035 caps hostname length at 253 chars; port is 1..65535
# (max 5 digits). The total ``len(value) > 259`` cap below covers
# 253 + ":" + 5.
_HOST_HEADER_PATTERN = re.compile(
    r"^"
    r"(?P<host>"
    r"(?:\[[0-9a-fA-F:]+\])"      # bracketed IPv6 literal
    r"|"
    r"[A-Za-z0-9](?:[A-Za-z0-9.\-]{0,251}[A-Za-z0-9])?"  # host / IPv4 (max 253)
    r")"
    r"(?::(?P<port>\d{1,5}))?"      # optional :port (1..65535 enforced below)
    r"$"
)

# Bind-host wildcards. ``0.0.0.0`` and ``::`` mean "all interfaces";
# an empty value would be a misconfig but the daemon defaults to
# ``0.0.0.0`` per ``DaemonConfig.host``. All three collapse to
# ``127.0.0.1`` for URL minting so the daemon never produces an
# obviously-nonsense URL like ``http://0.0.0.0:8079/...`` (browsers
# can't resolve 0.0.0.0; some libraries turn it into a 0.0.0.0:8079
# string in a chat client and the link is dead on click).
_BIND_WILDCARDS: frozenset[str] = frozenset({"0.0.0.0", "::", ""})


# Module-level once-per-process throttle for the resolver's
# operator warnings (downgrade / bind-tier / path-relative mints).
# Per-tier: one warning per (kind, tier) tuple is enough — repeated
# identical messages would spam the daemon log on every ``view_link``
# call from a long-running LLM session. ``_once_warned`` survives
# the lifetime of the daemon process; the tracker is reset by a
# process restart (acceptable — the warning is operator-actionable,
# not per-request noise).
_once_warned: set[str] = set()


def _warn_once(key: str, message: str) -> None:
    """Emit ``logger.warning(message)`` exactly once per process for ``key``.

    The tracker is module-local (``_once_warned``) and survives only
    for the process lifetime; on daemon restart the warning will
    fire once again for the first mint on the new tier. The key
    names the specific tier+kind so distinct warnings don't
    suppress each other.
    """
    if key in _once_warned:
        return
    _once_warned.add(key)
    logger.warning(message)


def _is_valid_host_header(value: str) -> bool:
    """Return True iff ``value`` is a syntactically valid Host header.

    Per ``_HOST_HEADER_PATTERN``: hostname, IPv4, or bracketed IPv6,
    with optional ``:port`` (1..65535). Rejects empty, whitespace,
    CR/LF (header smuggling), path-bearing values (``host/x``),
    userinfo (``user@host``), and out-of-range ports. No semantic
    check (an attacker can spoof any value; see ``HostRecorder``
    docstring for the trust model + mitigation).
    """
    if not value:
        return False
    # Header-smuggling protection: strip CR / LF / tab / space that
    # could indicate a multi-line header injection attempt.
    if any(c in value for c in "\r\n\t "):
        return False
    if len(value) > 259:
        return False
    m = _HOST_HEADER_PATTERN.match(value)
    if not m:
        return False
    port = m.group("port")
    if port is not None:
        try:
            p = int(port)
        except ValueError:
            return False
        if p < 1 or p > 65535:
            return False
    return True


def _split_host_port(value: str) -> tuple[str, int | None]:
    """Split a validated Host header value into ``(host, port)``.

    Caller guarantees ``value`` matched ``_HOST_HEADER_PATTERN``;
    the IPv6-bracket case is handled explicitly because ``rpartition``
    is not bracket-aware.
    """
    if value.startswith("["):
        end = value.index("]")
        host = value[1:end]
        rest = value[end + 1 :]
        if rest.startswith(":"):
            return host, int(rest[1:])
        return host, None
    if ":" in value:
        host, _, port_str = value.rpartition(":")
        return host, int(port_str)
    return value, None


def _normalize_bind_host(host: str) -> str | None:
    """Map the operator-configured bind host to a URL-mint host.

    Wildcards (``0.0.0.0``, ``::``, empty) collapse to
    ``127.0.0.1`` — the daemon cannot realistically mint a URL
    the operator will paste into a chat client against the
    literal wildcard. ``localhost`` survives as-is (operator
    intent — same shape as the bind-default region above).
    """
    if not host or host in _BIND_WILDCARDS:
        return "127.0.0.1"
    return host


class HostRecorder:
    """Thread-safe recorder of the most recent inbound Host header.

    Lives at ``app.state.host_recorder``; the ``HostCaptureMiddleware``
    (daemon/api.py) feeds it from each HTTP request the daemon
    serves. The ``BaseURLResolver`` reads the latest record to mint
    fully-qualified ``view_link`` URLs.

    **Trust model.** An anonymous client can send any value in the
    Host header — HTTP Host is not authenticated. The recorder
    accepts the literal value; the resolver restricts its inputs
    to syntactically valid Host shapes (no userinfo, no path, no
    whitespace) so a malicious header cannot produce a malformed
    URL. The risk that remains is that an attacker can poison the
    minted base URL with a chosen hostname — the documented
    trade-off; the operator override
    (``config.live_views.external_base_url``) is the canonical
    mitigation when the deployment has a known public hostname.

    **Concurrency.** The daemon runs on a single asyncio loop
    (uvicorn), but ``add_middleware`` + lifespan wiring run
    inside the loop too — a defensive lock keeps the state
    strictly race-free.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._host: str | None = None
        self._port: int | None = None
        self._scheme: str = "http"
        self._recorded_at: float = 0.0
        # Phase 2 follow-up: once an HTTPS request has been
        # observed via X-Forwarded-Proto, the recorder remembers
        # the fact even if the current host is later dropped
        # (malformed Host, daemon restart, etc.). The resolver
        # uses this flag to surface the http-downgrade mint case
        # — the operator hasn't changed the topology, but the
        # mint silently dropped to ``http://...`` and the next
        # chat-client click would be over plain HTTP. See
        # ``BaseURLResolver.resolve``.
        self._saw_https: bool = False

    def record(
        self,
        host: str | None,
        scheme: str | None = None,
    ) -> None:
        """Record a ``(host, scheme)`` tuple from an inbound request.

        ``host`` is the raw ``Host`` header value (post-decode);
        ``scheme`` is the ``X-Forwarded-Proto`` value (or ``None``
        for the default ``http``). Invalid Host values are silently
        dropped — middleware errors must NEVER break the request
        path. The recorder's last-write-wins shape means the most
        recent request's host becomes the next minted URL base;
        this is acceptable for the use case (a daemon-fronted
        deployment has a consistent public hostname; localhost
        dev mint the localhost URL when the dev hits the daemon).
        """
        if not host or not _is_valid_host_header(host):
            return
        split_host, split_port = _split_host_port(host)
        # X-Forwarded-Proto is the canonical scheme-injection header
        # when the daemon sits behind an OAuth proxy / TLS terminator
        # that does not otherwise reach the client. Default ``http``
        # covers direct-connect (no proxy) and the common case where
        # the proxy passes X-Forwarded-Proto: https only on TLS.
        # RFC 7239 comma-chained proxies pass multi-hop values like
        # "https, http" — take the FIRST hop's scheme (the scheme
        # the client actually used to reach the trust boundary).
        # An exact-membership check would otherwise silently
        # downgrade the whole chain to the http default.
        chosen_scheme = (scheme or "http").split(",", 1)[0].strip().lower()
        if chosen_scheme not in ("http", "https"):
            chosen_scheme = "http"
        with self._lock:
            self._host = split_host
            self._port = split_port
            self._scheme = chosen_scheme
            self._recorded_at = time.monotonic()
            if chosen_scheme == "https":
                self._saw_https = True

    def latest(self) -> tuple[str, int | None, str] | None:
        """Return ``(host, port, scheme)`` of the latest record, or None.

        ``port`` is the port the client used to reach us — taken
        from the Host header when present, None otherwise (the
        caller decides whether to fall back to the configured
        bind port for the URL mint). Returns None when the
        recorder has never seen a valid request.
        """
        with self._lock:
            if self._host is None:
                return None
            return (self._host, self._port, self._scheme)

    def saw_https(self) -> bool:
        """Return True iff ``X-Forwarded-Proto: https`` was ever recorded.

        Phase 2 follow-up: the resolver uses this flag to log an
        operator warning when a downstream mint silently drops to
        ``http://...`` despite the deployment having produced https
        evidence at some prior point. See ``BaseURLResolver.resolve``.
        """
        with self._lock:
            return self._saw_https

    def reset(self) -> None:
        """Clear the recorded state (test seam)."""
        with self._lock:
            self._host = None
            self._port = None
            self._scheme = "http"
            self._recorded_at = 0.0


class BaseURLResolver:
    """The single base-URL resolution function (Phase 2 chain).

    Precedence (top wins):

    1. **Operator override** — ``config.live_views.external_base_url``.
       The existing knob from Phase 1; always wins. Syntactic
       validation lives in the resolver (a malformed value falls
       through to step 2 — never a bad mint).
    2. **Host-capture auto-detect** — the most recent
       ``(host, port, scheme)`` the ``HostRecorder`` has seen from
       inbound HTTP requests the daemon actually served. Uses the
       ``port`` from the Host header when present. Syntactically
       validated (no userinfo / path / whitespace); see
       ``HostRecorder`` docstring for the trust model.
    3. **Bind evidence** — the operator-configured daemon bind
       host + port (``config.daemon.host`` / ``config.daemon.port``),
       with bind-host wildcards (``0.0.0.0`` / ``::`` / ``""``)
       mapped to ``127.0.0.1``. Scheme ``http``. This is the
       last-known-reachable guess; it works for single-host
       localhost dev and for behind-proxy deployments where the
       proxy terminates TLS and we don't have a recorded host yet.
    4. **None** — caller emits a path-relative URL (last resort,
       should be rare after host-capture kicks in).
    """

    _ALLOWED_SCHEMES = ("http", "https")

    def __init__(
        self,
        external_base_url: str | None,
        bind_host: str | None,
        bind_port: int | None,
        host_recorder: "HostRecorder | None",
    ) -> None:
        self._external_base_url = (
            self._validate_external_base_url(external_base_url)
        )
        # ``bind_host is None`` is the test-mode sentinel — skip
        # bind evidence entirely (no daemon, no bind config). Any
        # other value (including the empty string, which the
        # daemon default-args never does because DaemonConfig defaults
        # to ``0.0.0.0``) goes through normalization. ``nullptr``
        # is the production path: bind_host comes from
        # ``config.daemon.host`` and is always a real string.
        self._bind_host_provided = bind_host is not None
        self._bind_host = _normalize_bind_host(bind_host or "")
        self._bind_port = (
            bind_port if isinstance(bind_port, int) and 0 < bind_port < 65536 else None
        )
        self._host_recorder = host_recorder

    @staticmethod
    def _validate_external_base_url(value: str | None) -> str | None:
        """Normalize + structurally validate the operator override.

        Returns the URL with trailing slash stripped, or None
        when the value is missing or malformed. The validation
        is structural only (scheme + host + optional port + no
        path/userinfo) — a fully-malformed value falls through
        to step 2 of the chain rather than producing a bad mint.
        """
        if not value:
            return None
        v = value.strip().rstrip("/")
        if not v:
            return None
        # Find scheme separator.
        if "://" not in v:
            return None
        scheme, _, rest = v.partition("://")
        scheme = scheme.lower()
        if scheme not in BaseURLResolver._ALLOWED_SCHEMES:
            return None
        if not rest:
            return None
        # Split host[:port][/path] — refuse any path or userinfo so
        # the override URL cannot be hijacked to mint a poisoned
        # endpoint.
        host_part, sep, path_part = rest.partition("/")
        if sep and path_part:
            return None
        if "@" in host_part:
            return None
        # Validate the host_part shape using the same parser as
        # the Host-header validator (minus the length cap — the
        # operator override is operator-supplied, not attacker-
        # controlled).
        if not _is_valid_host_header(host_part):
            return None
        return f"{scheme}://{host_part}"

    def resolve(self) -> str | None:
        """Return the base URL (no trailing slash) or None.

        None = no trustworthy base; caller emits path-relative.

        Operator-warning contract (Phase 2 follow-up):

        * When this method falls through to the **bind-derived
          tier** (step 3) or the **path-relative fallback**
          (step 4), a one-shot ``logger.warning`` fires per
          process per tier. The bind-derived warning's message
          escalates when the recorder saw an ``X-Forwarded-Proto:
          https`` capture at any prior point — that's the
          **http-downgrade** case (silently minting ``http://``
          for a deployment that previously produced https
          evidence). The escalation lives on the same warn-once
          key so a single process logs at most one bind-tier
          warning regardless of which sub-case applies.
        * The warning is process-scoped (module-level
          ``_once_warned`` set in ``daemon/services/live_views.py``)
          so a long-running LLM session that calls ``view_link``
          hundreds of times does not flood the daemon log.
        * The cure is the same in every case: set
          ``live_views.external_base_url`` with an ``https://``
          value so the operator-override tier (1) short-circuits
          the chain before Host-capture / bind evidence is touched.
        """
        # 1. Operator override.
        if self._external_base_url:
            return self._external_base_url
        # 2. Host-capture auto-detect.
        saw_https_evidence = (
            self._host_recorder.saw_https()
            if self._host_recorder is not None
            else False
        )
        if self._host_recorder is not None:
            latest = self._host_recorder.latest()
            if latest is not None:
                host, port, scheme = latest
                # RFC 3986: IPv6 literals in a URI authority MUST be
                # bracketed when a port is present (and conventionally
                # even when not). _split_host_port() strips the
                # brackets at capture time; re-wrap here so the
                # minted base URL is well-formed for both the
                # port-present and port-less composition branches.
                host_for_url = f"[{host}]" if ":" in host else host
                port_str = f":{port}" if port else ""
                return f"{scheme}://{host_for_url}{port_str}"
        # 3. Bind evidence.
        if self._bind_host_provided and self._bind_host:
            port_str = f":{self._bind_port}" if self._bind_port else ""
            if saw_https_evidence:
                _warn_once(
                    "bind-tier-downgrade",
                    "[LiveViews] view_link minted http://... but the "
                    "recorder previously captured X-Forwarded-Proto: "
                    "https evidence; falling through to bind-derived "
                    "is silently downgrading the URL. Set "
                    "live_views.external_base_url with an https:// value "
                    "(e.g. https://ensemble.example.com) — operator "
                    "override always wins and short-circuits the chain.",
                )
            else:
                _warn_once(
                    "bind-tier-fallback",
                    "[LiveViews] view_link landed on the bind-derived "
                    "tier (http://<bind-host>:<port>/...). Host-capture "
                    "has not yet recorded a request, or the recorded "
                    "Host was rejected as malformed. Set "
                    "live_views.external_base_url with an https:// value "
                    "to override (recommended for behind-proxy / "
                    "internet-exposed deployments where pre-auth Host "
                    "poisoning is in scope).",
                )
            return f"http://{self._bind_host}{port_str}"
        # 4. None.
        _warn_once(
            "path-relative-fallback",
            "[LiveViews] view_link landed on the path-relative "
            "fallback (/views/...). No bind host was configured and "
            "Host-capture has no record. Set live_views.external_base_url "
            "with an https:// value to mint fully-qualified links; the "
            "path-relative shape is only safe on a localhost dev loop "
            "where the chat client can resolve it.",
        )
        return None


# ─────────────────────────────────────────────────────────────────
# Resolved-target dataclass
# ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ResolvedTarget:
    """A request that survived every traversal / isolation check.

    Carries the absolute on-disk path to read from, the
    content-type to advertise, the file size for the
    ``Content-Length`` header, and the root type so the router
    can branch on tmp-images semantics (sidecar-MIME) vs the
    filesystem path.
    """

    on_disk_path: Path
    content_type: str
    size_bytes: int
    root_type: Literal["filesystem", "project_scoped", "tmp_images"]

    # REWORK 2026-10-07 (m5): ``raise_if_over_cap`` was dead
    # code — no caller ever invoked it (the router's
    # cap check is the service's ``size > MAX_SERVED_BYTES``
    # at resolve time, and the post-M4 router's
    # ``_fd_read`` re-checks via fstat at the read layer).
    # The size cap survives as a service-level guard on
    # the resolve path AND as a router-level guard on the
    # read path; this dataclass just carries the metadata.


class LiveViewsServiceError(Exception):
    """Base class for resolution errors. All map to a uniform 404 in the router."""


class RootNotFoundError(LiveViewsServiceError):
    """The root name is unknown / disabled / removed."""


class TraversalError(LiveViewsServiceError):
    """A path-traversal attempt was rejected (encoded, ``..``, symlink escape)."""


class PathNotFoundError(LiveViewsServiceError):
    """The resolved path does not exist on disk."""


# ─────────────────────────────────────────────────────────────────
# Service
# ─────────────────────────────────────────────────────────────────


class LiveViewsService:
    """Resolves ``(root_name, rel_path)`` to a ``ResolvedTarget``.

    Construction is the only place that knows the per-app
    ``TmpImageStore`` (for the ``tmp-images`` root) and the
    per-instance project repository (for the project-scoped
    ``planning`` root). Once built, the service is a pure
    function of (root_name, rel_path, calling_instance_id).

    The instance_id parameter exists so the ``designer-artifact``
    root (filesystem type, but the path is relative to the
    project workdir) can be resolved against the active project.
    The ``planning`` root does the same via shortname lookup
    inside the URL itself, not via the instance context.
    """

    def __init__(
        self,
        *,
        config: "LiveViewsConfig",
        tmp_image_store: "TmpImageStore | None" = None,
        project_workdir_by_shortname_resolver: Callable[[str], str | None] | None = None,
        bind_host: str | None = None,
        bind_port: int | None = None,
        host_recorder: "HostRecorder | None" = None,
    ) -> None:
        self._config = config
        self._tmp_image_store = tmp_image_store
        # Resolver is passed in by the lifespan (avoids a hard
        # dependency on the manager here). It returns the project
        # main_directory (or None) for the supplied shortname.
        # None on unknown project / missing repo row. Only the
        # shortname variant survives — the per-instance
        # ``project_workdir_resolver`` was removed in the
        # REWORK 2026-10-07 (M2) when ``designer-artifact``
        # moved from filesystem-typed to project_scoped.
        self._project_workdir_by_shortname_resolver = (
            project_workdir_by_shortname_resolver
        )
        # Phase 2: full-URL base resolution chain. The single
        # ``BaseURLResolver`` owns the precedence (operator
        # override > Host-capture > bind evidence > None); both
        # ``bind_host`` and ``host_recorder`` are optional so the
        # service remains constructible in tests without them —
        # they just short-circuit to the path-relative URL.
        self._base_url_resolver = BaseURLResolver(
            external_base_url=config.external_base_url,
            bind_host=bind_host,
            bind_port=bind_port,
            host_recorder=host_recorder,
        )

    # ────────────────── public surface ──────────────────

    def enabled(self) -> bool:
        return self._config.enabled

    def root_names(self) -> list[str]:
        """Return the names of every registered root (for diagnostics)."""
        return sorted(self._config.roots.keys())

    def has_root(self, root_name: str) -> bool:
        """Return True iff ``root_name`` is registered AND enabled."""
        entry = self._config.roots.get(root_name)
        return bool(entry and entry.enabled)

    def is_known_root(self, root_name: str) -> bool:
        """Return True iff ``root_name`` is registered (regardless of enabled)."""
        return root_name in self._config.roots

    def resolve_for_instance(
        self,
        root_name: str,
        rel_path: str,
        *,
        calling_instance_id: str | None = None,
    ) -> ResolvedTarget:
        """Resolve a (root, rel) pair against the caller's project context.

        ``calling_instance_id`` is required for the
        ``designer-artifact`` root (which resolves relative to
        the caller's project workdir). For the ``tmp-images`` and
        ``planning`` roots the instance id is not consulted; the
        shortname in the URL is the project locator for
        ``planning``, and ``tmp-images`` keys on the URL id.
        """
        if not self._config.enabled:
            # Subsystem is OFF — uniform 404, never a partial.
            raise RootNotFoundError(root_name)

        if not is_well_formed_root_name(root_name):
            raise RootNotFoundError(root_name)

        entry = self._config.roots.get(root_name)
        if entry is None or not entry.enabled:
            raise RootNotFoundError(root_name)

        if not is_well_formed_rel_path(rel_path):
            raise TraversalError(rel_path)

        if entry.type == "tmp_images":
            return self._resolve_tmp_images(root_name, rel_path, entry)
        if entry.type == "project_scoped":
            return self._resolve_project_scoped(root_name, rel_path, entry)
        # filesystem (default) — works for designer-artifact
        return self._resolve_filesystem(
            root_name, rel_path, entry, calling_instance_id=calling_instance_id
        )

    # ────────────────── private resolvers ──────────────────

    def _resolve_filesystem(
        self,
        root_name: str,
        rel_path: str,
        entry: "LiveViewsRootConfig",
        *,
        calling_instance_id: str | None,
    ) -> ResolvedTarget:
        if not entry.path:
            # Misconfiguration — the operator declared a filesystem
            # root without a path. Treat as unknown root (uniform
            # 404); no path disclosure.
            raise RootNotFoundError(root_name)

        # The filesystem type is rooted at an ABSOLUTE path
        # (``entry.path``). REWORK 2026-10-07 (M2): the
        # ``designer-artifact`` root is no longer filesystem-typed
        # — it is project_scoped now so the URL pattern
        # ``/views/designer-artifact/<shortname>/<rel>`` resolves
        # by shortname lookup, exactly like ``planning``.
        if entry.path and not Path(entry.path).is_absolute():
            # Refuse relative paths that did not get resolved by a
            # project context. An operator who configured a
            # filesystem root with a relative path will get a
            # uniform 404 — no auto-resolve against CWD (path
            # leak / test flake).
            raise RootNotFoundError(root_name)
        root_dir = Path(entry.path)

        return self._resolve_under_root(
            root_name, root_dir, rel_path, entry, root_type="filesystem"
        )

    def _resolve_project_scoped(
        self,
        root_name: str,
        rel_path: str,
        entry: "LiveViewsRootConfig",
    ) -> ResolvedTarget:
        # The URL pattern is:
        #   /views/<root>/<project_shortname>/<rel_path>
        # The first path segment after the root is the project
        # shortname; the rest is the relative path under the
        # project workdir's ``entry.path`` subdir.
        parts = [p for p in rel_path.split("/") if p]
        if not parts:
            # No project shortname supplied — uniform 404.
            raise RootNotFoundError(root_name)
        project_shortname, *rest = parts
        if not rest:
            # Only the shortname, no actual path — uniform 404.
            raise RootNotFoundError(root_name)
        sub_rel = "/".join(rest)

        if not is_well_formed_rel_path(sub_rel):
            raise TraversalError(sub_rel)

        # REWORK 2026-10-07 (M3): structural enforcement of a
        # required rel subpath. When the operator sets
        # ``required_rel_subpath`` on a project_scoped root, the
        # rel's ``/``-separated parts must contain that subpath
        # as a CONTIGUOUS subsequence. For ``designer-artifact``
        # the seeded subpath is ``['design', 'mockups']`` — a
        # rel like ``feat/design/mockups/landing.html`` passes
        # (``design``+``mockups`` are adjacent parts), but
        # ``feat/random.html`` 404s uniformly. This is the M3
        # closure: the operator cannot accidentally expose the
        # parent planning tree under a sub-scoped name.
        if entry.required_rel_subpath:
            required = entry.required_rel_subpath
            sub_parts = sub_rel.split("/")
            # Sliding window over sub_parts; require each element
            # of ``required`` to match in order, contiguously.
            # Refuse if any part of ``required`` is empty.
            if any(p == "" for p in required) or len(required) == 0:
                # Misconfiguration — uniform 404, no leak.
                raise RootNotFoundError(root_name)
            match = False
            for start in range(len(sub_parts) - len(required) + 1):
                if sub_parts[start:start + len(required)] == required:
                    match = True
                    break
            if not match:
                raise RootNotFoundError(root_name)

        if self._project_workdir_by_shortname_resolver is None:
            # Lifespan forgot to wire the shortname resolver —
            # treat as a missing project (uniform 404).
            raise RootNotFoundError(root_name)
        workdir = self._project_workdir_by_shortname_resolver(project_shortname)
        if not workdir:
            raise RootNotFoundError(root_name)

        if not entry.path:
            # Same misconfiguration handling as the filesystem
            # branch.
            raise RootNotFoundError(root_name)
        root_dir = Path(workdir) / entry.path
        return self._resolve_under_root(
            root_name, root_dir, sub_rel, entry, root_type="project_scoped"
        )

    def _resolve_tmp_images(
        self,
        root_name: str,
        rel_path: str,
        entry: "LiveViewsRootConfig",
    ) -> ResolvedTarget:
        # The TmpImageStore is the substrate. ``rel_path`` is the
        # bare 32-hex image id; the store's stat_with_meta
        # (REWORK 2026-10-07 m4) returns ``(size, content_type,
        # sha)`` without reading the blob — the router layer
        # does the single read at request time. The sidecar
        # MIME is the only thing that survives from the
        # pre-M4 shape (architect risk #7 — NEVER
        # extension-guessed); the resolve is stat-based, not
        # blob-based, so the ``daemon/routers/tmp_images.py``
        # precedent only carries the MIME contract. A
        # ``/views/tmp-images/<id>`` URL and a
        # ``/api/tmp_images/<id>`` URL still serve the same
        # bytes with the same sidecar-MIME.
        if self._tmp_image_store is None:
            # The lifespan did not wire the store (unwritable
            # data dir, etc.) — uniform 404.
            raise RootNotFoundError(root_name)
        image_id = rel_path.strip()
        # Reject any shape the store would reject. Same regex
        # gate as the original /api/tmp_images router.
        if not re.match(r"^[a-f0-9]{32}$", image_id):
            raise RootNotFoundError(root_name)
        try:
            # REWORK 2026-10-07 (m4): use ``stat_with_meta`` so
            # the resolve layer does NOT read the blob bytes
            # (the prior ``open_with_meta`` call read the full
            # blob just to populate ``size_bytes``, then the
            # router's GET/HEAD layer read it again). The
            # ``stat`` is a no-read kernel call; the sidecar
            # is a small JSON read. The blob is read once, at
            # the router layer.
            size, content_type, _sha = self._tmp_image_store.stat_with_meta(
                image_id
            )
        except Exception as exc:
            # Any error from the store (TmpImageNotFound, torn
            # sidecar, OSError) maps to a uniform 404.
            logger.debug(
                "[LiveViews] tmp-images miss for %s: %s", image_id, exc
            )
            raise RootNotFoundError(root_name) from exc

        # The store's blob is extensionless; we synthesize an
        # "on_disk_path" for the response shape (the router
        # doesn't read it; the dataclass is informational).
        return ResolvedTarget(
            on_disk_path=self._tmp_image_store.dir / image_id,
            content_type=content_type,
            size_bytes=size,
            root_type="tmp_images",
        )

    # ────────────────── tmp-images public open (REWORK m4) ──────────────────

    def open_tmp_image(self, image_id: str) -> tuple[bytes, str, str] | None:
        """Read the bytes + content_type + sha for a tmp-image id.

        REWORK 2026-10-07 (m4): the router previously reached
        into ``self._tmp_image_store.open_with_meta`` (a
        private-attr access from outside the service). The
        public method on the service is the same wire with a
        name that survives grep + review. Returns ``None``
        on any store error (the router collapses ``None`` to
        the uniform 404). The store is the one that owns the
        read; the service is the seam the router reaches.
        """
        if self._tmp_image_store is None:
            return None
        try:
            return self._tmp_image_store.open_with_meta(image_id)
        except Exception as exc:
            logger.debug(
                "[LiveViews] tmp-images open miss for %s: %s",
                image_id,
                exc,
            )
            return None

    # ────────────────── shared containment check ──────────────────

    def _resolve_under_root(
        self,
        root_name: str,
        root_dir: Path,
        rel_path: str,
        entry: "LiveViewsRootConfig",
        *,
        root_type: Literal["filesystem", "project_scoped", "tmp_images"],
    ) -> ResolvedTarget:
        """Containment-resolve a relative path under ``root_dir``.

        The sequence is the architect-ruled layered guard:

        1. ``is_well_formed_rel_path`` already filtered the input
           shape (``..`` segments, control chars, leading ``/``,
           backslashes).
        2. ``root_dir.resolve(strict=False)`` followed by
           ``(root_dir / rel_path).resolve(strict=False)`` so a
           symlink inside the root cannot point outside the
           resolved root boundary. If the resolved candidate
           does not start with the resolved root, REJECT.
        3. Allowlist check on the file extension when the root
           declares ``allowed_extensions`` (None = no gate).
        4. Existence check: missing file → 404 (uniform miss).
        5. Read as a regular file (refuse directories / sockets
           / fifos / device files — those 404 too).
        """
        # Containment under the resolved root_dir:
        # HARDENING (M10): ``Path.resolve()`` can raise OSError
        # for EACCES / ELOOP / ENOTCONN (network FS) / EIO
        # — these would otherwise escape the service's
        # typed-error envelope and surface as a FastAPI 500
        # from the router (which only catches the three typed
        # errors). Collapse OSError into the uniform 404 here
        # so the contract holds: a permission-denied
        # containment check and a path-doesn't-exist are
        # indistinguishable to the client.
        try:
            root_resolved = root_dir.resolve()
            candidate = (root_resolved / rel_path).resolve()
        except OSError as exc:
            # ``raise RootNotFoundError from exc`` chains the
            # original OSError for the daemon log; the
            # client sees the uniform 404 (no errno leak,
            # no path leak).
            raise RootNotFoundError(root_name) from exc
        try:
            candidate.relative_to(root_resolved)
        except ValueError as exc:
            # Symlink escape (or any other ``..`` that slipped
            # past the shape check) — uniform 404.
            raise TraversalError(rel_path) from exc

        # Allowlist (optional):
        if entry.allowed_extensions is not None:
            ext = extension_for(rel_path)
            if ext not in entry.allowed_extensions:
                # Extension not in the allowlist — uniform 404
                # (the file may exist; we just don't serve it).
                raise PathNotFoundError(rel_path)

        # Existence + regular-file check:
        if not candidate.is_file():
            raise PathNotFoundError(rel_path)

        size = candidate.stat().st_size
        if size > MAX_SERVED_BYTES:
            # Soft cap — uniform miss, no path-disclosure error
            # message.
            raise PathNotFoundError(rel_path)

        mime = mime_for_extension(extension_for(rel_path))
        return ResolvedTarget(
            on_disk_path=candidate,
            content_type=mime,
            size_bytes=size,
            root_type=root_type,
        )

    # ────────────────── URL minting ──────────────────

    def build_url(self, root_name: str, rel_path: str) -> str | None:
        """Return a canonical URL for ``(root_name, rel_path)`` or None.

        None = the root is unknown / disabled / subsystem off.
        The tool surface renders None as an ``Error: ...`` so
        the agent gets a typed rejection (never a partial URL).

        URL shape (Phase 2 resolution chain — see
        :class:`BaseURLResolver` for the precedence):

        * Fully-qualified ``<base>/views/<root>/<rel>`` when the
          resolver returns a base — top wins: operator override
          (``config.live_views.external_base_url``) > Host-capture
          auto-detect (the most recent inbound Host the daemon
          served) > bind evidence (``config.daemon.host``/``port``,
          wildcards mapped to ``127.0.0.1``).
        * Path-relative ``/views/<root>/<rel>`` when nothing
          trustworthy resolves (last resort; should become rare
          after Host-capture kicks in).

        ``rel_path`` is NOT URL-encoded here — it must be a
        SAFE, already-resolvable path. The agent supplies it.
        The router / store resolve it later; a bad agent-supplied
        path produces a 404 on the access side, never a broken
        link.
        """
        if not self._config.enabled:
            return None
        if not self.has_root(root_name):
            return None
        if not rel_path:
            return None
        if not is_well_formed_root_name(root_name):
            return None
        if not is_well_formed_rel_path(rel_path):
            return None
        path_part = f"/views/{root_name}/{rel_path}"
        base = self._base_url_resolver.resolve()
        if base:
            return f"{base}{path_part}"
        return path_part


# ─────────────────────────────────────────────────────────────────
# Markdown rendering (Phase 2: content-aware smart rendering)
# ─────────────────────────────────────────────────────────────────

# Pinned CDN URLs + SRI hashes. The pin + integrity attribute
# combo is the security contract: a CDN compromise cannot inject
# new JS without the SRI failing (the browser refuses to load),
# and the CSP in ``_MARKDOWN_CSP_TEMPLATE`` further restricts
# allowed script origins to ``cdn.jsdelivr.net`` only.
# Versions are pinned to current stable releases of the
# ``marked`` markdown renderer and ``DOMPurify`` XSS sanitizer.
#
# Marked 12.0.2 — ``https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js``
# DOMPurify 3.0.11 — ``https://cdn.jsdelivr.net/npm/dompurify@3.0.11/dist/purify.min.js``
# SRI hashes computed locally:
#   marked     sha384-/TQbtLCAerC3jgaim+N78RZSDYV7ryeoBCVqTuzRrFec2akfBkHS7ACQ3PQhvMVi
#   dompurify  sha384-Ic7KEGROu37YaruU6NyiYeib7UhjFyDZQ5fzBAji965L75T/4LGk5nzwMEjNGexs
MARKED_CDN_URL = "https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js"
MARKED_CDN_INTEGRITY = (
    "sha384-/TQbtLCAerC3jgaim+N78RZSDYV7ryeoBCVqTuzRrFec2akfBkHS7ACQ3PQhvMVi"
)
DOMPURIFY_CDN_URL = (
    "https://cdn.jsdelivr.net/npm/dompurify@3.0.11/dist/purify.min.js"
)
DOMPURIFY_CDN_INTEGRITY = (
    "sha384-Ic7KEGROu37YaruU6NyiYeib7UhjFyDZQ5fzBAji965L75T/4LGk5nzwMEjNGexs"
)

# Content-Security-Policy applied to the markdown wrapper page.
# The CSP allows:
#   - scripts from self + jsdelivr (marked + DOMPurify CDN);
#   - one inline bootstrap script per request via the per-request
#     ``nonce`` (NO ``unsafe-inline`` for scripts — the nonce is
#     the strict mechanism);
#   - styles from self + one inline <style> per request via nonce
#     + ``unsafe-inline`` (documented: markdown-emitted inline
#     style attrs may slip through DOMPurify when authors use raw
#     HTML in markdown; the trade-off is documented in the runbook
#     and the wrapper gracefully degrades when this attribute is
#     removed);
#   - images from self + data: URIs (data: covers inline SVG and
#     base64-embedded images in markdown);
#   - object-src 'none' (no plugins), base-uri 'self' (no <base>
#     hijack), form-action 'self' (no form posting to attacker
#     hosts), frame-ancestors 'none' (no embedding in attacker
#     iframes).
_MARKDOWN_CSP_TEMPLATE = (
    "default-src 'self'; "
    "script-src 'self' https://cdn.jsdelivr.net 'nonce-{nonce}'; "
    "style-src 'self' 'nonce-{nonce}' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)

# The wrapper page template. The raw markdown is embedded (escaped
# via ``html.escape``) inside the ``<article id="rendered"><pre>``
# so it serves as the readable fallback for NO-JS, JS-failed, or
# CDN-unreachable cases — the bootstrap script either rewrites
# the ``<article>`` with sanitized rendered HTML or leaves the
# raw markdown visible (whichever is feasible given runtime state).
#
# Marked does NOT escape raw HTML in markdown by default in v12 —
# that is why DOMPurify is mandatory on the output. The wrapper
# also keeps the embedded ``<pre>`` outside the live body content
# path until the bootstrap rewrites the article, so a malicious
# header cannot force-execute by targeting the embedded text (the
# only consumer is the bootstrap ``script``, which only renders
# sanitized output).
_MARKDOWN_WRAPPER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style nonce="{nonce}">
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; max-width: 920px; margin: 2em auto; padding: 0 1em; line-height: 1.5; color: #222; }}
  pre {{ white-space: pre-wrap; word-wrap: break-word; background: #f6f8fa; padding: 1em; border-radius: 4px; overflow-x: auto; }}
  article {{ line-height: 1.6; }}
  article h1, article h2, article h3 {{ line-height: 1.25; margin-top: 1.5em; }}
  article code {{ background: #f6f8fa; padding: 0.15em 0.3em; border-radius: 3px; }}
  article pre code {{ background: transparent; padding: 0; }}
  article a {{ color: #0366d6; }}
  article blockquote {{ border-left: 4px solid #dfe2e5; margin: 0; padding: 0 1em; }}
  article table {{ border-collapse: collapse; }}
  article table th, article table td {{ border: 1px solid #dfe2e5; padding: 0.4em 0.8em; }}
</style>
</head>
<body>
<article id="rendered"><pre>{escaped_markdown}</pre></article>
<script src="{marked_url}" integrity="{marked_integrity}" crossorigin="anonymous" nonce="{nonce}"></script>
<script src="{dompurify_url}" integrity="{dompurify_integrity}" crossorigin="anonymous" nonce="{nonce}"></script>
<script nonce="{nonce}">
(function() {{
  var article = document.getElementById('rendered');
  if (!article) {{ return; }}
  var raw = article.textContent || '';
  try {{
    // Phase 2 follow-up — bootstrap guard tightening:
    //
    // * ``typeof marked.parse === 'function'`` — not just
    //   ``typeof marked !== 'undefined'`` — a future marked
    //   release that ships without ``parse`` (e.g. ESM-default
    //   re-export shape) would otherwise leave us with a
    //   defined object that throws ``TypeError: marked.parse
    //   is not a function`` at the call site; the catch block
    //   already swallows it but we want a deterministic guard.
    // * DOMPurify availability guard was always present; the
    //   pin test (``test_bootstrap_guards_precede_render_call``)
    //   asserts both guards textually precede the
    //   ``marked.parse(raw)`` call so a render-then-guard
    //   reorder fails the test.
    if (typeof marked === 'undefined') {{ throw new Error('marked not loaded'); }}
    if (typeof marked.parse !== 'function') {{ throw new Error('marked.parse is not a function'); }}
    if (typeof DOMPurify === 'undefined') {{ throw new Error('DOMPurify not loaded'); }}
    if (typeof DOMPurify.sanitize !== 'function') {{ throw new Error('DOMPurify.sanitize is not a function'); }}
    var html = marked.parse(raw);
    var safe = DOMPurify.sanitize(html, {{ USE_PROFILES: {{ html: true }} }});
    article.innerHTML = safe;
  }} catch (e) {{
    // Bootstrap failed (JS off, CDN unreachable, marked/DOMPurify
    // missing, or a guard tripped). Leave the embedded <pre> with
    // raw markdown visible — graceful degradation contract.
  }}
}})();
</script>
</body>
</html>
"""


def new_csp_nonce() -> str:
    """Generate a per-request CSP nonce (base64-url, ~22 chars).

    The middleware-style per-request nonce closes the inline-
    script injection vector: the bootstrap ``<script nonce=>``
    is the only inline script allowed by the CSP, and the
    nonce changes on every request.
    """
    return secrets.token_urlsafe(16)


def render_markdown_wrapper(
    *,
    rel_path: str,
    markdown_text: str,
    nonce: str | None = None,
) -> tuple[str, dict[str, str]]:
    """Return ``(html_body, headers)`` for the markdown wrapper.

    The caller (``daemon/routers/live_views.py``) wraps the
    tuple in a FastAPI ``Response`` so this module stays free
    of FastAPI imports. The headers include the per-response
    CSP nonce and the same hardening headers as raw serving
    (``X-Content-Type-Options: nosniff`` + ``Cache-Control``).

    The raw markdown is HTML-escaped before being embedded in
    the wrapper's ``<article><pre>`` element. The bootstrap
    script reads ``article.textContent`` (which auto-decodes
    the entities back to the original markdown source) and
    runs marked.parse + DOMPurify.sanitize. The raw form
    stays in the DOM until the bootstrap rewrites the article;
    if the bootstrap never runs (JS off / CDN unreachable),
    the raw markdown is the page's content — readable, never
    blank.
    """
    nonce = nonce or new_csp_nonce()
    title = f"{rel_path} - live view"
    body = _MARKDOWN_WRAPPER_HTML.format(
        title=html.escape(title),
        escaped_markdown=html.escape(markdown_text),
        nonce=html.escape(nonce),
        marked_url=MARKED_CDN_URL,
        marked_integrity=MARKED_CDN_INTEGRITY,
        dompurify_url=DOMPURIFY_CDN_URL,
        dompurify_integrity=DOMPURIFY_CDN_INTEGRITY,
    )
    headers: dict[str, str] = {
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, max-age=60",
        "Content-Security-Policy": _MARKDOWN_CSP_TEMPLATE.format(nonce=nonce),
        # Phase 2 follow-up — ``Referrer-Policy: no-referrer`` on
        # the markdown wrapper only (not raw .html / image / text
        # responses — those are operator-curated artifacts whose
        # outbound linking behavior we don't presume to override).
        # The wrapper's bootstrap script rewrites ``article.innerHTML``
        # with sanitized HTML that may carry user-authored ``href=``
        # values; suppressing the Referer prevents the destination site
        # from learning the artifact path came from our daemon
        # (defense in depth — a leaked path is informational, not
        # secret, but path leakage correlates with URL-space probing).
        "Referrer-Policy": "no-referrer",
    }
    return body, headers


def is_markdown_content_type(content_type: str) -> bool:
    """Return True iff ``content_type`` advertises a markdown variant.

    Catches both ``text/markdown`` (canonical) and
    ``text/x-markdown`` (some user agents / older clients) with
    any charset suffix stripped.
    """
    base = content_type.split(";", 1)[0].strip().lower()
    return base in ("text/markdown", "text/x-markdown")
