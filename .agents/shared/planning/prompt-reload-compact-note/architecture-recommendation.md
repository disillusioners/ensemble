# Architecture Recommendation — `prompt_reload(compact, compact_note)` (note-guided compaction)

**Status:** DECISION OF RECORD — v1.2 (amendment rounds 2026-11-17: consumption ruling REVISED per leader challenge + resident axis argument; three resident inputs folded; two leader confirmations recorded; substitution prominence raised; **D25 ruled — the merge-skip's single home + render-gate sharpening; D26 anchor corrected**. Changelog at §13.)
**Date:** 2026-11-17 (UTC)
**Base:** `feature/prompt-reload-compact-note` @ `858a85e31489c7db9e9275955e63c021cfc5a2c4` == `origin/latest` (worktree `/home/nea/ensemble-src-wt-prompt-reload-compact`, porcelain clean at every read)
**Method:** 3 dispatched analysts (`data-flow-design` ×2, `structural-design` ×1) + 1 governor council (2 models, `trade-off-analysis`) + architect's own re-measurement of every load-bearing anchor. Every `file:line` below was **measured at use time** at `858a85e31`; anchors from second-hand sources were re-pinned where drifted (four drifts found and corrected — §12).
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
6. **`emergency_truncate` (def `daemon/compaction.py:1645`) bypasses the GLOBAL render entirely** — `compaction_type="emergency_truncation"` (`:3560`), no doc, no envelope, no placeholder; its C1 head-pop (`:1725-1727`) drops oldest messages arbitrarily. Contrast: the `|S|=0` **truncation fallback** (`_truncate_fallback`, def `:4891`) DOES build a doc at `:5035` and DOES hit the render chain.
7. **(v1.1) `force=True` below the minimum-compaction floor does NOT skip — it 50%-tail-truncates.** At `daemon/compaction.py:3357-3386`: below floor + `not force` + `not over_budget` → clean `anti_refire_skip(skip_reason="skipped_below_min_messages")`; but `force` OR `over_budget` → falls through to `_last_effort_tail_truncation` — *"last-effort floor (50%-tail truncation)"* — **dropping half the verbatim tail**. The floor is engine-side and pre-existing: `min_messages_before_compaction: int = Field(default=10)` (`daemon/config.py:1660`; `daemon/constants.py:603`; enforced `daemon/compaction.py:3357`, referenced `:3226`/`:3380`).
8. **(v1.2, D25) There are THREE live `_merge_summaries(` call sites, not two.** Measured: `:3687` (call span `:3687-3691`) — the outer **re-merge** inside `if outcome.merge_failed:` (`:3657`) on the full-success path; `:3768` (span `:3768-3772`) — the partial path (`:3649`, stop_reason ∈ {timeout, budget}); **`:4187` (span `:4187-4191`) — the INNER merge inside `_summarize_chunked` (def `:3922`, its sole caller `:3624`), on the full-success branch (`if partial_summaries and stop_reason == "completed"`, ≥2 summaries)**. On inner-merge SUCCESS the outcome collapses to `summaries=[merged]` and **the outer sites never execute** — a skip gate placed only at `:3687`/`:3768` is dead code on that path. Ruled in §D.1.

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

**Files that change (complete inventory):** `daemon/compaction.py` (CompactionContext +1 field; one render gate; one conditional in the ceiling numerator; **the `_merge_summaries` entry guard**), `daemon/services/instance_messaging.py` (T3 gate: armed-check threshold bypass), `daemon/tools/prompt_reload.py` (widen `PromptReloadInput` at `:225` — a LOCAL class, cheap), `daemon/config.py` (2 new `CompactionConfig` fields), NEW `daemon/_compact_note_prefilter.py` (the stage, lazily imported), NEW tests/packs. **Coupling: 1 dataclass field + 2–3 call sites.**

**Flip condition (both council models):** if the note must later alter *selection semantics* (not just the GLOBAL layer + optional drop set), A and C converge — extract the note-guidance fully into the new module.

---

## B. jev routing — the measured answer

- **Public API** (`daemon/services/decision/client.py`): `DecisionClient(config, ...)` at `:124`; `async consult(request, *, floor=None, timeout_s=None) -> ClientOutcome` at `:160`. **Never raises; never returns an error envelope.** `ClientOutcome` at `:128`. Answer types `{predicate, choice, score, refusal}` (`:861`); 1 MiB response cap (`:65`); single `httpx` POST to `{base}/decisions`, **no retry**.
- **Model routing:** purpose-id **`"decision"`** — `DEFAULT_DECISION_MODEL = "decision"` (`daemon/services/decision/config.py:130`), stamped via `request.with_model(self._config.model)` in `registry.execute`. **`COMPACTION_MODEL` is never consulted by this family** (grep-verified). The compaction summarizer's model resolution is a SEPARATE purpose-bound knob: `resolve_compaction_model` (`daemon/compaction.py:1185`), precedence `config.model` → legacy `summarization_model` → `""`; `COMPACTION_MODEL` env beats the yaml key (`config.yaml:~465-470`), and it is *never consulted against `llm.allowed_models`* — the exact purpose-bound precedent the pre-filter must copy.
- **Cost metering:** `app_settings` table, key `decision_cost_ledger_{YYYY-MM}` (JSON: period/usd/tokens/per-seam), one process-level lock. Ceilings: **USD 50.0/mo global**, **10M tokens/mo**, per-seam share 25.0, per-attempt floor $0.0005. On breach: `trip_shadow_off` writes per-seam enabled=0+stage=shadow AND global off, then board item + daemon-log WARNING (measured `overlay.py:959-977` region).
- **What `"JEV call counts towards …"` actually bounds** (`daemon/config.py:4320`): it sits in `MemoirReflectionConfig.max_llm_calls_per_pass` (default 4) and bounds the **memoir reflection pass** — the memoir persona-gate classifier is the only thing NAMED "JEV" in-tree. **Two distinct "JEV"s, zero coupling.** The decision family's bounds are the ceilings above.
- **Kill switch:** three surfaces, ladder DB > env > yaml > default, **fail-closed**: global `decision_global_enabled` (env `DECISION_ENABLED`, default **OFF**), per-seam enabled (default **OFF**), per-seam stage (default **shadow**). Write path is **operator-only**: `SettingsService.set_override` over `MANAGED_KEYS` (`daemon/services/settings_service.py:635-675`); the `settings` category is granted to ari+leader only — no worker can flip it.
- **Shadow-only enforcement is STRUCTURAL, not conventional** (three independent walls): (1) `DENIED_SEAM_ROLES` (`registry.py:82-90`) names `compaction_message_selection` (**EX-2**, `:85`) and registration is *refused before any call path exists* (`:275-280`); (2) the `Monotonicity` enum (`types.py:92-97`) admits only `TOWARD_COMPLETION / TOWARD_TIGHTENING / WITHIN_SET / ADVISORY_ONLY / NONE` — **no direction admits removal**; add-only demands superset (`registry.py:616-623`), `WITHIN_SET` may never empty (`:629-630`); (3) `transition_stage` (`registry.py:355-387`) refuses shadow→deciding without a ratified `ReviewRecord` — the docstring names *"the Emperor's ratification"* — and `is_deciding_permitted` (`:389-399`) makes writing `deciding` into the store alone inert. **No production caller of `transition_stage` exists.** Only one seam is registered at all: S1 `snapshot.l3.selection` (shadow, detached).

---

## C. Governance tension — RULING: the pre-filter is **OUTSIDE** the P1 doctrine; **not** a seam; a purpose-bound non-family stage; **default OFF**

The Emperor's directive authorizes jev filtering; the ratified doctrine says the overlay never decides. The measured truth dissolves the tension: **the decision family structurally cannot express "drop this message"** (§0/§B walls). Therefore:
- The pre-filter is a **distinct, purpose-bound mechanism inside the compaction pipeline** — never registered on the decision registry, never called through `DecisionOverlayPort`, never billed to the decision ledger. It reuses the *pattern* (own model resolution à la `resolve_compaction_model`, own budget, own kill lever, single attempt, identity on every failure), not the family.
- **Kill lever:** one `CompactionConfig` bool (the `proactive_enabled` precedent at `daemon/config.py:1686` — aliased `AliasChoices` field, `extra="forbid"`-safe per the `dedup_window_s` idiom at `:1738-1753`), **code-default OFF, env-overridable, and deliberately NOT set in `config.yaml`** — a yaml value would silently defeat an operator's env kill-switch (pydantic-settings init-kwargs priority).
- **P1 SHADOW-ONLY doctrine stays ratified, untouched.** EX-2 stays. The enum stays. The D3 gate stays. The open question is **recorded in §11, not decided here**.

**Byte-identity guarantee (structural):** the filter's only input is the compactable partition output of `select_compactable_groups` (`:1582`); the verbatim tail, hoisted injections, sentinel, and ceiling are outside its reach. Every failure mode — off, timeout, transport, over-budget, unparseable, out-of-set references, empty-output-on-non-empty-input — converges to **identity pass-through: "skipped" and "off" are the SAME code path**, not two paths that happen to agree.

---

## D. Note placement + composition — RULING

**Carrier (arming store): `instances.metadata` JSONB** via `InstanceRepository.set_metadata` / `delete_metadata` (`daemon/repositories/instance/repository.py:2990+` — "dialect-aware single-statement UPDATE so concurrent calls targeting different keys compose correctly"). Keys: `compact_note_arm` (the verbatim note), `compact_note_armed_at`, `compact_note_sha256_12`, `compact_note_last_applied_at` + `compact_note_last_sha256_12` (consumption receipt + the rate-limit clock). This is the exact precedent of `daemon/services/_escalation_metadata.py:73-84` (four compaction-scoped keys). **Not** `SessionState` (the arming write originates in a tool mid-turn; T3 consumes pre-dispatch outside the graph node), **not** an in-memory registry (`prompt_reload`'s pop is restart-unsafe — W1's mirror-warning).

**Migration verdict: NO.** The record is the checkpoint `messages` channel (§0.4); the doc body is a plain string (`SystemMessage(content=body)`, `daemon/compaction.py:1053-1056`) carried as opaque JSONB/BYTEA. A note riding the doc body is string concatenation inside `build_compaction_doc`. The arming carrier is existing JSONB. **Zero DDL, zero schema evolution, in either place.**

**Render slot: `daemon/compaction.py:993-1032` — the note is the FIRST arm of the GLOBAL OVERVIEW region's branch (v1.2 sharpening of v1.1's ":1033 additive band").** The `── GLOBAL OVERVIEW ──` header opens at `:993`; the branch structure is:
- **note-first gate (new, first):** `if note: body += note + "\n"` — **the note REPLACES the three-way chain when a note is consumed.** Same region, verbatim, **NOT subject to `GLOBAL_OVERVIEW_TOKEN_CAP` (600)** render-time truncation (`:1012-1021`); the **only** cap enforcement is the arm-time typed refusal (§G).
- `if global_overview:` (`:998`) → cap check + merged text — **auto path only; unreachable when a note is consumed** (the merge is skipped ⇒ `global_overview` is `None`).
- `elif mode == "truncation":` (`:1024-1029`) → nothing — auto path only.
- `else:` (`:1030-1032`) → the placeholder — **auto path only; SUPPRESSED when a note is consumed** (co-emitting `"(overview unavailable — merge pass failed…)"` under an authoritative note would be double text and a lie — "unavailable" when the note IS the overview).
- `if n_sections > 0:` (`:1034`) → SECTION DETAIL — unchanged on both paths.

