# Staged Verification — v0.26.4 (running-zombie fix release)

**Commission:** final staged-artifact gate before the user's 3-factor nonce ceremony — the v0.26.0/61/62/63 pattern + the v0.25.4 final-gate pattern, on top of this tester's same-day independent functional gate (`RESULTS/2026-10-21-running-zombie-functional-gate.md`, PASS-WITH-PREEXISTING @ `9b2068369`, PG 23/23).
**Date:** 2026-10-21 (UTC, ~19:45–20:40)
**Staged artifact:** `/home/nea/agents-ensemble/releases/v0.26.4` (staged 2026-10-21T18:36:35Z by giter from the KEPT worktree `/home/nea/ensemble-src-wt-stage-v0264`)
**Build tree:** `6df7d899422488be442a805bac7ed35a9a51e61f` (== `origin/latest`; branch `fix/running-zombie-sweep` merged @ `cc576bd42` → merge `9b1777b8` → docs `fc75528e3` → bump `6df7d8994`)
**Binary sha256 (3-way):** `3db52510535877e51dc43a4aab556edeb8808db0265cd7c90b6fa7fe33899ba2` (disk == manifest == sidecar, independently recomputed)
**Mode:** read-only + sanctioned scratch only. 6 lanes (one skill each where applicable) + 1 digest-gap lane. **0 promotes, 0 flips, 0 restarts, 0 production-code changes, 0 pushes to `latest`.**

---

## VERDICT: 🟢 **STAGED-VERIFIED (PASS-WITH-NOTES)** — one process-pin adjudication routed to the ceremony owner

