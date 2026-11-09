# Approver Tracking — decision-module (thin client + seam registry)

Slug: decision-module
Artifact: /home/nea/ensemble-src-wt-decision-module-design/.agents/shared/planning/decision-module/
  (5 docs, 1223 lines: scope-and-doctrine.md, thin-client.md, seam-registry.md, controls-and-phasing.md, architecture-recommendation.md)
Base: branch docs/decision-module @ 5da1021f2 (set UNCOMMITTED)
Mode: Plan Approval — large design set, 3 workers partitioned by verification axis, fan-in via todo_graph

---

## Iteration 001 — 2026-11-09 — VERDICT: REJECTED (13 blocking, 14+ notes)

Workers (all `plan-approval`, fresh cold context, read-only):
- W1 3cd907d7-8cd4-4bb7-aad8-75610141e6fb — proxy contract + thin-client faithfulness — REJECTED, 2 blocking
- W2 bc37a7da-d6ca-43f5-8ee8-fa3df608e4c9 — ensemble-claim faithfulness — REJECTED, 4 blocking
- W3 e741ffcc-3a0f-49b8-a98b-77076fae2d09 — design mechanics / safety / completeness / consistency — REJECTED, 7 blocking

BLOCKING (aggregated, renumbered by theme; worker provenance retained):
B1  (W2) scope-and-doctrine.md §S.2 S4 + §D4; seam-registry.md §5.4 + §2.2; thin-client.md §8.4 — "DENY minted ONLY at attestation_gate.py:950" is false; :950 is the report_obligation_read_error variant, main path is :1024 (code comment "(5) deny"), step 4 at :1016. Load-bearing as the structural non-denial proof.
B2  (W2) scope-and-doctrine.md §D5.2 item 4 — "Observed drift at 5da1021f2" list materially incomplete: EX-1 (real wiring graph.py:10568/10570, not :8665-8667), EX-2 (all three anchors point at unrelated code), EX-5 (rungs at graph.py:4558/4577/2448) all stale and undisclosed; §S.3 carries no EX-anchor corrections.
B3  (W2) scope-and-doctrine.md §S.1 census table — cites daemon/tools/upgrade_journal_sweep.py (does not exist; real daemon/services/upgrade_journal_sweep.py:981/:1037) and R9 verdict at snapshot_tools.py:365-381 (real :328, set-ops at :324-325).
B4  (W2) scope-and-doctrine.md §S.1 — judge call site given as graph.py:6317 (import line) vs :6326 (call) in all four other docs.
B5  (W3) controls-and-phasing.md §3.1.1 :118,:120 — kill-switch write channel set_instance_tunable is per-instance (not daemon-global), agent-callable via the "instance" tool category, feature-gated behind long_tool_nudge.enabled, and int-typed in [60,1800]; "operator only" has no named mechanism; daemon/services/settings_service.py (MANAGED_KEYS, settings:write scope) not named.
B6  (W3) controls-and-phasing.md:166 vs seam-registry.md:246 + thin-client.md:217 + scope-and-doctrine.md:165 — S3 hook placement stated two incompatible ways; the coverage matrix conflates the two anchors the set itself forbids conflating, so §3.2.2's composition rule does not gate S3.
B7  (W3) controls-and-phasing.md:106-110 — per-seam default OFF never specified (commission scope item 2; source doctrine docs/jev-system-one-integration-discussion.md:95 says "default off"; zero hits for "default off" in the set; the existing gate S3 would ride is default ON).
B8  (W3) seam-registry.md:156,:175,:262; thin-client.md:224-226 — confidence floor never specified (no value, source field, below-floor behaviour, or per-seam table); for the two predicate seams (S3, S4) the named field `confidence` does not exist on the wire (predicate carries probability only) — independently corroborated by W1 note N-8 (absent confidence: pass or fail, undefined).
B9  (W3) seam-registry.md:262,:266; thin-client.md:224,:226; controls-and-phasing.md:267 — D4's rescue-band bound has no runtime enforcement in C2: the overlay substitutes for judge_fused_bundle_async's verdict at the deny band (graph.py:6398-6400) and the judge is skipped (:6326), so a "complete" where the judge would have said "not complete" makes the gate LOOSER, undetectable at runtime; §4 P4 still sequences S4 into a shadow→deciding cycle.
B10 (W3) controls-and-phasing.md:36,:40,:42,:236 + thin-client.md:164 — two closed metric vocabularies collide with the 30-row failure table: rows 1-5 are pre-flight (never failures) and rows 4/5 reason values have no member in the 7-value suppressed vocabulary; the fallback==failures identity does not hold in C1 and the fallback family carries no mode label.
B11 (W3) controls-and-phasing.md:66-90 — scope item 4 only partly specified: shadow log has no named storage substrate, no retention mechanism, no access control; "audit" occurs zero times in the five files → no decision-audit trail.
B12 (W1) scope-and-doctrine.md §PX-3 :220; thin-client.md §2 :29-30, §6 rows 25/26/28, §8.2 — the normative response-contract digest omits the wire answer's `type` (discriminator) and `name` (join key): pkg/decision/types.go:167-193, MarshalJSON :202-231. Rows 25/26/28 are unimplementable without `name`; missing/unmatched name must be a content failure → fail open; the System One path 502s on an empty question name and §8.2 never states S1's question name.
B13 (W1) thin-client.md §6 row 18 :148 vs §2.1 :35-44 and §PX-8 :225 — a 502 subclass identified by body message text, contradicting the set's own status-only rule; the proxy emits 502 for >=8 distinct causes, so the declared label http_502_no_adapter is unproducible.

