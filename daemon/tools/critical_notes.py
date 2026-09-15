"""Critical Notes tools for project-scoped experience management.

Tools for adding, listing, and removing critical notes entries
that capture important lessons learned during project work.

Design notes (2026-09-10):
- No silent fuzzy-upsert into unrelated rows. Strict collision detection:
  two summaries collide only if their NORMALIZED forms are equal
  (lowercased + whitespace-collapsed). Token-overlap matching is forbidden
  because it falsely merged distinct notes sharing generic vocabulary
  ("fixed", "restart", "merge") in prod (2026-09-10 hijack incident).
- On collision, the tool REJECTS with an error naming the colliding
  entry's id and summary. Caller decides whether to remove+re-add or
  pass entry_id explicitly to update.
- entry_id (optional) supports explicit in-place update. No fuzzy.
- Cap raised to 50 (live store was already at 31). Past the cap, the
  tool REJECTS with an error naming eviction candidates — no silent
  data loss.

Lifecycle notes (2026-09-15, critical-notes-retrieval Phase 1):
- New leader-only verbs ``project_cn_pin`` and ``project_cn_supersede``.
  Pin cap is REJECT-don't-evict: the 9th pin names demotion candidates
  and refuses; nothing is ever auto-evicted.
- Collision detection excludes SUPERSEDED rows (R20): a superseded row
  is invisible to duplicate-rejects, so a verbatim re-add after a
  supersede inserts a fresh row (an explicit supersede followed by a
  re-add is an implicit un-supersede, visible in the list). Strict
  normalized-equality semantics are otherwise byte-identical.
- ``reference`` is bounded at 500 chars with dual enforcement and
  reject-first precedence: the write-side tool REJECT is authoritative;
  injection-side truncation exists only for rows that predate or bypass
  the bound — never as an alternative to rejection.
- NO fuzzy matching may ever write/merge/supersede. Every write, pin,
  supersede, and remove is leader-explicit through these tools.
"""

from __future__ import annotations

from datetime import datetime, timezone

from langchain_core.tools import tool

from ..repositories.project.models import (
    CriticalNotes,
    CriticalNotesCategory,
    CriticalNotesPriority,
    CriticalNoteModel,
)
from ..repositories.project.repository import SQLModelProjectRepository
from ._tool_registry import register_tool_category

CATEGORY_NAME = "critical_notes"
CATEGORY_DOC = "Manage critical notes entries for projects — lessons learned, important observations, and key insights."

# Raised from 30 to 50 (live store was already at 31 on 2026-09-10;
# silent eviction is removed, so we need headroom for genuine growth).
_MAX_ENTRIES = 50
_MAX_SUMMARY_LEN = 200
# Reference bound (architecture-recommendation §4.4, reviewer #11).
# Write-side REJECT at this tool layer is the AUTHORITATIVE enforcement;
# the injection-side truncation in ``context_messages`` is a defensive
# backstop for legacy/pre-bound rows only.
_MAX_REFERENCE_LEN = 500
_MAX_EVICTION_CANDIDATES_NAMED = 3
# Core-tier cap (D2): max simultaneously-pinned ACTIVE rows. Tunable via
# ``config.yaml → critical_notes.core_cap`` (installed at boot by
# ``load_config``; see ``install_critical_notes_config`` below). NOT
# env-tunable (D4: no new ``ENSEMBLE_*`` env vars for this feature).
_DEFAULT_CORE_CAP = 8
# Staleness horizon (§4.5): ``last_reviewed_at`` older than this renders
# a STALE mark + archive-candidate proposal in list surfaces. Tunable via
# ``critical_notes.stale_days`` (installed at boot; no env var, D4).
_DEFAULT_STALE_DAYS = 90
_PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2}

# ── Boot-installed config knobs (D4: config-layer tuning, no env vars) ──
# ``load_config`` resolves the ``critical_notes:`` yaml block and installs
# the effective values here via :func:`install_critical_notes_config`.
# Defaults below keep the tools fully functional when no install ran
# (unit tests, direct construction). Values are read at CALL time so a
# boot install is picked up without recreating the tool closures.
_CORE_CAP = _DEFAULT_CORE_CAP
_REFERENCE_MAX = _MAX_REFERENCE_LEN
_STALE_DAYS = _DEFAULT_STALE_DAYS


