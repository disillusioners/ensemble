"""Image analysis tools for vision-capable agents.

Mirrors the closure-injection pattern of ``daemon.tools.chart_tools`` and
``daemon.tools.knowledge_tools``: ``create_image_tools(manager, current_instance_id)``
is invoked from ``create_instance_tools`` to assemble the per-instance tool list.
The generated ``explain_image`` tool delegates to the ``image-reader`` agent via
``invoke_agent_and_wait`` and returns the vision model's analysis of the image.
"""

import base64
import ipaddress
import logging
import re
import socket
import stat
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx
from langchain_core.tools import tool

from ._tool_registry import register_tool_category
from daemon.utils import invoke_agent_and_wait

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)

CATEGORY_NAME = "Image"
CATEGORY_DOC = """\
Image analysis tools for vision-capable agents.

explain_image() delegates to the image-reader agent which analyzes
images (URLs or local paths) and answers questions about them using a
vision-capable LLM.
"""

# Supported image formats for vision model ingestion. Anything outside
# this set is rejected so we never produce a malformed data URI.
SUPPORTED_IMAGE_FORMATS: tuple[str, ...] = ("png", "jpeg", "gif", "webp")

# Default timeout (seconds) for fetching remote image URLs. Kept shorter
# than the agent timeout (300s) so a stalled fetch fails fast and is
# surfaced as a tool error rather than tying up the worker.
_IMAGE_FETCH_TIMEOUT_S: float = 30.0

# Maximum number of HTTP redirects the URL fetcher will follow before
# failing. Each hop is re-validated against the SSRF guard so a malicious
# redirect chain cannot bypass the IP allow-list.
_MAX_REDIRECT_HOPS: int = 5

# Hard cap on bytes read from a URL or local path. Matches the cap
# enforced by MessageCreate.validate_images so we never produce a data
# URI that the downstream vision pipeline would later reject.
_MAX_IMAGE_BYTES: int = 10 * 1024 * 1024  # 10MB

# Magic byte signatures for format detection when Content-Type / file
# extension are missing or misleading. Indexes are absolute byte offsets.
_MAGIC_PNG: bytes = b"\x89PNG\r\n\x1a\n"
_MAGIC_JPEG: bytes = b"\xff\xd8\xff"
_MAGIC_GIF87: bytes = b"GIF87a"
_MAGIC_GIF89: bytes = b"GIF89a"
_MAGIC_RIFF: bytes = b"RIFF"
_MAGIC_WEBP: bytes = b"WEBP"

# Mapping of lowercase Content-Type values (parameterless) → format slug.
_CONTENT_TYPE_TO_FORMAT: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "image/gif": "gif",
    "image/webp": "webp",
}

# Mapping of lowercase file extension (no leading dot) → format slug.
_EXT_TO_FORMAT: dict[str, str] = {
    "png": "png",
    "jpg": "jpeg",
    "jpeg": "jpeg",
    "gif": "gif",
    "webp": "webp",
}


