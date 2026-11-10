# Approval Tracking: project-operating-knowledge (POK)

Artifact set: `.agents/shared/planning/project-operating-knowledge/` — architecture-recommendation.md (312L) · decisions.md (150L) · plan-overview.md (151L)
Worktree: /home/nea/ensemble-src-wt-pok · branch feature/project-operating-knowledge @ 7426e0d8a
Commission: investigation + DESIGN only (no implementation authorized)
Mode: mixed — decision-approval (recommendation/rejections/reuse) + plan-approval (layer map/rollout/migration/ACs), 2 sequential workers, one skill each.

## Iteration 001 — 2026-11-06
Workers:
- f5c65fbc-78d7-43f0-ba9b-cccec425366d (decision-approval) — REJECTED, 3 blocking / 6 notes. ~18 layer claims re-verified in tree, nearly all exact.
- 818979f2-ab48-4167-87bf-f91ecfa8c154 (plan-approval, attempt 1) — INVALID: report carried zero tool-call evidence ([REPORT SANITY] marker), no findings. Re-dispatched once per escape valve.
- e16a5651-400f-47bf-bc90-ab7cf6cff830 (plan-approval, attempt 2) — REJECTED, 9 blocking / 9 notes. Receipts re-verified; 4 doc claims failed verification.

VERDICT: REJECTED (iteration 001). 8 aggregated blocking issues after dedup/merge.

Aggregated blocking set (dedup map):
B1 reuse bar / infra_assets omission — W1#1 + W2 note (independent find, both workers)
B2 supersede/pin integrity + risk bounders == vector — W1#2 + W2#4 (merged)
B3 scope predicate vacuous after normalize_project_id + system-default boundary + target project identity unverified — W1#3 + W2#1 + W2#2 (merged)
B4 cap arithmetic 2400/8/1500 unsatisfiable + no overflow rule — W2#3
B5 grant-sweep population does not exist + privileged-category-with-universal-grant unreasoned — W2#5
B6 missing registration surfaces TOOL_REQUIRED_AGENTS + VOCABULARY_VERSION; "#20" ordinal conflates privileged-ordinal with 60-entry artifact space — W2#6 (W1's "#20 correct" finding merged: correct as privileged ordinal only)
B7 pin verb absent from the tool surface though pinning is load-bearing — W2#7
B8 no failure/degradation contract (store down / oversize / malformed / superseded) — W2#8
B9 (folded into B1-B3) acceptance criteria cannot falsify the walkthrough; AC10/11/12 missing; AC3 ranking non-reproducible; AC7 Tier-1 dead — W2#9

Notes carried: 10x byte error in layer row 10 (2005 files ≈4.7MB → actual 47.68MB, 49,999,702B); "read by NO automatic daemon path" should scope to "no daemon CODE reader" (routers/blueprints.py:583 prompt string); 82%/408,809B basis trusted-not-probed and load-bearing; snapshots/source_configs omissions (non-carriers); pin-verb; KV placement list ambiguity; pok_verify uncovered by ACs; supersede embedding lag contract; agents-ensemble-vs-llmproxy target-project ambiguity; agent_id in-body check buys attribution not authorization for POK.

Positive record: memoir unwired-column catch, uq_pok_active_slug partial-unique, deterministic no-relevance-gate injection, dead-weight honesty, dissent logging (C-with-mirror), design-only discipline honoured.

Next: iteration 002 opens on a folded artifact set. Re-dispatch fresh workers; zero history passed.

## Iteration 002 — 2026-11-06
Workers (fresh, cold prompts, zero history passed):
- 568b3e08-2665-4eb4-92f7-d35c7c9dd309 (decision-approval, architecture-recommendation.md + decisions.md) — REJECTED, 4 blocking / 6 notes. ~30 receipts spot-checked vs tree, essentially all exact.
- 5b083f53-fc26-4e6f-a1ee-ff099c781e63 (plan-approval, plan-overview.md + discharge-round-1.md) — REJECTED, 1 blocking / 8 notes. ~15 receipts re-verified, zero false claims; cap arithmetic recomputed.