This is strictly stronger than v1.1's "additive band on all three paths": D25 exposed that a purely additive band would **co-render with the placeholder/merged text** when the merge is skipped. The invariant that matters is preserved and sharpened: **the note renders in the GLOBAL OVERVIEW region on every doc-producing path (full / partial / truncation-fallback) whenever a note is consumed, the auto arms are byte-identical when it is not, and the note never co-exists with another GLOBAL text.** (This overrules W3's slot-(ii) recommendation — riding the `global_overview` parameter would be silently truncated at ~600 tokens AND lost entirely on `global_overview=None`.)

**One consequential predicate rides with the gate:** the `if previous_overview:` line at `:996-997` ("Previous overview: …") is **suppressed when a note is consumed**. Reason: after a note-guided compaction the prior doc's GLOBAL *is* the note, so `_extract_previous_overview` (`:222`) would otherwise re-emit the old note verbatim (up to 12k chars, uncapped — `:996-997` sits before the cap check) directly above the new note — unbounded cross-pass duplication. The note REPLACES the overview lineage for that conversation; it is authoritative and self-contained. (The no-note path keeps `previous_overview` seeding byte-identically.)

**Budget policy:** the note is **counted** in the `_apply_ceiling_rule` numerator when present (never-condensed class, alongside GLOBAL) and **never render-truncated**. Implementation: `note_tokens = estimate(note) if note else 0` added to the numerator at `:788` — with no note the computation is bit-identical (the estimate is skipped entirely, not estimated-as-zero). At the 12,000-char cap (~3k tokens) the note is ~0.43% of a 700k window and ~2.9% of the 15%-ceiling allowance (105k tokens); on small windows the ceiling condenses sections — never the note — which is the intended authority ordering. Disclosed: on a 32k window a max-cap note could squeeze sections heavily.