| # | Mission item | Verdict | Lane (instance) | Skill |
|---|---|---|---|---|
| 1 | Artifact integrity + tag/refs + L1 basis | ✅ PASS 35/35 | `49542d96` | `independent-staged-release-integrity-verification` (+2 consulted) |
| 2 | Daemon-only proof + payload shape + migration delta | ✅ PASS (within #1) | `49542d96` | — |
| 3 | Rollback posture (module-id old-vs-new) + ceremony fences | ✅ PASS **0/0** | `36585707` | `pyinstaller-co-consts-doc-surface-proof` (+2 consulted) |
| 4 | ★ Heal E2E from the STAGED BINARY | ✅ PASS (a)–(d) | `868f63ba` | `staged-binary-boot-smoke` |
| 5 | ★ Staged-binary negatives (d)/(e) | ✅ PASS / PASS | `e2b92c50` | `staged-binary-boot-smoke` |
| 6 | Isolated boot smoke | ✅ PASS (within #4/#5 — three boots total, all v0.26.4-clean) | `868f63ba`, `e2b92c50` | — |
| 7 | Seal / tripwire / audit / census | ⚠ **PASS-with-ONE-red** (see Finding V-1) | `57ef1f07` | `test-pack-execution` |
| 8 | Fences (all) | ✅ PASS — every lane, every boot | all | — |
| 9 | (gate-added) Build-tree digest equality | ✅ PASS byte-identical | `5f652b45` | none (DIY) |

**Ceremony path:** promote the staged artifact **AS-IS**. Everything artifact-side is green. The single note (V-1) is a SOURCE-tree process-pin violation that exists on `origin/latest` independent of any promote decision — adjudication below; a later re-pin commit does not invalidate the staged artifact (tests/-only delta, shipped payload trees unchanged — v0.26.1/63 tip-drift precedent).

**Live state (read-only, unchanged by this gate):** `:9797` = **v0.26.3** (MainPID 528942, NRestarts 0, epoch 2026-10-21T10:38:47Z), `current → releases/v0.26.3` (mtime 10:38:09Z) NOT flipped, `releases/state.json` sha `23986880…4ec6` unchanged (current v0.26.3 / previous v0.26.2 / in_flight null / pending_op null / history 115), all four release dirs byte-identical, `ensemble_prod` + `10.44.0.2` never contacted by any lane.

---

## 1 — Artifact integrity (L1, 35/35 PASS, 0 discrepancies)

- **sha 3-way** all-equal: disk == manifest `binary_sha256` == sidecar `artifact_sha256` == `3db525105358…99ba2`. Both provenance sidecars in the KEPT staging worktree: `git_head=6df7d899422488be442a805bac7ed35a9a51e61f` (full 40-hex), `git_dirty=false`; FE sidecar `artifact_sha256=a017dd21…`.
- **Tag**: `refs/tags/v0.26.4` annotated `402ba049f43e0b240ec628be79006eeb2007ea91` → peels to `6df7d8994`; `git ls-remote origin` carries **zero tags** (no v0.26* anywhere on origin). **L1 basis**: `refs/remotes/origin/latest == 6df7d8994…` == build tree (local `latest` pointer stale at `8e6df58e4` — informational).
- **Manifest scalars**: `version=v0.26.4`, `binary_version=0.26.4`, `rollback_safe=true`, `staged_at=2026-10-21T18:36:35Z` (manifest mtime matches to the second), `known_schema_gen=20261020_060000_create_board_tables.sql`; config `7bab75e9…` / launcher `a3a44461…` re-hash == manifest.
- **Daemon-only proof**: tree-hash algorithm **self-validated on live v0.26.3 first (3/3)**, then v0.26.4 → agents `2990c411…` / frontend `8d6afeaa…` / plugins `4715cf64…` all == v0.26.3; `diff -rq` per subtree → 0 differing entries; full per-file re-hash **339/339 + 92/92 + 4930/4930, 0 mismatch/missing/extra**, all 3 per-file maps IDENTICAL; `diff -rq` v0.26.3↔v0.26.4 → **exactly 2: `ensemble-prod` + `manifest.json`**; manifest scalar delta = exactly the 4 version fields; FE index on disk `a017dd21…` byte-identical. Git corroboration: `agents`/`frontend`/`plugins`/`scripts` tree objects IDENTICAL between the build trees; only `daemon/` differs (10 files — the fix).
- **Payload source-less**: no top-level `scripts/`/`daemon/`, 0 `*.sql`, 0 build sidecars, 0 symlinks/`.pyc`; the single `plugins/opendesign/copy_freely/design-templates/live-artifact/examples/stock-portfolio-live/provenance.json` is a vendored plugin resource, byte-identical to live.
- **Migration delta EMPTY**: `git diff efb8158ee..6df7d8994 -- daemon/migrations/versions/` → 0 entries (rename-detection-off control 0); 106==106 at git-tree and FS level, 106 distinct prefixes, `uniq -d` empty.
- **Zero-writes**: main porcelain 48→48 (foreign WIP, untouched), staging worktree porcelain 0→0, `find releases -newermt <t0>` → 0, tree-loss gate (`journalctl -t ensemble-treecheck --since "-10 min"`) → `-- No entries --`.

## 2 — Rollback posture + ceremony fences (L2, co_consts over 529 modules × 2 binaries)

- **Version anchors**: staged PYZ `0.26.4` / live PYZ `0.26.3` — each PYZ bound to its binary. Binary delta confined to **9 of 529** daemon modules (`daemon`, `daemon.api`, `daemon.config`, `daemon.constants`, `child_reports`, `job_queue_service`, `mission_live_guard`, `mission_strand_healer`, `work_notifier`); `repositories.instance.repository` UNCHANGED (the anchor fix reads the repo, doesn't alter it); control module `sources.adapters.scheduler` delta **0** on all three const namespaces.
- **Needles**: 10/10 commissioned NEW-only (`member_task_hold`, `member-live-task`, `running-zombie` ×27, `finalize_gap`, the kill-switch env, `members_with_undatable_live_work`, the loud-line format ×7, `transitioned=completed`, both probe-error phrasings, the anchor-fix reason strings); 5 legitimately pre-existing tokens individually adjudicated (`gate-block` 6/5 — the fix adds exactly the healer's one holder; 4 shared log-prefix phrases with pre-fix holders).
- **Rollback verdict (commissioned 0/0)**: embedded `.sql` migrations 106==106 with **identical aggregate set-sha** `f3bf5fe1…6e56`; SQLModel table surface 1452==1452; repository identifier surface 1273==1273; **0 DDL-shaped literals** among all 163 whole-tree only-new consts. Source cross-check: `daemon/migrations/ + daemon/models* + daemon/repositories/` diff = **0 files** (pathspec control proves the emptiness is real). → **new schema objects the OLD binary lacks: 0; schema state the OLD binary cannot handle: 0** (config keys are settings, not schema).
- **Fences quoted green**: symlink `releases/v0.26.3` @10:38:09Z; MainPID 528942 / NRestarts 0 / epoch 10:38:47Z / livez `0.26.3`; `state.json` sha `23986880…4ec6` before==after, current v0.26.3 / previous v0.26.2 / in_flight+pending_op null / history 115; all four release dirs file-count+mtime identical start==end; zero DB/remote contact (socket-level proof).

## 3 — ★ Heal E2E from the STAGED BINARY (L3 — the release-level proof the binary carries the fix)

Full isolation per the boot-smoke skill: own `systemd-run --user` transient unit `ensemble-v0264heal-6de7fc` (cgroup `/user.slice/…/app.slice/…` vs live `/system.slice/ensemble-main.service`, proven on both PIDs), scratch role+DB `rzv0264heal_2a9dbf9d`/`ensemble_v0264heal_2a9dbf9d` (owner-role, ladder step 1), port **19140** (both `PORT`+`DAEMON_PORT`), `.env` sourced INSIDE the unit, kill-switches `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` + `ENSEMBLE_ORPHAN_F1_ENABLED=0` (confirmed inside the daemon's own log), `ENSEMBLE_SELF_ENV=dev`, FAKE_HOME, `SERVICES_MISSION_STRAND_HEAL_INTERVAL_SECONDS=5` (daemon self-report: `MissionStrandHealer started: interval=5s (default 300s)`).

- **(a)** banner `Starting Ensemble v0.26.4` ×2 (both quoted); livez 200 `version 0.26.4`; readyz 200 (database/queue_freshness/services/checkpoint_saver all true); ERROR census **exactly 7, all benign `computer-use` MCP** (`libXtst.so.6` class); harness-signal scan EMPTY (no uvx/webfetch/LiveViews/SecretCipher/42804/startup-failed); `Skipping migrations for non-SQLite database` + `applied=[]`; 77 tables all scratch-role owned; `schema_migrations=0` (expected fresh PG).
- **(b)** seeded the Oct-21 incident fixture (root `running`, latest task terminal-COMPLETED aged 25h, 7 completed children, depth-3 `idle` member with NO task row, JobItem pre-`done`+`terminal_reason=mission_complete`, no message_queue rows, no question markers) → healed on the **first tick** (~3s):
  `mission_strand_healer|running-zombie: instance=rzv0264heal-root task=work-incident-root completed_age=90001 tree=11 transitioned=completed reason=finalize_gap` + `healed 1 running-zombie mission(s) of 1 scanned` — through the **production completion chain** (pre-stamp guard → DB stamp → emission gate → lifecycle publish → observer). Guard lines captured (`member-alive probe … no live task row ⇒ stale-eligible`; `members_with_undatable_live_work=0`). 20s of further sweeps: no second heal, no skip — candidate-set exit as designed.
- **(c)** DB truth: root `running → completed`, version 1→2, `last_activity_at`/`updated_at` advanced by the stamp; **JobItem byte-identical to seed** (idempotent; `Step 1 (JobItem UPDATE) skipped — no JobItem … (post-D13 MESSAGE path)`); epoch adjudication per design §1.2/§2.2 — `terminal_reason` is f2-owned on the JobItem and the instance's terminal write IS the epoch end (functional-gate D2 adjudication confirmed at release level).
- **(d)** residue: only the root row changed (11/1/1/0 rows across instances/task/job_queue_items/message_queue, all descendants seed-identical).
- **Teardown**: 3-marker ownership before any signal; SIGTERM-only; DB+role catalog 0/0; port freed; unit collected; scratch+FAKE_HOME removed (prefix-guarded); staged payload 0 modifications (binary sha byte-identical); live fence re-asserted.
- **Disclosure (harness, important for future gates): the healer's log lines land in the daemon's own `ensemble.log`/unit stdout — `journalctl --user -u <unit>` shows NOTHING. A journalctl-only verification would false-FAIL this lane.**

## 4 — ★ Staged-binary negatives (L4, two sequential boots)

- **(d) F1 member-live-task HOLD — PASS.** Port 19150, own role/DB. Fixture = healable shape EXCEPT a member owns a LIVE `processing` task row (age 0s, horizon 21600s). 75s of 5s ticks: root stayed `running`, heal lines 0, and the skip is **WARNING-level, one per tick** (15 lines, verbatim first/last): `SKIP class=member-live-task detail=non-terminal member … owns a LIVE task row younger than the 21600s staleness horizon (0s→73s old) — the tree is working, not stale; the backstop holds it (fail-miss over a false completion)`. **Bonus discriminator:** the coarse `has_live_work` probe is blind to `processing` (`_LIVE_TASK_STATUSES_FOR_ZOMBIE_SCAN` = pending/running/paused) yet the hold still fires — the decision rides the dateable `NON_TERMINAL_TASK_STATUSES` evidence. Stronger than the `pending` variant.
- **(e) kill-switch off ⇒ complete no-op — PASS.** Port 19151, own role/DB, `SERVICES_MISSION_STRAND_HEAL_RUNNING_ZOMBIE_ENABLED=false`, HEALABLE fixture (would heal under ON). Exactly **1 WARN** `SKIP class=kill-switch detail=disabled by config … [warned once per healer instance]` + 16 DEBUG repeat-ticks; root unchanged; heal lines 0; per-root SKIP lines 0; **`mission_live_guard` probes 0** — the switch short-circuits BEFORE candidate selection (a complete no-op, not a coincidental silence; boot-1's switch-ON liveness on the same code path is the control). Banner v0.26.4 ×2, livez/readyz 200 v0.26.4, census 7 benign (+1 late re-warm, same class).
- Both boots: full teardown proofs (catalog 0/0, ports freed, units not-found, payload 0 modifications, live fence re-asserted, final residue sweep zero). Two discarded harness-deviation runs (a glob-vs-ERE guard bug; a psql `BEGIN` without `COMMIT`) were aborted before judging, torn down with zero residue — no artifact signal drawn.

## 5 — Seal / tripwire / audit / census (L5, at the exact build tree via throwaway worktree `wt-v0264-l5` @ `6df7d8994`, removed after)

| Pack | Result |
|---|---|
| Seal repo `generate_prompt_artifact_baseline.py --check` | **PASS** rc 0 (161 artifacts) |
| Seal payload `sha256sum -c` (release dir, read-only) | **PASS 161 OK / 0 FAILED**; seal file byte-identical to live v0.26.3 (sha `04d46c7a…`) |
| Tripwire `tests/test_chart_image_delivery_e2e.py` | **23P / 1F** — the 1F is Finding **V-1** below |
| Chart audit `tests/test_chart_image_delivery_audit.py` | **PASS 46P/0F**; recount **35 pins / 34 green / 1 stale / 0 missing** (the 1 stale IS V-1 — nothing else drifted) |
| Census `tests/job_queue/test_terminal_write_census.py` | **PASS expected-shape**: exactly the 2 pre-existing reds, stale-14 set == the functional gate's reference as an exact multiset; other 2 nodes green |
| Census walker (module's own, `DAEMON_ROOT` asserted) | **PASS 39/32/7**; unlisted = `manager.py:6757` + 6 `prompt_version` sites (exact) |
| RZ pack at build tree | **PASS 77P/0F** (71→77 growth via the branch's fix cycles — collect-only proven, no test lost) |
| f2/gate at build tree | **PASS 9P/0F** |

## 6 — (gate-added) Build-tree digest equality (L6)

Whole `tests/job_queue/`, identical commands both legs, own environment: **FAILED-list sha256 `82557ce0b62d5f64ed5b78e7e267bbfae618ac70c55d13c42c1d7f27692e4043` (nodeids-only form) byte-identical base `33e1ef513` ↔ build tree `6df7d8994`** — and identical to the functional gate's digest (zero environment shift). Counts 2152→2233 collected (+81 = RZ 77 + f2 4, exactly), **15F/15F, net-new = ∅**; the 15 reds deterministic across 6 isolated re-runs. This closes the gap between the functional gate (@ `9b2068369`) and the staged build tree (@ `6df7d8994`, + the branch tail). *Form caveat recorded: raw `FAILED `-line digests of the same run differ (`fe9978b8…`) — future gate records must pin the digest form.*

---

## Findings

### V-1 (🟠 — the gate's one adjudication, routed to the ceremony owner)

**The chart-image seal tripwire is RED at the build tree: `daemon/constants.py` (a sealed Phase-B artifact) was modified additively by the running-zombie lane WITHOUT the seal re-pin ceremony.**
- Evidence: `TestPhaseDDoesNotModifySealedArtifacts` — baseline `df41a67f…` (== the file at v0.26.3 build tree `efb8158ee`) vs current `bd0545bd…`; deterministic 2×; recount scope = **only 1 of 35 pins stale** (nothing else drifted). Offending commits: `3539d45aa` (the member-task liveness leg — adds `NON_TERMINAL_TASK_STATUSES` frozenset + comments, +42 lines) + `bfc87b9ec` (docs). Diff purely additive; chart-image functionality untouched (audit 46/46; the other 23 tripwire nodes green).
- Classification: the guard worked as designed — this is a **process-pin violation on the source tree**, NOT payload corruption (the agents/ payload seal is 161/0 byte-identical to live; binary/manifest untouched).
- **Tester recommendation:** follow-up **seal re-pin commit on `latest`** (tests/docs-only: pin `bd0545bd…` + a Re-pinning-history entry citing `3539d45aa`/`bfc87b9ec` + the additive diff). It does NOT invalidate the staged artifact (shipped payload trees unchanged → promote AS-IS remains valid under the v0.26.1/63 tip-drift precedent), and it can land before or after the ceremony. Alternative: proceed with the ceremony and carry this as an explicit user-adjudicated disclosure. The guard has no acceptance path without the re-pin — leaving it red permanently grows base debt.

### Notes (🟢 / informational)

- **N-1** RZ pack count drift 71→77 between the functional gate (`9b2068369`) and the build tree — growth via the branch's own fix cycles, all 77 green, attribution exact (L5 + L6).
- **N-2** Healer log lines do NOT reach `systemd` journald for transient units — verify via the daemon's `ensemble.log`/unit stdout (L3; would false-FAIL a journalctl-only check).
- **N-3** ERROR census "exactly 7" is a **boot-window** figure; the benign `computer-use` pool re-warms ~60s-cyclically (8 by +75s, 10 by +3.5min). Census must be time-anchored (L3/L4).
- **N-4** Digest-form pin (nodeids-only vs raw `FAILED ` lines) — recorded in KB + the new `failed-digest-equality-gate` skill.
- **N-5** `env -u POSTGRES_*` remains a silent no-op — every lane here used the explicit 6-variable scrub with in-script `!= ensemble_prod` assertions (the house idiom fix from the functional gate is applied and proven at scale).

## Disclosed limits

1. Whole-project PG suite (`tests/postgres/`) and full non-integration sweep NOT run (out of staged-gate scope; the functional gate + council cycles own behavior; L6 owns the job_queue regression net at the build tree).
2. The heal E2E's message-publish subpath operates on a seeded fixture with no real checkpoint transcript — the chain's own soft-skip disclosures are quoted (title-generation "message not found", PP1 zero-watcher WARNING — synthetic-fixture artifacts, no LLM calls made).
3. Census/line-pin pre-existing debt (14 stale, 7 unlisted, tripwire red-at-base) — disclosed, not fixed, per commission.
4. `daemon/constants.py` V-1 adjudication pending (above) — the ONLY item standing between this report and an unqualified PASS.

## Fences held (all lanes)

`releases/` read-only (binary shas byte-identical before/after every lane; `find -newermt` → 0); the KEPT staging worktree untouched (porcelain 0, sidecars recoverable); no daemon ever shared the live cgroup (three boots, all `/user.slice/` transient units, `/proc/<pid>/cgroup`-proven; L2 independently OBSERVED L3's boot and re-ran the fence battery after); SIGTERM-only teardowns with 3-marker ownership before any signal; `ensemble_prod` never written and `10.44.0.2` never contacted (socket-level); zero commits/pushes by lanes; scratch worktrees/dirs created and proven removed; main checkout dirty state untouched (foreign WIP).

---
*Gate executed by the tester. 6 lanes + 1 gap-closing digest lane, 0 re-dispatches, 0 timeouts, 0 artifact-side defects. The user's 3-factor nonce ceremony owns the promote.*
