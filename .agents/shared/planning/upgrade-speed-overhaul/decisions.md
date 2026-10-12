# Design Decisions: upgrade-system overhaul (fast-by-default + reliable promote)

- Date: 2026-10-12
- Mission branch: `feature/upgrade-speed-overhaul` @ `c8a3fbf6a48d6dd1bfea97d0fb41140501a552a9`
- Author: technical-analysis worker (via planner context)
- Status: Ready for Review (phase-plan worker should consume verbatim)
- Scope source: upgrade-speed-overhaul mission — 6 goals (G1–G6) + the 4 hard constraints enforced verbatim (livez/readyz gates, auto-rollback, nonce ceremony, journal txn integrity, fail-closed checks, plugin staleness journal-and-proceed, promote-only tool-lane fence)
- Read-only w.r.t. code: this document is the input to a later coding worker; nothing here is executed.

---

## Summary table (decisions → recommendation → one-line)

| # | Decision | Recommendation | One-line |
|---|----------|----------------|----------|
| (a) | Journal splice implementation | **P1** — `scripts/upgrade/journal_splice.py`, stdlib-only, lock-bound (caller holds it), history-only + in_flight + previous writes (NOT pending_op / NOT current on its own), fail-closed on python3 missing | Replace bash brace-walk journal_read+update+history_append with O(1) json round-trip; never silently degrade to the 139s/call walk |
| (b) | Pending-op liveness | **P1+C** — writer via splice (in_flight.last_heartbeat + owner_heartbeat_at) at every lock_heartbeat call site; sweep reads heartbeat + cgroup/pid liveness; LOCK_HEARTBEAT_S=30 cadence preserved | Close the armed→flip misclassification window by adding a heartbeat writer that does not exist today |
| (c) | Adaptive soak default | **P2** — 60s floor · 120s cap · 3 consecutive green probes (livez+readyz+version) · 15s probe cadence · fail-fast on red; opt-out = `ENSEMBLE_PROMOTE_SOAK_S=300`; cap 900 preserved | Cut observed 300s soak to 60–120s while keeping version-drift detection and the anti-flapping ceiling |
| (d) | Total ceremony budget | **P2** — hard 900s wall-clock ceiling enforced at phase boundaries (preflight 45s / stop 240s / flip 30s / handback 90s / livez 180s / readyz 120s / version 30s / soak adaptive / commit 30s); breach → journal `halt` + exit 78 | Honor the user's "TOTAL wall-clock budget honored fail-closed" hard constraint without skipping any safety gate |
| (e) | Transient / boot handling | **P2** — classify HTTP 503 + curl exit 7/22/28 as transient; 3 bounded retries inside probe budget (1s/2s/4s backoff); 60s post-restart boot-window grace (auto-extends past transient probes); extend busy-amnesty to livez+soak by reading the **EXISTING** `readiness.py:198/222 detail.inflight_turns` (NO new `/run/ensemble/inflight_turns` file, NO new daemon writer) with `inflight_turns ≤ 5` bound; probe uses `curl -sS -o tmp -w '%{http_code}'` (NEVER body-sniff after `-f`) | Handle the v0.20.0 boot-storm race (1.5s transient window between pool-ready and gate-fire) without weakening any fail-closed path; the existing readyz `detail.inflight_turns` is the source of truth (no race-prone file) |
| (f) | Audit-after-soak sequencing | **P2** — split `MessageQueueRepository.clear_all` into CAPTURE_PHASE (sync, pre-pool) and DELETE_PHASE (background, post-readyz green + 1s OR 5s); preserve boot_epoch ordering + task-table-intactness assumption + idempotency + config gate | Move the env-poison purge off the boot critical path while keeping every ordering guarantee and the QUEUE_DISCARD_ON_STARTUP knob |
| (g) | Rollback target fix | **P1** — `promote.sh:438` writes `journal.previous = $OLD_CUR` (captured at :227 / :436) instead of letting the auto-rollback branch read the journal `previous` field at `:452-453`; add an immediacy assertion at commit; update `launcher.sh:1009-1019` comment + boot_sweep_commit_and_continue path | Land N-1 on halt→re-promote chains (Oct 9 arc) instead of N-2 |
| (h) | Adjacent smalls | include: G6 printf `:592`, stale comment `promote.sh:21-23`; defer: read-only systemctl probes `:2868/:3066/:4075-4082`, cosmetic `:3662` | Polish that rides the next promote; classify what is NOT scope-creep |

**Tier mapping** (for the phase-plan worker; within-mission ordering — all ship in the first promote on this branch, none are deferred to a later cycle):

- **P1** = ships in the FIRST promote on this branch (correctness + smallest-perf unlock)
- **P2** = ships in the FIRST promote on this branch together (the perf-correctness trio: c, d, e + the dependency f)
- **P3** = rides the FIRST promote on this branch together with P1/P2 — including (f) audit-after-soak sequencing, which interacts with (c)'s stable gate but is otherwise independent. The build does NOT defer (f) to a later promote.

(a)–(f) all ship in the v0.20.1 vehicle. (g) and (h) ride PR-1; (a)+(b) ride PR-2; (c)+(d)+(e)+(f) ride PR-3.

---

## (a) Journal splice implementation

### Problem
`promote.sh` issues 19 `journal_history_append` sites (lines `:232 :268 :315 :327 :336 :357 :440 :463 :472 :481 :491 :506 :512 :521 :557 :560 :566 :575 :583`) plus 4 success-path writes at `:440 :550-553` (the commit block). Each call invokes `journal_read` (`lib.sh:546-584`, per-character brace walk `:560`), `journal_update` (`:609-666`, re-read + brace walk), or `journal_history_append` (`:676-687`, single read + walk). Measured at **139 s/call** on the live `87,080-B / 420-entry state.json` → ~280 s per journal event; quadratic in history length. A clean promote's success path pays ~6–10 events × ~4.6 min ≈ **28–46 min** of journal overhead today (E6 timing digest). The fix the user wants is E1: python helper + atomic os.replace, not "shave the bash walk."

### Options considered

1. **A — Status quo (bash brace walk).** Keep `lib.sh:546-687` as the only journal layer. Perf budget gets eaten; auto-rollback fires only when 60-min soak failure already exhausted the user's patience.
2. **B — Replace bash walk with a python3 helper, stdlib-only, single-purpose, FAIL-CLOSED on missing interpreter.** Caller holds the pipeline lock (today's `journal_history_append` at `promote.sh:177 :301 :342 :427 :503 :2526 :2532 :4249` all execute under `lock_acquire` from `:165`). Helper is invoked as `python3 scripts/upgrade/journal_splice.py <op> <args…>` with explicit argv (no env-sniffing, mirrors `scripts/upgrade/ledger_check.py:18-26` discipline). Writes go via temp file + `os.replace` (Python's atomic rename) inside the same dir. Helper refuses (exit 78) when: interpreter missing, target journal under live install root (`~/agents-ensemble`, the `ledger_check.py:46-55` compare-after-expanduser+resolve+samefile test), or argv malformed. **No silent bash fallback.**
3. **C — Helper, but with a bash fallback on missing python3.** Falls back to `lib.sh:546-687` if `python3` not found. Looks "safer," but is the regression we're fixing: on hosts that ever lack `python3` we silently pay 280 s/event again.

### RECOMMENDATION: **B**

### Rationale (with citations)

