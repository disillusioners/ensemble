# v0.28.5 STAGED VERIFICATION — wi-1fbee2ec (report-obligation visibility + WC-orphan detection)

**Commission:** wi-1fbee2ec · **Gate:** staged verification (final gate; functional GO already banked @ `40bfc6d43` + scoped-confirm)
**Artifact:** `/home/nea/agents-ensemble/releases/v0.28.5` · staging worktree `/home/nea/ensemble-src-wt-stage-v0285` (detached @ `600057a83bacf494e97e473b19bd2e1a0d2fe2f4`, sidecars intact)
**binary_sha256:** `a59e09a40958d267fb2942722830ff77673b84d38d212dfb28291f5c2f52a369` (4-way verified) · tag `v0.28.5` = annotated `58ba56ef9` → `600057a83` PUSHED
**Merge:** `8a884912a` (parents `9a984493a` kms-deadlock drift lane — already LIVE as v0.28.4 — + `40bfc6d43` our verified tip)
**Date:** 2026-11-01 · **Verdict: STAGED-VERIFIED PASS — CEREMONY-READY (NO promote; user 3-factor nonce ceremony owns the flip)**

---

## S1 — ARTIFACT INTEGRITY: PASS (10/10)

- **4-way sha**: release dir == staging-worktree `dist/ensemble-prod` == `manifest.json binary_sha256` == binary sidecar `artifact_sha256` — all `a59e09a4…1a369`. Sidecars `git_head=600057a83bacf494e97e473b19bd2e1a0d2fe2f4`, `git_dirty=false` (binary sidecar in `dist/`; FE sidecar at `frontend/dist/frontend/browser/index.html.build-provenance.json`, sha `5531cbe4…` — path note: NOT beside the dist binary).
- **Tag peel**: local `v0.28.5^{}` / `^{commit}` → `600057a83…`; `cat-file -t` → `tag`; remote `ls-remote` → annotated `58ba56ef9…` + peeled `600057a83…` server-side — PUSHED and verified.
- **Tip identity**: after `git fetch`, `origin/latest` == HEAD == tag target == bump commit `600057a83` (`chore(release): bump version to 0.28.5`) — **zero tip ambiguity**.
- **Manifest**: agents 338 / frontend 93 / plugins 4930 = **5361**; `rollback_safe=true`; `known_schema_gen=20261031_120002…`; `pipeline_scripts_floor_commit=953603108…`. (`fe_index_sha256` not a top-level scalar — carried by per-file map + FE sidecar, v0.27.8 precedent.)
- **Tree migration census** (staged tree): 119 `.sql`, **119 distinct prefixes**, `uniq -d` empty.
- **Payload source-less**: only `.prompt-artifacts.sha256`, `config.yaml`, `ensemble-prod`, `launcher.sh`, `manifest.json` + `frontend/` `plugins/` — no scripts/, no .env, no provenance inside, no `.staging.*` residue.
- **Staging worktree porcelain**: EMPTY (verified twice); detached at the tag.
- **Prompt-baseline**: house authority `scripts/protection/generate_prompt_artifact_baseline.py --check` → **`PROMPT-BASELINE: OK — 161 versioned artifact(s) match`**.

## S2 — ISOLATED BOOT SMOKE: PASS (6/6, fences held) — 2026-10-31 incident class fenced

Recipe: v0.27.13 §3 arena pattern + the post-incident **v0.28.2 §4 recipe (sweep pin)** — recovered from git (`5693bb4a6`) + worktree copy.

