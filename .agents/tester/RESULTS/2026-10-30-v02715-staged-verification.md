# STAGED VERIFICATION — v0.27.15 (promote-preflight-hang lane, board wi-fc1786a2) — FINAL GATE

**Artifact:** `/home/nea/agents-ensemble/releases/v0.27.15` · binary sha256 `58a9a4cd39c2d0dd4f598fdc5c975dcfd54190d26206065ff27541a4dbd11706` · staging worktree `/home/nea/ensemble-src-wt-preflight-hang` @ `2f0c613a5f10dbe37aa79e215608dfe9b4d07cbe` (bump commit; sidecars kept in `dist/` + `frontend/dist/`) · merge `953603108` · floor-pin `5b80a8825` · tag `v0.27.15` = annotated `5c2f6c252` → `2f0c613a5` PUSHED · origin/latest == `2f0c613a5`.
**Preceded by:** functional gate PASS-WITH-NOTES @ `bb0cda5fd` (`.agents/tester/RESULTS/2026-10-30-preflight-hang-functional-gate.md`; REQUIRED G3 1-line fold — folded by the lane as part of merge content per M=953603108 lineage; merged-tree Q2 gates: gate.sh 12/12, 13 packs 0-failed, daemon corpus 519P/1S/0F, G3 chunk 32/0).
**Date:** 2026-10-30 · 3 fenced workers (S1 `75700331`, S2 `809f946e`, S3 `6a61fa78`) + tester aggregation. Live contact: READ-ONLY fence probes only.

## VERDICT: **PASS-WITH-NOTES** — the staged artifact is CEREMONY-READY; one environment PRECONDITION is UNMET and is leader/user-owned (S4 checklist #1)

---

## S1 — ARTIFACT INTEGRITY: **PASS**

