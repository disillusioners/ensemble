# Scope & Doctrine — Ensemble Decision Module (Thin Client + Seam Registry)

> **Status:** **RATIFIED P1 SHADOW-ONLY — Emperor ratification 2026-11-09: "yeah, look good, go ahead, shadow only for now".** Design of record. P1 implementation authorised (client + registry + shadow instrumentation); seams 2/3/4 unregistered; S4 shadow-only. (The leader's giter commits at finalize.)
> **Base:** branch `docs/decision-module` @ `5da1021f2` · Worktree `/home/nea/ensemble-src-wt-decision-module-design`
> **Role in this set:** **NORMATIVE ANCHOR.** This file is the single source of the doctrine, the exclusion list, the census, the proxy surface digest, and the infra precedents. Every other document in this set cites **this file by §id** and never re-quotes it.

---

## §0 Commission (restated)

Design the **ensemble side** of a decision capability: a **thin client** to the llm-supervisor-proxy `POST /v1/decisions` surface, plus a **seam registry** that decides, per decision point, whether a typed overlay may be consulted at all.

Scope items covered by this set:

| # | Scope item | Document |
|---|---|---|
| 1 | The thin client | `thin-client.md` |
| 2 | The seam registry | `seam-registry.md` |
| 3 | The seam catalog (incl. re-validation table) | `seam-registry.md` |
| 4 | Observability | `controls-and-phasing.md` |
| 5 | Kill switches | `controls-and-phasing.md` |
| 6 | Phasing | `controls-and-phasing.md` |
| 7 | Rejected alternatives | `architecture-recommendation.md` |

Everything below is the **design of record, ratified P1 SHADOW-ONLY**. The doctrine, exclusion list, census, proxy surface digest, and infra precedents stand as ratified; P1 implementation (client + registry + shadow instrumentation) is authorised. Emperor ratification of any further transition (shadow → deciding) gates later phases.

### §0.1 Citation map (how the set cites this file)

| Id | Meaning | Where it comes from |
|---|---|---|
| **D1–D5** | The five doctrine clauses | `docs/jev-system-one-integration-discussion.md` §2 + §5 + §3 (quoted once, here) |
| **EX-1 … EX-5** | The standing exclusion list entries | §3 of the source discussion doc (quoted once, here) |
| **S1 … S4** | The four candidate seams | §S.2 (this file) |
| **PX-1 … PX-9** | Proxy surface clauses | §PX (this file) |
| **IP-1 … IP-6** | Infra precedents | §IP (this file) |

---

## §D1 — Heuristic-first (the deterministic layer is permanent)

*Source: `docs/jev-system-one-integration-discussion.md` §2 "Governing principle (user-ratified 2026-09-26)", lines 22–32. Quoted VERBATIM.*

> The heuristic/logic layer is the **PERMANENT default AND fallback**. Jev is an optional overlay, active only when env-configured — not all users will have the API (cost/setup). Recorded as project critical note `e5a37ed5`.
>
> | Layer | Behavior |
> |---|---|
> | **Default (no env)** | Pure heuristic — byte-identical to today. `JEV_API_KEY` unset ≡ empty ≡ disabled (mirrors `OPENAI_MODEL_VISION` semantics) |
> | **Env configured** | Jev consulted first where wired; heuristic fallback stays |
> | **CI** | Green without key; each seam tested in BOTH modes (heuristic-only + Jev-active mocked) |
> | **Heuristic layer** | First-class forever, never demoted to "legacy path" |

**Design reading (this set):** the overlay is an *addition on top of* a permanent heuristic. `DECISION_MODEL` unset ≡ empty ≡ disabled; the disabled path must produce **byte-identical DECISIONS** — observability differs, and a suppressed row plus `decision_overlay_suppressed_total` deltas are **expected** (they are the operator's confirmation signal). The heuristic layer is never demoted to a "legacy path" and never deleted to simplify the overlay. Every seam in `seam-registry.md` is specified in **both modes** (heuristic-only, overlay-active). **The kill contract is stated once, in `seam-registry.md` §5.1.1; this file does not restate it.**

**Naming note:** the source doctrine predates the proxy surface and names "Jev" (TypeSafe System One). This set targets the **same doctrine carried by the proxy's `POST /v1/decisions`**. The quoted text is preserved verbatim; the doctrine is not re-opened, re-worded, or re-litigated here.

---

## §D2 — Fail-open (the external call never blocks)

*Source: §2, table row "Call fails / times out". Quoted VERBATIM.*

> | **Call fails / times out** | Fail-open → heuristic; external API can never block dispatch/compaction/repair |

**Design reading (this set):** **every** failure class returns the heuristic result — connection error, timeout, 4xx, 5xx, unparseable body, malformed answers, refusal, and (for the attestation lane) a missing configuration. There is no failure class that produces an error instead of the heuristic answer, and no failure class that delays the caller beyond the client timeout budget. See the exhaustive failure-mode table in `thin-client.md`.

---

## §D3 — Shadow-logging A/B before any seam decides

*Source: §2, line 34 ("Bonus"). Quoted VERBATIM.*

> **Bonus:** heuristic-underneath = permanent shadow-logging A/B baseline (log Jev choice next to heuristic choice per seam before letting Jev win).

**Design reading (this set):** the heuristic runs first and always; the overlay's answer is logged **next to** the heuristic's answer for the same input. **No seam may move from shadow to deciding until its shadow window has been reviewed and the Emperor has ratified the transition.** Shadow logging is permanent infrastructure, not a temporary scaffold — it survives promotion to deciding.

---

## §D4 — Rescue-band-only for attestation (never deny, never loosen)

*Source: §5 "LCA governance rule (must survive into any future plan)", lines 74–80. Quoted VERBATIM.*

> The `9d83f224` false-completion cycle just tightened judge semantics: judge-override → **loud** `completion_gate_escalated`; judge-silent → **withhold**, unbounded.
>
> Any Jev touch must be **rescue-band ONLY**:
>
> - Can only ADD completions the rescuer judge would, above high confidence floor.
> - Every other band falls to canonical LLM judge.
> - Gate stays **monotonically tighter-or-equal, never looser**.

**Design reading (this set):** on the attestation lane (S4) the overlay is permitted **only** to propose *additional completions* above a high-confidence floor. It may never propose a denial, a withholding, or a loosening of any rung. The `DENY` rung of the 5-step ladder remains **heuristic-owned** — the overlay never owns it. Every other band falls through to the canonical judge. The gate is monotonically tighter-or-equal: an overlay answer can move a decision *toward completion*, never *toward denial*.

---

## §D5 — Exclusion principle (exact/deterministic decisions get NO overlay)

*Commission-ratified clause (pre-ratified doctrine, not a source quote):* **exact/deterministic decisions receive NO overlay.** The standing list below is the operative set.

### §D5.1 Standing exclusion list — VERBATIM, all five entries

*Source: `docs/jev-system-one-integration-discussion.md` §3 "Candidates REJECTED after code verification (v0.15.0)", lines 50–58. Quoted VERBATIM.*

> | Seam | Why rejected |
> |---|---|
> | **Retry classification** (`daemon/llm_error_classifier.py`, wired `graph.py:8665-8667`) | Zero-LLM today: status-code sets {429,5xx,52x}, exception-type tuples, body regex, per-category budgets. Exact + infallibility-critical; ground truth lives in exception types |
> | **Compaction message selection** (L1 `instance_messaging.py:1168`; L2 `graph.py:6318,6354`; L3 `compaction.py:2243-2300`) | Selection already pure heuristic (three-bucket partition, hoisting, 10-group window, ≥60% chunking). LLM only SUMMARIZES chunks = generation, outside Jev shape. Marginal gain only in compact-now-vs-defer scoring |
> | **Snapshot L2 trigger-query generation** (`snapshot_embedding_service.py:388-458`) | Text GENERATION (novel user-style queries) — System One models don't generate |
> | **Snapshot L1 digest generation** (`executor.py:612-672`) | Prose synthesis = generation, not classification |
> | **Symptom ladder rungs** loop / truncated / empty (`graph.py:85,1142` / `:4271-4283` / `:4321-4350`) | Already exact deterministic detectors |

Stable ids for citation elsewhere in this set: **EX-1** retry classification · **EX-2** compaction message selection · **EX-3** snapshot L2 trigger-query generation · **EX-4** snapshot L1 digest generation · **EX-5** symptom ladder rungs.

### §D5.2 Extension policy (cause required + approver)

The list is **closed by default**. Extending it is a doctrine change, not an implementation detail.

1. **CAUSE (required).** An extension request must state, in writing: the decision site and its anchor; why the site was believed eligible (i.e. which part of the "exact/deterministic" judgement changed); and what makes the new site *not* exact/deterministic. A request without a cause is not reviewable and is refused.
2. **APPROVER (required).** The standing list originates in a **user-ratified** doctrine (`2026-09-26`). Only the **Emperor (the user)** may approve an extension. The architect, the planner, and the reviewer lanes may recommend; they may not approve.
3. **Registration-time enforcement.** The seam registry enforces this list **at registration time** — a registration naming an EX-1…EX-5 role is refused before any call path exists. The deny-list is *referenced* by §id in `seam-registry.md`, never re-quoted there.
4. **Downstream re-verification.** The anchors above are **v0.15.0-era** (the source document's own header states they are exact only for that commit). Before any future commission touches an excluded seam, its anchors must be re-verified at the target HEAD and a corrections table published (house convention: `decisions.md` D6 pattern — `.agents/shared/planning/promote-handback-halt/decisions.md`) — never silently patched.

**The drift list at `5da1021f2` — COMPLETE.** Every excluded seam's anchors were re-derived at `5da1021f2`; the §D5.1 quoted table above stays **verbatim** (it is correct as a quote), and the true anchors are:

| Entry | Quoted anchor (v0.15.0-era) | **True anchor at `5da1021f2`** |
|---|---|---|
| **EX-1** retry classification | wired `graph.py:8665-8667` | the classifier is invoked at **`graph.py:10568` / `:10570`** (`classify_llm_errors(llm_with_tools)` / `(llm_standard)`); `:8665-8667` is now the **tool-call-pairing guard** — unrelated |
| **EX-2** compaction message selection | L1 `instance_messaging.py:1168`; L2 `graph.py:6318,6354`; L3 `compaction.py:2243-2300` | **all three point at unrelated code**: `instance_messaging.py:1168` is `_resolve_recursion_limit_for_meta`; `graph.py:6318`/`:6354` sit inside the **fused-judge region**; `compaction.py:2240` is `def __init__(` (`:2243` is a signature parameter) |
| **EX-3** snapshot L2 trigger-query generation | `snapshot_embedding_service.py:388-458` | **`daemon/services/snapshot_embedding_service.py:388-458`** — **unchanged**; `:388` is `generate_trigger_queries` |
| **EX-4** snapshot L1 digest generation | `executor.py:612-672` | **`daemon/services/snapshot_executor.py:612-674`** — the module moved; `:612` is the "── 5. Digest LLM call" block and `:673-674` is the empty-digest raise |
| **EX-5** symptom ladder rungs | `graph.py:85,1142` / `:4271-4283` / `:4321-4350` | the rungs are **`graph.py:4558`** (`_is_truncated_response_error`), **`:4577`** (`_is_empty_response_error`) and **`:2450`** (the loop rung's `_emit_symptom_telemetry`) |

This table is **complete for the five entries** — a ratified-doctrine clause must not publish a drift list that reads as complete while being materially incomplete.

---

## §S Census (restated inline — RAG-only source, never cited as a repo file)

> **Provenance:** the census below is a **knowledge-base survey**, not a repository artifact. **No census file exists under `.agents/shared/planning/`** and none is cited as one. Its anchors were measured at `f49006b92` (v0.29.6); the **four seam entries in §S.2 are re-validated at `5da1021f2`** and are the only anchors this set relies on.

### §S.1 The scattered decision points — 16, per a dated 2026-11-08 survey

**There is no unified decision module in the daemon.** `DecisionEngine` has **zero hits repo-wide**. Every `decide()` / `judge` / `verdict` / `classifier` construct has **1–3 callers confined to one subsystem** — each lane rolls its own decision logic, and there is no shared seam abstraction to hang an overlay on.

Verified constructs from the survey (each with 1–3 callers inside a single subsystem):

| Construct | Role | Callers |
|---|---|---|
| `daemon/services/attestation_gate.py:653` `decide()` | completion gate ladder | sole caller via the graph gate node |
| `judge_fused_bundle_async` (`daemon/services/attestation_report_judge.py:703`) | fused rescue judge | **one** production call site — `daemon/graph.py:6326` (`:6317` is the import) |
| `classify_llm_errors` (`daemon/llm_error_classifier.py`) | technical retry wrapper | 3 callers — **zero business decisions** |
| snapshot L3 stage-3 search selection (`daemon/services/snapshot_search_service.py:578-685`) | ranked selection | **three** blocking call sites: tool, warm-spawn internal, search-before-create |
| busy-slow arbitration sites (`daemon/services/long_tool_nudge.py`) | timer → advisory nudge | **no classifier exists today** |
| ghost-promise detectors (`daemon/graph.py`) | phrase/structural heuristics | per-turn-end |
| R9 snapshot reuse verdict (`daemon/tools/snapshot_tools.py:328`; set-ops `_overlapping_tags` `:324-325`) | set-ops verdict | reuse path |
| **Two hand-rolled decision trees** in `daemon/services/upgrade_journal_sweep.py` (render `:981`, settle `:1037`) | render + settle paths | upgrade sweep |

**Honesty note on the count:** a **dated 2026-11-08 survey** mapped **16** scattered per-lane decision points; **the artefact is not in the repository**. The retrievable census record enumerates the constructs above verbatim but **does not individually enumerate all 16**. This set therefore carries the number **only with that dated-survey provenance**, and cites the **enumerated subset** above — it does **not** fabricate the remaining entries. The four seams in §S.2 are the surveyed subset that the commission selects for design.

**Inert surfaces (zero consumers, today):** the `decision` purpose-id model is an allowlist member with no consumer (`daemon/config.py:5925`, annotated "no current consumer" at `:5921`); `DECISION_MODEL` has **0** daemon consumers (documented at `.env.example:268`).

**Daemon ↔ proxy topology:** the daemon calls the proxy only via standard `chat/completions`, `embeddings`, and `image_generation`. There are **zero** references to decision endpoints, `jev`/`systemone`/`typesafe`, or `openrouter` anywhere in `daemon/`. The overlay surface is entirely greenfield on the ensemble side.

### §S.2 The four candidate seams (S1–S4) — re-validated anchors @ `5da1021f2`

**S1 — SNAPSHOT L3 SEARCH SELECTION — PILOT.**
Stage-3 is an LLM call, temp=0, prompt-instructed JSON `{"selected":[ids]}` over a top-20 candidate set (`_LLM_SELECT_TOP_N = 20`): `daemon/services/snapshot_search_service.py:578-685` (`_llm_select`; the `_chat_call` **invocation** is `:663-665`; the OpenAI SDK call — the only outbound stage-3 call — is `:750-757`, carrying **only** `model`/`messages`/`temperature=0.0`, so "strict JSON" is a prompt instruction, **not** a schema guarantee). Fail-soft to the **fused stage-2 composite** (BM25 + cosine + tag-overlap) **only on exception** (`:410-415`; the log string says "cosine/BM25 order" but the source is the fused composite).
**Three** consumer call sites, all **blocking** on stage 3: `daemon/tools/snapshot_tools.py:671-677` (agent-facing `snapshot_search`), `:875-882` (warm-spawn internal search, `limit=1`, degrades to a cold spawn on failure), and `:549-558` (the call spans `:549-558`; `limit=5` is at `:557`) (search-before-create inside `snapshot_create`, `limit=5`) — **the third is inside the pilot's blast radius**. ⚠️ **The snapshot gate is not the overlay's switch.** `daemon/tools/instance.py:3327-3330` (per-target-agent `meta.json` flag) governs **warm-start consumption only** — consumer 2. Consumers 1 and 3 are gated by the **tool allow-list** instead. It is therefore *not* a whole-path switch; the overlay's whole-path switch is the registry's `DECISION_SEAM_*` switch (`controls-and-phasing.md` §3). Observability is log-lines-only — `daemon/services/snapshot_metrics_service.py:31-34` **structurally forbids** ranking modules importing the metrics service (test-pinned by `TestMonitoringOnlyPin` (`tests/unit/tools/test_snapshot_v3.py:1559`)).
*Candidate-set precondition:* a `choice` question needs **≥ 2 options** or the proxy answers **400**. The candidate set is `reranked[:20]`, so it can be short — even length 1 — whenever filtering leaves few survivors. The client therefore **pre-flights `n ≥ 2`** and fails open when it does not hold (`thin-client.md` §6).
*C2 shape:* **In C2 the overlay replaces the chat call ON THE DECIDING PATH; the baseline chat selection is RETAINED (not deleted) as the KILL TARGET.** (Canonical statement — `seam-registry.md` §5.1.1.)
*Adjacent defects (out of scope, recommend a separate tiny fix commission):* malformed-JSON stage-3 returns `[]`, contradicting **two** docstrings (`:27-28` and `_parse_selection`'s `:790-792` vs code `:667-669`) — the path is **fail-closed-to-empty**, not fail-soft; the fused composite score is **discarded** before the LLM sees it (`:629`; the prompt block `:640-642` carries id/title/tags/summary only).

**S2 — BUSY-SLOW ARBITRATION — deferred.**
"Busy-slow" is **prose, not code** — **2 runtime literals + 1 docstring line** (`:1157`, `:1177`; `:1135` is a docstring); there is no identifier, state key, branch, or constant for it. Detection is elapsed-vs-per-child-threshold **strict `>`** inside `LongToolNudgeScanner.run_once`, `daemon/services/long_tool_nudge.py:1560` (comparison `:1614-1616`). Threshold chain: 5 rungs, default 900 s (constructor default `:1208`), clamp `[60,1800]` (`:80-98`, resolver `:1300-1325`; constants `:319`/`:327`/`:333`). The nudge is **advisory parent-bound text** (`:1156-1160`; delivery `enqueue_message` `:1471-1477`). `model_tier='high'` resolver: `daemon/services/instance_lifecycle.py:1326-1415`; arm path `daemon/tools/instance.py:3245-3296`; tunables `daemon/tools/tunables.py:100-207` (only `long_tool_call_threshold_seconds` is a *tunable*; `LONG_TOOL_NUDGE_ENABLED` and `LONG_TOOL_NUDGE_DEFAULT_THRESHOLD_SECONDS` are env/config knobs — `daemon/config.py:5526-5533`).
*Honest input-gap:* **only 1 of the 4 inputs a typed score would want is a structured value at the seam today** — elapsed-vs-threshold is available with zero plumbing (`:1614-1615`); the model **tier** is stored nowhere (only a model *name* persists, and only for the override / `llm_models`-pool sources); a per-episode **LLM error count does not exist at all**; and **loop-breaker "misses" do not exist** — the only structured artefact is a RAM-only loop-**repair** counter (`daemon/manager.py:6879-6912`), which is the wrong signal and is lost on restart. See `seam-registry.md` §5.2 for the deferral rationale; **no inputs are invented**.

**S3 — GHOST-PROMISE DETECTION.**
**S3's overlay consult is placed in the router's ladder-routing branch (`daemon/graph.py:4261` `should_continue`, ghost block `:4380-4402`, master gate `:4384`), inside the branch that `ENSEMBLE_SYMPTOM_REPAIR_LADDER` gates — so that switch gates the overlay as well as the routing.**
**Three distinct detectors.** (A) colon-suffix re-invoke predicate, `daemon/graph.py:4482` (predicate `content.rstrip().endswith(":")` at `:4502`; cap 3 at `:4479`; router `should_continue` `:4261`, ghost block `:4380-4402`, master gate `:4384`). (B) junk-report **structural** rule, `daemon/services/child_reports.py:1794` → `[REPORT SANITY]` marker (`daemon/constants.py:1606`; producer `child_reports.py:1739`; rollback seam `SANITY_FLAG_VERSION=1` at `daemon/constants.py:1614`) — **descriptive-only**. (C) phrase catalogs, `daemon/services/attestation_marker_scanner.py:118` (MID_WORK, **16** entries) and `:258` (CHILD_TERMINAL, **17** entries), feeding the fused judge. Both catalogs live in **one module** and the two subscribers import it (`daemon/services/attestation_gate.py:107-111`; `daemon/services/attestation_resolver_activation.py:89-93`) — "register once, two seams subscribe" holds at the module level (subscription = import).
*Hook site for (A) — ⚠️ two incompatible anchors, do not conflate them.* The **routing** decision for detector (A) lives in the router `should_continue` (`daemon/graph.py:4261`, ghost block `:4380-4402`, master gate `:4384`); the `attestation_gate_node` (`:5932`) is a **different node**. The overlay's **switch point** for S3 is therefore the **router master gate** (`:4384`); the node window is a separate observation surface. `evaluate()` runs at `:6149`; the gate-exception early-return block spans `:6170-6185`, so the **read-only post-evaluate window starts at `:6187`** (→ `~6270`), **after** `evaluate()` (`daemon/services/attestation_gate.py:1320`). ⚠️ **Scoped invariant:** "never mutate `decision.decision`" holds **in that window only** — the node body *does* mutate the decision downstream (the withhold conversion `TERMINAL_AFTER_BOUND`→`DENIED` at `:6682-6687`; the P2/P3 guard conversions at `:6716` (guard header `:6705`; conversions `:6728`/`:6788`)). The no-mutation rule is a property of the **insertion window**, not of the node.
⚠️ The **completion-check nudge** lives at `daemon/graph.py:4958` (`ATTESTATION_NUDGE_TEXT`) and `:7069-7075` (deny-path nudge-body selection) — **not** in `long_tool_nudge.py` (that file is the slow-tool watchdog).
*Observability:* `daemon/services/report_integrity_metrics.py:38-92` (counter + INFO line + sink seam) — **not greenfield**: the family `report_integrity_junk_report_total` already exists (`:59`; name constant `daemon/constants.py:1621`).

**S4 — ATTESTATION RESCUE-LANE — LAST, rescue-band-only.**
5-step `decide()` ladder, `daemon/services/attestation_gate.py:653`: (1) `user_answer_pending` ⇒ pending-wakeup allow (`:875`); (2) any pending input > 0 ⇒ pending-wakeup allow (`:920`); (3) attested ∧ quiet ⇒ standalone report ⇒ allow (`:973`), otherwise HOLD + reminder (`:990`); (4) ¬attested ∧ quiet ∧ `denied_count + 1 > bound` ⇒ TERMINAL (`:1016` main path / `:944` error variant, bound default 3); (5) otherwise ⇒ **DENY** + nudge (`:1024` main path / `:950` error variant). Reminder cap 2 (constant `daemon/graph.py:5059`; texts `:5032`/`:5044`; injection `:7200-7310`).
Judge rescue `judge_fused_bundle_async`, `daemon/services/attestation_report_judge.py:703`, invoked `daemon/graph.py:6326` — the judge is **a RESCUER, never the denier** (doc `:6214`; impl `:6330-6346`; the rescue itself `:6398-6420`). The **only** `DENIED → ALLOW` flip in the codebase is that rescue, and it is triple-gated (deny band `:6391`, would-be-DENY veto `:6283-6286`, `fused_result.is_complete` `:6398`). The `DENY` rung is minted at **two** sites, **both inside `decide()`**: **`attestation_gate.py:1024`** — step 5, the **main path** that runs every non-attested turn (comment "(5) deny — increment + nudge." at `:1022`) — and **`:950`**, the `report_obligation_read_error` **error-path variant**. `TERMINAL_AFTER_BOUND` is likewise minted at two sites: **`:1016`** (main path, step 4) and **`:944`** (the error-path variant). The composition gate can only *tighten* a decision (`TERMINAL_AFTER_BOUND` → `DENIED`, `daemon/graph.py:6682-6687`). **An implementation guarding `:950` alone would leave the primary deny path unguarded** — both sites are in scope.
Judge-silent ⇒ withheld (`daemon/graph.py:6637-6699`): a budget-exhausted withhold keeps the loud terminal exit; an unexhausted one converts to DENY + continuation. ⚠️ The withhold arc is unbounded **in epochs** (exits: attest / finish / ask user), but per-dispatch conversions are **budget-capped at 8** since P1 (2026-09-30) — `WITHHOLD_DENY_BUDGET_DEFAULT` (`daemon/services/attestation_gate.py:613`). That budget is the **withhold-conversion** budget, **not** the judge's HTTP budget (the judge makes ≤2 attempts per invocation).
Band-classifier host: `activation_predicate`, `daemon/services/attestation_resolver_activation.py:962` — pure, no I/O; band precedence `deny > marker > a_suspicion` (`:1096-1102`), band constants `:147-150`. The **judge plan** is gated on the deny band at `daemon/graph.py:6283-6288`; the judge **call** is at `:6326` behind `fused_judge_on` (`:6289-6296`). ⚠️ The "4th band" framing is **not** how the code is organised — see §S.3 row 14; the overlay's rescue position is the judge-call gate.
*Best-in-class observability:* the `event=leader_completion_gate` rows at `daemon/services/attestation_gate.py:2095-2138` — the emitted row carries **29 key=value fields** (28 format slots + the literal `event=`); the "17" is the *canonical log schema* — FR-10, extended by `attestation_required` as the **11th of the 17** fields (at `daemon/services/attestation_gate.py:1250`, right after `live_descendants` `:1249`), defined in `.agents/shared/planning/leader-completion-attestation/requirements.md:731` and exported as `daemon.services.attestation_gate.CANONICAL_LOG_SCHEMA_FIELDS`. The delegation/marker/length diagnostics sit explicitly outside that tuple, and the canonical roster is **not restated in-file** — use the 28-name list for greps. The A/B baseline for S4 is these rows **plus** the existing promotion counters `dry_log_total` / `dry_log_deny_predicate_total` / `enforce_denied_total` (`daemon/services/attestation_resolver.py:123-130`) — **not greenfield**.

**Cross-cutting rule:** S3 and S4 share the **same** phrase catalogs. The registry registers the **scanner once**; S3 and S4 subscribe. It is never double-registered.

---


### §S.3 Anchor corrections table (verified at `5da1021f2`)

House convention: corrections are **published, never silently patched**. Each row below was re-verified against the worktree at `5da1021f2` by a read-only verification pass.

| # | Anchor as originally stated | Corrected at `5da1021f2` | Note |
|---|---|---|---|
| 1 | `daemon/tools/long_tool_nudge.py` | **`daemon/services/long_tool_nudge.py`** (1815 lines) | path drift |
| 2 | `daemon/tunables.py` | **`daemon/tools/tunables.py`** (217 lines) | path drift |
| 3 | metrics guard `:30-33` | **`:31-34`** | 1-line shift |
| 4 | S1 consumers: two sites | **three** sites — `snapshot_tools.py:671-677`, `:875-882`, **`:549-558`** (`limit=5` at `:557`) | the search-before-create site also blocks on stage 3 |
| 5 | "chat call `:663-665`" | `:663-665` is the `_chat_call` **invocation**; the SDK call is **`:750-757`** | cite both |
| 6 | "strict-JSON" | **prompt-instructed only** — the request carries no `response_format`/schema | no schema guarantee exists |
| 7 | fallback = "cosine/BM25 order" | fallback = the **fused stage-2 composite**; only the log string says cosine/BM25 | code is right, message is loose |
| 8 | tunables = threshold chain | only `long_tool_call_threshold_seconds` is a tunable; the kill-switch + default are env/config | `daemon/config.py:5526-5533` |
| 9 | "loop-breaker misses" | a RAM-only loop-**repair** counter exists (`manager.py:6879-6912`); **misses do not** | wrong signal, lost on restart |
| 10 | S3 "never mutate `decision.decision`" | holds **only in the read-only post-evaluate window** (`graph.py:6187` → `~6270`); the node body mutates downstream (`:6682-6687`; the P2/P3 guard opens at `:6716`, header `:6705`, conversions `:6728`/`:6788`) | scope the invariant to the insertion window |
| 11 | S4 "judge-silent ⇒ terminal withheld, **unbounded**" | the withhold arc is unbounded **in epochs**, but per-dispatch conversions are budget-capped at **8** since P1 (`attestation_gate.py:613`) | "unbounded" is stale as an absolute |
| 12 | S4 "budget 8" read as the judge's budget | it is the **withhold-conversion** budget; the judge makes **≤2 HTTP attempts** per invocation | two distinct budgets |
| 13 | S4 "**17-field** `leader_completion_gate` rows" (`:2095-2110`) | the emitted row spans `:2095-2138` and carries **29 key=value fields** (28 format slots + the literal `event=`); "17" is the **tuple length** (`CANONICAL_LOG_SCHEMA_FIELDS`, `daemon/services/attestation_gate.py:1239-1258`) and `attestation_required` sits at **position 11** (`:1250`, right after `live_descendants` `:1249`) — `requirements.md:731` proves the 17-field schema, **not** position 17 | use the 28-name list for greps |
| 14 | S4 "4th band before judge plan" | the bands are computed inside `evaluate()` by the activation predicate (`daemon/services/attestation_resolver_activation.py:1096-1102`: `deny > marker > a_suspicion`); the judge plan is gated on the **deny band** (`graph.py:6283-6288`) | the overlay's rescue position is the judge-call gate (`:6289-6296` → `:6326`) |
| 15 | "all four seams have ZERO metric families — greenfield" | S3 has `report_integrity_junk_report_total`; S4 has three promotion counters + the gate row family | greenfield = **new** counter families only |
| 16 | `SANITY_FLAG_VERSION` at `daemon/constants.py:1615` | **`:1614`** | 1-line drift |
| 17 | `WITHHOLD_DENY_BUDGET_DEFAULT` at `attestation_gate.py:614` | **`:613`** | 1-line drift |
| 18 | `.env.example:268-275` documents `DECISION_MODEL` **and** `DECISION_BASE_URL` **and** `DECISION_API_KEY` | only **`DECISION_MODEL`** is documented (`:268-276`); the other two do **not** exist there | BASE_URL/API_KEY are **proposed** names, not pre-carved |
| 19 | `confidence` exists **only on choice** answers | `confidence` is populated on **score** answers too when the upstream supplies it (`pkg/decision/typesafe.go:700-703`); never on predicate | "only choice" was the doc-comment's framing, not the wire's |
| 20 | a `choice` answer carries one picked value | it carries the picked value **plus the full per-option distribution** (`probabilities[]` of `{value, probability}`) | this is what makes the S1 multi-select contract expressible in **one** call |
| 21 | `input` is a string | it is `string \| DecisionInputMessage[]`, and the edge validators **do not check it** — a missing/empty `input` is mis-signalled as a **502** | the client must pre-flight validate it |
| 22 | S4 withhold conversion `TERMINAL_AFTER_BOUND`→`DENIED` at `graph.py:6655-6662` | **`:6682-6687`** — the cited range was the budget-exhausted **KEEP** branch | council-verified, second pass |
| 23 | `confidence` synthesized at `pkg/decision/typesafe.go:704-707` | **`:700-703`** | council-verified, second pass |
| 24 | S3 master gate at `graph.py:4385`; S3 read-only window from `:6167` | master gate **`:4384`**; the gate-exception early-return spans `:6170-6185`, so the window starts at **`:6187`** | council-verified, second pass |
| 25 | `_parse_selection` def at `:784` | **`:785`** (body `:785-812`) | council-verified, second pass |
| 26 | `substitute_env_vars` called at `config.py:7149` | **`:7157`** — `:7149` is `yaml.safe_load(f)` | council-verified, third pass; this is the anchor the boot-snapshot argument leans on |
| 27 | "DENY is minted **only** at `attestation_gate.py:950`" | **two mint sites, both inside `decide()`**: **`:1024`** (step 5, the **main path** — comment "(5) deny" at `:1022`) and **`:950`** (the `report_obligation_read_error` **error variant**). `TERMINAL_AFTER_BOUND`: **`:1016`** (main) and **`:944`** (error variant) | approver-verified — guarding `:950` alone leaves the primary deny path unguarded |
| 28 | EX-1 wired at `graph.py:8665-8667` | **`:10568` / `:10570`** (`classify_llm_errors(llm_with_tools)` / `(llm_standard)`); `:8665-8667` is the **tool-call-pairing guard** | approver-verified |
| 29 | EX-2 anchors `instance_messaging.py:1168` / `graph.py:6318,6354` / `compaction.py:2243-2300` | **all three unrelated**: `instance_messaging.py:1168` = `_resolve_recursion_limit_for_meta`; `graph.py:6318`/`:6354` = the **fused-judge region**; `compaction.py:2240` = `def __init__(` (`:2243` is a signature parameter) | approver-verified; the §D5.2 drift table now publishes these |
| 30 | EX-5 rungs `graph.py:85,1142` / `:4271-4283` / `:4321-4350` | **`:4558`** (`_is_truncated_response_error`), **`:4577`** (`_is_empty_response_error`), **`:2450`** (the loop rung's `_emit_symptom_telemetry`) | approver-verified |
| 31 | census: `daemon/tools/upgrade_journal_sweep.py` | **`daemon/services/upgrade_journal_sweep.py`** — render `:981`, settle `:1037` | approver-verified; the `daemon/tools/` path does not exist |
| 32 | census: R9 reuse verdict `snapshot_tools.py:365-381` | **`:328`** (`_r9_reuse_verdict`); set-ops `_overlapping_tags` **`:324-325`** | approver-verified; `:365-381` is the git-provenance block |
| 33 | census: judge "single caller `graph.py:6317`" | **`:6326`** is the sole production **call**; `:6317` is the **import** | approver-verified; §S.1 now reads ":6317 import, :6326 call" |
| 34 | "structured log telemetry from the `agent_repair_ghost` node (`graph.py:4372-4378`)" | **`graph.py:4370-4378` is a CODE COMMENT**, not telemetry | approver-verified; the telemetry is emitted from the node, per the comment |
| 35 | "`attestation_required` … the **17th** canonical field" | `CANONICAL_LOG_SCHEMA_FIELDS` (`daemon/services/attestation_gate.py:1239`) holds **17** entries; `attestation_required` is the **11th** | approver-verified; the "17" is the tuple length, not its position |
| 36 | S4 gate-exception early-return `:6159-6185` | **`:6170-6185`** (the `except Exception as gate_exc` arm opens at **`:6170`**; `:6171` is `logger.error`) | approver-verified |
| 37 | S4 deny band `:6391` | **`:6391`** — the original was **correct**; `graph.py:6390` is `if act.fired and resolver_snapshot.mode == "enforce":` | the truth table found the **correction** itself was wrong — restored to the original |
| 38 | `_parse_selection` def — re-checked against the approver's `:786` | **`:785`** — `sed -n '783,790p'` shows 783 blank, 784 `@staticmethod`, **785 `def _parse_selection`**; `:786` is its docstring | source-verified; the approver's value is off by one |
| 39 | S4 "P2/P3 guard conversions at `:6710+`" | the guard **header** is **`:6705`** ("P2 + P3 guards (2026-09-30 …)") and the **conversion site opens at `:6716`** (`if (`; conversions at `:6728`/`:6788`) | approver-verified |
| 40 | `MonitoringOnlyPinTest` | the class is **`TestMonitoringOnlyPin`** (`tests/unit/tools/test_snapshot_v3.py:1559`) | approver-verified; the pin is real (an AST-walk over the snapshot modules) |
| 41 | PX-3 "`predicate` → `probability` **only**" | the wire answer is `{type, name?, probability?, choice?, probabilities?, confidence?, score?, refusal?}` (`pkg/decision/types.go:167-193`; `MarshalJSON` `:202-231`); `type` is **always** emitted and `name` is the **join key** to `questions[].name` | the old clause contradicted its own digest; `probability only` is now scoped to the predicate's *fields*, not the answer's shape |
| 42 | IP-3 client transport template — POST `:780` | the request is **executed** at **`:802`** (`await client.post(...)`); `:780` is the URL construction | approver-verified |
| 43 | thin-client §6 status taxonomy (409 / 422 / 429 / 503) | the route emits **400** for request-shape rejections and **never** 409/422/429/503; **401** carries `authentication_error` and **405** a plain-text body — the 4xx envelope is **not** uniformly `server_error` | approver-verified; the status labels were corrected |
| 44 | per-token model allow-list | the reused `OPENAI_API_KEY` inherits the proxy's **per-token model allow-list**; a non-empty list excluding the decision model ⇒ **every consult 403** ⇒ fail open (`pkg/auth/token.go:35` `AllowedModels`, `:129-144` `IsModelAllowed`; enforced at `pkg/decision/handler.go:281-293`, 403 at `:718-727`) | approver-verified; added to the operator prerequisites and the liveness observable |
| 45 | `levels[]` shape | a score level is `{label, description?}` and **`label` is required** (`pkg/decision/types.go:86-92`) — but this is **wire-shape / System One semantics, not a server-side 400**: no validator rejects an empty label | approver-verified |
| 46 | "the 185→187 delta is only a provider" | the delta is **larger**: the proxy→upstream wire shape and the URL construction changed as well as the `openrouter` provider | approver-verified |
| 47 | thin-client §6 **row 17** (historically row 18) — 502 "no decision adapter registered" as its own class | the proxy emits **502 for 7 handler-emitted causes + 1 relayed upstream envelope** (credential unresolved `:307`, provider-not-registered `:325`, build failed `:366`, upstream transport `:433`, upstream read `:465`, over-cap `:478`, inspect error `:497`, relayed upstream envelope) and the no-adapter case is **not uniquely identifiable by status** | the row is **folded into the single `http_502` label**; the no-adapter case is resolved at **provisioning** time, never by body text |
| 48 | S3 hook site — the attestation-gate node window `:6187`→`~6270` | S3's overlay consult is placed in the **router's** ladder-routing branch (`daemon/graph.py:4261` `should_continue`, ghost block `:4380-4402`, master gate `:4384`); the node window belongs to the **S4** gate surface | approver-verified — the two anchors are different code paths and are not interchangeable |

## §PX Proxy surface digest — `POST /v1/decisions`

Verified against the deployed build 185 (`9cf17bb`) and v187 (`43fb7e5`). **The client-facing contract is byte-identical across the two; v187 adds the `openrouter` provider** (the 185→187 delta is larger than a provider — the proxy→upstream wire shape and URL construction changed too — but **not** the client-facing contract this client depends on).

- **PX-1 — Typed kinds are per-question:** `predicate` | `choice` | `score`. **Refusal is answer-side only** — never a request question type.
- **PX-2 — Request:** `{model, input, questions[{type, name, instructions?, choices?[], levels?[]}], safety_identifier?}`. **`input` is `string | DecisionInputMessage[]`** and the request-edge validators do **not** check it (`pkg/decision/validators.go:61-71` `allValidators()`; the validator set spans `:76-217`) — an omitted or empty `input` is **mis-signalled upstream as a 502**, not a 400, so the client must **pre-flight validate** it. `choice` needs 2–255 options (`choices[]` entries are `{value, description?}`); `score` needs 2–10 levels.
- **PX-3 — Response:** `{model, answers[], usage}`. The wire answer is **`{type, name?, probability?, choice?, probabilities?, confidence?, score?, refusal?}`** (`pkg/decision/types.go:167-193`; hand-rolled `MarshalJSON` `:202-231`). **`type` is ALWAYS emitted**; **`name` identifies the question the answer belongs to** — proxy-synthesized on the System One path, upstream-supplied on the openai path. **`answers[].name` is the JOIN KEY to `questions[].name`.** Per type: `predicate` → `probability`; `choice` → the picked `choice` value **plus `probabilities[]` — the FULL per-option distribution of `{value, probability}` — plus `confidence`**; `score` → a scalar `score` **plus `probabilities[]` (level-indexed `{value, label, probability}`)**, and `confidence` when the upstream supplies it; `refusal` → a `refusal` string. **An answer whose `name` is missing or does not match any question ⇒ FAIL OPEN** (no join ⇒ no usable answer).
- **PX-4 — No provider field in the request.** The provider is bound to the **credential row** of the model (`kind=decision` gate → credential → registry lookup by `cred.Provider`). Multiple providers ⇒ multiple model rows ⇒ **operator provisioning is a prerequisite**.
- **PX-5 — Decision models are EXCLUDED from `GET /v1/models`** (ids are configured, not discovered).
- **PX-6 — Budgets:** proxy decision timeout default 30 s, clamp `[10,60]`; blocking single JSON (**no SSE**); **no proxy-side retry/fallback**; **no idempotency keys** (a retry double-bills); **no inbound rate limiting** (the client is the only throttle); client disconnect does **not** cancel upstream (the handler builds its context from `context.Background()`, not the request context — abandoned calls still bill); 16 MB request / 64 MB response caps; responses never gzipped (compression is scoped to `/fe/api/*`).
- **PX-7 — Auth:** reuse the existing token store — `OPENAI_BASE_URL` + `OPENAI_API_KEY`. URL = `OPENAI_BASE_URL` + `/decisions` (the base already carries `/v1`; **never append `/v1` again**). Header precedent: `Bearer` + `x-proxy-app: ensemble` (`daemon/tools/image_gen.py:140-143`).
- **PX-8 — Errors: branch on HTTP STATUS only.** The 403 body is structurally non-standard; other 4xx carry `type:"server_error"` **except 401 (`authentication_error`, `pkg/models/errors.go:9`) and 403 (a non-standard `access_denied` body)**; 502 relays the upstream envelope **verbatim**. **Never parse message text.** **Any 502 ⇒ fail open.** The "no decision adapter registered" case is **not separately detectable** by the client and is **not** a body-text classification — it is handled at provisioning time. **Deploy-order non-dependence:** no phase may assume `openrouter` exists — openrouter decisions do not exist on the ensem line until v187 lands.
- **PX-9 — The decision lane is always-on in code** (no `DECISION_ENABLED`-style flag). "Disabled" is a *deployed state* (upstream 4xx, missing credential, dead base); `models.enabled=0` does not gate the route.

---

## §IP Infra precedents digest

- **IP-1 — Env seam (one pre-carved name, two proposed).** `DECISION_MODEL` → literal purpose-id `"decision"` (**never** `OPENAI_MODEL`) — **pre-carved**: the purpose-id is already in `_ALLOWED_MODELS_DEFAULT` (`daemon/config.py:5911-5926`; member `:5925`; "no current consumer" comment `:5921`) and the variable is pre-documented at `.env.example:268-276`. `DECISION_BASE_URL` → `OPENAI_BASE_URL` and `DECISION_API_KEY` → `OPENAI_API_KEY` (**reuse, never mint new key material**) are **PROPOSED names with proposed reuse semantics** — they do **not** appear in `.env.example` today. **Zero daemon consumers for any `DECISION_*` variable.** Config chain: `config.yaml` → `daemon/config.py` `substitute_env_vars` (`:66-83`, called from `load_config` **`:7157`**) → `LLMConfig` (`:139-230`, `env_prefix="OPENAI_"`) → `Config.llm` (`:5728`); because a `DECISION_*` name carries **no** `OPENAI_` prefix, it needs an explicit `config.yaml` entry or an explicit read in `load_config` (`:7150-7300`).
- **IP-2 — Failover machinery:** `daemon/services/llm_failover.py` (`current_failover_url` `:369-388`; `invoke_raw_with_failover` `:700-861`). ⚠️ `daemon/graph.py:3924` `clean_llm_config` **strips `base_url_backup`** — raw lanes read it themselves. There is no httpx-shaped failover helper a decisions client could reuse without inventing one.
- **IP-3 — Client transport template:** `daemon/tools/image_gen.py` — the request is **executed** at `:802` (`:780` constructs the URL); headers `:140-143`/`:781-784`; **single attempt**; `asyncio.wait_for` timeout `:817-819`; 1 MiB cap; fail-closed error envelope `:317-326`; SSRF guard `:177-231`. **THE DELTA:** image_gen is **fail-closed**; the decisions client is **fail-open** (D2).
- **IP-4 — Metrics registry:** registration-time `register_counter`/`gauge`/`histogram` (`daemon/checkpoint_metrics.py:1139-1243`); metric metadata `family`/`source_idiom`/`durability`/`cardinality_weight`/`chart_role` (`:618-665`); 15 s cadence ring buffers; pull adapters; `/api/metrics` door; the 13 chart-primary family roster invariant **fails loud** (`daemon/**/roster.py:259-282`). ⚠️ **The "all four seams are greenfield" claim is only partly true** (verified): S3 already has `report_integrity_junk_report_total` (`daemon/services/report_integrity_metrics.py:59`; name constant `daemon/constants.py:1621`), S4 already has the three promotion counters (`daemon/services/attestation_resolver.py:123-130`) and the `leader_completion_gate` row family, and S3's ghost routing emits structured log telemetry (`daemon/graph.py:2738-2745` (`:4370-4378` is the **comment** describing it)). **Greenfield applies only to NEW counter families** — and the repository has **no Prometheus/OTel client** ("NO metrics infra", `report_integrity_metrics.py:8`), so a "metric family" here is a module counter / structured log row / sink seam. Derive label vocabulary from code (`daemon/**/wiring/vocab.py`); join the roster **only** if chart-primary.
- **IP-5 — Charter for the client:** **single-attempt, NO retry, NO failover machinery** (leader-ruled; the image_gen precedent is single-attempt). Client timeout **5–10 s**, justified per lane against that lane's latency tolerance. **Fail-open on every failure class.**
- **IP-6 — The census artifact is RAG-only** — restate content, never cite a repo file.

---

## §CV Sibling documents

| Document | Depends on (from this file) |
|---|---|
| `thin-client.md` | D1, D2, PX-1…PX-9, IP-1, IP-2, IP-3, IP-5 |
| `seam-registry.md` | D1, D3, D4, D5, EX-1…EX-5, S1…S4, IP-4 |
| `controls-and-phasing.md` | D2, D3, D4, S1…S4, IP-4 |
| `architecture-recommendation.md` | all of the above |
