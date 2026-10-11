# Dev-Env E2E — designer→sketcher→critic pipeline — FIRST LIVE RUN — v0.19.0

**Date:** 2026-10-11 (UTC) · **Run by:** Tester lane (leader) + 3 workers
**Code under test:** `/home/nea/ensemble-src` working tree, branch `latest` @ `5cb964187` (v0.19.0 release vehicle), bare-script boot (non-frozen)
**Prior coverage replaced:** definition-level pytest + single od.generate streaming smoke — this is the first live end-to-end pipeline run.
**Workers:** `dp-preflight` b9f8a4ff (recon, no skill) · `dp-e2e-boot-drive` c27cf734 (boot fence + drive start; died ×2 on its OWN LLM lane rate limit — sandbox unaffected) · `dp-e2e-recovery` 7c6d088c (high-tier replacement; state recovery, evidence backfill, teardown)
**Consolidated evidence:** `/tmp/ens-sbx-des-ske-cri-20261011/evidence/RECOVERY-REPORT-20261011.md` + transcripts ×4 + `$SBX/daemon.log` + `$SBX/boot.sh` + `$SBX/leak/` (this file cites; nothing committed — giter handles commits)

---

## VERDICT: ✅ PIPELINE WORKS CORRECTLY — end-to-end live run PASSED with 4 flaws (1🟠 operational, 1🟠 containment, 2🟡 design-seam) — FIT FOR demo/live progression

All four hand-offs correct (designer→sketcher, sketcher→critic, critic→designer, designer→save). Every ratified invariant held LIVE: designer ZERO od.\* calls, critic read-only write-leak set, ≤3 rounds severity-gated, KV round_count pinned + revival re-bind. The named promote-time smoke item (KV round_count revival re-bind) is **already evidenced** by this run.

---

## Sandbox & boot (Step 2 evidence)

| Item | Evidence |
|---|---|
| HEAD | `5cb964187c129…` on `latest` (preflight-verified; merge "release/v0.19.0 — version bump v0.19.0") |
| Daemon | `127.0.0.1:8377`, uvicorn bare-script, banner `07:57:47Z Starting Ensemble v0.19.0`, `/livez`+`/readyz` 200 (4/4 components) |
| Plugin boot-scan | `Plugin registry boot-scan: 1 plugin(s) (opendesign), 2 plugin-skill(s)` — N=1 ✅ (bare-script boot requirement met) |
| Registry | 39 agents incl. designer (team `[worker, sketcher, critic]`), sketcher, critic |
| DB | **disposable local PG16 cluster** `127.0.0.1:15442` (`initdb -A trust`, superuser=`ensemble_dev_app`), db `ensemble_sandbox`, 56 tables. **Provisioning deviation (documented):** `ensemble_dev_app` lacks CREATEDB on the shared dev cluster 5432 (`rolcreatedb=f`) → both provisioning paths denied → leader ruling: disposable-cluster fallback (on-host precedents 2026-09-09 W1 lane 15432, snapshots-v2 lane 15532). Shared 5432 **never modified** (preflight SELECTs only). |
| Env-poison fence | scrub-first (ambient `POSTGRES_*`/`ENSEMBLE_SELF_ENV`/`QUEUE_DISCARD_ON_STARTUP` confirmed present in worker shell → scrubbed) → `set -a source .env` → RE-PIN triple (`POSTGRES_PORT=15442 POSTGRES_DB=ensemble_sandbox`) → `ENSEMBLE_SELF_ENV=sandbox` → `DATA_DIR=$SBX/data` → bind 127.0.0.1:8377, no `--reload`. Reproducible launcher: `$SBX/boot.sh` (no secrets inside — sourced at runtime). |
| DB ×2 proof | pre-boot `current_database()`=ensemble_sandbox; post-boot `pg_stat_activity` → 8 daemon connections, ALL `datname=ensemble_sandbox` |
| Boot wall | launch 07:57:38Z → banner +9s → readyz green 07:58:45Z (uptime 60s) |
| Migrations | fresh-PG runner-skip by design (column-ensure path); 56 tables materialized |

**Boot recipe lineage:** today's od-generate-async-poll RESULTS (freshest LLM-lane expectations) + snapshots-v2 daemon-lane boot (`frontend/scripts/boot-e2e-snapshots-daemon.sh` fence shape) + lib.sh:411-456 sandbox conventions (forced DB name) + dev.sh refusal-guard analysis (dev.sh NOT used — hard-codes 8079/SQLite) + uv-PATH nit (`/home/nea/.local/bin/uv`; PG bins `/usr/lib/postgresql/16/bin`).

## The run (Steps 3–4)

