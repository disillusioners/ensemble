# rogue-containment-p2 — Decisions Record

**Role of this file:** the dispatcher's recommendations and triage for LEADER adjudication at review time, plus the standing leader adjudications honored, the (D) decision record, and the worker-surfaced decision points consolidated. Component files carry the detail; this file is the ledger. (Ledger/body agreement: every recommendation below matches the component file it cites — flagged where a component dissents.)

---

## 1. Analysis §9 — six pending decisions, WITH recommendation (leader adjudicates)

Framed against the CURRENT tip `bf4bfde6e` (the analysis was written pre-Lane-2/3-shipment; several decisions are now settled or narrowed by what shipped).

### §9-1 — Lane 4 (burst-window fix): defer or adopt now?
**Dispatcher recommendation (superseded by FINAL verdict below): defer with trigger.** ~~This lane BUILDS the mandated measurement leg (E2 § D: N≥5 cycles, direct `python -m daemon` invocation never via launcher, per-cycle U, verdict rule median U > 58s ⇒ trigger fires / ≤ 58s ⇒ dormant) — closes hole H2 with data instead of the inferred ~30–39s.~~ *(Superseded — the measurement leg is retained as pure telemetry; see the final record.)* (c) `launcher.sh` stays zero-touch this lane (ceremony visibility: `launcher_sha256` manifest + twin pins).

**FINAL VERDICT (architect addendum §0-V3 @ `ad814dae4`; leader-ratified, binding): (D) = CLOSED-BY-A2, CONDITIONAL.**
- **C1 — A2 must actually merge:** `daemon/services/port_holder.py` is plan-only at tip; path (ii)'s 78 is plan-stage. **The closure is DATED TO A2'S MERGE COMMIT**, not to the addendum or this plan.
- **C2 — live-unit property check:** the unit-template `RestartPreventExitStatus=78` (`ensemble-daemon.service:110`) is TEMPLATE-VERIFIED ONLY — the live unit (`/etc/systemd/system/ensemble-main.service`, 0600 root:root, operator-edited 2026-11-01) is unreadable from agent context. One root-side read-only command closes it: `systemctl show ensemble-main.service -p RestartPreventExitStatus` ⇒ `=78` expected. Scheduled as **S9b** (stage-verification operator leg); if access-refused from agent context it rides the **Emperor's promote-ceremony checklist** and does NOT block staging.
- **C3 — measured median U ≤ 58s** (E2 § D telemetry leg).
- **Retirement predicate:** the Lane-4 defer-trigger is RETIRED once C1+C2+C3 all hold.
- **Pre-drafted both-way verdict (verbatim from the architect):** if the live unit returns ≠78 for `RestartPreventExitStatus`, (D) **REVERTS to DEFER-WITH-TRIGGER** until the live unit is regenerated via `adopt-unit.sh` (which restores `:110` by construction — template + splice inheritance, NOT literal carriage).
- **Rationale (path trace):** every realistic ensemble-holder port-squat path post-p2 terminates in 78 — (i) foreign claim ⇒ 78 pre-bind; (ii) foreign unclaimed holder + 6a ⇒ 78; (iii) transient holder departing inside the 30s ladder ⇒ bind succeeds. The residual exit-1 class (iv) is NON-port, and **Lane 4 never cured it either** (`RestartSec=10` ⇒ ≤2 unit starts per 10s window ⇒ `StartLimitBurst=5` arithmetically unreachable ⇒ paced-forever regardless; Lane 4 only shortens launcher-internal bursts). Insurance value on this systemd host ≈ nil.
- **Residuals disclosed:** the launchd host (GAP-1, `docs/deployment.md:327,348`) sits outside `RestartPreventExitStatus` entirely — untouched by Lane-4 or this closure; observability residual for the final report. A real cure for class (iv) would be a `StartLimitIntervalSec`/`StartLimitBurst` unit conversation — explicitly out of scope (unit = zero-touch).
- **E2 measurement leg: KEPT as pure telemetry** (H2 closure + Lane-1 constraint proof via its fast-refuse-ON cycle); it arms nothing. No `launcher.sh` edits in this lane — upheld.

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

