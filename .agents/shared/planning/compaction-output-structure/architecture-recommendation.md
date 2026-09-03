# Architecture Recommendation — Compaction Output Structure

Date: 2026-09-01
Status: **Complete** — pick is decisive; implementation-ready plan below
Base: `latest` @ 7394e716 + `feature/compaction-parallel-model` @ f0ee15e3 (must compose with both)
Instances: `architect-worker-tiered-multi` (ae729ee1), `architect-worker-compact-single` (802187f8), `architect-worker-structural-mechanics` (a69f8165)
Companion: `approach-comparison.md` (same directory)

---

## 1. Problem (verified, from checkpoint inspection + source)

Compaction persists a non-chronological, unlabeled transcript: `[7 injected][16 tail = NEWEST turn, upserted in place][12 batch summaries of the OLDEST arcs, appended][truncation marker LAST]`. Token split: injected 37% | tail 18% | 12 fragments 45% | marker 0.04% (29.5k from 418.7k).

- **W1 (root cause)**: the `add_messages` reducer (langgraph 1.0.9, `.venv/.../langgraph/graph/message.py`) upserts existing-id messages IN PLACE (`:232-235`), appends new-id messages at the end (`:241-243`). The intended `[summaries][marker][tail]` list order (`compaction.py:1627-1635` comment) never lands — at **all three** persist sites: on-demand `compact_executor.py:1560-1568` (no `as_node`), proactive `instance_messaging.py:1196-1207` (`as_node='agent'`), reactive `graph.py:3524-3528` (`as_node='agent'`).
- **W2** marker dangles last · **W3** generation-time `Timestamp:` stamps, no span/coverage labels (`compaction.py:1353-1356`) · **W4** ~54% of compactable span silently dropped — `failed_batches` exists at assembly (`:1275-1300`) but is unused · **W5** fragments overlap/restate global context · **W6** 13 consecutive trailing system blocks on the OpenAI-compatible wire.
- **Test gap that let W1 ship**: existing tests pin the *sent replacement list* (`test_compaction.py:417-441`, `:1530-1543`, `:1621-1666`; `test_compact_executor.py:1263-1298`), never the *landed channel order*. The real-reducer harness already exists (`_load_real_add_messages`, `test_compaction.py:2205`) but asserts marker count only.

## 2. Decision

**Adopt Option E — a single sectioned compaction document, landed via the `REMOVE_ALL_MESSAGES` sentinel recipe, with tiered content inside.**

Every compaction (proactive, reactive, on-demand; full, partial, truncation) persists **exactly ONE new SystemMessage** — id `compaction-global-{instance_id}-{seq}` — whose body reads top-down as: envelope header → GLOBAL OVERVIEW → SECTION DETAIL (provenance-labeled, arc-local) → boundary line. The preserved tail (SAME ids, verbatim) follows it; injected context keeps its head position. The truncation marker stops being a separate dangling message (W2) and becomes the boundary line inside the doc; W6 collapses from 13 trailing system blocks to exactly ONE system block mid-transcript.

This is the user's requested reading order — *big/whole summary first, details next, originals last* — on a carrier that is id-stable, ghost-free in the FE merge, and order-correct by verified reducer semantics.

## 3. Verified Foundations (the linchpin facts)

**Reducer semantics (langgraph 1.0.9, read from source — Worker 3):**
1. Existing-id input → upsert IN PLACE, position never changes.
2. New-id input → APPEND at channel end.
3. `RemoveMessage` of an ABSENT id → **ValueError**.
4. Same-call remove→re-add of id X → X resurrects IN PLACE (order unchanged) — *the leader's proposed per-id remove-all-then-re-add fix DOES NOT work*.
5. Same-call re-add→remove of X → X deleted (removals filter runs last).
6. **`REMOVE_ALL_MESSAGES = "__remove_all__"` sentinel** (`message.py:38`, detected `:209`): everything AFTER the first sentinel in the input list becomes the ENTIRE new channel value, **verbatim order** (`:223-224`). This is the only position-control path.
7. Two-step aupdate (remove pass, then add pass) opens a permanent-loss crash window on quiescent instances — rejected.

**Id stability (Worker 3):** FE `mergeMessagesById` (`message-merge.util.ts:88-95`) is union-by-id — same-id re-add is an idempotent upsert that KEEPS the earlier `created_at` (MIN-4, `:106-110`), which derives from first checkpoint appearance per id (`persistence.py:322-356`). Fresh ids per compaction would strand in-session ghost duplicates and re-sort the tail to the bottom of the FE view. → **Tail and injected MUST keep their original ids** (they do today; sentinel preserves this).

