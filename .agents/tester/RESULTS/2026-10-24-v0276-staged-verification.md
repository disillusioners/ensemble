# v0.27.6 STAGED-VERIFICATION — unit-secret-exposure lane (2026-10-24)

**Staged release:** `/home/nea/agents-ensemble/releases/v0.27.6` · binary sha256 `18f2d025f319ca61458f077c367e310e23cf9a9baaa210eb3b17e0515f987b08` (**3-way verified**: disk re-hash == manifest `binary_sha256` == sidecar `artifact_sha256`) · tag `v0.27.6` local object `9c81c0910` → **peels to `e547b71ec`** · bump `e547b71ec` == `origin/latest` (fresh fetch) == stage-worktree HEAD (`/home/nea/ensemble-src-wt-stage-v0276`, detached) · merge `85421056b` (parents `0c394f8a7` + `49e912454`, `--no-ff`) · evidence rider `49e912454` (`.agents/tester/**` only, 2 files).
**Upstream gate:** functional gate PASS-WITH-NOTES · GO (this lane's own evidence: `RESULTS/2026-10-24-unit-secret-exposure-verification.md`, riding as rider `49e912454`).

## VERDICT: ✅ **PASS-WITH-NOTES — STAGED-VERIFIED, CEREMONY-READY** (NO promote performed; the user's 3-factor nonce ceremony owns the flip)

All checklist items verified by independent re-derivation (never trusted the recorded values). One adjudication correction (§2, rollback-posture premise) — non-blocking, additive-only posture intact.

---

## §1 Artifact integrity (worker `bb7a650a`, read-only, A1–A9 all PASS)

| Check | Result |
|---|---|
| **A1 — 3-way sha** | **PASS** — disk re-hash (80,717,712 B) == manifest == sidecar `dist/ensemble-prod.build-provenance.json` artifact sha, all `18f2d025…987b08` (sidecar built 2026-10-24T04:03:30Z via `pyinstaller:ensemble.spec`) |
| **A2 — sidecar fields** | **PASS** — binary sidecar `git_head=e547b71ec…`, `git_dirty=false`; FE sidecar (`frontend/dist/…/index.html.build-provenance.json`) same head, sha ties to the manifest FE entry `5531cbe4…` |
| **A3 — L1 tip-identity** | **PASS** — fresh `git fetch origin`: `origin/latest` == `e547b71ec` == stage worktree HEAD (`git describe --exact-match` = `v0.27.6`); rider `49e912454` IS an ancestor; lane worktree clean @ `49e912454` pre/post |
| **A4 — tag state** | **PASS** — remote `refs/tags/v0.27.6` peels to **`e547b71ec`** (landed before first look; re-checked at end — nothing moved). Pre-existing mis-point **v0.27.5 → `9fc95176b`** confirmed and **UNTOUCHED** (morning cleanup item). LOCAL tag v0.27.5 peels to `6bebb9ba0` (`__version__="0.27.5"` verified) — used for corroboration only |
| **A5 — payload hygiene** | **PASS** — release dir = `agents/` (338) + `frontend/` (93) + `plugins/` (4930) + `config.yaml` + `launcher.sh` + `ensemble-prod` + `manifest.json`. NO `scripts/`, NO `.env`, NO `*.build-provenance.json`, NO `.staging.*`. **Security-lane specific: zero unit-generator scripts in the payload** (explicit scan for adopt-unit/deploy/stage/lib/promote/rollback.sh → 0 hits; the fix reaches operators via the updated checkout post-merge per T3.7). Nuance recorded: 5 dirs named `scripts` under `plugins/opendesign/copy_freely/design-templates/*/` are vendored plugin template content inside the 4930 manifest-tracked files — not the upgrade pipeline |
| **A6 — full manifest re-hash** | **PASS — 5361/5361 clean** (agents 338 / frontend 93 / plugins 4930; 0 mismatches, 0 missing) |
| **A7 — manifest delta vs v0.27.5** | **PASS — exactly the claimed shape** — changed scalars EXACTLY 5: `binary_sha256` 26a56ea6…→18f2d025…, `binary_version` 0.27.5→0.27.6, `version` v0.27.5→v0.27.6, `staged_at` →2026-10-24T04:10:23Z, `agents_tree_sha256` 852b3e6b…→5870901c… · agents map 339→338 (deleted exactly `gaia/scenario/npx.md`; modified exactly 8: `ari/rule.md`, `gaia/{meta.json,rule.md,soul.md,tools_note.md,workflow.md}`, `leader/meta.json`, `leader/soul.md` — gaia is ×5). **`launcher.sh` + `config.yaml` ABSENT from the delta** (pinned `launcher_sha256 a3a44461…` / `config_sha256 7bab75e9…` IDENTICAL both manifests) — this security lane touched neither ✓ · frontend/plugins maps + tree hashes, `known_schema_gen` (`20261022_000002_create_wedge_captures.sql`), `rollback_safe` all identical ✓ · **"NOT this lane" proven at blob level**: all 8 modified files blob-identical between `0c394f8a7` and `e547b71ec`; entries == git blobs at `e547b71ec` (8/8 tie-out) — inherited from the gaia-redefinition lineage that landed before this lane branched |
| **A8 — migration delta (embedded .sql census, binary level)** | **PASS — 109 == 109, identical** — method: `strings -n 20 <binary> \| grep -E '(^\|/)[0-9]{8}_[0-9]{6}_[a-z0-9_]+\.sql$' \| sed 's#^.*/##' \| sort -u` (suffix-anchored — the CArchive stores names WITH repo-path prefix; the strict full-line pattern yields 0 on BOTH binaries, discrepancy disclosed). v0.27.5 census 109 == v0.27.6 census 109, `diff` identical, `known_schema_gen` present. Git corroboration: local `v0.27.5^{}`=6bebb9ba0, `git diff 6bebb9ba0..e547b71ec -- daemon/migrations/versions/` EMPTY, `ls-tree` 109==109. Baseline spot-check: v0.27.5 binary re-hashes to its own manifest `26a56ea6…` |
| **A9 — lineage** | **PASS** — rider `49e912454`: exactly 2 files, `.agents/tester/**` only (PACKS.md +15, unit-secret-exposure RESULTS +136) · merge `85421056b` parents EXACTLY `0c394f8a7`+`49e912454` (--no-ff, author giter) · bump `e547b71ec`: EXACTLY the 4-file house set, 1 hit each, `__version__="0.27.6"` · **true lane diff** (`9fc95176b..49e912454`): exactly 20 files — scripts/×4, tests/×1, docs/runbooks/×2, .agents/×13 — **NO daemon/, NO launcher.sh, NO config.yaml, NO agents/, NO migrations** |

## §2 Rollback posture (A10) — adjudicated WITH CORRECTION

- **Migration delta EMPTY (109==109, binary-census-proven)** — no schema mutation ships; no down-migration exists or is needed. Additive-only posture holds.
- **Correction to the claimed premise** ("daemon code otherwise identical vs v0.27.5"): measured daemon delta `6bebb9ba0..e547b71ec -- daemon/` = **3 files, not 1** — `daemon/__init__.py` (version) **+ `daemon/tools/db_tools.py` (SELECT-replace guard, `7874feda6`) + `daemon/tools/filesystem.py` (edit_file phantom-success race fix, `d6d7f930d`)** — both INHERITED from the edit-file-race-fix lane (`eb0340561`), landed on latest BETWEEN the v0.27.5 bump and this lane's base. **This lane's own daemon diff (`9fc95176b..49e912454`) is EMPTY.**
- **Adjudication:** `rollback_safe=true source=explicit` **STANDS** — the old binary remains schema/state-compatible (empty migration delta, no DDL, no down-migration on rollback). The corrected framing: rollback is **fix-loss, not incompatibility** — a rollback to v0.27.5 would re-expose the edit_file phantom-success race and the SELECT-replace guard gap (both inherited fixes, already live on `latest` since 2026-10-23); a rollback to the operative post-promote `previous` (machinery re-points `previous`→v0.27.4 at promote time — `state.json` is promote-machinery-owned) additionally re-exposes the v0.27.5 wedge-capture lineage. Disclosed for the ceremony record; no blocking hazard.

## §3 Boot smoke from the STAGED binary (worker `9a2f7761`) — **PASS 4/4**

Route: `systemd-run --user` transient `ens-v0276-smoke-c7c7d2`, **zero `--setenv`** (user-manager env verified clean of PORT/POSTGRES_*/HOST/ENSEMBLE_*). Fixture: mktemp `$WORK` + `FAKE_HOME` + escape guards; staged binary **copied** (release dir untouched) and sha-verified `18f2d025…987b08` == claimed; `current → releases/v0.27.6`; launcher.sh + config.yaml from `git show e547b71ec:`; scratch `.env` mode 600 (PORT=13188 free-verified, HOST=127.0.0.1, throwaway PG DUMMYs, `ENSEMBLE_SELF_ENV=sandbox` explicit); throwaway PG16 role+DB (DROP-verified); per-variable POSTGRES_* scrub on every invocation (worker shell was again prod-poisoned — `PORT=9797`, `POSTGRES_*=10.44.0.2`, `ENSEMBLE_SELF_ENV=live` — scrubbed per-variable every time).

| Assertion | Evidence |
|---|---|
| Banner | `Starting Ensemble v0.27.6` — unit journal AND scratch `data/logs/ensemble.log` |
| livez | **200** `{"status":"alive","uptime_seconds":4.1…,"version":"0.27.6"}` |
| readyz | **200** — database/queue_freshness/services/checkpoint_saver all true, draining false |
| Version | **0.27.6** in livez payload |

- **cgroup isolation proven:** daemon tree `0::/user.slice/…/ens-v0276-smoke-c7c7d2.service` vs live `/proc/3522801/cgroup` = `/system.slice/ensemble-main.service`.
- **Live state.json watch (cn cc3d107b caveat): NO CHANGE** — sha `abac3978…45d15` byte-identical before/mid/after (baseline current=v0.27.4, previous=v0.27.1 confirmed); **no supervision_boot/pending_actions rows written; nothing to disclose; nothing touched** (the path-derived install root resolved to the scratch fixture).
- **Live-untouched fence (all four re-verified post-teardown):** livez `:9797` → 0.27.4 uptime-continuous; MainPID **3522801** unchanged; `current → releases/v0.27.4` unchanged; state.json sha unchanged.
- **Cleanup verified:** transient stopped + reset-failed; port 13188 freed; throwaway DB+role dropped (0/0 counts); fixture removed under double escape guard; zero scratch processes (the one `pgrep` hit was the teardown shell itself — false positive, re-checked); no `/tmp/v0276-smoke.*` residue.
- No real-LLM nodes (boot + health polls only). Benign deviations disclosed: PG admin lane via unix socket (peer auth; postgres superuser TCP-gated); one setup-script SIGPIPE retry (resumed, all guards re-run).

## §4 Tag & ref state (final)

- `refs/tags/v0.27.6` → peel `e547b71ec` ✓ (pushed by the giter per the f13a4f59e convention adjudication; verified twice, nothing moved).
- **v0.27.5 remote mis-point (`9fc95176b`) untouched** — pre-existing morning cleanup item (owner: giter/user).
- `origin/latest` == `e547b71ec` — never modified by this gate.

## §5 Notes (non-blocking)

1. **Rollback-premise correction (§2)** — the only material note; additive-only posture intact, `rollback_safe=true` stands.
2. The manifest "8 modified" list is gaia ×5 (not ×4 as the dispatch shorthand said) + ari/rule.md + leader/meta.json + leader/soul.md — total 8M+1D exactly as claimed; all inherited (blob-level proof).
3. Worker shells on this host are consistently **prod-poisoned** (both tonight's boot-smoke and mutation-leg workers) — per-variable scrub discipline confirmed load-bearing (KB-recorded).
4. `keep-3` over-retention: `releases/` holds v0.27.1/v0.27.3/v0.27.4/v0.27.5/v0.27.6 — promote-machinery-owned, nothing evicted by this gate.
5. Standing morning items carried from the functional gate: §5a live remediation (form validated; runbook conforms), `dev-evidence.md:259` A2-pin whitelist amendment, stage-freshness create-only coverage pin, A5 75-vs-78 taxonomy line, remote v0.27.5 tag mis-point.

## §6 Worker ledger

| Worker | Instance | Skill | Scope | Result |
|---|---|---|---|---|
| v0276-artifact-integrity | `bb7a650a-e727-4775-a626-931ccb3e1793` | none (infra) | A1–A10 | PASS (A10 correction for adjudication) |
| v0276-staged-bootsmoke | `9a2f7761-c43a-4a99-bfac-1296d902d470` | e2e-test | boot smoke + fences | PASS 4/4 |
| tester (this instance) | `5176b053-3b9a-4265-90a3-9227761748d4` | — | dispatch, adjudication, RESULTS | this doc |

**Evidence committed on branch `tester/v0276-staged-verification` (from `e547b71ec` == origin/latest), pushed — NOT merged to latest (leader sweep owns it).**

**Overall: integrity ✅ · tags ✅ · hygiene ✅ · manifest deltas ✅ (launcher/config untouched) · migrations 109==109 ✅ · boot smoke ✅ · live untouched ✅ · rollback posture ✅ (with §2 correction) — STAGED-VERIFIED, CEREMONY-READY.**
