# Architecture Recommendation: LangGraph Checkpoint Perf v2 — Port Validation & Enrichment

> Rev 2.2 — delta-review sweep (2026-09-04): stale-text alignment (5 warnings + 3 suggestions); APPROVE sign-off precondition

Date: 2026-09-04
Architect instance: controller + 3 dispatched analysts
Worker instances: A=`42dacea1` (trade-off-analysis, port strategy) · B=`249f31f9` (data-flow-design, read path) · C=`9695b322` (resilience-design, prune safety)
Mode: Standard Design (3-worker multi-dimensional fan-out). Council checklist: only cross-system impact clearly met; the contested-approaches criterion applies to execution mechanics, not architecture direction (architecture was decided and reviewed in v1), and every sub-decision is reversible pre-merge under the plan's own constraints (fresh branch, no-merge, flag-gated destructive ops, PR5 gate). Borderline → Standard per calibration.
Inputs: plan-overview.md, phase0–5-plan.md, technical-analysis.md, requirements.md, v1 branch (read-only, `fc908945`), source doc `~/Downloads/langgraph-checkpoint-performance-discussion.md` (1777 lines), live repo @ `2f80d45b`.
Attribution note: Worker B omitted the required first-line skill confirmation; report quality is consistent with skill application and its load-bearing claims were independently verified by the architect (spot-checks below). No re-dispatch needed.

---

## 0. Headline Verdicts

