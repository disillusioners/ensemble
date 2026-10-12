# TEST PHASE — install-dir protection (Layers 1+3+4) — `feature/install-dir-protection`

- **Date**: 2026-10-12 (phase ran ~20:55Z–~21:5xZ UTC)
- **Commission**: install-dir protection layers 1+3+4 (incident 891820f3 followup) — TEST phase, report-only
- **Worktree**: `/home/nea/ensemble-src-wt-install-dir-protection`, branch `feature/install-dir-protection`
- **Commits under test**: `ef5282132 → c527cdd32 → 6ad917338 → 7dcf86942 → f0c394388` (primary evidence) **plus `ffb4acd6a`** (review round 3 landed MID-PHASE; layer-1 packs + tools-complement re-verified at tip). Base = `c8a3fbf6a` (latest).
- **Workers**: 9 (W1 `01e13eab` layer-1 packs · W2 `ff698649` upgrade-surface sweep · W3 `0e6e72d5` unit sweep minus tools · W4 `886ea88e` maintenancer base-proof · W5 `a2b243d5` host L3+L4 · W6 `bd67d124` host L1 inert · W7 `f9db23ee` tools complement · W8 `d4f4963c` sweep base-proof + flaky budgets · W9 `9966e261` HEAD re-verify). Zero re-dispatches; zero junk reports.

## VERDICT

| Part | Verdict |
|---|---|
| A — repo tests (worktree) | **PASS** — zero branch-caused failures; every red base-proven at `c8a3fbf6a` or exact-match to standing QUARANTINE ledger rows |
| B — host armor (read-mostly) | **ALL PASS** — L3+L4 PROVEN live; L1 staging PROVEN inert; refusal drills PROVEN (77/76) |
| C — commission statement | detection **PROVEN** · recovery **PROVEN** · prevention **STAGED** (user-gated flip) |

**Branch is test-clean at both `f0c394388` and tip `ffb4acd6a`.**

## Scope Decision

Full leader-enumerated scope run (Parts A+B as dispatched) — warranted: commission-level defense-in-depth verification. Three follow-ups added by tester for coverage/classification rigor (not scope expansion): tools-complement sweep (closed the `tests/unit/tools` gap left by the W2 subset narrowing), base-proof of all sweep failures (dispatch standard: reasoned-pre-existing ≠ proven), HEAD re-verify after the branch moved mid-phase.

---

## PART A — REPO TESTS

### A.1 Layer-1 packs (W1 @ f0c394388; re-verified W9 @ ffb4acd6a)

| Pack | Command | Result @ f0c394388 | Result @ ffb4acd6a (tip) |
|---|---|---|---|
| pytest layer-1 | `pytest tests/unit/tools/test_upgrade_install_root_layer1.py -q` | **PASS 43/43** (5.9s) | **PASS 43/43** (5.8s) |
| bash layer-1 | `bash tests/test_install_root_layer1.sh` | **PASS 36/36 assertions** (0.8s) | **PASS 36/36** (0.8s) |

`ffb4acd6a` touched only `docs/runbooks/install-dir-protection-layer1-arm.md` + `scripts/protection/ensemble-promote` (76 lines) — zero test files. Env proof every run: `daemon.__file__` resolved inside the worktree (editable-install trap checked 5×).

### A.2 Regression sweeps

**Inventory**: `pytest --collect-only tests/unit tests/unit/tools` → **16,351 tests**.

| Sweep | Worker | Scope | Result | Runtime |
|---|---|---|---|---|
| Upgrade-surface subset + maintenancer | W2 | 8 files (5× tools/test_upgrade_*, layer1, test_install_audit.py, test_maintenancer_kb_coverage.py) | **433P / 1F(known) / 1S** of 435 collected | 24.3s |
| tests/unit minus tools | W3 | 19 chunks, all subdirs + 330 top-level files | **12,687P / 64F / 21E / 55S** of 12,827 run | ~21 min (max chunk 252s) |
| tools complement | W7 | remaining 56 of 62 tools files | **3,124P / 8F / 5S** of 3,137 collected (@ ffb4acd6a) | 319s (3 chunks, max 190s) |
| **Executions total** | | | **16,244P / 73F / 21E / 61S** (16,399 executions; incl. 41-test kb_coverage overlap W2∩W3 + 6 flaky-budget re-runs) | |

