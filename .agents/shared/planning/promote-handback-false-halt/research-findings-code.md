# Research findings — promote-machinery healthy-boot false-halt (CODE SITES)

Read-only analysis of `/home/nea/ensemble-src-wt-hbfix` (branch `fix/promote-handback-false-halt`,
base == origin/latest). All anchors GREP-VERIFIED against this worktree at research time; the lead
anchors in the commission drifted in most cases (see §Drift table at the end).

Files of record: `scripts/upgrade/lib.sh` (9052 lines), `scripts/upgrade/promote.sh` (1121),
`scripts/upgrade/restart.sh` (374), `scripts/upgrade/stage.sh` (1123),
`scripts/upgrade/adopt-unit.sh` (596), `daemon/tools/upgrade_tools.py` (4390),
`daemon/services/upgrade_journal_sweep.py` (3421).

---

## Q1 — `_run_bounded` rc production + every "nonzero rc labelled timeout/budget" site

### How rc is produced — `lib.sh:4856-4882`

```
4856: _run_bounded() {
4857:     local timeout_s="$1"; shift
4858:     [ "${1:-}" = "--" ] && shift
4859:     local pid watcher rc=0 TERM_GRACE_S=5
4860:     "$@" &
4861:     pid=$!
4862:     (
4863:         sleep "$timeout_s" 2>/dev/null
4864:         kill -TERM "$pid" 2>/dev/null
4865:         sleep "$TERM_GRACE_S" 2>/dev/null
4866:         kill -KILL "$pid" 2>/dev/null
4867:     ) >/dev/null 2>&1 &
4868:     watcher=$!
4869:     if wait "$pid" 2>/dev/null; then
4870:         rc=0
4871:     else
4872:         rc=$?
4873:     fi
4874:     # SIGKILL = 137 (128+9), SIGTERM = 143 (128+15) → collapse to 124
4876:     case "$rc" in
4877:         137|143) rc=124 ;;
4878:     esac
```

- NO `timeout(1)` — a background child + a `sleep/kill` watcher (portable bash 3.2 / BSD). Documented at
  `lib.sh:4808-4855` ("Why NOT GNU `timeout(1)`: absent on BSD/macOS stock installs").
- The ONLY rc remapping is `137|143 → 124` (`:4876-4878`). **Every other child rc is passed through
  verbatim** — including `systemctl is-active`'s own rc 3 ("inactive"/"activating" are both "not active"
  to is-active) and rc 4 ("unknown"). That is the mechanical source of the incident's rc=3 event.
- Callers MUST capture with `|| rc=$?` (`:4823-4827`) because these scripts run under `set -e`.

### Timeout/budget mislabelling sites (exhaustive grep of `scripts/upgrade/**` for `kind=budget`)

| # | Site (function) | Guard shape | Label honesty |
|---|---|---|---|
| 1 | `lib.sh:6332-6339` `_supervision_handback_unit` → reset-failed probe | `if _run_bounded …; then : else` — **no rc test at all** | **DISHONEST, doubly**: journals `kind=budget reason=child: timeout(SYSTEMCTL_PROBE_BUDGET_S=…) … rc=124` with a **hard-coded literal `rc=124`** even when the child failed with its own rc (e.g. 1). WARN text: "reset-failed probe exceeded budget (…) — continuing". |
| 2 | `lib.sh:6356-6360` is-active preflight | `if [ "$isact_rc" -ne 0 ]` | **DISHONEST (the incident shape)**: any nonzero rc → WARN "is-active probe exceeded budget (…)"; journal `kind=budget reason=child: timeout(SYSTEMCTL_PROBE_BUDGET_S=…) … rc=$isact_rc`. The rc VALUE is truthful, the LABEL/kind/ reason are not: rc=3 or 4 arrives here and is recorded as a budget timeout. |
| 3 | `lib.sh:6697-6701` `restart_via_launcher` scope-arm is-active | `if [ "$_sc_rc" -ne 0 ]` | **DISHONEST, identical shape** (`site=scope_arm_is_active kind=budget reason=…timeout… rc=$_sc_rc`). |
| 4 | `lib.sh:6383-6387` unit start | `if [ "$sbrc" = "124" ]` | Honest — timeout branch only; the `else` (`:6388-6389`) has no journal row, just a WARN. |
| 5 | `lib.sh:6747-6751` scope-arm start | `if [ "$_sc_rc" = "124" ]` | Honest (timeout-only), else the frozen 7c WARN at `:6753`. |
| 6 | `lib.sh:5281-5285` / `:5311-5315` `stop_via_stop_script` (unit / pid arms) | `if [ "$sbrc" = "124" ]` | Honest — timeout branch `kind=budget`, `else` branch `kind=nonzero_exit` (`:5289-5291`, `:5319-5321`). |
| 7 | `lib.sh:7688-7702` `_df_systemctl` | `if [ "$__rc" -eq 124 ]` | Honest — but note the comment at `:7660` "is-active exits 3 when inactive — preserved", i.e. the codebase already knows rc 3 is a legitimate is-active answer. |

Secondary label incongruity: the journal EVENT KIND is `subprocess_wait_timeout` for **both** timeout and
plain-nonzero outcomes (`lib.sh:5289`, `:5319` write `kind=nonzero_exit` inside a
`subprocess_wait_timeout` event). Any fix that "gives honest rc classification" should decide whether the
event kind is part of the contract or only the `detail` field is (journal readers: `journal_read.py`,
`daemon/tools/upgrade_journal.py`).

**Net for candidate A:** exactly three sites need change — `lib.sh:6332-6339`, `:6356-6360`,
`:6697-6701` — of which two are the identical is-active conflation and one hard-codes `rc=124`.

---

## Q2 — Serving-verify green-class fail-fast

### Function: `_handback_serving_probe` — `lib.sh:6093-6133`

```
6093: _handback_serving_probe() {
6094:     local running hspc_rc=0
6095:     _probe_once_classified "/livez" "${PORT:-}" || hspc_rc=$?
6096:     case "$hspc_rc" in
6097:         0)
…
6103:             if ! _gate_serving_attribution "hand-back verify"; then
6104:                 _warn "hand-back verify: serving-process attribution REFUSED${GATE_ATTRIBUTION_DETAIL:+ — $GATE_ATTRIBUTION_DETAIL} (probe-level fail-fast, Fix #2); NOT success"
6105:                 return 1
6106:             fi
```

- Green-class signal = `_probe_once_classified` rc **0** (`lib.sh:4449-4508`; taxonomy 0 green / 1 red /
  2 transient / 3 boot-grace-suppressed; 2xx → `_PROBE_STATUS=green`, `:4486`).
- **The fail-fast:** on the FIRST green answer whose attribution conjunct refuses it, the probe returns 1
  (`:6103-6106`) — no retry, no grace, no "transient" classification. Version mismatch (`:6113-6116`) and
  absent version (`:6109-6111`) also return 1 immediately by design.
