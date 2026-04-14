"""Utility functions for the ensemble daemon."""

import hashlib
import re

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


def _stable_message_id(msg) -> str:
    """Generate a stable ID for messages without msg.id.
    
    Uses a hash of role + content + tool_call_id so the same message
    always gets the same ID across re-emissions.
    
    Args:
        msg: LangChain BaseMessage with potentially no .id attribute.
    
    Returns:
        A deterministic 16-char hex string prefixed with "fallback-".
    """
    role = msg.type if hasattr(msg, 'type') else str(msg.__class__.__name__)
    content = getattr(msg, 'content', '') or ''
    content_str = content if isinstance(content, str) else str(content)
    tc_id = getattr(msg, 'tool_call_id', '') or ''
    
    key = f"{role}:{content_str[:200]}:{tc_id}"
    digest = hashlib.md5(key.encode('utf-8', errors='replace')).hexdigest()[:16]
    return f"fallback-{digest}"


def serialize_message(msg, tool_outputs: dict | None = None) -> dict:
    """Serialize a LangChain message to dict matching REST API format.
    
    Must handle all 5 thinking extraction paths:
      1. additional_kwargs.get("reasoning_content")
      2. additional_kwargs.get("thinking")  
      3. msg.reasoning_content attribute
      4. msg.thinking attribute (Claude models)
      5. msg.content as list with type="reasoning" blocks
    
    Args:
        msg: LangChain BaseMessage (HumanMessage, AIMessage, ToolMessage, etc.)
        tool_outputs: Optional map of tool_call_id -> output content.
    
    Returns:
        Dict with message_id, role, content, thinking, tool_calls, created_at.
    """
    role_map = {"human": "user", "ai": "assistant", "system": "system", "tool": "tool"}
    role = role_map.get(msg.type, msg.type)
    content = getattr(msg, 'content', '') or ''
    
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
    content_str = content if isinstance(content, str) else str(content)
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
        "message_id": getattr(msg, 'id', None) or _stable_message_id(msg),
        "role": role,
        "content": content_str,
        "thinking": thinking,
        "thinking_extracted": thinking_extracted,
        "tool_calls": tool_calls,
        "created_at": None,
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
