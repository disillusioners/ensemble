"""Image analysis tools for vision-capable agents.

Mirrors the closure-injection pattern of ``daemon.tools.chart_tools`` and
``daemon.tools.knowledge_tools``: ``create_image_tools(manager, current_instance_id)``
is invoked from ``create_instance_tools`` to assemble the per-instance tool list.
The generated ``explain_image`` tool delegates to the ``image-reader`` agent via
``invoke_agent_and_wait`` and returns the vision model's analysis of the image.
"""

import base64
import logging
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


async def _load_image_as_data_uri(image: str) -> str:
    """Load an image from a URL or local path and return a base64 data URI.

    Accepts ``http://`` / ``https://`` URLs and filesystem paths. Format is
    detected from the Content-Type header (URL), URL path extension, file
    extension, or magic bytes — in that order.

    Args:
        image: URL or local filesystem path to an image (png/jpeg/gif/webp).

    Returns:
        A ``data:image/{format};base64,{base64_data}`` string ready to pass
        to ``invoke_agent_and_wait`` via its ``images`` parameter.

    Raises:
        ValueError: If the source string is empty, the format is unsupported,
            the URL cannot be reached, or the local file is missing.
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
    """Fetch a remote image and return a base64 data URI."""
    timeout = httpx.Timeout(_IMAGE_FETCH_TIMEOUT_S, connect=10.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.content
    except httpx.HTTPError:
        # Re-raise — caller (the tool body) converts to a user-facing error.
        raise

    fmt = _detect_format_from_content_type(response.headers.get("content-type"))
    if fmt is None:
        fmt = _detect_format_from_url(url)
    if fmt is None:
        fmt = _detect_format_from_magic_bytes(data)
    if fmt is None:
        raise ValueError(
            f"Could not determine image format for URL {url!r}: "
            "Content-Type, URL extension, and magic bytes all failed."
        )

    b64 = base64.b64encode(data).decode("ascii")
    return f"data:image/{fmt};base64,{b64}"


def _load_image_from_path(path: str) -> str:
    """Read a local image file and return a base64 data URI."""
    p = Path(path)
    if not p.exists():
        raise ValueError(f"Image file does not exist: {path}")
    if not p.is_file():
        raise ValueError(f"Image path is not a file: {path}")

    data = p.read_bytes()
    if not data:
        raise ValueError(f"Image file is empty: {path}")

    fmt = _detect_format_from_extension(p.suffix)
    if fmt is None:
        fmt = _detect_format_from_magic_bytes(data)
    if fmt is None:
        raise ValueError(
            f"Could not determine image format for {path!r}: "
            "unsupported extension and magic bytes."
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

        Args:
            image: URL (``http://`` / ``https://``) or local file path to an
                image. Supported formats: PNG, JPEG, GIF, WebP. URLs are
                fetched with a 30-second timeout and follow redirects;
                local paths are read directly from the filesystem.
            question: The question to answer about the image. Defaults to
                "Describe this image in detail" which produces a thorough
                description; pass a more specific question to elicit a
                targeted answer.

        Returns:
            The image-reader agent's response analyzing the image. On
            failure (load error, unsupported format, network error,
            agent timeout) the tool returns a short ``"Error: ..."``
            string — it never raises.
        """
        pid = _get_project_id()

        # Load + base64-encode the image so the vision model actually sees
        # the bytes (URLs alone never reach the multimodal pipeline). All
        # errors are caught and surfaced as tool result strings so callers
        # can reason about them without exception handling.
        try:
            image_data_uri = await _load_image_as_data_uri(image)
        except Exception as exc:
            logger.warning("Failed to load image for explain_image: %s", exc)
            return f"Error: Failed to load image {image!r}: {exc}"

        # Message body is just the question (+ optional project scope) —
        # the image is attached via the ``images`` parameter, never inline.
        image_message = question
        if pid:
            image_message += f"\nProject: {pid}"

        # Invoke image-reader agent synchronously — the tool waits for the
        # vision model's analysis. Always returns ``(content, instance_id)``
        # tuple when ``return_instance_id=True``.
        result, child_instance_id = await invoke_agent_and_wait(
            manager=manager,
            agent_id="image-reader",
            message=image_message,
            project_id=pid,
            parent_id=current_instance_id,
            instance_name=f"image-{image[:30]}",
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

Args:
    image: URL (``http://`` / ``https://``) or local file path to an
        image. Supported formats: PNG, JPEG, GIF, WebP. URLs are
        fetched with a 30-second timeout and follow redirects; local
        paths are read directly from the filesystem. Format is
        detected from (in order) Content-Type header, URL/file
        extension, then magic bytes.
    question: The question to answer about the image. Defaults to
        "Describe this image in detail" which produces a thorough
        description. Try more specific questions to elicit targeted
        answers ("What is the error message on line 3?", "How many
        people are in this diagram?", "Summarize the architecture in
        this flowchart.").

Returns:
    image-reader agent's response analyzing the image. On failure
    (load error, unsupported format, network error, agent timeout)
    the tool returns a short ``"Error: ..."`` string — it never
    raises.

Example:
    >>> # Analyze a remote image:
    >>> explain_image("https://example.com/chart.png", "Summarize this chart.")

    >>> # Analyze a local image:
    >>> explain_image("/tmp/screenshot.png", "What error is shown?")
"""

    return [explain_image]
