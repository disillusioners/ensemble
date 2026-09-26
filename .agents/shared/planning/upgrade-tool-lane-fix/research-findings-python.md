# Research Findings — tool-lane live promote fix commission (v0.15.3)

**Branch inspected:** `feature/upgrade-tool-lane-fix` (confirmed via `.git/HEAD`: `ref: refs/heads/feature/upgrade-tool-lane-fix`). Base latest @ 139ba352 (v0.15.2). All line numbers below are ACTUAL current lines on this branch.
**Method:** read-only file reads + greps. No builds, no tests run.

---

## 1. DIAGNOSIS CONTEXT

### 1a. Critical note a9371b75 detail_ref / history 91ea4646 — NOT-FOUND as repo files

- Grep for `a9371b75`, `91ea4646`, `r-20260926-092839-5bf2` across `.agents/`, `docs/`, `scripts/`, `data/`, and repo root: **ZERO matches** (data/ grep noted 4-5 oversized files >1.4MB skipped; token too rare to be in code).
- **Verdict: NOT-FOUND in the repo.** These ids live in the ensemble critical-notes / project-history SYSTEM (external DB), not as files. The diagnosis content survives in two carriers:
  1. The **KB shared-context experience file** (the only context file, written 2026-09-26T09:59Z): `agents-ensemble-upgrade-journal-architecture-verified-2026-0_20260926_095916_experience.md`. Key verified claims (quoted):
     - "`pending_op` (armed op) and `pending_actions` (nonce registry) are SEPARATE root keys in releases/state.json."
     - "pending_op auto-clears ONLY via reconcile_pending_op at system_upgrade/system_restart TOOL ENTRY (terminal-event branch, or expiry at armed_at+600s+600s) — no boot-time or timer sweep."
     - "EXECUTOR_ENV_ALLOWLIST (upgrade_journal.py:988) unconditionally strips ENSEMBLE_UPGRADE_LIVE from the daemonized executor child (F2 fence, test-pinned \"poison\") and the executor argv never carries --f2-verified-closed → tool-lane live promote is structurally impossible on these builds."
     - "Executor child exits are invisible to the daemon (no wait(), no journal event — manager.py:3773-3775); their only trace is data/upgrade.log (parent-created before Popen)."
     - "upgrade_status outcome derives from the last history event of ANY type (_terminal_outcome, no class filter) → shows TERMINAL at nonce_consumed while pending_op is still armed."
  2. The **critical note itself** (system-context copy): "(a) executor child exit-78 unseen by daemon (no wait/journal, manager.py:3773-75); allowlist strips ENSEMBLE_UPGRADE_LIVE unconditionally (upgrade_journal.py:988); argv never carries --f2-verified-closed (manager.py:3812-15); nonce_consumed ∉ _TERMINAL_EVENTS → status TERMINAL while pending_op armed; reconcile tool-entry-only → stale pending_op starves re-arm (~20min observed, arm r-20260926-092839-5bf2)."

### 1b. Failure sequence (synthesis from critical note + KB file + code)

1. `system_upgrade(live, dry_run=false, user_confirmed=true, nonce=…)` passes the 3-factor gate → burns nonce (`consume_pending_action`, upgrade_tools.py:2686-2688) → writes `PendingOp` with `nonce_consumed=true` (:2702) → sets marker spec (:2711-2723) → tool returns ARMED.
2. Turn ends → `drain_pending_system_execution` (manager.py:3762) fires `promote.sh` via `spawn_executor` **without `ENSEMBLE_UPGRADE_LIVE` in the child env** (not in `extra_env` :3795-3799; ambient inheritance stripped by `EXECUTOR_ENV_ALLOWLIST` upgrade_journal.py:988-991) **and without `--f2-verified-closed` in argv** (:3812-3815).
3. promote.sh live hits `require_live_guard` (lib.sh) → `_refuse` → **exit 78**.
4. Daemon never `wait()`s the child; no journal event written on spawn failure (manager.py:3778-3781, :3843-3846). Exit-78 invisible.
5. `nonce_consumed` is not in `_TERMINAL_EVENTS` (upgrade_journal.py:890) and `_terminal_outcome` (upgrade_tools.py:1021-1036) takes the LAST history event of ANY type → `nonce_consumed` event maps to label "live-confirmation nonce consumed" (:1047) → upgrade_status reports TERMINAL while `pending_op` stays armed.
6. `reconcile_pending_op` runs ONLY at tool entry (upgrade_tools.py:2027, :2293) → no terminal event and no expiry for 600s+600s grace → stale pending_op blocks re-arm for ~20min (re-arm refused `pipeline-busy` :2630-2635).

### 1c. Explicit constraints from the ratified policy (user verdict 2026-09-26)

- ITEM-7 RESOLVED — RATIFIED POLICY (single branch): user nonce-echo via registered source = the F2-equivalent attestation for tool-lane live promotes; the 3-factor-verified arm itself IS the attestation source; no additional gate. Plan.md decision table must be RETAINED with ratified outcome marked (audit history).
- Feature-first, revisit if threat model changes.

---

## 2. ANCHOR VALIDATION (actual lines on this branch)

