"""Image generation tool (``generate_image``) via llmproxy ``/v1/image_generation``.

Image-Gen Tool commission, Phase 1 (2026-10-14) — plan:
``.agents/shared/planning/image-gen-tool/phase1-plan.md`` (LOCKED + APPROVED,
architect amendments AM1-AM12 folded in; reviewer implementation-time notes
binding). Decision record: ``decisions.md`` D1-D9.

Mirrors the closure-injection pattern of ``daemon.tools.chart_tools`` and
``daemon.tools.image_tools``: ``create_image_gen_tools(manager,
current_instance_id)`` is invoked from ``create_instance_tools`` to assemble
the per-instance tool list. Unlike the chart/image delegation facades,
``generate_image`` is a DIRECT HTTP capability call against the ensemble's own
LLM lane (llmproxy ``POST <base>/image_generation`` — the same
``OPENAI_BASE_URL``/``OPENAI_API_KEY`` the chat pool uses; NOT
``/v1/images/generations``), so ``TOOL_REQUIRED_AGENTS`` does not apply
(architect §4 — no agent spawn behind this tool).

Purpose-bound config (D4, OD resolver pattern — NEVER ``OPENAI_MODEL``):

- ``IMAGE_GEN_MODEL``     — purpose-bound. Unset/empty ⇒ falls back to the
  purpose-id ``"img-gen"`` (the proxy resolves purpose → real
  provider/model+credentials; see daemon.config.RT #purpose-model-defaults).
  Env value wins when set. Value = the proxy-registered CLIENT-FACING
  model id (e.g. ``minimax-image-01``); the proxy rewrites it to its
  ``InternalModel`` upstream.
- ``IMAGE_GEN_BASE_URL``  — optional; defaults to ``OPENAI_BASE_URL``.
- ``IMAGE_GEN_API_KEY``   — optional; defaults to ``OPENAI_API_KEY``
  (existing key reused — never minted).
- ``IMAGE_GEN_TIMEOUT_S`` — optional; default 240, clamped to [30, 300].
  Owns the POST+read+parse wall clock via ``asyncio.wait_for`` (D7) — the
  client ``httpx.Timeout`` DERIVES FROM THE SAME KNOB (httpx's default 5s
  read timeout would abort a live ~17s generation).

Caps and limits are MODULE CONSTANTS in this file — deliberately NOT in
``daemon/constants.py`` (sealed file; phase1-plan Task 1).

Failure convention (D8 + AM9): every failure path returns an
``"Error: ..."`` string carrying a SINGLE-LINE JSON envelope
``{"ok": false, "code": ..., "message": ...}`` — never raises, never
multiline (multiline payloads trip ``startswith("Error:")`` consumers).
Codes: ``image_gen_not_configured`` | ``image_gen_invalid_args`` |
``image_gen_upstream_error`` | ``image_gen_timeout`` |
``image_gen_fetch_failed``.

Marker contract (D2): one ``<!-- ens-img:image-gen:<32hex> -->`` line per
saved image; marker id == saved image id. The family regex in
``daemon/sources/dispatcher.py`` (P2) consumes these — the format is
byte-frozen here.
"""

import asyncio
import ipaddress
import json as _json
import logging
import os
import socket
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping
from urllib.parse import urlparse

import httpx
from langchain_core.tools import tool

from ._tool_registry import register_tool_category

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)

CATEGORY_NAME = "Image Gen"
CATEGORY_DOC = """\
Image generation tools for producing images from text prompts.

generate_image() POSTs to the ensemble LLM lane's image_generation
endpoint (MiniMax upstream via llmproxy), fetches the returned OSS
image bytes, saves each to the daemon-side tmp_images substrate, and
returns view URLs plus paste-ready ``<!-- ens-img:image-gen:<id> -->``
markers.
"""

# ── Module constants — caps & limits (phase1-plan Task 1) ────────────────────
#
# Deliberately NOT in daemon/constants.py (sealed file). These bound a single
# generate_image call: spend (n ≤ 4 ≈ $0.014), latency (240s POST cap + 4×30s
# fetch caps = 360s worst case, D7 + AM6), and store pressure (4×15MB = 60MB
# against the TmpImageStore 1 GiB store cap; 32 MiB is the SERVE-side cap —
# AM10).

MAX_N: int = 4
MAX_PROMPT_CHARS: int = 1500
MIN_SIZE: int = 512
MAX_SIZE: int = 2048
SIZE_DIVISOR: int = 8

# Per-image OSS byte fetch cap — OUTSIDE the POST-phase wait_for (AM6).
PER_IMAGE_FETCH_TIMEOUT_S: float = 30.0

# Size sanity per fetched image (AM10 — 1 GiB store cap at tmp_image_store.py,
# NOT the 32 MiB serve cap).
MAX_IMAGE_BYTES: int = 15 * 1024 * 1024

# POST-phase default + clamp bounds (D4). The client httpx.Timeout AND the
# asyncio.wait_for cap both derive from this resolved value.
DEFAULT_TIMEOUT_S: int = 240
MIN_TIMEOUT_S: int = 30
MAX_TIMEOUT_S: int = 300

