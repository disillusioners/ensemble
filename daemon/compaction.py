"""Context compaction utilities for managing token limits.

This module provides configuration and utilities for context window management
and token estimation. The compaction engine itself (actual message pruning
strategies) will be added in later phases.

Key components:
- MODEL_CONTEXT_LIMITS: Registry of model context window sizes
- get_model_context_limit(): Lookup function with fuzzy matching
- estimate_tokens(): Token counting via tiktoken (imported from loader)
"""

from typing import Optional

# Import from loader module for token estimation
from .loader import estimate_tokens

# Context window sizes for known models (in tokens)
MODEL_CONTEXT_LIMITS: dict[str, int] = {
    # OpenAI models
    "gpt-4": 8192,
    "gpt-4-turbo": 128000,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4.5": 128000,
    "gpt-3.5-turbo": 16385,
    "o1": 200000,
    "o1-mini": 128000,
    "o3-mini": 200000,
    
    # Anthropic models (via OpenAI-compatible API)
    "claude-3-opus": 200000,
    "claude-3-sonnet": 200000,
    "claude-3-haiku": 200000,
    "claude-3.5-sonnet": 200000,
    "claude-3.5-haiku": 200000,
    "claude-4": 200000,
    
    # Open-source models
    "llama-3": 8192,
    "llama-3.1": 128000,
    "mistral": 32000,
    "mixtral": 32000,
    "deepseek": 128000,
    "qwen": 32768,
}

DEFAULT_CONTEXT_LIMIT = 128000


def get_model_context_limit(model_name: str, config: Optional[object] = None) -> int:
    """Get the context window limit for a given model name.
    
    Uses fuzzy matching against MODEL_CONTEXT_LIMITS registry.
    If config has context_window_override set (non-zero), that takes priority.
    
    Args:
        model_name: Model identifier string (e.g., "gpt-4o", "claude-3.5-sonnet")
        config: Optional config object with context_window_override attribute.
                If provided and context_window_override > 0, overrides the 
                registry lookup.
                
    Returns:
        Context window size in tokens.
    """
    # Config override takes priority
    if config is not None:
        override = getattr(config, 'context_window_override', 0)
        if override > 0:
            return override
    
    # Normalize model name for matching
    normalized = model_name.lower().strip()
    
    # Direct match first
    if normalized in MODEL_CONTEXT_LIMITS:
        return MODEL_CONTEXT_LIMITS[normalized]
    
    # Fuzzy match: check if any registry key is contained in the model name
    # Check longer keys first to get more specific matches
    for key in sorted(MODEL_CONTEXT_LIMITS.keys(), key=len, reverse=True):
        if key in normalized:
            return MODEL_CONTEXT_LIMITS[key]
    
    return DEFAULT_CONTEXT_LIMIT