**Role precedent:** compaction summaries and LoopRepairer summaries are SystemMessages by explicit project decision (D9 — "system-level directive, NOT a user message", `graph.py:1720-1723`); synthetic system head is wire-only (`persistence.py:431-448`). No human-with-prefix anywhere. → All compaction output stays SystemMessage.

## 4. Output Contract — Message Format Spec (VERBATIM)

One SystemMessage per compaction:

```python
SystemMessage(
    id=f"compaction-global-{instance_id}-{seq}",   # seq = (max seq parsed from prior
                                                    # compaction-global-{iid}-* ids in the
                                                    # pre-compaction snapshot) + 1
    content=(
        "[CONTEXT COMPACTION — mode={summary|partial_summary|truncation} "
        "| compacted_at={generation_time_iso} "
        "| summarized messages #{start}–#{end} → global overview + {k}/{n} sections "
        "| dropped without summary: {NONE | messages #{a}–#{b}, #{c}–#{d} — content not recoverable} "
        "| preserved verbatim: {m} most recent messages (below this notice) "
        "| self_id=compaction-global-{instance_id}-{seq}]\n"
        "\n"
        "── GLOBAL OVERVIEW ──\n"
        "{global_summary_text}"
        #  ^ capped ~600 tok; on merge-pass failure replaced by:
        #  "(overview unavailable — merge pass failed; the sections below are authoritative)"
        #  truncation mode: this section is omitted unless the bounded best-effort
        #  overview succeeded (see §6.3)
        "\n\n── SECTION DETAIL ──\n"
        #  repeated per succeeded batch, batch order:
        "### SECTION {i}/{n} — messages #{s}–#{e} | conversation time {t0_iso} → {t1_iso}\n"
        "{arc_local_summary_text}\n\n"
        #  ^ conversation-time clause OMITTED (never generation-time) when the
        #    first-appearance map has no rows for the boundary ids
        #  ceiling-condense fired:
        "── ARCHIVED: {j} oldest sections condensed for budget; global overview above is authoritative ──\n"
        "\n"
        "── END OF COMPACTED CONTEXT — everything below is the verbatim recent transcript ──"
    ),
)
```

Rules:
- Conditional clauses are omitted, never falsified (coverage clause only when `k < n`; dropped-span list only when non-empty — this is the W4 fix: dropped spans become explicit).
- Exactly ONE generation timestamp, in the envelope header (structural `compacted_at` channel already exists, `graph.py:2435`). Per-section times are CONVERSATION times sourced from the first-appearance checkpoint map — the W3 fix.
- Prompts for section bodies are re-scoped to **arc-local detail only** (batch decisions, tool outcomes, quotes); global context lives once, in GLOBAL OVERVIEW — the W5 fix (estimated 30-50% body shrink).
- The summary prompt instructs cross-references ("see SECTION 7 for the failing test case") so the model can re-anchor from the short GLOBAL into sections.
- Failed/old-generation compaction docs inside a new compactable span are removed with the span (same write), and the prior doc's GLOBAL OVERVIEW is passed to the new merge as seed ("Previous overview: …") — the global frame converges across passes instead of being re-derived; prior sections collapse to an ARCHIVED line only when budget requires.

## 5. Persist-Seam Change (all three sites)

Shared helper (place in `daemon/compaction.py`, exported; consumed by all sites):

```python
def build_sentinel_replacement(result: CompactionResult,
                               current_messages: list[BaseMessage]) -> list:
    """W1 fix: land the intended order verbatim via the REMOVE_ALL sentinel.

    1. PRE-WRITE GUARD (mandatory, 🔴 mitigation): the injected + preserved-tail
       message ids and counts in result.replacement_messages must EXACTLY match
       current_messages minus the compacted span. On mismatch: raise
       CompactionAborted — compaction fails open, checkpoint untouched.
    2. Desired final order (tail keeps ORIGINAL ids, full message objects):
       [injected…][compaction doc][tail…]
    3. Return [RemoveMessage(id=REMOVE_ALL_MESSAGES), *injected, *doc, *tail]
       — sentinel MUST be element 0 (anything before it is discarded).
       No per-id RemoveMessages are sent (eliminates the
       ValueError-on-absent-id class entirely).
    """
```

