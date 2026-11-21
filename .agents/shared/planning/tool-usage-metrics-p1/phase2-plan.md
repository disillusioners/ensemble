# Phase 2: Central Metrics Family — F24 (aggregate tool-usage signal)

Lane: **tool-usage-metrics-p1** (commission ENSEMBLE-37 / TokenEconomizer adoption — Phase 1 measurement slice)
Phase owner: plan-worker (central-metrics-family phase)
Status: Draft for the planner's review
Consumes (ZERO edits): Phase 1's frozen ledger `tool_usage_daily` + its observer sink interface.
Worktree (READ-ONLY source of truth for every anchor below): `/home/nea/ensemble-src-wt-tool-metrics-p1` @ `0b5624dc9` (branch `feature/tool-usage-metrics-p1`).
Framing: `.agents/shared/planning/tokeneconomizer-study/writing-block-tiering.md` §3–§4 + `.agents/shared/writing-block.md` (the layer-2 dispatcher tier; ranking key = schema token weight × call frequency).

---

## 0. Dispatch mode & file ownership

**Mode 1 — Greenfield family registration.** No prior F24 exists. This phase adds ONE family through the WIRING registrar and amends the adjudication pack + catalog.

**Single-ownership invariant (REJECT-grade).** Phase 2 owns EXACTLY these edits:

| Owned file | Edit |
|---|---|
| `daemon/services/metrics_query/wiring/w5.py` | **NEW** — the F24 registration module + family gate |
| `daemon/services/metrics_query/wiring/__init__.py` | import + `register_w5_adapters()` call lines |
| `daemon/services/metrics_query/wiring/adjudication.py` | verdicts row + `REGISTERED_FAMILIES` + `FAMILY_SERIES_BANDS` entry + docstring count |
| `tests/unit/metrics/test_metrics_family_adjudication.py` | count pins + new F24 assertions |
| `test/packs/tool_usage_family_unit_test.sh` (**NEW**) + `.agents/tester/PACKS.md` | family pack + registration row |
| `.agents/shared/planning/metrics-collection/metric-catalog.md` | §1 / §8 / §11 / §14 amendments |

**Owns ZERO edits to:** `daemon/graph.py`, the ledger repo, the routers, `daemon/checkpoint_metrics.py` (ruled NOT needed — see §3), `frontend/**`, `daemon/services/settings_service.py`, `daemon/config.py`. The `daemon/services/metrics_query/wiring/` root is EXCLUDED from the adjudication pack's daemon-diff offender check (`WIRING_ROOT`, `test_metrics_family_adjudication.py:391/:546`), so the new `w5.py` cannot create a new offender.

---

## 1. Objective

Publish ONE aggregate-only central metric family that records tool calls by **closed** `tool_category` × `result` — enough for the exposition surface to show *that* tool usage is happening and *how it is trending*, while per-tool/per-project detail stays in the Phase-1 ledger + `/api/tool-usage/*` domain door. Outcome test: a Prometheus scrape shows a bounded `ensemble_tool_usage_calls_total` series set whose cardinality is provably under the declared band and the global 2000 cap, gated by an independent per-family kill-switch that stacks under the two global metrics gates, with the adjudication pack GREEN after its count amendments.

---

## 2. Scope

### In scope
- Register family **F24** (verified free — see §10 A1) via `register_family_metric` ONLY (`registry.py:74`), never raw `cm.register_*`.
- One counter: `ensemble_tool_usage_calls_total{tool_category, result}` — name matches `^ensemble_[a-z0-9_]+$` (`checkpoint_metrics.py:110`).
- Labels: `tool_category` (EXISTING closed vocabulary, reused as-is) + `result ∈ {ok, error}` (closed 2-set). NO project / agent / tool-name labels (B-2 precedent, `.agents/shared/planning/decision-module/decisions.md:58`).
- Independent per-family collection kill-switch, stacking UNDER the global gates.
- Adjudication-pack + catalog amendments; family pack test.

### Out of scope
- Per-tool / per-project series — belongs to the ledger + domain door (B-2; `decisions.md:58`: per-entity attribution must NOT be a metric label — unbounded series vs cap 2000).
- Any edit to Phase-1 files (ledger, observer, tap) — consumed as a frozen interface (§9).
- `roster.py` — F24 is **stat-primary** (current value only), not chart-primary; the FE tab reads the domain door, not central families. `roster.py:6-30`: "a family NOT in this roster is stat-primary — the FR-C2 default." No roster edit.
- Vocabulary amendment — ruled NOT needed (§3).
- The digest tool / scheduler / seeder — Phase 3.

---