### 2a. daemon/manager.py (11,665 lines)

- `drain_pending_system_execution` — **:3762**. Docstring "no halt journal event is written here" — **:3778-3781** (caller cited ~:3773-75; drift +5):
  > `3778:  Never raises — a spawn failure is logged as a warning and the`
  > `3779:  marker is still consumed (no halt journal event is written here;`
  > `3780:  the journal pending_op remains the durable fallback for the`
  > `3781:  boot sweep).`
- `extra_env` construction — **:3795-3799**:
  > `3795:  extra_env: dict[str, str] = {}`
  > `3796:  if install_dir:`
  > `3797:      extra_env["INSTALL_DIR"] = str(install_dir)`
  > `3798:  if spec.get("port"):`
  > `3799:      extra_env["PORT"] = str(spec["port"])`
- argv construction — promote **:3808-3815** (caller cited ~:3812-15 for the argv list — the list literal is :3812-3815):
  > `3808:  elif kind == "promote":`
  > `3809:      # Handoff: promote.sh acquires the lock itself at preflight;`
  > `3810:      # releasing here keeps exactly one lock holder at a time.`
  > `3811:      _uj.lock_release(install_dir)`
  > `3812:      argv = [`
  > `3813:          "bash", str(scripts_dir / "promote.sh"), env,`
  > `3814:          "--version", str(spec.get("target", "")),`
  > `3815:      ]`
  - **NO flag slot exists**: no f2 flag, no note flag, no extension point. Restart argv: :3801-3807 (`restart.sh env --run-id … [--reason …]`).
- Spawn — **:3823**: `child_pid = _uj.spawn_executor(argv, install_dir, extra_env)`; child pid stamped into pending_op as owner at **:3831-3837** (`op.owner_pid = child_pid; op.owner_kind = "executor"; op.trigger = "post-turn-callback"`). Catch-all warning **:3843-3846** (never raises).

### 2b. daemon/tools/upgrade_journal.py (1,214 lines)

- `journal_history_append` — **:301-316** (terminal-class SSE rider after durable write; `nonce_consumed` listed as emit-NOTHING ordinary event in comment :380-383).
- `_TERMINAL_EVENTS` — **:890**:
  > `_TERMINAL_EVENTS = ("commit", "rollback", "halt", "sweep_rollback", "sweep", "quarantine")`
  **`nonce_consumed` ∉ tuple — defect (d) confirmed verbatim.**
- `reconcile_pending_op` — **:909-980** (caller cited ~:957-979 = expiry branch only). Signature `def reconcile_pending_op(install_dir: Path) -> str | None` (:909). Behavior: reads journal (:919), returns None if op None or kind=="restart" (:922-924), returns None if in_flight live (:925-927), closes on terminal event ≥ armed_at (:928-956), expiry-closes past `expires_at + RECONCILE_GRACE_S` (:957-979). **No lock is taken by reconcile itself** — closure writes ride the additive journal splice; never-raises; READ-FIRST discipline (:915-917).
- `EXECUTOR_ENV_ALLOWLIST` — **:988-991**:
  > `988: EXECUTOR_ENV_ALLOWLIST: tuple[str, ...] = (`
  > `989:     "PATH", "HOME", "INSTALL_DIR", "PORT", "POSTGRES_DB", "TMPDIR",`
  > `990: )`
  > `991: EXECUTOR_ENV_PREFIXES: tuple[str, ...] = ("PG",)`
- `executor_env` — **:994-1005** (VERIFIER POINT B — fresh dict, allowlist never inherits ambient beyond the list; **explicit `extra` merged LAST and unconditionally**, :1003-1004):
  > `994: def executor_env(extra: dict[str, str] | None = None) -> dict:`
  > `995:     env: dict[str, str] = {}`
  > `996:     for key in EXECUTOR_ENV_ALLOWLIST:`
  > `997:         val = os.environ.get(key)`
  > `998:         if val is not None:`
  > `999:             env[key] = val`
  > `1000:    for key, val in os.environ.items():`
  > `1001:        if any(key.startswith(p) for p in EXECUTOR_ENV_PREFIXES):`
  > `1002:            env[key] = val`
  > `1003:    for key, val in (extra or {}).items():`
  > `1004:        env[key] = str(val)`
  > `1005:    return env`
  **Consequence: `extra_env={"ENSEMBLE_UPGRADE_LIVE": "1"}` WOULD reach the child — the fence strips AMBIENT inheritance only; tool-supplied extras always pass. The verified-arm passthrough can therefore come from pending_op fields via extra_env while the F2 fence stays intact for every unverified path.**
