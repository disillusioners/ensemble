import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


_BASE64_IMAGE_PATTERN = re.compile(r'^data:image/(png|jpeg|jpg|gif|webp|bmp|tiff);base64,[A-Za-z0-9+/=]+$')


class MessageCreate(BaseModel):
    """Request for sending a message to an instance."""

    content: str = Field(..., description="Message content to send to the agent")
    images: list[str] | None = Field(default=None, description="Base64-encoded images (data URI format)")
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