**Brief (exact, 143 chars, posted 08:03:20.635Z):** "Design a simple landing-page hero section mockup: headline, subheadline, one CTA button, minimal top nav, light theme. Deliver one HTML mockup."

**Timeline (UTC):** 08:02:59 designer `f5e1a81f` created → 08:03:20 brief POSTed → **~08:05 first-era daemon died (unexplained; see F1)** → 08:07:29 daemon relaunched (PID 3094195) → designer **resumed from checkpoint, completed the entire pipeline unattended** → 08:28:40 tree complete. **Brief→terminal = 21m56s including the ~2.5-min outage.** Incidental bonus: a live proof of crash-recovery on the durable core (two observer-worker deaths + one daemon death → zero pipeline impact).

### Stage 1 — designer (orchestrator)
- Vision routing: log `model=vision` (08:07:42/43). ✅
- **ZERO od.\* tool calls** — structured extraction over all 24 transcript messages; 4 textual `od.` hits all benign (1 instruction prose, 1 quoted report, 2 regex false-positives on `.env.prod.demo/.example`). ✅ (D1 kill switch holds live)
- Brief authored → sketcher spawn 08:11:06, dispatch envelope 3611 chars "MISSION: Generate ONE self-contained HTML mockup…". ✅
- **KV round_count: `0` @08:10:26 → `1` @08:11:05** (key `round_count`, context_key = designer tree-root; init-at-zero then bump-at-dispatch). ✅

### Stage 2 — sketcher (generation lane)
- Sequence: `od.compose_brief` (6ms) → `od.generate` (**66.673s**) → `od.lint` (10ms, pass) → `od.save` (7ms). All four od.\* tool-internal. ✅
- Envelope: `finish_reason=stop, truncated=false, error=null, attempts=1, marker_pass=true` — completeness gates green, zero failover events, zero 429s, zero ERROR lines. ✅
- Inline HTML return: 6920 chars; **DOCTYPE at offset 885** (model plan-prefix precedes the HTML — F3). ✅
- Artifact write-through: `od.save` → 5144 B, `sha256 3f26d392…a8744` — disk-verified byte-identical post-run and again post-teardown-move. ✅
- tmp_images: table not provisioned in sandbox (disk write-through channel; no image ops this run) — OBSERVED N/A.
- **Scope note:** >120s generation survival NOT stressed this run (66.7s on a small brief); that leg is covered by today's async-poll gate1 evidence (130.7s single stream, RESULTS/2026-10-11-od-generate-async-poll-gate1-realsmoke.md).

### Stage 3 — critic (QA gate)
- Actual tool calls: `read_file` ×3 — **⊆ resolved read-only set**; NO image_save/write_file/edit_file/bash anywhere in transcript. **Write-leak invariant HELD live.** ✅
- Verdict: **PASS** — "0 critical, 1 advisory, 1 brief-level, 1 nit" + AC1–5 with WCAG contrast math. ✅
- **pinned_spec_sha:** critic field `null` **pre-freeze (by design)**; the effective pin `2e601588…` lives designer-side and materializes at accept/freeze — recovery worker **independently reproduced** it (git-blob SHA-1 of design-spec.md minus status/pin lines). Mechanism sound; lifecycle semantics need doc reconciliation (F2).

### Stage 4 — designer decision
- Critic PASS (0 critical) → **accept + spec freeze**: `page_status: accepted + spec frozen`, `phase: approved` @08:24:31. ✅ Natural severity-gating (0 critical → accept; no iteration).
- Rounds: **1 of ≤3**; round_count never advanced past 1. ✅
- Artifacts on disk (originally landed in MAIN CHECKOUT — F4; moved to `$SBX/leak/landing-hero/` at teardown, shas re-verified): `design/design-spec.md` 11310 B `sha256 f3cc5f6c…b430`; `design/mockups/landing-hero.html` 5144 B `sha256 3f26d392…a8744`. ✅
- Designer terminal 08:25:16.456; final report cites artifacts + view_url.

### Probe — KV revival re-bind (named promote-time smoke item)
Designer revived 08:28 with one status-check message → restated "round count: 1 of ≤3 … KV `round_count: 1` confirmed live" after re-reading KV + re-hashing the artifact → **RE-BIND, not reset. PASS.** ✅

---

## Workflow evaluation (Step 5)

**Correctness:** every hand-off correct; severity-gating exercised on its happy path (0 critical → accept); round-count mechanics sound (init 0 → bump at dispatch → stable across critic hand-off → re-bind on revival). The KV revival proof closes the last runtime-deferred item from the 2026-10-10 final gate (Battery 6b).

