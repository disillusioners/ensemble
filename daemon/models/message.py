import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_BASE64_IMAGE_PATTERN = re.compile(r'^data:image/(png|jpeg|jpg|gif|webp|bmp|tiff);base64,[A-Za-z0-9+/=]+$')


# C3-fixed (round 2) regex for ``image_refs`` entries. Accepts ALL THREE
# canonical input forms (the round-1 ``^(tmpimg://)?[a-f0-9]{32}$`` rejected
# the canonical URL ``/api/tmp_images/<32hex>`` form and is DELETED):
#
#   * bare 32-hex id (``a`` * 32)
#   * ``tmpimg://<32hex>`` accepted-input alias (NEVER emitted, NEVER persisted)
#   * ``/api/tmp_images/<32hex>`` canonical URL form (canonical persisted)
#
# The bare-id form is what the FE / curl callers will commonly send; the
# tmpimg:// alias is parse-tolerant input; the URL form is the canonical
# canonical-emitted wire form. ``normalize_image_ref_to_canonical_url``
# (this module) maps all three to the canonical URL at the seam so only
# the URL form ever lands in ``MessageQueue.images`` (audit column) and
# in the agent-facing list returned by GET /messages.
_IMAGE_REF_PATTERN = re.compile(r'^((tmpimg://)|(/api/tmp_images/))?[a-f0-9]{32}$')

# The canonical emitted/persisted URL prefix. Every entry in
# ``MessageQueue.images`` (and the wire ``images`` list returned by
# GET /messages after the union) is this prefix + the 32-hex id.
_TMP_IMAGE_CANONICAL_URL_PREFIX = "/api/tmp_images/"


def normalize_image_ref_to_canonical_url(ref: str) -> str:
    """Normalize any of the 3 accepted input forms to the canonical URL.

    Round-2 amendment #26 / decisions.md §2: the canonical persisted and
    emitted form is ``/api/tmp_images/<32hex>``. The ``tmpimg://`` alias
    is accepted on INPUT only (validator + GET endpoint) and is NEVER
    emitted or persisted. A bare 32-hex id is also accepted on input
    and gets prefixed here.

    The validator already rejects malformed entries before we get here,
    so no defensive validation is performed — this helper assumes the
    input matched :data:`_IMAGE_REF_PATTERN`.
    """
    if ref.startswith(_TMP_IMAGE_CANONICAL_URL_PREFIX):
        return ref
    if ref.startswith("tmpimg://"):
        return f"{_TMP_IMAGE_CANONICAL_URL_PREFIX}{ref[len('tmpimg://'):]}"
    # bare 32-hex
    return f"{_TMP_IMAGE_CANONICAL_URL_PREFIX}{ref}"


class MessageCreate(BaseModel):
    """Request for sending a message to an instance.

    TWO signature-separated channels (round-2 amendment #26 / C1):

    * ``images`` — legacy data-URI vision path. Optional ``list[str]`` of
      ``data:image/...;base64,...`` strings. Sends a multimodal
      HumanMessage and requires ``config.llm.model_vision`` to be set
      (the existing vision gate at
      ``daemon/routers/messages.py:222-231`` enforces this).
    * ``image_refs`` — NEW display-only channel. Optional ``list[str]``
      of refs in any of the 3 accepted forms (bare 32-hex,
      ``tmpimg://<32hex>``, ``/api/tmp_images/<32hex>``). Each ref is
      converted to a text description via the image-reader agent
      BEFORE the existing vision gate, prepended to the message
      content, then the canonical URL form persists on the
      ``MessageQueue.images`` JSONB column (audit) and stamps the
      ``HumanMessage.additional_kwargs["image_refs"]`` checkpoint
      sidecar (display). The main chat agent is NEVER switched to
      vision on this path.

    The two channels are XOR-validated at the model layer (Task 3).
    """

    content: str = Field(..., description="Message content to send to the agent")
    images: list[str] | None = Field(default=None, description="Base64-encoded images (data URI format)")
    image_refs: list[str] | None = Field(
        default=None,
        description=(
            "Optional list of refs to clipboard images previously uploaded "
            "via ``POST /api/tmp_images``. Each entry accepts any of the "
            "3 canonical input forms — bare 32-hex id, ``tmpimg://<32hex>``, "
            "or the canonical URL ``/api/tmp_images/<32hex>``. The "
            "backend converts each ref to a text description and prepends "
            "it to the message content; the canonical URL form persists "
            "with the message (audit + display). XOR with ``images`` — "
            "a request may carry at most ONE channel non-empty."
        ),
    )
    queue_id: str | None = Field(
        default=None,
        description=(
            "Optional JobQueue ``queue_id`` to route the message JobItem mirror to. "
            "When omitted (or empty) the default ``system_parallel_queue`` is used. "
            "Invalid IDs and IDs belonging to a different project fall back to the "
            "default queue with a WARNING log — graceful degradation by design."
        ),
    )

    @field_validator("images")
    @classmethod
    def validate_images(cls, v: list[str] | None) -> list[str] | None:
        """Validate images: max 3, valid base64 data URI format, max 10MB each.
        
        Also converts empty list to None for clarity.
        """
        if v is None:
            return None
        
        # Convert empty list to None for clarity
        if len(v) == 0:
            return None
        
        if len(v) > 3:
            raise ValueError("Maximum 3 images allowed per message")
        
        for i, img in enumerate(v):
            if not _BASE64_IMAGE_PATTERN.match(img):
                raise ValueError(
                    f"Invalid image format at index {i}: must be a base64 data URI "
                    f"(e.g., 'data:image/png;base64,...')"
                )
            
            # Estimate original size from base64: base64_size * 3/4 ≈ original size
            # Max 10MB = 10 * 1024 * 1024 bytes
            # Use only the base64 portion (after the comma) for accurate size calculation
            base64_str = img.split(",", 1)[1] if "," in img else img[len("data:image/png;base64,"):]
            base64_size = len(base64_str)
            original_size = base64_size * 3 // 4
            max_size = 10 * 1024 * 1024  # 10MB
            if original_size > max_size:
                raise ValueError(
                    f"Image at index {i} exceeds maximum size of 10MB "
                    f"(estimated: {original_size / (1024*1024):.1f}MB)"
                )
        
        return v

    @field_validator("image_refs")
    @classmethod
    def validate_image_refs(cls, v: list[str] | None) -> list[str] | None:
        """Validate ``image_refs``: max 3 entries; each must match the C3 regex.

        The C3 (round 2) regex
        ``^((tmpimg://)|(/api/tmp_images/))?[a-f0-9]{32}$`` accepts all
        THREE canonical input forms: bare 32-hex, ``tmpimg://<32hex>``,
        and ``/api/tmp_images/<32hex>``. The round-1 regex
        ``^(tmpimg://)?[a-f0-9]{32}$`` rejected the URL form — fixed.

        Empty list is coerced to ``None`` (same convention as
        ``images`` — "absent" is the canonical zero-image state).

        The validator does NOT normalise the entries — that is the
        router-seam converter's job. Pydantic's validator returns the
        input unchanged so the converter can decide whether to
        normalise before the legacy ``images`` column write (it does).
        """
        if v is None:
            return None
        if len(v) == 0:
            return None
        if len(v) > 3:
            raise ValueError(
                f"Maximum 3 image_refs allowed per message (got {len(v)})"
            )
        for i, ref in enumerate(v):
            if not _IMAGE_REF_PATTERN.match(ref):
                raise ValueError(
                    f"Invalid image_ref at index {i}: {ref!r} — must be one "
                    f"of: bare 32-hex id, 'tmpimg://<32hex>', or "
                    f"'/api/tmp_images/<32hex>'"
                )
        return v

    @model_validator(mode="after")
    def _xor_images_and_image_refs(self) -> "MessageCreate":
        """Reject requests that carry BOTH images and image_refs.

        The two channels are signature-separated (round-2 amendment #26 /
        C1) and never overlap in a single request — XOR is the
        canonical-zero-or-one invariant. This is the test-freeze-list
        A5 guard. Pydantic raises ``ValidationError`` which the FastAPI
        layer turns into 422.
        """
        has_images = self.images is not None and len(self.images) > 0
        has_refs = self.image_refs is not None and len(self.image_refs) > 0
        if has_images and has_refs:
            raise ValueError(
                "Request may carry at most one image channel: 'images' "
                "(data-URI) OR 'image_refs' (clipboard refs) — not both. "
                "Use 'image_refs' for the new clipboard flow."
            )
        return self

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "content": "Hello, agent!",
                "queue_id": None,
            }
        }
    )


