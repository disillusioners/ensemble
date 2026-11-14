# rogue-containment-p2 — Decisions Record

**Role of this file:** the dispatcher's recommendations and triage for LEADER adjudication at review time, plus the standing leader adjudications honored, the (D) decision record, and the worker-surfaced decision points consolidated. Component files carry the detail; this file is the ledger. (Ledger/body agreement: every recommendation below matches the component file it cites — flagged where a component dissents.)

---

## 1. Analysis §9 — six pending decisions, WITH recommendation (leader adjudicates)

Framed against the CURRENT tip `bf4bfde6e` (the analysis was written pre-Lane-2/3-shipment; several decisions are now settled or narrowed by what shipped).

### §9-1 — Lane 4 (burst-window fix): defer or adopt now?
**RECOMMEND: DEFER WITH TRIGGER — reaffirmed, and now armed.** (a) This lane BUILDS the mandated measurement leg (E2 § D: N≥5 cycles, direct `python -m daemon` invocation never via launcher, per-cycle U, verdict rule median U > 58s ⇒ trigger fires / ≤ 58s ⇒ dormant) — closes hole H2 with data instead of the inferred ~30–39s. (b) A2's immediate-78 fast-refuse (LDP-2) + unit `RestartPreventExitStatus=78` means a port-squat boot REFUSES instead of crash-looping and systemd does not restart — the loop class the burst window guards against cannot form via systemd restart. (c) `launcher.sh` stays zero-touch this lane (ceremony visibility: `launcher_sha256` manifest + twin pins). **Routing:** the architect evaluates obsolescence (A2 §6.4 + E2 § D frame the evidence: measured U; whether every realistic squat path terminates in 78; residual exit-1 crash-loop exposure from OTHER causes). If fast-refuse lands AND measured U ≤ 58s: recommend RETIRING the trigger with the measurement as evidence. Rejected sub-variants stay rejected (`-ge` predicate, teardown-budget half — analysis §5.4/§5.6).

### §9-2 — The 9798 fence shape: env-wins validator vs derive-from-main-port?
**RECOMMEND: MOOTED BY SHIPMENT — verify-and-confirm, no build.** The analysis's preferred DERIVE shape is what shipped: `derive_metrics_port` (`daemon/services/metrics_listener.py:75-142`) + managed-key retirement, commit `1ec2afb1b` (rider (d) phase 4), wired at `daemon/api.py:2736-2744`, pinned in `f4157fbf0`. Three-way confirmed (B2 §12-C2, E2 §CONTRACTIONS-2, dispatcher git spot-check). **Remainder:** the H3 read-only verification — one `SELECT` for whether a managed `limits.metrics_listener_port` row still exists post-retirement — rides as B2 §5.1, an operator step, timing = leader's call (B2-D5). "Never a YAML literal" remains binding for any future port work.

### §9-3 — The refusal exit code: reuse 78 vs commission a dedicated code?
**RECOMMEND: SETTLED — reuse 78.** The shipped claim gate already exits 78; both new fences (B2 prod-DSN, A2 holder) reuse it; the unit already carries `RestartPreventExitStatus=78`. 75 is budget-exempt tempfail (unbounded slow loop); a new code lands in `*→crash` and loops unless launcher+unit both learn it — "never piecemeal"; a dedicated ~73/79 stays a long-term paired-lane option only. No new exit codes this cycle.

