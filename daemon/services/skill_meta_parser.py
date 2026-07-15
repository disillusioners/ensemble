"""Parser for <meta> control tags embedded in agent messages.

Agents embed machine-readable control data in `<meta>...</meta>` tags inside
their text replies. This module strips those tags from the human-visible
message and surfaces the parsed payload to the host system.

The host uses `parse_meta_tag` to clean agent output before it is shown to the
user and `extract_load_skill` to read the most common control field.
"""

import json
import logging
import re

logger = logging.getLogger(__name__)

# Match <meta>...</meta> blocks. DOTALL lets '.' cross newlines; IGNORECASE
# accepts e.g. <META> from models that drift on casing.
_META_TAG_RE = re.compile(r"<meta>(.*?)</meta>", re.DOTALL | re.IGNORECASE)

# Whitelist of recognized meta keys. Unknown keys are dropped (with a warning)
# so the schema stays tight and a hostile or hallucinated payload cannot
# smuggle arbitrary fields into the control plane.
_ALLOWED_META_KEYS: frozenset[str] = frozenset({"load_skill"})

# Cap on raw <meta> content size to prevent memory exhaustion from
# pathological payloads (e.g. a 10MB JSON blob).
_MAX_META_BYTES = 64 * 1024  # 64KB


def parse_meta_tag(message: str) -> tuple[str, dict | None]:
    """Strip all <meta> tags from ``message`` and return the parsed payload.

    Returns a tuple of ``(cleaned_message, parsed_data)`` where ``parsed_data``
    is the last valid JSON object found inside a <meta> tag, filtered to the
    allowed schema. Returns ``(message, None)`` if no tags are present.

    Multiple tags use last-wins semantics: a later valid tag overwrites an
    earlier one. Malformed tags are skipped silently (with a warning) but are
    still stripped from the visible message.
    """
    matches = list(_META_TAG_RE.finditer(message))
    if not matches:
        return message, None

    last_valid: dict | None = None

    for index, match in enumerate(matches, start=1):
        raw_content = match.group(1)
        tag_label = f"#{index}/{len(matches)}"

        # Size guard: prevent memory exhaustion from huge payloads
        if len(raw_content.encode("utf-8")) > _MAX_META_BYTES:
            logger.warning(
                f"<meta> tag {tag_label} exceeds {_MAX_META_BYTES} bytes, skipping"
            )
            continue

        try:
            data = json.loads(raw_content)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(
                f"<meta> tag {tag_label} JSON parse failed: {e}. Ignoring this tag."
            )
            continue

        if not isinstance(data, dict):
            logger.warning(
                f"<meta> tag {tag_label} JSON is not a dict (got {type(data).__name__}). "
                "Ignoring this tag."
            )
            continue

        unknown_keys = sorted(set(data.keys()) - _ALLOWED_META_KEYS)
        if unknown_keys:
            logger.warning(
                f"<meta> tag {tag_label} contains unknown keys {unknown_keys} "
                "— ignoring them."
            )
            filtered = {k: v for k, v in data.items() if k in _ALLOWED_META_KEYS}
        else:
            filtered = data

        last_valid = filtered
        logger.info(
            f"[MetaTag] Parsed <meta> tag: {last_valid} "
            f"(from {len(matches)} tag(s), last-wins)"
        )

    cleaned = _META_TAG_RE.sub("", message).rstrip()
    return cleaned, last_valid


def extract_load_skill(meta: dict | None) -> str | None:
    """Return the ``load_skill`` value from a parsed meta dict, or ``None``."""
    if meta is None:
        return None
    skill_name = meta.get("load_skill")
    if isinstance(skill_name, str) and skill_name.strip():
        return skill_name.strip()
    return None