- 1 = fail-fast, 2 = transient (keep polling) — contract stated at `:6083-6092`.

### Caller: `_supervision_unit_serving` — `lib.sh:6170-6298`

The probe is called in two arms of the poll loop:
- lsof present + curl present: `:6222-6236` → `hsp_rc=0; _handback_serving_probe || hsp_rc=$?` then
  `case "$hsp_rc" in 0) … ;; 1) return 1 ;; *) ;; esac` — **`:6234: 1) return 1 ;;`**
- lsof absent + curl present (degraded serving-only): `:6246-6258` → **`:6256: 1) return 1 ;;`**

Hand-back chain: `promote.sh:684 restart_via_launcher` → `lib.sh:6501 restart_via_launcher` →
`lib.sh:6650/:6655 _supervision_handback_unit` → `lib.sh:6395 _supervision_unit_serving` → probe.
`_supervision_handback_unit` turns any verifier rc≠0 into `return 1` (`:6395-6397`), and
`promote.sh:684-700` turns that into halt + B4 leave-txn-open.

### Where the 180 s floor / "+38 s" come from

- Knobs resolved at source time: `lib.sh:225-237` — `HANDBACK_VERIFY_BUDGET_S` ceiling **300**
  (`:225`, and **exported** at `:233`), `HANDBACK_VERIFY_FLOOR_S` **180** (`:234`),
  `HANDBACK_VERIFY_GRACE_S` **60** (`:235`), `HANDBACK_TAIL_RESERVE_S` **120** (`:236`),
  `SYSTEMCTL_PROBE_BUDGET_S` **5** (`:237`), `HANDBACK_START_BUDGET_S` **60** (`:191`).
- Effective budget arithmetic — `lib.sh:6175-6203`: with a ceremony clock
  (`PROMOTE_CEREMONY_DEADLINE_EPOCH`, set at `promote.sh:336`, plain non-exported variable) →
  `effective = clamp(remaining − 120, 180, 300)`, `grace = min(60, max(0, remaining − effective))`.
  Floor-wins bands get distinct "ceremony time exhausted" verdicts at `:6288-6297`.
- **The +38 s halt is NOT a code constant** — NOT-FOUND as an artifact. Wall-clock to halt on the
  incident path is dominated by the *fail-fast* (a single poll iteration) plus the stop/start/probe
  latencies; the budget never gets consumed because attribution refusal returns on the FIRST green.
  So candidate B cannot "raise a budget" to fix it — the defect is that the green is refused at all.

---

## Q3 — `_gate_serving_attribution` / `_supervision_owned_pids` / why inherited-fd children miss

### `_gate_serving_attribution <what>` — `lib.sh:8004-8092`

```
8004: _gate_serving_attribution() {
8005:     local what="${1:-gate}" holders h owned nonmatch="" lpid lrc hrc
8006:     GATE_ATTRIBUTION_DETAIL=""
8007:     holders="$(_supervision_port_holders)"
8008:     hrc=$?
8009:     case "$hrc" in
8010:         2) GATE_ATTRIBUTION_DETAIL=""; _gate_attr_unprobeable "$what" "no lsof / PORT unset / lsof errored"; return 0 ;;
8011:     esac
8012:     if [ -z "$holders" ]; then
…        → REFUSE (rc 1): "answered green but lsof finds NO listener"
8020:     fi
```

Layer 1 — OWNERSHIP (`:8021-8047`): every LISTEN holder must be a member of
`_supervision_owned_pids`; the owned list is newline-joined and normalised to spaces
(`owned_spaced`, `:8031`) before token matching (`:8032-8038`). Any non-member →
`GATE_ATTRIBUTION_DETAIL="port-holder(s):$nonmatch NOT in the supervision-owned pid set (owned:…)"`
→ **rc 1** (`:8039-8047`). Empty owned set + live holder is the same refusal.

Layer 2 — SUPERVISOR LINEAGE (`:8048-8090`), keyed on the PRE-STOP classification
`SUPERVISION_MODE`:
- `launchd` → `_supervision_launchd_job_pid` + `_supervision_pid_in_launchd_lineage` (ppid walk,
  depth ≤ 12 — `:7092-7105`); tool failure → loud skip/pass (`:8054-8059`).
- `unit` → `_gate_attr_unit_lineage "$what" $holders` (`:8087`).

`_gate_attr_unit_lineage` (`lib.sh:8100-8137`): `systemctl show $unit -p MainPID --value` (bounded,
`:8104`), then `-p ControlGroup --value` (`:8112`); each holder must be `= MainPID` **or** have
`/proc/<pid>/cgroup` (last line, after the final `:`) contain the unit ControlGroup as a substring
(`:8119-8135`). Unresolvable MainPID / unobservable ControlGroup → loud skip + rc 0 (`:8107-8118`).

### Censuses and anchoring

- Port-holder census: `_supervision_port_holders` — `lib.sh:7037-7054`:
  `lsof -ti:"$PORT" -sTCP:LISTEN` (`:7042`), stderr captured; **rc 2 = unprobeable** (PORT unset, lsof
  absent, or lsof errored with non-empty stderr — `:7044-7047`). The `-sTCP:LISTEN` scope is deliberate
  (`:6952-6959`: bare `lsof -ti:$PORT` would include ESTABLISHED clients).
  **No `ss`, no `fuser`, no `/proc/net/tcp`** anywhere in `scripts/` (grep: zero hits).
- Owned-pid anchoring: `_supervision_owned_pids` — `lib.sh:6921-6950`. Two-tier, mirroring
  `scripts/stop-ensemble.sh`:
  - Tier 1a — anchored executable paths: `pgrep -f "<INSTALL_DIR>/launcher\.sh( |$)"`,
    `…/ensemble-prod( |$)`, `…/current/ensemble-prod( |$)` (`:6927-6929`).
  - Tier 1b — `ps -axo pid=,comm=,args=` scan for `ensemble-prod|launcher.sh` shapes (`:6930-6936`);
    such candidates must ALSO pass the anchored-path regex on `ps -o args=` or have their cwd
    (`lsof -a -p <pid> -d cwd -Fn`) equal INSTALL_DIR/physical INSTALL_DIR (`:6943-6947`).
  Deduped, ascending, one per line (`:6937-6949`). **The anchor is argv path + cwd — there is no
  cgroup walk and no ppid chain in this function.**

### Why inherited-fd MCP children fall outside

An MCP child (context7 npm process, code-server, …) forked by our daemon:
1. has argv that is NOT `<INSTALL_DIR>/launcher.sh` / `ensemble-prod` → tier 1a misses it;
2. is not `ensemble-prod`/`launcher.sh`-shaped → tier 1b misses it;
3. therefore it is not in the owned set → **Layer 1 refuses** (`:8039-8047`) even though the daemon
   itself is owned. The refusal fires once per census with no grace (there is no LISTEN-grace here —
   contrast `_flip_verify_port_attribution`, `:6960-7035`, which DOES have
   `FLIP_VERIFY_LISTEN_GRACE_S` defaulting to `LIVEZ_BUDGET_S`).

