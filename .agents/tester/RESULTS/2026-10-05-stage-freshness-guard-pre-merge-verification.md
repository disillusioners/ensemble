# Thread C — stage.sh Freshness Guard: Pre-Merge Verification

- **Date**: 2026-10-05 (02:06–02:45 UTC window)
- **Worktree**: `/home/nea/ensemble-src-wt-stage-guard` · branch `feature/stage-freshness-guard` · HEAD `a0a61906` (verified)
- **Base / merge target**: `ac1bf7b9` (`latest` tip)
- **Commission**: pre-merge verification; verdict gates merge into `latest`. Fully sandboxed (mktemp fixtures / fake INSTALL_DIR only). Live `~/agents-ensemble` and `~/agents-ensemble-demo` never touched; no promote/rollback/restart; no merges/pushes/version bumps. This file is deliberately UNCOMMITTED.
- **Tester instance**: c9bfbde7 (Test Leader); all execution via 21 dispatched workers (roster at end).

## Scope Decision

Commissioned scope honored exactly: shell-only surface (`scripts/upgrade/{stage.sh,lib.sh,_build_frontend.sh}` + their packs). No Python suite. Adjacent surface = 11 shell packs sharing `lib.sh`/`stage.sh`/deploy/launcher/watchdog (leader-authorized) + 3 base-leg A/B classification runs for every adjacency red. No scope expansion beyond commission.

## 1. Repo State (verified, not trusted)

