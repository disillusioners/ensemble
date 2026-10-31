# Scratch-Clone Mutation Gate — critical-notes cap-fix @ c8252f916 (+ PK-immutability probe audit)

- Date: 2026-10-31 (UTC evening)
- Executor: Worker (mutation-gate audit, mock-theater check)
- Reviewed worktree (NEVER edited): `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-critical-notes-cap-fix`
- Worktree HEAD verified: `c8252f916` (full: `c8252f916c2a76ec8e4af07315e8c3ff051140b0`)
- Scratch: `/tmp/cncap-mut-scratch` (cp -a incl. .venv; git detached; all edits ONLY there)

## Scratch setup verification

1. `cp -a` copy: OK (490 MB incl. .venv 171 MB).
2. `.git` in copy was a **gitdir FILE** (131 B, linked-worktree pointer) → removed; `git init -q && git add -A && commit -qm baseline` → OK.
3. `git rev-parse --git-common-dir` → `.git`; `git rev-parse --show-toplevel` → `/private/tmp/cncap-mut-scratch` — resolves INSIDE the scratch ✓.
4. Editable path rewritten: `.venv/lib/python3.13/site-packages/_editable_impl_ensemble.pth` → `/tmp/cncap-mut-scratch` (only ensemble .pth found; no `__editable__*` variants).
5. Shebangs rewritten in BOTH `.venv/bin/pytest` and `.venv/bin/py.test` → `#!/tmp/cncap-mut-scratch/.venv/bin/python`.
6. `__pycache__` purged under repo (55 dirs; .venv internals untouched).
7. Import proof (every leg): `./.venv/bin/python -c "import daemon …"` →
   `/private/tmp/cncap-mut-scratch/daemon/__init__.py` exit 0.
   NOTE: naive `startswith('/tmp/…')` check returns exit 4 on macOS because `/tmp` symlinks to `/private/tmp`; proof normalized with `os.path.realpath` — the import IS from the scratch tree. First proof run printed the scratch path with exit 4 (string-prefix artifact only); corrected proof used throughout.
8. Baseline (pre-mutation): all 4 targeted files GREEN —
   `tests/unit/repositories/test_critical_note_id_resolution.py` 15,
   `tests/unit/tools/test_critical_notes_lifecycle.py` 39,
   `tests/unit/services/test_critical_notes_render_phase1.py` 23,
   `tests/unit/tools/test_critical_notes.py` 49. Total 126 passed, exit 0.

## Per-mutation table

Protocol per mutation: sha256 BEFORE → ONE minimal edit (anchor `count==1` verified) → `git diff --stat` exactly ONE site → predicted scope run (`timeout 120`) → require exit EXACTLY 1 + 'failed' summary → `git checkout --` → re-hash == pre-sha → re-run GREEN.

