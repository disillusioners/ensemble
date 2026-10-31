# 2026-10-31 — critical-notes cap-fix: READ-ONLY delta + claim-verification map

Worktree: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-critical-notes-cap-fix`
Branch: `feature/critical-notes-cap-fix` · Base: `78f1aaead` · Mid: `5aa92c915` · Tip: `c8252f916`
Mode: read-only (no edits, no commits, no checkout/reset/stash, no daemon ops; only this evidence file written).

HEAD pre-check (gate): `git -C <wt> rev-parse --short HEAD` → **c8252f916** ✓ (chain confirmed: 78f1aaead → 5aa92c915 → c8252f916).

---

## Deliver 1 — delta map

### git log --oneline 5aa92c915..c8252f916
```
c8252f916 fix(critical-notes): review round — strict fingerprint, tri-state resolve, render-all cap
```

### git diff --stat 5aa92c915..c8252f916
```
 CHANGELOG.md                                       |   5 +-
 daemon/config.py                                   |   1 +
 daemon/repositories/project/critical_note_ids.py   | 121 ++++++++++--
 daemon/repositories/project/models.py              |  49 +++--
 daemon/repositories/project/repository.py          | 144 ++++++++++----
 daemon/services/context_messages.py                |  87 ++++++++-
 .../critical_notes_selection_orchestrator.py       |   7 +
 daemon/tools/critical_notes.py                     | 137 +++++++++----
 docs/usage.md                                      |  11 +-
 .../test_critical_note_id_resolution.py            | 163 +++++++++++++++-
 .../services/test_critical_notes_render_phase1.py  | 109 +++++++++++
 tests/unit/tools/test_critical_notes.py            | 211 +++++++++++++++++++--
 tests/unit/tools/test_critical_notes_lifecycle.py  |  66 +++++--
 13 files changed, 962 insertions(+), 149 deletions(-)
```

### git diff --name-status 78f1aaead..c8252f916 (full 13-file list)
```
M	CHANGELOG.md
M	daemon/config.py
A	daemon/repositories/project/critical_note_ids.py
M	daemon/repositories/project/models.py
M	daemon/repositories/project/repository.py
M	daemon/services/context_messages.py
M	daemon/services/critical_notes_selection_orchestrator.py
M	daemon/tools/critical_notes.py
M	docs/usage.md
A	tests/unit/repositories/test_critical_note_id_resolution.py
M	tests/unit/services/test_critical_notes_render_phase1.py
M	tests/unit/tools/test_critical_notes.py
M	tests/unit/tools/test_critical_notes_lifecycle.py
```

---

## Deliver 2 — claim verification (W1–W9)

### W2 — CONFIRMED — strict fingerprint, fullmatch `[0-9a-f]{32}` on BOTH sides
`daemon/repositories/project/critical_note_ids.py` — module constants (:59-66):
```python
# The ONLY shape the fingerprint arm accepts on BOTH sides (W2): exactly
# 32 hex chars once whitespace + separators are stripped.
_HEX32_RE = re.compile(r"[0-9a-f]{32}")

# Separators the fingerprint arm may strip: whitespace runs + hyphens
# (canonical UUID separators). NOTHING else — any other surviving char
# (letters, punctuation, junk prefixes) disqualifies the fingerprint.
_FINGERPRINT_STRIP_RE = re.compile(r"[\s\-]+")
```
Fingerprint function in FULL (:92-111):
```python
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
```
Match predicate in FULL (:114-139):
```python
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
```
Both sides fingerprint-checked (`fp_stored is not None and fp_stored == fp_requested`, :135-138) ✓. `fullmatch` used (:109) ✓.

### W3 — CONFIRMED — empty-after-normalize refused PRE-scan, `reason=empty_after_normalize`
`daemon/repositories/project/repository.py:1596-1608` (inside `_resolve_critical_note_id`, before the scan):
```python
requested = "" if entry_id is None else str(entry_id)

