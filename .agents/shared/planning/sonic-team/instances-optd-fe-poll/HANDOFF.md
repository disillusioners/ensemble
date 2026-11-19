# HANDOFF — instances-list Option D (daemon) + FE home-poll cadence (sonic/instances-optd-fe-poll)

Commission: Emperor directives 2026-11-19 ("go for D too" + "FE poll every ~2 minutes"). Binding input: `../instances-list-latency/ANALYSIS.md` (§3 trace, §8 row D, §10 risks). Item 1 sequenced AFTER the Option-A lane (job 95e48b0b) per its coordination gate — verified landed as fd61c0ba2 / v0.31.12 before D dispatch; D byte-identity baseline = post-A tree.

HANDOFF_PATH = .agents/shared/planning/sonic-team/instances-optd-fe-poll/HANDOFF.md
Release lineage: fd61c0ba2 (v0.31.12, post-A) → ef89c8a97 (v0.31.13 release commit, tag v0.31.13, origin/latest). Code diff range for the verification wave: **fd61c0ba2..ef89c8a97**. This HANDOFF commit rides the task branch `sonic/instances-optd-fe-poll` (docs-only, above merge 72168a074).

## Files touched across modules
- **FE module** (`fe-home-poll-120s`, commit bf0d117c9): `frontend/src/app/pages/home/home.component.ts` (ONE literal :110, 10000→120000); `.agents/shared/planning/sonic-team/instances-optd-fe-poll/FE-POLL-SURVEY.md` (NEW, 188 lines, 16 pollers).
- **D1 module** (`optd-d1-offload-prefs`, commit b22f03bba): `daemon/routers/instances.py` (:545-548 — prefs batch read moved to `await _offload(manager._instance_ui_prefs_repo.get_all, instance_ids)`; manager.py UNTOUCHED); `tests/api/test_instances_list_offload_prefs.py` (NEW, 5 tests, mutation-proven 4/5-die-on-revert).
- **D2 module** (`optd-d2-skip-revalidation`, commit 9ac6eab6c): `daemon/routers/instances.py` (import :11 `fastapi.responses.JSONResponse`; return path :586-602 — `InstanceListResponse` built, then `return JSONResponse(content=resp.model_dump(mode="json", exclude={"instances": {"__all__": {"mcp_tool_names"}}}))`; decorator `response_model`/`response_model_exclude` UNTOUCHED); `tests/api/test_instances_list_response_bytes.py` (NEW); `tests/api/golden/instances_list_response_bytes.golden.txt` (NEW golden fixture).
- **Release cut** (commit ef89c8a97 on `sonic/optd-fe-poll-release` → pushed to origin/latest): `CHANGELOG.md`, `pyproject.toml`, `daemon/__init__.py`, `tests/test_api.py`, `uv.lock` (bump 0.31.12→0.31.13; CHANGELOG entry rode the bump's targeted commit).

## What changed
- **Item 2 (FE)**: the home page (the app's DEFAULT route) no longer polls `GET /api/instances?limit=100&include_descendants=true` (FE_A, 6.06 MB pre-A) every 10 s — now every **120 s** (12× rate cut). Polling mechanism (setInterval/clearInterval, ngOnDestroy teardown) untouched. FE-POLL-SURVEY.md enumerates ALL 16 FE pollers (surface | interval | file:line | pattern | API driven) for the Emperor's separate fleet-wide cadence decision — headline: badge 8 s (5 API legs/tick, ungated) is now the heaviest; 6 of 16 pollers lack a visibility gate; instance.service also list-polls the same endpoint at 60 s.
- **Item 1 (Option D)**: `GET /api/instances` list handler — (a) the ui-prefs batch read (~35 ms sync DB) moved OFF the event loop onto the house db-offload 6-worker executor (v0.27.14-consistent second route-local offload); (b) FastAPI's response_model validate+jsonable-encode pass (~100 ms at 1541 rows) SKIPPED by returning a hand-built JSONResponse whose body reproduces the Option-A `response_model_exclude` (mcp_tool_names key-ABSENT per row) BYTE-FOR-BYTE. OpenAPI/docs unchanged. Expected: ~−180 ms/req of loop occupancy; should damp the 3.7–4× badge-load inflation of instances legs (ANALYSIS §6 H3). Combined with A (−57.7 % payload) and the 12× poll cut, the home-poll load on the daemon drops by roughly two orders of magnitude in bytes/second.
- **Staged release v0.31.13** at `/home/nea/agents-ensemble/releases/v0.31.13` (rollback_safe=true source=explicit — zero migrations in the delta, additive-only; prompt-baseline OK 217 artifacts; floor 70a0960e5 validated). **NO promote** — the Emperor's 3-factor ceremony owns the flip; live still boots v0.31.10.

## What is UNTESTED
- Full-suite pytest regression NOT run — scoped packs only (see How to verify). The reviewer/tester wave owns the wide gate.
- Byte-identity proven on a 12-row deterministic golden fixture; the 1541-row large-page byte-identity and the −180 ms/req wall-clock saving are NOT re-measured (impact is the ANALYSIS §1.2/§8-D projection; D is structurally proven — thread identity + bypassed serialization pass).
- D2's optional timeit old-vs-new serialization comparison was skipped (unasserted nice-to-have).
- FE: whole-tree jest gate NOT run (home.component.spec only, 36/36); no `ng build` (no template change — AOT class NA); browser-side 120 s cadence not observed live.
- No live/PG end-to-end contact of the new code paths (dev/scratch + in-memory SQLite only, by commission fence).

## Known risks
- D1's fence pin (test e) regexes the exact call form `prefs_map = await _offload(manager._instance_ui_prefs_repo.get_all, instance_ids)` — a rename/refactor of that statement needs a re-pin (behavioral pins (a)-(d) are rename-proof).
- D2's byte-identity depends on `model_dump(mode="json", exclude=…)` matching FastAPI's encoder for this schema — the golden pack is the tripwire on pydantic/FastAPI upgrades.
- Ambient `ENSEMBLE_SELF_ENV=live` SHADOWS the worktree .env for any direct python/pytest invocation (anomaly 3f23b845) — every test invocation must use the per-var scrub + `ENSEMBLE_SELF_ENV=dev` override form (documented in both module briefs).
- Home is now the SLOWEST FE poller (120 s, ungated): a hidden tab can lag up to 2 min (was 10 s). Badge 8 s remains the dominant cadence — the `/api/jobs` recent-leg loop hog (ANALYSIS §8-E) is NOT addressed by this commission.
- OpenAPI still advertises `mcp_tool_names` on the list route (A-lane disclosed schema-vs-runtime gap; unchanged by D — decorator kept for docs).
- Plan-open `explore()` TIMED OUT (300 s, agent ce9afd97) — no KB read this commission; the injected POK (golden-envelope recipe, stage gotcha, FE tsc rule) substituted. Disclosed per the explore bound.

## How to verify (on the MERGED tree / staged release)
- Range: `git diff fd61c0ba2..ef89c8a97` (daemon: 1 file + 3 test/fixture files; FE: 1 file + survey doc; release: 5 bump files).
- Packs (worktree venv, per-var scrub + ENSEMBLE_SELF_ENV=dev): `pytest tests/api/test_instances_list_response_bytes.py tests/api/test_instances_list_mcp_tool_names.py tests/api/test_instances_list_offload_prefs.py -p no:randomly -q` → 12 passed; `bash test/packs/instances_list_mcp_tool_names_wire_shape_unit_test.sh <tree>` → 5 passed.
- FE gate: `cd frontend && ./node_modules/.bin/tsc --noEmit -p tsconfig.app.json` → exit 0; `npx jest src/app/pages/home/home.component.spec.ts` → 36 passed.
- Staged release: `status.sh` shows staged v0.31.13, live current→v0.31.10 unchanged; banner probe (sanctioned non-boot smoke): mktemp ENSEMBLE_DATA_DIR + per-var PG scrub + `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 ENSEMBLE_CONFIG=<staged>/config.yaml <staged>/ensemble-prod` → banner `Starting Ensemble v0.31.13`.
- 4-way binary sha (release dir == dist == manifest.binary_sha256 == sidecar.artifact_sha256): **e3e79fd4252683f45ec228be07adb13d8eeafc925e625d41139f73b83b04f4f2** — verified identical post-stage; tag v0.31.13 (annotated, 6d23c38cd) == origin/latest == ef89c8a97.

## Bash-raises used
none — all foreground commands ≤600 s; the long legs ran as background procs (worktree `uv sync`; `npm ci` + `_build_frontend.sh`; `stage.sh live`).

## Complexity self-assessment
MEDIUM (assessed at plan-open after explore+split; not raised, not overridden). Drivers: two disjoint items, zero migrations, single-file daemon change with a hard byte-identity contract (decomposed D into two stacked coder legs per the commission's decomposition note); FE = XS one-literal + bounded survey. The Option-A coordination gate was honored by holding Item 1 until A landed (verified fd61c0ba2) — no parallel edit of `daemon/routers/instances.py` ever occurred.
