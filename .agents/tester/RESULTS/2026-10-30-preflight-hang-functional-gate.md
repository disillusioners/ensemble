# FUNCTIONAL GATE — promote-preflight-hang (board wi-fc1786a2)

**Lane:** `fix/promote-preflight-hang` @ HEAD `0b6a2123669b0c3597dbb59646e181c36bfa9f96` (worktree `/home/nea/ensemble-src-wt-preflight-hang`), base `b1183a637`.
**Gate:** independent, execution-based (dev assertions never trusted); 12 worker instances, all gates sandbox-fenced; lane HEAD pinned throughout (no worker commits; porcelain foreign-delta attributed below).
**Date:** 2026-10-30. **Verifier:** tester (functional-gate commission).

## VERDICT: **PASS-WITH-NOTES** — ceremony-ready CONDITIONAL on one REQUIRED pre-stage fold (G3, 1-line test-side, patch below)

| Gate | Verdict | One-line basis |
|---|---|---|
| G1 symptom RED→GREEN (closure) | **PASS** | base k=1.6865e-8 s/B² (R²=0.99988, 6 bounded legs) ⇒ extrapolated 58.4 min @ 455,783 B; HEAD reads full fixture 337/361/330 ms (<5 s SC1, ~15× margin); e2e sandbox preflight journal stage 0.52 s → clean `not-staged` terminal; parity 9/9 incl. duplicate-key BASE-bug preserved |
| G2 pack suite (12 shell packs + ceremony ×2 modes + daemon pytest) | **PASS-WITH-DISCLOSURE** | every pack completing under the 5-min cap: **0 failed**; count-growth table below; 2 modes of cap-incompatible-by-construction measurement subjects (real-soak, shasum `--full`) accepted with bounded/corroborating evidence |
| G3 scoped regression base↔HEAD | **FAIL at HEAD → remediation VERIFIED** | exactly **1** net-new deterministic red = stale 1-line test stub vs lane-added contract widening (`manager.py:5579` `unit_properties`); 3×-isolated both directions; fix verified in disposable tree (mechanism proven, not masking); **REQUIRED pre-stage fold** |
| G4 watchdog | **PASS** | firing: exactly 1 halt row, kind `deadline-breach-journal-read`, rc=78, lock released; non-firing: 0 halt rows healthy+natural-refusal legs; W3 systemctl-probe timeout ⇒ WARN+continue rc=0≠78; W1 adopt-refusal classes exactly 1 row / 0 rows; cgroup-TERM invariant pack + independent pre-kill-verified group kill |
| G5 log survival | **PASS** | parent SIGKILL + group-wide kill + stdout-closed + broken-pipe: narrative survives fully (mid-phase markers intact, no torn writes); retention = 20 non-fenced bound + A2 four-name family fenced (observed 25=20+5) |
| G6 arm-time guard (a–e) | **PASS** | stale dir ⇒ REFUSE + nonce NOT consumed (ordering proven); fresh ⇒ arm completes; non-git ⇒ WARN-only + sha256 row (both sub-shapes); git-shaped + rc=128 probe ⇒ REFUSE (W2 fail-closed); release-time floor gate refuses unset/non-40-hex/non-resolving/non-hex + lib.sh-stamp placeholder — all rc=78, zero residue |
| G7 mutation spot-execution | **PASS** | 12 legs (M6/M41/M42/M43 mandatory + 7 span + M15×2): all RED→GREEN reproduced, planted digests byte-exact vs R3 TSV; M15 reproduced-green 2/2 with pid-collision mechanism analysis |
| G8 size-guard matrix | **PASS** | six paths individually: promote rc=78 REFUSE (size+threshold+remedy token, designed halt row); status/rollback/restart/adopt-unit/stop-ensemble all WARN + 120 s bound + continue rc≠78 with full terminal proofs |
| G9 fence audit | **PASS** | matrix harness sources COMMON fence + PF_PACK_NAME (M9 fix confirmed); 6 packs sampled; safe-copy trip rc=2 BEFORE any plant/leg; no-touch proof (porcelain + live state.json sha before==after); negative control green |

## G1 — evidence detail