- `spawn_executor` — **:1012-1037**: Popen with `start_new_session=True` (:1034), `env=executor_env(extra_env)` (:1033), stdio → `data/upgrade.log` (:1024-1030), deliberately NOT in any teardown registry (:1018-1022). Returns pid only — no wait, no exit capture (defect (a) seam).
- User-origin classification — the old static `USER_ORIGIN_SOURCES` whitelist is GONE as code; caller anchors ~:1038-1092 now land in the **HISTORY comment block** (:1040-1129) documenting the registry-backed replacement (merge 4db90d74). Live code:
  - `_USER_ORIGIN_EXACT = frozenset({"api"})` — **:1130**
  - `USER_ORIGIN_CHAT_SOURCE_TYPES = frozenset({"telegram", "slack", "discord", "whatsapp"})` — **:1134-1136**
  - `user_origin_sources_display()` — **:1139-1149**
  - `classify_user_origin(source, registry_get) -> tuple[bool, str]` — **:1152-1202**; fail-closed detail tokens: `no-source`, `exact:api`, `reserved-internal`, `empty-segment`, `registry-unavailable`, `registry-error:<Exc>`, `unregistered`, `source-type-not-chat:<rendered≤40>`, `registered-chat:<type>` (returns True only on the last).
- `PendingOp` dataclass — **:695-729**. Fields (verbatim, :699-716): `run_id: str`, `kind: str`, `env: str`, `target: str|None`, `mode: str|None`, `reason: str`, `armed_at: str`, `armed_by_instance: str`, `owner_pid: int`, `owner_kind: str`, `owner_heartbeat_at: str|None`, `trigger: str`, `nonce: str|None`, `nonce_consumed: bool`, `confirmed_by_human: bool`, `confirmed_source: str|None`, `flipped: bool`, `expires_at: str`. **The verified-arm record IS the pending_op: `nonce_consumed`, `confirmed_by_human`, `confirmed_source` are literal persisted fields** (written upgrade_tools.py:2702-2704; read via `read_pending_op` :740-745 — including manager.py:3832).
- `mint_run_id` — **:149-152**: `r-<yyyymmdd-HHMMSS>-<4hex>` ("cross-death join key") → arm ids like `r-20260926-092839-5bf2` are **arm ids, NOT job ids**. Nonce format `CONFIRM-` + 8 base32 (:155-158).
- Expiry constants — `PENDING_OP_EXPIRE_RESTART_S = 30*60` :104; `PENDING_OP_EXPIRE_PROMOTE_S = 10*60` :105.

### 2c. daemon/tools/upgrade_tools.py (2,806 lines)

- `_terminal_outcome` — **:1021-1036** (caller ~:1021-1040 ✓). Returns the LAST history entry of ANY type (no class filter — defect (d) surface):
  > `1033:    for entry in reversed(history):`
  > `1034:        if isinstance(entry, dict) and entry.get("event"):`
  > `1035:            return str(entry["event"]), entry`
- `_OUTCOME_LABELS` — **:1039-1048** (caller ~:1043-1052, small drift). Includes `"nonce_consumed": "live-confirmation nonce consumed"` (:1047).
- `_journal_refusal_event` — **:1056+**; detail format `"<msg> (reason=<token>)"` (:1090-1093) parsed by `_reason_token` (upgrade_journal).
- Reconcile tool-entry call sites: **system_restart :2027** ✓ and **system_upgrade :2293** ✓ — both wrapped `except Exception` best-effort, never gate (:2028-2033, :2294-2299). These are the ONLY entries (KB: "no boot-time or timer sweep").
- `system_upgrade` signature — **:2271-2277**: `(target_env, version=None, user_confirmed: bool = False, dry_run: bool = True, nonce: str | None = None)`.
- **3-factor live gate** — **:2452-2620** (live branch of armed path; `if self_env == "live":` :2457):
  - Factor 1: `if not user_confirmed:` → `user-confirmation-missing` (:2459-2463).
  - Factor 2: reads `manager._user_origin_windows` (:2465-2468); missing → W1 observed-source diagnostic from `manager._user_origin_last_stamp` (:2475-2496); expired/unparseable → fail-closed (:2498-2509); **valid → `confirmed_source = window.get("source")` :2511, `confirmed_msg_id = window.get("message_id")` :2512**.
  - Factor 3 + nonce validation (:2514-2620): nonce-mismatch (:2525), nonce-already-used (:2530), **nonce-instance-mismatch** (:2542), nonce-expired (:2558), **nonce-action-mismatch** (:2575), nonce-verification-unavailable (:2603 — MessageQueue row content must contain the nonce, single-row read :2592-2600).
  - Gate failure → refusal detail + guidance `user_confirmed=true, nonce="{nonce_grouped(action.nonce)}"` (:2438).
- **Arm path**: run_id carry — **:2672-2676** (`confirmed_action.run_id if confirmed_action is not None else mint_run_id()`); lock acquire **:2677**; **nonce burn AFTER lock** — **:2685-2688** (`uj.consume_pending_action(install_dir, confirmed_action, confirmed_msg_id)`) — caller anchor ~:2668-2672 is the comment explaining burn-after-lock ("a busy-lock race refusal never wastes the nonce" :2669-2671). `journal_init` :2689, `ensure_extensions` :2690.
- **PendingOp construction (VERIFIED ARM RECORD)** — **:2691-2706**:
  > `2691: op = PendingOp(`
  > `2692:     run_id=run_id,`
  > `2693:     kind="promote",`
  > `2694:     env=self_env,`
  > `2695:     target=version,`
  > `…`
  > `2701:     nonce=confirmed_action.nonce if confirmed_action else None,`
  > `2702:     nonce_consumed=confirmed_action is not None,`
  > `2703:     confirmed_by_human=confirmed_source is not None,`
  > `2704:     confirmed_source=confirmed_source,`
  > `2705: )`
  > `2706: uj.write_pending_op(install_dir, op)`
  Failure → lock released + error :2707-2709.
