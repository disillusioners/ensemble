# v0.29.16 STAGED ARTIFACT VERIFICATION — promote-readiness TESTER GATE (re-gate)

- **Date:** 2026-11-10 (gate window ~22:40–23:10 UTC) · **Workers:** `9b9a9757` (static) · `5cf6758e` (boot+cleanup), both revived with first-gate context
- **Verdict: SHIP — PROMOTE-READY** (all executable gates PASS; zero NO-GO flags; two caller-side promote-runbook checks flagged, neither an artifact defect)
- **Staged artifact:** `/Users/nguyenminhkha/agents-ensemble/releases/v0.29.16/ensemble-prod` · sha256 `5457822dcfc55ed5fb8a8ae2046edfe8f3e94b2b396082f14df51f015ffdc23c` · 56,279,856 B
- **Cross-ref:** first-build record `2026-11-10-v02916-artifact-verification.md` (sha `00f647e9…`, **SUPERSEDED** by this artifact — see Anomaly B; its evidence transcript is now the only surviving record of that build, since the rebuild overwrote it in place).
- **Boundary compliance:** live install dir READ-ONLY (hash/read only; boot ran a scratch COPY); live daemon pid 51569 proven untouched (identical `lstart` before/after); no promote/rollback/journal/current writes; no make/deploy/stop/upgrade; MAIN untouched; both RESULTS files left **untracked** (path-limited finalize commit is the giter's next step).

---

## Re-gate 1 — Version truth + provenance (STAGED binary, never executed): PASS

Reader selection per A1 rule: CArchive cookie read FIRST with a plain interpreter (no PyInstaller import) → `pyvers=313`, `libpython3.13.dylib` → derived reader CPython 3.13. Dual routes (build-venv 3.13.3/PyInstaller 6.19.0 + uv 3.13.3/6.22.3), identical results.

| Assertion | Result | Evidence |
|---|---|---|
| sha256 == `5457822d…dc23c` | ✅ | `shasum -a 256` exact; size `56279856` (+1,344 B vs first gate — decomposition in Anomaly C) |
| Bundled `pyproject.toml` (typecode `x`=DATA, 3,788 B) | ✅ | `version = "0.29.16"` |
| PYZ module census | ✅ | `len(pyz.toc)` = **4717 == first gate 4717, delta 0** |
| `daemon` version constant | ✅ | recursive `co_consts` → `['0.29.16']` exactly one |
| `0.29.15` remnants | ✅ | 0 in `daemon` + 0 whole-PYZ (all 4,717 modules; only module with any 0.29.x constant is `daemon`) |
| Manifest corroboration | ✅ | `manifest.json` fields all match independent verification: `binary_sha256` re-hashed ✅; `launcher_sha256 6723d1b82b23f75c…` — staged `launcher.sh` (204,307 B) re-hash **identical** ✅; `config_sha256` re-hash identical ✅; tree hashes sampled 12/12 (agents/frontend/plugins) ✅; `version v0.29.16`, `binary_version 0.29.16`, `staged_at 2026-11-10T22:37:18Z`, `rollback_safe true` |
| Tag-content proof | ✅ | every repo-mirrored DATA entry matched to tag `ef8eda0e` by **git blob OID: 139/139 exact, 0 mismatches** (other 727 entries environment-derived); provenance sidecar `ensemble-prod.build-provenance.json`: `git_head ef8eda0e…, git_dirty false, build_at 2026-11-10T22:36:18Z, artifact_sha256 5457822d…` |

## Re-gate 2 — Isolated boot smoke (scratch COPY of the staged binary): PASS

Recipe identical to first gate with A2/A3 lessons applied: new scratch `/private/tmp/v02916b-smoke.t5h6nS`; copy re-hashed identical; config.yaml byte-identical (`cmp`) from BUILD worktree; agents→BUILD worktree symlink; 15-var named `env -u` scrub (re-enumerated: no new ambient matches; ambient `PORT=9797` hazard still present — **PINNED** `PORT=8264 HOST=127.0.0.1`, bound loopback only); pins incl. `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0`, `PLANE_SYNC_ENABLED=0`, `PLANE_MCP_ENABLED=0`; fresh scratch DB `ensemble_smoke_v02916b` (loopback PG).

- **Boot duration (exact, A3-fixed T0):** spawn→live **17.18 s** (uptime-anchored: `T1 1794350651.855 − uptime 3.236761`), server-start→live **5.6 s** (±0.5 s log resolution). Slower spawn figure than gate-1 (~11 s) fully explained by **cold extraction of the fresh rebuild** (gate-1's copy had been warmed by its `--help` probe); server-start phase 5.6 s ≈ gate-1's 4.4 s. **Healthy class; no minutes-class wedge; 240 s deadline untouched.**
- **/livez (verbatim):** `{"status":"alive","uptime_seconds":3.2367610931396484,"version":"0.29.16"}` ✅
- **Banner:** `05:44:03 - daemon.api - INFO - Starting Ensemble v0.29.16` ✅
- **/readyz:** `status=ready`, all 4 components true (database, queue_freshness, services, checkpoint_saver) ✅
- Log hygiene: stdout tracebacks 0; 8 DEBUG F18 `TypeError` lines (metrics_query.wiring.w3, the known non-fatal A3 noise, unchanged shape); **0 ERROR / 0 CRITICAL**.
- **Non-interference proof:** pid 51569 `/bin/bash …/launcher.sh` — `ps` BEFORE == AFTER (identical `lstart` Tue Nov 10 01:12:57 2026, stat S) → neither restarted nor signaled. Zero writes under `~/agents-ensemble` beyond read-only cp/hash.

## Re-gate 3 — Cleanup: PASS

Genealogy `95511→95515→95526→95716` (incl. daemon-spawned context7 MCP child), TERM root-down, all dead in 2 s, no SIGKILL; post-`ps` header-only; `lsof -nP -iTCP:8264 -sTCP:LISTEN` → **empty**; `DROP DATABASE ensemble_smoke_v02916b` → `still_exists: False`; scratch preserved (`/private/tmp/v02916b-smoke.t5h6nS`: t0.txt, launch.sh, logs, livez/readyz bodies). Post-cleanup re-probe: pid 51569 + pointer state identical to pre-boot.

---

## Anomalies & rulings

- **A 🟡 — Rebuild overwrote the first-gate artifact in place.** `…wt-build-v02912/dist/ensemble-prod` now hashes `5457822d…` (was `00f647e9…`); the first-gate binary survives nowhere (install dir, build worktree, /tmp). The worker's interim "TOC diff: 0 differences" was a self-comparison and is **retracted/VOID**; the reconstruction used the gate-1 transcript instead. Ruling: no operational exposure (nothing was pushed/staged from the first build); the first-gate RESULTS file is the surviving evidence record — archival write vindicated. (Worker filed `report_anomaly` c8c35d1d.)
- **B 🟡 — First build was content-unattestable and provably ≠ tag in ≥1 file.** Gate-1's TOC recorded `daemon/migrations/versions/20260927_000001_create_maintenance_runs_table.sql` at uncompressed 4,087 B; the staged build has 3,387 B **and matches tag `ef8eda0e` by blob OID** — so the first binary embedded a different version of at least that file, and it carried **no build-provenance sidecar**. Ruling: the first-gate PASS is retroactively a **version-truth-only** record (its version surfaces were genuine); it is **superseded and closed** by the staged artifact, which is strictly stronger (sidecar + 139/139 tag-exact). Escalated to caller for the release record; mechanism of the first build's divergence is indeterminable (artifact gone). Lesson appended: artifact gates require provenance sidecar + tag blob-OID matching, and must pin the sha before any in-place rebuild can occur.
- **C 🟢 — "+1,344 B = build-timestamp" attribution corrected.** Decomposition: archive_length +1,299 (environment-derived window AFTER offset 19,199,282 and BEFORE migrations: `base_library.zip`, `certifi/cacert.pem`, dependency `.so`s — all repo-mirrored entries in-window are tag-exact) + ~45 B bootloader/code-signature. `PYZ.pyz` stored length identical (29,890,048 both); no build-timestamp constant found in PYZ. **App content is bit-identical to the tag; the delta is dependency/stdlib build noise, not a timestamp.**
- **D 🟡 — Live pointer ABSENT at both locations; `releases/` layout never promoted; live root is a v0.29.8-era flat build.** `readlink ~/agents-ensemble/current` and `~/agents-ensemble/releases/current` both rc=1; `releases/state.json` = `{"current":null,"previous":null,"in_flight":null,…,"history":[]}`. Install root still holds the flat-layout binary (55,920,608 B, mtime 2026-11-10 00:31) whose embedded content is **v0.29.8** (pyproject + PYZ constant, CArchive 953 entries, census 4,687); root `launcher.sh` 144,301 B vs staged 204,307 B. Rulings: (1) pointer absence = **expected first-promote state**, not a defect — but the promote path must be the one that CREATES the root-level `current` (nea-host precedent: `INSTALL_DIR/current -> releases/vX.Y.Z`); caller should confirm the promote runbook handles pointer-creation. (2) First flip has **no in-pipeline rollback target** (`previous:null`, empty history) — operator should note the manual fallback (root flat files remain on disk). (3) Live-version gap is larger than assumed (live=v0.29.8, not v0.29.12) — promoting v0.29.16 closes it; worth an operator sanity-check on why the root binary embeds 0.29.8.

## Scope Decision

Promote-readiness gate on the STAGED artifact only, per commission: no test packs, no regression legs (source review APPROVED; first gate + this gate both boot-probed). The decisive probes — version truth (static, dual-reader, tag-OID) and boot behavior (isolated, non-interfering) — are green.

## Documentation

- RESULTS: this file + first-gate file (both **untracked** — giter's path-limited finalize commit is next; do NOT commit from tester per brief)
- LESSONS: appended rule 3 to `LESSONS/2026-11-10-v02916-gate-recipe-hardening.md` (provenance-sideoof requirement / in-place-rebuild overwrite hazard)
- Code changes: **none** (read-only gate). Commit: n/a by boundary.

## Overall Status

- Re-gate 1 Version truth + provenance: ✅ PASS
- Re-gate 2 Boot smoke (non-interfering): ✅ PASS (17.18 s spawn→live; version 0.29.16)
- Re-gate 3 Cleanup: ✅ PASS
- **TESTER GATE VERDICT: SHIP — the staged artifact `5457822d…` is PROMOTE-READY.** Caller-side flags (not artifact defects): promote-runbook pointer creation + no-rollback-target on first flip (Anomaly D), and release-record amendment for the superseded first build (Anomaly B).