- Fixture regenerated (`tests/helpers/journal_fixture_incident.py`): sha256 `06ebaac3eb9b881b9ff550c6adca591941158eaf87e5d6bc5a90ec98a2398c6c`, **455,783 B / 2,949 entries** — exact.
- BASE bounded sampling (user CPU, `timeout -k 10 210` caps, full fixture NEVER run legacy): 26,520 B → 13.19/12.53 s; 52,884 B → 48.27/47.19 s; 105,581 B → 188.34/187.25 s. LSQ-through-origin **k = 1.6865e-8 s/B², R² = 0.999880**; doubling ratio 3.90–3.97×≈4×². **EXTRAPOLATED [never run] T_user(455,783 B) = 3,503 s ≈ 58.4 min** — consistent with dev band (k≈1.3–1.6e-8, 56.9 min) and the killed-at-37m43s incident executor.
- Symptom shape at base: 69,941 B slice @ 60 s budget ⇒ **rc=124, 60.00 s wall (57.26 s user — CPU-bound spin), stdout = 0 bytes**.
- HEAD full-fixture ×3 (lib.sh-routed): **337 / 361 / 330 ms**, rc=0, stdout sha == fixture sha (byte-identical), 2,949 entries parsed.
- E2E sandbox: `promote.sh sandbox --version v0.27.99-never-staged` ⇒ **rc=78 in 531 ms**; journal stage fast; terminal `not-staged` refusal appended to the SCRATCH journal only (2,949→2,950 entries).
- Parity 9/9 on 45,133 B slice: verdict rc + stdout bytes + stderr class identical; duplicate-key BASE-bug carried verbatim (`{"x":10,"b":2,"x":99,"b":2,"x":20}`); torn-journal parity (rc=1, malformed, 0 stdout both).

## G2 — pack suite (all @ HEAD 0b6a21236, per-pack `timeout 300`, lane porcelain clean per worker)

