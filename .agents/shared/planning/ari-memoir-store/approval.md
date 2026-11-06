# Phase B plan approval — Memoir Store v1 (ari memory system)

**VERDICT: APPROVE-WITH-CONDITIONS**
**Iteration:** 001 (fresh approver, independent) · **Artifact set:** `plan-overview.md` + `phase0-plan.md`…`phase8-plan.md` + `design.md` + `decisions.md` + `flag-dispositions.md` + `architecture-recommendation.md` + `council-findings.md` + `roadmap.md`
**Reviewed at:** `feature/ari-memoir-store` @ `adc64a9a4` (worktree `/home/nea/ensemble-src-wt-memoir-store`); code base `1f6898ae2`.
**Phase C MAY START** once **C-1** and **C-2** (§6) are folded. Both are documentation-level folds inside the plan set (≈6 lines, no code, no design change, no new adjudication). No other gate stands between this plan set and Phase C.

---

## 0. How this review was performed

The plan set was read in full (18 files, 2,264 lines) and **independently re-verified against the code at `1f6898ae2`** rather than accepted on the plan's own authority — the house "grep-verify before citing" rule. Verification covered the claims that carry the lane's weight: the Q6 blast-radius claim, the PG SQL argument order and the `to_tsvector` immutability claim, the boot order, the once-flag write-site hazard, the tool-category census, the mutation-matrix coupling, the knob's exemplar divergence, and the rollback-inertness claim. What I verified, and what I did not, is listed in §8.

