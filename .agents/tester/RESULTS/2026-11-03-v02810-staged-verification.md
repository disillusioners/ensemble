# v0.28.10 — Staged Verification (lane wi-76d89e11, skill-list-full-uuid)

- **Date**: 2026-11-03 (verification window ~22:50–23:30Z)
- **Staged artifact**: `/home/nea/agents-ensemble/releases/v0.28.10` — binary sha256 `50d8610c1233f05cb896bb7ed6ac8970b059c946e64702171c29dbc713ab5342`
- **Lineage** (re-verified both hops): merge `8c8e79d8e` (parents `c4161335b` + `9247210c8`) → changelog `e0d6e388f` → bump `a287775f3` (exactly 4 files, 1 line each). Tag `v0.28.10` = annotated `283542969d…` → `a287775f31f015552e73f24b6c41aad466be5e1c`, PUSHED. `origin/latest == a287775f3 == staged tree == stage-worktree HEAD` — zero tip drift.
- **Live at verification**: v0.28.9 (current → releases/v0.28.9; MainPID 3175816, epoch 15:43:16Z, NRestarts=0).
- **Workers**: W-A `5a43b8be` (S1+S4) · W-B `d1a3f6e6` (S2) · W-C `d06e6276` (S3+S5) · branch setup `53d30e0f`.
- **VERDICT: STAGED-VERIFIED — PASS-WITH-NOTES** (all 5 legs PASS; notes are ceremony-text corrections and process disclosures, none is an artifact defect).

## S1 — Artifact integrity: 7/7 PASS

| # | Item | Measured |
|---|---|---|
| 1 | 4-way sha | release-dir binary == stage-worktree dist binary (**cmp identical**) == manifest `binary_sha256` == sidecar `artifact_sha256` — all `50d8610c…ab5342`; sidecar `git_head=a287775f3`, `git_dirty=false`, build_at 22:41:54Z |
| 2 | Tag peel | local tag object `283542969d…` → peeled `a287775f3…`; `ls-remote` confirms PUSHED annotated; `origin/latest == a287775f3` (zero drift) |
| 3 | Migration census | **119** .sql files (disk AND git @ a287775f3); **119** distinct full prefixes (`uniq -d` empty); `git diff 7fcf1bd71..a287775f3 -- daemon/migrations/versions/` → **0 files**; **embedded-binary census 119, EXACT set equality with source** (house CArchive-TOC method, PyInstaller 6.19.0 — raw `grep -a` is blind on PyInstaller) |
| 4 | Payload source-less | root = 7 entries (agents/ frontend/ plugins/ config.yaml ensemble-prod launcher.sh manifest.json); 0 `.env*`/`.staging*`/`.git`/node_modules; the 5 `scripts/` + 2 `*provenance*` hits are opendesign **vendored plugin payload** (git-tracked, byte-identical to live) — disclosed, not stage machinery |
| 5 | Manifest deltas vs live v0.28.9 | CHANGED = **exactly** {`binary_sha256`, `binary_version`, `staged_at`, `version`}; `launcher_sha256` `c4f640e11c31a505e2e63d6c66d7728dc2bb79f7cd2856ba9077a0a8236daf2a` and `config_sha256` `0f0875bdca2ef83497b3b07181cbfde917b37235c05601e79fa746d2548eb768` UNCHANGED both sides; payload maps **338/95/4930 deep-identical** (tree hashes `3ad0ea53`/`ba93f3d4`/`4715cf64` equal); **5,363/5,363 entries re-hashed from disk on BOTH releases — 0 mismatch** |
| 6 | Prompt baseline | `PROMPT-BASELINE: OK — 161 versioned artifact(s) match .prompt-artifacts.sha256`, exit 0 |
| 7 | Stage worktree | porcelain EMPTY @ a287775f3 (detached); both provenance sidecars inside gitignored build dirs; FE sidecar `7461aa8d…` byte-matches the released index.html; **no daemon/ source modification** |

## S2 — Isolated boot smoke: PASS