CROSS-WORKER CONFLICT RESOLVED: W3 re-verified attestation_gate.py:950 and credited the deny-ownership anchor as correct; W2 proved the "only" false by locating a second mint site (:1024). Carried as B1 on W2's more specific and materially safer variant (both mint sites sit inside decide(), so the conclusion survives while the anchor does not). W3's partial credit superseded.

NOTES carried (non-blocking, worker-verified): W3 — DECISION_ENABLED vs "Enabled deliberately absent"; canonical composition rule omits the global master and stage key; stage→C1/C2 mapping unstated; shadow-writer row contradicts the canonical kill statement; S1 pilot rationale asserts the wrong fact about the per-agent gate; C1 path claim re-introduces a path-level claim the canonical wording avoids; decision_overlay_shadow_write_failures_total outside the family shape; "every switch named anywhere" over-broad; out-of-set "decisions.md D6" reference unpathed; D4-vs-D5 reconciliation for S4 implicit only; source observation graph.py:4487-4488 stale in-source pointer (not a set defect). W2 — graph.py:4372-4378 is a comment not telemetry; attestation_required is the 11th field, not positionally 17th; gate-exception span :6170-6185; deny band at :6390; _parse_selection def at :786; P2/P3 guard header :6705; test class is TestMonitoringOnlyPin not MonitoringOnlyPinTest; "structurally forbids" is an AST-walk test pin. W1 — "empty input" is 502 for omitted but NOT for empty string on the System One path; top-K completeness is an upstream property, not a proxy guarantee; safety_identifier and image input parts are 400-rejected on the System One path; v187 delta is larger than "only a provider"; IP-3's POST citation is :802 not :780; §4 mislabels 409/422 (route emits 400); 4xx envelope is not uniformly server_error (401 is authentication_error, 405 is plain text); absent confidence undefined; levels[] label required but undocumented; token allow-list 403 path omitted from §7; rows 21-27 defensive-only; running-proxy build 185 unverified; token store config unverified; upstream distribution completeness unverified.

POSITIVE (worker-attested): doctrine quotes verbatim-accurate vs docs/jev-system-one-integration-discussion.md §2/§3/§5; identifier spaces gap-free (D1-D5, EX-1..EX-5, PX-1..PX-9, IP-1..IP-6, RA-1..RA-8, O1-O3, T1-T6, P1-P4, C0-C2); file manifest matches directory listing exactly; C0/C1/C2 framing strong; kill-target retention requirement met and character-identical in all five docs (6 occurrences); registry-owns-the-call architecture correct for D2; 30-row failure table every row terminates at the heuristic result; all 26 published §S.3 correction rows real; daemon-inertness confirmed by grep (zero DECISION_* consumers, zero jev/systemone/typesafe/openrouter hits, DecisionEngine zero hits); ~125 anchors confirmed correct; deploy-order non-dependence structurally proven (merge-base ancestry + byte-identical client contract across 185→187).

