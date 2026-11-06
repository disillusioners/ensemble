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


---

# DELTA — Fix-cycle re-approval (Round 2 of 2)

- **Date:** 2026-11-05
- **Council:** same governor `55f9b6ec-cb61-4ee2-9e8d-9bf599b66dea`, same 2 councilors (`agentic` + `coding`), revived with Round-1 context intact, skill `code-review`, both completed; read-only discipline held (only reviewer-authorized scoped packs and plant/restore probes; all restored; porcelain 0).
- **Scope (delta only — Round 1 stands as recorded above):** verifies the fix cycle answering Round 1's REJECT. Code: `d278ee71c` (C-1 apply k + pin) · `25ef71634` (W-1 rendered-line admission) · `f012b4787` (W-2 terminal-aware scan + stall warning) · `4e30a4f6c` (V-1 19th code, user-ruled) · `4d5305422` (real plant/restore mutation matrix) · `b925d34eb` (degradation-pack plant follows the W-2 single-sourced predicate). Docs: `29e4d77` (V-1 ruling), `1256f95df` (ceremony fold), `31c246be` (decisions §4 row 19), `b60502671` (Phase 8 ceremony notes — in-turn query-embed disclosure). **Evaluated HEAD: `b60502671`** — docs-only above `31c246be7`; code identical. Fix-cycle delta vs reviewed tree: 17 files, +1,477/−201.

## Verdict: **APPROVE-WITH-ADVISORIES** (delta) — unanimous 2/2

All four Round-1 code findings are closed with **executed** plant→RED→restore→GREEN evidence from both councilors independently; the matrix is now a real instrument with a deterministic committed digest; the only new item (an unpinned Python belt) is genuinely non-blocking defence-in-depth. **The tree is gate-ready for tidier + tester.** A-4 is carried into the ceremony notes.

## Per-item verification