| # | Site (scratch-relative) | Anchor (unique, count==1) | Predicted RED | Observed RED (verbatim) | Exit | Restore sha match | Post-restore GREEN |
|---|---|---|---|---|---|---|---|
| M1 | `daemon/repositories/project/repository.py` `_resolve_critical_note_id` — exact-hit early return re-inserted at top of scan block (`session.get` + project match → `ResolvedCriticalNoteId`) — undo W4 | `        # Project-scoped scan, id column ONLY (W6)` | `…test_exact_hit_with_representation_twin_is_ambiguous` | `tests/unit/repositories/test_critical_note_id_resolution.py::test_exact_hit_with_representation_twin_is_ambiguous`; `::test_ambiguous_refusal_logs_degraded_line`; `::test_resolve_probe_tri_state_shapes` (3 failed / 51 passed; lifecycle file 39/39 green) | 1 | ✓ `f3501e69df21785666cb8f2cbff4d742b97d40608f949a5c7e1ec102286e9f89` | 54 passed |
| M2 | `daemon/repositories/project/critical_note_ids.py` `critical_note_hex_fingerprint` — `fullmatch` → `search` returning matched 32-hex substring (junk-prefixed ids can fingerprint-match) | `    if _HEX32_RE.fullmatch(s):\n        return s\n    return None` | `…test_junk_prefixed_id_never_fingerprint_matches` (+ predicate unit) | `tests/unit/repositories/test_critical_note_id_resolution.py::test_junk_prefixed_id_never_fingerprint_matches`; `::test_predicate_rejects_junk_and_blank_unit_level` (2 failed / 101 passed) | 1 | ✓ `1f6e5562fdd1af260181248470cdb93bbd19aa67b88fa50f92e42c937e31c298` | 103 passed |
| M3 | `daemon/services/context_messages.py` `_format_critical_notes_section` — `section_cap = None` unconditionally (W8 render-all cap bypassed) | 3-line `section_cap = (\n        None if pre_ordered else …\n    )` block | ≥1 node in `TestRenderAllSectionCap` | `tests/unit/services/test_critical_notes_render_phase1.py::TestRenderAllSectionCap::test_render_all_large_set_bounded_with_exactly_one_truncation_line`; `::test_cap_cannot_cut_the_trailing_line`; `::test_cap_is_installable_no_env`; `::test_build_message_render_all_path_bounded` (4 failed / 19 passed) | 1 | ✓ `6deaaca6b16c79c85a2abda11266c74b2d475298c7c0c9e55306bbd601f59ec6` | 23 passed |
| M4 | `daemon/repositories/project/repository.py` `remove_critical_note` — `resolved = stored_id_or_none(self._resolve_critical_note_id(…))` → `resolved = entry_id` (resolver bypassed, raw key) | block incl. `session.delete(note)` (count==1) | ≥1 RED in `TestCriticalNoteIdResolutionToolLevel`; variant params `test_variant_resolves_and_removable[*]` | RED landed at the REPOSITORY seam (opposite of the task's hedge): `tests/unit/repositories/test_critical_note_id_resolution.py::test_variant_resolves_and_removable[d0371008-a55d-4b86-831d-1c76b381a920 ]`; `::test_variant_resolves_and_removable[\u200bd0371008-a55d-4b86-831d-1c76b381a920]`; `::test_variant_resolves_and_removable[D0371008-A55D-4B86-831D-1C76B381A920]`; `::test_variant_resolves_and_removable[d0371008a55d4b86831d1c76b381a920]`; collateral `::test_junk_prefixed_id_never_fingerprint_matches`; `::test_exact_hit_with_representation_twin_is_ambiguous`; `::test_ambiguous_refusal_logs_degraded_line` (7 failed / 13 passed). **Tool-level class 5/5 GREEN** — mechanism: `project_cn_remove` calls `repo.get_critical_note(project_id, entry_id)` (resolver-routed) FIRST, then `repo.remove_critical_note(project_id, removed_entry.id)` with the already-canonical id → remove-internal bypass is invisible at the tool seam (redundant routing); the delete seam kills it. | 1 | ✓ `f3501e69df21785666cb8f2cbff4d742b97d40608f949a5c7e1ec102286e9f89` | 20 passed |
| M5 | `daemon/tools/critical_notes.py` — `_MAX_ENTRIES = 200` → `50` | `^_MAX_ENTRIES = 200$` | `TestCapBehavior::test_cap_boundary_at_exact_cap_rejected`, `::test_cap_boundary_one_below_accepted`, `::test_cap_regression_51_preexisting_accepted`, `TestConstants::test_max_entries_is_200` | `tests/unit/tools/test_critical_notes.py::TestCapBehavior::test_cap_regression_51_preexisting_accepted`; `tests/unit/tools/test_critical_notes.py::TestConstants::test_max_entries_is_200` (2 failed / 47 passed). The two boundary nodes SURVIVE BY CONSTRUCTION: both build their fixture via `range(_MAX_ENTRIES)` (cap-value-agnostic), so any constant passes them — the value-pin + 51-row regression pin are the killing nodes. | 1 | ✓ `e4a49a5d7dbb04d05b229c7c4852ef4fbf42698aed6bd9dd03c71030f4ce2428` | 49 passed |
| M6 (PK probe confirmation) | `daemon/repositories/project/repository.py` `update_critical_note` — `note.id = normalize_critical_note_id(note.id)` before commit (silent PK rewrite; targeted at a write path the PK test actually drives — the test never calls add) | block `note.last_reviewed_at = now\n\n            session.commit()…` (count==1) | pk node RED | `tests/unit/repositories/test_critical_note_id_resolution.py::test_pk_immutable_on_load_and_write` — failed at `assert raw_id == variant` (raw-SQL read caught canonicalized PK; canonical ≠ stored variant with trailing ZWSP) (1 failed) | 1 | ✓ `f3501e69df21785666cb8f2cbff4d742b97d40608f949a5c7e1ec102286e9f89` | 1 passed |

All exit codes read from bare pytest (no pipe masking): `… pytest … -q > out 2>&1; echo $?`.

## PK-immutability probe audit

Test: `tests/unit/repositories/test_critical_note_id_resolution.py::test_pk_immutable_on_load_and_write` (lines 273–302).

**Verdict: LOAD-PATH-REAL + WRITE-PATH-REAL (NOT a construct-only tautology).**

Decisive lines:

- LOAD path — raw-SQL legacy-row insert, then repository read-back, byte-form asserted:
  - `:279-280` `variant = CANONICAL + "\u200b"` / `_raw_insert(engine, project_id, variant)` — non-canonical STORED id inserted like a legacy row via raw SQL (`_raw_insert`, `:44-54`, plain `text("INSERT INTO critical_notes …")`, bypassing the model).
  - `:282-284` `n = repo.get_critical_note(project_id, CANONICAL); assert n is not None` — repository ORM load resolves canonical request to the variant row.
  - `:300-302` `listed = repo.list_critical_notes(project_id); assert "\u200b" in listed[0].id` — repository load path returns the stored byte form (an ORM-side normalizing validator would fail here).
- WRITE path — repository writes on the canonical request, then RAW-SQL re-read compared byte-identical to the stored variant:
  - `:287-288` `repo.pin_critical_note(project_id, CANONICAL, True, "leader")` / `repo.update_critical_note(project_id, CANONICAL, summary="changed")`.
  - `:290-297` raw `SELECT id FROM critical_notes WHERE project_id = :p` → `assert raw_id == variant` + `assert "\u200b" in raw_id` — the comparison is made OUTSIDE the ORM, so an ORM-level rewrite cannot fake a pass.
- Mutation confirmation (M6): injecting `note.id = normalize_critical_note_id(note.id)` into `update_critical_note` (a path the test drives) → pk node RED at exactly `assert raw_id == variant` (exit 1). Probe is mutation-killed on its stated invariant; survival under M6 would have meant tautology, but it died. → probe REAL.

Residual note (non-blocking): the test never calls the add/create path, so add-path PK handling is outside this probe's coverage (M6 was aimed at update, not add, for that reason). If add-path id normalization is ever a concern, a sibling probe pinning `add_critical_note` byte-preservation would close it.

## Collateral / observations

- M1 collateral: `test_ambiguous_refusal_logs_degraded_line`, `test_resolve_probe_tri_state_shapes` — both W4-ambiguity-contract nodes; expected to fall with the fast path (the ambiguity check is their subject). No unexpected files affected.
- M4 collateral: junk-prefixed + twin + degraded-line nodes in the repository file — all in the resolution contract family, consistent with bypassing the resolver in remove.
- No reds appeared outside `tests/unit/repositories/test_critical_note_id_resolution.py`, `tests/unit/services/test_critical_notes_render_phase1.py`, `tests/unit/tools/test_critical_notes.py` in any leg (scopes were file/class-targeted by design).
- Exit taxonomy honored: every mutation leg exited EXACTLY 1 with a `failed` summary; no 0 (survived), no 2–5 (harness fault).
- Port 8088 untouched; no daemon interaction; all runs SQLite in-memory unit scopes; no commits made anywhere; reviewed worktree untouched except this evidence file.

## Final state

- Scratch final full gate: all 4 files, 126 passed, exit 0; import proof exit 0 post-restore.
- `git status --porcelain` in scratch after M6 restore: clean (0 diff lines).
- Scratch cleanup: `rm -rf /tmp/cncap-mut-scratch` executed at end (see report confirmation).
- Reviewed worktree files touched by this audit: ONLY this evidence file.