# Default purpose-id for the image-generation model (purpose-model-defaults
# commission). Mirrors the ``OPENAI_MODEL_VISION`` → ``"vision"`` literal-
# fallback pattern at
# ``daemon/plugin_subsystem/opendesign/generate.py:709-727``: when the
# ``IMAGE_GEN_MODEL`` env var is unset/empty, this purpose-id is sent to
# the LLM proxy verbatim and the proxy resolves purpose → real
# provider/model+credentials. The env value still wins outright when set.
# Single source of truth — referenced by the test_image_gen_tool unit
# suite (default-behavior pin).
IMAGE_GEN_DEFAULT_MODEL: str = "img-gen"

# POST-phase body-read cap (review-hardening Finding 3) — the fetch path
# already dual-enforces ``MAX_IMAGE_BYTES`` (Content-Length pre-check +
# streamed running count); the POST response body read has no cap and a
# hostile / misconfigured upstream could pin the tool at the POST-cap
# floor while dumping unbounded bytes into memory. ~1 MiB is well above
# the largest known-valid generation response (the OpenAI-shape envelope
# plus a base64 alt-field; well under 200 KiB in live shapes) and well
# below the worst-case wall time spent in ``aread``-equivalent I/O.
POST_BODY_READ_CAP_BYTES: int = 1 * 1024 * 1024  # 1 MiB

# Sentinel returned by ``_post_and_parse`` when the streamed POST body
# crosses ``POST_BODY_READ_CAP_BYTES`` (Finding 3). The caller translates
# this into a fail-closed envelope WITHOUT attempting to JSON-parse the
# unbounded buffer.
_POST_BODY_OVERSIZE: object = object()

# Proxy identity headers — house parity with the OD raw-SDK lane
# (daemon/plugin_subsystem/opendesign/generate.py:649-658). Forwarded
# verbatim by the proxy; harmless on this route, kept for parity.
_PROXY_IDENTITY_HEADERS: dict[str, str] = {
    "x-proxy-app": "ensemble",
    "x-proxy-interleaved-thinking": "True",
}

# Default content_type when neither the OSS response Content-Type header nor
# the URL extension yields one (AM8 inference chain terminator — precedent
# image_tools.py:374 default family).
_DEFAULT_CONTENT_TYPE = "image/jpeg"

# URL-path extension → MIME (AM8 chain, step 2).
_EXT_TO_CONTENT_TYPE: dict[str, str] = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}

# Marker template (D2 — byte-frozen grammar
# ``ens-img:([a-z0-9-]+):([a-f0-9]{32})``; feature token ``image-gen``).
_MARKER_TEMPLATE = "<!-- ens-img:image-gen:{image_id} -->"

# Standard marker-handling instruction returned with every payload (the
# agent-facing teaching; mirrors the charter chart-render marker discipline).
_USAGE_NOTE = (
    "Paste each marker line VERBATIM (one per line, own line) where the image "
    "should appear in your reply. Do not wrap, strip, or reformat markers."
)

# Transport seam (tests): when the factory returns a transport, EVERY client
# this module builds (POST phase + per-image fetch phase) uses it — the mock
# sits INSIDE the code path under test (canonical pattern
# tests/unit/test_llm_failover_v2.py:326-478). Default ``None`` = real network.
_TRANSPORT_FACTORY: Any = lambda: None  # noqa: E731