def install_critical_notes_config(
    *,
    core_cap: int | None = None,
    reference_max: int | None = None,
    stale_days: int | None = None,
) -> None:
    """Install boot-resolved ``critical_notes`` knobs into module state.

    Called once from ``load_config`` (daemon/config.py). ``None`` leaves
    the current value untouched, so a partial yaml block falls back to
    the documented defaults above rather than resetting to them.
    """
    global _CORE_CAP, _REFERENCE_MAX, _STALE_DAYS
    if core_cap is not None:
        _CORE_CAP = int(core_cap)
    if reference_max is not None:
        _REFERENCE_MAX = int(reference_max)
    if stale_days is not None:
        _STALE_DAYS = int(stale_days)


def reset_critical_notes_config() -> None:
    """Restore documented defaults (test isolation helper)."""
    global _CORE_CAP, _REFERENCE_MAX, _STALE_DAYS
    _CORE_CAP = _DEFAULT_CORE_CAP
    _REFERENCE_MAX = _MAX_REFERENCE_LEN
    _STALE_DAYS = _DEFAULT_STALE_DAYS


def _is_valid_category(category: str) -> bool:
    """Check if category is a valid CriticalNotesCategory value."""
    return category in CriticalNotesCategory._value2member_map_


def _is_valid_priority(priority: str) -> bool:
    """Check if priority is a valid CriticalNotesPriority value."""
    return priority in CriticalNotesPriority._value2member_map_


def _normalize_summary(summary: str) -> str:
    """Normalize a summary for strict equality matching.

    Lowercases + collapses all whitespace runs to a single space + strips.
    This is the ONLY form compared when checking duplicates — distinct
    summaries must never collide.
    """
    return " ".join(summary.lower().split())


def _load_entries(
    repo, project_id: str, *, predicate=None
) -> list[CriticalNotes]:
    """Load and coerce all critical-note rows for a project.

    Replaces the repeated ``repo.list_critical_notes(...) + isinstance-
    coerce comprehension`` pattern: the repo returns SQLModel rows, but
    the tool layer operates on the BaseModel ``CriticalNotes`` form so
    predicates and helpers see the canonical in-memory shape. When
    ``predicate`` is provided, only entries for which it returns True
    are kept; the predicate runs AFTER the coerce (BaseModel form), so
    it can rely on attribute access without re-checking the type.
    """
    notes_list = repo.list_critical_notes(project_id)
    coerced = [
        CriticalNotes(**note.to_dict()) if isinstance(note, CriticalNoteModel) else note
        for note in notes_list
    ]
    if predicate is None:
        return coerced
    return [e for e in coerced if predicate(e)]


def _find_near_duplicate_entry(
    entries: list[CriticalNotes], summary: str
) -> CriticalNotes | None:
    """Return a colliding entry if its normalized summary equals the new one.

    Replaces the old token-overlap _find_similar_entry: that matcher
    silently merged unrelated rows that shared generic vocabulary
    ("fixed", "restart", "merge", "pending"). Normalized equality is
    strict by design — a summary differing in any word, punctuation,
    or number is NOT a duplicate.

    R20 collision scope (2026-09-15): SUPERSEDED rows are excluded —
    the scan covers ACTIVE rows only (``superseded_by_id IS NULL``).
    Naming an invisible row in a reject is a defect: a leader re-adding
    knowledge that exists only as a superseded row would be told
    "duplicate" by a row it cannot see in normal flows. Consequence
    (documented): a verbatim re-add after a supersede inserts fresh.
    Strict normalized-equality semantics are otherwise byte-identical;
    NO fuzzy matching may ever write/merge/supersede.
    """
    target = _normalize_summary(summary)
    for entry in entries:
        if entry.superseded_by_id is not None:
            continue  # R20: superseded rows are invisible to the scan
        if _normalize_summary(entry.summary) == target:
            return entry
    return None


def _is_active_critical_note(entry: Any) -> bool:
    """R21 entry-gate predicate — an entry counts as ACTIVE iff its
    ``superseded_by_id`` is ``None``.

    Shared helper applied at the 3 external surfaces that pass
    ``list_critical_notes`` through unfiltered (R21, LOCKED-L 2026-09-15).
    Defensive against the empty-string class (a stray empty ``""``
    pointer must NOT bypass the filter — ``is not None`` is the
    authoritative comparison). Accepts either a SQLModel row, a
    ``CriticalNotes`` BaseModel, or a plain dict (the surfaces pass
    different shapes, so the predicate duck-types).
    """
    if isinstance(entry, dict):
        sid = entry.get("superseded_by_id")
    else:
        sid = getattr(entry, "superseded_by_id", None)
    return sid is None