Full critical-note fence set, no exception relaxed:
- Binary COPY to scratch (2-way sha verified; never run in place). **Ambient live poison OBSERVED and OVERRIDDEN**: `POSTGRES_DB=ensemble_prod`, `POSTGRES_HOST=10.44.0.2`, `PORT=9797`, `HOST=0.0.0.0`, `ENSEMBLE_SELF_ENV=live`, `ENSEMBLE_UPGRADE_LIVE=1`, `RAG_IS_REQUIRED=true`, live-bundle `_PYI_*` temp vars — all scrubbed per-variable then explicitly set (29-var scrub list recorded verbatim in artifacts).
- `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` **pinned explicitly** (2026-10-31 kill-live incident class); `ENSEMBLE_ORPHAN_F1_ENABLED=0`; `ENSEMBLE_SELF_ENV=sandbox`; `ENSEMBLE_CONFIG`+`ENSEMBLE_DATA_DIR` explicit; throwaway config.yaml + ensemble.json with hard-coded scratch port 18097 and DSN (zero `${PORT}` interpolations left).
- **Banner** (twin-logger, known): `Starting Ensemble v0.28.10` ×2. **/livez** 200 `{"status":"alive",…,"version":"0.28.10"}`. **/readyz** 200 — `status:"ready"`, ALL components true, **`reasons: []`**, `grace_active:false`, `leak_signal:false`, MCP context7/webfetch/plane `ok`, `skill_sources.enabled:true` (full body captured).
- Schema landed in the throwaway DB: **87 tables, all owned by `ensemble_dev_app`** (privilege-independent census pre-DROP).
- **Isolation: PRIMARY form** — `systemd-run --user --scope` unit `ensemble-smoke-v02810-231019`; `/proc/<pid>/cgroup` proofs for BOTH processes inside the user scope (not `ensemble-main.service`); scope `Result=success` on exit.
- **state.json fence**: live `/home/nea/agents-ensemble/releases/state.json` sha256 `e7c5db34fbd00faa8ea940cc5e9a9f23dbd75276fa4502db9b4f06891310dd98` **PRE == MID == POST** (mtime 15:45:15.644Z unchanged); the scratch root bound **no journal** (`install_dir=<none — dev/unresolved>`, no `/releases`), so no live nonce was prunable.
- **Live fence**: :9797 GET-only ×4 — uptime monotone 26826.6 → 26832.4 → 26891.8 → 27033.9s, version 0.28.9 every probe, MainPID 3175816 unchanged, NRestarts=0. Independent PG-server-log proof: 0 ensemble_prod sessions; local PG contains no ensemble_prod at all (live prod is remote 10.44.0.2) — a local smoke physically cannot reach prod.
- **Teardown**: SIGTERM only → graceful shutdown logs → exit 143 in ~3s; pids gone; orphans EMPTY; port 18097 freed; throwaway DB **dropped with proof** (catalog 0); scratch GONE; staged payload untouched (`find -newermt` → 0); stage worktree porcelain still empty.

## S3 — Staged-content one-way proof: PASS (12/12 one-way + 3 controls green)

**Method correction (note N1)**: the `strings -a | grep -c` recipe FAILS its own positive control on this host (PYZ is zlib-compressed — `strings` returns 0 for `skill_view` itself). Replaced with the house chain: CArchiveReader → ZlibArchiveReader.extract → code objects → recursive `co_consts`/`co_names`, run with the build venv (CPython 3.14.7 + PyInstaller 6.19.0). Controls: VERSION anchor (daemon pool `0.28.10` staged / `0.28.9` live); NEGATIVE module pools byte-identical; POSITIVE `skill_view` 12/12 both sides; TOC/PYZ entries **4671/4671 equal** (no module-count delta claim available this release).