STATUS: IN_PROGRESS — awaiting iteration 002.

---

---

## Iteration 002 — 2026-11-09 — REPAIR VERIFICATION — VERDICT: REJECTED (13 distinct blocking, 24 raw findings across 3 workers)

Artifact re-measured: 5 files, 1,348 lines (206/283/297/247/315), HEAD unmoved at 5da1021f2, no commits, only the untracked planning dir dirty. PASS-3 log: architecture-recommendation.md §9.

Workers (all plan-approval, cold context, read-only, repair claims dispatched as ASSERTIONS to label HOLDS/PARTIALLY HOLDS/DOES NOT HOLD/REGRESSION):
- R1 13eb67f2-62ad-4a85-929c-cb99aebc04a4 — proxy/wire/client — REJECT, 5 raw
- R2 1384d731-5700-4325-9e70-34a03e975b3d — ensemble anchors + register — REJECT, 11 raw
- R3 89471236-7d60-4765-b4a8-dbab29bcc61f — mechanics/controls — REJECT, 8 raw

REPAIR SCOREBOARD against the 13 iteration-001 blockers:
- B1 deny-mint anchor — PARTIALLY FIXED (register + 3 docs corrected; 3 prose sites still carry "only :950")
- B2 EX-anchor drift — PARTIALLY FIXED (EX-1/2/5 true anchors published; EX-3/EX-4 cells publish NO anchor yet claim COMPLETE)
- B3 census table paths — FIXED (exact)
- B4 judge call site — FIXED (exact; independently pinned by tests/unit/test_attestation_stage3_census.py:97)
- B5 kill switch write channel — FIXED (settings_service.py:200/:54, routers/settings.py:946/:969/:1023 all exact; operator-only genuinely enforced; value types fit; live read has house precedent; set_instance_tunable explicitly not the channel)
- B6 S3 placement — FIXED (one placement, identical wording in all five docs; no contradicting prose)
- B7 per-seam default OFF — FIXED (seam-registry.md §3.4 complete; doctrine quoted correctly; default stated in exactly one place)
- B8 confidence floor — FIXED (per-seam table with wire field, floors 0.5/0.9/0.9, absent => fail open; wire-implementability re-derived from proxy 9cf17bb)
- B9 D4 additive form — PARTIALLY FIXED (ruling + rationale + shadow-only fallback stated; 2 sites still specify the substituting form; §2.2 not updated)
- B10 observability vocabularies — PARTIALLY FIXED (disjointness holds, mode-scoped identity holds, liveness observable present and discriminating; counts wrong at 4 sites; below-floor outcome unlabelled)
- B11 shadow log substrate/audit — PARTIALLY FIXED (substrate/retention/audit named; the access-control claim is not implementable on the named substrate)
- B12 wire answer type/name — FIXED in substance, but the repair INTRODUCED a new false anchor (empty-question-name rejection)
- B13 row-18 fold — FIXED mechanically (single http_502 label; zero http_502_no_adapter hits; table contiguous 29 rows), residue blocking
=> 6 fully addressed (B3,B4,B5,B6,B7,B8); 7 partially addressed with the remainder still blocking (B1,B2,B9,B10,B11,B12-residue,B13-residue).

