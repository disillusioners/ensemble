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

    Phase 5 (Option B): ``message_id`` is now OPTIONAL. Under the
    Job-as-Front-Primitive (JAFP) cutover, ``enqueue_message_job`` no
    longer creates the Task row at enqueue time — the Task is created
    later at dispatch time inside ``JobProcessor._process_next_job``'s
    message branch. The HTTP response therefore can only carry the
    ``job_id`` (JobItem mirror) immediately; the ``message_id`` (Task
    row) is populated when the dispatch runs. Callers that need
    ``message_id`` for round-trip correlation should poll
    ``GET /api/instances/{id}/jobs/{job_id}`` or subscribe to the
    ``message_queued`` / ``message_dispatched`` SSE events emitted by
    ``JobProcessor``.

    ``None`` is a transient, valid steady state — *the message is
    queued, but not yet dispatched*. The field becomes a non-null
    string after ``JobProcessor`` creates the matching Task row.
    """

    message_id: str | None = Field(
        default=None,
        description=(
            "Unique message identifier — IDENTIFIER OF THE TASK ROW, not the "
            "JobItem mirror. ``None`` until ``JobProcessor`` dispatches the "
            "message and creates the Task row; populated when the Task is "
            "actually created. Use ``job_id`` for immediate JobItem-mirror "
            "correlation."
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
            }
        }
    )


__all__ = ["MessageCreate", "MessageResponse"]