Important asymmetry for candidate B: **Layer 2's `unit` arm would have ACCEPTED those children** — they
are descendants of MainPID and therefore inside the unit ControlGroup (`:8119-8135`). The refusal can
only have come from Layer 1, which is evaluated first and unconditionally. In other words the codebase
already possesses a containment signal (cgroup membership) that is strictly more permissive and more
correct than argv anchoring, but it is only consulted after Layer 1 has already refused
(and only when `SUPERVISION_MODE=unit`).

### Signals the script can feasibly read (all already used somewhere in lib.sh)

| Signal | Availability | Existing use |
|---|---|---|
| `/proc/<pid>/cgroup` (last line, after final `:`) | Linux; guarded by `[ -r … ]` | `lib.sh:7114-7120`, `:7426-7427`, `:7798-7801`, `:7822-7825`, `:8123-8134` |
| `ps -o ppid=` ancestor chain | POSIX, bounded depth | `_supervision_pid_in_launchd_lineage` `:7092-7105` |
| `ps -axo pid=,comm=,args=` | POSIX/BSD+GNU | `:6930-6947` |
| `lsof -a -p <pid> -d cwd -Fn` | POSIX w/ lsof | `:6945-6946` |
| `lsof -ti:<port> -sTCP:LISTEN` (inode/pid census) | POSIX w/ lsof | `:7042` |
| `systemctl show <u> -p MainPID / -p ControlGroup --value` | Linux+systemd, bounded | `:8104`, `:8112` |
| `/proc/<pid>/environ` (INVOCATION_ID) | Linux; `[ -r ]` guarded | `:7426-7427` |
| `readlink /proc/<pid>/fd/*`, `/proc/net/tcp` inode→fd provenance | Linux | **NOT USED anywhere** (grep: zero hits). Available but net-new. |

Candidate-B feasibility notes (facts, not recommendations): the cgroup arm is portable-to-Linux only and
already written; on Darwin only ppid-walk exists (`:7092-7105`) and the unit arm is unreachable
(`_supervision_host_allows_unit` `:5417-5421` gates on Linux + `/run/systemd/system`).

---

## Q4 — Retry asymmetry: systemd unit-arm vs launchd arm — CONFIRMED

**Systemd unit arm — ZERO retry.** `_supervision_handback_unit` (`lib.sh:6317-6415`) performs exactly
one `start` invocation and one verify pass:

```
6365:     # 3. the start itself …
6377:     if _run_bounded "$HANDBACK_START_BUDGET_S" -- "$SYSTEMCTL_BIN" start "$unit" 2>"$errfile"; then
6378:         :
6379:     else … return 1
6393:     rm -f "$errfile"
6394:     # 4. verify NEW MainPID (differs from pre-stop) + port serving.
6395:     if ! newpid="$(_supervision_unit_serving "$unit" "$prestop")"; then
6396:         _warn "unit hand-back FAILED verification for $unit — NO nohup fallback (Amendment #1) — halting for the caller's B4 policy"
6397:         return 1
6398:     fi
```

No loop shape of any kind: no `while`, no attempt counter, no re-`start`, no re-verify. Lead
`:6394-6397` is accurate (the verify-fail return).

**launchd arm — bounded retry (2 attempts, 2 s→4 s backoff).** `restart_via_launcher`,
`lib.sh:6541-6557`:

```
6541:         lrc=1
6542:         while [ "$lattempt" -le 2 ]; do
6543:             _supervision_launchd_bootstrap "$lplist"
6544:             lrc=$?
6545:             # rc 0 = started; rc 2 = already loaded …
6547:             if [ "$lrc" -eq 0 ] || [ "$lrc" -eq 2 ]; then break; fi
6548:             _hb_err="$(_handback_last_error_text)"
6549:             _warn "launchd hand-back: bootstrap attempt $lattempt FAILED (rc=$lrc) …"
6550:             sleep $(( lattempt * 2 ))
6551:             lattempt=$(( lattempt + 1 ))
6552:         done
6553:         if [ "$lrc" -ne 0 ] && [ "$lrc" -ne 2 ]; then … return 1 ; fi
```

Note the asymmetry is narrower than "retry vs no retry": launchd retries the **start/bootstrap command**
(its post-start verify is still a single pass, done in promote.sh's launchd flip-verify block
`promote.sh:726-767` which does have a bounded wait loop at `:727-732`). The Linux unit path retries
**nothing** — neither start nor verify.

---

## Q5 — `supervision_boot=UNIT_MANAGED` advisory: produced, consumed, and the halt-path blindness

### Produced — `daemon/services/upgrade_journal_sweep.py:405` → `:407-465`

```
405:         self._emit_supervision_boot_advisory()
...
431:         try:
432:             env = dict(os.environ)
433:             env["INSTALL_DIR"] = str(self._install_dir)
434:             det = uj.supervision_detect(env)
441:             mapped = uj.supervision_outcome(det.mode, det.state, det.unit)
442:             detail = (f"state={det.state} mode={det.mode} "
444:                       f"unit={det.unit or '<none>'} "
445:                       f"outcome={mapped.outcome}")
449:             uj.journal_history_append(self._install_dir, "supervision_boot", detail)
```

Boot-time ONLY (comment `:402-405`: "Boot-time ONLY — deliberately NO periodic sweep"). Advisory:
never gates boot, never retries, never raises (`:410-414`, `:457-465`). Detail grammar:
`state=<STATE> mode=<mode> unit=<unit|<none>> outcome=<outcome>[ note="…"]`.

### Consumed — ONLY `scripts/upgrade/adopt-unit.sh:477-497` (grep: the sole reader in `scripts/`)

```
481:     jp="$INSTALL_DIR/releases/state.json"
483:         detail="$(sed -n 's/.*"event": *"supervision_boot", *"detail": *"\([^"]*\)".*/\1/p" "$jp" | tail -1)"
485:             "state=UNIT_MANAGED mode=unit unit=$UNIT_NAME "*|"state=UNIT_MANAGED mode=unit unit=$UNIT_NAME")
489:                 _warn "verify: journal $jp has NO supervision_boot advisory yet …"
492:                 _report_partial_and_fail "verify/boot-journal" "last supervision_boot advisory is '$detail' …"
```

### Confirmed: the halt path NEVER reads it

- The only two occurrences of the literal `supervision_boot` in `scripts/**` are the adopt-unit block
  above (`:478` comment, `:483`, `:489`, `:492`).
