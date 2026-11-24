# Wave 2 · Phase 7: Gate Service — 4-Outcome POK Librarian (SHADOW)

## Objective
Build `daemon/services/pok_gate.py` — the semantic judge as a POK-librarian maintenance agent with FOUR outcomes: ACCEPT / MERGE / ASK / REJECT. In SHADOW by default: logs verdicts to the ledger, refuses no write. Judge input = caller's `reason` as OPERATIONAL CLAIM; judge question *"what breaks in THIS repo if we drop this?"* Binding picks: F2 (merge authorship), 20s/45s timeouts, K=48 digest, purpose-bound alias seam, ASK outcome variant.

## Tasks
| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Module skeleton + typed outcome (verdict ∈ {ACCEPT,MERGE,ASK,REJECT}, family, explanation, alternative, confidence, target_slug, merge_kind, proposed_body, proposed_body_sha256, target_body_sha256, executed_body_sha256, executed_by, question, latency, cache_hit, model_alias) | Wave 1 done | Import smoke |
| 2 | Judge client (quick alias, per-attempt HTTP timeout + wall cap, factory re-reads failover URL) | T1 | Mocked LLM unit |
| 3 | R0–R5 + R-RET/R-PROV gate-rule parser (closed-set verdict+family UNCHANGED, one fence, garbage→unavailable — fail-closed, never re-labelled `pok_gate_reject`) | T1 | Unit tests |
| 4 | Family→template map (5, each names alternative home + Emperor trace phrase) | T1 | 5 CONTRACT tests |
| 5 | MERGE detection: if active-row digest carries the fact → MERGE + target slug, NOT a second row | T1 + Phase 1 | Unit test (count-preserving) |
| 6 | MERGE-body authorship = **F2**: judge outcome carries `target_slug` + `merged_body_proposal`; ASK payload carries the FULL target body (not the 80-char digest slice) + candidate body; caller confirms/edits; TOOL executes the caller-final body. `merge_policy` enum ∈ {`ASK_WITH_PROPOSAL` (default), `AUTO`, `ASK_NO_PROPOSAL`}. **No-delta merge = NO write** (verdict row + `record_verification` stamp on the target); **delta merge** = the only case a merged body is authored | T5 | PIN: default = ASK_WITH_PROPOSAL; mutations: auto-execute without confirmation → RED; no-delta merge writing a row → RED |
| 7 | Confidence ≤2/3 → ASK (never auto-reject) | T3 | Unit test |
| 8 | Verdict cache (bounded LRU, key = project+slug+sha256 body+sha256 normalized reason; ASK cached AS-ASK, answered NEVER cached as ACCEPT; stub/llm entries segregated) | T1 | Key-exactness unit |
| 9 | Digest (**K=48**, 80-char one-line bodies, 8000-char cap ≈7250 used, boundary clause never dropped; zero-overlap fallback = same-family-first / most-recently-verified) | T1 | Budget unit: K=48 ≤ 8000 |
| 10 | Pattern-C timeout knobs — **20s/attempt, ≤1 retry, 45s wall**, env `ENSEMBLE_POK_GATE_TIMEOUT_S`, min 10 / max 180 (12s OVERTURNED: it sat below an observed 13.6s judge success) | T1 | Unit tests |
| 11 | Stub mode (rule-based, never silent pass-through; **prod-forced to llm** — mode=stub in prod ⇒ loud one-shot WARN + forced llm; stamp `model_alias="stub"`; segregate stub cache entries) | T1 | Rejects known-bad; prod-force unit |
| 12 | R1-beats-R4 clause verbatim + **R-PROV protective clause** (date-as-evidence admits; R1-beats-R4 shape) + judge question in prompt | T1 | Boundary CONTRACT test (incl. R-PROV dated-provenance admits) |
| 13 | Alias seam — purpose-bound `POK_GATE_MODEL` env, default literal `"quick"`; NEVER reads `config.llm.model_quick` (silently rewritten to the GLOBAL model by `set_title_model_fallback`, `config.py:573-584`) nor `config.llm.model`; stamp the resolved alias on every verdict row | T1 | Unit test: alias is the literal purpose-id, not the global model |
| 14 | `UpsertOutcome` gains an ASK variant (current shape has non-optional `entry`) | Phase 7 boundary | Unit test: ASK outcome constructible at `daemon/services/pok_service.py:237-242` |
| 15 | **R-RET (census R6)** judge question *"would a searcher using the body's own operative terms find this row by its slug?"* + advisory-only lexical leg (no mechanical pre-filter); optional `proposed_slug` outcome field (T7.1 retains base fields); no new verdict/family token | T1 | Unit test: R-RET advisory flag; mutation: lexical-only auto-reject → RED |

## Coupling
- **Tight with:** Phase 8 (authority split + G1 answer tool); Phase 10 (soft-max tightening feeds ASK/MERGE preference); Phase 12 (guideline shape).
- **Loose with:** Phase 11 (test stubs mirror service).

## Risks
- D1 tail at enforce — 45s worst case inside a 180s-heartbeat turn; measure the fail-closed rate in shadow before P1; the latency probe (2.6–13.6s, 4/8 >15s) is from 2026-09-07 and must be re-measured.
- Alias seam hole: reading `model_quick` would silently route the gate to the global model — the env seam closes it by construction.
- Merge proposal rubber-stamping — mitigated by the FULL target body in the ASK payload plus proposal-vs-executed sha256 in the ledger.

## Exit Criterion
`pok_gate.py` importable; 4 outcomes reachable; ASK outcome variant exists; parser closed-set (verdict+family unchanged) over the R0–R5 + R-RET/R-PROV rule set; R-RET advisory-only lexical leg present; R-PROV protective clause present; MERGE names slug under F2; confidence→ASK; 5 templates name homes; R1 beats R4; K=48 digest within cap; 20s/45s knobs; stub prod-forced; alias purpose-bound.