# W3 guard FIRST: an empty-after-normalize request can never
# address a real row — if a stored ""/blank PK exists in any
# legacy store, the scan must not silently serve it to a remove.
if not normalize_critical_note_id(requested):
    logger.info(
        "[CriticalNotes:Degraded] stage=id_resolve "
        "reason=empty_after_normalize project_id=%s requested=%r",
        project_id,
        requested,
    )
    return MissingCriticalNoteId()
```
grep hits: `repository.py:1604` (the reason string) + `tests/unit/repositories/test_critical_note_id_resolution.py:176` (comment). Only hits (excluding .pyc).

### W4 — CONFIRMED — exact-PK fast path REMOVED; tri-state resolve; twin ⇒ AMBIGUOUS even on byte-exact request
CURRENT full method body — `daemon/repositories/project/repository.py:1574-1632`:
```python
def _resolve_critical_note_id(
    self, session, project_id: str, entry_id: str
) -> CriticalNoteIdResolution:
    """Resolve a (possibly format-variant) entry_id — TRI-STATE (W5).

    Returns:
    * ``ResolvedCriticalNoteId(stored_id)`` — exactly one row in
    the project matches the requested representation;
    * ``AmbiguousCriticalNoteId(match_count)`` — ≥2 rows share
    the requested representation. NOTHING is selected, nothing
    changes; one ``[CriticalNotes:Degraded]
    stage=id_resolve reason=ambiguous`` line is logged here.
    This check runs on the EXACT-BYTE hit too (W4): a
    case-variant twin must never let a remove silently pick
    one of two rows;
    * ``MissingCriticalNoteId`` — zero matches. A request that
    normalizes to "" is refused BEFORE the scan (W3): it can
    never address a row, and stored ""/blank PKs are skipped.

    Read-only: never rewrites a stored id. NO fuzzy matching — ids
    match by REPRESENTATION only (see ``critical_note_ids``).
    """
    requested = "" if entry_id is None else str(entry_id)

    # W3 guard FIRST: ... (see W3 quote above)
    if not normalize_critical_note_id(requested):
        logger.info(... reason=empty_after_normalize ...)
        return MissingCriticalNoteId()

    # Project-scoped scan, id column ONLY (W6): the resolver never
    # needs the row bodies — detail_ref payloads stay unpaged. The
    # scan SUBSUMES the old cheap exact-PK path (exact byte equality
    # is arm 1 of the predicate) and lets the W4 ambiguity check run
    # on every resolution, exact hit included.
    rows = list(session.exec(
        select(CriticalNoteModel.id).where(
            CriticalNoteModel.project_id == project_id
        )
    ))
    matches = [rid for rid in rows if critical_note_id_matches(rid, requested)]
    if len(matches) >= 2:
        logger.info(
            "[CriticalNotes:Degraded] stage=id_resolve reason=ambiguous "
            "project_id=%s requested=%r match_count=%d",
            project_id, requested, len(matches),
        )
        return AmbiguousCriticalNoteId(match_count=len(matches))
    if len(matches) == 1:
        return ResolvedCriticalNoteId(matches[0])
    return MissingCriticalNoteId()
```
- **No `session.get`-first shortcut**: the method's first DB touch is the `select(CriticalNoteModel.id)` scan; `session.get` appears only AFTER resolution, at the caller seams (`get_critical_note` :1663, `remove_critical_note` :1805, `update_critical_note` :1839 — post-resolution fetch by resolved stored id). Docstring: "The scan SUBSUMES the old cheap exact-PK path" (:1612-1614) ✓.
- **Twin + byte-exact id requested ⇒ AMBIGUOUS (refusal), not match**: `len(matches) >= 2` fires BEFORE the single-match arm regardless of whether one match is byte-exact. Pinned by `test_exact_hit_with_representation_twin_is_ambiguous` (test_critical_note_id_resolution.py:181-197): seeds CANONICAL + CANONICAL.upper(), `resolve(CANONICAL)` → `AmbiguousCriticalNoteId(match_count=2)`, and `remove_critical_note(CANONICAL)` AND `remove(CANONICAL.upper())` both return False; both rows survive. Tri-state: match / ambiguous / none ✓.

### W5 — CONFIRMED — ambiguity refusal message + caplog pin at repository seam
Tool-layer refusal message — `daemon/tools/critical_notes.py:255-283` (`_not_found_error`, TRI-STATE aware):
```python
resolution = repo.resolve_critical_note_id(project_id, entry_id)
if isinstance(resolution, AmbiguousCriticalNoteId):
    return {
        "error": (
            f"{which} '{entry_id}' matches {resolution.match_count} "
            "entries' representation — ambiguous, nothing changed; "
            "use project_cn_list and retry with the exact id"
        )
    }