VERDICT: REJECTED (iteration 002). 5 aggregated blocking issues — ALL NEW (deeper-layer findings; NO iteration-001 blocker recurs).

Iteration-001 delta check (post-verdict, tracking-vs-worker evidence): ALL 8 prior blockers verified ADDRESSED —
- 001-B1 infra_assets omission -> infra row present, receipts verified (models + 9 tools _tool_registry.py:1589-1597 + exactly 3 grants + critic deny). Reuse bar re-broken in NEW forms (002-B1', 002-B5').
- 001-B2 supersede/pin integrity -> curator-only pinned-supersede + disputed path-independent bounder verified coherent (defense-in-depth).
- 001-B3 vacuous scope predicate -> project_id != SYSTEM_DEFAULT_PROJECT_ID verified vs real normalizer (project_normalizer.py:6-45); AC10 pins both directions; P-1 project-row precondition surfaced as decision (option (a)/(b)).
- 001-B4 cap arithmetic -> 240/64/4/2600/8/0.25/30 identical across all 4 files; 80+8x300+60=2540<=2600 and pinned 1340<=2600 recomputed OK.
- 001-B5 grant sweep -> empty-allow inheritance CONFIRMED (_tool_registry.py:326-331); 40-agent sweep mechanically real; universal grant now reasoned.
- 001-B6 registration surfaces -> TOOL_REQUIRED_AGENTS avoidance reasoned; registry 60->61 + VOCABULARY_VERSION 6->7 consistent (A-16/A-21); AC8 pins the F-1 regeneration trap via house generator.
- 001-B7 pin verb -> pok_curate (pin/dispute) in the 4-tool surface + authorization matrix (arch 4.3).
- 001-B8 failure/degradation -> 4.9 degradation table + AC13/AC14/AC16. New gaps found deeper (002-B2' terminality; 002-B3' fence).
- 001-B9 (folded) ACs could not falsify -> 16 discriminating ACs, each mechanism-tied.

Aggregated blocking set (dedup map):
B1' global-KV rejection strawman-shaped — three factually wrong grounds AND self-contradiction with arch 2 row 14 (:48 "only per-turn-refreshable pattern", which the design itself borrows); KV granted to 34 agents, not ari-only; the REAL structural grounds (tree-scoped context_key, <=4KB row cap, per-tree partition) never stated — W1#1 (arch 6.7 :212, decisions A-15.7 :80)
B2' sizing terminal lockout — 64 active-slug cap + refuse-never-evict (arch 4.2 :107) + supersede nets ZERO active-count (1-for-1) + disputed rows still count active (4.1 :79) + archived CHECK-reserved/unreachable (4.3 :129) + no freeing verb => 65th slug permanently refused; absent from 12 (:297-306); residual (iii) framed as missing VERB when it is the only cure for a TERMINAL state — W1#2 (W2's contrary "exits exist" reading merged and rebutted: supersede frees no slot; no dedupe-sweep surface in v1's 4 tools; archive is phase-2)
B3' no content-rendering/fence contract for a NEW universal 40-writer verbatim-injection channel — arch 4.5 :141 "verbatim"; 240c body can carry forged block headers (## Related Project, [SYSTEM CONTEXT: ...]); incumbent precedents do not transfer (KV builder escapes content, context_messages.py:1404; critical notes render raw but are leader/PM-written); house just shipped injection-fencing doctrine (canary, v0.28.15); no 12 row / 4.5 sentence / AC leg — W1#3
B4' layer map omits the DB-versioned prompt-memory artifact family (memory/user/growth/memories + load_recent_memories loader.py:780-807 + access_memory + create_memory_version prompt_version_service.py:1030); memoir gets a full row (:40) — inconsistent scoping; shared_meta_kv also has no map row (only the flawed 6.7 dismissal) — W1#4 (arch 2 :35-48)
B5' ORDERED-procedures clause of the reuse bar has NO mechanism — no sequence field in schema; admission rule renders by decay x observed x access RANK (arch 4.5 :141); split-slug multi-step procedures (POK's exact genre) inject OUT OF ORDER to every project-linked agent; residual (i) ordering consequence silently waved off, NOT decision-framed — W2#1 (arch 0 :13, 6 :204 vs 4.1 / 4.2 :108; decisions A-1 :17, A-6 :29; plan 4.5), corroborated by W1-N3

