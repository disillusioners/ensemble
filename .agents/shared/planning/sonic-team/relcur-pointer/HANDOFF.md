# HANDOFF — relcur-pointer — release-pointer invariant (live repair + durable enforcement)

- **Task**: `relcur-pointer` — fix the divergent, dangling `releases/current` symlink at the LIVE install root, then enforce the pointer invariant durably.
- **Emperor authorization (verbatim, binding)**: *"Yeah, fix that release current."* (Emperor via Ari, 2026-11-23)
- **Branch / release tip**: `sonic/relcur-pointer` — **release tip `83ce0da99dcbb348e85869567bf7ec9978653b76`** (this is what `v0.31.46` points at: annotated tag `5993e5ce5` → `83ce0da99`; it was pushed to `origin/latest` at push time, and a **docs-only** HANDOFF commit lands on top AFTER the stage — the staged artifact stays bound to the tag, never to the moving branch tip)
- **Staged release**: `v0.31.46` — binary sha256 `8509ed12af66048cf246cebb41e4fadfc70af12314c43f3428164d474a8d8107` (4-way: release dir == build dist == provenance sidecar `artifact_sha256`; manifest `version=v0.31.46 binary_version=0.31.46`). **NO live promote performed** (the ceremony owns the flip).
- **Integration owner**: sonic-developer (planning/integration layer). Coders: sonic-coder ×4 (M1–M4), 3 revision cycles + 1 merge-conflict bounce.

## Files touched across modules

**M1 `relcur-m1-pointer-lifecycle`** (`2e36aaa70` → r2 `9d8167551` → merge-resolve `aaa930622`)
- `scripts/upgrade/lib.sh`
- `scripts/upgrade/promote.sh`
- `scripts/upgrade/pointer-alias-repair.sh` (new)

**M2 `relcur-m2-boot-check`** (`98044a57d` → r2 `00e751f53`)
- `daemon/services/release_pointer_check.py` (new)
- `daemon/__main__.py`
- `daemon/api.py`

**M3 `relcur-m3-release-info`** (`2ba63c97e`)
- `daemon/tools/upgrade_tools.py`
- `tests/unit/tools/test_upgrade_tools.py`
- `tests/mocks/upgrade_tools_live_safety_mock.py`

**M4 `relcur-m4-pins`** (`3620b4b9d` → r2 `c9f8cb341` → r3 `6ef0b57fd` → conflict-merge `79e2ddcfb`)
- `tests/packs/relcur_flip_moves_alias_test.sh` (new)
- `tests/packs/relcur_eviction_refuses_pointer_target_test.sh` (new)
- `tests/packs/relcur_repair_idempotent_test.sh` (new)
- `tests/unit/services/test_release_pointer_check.py` (new)
- `.agents/tester/PACKS.md`

**Integration owner (sonic-developer)** — docs + mechanical release mechanics only
- `.agents/shared/planning/sonic-team/relcur-pointer/HANDOFF.md` (this file)
- `.agents/shared/planning/sonic-team/relcur-pointer/repair-receipt-live-run.txt`
- `.agents/shared/planning/sonic-team/relcur-pointer/repair-relcur.sh`
- `.agents/shared/planning/sonic-team/relcur-pointer/COORDINATION-TO-retention-lane-60d008ef.md`
- `pyproject.toml` + `daemon/__init__.py` — mechanical version bump to `0.31.46` via `scripts/bump_version.py` (release mechanics; no source logic authored by the integration owner)

## What changed

