# Architecture Recommendation — nest-anomaly-report (board wi-2d691ed2)

Date: 2026-11-05T00:5xZ · Author: Architect (adjudication pass)
Base: `1f6898ae2` (worktree `/home/nea/ensemble-src-wt-nest-anomaly-report`, HEAD == origin/latest)
Method: 3 dispatched evidence workers (data-flow-design, security-design, resilience-design), each
reading the actual code at every seam ruled on below; this file aggregates their findings, applies
the architect's judgment, and RULES. The planner's R1–R9 + F23 stand except where amended here.
Evidence quality note: two workers INDEPENDENTLY converged on one corrected premise (V2/§4 below) —
the TEXT-ISO lexicographic compare is order-safe for the current producer — which raises confidence
in both the correction and the rest of their divergent findings.

---

## 1. Executive verdict

**SOUND-WITH-AMENDMENTS.**

The plan's architecture is correct on every structural question: the per-domain one-door reading
(§2), the two-table write-time clustering (§3), the registry-idiom F23 family (§4), the
non-privileged category + explicit-grant ceremony (§5), and the full-payload release posture (§8).
The plan's evidence discipline is unusually strong (file:line throughout, pre-derived pin
censuses verified correct by an independent worker — N4).

But four load-bearing SAFETY claims do not survive contact with the code, and each requires a
binding fence before this surface ships to ALL agents:

1. **"Rate-limited ⇒ bounded" is FALSE** — the refusal row is itself an unbounded persisted write
   path (one row per refused call, consuming no budget; the sweep is ~288× short of the achievable
   refusal rate). → **AM-9**.
2. **"Scrubbed" is FALSE for the secret classes that actually occur in this repo's transcripts** —
   `POSTGRES_PASSWORD=…`, bare `sk-…` keys, `?api_key=…` query params, bearer tokens, and JSON
   secret dumps ALL survive the composed `redact_exc_str` + exact-match-store pipeline (verified
   empirically against `daemon/_redact.py:74-98` by the security worker). → **AM-11**.
3. **"Per-agent budget" is per agent TYPE** (`agent_id` = the type, `instance.py:3015-3023`,
   `instance_lifecycle.py:2144-2149`) — a 34× ceiling, shared budgets across a type's instances,
   and a noisy-neighbour property the plan never states. → **AM-10**.
4. **"One board item per cluster" bounds duplicates, not distinct clusters** — up to ~480
   global-namespace items/day from one agent type into the operator's newest-first morning board,
   and the item never leaves `todo` (the board has a terminal `done` the plan never reaches). →
   **AM-13 + AM-7**.

Plus one hard mechanical bug: the phase-2 rate-limit COUNTs compare a TEXT column against
`now() - interval` in SQL — **PG has no such operator; the queries error as written**
(→ AM-4).

None of these overturn a ruling; all are foldable. The amendment set below is binding and
mechanical.

---

## 2. Adjudication 1 — ONE-DOOR: the planner's per-domain reading is CORRECT

**Ruling: R8/R2 STAND. The literal "ops read tools consume MetricsQueryProvider ONLY" is
UNSATISFIABLE for row-shaped data and is recorded as survey-vs-ruling discrepancy, exactly as the
planner adjudicated.**

Evidence (all read at base):
- The provider publishes **series only**: exactly three read methods — `families()`
  (`provider.py:211`), `snapshot()` (`:281`), `history()` (`:318`) — and its docstring (`:6-10`)
  says it "reads PUBLISHED registry state only … never touches a database". No row plane exists.
- Rows-as-series is **structurally impossible**, not merely discouraged: a cluster-id label axis
  is unbounded and cannot be enumerated (AM-2 raise, `wiring/registry.py:102-107`), and a
  per-metric cap of 2000 (`checkpoint_metrics.py:129`, enforced `:789/:820`) would saturate and
  drop series.
- The frozen contract is a **document**: `test_central_metrics_contract_doc.py` pins the three
  query routes (`:37-41`, asserted `:147`), the closed availability-reason set (`:44-50`), the
  closed error vocabulary (`:57-64`). A row-plane route would amend the closed route block AND the
  error vocabulary — i.e. it unfreezes the contract.
