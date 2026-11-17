# Skill-System Defects — Light Plan (C-lane implementation)

Commissioner: leader (Emperor-authorized C-lane). Implementer: coder.
Worktree: `/home/nea/ensemble-src-wt-skillfix` @ branch `feature/skill-system-defects`, base `0181dadbf` (= origin/latest).
Scope: defects 1, 2+C2, 3(C1), 5, 7 + supplement items (a)–(f) + decision-package defect D.
Out of scope: 4, 6, 8, 9.

## DECISION PACKAGE (adjudicated 2026-11-17, wanderer map + orchestrator overlay) — recorded as DECISIONS

- **Q1 (winner policy):** E-6 rule — newer LINEAGE GENERATION wins, by
  generation number, NEVER timestamps; never crown a verified-defective
  newer member (corrective-first via the seam where applicable, else the
  non-defective member). The defective-divert input is EXPLICIT
  (operator/manifest disposition), never sweep-judgment. ONE mechanism
  serves C2 + defect 2 + the pending-wiring backlog: a
  `skill_ab_inactivity_sweep` MaintenanceService companion (house
  pattern: interval + idle-gated + never-raising), registered beside
  `skill_metric_scan` / `skill_orphan_sweep` in `daemon/manager.py`
  (~:4749-4766 AS FOUND, re-derived — manager drifted vs the map) —
  the hunk carries a sanctioned `decision` marker comment per the
  drift-guard constraint.
- **Q2 (C1 placement):** EXTEND the existing REST route
  `POST /api/skills/{id}/ab-test/resolve` (`daemon/routers/skills.py:1607`
  AS FOUND) with a sanctioned explicit-winner mode wired to
  `SkillStaleDeactivator.resolve_pair`
  (`daemon/services/skill_deactivation.py:1346` AS FOUND — verified DEAD
  CODE: zero importers). Sanctioned mode REQUIRES explicit winner +
  reason/provenance; the sample-size gate is bypassed ONLY thus; the
  announcement record goes through the capture-observability sink; the
  normal auto-resolution path stays gated as today. resolve_pair gains
  the winner-side completion per terminal-state spec (d) (winner →
  group cleared + status='active') — its current apply arm resolves
  the test row and deactivates losers but leaves the winner's
  `ab_test_group`/`status` untouched. ALSO: fix the silent-skip
  observability — the `skill_evolve` A/B-guard refusal returns a plain
  dict, so the job ledger records SUCCESS with zero mutation;
  guard-refusals must be distinguishable outcomes (skipped/refused).
- **Q3 (C3 historical nulls):** LEAVE them (fix-forward only; the sink
  is schema-free JSONL with zero production readers; the correlation
  limit is documented in the sink docstring). Fix scope is WIDER than
  filed: SIX null-producing writers (metrics wrapper, tools seam,
  dispatcher cap-check-error, `skill_tools.py` fix-dispatch-fault, AND
  `job_processor.py` execution-refusal records :820/:869 — execution
  refusals mint too). Correct the false `build_run_record` docstring
  carve-outs; extend the m2 pin to cover the previously-null paths for
  real (not only the already-correct dispatcher paths).

All anchors above were **grep-verified at plan time (2026-11-17)** against this worktree.
Anchors are leads, not contracts; implementation re-greps every site.

---

## D1 — description-freeze on FIX generations

**Root cause AS FOUND:** `daemon/services/skill_evolution_service.py:540` —
`_evolve_fix` creates the new generation with `description=skill.description`
(parent verbatim). The direction / fix payload never influences the description,
so a scope-change FIX generation is undiscoverable via `skill_search` (both BM25
and embedding index the description). **Decision package: BOTH sites** — the
same-shape copy in `_evolve_derived` at `:643` is IN scope. No description
field exists in any FIX/DERIVED payload today — the D5 edit-script payload adds
the description contract (JSON `"description"` key; the pre-existing
`_RE_KV_DESC` KV-regex at `:154` stays CAPTURED-only — different response
format, not shared machinery). Load-bearing for discovery: the BM25 corpus, the
LLM rerank (name+description only), and the trigger-query builder all consume
the description.

**Fix approach:**
- The FIX/DERIVED evolution payload carries an optional `description` field
  (D5's edit-script protocol: the LLM emits a new one-line description when
  the direction changes scope).
- `_evolve_fix` AND `_evolve_derived` use
  `_resolve_fix_description(parent_description, payload_description)`:
  payload description when present and non-blank (sanitized, one line, capped
  512); parent description as fallback when absent/blank.
- Same path for first-evolution and **corrective re-FIXes** (supplement (f)) —
  the post-promote corrective FIXes that re-introduce improved content get
  correct descriptions by construction.