def _ensure_public_url(url: str) -> tuple[str | None, str | None]:
    """Validate URL scheme and resolve hostname to reject non-public IPs.

    Resolves the hostname via ``socket.getaddrinfo`` and validates that every
    returned address is publicly routable, blocking internal ranges
    (private, loopback, link-local, reserved, unspecified, multicast).

    Note: This performs a pre-flight DNS resolution to block internal IPs.
    There is a small TOCTOU window between validation and the actual httpx
    connection (which re-resolves DNS). This is acceptable for an image
    analysis tool's threat model — a deliberate DNS-rebinding race against
    a one-shot image fetch is well below the bar of other risks the tool
    already accepts (malicious image content, redirects, etc.). Each
    redirect hop is re-validated to keep the SSRF guard tight.

    Used as the SSRF guard before any outbound HTTP request, including each
    hop of a manually-walked redirect chain.

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
            return None, f"Blocked: hostname {hostname} resolves to non-public IP {ip}"
    if not infos:
        return None, f"Cannot resolve hostname to a usable address: {hostname}"
    return url, None


async def _load_image_as_data_uri(image: str) -> str:
    """Load an image from a URL or local path and return a base64 data URI.

    Accepts ``http://`` / ``https://`` URLs and filesystem paths. Format is
    detected from magic bytes first (the authoritative signal), then
    verified against the Content-Type header / URL extension / file
    extension hint — a mismatch is rejected so a server cannot smuggle
    a different payload past the vision model.

    Args:
        image: URL or local filesystem path to an image (png/jpeg/gif/webp).

    Returns:
        A ``data:image/{format};base64,{base64_data}`` string ready to pass
        to ``invoke_agent_and_wait`` via its ``images`` parameter.

    Raises:
        ValueError: If the source string is empty, the format is unsupported,
            the URL cannot be reached, the URL resolves to a private/reserved
            IP, the image exceeds ``_MAX_IMAGE_BYTES``, the local file is
            missing / outside the project workdir, or the file is not a
            regular file.
        httpx.HTTPError: If an HTTP fetch fails (timeout, connection error,
            non-2xx status). The caller catches and returns a tool error.
        OSError: If a local read fails (permissions, I/O). Caught by caller.
    """
    if not isinstance(image, str):
        raise ValueError(f"image must be a string, got {type(image).__name__}")
    image = image.strip()
    if not image:
        raise ValueError("image must be a non-empty URL or local path")

    lowered = image.lower()
    if lowered.startswith("http://") or lowered.startswith("https://"):
        return await _load_image_from_url(image)
    return _load_image_from_path(image)


async def _load_image_from_url(url: str) -> str:
    """Fetch a remote image and return a base64 data URI.

    Walks redirects manually (max ``_MAX_REDIRECT_HOPS``) so every hop can
    be re-validated against the SSRF guard before httpx connects. Uses the
    ORIGINAL URL on every hop — httpx derives TLS SNI from the URL netloc,
    so substituting the resolved IP (the previous approach) broke the
    SSL handshake. Each hop accepts a small DNS-rebinding window between
    validation and connect; this is the documented threat-model tradeoff.
    Uses ``client.stream`` + ``aiter_bytes`` with a running byte count so
    a hostile server cannot exhaust memory with an unbounded response.
    The ``follow_redirects`` flag is intentionally left at its default
    (``False``) — automatic redirects would skip URL validation on each
    hop and bypass the SSRF guard entirely.
    """
    timeout = httpx.Timeout(_IMAGE_FETCH_TIMEOUT_S, connect=10.0)
    current_url = url
    data = bytearray()
    final_response: httpx.Response | None = None

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        for hop in range(_MAX_REDIRECT_HOPS + 1):
            # SSRF guard: validate every hop (including the initial URL).
            # _ensure_public_url returns (url, None) on success.
            validated_url, err = _ensure_public_url(current_url)
            if err:
                raise ValueError(err)
            assert validated_url is not None  # for type-checkers

            async with client.stream("GET", validated_url) as response:
                is_redirect = response.status_code in (301, 302, 303, 307, 308)
                if is_redirect:
                    if hop >= _MAX_REDIRECT_HOPS:
                        await response.aclose()
                        raise ValueError(
                            f"Exceeded max redirect hops ({_MAX_REDIRECT_HOPS}) "
                            f"while fetching {url!r}"
                        )
                    location = response.headers.get("location")
                    await response.aclose()
                    if not location:
                        raise ValueError(
                            f"Redirect without Location header from {validated_url}"
                        )
                    # Resolve relative redirects against the current URL.
                    current_url = str(httpx.URL(validated_url).join(location))
                    continue

                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > _MAX_IMAGE_BYTES:
                        await response.aclose()
                        raise ValueError(
                            f"Image exceeds maximum size of "
                            f"{_MAX_IMAGE_BYTES} bytes"
                        )
                final_response = response
                break

        if final_response is None:
            # Defensive guard: every iteration either redirected
            # (and would have raised) or broke out with a final
            # response. Reaching here means a logic bug.
            raise ValueError(
                f"No final response received after {_MAX_REDIRECT_HOPS} redirects"
            )

    fmt = _detect_format_from_magic_bytes(bytes(data))
    if fmt is None:
        raise ValueError(
            f"Could not determine image format for URL {url!r}: "
            "magic bytes did not match any supported format."
        )

    # Cross-check the magic-byte verdict against the URL/Content-Type hint
    # so a server claiming ``Content-Type: image/png`` cannot smuggle a
    # different payload (e.g. SVG, HTML, JS) past the vision model.
    hint = _detect_format_from_content_type(
        final_response.headers.get("content-type")
    ) or _detect_format_from_url(url)
    if hint and hint != fmt:
        raise ValueError(
            f"Format mismatch for {url!r}: magic bytes indicate "
            f"{fmt!r} but URL/Content-Type hint was {hint!r}"
        )

    b64 = base64.b64encode(bytes(data)).decode("ascii")
    return f"data:image/{fmt};base64,{b64}"


def _validate_local_path(path_str: str, workdir: str | None) -> tuple[Path | None, str | None]:
    """Validate that ``path_str`` resolves to a regular file inside ``workdir``.

    Enforces the workdir boundary for BOTH relative and absolute paths —
    absolute paths no longer bypass the check. Fails closed (rejects the
    path) if no workdir is available.

    Args:
        path_str: Path provided by the caller (relative or absolute).
        workdir: Project workdir boundary. If ``None``, every path is
            rejected — there is no safe fallback for the image tool.

    Returns:
        ``(resolved_path, None)`` on success, or ``(None, error_msg)`` if
        the path escapes the workdir, does not exist, cannot be resolved,
            or is not a regular file.
    """
    if not workdir:
        return None, "No project workdir available — cannot validate local file path for security."

    workdir_resolved = Path(workdir).resolve()
    raw = Path(path_str)
    if not raw.is_absolute():
        raw = workdir_resolved / raw

    try:
        resolved = raw.resolve(strict=True)
    except FileNotFoundError:
        return None, f"Image file does not exist: {path_str}"
    except OSError as exc:
        return None, f"Cannot resolve path {path_str!r}: {exc}"

    try:
        resolved.relative_to(workdir_resolved)
    except ValueError:
        return None, f"Path {path_str!r} is outside the project workdir boundary."

    file_stat = resolved.stat()
    if not stat.S_ISREG(file_stat.st_mode):
        return None, f"Path is not a regular file: {path_str}"
    return resolved, None


def _load_image_from_path(path: str, workdir: str | None = None) -> str:
    """Read a local image file and return a base64 data URI.

    Uses ``_validate_local_path`` to enforce the project workdir boundary
    for BOTH relative and absolute paths — absolute paths are no longer a
    bypass. Fails closed (rejects the path) when no workdir is available
    so the tool cannot read arbitrary files outside the project.

    Args:
        path: Filesystem path to the image (absolute or relative to ``workdir``).
        workdir: Optional project workdir. When ``None`` the path is
            rejected — see ``_validate_local_path``.

    Returns:
        A ``data:image/{format};base64,{base64_data}`` string ready to pass
        to ``invoke_agent_and_wait`` via its ``images`` parameter.

    Raises:
        ValueError: If the path escapes the workdir, the file does not
            exist, is not a regular file, exceeds ``_MAX_IMAGE_BYTES``,
            or the magic-byte / extension check fails.
        OSError: If the read fails (permissions, I/O). Caught by caller.
    """
    resolved, err = _validate_local_path(path, workdir)
    if err:
        raise ValueError(err)
    assert resolved is not None  # for type-checkers

    file_stat = resolved.stat()
    if file_stat.st_size > _MAX_IMAGE_BYTES:
        raise ValueError(
            f"Image file exceeds maximum size of {_MAX_IMAGE_BYTES} bytes: {path}"
        )

    data = resolved.read_bytes()
    if not data:
        raise ValueError(f"Image file is empty: {path}")

    fmt = _detect_format_from_magic_bytes(data)
    if fmt is None:
        raise ValueError(
            f"Could not determine image format for {path!r}: "
            "magic bytes did not match any supported format."
        )

    hint = _detect_format_from_extension(resolved.suffix)
    if hint and hint != fmt:
        raise ValueError(
            f"Format mismatch for {path!r}: magic bytes indicate "
            f"{fmt!r} but extension hint was {hint!r}"
        )

    b64 = base64.b64encode(data).decode("ascii")
    return f"data:image/{fmt};base64,{b64}"


def _detect_format_from_content_type(content_type: str | None) -> str | None:
    """Map a Content-Type header value to a format slug, or ``None``."""
    if not content_type:
        return None
    primary = content_type.split(";", 1)[0].strip().lower()
    return _CONTENT_TYPE_TO_FORMAT.get(primary)


def _detect_format_from_url(url: str) -> str | None:
    """Map a URL's path extension to a format slug, or ``None``."""
    path = urlparse(url).path
    return _detect_format_from_extension(Path(path).suffix)


