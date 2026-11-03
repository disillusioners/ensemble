# Decisions — metrics-agent-tools

Status: the design decisions below are folded **VERBATIM** from the binding design `technical-analysis.md` §D (:1557-1695) — they are authored decisions, cited by section; the planner does not redesign them. The planner adjudications **P-1..P-7** follow, binding on this plan set.

---

## Part 1 — D.UX — output shape & UX (folded verbatim from `technical-analysis.md` §D "D.UX", :1561-1636)

**D-UX-1 — Flat tools, ONE category, three tools.**
*Decision:* one privileged category `metrics-read`; three flat tools `metrics_families` / `metrics_snapshot` / `metrics_history`, mirroring the provider's three methods.
*Rationale:* each `args_schema` stays minimal and is itself the agent's documentation surface (`loader.py:684-707`); each tool makes ONE provider call and is independently auditable; the house precedent (wedge-capture: 2 tools/1 category; instance_read: 4 tools/1 category) matches.
*Rejected:* a mega-tool with a `view=` enum (forces one schema to carry the union of all parameters and makes the tools-list in the system prompt uninformative); a single "query" tool with a raw filter string (defeats the contract's label-aware selector and re-implements query parsing in the tool layer).
*Cite:* technical-analysis.md §A.0.

**D-UX-2 — The tool is named `metrics_history` (provider method), not `metrics_series` (wire route).**
*Decision:* `metrics_history`.
*Rationale:* the tool calls `provider.history()` in-process; the three names then read as a 1:1 mirror of the Protocol's three methods, matching D4's "one provider, three views". The `/series` wire name survives in the tool's `series` ARGUMENT (the contract's canonical label-aware selector) and the mapping is stated in `CATEGORY_DOC`, each docstring and `_full_doc_`.
*Rejected:* `metrics_series` (creates a second vocabulary that matches the wire but not the seam; an agent that read the API doc would hunt for a name that does not exist).
*Cite:* technical-analysis.md §A.0.

**D-UX-3 — The tool result is a PROJECTION of the frozen envelope, not the envelope.**
*Decision:* the tool echoes the frozen header members (`schema_version`, `vocabulary_version`, `generated_at_utc`, `restart_epoch`, `activation`) verbatim, reuses the frozen field names for what it surfaces, and ADDS tool-layer members (`read_status`, `query`, `matched_count`, `returned_count`, `dropped_count`, `more_available`, `truncated`, `narrow_hint`, `caveats`, plus the derived per-row/per-family members).
*Rationale:* the frozen contract governs the **HTTP wire**; the agent result is a new surface and must not claim to BE the envelope. The AM-7 two-axis policy (`schema_version` = envelope shape, `vocabulary_version` = label vocabulary) is untouched — the tool echoes both and never gates on them.
*Rejected:* returning the raw envelope verbatim (it carries the HTTP page semantics — `offset`/`limit`/`truncated`-as-page — which do not apply to a tool call, and it lacks the decision-ready derived members).
*Cite:* technical-analysis.md §A.3, §A.5 consequence.

