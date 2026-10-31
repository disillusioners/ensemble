"""Cycle-neutral id normalization / matching helpers for critical notes.

This module is deliberately free of DB/engine imports so it can be used by
both the repository layer and the tool layer without pulling in SQLModel.

Matching contract (READ THIS BEFORE CHANGING):
  * IDs are matched by REPRESENTATION only — never by similarity. There is
    NO fuzzy / edit-distance / semantic matching that writes or merges
    rows. Every write, pin, supersede and remove stays leader-explicit.
  * ``critical_note_id_matches`` compares byte-exact first, then
    normalized-equality, then a STRICT 32-hex-char fingerprint. It never
    auto-applies. The tool layer may use a separate, diagnostics-only
    single-edit near-match hint for error messages, but that hint NEVER
    selects a row.
  * STRICT fingerprint (review 2026-10-31, W2): both sides must reduce to
    exactly 32 hex chars after stripping ONLY whitespace + separators. A
    junk-prefixed id (e.g. ``zz-<32 hex>``) is NOT a representation
    variant — it matches nothing.
  * EMPTY guard (review 2026-10-31, W3): a value that normalizes to ""
    (e.g. whitespace-only) matches NOTHING, not even a stored "" PK.
    Legacy ""/blank stored ids are unaddressable by design.
  * LIMITATION, stated plainly (review 2026-10-31, W1): STRUCTURALLY
    malformed ids (wrong hex count, e.g. an 8-4-4-5-12 shape) are NOT
    auto-resolved — ``uuid.UUID`` rejects them and the fingerprint arm
    requires exactly 32 hex on BOTH sides. The not-found error names the
    stored form (``repr()``-escaped) for an exact-bytes retry.
  * Resolution is TRI-STATE (review 2026-10-31, W5): the repository
    resolver returns :class:`ResolvedCriticalNoteId`,
    :class:`AmbiguousCriticalNoteId` or :class:`MissingCriticalNoteId` —
    ambiguity never silently picks one of two representation twins.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

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

# The ONLY shape the fingerprint arm accepts on BOTH sides (W2): exactly
# 32 hex chars once whitespace + separators are stripped.
_HEX32_RE = re.compile(r"[0-9a-f]{32}")

# Separators the fingerprint arm may strip: whitespace runs + hyphens
# (canonical UUID separators). NOTHING else — any other surviving char
# (letters, punctuation, junk prefixes) disqualifies the fingerprint.
_FINGERPRINT_STRIP_RE = re.compile(r"[\s\-]+")


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


def critical_note_hex_fingerprint(value: str) -> str | None:
    """STRICT representation fingerprint (W2): 32 hex chars or ``None``.

    Strips ONLY whitespace + separators (hyphens, after the unicode dash
    variants are mapped by normalization) and requires the remainder to
    fullmatch ``[0-9a-f]{32}``. Any other surviving character — a junk
    prefix like ``zz``, a wrong hex count, a 33-char malformed shape —
    disqualifies the value: the arm returns ``None`` and can never match.

    Returns ``None`` for anything that is not a representation variant of
    a 32-hex id. Single canonical implementation: the tool layer imports
    THIS function (no local duplicate).
    """
    s = normalize_critical_note_id(value)
    if not s:
        return None
    s = _FINGERPRINT_STRIP_RE.sub("", s)
    if _HEX32_RE.fullmatch(s):
        return s
    return None


def critical_note_id_matches(stored: str, requested: str) -> bool:
    """Representation-only equality predicate for two critical-note ids.

    Order:
      1. exact byte match;
      2. normalized equality (``normalize_critical_note_id``) — SKIPPED
         when either side normalizes to "" (W3: a blank request never
         addresses a row, not even a stored blank PK);
      3. STRICT hex-fingerprint equality — BOTH sides must reduce to
         exactly 32 hex chars after stripping only whitespace +
         separators (W2: junk chars are never stripped, so a
         junk-prefixed request matches nothing).
    """
    if stored is None or requested is None:
        return False
    if str(stored) == str(requested):
        return True
    norm_stored = normalize_critical_note_id(stored)
    norm_requested = normalize_critical_note_id(requested)
    if norm_stored and norm_requested and norm_stored == norm_requested:
        return True
    fp_stored = critical_note_hex_fingerprint(stored)
    fp_requested = critical_note_hex_fingerprint(requested)
    if fp_stored is not None and fp_stored == fp_requested:
        return True
    return False


# ── Tri-state resolution result (W5, review 2026-10-31) ──────────────────────
#
# Tiny cycle-neutral value types: the repository resolver returns one of
# these instead of ``str | None`` so the tool layer can distinguish
# "not found" (may carry the diagnostics-only near-match hint) from
# "ambiguous" (≥2 rows share the requested representation — refuses with
# its own message, NEVER a hint, because a hint would invite picking one
# of the colliding rows).


@dataclass(frozen=True)
class ResolvedCriticalNoteId:
    """Exactly one stored row matches the requested representation."""

    stored_id: str


@dataclass(frozen=True)
class AmbiguousCriticalNoteId:
    """Two or more stored rows share the requested representation.

    Resolution refuses — nothing is selected, nothing changes.
    """

    match_count: int


@dataclass(frozen=True)
class MissingCriticalNoteId:
    """Zero stored rows match (incl. empty-after-normalize requests)."""


CriticalNoteIdResolution = (
    ResolvedCriticalNoteId | AmbiguousCriticalNoteId | MissingCriticalNoteId
)


def stored_id_or_none(resolution: CriticalNoteIdResolution) -> str | None:
    """Collapse a tri-state resolution to the legacy ``str | None`` shape.

    Convenience for repository seams that only need the stored id and
    treat ambiguous exactly as not-found (the resolver already logged the
    ambiguous case before returning).
    """
    if isinstance(resolution, ResolvedCriticalNoteId):
        return resolution.stored_id
    return None


def new_critical_note_id() -> str:
    """Single canonical producer for new critical-note ids."""
    return str(uuid.uuid4())