def _eviction_candidates(entries: list[CriticalNotes]) -> list[CriticalNotes]:
    """Return the entries that WOULD be evicted, oldest-first among lowest priority.

    Used to name candidates in the cap-rejection error so the caller can
    make an informed decision. Pure function — does NOT mutate.

    Sort key: negative priority_value first (lowest priority surfaces),
    then created_at ASC (oldest among ties). Note this differs from the
    pre-existing buggy ascending-priority sort — the new contract is
    "evict the LEAST important first", which is the sensible semantic.
    """
    return sorted(
        entries,
        key=lambda e: (-_PRIORITY_ORDER.get(e.priority, 2), e.created_at),
    )


def _parse_iso_ts(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp defensively; None on missing/invalid.

    Naive timestamps (no tz offset) are coerced to UTC — callers pass a
    tz-aware ``now`` (``datetime.now(timezone.utc)``) so the downstream
    ``now - ts`` subtraction cannot raise ``TypeError`` on a naive
    ``ts``. This matches the storage convention: timestamps written by
    ``datetime.now(timezone.utc).isoformat()`` are tz-aware, but legacy
    rows / 3rd-party adapters may write naive ISO strings.
    """
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _days_since(value: str | None, now: datetime) -> int | None:
    """Whole days since an ISO timestamp (floored at 0); None if unparseable."""
    ts = _parse_iso_ts(value)
    if ts is None:
        return None
    # Both sides tz-aware now: _parse_iso_ts coerces naive→UTC, callers
    # pass ``datetime.now(timezone.utc)``; mixing tz-aware and naive
    # would raise TypeError (the bug this guards against).
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    delta = now - ts
    return max(0, int(delta.total_seconds() // 86400))


def _is_stale(entry: CriticalNotes, now: datetime) -> tuple[bool, int | None]:
    """Return (stale, days_since_review) for an entry (§4.5 STALE derivation).

    Staleness is orthogonal to supersession: the review clock is
    ``last_reviewed_at``, falling back to ``created_at`` for rows that
    predate the lifecycle backfill (NULL last_reviewed_at). A row with
    NEITHER timestamp parseable is never marked stale (fail-open).
    """
    reviewed = entry.last_reviewed_at or entry.created_at
    days = _days_since(reviewed, now)
    if days is None:
        return (False, None)
    return (days > _STALE_DAYS, days)


def _superseded_display_summary(summary: str, superseded_by_id: str | None) -> str:
    """Render the strike-through form for a superseded row (R19, list-only)."""
    if superseded_by_id is None:
        return summary
    return f"~~{summary}~~ ✅ superseded by {superseded_by_id}"


def _attach_pin_suggestion(result: dict) -> None:
    """Attach a pin SUGGESTION to a successful write response (D2).

    A ``priority=critical`` write surfaces an in-band suggestion to pin
    the row into the always-injected core tier — the decision stays the
    leader's explicit ``project_cn_pin`` call. NEVER auto-pins.
    """
    if result.get("priority") == "critical" and not result.get("pinned"):
        result["pin_suggestion"] = (
            "priority=critical — consider pinning this note into the "
            f"core tier via project_cn_pin(entry_id='{result.get('id')}', "
            "pinned=true). Core is capped (reject-don't-evict); pin only "
            "always-relevant contracts/traps, not incident narration."
        )


def create_critical_notes_tools(
    repo: SQLModelProjectRepository, current_instance_id: str = "", agent_id: str = ""
) -> list:
    """Create critical notes management tools bound to a project repository."""

    @register_tool_category(CATEGORY_NAME)
    @tool
    def project_cn_add(
        project_id: str,
        category: str,
        priority: str,
        summary: str,
        reference: str | None = None,
        entry_id: str | None = None,
        detail_ref: str | None = None,
    ) -> dict:
        """Add or update a critical notes entry for a project. Use tool_help() for details.

        Behavior:
        - If entry_id is provided, that specific row is updated exactly
          (summary/priority/reference/detail_ref/category/source_agent).
          No fuzzy matching. Category is forwarded on update, so a
          recategorize is visible on the returned dict — never silent.
        - Otherwise, if a near-duplicate summary already exists among the
          project's ACTIVE notes, the call REJECTS with an error naming
          the colliding entry's id and summary — caller decides next
          step. Collision detection is CROSS-CATEGORY by design
          (normalized-summary equality ignores category), so passing
          entry_id on the conflicting entry's id is the supported
          recategorize path. Superseded rows are invisible to the
          collision scan (R20).
        - Otherwise, a new entry is appended. If the project is at the
          cap (_MAX_ENTRIES=50), the call REJECTS with an error naming
          eviction candidates — no silent data loss.
        - ``reference`` is bounded at 500 chars: the call REJECTS on a
          longer reference (reject-first — put overflow detail in
          detail_ref instead). Truncation never substitutes for this
          rejection.
        - A ``priority=critical`` write returns a ``pin_suggestion``
          field — curation is still the leader's explicit call (pin via
          project_cn_pin); nothing is ever auto-pinned (D2).
        """
        # Step 1: Validate inputs
        if not _is_valid_category(category):
            return {"error": f"Invalid category '{category}'. Valid: {[c.value for c in CriticalNotesCategory]}"}
        if not _is_valid_priority(priority):
            return {"error": f"Invalid priority '{priority}'. Valid: {[p.value for p in CriticalNotesPriority]}"}
        if len(summary) > _MAX_SUMMARY_LEN:
            return {"error": f"Summary must be <= {_MAX_SUMMARY_LEN} chars, got {len(summary)}"}
        if not summary.strip():
            return {"error": "Summary cannot be empty"}
        if entry_id is not None and not entry_id.strip():
            return {"error": "entry_id must be non-empty when provided"}
        # Reference bound (§4.4 #11) — WRITE SIDE IS AUTHORITATIVE.
        # Reject-first: injection-side truncation exists ONLY for rows
        # that predate or bypass this bound (legacy data, non-tool
        # paths); it is never an alternative to this rejection. Mirror
        # of the _MAX_SUMMARY_LEN idiom above.
        if reference is not None and len(reference) > _REFERENCE_MAX:
            return {
                "error": (
                    f"Reference must be <= {_REFERENCE_MAX} chars, got "
                    f"{len(reference)}. Move the overflow detail into "
                    f"detail_ref (reachable via project_cn_list reads; "
                    f"never injected into context)."
                )
            }

        # Step 2: Check project exists
        project = repo.get(project_id)
        if not project:
            return {"error": f"Project '{project_id}' not found"}

        # Step 3: Load current entries (coerced to BaseModel form).
        entries = _load_entries(repo, project_id)

        # Step 4: EXPLICIT UPDATE PATH (entry_id provided)
        if entry_id is not None:
            target = next((e for e in entries if e.id == entry_id), None)
            if target is None:
                return {"error": f"Entry '{entry_id}' not found in project '{project_id}'"}

            updated = repo.update_critical_note(
                project_id,
                entry_id,
                priority=priority,
                summary=summary,
                category=category,
                reference=reference,
                detail_ref=detail_ref,
                source_agent=agent_id,
            )
            if updated is None:
                # Repo could not find/apply the update — surface this as an
                # error so the caller is not misled by a SUCCESS-shaped echo
                # with fabricated timestamps. Matches the {"error": ...}
                # return convention used elsewhere in this tool.
                return {"error": f"Entry '{entry_id}' no longer exists; update not applied"}
            result = updated.to_dict()
            _attach_pin_suggestion(result)
            return result

        # Step 5: STRICT COLLISION CHECK (new-add path) — ACTIVE rows
        # only per R20; superseded rows are invisible here.
        duplicate = _find_near_duplicate_entry(entries, summary)
        if duplicate is not None:
            return {
                "error": (
                    f"Near-duplicate critical note already exists "
                    f"(id={duplicate.id}): {duplicate.summary}. "
                    f"To update, pass entry_id='{duplicate.id}'."
                )
            }

        # Step 6: CAP CHECK (new-add path)
        if len(entries) >= _MAX_ENTRIES:
            candidates = _eviction_candidates(entries)[:_MAX_EVICTION_CANDIDATES_NAMED]
            candidate_lines = [
                f"  - id={c.id} priority={c.priority} created_at={c.created_at} summary={c.summary!r}"
                for c in candidates
            ]
            return {
                "error": (
                    f"Project '{project_id}' is at the cap of {_MAX_ENTRIES} critical notes. "
                    f"Remove one of the following eviction candidates and re-add, or use "
                    f"project_cn_remove + project_cn_add.\n"
                    + "\n".join(candidate_lines)
                )
            }

        # Step 7: ADD NEW ENTRY
        added = repo.add_critical_note(
            project_id,
            source_agent=agent_id,
            category=category,
            priority=priority,
            summary=summary,
            reference=reference,
            detail_ref=detail_ref,
        )
        result = CriticalNotes(**added.to_dict()).to_dict()
        _attach_pin_suggestion(result)
        return result

    project_cn_add._full_doc_ = """Add or update a critical notes entry for a project.