## 3. FROZEN CONTRACTS (interfaces / invariants / bounds — NEVER node counts)

1. **Registration path.** `register_family_metric` is the ONLY registration path (`registry.py:74`). It structurally enforces AM-2 (every declared label key has a non-empty `allowed_values`, else `ValueError` at `registry.py:102-114`) and AM-3 (`source_idiom` / `durability` / `cardinality_weight` have NO defaults). `source_idiom="registry"`, `durability="process"`, `cardinality_weight` an int — these are the only values the pack accepts (`test_metrics_family_adjudication.py:488-492`).
2. **Name rule.** `^ensemble_[a-z0-9_]+$` (`checkpoint_metrics.py:110`); family id `F24` is free-form and stays `F24`.
3. **Closed label spaces.** `tool_category` = the frozen set from `tool_category_vocabulary()` (`checkpoint_metrics.py:439`), which returns the committed artifact's 66 categories + reserved `other` = **67 values** (`.agents/shared/metrics/tool-categories.json`, `count:66`, `reserved:"other"`, `vocabulary_version:11`; `checkpoint_metrics.py:418`, `:450-463`). `result` = exactly `{"ok","error"}`. The sink maps the observer's `ok: bool` → `"ok"`/`"error"`.
4. **No new tool-category values.** The observer's `tool_category?` comes from the same closed registry vocabulary; any out-of-set value folds to reserved `other` (`checkpoint_metrics.py:439-463`, catalog §8.6). Therefore **NO `VOCABULARY_VERSION` bump** (stays 11, `checkpoint_metrics.py:290`), **NO artifact regen**, **NO amendment test run** — UNLESS task T2's verification finds a genuinely new category value, in which case run the FULL procedure: bump 11→12 + regenerate `.agents/shared/metrics/tool-categories.json` + run both `tests/unit/tools/test_tool_category_artifact.py` and `tests/unit/metrics/test_metrics_vocabulary_amendment.py`, AND edit `checkpoint_metrics.py` (the ONE conditional exception to Phase 2's zero-edits list).
5. **Cardinality bound.** Closed space of the single counter = 67 × 2 = **134 series**. Declared band `FAMILY_SERIES_BANDS["F24"] = 160` = 134 + **26 explicit headroom** (= 13 new categories × 2 results; the vocabulary has grown ~7 times historically, `checkpoint_metrics.py:279-289`). Never zero-headroom (F23's 67 and `decision_overlay`'s 411 are zero-headroom BY DESIGN and must not be copied — `adjudication.py:175-212`). Global cumulative: `BASELINE_SERIES=89` (`adjudication.py:215`) + Σ measured ≤ `GLOBAL_SERIES_CAP=2000` (`adjudication.py:253`); the MEASURED closed-space total is 491 today, +134 = **625 ≤ 2000** (this is THE claim) (approver iteration-001 B5: the U16b +1 correction was wrong — 492 derivable by no convention (pack helper 402+89=491; enforced cumulative 89+813=902); reverted to the architect's measured 491/625, which stands as written in architecture-recommendation.md §5). The band-sum (Σ bands 846 + F24's 160 + baseline 89 ≈ 1 095) may be cited separately but must be labeled as a band-sum — mixing conventions misleads future cardinality gates.

*Amended by architect pass @ fd254b335 — Correction 1 (global total convention).*
6. **Kill-switch stacking.** Family collects IFF (global metrics gate ON) AND (family gate ON). Global gates: env `ENSEMBLE_METRICS_ENABLED` (fail-closed: explicitly set + not truthy = OFF, `metrics_gate.py:104/:111/:104-114`) + managed `limits.metrics_enabled` (`metrics_gate.py:111`). Family gate = env `ENSEMBLE_METRICS_TOOL_USAGE_ENABLED` (same fail-closed polarity) THEN managed `limits.metrics_tool_usage_enabled` read via `metrics_gate.live_managed_value(key, default)` (`metrics_gate.py:~240`, PURE cached read — no DB on request path). Derivation chain: gate pattern lives in `metrics_gate.py` (env TRUTHY polarity + `live_managed_value` read) — `metrics_gate.py:104/:111` (global gates) and `metrics_gate.py:~240` (cached managed read, no DB on request path); `kill.py` is the decision-module kill store and is NOT the metrics gate. The family can be OFF while the ledger runs and vice versa (Phase 1 owns its own collection key).

Managed-key placement (architect ruling): registration of `limits.metrics_tool_usage_enabled` MOVES to Phase 1's `MANAGED_KEYS` edit in `settings_service.py` (Phase 2 keeps its ZERO-edit stance on that file). `config_path=None` (DB/ENV-ONLY), default ON. Phase 2 remains the semantic owner of the key's READ path (`live_managed_value`). The env var `ENSEMBLE_METRICS_TOOL_USAGE_ENABLED` stays a complete fail-closed off-switch on its own.

*Amended by architect pass @ fd254b335 — Correction 2 (citation) + managed-key placement ruling.*
7. **Observer interface consumed, not edited.** Phase 1 exposes in-process sinks receiving `(tool_name, tool_category?, agent_id, project_id, ok: bool)` with NO argument payload by construction. F24 registers one sink; it uses only `tool_category` and `ok`, discarding the rest (aggregate-only).

### 3.7 Gauge question — REJECTED for Phase 1 (review fix U16a: heading renamed — the block's heading previously read like a generic anchors block)

*Amended by architect pass @ fd254b335 — confirm-and-keep verifications + Gauge rejection.*

- Vocabulary: 66 committed categories + reserved `other` = **67 values, version 11** (`tool-categories.json`, `checkpoint_metrics.py:439-463`); no vocab bump needed (§3.4).
- Band: 134 ≤ 160 with **26 explicit headroom**; never zero-headroom (F23/decision_overlay are zero-headroom by design and must not be copied).
- Name rule `^ensemble_[a-z0-9_]+$` (`checkpoint_metrics.py:110`).
- AM-2 enforcement: every declared label key has non-empty `allowed_values` (`registry.py:102-114`).
- Gate polarity: env explicit-not-truthy = OFF; managed read via `metrics_gate.live_managed_value` (`metrics_gate.py`).
- **Gauge (distinct-tools-observed): REJECTED for Phase 1** — scope-creep (second series family, another vocabulary surface, near-zero decision value; the ledger answers distinctness exactly). Do NOT add a gauge series in this phase.

Test additions (architect §10.5 phase-2 rows): band pin 134≤160; `VOCABULARY_VERSION==11`; measured-total 625 ≤ 2000 as the pin convention (approver fix B5). These fold into T6/§8.

---


## 4. Tasks (file-level claims + per-task verification)

| # | Task | Owns | Depends On | Acceptance / verification |
|---|---|---|---|---|
| T1 | Create `wiring/w5.py`: constants `FAMILY_ID="F24"`, `CLUSTER="W6"`, `VERDICT="IN"`, metric name, gate key/env constants; `register_w5_adapters()` calling `register_family_metric` for the single counter with `allowed_values` for both keys; a sink callback registered against Phase 1's observer that increments only when the family gate is ON. | `wiring/w5.py` | none | `pytest tests/unit/metrics/test_metrics_family_adjudication.py` sees F24 registered; `register_family_metric` raises on a missing `allowed_values` leg (AM-2 mutation stays RED). |
| T2 | Verify `tool_category` needs no new values: enumerate the observer's emitted categories against `tool_category_vocabulary()`; confirm all fold into the 67-value closed set. | verification only (no edit unless a new value is found) | T1 | If no new value: `VOCABULARY_VERSION` still 11, artifact unchanged, both amendment tests GREEN. If a new value: execute the full §3.4 procedure and record the exception in the plan's ledger. |
| T3 | Wire the family gate: env-explicit fail-closed check + `metrics_gate.live_managed_value("limits.metrics_tool_usage_enabled", True)`; AND-combine with global gates; sink increments only when ON. | `wiring/w5.py` | T1 | Unit test: family OFF with env explicitly `0`; family ON when env unset + managed default; global OFF suppresses regardless of family key; no DB touched on the read path. |
| T4 | Add import + `register_w5_adapters()` to `wiring/__init__.py` beside the existing W1–W4 registrations (the "module import IS registration" contract, `__init__.py:23/:58-61`). | `wiring/__init__.py` | T1 | Importing `daemon.services.metrics_query.wiring` registers F24; door discovers it from AM-3 metadata with no door edit. |
| T5 | Amend `adjudication.py`: add `"F24": ("IN", <w5.py anchor>)` to `CATALOG_FAMILY_VERDICTS` (`:59-99`), append `"F24"` to `REGISTERED_FAMILIES` (`:104-123`), add `"F24": 160` to `FAMILY_SERIES_BANDS` (`:175-212`), update the "12 full + 6 partial = 18 of 28" docstring (`:48`) to "13 + 6 = 19 of 29". | `adjudication.py` | T1 | `len(verdicts)==29`; binding subset 19-of-29; every registered family carries a band. |
| T6 | Amend `test_metrics_family_adjudication.py`: `len(verdicts)` 28→29 (`:417`); expected set `range(1,24)`→`range(1,25)` (`:418-422`); rename `test_the_binding_subset_is_eighteen_of_twenty_eight`→`..._nineteen_of_twenty_nine`, `len(full)` 12→13 (`:428-434`); add F24 assertions (name rule, both `allowed_values`, source_idiom/durability/cardinality_weight, band 134≤160, global cap, measured-total 625 ≤ 2000 pin convention) (approver fix B5). Confirm `VOCABULARY_VERSION==11` pin (`:870`) unchanged. | `tests/unit/metrics/test_metrics_family_adjudication.py` | T5 | Full pack GREEN; daemon-diff pin stay GREEN (w5.py under `WIRING_ROOT` is excluded). |
| T7 | Add family pack `test/packs/tool_usage_family_unit_test.sh` (pattern: `decision_module_p1_unit_test.sh`) covering T1/T3/T6, and register it in `.agents/tester/PACKS.md`. | pack script + PACKS.md | T1–T6 | Pack runs GREEN in the worktree; PACKS.md row records base SHA + date. |
| T8 | Amend catalog §1 (F24 family row), §8 (result 2-set + tool_category reuse ref), §11 (one C-series correction row for the new family), §14 (family roster count 28→29 + series totals). Keep `BASELINE_SERIES=89` semantics: F24 is a MEASURED family, not baseline. | `metric-catalog.md` | T5 | One canonical number everywhere (§1/§7/§14 cross-check); body matches ledger per POK `plan-predicate-ledger-01-attestation`. |

**Ordering:** T1 → T2/T3/T4 (parallel) → T5 → T6 → T7/T8 (parallel). T2's outcome decides whether `checkpoint_metrics.py` is touched.

---

## 5. Coupling

- **Tight with Phase 1** — consumes the frozen observer sink interface + ledger schema (§9). No Phase-1 edits.
- **Tight with Phase 3** — Phase 3's digest reads the SAME ledger; the family and the ledger must agree on what a "tool call" is (the observer's `ok` → `result` mapping). Coupling is via the frozen interface, not shared data structures.
- **Loose with the FE phase (Phase 4)** — the FE tab reads the domain door, not central families; no shared contract.
- **Independent of the scheduler/seed** — no overlap.