**RATIFIED (leader, on architect validation addendum §0-V1): CLOSE via claim-lineage — A2-LDP-1 option (i), with TWO MANDATORY SHARPENINGS.** Claim v2 records the claimant's parent-cmdline lineage; same-install + live holder + lineage mismatch ⇒ refuse 78. Validating evidence (architect): additive `PortClaim` fields are invisible to old readers (`read_claim` `:322-343` reads via `.get()`); every promote-shape pin plants a lineage-less legacy claim and stays green under absent-lineage⇒non-foreign; the comparison is hash-of-recorded-tuple vs hash-of-self-parent-tuple — no `/proc` read of the recorded parent, hence reparent-immune and stable across `systemctl restart` pid churn; the refusal precedes the `:407` unlink inside `try_claim` (`:401-434`), closing the side-defect without reordering; a dead rogue's leftover claim cannot false-refuse (pid+starttime staleness `:349-350` ⇒ take-over before lineage is consulted).

**Mandatory sharpenings (adopted into A2 §5.3):** (i) the lineage tuple normalizes on the **script-name argument** (first non-flag arg after the interpreter), NOT `argv[-1]` — `bash launcher.sh --foo` or a `bash -c` wrapper changes `argv[-1]` and would mint a live-wide false-refuse on a future launcher arg; (ii) a §7 lane-walk row + docstring residual for the invocation-shape change (dev.sh claim → direct `python -m daemon` boot on the same port+tree ⇒ lineage mismatch ⇒ refuse where today = take-over — arguably intended H1 semantics, walked and documented) plus a lineage-tuple-stability control leg (counter-mutant: "hash full argv" ⇒ RED). Same-uid forgery bound stays the documented residual — ratified as intended scope (accidental-rogue class, not claim-forging adversary). **If the leader had picked (ii) re-declare instead:** A2's amputation path drops Phase-2 lineage tasks + AH-9..12 — NOT taken.

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

- **Council decision (analysis §5.4, superseded):** DEFER WITH EXPLICIT TRIGGER — promote the sliding-window variant to its own staged lane **iff** measured post-Lane-1 U > 58s OR a loop recurs. Rejected sub-variants: `-gt`→`-ge` predicate (breaks launcher + watchdog twin pins), teardown-budget half (unit + watchdog breakage).
- **Dispatcher preliminary ruling (commission):** ROUTED OUT of this lane — UPHELD (launcher.sh zero-touch throughout).
- **FINAL (architect addendum §0-V3 + §3 @ `ad814dae4`; leader-ratified): CLOSED-BY-A2, CONDITIONAL.** Full verdict text, conditions C1/C2/C3, the pre-drafted both-way reversion clause, and the path-trace rationale live in §1 §9-1 above (single source). Summary: closure dated to A2's merge commit (C1); live-unit `RestartPreventExitStatus` check scheduled as S9b (C2 — read-only `systemctl show`; access-refused ⇒ rides the Emperor's promote-ceremony checklist, does NOT block staging); E2 measurement leg KEPT as pure telemetry (C3 input + H2 closure + Lane-1 constraint proof); trigger RETIRED once C1+C2+C3 hold; if the live unit returns ≠78, (D) reverts to DEFER-WITH-TRIGGER until regenerated via `adopt-unit.sh`.
- **This lane's obligations (discharged by the plan):** (1) E2 § D telemetry leg — N≥5 cycles, legacy exit-1 path measured at a **pre-A2 tip** (NO fast-refuse knob exists; per-cycle 78 = invalid leg), direct child invocation, RESULTS file at `.agents/tester/RESULTS/<date>-rogue-p2-burst-window-U-measurement.md`. (2) The architect's obsolescence evaluation is CLOSED by the addendum itself (no further architect input required); the evidence base (measured U, path trace, residual class-iv honesty) is recorded in §1. One extra fast-refuse-ON cycle proves the new gate does not itself lengthen failed-boot U past 58s (Lane-1 constraint).
- **New finding routed OUT (addendum §3):** the UNIT TEMPLATE carries NO sha pin anywhere (only `launcher.sh` is pinned) — a template edit silently changes every subsequently adopted unit; this is the live-divergence vector behind C2. Recommended as a small scripts-side FOLLOW-UP lane — leader's call whether to queue (record-only in this corpus).