**Test plan:** FIX with a scope-changing payload description → new row carries
it; DERIVED likewise; no description in payload → parent fallback; corrective
FIX (D2a path) → same behavior.

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

**Surfaces (DECIDED Q1 supersedes the no-manager-touch stance):**
- Admin HTTP: the EXISTING `POST /api/skills/{id}/ab-test/resolve` route
  extended with the sanctioned explicit-winner mode (Q2) + a stalled-pairs
  listing endpoint (`GET /api/skills/ab-tests/stalled`).
- Keeper tool lane: `skill_resolve_ab` gains optional `basis` /
  `defective_arm_id`; `skill_evolve` gains `corrective`.
- **`skill_ab_inactivity_sweep` MaintenanceService companion** (Q1): house
  pattern — interval knob + idle-gated + never-raising; registered beside
  `skill_metric_scan` / `skill_orphan_sweep` in `daemon/manager.py`
  (~:4749-4766 AS FOUND). The SWEEP detects stalled pairs (predicate:
  comparisons == 0 ∧ age ≥ `ab_stall_timeout_days`) and SURFACES them
  (anomaly rows; parked resolution plans) — it resolves ONLY pairs whose
  E-6 winner is unambiguous AND never diverts on sweep-judgment alone
  (defective-divert requires an explicit operator/manifest disposition).
  The manager.py hunk carries a sanctioned `decision` marker comment
  (drift-guard constraint).

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

**Placement (DECIDED Q2):** EXTEND the existing REST route
`POST /api/skills/{id}/ab-test/resolve` (`daemon/routers/skills.py:1607` AS
FOUND) with a **sanctioned explicit-winner mode**, wired to
`SkillStaleDeactivator.resolve_pair`
(`daemon/services/skill_deactivation.py:1346` AS FOUND; verified DEAD CODE —
zero importers; it already carries the receipt-BEFORE-flip invariant,
receipt rotation, run_id, and `--reactivate` reversal).

- Sanctioned mode contract: explicit `winner_id` + REQUIRED
  `reason`/provenance (rejected with 400 when missing); the sample-size
  gate is bypassed ONLY in this mode; the normal auto-resolution path
  stays gated exactly as today (2026-10-22 confinement intact).
- resolve_pair gains the winner-side completion per terminal-state spec
  (d): winner → `ab_test_group=None`, `status='active'` (mirrors the
  service's forced-winner side effects; its current apply arm stops
  short). Loser side already matches (d) exactly via the sanctioned
  `deactivate_skill` writer.
- Announcement: capture-observability sink record (minted run_id,
  stage='resolution') + `emit_lane_anomaly` — append-only, auditable.
- The E-6 winner rule (D2/C2) supplies the default winner when the
  caller asks the sanctioned mode to decide (stalled basis); an explicit
  winner overrides. Never sweeps a verified-defective newer member to
  victory — defective-divert requires an EXPLICIT disposition.
- **Silent-skip observability (DECIDED Q2):** the `skill_evolve`
  A/B-guard refusal currently returns a plain dict → the job ledger
  records SUCCESS with zero mutation. Guard-refusals become
  distinguishable outcomes (envelope carries `skipped`/`refused`
  semantics + a sink refusal record), never plain success.

The D2/C2 service machinery (E-6 stall resolver + corrective FIX path)
remains as designed below — Q2 wires the REST surface through
resolve_pair; the service method and resolve_pair share the terminal-state
writer semantics.

**CRITICAL QUESTION — pre-promote applicability (answered):**
The terminal state uses ONLY lifecycle states and columns that the CURRENT live
binary (v0.31.0) already respects: single-arm serving
(`_select_ab_variant` returns the original skill unchanged when <2 active
variants — no A/B routing on a resolved pair), resolved `skill_ab_tests` rows
are inert to the resolver, `is_active=false` rows are unserved. **A retirement
applied to live data pre-promote IS honored by the current live binary** — it is
the same state the existing forced-winner path writes. The NEW machinery
(sanctioned mode / E-6 / corrective param / sweep) is new-binary-only but
consumes and writes only existing schema — **no migration, no new table**.

**Pending-wiring backlog table:** no trace in `daemon/migrations/versions/`
nor in code ⇒ the data lives only in the live DB. Designed against the
`SkillABTest` schema; consumption of the live rows is **post-promote** (the
route/resolver accepts group ids one at a time — no live reads from this lane).

**Test plan:** route-level sanctioned resolve (winner+reason required;
400 without reason; sample-size bypass ONLY sanctioned); resolve_pair
winner-side completion ((d) exact); reversal via receipt reactivate;
silent-skip observability pin (guard refusal ≠ ledger SUCCESS shape).

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
  exact-anchor matching only (anchor must occur EXACTLY ONCE; ambiguous
  anchors rejected, never guessed); each rejected op is skipped and reported;
  the body outside edited regions carries the parent **verbatim by
  construction**, with the FULL parent body in the prompt (3000-char
  truncation removed from the evolution path entirely).