1. **Binary sha 4-way** — release dir (81,171,760 B) == staging-worktree dist == manifest `binary_sha256` == sidecar `artifact_sha256`, all `58a9a4cd…d11706`.
2. **Sidecars** — binary: `git_head 2f0c613a5…` (full), `git_dirty false`, build_at 2026-10-30T15:53:42Z. FE: same head/dirty; `artifact_sha256 5531cbe4…` == manifest FE hash == actual `frontend/dist/frontend/browser/index.html` sha (3-way).
3. **Tag/origin** — `cat-file -p v0.27.15`: annotated `5c2f6c2520ff30d11d3269dba77b1e50ad364ba4` → `2f0c613a5…`; `ls-remote origin`: tag PRESENT + `refs/heads/latest → 2f0c613a5…`.
4. **Payload source-less** — layout exactly `{agents/, config.yaml, ensemble-prod, frontend/, launcher.sh, manifest.json, plugins/}`; 0 scripts/, 0 .env*, 0 provenance sidecars in payload, 0 `.staging.*`, 0 root dotfiles.
5. **Manifest census** — counts 338 / 93 / 4930; per-tree disk==map zero set-difference both directions; **70-file re-hash sample (largest per tree incl. 3.4 MB js + 2.0 MB png): 0 mismatches, 0 missing**.
6. **Prompt-artifact baseline** — house checker (`scripts/protection/generate_prompt_artifact_baseline.py --check`): `PROMPT-BASELINE: OK — 161 versioned artifact(s) match` rc=0.
7. **Migration census** — embedded .sql via CArchive TOC: **115, 115 distinct prefixes, 0 duplicates** (strings cross-validated); lane-authored delta EMPTY (`git diff 953603108^1 953603108 -- daemon/migrations/versions/` empty; whole range b1183a637→2f0c613a5 empty); **delta vs LIVE v0.27.12 binary: 115==115, ADDED=0 REMOVED=0 — the additive file list is EMPTY** (cleaner than the brief's expectation; `known_schema_gen 20261026_060000…` present in both). Rollback posture trivial: no schema delta vs live at all.

## S2 — ISOLATED BOOT SMOKE: **PASS** (+ S5 fence evidence)

- Arena technique (v0.27.13 recipe, recovered from git object `5693bb4a6`): binary+config copied to `/tmp/pfs2715-boot/releases/v0.27.15/`; sha 3-way on the copy; FAKE_HOME; PORT **18971** (verified free; 9797 untouched); ENSEMBLE_CONFIG→scratch; scrub-then-set scratch env; transient unit cgroup.
- **Isolation proof:** `/proc/<pid>/cgroup` = `…/app.slice/pfs2715-smoke.service` (≠ live `/system.slice/ensemble-main.service`); boot.log verbatim `journal absent at /tmp/pfs2715-boot/releases/state.json` — path derivation resolved INTO the arena; the live journal was never bound.
- **Assertions:** banner (logged twice) `daemon.api - INFO - Starting Ensemble v0.27.15`; livez 200 `alive version 0.27.15`; readyz 200 `status=ready` ALL components true (`database, queue_freshness, services, checkpoint_saver`), `grace_active:false`, `leak_signal.detected:false`; MCP context7 **ok** (warmed 1/1), webfetch **degraded-advisory** (init_failed in fenced FAKE_HOME env; readiness NOT flipped — same class as the v0.27.13 precedent). Boot clean on scratch PG16.15 (`Skipping migrations for non-SQLite database…` — zero errors; 83 tables; attribution `ensemble-manager ×9 + ensemble-checkpoint-adapter ×1`).
- **Teardown:** SIGTERM-only (`systemctl --user stop` → `Result=success`; `Graceful shutdown complete` / `Application shutdown complete.`); port freed; cgroup gone; scratch DB + role DROPPED (residual 0|0); cluster stopped; all scratch removed.

## S3 — LANE-SPECIFIC STAGED ASSERTIONS: **PASS** (one premise correction, disclosed)

- **(a) Floor constants:** source `daemon/tools/upgrade_tools.py:3916` `PIPELINE_SCRIPTS_FLOOR_COMMIT = "953603108fa35d4f59031818c3091eecda8c1663"`; worktree `lib.sh:95` `PIPELINE_SCRIPTS_REV` = same value; git-proven == the merge commit (parents `70a13fd5c + 1ee191f06`). **In-binary:** PYZ raw-extract (dual-reader 3.13 + uv-3.14.7, extracted-sha match) — the 40-hex value ×1 + the `floor-unset: …` literal in `daemon_tools_upgrade_tools.pyc`.
- **(b) Guard symbols in binary:** `stale-scripts-dir` ×1 · `arm-scripts-provenance` ×3 (token + two row-failure literals) · `scripts_dir_provenance_verdict` ×1 · `PIPELINE_SCRIPTS_FLOOR_COMMIT` ×2 — all in `daemon_tools_upgrade_tools.pyc` (full-archive sweep places each in exactly this module).
- **(c) Behavioral leg — PASS via sanctioned FALLBACK (worktree-tree arm-path drive, no scratch-instance boot), with a strong binary bridge:** module-identity proven at code-object level — per-code-object signature multisets (co_code sha + names + varnames + str consts) of EXTRACTED binary blobs vs fresh compiles of worktree source on the build interpreter: `upgrade_tools 158/158`, `upgrade_journal 194/194`, `upgrade_journal_sweep 84/84` **IDENTICAL**. Drive: real arm path, real mint (`nonce CONFIRM-5PE2-QJCQ`, run_id `r-20261030-161646-3e43`), scratch scripts repo with orphan HEAD `6aa4ce9f…` (floor-not-ancestor, rc=1 pre-checked). Verbatim refusal: `UPGRADE REFUSED — reason=stale-scripts-dir: arm preflight: stale-scripts: HEAD 6aa4ce9f… does NOT contain the floor commit 953603108… as an ancestor … — nonce NOT burned (the arm simply did not happen; nothing consumed)`. Nonce `consumed_at=None` BEFORE == AFTER; `no-pending-op-armed` PASS; `lock-released` PASS; journal-always row `{"event":"arm-scripts-provenance","detail":"verdict=refuse stale-scripts: …"}` present. 11/11 harness checks. Incidental negative control: a failing queue-repo seam tripped `nonce-verification-unavailable` FAIL-CLOSED with nonce intact (factor-3 hardening observed live).
- **(d) Shipped read surface — PASS on substance; PREMISE CORRECTED:** `journal_read.py`/`journal_splice.py` are **NOT compiled into the binary** (no spec entry, 0 importers, 0 CArchive entries, 0 PYZ matches; full 5,571-blob literal sweep zero hits) — **consistent with house design** (payloads ship no `scripts/`; the arm-time guard exists precisely to validate the EXTERNAL pinned scripts dir — see S4 #1). R4 substance: worktree `journal_splice.py` byte-identical to base `b1183a637` (`cmp` rc=0, git blob `9b373314f…` both sides). Canonical sha256 **correction**: `50c406a4769025661bb76c006869d274f39967edb0a182342380798d0a8eb84b` (the functional-gate dispatches carried a one-digit transcription `…6867d…`; the 16-char prefix and all byte-level proofs were always correct). Two-file sink IS embedded: `executor_run_log_paths` ×2 (`daemon.tools.upgrade_journal` + `daemon.services.upgrade_journal_sweep`), `_append_pointer_line` ×1.

## S4 — CEREMONY-READINESS CHECKLIST (user-owned items, verified facts)

1. **🔴 PRECONDITION — UNMET (the headline note):** live `.env:107` pins `ENSEMBLE_UPGRADE_SCRIPTS_DIR=/home/nea/ensemble-src/scripts/upgrade`; the main checkout HEAD is `0138952876…` (v0.27.8-era), `merge-base --is-ancestor 953603108 HEAD` **rc=1** (object present — not object-missing), `journal_read.py` **absent** from the pinned tree on disk, porcelain clean. ⇒ Every live arm would REFUSE pre-nonce **BY DESIGN** (the guard working as intended — verified behaviorally in S3(c)). **Resolution before ceremony (leader/user):** advance `/home/nea/ensemble-src` to ≥ `953603108` (ff to `2f0c613a5` satisfies tip-identity too) OR move the `ENSEMBLE_UPGRADE_SCRIPTS_DIR` pin to a floor-descendant tree.
2. **Ceremony queue:** v0.27.13 → v0.27.14 → **v0.27.15 (ours; supersedes content-wise)**; next free slot v0.27.16.
3. **Promote command:** `ENSEMBLE_UPGRADE_LIVE=1 VERSION=v0.27.15 bash scripts/upgrade/promote.sh live --f2-verified-closed` — executing from a tree that satisfies #1.
4. **Retention:** `releases/` holds **8 version dirs** (v0.27.4/.7/.8/.11/.12/.13/.14/.15) vs keep-3; eviction is promote-machinery-owned (known GNU no-op defect on this host); protected present: live v0.27.12 + candidates .13/.14/.15. Housekeeping residue (untouched, disclosed): 5 `state.json.*.bak`, `rollback.lock.d.stale.2480459`.

## S5 — FENCE AUDIT: **CONFIRMED — NOTHING live moved**

- `state.json` sha256 **PRE=MID=POST byte-identical**: `47fa92ead2d917ac580313af361fe5244b22a6821a3b6360d30c40722ea92f76` (16:00:59Z → 16:07:06Z → 16:07:58Z).
- `current` → `releases/v0.27.12` unchanged at every checkpoint; releases/ listing byte-identical PRE==POST.
- Live daemon: livez `0.27.12` uptime **monotonic** 17192.4 → 17550.9 → 17603.5 s; MainPID 1181746 + epoch 11:14:09Z + NRestarts=2 unchanged (pre-existing state, recorded as-is); port 9797 bound only by the live daemon's own pid — never touched by this gate.
- All other work: mktemp scratch + FAKE_HOME + disposable cgroup; per-variable env scrub on every invocation (the `env -u POSTGRES_*` no-op idiom used nowhere); zero writes to `/home/nea/agents-ensemble`, `/home/nea/ensemble-src`, and the staging worktree (porcelain empty pre/post, HEAD unmoved); zero commits until this evidence commit.

## Notes (the "WITH-NOTES")

1. 🔴 S4#1 precondition UNMET — leader/user-owned resolution required before any live arm; the artifact itself is ceremony-ready.
2. S3(d) premise correction: scripts are not in the binary (by design); guard validates the external pinned dir — makes note 1 load-bearing, not cosmetic.
3. webfetch MCP degraded-advisory at scratch boot (init_failed under fenced FAKE_HOME; readiness not flipped; v0.27.13 precedent class) — environment-shaped, not an artifact defect.
4. R4 sha transcription correction (canonical value above; all byte-level proofs unaffected).
5. Scratch-PG socket-dir hiccup (first start attempt; fixed via `-k /tmp/...`; disclosed) and the v0.27.13 recipe file recovered from git object `5693bb4a6` (not on disk in the worktree at 2f0c613a5 — expected: it landed post-stage on the audit ref, exactly where this report is headed).
