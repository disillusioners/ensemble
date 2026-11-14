# Rogue-Containment p2 — Architecture Addendum

**Lane:** rogue-containment phase 2 (board wi-eb4e5059; incident wi-2454ede3 occ#9 / bind-race restart loop)
**Date:** 2026-11-14
**Author:** Architect (controller) — synthesis of three dispatched read-only design analysts: Worker A `6e829fbc` (B2 prod-DSN fence), Worker B `8e0aa1c0` (A2 holder identity), Worker C `1f82594d` ((D) obsolescence evidence). Skill-bank note: canonical `security-design` / `resilience-design` / `trade-off-analysis` skills absent from this deployment's bank (DEGRADED flag); Workers A/B ran with `grep-fence-check-before-design-substrate` (applied to every landing zone), Worker C ran skill-less with a manual prompt.
**Worktree:** `/home/nea/ensemble-src-wt-rogue-contain`, branch `feature/rogue-port-containment`, tip `cbe04f4ff` (docs-only on `bf4bfde6e`; all code anchors verified at tip by the workers).
**Authority:** this addendum ENRICHES the committed plan (plan-overview + component-B + component-A2 + component-E2 + decisions.md @ `cbe04f4ff`); the bind-race analysis (FINAL rev 2) still GOVERNS mechanics. Where this addendum corrects the plan, §5 is the defect register. Already-adjudicated items (§9-2/3/4/5/6, CHANGELOG anchor — now **v0.30.12** per the finalize rulings; the addendum-era v0.30.10 anchor is superseded: phantom .10 = a bump commit inside sweep ancestry, zero tags/artifacts, v0.30.11 = the absorbed sibling; forestry owned by the finalize lane — (C) no-remediation, no promote, live untouched, no migrations, B2-D2 exact-name) are honored and NOT re-litigated.

---

## §0 Executive verdicts (commission's three asks)

### V1 — H1 close shape: VALIDATED (claim-lineage), with two mandatory sharpenings
The plan's claim-lineage close is sound and evidence-clean: additive `PortClaim` JSON fields are invisible to old readers (`port_ownership.py:73-82` dataclass; `read_claim` `:322-343` reads every field via `.get()`); every promote-shape pin plants a lineage-less legacy claim and expects `taken` (`tests/unit/test_port_ownership_pins.py:117-132`, `:67`) and stays green under the absent-lineage⇒non-foreign rule; the comparison is **hash-of-recorded-tuple vs hash-of-self-parent-tuple** — no `/proc` read of the *recorded* parent is ever needed, which is what makes it reparent-immune and stable across `systemctl restart` pid churn, release swaps, and run-loop cycles (parent is `bash launcher.sh` in every sanctioned path: unit `ExecStart=__LAUNCHER__` `scripts/systemd/ensemble-daemon.service:107`; launcher run-loop `launcher.sh:3843-3849`). The refusal precedes the `:407` unlink inside `try_claim` (`:401-434`), closing the unlink+rewrite side-defect without reordering. A dead rogue's leftover claim cannot false-refuse (pid+starttime liveness `:349-350` marks it stale ⇒ take-over before lineage is consulted).
**Mandatory sharpenings (adopt into A2 before build):** (i) the lineage tuple must normalize on the **script-name argument** (first non-flag arg after the interpreter), NOT `argv[-1]` — `bash launcher.sh --foo` or a `bash -c` wrapper changes `argv[-1]` and would mint a live-wide false-refuse on a future launcher arg; (ii) add a §7 lane-walk row + docstring residual for **same-install invocation-shape change** (dev.sh claim → direct `python -m daemon` boot on the same port+tree ⇒ lineage mismatch ⇒ refuse where today = take-over; arguably intended H1 semantics, but it must be walked and documented), plus a lineage-tuple-stability control leg (counter-mutant: "hash full argv" ⇒ RED). The same-uid forgery bound stays the documented residual — ratified as intended scope (accidental-rogue class, not claim-forging adversary).

### V2 — B2-D1 None-identity polarity: DEGRADE (fail-open-with-loud-warning), refuse on guard-internal RAISE
Decisive new fact from Worker A: **the live boot carries NO explicit `ENSEMBLE_SELF_ENV`** (live `.env` key census) — its fence-pass depends ENTIRELY on auto-derive (canonical HOME → lanes loop reads `~/agents-ensemble/.env` `POSTGRES_DB` cross-check → `live`; `_auto_derive_env` `daemon/tools/upgrade_tools.py:532-556`). Refuse-on-None would convert any derive-regression (non-canonical HOME + missing/unreadable live `.env`) into a live-down at promote — the BF-2 catastrophe. The None+prod conjunction is genuinely degraded evidence, and the deliberate identity-blurring rogue in that narrow class is already narrowed by the tier-2 pre-guard (`ensemble_config.py:138-206`) + component A. Degrade MUST log the `_self_env_source` vocabulary (`upgrade_tools.py:623-656`, incl. `auto-unresolved`/`ignored-garbage`) so the operator sees WHY identity was unresolved. Guard-internal exception ⇒ fail-closed exit 78, mirroring the shipped tier-2 guard (`:195-206`).

### V3 — 6a fast-refuse: RATIFIED. (D) burst-window: CLOSED-BY-A2, conditional on two named legs
- **6a ratified:** pre-bind fast-refuse on positive proof of a live foreign UNCLAIMED holder. Refusal-path U ≈ 18.3 s (boot-to-prebind, `bind_retry.py:150-160` coupling arithmetic) + walk (single-digit ms, measured: 252 procs/384 fds on this host) + one 250 ms sample ≈ **18.6 s ≪ 58 s** — the Lane-1 constraint holds by construction; the 30 s ladder is never entered. The transient-holder false-refuse class is bounded: to fire, a holder must be positively attributed, alive with stable starttime, LISTEN-ing, and **re-confirmed by a full second table re-walk ≥250 ms later** (pid-alive-but-socket-closed must NOT confirm — see §2). Same-install and unattributable holders never fast-refuse ⇒ no §7-sanctioned lane can hit it.
- **(D) unit evidence:** the unit template carries `RestartPreventExitStatus=78` at `scripts/systemd/ensemble-daemon.service:110` (`:108` `Restart=on-failure`, `:109` `RestartSec=10`, `:111` `SuccessExitStatus=143`; NO `StartLimit*` ⇒ systemd defaults 10 s/5). `adopt-unit.sh` inherits it by whole-template splice (`:353-365` `_resolve_template`, `:401-421` `_generate_unit` with torn-substitution guard) — every unit it adopts contains `:110` verbatim. Runbook `docs/runbooks/systemd-adoption.md:72,:210-213` states the 78-never-restart contract; drill DR-2 (`docs/runbooks/upgrade-drills.md:382`) asserts `--property=RestartPreventExitStatus=78` at runtime. Exit-78 propagation is confirmed end-to-end in repo terms: daemon `sys.exit(78)` (`port_ownership.py:637`; gate at `__main__.py:457` with `except SystemExit: raise` `:458-459`) → launcher `wait` (`launcher.sh:3775-3776`) → `classify_exit` 78⇒refuse (`:3588-3595`) → refuse branch `exit 78` (**current anchor `launcher.sh:3847-3849`** — the analysis's `:3814-3816` is drifted ~33 lines) → systemd suppresses restart.
- **Path trace:** every realistic ENSEMBLE-holder port-squat path post-p2 terminates in 78 — (i) foreign claim ⇒ 78 pre-bind; (ii) foreign unclaimed holder + 6a ⇒ 78; (iii) transient holder departing inside the 30 s ladder ⇒ bind succeeds (same-install hand-over is non-foreign by lineage). The residual exit-1 class (iv) is **non-port** (non-ensemble squatter the gate correctly degrades past; same-install claim-less holder outlasting 30 s ⇒ exit 1 WITH attribution; ordinary boot-path faults) — and **Lane 4's sliding window never cured (iv) either**: with `RestartSec=10`, at most 2 unit starts fall in any 10 s window, so `StartLimitBurst=5` is arithmetically unreachable and the exit-1 loop is paced-forever regardless; Lane 4 only shortens launcher-internal bursts. Hence Lane 4's insurance value on this systemd host ≈ nil.
- **Two closure conditions (both named, both cheap):** (C1) **A2 must actually merge** — `daemon/services/port_holder.py` does not exist at tip; path (ii)'s 78 is plan-stage. Date the (D) closure to A2's merge, not to this addendum. (C2) **the LIVE unit property check** — `/etc/systemd/system/ensemble-main.service` is `0600 root:root` (documented as intentional, `docs/deployment.md` parity table), was operator-edited 2026-11-01 22:56 (pgenv-hygiene, `.bak` alongside), and is unreadable from agent context; `systemctl show` on it is refused by the pkill overlay guard (correct behavior; not bypassed). Readable drop-ins (`0644`) carry NO `Restart*` overrides. One root-side operator command closes it: `systemctl show ensemble-main.service -p RestartPreventExitStatus` ⇒ `=78`. **Both-way verdict pre-drafted:** if ≠78, (D) reverts to DEFER-WITH-TRIGGER until the live unit is regenerated via `adopt-unit.sh` (which restores `:110` by construction).
- **Consequence for decisions.md §5:** once (C1)+(C2) hold AND the E2 measurement records median U ≤ 58 s, the architect RETIRES the Lane-4 trigger per §5's own clause. The E2 measurement leg is KEPT as pure telemetry (H2 closure + the Lane-1 constraint proof via its fast-refuse-ON cycle); it arms nothing. No `launcher.sh` edits in this lane — upheld. The launchd host (GAP-1, `docs/deployment.md:327,348`) sits outside `RestartPreventExitStatus` entirely and is untouched by either Lane-4 or this closure — remains an observability residual, disclose in the final report.

---

## §1 Component B2 — tier-1 prod-DSN fence (occ#9 closer): enriched design

### 1.1 Identity model (repo-verified, not assumed)

| Environment | Resolved DB name | Resolution tier | Boot identity marker (verified) |
|---|---|---|---|
| **live** `~/agents-ensemble`, :9797, `ensemble-main.service` | file json `db="ensemble"` → **`ensemble_prod` via env-wins** (`ensemble_config.py:126`) | tier-1 json (`:71-76`) | **NONE explicit** — auto-derive only: natural HOME → lanes loop reads `~/agents-ensemble/.env` (`POSTGRES_DB` cross-check, `upgrade_tools.py:532-556`); db_hint concurs; unit carries zero PG env (`ensemble-daemon.service:14-16,95`); launcher exports all `.env` keys (`launcher.sh:184-246`) |
| **dev** :8079 | `ensemble_dev` | tier-1 or tier-2 | worktrees: explicit `ENSEMBLE_SELF_ENV=dev` (giter fence, `dev.sh:88-89`); marker-less source boots: db_hint ⇒ dev (`:558-559`) |
| **demo** :7979 `~/agents-ensemble-demo` | `ensemble_demo` | tier-1 | explicit marker present in demo `.env` (key census) |
| **sandbox** | whatever its json/ambient says | tier-1 or tier-2 | frozen + `releases/` + non-canonical dir (`:502-515`, decided BEFORE any file read) — the incident rogue's shape |
| **e2e-scratch** | scratch names (forbidden set `tests/postgres/conftest.py:136-142`) | tier-2 (fresh `ENSEMBLE_DATA_DIR`) or tier-1 | non-frozen, no marker ⇒ **derives `live` on this host** (host `.env` evidence) — the standing R1 residual |

**Load-bearing consequence:** the live boot's file-tier name is `ensemble` and the prod name arrives via env-wins ⇒ **the predicate fires on every live boot** — the sanctioned carve-out is exercised boot-in-boot-out, incl. every promote leg. This is why V2 (degrade-on-None) is the only safe polarity and why the promote-time probe (§1.2-e) is cheap insurance worth adding.

### 1.2 Predicate + adjudications

**Predicate: final-DSN NAME check, not URL parse.** `resolved_db_name.strip().lower() == "ensemble_prod"` (house idiom: `db_hint`, `upgrade_tools.py:513`). TWO DSN construction sites share the identical env-wins expression `os.environ.get("POSTGRES_DB", pg.db)` — `get_postgres_url` (`ensemble_config.py:124-131`) and `create_postgres_engine` (`daemon/repositories/factory.py:316`, env-wins line `:360`) — one resolved-NAME helper covers both. URL-parsing the rendered f-string is hazardous (passwords containing `?`/`@`; `factory.py:380-387` documents exactly this as the reason `URL.create` exists). Kwarg-form carries no params touching the db component (`factory.py:388-408`) ⇒ no param normalization needed. **Pre-TCP hook points:** `__main__._boot_db_preflight` after `load_or_create` `:213`, inside the postgres branch, before the probe's local `create_engine`; `api.py` lifespan after `:754` before `:755` (pin-safe — `test_db_resolution_guard.py:133` pins a call-count of a DISTINCT name); `auth/cli.py` after `:150` before `create_postgres_engine` `:154`. Declare the out-of-hook residual in the docstring: `migration_worker.py:487-489` builds a fresh post-boot config (boot already fenced).

Adjudications (each firm, evidence-cited):

- **(a) `ENSEMBLE_DB_AMBIENT_ALLOW=1` must NOT override a prod-NAME refusal — NO.** Leader lean upheld. Today ALLOW affects tier-2 only (tier-1 returns `:170-171` BEFORE the ALLOW check `:176`); its only legitimate consumers are dev.sh first boot (`dev.sh:145-149`) and sanctioned first boots (json persists `:95`; ALLOW lifetime = one boot). The bypass is REAL: ALLOW is not in `SPAWN_BOUNDARY_STRIP_ENV` (`spawn_env_policy.py:43-65`, 12 names verified) — a rogue inheriting `ALLOW=1` passes tier-2 today; independent-gating (§4.6) closes it. Disaster-recovery fresh live re-install passes BOTH gates via auto-derive with no new mechanism — no lane needs an ALLOW-override.
- **(b) None-identity ⇒ degrade** (V2 above). Refuse only on guard-internal RAISE (fail-closed mechanism failure, mirroring `:195-206`).
- **(c) `auth/cli.py:150` exemption: CLOSE.** The CLI's `load_or_create` PERSISTS json (`:95`, atomic `:98-112`) — the exemption is an active poisoner. Closure order: `assert_db_resolution_sanctioned(data_dir)` BEFORE `:150` so a refused CLI writes no artifact; prod gate when `is_postgres`; escape hatch `ENSEMBLE_SELF_ENV=live` (`:617-619`). Fence-check: NO structural pin exists on `resolve_engine` (zero callers outside `auth/cli.py:265`; auth tests inject sqlite engines `tests/conftest_auth.py:154`) ⇒ closure lands clean; P9 is additive.
- **(d) extra_env sanctioned channel: PRESERVED, zero change.** The promote-executor seam is explicitly NOT a scrub consumer (`spawn_env_policy.py:12-16`); `EXECUTOR_ENV_ALLOWLIST` (`upgrade_journal.py:2944-2960`) forwards `HOME`+`POSTGRES_DB` deliberately; `ENSEMBLE_SELF_ENV` is NOT in it ⇒ executor boots sanction via **auto-derive** (frozen staged binary under `<install>/releases/<ver>/` → canonical topology + prod cross-check ⇒ `live` under both HOME shapes). D6-(b) executor-allowlist add is unnecessary.
- **(e) BF-2 mitigation: D6-(a) + one new cheap leg.** P4 pin + S5 staged boot + auto-derive reliance, PLUS a stage-verification probe that resolves identity from the staged tree under the executor env — read-only `python -c "from daemon.tools.upgrade_tools import _self_env_marker; print(_self_env_marker())"` with `HOME`+`POSTGRES_DB` staged as the executor would. Turns BF-2 from asserted to measured-at-stage-time; zero seam changes.

### 1.3 B2 test seams (no live PG)

1. **Guard-level matrix** (`tests/unit/test_prod_dsn_fence.py`): identity monkeypatched at `daemon.tools.upgrade_tools._self_env_marker` — the guard MUST look the marker up at CALL time (function-local import / module-attr); pin the patch target in P1–P7 docstrings. Load-bearing cells: `live×prod`→proceed+INFO; `dev/demo/sandbox×prod`→78; `None×prod`→proceed+WARNING; `RAISE×prod`→78 UNVERIFIABLE; `{live,dev,sandbox,None}×{ensemble_dev, ensemble_prod_test, ensemble, scratch}`→proceed; **P4b (new): the real live shape** — json `db="ensemble"` + ambient `POSTGRES_DB=ensemble_prod` + identity `live` ⇒ proceed.
2. **Preflight-level integration seam** (subprocess harness pattern `tests/integration/test_auth_boot_preflight.py`): staged prod json in tmp `ENSEMBLE_DATA_DIR`, patched identity, assert pre-bind exit 78 **with the marker substring** (`tier=resolved-dsn-targets-prod`) — NEVER bare exit 78: the auth-config preflight (`__main__.py:289`) runs BEFORE the DB preflight in `main()` and has its own 78 (e.g. missing `ENSEMBLE_AUTH_ISSUER`). Frozen entry order differs (`run_app.py:68` runs DB preflight first) — the fence cannot be preempted there.
3. **CLI seam:** call `auth.cli.resolve_engine` directly (tmp data dir + prod ambient + patched identity) ⇒ `SystemExit 78` + `not (data_dir/"ensemble.json").exists()` (P9 anti-poison). No argparse.
4. **Counter-mutants:** BF-1 sloppy-pattern→P2; BF-2 live-false-refuse→P4+P4b; BF-3 file-tier-only→P3; BF-4 ALLOW-bypass→P7; BF-5 fail-open→P6; BF-6 CLI-exemption→P9; BF-7 None-polarity both directions→P5; BF-8 ordering→P8+P10.

### 1.4 B2 S5 / frozen-binary implications

Imports frozen-safe (guard in `ensemble_config`, pydantic+stdlib; new dependency `daemon.tools.upgrade_tools` is stdlib-only at top level `:82-94` with boot-path precedent `port_ownership.py:207-214`); **use a function-local import inside the guard** to keep the very-early preflight cycle-free. New file reads = lane-2 candidate `.env` reads + `Path.home()` — identical to existing port-ownership boot reads; the sandbox shape exits `:510` BEFORE any read (zero extra I/O for the incident class). `run_app.py` untouched — its source pin (`test_main_entry.py:703-716`) requires `_boot_db_preflight()` + ordering; T2 edits inside the body. **Marker-channel caveat:** the DSN refusal fires BEFORE `install_root_logging()` (`:447`, inside `_serve_with_bind_retry`) ⇒ marker lands via root-logger lastResort → stderr → journald (`StandardError=journal` in the unit). S5 must assert on stderr/journal text for the B2 family, not the file log (contrast A2, §2.4).

---

## §2 Component A2 — bind-time socket-holder identity: enriched design

### 2.1 Holder inspection: pure-/proc RATIFIED (D-A2-1)

Parse `/proc/net/tcp` AND `/proc/net/tcp6` (LISTEN `st==0A`; hex port after `:` in `local_address`; both tables REQUIRED — a dual-stack socket bound `::` and v4-mapped `::ffff:` rows appear ONLY in tcp6); filter by port+state, never address. Then `/proc/*/fd` walk (`socket:[inode]` → pid), then `/proc/<pid>/{exe,cwd,cmdline,stat}` attribution (starttime vs pid-recycle). **Measured cost on this host (read-only): 252 processes, 384 fds ⇒ single-digit ms**; early-exit once every listener inode is matched. vs `ss`/`lsof`: fork+exec 5–20 ms, output-format drift, absent from minimal container images, identical privilege ceiling, same kernel tables — and pure-/proc is the ONLY variant that is tmp-procfs-injectable for tests and frozen-binary-safe. **Explicitly forbid a `psutil` dependency** (binary deps; breaks the frozen lane). Privilege degrade: other-uid `fd` dir EACCES ⇒ inode unresolvable ⇒ discovery returns no holder ⇒ no refusal (correct direction) — ADD a §5.6 row: "fd-dir EACCES on a listener inode ⇒ `inode unresolved to pid (permission)` WARNING" (today's §5.6 covers exe/cwd only).

**TOCTOU honesty (fix D-1):** what must hold between the two samples is NOT liveness-only — sample 2 must RE-RUN `find_listener_holders`: same `(pid, starttime)` AND the same inode still present in a freshly parsed LISTEN row AND unchanged attribution. A pid alive whose socket closed must NOT confirm. Add counter-mutant AH-M15 (sample-2 implemented liveness-only ⇒ AH-14 reddens).

### 2.2 Check placement: BOTH, with distinct roles

- **Pre-bind gate** (`__main__.py:455-457`, before `bind_socket_with_retry` `:489`): REFUSES (6a). Its false-refuse class (holder that would release inside the 30 s ladder) is bounded by positive-proof-only + the two-full-rewalk samples.
- **Post-conflict EADDRINUSE exhaustion path** (`bind_retry.py:381-390` → `BIND_FAILURE_EXIT_CODE=1` `:178`): ATTRIBUTES ONLY — a holder found at exhaustion is ground truth (the bind proved contention at that instant) and covers the race where a holder appears AFTER the gate passed. Write attribution into the exhaustion log line (+ optional forensics-only marker file); NEVER refuse there — changing that exit would violate §6.2's "BIND_FAILURE_EXIT_CODE untouched".

### 2.3 6a fast-refuse — ratified (V3); leg-degeneracy already handled

E2 §112 declares per-cycle-78 = invalid legacy leg; E2 sequencing step 5 orders the (D) measurement BEFORE fast-refuse default-ON; S9 demands one fast-refuse-ON cycle as direct U evidence. **Resolves defect D-2:** declare **NO knob** — the legacy-path measurement runs at a pre-A2 tip (exactly decisions.md §5's own "fast-refuse OFF or pre-A2 tip" language); E2's "knob, exact name per A2 sibling" expectation is deleted in favor of the pre-A2-tip leg. Sequencing survives unchanged.

### 2.4 Edge cases (verified) + marker surfacing

PORT=0/ephemeral: gate validates (`:606-615`; `validate_claim_port` `:245-255`) BEFORE discovery ⇒ skip preserved. Same-install WITH claim: lineage path (V1). Same-install WITHOUT claim: WARNING + retry, declared §5.7 residual — do NOT close it claim-less (reparent hazard). Holder dies mid-check: AH-8/AH-M8 starttime mismatch ⇒ stale ⇒ degrade. IPv6/v4-mapped: tcp6 parse (AH-13) + ADD a `::ffff:` row fixture. **Metrics port 9798: correctly ungated** — the gate is invoked only on the main port; `metrics_listener.py` has zero `port_ownership` references, is loopback-only, and bind-failure ⇒ disabled + boot continues (module docstring `:16`, `server_bind` `:350-355`); `derive_metrics_port` (`:75-142`, wired `api.py:2740-2744`) prevents split-key confusion, not squatters. Add ONE module-docstring line declaring that boundary; NO discovery rider. **Marker surfacing:** today's reader logs only the legacy field set (`port_ownership.py:503-519`) — `family`/`source`/`evidence`/`ppid_cmdline_head` require the T5 reader extension (LH-2) or the new refusal surfaces namelessly; `write_refusal_marker`'s getattr branch (`:544-549`) accepts a `HolderRef` exposing `pid`/`install_dir`; exe/cwd are re-read at marker-write time (`:556-557`) and may be `<unreadable>` if the holder died in-window — acceptable (discovery-time evidence rides the log line). Note the channel split vs B2: A2's gate runs AFTER `install_root_logging()` (`:447`) ⇒ file log; B2's fires before ⇒ stderr/journald (§1.4) — S-legs assert per-family.

### 2.5 A2 test seams (no live ports) + S5

Fixtures: injectable proc-root tmpdir trees (synthetic `tcp`/`tcp6` files; `fd/` dirs with REAL symlinks `socket:[inode]` — `os.readlink` works on tmpdir symlinks; fake `stat` starttime); tmp claims-dirs (pins pattern `:152-167`; conftest fence `tests/conftest.py:912-919`); gate seams extend `test_gate_refusal_never_enters_bind_retry` (`:167`); harness legs (b)/(c) per E2 `:80-81` — leg (b) plants a scratch holder (python subprocess on a `free_port()` socket, cwd = scratch install ⇒ foreign ⇒ 78); leg (c) spawns holder+daemon from the SAME parent cmdline ⇒ non-refuse. **C1 claims-dir fix is a PRECONDITION, not a gap-fix:** the harness plants at the pre-B1 path and never sets `ENSEMBLE_PORT_CLAIMS_DIR` ⇒ the existing refuse-foreign leg is silently green today (verified vs `resolve_claims_dir` `:108-125`). Counter-mutants AH-M1..M15 / LH-M1..3 / HY-M1..4 named per the `mutation-01` house rule; ADD the lineage-tuple-stability control (mutant "hash full argv" ⇒ RED) and fold §5.2's claim-and-socket agreement into AH-2 assertions (D-5). S5: stdlib-only (`os/re/pathlib/time/hashlib/json`) ⇒ zero hiddenimports; the gate already runs on the frozen entry; foreign-holder mirror leg boots the real staged artifact against a planted holder (E2 `:97`, scoped safe).

---

## §3 (D) burst-window obsolescence — full record

Verdict, conditions, and evidence are in §0-V3. Additional record items:

- **Adoption-chain mechanism (premise correction):** neither `adopt-unit.sh` nor `lib.sh` carries the directive LITERALLY — adopt-unit.sh inherits it via whole-template splice; `lib.sh` carries no unit-template content at all (the `ensemble-daemon.service` mention at `lib.sh:6272` refers to a retired non-existent template). Net effect equivalent (generated units contain `:110`), but the plan's citations should say "template + splice inheritance", not "adoption tooling carries it".
- **Integrity gap (new finding, routes out):** `launcher.sh` is sha-pinned (`launcher_sha256`, `lib.sh:3055,4076-4084`) but the UNIT TEMPLATE has NO sha pin anywhere — a template edit silently changes every subsequently adopted unit; this is exactly the live-divergence vector behind closure condition C2. Recommend a follow-up lane (small, scripts-side), NOT this lane.
- **In-code corroboration:** `daemon/tools/upgrade_journal.py:3479,3503-3508` refuses operator overrides of `Restart` on promote-transient units, citing that "RestartPreventExitStatus=78 alone fences only the refusal exit class" — the codebase already treats 78-never-restart as a standing invariant.
- **Residual loop honesty for the final report:** the exit-1 paced-forever loop class survives p2 for NON-port causes (start-limit arithmetic makes `failed` unreachable with `RestartSec=10`); Lane 4 never addressed it (burst-shortening only). If the house ever wants a real cure it is a `StartLimitIntervalSec`/`StartLimitBurst` unit conversation — explicitly out of scope here (unit = zero-touch).

---

## §4 Cross-cutting

### 4.1 Exit-78 refusal taxonomy — one family, two channels, one record
Family members post-p2: claim-gate refusal (shipped), A2 holder refusal (new), B2 prod-DSN refusal (new), auth-config refusal (`__main__.py:80,189,268`), DB-auth refusal. Coherence rules: (1) same exit code 78 + same marker grammar (`family=`/`source=`/`tier=`/`evidence=` fields, extending `port_ownership.py:503-519` via T5/LH-2); (2) ONE family record + generalized exactly-once next-boot surfacing + watchdog file-watch leg per §9-4 (journald+marker caveat stands); (3) **channel split is deliberate and must be pinned per-family**: B2 fires pre-logging (stderr→journald lastResort), A2/claim-gate fire post-logging (`:447`) (file log); S-legs assert on the correct channel per family; (4) auth-config's own 78 can preempt in `main()` order — integration legs assert marker substrings, never bare exit codes (§1.3-2).

### 4.2 Test-seam design (consolidated; mutation-proven with ZERO live resources)
- **Fake DSNs** — guard-level monkeypatch matrix + tmp `ENSEMBLE_DATA_DIR` staged json; call-time identity seam; P4b real-live-shape cell; CLI no-artifact assertion (§1.3).
- **Fake holders** — injectable proc-root tmpdir (synthetic tcp/tcp6 + fd symlinks + starttime), two-full-rewalk sample mutant AH-M15, v4-mapped fixture, fd-EACCES fixture (§2.1/§2.5).
- **Fake claims** — tmp claims-dirs, legacy vs lineage claim fixtures, dead-claim (stale starttime) take-over leg, lineage-tuple-stability control (§2.5).
- **Harness legs** — (b) foreign holder ⇒ 78; (c) same-parent ⇒ non-refuse; C1 claims-dir fix as PRECONDITION; fenced-mirror scratch-port (18790-class) discipline per S8 (never 9797/9798/7979).
- **(D) telemetry** — harness-side direct `python -m daemon` invocation; zero new boot-path reads (§0-V3, confirmed).

### 4.3 S5 / frozen-binary implications of each new boot-path read
| Read | Frozen-safety | New I/O | S5 assertion |
|---|---|---|---|
| B2 resolved-name guard (`ensemble_config`) | function-local import of `upgrade_tools` (stdlib top-level) | lane-2 `.env` + `Path.home()` (= port-ownership precedent); sandbox exits pre-read | stderr/journal marker text; stage-time identity probe (§1.2-e) |
| A2 holder walk (`port_holder.py` NEW) | stdlib-only; forbid psutil; no hiddenimports | `/proc/net/tcp{,6}` + `/proc/*/fd` reads (ms-class) | file-log marker; foreign-holder mirror leg on the staged artifact |
| (D) measurement | none (harness-side) | none | n/a — S9 telemetry only |

Plus the standing §9-6 re-assertion: fenced-mirror happy-leg shows tagged root-handler output in the file log.

---

## §5 Plan-defect register (consolidated, deduped, severity-ordered)

**🔴 fix-before-build (plan text):**
1. **Live-unit evidence gap** — A2 §6.2, E2 §D, decisions.md §9-1(b)/§9-3 say the unit "verified" carries `RestartPreventExitStatus=78`; that verification is TEMPLATE-only (`ensemble-daemon.service:110`). The live unit is 0600 root, operator-edited 2026-11-01, unreadable from agent context. Fix: add closure condition C2 (root-side `systemctl show` one-liner, §0-V3) to the (D)-closure gate; reword the three "verified" claims to "template-verified, live-unit check pending".
2. **(D) closure contingency** — path (ii)'s 78 is plan-stage (`port_holder.py` absent at tip); decisions.md §9-1(b) reads as if fast-refuse were landed. Date the closure to A2's merge (condition C1).
3. **D-2 knob interface** — E2 expects a fast-refuse knob; A2 never declares one. RESOLVED HERE: no knob; legacy measurement at pre-A2 tip (§2.3). E2 §112 wording edits accordingly.
4. **D-3 lineage fragility** — argv[-1] normalization + missing §7 invocation-shape row + stability control (§0-V1 sharpenings). Mandatory.

**🟡 fix-in-plan (accuracy/completeness):**
5. Component-B §3 stale anchors: `:476-491/:494-533/:529/:545-547` → **`:502-515/:532-556/:513/:558-559`**; §4.7 `:566-571` → **`:617-619`** (claims correct; citations stale).
6. Second DSN site uncited: `factory.py:364-369` (same env-wins expression) — cite it; optional structural pin/docstring cross-ref on the factory db line to catch drift. [Re-verified 2026-11-14: the env-wins line now sits at `factory.py:360` in `create_postgres_engine` (`:316`).]
7. Pin gap P4b: the real live shape (json `db="ensemble"` + ambient prod + identity live ⇒ proceed) is only transitively covered — add the cell.
8. plan-overview Phase-1 file column omits `daemon/api.py` though T3 edits it (single-ownership intact — T1/T2/T3/T4 disjoint; add to sweep).
9. C1 claims-dir harness fix is a PRECONDITION for the existing refuse-foreign leg (silently green today) — raise from gap-fix to blocking.
10. Marker-channel split (B2 stderr/journald vs A2 file-log) — pin per-family assertion channels in the S-legs (§4.1).
11. D-1 two-sample wording (`confirm_holder` must re-walk, not liveness-check) + AH-M15; D-5 fold claim-socket agreement into AH-2.
12. §5.6 add fd-dir EACCES row; AH-13 add `::ffff:` fixture; metrics_listener docstring boundary line; T5/LH-2 reader extension named as the surfacing dependency for the new refusal.

**🟢 record-only:**
13. Anchor drift: launcher refuse branch is `:3847-3849` at tip (analysis `:3814-3816` stale; A2's `:3847-3848` under-cites the `exit 78`); `classify_exit` `:3588-3595`.
14. Adoption-chain mechanism: template-splice inheritance, not literal carriage (§3).
15. `migration_worker.py:487-489` post-boot config build — declare as out-of-hook residual in the guard docstring.
16. Unit-template sha pin missing — follow-up lane, not this one (§3).

## §6 Decisions pending for the leader
1. **Ratify V1/V2/V3** (H1 lineage close with the two sharpenings; D1 degrade; 6a + (D) CLOSED-BY-A2 conditional on C1+C2).
2. **Schedule the live-unit property check** (closure condition C2): one root-side command at stage time — recommend attaching to S9 as an operator leg (S9b) so trigger retirement is evidence-complete at ceremony time.
3. **Follow-up lane for the unit-template sha pin** (defect 16) — leader's call whether to queue.

## §7 Confidence + flip assumptions
- **V1/V2 design verdicts: HIGH.** Pins verified against the actual test files; no grep-fence blocks any landing zone (fence-check applied). Flip assumption: a hidden consumer of `PortClaim` fields outside the verified set would break additivity — none found by grep.
- **V3 6a: HIGH.** U arithmetic is measured-input-based. **V3 (D): MEDIUM-HIGH, conditional** — flips to DEFER-WITH-TRIGGER iff the live unit returns ≠78 for `RestartPreventExitStatus` (both-way verdict pre-drafted, §0-V3); also flips if A2's merge is descoped (C1).
- Cross-cutting S5/channel analysis: HIGH (channel split verified from `__main__.py` ordering + unit `StandardError=journal`).