FINAL BLOCKERS (deduped; provenance retained):
K1  (R2-B7 + R3-BL-1) S4 shadow phase not measurable: judge_plan_on requires mode==enforce (graph.py:6281); in dry the fused_judge_on block never executes => 0 consults => no agreement rate.
K2  (R2-B2/B3/B4/B5/B6/B9/B10/B11 + R3-BL-4) SYSTEMIC: register corrections published but never propagated to body prose — 8 residue sites.
K3  (R2-B1 + R3-BL-8) §S.3 row 37 publishes a WRONG correction (deny band :6390 is the enforce-mode guard; the band test is :6391); propagated to 2 live sites.
K4  (R2-B8) §D5.2's "COMPLETE" drift table publishes no true anchor for EX-3/EX-4.
K5  (R1-B3) THE LEADER'S PROVISIONING-TIME RULING HAS NO IMPLEMENTABLE MECHANISM at build 185.
K6  (R1-B2) §PX-8 retains the message-text-keyed 502 clause the fold was meant to delete.
K7  (R1-B1) the empty-question-name anchor (validators.go:101-108) is the duplicate-name check which SKIPS empty names; real rejection typesafe.go:162-165 -> 502 via handler.go:366-369.
K8  (R1-B5) the below-floor outcome has no label in either closed vocabulary => breaks fallbacks_total == failures_total on the C2 main branch.
K9  (R3-BL-2) the substituting form survives at seam-registry.md:268 and thin-client.md:244.
K10 (R3-BL-3) seam-registry.md §2.2 still claims type-level impossibility and permits a "subset".
K11 (R3-BL-5) §2.8's access-control claim is not implementable on the named substrate (the daemon log file IS agent-readable via the system-log tool category).
K12 (R1-B4 + R3-BL-6) row-count / vocabulary-count contradictions at 4 sites (controls:80 "30 classes", seam-registry:81 "row 30", arch:140 "30 rows", arch:132 "row 20"); truth = 29 rows / 24 failure rows / 18 distinct failure labels / 9 suppressed labels.
K13 (R3-BL-7) dangling controls-and-phasing.md §3.6 pointers (arch:60, controls:133) while the set's own dangling-pointer check reports clean.

C-CHECK RULINGS: C1 DOES NOT HOLD · C2 PARTIALLY HOLDS (P4 bullets present and semantically intact; the 50-string audit is not an artifact so its expected strings are not greppable) · C3 HOLDS · C4 DOES NOT HOLD (MEASURABLE ONLY IF mode=enforce) · C5 DOES NOT HOLD · C6 APPROVE (no numeric budget exists in the artifact; measured 206/283/297/247/315 matches the claim; controls is table-dominated) · C7 HOLDS.

ADJUDICATIONS — ARCHITECT CORRECT ON BOTH:
(a) snapshot_search_service.py:784=@staticmethod, :785=def _parse_selection, :786=docstring; body ends :812. The iteration-001 note (decorator :785 / def :786) was OFF BY ONE — WITHDRAWN.
(b) CANONICAL_LOG_SCHEMA_FIELDS holds 17 entries; attestation_required is the 11th (:1250). "17" is the tuple LENGTH, not the field's position. FR-10's own text ("positioned AFTER live_descendants") supports 11th.

STATUS: IN_PROGRESS — awaiting iteration 003 (final; a 3rd rejection escalates to Leader).

---

---

## Iteration 003 — 2026-11-09 — FINAL REPAIR VERIFICATION — VERDICT: REJECTED (4 blocking) — **ESCALATED**

Artifact: 6 files (5 design + anchor-audit.md), 1605 wc -l total; HEAD 5da1021f2; no commits; only the untracked planning dir dirty. Logical-line counts: arch 110 / scope 283 / seam 299(+0 nl) / controls 300 (wc 299, no trailing nl) / thin 246 (wc 245, no trailing nl) / anchor-audit 369. Budget rule "DESIGN <= 300 each, artefact exempt" HOLDS under the logical-line convention.

Workers (plan-approval, cold context, read-only):
- S1 0ab1ab4f-29f0-40a9-bd3b-7171a5e3e85a — K1 + K5 / V1 — REJECT (1 blocking). K1 HOLDS. K5 PARTIALLY HOLDS.
- S2 65ec53cd-a464-4e88-81d6-92558e12c2c8 — register + propagation + artefact / V5,V6 — REJECTED (12 raw blocking, mostly artefact).
- S3 ae1d3728-5e8b-4515-83e0-5746e5c9a8f4 — vocab/mechanics/regression / V2,V3,V4,V7 — APPROVED (0 blocking).