**Clunky / fragile / improvement list:**
- **F1 🟠 Unexplained first-era daemon death (~08:05) + log loss.** Cause undetermined — the relaunch truncates `daemon.log` (`>` redirect in boot.sh), so the first-era log survives only as `death_evidence.txt` excerpts. Pipeline self-recovered via checkpoint resume (strong), but an unexplained daemon death on a 25-min-old boot warrants understanding before promote. *Fix: append-mode or timestamped log files on relaunch; pull system logs (OOM?) for the 08:05 window before the ceremony.*
- **F2 🟡 pinned_spec_sha lifecycle ambiguity.** Critic-verdict-schema declares the field mandatory, but critic emits `null` pre-freeze and the pin lives designer-side until accept/freeze. Works and is reproducible — reconcile the schema/rule docs with the freeze-time semantics (one-paragraph fix).
- **F3 🟡 od.generate plan-prefix leakage.** The `html` field carries model plan-prefix/trailing prose (DOCTYPE@885); the sketcher prompt compensates by cleaning before lint/save. *Fix: strip or envelope-flag the prefix in the od.generate port so downstream consumers can't be bitten.*
- **F4 🟠 Sandbox containment: agent file-writes unfenced.** The designer wrote planning artifacts into the MAIN CHECKOUT (`/home/nea/ensemble-src/.agents/shared/planning/landing-hero/`) — cwd-relative writes, DATA_DIR fence does not cover agent filesystem tools. Moved out at teardown (2 files, gate-checked mtimes 08:15:17/08:24:48 inside run window, inventory `$SBX/evidence/landing-hero-leak.txt`). *Fix: sandbox boot should fence agent write roots to ENSEMBLE_DATA_DIR (or a sandbox project root) — matters for any disposable-env scenario.*
- **F5 🟢 Observability gaps:** usage tokens all zero (native lane not instrumented); `/api/instances` omits `agent_id` + returns `model=null`; daemon `[LLM] Tool call:` lines record only the first tool of a parallel block (transcript JSON is authoritative).
- **F6 🟢 KV transient:** the `round_count=0` state is only visible by polling between init and dispatch — fine, but worth a doc line.
- **F7 🟢 Ergonomics:** critic spawn filter advertises 264→22 tools vs. the resolved 5-tool set — cosmetic overstatement; the meaningful check is actual calls (which passed).

**Worker-lane note (incidental):** two observer-worker deaths (openai rate limit on the workers' own model lane) had ZERO effect on the pipeline — an unplanned but clean proof of the "execution runs on the durable core" thesis. Rate-limit hygiene (batched bash poll-loops instead of many LLM turns) recorded in LESSONS.

## Fit-for-release call (feeds the promote ceremony decision)

**GO for demo/live progression.** The pipeline mechanics are proven end-to-end live on v0.19.0: correct hand-offs, all ratified invariants held, KV revival re-bind (the promote-time smoke item) already evidenced. Conditions/recommendations before or at promote:
1. Understand or bound F1 (daemon death) — it self-recovered, is not pipeline-caused, and plausibly host-level, but an unexplained death on a fresh boot should not ride a promote silently.
2. F4 is a demo-environment risk only if demos boot from a checkout cwd — note it; fence later.
3. F2/F3/F5 are cheap doc/port polish items — schedule, don't block.

## Teardown state (Step 6)

- Daemon: SIGTERM 3094195 (identity-verified: pid+port+cmd+cwd) → exited in 1s, `Graceful shutdown complete`; **8377 FREE**.
- PG: `pg_ctl stop -m fast` → **15442 FREE**; pgdata retained (71 MB, holds the only copy of `ensemble_sandbox` — disposable by construction, deleted whenever /tmp sweeps or by hand).
- $SBX `/tmp/ens-sbx-des-ske-cri-20261011` retained (75 MB): `evidence/` 17 files (4 transcripts, RECOVERY-REPORT, leak inventory), `leak/landing-hero/`, `daemon.log` 3.6 MB, `boot.sh`, `pgdata/`.
- Protected lanes untouched throughout: 8079 (dev), 7979 (demo), 9797 (live), 8088 (self-system); shared 5432 never modified. Parallel forensic lane (e8a61da3) never contacted.
- Main checkout: this mission's net footprint **zero** (leak written during run → moved out at teardown). Porcelain delta vs preflight baseline = +1 untracked dir `.agents/shared/planning/watch-loss-fix-options/` (mtime 08:32:01, **parallel watch-loss lane's work — not ours**; flagged so giter isn't surprised).
- **Zero commits** (leader handles via giter, per mission).

## Lane bookkeeping
- No test pack created (single-run E2E commission; PACKS.md unchanged).
- LESSONS: `LESSONS/2026-10-11-sandbox-daemon-e2e-fence-and-worker-ratelimit.md` (provisioning fallback + fence recipe + rate-limit hygiene + PID-vs-port liveness).
