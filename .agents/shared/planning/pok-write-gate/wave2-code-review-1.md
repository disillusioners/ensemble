# Wave-2 Code Review — POK Write Gate (agents-ensemble)

**VERDICT: APPROVE-WITH-ADVISORIES (merge-level)** — with **C-1, a latent 🔴 CRITICAL, gating the P1 enforcement flip**: not a merge blocker under the shipped shadow-default-OFF, but it MUST be fixed before enforcement is armed.

Findings: **1 CRITICAL (latent, enforcement-gated) · 1 WARNING · 5 ADVISORIES**

| Field | Value |
|---|---|
| Date | 2026-11-24 (UTC) |
| Reviewer | Reviewer v2 (controller/dispatcher — Deep-Review council, round 2 of the lane; the controller did not analyze code directly) |
| Mode | 🔴 Deep-Review — triggers: Data Integrity (same-txn audit rows, unique-index migration, TTL fail-closed), Security (credential-scan pre-persist, judge model seam, ASK tool owner-scope), Cross-Cutting (2 new tools, route table, ari rider + baseline regen), Business-Critical (gate outcomes ACCEPT/MERGE/ASK/REJECT, librarian policy) |
| Sessions | Governor `c3d90588-6240-42f0-8b66-91f4a8e2bb2b`; councilors `councilor-agentic-w2-review` + `councilor-coding-w2-review` (agent `worker`, skill `code-review`, models agentic + coding = the full allowed set; 4-slot cap unreachable, multi-model consensus ≥2 achieved — normal confidence) |
| Target | Worktree `/home/nea/ensemble-src-wt-pok-write-gate`, branch `feature/pok-write-gate`; FIXED range `3f35f9d76^..942055e22`; HEAD at report time `ac03ddbf1` (advanced during review by the parallel Phase-12 lane: `aee27f358`, `e14001121`, `ac03ddbf1` — all excluded by range definition); porcelain CLEAN; remote branch ABSENT (unpushed). |
| Plan of record | `.agents/shared/planning/pok-write-gate/` (plan-overview + phase7–13 plans, design-amendments-v2, gate-rules amendments, `275266747` wave-2 prep fold). Task-row ownership CHAINS verified, not prose. |

## Scope

**In-range commits (11):** `3f35f9d76` judge/authority-split/G1 answer tool · `96a14de54` W2 unit pack + fork pins · `97f269ea4` fix round (F1 docstring truth, F2 mode-match replay, F3 type lie, F4 triple-homing pin) · `bf23c8c10` Phases 9-10 census/librarian/retrieval wiring · `c609f7247` librarian/census packs + shadow-mix · `15fb6b79a` route table per-content-class · `103b96b3c` ari rider + mutation driver · `9883920e0` docs — feeder assessment · `72e3ad1cb` idle-policy re-pins · `c05488a22` docs · `942055e22` disclosure truth + dormancy pin.

**Diff derivation (exact, topology-verified):** `git diff 3f35f9d76^..942055e22` isolates the 11 in-range commits plus `c134df2f3`'s planning-doc paths (context-only). The drift merge `9cf64bacd` (foreign origin/latest `7e97be2ff` content) sits ABOVE the range; the Wave-1-response tidy wave (`5167d0548..589cbcfbc`) sits BELOW it — both excluded by construction.

**Excluded:** drift-merge foreign content; planning-dir docs as review targets; all commits postdating `942055e22` (Phase-12 parallel lane).

## Method & process

Two councilors, identical dispatch with read-only directive; Round 0 full reports + ONE targeted reconciliation round adjudicating the single substantive asymmetry (single-sourced WARNING) and two classification conflicts — all three resolved (re-slug defect independently confirmed on range-top blobs; Advisory-7 "CLOSED" retracted by its own finder; 275-vs-270 pack count attributed to out-of-range Phase-12 commits). Convergence at Round 1; no Round 2. **Read-only held — zero behavioral violations**: sanctioned throwaway-worktree carve-out only (mktemp outside live paths, `git worktree add --detach`, removed afterwards), worktree-venv runs only, per-variable env scrubs, no live-store contact, no live LLM calls, no inter-instance messaging. Councilor-agentic explicitly declined to run the mutation harness because it mutates the live worktree in place — correct judgment.

## Findings

### 🔴 CRITICAL (latent, enforcement-gated) — C-1. Confirmed re-slug on the supersede leg is NOT count-neutral (violates plan T8.15).