cands = _near_id_candidates(_load_entries(repo, project_id), entry_id)
hint = ""
if cands:
    hint = " Near-match(es): " + ", ".join(repr(c) for c in cands) + " — retry with the exact id."
return {"error": f"{which} '{entry_id}' not found in project '{project_id}'" + hint}
```
(Note: actual wording is "matches N entries' representation — ambiguous, nothing changed" — semantically the claimed "N entries match this id's representation"; ambiguous refusals carry NO near-match hint, not-found does.)

grep evidence: `critical_notes.py:51` (imports AmbiguousCriticalNoteId), :262, :272-276 (message), :440, :500 (tri-state aware call sites), :585, :612.

Caplog pin at the REPOSITORY seam — `tests/unit/repositories/test_critical_note_id_resolution.py:199-216`:
```python
def test_ambiguous_refusal_logs_degraded_line(repo, engine, project_id, caplog):
    """W5 pin (REPO SEAM): the refuse path logs stage=id_resolve
    reason=ambiguous under the [CriticalNotes:Degraded] prefix."""
    import logging

    _raw_insert(engine, project_id, CANONICAL)
    _raw_insert(engine, project_id, CANONICAL.upper())
    with caplog.at_level(logging.INFO, logger="daemon.repositories.project.repository"):
        repo.remove_critical_note(project_id, CANONICAL)  # refuses
    degraded = [
        r for r in caplog.records
        if "stage=id_resolve" in r.getMessage()
        and "reason=ambiguous" in r.getMessage()
        and "[CriticalNotes:Degraded]" in r.getMessage()
    ]
    assert degraded, "expected [CriticalNotes:Degraded] stage=id_resolve reason=ambiguous"
    assert len(repo.list_critical_notes(project_id)) == 2
```

### W6 — CONFIRMED — id-only resolver scan + two-column pointer scan + bulk UPDATE
Resolver scan, id column only — `repository.py:1610-1619`:
```python
# Project-scoped scan, id column ONLY (W6): the resolver never
# needs the row bodies — detail_ref payloads stay unpaged. ...
rows = list(session.exec(
    select(CriticalNoteModel.id).where(
        CriticalNoteModel.project_id == project_id
    )
))
```
Pointer-clear scan projects ONLY (id, superseded_by_id) — `repository.py:2047` (`clear_superseded_by_pointers`), :2064-2076:
```python
# W6 (review 2026-10-31): project ONLY (id, superseded_by_id) —
# the old full-entity scan paged every row's unbounded
# detail_ref payload just to read one pointer column.
rows = list(session.exec(
    select(
        CriticalNoteModel.id,
        CriticalNoteModel.superseded_by_id,
    ).where(CriticalNoteModel.project_id == project_id)
))
matching_ids = [
    rid
    for rid, pointer in rows
    if pointer is not None
    and critical_note_id_matches(pointer, entry_id)
]
```
Bulk UPDATE — `repository.py:2078-2088`:
```python
if matching_ids:
    now = datetime.now(timezone.utc).isoformat()
    session.execute(
        update(CriticalNoteModel)
        .where(CriticalNoteModel.id.in_(matching_ids))
        .values(superseded_by_id=None, updated_at=now)
    )
    session.commit()
