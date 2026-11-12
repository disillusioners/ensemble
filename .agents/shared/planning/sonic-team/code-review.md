# Code Review — ENSEMBLE-31 "sonic team v1" (content-only implementation)

Date: 2026-11-12 (UTC)
Mode: 🔴 Deep-Review (governor council — new-agent + Ari-routing workflow change feeding a stage/promote gate)
Session: governor `793418f1-8a38-48df-bcd9-98e25f56e003` (sonic-v1-review-council, 2 councilors: agentic + coding, skill `code-review`)
Target: working-tree diff of `/home/nea/ensemble-src-wt-sonic-v1` (branch `feature/sonic-team-v1`) vs HEAD `78391e0adc59809944f90daaaeb48d51b6116ae4`; base ancestry `ac9849bf7` (origin/latest) — verified.
Spec authority: `phase1-implementation-spec.md` (6 FROZEN blocks, 29 ACs) + `phase2-test-pack-spec.md`.

## Verdict: ✅ APPROVE-WITH-NOTES — MUST-FIX: NONE

**0 🔴 Critical · 3 🟡 Warning · 5 🟢 Suggestion — all advisory.**
Proceed to stage/promote decision; route the three 🟡s as a fast-follow hardening lane (see Recommendations).

## Scope & Inventory Reconciliation

Actual diff = **22 untracked + 10 modified files, nothing else** (porcelain set-exact vs the claimed inventory; re-verified after the sanctioned pack run: 32==32, zero residue).
- NEW: `agents/sonic-{leader,coder,reviewer,tester}/{meta.json,soul.md,rule.md,workflow.md,tools_note.md}` (20) + `test/packs/sonic_agent_defs_unit_test.sh` + `test/packs/sonic_agent_defs_checks.py`
- MODIFIED: `agents/ari/rule.md` (+9-line Sonic Lane note), `agents/.prompt-artifacts.sha256` (181→197), `.agents/tester/PACKS.md` (+7 append-only), `tests/test_chart_image_delivery_e2e.py` (seal re-pin), `tests/unit/test_anomaly_report_duty_prompts.py`, `tests/unit/test_pok_grant_pins.py`, `tests/unit/test_anomaly_report_grant_pins.py`, `tests/unit/agents/test_aegis_security_family.py`, `tests/unit/test_snapshot_agent_spaces.py`, + planner's `phase2-test-pack-spec.md` amendment (verified planner-intent; does not move goalposts vs frozen phase1 blocks — A11 anchor normalization is a legitimate AC-trap fix, see evidence below).

## Evidence of Multi-Model Convergence (why this verdict is trustworthy)

Two councilors, complementary partitions; each one's UNVERIFIED list was exactly the other's verified partition — no coverage gap, no disagreement.

- **Porcelain reconciliation** — both independently read 22+10=32 matching the claimed inventory; zero residue after the sanctioned pack run.
- **Ari seal `8d6ca1642a34…7015f9`** — static proof (all 35 rows recomputed clean; Ari note RAW BYTE-EQUAL to frozen §5.2 — stronger than the required whitespace-normalized equality) + dynamic proof (A8 seal-revert counter-mutation goes RED; full 197-row baseline `--check` exits 0).
- **A11 normalization is NOT a drift mask** — proven twice: raw byte-equality of the artifact AND the A11-anchor-drop counter-mutation still fires. The two-line-wrap gotcha is real; the spec amendment is an AC-trap fix, not goalpost-moving.
- **meta.json ×4** — text-level field-for-field vs frozen `[sonic-tools-v1]` + real-resolver spawn-rights verification in both directions with counter-mutation pinning. `llm_model: agentic` ×4; ×7 recursion multiplier coder-only, matching the `agents/coder` precedent; sonic-leader team_members = 4 incl. giter.
- **Fence (focus 3)** — daemon/, migrations/, and every steady-team agent dir untouched; only sonic-* new + ari/rule.md modified across all of agents/.
- **Pack run** (sanctioned): PASS in 11s — 11 GREEN fixture legs + 22 RED counter-mutation legs proving the mutations are real (not vacuous); drift-pin failure path proven live (exit 0/1/128). PACKS.md +7 registration rows verified real (pack is gate-visible).

## Findings

### 🔴 Critical
None.

### 🟡 Warnings (fast-follow lane; none block this content-only promote)
1. **Pack doc-vs-code mismatch** — `test/packs/sonic_agent_defs_unit_test.sh:7-9,19-27` + `.agents/tester/PACKS.md:4864-4870` document an "inner 240s guard" that does not exist (Layer A uncapped; Layer B 180/120s).
   - Fix: add the 240s cap or correct the doc/PACKS wording to the actual layer budgets.
