# TEST RESULTS — wi-1921ba17 FINAL SURFACE (cap raise + Emperor repin-revert rider)

**Verdict: TEST: PASS** — all four commissioned items verified on the pushed tip's tree.

**Tree identity.** Worktree `/home/nea/ensemble-src-wt-pok-rider`, branch `sonic/pok-cap-rider`.
HEAD `7be5c3a78` = `ea5325a60` + exactly one doc-only commit (`HANDOFF.md` +66, `git diff --stat` proven)
with a clean working tree ⇒ every pack run below executed code byte-identical to the pushed tip.
Remote state proven: `git ls-remote` → `refs/heads/latest == ea5325a60`, tag `v0.31.42`
(annotated `5ed8d13bf`, peeled `^{}` == `ea5325a60`). Full range `04bc5e513..ea5325a60`:
20 files, +148/−65 — matches the dispatch; no widening required.

## 1. THE BUDGET PROOF (commission demand) — MEASURED

`tests/unit/services/test_pok_injection_unit.py::TestCapIndependenceReadBudget` — **3/3 PASSED**
(`-v -s`, storage cap monkeypatched both regimes; measured numbers printed by the test itself):

```
test_constant_pins                                   PASSED   (POK_BLOCK_MAX_ENTRIES == 8; POK_BLOCK_MAX_CHARS == 2_900)
test_read_budget_independent_of_storage_cap[64]      PASSED   storage_cap=64:   8 entries / 2371 chars (budget 8 / 2,900 UNCHANGED)
test_read_budget_independent_of_storage_cap[4096]    PASSED   storage_cap=4096: 8 entries / 2371 chars (budget 8 / 2,900 UNCHANGED)
```

Injection file census: **45 collected** (42 standing + the 3-node budget class). Synthetic
in-memory store, 40 ari-visible rows seeded ⇒ EXACTLY 8 admitted in BOTH regimes — cap raise
512→1024 provably does not move the read-side budget.

## 2. Cap pins — PASSED

- `test_pok_service_unit.py::TestAc19TerminalityCured::test_active_cap_constant_is_1024` **PASSED**
  (asserts `POK_ACTIVE_CAP_PER_PROJECT == 1024`; source `daemon/services/pok_service.py:105`
  verified on BOTH the worktree and `git show ea5325a60`).
- `test_pok_tools_unit.py::TestRegistrationSurface::test_upsert_description_renders_the_live_cap` **PASSED**
  — retired-value guards 64/256/512 ("64/256/512 active entries" absent from the tool prose)
  + discriminating leg (description reads the constant at build time; a patched 999 renders through).
  NUANCE (disclosed, not a failure): the literal `== 1024` assertion lives in the SERVICE file only;
  the tools file pins via live-constant render + retired-value negatives — same guard chain as
  claim m1 describes ("AC19 pins" + "retired-value guard").

## 3. RIDER — repin revert (canonical `agentic` restored) — PASSED

- 4 metas at `ea5325a60` (via `git show`) AND worktree: aegis, sonic-developer, sonic-leader,
  sonic-reviewer → `"llm_model": "agentic"` ×4. Range diff of the 4 metas touches ONLY the
  `llm_model` lines (`agentic-turbo` → `agentic`) — tools/other keys untouched.
- `grep -rln agentic-turbo agents/` → **EMPTY** (worktree) and `git grep ea5325a60 -- agents/` → **EMPTY**.
- Pack `test/packs/sonic_agent_defs_checks.py`: `--self-test` → **SELF-TEST: PASS** (mutation legs
  fire RED, fixtures green; exit 0). `FROZEN [sonic-llm-pins-v3]` both markers present,
  `EXPECTED_LLM_MODEL` = leader/developer/reviewer **agentic** ×3 (coder/tester `coding`),
  in-memory fixture metas `:1113/:1121/:1137` = "agentic".
- `--static` → 19 PASS / **exactly {A3, A8, A9} FAIL** = the documented PRE-EXISTING red set.
  Red-set identity base-vs-head PROVEN, not assumed: A8's inputs (`agents/maintenancer/`,
  `.prompt-artifacts.sha256`) show an EMPTY diff across `b5484776e..ea5325a60`; A3's subject
  (tools allow/deny) untouched (only llm_model lines changed); `AGENTS_FENCE_BASE = "3f45cd6dd"`
  unchanged in the pack diff and none of the rider-touched metas appear in A9's residual list
  (they postdate the stale fence base). Attribution per HANDOFF Known risks + QUARANTINE context.
- Aegis family: `tests/unit/agents/test_aegis_security_family.py` → **556 passed** in 72.4s, exit 0
  (per-variable PG/DSN scrub, `ENSEMBLE_SELF_ENV=dev`, `-p no:randomly`).
- T27 pin: `TestAssignments::test_model_assignments` **PASSED** (asserts aegis `llm_model == "agentic"`, file `:833`).

## 4. Scoped 6-file POK pack — PASSED

Registered invocation shape (per-variable PG scrub, `ENSEMBLE_SELF_ENV=dev`, `-p no:randomly --tb=short -q -rfE`):
service + injection + metadata-cap + trust-tier + ari-seam + tools → **212 passed, 0 failed, exit 0** —
matches the handoff figure; 45 injection nodes included (§1).

## Rider doc leg (anchor re-derivation) — PASSED

`daemon/services/pok_service.py` anchors verified: `:105` `POK_ACTIVE_CAP_PER_PROJECT = 1024`,
`:121` `POK_BLOCK_MAX_CHARS = 2_900`, `:123` `POK_BLOCK_MAX_ENTRIES = 8`.
Stale-anchor grep `pok_service\.py:(104|120|122|240|310|460)` in
`docs/guidelines/guideline-pok-durable-vs-rolling-state.md` → **0 hits**.
Guideline registration `tests/unit/tools/test_guidelines_registration.py` → **43 passed**.

## Release layer — consistent

`daemon/__init__.py` `__version__ = "0.31.42"`; `pyproject.toml` `version = "0.31.42"`; CHANGELOG v0.31.42 fold present in range.

## Constraints honored

Live POK reads FENCED (AC10): no live-store assertions — all pins constants/synthetic.
No live-daemon boot; NO promote; no git writes (read-only inspection only). All commands under `timeout`.

## Adjudication

No NEW red inside the declared blast radius. Pre-existing static reds {A3, A8, A9} attributed
(documented in HANDOFF Known risks; inputs proven byte-identical base-vs-head). Pack tallies at
`ea5325a60`: budget 3/3 · cap pins 2/2 (+T27 1/1) · aegis 556/556 · defs self-test PASS ·
6-file POK 212/212 · guideline registration 43/43.