return matching_ids
```

### W8 — CONFIRMED — render-all injection bounded by `critical_notes.section_char_cap` (12k) with canonical truncation line
Cap default 12000 — `daemon/services/critical_notes_selector.py:67` (`section_char_cap: int = 12000`); config field `daemon/config.py:4810` (`CriticalNotesConfig.section_char_cap`); renderer mirror comment `daemon/services/context_messages.py:706` ("default mirrors ``CriticalNotesConfig.section_char_cap``"), resolver `context_messages.py:747-749`.

Bound + truncation code — `daemon/services/context_messages.py:875-883` (apply) and :925-932 (truncation line):
```python
# Apply the SAME section_char_cap here (config-installed), dropping
# whole rows (selector _enforce_char_cap semantics) ...
section_cap = (
    None if pre_ordered else _resolve_critical_notes_section_char_cap()
)
...
if section_cap is not None and used_chars + len(line) + 1 > section_cap:
    # W8: whole-row drop; every remaining row is named by the
    # count in the canonical truncation line below. (+1 = the
    # "\n" join separator, so the FINAL text — not just the row
    # budget — stays inside the cap.)
    hidden_count = len(ordered) - rendered_row_count
    break
...
if hidden_count > 0:
    # W8 render-all truncation line — the EXISTING canonical copy ...
    rendered.append(
        f"- …({hidden_count} additional notes not shown — use "
        "project_cn_list for the full view)"
    )
```
TIERED path never re-capped (`section_cap=None` when `pre_ordered=True`), protecting the `__hint_drop_count` sentinel (:875-883 comment; sentinel pop at :890-895).

Test files pinning it: `tests/unit/services/test_critical_notes_render_phase1.py` (nodes at :254 `test_render_all_large_set_bounded_with_exactly_one_truncation_line`, :276 `test_render_all_small_set_renders_in_full`, :283 `test_cap_cannot_cut_the_trailing_line`, :297 `test_cap_is_installable_no_env`, :308 `test_tiered_path_is_never_re_capped_sentinel_survives`, :328 `test_build_message_render_all_path_bounded`); plus default-12k pin `tests/unit/test_critical_notes_config.py:57` (`assert cn.section_char_cap == 12000`).

### W9 — CONFIRMED — cross-project leg uses a REAL project-B note
`tests/unit/tools/test_critical_notes_lifecycle.py:298-353` (`test_rejects_cross_project_ids`, class member):
```python
def test_rejects_cross_project_ids(self, tools, repo, project_id):
    """W9: cross-project REAL ids refuse with no hint and no leak.

    Seeds a REAL note in project B, then calls remove/pin/supersede
    scoped to project A with B's REAL id. ...
    """
    other = repo.create(name="cn-other-project")
    a = repo.add_critical_note(
        project_id, source_agent="l", category="risk", priority="high",
        summary="Note in the first project",
    )
    b = repo.add_critical_note(
        other.project_id, source_agent="l", category="risk", priority="high",
        summary="Note in the second project",
    )

    # REMOVE scoped to A with B's real id.
    res_remove = tools["project_cn_remove"].invoke(
        {"project_id": project_id, "entry_id": b.id}
    )
    assert "error" in res_remove
    assert "Near-match(es):" not in res_remove["error"]
    assert res_remove["error"].count(b.id) == 1  # echo only, never a hint

    # PIN scoped to A with B's real id.
    res_pin = tools["project_cn_pin"].invoke(
        {"project_id": project_id, "entry_id": b.id, "pinned": True}
    )
    assert "error" in res_pin
    assert "Near-match(es):" not in res_pin["error"]
    assert res_pin["error"].count(b.id) == 1

    # SUPERSEDE scoped to A: old = A's real note, new = B's real id.
    res_sup = tools["project_cn_supersede"].invoke(
        {"project_id": project_id, "old_id": a.id, "new_id": b.id}
    )
    assert "error" in res_sup
    assert "Near-match(es):" not in res_sup["error"]
    assert res_sup["error"].count(b.id) == 1

    # No pointer written; both rows untouched in their projects.
    assert repo.get_critical_note(project_id, a.id).superseded_by_id is None
    b_row = repo.get_critical_note(other.project_id, b.id)
    assert b_row is not None
    assert b_row.superseded_by_id is None
    assert b_row.pinned is False
    assert repo.get_critical_note(other.project_id, b.id).summary == (
        "Note in the second project"
    )
    assert repo.count_critical_notes(other.project_id) == 1
