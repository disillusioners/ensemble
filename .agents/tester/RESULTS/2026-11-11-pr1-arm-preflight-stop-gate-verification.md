# PR1 Gate — arm-preflight stop-script gate (`fix/promote-stop-script-preflight`)

- **Date:** 2026-11-11
- **Tester:** agent `tester` (all execution delegated to workers; no test code executed by the tester)
- **Branch:** `fix/promote-stop-script-preflight`
- **Verification tip (as commissioned):** `5fce2dbdd2f6aa68b35bc4366835acbafed3a1eb`
- **Final merge tip (moved mid-gate; leader steer):** `add499b9380197d7502dff7cc7e3b2fd9e06e7f2`
- **Lineage:** `6ca643ad7` (base) → `097ab1416` → `5fce2dbdd` → `add499b93`
- **Base for red classification:** `6ca643ad7`
- **VERDICT: SHIP for `add499b93` — zero branch-caused reds.**

---

## 1. Scope

Commissioned: python lane (3 files) · deferred shell packs (upgrade-tool interlock, preflight ceremony
readiness, preflight mutation matrix) · adjacent quick suites (stop-ownership / launcher / watchdog) ·
red classification against base `6ca643ad7` · read-only live confirm.

Change set (`git diff --name-status 6ca643ad7..5fce2dbdd`, 3 files):

```
M	daemon/tools/upgrade_tools.py            (+51 / -2   at 5fce2dbdd; +77/-26 more at add499b93)
M	docs/runbooks/upgrade-overhaul-watch.md
M	tests/unit/tools/test_upgrade_tools.py
```

`scripts/stop-ensemble.sh`, `launcher.sh`, `dev.sh`, `scripts/upgrade/*` are **untouched** by the branch —
the adjacent suites are seam guards only.

## 2. Evidence base (read why)

The commissioned worktree was **not frozen**: a concurrent lane (`giter`, claim heartbeat stale at
02:52Z) wrote uncommitted WIP into the three files under test during the gate window (porcelain grew
1 file → 3 files mid-run; `upgrade_tools.py` sha `119df191…` → `ee8a8b84…`; later `git add`-ed, then
committed as `add499b93`). The tester did **not** restore or tidy those files (foreign-WIP discipline).

Gate legs were therefore executed against **detached worktrees at the pinned commit** (same committed
bytes, zero writes to the reviewed tree), each with:
`git worktree add --detach <path> <sha>` → fenced `.env` copied **verbatim** from the commissioned
worktree (`POSTGRES_DB=ensemble_dev`, `DAEMON_PORT=8079`, `ENSEMBLE_SELF_ENV=dev`, 0 × `ensemble_prod`)
→ `uv sync --frozen --python 3.13.3` → import proof (`daemon.__file__` resolves **inside** the leg tree)
→ post-run `git status --porcelain` empty → `git worktree remove` (no `--force`).

Shared evidence tree for the original tip: `/tmp/pr1-frozen-01` @ `5fce2dbdd`.

**Interpreter:** CPython **3.13.3** in every leg (`uv sync --frozen --python 3.13.3`, per-tree venv, never
the shared/system python). `uv.lock` unchanged everywhere (`--frozen` held).

## 3. Results — per suite, with the tip each ran at

