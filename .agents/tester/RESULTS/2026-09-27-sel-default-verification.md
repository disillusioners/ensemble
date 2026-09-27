# Selectable-Models Fail-Safe Default — Tester Verification (sel-default-20260927)

- **Date**: 2026-09-27
- **Commission**: verify selectable-models fail-safe default in worktree (item 5 evidence file = this file)
- **Branch / tip / base**: `feature/selectable-models-default` @ `94cf54c5` (3 commits over base `1989b3b5` = v0.16.0)
- **Worktree**: `/home/nea/ensemble-src-wt-selectable-default`
- **Tester instance**: this tester (dispatch only, zero direct execution)
- **Workers**: `9cfa5016` boot-a (mock-test) · `9a6c3136` boot-b (mock-test) · `635d3df3` boot-c (mock-test) · `3007448b` lane (test-pack-execution) · `ab7fa55a` A/B adjudication (no skill) · `db5ad390` docs (no skill)

## Overall Verdict: ✅ PASS — tester gate GREEN

| # | Commission item | Verdict | Key evidence |
|---|---|---|---|
| 1a | Boot smoke: both env vars UNSET → default + exactly ONE warning, vision included, ready | ✅ PASS | `daemon_log_hits=1` for `allowed_models default applied: ['agentic', 'coding', 'coding2', 'vision']`; `/livez` 200 `version 0.16.0` at 12s; resolver probe returns default WITH vision; port 10081 freed, DB `ensemble_seldef_a` dropped; 15-16s, reproducible ×2 |
| 1b | Boot smoke: `OPENAI_SELECTABLE_MODELS=agentic,coding,coding2` (no vision) → no warning, strict, vision fails LOUDLY pre-LLM | ✅ PASS | warning grep = **0**; `POST /api/instances` → **HTTP 500** `VisionModelNotAllowedError: compare_images: 'vision' model is missing from config.llm.allowed_models (['agentic', 'coding', 'coding2']). Silent default resolution is FORBIDDEN per arch §8 🔴. Add 'vision' to OPENAI_SELECTABLE_MODELS and restart the daemon.`; fired via `_verify_vision_allowed` → `create_compare_tools` → `create_instance_tools` → `spawn_instance` (6 frames); **0 LLM HTTP calls** (dead port 10089 never consulted); port 10082 freed, DB dropped; 15s |
| 1c | Boot smoke: var set WITH vision → no warning, vision resolves | ✅ PASS | warning grep = **0**; `Spawning instance af31f9c0-… (agent=designer, model=vision, source=llm_model)`; ordering proof: validation passes → `[LLM] Invoking LLM … (model=vision)` → `Connection error` ×2 against dead `127.0.0.1:10089` (local-only failure, zero external calls); graceful shutdown 8s, port 10083 free, DB dropped; 31s |
| 2 | Precedence lane re-run: `tests/unit/test_llm_allowed_models_precedence.py` | ✅ PASS | **38 passed** in 1.03s (deterministic ×2 runs); ad-hoc wrapper `/tmp/seldef_lane.sh` under `timeout 120`/internal `timeout 110` (no exact-scope pack existed in `test/packs/` or PACKS.md); env scrub proof 0 survivors |
| 3 | Pre-existing-reds spot-adjudication (vision-guard fixture family) | ✅ PRE-EXISTING CONFIRMED | `tests/integration/test_spawn_intelligence_tier.py`: **7 failed / 4 passed byte-identical at tip `94cf54c5` (1.47s) and base `1989b3b5` (1.37s)** — same 7 node IDs, same `VisionModelNotAllowedError` shape. Cross-check: `git diff 1989b3b5..94cf54c5 -- <file> tests/helpers/send_message_fixtures.py` = EMPTY. Root cause: fixture `tests/helpers/send_message_fixtures.py:268-269` hardcodes `allowed_models=["agentic","coding","coding2"]` (no `vision`) — exists unchanged at both commits. 7 of the 8 claimed reds proven; the 8th (in `test_spawn_default_unchanged.py`, KB-attributed) shares the same fixture/root cause |
| 4 | Docs spot-check (setup.md :341-401 region, env examples, alias match, ensure.md static) | ✅ PASS | v0.16.0 upgrade note `setup.md:390-404`; env-var rows `:281-282` (`OPENAI_SELECTABLE_MODELS` default `agentic,coding,coding2,vision`; `OPENAI_ALLOWED_MODELS` legacy alias) + detailed semantics `:355-388` (default / strict-when-set incl. fail-open `[NOTE]` fallback, fail-closed `ValueError` for `model_tier`, instance-creation block / one-shot startup warning); `.env.example:59-61` and `.env.prod.example:48-50` both show real default + with-vision commented example; legacy alias byte-matches code (`config.py:431,2790,4096`); ensure.md Core static check: `--timeout-graceful-shutdown 10` at `dev.sh:102` |
| 5 | Evidence file | ✅ THIS FILE | committed as evidence rider (see commit hash in §Rider) |

