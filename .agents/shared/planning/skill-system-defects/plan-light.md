# Skill-System Defects — Light Plan (C-lane implementation)

Commissioner: leader (Emperor-authorized C-lane). Implementer: coder.
Worktree: `/home/nea/ensemble-src-wt-skillfix` @ branch `feature/skill-system-defects`, base `0181dadbf` (= origin/latest).
Scope: defects 1, 2+C2, 3(C1), 5, 7 + supplement items (a)–(f). Out of scope: 4, 6, 8, 9.

All anchors below were **grep-verified at plan time (2026-11-17)** against this worktree.
Anchors are leads, not contracts; implementation re-greps every site.

---

## D1 — description-freeze on FIX generations

**Root cause AS FOUND:** `daemon/services/skill_evolution_service.py:540` —
`_evolve_fix` creates the new generation with `description=skill.description`
(parent verbatim). The direction / fix payload never influences the description,
so a scope-change FIX generation is undiscoverable via `skill_search` (both BM25
and embedding index the description). Same-shape copy exists in `_evolve_derived`
at `:643` — **left untouched** (out of filing scope; disclosed as follow-up).

**Fix approach:**
- The FIX evolution payload gains an optional `description` field (see D5's
  edit-script protocol: the LLM may emit a new one-line description when the
  direction changes scope).
- `_evolve_fix` uses `_resolve_fix_description(parent_description, payload_description)`:
  payload description when present and non-blank (sanitized, one line, capped);
  parent description as fallback when absent/blank.
- This is the same path for first-evolution FIXes and **corrective re-FIXes**
  (supplement (f)): the post-promote corrective FIXes that re-introduce improved
  content get correct descriptions by construction — no special-casing.

**Test plan:** FIX with a scope-changing payload description → new row carries it;
FIX with no description in payload → parent fallback; corrective FIX (D2a path)
→ same behavior.

**Serving-behavior impact:** better `skill_search` discoverability for new
generations; no serving-path logic change.

---

## D2 + C2 — ONE sanctioned resolution mechanism (self-deadlock + zero-traffic stall)

**Root causes AS FOUND:**
1. **FIX-path self-deadlock** — `skill_evolution_service.py:512-522`: `_evolve_fix`
   refuses `status == 'ab_testing'` targets, while the same call *sets* the new
   generation `status='ab_testing'` at `:547`. Every FIX-born generation is
   therefore un-fixable (observed 09c2746f; supplement (c): dispatches on pair
   members are ADMITTED through the skill_fix seam but DIE AT EXECUTION here —
   job 2b18c0b6 failed; db9a484f/238be875 skipped).
2. **Zero-traffic stall** — `check_ab_test_resolution` returns `needs_more_data`
   at `:1472-1479` whenever `comparisons < ab_sample_size`, BEFORE the
   `extension_count` bump at `:1546`. `comparisons` bumps ONLY on genuine
   injections (`skill_injection_service.py` `_select_ab_variant` →
   `increment_comparison`, fires only when ≥2 active variants route), so a pair
   that never receives traffic stays `is_active` on both arms forever.

**Fix approach — one mechanism, two sanctioned bases:**

New service method `resolve_ab_test_sanctioned(ab_test_group, *, basis, defective_arm_id=None, actor)`:

- `basis="stalled"` — stall predicate enforced in code:
  `comparisons == 0` AND pair age ≥ `ab_stall_timeout_days` (new
  `SkillEvolutionConfig.ab_stall_timeout_days`, default `7.0`; age gate is the
  *trigger* condition only — winner selection NEVER uses timestamps).
  Winner by **E-6**: the member with the higher `generation` number wins;
  generation tie ⇒ **park + surface** (anomaly row, no mutation,
  `reason='stalled_generation_tie_parked'`).
- `basis="defective_arm"` (C1 retirement) — caller names the verified-defective
  member (`defective_arm_id`); winner = the OTHER member (E-6: never crown a
  verified-defective newer member).
- **Terminal state EXACTLY per supplement (d)** (verified identical to the
  existing forced-winner side effects at `:1449-1463` / Path 2/3):
  loser → `deactivate` (`status='inactive'`, `is_active=false`, `ab_test_group`
  RETAINED — `SkillRepository.deactivate` at `repositories/skill/repository.py:383-396`
  already matches); winner → `ab_test_group=None`, `status='active'`; pair row →
  `SkillABTestRepository.resolve` stamps `winner_skill_id` + `resolved_at`.
- **Auditable:** minted-run observability record (`stage='resolution'`) +
  `emit_lane_anomaly` row on every resolution and every park.