---

## 6. Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| R1 | A genuinely new tool-category value appears → family label space grows past the band, or VOCABULARY_VERSION drift REDs the pack. | High | Low | T2 verification gate + full amendment procedure (§3.4); band carries 26-series headroom. |
| R2 | Per-family managed key read via `live_managed_value` operator-writability depends on the `MANAGED_KEYS` row. | Medium | Medium | Per the architect placement ruling, registration MOVES to Phase 1's `MANAGED_KEYS` edit (`config_path=None`, DB/ENV-ONLY, default ON) — Phase 2 keeps its zero-edit stance; Phase 2 remains semantic owner of the READ path. Env var is a complete off-switch on its own. |

*Amended by architect pass @ fd254b335 — managed-key placement ruling.*
| R3 | `register_family_metric` call omits an AM-3 kwarg or an `allowed_values` leg → registration raises. | Medium | Low | Copy the `decision/metrics.py:219-228` `_counter` helper shape verbatim; AM-2/AM-3 mutations stay RED. |
| R4 | Catalog §14 interlocking totals (582/653/89) amended inconsistently. | Medium | Medium | T8 cross-check + POK `plan-predicate-ledger-01-attestation`; keep `BASELINE_SERIES=89` untouched. |
| R5 | Cluster label collision with `decision_overlay`'s `CLUSTER="W5"` (`decision/metrics.py:131`). | Low | Medium | Use `CLUSTER="W6"` — the FILE slot is `w5.py` (free), the CLUSTER label is W6 (W5 taken). Cluster is metadata-only, not uniqueness-enforced, but keep labels distinct. |