def _ensure_public_url(url: str) -> tuple[str | None, str | None]:
    """Validate URL scheme + resolve hostname; reject non-public IPs (F2).

    Mirrors the SSRF guard at ``daemon/tools/image_tools.py`` (Finding 2 of
    review-hardening). Defense-in-depth against an OSS URL that — even
    though it is supposed to be the proxy-issued https endpoint — carries
    a non-public host (loopback typo, private network injection). Each
    resolved address is checked against the standard non-public set
    (private, loopback, link-local, reserved, unspecified, multicast).

    This performs a pre-flight DNS resolution to block internal IPs. There
    is a small TOCTOU window between validation and the actual httpx
    connection (which re-resolves DNS); this matches the threat-model
    tradeoff documented in ``image_tools.py``. The fetch path also passes
    ``follow_redirects=False`` so a redirected-to-private-IP cannot
    bypass the guard.
    Divergence from ``image_tools.py`` is DELIBERATE: OSS v1-signed
    URLs are direct, and any redirect on that path is treated as a
    hostile upstream anomaly.

    Returns:
        ``(url, None)`` on success, or ``(None, error_msg)`` on failure
        (bad scheme, unresolvable hostname, non-public IP).
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None, f"Unsupported URL scheme: {parsed.scheme!r}. Only http/https are allowed."
    hostname = parsed.hostname
    if not hostname:
        return None, "URL has no hostname."
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return None, f"Cannot resolve hostname: {hostname}"

    for info in infos:
        ip = str(info[4][0])
        try:
            ip_obj = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if (
            ip_obj.is_private
            or ip_obj.is_loopback
            or ip_obj.is_link_local
            or ip_obj.is_reserved
            or ip_obj.is_unspecified
            or ip_obj.is_multicast
        ):
            return None, (
                f"Blocked: hostname {hostname} resolves to non-public IP {ip}"
            )
    if not infos:
        return None, f"Cannot resolve hostname to a usable address: {hostname}"
    return url, None


def _build_client(timeout: float) -> httpx.AsyncClient:
    """Build the async client with the knob-derived timeout + test seam.

    The timeout is ``httpx.Timeout(timeout, connect=10.0)`` — house precedent
    ``daemon/tools/image_tools.py:199-204``. Deriving from the knob (NOT
    httpx's 5s default) is load-bearing: a live ~17s generation would be
    misclassified as a failure under the default read timeout.
    """
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=10.0),
        transport=_TRANSPORT_FACTORY(),
    )


def _clamp_timeout(raw: int | float | None) -> int:
    """Clamp ``IMAGE_GEN_TIMEOUT_S`` into [MIN_TIMEOUT_S, MAX_TIMEOUT_S].

    Non-numeric / unset values fall back to ``DEFAULT_TIMEOUT_S`` (fail-open
    to the DEFAULT, never to an unbounded timeout).
    """
    if raw is None:
        return DEFAULT_TIMEOUT_S
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_S
    return max(MIN_TIMEOUT_S, min(MAX_TIMEOUT_S, value))


def _resolve_env(
    env: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Resolve the purpose-bound env knobs (D4 — OD resolver pattern).

    NEVER reads ``OPENAI_MODEL`` — the generation model is the purpose-bound
    ``IMAGE_GEN_MODEL`` only (the 2026-10-07 occ-3 wrong-model class of bug,
    prevented by construction).

    When ``IMAGE_GEN_MODEL`` is unset/empty, falls back to the
    ``IMAGE_GEN_DEFAULT_MODEL`` purpose-id (``"img-gen"``) — same
    override-precedence as the ``model_vision`` → ``"vision"`` family
    pattern at ``daemon/plugin_subsystem/opendesign/generate.py:709-727``.
    Env-set values still win outright.

    Args:
        env: Optional mapping override (tests). Default ``os.environ``.

    Returns:
        ``(config, None)`` on success with keys ``model`` / ``base_url`` /
        ``api_key`` / ``timeout_s``, or ``(None, error_envelope_string)``
        fail-closed (only when base_url/api_key are also unresolvable —
        the model fallback is now LIVE-by-default, never fail-closed).
    """
    src = env if env is not None else os.environ

    model = (src.get("IMAGE_GEN_MODEL") or "").strip() or IMAGE_GEN_DEFAULT_MODEL

    base_url = (src.get("IMAGE_GEN_BASE_URL") or src.get("OPENAI_BASE_URL") or "").strip()
    api_key = (src.get("IMAGE_GEN_API_KEY") or src.get("OPENAI_API_KEY") or "").strip()
    if not base_url or not api_key:
        missing = []
        if not base_url:
            missing.append("IMAGE_GEN_BASE_URL (or OPENAI_BASE_URL)")
        if not api_key:
            missing.append("IMAGE_GEN_API_KEY (or OPENAI_API_KEY)")
        return None, _error_envelope(
            "image_gen_not_configured",
            "Image generation endpoint unresolvable: " + ", ".join(missing) +
            " must be set (same LLM lane the daemon already uses).",
        )

    timeout_s = _clamp_timeout(src.get("IMAGE_GEN_TIMEOUT_S"))
    return (
        {
            "model": model,
            "base_url": base_url.rstrip("/"),
            "api_key": api_key,
            "timeout_s": timeout_s,
        },
        None,
    )


def _error_envelope(code: str, message: str, details: Any = None) -> str:
    """Format the fail-closed error surface (D8 + AM9).

    ``"Error: "`` + SINGLE-LINE JSON envelope — ``json.dumps`` without indent
    never emits newlines; ``details`` is inlined compactly. NEVER raises.
    """
    envelope: dict[str, Any] = {"ok": False, "code": code, "message": message}
    if details is not None:
        envelope["details"] = details
    return "Error: " + _json.dumps(envelope, sort_keys=True, default=str)


def _validate_args(
    prompt: str,
    aspect_ratio: str | None,
    width: int | None,
    height: int | None,
    n: int,
    seed: int | None,
    prompt_optimizer: bool,
) -> str | None:
    """Validate the arg contract (plan Tool Design Contract table).

    Returns ``None`` when valid, or an ``image_gen_invalid_args`` envelope
    string naming the violated rule. Runs BEFORE any network call.

    Rules: prompt REQUIRED non-empty ≤ ``MAX_PROMPT_CHARS``;
    ``aspect_ratio`` MUTUALLY EXCLUSIVE with the ``width``/``height`` pair;
    the pair is PAIR-ONLY (both or neither) with each in
    [``MIN_SIZE``, ``MAX_SIZE``] divisible by ``SIZE_DIVISOR``;
    ``n`` in [1, ``MAX_N``]; ``seed`` int64 (pass-through); ``response_format``
    and ``subject_reference`` are deliberately NOT exposed (AM7 / I2I out of
    scope).
    """
    if not isinstance(prompt, str) or not prompt.strip():
        return _error_envelope(
            "image_gen_invalid_args", "prompt is required and must be a non-empty string."
        )
    if len(prompt) > MAX_PROMPT_CHARS:
        return _error_envelope(
            "image_gen_invalid_args",
            f"prompt exceeds the upstream limit of {MAX_PROMPT_CHARS} characters "
            f"(got {len(prompt)}).",
        )

    has_aspect = aspect_ratio is not None
    has_size = width is not None or height is not None
    if has_aspect and has_size:
        return _error_envelope(
            "image_gen_invalid_args",
            "aspect_ratio is mutually exclusive with width/height — supply one "
            "sizing mode, not both.",
        )
    if has_aspect and (not isinstance(aspect_ratio, str) or not aspect_ratio.strip()):
        return _error_envelope(
            "image_gen_invalid_args", "aspect_ratio must be a non-empty string (e.g. '16:9')."
        )
    if (width is None) != (height is None):
        return _error_envelope(
            "image_gen_invalid_args",
            "width and height must be given as a PAIR (upstream pair-only rule); "
            "got only one of them.",
        )
    for name, value in (("width", width), ("height", height)):
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int):
            return _error_envelope(
                "image_gen_invalid_args", f"{name} must be an integer."
            )
        if not (MIN_SIZE <= value <= MAX_SIZE):
            return _error_envelope(
                "image_gen_invalid_args",
                f"{name}={value} outside the supported range "
                f"[{MIN_SIZE}, {MAX_SIZE}].",
            )
        if value % SIZE_DIVISOR != 0:
            return _error_envelope(
                "image_gen_invalid_args",
                f"{name}={value} must be divisible by {SIZE_DIVISOR}.",
            )

    if isinstance(n, bool) or not isinstance(n, int):
        return _error_envelope("image_gen_invalid_args", "n must be an integer.")
    if not (1 <= n <= MAX_N):
        return _error_envelope(
            "image_gen_invalid_args",
            f"n must be between 1 and {MAX_N} (got {n}); the tool caps the "
            "upstream 1-9 range on spend/latency grounds.",
        )

    if seed is not None:
        if isinstance(seed, bool) or not isinstance(seed, int):
            return _error_envelope("image_gen_invalid_args", "seed must be an integer (int64).")
        if not (-(2**63) <= seed <= 2**63 - 1):
            return _error_envelope(
                "image_gen_invalid_args", "seed must fit in a signed 64-bit integer."
            )

    if not isinstance(prompt_optimizer, bool):
        return _error_envelope(
            "image_gen_invalid_args", "prompt_optimizer must be a boolean."
        )
    return None


