# Memoir Store v1 — Phase C Code Council Record

- **Date:** 2026-11-05
- **Commission:** CODE COUNCIL — deep, security-focused review of the Phase C implementation; judge the code against the approved plan set; do not re-litigate adjudicated decisions.
- **Council:** governor `55f9b6ec-cb61-4ee2-9e8d-9bf599b66dea` (`memoir-code-council`), 2 councilors (worker agent; models `agentic` + `coding` — full available diversity), skill `code-review`, 2 rounds (dispatch + one targeted refinement). Read-only directive held on every dispatch; no write violations; councilor scratch was `/tmp`-only and self-deleted.
- **Record:** written and committed by the reviewer controller (dispatcher/aggregator); councilors produced findings only.

## Scope

- Worktree `/home/nea/ensemble-src-wt-memoir-store`, branch `feature/ari-memoir-store`.
- **Reviewed diff:** `5dc08aca8..d14702b40` — 23 commits, 75 files, +17,309/−64.
- **HEAD at record time:** `1256f95df` — three docs-only V-1 ruling commits above `d14702b40` (`affcda4c7`, `29e4d7752`, `1256f95df`; touch only `architecture-recommendation.md`, `decisions.md`, `phase8-plan.md`). Code files identical; this review stands at both.
- Approved plan set: `.agents/shared/planning/ari-memoir-store/` (plan-overview + phase0–8 + design + decisions §4 17 rows + architecture-recommendation 43 AMs/6 DRs + council-findings + approval). Adjudicated decisions were not re-litigated; disagreements with an adjudication are marked "adjudicated — not re-litigating" where applicable (none materialized).

## Verdict: **REJECT**

One blocking correctness defect: `memoir_recall`'s `k` parameter is validated at two layers and applied at neither — recall returns every budget-fitting episode regardless of `k`, reproduced empirically by both councilors independently at the agent-visible tool surface, with no existing test able to see it. Everything else examined holds; this is a small fix plus a discriminating pin, not a redesign. **This is NOT a security rejection** — the priority-1 privileged/security surface came back clean on both independent passes (see §Security).

**Counts: 1 CRITICAL / 3 WARNING / 9 ADVISORY** (+1 council-process retraction, +3 councilor-accepted-no-action notes).

---

## CRITICAL

### C-1 — `k` is inert end-to-end in `memoir_recall`
- **Anchor:** `daemon/services/memoir_retrieval.py:370` (dead `count = self._validate_k(k)` — never read before the return); `daemon/services/memoir_retrieval.py:731-737` (`_apply_budget` takes no `k`/`count`, stops only on the token budget); `:390` passes the FULL ranked list; tool layer renders ALL `result.episodes` (`daemon/tools/memoir.py:989-1006`). Blessed signature (`daemon/tools/memoir.py:593` — "default 5, hard max 20") and D2.10/D2.4 violated.
- **Reproduction (both councilors, independent):** in-memory SQLite, 8 rows — service `k=2`→8 and `k=1`→8; shipped factory tool `ainvoke({"k":2})`→8 blocks, header "8 episode(s), 0 dropped for budget".
- **Why no test sees it:** the existing budget pack uses `k=8` over exactly 8 rows — structurally insensitive to `k`.
- **Fix:** slice `ranked[:count]` before `_apply_budget`; add a pin asserting `len(result.episodes) <= k` over a corpus strictly larger than `k`.

## WARNINGS

### W-1 — Keepsake admission measures the wrong string (zone arithmetic unenforced)
- **Anchor:** `daemon/repositories/memoir/repository.py:644-651` + `:717-724` vs `daemon/services/memoir_briefing.py:80`.
- **Issue:** admission estimates `render_episode_content(title, body)` while the shipped line is `KEEPSAKE_LINE_TEMPLATE` (`- [keepsake] {title} (pinned_by=…; provenance=…) {pinned_reason}`): fixed ≈+18 tokens/pin (≈216 at the 12-pin cap) plus unbounded `pinned_reason` (no cap in `_validate_pin_claim`, `:1301-1322`; measured +269 t at 2,000 chars). D5.7/D2.12's "the pinned template is the input to `est(candidate_render)`" is false, so the DR-1 24k/48k zone arithmetic is unenforced — ordinary tool use can reach the overage/withhold rungs.
- **Fix:** measure the rendered keepsake line in admission (or cap `pinned_reason`) and disclose the delta.