When adding an entry:
- If a near-duplicate summary (normalized equality, CROSS-CATEGORY by
  design — category is ignored for collision detection) already exists
  among the project's ACTIVE notes, the call REJECTS with an error
  naming the colliding entry's id and summary. The caller decides
  whether to remove it via project_cn_remove or update it via entry_id.
  Superseded rows are invisible to this scan — re-adding knowledge that
  only exists as a superseded row inserts fresh.
- If the list is full (50 entries), the call REJECTS with an error naming
  eviction candidates. No silent eviction.
- Pass entry_id to update a specific entry in place (exact match, no fuzzy).
  An entry_id update can change any updatable field including category —
  the returned dict reflects the new category, never silently retaining
  the old one.
- Updates cannot clear a reference or detail_ref: passing None (or
  omitting) leaves the existing value unchanged; the repository guard
  applies only non-None values.
- ``reference`` is bounded at 500 chars (reject on longer). Overflow
  detail belongs in ``detail_ref`` — never injected into context,
  readable via project_cn_list.
- Any write refreshes last_reviewed_at (re-affirmation resets the
  staleness clock).
- A priority=critical write carries a ``pin_suggestion`` field; pinning
  stays an explicit project_cn_pin call (never auto-pinned).

Args:
    project_id: The project to add the note to
    category: One of: convention, pattern, risk, decision, constraint
    priority: One of: critical, high, medium
    summary: Brief description (max 200 chars)
    reference: Optional reference URL or path (max 500 chars)
    entry_id: Optional. When provided, that entry is updated exactly
       (no fuzzy match). If omitted, a near-duplicate check rejects the
       call instead of silently merging.
    detail_ref: Optional unbounded detail text (list-read only; never
       injected into context blocks).