- `promote.sh:684-700`, `lib.sh:6317-6415`, `lib.sh:670-700` (halt emitters), `restart.sh:251-264`,
  `rollback.sh:250-262` contain no `supervision_boot` reference and no journal read of history at all —
  they read only the in-process `SUPERVISION_*` globals.

### Where the halt text's `verified=UNIT_MANAGED` comes from — `promote.sh:698`

```
698:     journal_history_append halt "promote aborted post-flip: unit hand-back failed for $VERSION — … (declared=${SUPERVISION_MODE:-?} verified=${SUPERVISION_STATE:-?} unit=${SUPERVISION_UNIT:-?}; …; hand-back error: ${HB_ERR:-none captured})" \
```

`SUPERVISION_*` are **shell globals in the promote executor process**, set by
`supervision_classify` during the PRE-STOP preflight
(`promote.sh:460 supervision_preflight` → `lib.sh:7864 supervision_preflight` → classify), and
re-derived at the stop site (`lib.sh:5202 supervision_classify || true` inside
`stop_via_stop_script`). Assignment ladder: `lib.sh:7211`, `:7256-7257`, `:7312-7377`, `:7405-7417`,
`:7483-7487`.
**There is no persistent "pre-stop classification store"** beyond:
(a) the additively stamped txn (`journal_mark_supervision`, `lib.sh:7918-…`, called from
`promote.sh:598`, `restart.sh:223`, `rollback.sh:201`), and
(b) two pre-stop stashes — `SUPERVISION_PRESTOP_MAINPID` (`lib.sh:5117`, `:5239-5242`) and
`SUPERVISION_LAUNCHD_PLIST_PATH` (read at `lib.sh:5698`).
The daemon-side `supervision_boot` advisory is a THIRD, independent classification (python twin
`uj.supervision_detect`) and is never consulted by the halt path — matching the commission's finding (4).

---

## Q6 — HB_ERR / error stash: launchd-only writer, systemd path structurally cannot populate it

### Stash plumbing — `lib.sh:5592-5614`

```
5592: HANDBACK_LAST_ERROR=""
5593: HANDBACK_ERROR_FILE="${TMPDIR:-/tmp}/ensemble-hbk-err.$$"
5594: _handback_err_file_write() { printf '%s' "$1" > "${HANDBACK_ERROR_FILE:-…}" 2>/dev/null || true; }
5599: _handback_err_file_clear() { rm -f "${HANDBACK_ERROR_FILE:-…}" …; }
5605: _handback_last_error_text() {
5606:     if [ -n "${HANDBACK_LAST_ERROR:-}" ]; then printf '%s' "$HANDBACK_LAST_ERROR"; return 0; fi
5610:     local _f="${HANDBACK_ERROR_FILE:-…}"
5611:     if [ -s "$_f" ]; then printf '%s' "$(head -c 400 "$_f" … | tr '\n' ';')"; fi
```

Two channels: in-process variable (survives plain calls) + `$$`-keyed temp file (survives `$( )`
subshells — `MAJOR #1` comment `:5577-5591`).

### Every WRITE site (grep `_handback_err_file_write` / `HANDBACK_LAST_ERROR=`)

| Site | Function | Host reachability |
|---|---|---|
| `lib.sh:5634-5635` | `_supervision_launchd_bootstrap` — `HANDBACK_LAST_ERROR="$(printf 'launchctl bootstrap %s %s failed (rc=%s): %s' …)"` + file write | **Darwin only** |
| `lib.sh:5720-5721` | `_supervision_launchd_plist_path` — `plist resolution failed for job '%s' (launchctl print: …)` + file write | **Darwin only** |

Clear sites: `:5626-5627` (bootstrap success), `:5692`, `:5701`, `:5709` (resolver rungs),
`:6558` (`HANDBACK_LAST_ERROR=""; _handback_err_file_clear` after launchd success).

### Readers — the three halt templates + WARN lines

- `promote.sh:692 HB_ERR="$(_handback_last_error_text)"` then `:697 rm -f "${HANDBACK_ERROR_FILE:-}"`,
  used at `:698` (`hand-back error: ${HB_ERR:-none captured}`).
- `restart.sh:257-261` — same shape, same template tail.
- `rollback.sh:256-260` — same shape.
- WARN interpolation at `lib.sh:6537`, `:6548`, `:6554` (launchd branch only).

### Conclusion

**The systemd lane writes NOTHING to the stash**, so `hand-back error: none captured` is not a bug in
the reader — it is the truthful answer on Linux. What the Linux arm *does* have available but discards:
the `start` command's first stderr line (`lib.sh:6376-6389`: `errfile="$(mktemp /tmp/.ensemble-hb-sc.XXXXXX)"`,
`first="$(head -n1 "$errfile" …)"`, used ONLY in the WARN at `:6389`, never stashed), and the verifier's
`GATE_ATTRIBUTION_DETAIL` / `_warn` text (`lib.sh:6104`, `:6219`, `:6296`), plus
`GATE_ATTRIBUTION_DETAIL` for the other gates.

A systemd-lane stash write (candidate C) would therefore need to touch, minimally:
1. `lib.sh:6332-6339` (reset-failed: capture the child rc / first stderr line),
2. `lib.sh:6356-6360` (is-active: same),
3. `lib.sh:6376-6389` (start: stash `first` on the non-124 failure arm — currently discarded),
4. `lib.sh:6395-6397` (verifier refusal: stash `GATE_ATTRIBUTION_DETAIL` before returning 1),
5. success/benign clears so a stale seed never masks the real diagnosis (`:6558` precedent),
6. the three halt templates are already correct readers — no change needed there
   (`promote.sh:692-698`, `restart.sh:257-261`, `rollback.sh:256-260`).

---

## Q7 — `scope_arm_is_active` in the restart lane

### NOT a function — it is a journal SITE LABEL

Grep for `scope_arm_is_active` returns **one** hit: `lib.sh:6700`. There is no function of that name.
The code is inline in `restart_via_launcher` (the comp7 `a′-1 PREFLIGHT` arm,
`lib.sh:6667-6755`).

### rc handling — identical conflation, CONFIRMED

```
6695:         _sc_isact="$(_run_bounded "$SYSTEMCTL_PROBE_BUDGET_S" -- "$SYSTEMCTL_BIN" is-active "$ENSEMBLE_RESTART_UNIT" 2>/dev/null)"
6696:         _sc_rc=$?
6697:         if [ "$_sc_rc" -ne 0 ]; then
6698:             _warn "is-active probe exceeded budget (${SYSTEMCTL_PROBE_BUDGET_S}s) for $ENSEMBLE_RESTART_UNIT — treating as NOT-active; continuing (probe timeout never fails the launcher start on its own)"
6699:             journal_history_append subprocess_wait_timeout \
6700:                 "site=scope_arm_is_active kind=budget reason=$(printf 'child: timeout(SYSTEMCTL_PROBE_BUDGET_S=%s)' "$SYSTEMCTL_PROBE_BUDGET_S") unit=$ENSEMBLE_RESTART_UNIT rc=$_sc_rc — treating as not-active, continuing" \
6702:             _sc_isact=""
6703:         fi
```

