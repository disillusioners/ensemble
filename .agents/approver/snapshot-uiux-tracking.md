# Tracking: snapshot-uiux (Snapshot UI/UX Dedicated Page)

Plan package: .agents/shared/planning/snapshot-uiux/ (plan-overview, be-plan, fe-plan, sequencing, design/ spec+amendment+mockup)
Worktree: /home/nea/ensemble-src-wt-snapshot-uiux | Branch: feature/snapshot-uiux

## Iteration 001 — REJECTED (2026-10-05)
Dispatch: 3 section-parallel workers, plan-approval skill each (cold context).
- A framing/sequencing/consistency: 8c336ed5-4db2-4075-abba-32941062210f — REJECTED, 1 blocking
- B backend (be-plan.md): 5860198b-7f60-43cd-8175-900939647dc7 — APPROVED, 0 blocking, 5 notes
- C frontend+design (fe-plan.md, design/): 827f8cd8-d8e0-4dde-becd-38fec5cd3b23 — APPROVED, 0 blocking, 6 notes

### Blocking (1)
1. be-plan.md §4.4 lines 273-279 — flat contradiction of the rest of the package on toggle relocation: states "The snapshot-uiux feature does not relocate the toggle (it lives on the Settings page, not the Snapshots page)" vs 14 other sources (brief; plan-overview §1 L26 + §4 row 7; fe-plan §2.1 row 7, §4.2 L177/179, §5.2 L264-268, §5.5, §10.3 row 10, §10.4 row 14; sequencing §4.3 E2E steps 7-8, §6.2 CHANGELOG; amendment AC-2.x; actual settings.component.html:242-321 block). Unattended overnight implementer reading be-plan as lane source-of-truth can mis-implement or dead-state the toggle (atomicity mandate makes this an implementation hazard, not wording). Fix: delete the sentence or rescope §4.4 to the API endpoint only (toggle API stays at daemon/routers/settings.py:657-691; UI control relocates per fe-plan §4.2 + sequencing §4.3 step 7).

### Notes (non-blocking, merged/deduped across workers)
- be-plan §4.3 L269 + case 22: `deprecated=True` does NOT auto-add Deprecation/Sunset headers — copy 4-line manual pattern from daemon/routers/blueprints.py:551-555 or test 22 fails.
- be-plan §8.1 L658: `daemon._engine_for_tests` doesn't exist — use test_missions_api.py:124-135 router-into-test-app pattern.
- Test-count bookkeeping: "~36" (overview §3/§7, seq §4.1) counts new-file cases only; be-plan §8 totals 45 incl. 7 extending-repo + 1 search-service + 1 settings-regression — overnight gate should also run existing test_snapshot_repository.py.
- plan-overview §8 / seq §6.4: 4 manual green-light gates (D-1..D-7 sign-off, wall-clock confirm, unify-spawn-tools rebase ack, 7-commit accept) are PRE-implementation — operator must tick all 4 (esp. rebase ack) before dispatching the unattended run.
- Wall-clock drift: overview §5 "20.5h serial" vs phase sum 21.5h (21.0h w/o P3); FE "one overnight" framing vs overview 2-overnights-or-pair guidance. Operational cut-line unaffected.
- Mockup is pre-amendment doc-debt: 5 age toggles vs D-7's 4; drawer warm-spawn section vs D-5 drop; emerald #10b981 vs frozen-spec Material #4caf50 (WCAG-AA pinned); warm-count numbers vs "—". Implementer reads fe-plan + amendment, not mockup.
- fe-plan §3.1 L86-110 example JSON shows project_name/warm_spawn_count that L112 disclaimer disowns — canonical shape is amendment §4 SnapshotRow (L262-281).
- Drawer section count: fe-plan §5.4/§8.1 "8 sections" vs amendment D-5's 7 — relax e2e assertion to 7 (e2e_snapshots_drawer).
- Citation slips: snapshot-detail-drawer spec ref schedule-detail-drawer.component.spec.ts doesn't exist (use skill-usage-table.component.spec.ts:89-97 pattern); manager anchor 1861-1864 → actual 1869; app.component.spec.ts doesn't exist (fe-plan §8.3 already hedges).
- unify-spawn-tools side: daemon/services/_tool_registry.py renamed on their branch — re-verify pre-merge per seq §3.4; hunk-number format differs overview §6 vs seq §3.4 (cosmetic; git diff -U0 is truth).