Residual framing audit (caller criterion): (ii) supersede-of-disputed DECISION-FRAMED ok (stated twice w/ rationale, arch 4.2 :106 / decisions A-4 :25); (iii) archive-verb deferral DECISION-FRAMED ok as a verb decision (arch 4.3 :129) BUT its terminal-state consequence unframed -> folded into B2'; (i) 240c cap itself justified (arithmetic verified) BUT ordering consequence NOT framed -> folded into B5'.

Notes carried (12 after dedup): 6.2 false receipt ("the reference field is not injected" refuted at HEAD — context_messages.py:1030-1040 renders ref bounded 500; rejection survives on field-caps + writer grounds); 6.3 convention-read-as-structure (blueprint genre ban lives in build-blueprint.md:25/:33 prompt-doc, not code; rejection survives on writer/currency grounds); AC3 "rank 1" under-derived from the ranking mechanism (pin to top-k or add pinned retrieval-boost); 4.3 "an attempt is still recorded" names no recording surface; once-per-instance flag behavior on store-down first turn unspecified; dispute-laundering-by-near-dupe-rewording path unnamed (exact-hash dedupe only); pok_verify deferrable to phase 2 (re-upsert refreshes last_verified_at); operational-vs-forensic key classification rule for the metadata tail unstated (D2 owner); memoir "three literal ari constants" — grep finds 4 files (substance verified; precision); "five pseudo-incumbents" vs section 6's eight + "60-200 chars" vs measured 54-93 (cosmetic); DB dynamic-skill tier lacks own map row; "7 facts" superset fully covers the commission's six.

Positive record: receipt integrity exceptional (~45 spot-checks across both workers, one false claim total); B2/B3 v1->v2 fixes verified GENUINE (curator-only pinned-supersede + disputed bounder; falsifiable scope predicate vs real normalizer); migration lossless (8 slugs, order embedded in pinned prose, healthz pointer-not-copy); dogfood-through-the-write-path + AC12 measure-before-claim gating; 16 discriminating ACs; honest retractions (200-cap saturation, 10x byte correction, inherited-measurement gating); dead-weight honesty; recorded dissents.

Unverified/inherited: live-daemon 69-keys/279,810B/82% economics honestly gated behind AC12 (not probed, fenced posture); RAG project-name-vs-id keying not traced end-to-end (not load-bearing).

Next: iteration 003 opens on a folded artifact set — FINAL iteration before the cap (a 3rd rejection => ESCALATED to Leader). Fresh workers, zero history passed.

## Iteration 003 — 2026-11-06 (FINAL — cap reached)

Workers (fresh, cold prompts, zero history passed):
- b69a158a / f29185b8 (attempt 1, decision-approval / plan-approval) — STALLED mid-turn ~22:06Z, no output 1h35m, status=running; daemon healthy (livez 200, load 0.49, no wedge captures) => per-turn stall, not systemic. Both terminated per escape valve step 2. Anomaly reported a9d40770.
- d8cd0efc-c590-432e-880a-962ad1540fa6 (decision-approval, re-dispatch, model_tier=high) — REJECTED, 1 blocking / 10 notes. ~45 receipts spot-checked; essentially all symbol-exact.
- 122095ff-2f5d-48f8-8921-03b05c619de4 (plan-approval, re-dispatch, model_tier=high) — REJECTED, 7 blocking / 14 notes. 15 receipts independently re-verified, zero discrepancies.

