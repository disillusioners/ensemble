# Approach Comparison — Compaction Output Structure

Date: 2026-09-01
Base: `latest` @ 7394e716 + `feature/compaction-parallel-model` @ f0ee15e3 (worktree verified on f0ee15e3)
Workers: `architect-worker-tiered-multi` (data-flow-design), `architect-worker-compact-single` (data-flow-design), `architect-worker-structural-mechanics` (structural-design)

## Options

| Option | Shape | Carrier | New ids per compaction |
|---|---|---|---|
| **A+C — Tiered multi-message** (Worker 1) | `[injected][ENV][TOTAL][F1..Fk][MARKER][tail]` | 4+ SystemMessages | 4 id prefixes (`compaction-envelope/-total/-frag`, `truncation-marker-`) |
| **B — Merged-only** (Worker 2) | one ~2.5k global summary, no detail tier | 1 SystemMessage | 1 (`compaction-global-{iid}-{seq}`) |
| **D — Sectioned single document** (Worker 2) | one doc: envelope header + GLOBAL TOTAL + ARC sections + DROPPED list | 1 SystemMessage | 1 (`compaction-global-{iid}-{seq}`) |
| **E — Hybrid** (synthesis; the pick) | D-shaped single doc carrying A+C's tiered *content* (global-first, arc-local re-scoped sections, fail-open ladder, ceiling rule) + Worker 3's sentinel landing + same-id stability | 1 SystemMessage (+ boundary line folded in) | 1 (`compaction-global-{iid}-{seq}`) |

## Five-Axis Comparison

| Approach | Complexity | Scalability | Maintainability | Risk | Cost | Recommendation |
|---|---|---|---|---|---|---|
| A+C: Tiered multi-message | **Med-High** — 4 templates + 4 id prefixes to keep in sync across engine/tests/FE; merge ladder + truncation-total path | Med — k fragments grow linearly; 15%-window ceiling bounds it | Med — more contracts; mitigated by module-scope template helpers | Med — W6 improved (13→~9 trailing system blocks) but **not eliminated**; merge latency on degraded paths (bounded, fail-open) | Low-Med — +1 LLM call; net token **−3k to −4.5k** after fragment re-scoping | Right content economics, wrong carrier — the multi-block wire shape keeps a W6 residue and quadruples the id contract surface |
| B: Merged-only | **Low** — reuses `_merge_summaries` as-is | High — ~2.5k bounded, log-depth merge | High — one template | Med — **silent detail loss**; no recourse when the next call needs an 8-batch-old fact | Lowest — ~2.5k carried | **Reject as primary**: fails the user's explicit "then detail summaries" tier; keep as E's degrade path when doc exceeds hard cap |
| D: Sectioned single document | Med — section-schema template + provenance headers | Med — linear in batch count, ~30-40% below current via W5 overlap stripping | Med — schema is a contract rippling to tests + FE | **Low-Med** — W4+W6 fixed by construction; single stable id; 🔴 stable-id contract debt (UUID4 → `{iid}-{seq}`); 🟡 FE giant-row needs fold affordance | Med — ~9.8k carried, +1 LLM pass | Closest single option — **fold into E** |
| **E: Hybrid (PICK)** | **Med** — D's single-doc carrier + A's ladder/ceiling engine logic; seam transform is one shared helper | Med — ceiling rule (condense oldest sections first, never GLOBAL) + degrade-to-B fallback bound growth by policy | **Med** — ONE carrier message, ONE id pattern, ONE seam helper, reducer contract pinned by version-guarded tests | **Low-Med** — sentinel's loss-on-materialization (🔴) mitigated by mandatory pre-write assertion; ladder is fail-open at every rung | **Med-Low** — ~8-10k carried (−5 to −6.5k vs today's ~14.7k); +1 bounded LLM call per compaction | **RECOMMENDED** — see `architecture-recommendation.md` |

## Token Delta (per compaction, observed-instance basis)

| Bucket | Current | A+C | B | D | E |
|---|---|---|---|---|---|
| Detail bodies | 13.3k (12 frags, overlapping) | ~7-9k (re-scoped) | 0 | ~8-9k | ~7-9k (re-scoped) |
| Global tier | 0 | +0.5-2k | ~1.5-2.5k (is the whole output) | +0.6-1k | +0.6k (capped) |
| Wrappers/headers/markers | ~1.4k | ~0.6k (4 kinds) | ~0.1k | ~0.9k | ~0.5k (1 doc) |
| **Total** | **~14.7k** | **~10-11.5k** | **~2.5k** | **~9.8k** | **~8-10k** |

Ceiling rule (E): `GLOBAL + Σsections ≤ 15% of context window` → condense oldest sections first, never the GLOBAL; hard cap → degrade to B-shape (GLOBAL + archived-sections line).

## Comprehension (the deciding metric)

- **Reading order after the W1 fix** is identical for A+C, D, E: global frame → detail → verbatim recency. B lacks the detail tier.
- **Primacy economics** (Worker 1): the first blocks after system context set the interpretive frame; the tail conditions generation. A short GLOBAL (~600 tok cap, E/D) at the top of the compacted block rides the high-attention zone; today's 12 unlabeled fragments sit at the *end* — the weakest-attention position (W1's cognitive damage).
- **Single-block vs tiered** (Worker 2, D completion): attention decays deep into a 10k block — mitigations adopted in E: GLOBAL kept short, cross-references instructed in the summary prompt ("see SECTION 7 for the failing test case"), boundary line marking the verbatim transition.
- **Wire shape** (Worker 3): 13 consecutive trailing system blocks is the W6 pathology; a single consolidated system block (with the boundary line folded in) removes it entirely. A+C leaves ~9.

## Why E Wins (one paragraph)

E takes D's carrier because Worker 3's verified id-stability evidence (FE union-merge by `message_id`, `created_at` from first checkpoint appearance) makes ONE stable-id message strictly safer than N fresh-id messages — no in-session ghosts, no tail re-sorting, one FE row. E takes A+C's content economics because the user's goal is tiered comprehension (total first, details next, originals last), and the arc-local re-scoping plus fail-open ladder deliver it *inside* the single carrier without W6 residue. E takes Worker 3's landing mechanics because the sentinel recipe is the only verified order-control path in langgraph 1.0.9 — every alternative (per-id remove+re-add, two-step aupdate) is disproven by source. Confidence: **High**. Flip condition: the sentinel path misbehaving at runtime (source-verified, not yet executed) — the version-guarded reducer unit pins are the acceptance gate before merge.