Returns:
    The created or updated entry as a dict, or an error dict on
    validation failure, collision, or cap hit."""

    @register_tool_category(CATEGORY_NAME)
    @tool
    def project_cn_list(project_id: str) -> dict:
        """List all critical notes entries for a project. Use tool_help() for details."""
        project = repo.get(project_id)
        if not project:
            return {"error": f"Project '{project_id}' not found"}

        entries = _load_entries(repo, project_id)
        now = datetime.now(timezone.utc)

        # R19: list surfaces OWN the maintenance marks — strike-through
        # for superseded rows and the staleness marker. Order stays
        # created_at DESC (repository contract, unchanged).
        listed: list[dict] = []
        stale_candidates: list[tuple[int, str, dict]] = []
        for e in entries:
            d = e.to_dict()
            d["superseded"] = e.superseded_by_id is not None
            stale, days = _is_stale(e, now)
            d["stale"] = stale and not d["superseded"]
            d["days_since_review"] = days
            display = _superseded_display_summary(e.summary, e.superseded_by_id)
            if d["stale"]:
                display = f"{display} ⚠️ last reviewed {days} days ago"
            d["display_summary"] = display
            listed.append(d)
            # Archive-candidate set (§4.5): stale ACTIVE rows, sorted
            # priority → oldest. PROPOSAL-ONLY — never auto-applied; the
            # leader confirms via supersede/remove.
            if d["stale"]:
                stale_candidates.append(
                    (_PRIORITY_ORDER.get(e.priority, 2), e.created_at, d)
                )
        stale_candidates.sort(key=lambda t: (t[0], t[1]))

        housekeeping = {
            "proposal_only": True,
            "note": (
                "Archive candidates are a PROPOSAL — confirm via "
                "project_cn_supersede / project_cn_remove. Never "
                "auto-applied. Re-affirm a still-valid note by updating "
                "it (any write refreshes last_reviewed_at)."
            ),
            "stale_days": _STALE_DAYS,
            "archive_candidates": [
                {
                    "id": d["id"],
                    "priority": d["priority"],
                    "summary": d["summary"],
                    "created_at": d["created_at"],
                    "last_reviewed_at": d["last_reviewed_at"],
                    "days_since_review": d["days_since_review"],
                    "pinned": d["pinned"],
                }
                for _, _, d in stale_candidates
            ],
        }
        return {
            "project_id": project_id,
            "count": len(listed),
            "entries": listed,
            "housekeeping": housekeeping,
        }

    project_cn_list._full_doc_ = """List all critical notes entries for a project.