- **Owning commit:** `3f35f9d76` (R-RET rename originates `daemon/services/pok_gate.py:1609-1624`; mint/answer legs `daemon/tools/pok.py:926, 997-1009, 1769`; prior-resolution `daemon/services/pok_service.py:510-511` → new-write leg `:662-714`; `supersede_and_insert` `daemon/repositories/pok/repository.py:508` never reached).
- **Mechanism (councilor-coding's verified chain, corroborating councilor-agentic's Round-0 finding):** R-RET fires only on an ACCEPT verdict, converting it to ASK with `proposed_slug` but no `target_slug`/`merge_target_slug`; `PendingGate` has exactly one slug field (grep for `original_slug|prior_slug|source_slug` at `942055e22` → 0 hits), so the pre-rename slug is discarded at mint; at answer time the upsert lands under the renamed slug where `resolve_slug_row(scope="active")` finds **no prior** → the write takes the **new-write leg**, inserting a fresh row while the old active row survives. Net: **+1 active row per write; the mis-slugged row persists.**
- **Trigger (all conjunctive):** enforcement ON + active prior with changed body + judge ACCEPT + zero slug↔body token overlap + confirming answer. **Dormant under the shipped shadow-default OFF.**
- **Test gap:** `tests/unit/tools/test_pok_gate_tool_unit.py:471-504` (`TestRetrievabilityRename`) covers only the new-write leg; `TestAuthoritySplit::test_a_supersede_is_judged` covers the supersede leg without rename. The combination is untested.
- **Fix:** carry the prior slug on `PendingGate` (e.g. `prior_slug`); on confirm, execute a count-preserving supersede of the prior into the proposed slug (or refuse with a typed code when the rename would strand a prior); add the supersede+rename leg to the R-RET test. Note: the plan's own `repository.py:286-311` pointer is stale at range top (`supersede_and_insert` lives at `:508`).
- **Severity divergence, surfaced not flattened:** agentic = WARNING ("P1-ceremony precondition, not a merge blocker"); coding = CRITICAL ("silently corrupts the store, reachable under a shipped DB-backed switch, untested — a landmine on the enforcement flip"). Synthesis adopts **CRITICAL-latent**: the stronger reasoning engages the plan-contract violation (T8.15 count-neutral is an explicit task-row predicate — body wins, and the body does not implement it) and the untested reachability. Both models converge on the identical action: **fix before enforcement; don't block the merge under shadow-default OFF.**
- **Controller ruling (verdict shape):** C-1 does not block the Wave-2 branch merge as shipped; it **BLOCKS the P1 enforcement flip** and is recorded as a ceremony precondition (see Controller Adjudication, Q1).

### 🟡 WARNING (unanimous) — W-1. The F2 merge-policy dial is inert.

- `daemon/tools/pok.py:556-570`, commit `3f35f9d76`. `merge_policy_override()` imports `SettingsService` but only returns the registry default (`ASK_WITH_PROPOSAL`) — the DB-backed `pok_gate.merge_policy` row (registered `settings_service.py:817-836`, allowed `ASK_WITH_PROPOSAL|AUTO|ASK_NO_PROPOSAL`) is never read; the AUTO branch is unreachable. Same docstring-truth class this wave's F1 fixed; contradicts `design-amendments-v2.md:146/:166` ("P1 ceremony moves the dial, not a rebuild").
- **Fix:** read via `SettingsService(engine).get_overrides().get(key)` mirroring `read_gate_switches`, validate against `MERGE_POLICIES`, fall back to default on missing/invalid; add a pin that a DB `AUTO` override changes the executed policy (RED when the read is removed). Not a safety hole — the default direction is fail-safe (caller confirmation); it is a **dead ceremony mechanism**. Recommended posture on the env-tier question: DB-only, matching the sibling switches; the P1 ceremony is DB-backed.

### 🟢 ADVISORIES (5)