| Question | Verdict |
|---|---|
| Port strategy (planner's hybrid) | **CONFIRM Approach A** — but the planner's *rationale* is partly wrong and the conflict map is materially overstated (§1) |
| Q1 PG parity (<14.22) | **Not a hard blocker for execution; IS a blocker for binding validity → PIN-PARITY ≥14.22** (§2.1) |
| Q4 reviewer artifact | **Design validated + full artifact spec** (§2.2) |
| Q8 seq-index | **DEFER the index; ship the row-growth prune instead** (§2.3) — the prune is now a merge precondition (§3) |
| Backfill criteria | **Criteria A+B+C insufficient — Criterion B is false on the merits; adopt corrected A′/B′/C′; expected outcome still DROP** (§2.4) |
| Phase ordering | **CONFIRM 0→1→2→3→4→5** — do not move Phase 4 before Phase 3; do not merge/split phases (§4) |
| Guardrails (§33, revive-on-send, quiesce) | **PRESERVED** — with 2 mandatory plan additions (§5) |
| NEW FINDING | 🔴 `message_metadata` side table has **no prune anywhere** — unbounded growth inherited from v1, goes live with PR2 (§3) |
| Overall | **CONDITIONAL GO** for implementation dispatch — apply the 7 MUST-FIX plan edits first (§8) |

---

## 1. Port Strategy Verdict — CONFIRM hybrid, CORRECT rationale + conflict map

### 1.1 The planner's load-bearing claim is FALSE, but the recommendation survives on better grounds

All four gates cited as "requiring per-PR commit boundaries" are **HEAD-relative, not commit-history-relative** (Worker A, verified against v1 sources):

| Gate | Boundary-dependent? | Evidence |
|---|---|---|
| GATE_SUITES.txt regen | No — `pytest --collect-only` at HEAD | regen method in file header, `fc908945:tests/integration/gate_suites/GATE_SUITES.txt:6-15` |
| AST placement gate (4-site/4-label/no-ToolNode) | No — AST walk of `daemon/**/*.py` at HEAD | `fc908945:tests/integration/test_message_metadata_hook_placement.py:1-50` |
| Binding gate (real PG 9/9) | No — runtime test | `fc908945:tests/integration/checkpoint_prune_real_saver.py` |
| PR4 structural-unreachability AST gate | No — 8-combo flag matrix + AST, HEAD-relative | `tests/unit/services/test_maintenance_prune_direct_anti_join.py` |

**Cherry-pick-per-PR wins on provenance, revertibility, and PR4 pair semantics — not gate mechanics:**

| Approach | Complexity | Scalability | Maintainability | Risk | Cost | Recommendation |
|---|---|---|---|---|---|---|
| **A: cherry-pick per-PR + manual PR1** (planner) | Med (4 per-PR conflict loops) | Good (small diffs, 3-way holds) | **Best** (`cherry-pick -x` = mechanically auditable v1 provenance; conflict hunks ARE regression evidence) | Good (per-PR `git revert`; PR4 pair protected) | ~16–24h | **ADOPT** (weighted 3.84) |
| B: whole-branch merge/rebase | High (521-file one-shot) | Poor (no per-PR control) | Poor (12k-LOC single-pass review) | **Poor — dissolves PR4's atomic-pair safety net; single revert unit** | ~6–8h | REJECT (2.60) |
| C: full manual re-apply | Med | Good | Good (clean trees, loses `-x` SHA annotation) | Good | ~16–24h | Fallback per-PR when A's 3-way merge fails (3.65) |

**Fallback rule:** if `cherry-pick -x --3way` fails structurally on PR2's hot files (the only real conflict zone), switch **PR2 only** to manual re-apply (Approach C); PR1/PR3/PR4 cherry-pick cleanly (§1.2).

### 1.2 CORRECTED conflict map — the TA's premise is false for 5 files

**Triple-verified pre-finding** (Workers A + C independently; architect spot-check): `git diff 58260f35..2f80d45b` returns **ZERO lines** for `daemon/services/maintenance.py`, `daemon/persistence.py`, `daemon/checkpoint_adapter.py`, `daemon/repositories/__init__.py`, `daemon/repositories/factory.py`. The 9-day churn landed in mission/defer-gate files (`job_queue_service.py`, `instance_lifecycle.py`, `mission_resolver*`), NOT in PR1/PR3/PR4's primary hot files.

| File | TA claim | Corrected verdict | Evidence |
|---|---|---|---|
| `daemon/persistence.py` | HIGH (PR1 timing + PR3 alist deletion vs compaction/identity churn) | **ZERO-CONFLICT** — byte-identical; both cherry-picks replay verbatim | empty diff 58260f35..2f80d45b |
| `daemon/services/maintenance.py` | MED (defer-gate widened predicates) | **ZERO-CONFLICT** — defer-gate fix landed elsewhere; Operation E anchor (`:448`→`:450`) intact | empty diff; Worker C anchor cite |
| `daemon/checkpoint_adapter.py` | LOW | **ZERO-CONFLICT** — abstract-method anchor `:85` (`find_excess_checkpoint_groups`), PG adapter `:378`, SQLite `:210` intact | empty diff |
| `daemon/manager.py` | HIGH, "block-last" | **HIGH but re-anchor** — v2 `manager.py:6642` already contains an **unrelated** `message_metadata` kwarg (v2 task-context). Insert the `message_metadata_repo` property at the `_db_connection_repository` property block end, NOT at :6642 | grep confirms kwarg at :6642 (commit `dbf9ef44`); names absent in v2 |
| `daemon/services/instance_messaging.py` | HIGH at `:821`/`:3425` | **HIGH, targets shifted ~335–340 lines** — `_maybe_compact_context` now at `:1156`; entry-path tap now ~`:3747-3765`; import appended after `from .messaging_types import ...`. 3-way merge should still resolve (import block unmodified) | Worker A line cites |
| `daemon/graph.py` | HIGH, F2-hoist `:3386-3397` | **CONFIRM, site shifted** — dual-return now at `:3731-3732`; F2 single-return hoist applies there; reactive-compaction tap inserts after `aupdate_state` at `:3583-3585` | Worker A |
| `daemon/services/instance_lifecycle.py` | MED-HIGH | **CONFIRM HIGH** — ~515-line real churn (governor, P1–P3 fixes, tidier); 4 MessageTapSlot constructions need manual fix-up | Worker A |
| `daemon/constants.py` | adjacent-inserts | **CONFIRM LOW** — all 4 PR4 flag names absent from v2; `IDEMPOTENCY_KEY_TTL_HOURS` anchor `:75` intact | Worker A grep |

Net effect: PR1, PR3, PR4 land with zero conflict on primary hot files; **all real conflict is PR2's** (`instance_messaging.py`, `instance_lifecycle.py`, `graph.py`, `manager.py`). Estimated effort drops ~3–5h vs the TA's register.

### 1.3 Hidden coupling — none beyond known ordering constraints

Only ordering constraints exist (Worker A, commit-diff evidence): **PR1→PR3** (PR1's timing brackets make PR3's alist deletion a smaller diff; PR3 does NOT delete PR1's aget bracket), **PR2→PR3** (side table + repo property). PR4↔PR3: zero file overlap (`f89ccacc`/`7a7998fe` never touch `persistence.py`). PR1 is independent. PR2's intra-PR hunks ship atomically in `fa31a520`.

### 1.4 Tap-site drift — 4-site contract maps 1:1; no 5th surface

v2 has **one** `astream` invocation site (`instance_messaging.py:3929`), no new graph entry path since `58260f35`; the four v1 tap-site equivalents are locatable (dual-return `graph.py:3731-3732`, reactive compaction `:3583-3585`, messaging-compaction `~:1156`, entry `~:3747-3765`). Pre-port action (cheap insurance): re-grep `graph.astream|graph.ainvoke` in `instance_messaging.py` immediately before PR2 wiring — Worker A's flip assumption.

---

## 2. Open-Question Resolutions

### 2.1 Q1 — PG version parity: **PIN-PARITY ≥14.22** (not a hard execution blocker; a binding-validity blocker)

- The gate exercises three version-sensitive behaviors (Worker C): pipeline-mode two-implicit-transaction `aput` (PG14+; race window collapses to non-pipeline atomic path below), SERIALIZABLE/SSI 40001 abort-retry, and the bidirectional race test. On <14.22 the gate *runs and passes* but **validates a different regime than prod** — passing for degenerate reasons.
- Disposition: **hard-block <14.22 = rejected** (too strict); **version-conditional expectations = rejected** (matrix multiplication); **PIN-PARITY = ADOPT**: the *binding* run must execute on disposable PG ≥14.22 (same major.minor family as prod).
- **Phase 0 T0.3 addition (MUST-FIX):** `psql $POSTGRES_URL -c "SELECT version();" | tee phase0-pg-version.txt` + assert major.minor ≥14.22. Existing SKIP-LOUDLY contract ("do NOT merge PR4 or enable DESTRUCTIVE on a skip") already covers the unreachable case.

### 2.2 Q4 — Reviewer-instance re-review artifact: **design VALIDATED**, spec below

Design verdict: correct and necessary — independence (separate dispatched reviewer instance, never the implementer), SHA-anchored evidence, reviewer **personally re-runs the binding gate** (cannot merely cite the implementer's run).

Required contents of `.agents/reviewer/memories/2026-09-XX-pr4-blob-prune-race-fold-re-review.md`:

1. **Header** — date, branch, v2 PR4 cherry-pick SHAs, reviewer-instance ID, verdict stamp, SHA-reference to v1 NEEDS_CHANGES doc (`c37c870c:.agents/reviewer/memories/2026-08-26-pr4-blob-prune-deep-review-needs-changes.md`).
2. **Per-finding resolution table** — each original finding (1 🔴 aput non-atomicity race; 2 🟡 one-directional coverage, harness topology) → v1 fold evidence (`7a7998fe`: `TestRealSaverRaceWindow`, `TestRealSaverSerializableRetry`, runbook §7) → v2 post-port re-run evidence → status RESOLVED / NOT-RESOLVED / REGRESSED.
3. **9-item FR-8 verification matrix** — binding gate 9/9 on real PG; 40001 retry GREEN; bidirectional race byte-equal; aput retraction note citing `aio.py:82, 280-304, 393-399`; `_DELETE_RETRIES` in constants; structural-unreachability AST gate GREEN; `find_all_thread_ns_pairs` invoked; the 3 anti-join method signatures present; runbook §7 disclosure present.
4. **Re-run evidence** — binding gate re-run **by the reviewer** (timestamp, disposable-DB identity, pass count); AST/import gates may be cited.
5. **Verdict-stamp semantics** — `APPROVED` ⟺ every finding RESOLVED + 9/9 GREEN on reviewer re-run + zero NEW findings from the port-only delta scan.
6. **Sign-off fields** — reviewer-instance ID, artifact commit SHA, verdict stamp, reviewer model.
7. **Loop-closed semantics** — APPROVED (per #5) = loop CLOSED; any NOT-RESOLVED/REGRESSED = RE-OPENED → re-dispatch to developer with regression evidence.

### 2.3 Q8 — `message_metadata` seq-index: **DEFER the index; the row-growth prune is the real decision**

Evidence (Worker B, v1 sources): the *only* query shape is `get_for_thread(thread_id)` — filter on `thread_id` (covered by PK `(thread_id, message_id)` leading column + secondary `ix_message_metadata_thread`), **no ORDER BY**, `seq` is NULL in phase 1 (D5). An index on `seq` adds INSERT cost on every tap (2–4×/turn) and buys nothing today.

- **Decision framework:** revisit the seq-index only when (a) a consumer needs seq ordering (i.e., OOS-1 cursor pagination lands), or (b) `EXPLAIN ANALYZE` on `get_for_thread` at measured N (1k / 100k / 1M) shows degradation. Add-now cost = index bloat on an unbounded table (§3) — the worst possible substrate.
- **Recommendation:** DEFER with explicit trigger; **reprioritize D-2/T5.10** to measure row count + INSERT rate and ship the §3 prune. The seq-index question is academic until the prune exists.

### 2.4 Backfill (FR-14 / Solution N / PERF-4): **criteria corrected; expected outcome still DROP**

- **Criterion B is false on the merits**: `state.ts` fallback = `state.get("ts")` of the **latest** aget'd checkpoint — pre-side-table messages render with a uniform *last-update* timestamp, not first-appearance. Misleading but non-breaking (Worker B, `persistence.py:368-371` degradation contract).
- Live-path backfill is **unacceptable** — it reintroduces the O(N²) `alist` walk PR3 removes.
- **Corrected criteria (adopt):** DROP backfill iff (A′) fallback timestamps suffice for UI display (accepted degradation, non-breaking); AND (B′) no scheduled/batch consumer requires accurate first-appearance timestamps for pre-side-table history (none exists — `created_at` is the only consumer); AND (C′) the §3 row-growth defect is addressed by a prune, not backfill. If any fails: the **only** acceptable shape is a bounded, operator-initiated offline backfill via `daemon/migrations/checkpoint_migrator.py` (already exempt from the §33 alist ban), terminating in `get_for_thread`-style inserts with `ON CONFLICT DO NOTHING`.

---

## 3. 🔴 NEW FINDING (MUST-FIX before merge): `message_metadata` has no prune — unbounded growth

Architect-verified (spot-check confirms Worker B): `MessageMetadataRepository` has only `upsert_batch` + `get_for_thread` (no delete); `_cleanup_instance` (`maintenance.py:734-799` (v2); v1 anchor was ~`:817-882` (v1)) cascades instance-row → `adelete_thread` → callback, **never touching the side table**; Operations A–D never touch it; **no FK** to `instances` on either backend; pinned subtrees + revivable terminals make rows permanent. Growth ≈ 2–4 rows/turn × turns × instances, forever. This is the same defect class (unbounded table growth) that motivated PR4 — and the port **introduces it live** into v2 (side table absent today; confirmed greenfield). **Note:** `git diff 58260f35..2f80d45b` returns ZERO lines for `daemon/services/maintenance.py` — the body is byte-identical between v1 and v2; the v1/v2 line-range divergence is purely from v2 reshuffling surrounding code (which moved the function up by ~80 lines).

**Required fix (v2-new work, Phase 5 — keep the cherry-picks faithful to v1):**
1. `MessageMetadataRepository.delete_for_thread(thread_id) -> int` (PG + SQLite).
2. Wire into `_cleanup_instance` **after** `adelete_thread`, before the in-memory callback.
3. Real-PG acceptance test: populate → tap → assert N rows; `_cleanup_instance` → assert 0 rows + checkpoints gone.
4. Document deliberate non-action: Operation D checkpoint-prune orphans are tolerable (over-record-only, never join — per PR2 review §3).

---

## 4. Phase-Ordering Verdict — **CONFIRM 0→1→2→3→4→5**

- **Phase 4 before Phase 3? NO.** Both orders are mechanically safe (PR4 provably never touches `persistence.py`; the TA's "loose coupling" is actually zero file overlap), but the plan order wins on per-PR bisectability (a Phase-3 regression is unambiguously PR3's) and EASY-first risk sequencing. Phase 4 still delivers its independent, dry-run-default defect fix well before any destructive enablement.
- **No merges** (0+1 rejected — PR1's observability is Phase 2's visibility), **no Phase 5 split** (gate + process-closure share the reviewer dispatch; splitting multiplies review overhead).
- **Phase 0 additions (both MUST-FIX):** (a) **`data/ensemble.json`-DSN guard** — the current STOP-if-env-points-at-prod check covers only `POSTGRES_URL` env; `_build_pg_connection_string` also assembles a DSN by loading `data/ensemble.json` (or `data_dev/ensemble.json`) when env is unset (the repo-root `config.yaml` has NO `postgres:` block — verified), and `_ensure_postgres_columns` DDL fires on every manager init on that path. Add the DSN-assembly assertion (Worker C's snippet). (b) `SELECT version()` capture + ≥14.22 assert (§2.1).

---

## 5. Guardrail Checklist

| Guardrail | Status | Evidence / action |
|---|---|---|
| §33 saver never exposed to routers | ✅ PRESERVED | No phase task routes saver internals into `daemon/routers/**`; PR3's flip adds zero new imports to `persistence.py` (deletions only); v1 gate does import + `.alist(` call scan with empty allowlist. **MUST-FIX:** explicitly add the clean-add port of `tests/integration/test_no_saver_imports_in_routers.py` to Phase 3/5 task list — it is currently unlisted, leaving AC-7.1/7.2 with no test. |
| COMPLETED revive-on-send survives read flip | ✅ SAFE + add AC | Read path shares **zero code** with revive path (`_prepare_enqueued_message` :1505-1808 vs `get_instance_messages`); `aget` is a single SELECT (preserves quiescent `next=()` shape); synthetic-system id is deterministic per instance. **MUST-FIX:** add AC-13.3 read→revive→read test (§8 item 2). |
| Pause-first/quiesce | ✅ SAFE | Reads cannot observe torn two-transaction `aput` (only the PR4 prune is exposed to that window); tap bare-awaits with `except Exception` only (C-14) — no new await points from the flip. |
| PR4 atomic pair + SERIALIZABLE + retraction | ✅ PINNED | `TestRealSaverSerializableRetry` + `TestRealSaverRaceWindow` + 8-combo flag matrix + vocabulary grep #6; aio.py line cites verified against installed lib. All 6 safety elements SAFE at zero-drift insertion sites. |
| Bare-await taps / never `BaseException` | ✅ PINNED | AST placement gate + C-14. |
| RemoveMessage filtered before INSERT | ✅ covered, 🟢 not enumerated | `test_message_tap_slot.py` sub-cases exist; add explicit row to Phase 2 verification table (NICE-TO-HAVE). |
| ON CONFLICT DO NOTHING idempotency | ✅ PINNED | `test_message_metadata_repository.py` idempotency suite. |
| `message_api_checkpoint_list_total == 0` | ✅ PINNED | FR-2 observed-count + armed-absence + unit `assert_not_called(alist)`. |

---

## 6. Risk Register (go/no-go for implementation dispatch)

| # | Risk | Sev | Lik | Mitigation | Catching gate | Phase |
|---|---|---|---|---|---|---|
| R1 | 🔴 `message_metadata` unbounded growth ships with PR2 (§3) | High | **Certain** (by design, unfixed) | delete_for_thread + `_cleanup_instance` wiring + test as **merge precondition** | new real-PG test (Phase 5) | 5 |
| R2 | PR4 pair broken (f89ccacc without 7a7998fe) → 🔴 data-integrity finding returns | High | Low | cherry-pick PAIR atomically; no individual landing | `git log` consecutive-pair check + FR-8 re-review + race tests | 4+5 |
| R3 | aput non-atomicity + retraction lost in port | High | Low | verbatim port; never rephrase as "atomic" | vocab grep #6 + binding gate | 4 |
| R4 | `_ensure_postgres_columns` DDL touches prod via **config.yaml DSN path** (unguarded today) | High | Med | Phase 0 DSN-assembly guard (MUST-FIX #4) | Phase 0 T0.2 extended | 0 |
| R5 | manager.py anchor collision (`:6642` unrelated `message_metadata` kwarg) mis-wires repo property | Med | Med | anchor at `_db_connection_repository` block end; grep `message_metadata_repo` absence first | repo-liveness unit test | 2 |
| R6 | graph.py F2-hoist applied at stale site / tap inserted pre-aupdate | Med | Med | hoist at `:3731-3732` dual-return; tap AFTER `aupdate_state` | AST placement gate (4-site/4-label/no-ToolNode) | 2 |
| R7 | instance_lifecycle.py manual fix-up (~515-line churn, 4 slot constructions) | Med | Med | manual re-apply per corrected rule; run lifecycle-wiring test | `test_message_metadata_lifecycle_wiring.py` | 2 |
| R8 | PG <14.22 binding run passes for degenerate reasons | Med | Med | PIN-PARITY: version capture + assert in Phase 0 | T0.3 version stamp | 0+5 |
| R9 | Gate manifest v1 counts copied verbatim | Med | Med | regen per PR closure on v2 tip | manifest self-test | 5 |
| R10 | Stale TA conflict map misleads implementer (over-prepared for wrong files, under-prepared for PR2) | Med | Med | this document §1.2 supersedes; annotate TA | n/a (process) | 0 |
| R11 | Reviewer artifact authored by implementer (process gap persists) | High | Med | explicit reviewer-instance dispatch contract (§2.2) | FR-8 + sign-off fields | 5 |
| R12 | Residual READ-COMMITTED µs-racer during future destructive enablement | High | Low | idle-gate + runbook §6 backup + §7 disclosure | runbook checklist | ops |

**Verdict: CONDITIONAL GO** — dispatch implementation after applying MUST-FIX edits 1–7 (§8). No blocking unknowns remain; all three analyst reports landed with High confidence on load-bearing claims, and the two surprising claims (zero-drift hot files; unpruned side table) were independently verified by the architect.

---

## 7. Source-Doc Gap Triage (Solutions A–U)

**Absorbed free:** **Solution L** (write `created_at` at creation) — PR2's `MessageTapSlot` already writes `now_iso` at tap time (`message_tap.py:220-230`); the 4 sites cover all surfaced-from-checkpoint messages. Mark "absorbed by PR2" in OOS enumeration.
**Correctly out of scope:** B (event store), F (bounded window — compaction already covers LLM-context level), G, H (schema redesigns), O (cache — 33–114× flip makes it redundant; source doc §19 itself says cache only after read-path fix), P (replica — moves load, doesn't fix transfer), R (infra), S (custom checkpointer), T (hybrid saver), U (completed-run compaction — **conflicts with revive-on-send semantics**, high blast). Nothing else easy/small-blast/high-impact was missed.

---

## 8. Required Plan Edits

### MUST-FIX (before implementation dispatch)
1. **Add the §3 side-table prune** as a Phase 5 v2-new task (`delete_for_thread` + `_cleanup_instance` wiring + real-PG test) and mark it a **merge precondition**.
2. **Add AC-13.3** (read→revive→read on a COMPLETED instance): pre-revive snapshot byte-identical post-revive; new tail message has non-null `created_at`; `synthetic-system-{iid}` id identical both reads; `alist_count == 0` both reads.
3. **Add explicit clean-add port task** for `tests/integration/test_no_saver_imports_in_routers.py` (Phase 3 or 5) — currently unlisted; AC-7.1/7.2 otherwise have no test.
4. **Phase 0 T0.2:** add the `config.yaml`-DSN assembly guard (Worker C snippet) alongside the `POSTGRES_URL` env check.
5. **Phase 0 T0.3:** add `SELECT version()` capture + major.minor ≥14.22 assertion (PIN-PARITY, §2.1).
6. **Correct the TA per-file conflict rules** per §1.2: persistence.py / maintenance.py / checkpoint_adapter.py / repositories → ZERO-conflict (byte-identical, triple-verified); manager.py anchor moved off `:6642`; instance_messaging.py targets re-anchored (+~335 lines); correct the "gates require per-PR boundaries" rationale to provenance/revertibility/pair-semantics.
7. **Replace FR-14 backfill criteria** with A′/B′/C′ (§2.4) + document offline-`checkpoint_migrator.py`-only shape.

### NICE-TO-HAVE
8. Trim perf matrix 5×3 → 4 targeted cells: (1,10000), (10,1000), (100,10000), (1000,100) — saves ~11 runs, keeps the cost-∝-page-size proof at both extremes.
9. Vocabulary grep guards 6→3 (drop #2/#3 as duplicated by the mission program's M2 final-gate) — **conditional on verifying the M2 gate exists as canonical detector** (Worker A inferred, did not read QUARANTINE.md).
10. Enumerate the RemoveMessage-filter sub-cases of `test_message_tap_slot.py` in the Phase 2 post-port verification table.
11. OOS enumeration: mark Solution L "absorbed by PR2" (`message_tap.py:220-230`); one-line rationales for B/F/G/H/O/P/R/S/T/U per §7.
12. D-2/T5.10 rewrite per §2.3 (defer seq-index with explicit trigger; prune measurement instead).
13. Worker A proposed cutting the Phase-0 v2-base gate-suite pre-count (OQ5) — **architect disagrees, KEEP**: it is the attribution baseline distinguishing "port changed counts" from "base drifted"; one cheap task. Cut instead the OQ4 destructive-timeline re-affirmation (already OOS).

---

## 9. Confidence & Flip Assumptions

- **High** (triple-verified): zero-drift hot files → PR1/PR3/PR4 verbatim replay; PR4↔PR3 file disjointness.
- **High** (dual-verified + architect spot-check): unpruned `message_metadata` (§3); read/revive path separation.
- **High** (single-worker, evidence-cited): tap-site 1:1 mapping (flip: a second `astream` site appears — re-grep pre-PR2); Q1 pin-parity; Q4 artifact spec.
- **Medium**: grep-guard cut (assumes M2 final-gate existence); perf-matrix cell sufficiency; prune threshold values (measure first per §2.3).

## Gaps

None — fan-in complete (3/3 workers reported; no re-dispatches; no skill-bank misses on Workers A/C; Worker B's missing first-line confirmation noted in the attribution note above with claims independently verified).