- **Never fires on normal pairs:** `stalled` basis is gated by the stall
  predicate (comparisons==0 ⇒ by definition not traffic-bearing);
  `defective_arm` basis requires an explicit operator assertion. The normal
  `check_ab_test_resolution` decision tree is untouched.

**Self-deadlock fix (execution path, per supplement (c)):**
`evolve_skill(..., corrective=True)` — the sanctioned corrective FIX on an
`ab_testing` member (only reachable through the corrective param):
1. Validate the target is a live pair member (`ab_test_group` + `ab_testing`).
2. Create the corrected generation: FULL parent body carried server-side with
   surgical edits (D5) + description from payload (D1); new row is
   `status='active'`, `is_active=True`, **no new A/B group** (it stands on its
   own; E-6 "fix first, then resolve").
3. Resolve the OLD pair to the non-defective member (the target's sibling):
   target deactivated per (d), sibling crowned, pair row resolved.
4. Non-corrective FIX on an `ab_testing` target keeps the existing refusal —
   the guard's normal-shape semantics are preserved.

**Surfaces (all lane-gated like the existing tools):**
- Admin HTTP: `POST /api/skills/ab-tests/resolve-sanctioned` (operator-visible)
  + `GET /api/skills/ab-tests/stalled` (sweep listing; supports the post-promote
  consumption of the 39-pair pending-wiring set one group at a time).
- Keeper tool lane: `skill_resolve_ab` gains optional `basis` /
  `defective_arm_id`; `skill_evolve` gains `corrective`.
- **No manager.py changes** (drift-guard constraint). No autonomous timer: the
  sweep is operator/Ari-invoked — with both skill lanes PAUSED, an autonomous
  mutator would be the wrong default; the endpoint + anomaly rows are the
  "operator-visible, auditable" surface. Disclosed as a deliberate design bound.

**Test plan:** stalled-basis resolve (E-6 newer-gen winner + exact (d) terminal
state); refuse when comparisons>0; refuse when age<threshold; generation-tie →
park+surface (no mutation, anomaly row); defective-arm basis crowns the sibling;
corrective FIX creates corrected gen + resolves pair (target inactive, sibling
active+cleared, corrected gen active); non-corrective FIX on ab_testing still
refused; HTTP route contract (400s).

**Serving-behavior impact:** none on the default path; new mutation surface is
explicit-only. Reversibility: deactivation is soft (rows never deleted); manual
revert = reactivate + clear `resolved_at` (runbook note, no auto-undo).

---

## C1 — sanctioned corrective/retirement for verified-defective arms

Covered by the D2/C2 mechanism above (execution-path fix + `defective_arm`
basis + surfaces). Supplement (c) honored: the fix is at EXECUTION
(`_evolve_fix` corrective path), not at admission — the dispatcher seam already
admits FIX dispatches on pair members.

**CRITICAL QUESTION — pre-promote applicability (answered):**
The terminal state uses ONLY lifecycle states and columns that the CURRENT live
binary (v0.31.0) already respects: single-arm serving
(`_select_ab_variant` returns the original skill unchanged when <2 active
variants — no A/B routing on a resolved pair), resolved `skill_ab_tests` rows
are inert to the resolver, `is_active=false` rows are unserved. **A retirement
applied to live data pre-promote IS honored by the current live binary** — it is
the same state the existing forced-winner path writes. The NEW machinery
(basis/E-6/corrective param/sweep endpoint) is new-binary-only but consumes and
writes only existing schema — **no migration, no new table**.

**39-pair pending-wiring table:** no trace in `daemon/migrations/versions/`
(119 files checked by grep) nor in code (`pending_wiring`/`pending_ab` greps
empty) ⇒ the data lives only in the live DB. Per supplement (d): designed
against the `SkillABTest` schema (`ab_test_group`, `skill_id_old/new`,
`comparisons`, `extension_count`, `created_at`, `resolved_at`,
`winner_skill_id`); consumption of the live rows is **post-promote** (the
sweep/resolver accepts group ids one at a time — no live reads from this lane).

---

## D5 — destructive regeneration (supplement (a): server-side full-body carry)

**Root cause AS FOUND (two truncation sites, same mechanical-enabler class):**
1. `skill_evolution_service.py:2498` — the evolution prompt itself truncates the
   parent body to **3000 chars** (`[:3000]`) and instructs the LLM to "Produce
   an improved version of the skill content" (unconditional full-body regen).
   The evolution path never routes through `skill_view` — it has its own
   truncation.
2. `daemon/tools/skill_tools.py:1224-1227` — `skill_view` display cap 8000
   chars (the agent-facing view the keeper reads; the observed 12,300-char
   parent → tail invisible to the LLM-driven flow). This display cap stays
   (display-only); the FIX is that evolution no longer depends on any
   truncated view.

**Fix approach:**
- New **surgical edit-script protocol** (default for FIX and DERIVED — both
  were filed): the evolution LLM returns a strict JSON document
  `{"description": str|null, "edits": [{"op": "replace|delete|insert_after",
  "anchor": "<exact verbatim text>", "replacement"/"text": str}]}`.
- `_apply_surgical_edits(parent_content, edits)` — pure server-side applier:
  exact-anchor matching only (no fuzzy); each op whose anchor does not match
  exactly is REJECTED and logged (count surfaced in the result payload); the
  body outside edited regions carries the parent **verbatim by construction**,
  with the FULL parent body in the prompt (3000-char truncation removed from
  the evolution path entirely).
- Zero applicable ops + no description op ⇒ `ValueError` (fail loud — no
  silent no-op generation, no destructive fallback regen).
- **Full-body LLM regeneration ONLY on explicit direction** — documented
  marker phrases in the direction (e.g. "full-body regeneration", "regenerate
  the entire body", "full rewrite"); marker ⇒ legacy `_generate_evolved_content`
  path, but now with the FULL parent body in the prompt. The marker list is
  documented in the method docstring and plan-light; it is the escape hatch
  (no new feature switch — both skill gates stay untouched).
- Non-directed default-path behavior stays as close to unchanged as the fix
  allows: A/B wiring, lineage rows, embedding refresh, cooldowns — unchanged;
  only the content-production seam changes.

**Test plan:** parent bodies >8000 and >3000 chars → untouched sections
byte-identical after surgical FIX (the 4-invariant-regression shape);
anchor-miss rejected; total-parse-failure raises; full-regen marker takes the
legacy path (with full parent body in prompt); no-marker never full-regenerates.

**Serving-behavior impact:** FIX/DERIVED generations stop losing unrelated
invariants by construction. Highest-risk change of the lane — mitigated by the
fail-loud contract and per-defect tests.

---

## C3 — refused-admission records mint `run_id: null`

**Root cause AS FOUND** (four producers; `build_run_record` at
`skill_capture_observability.py:825-863` already documents the
minted-before-the-gate contract that these sites violate):
1. `daemon/services/skill_metrics_service.py:140-186` — metrics-lane wrapper
   `_record_capture_lane_off_refusal` accepts NO `run_id` at all → every
   gate-0 capture refusal on the production path records `run_id: null`.
2. `daemon/tools/skill_evolution_tools.py:539` — tools-lane
   `record_capture_lane_disabled_refusal` call passes no `run_id`.
3. `daemon/services/skill_job_dispatcher.py:789` — `cap_check_error` admission
   record omits `run_id` (the parameter exists on `_check_fix_burst_cap`).
4. `daemon/tools/skill_tools.py:410` — skill_fix dispatch-fault
   `stage="admission"` record passes no `run_id`.

(`enqueue_evolution` and `enqueue_capture` mint correctly — verified.)

**Fix approach:** mint `observability.new_run_id()` before each gate at all
four sites and thread it through. Tighten the `build_run_record` docstring:
`stage="admission"` records MUST carry a run id (test-pinned).

**Historical null rows — decision: STAY AS-IS.** The sink is an append-only
JSONL day-file log (`skill_capture_observability` sink), not a DB table: run
ids that were never minted cannot be reconstructed, and rewriting history in
an audit log would be worse than the gap. Interpretive rule for reading old
rows: `run_id: null` on a pre-fix `stage="admission"` record identifies the
legacy producers listed above. No backfill.

**Test plan:** each of the four refusal paths emits a record with non-null
run_id; distinct run ids across two consecutive refusals; docstring pin.

**Serving-behavior impact:** observability only.

---

## D7 — skill_view lineage renders blank (supplement (b): IN SCOPE, cheap)

**Root cause AS FOUND:** key-schema mismatch. `SkillLineage.to_dict()`
(`repositories/skill/models.py:293-300`) emits
`{skill_id, parent_skill_id, change_summary, content_diff, created_at}`;
the `skill_view` DB-branch renderer at `daemon/tools/skill_tools.py:1237-1249`
reads `from_skill_id` (parents) / `to_skill_id` (children) / `relation_type` —
NONE of which exist in the emitted dict ⇒ blank lineage lines on every skill
with lineage (fresh generations render an empty "## Lineage" body; anomaly
c24f4d39). No other consumer reads the phantom keys (grepped).

**Fix approach:** render the real keys — parents:
`- parent_skill_id=… change_summary=…`; children: `- skill_id=… change_summary=…`.

**Test plan:** unit test on the tool renderer with a real lineage row dict:
non-empty parent/child lines; fresh-generation shape (92f26d3e class).

**Serving-behavior impact:** display-only.

---

## Item (e) — capture-enqueue upstream darkness: diagnosis + e2e admission test

**AS FOUND (repo-side):**
- `ENSEMBLE_SKILL_CAPTURE_ENABLED` kill-switch introduced by `b96c0d671`
  (2026-09-17), **default OFF**, per-call env resolution
  (`skill_job_dispatcher.py` `_resolve_capture_enabled`). The dev worktree `.env`
  sets NEITHER skill-gate env (verified). With the env unset, Gate 0 of
  `SkillMetricsService._check_capture_eligibility`
  (`skill_metrics_service.py:667` area) refuses EVERY capture candidate before
  any evaluation — the enqueue trigger is structurally dark unless the operator
  sets the env. This is a deliberate 2026-09-17 confinement design
  (owner-authorized flag), NOT a regression.
- The flag first shipped in release `v0.13.10` (tagged 2026-09-22). The
  observed live stop date (2026-09-01) PREDATES the flag — the initial stop
  had an earlier/additional cause not derivable from the repo alone (candidates:
  the eligible-side population predicates on live data — skill_injection flag,
  complexity thresholds, genuinely-evolved attribution — or the v0.13.x promote
  cadence). **Disclosed as a precise uncertainty; not half-fixed.**
- The gate-0 refusal IS counted and recorded (metrics seam), but at DEBUG log
  level on the dispatcher's own gate and — pre-C3 — with `run_id: null`, which
  made the darkness quiet and uncorrelatable. C3's fix restores correlation.

**Wiring-level actions in this lane (contained):**
1. C3 run_id fix (above) — refusals become correlatable.
2. **New e2e capture-admission test on the DEV DB** (env-fenced, POSTGRES_DB=
   ensemble_dev, per-variable scrub, ENSEMBLE_SELF_ENV=dev, worktree venv):
   full chain `record_task_completion` → `_check_capture_eligibility` (gate ON
   via monkeypatched env) → `check_and_capture` → `enqueue_capture` — proving
   the admission path works end-to-end when the gate is ON, independent of
   live behavior.
3. **DISCLOSURE (not fixable from this lane):** with the flag default OFF and
   the deployment env unset, capture stays dark BY DESIGN. Turning it on is an
   operator env decision (`ENSEMBLE_SKILL_CAPTURE_ENABLED=1`) — outside my
   commission (no feature-switch flips). The 2026-10-22 review minor (d)
   deliberately keeps lane-off refusals at INFO; not fought.

---

## Defect cross-check — supplement items (a)–(f)

- (a) D5 carries the FULL parent body SERVER-SIDE (own 3000-truncation removed;
  never routed through the 8000-cap view). ✔
- (b) D7 in scope, fixed at the renderer. ✔
- (c) C1 fixes the EXECUTION path (corrective `_evolve_fix` + sanctioned
  resolver), not admission. ✔
- (d) Terminal-state spec matches existing repo machinery exactly (verified:
  `deactivate` = inactive+is_active-false+group-retained; winner clear+active;
  `resolve` stamps winner+resolved_at). Wiring accepts per-group resolution of
  the 39-pair live-DB set post-promote. ✔
- (e) Diagnosis + e2e test + disclosure above. ✔
- (f) D1's description path is basis-independent — corrective re-FIXes get
  payload descriptions on the same code path. ✔

## E-6 encoding (binding)

Winner rule in `resolve_ab_test_sanctioned` and the corrective path: **newer
LINEAGE GENERATION NUMBER wins; NEVER timestamps** (timestamps appear only in
the stall-age TRIGGER gate, never in winner selection). A verified-defective
newer member is never crowned: corrective FIX first (D2a path), else resolve to
the non-defective member (defective_arm basis).

## Process constraints honored

Per-defect commits (`<type>: <desc>`); this plan committed first as
`docs(skill): …`. No version bump / CHANGELOG / release work (v0.31.1 is
giter's). Both skill gates stay ON/untouched. No live-DB contact (reads
included). No manager.py hunks (design avoids it). No new SQLModel tables ⇒ no
migration files. Tests run with explicit paths, per-variable POSTGRES_*
scrubbing, ENSEMBLE_SELF_ENV=dev, worktree `.venv` only.