- **Fences (all hard-required, all respected)**: binary COPY at `/tmp/rt-v0285-s2/root/current/ensemble-prod` (sha re-verified `a59e09a4…`; never executed in place); **`ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` PINNED explicitly** (never `env -u`); `ENSEMBLE_SELF_ENV=sandbox`; 32-variable per-variable scrub (POSTGRES_*×5, DATABASE_URL, **plus discovered live-poison: `ENSEMBLE_UPGRADE_LIVE=1`, `ENSEMBLE_SELF_ENV=live`, `PORT=9797`, `OPENAI_*`×8, `EMBEDDING_*`×3, `_PYI_*`×4 pointing at the LIVE binary, HOME, OLDPWD**) then explicit sets: scratch `PORT=18079` (verified free), `ENSEMBLE_CONFIG` scratch copy, `ENSEMBLE_DATA_DIR` scratch, throwaway PG `v0285s2_smoke`, dead-loopback `OPENAI_BASE_URL`, scratch HOME; scratch metrics port `18080` pinned (9798-collision class); `timeout 240` hard cap; pid identity-verified (exe + cwd) BEFORE any signal.
- **Assertions**: banner **`Starting Ensemble v0.28.5`** ×2; livez 200 `{"status":"alive","version":"0.28.5"}` (~18s); readyz 200 — **all components true** (`database, queue_freshness, services, checkpoint_saver`), `reasons:[]`, `grace_active:false`, `leak_signal.detected:false`, MCP context7/webfetch/plane `ok`.
- **Lane surface (runtime)**: `ReportDeliveryRecoveryService started: lanes=[…, wc_orphan_detector=True]` — the detector lane REGISTERED live at boot; RDRS surfaces ×3. `obligation`/`SYNC_MINT`/`kms`/`redact`: honest zero runtime hits (fresh boot fires no child-terminalization/KMS events) — **S3 owns the binary-level proof** (delivered, below).
- **Sweep safety**: 10 benign sweep lines, all `candidates=0/alive=0/reaped=0`, **zero SIGTERM/SIGKILL lines**; isolation proven (scratch PG engine line; `install_dir=<none — dev/unresolved>`; derived agents dir = scratch; **0 references** to the live install/prod-DB/9797 in the boot log).
- **Teardown**: SIGTERM → graceful ~2s (`Graceful shutdown complete`), no SIGKILL, zero orphans, throwaway DB+role DROPPED (census 0|0), ports freed, scratch removed; record copies kept.
- **LIVE FENCE**: `state.json` (`/home/nea/agents-ensemble/releases/state.json`) sha `72be3af7…132273` **PRE==MID==POST**; `current → releases/v0.28.4` untouched; :9797 livez 0.28.4, PIDs 551104/551108 same, uptime monotonic; zero-writes sweep → only the live daemon's own log files (its continuous logging). **ZERO install/payload writes.**

## S3 — STAGED-CONTENT PROOF AT BINARY LEVEL: PASS (7/7, no PARTIAL)

Reader: uv-managed **cpython-3.14.7** (pyvers=314 artifact; certified 3.13 cannot read it) + **native PyInstaller `CArchiveReader`/`ZlibArchiveReader` as the independent second reader** — dual-reader cross-validated (4649==4649 PYZ TOC keys, 9/9 key modules identical, full string-pool equality 235,018==235,018, symbol matrix 14/14 agreement BOTH binaries).

- **Discriminating test (ours)**: 10/10 lane symbols **PRESENT in staged / ABSENT in live v0.28.4** — `count_outstanding_report_obligations`, `count_outstanding_for_subtree`, `DEFERRED_REASON_SYNC_MINT`, `outstanding_report_obligations`, `report_delivery_recovery_lane_wc_orphan_detector`, `wc_orphan_detector`, `wc_orphan_direct`, `wc_orphan_pure_chain`, `suppressed_root_terminal`, `report_obligation_read_error`. Module locations match the source tree.
- **Drift (kms/redaction)**: 4/4 symbols (`_resolve_get_store`, `_GET_STORE_MAX_ATTEMPTS`, `_store_import_latched`, `_get_store_fn` — derived from `git diff f7039c3ff..9a984493a`, 3 files: kms_lite.py, util/log_redaction_filter.py) PRESENT in staged AND in live — exactly the rides-along expectation (drift already live as v0.28.4).
- **Staged-only pool = exactly 78 strings**, all lane-shaped (PART 3/4 docstrings, `_classify_wc_orphan`, `WcOrphanDetectorLaneResult`, D1/D3 log strings, `'0.28.5'`) — the compiled payload delta is precisely the lane + version const.
- **Kill-switch default at BYTECODE**: `dis` of `daemon.config::ServicesConfig` — `LOAD_CONST True` + lane description const → `CALL_KW Field` → `STORE_NAME report_delivery_recovery_lane_wc_orphan_detector` — **default True compiled-proven** (negative control: no binding in live at all).
- **Embedded migration census (CArchive TOC, both binaries)**: staged 119/.distinct 119, live 119/119, **name-set and prefix-set deltas EMPTY** — rollback-adjudication corroboration (=1 legitimate).
- **Additive-only**: old-tree grep of our surface at `f7039c3ff` → ZERO files; `.sql` diff across the ENTIRE staging range (`f7039c3ff..600057a83` and `9a984493a..600057a83`) → empty — lane .sql contribution exactly 0.