Call sites:
- `daemon/services/compact_executor.py:1560-1571` — first `aupdate_state` sends the sentinel list (no `as_node`, unchanged); second `aupdate_state` (`compacted_at`) unchanged.
- `daemon/services/instance_messaging.py:1196-1207` (proactive) and `daemon/graph.py:3524-3528` (reactive) — same helper, keep their `as_node='agent'`.
- Atomicity: one checkpoint write for messages — **same as today**; crash-after-write-1 leaves messages compacted with stale `compacted_at` → later dedup re-compact is idempotent under sentinel + same ids.

## 6. Engine Changes (`daemon/compaction.py`)

1. **Consolidated doc builder** replaces the three per-emit SystemMessage sites (`:1355-1358`, `:1393-1396`, `:1427-1430`) and `_build_partial_replacement_messages` (`:1577-1660`) emits the single doc + tail (no per-batch messages, no separate marker).
2. **Generalize `_merge_summaries` to partial sets** (today full-success-only, `:1273-1286`; partial refuses, `:1290-1295`): merge over succeeded fragments only; coverage + dropped clauses come from `failed_batches` (already available, `:1275-1300`). Merge call gets an **independent budget** `min(inner_cap, 25% of remaining compaction deadline)`, excluded from the batch-pool deadline, one retry max. Failure ladder (fail-open, never deepens partiality):
   - merge OK → doc with GLOBAL
   - merge fail/timeout → doc WITHOUT GLOBAL (placeholder line), sections intact, `total_summary_status='failed'`, `compaction_type` unchanged
