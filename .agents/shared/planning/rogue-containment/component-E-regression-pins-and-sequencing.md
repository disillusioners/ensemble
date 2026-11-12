# Rogue-Process Containment — Component E: Regression Pins + Fenced Mirror + Sequencing + Staged Verification

**Feature:** rogue-containment (incident wi-2454ede3, 2026-11-11 bind-race restart loop)
**Component:** E — integration lane (pins matrix + fenced mirror + sanctioned-lane census + D adjudication + §7 hardening adjudication + sequencing + staged verification + foot-guns + risks + missed items)
**Mode:** Mode 1 — design only. NO code changes, NO test runs, NO repo writes except this file, the stub, plan-overview.md, and the A anchor fix, plus their commit.
**Governing authority:** `.agents/shared/planning/bind-race/rogue-port-containment-analysis.md` (FINAL rev 2, sha256 `78e25565…`). Analysis wins on every conflict; conflicts disclosed in § Contradictions (already in plan-overview.md).
**Worktree:** `/home/nea/ensemble-src-wt-rogue-containment`, branch `feature/rogue-containment`, tip `cace21473` (parent `e87f987a8` = origin/latest).
**Sibling inputs:** `component-A-port-ownership.md` (cfc232a15), `component-B-spawn-boundary-scrub.md` (51f187be6), `plan-overview.md` (dispatcher synthesis).

---

## § E — REGRESSION PIN MATRIX (mutation-proven, non-vacuous)

House mutation discipline is BINDING: every pin is a POSITIVE shape (a counter-mutation must redden it); absence-only pins are prose-satisfiable and forbidden. Exemplars: `tests/unit/test_ari_pm_terminology_pins.py` (pure checker `:118-150`, in-memory legs `:329-422`, non-vacuity control `:409-412`); `tests/bind_retry_mutation_matrix.md` (13 mutants, sha256 restore `:13-17`, venue discipline `:24-36`, B-1 cross-component pin `:52`); `tests/unit/agents/test_aegis_security_family.py:2527-2564` (wrong-pin refusal probe).

Consolidated matrix. Pin id | component | positive shape asserted | counter-mutation that reddens it | non-vacuity control | RED-leg venue.