def _coerce_count(value: Any) -> int:
    """Coerce an upstream metadata count to int (proxy sends STRINGS — AM1).

    Live shape: ``metadata.success_count == "1"``. Absent / garbage ⇒ 0.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _parse_generation_response(
    status_code: int, body: Any
) -> tuple[dict[str, Any] | None, str | None]:
    """Parse the proxy response body into image URLs + disclosure (AM1/AM2/AM5).

    Error-body shapes are PROXY-specific:

    - HTTP 502 (proxy relay of an upstream app error): the upstream body is
      preserved VERBATIM — parse ``base_resp.status_code`` / ``status_msg``
      out of the 502 body.
    - HTTP 504 (proxy deadline): self-generated OpenAI envelope
      ``{"error": {"type": "server_error", "message": ...}}`` — parse
      ``error.message``.
    - Other non-200: generic envelope with the status.

    HTTP 200 body: ``data`` is a SINGLE OBJECT holding ``image_urls[]`` (NOT
    an array); ``metadata.success_count`` / ``failed_count`` are STRINGS;
    success ONLY ``base_resp.status_code == 0`` — fail closed on ANY non-zero
    value, NO code whitelisting (AM2).

    Returns ``(parsed, None)`` with keys ``image_urls`` / ``success_count`` /
    ``failed_count``, or ``(None, error_envelope_string)``.
    """
    if not isinstance(body, dict):
        return None, _error_envelope(
            "image_gen_upstream_error",
            f"Upstream returned HTTP {status_code} with a non-JSON or "
            "unexpected body — cannot locate image data.",
            {"status": status_code},
        )

    if status_code == 502:
        # Upstream app error relayed verbatim — parse base_resp from THIS body.
        # The dict shape echoes ONLY ``status_code`` + ``status_msg`` (Finding 4):
        # the upstream ``base_resp`` may carry extra keys (request_id, trace_id,
        # internal flags, etc.) that we MUST NOT relay — they are upstream
        # internals and have no place in our user-facing error envelope.
        base_resp = body.get("base_resp")
        if isinstance(base_resp, dict):
            app_code = base_resp.get("status_code")
            if app_code != 0:
                return None, _error_envelope(
                    "image_gen_upstream_error",
                    "Upstream image generation app error (relayed verbatim by "
                    f"the proxy as HTTP 502): status_code={app_code!r} "
                    f"status_msg={base_resp.get('status_msg')!r}.",
                    {
                        "status": 502,
                        "status_code": app_code,
                        "status_msg": base_resp.get("status_msg"),
                    },
                )
            # 502 carrying base_resp.status_code == 0 is contradictory — the
            # proxy only relays non-zero app errors on 502. Fail closed.
        return None, _error_envelope(
            "image_gen_upstream_error",
            "Proxy returned HTTP 502 without a parseable upstream base_resp "
            "body.",
            {"status": 502, "body_keys": sorted(body.keys())},
        )

    if status_code == 504:
        error = body.get("error")
        message = error.get("message") if isinstance(error, dict) else None
        return None, _error_envelope(
            "image_gen_timeout",
            message
            or "Proxy deadline exceeded (HTTP 504) before the upstream "
            "generation completed.",
            {"status": 504, "proxy_message": message},
        )

    if status_code != 200:
        return None, _error_envelope(
            "image_gen_upstream_error",
            f"Image generation endpoint returned HTTP {status_code}.",
            {"status": status_code, "body_keys": sorted(body.keys())},
        )

    # ── HTTP 200 ─────────────────────────────────────────────────────────────
    base_resp = body.get("base_resp")
    if not isinstance(base_resp, dict) or "status_code" not in base_resp:
        return None, _error_envelope(
            "image_gen_upstream_error",
            "HTTP 200 body malformed: missing base_resp.status_code — success "
            "cannot be proven, failing closed.",
            {"body_keys": sorted(body.keys())},
        )

    # Success gate: ONLY status_code == 0. ANY non-zero value fails closed —
    # NO whitelisting of historically observed app-error codes (AM2).
    app_code = base_resp.get("status_code")
    if app_code != 0:
        return None, _error_envelope(
            "image_gen_upstream_error",
            "Upstream image generation failed: base_resp.status_code="
            f"{app_code!r} status_msg={base_resp.get('status_msg')!r}.",
            {
                # Same 3-key filter as the 502 path (council minor #1 —
                # Finding 4 adjacent-path coverage): the upstream dict
                # may carry extras (request_id / trace_id / internal
                # flags) that MUST NOT relay through the envelope.
                "status": 200,
                "status_code": app_code,
                "status_msg": base_resp.get("status_msg"),
            },
        )

    data = body.get("data")
    if not isinstance(data, dict):
        return None, _error_envelope(
            "image_gen_upstream_error",
            "HTTP 200 body malformed: 'data' is not a single object holding "
            "image_urls (got "
            f"{type(data).__name__}) — failing closed.",
        )

    raw_urls = data.get("image_urls")
    if raw_urls is None:
        raw_urls = []
    if not isinstance(raw_urls, list):
        return None, _error_envelope(
            "image_gen_upstream_error",
            "HTTP 200 body malformed: data.image_urls is not a list.",
        )
    image_urls = [u for u in raw_urls if isinstance(u, str) and u.strip()]

    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    parsed = {
        "image_urls": image_urls,
        "success_count": _coerce_count(metadata.get("success_count")),
        "failed_count": _coerce_count(metadata.get("failed_count")),
    }
    return parsed, None


def _content_type_for(content_type_header: str | None, url: str) -> str:
    """AM8 inference chain: response Content-Type → URL extension → default.

    ``application/octet-stream`` is treated as ABSENT (it means "unknown
    binary" — persisting it as an image MIME would be strictly worse than
    sniffing the URL extension).
    """
    if content_type_header:
        primary = content_type_header.split(";", 1)[0].strip().lower()
        if primary and primary != "application/octet-stream":
            return primary
    ext = Path(urlparse(url).path).suffix.lstrip(".").lower()
    return _EXT_TO_CONTENT_TYPE.get(ext, _DEFAULT_CONTENT_TYPE)


async def _fetch_image_bytes(
    url: str,
) -> tuple[bytes, str] | str:
    """Fetch ONE OSS image immediately (≈24h TTL URLs) with its own 30s cap.

    Sits OUTSIDE the POST-phase ``wait_for`` (AM6). Size sanity ≤
    ``MAX_IMAGE_BYTES`` (Content-Length pre-check + streamed running count so
    a hostile server cannot exhaust memory). Returns ``(bytes,
    content_type)`` or a short error string (caller skips with disclosure —
    one bad image never aborts the batch).

    Defense-in-depth (review-hardening Finding 2):
        ``_ensure_public_url`` rejects scheme-mismatch, unresolvable
        hostname, and any resolved address inside private/loopback/
        link-local/reserved/unspecified/multicast BEFORE any network I/O.
        The httpx client is built with ``follow_redirects=False`` so a
        redirect to a private host cannot bypass the guard (mirrors the
        image_tools posture, where redirects are walked hop-by-hop and
        each hop is re-validated). The single-hop ``follow_redirects``
        refusal is appropriate here: OSS URLs are proxy-issued and not
        expected to redirect in normal operation; any redirect is a sign
        of an upstream anomaly and is surfaced as a non-200 failure.
    """
    validated_url, err = _ensure_public_url(url)
    if err:
        return f"image URL refused: {err}"
    try:
        async def _do_fetch() -> tuple[bytes, str]:
            declared_size: int | None = None
            async with _build_client(PER_IMAGE_FETCH_TIMEOUT_S) as client:
                async with client.stream("GET", validated_url, follow_redirects=False) as response:
                    if response.status_code != 200:
                        return (
                            f"fetch HTTP {response.status_code} for image URL"
                        )
                    declared_size = response.headers.get("content-length")
                    if declared_size and declared_size.isdigit():
                        if int(declared_size) > MAX_IMAGE_BYTES:
                            return (
                                f"image exceeds size cap ({declared_size} > "
                                f"{MAX_IMAGE_BYTES} bytes)"
                            )
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > MAX_IMAGE_BYTES:
                            return (
                                f"image exceeds size cap (>{MAX_IMAGE_BYTES} "
                                "bytes streamed)"
                            )
                        chunks.append(chunk)
                    body = b"".join(chunks)
                    if not body:
                        return "image URL returned 0 bytes"
                    return (
                        body,
                        _content_type_for(response.headers.get("content-type"), url),
                    )

        return await asyncio.wait_for(_do_fetch(), timeout=PER_IMAGE_FETCH_TIMEOUT_S)
    except asyncio.TimeoutError:
        return f"image fetch timed out after {PER_IMAGE_FETCH_TIMEOUT_S}s"
    except Exception as exc:  # noqa: BLE001 — never-raise contract (D8)
        return f"image fetch failed: {type(exc).__name__}: {exc}"


def create_image_gen_tools(
    manager: "InstanceManager | None", current_instance_id: str | None
) -> list:
    """Create the image generation tool with injected manager reference.

    Args:
        manager: The InstanceManager instance (used to reach
            ``manager.tmp_image_store`` for the save substrate; ``None`` is
            tolerated — saves then fail closed with disclosure).
        current_instance_id: The calling instance id (``provenance.
            source_agent`` auto-stamp resolution).

    Returns:
        List of tool functions: [generate_image]
    """

    def _get_store():
        """Resolve the daemon-side ``TmpImageStore`` via the manager.

        Same contract as ``image_tools._get_store``: ``None`` when the
        lifespan did not wire the store (test stubs, partial-init daemon).
        """
        try:
            return manager.tmp_image_store
        except Exception:
            return None

    def _get_caller_agent_id() -> str | None:
        """Resolve the calling agent's id for the provenance auto-stamp.

        Mirrors ``image_tools._get_caller_agent_id``: ``None`` on any
        resolution failure — a missing tag drops the provenance entry rather
        than persisting an empty-string sentinel.
        """
        try:
            instance_meta = manager._instance_repository.get(current_instance_id)
            if instance_meta is None:
                return None
            agent_id = getattr(instance_meta, "agent_id", None)
            return agent_id if isinstance(agent_id, str) else None
        except Exception:
            return None

    @register_tool_category("image-gen")
    @tool
    async def generate_image(
        prompt: str,
        aspect_ratio: str | None = None,
        width: int | None = None,
        height: int | None = None,
        n: int = 1,
        seed: int | None = None,
        prompt_optimizer: bool = False,
    ) -> str:
        """Generate images from a text prompt via the ensemble LLM lane.

        Sends ONE POST to the image_generation endpoint (MiniMax upstream
        via llmproxy — no retries; upstream is 10 RPM), immediately fetches
        each returned OSS image (~24h TTL), saves every blob to the
        daemon-side tmp_images substrate with ``feature="image-gen"``, and
        returns view URLs plus one paste-ready
        ``<!-- ens-img:image-gen:<32hex> -->`` marker per saved image.

        Paste each returned marker line VERBATIM on its own line where the
        image should appear in your reply — never wrap, strip, or reformat
        markers. Cost ≈ $0.0035 per image (n ≤ 4).

        Sizing: pass ``aspect_ratio`` (e.g. "16:9"; upstream default "1:1")
        OR the ``width``/``height`` pair (each 512-2048, divisible by 8) —
        never both.

        Args:
            prompt: What to generate. Required; ≤ 1500 characters.
            aspect_ratio: Optional aspect ratio (e.g. "1:1", "16:9").
                Mutually exclusive with width/height.
            width: Optional width in px (512-2048, divisible by 8). Must be
                given together with ``height``.
            height: Optional height in px (512-2048, divisible by 8). Must
                be given together with ``width``.
            n: Number of images (1-4). Each image costs ≈ $0.0035.
            seed: Optional int64 random seed for reproducibility.
            prompt_optimizer: Let the upstream rewrite the prompt for better
                results. Default False.

        Returns:
            JSON payload on success::

                {"ok": true,
                 "images": [{"image_id": "<32hex>", "content_type": "...",
                             "size_bytes": N, "sha256_hex": "...",
                             "view_url": "/views/tmp-images/<32hex>"}, ...],
                 "markers": ["<!-- ens-img:image-gen:<32hex> -->", ...],
                 "usage_note": "Paste each marker line VERBATIM ..."}

            One marker per successfully saved image; marker id == image id.
            On failure a short ``"Error: ..."`` string carrying a single-line
            JSON envelope (codes: image_gen_not_configured,
            image_gen_invalid_args, image_gen_upstream_error,
            image_gen_timeout, image_gen_fetch_failed). Never raises.
        """
        # 1. Resolve env — fail closed WITHOUT any network attempt when
        #    unconfigured (D4).
        cfg, env_err = _resolve_env()
        if cfg is None:
            assert env_err is not None  # _resolve_env invariant: cfg None → err non-None
            return env_err

        # 2. Validate args — before any network call.
        arg_err = _validate_args(
            prompt, aspect_ratio, width, height, n, seed, prompt_optimizer
        )
        if arg_err is not None:
            return arg_err

        payload: dict[str, Any] = {
            "model": cfg["model"],
            "prompt": prompt,
            "n": n,
            "prompt_optimizer": prompt_optimizer,
        }
        if aspect_ratio is not None:
            payload["aspect_ratio"] = aspect_ratio.strip()
        if width is not None and height is not None:
            payload["width"] = width
            payload["height"] = height
        if seed is not None:
            payload["seed"] = seed

        url = f"{cfg['base_url']}/image_generation"
        headers = {
            "Authorization": f"Bearer {cfg['api_key']}",
            **_PROXY_IDENTITY_HEADERS,
        }

        # 3. POST + body-read + parse under ONE wall-clock cap (D7 + AM6) —
        #    the tool owns its cap; nothing else kills a ~150s native call.
        #    SINGLE attempt — no retries (upstream 10 RPM).
        #
        # Body-read is CAPPED at ``POST_BODY_READ_CAP_BYTES`` (Finding 3): a
        # hostile / misconfigured upstream could otherwise pin the tool at
        # the POST-cap floor while dumping unbounded bytes into memory. We
        # stream-read chunk by chunk via ``response.aiter_bytes()``; once
        # we cross the cap we close the response and return the special
        # ``_POST_BODY_OVERSIZE`` sentinel that the caller translates into
        # the fail-closed envelope below. ``aiter_bytes`` (vs
        # ``aiter_raw``) is selected so the same code path works through
        # ``MockTransport`` in tests; production reads from the socket in
        # the same loop, so memory peak is bounded by ``chunk_size``.
        async def _post_and_parse() -> tuple[int, Any]:
            async with _build_client(float(cfg["timeout_s"])) as client:
                response = await client.post(url, json=payload, headers=headers)
                buf = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(buf) + len(chunk) > POST_BODY_READ_CAP_BYTES:
                        await response.aclose()
                        return response.status_code, _POST_BODY_OVERSIZE
                    buf.extend(chunk)
                raw_body = bytes(buf)
            try:
                body = _json.loads(raw_body)
            except (ValueError, UnicodeDecodeError):
                body = None
            return response.status_code, body

        try:
            status_code, body = await asyncio.wait_for(
                _post_and_parse(), timeout=float(cfg["timeout_s"])
            )
        except asyncio.TimeoutError:
            return _error_envelope(
                "image_gen_timeout",
                f"Image generation POST exceeded the {cfg['timeout_s']}s "
                "wall-clock cap (POST + body read + parse).",
                {"timeout_s": cfg["timeout_s"]},
            )
        except Exception as exc:  # noqa: BLE001 — never-raise contract (D8)
            return _error_envelope(
                "image_gen_upstream_error",
                f"Image generation request failed: {type(exc).__name__}: {exc}",
            )

        # Sentinel → fail-closed envelope (Finding 3, oversize POST body).
        if body is _POST_BODY_OVERSIZE:
            return _error_envelope(
                "image_gen_upstream_error",
                "Image generation POST response body exceeds the "
                f"{POST_BODY_READ_CAP_BYTES}-byte read cap — refusing to "
                "parse unbounded upstream output.",
                {
                    "status": status_code,
                    "post_body_read_cap_bytes": POST_BODY_READ_CAP_BYTES,
                },
            )

        # 4. Parse — proxy-specific error-body shapes (AM1), status_code==0
        #    gate (AM2), single-object data, string counts.
        parsed, parse_err = _parse_generation_response(status_code, body)
        if parsed is None:
            assert parse_err is not None  # _parse_generation_response invariant: parsed None → err non-None
            return parse_err

        image_urls: list[str] = parsed["image_urls"]
        failed_count: int = parsed["failed_count"]

        disclosures: list[str] = []
        if failed_count > 0:
            disclosures.append(
                f"Upstream reported {failed_count} failed generation(s) out of "
                f"{n} requested; delivering the {len(image_urls)} that exist."
            )

        # 5. AM5 — empty success is STILL a success: ok:true + disclosure.
        if not image_urls:
            return _json.dumps(
                {
                    "ok": True,
                    "images": [],
                    "markers": [],
                    "usage_note": (
                        "Upstream reported success but returned NO images "
                        "(empty image_urls). Nothing was generated or saved. "
                        + _USAGE_NOTE
                    ),
                },
                sort_keys=True,
            )

        # 6. Fetch EACH OSS URL immediately — per-image 30s cap OUTSIDE the
        #    POST-phase wait_for (AM6); first failure skips that image with
        #    disclosure, never aborts the batch.
        store = _get_store()
        images: list[dict[str, Any]] = []
        markers: list[str] = []
        for i, image_url in enumerate(image_urls):
            fetch_result = await _fetch_image_bytes(image_url)
            if isinstance(fetch_result, str):
                disclosures.append(
                    f"image[{i}] skipped: {fetch_result}."
                )
                continue
            blob, content_type = fetch_result

            # image_save semantics: feature="image-gen", page=None,
            # retention_class="normal" (D9), source_agent auto-stamped.
            source_agent = _get_caller_agent_id()
            provenance: dict[str, str] = {"feature": "image-gen"}
            if source_agent is not None:
                provenance["source_agent"] = source_agent

            if store is None:
                disclosures.append(
                    f"image[{i}] skipped: tmp-image store not initialized."
                )
                continue
            image_id = uuid.uuid4().hex
            try:
                record = store.save(
                    image_id,
                    blob,
                    content_type,
                    provenance=provenance,
                    retention_class="normal",
                )
            except Exception as exc:  # noqa: BLE001 — incl. TmpImageStoreFull
                disclosures.append(
                    f"image[{i}] skipped: tmp-image save failed: "
                    f"{type(exc).__name__}: {exc}."
                )
                continue

            images.append(
                {
                    "image_id": record.image_id,
                    "content_type": record.content_type,
                    "size_bytes": record.size_bytes,
                    "sha256_hex": record.sha256_hex,
                    "view_url": f"/views/tmp-images/{record.image_id}",
                }
            )
            markers.append(_MARKER_TEMPLATE.format(image_id=record.image_id))

        # Every upstream URL lost in fetch/save ⇒ error, not a silent
        # ok:true-empty (that shape is reserved for upstream-empty, AM5).
        if not images:
            return _error_envelope(
                "image_gen_fetch_failed",
                "Upstream returned image URL(s) but none could be fetched and "
                "saved.",
                {"disclosures": disclosures},
            )

        usage_note = _USAGE_NOTE
        if disclosures:
            usage_note = " ".join(disclosures) + " " + _USAGE_NOTE

        return _json.dumps(
            {
                "ok": True,
                "images": images,
                "markers": markers,
                "usage_note": usage_note,
            },
            sort_keys=True,
        )

    generate_image._full_doc_ = """\
