# DF-4 Architecture Addendum — AGENT-ORIGIN Provenance Gate Closure (architecture-gate review)

- **Date:** 2026-10-22 (authoring session of 2026-10-21/22)
- **Author:** Architect — aggregation of 3 parallel analyst reports (one skill each): `security-design` (inst `83848dc9`), `structural-design` (inst `2e5d48da`), `data-flow-design` (inst `85a45f5a`). Analysts were read-only; this addendum is the only file they produced output for.
- **Base:** `/home/nea/ensemble-src-wt-df4` @ `60d7daaf4338b8856c923bc2d7b4ba3a68f75308` (branch `fix/df4-provenance-gate`; HEAD + cleanliness verified by all three analysts; only untracked content = this planning dir).
- **Under review:** `plan-overview.md` + `implementation-plan.md` (483 lines). Not rewritten here; amendments are stated for the planner to apply.
- **Exhaustiveness basis (Q1):** `_user_origin_windows` has exactly ONE writer (`manager.py:4899`, inside `stamp_user_origin_window`) and `_inherited_provenance_windows` exactly ONE (`manager.py:4994`, inside `_inherit_provenance_window`). A user-origin window exists iff the dispatch funnel stamped it from a classifying source → the mint-path enumeration below is closed, not sampled. Supplemented by repo-wide literal grep and an AST census of all 45 `enqueue_message*` call sites in `daemon/` (0 omit `source=`).

---

## Q1 — Trust-chain soundness

`classify_user_origin` (`upgrade_journal.py:3376-3391`) arms ONLY on: exact `"api"` (`_USER_ORIGIN_EXACT`, `:3354`) OR a registry-resolved chat adapter (`source_type` ∈ {telegram, slack, discord, whatsapp}, `:3358-3362`). Reserved lanes fail closed (`:3395-3397` via `is_reserved_source`, `constants.py:980-1004`). Every remaining no-genuine-human-turn mint path at `60d7daaf4`:

| # | Path | Mint anchor | Class | Notes |
|---|------|-------------|-------|-------|
| 1–2 | Sweep fallback wakes (`source=""` → `or "api"`) | `upgrade_journal_sweep.py:1209`, `:1734` | **(a) closed by P1** | Only two `or "api"` sites in `daemon/`; only two `enqueue_message` calls in the sweep (`:1200`, `:1723`); no third arm; error paths (`:1215-1230`, `:1775`, `:1840-1852`) never construct a source; re-stamp `:1700-1709` is `if w.source:`-gated — never fabricates |
| 3 | REST `POST /jobs` body `source` (Pydantic default `"api"`) | `jobs_crud.py:597` → `job_processor.py:1330` (recovery/resume legs `:1004`/`:1096`) → funnel | **(b) filed `257488dc` + (c) consequence** | Post-P2 the forged DIRECT shape on the acting instance flips refused→admitted. Bounded by `jobs:write` scope (`jobs_crud.py:450`), acting-instance allow-list, named-target confirmation, D-16 budget (protected 1/24h, fleet 3/24h — `instance_ops.py:147/:149/:602-623`), two-tier audit, `ENSEMBLE_INSTANCE_OPS_ENABLED` kill switch |
| 4 | REST `POST /{id}/messages` hard-coded `"api"` | `messages.py:691` | genuine-by-design; same trust tier as #3 | Defect-B fix target. "Genuine" rests on the assumption that a `messages:write` token speaks for a human — that assumption IS the `257488dc` class |
| 5 | `api_resume_fallback` | `messages.py:427`; reserved exact-member `constants.py:960` | closed (pre-existing) | `api_answer_fallback` / `api_gate_supersede` likewise fail closed as unregistered (not `"api"`-exact) |
| 6 | Agent tool-lane enqueue rewrite | `job_queue.py:1318-1322` → `agent:{caller}` / `internal_agent:unknown` (server-side, unconditional) | closed (pre-existing MAJOR-1(a)) | Caller-supplied source cannot thread through; the tool-arg default `"api"` at `:1292` is IGNORED |
| 7 | R18 auto-dispatch fallback | `instance.py:973` | closed (pre-existing R18) | ⚠ reserved by **exact-value membership** (`constants.py:976`), NOT the `"system:"` prefix — P1's literal correctly uses the prefix family; two mechanisms, both sound |
| 8 | Defer-resend / replay / retry re-enqueue | **`job_queue_service.py:2608` hardcoded `"api"`**; propagators: `dead_letter_service.py:153/:290`, `job_queue_service.py:2816`, `job_feedback_observer.py:4862` (verbatim `JobItem.source`) | **(b)-inheritor + a same-class residual the plan does NOT disclose** | `resend_deferred_foreground` (`:2465`, route `jobs_management.py:1030`, scope `jobs:write`) mints a FRESH direct window at resend time — no concurrent human turn. Post-P2 this admits. → Amendment **A2** |
| 9 | External chat adapters | registry arm `:3399-3410` | genuine-by-design | Spoofing = channel-credential compromise (channel-auth domain, out of scope) |
| 10–11 | `"scheduler"`; `internal_report:`/`internal_error_report:` | reserved (`constants.py:972`, `:950-951`) | closed | |
| 12 | `instance_ops:{caller}` notify lane | `instance_ops.py:968` | 🟢 forward-compat note | NOT in `RESERVED_SOURCE_PREFIXES`; fails closed only as "unregistered". Register the family in a later lane (A13), not in DF-4 |