Entries are returned in created_at DESC order (unchanged contract).
Each entry carries the lifecycle fields (pinned, pinned_at, pinned_by,
superseded_by_id, last_reviewed_at, detail_ref) plus derived maintenance
marks:

- ``superseded``: true when another row supersedes this one. Its
  ``display_summary`` renders struck-through with the successor's id.
  Superseded rows never load into the injected context block.
- ``stale`` + ``days_since_review``: the note has not been re-affirmed
  within the staleness horizon. ``display_summary`` carries the
  "⚠️ last reviewed N days ago" mark.
- ``detail_ref`` is returned here (and in the router detail payloads)
  but is NEVER injected into context blocks.

The response also carries a ``housekeeping`` block: the archive-
candidate set (stale ACTIVE rows, priority → oldest), PROPOSAL-ONLY.
Confirm a candidate via project_cn_supersede or project_cn_remove;
re-affirm a still-valid note by updating it (any write refreshes
last_reviewed_at).

Args:
    project_id: The project to list notes for

Returns:
    Dict with project_id, count, entries list (with maintenance marks),
    and the proposal-only housekeeping block."""

    @register_tool_category(CATEGORY_NAME)
    @tool
    def project_cn_remove(project_id: str, entry_id: str, cascade: bool = False) -> dict:
        """Remove a specific critical notes entry by ID. Use tool_help() for details."""
        project = repo.get(project_id)
        if not project:
            return {"error": f"Project '{project_id}' not found"}

        # Get the entry first to return its details
        removed_entry = repo.get_critical_note(project_id, entry_id)
        if removed_entry is None:
            return {"error": f"Entry '{entry_id}' not found"}

        # ── Supersede-pointer guard (§4.4; N6 semantics — EXACT contract) ──
        # If OTHER rows point at THIS row via superseded_by_id, a plain
        # remove would orphan their pointers. EXACT semantics of
        # cascade=True: the target row is removed AND every pointer is
        # NULLIFIED, which returns the pointing rows to ACTIVE — they
        # re-enter the injection pool immediately. The response names
        # each re-activated row (``reactivated`` list) so an
        # un-supersede is never silent. This is the deliberate,
        # documented choice:
        #   * Default (cascade=False) REFUSES when any row points here —
        #     reject-don't-evict conservatism (mirrors R20 / the pin cap).
        #     Resolve the lineage first: supersede the pointing rows onto
        #     a current note, or pass cascade=True deliberately.
        #   * Chain-DELETE (removing the pointing rows too) was REJECTED:
        #     it silently destroys rows the leader did not name — the
        #     exact eviction shape this tool family forbids.
        #   * Leaving dangling pointers was REJECTED: the pointing rows
        #     would stay hidden from injection forever (silent data
        #     loss), pointing at a ghost id.
        pointing = _load_entries(
            repo,
            project_id,
            predicate=lambda e: e.superseded_by_id == entry_id,
        )
        if pointing and not cascade:
            pointer_lines = "\n".join(
                f"  - id={p.id} summary={p.summary!r}" for p in pointing[:_MAX_EVICTION_CANDIDATES_NAMED]
            )
            return {
                "error": (
                    f"Entry '{entry_id}' is referenced as a successor by "
                    f"{len(pointing)} superseded-by pointer(s). Re-run with "
                    f"cascade=true to remove it AND nullify those pointers "
                    f"(the pointing rows return to ACTIVE and are named in "
                    f"the response), or resolve their lineage first via "
                    f"project_cn_supersede.\n"
                    + pointer_lines
                )
            }

        summary = removed_entry.summary

        # Cascade lane: nullify pointers BEFORE the delete (the rows must
        # survive as ACTIVE; only their pointers die with the target).
        reactivated: list[str] = []
        if pointing and cascade:
            reactivated = repo.clear_superseded_by_pointers(project_id, entry_id)

        # Remove via repository
        removed = repo.remove_critical_note(project_id, entry_id)

        return {
            "removed": removed,
            "entry_id": entry_id,
            "summary": summary,
            "cascade": bool(pointing and cascade),
            "reactivated": reactivated,
        }

    project_cn_remove._full_doc_ = """Remove a specific critical notes entry by ID.

