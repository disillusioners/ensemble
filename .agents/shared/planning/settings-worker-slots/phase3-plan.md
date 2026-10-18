# Phase 3: Surfaces — allow-list (ari + leader) + FE hint rewrite

## Objective

The two user-facing/agent-facing surfaces flip on: ari + leader gain the `settings` category via meta.json (both files UNSEALED — no re-pin), and the FE hint stops asserting the stale literal "5 worker + 2 chat = 7 — constants, not settable here" in favor of registry-key prose. ZERO other FE code — the two new int keys render registry-driven.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | `agents/ari/meta.json`: append `"settings"` to the `tools.allow` array immediately after `"secrets"` (:35) | P2 t3/t4 (category exists) | ari's resolved tool set contains exactly `settings_get`/`settings_set`/`settings_clear`; meta.json parses; file remains unsealed (no `SEALED_SHA_BASELINES` entry) |
| 2 | `agents/leader/meta.json`: append `"settings"` to the `tools.allow` array (:14-15, single-line list) | P2 t3/t4 | Same resolution check for leader; no other allow entries touched |
| 3 | `frontend/src/app/pages/settings/settings.component.html`: rewrite the hint at :343-345 (exact current string: `"Real parallelism is also capped by the fixed worker thread pools (5 worker + 2 chat = 7 concurrent graphs — constants, not settable here)."`) to the registry-neutral wording: *"Real parallelism is also capped by the worker thread pools — `limits.worker_pool_size` + `limits.chat_worker_pool_size`, themselves managed settings on this page. Effective concurrent graphs = min(llm_concurrency, worker + chat), boot-applied."* — names KEYS, never literal numbers | P1 t2 (keys exist in the registry → they render in the same section) | The two new keys render in the Daemon section registry-driven: restart badge (:261-263), number input with min=1 (:303-311), TS integer/min-1 validation (:1022-1034) — all generic, ZERO component-code diff beyond the hint `<p>` |
| 4 | Manual sanity + specs: run the FE jest suite; manual GET/PUT/clear cycle against the dev daemon — edit `limits.worker_pool_size` → save → row shows db_override + restart badge → restart → value + source flip to the new tier → clear → reverts | 1-3 | Jest green; manual cycle documented in the PR (set → pending → restart-applied → cleared); no FE code file other than the .html hint modified (`git diff --stat frontend/` shows exactly one file) |

## Gate (tests that must pass to exit)

1. `cd frontend && npm test -- --watch=false` (jest) — green vs base.
2. Tool-resolution spot check: ari/leader spawn resolves the three tools; a worker/tester-shaped agent (not on the allow-list) resolves none — reuses the P2 strip tests.
3. `git diff --stat` for this phase: exactly `agents/ari/meta.json`, `agents/leader/meta.json`, `frontend/src/app/pages/settings/settings.component.html`.

## Coupling

- **Tight with Phase 2** — the allow entries name the category P2 froze; resolving them before P2 lands is impossible.
- **Independent of Phase 1 at the code level** (no imports), but the FE manual cycle needs P1's registry entries to render.
- **Loose with Phase 4** — jest re-run in the full suite.

## Risks

- Allowing `settings` on an agent whose team inherits tools → check the allow-list semantics before editing (empty-allow inherit universe never auto-grants privileged categories — structural).
- FE hint drift — wording references keys; reviewer checklist item (plan-overview risk 6/10).

## Exit Criterion

ari + leader can call the three settings tools; worker/tester cannot; the settings page renders both new keys with restart badges and passes a manual set → restart → apply → clear cycle; the FE diff is the hint string only.