- Perf: `os.replace` + `json.load/dump` is O(1) per event vs O(history-length) walk — measured delta is the difference between 28–46 min of journal overhead and <1 min. Source: live measurement on 87 KB state.json (digest; re-anchor at `lib.sh:546-584` `journal_read` per-char brace loop `:560` and `:609-666` `journal_update`'s re-read at `:611`).
- Discipline match: `scripts/upgrade/ledger_check.py:18-26` is the existing template (stdlib-only, explicit inputs, exit 0/1/78, refuses live install via `expanduser+resolve+samefile` test at `:46-55`). Using the same template keeps the repo's python-flavored surface uniformly disciplined.
- Lock interplay: every current journal-write call site already runs under pipeline lock (`promote.sh:165` `lock_acquire` → `:166-170` signal trap → `:177 :2526 :2532 :4249 :301 :342 :427 :503` `lock_heartbeat`). Helper inherits this — the caller holds the lock, helper does not acquire. Single-writer invariant preserved.
- Interactive side: `LOCK_HEARTBEAT_S=30` (`lib.sh:108`) refresh cadence is unchanged; helper is invoked from the same call sites that already `lock_heartbeat`.

### Helper contract (concrete — verbatim from `architecture-recommendation.md` §3; phase-plan worker builds from this, no re-derivation)

**Placement**: `scripts/upgrade/journal_splice.py` (NEW FILE).

**Invocation model** (exact argv grammar — copied verbatim from arch §3, including the C-2 correction):

```
python3 scripts/upgrade/journal_splice.py <journal_path> <op> [op-args...]

  ops (corrected):
    append-history <event-kind> <detail-string>           # top-level `history` append
    set-field <top-level-field> <raw-json-literal>        # ONE top-level key
    set-in-flight-field <nested-key> <raw-json-literal>   # NEW (C-2): `in_flight[nested-key]`;
                                                            # creates `in_flight={}` if absent;
                                                            # refuses non-object
    unset-field <top-level-field>                         # → null
    clear-in-flight                                       # `in_flight` → null
    set-previous <quoted-version|"null">                  # `previous` only

  exit codes: 0 success · 1 runtime error (logged) · 78 refusal
              (interpreter missing/below floor, live-install path via
               `expanduser+resolve+samefile` per `ledger_check.py:46-55 / 90-97`,
               torn journal `JSONDecodeError`, schema drift, malformed argv)
              · 2 argparse-default only
  stderr: one greppable line, token-pinned:
              `splice: ERROR: <op> <args...>: <reason>`
              `splice: REFUSED: <token>: <detail>`
```

**NEVER in helper** (architecture §3 fenced-out list — C-13 disambiguation; the open-txn op is fenced OUT of P2 explicitly; arch §3 wins over the prior self-contradictory narrative at decisions.md ~:79):

- `set-current` (bash `journal_set_current`, lib.sh:826)
- pending_op family (python reconciler owns)
- counters / cooldown_until / quarantine / `rollback_window_count` (bash owners)
- **`open-txn` (fenced OUT of P2)** — bash `journal_open_txn` at `lib.sh:700-...` stays the sole writer until P2 review re-opens the question (arch §3 explicit)

**Field ownership matrix** (protocol boundary — explicit, no implicit; C-2 sets the NESTED write path):

| Field | Helper may write? | Notes |
|------|-------------------|-------|
| `history` (append) | YES | The 19 `promote.sh` sites migrate here. |
| `in_flight` (NESTED keys, e.g. `last_heartbeat`) | YES | Only via `set-in-flight-field <key> <raw-json-literal>`; creates `{}` if absent; refuses non-object. The C-2 correction is that the reader (`daemon/services/upgrade_journal_sweep.py:1590`) reads `in_flight.last_heartbeat`, NOT a top-level `last_heartbeat` — a top-level `set-field last_heartbeat` would be invisible to the sweep. |
| `in_flight` (clear/set) | YES | Only via `clear-in-flight` op; the bash `journal_open_txn` at `lib.sh:700-...` stays the sole writer for txn open/set until P2 review re-opens this. |
| `previous` | YES | Only via `set-previous`; called from `promote.sh:438` (commit) with `$OLD_CUR`. |
| `pending_op` | NO | Python reconciler owns this (`daemon/services/upgrade_journal_sweep.py:1590` reads `owner_heartbeat_at or armed_at`); never write from the helper. Comment in the helper must state this. |
| `current` | NO (helper) | Promote shell writes `current` via `journal_set_current` (`promote.sh:437-area`); helper does not gain a `set-current` op. The shell's ordered commit block (`set_previous → close_txn → history_append`) stays bash-coordinated. |
| `counters`, `cooldown_until`, `quarantine`, `rollback_window_count` | NO | Library/component owners (counters: `lib.sh:2700ish`; quarantine: `journal_set_quarantine`); never from helper. |

**Atomic write** (C-6 serialization rule — verbatim from arch §3):

- Temp file `tmp = jp.with_name(f"{name}.tmp.{pid}.{uuid4hex}.{msecs}")` in the same dir
- `open(tmp,'w',encoding='utf-8')`; write; flush; **no fsync** (matches `journal_write` at `lib.sh:587-603`; D4 spec is "atomic mv" not "durable-on-power-loss")
- `os.replace(tmp, jp)`
- Best-effort tmp unlink on OSError
- **Serialization**: `json.dumps(d, separators=(",", ":"), ensure_ascii=False)` + one trailing `"\n"`
- **Invariants** (C-6 reframe — refutes the R-02 "byte-identity of untouched regions" claim which is unachievable under `json.load → json.dump`): **key order preserved from input (Python 3.7+ dict order) + non-target field values byte-identical after round-trip**

**Preflight probe** (C-7 — verbatim from arch §3; placed between `lock_heartbeat` (`promote.sh:177`) and `journal_init` (`promote.sh:183`), before any destructive step):

- `python3 -c "import sys,json,os,pathlib; assert sys.version_info >= (3,7)"`
- Missing/short → `_warn` + exit 78
- Journal an informational probe-OK event (interpreter path included)
- Floor 3.7 is sufficient (`pathlib.samefile` + dict-order guarantees)
- Stdlib-only; bare `python3` from PATH; `PLUGIN_STALENESS_PYTHON` chain deliberately NOT used (that chain exists for PyYAML deps the helper doesn't have)

**Validation**: parse the entire journal via `json.load` BEFORE computing the mutation; on `JSONDecodeError` exit 78 (refuse to splice a torn journal). This replaces `journal_read`'s brace-balance sanity at `lib.sh:560-580` with a proper parse — cheap, correct, robust against string-embedded braces (the brace walk tolerates them today only by accident).

**Read vs write split**: helper is a **writer**; reads stay in bash (`journal_read`) or move to bash too — but reads are not on the perf-critical path (the perf win is the write side). Reads do not need to migrate in P1.

**Launcher.sh compatibility**: launcher.sh is SELF-CONTAINED (`launcher.sh:59-60 :745-746` — never sources `lib.sh`, zero `python3`). The boot-sweep commit path at `launcher.sh:1020-1037` keeps its own in-shell `_js_journal_update` / `_js_history_append` writes (rare event, fires only on `intent_flip + operator-declared rollback_safe:false`); the bash walk cost is acceptable there because the boot-sweep rarely mutates and never during a hot promote. **Do NOT** add a python3 dependency to launcher.sh in P1. Document this protocol boundary in BOTH `lib.sh` (helper contract) and `launcher.sh` (existing comment at `:1009-1019` is the right anchor — extend it).

**Invocation in promote.sh**: replace inline `journal_history_append "halt" "..."` calls with `python3 scripts/upgrade/journal_splice.py "$JOURNAL_PATH" append-history halt "..."` (the script must export `JOURNAL_PATH` once at top — it is already available via `journal_path` at `lib.sh:537-545`; thread an env var or capture into shell). Add a single `_splice_journal <op> ...` wrapper in `lib.sh` that resolves the path, prints a `_log` line on failure, and returns the helper's exit code — same ergonomics as today's `journal_history_append` so the 19 call sites change minimally.

### Failure modes + fail-closed behavior

| Failure mode | Behavior | Fail-closed? |
|---|---|---|
| `python3` not in PATH | helper exits 78; wrapper refuses the entire promote with `_warn "python3 missing — refusing promote (journal helper unavailable)"`, `lock_release`, exit 78 | YES — never silent bash fallback (option C is rejected for exactly this reason) |
| Journal under live install (`~/agents-ensemble` resolved via expanduser+resolve+samefile) | helper exits 78 | YES — same precedent as `ledger_check.py:46-55` |
| Torn journal (JSON parse failure) | helper exits 78; operator must intervene | YES — refuses to splice a malformed state |
| Schema drift (target field missing) | helper exits 78 with detail | YES — matches `_json_has` refusal at `lib.sh:612` |
| Atomic rename fails (EACCES / ENOSPC) | helper exits 1; wrapper treats as halt | YES — `journal_write` at `lib.sh:587-603` does the same |
| Race with another writer (helper misuse — caller forgot lock) | NOT POSSIBLE — helper does not check the lock; `promote.sh` always holds it. Document this contract in helper docstring. | N/A (caller contract) |

### Interactions

- **(b) Pending-op liveness** — the heartbeat writer hooks into the same lock_heartbeat call sites that the helper migrates from bash to python. Co-design: P1 helper exposes `set-field last_heartbeat <int>` via the `set-field` op; P1 writes that too at the same sites.
- **(d) Total ceremony budget** — the helper makes per-event journal cost trivial, which lets `(d)` be enforced without paying quadratic costs on every retry.
- **(g) Rollback target fix** — `set-previous` op lands in helper, called from `promote.sh:438` (commit block); immediacy assertion reads `journal_path + get-current` which stays bash.

---

## (b) Pending-op liveness

### Problem
`PendingOp.owner_heartbeat_at` is declared; today's `journal_heartbeat` already refreshes `in_flight.last_heartbeat` (since 2026-09-28 commit `31cd4fb59`) (`daemon/tools/upgrade_journal.py:741` — declared field; `daemon/services/upgrade_journal_sweep.py:1590` reads `op.owner_heartbeat_at or op.armed_at`; zero writers exist in python OR shell). The sweep is misclassification-prone in the `armed → flip` window: today a slow bash walk can keep that window open for ~37 min (E6 timing digest); after (a) shrinks to ~1–2 min. Even at 1–2 min, the sweep's `EXECUTOR_STALENESS_WINDOW_S=900` (`daemon/services/upgrade_journal_sweep.py:82`) is too generous — the field was invented for this case but never wired. Shell already has a heartbeat (`lock_heartbeat` at `lib.sh:1186-1197`, called at `promote.sh:177 :301 :342 :427 :503` and inside `_probe` at `:2526 :2532` and `gate_soak` at `:4249`) but it only writes a file inside the lock dir, not the journal — bridging those is the gap.

### Options considered

1. **A — Status quo (writer exists for `in_flight.last_heartbeat` since 2026-09-28 commit 31cd4fb59, but slow bash walk makes the cadence a fiction; sweep's `PendingOp.owner_heartbeat_at` reader points at a field with zero writers).** Sweep reads `owner_heartbeat_at or armed_at`; `owner_heartbeat_at` is always None → sweep behaves as if armed-time is the only signal, AND the `in_flight.last_heartbeat` stamp is rarely refreshed because the bash walk can't keep up with 30 s cadence. The 37-min window corrupts every promote that depends on the sweep.
2. **B — REPLACE `journal_heartbeat`'s bash walk with the (a) helper's `set-in-flight-field last_heartbeat <epoch>` op (C-3 + C-2 corrections).** Single touch surface; with the splice path replacing the bash walk, the existing 30 s cadence (`LOCK_HEARTBEAT_S=30`, `lib.sh:108`) is actually achievable. Sweep now reads fresh evidence via the existing `in_flight.last_heartbeat` accessor (a `set-field last_heartbeat` at the top level would be **invisible** to the sweep — C-2 explicitly requires the NESTED write via `set-in-flight-field`). **The change MUST REPLACE the bash walk, not augment it** — augment keeps the slow walk on the 30 s loop and the perf premise dies (this is the C-3 correction). Defense in depth: sweep ALSO reads systemctl/cgroup liveness for executor-kind ops (already partially at `daemon/services/upgrade_journal_sweep.py:1579-1597` via `os.kill(pid, 0)` + evidence age; extend to cgroup leaf). The `PendingOp.owner_heartbeat_at` writer on the daemon-side is a separate, future op (the helper MUST NOT touch `pending_op` per the (a) field-ownership matrix).
3. **C — Sweep-only fix: read `journal.in_flight.last_heartbeat` (which `journal_open_txn` already stamps at `lib.sh:709` via `journal_open_txn`) plus `os.kill(pid, 0)`.** No new writer — just read what's there. Misses the case where owner_pid is alive (kill -0 = 0) but the shell-side bash walk is wedged for 4.6 min; sweep still misclassifies.

### RECOMMENDATION: **B** (with a C-style defensive read)

### Rationale

- **B** is the structural fix: it REPLACES the slow bash walk (C-3) with the splice path (C-2 corrected by using the NESTED op `set-in-flight-field last_heartbeat`, not `set-field last_heartbeat` which would be invisible to the sweep's `in_flight.last_heartbeat` accessor). With the splice path on the 30 s cadence, the freshness signal is real.
- The existing `lock_heartbeat` at `lib.sh:1186-1197` and the existing `journal_open_txn` stamp at `lib.sh:709` already establish the cadence and the field. The P2 fix REPLACES the bash `journal_heartbeat` walk at `lib.sh:1196` with a single `_splice_journal set-in-flight-field last_heartbeat "$(_now_epoch)"` call (spliced in the same location, not augmented) — that's the smallest payload that closes the gap.
- **C-only** under-delivers because the misclassification we care about (G5 "live executor misclassified") is the wedge where the executor is alive but slow — `kill -0` alone reads "alive," and we have no signal of "still actually making progress."
- The launcher.sh side (`launcher.sh:908-915` — age > SWEEP_STALE_S=600 defaults the owner to presumed-dead REGARDLESS of heartbeat unless pipeline-lock held; the D5 defer carve-out at `:913-915 :925-936`) is also a write site for the in_flight heartbeat in the boot-sweep case. Boot-sweep writes are rare (`launcher.sh:1020-1037` only fires on `intent_flip + operator-declared rollback_safe:false`); an additional rare bash-side write is acceptable in launcher.sh (does NOT need to migrate to the helper).
- Sweep reads THREE signals: (1) `journal.in_flight.last_heartbeat` freshness, (2) `os.kill(owner_pid, 0)`, (3) cgroup leaf for executor-kind ops. All three must individually fail-closed: heartbeat stale AND pid dead → reclaim; heartbeat fresh OR pid alive → leave alone.

### Concrete contract

**In `lib.sh`** (pseudo-patch — phase-plan worker fills in):

- Extend `lock_heartbeat` (`:1186-1197`) to REPLACE (not augment) the bash `journal_heartbeat` walk at `:1196` with a single `_splice_journal set-in-flight-field last_heartbeat "$(_now_epoch)"` call. The C-3 correction requires REPLACE — keeping the bash walk on the 30 s hot loop is the perf-killing direction. Owner-guarded (already enforced inside `lock_heartbeat`; the splice write inherits the owner guard because the helper writes the field the caller asked for — no special-casing needed). The C-2 correction requires the **NESTED** `set-in-flight-field` op so the sweep's `in_flight.last_heartbeat` accessor actually sees the stamp.
- The `PendingOp.owner_heartbeat_at` writer on `pending_op` is a separate, future op (the helper MUST NOT touch `pending_op` per the (a) field-ownership matrix; the shell lane writes `last_heartbeat` in `in_flight`, and the daemon lane reads from that).

**In `daemon/services/upgrade_journal_sweep.py`** (P2 hardening, not P1):

- `:1590` reads `op.owner_heartbeat_at or op.armed_at` → change to THREE-signal: `(journal_owner_heartbeat_fresh) OR (os.kill(owner_pid, 0) == 0 AND cgroup_leaf matches expected systemd unit)` → leave alone. Truncate `or armed_at` to "fallback only if neither signal is available."
- Add a `journal_owner_heartbeat_fresh` accessor: re-read state.json, check `in_flight.last_heartbeat` + `now - last_heartbeat ≤ OWNER_HEARTBEAT_FRESH_S = 60`. Fail-closed: unreadable heartbeat → treat as stale.

**In `launcher.sh`** (no change in P1):

- Document the protocol boundary in the existing comment at `:1009-1019`: "previous is NOT mutated here; promote.sh owns previous writes via the journal splice helper at commit. This boot-sweep commit_and_continue is the ONLY path that may update previous from boot-sweep land (P1 g-fix); see promote.sh:438."

**Cadence table** (from `lib.sh:108 :113` and the verified call-site scan):

| Heartbeat | Cadence | Writer | Reader |
|---|---|---|---|
| File heartbeat (lock dir) | 30 s (LOCK_HEARTBEAT_S) | `lock_heartbeat` shell | `journal_read` sweep, launcher acquire |
| `in_flight.last_heartbeat` | 30 s nominal — **achieved today only in theory** because `journal_heartbeat` does a bash brace-walk on the 87 KB state.json (~280 s/call) before each update write. P2 fix: REPLACE the bash walk at `lib.sh:1196` with a splice-based write via `_splice_journal set-in-flight-field last_heartbeat "$(_now_epoch)"` (decisions (b) C-3 + C-2 corrections). | `lock_heartbeat` shell via splice helper | daemon sweep, future P2 |
| `pending_op.owner_heartbeat_at` | NOT WRITTEN TODAY (declared at `daemon/tools/upgrade_journal.py:741`; zero writers anywhere in the code). Future P2 hardening pass lives with the python reconciler since the helper MUST NOT touch `pending_op`. | python daemon-side (future P2 op) | daemon sweep `:1590` (`owner_heartbeat_at or armed_at`) |
| systemd cgroup leaf | continuous (no refresh) | systemd | sweep `kill(pid, 0)` + `/proc/<pid>/cgroup` leaf match |

### Failure modes + fail-closed behavior

| Failure mode | Behavior | Fail-closed? |
|---|---|---|
| Helper missing (python3 absent) | `lock_heartbeat` extended write fails silently? NO — wrapper in lib.sh returns non-zero, `lock_heartbeat` itself fails loudly (return 1). Caller already routes the failure to `lock_acquire` paths (`:165` refusal). | YES (caller-side) |
| Sweep reads stale heartbeat | Default to stale → reclaim (existing behavior; net tighter than today) | YES |
| Sweep cannot read owner_pid (gone) | `kill(pid, 0)` returns ESRCH → fall through to heartbeat check. Both stale → reclaim. | YES |
| Sweep cannot read cgroup | Ignore cgroup signal; rely on heartbeat + pid. Three-of-three must agree to leave alone. | YES |

### Interactions

- **(a) Journal splice** — extends helper to write `last_heartbeat`; same code path.
- **(d) Total ceremony budget** — heartbeat is the cheapest amortization of budget; armed→flip window collapses from ~37 min to <2 min.
- **(g) Rollback target fix** — independent; do not couple.

---

## (c) Adaptive soak default

### Problem
`gate_soak` at `lib.sh:4240-4266` always uses `ENSEMBLE_PROMOTE_SOAK_S` (default 300s, hard cap 900 at `promote.sh:156`) with fixed `sleep 30` between probes. The "shorter soak by default" goal (G2) requires the gate to terminate as soon as the daemon is convincingly stable, not at fixed time. The hard constraints require: (1) version-drift detection stays (`:4258` — `gate_version` mid-soak drift fails soak); (2) anti-flapping cap 900 stays; (3) anti-flapping block `lib.sh:102-115` stays; (4) all current fail-closed paths inside `gate_soak` stay (livez/readyz non-2xx → red; version drift → red).

### Options considered

1. **A — Status quo.** `ENSEMBLE_PROMOTE_SOAK_S=300` default; 300s flat wait. Why move: G2 mandates speed.
2. **B — N-consecutive-green only.** Skip time, gate exits as soon as N=3 green probes (livez+readyz+version). Three greens at 15s = exit at 45s. Why not: under transient flapping (e.g., the v0.20.0 1.5s readyz transient window), we may miss a real red by exiting early on three greens that happened to land on transient windows.
3. **C — Time-budget envelope + minimum.** Adaptive cap `<= 120s`; floor `>= 60s`; 3 consecutive green (15s cadence) as the EARLIEST exit; floor holds the window long enough to clear one transient blip; cap protects against genuine instability that flutters through green thresholds.
4. **D — Like C but tiered; opt-out via `ENSEMBLE_SOAK_CONSERVATIVE=1`.** Same as C with the explicit knob so risk-averse operators can flip back to 300s without hand-editing config.

### RECOMMENDATION: **D**

### Rationale

- **D** satisfies G2 (60–120s typical vs 300s today) while preserving the hard constraints.
- The 1.5s transient window in the v0.20.0 boot storm — `await readyz-green at 05:05:27Z` raced a transient — is solved by requiring 3 consecutive greens at 15 s cadence. A single transient green cannot satisfy 3-in-a-row. Source: digest (E3/E5).
- The 60 s floor gives one full 15 s probe cycle past the typical boot-storm window, plus headroom for one curl timeout (5 s) plus retry. Below 60 s the gate is brittle against future boot storms.
- Opt-out via `ENSEMBLE_SOAK_CONSERVATIVE=1` is clean (no fork of `gate_soak`); the knob is read once at the top of the function.
- **T-G2a math at 15 s cadence:** `consecutive_green=3 → exit at 3rd green = 45 s + first-probe ~5 s ≈ 50 s typical`; `consecutive_green` cap-reach at 120 s yields `120 / 15 = 8 iterations`, sufficient to clear transient storms while fitting the floor. The 15 s cadence (vs the plan's earlier 30 s) is the architect-corrected number — see C-1 math in `architecture-recommendation.md` §2.3 and `phase3-plan.md` D-P3.1.

### Concrete contract (concrete — phase-plan worker builds from this)

**Default mode (NEW behavior — replaces the loop body at `lib.sh:4240-4266`; C-1 cap semantics corrected):**

- Floor: `ADAPTIVE_SOAK_FLOOR_S=60` (NEW constant). Floor is the earliest time an exit-0 may fire; not a separate phase.
- Adaptive cap: `ADAPTIVE_SOAK_CAP_S=120` (NEW constant).
- Consecutive-green threshold: `ADAPTIVE_SOAK_CONSECUTIVE=3` (NEW constant).
- Probe cadence: `sleep 15` (was 30; tightened per the leader's Q6a ruling — 15 s × 3 = 45 s minimum, well inside the 60 s floor with 15 s headroom for one transient retry).
- **Termination logic — C-1 corrected:**
  - **Each loop iteration probes livez+readyz+version.** If all green AND `consecutive_green >= 3` → **exit 0 SUCCESS** (early exit; floor not required because 3 greens at 15 s cadence can only land at ≥45 s elapsed anyway).
  - If `now >= adaptive_cap` AND `consecutive_green >= 3` → **exit 0 SUCCESS**.
  - **If `now >= adaptive_cap` AND `consecutive_green < 3` → exit 1 → auto-rollback** (C-1: under sustained DB churn — the recurring 2026-10-10 incident class; certain once the `feature/checkpoint-conn-resilience`'s checkpoint_saver 503 probe merges — the gate would otherwise reach cap with zero greens and silently commit, vs today's behavior of instant red → auto-rollback).
  - **Red at any iteration → exit 1 FAILURE immediately** (fail-fast; do NOT ride out — ride-out is anti-flapping direction reversed).
  - **Floor holds for one transient retry without flipping exit decision** (this is what gives the floor meaning; the C-1 math is `consecutive_green ≥ 3` ONLY counts a green if at least 3 probes were green across iterations, regardless of when they land — concrete: a transient storm can delay but not bypass the consecutive-green requirement).
- Mid-soak version drift preserved (`lib.sh:4258` unchanged).
- Hard ceiling: `now >= ensemble_promote_soak_s_hard_cap=900` is enforced at `promote.sh:156` (already present); the adaptive loop respects this floor.

**Conservative mode (opt-in):**

- Set `ENSEMBLE_SOAK_CONSERVATIVE=1` → gate reverts to current behavior (no early exit, 30 s cadence, soak_s=300 unless explicitly overridden). A single branch in the gate is enough.
- Existing `ENSEMBLE_PROMOTE_SOAK_S` env var remains the user-override knob in BOTH modes.

**Method to compute "default soak_s" for the log line**: log "soak ${adaptive_cap}s (early-exit on ${adaptive_consecutive} consecutive green probes @ ${adaptive_cadence}s cadence, floor ${adaptive_floor}s)" when adaptive is in effect. When conservative, log the current line unchanged.

**Backwards compat**: any caller passing `ENSEMBLE_PROMOTE_SOAK_S=300` continues to get the current 300s behavior (existing override path); only the DEFAULT changes.

**Failure modes + fail-closed**:

| Failure mode | Behavior | Fail-closed? |
|---|---|---|
| `_probe_once` curl exits 7/22/28 (transient) | bounded retry inside the probe (see (e)); if still transient after 3 retries → counted as "no green" (do not increment consecutive); eventually fail-closed | YES |
| All three probes return green in <3 consecutive iterations | gate waits for the floor; auto-rollback route taken if floor+cap elapses without 3 consec | YES |
| `ENSEMBLE_PROMOTE_SOAK_S>900` | already capped at `:156` (`SOAK_S=900`) | YES (preserved) |
| Adaptive mode disabled (conservative) | existing behavior unchanged | YES (no regression) |

### Interactions

- **(d) Total ceremony budget** — soak is the only phase that "wants" to be variable; adaptive soak must fit inside the remaining budget from (d). The gate computes its effective cap as `min(adaptive_cap, ceremony_remaining - 60)`. Cross-decision coupling.
- **(e) Transient handling** — bounded retries inside each probe iteration are a sub-decision of (e); (c) explicitly allows retries to keep "no green" status without failing the gate.
- **(f) Audit-after-soak** — depends on the soak gate being stable; (c) ships first.

---

## (d) Total ceremony budget

### Problem
G1 mandates "TOTAL wall-clock budget honored fail-closed" — the user wants hard wall-clock guarantees for promote.sh end-to-end, with breach routing to existing auto-rollback/halt machinery. Today there is no top-level ceiling; only per-phase env-plumbed budgets (LIVEZ_BUDGET_S=180, READYZ_BUDGET_S=120, `ENSEMBLE_PROMOTE_SOAK_S≤900`). The 3h15m failure arc and the 28–46-min clean-promote journal overhead are the motivating data points.

### Options considered

1. **A — Status quo.** Per-phase budgets only; no top-level ceiling. G1 is unmet.
2. **B — Hard top-level wall-clock ceiling, breach routes to halt.** Set `PROMOTE_TOTAL_DEADLINE_S=900` (15 min). Each phase sets a `phase_end=$(now + phase_budget)`; on entry to the next phase, check elapsed; if breach → journal `halt` + exit 78 + lock_release. Reroute: the breach does NOT auto-rollback (current is fine — we just timed out); it halts for human. The fail-closed behavior is: refuse the entire promote when over budget, with full audit trail.
3. **C — Two-tier budget (warning + breach).** Soft warning at 80% of budget, hard halt at 100%. Adds complexity without operational benefit.
4. **D — Top-level ceiling + adaptive soak cap derived from remaining.** Adaptive soak (c) shrinks/grows against the budget remaining; if budget tightens to <60s, soak uses 0s with full audit trail (NOT promoted-without-soak — that's a separate opt-in knob).

### RECOMMENDATION: **B + D coupling** — single top-level ceiling, adaptive soak fits inside the remainder.

### Rationale

- G1 hard constraint: "TOTAL wall-clock budget honored fail-closed (breach routes to existing auto-rollback/halt machinery, never skips safety)." A halt is the existing route; reusing it preserves the user's mental model and the runbook commands.
- 15 min is achievable per the E6 timing model — **C-10a arithmetic (single reconciled model):**

  | Phase | Budget (s) | Notes |
  |---|---|---|
  | Preflight + integrity | 45 | measured ~35 s today |
  | Stop ensemble | 240 (worst 755) | `_derive_parent_stop_budget` at `lib.sh:2756`; USE TODAY'S DERIVED BUDGET — single source |
  | Atomic flip | 30 | (1 s typical) |
  | Restart / handback | 90 | `HANDBACK_START_BUDGET_S=60` at `lib.sh:95` + 30 s slack |
  | Livez | 180 | `LIVEZ_BUDGET_S=180` at `lib.sh:78` |
  | Readyz | 120 | `READYZ_BUDGET_S=120` at `lib.sh:79` |
  | Version | 30 | (5 s typical) |
  | Adaptive soak | 60–120 (adaptive per (c)) | cap shrinks against remaining budget |
  | Commit | 30 | (2–4 s today) |

  - **Nominal all-budgets-max sum** (every budget at its ceiling, stop at 755 s worst): 45 + 755 + 30 + 90 + 180 + 120 + 30 + 120 + 30 = **1400 s**. This is NOT the operative case for a typical run.
  - **Worst-case non-stop sum** (every non-stop budget at its ceiling, stop at the derived default `eff=225 s` at `lib.sh:2756`): 45 + 225 + 30 + 90 + 180 + 120 + 30 + 120 + 30 = **870 s**.
  - **Tight nominal** (typical observed non-journal ≈ 221 s + journal < 1 s post-splice + soak 60 s): ≈ **282 s ≈ 4.7 min**.
  - **All-budgets-max ceiling** (stop worst 755 + everything max) = **1400 s** — this is the abort-path ceiling, breached → halt.
  - **Hard cap** = `PROMOTE_TOTAL_DEADLINE_S=900` at `promote.sh:156`. The 870 s non-stop-worst fits inside the 900 s cap with **30 s slack**, satisfying the leader's Q6a ruling: "worst-case 870s + 30s slack; the ≤600s claim is RE-SCOPED to the TYPICAL path; worst-case 870s stays inside the 900s fail-closed ceiling."
  - **Soak squeeze at boundary**: dynamic-soak `min(120, remaining - 60)` at the 735 s boundary yields 105 s of soak (not 120 s). The math is: at total elapsed 45+225+30+90+180+120+30+15 = 735 s, `ceremony_remaining_s = 900 - 735 = 165 s`; adaptive cap = `min(120, 165-60) = 105 s`. Soak fits.
  - **Stop-phase breach to halt**: a deadline halt AFTER `stop-ensemble` but BEFORE `flip` leaves the daemon DOWN (C-10b — see `Failure modes` table below and the runbook first-recovery-step).

### Concrete contract

**Phase budgets table** (single source of truth at top of `promote.sh`):

| Phase | Budget (seconds) | Cite (today) | Notes |
|---|---|---|---|
| Preflight + integrity | 45 | (no current budget; measured ~35s) | tightens pre-rollback window |
| Stop ensemble | 240 | `_derive_parent_stop_budget` at `lib.sh:2756` (default eff 225s, worst 755s) | USE TODAY'S DERIVED BUDGET, not a new constant — single source |
| Atomic flip | 30 | unwritten today (1s typical) | generous for slow disk |
| Restart / handback | 90 | `HANDBACK_START_BUDGET_S=60` at `lib.sh:95` + 30s slack | tight; needs assertion via `_run_bounded` |
| Livez | 180 | `LIVEZ_BUDGET_S=180` at `lib.sh:78` | preserve |
| Readyz | 120 | `READYZ_BUDGET_S=120` at `lib.sh:79` | preserve |
| Version | 30 | (unwritten; 5s typical) | generous |
| Adaptive soak | 60-120 (adaptive) | (c) | cap shrinks against remaining budget |
| Commit | 30 | (unwritten; measured 2-4s today) | generous |
| **TOTAL ceiling** | **900** | (NO analog today) | **NEW — top-level cap** |

**Implementation shape** (concrete — phase-plan worker fills in):

- At the top of `promote.sh`, after the signal trap and preflight-journal-txn-open: `promote_deadline=$(( $(_now_epoch) + PROMOTE_TOTAL_DEADLINE_S ))`.
- Each phase boundary inside `promote.sh` calls `_enforce_deadline promote_deadline <phase-name>` (NEW helper in `lib.sh`). The helper: `now=$(_now_epoch); if [ "$now" -gt "$deadline" ]; then journal_history_append halt "deadline-breach-<phase> (elapsed $((now - start))s > $deadline_s)" || true; lock_release; exit 78; fi`. The reason token `deadline-breach-<phase>` is C-10c (runbook-greppable; distinguishes deadline halts from gate_fail halts).
- Halt detail (C-10b): the halt journal event MUST carry a `daemon-state` field reflecting the daemon's state at halt time (`stopped` if past stop ensemble; `handed-back` if past restart; `live` if before stop). Post-stop breach leaves the daemon DOWN — the runbook first-recovery-step is to verify/restart current BEFORE re-promote (no skip-rollback path).
- The breach path is the standard halt: journal event, lock release, exit 78 (the same channel as gate-fail halts at `promote.sh:459-496`). The breach does NOT trigger auto-rollback because the daemon may not even be live (post-stop breach). The user reads the halt event, decides, and follows the runbook first-recovery-step.

**Adaptive soak coupling** (concrete — phase-plan worker fills in):

- `gate_soak` receives `ceremony_remaining_s` as a new positional arg (or reads a `promote_deadline` env var threaded through).
- Adaptive cap is `min(ADAPTIVE_SOAK_CAP_S, ceremony_remaining_s - 60)` (the "-60" preserves a 60s audit tail). If `ceremony_remaining_s < 60`, soak runs with `0s` and logs "ceremony_remaining=$X — soak skipped (drill knob); journal event records the deferral."

**Failure modes + fail-closed**:

| Failure mode | Behavior | Fail-closed? |
|---|---|---|
| Bash `_now_epoch` returns wall-clock time wrong (operator set TIME manually mid-ceremony) | enforcement uses `_now_epoch` which is shell-side `date +%s`; any tampering breaks our deadline but not safety — gate stays the source of truth | N/A (operator action) |
| Pre-stop phase already past deadline (e.g., stop took 250s on a slow VM) | `_enforce_deadline` at restart_via_launcher fires → halt | YES |
| Adaptive soak declares 0s due to remaining-budget starvation | journal event records the deferral; `commit` proceeds; gate stays the safety layer (if adaptive soak green-light then commit proceeds; if not, gate fail → auto-rollback route) | YES |

### Interactions

- **(c) Adaptive soak** — single hard coupling. (d) supplies `ceremony_remaining_s`; (c) consumes it.
- **(a) Journal splice** — without (a), (d) is meaningless because the journal itself is the longest phase; (a) is a prerequisite.
- **(e) Transient handling** — bounded retries inside livez/readyz/soak probes count against the per-phase budget, not the top-level ceiling (the ceiling is meant for catastrophic phases like stop).

---

## (e) Transient / boot handling

### Problem
The v0.20.0 boot storm incident (`05:05:27Z`): env-poison purge + 3 concurrent turns + 1.0s sentinel budget caused pool saturation → livez red at soak-0s; readyz-green at `:05:27Z` was a ~1.5s transient window the gate raced. The "livez → readyz" telemetry gap (readyz has busy-amnesty at `daemon/api.py` inflight_turns>0 → COMMIT; soak/livez do NOT — the asymmetry gap) means the current behavior is to fail any non-2xx immediately, including transients like curl exit 7 (connection refused) during pool re-init. The 120s-onward-1.5s-window failure mode is what the user wants closed: N-consecutive-green must be robust to transient greens.

### Options considered

1. **A — Status quo.** `_probe_once` at `lib.sh:2539-2541` is `curl -fsS --max-time 5 ... 2>/dev/null`; any non-2xx = silent failure; `gate_soak` at `:4250-4258` treats silent failure as "red." Transient curl exit 7/22/28 = instant failure.
2. **B — Classify transients at the probe level; retry-with-backoff inside per-phase budget.** New `_probe_once_classified` returns a 3-state: green / red / transient. The gate counts greens only; transients do not advance the "consecutive greens" counter (do not break it either — same "no green" status). Cap retries at 3 (1s/2s/4s backoff) inside the per-phase budget.
3. **C — Boot-window grace entirely.** After `restart_via_launcher` succeeds, suppress all red-probes for 60s, count anything as soft-pass. Loses fail-closed semantics during that 60s.
4. **D — Combination of B (classification + retries) and a bounded grace period.** Grace auto-extends ONLY while transient probes are encountered (so a wedged daemon doesn't get 60s of grace); once N consecutive non-transient greens are seen, grace ends. The wedged case falls back to existing red-fail.

### RECOMMENDATION: **D**

### Rationale

- The 1.5s transient window is exactly what bounded retries handle: 1s backoff first, retry finds green. No boot-window magic needed for THAT case.
- For a true pool-saturation (every probe transient for 5–10s), the grace kicks in: don't fail fast on red, give the daemon time to settle. The grace auto-ends on 3 non-transient greens (consistent with adaptive soak's exit condition).
- The asymmetry gap (readyz has busy-amnesty; livez/soak don't) is closed by extending the same `inflight_turns > 0` semantic to livez+soak via a SHARED helper. The bound is `inflight_turns ≤ 5` so a wedged daemon (inflight_turns=0 but pool saturated, e.g., heartbeat-frozen) does NOT get amnesty.
- "Wedged daemon" is a real case (per `daemon/api.py:415` "Stop-frozen heartbeat amnesty"); the bound `≤ 5` keeps amnesties genuinely narrow.

### Concrete contract

**Probe classification** (NEW — extends `_probe_once`; **C-8a fixes the curl invocation**):

```
# WRONG (body-sniff after -f suppresses body on exit 22):
#   curl -fsS --max-time 5 <url> 2>/dev/null

# RIGHT (status-code via -w, body to /tmp, classify on the code):
curl -sS -o <tmp-body> -w '%{http_code}' --max-time 5 <url>
# Then classify on the integer exit:
#   exit 0 + http_code "200"/"2xx" → green
#   http_code "503"              → transient (server alive, not ready)
#   http_code "5xx" (other)      → red
#   http_code "4xx"              → red
#   curl exit 7                  → transient (connection refused)
#   curl exit 28                 → transient (timeout)
#   curl exit 22                 → red (with whatever body in <tmp-body>)
#   curl exit anything-else      → red
```

The C-8a correction is load-bearing: `curl -fsS` suppresses the body on exit 22, which means a body-sniffing classifier sees empty-stdout and silently classifies EVERY HTTP error as red — making the 503-transient path (which is exactly what G4 closes) **never fire**. The status-code-via-`-w` invocation is the only way the transient classifier can do its job.

Backoff schedule (per-probe, inside per-phase budget): 3 retries max, `1s 2s 4s`. After 3 retries → if last classification = transient → count as "no green" (do not fail); if last classification = red → fail the phase as today.

**Boot-window grace** (NEW):

- New constant: `BOOT_GRACE_S=60`.
- New env var: `PROMOTE_BOOT_GRACE_S` (operator override; same env-plumbed style as `LIVEZ_BUDGET_S`).
- Grace period starts on `restart_via_launcher` returning 0; ends on earliest of: (a) grace_s elapsed, (b) 3 consecutive non-transient greens seen on livez. During grace: probe classification "transient" → silently ignored (not counted as red, not counted as green); "green" → counted; "red" → counted as red (don't blanket-suppress — only suppress transients).

**Busy-amnesty decision** (extends `daemon/api.py` readyz semantic; **C-8b RESOLVES Q5 — NO new file, NO new daemon writer**):

- Currently: `inflight_turns > 0` → COMMIT on readyz (only), via the EXISTING `daemon/api.py:198/222 detail.inflight_turns` field on the readyz response.
- Extend: livez + soak read the **EXISTING** `detail.inflight_turns` from the readyz endpoint via a SHARED helper `_read_inflight_turns_bounded` (NEW — shell-side). Bound: `BUSY_INFLIGHT_BOUND=5` (NEW constant). Hard cap. Anything > 5 OR 0 → no amnesty; standard probe semantics.
- **Q5 resolution (C-8b):** do NOT introduce a `/run/ensemble/inflight_turns` file (race-prone, new surface); do NOT introduce a daemon-side counter writer; do NOT reuse the predicate `has_instance_busy` (a different predicate: PENDING+RUNNING+PAUSED tasks vs readiness turns). The readyz `detail.inflight_turns` is already the source of truth. The detail field exists at `readiness.py:198/222` — extending the probe-side reader to consume it is a one-line delta on the shell side.
- Failure mode of the bound: a wedged daemon (heartbeat-frozen) reports `inflight_turns=0` → no amnesty → standard probes → fail-closed. Source: digest (E3/E5) and `daemon/api.py:415` (Stop-frozen heartbeat amnesty already present, separate semantic).

**Failure modes + fail-closed**:

| Failure mode | Behavior | Fail-closed? |
|---|---|---|
| `_probe_once` classification itself buggy (e.g., 502 mis-classified as green) | implementation MUST unit-test the curl exit-code table; pin by test pack. Otherwise: | YES via test, defensive at runtime |
| Grace period exceeds 60s | hard cap at `BOOT_GRACE_S` | YES |
| Grace auto-extends on green-seen, then daemon re-reds | 3 non-transient greens AND then a red → red-counted, gate can still fail | YES |
| `inflight_turns` file unreadable | treat as 0 (fail-closed: no amnesty) | YES |
| `_read_inflight_turns_bounded` hits BUSY_INFLIGHT_BOUND=5 + 1 real inflight | refuse amnesty, fail-closed | YES |

### Interactions

- **(c) Adaptive soak** — soak reads from these probes; the N-consecutive-green counter at (c) tolerates the bounded retries here.
- **(d) Total ceremony budget** — bounded retries count against the per-phase budget; grace extends livez budget but NOT the total ceremony ceiling (grace is sub-budget).
- **(f) Audit-after-soak** — independent.

---

## (f) Audit-after-soak sequencing

### Problem
The v0.20.0 boot storm was caused (in part) by `InstanceManager.__init__` at `daemon/manager.py:843` running the env-poison wipe **synchronously** pre-pool, gated by `config.queue.discard_on_startup` (`env QUEUE_DISCARD_ON_STARTUP`). The wipe captures `boot_epoch` at `:859-873` and again post-init at `:424-426` (ordering load-bearing for `queue_freshness` readiness + `StaleTaskRecovery` + auto-continue). The task-table-intactness assumption at `daemon/repositories/message_queue/repository.py:1144-1148` is foundational (wipe assumes task table hasn't been truncated). The wipe is IDEMPOTENT by predicate (2nd boot deletes 0). The audit log at `:1136-1143` is mandatory. The challenge: defer the DELETE off the boot critical path while preserving EVERY ordering guarantee.

### Options considered

1. **A — Status quo.** Wipe is synchronous pre-pool; can't be moved without rethinking queue_freshness readiness.
2. **B — Split into CAPTURE_PHASE (sync) + DELETE_PHASE (post-readyz-green async background task).** Capture doomed-id list synchronously, write audit log synchronously; defer the actual row DELETE to a background task that runs only AFTER `/readyz` first returns green AND +1s OR +5s. CAPTURE_PHASE keeps boot_epoch ordering. DELETE_PHASE keeps the idempotency assumption (next boot sees no doomed ids to wipe because prev DELETE finished OR a new doomed-id set was captured).
3. **C — Flip config default.** Make `QUEUE_DISCARD_ON_STARTUP=false` the default; operators opt in. Loses the "operator's runtime evidence" capture that the wipe audit provides.
4. **D — Defer WIPE entirely to a separate process / cron-like scheduler.** Adds a new daemon-supervisor process; out of scope.

### RECOMMENDATION: **B**

### Rationale

- (B) defers ONLY the slow part (row DELETE) — the capture and audit remain in-band so the ordering guarantee at `daemon/manager.py:859-873 :424-426` stays tight.
- Order of operations: pool initializes → capture doomed ids + audit log → `INIT_DONE` event → readyz returns green AFTER pool is up (existing semantics) → background DELETE fires.
- Crash-safety: if daemon crashes between boot and deferred DELETE, next boot re-runs the WIPE — doomed ids cap is bounded by max table size (10k entries per `daemon/repositories/message_queue/repository.py:1144-1148` comment). The 2nd boot's CAPTURE_PHASE re-logs audit, sees the doomed-id set is the original (predicate stable), DELETE re-runs.
- (C) is operationally attractive but loses the auto-recover semantics. (D) is out of scope.

### Concrete contract

**Refactor `MessageQueueRepository.clear_all`** (NEW shape):

- `clear_all` becomes a two-stage coordinator: calls `capture_doomed_ids_and_audit` (synchronous, in-band) then schedules `delete_doomed_ids_async` (background, non-blocking).
- `capture_doomed_ids_and_audit` (NEW public method): reads doomed ids, audits at `:1136-1143`, returns the doomed-id list. Stays pre-pool at `daemon/manager.py:843`.
- `delete_doomed_ids_async` (NEW public method): runs in a daemon-internal asyncio task (use the existing pool-task pattern at `daemon/manager.py:407-413` lifespan startup); takes the doomed-id list as captured; DELETE in chunks; logs completion. Triggered by an event hook on `lifespan startup readyz-green` (NEW event hook) OR via sleep-and-poll fallback if event hook isn't ready.
- Per chunk size: 1000 rows (small; preserves existing DELETE-where-in-predicate pattern). Bounded by existing pool.
- Idempotency: doomed-id set is derived from the SAME predicate each boot; the next boot's CAPTURE_PHASE will re-derive the same set if DELETE didn't run. Bounded divergence is "set of doomed ids" — bounded by max rows per delete pass.

**Preserved semantics**:

- `boot_epoch` ordering (capture pre-wipe :859-873 + again post-init :424-426) preserved verbatim.
- `queue_freshness` readiness uses post-init boot_epoch; unchanged.
- `StaleTaskRecovery` runs against the post-capture, pre-delete state — slightly different window than today (today it's pre-delete, after the fix it's "between capture and delete"), but the wipe assumption that task table is intact is preserved.
- Audit log: CAPTURE_PHASE logs the doomed-id capture; DELETE_PHASE logs completion.
- Idempotency: 2nd boot finds 0 doomed ids (DELETE finished) OR the same doomed-id set (DELETE didn't finish) — both safe.
- Config gate `QUEUE_DISCARD_ON_STARTUP` preserved.

**Boot-path side-effects**:

- `lifespan startup` at `daemon/api.py:406-412 → :413` now has TWO phases: `await instance_manager.initialize()` (which calls capture) then `await pool.start()` (existing), then `READY` event fires, then background `delete_doomed_ids_async` starts.
- The `INIT_DONE` event hook fires AFTER pool is up; readyz gates on this hook.
- If DELETE hasn't completed by the time we exit the lifespan, a `__del__` cleanup OR an explicit `on_shutdown` hook logs a WARN ("deferred wipe incomplete at shutdown"); the next boot's predicate-based wipe cleans up.

**Failure modes + fail-closed**:

| Failure mode | Behavior | Fail-closed? |
|---|---|---|
| Daemon crashes between boot and DELETE | next boot re-derives doomed ids, DELETE runs, audit log shows two capture entries (P1 test pins this) | YES |
| DELETE blocks pool forever (e.g., lock contention) | DELETE runs in a bounded asyncio task with timeout 5min; on timeout, logs WARN + releases | YES |
| `clear_all` refactor breaks audit log | unit test pins `:1136-1143` audit-line structure | YES |
| Operator sets `QUEUE_DISCARD_ON_STARTUP=false` | whole flow no-ops; CAPTURE_PHASE returns empty list; DELETE no-op | YES |
| Crash BEFORE audit log written (pre-CAPTURE) | next boot starts cleanly; idempotent | YES |

### Interactions

- **(c) Adaptive soak** — soak gate is the consumer of "is readyz green yet?"; with deferred DELETE, readyz greens faster (1s typical vs 5s typical today). Adaptive soak floors/caps remain; net effect is shorter TTI.
- **(d) Total ceremony budget** — deletes shrink the boot phase; (f) reduces `boot_epoch` time-to-readyz by ~4s typical.

---

## (g) Rollback target fix

### Problem
On halt→re-promote sequences (the Oct 9 arc), auto-rollback at `promote.sh:452-453` reads `journal.previous` to determine the rollback target. But `journal.previous` was set by the PREVIOUS run's `journal_set_previous` call (via `lib.sh:2700ish`). If run N-2 halted, run N-1 promoted-and-succeeded-then-halted (set `journal.previous = OLD_CUR = vN-2`), and run N is now failing — `journal.previous` correctly points at vN-2 if run N-1 hit commit; but the auto-rollback target SHOULD be run N-1's displaced version, not run N-2's. Today the logic at `:452-453` is correct because run N-1 set `previous = OLD_CUR = vN-2` AFTER commit; the bug shows up only when (a) run N-1 halted AFTER the flip but BEFORE commit (then `previous` is stale) or (b) boot_sweep_commit_and_continue minted a non-standard previous.

Concretely, per the digest:

- `promote.sh:438` writes `journal.previous = $OLD_CUR` from `journal_set_previous` ($OLD_CUR captured at `:227 :436`).
- `promote.sh:452-453` reads `_json_field "$PREV_JSON" previous` to determine auto-rollback target.
- This is the bug class: in a halt-after-flip-before-commit scenario, `:438` never ran, so `:452-453` reads stale `previous` (the previous run's previous).

### Options considered

1. **A — Read $OLD_CUR / $CUR / this-run displaced version explicitly.** Replace the `journal.previous` read at `:452-453` with `$OLD_CUR` captured at `:227 :436`. Single-source-of-truth: the version THIS run displaced. **Add an immediacy assertion at `:438` (set_previous) that journal current == $VERSION BEFORE the previous-write; if mismatch, halt "immediacy breach."** Also harden the read with `_json_field_quoted` (the precedent at `lib.sh:1355-1364`) — `_json_field` uses bare substring anchor that could match an embedded key.
2. **B — Re-read journal under pipeline lock with ordering constraints.** More complex; not addressing the actual bug (which is that we're reading the wrong field).
3. **C — Capture-displaced-at-intent_flip, store as new field in `in_flight`.** Cleaner: store `displaced_version` in the in_flight object at intent_flip; auto-rollback reads `in_flight.displaced_version`. Requires expanding the in_flight schema; touches the helper (a) too.

### RECOMMENDATION: **A & C-coupling** — `promote.sh:438` writes `journal.previous = $OLD_CUR` from `journal_set_previous` (via the P2 splice `set-previous` op, captured at `:227 :436`), AND `launcher.sh:1020-1037` `boot_sweep_commit_and_continue` writes `previous = in_flight.displaced_version` (captured at `journal_open_txn` time by the bash whole-object writer at `lib.sh:700-712`). The two sources are independent (live lane vs boot-sweep lane) but converge on the FORWARD semantic ("previous = the version displaced by the most recent terminal event").

### Rationale

- The read-site fix (A) is correct for the live promote chain: bug class is "reading the wrong field" — the auto-rollback branch at `:452-453` should read `$OLD_CUR` captured at `:227 :436`, not the journal's stale `previous`.
- The boot-sweep path needs a DIFFERENT shape than the live promote. Originally (option C in the plan) was rejected on the false premise that it required expanding the in_flight schema and touching the P2 helper — that premise was wrong:
  - `in_flight.displaced_version` is a **whole-object bash write**, same shape as the existing `journal_open_txn` at `lib.sh:700-712`. No new helper op, no new schema column, no python change.
  - **C-4 correction**: boot_sweep reads `in_flight.displaced_version` (stamped at `journal_open_txn` time = `journal.current` BEFORE the txn is opened on top of it), NOT `journal.previous`. Under FORWARD semantics `journal.previous` is N-2 (predecessor-of-predecessor) — wrong target for a subsequent manual `rollback.sh --to` after a boot-sweep commit. `in_flight.displaced_version` is captured at `journal_open_txn` and **preserved across the boot_sweep_commit_and_continue's in-place `current` flip**, so it carries N-1 correctly.
  - **Old-format txn fallback**: a pre-change-format `in_flight` (no `displaced_version` field) gets treated as **halt-for-human** — ambiguous state, consistent with the "halt-for-human is BY DESIGN" recovery invariant. Recommends running `dev.sh` fence + manual review.
- All six pointer-algebra chains (fresh success; halt-after-flip→auto-rollback→re-promote [Oct 9 arc]; halt→manual rollback.sh→re-promote; boot_sweep path; double rollback; adopt_stale_txn) **walk clean under the FORWARD semantic.** The immediacy assertion at commit (journal.current == $VERSION else halt 78) is sound defense-in-depth. `_json_field_quoted` precedent already present at HEAD (lib.sh:1355-1364) — no ordering hazard. R-07 avoided: read-time normalize, no blanket refuse.
- The original D-P1.1 rejected the `displaced_version` alternative (option C in the early plan) on the false premise that it required a new helper op. The actual shape is a bash whole-object write inside `journal_open_txn` — same shape as today, no helper, no P2 dependency. The reasoning above shows option C (in this refined form) is the right path for boot_sweep, paired with option A for the live lane.

### Concrete edit sites

| Site | File:Line | Edit | Why |
|---|---|---|---|
| Commit block — set_previous | `promote.sh:438` | ADD: `current=$(_json_field_quoted "$PREV_JSON" '"current":')` BEFORE `journal_set_previous`; `if [ "$current" != "\"$VERSION\"" ]; then halt "immediacy breach at commit: journal current=$current ≠ $VERSION"; exit 78; fi` | Immediacy assertion — `journal.previous` must be set when `journal.current == $VERSION` |
| Auto-rollback target read | `promote.sh:452-453` | CHANGE: `PREV=$(_json_field "$PREV_JSON" previous)` → `PREV="$OLD_CUR"` (with empty-quoted-null handling) | Read THIS-RUN displaced version, not the journal's stale previous field |
| Auto-rollback read hardening | `promote.sh:452` (the PREV_JSON derivation line above) | RECOMMEND CHANGE: swap `_json_field` → `_json_field_quoted` (per `lib.sh:1355-1364` precedent) — defends against a future key-naming collision | Substring-anchor safety |
| rollback.sh target + validation | `rollback.sh:70-77` and `:108-113` | The target=$OLD_CUR (passed in by caller per the new contract) — the gate reads $1 as the target, asserts $TARGET == $OLD_CUR (no drift between caller-captured and gate-received) | Caller contract; rollback.sh now reads the caller's target, not journal's previous |
| boot_sweep_commit_and_continue | `launcher.sh:1020-1037` | NEW (C-4): stamp `in_flight.displaced_version = journal.current` at `journal_open_txn` (`lib.sh:700-712` — extend the existing bash whole-object writer; no helper dependency); boot_sweep reads `in_flight.displaced_version` and sets `previous = displaced_version` BEFORE closing the txn. Old-format txn (no `displaced_version`) → halt-for-human. Refresh comment at `launcher.sh:1009-1019` to document the boundary. | C-4: this is the ONLY path in launcher.sh that may update `previous` from boot-sweep land — preserves the FORWARD semantic rule. **R-08 mitigation: smallest possible launcher surface change (write-only patch on a new bash write, no helper, no python3 in boot path); no new external deps.** |
| boot_sweep_commit_and_continue comment | `launcher.sh:1009-1019` | REFRESH comment: "NOT mutated: pending_op (python lane). previous in live lane: NOT mutated by boot_sweep in the standard path — promote.sh owns previous via `set-previous` (P2 splice op) at commit (`:438`). boot_sweep path writes `previous = in_flight.displaced_version` ONLY in the operator-declared `rollback_safe:false` + `intent_flip` evidence path (`boot_sweep_commit_and_continue` at `:1020-1037`); `displaced_version` was stamped at `journal_open_txn` (C-4)." | Trace the boundary AND the FORWARD semantic source |
| Lib.sh field-read sites in the auto-rollback path | `lib.sh:1355-1364` (already `_json_field_quoted` for pending_op), the rollback.sh readers, promote.sh auto-rollback readers | Already migrated via precedent; the only remaining bare-substring read in the auto-rollback path is the one at `promote.sh:452` (`_json_field "$PREV_JSON" previous`) | Defense in depth |

### Failure modes + fail-closed

| Failure mode | Behavior | Fail-closed? |
|---|---|---|
| `$OLD_CUR` was empty (no displaced version — fresh install) | existing halt path at `promote.sh:459-468` (`halt(no-previous)`); halt-for-human, daemon rests on $VERSION (degraded, alerted) | YES (preserved) |
| `$OLD_CUR == quarantined` | existing halt path at `:472-481` (`halt(quarantined-previous)`); M4 — never auto-flip onto quarantined | YES (preserved) |
| `$OLD_CUR == missing-dir` | existing halt path at `:481-491` | YES (preserved) |
| `$OLD_CUR` has `rollback_safe != true` | existing halt path at `:491-501` (schema-drift guard D-FA4.5) | YES (preserved) |
| Immediacy assertion at commit fires (`journal.current ≠ $VERSION`) | halt "immediacy breach: journal current=$current ≠ $VERSION" + exit 78 | YES (NEW) — refuses to set previous on out-of-sync journal |
| boot_sweep_commit_and_continue hits the operator path | OLD_CUR captured from journal previous BEFORE current update; previous = OLD_CUR before txn close | YES (preserved via the new assertion + schema shape) |

### Proving halt→re-promote chain lands N-1

- Run N-1 halted AFTER flip, BEFORE commit (the Oct 9 arc scenario). Then:
  - `journal.current = vN-1` (flipped). `journal.previous = vN-2` (from N-2's successful commit; may also be stale from N-1's pre-flip window — the bug class).
  - Run N starts. `$OLD_CUR = vN-1` (captured at `:227` from journal `current` — but the digest notes promote.sh binds to the daemon's current via symlink; this capture must verify by reading `current`, not by assuming).
  - Promoting run N: commit-block succeeds; `journal_set_previous OLD_CUR` writes `previous = vN-1`. Inmediacy assertion: `journal.current == $VERSION == vN`. Pass.
  - Auto-rollback in run N (if it fails): reads `PREV = $OLD_CUR = vN-1` (NEW behavior). Correct.
- Without the fix: auto-rollback reads `journal.previous = vN-2`. **Bug**: rolls back two versions.
- **For the boot-sweep path (C-4):** at `journal_open_txn` time, `lib.sh:700-712` stamps `in_flight.displaced_version = journal.current`. When `boot_sweep_commit_and_continue` runs, it reads `in_flight.displaced_version` (not `journal.previous`) and writes `previous = vN-1`. A subsequent manual `rollback.sh --to` lands on N-1, not N-2. The two source paths (live vs boot-sweep) converge on the FORWARD semantic but use different writers; both are verified by AC5 cells 1-4.

### Interactions

- **(a) Journal splice** — independent. (`a` writes via the helper; the immediacy assertion reads via `_json_field_quoted` which stays bash.)
- **(b) Pending-op liveness** — independent.
- **(c), (d), (e), (f)** — independent.

---

## (h) Adjacent smalls (polish that rides the next promote)

### Include in P1 (the first promote on this branch)

| Site | Issue | Fix | Cite |
|---|---|---|---|
| `lib.sh:592` printf broken-pipe | `printf '%s\n' "$json" > "$tmp"` will SIGPIPE if downstream `mv` somehow truncates; produces noisy logs that don't change semantics but bury real warnings | Wrap with `2>/dev/null` OR redirect through `{ ... ; } 2>/dev/null`; mirror the existing pattern at `lib.sh:158` (`printf '%s %s\n' "$ts" "$*" >> "$file" 2>/dev/null`) | Real broken-pipe noise from the digest; small fix (this row is the `lib.sh:592` printf wrap; see the next row for the `_log`/`_logv`/`_warn` narrow claim at `lib.sh:120-122`) |
| `promote.sh:21-23` stale comment | "60s" claim no longer matches default behavior | refresh to "Default soak is adaptive: 60s floor, 120s cap, 3 consecutive greens, fail-fast on red. Override with `ENSEMBLE_PROMOTE_SOAK_S` or revert to conservative via `ENSEMBLE_SOAK_CONSERVATIVE=1`. Total ceremony budget: 900s (15min) wall-clock." | Discoverability for operators |
| `lib.sh:120 :121 :122` `_log` / `_logv` / `_warn` | G6 printf broken-pipe: source-of-noise for many cosmetic warnings when terminal is not a tty | **Narrowed to `lib.sh:120-122` ONLY** (cheap suggestion — the `lib.sh:592` printf wrap is a separate decision in T1.6 row above): apply `2>/dev/null` consistently OR add a single `exec 2>&1 || true` early in the lib.sh preamble; pick ONE | Closure for G6 |

### Defer (out of P1 scope)

| Site | Issue | Why defer |
|---|---|---|
| `lib.sh:2868` (PRESTOP MainPID), `:3066` (hand-back MainPID), `:4075-4082` (supervision classify) | unbounded read-only `systemctl show` / `is-active` calls | No perf-budget impact today (the per-phase budgets absorb the latency); cosmetic-only; can ship as a separate micro-task |
| `lib.sh:3662` (supervision pid) | `printf '%s\n' "$pid"` — cosmetic only | Not a broken-pipe risk (output is captured in shell var); skip |
| GNU debt sites: `lib.sh:4313` (`stat -f '%m'` retention eviction garbled on GNU), `launcher.sh:466` (`date -jur → zero-length cooldown`) | Platform-conditional bugs | Out of scope (`_iso_to_epoch` and lib cooldown arm are already uname-dispatched/fixed per digest) |

### Decision interactions + implementation sequencing

**Which decisions couple, what must land before what:**

| # | Decision | Pre-req | Lands with | Notes |
|---|---|---|---|---|
| (g) rollback target | — | P1 first | alone | Surgical; smallest payload; unblocks prior incident class; independent of (a) |
| (a) journal splice | — | P1 second | (b) | Foundation for everything else |
| (b) pending-op liveness | (a) | P1 second | (a) | Helper exposes `set-field last_heartbeat`; heartbeat writer is a 1-line extension |
| (c) adaptive soak | (a) (only for splash logs) | P1 third | (d), (e) | Tightly coupled trio — ship as one PR |
| (d) total ceremony budget | — | P1 third | (c) | Adaptive cap derives from budget remaining |
| (e) transient / boot handling | — | P1 third | (c) | Gate reads retries+grace; soak gate has its own retry caller |
| (f) audit-after-soak | — | P1 third, can land later | (c) ships first; (f) follows | Independent of (a/b/c/d/e) |
| (h) adjacent smalls | (g) | P1 first | (g) | printf noise rides the rollback PR |

**Suggested PR break-up** (for the phase-plan worker — three PRs max):

1. **PR-1 (correctness):** (g) rollback target + (h) printf + comment refresh. Smallest, surgical, unblocks the historical incident class.
2. **PR-2 (perf + correctness):** (a) journal splice + (b) pending-op liveness. Biggest perf win (28–46 min → <1 min), also closes the armed→flip misclassification window.
3. **PR-3 (perf + safety):** (c) adaptive soak + (d) total ceremony budget + (e) transient handling + (f) audit-after-soak sequencing. The "fast-by-default + reliable" trio.

**Order they should ship** (so each PR is self-consistent and the slowest dependent is last):

- (g) first — independent
- (a) second — foundation
- (c) (d) (e) (f) third — depend on the (a) helper for any new journal writes; ship as one PR because the trio's interactions are inseparable
- (b) can ride PR-2 OR PR-3; recommend PR-2 because the lock_heartbeat extension is a 1-line delta against the helper

**Final state expectations (the "before / after" numbers)**

| Metric | Today (v0.18 baseline) | After this mission |
|---|---|---|
| Clean promote wall-clock (typical) | 28–46 min (journal overhead) + ~8–13 min ceremony ≈ **36–59 min** | <1 min journal + ~8–13 min ceremony ≈ **8–13 min** |
| Clean promote wall-clock (worst-case envelope) | 3 h 15 m failure arc | ~870 s ceremony (per (d) C-10a arithmetic, +30 s slack inside the 900 s ceiling) + <1 s journal ≈ 14.5 min; abort-path all-budgets-max ceiling 1400 s (halts before commit) |
| Soak duration (default) | 300 s flat wait | 60–120 s adaptive; early exit on 3 consec greens @ **15 s** cadence; floor 60 s; **cap-reach WITHOUT 3 greens → exit 1 → auto-rollback (C-1)** |
| Boot-storm-induced false-red probability | ~10 % (1.5 s transient window race) | <0.1 % (60 s grace + bounded retries + 3 consec requirement + correct curl status-code classification per C-8a) |
| Halt→re-promote-chain rollback distance | N-2 sometimes (when N-1 halted after flip before commit) | **Always N-1** — live lane via `$OLD_CUR` captured at `:227 :436` + immediacy-asserted at `:438`; boot-sweep lane via `in_flight.displaced_version` captured at `journal_open_txn` (C-4) |
| Pending-op misclassification window (armed→flip) | ~37 min (slow bash walk — `journal_heartbeat` does ~280 s/call vs 30 s nominal; sweep's `PendingOp.owner_heartbeat_at` reader points at a field with zero writers) | <2 min (splice REPLACES bash walk at `lib.sh:1196`; cadence now matches `LOCK_HEARTBEAT_S=30`; sweep's `in_flight.last_heartbeat` reader sees the stamp — C-3 + C-2 corrections) |

**Expected before/after for a clean promote per the user's hard constraint**: "between 8–13 minutes typical, 14.5 minutes worst-case envelope" — well inside the 15-min default and inside the 900 s ceiling (30 s slack per the C-10a reconciled arithmetic). The ≤600 s claim from the original digest is re-scoped to the TYPICAL path (4.7–5.7 min typical per T-AC2a, ≤2x margin); worst-case 870 s + 30 s slack is the operative ceiling per the leader's Q6a ruling.

**R-10 ≤540 s relaxation proof (C-13):** the original R-10 mitigation pin "keep effective total ≤ 540 s" is RELAXED to the 870 s + 30 s slack ceiling given:

- Three-signal design (heartbeat freshness + `os.kill(pid, 0)` + cgroup leaf) holds the launcher's `hb_stale=0` against an in-flight ceremony; launcher reclaim is gated on hb-stale, not wall-clock.
- Lock-held defer is verified by `T-AC4b-ii` (lock-held → return 0, no mutation).
- Boot-sweep evidence check `:491` is verified by `T-AC5a` cell 3 (with the C-4 fix, previous is captured correctly).
- 900-total vs 900-staleness race is benign under the C-2/C-3 corrections: breach → exit 78 → owner-dead → hb stale → launcher reclaim is the correct post-mortem.

Budget counts lock-wait **in** (wall-clock is the hard constraint). Bound: `grace ≤ enclosing_phase_budget + EXECUTOR_STALENESS_WINDOW_S = 900 s`.

---

## (i) C-12 vehicle reconcile — `feature/checkpoint-conn-resilience`

`feature/checkpoint-conn-resilience` (base `0c040e5f8`) is **already on latest `c8a3fbf6`**:

- `persistence.py` — pool-backed saver
- `checkpoint_adapter.py` — retry wrapper
- `readiness.py` — probe
- (councilor-verified)

It **rides the v0.20.1 vehicle as-is** without an extra merge commit. The P4.1 defer-record outcome is: "no separate merge required; the resilience is on `c8a3fbf6` already; the reviewer gate is to confirm at PR-open time that the relevant pieces are present and that the (d) ceiling arithmetic accounts for the new probe cost." This closure closes the open question in `architecture-recommendation.md` §7 item 3.

---

## Open questions

| # | Question | Owner | When |
|---|---|---|---|
| Q1 | Does the operator want a separate `ENSEMBLE_PROMOTE_DEADLINE_S` override for the total ceremony budget, or is `PROMOTE_TOTAL_DEADLINE_S=900` enough? The digests say not — but the user's "PREFER SPEED" directive suggests overrides are not needed in default config. | planner → operator | P1 implementation |
| Q2 | Should `BUSY_INFLIGHT_BOUND` be 5 (default) or a per-instance tunable? `BUSY_INFLIGHT_BOUND=5` is a static guess; production might want it observable. | planner | P1 implementation |
| Q3 | For (f)'s deferred DELETE, should the background task be asyncio-managed from `lifespan startup` (current proposal), or a separate `MessageQueueJanitor` service? Single-process is simpler but couples pool lifetime to DELETE lifetime. | planner → architect | P1 implementation |
| Q4 | **CLOSED by C-4.** `in_flight.displaced_version` is captured at `journal_open_txn` (`lib.sh:700-712` extend); boot_sweep reads it. Test pin is `T-AC5a cell 3` per `tests/test_rollback_target.sh` (extended) and `tests/test_boot_sweep_commit_and_continue.sh` (extended). Old-format txn → halt-for-human (consistent with "halt-for-human is BY DESIGN"). | n/a | P1 PR-1 review |
| Q5 | **CLOSED by C-8b.** Busy-amnesty reads the **EXISTING** `readiness.py:198/222 detail.inflight_turns` field. No new `/run/ensemble/inflight_turns` file; no new daemon-side counter writer. The decision is `decision (e) busy-amnesty` per decisions.md (e) Concrete Contract. | n/a | n/a |

---

## References

- **Digest source**: agent-technical-analysis digest (planner-provided, 2-explorer verified) at HEAD `c8a3fbf6a48d6dd1bfea97d0fb41140501a552a9`
- **Pre-existing runbook reference**: `docs/runbooks/upgrade-drills.md` §7 (live-debug procedure for the boot-storm incident)
- **Pre-existing precedent**: `scripts/upgrade/ledger_check.py` (stdlib-only python helper; live-path refusal pattern at `:46-55`; exit code 0/1/78/2)
- **Hard-cap anchors verified by re-read**: `promote.sh:156` (soak cap 900), `lib.sh:102-115` (anti-flapping block), `promote.sh:177 :301 :342 :427 :503 :2526 :2532 :4249` (lock_heartbeat call sites), `lib.sh:108 :113` (LOCK_HEARTBEAT_S=30 / rwlock separation), `lib.sh:4258` (mid-soak version drift fails soak), `launcher.sh:908-915 :925-936` (D5 defer + under-lock revalidation), `launcher.sh:1009-1019 :1020-1037` (boot_sweep_commit_and_continue), `daemon/manager.py:843 :859-873 :424-426` (boot ordering), `daemon/repositories/message_queue/repository.py:1136-1143 :1144-1148` (audit log + task-table assumption), `daemon/services/upgrade_journal_sweep.py:82 :1579-1597 :1590 :1602-1683` (sweep shapes), `daemon/api.py:415 :812 :2873-2895` (Stop-frozen heartbeat amnesty + busy-set witnesses via has_instance_busy)
- **JSON-field hardening precedent**: `lib.sh:293-322 :1355-1364` (`_json_field_quoted` migration from bare-substring `_json_field`)
- **Plugin staleness precedent for python invocation in upgrade shell**: `lib.sh:2060-2085` (PLUGIN_STALENESS_PYTHON → REPO_ROOT/.venv/bin/python → python3 → python chain) — explicitly NOT used for the new helper (stdlib-only, plain `python3` is sufficient; this is the same approach `stage.sh:464` takes)
- **Anti-flapping block & cooldown machinery**: `lib.sh:102-115` (cap arming)
- **E6 timing model**: preflight 35s · stop 120s · flip 1s · handback 75s · livez 180s · readyz 120s · version 5s · soak 300s · commit ≈ **8–13 min**; journal overhead today: 28–46 min on clean promote, 37m observed armed→flip arc on the failure case
- **Mission source**: shared-meta-kv `wt.active.feature/upgrade-speed-overhaul.upg-overhaul` (mission object); shared-context `promote-ceremony-stall-executor-false-dead-handback-subprocess-wait-timeout-syst_20261012_072018.md` (debug-only; not match for threshold 10%)
- **Plugin staleness policy reminder**: `plugin_staleness_policy.policy = "ALLOW-STALE IS THE DEFAULT"` (from project metadata) — these decisions do not affect plugin staleness rules

---

**End of decisions.md**