| Pack | Gate result | Dev anchor | Δ |
|---|---|---|---|
| preflight_journal_parity | **60/0** | 60/0 | = |
| preflight_journal_scale | **12/0** | 12/0 | = |
| preflight_repro | **7/0** | 7/0 | = |
| preflight_census | **10/0** | 10/0 | = |
| preflight_anchor_index | **42/0** | 42/0 | = |
| preflight_stage_lock | **13/0** | 13/0 | = |
| preflight_size_guard | **31/0** | 24/0 | +7 (growth; six-path matrix independently verified in G8) |
| preflight_logging | **33/0** | 30/0 | +3 (double-confirmed by G5's independent run) |
| preflight_watchdog | **68/0** | 38/0 | +30 (double-confirmed by G4; sections D10b/D12/D13/D14 added by fix-cycle commits `536522196`+`2fb53ce9e`) |
| preflight_ceremony_readiness | **40/0 both modes**, deferrals 0 | 27/0 | +13 (I16d–I16g stamp-gate assertions) |
| preflight_shasum_batch_parity | **7/0 default** | 4/0 | +3 (`--full` = measurement mode, see disclosure) |
| preflight_real_soak | bounded 20 s leg green (responder real HTTP :8466) | 5/0 @330 s | cap-incompatible by construction, see disclosure |
| daemon-side pytest corpus (9 files, superset) | **469 passed / 0 failed / 1 skipped** (41 s) | 236 (3-file composition) | superset all-green; growth same class |

**Count-growth adjudication:** all Δ>0 rows are post-anchor pack growth from the lane's own fix-cycle commits (growth quadruple-corroborated: size-guard, logging, watchdog, ceremony, pytest). Rule applied: count ≥ anchor with 0 failed = PASS; growth disclosed, never penalized. All anchors predate HEAD.

**Cap-incompatible-by-construction disclosures (2):**
1. `preflight_real_soak`: conservative mode = literal `gate_soak 300`, **no knob** (adaptive vars ignored under conservative=1). Full pack wall 330 s > 300 s cap — the test subject IS the 300 s conservative soak. Evidence accepted: bounded 20 s leg (soak-independent assertion green), G4 non-firing drills + watchdog pack healthy runs (machinery: 0 halt rows, lock discipline), dev full-run record (FINAL-VERIFY logs). **Routed follow-up (optional, lane owner):** ~3-line pack-level `PREFLIGHT_REAL_SOAK_S` knob (default 300, pack-only, no shipped-code change) so future gates can run conservative machinery under the cap. The 5-min cap was never relaxed.
2. `preflight_shasum_batch_parity --full`: measures the SLOW BASE fork-per-file walk (dev record: plugins BASE leg alone 303,774 ms). Killed at 300 s mid-§5 after agents (338 files) and frontend (567 files) legs both `parity=OK`. Default mode = the gate class (green 7/0); `--full` registered measurement-only/out-of-cap.

## G3 — the single net-new red + VERIFIED remediation (REQUIRED pre-stage fold)

- Corpus: 21 pytest files (scoped to the 9 shipped files' surfaces), 8 chunks both legs, identical commands. BASE 573P/2F/1S/4desel · HEAD 608P/**3F**/1S/4desel. FAILED-diff = exactly 1: `tests/unit/tools/test_agent_restart.py::TestT8Extras::test_restart_kind_argv_lock_adoption_and_unit_prefix`. Digests: BASE `b5bd39532226ad1c…a8bd` · HEAD `e7dda500fea51740…2ae6` (full lists `/tmp/pfh-g3-scratch/FAILED-{BASE,HEAD}.txt`).
- 3× isolation: BASE 3/3 PASS, HEAD 3/3 FAIL ⇒ deterministic. Test file byte-identical base↔HEAD.
- Mechanism: lane-added `daemon/manager.py:5579` threads `unit_properties=_promote_unit_props` into `spawn_executor`; the test's local `_rec` stub lacks the kwarg ⇒ TypeError ⇒ drain fails ⇒ pending_op stranded (boot-sweep fallback) ⇒ `assert False is True`. Stale-stub-on-contract-widening (the v0.27.11 class).
- **Remediation VERIFIED in disposable tree (no commit; lane HEAD pinned):**
  ```diff
  @@ -701,7 +701,7 @@ class TestT8Extras:
           def _rec(argv, install_dir, extra_env=None, *, run_id=None,
  -                 unit_prefix="ensemble-upgrade-"):
  +                 unit_prefix="ensemble-upgrade-", unit_properties=None):
  ```
  C5 chunk 32/32 (nothing else moved); fixed node 3/3 PASS isolated; mechanism proof: `drain failed` WARNING absent, drain completes as genuine **ownership transfer** (`owner_pid tool-arm→executor`, `trigger=post-turn-callback`, spawn payload carries `unit_prefix='ensemble-restart-'`, `ENSEMBLE_RESTART_LIVE` minted only for provenance_ok=True); production diff = 0 (1 test file, 1 line).
- **REQUIRED ACTION (pre-stage, lane owner/giter):** fold this 1-line commit before staging — else whole-tree/pg gates redden downstream (v0.27.11 "still-to-fold" lesson). Patch is exact and verified; no production change.
- Pre-existing reds (identical both legs, digest-pinned): `test_api_py_wraps_pass_in_try_except`; `test_no_new_http_endpoint_in_api_router`.

## G4/G5/G6/G8 — drill evidence (condensed; full verbatim in worker reports)

- **G4:** firing leg: 1 halt row `deadline-breach-journal-read (budget=PREFLIGHT_STEP_BUDGET_S value=3 elapsed=3s phase=promote:preflight) daemon-state=live`, rc=78, lock absent post-run. Healthy leg rc=0, 0 halt rows. W3: wedged-systemctl shim (PATH+`SYSTEMCTL_BIN`) ⇒ 2 WARNs (`supervision probe 'is-active' exceeded SYSTEMCTL_PROBE_BUDGET_S=5s … continuing WITHOUT a classification (R1: no fail-fast on a recovery path)`), rc=0, 0 halt rows (recovery WARN is stderr-only BY DESIGN — a journal row would be a halt row). W1: journaling class exactly 1 halt row (`adopt: previous release v0.0.0-missing missing … halt-for-human, NO repoint, txn left in place`); warn-only class 0 rows. Cgroup-TERM: pack pins + independent setsid group kill (membership verified pre-kill) + banned PID-only counter-leg.
- **G5:** Drills A/B: narrative byte-stable after SIGKILL (parent) and group-KILL (full ordered markers `narrative begins → journal_read → step-m1 → step-m2`, last line complete, payload consistent). C: closed-fd ⇒ WARN-class stderr, run completes rc=0; broken-pipe ⇒ executor SIGPIPE rc=141, narrative complete (sink structurally immune — `_log/_warn/_logv` return before stdout when `ENSEMBLE_UPGRADE_LOG_FILE` set). D: 25 fillers + in-flight four-name family (aged 3 d, older than fillers): evicted exactly the 4 oldest, non-fenced kept = 20, **all four family names incl. `.payload.client.log` survived** + pointer-named survivor. Intent ruling: "retention ≤20 respects current-run files" = shipped five-fence semantics (bound non-fenced, fence the A2 family); hard total-≤20 would self-contradict.
- **G6:** (a) stale ⇒ `UPGRADE REFUSED — reason=stale-scripts-dir … HEAD cc8fdfc291 does NOT contain the floor commit fa8e663e758 as an ancestor … nonce NOT burned`; pending_actions record retained `consumed_at=None`, `nonce_consumed_events=0`, ordering proven. (b) fresh ⇒ guard passes, arm completes (`UPGRADE ARMED … target=1.2.3`; consumption contract = record retained + `consumed_at` stamped + `nonce_consumed` event). (c) non-git ⇒ `verdict=warn (non-git-no-stamp … lib.sh_sha256=62f5025a…)` arm proceeds (+ with-stamp sub-shape `verdict=arm`). (d) git-shaped + `git rev-parse` rc=128 ⇒ `git-probe-failed (git-shaped dir) … ancestry is UNVERIFIABLE` REFUSE, nonce intact — **W2 fail-closed confirmed**. (e) stage.sh floor gate: unset / 20-hex / non-ancestor-40-hex / non-hex / (bonus) valid floor + lib.sh placeholder stamp — all `REFUSING TO STAGE` rc=78, zero `releases/` residue. Real production surfaces throughout (factory arm path, real probes, real scripts copies); DB surface none (file-based journal).
- **G8:** promote ⇒ rc=78 `REFUSING (promote path; JOURNAL_SIZE_GUARD_BYTES=513) … Remedy: put python3 >= 3.7 on PATH …` + designed halt row; status ⇒ WARN ×1, full bounded read, exit 0; rollback ⇒ WARN ×14 through a COMPLETE scratch-internal rollback (atomic flip + re-gate; the demonstrated rc=78 variant is the re-gate halt, not a guard refusal); restart ⇒ WARN ×7 through lock-adoption→gates→`RESTART COMPLETE`; adopt-unit (DRY_RUN) + stop-ensemble ⇒ WARN suppressed by `pipeline_settled` quiet-success, settled parse of over-threshold journal succeeded, natural terminals. Promote-path journal growth = the two designed audit rows via the splice WRITE path (read path write-pure; pack G2 leg green).

## G7 — mutation sample

12 legs: M6 (red rc=124 via runner-internal `timeout -k 5 400`, RED wall 411 s), M41 (`SCENARIO I13b FAIL expected=refuse got=arm`), M42 (`FAIL D12 rc=78 want 0`), M43 (`D13a a txn-busy refusal row was appended`), M2, M13, M14, M29, M37, M44, M46, M15×2 — **all** planted-sha256 byte-exact vs `MUTATION-MATRIX-FULL-20261030-R3.tsv`, all RED→GREEN with pin evidence. File span: lib.sh, promote.sh, journal_read.py, upgrade_tools.py, upgrade_journal.py. **Outer-timeout exception (accepted, disclosed):** M6 outer=900 s — the leg's subject is the runner's internal 400 s bound firing; outer 300 would corrupt plant/restore mid-leg. **M15:** reproduced-green 2/2; mechanism = dead-owner predicate `! _pid_alive 999999` (lib.sh:2275) is host-level ⇒ "load-transient" plausible strictly via pid-collision (day-of high churn: five wedged instances), not pack timing. Dev record not contradicted; the reviewer's self-declared gap ("executed zero legs, M15 not independently reproduced") closed by this run.

## G9 — fence audit

Common fence `tests/helpers/preflight_pack_common.sh` (live-path/main-checkout/.git/HOME-derived refusals :57-69; scratch+FAKE_HOME first :75-98 incl. baked-in escape-guard self-test; per-variable scrub :80-87 with the `env -u POSTGRES_*` no-op idiom grep-asserted absent; cleanup scoped :102-107). Matrix harness `preflight_mutation_matrix_test.sh:51-54` sources common fence + `PF_PACK_NAME` (M9 fix; no local drift; PF_PY probe :135-145; `assert_plants_clean_or_die` :219 pre-leg). 6 packs sampled — all source the same helper. **Safe-copy trip:** runner + helpers copied to /tmp ⇒ `FATAL: no interpreter can import the daemon package` rc=2 BEFORE any plant/leg; predicate arms proven from the copy (live/main-checkout refused, lane worktree accepted); no-touch proof: porcelain + live `state.json` sha `8735ea8515360be25582a3dcd501c22401200a70cc1e2caed65e9efe11132223` before==after. Negative control: census 10/0, stage-lock 13/0 green from the worktree. Sanctioned exception: `PYV=/home/nea/ensemble-src/.venv/bin/python` read-only borrow in watchdog/logging packs (house worktree convention).

## Disclosures & fence statement

1. **Foreign concurrent-lane porcelain delta** (5 workers independently sighted, identical attribution): `.agents/shared/planning/promote-preflight-hang/review-code-council.md` grew +73 (14:23:55Z) then +85 (15:01:59Z) — the reviewer council's fix-cycle-2 delta for this same commission. Docs-only planning prose; HEAD never moved; every worker proved its own footprint. NOT included in the gate's docs commit (targeted adds only).
2. **Live contact: ZERO.** All work in mktemp scratch + FAKE_HOME + disposable /tmp worktrees; per-variable env scrub on every invocation (the `env -u POSTGRES_*` no-op idiom used nowhere); port 9797 untouched; no live daemon restarts; no promote/stage/rollback against live; no symlink flips; live PG untouched. The one live-`state.json` churn observation (G1) was read-only attributed to the live daemon's own `mcp_server_state` journaling (context7 quarantine cycling, post-v0.27.11 documented behavior).
3. **Filename corrections vs the commission brief:** `preflight_pack_common.sh`, `preflight_mutation_apply.py`, `preflight_mutation_matrix_test.sh`; `stop-ensemble.sh` at `scripts/` root.
4. **Worker-side near-misses (both caught before evidence, disclosed):** G6 + G3-mechanism scripts initially resolved `daemon` from the main checkout (script-mode `sys.path[0]`); fixed with explicit path pins + module-origin asserts; defective runs were read-only.
5. **G2 worker-grouping deviation:** packs dispatched in 4 same-lane groups (4 workers) rather than 1 worker/pack — individual dual-layer `timeout 300` per pack preserved, per-pack verbatim counts recorded; grouping disclosed here.
6. **Real-soak knob + shasum `--full`** handled per cap-discipline (above); 5-min cap never relaxed anywhere in this gate.

## Required / routed actions

- **REQUIRED (pre-stage):** fold the G3 1-line test fix — `tests/unit/tools/test_agent_restart.py`, the `_rec` stub signature at ~line 701-703 (patch verbatim in the G3 section above); verified closing, production untouched.
- Routed (optional, lane owner): `PREFLIGHT_REAL_SOAK_S` pack knob (~3 lines) for future cap-compliant conservative-soak gates.
- Standing lane obligation re-affirmed (change-surface closeout): release bump MUST set `PIPELINE_SCRIPTS_FLOOR_COMMIT` (upgrade_tools.py) AND `PIPELINE_SCRIPTS_REV` (lib.sh) to the merge commit; stage.sh refuses while unset (verified live in G6-e).

## Worker instances

G1 `41f711a7` · G2a `309b280d` · G2b `b94ba747` · G2c `700171ae` · G2d `ffdf9848` · G3 `75f08746` (+follow-up) · G4 `cfd376df` · G5 `7037827b` · G6 `6ad0ead5` · G7 `57dac733` · G8 `7ebc59de` · G9 `7831cda3`. Scratch evidence: per-worker /tmp paths in their reports (session-scoped; lane repo carries this RESULTS file + PACKS.md rows only).