Generate images from a text prompt via the ensemble LLM lane's
image_generation endpoint (MiniMax upstream, relayed by llmproxy).

The tool POSTs ONCE (no retries — upstream is 10 RPM), immediately
fetches each returned OSS image URL (~24h TTL) with a per-image 30s
cap and a 15MB size sanity bound, saves every blob to the tmp_images
substrate with provenance ``feature="image-gen"`` (retention normal),
and returns a JSON payload with one ``<!-- ens-img:image-gen:<32hex>
-->`` marker per saved image (marker id == image id) plus a
``/views/tmp-images/<32hex>`` view URL per image.

MARKER DISCIPLINE: paste each marker line VERBATIM on its own line
where the image should appear in your reply — the chat dispatcher
extracts markers byte-exactly; wrapped/stripped/reformatted markers
silently degrade to literal comment text.

Sizing is either ``aspect_ratio`` (e.g. "16:9"; upstream default
"1:1") OR the ``width``/``height`` pair (each 512-2048, divisible by
8, pair-only) — mutually exclusive. ``n`` is capped at 4 (≈$0.0035 per
image). ``seed`` is an optional int64 pass-through.
``prompt_optimizer`` defaults to False. ``response_format`` and
``subject_reference`` are deliberately not exposed.

Failure surface: ``"Error: ..."`` strings carrying a single-line JSON
envelope (``image_gen_not_configured`` when ``IMAGE_GEN_BASE_URL`` /
``IMAGE_GEN_API_KEY`` (or their ``OPENAI_*`` fallbacks) are unresolvable —
the model fallback (``IMAGE_GEN_DEFAULT_MODEL = "img-gen"``) is
LIVE-by-default so the model env var alone never trips this code;
``image_gen_invalid_args``; ``image_gen_upstream_error``;
``image_gen_timeout``; ``image_gen_fetch_failed``). Never raises.
Partial success (some upstream generations failed) is delivered with a
disclosure in ``usage_note``; an upstream success with zero URLs is
``ok:true`` with an explicit disclosure.
"""

    return [generate_image]
