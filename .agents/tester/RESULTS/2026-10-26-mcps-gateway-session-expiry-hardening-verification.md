# MCPS Gateway Session-Expiry Hardening — Final Independent Verification

- **Date**: 2026-10-26
- **Branch**: `feature/mcps-gateway-session-expiry-hardening` (worktree `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-mcps-gateway-session-expiry-hardening`)
- **HEAD**: `6e63f348222f577259e2e8f5183e641df21bad48` (6 commits: `8362502ea`, `81c1a1d42`, `6c5b89ec3`, `8af4473d3`, `20ca90b02`, `6e63f3482`)
- **BASE**: `0138952876bb456f63417e040884ab28761c9835` (= remote `latest` tip at gate time)
- **Verifier**: tester (independent; 8 dispatched workers — recon, M1, M2a, M2b, M3×3 lanes, M4, M5)
- **Verdict**: **BLOCK — narrowly scoped, overridable** (details in §0)

---

## 0. Verdict summary

**BLOCK** on exactly one item, and it is a *process/evidence* item, not a code defect:

> The developer's justification for relaxing the `session.end` audit assertion — "the P3 test **failed 3/3 on BASE**" — **is not reproducible**. BASE is green in 2 of 3 required attempts (2 of 5 total). The mandate-4 tripwire ("if your evidence contradicts the developer's claim, BLOCK and say so") therefore fires. The developer's BASE decomposition is also wrong: **BASE has no `session.end` gate at all** (the branch *adds* it), so the relaxation was justified against a gate that does not exist at BASE.

**What is NOT blocked** — every technical mandate passes on its own evidence:

| Mandate | Result | Core evidence |
|---|---|---|
| 1 — close against the original incident | **PASS (both legs)** | All 4 symptoms reproduce on BASE; all 4 gone on HEAD with positive counterparts (§2) |
| 2 — mutation-proven coverage | **PASS (11/11 mutations proven)** | Every mutation → in-body `AssertionError`; control + real tree green (§3) |
| 3 — A/B regression | **PASS (zero A/B delta)** | L1 232→304 (+72 new, 0 regressions), L2 16 files 0 delta, L3 41→42, L4 38/38 solo, L5 299→371 (§4) |
| 4 — P3 ratification | **Ruling: strictly-correct-not-weaker (independent proof); developer claim contradicted** | Race proven with DB rows; owner gate still bites; old conjunct verdict-neutral (§5) |
| 5 — sanity sweeps | **PASS (6/6)** | Wire params, vocabulary, tool signature, scope, push state, secret scan (§6) |

**Override path for the leader**: if you accept the contradicted BASE claim as a *process* finding (rather than a gate condition) — which the evidence supports, because the relaxation itself is independently proven safe and verdict-neutral — then **all technical grounds for SHIP are met**. To accept it, waive the M4 tripwire explicitly; nothing else in this report needs waiving.

**Recommended before merge (not the block)**:
1. 🟠 De-flake the P3 lane: `CENTRE_PROFILE` has `HEARTBEAT_TIMEOUT_MS=1500` vs `COMMAND_TIMEOUT_MS=1200` → a ≥300 ms window where the heartbeat kill wins the race. The lane is ~50 % flaky on **both** trees (BASE 2/5 green, HEAD 3/6 green) — **base-attributed, not branch-caused**, but the branch's new assertions live inside that flaky lane. Cheapest fix: raise the profile's heartbeat timeout above `COMMAND_TIMEOUT_MS` + margin (or lower the command deadline) — test-code only.
2. 🟠 Isolate the conformance/integration fence DB per run: `tls_harness.reset_audit_table` issues `DELETE FROM mcp_gateway_audit` (no `WHERE`) against the **shared** `ensemble_dev_test_only` with a fixed `MACHINE_ID='mach_interop'` → any concurrent mcps-gateway/P0 run can wipe the ledger mid-lane (observed: BASE attempt a1's ledger-contamination signature, `delta=5`, every session-scoped pick `rows=0`).
3. 🟢 Ledger the two deterministic base reds found here (§4.3) — done in `QUARANTINE.md` by this gate.

---

## 1. Scope, method, constraints

**Change set** (blast radius): 17 files, +4530/−83 — `daemon/services/mcps_gateway/{__init__,config,protocol,service,tombstones}.py` (5), `daemon/tools/mcp_rpc_tools.py`, `daemon/routers/mcps_gateway_read.py` (2), `docs/mcps-gateway.md` (1), `tests/**` (8: 3 conformance, 2 integration, 3 unit), `.agents/shared/planning/mcps-gateway-interop/p0-gate-evidence.md` (1 — evidence note, see §6.4). Only 3 of the 9 L1 unit files are branch-modified (`config_and_tools` +2 tests, `docs` +11, `session_expiry` **new file, 59 tests**).

**Method**: 8 workers, all scratch-isolated. Scratch trees were created only via `git archive` into `/tmp/mcps-gate/*` (never `checkout`/`stash`/`reset` in the worktree); each mutation worker re-initialised a local git repo in its scratch for restore discipline. The worktree HEAD never moved (`6e63f3482` verified before/after by 4 workers); the worktree `.venv` was never `uv sync`ed (that path can rewrite the tracked `uv.lock`); no push, merge, rebuild, restart, or activation was performed; ports 8088/8079 never touched.

**Branch purpose as tested**: the gateway CENTRE's idle/TTL sweep becomes the single owner of the expiry reap — two-pass (batch centre-side release with no await, then serial bounded `session.end` forwards), reason-coded end responses (`why_not_ended ∈ {expired, already_ended, unknown_session, machine_refused}`; `{machine_unreachable, machine_timeout}` ledger-only, never in a response), bounded tombstone store (512 entries / 3600 s), `session.end` + `session.expire` audit rows, type-gated timeout discrimination (a relayed `TOOL_TIMEOUT` is a refusal and the session survives; only the centre's own deadline releases authoritatively). Wire contract unchanged: `session.end` params stay exactly `{"session_id": ...}`.