### W-2 — Embedding worker head-of-line stall: silent permanent enrichment stop
- **Anchor:** `daemon/services/memoir_embedding_worker.py:425-436` + `daemon/repositories/memoir/repository.py:825-841`.
- **Issue:** scan window is the OLDEST `SCAN_LIMIT=200` pending rows (`ORDER BY created_at ASC, id ASC`); terminal rows still satisfy the SQL predicate (`record_embedding_attempt` never sets `embedded_at`) and are filtered only in memory; no offset/skip/advance exists (grep empty). Once ≥200 terminal rows occupy the head, `candidates == []` every tick — `pending>0 / embedded=0` forever, no error; `_count_pending` subtracts terminal so the tick line looks alive.
- **Severity calibration (reviewer ruling):** kept WARNING, not CRITICAL — no loss, corruption, or exposure (rows remain pending and recoverable; lexical recall unaffected; not a security/data-integrity finding). Both councilors flag it as the strongest warning; the agentic councilor notes it borders CRITICAL at a "no silent permanent stall" bar. It is REQUIRED in the fix cycle regardless.
- **Fix:** terminal-aware SQL predicate or advance-past-terminal; plus a WARNING when `pending>0 and embedded==0` for N consecutive ticks.

### W-3 — Phase-7 "mutation matrix" driver is vacuous as mutation evidence
- **Anchor:** `test/packs/memoir_mutation_matrix_test.py:1-86` (subprocess re-run at `:38-46`, loop `:74-80`; no `write_text` anywhere in the file); artifact `mutation-matrix-digest.json`.
- **Issue:** the driver plants NOTHING, parses no `FAILED <node>` ids, asserts no RED→GREEN; it re-runs 10 pytest files and digests `{label: {rc, summary}}`. Its PASS is green whenever the suites are green — even with every load-bearing guard deleted. Therefore **not** T7.2/AC2 mutation evidence; 4 core modules (briefing / budget / retrieval-arithmetic / schema-WP) have no in-pack leg.
- **Fix:** implement plant/red-marker/restore/green-re-run (the genuine sibling `metrics_read_mutation_matrix_test.py:608-630` is the template), or rename the artifact and cite the per-phase leg tables. Fix or rename **before any ceremony text claims a proven mutation matrix**.

## ADVISORY

