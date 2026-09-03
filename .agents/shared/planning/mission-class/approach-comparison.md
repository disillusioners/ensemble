# Approach Comparison — Mission Class Options (5-Axis Matrix)

**Date:** 2026-09-02 · Ranked by synthesis of 5 worker reports (read-only, pinned `latest @e676ddea`). Risk = amount of risk (lower is better). Scores adjudicated from worker self-scores + cross-report evidence.

---

## 1. Ranked options

### 🥇 #1 — (b)+(d) hybrid: Mission read-model projection + agent-tool surface, mission-first vocab cutover ← **RECOMMENDED**
Mission as pure projection over instances/jobs (identity = `instance_id`, epochs best-effort); agents get `get_mission`/`await_mission`/`list_missions` with structural anti-trap guardrails; mirror wire rename to `settled` lands LAST, after consumer migration; storage deferred.
- **One line:** two nouns, two disjoint vocabularies, zero new writers — the noun split the user asked for at the lowest complexity that achieves it.

### 🥈 #2 — (a') additive vocab-only (`receipt_state` field, no Mission class)
Add a mirror-only `receipt_state` field alongside `status`; no Mission noun, no tools; `mission_liveness` remains the only outcome signal.
- **One line:** fixes the wire word without giving the outcome a noun — viable interim, but leaves `mission_liveness=None` indistinguishability, no mission identity for agents to await, and the wrong-predicate trap armed.
- **Disposition:** subsumed — its mechanics ARE M1/M3 of the recommendation; shipping it alone forfeits the tool contract that closes the trap.

### 🥉 #3 — (b) HTTP-first variant (GET /missions now, tools later)
Same projection as #1 but HTTP endpoint before agent tools.
- **One line:** serves operators (who already have FE mission chips) before agents (who have the live confusion) — correct architecture, wrong sequencing; folded into #1 as M4-gated.

### #4 — (c) Mission with own storage/table now (pull D forward)
`missions` + `mission_epochs` tables written alongside instance lifecycle.
- **One line:** rejected — a category error: vocabulary is fixed by projection + naming regardless of storage, while storage adds a third answer to "is the work done?", TP1-class dual-write divergence across ~20 instance-status sites, a Fix-C perf-contract violation, and a registry-INVISIBLE writer family (the 05-24 pattern).

### #5 — (a) pure rename-only (hard cutover, no additive field, no Mission)
Rename mirror terminal wire-status in one shot.
- **One line:** broken-by-design — every consumer (ari/jober, FE, filters) treats `completed` as the universal done-sentinel; silent misclassification of every mirror receipt until each consumer migrates; only coherent as the LAST step of #1.

### Variant notes
- **(d) tools-only, no HTTP ever:** rejected as a permanent stance (operators lose nothing today, but the projection contract should exist for a future endpoint under the same D3 declaration) — adopted as "tools first, HTTP gated."
- **(d2) epochs-as-first-class (epoch ids as params/filters):** rejected — zero control-plane benefit, new id lifecycle, violates the user's complexity ceiling; epochs are read-only nested history.

## 2. Five-axis matrix

| Approach | Complexity | Scalability | Maintainability | Risk | Cost | Verdict |
|---|---|---|---|---|---|---|
| **#1 (b)+(d) hybrid** | **Low** (1 resolver + 1 router + 3 tools + additive fields; zero DB, zero writers) | **High** (batch-read reuse of `_batch_instances`; no new contention) | **High** (one seam, one concept, terminal authority stays with Instance; self-documenting asymmetry) | **Low** (additive until M3; M3 gated on consumer migration + version window) | **Low** (~3-5 d across M0-M3) | ✅ **Recommended** |
| #2 (a') additive vocab-only | Med | High | Med (keyword-laden derivation; no noun for outcome) | Low | Med (~1-2 d) | Subsumed into #1 (M1/M3) |
| #3 (b) HTTP-first | Low | High | High | Low | Low | Folded into #1 as gated M4(i) — sequencing only |
| #4 (c) storage now | **High** (2 tables + epochs + ~20-site sync or new reconciler lane + backfill) | No gain (slight write amplification on hottest txn) | **Low** (third copy of mission truth; new divergence class to babysit) | **High** (dual-write divergence reborn; epoch gaps on revive; lossy backfill; D3 three-answers) | **High** (migrations + census extension + reconciler contract + API + tests) | ❌ Rejected — category error |
| #5 (a) pure rename | Med | High | Med | **High** (silent agent breakage) | Med | ❌ Broken-by-design standalone |