| Check | Expected | Measured | Verdict |
|---|---|---|---|
| HEAD | a0a61906 | `a0a6190672176803…` | ✅ |
| Branch | feature/stage-freshness-guard | same | ✅ |
| Commits since base | 5 (1c8b8bf0→23c80982→736d40b5→37fab3a1→a0a61906) | same set, same order | ✅ |
| Tree | clean | `git status --porcelain` empty (prior audit's 4 dirty files are now committed) | ✅ |
| Diffstat | +2092/−7, 6 files | identical per-file + cumulative | ✅ |
| `bash -n` | — | all 8 `scripts/upgrade/*.sh` + `launcher.sh` OK | ✅ |

## 2. Fix-Round-3 Re-Verification (reviewer's recipe — reproduced independently)

All 3 probes PASS in worker-built mktemp fixtures (pack's fixture scripts never executed).

| Probe | Method | Result | Key evidence |
|---|---|---|---|
| d-1 dispatch: UNREADABLE | chmod-000 sidecar | **PASS** — rc=78, message "exists but is unreadable" (reason=provenance-missing; token collapse absent/unreadable is intentional per tests:894-896); NOT shape-gate text | exact stderr captured |
| d-1 dispatch: MALFORMED/laundering | hand-crafted invalid JSON, 2× `"git_dirty"`, 2× `"git_head"` decoys (python3-verified unparseable) | **PASS** — rc=78, "is MALFORMED (… laundering signature)" (reason=provenance-malformed); NOT "git_head missing/empty", NOT unreadable/absent branch | 4-state dispatch live; d-1 fix (parent-side invocation + mktemp stdout capture, lib.sh:1946-1955) proven working |
| G3 rm-failure guard | directory-as-sidecar (EISDIR forces `rm -f` failure; same mechanism as pack case xii) | **PASS** — rc=1, WARN names failure + payload risk, STAGE_TMP cleaned, stand-in cannot ship | fragment-level invocation (verbatim stage.sh:392-396 pattern) — justified: FE verifier precedes cp+rm so the failure case is unreachable end-to-end; **differential control**: without the `||` guard the stand-in ships |

## 3. Pack Runs (all measured; claims independently reproduced)

| Pack | Result | Detail |
|---|---|---|
| `tests/test_stage_freshness_guard.sh` ×3 | **118/0 ×3 PASS** | 46s each, deterministic, no inter-run variance (tag-SHA deltas in output are fixture-only). Sandbox-clean (zero real-install refs). Claim verified. |
| `tests/test_release_journal.sh` A/B | **315/19 both sides; failure-union diff ∅** | HEAD 188s / base(git-archive ac1bf7b9) 174s. One apparent name divergence was a wall-clock `(got delta N)` suffix inside the SAME failing test — normalized → intersection 19/19 identical. The 19 are pre-existing at base → **no new failure, no blocker** (commission's explicit criterion met). Claim verified (vs prior dev 320/14 ×2 claims). |
| `tests/test_atomic_flip.sh` | **36/0 PASS** | 1s. Claim verified. |

## 4. Commissioned Acceptance Scenarios (own sandbox fixtures; production writer `_provenance_write` used for all sidecars)

| # | Scenario | Exit | Key evidence |
|---|---|---|---|
| (i) | STALE BINARY (2026-10-02 incident shape: sidecar stamped at ac1bf7b9 via production writer vs tree @ a0a61906) | **78 REFUSED** | "built at ac1bf7b979fa, current HEAD=a0a61906…reason=stale-provenance"; no payload; durable refusal journal on virgin install dir. Existence-only staging confirmed un-passable. |
| (ii) | FRESH MATCH (writer-stamped at staged tree) | **0 PROCEEDED** | full payload (ensemble-prod/agents/frontend/launcher/config/manifest), sha256 match, journal history empty, **0 sidecars shipped** (G3 happy path) |
| (iii) | FE TOUCHED no-rebuild → REFUSED; with `_build_frontend.sh` rebuild (real `ng build`, 27s, timeout-capped) → PROCEEDS | **78 → 0** | refusal names the FE artifact specifically (binary passed); after rebuild: rc=0, sha-matched, idempotent re-stage rc=0 |
| (iv) | BLIND REUSE (dist present, NO sidecar — pre-guard world) | **78 REFUSED** | "no build provenance… Blind reuse of prebuilt artifacts is structurally impossible (cn 0472b31f trap family)…reason=provenance-missing"; only state.json on install dir, no payload |
| (v) | Override discipline | **argv 0 / env×4 all 78** | `--allow-stale-stage` argv-only: proceeds, journal records 2× `stage_freshness_override … operator_accepted=true`. Env spellings `ALLOW_STALE_STAGE`, `ENSEMBLE_ALLOW_STALE_STAGE`, `STAGE_ALLOW_STALE`, and even literal internal `STAGE_FRESHNESS_OVERRIDE` all REFUSED — `stage.sh:88` top-level `=0` shadows inherited env; sole `=1` writer is the argv case (stage.sh:122); 6 verifier sites gate on in-script var. Journal diff binary-conclusive. |

## 5. Adjacent Shell Packs (11) + Base Classification (A/B via `git archive ac1bf7b9`)

| Pack | HEAD | Base ac1bf7b9 | Classification |
|---|---|---|---|
| adopt_unit | 63/0 ✅ | — | green |
| deploy | **51/2 ❌** | **51/2 ❌** (identical names; reproduced on main checkout @ base) | **PRE-EXISTING** — fixture never copied `scripts/upgrade/lib.sh` (stop-ensemble.sh dependency since 6d564c35, 2026-09-29); 1-line test-side fix suggested, not applied |
| launcher | **204/3 ❌** (§8: 8c×2, 8g) | **204/3 ❌** (identical names, 14s) | **PRE-EXISTING** — same 3 §8 journal-sweep failures at base; `launcher.sh` untouched by branch; part of the same pre-existing sweep-semantics family as the journal base-19 |
| promote_cgroup_survivorship | 45/0 ✅ | — | green |
| stop_ownership | 43/0 ✅ | — | green |
| supervision_classify | 237/0 ✅ | — | green |
| **supervision_e2e** | **12/43 ❌** | **52/0 ✅** | **🔴 NEW-BY-BRANCH** |
| **supervision_journal** | **11/26 ❌** | **39/0 ✅** | **🔴 NEW-BY-BRANCH** |
| supervision_stop_handback | 152/0 ✅ | — | green |
| supervision_twins | 84/0 ✅ | — | green |
| watchdog_watcher | 60/0 ✅ (×2) | — | green |

**NEW-BY-BRANCH failure class (both supervision packs, single root cause):** the packs' `--skip-build` stub binaries (stub-prod-a/b, E1–E4 stubs) carry NO `.build-provenance.json` sidecars; the new guard refuses them (`reason=provenance-missing`), FATALing each stage leg and cascading 43 / 26 downstream assertions. Neither pack is in the branch diffstat. The guard is behaving as designed (its refusal text even documents the fixture remedy); the branch failed to migrate its `--skip-build` consumers. Remediation is test-code-only and small: stamp sidecars via `_provenance_write` in each pack's fixture setup, or pass `--allow-stale-stage` on fixture-mode invocations.

## Findings

| # | Severity | Finding | Evidence |
|---|---|---|---|
| F-1 | 🔴 critical (merge blocker) | Two adjacent DR-leg packs regress green→red on this branch: supervision_e2e 52/0→12/43, supervision_journal 39/0→11/26 | §5 A/B, base green both |
| F-2 | 🟠 important | `tests/test_stage_freshness_guard.sh` (56 KB, first-class pack) unregistered in `.agents/tester/PACKS.md` — must be registered at merge | W0 grep: zero matches |
| F-3 | 🟠 important (pre-existing, not branch-caused) | deploy pack 2 fails latent since 2026-09-29 (fixture lacks lib.sh copy); 1-line fix | worker reproduced 51/2 at base |
| F-4 | 🟢 nice-to-have (pre-existing) | `test_atomic_flip.sh` pack-registration gap — PACKS.md L518 already marks "register at merge if desired" | W0 |
| F-5 | 🟢 info | release_journal base-19 family + 2 test-side artifacts (`assert_not_contains` helper gap — line 269 HEAD / 203 base, same gap both sides; BSD `date -j` noise) — all pre-existing, identical both sides | W2 |
| F-6 | 🟢 info | Sandbox runs need `ENSEMBLE_ROLLBACK_SAFE=1` (65 real destructive-DDL migrations) — orthogonal to guard, expected | W5 |
| F-7 | 🟢 info | G3 rm-failure exercised at fragment level (verbatim stage.sh:392-396; identical to pack case xii) with differential control — unreachable end-to-end because the FE verifier precedes cp+rm | W4 |

## Verdict: **BLOCKED** (narrow, cheap unblock)

Feature itself is fully verified correct: all probes PASS, all 5 commissioned acceptance scenarios PASS with exact exit codes, own pack 118/0 ×3 deterministic, journal A/B clean (no new failures), atomic_flip 36/0, repo state exact. **The merge is blocked by F-1 only**: merging as-is leaves `latest` with two newly-broken DR-leg packs (supervision_e2e, supervision_journal) — exactly the "merge-window scoped-pack gates missed the full-dir family" debt pattern this project tracks (v0.16.0 lesson).

**Unblock path (test-code only, no production change):**
1. Fixture-align both supervision packs (`_provenance_write` sidecars for `--skip-build` stubs, or `--allow-stale-stage` on fixture invocations) — expected ~1 line per stage invocation.
2. Re-run both packs green + re-run `test_stage_freshness_guard.sh` (118/0).
3. Register the new pack in PACKS.md (F-2) in the same follow-up commit.
Then merge.

## Gaps

- ~~Launcher base-leg A/B~~ **RESOLVED (addendum, 02:5x UTC): measured PRE-EXISTING** — base ac1bf7b9 runs 204/3 with the identical 3 §8 failures (`8c journal byte-identical`, `8c current symlink untouched`, `8g stale window rollover`), 1:1 name match with HEAD, 14s. The initial miss was a tester dispatch gap (spawn-without-send); dispatched upon discovery, measured, classification confirmed by evidence not hypothesis. Not a blocker; belongs to the pre-existing sweep-semantics family (test-fixture expectations likely need adjustment per the base-leg worker's side note).
- Adjacency base-legs were run only for red packs (green-at-HEAD packs need no classification by definition).

## Worker Roster (21)

W0 tc0-repo-inventory · W1 tc1-pack-stagefresh · W2 tc2-pack-journal-ab · W3 tc3-pack-atomic-flip · W4 tc4-probes-dispatch-g3 · W5 tc5-scn-stale-fresh-fe · W6 tc6-scn-reuse-override · adj-* ×11 (adopt-unit, deploy, launcher, promote-cgroup, stop-ownership, supervision-classify/e2e/journal/handback/twins, watchdog-watcher) · ab-supervision-e2e-base · ab-supervision-journal-base · ab-launcher-base (late-dispatched; classification delivered). All workers: verification-only, worktree left clean at a0a61906, no live/demo contact, no process kills, dual-layer timeouts honored, no commits made.

---

# Re-Gate (F-1 fix verification) — commit 1ed5f051, 2026-10-05

- **Commission**: focused final re-gate of F-1 fix `1ed5f051` ("test(supervision): port production-writer provenance stamping into e2e+j fixtures") on top of a0a61906. Same fences: sandboxed, live/demo untouched, no ceremony, no merge/push. 4 workers (rg1 commit-freeze/grep/registry; rg2 journal; rg3 e2e; rg4 stagefresh).

## Commit + freeze verification (all measured)

| Check | Result |
|---|---|
| HEAD | `1ed5f05191cf814bd61aa0c291d6e0b7c2ecb762` ✅; exactly ONE commit on a0a61906 (parent verified) |
| Diffstat | **+83/−0, exactly 2 files** (journal 42, e2e 41) — matches claim |
| Product freeze | **INTACT** — no path outside the two test files; scripts/daemon/docs/launcher untouched since a0a61906 |
| Tree | clean (tracked); sole untracked file = this RESULTS file (tester scratch, by design) |
| `--allow-stale-stage` | **ABSENT** — zero matches in both pack files AND in the full commit diff (broader `stale-stage/stale_stage` sweep also zero); unsanctioned fallback not used |
| Writer | **REAL** — both packs port the cn 0472b31f `_stamp_provenance_for_repo` pattern: source `scripts/upgrade/lib.sh` in a subshell with `REPO_ROOT=$repo`, call production `_provenance_write` (lib.sh:1640) + `_git_dirty_porcelain`, invoked PRE-stage, stamping BOTH binary and FE artifacts; no hand-rolled JSON |

## Pack results @ 1ed5f051

| Pack | Result | Signature check |
|---|---|---|
| tests/test_supervision_journal.sh | **39/0/0 PASS** (23.5s) | zero `provenance-missing`/`stale-artifact stage refused` — base-level green restored |
| tests/test_supervision_e2e.sh | **52/0/0 PASS** (63s; E1–E4 + host-clean leg all green) | zero refusal/FATAL signatures |
| tests/test_stage_freshness_guard.sh | **118/0 PASS** (46s) | no collateral from the test-code fix |

## F-2 disposition (registry)

`.agents/tester/PACKS.md`: `test_supervision_journal` = 3 rows (L204/L249/L455) ✅; `test_supervision_e2e` = 3 rows (L205/L248/L456) ✅; **`test_stage_freshness_guard` = zero rows — remains UNREGISTERED**. The dev's commit message explicitly scopes this out of the unblock round. F-2 stays OPEN as 🟠 important, NON-BLOCKING: register the new pack in PACKS.md at merge time (merge-step action for giter/dev, ~1 row).

## Re-gate verdict: ✅ **READY-TO-MERGE**

F-1 closed by evidence: both regressed packs restored to base-level green via the production-writer fixture pattern (no override fallback), product frozen, guard's own pack unaffected. Sole open item F-2 (pack registration) is non-blocking registry hygiene to complete at merge.

**Gating line:** READY-TO-MERGE — supervision_journal 39/0 · supervision_e2e 52/0 · stage_freshness_guard 118/0, all measured at 1ed5f051 with product freeze intact.