- Zero applicable ops + no description op ⇒ `ValueError` (fail loud — no
  silent no-op generation, no destructive fallback regen).
- **Full-body LLM regeneration ONLY on explicit direction** — documented
  marker phrases (see `_FULL_REGEN_MARKER_RE`); marker ⇒ legacy
  `_generate_evolved_content` path, with the FULL parent body in the prompt
  (its `[:3000]` truncation is removed — a rewrite must see what it
  rewrites). The marker is the escape hatch; no new feature switch.
- **Fault-guard (decision package):** the `_call_llm` ""-fault path
  (`"(no content generated)"` at the legacy return) can never become
  storable content — the FIX/DERIVED flow raises on it instead of creating
  a broken row.
- **Content gate (decision package):** FIX/DERIVED get a carry-over gate
  (CAPTURED already has `_captured_content_gate`) asserting carry-over
  invariants: parent sections that the direction does NOT name must survive
  byte-identical — enforced structurally by the surgical path, and asserted
  as a belt-and-braces gate before the row is created (parent-section
  presence check).
- Non-directed default-path behavior stays as close to unchanged as the fix
  allows: A/B wiring, lineage rows, embedding refresh, cooldowns — unchanged;
  only the content-production seam changes.

**Test plan:** parent bodies >8000 and >3000 chars → untouched sections
byte-identical after surgical FIX (the 4-invariant-regression shape);
anchor-miss rejected; total-parse-failure raises; full-regen marker takes the
legacy path (with full parent body in prompt); no-marker never full-regenerates;
""-fault raises (never storable); carry-over gate trips on a hypothetical
destructive result.

**Serving-behavior impact:** FIX/DERIVED generations stop losing unrelated
invariants by construction. Highest-risk change of the lane — mitigated by the
fail-loud contract and per-defect tests.

---

## C3 — refused-admission records mint `run_id: null`

**Root cause AS FOUND** (SIX producers per DECIDED Q3; `build_run_record` in
`skill_capture_observability.py` documents the minted-before-the-gate
contract these sites violate):
1. `daemon/services/skill_metrics_service.py` — metrics-lane wrapper
   `_record_capture_lane_off_refusal` (accepts NO `run_id`) → every gate-0
   capture refusal on the production path records `run_id: null`.
2. `daemon/tools/skill_evolution_tools.py` — tools-lane
   `record_capture_lane_disabled_refusal` call passes no `run_id`.
3. `daemon/services/skill_job_dispatcher.py` — `cap_check_error` admission
   record omits `run_id`.
4. `daemon/tools/skill_tools.py` — skill_fix dispatch-fault
   `stage="admission"` record passes no `run_id`.
5. `daemon/services/job_processor.py:820` AS FOUND — queued
   execution-refusal record (no run id).
6. `daemon/services/job_processor.py:869` AS FOUND — active
   execution-refusal record (no run id).

(`enqueue_evolution` and `enqueue_capture` mint correctly — verified.)

**Fix approach (DECIDED Q3):** ALL SIX sites mint/pass a run id (the
dispatcher gate pattern is the reference). The `build_run_record` docstring
is corrected — no false carve-outs: after the fix, an admission OR
execution refusal record always carries a run id. **Historical null rows
LEAVE AS-IS** (fix-forward only — schema-free JSONL, zero production
readers; the correlation limit is documented in the sink docstring). The m2
pin is EXTENDED to cover the previously-null paths for real (metrics seam,
tools seam, cap-check-error, dispatch-fault, both execution-refusal sites)
— the existing m2 pin only exercised the two already-correct dispatcher
paths.

**Test plan:** each of the six refusal paths emits a record with non-null
run id; distinct run ids across two consecutive refusals; docstring pin.

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

## Defect D (decision package, IN scope) — psycopg2 gap = missing URL rewrite

**AS FOUND:** bare `postgresql://` URLs make SQLAlchemy default to the
psycopg2 driver at `create_engine` — psycopg2 is NOT in pyproject; the
daemon uses `postgresql+psycopg` (`daemon/repositories/factory.py:389`
AS FOUND per map). Two audit scripts build engines from operator-supplied
`--db-url` verbatim:
- `scripts/audit/skill_counter_reconcile.py` (~:496 AS FOUND — guarded by
  a try/except, but the failure message is the psycopg2 gap, not the
  intended liveness error);
- `scripts/audit/skill_stale_deactivate.py:200` AS FOUND — UNGUARDED
  (raw traceback).