| # | Suite / pack | Tip | Invocation | Result | Runtime | Interp. |
|---|---|---|---|---|---|---|
| 1 | python lane (3 files) | `5fce2dbdd` | `timeout 300 bash scripts/run_tests_scrubbed.sh <3 files> --tb=short -q -rA -p no:cacheprovider` | ✅ **205/205**, 0 skipped/deselected | 14 s | 3.13.3 |
| 2 | python lane (3 files) | **`add499b93`** | same | ✅ **208/208**, 0 skipped/deselected | 34 s (pytest 18.36 s) | 3.13.3 |
| 3 | `upgrade_tool_interlock_unit_test` | `5fce2dbdd` (dirty tree — cross-check only) | `timeout 300 bash test/packs/upgrade_tool_interlock_unit_test.sh` | ✅ 302P / 1S | 12 s | 3.13.3 |
| 4 | `upgrade_tool_interlock_unit_test` | `5fce2dbdd` (clean tree) | same | ✅ pack PASSED (per worker trace); final report not delivered — worker terminated in a trailing analysis command; **superseded by #5** | — | 3.13.3 |
| 5 | `upgrade_tool_interlock_unit_test` | **`add499b93`** (clean tree) | same | ✅ **302P / 1S / 0F**, `RESULT: PASS` | 37 s (pytest 22.89 s) | 3.13.3 |
| 6 | `preflight_ceremony_readiness_test` (both modes) | `5fce2dbdd` (clean tree) | `timeout 300 bash tests/packs/preflight_ceremony_readiness_test.sh [--expect-implemented]` | ⚠️ **39P / 1F** both modes (fail id `E2-fix`) — **pre-existing** (see §4) | 9 s / 9 s | 3.13.3 |
| 7 | `preflight_ceremony_readiness_test` (both modes) | **`add499b93`** (clean tree) | same | ⚠️ canonical **39P / 1F** (`E2-fix`); shimmed diagnostic **40P / 0F** exit 0 both modes | 8.5 s / 9.2 s | 3.13.3 |
| 8 | `preflight_mutation_matrix_test.sh --legs M23…M28` | `5fce2dbdd` (throwaway tree, shim) | `timeout 300 bash … --legs M23,M24,M25,M26,M27,M28` | ✅ **6/6 green-and-red-proven**, `TREE: clean`, exit 0 | 294 s | 3.13.3 |
| 9 | `preflight_mutation_matrix_test.sh --legs M34,M35,M39,M40,M41` | `5fce2dbdd` (throwaway tree, shim) | `timeout 300 bash … --legs M34,M35,M39,M40,M41` | ✅ **5/5 green-and-red-proven**, `TREE: clean`, exit 0 | 262 s | 3.13.3 |
| 10 | `preflight_mutation_matrix_test.sh --legs M23…M28` | **`add499b93`** (throwaway tree, shim) | same | ✅ **6/6 green-and-red-proven**, `TREE: clean`, exit 0 | ~196 s | 3.13.3 |
| 11 | `preflight_mutation_matrix_test.sh --legs M34,M35,M39,M40,M41` | **`add499b93`** (throwaway tree, shim) | same | ✅ **5/5 green-and-red-proven**, `TREE: clean`, exit 0 | 101 s | 3.13.3 |
| 12 | `stop_ownership_unit_test` | `5fce2dbdd` | `timeout 300 bash test/packs/stop_ownership_unit_test.sh` | ✅ **43/43** | 19 s | 3.13.3 |
| 13 | `watchdog_watcher_unit_test` | `5fce2dbdd` | `timeout 300 bash test/packs/watchdog_watcher_unit_test.sh` | ✅ **90/90** (fixture ports 19139/19140 freed) | 8 s | 3.13.3 |
| 14 | `test_launcher.sh` | `5fce2dbdd` | `timeout 300 env -u <19 vars> bash tests/test_launcher.sh` | ✅ gate-PASS **417P / 1F (quarantined) / 1S**, 33/33 sections | 93 s | 3.13.3 |

Legs #1/#2 census: `test_upgrade_tools.py` 182→185 · `test_upgrade_registration.py` 21 · `test_maintenancer_upgrade_gate_refuses.py` 2
→ 205 → **208** (Δ = exactly the 3 new resolution legs; **LOST ∅**; no renames, no deselections).

`TestArmPreflightStopScriptSibling` legs — all PASS at every tip:

| Leg | 5fce2dbdd | add499b93 |
|---|---|---|
| `test_refuses_missing_sibling_names_expected_path` | ✅ | ✅ (+ `"$(pwd)/scripts/stop-ensemble.sh" in out`, `"watchdog-watcher.sh" in out`) |
| `test_passes_present_executable_sibling_arms` | ✅ | ✅ |
| `test_refuses_unexecutable_sibling` | ✅ | ✅ |
| `test_refusal_resolves_relative_override_before_deciding` | ✅ | ✅ |
| `test_refusal_resolves_tilde_override_before_deciding` | ✅ | ✅ |
| `test_resolution_runtime_error_refuses_cleanly` | — | ✅ NEW |
| `test_resolution_oserror_refuses_cleanly` | — | ✅ NEW (kills the dropped-fallback mutant) |
| `test_resolution_failure_refuses_cleanly_restart_lane` | — | ✅ NEW |

