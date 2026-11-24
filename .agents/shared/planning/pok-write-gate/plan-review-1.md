# Plan Review 1 — POK Write Gate (commission plan set)

**Verdict: APPROVE-WITH-ADVISORIES — CONDITIONAL.** Fold CRITICAL A1 (a one-string fix at `plan-overview.md:104`) **before build start**; if A1 is not folded, treat the plan as **REJECT for build start** — its success-criteria ledger would gate on the content-destructive merge shape the body forbids. Every other finding is fold-into-plan-text; no redesign required.

- **Date:** 2026-11-23 (council evidence window ~18:40–18:50Z; anchors re-grepped in-worktree)
- **Mode:** 🔴 Deep-Review — 5/5 trigger categories (data-integrity migration/store-write gating; cross-cutting grant removal; ASK-TTL concurrency/state; business-critical POK lifecycle; new gate-agent architecture)
- **Session:** governor `4dd2bc51-43ac-409f-a511-63f12a84b95c` (review-council-pok-write-gate), 2 councilors — `agentic` → dims A–D, `coding` → dims E–I — skill `plan-review`; both COMPLETED inside the soft deadline; zero factual disagreement; 3 defects independently found by both models
- **Target:** `/home/nea/ensemble-src-wt-pok-write-gate/.agents/shared/planning/pok-write-gate/` — 20 files, all confirmed present
- **Aggregate: 1 CRITICAL / 13 WARNING rows (12 unique defects) / 18 ADVISORY / 0 SCOPE-EXPANSION**

---

## 🔴 CRITICAL — blocking, must fold before build start