**User-philysics check:** #1 is "more things but separated concerns" (two nouns, two vocabularies, one new leaf service, zero new coordination) with no high-complexity ingredient — every added piece is a separation, not a coupling. #4 is the opposite: one new concept that couples three truth surfaces.

## 3. Cross-report conflict adjudications (how the synthesis was reached)

### 3.1 Mission identity — `instance_id` (W2) vs `job_id` (W5) → **`instance_id` WINS**
W5 anchored `mission_id == job_id` ("Mission IS the instance['s first task job]"). W3's evidence refutes job-keying decisively: `spawn_instance` (manager.py:6246) creates **no JobItem** for internally-spawned children (I4/JAFP) — job-keyed missions would orphan every agent-spawned child (the most common mission class in this system) or force JobItem creation on internal paths (I4 violation). Instance-keying inherits `parent_id` permanence, adds zero mint surface (D4-clean), and preserves the FE badge de-dup. **W5's guardrails are identity-agnostic** (they rest on naming/payload asymmetry, not on which id) — the tool contract survives the remap intact; `mission_ref.mission_id` on any job payload = that job's linked `instance_id`.

### 3.2 Rename mechanics — W4's word (`settled`) vs W1's additive field (`receipt_state`-first) → **MERGED as mission-first cutover**
Not a conflict but a sequencing union: W1 proved pure rename breaks consumers (M1 framing) and additive-first is safe (M3/M4 framing); W4 proved `settled` is the right word with a bounded FE prerequisite. Synthesis: additive projection + tool migration (M1/M2) → THEN rename the mirror wire value to `settled` under a one-release version-gate (M3). W4's own risk note ("version-gate... one release window") independently converges on this.

### 3.3 HTTP API timing — W2 (ship with M1) vs W5 (never/defer) → **DEFER, gated (M4-i)**
Agents are the burning consumer (live wrong-predicate trap in ari/jober prompts); operators already consume the split via FE mission chips (Fix C, shipped). Tools first; `GET /missions` when operator demand materializes, under the same D3 declaration. W2's endpoint design is preserved verbatim for that gate.

### 3.4 Epoch derivability — W2 (read-time epochs) vs W3 (no terminal-transition timestamps) → **W3's evidence wins; epochs best-effort now**
W2's epoch sub-struct assumed per-transition timestamps that the DB does not store (constitution §5 gap; only job terminal stamps approximate). Current epoch + liveness: precise. Historical epochs: best-effort reconstruction. Full-fidelity durable epoch history = the ONE genuine storage case → M4(ii) as append-only `mission_events`, gated on D's trigger / N2. Flagged as a documented limitation in the recommendation.

### 3.5 Task-job `completed` — **unanimous: STAYS**
W1 (renaming task rows orphans outcome search), W4 (task job IS its own mission; read-aloud passes), W5 (job_type='task' rows are the mission sentinel). No dissent to adjudicate.

## 4. Anti-recommendations (explicit)

- **No pure rename (M1/W1) before consumer migration** — silent agent breakage.
- **No status-bearing `missions` table now** — three-answers D3 hazard + registry-invisible writers + TP1-class dual-write divergence; if storage ever ships, append-only events preserving the single truthmaker.
- **No physical FK from missions/instances to jobs** — front-runs the deferred Phase-5 purge audit (repo norm: no FK).
- **No epoch ids as first-class params** — new lifecycle for zero control-plane value.
- **No mutation of Fix-C's four read-surface contracts** — extend additively (`mission_ref`, `mission_*` fields); never recase or repurpose existing fields.