**Coverage verdict at `add499b93`:** "fail-closed scripts-dir resolution" = COVERED at both seams
(restart `upgrade_tools.py:2906`, upgrade `:3609`, token `scripts-dir-unresolvable` registered in both
vocabularies `:2914` / `:3617`); "names both stop legs" = COVERED and **load-bearing** (the phrase
`$(pwd)/scripts/stop-ensemble.sh` is absent at `5fce2dbdd` — `grep -cF` = 0 — so the assertion is RED at
base). **Boundary:** the refusal text is pinned by **2 substring assertions (partial body pin, not
body-equality)**; the commit's comment block and runbook prose are **uncovered** by any executed leg
(doc-only surface).

## 4. Red classification — **zero branch-caused reds**

| Red | Where | Class | Evidence |
|---|---|---|---|
| `E2-fix the unarmed fixture still resolves the watchdog machinery (sed strip failed)` | ceremony pack, both modes, every tip | **PRE-EXISTING** | **Base leg** at `6ca643ad7` reproduces it: identical 40-id set, identical failing id, identical mechanism line `sed: 1: … RE error: braces not balanced`; ok∪fail = 40 = 39+1 (no prefix dropped). `git diff 6ca643ad7..5fce2dbdd -- tests/packs/preflight_ceremony_readiness_test.sh` = **EMPTY**; the pack's own predicates re-run against base and tip blobs are identical (`floor_const=2147c029…`, C4/C6/C7 pass, C5 guard-before-burn preserved on both). The historical **40/0 was recorded on a Linux runner** — the macOS baseline is 39/1. |
| `8b-c2 slow command: rc 124, SIGKILLed within deadline` (`expected rc=124 elapsed=1`, `actual rc=124 elapsed=2`) | launcher pack | **QUARANTINED / pre-existing** | `QUARANTINE.md` row (2026-11-10, pre-existing at base `818411107`); mechanism = `elapsed=$((SECONDS - T0))` is whole-seconds-since-shell-start → a ~1.02 s watchdog kill straddling a second boundary reads `elapsed=2` in ~7 % of trials. Branch reachability **nil** (pack subject `launcher.sh` / `scripts/upgrade/lib.sh` untouched). Per ensure.md quarantine-awareness it does not fail the gate. |

**Branch-caused reds: 0.** Merge gate satisfied.

## 5. Live confirm (read-only — `curl` / `ls` / `stat` only; `stop-ensemble.sh` never executed)

- `GET http://localhost:9797/livez` → **HTTP 200** `{"status":"alive","version":"0.29.16"}`
- `GET http://localhost:9797/readyz` → **HTTP 200** `{"status":"ready"}` — components `database/queue_freshness/services/checkpoint_saver` all `true`, `reasons: []`, `draining: false`
- `/Users/nguyenminhkha/agents-ensemble/scripts/stop-ensemble.sh` → present, mode **`-rwxr-xr-x`** (executable bit SET), 43468 bytes, mtime 2026-11-11 04:50:49 — metadata only

No writes, no execution, no daemon/promote/upgrade/journal/pending-op operation, no process kill; ports
8088 and 9797 untouched.

## 6. Findings

