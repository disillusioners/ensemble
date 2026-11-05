# Phase 7 — Tests, gates, boot smoke

**Depends on:** P1–P6 (packs accumulate per phase; this phase consolidates, runs the tiered gates, the boot smoke, and registers everything into the tester canon).

## 1. Objective

Independent verification of the whole lane: consolidated pack census + mutation matrix, Tier-1 blast-radius legs, the MECHANICAL Tier-2 whole-tree gate (the lane touches `daemon/manager.py` and `daemon/api.py` — both core seams), real-PG behavior legs, the frozen-binary boot smoke under the standing critical conventions, and PACKS.md registration.

## 2. Decisions

| ID | Decision | Rationale (verified anchors) | Rejected alternatives |
|----|----------|------------------------------|------------------------|
| D7.1 | **Tier-2 whole-tree dual-side at lane end — MECHANICAL, not judgment**: the diff touches `daemon/manager.py` (registration + tsv ensure) and `daemon/api.py` (worker lifespan), both on the ensure.md core-seam path-prefix list | `.agents/tester/rules/ensure.md` Gate Tiers: "Escalation Tier 1→2 via the core-seam list is MECHANICAL, never a judgment call" — verified this session | Tier-1-only (would under-gate two core-seam files) |
| D7.2 | Per-phase Tier-1 blast-radius legs on merge (changed-pack scope: memoir packs + the registration/census families) | the tiered doctrine default for feature lanes; ≤30min budget, fail-loud | skipping per-phase gates until the end (late surprise) |
| D7.3 | Boot smoke: frozen-binary COPY to a scratch dir (never in place), `ENSEMBLE_SELF_ENV=sandbox`, **`ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` PINNED EXPLICITLY**, per-variable PG env scrub (NEVER `env -u POSTGRES_*` — it is a silent no-op), throwaway scratch DB, state.json fence PRE==MID==POST, `current->` symlink untouched, SIGTERM-only teardown | standing 🔴 critical notes: the sweep killed LIVE processes when the pin was missing (2026-10-31); the env-glob no-op (2026-10-21); the v0.28.x smoke recipe learnings (SELF_ENV=sandbox; binary copy; 32-var per-var scrub) | in-place boot (path derivation resolves the LIVE install root — 🟡 2026-10-21 note) |
| D7.4 | Mutation matrix (consolidated): cap rejection ×2 (count-bound, budget-bound), keepsake exemption, once-flag, agent-gate order (gate BEFORE any DB touch), decorator order, extends order, budget clamps ×3 (admission, render ceiling, recall envelope-on-shipped-projection), seed byte-identity, knob tristate (incl. fail-closed garbage), inner_soul non-ari byte-identity, `[MemoirEmbed]` counter honesty, repository no-general-update | every load-bearing guard in the lane gets a RED→GREEN proof (house doctrine; the metrics-read MT-26 / notify-user mutation-verified precedents) | happy-path-only packs (false confidence — the exact class the tiered doctrine exists to kill) |
| D7.5 | Real-PG legs required for: tsv generated-column population, GIN usage (EXPLAIN), `ts_rank_cd`/`plainto_tsquery`, JSONB embedding round-trip + Python cosine, ARRAY `stars`, CHECK constraints, partial indexes, pin-admission arithmetic under real tokens, the ALTER-ensure idempotence (second boot = no-op) | behavior is PG-specific where the drivers differ; SQLite legs cover the Python-BM25 fallback + the migration-runner path | mock-only DB tests (would miss the entire DDL-divergence risk R1) |
| D7.6 | PACKS.md registration block for the lane: every pack + last-run rows + declared scopes (the FE-T1 registration shape), and the guard `--collect-only` slicing proof where packs compose | house canon (verified: PACKS.md registration blocks carry invocation + scope + outcome + mutation-guard rows) | unregistered packs (invisible to future gates) |
| D7.7 | Non-goal guardrail sweep as an ACCEPTANCE test: grep-assert none of the forbidden surfaces were touched — `daemon/loader.py`, RAG tools beyond the inner_soul ari branch, any `agents/*/meta.json` except ari, `pyproject.toml`/`uv.lock` (beyond the release bump), `plugins/**`, `frontend/**` | plan-overview §2/§11 make the guardrails contractual; a gate that never checks them is decoration | prose-only guardrails |

## 3. Tasks

| ID | Task | Outcome | Depends | AC |
|----|------|---------|---------|----|
| T7.1 | Consolidate per-phase packs into the lane census; register in `.agents/tester/PACKS.md` | registration block merged; collect-only slicing proof for composed packs | P1–P6 | AC1 |
| T7.2 | Mutation matrix execution + digest archive (every leg RED→GREEN with planted-defect evidence) | matrix table with digests in the lane RESULTS doc | T7.1 | AC2 |
| T7.3 | Tier-1 legs per phase-merge boundary (as those merges happen) | per-phase gate rows | P1–P6 | AC3 |
| T7.4 | Tier-2 whole-tree dual-side at lane end (base = `1f6898ae2`): FAILED-digest equality, net-new-deterministic-reds = 0, 3× flaky isolation on diff nodes | gate report in `.agents/tester/RESULTS/` | all merges | AC4 |
| T7.5 | Real-PG corpus (D7.5 list) + SQLite fallback corpus | both green; driver-divergence table recorded | T7.1 | AC5 |
| T7.6 | Boot smoke per D7.3 + worker task census (exactly one `[MemoirEmbed]`-emitting task; absent with knob OFF) + tsv ensure idempotence (second boot no-op) + briefing present at ari turn-1 in the smoke env | smoke report; live fence intact; teardown clean | T7.4 | AC6 |
| T7.7 | Guardrail sweep D7.7 + prompt-artifact baseline 161/161 + registration census (`len==18` everywhere) | sweep report appended to the gate report | T7.4 | AC7 |

## 4. Acceptance criteria

| ID | Criterion |
|----|-----------|
| AC1 | Every lane pack registered with invocation + scope + last-run; PACKS.md carries the lane block |
| AC2 | 13+ mutation legs proven RED→GREEN with archived digests |
| AC3 | Per-phase Tier-1 rows green (or explicitly base-proven pre-existing) |
| AC4 | Tier-2: net-new deterministic reds == 0; FAILED-digest equality honored; wall-clock within the 90-min advisory or the breach documented |
| AC5 | Driver table complete: every D7.5 behavior proven on real PG; SQLite fallback legs green |
| AC6 | Boot smoke: banner + tables + tsv on scratch PG; sweep-pin explicit; state.json + symlink fences byte-identical PRE==MID==POST; SIGTERM teardown; worker census correct in both knob states |
| AC7 | Guardrail sweep clean; baseline 161/161; `grep` census shows only 18-flavored pins |

## 5. Test packs (consolidated view)

Per-phase packs (schema, retrieval, admission, budget, worker, degradation, briefing, seam, tools, knob, redirect, registration) + lane-level: `memoir_gate_tier2` (the whole-tree leg per the recipe skills), `memoir_boot_smoke` (D7.3 conventions), `memoir_guardrail_sweep` (D7.7). Recipe-skill loading is MANDATORY per ensure.md ("READ the recipe skill body before executing any gate leg" — the deterministic-path rule, never `skill_search` discovery).

## 6. Flag consumption

All flags' dispositions get their pinned proofs here (the mutation matrix maps 1:1 to flag dispositions #1/#2/#5/#6/#7/#11/#12/#13 + budget #3 + once #9 + timeline #10 neutrality + #14 idempotence).