Removal is REFUSED by default when another row's superseded_by_id
targets this entry — the pointing rows would be orphaned. EXACT
cascade=true semantics: the target row is removed AND each pointing
row's superseded_by_id pointer is nullified, returning those rows to
ACTIVE (they re-enter the injected context). The response lists every
re-activated row id so an un-supersede is never silent.

Args:
    project_id: The project to remove the entry from
    entry_id: The ID of the entry to remove
    cascade: Default false. When true and other rows point at this
        entry as their successor, remove this entry and nullify their
        pointers (they return to ACTIVE and are named in the response).

Returns:
    Confirmation dict with removed entry details, plus ``cascade`` and
    ``reactivated`` (ids un-superseded by this removal)."""

    @register_tool_category(CATEGORY_NAME)
    @tool
    def project_cn_pin(project_id: str, entry_id: str, pinned: bool) -> dict:
        """Pin or unpin a critical note into the always-injected core tier. Use tool_help() for details."""
        project = repo.get(project_id)
        if not project:
            return {"error": f"Project '{project_id}' not found"}

        target = repo.get_critical_note(project_id, entry_id)
        if target is None:
            return {"error": f"Entry '{entry_id}' not found"}

        if pinned and target.superseded_by_id is not None:
            return {
                "error": (
                    f"Entry '{entry_id}' is SUPERSEDED (by "
                    f"{target.superseded_by_id}) — a retired note cannot "
                    f"join the core tier. Pin its successor instead."
                )
            }

        # Core-cap enforcement (D2): count PINNED + ACTIVE rows. Reject-
        # don't-evict — the 9th pin REFUSES and names demotion candidates
        # (which pinned notes the leader might unpin). Nothing is ever
        # auto-evicted or auto-demoted.
        if pinned and not target.pinned:
            pinned_active = repo.count_pinned_critical_notes(project_id)
            if pinned_active >= _CORE_CAP:
                candidate_entries = _load_entries(
                    repo,
                    project_id,
                    predicate=lambda c: (
                        c.pinned
                        and c.superseded_by_id is None
                        and c.id != entry_id
                    ),
                )
                demotion = sorted(
                    candidate_entries,
                    key=lambda e: (-_PRIORITY_ORDER.get(e.priority, 2), e.created_at),
                )[:_MAX_EVICTION_CANDIDATES_NAMED]
                demotion_lines = "\n".join(
                    f"  - id={c.id} priority={c.priority} created_at={c.created_at} summary={c.summary!r}"
                    for c in demotion
                )
                return {
                    "error": (
                        f"Core tier is at the cap of {_CORE_CAP} pinned notes "
                        f"for project '{project_id}'. Unpin one of the "
                        f"following demotion candidates via "
                        f"project_cn_pin(entry_id=..., pinned=false) and "
                        f"re-try, or choose a different note. Nothing was "
                        f"changed.\n"
                        + (demotion_lines if demotion_lines else "  (no other pinned notes)")
                    )
                }

        updated = repo.pin_critical_note(project_id, entry_id, pinned=pinned, pinned_by=agent_id)
        if updated is None:
            return {"error": f"Entry '{entry_id}' no longer exists; pin not applied"}
        return updated.to_dict()

    project_cn_pin._full_doc_ = """Pin or unpin a critical note into the always-injected core tier.

The core tier is the set of PINNED + ACTIVE notes that always load into
the project context block (cap: 8). Pinning is REJECT-don't-evict:
pinning a 9th note REFUSES with demotion candidates named (which pinned
notes to unpin) and changes nothing — nothing is ever auto-evicted.
Unpinning is also a curation write (refreshes last_reviewed_at).