**Composition with today's layers:**

| Layer | Ruling |
|---|---|
| (i) `global_overview` | **The merge pass is NOT RUN when a note is consumed** — via the **single entry-point guard in `_merge_summaries` (§D.1)**, which covers all three call sites by construction. The note occupies the GLOBAL region's authority. **Merge-success case: moot — not suppressed, not demoted, not preceded; simply not generated.** The merge-overview and the note never coexist in one doc. On the auto path (no note) the merge runs byte-identically. |
| (ii) Section summaries | **Unchanged — the summarizer still runs, unseeded** (`_summarize_chunked` at `:3624` is ON the note-guided path by design — it produces the sections). The note replaces the *merged overview*, not the per-batch sections. Seeding the summarizer prompt with the note is a documented future increment (flip: if sections visibly contradict the note's account). |
| (iii) Verbatim tail | Untouched — `select_compactable_groups` window logic (`:1625-1642`), appends at `:3727-3731` / `:3790-3794` / `:5051-5055`. The pre-filter may draw only from compactable groups, never the tail. |
| (iv) Sentinel / emergency | The sentinel is untouched (it is applied in the persist seam, `:224`, and never bypasses the doc). **`emergency_truncate` produces no doc — the note does not render there; the arm PERSISTS and the status surface discloses "still armed."** (The alternative — riding the hoist bucket, which survives even emergency via the `:3530` re-attach — was rejected because it renders *above* the doc, violating the placement clause.) |

**Consumption + exactly-once:** the arm is consumed by the **first quiescent (Variant A, `mid_turn=False`) doc-writing compaction** — T3 expected, T4 (`/compact`, also quiescent) also consumes. **Mid-turn sites (T1/T2) NEVER read the arm** — they stay byte-identical by construction, honoring the commission's "NEVER mid-turn" for the tool-initiated path. The arm clears **only when `result.replacement_messages` is non-empty AND `persist_compaction_result` returns `True`** — the bool alone cannot distinguish a full write from the stamp-only anti-refire write (the seam returns `True` for both, `daemon/services/_compaction_persist_seam.py:72-107`), and the stamp-only path must NOT consume the arm. Crash-window (engine success, delete not yet run): the note re-applies at the next quiescent seam — bounded and benign; disclosed. Emergency (`compaction_type="emergency_truncation"`, no doc) does not consume.

### D.1 — D25 RULING: the three merge sites, and the skip's single home

**Verification (re-measured at `858a85e31`):** the planner's claim is **CORRECT and material**. Three live `_merge_summaries(` call sites exist:

| Site | Call span | Enclosing branch | Fires when |
|---|---|---|---|
| 1 | `daemon/compaction.py:3687-3691` | outer **re-merge** inside `if outcome.merge_failed:` (`:3657`) | inner merge failed (retry) |
| 2 | `daemon/compaction.py:3768-3772` | partial path (`:3649`, stop_reason ∈ {timeout, budget}) | partial summaries |
| 3 | **`:4187-4191`** | **inner merge inside `_summarize_chunked`** (def `:3922`; sole caller `:3624`), full-success branch (`if partial_summaries and stop_reason == "completed"`, `len ≥ 2`) | **all batches completed, ≥2 summaries — fires FIRST** |

On site-3 success the outcome collapses to `summaries=[merged]` and the outer sites **never execute** — my v1.1 coverage citation (sites 1–2 only) was therefore **wrong on the primary path**: a gate at `:3687`/`:3768` alone is dead code exactly when the merge would actually run. **No structural unreachability exists:** `_summarize_chunked` IS the section summarizer that note-guided mode deliberately keeps (§D ii), so site 3 is on the note-guided path by design; the `len ≥ 2` precondition is ordinary for any real compaction, and nothing note-aware exists inside `_summarize_chunked` today.

**RULING — the skip predicate's single home is the FIRST STATEMENT of `_merge_summaries` itself (def `daemon/compaction.py:4306`):**

```python
async def _merge_summaries(self, ...):
    if context.compact_note:
        logger.info("merge skipped: note-guided mode (iid=%s)", iid8)  # content-free
        return (None, False)
    ...
```

**Why the entry point, not the sites:** site-enumeration gates are exactly the drift class D25 caught (a guard per call site is stale the day a fourth site appears). A guard at the single callee covers sites 1, 2, 3 **and every future call site by construction** — the bug class is structurally impossible against it. The return `(None, False)` is the existing merge-failed contract at every site: site 3 takes its `if not ok:` branch (`:4192`) → `ChunkedOutcome(merge_failed=True)` → the outer re-merge at `:3687` hits the same guard (a second no-op, zero LLM) → `global_overview = None` (`:3697`/`:3778`) → **the note-first render gate (§D) emits the note; the placeholder never co-renders.** Observability stays honest via the guard's own content-free INFO line ("merge skipped: note-guided mode"), and the envelope's additive `note_guided=true` disambiguates the `total_summary_status="failed"` that the skip routes through — the FE-read `"ok"|"failed"|None` enum is NOT overloaded (council R7).

**D26 (anchor adjudication):** the planner is right — the second call begins at **`:3768`** (span `:3768-3772`; `:3773` is `if ok:`). My v1.1/W1 citation `:3769-3773` was off by one, as was `:3686-3690` (correct: `:3687-3691`). Corrected throughout this document.

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

**ONE pass, no retry; every failure ⇒ skip-the-filter, never fail-the-compaction.** Taxonomy: disabled / timeout / transport / over-budget / unparseable marker block / out-of-set references / empty-output-on-non-empty-input — **all converge to identity**, and identity is the *same code path* as "off" (§C). WARN once per pass. The v1 occupant is **deterministic** (set arithmetic, zero LLM), so its wall-clock is effectively zero; the **stage contract** for the future LLM occupant is `min(5s, 10% of remaining operation budget)` — strictly subordinate to the operation budget, matched to the decision-client's 5s single-attempt discipline. **The fallback is byte-identical to no-filter by construction.**

---

## G. Cap + refusal vocabulary

**Cap: 12,000 chars — AUTHORITATIVE (leader ruling, amendment round: the commission says the architect sets it; the planner's 8,000 proposal is superseded — concurrence noted where they agree, delta recorded where they do not).** `CompactionConfig.compact_note_max_chars`, aliased field, default 12000. Reason: the Emperor's stated instinct is "~8–16k"; 12k is its midpoint, comfortably inside budget on any window (§D), generous enough that a rich settled/open/dead account never hits the refusal, tight enough to keep the doc honest. The council's 4k/6k was justified against the 600-token GLOBAL cap — a justification the budget ruling already absorbs (counted in the ceiling, never render-truncated). **Co-pinned in tests against `GLOBAL_OVERVIEW_TOKEN_CAP`** (drift guard). Flip condition: if profiling shows doc-assembly or FE cost at 12k, drop to 8k (the Emperor's floor).

**Refusal vocabulary** — house envelope `Error: prompt_reload REFUSED — reason={code}: {message}` (`daemon/tools/prompt_reload.py:94-96`; codes at `daemon/services/prompt_refresh.py:147-174`). Five NEW codes (the existing 9 REFUSED + 1 FAILED stand; the "codes 1-9" comment and the `prompt_reload_refusals_unit` pin are **re-derived, never copied**):

| Code | Trigger | Behavior |
|---|---|---|
| `prompt_reload_note_required` | `compact=true` with note absent **or** whitespace-only (one code — forking them buys nothing) | REFUSED; nothing armed |
| `prompt_reload_note_over_cap` | note > 12,000 chars | REFUSED; **echoes chars + cap; never truncates; nothing armed** |
| `prompt_reload_note_requires_compact` | note supplied with `compact=false` | REFUSED — **ignoring is dishonest** |
| `prompt_reload_compact_disabled` | `compact=true` while `CompactionConfig.enabled=false` | REFUSED — arming a note that can never land would strand it |
| `prompt_reload_compact_rate_limited` *(v1.1)* | `compact=true` within `DELIBERATE_COMPACT_RATE_LIMIT_S` of the last **deliberate** (note-guided) compaction | REFUSED at **arm time** — echoes remaining seconds; **an honest typed refusal, never a silent deferral** (the resident's anti-abuse preference) |

**Deferred states (v1.1) — receipt vocabulary, never success receipts.** Per the resident's non-negotiable: *if any window ever binds, the receipt MUST say "deferred, not compacted" — never a success receipt for work not done.* Two honest states ride the arm-status surface:
- `armed — deferred (below minimum-compaction floor), not compacted` — the engine's own `skipped_below_min_messages` skip (`daemon/compaction.py:3368-3370`); the arm persists and fires as soon as the floor is crossed.
- `armed — deferred (anti-refire window), not compacted` — worst case a single 60s dedup cycle (`:3126`), only when an automatic compaction fired moments before the armed seam.

**Second note while one is armed: REPLACE, with disclosure — LEADER-CONFIRMED (amendment round, ruling A: the planner's REFUSE proposal is OVERRULED in this design's favour).** The receipt carries `superseded_previous=true` + the prior note's `sha256[:12]`. **The trap is the rationale, recorded as ruled:** a refuse would TRAP the agent — arm → wait → cannot correct or update its own distillation, which is precisely the thing the feature says the agent owns; a refuse without any un-arm path strands a mistyped note permanently. `compact=false` with no note = today's behavior, byte-identical.

**Every refusal is honest: the tool only ARMS.** No code path can emit a success-for-compaction receipt — the tool body writes the arm and returns; it structurally cannot observe a compaction.

---

## H. The honest tool contract — CONSUMPTION RULING **REVISED (v1.1, leader challenge + resident axis argument accepted)**

### The revision

**v1.0's "disclosed gap — arming does not lower the 80% gate; a note can wait indefinitely" is WITHDRAWN.** It was an internal inconsistency, not a defended position: my turn-level reconciliation had already ruled "(2a) the armed check short-circuits the threshold test," while §H carried the council's gap-disclosure without reconciling it against (2a). The leader's challenge resolves the contradiction, and the resident's contract decides it: **"Compacted now is the contract when I ask"** (Ari, 2026-11-16). The commission's letter agrees — *"compacted at the NEXT SAFE SEAM"*, not "at the next threshold crossing."

### The axis argument (leader-adopted, resident-originated — the decisive reasoning, recorded verbatim-in-substance)

**The 80% gate measures SIZE. The compact note measures RELEVANCE. These are different axes, and the v1.0 ruling conflated them.** The Emperor's founding use case is explicitly relevance-driven, in his own words: *"for this conversation you don't need know about sonic team any more and many thing already old."* That statement is **true at 30% fill exactly as it is true at 90%** — a context can be half-full and still be cluttered with dead arcs. Waiting for SIZE pressure to fire a RELEVANCE distillation means the wake-up never comes when it is actually wanted: the agent asks to refresh its mind, and the system answers *"not until you are big enough."* Threshold-gating a deliberate relevance request on a size predicate is a **category error**, not a trade-off. **The v1.0 position was optimizing token-fill; the feature's purpose is ATTENTION QUALITY.** A note-armed request is DELIBERATE INTENT — it fires at the seam regardless of fill. The automatic 80% gate stays byte-identical for the automatic path (the arm predicate short-circuits false when no arm exists; the default path is untouched).

### Disposition under the defense constraint

The leader's constraint on any surviving defense: *"wasted compaction" is disallowed — the note's author is the one spending it, and a deliberate request that costs one bounded compaction is the agent managing its own attention, not the system wasting tokens. If your defense rests on cost, it must be a cost the AUTHOR does not intend to pay."* **No such defense exists under the revised mechanics, and none is offered.** The one concrete forcing failure mode found this round — `force=True` below the floor triggering `_last_effort_tail_truncation`, **half the verbatim tail dropped** (`daemon/compaction.py:3357-3386`, §0.7) — is precisely *a cost the author does not intend to pay* (the author asked for a note-guided distillation, not a tail amputation), which is why the revised mechanics route the forcing through the **gate** with `force=False`, neutralizing it. With that neutralized: the compaction performed is exactly what the author asked for, its frequency is bounded by the author's own rate limit, and its content is the author's own distillation. **Nothing remains that the author does not intend to pay.**

**REVISED RULING: an armed note FORCES a note-guided compaction at the next quiescent seam (T3/T4), gated ONLY by (i) the existing engine-side minimum-compaction floor and (ii) a hardcoded deliberate-compact rate limit enforced as a typed arm-time refusal.** The no-arm path remains byte-identical by construction — the T3 predicate short-circuits false when no arm exists.

**The mechanics (v1.1) — forcing lives in the GATE, never in the engine's force flag:**
1. **T3 gate** (`instance_messaging.py:1337`): `if armed: bypass the threshold test` (enter the compaction path) and call the engine **with `force=False`**. NOT `force=True` — measured §0.7: force below floor = 50%-tail truncation, a new data-loss surface.
2. **Engine floor** (`compaction.py:3357`): below floor + `not force` + `not over_budget` → clean `anti_refire_skip("skipped_below_min_messages")`. The arm persists; the status surface says `armed — deferred (below minimum-compaction floor), not compacted`; it fires as soon as the floor is crossed at a quiescent seam.
3. **Dedup** (`:3126`): with `force=False`, the 60s window CAN bind — worst case one seam-cycle of deferral, only when an automatic compaction fired moments earlier. Bounded at 60s, disclosed as `armed — deferred (anti-refire window), not compacted`. **Never a success receipt for work not done.** (The rate limit at arm time makes this rare: arming is refused within 15 min of the last *deliberate* compaction, but an *automatic* compaction can still land inside the window.)
4. **Rate limit**: **`DELIBERATE_COMPACT_RATE_LIMIT_S = 900` (15 minutes), a hardcoded module constant — no new config knob** (the commission permits exactly this). Enforced at **ARM time** as the typed refusal `prompt_reload_compact_rate_limited` (echoing remaining seconds), counting **deliberate (note-guided) compactions only** — the clock is `compact_note_last_applied_at`, written at consumption. **Rationale for 900s:** (a) ≤4 deliberate compactions/hour makes a tight arming loop structurally pointless; (b) a genuine agent compacts deliberately a handful of times per session and never sees it; (c) 15× the automatic 60s dedup keeps the resident's two trigger classes numerically distinct by an order of magnitude; (d) well above any agent turn cadence, so a legitimate re-ask after a completed deliberate compact is never refused.

### The receipt (unchanged in shape, updated in wording)

**What the tool returns (arm receipt):** `outcome=armed_pending_seam`, `note_chars`, `cap`, `note_sha256[:12]`, `superseded_previous?`, kill-switch state, expected seam ("your next turn boundary — the quiescent pre-dispatch gate; **it fires there unconditionally, gated only by the minimum-compaction floor and the deliberate-compact rate limit**"), and the explicit line: **"CONTEXT NOT YET COMPACTED — the compaction happens at your next turn boundary; until then this note remains armed."** A still-armed status line (with any deferred state) rides every subsequent `prompt_reload` receipt.

**How the agent learns it landed (three independent signals):**
1. **The context itself** — the doc's **envelope header** carries `note_sha8=<prefix> note_chars=<n>` + `note_guided=true` (additive; FE reads defensively). Provenance rides the *envelope*, not the GLOBAL slot, so the slot stays byte-equal to the note and the **verbatim contract holds exactly**. The agent recomputes the hash of what it authored and verifies its own next-turn context.
2. **Arm-key absence** — `compact_note_arm` deleted on consumption; `compact_note_last_applied_at` + `compact_note_last_sha256_12` written post-hoc (the verification surface AND the rate-limit clock).
3. **The frozen INFO line extended with arm fields — content-free (chars + sha256 prefix ONLY, never the note body, in any agent-readable log).** The note is the agent's working memory and can carry intimate personal content; it rides verbatim in the RECORD and the CONTEXT and nowhere else. **This cites, BY NAME, the memoir-lane content-free doctrine and the decision-family shadow-lane content-free doctrine (logs carry ids/hashes/scores only, v1)** — per the resident's explicit request that the citation, not just the substance, appear.

**Mid-turn sites (T1/T2) never read the arm** — the distress paths stay byte-identical *by construction*. The commission's "NEVER mid-turn" is honored on the tool-initiated path; the council's any-site rule remains overruled (flip: one predicate — the arm read at the two mid-turn call sites — if the Emperor prefers consumption-at-any-site).

**T4** (`/compact`, quiescent, `force=True` by its own existing contract at `compact_executor.py:1220`): also consumes the arm. Its own executor pre-check applies as today and does not consume the arm on a skip. (T4's `force=True` is the *operator's* deliberate path on an over-threshold context by construction — the `/compact` UI contract — and is unchanged by this lane.)

---

## I. Testability — the minimal non-vacuous surface

**Extend REGISTERED packs** (a pin in an unregistered pack is not gate-verifiable):
- `prompt_reload_refusals_unit` (`.agents/tester/PACKS.md:483`) — every new code **including `prompt_reload_compact_rate_limited`**; over-cap ⇒ no-truncate + nothing-armed; note-without-compact ⇒ refused; the 9-code pin **re-derived by grep**.
- `prompt_reload_tool_unit` (`PACKS.md:484`) — arm-receipt shape pins incl. a **never-"compacted" negative grep**; still-armed status line; **both deferred-state wordings** ("deferred (below minimum-compaction floor), not compacted" / "deferred (anti-refire window), not compacted"); supersede disclosure.
- `compaction_unit_test` (`PACKS.md:3577/:4572`; script `test/packs/compaction_unit_test.sh:44` runs `tests/unit/test_compaction.py`) — note rendered **verbatim** (golden bytes); **D25 pin: ≥2 batches + armed note ⇒ NO merge LLM call on the chunked path (`:4187` guarded at `_merge_summaries` def `:4306`) ⇒ per-batch sections survive ⇒ note in the GLOBAL region and NO placeholder co-emission**; merge-**skipped**-when-armed pin (all three sites); render-time-cap-exemption pin (an over-600-token note is NOT truncated at `:1012`); ceiling-inclusion pin; **previous-overview-line suppressed when a note is consumed pin**; armed-forces-compaction-below-threshold pin (the revised ruling: gate bypass with `force=False`); below-floor ⇒ `skipped_below_min_messages` + arm persists + NO tail truncation pin (the §0.7 trap); emergency ⇒ still-armed; stamp-only ⇒ arm-not-consumed; envelope `note_sha8`/`note_guided` pins.
- Byte-identity: `tests/integration/test_service_tool_flag_off_byte_identical.py` (verified to exist) — flag-unset vs flag-on-no-note ⇒ identical doc bytes.

**NEW (must be REGISTERED in `PACKS.md`):** `prompt_reload_compact_note_unit`, `prompt_reload_compact_note_e2e`, `compaction_note_prefilter_unit` (predicate truth table: marker-match drops; free-text ⇒ ∅; every failure ⇒ ∅; input-list immutability; never-empty wall; whole-group atomicity).

**E2E acceptance (Ari's mandatory bar, adopted; v1.1 strengthens the named case):** real conversation + real note + `_merge_summaries` **monkeypatched to fail** ⇒ assert the **NEXT TURN's actual context** carries the note verbatim in the GLOBAL band — "stored" is not the bar. Modeled on the `test_injected_notes_hoisting_acceptance.py` precedent (real StateGraph + AsyncSqliteSaver + real persist seam + fresh-connection reload). **The MERGE-FAILED branch is THE named case of the acceptance artifact** — that is the live defect (Ari's current header: *"overview unavailable — merge pass failed"*). Per the resident: **"Surviving where the overview died is the feature's claim to exist."**

**Audit:** the frozen INFO line (D24 pattern) extended with arm fields — content-free. No new audit table.

**⚠ Routed follow-up (NOT silently fixed in this lane):** `PACKS.md:4572` describes `compaction_unit_test` as a "22-file authoritative compaction-scoped set … 781/0/0", but the on-disk script runs **one file**. The lane must either fix the script to match the claimed scope or re-scope the PACKS row — 18 compaction test files currently run under **no named pack**.

---

## Pre-filter decision matrix

| Option | Verdict |
|---|---|
| Decision-family seam via EX-2 removal | **REFUSED** — standing Emperor-ratified exclusion + no removal-admitting monotonicity + the structural D3 ratification gate; recorded as the standing open question (§11) |
| Purpose-bound non-family **LLM** triage (own model/budget/kill, default OFF) | Legal **upgrade occupant** for the stage — NOT built this release (no new LLM consumer); disclosed as a substitution, never "the decision engine" |
| **Deterministic marker matcher** (in-process, zero LLM, default OFF) | ✅ **v1 occupant** — Ari's invariant demands the marker scheme regardless; a shipped matcher makes "vacuous-until-markers" *real* rather than documented; the house ship-gated-flip-later pattern (P1 itself, RAM guard, prompt-assent) |
| No filter at all (design annex only) | Viable leaner alternative (= leader option A) — adopted **only if** the Emperor's doctrine ruling lands as "no deciding surface ever"; the stage is ~30 lines + tests, default-off, cheap to delete |

This lands on the leader's recorded option **(B)** with a zero-LLM occupant, honors the Emperor's capability ask without touching EX-2, and defaults OFF per Ari's containment (a). **The substitution is disclosed prominently at the top of this document, not in a footnote.**

---

## Default-state table (v1.2)

| State | Behavior |
|---|---|
| Pre-filter OFF (default), no note | **Byte-identical to today** — golden-parity pinned |
| Note armed, context ≥ floor (default) | **Forced note-guided compaction at the next quiescent seam (T3/T4)** — gate bypasses the threshold test; engine runs with `force=False`; note = GLOBAL band, verbatim; **merge skipped at the `_merge_summaries` entry guard (all three sites)**; sections + tail unchanged |
| Note armed, context < floor | Engine's own `skipped_below_min_messages` skip; **arm persists**; status: `armed — deferred (below minimum-compaction floor), not compacted`; fires when the floor is crossed. **NO tail truncation** (`force=False` by construction) |
| Note armed, 60s dedup active (rare) | One seam-cycle deferral max; status: `armed — deferred (anti-refire window), not compacted`; **never a success receipt** |
| `compact=true` within 15 min of a deliberate compaction | Typed refusal `prompt_reload_compact_rate_limited` (arm time, echoes remaining seconds) |
| Pre-filter ON, note armed | Marker-matched whole groups dropped (itemized in dropped-spans); never-empty wall; **every failure ⇒ identity** |
| Pre-filter ON, no note | No-op — gated on note-present; byte-identical |
| jev / decision family | **UNTOUCHED** — S1 shadow-only detached; EX-2 intact; D3 gate intact; USD 50/mo ceiling intact |
| `prompt_reload(force=…)` (today's shape) | **Byte-identical** — pure additive surface |
| Daemon restart between arm and seam | Arm survives (JSONB); note fires at the next quiescent seam |

---

## Commission clause 4 — the owed rewording (architect's draft)

> **NO NEW DATA-LOSS SURFACE ON THE NOTE PATH:** on every doc-producing compaction, everything the engine already preserves — the verbatim tail, the hoisted injected notes, the envelope, the section summaries, the sentinel pre-write guard — is preserved unchanged; the note ADDS an authoritative layer and removes nothing that survives today. The pre-filter is the ONE separately-authorized exception: it may remove a candidate group only when the note's structured marker block mechanically accounts for that group (the seam-safety invariant), and only when the operator has armed it (default OFF).

---

## SCOPE DISCIPLINE — what this lane must NOT build

1. **No parallel engine** — `daemon/compaction.py` stays the single source for grouping, selection, ceiling, sentinel, persist, and render.
2. **No new agent** — the "dreamer" is a *mode* of the existing pipeline (ruling A), not a roster entry.
3. **No changes to the default compaction path** — byte-identical, proven by golden-parity; the only conditionals added to shared code (`note_tokens` in the ceiling numerator; the `_merge_summaries` entry guard) are skipped entirely when no note is present.
4. **No doctrine change** — EX-2, the `Monotonicity` enum, and the D3 ratification gate are untouched; the open question is recorded (§11), not decided.
5. **No new knobs beyond two** — `compact_note_max_chars` (default 12000) and `compact_note_prefilter_enabled` (default **OFF**, env-only override, deliberately absent from `config.yaml`). `CompactionConfig` is `extra="forbid"`: bare yaml keys are refused at boot; every knob is a class field in the aliased idiom. **The deliberate-compact rate limit is a HARDCODED MODULE CONSTANT (`DELIBERATE_COMPACT_RATE_LIMIT_S = 900`), NOT a knob** — per the commission's explicit permission. The future LLM occupant's budget/timeout are **specified in this document only** — not added as config now.
6. **No new LLM consumer this release** — the v1 pre-filter occupant is deterministic.
7. **No summarizer-prompt changes** — sections are generated exactly as today (§D ii).
8. **No audit table** — the INFO line is the audit surface, content-free.
9. **Doc deliverable owed by the implementer (named here, not mine to write):** `docs/guidelines/guideline-prompt-reload-compact-note.md` — README checklist item 3 (refusal codes carry contract meaning) and item 4 (`prompt-reload` is a PRIVILEGED category, #20 of `PRIVILEGED_TOOL_CATEGORIES`, `daemon/tools/_tool_registry.py:845`; the count pin `tests/unit/tools/test_prompt_reload_pins.py:71` is **unaffected** — no new category is created).
10. **Docs line (carries the design's soul — ship verbatim):** *"the working-context counterpart of the memoir dream lane — the dream consolidates her episodic memory; the compact note consolidates her working memory."*

---

## Open questions (recorded, not decided)

1. **The Emperor's doctrine question:** may the decision-engine overlay ever be a DECIDING surface (a P1 doctrine amendment)? This lane's design is inert to the answer: the pre-filter never touches the family. Surfaces on the Emperor's next waking, alongside EX-2 and the absence of a removal-admitting monotonicity direction.
2. **The pre-filter stage's right to exist:** if the Emperor's answer to (1) is "no deciding surface ever," delete the stage (§F top). If "yes," the designed slot takes the LLM occupant — or, if the Emperor insists on the literal decision-family route, EX-2 removal + a removal-admitting direction + a ratified `ReviewRecord` become a SEPARATE lane.
3. **W1's ambiguity, resolved by ruling (recorded for the implementer):** the "note" here is a **NEW agent-authored note** passed as a tool parameter — NOT the compaction-internal operator notes (`daemon/_content_hardening.py:54-73`, already hoisted/answerable) and NOT the project-scoped `critical_notes` table (leader-gated, project-scoped — wrong scope for a per-instance working-memory note).

---

## Divergences, drifts, and dispositions

| Source | Divergence | Disposition |
|---|---|---|
| Commission orientation | "seam = turn boundary, never mid-turn" | **Corrected** (§0.1) — 2 of 4 sites are mid-turn; the constraint is imposed on the tool-initiated path (§H) |
| W3 | Note rides the `global_overview` parameter | **Overruled** — 600-token silent truncation + one-of-three-branches (§D) |
| Council | Consumption at ANY doc-writing site | **Overruled** — quiescent-only (T3/T4); mid-turn sites stay arm-blind (§H, with flip condition) |
| Council D1 | Cap 4000/6000 | **Overruled** — 12,000 (the Emperor's stated range, midpoint; council's justification absorbed by the budget ruling) (§G) |
| Council R7 | "Clear the arm after a real write" | **Sharpened** — `replacement_messages` non-empty ∧ persist `True`; the bool alone cannot distinguish stamp-only (§D) |
| **Leader challenge + resident axis argument (v1.1)** | **v1.0 "arming does not lower the 80% gate" — note waits indefinitely** | **WITHDRAWN and REVISED** (§H) — internal inconsistency resolved; the axis argument (80% measures SIZE, the note measures RELEVANCE — the Emperor's use case is true at 30% fill as at 90%) makes v1.0 a category error, not a trade-off; forcing lives in the GATE with `force=False` (the §0.7 50%-tail trap makes `force=True` unsafe) |
| **Planner D25 (v1.2)** | **v1.1 merge-skip coverage cited two call sites; a THIRD exists (`:4187`, inside `_summarize_chunked`) and fires FIRST on full success** | **PLANNER RIGHT — v1.1 coverage was wrong on the primary path.** Ruled in §D.1: the skip's single home is the `_merge_summaries` **entry guard** (def `:4306`), covering all three sites and every future site by construction; render sharpened to a note-first gate (placeholder/merged text never co-renders; `previous_overview` line suppressed when a note is consumed) |
| **Planner D26 (v1.2)** | `:3769-3773` (my/W1's) vs `:3768-3773` (planner's) | **Planner right** — the call begins `:3768` (span `:3768-3772`; `:3773` is `if ok:`); site 1 likewise corrected `:3686-3690` → `:3687-3691`. Off-by-one drifts from W1, corrected throughout |
| Planner | REFUSE on second-note-while-armed | **OVERRULED by leader, ruling A** (§G) — the trap rationale recorded |
| Planner | Cap 8,000 | **SUPERSEDED by leader, ruling B** (§G) — 12,000 authoritative |
| W2/KV anchors | `transition_stage :300-315`, `is_deciding_permitted :326-332` | **Drifted — re-pinned** to `:355-387` and `:389-399` |
| W1 | `plan_ref` file absent | Correct — this document is that deliverable |
| PACKS | `:4572` claims 22 files; script runs 1 | **Routed follow-up** (§I) — not silently fixed |

**Confidence: HIGH.** The assumption that would flip the engine-shape ruling: if the note must later alter *selection semantics* (not just the GLOBAL layer + drop set), A and C converge. The assumption that would flip the consumption ruling: the Emperor preferring consumption-at-any-site. Both flips are one-predicate changes.

**Evidence instances:** W1 `f1251596-9fec-4fb8-8e35-ca1ddef97cad` (data-flow-design), W2 `fbe80e10-00b9-49b3-abba-031c20769c0d` (data-flow-design), W3 `f4ff527d-112b-461d-9d59-18382530c1a6` (structural-design), Council governor `e824ef5e-e407-4961-bfc0-e4923f7dde8b` (worker@agentic + worker@coding, trade-off-analysis).

---

## 13. Amendment-round changelog (v1.0 → v1.1 → v1.1.1 → v1.2, 2026-11-17)

1. **Consumption ruling REVISED** (leader challenge accepted): armed note FORCES note-guided compaction at the next quiescent seam; v1.0's "waits indefinitely" disclosed gap withdrawn as an internal inconsistency; forcing lives in the T3 gate with `force=False` — **new measured fact §0.7: `force=True` below the floor triggers `_last_effort_tail_truncation` (50%-tail truncation)**, which would violate clause 4.
2. **Rate limit added:** `DELIBERATE_COMPACT_RATE_LIMIT_S = 900` (hardcoded constant, no knob); typed arm-time refusal `prompt_reload_compact_rate_limited`; two honest deferred-state receipt wordings added (floor / anti-refire) — the resident's "deferred, not compacted" non-negotiable is now load-bearing (it CAN bind, 60s worst case).
3. **Resident input (1) folded:** the MERGE-FAILED branch is THE named case of the E2E acceptance artifact; the resident's line — "Surviving where the overview died is the feature's claim to exist" — quoted in §I.
4. **Resident input (2) folded:** the anti-refire resolution (gate-bypass + engine floor + honest deferral wording) in §H; the resident's trigger-class distinction made numeric (15× the automatic window).
5. **Resident input (3) folded:** the memoir-lane and decision-family shadow-lane content-free doctrines cited BY NAME in §H signal 3.
6. **Leader confirmation (A) recorded:** second-note REPLACE-with-disclosure stands; the planner's REFUSE overruled; the trap rationale recorded in §G.
7. **Leader confirmation (B) recorded:** cap 12,000 AUTHORITATIVE; the planner's 8,000 superseded (§G).
8. **Substitution prominence raised:** the ⚠ SUBSTITUTION DISCLOSURE now opens the document; the delete-the-stage instruction now opens §F; Decisions Pending §11.2 names it.
9. **New anchors measured (v1.1 round):** `daemon/config.py:1660` (`min_messages_before_compaction`, default 10), `daemon/constants.py:603`, `daemon/compaction.py:3357-3386` (the floor branch + the 50%-tail fall-through), `daemon/compaction.py:3368-3370` (`skipped_below_min_messages`).
10. **v1.1.1 — the resident's axis argument folded in (leader-adopted):** §H carries "The axis argument" as the decisive reasoning — the 80% gate measures SIZE, the note measures RELEVANCE; the Emperor's founding use case is true at 30% fill exactly as at 90%; threshold-gating a deliberate relevance request on a size predicate is a **category error**. Under the leader's defense constraint: **no surviving defense exists and none is offered** — the one concrete author-unintended cost (the §0.7 50%-tail trap) is neutralized by gate-bypass-with-`force=False`.
11. **v1.2 — D25 RULED (planner right; v1.1 coverage wrong on the primary path):** three live `_merge_summaries` call sites measured (`:3687-3691` outer re-merge under `if outcome.merge_failed:` `:3657`; `:3768-3772` partial path `:3649`; **`:4187-4191` inner merge inside `_summarize_chunked` def `:3922`, sole caller `:3624` — fires FIRST on full success, ≥2 summaries, and collapses `summaries=[merged]` so the outer sites never execute**). No structural unreachability — `_summarize_chunked` IS the section summarizer note-guided mode keeps. **Ruling (§D.1): the skip's single home is the `_merge_summaries` ENTRY GUARD (def `:4306`), first statement, returning `(None, False)`** — covers all three sites and every future site by construction; the D25 site-enumeration drift class is structurally impossible against it. **Render sharpened:** the note is the FIRST arm of the GLOBAL OVERVIEW branch (v1.1's additive `:1033` band would have co-rendered with the placeholder/merged text); the placeholder and the merged text never co-exist with the note; the `previous_overview:` line (`:996-997`) is suppressed when a note is consumed (else the prior note re-emits uncapped above the new note — unbounded cross-pass duplication). New D25 pin in §I (≥2 batches + note ⇒ no merge LLM ⇒ sections survive ⇒ note in GLOBAL region, no placeholder). **D26:** planner right — `:3768` (site-1 likewise `:3687`); W1-derived spans were off by one; corrected throughout.
