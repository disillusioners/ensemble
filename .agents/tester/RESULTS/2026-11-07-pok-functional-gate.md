# POK (Project Operating Knowledge) — FUNCTIONAL GATE REPORT

**Date:** 2026-11-07
**Commission:** POK implementation lane functional gate (Emperor GO 2026-11-07)
**Lane:** `feature/project-operating-knowledge` @ `c4c2293fa` (frozen tip), worktree `/home/nea/ensemble-src-wt-pok`, TRUE BASE `6b571771d` (32 lane commits)
**Gate executor:** tester (dispatch-only); 8 worker instances (recon, views, cap, seed, supersede, packs, boot, regression)
**Design set (frozen):** `.agents/shared/planning/project-operating-knowledge/` (plan-overview v3.4, architecture-recommendation §4.5.1/§4.6 amended, decisions v3.4 ER-1, trust-tier-addendum, implementation-readiness, seed-payload-llmproxy)

---

## VERDICT: **FAIL — two legs (L1/AC1, L10). All other legs PASS or PASS-WITH-DISCLOSURES.**

**L1:** the acceptance walkthrough's central promise — the agent knows the operational facts **at spawn** — is not delivered as specified: the assembled injection block admits **5 of 8** entries under the Ari-seam's empty query and **7 of 8** under a realistic first user message; `artifact-is-container-image` was **never admitted under any query tested**, and the once-per-instance flag makes the shortfall **permanent for that instance** (`pok_query` is the only recovery). The mechanism itself is design-faithful (B5 selector reuse, threshold 0.30, floor 2, pinned bypass, zero POK-invented weights) — the design's own AC1/AC22 ("8 entries") over-promises relative to §4.5.1. This is an **AC-vs-mechanism inconsistency, not a code bug**; fix direction below.