- rc 124 (timeout), rc 3 (inactive/activating) and rc 4 (unknown) all take the same branch
  (`-ne 0`), all produce "exceeded budget" WARN + `kind=budget reason=…timeout…`, and `_sc_isact` is
  cleared so the `[ "$_sc_isact" = "active" ]` test at `:6704` is skipped. Behaviourally benign
  (all three mean "not confirmed active"), **but the journal lie is identical to the promote path** —
  this is the "restart lane carries the IDENTICAL rc conflation" the commission suspected. CONFIRMED.
- Related rc handling in the same function: `:6731-6733` `_run_bounded … start; _sc_rc=$?`;
  `:6747` `if [ "$_sc_rc" = "124" ]` → timeout WARN + `site=scope_arm_start` journal; `:6753` else →
  frozen 7c WARN. Those two are honest.

### Dispatch refusal paths that could abandon a restart arm WITHOUT a cause row

`restart.sh` refusals, exit 78, and whether they journal:
- `:135-138` stop-script sibling unusable — `_warn` only, **no journal row**.
- `:147-150` journal unreadable/TORN — `_warn` only, **no journal row**.
- `:154-157` no `pending_op` — `_warn` only, **no journal row**.
- `:160-163` `pending_op.kind != restart` — **no journal row**.
- `:164-167` `pending_op.run_id` mismatch — **no journal row**.
- `:171-175` `in_flight.kind != restart` — **no journal row**.
- `:178-183`, `:184-189`, `:190-193` lock-missing / lock run_id mismatch / owner-write failure —
  these DO `journal_history_append halt` (`:180`, `:186`; `:190` is warn-only).
- `require_live_guard_restart` (`lib.sh:1492-1506`) journals `live-restart-refused` ONLY for target
  `live` (`:1494`), then exit 78.
- `supervision_preflight` (`restart.sh:213`) can `_refuse` exit 78 with its own journal row.

So the "clean audit, no cause" shape for the two abandoned live restart arms is consistent with ANY of
the six silent `_warn`-only exits above (`:135`, `:147`, `:154`, `:160`, `:164`, `:171`) — none of them
writes a journal event. This is a factual enumeration, not an attribution.

---

## Q8 — "SUPERVISION_* frozen vars in promote.sh:676-680"

**Drift: there are no frozen/exported SUPERVISION_* values in promote.sh.** `promote.sh` contains
**zero** `export` and zero `readonly`/`declare -r` (grep). The lead anchor `:676-680` lands inside a
COMMENT block, `promote.sh:673-683`:

```
673: # ═════════════ 6. RESTART — supervision-aware hand-back (P3) ═════════════════
674: # The executor's P1 classification — consumed from the stop-site globals
675: # (SUPERVISION_* set while the daemon still ran; never re-derived here,
676: # the daemon is down) — selects the hand-back mode: UNIT_MANAGED → unit
677: # hand-back (NO nohup fallback — Amendment #1: a fallback re-creates the
678: # survivor lineage; systemd brings up the flipped release); SCOPE_SURVIVOR
679: # + unit → same unit hand-back + the scope→unit self-heal journal event;
680: # script shapes → today's nohup / comp7 paths. A unit hand-back failure
681: # is NEVER a false success: halt journal event + B4 leave-txn-open (…)
```

What is actually "frozen" around the hand-back, and what it implies for changing verify behaviour:

1. **7b substring trap (success-path only)** — no success-path `_log`/`_warn` line may contain the
   literal `systemctl start`. Stated at `lib.sh:6311-6316`, `:6167-6169`, `:6675-6686`; pinned by
   `assert_not_contains "systemctl start"` in `tests/test_supervision_stop_handback.sh:288`, `:768`,
   `:840`, `:862`; `tests/test_supervision_e2e.sh:474`, `:548`; `tests/test_supervision_journal.sh:456`;
   `tests/packs/handback_verify_budget_test.sh:522`, `:534`, `:603`;
   `tests/test_bounded_subprocess_waits.sh:377`; `tests/test_promote_cgroup_survivorship.sh:577`.
2. **7c frozen failure wording** — `falling back to nohup launcher (comp7 opt-in path)` is byte-pinned
   on the scope-arm failure path (`lib.sh:6748`, `:6753`; pinned at
   `tests/test_bounded_subprocess_waits.sh:448`, `tests/test_supervision_stop_handback.sh:894`).
3. **Plain-variable channel discipline** — `PROMOTE_CEREMONY_DEADLINE_EPOCH` (`promote.sh:336`,
   comment `:329-335`) and `PROMOTE_EXPECTED_VERSION` (`promote.sh:556`, comment `:546-555`) are
   deliberately NOT exported so child scripts cannot inherit them. `HANDBACK_VERIFY_BUDGET_S` IS
   exported (`lib.sh:233`) because the daemon-side closer reads it (coupling
   `UGPRADE_CLOSER_BOOT_GRACE_S ≥ HANDBACK_VERIFY_BUDGET_S`, comment `lib.sh:222-233`) — so lowering
   or redefining that knob has a daemon-side consequence.
4. **Bound-table duplication** — the C-10a table is byte-identical in `lib.sh:157-173` and
   `promote.sh:309-327` ("single source of truth holds only while BOTH carry the same table").

Implication: verify-behaviour changes can live entirely inside `lib.sh` (promote.sh needs no edit), but
log-text additions must respect 7b/7c, and changing the budget knob family must keep the C-10a table
copies in sync and respect the exported-knob coupling.

---

## Q9 — Scripts-floor / scripts-stamp constants and the provenance guard

### Definitions

| Constant | Definition site (now) |
|---|---|
| `PIPELINE_SCRIPTS_REV` | `scripts/upgrade/lib.sh:95` → `PIPELINE_SCRIPTS_REV=1aa0065eb818ff00cb32730fab54d7f2060c42d8` |
| `PIPELINE_SCRIPTS_FLOOR_COMMIT` | `daemon/tools/upgrade_tools.py:4145` → `PIPELINE_SCRIPTS_FLOOR_COMMIT = "1aa0065eb818ff00cb32730fab54d7f2060c42d8"` |
| `PIPELINE_SCRIPTS_STAMP_WINDOW_BYTES` | `daemon/tools/upgrade_tools.py:4165` (64 KiB — bounded first-N-KB read of `<scripts_dir>/lib.sh`) |

**Both values are currently the SAME sha** (`1aa0065e…`), i.e. the D6-8 "the two travel together"
invariant holds at this tip.

### Validators (release-time, shell) — `lib.sh:738-753` and `:773-788`