1. **A-1** `.agents/tester/PACKS.md:5587` scope cell says "168 nodes (4 files)" while the same row's invocation lists 7 files and status says PASS 244/244 (7 files) — self-inconsistent; refresh scope+status in one edit. (`942055e22`)
2. **A-2** Registry-count prose drift: the wave adds two tools, `KNOWN_TOOL_NAMES` 269→271 (pin is correct: `test_pok_gate_tool_unit.py:781` asserts `== 271`); plan prose (`design-amendments-v2.md:190`), PACKS.md, and the test file's module docstring still say "269 → 270". Body wins; fix the three prose sites. (`96a14de54` docstring; plan/pack prose)
3. **A-3** `tests/unit/repositories/test_pok_write_gate_ledger_unit.py:476-481` docstring claims ASK_EXPIRED has "deliberately NO production writer" — false: the librarian writes it (`daemon/services/pok_librarian.py:1055-1071`). Drop the stale clause. (`97f269ea4`)
4. **A-4** `scripts/pok_librarian_mutations.py:24` hardcodes the live worktree and mutates in place — cannot run against a clean checkout, and risks mutating a parallel lane's bytes. Take the repo root as an argument and refuse to run on a dirty tree. (Neither councilor executed it for this reason.) (`103b96b3c`)
5. **A-5** (divergence, minor) `POK_GATE_DIGEST_K_OBSERVED = 48` (`daemon/services/pok_gate.py:197, 597`) is still a module constant while its docstring calls it "not a pinned constant". Coding flags the docstring/code mismatch; agentic accepted it as documented observed-sizing. Functionally inert (the 8000-char ceiling binds). Cosmetic fix: derive or rename. (`3f35f9d76`)

## Wave-1 carry-over closure table (at range top `942055e22`, post-reconciliation)