```

### Minors
- **`_not_found_error` extraction — CONFIRMED**: `daemon/tools/critical_notes.py:255` `def _not_found_error(repo, project_id: str, entry_id: str, *, which: str = "Entry") -> dict`; call sites :502, :726, :801 (default which), :889 (`which="old_id"`), :894 (`which="new_id"`).
- **`_MAX_NEAR_ID_HINTS = 3` — CONFIRMED**: `critical_notes.py:82`; used as default in `_near_id_candidates(..., limit=_MAX_NEAR_ID_HINTS)` (:215); pinned by `tests/unit/tools/test_critical_notes.py:1688-1690` (`from daemon.tools.critical_notes import _MAX_NEAR_ID_HINTS; assert _MAX_NEAR_ID_HINTS == 3`).
- **Dead constant `CRITICAL_NOTES_MAX_ENTRIES` — DELETED**: `grep -rn CRITICAL_NOTES_MAX_ENTRIES daemon tests` → 0 hits. Replacement: `daemon/tools/critical_notes.py:70` `_MAX_ENTRIES = 200` ("Raised from 30 to 50 (2026-09-10), then to 200 (cap-fix mission)").
- **Cap message "(currently N)" — CONFIRMED**: `critical_notes.py:538` `if len(entries) >= _MAX_ENTRIES:` → :546-547:
  ```python
  f"Project '{project_id}' is at the cap of {_MAX_ENTRIES} "
  f"(currently {len(entries)}) critical notes. "
  ```

### W1 — CONFIRMED — CHANGELOG rescoped (class-fix framing + forensic note)
`git diff 78f1aaead..c8252f916 -- CHANGELOG.md` hunk (@@ -7,6 +7,15 @@ under `## [Unreleased] — 2026-10-24`):
```markdown
+### Fixed — critical-notes ids are resolved by representation (cap 50 → 200) (`feature/critical-notes-cap-fix`)
+
+> **Scope + forensic note (2026-10-31 review round):** *this fix is about the CLASS, not any live row: rows whose STORED id is a pure representation variant (whitespace / zero-width / unicode dash / case / hyphenless 32-hex) become removable, pinnable, updatable and supersedable again. It does NOT repair the incident that motivated the investigation — that incident was forensically resolved (byte-verified, read-only) as caller-side id mis-transcription on an already-deleted canonical row; no store defect existed. Structurally malformed ids (wrong hex count) are NOT auto-resolved — the not-found error names the stored form (`repr()`-escaped) for an exact-bytes retry.*
+
+- **Critical-notes ids resolve by pure representation variants, not byte-exact** (...) — stored ids are matched exact → normalized (...) → STRICT 32-hex fingerprint (both sides must reduce to exactly 32 hex chars after stripping only whitespace + separators, so junk-prefixed ids match nothing) ... Resolution is TRI-STATE ... **Structurally malformed ids (wrong hex count, e.g. 8-4-4-5-12) are NOT auto-resolved** ...
+- **Total entry cap raised 50 → 200** (`daemon/tools/critical_notes.py`, `docs/usage.md`) — the pinned core cap stays 8; reject-don't-evict and eviction-candidate naming are unchanged.
+- **Render-all injection is now char-bounded (cap-raise consequence)** (...) — the 12k `critical_notes.section_char_cap` previously covered only the TIERED (pinned) path; a zero-pin project rendered ALL active notes unbounded ... closing with the existing canonical line `- …(N additional notes not shown — use project_cn_list for the full view)` ...
+- **Behavior change — variant supersede pointers are now matched** — `project_cn_remove(cascade=false)` can now REFUSE where it previously succeeded on an exact id whose `superseded_by_id` pointer was stored as a format variant ...
```