VERDICT: REJECTED (iteration 003). 8 aggregated blocking issues after dedup/merge.
STATUS: ESCALATED — max iterations reached (3).

Aggregated blocking set (dedup map):
B1 block-char bound omits the 47c [SYSTEM CONTEXT: …] wrapper (_make_context_message: CONTEXT_PREFIX 17 + title 27 + CONTEXT_SUFFIX 3) => 47+40+2664+60 = 2,811 or 47+31+2664+60 = 2,802 vs 2,800 cap; "PROVEN on rows AND chars" + "36c slack" false at the stated maximum; LHS of POK_BLOCK_MAX_CHARS never defined; AC11 chars leg repeats 2,764 — W1#1 (arch 4.5 :124, 4.7, 4.9, 12, A-11/A-13/A-34, AC11)
B2 Phase 1 makes the walkthrough true on a throwaway DB only — dogfood "fenced dev daemon", AC1/AC3 dev-scoped, no phase seeds the target install, §2 excludes bulk backfill without naming an alternative or owner => walkthrough TRUE in ensemble_dev, FALSE in ensemble_prod indefinitely — W2#B1
B3 commission fact 5 (daoduc-Helm-v28 vs ensem.dev-plain-apply) spans two UNORDERED slugs (family regex groups only pipeline-*): may render split and be half-admitted under the 8-entry cap => one-sided delivery is more dangerous than silence and lands on the 12 wrong-but-confident-fact risk whose bounders catch none of it — W2#B2
B4 40-agent grant sweep, applied literally, appends to agents/watcher/meta.json (EMPTY allow = inherit-all, _tool_registry.py:326-340) => silently strips every inherited non-privileged tool; and appends AFTER "nest-report" => test_anomaly_report_grant_pins.py leg (a) REDs on 39 files. Neither "watcher" nor "trailing order" appears in any of the 5 artifacts — W2#B3
B5 AC3 determinism asserted on a SORT that cannot produce it — precedent achieves pinned-always-returns by BYPASSING the >=0.25 threshold; a sort cannot rescue a threshold-discarded row; and AC3 only observes the pinned fact, so a totally broken ranker passes — W2#B4 (corroborated by W1-N2: "hybrid 0.4*BM25+0.6*cos >= 0.25" exists nowhere; skill_search_service is BM25 prefilter -> MAX-cosine -> LLM with 0.7/0.3 thresholds)
B6 metadata-cap companion deletes ~94% of a live prompt surface (16,384c vs 279,810B) with no key-usage inventory, no recovery path for non-privileged agents (project_get = ari/devops/governor/jober/leader only, AC15 declines any pointer), AC6 satisfiable by emitting zero keys, and no 12 row for semantic deletion — W2#B5
B7 daemon/manager.py pre-create_all model registration missing from the file table and from 7 registration surfaces; AC9 asserts only that two .sql files exist and carry no FK => gate passes with the model unregistered and NO table on PG. Related same-commit re-pins unnamed: test_metrics_vocabulary_amendment.py:32 (VOCABULARY_VERSION==6), test_tool_category_artifact.py — W2#B6 (RECURRENCE of the 001-B6 registration-surface class, new instance)
B8 D2 presented as pending while A-14 (in DECIDED-so-far) + AC6 already fix the uniform shape and its numbers; allowlist branch has no Phase-1 deliverable => Phase 1 implementable under exactly one branch of a decision it says it awaits — W2#B7