---

## 2. Mandate 1 — integrated incident reproduction (PASS, both legs)

**Scenario** (one narrative, `/tmp/mcps-gate/m1/scenario.py`, 25 KB, kept OUT of the branch; recommended **not** a keeper test — every symptom already has dedicated unit coverage in `tests/unit/test_mcps_gateway_session_expiry.py`, and the script's unique value is cross-tree A/B, which is gate-harness, not unit-test, scope):

1. connect a fake machine → centre; 2. agent opens a session; 3. pinned `Clock` advanced past the **idle** timeout (1000 ms; TTL 600 000 ms so idle fires), `service.expire_sessions()` invoked **directly** (background monitor never started → nothing else can reap); 4. observe S1→S4 in order. Anti-tautology: `session.end` frames counted **before** the sweep (=0), so any observed frame is provably the sweep's. Fail-closed tree gate: exits 2 unless `daemon.__file__` **and** `sys.prefix` both resolve under the target tree. In-process; no socket, no port, nothing left behind.

| Symptom | Reproduces on BASE? | Gone on HEAD? | Positive counterpart on HEAD? | Evidence |
|---|---|---|---|---|
| **S1** machine got no `session.end` (lock stranded) | **YES** | **YES** | **YES** | BASE 0 frames; HEAD exactly 1, `params=[{"session_id": "sess-alpha"}]`, pre-sweep baseline 0 |
| **S2** roster `busy:true` + dead `session_id` | **YES** | **YES** | **YES** | BASE `busy=True session_id='sess-alpha'` at store **and** read-router; HEAD `busy=False session_id=None` at both layers, `agent_facing_roster` `session_id: null` |
| **S3** agent end → bare no-reason answer | **YES** | **YES** | **YES** | BASE raised `SESSION_NOT_FOUND`, `data.session_id='ari'` (**agent** id — wrong-helper bug; expected the session id `sess-alpha`), no `ended`/`why_not_ended`/`detail`; HEAD envelope `ended=false, why_not_ended='expired'`, `detail='the session expired and was reaped by the center'`, `machine_notified=true` |
| **S4** no audit row for the expiry | **YES** | **YES** | **YES** | BASE ledger **entirely empty** (0 rows — worse than "missing one row": no record the session existed); HEAD exactly **1** `session.expire` row (`outcome=ok, error_code='idle'`) |

BASE exit 0 (5.0 s) / HEAD exit 0 (6.0 s); both legs PASS. No symptom lingers on HEAD → no BLOCK evidence from Mandate 1.

---

## 3. Mandate 2 — mutation-proven coverage (PASS, 11/11)

Discipline enforced: control-green-first in the unmutated scratch, **every** producing site enumerated by grep and stripped (site counts reported), one mutation at a time, `git diff` verified to touch exactly the intended site(s), restore + porcelain check between mutations, mutant failure required to be an **in-body assertion** (no import/fixture/skip failure), control + real-tree re-verified, and an import-path proof that the scratch's own editable `.pth` resolved the mutated code (not the reviewed tree). Letter-bearing ids (`sess-alpha`, `machine-alpha`, `ari`, `sess-bravo`) cited so no failure could be an identity function.

| id | Mutation (file:line; sites found → stripped) | Pin that bit | Failure (verbatim) | In-body? | Control | Real tree |
|---|---|---|---|---|---|---|
| **M-a** | sweep's `session.end` forward stripped — `service.py:1074-1080` (`_forward_session_end_outcome`; `METHOD_SESSION_END` sites 5 → sweep site 1) | `test_mcps_gateway_session_expiry.py::TestTheSweepForwardsTheSessionEnd::test_the_sweep_forwards_the_session_end_to_the_machine` | `:295 assert len(machine.session_ends) == 1` → `E assert 0 == 1` | ✅ | 113/113 | 113/113 |
| **M-b** | sweep's roster clear stripped — `service.py:992` (`_store.clear_session`; 2 sites → sweep 1) | `…::test_the_sweep_clears_the_roster_pointer` | `:333 assert record.session_id is None` → `E assert 'sess-alpha' is None` | ✅ | 113/113 | 113/113 |
| **M-c** | sweep's tombstone write stripped — `service.py:993` (`_record_tombstone` 4 sites / `REASON_EXPIRED` 5 → sweep write 1) | `…::TestTheTombstone::test_a_late_end_after_the_sweep_answers_expired` | `:453 assert result["why_not_ended"] == REASON_EXPIRED` → `E assert 'unknown_session' == 'expired'` | ✅ | 113/113 | 113/113 |
| **M-d** | sweep made **single-pass** (per-session release→forward instead of batch-release-then-forward) — `service.py:984-998` | `…::TestTheTwoPassSweepAtN2::test_the_whole_batch_is_released_before_any_machine_is_told` | `:1508 assert events[:first_forward].count("store.clear_session") == 2` → `E assert 1 == 2` | ✅ | 113/113 | 113/113 |
| **M-e** | release **re-added** to the end-command path — `service.py:1439` (command path had 0 release sites → 1 release pair) | `…::TestTheSweepIsTheSoleOwnerOfTheReap::test_the_command_path_does_not_release_the_session` | `:763 assert service.sessions.for_agent(AGENT_ID) is not None` → `E assert None is not None` | ✅ | 113/113 | 113/113 |
| **M-f1** | `_response_reason` passes ledger-only tokens through — `service.py:1920` (1 def; 2 consumers leak) | `…::TestTheReasonVocabulary::test_the_runtime_guard_keeps_a_ledger_only_token_out_of_a_response` | `:1176 assert result["why_not_ended"] == REASON_UNKNOWN_SESSION` → `assert 'machine_unreachable' == 'unknown_session'` | ✅ | 125/125 | 125/125 |
| **M-f2** | `_safe_peer_code` passes a ledger-only peer code through — `service.py:1905` (1 def, 1 consumer) | `…::TestTheExplicitEndArms::test_a_peer_cannot_smuggle_a_ledger_only_token_into_the_response` | `:1296 assert LEDGER_MACHINE_TIMEOUT not in json.dumps(result)` → `'machine_timeout' not in '{… "detail": "the machine refused the session end (machine_timeout)" …}'` | ✅ | 125/125 | 125/125 |
| **M-g** | wait_for site raises plain `GatewayProtocolError` instead of `GatewayAckTimeout` — `service.py:830` (1 raise site repo-wide) | 3 centre-deadline pins: `…:687 assert result["ended"] is True`; `…:958 assert record.error_code == LEDGER_MACHINE_TIMEOUT`; `…:1333 assert result["machine_notified"] is False` | see note below | ✅ | 125/125 | 125/125 |
| **M-g′** | **inverse variant**: relayed `TOOL_TIMEOUT` re-typed as `GatewayAckTimeout` (same wait_for site) | `…::TestTheEndContract::test_a_relayed_tool_timeout_is_a_refusal_and_the_session_survives` | `:655 assert result["ended"] is False` → `E assert True is False` | ✅ | 125/125 | 125/125 |
| **M-h** | command path's tombstone lookup stripped — `service.py:1418-1420` (2 `latest_for_agent` sites; command-path one stripped, end-path one retained) | `…::test_a_command_after_the_sweep_answers_session_expired` | `:791 assert excinfo.value.code == CODE_SESSION_EXPIRED` → `AssertionError: assert 'SESSION_NOT_FOUND' == 'SESSION_EXPIRED'` | ✅ | 125/125 | 125/125 |
| **M-i** | `RESERVED_AUDIT_TOOL_NAMES` emptied — `protocol.py:171-173` (1 def; 2 consumers) | `…::TestTheReservedAuditToolNames::test_a_command_with_a_reserved_tool_name_is_refused` + `…::test_the_advertised_catalogue_omits_the_reserved_names` | `:1408 Failed: DID NOT RAISE <class 'Exception'>`; `:1444 assert advertised == ["screenshot"]` → `['session.end','session.expire','screenshot']` | ✅ | 125/125 | 125/125 |
| **M-j** | unscoped "only socket closure **in the gateway**" wording re-introduced — `docs/mcps-gateway.md:566` (3 scoped occurrences; negative arm bites) | `tests/unit/test_mcps_gateway_docs.py::test_doc_states_the_expiry_tells_the_machine_and_keeps_the_socket` | `:1075 assert "the only socket closure in the gateway" not in plain` → *"the unscoped … claim is FALSE — several other paths close a socket. The doc must not enshrine it."* | ✅ | 125/125 | 125/125 |

**Note on M-g (brief correction, important for the ledger)**: the literal mutation collapses the discrimination in the *opposite* direction — the centre's own deadline now masquerades as a refusal, so the **session survives** it — and is caught by three centre-deadline pins, while the named relayed-timeout-survives pin stays **green** (verified solo). A relayed error never enters that `except asyncio.TimeoutError` arm (it arrives via `future.set_exception(GatewayProtocolError(...))` in `_deliver`, `service.py:616-633`), so the only mutation that reaches the relayed-survives invariant is the inverse collapse — proven as **M-g′**. Net: the type-discrimination is guarded from **both** directions by two different pins. Coverage is complete; a ledger entry naming only one direction would have looked "not proven".

**Coverage notes (not defects)**
- The N=2 two-pass ordering property is pinned by **exactly one** node (`test_the_whole_batch_is_released_before_any_machine_is_told`); the N=1 sibling cannot catch a single-pass sweep. Single point of failure — worth a second, independently-shaped pin.
- `test_a_response_never_carries_a_ledger_only_token` exercises only real write paths and never injects a ledger-only tombstone, so the end-to-end-shaped invariant does not cover the guard's tombstone arm; the dedicated runtime-guard pin (`:1141`) owns it.

---

## 4. Mandate 3 — A/B regression (PASS: zero A/B delta)

### 4.1 Lane results (every run reports collected **and** deselected; a zero-collect run is reported as a failure, never as a pass)

| Lane | Tree | Command | Collected | Deselected | Passed | Failed | Exit | Runtime | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| **L1** 9 gateway unit files | HEAD `6e63f3482` | `timeout 300 env -u MCPS_GATEWAY_ENABLED -u MCPS_GATEWAY_TOKENS ./scripts/run_tests_scrubbed.sh <9 files> -q --tb=short -p no:randomly` | 304 | 0 | 304 | 0 | 0 | 20.5 s / 25 s wall | **PASS** |
| **L1** | BASE `0138952876` | same command, exact 9-file list | **0** | 0 | — | — | **4** | 1.35 s | **FINDING** — `test_mcps_gateway_session_expiry.py` is HEAD-only; pytest aborts on the missing path (not a test failure) |
| **L1** | BASE | same command minus the HEAD-only file (8 files) | 232 | 0 | 232 | 0 | 0 | 23.2 s / 30 s | **PASS** |
| **L2** unit A (6 files) | HEAD | scrub wrapper | 134 | 0 | 133 | **1** | 1 | 8.2 s | 1 red, **identical at BASE** |
| **L2** unit A (6 files) | BASE | scrub wrapper | 134 | 0 | 133 | **1** | 1 | 9.1 s | same nodeid, byte-identical message |
| **L2** unit B (6 files) | HEAD | scrub wrapper | 120 | 0 | 120 | 0 | 0 | 41.4 s | **PASS** |
| **L2** unit B (6 files) | BASE | scrub wrapper | 120 | 0 | 120 | 0 | 0 | 36.2 s | **PASS** |
| **L2** integration (4 files) | HEAD | fence env + `--override-ini='addopts=' -m integration` | 48 | **39** | 9 | 0 | 0 | 6.9 s | **PASS** |
| **L2** integration (4 files) | BASE | same | 48 | **39** | 9 | 0 | 0 | 6.1 s | **PASS** |
| **L3** conformance | BASE | `CONFORMANCE_RUN_TIMEOUT=280 timeout 300 bash scripts/conformance/run.sh` | 41 | 0 | 41 | 0 | 0 | 18 s | **PASS** (`41 passed in 15.23s`) |
| **L3** conformance | HEAD | same (+ `UV_NO_SYNC=1` guard; run.sh unmodified) | 42 | 0 | 42 | 0 | 0 | 15 s | **PASS** (`42 passed in 14.37s`) — branch's +1 lifecycle test green |
| **L4** `tests/integration/mcps_gateway` | BASE | fence env + marker override | 38 | 0 | 37 | **1** (`test_p3`) | 1 | 112 s | red = `test_p3` (see §5) |
| **L4** | HEAD | same | 38 | 0 | 37 | **1** (`test_p3`) | 1 | 101 s | red = `test_p3`, **concurrent with the M4 P3 runs** |
| **L4** | HEAD | same, solo re-run | 38 | 0 | **38** | 0 | 0 | 113 s | **PASS 38/38** |
| **L5** docs + `mcp_rpc` registration pins (13 files) | HEAD | scrub wrapper | 371 | 0 | 371 | 0 | 0 | 30.1 s / 34 s | **PASS** |
| **L5** | BASE | exact 13-file list | **0** | 0 | — | — | **4** | 0.08 s | same HEAD-only-file structural finding |
| **L5** | BASE | 12-file subset | 299 | 0 | 299 | 0 | 0 | 24.9 s | **PASS** |

**L1/L5 A/B conclusion**: every BASE-collected nodeid still collects and passes on HEAD. HEAD adds **+72** tests (new file `test_mcps_gateway_session_expiry.py` = 59, `docs` +11, `config_and_tools` +2). **Zero BASE tests removed, renamed, or failed.**

**Lane-scope decisions (mine, disclosed)**
- **L2 set** = the grep `mcps_gateway|mcp_rpc|MCPS_GATEWAY` over `tests/` **minus** the 9 L1 files **minus** `tests/integration/mcps_gateway/**` (L4) **minus** `tests/conformance/**` (L3) = 16 files, re-derived independently by the lane worker (byte-identical to recon). `tests/manual_test_pg_roundtrip.py` does **not** match the grep and was excluded from every file-list regardless (it sets `POSTGRES_*` at import time).
- **BASE file-list convention (L15 open question 1, ruled)**: the mandated exact lists are not runnable on BASE (HEAD-only file ⇒ exit 4, zero collected). Canonical A/B convention adopted: **BASE runs the files that exist at BASE**, and the exit-4 zero-collect runs are recorded verbatim as structural findings rather than hidden. The A/B conclusion is unaffected (L2 independently re-derived the same set and found zero delta; the per-file decomposition proves every BASE nodeid passes on HEAD).
- **L5(ii) `test_gaia_agent.py` (L15 open question 2, ruled)**: excluded from L5 — it is a *negative* forbidden-category pin on one agent's `meta.json`, not a registration-manifest pin. It was nevertheless executed in the L2 unit-A chunk and is green on **both** trees → no coverage gap.
- **Ambient-red guard**: `MCPS_GATEWAY_ENABLED=1` and a live `MCPS_GATEWAY_TOKENS` are exported in this host's env. Every unit run was prefixed `env -u MCPS_GATEWAY_ENABLED -u MCPS_GATEWAY_TOKENS`; a deliberate control run **without** the prefix produced exactly the two named reds and nothing else. **The two ambient reds did not appear in any measured run.**

### 4.2 Classified reds (all pre-existing, none branch-attributable)

| Red | Legs | Classification |
|---|---|---|
| `tests/unit/test_post_restart_arm_notify_no_parallel.py::TestNoNewHttpEndpoint::test_no_new_http_endpoint_in_api_router` — stale `api_router` include-list pin (actual has `wedge_capture_router`; pin does not) | **identical at BASE and HEAD** (same nodeid, byte-identical assertion text, `At index 28 diff: 'wedge_capture_router' != 'plane_router'`) | **PRE-EXISTING (A/B-executed proof).** Branch touches no `daemon/api.py` (`git diff --name-only BASE..HEAD \| grep -c 'daemon/api.py'` → 0) ⇒ cannot have introduced the router. Not in the quarantine ledger before this gate → **ledgered now** (deterministic base red; test-debt = extend the expected include-list). |
| `tests/integration/mcps_gateway/test_real_pair.py::test_p3_audit_db_proof_and_read_parity` | red at BASE (3/5 attempts) and at HEAD (3/6) under load; green solo at HEAD | **PRE-EXISTING FLAKE (base-attributed).** Race: profile heartbeat kill 1500 ms vs command deadline 1200 ms. One BASE attempt (`a1`) was **not** a race but shared-fence-DB ledger contamination (§5 F3). **Ledgered now.** |
| `tests/unit/test_mcps_gateway_config_and_tools.py::TestDefaultConstantsArePinned::{test_the_enabled_default_is_off, test_the_token_and_machine_defaults_are_empty}` | appear **only** without the `env -u` prefix | **AMBIENT ENV ARTIFACT** (known, A/B-proven). Eliminated by the prefix; control run reproduced exactly these two. |

### 4.3 Zero-collect / silent-death audit
- No lane reported a green summary on zero collected tests. The two zero-collect runs (**exit 4**) are reported above as findings with their verbatim cause.
- No silently-dead run (the 30 s/thread `os._exit` class) occurred in any lane: every run emitted a summary line. No run exceeded 300 s (longest: 113 s L4, 46 s L2 unit B). No EADDRINUSE/bind failures; no retry was needed.
- Full suite never run; `tests/manual_test_pg_roundtrip.py` never collected; no `pytest -x`; no bare `pytest`.

---

## 5. Mandate 4 — independent verification of the relaxed assertion

### 5.1 Attempt tables (nodeid identical in both trees: `tests/integration/mcps_gateway/test_real_pair.py::test_p3_audit_db_proof_and_read_parity`)

BASE scratch (`/tmp/mcps-gate/m4/base`, base archive + `uv sync`):

| attempt | exit | result | runtime | failing sub-gates |
|---|---|---|---|---|
| a1 (required) | 1 | **FAIL** | 28 s | 12 gates — ledger-contamination signature (shared-DB wipe, F3) |
| a2 (required) | 0 | **PASS** | 21 s | — |
| a3 (required) | 0 | **PASS** | 23 s | — |
| a4 (extra) | 1 | FAIL | 25 s | `p3-timeout-command-timed-out — code='INTERNAL'`; `p3-session-survived-the-request-timeout — still_live=False`; `gate-run — GatewayProtocolError: SESSION_NOT_FOUND: there is no live session 'ari'`; `no-orphan-stub-child — pids=[51299]` |
| a5 (extra) | 1 | FAIL | 25 s | same 4 gates (`pids=[51550]`) |

**BASE is green 2/3 (required attempts) and 2/5 overall → the developer's "failed 3/3 on BASE" is NOT reproducible.**

HEAD worktree (`6e63f3482`):

| attempt | exit | result | runtime | failing sub-gates | `session.end` row `outcome` (fence DB, read after the run) |
|---|---|---|---|---|---|
| a1 (required) | 1 | FAIL | 16 s | `timeout-row-correlates — rows=0`; `p3-timeout-command-timed-out — code='INTERNAL'`; `p3-session-survived-the-request-timeout — still_live=False`; `p3-session-closed — ended=False` | **error** (`already_ended`) |
| a2 (required) | 1 | FAIL | 20 s | same 4 | **error** (`already_ended`) |
| a3 (required) | 0 | **PASS** | 23 s | — | `ok` |
| a4 (extra) | 0 | PASS | 23 s | — | `ok` |
| a5 (extra) | 1 | FAIL | 21 s | same 4 | **error** (`already_ended`) |
| a6 (extra) | 0 | PASS | 27 s | — | `ok` |

### 5.2 Ruling on the relaxation — **strictly-correct-not-weaker** (independently proven)

1. **What was actually dropped.** The brief's framing ("the developer relaxed a base-era assertion") is wrong: BASE has **no `session.end` gate at all** — the branch **adds** it. The relaxation is `8362502ea` → `6e63f3482` (the hygiene pass) and drops **exactly one conjunct**:
   `... and session_end_rows[0].get("outcome") == "ok"` → removed. Retained: `len(session_end_rows) == 1`, `args_hash == canonical_args_hash({"session_id": sid})`, `session_id == sid`; plus `expected_rows` 6 → 7.
2. **The dropped value is genuinely racy — proven empirically.** Lane profile: `HEARTBEAT_TIMEOUT_MS=1500`, `COMMAND_TIMEOUT_MS=1200`, `HEARTBEAT_INTERVAL_MS=500` → the heartbeat timer is armed from the *last* heartbeat (0–500 ms before the command), so the kill wins whenever the last heartbeat was ≥300 ms earlier (1500 − 1200). Fence-DB proof: **3 of 6 HEAD runs wrote `outcome="error"`, `error_code="already_ended"`** (the late-end-over-tombstone path, `service.py:1853-1861`). ⇒ the developer's racy-value claim is **CONFIRMED**, with stronger evidence than offered.
3. **Ownership is preserved.** The owning gate (`test_real_pair.py:2069-2072`) asserts `p3-session-closed` = `bool(end_result.get("ended"))`; in **every** race run it **FAILED** (`ended=False`) while the relaxed gate passed. Counterfactual leg (HEAD code + the pre-hygiene test file, scratch `head_pre`): **3/3 FAIL**, each failing the same 4 gates **plus** the `session.end` gate ⇒ the old conjunct was red only in runs that were already red ⇒ the relaxation is **verdict-neutral** (it neither creates nor removes a lane failure).
4. **Residual is owned at the unit layer.** The third end path (machine unreachable / centre ack deadline → `ended=True`, `machine_notified=False`, `outcome∈{timeout,error}`) is no longer caught in the E2E lane, but `tests/unit/test_mcps_gateway_session_expiry.py::TestTheSessionAuditRows::test_an_explicit_end_leaves_a_session_end_row` asserts `tool=="session.end"` **and** `outcome=="ok"`, and the no-ack variants are pinned by `test_an_unreachable_machine_answers_ended_without_notification` / `test_the_centre_ack_deadline_answers_ended_without_notification` → **9 passed in 0.50 s**.

### 5.3 Where the developer's account is contradicted (the BLOCK trigger)

| Developer's claim | Gate evidence | Verdict |
|---|---|---|
| "P3 failed **3/3** on BASE" | BASE green in a2/a3 (2/3 required, 2/5 total) | **CONTRADICTED** — not reproducible under the gate's fixed conditions (base sha, 3 attempts, fence env). Possibly condition-dependent (contention, client version) — but as stated it is false. |
| "the same two gates [as the relaxed assertion] plus base's raising disconnect" | BASE has **no** `session.end` gate to fail; BASE's reds are the race aborting the phase via the raising disconnect (`GatewayProtocolError: SESSION_NOT_FOUND: there is no live session 'ari'`) | **CONTRADICTED** in decomposition — the relaxation cannot be justified by "BASE proved the old gate meaningful", because no such gate exists at BASE. |
| "~300 ms race between heartbeat kill (1500 ms) and command ack deadline (1200 ms) makes `outcome` racy-by-construction" | 3/6 HEAD runs wrote `outcome=error`/`already_ended`; DB-confirmed | **CONFIRMED** (independently). |
| "base's raising disconnect" | observed in BASE a4/a5 and in HEAD race runs | **CONFIRMED**. |

**Reading of the mandate**: the tripwire ("if your evidence contradicts the developer's claim, BLOCK and say so") fires on the BASE-3/3 claim. The *substance* of the relaxation is nonetheless sound and independently proven — which is why the block is scoped to the claim, with an explicit override path (§0), rather than to the code.

**Additional finding (F2 — important, not the block)**: the P3 lane is ~40–60 % flaky **on the branch's own code** (HEAD 3/6 green) and comparably on BASE (2/5) — the flake is **pre-existing**, but the branch's new `session.end`/`session.expire` assertions live inside that flaky lane, so a merge gate built on it will intermittently redden. Note the branch's actual improvement: on BASE the race **aborts** the phase with a raising disconnect; on HEAD the same race **survives and reports** (reason-coded envelope, `ended=False`) — that is the branch working as designed.

**F3 — shared-fence-DB hazard**: `tls_harness.reset_audit_table` issues `DELETE FROM mcp_gateway_audit` (no `WHERE`) against the shared `ensemble_dev_test_only` with a fixed `MACHINE_ID='mach_interop'`; any concurrent mcps-gateway/P0 run can wipe the ledger mid-lane (BASE a1's signature: `delta=5`, every session-scoped pick `rows=0`, a foreign row on the read surface). Such reds must not be attributed to the tree under test. **F5 — the "BASE" leg is not a pure base**: base daemon + base tests + the **current external Go client** (`~/…/opensource-projects/mcps_client`).

---

## 6. Mandate 5 — sanity sweeps (6/6 PASS)

1. **Wire frame unchanged — PASS.** Three `session.end` construction sites, all `daemon/services/mcps_gateway/service.py`: `:1074-1079` (sweep arm, new code), `:1489-1494` (refused-connect release, diff-untouched), `:1598-1603` (explicit end, control-flow-only change). Every payload is exactly `{"session_id": ...}` — no new member, no `reason`. `make_envelope` (`protocol.py:236`) forwards `params` verbatim. Captured live frame: `{"method": "session.end", "params": {"session_id": "sess_…"}}`.
2. **Zero v0.2 vocabulary — PASS.** `git grep -nE 'session\.terminate|MACHINE_ID_CONFLICT|params\.reason'` → 0 hits (tracked files, HEAD) and 0 in the branch diff; `client idle auto-disconnect` / `idle auto-disconnect` → 0. (Gate transcripts contain `params.context.reason` on `session.begin` — pre-existing v0.1 begin vocabulary, not a hit.)
3. **`mcp_rpc_disconnect` takes no argument — PASS.** `async def mcp_rpc_disconnect() -> dict[str, Any]:` (`daemon/tools/mcp_rpc_tools.py:305`); input schema `McpRpcDisconnectInput` has zero fields; all call sites (`test_mcps_gateway_session_expiry.py:1059,:1094`, `test_mcps_gateway_config_and_tools.py:325`) invoke with `{}`; base signature identical.
4. **Scope — PASS with one adjudicated note.** 16/17 files inside the allowed set (`daemon/services/mcps_gateway/**`, `daemon/tools/mcp_rpc_tools.py`, `daemon/routers/mcps_gateway_read.py`, `tests/**`, `docs/**`). The 17th, `.agents/shared/planning/mcps-gateway-interop/p0-gate-evidence.md`, sits outside the literal list: its diff is a **provenance-header correction** (the old blanket "daemon/ source was NOT modified" claim replaced by a base/branch provenance split) plus **six appended P0-gate run transcripts** (2 FAIL → 4 PASS). **Adjudication: acceptable** — a run-log/evidence note in the shared planning space, no source impact; flagged for the leader in case a strict allowlist is wanted (it is trivially droppable from the commit).
5. **Clean + unpushed — PASS.** `git status --porcelain` empty; HEAD `6e63f3482`; `git branch -r --contains HEAD` empty; `git ls-remote origin` → 0 refs for the branch; remote `latest` still `0138952876…`.
6. **Secret scan — PASS.** Live gateway token located in the **untracked** install env `~/agents-ensemble/.env` (`MCPS_GATEWAY_TOKENS`, 1 token, prefix `9e36…`, 64 chars). `git grep -F` over **all** tracked files at HEAD → **0** matches; full branch diff (292 230 bytes scanned) → **0**. Worktree `.env` has no gateway-token entry. (The token value is deliberately not reproduced in this report.)

**Hygiene note for the leader**: the branch's `tests/integration/mcps_gateway/p0_gate.py` **appends evidence to a tracked file by design** — every P0-gate run in the worktree dirties `p0-gate-evidence.md` (+~186 lines per pair of runs). Two gate workers observed the dirt and one restored the file byte-exactly from the HEAD blob (no checkout/stash/reset). The tree was clean at gate end. Consider pointing the evidence file outside the repo (or making it untracked) so a test run can never dirty a reviewed tree.

---

## 7. Findings & follow-ups (numbered, severity)

| # | Sev | Finding | Action |
|---|---|---|---|
| 1 | 🟠 (block trigger, process) | Developer's "BASE failed 3/3" is contradicted (BASE 2/3 required, 2/5 total) and the BASE decomposition is wrong (no `session.end` gate exists at BASE). | Leader ruling: waive the M4 tripwire (technical merits are complete) or require a corrected justification. The relaxation itself is independently proven sound. |
| 2 | 🟠 | P3 lane ~40–60 % flaky on **both** trees (heartbeat-kill 1500 ms vs command deadline 1200 ms). Base-attributed; the branch's new assertions inherit it. | Widen the lane profile's heartbeat timeout above the command deadline + margin (test-code only), then re-run L4/M4 3×3. |
| 3 | 🟠 | Shared fence DB: `reset_audit_table` = `DELETE FROM mcp_gateway_audit` (no `WHERE`) on the shared `ensemble_dev_test_only`, fixed `MACHINE_ID` → concurrent runs can wipe the ledger mid-lane (one BASE red was exactly this). | Per-run isolated fence DB (or per-run unique `MACHINE_ID`); until then, never run mcps-gateway lanes concurrently against one fence DB. |
| 4 | 🟢 | `test_no_new_http_endpoint_in_api_router` — deterministic stale pin (missing `wedge_capture_router`), identical red at BASE and HEAD; not previously ledgered. | **Ledgered in `QUARANTINE.md` by this gate**; test-debt = extend the expected include-list. |
| 5 | 🟢 | The two-pass N=2 ordering property has exactly one pinning node; the N=1 sibling cannot catch a single-pass sweep. | Add a second, independently-shaped pin (e.g. assert roster-busy state while machine A's ack is awaited). |
| 6 | 🟢 | `test_a_response_never_carries_a_ledger_only_token` does not exercise the tombstone arm of `_response_reason`; the dedicated runtime-guard pin owns it. | Coverage note only; consider folding the tombstone case into the end-to-end-shaped pin. |
| 7 | 🟢 | `p0_gate.py` appends evidence to a **tracked** file by design → any P0-gate run dirties a reviewed tree. | Point the evidence sink outside the repo / untracked. |
| 8 | 🟢 | `p0-gate-evidence.md` is outside the mandate's literal allowlist (evidence note only — see §6.4). | Leader may drop it from the commit; no code impact. |
| 9 | 🟢 | This host exports `MCPS_GATEWAY_ENABLED=1` + a live token; lanes without `env -u MCPS_GATEWAY_ENABLED -u MCPS_GATEWAY_TOKENS` report 2 false reds. | Keep the prefix in every lane command; consider unsetting in the daemon's dev profile. |
| 10 | 🟢 | "BASE" legs use the **current external Go client** with base daemon code — not a pure base. | State the client revision in future A/B legs. |

---

## 8. Not verified / limits

- **The Go client** (`~/…/opensource-projects/mcps_client`) was not under test — only the Python centre side. The "shipped Go client never self-expires" premise that the branch mitigates was not re-proven here (out of scope: the branch does not touch the client).
- **Live daemon behaviour** was not exercised: no restart, no activation, no live gateway traffic. The claim "the machine keeps its local session lock forever" is verified only at the centre protocol seam (S1: the centre now forwards `session.end`); the client's lock-release behaviour is inferred from the frame being sent, not observed in a live machine.
- **M5 sweep 1** could not compare the base-side construction sites byte-for-byte (the base file has 2 sites, HEAD 3) — verified by reading the base blob's construction sites individually instead.
- **M5 sweep 6** located the live token only in the install env; if another token is configured elsewhere (daemon config store, DB), that copy was not searched.
- **M4's BASE "pure base"** is qualified by F5; the developer's 3/3 claim might reproduce under their original conditions (contention, older client) — I could not reproduce it, and the conditions were fixed by the mandate.
- **BASE attempt a1** (ledger contamination) has no isolated mechanism proof — its signature matches F3, but that is correlation, not proof.

---

## 9. Artifacts

- M1 scenario: `/tmp/mcps-gate/m1/scenario.py` (+ `base-run.txt`, `head-run.txt`; scratch `/tmp/mcps-gate/m1/base`)
- M2a logs: `/tmp/mcps-gate/m2a/{control,m_a,m_b,m_c,m_d,m_e,real_tree}.log`; scratch `/tmp/mcps-gate/m2a/base`
- M2b logs: `/tmp/mcps-gate/m2b/*.log`; scratch `/tmp/mcps-gate/m2b/base`, `head_pre`
- L1/L5 logs: `/tmp/mcps-gate/l15/logs/`; L2: `/tmp/mcps-gate/l2/logs/`; L3/L4: `/tmp/mcps-gate/l34/logs/`
- M4 logs: `/tmp/mcps-gate/m4/{base_a1..a5, head_a1..a6, pre_a1..a3}.log`; runner `/tmp/mcps-gate/m4/run_leg.sh`
- Ledger updates: `.agents/tester/QUARANTINE.md` (2 new Active rows), `.agents/tester/PACKS.md` (d2 lane packs registered)

---

## 10. Final verdict

**BLOCK** — narrowly scoped to Mandate 4's tripwire: the developer's "P3 failed 3/3 on BASE" is contradicted by executed evidence (BASE 2/3 required, 2/5 total) and the BASE decomposition is wrong (BASE has no `session.end` gate). **The relaxation itself is independently proven strictly-correct-not-weaker and verdict-neutral**, the owning gate still bites, the residual is owned at the unit layer, and Mandates 1, 2, 3 and 5 pass outright (incident closed on both legs; 11/11 mutations proven; zero A/B regression across 5 lanes; 6/6 sanity sweeps clean).

**If the leader waives the contradicted BASE claim as a process finding, the technical verdict is SHIP** — with two recommended non-blocking remediations before merge: de-flake the P3 lane profile (#2) and isolate the conformance/integration fence DB (#3).