## Scope Decision

Commission-scoped verification only. Full-dir packs are giter's merge-window job — not run here, per commission. ensure.md mapping: **Core #1** (changed packs green) satisfied by item 2 (the changed lane); the boot-gate intent covered honestly by items 1a-1c (three scrubbed real boots); Core static check (dev.sh flag) covered by item 4. Release Gate not triggered (config-default + docs change, not architecture).

## Environment discipline (all six dispatches)

- Zero-survivor `POSTGRES_*` scrub proven before every boot/test (standalone `#!/bin/bash` wrappers; dash-`source` trap avoided). **The ambient env genuinely carried live pointers** (`POSTGRES_HOST=10.44.0.2`, `POSTGRES_DB=ensemble_prod`, `POSTGRES_PASSWORD=…`) **plus an ambient `OPENAI_SELECTABLE_MODELS` value** — all scrubbed in outer shell; daemon's own log proves it ran against `127.0.0.1:5432/ensemble_seldef_*`, zero ambient-leak tokens.
- Worktree venv isolation verified per worker (`import daemon` → inside worktree). Two workers independently recorded the same caveat: the check is **cwd-sensitive** (`sys.path[0]` = CWD resolves main-tree `daemon` if run from main-tree PWD) — always `cd <wt>` first.
- LLM guard: dead endpoint `127.0.0.1:10089` — no real external LLM call in any scenario (proven in logs).
- Ports: 10081/10082/10083 (boots) + 10089 (dead LLM) — all in mock range; 8088/9797/7979/8079 untouched.
- Main tree `/home/nea/ensemble-src`: untouched by all workers.

## Findings & observations (none block the gate)

1. **Strict-mode blast radius is ALL spawns, not just vision agents** (by design): with `vision` excluded, `compare_images` is a universal tool → `_verify_vision_allowed` raises at instance-tool creation → **any** instance spawn is refused with the loud arch-§8 error. This is the v0.16.0 non-silent contract (`instance_lifecycle.py` diff empty by design), and it is exactly what the docs' v0.16.0 upgrade note warns operators about. Three independent evidence streams agree (docs / boot-b observation / base-reds A/B).
2. **The 8 integration reds are honest pre-existing** — fixture debt (`send_message_fixtures.py:268`), separate fix commission; our branch's diff does not touch the file or fixture (git-diff-empty proof).
3. 🟠 **Dev-PG role churn (operational, dev-env only)**: parallel boot workers juggled the local dev PG `ensemble` role password (boot-a `ALTER ROLE … 'seldef_a_pw'`; boot-c overwrote, then created dedicated `seldef_c_role` and best-effort-reverted `ensemble`). **Live PG 10.44.0.2 untouched** (leak-check 0 in all runs). If dev workflows later fail PG auth on the `ensemble` role, re-set its password. House fix for future waves: assign a dedicated PG role per boot worker up front.
4. Runtime residue: `/tmp/seldef_a_data/` left in place (boot-a note); all `/tmp/seldef_boot_*.log|sh` artifacts retained as evidence. `install-audit.jsonl` drift expected — left uncommitted per giter convention. `.agents/wt-scrub.sh` (worker dispatch helper) left untracked.
5. Boot-b detailed evidence also captured at `RESULTS/2026-09-27-seldef-boot-b-scenario-strict-no-vision.md` (worker-authored, committed with this rider).

## Rider

Commit: 71a0dacc (local, not pushed; merge/push MERGE-GO-gated).