- **F-1 — PR shape was understated.** The brief described PR1 as "pure-addition + reworded refusal message". It is not: `5fce2dbdd` functionally changes `daemon/tools/upgrade_tools.py::_resolve_scripts_dir()` — an `ENSEMBLE_UPGRADE_SCRIPTS_DIR` override is now `expanduser().resolve()`d to absolute **before** the gate decides (base appended the raw `Path(override)`). `add499b93` then **drops** that raw-override fallback entirely (tip docstring: "There is deliberately NO raw-override fallback"). The 5 new legs are the direct cover for that behaviour.
- **F-2 — there is no old refusal message at base.** The token `preflight-stop-script-missing` does not exist at `6ca643ad7` (base→HEAD is an ADD, not a reword). The body was reworded only between `097ab1416` → `5fce2dbdd` (OLD "must exist AND be executable" / "would abort mid-promote" → NEW "must exist AND carry the exec bit" / "conservative hardening" / "`bash stop-ensemble.sh`" / "lib.sh itself only tests -f" / "a MISSING sibling aborts mid-promote"). **No committed pack or test pins the old wording** — the committed sibling-absent test pins only the token, the exact sibling path, the substring `scripts/stop-ensemble.sh` and nonce-intact. The leader's concern ("if a pack pins the old message, that red is branch-caused") therefore does not materialise.
- **F-3 — coverage gap, now closed.** The committed gate checks only **one of two** stop-script resolution legs: `lib.sh stop_via_stop_script` resolves the sibling one level up (`lib.sh:4849`, via `BASH_SOURCE[1]`) and falls back to `$(pwd)/scripts/stop-ensemble.sh` (`lib.sh:4852`); `make install` ships only `watchdog-watcher.sh`, so the fallback leg can also be missing (the v0.29.16 rc=127 halt class). `add499b93` names both legs in the refusal and pins them.
- **F-4 — gate-integrity incident (process, not product).** The commissioned "frozen" worktree was a live dev surface: uncommitted WIP appeared in the three files under test mid-run, was `git add`-ed, then committed as `add499b93`. Legs whose validity depends on the tree state were re-run in detached evidence worktrees; legs whose subject bytes were provably stable (stop-ownership, watchdog, launcher) stand at `5fce2dbdd` with hash/timestamp evidence. **Recommend: gate legs run in a detached worktree at the pinned commit, and pin the subject by sha256 rather than by porcelain.**
- **F-5 — pack defect (pre-existing, not PR1): `tests/packs/preflight_ceremony_readiness_test.sh:328` uses `sed -i -E` — BSD sed consumes `-E` as the in-place backup suffix** (stray `t.txt-E` artifact), the script is then parsed as BRE and dies (`RE error: braces not balanced`). Structurally RED on macOS with or without a plant. Fix: `sed -i.bak -E '…'; rm -f f.bak` (or a Python/`perl` edit). **Out of PR1 scope** (PR1 touches no `tests/packs/`).
- **F-6 — latent FALSE PASS in the same pack.** `E2`'s discriminator (`grep -qi 'watchdog' && rc=78`) is satisfied by the **honored** branch as well (`promote.sh` prints "watchdog" in both branches), so with the fixture never actually unarmed E2 reports ok while proving nothing. `E2b` already uses the correct discriminator (`grep -q 'watchdog token honored' && ! grep -q 'NOT armed'`). Tighten E2 to match. Also out of PR1 scope.
- **F-7 — resource hygiene.** The launcher pack leaves 122 `/tmp/launcher-*` dirs and **289 orphaned `sleep` processes** (known class `awk-01-watchdog-process-leak`, `ulimit -u` 2666) — fork exhaustion is a self-DoS risk for repeat runs. Left in place (not killed).

## 7. Row text for the PACKS.md single writer (not applied by the tester — read-only gate)