**D-UX-4 — The sane-default ladder, mapped onto the contract's two-axis bounds.**
*Decision:* `window_s` default 900 (max 7 200 = `WINDOW_SECONDS`); `max_points` default 120 (max 240 = `MAX_POINTS_CEILING`); `top_n` default 10 for history / 20 for snapshot (max 50 = `MAX_NAMES`); selectors ≤ 50 (the tool refuses above it, pre-empting the provider's `InvalidLabelFilter` at `provider.py:346-349`); the provider work bound is `max_series = top_n + 1`.
*Rationale:* the agent must never reason about cursors or cardinality; the defaults keep the provider's work at 11 × 120 = 1 320 points (11 % of `MAX_TOTAL_POINTS = 12 000`) and the tool's CEILINGS at the contract's own ceiling (51 × 240 = 12 240). A 15-minute default window covers the two operational horizons the daemon cares about (the 1800 s wedge trigger; the 30-minute self-alarm floor) without the LLM cost of a 60-minute chart window.
*Rejected:* the contract's own `DEFAULT_MAX_POINTS = 240` as the tool default (it is sized for a 60-minute CHART; for an agent it is a 4× larger payload for no extra decision value); passing `max_series=None` (unbounded provider work — the tail could be 2000 series × 240 points).
*Cite:* technical-analysis.md §A.4.

**D-UX-5 — No cursor in v1; every call re-anchors; the continuation mechanism is "narrow the selector".**
*Decision:* no tool accepts or returns a cursor. The tool computes `now = int(time.time())` itself and passes `now=instant`; `compare=true` reads the preceding window at `now = instant − window_s` (an internal re-anchor). Truncation is continued by narrowing the filter, guided by `narrow_hint`.
*Rationale:* a cursor pins an evaluation instant — correct for a paginating HTTP client, wrong for an agent making a forward-moving decision. Re-anchoring every call means "the last N seconds as of now", which is the question an agent actually has. The comparable-window leg still needs the anchor, which is why `now` is a required Protocol amendment (GAP-1).
*Rejected:* exposing the cursor (agents would carry a stale instant across turns and reason about it; the tool has no page model to resume); splitting ONE 2×-window read into two halves to avoid the second call (unsound: bucket-max downsampling makes a half-window's first/last point a bucket maximum, not an observation — the delta would be wrong).
*Cite:* technical-analysis.md §A.5.

**D-UX-6 — A bound that drops something is NEVER silent — the uniform quintet.**
*Decision:* every tool emits `matched_count`, `returned_count`, `dropped_count`, `more_available`, `truncated`, `narrow_hint` with identical semantics in all three tools. `truncated` is true whenever anything was dropped. `narrow_hint` names a CONCRETE next filter.
*Rationale:* the contract's clause *"a bound that drops something is never silent"* (`metrics-query-contract.md:283-286`) governs the HTTP page body; the tool honours its INTENT with strictly MORE information than a cursor (an exact count where it is knowable, plus an instruction). `metrics_families` and `metrics_snapshot` have EXACT counts; `metrics_history` has an exact `returned_count` + `more_available` (the provider's work bound prevents counting the tail) and the hint routes the agent to `metrics_snapshot` for the exact live-variant count.
*Rejected:* omitting `matched_count`/`dropped_count` and reporting only a boolean (the agent cannot tell "2 dropped" from "2 000 dropped" — a materially different decision).
*Cite:* technical-analysis.md §A.6.

**D-UX-7 — `dark` is NOT "no data": the derived `data_present` / `dark_with_live_series` + a GENERIC caveat.**
*Decision:* the tool carries `availability` **verbatim** and adds `data_present` (`live_series_count > 0`) and `dark_with_live_series`. When any returned family is dark-with-live, a `caveats[]` entry says the fact **without attributing a cause**.
*Rationale:* F1 (9 series), F3 and F6 (15 series) report dark while publishing — a PRODUCTION defect owned by other lanes (R-i/R-ii). A tool that read "dark" as "no data" would send the agent back to `bash`-curling `/readyz` — the uncontracted workaround the census names.
*Rejected:* treating `dark` as empty (wrong for 3 of 16 families today); hard-coding the defect attribution ("R-ii") into a shipped tool string (it would go stale the moment those lanes land — the tool asserts only what it can verify at runtime).
*Cite:* technical-analysis.md §A.9(i).

**D-UX-8 — `registry_read_ok` is the degraded-read signal; the uniform `read_status` block; the FE shares the vocabulary.**
*Decision:* all three tools carry `read_status: {registry_read_ok, mode, note}` at the top level, where `registry_read_ok` EQUALS the frozen `activation.registry_read_ok` (`envelope.py:70`; `provider.py:98-107`, set false at `:152-160` when the registry read RAISED), `mode` is `"ok"|"degraded"`, and `note` is null when ok. An EMPTY payload with `registry_read_ok=false` is reported as *"the read FAILED — unknown, not empty"*, never as "no data".
*Rationale:* a raised registry read and an empty registry produce byte-identical bodies (`provider.py:100-104`); without the flag a client cannot tell them apart. The FE has the same blind spot (its `catalogError` only fires on a transport failure — `metrics.component.ts:118-129`), so the FE mirror must set `catalogError` when `activation.registry_read_ok === false`, keeping the FE's error/empty distinction one-for-one with the tool's `mode`.
*Rejected:* using an empty payload as the absence signal (indistinguishable from failure — the exact defect the flag exists to prevent); inventing a second word for the same concept (the FE and the tool must share one vocabulary, or the alignment breaks).
*Planner note (P-3 refines the FE half):* the FE renders the degraded state as a DISTINCT badge/hint — never the error banner — so the existing "a 200-EMPTY response never renders the error banner" pin (`metrics.component.spec.ts:15`) stays true; see P-3 below.
*Cite:* technical-analysis.md §A.9(ii).

**D-UX-9 — The availability reason is carried VERBATIM; the tool never re-derives or re-orders it.**
*Decision:* `availability` is copied from the provider's family entry unchanged. The tool does not read the census switch, inspect threads, probe the DB, validate the reason against the closed set, or substitute a reason.
*Rationale:* the closed set and its REQUIRED ORDER (`census_not_running` BEFORE `census_gated`) are frozen (`metrics-query-contract.md:350-367`; `availability.py:76-92`), the backend already ASSERTS the emitted reason is in the set (`provider.py:256-266`), and the facts are INJECTED — *"the query package never goes looking for them itself"* (`availability.py:49-62`). The tool is a consumer, not a second source of truth.
*Rejected:* re-deriving availability in the tool layer (it would duplicate the reason order in a second place and drift); "improving" a reason the provider emitted (the tool cannot see the truth the provider saw).
*Cite:* technical-analysis.md §A.8.

**D-UX-10 — The `open` cardinality class renders `labels: null` and is NEVER value-enumerated.**
*Decision:* the catalog entry for an `open` name renders `labels: null` + `cardinality_class: "open"`; live rows carry their OBSERVED label combination plus the same `cardinality_class`; the tool never expands an open metric's label value space.
*Rationale:* the rule is frozen (`metrics-query-contract.md:150-153`, `:310-311`) and enforced by the provider (`_declared_labels` returns `None` unless closed, `provider.py:193-202`). Series identity IS `(name, labels)` (AM-1) so live rows must carry their labels; the class member is what stops the agent from reading an observed combination as a declared space.
*Rejected:* renaming the row field to `observed_labels` (breaks the frozen field-name reuse and the "reuse frozen names" rule); enumerating an open metric's values for the agent's convenience (a direct contract violation — the open class exists precisely because the value space is unbounded).
*Cite:* technical-analysis.md §A.10.

**D-UX-11 — Deltas are computed and compared, never fabricated.**
*Decision:* `delta` = last − first of the RETURNED window; `prev_delta` = the same over the preceding equal-length window; `delta_change` = `delta − prev_delta`; a window with fewer than 2 points yields `null` + `delta_reason: "insufficient_points"`; the series carries `downsampled: bool`.
*Rationale:* "deltas vs a comparable window" is what makes a trend decision-ready, and the comparable window is exactly the second anchored read (D-UX-5). A fabricated `0` asserts "nothing changed" — a different claim from "we cannot tell". `downsampled` tells the agent whether the delta is observation-level (the default: `max_points` ≥ `window_s ÷ effective_interval_s`) or bucket-level.
*Rejected:* emitting `0` for an insufficient window (a false claim); omitting the comparable-window leg (a raw trend is not a decision input); splitting one 2× read into halves (unsound under bucket-max — D-UX-5).
*Cite:* technical-analysis.md §A.7, §A.3 Tool 3.

**D-UX-12 — The provider is reached through an ADDITIVE manager-held mount.**
*Decision:* `InstanceManager.metrics_query_provider` (class-level default `None` + a typed property), set by the lifespan at `api.py:2533` beside the existing `app.state.metrics_query` assignment; the tool factory resolves it via `create_metrics_read_tools_if_available` (the `create_board_tools_if_available` pattern) and returns `[]` when it is absent. *(superseded-in-part by AmM-3/RULING-1 — see Part 5: the `_if_available` wrapper is DROPPED; tools stay PRESENT on degraded boot and refuse loudly at call time.)*
*Rationale:* the factory already receives `manager` (zero new plumbing at the call site); it matches the house's `manager.board_service` / `manager.scheduling_service` precedent; it avoids a module-level mutable global; and it guarantees the tool reads the SAME provider and the SAME in-RAM ring as the HTTP door (a second provider would have an empty ring — `provider.py:340-341`).
*Rejected:* a module-level holder in the seam (a mutable global); a factory called by both the app and the tool (the tool would build a SECOND ring — it would silently return no trend forever); the tool calling the HTTP door over loopback (a network hop for an in-process read, plus a bearer-secret dependency).
*Cite:* technical-analysis.md §A.1.

**D-UX-13 — The Protocol is extended ADDITIVELY; the tool is typed against it with NO `Any`/`getattr` escape.**
*Decision:* amend `daemon/services/metrics_query/protocol.py` — add `now` and `max_series` to `history()`, and add the properties `registry_read_ok`, `listener_enabled`, `census_enabled`. *(superseded-in-part by AmRULING-3 — see Part 5: the amendment TRIMS to `now`/`max_series` + `registry_read_ok` only; GAP-4/5 dropped.)* The two NUMERIC facts (`effective_interval_s`, `last_sample_age_s`) are read through the seam's own public reader (`envelope.effective_interval_s` + a promoted `envelope.read_numeric_fact`), NOT through `getattr`.
*Rationale:* the house lesson (*"a Protocol that no consumer references guarantees nothing — extend the Protocol BEFORE typing the consumer"*) applies with full force: this lane IS the first consumer. The amendment is additive in the SEAM (the contract governs the HTTP/wire semantics; the Protocol governs the in-process seam) and the implementation already supports it (`provider.py:325-326`, `:87-122`). The two numeric facts stay out of the Protocol because their duck-typed shape already varies between the provider and the sanctioned fake, and the seam already ships the sanctioned bridge for exactly that (`envelope._provider_float`, `envelope.py:135-146`).
*Rejected:* typing the provider as `Any` and using `getattr` everywhere (the exact failure the lesson names — the Protocol's incompleteness stays invisible); declaring the numeric facts in the Protocol (it would force one shape on a provider and a fake that deliberately differ, for no consumer value); a SECOND protocol for the freshness facts (splits the one substrate into two contracts).
*Cite:* technical-analysis.md §A.2.

**D-UX-14 — No audit ledger (R5).**
*Decision:* the metrics category writes NO audit row, NO migration, NO table.
*Rationale:* the house precedent is explicit and applies twice — `instance_read.py:62-64` (*"The audit ledger belongs to the ACT category only: a read has no side effect and writes NO audit row"*) and `wedge_capture.py:30-32` (*"THE READS HAVE NO SIDE EFFECT — A read writes NO audit row, deletes NOTHING, and triggers NOTHING"*). Nothing in the house pattern obliges a read to write.
*Rejected:* adding a "who queried what" ledger (it would be a NEW table + a migration + the two-tier coupling — a write surface in disguise, and it would make the migration delta non-empty for a read-only lane). If query auditing is wanted later, it is its own decision with its own lane.
*Cite:* technical-analysis.md §A.11 site 19.

**D-UX-15 — No kill-switch for this category.**
*Decision:* no dedicated kill-switch.
*Rationale:* a kill-switch is proportionate to a surface with a **side effect and a blast radius beyond the daemon** — `notify_user` has one because it writes to the user's chat channel (`notify_user.py:106`), and `instance_ops` has one because it can terminate a cascade tree. A read has neither. The effective off lever is **removing `"metrics-read"` from the three `meta.json` allow arrays** (three one-line edits, no restart of the daemon needed for the next instance construction) plus the privileged category's default-deny (an empty-allow agent never had it).
*Rejected:* adding `ENSEMBLE_METRICS_READ_ENABLED` (a knob that guards nothing observable — the switch would be read by the tool layer, adding a second check site; and the door itself is never switch-gated, so a switch-gated tool would be INCONSISTENT with the door it mirrors — the contract's invariant `no_direct_switch_read_in_query_package` and `check_site_census_equals_two` would both be violated in spirit).
*Cite:* technical-analysis.md §A.11 site 23.

---

## Part 2 — D.SCOPE — the `metrics:read` split (folded verbatim from `technical-analysis.md` §D "D.SCOPE", :1638-1673)

**D-SCOPE-1 — MINT a new scope `metrics:read`.**
*Decision:* add `"metrics:read"` to `SCOPE_TAXONOMY` (`daemon/auth/scopes.py:60-101`); re-point the three query routes to `scope_required("maintenance:read", "metrics:read")`; the text-exposition route keeps `maintenance:read`; the manifest string becomes `"/api/metrics": "maintenance:read|metrics:read"`.
*Rationale:* the D6 trigger is MET (the shipped API doc names the agent-tool door as a trigger — `metrics-query-api.md:286-292`); `maintenance:read` also confers checkpoint-cleanup-EXECUTE (the contract's own AM-20 disclosure at `:404-405`), which a metrics-only machine lane must not carry; the cost is 4 lines + 2 test strings, and `SESSION_OPERATOR_SCOPES = SCOPE_TAXONOMY` means no operator re-grant and no migration.
*Rejected:* reuse `maintenance:read` with a role distinction (the grant model has NO role axis — `scopes.py:42-52`; and it leaves the over-grant in place); a scope-gated agent identity (agents hold no AuthContext; the house precedent is that the agent's internal lane bypasses HTTP scope — `scopes.py:78-84`, D10).
*Cite:* technical-analysis.md §B.2, §B.5, §B.7.

**D-SCOPE-2 — The tool path is gated by the house TWO-LAYER pattern, not by the HTTP scope.**
*Decision:* the privileged category (visibility) + the in-body `agent_id` gate (authority). The tool's authority model is the agent-id allow-set `{maintenancer, devops, wanderer}`.
*Rationale:* the seam is unscoped by construction (`provider.py:26-43`; `protocol.py:3-8`) and D6's retrofit map is entirely HTTP-shaped — executing it literally changes NOTHING about the tool path. The in-body gate is the unbypassable one: a literal tool name in an allow list escapes the CATEGORY gate (`_tool_registry.py:559-560`), but the allow list never reaches the body gate.
*Rejected:* scope-gating the tool (there is no AuthContext to check); relying on the category gate alone (a literal tool-name allow entry bypasses it — D-13); relying on the allow list alone (the same hazard).
*Cite:* technical-analysis.md §B.1, §B.3.

**D-SCOPE-3 — The meta.json entries are CATEGORY IDS ONLY.**
*Decision:* each of the three holders' `tools.allow` carries `"metrics-read"` — never a tool name.
*Rationale:* D-13 — a literal tool name bypasses the category gate outright (`_tool_registry.py:559-560`; `instance.py:561-568`). The category id is also what makes the entry resolve as a known category at boot (`registry.py:1355-1368`).
*Rejected:* listing `metrics_families` / `metrics_snapshot` / `metrics_history` (they would be visible to any agent whose allow list contains the literal — and would escape the default-deny strip entirely).
*Cite:* technical-analysis.md §B.3.

**D-SCOPE-4 — The tool's fail-closed ladder mirrors the door's 401/403/503/400 semantics.**
*Decision:* four distinct refusal tokens — `unknown-agent` (blank/`None`/non-`str` agent id ≡ 401), `not-authorized` (known agent id outside the allow-set ≡ 403), `metrics-unavailable` (provider not mounted ≡ 503 registry_unavailable), `invalid_label_filter` (malformed / over-ceiling selector ≡ 400). The authorization check runs **FIRST** — before the provider is touched at all.
*Rationale:* the door's fail-closed principle is *"the gate fires BEFORE the registry is read"* (`routers/metrics.py:171-220`); the tool must mirror it exactly, so an unauthorized call never reads the registry, never materializes a series, and never even learns whether the substrate is mounted.
*Rejected:* one generic "denied" token (the agent cannot distinguish "I am not allowed" from "the metrics stack is down", and those have different next actions); checking the substrate before the authorization (it leaks the substrate's state to an unauthorized caller).
*Cite:* technical-analysis.md §B.3 (the ladder table + the gate chain).

**D-SCOPE-5 — The tool does NOT require auth to be configured.**
*Decision:* the tool path never consults `app.state.auth_config`.
*Rationale:* the tool is not a network surface — there is no anonymous caller in-process, and the exposure the router's 503 guards against (*"anyone who can reach the port"*) does not exist. The deployed default on this host is auth-OFF; gating the tools on it would kill the only sanctioned health view for the daemon's own maintainer/devops/wanderer. The house precedent is that the agent's internal lane bypasses HTTP scope (`scopes.py:78-84`).
*Rejected:* mirroring the 503 literally (it would disable the tools in the default deployment); checking the auth config as a soft warning (a warning nobody reads is not a control).
*Cite:* technical-analysis.md §B.4.

**D-SCOPE-6 — Blast radius on the scope pins: two string pins change, the route-count pin does NOT move.**
*Decision:* update `tests/unit/test_router_scope_manifest.py:169` (family scope) and `tests/unit/metrics/test_metrics_query_scope_cage.py:43`/`:147`/`:150-157` (the `FAMILY_SCOPE` constant and the per-route assert). The declared-route count stays **111** — this lane adds ZERO routes. The mount census stays 37 and the class-manifest count stays 33. The `ari` trailing-entry allow pin is unaffected (the metrics category is not granted to ari).
*Rationale:* D6's step 4 (the cage is a subset check) means the routes stay inside the pinned family set; and a tool is not a route.
*Rejected:* bumping the route count (it would be wrong — the count already moved 108 → 111 in central-metrics-v1, and this lane adds no routes).
*Cite:* technical-analysis.md §B.6 (incl. the disclosed research correction at `:1463`). *Binding restatement:* P-4.

**D-SCOPE-7 — The contract doc amendment is a cited, DESCRIPTION-ONLY amendment.**
*Decision:* amend the frozen contract's PROSE (the §1.13 matrix rows 2–3, the §6 truth table, the §7 disclosure, the AM-20 disclosure) to record that the three query routes accept `maintenance:read` OR `metrics:read`. **No machine-pinned fenced block is touched** (the pinned blocks are `activation-surfaces`, `query-routes`, `error-vocabulary`, `truth-table-invariants`, `availability-reasons`, `gate-pack-ids` — none carries a scope string), so `central_metrics_contract_doc` stays green. `schema_version` stays 1 and `vocabulary_version` stays 2 (no wire shape changes). The amendment cites D6 as its authority, following the doc's own in-place amendment precedent (`:248`, `:396`).
*Rationale:* the contract names `maintenance:read` as THE scope in four prose places; the retrofit changes the door's grant set, so the prose must follow. The amendment changes NO behaviour for any existing holder.
*Rejected:* leaving the prose stale (the contract would name a scope set that no longer matches the code — a documentation defect in a frozen document is worse than a cited amendment); editing a machine-pinned block (it would break the pack for no reason).
*Planner note (P-1 bounds the execution):* this lane does NOT execute the prose amendment by default — it is the CONDITIONAL APPENDIX requiring explicit lane-owner confirmation; see P-1 below. *(SUPERSEDED 2026-11-03 by the P-1 / E-1 adjudication: the amendment is now PRIMARY (3.6b) under guardrails (a)-(d); this note remains as the pre-adjudication record.)*
*Cite:* technical-analysis.md §B.6 (:1469-1471), §E.

---

## Part 3 — D.WATCH — the future lane (folded verbatim from `technical-analysis.md` §D "D.WATCH", :1675-1695; design-only, carried for reference)

**D-WATCH-1 — v1 is pull-only; the watch/alert surface is a SEPARATE future lane.**
*Decision:* this lane ships three read tools and no alert machinery. The watch/alert design doc is authored DURING the future lane's implementation as a close-out artifact, not planned here.
*Rationale:* ruling R3; the delivery problem is genuinely hard (§C.3 — only ONE primitive is reusable without widening an ari-owned authority surface), and the estimate is 3–4 engineer-weeks — a lane, not a follow-on.
*Rejected:* adding an "arm a watch" tool to this lane (arming is a WRITE with a durable schedule row and a firing invariant — it would need an ACT category, an audit ledger, and its own kill-switch; it would also make this lane's migration delta non-empty and its "read-only" posture false).
*Planner note (P-7 re-schedules the doc):* the outline doc is authored during THIS lane's implementation as the close-out artifact (Phase 6 task 6.1), from the design's Section C outline verbatim — still planning no machinery.
*Cite:* technical-analysis.md §C preamble.

**D-WATCH-2 — The ONLY immediately reusable delivery primitive is the scheduler's deferred/coalesced path.**
*Decision:* if the future lane wants a delivery path that works for ANY agent, the scheduler's deferred-durable + coalescing delivery is the base (`daemon/sources/adapters/scheduler.py:1111-1163`). Its bounds are pydantic-settings knobs, not DDL ⇒ the migration delta stays EMPTY.
*Rationale:* it is agent-agnostic (the pinned caller is a schedule row, not an agent), durable, and at-most-once by construction (`deferred`/`coalesced`; `_COALESCE_*` vocabulary at `:126-136`).
*Rejected:* `self_alarm` (target hardcoded to ari — widening it is a user-visible authority change); `notify_user` (same, plus it writes to the user's chat); `board_*` (ari-owned global namespace); `watchover` (**not a delivery channel at all** — it is a tool-call interceptor with no queue and no recipient).
*Cite:* technical-analysis.md §C.1 item 2, §C.3.

**D-WATCH-3 — Watchover is NOT a delivery channel.**
*Decision:* the future lane may reuse watchover's **pause-first-then-quiesce choreography** and nothing else.
*Rationale:* `WatchoverService` intercepts a watched instance's tool calls with an LLM evaluator and returns Allow/Deny (`watchover_service.py:236`, `:12-18`); the `watcher` agent is tools-less. There is no queue, no recipient, no delivery.
*Rejected:* treating it as an alert transport (it would fire on tool calls, not on metrics — a different trigger class entirely).
*Cite:* technical-analysis.md §C.3 (the watchover row).

**D-WATCH-4 — The "may alerts reach non-ari agents?" question is OPEN and belongs to the future lane — but it is flagged here because it bears on this lane's scope decision.**
*Decision:* this lane's READ surface is granted to three non-ari agents (R4); the future lane's ALERT surface may end up ari-only. The asymmetry is acceptable and must be stated in the shipped docs.
*Rationale:* the read surface is strictly less sensitive than the wedge-capture surface the three agents already share (all-thread stack dumps + per-thread CPU maps, `wedge_capture.py:30-32`), whereas the alert surface touches a durable schedule row and possibly the user's chat.
*Rejected:* pre-deciding the alert audience in this lane (it is a user decision, and it depends on the future lane's delivery design).
*Cite:* technical-analysis.md §C.4 item 1.

---

## Part 4 — Planner adjudications (BINDING, authored by this plan)

**P-1 / E-1 — ADJUDICATED 2026-11-03.**
*Ruling (recorded VERBATIM from the lane owner; supersedes P-1's default-safe posture below — the mint/re-point/manifest content of P-1 STANDS):*
> "ADJUDICATION on E-1 (2026-11-03, lane owner): PROCEED with task 3.6b (the pre-specified, cited, description-only amendment executed in the SAME commit as the re-point). Rationale: D6's scope split is the contract's OWN pre-named commitment — authored by lane #1 and user-ratified via the metrics sequence. Implementing it in prose is contract COMPLETION, not drift. The frozen-contract reservation protected the WIRE contract (VOCABULARY_VERSION 2, series identity, cardinality classes, cursor policy, buckets-on-the-wire) — none of which a scope-name prose amendment touches. Leaving known doc/code divergence (the 3.6a path) is strictly worse than a scoped same-commit amendment — house truth-in-docs precedent.
> Guardrails (binding):
> (a) amendment strictly limited to the 4 cited prose places (metrics-query-contract.md:31-32, :318-322, :379-380, :404-405) — nothing else in the contract doc changes;
> (b) NO fenced/machine-pinned block changes — the contract-doc pin must stay green by construction, not by re-pinning;
> (c) same-commit discipline: doc amendment + scope re-point land in ONE commit, doc and code never diverge on the branch;
> (d) the plan council and fresh approver still review it — if either objects on substantive grounds, E-1 returns to the lane owner for re-adjudication (3.6a remains the documented fallback)."

*(GUARDRAIL (a) ABOVE IS SUPERSEDED-IN-PART by E-1 amendment-2 below: the enumeration widens to the FIVE canonical line-ranges (`:387` is IN); guardrail (b) is RESTATED (green WITH the amendment applied, protected by the verbatim-preservation list + the line-scoped allow-list); guardrails (c)/(d) stand unchanged. The original block is retained as the historical record, house pattern.)*

*Effect:* task 3.6b (the amendment) is PRIMARY and always-executed; task 3.6a (the debt-record path) is the FALLBACK, executed only if guardrail (d) triggers. Fold sites: `phase3-plan.md` task 3.6 (polarity flipped), `plan-overview.md` (P-1 row, phase map, R9, P3 criterion, open questions), `fold-ledger.md` §D-2.

**E-1 amendment-2 / lane-owner re-adjudication (2026-11-03) — supersedes the site enumeration.**
*Ruling (recorded VERBATIM from the lane owner):*
> "Original ambiguity: my adjudication named 4 prose places; plan/task text in some spots said `:379-380 + :387`. Resolution on the ruling's own rationale (doc and code never diverge):
> 1. Canonical amendment set = FIVE line-ranges: metrics-query-contract.md :31-32, :318-322, :379-380, :387, :404-405 — i.e., :387 is IN. Rationale: :387 names maintenance:read as the door's scope; excluding it would leave known divergence in the exact class the E-1 ruling exists to eliminate. Nothing outside these five ranges changes.
> 2. Guardrail (b) RESTATED: the contract-doc pack must be GREEN WITH THE AMENDMENT APPLIED — protected by (i) an explicit verbatim-preservation list of every pinned substring the edit must leave byte-identical, and (ii) the line-scoped allow-list above as the only permitted edit surface. Re-pinning the pack REMAINS FORBIDDEN. If no edit wording can both make the scope change AND preserve every pinned substring, E-1 returns to me for re-adjudication (3.6a fallback).
> 3. Guardrail (a) updates to the five-range canonical set; guardrails (c) same-commit and (d) objection-return stand unchanged."
*Effect:* task 3.6's amendment surface is the FIVE canonical ranges; the acceptance artifact is the verbatim-preservation list (Phase 3 task 3.6, Phase 5 tasks 5.2/5.8); the fallback (3.6a) additionally fires if no edit wording can satisfy guardrail (b) as restated. Propagated to: `phase3-plan.md` (tasks 3.6/3.5/3.8 + the fence), `plan-overview.md` (P-1 row, R9, P3 criterion, the scope bullet), `technical-analysis.md` (inline pointers only), `fold-ledger.md` §A.3/§F.

**P-1 — E-1 RESOLVED AS DEFAULT-SAFE.** *(PRE-ADJUDICATION RECORD — superseded-in-part by the P-1 / E-1 adjudication 2026-11-03 and its amendment-2 above the DEFAULT-SAFE posture is superseded; the mint/re-point/manifest content stands; 3.6a remains only as guardrail-(d)'s documented fallback.)*
*Ruling:* mint `metrics:read` in the taxonomy + re-point the three routes to any-of (`maintenance:read`, `metrics:read`) + update the two scope-manifest pins — but the FROZEN contract doc's PROSE is NOT amended in this lane (the commission reserved contract changes for itself). The plan instead includes (i) a task to log the prose divergence as contract-owner debt (Phase 3 task 3.6a), AND (ii) the pre-specified description-only amendment (technical-analysis.md E-1; the four cited prose sites `metrics-query-contract.md:31-32/:318-322/:379-380/:404-405`) as a CONDITIONAL APPENDIX task (3.6b) the implementer executes ONLY if the lane owner confirms before implementation. The contract-doc pin test must stay green either way (no machine-pinned block carries a scope string — verified site list in D-SCOPE-7).
*Rationale:* the design's own escalation (§E) recommended PROCEED-under-D6 and offered exactly this fallback (*"If the caller rules 'no contract prose edit at all': the fallback is to execute D-SCOPE-1's taxonomy line + the manifest/decorator re-point only, and record the contract-prose divergence as a known documentation debt"*). The planner takes the fallback as the DEFAULT because the commission's frozen-contract fence outranks the doc-hygiene argument, while the code-side retrofit is zero-behaviour and D6-mandated. The conditional appendix preserves the design's preferred outcome behind an explicit confirmation gate.
*Interaction:* D-SCOPE-7 (the amendment's content and style) stands; P-1 bounds WHO may fire it and WHEN. The evidence pack records the executed variant (Phase 3 task 3.6; Phase 6 task 6.3).

**P-2 — Fold (b)'s six numbered tidier Mediums are a PROVENANCE GAP; no invented mappings.**
*Ruling:* the six numbered tidier Mediums (items 3, 5, 10, 16, 17, 21) are NOT recoverable from the repo (the tidier's per-item list was never committed; research-folds §2.2's search table proves it). They are routed to the implementation lane's OWN tidier pass, which records the numbered mapping into `fold-ledger.md` §G at that time — or records the cannot-recover outcome verbatim. The plan does not invent item mappings.
*Rationale:* research-folds §2.2 names the two admissible resolutions (obtain the list from the tidier lane/leader, or re-derive with the explicit no-numbering-reproduction caveat); inventing a third ("guess the findings") would fabricate gate obligations. The closest matching findings that DO exist (fold (a); the R-i/R-ii escalations; the PACKS.md registration — already closed) are dispositioned in `fold-ledger.md` §A/§B instead.

**P-3 — FE mirror shape: optional member + `=== false` + a DISTINCT degraded surface.**
*Ruling:* the FE `MetricsActivation` gains `registry_read_ok` as an OPTIONAL member; the failure predicate is `=== false` (absent = healthy — mirroring the BE's `getattr(provider, "registry_read_ok", True)` default at `envelope.py:70`); the degraded state renders a DISTINCT badge/hint surface (own `data-test="metrics-registry-degraded"`) — it must NOT render the error banner, preserving the existing pin *"a 200-EMPTY response never renders the error banner"* (`metrics.component.spec.ts:15`); `showEmptyPanel` gains the degraded guard so the empty panel does not fire.
*Rationale:* research-folds §1.5 F1's own analysis recommends optional (required churns every activation fixture and turns the type-check leg into a fixture-completeness gate); the `=== false` predicate matches the BE's default-safe semantics and the contract's AM-7 "consumers ignore unknown members" rule. F3's minimal shape (reuse the error banner with a distinct `data-test`) is OVERRIDDEN: the banner element carries transport-failure semantics the `:15` pin reserves, so the degraded state gets its own element (the deviation is recorded in `fold-ledger.md` §D).

**P-4 — Route-count correction is binding: 111 today, zero new routes.**
*Ruling:* the declared-route census is **111** and stays **111**; the mount census stays **37**; the class-manifest family count stays **33**; the `ari` trailing-entry allow pin is untouched (metrics is not granted to ari). Any plan step or implementation step that says "bump the route count" is wrong and is rejected.
*Rationale:* the design discloses and corrects the research file at `technical-analysis.md:1463` — the 108→111 transition already happened in lane #1 (the docstring at `test_router_scope_manifest.py:398-401` records it). A tool is not a route (D-SCOPE-6). Phase 3 task 3.4 is the verification task that enforces this.

**P-5 — No audit ledger for the read-only category; the 4-token ladder replaces HTTP semantics in-process.**
*Ruling:* no audit ledger, no migration, no table (house precedent `instance_read.py:62-64`, `wedge_capture.py:30-32`); the fail-closed ladder tokens `unknown-agent` / `not-authorized` / `metrics-unavailable` / `invalid_label_filter` replace HTTP 401/403/503/400 semantics in-process, with authorization firing FIRST.
*Rationale:* restates D-UX-14 + D-SCOPE-4 as a single binding pair so no gate re-litigates the read-vs-act ledger split or proposes HTTP status mapping inside a tool. The ladder's ordering (authorization → substrate → provider) is pinned (Phase 2 task 2.9; Phase 5 task 5.3).

**P-6 — Rulings R1–R6 from the design brief all stand.**
*Ruling:* (R1) flat tools; (R2) the scope split is THIS lane's deliverable; (R3) pull-only — no alert machinery, no cursors to agents in v1; (R4) ONE category, three holders (`maintenancer`/`devops`/`wanderer`); (R5) no audit ledger; (R6) no cursors exposed to agents. Restated so no reviewer re-opens them; each carries its design cite (R1 §A.0, R2 §B.5, R3 §C preamble, R4 §A.0 holders row + research-tool-conventions §5.3, R5 §A.11 site 19, R6 §A.5).

**P-7 — The watch/alert doc is authored DURING IMPLEMENTATION as a close-out artifact.**
*Ruling:* the design's Section C outline becomes `docs/architecture/metrics-watch-outline.md`, authored during this lane's implementation as the final close-out task (Phase 6 task 6.1) — not a planning deliverable of this plan set, and not deferred to the future lane's own bootstrap. It plans NOTHING (D-WATCH-1's hard bound preserved).
*Rationale:* the design wrote Section C as "questions with a recommended shape" for a future lane; authoring the outline now (from the already-complete Section C) costs no redesign and hands the future lane a cited starting point, while the future lane itself still owns every open decision (§C.4). This re-schedules D-WATCH-1's "authored during the future lane's implementation" to "authored during THIS lane's close-out" — the content boundaries are unchanged.

---

## Part 5 — Architect-fold amendments (2026-11-03, BINDING)

Source: `architecture-recommendation.md` (139 L, same dir) — verdict **SOUND-WITH-AMENDMENTS**, zero blocking. READ-ONLY input; folded here per the consolidated fold-map (§2) + rulings (§3) + risk deltas (§4). Every amendment below cross-references its architect ID. Planner rulings **PL-1/PL-2** are recorded in `fold-ledger.md` §E. **The E-1 adjudication block above (P-1 / E-1 — ADJUDICATED 2026-11-03) and its guardrails are BYTE-INTACT** — AmA-8 lands in the API doc, not the contract amendment.

### The four rulings (folded VERBATIM from the architect doc §3)

**RULING-1 (C-3): Option A — tools stay present; provider resolved at CALL time; typed `metrics-unavailable` refusal.**
> "Grounds: (1) the plan's own doctrine weaves the ladder token through D-SCOPE-4, task 5.3's pin, and the completion test ('every tool call is authorized by the in-body gate BEFORE the provider is touched' presupposes the body runs); (2) the stronger in-house precedent for a *substrate-degraded* tool is `notify_user`'s kill-switch shape — the tool remains present and refuses loudly at call time (`notify_user.py:709`), rather than vanishing; for a **diagnosis** surface, silent absence is the worst failure mode (the diagnosing agent doesn't know what it's missing); (3) Option B (delete the token) touches more pinned surfaces than it spares. Constraints: the factory still gates on module + manager handle existence (construction), the **body** reads `manager.metrics_query_provider` per call, a registration-pack leg pins the degraded-boot shape (tools present + refusal), and task 1.6(ii)'s `manager.metrics_query_provider IS app.state.metrics_query` pin stands. Naming: prefer dropping `_if_available` from the factory name (same naming-debt class as the v0.27.8 deferred `create_notify_user_tools_if_available` rename)."
*Fold:* Phase 2 task 2.6 (factory renamed `create_metrics_read_tools`, gates on module+handle existence only, passes `agent_id=agent_id` per AmA-2); task 2.9 (the degraded-boot leg); Phase 5 task 5.3 (the ladder pin is now REACHABLE); D-UX-12's `*_if_available` mention superseded-in-part. *(token: **AmRULING-1**, cited with AmM-3 by phase2 tasks 2.1/2.6/2.9 and phase5 task 5.3.)*

**RULING-2 (B-15): amend the phase-4 success criterion to scope it to the catalog/shell surface, and record the tab-poll residual as routed debt.**
> "Rationale: the tab polls are a pre-existing v0.28.6 FE blind spot, not created by this lane; P-3's binding shape is the shell mirror. In-lane extension remains the planner's option with FE cost — but the criterion as written is false either way and must be corrected now."
*Fold:* Phase 4 success criterion amended; the Overview/Charts tab residual (`overview-tab.component.ts:284-296`, `charts-tab.component.ts:301-311`) recorded as ROUTED DEBT in `fold-ledger.md` §E with the documented cost. Planner ruling **PL-2: NO in-lane tab extension** (see §E). *(tokens: **AmRULING-2** / **AmB-15** — cited by the phase4 success criterion.)*

**RULING-3 (B-1): the tool consumes `activation_block(provider)`; the Protocol amendment TRIMS.**
> "package (ii) — the tool consumes `activation_block(provider)` and derives `read_status.registry_read_ok` from that block; the Protocol amendment trims to GAP-1/GAP-2 (+GAP-3 only if the pack wants a typed surface for the pin). Rationale: GAP-4/5 have no v1 consumer ('a Protocol no consumer references guarantees nothing'); consuming the shipped builder kills the block-drift risk (the wire's `query_api` literal is hardcoded `envelope.py:69`) and makes C-4's per-call read natural. Pin the construction path in `phase2-plan.md:25-27`; make `phase1-plan.md:23` + the completeness pin consistent with the trimmed set."
*Fold:* Phase 1 tasks 1.1/1.6 amended (GAP-4/5 dropped; GAP-3 optional — included here: the pack WANTS the typed pin surface, so GAP-3 stays); Phase 2 task 2.2 (the tools consume `activation_block(provider)` — `envelope.py:56-71`, exported `__init__.py:108` — and derive `read_status.registry_read_ok` from it per call). D-UX-13's GAP-4/GAP-5 declarations superseded-in-part. *(token: **AmRULING-3** — cited by phase1 tasks 1.1/1.2/1.6 and phase2 task 2.2.)*

**RULING-4 (C-1): `async def` bodies AND off-load the body build via `db_offload.offload`.**
> "keep `async def` (house template) AND off-load the body build via `db_offload.offload`, mirroring `routers/metrics.py:441-451`. Rationale: the door already established the pattern for exactly this payload; async-native keeps the tool out of the default executor (the pool the v0.27.14 lane found saturating); the ~3.7 ms figure says the cost is bounded either way, so the ruling optimizes for pattern-consistency and future budget growth, not present need. Pin the callable shape in the registration pack (a leg asserting the coroutine + offload composition)."
*Fold:* NEW decision **D-UX-16** (below); Phase 2 tasks 2.2-2.4 (bodies `async def` + offload); task 2.9 (the registration-pack leg); the event-loop question is RESOLVED NEGATIVE with proof (architect §0: the provider read path is in-memory end-to-end, no psycopg/persistence import on the provider path). *(token: **AmRULING-4** — cited by phase2 tasks 2.2-2.4/2.9.)*

### The merged-cluster resolutions

**AmM-1 (B-5+C-2) — output-budget enforcement is BINDING tool design, amending D-UX-4.** The tool path inherits NONE of the door's output bounds (`MAX_TOTAL_POINTS` enforced in the envelope only, `envelope.py:213,231-250`; `RESPONSE_BYTE_CEILING` in the router, `routers/metrics.py:338-343,391-405`; the tool calls `provider.history` directly). Corrections to the plan's framing: the tool ceiling 51×240 = **12 240 is 240 points ABOVE** `MAX_TOTAL_POINTS` = 12 000 (the design printed both without flagging); `compare=true` is the DEFAULT ⇒ provider work at defaults = **2 640 pts (22 %, not 11 %)**; defaults 1 320 pts ≈ 33 KB ≈ 9k tokens — material for a 7-call patrol. **BINDING requirements:** (a) clamp the returned leg so worst case ≤ `MAX_TOTAL_POINTS` (e.g. `max_points ≤ MAX_TOTAL_POINTS // (top_n+1)`); (b) declare the compare leg's transient provider work separately — it returns only scalars, provider work not agent-context tokens; (c) tool-local byte ceiling with the same trim + six-member bound block the door uses; (d) the default-call agent-context cost stated in `CATEGORY_DOC`. *Fold sites:* Phase 2 tasks 2.2/2.4; Phase 5 task 5.2 (structural assertions); D-UX-4's ceiling clause superseded-in-part (the DEFAULTS values in D-UX-4 stand).

**AmM-2 (B-8+C-5+C-10) — freshness semantics, three landing sites.** Root cause: ring freshness signals cannot distinguish **empty-ring** from **fresh** — post-restart `last_sample_age_s` returns 0.0 (`provider.py:397-404`, `newest is None → 0.0`), derived `stale` is false, `availability` can read "live" (`availability.py:76-92` has no empty-ring reason) ⇒ the agent sees "fresh but empty" for the ~15 min ref-fill window. **BINDING:** (i) per-series `last_sample_age_s` (age of that series' newest point vs anchor) and/or `points_span_s` on history rows; (ii) an explicit ring-state signal in `read_status` (`trend_empty_since_restart` or a distinct caveat); (iii) the watch outline (Phase 6 task 6.1) replaces the design §C.2's wrong "Gap: none" cell for the absence/staleness trigger class — `last_sample_age_s > 3×interval` is **silently inert** post-restart, exactly the wedged-sampler family it claims to cover (design-only fix, no machinery). *Fold sites:* Phase 2 tasks 2.2/2.4; Phase 6 task 6.1; D-WATCH-2 amended (below).

**AmM-3 (C-3) — the mount/unavailable contradiction is resolved by RULING-1.** The `_if_available`-returns-`[]` shape made `metrics-unavailable` unreachable (with tools absent the body never runs) and made the surface vanish silently on degraded boot. Resolution: RULING-1's Option A. *Fold sites:* Phase 2 task 2.6; Phase 5 task 5.3; D-UX-12 superseded-in-part (naming + the degradation posture).

### Amended decision content (each supersedes the cited clause in Parts 1-3)

**AmA-1 — D-SCOPE-2 citation fix.** D-SCOPE-2's rationale cites `_tool_registry.py:559-560` as the bypass mechanism; that range is a **comment**. The enforcement cites are `instance.py:550-560` (the default-deny strip) + `instance.py:561-568` (the literal-name fall-through). Part 2's D-SCOPE-2 *Rationale* is amended accordingly (the D-13 doctrine cite itself stands).

**AmA-4 — D-SCOPE-2 precedent-composition rationale.** Folded where reviewers will look: the two-layer composition is IDENTICAL to the read-precedents (`wedge_capture`/`instance_read` — `_tool_registry.py:529-566`; `wedge_capture.py:84-118`; `instance_read.py:124-128,199-212`); `notify_user`'s third layer is a KILL-SWITCH for side-effect blast radius, correctly rejected for read-only. Read = 2 layers; write/act = +kill-switch/ledger.

**AmD-UX-16 (NEW, from RULING-4) — async bodies + off-loop body build.** The three tool bodies are `async def` (the house template) and off-load the body build via `db_offload.offload`, mirroring `routers/metrics.py:441-451`. *Rejected:* sync bodies on the default executor (the pool the v0.27.14 lane found saturating); unoffloaded on-loop build (pattern-inconsistent with the door; bounded ~3.7 ms today but unbounded as budgets grow).

**AmB-10 (PL-1) — D-UX kwargs deviation, pinned.** `metrics_snapshot` gains `families: list[str] | None` implemented TOOL-SIDE over ONE provider call (`family=None` → local filter → sort → cut), composing with AmB-6's sort-before-cut and AmM-1's ceiling (filter → sort → cut → ceiling order). This DEVIATES from A.3's "exactly the provider's two kwargs" — the deviation is pinned here and inlined in the design (pointer at §A.3). Patrol cost: 7 round trips → 3, noted in `CATEGORY_DOC`. (Part 2 task 2.3's "EXACTLY the provider's two keyword arguments" superseded-in-part.)

**AmD-WATCH — two adjustments.** (i) **D-WATCH-2/§C.2 correction (AmM-2iii):** the outline's absence/staleness trigger row claims "Gap: none" — FALSE post-restart (the `3×interval` trigger is silently inert while the ring refills); the Phase 6 outline doc corrects this cell (design-only). (ii) **D-WATCH-4 landing sites (C-11):** the read-broad/alert-may-be-narrow asymmetry sentence lands in `CATEGORY_DOC` (loader-rendered) + the three `tools_note.md` sections, and the Phase 5 task 5.6 fixture asserts it — the repo-only outline doc is NOT a sufficient landing site.

**AmB-2 — the selector-ceiling correction (composes with the pointer at technical-analysis §D D-UX-4).** `provider.py:346-349` caps `names` only; the `series` list is uncapped provider-side (`provider.py:342-344`); the combined cap is the ROUTER's (`routers/metrics.py:719`) and the tool bypasses the router ⇒ the tool refuses on `len(set(names)) + len(series) > MAX_NAMES` (the router formula, mirrored exactly). Task 2.4's citation corrected accordingly.

**AmB-6 — sort-before-cut.** The provider returns snapshot rows in REGISTRATION order (`provider.py:289` → `checkpoint_metrics.py:437-441`), not the promised ascending order; the tool sorts before the `top_n` cut (tool-side, zero provider change) — keeping the ascending promise true AND making `dropped_count` meaningful.

**AmB-9 — `end_offset_s: int = 0` on `metrics_history`** (window END shifted back from now; clamp `[0, WINDOW_SECONDS − window_s]`; the compare leg anchors at `now − end_offset_s − window_s`) — post-incident review capability, nearly free on the 2 h ring; the no-cursor doctrine is preserved (the anchor stays relative).

**AmB-11 — non-goal declaration** in `CATEGORY_DOC` + tool docstrings: no anomaly-first ordering, no cross-family top-N; the only ordering is deterministic/registry; the anomaly signal is `metrics_history`'s `delta_change`.

**AmB-13 — caveat wording:** the shipped dark-with-live caveat says "the series below are real" — no cause attribution, no invented "dark-but-publishing" availability member (the closed reason set is untouched).

**AmB-14 — "quintet" → "the six-member bound block"** everywhere (`more_available` is the sixth member). Task tables and phase text updated; the design carries the inline pointer at §A.3's block description.

**AmC-4 — per-call freshness semantics.** Shared-instance `registry_read_ok` is one mutable bool flipped by every reader (`provider.py:156,159`) — benign race, but `read_status` presents it as *that call's* fact. Composing with RULING-3: the tool reads `activation_block(provider)` PER CALL; the `read_status` doctrine states the racy-bool semantics explicitly and the Phase 5 pack pins the statement.

**AmA-8 — the item-#3 forward-declared seam (API-doc path; E-1 guardrails byte-intact).** The Prometheus TEXT route keeps `maintenance:read` alone (D6 step 3), so an item-#3 Grafana token with `metrics:read` alone is 403 on the text surface; item #3 must decide the text-route scope, and the second manifest-family amendment site is `tests/unit/test_router_scope_manifest.py:169` (+ the reversal of Phase 3 task 3.5 row (ii)). Landing: a NEW Phase 3 task (3.8) adds the item-#3 debt entry to `docs/architecture/metrics-query-api.md:280-292` — the API doc, NOT the frozen contract amendment; task 3.5 row (ii) is labelled a REVERSAL pin. This COMPOSES with the E-1 adjudication — it is not an objection to it.

**Advisory A1 cite corrections (2026-11-03):** `FAMILY_SCOPE` sits at `tests/unit/metrics/test_metrics_query_scope_cage.py:42` (not `:43`) and the series-route seam cite is `routers/metrics.py:672` (not `:673`) — the WORKING copies (phase3 tasks 3.2/3.3) are corrected; the Part 1/Part 2 VERBATIM folds retain the design's original cites by design (superseded here). `constants.py` cites in working copies are qualified to the full `daemon/services/metrics_query/constants.py` path (the bare name collides with the SEALED `daemon/constants.py`).

**AmA-7 / PL-3 — constants single-sourcing (advisory A7, planner ruling PL-3).** The tool module IMPORTS — never duplicates — `STALE_INTERVAL_MULTIPLIER` / `MAX_TOTAL_POINTS` / `MAX_NAMES` from `daemon/services/metrics_query/constants.py` (always cited by full path per advisory A1 — the bare `constants.py` collides with the SEALED `daemon/constants.py` that task 2.1 forbids touching). *Rationale:* a duplicated literal silently diverges from the seam and re-creates the M-1 breach class; single-sourcing makes a contract-constant change flow to the tool's clamp automatically. *BINDING on verification:* the 5.2 mutation leg plants a change to the seam constant and asserts the tool's bounds FOLLOW it (a duplicate would keep the tool green while the door changes — the drift class the leg must catch). *Rejected:* module-local copies of the seam values (the M-1 drift class); re-exporting through the tool module (a second name for the same truth). *Fold sites:* phase2 tasks 2.1/2.3/2.4; phase5 task 5.2.

**PL-4 (planner ruling — approver N11 reconciliation): `read_numeric_fact` import path.** The design (TA §A.2 :190) says "imports from the package" but `daemon/services/metrics_query/__init__.py` does NOT export it (`__all__` carries `activation_block`/`effective_interval_s` only). **Ruling: KEEP the DIRECT `envelope`-module import** (task 1.2's acceptance already pins it) and do NOT add `__init__.py` to P1's file list — widening it would break P1's "envelope.py diff = exactly the 1-line promotion" criterion for zero consumer value. TA :190 carries the inline pointer; task 1.2 carries the clarifying clause. *Rejected:* widening `__init__.py` (an unplanned P1 file, a criterion breach, no consumer value); re-exporting from the tool module (a second name).

**Task-level amendment index (no decision content changes — landing is the named task; full disposition in `fold-ledger.md` §E):** **AmA-3** (the holder-set equality leg → phase2 task 2.9) · **AmA-9/AmA-10** (mutation-pack coupling + the decorator-literal pin → phase3 task 3.2 acceptance + this Part's cluster note above) · **AmC-6** (risk rows R13/R14/R15 + the re-rank note → plan-overview §Risk register) · **AmC-7** (the pin fan-out = 15 edit sites + 1 verify-only → phase2 task 2.8) · **AmC-8** (SEALED_SHA_BASELINES no-match + `generate_prompt_artifact_baseline.py --check` as REAL gate legs → phase5 task 5.8 + phase6 task 6.3) · **AmC-9** (post-merge pin-set grep re-derivation artifact → phase5 task 5.7) · **AmC-11** (the D-WATCH-4 asymmetry sentence in CATEGORY_DOC + 3× tools_note + the 5.6 fixture assertion → phase2 tasks 2.1/2.7 + phase5 task 5.6; folded with AmD-WATCH-ii above).

**AmA-5/A-6/A-7 — the auth-matrix pin cluster (deterministic RED + truth-in-pins).** (AmA-5) `tests/unit/metrics/test_metrics_query_auth_matrix.py:114-131` asserts exact set equality `scopes == {"maintenance:read"}` ⇒ **deterministic RED after the re-point**; amend to `frozenset({"maintenance:read", "metrics:read"})` and ADD this file to the D-SCOPE-6 pin list (it was absent — D-SCOPE-6 named only the family-scope + scope-cage sites). (AmA-6) `:133-137` (`test_no_new_scope_is_minted_for_the_door`) and the file docstring `:3-15` become false after the mint ⇒ rename/reword in the SAME commit. (AmA-7) `routers/metrics.py` docstrings still say "no new scope is minted" / name only `maintenance:read` at `:32,:53,:55,:62-63,:180,:190` and in the four OpenAPI 403 descriptions `:153,:564,:609,:677` ⇒ fold docstring + OpenAPI-description updates into task 3.2 (description text only; no wire-shape change). *Consequence:* task 3.5's "original rows unchanged" criterion is FALSE for the amended row — reworded. *Fold sites:* Phase 3 tasks 3.2/3.5; D-SCOPE-6's pin list extended by this amendment.

**AmA-9/A-10 — mutation-pack coupling + the conditional decorator-literal pin.** (AmA-9) `test/packs/metrics_fe_mutation_matrix_test.py:212-231` (FIX2-A5) embeds the FE 403 copy literal naming only `maintenance:read`; if any FE 403 copy is updated, the pack updates IN THE SAME COMMIT or the mutation silently no-ops — the plan's FE fold (Phase 4) touches NO 403 copy, so the standing rule is recorded: any future FE copy change carries the pack fix same-commit. (AmA-10) `tests/unit/metrics/test_metrics_query_scope_cage.py:159-166` asserts the TEXT route's literal decorator substring ⇒ task 3.2's acceptance states the text-route decorator literal stays VERBATIM (the second-constant split for the query routes is the right form). *Fold sites:* Phase 3 task 3.2 acceptance; `fold-ledger.md` §E.

**AmB-12 — the RED-if-copied example string: DERIVED (method + outcome).** Method: the two persona JSON examples (`technical-analysis.md` §A.12) + the §A.9 presentation caveat were scanned against the Phase-5 structural assertions and the pinned literal families (contract-doc pin pack: 6-token error vocabulary, 5-reason availability set; the FE 403 copy literal family; the scope strings; the Phase-5 genericity pin). **Outcome: derivation SUCCEEDED — the offending strings are the caveat examples**, which embed the cause-attribution token "(R-ii)" (§A.12 PERSONA (a) Call 1 `caveats[0]`, PERSONA (b) Call 1 `caveats[0]`, and the §A.9 :573 presentation string). Copied verbatim into the tool or a test assertion, they RED the Phase-5 genericity pin (task 5.2: no `R-i`/`R-ii`/defect-attribution token in any shipped string) and violate D-UX-7 + AmB-13. **Fix (plan-side; the design file carries no content rewrite):** task 5.4 asserts the persona SHAPES with the GENERIC caveat form — the design examples' attribution clause is superseded for copy purposes by the canonical caveat (task 2.2/AmB-13). Availability/error tokens in the examples all check clean against the closed sets.