---

## 6. Worker-surfaced leader decision points (consolidated; detail in component files)

**B2 (`component-B-tier1-prod-dsn-fence.md` §11):** D1 None-identity polarity — **RATIFIED (leader): DEGRADE** with a loud warning carrying the `_self_env_source` vocabulary; guard-internal raise paths still fail closed. Normative rationale (addendum §0-V2): the live boot carries NO explicit `ENSEMBLE_SELF_ENV` (auto-derive only, `upgrade_tools.py:532-556`) and the prod name arrives via env-wins (`ensemble_config.py:126`) ⇒ the predicate fires on EVERY live boot incl. every promote leg — refuse-on-None is BF-2 live-down. D2 predicate scope — exact `ensemble_prod` only (adjudicated, honored). D3 CLI exemption — **CLOSE** (ratified; closure order per addendum §1.2-c: guard BEFORE `:150` so a refused CLI writes no artifact). D4 operator alert — resolved by the §9-4 ruling (family record + channel split). D5 H3 timing — leader's call (read-only, independent). D6 promote-lane attestation — **RATIFIED (a): rely on auto-derive**; the executor allowlist add is unnecessary (executor boots derive `live` via frozen-binary topology + prod cross-check under both HOME shapes — addendum §1.2-d). NEW stage probe (addendum §1.2-e): read-only `_self_env_marker()` resolution from the staged tree under executor env — turns BF-2 from asserted to measured-at-stage-time.

**A2 (`component-A2-socket-holder-identity.md` §13):** LDP-1 H1 close — **RATIFIED (i) with the two mandatory sharpenings** (see §2). LDP-2 fast-refuse — **RATIFIED 6a, placement BOTH**: pre-bind gate refuses on positive proof (two-FULL-rewalk samples; refusal-path U ≈ 18.6s ≪ 58s by construction); post-conflict EADDRINUSE exhaustion path (`bind_retry.py:381-390`) is ATTRIBUTES-ONLY, never refuses (`BIND_FAILURE_EXIT_CODE` untouched). **NO KNOB** — the legacy-path (D) telemetry runs at a pre-A2 tip (D-2 resolved). LDP-3 alert channel — see §9-4; marker-channel split is deliberate and pinned per-family (B2 pre-logging ⇒ stderr/journald; A2/claim-gate post-`:447` ⇒ file log). LDP-4 watchdog on THIS host — named, NOT built (ops lane). LDP-5 Lane-4 evaluation — CLOSED by the §5 final record (closed-by-A2 conditional).

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

## 9. Architecture-addendum application record (fix-up pass, 2026-11-14)

Addendum `architecture-addendum.md` @ `ad814dae4` (enriches this corpus; bind-race analysis still GOVERNS mechanics). Leader adjudications BINDING; applied in ONE fix-up pass to the component plans:

**Ratified verdicts (normative, incorporated in-place):** V1 H1 claim-lineage VALIDATED + two mandatory sharpenings (script-name-arg normalization, NOT argv[-1]; §7 invocation-shape row + lineage-stability control mutant). V2 B2-D1 = DEGRADE on None-identity with `_self_env_source` warning vocabulary; guard-internal raise ⇒ fail-closed 78; normative rationale = the predicate fires on every live boot (no explicit SELF_ENV live; prod name via env-wins), so refuse-on-None is BF-2 live-down. V3 fast-refuse 6a RATIFIED with placement BOTH (pre-bind refuses on positive proof, two-FULL-rewalk samples; post-conflict exhaustion path attributes-only, never refuses) and **NO KNOB** (legacy-path measurement at pre-A2 tip).