**Root cause (file:line) — the commission's working hypothesis was PARTLY wrong, and the correction matters:**
1. **The flip NEVER wrote `releases/current`.** The atomic flip writes `$INSTALL_DIR/current` (install ROOT) only — `scripts/upgrade/lib.sh` flip site (`ln -sfn "releases/$ver" "$INSTALL_DIR/current.new.$$"` + `mv -f`, i.e. rename(2)), with the contract documented at `scripts/upgrade/promote.sh:17`. A repo-wide grep for `ln -sfn` + `git log -S` over the upgrade scripts proves **no in-tree writer for `releases/current` has ever existed**. So the answer to "did the flip ever maintain it?" is: **no — it never did.**
2. **The pointer was written OUT-OF-TREE.** It was created ~Nov 18 by a nea-owned actor and re-pointed **once** on **Nov 19 17:10:28Z** to `v0.31.10` — relative target (⇒ the writer's cwd was inside `releases/`), owner `nea:nea`, **zero journal events**, **zero pipeline-log records**. Not the Layer-3 snapshot service (`/usr/local/sbin/ensemble-install-snapshot.sh` only *writes* snapshots; its restore is a manual root runbook, and the journal/tree show no discontinuity). Not `stage.sh` (no pointer writes in it). Not the promote lineage.
3. **It dangled because eviction never consulted the pointer's TARGET.** Pre-fix `retention_evict()` pinned journal `current`/`previous` and skipped a directory literally *named* `current` ("the flip symlink, not a release") — but when `v0.31.10` was evicted (post-`v0.31.42`-commit sweep, Nov 23 ~10:24Z), the stale pointer was left aimed at a deleted directory.
4. **`release_info` mislabeled its own source.** `_current_symlink()` (`daemon/tools/upgrade_tools.py:881`) reads `install_dir/current` — the INSTALL-ROOT symlink — while the two display sites printed `(via releases/current → …)`. The "current=v0.31.37" the commission observed was the **pre-flip install-root target** (probe window 03:10Z–10:22Z Nov 23, before the `v0.31.42` flip at 10:22:12Z), **not** a `releases/current` read and **not** journal-derived. The label was the only thing lying.

**Live repair (scope 2) — receipt (Ari's ask #4):**
- **Old**: target `v0.31.10`, owner `nea:nea`, mtime `2026-11-19 17:10:28.031188982 +0000`, inode `5037304`.
- **New**: target `v0.31.42`, owner `nea:nea`, mtime `2026-11-23 12:08:44.239857592 +0000`, inode `4982607`.
- **Preconditions proven BEFORE the flip**: journal `current=v0.31.42`; `releases/v0.31.42` is a directory; `/livez` version `0.31.42` == journal current.
- **Fences proven**: `releases/state.json` sha256 unchanged (`0e3a006f6024…`); install-root `current` unchanged (`releases/v0.31.42`); **nothing deleted**; no daemon restart; no `promote.sh`; no state.json promote-field writes.
- **Idempotency proof**: run 2 = `IDEMPOTENT-NOOP`, inode unchanged (`4982607`).
- **Held through the crash**: host kernel-panic ~16:15–16:39Z + reboot; the repaired pointer survived (leader-verified read-only).

**Durable enforcement (scope 3, staged — not live):**
- **(a) Flip seam (M1)**: the flip now mirrors the pointer onto `$INSTALL_DIR/releases/current` (same rename(2) discipline, create-on-first-flip). Flip-time asserts keep only what holds at flip time (top == alias == flipped `$ver`); the `alias == journal current` equality is asserted at the **COMMIT site** (`pointer_alias_assert_commit`, wired at `promote.sh:1220` after `journal_set_current "$VERSION"` and `promote.sh:1472` after `journal_set_current "$PREV"`), LOUD (`_warn` + journal `warn` row) but never fatal, naming `scripts/upgrade/pointer-alias-repair.sh` as the remedy. *(An earlier revision asserted the journal equality at the flip site — guaranteed-false during the ceremony window; the integration owner rejected it and it was moved.)*
- **(b) Boot check (M2)**: cheap, loud, **flag-not-block**. See the loudness proof below.
- **(c) Eviction seam (M1 + the absorbed retention rewrite)**: after the pre-push absorb of `origin/latest`, the sibling retention lane's journal-aware `retention_evict` rewrite became the owning structure; M1's pin was replaced by an **alias-divergence refusal** expressed inside it: with the install-root pointer healthy and the alias dangling or != journal current ⇒ refuse ALL evictions (journaled row + `_warn`), mirroring the lane's primary-pointer divergence shape. **Eviction-safety pins transferred to lane 60d008ef** (Ari's ruling).
- **(d) Repair CLI (M1)**: `scripts/upgrade/pointer-alias-repair.sh` — idempotent operator tool implementing the proven live algorithm (preconditions → receipt → atomic replace → verify → no-op when already correct; named refusal reasons; writes no journal rows; deletes nothing).
- **(e) Honesty fix (M3)**: `release_info` now labels the real source (install-root `current`) and reports the alias explicitly (target / resolves-or-DANGLING / matches-journal-or-not), with D-FA5.3-family wording.

**Boot-check loudness PROOF (Ari's ask #3):**
- Exact lines emitted (`daemon/services/release_pointer_check.py`):
  - drift → `logger.warning("release-pointer-drift: journal_current=%s top_target=%s alias_target=%s reasons=%s", …)` (greppable token `DRIFT_TOKEN = "release-pointer-drift"`)
  - clean → `logger.info("[release-pointer-check] ok: journal_current=%s top=%s alias=%s", …)`
  - nothing-to-check (fresh install, journal `current` null) → `logger.info("[release-pointer-check] nothing-to-check: %s", …)`
  - promote ceremony window → `ceremony-window: release-pointer ceremony window — …` at INFO, **no** drift token (r2: `in_flight.target`/`pending_op.target`-aware, so a promote in flight is not reported as drift)
- **Where it lands**: the daemon's own logger stream — the live daemon's stdout/journal under `ensemble-main.service` (NOT the upgrade journal; the check writes nothing), plus the cached result exposed via `last_boot_check()`.
- **The pin that fails when pointer/journal disagree**: `tests/unit/services/test_release_pointer_check.py` (10 nodes: matched / mismatched / dangling / absent-alias-with-journal-current / top-mismatch / journal-null / no-install-dir / `run_boot_check` token / ceremony-window-INFO / third-version-mismatch). Mutation leg: gutting the comparison ⇒ RED (mutation-proven).
- **Live enforcement begins at the NEXT promote ceremony.** The live daemon is still `v0.31.42` and does NOT carry this code; the staged `v0.31.46` carries it. Until that ceremony promotes `v0.31.46`, the invariant is enforced by the ceremony/journal only.

**Crash accounting (Ari's ask #1) — what survived on disk vs what was re-done:**
- **Survived the crash**: all 6 module commits through `c9f8cb341`; the live repair (pointer + receipt); the coordination note; M4's in-flight r3 WIP (3 files, uncommitted — recovered and committed by M4 after the reboot); the release worktree and its `.venv`.
- **Re-done after the crash**: the pre-push absorb of the new `origin/latest` (`51dc98bb7` → later `a52b63dcea`) with a PACKS.md conflict bounce; the final gate re-run; the version bump re-verified (`0.31.46`); the stage pipeline re-run (the first post-crash stage attempt hit the UI-less FE gate — the stage worktree had no frontend build; fixed with the sanctioned `npm ci` + `scripts/upgrade/_build_frontend.sh` wrapper, sidecar `head=83ce0da99 dirty=false`); the stale-artifact adjudication (below).
- **Not redone**: no module work was re-implemented; the M2 r2 and M4 r3 revisions were already committed before the crash.

**Slot discipline (W9, derived live at stage time)**: `v0.31.43` + `v0.31.44` (selectable-models wave) and `v0.31.45` (retention-lane stopgap) were taken by other lanes; the tree carried `0.31.44`, so two mechanical bumps were required — the intermediate `0.31.45` is the **recorded slot collision** (same pattern the selectable-models lane recorded in `69dd0c7c6`), and the shipped slot is **`v0.31.46`** (tag `v0.31.46` was free on origin and absent from `releases/`; never re-cut).

**Stale-stage adjudication (disclosed)**: `stage.sh live` first refused **rc=78 `non-tip-tree`** — correct guard behaviour: `origin/latest` advanced to `a52b63dcea` *after* my tag was placed. Verified delta my tag lacked: `.agents/tester/PACKS.md` (+9 rows), one tester RESULTS doc, `test/packs/sonic_selectable_models_guard.sh` — **zero daemon/scripts/frontend/agents/tests delta**, and my tag is an **ancestor** of `origin/latest`. The house's journaled stale-case override (`--allow-stale-stage`) was therefore used; the staged payload is exactly tag `v0.31.46` = `83ce0da99`.

## What is UNTESTED

- **Live promote ceremony end-to-end** — no ceremony was run (by design; the Emperor ceremony owns the flip). The flip-mirror, commit-site assert, auto-rollback assert and the alias-divergence refusal are proven only against fixtures.
- **Live enforcement of the boot check** — the live daemon (`v0.31.42`) does not carry it; it activates only when `v0.31.46` is promoted.
- **`launcher.sh`'s self-contained `_js_flip_current` twin** does **not** mirror the alias (outside M1's file fence): the boot-time sweep-rollback path can still leave the alias stale. Detected loudly by M2's boot check and repairable via the repair CLI — but not fixed here.
- **`tests/mocks/upgrade_tools_live_safety_mock.py`** was edited but never executed (standalone script, not pytest-collected).
- **The alias-divergence refusal's journal-row shape under a real ceremony** — fixture-proven only.
- **Pre-existing reds disclosed by modules (NOT introduced here, verified identical at base)**: `tests/test_release_journal.sh` times out at 900 s in BOTH my tree and the base tree (same 7 FAILs at base + 3 extra at base); `tests/test_rollback_target.sh` C6 fails identically at base `a1130e257`; `tests/packs/preflight_census_test.sh` HEAD-total assertion is RED at base (37 sites vs pinned 26); 24 failures in 5 files of `tests/unit/tools/` that M3 does not touch (proven by a `git archive` base-extraction re-run with `daemon.__file__` asserted into the base tree — identical 24-failure list, 0 grep hits for `upgrade_tools`/`release_info`).
- **No full in-process daemon boot** with the new boot-check wiring executing (M2 shipped code + scoped functional script over 16 scenarios; M4's pytest pins the check function).

## Known risks

- **Alarm-fatigue class, closed twice, but structurally possible**: the commit-site assert fires on `alias != journal current`; if a future change writes journal `current` before mirroring the alias, every commit emits a loud warn + journal row. Likewise the auto-rollback site asserts against `$PREV` — the launcher-side sweep-rollback (which does not mirror the alias) is the most likely source of a future spurious warn.
- **Boot-check is loud, not blocking** — a divergent pointer does NOT stop the daemon (deliberate: the daemon resolves through the install-root pointer; blocking boot would convert metadata drift into an outage and violate the ≤120 s boot SLO). Enforcement teeth are the ceremony assert + the repair CLI, not the boot.
- **Staged payload lags `origin/latest` by 3 tester-only commits** (disclosed above) — no functional delta; the promote ceremony will move live to exactly `83ce0da99`.
- **Eviction-safety pins are owned by lane 60d008ef** (transfer per Ari); the `relcur_eviction_refuses_pointer_target_test` pack remains on disk as **provisional pointer-seam coverage** and its registry row is annotated as transferred.
- **Two coder stalls** (M1, M4 — work complete, turn wedged pre-commit) were recovered by wake-injection, not by re-implementation; each stalled once and was recovered once. Root cause of the stall is not established.
- **Stage-time frontend build is required** for a clean stage worktree (`npm ci` + `scripts/upgrade/_build_frontend.sh`, sidecar head-binding) — an operational prerequisite, not a code defect; the first stage attempt refused rc=78 (UI-less) until satisfied.
- **Version-slot churn**: three slots (43/44/45) were consumed by sibling lanes during this commission; the intermediate `0.31.45` bump commit exists on the branch as the recorded collision and is superseded by `0.31.46`.
- **`ENSEMBLE_SELF_ENV` marker**: staged as `live` in `/home/nea/agents-ensemble/.env` (never inside the release dir) by the stage — operator-visible live-tree write by the sanctioned stage path.

## How to verify

On the merged tree (`git checkout v0.31.46`; or worktree `/home/nea/ensemble-src-wt-relcur`):

```bash
# 1. Flip moves the pointer; healthy flip is silent; commit-site detects drift
bash tests/packs/relcur_flip_moves_alias_test.sh                    # 15P/0F (mutation-proven)
# 2. Eviction seam (pointer-strand refusal) — PROVISIONAL, transferred to 60d008ef
bash tests/packs/relcur_eviction_refuses_pointer_target_test.sh     # 14P/0F (mutation-proven)
# 3. Repair CLI is idempotent (run2 no-op, inode unchanged)
bash tests/packs/relcur_repair_idempotent_test.sh                   # 9P/0F
# 4. Boot check detects mismatch loudly (mutation leg ⇒ RED)
cd $WT && env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST \
  -u POSTGRES_PORT -u DATABASE_URL ENSEMBLE_SELF_ENV=dev \
  .venv/bin/python -m pytest tests/unit/services/test_release_pointer_check.py \
  tests/unit/tools/test_upgrade_tools.py -q --tb=short -p no:randomly --timeout=120
# 5. release_info is honest about BOTH pointers
.venv/bin/python -m pytest tests/unit/tools/test_upgrade_tools.py -q   # 241P
# 6. shell syntax + live pointer state (read-only)
bash -n scripts/upgrade/lib.sh && bash -n scripts/upgrade/promote.sh && bash -n scripts/upgrade/pointer-alias-repair.sh
readlink /home/nea/agents-ensemble/current && readlink /home/nea/agents-ensemble/releases/current
# 7. staged artifact identity (4-way)
sha256sum /home/nea/agents-ensemble/releases/v0.31.46/ensemble-prod   # 8509ed12af66048cf246cebb41e4fadfc70af12314c43f3428164d474a8d8107
# 8. registry registration (gate-verifiable = registered)
grep -n 'relcur' .agents/tester/PACKS.md
```

## Bash-raises used

**None.** No bash raise was consumed by this lane. Every long operation ran in the background `proc` lane instead of a blocking bash call: the PyInstaller stage build (proc-a998db0f), `npm ci` (proc-2e0b1945), the FE build (proc-1713b834), and the stage re-runs (proc-b887d083, proc-c3c7c20e). Blocking bash calls stayed well under the 600 s default.

## Complexity self-assessment

**MEDIUM** — the band assessed at plan-open (before dispatch); no handover was raised, therefore no override applies and the band is not softened. Basis at plan-open: four small modules with disjoint file sets, no schema/migration work, no core-daemon surgery, and the risky part (the live symlink repair) bounded to one audited operation with proven preconditions. Post-plan drivers absorbed without changing the band: the pre-push absorb of a foreign retention rewrite (one content conflict resolved semantically by the owning coder), the host crash+reboot mid-lane, two concurrent `origin/latest` advances (one absorbed, one adjudicated via the journaled stale-case override), the slot-collision double bump, and a stage-time frontend-toolchain gap. If this lane were re-scoped from scratch, the merge-conflict + crash + live-race handling would push it toward `high`; the shipped outcome stayed inside `medium` because each conflict was resolved by the owning coder in its own file set and no module exceeded one report + bounded revision cycles.

### Coordination
- Coordination note (root-cause finding + pin-(b) transfer + merge-resolution summary): `/home/nea/ensemble-src/.agents/shared/planning/relcur-pointer/COORDINATION-TO-retention-lane-60d008ef.md` (copy committed at `.agents/shared/planning/sonic-team/relcur-pointer/COORDINATION-TO-retention-lane-60d008ef.md`).
- Sibling lane: task `60d008ef`, branch `fix/retention-evict-journal`, worktree `/home/nea/ensemble-src-wt-retention-evict` — owns staged-dir eviction + journal rows + the canonical eviction-safety pins.
