# v0.29.1 STAGED VERIFICATION — prompt_reload (final pre-ceremony gate)

- **Date:** 2026-11-07 (gate window ~06:0x–06:5x UTC)
- **Staged artifact:** `/home/nea/agents-ensemble/releases/v0.29.1` · binary `ensemble-prod` sha256 **`99c5c1442421fc115231e2a461eb82d628d7ed2c222e56d6bd6cd890f115532b`** (4-way: release dir == staging-worktree dist == manifest == sidecar)
- **Staged tree:** `24e098245f9afc481c1824d5974ebb6b6f812bcf` (staging worktree `/home/nea/ensemble-src-wt-merge-v0291`, detached, both sidecars intact: binary `99c5c144…` git_head=24e098245 dirty=false; FE `7a050d4e…` == v0.29.0's, FE untouched)
- **Tag:** `v0.29.1` = annotated object `2c06196b18be1a14b634953d7db479bdeb16cc1c` → peels to `24e098245…` — PUSHED; `origin/latest` == staged tree (post-fetch; **zero tip drift**)
- **Live at gate time:** v0.29.0 (`/home/nea/agents-ensemble/current -> releases/v0.29.0`, mtime 2026-11-06 11:30:23Z; livez 0.29.0 via ONE disclosed read-only GET; MainPID 2093550 epoch 2026-11-06 11:30:24Z NRestarts=0)
- **Workers:** integrity `24fd7dc6` · smoke `7734c37d` · evidence lane `2c4cf4e1`/commit — logs `/tmp/pr-sv-v0291/`, `/tmp/pr-v0291-verification/`

## VERDICT: **STAGED-VERIFIED — PASS-WITH-NOTES** (S1 PASS · S2 PASS · S3 PASS-WITH-NOTES · S4 PASS). Ceremony-ready.

---

## S1 — Artifact integrity — PASS (8/8)
1. Staging worktree @ `24e098245…` porcelain 0; BOTH sidecars present with git_head=24e098245/git_dirty=false.
2. **4-way binary sha** all == `99c5c144…f115532b` (release / worktree dist / manifest / sidecar).
3. Tag peels local AND origin (same object); `origin/latest` == staged tree — no tip drift.
4. Manifest scalar delta vs live v0.29.0 = **exactly 5** (version, binary_version, binary_sha256, staged_at, agents_tree_sha256 `044eef08…→a34b27e1…`); config/launcher/plugins/frontend tree fields untouched.
5. Per-file maps: agents 379==379 with **exactly 1 modified** — `agents/ari/meta.json` (`983018de…→7ca99c90…`, the prompt-reload allow entry); frontend 95==95 and plugins 4930==4930 zero changes.
6. **Migration census 122 == 122** (CArchiveReader TOC + per-entry extract + content sha; BOTH binaries): staged 122 entries / 122 distinct prefixes / 0 dups; live identical; set delta EMPTY both directions; head `20261105_102821` (memoir, unchanged).
7. Payload source-less: top level exactly `{agents, config.yaml, ensemble-prod, frontend, launcher.sh, manifest.json, plugins}`; no .env/provenance/.staging/.aside/daemon/tests. NOTE: 5 nested `scripts/` dirs under `plugins/opendesign/copy_freely/design-templates/*` — vendored od resources, byte-present identically in live v0.29.0 and v0.28.13 (pre-existing, benign).
8. **PROMPT-BASELINE 181** BOTH trees (staging worktree bare `--check`; release payload via `--tree`): `PROMPT-BASELINE: OK — 181` rc=0 ×2.

## S2 — Isolated FROZEN-binary boot smoke — PASS (a–g) — the load-bearing leg
Scratch release-mirror `/tmp/pr-sv-v0291/rel/` (binary COPY, never in place; mirror = binary's parent holding `agents/`, the live-install shape); `env -i` EXPLICIT ALLOWLIST (ambient live poison unreachable by construction); `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned; `ENSEMBLE_SELF_ENV=sandbox` (live-shaped → manifest-verification branch active); PRIVATE scratch PG16.15 cluster (TCP-only, free port; `ensemble_prod` absent from it; `current_database()==ensemble_smoke_v0291` asserted pre-write); scratch port 10097; mock LLM 127.0.0.1:16210 (chat census 0); dead-local embedding pin.
- **(a)** banner `Starting Ensemble v0.29.1` verbatim; livez 200 `version 0.29.1` (~19 s); readyz READY all-4-true, reasons=[], grace_active=false, leak_signal=false.
- **(b) HEADLINE — frozen registry proof:** frozen resolution (`Path(sys.executable).parent/"agents"`, registry.py:1536) **executed frozen end-to-end**: `GET /api/agents` → 44 agents, ALL 44 `agent_dir` under `/tmp/pr-sv-v0291/rel/agents/`, ZERO worktree/source paths; resolved `ari/meta.json` sha `7ca99c90…` == release payload byte-identical.
- **(c)** `prompt_reload` registration: PYZ one-way (staged 4691 modules present / live 4688 absent; Δ=+3 exactly the lane's modules); `_tool_registry` bytecode roster **20/20 incl. prompt-reload** (live 19/20); staged prompt_reload consts carry `prompt-reload` + `ENSEMBLE_PROMPT_RELOAD_ENABLED`; boot-resolved artifact (the MEI extraction THIS boot materialized) `vocabulary_version=7 count=61 prompt-reload=True`, sha `1539723d…` — **triple identity: MEI-resolved == CArchive-embedded == source-committed**; zero ari tool-config validation warnings (36-entry allow clean). Honest boundary disclosed: no driven tool CALL and no in-memory introspection — registration proven at frozen-payload + boot-artifact + boot-validation level (the E2E tool-call behavior was proven LIVE in the functional gate's harness legs).
- **(d)** resolved `tools.allow` == **36** incl. `prompt-reload` (sha-identical to payload).
- **(e)** tool-categories v7 / 61 / prompt-reload at all three layers.
- **(f)** SIGTERM-only teardown rc=143 (~4 s); port re-bind-verified FREE; `DROP DATABASE ensemble_smoke_v0291` + census-absent proof; scratch cluster stopped; ps census zero leaks.
- **(g)** live fence: `/home/nea/agents-ensemble/releases/state.json` sha256 `a1460853621c035c88e69d2374fbd3030bc9adb6ece27009c4d5a15709f013bf` **PRE==MID==POST** (whole-file sha covers promote fields AND journal — no journal write); `current` unchanged; zero writes under the live install; **network census: loopback-only** (scratch daemon :10097, mock :16210, scratch PG :16645, dead embedding pin 127.0.0.1:1 — no :9797, no external, no DNS).

## S3 — One-way markers — PASS-WITH-NOTES
- Module symbols: `daemon.tools.prompt_reload` + `daemon.services.prompt_refresh` + `daemon.services.release_manifest` PRESENT staged / ABSENT live (TOC method; substring `prompt_reload` 1-vs-0).
- **VOCABULARY_VERSION 7 vs 6** via bytecode `dis` of `daemon.checkpoint_metrics` (staged :249 `LOAD_SMALL_INT 7` / live :244 `6`). Method note (KB-recorded): CPython 3.14 small ints are instruction operands, NOT co_consts — const-pool walks miss them; `dis` is the reliable probe.
- Marker literals (`[PROMPT-RELOADED`, `prompt_reload: instance=`): raw whole-binary scan compression-blocked (disclosed); proven at code-object consts level inside staged `prompt_refresh` (live has no such module) — non-vacuous per the v0.29.0 method-adjusted precedent.

## S4 — Ceremony inputs — PASS (6/6)
- `current -> releases/v0.29.0` UNTOUCHED (mtime predates stage). PATH NOTE: the symlink lives at `/home/nea/agents-ensemble/current` (install root), not `releases/current`.
- state.json: `current=v0.29.0 previous=v0.28.16 in_flight=None pending_op=None quarantined=['v0.28.5' pre-existing]`; whole-file sha `a1460853…` (matches S2's fence).
- Scripts-pin: `/home/nea/ensemble-src-wt-scripts-pin` @ `84df6b55c…` porcelain 0; `merge-base --is-ancestor 2147c0298 HEAD` rc=0 → **arm-time guard PASSES**.
- Retention: 6 dirs (v0.28.13/15/16/18, v0.29.0/1); promote-time keep-3 candidate **v0.28.13** (machinery-owned); protected live v0.29.0 + target v0.29.1.
- Promote command (NOT executed): `ENSEMBLE_UPGRADE_LIVE=1 VERSION=v0.29.1 bash scripts/upgrade/promote.sh live --f2-verified-closed`
- Live version confirmed 0.29.0 (one disclosed read-only GET; ~18.7 h uptime).

## Disclosures (all non-blocking)
1. **Official run 2:** S2's run 1 hit two WORKER-side probe bugs (banner exact-equality vs timestamp-prefixed line; `ls /tmp/_MEI*` first-match picked the LIVE v0.29.0 daemon's extraction dir → false C1 FAIL). Both fixed in the probe; run 1 fully torn down; run 2 official, all PASS. KB-recorded MEI-selection gotcha: pick the MEI dir whose artifact sha == the staged binary's embedded sha.
2. **Recipe availability:** the v0.29.0 RESULTS recipe file is not on disk nor in any git ref (its evidence rode the retag lane). Methods taken from KV composition records + the v0.28.9 RESULTS + the registered G3 boot-smoke pack (same ceremony-verified lineage). Routed: ceremony-critical recipes must ride a durable ref.
3. **Env form upgraded:** `env -i` allowlist (dispatch fence) over the G3 pack's subtract form — strictly stronger; DB via private scratch PG cluster over the house-superuser method — strictly stronger isolation.
4. **:9797 discipline:** S2 performed ZERO :9797 contact (its live-fence via filesystem + read-only systemctl metadata); the single optional livez GET was the integrity worker's.
5. **Pre-existing, base-proven, NOT this lane:** boot WARNING `maintenancer: deny entry 'git_commit' is neither a known category nor a known tool` (meta byte-identical across v0.29.0/v0.29.1); MCP init-guard journal degraded into the scratch HOME path (expected under the HOME fence; live journal untouched).
6. Probe needle note: exact-match consts `prompt_reload`/`prompt_reload_disabled` live in f-strings/docstrings — the category string + kill-switch env name carry the payload proof.
7. S1#7 nested plugin `scripts/` dirs = vendored od templates, identical in live payloads.

## Ceremony summary
- Target **v0.29.1** supersedes staged-unpromoted v0.28.18 (ancestor content rides); migration delta EMPTY vs live → rollback-safe additive (fix-loss-only).
- Preconditions: scripts-pin ARM-CLEAN; tip-identity zero-drift; retention candidate v0.28.13 machinery-owned.
- Command: `ENSEMBLE_UPGRADE_LIVE=1 VERSION=v0.29.1 bash scripts/upgrade/promote.sh live --f2-verified-closed`

## Evidence
`/tmp/pr-sv-v0291/` (run/boot.log 390 L, run/agents.json, run/fence.{pre,mid,post}, smoke2.sh, bin-check/probe.py, smoke2-output.log) · `/tmp/pr-v0291-verification/` (integrity logs + reimplemented census) · this file + PACKS.md block on branch `tester/v0291-staged-verification` @ `24e098245`.
