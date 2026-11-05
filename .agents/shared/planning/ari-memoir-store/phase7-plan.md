# Phase 7 — Tests, gates, boot smoke

**Depends on:** P1–P6 (packs accumulate per phase; this phase consolidates, runs the tiered gates, the boot smoke, and registers everything into the tester canon).

## 1. Objective

Independent verification of the whole lane: consolidated pack census + mutation matrix, Tier-1 blast-radius legs, the MECHANICAL Tier-2 whole-tree gate (the lane touches `daemon/manager.py` and `daemon/api.py` — both core seams), real-PG behavior legs, the frozen-binary boot smoke under the standing conventions **plus the embedding-endpoint fence**, and PACKS.md registration.

## 2. Decisions

| ID | Decision | Rationale (verified anchors) | Rejected alternatives |
|----|----------|------------------------------|------------------------|
| D7.1 | **Tier-2 whole-tree dual-side at lane end — MECHANICAL, not judgment**: the diff touches `daemon/manager.py` (registration + the `tsv` ensure) and `daemon/api.py` (the worker lifespan), both on the ensure.md core-seam path-prefix list | `.agents/tester/rules/ensure.md` Gate Tiers: "Escalation Tier 1→2 via the core-seam list is MECHANICAL, never a judgment call" | Tier-1-only (would under-gate two core-seam files) |
| D7.2 | Per-phase Tier-1 blast-radius legs on merge (changed-pack scope: the memoir packs + the registration/census families) | the tiered doctrine default for feature lanes; ≤30 min budget, fail-loud | skipping per-phase gates until the end (late surprise) |
| D7.3 | Boot smoke: frozen-binary COPY to a scratch dir (never in place), `ENSEMBLE_SELF_ENV=sandbox`, **`ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` PINNED EXPLICITLY** (never `env -u` it — the sweep kills LIVE processes), per-variable PG env scrub (**NEVER `env -u POSTGRES_*`** — bash does not glob env names, so the glob form is a silent no-op), **per-variable `EMBEDDING_BASE_URL` / `EMBEDDING_API_KEY` / `EMBEDDING_MODEL` scrub (or a dead local port)**, throwaway scratch DB, state.json fence PRE==MID==POST, `current->` symlink untouched, SIGTERM-only teardown | the standing 🔴 critical notes (the sweep killed LIVE processes on 2026-10-31; the env-glob no-op on 2026-10-21); the embedding fence is NEW — the standing conventions do not cover the embedding endpoint | in-place boot (path derivation resolves the LIVE install root — the 2026-10-21 note); the env-glob scrub (a silent no-op that lets a fenced run hit prod) |
| D7.4 | **The boot smoke also asserts the WORKER TASK CENSUS**: exactly one `memoir_embedding_worker_task` with the knob ON, **ZERO** with `ENSEMBLE_MEMOIR_ENABLED=false` pinned explicitly (because unset ⇒ ON) | "unset ⇒ ON" means a bare smoke would start the worker — the census must pin both states explicitly | a single-state census (the OFF state is the operator-visible one) |
| D7.5 | Mutation matrix (consolidated) — the architect-named pins plus the standing ones: **write-path purity** (AM-12: an inline `await embed(...)` in the write body ⇒ RED), **flag write-site** (AM-22: drop the block from the persistent half ⇒ the flag MUST stay unset), **thread-name prefix** (AM-29: `memoir-embed`), **dimension mismatch** (AM-31: a 3072-dim vector ⇒ terminal, nothing stored), plus cap rejection ×2, keepsake exemption, once-flag, agent-gate order, gate order (AM-16: inverted ⇒ RED), decorator order, extends order, budget clamps ×3, the marked-id **equality** (AM-14), the atomic write-back (AM-15), write-back failure ⇒ an attempt (AM-30), the error classification (AM-32: 401 ⇒ terminal at once), the driver-agnostic worker (AM-36), the knob fail-closed (AM-25), the resolved-state discoverability (AM-26), seed byte-identity, the seed-failure tolerance (AM-39), the backstop legs (AM-43), the off-loop flag read (AM-37), the read deadline (AM-38), the PG SQL argument order (AM-1), the two-arg `to_tsvector` (AM-8), the result-shape bytes (AM-23), and the refusal-vocabulary freeze (AM-21) | every load-bearing guard gets a RED→GREEN proof (house doctrine; the metrics-read MT-26 / notify-user mutation-verified precedents) | happy-path-only packs (false confidence — the class the tiered doctrine exists to kill) |
| D7.6 | Real-PG legs required for: the two-arg `tsv` generated-column population, GIN usage (EXPLAIN), the **corrected** `ts_rank_cd(tsv, plainto_tsquery('english', :q))` (plus a mutation leg proving the reversed order fails), JSONB embedding round-trip + Python cosine, ARRAY `stars`, CHECK constraints, partial indexes, the ensure idempotence (second boot = no-op), **the ensure's one-transaction semantics (a failure in ANY entry — the memoir's or an inherited lane's — aborts the boot; there is no per-statement try/except, and statement ORDER IS NOT A CONTAINMENT MEASURE)**, and the `tsv` ALTER's failure being **loud** (no swallowed error) | behavior is PG-specific where the drivers differ; SQLite legs cover the Python-BM25 fallback + the migration-runner path | mock-only DB tests (would miss the entire DDL-divergence risk) |
| D7.7 | **The registration ceremony's pin set is re-derived BY GREP at implementation time** (G4) — the count in any document is dated EVIDENCE, not a specification | the registry's own instruction (`daemon/tools/_tool_registry.py:357-366`): "DERIVE THE PIN SET BY GREPPING THE TREE — do NOT trust any prose" — including the planner's earlier 11-site list, the architect's 16-site/11-file figure, and the registry's own stale prose | trusting any prose count (the plan's earlier list was incomplete; the registry's prose is stale) |
| D7.8 | PACKS.md registration block for the lane: every pack + last-run rows + declared scopes, with the `--collect-only` slicing proof where packs compose | house canon (PACKS.md registration blocks carry invocation + scope + outcome + mutation-guard rows) | unregistered packs (invisible to future gates) |
| D7.9 | Non-goal guardrail sweep as an ACCEPTANCE test: grep-assert none of the forbidden surfaces were touched — `daemon/loader.py`, RAG tools beyond the inner_soul ari branch, any `agents/*/meta.json` except ari, `pyproject.toml`/`uv.lock` (beyond the release bump), `plugins/**`, `frontend/**`, and **no new external dependency** (G6) | plan-overview §2/§11 make the guardrails contractual; a gate that never checks them is decoration | prose-only guardrails |