**Fix approach:** normalize `postgresql://` → `postgresql+psycopg://`
before `create_engine` in BOTH scripts (the 4-line idiom at
`tests/integration/test_maintenance_checkpoint_cleanup_api.py:112-117`
AS FOUND: `_sync_pg_url`). Correct the runbook's prescribed broken forms
(`.agents/shared/planning/skill-system-revival/phase2-runbook.md:636/:759/:1053`
AS FOUND — all three prescribe bare `postgresql://`). One e2e PG pin calls
`module.main(["--db-url", "postgresql+psycopg://…"])` against the
disposable/dev PG (the bare form would fail with the psycopg2 gap; the
pin proves the rewrite makes BOTH forms work).

**Test plan:** unit pin on the rewrite helper (both forms accepted);
e2e pin via `module.main` on PG; runbook grep-clean of bare forms.

**Serving-behavior impact:** operator tooling only.

## Item (e) — capture dark-path RECONCILIATION (decision-package map)

Map (verified): the trigger engine has ZERO capture references; capture
originates at the task-completion hook —
`record_task_completion` (`skill_metrics_service.py:358` AS FOUND; the
`if not injected_ids: return 0` early-return at `:471-472` means the
eligibility gate runs ONLY for instances with a skill INJECTED) →
`_check_capture_eligibility` (`:567`; gates: kill-switch `:663`,
service-wired `:692`, task_succeeded `:698`, injection `:715-731`,
genuinely-evolved `:733+`) → `enqueue_capture` (the ONLY production call
site `:814`) → dispatcher gate (`:1126` AS FOUND map). The manual tool
`skill_execute_capture` BYPASSES `enqueue_capture` (inline LLM + insert,
no admission record) — it cannot serve as the e2e proof seam.

Reconciliation to produce (code-level, no live reads): why the recent
window shows ZERO gate evaluations while the historical sink holds
`capture_lane_off` null records (source=skill_metrics_service ≈65%) —
candidate causes to discriminate: (i) kill-switch state; (ii) eligibility
conjunct mismatch vs the census's 499 candidates; (iii) the
`:471-472` early-return (no injections ⇒ no gate). Deliverable: the
reasoning pinned in this plan + a NEW e2e capture-admission test wired
through the REAL enqueue path (`record_task_completion` → … →
`enqueue_capture`) on the dev DB — NOT through the bypassing tool.

## Item (e) — capture dark-path RECONCILIATION (code-level, DELIVERED)

The chain verified in code (no live reads): usage rows flowing ⇒
``record_task_completion`` ran ⇒ ``_check_capture_eligibility`` was
CALLED (it runs unconditionally after usage rows, soft-fail) ⇒ Gate 0
evaluated. Reconciled candidate causes of the live darkness, in
likelihood order:

1. **Kill-switch default OFF + env unset (dominant, structural).**
   ``ENSEMBLE_SKILL_CAPTURE_ENABLED`` landed OFF by default
   (b96c0d671, 2026-09-17; first shipped v0.13.10 tagged 2026-09-22)
   and the deployment env does not set it (the dev worktree .env sets
   NEITHER gate — verified). With the env unset, Gate 0 refuses EVERY
   candidate before any evaluation: the trigger is dark BY DESIGN,
   not by regression. The observed stop date (2026-09-01) PREDATES
   the flag's first release — the initial stop had an earlier cause
   not derivable from the repo alone (eligible-population predicates
   or the v0.13.x promote cadence); disclosed as a precise
   uncertainty, not half-fixed.
2. **Invalid-env soft-fail darkness (CLOSED by this lane).** A
   misconfigured value (ValueError from the resolver) was contained
   by the ``record_task_completion`` soft-fail boundary with ONE
   warning line and ZERO sink residue — invisible in both surfaces.
   FIXED: the boundary now leaves a ``capture_gate_error`` sink
   record + counter (raise contract unchanged; the existing
   containment pins stay green).
3. **Historical sink evidence is CONSISTENT with Gate 0 firing**: the
   ``capture_lane_off`` null-run-id records (source=
   skill_metrics_service ≈65%) prove the metrics seam refused — the
   pre-C3 records were uncorrelatable by run id, now fixed. A recent
   window with ZERO such records while usage rows flow would point at
   cause 2 or sink-destination drift on the deployment — those are
   live-side discriminations (out of this lane).

**The durable proof:** a NEW e2e capture-admission test
(``tests/integration/test_skill_capture_admission_e2e_dev.py`) drives
the REAL chain on the DEV DB with the gate ON — real skill/queue/job/
lock repositories, real ``JobQueueService`` + ``SkillJobDispatcher``,
``system_parallel_queue`` routing, ``capture_run_id`` in the job
metadata correlated with the sink's ADMITTED record. The chain is
proven end-to-end; the live darkness is an operator env decision
(``ENSEMBLE_SKILL_CAPTURE_ENABLED=1``), outside this commission's
no-flip constraint.

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
