# Approver Verdict — promote-full-auto (D2 Supervisor-Deterministic Promote Flip)

Plan set: .agents/shared/planning/promote-full-auto/
Artifact: d2-supervisor-flip-design.md (rev 4, 277 lines)
Declared base: latest 6ca643ad7 (ACTUAL at review time: latest = edbf3c92f — see blocking 6)
Date: 2026-11-11 | Mode: Plan Approval (2 workers, one skill each; fan-in via todo_graph)

## VERDICT: REJECTED — Iteration 001

Both workers REJECTED independently (plan-approval: 4 blocking; decision-approval: 4 blocking).
Aggregation = dedupe-merge only (no upgrades, no new blockers, no downgrades): 8 raw → 6 blocking.

### Blocking issues (merged)

1. [A-B1 + B-B1] Vehicle contract cannot produce the 8b / verify-timeout end state
   (§5 step 7 L88; §5.3 steps 0–2 L104–107; §7 rows L148–149; §12 items 2/10).
   Step-0 predicate (loaded ∧ running ∧ program==launcher chain) is TRUE for the
   running-but-wrong-version new binary → short-circuits before LKG repoint → no
   rollback (A). Alternate reading: step 2 census fails because the broken daemon
   holds the port → RC-1 abort_supervisor_absent → live-wrong-and-armed — exactly
   what §5 step 7 forbids (B). Vehicle contains NO stop-the-running-job step; its
   scope rule covers in-window exits only. Existing 8b at promote.sh:782–880
   (stop_via_stop_script :844 → launcher_swap :846 → atomic_flip :847 →
   restart_via_launcher :857 → re-gate → close) is complete; §9's launchd-aware
   seams yield a supervised 8b with no vehicle.