| Pin | Component | Positive shape asserted | Counter-mutation that reddens it | Non-vacuity control | RED-leg venue |
|---|---|---|---|---|---|
| **E-i** | A (rogue-boot refuse family) + B (DB-refusal) + scrub-effect | See contingency note below — under analysis-wins, E-i maps to the rogue-boot REFUSAL family: (A's E-ii) foreign-claim ⇒ exit 78 + marker line + refusal-marker file; (B's pin iii) ambient-tier DB refusal ⇒ exit 78 before any TCP connect; plus the scrub-effect pin (B pin i) — a foreign-claim/ambient-poison boot is REFUSED, never silently succeeds | M1 (flip planted claim install_dir to self's ⇒ no refuse ⇒ boot succeeds ⇒ reddens); M2 (dead/mismatched starttime ⇒ stale ⇒ no refuse ⇒ reddens); M3 (remove foreign-ness check entirely ⇒ same-install refuses ⇒ reddens); B-pin-iii M (remove ambient predicate ⇒ guard never fires ⇒ sentinel called ⇒ reddens) | M7-style control leg: clean tree stays green (E-ii M4/M5 + B pin iii non-vacuity control with `ENSEMBLE_DB_AMBIENT_ALLOW=1` set ⇒ guard does NOT fire and engine construction proceeds ⇒ green) | Fenced mirror (scratch port, per-var scrub, FAKE_HOME, disposable PG, sweep pin 0) |
| **E-ii** | A | Rogue-boot REFUSED in a fenced mirror: planted claim with `install_dir = "foreign-install-root"` (≠ self), live pid, matching starttime ⇒ daemon-under-test exits 78 (not 0/1); holder-identity marker line in log (pid/exe/cwd/install/port); durable refusal-marker file written; live 9797/9798 and `ensemble_prod` untouched (G5 fence + port probes) | M1 flip install_dir to self ⇒ same-install ⇒ no refuse ⇒ readyz 200 ⇒ reddens; M2 dead/mismatched starttime ⇒ stale ⇒ no refuse ⇒ reddens; M3 remove foreign-ness check ⇒ same-install holder refuses ⇒ reddens | M5 degrade leg: read-only claims dir ⇒ gate degrades ⇒ daemon still boots (readyz 200) ⇒ stays green (proves degrade-never-refuse is the load-bearing condition, not always-refuse) | Fenced mirror |
| **E-iii** | A | SANCTIONED lane boots end-to-end: scratch port 18790, claim absent ⇒ take claim ⇒ bind succeeds ⇒ readyz 200 within budget; no refusal marker; self-claim written; claim cleaned up on graceful shutdown. NON-NEGOTIABLE: the guards must not break the house's own staged-verification lanes | M4 plant foreign claim on 18790 before boot ⇒ daemon refuses 78 ⇒ happy-path pin reddens (cross with E-ii); M5 read-only claims dir ⇒ degrade ⇒ still boots (readyz 200) ⇒ stays green | M7 control: clean tree green | Fenced mirror — named lane below |
| **Promote-shape non-refuse** | A | Same-install hand-over does NOT refuse: planted claim with `install_dir == self_install_dir` + live pid/starttime ⇒ daemon does not exit 78 (exit 0 / readyz 200); no refusal marker/file; claim taken (overwritten with self's pid) | M6 flip foreign-ness to treat same-install as foreign ⇒ daemon refuses 78 ⇒ reddens | M7 control: clean tree green | Fenced mirror |
| **B pin (i) scrub-effect** | B | Probe child spawned through the scrubbed seam: stripped vars ABSENT (`HOST`/`DAEMON_PORT`/`LIMITS_METRICS_LISTENER_PORT`/`POSTGRES_*`/`ENSEMBLE_UPGRADE_LIVE`) AND explicit-setter vars PRESENT (`PORT` overlay wins ⇒ `PORT==12345`); service-spawner leg: `subprocess.Popen` with new `env=` + `extra_env={"PORT":"9"}` ⇒ child sees PORT=9 and stripped set absent. `SPAWN_BOUNDARY_STRIP_ENV` positive-count gate = 10 names (bind 4 + DB 5 + attestation 1) | Remove one strip entry ⇒ that var survives ⇒ RED; remove overlay-merge line ⇒ PORT absent ⇒ RED | Non-vacuity control: clean tree green | Unit (in-memory `apply_spawn_boundary`) + fenced mirror for end-to-end |
| **B pin (ii) rider-(d) derived metrics** | B | Derived metrics port never equals live 9798 when main is scratch: main 8099 ⇒ metrics 8100 (not 9798); main 9797 ⇒ metrics 9798 (unchanged); `LIMITS_METRICS_LISTENER_PORT=19999` explicitly set ⇒ metrics 19999 (escape hatch honored); DB managed row for `limits.metrics_listener_port` does NOT override the derived value (registry entry retired) | Revert derive to `getattr(config.limits, "metrics_listener_port", 9798)` ⇒ scratch lane collides on 9798 ⇒ RED | Non-vacuity control: clean tree green | Unit + fenced mirror |
| **B pin (iii) DB-refusal** | B | Poisoned ambient + synthetic `ensemble.json`-absent data dir ⇒ refuse (exit 78) BEFORE any TCP connection; zero connections to disposable PG / closed port; unit leg patches `create_engine` with a failing sentinel; guard raises `SystemExit(78)` with marker line naming tier + ambient var names + fence var; sentinel never called; mirror leg: harness boot exits 78 with zero pg_stat_activity rows for the scratch DB | Remove ambient-tier predicate (or fence-var check) ⇒ guard never fires ⇒ boot proceeds to engine construction ⇒ sentinel called / connection made ⇒ RED | Non-vacuity control: with `ENSEMBLE_DB_AMBIENT_ALLOW=1` set, guard does NOT fire and engine construction proceeds ⇒ green (proves the fence var is the load-bearing condition) | Unit (primary) + fenced mirror (end-to-end) |

**Pin (i) subject-matter (contradiction #1 carry):** the commission's "pkill shape REFUSED" presupposes a bash-tool pkill guard which the analysis's chosen set does NOT include. Under analysis-wins, E-i does NOT assert a pkill refusal. Instead E-i maps to the rogue-boot REFUSAL family (A's E-ii + B's pin iii) plus the scrub-effect pin (B pin i): a foreign-claim/ambient-poison boot is REFUSED, never silently succeeds. **Contingency row (if the leader overlays a pkill guard later):** pin-i would additionally assert that an agent-issued bash command containing a pkill/kill against a daemon-shaped process is refused at the tool layer with a marker line naming the matched pattern, and that a sanctioned test invocation (fenced) is allowed — modeled on the wrong-pin refusal probe (`test_aegis_security_family.py:2527-2564`). That pin has NO subject today and must not be invented in this lane.

**Mutation-matrix artifact (REQUIRED in the developer's task list):** create `tests/bind_race_rogue_containment_mutation_matrix.md` (modeled on `tests/bind_retry_mutation_matrix.md`) recording each mutant (M1–M6 + B pin mutants), the file/line edited, the expected REDs, the sha256-verified restore procedure (`:13-17`), venue discipline (`:24-36`), and the non-vacuity control leg. The developer's task list MUST include this artifact as a named deliverable (see § Sequencing task list).

---

## § FENCED MIRROR (RED-leg safety — NON-NEGOTIABLE)

One reusable harness spec. Proving the incident shape reproduces must NEVER actually capture live ports (9797/9798) or touch `ensemble_prod`.

**Harness contract (all legs):**
- Scratch port — never 9797/7979/8079-colliding (use `free_port()` re-roll off 9797/7979, or an explicit scratch port like 18790).
- Per-variable scrubbed env — NEVER `env -u POSTGRES_*` (bash does not glob env names; it is a silent NO-OP). Scrub one-by-one: `unset POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_HOST POSTGRES_PORT` plus `DATABASE_URL`, `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`, `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES` (unset then PIN 0 explicitly).
- FAKE_HOME with the 4-stage escape guard (`tests/upgrade_test_lib.sh:69-102`): (1) HOME export took; (2) FAKE_HOME under /tmp; (3) resolved live-install parent under /tmp; (4) never touches /home/nea.
- Fresh disposable PG via `PG_TEST_*` (`tests/postgres/conftest.py:78-158`): G5 fence FORBIDS DB names `ensemble_prod`/`ensemble_dev`/`ensemble_prod_test` even on scratch clusters — never use them. Component B solved this with a sentinel engine factory + scratch-named mirror DB — align (unit-level synthetic `ensemble.json` + `create_engine` sentinel; mirror leg uses scratch DB name `ens_e2e_scrub_probe` or `ensemble_test`).
- `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` PINNED EXPLICITLY (never `env -u` on it — that strips the ambient guard and the sweep KILLS LIVE processes; 2026-10-31 incident).
- SIGTERM-only teardown (no SIGKILL in the harness; rely on the daemon's own graceful shutdown).
- F7 rules binding on the harness: umask 077 (`boot_v2.sh:3-4`); captures redacted (name + sha256 prefix only); scrub ambient BEFORE effect; fail-closed.

**Closest ancestors to model:**
- `boot_v2.sh` (`.agents/tester/RESULTS/assets/2026-11-11-skill-capture-admission-proof/scripts/boot_v2.sh`): umask 077 `:3-4`, occupied-port pre-flight `:16-19`, per-var scrub + re-read-own-fence-values `:22-28`, scratch `ENSEMBLE_CONFIG` `:36-38`, sweep pin `:48`.
- `tests/support/wedge_smoke.py`: free_port re-roll off 9797/7979 `:102-109`, cgroup assert `:440-448`, transient_unit_argv `:451-477`, `PG_TEST→POSTGRES` translation `:552-559`, scratch_env `:562-586`.

**§7.6 scratch config.yaml with literal port:** belongs to the mirror/sanctioned-lane harness — component B §4.2 lane 4 (`boot_v2.sh`) and §6.4 already carry the scratch `ENSEMBLE_CONFIG` + explicit `PORT`/`HOST`/`POSTGRES_*`. Component A §5.1/D2 relies on the frozen-binary path derivation (staged resolves to live install root). The harness spec cites these existing carriers; no new carrier is added here. If a lane lacks a literal-port config, add `ENSEMBLE_CONFIG=<scratch>/config.yaml` with `daemon.port: <scratch>` (never `${PORT:-8088}`) — otherwise ambient `PORT` still wins through interpolation and `ENSEMBLE_CONFIG` is not a real fence.

---

## § SANCTIONED-LANE ENUMERATION (16 rows)

Carried from the verified census (component A §7 + component B §4.2), with trip-risk verdicts consistent with both. For each: one line + trip-risk verdict (SAFE/precondition).

1. **Live boot + promote hand-over window** — SAFE (same install_dir ⇒ non-foreign ⇒ take claim; 30s bind-retry covers transient EADDRINUSE; same-install never refuses). Component A row 1.
2. **Promote / restart / rollback chain** (`promote.sh:464-499`, `adopt-unit.sh:284,304-306,450-460,579` — PORT read ONLY from staged `.env`) — SAFE (staged resolves to live install root via frozen-binary path derivation ⇒ non-foreign ⇒ no refuse; deliberate PORT setter preserved; claim cleanup pid-gated). Component A row 2.
3. **`dev.sh` boots on 8079** (`dev.sh:141-142`) — SAFE (binds 8079 ≠ 9797 ⇒ different claim file; dev install_dir resolves None ⇒ D6 ⇒ non-foreign). Component A row 3.
4. **Pytest transient-unit boots** (`wedge_smoke.py: transient_unit_argv :451-477`, `scratch_env :552-586` sets `DAEMON_PORT` inert; `free_port :102-109` re-rolls off 9797/7979; cgroup assert `:440-448`) — SAFE (main port resolves to 8088 or a free port, not 9797 ⇒ different claim file; 9798 collision is anomaly 07e8410c owned by B rider (d); transient boots from source resolve install_dir=None ⇒ D6 ⇒ non-foreign). Component A row 4.
5. **Tester staged-binary scratch mirror** (`boot_v2.sh`: umask 077 `:3-4`, occupied-port pre-flight `:16-19`, per-var scrub `:22-26`, FAKE_HOME `:30-34`, scratch ENSEMBLE_CONFIG `:36-38`, PORT=18790 `:40`, scratch PG `:42-46`, sweep pin `:48`, ENSEMBLE_SELF_ENV=sandbox `:55`) — SAFE (binds 18790 ≠ 9797/9798; sandbox ⇒ self-consistent install_dir; no foreign live owner on 18790 ⇒ take claim ⇒ bind succeeds). Component A row 5.
6. **DR-2 exit-78 drill** (`docs/runbooks/upgrade-drills.md:164-191`: `SBX=/tmp/ens-sbx-dr2-$$`, `PORT=8377`, `HOME=$(mktemp -d)`) — SAFE (port 8377 has no live holder ⇒ claim absent ⇒ take claim ⇒ bind succeeds ⇒ then config refusal ⇒ exit 78 as before; ownership gate does not mask it). Component A row 6.
7. **`scripts/test_builtin_mcp_servers.sh`** (`PORT=18088` pinned AFTER sourcing repo `.env` `:89-116`) — SAFE (binds 18088 ≠ 9797; precondition: `export PORT=$TEST_PORT` executes after the `.env` source, verified at tip ~line 116). Component A row 7.
8. **Shell packs sourcing launcher.sh** (`scripts/run_tests_scrubbed.sh:25-70` enumerated unset + `:53-62` unset PORT + `:64-70` zero-survivor verify gate) — SAFE (daemon-side scrub codifies this class). Component B row 6.
9. **Soak/pack fences** (`tests/packs/dbpool_gate_ladder.sh:103-105` per-var fence incl. `DAEMON_PORT`; `tests/packs/soak_gate_mcp_warmup_acceptance.sh:388-389`) — SAFE. Component B row 7.
10. **Live boot** (live install, tier-1 ensemble.json) — SAFE (tier-1 ⇒ DB guard does not fire; live boots need no fence var; binding principle holds). Component B row 1 (promote executor) + §6.2.
11. **Stage.sh no-boot** (stage does not bootstrap live; `:424`) — SAFE (no daemon boot in stage; no port bind). (analysis §6 row "stage.sh no-boot".)
12. **dev.sh 8079** — already row 3.
13. **Manual PG smoke 8199** (`tests/manual/maintenance_console_smoke.sh:12` `PORT="${SMOKE_PORT:-8199}"`) — SAFE (disposable PG DB, per-var scrub, no live contact).
14. **Builtin-MCP script 18088** — already row 7.
15. **Deploy.sh** (deploy to INSTALL_DIR, ownership-scoped stop, `:378-396`) — SAFE (ownership-scoped stop, never port-based; no daemon boot in deploy itself beyond launcher start on the target port).
16. **Makefile install** (install to INSTALL_DIR, `make install`) — SAFE (ownership-scoped; no live contact beyond the target install).
17. **Frozen entry points** `run_app.py` + `ensemble.spec` — SAFE (run_app runs `_boot_db_preflight` then `main(run_preflight=False)`; ownership gate inside `_serve_with_bind_retry` runs on both entry paths; install_root_logging at `:445` precedes the gate).
18. **Shell-pack launcher sourcing** — already row 8.
19. **run_tests_scrubbed wrapper** — already row 8.
20. **Interpreter-certification pack fences** (`dbpool_gate_ladder.sh:107-125`) — SAFE (per-var scrub + interpreter certification; no live contact).
21. **Soak packs** — already row 9.
22. **Docs-described drills incl. DR-2 exit-78** — already row 6.

(22 enumerated; the commission's "16" is a floor — the census above is the superset; rows 12/14/18/20/21/22 are duplicates of 3/7/8/10/9/6 and are listed for completeness only. The distinct count is 16: live boot/promote, dev.sh 8079, pytest transient, tester mirror, DR-2, builtin-MCP 18088, shell packs, soak fences, live boot tier-1, stage.sh no-boot, manual PG smoke 8199, deploy.sh, Makefile install, frozen entry points, run_tests_scrubbed, interpreter-certification fences.)

**ONE lane named for the end-to-end E-iii proof FROM THE STAGED ARTIFACT:** the **tester-style staged-binary scratch mirror boot** (`boot_v2.sh`-shaped) during staged verification — justification: it is the closest existing ancestor (already fenced, already a full boot), it exercises the ownership gate on the TAKE-CLAIM path (claim absent ⇒ take ⇒ bind succeeds ⇒ daemon reaches readyz), and it proves component A does NOT break normal boots. The sad path (refuse) is covered by E-ii. Confirm-or-improve: the staged-binary mirror is the right lane because (a) it is the only lane that boots the ACTUAL frozen artifact (the staged verification gate requires 4-way sha + CArchive + PROMPT-BASELINE + measured interpreter — all of which are properties of the staged artifact, not of a source tree), and (b) it is the lane that already carries every fence the E-iii leg needs (per-var scrub, FAKE_HOME, scratch PG, sweep pin 0, sandbox self-env). A source-tree `python -m daemon` boot would NOT exercise the frozen-binary path derivation that D2 relies on, so it cannot prove the promote-shape non-refuse pin. No improvement needed; the named lane stands.

---

## § D — ADJUDICATION RECORD (burst-window fix)

**Verdict per analysis §5.4: DEFER with explicit trigger, NOT implemented in this lane.**

Quote of the analysis's reasoning (§5.4): adopt the sliding-window variant (re-anchor `window_start = now` on every counted crash ⇒ "6 crashes with no ≥600 s quiet gap"; the `uptime ≥600` exemption still covers a healthy daemon) to its own staged lane **iff** the measured post-Lane-1 `U > 58 s`, or a loop recurs. Rationale: coding's own adoption argument *is* that trigger condition; deferring avoids dragging the ceremony-critical `launcher.sh` + `manifest.launcher_sha256` + the twin pins into this cycle **without measured need**. *If you prefer unconditional insurance, coding's Lane-4-now is defensible* — the sliding-window variant breaks no machine contract (the watchdog reads the persisted marker, `scripts/watchdog-watcher.sh:446-464`).

**The three-constant inconsistency:** `BUDGET_MAX_CRASHES=5`, `BUDGET_WINDOW_S=600`, `BUDGET_UPTIME_RESET_S=600` (`launcher.sh:57-59`); the backoff ladder before the 6th crash is 10+20+40+80+160 = 310 s, so abort requires `310 + 5U ≤ 600` ⇒ `U ≤ 58 s`. Observed U = 87–95 s ⇒ the 6th crash lands at ~775 s ⇒ the aged-window branch fires `reset` every time (13 consecutive ladders, each ending `action=reset`). The defect is an internal inconsistency between the three constants. The rejected `-ge` variant (breaks `tests/test_launcher.sh:215-216` plus `scripts/watchdog-watcher.sh:434` `BUDGET_MAX_CRASHES=5 # MUST match launcher.sh` and its pin `tests/test_watchdog_watcher.sh:26-30`, and `watchdog-watcher.sh:453-455` abort detection goes blind). The teardown-budget half (≥150 s reconciliation) is a separate, later, durability lane (Lane 5).

**Trigger measurement belongs to the v0.29.19 promote/boot-smoke ceremony lane** (Lane 1 is merged at tip; its U measurement happens at its ceremony — the constraint `bind_retry_max_wait_seconds ≤ ~45 s` is now enforced in-code at 30.0 ceiling, `bind_retry.py:162`, and must be preserved by this lane's reviewers: A's gate must not lengthen the failed-boot path U past 58 s).

**Owning lane for the deferred fix:** a future trigger-gated dedicated launcher lane (Lane 4). This lane routes only; it does not implement.

**Routing table (Lane 5 + §9.6 + others):**

| Item | Owner | Notes |
|---|---|---|
| Lane 4 burst-window fix (sliding-window) | Future trigger-gated launcher lane (Lane 4) | Deferred; trigger = measured post-Lane-1 U > 58 s or loop recurs; touches `launcher.sh` ⇒ manifest `launcher_sha256` + twin pins ⇒ own staged lane |
| Lane 5 teardown-budget durability (≥150 s reconciliation; shorten 60 s durable sweep) | Durability lane (later) | `launcher.sh` + unit template; `bash_spawn_reaper.py:132`; `scripts/stop-ensemble.sh:53-59,110-113`; `scripts/systemd/ensemble-daemon.service:115` |
| §9.6 frozen-bundle `install_root_logging()` verify | v0.29.19 ceremony lane (Lane 1) | Verify `install_root_logging()` import resolves in the frozen bundle on the `run_app → main(run_preflight=False) → _serve_with_bind_retry` path; component A depends on it (D5) |
| §7.2 sweep-disarm host investigation | Leader/host lane | `live .env:115`; reason not in-repo; sweep stays off until recovered |
| §7.4 teardown budget (same as Lane 5) | Durability lane | — |
| §7.5 shorten 60 s durable sweep (same as Lane 5) | Durability lane | — |
| H1 same-install rogue (survives) | Declared hole — future ownership-scoped reclaim | Out of scope |
| Copied-`ensemble.json` residual (tier-1 live-DB pin) | Future hardening (leader decision §12 item 8 in B) | Install-provenance field in `ensemble.json` |
| Promote leg on json-less install fence var | Promote lane (leader decision §12 item 7 in B) | Executor forwards `ENSEMBLE_DB_AMBIENT_ALLOW=1` via explicit-extra merge |

---

## § §7 HARDENING ADJUDICATION

For each §7 item: in-lane or routed, one-line reason.

| §7 item | Disposition | Reason |
|---|---|---|
| §7.1 Make the boot sweep observable (emit summary at WARNING or install logging before `daemon/__main__.py:392`) | **Fold-in as a rider** (cheap, daemon-binary, one-line change + its own pin) | It is a one-line change to `daemon/services/boot_subprocess_sweep.py` (raise the no-candidates INFO to WARNING, or install logging earlier) and is independently valuable; component A's gate depends on logging being live at the refusal moment, so observability of the sweep is a natural co-tenant. Add to the developer task list as a rider with its own pin (pin: the sweep summary line appears in the file log at WARNING level even when no candidates exist). |
| §7.2 Settle the 10-2x live-lane sweep disarm | **Route to leader/host lane** | Reason not in-repo (analysis §4.2); host-level lookup; §9.5 leader decision. |
| §7.3 Give the metrics port a fence that no tier can split | **In-lane (component B rider (d))** | Already owned by component B rider (d) (derive + env-wins escape hatch); B's pin (ii) covers it. |
| §7.4 Reconcile the teardown budget with reality | **Route to durability lane (Lane 5)** | `launcher.sh` + unit template; durability/consistency defect, not a port-hold defect. |
| §7.5 Shorten the dominant 60 s durable sweep | **Route to durability lane (Lane 5)** | `bash_spawn_reaper.py:132`; non-fatal/retryable but overran. |
| §7.6 Ship a scratch `config.yaml` whose port is a literal | **Belongs to the mirror/sanctioned-lane harness** | Component B §4.2 lane 4 (`boot_v2.sh`) and §6.4 already carry the scratch `ENSEMBLE_CONFIG` + explicit `PORT`/`HOST`/`POSTGRES_*`; component A §5.1/D2 relies on the frozen-binary path derivation. The harness spec (§ Fenced Mirror above) cites these existing carriers; no new carrier added here. |

---

## § SEQUENCING (commission #5, verbatim requirements)

**Order by dependency:**
1. Shared scrub-policy module (`daemon/services/spawn_env_policy.py`) — before its two seam consumers (B tasks 1-3).
2. Component A's `port_ownership` module + gate (A tasks T1-T2) — independent of B; can run in parallel with step 1.
3. Component B's rider (d) + DB guard (B tasks 4-5) — rider (d) touches `api.py:2694` which is the same lifespan as the DB boot-apply `:749` (ordering constraint B §5.2); DB guard shares `ensemble_config.py` with `load_or_create`.
4. Then the pin tests + mutation matrix (A T4-T5 + B task 7 + the new mutation-matrix artifact).
5. §7.1 rider (observability one-liner) — can fold into step 2 or 4 (its own pin).
6. House gates (ruff/pytest collection, interpreter certification).
7. Reviewer.
8. Tester (mutation-proof: the matrix legs + counter-mutations, all RED legs in the fenced mirror only).
9. Stage cut (giter; version slot from LIVE census at stage time — refs floor v0.29.19; do not assume the next slot; `ENSEMBLE_UPGRADE_LIVE=1` + `--f2-verified-closed` at ceremony).
10. Staged verification with legs proving E-i/ii/iii FROM THE STAGED ARTIFACT: 4-way sha equality (release dir == dist == manifest == sidecar) + sidecar witness; CArchive census with full-prefix `uniq -d` (duplicate-member detection); PROMPT-BASELINE both trees (181/181 expected); measured interpreter cpython-3.14 recorded.
11. **NO PROMOTE** — the ceremony is the Emperor's.

**Zero-migration expectation:** confirm from A/B — A D7 (filesystem JSON claim, no SQLModel table, no `*.sql`); B §14 (zero tables; only an optional DELETE of an orphaned `limits.metrics_listener_port` row — data cleanup, not schema). Rider-(d) retires a managed-key registry entry which is DATA cleanup, not schema — note it as the one DB-touching task, with its cleanup shape per component-B task list: (a) remove the `ManagedKeySpec` entry from `MANAGED_KEYS` in `settings_service.py`; (b) optionally DELETE the orphaned `app_settings` row `WHERE key='limits.metrics_listener_port'` (best-effort, idempotent, no migration); (c) update the settings UI/registry docstring; (d) the env escape hatch `LIMITS_METRICS_LISTENER_PORT` remains honored.

**Rollback_safe adjudication shape:** fix-loss-only (additive logic, no schema change expected). The one DB-touching task (rider-(d) registry retirement + optional row DELETE) is data cleanup with no schema delta, so `rollback_safe=true` with `source=explicit (fix-loss-only)` is the correct adjudication — consistent with v0.29.19's `rollback_safe=true source=explicit (fix-loss-only)`. If the developer discovers a schema need, STOP and flag (changes rollback_safe and ceremony scope).

---

## § FOOT-GUNS (bake into developer/tester tasks; each with its receipt)

1. **Frozen-binary boot smokes MUST pin `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` explicitly** — `env -u` on it strips the ambient guard and the sweep KILLS LIVE processes (2026-10-31 incident; critical note). Receipt: `boot_v2.sh:48` pins it; `tests/unit/agents/test_aegis_security_family.py:1722,1782,2547,2851`.
2. **Staged boot smokes are NOT isolated** — the frozen binary's path derivation binds the LIVE install root/promote journal (`daemon/api.py:2545` is the wrong module — actually `daemon/tools/upgrade_tools.py:666` `_resolve_install_dir`, `:424` `_canonical_install_root`; `daemon/services/promote_txn_hold.py:140` `_resolve_install_root`). `ENSEMBLE_SELF_ENV=sandbox` selects the branch but does NOT override path derivation. A staged boot smoke binds the live `releases/state.json`. Receipt: critical note + A §5.1/D2.
3. **Daemon-child shells inherit LIVE knobs** (`POSTGRES_*` → `ensemble_prod`, `ENSEMBLE_UPGRADE_LIVE=1`) — scrub PER-VARIABLE; `env -u POSTGRES_*` is a silent NO-OP (bash does not glob env names). Receipt: critical note + component B F7 rule 1 + `run_tests_scrubbed.sh:25-70`.
4. **SQLite boot impossible** → fresh disposable PG DB (`PG_TEST_*`); G5 fence FORBIDS `ensemble_prod`/`ensemble_dev`/`ensemble_prod_test` even on scratch clusters. Receipt: `tests/postgres/conftest.py:78-158`.
5. **NO bare `python` in worktrees** (no `.venv` there; use the venv interpreter). Receipt: repo conventions + `uvvenv-01-no-pip-use-importlib` POK.
6. **Pytest notes:** `testpaths=["tests"]`; four dirs deselected (integration/postgres/slow/conformance); `timeout=30` thread mode (`os._exit(1)`; exit 1≠124); pass explicit `tests/` lists; net-new-reds = FAILED-list diff vs a TRUE pre-fix base; new `await` ⇒ AsyncMock census. Receipt: repo conventions (PYTEST).
7. **G5 forbidden DB names** even on scratch clusters — use sentinel engine factory + scratch-named mirror DB (B §6.4).
8. **GNU grep ERE quirks** (`\|` = literal pipe under -E; plain `|` under -E) + `/bin/sh` vs bash loops (use bash scripts for loops; `PIPESTATUS`/`pipefail`/`${var%%suffix}` ⇒ "Bad substitution" under /bin/sh). Receipt: repo conventions (TESTING).
9. **House POK rule `plan-body-ledger-agreement`:** diff plan body vs ledger every cycle — the BODY wins; predicate strings must match character-for-character; no marker-grep check detects this mismatch. Receipt: POK entry `plan-body-ledger-agreement`.
10. **Frozen-binary boot smokes bind the LIVE install root** (foot-gun #2) — any test that writes a claim file under the resolved install root writes to the LIVE claims dir. Mitigation: the E-ii/E-iii legs plant claims with explicit `install_dir` values and assert via port probes + DB-name assertion; never rely on "scratch" resolving to a scratch install root under a frozen binary.

---

## § RISKS + LEADER DECISION POINTS

### Consolidated risks (dedup A/B, keep highest-severity + most-specific)

| # | Risk | Source | Impact | Likelihood | Mitigation |
|---|---|---|---|---|---|
| 1 | Claim file lives under the install root shared with live via the frozen-binary path-derivation foot-gun ⇒ live + staged share one claims dir | A R1 | medium | high (by construction) | Foreign-ness is by `install_dir` field, not claim-file location (A D2); staged resolves to live install root ⇒ non-foreign by construction; verified by trip-risk rows 1/2/5 |
| 2 | pid recycle between claim write and check | A R2 | high | low | field-22 starttime verification (`pid_alive_with_starttime`); mismatch ⇒ stale ⇒ reclaim |
| 3 | Claim write failure disables ownership enforcement for that boot | A R3 | medium | low | degrade-never-refuse (A D1) + WARNING log + refusal-marker best-effort |
| 4 | Two-boots race (simultaneous boots of different installs) | A R4 | medium | low | atomic `O_CREAT\|O_EXCL` + bounded re-read (A §5.4); unknown ⇒ retry, never refuse |
| 5 | H1 residual: rogue in the SAME install dir executing the live's own binary is non-foreign | A R5 | high | low (survives) | Declared hole; future ownership-scoped reclaim (analysis §4.4) or per-boot identity in the claim |
| 6 | Promote hand-over: if staged did NOT resolve to the live install root, new daemon sees foreign live owner and refuses 78, breaking every promote | A R6 | critical | low (verified) | Verified staged resolves to live install root; covered by trip-risk row 2 and promote-shape pin 8.3; pin M6 guards the regression |
| 7 | Rider (d) derive retires the `limits.metrics_listener_port` managed key; operators who set it via settings UI see a dead knob | B R1 | medium | medium | Retirement task + UI copy + orphaned-row ignore; document the env escape hatch |
| 8 | Guard (b) refuses a legit fresh-postgres boot whose operator forgot the fence var | B R2 | medium | medium | Clear refusal marker naming the fence var + one-line remediation; first-boot json persists ⇒ only the FIRST boot needs it |
| 9 | Scrub breaks agent scripts that read `$PORT`/`$HOST`/`$POSTGRES_*` from ambient | B R3 | low-medium | high | Desired (H4 loud failure); document; agent scripts switch to explicit values |
| 10 | `wedge_smoke.scratch_env` must add the fence var or its tier-2 postgres selection refuses | B R4 | high (test breakage) | high | Task 6 adds `ENSEMBLE_DB_AMBIENT_ALLOW=1` to scratch_env; pin (iii) non-vacuity control proves it |
| 11 | Promote leg on a brand-new install dir (no json) needs the fence var | B R5 | medium | low | Flag as leader decision; default: promote legs set it via the executor's explicit-extra merge when target lacks json |
| 12 | Copied `ensemble.json` pin (tier 1) pointing at live DB is NOT caught by guard (b) | B R6 | high | low | Declared hole (B §6.3); future hardening = install-provenance field in json |
| 13 | Overlay re-add of `ENSEMBLE_UPGRADE_LIVE` via agent `env=` | B R7 | medium | low | F2 gate (`--f2-verified-closed`) still refuses; flag as leader decision |
| 14 | Derive changes metrics port for lanes that pinned 9798 explicitly via yaml | B R8 | low | low | Env escape hatch (`LIMITS_METRICS_LISTENER_PORT`) preserves explicit pin; derive only replaces the default/yaml-9798 path |
| 15 | Service-spawner `extra_env` parameter threading touches callers | B R9 | low | medium | Default None ⇒ byte-identical behavior for existing callers; pin (i) covers the new path |
| 16 | Guard at `api.py:727` (lifespan) must not become exit-1 crash-loop | B R10 | high | medium | Design uses `SystemExit(78)` (propagates as clean exit-78), not a raised Exception; pin (iii) asserts exit code 78 |
| 17 | §7.1 observability rider (if folded in) adds a log-level change that could mask the "no candidates" signal | new | low | low | Pin: sweep summary line appears in file log at WARNING level even when no candidates exist |
| 18 | Lane-1 constraint preservation: A's gate must not lengthen the failed-boot path U past 58 s | analysis §5.1 | critical | low | Gate runs before `bind_socket_with_retry` and exits 78 directly (no retry loop entry); no lifespan startup on the refuse path; verify U at the ceremony boot smoke |

### Leader decision points (consolidated; detail in component files; do not re-derive)

Reference plan-overview.md § Leader decision points (6 items) + component A §13 + component B §12 (8 items). New/added by this integration file: none beyond those already listed. The 14 decision points total are carried verbatim; this file adds no new decision beyond the already-listed §7.1 fold-in (recommended: fold in) and the E-iii lane naming (recommended: staged-binary mirror, confirmed above).

---

## § WHAT THE SCOPE SUMMARY MISSED (commission asked)

The analysis items the commission scope did not name:

1. **Lane 1's load-bearing constraint** — `bind_retry_max_wait_seconds ≤ ~45 s` (now enforced in-code at 30.0 ceiling, `bind_retry.py:162`); the constraint must be preserved by this lane's reviewers: A's gate must not lengthen the failed-boot path U past 58 s. (analysis §5.1)
2. **§7.1–§7.6 hardening set** — adjudicated above (§7.1 fold-in; §7.2 route; §7.3 in-lane B; §7.4/7.5 route Lane 5; §7.6 harness).
3. **H1/H3 residual holes** — H1 (same-install rogue survives; future ownership-scoped reclaim); H3 (DB-managed metrics-port tier unverified; operator SELECT before relying on the env lever). Both survive by design.
4. **§9.6 frozen-bundle `install_root_logging()` verify** — routed to the v0.29.19 ceremony lane (A depends on it: D5).
5. **The §6 POLICY row** — lanes must never boot a live-shaped daemon; the fence lives in the spawn boundary, institutionalized by (e′). (analysis §6 POLICY)
6. **The sweep's disarm-provenance gap** (§4.2/§7.2) — reason not in-repo; sweep stays off until recovered.
7. **The burst-window three-constant inconsistency** (§3.3 ii) — routed to Lane 4 (deferred).
8. **The teardown-budget mis-modelling** (§3.3 i) — routed to Lane 5 (durability).
9. **The mandatory companion operator alert** (§5.2) — A D8; must ship in the same release as (f) (a 78 is a silent-down otherwise).
10. **H4 (ambient-`$PORT` consumer outside the repo)** — desired direction is loud failure after (e′); corroborated by `.agents/tester/RESULTS/2026-11-02…:204`.
11. **H7 (live `config.yaml` not byte-identical to origin/latest's)** — closed for port keys; a hand-edited live `config.yaml` with a literal `port:` would already have made `PORT` inert (it is not: live binds 9797 with `PORT=9797` in `/proc/3612324/environ`).
12. **The two adjacent defects that ARE real** (§3.3): teardown budget mis-modelled + burst budget structurally unreachable — both routed, not implemented here.

---

## § CONTRADICTIONS (commission scope vs analysis — analysis wins)

Carried verbatim from plan-overview.md § Contradictions (4 items); no new contradictions found in this integration pass. Re-stated for the dispatcher's convenience:

1. Bash-tool pkill guard (commission shape A-1) is NOT in the analysis's chosen set — E-i maps to the rogue-boot refusal family + scrub-effect pin; contingency row documents what pin-i would assert if the leader overlays a pkill guard.
2. Wiring/refusing-loudly on `DAEMON_PORT` (commission shape A-3) is REJECTED by analysis §5.6 — cured by (e′) + scratch-config lever (§7.6).
3. Commission A-2's "explicit fence env var marks a sanctioned test" exemption vs the analysis's install-dir scoping — analysis needs no fence var for port ownership; a fence var appears only in B's DB-resolution guard (`ENSEMBLE_DB_AMBIENT_ALLOW`), where the analysis is silent.
4. Commission B's DB root (`data/ensemble.json` postgres pin outranking the .env fence) is analysis-silent — B extends the (e′) MECHANISM to the DB-poison family under blueprint + critical-note authority; no analysis receipts for the DB half. Disclosed, not analysis-contradicted.

---

## § ASSUMPTIONS / OPEN QUESTIONS

- Assumption: the frozen-binary path derivation continues to resolve staged boots to the live install root (foot-gun #2). If a future change fixes it (staged resolves to its own root), component A's "staged is non-foreign" property breaks and H1-style handling is needed for staged too. Coupling to monitor.
- Assumption: `install_root_logging()` (Lane 1, merged) runs before the gate so the marker line is visible. Component A depends on Lane 1 being present (it is, at tip `cace21473`).
- Open question (A §15, carried): single global refusal-marker file vs per-port (`port-ownership-refusal-<port>.json`)? Recommend per-port to avoid one refusal clobbering another's record; default in A §5.7 is single-file for simplicity.
- Open question (A §15, carried): `release_claim` in lifespan teardown vs `atexit`/signal handler? Recommend lifespan teardown (clean path) + staleness self-healing (crash path) — no signal handler needed.

---

## § CROSS-REFERENCES

- Governing analysis: `.agents/shared/planning/bind-race/rogue-port-containment-analysis.md` (FINAL rev 2).
- Component A: `.agents/shared/planning/rogue-containment/component-A-port-ownership.md` (cfc232a15).
- Component B: `.agents/shared/planning/rogue-containment/component-B-spawn-boundary-scrub.md` (51f187be6).
- Plan overview: `.agents/shared/planning/rogue-containment/plan-overview.md`.
- Exemplars: `tests/unit/test_ari_pm_terminology_pins.py`, `tests/bind_retry_mutation_matrix.md`, `tests/unit/agents/test_aegis_security_family.py:2527-2564`, `tests/upgrade_test_lib.sh:69-102`, `tests/postgres/conftest.py:78-158`, `tests/support/wedge_smoke.py:102-109,440-448,451-477,552-586`, `boot_v2.sh`.

---

*End of component E plan. Design only — no code, no tests run, no repo writes except this file, the stub, plan-overview.md, and the A anchor fix, plus their commit.*
