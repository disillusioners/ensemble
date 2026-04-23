"""Utility functions for the ensemble daemon."""

import hashlib
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeVar, Callable, Optional, Any

from fastapi import HTTPException

from daemon.models import ErrorCodes, ErrorResponse
from daemon.registry import get_registry

# Pattern for parsing <think/> tags
_THINK_PATTERN = re.compile(r'<think[^>]*>(.*?)</think\s*>', re.DOTALL | re.IGNORECASE)


def parse_think_tags(content: str) -> tuple[str, str | None]:
    """Parse <think/> tags from message content.
    
    Extracts thinking content from <think...>...</think tags and removes them
    from the content string. Handles multiple think blocks by combining them
    with newlines.
    
    Args:
        content: The message content potentially containing think tags.
        
    Returns:
        Tuple of (cleaned_content, thinking_extracted) where:
        - cleaned_content: Content with think tags removed
        - thinking_extracted: Combined content from think tags, or None if none found
    """
    think_matches = _THINK_PATTERN.findall(content)
    if think_matches:
        thinking_extracted = '\n'.join(think_matches).strip()
        cleaned_content = _THINK_PATTERN.sub('', content).strip()
        return cleaned_content, thinking_extracted
    return content, None



def _extract_timestamp(msg) -> str:
    """Extract timestamp from message metadata or return current UTC time.
    
    Args:
        msg: LangChain message with potentially no timestamp.
    
    Returns:
        ISO format timestamp string (always non-null).
    """
    if hasattr(msg, 'response_metadata') and msg.response_metadata:
        metadata = msg.response_metadata
        # Only check known timestamp keys — no fuzzy matching
        for key in ('created_at', 'timestamp'):
            val = metadata.get(key)
            if val:
                if isinstance(val, str) and len(val) >= 10:
                    return val
                if hasattr(val, 'isoformat'):
                    return val.isoformat()
    return datetime.now(timezone.utc).isoformat()


def serialize_message(msg, tool_outputs: dict | None = None, message_id: str | None = None) -> dict:
    """Serialize a LangChain message to dict matching REST API format.
    
    Must handle all 5 thinking extraction paths:
      1. additional_kwargs.get("reasoning_content")
      2. additional_kwargs.get("thinking")  
      3. msg.reasoning_content attribute
      4. msg.thinking attribute (Claude models)
      5. msg.content as list with type="reasoning" blocks
    
    Also handles multimodal content (images):
      - Extracts image URLs from content blocks with type="image_url"
      - Preserves images in serialization for checkpoint persistence
    
    Args:
        msg: LangChain BaseMessage (HumanMessage, AIMessage, ToolMessage, etc.)
        tool_outputs: Optional map of tool_call_id -> output content.
        message_id: Optional explicit message ID to use. If not provided,
            falls back to msg.id or generates a new UUID.
    
    Returns:
        Dict with message_id, role, content, thinking, tool_calls, images, created_at.
    """
    role_map = {"human": "user", "ai": "assistant", "system": "system", "tool": "tool"}
    role = role_map.get(msg.type, msg.type)
    content = getattr(msg, 'content', '') or ''
    
    # Extract images from multimodal content (list format)
    images: list[str] | None = None
    content_str = content if isinstance(content, str) else ""
    
    if isinstance(content, list):
        # This is multimodal content - extract text and images
        text_parts: list[str] = []
        images_list: list[str] = []
        
        for block in content:
            if isinstance(block, dict):
                block_type = block.get("type")
                if block_type == "text":
                    text_parts.append(block.get("text", ""))
                elif block_type == "image_url":
                    # Extract image URL from image_url block
                    img_url = block.get("image_url", {})
                    if isinstance(img_url, dict):
                        url = img_url.get("url", "")
                    else:
                        url = str(img_url)
                    if url:
                        images_list.append(url)
        
        content_str = " ".join(text_parts)
        images = images_list if images_list else None
    
    # Thinking extraction (5 paths)
    thinking = None
    if hasattr(msg, 'additional_kwargs'):
        kwargs = msg.additional_kwargs or {}
        thinking = kwargs.get("reasoning_content") or kwargs.get("thinking")
    if not thinking and hasattr(msg, 'reasoning_content') and msg.reasoning_content:
        thinking = msg.reasoning_content
    if not thinking and hasattr(msg, 'thinking') and msg.thinking:
        thinking = msg.thinking
    if not thinking and isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "reasoning":
                    thinking = block.get("reasoning") or block.get("summary_text", "")
                    break
    
    # Parse <think/> tags from content
    content_str, thinking_extracted = parse_think_tags(content_str)
    
    # Tool calls for AIMessage
    tool_calls = None
    if hasattr(msg, 'tool_calls') and msg.tool_calls:
        tool_outputs = tool_outputs or {}
        tool_calls = []
        for tc in msg.tool_calls:
            if isinstance(tc, dict):
                tc_id = tc.get("id", "")
                tool_calls.append({
                    "id": tc_id,
                    "name": tc.get("name", ""),
                    "arguments": tc.get("args", {}),
                    "output": tool_outputs.get(tc_id),
                })
            else:
                tc_id = getattr(tc, "id", "")
                tool_calls.append({
                    "id": tc_id,
                    "name": getattr(tc, "name", ""),
                    "arguments": getattr(tc, "args", {}),
                    "output": tool_outputs.get(tc_id),
                })
    
    return {
        "message_id": str(message_id) if message_id else getattr(msg, 'id', None) or str(uuid.uuid4()),
        "type": msg.type,
        "role": role,
        "content": content_str,
        "thinking": thinking,
        "thinking_extracted": thinking_extracted,
        "tool_calls": tool_calls,
        "images": images,
        "created_at": _extract_timestamp(msg),
    }


