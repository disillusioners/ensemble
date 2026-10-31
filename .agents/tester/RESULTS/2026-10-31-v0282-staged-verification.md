# STAGED VERIFICATION — releases/v0.28.2 (terminal-row status strand fix)

- **Date:** 2026-10-31 · **staged_at:** 2026-10-31T17:38:54Z
- **Artifact:** `/home/nea/agents-ensemble/releases/v0.28.2/ensemble-prod` — sha256 `31e722ae334271559bd9fd0cbaa8b5a10b874f98cc32a0483e7879de532e17be`
- **Lineage:** merge `6c49dc7e3` (parents `7bd8b123a` + `8bf190290`) → bump `0db6e8b677db257eaf73cacf9c7e7330c2ecfabb` → tag `v0.28.2` = annotated `01be88165faa0e3cd872ec575faa87ede78458b6` PUSHED
- **Staging worktree:** `/home/nea/ensemble-src-wt-stage-v0282` (detached @ `0db6e8b67`, both sidecars, clean)
- **Verifier:** tester — 4 parallel lanes (S1 static/git, S2 in-binary content, S3 fenced boot smoke, S4 pin mirror), 0 re-dispatches; functional gate for the same lane PASS-WITH-NOTES earlier today (RESULTS/2026-10-31-terminal-row-status-strand-functional-gate.md)
- **Live boundary at verification:** `current → releases/v0.28.0`; live daemon pid 227259 (since 10:20:26Z); `state.json` sha `ad7c5deecc776f5b166dcb7edfde151af38d840ea601d9e7802c36b3a206971c`

# VERDICT: ✅ STAGED-VERIFIED PASS-WITH-NOTES — CEREMONY-READY (NO promote performed; user 3-factor nonce ceremony owns the flip)

All 4 lanes PASS. Notes are fail-soft smoke observations + method disclosures, none touching the live system and none blocking the ceremony.

## 1. ARTIFACT INTEGRITY — PASS (S1, 8/8)
- **4-way sha:** staged dir == staging-worktree dist (re-hashed, sidecar never trusted) == manifest `binary_sha256` == sidecar `artifact_sha256` — all `31e722ae…e17be`.
- **Sidecars:** binary + FE, both `git_head=0db6e8b677db…cabb`, `git_dirty=false`; FE `artifact_sha256=5531cbe4…` cross-checked against the shipped `index.html` hash directly.
- **Manifest delta vs v0.28.1 = EXACTLY 4 scalars** (`binary_sha256`, `binary_version 0.28.1→0.28.2`, `version v0.28.1→v0.28.2`, `staged_at`). Key sets identical; payload counts agents 338==338 / frontend 93==93 / plugins 4930==4930; per-file maps added/removed/changed = 0/0/0; all three tree-shas identical; `config_sha256`/`launcher_sha256`/`known_schema_gen`/`pipeline_scripts_floor_commit (953603108…)` identical.
- **fe_index `5531cbe4…` byte-identical** (note: rides `frontend_manifest["dist/frontend/browser/index.html"]` in this manifest generation, not top-level — verified both sides).
- **Source-less payload:** dir = `agents/ config.yaml ensemble-prod frontend/ launcher.sh manifest.json plugins/` only; NO `scripts/`, NO `.env`, NO in-dir sidecars, NO `.staging.*`/`.aside.*` residue.
- **Tag/origin:** local `v0.28.2^{}` == remote peeled == `0db6e8b67…cabb`; annotated object `01be88165…` local==remote; `origin/latest` == bump commit (subject `chore(release): bump version to 0.28.2`, house form).