2. **Pack coverage gaps** (strongest fast-follow candidate) — `test/packs/sonic_agent_defs_checks.py:630-642` does not assert six load-bearing focus-1 prompt properties: leader `invoke_agent_and_wait` ban, ~90-min intake guard, giter pre-approval envelope, coder one-shot discipline, bash-rule literals (≤10min/raise/30min), coder lightweight mandate (zero kb-writer/experience). A future regression in any of these would pass the gate green. The properties are correct TODAY (verified text-level + resolver-level); the gap is regression detection, not current correctness.
   - Fix: extend the pack with a properties leg asserting these six (subsumes 🟢4 below).
3. **Duty tripwire desensitized** — `tests/unit/test_anomaly_report_duty_prompts.py:343-481`: the 4 permanently-red canary legs (accepted per spec §448) mean a future duty-block loss in any of the 48 compliant files yields no NEW red.
   - Fix (follow-up): repair `agents/canary/rule.md` or add canary-aware exclusion so the 48-file leg re-arms.

### 🟢 Suggestions
4. Sonic-reviewer's third blast-block copy is unpinned in the A7 loop (`test/packs/sonic_agent_defs_checks.py:504`) — covered by the 🟡2 hardening leg.
5. `agents/sonic-leader/workflow.md:30` under-describes the reviewer's third copy (floor-not-ceiling is fine; one clarifying sentence would help).
6. `tests/unit/agents/test_aegis_security_family.py:2630` stale "177 base" parenthetical vs the 197 pin (cosmetic; message string per zero-logic mandate — see deviation d).
7. Stale test method names (`test_the_total_is_41` etc.) — pre-existing, ride-along cleanup only.
8. Context: the baseline pin was already stale at HEAD (177 vs 181) — this lane's 197 bump cures it.

## Deviation Adjudication (a)–(e)

| Dev | Verdict | Key evidence |
|---|---|---|
| (a) A22 amended "exactly 5 failed" | **ACCEPT-AS-DISCLOSED** | Replica proof: exactly 5 reds, all base-attributed files (developer/developer[v2] workflow.md, charter/rule.md, charter/workflow.md, charter/install-mermaid-cli.md), zero sonic/ari. ⚠️ Governance note: in-tree spec §447 still reads "0 failed"; the amendment lives only in the developer's disclosure, and the pack does not run A22 (review-verified, not gate-verified) → formalize the amendment in the commission record (see Recommendations). |
| (b) 4 canary-rooted reds | **ACCEPT** (spec-pinned §448) | All 4 legs root solely in `agents/canary/rule.md`; residual risk captured as 🟡3. |
| (c) reviewer's third frozen blast block | **ACCEPT-AS-DISCLOSED** | Byte-identical 14/14 lines to spec + both mandated copies; §4.0 is floor-not-ceiling; leaf agent needs the inline predicate; residual = 🟢4/🟢5. |
| (d) aegis stale assert-message string (:2629/:2630 "181"/"177") | **ACCEPT-AS-DISCLOSED** | Zero-logic mandate correctly honored; cosmetic only. |
| (e) ram_breach telemetry | **CONFIRMED BENIGN** | Typed content-free envelope, no argv leak, kill REFUSED, absent from pack output — environmental noise. |

## Fence & Residue Ledger

- Worktree HEAD unmoved (`78391e0ad`); no commits by any party; porcelain 32==32 set-exact before/after the sanctioned pack run and the whole review; zero stray appends (no install_audit.jsonl-class writes).
- Sanctioned pack run side effects: `.venv/` + `.pytest_cache/` (gitignored, inside worktree) and `~/.cache/uv` (outside repo) — acceptable, disclosed.
- Canonical checkout `/home/nea/ensemble-src`: **unchanged during the review** — `-uall` porcelain = 205 at dispatch AND at fan-in (byte-identical count; top entries identical). Reviewer correction of the council's ledger note: the council's "205 vs 111" divergence was **flag semantics, not staleness** — 205 = `--porcelain -uall` (untracked enumerated), 111 = `--porcelain` default (untracked dirs collapsed). Both readings were correct snapshots; neither tree drifted. Future fence ledgers: record the porcelain flag alongside the count.
- No read-only violations by any councilor. No 402 provider errors encountered.

## Recommendations (priority order)

1. **Proceed**: stage/promote decision is unblocked by this review (tester gate still applies per the commission).
2. **Fast-follow hardening lane** (before or alongside the next touch of these files): 🟡2 pack properties leg (six focus-1 properties) → also closes 🟢4; 🟡1 240s wording/cap; 🟡3 canary re-arm.
3. **Formalize the A22 amendment**: planner edits spec §447 (or the commission record notes it) so the tree's criterion matches the adjudicated "exactly 5 failed, none sonic/ari" — otherwise the next audit re-litigates it.
4. Ride-along cosmetics when convenient: 🟢5, 🟢6, 🟢7.

— Reviewer (v2), aggregating governor council 793418f1; 2026-11-12.