3. **Truncation fallback** (`|S|=0`): bounded best-effort GLOBAL — single LLM call, ~20s own cap, sampled input capped ~30-40k chars, only if `tokens_before ≥ ~2k`; failure → envelope + dropped spans only (today's marker semantics, now labeled). *(Decision pending §12.3 — leader may veto to keep truncation zero-LLM.)*
4. **Ceiling rule**: `GLOBAL + Σsections ≤ 15% of context window`; breach → condense OLDEST sections first, never the GLOBAL; hard cap → degrade to GLOBAL + ARCHIVED line (B-shape).
5. **Conversation-time ranges**: arc boundary message ids → first-appearance checkpoint `ts` map (same logic as `persistence.py:322-356`); missing rows → omit the clause. Window cap `limit=1000` checkpoints is a known bound (§13).
6. **Emergency truncation path** (`compaction.py:866-913`) unchanged.
7. **Composes with f0ee15e3**: the merge pass uses the same optional-compaction-model selection as chunk summarization; parallel-chunk partial sets feed §6.2 directly.

## 7. Token Budget

Observed-instance projection: header ~80 tok + GLOBAL ~600 (capped) + sections ~7-9k (re-scoped from 13.3k) + boundary ~30 ≈ **8-10k vs today's ~14.7k → net −5 to −6.5k per compaction**, plus one bounded merge call (~13k in / ~1.5k out, once). Worst case (no shrink materializes): +0.6k on 29.5k = +2%. Ceiling rule bounds growth as transcripts scale.

## 8. Comprehension Rationale

Post-fix wire reading order: system prompt → injected context → **[GLOBAL OVERVIEW — frame: entities, goals, decisions]** → labeled sections (detail of a known whole; overlap now visibly redundant, safely stripped) → boundary line → verbatim newest turn adjacent to the generation point. This exploits primacy for the frame and recency for generation — today's defect spends 45% of context at the weakest-attention position (channel end) with no frame at all. The stable GLOBAL also converges across re-compactions (merge seed), so the frame is maintained rather than re-derived every pass.

## 9. Frontend Changes

- `chat-interface.component.ts` (verified: NO `compaction-*` special-case today, `:320-347`): render `compaction-global-` id prefix as a **fold-with-preview card** — ≤500-char preview drawn from GLOBAL OVERVIEW + "Show compacted context" expander. Ships in the same PR as the BE change (🟡 Worker 2: needed before single-big-doc lands).
- `/compact` card copy: success → "Context compacted — global overview + N section summaries preserved"; partial keeps `timed_out`/`fallback_applied` phases with copy "(k/N sections kept; dropped spans listed in the compaction notice)". `compacted_type` values and the 7-state phase machine unchanged (no `models/index.ts` change).
- FE merge: one stable id per compaction → union-merge upserts cleanly, earlier `created_at` preserved, no ghosts, tail never re-sorted.

## 10. Test Plan

1. **Order-pinning real-graph test (the W1 killer)** — `tests/unit/test_compaction.py`, new class; real `StateGraph(SessionState)` + file-backed SQLite `tmp_path` (pattern: `test_compact_executor_revive_brick_e2e.py:136-156`; NO StaticPool). Seed injected + old arc (A1..A20) + tail (T1..T5) with explicit ids; run compaction (stubbed LLM, 12-batch partial fixture); apply the seam's sentinel list; `aget_state` read-back; assert landed order element-by-element: injected ids at head → `compaction-global-{iid}-{seq}` → tail ids in original order at END; assert NO `compaction-`/`truncation-marker-` id after the first tail id; assert read-back `created_at` (persistence path) preserves original tail timestamps.
2. **Reducer-semantics unit pins** (version-guarded on langgraph 1.0.9, direct `add_messages` import): upsert-in-place; new-id append; remove-absent-id raises; same-call remove→re-add = in-place; same-call re-add→remove = deleted; sentinel truncates to `right[idx+1:]` discarding the prefix. *(These convert Worker 3's source-reading into executed proof — the acceptance gate for this design.)*
3. **Pre-write guard**: id/count mismatch between replacement and snapshot → `CompactionAborted`, checkpoint byte-identical.
4. **Ladder**: merge timeout → no GLOBAL, sections intact, `total_summary_status='failed'`, `compaction_type` unchanged; truncation±bounded-total both shapes.
5. **Ceiling rule**: over-cap doc → oldest sections condensed, GLOBAL preserved; hard cap → B-shape degrade.
6. **Provenance/W3**: section headers carry `SECTION i/n`, span indices, conversation-time range or omitted clause; assert NO generation-time `Timestamp:` leak anywhere except the envelope header.
7. **Pass-2** (extend `TestChainedSecondCompactionMarkers`, `test_compaction.py:2230+`): prior doc removed with span; new merge prompt contains "Previous overview:"; seq increments; exactly one `compaction-global-` id survives.
8. **Parametrized across persist sites**: on-demand (no `as_node`), proactive, reactive → identical landed order.
9. **FE spec**: `mergeMessagesById`/`upsertMessage` — re-delivered same-id doc upserts without duplication keeping earlier `created_at`; fold-card renders for `compaction-global-` prefix.
10. Reactive-path `_ensure_tool_result_pairing` unaffected (doc is a SystemMessage, no tool_calls).

## 11. Risks

- 🔴 **Loss-on-materialization under the sentinel**: anything not re-supplied is gone from state (checkpoint history retains it; no auto-recovery). Mandatory pre-write assertion (§5.1) + order-pinning test (§10.1). This is the one landmine of the design — it is guarded, not assumed away.
- 🟡 Merge-pass latency on timeout-degraded paths — independent cap, one retry, skip when remaining budget < 20%, fail-open ladder.
- 🟡 FE giant-row rendering — fold affordance must ship with the BE change.
- 🟡 Conversation-time map window (`limit=1000` checkpoints) — clause-omission fallback; measure coverage on a long-lived prod instance before relying on time ranges.
- 🟢 Strict providers: exactly ONE mid-transcript system block remains (down from 13); if any provider still objects, the documented alternative is folding the boundary line into the preceding section (already the default) — no second block exists to remove.
- 🟢 `seq` derived by parsing prior ids from the snapshot — no schema change; wrong seq worst-case is an id collision that the union-merge dedupes.

## 12. Decisions Pending (leader)

1. **Marker carrier**: boundary line inside the doc (recommended — 1 system block total) vs separate tiny marker SystemMessage (2 blocks; closer to today's shape).
2. **FE fold affordance timing**: same PR (recommended) vs immediate follow-up.
3. **Truncation best-effort GLOBAL** (§6.3): attempt a bounded overview on the zero-summaries path (recommended; strictly-bounded, absent-on-failure) vs keep truncation zero-LLM (marker-equivalent envelope only).

## 13. Open Questions

- `msg_timestamps` window coverage on very long instances (not measured).
- Per-provider strictness on a single mid-transcript system block (residual W6 risk assessed low; untested against specific endpoints).
- `partial_summaries` type composition depth for the stable-id rewrite (Worker 2 unverified item) — confirm during implementation.

## 14. Gaps

None — all three fan-out nodes completed with skill confirmation and file:line evidence. One mid-delivery truncation (Worker 2) was remedied by a continuation dispatch; its seam-contract item was superseded by Worker 3's source-verified reducer findings (recorded here, not silently dropped).