W2 scope note: literal `tests/unit/tools` dir = 3,564 tests ≈ 370s (> 300s cap) → narrowed to the 8-file upgrade-surface subset exercising the branch-touched files; 40 short of the developer's ~475 anchor, rationale documented (peripheral files). Coverage gap closed by W7.

### A.3 Failure classification — ZERO branch-caused

**Unique failures: 72F + 21E. All pre-existing.**

| Class | Count | Evidence |
|---|---|---|
| Stable drift families, base-proven | 61F + 21E | W8: all 17 failing files re-run at base `c8a3fbf6a` (temp worktree `/tmp/ensemble-base-sweep`, uv venv, import-proved, removed after) → **61F + 21E ≡ 61F + 21E**, per-family match. Two 1-node count swaps (`configure_builtin_idempotency` 16↔15, `repository_claim_lane` 5↔6) = batch-ordering artifacts within identical drift clusters. Services 9F == maintenance-gate 2026-10-12 documented 9F exactly. |
| Maintenancer release-tag pin | 1F | W4 base-proof: **FAILS-AT-BASE** — same assertion at `c8a3fbf6a` (`EXPECTED_RELEASE_TAG=v0.12.4` vs pyproject `v0.20.0`). Root cause: `5f2936c38` "chore(release): bump version to v0.20.0" (2026-10-12 02:07Z) forgot the pin; ANCESTOR of base; both files byte-identical base↔HEAD. **Not a branch blocker.** |
| Tools-side families | 8F | W7 @ ffb4acd6a → exact node-ID match to standing ledger rows: prompt-integrity ×6 (row 2026-10-07: developer[v2]/charter bare-`.md` ×4 + odd fences ×2, byte-identical at base 2753ee78d) + `TestExploreCallerModelOverrides` ×2 (row 2026-10-03, KB-stale-vs-1a40bc56 family, base-attributed 24705dc4). |
| Flaky | 2F | `llm_failover_v2` latency-caps: **3/3 PASS solo** → batch-only flake, no quarantine. `plane_sync` concurrent-calls: **2P/1F solo** → FLAKY CONFIRMED (already QUARANTINE row 2026-09-04; escalated from partition-only to solo-visible — addendum appended). |

Drift families (all base-attributed, cross-referenced in QUARANTINE.md consolidated row 2026-10-12): job_queue_proxy_phase1 canonical-map ×7 · scheduling once-anchor time-bomb ×1 · service_tool pid_dead ×1 · plugin snapshot hash 29-vs-38 ×1 + sync-runner ×3 · builtin_mcp ×17E + context7 ×4E (`service_tool` mock-gap) · vision-fixture ×11 · coder_developer_migration ×5 · configure_builtin_idempotency ×15↔16 (newly ledgered) · devops meta ×3 · gaia tools.allow ×3 · project_manager prompt ×2 · repository_claim_lane ×5↔6 (newly ledgered) · terminal_reason MIRROR_SET ×1 · api_router ×2.

---

## PART B — HOST ARMOR VERIFICATION (read-mostly; sudo passwordless available, no fallbacks needed)

### B.4 L3 snapshots — PASS
- Timer `ensemble-install-snapshot.timer` **active (waiting)**, 30-min cadence; last service run SUCCESS 1.26s (`snap-20261012T210007Z ok (set=5, total=184M, linkbase=snap-20261012T203002Z)`).
- `/home/nea/.install-snapshots/` = `drwx------ root root` (700) with 5 timestamped snapshots; newest 166–184M, 5,358 files.
- Retention script (deployed `/usr/local/sbin/ensemble-install-snapshot.sh` vs repo copy): logically identical — byte-diff confined to the repo-side 6-line banner. Retention = newest **96** snapshots (48h @ 30-min), `rsync -aHAX --link-dest` hardlink trees (near-zero cost for unchanged files), lexicographic prune.

### B.5 L3 recovery drill — PASS, EXCEEDS PLAN
- Throwaway restore (`cp -a` newest snapshot → `/tmp/ensemble-restore-drill-*`): **3.02s wall** (plan claim ~2 min; incident baseline ~2h manual rebuild → **~2400× faster than rebuild, ~40× faster than plan**).
- Integrity: 5,358/5,358 files; top-level shape correct (`config.yaml current data docs launcher.sh releases` + dotfiles incl. `.env`; `releases/v0.20.0/` + `state.json`).
- **`.env` byte-identical: yes** (cmp, content never printed). /tmp cleaned and verified gone.