1. **C-1 `k` — EXECUTED (both).** `count` threaded into `_apply_budget` (`memoir_retrieval.py:396`, signature `:740`); slice `candidates[:count]` runs **before** the budget loop (`:757-761`); `dropped` rebased on `candidates`; timeline passes nothing ⇒ `count=None` (`:458`). Pin (`tests/unit/test_memoir_retrieval_unit.py:450`) uses **12 rows / k=5**, bodies small so the envelope never binds. Both councilors planted the slice away: RED on `test_k_caps_the_shipped_episode_count_on_a_larger_corpus` (`1 failed, 27 passed`), restored → `28 passed`, file sha identical pre/post (`563715630f9ae86c`), porcelain 0. **Discrimination confirmed on a corpus larger than `k`** — the old pack was silent because its largest corpus was 4 rows at k=5.
2. **W-1 keepsake admission — EXECUTED + READ-VERIFIED.** `KEEPSAKE_LINE_TEMPLATE` / `MEMOIR_UNTITLED_TITLE` / `KEEPSAKE_ADMISSION_PCLASS` / `render_keepsake_line()` single-sourced in `repositories/memoir/models.py:407-476`; admission measures the **rendered** line at `repository.py:702` (insert) and `:784` (`set_pin`), held pins at `:1152`; briefing **delegates to the same function object** (`memoir_briefing.py:213-221`) — no second implementation. Import direction correct (models → sqlalchemy/sqlmodel/infra types only; no cycle). Oversized `reason` (5,000 words) ⇒ `MemoirAdmissionError(constraint="keepsake_zone_tokens")` with **original pin intact, count 1→1** — reject, never clamp/evict. Zone worst-case pclass `25 ≥ {24,25,25}` ⇒ DR-1 bound holds. Briefing template bytes **byte-identical** to the pre-fix literal. Packs 56 green.
3. **W-2 embedding stall — EXECUTED + READ-VERIFIED.** One module owns both legs (`repository.py:592` SQL predicate, both operands `coalesce`d; `:621` Python belt); scan applies `~predicate` (`exclude_terminal=True` default, `:930`); `_count_terminal` (`worker:790-810`) uses the same predicate — the inline `text()` SQL is gone; `_is_terminal_row` (`:300-306`) is a thin delegate. Stall warning `MEMOIR_EMBED_STALL_WARN_TICKS=3` (`worker:519-546`), re-armed by a healthy tick. Executed: fresh 5-attempt terminal row ⇒ `_count_terminal()==1`, scan returns **0 rows**; both new pins green; worker + degradation packs 45 passed.
4. **W-3 matrix — EXECUTED (coding, twice) / READ-VERIFIED + two legs executed (agentic).** Real engine: independent plant on production source, RED required **with the intended node** (`re.search(rf"FAILED {guard_path}::(?:\w+::)?{marker}")`), in-memory byte-copy restore, GREEN re-run, per-leg porcelain before/after, in-flight journal with `--recover`, post-run manifest verify. **7 legs** (k-slice, admission-after-insert, stall-warning, v1-recall-map, knob-garbage, non-ari-redirect, keepsake-template). Digest `7072f23224fc7bbb` records all 7 `OK` / `red_rc=1` / `green_rc=0` / `restore_byte_identical=True` / `porcelain_clean_after=True`; green node counts `28/14/37/43/38/(script)/17` match independent collections. **Determinism:** two consecutive runs byte-identical; `sha256(on-disk) == sha256(git show HEAD:…digest)` — a re-run does **not** dirty the committed file. The lane's prior lockstep claim was **not trusted**: the M6 plant independently confirmed to target the **SQL form** (`test/packs/memoir_embedding_degradation_test.sh:103-124`), not the belt.
5. **V-1 — EXECUTED.** `MEMOIR_CODE_QUERY_REQUIRED` + frozenset member + `assert len(MEMOIR_CODES) == 19` (`memoir.py:182/206/210`) + `_RECALL_CODE_MAP["query_required"]` (`:260`); test renamed `…freezes_nineteen_codes` (`:171-172`); empty-query test **inverted** to assert the true code (`:408-425`). **Exactly two count-bearing literals**: `daemon/tools/memoir.py:210` and `tests/unit/test_memoir_tools_unit.py:172` (grep-confirmed). Matrix leg 4 executed: map entry present ⇒ `query_required`; popped ⇒ `memoir_internal_error`; restored ⇒ `query_required`; porcelain 0. Tools pack 43; prompt baseline **161/161 OK**. **Zero metrics surface** (`git diff --name-only | grep -i metric` = 0). **MT-01 not run and not owed** — refusal set is module-local; `PRIVILEGED_TOOL_CATEGORIES` untouched at 18.
6. **Judgment calls + A-4 — see below.**
7. **Porcelain + boundary — EXECUTED / READ-VERIFIED (and independently re-confirmed by the reviewer).** Porcelain 0 at every checkpoint (non-zero samples were the other councilor's live plants, each reverting to 0). HEAD `b60502671`, one docs commit past `31c246be7`; code identical. Branch **not pushed** (no `refs/remotes/*memoir*`). No metrics surface. **Zero** boot-recovery / `discard_on_startup` / `hard_reboot` / `message_queue_boot` files in the diff. Semantic wiring unchanged (retrieval diff is the k-slice only). `decisions.md` row 19 read: (i)–(iv) in-mandate unanimously, **(v) recorded as CODE ACCEPTABLE** — default query embed resolved in-service, ~5 s bounded and degrading — matching the Round-1 reviewer ruling.

## Judgment calls

- **(a) Coder replaced the suggested belt-plant with the SQL-form plant — argument SOUND, empirically proven.** Planting `memoir_row_is_terminal → False` alone: **37 passed, zero reds** — the belt plant is genuinely vacuous because the SQL exclusion keeps terminal rows out of the window, so the skip assertion still holds. Planting the exclusion off turns `test_pending_rows_behind_a_head_of_terminal_rows_are_still_found` RED. Independently reproduced (faithful M6 simulation by the second councilor). The correction of the dev's suggested target is right, and it is now evidence, not argument.
- **(b) Moved RED trip point still meaningful — YES, with an honest caveat.** The planted defect is *"the terminal predicate decides terminal rows are not terminal"*; the count goes 1→0 at the single-sourced predicate — that guard's own observable output — and the pack header documents the move. The exclusion behavior has its own discriminating pin (independently proven RED by disabling the exclusion). The defect class is therefore covered by **two** discriminating tests; the single M6 leg proves the count wiring, not the exclusion wiring directly — a **coverage-shape note, not a defect**. Consequence: the behavioural escape (re-embed forever) now needs **two** reverts (SQL + belt) — defence-in-depth working, producing advisory N-1.
- **A-4 (`body_required → memoir_body_to_long`) — ACCEPTABLE to leave, must be carried forward.** Not an internal-error masquerade; message text truthful; a 20th code would re-open the user's frozen 19-code ruling for zero agent-visible gain. Decisive nuance: the **agent-visible path never emits it** — `memoir_write`'s own gate refuses empty/whitespace text pre-DB (executed: `''` and `'   '` both return the tool-level code); the repository branch is reachable only by direct callers/tests. **Both councilors require it be recorded in the Phase-8 ceremony notes** (today it lives only in the Round-1 record).

## New findings (introduced by the fix commits)

- 🟡 **N-1 — the Python belt is unpinned** (`daemon/repositories/memoir/repository.py:621`, `daemon/services/memoir_embedding_worker.py:298`). Executed: belt plant alone produced **0 reds in 37 nodes**; every terminal-row assertion is satisfied by the SQL exclusion alone, so the "two-gate" design has one *pinned* gate. **Non-blocking** (the single-sourced predicate makes drift impossible; the SQL leg is the reachable trip). Fix: one pack node calling `get_pending_embeddings(exclude_terminal=False)` and asserting the belt still skips the terminal row.
- 🟢 **N-2 — footer disclosure precision** (pre-existing, not introduced): `missing_embedding` / `embedding_excluded` count over the candidate pool while `episodes` is now k-capped, so the footnote can report more missing-embedding candidates than shipped episodes. Cosmetic; label or bound when convenient.
- 🟢 **Records — A-4 not yet in `phase8-plan.md` / ceremony notes** (both councilors' one action advisory).

## Carried-forward Round-1 advisories (unchanged, non-blocking)

Four packs still fall back to bare `python3` (`memoir_knob` / `persona_lane` / `schema_unit` / `seed`); `resolve_memoir_knob(None)` labels `source="config"` with no config tier consulted; id-only `set_pin`/`clear_pin`/`mark_recalled`; the 48k withhold path via long reasons (now bounded by admission); `memoir_timeline` double window read; unauthenticated `/readyz` echoing `rejected_value`; GREEN-AT-ZERO `N>=1` rather than exact counts; seed docstring wording; persona guard has no production callers in v1.

## Council-process disclosure (not a code finding)

The two councilors ran **concurrently in the same worktree**, each planting/restoring mutations in shared source files — a real corruption risk (one party's plant can poison the other's restore baseline). Evidence it did not corrupt the result: digest byte-identical across two full runs; on-disk digest equals the committed digest after both runs; each councilor verified its own planted file restored to its pre-plant sha; final porcelain 0 at `b60502671`. **Rule for future delta passes: serialize mutation-running councilors rather than parallelize them; tidier should re-verify `git status --porcelain` at handoff as a cheap belt.**

## Gate readiness / boundary

**Gate-ready for tidier + tester: YES.** No blocking delta finding; all four Round-1 code findings closed with executed evidence; matrix real, deterministic and committed; tree clean at `b60502671`; branch unpushed; zero metrics or boot-recovery drift; scope and ceremony integrity intact. **Cite `b60502671`** (not `31c246be7`). **Carry into ceremony notes: A-4, N-1, and the four-pack `python3` fallback.**

## Record provenance

Append-only continuation of the Round-1 record above (unchanged). Reviewer-verified at evaluation time: HEAD `b60502671`, porcelain 0, `code-review.md` untouched since `003966f4c`, zero metrics/boot-recovery files in the fix diff, no remote memoir ref.


---

# DELTA-2 — Tidier-cycle re-review (Round 3 of the review arc)

- **Date:** 2026-11-05
- **Scope (delta only — Round 1 and DELTA-1 above stand as recorded):** the tidier cycle answering the tidier's 49 findings — `5324d4beb` (Batch A: gate helper, dead validation, fail-loud packs, annotations; 19 files) · `af013ed20` (Batch B: recall observability, query-dims guard, fail-closed knob, honest failure classes; 9 files) · `dd8b6cd06` (Batch C: post-commit honesty, per-read briefing guards, stats-write guard; 9 files) · `bed22dc6d` (docs) · `0ff59023e` (digest refresh: 9/9 lines, counts+hash only, no timings). **Evaluated HEAD: `0ff59023e`, porcelain 0, branch unpushed.**

## Council-process record (disclosed)

The council governor (`55f9b6ec`) died twice on the provider-side `insufficient_balance` error (coding-plan quota exhausted + empty wallet; the known D7 proxy-defect family — third lane impact today). Recovery ladder followed: one revive (failed identically) → one replacement per the ladder. **This delta was verified by a SINGLE replacement reviewer** (`b29f386c`, worker agent, model `agentic`, skill `code-review`; ran 23:15–23:39Z with zero provider failures) — single-reviewer caveat applies to the full delta. **Mitigating corroboration:** the interrupted council's own `agentic` councilor (council manifest, round 3) had already COMPLETED with the identical gate-blocking finding (same file, same lines :244/:253, same mechanism) before the governor died — so NEW-1 below carries **two independent model sources**, and the reviewer independently confirmed both pin literals present at HEAD. The governor's replacement mutation runner (`29a56b20`) errored on the same provider class and is NOT running; no orphan planter (porcelain 0 verified before record-write).

## Verdict: **REJECT**

Solely on **NEW-1**: the H1 refactor orphaned two static shape-pins, producing **2 net-new deterministic reds at HEAD** (`tests/unit/test_memoir_registration.py`: 2 failed / 195 passed across the memoir unit corpus) — the lane's own net-new-reds=0 doctrine makes this gate-blocking. The properties the pins guarded HOLD (behavioral gate-order pins green); the pins are stale in FORM only. All ten briefed items otherwise verify, several with executed plant→RED→restore→GREEN evidence. **One mechanical test-side re-point away from APPROVE-WITH-ADVISORIES.**

## Per-item verification

1. **H1 gate helper — EXECUTED/READ-VERIFIED.** `_gate(label, action)` (`daemon/tools/memoir.py:319`) called by all five bodies (:750 write, :863 pin, :962 unpin, :1029 recall, :1079 timeline). Pre-image (`5324d4beb^` :658/:772/:878/:963/:1045) vs post: identical refusal codes; `AGENT_UNKNOWN`/`NOT_AUTHORIZED` remain DISTINCT branches inside `_gate` (:346-360); knob branch in every body; per-body codes retained. Disabled wording unified ("nothing was {action}") — wording-only, outcomes identical. Behaviorally re-proven by the tools pack (45/45).
2. **H3 tool-side re-derivation deleted — EXECUTED.** `_validate_k` exists only in the service (`memoir_retrieval.py:528`); tool grep = 0. Executed at the tool surface: `k=0` → `memoir_k_out_of_range`, `k=21` → same, inverted window → `memoir_invalid_window`, unparseable `since` → `memoir_invalid_event_at`. No code lost.
3. **H5 stable id — EXECUTED (pin) + READ-VERIFIED.** One context_messages row `memoir:{agent_id}`; producer (`memoir_briefing.py:528`) and consumer (`:398`) call `_stable_id_for("memoir", agent_id=…)` with identical arguments; once-flag written only after the persistent half is confirmed; failed flag-write self-heals. Pin green (11/11 incl. idempotence).
4. **H6 packs/matrix/M12/L21/M15 — EXECUTED.** All **17** packs fail-loud now (superset of the five briefed; `MEMOIR_PACK_PYTHON` override honored; loud branch executed RC=1). M12: `EXPECTED_PASSED_N=10` == measured collected 10; one node deselected in a byte-faithful replica → RED `passed=9 != 10` — **measured, not hardcoded**. L21 membership loop present (`memoir.py:239-246`); M15 RESERVED annotations on the two reserved codes, `len(MEMOIR_CODES)==19` asserted (:235), re-derived by import: 19.
5. **H7/H8 — EXECUTED.** H8 dims guard plant → pin RED → restore (sha OK) → 32/32 green. `_classify_enabled` fails closed for garbage types, never raises (truth-table pins 40/40 incl. list/dict/float→DISABLED). Worker `fail_classes=` keeps `credential_rejected` (401/403, latched) DISTINCT from `embedding_dimensions_mismatch` (worker suite 39/39). M8 `_count_pins` derived (`repository.py:1189`). L22: all four fallbacks WARNING-logged.
6. **M16 post-commit honesty — EXECUTED (pins 45/45) + full enumeration.** Write :827 POST string: "the episode write MAY HAVE BEEN APPLIED … Verify with memoir_timeline (or memoir_recall) BEFORE retrying: a retry would record the memory twice"; "nothing was recorded" reachable only pre-write. Pin :937 analogous. Unpin :1014 has NO post-commit assembly work ⇒ truthful. Recall/timeline are read paths. Pins assert MAY-HAVE-BEEN-APPLIED + the named verification read + NOT-"nothing was recorded/changed".
7. **M17 per-read guards — EXECUTED.** Seeded persona-read failure (11/11): block still ships, ONLY the persona zone degrades with the missing-persona prefix, failure logged — D5.9 conformance proven.
8. **M18 stats-write guard — EXECUTED.** Monkeypatched exploding `mark_recalled`: episodes returned, `stats_updated=False`, `marked_ids=[]`, `WARNING stats_write_failed:RuntimeError`.
9. **A11 — ruling below.** READ-VERIFIED.
10. **C3 — ruling below.** READ-VERIFIED.

## Rulings (reviewer accepts both, on the evidence)

- **(9) A11 provenance home — RIGHT HOME for v1: keep `daemon/tools/provenance.py`; route a debt row; NOT gate-blocking.** The helper's deps are stdlib + `.upgrade_journal`→constants (no FastAPI/router/service deps) — the nest-ops-M7 weight class does not apply; the services→tools import is function-local (`memoir_briefing.py:431`), no cycle is possible or latent; single-sourcing is real (exactly ONE `def capture_provenance_class_name` at `provenance.py:172`; both consumers delegate); the log line is real (module logger, WARNING, `instance=<id>` + ExcType, :189-195). Minting `daemon/services/provenance.py` would create a second home for zero benefit. The directional inversion is genuine house debt — flag in implementation-notes (already A11) and relocate later if more services consume it.
- **(10) C3 footer/stats mismatch — DISCLOSED-MISMATCH-ACCEPTABLE-FOR-V1; route a v2 polish row; do NOT fix now.** The `stats_updated=` footer token is priced before `mark_recalled` and reaches ONLY agent-visible tool output verbatim — nothing parses it programmatically, the briefing does not consume it, and the operator truth exists exactly on the failed path (`WARNING stats_write_failed:<ExcType>` + structured result fields). Fix-now costs more than the cosmetic mismatch: re-pricing after stats would need to UN-MARK popped ids (violating D2.8 mark-exactly-shipped) or post-write string substitution would grow the byte-pinned envelope. v2 shape: conditional footer append on the failure path.

## N-1 (DELTA-1 carry): **NOT COVERED — definitive; route it**

`grep exclude_terminal` over `tests/` + `test/packs` = zero hits; every test call site invokes `get_pending_embeddings()` bare (default SQL-exclusion shape); no `exclude_terminal=False` call, no belt-predicate plant anywhere (`memoir_row_is_terminal`/`_is_terminal_row` in no test). The SQL leg remains the only *pinned* gate (single-sourced predicate keeps drift structurally hard; defence-in-depth intact). Suggested pin unchanged: one node calling `get_pending_embeddings(exclude_terminal=False)` then running the worker's Python filter, asserting the terminal row is skipped. Non-blocking; carry to ceremony notes.

## New findings

- 🟡 **NEW-1 (gate-blocking under the lane's net-new-reds=0 doctrine)** — `tests/unit/test_memoir_registration.py:243-244, :252-258`: static pins assert the pre-H1 inline shape — `source.count("if not _is_memoir_authorized(caller_agent_id):") == 5` now counts 0 (the shape lives ONCE inside `_gate` with the param renamed `agent_id`), and the order pin `index()`es the same literal. 2 net-new deterministic reds at HEAD; introduced by `5324d4beb` without re-pointing these pins. **Fix (mechanical, ~10 lines):** count `refusal = _gate(label,` == 5 across the bodies; re-anchor the order assertion inside the `_gate` body span (auth < `memoir_enabled` < first `asyncio.to_thread`), or delegate order to the behavioral pins and pin the helper's internal order once. Re-run the file green + porcelain 0 → this delta becomes APPROVE-WITH-ADVISORIES.
- 🟢 **NEW-2** — historical records describe the pre-tidier `python3` fallback as current-at-the-time (append-only records, correct as history); awareness only.
- 🟢 **NEW-3** — `daemon/services/memoir_retrieval.py:747/:779/:796`: per-recall WARNING on a *steadily* degraded semantic lane (one WARNING per recall); optional N-tick dampener if volume proves noisy.
- 🟢 **carried** — A-4 (`body_required → memoir_body_to_long`) still present, carried per DELTA-1 to ceremony notes.

## Boundary

Porcelain 0 at every checkpoint; planted file restored sha-identical; **branch not pushed** (`git branch -r --contains HEAD` = 0); tidier diff = memoir daemon files + packs + unit tests + 3 planning docs ONLY — zero metrics surface, zero boot-recovery/discard/hard-reboot files, zero new deps; digest diff `0ff59023e` read: 1 hash + 8 counts, no timings. Aggregate memoir unit corpus: 195 passed / 2 failed (both NEW-1).

## Gate readiness

**TESTER-GATE-READY: NO — blocked solely by NEW-1.** One mechanical test-side re-point (no production code) closes it: re-run `tests/unit/test_memoir_registration.py` green + confirm porcelain 0, and this delta is APPROVE-WITH-ADVISORIES with the advisory set: N-1 unpinned belt (route), C3 v2 polish row, NEW-2/NEW-3, A-4 ceremony carry, plus the standing DELTA-1 advisories.

## Record provenance

Append-only continuation; Round-1 and DELTA-1 sections untouched. Written and committed by the reviewer controller from the replacement reviewer's evidence-tagged payload, with the council-manifest corroboration noted above.


---

# DELTA-2.1 — Micro-fix confirmation (NEW-1/N-1 closure; verdict flip)

- **Date:** 2026-11-05
- **Scope:** the three micro-fix commits closing DELTA-2's blocker — `a5176e6f4` (NEW-1: registration pins re-pointed, tests-only +60/−12) · `62d5ef99e` (N-1: Python-belt pin, tests-only +55) · `79e57fb3a` (NEW-2: implementation-notes A5 fail-loud idiom, docs-only). **HEAD `79e57fb3a`, porcelain 0, branch unpushed, zero production-code files in `0ff59023e..79e57fb3a`.**
- **Verified by:** the same DELTA-2 replacement reviewer (`b29f386c`, `agentic`), revived with its own context; all evidence EXECUTED (sole planter; both planted files restored sha-identical).

## Verdict flip: DELTA-2 REJECT → **APPROVE-WITH-ADVISORIES** — TESTER-GATE-READY: YES

The stated mechanical condition (registration file green + porcelain 0) HOLDS, executed and confirmed.

- **NEW-1 CLOSED (executed).** `tests/unit/test_memoir_registration.py` → **20 passed** (was 2 failed / 18 passed; no node-count change). Pin 1 (:245/:247): `count("refusal = _gate(label,") == 5` + the helper exists exactly once. Pin 2 (:261+/:299): helper-span auth-literal < knob-literal, **no `asyncio.to_thread` in the helper span**, and every one of the 5 call sites precedes its body's first DB touch (decorator-anchored prefix check). **Mutation-meaningful by reviewer-executed M2 re-proof** (independent of the dev's self-plant): auth/knob literals swapped inside the `_gate` span → order pin **1 failed** (`test_the_gate_order_is_auth_first_in_source`) / 19 passed → restored byte-identical (sha `581aee8c…` OK) → **20 passed**, porcelain 0.
- **N-1 CLOSED (executed) — no longer needs routing.** Worker file **40 passed** (39+1). The new node (`test_memoir_embedding_worker_unit.py:477`) genuinely discriminates: non-vacuity (raw `exclude_terminal=False` MUST return all 3 rows), control leg (default SQL exclusion returns only the pending row), belt leg (`_is_terminal_row` over the raw scan yields only the pending row). **Independent mutation evidence:** planted `return False` in the belt delegate (`memoir_embedding_worker.py:336`) → node **RED** → restored (sha `1f4771fe…` OK) → green. The two-gate design is now **fully pinned** (SQL leg + belt leg).
- **NEW-2 CLOSED.** A5 record states the shipped fail-loud interpreter idiom (`implementation-notes.md:111`), docs-only.

## New (non-blocking) finding — the `_full_doc_` C3 caveat SUBSTANTIATED

- 🟢 **NEW-4** — `daemon/tools/memoir.py:1184-1185` (`memoir_recall._full_doc_`, the agent-visible `tool_help` text): the clause *"the episodes returned are **exactly the episodes marked as accessed**"* overclaims the D2.8 mark-exactly-shipped invariant. On the C3 failed-stats path the recall is kept with `marked_ids=[]` / `stats_updated=False` — episodes returned, nothing marked — so the unconditional claim is false there. **Provenance:** this is the `_full_doc_` caveat recorded by the interrupted council's `agentic` councilor (council manifest, round 3 — not previously anchored in this record); now substantiated and anchored. No other surface carries stats-token semantics (input-schema descriptions clean; module docstring :79 covers only the semantic/footer disclosure; timeline `_full_doc_` correctly says "NO stats write"). **Fix:** one doc-only qualifying sentence ("…except the disclosed stats-write-failure path, where the recall is kept but the stats flag is not updated — the footer shows the pre-write value"). Route with the C3 ceremony-note row or the next doc pass; not gate-blocking.

## Final advisory set for this delta (all non-blocking, all routed)

1. C3 footer/stats mismatch — disclosed-acceptable-for-v1 (DELTA-2 ruling; v2 polish row) **+ NEW-4 doc overclaim** (same family, same routing).
2. NEW-3 — per-recall WARNING on a steadily degraded semantic lane (optional N-tick dampener).
3. A-4 — `body_required → memoir_body_to_long` label imprecision (agent-invisible; ceremony notes).
4. Standing DELTA-1 advisories, **except the four-pack bare-`python3` fallback — CLOSED by Batch A** (all 17 lane packs fail-loud, executed in DELTA-2): knob `source="config"` mislabel; id-only `set_pin`/`clear_pin`/`mark_recalled` (single-agent v1); timeline double window read; unauthenticated `/readyz` echoing `rejected_value`; GREEN-AT-ZERO `N>=1` (mitigated by M12 exact counts in eight packs); seed docstring wording; persona guard has no production callers in v1.

## Record provenance

Append-only continuation; Round-1, DELTA-1, and DELTA-2 sections untouched. Boundary at close: porcelain 0, unpushed, no production code in the micro-fix delta, both probe plants restored sha-identical.