---

## Deliver 3 — test-shape map (collect-only; command: `timeout 120 .venv/bin/python -m pytest <f> --collect-only -q --override-ini="addopts=" -p no:randomly`)

| test file (changed 78f1aaead..c8252f916) | collected |
|---|---|
| tests/unit/repositories/test_critical_note_id_resolution.py | 15 tests collected in 0.12s |
| tests/unit/services/test_critical_notes_render_phase1.py | 23 tests collected in 0.12s |
| tests/unit/tools/test_critical_notes.py | 49 tests collected in 0.11s |
| tests/unit/tools/test_critical_notes_lifecycle.py | 39 tests collected in 0.11s |

(Note: resolution file has 12 `def test_` functions but 15 collected nodes — `test_variant_resolves_and_removable` is fixture-parametrized over id variants.)

### tests/unit/services/test_critical_notes_render_phase1.py — ALL node names (`grep -n "def test_"`)
```
58:    def test_superseded_rows_never_injected(self):
65:    def test_all_superseded_renders_empty(self):
69:    def test_all_non_superseded_rendered_phase1(self):
77:    def test_pinned_tier_first_then_remainder(self):
87:    def test_recency_within_tier_newest_first(self):
93:    def test_unknown_priority_sorts_last_within_group(self):
103:    def test_over_bound_truncated_with_suffix(self):
110:    def test_within_bound_untouched(self):
116:    def test_bound_is_installable_no_env(self):
126:    def test_detail_ref_never_in_injected_block(self):
131:    def test_detail_ref_not_in_built_message(self):
140:    def test_block_still_single_project_context_kind(self):
145:    def test_injected_block_has_no_maintenance_marks(self):
178:    def test_pre_ordered_mode_preserves_fusion_order(self):
190:    def test_pre_ordered_mode_still_filters_superseded_and_emits_hint(self):
201:    def test_default_mode_keeps_legacy_r19_resort(self):
211:    def test_block_level_tail_order_matches_fusion_order(self):
254:    def test_render_all_large_set_bounded_with_exactly_one_truncation_line(self):
276:    def test_render_all_small_set_renders_in_full(self):
283:    def test_cap_cannot_cut_the_trailing_line(self):
297:    def test_cap_is_installable_no_env(self):
308:    def test_tiered_path_is_never_re_capped_sentinel_survives(self):
328:    def test_build_message_render_all_path_bounded(self):
```

### tests/unit/repositories/test_critical_note_id_resolution.py — FULL node-name list (`grep -n "def test_"`)
```
66:def test_variant_resolves_and_removable(repo, engine, project_id, variant):
76:def test_malformed_shape_is_asserted_limitation_not_resolved(
119:def test_junk_prefixed_id_never_fingerprint_matches(repo, engine, project_id):
135:def test_predicate_rejects_junk_and_blank_unit_level():
163:def test_blank_request_never_deletes_stored_empty_pk(repo, engine, project_id):
181:def test_exact_hit_with_representation_twin_is_ambiguous(repo, engine, project_id):
199:def test_ambiguous_refusal_logs_degraded_line(repo, engine, project_id, caplog):
218:def test_resolve_probe_tri_state_shapes(repo, engine, project_id):
236:def test_ambiguity_returns_none(repo, engine, project_id):
247:def test_get_update_pin_supersede_resolve(repo, engine, project_id):
267:def test_supersede_old_equals_new_guard(repo, engine, project_id):
273:def test_pk_immutable_on_load_and_write(repo, engine, project_id):
```

---

## Method note
All commands read-only (git log/diff, grep, sed, pytest --collect-only). No daemon ops, no port 8088 contact, no DB connections. Single write: this evidence file. Collected counts reflect the worktree's own `.venv` editable install at HEAD c8252f916.