| Item | Status | Evidence |
|---|---|---|
| W-1 on_retrieval wiring, both admit_view sites | **CLOSED** | `context_messages.py:793-807` + `pok_ari_seam.py:179` pass `on_retrieval=make_retrieval_recorder(...)`; §4.9 double containment intact |
| Adv 1 reason/gate_fn forwarding | **CLOSED** | `tools/pok.py:1370-1383` → consumed `pok_service.py:362-378, :536, :662` |
| Adv 2 T8.16 partial unique index (3 sites) | **CLOSED** | migration `20261124_093000…gate_chain_unique_index.sql` + `models.py:512-538` + `manager.py:10375-10397`, byte-identical predicate |
| Adv 3 T8.17 curate-basis ledgered | **CLOSED** | `tools/pok.py:1935-1958` ledger row with `basis`, `executed=True` |
| Adv 5 credential-scan pre-persist (3 write sites) | **CLOSED** | `_scrub_ledger_row` at `repository.py:707/480/597` + gate-side redaction `pok_gate.py:877-888` |
| Adv 6 POK_LEDGER_SOURCES in `__all__` | **CLOSED** | `models.py:89` |
| Adv 7 SQL-push standing-defect prefix filter | **DEFERRED-BY-PLAN (still open)** | Python post-query filter `repository.py:877-878` over `query_ledger(limit=None)`; no LIKE/prefix in the query (grep 0 hits); growth-triggered, no plan row — non-verdict-driving. (Councilor-coding's Round-0 "CLOSED" was retracted in Round 1.) |
| Adv 8 FOR UPDATE supersede fix | **CLOSED** | `_read_for_update` `repository.py:486-506` (PG `with_for_update()`, SQLite plain), called at `:561` |
| Adv 10 same-txn vs best-effort decision record | **CLOSED** | `design-amendments-v2.md:216-225` §13.10 + code `pok_service.py:693-714, :978-995`, `tools/pok.py:856-890` |
| record_verification (no caller) | **WIRED** | `tools/pok.py:589` `_stamp_target_verification` on no-delta merge — resolves the retire-with-disclosure default |
| vector leg (dead twice) | **RETIRED-WITH-DISCLOSURE** | `repository.py:182` + `pok_librarian.py:670` |
| *(extra, agentic-only)* kb-writer resolved-tool pin RED window | **CLOSED in-range** | pin RED from `fd7dc6480` until `c609f7247` re-pinned; **Wave-1 review missed it** — recorded here for honesty. |

## Verified vs unverified (no silent gaps)

**Verified by execution** (worktree venv, per-variable `POSTGRES_*`/`DATABASE_URL`/`ENSEMBLE_DB_DSN` scrubs, `ENSEMBLE_SELF_ENV=dev`, `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`, throwaway detached worktrees removed afterwards):
- W2 pack **244/244** (both councilors, independent) · W1 pack **172/172** (both) · family_key_scoped **270/270 at range top `942055e22`** (coding; the leader's 270/270 is correct for the drift-synced tree) · maintenance-idle **35/35** (coding)
- LLM integration path: 3 collected / 3 deselected by default addopts — no live LLM invocation (both)
- Pre-existing-failure spot-check: `test_phase4_manager_decomposition` → **1 failed / 70 passed at BOTH base `a1130e257` and range top, same node** — base-attributed, not branch-caused (both, independent)
- Census artifact sha256 `6737281cf…75ab1` independently re-derived == shadow-mix gate assertion (agentic)

**Verified statically (range-top blobs `git show 942055e22:<path>`):** authority split by call-graph (judge's transitive callees: redaction regex, family-key regex, LLM client — zero store-write paths; only process-local `VerdictCache` + `PendingGateStore`) · R0–R5 + R-RET/R-PROV prompt strings byte-fidelity vs plan contracts · six typed ASK refusals, TTL 1200 fail-closed, owner scope, in-process-only pending state · EXECUTION-same-txn vs VERDICT-best-effort at both seams · T8.16/T8.17/T8.18 · librarian proposal-only call-graph + ruled-bases-only + soft-max floor ≥ current-active band · cache mode-match / ASK-as-re-ask / answered-never-accept / no-op-before-gate · D2 garbage→`POKGateUnavailable` never re-labelled reject · alias seam never reads `model_quick` · 20s/45s Pattern-C knobs · mutation legs non-vacuous (anchors verbatim, each mutation contradicts a named assertion, harness fails on missing anchor) · ari rider truthful (meta.json allow-list contains no `explore`; tools_note sha256 `99bd8726…` == single changed baseline row; baseline 217) · feeder zero `meta.json` edits in range.

**Unverified (disclosed):** the 11+4 mutation-leg **RED run artifacts** (no in-tree evidence; non-vacuity proven statically only — the harness mutates the live worktree, so neither councilor ran it) · the **"46 pre-existing failures in tests/unit/tools/" claim** (no in-branch provenance found in plan set, PACKS.md, or planning dir; agentic's full-directory base run exceeded 900s and was killed; only the one-file spot-check stands) · **PG dialect** behavior of the T8.16 index and FOR UPDATE (SQLite-verified only) · live LLM judge semantics · Phase-12 lane content (out of range by definition).

## Disagreements surfaced (not flattened)

1. **C-1 severity label** — agentic WARNING vs coding CRITICAL; identical operational prescription; synthesis adopted CRITICAL-latent (see C-1).
2. **Adv 7 classification** — coding CLOSED → retracted in Round 1 after reading the code; resolved DEFERRED-BY-PLAN.
3. **K=48 constant** — coding advisory vs agentic cleared; carried as A-5 with both positions.
4. **Prose vs pins inside the wave's own artifacts** ("269→270", "168 nodes/4 files", ASK_EXPIRED docstring) — both ruled body-wins: pins are right, prose is stale (A-1…A-3).

## Controller adjudication of the council's open questions

**Q1 — sequencing of the two dormant defects (C-1, W-1):** Both are recorded as **explicit P1-ceremony preconditions** (durable obligation: the plan/runbook must gate the enforcement flip on both fixes — verifiable at ceremony time), AND an **in-wave fix commit is strongly recommended while the branch remains open**: both are small (C-1 ≈ prior-slug carry + supersede routing; W-1 ≈ mirror `read_gate_switches`), and the untested supersede+rename leg needs its test landed *before* the ceremony, not discovered after. Rationale: writing the obligation only into the runbook leaves an untested store-corruption path behind a shipped DB-backed switch; fixing only in-wave leaves the obligation undocumented if the fix slips. Both.
**Q2 — the 46-failure claim:** No in-branch artifact exists that either the council or the controller can name. Adopted the council's recommendation: the plan's measured-state section must either **name the source artifact** for the 46-failure claim or **drop the bare number**; this record cites only the one-file spot-check (clean, base-attributed, dual-verified). Carried upward to the commissioning leader: if the number's provenance is a lane report outside the branch, name it or let it drop.

## Recommendations

1. **Before the P1 enforcement flip (hard gate):** fix C-1 (prior-slug carry → count-preserving supersede + the supersede+rename R-RET test) and W-1 (read the DB-backed `pok_gate.merge_policy`). In-wave commit recommended; ceremony preconditions recorded regardless.
2. A-1…A-5 hygiene (one-edit PACKS.md fix; three prose sites; one stale docstring clause; the mutation driver's live-worktree hardcode — fixing A-4 also makes RED runs reviewable, closing the disclosed evidence gap; K=48 derive-or-rename).
3. Adv 7 stays deferred-by-plan (growth-triggered; add a plan row when the ledger grows).
4. Mutation-leg RED **executions** remain evidence-debt: record the RED runs as in-tree evidence artifacts once the driver is checkout-safe (A-4).
5. Confidence: **2/2 models, normal path — no degraded-confidence notice.**

— Landed by the Reviewer controller (v2) on the commissioning instruction. Council closed cleanly: both councilors terminal `completed`, parent-error flag cleared and verified, manifest removed from `shared_meta_kv`.
