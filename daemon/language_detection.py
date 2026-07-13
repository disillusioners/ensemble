"""Language detection heuristics for the language check node."""
import re
import logging

logger = logging.getLogger(__name__)

# CJK Unicode ranges (Chinese, Japanese, Korean)
CJK_PATTERN = re.compile(
    r'[\u4e00-\u9fff'   # CJK Unified Ideographs
    r'\u3400-\u4dbf'     # CJK Extension A
    r'\u3040-\u309f'     # Hiragana
    r'\u30a0-\u30ff'     # Katakana
    r'\uac00-\ud7af'     # Hangul
    r']+'
)

# W1 FIX: Only unambiguously Spanish words.
# EXCLUDED: 'no', 'a', 'en', 'con', 'sin', 'si', 'lo', 'al', 'que', 'y'
# — all valid English words that caused false positives.
SPANISH_INDICATORS = {
    'porque', 'cuando', 'donde', 'quién', 'cómo', 'qué',
    'bueno', 'malo', 'hacer', 'tener', 'decir', 'poder',
    'querer', 'saber', 'venir', 'pasar', 'deber', 'poner',
    'parecer', 'quedar', 'creer', 'hablar', 'llevar', 'dejar',
    'seguir', 'encontrar', 'llamar', 'entonces', 'también',
    'ahora', 'después', 'antes', 'aquí', 'allí', 'muy',
    'mucho', 'poco', 'todo', 'otro', 'mismo', 'tanto',
    'nuestro', 'vuestro', 'suyo', 'mía', 'tuya', 'suya',
    'está', 'están', 'era', 'fueron', 'sea', 'ser',
    'ha', 'han', 'había', 'tendrá', 'podría', 'querría',
}

# W1 FIX: Raised from 30% to 50%
SPANISH_RATIO_THRESHOLD = 0.50
# W1 FIX: Minimum absolute count for short responses
SPANISH_MIN_ABSOLUTE_COUNT = 5


def _normalize_content(content) -> str:
    """Normalize message content to a string.

    Handles:
    - str → returned as-is
    - list (multimodal) → extracts text blocks and joins them
    - None → empty string
    - Other → str(content)
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # Multimodal content: [{"type": "text", "text": "..."}, {"type": "image_url", ...}]
        text_parts = []
        for block in content:
            if isinstance(block, dict) and block.get("text"):
                text_parts.append(block["text"])
            elif isinstance(block, str):
                text_parts.append(block)
        return " ".join(text_parts)
    return str(content)


def strip_code_blocks(content: str) -> str:
    """Remove fenced code blocks (```...```) from content before language detection."""
    return re.sub(r'```[\s\S]*?```', '', content)


def has_cjk_characters(content: str) -> bool:
    """Check if content contains any CJK characters."""
    return bool(CJK_PATTERN.search(content))


def spanish_word_count(content: str) -> tuple[int, int]:
    """Count Spanish indicator words and total words.

    Returns:
        Tuple of (spanish_count, total_word_count).
    """
    words = re.findall(r'\b[a-zA-ZñáéíóúüÁÉÍÓÚÜ]+\b', content.lower())
    if not words:
        return (0, 0)
    spanish_count = sum(1 for w in words if w in SPANISH_INDICATORS)
    return (spanish_count, len(words))


def detect_wrong_language(content, preferred_language: str) -> bool:
    """Check if content is in a language different from the preferred language.

    Args:
        content: The assistant message content (str or list for multimodal).
        preferred_language: The user's preferred language (e.g., "English").
            ``None`` or ``"Auto"`` (case-insensitive) means "no preference" —
            the check is disabled and this function always returns False.

    Returns:
        True if the content appears to be in the wrong language.

    Detection rules:
    - C2 FIX: English preference IS checked (detects CJK/Spanish drift)
    - W1 FIX: Spanish detection uses cleaned word list, 50% threshold, ≥5 absolute words
    - W4 FIX: Content is normalized to string (handles multimodal list content)
    - Code blocks are stripped before detection
    - Empty content → not wrong (let other logic handle empty)
    - "Auto" / None preference → not wrong (language check disabled)
    """
    # Defense-in-depth: when preference is "Auto" (or unset), there is no
    # language to enforce — never flag the response as wrong. The graph
    # node itself is skipped via language_check_enabled=False in
    # build_instance_graph(), but this guard protects any other caller.
    if not preferred_language:
        return False
    # Lazy import breaks the circular dependency: daemon.services.__init__
    # pulls in instance_lifecycle → compaction → graph → language_detection,
    # so a module-level ``from .services.language_utils import ...`` would
    # re-enter this module mid-load.
    from .services.language_utils import is_auto_language
    if is_auto_language(preferred_language):
        return False

    # W4 FIX: Normalize content to string
    text = _normalize_content(content)

    if not text or not text.strip():
        return False

    # Strip code blocks before detection
    clean_content = strip_code_blocks(text)
    if not clean_content.strip():
        return False  # Content was entirely code blocks

    preferred = preferred_language.lower().strip()

    # Vietnamese shares the English detection profile: CJK and Spanish drift
    # are flagged, while English text is allowed (Vietnamese users commonly
    # code-switch in/out of English).
    if preferred in ("english", "vietnamese"):
        if has_cjk_characters(clean_content):
            return True
        # W1 FIX: 50% threshold + ≥5 absolute count
        spanish_count, total_words = spanish_word_count(clean_content)
        if total_words > 0:
            ratio = spanish_count / total_words
            if ratio >= SPANISH_RATIO_THRESHOLD and spanish_count >= SPANISH_MIN_ABSOLUTE_COUNT:
                return True
        return False

    # For non-English preferences: check if content lacks the preferred language
    if preferred in ("chinese", "中文", "mandarin"):
        if not has_cjk_characters(clean_content):
            return True
        return False

    if preferred in ("spanish", "español"):
        spanish_count, total_words = spanish_word_count(clean_content)
        if total_words > 0 and spanish_count < SPANISH_MIN_ABSOLUTE_COUNT:
            return True
        return False

    # For other languages, we don't have detection heuristics — skip check
    return False