`_pipeline_scripts_stamp_verdict <stamp> <floor> <repo_root>` (`:738`):
1. 40-hex (`:740-743`); 2. `git cat-file -e <stamp>^{commit}` (`:744-747`);
3. `stamp == floor` when floor set (`:748-751`).

`_pipeline_scripts_floor_verdict <floor> <repo_root>` (`:773`):
1. set and `^[0-9a-f]{7,40}$` (`:775-782`); 2. `git merge-base --is-ancestor "$floor" HEAD` (`:783-786`).

### Where they run — `scripts/upgrade/stage.sh`

```
773: FLOOR_SRC="$REPO_ROOT/daemon/tools/upgrade_tools.py"
774: FLOOR_COMMIT="$(sed -n 's/^PIPELINE_SCRIPTS_FLOOR_COMMIT = "\([0-9a-f][0-9a-f]\{6,39\}\)"$/\1/p' "$FLOOR_SRC" …)"
775: if ! FLOOR_REFUSAL="$(_pipeline_scripts_floor_verdict "$FLOOR_COMMIT" "$REPO_ROOT")"; then … exit 78
780: _log "floor validated: PIPELINE_SCRIPTS_FLOOR_COMMIT=$FLOOR_COMMIT is an ancestor of the built tree (T6.15)"
794: STAMP_SRC="$REPO_ROOT/scripts/upgrade/lib.sh"
795: STAMP_REV="$(sed -n 's/^PIPELINE_SCRIPTS_REV=…/\1/p' "$STAMP_SRC" …)"
796: if ! STAMP_REFUSAL="$(_pipeline_scripts_stamp_verdict "$STAMP_REV" "$FLOOR_COMMIT" "$REPO_ROOT")"; then … exit 78
801: _log "stamp validated: PIPELINE_SCRIPTS_REV=$STAMP_REV is a resolvable commit sha == the floor (W-2/T6.4/D6-8)"
817: _scripts_coverage_missing="$(executor_family_verify_stamp_coverage "$REPO_ROOT" "$STAMP_REV")"
818: if [ -n "$_scripts_coverage_missing" ]; then … exit 78
```

The manifest carries the value too (`stage.sh:835 "pipeline_scripts_floor_commit": "$FLOOR_COMMIT"`).

### Arm-time guard (daemon side) — `daemon/tools/upgrade_tools.py:4326-4375`

```
4326:         _s_rc, _s_out, _s_err = uj.bounded_git_probe(path, "diff-index", "--quiet", "HEAD", "--")
4334:         if stamp is None: → refuse "stamp-absent: …"
4343:         if not floor_commit: → refuse "floor-unset: …"
4352:         anc_rc, _, anc_err = uj.bounded_git_probe(path, "merge-base", "--is-ancestor", floor_commit, "HEAD")
4355:         if anc_rc == 0: → "arm", "floor-ancestor-ok: …")
4360:         if anc_rc == 1: → refuse "stale-scripts: HEAD … does NOT contain the floor commit …"
4368:         → refuse "floor-object-unknown: …"
```

Floor value threaded in at `upgrade_tools.py:3940 floor_commit=(PIPELINE_SCRIPTS_FLOOR_COMMIT or None)`;
`floor-unset` refusal detail at `:4346`. Non-git pinned dirs are WARNING-ONLY (comment `:4377-4379`).
Escape hatch: `ENSEMBLE_ALLOW_STALE_SCRIPTS=1` (per-invocation, named at `:4349`).

### Floor semantics

The floor is the commit whose **ancestry of the pinned scripts dir's HEAD** proves the scripts tree
contains the ceremony-relevant fix. It is checked at ARM time (daemon, before the nonce burn — see the
`promote.sh:204-210` comment: the executor echo "gates nothing"), against the **scripts dir** HEAD (which
may differ from the release tree). It is validated at RELEASE time (stage.sh) with
`merge-base --is-ancestor <floor> HEAD`, i.e. the floor must name a commit **contained in the tree being
built**.

### Chicken-and-egg (stated as fact, with the resolution the code implies)

`PIPELINE_SCRIPTS_FLOOR_COMMIT` lives in `daemon/tools/upgrade_tools.py` and `PIPELINE_SCRIPTS_REV` in
`scripts/upgrade/lib.sh` — both inside the very tree the floor gates — plus stage.sh additionally
requires the stamp to COVER every shipped executor-family path (`:817-828`,
`executor_family_verify_stamp_coverage`) and to EQUAL the floor (`:748-751`).

Consequences mechanically visible in the code:
- A commit that changes `scripts/upgrade/**` cannot name ITSELF as the floor (a sha is content-addressed;
  the value must be written after the commit exists).
- The valid ordering is therefore: land/merge the `scripts/upgrade/**` change → then a FOLLOW-UP
  release-bump commit sets BOTH constants to that merge commit's sha. That satisfies
  `merge-base --is-ancestor` (the merge commit is an ancestor of the later bump commit / of any tree
  containing it) and `stamp == floor`.
- Shipping the fix in the same single commit is refused: `stage.sh:775-779` (`floor` unresolvable or not
  ancestor) and `:796-800` (stamp≠floor) both `exit 78`.
- The dev-lane danger the floor exists for: setting it too LOW keeps `stale-scripts` honest; setting it
  to a commit that does not contain the change is the "no arm can ever succeed"/"fix silently missing"
  class named at `stage.sh:756-772` and `upgrade_tools.py:4360-4367`.
- `executor_family.sh:310-313` documents the re-pin obligation for family additions:
  "re-pin PIPELINE_SCRIPTS_REV + PIPELINE_SCRIPTS_FLOOR_COMMIT to …".