### A1 — Success-criteria ledger still carries the content-destructive merge shape (closed-blocker #1 violated in a FOURTH location)
- **Where:** `plan-overview.md:104` (success criterion 3) — reads `"21→2 breaches; 19 dissolve; 2 merge"`.
- **Defect:** The body (`phase2-plan.md:11-12`, tasks T2.3/T2.4), the overview phase table (`plan-overview.md:37`), and the architect fold (`design-amendments-v2.md:163`, §13.4#1) ALL mandate **split/re-key + archive, never a content-merging supersede** — because four durable rules cannot fit one ≤240-char body (house rule `pok-sweep-01-merge-cap-split`). A build lane gating on SC#3 as written could accept the merge-with-drop shape the commission explicitly closed.
- **Why both models' Dim-I results are simultaneously true:** closure is verified at three locations; the stale predicate sits in a fourth (the success-criteria ledger) that only the full ledger-vs-body walk caught. This is the body-vs-ledger trap class working as designed, not a contradiction.
- **Fix:** rewrite to `"…2 split/re-key + archive (no content-merge)"` — one string.

---

## Closed-blocker verification (the two the commission explicitly asked about)

1. **Split/re-key + archive, never content-merge-with-drop — CLOSED in 3 of 4 places:** `phase2-plan.md:11` + `design-amendments-v2.md:163` (§13.4#1) + Risk#1/SC#24 rows. **See A1:** `plan-overview.md:104` (SC#3) contradicts them — fold A1 to make closure total (4/4).
2. **Counter carry-over on `supersede_and_insert` has its RED pin — CLOSED:** `phase2-plan.md:16` + §13.4#2 + Risk#2/SC#25 + fork pin T11.16. Underlying defect verified real: `supersede_and_insert @ repository.py:286` copies no counters today.

---

## 🟡 WARNINGS — fold into plan text before/during build

| ID | Dim(s) | Location | Defect | Fix |
|---|---|---|---|---|
| W1 | E | `phase2-plan.md:11-12` | T2.3/T2.4 acceptance rows omit the scratch-fence — phase plan is the execution contract; live-store mutation hazard | Add "scratch fixture only; no live POK-store write" (mirror phase6/11 idiom) |
| W2 | A+D | `phase12-plan.md:23` | T12.7 (verbatim Emperor element "gate MAINTAINS the library") has **no task-map row** → unchained | Add row with File cell `guideline §2 (measured-state)`, leg `diff review` |
| W3 | A+F | `plan-overview.md:48,:51,:52`; `phase6-plan.md:14-15` | Task-count drift (P8 13↔14, P11 13↔17, P12 7↔8) + duplicate task number "5" in phase 6 | Recount from phase files (script-able); renumber phase 6 and shift |
| W4 | A | `plan-overview.md:110` vs `phase8-plan.md:22` | Criterion 9 asserts MERGE ⇒ supersede on every MERGE; body's F2 fork = no-delta ⇒ **no write** + stamp only | Restate criterion 9 per the F2 fork |
| W5 | A+G | `plan-overview.md:113` | Criterion 12 "No write refused in Wave 2" parses as shadow OR as enforcement-ON | Restate: "judge refuses NO write while switch OFF (shadow logs only)" |
| W6 | A+E | `plan-overview.md:119,:24` vs `phase1-plan.md:10` | Criterion 18 "No new .sql unless justified" collides with the house rule (every new table ⇒ own `.sql`) for the ledger's own migration | Restate: "EXACTLY ONE new .sql (the ledger's); none touching POK/embeddings tables" |
| W7 | B | `phase1-plan.md:12`, `phase5-plan.md:9` vs `:27`/`:25` | Two Wave-1 exit criteria require Wave-2 artifacts (Phase-8 ASK chain; gate-path stamping) | Split criteria into W1/W2 halves or move rows to Phases 7/8 |
| W8 | B+F+H | `phase3-plan.md:11` vs `tests/unit/tools/test_pok_tools_unit.py:180,:197,:281,:291,:299,:315` | `reason`-check **order** vs existing refusal precedence unspecified — 6 tests assert precedence-sensitive codes | Pin order (recommend scope→store→reason→fence/budget/family, preserving existing codes) + leg row |
| W9 | B+H | `plan-overview.md:189` | Tool-sweep figure wrong: actual **37** `.invoke(` in `tests/unit/tools/test_pok_tools_unit.py` (18 upsert-index + 13 bare-tool) + 9 in `tests/unit/services/test_pok_trust_tier_unit.py` (6 upsert); plan says "~18+9" with bare filenames | State exact paths + tool-index + call class; unswept upserts will RED once reason is REQUIRED |
| W10 | B+E | `phase10-plan.md:12` vs `maintenance.py:985-996` | H3 pin's benefit requires the POK pass to **write a `maintenance_runs` row** — no task says it does; without it the section entry is vacuous | Add to T10.4: write `maintenance_runs` row (`section='pok-librarian'`) — table exists, zero migration |
| W11 | C | `plan-overview.md:82-96` | Risk register has **no owner column** (acceptance frame asks for owned risks) | Add Owner/decision-forum column |
| W12 | C+G | `plan-overview.md:86,:90` | **TTL expiry storm** not a risk row (mass fail-closed at P1 → silent lesson loss → retry ping-pong) | Add row; mitigation = `pok_gate_expired` surfacing via librarian pass + expiry-rate metric |
| W13 | D+H | `plan-overview.md:197-198`; `phase4-plan.md:14-15` | Directory-glob File cell; 17 sealed candidates in `agents/.prompt-artifacts.sha256` (4 agents × 4–5 artifacts), no per-agent enumeration — baseline-regen hazard | Name `tools_note.md` per agent (natural home for capture-duty guidance) + list baseline-regen entries |

(W2≡D2 are the same defect seen from two dimensions; 13 rows, 12 unique defects.)

---

## 🟢 ADVISORIES (18 — fold at convenience)

**Caller-side / test posture (from coding partition):**
1. Phase-1 sole-migration note (make "this is the only new .sql" explicit in the phase doc).
2. Phase-13 staging-env-only + Emperor-ceremony promote gate — state explicitly.
3. Shadow-mix gate must invoke the REAL judge; stub-only evidence unacceptable.
4. Pytest mark registered in config (not just "marked" in doc text).
5. Mutation-leg scratch venue named in task rows.
6. Kill-switch OFF ⇒ pre-gate parity as an explicit testable pin.
7. Mechanical-unconditionality (reason+R0) note/pin.
8. Absence-pin exact assertion + mutation spelled out.
9. T9.7 grep recorded as evidence artifact.

**Completeness / chain (from agentic partition):**
10. `technical-analysis.md:136` — judge-JSON 2→4-verdict contract supersession gap.
11. BINDING arch doc stale `271→272` should read `269→270` (verified against worktree; `KNOWN_TOOL_NAMES`=269 re-verified, `pok_gate_answer` absent — plan's 269→270 is correct).
12. "71 dup-slug" uncited — see adjudication Q1 below.
13. Phase-13 tag-push fence carve-out — see adjudication Q2 below.
14. `phase12-plan.md:20,:22` verbatim-duplicate task rows.
15. Gate-bottleneck/throughput risk row missing (convergent both models; mitigations exist — shadow-first, ASK-cache, TTL, soft-max — but no owned row).
16. `quick`-latency re-measure needs owner + date.
17. PACKS rows lack invocation + marker block.
18. Scratch venue convention uncited — name the `/tmp` worktree + FAKE_HOME/mktemp fence in plan text.

---

## Open questions — adjudicated by the review controller

1. **"71 dup-slug" provenance** (`plan-overview.md:62,:152,:299` — referenced 6×, sourced 0×, violating the plan's own cite-snapshot-times fence). **DECISION: delete the bare number, keep the reconciliation gate**, unless the author can cite the sourcing artifact at fold time. An uncited count in a fenced plan is worse than no count; the reconciliation gate catches the true number regardless.
2. **Phase-13 release-tag push vs "branch+worktree only" fence** (`phase13-plan.md:11` vs `plan-overview.md:299`). **DECISION: the push is sanctioned — it IS the staging mechanism under ruling (7) ("staged release, NO promote"); amend the fence text with an explicit carve-out naming the Phase-13 tag push rather than removing the push.** Promote remains the Emperor ceremony; no fence change beyond the carve-out sentence.

---

## What verified clean (evidence base — both partitions)

- **Ruling-set completeness (A):** all 7 parts COMPLETE — carried in **phase bodies**, not just the overview, including the two most droppable elements (`reason`-REQUIRED-now; counter carry-over). `POK_GATE_MODEL` default literal `"quick"` pinned at `phase7-plan.md:21`.
- **Phase feasibility (B):** dependency graph acyclic and correctly ordered `0→{1,2,3,4,5}→6→{7,8,9,10}→11→12→13`; no scope absorption; effort plausible (W1 45 / W2 78 tasks).
- **Ownership chain (D):** 5 representative chains traced end-to-end across 5 phases (T2.7/T2.8, T4.2–T4.4, T8.5, T10.3, T10.4) — all checkable task → artifact → File cell → leg row → PACKS.md row. The defect the plan fixes is real at the cited line (`supersede_and_insert @ repository.py:286` copies no counters today).
- **Fences (E):** baked into every phase (`plan-overview.md:297-299`); the admission-ledger additive `.sql` is the SOLE migration (touches no POK/embeddings table); no promote; no live config change. (W1/W6/W10 are text-level tightenings, not fence breaks.)
- **Test posture (F):** LLM-path tests marked; deterministic stub prod-forced with stamped alias (never a silent pass-through); scratch-only fixtures; behavioural mutation legs.
- **Enforcement-switch surface (G):** two SIBLING default-OFF switches `pok_gate.semantic_enforcement` + `pok_gate.softmax_tightening`, wired as siblings; mechanical reason+R0 unconditional.
- **Caller-side rails (H):** grant removal fully carried — `test_pok_grant_pins.py` 54→53, completeness exception, docstring refresh, NEW absence pin + mutation leg, tool-categories.json UNTOUCHED, meta.json UNSEALED (no baseline regen).
- **Anchor hygiene:** the agentic councilor re-grepped every anchor in the worktree — all resolve as of the evidence window.

## Gaps

None. Fan-in complete: single council node, both councilors COMPLETED, no terminations, no re-dispatch under the escape valve, no `[REPORT SANITY]` markers, no silent skill-load failures.

## Process notes

- Neither councilor's final message explicitly attests the mandated `skill_feedback(...)` first-action call; both reports are otherwise evidence-dense (re-grepped anchors, byte-level PACKS.md cross-check). Flagged for telemetry verification — not blocking.
- Anchor verdicts are worktree-verified as of 2026-11-23 ~18:40–18:50Z; live-source re-verification correctly stays with Phase 0 (T0.1/T0.2) per the plan's anchors-are-leads rule.
