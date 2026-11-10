# v0.29.16 ARTIFACT VERIFICATION — release-cut TESTER GATE (SHIP/NO-GO)

- **Date:** 2026-11-10 (gate window 22:07–22:20 UTC)
- **Verdict: SHIP** — all executable gates PASS; Gate 3 N/A by rule (covering evidence = reviewer tree-level string verification); zero NO-GO flags.
- **Artifact:** `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-build-v02912/dist/ensemble-prod` · sha256 `00f647e9382f6187d00c71760e01d0e9f129f54044db7849763310f897544faf` · 56,278,512 B · Mach-O arm64
- **Tag lineage (caller-stated, independently corroborated):** `v0.29.16` (object `f0a619f53`) → `ef8eda0eb4890291b9ee44b43d1730c916d48a00`, parent `85bd6a37b` == origin/latest; build worktree HEAD == `ef8eda0e…`, `describe --tags --exact-match` == `v0.29.16`, porcelain clean.
- **Workers:** `9b9a9757` (Gate 1, static) · `5cf6758e` (Gates 2–4, boot+cleanup). Both no-skill infrastructure dispatches.
- **Boundary compliance:** MAIN checkout + build worktree READ-ONLY (pre-existing foreign `M .agents/tidier/notes.md` untouched); live install (9797), self-system (8088), dev daemon (8079), FE (4199) never touched; no pattern-kill; no make/deploy/stop/upgrade scripts; no push/tag/stage. This RESULTS file is intentionally **NOT committed** — brief's HARD boundary "MAIN checkout untouched · verdict only"; leader folds it into the release finalize path-limited if desired.

---

## Gate 1 — Version truth (frozen-binary scan): PASS

Method: PyInstaller `CArchiveReader` (house chain; `strings`/grep never used as proof — PYZ is zlib-compressed marshalled bytecode). Dual reader routes, byte-identical results: build-venv **Python 3.13.3 + PyInstaller 6.19.0** and uv-ephemeral **3.14.6 + 6.22.3**.

| Assertion | Result | Evidence |
|---|---|---|
| sha256 == `00f647e9…4faf` | ✅ | `shasum -a 256` exact (re-checked post-run, unchanged) |
| size == 56,278,512 B | ✅ | `stat -f %z` → `56278512` |
| Bundled `pyproject.toml` = DATA entry, `version = "0.29.16"` | ✅ | CArchive TOC 945 entries; entry tuple `(25357324, 1820, 3788, 1, 'x')` — typecode `x`=DATA; extracted head lines 1-6 show `version = "0.29.16"`, `requires-python = ">=3.13"` |
| PYZ module census | ✅ | `len(pyz.toc)` = **4717** — independent number vs build claim 4717, **delta 0** |
| PYZ `daemon` version constant | ✅ | `pyz.extract('daemon')` (`co_filename daemon/__init__.py`) recursive `co_consts` walk → `['0.29.16']` — **exactly one** |
| Version-bump negative control | ✅ | `0.29.15` count = **0**; whole-PYZ walk (all 4717 modules): the ONLY module containing any `0.29.x` constant is `daemon` → `['0.29.16']` |

Helper artifact: `/tmp/v02916-g1/g1_static.py`. Binary never executed in this gate.

## Gate 2 — Isolated boot smoke: PASS

Recipe: scratch root (`/private/tmp/v02916-smoke.pfr7LC`), binary **copied** + re-hashed (identical), `config.yaml` byte-identical (`cmp`) from BUILD worktree, `agents/` symlinked, 15 **named** `env -u` scrubs (per-variable, never glob), pins: `HOME`/`ENSEMBLE_DATA_DIR`/`DAEMON_LOG_DIR` in scratch, `ENSEMBLE_SELF_ENV=sandbox`, `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0`, `PLANE_SYNC_ENABLED=0`, `PLANE_MCP_ENABLED=0`, `POSTGRES_*=127.0.0.1:5432/ensemble/∅/ensemble_smoke_v02916` (dedicated fresh scratch DB on loopback PG 14.22; trust-auth shape from MAIN .env).

- **Port:** 8264, proven free pre-boot, bound `127.0.0.1` only. Port mechanism: config interpolation `config.yaml:112-113` (`server.port: ${PORT:-8088}`); binary accepts **no CLI flags** (`--help` boots the daemon; probe SIGTERMed cleanly at 15 s cap, zero orphans).
- **Boot duration: 4.4 s** server-start→first-200 (log-timestamp-exact: `05:14:31` banner → `22:14:35.387Z` livez), ~11.2 s spawn→200 (poll arithmetic ±1 s). Healthy class — **no minutes-class boot wedge** (the v0.29.9 gate-FAIL shape is absent).
- **/livez (verbatim):** `{"status":"alive","uptime_seconds":88.09…,"version":"0.29.16"}` → version surface ✅
- **Boot banner (exact):** `Starting Ensemble v0.29.16` ✅
- **/readyz:** `status=ready`, all four components true (database, queue_freshness, services, checkpoint_saver).
- Log hygiene: stdout tracebacks 0; `ensemble.log` 0 ERROR / 0 CRITICAL (see Anomaly A3 for DEBUG-level noise).