**The reframing finding:** the OLD gate never blocked the delegated forge. `_inherit_provenance_window` copies the root without inspecting the root's own provenance (`manager.py:4960-4984`), so a forged direct on a spawner propagated to an INHERITED window that `:369` **admitted** — the module docstring concedes this (`instance_ops.py:53-56`, D-18). The old gate's entire real guarantee was "no DIRECT window, genuine or forged, arms a protected act" — which is itself defect (B). Consequence for (c): Change 2 widens admission for every forged-DIRECT path whose window stays on the acting instance (#3, #4-non-genuine-holder, #8-resend) — REAL but bounded exposure; and it simultaneously closes the delegated exploit once P1 kills the sweep's root mint.

**VERDICT: Change 2 NEUTRALLY RE-SCOPES the gate — it strictly STRENGTHENS the delegated-forgery surface (the one the old gate actually admitted) in conjunction with P1, and strictly WEAKENS exactly one narrow shape (DIRECT on the acting instance, genuine operator AND REST-class forged) from refused to admitted, disclosed under Honesty ¶3 and bounded by scopes/allow-list/named-confirmation/D-16-budget/audit/kill-switch; Change 2 ALONE would be strictly weakening, hence P1→P2 is load-bearing (Q4).**

---

## Q2 — The requirement-3 interpretation

**(i) The leader's reading is SOUND.** Byte-level audit of the DF-4 change set (P1–P4) against all five frozen surfaces: `provenance.py` relocation — no move; alias block `upgrade_tools.py:1289-1296` — byte-unchanged (sanctioned comment fix is `:1275`, 14 lines above; verified six names bound vs "FIVE" stated); factor-2 `:3318-3366` — untouched; restart gate `:2720-2730` — untouched, and already keys on `provenance.ok` only (`:2725-2727`), so the plan's "predicate-identical" claim is exactly true; `stamp_user_origin_window` `manager.py:4850-4912` — untouched; classify arm set — untouched (the new literal is reserved by the prefix family, no arm-set edit needed). **Zero bytes of any frozen surface change.**

**(ii) No alternative implementable reading exists.** Under a strict file-level freeze, the defect sites themselves (`upgrade_journal_sweep.py:1209/:1734`) are upgrade-lane files — unfixable wherever the constant lives. The only theoretical escape (make `"api"` itself reserved) is closed: `classify` tests `_USER_ORIGIN_EXACT` at `:3391` BEFORE `is_reserved_source` at `:3395`, so `"api"` would still arm without editing the frozen arm set. Independent support: shared-KV `wt.claim.df4.surface` grants the commission exactly the seven daemon files (excluding only `scripts/upgrade/**`, the *release-artifact* provenance surface) — the freeze is per-surface, not per-file.

**Restatement to adopt verbatim:** *the freeze protects the five enumerated semantic surfaces; the DF-4 change set touches zero bytes of all five; the sweep file must change because the defect lives there.*

**(iii) Constant placement — `daemon/tools/upgrade_journal.py` beside `RESTART_WAKE_SOURCE` (`:1177`).** The sweep already imports from that module (`upgrade_journal_sweep.py:69-70`) — one more name, zero new import paths; it is the wake-source vocabulary module (owns `PendingWake.source` + `""` sentinel `:1127`, `RESTART_WAKE_SOURCE` `:1177`, `classify_user_origin` `:3376`); sibling identity is exact (a `system:` literal whose producer-consumer pair is journal→sweep); pin ergonomics match the house pattern (`test_agent_restart.py:228/:231`). `constants.py` REJECTED: it holds the families (the rule), not family-derived literals — `RESTART_WAKE_SOURCE` is itself NOT a frozenset member; parking a non-registered literal among registered ones misleads; it adds a second import edge and inverts the producer-owns-literal house pattern (`WATCHDOG_SOURCE`, `WEDGE_SOURCE`, `LONG_TOOL_NUDGE_SOURCE`, `REPORT_INTEGRITY_GUARD_NOTICE_SOURCE` all live in their producer modules).

**Carve-out (must be explicit):** the Execution Contract (`implementation-plan.md:17`) reads like a file ban on `provenance.py`; the freeze intent is *relocation/code*, and two docstring-only amendments there are required post-change (`provenance.py:49-54`, `:119` — see A4). Authorize docstring-only edits.

**VERDICT: Interpretation SOUND and uniquely implementable; freeze restated as per-surface; constant lives in `daemon/tools/upgrade_journal.py` beside `RESTART_WAKE_SOURCE` (`:1177`); one carve-out — authorize docstring-only amendments inside `provenance.py` (A4).**

---

## Q3 — The reserved literal's shape

**RECOMMEND: `SWEEP_NO_USER_ORIGIN_WAKE_SOURCE = "system:sweep_no_user_origin_wake"`** (the plan's candidate, confirmed on independent evidence). Traced: `is_reserved_source` → True via the `"system:"` prefix (`constants.py:948` → `:998-1000`, no frozenset entry needed); `classify_user_origin` → `(False, "reserved-internal")` (`upgrade_journal.py:3395-3397`) → funnel POP (`manager.py:4905-4906`) → `_inherit_provenance_window` keys only on `internal_agent:` delegation → no direct window AND no inherited window. Zero collisions repo-wide; existing `system:` family members enumerated and distinct. Reason-named (`no_user_origin`) beats producer-named for audit legibility: the literal appears only as the `else` of a truthiness test, so its presence in a delivery row IS the signal "no user origin at arm time". The `_WAKE_SOURCE` suffix mirrors `RESTART_WAKE_SOURCE`.

Source-shape consumers: a `system:` value trips exactly the two it should — `instance_messaging.py:396` (`_INTERNAL_STAMPED_SOURCE_PREFIXES` → attestation exclusion, `attestation_scanner.py:307-311` — wake turn does not re-arm the attestation window, identical to restart-kind today) and `:3490` (`is_system_origin` — see A5). No `internal_*` prefix-matching consumer matches (`report_injection/repository.py:2135` et al.).

**Correction to the commissioning brief:** `RESTART_WAKE_SOURCE` does **not** feed `_settle_restart_audit` (no reference; audit rows handled independently at `upgrade_journal.py:454+`, `upgrade_tools.py:2353/:2776/:2865`). Its only non-sweep consumer is the hard-coded `"wake_source"` field render at `upgrade_tools.py:2924`. No collision either way — the two literals are mutually exclusive arms of the same ternary.

**VERDICT: NAME `SWEEP_NO_USER_ORIGIN_WAKE_SOURCE`, VALUE `"system:sweep_no_user_origin_wake"`, declared module-level in `daemon/tools/upgrade_journal.py` beside `RESTART_WAKE_SOURCE` — prefix-family reserved, fail-closed, collision-free, audit-legible, no user-origin implication.**

---

## Q4 — Sequencing + partial-state safety

- **P1 alone (sweep reserved, gate class-keyed): SAFE and strictly improving.** The forge dies at mint (reserved → fail-closed → POP). Today's forged window was not inert — it could arm factor-2 of the live upgrade gate and was written durably as `original_source="api"` (`instance_messaging.py:3523-3529`); P1 stops both (verified via the `is_system_origin` lane `:3492`). Defect B remains open = status quo. No new exposure.
- **P2 alone (gate ok-keyed, sweep still `"api"`): CONFIRMED strictly worse than today.** Path-walk: sweep `"api"` → funnel stamps DIRECT on the woken instance → old gate `:369` refuses (`pclass="direct"`); new gate admits (`ok=True`) — while the delegated shape was already admitted pre-P2. Risk R1 is real; the hard order is load-bearing.
- **Stage-time:** no runtime intermediate exists (staged binary carries both). Import shape acyclic: P1 adds one name to the sweep's existing import (`upgrade_journal_sweep.py:69-70`); no reverse edge; `instance_ops.py:92` → `provenance.py:63` → `parse_iso_utc` only.
- **Commit-granularity advisories:** (1) pin (iii) must NOT land in the P1 commit (the P1-only intermediate keeps the existing suite green); (2) the routing-test amendment A1 MUST land in the P1 commit (it asserts P1's own behavior; the suite is red without it).

**VERDICT: Hard order P1→P2 VALIDATED (P2-alone = strictly weakening, confirmed by path-walk; P1-alone = strictly improving); no stage-time ordering hazard; two commit-placement advisories (A1 in P1, pin (iii) not in P1).**

---

## Q5 — The honesty boundary

- **Sweep class fully closed by P1: YES.** Exactly two `or "api"` sites in `daemon/`, exactly two sweep enqueue sites, no third arm, re-stamp cannot fabricate (Q1 #1–2). P1-AC-1/-7's repo-wide grep covers the class.
- **Does Change 2 make REST-ingress WORSE? Precisely yes — for the direct shape only.** `jobs_crud.py:597` → funnel stamps DIRECT on the job's instance → protected act → old `:369` REFUSED, new gate ADMITS. Adjudication: **disclosed residual, NOT a regression**, because the retired refusal was (1) explicit D-18 design intent ("SPEED BUMP, NOT A PROOF" / "refused ON PURPOSE", `instance_ops.py:49-56/:374-382`), (2) vacuous against the actual exploit — the delegated shape of the very same REST forge was ALREADY admitted pre-DF-4 (Q1 reframing) — and (3) simultaneously the cause of defect B. The plan's Honesty ¶3 discloses exactly this. The widening is bounded by the verified control stack (scopes, allow-list, named confirmation, D-16 budget, two-tier audit, kill switch).
- **Boundary gap:** the honesty clause names only `jobs_crud.py:597` + `messages.py:691`. The defer-resend lane (`job_queue_service.py:2608`) is a third mint surface of the same class (Q1 #8) and must join the disclosure (A2). Note the analysts' split on it (genuine-origin-content vs forge) resolves on mint-time semantics: the window is minted FRESH at resend with no concurrent human turn — same class as the sweep forge; disclose now, fix (carry `job.source`) in its own gated lane.
- **Honesty item 2 verified:** arm captures the live window's source at schedule time (`upgrade_tools.py:1570`); on live promote-kind the frozen factor-2 read precedes the arm, so the fallback never fires (anchor + pin level; a full control-flow walk of every promote-kind branch was not executed — residual noted below). Remains correctly open: non-live arms, the durable `original_source="api"` write (P1 stops it), the env-flip 60-min hazard (`NONCE_TTL_S` `upgrade_journal.py:140`; P1 closes it).

**VERDICT: Boundary correctly drawn and disclosure accurate — disclosed residual, not regression — CONDITIONAL on two amendments: add `job_queue_service.py:2608` to the disclosure (A2) and correct the now-false agent-visible doc surfaces in the P2 commit (A3); release notes must state plainly that the D-18 "direct refused on purpose" property is retired BY DESIGN with scopes/budget/audit as compensating controls (the honest answer to R10).**

---

## Q6 — Containment preservation

**Plan anchors are WRONG; containment itself is PRESERVED.** Reconciled truth at `60d7daaf4` (`daemon/tools/instance_ops.py`):

| Site | Kind | Carries the 4 provenance fields? |
|---|---|---|
| `:569` (kwargs `:577-580`) | refusal — protected gate | ✅ |
| `:629` (kwargs `:637-640`) | refusal — **budget** (not a commit row) | ✅ |
| `:669` | refusal — cascade/orphan | ❌ (never had them) |
| `:686-694` | `prov_fields` dict **assembly** (not a write) | source of the rows below |
| `:699`/`:707` | preview (dry-run) | ✅ |
| **`:746`/`:755`** | **COMMIT — `write_audit_and_act`** | ✅ |
| `:773`/`:780` | refusal — service said no | ✅ |
| `:724-727` | observability read | n/a |

`plan-overview.md:70`'s "`:637` and `:690` (commit)" is materially wrong (`:637` = budget refusal; `:690` = dict literal); `implementation-plan.md:83`'s `:688-694` is the assembly, not the commit write; **neither plan names `:746`**. A developer following the plan literally inspects a dict. → Amendment **A6**.

**No column orphaned.** `pclass` survives as a verdict field (`provenance.py:161/:195/:229/:271`); refuse path records `root_source=None`/`depth=None` exactly as pin (iii) asserts (unchanged — the class branch is downstream of the `ok` check); admit path still writes `provenance_class`/`provenance_depth`/`root_source`/`root_message_id` through `write_audit_and_act`/`write_preview`/`write_refusal` (`repositories/instance_ops/repository.py:88-142/:244-275/:284-298`); schema untouched (`models.py:154-169`; migration `20261021_120000`).

**Is `provenance_class` still meaningful with admitted `"direct"`? YES — it becomes MORE meaningful:** post-DF-4 it answers "which window shape authorized this act?" (minted-on-actor vs inherited-through-delegation) on one ledger — precisely Requirement 2's countability surface. Only its ROLE changes (gate input → observability). Docstring pointer cleanup at `models.py:78` (`(D-18)` → "OBSERVABILITY ONLY — not a gate input (D-18 superseded by DF-4)") → A4.

**VERDICT: Containment PRESERVED, no orphaned columns, `provenance_class` valid as pure observability — but the plan's audit anchors are wrong (commit write = `:746`; see table) and five agent-visible/doc sites that go FALSE post-change are missing from P2/P4 (A3/A4/A7).**

---

## Q7 — Missed-items sweep

Census results (all anchors re-derived): exactly two sweep delivery arms (`:1200-1211`, `:1723-1736`) and two `or "api"` sites; `PendingWake.source` has exactly one producer (`upgrade_tools.py:1570`); the coalesce path is kind-homogeneous (`:537-573`) and only ever re-stamps recorded sources; `_capture_user_source` sentinel consumers unchanged by the swap (arm-time read, not delivery); display arm set unchanged (`user_origin_sources_display` reads the chat-type registry + exact-set narrative, not the fallback); the "route through the leader" teaching lives entirely inside the retired branch P2 deletes (plan correct); kill-switch `ENSEMBLE_POST_RESTART_ARM_NOTIFY` orthogonal (gates arming, not source) — plan correct not to touch it; `api_resume_fallback`/`api_answer_fallback`/`api_gate_supersede` all fail closed.

**Findings the plan missed (amendment numbers in parens):**

1. **BUILD-BREAKING:** `tests/job_queue/test_post_restart_arm_notify_routing.py:203-221` `test_empty_source_uses_api_sentinel` asserts `source == "api"` — fails post-P1; not in any plan table (A1).
2. **Undisclosed same-class forge lane:** `job_queue_service.py:2608` (A2, see Q5).
3. **`MessageQueue.type` is INVARIANT** — `"api"` and `system:*` both take the `else` branch → HUMAN (`instance_messaging.py:2102-2103`); and `priority=2` at both sweep sites ⇒ `is_fresh_episode_user_message` stays False before AND after (`:2420-2423`). The commission's sharpest worry is **bounded** — but the plan is silent; record it so the reviewer does not re-derive (A10).
4. **`dispatch_source` branch flip (real behavioral change, precedented):** post-P1 the wake takes the `is_system_origin` lane (`instance_messaging.py:3488`) — `dispatch_source` resolves from `instance_metadata.original_source` instead of the literal `"api"`, and external dispatch may become REAL (today `"api"` has no `":"` → `dispatcher.py:693-695` skips = no-op; post-P1 it can resolve to e.g. `discord:user1`). Restart-kind already ships this flip in production — precedented, but promote-kind now inherits it: undisclosed, unpinned (A5).
5. **Shipped-code docstrings that lie post-P1:** `provenance.py:119` + `upgrade_tools.py:1262` ("falls back to `'api'`") — plan amends only the test docstring (A4).
6. **Stale house-note anchor + missing risk row:** the re-stamp/TTL-reset defect lives at `:1698-1709`, NOT `:1305-1317` (those lines are the grace-pass handler at `60d7daaf4`); the plan has no adjacent-not-in-scope row (A9).
7. **P4 doc list incomplete:** `nest-ops/implementation-plan.md` (`:643/:644/:660`) and `nest-ops/risk-register.md` (`:21-25/:63-67`) absent entirely; `decisions.md:657` and `:137`, `nest-ops/plan-overview.md:450/:645-649/:679/:687-692/:694-699/:701-705/:1084-1085/:1097`, and `nest-ops/architecture-recommendation.md:272` outside the cited ranges (A7); cite-only: `post-restart-arm-notify/phase2-plan.md:265` (historical), `agent-restart/architecture-recommendation.md:44` (stale producer census).
8. **Latent default-parameter surface:** all 45 `enqueue_message*` call sites pass `source=` explicitly (AST census, 0 omissions) — but `source: str = "api"` defaults (`manager.py:9442/:9505`, `instance_messaging.py:2523/:2699`) are one forgotten kwarg away from re-inverting the class; the R4 grep shape (`or "api"`) does not catch this (A12).

**VERDICT: The plan's two core changes are correct and well-evidenced, but Q7 finds 8 missed items — one build-breaking (A1), one undisclosed forge lane (A2), one undisclosed dispatch flip (A5) — plus documentation/anchor corrections. Plan NEEDS AMENDMENTS before implementation.**

---

## CONSOLIDATED RULING

| Q | Verdict | Plan amendment required |
|---|---------|------------------------|
| Q1 | Neutral re-scope: strengthens the (actually-admitted) delegated forge with P1; weakens one narrow shape (DIRECT on acting instance, genuine + REST-forged) — disclosed; Change 2 alone = strictly weakening | None to the mechanism; disclosure additions A2 |
| Q2 | Leader's interpretation SOUND and uniquely implementable; freeze is per-surface; constant → `upgrade_journal.py:1177` | Execution-contract carve-out (A4) |
| Q3 | `SWEEP_NO_USER_ORIGIN_WAKE_SOURCE = "system:sweep_no_user_origin_wake"` in `upgrade_journal.py` | None (plan's suggestion confirmed); note the `_settle_restart_audit` correction |
| Q4 | P1→P2 validated; P1-alone strictly improving; P2-alone strictly worse; no stage hazard | Commit-placement advisories (A1-in-P1, A11) |
| Q5 | Boundary correctly drawn; REST widening = disclosed residual, NOT regression | A2 (defer-resend disclosure), A3 (agent-visible surfaces), release-note statement |
| Q6 | Containment preserved, no orphaned columns; class MORE meaningful as observability | A6 (re-anchor), A4 (`models.py:78`), A7 (doc ranges) |
| Q7 | 8 missed items: 1 build-breaking, 1 undisclosed forge lane, 1 undisclosed dispatch flip, 5 doc/anchor | A1–A12 as itemized |

**Overall: APPROVE the architecture (P1 reserved-literal swap → P2 ok-keyed gate, hard-ordered) — sound by construction (class is a read-time derivation; the old gate never blocked the real exploit) — CONDITIONAL on the amendments below. The planner applies them; this addendum does not modify the plan.**

## AMENDMENTS (stated exactly; planner applies)

- **A1 (P1 commit, AMENDED table):** `tests/job_queue/test_post_restart_arm_notify_routing.py:203-221` — re-point `test_empty_source_uses_api_sentinel` to assert the reserved literal (not `"api"`), `is_reserved_source(lit) is True`, `classify_user_origin(lit)` not user-origin; reword docstrings `:13-18`, `:129-134`. Suite-green requires it in the P1 commit.
- **A2 (Honesty Clause):** add `daemon/services/job_queue_service.py:2608` (`resend_deferred_foreground` hardcodes `source="api"` on re-enqueue → fresh DIRECT window at resend time; post-P2 admits). Disclose; the fix (carry `job.source`) is a behavior change for its own gated lane.
- **A3 (P2 commit):** amend agent-visible surfaces that go FALSE post-P2 — `instance_ops.py:49-63` (D-18 paragraph; also stale `f4cf9c4f` "owns the root fix" citation), `:119-121` (`CATEGORY_DOC` — feeds tool registry `:1511/:1519`), `:810-815` (`instance_terminate._full_doc_`). Release notes: D-18 "direct refused on purpose" retired BY DESIGN; compensating controls = scopes/allow-list/named-confirmation/D-16-budget/audit/kill-switch.
- **A4 (Execution Contract + P4):** authorize docstring-only edits inside `provenance.py` (freeze = relocation/code, not file ban): `provenance.py:49-54` (class-keyed-gate claim now false), `:119` ("falls back to `'api'`" now false); plus `upgrade_tools.py:1262` comment; `repositories/instance_ops/models.py:78` (`(D-18)` → observability-only wording).
- **A5 (P3 + release notes):** disclose + pin the `is_system_origin` flip — post-P1 `dispatch_source` resolves from `original_source` metadata (`instance_messaging.py:3488-3540`); durable `original_source="api"` write stops (plan already claims this half); external dispatch may become real (`dispatcher.py:693-695`). Precedented by restart-kind; promote-kind now inherits.
- **A6 (re-anchor, both plan files):** audit containment truth = refusal `:569`(kwargs `:577-580`), budget refusal `:629`, cascade `:669`, assembly `:686-694`, preview `:699/:707`, **COMMIT `:746`/`:755`**, service-refusal `:773/:780`, read `:724-727`. P3 acceptance #8 re-anchored accordingly.
- **A7 (P4 file table):** add `nest-ops/implementation-plan.md:643/:644/:660`, `nest-ops/risk-register.md:21-25/:63-67`; widen to `decisions.md:657` (+`:137` index line), `nest-ops/plan-overview.md:450/:645-649/:679/:687-692/:694-699/:701-705/:1084-1085/:1097`, `nest-ops/architecture-recommendation.md:272`; cite-only notes for `post-restart-arm-notify/phase2-plan.md:265` and `agent-restart/architecture-recommendation.md:44`.
- **A8 (P3 e2e step 5):** additionally assert `stamp_user_origin_window` was NOT CALLED for the `""`-source wake (mirror `test_post_restart_arm_notify_routing.py:221`), not merely that no window exists.
- **A9 (risk register):** add adjacent-not-in-scope row — re-stamp TTL reset at `sweep:1698-1709` (pre-existing, untouched); correct the house critical note's stale `:1305-1317` anchor.
- **A10 (plan notes):** record the bounded findings — `MessageQueue.type` INVARIANT (`else` → HUMAN, `instance_messaging.py:2102-2103`); `priority=2` at both sweep sites ⇒ `is_fresh_episode_user_message` False before and after.
- **A11 (P1 commit discipline):** pin (iii) must NOT land in the P1 commit (P1-only intermediate stays suite-green).
- **A12 (later lane, optional now):** source-level pin that no `enqueue_message*` call site omits `source=` (AST census today = 0); consider flipping the `source: str = "api"` defaults (`manager.py:9442/:9505`, `instance_messaging.py:2523/:2699`) to a reserved literal per the `auto_dispatch_fallback` precedent.
- **A13 (later lane, low):** register the `instance_ops:` prefix in `RESERVED_SOURCE_PREFIXES` (currently fails closed only as "unregistered").

## Adjudication notes, decisions pending, unverified residuals

- **Divergence adjudicated (Q1 #8 / Q5):** security lens called `:2608` "genuine-by-design with a staleness caveat"; data-flow lens called it an undisclosed forge. Ruling: **disclose** — mint-time semantics (fresh window, no concurrent human turn) make it the sweep forge's class; content origin does not launder the mint.
- **Decisions pending (leader, non-blocking):** (1) whether to fix `job_queue_service.py:2608` now instead of disclosing — recommended NO (behavior change, own gate); (2) whether to schedule A12's default-flip lane.
- **Unverified residuals (carried honestly):** full control-flow walk that factor-2 precedes EVERY promote-kind arm branch (verified at anchor + pin level only — `test_t0a_upgrade_factor2_never_calls_restart_helper` is the standing guarantee); MISSED-5's dispatch-flip consequence derived statically (read-only lane), not executed against a live adapter; the "14-source" adapter census was not independently re-derived (confirmed only that the sweep never consumes adapter sources); ~59 peripheral `enqueue_message` sites covered by the single-writer argument rather than individual inspection.

---

## ERRATUM — 2026-10-22 (appended by the planner; APPEND-ONLY, no history rewrite)

This block is the **first and only** edit to this file. It is **appended** at the end; the architect's original Q1–Q7 text above is **quoted, not rewritten or deleted**. The same no-history-rewrite discipline that applies to the nest-ops docs applies here.

**Erratum 1 — Q3's "trips exactly the two it should" UNDERSTATES the blast radius.**

The architect wrote (Q3, line 54, quoted verbatim):

> "Source-shape consumers: a `system:` value trips exactly the two it should — `instance_messaging.py:396` (`_INTERNAL_STAMPED_SOURCE_PREFIXES` → attestation exclusion, `attestation_scanner.py:307-311` — wake turn does not re-arm the attestation window, identical to restart-kind today) and `:3490` (`is_system_origin` — see A5)."

**That enumeration is incomplete.** The `system:` value is stamped via **`_stamped_additional_kwargs` (`daemon/services/instance_messaging.py:400-424`, applied at `:587`)**, and that stamp has **three consumers**, each a real behaviour delta for the promote-kind wake:

1. **Attestation ladder exclusion** — `daemon/services/attestation_scanner.py:315-444` (the exclusion predicate `is_real_user_message`): the wake no longer advances the last-real-user-message anchor and renders as **SECTION U** (`daemon/services/attestation_resolver_activation.py:1297-1325`).
2. **No durable repair-budget reset** — `daemon/graph.py:2138-2150` (the "REAL (non-injected) HumanMessage" predicate): the wake no longer resets the durable repair budget.
3. **Compacted as an injected note** — `daemon/_content_hardening.py:141-160` (`is_injected_message`) and `:216-224` (the compaction absorb set): the wake is compacted as an injected note rather than as a user message.

**Corrected reading:** *"`system:` value trips `_stamped_additional_kwargs` (`instance_messaging.py:400-424`, applied at `:587`) and its three consumers — attestation ladder (`attestation_scanner.py:315-444`; repair-budget reset (`graph.py:2138-2150`); compaction class (`_content_hardening.py:141-160`/`:216-224`) — plus the `is_system_origin` lane (`instance_messaging.py:3490`; see A5)."* Note also that `_content_hardening.py` lives at **`daemon/_content_hardening.py`**, not `daemon/tools/`.

**Disposition:** precedented and acceptable — **the restart-kind wake already has exactly this shape in production** (`RESTART_WAKE_SOURCE` is also a `system:` literal), so the promote-kind wake is *inheriting* a shipped shape rather than inventing one. But it is a **real behaviour delta** and must be **disclosed** in the DF-4 release notes and **pinned** (pin (vi) asserts the `injected_message` stamp shape at `:587`). The DF-4 plan set now carries this in plan-overview R1b, Honesty Clause item 7, and Success Criterion #12.

**Erratum 2 — the `enqueue_message*` census count.**

The Q1 exhaustiveness basis (line 7) states *"an AST census of all 45 `enqueue_message*` call sites in `daemon/` (0 omit `source=`)"*. **The correct count is 43, not 45.** An independent AST walk over `daemon/` (2026-10-22, planner) finds **43** `enqueue_message*` calls, **0** omitting `source=`. The **material fact — 0 omissions — is unchanged** and the exhaustiveness argument stands; only the denominator was off by two.

**Erratum 2 (scope extension) — the SAME denominator error in the Q7 missed-item 8 text.**
The Q7 finding 8 (line 122) states *"all 45 `enqueue_message*` call sites pass `source=`
explicitly (AST census, 0 omissions)"*. **The correct count is 43, not 45** — the same
correction as above, applied to the second site. The **material fact — 0 omissions — is
unchanged**. The architect's prose above is **quoted, not rewritten**; the corrected
denominators also live in `plan-overview.md` (the Honesty Clause item 8 and Success
Criterion 11), which this erratum covers.

**Erratum 3 — the A13 follow-up anchor.**

A13's anchor `daemon/tools/instance_ops.py:968` was flagged in a later pass as "the confirmed-target check". **That flag is incorrect.** `:968` is a genuine `instance_ops:{caller}` notify-lane source literal — `source=f"instance_ops:{caller_instance_id or 'unknown'}"` — inside `async def instance_reclaim` (line 895). Two sibling literals exist at **`:1114`** and **`:1119`** inside `async def instance_nudge` (line 1048). The DF-4 plan's Routed-Follow-ups A13 row now cites all three.

**Erratum 4 — census denominator, consolidated statement (2026-10-22, leader-directed).**

Both architect sites carry the same wrong denominator: **line 7** ("an AST census of all 45 `enqueue_message*` call sites") and **line 122** (Q7 finding 8, "all 45 `enqueue_message*` call sites"). The correct count is **43**, with **0 calls omitting `source=`** — the material fact is unchanged and the exhaustiveness argument stands. Confirmed by **two independent AST passes agreeing** (the reviewer's governor-council pass and the approver's worker pass), plus the planner's own walk (43 calls, 0 omissions). Per the append-only discipline the architect's prose at both lines is **not edited** — this erratum is the correction of record for both. The plan set states 43 wherever the census is asserted, citing this ERRATUM.