- **Execution marker spec** — **:2711-2723**: `{kind:"promote", env, run_id, target, install_dir, scripts_dir, port}`. **NOTE: the marker does NOT carry confirmed_by_human / confirmed_source / nonce — the drain would need to read the pending_op (already loaded at manager.py:3832) or the spec must be extended.**
- Arm reply includes `live-confirmation: nonce consumed (confirmed_source={confirmed_source})` — **:2731-2732**.
- **Refusal tokens observed (verbatim `_refusal` label args, upgrade_tools.py)**: `no-staged-install` (:2038, :2304), `journal-unavailable` (:2045, :2311), `pipeline-busy` (:2059, :2068, :2076, :2632, :2653, :2681), `restart-under-burst-abort` (:2083), `live-restart-refused` (:2011), `layout-divergence` (:2329), `target-not-staged` (:2339, :2358), `target-quarantined` (:2350), `manifest-unsafe` (:2366), `cooldown-active` (:2646), `executor-scripts-unavailable` (:2660), `env-self-match` (:1001, :1012), `user-confirmation-missing` (:2461, :2483, :2491, :2505, :2517), `nonce-mismatch` (:2525), `nonce-already-used` (:2530), `nonce-instance-mismatch` (:2542), `nonce-expired` (:2558), `nonce-action-mismatch` (:2575), `nonce-verification-unavailable` (:2603). Shell-side adds `f2-not-verified` (promote.sh:104) + `pipeline-busy` + live-guard tokens (lib.sh `_refuse`). **No single constant list exists — tokens are per-call-site strings; the KB's "~21-token taxonomy" is the union above.** Pin style: regex-equality on the `reason=<token>` fragment in tests (per blueprint §Tests; `_reason_token` parses the D-FA2.2 token).
- `release_info` user-origin drift probe also consults `classify_user_origin` (single-source-of-truth comment upgrade_journal.py:1120-1123).

### 2d. daemon/tools/job_queue.py — job-source anti-forgery (DRIFTED)

Caller anchor :526-538 is **stale** — that region is now the `job_progress`/`job_inject` docstrings (:515-554). The anti-forgery override lives at:
- **:1256-1257**: `f"agent:{caller_agent_id}"` (inside the enqueue path; `caller_agent_id = agent_id` :1171)
- **:1993**: `source=f"agent:{caller_agent_id}" if caller_agent_id else "internal_agent:unknown",`
Pattern (server-side derivation — LLM/caller-supplied `source` never trusted verbatim on this path; matches docs/self-upgrade-live-rung-parked-decision.md:62 which cites the template as `daemon/tools/job_queue.py:527-533` — **that doc citation is also stale**).

### 2e. VERIFIED ARM RECORD shape (summary table)

