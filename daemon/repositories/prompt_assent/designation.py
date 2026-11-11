"""The designation map — the operator's ``channel_id → pinned approver`` registry.

The pure, dependency-free half of the prompt-assent Phase-1.5 extension
("designated channels, pinned-approver model"). The operator designates a
Discord channel and pins **exactly one** human to it; a turn in that channel
by that human is *private-equivalent* to a 1:1 DM (the Emperor's doctrine —
*"we consider channel is private/dm for now"*).

THIS MODULE OWNS THREE THINGS AND NOTHING ELSE
-----------------------------------------------
1. the env-name constant :data:`ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS`
   (the ONE literal in ``daemon/`` — every other module IMPORTS it);
2. the frozen :class:`DesignationMap` value type;
3. the strict, all-or-nothing :func:`parse_designations` parser and the
   :class:`DesignationParseResult` typed outcome.

No daemon imports, no I/O, **no environment read** (D-3 / phase1-plan §5): the
caller owns the environment read and passes the RAW STRING in. That keeps
the parser unit-testable without a manager and keeps one home for the read.

THE PARSE CONTRACT (D-4 — every rule has a reason)
--------------------------------------------------
* the value is a **JSON object** (keys = channel ids, values = approver ids);
  an array or a scalar is a shape error, never a partial map;
* keys AND values are digit-strings matching ``^[1-9][0-9]{0,31}$`` — digits
  only, **no leading zero**, not ``0`` (the adapter stringifies Discord ids,
  so digits-only is the *proven* shape; the tightened form kills the
  leading-zero never-match typo class);
* a non-string JSON value (an int, a bool) is **REJECTED, never coerced** —
  ids are ``str`` by construction and a ``1`` that became ``"1"`` would hide
  a typo;
* **duplicate keys are refused** (``object_pairs_hook`` — ``json.loads``
  would keep the last one silently, the textbook "silently drop");
* at most :data:`MAX_DESIGNATIONS` (64) entries;
* an EMPTY value (``""``, or whitespace only) is **unset**, ``{}`` is a valid
  EMPTY map — behaviourally identical, diagnostically distinct (the parse
  state names which).

**All-or-nothing**: one invalid entry ⇒ the WHOLE map is invalid. A partially
applied map would be a silent *partial authority* state the operator cannot
reason about ("is channel X enabled or not?"). Every error names the offending
**key** and the **rule** — never the operator's raw value, never a value at
all (S-6: the logs and the refusals are content-free).

THE MAP-PARSE LOG LINE (mandatory, content-free)
------------------------------------------------
:func:`describe_parse` renders the ONE line the gates log on every resolution:
the parse state (``unset`` / ``empty`` / ``invalid`` / ``N entries``) plus a
content-free fingerprint — the first :data:`MAP_DIGEST_HEX_LEN` hex of the
canonical JSON's sha256. Both sides of the lane (this module's
:func:`designation_digest`, the tool's mint INFO line and the audit payload)
use the SAME 12-hex length — one length, everywhere (D-15). This line is the
only discriminator between *"the map says no"* and *"the map is broken"*.

The parser NEVER logs (it has no I/O): it returns the typed outcome, and the
caller emits the line. Invalid maps carry no fingerprint (there is no
canonical JSON to hash — and hashing the raw operator string would leak a
guessable value for short inputs).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

#: The ONE env var holding the designation map. This literal appears
#: EXACTLY ONCE in ``daemon/`` (here); every consumer imports it. Tests are
#: exempt (they use the imported constant or a programmatic construction);
#: ``docs/`` and the runbook are exempt — the operator must be told the name
#: (T1.6 / risks.md P-1).
ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS = (
    "ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS"
)

#: The bound on the operator's error surface and on the parse (D-4 rule 8).
MAX_DESIGNATIONS: int = 64

#: The content-free fingerprint length — **one length, everywhere** (D-15):
#: the parse log line, the mint INFO line's ``map_digest`` and the decision
#: audit payload's ``map_digest`` all carry exactly this many hex chars.
MAP_DIGEST_HEX_LEN: int = 12

#: The id shape (D-4 rule 2): digits only, no leading zero, not ``0``,
#: at most 32 chars (a Discord snowflake is 17-20 digits).
_DESIGNATION_ID_RE = re.compile(r"^[1-9][0-9]{0,31}$")

#: The rendered-key bound — an error message names the key, bounded so a
#: pathological key cannot flood a log line (and never the raw value).
_KEY_RENDER_MAX: int = 48


class _Pairs(list):
    """A JSON object decoded as ordered ``(key, value)`` pairs.

    The ONLY way to see a duplicate key: ``json.loads`` collapses them
    before a post-hoc scan could look (D-1.6).
    """


def _pairs_hook(pairs: list[tuple[str, Any]]) -> _Pairs:
    return _Pairs(pairs)


def _render_key(key: Any) -> str:
    """A bounded, escaped rendering of an offending KEY (never a value)."""
    text = key if isinstance(key, str) else str(key)
    text = "".join(
        ch if ch.isprintable() else f"\\x{ord(ch):02x}" for ch in text
    )
    if len(text) > _KEY_RENDER_MAX:
        text = text[:_KEY_RENDER_MAX] + "…"
    return text


@dataclass(frozen=True)
class DesignationMap:
    """The operator's designation map — immutable once parsed (D-1.1).

    ``entries`` maps a Discord channel id (``str``) to the pinned approver's
    Discord user id (``str``). It is wrapped in a
    :class:`types.MappingProxyType` at construction so a caller cannot mutate
    a map mid-decision (a TOCTOU edit between the mint check and the ledger
    write is exactly what immutability rules out).

    ``unset`` records the diagnostic distinction the value alone cannot
    carry: an ABSENT env var (or an empty string) yields a valid map with
    ``unset=True`` and zero entries, while an explicit ``{}`` yields a valid
    map with ``unset=False`` and zero entries. Behaviourally identical (both
    mean *no designated channels*), diagnostically distinct (the parse log
    line says which).
    """

    entries: Mapping[str, str] = field(default_factory=dict)
    unset: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "entries", MappingProxyType(dict(self.entries)))

    def __len__(self) -> int:
        return len(self.entries)

    def __contains__(self, channel_id: Any) -> bool:
        return isinstance(channel_id, str) and channel_id in self.entries

    def pinned_approver(self, channel_id: Any) -> str | None:
        """The approver pinned to ``channel_id``, or ``None``.

        Exact channel-id match only (D-9 — no thread inheritance) and no
        coercion: the adapter stringifies ids, so a non-``str`` argument is
        a shape we do not trust (fail-closed ``None``).
        """
        if not isinstance(channel_id, str):
            return None
        return self.entries.get(channel_id)

    def canonical_json(self) -> str:
        """The canonical JSON form — sorted keys, compact separators.

        Deterministic so the digest is a stable fingerprint of the map's
        CONTENT (never of its textual spelling in ``.env``).
        """
        return json.dumps(
            {key: self.entries[key] for key in sorted(self.entries)},
            separators=(",", ":"),
            ensure_ascii=True,
        )


@dataclass(frozen=True)
class DesignationParseResult:
    """The typed parse outcome — a valid map OR a typed-invalid carrier.

    ``errors`` names EVERY offending entry (key + rule), never a value; a
    single error makes the whole result invalid (all-or-nothing).
    """

    valid: bool
    map: DesignationMap | None
    errors: tuple[str, ...] = ()

    @property
    def state(self) -> str:
        """``unset`` / ``empty`` / ``invalid`` / ``N entries``."""
        if not self.valid or self.map is None:
            return "invalid"
        if self.map.unset:
            return "unset"
        count = len(self.map)
        return "empty" if count == 0 else f"{count} entries"


def _invalid(*errors: str) -> DesignationParseResult:
    return DesignationParseResult(valid=False, map=None, errors=tuple(errors))


def designation_digest(designations: DesignationMap) -> str:
    """The content-free fingerprint — ``sha256(canonical JSON)[:12]``."""
    return hashlib.sha256(
        designations.canonical_json().encode("utf-8")
    ).hexdigest()[:MAP_DIGEST_HEX_LEN]


def parse_designations(raw: str | None) -> DesignationParseResult:
    """Parse the operator's raw env value into a typed outcome (D-4).

    ``None`` or an empty/whitespace-only string ⇒ a valid UNSET map. ``"{}"``
    ⇒ a valid EMPTY map. Anything else must be a JSON object whose keys and
    values match the id shape — otherwise the result is invalid and names
    every offending entry.

    Never raises: a malformed value is a REFUSAL (the designated-channel path
    is unavailable), never an exception at a call site (D-4: *"the call sites
    need a refusal, not an exception"*).
    """
    if raw is None:
        return DesignationParseResult(
            valid=True, map=DesignationMap(unset=True), errors=()
        )
    text = raw.strip() if isinstance(raw, str) else ""
    if not text:
        return DesignationParseResult(
            valid=True, map=DesignationMap(unset=True), errors=()
        )

    try:
        decoded = json.loads(text, object_pairs_hook=_pairs_hook)
    except (ValueError, RecursionError):
        return _invalid("<root>: malformed JSON (rule: format)")

    if not isinstance(decoded, _Pairs):
        return _invalid("<root>: the designation map must be a JSON object (rule: format)")

    errors: list[str] = []
    if len(decoded) > MAX_DESIGNATIONS:
        errors.append(
            f"<root>: at most {MAX_DESIGNATIONS} designations (rule: bound)"
        )

    entries: dict[str, str] = {}
    seen: set[str] = set()
    for key, value in decoded:
        rendered = _render_key(key)
        if isinstance(key, str):
            if key in seen:
                errors.append(f"{rendered}: duplicate key (rule: unique-keys)")
                continue
            seen.add(key)
        if not (isinstance(key, str) and _DESIGNATION_ID_RE.match(key)):
            errors.append(
                f"{rendered}: the channel id must be a digit string "
                "(rule: id-shape)"
            )
            continue
        if not isinstance(value, str):
            errors.append(
                f"{rendered}: the approver id must be a JSON string, not "
                f"{type(value).__name__} (rule: id-shape)"
            )
            continue
        if not _DESIGNATION_ID_RE.match(value):
            errors.append(
                f"{rendered}: the approver id must be a digit string "
                "(rule: id-shape)"
            )
            continue
        entries[key] = value

    if errors:
        return _invalid(*errors)

    return DesignationParseResult(
        valid=True, map=DesignationMap(entries=entries), errors=()
    )


def describe_parse(result: DesignationParseResult) -> str:
    """The mandatory MAP-PARSE LOG LINE — content-free by construction.

    Carries the parse state, the entry count and the 12-hex fingerprint of
    the canonical JSON (``-`` when there is no valid map to fingerprint).
    NEVER a channel id, NEVER a user id, NEVER the operator's raw value.
    """
    entries = len(result.map) if result.map is not None else 0
    digest = designation_digest(result.map) if result.map is not None else "-"
    return (
        f"designation_map state={result.state} entries={entries} "
        f"map_digest={digest} errors={len(result.errors)}"
    )


__all__ = [
    "ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS",
    "MAP_DIGEST_HEX_LEN",
    "MAX_DESIGNATIONS",
    "DesignationMap",
    "DesignationParseResult",
    "designation_digest",
    "describe_parse",
    "parse_designations",
]