**L10:** **14 deterministic net-new reds** vs TRUE BASE (criterion was 0). Twelve are one mechanism — the POK B4 grant sweep (commit `16b4e70b2`, `"pok"` into 45 agents' `tools.allow`) and the F-1 ceremony bumps were **not re-pinned in 8+ other lanes' exact-set / trailing-element / grant-array pins** (aegis ×7 incl. canary + leaf trio, critic resolved-set, kb-writer resolved-set, memoir trailing-pin, prompt-reload trailing-pin, nest-report grant-array ×2); one is a wedge-capture BOOT_CONTINUE logger-site count pin (20→30); one a metrics `VOCABULARY_VERSION` pin still expecting 7 after the sanctioned 7→8 bump. All 14 are stale-test-expectation drift, no production-behavior regression is evidenced — but they redden every future whole-tree gate until re-pinned. Additionally: a **6-node attestation hang family** (tests/integration) that fails gracefully at base but **wedges the whole pytest process at HEAD** (base probe rc=1 with clean Timeout section vs HEAD outer-timeout 124) — unresolved, needs its own diagnosis commission; and **49 integration reds remain base-unclassified** (disclosed gap).

| # | Leg | Verdict | One-line evidence |
|---|-----|---------|-------------------|
| 1 | llmproxy facts INJECT to ari/leader/devops slices | **FAIL** | admitted 5 (query="") / 7 (realistic query); artifact never admitted; permanent per instance; visible set 8 ⇒ audience routing correct |
| 2 | Audience filtering + per-view budget | **PASS** | dev/worker = exactly the 3 `both` rows; impl absent from op views, op absent from dev view, `both` everywhere; audience_agents override honoured; 961c/359c ≤ 2,900; ≤8 entries |
| 3 | `pok_query` universal | **PASS** | all 8 slugs returned from every audience position incl. developer→operation-only; tier-blind; `[CURATED]` lineage-only; default project → typed `pok_default_project` |
| 4 | Supersede makes contradiction impossible | **PASS** | `uq_pok_active_slug` partial index binds exactly dual-active (superseded/archived dup inserts succeed); 8/8 race trials = {1 created, 1 typed `pok_slug_conflict`}, zero raw escapes; flip+insert one transaction (CheckViolation rollback ⇒ prior still active) |
| 5 | Decay renders | **PASS** | `[stale 40d]` exact substring; boundary strictly >30d (30 fresh, 31 stale); 240c body + mark = 292 ≤ 332 budget; stale ranked down, never dropped |
| 6 | Fence renders forged headers inert | **PASS** | write-time: 5/5 typed refusals (`pok_body_structural_token`/`pok_body_empty`), 0 rows persisted; render-time: hostile SQL-injected body flattened+prefixed ⇒ exactly 1 header line, sibling byte-identical, `[SYSTEM CONTEXT:` never line-initial |
| 7 | Metadata cap shrinks injection (independent) | **PASS** | fixture 352,991B→15,543B = **−95.60%** (dev's claim independently confirmed); tokens 88,248→3,886 (chars/4; tiktoken −95.35%); BEFORE renderer re-extracted from TRUE BASE, byte-identical to the vendored copy |
| 8 | Dev seed receipts + idempotence | **PASS** | 9 rows = 8 active + 1 superseded, all receipt ids/fields match; 8/8 bodies byte-identical to payload module; 22/19 probe artifacts re-verified; seeder re-run = 8× `no-op`, 0 new rows, pin re-asserted |
| 9 | Tier semantics | **PASS (20/20)** | CHECK fires on PG; default `agent`; `pok_tier_requires_curator`/`pok_curator_required`/`pok_curated_requires_curator` typed; curated band cap 2/view (3 visible → 2 admitted), order pinned→curated→ranked, 739c ≤ 2,900; tier inherits on supersede, explicit override wins |
| 10 | Regression net-new reds = 0 vs TRUE base | **FAIL** | tests/unit 23,484 results / 224 reds = **204 base-proven + 14 deterministic net-new** (12 = B4 `"pok"`-grant pin-not-re-pinned class; 1 wedge-capture logger-count; 1 vocab-version pin) + 5 context-dependent batch-only; integration: 49 reds base-unclassified + 6-node hang family (graceful-fail at base → process-wedge at HEAD) |
| 11 | Pack registration honesty + baseline + migrations | **PASS-WITH-DISCLOSURES** | 9/9 registered packs PASS (none >15.2s); baseline `--check` OK 181; migrations +3 additive, unique prefixes, CHECKs mirrored, no FK; **4 stale PACKS.md node counts** (43→48, 37→39, 20→21, 9→10) |
| 12 | Isolated boot smoke | **PASS-WITH-NOTES** | fenced boot on throwaway PG: both POK tables + `uq_pok_active_slug` materialize (AC9/B7), boot idempotent, roster 44/39 zero parse failures, `pok` + 3 tools registered, livez/readyz 200 all-true, SIGTERM graceful rc 143, zero live contact |
| 13 | Mutation honesty | **PASS** | 3/3 independent plants RED→restore→GREEN with sha-verified byte-for-byte restores; final `git status --porcelain` empty at tip c4c2293fa |

---

## Scope Decision

Full gate (13 legs) as commissioned — cross-module lane (storage + schema + tools + injection + 46-agent grant sweep + seed + metadata cap), release-gate-class blast radius. No scope reduction applied. Method disclosure: the base side of L10 uses per-red-file confirmation against a `git archive` extraction of TRUE BASE (not a whole second tree run) — cost-bounded on a memory-tight host; each HEAD red file is re-run at base with identical commands.

## Environment / fences (held on every leg)

Worktree venv `.venv/bin/python` with `import daemon` proven INSIDE the worktree before any run (F6 import-shadow trap); ambient PROD `POSTGRES_*` scrubbed **per-variable** (never `env -u POSTGRES_*`); `current_database()='ensemble_dev'` asserted before every dev-DB write; zero `:9797`/live-daemon/`ensemble_prod`/release-symlink/live-state.json-write contact (the boot leg hashed live state.json read-only; see its note); every run `timeout`-bounded; serial pytest; scratch rows/projects created and deleted with cleanup proofs; `git status --porcelain` empty at start and end of every leg.

---

## L1 — injection into ari/leader/devops slices — **FAIL**

**Path (real, not hand-rendered):** `POKService._repo.list_entries` → `admit_view(agent_id, query, rank_fn, embeddings_map)` → `build_pok_context_message`.

| View | Visible (audience-filtered) | Admitted (query="") | Omitted-tail | Chars |
|------|------------------------------|---------------------|--------------|-------|
| ari | 8 | **5** | 2 | 961 |
| leader | 8 | **5** | 2 | 961 |
| devops | 8 | **5** | 2 | 961 |
| developer | 3 | 2 | — | 359 |
| worker | 3 | 2 | — | 359 |

- Pinned tier (pipeline-03), spine order 01→02→03, wrapper: **PASS**. Deploy contrast pair: adjacent only under a relevance-bearing query (absent under `query=""`).
- Broad query (`deploy pipeline artifact healthz model catalog backup verify`): admitted 7; `artifact-is-container-image` still below threshold.
- **Real assembly path** (`_maybe_pok_context_message`, fixture manager): `user_query=""` → 5 entries / 961c; realistic first message ("continue the llm-supervisor-proxy deployment work — what is the deploy order?") → **7 entries / 1193c** with spine + deploy pair intact; artifact still absent.
- **Mechanism (from code):** `rank_for_view` → `_rank_with_selector` → `select_critical_notes_for_injection` with `SelectionConfig(fusion_threshold=0.30, fusion_bm25_weight=0.4, fusion_vector_weight=0.6, tail_cap=6, floor_count=2)`; pinned bypass threshold; empty query ⇒ BM25=0 ⇒ all non-pinned below 0.30 ⇒ floor rescues 2 by (priority, recency) = model-catalogs + healthz. Zero POK-invented weights (B5/A-12 compliant).
- **Permanence:** `POK_INJECTION_FLAG="pok_injected"` (instance metadata) set by the consumer after first carry; `_maybe_pok_context_message` returns None once set ⇒ a 5-entry spawn never re-admits. Ari seam mirrors this per (instance, project). `pok_query` is the only recovery path.
- **Design tension (verbatim):** plan-overview AC1 "renders the block containing the 8 entries …" and AC22 "operation∪both (8 entries here)" vs architecture-recommendation §4.5.1 "the ari seam passes the empty query and the selector's floor supplies the deterministic baseline" (⇒ pinned 3 + floor 2 = 5).

**Minimal fix direction (report only — not applied):**
(a) raise `floor_count` (or bypass the fusion threshold) for the POK injection path so the per-view admitted set reaches the full visible set under budget (8/2,900 already sized for it) — one config/branch, no schema change; or
(b) amend AC1/AC22 to the delivered reality (5 empty-query / ≤7 with query) and rely on `pok_query` + the F5 omitted-tail line for coverage.
Decision owner: leader/Emperor (design-doc amendment vs code change).

## L2 — audience filtering + budget — **PASS**

- Seed project: developer and worker views = **exactly** the 3 `both` rows (artifact, pipeline-01, healthz); 0 of 5 operation-only rows. ari/leader/devops visible = 8.
- Scratch project (synthetic operation/implementation/both + `audience_agents` override): implementation absent from ari/devops/worker views; operation absent from developer view; `both` in every view; **`audience_agents` override honoured** (operation row narrowed to `["developer"]` visible only there).
- Budgets: every rendered view ≤8 entries and ≤2,900 chars incl. the 47c wrapper (max observed 961c); per-entry render ceiling observed 224c < 332c design bound.

## L3 — `pok_query` universal — **PASS**

- All 8 slugs returned to a `developer` position (incl. every operation-only slug); identical counts across developer/worker/devops/leader/ari for the same query — **no audience filter on retrieval** (ER-1.4 invariant holds).
- Tier-blind: a `curated` row is returned to a non-curator; `[CURATED]` appears in query lineage only — **no in-block curated marker** (T-4 v1 conservative default confirmed).
- Default project (`__system_default__`): typed `pok_default_project` refusal, both directions.
- Ari seam (`maybe_inject_ari_pok_block`, fixture manager): operation view emitted for non-default target with caller=ari; implementation row filtered; fresh-read per dispatch (two projects both inject; metadata-cleared instance re-injects); same-project re-dispatch suppressed (once-per-instance+project, by design); negative legs (non-ari, no project, default by id/name) → no block, no exception.

## L4 — contradiction impossible — **PASS**

- Direct second ACTIVE insert, same `(project_id, slug)` → `sqlalchemy.exc.IntegrityError (psycopg.errors.UniqueViolation) … "uq_pok_active_slug"`; index catalog-proven **PARTIAL** (`WHERE status='active'`); superseded- and archived-duplicate inserts **succeed** ⇒ the index binds exactly the contradiction case.
- Concurrent-write race (2 threads, `threading.Barrier(2)`, 8 trials): **8/8 = {1× created, 1× typed `pok_slug_conflict`}**, zero `no-op`, zero raw escapes, `active_rows=1` every trial. Forced-conversion leg (monkeypatched insert raising UniqueViolation) → typed `pok_slug_conflict` with `details.slug` populated; discriminator truth-table 4/4 (`uq_pok_active_slug`→conflict; other-UNIQUE→no; CHECK→re-raised raw, correctly not mislabeled; NOT NULL→no).
- `pok_upsert` on existing active slug → `action=superseded`; post-state: old `status=superseded`, `old.superseded_by_id==new.id`, exactly 1 active row.
- **One logical operation:** successor violating `ck_pok_audience` inside `supersede_and_insert` → `CheckViolation` raised and **prior still active, `superseded_by_id` NULL, 0 successor rows** — flip+insert is a single commit. Supplemental `BEGIN; flip; ROLLBACK` direct-connection test concurs.
- Disclosure (routed, not a gate failure): the ORM `prior` object is mutated pre-session and **expires** after a failed commit (DetachedInstanceError on re-read) — DB state authoritative and correct; caller-held object stale across a lost supersede.

## L5 — decay renders — **PASS**

- Aged 40d: exact substring `[stale 40d]` (renders inside the audience bracket per shipped template: `[deploy|both [stale 40d]]`); fresh: no marker.
- Boundary: 29d/30d fresh, **31d stale** — rule is strictly `(now − last_verified).days > 30`.
- Budget: 240c body (post-escape) + 12c mark = **292 ≤ 332** entry budget; mark contributes exactly its 12 chars.
- Stale entry admitted at rank 2 of 3 (ranked down, never dropped) in a view with headroom.

## L6 — fence, two layers — **PASS**

- Write-time (5/5 typed, 0 rows persisted): newline / leading `#` / `[SYSTEM CONTEXT:` / triple-backtick → `pok_body_structural_token`; empty+whitespace → `pok_body_empty`.
- Render-time (hostile body planted via direct SQL, bypassing the write fence): newlines+CRs flattened to spaces, `escape_for_context_block` escapes `&<>`, body always prefixed by `- {slug} [{domain}|{audience}{stale}]: ` ⇒ exactly **1** line-initial `## ` header in the block; forged `## Injected Header` mid-line only; **sibling entry byte-identical** between hostile and control renders; `[SYSTEM CONTEXT:` never line-initial. Slug slot also flattened (defense-in-depth).
- Residual (cosmetic, disclosed): forged tokens remain visible as mid-line text; no line-delimited consumer in this system parses mid-line `#`.

## L7 — metadata cap, INDEPENDENT measurement — **PASS**

| Basis | Before B | After B | Δ | Before tok (chars/4) | After tok |
|---|---|---|---|---|---|
| Calibrated fixture (69 keys) | 352,991 | 15,543 | **−95.60%** | 88,248 | 3,886 |
| tiktoken cl100k cross-check | — | — | −95.35% | 80,856 | 3,763 |
| Live-shaped dev `__system_default__` (3 tiny keys) | 704 | 704 | 0.00% | 176 | 176 |

- **Independence:** the dev harness's "before" is a vendored copy; the gate worker re-extracted `_format_project_json_section` from TRUE BASE `6b571771d` (`git show`) and rendered the same fixture — **byte-identical (352,991 == 352,991)**. Bases reproduce exactly (279,810 B / 408,809 B / 68.45%).
- Anti-vacuity: BEFORE passes a 3,000c non-allowlisted value verbatim (uncapped); AFTER caps it with `…[truncated +1002c]` + `_omitted_metadata_keys: 193` tail, section 15,938 ≤ 16,384; allowlisted `ari_plane_tracking_policy` renders verbatim untruncated.
- Disclosures: dev DB has no large metadata row (0% delta live-shaped — matches dev's own disclosure); dev's vendored-vs-live equality proof holds only on non-capping payloads (the base-extraction check closes that gap); chars/4 vs tiktoken differ ~0.25pp.

## L8 — seed receipts + idempotence — **PASS**

- Snapshot vs `seed-payload-llmproxy.md`: **all 9 rows match** (8 active + 1 superseded; every receipt uuid; attribution `leader` ×9; `provenance=observed`; `source_tier=agent`; `pipeline-03` pinned; supersede wire `cba42550→dab0ae27` flipped 11:56:41.320117; body lengths 91/89/90/91/93/92/90/167).
- Bodies **byte-identical** to `scripts/pok/payload_llmproxy.py` (8/8, verbatim, no normalization). Entry 8: slug design-verbatim; body carries 22-vs-19 + primary-only {agentic-Main, deepseek, opendesign-flash, quick-main} + backup-only {coding-free}; **external probe re-verified** from the preserved `/tmp/pok_models_{primary,backup}.json` (counts + delta sets recomputed from raw `data[].id`).
- Idempotence (real path re-run): **8× `action=no-op`, 0 new rows, all 9 ids unchanged, pin re-asserted** (`[pin] … pinned=True (curator=leader)`), post-write verify 8/8.
- Finding (disclosed, resolved): the **no-op echo stamps `last_verified`** (frozen A-8/W3, commit `7404949ef`, postdates the receipt doc) — the re-run stamped all 8 active rows; the worker restored the two volatile columns (`updated_at`, `last_verified`) to the pre-run snapshot by direct SQL and re-verified **20 fields + body hashes byte-identical**. Consequence: the receipt doc's "no row has last_verified_at set" holds only until the next seeder run — future lanes must snapshot before re-running.

## L9 — tier semantics — **PASS (20/20)**

- PG catalog: `source_tier` NOT NULL DEFAULT `'agent'`; `ck_pok_source_tier` CHECK fires (`'guidelines'` → CheckViolation, rolled back, 0 residue); Phase-1 CHECKs + partial index present.
- Default path: omitted tier → `'agent'` (Phase-1 shape byte-unchanged).
- Refusals (typed, pre-write, no residue): developer+curated → `pok_tier_requires_curator`; developer `pok_curate(source_tier=…)` → `pok_curator_required`; developer supersede-of-curated → `pok_curated_requires_curator`; leader/PM succeed on all three.
- Curated band: 12-active scratch (1 pinned + 4 curated + 7 agent) → ari view admitted exactly 8 = 1 pinned + **2 curated (cap held with 3 curated visible)** + 5 ranked, order pinned→curated→ranked, render 739c ≤ 2,900 incl. wrapper; developer view 450c, audience-invisible curated excluded-for-view, no drag.
- Inheritance: supersede curated without tier → successor `curated` (inherited); explicit `source_tier='agent'` by curator → override wins.

## L10 — regression vs TRUE base — **FAIL (14 deterministic net-new reds; criterion was 0)**

Corpus: **27,630 covered test results at HEAD** (first leg 4,547 across 14 non-unit dirs + follow-up 23,484 across all of tests/unit) + tests/integration top-level (1,156 runnable). DEFAULT addopts both sides (`-m 'not integration and not postgres and not slow and not conformance'`, `timeout=30 thread`); import fences proven both sides (HEAD→worktree; BASE→`git archive 6b571771d` extraction; F6 shadow-trap avoided). Method: per-FILE base confirmation with identical commands + one dir-level base run for the api order-dependence discriminator + 3× isolation re-runs for every candidate. Evidence: `/tmp/pok-leg10-wrap/logs/LEG10-SUMMARY.md` (first leg, 51 logs) and `/tmp/pok-l10-followup-1794105824-/` (SUMMARY.md ledger, base-reds/, netnew/, logs/).

**First leg (14 non-unit dirs, 4,547 tests, 73 reds):** 70 base-proven + 3 flaky (`atomic_dequeue` ×2, `atomic_status` ×1 — both-sides flaky, known concurrency family) ⇒ 0 net-new. The 6 api reds are pre-existing order-dependent `Task attached to a different loop` (db_offload.py:188) — base-identical at dir level.

**Follow-up leg (tests/unit, 23,484 results, 224 reds):**

| partition | HEAD reds | base-proven | candidates |
|---|---|---|---|
| unit-services | 18 | 17 | 1 |
| unit-tools | 16 | 14 | 2 |
| unit-top-ac | 52 | 52 | 0 |
| unit-top-dr | 44 | 39 | 5 |
| unit-top-sz | 5 | 3 | 2 |
| unit-routers | 72 | 72 | 0 |
| unit-plugin-subsystem | 5 | 4 | 1 |
| unit-metrics | 3 | 2 | 1 |
| unit-jobqueue-persistence | 1 | 1 | 0 |
| unit-smalldirs | 8 | 0 | 8 |
| **total** | **224** | **204** | **20 → 19 unique** (one double-listed) |

**3× isolation (corrected rounds — see defect note): identical 14-node red set all three rounds.**

- **14 DETERMINISTIC net-new reds** (`netnew/deterministic-clean.txt`, tb=short `logs/netnew-tbshort-corrected.log`):
  - **12 × B4 pin-not-re-pinned** — `Extra items in the left set: 'pok'` class: `test_aegis_allow_set_exact`, `t35_canary_allow_set_exact`, `t35_leaf_trio_allow_sets_individual`, `children_allow_set_exact[canary|cracker|hacker|sweeper]` (aegis family, 7), `test_designer_rewire::test_critic_tools_resolve` (critic resolved-set), `test_kb_writer_tools::test_resolved_set_is_exactly_expected`, memoir trailing-pin (`'pok' == 'nest-report'` — last-element assertion), prompt-reload trailing-pin (same shape), nest-report grant-array pins ×2 (`('maintenancer', ['nest-report-read', 'pok', 'nest-report'])`). **Mechanism CONFIRMED**: commit `16b4e70b2` added `"pok"` to 45 agents' `tools.allow` (aegis meta.json one-line diff `"nest-report"` → `"pok", "nest-report"`); the pinned expectation (`AEGIS_ALLOW` frozenset) contains no `"pok"`; single-node rerun reproduces `AssertionError: Extra items in the left set: 'pok'` at `tests/unit/agents/test_aegis_security_family.py:385`.
  - **1 × wedge-capture BOOT_CONTINUE logger-site count pin**: base 20 → HEAD 30 sites (+10 within-lane; owner should attribute).
  - **1 × metrics vocabulary-version pin**: `cm.VOCABULARY_VERSION` == 8 (sanctioned F-1 bump), a pin in another lane's file still expects 7.
- **5 batch-only reds** (green 3/3 in isolation — order/context-dependent, NOT deterministic): aegis `test_signal_terminates_the_install_and_releases_the_lock[SIGTERM-143-bash]`, `test_completion_registry::test_cleanup_stale`, `test_hide_kb_instances` ×2, `test_mcp_tool_timeout::test_tool_node_handles_timeout`. Flake-family; quarantine recommended AFTER attribution (not done in this gate — attribution unclear).
- **tests/integration top-level (COMPLETE, 883s, no cap-limit): 49 reds (48F+1E) / 1,076 passed / 24 skipped / 485 house-deselected.** Reds concentrate: initiative_message_integration 8, maintenancer_end_to_end 6, spawn_intelligence_tier 5, complete_cancel_route 4, message_metadata_prune 3, long_tool_nudge_graph_smoke 3, +19 others. **Base classification NOT run** (disclosed gap; verdict is FAIL on unit alone, so the gap does not change it — offered as follow-up).
- **6-node attestation hang family (UNRESOLVED, regression-class):** `test_attestation_bound_escalation.py` et al. — representative node at BASE: **rc=1, pytest-timeout fires, run completes gracefully** (Timeout section in `logs/base-hang-probe.log`); at HEAD the same node **wedges the whole pytest process** (outer timeout 124). Neither pass nor red at HEAD; deselected via the hang-loop ledger (`desel-integration.txt`). Needs its own diagnosis commission (candidate seam: the lane's `instance_messaging.py`/injection-landing touch).
- **Harvest defect (found + repaired, documented):** the detached driver's isolation rounds were a **false zero** — `FAILED `-prefixed ids fed to pytest → usage-abort (0 tests) ×3 recorded as `reds=0`. The harvester re-ran the identical step with prefixes stripped (same 19 ids, same flags) — the 14-node deterministic set above is from the CORRECTED rounds (`netnew/round{1,2,3}-corrected.txt`, SUMMARY.md correction block 05:00:39Z). Candidate derivation itself was valid (prefix cancels in the comm-diff).
- **Known-debt verification:** canary duty-prompts 7F ✓ (exact 7); metrics adjudication stale-LANE_BASE family ✓ (base-proven); T7 dispatch_chain ×3 ✓ (base-proven, unit-services); g7 sys.exit collection error NOT observed at HEAD (all unit partitions completed with counts).
- Operational disclosures: both regression workers suffered death-class stalls (~11h and ~1h45m; todo store lost on the first; anomaly `ab08040a` filed for a /tmp evidence-dir wipe — suspected sibling collision); the follow-up worker's shell carried ambient LIVE poison (ENSEMBLE_SELF_ENV=live / PORT=9797 / POSTGRES_DB=ensemble_prod) — scrubbed per-variable on every invocation, zero live contact; foreign WIP observed in `.agents/shared/planning/designer-agent/install-audit.jsonl` (+33 lines, attributed to the designer/OD runtime writer inside the stall gap — untouched).

**Minimal fix direction (report only):** re-pin the 12 grant/trailing/resolved-set pins to include `"pok"` (aegis/canary/leaf-trio/critic/kb-writer/memoir/prompt-reload/nest-report test files) + the wedge-capture count pin (20→30) + the one VOCAB pin (7→8) — a single pin-ceremony commit, no production code. Separately: commission the attestation hang-family diagnosis; optionally base-classify the 49 integration reds.


## L11 — pack registration honesty + baseline + migrations — **PASS-WITH-DISCLOSURES**

- **All 9 registered packs PASS** (serial, timeout-wrapped, none >15.2s): trust_tier 40/40 · service 48 · injection 39 · ari_seam 13/13 · tools 21 · pg_materialization 10 (scratch `ensemble_test`, conftest fence held) · ceremony_pins 45/45 · metadata_cap 23/23 · metadata_cap_regression 140p+1s.
- Baseline: `scripts/protection/generate_prompt_artifact_baseline.py --check` → **PROMPT-BASELINE: OK — 181**.
- Migrations: 122 → **125** (+3: `20261107_110001_create_project_operating_knowledge.sql`, `20261107_110002_…_embeddings.sql`, `20261107_110003_add_pok_source_tier.sql`), zero duplicate prefixes, UP additive-only (DROPs only after `-- DOWN`), CHECK vocabularies mirrored in .sql + SQLModel, **no FK**; both models registered `daemon/manager.py` pre-create_all; `source_tier` via `_ensure_postgres_columns`.
- **Defect (docs-only, minor):** 4 PACKS.md node-count claims are stale/under-stated at tip: `pok_service_unit` 43→**48**, `pok_injection_unit` 37→**39**, `pok_tools_unit` 20→**21**, `pok_pg_materialization` 9→**10**. All packs still PASS; a re-count + re-stamp of the POK blocks is warranted.

## L12 — isolated boot smoke — **PASS-WITH-NOTES**

- Fenced boot (`env -i` allowlist; `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned and verified in the child's `/proc/<pid>/environ`; `ENSEMBLE_SELF_ENV=dev`; `PORT=DAEMON_PORT=10097`; throwaway PG `pok_smoke_gate_l12` created over TCP by the house superuser, `current_database()` asserted pre-boot; `ENSEMBLE_CONFIG`/`ENSEMBLE_DATA_DIR` in /tmp; no `data/ensemble.json` pin in the worktree).
- Assertions: banner `Starting Ensemble v0.29.1`; **both POK tables + `uq_pok_active_slug` materialize on the throwaway DB (AC9/B7 proven)**; audience/audience_agents/source_tier/pinned/disputed columns present; **second boot idempotent** (zero duplicate-index errors, index count still 1); roster **44 entries / 39 unique ids, zero parse failures**; `pok` category in the registry artifact (vocabulary_version 8, 62 categories) + 3 tool names registered via the three-step seam; **45 agent meta.json files grant `pok`**; livez 200, readyz ready all-4-true; SIGTERM graceful rc 143, port freed; throwaway DB dropped.
- Notes (adjudication items, non-blocking): **column-name drift** — design A-8 says `last_verified_at`, shipped column is `last_verified` (self-consistent across .sql + model + DB); roster 44/39 vs the lane's "46 registered" phrasing (45 explicit grants + watcher inheritance; counting-basis difference, disclosed); PG15 schema-grant workaround on the throwaway DB only; `PORT` env required alongside `DAEMON_PORT` (config reads `${PORT:-8088}`); webfetch MCP degraded under `env -i` (uvx off-PATH — env artifact, graceful per init-guard).
- Live boundary: `:9797` never probed; live `state.json` hashed read-only — sha changed mid-window (mtime 15:37) with **promote fields stable** (`current=v0.29.1 / previous=v0.29.0 / in_flight=null`) ⇒ live-daemon-owned journaling churn, zero lane writes (all gate boot state proven under `/tmp/pok-gate-data`).
- Transient observation (explained): the boot worker saw `M daemon/services/context_messages.py` mid-window — timing matches the packs worker's M-C mutation plant (allowlist drop), which was sha-verified restored (`fc30950a…`) and the tree re-verified clean at tip before this report was finalized.

## L13 — mutation honesty — **PASS**

| Plant | RED nodes | Restore | GREEN |
|---|---|---|---|
| remove `"pok"` from `agents/coder/meta.json` | grant-walk ×2 (matches M-POK-1) | byte-for-byte (sha `1f3bd9ca…`) | 45/45 |
| `VOCABULARY_VERSION` 8→7 | vocab + artifact pins ×4 | byte-for-byte (sha `a336be9f…`) | 45/45 |
| drop `ari_plane_tracking_policy` from `POK_METADATA_ALLOWLIST` | verbatim + families + cap-constants ×3 | byte-for-byte (sha `fc30950a…`) | 23/23 |

Final state: `git status --porcelain` empty; `git diff --stat` empty; HEAD `c4c2293fa`. Backups only under /tmp.

---

## Consolidated disclosures & routed follow-ups

1. **AC1/AC2 8-entry promise vs selector reality (L1, gate FAIL)** — fix direction (a) floor/threshold for the POK path or (b) AC amendment; leader/Emperor decision.
2. **14 deterministic net-new reds (L10, gate FAIL)** — 12 × B4 `"pok"`-grant pin-not-re-pinned + wedge-capture count + VOCAB pin; single pin-ceremony commit fixes all 14 (test-only).
3. **6-node attestation hang family (L10)** — graceful-fail at base → process-wedge at HEAD; unresolved; needs its own diagnosis commission (candidate seam: instance_messaging/injection landing).
4. **49 integration reds base-unclassified (L10)** — disclosed gap; optional follow-up (verdict unaffected).
5. **5 context-dependent batch-only reds (L10)** — flake-family; quarantine AFTER attribution (not done in this gate).
6. **PACKS.md stale node counts ×4 (L11)** — re-count + re-stamp the POK blocks (docs-only).
7. **`last_verified` vs design's `last_verified_at` (L12)** — design-doc correction or column rename; self-consistent as shipped.
8. **No-op echo stamps `last_verified` (L8)** — receipt-doc claim is stale after any seeder re-run; future lanes snapshot first.
9. **ORM prior-object expiry after failed supersede (L4)** — DetachedInstanceError on caller-held object; DB correct; routed to implementer.
10. **Dev DB credential hygiene (supersede leg)** — dev `ensemble_dev_app` password transited a worker transcript + a /tmp file (chmod 600 then deleted); standing env-poison/hygiene class, couples to the deferred rotation item.
11. **Receipt-doc micro-staleness (L8)** — pin re-assert timestamp drift; doc-only.
12. **Boot roster counting basis (L12)** — 44 entries/39 unique vs "46 registered"; align phrasing.
13. **Detached-driver false-zero gotcha (L10 harvest)** — `FAILED `-prefixed ids fed to pytest silently run 0 tests and read as a green ×3; corrected rounds are authoritative; every future detached pipeline must strip prefixes and assert `collected > 0`. (KB entry recorded by the harvester.)
14. **Two death-class worker stalls on the regression leg** (~11h, ~1h45m; todo-store loss; anomaly `ab08040a` /tmp wipe) — consistent with the known daemon-OOM/child-clobber quirk and/or the live proxy 'coding'-alias defect; detached-execution + per-partition SUMMARY.md checkpoints are the working mitigation.

## Code changes by this gate

**None.** No source edits, no fixes, no pushes. Scratch DB objects/projects created and deleted with cleanup proofs. The only repo delta is this evidence document (+ PACKS.md gate block), committed as a docs commit on the branch.