Next: iteration 002 upon revised plan re-submission.

## Iteration 002 — REJECTED (2026-10-05)
Dispatch: 3 FRESH section-parallel workers (cold prompts, no iteration-001 history carried).
- A framing/consistency: 43f3b544-ae9c-41eb-b372-6610cd2982d3 — REJECTED, 3 blocking
- B backend: b5e3f12f-9cea-4af1-80bb-7cf1b65c9ae2 — REJECTED, 4 blocking
- C frontend+design: 7959d1d4-bdb2-40a5-a1ba-cdf8cf7b1c45 — APPROVED, 0 blocking, 11 notes

Iteration-001 status: §4.4 toggle contradiction CONFIRMED FIXED (be-plan §4.4 L291-299 rescopes to API endpoints); deprecation headers, _engine_for_tests, drawer 7-sections, §3.1 JSON all verified fixed by their lanes.

### Blocking (7 after merge)
MERGED TEST-COUNT BOOKKEEPING (A1 + B-note + C-note8):
1. sequencing.md §4.1 — header L263 "33 new test cases" (stale pre-amendment) vs table L265-272 summing 45 vs footer L273 "~36" vs plan-overview L19/L116 "45"; AND the 45 rollup itself double-counts §8.6 (== §8.1 case 23) → 44 UNIQUE planned tests; AND seq P5 row carries stale FE counts "7/7/5" vs fe-plan §8.1 / seq §4.2 (11/9/10). Same defect class as iter-001, relocated. Unattended test gate unreliable on divergent counts.
2. sequencing.md §2.3 L120 + §4.3 — P6 (13-step browser smoke: gear-menu click, drawer slide, toggle Apply, DevTools URL-block in 11c) listed "must before merge" but unreachable by an unattended pipeline → silent gate loss OR indefinite block. C counter-observation (note 10): checklist precise enough to script; fix = one line declaring P6 executed as automated Playwright e2e (route-interception for 11c) or re-marking it post-merge manual with documented fallback.
3. Run-mode/wall-clock undeclared — plan's own math: 21.5h serial "DOES NOT fit one overnight" (seq §2.3 L102-107), pair ~12h fits (§2.2 L98), overview §5 L90-92 "19h best case, still 2 nights"; dispatch = unattended overnight single-flow. Plan never declares 1-agent-2-nights vs 2-agent-1-night; §2.3 cut-line requires real-time human judgment. Corroborated by C note 9 (overview §8.2 wall-clock confirm is a user gate that must actually be ticked).
4. be-plan §6.2 vs §6.1 — tag-filter pipeline ordering contradiction: param→SQL table maps limit/offset to SQL while tag filter is a Python post-pass → literal reading yields wrong pages and total ≤ limit for ?tags= (FE's core interaction); count query can't include the SQLite post-pass either; §6.2 flips window-function→two-queries mid-section. Fix: pin order explicitly (SQL WHERE+ORDER BY unpaginated → filter_by_tags → total=len(filtered) → Python slice).
5. be-plan §8.1 case 21 vs §4.1/§7 — case pins limit=10000→200 / offset=-5→200 but skeleton has Query(ge=1, le=200) → FastAPI 422 before handler; cited precedent skills.py:1235-1240 has ge=1 and NO le=. Pick one semantics.
6. be-plan §8.1 harness infeasible as specified — (a) copied foreign_keys=ON pragma + FK Snapshot.project_id with no Project seed → IntegrityError on ~all 26 router cases (existing test_snapshot_repository.py:66-76 deliberately omits the pragma); (b) client mounts only snapshots_router → cases 22-23 (/api/settings/*) 404; (c) case 23 needs set_project_repository wiring + system-default project row else 503 (settings.py:93-96).
7. be-plan §5.2/T5 — "from daemon.routers import snapshots_router" needs daemon/routers/__init__.py re-export; file absent from §5.1/§5.2/seq P1 edit lists → ImportError at api.py import time, caught only at T10 AFTER unit gates pass. Fix: add __init__.py row or direct-module import per api.py:135-136 precedent.

### Notes (non-blocking, condensed — full text in worker reports)
- BE: §2 vs §5.1 schemas.py placement wording; D3 repeat-param miscited as existing standard (blueprints.py:60-63 is a body field); §4.3 "exactly/SAME service object" wording (settings router constructs fresh service per request); §7 skeleton import block incomplete (Snapshot, SnapshotUsageMetricsResponse); "status rejects duplicates" not implemented; case 20 pins manager-unreachable state (harmless); old-endpoint deprecation re-export verified no consumer impact.
- Framing: unify-spawn-tools rebase handshake is post-merge (note in unattended brief); §6.4 gates #1/#2/#4 presumed pre-approved — record acks; P5 1.5h zero-buffer; fe-plan 773 vs overview 772; P3∥ single-agent interleaving risk vs 7-commit slicing; amendment procedure should package-wide grep-sweep counts (defect class recurred across files); c6 atomicity = all-or-nothing P4 risk; add documented flaky-spec "1 retry then halt" rule.
- FE/design: mockup still pre-D-5/D-7 (banner comment recommended); digest-fetch ownership unpinned (§5.4 vs §6.4 — one line needed); R5 MatSidenav label wrong (schedules already mat-drawer); §4.1 file-count arithmetic; stale "BE plan NOT YET WRITTEN" header; amendment D-5/D-6 citation drift; design-spec AC-1.2 "placed LAST" unreconciled (outcome-equivalent); menu-order pinned only by e2e step 1 (add unit assertion).

### Aggregation decisions
- A1 merged with B's 44-vs-45 rollup note and C's P5-row note (same defect class, most-specific variants kept).
- A2 kept BLOCKING despite C's note-10 containment read (plan does not state P6's unattended execution mode; C's assumption recorded).
- A3 merged with C's note-9 corroboration.
- No upgrades, no new blocking introduced.

Next: iteration 003 (FINAL before ESCALATED per 3-iteration cap).

## Iteration 003 — REJECTED → ESCALATED (2026-10-05, FINAL — 3-cap reached)
Dispatch: 3 FRESH section-parallel workers (cold prompts).
- A framing/consistency: f4b99283-4c8b-4948-ac34-dd4fc5ff0f9b — REJECTED, 3 blocking
- B backend: 6b89ca00-129b-444f-b928-5e04410696de — REJECTED, 4 blocking
- C frontend+design: 5da65cf2-01cd-486a-9baf-00279607b18b — REJECTED, 2 blocking

Iteration-002 status: ALL 7 blockers + run-brief items CONFIRMED FIXED (pipeline order pinned §6.2; case 21 → 422; harness rebased — pragma dropped, both routers mounted, project seeded; __init__.py re-export listed; counts recomputed package-wide; P6 automated Playwright; pair-mode declared; run-brief §7 present).

### Blocking (8 after merge — the unresolved set for Leader)
1. plan-overview.md:47 (§2 reading order) says "36 test cases" — the exact pre-amendment figure be-plan §8.6:946-948 names as the bug being fixed; every other rollup says 44 (recomputed: 26 router + 10 new-repo + 7 repo-ext + 1 search-ext). One-word fix, but it sits in the doc read FIRST whose job is locking counts. THIRD recurrence of the stale-figure class (iter-001 §4.4 sentence; iter-002 seq §4.1 "33"/"~36" + P5 row).
2. sequencing.md:353 (§4.3 step 10) + :514 (§6.4 gate 3) + :81 (P2 verify): pytest gate pinned to `cd /home/nea/ensemble-src` = MAIN checkout on latest — the four new test files don't exist there; worktree has NO .venv; main .venv editable-install .pth imports the MAIN tree (documented repo trap). Gate not executable as written. Fix: pin worktree cwd + `uv run pytest` (or per-worktree venv bootstrap phase).
3. sequencing.md:337/:353/:516: Playwright spec pinned at `tests/e2e/snapshots.spec.ts` but playwright.config.ts testDir = `./e2e` (existing ~25 specs in frontend/e2e/); repo-root tests/e2e/ is the PYTHON suite → runner finds no tests → 4-GREEN gate can never pass. ALSO frontend/node_modules absent in worktree (no npm ci in any phase — gates 1/2/4 cannot run), AND webServer reuseExistingServer on 8079 can silently e2e the wrong backend. (Merges C's note-level same findings.)
4. be-plan §7 skeleton import surface incomplete (merged B1+B2): `Snapshot` (used 8x from line 538) and `SnapshotUsageMetricsResponse` (used :644/:654-667 + §4.3:256) not imported; §5.1 doesn't list the latter in snapshot_schemas.py either → NameError at module import, unit gate fails at collection. (Iter-2 worker had noted this class as a note; it survived the fix pass.)
5. be-plan §4.3:258/:273-275 + §5.2: `Response` used by deprecated handler (`response.headers[...]`) but not in settings.py's fastapi import (:10) and §5.2 doesn't add it — handler fails to bind (house style precedent blueprints.py:25).
6. be-plan §4.3:276 + §11 T4: `_proxy_to_snapshots_metrics(request)` referenced, never defined anywhere; file placement unstated.
7. fe-plan §5.4 vs §6.4 vs §7.2 vs §8.1(f)(g)(h) + seq §1.2 OK-4: drawer data-flow has two competing owners (§6.4/OK-4 say drawer fetches digest; §5.4 drawer is presentational with no service/loading/error/digest surface; §5.2/§7.2 put loading/error at the page) — violates the plan's own AC-P1 (no re-derivation); forces overnight improvisation.
8. fe-plan §5.2 seenAgents (amendment-#5): page signal "updated on every list() response" but list fetch is private to the table (§5.3), table's only output is rowClick, service items/total signals never populated → Agent dropdown silently degrades to "All agents"; no case among 39 FE specs covers agentOptions → 4-GREEN passes with the filter broken.

### Notes (non-blocking, condensed)
- unify-spawn-tools premise STALE: their branch now has 2 commits (62c33c40, 2fa92fa8) incl. committed settings.component.html edits; hunk map verified verbatim against committed tree; "dirty tree/0-commit" mechanics superseded; no contingency for THEM landing first (mirror needed). Both A and C found this independently.
- Overview §8 gate 3 lacks the "INFORMATIONAL only — do NOT block" qualifier that seq §6.4/§7 pin (unattended run reads overview gate table first).
- PR-centric mechanics (ONE PR, branch-protection, PR-description note) vs declared local merge --no-ff → push; put coordination note in merge commit message.
- Pair wall-clock realistically ~13-14.5h not 12 (P6 serial tail; P4 waits on P1+P2; T10 0.5h smoke unscheduled) — direction unchanged (pair fits overnight, serial doesn't).
- Stale headers: seq :14-15 input line-counts (892/736 vs actual 1126/772); fe-plan :13 "BE plan NOT YET WRITTEN"; fe-plan :765 "755 lines" vs 754; AC-P7 cites nonexistent §4.7; be-plan :39 §2 schemas.py vs §5.1 snapshot_schemas.py; orphan comment settings.component.ts:103-106 outside all deletion ranges (hyphenated "snapshot-create" missed by camelCase grep — add to T12); §4.3 dangling-ref table misfiles html lines under ts label (grep claim itself re-verified TRUE); §4.1 file-manifest arithmetic (8 vs 10 vs 11 files; missing table scss row); §3.1 stale editorial sentence; phantom spec sibling §6.3 schedule-detail-drawer.component.spec.ts (real: job-detail-drawer or skill-usage-table spec); R5 "MatSidenav" label (conclusion right); data-test hooks (~12 asserted by e2e) created by no P3-P5 task; metrics outer try/except is dead belt (service already fail-soft internally); §8.2 case 8 may duplicate existing TestTagFilterPGDriftPin; "44 UNIQUE" ambiguous pytest-item vs unique-behavior (§8.6 vs §8.3 overlap statement); harness needs 2 trivial fixtures (SnapshotRepository/SnapshotMetricsService) beyond listed; create_default_system_project → actual ensure_system_default_project (repository.py:242); __init__ row anchor off by one (after :28 not :27); §4.1 422-shape wording vs §7 custom error body; plan files untracked — commit before implementation (branch-surgery convention).
- Independence wrinkle: iter-3 worker B read the approver tracking file despite cold prompt (self-reported). Findings independently re-verified against artifact+code with citations; bias direction if anything anti-approval. Recorded for transparency; no verdict impact.

### Aggregation decisions
- B1+B2 merged (same import block/fix locus). C's note-level e2e-path + pytest-cwd findings merged into A2/A3 (A's blocking variants kept as most specific). No downgrades (no style/duplicate grounds). No upgrades, no new blocking.

### FINAL STATE: ESCALATED (3 rejections: 001, 002, 003)
Per 3-iteration cap: returned REJECTED with max-iterations note; Leader presents full tracking history to user. Workers' aggregate estimate for the 8 fixes: ~1-2 hours of plan edits (A: "≈10 minutes" for its 3; B: "4 one-liners"; C: "under an hour") — but that judgment is the Leader's/user's lane now, not the approver's.