### B.6 L4 treecheck — PASS
- Timer active, NEXT/LAST exactly 60s apart; service triplets (`Starting → Deactivated → Finished`) every 60s, **zero CRIT in sliding window = healthy silence**.
- Devops drill CRIT on disk: `19:40:24Z TREE LOSS DETECTED: /tmp/ensemble-treecheck-drill-absent-533230-mustnotexist absent …` (dispatch said "~19:45Z"; actual = timer-arm moment; the only CRIT today).
- **Re-fire drill (ours)**: absent throwaway path → exit 2 + new CRIT `21:03:34Z` within seconds. Interface: `$1` arg or `ENSEMBLE_TREECHECK_PATH` env (default `releases/state.json`); nea-runnable (logger-only writes). Never pointed at the real path.

### B.7 L4 auditd — BLOCKED-absent stands
`auditctl` ABSENT, no auditd unit (client libs only) — attribution sub-layer unavailable by deploy decision; treecheck journal attribution is the active substitute. One line, as dispatched.

### B.8 L1 staging INERT — ALL 14 CHECKS PASS
- `ensemble` user: uid 999, **locked (L)**, `/usr/sbin/nologin`.
- `/usr/local/bin/ensemble-promote`: root-owned 755; diff vs repo = **exactly the 6-line mapping header, 0 executable-line delta — deployed copy already carries round-3 (`ffb4acd6a`) content** (verified via NEW-1/NEW-2 markers present in the deployed file).
- **No sudoers grant** (`/etc/sudoers`, `/etc/sudoers.d/` greps empty); **no unit references** (`/etc/systemd/system` grep + `list-unit-files` empty).
- Refusal drills (runbook-verbatim): non-allowlisted `lib.sh` → **rc=77** (`REFUSED (C1) — script not on the enumerated allowlist`); allowlisted-but-writable `promote.sh` as nea → **rc=76** (`REFUSED (C1) — script writable by invoking user…`). Both journaled with attribution (`-t ensemble-promote`, invoker/owner/mode encoded). **Zero mutation proven**: no `exec runuser` / `promote starting` line anywhere in the 3h journal window — both drills died at the gates.

### B.9 Drop-in candidate inert — PASS
`~/.ensemble-layer1-unit-drop-in.candidate.conf` staged only (1371B, 19:42); `/etc/systemd/system/ensemble-main.service.d/` absent entirely; live unit `User=nea`; `DropInPaths=` empty.

---

## ensure.md VALIDATION STATUS (Core, blast-radius scoped)

| Requirement | Status | Evidence |
|---|---|---|
| Critical: no regressions in changed packs | **PASS** | Layer-1 packs 43/36 green @ both HEADs; upgrade-surface 433P/1F-known; all sweep reds base-proven/ledgered — branch-caused = 0 |
| Critical: `concurrency_atomic_unit_test` pack | **N/A by blast radius** (branch touches no concurrency surface: `daemon/tools/upgrade_*` L1 + scripts + runbook only) — freshest evidence: pack PASS 99P/0F/74S earlier TODAY at sibling gate `fe5efcd58` (shared base lineage, PACKS.md) | not re-run this phase |
| Critical: no sync DB calls on event loop | covered by the same pack — same N/A-by-scope note | — |
| Critical: `dev.sh --timeout-graceful-shutdown 10` | **N/A by blast radius** — `dev.sh` untouched by any of the branch's 6 commits (file lists verified W1/W9) | static grep not re-run |
| Release Gate | **NOT triggered** — feature-branch verification, not a promote/release | — |

No contradictions between ensure.md methods and pack/timeout rules this phase (all validations ran as chunked packs with dual-layer timeouts).

## QUARANTINE ACTIONS (this phase)
- Consolidated gate row added (82 nodes base-attributed @ `c8a3fbf6a`, cross-referenced families, 2 newly-ledgered clusters).
- Row-2026-09-04 `plane_sync` addendum: flake escalated to solo-visible (2P/1F isolation budget).
- No un-quarantines.