# Sequence counter for checkpoint events
_sequence_counter: dict[str, int] = {}


def get_next_sequence(instance_id: str) -> int:
    """Get next monotonically incrementing sequence number for an instance.
    
    Args:
        instance_id: The instance to get sequence for.
    
    Returns:
        The next sequence number (starts at 1).
    """
    current = _sequence_counter.get(instance_id, 0)
    next_seq = current + 1
    _sequence_counter[instance_id] = next_seq
    return next_seq


# Legacy compatibility wrapper - to be removed in Phase 4
def compute_message_id(instance_id: str, role: str, content: str) -> str:
    """Legacy wrapper for compute_message_id compatibility.
    
    This is a temporary shim. Phase 4 will refactor message_service.py
    to use checkpoint-based msg.id instead of this function.
    
    Args:
        instance_id: Unused (kept for API compatibility).
        role: Unused (kept for API compatibility).
        content: Content to hash for stable ID.
    
    Returns:
        A deterministic ID based on content hash.
    """
    content_str = content if isinstance(content, str) else str(content)
    key = f"legacy:{content_str[:200]}"
    digest = hashlib.md5(key.encode('utf-8', errors='replace')).hexdigest()[:16]
    return f"legacy-{digest}"


# ── DateTime Utilities ──

def parse_utc_datetime(value: str | datetime | None) -> datetime | None:
    """Parse a datetime string or pass through a datetime object, ensuring UTC.
    
    Centralizes the repeated pattern:
        datetime.fromisoformat(x).replace(tzinfo=timezone.utc) if isinstance(x, str) else x
    
    Args:
        value: ISO format datetime string, datetime object, or None.
        
    Returns:
        datetime object with UTC timezone, or None if input is None.
        
    Note:
        This function intentionally fixes edge cases from the original inline patterns:
        - None values are returned as None (previously handled inconsistently)
        - datetime objects pass through unchanged with UTC normalization
    """
    if value is None:
        return None
    if isinstance(value, str):
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    return value


# ── HTTP Exception Helpers ──

def raise_not_found(detail: str = "Resource not found") -> None:
    """Raise a 404 HTTPException."""
    raise HTTPException(status_code=404, detail=detail)

def raise_service_unavailable(detail: str = "Service not initialized") -> None:
    """Raise a 503 HTTPException."""
    raise HTTPException(status_code=503, detail=detail)

def raise_bad_request(detail: str = "Bad request") -> None:
    """Raise a 400 HTTPException."""
    raise HTTPException(status_code=400, detail=detail)


# ── Service Dependency Factory ──

T = TypeVar("T")

def create_service_dependency(service_type: type[T]) -> Callable[[], T]:
    """Creates get/set functions for FastAPI service injection.
    
    Replaces the repeated global+getter+setter pattern in routers:
        _service: Optional[SomeType] = None
        def get_service() -> SomeType: ...
        def set_service(svc: SomeType) -> None: ...
    
    Usage:
        get_my_service = create_service_dependency(MyService)
        # get_my_service() -> raises 503 if not set
        # get_my_service.set_service(instance) -> sets the instance
    """
    _instance: Optional[T] = None

    def get_service() -> T:
        nonlocal _instance
        if _instance is None:
            raise_service_unavailable(f"{service_type.__name__} not initialized")
        return _instance

    def set_service(instance: T) -> None:
        nonlocal _instance
        _instance = instance

    get_service.set_service = set_service  # type: ignore[attr-defined]
    return get_service