One-way discriminators (staged `50d8610c…` vs live `40d20a50…`, both == their manifest binary_sha256):
- **skill_tools.py**: OLD docstring `"is the first 8 chars of the skill's UUID4"` **live 1 / staged 0** (the required old-render discriminator); NEW docstring `"the skill's full UUID4 primary key"` staged 1 / live 0; OLD bullet render `- [<short_id>]` live 1 / staged 0; NEW `- [<id>]` staged 1 / live 0; OLD create echo `created with id <short_id>` live 1 / staged 0; NEW staged 1 / live 0; payload key `"pseudo_id"` EXACT staged 1 / live 0 (module-scoped — the one-way form; whole-binary is a +2 pool diff because service/repository/manifest construct pseudo-ids pre-existing).
- **project_skill_source/model.py**: `to_dict` docstring + body phrase + `d["id"] = self.address` co_name — each staged 1 / live 0; controls `to_summary`/`ADDRESS_PREFIX`/`"pf-"` present BOTH.
- **skill_search_service.py / skill_injection_service.py**: `"project-file"` EXACT in search-svc **live 1 / staged 0** (definition removed) and in skill-tools **live 2 / staged 0** (3 source sites → 2 compiled constants via PEP 709 inlining + compiler constant de-dup — honest attribution); model.py `"project-file"` BOTH (the constant moved there — single definition verified at source: exactly ONE definition site `model.py:74` staged vs `skill_search_service.py:109` live, three consumers import); `_is_project_file_skill` co_name in inject-svc staged 1 / live 0 (control: search-svc pre-existing BOTH); new/old inject docstrings each one-way; `ADDRESS_PREFIX` co_name in inject-svc staged 1 / live 0.

## S4 — Ceremony inputs: 5/5 PASS

- **Version slot**: releases/ = exactly {v0.28.7, v0.28.8, v0.28.9, v0.28.10} = 4 vs keep-3; no v0.28.11+ dir; highest local AND remote tag = v0.28.10.
- **Ceremony command (RECORDED, NEVER RUN)**: `ENSEMBLE_UPGRADE_LIVE=1 VERSION=v0.28.10 bash scripts/upgrade/promote.sh live --f2-verified-closed` — CWD `/home/nea/ensemble-src-wt-scripts-pin` (@ 84df6b55c) **or** `/home/nea/ensemble-src-wt-stage-v02810`; NEVER the stale main checkout. `promote.sh` present + identical at both permitted CWDs.
- **Scripts-pin arm-readiness**: pin HEAD `84df6b55c8cdb2cbe515a63c75cd10de5343640f` (porcelain clean); `PIPELINE_SCRIPTS_FLOOR_COMMIT` (upgrade_tools.py:3923) == `PIPELINE_SCRIPTS_REV` (lib.sh:95) == `2147c0298a09a5d7c5a0caeea04cdc51ed1ebcaa`; `merge-base --is-ancestor 2147c0298 84df6b55c` → 0; floor is ALSO an ancestor of the staged tree → **arm-time guard returns ARM**.
- **Journal**: current v0.28.9 / previous v0.28.8 / in_flight null / pending_op null / **quarantined ["v0.28.5"]** (target v0.28.10 clean); cooldown_until past (expired); rollback counter 2/3 window lapsed — stale-and-inert.
- **Rollback + retention**: migration delta EMPTY at 3 levels (git diff, disk census, embedded census 119==119) → `rollback_safe=true source=explicit` (delta-empty-legitimate); auto-rollback safe (live binary references no new symbols); consequence = **FIX-LOSS not incompatibility**. Retention 4 vs keep-3; promote-time eviction candidate v0.28.7 — machinery-owned, nothing evicted by this verification.

## S5 — Ceremony disclosure set: 6/6 ACCURATE (2 labelling notes)