Recurrence analysis (caller criterion — "no new defect introduced by the fold chain" vs "defects the folds never reached"):
- B1 is NEW (the wrapper class; distinct from W5 escape expansion and from F1's overhead — the fold fixed overhead 80->92 and still missed the wrapper above it).
- B5 and B6 are RECURRENCES of iteration-002 NOTES that were carried, not folded: 002-note "AC3 'rank 1' under-derived from the ranking mechanism (pin to top-k or add pinned retrieval-boost)" and 002-note "operational-vs-forensic key classification rule for the metadata tail unstated (D2 owner)". Fresh workers independently re-found both, now at blocking weight.
- B7 recurs the 001-B6 class (registration surfaces) in an instance the 002 check did not enumerate.
- B2, B3, B4, B8 are NEW findings at depths no prior round reached.
- C1/W5/F1/F3 fold integrity: the 92c overhead, entry 332c, rows 6<=8, slug<=44, pinned 2,138<=2,800 all RECOMPUTED CORRECT by W1; the fold chain introduced no arithmetic error of its own — B1 is an omission ABOVE the folded formula, not a corruption of it.

Positive record: receipt integrity remains exceptional across all rounds (~45 + 15 spot-checks, one loose quantifier and three loose line-ranges total, zero false structural claims). Two v2 retractions independently confirmed as real corrections. W5 escape closure genuinely closed (all-escapable raw = 240/6 = 40c). F3 exclusion rule clean and testable. Rejected alternatives survive a hostile reader (ten carriers, KV grounds rebuilt from scratch, blueprint genre ban self-downgraded). Dead-weight accounting honest including rows that make the design look worse. Design-only discipline honoured throughout.

Unverified/inherited: live 69-keys/279,810B vs 82%/408,809B mutually inconsistent (279,810/408,809 = 68.4%); AC12 gates it. Retrieval quality unassessable (no implementation; stated weights match no code). Compaction interaction "code shape, not measured". Post-D7 multi-model corroboration blocked => whole set at MEDIUM, single-source confidence.

Note for Leader on escalation: 7 of 8 blockers are narrow mechanical fixes (B1 one line + propagation; B3 one paragraph; B4 one paragraph; B5 one sentence; B6 one paragraph + AC leg; B8 one sentence; B2 a step + an AC). Only B5/B6 (metadata semantic deletion; PG table materialisation) require a decision or an inventory rather than an edit. The core design — DB-canon, the injected block, the derivation, the family mechanism, code-site placement — survived independent verification intact.

---

## Forward pointer — 2026-11-10 (cap raise; NOT a new approver iteration)

**Scope note:** this section is a DATED FORWARD POINTER added by the fix-pass coder lane. It does **not** revise, reopen, or re-score iterations 001–003 above — that historical framing is deliberately left untouched (iteration 001 opened 2026-11-06; all three iterations and the escalation stand as recorded).

- **Event:** the Emperor directed a raise of the POK active-entry admission cap **64 → 256** `[active cap 256 — Emperor directive 2026-11-10]` (2026-11-10, source: Discord). Ruling of record: `decisions.md` **ER-2**; amended constants: **A-6** / **A-13**.
- **Superseded invariant:** iteration 001's check "`…/64/…` identical across all **4** files" (`:44`) is **SUPERSEDED**. Numeric identity across four duplicating files was the fragile construct — an approver invariant that a single-value change breaks in four places. Do **not** re-assert 4-way numeric duplication in any future approval of this artifact set.
- **Single source of truth NOW:** `.agents/shared/planning/project-operating-knowledge/decisions.md` **A-13** is the ONE authoritative statement of the live sizing constant set. Other sites must REFERENCE A-13, or (where a file genuinely must carry the value) use the house marker form `[active cap 256 — Emperor directive 2026-11-10]`. No bare, unmarked `256` outside A-13.
- **Unchanged by the raise:** refuse-never-evict semantics, the archive cure, the family-max/pin-cap intersection (`PIN_CAP × FAMILY_MAX ≤ BLOCK_MAX_ENTRIES`), all per-view bounds, and every AC other than the cap-dependent leg of AC19.
