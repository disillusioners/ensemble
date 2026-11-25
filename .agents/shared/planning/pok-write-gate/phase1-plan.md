# Wave 1 · Phase 1: Admission Ledger

## Objective
Add a dedicated additive table that records admission + retrieval + gate verdict + tool execution + basis for every gate event. It is the decision substrate for every later cut and the sink for both the gate's verdict and the tool's execution (authority split, addendum-3 item 3). Scope: admission + retrieval (DIVERGENCE C — architect's wider scope, sign-off recorded). **Acceptance: an operator must be able to answer "why was this row accepted?" from the LEDGER ALONE** (gate_id, verdict, reason excerpt, judge explanation, phase, execution) — no cross-store join. **The `gate_id` + `phase` + merge-proposal fields land UP FRONT** — the ledger is additive and cheap now; a schema that cannot represent a proposal forces a migration at P1.

## Tasks
| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Define ledger SQLModel table: admission + retrieval + verdict + execution + basis **plus `gate_id` + `phase` + merge-proposal fields** (`target_slug`, `merge_kind` no_delta\|delta, `proposed_body`, `proposed_body_sha256`, `target_body_sha256`, `executed_body_sha256`, `executed_by`) | Phase 0 | Schema test passes |
| 2 | Add additive migration `.sql` if required by house rule (every new table gets its own file) | T1 | Migration present or explicit no-migration argument |
| 3 | Ledger repository: append-only, read-only-cheap append/query | T1 | Unit test append + query |
| 4 | Gate→ledger verdict write + tool→ledger execution write (BOTH recorded; ASK chain = 3 rows keyed by `gate_id` + `phase`: `ASK_MINTED` → `ASK_ANSWERED` → `ASK_EXECUTED`; plain ACCEPT/MERGE/REJECT keep 2 rows) | T3 + W2 Phase 8 | **W1 half:** schema + repo accept the 3-row ASK chain shape (columns present) — Unit test: schema carries `gate_id` + `phase`. **W2 half:** verdict AND execution rows actually written (3-row chain) — Unit test both rows + 3-row chain |
| 5 | Retrieval event capture hook in injection path | T3 | Unit test retrieval logged — these counts supersede access_count for the librarian (J amendment 1) |
| 6 | Mandatory `basis` enum on `pok_curate` archive | T1 | Unit test basis required |
| 7 | Ledger read surface (internal) | T1 | Unit test |

## Coupling
- **Tight with:** Phase 2 (the coordination row — the basis enum records the split/archive basis; the split/archive itself is the parallel lane's execution); Phase 5 (metrics sink); Phase 8 (authority split audit + 3-row ASK chain); Phase 10 (soft-max reads ledger retrieval counts).
- **Loose with:** Phase 3 (mechanical verdicts).

## Risks
- DIVERGENCE C/D resolved (architect sign-off §3) — build the wider dedicated-table shape.
- Migration rule (every new table needs `.sql`) — verify at build; no existing-row schema change.
- **Prohibition (I1): pass summaries do NOT live in the admission ledger** — the soft-max is metadata, not a ledger row (retention could delete state).

## Exit Criterion
Ledger table + repo + hooks land with `gate_id`/`phase`/merge-proposal fields present; append/query tested; basis enum enforced; no existing-row migration. (Wave 2 half — verdict AND execution rows recorded (3-row ASK chain) — lands in Phase 8's exit criterion.)