---

## 7. Success criteria

| # | Criterion | How to measure | Threshold |
|---|---|---|---|
| S1 | F24 registered via WIRING registrar only | `registered_families()` contains `F24`; no raw `cm.register_*` in w5.py | exact |
| S2 | AM-2/AM-3 structural | pack mutation tests RED; registration carries all four kwargs + both `allowed_values` | exact |
| S3 | Cardinality bounded | `_closed_label_space(F24 metrics)` ≤ band; global total ≤ cap | 134 ≤ 160; measured total 625 ≤ 2000 (approver fix B5) |
| S4 | Kill-switch independent + fail-closed | unit matrix (T3) | 4/4 cases pass |
| S5 | Adjudication pack GREEN | `pytest tests/unit/metrics/test_metrics_family_adjudication.py` | all pass |
| S6 | Vocabulary unchanged | `VOCABULARY_VERSION==11`, artifact drift test GREEN | exact (unless T2 exception) |
| S7 | Family pack registered + GREEN | PACKS.md row + pack run | pass |

---

## 8. Test strategy

- **Unit:** `test_metrics_family_adjudication.py` (amended) — census, verdicts, bands, global cap, measured-total 625 ≤ 2000 pin (convention per Correction 1) (approver fix B5), `VOCABULARY_VERSION==11`, AM-2/AM-3, daemon-diff (w5.py excluded via `WIRING_ROOT`).
- **Gate matrix (T3):** env explicit OFF / env unset+managed default / global OFF / family OFF-while-ledger-runs.
- **Mutation:** AM-2 drop `allowed_values` leg → registration `ValueError`; AM-3 drop kwarg → `TypeError`; wrong name → name-rule RED.
- **Pack:** `test/packs/tool_usage_family_unit_test.sh` — single entry point for the family's tests, registered in PACKS.md.
- **No live contact** — all tests run against the worktree; no daemon restart, no prod/env writes.