```
| `preflight_ceremony_readiness` | `timeout 300 bash tests/packs/preflight_ceremony_readiness_test.sh [--expect-implemented]` | ceremony interlock; I16d–I16g stamp gates | 2026-11-11 @ `5fce2dbdd` + `add499b93` | ⚠️ **39P/1F** BOTH modes — macOS BSD-`sed` PACK defect at `tests/packs/preflight_ceremony_readiness_test.sh:328` (`sed -i -E` → `-E` eaten as backup suffix → BRE parse error); PRE-EXISTING at base `6ca643ad7` (base leg: identical 40-id set, identical `E2-fix` id). The historical 40/0 was a **Linux** runner fact — macOS baseline is 39/1. Shimmed diagnostic (`-i`→`-i ''` PATH shim, env-only) = 40/0 both modes. Also: E2 is a latent FALSE PASS (honored branch also matches `grep -qi 'watchdog'`) |
| `preflight_mutation_matrix` (subset) | `timeout 300 bash tests/packs/preflight_mutation_matrix_test.sh --legs <M23-M28\|M34,M35,M39,M40,M41>` + BSD-sed PATH shim | plant→RED→GREEN for the 11 legs that plant into `daemon/tools/upgrade_tools.py` | 2026-11-11 @ `5fce2dbdd` + `add499b93` | ✅ **11/11 green-and-red-proven** at both tips (A: 6/6 294 s @5fce2dbdd, 196 s @add499b93; B: 5/5 262 s / 101 s), 0 plant survivals, `TREE: clean` both. GREEN half is only reachable with the `-i -E` shim (see the ceremony row). No anchor staleness across the +103-line tip churn; no dead pins |
| `upgrade_tool_interlock_unit_test` | `timeout 300 bash test/packs/upgrade_tool_interlock_unit_test.sh` | P2.2 tools: system_upgrade surface + journal/lock/nonce interlocks; arm-preflight tokens | 2026-11-11 @ `add499b93` | ✅ **302P/1S** clean-tree run, 37 s. Baseline note: the same commit's **dirty-tree** run also reads 302P/1S because the dirty tree already carried `add499b93`'s tests — the clean 5fce2dbdd baseline is 299P/1S |
| `stop_ownership_unit_test` | `timeout 300 bash test/packs/stop_ownership_unit_test.sh` | stop-ensemble.sh ownership scoping | 2026-11-11 @ `5fce2dbdd` | ✅ 43/43 in 19 s |
| `watchdog_watcher_unit_test` | `timeout 300 bash test/packs/watchdog_watcher_unit_test.sh` | watchdog-watcher observation-only latch | 2026-11-11 @ `5fce2dbdd` | ✅ 90/90 in 8 s (fixture ports 19139/19140 freed) |
| `test_launcher` | `timeout 300 env -u <19 vars> bash tests/test_launcher.sh` | 8x sweep pins + chain/halt gates | 2026-11-11 @ `5fce2dbdd` | ✅ 417P/**1F (quarantined `8b-c2`)**/1S, 33/33 sections, 93 s — branch reachability nil; leaves 122 /tmp dirs + 289 orphan sleeps (`awk-01-watchdog-process-leak`) |
```

## 8. Open questions for the leader

1. **Re-gate after the next commit?** The branch moved twice during the gate; the delivered verdict covers `add499b93`. If a further commit lands, the PR-surface suites (python lane + interlock) must be re-run — the ceremony/matrix legs are slow (up to 294 s) and near the 300 s cap.
2. **Pack-hardening lane:** F-5 (`sed -i -E` portability) and F-6 (E2 false pass) are pack-only fixes — own lane, no product change, macOS baseline re-anchor 40/0 → 39/1.
3. **Matrix cap:** the mutation pack's own internal budget is 400 s per invocation while the house cap is 300 s — subset A ran at 294 s (98 % of cap). Splitting the 11 legs into ≤5-leg subsets (or a dedicated cap) keeps the matrix inside the discipline.

## 9. Artifacts (retained in /tmp)

`/tmp/pr1-frozen-01` (evidence tree @5fce2dbdd) · `/tmp/pr1-sedshim-1309536699/sed` (BSD-sed shim) ·
`/tmp/pr1-muta2-1309536699.tsv`, `/tmp/pr1-mutb2-b.tsv` (matrix digests @5fce2dbdd) ·
`/tmp/pr1-tip-muta-ta-132140.tsv`, `/tmp/pr1-tip-mutb-tb1.tsv` (matrix digests @add499b93) ·
`/tmp/pr1-tip-ceremony-t0617a-leg{A,B}{,-shim}.log` · `/tmp/pr1-tip-py-w01-run.log` ·
`/tmp/pr1-base-ceremony-out/` (base-leg evidence).

## 10. Overall status

- Python lane @ `add499b93`: ✅ PASS 208/208 (8/8 arm-preflight legs)
- Interlock pack @ `add499b93`: ✅ PASS 302P/1S
- Ceremony pack @ `add499b93`: ⚠️ 39P/1F — **pre-existing, base-leg-proven** (40/0 under the env-only shim)
- Mutation matrix (11 legs) @ both tips: ✅ 11/11 green-and-red-proven, 0 plant survivals
- Adjacent suites @ `5fce2dbdd`: ✅ stop 43/43 · watchdog 90/90 · launcher 417P/1 quarantined
- Live confirm: ✅ livez/readyz 200 (v0.29.16 ready) · `stop-ensemble.sh` present +x (never executed)
- **Branch-caused reds: 0**
- **VERDICT: SHIP for `add499b93`.**