## Gate 3 — Caller-visible spot-check: N/A (by rule)

`/openapi.json` fetched (HTTP 200, 481,662 B): 40 distinct path prefixes, **zero** tool-invocation paths — no non-LLM route exists to call `compare_images`; tools are reachable only via an LLM-backed agent turn, which the gate rules forbid creating. Covering evidence: reviewer's tree-level string verification (review gate APPROVED, 2 cycles). Budget used ≈1 min.

## Gate 4 — Cleanup: PASS

- Genealogy before (ancestry walk from worker's own spawn): `68617` (/bin/sh wrapper) → `68622` (bootloader) → `68626` (uvicorn server) → `68793` (daemon-spawned context7 MCP child).
- SIGTERM root-down: all dead in 2 s, zero SIGKILL escalation; post-`ps` → header only (rc=1); `pgrep -fl ensemble-prod` → none.
- Port proof: `lsof -nP -iTCP:8264 -sTCP:LISTEN` → **empty** ✅
- Scratch PG DB `ensemble_smoke_v02916` → `DROP DATABASE` confirmed (`still_exists: False`) ✅
- Evidence preserved: `/private/tmp/v02916-smoke.pfr7LC` (logs, livez/readyz bodies, openapi.json, config copy).

---

## Anomalies & disclosures

- **A1 🟡 Build-interpreter provenance divergence:** artifact is a **CPython 3.13.3** build (cookie `pyvers=313`, `libpython3.13.dylib`, all C extensions `cpython-313-darwin.so`, dylib `PY_VERSION` string `3.13.3 [Clang 20.1.0]`), NOT 3.14.7. The 3.14.7 figure traces to nea-host staging lanes (e.g. `2026-11-09-v0297-staging.md`) and entered this gate via the dispatcher brief — the caller made no interpreter claim. Version truth unaffected; `requires-python >=3.13` satisfied. **Recommendation:** correct the release record's build-environment line to CPython 3.13.3 before operator consumption (leader's call). Process fix adopted: future gates derive the reader interpreter from the artifact cookie `pyvers`, never from lane docs.
- **A2 🟡 Ambient `PORT=9797` near-miss (defused):** the dispatch environment carried `PORT=9797` (prod install port); `config.yaml` interpolates `${PORT:-8088}` — an unpinned launch would have bound the scratch daemon against the prod port, and a *scrubbed* PORT would fall back to **8088 (self-system port)**. Explicit `PORT=8264 HOST=127.0.0.1` pins neutralized it. **Recipe hardening (recorded in LESSONS): boot smokes must always PIN `PORT`+`HOST`, never merely scrub.**
- **A3 🟢 Repeating DEBUG tracebacks (non-fatal):** `daemon.services.metrics_query.wiring.w3` logs `F18 roster read failed` / `F18 tombstone read failed` (~1/10 s while live, 9+9 over the window) with `TypeError: 'InMemoryMachineStore'/'TombstoneStore' object is not callable`. DEBUG-level only, 0 ERROR/CRITICAL, /livez + /readyz unaffected — likely fresh-empty-DB wiring shape. Flag for the metrics lane as follow-up; not a v0.29.16 blocker.
- **A4 🟢 CLI shape:** binary takes no flags (args ignored); port/host control is env-only via config interpolation. Probe artifact `help.txt` in scratch.

## Scope Decision

Artifact-only gate as commissioned: no test packs, no regression legs (source review gate APPROVED separately — 2 cycles), no ensure.md Core pack runs (no source change under test). The 5 caller gates map 1:1 to executed work. Boot-path verify-first discipline honored given the v0.29.9/v0.29.12 wedge history — the decisive probe (boot duration + version surface) is green.

## Documentation

- RESULTS: this file (NOT committed — HARD boundary; leader folds into finalize if desired)
- LESSONS: `LESSONS/2026-11-10-v02916-gate-recipe-hardening.md` (PORT/HOST pin rule; artifact-derived pyvers reader rule)
- KB: workers recorded the CArchive cookie/pyvers + census/version-constant recipe via `experience()`
- Code changes: **none** (read-only gate). Commit: n/a by boundary.

## Overall Status

- Gate 1 Version truth: ✅ PASS
- Gate 2 Boot smoke: ✅ PASS (4.4 s; version surface 0.29.16)
- Gate 3 Spot-check: ⚪ N/A (no non-LLM route; reviewer string evidence covers)
- Gate 4 Cleanup: ✅ PASS
- **TESTER GATE VERDICT: SHIP**
