# Final gate — `feature/promote-full-auto` @ `e70a23b4f` (micro-verify; gate body at `fe362aedd`)

**Verdict: SHIP at `e70a23b4f617c8d86c411b86410db9069591a80c` — the round-3 follow-up (H4) is CLOSED, landed in `e70a23b4f`.**

> **Micro-round (appended 2026-11-11, tester byte+behavior verification).** One commit past `fe362aedd`: `e70a23b4f` "fix(test-hygiene): H4's operative line — unset PORT lands (the earlier commit was comment-only, ledger corrected)". Scope `git diff --stat fe362aedd..e70a23b4f` = **exactly 2 files, +4/−1**: `scripts/run_tests_scrubbed.sh` (+2: `unset PORT` at :62 inside the unset list, `PORT)` at :89 in the leak-gate case-list) and `.agents/tester/PACKS.md` (+3/−1: a dated CORRECTION note appended to the tester-triage block — *"the claim above that H4 had landed in `2ab671b6a` was WRONG … The ledger records the error rather than laundering it"* — plus a parenthetical pointing the launcher caveat row at the micro-commit; the original claim text and history are preserved, nothing rewritten). No product/family file touched.
>
> **Behavioral proof (independent, detached tree @ e70a23b4f, env-scrubbed):** LEG A — ambient `PORT=9999` (riding on top of an already-set `PORT=9797` in the runner's parent shell, so the scrub was proved against two ambient values at once): wrapper's own verify-gate PASSES (no `ENV-SCRUB FAILURE`), and a python child via the wrapper asserts `"PORT" not in os.environ` → **PASSED** (the wrapper is pytest-only — `exec .venv/bin/pytest "$@"` at :118 — so the literal shell-child form is inapplicable; the pre-authorized python-probe adaptation was used). LEG B — mutant deleting the `unset PORT` line (gate arm left intact): wrapper exits **rc=78** with `ENV-SCRUB FAILURE: leaked connection vars still set: PORT` → the detector is live and discriminating. Restore sha-verified (`cf5c26a9…` OK), porcelain 0.
>
> **Sanity at e70a23b4f:** stamp trio `087f782afbf1957dd0bf6acea5198314093d2a89` ×3 byte-equal (lib.sh :95 / upgrade_tools FLOOR :4145 / M22 literal :281), `commit`, **ancestor rc=0**, **14/14 coverage 0 MISSING** — and the 2-file diff proves no family file moved, so coverage is structurally unchanged. Ceremony pack one mode: **40P/0F rc=0** (11.8 s). `bash -n` the wrapper: **rc=0, no output**. Live: `/livez` v0.29.16 HTTP 200, `/readyz` ready 4/4 components true, `stop-ensemble.sh` `-rwxr-xr-x` 43 468 B (never executed).
>
> **§H4 below is retained as the historical finding text** (the gate that caught the missing line). Its status is now **CLOSED at `e70a23b4f`** — the follow-up asked for (the operative line + the leak-gate arm + the ledger correction) all landed, and the ledger records the error rather than laundering it.

**Verdict (gate body, retained as history): SHIP at `fe362aedd` — with one named follow-up (H4's operative line did not land; see §H4).**
Commissioned re-verify after two branch moves past the round-2 tip `ba452fafb`. Method identical to rounds 1–2: every leg in a **detached `git clone --shared` tree pinned by sha** (freeze proven per tree), `env -i`-scrubbed runner (`/tmp/pfa/run.sh`), Python 3.13.3 via `uv sync --frozen --python 3.13.3`, no PORT ever set by the harness, live dir read-only.

- **Tip:** `fe362aedda0518a9d3acc6c35f9cfd9dc18cd6ae` (branch HEAD, porcelain 0)
- **Base:** `edbf3c92f622b87f0c7758d10a724d00c68d8219` · 57 commits · 37 files
- **Prior tip (round 2, verdict SHIP):** `ba452fafb` — 11 commits in `ba452fafb..fe362aedd`
- **Stamp trio @ tip:** `087f782afbf1957dd0bf6acea5198314093d2a89` (lib.sh REV = upgrade_tools FLOOR = M22 plant literal; commit type `commit`; **ancestor of tip rc=0**; 14/14 executor-family coverage, 0 MISSING; re-pin + coupling refresh in ONE commit — `fe362aedd` itself, 3 files × 1 line)
- **Tree identity:** proven on a discriminating file, not `daemon/__init__.py` (identical across shas): `daemon/tools/upgrade_journal.py` `cab5e387…`/4745 L = `git show fe362aedd:` exactly, in all 4 trees
- **Concurrent lane disclosed:** a sibling gate (tidier round 3) ran `test_release_journal.sh` + mutation matrix on this host during the round (`/tmp/rj-r3b.log`, `/tmp/matrix-r3.log` — its own 374P/4F with the same quarantined 4). Mutation-matrix TSVs are host-global (`/tmp/preflight-mutation-matrix*.tsv`); my per-leg TSVs were copied out immediately after each invocation so rows are isolated. No freeze-integrity event: all legs ran in `/tmp` clones, never the shared worktree.

## Per-suite table

| # | suite | tip | legs | result | wall | interp |
|---|---|---|---|---|---|---|
| 1 | release_journal full | fe362aedd | 1 (tip) | **375P / 4F / 2S** rc=1 — the 4F = the quarantined set, byte-matching mechanisms | ~15 min | 3.13.3 |
| 2 | ceremony pack (both modes) | fe362aedd | 2 | **40P/0F rc=0** ×2, deferrals 0 both | 21 s | 3.13.3 |
| 3 | executor_family_payload | fe362aedd | 1 | **53P/0F** `RESULT: PASS` | 2 s | 3.13.3 |
| 4 | upgrade_tool_interlock | fe362aedd | 1 | **332P/1S** `RESULT: PASS` (skip = opt-in `ENSEMBLE_UPGRADE_E2E_SERVICE` unit smoke) | 14 s | 3.13.3 |
| 5 | mutation matrix M22 / M32 | fe362aedd | 2 | **both legs red(rc=1)→green(rc=0), restore ok, `TREE: clean`** — M22 needle `DEFERRED_RED C1`; M32 needle `DEFERRED_RED I11 the token is not refused` (tightened, discriminating) | 30 s | 3.13.3 |
| 6 | bounded_subprocess_waits | fe362aedd + edbf3c92f | 2 (A/B) | **36P/2F/2S ≡ BASE 36P/2F/2S** — comm EMPTY both directions (3-line and clean-2-line forms); S1 GREEN at tip via the H3-moved fixture | 3.0 s / 4.5 s | 3.13.3 |
| 7 | test_launcher (scrubbed, no PORT) | fe362aedd | 1 | **385P/36F/1S** rc=1 — pack byte-identical `ba452fafb..fe362aedd`; failing-set digest `7786a645…` = the round-2 TIP-no-PORT digest (36 ids, byte-identical) | ~21 s | 3.13.3 |
| 8 | Python lane (journal/tools/registration/supervision_python) | fe362aedd | 4+1 | **139P/1S · 193P · 21P · 97 pins (native)** — zero count deltas vs round-2 tip; supervision_python pytest rc=5/0-collected (the standing phantom-green trap, PASS via native runner only) | 20 s | 3.13.3 |
| — | stamp gate (floor_stamp) | fe362aedd | 1 | `FATAL F1` Darwin fence (pre-existing, unchanged bytes) — replaced by direct facts: stamp= floor = `087f782af…`, type commit, ancestor rc=0, 14/14 coverage | <1 s | 3.13.3 |

## Red classification (base-proven)

| red | count | class | proof |
|---|---|---|---|
| `8c` ×3 (adopt refuses / journal byte-identical / symlink unchanged) | 3 | **pre-existing, fixture-TZ** — quarantined | byte-matching mechanism at BOTH tips and at base (rounds 1–2): fixture stamps `last_heartbeat` via naive `strptime('…Z').timestamp()`; on this +07 host that is −25200 s exactly. This round's journal: hb `1794404267` for started_at `20:37:47Z` = the naive reading, delta 25200 s; `age 32s` in the reclaim detail while hb reads 7 h stale — the smoking gun. 4-cell TZ dissociation (round 2): only the epoch-stamping TZ moves the behaviour (3≡2, 4≡1) ⇒ **fixture-side**; product `_iso_to_epoch` (`date -ju`) is TZ-invariant. Fixture block byte-identical base↔tip; fix locus `tests/test_release_journal.sh:1267` (`.replace(tzinfo=utc)`). |
| `12c` (sweep halt event cites quarantine) | 1 | **pre-existing, expectation drift** — quarantined | same `sweep_deferred … DEFERRED (observation-not-qualifying)` at base and both tips; 12c block byte-identical base↔tip; classifier (`1153ede0d`) ancestor of base. Fixture seeds `history:[]` ⇒ `intent_flip=0` ⇒ non-qualifying by design; only the `QUARANTINED` needle of 4 sibling asserts discriminates (1-of-4 load-bearing). Re-baseline the needle or seed an `intent_flip` event. |
| launcher 36-red block (no PORT) | 36 | **pre-existing env dependency** — ledgered | round-2 4-leg A/B: digest `7786a645…` byte-identical TIP≡BASE without PORT; `comm` EMPTY both ways; the `unprobeable no-port` DEFER arm + `_js_sweep_probe_port` byte-identical base↔tip; emitter from `c8338b619` (ancestor of base). With `PORT=1`: 421P/0F both legs. This round: scrubbed tip = 385P/36F/1S, digest `7786a645…` MATCH. PACKS.md now carries the ambient-PORT caveat row (ledgered per my round-2 F2). |
| bounded S2 pair | 2 | **pre-existing, host-class** | identical ids, expected/actual, SKIP set at BASE and TIP (`unit path unavailable on this host — falling back to pid-scoped stop (degraded)`); comm EMPTY both directions. |
| 8b-c2 / 8b-r2 | 0F / 1S | quarantine holds / by-design | 8b-c2 silent-passed (0 log hits); known ~7–17% whole-second flake, documentary quarantine row (pack has no skip mechanism). |

**Net-new branch-caused reds: 0** (every suite, both rounds).

## Fix-claim verification (the round's purpose)

### H4 — `unset PORT` in `scripts/run_tests_scrubbed.sh`: **CLAIM FAILED BYTE-VERIFICATION at `fe362aedd`** 🟠 → **CLOSED at `e70a23b4f`** (line + leak-gate arm landed; behaviorally proven — see the micro-round block above)

- Commit `2ab671b6a` "fix(test-hygiene): H4 scrub bare PORT + H5 …" — its message says *"One-line class fix: unset PORT (+ a comment naming the mechanism)"*. The actual diff to the wrapper is **comment-only** (9 lines, file lines 53–61). **The executable `unset PORT` statement does not exist at `fe362aedd`**: the complete unset list remains POSTGRES_* (:25–31), PG* (:40–41), PLANE_* (:52). The leak-gate case-list has **no `PORT)` arm** either, so bare `PORT` would also pass the verify gate silently. `DAEMON_PORT` is likewise unset nowhere.
- Consequence: every pack routed through the canonical scrub wrapper still inherits ambient `PORT` (on this host `9797` = the live daemon). The residual exposure is **read-only** `GET /livez` contact via the in-shell launcher-sweep subshells (`test_release_journal.sh:1641`, `test_launcher.sh:513` → `launcher.sh:1440/1963/1968`) — pins can pass for the wrong reason. The **kill** side of this hazard IS closed by H5 (below). No regression vs base (the wrapper never unset PORT before) — but the claimed fix did not land, and **`PACKS.md` row `@@-4642` records it as landed** ("H4 unsets bare `PORT` in the scrub wrapper"), so the false claim propagated into the ledger.
- **Required follow-up (the one-line fix):** add `unset PORT` (with the already-present comment) AND a `PORT)` arm to the leak gate; correct the PACKS.md row. Fold before merge or as the immediate next commit.

### H5 — emulator bootout fixture-ownership gate + 12i cell: **HOLDS** ✅

- Gate landed (`tests/test_release_journal.sh:487–497`): `port_is_fixture_owned` (range `RJ_LAUNCHD_OWNED_PORT_MIN/MAX` = 18300..18599, exported :290–292) precedes the `holders=` lsof; out-of-range PORT ⇒ loud rc-1 refusal, never TERM. The ba452fafb shape (`holders="$(lsof -ti:"$PORT")"` unguarded) is gone.
- **12i cell ran and PASSED in-suite** (2 asserts: bootout REFUSES foreign PORT rc=1; foreign listener SURVIVES `kill -0`). Evidence: this round's release_journal is **375P** = prior 373 + exactly 2 (the cell's silent `_pass` sites; the foreign branch taken — OS-allocated port outside 18300..18599). The cell spawns a **real** listener (`http.server` bound to `:0`, OS-chosen port written to a file, live pid asserted) — not a fabricated port.
- **Discrimination proven dynamically** (worker `pfa2-launcher-h5mutant`, runtime-extraction driver — the emulator is `eval`'d from the tree's own suite heredoc, so the mutant edit IS what ran): pristine → bootout rc=1, foreign listener ALIVE, 2/2 pass (stable ×3, OS ports 63748/64014/64019, all out-of-range so the real `else` arm ran); gate block → `:` mutant → bootout rc=0, foreign listener DEAD (TERM'd — `80277 Terminated: 15`), 2/2 FAIL. **Both assertions flip; the cell is non-vacuous.** Restore sha-verified (`ce2217b4…` OK), tree porcelain 0. Independently corroborated in-suite by the +2P silent pass (§table row 1) and by the rj worker's own sandbox probe (foreign port 62383 → rc=1, survivor, refusal message on stderr).
- Residual (disclosed): two non-TERMing ambient `${PORT}` reads remain unguarded by design — `list` (read-only pid report, :445) and `bootstrap` (stub spawn on `$PORT`, :537–547). The kill path is fully gated.

### M8 — `_supervision_launchd_bootout` rc-aware + anchored: **HOLDS** ✅

Full rc trace of the new body (`lib.sh:5287–5328`): rc 0 → success; **rc 124 → failure, decided BEFORE any output match**; rc 3 → success ONLY on one of three anchored phrases (`Could not find specified service` / `No such process` / `service not found`); rc 3 non-matching, any other rc, empty label, missing binary → `return 1`. **No fail-open path exists** — the old shape (benign case-first, rc-agnostic, loose substrings `*"Could not find"*|*"not found"*` matching e.g. `"No such process … permission denied"`) is unreachable. Also carries the M4 empty-label guard (no bare `gui/<uid>` whole-domain bootout). Phrase provenance: real-binary observation recorded in `docs/deployment.md:383` (note: doc observed `Could not find service`; the anchor set is the three-phrase band — byte-level divergence disclosed, fail-closed direction).

### H1/H2 — ONE sibling probe + stale-leg sweep: **HOLDS** ✅

- `_upgrade_stop_script_sibling_usable`: **1 definition** (`lib.sh:4944`), **3 call sites** (promote.sh:183, rollback.sh:148, restart.sh:135), stage.sh none. The three inline `[ ! -f "$_…_stop_script" ]` copies at ba452fafb are gone; the runtime stop site (`lib.sh:4964`) is sibling-only (`$(pwd)/scripts` fallback DELETED).
- The 22-line sweep (`7b8643810`): both operator-facing refusal strings (promote + restart lanes, `upgrade_tools.py:3823–3839`) are sibling-only; the surviving `watchdog-watcher.sh` mention is a historical-note phrase, not a second leg.
- Blast-radius pin updated in-range: `tests/unit/tools/test_upgrade_tools.py:3092` (`TestArmPreflightStopScriptSibling`) now asserts `"sibling" in out and "stop-ensemble.sh" in out` with the "sibling-only world" rationale; `tests/test_executor_family_payload.sh` payload-root sibling target likewise.

### Amendment honesty (newly touched)

- **H3 bounded-waits fixture move (`c0b8a395c`): NOT weakened.** Old S1 entered via the deleted `$(pwd)/scripts` fallback; new S1 stages a real caller at `$FAKETREE/scripts/upgrade/caller.sh` so the sibling leg resolves to the same hang file — **same production path** (same `stop_via_stop_script` UNIT branch, same systemctl stub, same `STOP_SCRIPT_BUDGET_S=2`), same downstream asserts. Log evidence: the sibling-shaped resolution path + `WARN: stop_script fork-exec timed out (budget=2s)` + `s1-rc=0`. S1 GREEN at tip; the A/B red set is set-identical BASE≡TIP.
- **PACKS.md classified grep:** 4 fix-class row edits + 3 historical-accurate (bounded 57/0 rows kept + STALE/RE-MEASURED annotations) + 1 appended record block. **`.agents/tester/RESULTS/` untouched by the range** (diff-stat empty) — evidence history never rewritten. Correct treatment throughout.

## Live confirm (read-only)

`/livez` `{"status":"alive","version":"0.29.16"}` HTTP 200 · `/readyz` `status=ready`, 4/4 components true · `current → releases/v0.29.16` · `scripts/stop-ensemble.sh` present `-rwxr-xr-x` 43 468 B (**never executed**) · pid 48372 on 127.0.0.1:9797, uptime monotone across the probe window (no restart) · 42 GiB free.

## Carried findings (rounds 1–2, status at fe362aedd)

| id | finding | status |
|---|---|---|
| F1/F2 | launcher pack's undeclared ambient-PORT dependency; ledger greens recorded under incompatible envs | **LEDGERED** (PACKS.md `@@-4642` ambient-PORT caveat + scrubbed re-record 385P/36F BASE≡TIP); canonical closure = H4's unset (pending, §H4) |
| F3 | 4 registered suites unrunnable on Darwin (`upgrade_test_lib.sh` F1 fence stage-2/stage-3 mutually exclusive on `/private/tmp`) | **OPEN** — `upgrade_test_lib.sh` bytes unchanged in range; ledgered in the round-3 follow-ups block |
| F5 | `test_supervision_python.py` not pytest-collectable (rc=5, 0 nodes) — phantom-green trap | **OPEN** (reproduced this round: native 97 / pytest rc=5) |
| F6 | `comp3-owner-dead` bounded-re-check DEFER branch unpinned | **LEDGERED** (round-3 follow-ups block) |
| F7 | 12c is 1-of-4 load-bearing; 8c TZ fixture | **OPEN** — the two un-quarantine fixes are one-liners (`:1267` tzinfo; 12c needle) |
| F8 | stale twins header "19 cells" (code 24); QUARANTINE.md 8b-c2 cite `:1042` vs actual `:1172` | **OPEN** (cosmetic) |
| — | mutation-matrix TSV host-global (concurrent-lane interleave) | **observed again this round**; per-leg isolation by immediate copy; pack should take a run-scoped path |
| — | release-journal `launchctl` emulator kill path | **CLOSED by H5** (range gate + 12i) |

## Verdict

**SHIP at `e70a23b4f617c8d86c411b86410db9069591a80c`** — merge the branch tip into `latest`. (Gate body verdict at `fe362aedd` was SHIP-with-one-follow-up; the follow-up H4 is now landed and verified in `e70a23b4f`.)

Historical verdict statement as issued for `fe362aedd`:

- Zero branch-caused reds across all suites, both rounds (round 3: release_journal 375P/4F with the quarantined set byte-matching; bounded 36P/2F BASE≡TIP comm-EMPTY; launcher 385P/36F digest `7786a645…` MATCH; python lane zero deltas; ceremony 40/0 ×2; payload 53/0; interlock 332/1S; M22+M32 red/green-proven TREE-clean).
- All re-verified fix claims hold **except H4**, whose operative line is missing (comment-only diff). H4 is a **no-op vs base** (no regression, test-wrapper only, product untouched) and the lethal half of its hazard is closed by H5. It does not block merge — but it is a **required immediate follow-up**, and the PACKS.md row claiming it landed must be corrected: add `unset PORT` + the leak-gate `PORT)` arm (one line each), or fold before merge.
- Amendment honesty verified end-to-end this round (fixture moves strengthen or hold; ledger history preserved, never rewritten).

Evidence: `/tmp/pfa2/` (rj-tip.log, bounded-{base,tip}.{log,ids*}, launcher-tip.log + digest, matrix-{M22,M32}.tsv, A1/A2/B/C/D1/D2/E logs, h5 artifacts) and round-1/2 artifacts `/tmp/pfa/`. All legs in detached sha-pinned clones; main worktree and live dir untouched except this file.