**Defect register disposition (addendum §5):**
- 🔴 1 live-unit evidence claims → reworded "template-verified, live-unit check pending" + C2/S9b leg — APPLIED (A2, E2, this file §1).
- 🔴 2 (D) closure contingency → dated to A2 merge (C1) — APPLIED (§1, §5, E2).
- 🔴 3 D-2 knob interface → recorded as no-knob — APPLIED (A2, E2).
- 🔴 4 D-3 lineage fragility → script-name-arg fix + §7 row + stability control — APPLIED (A2 §5.3/§7).
- 🟡 5 stale component-B anchors (`:502-515/:532-556/:513/:558-559`; §4.7 `:617-619`) — APPLIED (B2 §3/§4.7).
- 🟡 6 second DSN site `factory.py:364-369` cited (+ drift cross-ref) — APPLIED (B2); cite re-verified 2026-11-14 — the env-wins line now sits at `factory.py:360` in `create_postgres_engine` (`:316`).
- 🟡 7 P4b real-live-shape pin cell — APPLIED (B2; cross-ref in E2).
- 🟡 8 `daemon/api.py` into plan-overview Phase-1 file column — APPLIED (plan-overview).
- 🟡 9 C1 claims-dir harness fix → PRECONDITION (existing refuse-foreign leg silently green; re-based onto the shipped claims path) — APPLIED (E2, blocking).
- 🟡 10 marker-channel split (B2 stderr/journald vs A2 file-log) pinned per-family in S-legs — APPLIED (E2 S5/S7; addendum §4.1).
- 🟡 11 D-1 two-sample = full re-walk + AH-M15; D-5 claim-socket agreement folded into AH-2 — APPLIED (A2 §5.5, E2).
- 🟡 12 §5.6 fd-EACCES row; `::ffff:` fixture; metrics_listener boundary docstring line; T5/LH-2 reader dependency named — APPLIED (A2, E2).
- 🟢 13 anchor drift (`launcher.sh:3847-3849`; `classify_exit:3588-3595`) — RECORDED.
- 🟢 14 adoption-chain = template + splice inheritance, not literal carriage — RECORDED (A2, E2).
- 🟢 15 `migration_worker.py:487-489` out-of-hook residual declared in the guard docstring — RECORDED (B2).
- 🟢 16 unit-template sha pin missing → follow-up lane recommended, leader's call whether to queue — RECORDED (§5).

**Unchanged rulings (honored; version anchor updated per the finalize rulings):** CHANGELOG consolidated catch-up under **v0.30.12 ONLY** — NOT v0.30.10 (phantom .10 = a bump commit inside sweep ancestry, zero tags/artifacts; v0.30.11 = the absorbed sibling) — shaped as `[0.30.12]` carrying THREE attributed subsections ((i) this lane's new work; (ii) containment catch-up v0.29.19–v0.30.9 shipped-silent; (iii) 'absorbed from sibling staged v0.30.11' five sweep items), with the CHANGELOG forestry itself owned by the finalize lane (giter), not this implementation lane. NO heading backfill ([0.29.22]/[0.29.23]/[0.30.0]/[0.30.1] stay absent, referenced by tag); no remediation; no promote; live untouched; code-only release.

## 10. Rejected shapes (house memory, do not re-open without new evidence)

New exit codes (piecemeal launcher+unit breakage) · YAML-literal 9798 (kills the only working lever; derive shipped instead) · ppid-equality lineage (false-refuses `systemctl restart`) · full-argv lineage hash (false-refuses invocation variance) · after-ladder fast-refuse 6b as default (feeds the burst budget the fast path exists to stop) · refusing on unattributable holders (false-refuses container CI + nginx-style squatters; violates positive-proof-only) · per-version CHANGELOG backfill (invention risk) · re-arming the boot sweep as-is (disarm reason unknown) · select-by-port reclaim (killed real prod once — `stop-ensemble.sh:11-23` rule) · narrowing the sweep predicate (the orphaned-node case IS the pinned case).