| Field | Type | Written at | Read at |
|---|---|---|---|
| `nonce` | str \| None | upgrade_tools.py:2701 (`confirmed_action.nonce`) | journal pending_op; manager.py:3832 (read_pending_op) |
| `nonce_consumed` | bool | :2702 (`confirmed_action is not None`) | reconcile terminal check NOT (not in _TERMINAL_EVENTS — the defect); upgrade_status label :1047 |
| `confirmed_by_human` | bool | :2703 (`confirmed_source is not None`) | pending_op journal only |
| `confirmed_source` | str \| None | :2704 (`window.get("source")`, set :2511) | arm reply :2731; pending_op journal |
| `run_id` | str (`r-…`) | :2672-2676 (carried from nonce's run_id or minted) | argv, marker spec, pending_op, lock |

---

## 3. TEST-PIN INVENTORY

### tests/unit/tools/test_upgrade_journal.py (1,452 lines) — class TestExecutorSpawn

- **`test_env_allowlist_pure_function` :867-908** (verifier anchor :867 ✓). Freezes: allowlist membership + poison exclusion + structural containment. Poisons `ENSEMBLE_UPGRADE_LIVE, OPENAI_API_KEY, ANTHROPIC_API_KEY, DATABASE_URL, ENSEMBLE_SELF_ENV` (:877-881); asserts explicit extras pass (`env["RUN_ID"] == "r-1"` :892 — **the extras-passthrough pin**); structural assertion :901-908 (`key in EXECUTOR_ENV_ALLOWLIST or startswith PG-prefix or in extras`). **Style: equality + structural iteration (NOT AST).** Two-sided flip: if extras gain `F2_VERIFIED_NOTE`, add to `extras` set :902 (or the test fails on the structural check).
- **`test_real_spawn_env_and_process_group_independence` :910-977** (verifier anchor :910 ✓). Real child via `spawn_executor`; poison setenv `ENSEMBLE_UPGRADE_LIVE=1` :935; asserts child-env dump has no `OPENAI_API_KEY` :961 and no `ENSEMBLE_UPGRADE_LIVE` :962 (**poison strips** ✓); child leads own process group (:944-946); stdio in `data/upgrade.log` (:963-967).
- `test_static_no_bash_process_registry_reference` :979 — static T5 assertion (child must survive teardown; not argv-related).

### tests/unit/tools/test_upgrade_tools.py (3,444 lines)

- **`test_promote_kind_lock_handoff_before_spawn` :3367-3396** (verifier anchor :3367 ✓ = **THE ARGV LOCK**):
  > `3387: [call] = spawn_calls`
  > `3388: assert call["argv"] == [`
  > `3389:     "bash", str(scripts_dir / "promote.sh"), "demo",`
  > `3390:     "--version", "1.2.3",`
  > `3391: ]`
  **Style: exact-list equality.** Adding `--f2-verified-closed` / `--f2-note=…` to the promote argv REQUIRES updating this pin (two-sided contract). Same file pins the restart argv (earlier test, tail visible :3320-3365: pending_op flips to executor identity :3360-3365).
- **`test_spawn_extra_env_composes_to_allowlist_only` :3416-3444** (verifier anchor :3416 ✓; poison section :3423-3434 ✓ — `monkeypatch.setenv("ENSEMBLE_UPGRADE_LIVE","1")` :3423, composed-exclusion :3434-3436). Bridges drain→real env: `composed = uj.executor_env(call["extra_env"])` :3433; final subset assertion **:3444**: `assert set(composed) <= allowed` where `allowed = EXECUTOR_ENV_ALLOWLIST ∪ {"INSTALL_DIR","PORT"} ∪ PG-prefixed-os.environ` (:3439-3443). **Two-sided flip: any new extra_env key (e.g. `F2_VERIFIED_NOTE`) must be added to `allowed` here or this fails.**
- `test_unknown_kind_refused_no_spawn` :3398-3411; `test_no_marker_returns_false` :3413-3414 (drain contract pins).

### tests/test_release_journal.sh (shell journal suite)

- **:176-200 — MINOR-4b promote live F2 gate**: `:188 assert_contains "f2 gate: refusal names the flag" "--f2-verified-closed" "$out"`; `:200` live promote WITH the flag proceeds past the f2 gate. **Style: string-contains on promote output.** Any drain-side argv change must keep this green (flag still honored; refusal text unchanged).

### test/packs/

- **drill_ledger_unit_test.sh** — ledger-checker F2 pins: T4 F2-open ⇒ BLOCKED regardless of count (:221-232); T5 closed+<3 ⇒ NOT-READY (:235-243); `--f2-state` required + choice-validated (:358-364); JSON `f2_state` field :322. Pins `scripts/upgrade/ledger_check.py`, NOT argv.
- **upgrade_tool_interlock_unit_test.sh / upgrade_registration_unit_test.sh** — pack runners over the unit files above (refusal-token regex-equality + spawn_executor-seam patching patterns per blueprint). Grep found no additional AST pins freezing argv/allowlist beyond the equality/subset pins listed.
- AST pins repo-wide (`ast.parse` in tests/) target OTHER subsystems (work_notifier, metadata hooks, chokepoint SQL, idle gates) — **no AST pin touches upgrade argv/allowlist** (grep-verified).

### Refusal-token pin style

Per blueprint + `_journal_refusal_event` (:1090-1093): detail is `"<msg> (reason=<token>)"`; tests pin via **regex equality on the token fragment** (D-FA2.2 convention; `_reason_token` in upgrade_journal parses it).

---

## 4. DRIFT REPORT (caller-supplied anchor → current)

| Caller anchor | Current reality | Drift |
|---|---|---|
| manager.py ~:3773-75 docstring "no halt journal event" | :3778-3781 | +5 lines |
| manager.py ~:3796-3799 extra_env | :3795-3799 | ✓ (−1 start) |
| manager.py ~:3812-15 argv | :3812-3815 (list literal) ✓; promote branch starts :3808 | ✓ |
| upgrade_journal.py:301 journal_history_append | :301 | ✓ |
| upgrade_journal.py ~:890 _TERMINAL_EVENTS | :890 | ✓ |
| upgrade_journal.py ~:957-979 reconcile_pending_op | :909-980 (signature :909; cited range = expiry branch) | anchor was the tail; full fn starts :909 |
| upgrade_journal.py:986-996 allowlist | :988-991 constants; :994-1005 executor_env | ✓ close |
| upgrade_journal.py ~:1038-1092 "USER_ORIGIN_SOURCES gate" | :1040-1129 is now a HISTORY comment (static whitelist REPLACED by registry-backed classify_user_origin, merge 4db90d74); live code :1130-1202 | content changed, not just moved |
| upgrade_tools.py ~:1021-1040 _terminal_outcome | :1021-1036 | ✓ close |
| upgrade_tools.py ~:1043-1052 label map | :1039-1048 | −4 |
| upgrade_tools.py ~:2027 / ~:2293 reconcile sites | :2027 / :2293 | ✓ exact |
| upgrade_tools.py ~:2660 arm pre-lock | run_id :2672-2676, lock :2677 | +12 |
| upgrade_tools.py ~:2668-2672 nonce burn after lock | comment :2668-2671 ✓; actual burn :2685-2688 | ✓ comment / burn lower |
| upgrade_tools.py ~:2702 nonce_consumed | :2702 | ✓ exact |
| upgrade_tools.py:1910-1935 3-factor gate (parked-decision doc's "corrected" cite) | :2452-2620 | doc's correction is itself stale on this branch |
| job_queue.py:526-538 anti-forgery | **:1256-1257 / :1993** (`source=f"agent:{caller_agent_id}"`) | **moved ~+730 lines** |
| test_upgrade_journal.py ~:935 / :962 poison | :935 / :962 | ✓ exact |
| test_upgrade_tools.py :3367 / :3416 / :3423-3434 | :3367 / :3416 / :3423-3434 | ✓ exact |

---

## 5. VERIFIER-VERIFIED MECHANICS (job 81a8a206, 14:15:49Z — folded in verbatim-verified)

**Verifier ran the pinned tests 5/5 PASS on v0.15.1/v0.15.2 (latest lineage identical to this branch for these files).**

**(A) promote.sh argv case + gate sequencing** — child needs BOTH `ENSEMBLE_UPGRADE_LIVE` (env, `require_live_guard` FIRST) AND `--f2-verified-closed` (argv, f2 gate AFTER):
- argv parsing site **promote.sh:57-82**; the F2 flag case **:72-77**:
  > `72:         --f2-verified-closed)`
  > `73:             # MINOR-4b (P2.3 review cycle 1): explicit operator flag —`
  > `74:             # TARGET=live additionally requires it (see the F2 gate after`
  > `75:             # require_live_guard). No value; presence is the attestation.`
  > `76:             F2_VERIFIED_CLOSED=1`
  > `77:             ;;`
  Unknown flag → **:79** `*) echo "promote: unknown flag '$arg' … >&2; exit 78` (any new argv flag MUST join this case or it kills the run).
- env guard FIRST — **:91**: `require_live_guard "$UP_TARGET"`.
- f2 gate AFTER — **:103-105**:
  > `103: if [ "$UP_TARGET" = "live" ] && [ "$F2_VERIFIED_CLOSED" != "1" ]; then`
  > `104:     _refuse f2-not-verified "promote refused (f2-not-verified): TARGET=live requires the explicit --f2-verified-closed operator flag — F2 user-origin forge lane verified closed (runbook §9 hard block; ENSEMBLE_UPGRADE_LIVE=1 remains a separate, still-required factor)"`
  > `105: fi`
  Attestation recorded at txn open: `:187 journal_open_txn "promote" "$VERSION"` → `:198 journal_mark_f2_verified || journal_fail_loud "preflight: journal_mark_f2_verified (F2 attestation record)"`.

**(B) EXECUTOR_ENV_ALLOWLIST purity** — see §2b quote (:994-1005): fresh dict; ambient inheritance limited to allowlist + PG-prefix; **explicit `extra` merged LAST unconditionally**. Verified-arm passthrough must come from literal pending_op fields via `extra_env` — ambient env stays fenced for every unverified path.

**(C) argv hardcoded, no flag slot** — manager.py:3808-3815 (quoted §2a). The enabler must ADD a flag-slot mechanism (design directive). Today there is no conditional-argv structure at all.

**(D) NOTE is documentation, NEVER the gate** — the gate is exactly the two items in (A). The note (argv-adjacent flag or env) is audit text only, stamped AFTER the gate. **Recommended mechanism: `F2_VERIFIED_NOTE` env var threaded via `extra_env`** — one-line rationale: **lib.sh already consumes it** (`journal_mark_f2_verified`, lib.sh:584-589, stamps `f2_verified_note` into in_flight) **and `executor_env` extras passthrough already exists** (:1003-1004), so zero promote.sh argv-case edits (the :79 exit-78 trap stays dormant) and only the drain seam + two test flips change. An argv-adjacent `--f2-note` would force editing the promote.sh case statement AND the exact-equality argv pin (:3388-3391) AND risks the :79 unknown-flag exit if ordering slips. (If argv form is preferred anyway, `--f2-note=<source>:<run_id>` must be added to the case :64-80 and both argv pins flipped.)

**(E) PINNED TEST SURFACE (all quoted in §3; verifier 5/5 PASS)**:
1. test_upgrade_journal.py:867 allowlist purity (equality + structural; extras set :902)
2. test_upgrade_journal.py:910 real spawn (child-env dump; group independence)
3. test_upgrade_journal.py:935 + :962 poison strips (setenv poison; `not in child_env`)
4. test_upgrade_tools.py:3367 argv lock (exact-list equality :3388-3391)
5. test_upgrade_tools.py:3416 + :3423-3434 composed env + poison (subset assertion :3444)
Plus: tests/test_release_journal.sh:188/:200 (f2 flag refusal/proceed, string-contains); drill_ledger_unit_test.sh (checker F2-state pins); upgrade packs run the unit files above. **No AST pins on argv/allowlist.**

---

## 6. F2_VERIFIED_NOTE SITE SURVEY (extra ask 1)

### (a) Verified-arm data ALREADY available at the spawn seam

- **Everything needed is in the journal pending_op**, which the drain ALREADY reads at manager.py:3832 (`op = _uj.read_pending_op(install_dir)` — currently used only for the owner stamp :3833-3837). Fields: `confirmed_by_human: bool`, `confirmed_source: str|None`, `nonce: str|None`, `nonce_consumed: bool`, `run_id: str` (PendingOp :695-716; written upgrade_tools.py:2701-2704).
- **The marker spec does NOT carry them** (manager spec upgrade_tools.py:2711-2723 = kind/env/run_id/target/install_dir/scripts_dir/port only) → either extend the spec at :2711-2723 or read the pending_op in the drain (cheaper: drain already loads it).
- **`run_id` = the arm id** (`r-<stamp>-<4hex>`, mint_run_id :149-152; the nonce's run_id is carried into the op at :2672-2676) — NOT a job id, NOT the nonce id. The nonce (`CONFIRM-…`) is separate (`op.nonce`).
- **`source_type`**: `confirmed_source` stores the raw source STRING (e.g. `api`, `my-discord-bot:123`). The chat TYPE is not persisted; it is re-derivable via `classify_user_origin(op.confirmed_source, registry_get)` (:1152-1202, detail token `registered-chat:<type>`). For the note, echoing the raw `confirmed_source` is sufficient and lossless; deriving the type again would need the sources registry at drain time.
- Wiring shape (worker guidance): in drain, after loading `op` (:3832) and BEFORE spawn (:3823) — or at argv/env construction :3794-3815 — if `op.kind == "promote" and op.nonce_consumed and op.confirmed_by_human and op.confirmed_source`: add argv `--f2-verified-closed` (slot per (C)) and `extra_env["F2_VERIFIED_NOTE"] = f"{op.confirmed_source}:{op.run_id}"`. Unverified paths: neither item → child exits 78 at require_live_guard exactly as today (F2 fence preserved).

### (b) promote.sh parsing + lib.sh logging

- promote.sh argv: :57-82 (case loop; unknown → exit 78 :79). `VERSION` env still works (:57, :109-111) — env-only inputs are inert to the argv loop.
- **`F2_VERIFIED_NOTE` is ALREADY a first-class lib.sh input**: `journal_mark_f2_verified` :554-591 — comment :558: "optional f2_verified_note (from F2_VERIFIED_NOTE env, the operator's audit trail)"; consumption :584-589:
  > `584:     note="${F2_VERIFIED_NOTE:-}"`
  > `585:     if [ -n "$note" ]; then`
  > `586:         new_inf="${new_inf},\"f2_verified_closed\":true,\"f2_verified_at\":\"$(_now_iso)\",\"f2_verified_note\":\"$(_json_escape "$note")\"}"`
  > `587:     else`
  > `588:         new_inf="${new_inf},\"f2_verified_closed\":true,\"f2_verified_at\":\"$(_now_iso)\"}"`
  Additive-only schema (:559-561 — existing readers parse named fields, ignore extras).
- lib.sh logger: `_log/_logv/_warn` :71-73 (`printf '%s[%s]: %s\n' "$LOG_TAG" "${UP_TARGET:-lib}" "$*"`); promote stdout/stderr → `data/upgrade.log` via spawn_executor (:1026-1031), so any `_log` line lands in upgrade.log automatically.
- Called at promote.sh:198 immediately after `journal_open_txn` :187 (live lane only, under the held lock).

### (c) Recommendation

**`F2_VERIFIED_NOTE` env var via `extra_env`** (rationale pinned in §5(D)). Gate stays the two-item pair (A); the note lands in `in_flight.f2_verified_note` (journal) + is visible in upgrade.log if also `_log`ged. Keep the note `"<confirmed_source>:<run_id>"` — bounded, secret-free (source strings are registration ids, :1162 "no secrets… safe to embed in gate refusal reasons").

---

## 7. STALE ROW r-20260926-100210-53ca (extra ask 2)

- **Repo/.agents/data: NOT-FOUND** (grep across `.agents/`, `docs/`, `scripts/`, `data/` — zero matches; oversized-file skips noted).
- **KB (RAG): CONFIRMED** — entity `r-20260926-100210-53ca` (type data): "Stale expired pending_actions nonce present in live state at 2026-09-26T12:17Z; it is inert." Relation: `r-20260926-100210-53ca → v0.15.1` ("Live state at 2026-09-26T12:17Z contains one stale expired pending_actions nonce … inert").
- **Family adjudication: same broad defect family, distinct sub-mechanism.** The 5bf2 arm is a stale **pending_op** (armed op; `nonce_consumed` ∉ `_TERMINAL_EVENTS` + reconcile tool-entry-only → starves). The 53ca row is a stale **pending_actions** entry (the nonce registry, upgrade_journal.py:756+ "Nonce store (D-FA3.3 — pending_actions keyed by run_id)") — an expired nonce record that no tool entry has swept. Both are arm/nonce lifecycle rows that converge ONLY at tool entry (reconcile_pending_op at :2027/:2293 + pending-action expiry checks) — same root constraint (no boot/timer sweep), matching the fix commission's reconcile-widening scope. KB marks 53ca inert (expired ⇒ refusal path `nonce-expired`/expiry branch handles it at next touch).

---

## 8. ADR SERIES — live-rung / F2 (extra ask 3)

**Numbering caveat**: ADRs are PER-WORKSTREAM. The upgrade-pipeline series lives in `.agents/shared/planning/self-restart-upgrade-phase2/decisions.md` (ADR-017 at :33, ADR-032 at :205). The old job-system ADR-001..012 series (`.agents/reviewer/memories/2026-04-18-job-system-v5-review.md:17`) is a DIFFERENT series — do not continue that one.

| ADR | One-line summary | Cited at |
|---|---|---|
| ADR-004 | Retention keep-3, previous pinned | lib.sh:68; tracking :23 |
| ADR-005 | Promote gates: 300s prod soak (drill knob override) + rollback cap 3/24h + 10-min cooldown | lib.sh:54-61; promote.sh:32,411; drills :288 |
| ADR-009 | No network fetch (D3) — builds from local checkout only | lib.sh:34-35 |
| ADR-012 | Abort-lane leaves txn OPEN; journal sweep self-recovers stale in_flight (>600s: flipped⇒rollback / not⇒clear) | promote.sh:47; drills :288,:347 |
| ADR-014 | PORT is staged env state, not a script constant (resolve_env fails closed) | lib.sh:29 |
| ADR-017 | Automation stops at demo PERMANENTLY; live S4-S6 user-executed only (env-target model) | drills :451,:501; parked-decision :21 |
| ADR-020 | rollback_safe interim rule: two enforcement layers; column-dropping releases never rollback targets | drills :463 |
| ADR-021 | Live eligibility = N=3 consecutive CLEAN ledger cycles (machine truth: ledger_check.py) | drills :430,:443 |
| ADR-022 | system_upgrade dry_run defaults TRUE | drills :402 |
| ADR-024 | Sweep respects cooldown; sweep_rollback counts toward cap+cooldown | drills :347; decisions.md :121 |
| ADR-025 | Watchdog file-watch on .launcher-state burst-abort + journal halt/sweep_rollback; launcher burst-abort deliberately NOT an SSE kind | upgrade_journal.py:365-370; drills :476 |
| ADR-027 | Version-verify gate: /livez version == manifest binary_version | drills :29,:308,:466 |
| ADR-028 | Rollback-target re-gate failure ⇒ halt-for-human; recovery = user flip-forward through standard gate | promote.sh:397-404; drills :467 |
| ADR-032 | USER_ORIGIN_SOURCES whitelist (superseded by registry-backed classification, merge 4db90d74); scope disclaimer: anti-forgery structural at stamp site, does NOT close F2 loopback forge | parked-decision :75-77; decisions.md :205 |
| ADR-033 | Halt = boot-and-continue on degraded current (no repoint) | blueprint "Recovery invariants" (self-upgrade-pipeline) |
| ADR-034 | lib.sh textual splices tolerate hand-edit divergence without torn writes | blueprint "Recovery invariants" |

**Highest number seen: ADR-034.** E2's new ADR should be **≥ ADR-035** — and MUST be minted in the upgrade-workstream decisions file (self-restart-upgrade-phase2/decisions.md or its successor), NOT the job-system series. Grep-verify `decisions.md` for any ADR-033/034/035+ entries before minting (ADR-033/034 were seen only in blueprint text; the decisions file may already carry them).

---

## 9. CONSOLIDATED FIX-ANCHOR LIST (for the plan)

1. **argv flag slot** — manager.py:3808-3815 (add `--f2-verified-closed` when verified arm) + promote case already handles it (:72-77, NO promote.sh edit needed for the flag itself).
2. **env passthrough** — manager.py:3795-3799 (add `ENSEMBLE_UPGRADE_LIVE=1` + `F2_VERIFIED_NOTE` to extra_env from `op` fields; executor_env extras merge :1003-1004 delivers them).
3. **child exit visibility** — spawn_executor seam upgrade_journal.py:1012-1037 (pid-only today) + manager.py:3823-3846 (no wait/journal; docstring :3778-3781).
4. **reconcile widening** — reconcile_pending_op upgrade_journal.py:909-980 (tool-entry-only callers :2027/:2293; nonce_consumed ∉ _TERMINAL_EVENTS :890; expiry arithmetic :957-979 = armed_at+600s+600s observed).
5. **_terminal_outcome class filter (optional surface fix)** — upgrade_tools.py:1021-1036 + label :1047 (nonce_consumed masquerades as TERMINAL).
6. **Test flips (two-sided contract)**: test_upgrade_tools.py:3388-3391 (argv equality — only if argv gains the flag), :3439-3444 (allowed set — if extra_env gains F2_VERIFIED_NOTE); test_upgrade_journal.py:902 (extras set — if a new extra key flows through executor_env in the purity test's purview); test_release_journal.sh:188/:200 must stay green unchanged.