Pin discipline: core = always-relevant contracts/traps, NOT incident
narration. A priority=critical write only SUGGESTS pinning; the pin is
always this explicit call.

Args:
    project_id: The project whose note is being pinned/unpinned
    entry_id: The ID of the entry to pin or unpin
    pinned: true to pin into the core tier, false to unpin

Returns:
    The updated entry dict, or an error dict (cap hit with demotion
    candidates named / superseded target / not found)."""

    @register_tool_category(CATEGORY_NAME)
    @tool
    def project_cn_supersede(project_id: str, old_id: str, new_id: str) -> dict:
        """Mark an old critical note as superseded by a newer one. Use tool_help() for details."""
        project = repo.get(project_id)
        if not project:
            return {"error": f"Project '{project_id}' not found"}

        # Guard 1: identity — superseding a note with itself is a no-op
        # at best and a lineage cycle at worst.
        if old_id == new_id:
            return {"error": "old_id and new_id must differ (a note cannot supersede itself)"}

        old = repo.get_critical_note(project_id, old_id)
        if old is None:
            return {"error": f"Entry '{old_id}' not found in project '{project_id}'"}
        new = repo.get_critical_note(project_id, new_id)
        if new is None:
            return {"error": f"Entry '{new_id}' not found in project '{project_id}'"}

        # Guard 2: same-project (both ids must live in the target
        # project). ``get_critical_note`` already enforces membership, so
        # this explicit check is defense-in-depth — unreachable via the
        # current call paths. It documents the cross-project refusal
        # contract for callers who pass ids from two projects, and
        # protects against future call sites that skip the membership
        # check.
        if old.project_id != project_id or new.project_id != project_id:
            return {"error": "old_id and new_id must belong to the same project"}

        # Guard 3 (§4.4 #24): a SUPERSEDED row cannot act as a
        # superseder. Chain otherwise-tampered lineage: the row already
        # retired by {old.superseded_by_id} must not retire another.
        if old.superseded_by_id is not None:
            return {
                "error": (
                    f"Entry '{old_id}' is already SUPERSEDED by "
                    f"{old.superseded_by_id} — a superseded row cannot "
                    f"supersede another note."
                )
            }

        # Guard 4 (cycle closure, R21): the NEW row must also be ACTIVE.
        # Without this, ``supersede(A, B)`` then ``supersede(B, A)``
        # would succeed (Guard 3 only checks ``old``), leaving both
        # rows mutually hidden. Symmetric check on ``new`` closes the
        # 2-cycle so the lineage graph stays a partial order.
        if new.superseded_by_id is not None:
            return {
                "error": (
                    f"Entry '{new_id}' is already SUPERSEDED by "
                    f"{new.superseded_by_id} — a superseded row cannot "
                    f"be re-targeted as the superseder (would create "
                    f"a 2-cycle)."
                )
            }

        updated = repo.supersede_critical_note(project_id, old_id, new_id)
        if updated is None:
            return {"error": f"Entry '{old_id}' no longer exists; supersede not applied"}
        return updated.to_dict()

    project_cn_supersede._full_doc_ = """Mark an old critical note as superseded by a newer one.

The old note stays in the store (history), disappears from the injected
context, and renders struck-through ("~~summary~~ ✅ superseded by {id}")
in list surfaces. Guards: old_id == new_id is rejected; a SUPERSEDED row
cannot act as superseder; the NEW row must also be ACTIVE (no 2-cycle
— supersede(A,B) then supersede(B,A) is refused, so the lineage graph
stays a partial order); both ids must belong to the same project.
Supersede bumps the old row's last_reviewed_at (a leader write). This is
the PREFERRED maintenance verb — supersede-don't-re-add; a verbatim
re-add after a supersede inserts a fresh row (visible in the list).

Args:
    project_id: The project whose notes are being linked
    old_id: The ID of the note being retired
    new_id: The ID of the note replacing it

Returns:
    The updated old entry dict (superseded_by_id set), or an error dict
    on any guard violation."""

    return [project_cn_add, project_cn_list, project_cn_remove, project_cn_pin, project_cn_supersede]

    return [project_cn_add, project_cn_list, project_cn_remove, project_cn_pin, project_cn_supersede]
