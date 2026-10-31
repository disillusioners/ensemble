"""Cycle-neutral id normalization / matching helpers for critical notes.

This module is deliberately free of DB/engine imports so it can be used by
both the repository layer and the tool layer without pulling in SQLModel.

Matching contract (READ THIS BEFORE CHANGING):
  * IDs are matched by REPRESENTATION only — never by similarity. There is
    NO fuzzy / edit-distance / semantic matching that writes or merges
    rows. Every write, pin, supersede and remove stays leader-explicit.
  * ``critical_note_id_matches`` compares byte-exact first, then
    normalized-equality, then a 32-hex-char fingerprint. It never
    auto-applies. The tool layer may use a separate, diagnostics-only
    single-edit near-match hint for error messages, but that hint NEVER
    selects a row.
"""

from __future__ import annotations

import uuid

# Zero-width / invisible characters to strip from any id input.
_INVISIBLE_CHARS = (
    "\u200b",  # ZERO WIDTH SPACE
    "\u200c",  # ZERO WIDTH NON-JOINER
    "\u200d",  # ZERO WIDTH JOINER
    "\ufeff",  # ZERO WIDTH NO-BREAK SPACE / BOM
    "\u2060",  # WORD JOINER
    "\u180e",  # MONGOLIAN VOWEL SEPARATOR
)

# Unicode dash variants mapped to ASCII hyphen-minus.
_DASH_VARIANTS = {
    "\u2010": "-",  # HYPHEN
    "\u2011": "-",  # NON-BREAKING HYPHEN
    "\u2012": "-",  # FIGURE DASH
    "\u2013": "-",  # EN DASH
    "\u2014": "-",  # EM DASH
    "\u2212": "-",  # MINUS SIGN
}


def normalize_critical_note_id(value: str) -> str:
    """Non-raising normalization of a critical-note id string.

    Strips whitespace, removes zero-width/invisible characters, maps
    unicode dash variants to ASCII '-', lowercases, and — when the result
    parses as a ``uuid.UUID`` — returns the canonical ``str(uuid.UUID)``
    form. Otherwise returns the normalized string as-is (never raises,
    so legacy/malformed rows loaded by SQLModel stay readable).
    """
    if value is None:
        return ""
    s = str(value)
    for ch in _INVISIBLE_CHARS:
        s = s.replace(ch, "")
    for src, dst in _DASH_VARIANTS.items():
        s = s.replace(src, dst)
    s = s.strip().lower()
    try:
        return str(uuid.UUID(s))
    except (ValueError, AttributeError, TypeError):
        return s


def _hex_fingerprint(value: str) -> str:
    """Lowercase hex-only fingerprint (all non-hex chars stripped)."""
    return "".join(ch for ch in str(value).lower() if ch in "0123456789abcdef")


def critical_note_id_matches(stored: str, requested: str) -> bool:
    """Representation-only equality predicate for two critical-note ids.

    Order:
      1. exact byte match;
      2. normalized equality (``normalize_critical_note_id``);
      3. hex-fingerprint equality, ONLY when BOTH fingerprints are
         exactly 32 hex chars (guards against loose/prefix matching).
    """
    if stored is None or requested is None:
        return False
    if str(stored) == str(requested):
        return True
    if normalize_critical_note_id(stored) == normalize_critical_note_id(requested):
        return True
    fp_stored = _hex_fingerprint(stored)
    fp_requested = _hex_fingerprint(requested)
    if len(fp_stored) == 32 and fp_stored == fp_requested:
        return True
    return False


def new_critical_note_id() -> str:
    """Single canonical producer for new critical-note ids."""
    return str(uuid.uuid4())