I did not re-litigate the adjudications (the Emperor's R1–R4 are not adjudicable downward — P-5). My job was to rule on **implementability**, the **two blessed-text deviations**, the **risk posture**, **compliance**, and **release posture**, on the merits of the artifact as presented.

---

## 1. IMPLEMENTABILITY — could Phase C build this without inventing a material decision?

**Ruling: YES, with two local gaps closed (N-1, N-2 below). No material invention is forced.**

The set is unusually complete for a Phase B plan. The decisions an implementer would normally have to invent are all bound:

| Normally-invented decision | Where it is bound |
|---|---|
| Schema DDL, types, indexes, constraints | phase1 §3 + D1.2/D1.8/D1.9/D1.14/D1.15 + the model-bound CHECK note (W2) |
| PG materialization path (3-artifact split) | phase1 D1.3/D1.4 (two-arg `to_tsvector`) + D1.6/D1.7 (boot order) |
| The retrieval arithmetic | DR-5 + phase2 D2.4–D2.7 (pool 100, competition ranks 1/2/2/4, absent-term, total comparator, NULL-safe idle, `RRF_K=60` fixed, no rounding) |
| Tool signatures, refusal vocabulary, result shapes | phase4 D4.7 (incl. the `actor` claim), D4.8 (18 frozen codes), D4.9 (result shapes) |
| The kill-switch truth table | phase0 D0.5 (canonical), re-stated verbatim in phase3/4/5/6 |
| The briefing budget model + overflow outcome | phase5 D5.7/D5.8 (DR-1's ceiling + the render-time backstop) |
| The once-flag write site | phase5 D5.4 (consumer-side), D5.5 (off-loop read), D5.6 (deadline) |
| The worker contract | phase3 D3.1–D3.16 (owner, queue, timeout, executor, attempts, terminal, dims, latch, tick semantics) |
| The mutability partition | phase2 D2.1 (closed repository surface, no general update) + D2.15 (exact INSERT column set) |
| The registration seam + pin ceremony | phase4 D4.5/D4.12 (grep-derived set, no hardcoded count) |

**I independently confirmed the highest-risk claims are TRUE, not merely asserted:**

- The Q6 blast radius is factual. `_ensure_postgres_columns` is `def` at `daemon/manager.py:8055`; it builds ONE shared `statements` list at `:8269` and executes it at `:9555-9557` as `with self._engine.begin() as conn:` / `for stmt in statements: conn.execute(text(stmt))` — **no per-statement try/except**; the file's only other `for stmt in statements` is at `:10279`, in a different method. The house fail-loud docstring (`:8259-8265`) is as quoted. So a memoir DDL failure *does* roll back the inherited lanes' statements and abort the boot.
- The boot order the plan pins is real: model registration → `SQLModel.metadata.create_all(self._engine)` at `:975` → migration runner → `_ensure_postgres_columns()` at `:997` (PG-gated). The memoir `ALTER TABLE` therefore runs against an existing table — the "table missing" trigger is excluded by the order, exactly as R15 claims.
- The R3 hazard (silent, permanent briefing loss) is real: `daemon/graph.py:8524-8532` discards `_persistent_msgs` ("intentionally discarded"). The consumer-side flag write (phase5 D5.4/T5.4) is the correct fix, and the plan pins it with a drop-the-block ⇒ flag-stays-unset leg.
- `ts_rank_cd(tsvector, tsquery)` is the correct PostgreSQL signature (the plan's original order was wrong and was corrected); `to_tsvector(regconfig, text)` is IMMUTABLE while the one-arg form is STABLE and is rejected as a generation expression — so the two-arg form (phase1 D1.4) is required, not merely preferred.
- The once-flag needs **no schema column**: `instances.instance_metadata` is a JSONB dict column (`daemon/repositories/instance/models.py:64-66`) written via `set_metadata`'s `jsonb_set` path — so `memoir_briefing_injected` does not disturb the "+1 migration file" arithmetic.
- The tool-category census the plan treats as dated evidence is right at HEAD: `PRIVILEGED_TOOL_CATEGORIES` holds 17 entries (ending `"notify-user"`, `"metrics-read"`), 6 pins assert `== 17`, `EXPECTED_PRIVILEGED_SET` appears **13 times across 7 files**, MT-01's literals are find `== 17` / replace `== 16` (so the vacuity hazard the plan names is real), and the ari trailing-entry pin at `tests/unit/test_board_registration.py:285` is a list literal that must gain `"memoir"` last. `memoir` appears nowhere in `daemon/` or `agents/` — no collision.
- The exemplar divergence the plan follows is the right way round: `daemon/tools/notify_user.py` checks the **agent gate first** (`:702`) and the knob second (`:715`), while its comment calls the knob "the outermost gate". The plan follows the code (AM-16) and says why.
- Rollback inertness holds: `git grep memoir 1f6898ae2 -- daemon agents` returns **zero hits** — the pre-lane tree has no reference to any memoir table/column, so an old binary is inert against the new schema.

**Gaps an implementer would otherwise have to fill (both small, both closable inside the plan set):**

- **N-1** — `clear_pin`'s column semantics are unstated: does `memoir_unpin` null `pinned_by` / `pinned_reason` / `pinned_at` along with `is_keepsake`, or only flip the flag? The observable contract is specified (`memoir_unpin` on a non-keepsake is an idempotent no-op, phase4 D4.9) but the column-reset set is not, and it is what a re-pin overwrites and what the DB would otherwise carry as stale attribution. **Fix:** state it in phase2 D2.12/D2.1 (recommend nulling all four; keep `pinned_by` CHECK satisfied by NULL).
- **N-2** — the knob's *mechanism* needs one sentence. Phase0 D0.4/D0.5 require the value to be "resolved **through the model**, never hand-parsed", yet the truth table requires distinguishing "any other present value ⇒ INDETERMINATE ⇒ fail closed, WARNING naming the rejected value". A Pydantic `bool` field raises on a garbage value before the resolver sees it, so the raw string must be classified **inside** the model (a `field_validator(mode="before")` on `MemoirConfig`), never by a consumer. This does not change the contract and phase0 AC3 already pins the observable behavior (garbage ⇒ OFF + WARNING naming var/value/canonical spelling); it needs one clarifying clause so the implementer does not "fix" it by hand-parsing `os.environ` in three places.

---

## 2. THE TWO BLESSED-TEXT DEVIATIONS — explicit rulings

### DR-1 — briefing budget: 24,000-char absolute ceiling supersedes design §8.2's "~12k chars"

**Ruling: ACCEPTABLE as recorded. No re-ruling, no escalation to the Emperor.**

- It is not, in substance, a contradiction of the blessed text — it is the **exercise of a delegation the design itself made**: design §8.2 closes with "their reconciliation is explicitly a Phase B duty" and §14 item 3 lists it as an open intake item. Recording it as a §4 deviation is *stricter* than the design required, which is the correct posture under P-2/P-4.
- The reasoning is sound and the arithmetic is derived, not chosen: the stable zone is pre-bounded **by admission** (persona ≤2,000 tokens, keepsakes ≤4,000 tokens total), so it can never overflow; the ceiling is therefore sized from the admitted stable worst case (6,000 tokens ≈ 24,000 chars) and the "~12k" survives as the nominal size. The alternative reading (12k-truth) starves the persona to ~1,500 tokens and the keepsake shelf to ~83 tokens per pin — it guts the content the feature exists to carry, and it breaks "never truncated" from the other end (a 12-pin shelf could no longer hold 12 keepsakes).
- The invariant that must never break — the stable zone is never truncated — is preserved **by outcome**, not by truncation: >24,000 chars ⇒ stable zone in full + dynamic dropped + an exact overage disclosure; >48,000 chars ⇒ withhold the block, write no flag, WARNING. The dynamic zone is the part that gives way, and the overage is disclosed, so the agent is never silently clipped.
- Recording is exemplary and complete: `decisions.md` §4 row 3 (design-says / reality-forces / adjudication / outcome / links), `architecture-recommendation.md` DR-1, plan-overview header + §8 + §9 row 3 + §12, phase5 D5.7/D5.8 + AC5, phase8 D8.8 + the ceremony notes. The council confirmed visibility 2/2; the phase5 AC rows naming the ceiling without the literal string "DR-1" are cosmetic (advisory A3) — the deviation is named at every point of use.
- **It touches no Emperor ruling** (P-5 respected): R1–R4 say nothing about the briefing's size.
- **One caveat to carry into Phase C (non-blocking, N-3):** the "~15% headroom" claim rests on "real English averages <4 chars/token". If the ratio is worse, a *fully admitted* stable zone can sit at/over 24,000 chars and the backstop fires on every render (stable still in full; dynamic dropped; disclosure appended). The invariant holds either way, so this is not a correctness risk — but the phase5 pack should pin the ratio against the house estimator (`daemon/loader.py:1027`) for the admitted worst case, so the dynamic zone's expected survival is a measured fact rather than an assumption.

### DR-3 — kill-switch polarity: unset ⇒ config default ON (loud), garbage ⇒ INDETERMINATE ⇒ fail CLOSED

**Ruling: ACCEPTABLE as recorded. No re-ruling, no escalation to the Emperor.**

- DD-15's letter attaches "fail-closed" specifically to the **indeterminate** case, and the polarity is an explicitly open Phase B item (§14 item 13; the design's own reading says the default-vs-env polarity "is **not fixed** by the blessed text"). The architect therefore filled a delegated blank, under the correct protocol.
- Default-ON is right on the merits: the memory *is* the feature, and a default-dark memory ships the very leak it exists to cure (DD-16's point) — the first deploy would show "memory recording is disabled — this content was NOT recorded" as the normal experience. Two house precedents already default ON (`notify_user`, `mission_strand_heal`). It is admissible **only because the default is never silent**, and the plan makes that true in three ways: one WARNING per boot naming the var and the resolved state; the resolved state discoverable **without reading logs** (tool_help + health surface — phase0 D0.7/T0.5/AC5); and the knob read at call time (no restart to flip).
- The garbage⇒fail-CLOSED divergence from `notify_user` is justified and correctly bounded: DD-15's clause attaches to the indeterminate case, and the cost asymmetry is severe — a mis-set notify knob leaves a bounded, rate-limited, audited chat message; a mis-set memoir knob leaves unbounded, append-only, never-hard-deleted first-person narrative rows. The divergence is recorded in three places (decisions §4 row 12; arch-rec DR-3; phase0 D0.6 + the truth table restated in phase3/4/5/6) and the unpreventable residual (a misspelled var **name** ≡ unset ⇒ surface ON) is recorded as **accepted, loud-only** rather than papered over.
- The OFF semantics are stated as an operator-facing contract, not implied (tools refuse `memoir_disabled`; briefing not built and the memory **unreadable**; worker parks; the ari inner_soul branch still returns the loud not-recorded notice; data untouched) — and the OFF lever is carried into the ceremony notes (phase8 D8.7) so the operator knows what OFF does to a live deployment **before** the ceremony. That is the right standard.
- **Mechanism note:** see N-2 (one sentence needed so "resolved through the model" and "a garbage value is INDETERMINATE" are implementable together).

---

## 3. RISK POSTURE — Q6 / R15 (the memoir DDL inside the shared all-or-nothing ensure)

**Ruling: ACCEPTABLE TO PROCEED INTO IMPLEMENTATION. Do not engineer containment into this lane.**

Reasoning, in the order that actually decides it:

1. **The fact is true and the plan says so out loud.** I verified the blast radius directly (one transaction, one shared list, no per-statement try/except). R15, decisions §4 row 17, arch-rec §12.1 and phase1 D1.5/D1.6 all state the same thing without softening it. The plan did not hide the risk; it recorded it and gave it a row — which was the council's legitimate criticism, now discharged.
2. **The house's own contract lands on this lane's side.** The ensure's documented reason for failing loudly is "better to fail loudly at startup than to continue and crash on the first query that references the missing column" — and a missing `tsv` column is *exactly* that crash class, since the PG lexical ranker queries `tsv`. Per-lane containment for this lane alone would mean choosing "continue and crash later" against the house's own words.
3. **The likelihood floor is genuine, and I re-derived the exclusion.** Simple, idempotent (`IF NOT EXISTS`) DDL; verified on the house PG 16.15; a brand-new uncontended table; boot order pinned and verified (`create_all` `:975` before the ensure `:997`), so "table missing" is excluded by the order. Permission-denied is implausible (the same connection already ALTERs a dozen tables); connection-lost breaks everything rather than memoir; syntax error is excluded by the real-PG leg plus the AM-8 mutation leg.
4. **The consequence is loud, bounded and recoverable.** The boot abort names the offending statement; the rollback is **FIX-LOSS only** (the pre-lane tree has zero memoir references — I verified); every inherited statement is idempotent, so the whole set re-applies on the next boot; the operator's lever is a release rollback to the previous binary.
5. **The alternative is more expensive, permanently.** Per-statement isolation inside the shared ensure would be the first per-lane DDL-tolerance carve-out in that method's history, would make a single house seam conditional on one lane's policy (inviting N divergent DDL failure policies), and would require a **third** permanent lexical ranker variant (PG-without-`tsv` ⇒ Python BM25 over a fetched corpus) to keep the boot alive. It buys a LOW-likelihood event at the price of a permanent extra code path and a weakened house seam. Per-statement isolation is correctly **routed out** as a house-pattern change, not smuggled in.
6. **The discharge is proof, not prose, and the proof is well designed.** W8 is an induction, not an assertion: scratch-only (scratch PG + a **frozen-binary COPY** in a scratch root, because a frozen binary's path derivation resolves the LIVE install root), one deliberately-invalid memoir statement substituted **in the scratch copy only**, then five observations — non-zero exit before readyz; the failure names the statement; **no partial memoir schema** survives; **the inherited lanes' sentinel is ABSENT** (this is the assertion that turns the collateral-rollback half from a claim into a proof); the live fence holds — plus three mutation-verified structural pins (exactly one `begin()`; no per-statement `try:`; the memoir statements present in that same list), a two-sided non-regression leg (valid statements ⇒ boot succeeds *and* the inherited statements commit), and a disclosure clause that any **different** outcome is RED. It also pins the house property so a later lane cannot quietly weaken the shared ensure.
7. **The falsification condition is present** (a PG < 12 or managed/multi-tenant install base re-opens the ruling), and the residual — *the kill-switch does not protect the boot* — is recorded as residual, not mitigated.

**One gap in the discharge (this is condition C-1):** R15 says the ensemble is "down until an operator fixes it and reboots", but it does not name the actual recovery lever, and — more importantly — **phase8's ceremony handoff does not carry the boot-abort risk at all**. phase8's objective is explicit that there is no live promote, and D8.6/D8.7 carry the OFF lever and the two deviations; the lane's highest-severity risk is absent from the notes the promote-time reader will actually consult. By the plan's own standard ("the operator must know what OFF does to a LIVE deployment before the ceremony, not after" — D8.7), the boot-abort consequence and its recovery belong there too.

---

## 4. COMPLIANCE — R1–R4 and the commission's non-goal list

**Ruling: COMPLIANT on all four rulings and on every non-goal. Verified against the plan set, not on the architect's assurance alone.**

| Binding constraint | Finding |
|---|---|
| **R1** kingdom-level only; no federation/sync; the v3 Constella markdown+hash door must not be foreclosed | **COMPLIANT.** No sync/federation surface anywhere. The DB rows remain truth and a mirror can export from them; `stars` are captured **verbatim** (flag #5) precisely so the alias evidence survives to a v3 star table; the persona seed living in a code constant is provenance, not a format lock (the row is still exportable). The Constella door now appears on the two surfaces an implementer works from — plan-overview §2's Out-list ("introduced-not-synced (R1)") and phase7 D7.9's grep-assertion sweep — which is what council warning W7 required, and it landed. |
| **R2** keepsake governance BOTH paths; `pinned_by ∈ {empress, emperor}` | **COMPLIANT.** Both paths remain legitimate (the user path is Ari in chat → the same tool). `actor` is a **required, explicitly unverified claim** on `memoir_pin` and `memoir_write(keepsake=true)`, bounded by a model-bound CHECK on `pinned_by`, and corroborated at write time by the house provenance class surfaced in the tool result and the briefing keepsake line. `memoir_unpin` keeps the blessed `(id)` signature — the asymmetry (no unpin actor) is reasoned (no column exists; adding one would touch the blessed column list) and recorded. The durable per-pin ledger is deferred to v2 as a **recorded residual**, not a narrowing. |
| **R3** rolling roadmap | **COMPLIANT.** v1 only. v2/v3/v4 have no build authority. `memoir_syntheses` ships schema-only and empty (DD-2, grep-enforced writer absence — the strongest form of that guardrail in the set); `stars` ship flat (DD-19); no LLM importance scoring (DD-5); no `memoir_about` (DD-4); no per-turn reinjection (DD-11); the v2 alarm-wake refresh inherits the L4 constraint and is explicitly not built. |
| **R4** learn-not-adopt — no new external dependency, no graph DB, no new services | **COMPLIANT.** The only new imports are SQLAlchemy (`TypeDecorator`, `ARRAY(Text())`), stock PostgreSQL core (`ts_rank_cd`, `plainto_tsquery`, `to_tsvector` — no FTS extension needed), `tiktoken` cl100k (already a house dependency at `daemon/loader.py:1027`), and the **existing** `openai` SDK against the **existing** LLM lane with the shared `EMBEDDING_*` family. No new service, no graph DB, no pgvector. Phase7's G6 sweep asserts it as an acceptance test. |
| **Non-goal** — other agents' tool sets | **HELD.** ari-only allow entry; the census leg requires `memoir` in exactly one `agents/*/meta.json`; phase7 D7.9 sweeps every other agent's meta. |
| **Non-goal** — RAG/knowledge system except the ari `inner_soul` branch | **HELD.** Phase6 is the only sanctioned touch; the diff census (AC1) restricts the delta to one helper + one import + two call sites; the non-ari byte-identity leg covers the classification corpus including compound requests. |
| **Non-goal** — loader semantics | **HELD.** The briefing is a context block with a `context_kind` (DD-10); loader injection is listed as a rejected alternative; D7.9 sweeps `daemon/loader.py` (the estimator is a read, not a semantics change). |

No ruling was narrowed, waived or re-litigated by any of the 43 amendments (P-5 respected), and the architect's §6 compliance table is consistent with what I read.

---

## 5. RELEASE POSTURE — staging only, no live flip; rollback-safety reasoning

**Ruling: SOUND. The plan cannot be read as authorizing a live flip, and the rollback-safety reasoning holds.**

- **No live-flip authority anywhere.** plan-overview §1/§8 ("staged as a release, NO promote — the user's 3-factor nonce ceremony owns the flip"); phase8's objective ("**NO live promote**"); D8.6's rejected alternative is "performing any promote-adjacent flip ourselves (explicitly out of scope)"; T8.4's outcome is "no promote"; AC4 requires "No promote fields are touched". The ceremony handoff block **states** the promote command for the operator; it never runs it. The lane also keeps the house guards: the scripts-pin ancestry check, the W9 version derivation at stage time, both provenance sidecars, and the staging worktree kept.
- **Rollback-safety reasoning is sound.** Migration delta = **+1** file (the four-table `.sql`, additive `CREATE TABLE IF NOT EXISTS` ×4 + indexes); the PG `tsv` ensure is additive (`ALTER TABLE … ADD COLUMN IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS`); and — the load-bearing half — **zero references to any memoir table/column in the pre-lane tree**. I verified that directly (`git grep memoir 1f6898ae2 -- daemon agents` ⇒ 0 hits), so an old binary has no model, no SQL and no code path for any of it ⇒ the tables are **inert post-rollback** (the board-tables / notify_audit adjudication class in the house record) ⇒ `ENSEMBLE_ROLLBACK_SAFE=1`, `source=explicit`. The consequence is **FIX-LOSS, never incompatibility** — the correct posture for the standing rule, and the reasoning is recorded rather than reflex-asserted. The boot-smoke fences (scratch PG, binary copy, sandbox env, per-variable scrub, sweep pin, state.json PRE==MID==POST, `current->` untouched) plus the **embedding-endpoint fence** and the scratch-PG major-version parity assertion are the right guards for this lane's specific DDL risk.
- **Defect to fold (condition C-2):** phase8's AC1/T8.1 hard-code the absolute census ("120 distinct prefixes", "migration census 120 vs live 119"), which contradicts phase1 T1.6's own explicit rule ("re-derived at implementation time; **never the absolute 120** — another lane's migration landing first must not redden a correct tree") and phase8's own D8.1 ("never hardcoded"). This is the house's "no count may be a target" defect class (AM-18 / G4 / FN-3) and it has a concrete failure mode: a concurrent lane landing a migration first makes a **correct** tree fail phase8's AC1. Fix by expressing the assertion as a delta ("exactly +1 file; distinct-prefix count == file count at the **derived** census"), consistent with phase1.

---

## 6. CONDITIONS — must be folded before Phase C starts

**C-1 — Carry the Q6 boot-abort consequence and its recovery lever into the phase-8 ceremony handoff.**
*Anchor:* `phase8-plan.md` D8.6 (ceremony handoff block), D8.7 (operator-lever note), T8.4/AC4; source of truth `plan-overview.md` §6 R15 and `decisions.md` §4 row 17.
*What must change:* add a ceremony-note line stating (a) that a memoir `tsv`/GIN ensure failure **aborts the whole daemon boot and rolls back the shared ensure transaction** (whole-ensemble availability loss — the kill-switch does **not** protect the boot), and (b) the **recovery lever**: roll back to the previous release (its binary has zero memoir references and boots clean against the new schema) or fix the DB DDL, then re-promote — i.e. "fixes it and reboots" is not sufficient on its own, because the same DDL will fail again.
*Why it is a condition and not a note:* phase8 already establishes this standard for the OFF lever (D8.7: "the operator must know what OFF does to a LIVE deployment before the ceremony, not after"). The lane's highest-severity risk is the one fact the promote-time reader would need most, and it is the one fact missing from the notes. The fold is ≈4 lines in phase8 (and one clause in R15's mitigation cell naming the rollback lever).

**C-2 — Replace phase8's absolute migration census with a derived one.**
*Anchor:* `phase8-plan.md` T8.1, AC1, and the closing note ("the embedded-binary `.sql` census includes our file (120)").
*What must change:* express the assertion as a **delta plus a derived count** — "exactly +1 migration file vs the live census; distinct-prefix count == file count at the census **re-derived at stage time**" — matching phase1 T1.6/AC1 and phase8's own D8.1, and keeping the method note that the delta leg must read the **binary** census rather than the source tree.
*Why:* the house's "no count may be a target" rule (AM-18 / G4 / FN-3). As written, a concurrent lane's migration landing first makes a correct tree fail phase8's AC1.

Both conditions are documentation folds inside `.agents/shared/planning/ari-memoir-store/`. Neither touches code, the blessed design, or any §4 adjudication row, and neither changes an outcome the plan already specifies.

---

## 7. NON-BLOCKING NOTES (carry into Phase C; no gate)

- **N-1** — State `clear_pin`'s column-reset set (does unpin null `pinned_by`/`pinned_reason`/`pinned_at` as well as `is_keepsake`?). Anchor: phase2 D2.1/D2.12, phase4 D4.9. Recommend nulling all four; keeps `pinned_by`'s CHECK satisfied by NULL and prevents stale attribution on a re-pin.
- **N-2** — One sentence in phase0 D0.4/D0.5 on the knob's **mechanism**: classify the raw env string inside the model (a `field_validator(mode="before")` on `MemoirConfig`) so "resolved through the model" and "a present-but-unparseable value is INDETERMINATE ⇒ fail closed" are implementable together; the "never hand-parsed" rule means *no consumer* parses it, not that the model cannot classify it. phase0 AC3 already pins the observable behavior.
- **N-3** — Pin the char/token ratio for DR-1's admitted worst case against the house estimator (`daemon/loader.py:1027`) in the phase5 pack, so the dynamic zone's expected survival is measured rather than assumed. The "stable zone never truncated" invariant holds under every ratio, so this is about expectations, not correctness.
- **N-4** — Name the narrowable levers for an FC9 breach. phase4 D4.14 makes "narrow the grant" the default action on a prompt-budget breach, but `memoir` is ari-exact by R2/§5.2 — so state what is narrowable (tools_note prose, optional args, per-tool schema verbosity) or that a breach escalates to the leader rather than silently dropping a tool. (5 tools against ari's baseline makes a 25% growth breach unlikely; the point is that the failure action should not be undefined.)
- **N-5** — The design's §8.1 lead `_is_project_injected` is stale; the shipped symbol is `_is_project_already_injected` (`daemon/graph.py:952`). The plan already uses the correct name (phase5 D5.5) — worth a one-line P-7 note in phase5 so a reader comparing against design.md §8.1 does not "fix" the plan back to the stale lead.
- **N-6** — `phase4-plan.md` D4.12 names the second stale prose site as `test_service_tool_flag_off_byte_identified.py:819`; the file is `test_service_tool_flag_off_byte_identical.py` (the plan-overview FN-3 row has the same typo). Harmless for behavior, but it is a path an implementer will `sed` — fix the filename in both places.
- **N-7** — The two deviations are the lane's most visible artifacts and are correctly carried into the ceremony notes. Keep them there; also carry the C-1 boot-abort note in the same block so the promote-time reader has one place, not three.

---

## 8. WHAT I VERIFIED INDEPENDENTLY (and what I did not)

**Verified against the code at `1f6898ae2` (read-only):** the ensure's single-transaction/no-per-statement-try-except shape and its anchors (`:8055` / `:8269` / `:9555-9557` / the `:10279` sibling loop); the boot order (`create_all` `:975` → runner → ensure `:997`); `ts_rank_cd(tsvector, tsquery)` and `to_tsvector(regconfig, text)` vs the one-arg STABLE form; `daemon/llm_error_classifier.py`'s path and `RETRYABLE_STATUS_CODES` (no 408 ⇒ the memoir transient set is `RETRYABLE_STATUS_CODES | {408}`, house constant untouched); the inner_soul anchors (`:303` / `:311` / `:316` / `:365` / `:716` / `:777` / `:1022`); `notify_user`'s code-order gate (agent first, knob second) vs its misleading comment; `graph.py:8524-8532`'s discard of `_persistent_msgs`; `instances.instance_metadata` as a JSONB dict (⇒ no schema column for the once-flag); the privileged-category set (17 entries, ending `notify-user`/`metrics-read`), the 6 count pins, the 13 `EXPECTED_PRIVILEGED_SET` occurrences across 7 files, MT-01's find/replace literals, and the ari trailing-entry pin's list literal; 119 migration files; `memoir` absent from `daemon/`+`agents/` at base (⇒ rollback inertness); the existence of every cited precedent (`JSONBType`, `_bm25_score`, the `db_offload` executor, `cosine_similarity`, `HumanLaneRequiredError`, `provenance.py`, `is_hoisted_injected`, `assemble_context_messages`, `_stable_id_for`, the `resource_monitor` partial-index precedent, `prompt_version`'s `VALID_TRANSITIONS`) and the `tools.extend` placement rule in `instance.py`.

**Not verified (out of scope for a document approval; both are covered by the plan's own gates):** live-PostgreSQL runtime behavior (the real-PG legs in phase1/phase7 are the mechanism); Tier-2 whole-tree wall-clock viability and scratch-PG provisioning; that `memoir` is still ordinal #18 at Phase C time (the plan already treats the census as dated evidence and mandates grep derivation); the prompt-artifact baseline's 161/161 at implementation time.

---

## 9. VERDICT

**APPROVE-WITH-CONDITIONS.**

The plan set is adjudicated, internally consistent where it matters, compliant with R1–R4 and the commission's non-goals, and — on the evidence of my own code verification — implementable without a Phase C implementer inventing a material decision. The three questions put to this review are all answered in the affirmative on the merits:

- **DR-1** — acceptable, properly reasoned, properly recorded; **no re-ruling, no escalation**.
- **DR-3** — acceptable, properly reasoned, properly recorded; **no re-ruling, no escalation**.
- **Q6 / R15** — the no-containment posture is **acceptable to proceed into implementation**; the blast radius is factually correct, house-consistent, honestly recorded, and discharged by an inductive proof plus a risk row with a falsification condition. It must **not** be engineered away inside this lane; per-statement isolation in the shared ensure remains a house-pattern change routed out.

**Phase C MAY START once C-1 and C-2 are folded** — both are ≈6 lines of documentation inside `.agents/shared/planning/ari-memoir-store/`, neither touches code, the blessed design, or any adjudication row, and neither changes an outcome the plan already specifies. The non-blocking notes (N-1…N-7) should be folded with them but do not gate Phase C.

---

*Fresh-eyes plan approval, iteration 001. Read-only against the code; the only file written is this one. No promote, no daemon boot, no DB contact, no live contact, no source mutation.*
