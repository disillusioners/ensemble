# Architecture Recommendation — `prompt_reload(compact, compact_note)` (note-guided compaction)

**Status:** DECISION OF RECORD — **v1.4 @ `a95edb33`** is the architecture ruling of record. This commit carries the **same-pass corrections** the leader ordered on top of it: (1) §I routing alignment to the lane's OWN three packs (the plan's routing governs); (2) the §D consumption heading aligned to the at-most-once wording (Risk #3's two-store carve-out); (3) no-sentence-may-imply-an-LLM-consumer-ships correction in §C/§F; (4) the latest-amendment pointer naming v1.4 @ `a95edb33`. **No substantive architecture ruling changes.** Changelog at §13.
**Date:** 2026-11-17 (UTC)
**Base:** `feature/prompt-reload-compact-note` @ `858a85e31489c7db9e9275955e63c021cfc5a2c4` == `origin/latest` (worktree `/home/nea/ensemble-src-wt-prompt-reload-compact`, porcelain clean at every read)
**Method:** 3 dispatched analysts (`data-flow-design` ×2, `structural-design` ×1) + 1 governor council (2 models, `trade-off-analysis`) + 1 review council (C-1 cross-exam) + architect's own re-measurement of every load-bearing anchor. Every `file:line` below was **measured at use time**; anchors from second-hand sources were re-pinned where drifted.
**Siblings:** `plan-overview.md` + `decisions.md` (planner's engine-agnostic half, commit `a021194bd`) are untouched; this document is the engine-shape + governance ruling they were waiting on.

---

## ⚠ SUBSTITUTION DISCLOSURE — read this first

**The Emperor asked for "decision engine (jev model)" to filter messages. This design delivers a DETERMINISTIC, ZERO-LLM MARKER MATCHER instead — and that substitution is the point, not a compromise.** The measured reason: **the decision family structurally cannot drop messages.** Three independent walls, all measured: (1) `compaction_message_selection` is on the standing-exclusion list `DENIED_SEAM_ROLES` (**EX-2**, `daemon/services/decision/registry.py:85`, registration refused *before any call path exists* at `:275-280`); (2) the `Monotonicity` enum (`daemon/services/decision/types.py:92-97`) has **no removal-admitting direction** — add-only demands superset (`registry.py:616-623`), `WITHIN_SET` may never empty (`:629-630`), violations **fail open to the heuristic** (an armed deciding filter silently no-ops); (3) `transition_stage` (`registry.py:355-387`) refuses shadow→deciding without a ratified `ReviewRecord` — the D3 gate is structural, and no production caller exists. Wiring the Emperor's request literally would require removing a ratified exclusion, adding an enum direction, and ratifying a deciding stage — **all three are his, and none is smuggled in this release.** The marker matcher delivers the *capability* (mechanically-decidable pre-filtering) with zero LLM cost, zero doctrine exposure, and provable semantics. **If the Emperor rules "no deciding surface ever," delete the stage — it is ~30 lines + tests, default-OFF, and nothing else depends on it** (see Decisions Pending §11.2 and the pre-filter section §F).

---

## 0. Corrections to the commission's orientation (measured)

1. **"The SAME seam existing auto-compaction uses — turn boundary; NEVER mid-turn" is false as a description of today's system.** Auto-compaction fires at **four** `ContextCompactor.compact_state` sites, two of them mid-superstep:

   | # | Site | Owner | mid_turn | abort_policy |
   |---|---|---|---|---|
   | T1 | `daemon/graph.py:8394` | `_maybe_precall_compact_95` (def `:8138`) — 95% pre-call hook | **True** (`:8412`) | fail_open |
   | T2 | `daemon/graph.py:9990/9998/10019` | reactive `ContextLengthExceededError` handler (except `:9890`) | **True** (`:10058`) | fail-open → force retry |
   | T3 | `daemon/services/instance_messaging.py:1535` | `_maybe_compact_context` (def `:1337`) — pre-dispatch gate | False | fail_open (`:1585-1586`) |
   | T4 | `daemon/services/compact_executor.py:1220` | `execute_compact` (def `:555`) — `/compact` command | False | **raise** (`:1822-1823`) |

   The seam OWNER for all four is `daemon/services/_compaction_persist_seam.py::persist_compaction_result` (`:72`): Variant A (`mid_turn=False`, two `aupdate_state` **without** `as_node`, `:267/:271`) vs Variant B (`mid_turn=True`, **with** `as_node='agent'`, `:255/:261`). "Never mid-turn" is therefore a **constraint this lane imposes on the tool-initiated path**, not an inherited property. Ruled in §H.
2. **The degrade line is a literal constant in daemon code**: `GLOBAL_DOC_PLACEHOLDER_GLOBAL = "(overview unavailable — merge pass failed; the sections below are authoritative)"` at `daemon/compaction.py:156-159`, rendered at `:1030-1032` when `global_overview is None` (set at `:3697` and `:3778`). The Emperor's live evidence is verified verbatim. No `agents/**/*.md` contains either string — the defect lives entirely in the engine.
3. **The GLOBAL render site is `daemon/compaction.py:998-1032` ONLY** (inside `build_compaction_doc`, def `:874`). The `:809`/`:835` placeholder references are token-math inside `_apply_ceiling_rule` (def `:788`), not renders.
4. **There is no compaction table.** The compaction record IS the single `compaction-global-{iid}-{seq}` SystemMessage persisted into the LangGraph checkpoint `messages` channel (`daemon/compaction.py:1053-1056`; seam `daemon/services/_compaction_persist_seam.py:224→255/:267`). The only `.sql` mentioning compaction is a **comment** (`daemon/migrations/versions/20260825_000001_create_message_metadata.sql:24`). Checkpoint tables are langgraph-embedded DDL (`AsyncPostgresSaver.setup()`), outside our `.sql` ledger.
5. **`prompt_reload` writes no DB row today** — its effect is in-process (`prompt_cache.invalidate` at `daemon/services/prompt_refresh.py:876/:889`; the bare pop `instances.pop(instance_id)` at `:969`; RAM-only `_BASELINE_MAP` at `:306-325`). Its audit surface is the ONE frozen INFO line (`daemon/tools/prompt_reload.py:113-135`). A restart-durable note **cannot** mirror this precedent — ruled in §D.
6. **`emergency_truncate` (def `daemon/compaction.py:1645`) bypasses the GLOBAL render entirely** — `compaction_type="emergency_truncation"` (`:3560`), no doc, no envelope, no placeholder; its C1 head-pop (`:1725-1727`) drops oldest messages arbitrarily. Contrast: the `|S|=0` **truncation fallback** (`_truncate_fallback`, def `:4891`) DOES build a doc at `:5035` (measured: `mode="truncation"`, `global_overview=global_text or ""`) and DOES hit the render chain.
7. **(v1.1, CORRECTED v1.3) The below-floor fall-through is `force OR over_budget` — NOT `force` alone.** Measured at `daemon/compaction.py:3357-3389`: below floor (`len(selectable_messages) < min_messages_before_compaction`) computes `over_budget` (`:3358-3368`); the **clean skip requires `not force AND not over_budget`** (`:3369-3373`); otherwise WARNING (`:3374-3384`) and fall-through to `_last_effort_tail_truncation` (`:3385-3389`) — *"last-effort floor (50%-tail truncation)"*. **v1.2's claim "NO tail truncation — `force=False` by construction" was FALSE in the below-floor ∧ over-budget corner** (few-but-huge messages). Closed by the C-1 fix, §D.2(iv). The floor is engine-side and pre-existing: `min_messages_before_compaction` default 10 (`daemon/config.py:1660`; `daemon/constants.py:603`).
8. **(v1.2, D25) There are THREE live `_merge_summaries(` call sites, not two.** Measured: `:3687` (call span `:3687-3691`) — the outer **re-merge** inside `if outcome.merge_failed:` (`:3657`) on the full-success path; `:3768` (span `:3768-3772`) — the partial path (`:3649`, stop_reason ∈ {timeout, budget}); **`:4187` (span `:4187-4191`) — the INNER merge inside `_summarize_chunked` (def `:3922`, its sole caller `:3624`), on the full-success branch (`if partial_summaries and stop_reason == "completed"`, ≥2 summaries)**. On inner-merge SUCCESS the outcome collapses to `summaries=[merged]` and **the outer sites never execute** — a skip gate placed only at `:3687`/`:3768` is dead code on that path. Ruled in §D.1.
9. **(v1.3, C-1) The four-part consumption hole** — measured, all four parts verified:
   **(a)** the below-floor fall-through is `force OR over_budget` (`:3369` → `:3385-3389`) — armed T3 with `force=False` still 50%-tail-truncates when below-floor ∧ over-budget (§0.7);
   **(b)** T4 `/compact` consumes the arm while calling `compact_state(ctx, force=True)` (`compact_executor.py:1220`; its pre-checks `:1199-1203` bypass only the threshold; the module docstring `:12-16` says *"falls through … only on over-budget OR force=True"* and *"Under force, the engine still honors min-messages"* — the misleading wording part (iii) corrects) — below-floor `/compact` + armed note = the FORBIDDEN tail truncation;
   **(c)** `_last_effort_tail_truncation` (def `:2518`; `COMPACTION_TYPE_TAIL_TRUNCATION_LAST_EFFORT = "tail_truncation_last_effort"` at `:1880`, outcome `:2563`) returns a real **non-empty replacement with NO GLOBAL doc** — the note can NEVER render on that outcome;
   **(d)** v1.2's ES-7/ES-5 predicate (`replacement_messages` non-empty ∧ persist `True`) **satisfies both conjuncts on that outcome** → arm consumed, note silently lost, `compact_note_last_applied_at` written = **false "applied" provenance that also falsely arms the 900s rate limit**.
   Ruled in §D.2.

---

## A. Engine shape — RULING: **(C) Hybrid**, which subsumes Ari's recommendation for the note layer and overrules her on exactly one point

The note is **engine data**: one additive field on `CompactionContext` (dataclass at `daemon/compaction.py:1276`; the additive-optional-field precedent is `:1295-1318`), consumed by `compaction.py` at the existing render site. The pre-filter is a **separate, guarded, default-OFF stage** in its own module, lazily imported (cycle-safe).

**Ari's recommendation is ~90% adopted.** The overrule is narrow and cited: she proposed the pre-filter live *inside* the upgraded pipeline; I place it in a separate module because (i) `daemon/compaction.py` is a measured **god module** — 5206 lines, 1 god class (`ContextCompactor`, 25 methods) + 24 free functions, flat call graph — and (ii) a hard import cycle exists: `daemon/services/__init__.py:17` → `instance_lifecycle.py:59` → `from ..compaction import ContextCompactor`, so `compaction.py` cannot top-level-import a sibling service; the pre-filter module must be top-level `daemon/_*` or function-body-imported (the `prompt_refresh` lazy-import pattern).

**(B) new dreamer engine is REFUSED** (both council models, independently, identical grounds): a second engine forks the doc format, persist recipes, ceiling rule (`:788`), sentinel (`:317`), pre-write guard (`:480-495`), and 60s dedup (`:5091`) — thousands of lines of mutation-proven invariants re-proved from scratch. Shape C is also *largely the existing architecture*: `compact_executor.py` (1944 lines) + the persist seam already split concerns; the genuinely new surface is thin.

### 5-axis matrix (council-weighted C20/S20/M25/R20/C15; Risk/Cost inverted, 5=best)

| Approach | Complexity | Scalability | Maintainability | Risk | Cost | Recommendation |
|---|---|---|---|---|---|---|
| **A: upgrade in place** (everything inside `compaction.py`) | 3–3 | 3–4 | 3 | 3–4 | 4–5 | Supersetted by C — god-module growth on a 5206-line file is real, not hypothetical |
| **B: new dreamer/distill engine** | 1 | 3 | 1–2 | 2 | 2 | **REFUSED** — parallel-engine drift on mutation-proven invariants |
| **C: hybrid** (engine stays; note = engine data; pre-filter = separate default-OFF stage) | 4 | 4 | 4–5 | 4 | 4 | ✅ **RECOMMENDED** (council: 4.00–4.25 vs A 3.15–3.70 vs B 1.75–2.00) |

**Files that change (complete inventory — all inside the leader's widened edit surface):** `daemon/compaction.py` (CompactionContext +1 field; one render gate; one conditional in the ceiling numerator; **the `_merge_summaries` entry guard**; **the note-gated armed-aware floor predicate**), `daemon/services/instance_messaging.py` (T3 gate: armed-check threshold bypass), `daemon/services/compact_executor.py` (**arm ⇒ `force=False`** + docstring/comment corrections), `daemon/tools/prompt_reload.py` (widen `PromptReloadInput` at `:225` — a LOCAL class, cheap), `daemon/config.py` (2 new `CompactionConfig` fields), **NEW `daemon/_compact_note_prefilter.py` — the v1 deterministic matcher, IN SCOPE THIS RELEASE (default-OFF)**, NEW lane-owned test files + NEW pack scripts (§I). `daemon/services/prompt_refresh.py` and `daemon/constants.py` are widened-but-unused in this design; the implementer MAY host the 900s rate-limit constant in `daemon/constants.py` if it reads cleaner there — either host is inside the surface. **Coupling: 1 dataclass field + 2–3 call sites.**

**Flip condition (both council models):** if the note must later alter *selection semantics* (not just the GLOBAL layer + optional drop set), A and C converge — extract the note-guidance fully into the new module.

---

## B. jev routing — the measured answer

- **Public API** (`daemon/services/decision/client.py`): `DecisionClient(config, ...)` at `:124`; `async consult(request, *, floor=None, timeout_s=None) -> ClientOutcome` at `:160`. **Never raises; never returns an error envelope.** `ClientOutcome` at `:128`. Answer types `{predicate, choice, score, refusal}` (`:861`); 1 MiB response cap (`:65`); single `httpx` POST to `{base}/decisions`, **no retry**.
- **Model routing:** purpose-id **`"decision"`** — `DEFAULT_DECISION_MODEL = "decision"` (`daemon/services/decision/config.py:130`), stamped via `request.with_model(self._config.model)` in `registry.execute`. **`COMPACTION_MODEL` is never consulted by this family** (grep-verified). The compaction summarizer's model resolution is a SEPARATE purpose-bound knob: `resolve_compaction_model` (`daemon/compaction.py:1185`), precedence `config.model` → legacy `summarization_model` → `""`; `COMPACTION_MODEL` env beats the yaml key (`config.yaml:~465-470`), and it is *never consulted against `llm.allowed_models`* — the exact purpose-bound precedent the stage would copy **if** it ever grew an LLM occupant.
- **Cost metering:** `app_settings` table, key `decision_cost_ledger_{YYYY-MM}` (JSON: period/usd/tokens/per-seam), one process-level lock. Ceilings: **USD 50.0/mo global**, **10M tokens/mo**, per-seam share 25.0, per-attempt floor $0.0005. On breach: `trip_shadow_off` writes per-seam enabled=0+stage=shadow AND global off, then board item + daemon-log WARNING (measured `overlay.py:959-977` region).
- **What `"JEV call counts towards …"` actually bounds** (`daemon/config.py:4320`): it sits in `MemoirReflectionConfig.max_llm_calls_per_pass` (default 4) and bounds the **memoir reflection pass** — the memoir persona-gate classifier is the only thing NAMED "JEV" in-tree. **Two distinct "JEV"s, zero coupling.** The decision family's bounds are the ceilings above.
- **Kill switch:** three surfaces, ladder DB > env > yaml > default, **fail-closed**: global `decision_global_enabled` (env `DECISION_ENABLED`, default **OFF**), per-seam enabled (default **OFF**), per-seam stage (default **shadow**). Write path is **operator-only**: `SettingsService.set_override` over `MANAGED_KEYS` (`daemon/services/settings_service.py:635-675`); the `settings` category is granted to ari+leader only — no worker can flip it.
- **Shadow-only enforcement is STRUCTURAL, not conventional** (three independent walls): (1) `DENIED_SEAM_ROLES` (`registry.py:82-90`) names `compaction_message_selection` (**EX-2**, `:85`) and registration is *refused before any call path exists* (`:275-280`); (2) the `Monotonicity` enum (`types.py:92-97`) admits only `TOWARD_COMPLETION / TOWARD_TIGHTENING / WITHIN_SET / ADVISORY_ONLY / NONE` — **no direction admits removal**; add-only demands superset (`registry.py:616-623`), `WITHIN_SET` may never empty (`:629-630`); (3) `transition_stage` (`registry.py:355-387`) refuses shadow→deciding without a ratified `ReviewRecord` — the docstring names *"the Emperor's ratification"* — and `is_deciding_permitted` (registry.py:389-399) makes writing `deciding` into the store alone inert. **No production caller of `transition_stage` exists.** Only one seam is registered at all: S1 `snapshot.l3.selection` (shadow, detached).

---

## C. Governance tension — RULING: the pre-filter is **OUTSIDE** the P1 doctrine; **not** a seam; a purpose-bound non-family stage; **default OFF**

The Emperor's directive authorizes jev filtering; the ratified doctrine says the overlay never decides. The measured truth dissolves the tension: **the decision family structurally cannot express "drop this message"** (§0/§B walls). Therefore:
- The pre-filter is a **distinct, purpose-bound mechanism inside the compaction pipeline** — never registered on the decision registry, never called through `DecisionOverlayPort`, never billed to the decision ledger. **The v1 occupant it ships with is DETERMINISTIC and makes NO model call, so it consumes no budget and needs no model resolution** — the decision-family *shape* (own budget, own kill lever, single attempt, identity on every failure) is the PATTERN the stage is designed around, and it is what a FUTURE LLM occupant would inherit; **no LLM consumer ships in this release** (§F, SCOPE DISCIPLINE 6).
- **Kill lever:** one `CompactionConfig` bool (the `proactive_enabled` precedent at `daemon/config.py:1686` — aliased `AliasChoices` field, `extra="forbid"`-safe per the `dedup_window_s` idiom at `:1738-1753`), **code-default OFF, env-overridable, and deliberately NOT set in `config.yaml`** — a yaml value would silently defeat an operator's env kill-switch (pydantic-settings init-kwargs priority).
- **P1 SHADOW-ONLY doctrine stays ratified, untouched.** EX-2 stays. The enum stays. The D3 gate stays. The open question is **recorded in §11, not decided here**.

**Byte-identity guarantee (structural):** the filter's only input is the compactable partition output of `select_compactable_groups` (`:1582`); the verbatim tail, hoisted injections, sentinel, and ceiling are outside its reach. Every failure mode — off, timeout, transport, over-budget, unparseable, out-of-set references, empty-output-on-non-empty-input — converges to **identity pass-through: "skipped" and "off" are the SAME code path**, not two paths that happen to agree.

---

## D. Note placement + composition — RULING

**Carrier (arming store): `instances.metadata` JSONB** via `InstanceRepository.set_metadata` / `delete_metadata` (`daemon/repositories/instance/repository.py:2990+` — "dialect-aware single-statement UPDATE so concurrent calls targeting different keys compose correctly"). Keys: `compact_note_arm` (the verbatim note), `compact_note_armed_at`, `compact_note_sha256_12`, `compact_note_last_applied_at` + `compact_note_last_sha256_12` (consumption receipt + the rate-limit clock). This is the exact precedent of `daemon/services/_escalation_metadata.py:73-84` (four compaction-scoped keys). **Not** `SessionState` (the arming write originates in a tool mid-turn; T3 consumes pre-dispatch outside the graph node), **not** an in-memory registry (`prompt_reload`'s pop is restart-unsafe — W1's mirror-warning).

**Migration verdict: NO.** The record is the checkpoint `messages` channel (§0.4); the doc body is a plain string (`SystemMessage(content=body)`, `daemon/compaction.py:1053-1056`) carried as opaque JSONB/BYTEA. A note riding the doc body is string concatenation inside `build_compaction_doc`. The arming carrier is existing JSONB. **Zero DDL, zero schema evolution, in either place.**

**Render slot: `daemon/compaction.py:993-1032` — the note is the FIRST arm of the GLOBAL OVERVIEW region's branch.** The `── GLOBAL OVERVIEW ──` header opens at `:993`; the branch structure is:
- **note-first gate (new, first):** `if note: body += note + "\n"` — **the note REPLACES the three-way chain when a note is consumed.** Same region, verbatim, **NOT subject to `GLOBAL_OVERVIEW_TOKEN_CAP` (600)** render-time truncation (`:1012-1021`); the **only** cap enforcement is the arm-time typed refusal (§G).
- `if global_overview:` (`:998`) → cap check + merged text — **auto path only; unreachable when a note is consumed** (the merge is skipped ⇒ `global_overview` is `None`).
- `elif mode == "truncation":` (`:1024-1029`) → nothing — auto path only. **The `_truncate_fallback` outcome (`:5035`, `mode="truncation"`, `global_overview=global_text or ""`) IS doc-producing and DOES render the note** (part (i) of the C-1 fix includes it).
- `else:` (`:1030-1032`) → the placeholder — **auto path only; SUPPRESSED when a note is consumed** (co-emitting `"(overview unavailable — merge pass failed…)"` under an authoritative note would be double text and a lie — "unavailable" when the note IS the overview).
- `if n_sections > 0:` (`:1034`) → SECTION DETAIL — unchanged on both paths.

**One consequential predicate rides with the gate:** the `if previous_overview:` line at `:996-997` ("Previous overview: …") is **suppressed when a note is consumed**. Reason: after a note-guided compaction the prior doc's GLOBAL *is* the note, so `_extract_previous_overview` (`:222`) would otherwise re-emit the old note verbatim (up to 12k chars, uncapped — `:996-997` sits before the cap check) directly above the new note — unbounded cross-pass duplication. The note REPLACES the overview lineage for that conversation; it is authoritative and self-contained. (The no-note path keeps `previous_overview` seeding byte-identically.)

**Budget policy:** the note is **counted** in the `_apply_ceiling_rule` numerator when present (never-condensed class, alongside GLOBAL) and **never render-truncated**. Implementation: `note_tokens = estimate(note) if note else 0` added to the numerator at `:788` — with no note the computation is bit-identical (the estimate is skipped entirely, not estimated-as-zero). At the 12,000-char cap (~3k tokens) the note is ~0.43% of a 700k window and ~2.9% of the 15%-ceiling allowance (105k tokens); on small windows the ceiling condenses sections — never the note — which is the intended authority ordering. Disclosed: on a 32k window a max-cap note could squeeze sections heavily.

**Composition with today's layers:**

| Layer | Ruling |
|---|---|
| (i) `global_overview` | **The merge pass is NOT RUN when a note is consumed** — via the **single entry-point guard in `_merge_summaries` (§D.1)**, which covers all three call sites by construction. The note occupies the GLOBAL region's authority. **Merge-success case: moot — not suppressed, not demoted, not preceded; simply not generated.** The merge-overview and the note never coexist in one doc. On the auto path (no note) the merge runs byte-identically. |
| (ii) Section summaries | **Unchanged — the summarizer still runs, unseeded** (`_summarize_chunked` at `:3624` is ON the note-guided path by design — it produces the sections). The note replaces the *merged overview*, not the per-batch sections. Seeding the summarizer prompt with the note is a documented future increment (flip: if sections visibly contradict the note's account). |
| (iii) Verbatim tail | Untouched — `select_compactable_groups` window logic (`:1625-1642`), appends at `:3727-3731` / `:3790-3794` / `:5051-5055`. The pre-filter may draw only from compactable groups, never the tail. |
| (iv) Sentinel / emergency | The sentinel is untouched (it is applied in the persist seam, `:224`, and never bypasses the doc). **`emergency_truncate` produces no doc — the note does not render there; the arm PERSISTS with an honest "still armed" row (§D.2(i)).** (The alternative — riding the hoist bucket — was rejected because it renders *above* the doc, violating the placement clause.) |

### Consumption — AT-MOST-ONCE per arm (two stores; the persist-then-delete crash window — Risk #3's carve-out)

**TWO STORES are involved: the ARM row (`instances.metadata`) and the COMPACTION RECORD (the checkpoint `messages` channel).** The arm is consumed by the **first quiescent (Variant A, `mid_turn=False`) DOC-PRODUCING compaction** (§D.2(i)); the arm row is deleted only AFTER the compaction record has been persisted. Hence the semantics are **at-most-once per arm in steady state, with an at-least-once re-application inside the crash window**: a crash between the persist and the delete re-applies the note at the next quiescent seam (bounded, disclosed, and harmless — the note is re-rendered as the GLOBAL layer of a later compaction, whose own tail invariant still holds for that later compaction). **Mid-turn sites (T1/T2) NEVER read the arm** — they stay byte-identical by construction, honoring the commission's "NEVER mid-turn" for the tool-initiated path. A doc-less outcome does not consume the arm (§D.2(i)). **Staleness: RETIRED — see §D.3 (A-5).**

### D.1 — D25 RULING: the three merge sites, and the skip's single home

**Verification (re-measured):** three live `_merge_summaries(` call sites — `:3687-3691` (outer re-merge, `if outcome.merge_failed:` `:3657`), `:3768-3772` (partial path `:3649`), **`:4187-4191` (inner merge inside `_summarize_chunked` def `:3922`, sole caller `:3624` — fires FIRST on full success, ≥2 summaries, collapses `summaries=[merged]` so the outer sites never execute)**. No structural unreachability: `_summarize_chunked` IS the section summarizer note-guided mode keeps.

**RULING — the skip predicate's single home is the FIRST STATEMENT of `_merge_summaries` itself (def `daemon/compaction.py:4306`):**

```python
async def _merge_summaries(...) -> tuple[str, bool]:
    if context.compact_note:
        logger.info("merge skipped: note-guided mode (iid=%s)", iid8)  # content-free
        return ("", False)
    ...
```

**A-3 (v1.3): the guard returns `("", False)` — NOT `(None, False)`.** The annotation is `-> tuple[str, bool]` (`:4312`) and the documented failure ladder is *"merge fail/timeout → `("", False)`"* (`:4335-4336`); a `None` first element violates both. **Site-3 re-verification under `("", False)` (measured flow):** `:4187` `merged, ok = await self._merge_summaries(...)` → `ok=False` → `:4192 if not ok:` → logs + returns `ChunkedOutcome(summaries=partial_summaries, merge_failed=True)` → outer `:3657 if outcome.merge_failed:` → `:3687` re-merge hits **the same guard** (a second no-op, zero LLM) → `ok=False` → `:3692 if ok:` else-branch → `global_overview = None` (`:3697`) → **the note-first render gate emits the note; the placeholder never co-renders.** The `""` first element is never read on any `ok=False` path (every caller checks `ok` first). **`(None, True)` remains FORBIDDEN** (at `:4187` it would fall through to the merge-success branch and produce `summaries=[None]`, breaking downstream). **`(None, False)` is RETIRED outright** — the annotation admits no `None`; there is no remaining legitimate use. Observability: the guard's own content-free INFO line ("merge skipped: note-guided mode") + the envelope's additive `note_guided=true` disambiguate the `total_summary_status="failed"` the skip routes through — the FE-read `"ok"|"failed"|None` enum is NOT overloaded.

**Why the entry point, not the sites:** site-enumeration gates are exactly the drift class D25 caught. A guard at the single callee covers sites 1, 2, 3 **and every future call site by construction**.

**D26:** planner right — the second call begins `:3768` (span `:3768-3772`; `:3773` is `if ok:`); site 1 corrected `:3686-3690` → `:3687-3691`. Off-by-one drifts from W1, corrected throughout.

### D.2 — C-1 RULING (v1.3): the below-floor tail-truncation hole — four-part fix ADOPTED

The review council's cross-exam found a real hole in v1.2's consumption story (all four parts verified, §0.9): **(a)** the below-floor fall-through is `force OR over_budget` (`:3369` → `:3385-3389`), so armed T3 with `force=False` still 50%-tail-truncates when below-floor ∧ over-budget; **(b)** T4 consumes the arm while calling `force=True` (`compact_executor.py:1220`) — below-floor `/compact` + armed note = the forbidden tail truncation; **(c)** `_last_effort_tail_truncation` (`:2518`, type `tail_truncation_last_effort` `:1880`/`:2563`) returns a non-empty replacement with NO doc — the note can never render there; **(d)** v1.2's predicate (`replacement_messages` non-empty ∧ persist `True`) is satisfied on that outcome → arm consumed, note silently lost, **false "applied" provenance that falsely arms the 900s rate limit**.

**The four-part fix — ADOPTED in full:**

**(i) Consumption predicate (ES-7/ES-5) — doc-producing types only.** Consume the arm ONLY when `result.compaction_type` ∈ **doc-producing** — EXCLUDING `{emergency_truncation, tail_truncation_last_effort}` (`:1880`, `:2563`) and INCLUDING `_truncate_fallback`'s outcome (`:5035` — doc with `mode="truncation"`, note renders via the note-first gate). On a doc-less outcome the arm PERSISTS with an honest "still armed" row (the arm-status surface already discloses it on every subsequent receipt). This kills part (d) outright: no false provenance, no false rate-limit arming.

**(ii) T4 rule.** Arm present ⇒ the executor calls the engine with **`force=False`** (`compact_executor.py:1220` — one predicate). The operator's `/compact` on an armed instance becomes a *note-guided* compaction — which is the correct semantics: the note was authored for "my next compaction," and `/compact` is a deliberate quiescent compaction. Unarmed `/compact` keeps `force=True` byte-identically.

**(iii) Docstring/comment corrections** at `compact_executor.py:12-16` and `:1199-1201` — the current *"Under force, the engine still honors min-messages (WS-2.1)"* wording is exactly the misleading claim this hole exploited; corrected to state the measured truth: the clean skip requires `not force AND not over_budget`; force OR over-budget falls through to the 50%-tail last-effort floor.

**(iv) ⚠ ACKNOWLEDGED — LEADER-RATIFIED IN-LANE SPEC AMENDMENT.** I acknowledge and adopt: **an armed-aware floor predicate at the engine's below-floor branch** (`daemon/compaction.py:3357-3389`), **note-gated — when a note is armed and the context is below floor, the engine takes the CLEAN SKIP regardless of force/over-budget** (a distinct skip reason, e.g. `skipped_below_min_messages_armed`); **skipped entirely when unarmed**, so the no-note path is byte-identical. *Acknowledgment text, explicit:* the leader has RATIFIED this as an in-lane spec amendment on three grounds I confirm against my own measurements — (1) `daemon/compaction.py` is inside the lane's declared edit surface; (2) the predicate is note-gated, preserving unarmed byte-identity; (3) it is **REQUIRED, not optional**: parts (i)–(iii) alone leave a **repeat-truncation spiral** — in the below-floor ∧ over-budget corner, (i) keeps the arm, (ii) keeps `force=False`, but `over_budget` alone still triggers the `:3385` fall-through, so every quiescent seam amputates 50% of the tail while the note never lands and never renders; only (iv) closes the loop. **Safety of the clean skip in that corner:** T1/T2 (precall-95, CLE) remain the arm-blind distress valves — genuine pressure is still handled exactly as today — and the skip is bounded in time by conversation growth (the next messages cross the floor and the note fires). The planner extends AC7.4 with the **positive-shape pin** (below-floor ∧ over-budget ∧ armed ⇒ clean skip, arm persists, still-armed receipt); **the flip-to-`force=True` mutation does NOT catch this hole — the pin must assert the positive shape.**

### D.3 — A-5 RULING (v1.3): staleness mechanism — RETIRED

ES-7 crit 4 and Risk #4 assert a staleness mechanism that was never ruled. **Ruling: RETIRED — there is NO staleness mechanism in v1.** An armed note persists until consumed or replaced; no TTL, no expiry, no staleness state. Reasons: (1) any silent expiry would be exactly the "disclosed swallow is still a swallow" the resident forbade — a deliberate act deleted by the system without a receipt; (2) there is **exactly one arm per instance** — a second note REPLACES the first with disclosure (§G), so there is no accumulation to bound; (3) the agent always knows an arm is parked (the still-armed status line + any deferred state ride every subsequent `prompt_reload` receipt); (4) the rate limit bounds *arming frequency*, which is the actual abuse surface. If the Emperor later wants bounded staleness, it MUST be an honest typed refusal/notice at arm-read time — never a silent expiry. The plan's ES-7 crit 4 / Risk #4 rows are amended to point here (unruled mechanisms in a plan are future bug reports — this one is now ruled: retired).

---

## E. The seam-safety invariant — RULING

**Reading adopted: Reading 2 is the operative rule; Reading 1 supplies only the semantic justification, expressed through the marker scheme.** R1 alone ("the note said so ⇒ safe to drop") bets conversation integrity on two fallible layers compounding — rejected. R2 alone ("load-bearing ⇒ never drop") forbids the authorized capability. They collapse into one mechanism: "settled" means *the note adjudicated the item as concluded AND declared it in the structured marker block* — only a mechanically-matched marker authorizes a drop.

**THE INVARIANT (one sentence):**
> A pre-filter may drop a candidate group only when the note's structured marker block mechanically accounts for that group (exact message-id or group-index-span match, decidable without judgment at filter time); every other group falls through to today's path unchanged, and an absent or unparseable marker block yields an empty drop-set — free-text notes make the filter vacuous by construction.

The commission's conservative prior is **RIGHT** and is adopted verbatim-in-substance (it is also Ari's recorded ruling, which the council reproduced independently). Two hard walls ride with it: (1) the **never-empty wall** — a drop-set that would empty a non-empty candidate set is discarded → identity (local import of `registry.py:629-630`'s rule); (2) drops operate on **whole `MessageGroup`s only** (atomic tool sequences never split; the span math downstream is group-position-derived, so whole-group drops are structurally safe — W3's measurement). Dropped groups are itemized in the envelope's existing dropped-spans clause (`:977-978`) — never silently vanished.

**Does the pre-filter change the default (no-note) path? NO.** Gate = `enabled AND note_present`; no note ⇒ short-circuit before any list copy, LLM call, or arm-state read. Proven by golden-envelope byte-parity (flag-unset vs flag-on-no-note ⇒ identical doc bytes).

**Marker block format (v1):** an optional final fenced block of the note:
```
<<<COMPACT-ACCOUNT
accounted_for: msg:<message_id>, group:<start_idx>-<end_idx>, ...
>>>
```
Machine-parsed; the matcher intersects the declared set with the actual candidate groups (ids/spans re-derived at filter time); anything unparseable ⇒ drop-set ∅.

---

## F. Pre-filter failure surface

**> IF THE EMPEROR RULES "NO DECIDING SURFACE EVER": DELETE THIS STAGE.** It is ~30 lines + tests, default-OFF, lazily imported, and nothing else in the design depends on it (Decisions Pending §11.2). This line sits at the top of the section by explicit leader request.

**BUILD SCOPE — CONFIRMED: the v1 occupant — the DETERMINISTIC, ZERO-LLM marker matcher — IS in this release's build scope, as the NEW file `daemon/_compact_note_prefilter.py` (default-OFF; lazily imported).** **NO LLM CONSUMER SHIPS IN THIS RELEASE** — the v1 occupant is pure set arithmetic over group spans (no model call, no budget, no separate model resolution); **only a FUTURE LLM occupant is excluded** (SCOPE DISCIPLINE 6). The reviewer's A-6 reading is correct and the planner is folding to it.

**ONE pass, no retry; every failure ⇒ skip-the-filter, never fail-the-compaction.** Taxonomy: disabled / timeout / transport / over-budget / unparseable marker block / out-of-set references / empty-output-on-non-empty-input — **all converge to identity**, and identity is the *same code path* as "off" (§C). WARN once per pass. The v1 occupant is **deterministic** (set arithmetic, zero LLM), so its wall-clock is effectively zero; the **stage contract a FUTURE LLM occupant (NOT built this release) would inherit** is `min(5s, 10% of remaining operation budget)` — strictly subordinate to the operation budget, matched to the decision-client's 5s single-attempt discipline. **The fallback is byte-identical to no-filter by construction.**

---

## G. Cap + refusal vocabulary

**Cap: 12,000 chars — AUTHORITATIVE (leader ruling: the commission says the architect sets it; the planner's 8,000 proposal is superseded).** `CompactionConfig.compact_note_max_chars`, aliased field, default 12000. Reason: the Emperor's stated instinct is "~8–16k"; 12k is its midpoint, comfortably inside budget on any window (§D), generous enough that a rich settled/open/dead account never hits the refusal, tight enough to keep the doc honest. **Co-pinned in tests against `GLOBAL_OVERVIEW_TOKEN_CAP`** (drift guard). Flip condition: if profiling shows doc-assembly or FE cost at 12k, drop to 8k (the Emperor's floor).

**Refusal vocabulary** — house envelope `Error: prompt_reload REFUSED — reason={code}: {message}` (`daemon/tools/prompt_reload.py:94-96`; codes at `daemon/services/prompt_refresh.py:147-174`). Five NEW codes (the existing 9 REFUSED + 1 FAILED stand; the "codes 1-9" comment and the shared `prompt_reload_refusals_unit` census pin are **untouched** — the new codes' legs moved to the lane's own pack, §I):

| Code | Trigger | Behavior |
|---|---|---|
| `prompt_reload_note_required` | `compact=true` with note absent **or** whitespace-only (one code — forking them buys nothing) | REFUSED; nothing armed |
| `prompt_reload_note_over_cap` | note > 12,000 chars | REFUSED; **echoes chars + cap; never truncates; nothing armed** |
| `prompt_reload_note_requires_compact` | note supplied with `compact=false` | REFUSED — **ignoring is dishonest** |
| `prompt_reload_compact_disabled` | `compact=true` while `CompactionConfig.enabled=false` | REFUSED — arming a note that can never land would strand it |
| `prompt_reload_compact_rate_limited` *(v1.1)* | `compact=true` within `DELIBERATE_COMPACT_RATE_LIMIT_S` of the last **deliberate** (note-guided) compaction | REFUSED at **arm time** — echoes remaining seconds; **an honest typed refusal, never a silent deferral** |

**Deferred states — receipt vocabulary, never success receipts.** Per the resident's non-negotiable: *if any window ever binds, the receipt MUST say "deferred, not compacted" — never a success receipt for work not done.* Honest states riding the arm-status surface:
- `armed — deferred (below minimum-compaction floor), not compacted` — the **armed-aware floor predicate** (§D.2(iv)): clean skip, arm persists, **no tail truncation in ANY corner** (v1.3 closes the over-budget corner).
- `armed — deferred (anti-refire window), not compacted` — the BINDING 60s dedup (`:3126`), worst case a single seam-cycle, only when an automatic compaction fired moments before the armed seam. *(A-4: this clause is part of the §H receipt vocabulary verbatim, not merely implied.)*
- `armed — still armed (last compaction produced no doc), not compacted` — a doc-less outcome (`emergency_truncation` / `tail_truncation_last_effort`) did not consume the arm (§D.2(i)).

**Second note while one is armed: REPLACE, with disclosure — LEADER-CONFIRMED (ruling A: the planner's REFUSE proposal is OVERRULED in this design's favour).** The receipt carries `superseded_previous=true` + the prior note's `sha256[:12]`. **The trap is the rationale, recorded as ruled:** a refuse would TRAP the agent — arm → wait → cannot correct or update its own distillation, which is precisely the thing the feature says the agent owns; a refuse without any un-arm path strands a mistyped note permanently. `compact=false` with no note = today's behavior, byte-identical.

**Every refusal is honest: the tool only ARMS.** No code path can emit a success-for-compaction receipt — the tool body writes the arm and returns; it structurally cannot observe a compaction.

---

## H. The honest tool contract — CONSUMPTION RULING **REVISED (v1.1; tightened v1.3; gate literal made EXHAUSTIVE v1.4)**

### The revision (v1.1)

**v1.0's "disclosed gap — arming does not lower the 80% gate; a note can wait indefinitely" is WITHDRAWN.** It was an internal inconsistency, not a defended position. The leader's challenge resolves the contradiction, and the resident's contract decides it: **"Compacted now is the contract when I ask"** (Ari, 2026-11-16). The commission's letter agrees — *"compacted at the NEXT SAFE SEAM"*, not "at the next threshold crossing."

### The axis argument (leader-adopted, resident-originated — the decisive reasoning)

**The 80% gate measures SIZE. The compact note measures RELEVANCE. These are different axes, and the v1.0 ruling conflated them.** The Emperor's founding use case is explicitly relevance-driven, in his own words: *"for this conversation you don't need know about sonic team any more and many thing already old."* That statement is **true at 30% fill exactly as it is true at 90%**. Waiting for SIZE pressure to fire a RELEVANCE distillation means the wake-up never comes when it is actually wanted. Threshold-gating a deliberate relevance request on a size predicate is a **category error**, not a trade-off. **The v1.0 position was optimizing token-fill; the feature's purpose is ATTENTION QUALITY.** A note-armed request is DELIBERATE INTENT — it fires at the seam regardless of fill. The automatic 80% gate stays byte-identical for the automatic path.

### Disposition under the defense constraint

*"Wasted compaction" is disallowed — the note's author is the one spending it. If your defense rests on cost, it must be a cost the AUTHOR does not intend to pay."* **No such defense exists under the revised mechanics, and none is offered.** The one concrete author-unintended cost — the 50%-tail amputation — is closed in **every** corner by the C-1 fix (§D.2): the armed-aware floor predicate (iv) plus the T4 `force=False` rule (ii). With those: the compaction performed is exactly what the author asked for, its frequency is bounded by the author's own rate limit, and its content is the author's own distillation. **Nothing remains that the author does not intend to pay.**

### THE EXHAUSTIVE GATE INVENTORY (v1.4) — exactly THREE gates, no others

**REVISED RULING: an armed note FORCES a note-guided compaction at the next quiescent seam (T3/T4), subject to EXACTLY THREE GATES — this list is EXHAUSTIVE; no other gate, window, threshold, or deferral can stand between an armed note and its compaction:**

| # | Gate | Where | Effect on the deliberate compaction | Agent-facing wording |
|---|---|---|---|---|
| **(i)** | **Minimum-compaction floor** (armed-aware) | `daemon/compaction.py:3357-3389` (§D.2(iv)) | below floor ⇒ **clean skip**, arm persists, **no tail truncation in any corner** | `armed — deferred (below minimum-compaction floor), not compacted` |
| **(ii)** | **The pre-existing 60s AUTOMATIC anti-refire dedup window** | `daemon/compaction.py:3126` (the `last_compacted_at` stamp; the AUTOMATIC class's anti-thrash window, which the deliberate class does NOT get to bypass because the armed path runs `force=False`) | worst case **one seam-cycle** of deferral, only when an automatic compaction fired moments before the armed seam; arm persists | `armed — deferred (anti-refire window), not compacted` |
| **(iii)** | **The deliberate-compact rate limit** (`DELIBERATE_COMPACT_RATE_LIMIT_S = 900`, hardcoded constant) | enforced at **ARM time** in the tool | **not a seam deferral** — an arm-time **typed refusal** (`prompt_reload_compact_rate_limited`, echoing remaining seconds); nothing is armed | `Error: prompt_reload REFUSED — reason=prompt_reload_compact_rate_limited: …` |

The no-arm path remains byte-identical by construction — the T3 predicate short-circuits false when no arm exists.

**The mechanics — forcing lives in the GATE, never in the engine's force flag:**
1. **T3 gate** (`instance_messaging.py:1337`): `if armed: bypass the threshold test` (enter the compaction path) and call the engine **with `force=False`**.
2. **T4 executor** (`compact_executor.py:1220`): arm present ⇒ **`force=False`** (§D.2(ii)); unarmed `/compact` keeps `force=True` byte-identically. Docstrings corrected (§D.2(iii)).
3. **Engine floor** (`compaction.py:3357-3389`): the **armed-aware predicate** — armed ∧ below floor ⇒ **clean skip (`skipped_below_min_messages_armed`) regardless of force/over-budget**; arm persists. Unarmed ⇒ the existing branch byte-identically. **This closes the below-floor ∧ over-budget corner where v1.2's safety claim was false (§0.7/§0.9a).**
4. **Anti-refire window** (`:3126`): with `force=False`, the 60s AUTOMATIC dedup window CAN bind — worst case one seam-cycle of deferral. Bounded, disclosed (§G/A-4). **Never a success receipt for work not done.**
5. **Rate limit** (gate (iii)): **`DELIBERATE_COMPACT_RATE_LIMIT_S = 900` (15 minutes), a hardcoded module constant — no new config knob.** Counting deliberate (note-guided) compactions only — the clock is `compact_note_last_applied_at`, written at consumption. **Rationale:** (a) ≤4 deliberate compactions/hour makes a tight arming loop structurally pointless; (b) a genuine agent never sees it; (c) 15× the automatic 60s dedup keeps the resident's two trigger classes numerically distinct by an order of magnitude; (d) above any agent turn cadence.

### The receipt

**What the tool returns (arm receipt):** `outcome=armed_pending_seam`, `note_chars`, `cap`, `note_sha256[:12]`, `superseded_previous?`, kill-switch state, expected seam ("your next turn boundary — the quiescent pre-dispatch gate; it fires there unless one of exactly three gates applies: the minimum-compaction floor, the 60s anti-refire window, or the deliberate-compact rate limit — this list is exhaustive"), and the explicit line: **"CONTEXT NOT YET COMPACTED — the compaction happens at your next turn boundary; until then this note remains armed."** **A still-armed status line rides every subsequent `prompt_reload` receipt, carrying any deferred state verbatim — including `armed — deferred (anti-refire window), not compacted` (A-4: the binding 60s window is part of the receipt vocabulary, not merely implied), `armed — deferred (below minimum-compaction floor), not compacted`, and `armed — still armed (last compaction produced no doc), not compacted`.**

**How the agent learns it landed (three independent signals):**
1. **The context itself** — the doc's **envelope header** carries `note_sha8=<prefix> note_chars=<n>` + `note_guided=true` (additive; FE reads defensively). Provenance rides the *envelope*, not the GLOBAL slot, so the slot stays byte-equal to the note and the **verbatim contract holds exactly**. The agent recomputes the hash of what it authored and verifies its own next-turn context.
2. **Arm-key absence** — `compact_note_arm` deleted on consumption (**only on doc-producing outcomes**, §D.2(i) — no false "applied" provenance, no false rate-limit arming); `compact_note_last_applied_at` + `compact_note_last_sha256_12` written post-hoc.
3. **The frozen INFO line extended with arm fields — content-free (chars + sha256 prefix ONLY, never the note body, in any agent-readable log).** The note is the agent's working memory and can carry intimate personal content; it rides verbatim in the RECORD and the CONTEXT and nowhere else. **This cites, BY NAME, the memoir-lane content-free doctrine and the decision-family shadow-lane content-free doctrine (logs carry ids/hashes/scores only, v1).**

**Mid-turn sites (T1/T2) never read the arm** — the distress paths stay byte-identical *by construction*, and they remain the pressure valves in the below-floor corner (§D.2(iv) safety argument). The council's any-site rule remains overruled (flip: one predicate — the arm read at the two mid-turn call sites — if the Emperor prefers consumption-at-any-site).

---

## I. Testability — the minimal non-vacuous surface

**ROUTING (leader ruling — THE PLAN'S ROUTING GOVERNS): every leg of this lane is registered in the lane's OWN three named packs. NO lane leg maps into a pre-existing shared pack.** Rationale (measured): `compaction_unit_test` carries **THREE conflicting PACKS.md rows** — `:3577` (322/322, incl. `test_compaction.py` 126), `:4504` (narrow, 130/130), `:4572` (22-file, 781/0/0) — a pre-existing, tester-lane-owned inconsistency; a lane leg depending on it would **inherit that gate-verifiability defect**. The same routing minimises merge-contention with the concurrent group-chat lane adding rows to PACKS.md. **Substance is unchanged from v1.4 — only the routing moves.**

### The lane's three packs (all NEW; all lane legs live here)

| Pack (NEW) | NEW pack script | NEW lane-owned test file(s) | Legs |
|---|---|---|---|
| `prompt_reload_compact_note_unit` | `test/packs/prompt_reload_compact_note_unit.sh` | `tests/unit/tools/test_prompt_reload_compact_note.py` (tool-level) · `tests/unit/test_compaction_compact_note.py` (engine-level) | see below |
| `prompt_reload_compact_note_e2e` | `test/packs/prompt_reload_compact_note_e2e.sh` | `tests/integration/test_prompt_reload_compact_note_e2e.py` | see below |
| `compaction_note_prefilter_unit` | `test/packs/compaction_note_prefilter_unit.sh` | `tests/unit/tools/test_compaction_note_prefilter.py` | see below |

**`prompt_reload_compact_note_unit` — tool-level legs:** param validation (`compact`/`compact_note` on `PromptReloadInput`); the five new refusal codes with their exact envelopes (`note_required` for absent AND whitespace-only; `note_over_cap` echoing chars+cap with NO truncation and nothing armed; `note_requires_compact`; `compact_disabled`; `compact_rate_limited` arm-time with remaining seconds); the cap value 12,000 co-pinned against `GLOBAL_OVERVIEW_TOKEN_CAP`; the **ARMED-RECEIPT shape** pins (`outcome=armed_pending_seam`, `note_chars`, cap, `note_sha256[:12]`, `superseded_previous`, the expected-seam clause); the **never-"compacted" NEGATIVE GREP**; the **EXHAUSTIVENESS PIN — the receipt's expected-seam clause must name EXACTLY THREE GATES (a reader must not be able to believe the gate list is shorter)**; **all THREE deferred-state wordings verbatim** (floor / anti-refire / no-doc); the supersede-disclosure pin (replace-with-disclosure + the trap rationale).

**`prompt_reload_compact_note_unit` — engine-level legs:** note rendered **VERBATIM** (golden bytes); the D25 merge-skip on **ALL THREE** call sites incl. the chunked path (`:4187`) ⇒ no merge LLM, per-batch sections survive, note in the GLOBAL region, **NO placeholder co-emission**; the render-time-cap exemption; the ceiling inclusion; the previous-overview-line suppression; armed-forces-compaction-below-threshold (gate bypass, `force=False`); the **AC7.4 POSITIVE-SHAPE pin** (below-floor ∧ over-budget ∧ armed ⇒ clean skip `skipped_below_min_messages_armed`, arm persists, still-armed receipt, **NO tail truncation** — the flip-to-`force=True` mutation does NOT catch this hole, so the pin must assert the positive shape); the T4 pin (arm present ⇒ executor passes `force=False`); the consumption-predicate pin (doc-less outcomes do NOT consume the arm and do NOT write `compact_note_last_applied_at`; `_truncate_fallback`'s outcome DOES); the anti-refire deferral pin (armed ∧ a just-fired automatic compaction ⇒ the exact `armed — deferred (anti-refire window), not compacted` wording and **NO success receipt**); emergency ⇒ still-armed; stamp-only ⇒ arm-not-consumed; the envelope `note_sha8`/`note_guided` pins.

**`prompt_reload_compact_note_e2e`:** the acceptance bar — real conversation + real note + `_merge_summaries` **monkeypatched to fail** ⇒ assert the **NEXT TURN's actual context** carries the note verbatim in the GLOBAL band ("stored" is not the bar); modeled on the `test_injected_notes_hoisting_acceptance.py` precedent (real StateGraph + AsyncSqliteSaver + real persist seam + fresh-connection reload). **The MERGE-FAILED branch is THE named case** — *"Surviving where the overview died is the feature's claim to exist."* Plus the **byte-identity leg** (flag-unset vs flag-on-no-note ⇒ identical doc bytes), held in the lane's own file so the lane depends on no shared file's pack registration.

**`compaction_note_prefilter_unit`:** the deterministic matcher's truth table (marker-match drops; free-text ⇒ ∅; every failure ⇒ ∅; input-list immutability; never-empty wall; whole-group atomicity) + the default-OFF gate + the vacuous-without-markers pin.

### Legs MOVED out of the pre-existing shared packs — the routing delta (explicit)

| Moved FROM (shared) | Leg | Moved TO |
|---|---|---|
| `prompt_reload_refusals_unit` (PACKS.md:483, 15 nodes) | the five new refusal codes; over-cap no-truncate; note-without-compact ⇒ refused | `prompt_reload_compact_note_unit` |
| `prompt_reload_tool_unit` (PACKS.md:484, 20 nodes) | armed-receipt shape; never-"compacted" negative grep; the EXHAUSTIVENESS pin; the three deferred-state wordings; the supersede disclosure | `prompt_reload_compact_note_unit` |
| `compaction_unit_test` (PACKS.md:3577 / :4504 / :4572 — three conflicting rows) | ALL lane engine-level legs (merge-skip ×3 sites, render, ceiling, previous-overview, armed-aware floor, T4, consumption predicate, anti-refire, envelope pins) | `prompt_reload_compact_note_unit` (engine-level file) |
| shared `tests/integration/test_service_tool_flag_off_byte_identical.py` | the byte-identity leg | `prompt_reload_compact_note_e2e` (lane-owned file) |

**Shared test files and shared packs are NOT edited by this lane** — `test_prompt_reload_tool.py`, `test_prompt_reload_refusals.py`, `test_compaction.py` and the shared pack scripts stay tester-lane-owned. **Two pre-existing pins the lane MUST VERIFY (not edit):** `tests/unit/tools/test_prompt_reload_tool.py:14` ("zero conversation-history writes — grep + behavioral"; expected to stay green — the arm lives in `instances.metadata`, never the conversation history) and `tests/unit/tools/test_prompt_reload_pins.py:71` (`assert len(PRIVILEGED_TOOL_CATEGORIES) == 23`; **unaffected — no new tool category is created**). The shared refusal census pin (`==9` REFUSED codes, `prompt_reload_refusals_unit`) is **also undisturbed** precisely because the new codes' legs moved out of that file. **If a pre-existing pin genuinely fails on the surface change, that is a STOP-and-report to the leader / tester lane — never an in-lane edit of a shared pack.**

**Audit:** the frozen INFO line (D24 pattern) extended with arm fields — content-free. No new audit table.

**⚠ Routed follow-up (NOT silently fixed in this lane):** `PACKS.md:4572` describes `compaction_unit_test` as a "22-file authoritative compaction-scoped set … 781/0/0", while the on-disk script runs **one file** — 18 compaction test files run under **no named pack**. Tester-lane-owned; untouched here.

---

## Pre-filter decision matrix

| Option | Verdict |
|---|---|
| Decision-family seam via EX-2 removal | **REFUSED** — standing Emperor-ratified exclusion + no removal-admitting monotonicity + the structural D3 ratification gate; recorded as the standing open question (§11) |
| Purpose-bound non-family **LLM** triage (own model/budget/kill, default OFF) | Legal **upgrade occupant** for the stage — **NOT built this release** (no LLM consumer ships; §F, SCOPE DISCIPLINE 6); disclosed as a substitution, never "the decision engine" |
| **Deterministic marker matcher** (in-process, zero LLM, default OFF) | ✅ **v1 occupant — IN THIS RELEASE'S BUILD SCOPE** as the NEW file `daemon/_compact_note_prefilter.py` (§F build-scope confirmation) |
| No filter at all (design annex only) | Adopted **only if** the Emperor's doctrine ruling lands as "no deciding surface ever"; the stage is ~30 lines + tests, default-off, cheap to delete |

Lands on the leader's recorded option **(B)** with a zero-LLM occupant. **The substitution is disclosed prominently at the top of this document, not in a footnote.**

---

## Default-state table (v1.4)

| State | Behavior |
|---|---|
| Pre-filter OFF (default), no note | **Byte-identical to today** — golden-parity pinned |
| Note armed, context ≥ floor (default) | **Forced note-guided compaction at the next quiescent seam (T3/T4)** — gate bypasses the threshold; engine runs `force=False`; note = GLOBAL band, verbatim; merge skipped at the `_merge_summaries` entry guard (all three sites); sections + tail unchanged |
| Note armed, context < floor — ANY budget state | **Armed-aware floor predicate ⇒ clean skip (`skipped_below_min_messages_armed`), NO tail truncation in any corner**; arm persists; status: `armed — deferred (below minimum-compaction floor), not compacted`; fires when the floor is crossed — **GATE (i)** |
| Note armed, 60s dedup active (rare) | One seam-cycle deferral max; status: `armed — deferred (anti-refire window), not compacted`; **never a success receipt** — **GATE (ii)** |
| Note armed, doc-less outcome fires (emergency / last-effort) | Arm NOT consumed; no false provenance; no rate-limit arming; status: `armed — still armed (last compaction produced no doc), not compacted` |
| `compact=true` within 15 min of a deliberate compaction | Typed refusal `prompt_reload_compact_rate_limited` (arm time, echoes remaining seconds) — **GATE (iii)** |
| Armed `/compact` (T4) | Note-guided compaction, executor passes `force=False`; unarmed `/compact` keeps `force=True` byte-identically |
| Pre-filter ON, note armed | Marker-matched whole groups dropped (itemized in dropped-spans); never-empty wall; **every failure ⇒ identity** |
| Pre-filter ON, no note | No-op — gated on note-present; byte-identical |
| jev / decision family | **UNTOUCHED** — S1 shadow-only detached; EX-2 intact; D3 gate intact; USD 50/mo ceiling intact |
| `prompt_reload(force=…)` (today's shape) | **Byte-identical** — pure additive surface |
| Daemon restart between arm and seam | Arm survives (JSONB); note fires at the next quiescent seam; **no staleness mechanism exists (§D.3 — retired)** |

---

## Commission clause 4 — the owed rewording (architect's draft)

> **NO NEW DATA-LOSS SURFACE ON THE NOTE PATH:** on every doc-producing compaction, everything the engine already preserves — the verbatim tail, the hoisted injected notes, the envelope, the section summaries, the sentinel pre-write guard — is preserved unchanged; the note ADDS an authoritative layer and removes nothing that survives today. The pre-filter is the ONE separately-authorized exception: it may remove a candidate group only when the note's structured marker block mechanically accounts for that group (the seam-safety invariant), and only when the operator has armed it (default OFF).

---

## SCOPE DISCIPLINE — what this lane must NOT build

1. **No parallel engine** — `daemon/compaction.py` stays the single source for grouping, selection, ceiling, sentinel, persist, and render.
2. **No new agent** — the "dreamer" is a *mode* of the existing pipeline (ruling A), not a roster entry.
3. **No changes to the default (unarmed) compaction path** — byte-identical, proven by golden-parity; the note-gated conditionals added to shared code (the ceiling numerator; the `_merge_summaries` entry guard; **the armed-aware floor predicate — leader-ratified, §D.2(iv)**) are skipped entirely when no note is armed.
4. **No doctrine change** — EX-2, the `Monotonicity` enum, and the D3 ratification gate are untouched; the open question is recorded (§11), not decided.
5. **No new knobs beyond two** — `compact_note_max_chars` (default 12000) and `compact_note_prefilter_enabled` (default **OFF**, env-only override, deliberately absent from `config.yaml`; `CompactionConfig` is `extra="forbid"` — every knob is an aliased class field). **The deliberate-compact rate limit is a HARDCODED MODULE CONSTANT (`DELIBERATE_COMPACT_RATE_LIMIT_S = 900`), NOT a knob** — it MAY be hosted in `daemon/constants.py` or in the tool module; both are inside the widened edit surface. The future LLM occupant's budget/timeout are specified here only.
6. **No LLM consumer ships in this release** — the v1 pre-filter occupant is the deterministic matcher (`daemon/_compact_note_prefilter.py`, default-OFF) and IS in scope; only a FUTURE LLM occupant is excluded.
7. **No summarizer-prompt changes** — sections are generated exactly as today (§D ii).
8. **No audit table** — the INFO line is the audit surface, content-free.
9. **No edits to pre-existing shared test files or shared packs** — every lane leg lives in the lane's own three packs (§I); a genuinely-failing pre-existing pin is a STOP-and-report, not an in-lane edit.
10. **Doc deliverable owed by the implementer:** `docs/guidelines/guideline-prompt-reload-compact-note.md` — README checklist items 3 and 4 (`prompt-reload` is PRIVILEGED #20, `daemon/tools/_tool_registry.py:845`; the count pin `tests/unit/tools/test_prompt_reload_pins.py:71` is **unaffected**).
11. **Docs line (carries the design's soul — ship verbatim):** *"the working-context counterpart of the memoir dream lane — the dream consolidates her episodic memory; the compact note consolidates her working memory."*
12. **No staleness mechanism** (§D.3 — ruled RETIRED; an unruled mechanism is a future bug report).

---

## Open questions (recorded, not decided)

1. **The Emperor's doctrine question:** may the decision-engine overlay ever be a DECIDING surface (a P1 doctrine amendment)? This lane's design is inert to the answer. Surfaces on the Emperor's next waking, alongside EX-2 and the absence of a removal-admitting monotonicity direction.
2. **The pre-filter stage's right to exist:** if the answer to (1) is "no deciding surface ever," delete the stage (§F top). If "yes," the designed slot takes the LLM occupant — or the literal decision-family route becomes a SEPARATE lane (EX-2 removal + a removal-admitting direction + a ratified `ReviewRecord`).
3. **W1's ambiguity, resolved by ruling:** the "note" here is a **NEW agent-authored note** passed as a tool parameter — NOT the compaction-internal operator notes (`daemon/_content_hardening.py:54-73`) and NOT the project-scoped `critical_notes` table.

---

## Divergences, drifts, and dispositions

| Source | Divergence | Disposition |
|---|---|---|
| Commission orientation | "seam = turn boundary, never mid-turn" | **Corrected** (§0.1) |
| W3 | Note rides the `global_overview` parameter | **Overruled** — 600-token silent truncation + one-of-three-branches (§D) |
| Council | Consumption at ANY doc-writing site | **Overruled** — quiescent-only (§H, with flip condition) |
| Council D1 | Cap 4000/6000 | **Overruled** — 12,000 (§G) |
| Council R7 | "Clear the arm after a real write" | **Superseded by C-1(i)** — the predicate is `compaction_type` ∈ doc-producing |
| Leader challenge + resident axis argument (v1.1) | v1.0 "note waits indefinitely" | **WITHDRAWN and REVISED** (§H) — category error; forcing lives in the GATE |
| Planner D25 (v1.2) | Third merge site `:4187` | **Planner right** — skip's home is the `_merge_summaries` entry guard (§D.1) |
| Planner D26 (v1.2) | `:3768` vs `:3769-3773` | **Planner right** — corrected throughout |
| Planner | REFUSE on second-note | **OVERRULED by leader, ruling A** (§G) |
| Planner | Cap 8,000 | **SUPERSEDED by leader, ruling B** — 12,000 |
| **Review council C-1 (v1.3)** | **v1.2's below-floor safety claim false in the over-budget corner; T4 force=True hole; doc-less outcomes consume the arm** | **ADOPTED in full (§D.2)** |
| Approver/reviewer A-3 (v1.3) | Guard return `(None, False)` violates `tuple[str, bool]` | **ADOPTED** — `("", False)` (§D.1) |
| Reviewer A-4 (v1.3) | Receipt omitted the binding anti-refire clause | **ADOPTED** — clause added verbatim to §G + §H |
| ES-7 crit 4 / Risk #4 (A-5, v1.3) | Unruled staleness mechanism | **RETIRED explicitly (§D.3)** |
| Reviewer advisory sweep (v1.4) | The agent-facing gate literal was not exhaustive | **CORRECTED (§H)** — three gates, EXHAUSTIVE inventory, "unconditionally" removed, exhaustiveness pin added |
| **Reviewer routing sweep (same pass, v1.4)** | **§I routed lane legs into pre-existing shared packs (`prompt_reload_tool_unit`, `prompt_reload_refusals_unit`, `compaction_unit_test` with three conflicting PACKS.md rows) — a lane leg would INHERIT that gate-verifiability defect, and it collides with the concurrent group-chat lane's PACKS.md rows** | **ALIGNED to the plan's routing (§I)** — every lane leg now lives in the lane's OWN three packs (`prompt_reload_compact_note_unit`, `prompt_reload_compact_note_e2e`, `compaction_note_prefilter_unit`) in NEW lane-owned test files; **no shared test file or shared pack is edited**; the moved-legs table names each leg; pre-existing pins are VERIFIED, not edited (a genuine failure is a STOP-and-report) |
| **Reviewer heading check (same pass, v1.4)** | **§D heading "Consumption + exactly-once" contradicted the body and the plan's ES-7 at-most-once wording (two stores; persist-then-delete crash window — Risk #3's carve-out)** | **ALIGNED (§D)** — heading and body now state AT-MOST-ONCE per arm in steady state, with the two stores named and the crash-window re-application disclosed as at-least-once inside that window |
| **Reviewer LLM-occupant check (same pass, v1.4)** | **§C/§F sentences could be read as implying an LLM consumer ships** | **CORRECTED (§C, §F, §G, SCOPE DISCIPLINE 6)** — "NO LLM CONSUMER SHIPS IN THIS RELEASE" stated explicitly; the LLM occupant is named as a FUTURE occupant only |
| Leader ruling (same pass) | The latest-amendment pointer must name v1.4 @ `a95edb33` | **CARRIED** — the status header names v1.4 @ `a95edb33` as the architecture ruling of record |
| W2/KV anchors | `transition_stage`/`is_deciding_permitted` lines | **Drifted — re-pinned** to `:355-387` / `:389-399` |
| PACKS | `:4572` claims 22 files; script runs 1 | **Routed follow-up** (§I) — tester-lane-owned |

**Confidence: HIGH.** Flip conditions: engine shape — if the note must later alter selection semantics, A and C converge. Consumption — one predicate if the Emperor prefers any-site.

**Evidence instances:** W1 `f1251596-9fec-4fb8-8e35-ca1ddef97cad`, W2 `fbe80e10-00b9-49b3-abba-031c20769c0d`, W3 `f4ff527d-112b-461d-9d59-18382530c1a6`, Council `e824ef5e-e407-4961-bfc0-e4923f7dde8b`, review council (C-1, via leader).

---

## 13. Amendment-round changelog (v1.0 → v1.4 + same-pass corrections, 2026-11-17)

1. **(v1.1) Consumption ruling REVISED** — armed note FORCES note-guided compaction at the next quiescent seam; forcing lives in the GATE with `force=False`; rate limit `DELIBERATE_COMPACT_RATE_LIMIT_S = 900` (hardcoded); the resident's three inputs folded; leader confirmations A/B recorded; substitution prominence raised.
2. **(v1.1.1) The resident's axis argument folded in** — SIZE vs RELEVANCE; category error; no surviving defense under the cost-the-author-does-not-intend-to-pay constraint.
3. **(v1.2) D25 RULED** — three `_merge_summaries` call sites; the skip's single home is the entry guard (def `:4306`); render sharpened to a note-first gate; `previous_overview` line suppressed when a note is consumed. **D26** — planner's `:3768` right.
4. **(v1.3) C-1 RULED — the four-part fix ADOPTED**; **A-3** guard returns `("", False)`; **A-4** the binding anti-refire clause added; **A-5** staleness mechanism **RETIRED** (§D.3).
5. **(v1.4) The agent-facing gate literal made EXHAUSTIVE** — exactly THREE gates (floor / 60s anti-refire window / rate limit) with an EXHAUSTIVE GATE INVENTORY table; "unconditionally" removed; exhaustiveness pin added; **pre-filter v1 occupant (the deterministic zero-LLM matcher) confirmed in this release's build scope** as NEW `daemon/_compact_note_prefilter.py` (default-OFF); only the LLM occupant excluded.
6. **(v1.4 same-pass corrections, this commit — no substantive ruling changes):**
   **(a) §I ROUTING ALIGNED to the plan's** — every lane leg moves into the lane's OWN three packs (`prompt_reload_compact_note_unit`, `prompt_reload_compact_note_e2e`, `compaction_note_prefilter_unit`) in NEW lane-owned test files; the moved-legs table names each leg (five new refusal codes + over-cap + note-without-compact out of `prompt_reload_refusals_unit`; armed-receipt shape + never-"compacted" grep + exhaustiveness pin + three deferred-state wordings + supersede disclosure out of `prompt_reload_tool_unit`; ALL engine-level legs out of `compaction_unit_test`'s three conflicting rows; the byte-identity leg off the shared integration file into the lane's E2E file); **no shared test file or shared pack is edited**; the two pre-existing pins are VERIFIED not edited, and a genuine failure is a STOP-and-report.
   **(b) §D heading + body ALIGNED to at-most-once** — "Consumption — AT-MOST-ONCE per arm (two stores; the persist-then-delete crash window — Risk #3's carve-out)", naming both stores (the arm row in `instances.metadata`; the compaction record in the checkpoint) and disclosing the crash-window re-application as at-least-once inside that window.
   **(c) NO-LLM-CONSUMER wording corrected** in §C (the decision-family shape is the PATTERN a future LLM occupant would inherit; the v1 deterministic occupant needs no model call, no budget, no model resolution), §F ("NO LLM CONSUMER SHIPS IN THIS RELEASE"), §G (pre-filter matrix), and SCOPE DISCIPLINE 6.
   **(d) The latest-amendment pointer names v1.4 @ `a95edb33`** as the architecture ruling of record, per the leader's same-pass residual.