CONFLICT RESOLVED (S2 vs S3 on anchor-audit.md): S2 NOT FIT (6 rows mis-cited with quoted line content + §10-vs-§5 tally contradiction + a false verification output) vs S3 FIT WITH NOTES ("all 48 rows claim-level verified", no per-row detail). Kept S2: its findings are row-numbered with the actual cited line quoted, and row-level register re-derivation was S2's assigned axis while S3's brief covered V2/V3/V4/V7 (not the ledger). S3's blanket pass is not evidence on this axis.

FINAL BLOCKING (4):
B1 (S1) thin-client.md §6 row 17 still asserts the DELETED boot-probe ("the client verifies at boot ... kind=decision row with a resolvable credential and a registered adapter ... NOT_ENABLED/provisioning_missing") while §7 of the same file states "THE DAEMON DOES NOT PROBE". anchor-audit.md B13 also records the boot-probe as settled design.
B2 (S2) anchor-audit.md §5: six rows marked VERIFIED whose cited line does not support the claim — rows 6 (graph.py:4482 = _is_ghost_promise_message, claim = strict-JSON), 15 (snapshot_search_service.py:790, claim = S3/S4 metric families), 18 (graph.py:6326, claim = .env.example), 20 (long_tool_nudge.py:1614, claim = choice distribution), 23 (typesafe.go:704 = return out; construct column says :707, claim = synthesis :700-703), 46 (typesafe.go:1 = package decision, claim = 185->187 delta). Pattern = row/claim column misalignment. The CLAIMS verify true independently; their EVIDENCE does not.
B3 (S2) anchor-audit.md §10 vs §5: §10 records "21 rows whose construct could not be found at claim level are published as UNVERIFIED"; §5 records "48 VERIFIED / 0 UNVERIFIED / 0 CONTRADICTED"; no historical/superseded marker at the point of reading. Operative tally unresolved.
B4 (S2) anchor-audit.md §2 row A9 reports a grep result that returns 0 hits when re-run — a false verification output (the underlying fix, catch-all = row 19, is correct).

DOWNGRADED WITH REASON (dispatcher judgment band):
- S2 #8 (thin-client L1 site absent) -> NOTE: the iteration-002 defect was a FALSE claim at thin-client:245; the claim is gone and no "only :950" form survives in live design claims (two workers swept). Removal closes the defect; re-adding the statement is content sufficiency, not correctness.
- S2 #9/#10/#11 (seam:280, scope:245, arch:121/:186) -> NOTES: the docs were restructured this pass and the change logs moved to anchor-audit.md, so the site references in the brief predate the restructure. Stale brief line numbers are not set defects; S2's own report confirms the content levels elsewhere (K2 L2/L3/L4/L6 verified).

VERIFIED CLEAN (independent, from source): K1 enforce-mode shadow (judge_plan_on :6281 -> fused_judge_on :6287-6297 -> judge :6326 -> consult after judge only in enforce; :6231-6232 "dry = 0 LLM, node skipped"); K3 (:6390 enforce guard vs :6391 band test); K4 (EX-3 :388-458, EX-4 :612-674); K7 (typesafe.go:162-165 -> handler.go:366-369, System One only); K9 (judge first, consult after, never skipped); K10 (add-only/superset-only, subset forbidden, type-impossibility gone from live prose); K11 (log file agent-readable, write-time policy is the control); K12/K13; R-A (30 rows / 5 pre-flight using 3 of 9 / 25 values / 19 distinct incl seam_fault+http_other); R-B (0 additive forms; floor_rejections_total = subset view); R-C (28 slots + literal event = 29 key=value; canonical tuple 17, attestation_required 11th); V7 (doctrine once, exclusion list anchor-doc-only, 7 traceability rows, 2 open questions with defaults, status headers, no implementation implied, exactly 6 files).

POSITIVE: K1 closed. The design content is substantively sound and anchor-verified by two independent workers. The remaining blockers are ONE live design sentence and the evidence artefact's own accounting.

STATUS: **ESCALATED** — max iterations reached (3). Escalated to the Emperor via the Leader.

---