### §9-4 — The operator alert for the refusal path: which channel?
**RECOMMEND: A2-LDP-3 — ONE family record + generalized exactly-once next-boot surfacing + the `watchdog-watcher.sh` file-watch leg** (ADR-025(b) daemon-independent seam; NOT launcher.sh, carries no manifest sha ⇒ ceremony-invisible), covering the WHOLE exit-78 refusal family: claim refusal, A2 holder refusal, B2 prod-DSN refusal, auth-config refusal, DB-auth refusal. Rejected with reasons (A2 D-A2-8 table): systemd-notify (NotifyAccess drops child notices; unit-touch), Discord/webhook (new dependency + secrets — above this lane), self-alarm (ari-only, manager-wired, dead pre-lifespan). B2 correctly ships NO separate channel — uniform log-marker style subscribes to the family record (B2 §4.8 + §11-D4). **Honest caveat (A2-LDP-4):** the watchdog is launchd-shaped and unscheduled on this Linux host — while-down alerting HERE remains journald + marker + next-boot surface until an ops lane schedules a systemd-timer variant (operator-gated, out of scope). The launcher's own refuse-branch silence (`:3847-3848`, no `_notify_once`) is UNFIXABLE without touching launcher.sh — barred; the daemon-side record is the compensating control.

### §9-5 — The 10-2x sweep-disarm investigation: in scope, or sweep stays off?
**RECOMMEND: ROUTE OUT to a host-level ops investigation; the sweep STAYS OFF.** The disarm reason is not in-repo (live-lane-only change between 2026-10-20 and 10-26, bracketed by `.env` backups); the retained 383k-line journal contains ZERO `Boot sweep:` action lines (analysis §4.2). Re-arming as-is is unsafe (analysis §4.4). This lane neither arms nor modifies the sweep — tests keep the explicit `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pin discipline. A host journal/shell-history recovery pass for 2026-10-20..26 is an operator action, not a code lane.

### §9-6 — Verify the frozen-bundle `install_root_logging()` import before promoting Lane 1?
**RECOMMEND: SATISFIED-BY-EVIDENCE + cheap re-assertion.** Lane-1 bind-retry shipped in v0.29.19 and has booted the live daemon through every promote since (v0.29.19→v0.30.9) — the `run_app → main(run_preflight=False) → _serve_with_bind_retry` path is exercised in production repeatedly. Because this lane touches boot again (A2 pre-bind, B2 preflight), E2 S5 re-asserts cheaply at stage time: the fenced-mirror happy-leg log must show tagged root-handler output in the file log.

---

## 2. H1 disposition (commission item 3)

**RECOMMEND: CLOSE via claim-lineage — ratify A2-LDP-1 option (i).** Claim v2 records the claimant's parent-cmdline lineage (normalized `(argv[0] basename, argv[-1] basename)` tuple hash — survives `systemctl restart` pid churn; degrades to non-foreign for old-format claims, so every prior pin incl. promote-shape non-refuse stays green); same-install + live holder + lineage mismatch ⇒ refuse 78. Cost: ~40 decision lines + additive JSON fields, zero ceremony-visible surfaces. Bonus closure: the `try_claim` unlink+rewrite side-defect (`port_ownership.py:405-409`). Forgery bound honestly declared: a same-uid adversary can forge lineage — that is the ALREADY-documented same-uid residual; lineage raises the bar from "accidental rogue" (the incident class) to "deliberate claim-forging adversary" (out of containment scope). **If the leader picks (ii) re-declare instead:** A2's amputation path drops Phase-2 lineage tasks + AH-9..12 and everything else stands; H1 would remain a narrowed documented residual. Dispatcher's endorsement adds: every lineage miss degrades to non-refuse — the design introduces no false-refuse class.

---

## 3. §7 hardening triage (default = route; ride only if cheap + directly serving A/B/E)

| Item | Ride/Route | Reason |
|---|---|---|
| §7.1 boot-sweep observability (🔴) | **ROUTE** | Independent mechanism (orphan sweep), zero coupling to the A/B builds; the sweep is disarmed live pending §9-5; folding it drags un-adjudicated sweep semantics into a containment release. (If wanted later: a 1-task rider for any boot lane.) |
| §7.2 settle the 10-2x disarm (🔴) | **ROUTE** | = §9-5 recommendation: host-level ops investigation (journal/history recovery); sweep stays off; not a code lane. |
| §7.3 9798 fence no tier can split (🟡) | **MOOTED — shipped v0.29.22** | `1ec2afb1b`; remainder = H3 read-only SELECT (B2 §5.1, timing D5). |
| §7.4 teardown-budget reconciliation (🟡) | **ROUTE** | Analysis Lane 5 (durability, not this incident); touches launcher.sh + unit + stop-script = ceremony exposure; explicitly separate later lane. |
| §7.5 shorten the 60s durable sweep (🟡) | **ROUTE** | Same Lane 5 (`bash_spawn_reaper.py:132`). |
| §7.6 scratch config.yaml literal port (🟢) | **RIDES, HARNESS-LOCAL** | E2 ships the literal-port scratch-config template inside the fenced mirror (closes harness gap #4; makes `ENSEMBLE_CONFIG` a real fence for mirror boots). The repo-root `config.yaml:113` is NOT changed — PORT interpolation is load-bearing for dev lanes; changing it would break sanctioned scratch boots. |

## 4. §8 holes triage

| Hole | Disposition |
|---|---|
| H1 same-install rogue "non-foreign" | **CLOSE** via A2 claim-lineage (§2 above) — LDP-1 ratification |
| H2 post-Lane-1 U inferred, never measured | **CLOSE-BY-MEASUREMENT** — E2 § D leg (mandated by the commission); S9 records the data |
| H3 DB metrics-tier live state unverified | **VERIFY-ONLY** — one read-only SELECT (B2 §5.1); not load-bearing (derive shipped) |
| H4 external ambient-$PORT consumer | **DOCUMENTED RESIDUAL** — loud-failure direction already declared; no action this lane |
| H5/H6 kernel citation / unit-file readability | **DOCUMENTED, non-load-bearing** — no action |
| H7 live config.yaml byte-identity | **CLOSED for port keys** — no action |

---

## 5. (D) burst-window decision record

- **Council decision (analysis §5.4):** DEFER WITH EXPLICIT TRIGGER — promote the sliding-window variant to its own staged lane **iff** measured post-Lane-1 U > 58s OR a loop recurs. Rejected sub-variants: `-gt`→`-ge` predicate (breaks launcher + watchdog twin pins), teardown-budget half (unit + watchdog breakage).
- **Dispatcher preliminary ruling (commission):** ROUTED OUT of this lane — UPHELD. Rationale: `launcher.sh` is ceremony-critical (`launcher_sha256` manifest + twin pins); the binding principle (analysis §6) keeps this lane's containment entirely in the daemon binary.
- **This lane's obligations (both discharged by the plan):** (1) E2 § D MEASUREMENT leg arms the trigger with DATA — N≥5 cycles, legacy exit-1 path (fast-refuse OFF or pre-A2 tip; per-cycle 78 = invalid leg), direct child invocation, RESULTS file at `.agents/tester/RESULTS/<date>-rogue-p2-burst-window-U-measurement.md`, verdict line per the §5.4 arithmetic (310s ladder + 5U ≤ 600s ⇒ U ≤ 58s). (2) The architect's obsolescence evaluation is FRAMED and FED: A2 §6.4 + E2 § D supply (a) measured U, (b) the every-squat-path-terminates-in-78 analysis, (c) residual exit-1 crash-loop exposure from non-port causes. One extra fast-refuse-ON cycle proves the new gate does not itself lengthen failed-boot U past 58s (Lane-1 constraint).
- **Post-measurement routing:** U > 58s ⇒ trigger fires (launcher lane per §5.4); U ≤ 58s + fast-refuse ratified ⇒ architect may RETIRE the trigger; either way NO launcher.sh edits in THIS lane.

---

## 6. Worker-surfaced leader decision points (consolidated; detail in component files)

**B2 (`component-B-tier1-prod-dsn-fence.md` §11):** D1 None-identity polarity — recommend **degrade** (a) with R2 declared; flip option (b) documented. D2 predicate scope — recommend **exact `ensemble_prod` only** (a); widening breaks sanctioned dev/test lanes for no incident-class gain. D3 CLI exemption — recommend **CLOSE** (a); the CLI's auto-persist makes the exemption an active poisoner; `ENSEMBLE_SELF_ENV=live` escape hatch exists. D4 operator alert — defer to the lane-wide §9-4 ruling (above). D5 H3 timing — leader's call (read-only, independent). D6 promote-lane attestation — recommend **rely on auto-derive** (a); the executor-allowlist add (b) is belt-and-braces not worth ceremony-adjacent blast radius.

**A2 (`component-A2-socket-holder-identity.md` §13):** LDP-1 H1 close-vs-redeclare — recommend **CLOSE (i)** (see §2). LDP-2 fast-refuse timing — recommend **IMMEDIATE-78 pre-bind (6a)**; the ladder exists for transient hand-over contention, which positive-proof excludes; U shrinks by construction. LDP-3 alert channel — see §9-4. LDP-4 watchdog on THIS host — named, NOT built (ops lane). LDP-5 Lane-4 evaluation — see §5.

**E2 (`component-E2-pins-verification-measurement.md` § RISKS):** (1) CHANGELOG shape — recommend **consolidated catch-up** (per-version backfill of the four missing headings requires inventing their non-containment content; backfill itself routed to leader). (2) 9798 rider staleness — confirmed shipped; NO re-work. (3) AH-6 fast-refuse shape ratification — both variants pre-specified so E2 does not block. (4) BF-2 carve-out mechanism — B2's `_self_env_marker` design is authoritative (overview Integration Ruling 3). (5) (D) routing after measurement — see §5.

---

## 7. Standing leader adjudications honored (recorded, not re-litigated)

- **(C) rogue-written prod data:** NO new DB lane, NO remediation — Emperor-gated separately. Disclosures carried in plan-overview Risks 4-5: the `ensemble_prod_ro` `bash_spawn_registry` grant limitation (external RO re-read refused; primary path self-consistent) and the checkpoint-TTL watch item (no cleanup run since 2026-11-10; first 168h expiry risk ~2026-11-17; monitor the next due cycle).
- **No promote; live untouched; no migrations** — all three component plans independently confirm code-only (any schema need = STOP + flag).

## 8. Dispatcher integration rulings (binding for the merge)

1. **Refusal-record namespace:** ONE namespace — claims-dir co-location (N2, single reader). B2 ships no durable file; its log-markers subscribe to the family channel. (No conflict existed — B2 §4.8 declined the file precisely to protect A's exactly-once contract.)
2. **E2 mirror leg (c)** must satisfy A2's lineage contract (shared parent cmdline OR planted v2 claim with matching lineage).
3. **BF-2 mechanism:** B2's `_self_env_marker()` carve-out is authoritative; E2 pins whichever lands.
4. **Plan dir naming:** NEW sibling `rogue-containment-p2/` — the prior dir is the committed phase-1 historical record cited verbatim by the shipped-vs-built delta table; extending it would blur the shipped/built boundary the Emperor-facing report depends on.

## 9. Rejected shapes (house memory, do not re-open without new evidence)

New exit codes (piecemeal launcher+unit breakage) · YAML-literal 9798 (kills the only working lever; derive shipped instead) · ppid-equality lineage (false-refuses `systemctl restart`) · full-argv lineage hash (false-refuses invocation variance) · after-ladder fast-refuse 6b as default (feeds the burst budget the fast path exists to stop) · refusing on unattributable holders (false-refuses container CI + nginx-style squatters; violates positive-proof-only) · per-version CHANGELOG backfill (invention risk) · re-arming the boot sweep as-is (disarm reason unknown) · select-by-port reclaim (killed real prod once — `stop-ensemble.sh:11-23` rule) · narrowing the sweep predicate (the orphaned-node case IS the pinned case).
