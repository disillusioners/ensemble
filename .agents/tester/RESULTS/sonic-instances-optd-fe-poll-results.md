# sonic-tester verification — commission `instances-optd-fe-poll` (Option D + FE home poll 120s)

**Verdict: TEST: PASS-WITH-NOTES**

Task worktree: `/home/nea/ensemble-src-wt-optd-fe-poll` (branch `sonic/instances-optd-fe-poll`, tip `9e4137bb3`, HEAD above merge `72168a074`; release cut `ef89c8a97` (tag v0.31.13) is its ancestor-equivalent code). Diff range verified: `fd61c0ba2..ef89c8a97` (11 files: daemon 1, FE 1, tests 3+golden, release 5). Env fence verified: POSTGRES_DB=ensemble_dev, DAEMON_PORT=8079, ENSEMBLE_SELF_ENV=dev. All runs dev/scratch only; NO live daemon/DB contact.

## 1. Gate packs (merged tree)

- `pytest tests/api/test_instances_list_response_bytes.py tests/api/test_instances_list_mcp_tool_names.py tests/api/test_instances_list_offload_prefs.py -p no:randomly -q` → **12/12 PASS** (8.18 s, worktree; 6.16 s on scratch merged tree; order/load: 9 isolated + 3 order-varied + 3 load runs, all 12/12).
- `bash test/packs/instances_list_mcp_tool_names_wire_shape_unit_test.sh <tree>` → **5/5 PASS** (12.81 s, independent TestClient transport).
- FE: `cd frontend && ./node_modules/.bin/tsc --noEmit -p tsconfig.app.json` → **exit 0** (scratch FE tree, `npm ci` from `ef89c8a97` frontend; the worktree's own node_modules was stale (2026-10-03, missing chart.js) — an environment artifact, resolved by a real `npm ci`).
- FE: `./node_modules/.bin/jest src/app/pages/home/home.component.spec.ts` → **36/36 PASS** (5.10 s).
- NEW pack (added by this wave): `bash test/packs/fe_home_poll_interval_unit_test.sh <tree>` → **4/4 PASS** (merged); **2/4 FAIL** on the base tree `fd61c0ba2` (literal `10000`) → detection power proven.

## 2. Byte-identity (golden vs `git archive fd61c0ba2` baseline)

Capture script run INSIDE both trees (sys.path[0] ⇒ `daemon.__file__` asserted under each tree; ran via the worktree `.venv` python). Both envelopes: status 200, body 4639 bytes, `mcp_tool_names` x0 (key-ABSENT).

- base envelope sha256 = `b4b5ca6be2ebdbfe72fcc30567aba0cea5037e421727d305fe9c0285c9adaadc`
- merged envelope sha256 = `b4b5ca6be2ebdbfe72fcc30567aba0cea5037e421727d305fe9c0285c9adaadc` → **BYTE-IDENTICAL**.
- committed golden `tests/api/golden/instances_list_response_bytes.golden.txt` == base envelope → **golden provenance confirmed** (developer really captured from the base tree, not the new tree).
- raw-byte grep: `mcp_tool_names` = 0 occurrences in both bodies.

## 3. Reversion proofs (scratch trees only, restored after)

- **(a) D1 reverted to on-loop** (`prefs_map = manager._instance_ui_prefs_repo.get_all(instance_ids)`): `tests/api/test_instances_list_offload_prefs.py` → **4 FAILED / 1 passed** (12.8 s). Red node ids: `test_prefs_batch_read_runs_off_the_event_loop`, `test_page_without_prefs_rows_still_reads_off_the_event_loop`, `test_empty_page_still_reads_prefs_off_the_event_loop`, `test_the_route_source_offloads_the_prefs_read`. The lone pass is the teeth-probe (`test_the_probe_sees_the_loop_thread_when_called_directly`), which is correct by construction.
- **(b) D2 bare-return** (`return resp` instead of `JSONResponse(...)`): `tests/api/test_instances_list_response_bytes.py` → **1 FAILED / 1 passed**. Red node id: `test_instances_list_no_revalidation_pass` (`RuntimeError: serialize_response must not be called on the response path`). The golden byte test stayed green (proving D2 is a performance change, not a wire change — bytes identical even when reverted).

## 4. FE cadence (independent)

- `home.component.ts:110` → `}, 120000);` (confirmed via sed; exactly ONE `setInterval` call site at :108; teardown `clearInterval` + `ngOnDestroy` intact).
- diff range `fd61c0ba2..ef89c8a97` touches exactly ONE FE source file; the only numeric literal change is `-10000` → `+120000`. No other interval literal in the range.
- The other `120000` occurrences in the FE tree (metrics banner geometry spec ×4, SSE command-progress spec ×2, command-state spec ×1) are in unrelated files, untouched by this commission.
- GAP (noted): `home.component.spec.ts` (36 nodes) never touches `setInterval` / fake timers / the interval literal, so a cadence revert leaves all 36 jest nodes GREEN. Closed by the new pack `fe_home_poll_interval_unit_test` (4 legs) registered below. Recommend a future test-side commission add a jest pin mirroring the static pack.

## 5. Staged artifact (READ-ONLY; file reads only, no promote, no live process)

Release dir `/home/nea/agents-ensemble/releases/v0.31.13`:
- binary at release ROOT (`ensemble-prod`, no `dist/` subdir) ✓
- sha256(release-dir binary) = `e3e79fd4252683f45ec228be07adb13d8eeafc925e625d41139f73b83b04f4f2`
- manifest.binary_sha256 = `e3e79fd4252683f45ec228be07adb13d8eeafc925e625d41139f73b83b04f4f2` → **MATCH**
- claimed (HANDOFF) = `e3e79fd4252683f45ec228be07adb13d8eeafc925e625d41139f73b83b04f4f2` → **MATCH**
- rollback_safe = `true`
- `current` symlink → `v0.31.10` (live untouched) ✓
- Banner proof: extracted the frozen PYZ with PyInstaller's `CArchiveReader` + `ZlibArchiveReader`; `daemon` module carries `__version__ = "0.31.13"` and `daemon.__main__` carries the literal `"Starting Ensemble v"` → renders **"Starting Ensemble v0.31.13"**. Control against the v0.31.10 binary reports `0.31.10` and correctly fails the `v0.31.13` claim (non-vacuous).
- Code-presence proof: `daemon.routers.instances.list_instances` in the frozen binary has 2 `_offload` call sites at source lines **520** (list body) and **547** (the ui-prefs batch read = D1), vs 1 at line 490 in v0.31.10; `JSONResponse` + `model_dump` are present in the candidate and absent in the control (D2). The staged artifact DOES carry the Option-D change.
- Sidecar leg (4th way): **NOT independently verifiable** — no `*.build-provenance.json` sidecar for v0.31.13 exists on disk (the stage worktree was retired; `grep -rl` of the claimed sha finds it only in `manifest.json`). Reported as a scoped, non-vacuous re-verification (3-way + banner + code-presence).

## 6. Regression sizing (scoped: `tests/api` + `tests/unit/routers` + `tests/test_api.py`)

Identical commands both legs (env scrub + `ENSEMBLE_SELF_ENV=dev`, `-p no:randomly`, `--timeout=60`, `--junitxml`).

- **Ambient-auth pair**: BASE 180 failures / HEAD 180 failures, **NET-NEW 0**, DISAPPEARED 0, identical failure sets. (Ambient `ENSEMBLE_AUTH_ENABLED` inflates the count; the comparison is apples-to-apples.)
- **Auth-scrubbed pair** (21-name `ENSEMBLE_AUTH_*` union derived at run time + PG/DATABASE_URL/PORT scrub): BASE 95 failures / HEAD 98 failures, **NET-NEW 3**, DISAPPEARED 0.

NET-NEW node ids (auth-scrubbed pair):
1. `tests.api.test_list_instances_source_param::test_source_composes_with_existing_filters`
2. `tests.api.test_list_instances_source_param::test_response_envelope_is_unchanged`
3. `tests.api.test_list_instances_source_param::test_source_empty_string_forwards_none` (full-suite only; isolated run shows 2 of 3)

Root cause (NOT a production defect): `test_list_instances_source_param`'s `client` fixture never calls `_db_offload.configure(loop=asyncio.get_running_loop())`, unlike the D1/D2 packs' fixtures. D1's additional offload call surfaces the stale-loop binding. Proven: patched fixture (one added line) → **6/6 PASS**. The production seam is configured at daemon startup; only the test harness is missing the binding. Recommended test-side fix (out of tester scope, 1 line, in the fixture).

QUARANTINE pre-check: the only commission-touched file among the failing files is `tests/test_api.py`, whose diff is the version bump only (`"0.31.12"` → `"0.31.13"` in `test_health_check`, which passes on both legs). All other failing files have an EMPTY diff against `fd61c0ba2..ef89c8a97` (commission untouched) and are QUARANTINE-mentioned (e.g. `test_instance_ui_prefs_api` ×13 — the known `search=` kwarg standin issue) or ambient-auth artifacts.

## 7. Order / load stability (new pins)

- 9 isolated single-file runs (3× each of D1/D2/A): all 12/12.
- 3 order-varied combined runs: all 12/12.
- 3 load runs (4 CPU spinners): all 12/12.
- No order- or load-induced reds.

## 8. Pack registration (worktree `.agents/tester/PACKS.md` == origin/latest blob)

Added ONE row per pack (tagged `sonic`) in a new marked block `sonic-optd-fe-poll-pack-registration-begin..end`:
- `instances_list_offload_prefs_unit_test` (5 nodes)
- `instances_list_response_bytes_unit_test` (2 nodes)
- `fe_home_poll_interval_unit_test` (4 legs)

The main-checkout `.agents/tester/PACKS.md` (detached, behind origin/latest, already dirty) was NOT touched — my block is absent there (verified count 0).

## 9. Notes (non-blocking)

N1. The FE change has no jest pin (home.component.spec.ts never touches setInterval). Closed by the new static pack; a future test-side commission should add a jest pin mirroring it.
N2. The staged-release sidecar (4th way) could not be independently verified (retired stage worktree). 3-way + banner + code-presence provide a non-vacuous equivalent.
N3. `test_list_instances_source_param` needs a 1-line fixture fix (`_db_offload.configure(loop=asyncio.get_running_loop())`), proven by the patched run (6/6). Out of tester scope (test-side, not production).
N4. The ambient-auth scrub is mandatory for clean gate legs in this codebase (21-name union); the house POK documents it.

## Verdict

All packs the commission ships (12 + 5 + 4) PASS; byte-identity proven; both reversion proofs fire (4F/1P and 1F/1P); FE gate (tsc exit 0 + 36/36) passes; staged artifact carries the change (frozen PYZ bytecode); the 2-3 net-new reds are a test-harness gap (missing seam binding), proven by the patched fixture passing 6/6, not a production defect.

TEST: PASS-WITH-NOTES