class MessageResponse(BaseModel):
    """Response after sending a message.

    Option B (synchronous Task contract): ``message_id`` is REQUIRED and
    non-null. Under the new contract, ``enqueue_message_job`` creates
    the ``MessageQueue`` + ``Task`` rows synchronously (via
    ``_prepare_enqueued_message``) BEFORE the JobItem is enqueued; the
    HTTP response therefore carries the real ``message_id`` immediately
    (the Task row's ``message_id`` column).

    The ``job_id`` field is the JobItem's UUID4, which equals the
    Task's ``work_id`` (the linkage contract maintained via
    ``_prepare_enqueued_message(work_id=job_id)``). Both handles are
    populated at response time; no asynchronous correlation step is
    needed.
    """

    message_id: str = Field(
        ...,
        description=(
            "Unique message identifier — IDENTIFIER OF THE TASK ROW's "
            "``message_id`` column. Created synchronously in "
            "``enqueue_message_job`` via ``_prepare_enqueued_message`` "
            "before the JobItem is enqueued; the HTTP response carries "
            "the real ``message_id`` immediately. Use ``job_id`` for "
            "JobItem-mirror correlation (== Task.work_id)."
        ),
    )
    role: str = Field(..., description="Message role (always 'assistant')")
    content: str | None = Field(default=None, description="Message content")
    thinking: str | None = Field(default=None, description="Thinking from metadata (reasoning_content, etc.)")
    thinking_extracted: str | None = Field(default=None, description="Thinking extracted from <think/> tags in content")
    tool_calls: list[dict[str, Any]] | None = Field(default=None, description="Tool calls made by the agent")
    images: list[str] | None = Field(default=None, description="Images in the message (for vision messages)")
    created_at: datetime = Field(..., description="Message creation timestamp")
    job_id: str | None = Field(
        default=None,
        description=(
            "Identifier for the dispatch work unit (Task.work_id == "
            "JobItem.job_id). Allows callers to track the job through the "
            "WorkResolver facade."
        ),
    )
    queued: bool = Field(
        default=False,
        description=(
            "True when the message JobItem is waiting for a queue slot at "
            "the moment the response is built (JobItem.admission_state == "
            "'queued'). False once the worker claims the slot "
            "('active') or the JobItem cannot be read. Lets the frontend "
            "show a 'queued' indicator without an extra round-trip; the "
            "value reflects a snapshot, not a live subscription — clients "
            "needing progress should subscribe to the SSE stream."
        ),
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message_id": "msg-456",
                "role": "assistant",
                "content": "Hello! How can I help you?",
                "thinking": None,
                "thinking_extracted": None,
                "tool_calls": None,
                "images": None,
                "created_at": "2024-01-01T00:00:00Z",
                "job_id": "11111111-2222-3333-4444-555555555555",
                "queued": False,
            }
        }
    )


__all__ = ["MessageCreate", "MessageResponse"]