def _detect_format_from_extension(suffix: str) -> str | None:
    """Map a file extension (with or without leading dot) to a format slug."""
    if not suffix:
        return None
    return _EXT_TO_FORMAT.get(suffix.lower().lstrip("."))


def _detect_format_from_magic_bytes(data: bytes) -> str | None:
    """Detect image format from the file's leading magic bytes."""
    if not data:
        return None
    if data.startswith(_MAGIC_PNG):
        return "png"
    if data.startswith(_MAGIC_JPEG):
        return "jpeg"
    if data.startswith(_MAGIC_GIF87) or data.startswith(_MAGIC_GIF89):
        return "gif"
    # WebP: ``RIFF`` header at offset 0 with ``WEBP`` at offset 8.
    if len(data) >= 12 and data.startswith(_MAGIC_RIFF) and data[8:12] == _MAGIC_WEBP:
        return "webp"
    return None


def create_image_tools(manager: "InstanceManager", current_instance_id: str) -> list:
    """Create image analysis tools with injected manager reference.

    Args:
        manager: The InstanceManager instance to use for operations.
        current_instance_id: The ID of the current instance (used as parent
            for the spawned image-reader instance).

    Returns:
        List of tool functions: [explain_image]
    """

    def _get_project_id() -> str | None:
        """Auto-inject project_id from instance context."""
        try:
            # Use _instance_repository directly - get_instance() returns
            # CompiledStateGraph, not metadata.
            instance_meta = manager._instance_repository.get(current_instance_id)
            if instance_meta and instance_meta.project_id:
                return instance_meta.project_id
        except Exception:
            pass
        return None

    def _get_project_workdir() -> str | None:
        """Auto-inject project workdir from instance context.

        Used by ``_load_image_from_path`` to enforce the workdir
        boundary so the image tool cannot read files outside the
        project via symlinks, traversal, or absolute paths.
        """
        try:
            instance_meta = manager._instance_repository.get(current_instance_id)
            if instance_meta and instance_meta.project_id:
                project = manager._project_repository.get(instance_meta.project_id)
                if project and project.main_directory:
                    return project.main_directory
        except Exception:
            pass
        return None

    @register_tool_category("image")
    @tool
    async def explain_image(
        image: str,
        question: str = "Describe this image in detail",
    ) -> str:
        """Analyze an image by delegating to the image-reader agent.

        Fetches the image (from a URL or local path), converts it to a
        base64 data URI, and sends it to the image-reader agent along with
        a question. The agent uses a vision-capable model to interpret the
        image and answer the question. The image bytes — not just a URL
        string — are forwarded, so the model can actually see the content.

        URLs are validated against an SSRF guard (private/loopback IPs
        rejected, redirects walked manually up to ``_MAX_REDIRECT_HOPS``
        hops with each hop re-validated) and capped at 10 MB. Local paths
        must resolve inside the project workdir; both relative and absolute
        paths are confined. The tool fails closed (rejects the path) when no
        workdir is available.

        Args:
            image: URL (``http://`` / ``https://``) or local file path to an
                image. Supported formats: PNG, JPEG, GIF, WebP.
            question: The question to answer about the image. Defaults to
                "Describe this image in detail" which produces a thorough
                description; pass a more specific question to elicit a
                targeted answer.

        Returns:
            The image-reader agent's response analyzing the image. On
            failure (load error, unsupported format, SSRF block, size cap
            exceeded, network error, agent timeout) the tool returns a
            short ``"Error: ..."`` string — it never raises.
        """
        pid = _get_project_id()
        workdir = _get_project_workdir()

        # Load + base64-encode the image so the vision model actually sees
        # the bytes (URLs alone never reach the multimodal pipeline). All
        # errors are caught and surfaced as tool result strings so callers
        # can reason about them without exception handling.
        try:
            lowered = (image or "").strip().lower()
            if lowered.startswith("http://") or lowered.startswith("https://"):
                image_data_uri = await _load_image_from_url(image)
            else:
                image_data_uri = _load_image_from_path(image, workdir=workdir)
        except Exception as exc:
            logger.warning("Failed to load image for explain_image: %s", exc)
            return f"Error: Failed to load image {image!r}: {exc}"

        # Message body is just the question (+ optional project scope) —
        # the image is attached via the ``images`` parameter, never inline.
        image_message = question
        if pid:
            image_message += f"\nProject: {pid}"

        # Sanitize the instance name so a hostile URL/path cannot inject
        # weird characters (newlines, slashes, shell metacharacters) into
        # the spawned image-reader instance name.
        safe_image_name = re.sub(r"[^A-Za-z0-9._-]+", "_", image)[:30]
        instance_name = f"image-{safe_image_name}"

        # Invoke image-reader agent synchronously — the tool waits for the
        # vision model's analysis. Always returns ``(content, instance_id)``
        # tuple when ``return_instance_id=True``.
        result, child_instance_id = await invoke_agent_and_wait(
            manager=manager,
            agent_id="image-reader",
            message=image_message,
            project_id=pid,
            parent_id=current_instance_id,
            instance_name=instance_name,
            images=[image_data_uri],
            timeout=300.0,
            return_instance_id=True,
        )

        # Handle error results — ``invoke_agent_and_wait`` returns
        # ``"Error: ..."`` on failure / timeout when ``return_instance_id``
        # is True we still get the tuple; collapse to a single string for
        # the tool response. A ``None`` content means the agent never
        # produced a result (e.g. hard timeout during cleanup).
        if result is None:
            return "Error: image-reader agent timed out or failed. Try a different image or question."
        return result

    explain_image._full_doc_ = """\
Analyze an image by delegating to the image-reader agent.

Fetches the image (URL or local path), converts it to a base64 data
URI, and sends it to the image-reader agent along with a question.
The agent uses a vision-capable LLM to interpret the image and
answer the question. The image bytes — not just a URL string — are
forwarded, so the model can actually see the visual content.

The tool blocks until the agent produces its final response (default
``timeout`` = 300s) and returns the agent's text — a description,
answer, or analysis — directly to the caller.

URLs are validated against an SSRF guard (private/loopback IPs are
rejected and redirects are walked manually up to ``_MAX_REDIRECT_HOPS``
hops with each hop re-validated). Both URLs and local paths are
capped at ``_MAX_IMAGE_BYTES`` (10 MB). Local paths must resolve
inside the project workdir; both relative and absolute paths are
confined. The tool fails closed (rejects the path) when no workdir
is available. Format is detected from magic bytes
first, then verified against the Content-Type header / URL extension
/ file extension hint.

Args:
    image: URL (``http://`` / ``https://``) or local file path to an
        image. Supported formats: PNG, JPEG, GIF, WebP.
    question: The question to answer about the image. Defaults to
        "Describe this image in detail" which produces a thorough
        description. Try more specific questions to elicit targeted
        answers ("What is the error message on line 3?", "How many
        people are in this diagram?", "Summarize the architecture in
        this flowchart.").

Returns:
    image-reader agent's response analyzing the image. On failure
    (load error, unsupported format, SSRF block, network error,
    agent timeout) the tool returns a short ``"Error: ..."`` string
    — it never raises.

Example:
    >>> # Analyze a remote image:
    >>> explain_image("https://example.com/chart.png", "Summarize this chart.")

    >>> # Analyze a local image:
    >>> explain_image("/tmp/screenshot.png", "What error is shown?")
"""

    return [explain_image]