## 3. Tasks

| ID | Task | Outcome | Depends | AC |
|----|------|---------|---------|----|
| T7.1 | Consolidate per-phase packs into the lane census; register in `.agents/tester/PACKS.md` | the registration block is merged; the collect-only slicing proof for composed packs is recorded | P1–P6 | AC1 |
| T7.2 | Mutation matrix execution + digest archive (every leg RED→GREEN with planted-defect evidence), including the three architect-named pins (write-path purity, flag write-site, thread prefix) | the matrix table with digests lands in the lane RESULTS doc | T7.1 | AC2 |
| T7.3 | Tier-1 legs per phase-merge boundary (as those merges happen) | per-phase gate rows | P1–P6 | AC3 |
| T7.4 | Tier-2 whole-tree dual-side at lane end (base = `1f6898ae2`): FAILED-digest equality, net-new deterministic reds = 0, 3× flaky isolation on diff nodes | the gate report lands in `.agents/tester/RESULTS/` | all merges | AC4 |
| T7.5 | Real-PG corpus (D7.6) + the SQLite fallback corpus + the driver-agnostic worker leg | both green; the driver-divergence table is recorded | T7.1 | AC5 |
| T7.6 | Boot smoke per D7.3 + the worker task census per D7.4 + the ensure idempotence + the briefing present at ari turn-1 in the smoke env | the smoke report; the live fence intact; the teardown is clean | T7.4 | AC6 |
| T7.7 | Guardrail sweep D7.9 + the prompt-artifact baseline 161/161 + the **grep-derived** registration census (D7.7) | the sweep report is appended to the gate report; every pin hit is 18-flavored | T7.4 | AC7 |
| T7.8 | FC9 prompt-budget measurement (phase4's D4.14) folded into the lane gate evidence | the measurement is in the gate report | T7.1 | AC8 |

## 4. Acceptance criteria

| ID | Criterion |
|----|-----------|
| AC1 | Every lane pack is registered with invocation + scope + last-run; PACKS.md carries the lane block |
| AC2 | Every mutation leg in D7.5 is proven RED→GREEN with archived digests — including the write-path purity, the flag write-site, and the thread-prefix pins |
| AC3 | Per-phase Tier-1 rows are green (or explicitly base-proven pre-existing) |
| AC4 | Tier-2: net-new deterministic reds == 0; FAILED-digest equality honored; wall-clock within the 90-min advisory or the breach documented |
| AC5 | The driver table is complete: every D7.6 behavior is proven on real PG — including the **corrected** `ts_rank_cd` argument order (the reversed-order mutation leg REDs) and the two-arg `to_tsvector`; the SQLite fallback legs are green |
| AC6 | Boot smoke: banner + tables + `tsv` on scratch PG; the sweep pin explicit; the **embedding endpoint scrubbed per variable**; the worker task census exact in both knob states; state.json + symlink fences byte-identical PRE==MID==POST; SIGTERM teardown; a degraded read yields no block and no flag write |
| AC7 | The guardrail sweep is clean (including no new dependency); the baseline is 161/161; the grep-derived census shows only 18-flavored pins with `"memoir"` last in ari's allow array |
| AC8 | The FC9 measurement is recorded with both bounds evaluated |

## 5. Test packs (consolidated view)

Per-phase packs (schema, persona lane, retrieval, admission, budget, write purity, worker, degradation, briefing, seam, tools, knob, redirect, registration) + lane-level: `memoir_gate_tier2` (the whole-tree leg per the recipe skills), `memoir_boot_smoke` (D7.3 conventions + the embedding fence + the worker census), `memoir_guardrail_sweep` (D7.9). Recipe-skill loading is MANDATORY per ensure.md ("READ the recipe skill body before executing any gate leg" — the deterministic path, never `skill_search` discovery).

## 6. Flag consumption

All flags' dispositions get their pinned proofs here — the mutation matrix maps 1:1 to the adjudicated rulings and amendments (AM-1, AM-8, AM-12, AM-14, AM-15, AM-16, AM-18, AM-20, AM-21, AM-22, AM-23, AM-25, AM-26, AM-29, AM-30, AM-31, AM-32, AM-36, AM-37, AM-38, AM-39, AM-43).