2. [A-B2] §5.3 pinned order re-bootstraps (step 3) BEFORE terminal txn close
   (step 5) — L110/L112/L199. Re-bootstrapped launcher runs _journal_sweep
   (launcher.sh:3941–3942, impl :3226+) against the still-OPEN txn; AM-P2-18
   leave-OPEN requires owner alive AND heartbeat fresh (launcher.sh:2884–2896);
   vehicle is lockless (#6) so lock_heartbeat (lib.sh:2650) cannot refresh;
   txn age can exceed SWEEP_STALE_S=600 (launcher.sh:78). Sweep's flipped branch
   (_js_sweep_flipped_disposition :2831) may repoint current or emit a
   serving-green commit, racing the vehicle's journal_close_txn (lib.sh:2026–2029).
3. [B-B2] Auto-detection tier fails OPEN into the incident mechanism while §6
   claims fail-closed. §6 "zero → script path" + §9 cell auto/SCRIPT_NOHUP =
   "conforming" (L177) + ENSEMBLE_SUPERVISION absent from all env files (live
   mode = auto, lib.sh:5733) + launchctl always present on macOS (§7 row-1
   discriminator never fires) ⇒ a detection miss on the operator's Mac degrades
   to the nohup hand-back (lib.sh:5688) while launchd stays armed = the
   2026-11-11 incident, reported "conforming". Also unstated: fault→rc78 mapping
   for declared launchd; domain-probe failure handling (system/ as non-root).
4. [A-B3] Census predicate "INSTALL_DIR-anchored processes" undefined (L86/L21/
   L106/L142/L144/L206). Repo's own stop-ensemble.sh:528–531 documents Tier 1a
   cannot match the relative-argv launcher — and lib.sh:5688 produces exactly
   `nohup ./launcher.sh` (cwd=INSTALL_DIR). A path-only census passes over that
   straggler → bootstrap beside it → two-launcher race recreated. §12 item 9
   pins routing, not detection.
5. [B-B3] B7 lineage gate grounded on `launchctl print` "pid/forks closure" with
   no substrate (L82/L127/L140/L202). Repo's entire launchctl-print knowledge =
   state/pid (agents/gaia/workflow.md:189; .inspiration-projects/openclaw
   launchd.ts:247–273 parses state/pid/last-exit only, "not a stable API"). No
   forks field; stub-authored pin is vacuous.
6. [A-B4 + B-B4] Header provenance stale/false: base 6ca643ad7 vs actual latest
   edbf3c92f (PR1 branch MERGED); branch tip add499b93 touches 3 files
   (upgrade_tools.py +108/−8 incl. _resolve_scripts_dir + new refusal tokens,
   docs/runbooks/upgrade-overhaul-watch.md, tests/unit/tools/test_upgrade_tools.py)
   — not "arm-preflight only"; PN-4's "in-review parallel branch / disjoint
   region" framing obsolete (anchors :1255/:1304–1317 unaffected — hunks ≥:1985).

### Selected non-blocking notes (full lists in worker reports, in-session)

- invariant wording over-claims post-bootout (straggler exists until step 3); §4
  Risk axis "Lowest" mis-rated while 🔴 residuals open (re-rate Med-Low);
  alternative-C dismissal verified correct (plist:43–56/:112–118 +
  launcher.sh:3749–3762); armed-flip alternative correctly unavailable
  (launcher_swap non-atomic cp, lib.sh:4987) but doc should say why.
- truncated-drain (live 5s ExitTimeOut vs 20s Apple default; §8 mislabels) is a
  structural per-promote checkpoint void — should be 🔴 + activation gate (§15.1).
- SIGKILL-row recovery understated/overstated: comp3 fast path
  (launcher.sh:3288–3323) reclaims immediately (stronger than doc), but
  rollback_safe gate (:2700–2709) can HALT-FOR-HUMAN on the broken current, and
  next promote refuses 78 until the txn clears (journal_open_txn lib.sh:1951–1958).
- PN-4: two new TERMINAL event names must NOT enter frozen _TERMINAL_EVENTS
  (upgrade_journal.py:2088–2144 pinned) — state their home (sibling constant /
  detail-string token); PN-3's supervisor= token has no key in the
  supervision/unit/outcome triple (lib.sh:6465–6491) — ride the event detail.
- tests: twins ladder fenced on non-systemd hosts — add unfenced launchd lane;
  no pin asserts the abort END STATE; ordering/byte-identity pins should assert
  on stub call logs / exec traces, not text bans (doc's own L97 lesson); Darwin
  e2e real-seat leg needs a distinct test label + cleanup + opt-in.
- S11/S13 "not enumerated" rows leave checklist scope unbounded (cycle-1 list
  absent from repo) — fold or attach before using §16 as merge gate.
- anchor drift (1–5 lines, non-load-bearing): lib.sh:6490/6499/6216–6254,
  upgrade_journal.py:3789–3790, upgrade_tools.py:2592/2597, lib.sh:6453;
  incident narrative's flat-binary resolution is conditional on current/ absent.

### Worker sessions (reports received verbatim, in-session)

- approve-worker-plan-choreography 4ac914d3-b928-4b9c-a681-adb04e67c499 — plan-approval
- approve-worker-decision-mechanism 9256ce09-ab18-4d56-938e-138d3f0b724e — decision-approval

Next iteration on re-submission: 002 (max 3).

---

## VERDICT: REJECTED — Iteration 002

Date: 2026-11-11 | Mode: Plan Approval (2 workers, one skill each; fan-in via todo_graph)
Artifact: d2-supervisor-flip-design.md (rev 5, 260 lines)
Declared base: edbf3c92f — VERIFIED by both workers independently (HEAD = latest =
edbf3c92f622b87f0c7758d10a724d00c68d8219; header B6 accurate; tree dirt = concurrent lanes,
not the artifact).

Both workers REJECTED independently (plan-approval: 2 blocking; decision-approval: 3 blocking).
Aggregation = dedupe-merge only (no upgrades, no new blockers, no downgrades): 5 raw -> 4 blocking
(A-B1 == B-B2 = one defect, merged).

### Blocking issues (merged)

1. [A-B1 + B-B2] Abort-vehicle txn-discharge step is NOT executable in its own context, and its
   alternate branch violates the step's own invariant. (§5.3 step 2a L97; §5.3 "lock-free by
   construction" L91; §12 item 2 L194.)
   Step 2a: close the txn terminally OR refresh heartbeat via standalone `lock_heartbeat`
   (lib.sh:2650) -- "either way ... must NEVER see an OPEN+flipped txn".
   (a) `lock_heartbeat` is a GUARANTEED NO-OP in the vehicle: it requires lock-dir + owner == $$
   (lib.sh:2650-2656); both vehicle entry points release the lock FIRST (`_enforce_deadline`
   releases at lib.sh:561 -> exit 78 at :562; `_signal_handler` sets _LOCK_RELEASED=1 at :3000 ->
   releases at :3003 -> exit 143/129/130 at :3007-3010); `lock_release` does `rm -rf "$lock"`
   (lib.sh:2666-2678). It can refresh neither the lock nor the txn heartbeat (`journal_heartbeat`
   is documented as ownership-guarded BY `lock_heartbeat`, lib.sh:2050-2094).
   (b) The heartbeat branch VIOLATES the step's own invariant: fresh heartbeat + dead owner leaves
   the txn OPEN (launcher.sh:2893-2895 leave-OPEN conjunct) => the sweep DOES see OPEN+flipped; the
   txn stays open (next promote refused, `journal_open_txn` lib.sh:1951-1958) with flipped:true
   against a rolled-back `current` -- an end state the new abort END-STATE pin (§12 item 2: "txn
   closed, current==LKG") does not cover. After the vehicle exits the owner is dead and the
   heartbeat goes stale at 300 s (launcher.sh:97 HEARTBEAT_STALE_S) => a later boot's comp3 fast
   path (launcher.sh:3288-3323, hb_stale AND owner_dead -> immediate reclaim) rolls `current` back
   past the just-restored LKG. The §12 item 2 sweep-interaction cell would FAIL on that branch.
   FIX: make the terminal close the only step-2a option (`journal_close_txn` = `journal_update
   "in_flight" "null"`, lib.sh:2027-2029, is lockless-implementable), or replace `lock_heartbeat`
   with the ownership-agnostic `journal_heartbeat` AND state who closes the OPEN txn afterwards
   and what the end state is. Reconcile with the §5.3 doctrine amendment (L104).

2. [A-B2] Verify-failure (8b) end state does not hold against the repo's 8b gate-first contract;
   §5.3's scope sentence over-binds. (§5 step 7 L76; §7 matrix row "Verify: mismatch after
   provenance (8b, B1)" L139; §5.3 scope L87.)
   Doc's 8b = bootout -> repoint current to PREV -> bootstrap -> re-gate, end state "PREV serving,
   armed". The repo's 8b runs a manifest/quarantine gate FIRST, three HALT-for-human branches, NO
   repoint: PREV quarantined (promote.sh:810-817), PREV missing (:819-826), PREV not `rollback_safe`
   (:828-836 -> "daemon stays on $VERSION (degraded)"). Under repo ordering the supervisor stays
   armed and the daemon still runs the BROKEN $VERSION => exactly "live-wrong-and-armed", the state
   the doc forbids at L76. Under the doc's ordering (bootout before repoint) the same branch lands
   DARK + DISARMED on the broken binary with NO re-bootstrap -- a state the doc names nowhere (§7
   names "Dark + disarmed" only for pre-flip rows). §7 also has NO row for the 8b stop-failure halt
   (promote.sh:846-847, "txn left open") => DARK + DISARMED + an OPEN txn. Additionally §5.3's
   normative scope sentence ("every exit path inside the bootout->bootstrap window routes through
   the vehicle") must be scoped to the promote's PRIMARY window -- 8b itself opens a SECOND
   bootout->bootstrap window. (§5 step 7's summary also omits the repo's `launcher_swap "$PREV"`
   (promote.sh:850), which restores the root INSTALL_DIR/launcher.sh -- the file launchd's
   ProgramArguments points at (ensemble-prod.plist:100).)
   FIX: restate the 8b row against the repo's gate-first ordering; name the DARK+DISARMED-on-broken
   end state and the stop-failure halt; scope §5.3's rule to the primary window; add the
   `launcher_swap "$PREV"` root-launcher restore to the §5 step 7 summary.

3. [B-B1] The shell classifier's declared-mode ladder is never extended to accept `launchd` =>
   `ENSEMBLE_SUPERVISION=launchd` degrades to the nohup script path and is reported "conforming"
   -- the incident mechanism, silent. (§9 L158; §15.2 L235; §6 L108; lib.sh:5977-6042 mode ladder,
   :6037-6041 `*)` garbage arm, :5964-5965, :6218-6219; upgrade_journal.py:3784-3788.)
   The doc enumerates the launchd arms in the outcome-map ladder (lib.sh:6207-6254) and the python
   twin (upgrade_journal.py:3755-3791) but never the CLASSIFIER ladder; §9 freezes the vocabulary
   "declarations" citing :5778-5781 (declared vocabulary = unit|script|auto). As written,
   ENSEMBLE_SUPERVISION=launchd hits the `*)` arm => warn-once "garbage ENSEMBLE_SUPERVISION=
   'launchd' -- failing toward script", rc 0, SUPERVISION_MODE=script / SUPERVISION_STATE=
   SCRIPT_NOHUP => map cell script x SCRIPT_NOHUP => `conforming` (lib.sh:6218-6219; twin
   :3932-3938) => the promote takes the script path -> nohup hand-back (lib.sh:5688) WHILE the
   launchd KeepAlive job is still armed => the 2026-11-11 incident mechanism reported
   ENSEMBLE_SUPERVISION_OUTCOME=conforming. The same hole covers ANY typo'd mode: the `*)` arm
   returns rc 0 BEFORE the auto-derive chain, so §6's Darwin fail-closed rules (i)/(ii) are
   unreachable for unrecognised declared values => "Darwin never silently degrades" holds only for
   the three known modes. §12's "parse-garbage" cell names no required behaviour.
   FIX: add a `launchd)` arm to lib.sh:5977-6042 modelled on the `unit)` arm (:5986-6034, incl. the
   host-capability refusal rung at :6019-6023 that §6 already cites); make the garbage arm
   fail-closed on Darwin; state it in §9/§16; pin "declared=launchd on a launchd-less host -> rc 78
   supervision-not-verified" as a named test cell.

4. [B-B3] The system-domain probe's refuse-on-unreadable rule makes the supervised path refuse rc 78
   BY CONSTRUCTION on the operator's Mac, and the privilege it requires is never listed as an
   activation precondition. (§6 L115; §7 L130; §12 item 4; §15 L232-238; §17 L254;
   Makefile:409-424; docs/setup.md:217-218; docs/deployment.md:87-91.)
   §6 mandates "launchctl print system/ as non-root -- output garbage, empty, or unreadable => ALL
   refuse". The repo's entire operator lane is NON-ROOT by design: the supervisor is a USER
   LaunchAgent (`make plist-install` -> $(HOME)/Library/LaunchAgents, `launchctl bootstrap
   gui/$(id -u)`; Makefile:409-424; docs/setup.md:217-218; docs/deployment.md:87-91 "Never sudo the
   two plist targets"), and no `sudo` appears anywhere in scripts/upgrade/*.sh. So on the operator's
   Mac the supervised path refuses rc 78 at preflight by construction, while §15's activation gate
   (ExitTimeOut raise, ENSEMBLE_SUPERVISION=launchd, ENSEMBLE_SUPERVISION=script on unsupervised
   Darwin hosts) never mentions privilege and §17's pending decisions omit it => the headline
   "fully automatic next promote" on this Mac is NOT delivered by the design plus the listed
   operator steps. The §12 item 12 Darwin e2e real-seat leg inherits the same problem.
   FIX: either scope the system-domain probe (treat an unreadable system domain as out-of-scope for
   a user-agent install ONLY when the gui-domain probe yields exactly one match and the declared
   label resolves there -- keeping refuse for an explicitly pinned label), or state "the promote
   must run with privilege to read the system domain" as an explicit §15 activation-gate step and a
   §17 pending decision.

### Selected non-blocking notes (merged; full lists in worker reports, in-session)

- Vehicle THIRD end state unnamed: §5.3 steps 4-5's biconditionals leave "census passed AND
  re-bootstrap attempted AND pid NOT alive" matching NEITHER branch, while §12 item 2 pins
  "disjointness (exactly one event per run)" => needs a third disposition/event or an explicit
  "re-bootstrap failed => not restored" rule. (A)
- §1/§2 flat-binary co-condition incomplete: `resolve_binary` (launcher.sh:3653-3670) PREFERS
  current/ensemble-prod and reaches the flat path only when absent/non-executable => the doc's
  stated premise ("flat existed and was executable") is necessary but NOT sufficient under the
  cited code; "the pre-ADR-004 launcher" is unverifiable from the repo. §14's "preflight neutralizes
  it on the supervised path" overstates (the barrier + single bootstrap neutralises the RACE; a
  momentarily unresolvable current/ensemble-prod could still let the one bootstrap start the flat
  old binary -- caught loudly by the version gate -> 8b). (A+B)
- §3/§4 letter collision: A/B/C denote sub-mechanisms in §3 and different OPTIONS in §4 => the
  matrix cannot be read against the recommendation without ambiguity. (B)
- §4/§5.3 alternatives analysis omits the repo's canonical restart primitive `launchctl kickstart
  -k` (docs/deployment.md:118/:370-372) and the "per-instance supervision guard" option; the doc's
  bootout+bootstrap is arguably SAFER for this plist (KeepAlive{SuccessfulExit=false} matches exit
  143 -- docs/deployment.md:328 GAP-2), but that reasoning is absent. (B)
- TERM ownership on the supervised path UNSTATED: `launcher.sh:3683-3690 _handle_signal` forwards
  the signal to the child on every invocation (no once-guard) and the launcher stays alive up to
  CHILD_STOP_WAIT_S=70 (launcher.sh:60) => a bootout-initiated TERM plus the ladder's TERM could
  BOTH reach the daemon, voiding the graceful checkpoint write that §15.1's ExitTimeOut raise is
  meant to restore. The doc must state who delivers the daemon's single TERM and reconcile §8's
  min(ExitTimeOut, knob) attribution with the ladder. (A) -- escalate to Blocking if the operator
  confirms `launchctl bootout` TERMs the job (live probe not permitted this cycle).
- Declared-vs-resolved plumbing for the new launchd mode unspecified: `supervision_classify` defaults
  SUPERVISION_MODE=script (lib.sh:5963-5966) and only ever resolves to `unit`;
  `supervision_preflight` calls the map with the RESOLVED mode (lib.sh:6449) while the repo's
  RESOLVED-MODE EQUIVALENCE note (lib.sh:5802-5806) requires resolved cells to agree with declared
  cells row-for-row; §9's two launchd rows carry DIFFERENT reason strings => the twins' byte-identity
  pin depends on which value reaches the map. Related: on the Darwin zero-match path the preflight
  refuses (rc 78) BEFORE the map is called (lib.sh:6442-6449) => the Darwin auto x SCRIPT_NOHUP ->
  fault row is reachable only via a direct map call. (A)
- Twin host-awareness under-specified: `uj.supervision_outcome(declared, verified, unit)` is a 3-arg
  PURE function (upgrade_journal.py:3893-3895) pinned byte-identically by 19 cells
  (tests/unit/tools/test_supervision_python.py:187-224; tests/test_supervision_twins.sh:14/:100);
  the Darwin host-aware cell needs a shared host predicate threaded into BOTH twins (shell `uname -s`
  vs python `sys.platform`), and the doc names neither the predicate nor the two pins to amend. The
  daemon-side twin's boot advisory (daemon/services/upgrade_journal_sweep.py:426) would also run a
  launchd domain dump inside the daemon. (B)
- §6 refusal-token form breaks the repo's grep-ability convention: the global carries the UNPREFIXED
  token (lib.sh:6022 "unit-incapable-host") while the journal/_refuse reason carries the PREFIXED
  form (lib.sh:6447, pinned by tests/test_supervision_stop_handback.sh:939
  "reason=supervision-unit-incapable-host"). The doc's single
  `SUPERVISION_REFUSE_REASON=supervision-not-verified` form must be split the same way. (B)
- §5/§11 "provenance check" not pinned to a repo mechanism: the repo's provenance surfaces are the
  build-provenance sidecar (lib.sh:3245-3274) and the restart-lane ENSEMBLE_RESTART_LIVE gate
  (lib.sh:1457-1471) -- neither is a daemon-identity check => state what the check compares
  (pre-stop pid inequality? journal flipped marker? /livez version?) so it is testable. (B)
- §8 drain claim stronger than the repo's own live proof: docs/deployment.md:383 records "bootout ->
  job unloaded ... graceful drain ~1 s (checkpointer closed, MCP pool drained, DB disposed)"; the
  pessimistic reading supports the §15.1 gate but the two are not reconciled. (B)
- §12 "must stay green" vs B3: test_supervision_stop_handback.sh C10b (:941-959) pins the rc 0 +
  "launcher started (nohup)" contract for an auto-resolved classification on an incapable host --
  directly contradicted by B3's Darwin auto-zero-match => rc 78; the C10b expectation change is NOT
  listed among the same-PR amendments. (B)
- §12 "executable-site census 17x exit-78" does not reproduce from any stated criterion (the raw
  pair 36/13 reproduces exactly; 28 non-comment exit-78 sites) -- documentation-only since the doc
  disclaims the fixed count, but the criterion should be defined. (A)
- §5.2/S4: `LAUNCHCTL_BIN` absolute default does NOT mirror `SYSTEMCTL_BIN`'s PATH-resolved default
  (lib.sh:5043-5049) -- the latter is PATH-resolved PRECISELY so stub seams work; the absolute
  default is defensible (fail-closed vs PATH hijack) but the doc must say which property it wants and
  how the two interact. (A)
- The vehicle's "lock-free by construction" property has no pin, although it is load-bearing (it is
  exactly what makes the cited `lock_heartbeat` unusable). (A)
- Exit-code semantics of the signal path: the vehicle's exit 1 replaces the repo's signal-attribution
  exits (143/129/130, lib.sh:3007-3010) when it runs inside `_signal_handler`; the doc promises
  exit 1 but does not say which code the trap path yields. (A)
- Anchor drift (<=4 lines, non-load-bearing): lib.sh:6465 -> :6467 (journal_mark_supervision def);
  lib.sh:6499 -> :6495 (gate_livez; gate_version is :6500-6511); :5778-5781 is the vocabulary
  DECLARATION (matrix rows are :5790-5805); lib.sh:6045-6046 -> the uname check is at :6046. All other
  operational anchors re-read by both workers reproduced EXACTLY (~40 each: launcher.sh:78/:2884-2896/
  :3288-3323/:2700-2709/:3653, lib.sh:525/:562/:5688/:4611/:1951-1958/:5064/:6046/:6343-6344/:5815,
  lib.sh:6453/:6490 <-> upgrade_journal.py:3628, upgrade_tools.py:1255/:2592/:2597,
  upgrade_journal.py:2088, stop-ensemble.sh:504/:748-768/:528-531/:675-678, Makefile:371-372/:409-424,
  ensemble-prod.plist:57/:109, stage.sh:390-426, systemd-adoption.md A2 rows 1 & 5,
  gaia/workflow.md:189, openclaw/src/daemon/launchd.ts:247-273/:509, twins 19->25). (A+B)

### §16 boundedness (caller's pointer) -- NON-BLOCKING (both workers, independently)

Worker A (plan-approval): "bounded by the document's own content. Every S-item referenced in the body
(S2, S3, S4, S5, S8-S17) is enumerated in §16 -- no orphans ... S11/S13 remain unenumerable BY THE
DOCUMENT'S OWN ADMISSION (§16, §18), with the closure assigned to the approver and 'This doc + §16 is
the bounded canon' declared; the substance the commissioning note cites IS present in the document
(§12 must-stay-green 'test_supervision_stop_handback.sh' (C10 + C10b :941-959) -- verified at
tests/test_supervision_stop_handback.sh:935-961 -- and the nohup-absent cell at §12 item 7 / §16
cheap wins). Treated as a disclosed, owner-assigned closure item, not a blocking gap; note that §18's
own sentence ('this is the one dimension on which "none blocking" would over-claim') is an editorial
over-reach given that the doc contains the S11/S13 substance in §12/§16."
Worker B (decision-approval): "judged on the doc's own contents only. The doc states its own limit
honestly ... Every §16 item is either an anchored implementation instruction or an explicitly-flagged
unknown; the missing external review record does not itself produce a blocking finding."
=> §16 IS bounded by the doc. S11/S13 are a DISCLOSED, owner-assigned closure item (approver to
enumerate or strike at closure), NOT a blocking gap. The doc's own §18 over-reach sentence is a
wording defect (Note), not a blocker.

### Worker sessions (reports received verbatim, in-session)

- approve-worker-plan-choreography-002 e60b3fb5-a999-4023-84db-1bba3106d366 -- plan-approval -- REJECTED (2 blocking)
- approve-worker-decision-mechanism-002 baa265d4-da1a-44af-b312-c2fd0586e83c -- decision-approval -- REJECTED (3 blocking)

Next iteration on re-submission: 003 (max 3).

---

## VERDICT: REJECTED — Iteration 003 (FINAL — ESCALATED)

Date: 2026-11-11 | Mode: Plan Approval (2 workers, one skill each; fan-in via todo_graph)
Artifact: d2-supervisor-flip-design.md (rev 6, 350 lines)
Declared base: edbf3c92f — VERIFIED BY THE APPROVER directly (HEAD = latest = origin/latest =
edbf3c92f622b87f0c7758d10a724d00c68d8219; header base claim accurate).

Both workers REJECTED independently (plan-approval: 2 blocking; decision-approval: 4 blocking).
Aggregation = dedupe-merge only (no upgrades, no new blockers invented by the approver), with ONE
permitted downgrade (B-B4 -> Note, reason stated in Notes): 6 raw -> 5 blocking.

BOTH WORKERS independently judge the CORE B-SPINE ARCHITECTURE SOUND — the M1 bootout barrier
(launchctl bootout UNLOADS the job, so KeepAlive cannot re-arm inside the flip window), M2
"promote starts nothing", the Tier-1a/1b census, and the terminal-close-before-re-bootstrap
prescription are all verified correct against the repo. The rejections are NOT architectural: they
are end-state derivations (3) and Darwin detection short-circuits (2) that would each leave a
reachable path to "broken binary live and armed" or a silent boot of the possibly-broken current.

### Blocking issues (merged, 5)

1. [A-B1] The comp3 fast-path precondition is misstated, and the resulting end state is UNNAMED.
   (§5.3 case-2 truth L121; §5.3 step-2a parenthetical L114; §7 row "Promote SIGKILLed mid-window"
   L179; §14 residual L267; §15.5 runbook L281.)
   EXPECTED: the doc's recovery narrative matches the repo's reclaim condition.
   FOUND: `launcher.sh:3319-3324` fires comp3 ONLY when `hb_stale=1 AND owner_dead=1`; else it logs
   "is fresh ... leaving alone" and returns 0 (txn left OPEN, sweep does nothing). `HEARTBEAT_STALE_S=300`
   (launcher.sh:97), `SWEEP_STALE_S=600` (launcher.sh:78). The promote refreshes `in_flight.last_heartbeat`
   every 30 s (lib.sh:2650-2663), so at SIGKILL time the heartbeat is <=30 s old => FRESH => `hb_stale=0`
   => comp3 does NOT fire => the next boot leaves the txn OPEN and the launcher boots the FLIPPED,
   possibly-broken `current` (resolve_binary prefers `current/`, launcher.sh:3653-3670; _journal_sweep
   runs before it, launcher.sh:3941-3942) with NO halt-for-human. The doc's named outcome ("comp3
   immediate reclaim ... HALT-FOR-HUMAN on possibly-broken current") is therefore wrong for the most
   likely sub-case, and the real end state (txn OPEN + boots the flipped current, silently) is named
   nowhere. Same false premise at L114 (a fresh heartbeat with a dead owner cannot trigger comp3; the
   reclaim, if any, arrives later via the >600 s stale path).
   FIX: (a) correct the comp3 precondition everywhere to `hb_stale AND owner_dead` (heartbeat older
   than 300 s, not merely owner-dead); (b) add the missing end state to §7 — heartbeat still fresh at
   next boot => sweep leaves the txn OPEN, launcher boots the flipped `current`, no reclaim, no halt;
   (c) fix §15.5 to state the operator's real precondition (wait until `now - last_heartbeat > 300 s`
   or txn age > 600 s before the manual bootstrap, otherwise it will not reclaim); (d) correct R7's
   parenthetical at L114; (e) consider naming the launcher's serving-green COMMIT path
   (launcher.sh:2572-2646) as a third possible outcome at the next boot.
   NOTE: R7's PRESCRIPTION (`journal_close_txn` only) is correct and unaffected — `journal_close_txn` =
   `journal_update "in_flight" "null"` (lib.sh:2027-2029) clears the whole `in_flight` object including
   `flipped`, and `journal_update`/`atomic_flip` are lockless, so the vehicle's "lock-free by
   construction" property holds as written.

2. [A-B2] The 8b manifest/quarantine gate has FOUR HALT-for-human branches in the repo; the doc
   names THREE. The unnamed branch leaves the supervisor ARMED on the broken binary — the exact hazard
   the design's own rule exists to prevent. (§5.4 step 1 L129-133; §7 row L176; §12 cell 11 L243;
   §19 anchor index.)
   EXPECTED: every gate branch gets the design's own named safe end state (DISARM => DARK + DISARMED,
   journal state named, loud operator message).
   FOUND: `promote.sh:794-808` — `PREV` empty ("no previous release to roll back to": close txn +
   `journal_set_current "$VERSION"` + `journal_set_previous "null"` + exit 78) is NOT named in §5.4,
   §7, §12 cell 11 or §19. The doc's L129 says "three HALT-for-human branches" and lists exactly three
   (quarantined / missing / not `rollback_safe`). Because the gate runs BEFORE the stop
   (promote.sh:845), this branch leaves the launchd job LOADED AND ARMED on the new (broken) binary,
   with KeepAlive{SuccessfulExit=false} re-arming it — violating the doc's own L133 rule ("the supervisor
   must NOT be left armed on a broken binary => DISARM"). Reachability: `PREV="$OLD_CUR"`
   (promote.sh:794, captured at :593 from the journal's `current`) is empty when the journal's `current`
   is null (e.g. a first promote on a fresh install).
   FIX: add the fourth branch to §5.4 step 1 with the same DISARM + DARK + named-state treatment, add a
   §7 row, add it to §12 cell 11's test cells, and extend the §19 anchor index with promote.sh:800-808.
   (The doc's other three anchors verified exact: :810-817 quarantined, :819-826 missing, :828-836 not
   `rollback_safe`; :846-847 stop-failure => DARK + DISARMED + OPEN txn + exit 1; :850-851
   `launcher_swap "$PREV"` + `atomic_flip`.)

3. [B-B1] The abort vehicle's rollback repoints `current` but does NOT restore the root launcher, so
   the design's own "LKG serving, armed" end state is not achievable by the specified steps and the
   abort path runs a version-skewed launcher against the old binary. (§5.3 step 1 L112 vs §5.4 step 3
   L135; §7 abort rows L171/L172/L178; §12 abort END-STATE pin.)
   EXPECTED: the vehicle's rollback restores the same state the repo's own 8b restore restores.
   FOUND: §5.3 step 1 says only "If the journal flipped -> roll `current` back to LKG first" — it names
   no mechanism and no `launcher_swap`. §5.4 step 3 DOES `launcher_swap "$PREV"` + `atomic_flip "$PREV"`
   and states why: `launcher_swap` restores the root `INSTALL_DIR/launcher.sh`, the file launchd's
   ProgramArguments points at (ensemble-prod.plist:100). The repo's own 8b does both (promote.sh:850-851),
   and every release ships its own launcher (stage.sh:425-426), so the root launcher IS version-specific.
   After a post-flip abort the vehicle leaves the root launcher at the NEW version; step 3 re-bootstraps
   -> launchd execs the new launcher -> it resolves `current/ensemble-prod` = the LKG binary. The skew
   §5.4 step 3 exists to prevent is exactly what the abort path produces.
   FIX: §5.3 step 1 must name both actions in order — `launcher_swap "$LKG"` (restore the root launcher
   launchd execs) THEN `atomic_flip "$LKG"` — mirroring §5.4 step 3; add "root launcher == LKG release"
   to the §7 abort end-state row and to the §12 abort END-STATE pin. (Related, non-blocking: the doc
   should also name the LKG SOURCE — `in_flight.displaced_version` (written at lib.sh:1971) or `$OLD_CUR` —
   and distinguish the journal `current` FIELD from the `current` SYMLINK; a field-only "roll back"
   would leave the broken binary live.)

4. [B-B2] On Darwin the AUTO path short-circuits BEFORE any launchd probe, so the design's own refusal
   row is unreachable and the 2026-11-11 incident reproduces verbatim, reported as "conforming".
   (§5.2 L97-98; §6 auto tier L149; §7 row "Darwin + auto + zero-match" L164; §16 checklist L285.)
   EXPECTED: the Darwin auto path probes launchd before it can fall back to SCRIPT_NOHUP, and the
   zero-match refusal is reachable for `declared=auto` (today's most likely declaration — §15.2 has the
   operator ADDING the declaration).
   FOUND: `lib.sh:6046-6049` — the auto-derive chain returns SCRIPT_NOHUP + rc 0 for ANY non-Linux
   host, before any detection. The design names `:6046` as the root cause (§2, §19 anchor table) but its
   NORMATIVE instruction (R9, §16) is only "classifier gains a `launchd)` arm modelled on the `unit)` arm"
   — a DECLARED-mode arm (lib.sh:5986-6034), not the auto chain. Implemented literally: Darwin + auto/
   unset -> SCRIPT_NOHUP -> map cell `auto x SCRIPT_NOHUP` = conforming -> the promote takes the nohup
   hand-back (lib.sh:5688) with the launchd job still loaded and armed -> the incident, reported
   `ENSEMBLE_SUPERVISION_OUTCOME=conforming`. §7 L164 claims a refusal the code path cannot produce.
   FIX: add a normative requirement + checklist item — "the non-Linux short-circuit at lib.sh:6046 is
   re-scoped so the Darwin auto path executes the §6 Auto-tier launchd probe and only then falls back;
   the Darwin zero-match refusal is emitted from that path" — and add a pin: Darwin + auto never returns
   SCRIPT_NOHUP without a launchctl probe.

5. [B-B3] On Darwin the `script` and opt-out (`0|false|no|off`) arms also short-circuit BEFORE any
   detection, so a supervised Mac can silently take the nohup hand-back with the supervisor armed —
   the incident mechanism, with a "conforming" report. (§5.2 bullet 1 L97; §6 L145; §7 rows L163-167;
   §15.2 L278.)
   EXPECTED: on Darwin, every declared mode that would route to the nohup hand-back is either refused or
   loudly reality-checked ("Darwin never silently degrades").
   FOUND: `lib.sh:5978-5981` (`0|false|no|off`) and `:5982-5985` (`script`) return SCRIPT_NOHUP, rc 0,
   unconditionally, before any detection. The design closes only the GARBAGE arm on Darwin (§6 L145) and
   explicitly blesses the rest: L97 "Darwin with `ENSEMBLE_SUPERVISION=script` declared explicitly: the
   exact existing pid-stop -> flip -> nohup hand-back ... Byte-identical, pinned" — and §15.2 then
   instructs operators to use that exact value on Darwin hosts. On the operator's SUPERVISED Mac that
   yields: script x SCRIPT_NOHUP = conforming -> pid-stop (launcher exits 143 -> KeepAlive re-arms after
   ThrottleInterval 10) -> `launcher_swap` `cp`s over the root launcher while the supervisor may be
   exec'ing it -> atomic_flip -> nohup hand-back -> second launcher -> the port race. The repo's own
   doctrine is the opposite (`lib.sh:6012-6013`: "silently degrading an explicit assertion is the
   r-f82e surprise class" -> refuse).
   FIX: either (a) on Darwin, make the `script` (and opt-out) arm run the §6 launchd reality probe and
   REFUSE (rc 78) when a job matching the program predicate exists — mirroring the unit arm's refusal
   rung at lib.sh:6019-6023 — or (b) make it a loud, journaled disposition ("operator asserted
   unsupervised; a launchd job IS present"); never a silent conform. Add it to §7's refusal table and to
   §12's detection cells.
   NOTE: #4 and #5 are one CLASS (the Darwin refusal set is unreachable for every mode value except the
   new declared `launchd` arm) at two distinct code arms; land them as one coherent change.

### Selected non-blocking notes (merged)

- S11/S13 — DOWNGRADED from blocking (B-B4) to a note, with reason: the doc DISCLOSES them as
  unenumerable (§16 L287, §18 L301: "content was never transmitted to the architect and the cycle-1 list
  is not in the repo ... flagged for the approver to enumerate or strike at closure"), the content exists
  neither in the doc nor anywhere in the repo (verified: the planning dir contains only this document), and
  worker A — reviewing fresh — struck them outright rather than treating their absence as a defect in
  the design. APPROVER CLOSURE RULING (this is the closure the doc asks of the approver): S11/S13 are
  STRUCK from this design's standing normative set; nothing in this plan may be said to carry them, and
  any requirement they once held must be re-commissioned separately. §18's self-declared over-claim
  dimension is therefore closed.
- Vehicle exit-code overload: the vehicle exits 1 for non-signal aborts where the env may NOT be
  recovered, while `promote.sh:58-61` publishes 1 = "rolled back (env recovered)". State a distinct code
  or name the caller expected to read the caveat.
- §5.4 converts the three PREV-gate branches from "serving (degraded)" to "DARK + DISARMED" — a
  deliberate availability regression under launchd that belongs in §4's matrix and §14's residuals.
- Program predicate resolves one side only: `realpath(argv[1]) == $INSTALL_DIR/launcher.sh` compares a
  resolved path against the LOGICAL install dir. The repo's own discipline matches both forms
  (stop-ensemble.sh:260 PHYS_DIR, :423-424 ESC_DIR/ESC_PHYS); on a symlinked install the predicate fails
  => rc 78 LAUNCHD_MISCONFIGURED by construction on the target host.
- §6's host-capability refusal rung is worded so it cannot fire on Darwin ("non-Darwin AND no resolvable
  launchctl AND no matching job"); restate it as "host cannot honor launchd (not Darwin OR no resolvable
  launchctl)".
- The `ENSEMBLE_SUPERVISION_OUTCOME=` line is never emitted on the refusal path (`_refuse` exits 78 at
  lib.sh:7048) — drop "outcome fault" from that row or specify that the refusal path emits it.
- Pin the twins CELL LIST, not the count ("19 -> 25" holds only if the new launchd arm reuses the existing
  default reason string); the declared-mode vocabulary has more sites than §9 names (lib.sh:5733,
  :5778-5781, :6192; python upgrade_journal.py:3591, :3898), and the python counterpart of lib.sh:5778-5781
  is :3591-3612, not :3628. §9(a)'s `auto|LAUNCHD_MANAGED|<label>` row must not branch on the label (only
  the `unit` arm consults the unit column — pinned at tests/unit/tools/test_supervision_python.py:222-223).
- Lane-split sequencing is unstated: D2 (lib.sh) depends on D1 (stop-ensemble.sh internals — the census
  must be a single source) — either D1 lands first or the classification is duplicated. Also state
  restart.sh's required interim behaviour: `restart.sh:199` calls the GLOBAL `supervision_preflight`, so
  after D2 lands it classifies LAUNCHD_MANAGED on a launchd host while D3 (restart lane) has not landed —
  fail closed until D3 does.
- TERM-suppression ownership conflict: §5.3 step 3a calls the ladder-side TERM suppression "the in-scope
  fix", but §9's lane split assigns `stop-ensemble.sh` internals to D1 and §13 excludes D1 fixes; today the
  ladder TERMs the launcher unconditionally (stop-ensemble.sh:783-786). State the D1 dependency or move
  the change into D2's scope. The predicate must also be stated at the LAUNCHER granularity (the launcher
  forwards every signal, launcher.sh:3683-3690) so §12 cell 3 is testable.
- Boot-grace window is silently lost on the supervised path: `_boot_grace_start` has one caller
  (lib.sh:5696, inside `restart_via_launcher`); D2 replaces that hand-back, so the 60 s transient-silencing
  window is never armed. §5.1 step 6 should say whether it is re-armed, and reconcile the proposed
  "2 x ~5 s" verify-read retry with `gate_version`'s one-shot `_probe_once` (lib.sh:6502) and the 180 s /
  120 s livez/readyz budgets.
- Census seam is not named: stop-ensemble.sh exposes a DRY_RUN LOG-TEXT plan (:96, :114, :563-566), not a
  machine-readable census — name the seam (DRY_RUN parse vs a new D1 interface) and its owner.
- §8's truncated-drain claim is stronger than the repo's own measurement ("voided on EVERY supervised
  promote"): docs/deployment.md:383/:387 record a 1-2 s normal drain. The accurate statement is "a drain
  slower than the cap is SIGKILLed (voided checkpoint)". The §15.1 `ExitTimeOut >= 70` raise is still right.
- §15.1's "re-stage" step is ambiguous in a dangerous direction: the plist recipe lives inside the `install`
  target (Makefile:371-372), so "re-stage -> make plist-install" reads as "re-run make install on the live
  install". The sufficient action is to edit the INSTALLED plist and reload, with the no-`.bak` caveat
  (docs/deployment.md:91). Also: "edit scripts/ensemble-prod.plist" is a repo edit, which contradicts
  §13's "no plist change in this PR / operator-side only".
- §15.2 does not say where the declaration must live: `lib.sh:5971` reads the declared mode from the
  PROCESS ENVIRONMENT only (no .env fallback for the mode, unlike ENSEMBLE_RESTART_UNIT via
  `_supervision_unit_from_dotenv`), so an operator who puts ENSEMBLE_SUPERVISION=script in an unsupervised
  host's .env still gets `auto` -> Darwin zero-match -> rc 78 -> that host's promote breaks. Pin the
  placement (and twin agreement).
- Missing from the trade-off matrix: a plist KeepAlive-semantics change (`SuccessfulExit=true`, so a
  graceful exit-143 stop does not re-arm) removes the need for the bootout barrier in the TERM path and
  would also address GAP-1 (docs/deployment.md:346-354). It is excluded only by an ADR scope constraint,
  not on merits — add it with its rationale.
- The §5.3 lock-free journal write assumes a serialization the repo does not provide: `journal_update` is
  an unsynchronized read-modify-write (only the final write is atomic), so the vehicle's step-2a close can
  clobber a concurrent daemon-side executor-history append. The "lock-free by construction" pin should
  assert the serialization assumption explicitly.
- Minor anchor drift: §9 L198 and §19 L321 cite lib.sh:6449 for the map call with the resolved mode; the
  map call is at lib.sh:6452 (:6449 is the `supervision-unit-unresolved` refusal). Semantics are right;
  the line is off.
- §3's invariant over-claims ("zero ensemble-prod processes exist") — contradicted by the doc's own
  census-fail row (L172: "Post-flip; straggler present"); the census at step 5 is authoritative.
- Test strategy is materially stronger than a text-ban suite (ordering pins on stub call-logs/exec traces,
  vehicle enumeration + order + three-way branch + end-state + lock-free + launcher-sweep cells, twins
  byte-identity, Darwin e2e merge gate). Two additions: a cell asserting the FOURTH 8b branch is DISARMed,
  and a cell asserting the comp3 precondition as code (a fresh-heartbeat stale txn is left OPEN by
  `_journal_sweep`).

### Approver direct verification (read-only, evidence for the merge)

- launcher.sh:3319-3324 — comp3 requires `hb_stale=1 AND owner_dead=1`; else "leaving alone" -> return 0.
  launcher.sh:97 `HEARTBEAT_STALE_S=300`; launcher.sh:78 `SWEEP_STALE_S=600`. -> confirms #1.
- lib.sh:6046-6049 — non-Linux -> return 0 (SCRIPT_NOHUP, rc 0), no detection. -> confirms #4.
- lib.sh:5978-5985 — `0|false|no|off` and `script` arms return rc 0 before any detection. -> confirms #5.
- promote.sh:794-808 — PREV-empty HALT-FOR-HUMAN branch exists, gate precedes the stop (:845).
  Doc L129-132 names only three branches. -> confirms #2.
- promote.sh:848-851 — the repo's own 8b does `launcher_swap "$PREV"` then `atomic_flip "$PREV"`; doc
  §5.3 step 1 L112 names neither. -> confirms #3.
- Doc L121 / L114 / §7 L179 comp3 claim; §7 L164 Darwin-auto refusal row; §5.2 L97 blesses `script` on
  Darwin; §7 L163-167 has no row for declared=script-with-a-launchd-job; §16 L285 checklist (R9 covers
  the declared `launchd)` arm + Darwin garbage fail-closed only). -> confirms #4, #5, #1.
- Both workers independently verified ~40 operational anchors as exact, and both reproduced the
  exit-site census criterion exactly (`grep -c 'exit 78' scripts/upgrade/promote.sh` = 36,
  `grep -c 'exit 1\b'` = 13, 28 non-comment exit-78 sites) and the 19-cell twins tables
  (tests/unit/tools/test_supervision_python.py:188-209; tests/test_supervision_twins.sh:100-122).

### Verdict reasoning

REJECTED. The architecture is judged sound by both workers (M1 bootout barrier + M2 promote-starts-
nothing + census ownership + verify), so this is not an architectural rejection — but 5 blocking defects
remain on exactly the paths the requirement names ("no halt at verify, no competing launchers,
deterministic rollback on abort"): three end-state/rollback derivations that contradict the repo
(#1 comp3 + unnamed silent-boot state; #2 the fourth 8b branch leaves the supervisor armed on a broken
binary; #3 the abort path skews launcher vs binary) and two Darwin detection short-circuits that make
the design's own refusal set unreachable on the operator's supervised host (#4 auto, #5 script/opt-out)
— each of which reproduces the 2026-11-11 incident mechanism and reports it "conforming".

### Escalation

Iteration 003 is the 3rd rejection and the maximum. Status set to ESCALATED in `.agents/approver/active.md`.
The full history (001: 6 blocking; 002: 4 blocking; 003: 5 blocking) is preserved above for the Leader.
Note: the artifact was substantially revised between cycles (rev 4 -> 5 -> 6, 260 -> 350 lines) and the
core architecture was judged sound in every cycle — the blocking set has shifted from architecture
(001) to abort-contract + detection predicates (002) to end-state derivations + classifier short-circuits
(003). A 4th iteration is a Leader/operator decision, not an approver continuation.

### Worker sessions (reports received verbatim, in-session)

- approve-worker-plan-d2-rev6 2fa6a1bb-a5ca-4153-8186-21d06b06ee41 — plan-approval
- approve-worker-decision-d2-rev6 3b77c60a-06e4-4037-957d-a039c3291935 — decision-approval

---

## VERDICT: REJECTED — Iteration 004 (FINAL — leader-authorized; cycle closes here)

Date: 2026-11-11 | Mode: Plan Approval (2 workers, one skill each; fan-in via todo_graph)
Artifact: d2-supervisor-flip-design.md (rev 7, 314 lines)
Declared base: edbf3c92f — VERIFIED BY THE APPROVER directly (HEAD = latest = origin/latest =
edbf3c92f622b87f0c7758d10a724d00c68d8219; doc 314 lines; tree dirt = concurrent lanes + artifacts,
not this review).
Authorization: the leader (mission owner) explicitly authorized one final iteration after the
iteration-003 escalation. Recorded as iteration 004; NOT a continuation of the 3-iteration cap.

Both workers REJECTED independently (plan-approval: 7 blocking; decision-approval: 4 blocking,
one of which that worker itself also classified as a Note).
Aggregation = dedupe-merge only, with ONE permitted downgrade (B-B2 -> Note, reason stated):
11 raw -> 10 blocking.

BOTH WORKERS AGAIN JUDGE THE CORE ARCHITECTURE SOUND (4th consecutive cycle): the M1 bootout
barrier, M2 supervisor-owned start, the dual-fire/census ownership gate, the host-blind outcome
map, the availability-regression-by-design posture, and the B1/B2/B3 repo corrections. Both
workers independently reproduced ~40 anchors exactly and the 36/13/28 exit-code census exactly.
The rejection is again NOT architectural: it is (a) forward-path precision (rollback target +
missing launcher swap), (b) vehicle branch completeness, (c) the new Darwin vocabulary/delivery
surface (unsupervised value + outcome surface + declaration transport), (d) twin parity and
suite amendments, (e) one unobservable mitigation.

### Blocking issues (merged, 10)

1. [A-1] The terminal-close serialization rationale inverts the comment it cites, and the
   "lock-free by construction" pin encodes a hazard as an acceptance criterion.
   (§5.3 step 2a L95; §5.3 L89; §11 L195; §12 pin 2 L201.)
   EXPECTED: a rationale that matches the repo's actual concurrency discipline.
   FOUND: `lib.sh:1740-1741` says "ONE read (the journal_read above) — a second read could
   straddle THE WRITER'S atomic replace (D1-16)" — i.e. read-consistency against a torn/drifting
   snapshot, NOT cross-process serialization. The repo serializes journal MUTATIONS on the D5
   lock (`launcher.sh:3329-3333` "All mutations below serialize on the D5 lock" +
   `_js_lock_acquire` :3331), while `journal_write` (`lib.sh:1714-1729`) is an atomic temp+mv with
   no lock. Atomic replace ≠ serialization: a lockless `journal_close_txn` / `atomic_flip` /
   `launcher_swap` can clobber a concurrent writer (lost update) or repoint `current` under
   another actor. The plan concludes "inherits that atomic-replace serialization without needing
   the pipeline lock" — a non-sequitur — and §12 pin 2 ("lock-free by construction") makes the
   hazard an acceptance criterion.
   FIX: either acquire the pipeline lock in the vehicle around the mutations (and release), or
   state the exposure explicitly and specify the mitigation (re-read + compare txn identity
   before closing; refuse to close a changed txn). Redefine the pin as "the vehicle does not
   LEAVE the lock held", not "no lock acquisition".

2. [A-2] The forward choreography omits the release launcher swap, the exact skew the plan's own
   B3 forbids in the reverse direction.
   (§5.1 step 4 L71; §12 pin 1 L200; §0 B3 L20; contrast §5.3 step 1 L93 and §5.4 step 3 L116.)
   EXPECTED: the forward path names `launcher_swap "$VERSION"` in the stopped window before the
   flip, and the ordering pin covers it.
   FOUND: `$VERSION`'s swap appears nowhere in §5.1 (Preflight → BOOTOUT → straggler stop →
   Flip → census+BOOTSTRAP → verify); `grep launcher_swap` finds it only in the two RESTORE paths.
   The repo does it at `promote.sh:570-576` (phase 3, stopped window) BEFORE `atomic_flip` (:597),
   and every release ships its own launcher (`stage.sh:425-426`). Implemented as written, launchd
   would exec the PREVIOUS release's root `launcher.sh` against the NEW `current` binary — the
   incident's own mismatch class. §12 pin 1 (`bootout < TERM < flip < bootstrap`) cannot catch it.
   FIX: state the swap in §5.1 (or state that promote.sh phase 3 is retained in the D2 flow) and
   extend the ordering pin to `bootout < TERM < launcher_swap($VERSION) < flip < bootstrap`, with
   an assertion that root launcher == target release's launcher at bootstrap.

3. [A-3] The vehicle's rollback target `$LKG` is undefined — 7 uses, 0 definitions — and the repo
   carries an explicit warning against the most likely misreading.
   (§5.3 step 1 L93; §12 pins 2/3 L201-202; §7 L153.)
   EXPECTED: the rollback target defined in one sentence, with the repo's own derivation.
   FOUND: `$LKG` is never defined (not `journal.previous`, not `$OLD_CUR`, not a release dir).
   `promote.sh:585-593` captures `OLD_CUR="$CUR"` "UNCONDITIONALLY, before ANY branch that can
   read it", and `promote.sh:785-792` states the auto-rollback target is `$OLD_CUR`, NOT the
   journal's `previous` field, because "the bug class is 'reading the wrong field' —
   `journal.previous` is stale whenever the previous run halted AFTER the flip and BEFORE commit".
   An implementer choosing `journal.previous` reproduces that documented bug class on the
   design's most dangerous mutation.
   FIX: define `$LKG` = the version `current` pointed at before the flip (repo `$OLD_CUR`,
   `promote.sh:593`), hoisted before the window's first mutation; state explicitly it is NEVER
   `journal.previous`, citing `promote.sh:785-792`; pin the derivation, not only the end state.
   SEVERITY NOTE (band transparency): iteration 003 carried "name the LKG SOURCE" as a NON-BLOCKING
   related note. Rev 7's B3 fix PROMOTED `launcher_swap "$LKG"` into the normative sequence and
   into two pins while leaving the token undefined, and the fresh worker independently raised it
   as blocking on evidence the earlier note did not weigh (the repo's "wrong field" bug class).
   This is treated as a NEW defect instance introduced by the B3 fix, not a reclassification of
   the earlier note.

4. [A-4 + B-N2a] B5(i) inverts what a suite the plan lists as "must stay green" pins, and the
   plan's amendment list does not include it.
   (§6 B5(i) L124; §12 L216 "Must stay green"; contrast the C10/C10b amendments L214.)
   EXPECTED: every suite whose pins the behavior change breaks is named in the same-PR amendment
   list (as C10/C10b are).
   FOUND: `tests/test_supervision_classify.sh:621` opens `if [ "$HOST_HAS_SYSTEMD" = "1" ]` (the
   only if-block in :600-720) and inside it cell 6d at :675 asserts
   `assert_eq "6d Darwin: SCRIPT_NOHUP unchanged" "SCRIPT_NOHUP"`; cells 5 at :590-591 pin
   Darwin-auto `SCRIPT_NOHUP` / rc-auto=0. B5(i) requires Darwin AUTO to probe launchd and NEVER
   fall through to `SCRIPT_NOHUP` ⇒ these cells flip. The plan lists
   `test_supervision_classify.sh` under "Must stay green" with no amendment row.
   FIX: add a `test_supervision_classify.sh` amendment (cells 5 and 6d flip to the launchd-probe/
   refusal shape) alongside C10/C10b, and move the suite out of "Must stay green" into "amended".

5. [A-5 + B-N9 + B-N6] The classifier is implemented TWICE; the plan's B4/B5 change is shell-only,
   so the twins diverge on exactly the axis their guard covers.
   (§6 L124-138; §9 L177; §12 pins 8/9 L207-208; §14 L234 "Twins arm load-bearing".)
   EXPECTED: the Darwin detection change stated for BOTH twins (or an explicit scoped divergence
   with its consequence), including the new state constants and the "frozen vocabulary" scoping.
   FOUND: `daemon/tools/upgrade_journal.py:3790-3791` carries the identical short-circuit
   (`if sys.platform != "linux": return SupervisionDetection(SUPERVISION_STATE_SCRIPT)`) and
   `:3784-3787` the same garbage→script arm. `tests/test_supervision_twins.sh:14` is explicitly a
   classification-LADDER drift guard, so post-change the shell twin refuses on Darwin while the
   python twin still returns SCRIPT_NOHUP/script/"conforming" — the §2 root-cause symptom
   ("reported conforming") surviving on the daemon-side advisory surface. §9's helper list is
   lib.sh-only; the new state constants (`LAUNCHD_MANAGED`, `LAUNCHD_NOT_VERIFIED`) are not
   added to the twin (`upgrade_journal.py:3633-3646` region), and §9 calls the vocabulary at
   `lib.sh:5778-5781` / `:5790-5805` "frozen/untouched" while adding two declared values and two
   verified states.
   FIX: state the twin scope (require `upgrade_journal.py` detection arms + state constants +
   declared-value rows to change in lockstep, with a twins-agreement cell), or scope explicitly
   to the shell lane and state the divergence + consequence; scope "frozen" to the RESULT-line
   SHAPE only and list the rows to add.

6. [A-6] Vehicle step 0 ("validate-and-continue") has no event, no disposition and no exit code,
   and collides with both the one-event invariant and the §7 bootout-failure row.
   (§5.3 step 0 L92 vs step 5 L100; §5.3 scope L83; §7 L151; §12 pin 2 L201.)
   EXPECTED: every reachable branch has exactly one event + disposition + exit code, consistently
   across §5.3/§7/§12.
   FOUND: step 0 ("already loaded ∧ healthy ⇒ validate-and-continue (no double bootstrap)") has
   none of the three, while step 5 mandates "exactly one of the three events per run" and §12 pin
   2 demands it. It also does not say which steps "continue" skips. §7 row "Bootout fails / still
   loaded ⇒ rc 78 pre-mutation … No change" contradicts §5.3's scope rule ("every exit path inside
   the bootout → bootstrap window routes through the vehicle" — the vehicle's restored branch
   exits 1, not 78): either the site is in-window and must route through the vehicle, or step 0
   has no caller and is dead code.
   FIX: pick one and make §5.3/§7/§12 agree — either declare the bootout-failure site an explicit
   exception to the vehicle (delete/re-scope step 0; enumerate the exception in §12 pin 2), or
   route it through the vehicle (§7 row becomes a vehicle disposition; step 0 gets an explicit
   event + disposition + exit code). "validate-and-continue" should be replaced by an explicit
   statement of what continues and which steps are skipped.

7. [A-7] The B1 mitigation — the only barrier between a SIGKILLed promote and a silent boot of the
   flipped binary — is asserted but unobservable.
   (§5.3 case 2 L103; §14 L233; §16 L251 vs §12 L199-216.)
   EXPECTED: at least one pin per required behavior.
   FOUND: the mitigation is a launcher.sh BEHAVIOR CHANGE ("the launcher's reclaim must treat
   OWNER-DEAD as reclaimable even with a fresh heartbeat … with a bounded wait") plus a watchdog
   alert. §16 lists it as this pass's work and §14 names it as the residual mitigation, but §12's
   13 new pins contain no launcher-reclaim cell and no watchdog cell; "Must stay green:
   tests/test_launcher.sh" covers only EXISTING behavior.
   FIX: add (a) an owner-dead reclaim cell (fresh heartbeat + dead owner ⇒ reclaimed, bounded;
   negative: live owner + fresh heartbeat ⇒ left alone per `launcher.sh:2884-2896`) and (b) a
   watchdog-detector cell (C1).

8. [B-1 + B-N11] The new `unsupervised` declared value has no resolved mode/state, no
   declared×verified row and no map cell, so the literal reading faults the very lane §5.2 requires
   to stay byte-identical.
   (§6 B5(ii) L125; §5.2 L79; §7 L144/L146; §9(a) L182; §12 pin 5 L204; §20.5 L314.)
   EXPECTED: the new declared value's resolution stated, with its declared×verified row(s) in both
   twins' tables and (if a distinct mode) a map cell; `conforming` on the healthy unsupervised lane.
   FOUND: §6 never says what `unsupervised` resolves to; §9(a)'s enumerated cell list has no
   `unsupervised` row; the resolved-mode equivalence rule (`lib.sh:5802-5806`) demands row-for-row
   agreement that the doc's table (`lib.sh:5774-5805`, mirrored
   `upgrade_journal.py:3591-3612`, declared vocabulary "unit|script|auto") cannot satisfy. Under
   the literal reading ("a NEW value, not `script`") the map's default arm yields
   `fault|unknown declared mode or verified state — fail-closed` on the healthy unsupervised lane.
   §20.5's claim that "the semantics are fixed in §6" is false. Related: on non-Darwin hosts
   `unsupervised` is not a recognised ladder arm → warn-once garbage→script (spurious WARN).
   FIX: state the resolution (e.g. MODE=script / STATE=SCRIPT_NOHUP as a Darwin-permitted alias of
   the script lane, preserving byte-identity) plus the rows in both twins' tables; OR state a
   distinct mode and add the map cell. Make `unsupervised` a first-class, host-independent arm.

9. [B-3] The `outcome=fault` surface asserted for the Darwin refusal does not exist in the code
   shape the plan prescribes, and the twins-byte-identical pin for it is unsatisfiable.
   (§9(b) L182; §12 pin 8 L207.)
   EXPECTED: an observable fault-class outcome, twins-pinnable, at a named emission site.
   FOUND: `supervision_preflight` (`lib.sh:6440-6454`) calls `_refuse` INSIDE
   `if ! supervision_classify`; the map call (`:6452`) and the OUTCOME printf (`:6453`) are on the
   success path only. `_refuse` (`:7030-7049`) = WARN + `journal_history_append refusal` + exit 78.
   `journal_mark_supervision` is never reached (preflight `promote.sh:410` precedes
   `journal_open_txn` `:523`). So the only surfaces are rc 78, a stderr WARN, and a journal
   `refusal` event carrying `reason=<token>` — `ENSEMBLE_SUPERVISION_OUTCOME=` is never printed.
   The python twin (`upgrade_journal.py:3762-3783`) is advisory-only and structurally cannot
   refuse, so a "classifier-level Darwin fault row" cannot be byte-identical across twins unless
   it is a map row — which §9(b) forbids.
   FIX: name the emission site (print OUTCOME=fault before `_refuse` exits, or add an `outcome`
   field to the refusal journal event) and drop the twins-byte-identical clause for it; OR restate
   §9(b)/§12.8 to the three surfaces that exist, per the DUAL_FIGHT precedent (`lib.sh:6436-6438`).

10. [B-4] The "process environment only" declaration rule is incompatible with the repository's
    executor env allowlist, so the `unsupervised` acknowledgement cannot reach the classifier on
    the daemon-driven promote — the live path on the target Mac.
    (§6 L133; §15.2 L244; §17.1 L257.)
    EXPECTED: a delivery path for `ENSEMBLE_SUPERVISION` / `ENSEMBLE_LAUNCHD_LABEL` on the path the
    promote actually runs on.
    FOUND: `EXECUTOR_ENV_ALLOWLIST` (`upgrade_journal.py:2593-2609`) = PATH, HOME, INSTALL_DIR,
    PORT, POSTGRES_DB, TMPDIR, XDG_RUNTIME_DIR, DBUS_SESSION_BUS_ADDRESS,
    DBUS_SYSTEM_BUS_ADDRESS, ENSEMBLE_PROMOTE_WATCHDOG (+ `PG` prefix) — `ENSEMBLE_SUPERVISION` is
    absent; `manager.py:6407-6411` per-call extra_env sets INSTALL_DIR/PORT only; the launchd job's
    EnvironmentVariables carry PATH only (`ensemble-prod.plist:103-107`). The repo's established
    delivery path is the `.env` pin (`lib.sh:6005` `_supervision_unit_from_dotenv`), and the repo's
    own test states it plainly: "the executor's env is stripped by EXECUTOR_ENV_ALLOWLIST, .env pin
    is the only resolution path that fires from the promote executor"
    (`tests/test_supervision_stop_handback.sh:991-992`). §6 forbids that fallback. Consequence: an
    intentionally-unsupervised Darwin host hits the zero-match refusal with NO way to acknowledge —
    permanently un-promotable, while §5.2 claims the lane is preserved.
    FIX: specify the delivery path (allowlist entry and/or per-call-site extra_env merge and/or the
    plist EnvironmentVariables for the daemon-side twin), or scope the Darwin declarations to
    operator-invoked pipelines only; add the delivery decision to §17.

### Downgrade (permitted, reason stated)

- [B-2 -> Note] §6 B4 writes `SUPERVISION_STATE=LAUNCHD_MANAGED:<label>` while §9 requires the
  BARE state with the label in the unit slot. The same finding appears in the worker's blocking
  table (#2) AND in its Notes (#1), where that worker itself reasons: "§9 explicitly names the
  trap and §12.8 pins the correct invocation, so the plan as a whole does specify the right
  behavior; only the §6 shorthand is wrong." The plan therefore already fixes the semantics in two
  normative places; the defect is a one-line §6 wording error. DOWNGRADED to a Note (wording fix
  still required: `SUPERVISION_MODE=launchd`, `SUPERVISION_STATE=LAUNCHD_MANAGED` bare, label in
  the unit/label slot; `LAUNCHD_MANAGED:<label>` is the rendered RESULT-line form).

### Non-blocking notes (merged; both workers)

- [A] §7 bootstrap-fail row over-claims "absent": on a bootstrap failure the census passes, so
  `abort_supervisor_absent` is not producible (reachable: restored | unverified). §5.3 step 4 also
  calls `supervisor-unverified` "booted-but-unverified", false for a bootstrap that never
  succeeded — add a fourth disposition ("never booted") or reword both.
- [A] §5.4 step 2 stop-failure state "DARK + DISARMED + OPEN txn" presumes the bootout succeeded;
  if the bootout itself failed the job stays LOADED+ARMED on the broken binary. Qualify or require
  an explicit disarm attempt there (§12 cell 12 pins the unconditioned claim).
- [A] §12 launcher-sweep cell conflicts with step 2a: with the txn CLOSED post-vehicle, "survives a
  sweep against an OPEN+flipped txn (no repoint past the restored LKG)" is vacuous or asserts
  launcher behavior that does not exist. Restate as "post-vehicle the txn is CLOSED and a sweep is
  a no-op"; pin the open-txn case separately as the SIGKILL case.
- [A] Signal entry needs explicit reconciliation: the vehicle should RETURN a disposition (no
  `exit`); the caller chooses the exit code; `_signal_handler` (`lib.sh:2992-3012`) overrides to
  143/129/130 while still emitting exactly one abort event.
- [A] B1's "≤30 s old heartbeat" is not established for the D2 window (the repo's 30 s cadence
  belongs to gate_soak/stage loops, `lib.sh:2640-2648`); the load-bearing bound is "<
  HEARTBEAT_STALE_S (300 s) at the kill instant". Restate, and state any in-window heartbeat
  refresh placement relative to `promote.sh:522`/`:613`.
- [A] §12 pin 11 wording is ambiguous (which assertion fails/passes per cell) and reads as
  contradicting §5.1 step 3's "the census covers all four forms" — name the tier/assertion.
- [A] §5.1 step 3 does not say which entry point invokes the ownership ladder after bootout (the
  new launchd branch of `stop_via_stop_script` would re-bootout) — specify the entry and that no
  second supervisor-stop is issued.
- [A] §5.4's "named journal state" vs §13's "no new journal keys": say where
  `supervisor-disarmed-*` lives (history detail string / `_OUTCOME_LABELS` / new field).
- [A] §5.3's terminal close vs the repo's leave-OPEN doctrine: the amendment scopes only
  `promote.sh:63-66`, but the doctrine is also asserted in in-body comments (`:556-560`,
  `:578-582`, `:600-608`, `:627-635`) — state that the doctrine text itself is amended or name the
  vehicle as the sole exception.
- [B-N2b] C10b has no specified producing mechanism in the code path the test exercises (the cell
  hardcodes `SUPERVISION_MODE=unit; SUPERVISION_STATE=UNIT_MANAGED` and calls `restart_via_launcher`
  directly, bypassing the classifier): specify the Darwin hand-back refusal arm (e.g. in the
  `UNIT_MANAGED` degrade branch, `lib.sh:5557-5577`) or restate C10b as a classifier-level
  expectation — do not satisfy it by dropping the hand-back contract.
- [B-N3] "19 cells" is stale in §9 L138 and §12.8 (six launchd cells added), and §0's changelog
  says "Two cells added" while §9(a) enumerates six.
- [B-N4] Anchor precision: the machine-line print is `lib.sh:6021`; `:6022` is the
  `SUPERVISION_REFUSE_REASON` assignment.
- [B-N5] Rung D names no refusal token (rungs A/B/C do; §12.6 omits it too) and `_refuse`
  refuses loudly on an empty reason — name it; also pin the global/journal token PAIRING for
  rungs A and C (only B's pair is pinned).
- [B-N7] §15.1 has no sanctioned non-`make install` command for the plist re-stage (the
  substitution lives inside the forbidden `install` target, `Makefile:371-372`; `make plist-install`
  only copies an already-substituted file). Name the exact one-liner or add a `make plist-stage`;
  note `plist-install` overwrites the LaunchAgent with no `.bak` (`docs/deployment.md:91`).
- [B-N8] `ExitTimeOut` N ≥ 70 TIES launchd's job-level SIGKILL to the launcher's own escalation
  bound (`CHILD_STOP_WAIT_S=70`, `launcher.sh:60`); the repo's systemd analogue uses
  `TimeoutStopSec=90` for 20 s headroom (`docs/deployment.md:329`). Prefer N ≥ 90 (measured drain
  ~1-2 s, so 90 is conservative).
- [B-N10] Option-4's rejection is stated as "out of scope (plist change)" while §15.1 already
  commits to an operator plist edit; the load-bearing reason is inexpressibility (no
  SuccessExitStatus analog, GAP-1/GAP-2) — restate on that ground and keep it on the §17 ledger.
- [B-N12] §16's standing items (S2-S17, PN-1-PN-5, G1-G3, RC-1, C1) are referenced as if
  load-bearing but never defined in §1-§19 — inline at least S4/S5/S9/S14 for the implementer.
- [B-N13] The unit-incapable refusal text (`lib.sh:6447`) tells operators to "declare
  ENSEMBLE_SUPERVISION=script", which now refuses on Darwin — update the wording (fold into §20.6).

### Approver direct verification (read-only, evidence for the merge)

- `lib.sh:1740-1741` vs `launcher.sh:3329-3333` + `lib.sh:1714-1729` — D1-16 is read-consistency
  against a writer's atomic replace; journal MUTATIONS serialize on the D5 lock; `journal_write`
  takes no lock. -> confirms #1.
- `promote.sh:570-576` (`launcher_swap "$VERSION"` in the stopped window) and `:597`
  (`atomic_flip`) -> confirms #2 (forward swap exists in the repo, absent in §5.1/§12 pin 1).
- `promote.sh:585-593` (`OLD_CUR="$CUR"` unconditional at flip-prep) and `:785-792` (rollback
  target = `$OLD_CUR`, NOT `journal.previous`; "the bug class is reading the wrong field") ->
  confirms #3.
- `tests/test_supervision_classify.sh:621` fence + `:675` cell "6d Darwin: SCRIPT_NOHUP unchanged"
  (+ cells 5 at :590-591) with `test_supervision_classify.sh` on the "Must stay green" list ->
  confirms #4.
- `daemon/tools/upgrade_journal.py:3790-3791` (non-Linux -> SCRIPT_NOHUP) and `:3784-3787`
  (garbage -> script) + `lib.sh:5774-5805` (declared vocabulary "unit|script|auto";
  resolved-mode equivalence resolves only `auto` -> unit|script) -> confirms #5 and #8.
- `lib.sh:6440-6454` (`_refuse` inside the classify-failure branch; map call `:6452`; OUTCOME
  printf `:6453`) + `lib.sh:7030-7049` (`_refuse` = WARN + refusal journal + exit 78) +
  `promote.sh:410` vs `:523` (preflight precedes `journal_open_txn`) -> confirms #9.
- `upgrade_journal.py:2593-2609` (`EXECUTOR_ENV_ALLOWLIST`, no `ENSEMBLE_SUPERVISION`),
  `manager.py:6407-6411` (extra_env = INSTALL_DIR/PORT), `lib.sh:6005`
  (`_supervision_unit_from_dotenv`), `test_supervision_stop_handback.sh:991-992` ("the executor's
  env is stripped by EXECUTOR_ENV_ALLOWLIST, .env pin is the only resolution path") ->
  confirms #10.
- Doc text: §5.3 step 0 (L92) / step 5 (L100) / §7 L151 / §12 pin 2 (L201) -> confirms #6;
  §5.3 case 2 (L103) / §14 L233 / §16 L251 vs the 13 pins in §12 -> confirms #7; §9(a) cell list
  (L182) has no `unsupervised` row -> confirms #8.

### Verdict reasoning

REJECTED. For the fourth consecutive cycle both workers judge the CORE ARCHITECTURE SOUND (M1
bootout barrier, M2 supervisor-owned start, ownership census, host-blind map, verify spine), and
the repo corrections landed in rev 7 (B1 comp3 truth, B2 fourth-gate DISARM, B3 restore order) are
verified exact. But the design still cannot be implemented as written on the three things the
operator actually asked for:
- "deterministic rollback on abort" — the rollback target is undefined (#3) and the vehicle's
  serialization claim is inverted (#1);
- "no competing launchers" — the forward path omits the release launcher swap (#2) and the vehicle
  has an unnamed branch (#6);
- "no halt at verify / fully automatic next promote" — the new declaration cannot be delivered on
  the daemon-driven path (#10), the intended unsupervised lane would fault (#8), the refusal
  outcome surface does not exist (#9), and the twins would disagree (#5);
plus an unobservable mitigation for the one residual risk that can silently boot a broken binary
(#7) and a must-stay-green suite the change breaks (#4).

### Escalation / cycle closure

Iteration 004 was the leader-authorized final check; the 3-iteration cap was already exceeded at
iteration 003. Cycle CLOSES here. Status: ESCALATED (final) in `.agents/approver/active.md`.
Full history preserved: 001 (6 blocking) -> 002 (4) -> 003 (5, escalated) -> 004 (10).
Trajectory note for the Leader: the blocking set has moved from architecture (001) to abort
contract/detection predicates (002) to end-state derivations/short-circuits (003) to forward-path
precision + Darwin vocabulary/transport (004). The remaining set is mechanical but not cosmetic:
#2/#3 are safety-ordering, #8/#9/#10 are the difference between "declared conforming" and
"actually conforming" on Darwin. A further revision needs no new architecture — it needs the ten
fixes above and, for #7/#10, decisions the doc currently leaves to the implementer.

### Worker sessions (reports received verbatim, in-session)

- approve-worker-plan-d2-rev7 68344bc9-082e-4efb-9ab1-58fe1f193075 — plan-approval — REJECTED (7 blocking)
- approve-worker-decision-d2-rev7 8209d4d1-eb8c-4637-9592-580dca9c25c0 — decision-approval — REJECTED (4 blocking, 1 self-downgraded)
