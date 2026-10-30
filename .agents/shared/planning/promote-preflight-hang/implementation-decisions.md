# Implementation decisions — promote-preflight-hang, wave 2 (RESUME-WITH-SALVAGE)

Executor: wave-2 coder (2nd replacement). Date: 2026-10-30. Base: `b1183a637` (worktree
`/home/nea/ensemble-src-wt-preflight-hang`, branch `fix/promote-preflight-hang`).

This file records (1) the salvage audit verdict per file for the terminated wave-1
executor's uncommitted work, and (2) every deviation from the plan text / binding
rulings, as `deviation → ruling → rationale`.

---

## 1. Salvage audit (first action of the wave — read everything, then adjudicated)

| File | Verdict | Notes |
|---|---|---|
| `scripts/upgrade/journal_read.py` (new, 381 L) | **KEEP** (verified, not modified) | Three distinct algorithms per D1-12 (string-aware `verify`, string-naive `sub`, string-naive `update` with the 2-CHARACTER escape skip ported as a 2-byte skip — behaviourally identical because skipped bytes can only be UTF-8 continuation bytes, never `"`/`\`); single `open()` (D1-16); command-substitution stdout contract (D1-10); rc 0/1/2/3/4; `_warn` prefix byte-compatible with lib.sh; Python 3.7+ stdlib-only (3.12.3 verified on this host). Parity proven 60/60 against the git-extracted BASE oracle. |
| `scripts/upgrade/lib.sh` (modified, +313/−21) | **KEEP + 2 FIXES** | Fast paths + verbatim `*_legacy` fallbacks + size guard + batched `_tree_manifest` + bounded `_stage_tree_walk`. FIX-1 (applied): `_json_sub`/`journal_update` probe-failure path now emits the T1.5 "fallback discipline" warn (the slow path is never silent — previously only `journal_read`'s fallback warned). FIX-2 (applied at the promote.sh caller): R1's halt row (below). Verified `journal_splice.py` byte-unchanged: sha256 `50c406a4769025661bb76c006869d274f39967edb0a182342380798d0a8eb84b` == `git show b1183a637:scripts/upgrade/journal_splice.py`. |
| `scripts/upgrade/promote.sh` (modified, +9) | **KEEP + FIX-2** | `JOURNAL_READ_STRICT=1` as a PLAIN (non-exported) shell variable immediately after the floor probe — exactly R2. FIX-2 (applied): the `J="$(journal_read)" ||` refusal branch now appends a best-effort `halt` row via `journal_history_append … ‖ true` before `exit 78` (R1's "exit 78 + halt row"). Verified: lands on a valid journal (`"event":"halt"`, 156→325 B), refuses-and-collapses on a torn journal (the `‖ true` keeps the exit-78 unconditional). |
| `scripts/upgrade/stage.sh` (modified, +11/−3) | **KEEP** | The three tree walks route through `_stage_tree_walk` (bounded + heartbeated + logged before/after, stderr-only markers so stdout stays pure manifest data). |
| `tests/helpers/journal_fixture_corpus.py` | **KEEP** | 22 cases + G1–G5 = 27; regenerated → byte-stable (all 27 sha256 identical pre/post regeneration). G5 fixture is `{"a":"x\<NUL>"}` (see deviation D-3). |
| `tests/helpers/journal_fixture_incident.py` | **KEEP** | Deterministic 455,783 B / 2,949 entries / 2,775 `mcp_server_state` (Bresenham-exact ratio); `--print-sha` reproduces the recorded `06ebaac3…c98a2398c6c`. |
| `tests/helpers/preflight_pack_common.sh` | **KEEP** | Fence (mktemp sandbox + FAKE_HOME + escape guard + per-variable POSTGRES_* scrub + self-test leg), git-object BASE oracle extraction, bounded runner, stderr-class normalisation. |
| `tests/packs/preflight_journal_parity_test.sh` | **KEEP** | 60/60 PASS: phases A–G (verdict+stdout parity incl. G5 both legs, sub parity, update parity, duplicate-key BASE-bug-preserving, adjacency, locale invariance C/C.UTF-8/en_US.UTF-8, single-read structural + atomic-replace race, placement invariants). |
| `tests/packs/preflight_census.txt` + `_test.sh` | **KEEP** | Derived-from-git census == committed (25 direct sites, total=25 asserted); HEAD-side walk containment (SC3); TWO-SIDED unreachability proof; structurally non-recursive mutation self-test. Includes the R6 rows (stop-ensemble.sh:224, adopt-unit.sh:201, stage.sh:618). |
| `tests/packs/preflight_anchor_index.txt` + `_test.sh` | **KEEP** | Already restructured to the explicit `--check-only` argv entrypoint (structurally non-recursive — the §D child contains no mutation section at all). 20/20 PASS incl. the mutation leg. Fork-bomb safety PROVEN this wave: external spawn-count watcher (0.3 s sampler, kill >5) observed max 3 concurrent processes, convergence in 5 samples (~1.5 s). |
| `tests/packs/preflight_journal_scale_test.sh` | **KEEP** | 12/12 PASS: SC1 = 149 ms on the 455,783 B fixture (lib.sh path), update 598 ms, sub 405 ms, scaling flat (~1.0–1.2×), G4 no-guard-on-O(N)-path leg. |
| `tests/packs/preflight_size_guard_test.sh` | **KEEP** | 24/24 PASS: G1/G2/G3 scaled boundary trio (513 B), threshold/threshold−1/+1, size-named refusal, remedy pointer, journal byte-unchanged at the lib-level refusal, Q3 warn+continue, the 120 s-class bound attempted-then-bounded (no fail-fast), the 3-cell matrix, threshold-mutation leg. |
| `tests/packs/preflight_shasum_batch_parity_test.sh` | **KEEP** | Default mode PASS (synthetic-tree byte-parity, agents/ parity, misalignment fallback, scaled timing). `--full` re-run this wave (background) for the budget numbers — see §3. |
| `tests/packs/preflight_repro_test.sh` | **KEEP** | 7/7 PASS: BASE rc=124 at the 60 s budget with zero stdout (the symptom), HEAD 84 ms whole-journal, stdout == processed fixture bytes. |
| `tests/packs/preflight_base_scale_measure.sh` | **REDO (rewritten this wave)** | The salvaged version ran the 455,783 B BASE leg **unbounded to completion** (~61 min predicted) — a direct violation of the wave-2 dispatch's BOUNDED protocol ("NEVER let a legacy-walk benchmark run unbounded"). Rewritten: 3 samples (56,640 / 111,072 / 221,184 B) each under `timeout -k 10 1500`, user-CPU via `/usr/bin/time -p` (ambient-load robust), k-fit (LSQ through origin), extrapolation to 455,783 B **LABELLED EXTRAPOLATED**, incremental timestamped log lines for background polling. |
| `tests/fixtures/journal/*` (27 fixtures + CORPUS.md + INCIDENT.sha256) | **KEEP** | Regenerated → byte-identical. |

## 2. Deviations (deviation → ruling → rationale)

| # | Deviation | Ruling that wins | Rationale |
|---|---|---|---|
| D-1 | **Per-script floor probes (T1.7's letter) NOT added to `status.sh` / `adopt-unit.sh` / `stage.sh`** — the probe + bounded fallback live INSIDE `lib.sh`'s `journal_read` / `_json_sub` / `journal_update`, so every one of the 25 census sites + 3 transitive callers is covered with ZERO call-site edits. | Dispatch: "route journal_read + journal_update + _json_sub walk across ALL census sites"; phase1 §3.2 "call sites that change behaviourally: none" | Centralising the probe in the three verbs is strictly stronger than per-script probes: it cannot drift when a new caller appears, it preserves the argv/stdout/rc contract of every call site (§3.2), and the fallback discipline (warn + bound) is uniform. The census pack pins the same-25-call-sites invariant. |
| D-2 | **R1's "halt row" is appended at the promote.sh CALLER site, not inside the lib.sh guard.** | R1 (binding) + census §4.2 row 25 ("status.sh … **never** a journal write") + the size-guard pack's G2 pin ("refusal left the journal byte-unchanged") | The read path stays write-pure. The caller (promote.sh) owns mutation decisions and holds the lock; the append is best-effort (`‖ true`) so a torn journal cannot block the exit-78 refusal. Verified both ways (valid journal → row lands; torn journal → refuse-and-collapse). |
| D-3 | **G5 fixture is `{"a":"x\<NUL>"}` (backslash present), pinned: BASE REFUSES, helper (processed-bytes) REFUSES, a RAW-byte scanner ACCEPTS.** The dispatch's R5 text renders the fixture without the visible backslash and says "BASE bash walk REFUSES, python/processed ACCEPTS". | VERDICT-003 item 5 (the approver's measured correction, cited in READ FIRST) + R5's own "BASE bash walk REFUSES" direction + "mutation-meaningful" | On the no-backslash fixture `{"a":"x<NUL>"}` BASE and the helper both ACCEPT (verdict-identical, mutation-dead) — VERDICT-003 proved this. The ONLY fixture in this family where BASE refuses is the backslash one (processed `{"a":"x\"}` = unterminated string ⇒ `in_str=1` at EOF). "python/processed ACCEPTS" is only achievable by a RAW-byte port — which is precisely the mutation leg the pack asserts (raw scanner ACCEPTS ⇒ the processed-domain pin is mutation-meaningful). The no-backslash NUL family remains covered by corpus case 20 (stdout-byte parity of the NUL-dropped echo). |
| D-4 | **D1-18's bounded-diagnostic fail-fast is NOT implemented**; the diagnostic class (status/adopt-unit) gets WARN + proceed under the same 120 s bound as recovery. | R1 (binding): "status/rollback/restart/adopt-unit/stop-ensemble ⇒ WARN + proceed under a 120s bounded wrapper; NO fail-fast on recovery paths" | R1 wins over the plan text (binding-rules preamble). The size-guard pack documents this supersession in its own header and pins the diagnostic cell as WARN+CONTINUE. |
| D-5 | **The 455,783 B BASE leg is NEVER run** (also not by the one-off measurement script). The BASE cost at the incident size is stated as a k-fit EXTRAPOLATION from bounded 55–222 KB samples. | Dispatch BOUNDED protocol: "run the legacy walk on a 111–222 KB sample with a hard `timeout` wrapper … extrapolate to 455,783 B, and LABEL it extrapolated; NEVER let a legacy-walk benchmark run unbounded" | Supersedes phase0 P0-D2's "exact wall already measured" premise (that evidence was LOST with the wave-1 /tmp sandbox). The symptom itself (non-completion at 60 s) is still directly demonstrated by `preflight_repro_test.sh` on the real fixture. |
| D-6 | **`_journal_read_fallback`'s non-strict over-threshold branch runs the legacy walk under `JOURNAL_LEGACY_READ_BUDGET_S` (120 s default) and treats a breach as rc 1** ("treating the journal as UNREADABLE"). | R1: "WARN + proceed under a 120s bounded wrapper" | R1 prescribes the bounded wrapper; a walk that cannot finish inside the bound is a failed read (loudly named in the warn), not a hang. Without the bound the fallback recreates the 45–61 min stall on the very hosts that lack python3 — the failure class this lane exists to kill. |
| D-7 | **`_stage_tree_walk` logs its before/after markers to STDERR** (not `_log` stdout). | — (engineering necessity, disclosed) | The walk is invoked as `X="$(_stage_tree_walk …)"`; a stdout marker corrupts the manifest data (byte-parity of `_tree_manifest` output is the pinned invariant). `_log … >&2` preserves observability. |
| D-8 | **`STAGE_TREE_BUDGET_S` comment numbers re-derived this wave** from the re-run `--full` measurement (the wave-1 numbers were cited from LOST evidence). | Dispatch: "batch the stage manifest checksum double-walk and derive the budget from measurement" | See §3 — measured numbers replace the salvaged comment's claims; the 60 s default is re-justified (20× the worst measured batched walk). |

## 3. Measurements (this wave, this host; ambient: ~2 cores of unrelated CPU spinners)

* BASE k·N² fit (user CPU, `/usr/bin/time -p`, hard `timeout -k 10 1500` wrapper):
  see `evidence/BASE-SCALE-20261030.md` for the recorded legs and the EXTRAPOLATED
  incident-size cost.
* Stage-tree batching (`preflight_shasum_batch_parity_test.sh --full`, backgrounded):
  byte-parity OK on agents (338) and frontend (567) and the synthetic awkward-name
  tree; plugins leg recorded in the evidence file; budget = 60 s re-justified.
* HEAD: `journal_read` 149 ms / `journal_update` 598 ms / `_json_sub` 405 ms on the
  455,783 B fixture (scale pack, lib.sh integration leg); helper-level 84 ms (repro pack).

## 4. Ruling-compliance cross-check

* **R1**: promote × over-threshold × legacy-class ⇒ lib-level rc 1 → promote.sh `_warn` + best-effort halt row + `exit 78` (size-guard pack: matrix promote cell). status/rollback/restart/adopt-unit/stop-ensemble ⇒ WARN + proceed under `JOURNAL_LEGACY_READ_BUDGET_S=120` (pack: Q3 + matrix cells; stop-ensemble/adopt-unit covered transitively via `pipeline_settled` → `journal_read`, D-1). No fail-fast (D-4).
* **R2**: `JOURNAL_READ_STRICT=1` appears exactly once (promote.sh:221, plain assignment, after the floor probe); never `export`ed (grep-proven), never persisted, never inherited by child scripts (anchor pack §C pins non-export).
* **R4**: change surface = new `scripts/upgrade/journal_read.py` only; `journal_splice.py` byte-unchanged (sha256-proven above; parity pack §G re-asserts at every run).
* **R5**: pinned in the VERDICT-003-measurable direction (D-3); mutation-meaningful (raw-scanner ACCEPT leg).
* **R6**: `scripts/stop-ensemble.sh:224` is in the anchor table (line-verified at the pinned rev) and in the census's transitive-caller section; the honest count stays **25 direct sites** (+3 transitive, documented).
* **R8**: retention (`retention_evict`, census row 15) routes through the migrated `journal_read` like every other site; the guard/bound applies per its caller's class (retention runs under the pipeline lock inside promote — strict context only via the promote parent).

---

## 5. Wave-2 phases 2–4 deviations (2026-10-30)

| # | Deviation | Ruling that wins | Rationale |
|---|---|---|---|
| D-9 | **`RuntimeMaxSec` at `SOAK_S=600` is 1620, not the plan's worked example "1800"** (AC17). | D2-9 FORMULA (fold B2): `max(1200, PROMOTE_TOTAL_DEADLINE_S + SOAK_S + 120)` — the formula is the single source of truth. | The plan's own arithmetic: 900 + 600 + 120 = 1620; "1800 at SOAK_S=600" is unreachable under the formula (1800 needs SOAK_S=780). The worked example has an arithmetic slip; the derivation governs. Pin: `preflight_watchdog_test.sh` D5. |
| D-10 | **`_run_bounded_pg` (W6/T2.24) NOT added.** | D2-14(i) predicate: process-group kill is required only for SIDE-EFFECTING wrapped steps. | Every phase-2 wrap site is read-only (journal reads, entry checks, staleness predicate, integrity walks, bounded systemctl probes). The two side-effecting `bash "$stop_script"` sites remain UNWRAPPED (must-not-wrap). Adding a variant with zero users is dead code; the 7-consumer census of the byte-unchanged `_run_bounded` is re-pinned by the anchor pack. |
| D-11 | **`_run_bounded_step` wraps FUNCTION calls in a subshell** — steps that set parent globals are excluded. | D2-14 wrapper contract + the supervision-globals constraint. | `supervision_preflight` SETS `SUPERVISION_*` globals consumed by the whole ceremony ⇒ it is NOT wrapped; its 4 unbounded systemctl calls are bounded individually via `_df_systemctl` (T2.7) inside `supervision_dualfight_check`, whose callers are statement-form by contract (the existing comment at the halt site). |
| D-12 | **`_enforce_deadline` gained an OPTIONAL third argument** (`budget=<name> value=<n>`) appended to the L1 breach detail. | W3: one row shape for both producers; AC1: value-in-force named in the trip row. | Two-argument callers (all pre-existing sites) emit BYTE-IDENTICAL rows; only the new preflight checkpoints pass the extra. Both producers carry `daemon-state=`. |
| D-13 | **SC4b required a real status.sh change**: the plain-mode (`non---verify`) exit was 0 even on an unreadable journal. | SC4b (dispatch, binding): `status.sh` exits 1. | `VERIFY_RC_NOTE` only affected the `--verify` branch; the final `exit 0` now honours the note in plain mode (warn already emitted; no journal mutation; never 78). |
| D-14 | **Live-soak refusal placed right after `lock_heartbeat`** (post-lock, pre-staged-check), not inside the journal-read section. | T2.17 + the fence: the row needs the lock held. | First placement (before `journal_init`) was AFTER the not-staged refusal, so a live run without a staged target never reached the check; after moving earlier, the policy refusal precedes staged-ness and the row lands (best-effort when the journal is absent on a virgin install — disclosed). |
| D-15 | **The per-run retention helper derives the in_flight run via `journal_read` + `_json_sub`/`_json_field`** (string form), never a `/dev/stdin` pipe. | Engineering correctness. | `_json_sub` takes a JSON STRING, not a file; the pipe form was wrong at first draft (caught by Pack E E10 before commit). |
| D-16 | **`_logv` also rides the sink** (timestamp + 4 KiB cap + newline sanitization). | D3-11 (fold A21) names the `_logv` call sites as the unbounded latent payloads. | Otherwise a multi-line readyz body would bypass both the cap and the one-line contract; legacy (env unset) stays byte-identical. |
| D-17 | **`_tree_manifest` in-op markers come from a chunked batched walk** (xargs -n 500) with a progress line to STDERR derived from the growing sums file. | D3-13/T3.14 (markers every 500 files; NO background writer). | Output remains the concatenation of chunk outputs in argv order ⇒ byte-parity re-pinned by the shasum pack; the marker goes to stderr so manifest DATA on stdout is untouched. |
| D-18 | **Anchor-index rows for the two wrapped `journal_read` sites carry a HEAD-side alias** (`head_alias_for` in the pack). | "moved != deleted" (the index's own header rule). | The base-rev literals stay line-exact at b1183a637; at HEAD the wrapped forms are asserted instead (plus a printed `MOVE` line). C2 adds the 21 phase-2/3 constructs + the trap-order line check. |
| D-19 | **`pack D`'s healthy leg uses a harness replicating the wrapped preflight steps** (real lib.sh functions against a real release fixture + real journal), not a full promote.sh ceremony. | SC5 says "healthy sandbox runs"; a full ceremony drags in stop-ensemble/launcher hand-back/daemon boot (out of a pack's fence). | The REAL 300 s conservative soak runs as its own pack (`preflight_real_soak_test.sh`) with REAL sleeps and REAL HTTP probes; the structural leg proves the soak is outside the armed window. |
| D-20 | **D4(b) benign-detach kill contract stays ROUTED — no code change** (leader RULING 3). Handoff recorded below. | RULING 3. | The routed commission MUST carry, verbatim: (1) a PRE-TXN stall signal (a "preflight started" marker published BEFORE `journal_open_txn`, since `_executor_alive_recent` keys on the txn heartbeat and is blind to a pre-txn stall); (2) a "stalled" predicate (no journal progress AND no heartbeat advance for N s — NEVER a bare timer; 660 s is a healthy-path number); (3) the writer-side journal-growth policy (89 % of the 455 KB journal is `mcp_server_state`; D1's size guard only converts the cliff into a loud refusal). Rejected alternatives: a blind timer kill (kills legitimate slow promotes); keeping the current no-op (the shell now fails loudly first — D2 — and the row is correlatable — D3). In-lane correlatability already shipped: the enriched `executor_still_running` row (narrative path + last phase marker + elapsed-since-last-journal-activity). **Board items to open: T4.5 (stall escalation) + T4.7 (three mandatory scope items).** |
| D-21 | **`_HALT_JOURNALED` reset lives in `_trap_install_signal_handlers`** (a re-install may journal for the new phase). | D2-4 (the guard prevents duplicate rows). | Reset-on-install keeps a legitimate halt after a phase re-install possible while still collapsing re-entrancy within one handler execution. |

## 6. Wave-2 phases 2–4 measurements (this host, ~2 ambient cores loaded)

* `RuntimeMaxSec` derivation: 1320 (defaults) / 1620 (SOAK 600) / 1920 (SOAK 900 clamp) / 1200 (floor) — python pin in Pack D.
* Pack D: 38/38 (watchdog). Pack E: 30/30 (logging). Pack F: 13/13 (stage lock).
* Real conservative soak: see `tests/packs/preflight_real_soak_test.sh` run log (300 s, real probes, 0 breaches / 0 kills).
* Dev-vs-debug note: an ad-hoc (NON-pack) debug invocation at 02:43Z caused ONE `refusal` (not-staged) row to be appended to the LIVE journal — see the lane report's fence-breach disclosure. The pack itself is fenced (FAKE_HOME ⇒ `resolve_env live` resolves into the scratch).

## 7. Wave-3 (phase 5) deviations — 2026-10-30

| # | Deviation | Ruling / driver that wins | Rationale |
|---|---|---|---|
| D-22 | **Pack I declares the daemon seam CONTRACT instead of guessing phase 6's implementation**: `tests/helpers/preflight_ceremony_probe.py` discovers the guard among an ordered candidate-name list (`scripts_dir_provenance_verdict`, …) with a documented signature `(scripts_dir, floor_commit=None, env=None) -> (verdict, detail)`, verdict ∈ {arm, warn, refuse}. | phase5 T5.16 + N-18 ("the pack's *content* depends on phase6's spec, not on phase6's implementation") + the pack-must-run-RED requirement in the wave-3 dispatch | The pack must exist and FAIL LOUDLY before P6 exists. It exits **3** (`PF_DEFERRED_RED`) with one named symptom per deferred leg; `--expect-implemented` (the final-gate mode) turns every deferral into a hard failure. When P6 lands it implements one of the documented names and the pack flips green with zero pack edits. |
| D-23 | **M1's pin is Pack B, not Pack A** (phase5 §2 lists M1 under Pack A). | The parity design itself: the BASE oracle IS the legacy walk, so "new == old" is invariant to the implementation swap — a Pack A RED for M1 would mean the *helper* diverged (M2–M4's job). | M1 executes as: probe disabled (the guarded scanner is the only path) + `JOURNAL_LEGACY_READ_BUDGET_S=5` ⇒ Pack B's lib-integration leg fails ("lib.sh integration failed"). The size-guard G4 leg is the same defect's second manifestation. |
| D-24 | **M6 executes a bounded proxy of the unbounded O(N) restore**: plant = `journal_read` calls `_journal_read_legacy` directly (guard + bound bypassed), pinned on Pack J's **G2** leg (every size-guard refusal disappears ⇒ an over-threshold journal is read by the scanner). | The wave-2 BOUNDED protocol + BASE-SCALE-20261030: the unbounded form on the 455,783 B fixture is the incident itself (≈3,415 s extrapolated) and cannot complete inside any CI budget. | The plan's stated secondary manifestation ("Pack B SC1 timing") is cited, not executed, for exactly that reason. |
| D-25 | **M3 executes the `in_str` term's ACCEPT direction (inversion), not the literal "drop the term"**: the plant flips `in_str == 0` → `== 1` (a healthy balanced journal now REFUSES). | VERDICT-003 item 5's lesson (a mutation-dead pin is worse than a disclosed one) + AC2's frozen corpus cardinality (27). | Measured first: with the term literally dropped, all 27 fixtures return verdicts IDENTICAL to BASE — the corpus's unterminated-string cases are also brace-unbalanced, so the reject direction of the term is NOT isolated by the corpus. A 28th fixture (`{"a":"x\}` — balanced braces, unterminated string) would make it observable; the canonical 22 + G1–G5 = 27 count is plan-frozen and gate-asserted, so the corpus gap is ROUTED as a follow-up, not silently widened. |
| D-26 | **M2/M3/M4/M5/M6/M29/M30/M31 expected-RED substrings are the packs' `pf_bad` texts** (e.g `03-empty-file`, `G2: rc=`, `recovery class: rc=`), not the `pf_ok` texts. | House honesty: the leg must prove the pin FAILS, and the failing line is the `pf_bad` line. | Two-space/`$var` interpolation differences between the ok and bad strings made the first M4 attempt a false negative. |
| D-27 | **M14's pin lives in Pack H, not Pack E** (phase5 §2 lists M14 under Pack E). | The pin's real subject: `spawn_executor`'s CHOICE of the payload file. | Pack E's E7 leg pins `build_service_argv(...)` as a PURE function with an explicit path argument — it is invariant to which path the caller passes. Pack H's `test_payload_append_targets_the_per_run_payload_file` drives the real spawn seam, so the mutation (passing the aggregate `log` instead of `payload_path`) reddens there. |
| D-28 | **M10 executes the breach handler's ORDER break, not the literal "delete the lock release"**; and **M11's exclusion-plant keys on `*preflight*`**, not `!= "preflight"`. | D2-5 ("ORDER IS LOAD-BEARING and pinned") + measured behaviour. | Measured: deleting `lock_release` from `_budget_breach` leaves Pack D GREEN — the EXIT trap releases the lock anyway (defense-in-depth, D2-4), so the deletion is unobservable at pack level. The observable defect is release-BEFORE-journal, which loses the row on a live-shaped path (the exact D2 leg). M11's first form was inert because `_PIPELINE_PHASE` is `promote:preflight` (set by `_trap_install_signal_handlers`), not `preflight`. |
| D-29 | **Three stale daemon-side tests were fixed (test-side, no production change):** `test_upgrade_tools.py`'s spawn stubs accept the new kw-only `unit_properties`; `test_upgrade_executor_systemd_service.py`'s `FakeProc`/`Recorder` write the stubbed stderr into the client FILE (the R7 seam) and the allowlist leg pins the sanctioned `ENSEMBLE_UPGRADE_LOG_FILE` value instead of merely exempting it. | Net-new-failure discipline: P2/P3 changed the seams; 13 tests were red at HEAD. | 322 passed / 0 failed after the fix (was 309/13). The stub upgrade is faithful (systemd-run writes to its stderr = the file we redirect), not a weakening. |
| D-30 | **Multi-leg mutation runs are batched (≤2 legs) and backgrounded, and the runner carries a LEAVE-NO-PLANT guard**: it refuses to start if any plant target is dirty vs HEAD, restores the in-flight backup on EXIT/INT/TERM, and re-checks every target at the end (`git diff --quiet`), reclaiming leftovers with `git checkout -- <file>` + a loud `PLANT-LEFTOVER` line. | Orchestrator directive (2026-10-30): a SIGKILL mid-leg must never leave a planted defect in the tree. | Also: never edit the runner script while a batch is in flight — the M9–M10 batch was corrupted mid-run by exactly that (bash re-reads the file lazily); M10/M11 were re-run clean afterwards. |

### Wave-3 measurement notes
* Pack H's live-soak refusal legs EXECUTE promote.sh (fenced: `HOME` + the live-lane
  install root are `tmp_path`-derived and asserted BEFORE the invocation; per-variable
  scrub; explicit port; `resolve_env live` probed first and asserted inside the sandbox).
  No live path is ever resolved (the wave-2 incident's exact class).
* M16 flaked once (GREEN leg rc=1 inside a batch, immediately after M15); isolated
  re-runs passed twice (batch re-run + standalone), consistent with an
  order/load-sensitive leg under the ambient 2-core spinners — disclosed, not hidden.

## 8. Wave-3 final verification (2026-10-30, HEAD d12f999b0)

* **Full pack sweep at HEAD** (`evidence/FINAL-VERIFY-20261030.log`): parity 60,
  scale 12, size-guard 24, census 10, anchor 42, shasum 4, watchdog 38, logging 30,
  stage-lock 13, repro 7, real-soak 5 (330 s, 0 halt rows, 0 breaches), Pack H
  pytest 31, Pack I 10 pass / 15 deferred (exit 3 — expected). Daemon-side
  pre-existing suite: 322 passed, 1 skipped, 0 failed (net-new = ∅ after D-29).
* **Full mutation matrix** (`evidence/MUTATION-MATRIX-20261030.tsv/.log`): 24/24
  executed legs ok (plant → named pin RED → restore → GREEN; every planted sha256
  recorded), 8 deferred rows, TREE clean afterwards (leave-no-plant guard).
  M6's red leg hit the 400 s outer timeout AFTER the named pin had already
  reddened (G2) — recorded as red_rc=124 with the RED proof intact.
* **Stage adjudication inputs**: `stage-adjudication.md` +
  `phase5-stage-adjudication.sh` (runnable, read-only; live census v0.27.12,
  migration set EMPTY 115==115==115, rollback_safe=true source=explicit + the
  capability-regression caveat).

## 9. Wave-4 (phase 6) decisions + deviations — 2026-10-30

Base `b1183a637` → P6 commit `dc5e19728` (amended once to fold the two
matrix-discovered harness fixes). Everything below is a recorded deviation /
decision; the plan text and the D6-* rulings otherwise govern verbatim.

| # | Decision / deviation | Ruling that wins | Rationale |
|---|---|---|---|
| D-31 | **An UNSET `PIPELINE_SCRIPTS_FLOOR_COMMIT` is the fail-closed refuse class at arm time** — refuse on an EXPLICITLY pinned dir, downgrade to WARN on a fallback dir. | D6-10 ("missing/unknown floor object or command error ⇒ REFUSE") + T6.15/AC15. | An unset floor makes the ancestry comparison a command error; refusing keeps a mis-made release DETECTABLE at the ceremony (AC15's feared failure mode is "NO arm can ever succeed with nothing able to detect it" — the release-time stage gate detects it at build time, the arm refusal is the loud runtime backstop). The fallback carve-out preserves backward compatibility (§4.2); the escape hatch remains. |
| D-32 | **M25's expected-RED manifests on Pack I I4 (and I5), not I6** as the deferred note predicted. | D-26 precedent (record what the pin actually proves) + mutation-meaningfulness. | I6's `refuse` verdict is driven by the GIT-ANCESTRY leg and is acquisition-INVARIANT: whether the stamp comes from the file or the executor env, the no-stamp fixture still refuses. I4 (non-git WITH a stamp file) is the ONLY fixture family where file-vs-env acquisition changes the verdict (arm ⇒ warn), so the plant reddens there. The B3 property ("the file is the input") is genuinely broken by the plant; the pin that observes it is I4. |
| D-33 | **Pack I Section E amended**: E2 now runs a scratch COPY of the scripts dir with the L1/L2a watchdog machinery stripped (a genuinely-unarmed mixed-generation fixture; sed-renames the definitions, plus a fixture self-check leg), and E2b is a new ARMED control on the real promote.sh. Also the probe pins `sys.path[0]` to the repo under test. | T6.6/D6-5's testable outcome ("token + unarmed ⇒ loud refusal; armed ⇒ proceeds") + the pack-must-be-honest rule. | The committed E2 ran the REAL promote.sh and expected the refusal — impossible once phase 2 armed the tree by construction (the refusal branch is unreachable on an armed tree); pre-P6 it could only ever defer. Constructing the unarmed state needs a fixture, or the leg is vacuous. Separately, the probe (script mode) resolved `daemon` from the interpreter's ambient editable-install checkout (the MAIN tree!), i.e. it would have judged the WRONG tree; the sys.path pin fixes that. The D-22 SEAM CONTRACT (names/signature/verdicts) is untouched. |
| D-34 | **Probe fixture I7 rebuilt deterministically**: the dirty checkout now COPIES the fresh repo (including `.git`) instead of re-building "equivalent" commits. | Determinism (a flake is a defect). | The rebuilt fixture's sha equality depended on both repos committing inside the SAME wall-clock second; under ambient load the dirty repo's fix commit got a later timestamp ⇒ different sha ⇒ the shared floor object vanished from its history ⇒ I7 spuriously refused (observed: 1 fail in 6 standalone runs; reproduced by M24's GREEN leg). Post-fix: 6/6 green. |
| D-35 | **The git probe lives in `upgrade_journal.bounded_git_probe`** — `upgrade_tools.py` has NO `subprocess` reference. | `test_static_no_direct_process_spawn_in_tools_module` (D2/D3 — every process flows through `spawn_executor`; AST-pinned). | The guard needs a read-only git call; the tools module forbids direct spawning. Delegation keeps the discipline AND the seam on `daemon.tools.upgrade_tools` (Pack I's discovery surface). |
| D-36 | **The `install` test fixture scrubs the ambient `ENSEMBLE_UPGRADE_SCRIPTS_DIR`.** | Net-new-failure discipline + test hermeticity. | Discovered: this suite can run INSIDE the daemon's own environment (an agent shell), which carries the LIVE-shaped pin `/home/nea/ensemble-src/scripts/upgrade` (`ENSEMBLE_UPGRADE_LIVE=1` too) — the phase-6 guard makes that leak OBSERVABLE (10 tests reddened). The scrub makes the tests judge the fallback resolution they were written against; tests that deliberately pin a fixture dir set the var AFTER the scrub. No production change. |
| D-37 | **M26's plant re-anchored** after the `anc_rc` refactor (plant-only edit). | Exactly-once plant guard. | The plant's old-string still named `anc.returncode`; the guard refused to plant (ERROR-plant, 0 matches) rather than plant vacuously — the guard working as designed. |
| D-38 | **stage.sh carries the VALIDATE half of T6.15 only**; the SET half is the release bump's job (documented in the runbook §F.2 + the close-out file). The manifest gains `pipeline_scripts_floor_commit`. | D-F7 ("the *mechanism* (set + validate) ships here, the *value* is written at release time") + AC15. | The value is the merge commit — it cannot exist inside the tree that defines it. stage.sh REFUSES to stage while the constant is unset/unresolvable/non-ancestor, so the set step is enforced at the gate; this lane's own stage will therefore require the bump to set BOTH the floor and the stamp (recorded in `change-surface-closeout.md`). |
| D-39 | **R1/R6 re-verified, no code change**: `JOURNAL_READ_STRICT=1` exists only at promote.sh (plain, non-exported; lib.sh defaults it to 0 and refuses only when 1) ⇒ the size-guard refusal is promote-path-scoped; `scripts/stop-ensemble.sh:224` routes `pipeline_settled` → the migrated `journal_read` transitively (already in the anchor table + census transitive section). | R1 (leader ruling) + R6. | Verified by grep + source inspection this wave; the anchor/census packs pin both. |

### Wave-4 verification notes
* Pack I: **27 passed / 0 failed / 0 deferrals**, exit 0, in BOTH default and
  `--expect-implemented` modes; 6/6 repeat runs green after D-34.
* Daemon-side suites: `test_upgrade_tools.py` + `test_upgrade_preflight_daemon_side.py`
  + `test_upgrade_executor_systemd_service.py` = **236 passed, 0 failed**
  (net-new = ∅; the 10 ambient-pin failures were the D-36 hermeticity leak,
  fixed test-side only).
* Mutation matrix: full span **32/32 executable, 0 deferred** (M22–M28/M32
  added this wave; see evidence/MUTATION-MATRIX-20261030.tsv).

## 10. Fix iteration 1 (verifier W-1…W-3 + A-2/A-4) — decisions + deviations, 2026-10-30

Base for this wave: `dc70a46a6` (wave-4 close-out). Branch `fix/promote-preflight-hang`.
Scope: the independent verifier's three WARNINGs and two cheap advisories, surgical
only — no re-architecture, nothing outside the listed sites.

| # | Decision / deviation | Ruling that wins | Rationale |
|---|---|---|---|
| D-40 | **The probe-error class is SPLIT, not folded into the non-git branch**: a `GIT_PROBE_ERROR_RC` (255) from `bounded_git_probe` REFUSES on a git-SHAPED dir (`.git` present — file or dir) and WARNS on a non-git dir. | D6-10 (command-error ⇒ refuse) + Q2/D6-11 (non-git ⇒ stamp is the provenance). | A bare `(255, "", …)` folded into the non-git branch would ARM a stamped dir on an ancestry nobody verified — strictly worse than the exception. Refusing everywhere would invent a new refusal class for git-less hosts and break I4's documented non-git arm. Residual (disclosed): a git-less host judging a stamped dir that is a SUBDIR of a repository (no local `.git`) warns instead of refusing — the stamp is still the provenance there, so the outcome matches Q2. |
| D-41 | **A status-probe failure reports `dirty=yes`**, not a third "unknown" state. | Audit-row honesty with a one-line diff; M24 plants on the EXACT `prov_git` f-string. | Adding a third state would have moved M24's anchor for no safety gain. `dirty=yes` is the conservative reading (never claims a clean tree) and the row is an audit artefact, never a gate input. |
| D-42 | **`scripts-dir-unusable` is exercised by INJECTING the dir at the `_resolve_scripts_dir` seam.** | W-3 asks for executed coverage of a branch that had none. | `_resolve_scripts_dir` itself requires `lib.sh`, so through the resolver the branch is reachable only via a TOCTOU (lib.sh removed between resolution and the guard) or a future caller that does not pre-filter. The CALLER's handling — refusal token, lock release, journal row — is identical whichever way the state arises, and that is what the leg pins; Pack I's I15 covers the verdict itself. |
| D-43 | **stage.sh's stamp gate was EXTRACTED to `lib.sh _pipeline_scripts_stamp_verdict`.** | W-2 asks for a stage.sh refusal leg; the real stage.sh cannot run without a full PyInstaller build (~the whole release lane). | A pack can only execute the gate if the gate is a function. F0 pins that stage.sh really calls it (a helper nobody calls would gate nothing); the mutation matrix drives the function (M37/M38) and the pack drives six stamp shapes (I16a–f). |
| D-44 | **Pack I C1 accepts the documented `unreleased-<lane>` placeholder while the floor is UNSET**, and requires a 40-hex/resolving/floor-equal stamp otherwise. | The release bump owns both values (D-38/D-F7/D6-8); the pack must be green on the lane's own HEAD. | A blanket "must be 40-hex" would redden the pack pre-release, and the placeholder is *supposed* to survive until the bump. The hole W-2 identified (a placeholder ARMING) is closed where a value can actually ship: the release gate (stage.sh + I16a). C1 also cross-checks the PAIR — a 40-hex stamp must equal a set floor. |
| D-45 | **A-4's breach rc is inferred from the substitution's rc** (`BUDGET_BREACH_RC=78`) rather than a new signal channel. | Minimal diff; `_budget_breach`'s `exit 78` + its ORDER/shape are pinned by Pack D. | Only the two journal-read sites are affected and `journal_read` returns 0/1 exclusively, so 78 unambiguously means "the breach handler fired". A general channel (marker file / env token) would touch `_budget_breach`'s pinned contract for no extra coverage. |
| D-46 | **The `journal-read-current` site got the breach arm only** — a non-breach read failure still leaves `CUR` empty (pre-existing). | Brief scope ("keep the loud halt + exit 78"). | The breach case was the one with halting semantics being silently swallowed; the non-breach behaviour is unchanged and out of scope. |
| D-47 | **A-1 ROUTED (not fixed)**: the three synchronous `bounded_git_probe` calls — up to ~90 s worst case (3 × 30 s bound) — run INLINE inside `async def system_upgrade`, i.e. on the event loop, before a live ceremony. | Out of scope: event-loop offload is the sibling db-offload/loop-lag lane's subject. | Sites: `daemon/tools/upgrade_tools.py::scripts_dir_provenance_verdict` — `rev-parse` (:4027), `status` (:4058), `merge-base` (:4085) — called from the arm path at :3715. A promote ceremony already blocks for minutes; the offload belongs with that commission, not a surgical verifier fix. |
| D-48 | **A-3 DEFERRED (cosmetic)**: a 14-day-old in-flight log can be deleted by the per-run retention age pass despite the in-flight fence. | Requires a 14-day wedged txn to matter at all. | Site: `daemon/tools/upgrade_journal.py` per-run log retention (the `_UPGRADE_ENV_SCOPES` age pass, A-3's evidence). Fence-vs-age is a retention-policy question, not a defect on this lane's surface. |
| D-49 | **The plan set's `M1–M32` span claim is left untouched; M33–M40 live in THIS ledger** + the matrix runner + the TSV. | The plan set is a frozen approval artefact and `gate.sh` class (b) asserts ITS span. | Appending legs to the canonical span would mean editing six plan docs + the gate's own constant — noise for a verifier-fix wave, and it would re-open the approval chain. This is the D-25 precedent: record where the evidence lives. `gate.sh` re-run after the change: **12/12 PASS** (unmodified). |

### Wave-5 (fix iteration 1) verification notes

* **Pack I** (`preflight_ceremony_readiness_test.sh`): **38 passed / 0 failed / 0
  deferrals**, exit 0, in BOTH default and `--expect-implemented` modes. New legs:
  I13/I14 (W-1 probe-error ⇒ refuse / warn, never an exception), I15
  (scripts-dir-unusable), I16a–g (the stage-time stamp gate, executed).
* **Pack D** (`preflight_watchdog_test.sh`): **45 passed / 0 failed**. New leg D11
  (A-4) runs the REAL `promote.sh` from a scratch scripts-tree copy whose
  `journal_read` sleeps past `PREFLIGHT_STEP_BUDGET_S=3`, and asserts exactly ONE
  halt row (`deadline-breach-journal-read`), NO mislabelled "journal read REFUSED"
  row, ONE breach WARN, rc 78 and the lock released.
* **Daemon-side** (`test_upgrade_tools.py` + `test_upgrade_preflight_daemon_side.py`
  + `test_upgrade_executor_systemd_service.py` + `test_upgrade_journal.py`):
  **357 passed / 1 skipped / 0 failed**. New class
  `TestScriptsDirProvenanceArmPath` drives the real arm path for W-1 (probe raises ⇒
  refusal + LOCK RELEASED), W-3a (floor-unset on an explicit pin ⇒ refuse), W-3b
  (fallback dir ⇒ warn downgrade, arm proceeds) and W-3c (scripts-dir-unusable ⇒
  refuse) — each asserting the `arm-scripts-provenance` journal row.
* **Mutation matrix, legs M33–M40** (all executed: plant → named pin RED → restore →
  GREEN, planted digests recorded in `evidence/MUTATION-MATRIX-20261030.tsv`;
  leave-no-plant guard: TREE clean). M33 (probe try/except removed) ⇒ I13 raised;
  M34 (probe-error branch dead) ⇒ I13 `got=arm`; M35 (unusable guard dead) ⇒ I15;
  M36 (breach rc unrecognised) ⇒ D11 `halt rows=2`; M37 (40-hex requirement dropped)
  ⇒ I16a; M38 (floor-equality dropped) ⇒ I16f; M39 (floor-unset downgraded to warn)
  ⇒ W-3a; M40 (fallback downgrade removed) ⇒ W-3b.
* **Regression sweep, the other preflight packs** (all green at the fix commit):
  size-guard 24, logging 30, stage-lock 13, journal-parity 60, journal-scale 12,
  census 10, anchor 42, shasum-batch-parity 4, repro 7 — i.e. the A-4 caller
  rewrite did not disturb the size-guard refusal path (which still writes its
  own "journal read REFUSED" row for a NON-breach rc=1), the ledger/parity
  corpus, the anchor table or the census.
* **Fence**: zero live contact in this wave — every leg runs under the packs'
  `preflight_pack_common.sh` fence (scratch install root + `FAKE_HOME` + escape
  guard + per-variable `POSTGRES_*` scrub); the daemon-side legs are `tmp_path`-only.

## 11. Fix cycle 2 (code council: 3 WARNINGs + 9 advisories; tidier: H1/M1/M2/M5…M10) — decisions + deviations, 2026-10-30

Base for this wave: `f76765e6a82` (fix iteration 1 close). Branch
`fix/promote-preflight-hang`. Inputs: `review-code-council.md` (APPROVE-WITH-
ADVISORIES, 0 CRITICAL / 3 WARNING / 9 ADVISORY) and `tidier-report.md`
(29 findings from 1 of 3 lanes — the two readability/robustness lanes died on
a provider rate-limit storm, so the shell surface is NOT craftsmanship-reviewed
by that report; this ledger covers only the findings it delivered).

| # | Decision / deviation | Ruling that wins | Rationale |
|---|---|---|---|
| D-50 | **W1 fixed by propagating the rc, not by dropping the wrap**: `_run_bounded_step adopt-txn adopt_stale_txn \|\| { _arc=$?; [ "$_arc" -eq 78 ] && exit 78; _refuse txn-busy …; }`, and the wrapper-contract text now reads "read-only **or death-idempotent by its own design**" with the adopt idempotence argument cited at the call site. | W1's own fix; D2-14's invariant has to be TRUE as written. | Dropping the wrap was the council's alternative; keeping it preserves the 120 s kill vector on a step whose healthy cost is ms while the recovery is designed to survive death (flip-first ⇒ the launcher sweep re-runs it). What could NOT stay is the contract text claiming every wrapped site is read-only. The rc arm removes the double row for every adopt refusal class. |
| D-51 | **The warn-only adopt classes still write NO row** (kind=restart / unparseable `started_at` / FRESH txn). | Pre-existing behaviour; W1 asks to stop inventing a CONTRADICTING row, not to add rows. | Their WARN carries the reason on the per-run narrative, and `_refuse txn-busy`'s lie is gone. Pinned as "exactly one row for the journaling class, ZERO for the warn-only class, never a txn-busy echo" (Pack D D13) — the pin states the invariant, not a new row shape. |
| D-52 | **W2 refuses on 128 only when the dir IS git-shaped** (`(path/".git").exists()`, file or dir). | D6-10 (command-error ⇒ refuse) + Q2/D6-11 (non-git ⇒ the stamp IS the provenance). | One predicate mirroring the 255 handler. The companion leg I13c pins that the SAME 128 on a dir with no `.git` still ARMS on its stamp — otherwise the new arm would invent a refusal class for git-less hosts and break I4. |
| D-53 | **W3 branches on `_PIPELINE_PHASE`; UNSET ⇒ the promote (loud) arm.** | R1 (no fail-fast on a recovery path) + fail-closed-by-default. | Every shipped recovery script sets its phase BEFORE its supervision call (`restart:adopt` :192→:199, `rollback:preflight` :147→:154, `stop:lock`, `stage:lock`), so only an un-phased ad-hoc caller gets the loud arm — which is today's behaviour, so nothing silently degrades. Both arms pinned (D10/D10b loud, D12 continue). |
| D-54 | **A5 validates at the SOURCE site and never fails the source**: `_numeric_knob` prints a NAMED `ERROR:` line to stderr and substitutes the documented default. | A5 ("loud validation error, not a cryptic death") + R1. | A hard exit at source time would brick `status.sh`/`restart.sh`/`rollback.sh` on an unrelated typo — the exact fail-fast-on-recovery-path class W3 is about. `_budget_in_force` now prints the RESOLVED globals (one source of truth per budget; no duplicated default literals). |
| D-55 | **A2 renames the writer to `<run_id>.client.log` AND keeps the legacy `<run_id>.payload.client.log` fenced.** | A2's option 1 (rename) + retention safety for runs started by an older build. | The rename makes the family exactly the three documented names; the legacy fence entry costs one `case` alternative and prevents retro-deleting an old build's client log mid-ceremony. Pinned by E10b including a non-vacuity leg (20 fillers kept ⇒ the pass really ran). |
| D-56 | **A7 refuses with the existing `executor-systemd-unavailable` family** (`reason:` text names `client-log-unavailable`) instead of minting a new envelope token. | Fail-closed triage + the project's envelope-vocabulary discipline. | The refused claim is "a transient unit started and its payload ran" — unverifiable when the client stderr cannot be read. A new token would need an envelope/vocabulary decision this lane does not own; the reason text keeps the row unambiguous and a pinned pytest leg (`test_unreadable_client_log_refuses_instead_of_guessing`) holds it. |
| D-57 | **A8 uses `asyncio.to_thread` for the observation** (not a byte-tail re-implementation of `state.json`). | A8's own two options. | The read is ~5-15 ms; the loop-safety property is the point, and `to_thread` gets it with no parser fork. (The DEDICATED-executor rule in this module applies to the long BLOCKING `waitpid`, a different constraint.) |
| D-58 | **A1 restructures `_tree_manifest` into a forwarding-TERM wrapper + a backgrounded worker**, with `pgrep -P` (when present) reaping the in-flight `shasum` before `xargs`. | A1 ("`setsid` the batch or kill the group") under the constraint that `_run_bounded` is a shared primitive used by 7+ pre-existing sites. | A signal does NOT reach grandchildren, so a trap in the inner worker alone was unreachable — the FIRST attempt at this fix failed its own pin (proved by the new leg, which is why the leg exists). Changing `_run_bounded` to `set -m` + group-kill would have changed semantics for every bounded site (load-stop, hand-back, probes); the wrapper keeps the change inside one function. Every call site already runs `_tree_manifest` in its own subshell, so the trap cannot leak. Residual (disclosed): `pgrep` absent ⇒ only `xargs` is TERMed; at most ONE in-flight `shasum` chunk finishes and exits (no new spawns, no leak). |
| D-59 | **A4 derives each script's strictness FROM ITS FILE** rather than tabulating six constants. | A4 + R1. | A hand-copied table would agree with a future defect (if someone marked a recovery script strict, both the script and the table would say "strict"). Deriving from the file makes the leg RED on exactly the R1 violation. |
| D-60 | **A9 amends the G5 row IN PLACE with a supersession pointer to D-3**, rather than rewriting the row's verdict. | The plan set is a frozen approval artefact; the project's own supersession style. | The measurement stands (BASE and the helper both refuse); what was wrong was the plan's premise about `$(cat)` dropping the NUL before the scan. A reader of the plan alone now sees the ratified direction and cannot "fix" the code back. |
| D-61 | **M9 fixed AND the matrix's own restore trap re-scoped to `PLANTED_LIST`.** | M9 + a defect found while executing it. | Running the matrix from a dirty worktree made its EXIT trap `git checkout --` every dirty plant target — i.e. the safety guard DESTROYED uncommitted work (observed once in this session, work recovered and re-applied). The restoring half now only touches files this run actually planted; the pre-flight assertion stays unscoped (it is read-only). |
| D-62 | **M5's `Literal` sweep stops at the two modules that consume the verdict**; M7 keeps ONE resolver in `upgrade_journal` and both readers delegate; M6 introduces `PerRunObservation(NamedTuple)` with `None` for absent and the "unknown" rendering moved to the row builder. | M5/M6/M7 as written. | Each is the smallest change that removes the failure mode the finding names (typo ⇒ arm; three positional strings; two drifting resolvers). The logging pack's E13 probe was updated in the same commit (it called the old tuple contract). |

### Declined in this cycle (with reasons)

| Item | Disposition | Reason |
|---|---|---|
| M8 (hoist the duplicate drain harness into `tests/unit/tools/conftest.py`) | **DECLINED** | A test-harness refactor, not a safety fix: two `object.__new__(InstanceManager)` harnesses with DIFFERENT fixtures (one seeds a minimal install for `unit_properties` assertions, the other two releases + manifest + symlink) and different recorders. Collapsing them now would hand the next dispatch (full matrix + whole-tree rerun) a behavioural change in test infrastructure for no coverage gain. Both copies cite each other and the drift risk is a two-file edit that the tidier already documented. Next tidy pass, when no gate is queued. |
| M4 (stop committing the ~295 KB G1–G4 generated fixtures) | **DECLINED** | Not in this cycle's brief, and the counter-argument is documented at `CORPUS.md:3`. Generating them at pack run time is a fixture-lifecycle decision that belongs with the corpus owner. |
| Tidier low #15 (PACKS.md rows for the 14 packs + matrix) | **ROUTED** | `.agents/tester/PACKS.md` is the tester's file; the shape precedent is the dbpool table. Routed at merge, not edited from this lane. |

### Fix-cycle-2 verification notes

* **Pack D** (`preflight_watchdog_test.sh`): **68 passed / 0 failed**. New legs:
  D10b (explicit `promote:` phase ⇒ halt), D12 (recovery phase ⇒ WARN +
  continue + no halt row + the R1 wording), D13 a/b (adopt-refusal accounting
  through the REAL `promote.sh` and a planted stale txn — one halt row for the
  journaling class, zero for the warn-only class, never a `txn-busy` echo),
  D14 (garbage numeric knobs ⇒ three named ERROR lines + documented defaults +
  computable deadline arithmetic).
* **Pack I** (`preflight_ceremony_readiness_test.sh --expect-implemented`):
  **40 passed / 0 failed**. New legs I13b (git-shaped + `rev-parse` rc=128 ⇒
  refuse) and I13c (the same rc on a non-git dir ⇒ still stamp-provenance).
* **Pack E** (`preflight_logging_test.sh`): **33 passed / 0 failed**. New leg
  E10b (all four current-run names survive + the writer names the third one),
  and the E13 probe updated for the typed observation.
* **Pack A/shasum** (`preflight_shasum_batch_parity_test.sh`): **7 passed / 0
  failed**, including the new A1 kill-hygiene leg (no leaked mktemp pair, no
  surviving grandchild) — and the parity legs stayed byte-identical after the
  `_tree_manifest` restructure.
* **Pack J/size-guard**: **31 passed / 0 failed** with the six new A4
  per-script legs (+ the transitive stop-ensemble leg).
* **Unchanged and re-run green**: journal-parity 60, journal-scale 12, anchor
  42, stage-lock 13, repro 7, census 10.
* **Daemon-side pytest**: `test_upgrade_preflight_daemon_side` 32 + 
  `test_upgrade_tools` 177 + `test_upgrade_journal` + 
  `test_upgrade_executor_systemd_service` = **358 passed / 1 skipped / 0
  failed**. New: `test_unreadable_client_log_refuses_instead_of_guessing` (A7)
  and the A2 name assertions in `test_client_stderr_is_a_file`.
* **Mutation legs M41–M46** (executed: plant → named pin RED → restore →
  GREEN; tree clean after every leg): M41 (W2 arm dead ⇒ I13b `got=arm`),
  M42 (recovery phase not scoped out ⇒ D12 rc=78), M43 (78 not propagated ⇒
  D13a txn-busy row), M44 (`<run>.client.log` unfenced ⇒ E10b fence lost),
  M45 (numeric guard dead ⇒ D14 sourcing dies on a garbage knob), M46 (no
  forwarding TERM ⇒ A1 leak + surviving grandchild). Two further pins are
  mutation-verified by a manual plant (recorded here because they pin
  behaviours whose plant file is not a matrix PLANT target): the A4 legs
  (appending `JOURNAL_READ_STRICT=1` to rollback.sh ⇒ `FAIL A4 rollback:
  derived strict=1, R1 wants 0`, restored ⇒ green) and the A7 pytest leg
  (`if not err_tail_read:` → `if False:` ⇒ `1 failed`, restored ⇒ 32 passed).
* **Fence**: zero live contact. Every pack leg runs under
  `preflight_pack_common.sh` (scratch install root, `mktemp` FAKE_HOME, escape
  guard, per-variable `POSTGRES_*` scrub); the daemon-side legs use `tmp_path`.

## 12. Mutation-plant re-anchor (evidence re-run) — 2026-10-30

Base for this wave: `5bb465e22` (fix-cycle-2 close). The full-matrix run at that
HEAD measured **45/46 ok** with the single failure being leg M8 itself:
`PLANT-FAILED: scripts/upgrade/journal_read.py pair#1 matched 0 times (want
exactly 1) — the source drifted`
(evidence/MUTATION-MATRIX-FULL-20261030.log, sha256 871f9738…).

| # | Decision / deviation | Ruling that wins | Rationale |
|---|---|---|---|
| D-63 | **Leg M8's plant pair is RE-ANCHORED to the annotated signature; nothing else in the plant table changes.** The `find` text moves from `def cmd_verify(path):\n    data = _read_processed(path)` to `def cmd_verify(path: str) -> int:\n    data = _read_processed(path)` (count restored to exactly 1 at `5bb465e22`), and the `replace` text injects the SAME defect semantics — `import time as _t; _t.sleep(6)` before `_read_processed(path)` — under the new signature. | Fix-cycle-2's tidier pass annotated `journal_read.py` (type hints on `cmd_verify`), so the M8 anchor silently stopped matching. | The ERROR-plant guard worked AS DESIGNED: the exactly-once count refused loudly (PLANT-FAILED, matched 0) instead of planting nothing and reporting a vacuous pass — this is the drift-detection behaviour the plant table was built for, now proven on a real annotation drift. Re-anchoring the find text is the minimal change that restores the leg; the defect semantics (the 6 s stall the scale pack detects) are byte-identical to the original plant. No other leg touched; single-leg M8 proof re-run before the full-matrix re-run. |

## 13. Mutation-matrix evidence record — full-run ledger R1→R3 — 2026-10-30

Three hardened-setsid full-matrix runs in disposable worktrees bracket the M8
re-anchor (§12/D-63) and close the matrix. The `*.log` evidence files are
gitignored (`.gitignore:54`, `*.log`) — the digests below keep them verifiable
after worktree teardown; the committed per-leg record is the R3 `.tsv`
(`evidence/MUTATION-MATRIX-FULL-20261030-R3.tsv`, sha256
`3fd3bccbf53823e41dd00d2fba54f61307406694844f8d6482fcf5d779197fa1`, 46 rows +
header, per-leg `planted_sha256` included).

| Run | HEAD | Log (evidence/) | log sha256 | Result | Wall |
|---|---|---|---|---|---|
| R1 | `5bb465e22` (pre-re-anchor) | `MUTATION-MATRIX-FULL-20261030.log` | `871f97385f81fdfbba8a43eb70c8186f4316fb95e6baa01bded4af154b42c6d5` | 45/46 ok — the 1 failure is leg M8 itself: the ERROR-plant refused loudly on exact-string anchor drift after the fix-cycle-2 annotations (`PLANT-FAILED` per D-63; the M8 table row is absent from the log, summary reads `45 executed … 1 failures`, matrix FAIL) | 44m32s |
| R2 | `dec3a82c2` (post-re-anchor, D-63) | `MUTATION-MATRIX-FULL-20261030-R2.log` | `e9d290fd7d1411520f518c5dfbb46fa5065cf467502ea30eefbf4557d23ac39b`¹ | 45/46 ok — the 1 failure is M15 green-phase miss (`M15 GREEN-not-proven(rc=1)`, red-output head `FAIL F2 rows=0 want 1` post-restore); M15 passed in R1-full and 2/2 in isolation at the same planted digest `52c810c6…` | 46m34s |
| R3 | `dec3a82c2` | `MUTATION-MATRIX-FULL-20261030-R3.log` | `81b3b3577b959121f1ffc2d30ea36547f7893a0048c300bb25fad3f901f02a08` | **46/46 executed legs green-and-red-proven, 0 failures, contiguous** — `MUTATION MATRIX: PASS (span M1–M46 complete …)`; M6 `red_rc=124` is its designed timeout RED | 46m04s |

¹ Transcription note: the dispatch note carried `…dfbb46aa5065…` for this
digest; the file on disk hashes to `…dfbb46fa5065…` (a single hex digit
differs). Recorded as computed — the R2 file (4,311 B) is the only R2 log in
`evidence/`, and its content matches the R2 narrative above.

| # | Decision / deviation | Ruling that wins | Rationale |
|---|---|---|---|
| D-64 | **R3 is the authoritative mutation-matrix evidence for this lane** (46/46 executed legs green-and-red-proven, contiguous M1–M46, 0 failures at `dec3a82c2`), and the two earlier single-failure runs are dispositioned with NO code changes: R1's M8 failure = the plant-table drift guard firing AS DESIGNED (re-anchored at D-63), and R2's M15 failure = a characterized load-transient (3 of 4 contiguous observations green; every isolated observation green; ambient 2-core CPU spinners on the host during R2) — NOT routed as a product defect; flake-ledger candidate only if the tester sees it again. | D-63 (the re-anchor is proven by R2 and R3 both passing M8) + the matrix's own contiguity requirement (a run is evidence only when every leg red-proves its pin AND restores green). | Collapsing the ledger to R3 alone would destroy the failure history that proves the guard works (M8 in R1) and would leave M15's non-reproducibility undocumented — it failed exactly once, in one full run under ambient load, and is green everywhere else including both isolated runs at the same planted digest `52c810c6…`. Recording all three runs with verifiable digests keeps the gitignored logs auditable after worktree teardown, and routing M15 as a transient rather than a defect declines to invent a failure mode the evidence does not support. |
