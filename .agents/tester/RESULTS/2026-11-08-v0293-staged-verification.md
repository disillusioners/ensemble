# v0.29.3 STAGED VERIFICATION — source-gc lane (board wi-ed230eb4)

- **Staged artifact:** `/home/nea/agents-ensemble/releases/v0.29.3` · staging worktree `/home/nea/ensemble-src-wt-stage-v0293` detached @ `8730bfb8a` · tag `v0.29.3` = annotated `2e24f5ec3` → `8730bfb8a` PUSHED (peels local AND origin) · origin/latest = `8730bfb8a`.
- **Binary sha (4-way, independently recomputed):** `4de6200f6af4b9fd0a1057ddbcc7c19e7951e0d6f227d3ae134ebf055b68b086` — release dir == stage-worktree dist == manifest == sidecar.
- **Executor:** worker `dbc64097` (revived; same instance that ran this gate's G7/G8 PG legs). Evidence: `/tmp/sgc-sv/`. Flake ledger: **empty** — every leg first-shot.
- **Prior gates:** functional gate NO-GO @ `7b6fa2cdf` → fix @ `fadb2dde1` → scoped re-gate **GO** (`d7fbcaf13` evidence riders on the branch) → this leg verifies the FROZEN bytes.

## VERDICT: **PASS — CEREMONY-READY** (all S1–S5 legs green; the Emperor's 3-factor nonce ceremony owns the flip)

| Leg | Verdict | Evidence |
|---|---|---|
| **S1.1** 4-way sha | PASS | recomputed `4de6200f…68b086` at all four points |
| **S1.2** sidecars | PASS | binary sidecar `git_head=8730bfb8a…` `git_dirty=false` `artifact_sha256=4de6200f…`; FE provenance via manifest `frontend_manifest` (fe_index `7a050d4e…` 3-way: stage == manifest == payload index.html); config.yaml `87a037f5…` + launcher `c4f640e1…` match manifest |
| **S1.3** tags | PASS | `v0.29.3` annotated `2e24f5ec3` peels → `8730bfb8a` locally AND on origin; annotation carries rollback adjudication + no-promote disclosure |
| **S1.4** migration census | PASS | source 123 files / 0 dup prefixes; embedded binary 123 (CArchiveReader, py 3.14) / 0 dupes; `20261107_122648_create_source_gc_archive_tables.sql` PRESENT-staged / ABSENT-live (live v0.29.1 census 122) → delta exactly +1 |
| **S1.5** payload source-less | PASS | payload = agents/ config.yaml ensemble-prod frontend/ launcher.sh manifest.json plugins/ only — no scripts/, .env, daemon/, .git, .staging.*, build-provenance files |
| **S1.6** prompt baseline | PASS | `PROMPT-BASELINE: OK — 181` on BOTH the stage tree and the payload agents tree |
| **S2** isolated boot smoke (frozen binary) | **PASS 12/12** | binary COPIED to scratch; `ENSEMBLE_SELF_ENV=sandbox` + `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` PINNED; `env -i` allowlist (ambient ensemble_prod/9797/live poison confirmed present + scrubbed per-variable); throwaway PG `ensemble_smoke_sgc_sv` created→dropped (catalog 0); scratch port 10093 bind-probed; banner `Starting Ensemble v0.29.3`; `/livez`+`/readyz` 200 all-components-true; **`[SourcesGC] started: enabled=True dry_run=False interval=3600s retention=14d batch_cap=25`** (shipped defaults); both archive tables + all 3 indexes materialized; first tick on empty DB = **no-op SILENT (§8.5 shape: `[SourcesGC]` line count 1, work-line count 0)**; SIGTERM rc=143, port freed, no orphans; **state.json byte-fence `528af13c…3aba` PRE==MID==POST** |
| **S3** one-way content proof (bytecode, 3.14 reader) | PASS | `daemon.services.sources_gc_service` present-staged (consts 112; `gc_archive_and_delete`/`gc_find_candidates` =1) / absent-live; 5 `sources_gc_*` knob fields =1/=0 in `daemon.config`; `SERVICES_SOURCES_GC_` prefix =2 / absent; AM-5 softened disclosure strings + manual-recovery line =2/=0 in `daemon.tools.scheduling`; **W7 None-guard**: module marshal-sha `f0668e9d…` vs live `7dd9023b…`, `update_source` +19 instructions, first divergence @236 `LOAD_FAST 'updated'` (the None-check), raise-shape tail verified; source hunk byte-verified at `8730bfb8a:daemon/routers/sources.py:447`; migration filename S1.4 cross-note. **Positive control** (`daemon.services.scheduling_service` present both, expected consts) → method non-vacuous |
| **S4** ceremony inputs | PASS | `current → releases/v0.29.1` unchanged; promote fields byte-stable (`current=v0.29.1 previous=v0.29.0 in_flight=None pending_op=None quarantined=['v0.28.5']`); livez `:9797` alive 0.29.1 (one disclosed read-only GET); scripts-pin `84df6b55c` ⊇ floor `2147c0298` (`merge-base --is-ancestor` rc=0) → **ARM-CLEAN**; releases/ census 8 dirs (nothing evicted — machinery-owned; live v0.29.1 + pending v0.29.2/v0.29.3 present); **v0.29.2 tag `f794ec572` peels → `b21bbb87f` which IS an ancestor of `8730bfb8a`** → one ceremony carries both lanes |
| **S5** residuals | see disclosures | nothing blocking |

## Disclosures
1. **`:9797` tension:** the never-contact-:9797 fence vs S4's livez-version requirement — resolved with exactly ONE read-only `GET /livez` (house fence-verification-probe precedent); nothing else touched the live daemon.
2. **Sidecar carrier shape:** this tree carries ONE sidecar file (binary); FE provenance rides the manifest per-file map (no top-level fe_index field — v0.27.8+ house pattern). Recorded as carrier-shape note, not a gap.
3. **Knob-name proof granularity:** the 5 knobs verified as `daemon.config` field names + `SERVICES_SOURCES_GC_` prefix (=2 staged / absent live) — full env literals don't exist as single consts because env names compose from `env_prefix="SERVICES_"` (house config pattern).
4. **W7 const-level non-discrimination:** the guard reuses pre-existing string consts; the one-way evidence is marshal-sha diff + instruction-count/flow + the byte-verified source hunk at the exact staged tree. Honest shape, disclosed.
5. **state.json carrier path:** the live journal lives at `releases/state.json` (not the install root) — the actual file was fenced.
6. Ambient env poison recurred in the worker shell (ensemble_prod / 9797 / live) — scrubbed per-variable everywhere; boot via `env -i` allowlist; zero prod contact; nothing written under `/home/nea/agents-ensemble` (read-only honored in full); staging worktree untouched (porcelain 0).

## Ceremony
- **Command:** `ENSEMBLE_UPGRADE_LIVE=1 VERSION=v0.29.3 bash scripts/upgrade/promote.sh live --f2-verified-closed` (env-first; CWD = scripts-pin worktree or stage worktree — CWD selects the executing script).
- Precondition: scripts-pin ARM-CLEAN (verified above). At promote: previous flips v0.29.1→v0.29.3; v0.29.2 content rides as ancestor; keep-3 candidate at ceremony time is machinery-owned.
- **Gate lineage for the record:** functional gate (NO-GO, G6 migration splitter defect) → 1-line fix + 3 pin families → scoped re-gate GO → staged verification PASS. F-A/F-B/F-C pin-strength findings remain ROUTED to the pin owner (non-blocking; see RESULTS/2026-11-08-source-gc-functional-gate.md re-gate section).