## 2. MIGRATIONS — PASS (S1)
- Git delta v0.28.1(bump `b5644424`)→staged `daemon/migrations/versions/`: **EMPTY**; lane ranges (`7bd8b123a..6c49dc7e3`, `6c49dc7e3..0db6e8b67`): **zero .sql** (merge touched 12 files, services+tests, +1499).
- Census: staged tree **115 files / 115 distinct prefixes / `uniq -d` empty**; **embedded-binary census (decompressed) also 115, set-equal** to the git census (`diff` empty).
- **rollback_safe=true source=explicit** (stage log `rollback_safe=true source=explicit=true`); standing-rule ruling: delta EMPTY ⇒ no new schema object ships ⇒ the v0.28.0/v0.28.1 binaries cannot reference one ⇒ **delta-empty-legitimate**; auto-rollback may fire on a failed gate.

## 3. LANE CONTENT IN-BINARY — PASS (S2; static, execution-free)
Method disclosure: raw `strings` is structurally false-negative here (PYZ = zlib-compressed marshalled bytecode; 0/14 markers even for legacy symbols). Authoritative counts come from a static `CArchiveReader → PYZ → raw marshal bytes` extraction (Python 3.14 helper, PyInstaller 6.19.0; neither binary executed).
- **Decompressed-PYZ staged-only pool = 2,147 strings; ALL 14 new lane markers present in staged and 14/14 ABSENT in live v0.28.0** (negative control sha `0a12e3f8…c2f0` verified against its manifest).
- T1: new fail-open log `turn-start terminal-row reactivation failed for %s…` staged-only; manager docstring markers `TURN-START chokepoint (9f0ed4f5 incident family)` + `TWO production lanes share this write` staged-only. T2: `_TERMINAL_REARM_STATUSES` ×2, `allowed_statuses`, `downgrade a terminal row`, hold log — all staged-only. T3: `terminal-row liveness COUNT failed` + `re-armed to WAITING_CHILDREN instead of` staged-only (`deferred_waiting_children` correctly NOT used as discriminator — pre-existing in both). D2.7: compiled module-docstring constant `NARROW OVERRIDE (2026-10-31, user-mandated…` staged-only + D2.5-FLIP compiled markers (`_B_FALSY_TOKEN_ORDER`, `_B_TOKEN_PROSE`, `DISABLED (explicit =0`).
- **`posture=None` proven at bytecode level:** disassembly of `_process_child_completion_and_notify_parent` — staged `LOAD_CONST None → CALL_KW ('posture',)` @ :2989 vs live `instance_meta.status` @ :2907. (The literal leaves no string; this is the compiled truth.)