- **No `schema_version` bump either way**: the contract's AM-7 (`docs/architecture/
  metrics-query-contract.md:83-89`) separates the envelope axis from the vocabulary axis; an
  additive family is a new array element, not a shape change. `QUERY_SCHEMA_VERSION` stays 1.

**The invariant that replaces the literal wording:**

> **One resolution path per (domain, data-shape).** Metric-shaped surfaces resolve through
> `MetricsQueryProvider` and only through it; row-shaped surfaces resolve through their DOMAIN
> SERVICE and only through it. The metrics door publishes a series plane (catalog + snapshot +
> windowed history) and NEVER a row plane. A domain that needs rows gets its OWN route, carrying
> the door's auth posture but none of its data plane.

**Enforcement (AM-1).** The plan's M-3c (engine-handle mutation pin) and REST-scrape negative pin
are the right pins. TWO pins are missing and are required:
- **A metrics-router route-set pin**: `test_metrics_query_scope_cage.py:158-165` iterates the
  router's routes but only *checks* the three named paths — a 4th route is SKIPPED, not rejected.
  The only tripwire is the global declared-route census (`:39`, asserted `:81-92`), which phase 3
  itself bumps 111→113, degrading the diagnostic. **Required**: assert the metrics router's path
  set is exactly `{"/families", "/snapshot", "/series"}` (a 4th route → RED). Fold into the
  phase-3 route pack.
- **Advisory**: a pin that the route and the read tools call the SAME projection function in the
  service (the plan asserts shape equality, which is weaker than single-sourcing the call).

---

## 3. Adjudication 2 — Clustering: blessed in model, amended in five mechanics

The R5 model (write-time assignment, sliding 15-min window from last report, id assigned once,
derived resolution, second table as identity anchor, non-unique key over time) **STANDS** — every
stress test the leader posed resolved inside the model:

- **Clock skew: NON-ISSUE (blessed).** No agent-supplied timestamp is persisted; every timestamp
  is server-side (`_utc_now_iso`, `boot_epoch.py:70-75`, wedge `captured_at`, task-row harvest
  yields ids + server metadata only). The agent supplies only category/description/severity/
  suspected_cause.
- **Recurring hourly incident → N=24 clusters: BLESSED as identity model, AMENDED for
  readability.** A sliding 15-min window cannot distinguish "recurred" from "new" — and WIDENING
  W is REJECTED (W=4h would chain hourly reports into a cluster whose `last_report_at` rolls
  forever ⇒ never resolves under the derived 1h rule). The gauge is fine (only the newest cluster
  is open ⇒ reads ~1). The FE list is the problem (one row, `report_count=1`, no sense of "24×
  today"). **AM-6c**: the FE incident list groups rows by `(tree_root_instance_id, category)`
  and badges an occurrence count — a READ-side affordance over clusters, never a re-assignment.
- **Severity rollup (max-rank, never de-escalates): BLESSED as "peak severity"** — a new cluster
  for the same key starts fresh (confirmed). Every surface MUST label it peak ("critical
  (peak)"). The board interaction is amended (AM-7).

The five mechanical amendments:

**AM-4 (timestamps — fixed-width + Python-bound cutoffs; the phase-2 SQL bug).**
CORRECTED PREMISE (two workers independently): lexicographic ISO compare == chronological for the
current single producer (`'+'` 0x2B < `'.'` 0x2E puts the zero-microsecond form exactly at its
chronological position; all four boundary cases verified). The live hazards are real but
different: (i) **the phase-2 rate COUNTs as written ERROR on PG** — `created_at >= now() -
rate_window_seconds` compares TEXT against timestamptz, no such operator (the house idiom
computes the cutoff in Python and binds it — `notify_audit/repository.py:246-253`); (ii) phase-3's
real hazard is the PG-RENDERED right-hand side (`DateStyle` renders `timestamptz` with a SPACE
separator — the TEXT compare degenerates to "all rows"), which the `::timestamptz` cast correctly
removes; (iii) fragility to producer drift. **Fence**: all anomaly timestamps written
**fixed-width** — `datetime.now(timezone.utc).isoformat(timespec='microseconds')` (always 32
chars, always `+00:00`) in the new models' `_utc_now_iso` (a documented divergence from the
notify_audit sibling — whose shape IS the hazard); ALL cutoffs computed in Python and bound as
parameters; phase-3 keeps the cast as belt-and-braces; pin
`timestamp_shape_is_fixed_width` (regex `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\+00:00$`).

**AM-5 (crash window — one transaction + terminalize, never reassign).** The two-phase write
(INSERT accepted → assign → UPDATE) leaves a durable orphan on crash between INSERT and UPDATE,
and the pseudocode has NO error path around section 3 (a live assignment failure produces the
same orphan). Reassignment is the WRONG repair (post-hoc, the key may hold several clusters —
retroactive identity instability, exactly what R5 rejects). **Fence**: (a) the report INSERT +
cluster INSERT/UPDATE + report→cluster UPDATE execute as ONE transaction inside ONE repository
unit-of-work (one `db_offload` call; the service lock wraps the unit call; house precedent
`board_service.py:186-200`); (b) defense-in-depth: any row `outcome='accepted' AND cluster_id IS
NULL` is an ORPHAN and is TERMINALIZED (new outcome token `unclustered` — the notify_audit N4
`delivery_unknown` analog) by a bounded age-gated maintenance sweep, NEVER reassigned; a live
assignment failure writes the terminal outcome directly. With (b), `accepted` MEANS clustered —
which also repairs the visibility split (the metric counts accepted; the route serves by
cluster_id; an orphan made both disagree). **Pins**: `no_orphan_accepted_rows`,
`orphan_terminalized_not_reassigned` (mutation: reassign → red). Outcome vocabulary becomes
`accepted | rate_limited | persist-failed | unclustered` (phase-1 model spec + phase-3
accepted-only filters unchanged and now exactly right).

**AM-6 (the report_count invariant + retention interaction).** The invariant is
`report_count == COUNT(anomaly_reports WHERE cluster_id=:id AND outcome='accepted')` **per
cluster** — and the plan's retention breaks it: reports sweep by `created_at`, clusters by
`last_report_at`, so a chronic cluster (lifetime > 30d) keeps its newest member while older
members are deleted ⇒ the stored count permanently over-counts the surviving rows — and the
plan's own refuse-vs-merge defense RESTS on this invariant. **Fence**: the retention sweep
DECREMENTS `report_count` by the deleted-member count per cluster (GROUP BY over each batch's
deleted ids, then UPDATE) — with decrement the invariant holds absolutely. **Pins**:
`report_count_equals_member_rows` (per cluster, both directions),
`no_orphan_accepted_rows` (AM-5), `retention_decrements_counts`. The phase-2 pin table currently
has NONE of the three (the `n_agents_one_incident` pin is the happy path only).

**AM-7 (peak-severity labeling + board item lifecycle).** Every read surface, the metric `help`,
and the FE chip label `max_severity` as **peak**. The board item created by a critical cluster
currently NEVER leaves status `todo` (`board_service.py:189` default; the plan never transitions;
`done` exists at `:239/:261-263/:370-376`) ⇒ a monotone open-item count on the operator's board.
**Fence**: the anomaly maintenance job ALSO transitions board items to `done` once their cluster
is resolved (`last_report_at < now − resolve_window`) via `update_item(status=…)` — a BOARD-row
write only, never a cluster write (R5 forbids cluster status). Pin
`board_item_closes_when_cluster_resolves`. If this is de-scoped from phase 2, the monotone
open-item count MUST be a named morning-report disclosure.

**AM-14 (lock shape).** The single process-global `asyncio.Lock` held across board I/O serializes
EVERY anomaly assignment daemon-wide (a critical storm = a global choke point). **Fence**: per-key
locking via a fixed 64-slot stripe array (index = hash(cluster_key_hash) % 64) — bounded memory,
same-key serialization fully preserved (the double-create board race stays closed because the
board write stays inside the same-key stripe), cross-key contention eliminated. Disclose the
offload-gate (25-slot, strict admission invariant) as a degradation source in CATEGORY_DOC
(gate refusal ⇒ fields NULL, report still accepted).

---

## 4. Adjudication 3 — The F23 metrics seam: legitimate, with pin-set holes closed

The family's shape is **legitimate per the pinned discipline**, verified end to end:

- **Pull adapter (BLESS).** `adapt_f23` conforms exactly to the `adapt_f3` shape
  (`wiring/w1.py:477-500`): `current_sources()` → `dsn is None` early-return → `db.one_shot`
  (closes in `finally`, `wiring/db.py:102-137` — one-shot per tick, NEVER a pool) →
  `fetch_rows` (failing query = rate-limited WARNING, never a dead tick, `db.py:139-158`) →
  `int()` guards → fold → `.set()`. The "wiring never imports sqlalchemy" pin
  (`test_metrics_family_adjudication.py:252-258`) makes raw SQL MANDATORY. **Multi-statement
  adapter precedent confirmed** (`wiring/w2.py:733-734`, two `fetch_rows` on one `one_shot`) —
  three statements in one adapter conform. `.set()` on a counter is house-legal
  (`checkpoint_metrics.py:804-833`; the non-monotonic stored-row-count counter precedent is exact:
  `ensemble_wedge_capture_total`, `w1.py:158-173`).
- **R_i (BLESS — the plan's claim is TRUE, mechanism traced).** `derive_availability`
  (`availability.py:65-98`) enters the census branch ONLY for `source_idiom == "postgres_census"`
  (`:76-92`) and the capture branch ONLY for `"wedge_capture"` (`:93-95`); the registry idiom's
  ONLY reachable dark reason is `series_absent` (`:96-97`). R_i (the inverted
  `CensusSampler.gated` polarity, `metrics_census.py:322-335`/`:347-355`) reaches the provider
  only via `provider.py:143-148` and can only re-label census-idiom families. F23's SAMPLING is
  also ungated (`api.py:2540-2542`). Disclose R_i/R_ii in the ceremony text; do not fix (routed
  lanes own them).
- **Cardinality (BLESS the arithmetic; two fences).** 13 + 39 + 13 = 65 series; global closed
  space goes 425 → **490** against `GLOBAL_SERIES_CAP = 2000` — no cap risk; per-metric 2000 vs
  13/39/13 negligible. Fences: (a) the F23 band (65) has **zero headroom** — it equals the
  measured value exactly, so ANY future vocabulary extension reds it: state this in the code
  comment and re-declare the band with the vocabulary (R1's later lane); (b) the band check
  silently SKIPS a registered family with no band entry — ADD pin
  `set(FAMILY_SERIES_BANDS) ⊇ set(REGISTERED_FAMILIES)` in `test_metrics_family_adjudication.py`.
- **Chart-primary roster (BLESS the mechanism; the plan's pin set is one-third).** The chain is
  verified: `chart_role` derives SOLELY from `roster_role_for(family, name)`
  (`wiring/registry.py:116` — the registration signature has NO chart_role parameter) →
  non-roster = stat-primary (`checkpoint_metrics.py:662-669`) → family role chart iff any member
  chart-primary (`provider.py:249-252`) → the FE skips non-chart families
  (`central-tab.component.ts:513-529`). F23 outside the roster renders NO trend panel — the plan
  is right. Roles right: counters STEP, gauge LINE (`roster.py:22-24`; FE matches,
  `metrics.model.ts:277-279`). **Fence**: roster coverage is per (family, NAME), and
  `assert_roster_is_well_formed` (`roster.py:228-253`) never checks that every registered metric
  of a roster family HAS a roster entry — a typo in one series name silently degrades that one
  metric to stat-primary inside a chart family, invisible on the wire. The phase-3 pack must
  assert **all three** F23 series' roles (not one), plus a general "every registered metric of a
  roster family has a roster entry" pin. (Do NOT "fix" `RosterFamily.chart_role` returning the
  first series' mode — it is inert.)
- **AM-2d (fold semantics — let the registry fold).** The plan's adapter pre-folds out-of-vocab
  tokens to `other` BEFORE `.set()` — but `other` IS a vocabulary member, so the registry's own
  invalid-label counter (`ensemble_metrics_invalid_label_total`,
  `checkpoint_metrics.py:706-720`) can NEVER fire: a folded category is indistinguishable from a
  genuine `other`, and future vocabulary drift (a category removed; old rows carry a now-invalid
  value) is silently absorbed. **Fence**: the adapter passes the RAW stored token to `.set()` and
  lets the registry fold — identical output, and the counter becomes a real drift signal at zero
  cost. (This is exactly how F3 works — `w1.py:495-497`.)

**AM-3 (VOCABULARY_VERSION 3→4→5 — sites the plan missed; NO compat concern).** No FE compat
issue: the FE types `vocabulary_version: number` and a SPEC PINS that it must never be a literal
(`metrics.model.ts:20-21/:140`, `metrics.model.spec.ts:177-179`); the FE renders it dynamically.
`schema_version` does NOT move (AM-7 axes independent, pinned
`test_metrics_vocabulary_amendment.py:91-99`). Missing from the plan's task-5 table — all
binding: `test_metrics_family_adjudication.py:419` (`assert cm.VOCABULARY_VERSION == 3` literal +
its `:401-418` history docstring), `test_metrics_vocabulary_amendment.py:26` (same literal +
`:17-25` prose), `daemon/checkpoint_metrics.py:200-217` (the VOCABULARY_VERSION docstring history
block — the plan names only the constant at `:218`), and re-run
`tests/helpers/metrics_exposition_baseline.py` after each bump (it renders the live value).

**Artifact question settled (plan NOT gapped):** `.agents/shared/metrics/tool-categories.json`
carries `{categories, count, vocabulary_version, reserved, source}` derived from
`registry.CATEGORY_MODULES` + AST-scanned `@register_tool_category` call sites — **ZERO per-agent
grant information** (`test_tool_category_artifact.py:190-209`). Phase 5's 40-meta.json grant
ceremony requires **NO further artifact regen**. Phase 1 (57→58 @ v4) and phase 3 (58→59 @ v5)
regens remain mandatory, each in the SAME commit as its bump.

---

## 5. Adjudication 4 — All-agents write surface: sound shape, four fences made binding

The SHAPE is right: attribution is factory-captured and **unspoofable** (no agent/instance
parameter in the signature; `created_by_instance` caller-stamped by the service — a caller cannot
spoof identity); the harvest is self-scoped; the board sink is server-side; XSS posture is safe
by default (zero `innerHTML`/`bypassSecurityTrust` in the metrics FE family; the board renders
server-side with escaping — `board_view_pages.py:309/:91-97`); the category is deliberately
non-privileged so the 17-entry privileged set stays frozen. What does not survive:

**AM-9 (flood fences — CRITICAL).** The refusal row is itself an unbounded write path: one
persisted row per refused call, no budget consumption, and the retention sweep cannot drain a
fresh flood (≤5,000 rows/tick/day vs 60,000/hour achievable; it also only deletes >30d-old rows).
**Fences (all in `NestReportConfig` + `anomaly_service`):**
1. `refusal_ledger_max_per_agent` (default **10** / 10 min): beyond it, the SAME `rate-limited`
   envelope returns with **NO ledger row** — the signal moves to the
   `ensemble_anomaly_reports_rate_limited_total` counter (the metric is the scalable signal; the
   ledger is for morning review, and 10 rows/10min is ample for review).
2. `max_rows_global` (default **50,000**) and `max_rows_per_tree_root` (default **2,000**),
   checked PRE-INSERT (served by `idx_anomaly_reports_outcome_created` /
   `idx_anomaly_reports_tree_root_created`) → new refusal token `capacity-exhausted` (no ledger
   row).
3. `max_accepted_per_hour_global` (default **5,000**), enforced pre-harvest.
4. The retention job gains a **capacity trim** (oldest-first beyond `max_rows_global`, batched)
   alongside the age sweep — with the insert gate this is self-stabilizing (once at ceiling,
   inserts refuse ⇒ inflow stops ⇒ the trim converges).
5. Pins: the unbounded-refusal-loop leg (N calls ⇒ rows ≤ ledger cap) and a global-cap leg.

**AM-10 (budget keying — disclose, do not change).** `agent_id` is the agent **TYPE**
(`instance.py:3015-3023` docstring; threaded `instance_lifecycle.py:2144-2149/:4846-4851`;
`[v2]` mirrors resolve to the base id via `version_tag`; custom agents exist only via an
auth-gated REST route — the key space is NOT agent-expandable). Consequences: spawning does NOT
multiply the budget (good — the fan-out attack is structurally dead); a type's instances SHARE
one budget (noisy neighbour); a nest-wide critical burst past 20/hour/type is still VISIBLE (the
cluster exists, the rate-limited counter spikes — which is itself a nest-distress signal).
**Ruling**: KEEP the type key. The `(agent_id, tree_root)` alternative is REJECTED — it
multiplies the global ceiling by tree count (an orchestrator spawning trees re-opens the flood).
CATEGORY_DOC + `NestReportConfig` field docs MUST state the sharing explicitly. The critical cap
stays 20/hour default (knob exists for operators).

**AM-11 (scrub hardening — CRITICAL, empirically grounded).** Verified surviving the composed
pipeline (`redact_exc_str` + exact-match store scrub): `POSTGRES_PASSWORD=hunter2 …` (the KV-DSN
regex's `\b` fails after `POSTGRES_`), bare `OPENAI_API_KEY=sk-…`, URL **query-param** secrets
(the netloc masker stops at the first `/`), JSON secret dumps, bearer tokens. The exact-match
scrubber knows only STORED secrets — the daemon's runtime creds are invisible to it; the
scrubber-absent path is fail-open identity (`secret_persistence_scrub.py:1270-1283`). The excerpt
harvests raw tool stdout (`bash.py:1152` returns `STDOUT:` verbatim, 150k cap). **Fences:**
1. NEW additive function `redact_secret_shapes(text, limit)` in `daemon/_redact.py` — mask
   `sk-[A-Za-z0-9_\-]{8,}`, `Bearer\s+\S+`,
   `(?i)(api[_-]?key|token|password|secret|access[_-]?key)\s*[:=]\s*\S+`,
   `(?i)\?[^\s]*(key|token|secret)=[^&\s]+`, and env-var shapes `*_API_KEY=`/`*_TOKEN=`/
   `*_PASSWORD=`/`*_SECRET=`. Applied to description/suspected_cause/excerpt BEFORE
   `redact_exc_str`. **`redact_exc_str` itself is UNTOUCHED** (its callers are pinned).
2. The EXCERPT column flips **fail-CLOSED** on scrubber-absent (persist NULL + WARN);
   description/suspected_cause keep the fail-open shape (the pattern masker is the always-on
   floor) — the disclosure in CATEGORY_DOC is updated accordingly.
3. Excerpt is persisted for **major + critical ONLY** (AM-8e: for minor, the transcript read is
   skipped entirely — no harvest, no column, no cost).
4. The planted-secret pin plants a **NON-STORED** shape (e.g. `sk-proj-…` inside a transcript
   tool message) — a `secret_store`-planted value is satisfied by the exact-match path and
   proves nothing about the real risk. Mutation legs per new pattern.

**AM-12 (projection + injection pins).** (a) The LIST projection (`list_clusters` +
`/clusters`) EXCLUDES `excerpt` entirely — pin: the list payload has no `excerpt` key (mutation:
add it → red). (b) `excerpt` appears ONLY in the detail projection behind an explicit
`?include_excerpt=true`. (c) The FE renders the excerpt ≤300 chars with a reveal affordance,
never raw (spec-pinned). (d) The FE pack asserts the new tab's template contains NO
`innerHTML`/`bypassSecurityTrust` (mutation leg: inject → red) — the safety is currently
incidental, make it a pinned property. (e) `top_description` (the board body) is DETERMINISTIC:
highest severity, tie-break `created_at ASC, id ASC` — pinned; the board body carries a fixed
prefix line "auto-filed — unverified agent prose" so an operator cannot mistake agent prose for
a system assertion.

**AM-13 (board ceilings).** Distinct clusters defeat one-item-per-cluster: 13 categories × fresh
15-min windows × 20 criticals/hour/type ⇒ up to ~480 items/day/type into the GLOBAL, newest-first,
retention-free board (`board_service.py:399` ordering; `board_view_service.py:94` render limit
50 — attacker items push genuine items below the fold). **Fences**: `max_board_items_per_tree_root_per_hour`
(default **3**) and `max_board_items_per_hour_global` (default **50**); beyond a ceiling,
`ensure_board_surface` appends a NOTE to the tree's most recent anomaly board item instead of
creating an item, + counter `ensemble_anomaly_board_items_suppressed_total`. The board TITLE
carries the cluster_id (operator-distinguishable machine rows). The single-reporter-HOLD
mechanism is REJECTED (it needs a second timing authority — the exact class R6 rejected); the
ceilings + note-append preserve immediacy without timers. `MAX_NOTES_PER_ITEM=20` truncation
(`board_view_service.py:108/:255-259`) is DISCLOSED and routed (board-view lane: keep-latest).

**AM-16 (default-ON posture — conditional).** `ENSEMBLE_NEST_REPORT_ENABLED` stays default
**True** (the commission's "all agents" + the duty prompt require it; default-OFF would make the
phase-5 duty line instruct every agent to call a tool that refuses) — **CONDITIONAL on AM-9,
AM-11, AM-12, AM-13 landing in this lane** (they are binding). ADD a per-writer deny list
`ENSEMBLE_NEST_REPORT_DISABLED_AGENTS` (comma-separated agent-type ids, checked pre-rate-gate →
`tool-disabled` for that writer only; restart-required, documented) — the surgical lever the
whole-surface kill-switch lacks. If ANY of those four AMs is de-scoped, the default flips to
**False** and the promote disclosure says so.

---

## 6. Adjudication 5 — Evidence-harvest cost: one seam swap, honest ceilings

**AM-8 (harvest seams — binding):**
- **(a) The transcript seam is WRONG in the plan.** `daemon/persistence.py:627
  get_instance_messages` is full-read-then-full-serialize: `saver.aget` (`:703`) materializes the
  ENTIRE checkpoint state, the loop (`:844-869`) serializes EVERY message, there is NO limit
  parameter — and passing `manager` additionally reconstructs the FULL SYSTEM PROMPT off-thread
  (`:920-960`), pure waste for an excerpt; passing `manager=None` logs a WARNING per call. This
  is adjacent to the repo's O(N²)-walk defect family. **Fence**: use the bounded
  `instance_read.py:889-909` pattern — `raw_saver.aget(...)` + `messages[-N:]` (N ≤ 20,
  `instance_read.py:154` precedent) + per-message truncation. The read cap is enforced AT THE
  READ SEAM; mutation leg M-1g (remove the cap) must red against the seam, not a post-read trim.
- **(b)** `families()` (registry snapshot) is metadata-only and cheap (published-reference reads,
  no payload copies — the plan's "payloads NEVER copied" claim HOLDS, `provider.py:211` /
  `checkpoint_metrics.py:1067-1069`) but takes the REGISTRY-LEVEL lock and must be called
  OUTSIDE any offload worker (reentrancy guard, `db_offload.py:379-385`).
- **(c)** Corrected ceiling (the plan's "≤4 off-loop DB round-trips" is dishonest): **5 off-loop
  CALLS** — the ancestor walk is **O(chain-depth ≤256 SEQUENTIAL PK reads** inside one call,
  `instance/repository.py:1217-1235`, not 1 round trip — disclose; `list_recent(limit=50)` is one
  index-served query; `boot_epoch` is O(1) in-RAM.
- **(d)** `boot_epoch` shape pin: `get_boot_epoch()` returns NAIVE-UTC (no offset,
  `boot_epoch.py:70-75`); the wedge subsystem itself has TWO shapes (watchdog naive
  `wedge_capture/watchdog.py:591`; boot-record `isoformat()+'Z'`, `boot_record.py:122-133`). Pin
  the anomaly-side `boot_epoch` to the naive-UTC form (the watchdog writer's shape) and NEVER
  compare it against `+00:00`/`Z`-shaped values — the plan's "same boot_epoch" wedge match is
  format-fragile as written.

---

## 7. Adjudication 6 — Commit choreography: an ordered atomic series, NEVER one giant commit

**Ruling (AM-17):** the lane lands as **nine ordered commits**, each leaving the tree GREEN
(boot-scan, drift tests, privileged-set pins, artifact test) at its boundary:

| # | Commit | Atomic because |
|---|--------|----------------|
| C1 | P1 code: models + migrations + `NestReportConfig` + vocab constants + service + repository | code exists, nothing registers — all green |
| C2 | P1 registration: tool + `_tool_registry` + `instance.py` extend + loader + **artifact regen + VOCAB 3→4** | the SAME-PR class: the AST scan finds the decorator site the moment the tool file lands; the artifact embeds version+set — all in ONE commit or the drift test reds |
| C3 | P2 service: clustering + rate limits + board sink + retention + manager registration | one behavioral unit |
| C4 | P3 F23: family + roster 12→13 + adjudication census + packs | registering without the census verdict reds the two-sided census pin; roster without pack reds the count pin |
| C5 | P3 `nest-report-read`: category + **ALL 18 privileged-set sites** + 3 meta.json + artifact + VOCAB 4→5 | the SAME-PR rule (`_tool_registry.py:324-366`) — a missed site is red BETWEEN commits |
| C6 | P3 route: `/api/nest-reports/*` + the two router cages | an undeclared mount reds both cages loudly |
| C7 | P4 FE: tab + specs + bundle-guard extension | the guard and the tab co-land |
| C8 | P5a duty: 41 prompt `.md` + baseline re-stamp + 17 seal re-pins | the baseline row and the file content it pins must never disagree across a boundary (house rule, ari-voice precedent) |
| C9 | P5b grants: 40 meta.json + the 3 allow-array pins + sketcher doc amendment | the grant-completeness walk is all-or-nothing |

The 81-file single ceremony commit is **REJECTED** (bisect-hostile, and it is unnecessary: the
duty pin and the grant pin are independent — two green boundaries beat one giant commit). Two
facts make the series safe (both code-verified): the tool-categories artifact carries **ZERO
per-agent grant info** (C9 needs no regen — §4), and the prompt-artifact baseline covers **ONLY
the versioned `.md` artifacts** (`prompt_artifacts.py:119-140` — `meta.json` is not in the set),
so C5's three meta.json edits need **NO** re-stamp. **Per-commit smoke set (binding)**: after
every commit, `test_frozen_tool_name_discovery` + `test_tool_category_artifact` + the
privileged-set pins + a boot-scan must be green — the ladder between commits.

---

## 8. Adjudication 7 — Phase coupling: the plan's order is CORRECT, no fusion

Verified against the coupling map and the code facts above:
- **P3 after P1+P2** — confirmed (P3's allowed_values source is P1's `vocab.py`; F23's aggregate
  reads P2's cluster columns; the read tools need the service's read half which extends P1/P2
  substrate). The tools' degraded shape (`anomaly-unavailable`) means P3 could REGISTER before
  tables exist, but its packs could not pass — keep the order.
- **P4 after P3** — confirmed (the tab consumes P3's route + family; FE compiles against string
  paths but the spec and data need P3).
- **P5 after P1** (the tool exists) and **P5's appends after P3's 3 meta.json edits** — already
  ruled by the trailing-entry pin; consistent.
- **The VOCAB double-bump** (P1: 3→4; P3: 4→5) is ordered by a hard dependency (P3 imports P1's
  vocabulary) — two commits, never one 3→5 jump. Confirmed.
- Phase 6's A12 gate expectations: final artifact = **59 categories @ vocabulary_version 5**;
  agents baseline 161/161 re-stamped exactly once (C8). No re-stamp from C5/C9 (baseline covers
  `.md` only — §7).
- One factual update for phase 6: with AM-5's new outcome token, the both-directions script's A13
  end-to-end leg and the retention/rate packs must cover `unclustered` rows (they are excluded
  from `accepted`-filtered counts — assert it).

No re-order, no fusion.

---

## 9. Adjudication 8 — Release posture: blessed with the standing-rule template re-verified at stage

- **Full payload (agents/ + FE + daemon) — CONFIRMED.** The 41 duty files change
  `agents_tree_sha256`; the tab changes `frontend_tree_sha256`; daemon-only is NOT achievable
  (R-13 stands). Manifest deltas to declare at stage: version fields, binary, agents tree,
  frontend tree, `known_schema_gen` (+2 head), `staged_at`.
- **Migrations: +2 additive `CREATE TABLE IF NOT EXISTS`, zero FK, one per table, DOWN present
  but never executed (zero production callers; runner SQLite-only; PG arrives via create_all).
  `rollback_safe=true, source=explicit` — the standing-rule semantic adjudication in
  `phase6-plan.md` §(e) is CORRECT in shape; the implementer re-runs the old-tree reference
  greps (`git grep anomaly_reports / anomaly_clusters` at the previous release tag = zero) at
  stage time and records them in the journal + tag annotation. Consequence class: FIX-LOSS, not
  incompatibility — auto-rollback may fire safely.
- **VOCABULARY_VERSION 3→5 across the lane: NO consumer-side compat concern** (FE types it as
  number and PINS that; `schema_version` does not move; the exposition baseline re-renders from
  the live gauge — re-run, no edit). The four missed pin/prose sites are AM-3.
- **known_schema_gen** = the lane's lexically-later `.sql` (the 2nd file) — as planned.
- **DB-masking caveat (phase 5 R-01) — fresh census carried**: `prompt_versions` has ZERO
  approved+selected rows anywhere and ZERO `rule` rows of any state — **no planned rule.md edit
  can be shadowed**. ONE latent caveat: `(reviewer, workflow)` carries a `pending_approval` v2
  since 2026-10-19 — it shadows ONLY if the lane edits `reviewer/workflow.md` AND that row is
  later approved. The phase-5 census step + morning-report disclosure (already planned) covers
  it; no new fence.
- **Ceremony command shape** unchanged (staging only; promote is the user's).

---

## 10. New risks the plan missed (routed, with owners)

| # | Risk | Owner |
|---|------|-------|
| NR-1 | Refusal-ledger flood (AM-9) — unbounded persisted rows per refused call; sweep 288× short | THIS lane (binding AM-9) |
| NR-2 | Secret shapes surviving the composed scrub (AM-11) — empirically verified class list | THIS lane (binding AM-11); the underlying nomask/env-inherit gaps stay routed to the standing improve-lane |
| NR-3 | Per-TYPE budget semantics unstated (AM-10) — noisy neighbour + 34× ceiling | THIS lane (docs; key unchanged) |
| NR-4 | Board-item pollution + never-closing items (AM-13/AM-7) — up to ~480 items/day/type global; monotone `todo` count | THIS lane (binding AM-13/AM-7); `MAX_NOTES_PER_ITEM` keep-latest → board-view lane |
| NR-5 | Phase-2 rate-limit SQL not executable as written (AM-4) — TEXT vs `now()-interval` operator error | THIS lane (binding AM-4) |
| NR-6 | Chronic cluster breaks `report_count` under retention (AM-6) — stored rollup outlives swept members | THIS lane (binding AM-6) |
| NR-7 | Idle-gated maintenance starvation — the 24h retention NEVER runs on a continuously busy nest (the checkpoint-cleanup class; `maintenance.py:258-262`, `:326-395`) | Disclosed (AM-15) + routed to the maintenance lane (the class is systemic) |
| NR-8 | Process-global cluster lock held across board I/O (AM-14) — global choke point under a critical storm | THIS lane (binding AM-14) |
| NR-9 | `boot_epoch` shape mixing across writers (AM-8d) — the wedge match is format-fragile | THIS lane (binding AM-8d) |
| NR-10 | Offload-gate pressure — ~5 offloads/report against the 25-slot executor; burst ⇒ NULL evidence fields | Disclosed in CATEGORY_DOC (AM-14); systemic pressure → the DB-pool/offload lane |
| NR-11 | `monotonic:true` on non-monotonic stored-row counters (F23's two counters; house-wide — F3 precedent) — machine-readable field contradicts documented semantics; FE does not consume it | Disclose in the F23 family pack (AM-2f); general fix → central-metrics lane |
| NR-12 | Contract-doc vocabulary prose already stale ("VOCABULARY_VERSION == 2 FROZEN"; live 3, lane makes 5) — no pin breaks, the doc misstates | metrics-contract doc lane |
| NR-13 | Zero-headroom F23 band + the bands⊇registered pin hole (AM-2a/b) | THIS lane (binding AM-2) |
| NR-14 | `series_absent` is also what a DRIFTED adapter SQL produces — indistinguishable on the wire; only the rate-limited WARNING discriminates | Covered binding by the seeded-row PUBLISH pack leg (§4) — keep it a named pack leg, not an exit-line note |

---

## 11. Decisions pending (leader/user)

1. **AM-16 default-ON conditionality** — the default stays True iff AM-9/11/12/13 land. If the
   leader prefers default-OFF-first-release as posture policy regardless, flip it — one field
   default + one disclosure line; no architecture change.
2. **(reviewer, workflow) pending-approval row** — user-owned disposition per plan R-01 (the
   fresh census shows it is the ONLY masking-capable row, and only if reviewer's canonical duty
   home is workflow.md and the row is later approved). No lane action beyond the planned census
   step.

## 12. Open questions

None blocking. All eight adjudication points are ruled above with binding, mechanically foldable
amendments (AM-1 … AM-17; AM-18 records the phase-order confirmation). Gaps: none — all three
evidence workers returned complete reports with anchors; no node was re-dispatched.