## ANOMALIES / FLAGS
1. **Branch moved mid-phase** (`f0c394388` → `ffb4acd6a`) — absorbed: layer-1 packs re-verified at tip (43/36 unchanged), tools-complement ran at tip, wrapper sync re-diffed (0 executable delta). Final evidence covers both commits.
2. Snapshot dir mtimes are rsync-preserved (all equal) → `ls -1t` returns lexically-first, not newest. Harmless here (identical hardlink shapes; retention sorts lexicographically by design). Suggested one-line runbook note for devops: use `sort | tail -1` for "newest" in ad-hoc inspection.
3. Worktree carried one actively-appended tracked scratch file `.agents/shared/planning/designer-agent/install-audit.jsonl` (opendesign daemon audit sink) — dirty at every before/after proof, hash-stable across workers where checked; not a test input.
4. `test_supervision_python.py` collects 0 tests (conditional collection — legitimate).
5. `uv` not on default PATH on this host → use `/home/nea/.local/bin/uv` (W8 finding).
6. Editable-install trap variant (W8): running python from a foreign cwd shadows the target worktree via sys.path cwd entry — always `cd` into the worktree before import-proof.

## ACTION NEEDED (base-level test-debt, NOT branch blockers)
- [ ] Fix `EXPECTED_RELEASE_TAG` pin (owner: release process; `5f2936c38` bump forgot it) — 1-line, deterministic red on every future gate until fixed.
- [ ] Test-debt commissions for the 2 newly-ledgered clusters: `configure_builtin_idempotency` (bootstrap-path rot) and `repository_claim_lane` (StrictTwoWay/LaneSignature drift).
- [ ] Watch `plane_sync` concurrent-calls flake (solo-visible now); quarantine stands.
- [ ] 82-problem drift baseline: standing ledger rows carry the routed follow-ups (mock-gap `service_tool` fixtures, vision-fixture allowlist, plugin manifest counts, etc.).

## DOCUMENTATION UPDATED
- [x] RESULTS/2026-10-12-install-dir-protection-test-phase.md (this file)
- [x] PACKS.md — commission section + pack table
- [x] QUARANTINE.md — consolidated row + plane_sync addendum
- [x] LESSONS/2026-10-12-branch-moved-mid-phase-and-worktree-venv-traps.md
- [ ] rules/ensure.md — untouched (user-owned)

## CODE CHANGES
None. Report-only phase: zero source/test modifications, zero commits on the feature branch by any worker. (Tester docs under `.agents/tester/` committed separately per convention.)

---

## PART C — COMMISSION-LEVEL VERIFICATION STATEMENT

**Damage class: agent-authored destructive op against the live install dir (`/home/nea/agents-ensemble`).**

| Layer | Capability | Status | Proof |
|---|---|---|---|
| L4 — **detection** (treecheck) | **PROVEN** | 60s silent-healthy cadence; CRIT fires on absent-path drills (devops 19:40:24Z + tester re-fire 21:03:34Z, journal-attributed, nea-runnable) |
| L3 — **recovery** (snapshots + restore) | **PROVEN** | Timer active 30-min cadence, 5 snapshots, root:700, RETAIN=96/48h, hardlink rsync; throwaway restore drill 3.02s, 5358/5358 files, `.env` byte-identical — vs ~2h manual rebuild baseline |
| L1 — **prevention** (permission separation) | **STAGED — pending user-gated window** | Mechanism PROVEN: wrapper deployed & in sync with round-3 content; refusal gates fire pre-execution (77 non-allowlisted / 76 writable-invoker) with journal attribution; `ensemble` user exists/locked/nologin. Deployment NOT armed: no sudoers grant, no unit drop-in installed, live unit still `User=nea` — exactly the intended inert state until the user flips it |
| L4 — auditd attribution | **BLOCKED-absent** (by deploy decision; `auditctl` missing) | journal attribution is the active substitute |

**Could not verify / deliberately not performed:**
- A real restore ONTO the live path — intentionally not performed (commission constraint: never mutate the live install dir). Restore capability is proven via the throwaway-target drill (shape + count + byte-equality); the live-path operation itself remains user-gated.
- The L1 armed state (post-flip behavior under `User=ensemble` + sudoers) — out of scope by the same user gate.
- auditd-based attribution — blocked by absent tooling, not by test failure.

**Bottom line**: the incident's damage class is now covered by PROVEN detection (≤60s to CRIT) and PROVEN recovery (~3s restore vs ~2h rebuild); prevention is one user-gated flip away, with its enforcement mechanism already proven on the deployed artifact.