## 4. ISOLATED BOOT SMOKE — PASS (S3; fully fenced)
- Scratch `/tmp/v0282-smoke.0jHVct` (binary COPY re-hashed identical; scratch-derived root; `cd $S` + `ENSEMBLE_DATA_DIR=$S/data` extra fences because the inherited cwd was the shared checkout with its own `./data/ensemble.json`); throwaway PG role+DB `v0282_smoke_25bf02f45441` (connect proof `is_prod=false`; creds never echoed); PORT **8153** (verified free; 8079/8088/9797 avoided); config = staged release's own config.yaml with bind lines hard-coded; **ambient live-poison (`POSTGRES_DB=ensemble_prod`, `POSTGRES_HOST=10.44.0.2`, `PORT=9797`, `ENSEMBLE_SELF_ENV=live`, `ENSEMBLE_UPGRADE_LIVE=1`) fully neutralized by per-variable scrub + explicit sets**; **`ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` PINNED explicitly** (the 2026-10-31 sweep-kills-live incident guard) — zero sweep/SIGTERM/SIGKILL lines in the boot log.
- **Results:** banner `Starting Ensemble v0.28.2` (×2, normal shape); PG engine line names the throwaway on 127.0.0.1:5432; **livez 200 `{"status":"alive","version":"0.28.2"}`**; **readyz 200 all components true, `reasons:[]`, `grace_active:false`, `leak_signal.detected:false`, pool 0/15**; stable on re-poll. (Giter's rc=3 SQLite breaker never in play — PG route as briefed.)
- **Teardown:** pids identity-verified (exe=$S/bin/ensemble-prod, cwd=$S) BEFORE any signal; SIGTERM → clean graceful exit ~2s (no SIGKILL), MCP pool drained, engine disposed; port freed; zero orphans (pgid scan); throwaway DB+role DROPPED (post-drop catalog empty); scratch removed.
- **LIVE FENCE:** `state.json` sha `ad7c5dee…971c` **byte-identical pre/mid/post**; `current → releases/v0.28.0` unchanged; live pid 227259 uptime continuous, livez 200 mid-flight; smoke bound to 127.0.0.1:8153 ONLY; `ensemble_prod`/remote PG never addressed (boot log contains zero references to the live install path).

## 5. PIN MIRROR (artifact ↔ gated code) — PASS (S4)
At the staged tree `0db6e8b67` (read-only runs, certified interpreter + import guard): 16-pin strand suite **16/16**; mirror-interplay pair **`test_running_root_keeps_jobitem_active` (:1327) + `test_waiting_children_root_keeps_jobitem_active` (:1375) both PASS**; child_reports negative legs **2/2**. Ledgered pre-existing turn_reconciler failures untouched (24 deselected). Note: the `-k "keeps_jobitem_active"` filter also swept a pre-existing sibling `:1277` (passed) — benign superset, filter-wording artifact of the dispatch, not an artifact property.

## Notes (non-blocking) + ceremony warnings for the user
1. **Ceremony command (house shape):** `ENSEMBLE_UPGRADE_LIVE=1 VERSION=v0.28.2 bash scripts/upgrade/promote.sh live --f2-verified-closed` (env-first). Tag is PUSHED and `origin/latest == 0db6e8b67 == staged tree` — zero tip-identity ambiguity; promote the staged artifact AS-IS (a re-stage would be refused by the L1 tip-identity guard, exit 78, and is unnecessary).
2. **Smoke observations (fail-soft, none blocking):** (a) agents directory absent in the binary-only scratch layout → `registered 0 custom agent(s)` + prompt-artifact drift check skipped — inherent to the smoke shape, the real promote exercises the full payload; (b) MetricsListener tried live-port+1 (9798), collided with the LIVE daemon's metrics port, and fail-soft disabled itself — zero disturbance (live held the port throughout); **future smokes: pin a metrics-port override in scratch config**; (c) built-in `plane` MCP row invalid ×2 — fail-soft, MCP is not a readiness component; (d) ambient `ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART=1` leaked (not in the scrub list) — 0 candidates on fresh DB, zero effect; added to the scrub-list for future smokes.
3. **Standing hazard re-confirmed:** agent worker shells are HEAVILY live-poisoned (`POSTGRES_DB=ensemble_prod`, `ENSEMBLE_UPGRADE_LIVE=1`, `PORT=9797` ambient). Both S1-baseline and S3-launch survived only because every boot/DB touch used the per-variable scrub + explicit sets. Any future unfenced boot from an agent shell would hit prod — the env-poison critical-note family stands.
4. Bump commit carries a dated `## [0.28.2]` CHANGELOG section alongside the house 4-file set (house-consistent, disclosed). Machinery-owned residue in `releases/` (stale rollback lock dir, `state.json.pre-*` backups) observed untouched.
5. `releases/` retains prior versions vs keep-3 — eviction is promote-machinery-owned; nothing evicted by this gate.

## Evidence paths
`/tmp/v0282-sv/s1/` (4-way hashes, manifest diff, censuses, tag/origin) · `/tmp/v0282-sv/s2/` (marker_counts.tsv, decompressed pools, staged-only pool, disassembly) · S3: `/tmp/v0282-smoke-boot-log-kept.txt` + `/tmp/v0282-smoke-fence-pre-kept.txt` (scratch itself removed post-report) · S4: run table in this file. Functional-gate evidence: `/tmp/trs-gate/` + RESULTS/2026-10-31-terminal-row-status-strand-functional-gate.md.