- **A-1** `test/packs/memoir_{knob,persona_lane,schema,seed}_unit_test.sh:27-31` — four packs silently fall back to bare `python3` (the other eight fail loud); on this worktree `.venv` exists so the correct interpreter is selected and a wrong one surfaces as import/collection failure, **not** a false green. Fix: make them fail loud.
- **A-2** `daemon/tools/memoir_knob.py:300-330` — `resolve_memoir_knob(None)` with env unset labels `source="config"` though no config tier was consulted; behavior matches the default-ON row, label is wrong.
- **A-3** `daemon/repositories/memoir/repository.py:693-753` — `set_pin`/`clear_pin`/`mark_recalled` keyed on episode id with no `agent_id` predicate (safe in single-agent v1; becomes a cross-agent write surface the moment a second agent gets `memoir`); `memoir_unpin` also loads the whole set to find one id.
- **A-4** `daemon/tools/memoir.py:238-241` — `body_required` maps to `memoir_body_to_long`: caller-input error under a semantically wrong code (label-precision class; NOT an internal-error masquerade — see §V-1).
- **A-5** `daemon/services/memoir_retrieval.py:430-440` + `repository.py:948-975` — timeline `q` path reads the window twice (full row scan, then a second windowed lexical rank).
- **A-6** `daemon/api.py:5766-5778` — unauthenticated `/readyz` echoes `detail.memoir.rejected_value` verbatim (raw operator-typed env string); two stray blank lines in the lifespan block.
- **A-7** `test/packs/memoir_briefing_unit_test.sh:95-101` (same shape in sibling packs) — GREEN-AT-ZERO guard asserts `N>=1`, not the registered exact count (digest's exact counts mitigate).
- **A-8** `daemon/repositories/memoir/seed.py:88-96` — docstring says "pointer row OR any version ⇒ nothing written"; code checks versions only (the safer behavior — fix the prose).
- **A-9** `daemon/repositories/memoir/repository.py:147` — the persona human-lane guard **does** raise `HumanLaneRequiredError` (verified at the raise site), but `approve`/`reject` have no production callers in v1 (persona read-only) — guard is exercised by tests only; cover on first v2 caller.

**Councilor-accepted, no action (recorded, not counted):** 24k/48k ceiling as constants (DR-1 deliberate); module-level executor sizing (plan's count-not-concurrency doctrine); tsv/GIN ensure block inside the single-transaction list (house fail-loud; per-statement isolation already routed as a house-pattern change).

---

## Re-derivation table (independent of the dev report)

| Claim | Result |
|---|---|
| MT-01 matrix PASS | **NOT REDERIVED as a matrix** (the real instrument plants mutations; fenced). Verified by **inspection + static pin**: lockstep find `== 18` / replace `== 17`, never both 18 (`metrics_read_mutation_matrix_test.py:92-93`); `find != replace` pinned (`test_memoir_registration.py:332-341`, executed green). The coding councilor's Round-0 "REDERIVED" via the memoir driver was **vacuous and withdrawn** (§Disagreements). |
| Lane matrix driver PASS | **REDERIVED in part** — the 10 underlying suites executed directly: all green; per-file collected counts match the digest exactly (43/27/17/10/38/14/8/18/14/25). True as a suite re-run; meaningless as mutation evidence (W-3). |
| Tools pack + 5 mutation legs | **REDERIVED (executed)** — `memoir_tools_unit_test.sh` → PASS (baseline + 5 legs); read-verified discriminating (anchor-count guard, `FAILED <node>` capture, vacuous-red/timeout rejection, restore-then-prove-green). |
| Registration family 223 | **REDERIVED in part** — 219 passed across 10 registration/leak files + `test_memoir_registration.py` 20 passed; independent greps agree (5 inline exact-set, 6 expected-set defs, 7 `== 18` pins, 1 ari trailing-set pin; `"memoir"` in exactly one `meta.json` — ari). Residual nodes live in two integration-marked files not executed (fenced); **zero reds found**. |
| 161/161 prompt baseline | **REDERIVED** — `generate_prompt_artifact_baseline.py --check` → "PROMPT-BASELINE: OK — 161" (both councilors). |
| Guardrail sweep | **REDERIVED in part** — diff contains no `pyproject.toml`/`uv.lock`/`plugins/**`/`frontend/**` change; `test_tool_filter.py` 55 passed; privileged-category log green. Whole-tree sweep fenced. |
| Non-ari byte-identity (inner_soul) | **REDERIVED** — redirect pack executed (baseline + 3 mutation legs, PASS) AND both surviving call sites read-verified: non-ari path keeps the identical `_format_rag_redirect(...)`; only `agent_id == "ari"` branches. |
| Knob truth table / kill-switch DR-3 | **REDERIVED** — 38 unit tests green + live enumeration: unset→ENABLED(default), `""`→ENABLED, `false`/`0`/`no`/`off`→DISABLED, `banana`→DISABLED/indeterminate-with-rejected-value; garbage never raises, never enables. |
| RRF arithmetic | **REDERIVED numerically** — competition ranks `{a:1,b:2,c:2,d:4}`; fused sums exact; absent ranker contributes no term; `exp(−1)=0.367879`; prior on non-keepsakes only. |
| Migration SQLite-valid/idempotent | **REDERIVED** — UP section executed on sqlite3 3.53.1: 4 tables, second run clean. |
| Keepsake admission precedes insert | **READ-VERIFIED** — `_admit_pin` inside the session before the single `_MEMOIR_INSERT_STATEMENT`; no second statement (but see W-1: it measures the wrong string). |
| Three-step registration real / ari-LAST | **READ-VERIFIED + grep** — decorator sets `_tool_category` + marker; `CATEGORY_MODULES["memoir"]` AST-discoverable; category extension precedes help-build/scan/`_apply_tool_filter` (`instance.py:6695→7213/7218/7221`); ari `meta.json` last allow entry is `"memoir"`; no dynamic-skill/inheritance over-grant found. |
| ts_rank_cd order / two-arg to_tsvector | **CONFIRMED by both** — `ts_rank_cd(tsv, plainto_tsquery('english', :q))`; immutable two-arg `to_tsvector('english', …)`. |
| Real-PG legs / boot smoke / Tier-2 whole-tree | **NOT REDERIVED** — fenced by design (PG-cluster contact, frozen-binary boot, OOM-risky whole-tree). Routed to the tester's own gates; remain unverified by this council. |
| **NEW — `k` contract** | **REFUTED the lane's implicit claim** — no pack asserts `len(episodes) <= k` anywhere; budget pack structurally insensitive (C-1). |

## Ruling on the dev's five adjudications

| # | Adjudication | Ruling |
|---|---|---|
| (i) | `agent_id` predicate in the PG lexical SQL | **In-mandate** (unanimous; repository-scoped bound parameter from factory-captured identity, `repository.py:471-486`). |
| (ii) | `MemoirRepository` constructed per assembly | **In-mandate** (unanimous; wraps the caller's engine, never builds one, never reads ambient `POSTGRES_*`, `memoir_briefing.py:441-447` / `daemon/tools/memoir.py:_memoir_repository_for`). |
| (iii) | Dynamic zone excluding keepsakes | **In-mandate** (unanimous; already recorded `phase-notes-p5.md:185-188`). |
| (iv) | Readyz advisory injected at the route | **In-mandate** (unanimous; post-verdict injection is what makes advisory-only structural, `api.py:5766-5783`). |
| (v) | The "semantic wiring" (default embedder resolved inside the service) | **Split 1-1, RESOLVED by the reviewer: record `decisions.md` §4 row 19.** Both councilors agree the code is acceptable; the divergence is bookkeeping only. It changes production recall behavior (default 5s network embed inside an agent turn) and belongs in the §4 ledger (next free row 19), especially since row 18 was just used for the V-1 ruling. The ledger row is the cheaper, invariant-preserving resolution. |

## Disagreements and process notes

- **Round-0 split — RESOLVED by evidence, not by vote.** `agentic`: REJECT (1C/3W/8A). `coding`: APPROVE-WITH-ADVISORIES (0C/0W/5A). The split was an evidence asymmetry: the coding councilor had not examined `k` and had accepted the phase-7 driver's PASS at face value. In the targeted refinement, asked the same four questions against the same lines, the coding councilor confirmed all four points with its own independent `k=2`/`k=5` → 8-episode reproduction and **withdrew** its verdict: "REVISED VERDICT: REJECT — my earlier APPROVE-WITH-ADVISORIES is WITHDRAWN. The parallel councilor is right on all four points." **Final position: unanimous REJECT (2/2).** Residual split is only adjudication (v) — a records question (§ above).
- **Council-process retraction (behavioral, not a code finding):** the coding councilor's Round-0 "MT-01 matrix PASS — REDERIVED" was vacuous evidence (a passing suite re-runner cited as a mutation matrix), explicitly withdrawn in Round 1. Lesson for the record: **a digest over return codes proves determinism, not guard pinning.** The MT-01 lockstep itself remains verified by inspection + executed static pin.

## V-1 (input error → internal-error code), excluding the known empty-query case

**None found.** Both councilors independently enumerated every raise site (`MemoirWriteError`/`MemoirRecallError`/`MemoirAdmissionError`) and cross-checked `_WRITE_CODE_MAP`/`_RECALL_CODE_MAP`: the full mapped set is body_required, body_to_long, title_to_long, invalid_title, importance_out_of_range, event_at_invalid, invalid_stars, too_many_stars, star_to_long, episode_not_found, invalid_actor, reason_required, k_out_of_range, invalid_window. `query_required` is the sole unmapped code — the adjudicated V-1 instance (user-ruled 2026-11-05: authorize the 19th code `memoir_query_required`, identity-mapped from the service's `query_required`; lands in this council's fix cycle; acceptance = tools pack green + `len(MEMOIR_CODES) == 19` grep census, no MT-01 re-run, no prompt re-stamp). Nearest class-adjacent residue is `body_required` → `memoir_body_to_long` (A-4) — a mislabel, not an internal-error masquerade.

## Scope / ceremony verdicts

- **Creep: none.** Touch set is memoir-specific + required wiring (manager, api, config, inner_soul redirect, registry); other agents' tool sets untouched; RAG/loader semantics untouched beyond the ari `inner_soul` branch.
- **Dependencies: zero** (`pyproject.toml`/`uv.lock` absent from the diff).
- **Registration ceremony: clean.** Privileged set = 18, `memoir` is entry #18, ari-EXACTLY (independent grep: one `meta.json`); 18-site/11-file census independently reproduced; MT-01 lockstep verified by inspection + static pin; 161/161 baseline re-derived; CHECK constraints bound to the model (`__table_args__`); migration additive, SQLite-valid, registered before `create_all`; rollback is fix-loss only.

## Security summary (priority-1 focus)

The privileged-category surface is **clean on both independent passes**: three-step registration real (not decorator-only); two-layer gate (category + factory-captured `agent_id` in-body) unbypassed; auth-first ordering (AM-16) holds; kill-switch fail-closed through garbage values (DR-3); NO caller-supplied identity or scoping keys anywhere (the `list_context`/`read_context` cross-tree anti-pattern is absent); no cross-agent reachability (incl. dynamic-skill/inheritance); persona human-lane guard raises (`HumanLaneRequiredError` at the raise site).

## Follow-on requirements for the re-approval pass

1. **C-1 fix** — slice `ranked[:count]` before `_apply_budget` + discriminating pin (`len(episodes) <= k`, corpus strictly larger than `k`).
2. **W-1 fix** — keepsake admission measures the rendered `KEEPSAKE_LINE_TEMPLATE` line (or cap `pinned_reason`) + disclosed delta.
3. **W-2 fix** — terminal-aware scan predicate or advance-past-terminal + N-tick stall WARNING.
4. **W-3 fix-or-rename** — real plant/RED/restore/GREEN matrix (sibling template `metrics_read_mutation_matrix_test.py:608-630`) or honest rename; no ceremony text claims a proven mutation matrix until then.
5. **V-1 implementation** per the user ruling (19th code; acceptance per ceremony note).
6. **Records:** `decisions.md` §4 row 19 for adjudication (v).
7. Re-run tools + retrieval packs after the fix cycle; advisories may ride.

## Record provenance / disclosures

- This record was written and committed by the reviewer controller; the council produced findings only (read-only).
- Worktree HEAD at record time is `1256f95df` (docs-only V-1 ruling set above the reviewed `d14702b40`; code identical).
- A pre-existing modified file `.agents/shared/planning/ari-memoir-store/mutation-matrix-digest.json` was present in the worktree porcelain **before this record's commit** — not touched by the reviewer; owner lane re-applies/owns it. Only `code-review.md` is added by this commit.