## S4 — CEREMONY INPUTS: PASS

- **Promote command (verbatim)**: `ENSEMBLE_UPGRADE_LIVE=1 VERSION=v0.28.5 bash scripts/upgrade/promote.sh live --f2-verified-closed`
- **Tag/remote**: annotated `58ba56ef9` → `600057a83` PUSHED; `origin/latest == 600057a83` (zero tip ambiguity at ceremony time).
- **Rollback adjudication (tag annotation, verbatim)**: "ROLLBACK ADJUDICATION (standing rule, source=explicit): migration delta EMPTY — lane zero-DDL (git diff f7039c3ff..40bfc6d43 daemon/migrations/versions/ empty), drift zero-DDL, merged tree vs live v0.28.4 (62627265f) empty; behavior-only payload; report_injections substrate predates; old binary references no new symbols => ENSEMBLE_ROLLBACK_SAFE=1; auto-rollback may fire safely on a failed gate." — manifest `rollback_safe=true` concurs; S3's embedded 119==119 census independently corroborates.

## S5 — DISCLOSURES (none blocking)

1. S2 runtime logs carry no obligation/SYNC_MINT/kms lines (no such events at fresh boot) — payload presence proven at BINARY level (S3) + lane registration surface live at boot (S2 `wc_orphan_detector=True`).
2. Binary-only scratch boot → "Agents directory does not exist" (0 custom agents) + `skill_sources.roots:[]` — inherent smoke shape (v0.28.2 note a).
3. Path corrections vs the brief: `current` symlink at install ROOT (not `releases/current`); `state.json` at `releases/state.json`; FE sidecar under `frontend/dist/...`; `fe_index_sha256` not top-level in manifest. Value-bearing content unchanged.
4. `releases/` holds v0.28.0/2/3/4/5 (no v0.28.1 — superseded staged-unpromoted, pre-existing).
5. Live state.json is an actively-written journal; MID==PRE==POST held across the smoke window regardless.
6. S3 quick-probe false alarm (bare-filename vs full-path comparison) self-caught; corrected census shows `known_schema_gen` present in both binaries.

## Verdict

**STAGED-VERIFIED PASS — CEREMONY-READY.** Artifact integrity 4-way + tag + tip zero-ambiguity; isolated boot smoke green with every incident-class fence respected and the live boundary byte-identical; the staged binary carries exactly the wi-1fbee2ec lane at bytecode level (10/10 discriminating symbols, kill-switch default True, 78-string lane-shaped delta) with the kms drift riding as expected; migration delta EMPTY (rollback-safe =1, source=explicit). **NO promote performed — the user's 3-factor nonce ceremony owns the flip.**

*Workers: S1+S4 `746db38a` · S2 `be105817` · S3 `9eda2935` (dispatching tester `cd2df051`). Evidence working copies: `/tmp/wi1f-v0285-s1-20261101/`, `/tmp/wi1f-v0285-s2-{boot.log,readyz.json,livez.json,fences.txt}`, `/tmp/wi1f-v0285-s3-{logs,scratch,reader}/`.*