# ── Schedule Instance Mode Validation ──

def validate_instance_mode(
    instance_mode: str | None,
    schedule_type: str | None = None,
    config: dict | None = None,
) -> dict[str, Any]:
    """Validate instance_mode and return processed config.
    
    Args:
        instance_mode: The instance mode to validate ('new_instance', 'reuse_instance', or None).
        schedule_type: The schedule type ('cron', 'interval', 'one_time') if known.
        config: The schedule config dict to potentially modify.
        
    Returns:
        Processed config dict with instance_mode set appropriately.
        
    Raises:
        HTTPException: If instance_mode is invalid.
    """
    import logging
    from daemon.models import ErrorCodes, ErrorResponse
    from fastapi import HTTPException
    
    logger = logging.getLogger(__name__)
    VALID_INSTANCE_MODES = {"new_instance", "reuse_instance"}
    default_instance_mode = "new_instance"
    
    # Determine schedule type from config if not provided
    if schedule_type is None and config:
        if "run_at" in config and config["run_at"]:
            schedule_type = "one_time"
        elif "interval_seconds" in config:
            schedule_type = "interval"
        elif "schedule" in config:
            schedule_type = "cron"
    
    # For one_time schedules: ALWAYS force to new_instance
    if schedule_type == "one_time":
        if instance_mode is not None and instance_mode != "new_instance":
            logger.info("Forcing instance_mode to 'new_instance' for one_time schedule")
        return {"instance_mode": "new_instance"}
    
    # Validate instance_mode if provided
    if instance_mode is not None and instance_mode not in VALID_INSTANCE_MODES:
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message=f"Invalid instance_mode: '{instance_mode}'. Valid options: {list(VALID_INSTANCE_MODES)}"
            ).model_dump()
        )
    
    # Use provided value or default
    resolved_mode = instance_mode if instance_mode is not None else default_instance_mode
    
    return {"instance_mode": resolved_mode}


# ── Fuzzy String Matching ──

def edit_distance(s1: str, s2: str) -> int:
    """Calculate Levenshtein edit distance between two strings.
    
    Args:
        s1: First string.
        s2: Second string.
        
    Returns:
        The minimum number of edit operations (insertions, deletions, substitutions)
        needed to transform s1 into s2.
    """
    if len(s1) < len(s2):
        return edit_distance(s2, s1)
    
    if len(s2) == 0:
        return len(s1)
    
    previous_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            # cost is 0 if characters match, 1 otherwise
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    
    return previous_row[-1]


def find_near_instance(instance_id: str, instances: list, max_distance: int = 2) -> str | None:
    """Find a near-matching instance ID from recent instances using edit distance.
    
    Searches through instances using edit distance to find a close match.
    Matching is case-insensitive.
    
    Args:
        instance_id: The instance ID to find a near match for.
        instances: List of instance objects with instance_id attribute.
        max_distance: Maximum edit distance threshold (default: 2).
        
    Returns:
        The near-matching instance_id if found, None otherwise.
    """
    # Normalize input for case-insensitive comparison
    normalized_input = instance_id.lower()
    
    for instance in instances:
        # Skip if length difference exceeds threshold (quick filter)
        stored_id = instance.instance_id
        if abs(len(stored_id) - len(instance_id)) > max_distance:
            continue
        
        # Case-insensitive edit distance
        distance = edit_distance(normalized_input, stored_id.lower())
        if distance <= max_distance:
            return stored_id
    
    return None


# ── Agent Validation (relocated from daemon.api) ──

def validate_agent_id(agent_id: str) -> tuple[str, Path]:
    """Validate agent_id exists and return agent_id with path.
    
    This is the preferred function for validating agent references.
    
    Args:
        agent_id: The agent identifier to validate.
        
    Returns:
        Tuple of (agent_id, resolved_absolute_path).
        
    Raises:
        HTTPException: If agent is invalid or not found.
    """
    registry = get_registry()
    
    # Check agent exists
    metadata = registry.get(agent_id)
    if metadata is None:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message=f"Agent not found: {agent_id}"
            ).model_dump()
        )
    
    return agent_id, metadata.path