Also relevant for a fix that touches `lib.sh` log text: `tests/packs/preflight_size_guard_test.sh`
exercises script sizes, and `tests/test_ceremony_wallclock.sh` asserts knob defaults source-textually
(see `.agents/tester/RESULTS/2026-11-03-promote-handback-halt-functional-gate.md:38` — "11 knobs ×
(shipped-default source-text + resolved-value + table-equality)").

---

## Q10 — GNU/BSD dispatch idioms to follow (portability for fix B's pid census)

Existing `uname -s` dispatch patterns in `lib.sh` (grep `uname -s`):

| Site | Shape |
|---|---|
| `lib.sh:503-528` `_cooldown_until_iso` | `case "${_COOLDOWN_DATE_UNAME:-$(uname -s)}" in Darwin\|*BSD*\|*bsd*) … ;; Linux\|GNU*\|*GNU*) … ;; *) refuse fail-closed` — **note the test-only override variable** (`:494-497`: "SELECTION IS OBSERVABLE (test-only)") |
| `lib.sh:940-956` `_iso_to_epoch` | same `case … in Darwin\|*BSD*\|*bsd*) / Linux\|GNU*\|*GNU*) / *) echo "unrecognized platform … refusing (fail-closed)" >&2; return 1` |
| `lib.sh:2529` `atomic_flip` | same three-arm case; comment at `:5362-5379` |
| `lib.sh:5417-5421` `_supervision_host_allows_unit` | `[ "$(uname -s)" = "Linux" ] \|\| return 1; [ -d /run/systemd/system ] \|\| return 1` |
| `lib.sh:5445-5447` `_supervision_host_allows_launchd` | `[ "$(uname -s)" = "Darwin" ]` |
| `lib.sh:6667-6669` restart comp7 arm | inline `[ "$(uname -s)" = "Linux" ] && [ -d "/run/systemd/system" ] && [ -n … ]` |
| `lib.sh:8563-8567` retention stat seat | `case "$(uname -s)" in Darwin\|*BSD*\|*bsd*) stat -f … ;; Linux\|GNU*\|*GNU*) stat -c … ;; *) st=""` |

Documented convention (`lib.sh:934-939`): dispatch on `uname -s` rather than `||`-chaining families,
because "on BSD a REAL -j failure would otherwise hit the GNU form and surface a wrong-platform error;
Unrecognized platforms refuse (fail-closed; we never guess)". Both shell helpers live behind
`_supervision_host_allows_unit`/`_launchd` for host-gating (`:5413-5416`, `:5442-5444`: uname check
runs FIRST so non-Linux/non-Darwin hosts take zero `/proc`/`/run`/`launchctl` reads).

Test seam idiom for `uname`: a PATH-injected `uname` stub that delegates to `$REAL_UNAME` for other
flags — `tests/test_atomic_flip.sh:229-237` and `:265-269` (`case "$1" in -s) echo "Plan9"/"Darwin" ;; *)
"$REAL_UNAME" "$@" ;; esac`).

---

## Q11 — Bash-lane test harnesses

### `tests/test_release_journal.sh` (3183 lines)

- Fixture strategy documented at `:1-28`: "builds a THROWAWAY repo with the real scripts + stub payload
  trees, `git init` + tag it, and runs the real code against throwaway install dirs. HOME is overridden
  so demo/live topology resolution can never touch the real host dirs."
- Shared helpers: `. "$REPO_ROOT/tests/upgrade_test_lib.sh"` (`:33`); `_register_fixture` at
  `tests/upgrade_test_lib.sh:33` (+ `trap _cleanup_fixtures EXIT` at `:38`); `setup_fake_home` /
  `teardown_fake_home` live in that same lib (`tests/upgrade_test_lib.sh:59+`, F1 fence).
- Fixture build: `FAKE_REPO`/`FAKE_HOME` trees (`:67-80`), stub plugin predicate (`:99-119`), fake live
  `.env` (`:124`), the whole EXECUTOR FAMILY copied via `. "$UPGRADE_DIR/executor_family.sh"` +
  `executor_family_paths` (`:145-150`).
- **No systemctl/journald stubbing here at all** (grep `systemctl|STUB|PATH=` in that file: zero hits) —
  it exercises journal/manifest/stage/promote-preflight logic, not supervision.
- Real HTTP stub: inline `python3 - "$SRV_PORT"` `http.server` on port 18399 serving
  `/livez` (`{"status":"alive","uptime_seconds":1,"version":"9.9.9-stub"}`) and `/readyz`
  (`:1114-1136`), then `SRV_PID=$!; sleep 1` (`:1137-1138`).
- **Attribution seam pattern (reusable for fix B):** because the python stub is not ensemble-shaped,
  `RJ_OWNED_SEAM='_supervision_owned_pids() { lsof -ti:"$PORT" -sTCP:LISTEN 2>/dev/null; }'`
  (`:1147`) is `eval`'d inside a subshell after sourcing lib.sh (`:1148`, `:1153`) — i.e. the pack
  overrides the owned-set function so the census speaker IS the owned daemon. Documented rationale at
  `:1139-1146`.
- Registered/invoked: `tests/test_upgrade_overhaul_all.sh:184-185` runs it **only under `--full`**
  (D1 "ADJUDICATED", with a standing pre-existing-failure disclosure at `:24-34`, `:186-196`). Direct
  invocation is `bash tests/test_release_journal.sh` (`:8`).
  `.agents/tester/PACKS.md` registration: **NOT-FOUND** (grep for `release_journal`, `handback`,
  `bounded_subprocess`, `watchdog`, `supervision` across the 5091-line PACKS.md — zero hits).

### `tests/test_stage_freshness_guard.sh` (1101 lines)

- Same shape (header `:22-33`): throwaway git repo + real `stage.sh` + stub payloads, HOME overridden,
  "Portability: bash 3.2 / BSD-safe. Uses shasum (not sha256sum)". Fixture build at `:75-80+`.
- **No systemctl / uname stubbing** in this file either (grep zero hits) — it drives stage/backup/
  provenance logic.
- Registration: same NOT-FOUND result in PACKS.md; invoked directly
  (`bash tests/test_stage_freshness_guard.sh`, `:32`) and NOT wired into `test_upgrade_overhaul_all.sh`
  (grep: zero hits there). Note `docs/runbooks/upgrade-drills.md:684` describes it as "the freshness-guard
  test pack (10 scenario groups…)" and
  `.agents/tester/RESULTS/2026-10-05-stage-freshness-guard-pre-merge-verification.md:75` recorded
  finding "F-2 … unregistered in `.agents/tester/PACKS.md` — must be registered at merge"; the grep
  result shows it is still unregistered.

### Reusable fake-pid / fake-socket fixture patterns (for testing fixes A/B/C)

| Pattern | Location | What it gives you |
|---|---|---|
| `make_systemctl_stub <dir>` with the `SC_*` contract (`SC_IS_ACTIVE`, `SC_IS_ACTIVE2/_N`, `SC_MAINPID`, `SC_MAINPID2/_N`, `SC_START_RC`, `SC_START_ERR`, `SC_STOP_RC`, `SC_LOG`, `*.killed`) | `tests/packs/handback_verify_budget_test.sh:202-250`; `tests/test_bounded_subprocess_waits.sh:88`; `tests/test_supervision_stop_handback.sh:104` | Scriptable `is-active` / `show -p MainPID` / `start` / `kill` / `reset-failed`, including rc-3 inactive answers and per-call changing MainPID |
| `make_lsof_stub <dir> <rc>` printing pid `424242` | `tests/packs/handback_verify_budget_test.sh:252-270` (rationale `:257-265`: rc-0-EMPTY would be read as "no listener" by attribution) | Port-holder census replacement |
| Call-counting / delayed `lsof` stub (`GD_DELAY_CALLS`, `GD_HOLDERS`, `GD_NEVER`, counter file) | `tests/packs/handback_verify_budget_test.sh:86-109` + `gd_run` `:110-122` | Proves a grace WAITED (counter-token pin, `:133-137`, `:155-160`) |
| `make_curl_stub <dir>` (`CURL_MODE=ok|wrongver|noversion|non2xx`, `CURL_DELAY_S`, `CURL_START_EPOCH`) | `tests/packs/handback_verify_budget_test.sh:272-309` | /livez classification + slow-boot |
| Owned-set function override seam (`_supervision_owned_pids() { printf "%s\n" 424242; }`) | `tests/packs/handback_verify_budget_test.sh:386-392`; `tests/test_release_journal.sh:1147` | Makes the stub the "owned daemon" without touching pgrep/ps |
| Slow-boot end-to-end legs with PATH-shadowed lsof+curl and a shared clock | `tests/test_supervision_stop_handback.sh:1270-1329` (`run_slow`, `run_probe`, `LIVEZ_BUDGET_S=2`, `HANDBACK_VERIFY_BUDGET_S=<n>`, `SUPERVISION_*` set inline before `restart_via_launcher`) | The closest existing harness to the incident shape (unit arm + delayed serve) |
| Real listener on a real port | `tests/test_release_journal.sh:1114-1159` (python http.server) | Genuine LISTEN socket for census tests |
| `uname` PATH stub delegating to `$REAL_UNAME` | `tests/test_atomic_flip.sh:229-237`, `:265-269` | Darwin/Linux arm selection on a GNU host |
| FAKE_HOME fence + fixture registry | `tests/upgrade_test_lib.sh:32-38`, `:59+` | Live-install safety for any new pack |

Gap relevant to fix B: **no existing fixture models an inherited-LISTEN-fd child** (nothing in
`tests/` stubs a child whose listening socket was inherited from a parent; the closest is the delayed
lsof stub, which fakes the holder pid outright). A new fixture would need either a real forking server
(parent binds, child inherits) or an lsof stub that reports a child pid NOT present in the owned set
(the current stubs all report the same pid they arm as owned).

---

## Drift table (lead → actual, as they exist NOW)

| Commission lead | Actual anchor / finding |
|---|---|
| `lib.sh:6338` (journal label for rc) | `lib.sh:6337-6339` reset-failed site; **hard-codes `rc=124`** — the strongest mislabel, not the incident one |
| `lib.sh:6356-6359` | `lib.sh:6356-6360` — confirmed, the `isact_rc -ne 0` conflation (incident shape) |
| `lib.sh:6699` | `lib.sh:6699-6701` — confirmed (`site=scope_arm_is_active`) |
| `lib.sh:6093-6140` green-class fail-fast | `lib.sh:6093-6133` (`_handback_serving_probe`); returns-1 sites `:6105`, `:6111`, `:6115`, `:6125`; caller fail-fast returns at `:6234`, `:6256` |
| 180 s verify floor / +38 s halt | Floor = `lib.sh:234` (knob) / `:6182` (clamp) / `:6288-6297` (floor-band verdicts). "+38 s" **NOT-FOUND** as a code artifact (fail-fast path consumes no budget) |
| `lib.sh:6921-6955` `_gate_serving_attribution` | `_gate_serving_attribution` is `lib.sh:8004-8092`; `:6921-6955` is `_supervision_owned_pids` (the lead's range names the RIGHT function for the pid ladder, WRONG one for the gate) |
| `:6394-6397` systemd unit-arm zero retry | confirmed `lib.sh:6377` (single start) + `:6395-6397` (single verify, `return 1`) |
| `:6539-6553` launchd retries | actual loop `lib.sh:6541-6557` (`while [ "$lattempt" -le 2 ]`, `sleep $(( lattempt * 2 ))`) |
| `sweep.py:405` advisory produced | confirmed `daemon/services/upgrade_journal_sweep.py:405` → emitter `:407-465`, event append `:449-451` |
| `adopt-unit.sh:478-492` advisory consumed | confirmed `scripts/upgrade/adopt-unit.sh:477-497` (only consumer in `scripts/**`) |
| `lib.sh:5634-5635` HB_ERR launchd-only | confirmed; second writer at `:5720-5721`; no systemd-lane writer anywhere |
| `lib.sh:6695-6702` `scope_arm_is_active` | **not a function** — inline code in `restart_via_launcher` `lib.sh:6695-6703`, site label at `:6700` |
| `promote.sh:676-680` "SUPERVISION_* frozen vars" | **no frozen/exported vars**; `:673-683` is a comment block. Real pins = 7b substring trap + 7c frozen wording (test-pinned) and the non-exported plain-variable channel (`:329-336`, `:546-556`) |
| `PIPELINE_SCRIPTS_FLOOR` / scripts-stamp constants | `daemon/tools/upgrade_tools.py:4145` (floor) + `scripts/upgrade/lib.sh:95` (stamp), both `1aa0065eb818ff00cb32730fab54d7f2060c42d8`; validators `lib.sh:738`/`:773`; stage gates `stage.sh:774-800`, coverage `:817-828`; arm-time guard `upgrade_tools.py:4326-4375` |
| "uname-based dispatch helpers" | `lib.sh:505` (`_COOLDOWN_DATE_UNAME` override), `:942`, `:2529`, `:5418`, `:5446`, `:6667`, `:8563`; test seam `tests/test_atomic_flip.sh:229-237` |
| `tests/test_release_journal.sh` / `test_stage_freshness_guard.sh` fakes | neither stubs systemctl/journal; both use throwaway git repo + FAKE_HOME + real scripts; `test_release_journal.sh` uses a real `python3 http.server` /livez stub (`:1114-1136`) and the `RJ_OWNED_SEAM` function override (`:1147`). Supervision stubs live in `tests/packs/handback_verify_budget_test.sh` / `tests/test_supervision_stop_handback.sh` / `tests/test_bounded_subprocess_waits.sh` |
| Pack registration | PACKS.md registration for these upgrade packs: **NOT-FOUND** (grep zero hits for release_journal / stage_freshness / handback / supervision / watchdog / bounded_subprocess in `.agents/tester/PACKS.md`) |

### Explicit NOT-FOUND list
1. A code constant or artifact producing the "+38 s" halt timing (only the fail-fast + observed
   wall-clock explain it).
2. A persisted "pre-stop classification store" other than the shell globals, the additive txn stamp
   (`journal_mark_supervision`) and the two `SUPERVISION_PRESTOP_*`/plist-path stashes.
3. Any systemd-lane write to `HANDBACK_LAST_ERROR` / `HANDBACK_ERROR_FILE`.
4. Any use of `ss`, `fuser`, `netstat`, `/proc/net/tcp`, or `readlink /proc/<pid>/fd` anywhere in
   `scripts/` (so fd-provenance inspection would be net-new).
5. Any halt-path read of the `supervision_boot` journal event.
6. Registration of `test_release_journal.sh` / `test_stage_freshness_guard.sh` /
   `handback_verify_budget_test.sh` in `.agents/tester/PACKS.md`.
