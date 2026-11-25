# P1 Investigation — PACK-COVERAGE Registration Lane (2026-11-25)

**Lane:** `feature/pack-coverage-registration` @ `ed8ab508c` (== origin/latest), worktree `/home/nea/ensemble-src-wt-pack-coverage`
**Commission:** diagnosis + proof for three "functionally never run anywhere" integration files. NO PACKS.md edits (implementer-owned). No commits.
**Workers:** static diagnosis `d5c32784`; per-file measurement `443d6880` (first pass) → `4f780bf6` (follow-up, fresh after wedge); pack diagnosis `71a83c76` (static) → `7c028f79` (micro-demo, fresh after wedge); final verbatim verification `f049fb8a`.

---

## 1. Premise check — the census was partly wrong

| Census claim | Verdict | Evidence |
|---|---|---|
| "no script anywhere passes `-m integration`" | ❌ **REFUTED** | 21 pack scripts + `pyproject.toml:99` + the test file itself (23 grep hits); e.g. `test/packs/integration_test.sh:16` |
| "the `integration` marker DESELECTS them" | ⚠️ TRUE only for addopts-inheriting sweeps | `pyproject.toml:105` `addopts = "-m 'not integration and not postgres and not slow and not conformance'"`; `test/packs/ab/top_integration_integration_test.sh:18` sweeps `tests/integration` without clearing addopts → collect-then-deselect |
| "functionally never run anywhere" | ❌ **REFUTED** | `test/packs/integration_test.sh:13-17` runs `pytest tests/integration/ --override-ini="addopts=" -m integration` → selects all three |
| "not registered by name" | ✅ **CONFIRMED** | No PACKS.md row names any of the three (one descriptive mention of the POK file at `PACKS.md:5643`, inside another pack's Status cell — not a registration) |

All three confirmed at origin/latest, each carrying `pytestmark = pytest.mark.integration` at **line 37**. First-adds: `test_skill_audit_pg_url_rewrite` 2026-11-17 (`5b8fef156`), `test_pok_gate_llm_integration` 2026-11-24 (`96a14de54`), `test_skill_capture_admission_e2e_dev` 2026-11-17 (`61ca67d62`). No CI config of any kind exists in the repo (no `.github` dir, no pytest.ini/setup.cfg/tox.ini).

---

## 2. Per-file dispositions

### F1 — `tests/integration/test_skill_audit_pg_url_rewrite.py` (the PIN) — ✅ **(i) ZERO-TOUCH REGISTRATION**
- **Invocation (verbatim):**
  `cd /home/nea/ensemble-src-wt-pack-coverage && set -a && . ./.env && set +a && ENSEMBLE_SELF_ENV=dev timeout 300 .venv/bin/pytest --override-ini="addopts=" -m integration tests/integration/test_skill_audit_pg_url_rewrite.py -p no:randomly -q --tb=short -rfE`
- **Measured:** **4 nodes** (`grep -c '::'` with the same flags); **RC 0**; **4 passed / 0 failed / 0 skipped / 0 xfail**; 9.30s wall.
- **Needs:** dev PG `ensemble_dev` @ 127.0.0.1:5432 (fenced `.env`) + psycopg3. No LLM, no daemon.
- No test-file edit; no marker re-scope.

### F2 — `tests/integration/test_pok_gate_llm_integration.py` — ❌ **(i)-shape but EXPECTED-RED** (product defect, NOT missing infra)
- **Invocation (verbatim):**
  `cd /home/nea/ensemble-src-wt-pack-coverage && set -a && . ./.env && set +a && ENSEMBLE_SELF_ENV=dev timeout 300 .venv/bin/pytest --override-ini="addopts=" -m integration tests/integration/test_pok_gate_llm_integration.py -p no:randomly -q --tb=short -rfE --timeout=120`
- **Measured:** **3 nodes**; **RC 1 on 3/3 final runs** (1 failed / 2 passed; the failing node rotates between `TestLiveJudge::test_the_live_judge_returns_a_closed_set_verdict` (`:72`) and `::test_the_live_judge_answers_the_gate_question` (`:91`)); wall 54–94s.
- **Run record across all workers:** 2 green (first pass: `-o addopts=""` 44.4s; `-m integration --timeout=120` 61.9s) vs 5 red with adequate timeout; at the default `timeout = 30` per-test ceiling the run dies with a pytest-timeout and **no summary** (RC 1).
- **Root cause (diagnosed, not guessed):** the judge model returns `"family": null`, violating `JSON_OUTPUT_CONTRACT` (`daemon/services/pok_gate.py:740-745` — `family` is a non-nullable enum `boundary|reason|durability|redundancy|scope`); the fail-closed parser (`pok_gate.py:937`) raises `POKGateUnavailable("unparsable")`. The one-retry budget (`GATE_MAX_RETRIES=1`) does not help — the model repeats the null. Raw-capture spy on `pok_gate._house_llm_call`: `deploy-02-rollback-rule` → 0/3 OK, `family` null on 6/6 raw calls (deterministic); `deploy-03-freeze-window` → 2/3 OK (variable).
- **Needs:** live LLM proxy via **AMBIENT** `OPENAI_BASE_URL`/`OPENAI_API_KEY` — the worktree `.env` provides **none** ⇒ without them the file **skips** (a skip is not a pass). No DB, no daemon. `--timeout=120` is necessary but not sufficient.
- **Unblock:** pin `POK_GATE_MODEL` to a contract-adherent alias, OR fix the prompt/parser contract. POK row `pok-gate-l11-family-null-expected-red`.

### F3 — `tests/integration/test_skill_capture_admission_e2e_dev.py` — ✅ **(i) ZERO-TOUCH REGISTRATION**
- **Invocation (verbatim):**
  `cd /home/nea/ensemble-src-wt-pack-coverage && set -a && . ./.env && set +a && ENSEMBLE_SELF_ENV=dev timeout 300 .venv/bin/pytest --override-ini="addopts=" -m integration tests/integration/test_skill_capture_admission_e2e_dev.py -p no:randomly -q --tb=short -rfE`
- **Measured:** **1 node**; **RC 0**; **1 passed / 0 failed / 0 skipped**; 10.24s wall.
- **Needs:** dev PG only (real-enqueue path, synthetic row + teardown). No LLM, no daemon.

---

## 3. Wrapper verdict — `scripts/run_tests_scrubbed.sh` is NOT usable for this trio

- **Arg passthrough is correct:** L139 `cd "$(dirname "$0")/.."`, L140 `exec .venv/bin/pytest "$@"` — `--override-ini="addopts="`, `-m integration`, `-p no:randomly -q` all survive (empirically: 4/3/1 nodes collected — impossible under default addopts).
- **But the scrub unsets `POSTGRES_HOST/DB/USER/PASSWORD/PORT/URL`** (L47-53), libpq vars (L62-63), `PLANE_*` (L74), `PORT` (L84), with a fail-closed leak gate (L103-135). DB files fall back to `user=postgres` / empty password and **SKIP**: `dev PostgreSQL unreachable — e2e pin is opt-in` (confirmed via `-rs`).
- **Measured wrapper outcomes:** F1 RC 0 / **4 SKIPPED** (vacuous green); F3 RC 0 / **1 SKIPPED** (vacuous green); F2 RC 1 (TIMEOUT at the 30s default; `OPENAI_*` are NOT scrubbed, so live calls still happen).
- **Conclusion:** DB-integration legs must source the fenced `.env` directly; the wrapper form must NOT appear in their Invocation cells. A wrapper-form "RC 0" here proves nothing (zero nodes exercised).

---

## 4. `test/packs/integration_test.sh` — `RESULT:` line unreachable (proven)

```
 4  set -euo pipefail
13  timeout 300s .venv/bin/python -m pytest \
14    tests/integration/ \
15    --override-ini="addopts=" \
16    -m integration \
17    --tb=short -q 2>&1
19  EXIT_CODE=$?                      ← the ONLY rc capture
21  if [ $EXIT_CODE -eq 124 ]; then echo "RESULT: TIMEOUT"; exit 124
24  elif [ $EXIT_CODE -eq 0 ]; then echo "RESULT: PASS";    exit 0
27  else                           echo "RESULT: FAIL";    exit 1
30  fi
```
- No `trap`, no cleanup, no `: "${VAR:?…}"` guards, no per-test `--timeout` override anywhere in the script.
- **Reachability: CONFIRMED.** L13 is a bare simple command; under `set -e` any non-zero exit (test failure **or** `timeout`'s 124) kills the shell **at L13** — L19 never runs, so **`RESULT: FAIL` and `RESULT: TIMEOUT` are dead code**; only `RESULT: PASS` is reachable. `pipefail` is inert (`2>&1` is a redirection, not a pipeline).
- **Empirical proof (5/5 shell legs, pure `bash -c`):** failing cmd → `script-rc=1`, **no RESULT line**; success → `RESULT: PASS`/0; timeout → `script-rc=124`, **no RESULT line**; fixed shape failing → `RESULT: FAIL`/1; fixed shape timeout → `RESULT: TIMEOUT`/124.
- **Gate adjudication rule (proven):** `rc≠0 AND no RESULT line` ⇒ FAIL; `rc=124` specifically ⇒ TIMEOUT — stdout carries nothing on the failure/timeout paths.
- **Exact minimal fix (NOT applied by this lane):** `EXIT_CODE=0` + `… 2>&1 || EXIT_CODE=$?`, and **delete** the standalone `EXIT_CODE=$?` (it would overwrite the captured code with 0).
- **State:** HEAD `ed8ab508c` still carries the defect; the **working tree already carries the fix (uncommitted)** — the edit landed mid-investigation and belongs to the pack-coverage/implementer lane, not to this investigation.
- **PACKS.md L4207 (verbatim):** `| integration_test | test/packs/integration_test.sh | All integration tests (require OPENAI_API_KEY, auto-skip without) | 5 min | 2026-04-24 | ❌ FAIL (37 passed, 6 failed — PRE-EXISTING, not system_default_project) |` — **ACTIVE** row; the 2026-04-24 stamp predates all three files by ~7 months.
- **Feasibility:** internal `timeout 300s`; an outer `timeout 300` would race/truncate it (needs >300s to let the inner guard own the cap); the sweep today selects **~68 integration-marked files / ~507 `def test_` upper bound** — the recorded 43-node stamp is stale.

---

## 5. Draft PACKS.md rows (implementer owns the file — NOT applied)

```
<!-- pack-coverage-p1-integration-trio-pack-registration-20261125-begin -->
## PACK-COVERAGE P1 — NAMED ROWS FOR THE INTEGRATION TRIO (2026-11-25; lane `feature/pack-coverage-registration`, HEAD `ed8ab508c`)

> **What this block registers.** Three `tests/integration/` files carrying `pytestmark = pytest.mark.integration` (line 37) that the `integration_test` directory sweep (L4207) selects only *incidentally* — never by a named row. Default addopts (`-m 'not integration and …'`, pyproject.toml:105) deselects them in every bare run; selection requires `--override-ini="addopts=" -m integration`.
> **Fence.** Dev DB only: `set -a && . ./.env && set +a` (fenced `.env` → `ensemble_dev` @ 127.0.0.1:5432), `ENSEMBLE_SELF_ENV=dev`, `timeout 300`. ⚔️ `scripts/run_tests_scrubbed.sh` is NOT usable for this trio: its scrub unsets `POSTGRES_*` (L47-53) ⇒ DB files fall back to `user=postgres` and SKIP (vacuous green, RC 0 with 0 nodes exercised).
> **Zero-touch.** No test-file edit, no marker re-scope — both zero-touch idioms select 4/3/1 nodes (measured).

| Pack / leg | Invocation | Scope | Last run | Status |
|---|---|---|---|---|
| `skill_audit_pg_url_rewrite_integration_test` (NEW 2026-11-25) | `cd /home/nea/ensemble-src-wt-pack-coverage && set -a && . ./.env && set +a && ENSEMBLE_SELF_ENV=dev timeout 300 .venv/bin/pytest --override-ini="addopts=" -m integration tests/integration/test_skill_audit_pg_url_rewrite.py -p no:randomly -q --tb=short -rfE` | **4 nodes** (1 file — the PG-URL-rewrite PIN). Dev PG + psycopg3; no LLM, no daemon. | 2026-11-25 @ `ed8ab508c` | ✅ **PASS 4/4** (RC 0, 9.3s wall) — zero-touch |
| `pok_gate_llm_integration_named_test` (NEW 2026-11-25) | `cd /home/nea/ensemble-src-wt-pack-coverage && set -a && . ./.env && set +a && ENSEMBLE_SELF_ENV=dev timeout 300 .venv/bin/pytest --override-ini="addopts=" -m integration tests/integration/test_pok_gate_llm_integration.py -p no:randomly -q --tb=short -rfE --timeout=120` | **3 nodes** (1 file — live-LLM path; needs AMBIENT `OPENAI_BASE_URL`/`OPENAI_API_KEY`, the worktree `.env` provides none ⇒ skips without; no DB, no daemon). `--timeout=120` required (default 30s ⇒ TIMEOUT). | 2026-11-25 @ `ed8ab508c` | ❌ **EXPECTED-RED 3/3 runs** (1 failed / 2 passed; failing node rotates `:72`↔`:91`) — root cause: judge model returns `"family": null`, violating `JSON_OUTPUT_CONTRACT` (pok_gate.py:740-745); fail-closed parser `:937` raises `POKGateUnavailable("unparsable")`. Unblock: pin `POK_GATE_MODEL` to a contract-adherent alias OR fix prompt/parser contract. POK `pok-gate-l11-family-null-expected-red`. |
| `skill_capture_admission_e2e_dev_integration_test` (NEW 2026-11-25) | `cd /home/nea/ensemble-src-wt-pack-coverage && set -a && . ./.env && set +a && ENSEMBLE_SELF_ENV=dev timeout 300 .venv/bin/pytest --override-ini="addopts=" -m integration tests/integration/test_skill_capture_admission_e2e_dev.py -p no:randomly -q --tb=short -rfE` | **1 node** (1 file — real-enqueue e2e admission proof on the dev DB). Dev PG; no LLM, no daemon. | 2026-11-25 @ `ed8ab508c` | ✅ **PASS 1/1** (RC 0, 10.2s wall) — zero-touch |
<!-- pack-coverage-p1-integration-trio-pack-registration-20261125-end -->
```

Companion re-stamp for the covering sweep (existing row L4207 — repair, not a new pack):

```
| `integration_test` (REPAIR + RE-STAMP — existing row L4207) | `test/packs/integration_test.sh` — HEAD `ed8ab508c` still carries the bare `EXIT_CODE=$?` (L19); the working tree already carries the fix (`EXIT_CODE=0; … 2>&1 || EXIT_CODE=$?`, delete the bare capture) — UNCOMMITTED | All 68 integration-marked files (~507 `def test_` upper bound) | 2026-04-24 (STALE — predates the trio by ~7 months) | ❌ FAIL (37P/6F) — `RESULT:` line UNREACHABLE on failure/timeout (proven 5/5 shell legs); re-stamp = commit the fix + fresh run |
```

---

## 6. P1-fix read — BOTH (named rows AND repair + re-stamp)

- **Named per-file rows are required** because the trio's coverage today is *incidental* (they run only because they live in `tests/integration/`), the covering pack's verdict line is dead code on failure/timeout, and its recorded stamp predates the files by ~7 months. Named rows give bounded, verifiable invocations with measured node counts and RC — and they expose F2's expected-red instead of hiding it inside a ~507-node sweep.
- **Repair + re-stamp of `integration_test.sh` is also required** because the fix is already in the working tree (uncommitted) and HEAD still ships the defect: without it no gate can read the sweep's verdict at all. Re-stamp = commit + fresh run + update L4207.
- **Sequencing:** commit the repair → fresh `integration_test` run → re-stamp L4207 → land the three named rows. Neither half alone is sufficient.

---

## 7. Incident — revive-path wedge (known v0.31.52 defect)

Two revived workers (`443d6880` follow-up, `71a83c76` micro-demo) completed their work and minted reports but could not terminalize (per-child prefix report-mint guard — known defect). Re-dispatched once each with fresh never-revived workers; wedged pair terminated; all data recovered cold. No reports were lost.