---

## 9. Cross-phase interface notes (for the architect — do NOT plan edits into these files)

1. **Observer sink registration symbol (Phase 1).** Phase 1 must expose a module-level `register_tool_usage_sink(sink)` (or equivalent) that w5.py imports. The sink signature is frozen: `(tool_name, tool_category?, agent_id, project_id, ok: bool)`. If Phase 1's registration is construction-time only (not a module-level registry), the architect must rule the hook site; Phase 2's w5.py consumes whatever Phase 1 exports with ZERO Phase-1 edits.
2. **Managed-key writability (settings/config).** `limits.metrics_tool_usage_enabled` is read via the cached managed-values mapping (no registration needed for READS). Per the architect placement ruling, REGISTRATION of this key MOVES to Phase 1's `MANAGED_KEYS` edit in `settings_service.py` (`config_path=None`, DB/ENV-ONLY, default ON) — Phase 2 keeps its ZERO-edit stance on that file. Phase 2 remains the semantic owner of the key's READ path (`live_managed_value`). Making it operator-writable via `PUT /api/settings/daemon/{key}` therefore needs only the `MANAGED_KEYS` row (landed by Phase 1) — no `LimitsConfig` attribute and no `config.py` edit for the family key (A-7 style: `config_path=None`). The env var `ENSEMBLE_METRICS_TOOL_USAGE_ENABLED` stays a complete fail-closed off-switch on its own. `limits.tool_usage_collection_enabled` (Phase 1's own key) is registered in the same edit; its `config_path` shape follows A-7 (add the field or use `config_path=None` — do not ship a dotted path with no field).

*Amended by architect pass @ fd254b335 — managed-key placement ruling (§5).*
3. **`tool_category` value set (Phase 1).** If Phase 1's tap can emit a category outside the committed 67-value set, that is a new value → §3.4 full amendment. T2 verifies; the architect is notified only on a positive finding.

---

## 10. Assumptions

- **A1 (F24 is free).** Verified: no `F24` anywhere in `wiring/` or the catalog; highest numeric family is F23 (`adjudication.py:175-212`, `CATALOG_FAMILY_VERDICTS` covers F0.1–F23 + `decision_overlay`); catalog §1 roster ends at F22 + F7b/F7c. F24 is the next free id.
- **A2 (tool_category = 67 values).** Artifact `count:66` + reserved `other` (`tool-categories.json`); `tool_category_vocabulary()` returns the union (`checkpoint_metrics.py:450-463`). Verify at T2.
- **A3 (baseline headroom).** `BASELINE_SERIES=89` is F7c-inclusive and unchanged by adding a measured family (`adjudication.py:215`).
- **A4 (global gate default OFF is acceptable).** `limits.metrics_enabled` defaults False (`settings_service.py:518-525`); the family gate's managed default True only matters once the global gate is ON.

---

## 11. Phase exit gate

Phase 2 is DONE when: (1) F24 is registered via `w5.py` + `__init__.py`; (2) `adjudication.py` + `test_metrics_family_adjudication.py` amended with all counts GREEN; (3) family pack registered + GREEN; (4) catalog §1/§8/§11/§14 consistent; (5) kill-switch matrix passes; (6) `VOCABULARY_VERSION` unchanged (or the full amendment executed and recorded); (7) all cross-phase notes in §9 surfaced to the architect; (8) ZERO edits to Phase-1 files / graph.py / routers / frontend. No commit, no promote, no live contact — the planner sweeps + commits.