1. **Residual non-consumable emissions — ACCURATE**: `:1260` skill_fix echo + `:1491` skill_feedback echo print a bare 8-hex token `skill_view` refuses (DB paths only — `:1213-1214` refuses `pf:` BEFORE the echo; `:1465-1468` routes `pf:`/`pf-` away before `:1491`); `_render_pf_view` `:402` renders the pseudo-id directly beneath the address (`:401`). `pf:te` sweep: 5 withdrawal hits (decisions.md:61, plan-overview.md:182, tidier notes.md:790, gate RESULTS ×2); all other hits are legit address forms; `daemon/**` 0 hits. **The `pf:te…` hazard stays WITHDRAWN.**
2. **REST shares NOTHING — ACCURATE**: `git diff --name-only c4161335b..a287775f3 -- daemon/routers/ daemon/api.py` → EMPTY; basis quoted in-tree (architecture-ruling.md:59; decisions.md:84 — full uuid via `to_dict()` at routers/skills.py, REST search DB-only v1).
3. **Per-source id contract — ACCURATE (cite R1/R7, not "N12" — note N2)**: `decisions.md:6` R1 (db → full uuid4; project-file → `pf:<ns>:<name>`), `decisions.md:12` R7 + architecture-ruling.md:184-187 Correction 2 (file skills have NO uuid; address is the only verbatim-consumable form).
4. **Routed follow-ups — ALL 5 present**: skill_fix `pf-` gap `:1213` (skill-evolution/fix-dispatch lane; gate-F8 correction from `:1211`); `to_summary` dead surface (display-hygiene); P7-family siblings at base `:1201-1206` (move-together receipt); echo hygiene `:1260`/`:1491` (display-hygiene); REST/FE fusion parked v2.
5. **Routing semantics unchanged — ACCURATE**: skill_view exact-PK chain (`skill_tools.py:971` → `skill_store_service.py:436` → `repository.py:241` `session.get(Skill, skill_id)`); repositories/** + skill_store_service diff EMPTY; skill_feedback dual-form `startswith(ADDRESS_PREFIX) or startswith(PSEUDO_ID_PREFIX)` intact.
6. **Gate evidence in the staged tree — ACCURATE**: gate RESULTS (96 lines) present at a287775f3 with §Addendum `--kill-after` ruling (:90) + SIGTERM no-RESULT nit (:79); PACKS.md gate block + ADDENDUM invocation ruling + registered row all present.

## Notes carried into the ceremony text

- **N1 (method)**: cite the PYZ/`co_consts` chain for one-way proofs — `strings` fails its own positive control on PyInstaller binaries on this host and would have produced false "absent everywhere" evidence.
- **N2 (labels)**: cite **R1/R7** for the per-source contract (the label "N12" does not exist in this lane's tree); do not claim a PYZ module-count delta (4671/4671 equal).
- **N3 (precision)**: `to_dict()` is reached via the generic `_json_default` fallback (`:84-95`, used at `:649-650`/`:672`) as well as the explicit `_pf_skill_payload` builder — both seams lane-covered; do not imply `to_dict()` is the only live `skill_list` path.
- **N4 (S2 process)**: two aborted harness attempts archived (credential-probe bug; PostgreSQL identifier lowercase-folding — the uppercase `T/Z` DB name folded, caught via `current_database()` assertion, abandoned DB dropped); run 1 used compensating isolation (missing `XDG_RUNTIME_DIR`) and was superseded by run 2 under real cgroup isolation — both boots clean; one script assertion misfired on a port-owner race (the `/proc` cgroup reads are authoritative). Benign boot warnings enumerated (MetricsListener disabled — :9798 live-owned; Plane workspace-refused ⇒ no live-Plane contact; f1 warning self-induced by the kill-switch; 7× worker-stop warnings at shutdown; fresh-schema first-boot PG notices expected).
- **N5 (doc drift, no action)**: architecture-ruling.md:59/:204 cite `:1211` (pre-correction); staged source is `:1213`; the plan already carries the correction.
- **N6 (foreign WIP observation, lane-owner visibility)**: the shared checkout `/home/nea/ensemble-src` (still @ 013895287) carries ~20 uncommitted modifications including skill-lane files — pre-existing, untouched by this verification.

## Artifacts

- W-A: `/tmp/staged-ver-v02810-s1s4/` · W-B: `/tmp/v02810-s2-artifacts-run2-20261103T231017Z/` (incl. `prior-run-compensating-isolation/`) · W-C: `/tmp/s3s5-v02810/`
- Worker snapshot `6cd91f4f` (release-gate); skill feedback 4× recorded; 2 `skill_fix` recorded-not-dispatched (evolution lane disabled).

## Gaps

None — S1–S5 complete with measured evidence. PACKS.md untouched (no pack measurement moved).
