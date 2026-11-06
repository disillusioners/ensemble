# decisions.md — Ruling SSOT for nest-anomaly-report (wi-2d691ed2)

Date: 2026-11-04T22:30Z
Author: plan-creation worker
Status: Binding — phases 1-6 conform exactly; changes require a new leader ruling, not an
edit in flight. Every ruling carries: the decision, the justification, the evidence anchor
(verified in worktree `/home/nea/ensemble-src-wt-nest-anomaly-report` @ base `1f6898ae2`),
and what was REJECTED.

---

## R1 — Category: closed canonical vocabulary, raw preserved

**Decision.** `report_anomaly` takes `category: str` REQUIRED; free text accepted at the
tool boundary; normalized (lowercase → strip whitespace → exact-match-or-synonym-fold)
into a CLOSED canonical vocabulary; no match folds to the reserved `other`. The raw input
is preserved verbatim (≤64 chars, truncated) as `category_raw` on the row. The metric label
carries the CANONICAL value only. Initial vocabulary (≤15, defined in
`daemon/services/metrics_query/wiring/vocab.py` beside `WEDGE_REASONS` at `:66`
(`__all__` block at `:296`, WEDGE_REASONS member `:318`; `RESERVED = "other"` at `:36` —
advisory 2, fold round 3): `tool-failure, performance, resource-exhaustion, loop-stall,
wedge-suspected, data-corruption, auth-permission, external-dependency, mcp-failure,
llm-provider, queue-scheduling, environment-config` + reserved `other`. Vocabulary
extension is a LATER lane (vocab.py edit + VOCABULARY_VERSION bump), never this one.

**Justification.** AM-2 in the metrics contract structurally REQUIRES an allowed_values set
per label key: `daemon/services/metrics_query/wiring/registry.py:102-110` raises
`ValueError` ("a key without one is char-checked only and can reach the per-metric cap with
raw values"). A free-text label is `open` cardinality (`labels: null`, unbounded) — a
dishonest charted metric. Folding at WRITE time (not query time) means the stored canonical
value is already chart-legal and phase 3's family registration passes AM-2 without a
translation layer.

**Evidence.** `wiring/registry.py:102-110` (AM-2 raise, read verbatim); `wiring/vocab.py:66`
(WEDGE_REASONS precedent — the closed-set home).

**Rejected.**
- *Free-text categories stored as-is:* open-cardinality labels; would either fail AM-2
  loudly at F23 registration (phase 3 blocked) or force a query-time normalization that
  re-folds historical rows differently as vocab evolves — dishonest charts either way.
- *Refusing unknown categories:* punishes honest reporters for vocab gaps and suppresses
  exactly the novel incidents the tool exists to catch; fold-to-`other` keeps the report
  while keeping the metric closed.

---

## R2 — FE: central tab, custom body, DOMAIN REST route for the incident list

**Decision.** The FE incident list is a CENTRAL TAB on the existing metrics page frame
(custom body, not the generic chart renderer), fed by a NEW domain REST route
`/api/nest-reports/*` — a DOMAIN route, not a second metrics query path. Full
specification is authored by the phase 4 sibling in `phase4-plan.md`; this entry records
the ruling and cross-references it.

**Justification.** Row-shaped incident lists (cluster id, counts, severity, member agents)
are not time-series; forcing them through the metrics query door would abuse the frozen
series contract. A domain route keeps the metrics door FROZEN while reusing its auth
posture (identical gating, see R8). Central tab + custom body reuses the page frame
(range selector, door-state handling) without pretending anomalies are charts.

**Evidence.** Metrics door posture to mirror: `daemon/routers/metrics.py:104-115`
(`_SCOPE_READ = "maintenance:read"` `:106` + `_SCOPE_READ_METRICS = "metrics:read"` `:115`
— the QUERY-route any-of set; the TEXT-exposition route keeps `maintenance:read` ALONE and
is NOT our model), `_ERR_AUTH_NOT_CONFIGURED`, fail-closed 503.

**Rejected.** *FE reading the anomaly service directly over a second metrics query path:*
splits the door contract and unfreezes what v0.28.6 froze. *Generic chart tab with series
shoehorning:* dishonest rendering of row-shaped data.

---

## R3 — Severity: closed 3-tier; critical gates to the BOARD server-side; notify NEVER

**Decision.** `severity: str` optional arg, DEFAULT `"major"`, closed 3-tier
`minor | major | critical` (normalize lowercase/strip; out-of-set → typed `invalid-input`
refusal — severity drives board surfacing, so silently folding `critical`→`major` would
HIDE a nest-level event). Definitions: minor = annoyance, work continues; major = work
materially impeded or quality at risk; critical = nest-level, multiple trees/agents
impacted or integrity at risk, needs Ari/user at the next review.

CRITICAL GATING: on a critical report, the anomaly service writes the board row
SERVER-SIDE via `BoardService` (tool-internal, like the notify adapter seam) — agents
NEVER get board tools through this lane. ONE board item per CLUSTER: the first critical
creates it; later criticals on the same cluster APPEND a board note (with the updated
count), never duplicate items. Title carries the anomaly category + severity; body carries
cluster id + counts + top description; tags suggest category + severity;
`created_by_instance` = the REPORTING instance (stamped by the service — BoardService
already requires the caller to stamp it, `daemon/services/board_service.py:136`).
`notify_user` is NEVER called by this path.

**Justification.** Board tool categories are ari-only for AGENTS since the v0.26.2 ruling —
granting every reporter board write tools would be a surface regression. The service-side
seam keeps the agent surface to exactly one new tool. The doctrine is explicit:
`daemon/tools/notify_user.py:32-39` — "The board is the ALWAYS surface; notify is the MAY
voice… Nothing auto-sends." Anomaly reports are machine-generated context, not a
deliberate voice: board ALWAYS, notify NEVER here. One-item-per-cluster prevents a
critical storm from flooding the global board namespace (`project_id` NULL rows are
global).

**Evidence.** `notify_user.py:32-39` (doctrine, read verbatim);
`daemon/models/board.py:208/301/350` (BoardItem/BoardNote/BoardItemTag);
`board_service.py:136 create_item` (title 1-512 validated; tags deduped in-service;
`created_by_instance` caller-stamped), `:219 update_item(note=...)` appends the note in
the SAME transaction (`:297 _append_note`).

**Rejected.**
- *Auto-calling notify_user on critical:* violates the doctrine (notify is a deliberate
  opt-in voice, ari-only, N=1/turn ≥60s, M=6/hour/destination — none of which a machine
  trigger satisfies); board is the guaranteed surface.
- *One board item per critical report:* duplicates drown the board and break the
  cluster-as-incident mental model; note-append carries the count instead.
- *Folding unknown severity to major:* would silently demote a critical.

**Amendment trail (fold round 2 — `architecture-recommendation.md`, SOUND-WITH-AMENDMENTS; appended, history intact).**
- **AM-7** — `max_severity` is labeled **peak** on every read surface, the metric help, and the FE chip ("critical (peak)"); the anomaly maintenance job ALSO transitions the cluster's board item to `done` once the cluster resolves (`last_report_at < now − resolve_window`) via `update_item(status=…)` — a BOARD-row write only, NEVER a cluster write (R5 forbids cluster status); pin `board_item_closes_when_cluster_resolves`; if de-scoped, the monotone open-item count is a named morning-report disclosure. (Un-amended, the item never leaves `todo` — `board_service.py:189` default; `done` exists at `:239/:261-263/:370-376`.)
- **AM-13** — board ceilings: `max_board_items_per_tree_root_per_hour` (default **3**) + `max_board_items_per_hour_global` (default **20** — **W-6 correction, fold round 3: the architect-specified 50/h ⇒ 1,200/day EXCEEDED the architect's own harm estimate of ~480/day (arch-rec §5 AM-13); 20/h ⇒ 480/day and a uniform multi-tree flood at 20/h/type now trips the ceiling**); beyond a ceiling `ensure_board_surface` appends a NOTE to the tree's most recent anomaly board item instead of creating one + counter `ensemble_anomaly_board_items_suppressed_total`; the board TITLE carries the cluster_id (operator-distinguishable machine rows); single-reporter-HOLD REJECTED (needs a second timing authority — the exact class R6 rejected); `MAX_NOTES_PER_ITEM=20` truncation (`board_view_service.py:108/:255-259`) DISCLOSED and routed to the board-view lane (keep-latest). (Without ceilings: up to ~480 items/day/type into the GLOBAL newest-first retention-free board, pushing genuine items below the 50-render fold.)

---

## R4 — Write-tool category/grant: `nest-report`, NON-privileged; grant = phase-5 ceremony

**Decision.** New tool category `nest-report` (kebab-case). It is NON-privileged: it is
NOT added to `PRIVILEGED_TOOL_CATEGORIES` (the frozenset in
`daemon/tools/_tool_registry.py`; metrics-read is entry #17 per the `instance.py:6624-6663`
comment — that set and its pin sites stay UNTOUCHED). Grant path = explicit `tools.allow`
entry appended in ALL in-repo meta.json in ONE same-commit ceremony — owned by PHASE 5,
not by phases 1-2. Agents with empty/absent allow lists inherit the tool immediately via
the default universe (`daemon/tools/instance.py:551-561`: empty-allow expands every
non-privileged category). NO code-level default grant. Phase 1 registers the category +
tool + wiring ONLY: from phase 1, empty-allow agents can call the tool; explicit-allow
agents receive it at phase 5's ceremony. Phase 1 MUST run the tool-categories artifact
regen (`ENSEMBLE_REGENERATE_TOOL_CATEGORY_ARTIFACT` recipe,
`tests/unit/tools/test_tool_category_artifact.py:17-21`) AND bump VOCABULARY_VERSION 3→4
(`daemon/checkpoint_metrics.py:218`) in the SAME change-set — the v0.28.9 F-1 trap class
(stale committed artifact regenerates as net-new reds in every later gate).

**CENSUS CORRECTION (verified, not a deviation).** The briefed count "39 in-repo meta.json
(34 base + 5 [v2] mirrors)" is STALE. Observed in this worktree:
`find agents -maxdepth 2 -name meta.json | wc -l` = **41**, decomposed as **36 base +
5 `[v2]` mirrors** (`agents/reviewer[v2]`, `tidier[v2]`, `developer[v2]`, `approver[v2]`,
`planner[v2]`). The ruling's SEMANTICS (explicit same-commit tools.allow ceremony across
all in-repo meta.json) are unchanged; phase 5 must plan against the live census and
re-derive the count at ceremony time (agents may be added before then).

**Justification.** The empty-allow universe already excludes privileged categories
structurally (`instance.py:551-561` — "those NEVER join the default universe; an agent
reaches them only through an explicit allow entry"). A write tool that persists
agent-authored rows is not a privileged ops surface (it cannot read secrets, env, or
other instances' data — its own evidence harvest is self-scoped and scrubbed), so the
default-inherit posture is correct and keeps the 17-entry privileged set plus its pin
sites frozen. A code-level default grant would be a NOVEL default-allow mechanism inside
the R-SR16 (`_tool_registry.py` privileged fence, comments at entries #8/#9/#10) /
Council-Note-6 fence — rejected on principle, not convenience.

**Evidence.** `instance.py:551-561` (empty-allow universe); `instance.py:6624-6663`
(metrics-read extend precedent + "entry #17" privileged comment);
`tests/unit/tools/test_tool_category_artifact.py:17-21` (regen recipe);
`checkpoint_metrics.py:218` (`VOCABULARY_VERSION: int = 3`).

**Rejected.**
- *Code-level default grant for all agents:* a new default-allow mechanism inside the
  R-SR16 `:294-309` / Council-Note-6 `:220-229` fence — structurally rejected.
- *Privileged `nest-report`:* wrong semantics (not an ops/read surface) and churns the
  17-entry set + 16 pin sites for nothing.
- *Skipping the artifact regen because "the test will catch it":* that IS the F-1 trap —
  the trap fires as net-new reds in later gates; regen is mandatory, same change-set.

**Amendment trail (fold round 2 — `architecture-recommendation.md`; appended, history intact).**
- **AM-16** — the default-ON posture is CONDITIONAL (Leader decision (a) below): `ENSEMBLE_NEST_REPORT_ENABLED` stays default **True** IFF AM-9 + AM-11 + AM-12 + AM-13 land in this lane AND are verified by the phase-6 tester gate; if any of the four is missing/unverified at gate time, the lane ships default-OFF + a mandatory morning-report disclosure line. ADD the per-writer deny list `ENSEMBLE_NEST_REPORT_DISABLED_AGENTS` (comma-separated agent-type ids, checked pre-rate-gate → `tool-disabled` for that writer only; restart-required, documented) — the surgical lever the whole-surface kill-switch lacks.

---

## R5 — Clustering: WRITE-TIME assignment, gap-based sliding window, stable identity

**Decision.** Clustering happens at WRITE time (the stored report row is mutated once to
carry `cluster_id`), NOT at query time. Cluster KEY = `(tree_root_instance_id, canonical
category)`, where `tree_root` = the root of the reporter's ancestor chain (self if root;
chain via `repository.py:1217 get_ancestor_ids`, bounded 256). On accept: find the cluster
with the same key AND `last_report_at >= now − W` (W = 15 min, SLIDING from the cluster's
LAST report — never fixed buckets); found → assign + UPDATE (`report_count+1`,
`last_report_at`, `max_severity` rollup); else INSERT a new cluster then assign.
STABILITY: `cluster_id` is assigned ONCE at write and never recomputed; earlier reports
never move; later reports never change identity. Resolution is DERIVED, never stored:
open ⇔ `last_report_at >= now − RESOLVE_WINDOW` (1h) — no status column, no reopen races.
Rollup columns on the cluster: `report_count`, `max_severity` (stored, incremental
max-rank), `first_report_at`, `last_report_at`.

**SECOND TABLE justified.** The F23 metric family's pull adapter needs ONE cheap aggregate
(GROUP BY over cluster columns). Query-time fold would re-implement window clustering in
SQL AND make cluster identity retroactively unstable (a later report could re-fold earlier
rows into a different "incident"). A materialized cluster row is the identity anchor the
board item and the metric both point at.

**"N agents × same incident = ONE incident" proof (must be asserted by a pin).** All
descendants of one root resolve to the SAME `tree_root` (ancestor chain is deterministic,
`get_ancestor_ids`); same canonical category (R1 fold is deterministic); reports inside
sliding 15-min gaps CHAIN into one cluster — three agents reporting at t0, t0+5m, t0+12m
land on ONE row even though the span exceeds any fixed bucket; the cluster row's
`report_count` counts members, and read surfaces (phase 4) render clusters as incidents
with member counts.

**Index choice (the ONE pick, justified).** `anomaly_clusters` carries the NON-UNIQUE
composite index `(tree_root_instance_id, category, last_report_at)`.
- CHOSEN: the write-time lookup is exactly `WHERE tree_root=? AND category=? AND
  last_report_at >= cutoff ORDER BY last_report_at DESC LIMIT 1` — a single composite-index
  seek serves it.
- REJECTED `UNIQUE(cluster_key_hash)`: R5's own algorithm INSERTs a NEW cluster for the
  same key once the previous cluster has gone quiet past W (legitimate re-incident — same
  root, same category, hours later). A full unique constraint turns that legitimate INSERT
  into an IntegrityError; the key is intentionally non-unique OVER TIME. A partial unique
  ("open only") is impossible because resolution is DERIVED — there is no open flag to
  index on.
- The write race is answered at the SERVICE layer (single-writer lock, phase 2), not by a
  DB constraint — see phase2-plan.md.

**Evidence.** `repository.py:1217` + `_MAX_TRAVERSAL_DEPTH = 256` at `:50`;
`manager.py:13286 LIVE_DESCENDANTS_BFS_CAP = 500` (the bounded-tree precedent the lane
stays inside); `get_agent_ids_for` `:1237`.

**Rejected.**
- *Query-time fold:* SQL window re-implementation + retroactively unstable identity.
- *Fixed 15-min buckets:* splits one incident that spans a boundary (t0+14m and t0+16m
  reports are the SAME incident); sliding-from-last-report never splits a live incident.
- *Status column + reopen mutations:* reopen races and a second writer to the identity
  row; derived resolution has neither.

**Amendment trail (fold round 2 — `architecture-recommendation.md`; appended, history intact).**
- **AM-4** — all anomaly timestamps written FIXED-WIDTH: `datetime.now(timezone.utc).isoformat(timespec='microseconds')` (always 32 chars, always `+00:00`) in the models' `_utc_now_iso` — a DOCUMENTED divergence from the notify_audit sibling (whose shape IS the hazard); ALL cutoffs computed in Python and bound as parameters — **the phase-2 rate COUNTs as written ERROR on PG** (TEXT vs `now() - interval` has no such operator; house idiom `notify_audit/repository.py:246-253`). Corrected premise (two workers independently converged): lexicographic ISO compare == chronological for the current single producer; pin `timestamp_shape_is_fixed_width`.
- **AM-5** — the report INSERT + cluster INSERT/UPDATE + report→cluster UPDATE execute as ONE transaction inside ONE repository unit-of-work (one `db_offload` call; the service lock wraps the unit call; precedent `board_service.py:186-200`); any `outcome='accepted' AND cluster_id IS NULL` row is an ORPHAN → TERMINALIZED to the NEW outcome token `unclustered` by a bounded age-gated maintenance sweep, NEVER reassigned (post-hoc reassignment = retroactive identity instability — R5's own rejection); a live assignment failure writes the terminal outcome directly. With it, `accepted` MEANS clustered (repairs the metric/route visibility split). Outcome vocabulary becomes `accepted | rate_limited | persist-failed | unclustered`.
- **AM-6** — retention DECREMENTS `report_count` by the deleted-member count per cluster (GROUP BY over each batch's deleted ids, then UPDATE) — without it a chronic cluster (lifetime > 30d) permanently over-counts its surviving rows, and the refuse-vs-merge defense RESTS on this invariant; with decrement, `report_count == COUNT(members WHERE outcome='accepted')` holds ABSOLUTELY.
- **AM-14** — the process-global lock becomes a fixed **64-slot stripe array** (index = `hash(cluster_key_hash) % 64`): bounded memory, same-key serialization fully preserved (the board double-create race stays closed because the board write stays inside the same-key stripe), cross-key contention eliminated (the single global lock held across board I/O was a daemon-wide choke point under a critical storm).

---

## R5b — Rate limits: enforced pre-harvest; refusals ledgered, never counted as spend

**Decision.** minor/major: 5 accepted reports per agent per rolling 10 min (indexed COUNT
over `agent_id + created_at`, the notify_audit ledger-counting precedent). critical:
EXEMPT from the 5/10min but capped at 20 accepted per rolling hour per agent. Enforcement
point: the tool body, BEFORE evidence harvest — a refused report harvests NOTHING (no
transcript read, no ancestor walk, no task-row lookup). Failure mode: typed refusal
envelope `rate-limited` + LEDGERED row `outcome=rate_limited` with ALL harvest-derived
columns NULL, `cluster_id` NULL, NO count inflation. Refused rows never consume budget
(counting queries filter `outcome='accepted'`). The refusal IS the spam signal for
morning review.

**Justification.** Budget counted from accepted-only ledger rows is the notify_audit
discipline (C3 commit-before-count, "ONLY accepted rows consume budget" —
`daemon/config.py` NotifyUserConfig field docs, class at `:3085`): it makes refusals
pure signal without punishing a retrying agent with an ever-extending cooldown. Keeping
identity + authored content (agent, instance, category, severity, description) on the
refusal row but NULLing everything harvest-derived honors "a refused report harvests
NOTHING" literally — tree_root itself is harvest (it comes from the ancestor walk), so
`tree_root_instance_id` is NULLABLE on the model precisely for this row class.

**Evidence.** NotifyUserConfig counting precedent `daemon/config.py:3085+`; ledger prior
art `daemon/repositories/notify_audit/models.py:100-201`; counting index served by
`idx_anomaly_reports_agent_created`.

**Rejected.**
- *Counting all persisted rows (including refusals):* a spam burst would extend its own
  window indefinitely; refusals are signal, not spend.
- *Harvest-then-refuse:* wasted transcript reads on spam and a bigger scrub surface for
  rows nobody reads.
- *Silent refusal (no ledger):* morning review loses the only trace of suppression.

**Amendment trail (fold round 2 — `architecture-recommendation.md`; appended, history intact).**
- **AM-9** — the refusal row is itself an UNBOUNDED persisted write path (one row per refused call, no budget consumption; the sweep is ~288× short of the achievable refusal rate). Fences: `refusal_ledger_max_per_agent` (default **10**/10min — beyond it the SAME `rate-limited` envelope returns with NO ledger row; the metric is the scalable signal, the ledger is for morning review and 10 rows/10min is ample); `max_rows_global` (default **50,000**) + `max_rows_per_tree_root` (default **2,000**) checked PRE-INSERT (served by `idx_anomaly_reports_outcome_created` / `idx_anomaly_reports_tree_root_created`) → NEW refusal token `capacity-exhausted` (no ledger row); `max_accepted_per_hour_global` (default **5,000**) enforced pre-harvest; the retention job gains an **oldest-first capacity trim** beyond `max_rows_global`, batched — self-stabilizing with the insert gate (once at ceiling, inserts refuse ⇒ inflow stops ⇒ the trim converges). Pins: the unbounded-refusal-loop leg (N calls ⇒ rows ≤ ledger cap) + a global-cap leg.
  — **F-1 correction (fold round 3, R-OQ1; CRITICAL): the global cap binds TOTAL rows
  (accepted + refused), not accepted only.** As originally fenced, refusal rows were bounded
  ONLY by `refusal_ledger_max_per_agent` — the §0b global check counted `outcome='accepted'`
  exclusively and the 50k/2k caps lived exclusively on the accept path (a refused call never
  reached them; the per-tree cap is structurally inapplicable to refusal rows — NULL
  `tree_root` per R5b): worst case ~177k–1.77M rows over the 30-day window vs the declared
  50k ceiling that never fired, with the only drain being the idle-gated sweep that AM-15
  itself discloses as starving during flood. FIX: `max_rows_global` is a SINGLE TOTAL-row
  bound — the refusal-ledger INSERT is gated by a global capacity check over TOTAL rows →
  the envelope becomes `capacity-exhausted`, NO row, the rate-limited counter as signal;
  phase2's `self-stabilizing` fence-4 claim was FALSE pre-F-1 and is marked corrected; the
  pin gains the cross-agent/global leg "ledger INSERT never takes total rows above
  `max_rows_global`" (mutation-meaningful: flood planted with the per-agent cap
  unsaturated — the ceiling must fire ON THE REFUSAL PATH).
  — **C3 correction (fold round 5 — LIVENESS + DoS, option (a)): the per-tree cap SHEDS, it
  never refuses; capacity refusals become OBSERVABLE.** As fenced, one tree root
  accumulating 2,000 accepted rows (reachable at the per-type cap 5/10min ≈ 30/h ≈ 67h)
  was blocked from reporting ENTIRELY — including criticals — until the idle-gated 30-day
  sweep ran (the AM-15/NR-7 class), and at the global ceiling the WHOLE nest went dark with
  NOTHING recording that it happened. FIX: (i) a `capacity-exhausted` counter joins F23 —
  `ensemble_anomaly_capacity_exhausted_total` (the band is re-declared with it per AM-2a's
  zero-headroom rule; the F23 REGISTRATION is phase 3's — stated dependency); (ii) the
  per-tree cap gets a TRIM path — PLANNER RULING: a per-tree OLDEST-FIRST BATCHED TRIM
  (shed-then-accept at insert + re-run in the maintenance job; per-cluster `report_count`
  decrements per AM-6; the durable critical record is the BOARD item, which the trim does
  not remove) rather than a per-tree cluster cap — the channel never goes dark for one
  tree; the GLOBAL total-row ceiling remains the nest-wide wall (still refuses with
  `capacity-exhausted`); (iii) `global_capacity_cap_leg` asserts the OBSERVABLE
  CONSEQUENCE — envelope returned, NO row written, the capacity-exhausted counter
  increments, the refusal surfaced for review (not merely the envelope shape).
  — **N2 correction (fold round 5) + D3 correction (fold round 6): `max_accepted_per_hour_global` 5,000 → 2,000 → 1,500.** The N2 rationale's arithmetic was WRONG on the key count: the budget key is the agent TYPE and the distinct-key figure is **36** (41 dirs = 36 base + 5 `[v2]` mirrors resolving to the base id via `version_tag`) ⇒ maximal steady rate = 36 × (30 minor/major + 20 critical) = **1,800/h**, so 2,000 sat ABOVE the steady maximum (reachable only via window-boundary bursts + the N3 read-then-insert race). PLANNER RULING: **1,500/h** — it binds in STEADY state; the knob is an OPERATOR BRAKE, not a security fence (the per-type caps remain the inner brake). CATEGORY_DOC names the bound hierarchy plainly (per-tree row cap [sheds] + global total-row ceiling [the hard nest-wide wall] = hard walls; the 1,500/h hourly knob = early global brake; per-type caps = inner brake). **N3:** "5 per 10 min" is
  BEST-EFFORT under concurrency (read-then-insert; two concurrent reports from one agent
  type can both pass) — stated in CATEGORY_DOC, not a hard invariant. **N3-bound (fix cycle B4,
  2026-11-05, measured by the functional gate): the read-then-insert overshoot is BOUNDED by the
  concurrent in-flight writer width, never unbounded — a single wave of 20 concurrent accepts
  against `max_rows_global=5` admitted +15 beyond the ceiling and +18 beyond a per-type cap of 2,
  with every beyond-cap refusal ledgered or counted (no silent loss); the bound is disclosed in
  CATEGORY_DOC's N3 caveat verbatim.** **N4:** every COUNT
  in phase 2 executes via `db_offload.offload` (the v0.27.14 discipline).
- **AM-10** — `agent_id` is the agent **TYPE** (`instance.py:3015-3023` docstring; threaded `instance_lifecycle.py:2144-2149/:4846-4851`; `[v2]` mirrors resolve to the base id via `version_tag` — **D3, fold round 6: the distinct budget-key count is 36** (41 dirs = 36 base + 5 `[v2]` mirrors resolving to the base id; verified in-tree — e.g. `agents/approver[v2]/meta.json` carries `id="approver"`)); custom agents exist only via the auth-gated REST route — the key space is NOT agent-expandable. Ruling: **KEEP the type key** — spawning does NOT multiply the budget (the fan-out attack is structurally dead); a type's instances SHARE one budget (noisy neighbour); a nest-wide critical burst past 20/hour/type is still VISIBLE (the cluster exists + the rate-limited counter spikes — itself a nest-distress signal). The `(agent_id, tree_root)` alternative **REJECTED** — it multiplies the global ceiling by tree count (an orchestrator spawning trees re-opens the flood). CATEGORY_DOC + `NestReportConfig` field docs MUST state the sharing explicitly; the critical cap stays 20/hour default (knob exists for operators).

---

## R6 — Scrub pipeline + retention

**Decision.** Every harvested TEXT passes `redact_exc_str(text, limit=…)` FIRST
(`daemon/_redact.py:74-98` — pure shape masker: URI netloc / libpq DSN / KV DSN /
IPv4:port; never raises; limit applied AFTER redaction), THEN
`scrub_enqueued_body(text, actor=…)` (`daemon/services/secret_persistence_scrub.py:1250`
— exact-match value substitution on STORED secret values). Transcript excerpts are
bounded BEFORE persistence: **the excerpt = the last ≤5 ERROR-BEARING tool messages in the
turn, selected from a bounded scan window of the last ≤20 messages of that turn's
transcript (the AM-8a read cap); each excerpt ≤500 chars, total ≤2000** (C5, fold round 5 —
the singular rule, identical in phase1-plan.md; error-bearing = **the house ASSIGNMENT
form (`watchover_service.py:114`): `msg_type = getattr(msg, "type", None) or
getattr(msg, "role", None)` THEN `msg_type == "tool"`** (D1, fold round 6 — corrected from
`role == tool`, which never matched a LangChain `ToolMessage`; E2, fold round 7 — the
one-expression form WITHOUT outer parentheses parses as `A or (B == "tool")`, so the
`.type` string short-circuits and Human/AI prose carrying "ERROR:" would be admitted;
the two-line assignment form removes the precedence ambiguity entirely) AND the
content carries the house's typed error shape — the `ERROR:`/refusal-envelope markers house
tools return; the ≤20 scan window IS the same read cap — no extra cost); stored scrubbed
with `excerpt_sha256` + `excerpt_length` (notify_audit discipline: hash + length, never raw
bulk). Scrubber unavailable
(`get_process_scrubber()` → None, `:263`): `redact_exc_str` STILL applies + WARN log —
the report is NEVER refused for scrubber absence (fail-open, disclosed in the tool doc;
the S3 precedent). NEVER persist: raw transcripts, full message lists, env dumps,
checkpoint payloads.

RETENTION: 30 days for reports + clusters via a bounded batched DELETE (LIMIT) hooked into
the EXISTING periodic maintenance seam — `MaintenanceService.register(...)`
(`daemon/services/maintenance.py:141`), job registrations cluster at
`daemon/manager.py:4377-4525`. Fallback if the seam proves non-cheap at implementation:
documented operator runbook step + a routed follow-up commission — do NOT build a new
scheduler. (Pin the exact hook line in phase2-plan.md; the seam exists and is cheap.)

**Justification.** Order matters: the shape masker is dependency-free and always available,
so it runs even when the value-substitution scrubber is absent — the two layers degrade
independently and the composed guarantee is disclosed rather than silently weakened.
Hash+length lets reviewers verify excerpt integrity without re-introducing cleartext.
Retention on the existing service reuses a registered-job pattern that already sweeps
checkpoints; a new scheduler would be a second timing authority.

**Evidence.** `_redact.py:74-98` (read verbatim: "Never raises… limit applied AFTER
redaction"); `secret_persistence_scrub.py:615 scrub_text`, `:1250 scrub_enqueued_body`,
`:263 get_process_scrubber`; notify_audit `_utc_now_iso` `:67` + Index triplets `:135-146`.

**Rejected.**
- *Scrubber-only (skip redact when unavailable):* a scrubber outage would persist
  unmasked DSN/URI shapes; the masker is the always-on floor.
- *Refusing the report when the scrubber is down:* the nest goes blind exactly when
  things are broken; fail-open + WARN is the disclosed trade.
- *New retention scheduler:* second timing authority beside MaintenanceService for no gain.
- *Persisting raw transcripts "for context":* the exact bulk the scrub family exists to
  keep out of the DB.

**Amendment trail (fold round 2 — `architecture-recommendation.md`; appended, history intact).**
- **AM-11** — the surviving secret classes are EMPIRICALLY verified against `daemon/_redact.py:74-98`: `POSTGRES_PASSWORD=…` (the KV-DSN regex's `\b` fails after `POSTGRES_`), bare `OPENAI_API_KEY=sk-…`, URL **query-param** secrets (the netloc masker stops at the first `/`), JSON secret dumps, bearer tokens; the exact-match scrubber knows only STORED secrets (the daemon's runtime creds are invisible to it; scrubber-absent = fail-open identity, `secret_persistence_scrub.py:1270-1283`); the excerpt harvests raw tool stdout (`bash.py:1152` returns `STDOUT:` verbatim, 150k cap). Fence: NEW additive `redact_secret_shapes(text, limit)` in `daemon/_redact.py` (masks `sk-[A-Za-z0-9_\-]{8,}`, `Bearer\s+\S+`, `(?i)(api[_-]?key|token|password|secret|access[_-]?key)\s*[:=]\s*\S+`, `(?i)\?[^\s]*(key|token|secret)=[^&\s]+`, env-var shapes `*_API_KEY=`/`*_TOKEN=`/`*_PASSWORD=`/`*_SECRET=`) applied to description/suspected_cause/excerpt BEFORE `redact_exc_str`; **`redact_exc_str` itself is UNTOUCHED** (its callers are pinned); the EXCERPT column flips **FAIL-CLOSED** on scrubber-absent (persist NULL + WARN) while description/suspected_cause keep the fail-open shape (the pattern masker is the always-on floor) — the CATEGORY_DOC disclosure is updated accordingly; the planted-secret pin plants a **NON-STORED** shape (e.g. `sk-proj-…` inside a transcript tool message — a `secret_store`-planted value is satisfied by the exact-match path and proves nothing about the real risk). Excerpt persists for **major + critical ONLY** (AM-8e).
- **C5 amendment (fold round 5 — excerpt selection rule made SINGULAR; the approver's ruling that the ERROR-FILTER reading wins):** the canonical rule is now stated once, identically here and in phase1-plan.md — *"the excerpt = the last ≤5 ERROR-BEARING tool messages in the turn, selected from a bounded scan window of the last ≤20 messages of that turn's transcript (the AM-8a read cap); each excerpt ≤500 chars, total ≤2000."* Error-bearing predicate: **the house ASSIGNMENT form (`watchover_service.py:114`, `watcher_context_builder.py:309`): `msg_type = getattr(msg, "type", None) or getattr(msg, "role", None)` THEN `msg_type == "tool"` (D1, fold round 6 — corrected from `role == tool`, which never matched a LangChain `ToolMessage`; E2, fold round 7 — the unparenthesized one-expression form parses as `A or (B == "tool")` and admits Human/AI prose carrying "ERROR:" — the trust-boundary break the negative-selection leg `M-1i` now pins) AND the content carries the house's typed error shape (`ERROR:` — `daemon/tools/filesystem.py:175/:181/:190` — and/or `Error: {label} REFUSED — reason={token}: {message}` — `daemon/tools/instance_ops.py:256`, `instance_read.py:221`).** Numeric bounds unchanged (5 × 500, ≤2000 total); the scan window is the SAME read cap (no extra cost). **D1b:** the phase-1 transcript pack gains the POSITIVE-SELECTION LEG **M-1h** (plant an error-bearing tool message ⇒ it LANDS in the excerpt; mutation: invert the predicate ⇒ RED) — the former pack pinned only the read cap (M-1g), so the empty-excerpt failure was silent. No CATEGORY_DOC deviation note is needed — "recent tool errors" was always the intent.
- **C1 correction (fold round 5 — SECURITY, approver-verified): the secret-shape fence did NOT cover the JSON-secret class it named.** The keyword pattern required `:`/`=` immediately after the keyword, but JSON puts a QUOTE there; the house `_KV_DSN_RE` (`daemon/_redact.py:60-65`) misses it too. Verified UNMASKED by the approver: `{"password": "hunter2"}`, `{"postgres_password": "s3cr3t"}`, `{"token": "abc123XYZ"}` (while `POSTGRES_PASSWORD=`, `Authorization: Bearer`, `?api_key=` ARE masked). This matters more than a normal gap: the excerpt is RAW tool stdout (`bash.py:1152`, 150k cap) and the exact-match scrubber knows only STORED secrets (`secret_persistence_scrub.py:1250`) — for a NON-STORED secret the pattern masker is the SOLE defence, so "excerpt fails closed when the scrubber is absent" gives FALSE comfort there. FIX (all folded in phase1-plan.md): (a) `redact_secret_shapes` gains the JSON key/value shape — an OPTIONAL QUOTE between keyword and separator, masking the optional-quoted value (`(?i)["']?(api[_-]?key|token|password|secret|access[_-]?key)["']?\s*[:=]\s*["']?[^"'\s,}]+`; the alternation may match as a suffix, e.g. `postgres_password`); (b) the planted-secret pin plants a JSON-shaped NON-STORED secret IN ADDITION to `sk-proj-…`, with a mutation leg PER PATTERN; (c) phase1 states the sole-defence fact explicitly and forbids reading the fail-closed posture as covering the class; (d) this record.
- **R-a hardening (fold round 6):** the C1 pattern required the keyword FAMILY word to be FINAL before the separator — `{"secret_note":…}`, `{"password_hint":…}`, `{"client_secret_id":…}` survived (value unmasked). The keyword alternation now carries a TRAILING WORD SEGMENT `[a-z_]*` (keyword-FAMILY masks; over-masking is the fail-safe direction, and the over-match probe must still leave legitimate prose untouched). A PIN LEG covers the widened family (mutation: delete `[a-z_]*` ⇒ the suffix legs RED). Folded in phase1-plan.md's scrub step 0.
- **E3 (fold round 7 — LOW–MEDIUM, diagnostic preservation): the widened family over-masks the token-metering diagnostics.** `[a-z_]*` lets `token` absorb suffixes, so `{"max_tokens": 200000}`, `{"input_tokens": 1200, "output_tokens": 340}`, `token_budget: 200000` were masked — precisely the fields of a TOKEN-EXHAUSTION error, the exact text the excerpt exists to capture (the house doctrine `daemon/_redact.py:8-11` masks "only patterns that are unambiguous database identifiers" to preserve diagnostic value). PLANNER RULING: keep the widened secret-bearing family (R-a) but EXCLUDE the benign names explicitly — `max_tokens`, `input_tokens`, `output_tokens`, `token_budget`, `token_count`, and the `*_tokens` family — and extend the over-match probe to those names (the pin must be able to go RED on a regression). Folded in phase1-plan.md's scrub step 0.
- **R-b residual (fold round 6):** a per-tree/global shed can delete ALL member rows of a cluster while the cluster row survives (report_count = 0, still "open" up to 30 days, counted by `ensemble_anomaly_clusters_open`). Mitigation folded in phase2-plan.md: clusters whose report_count reaches 0 are DELETED in the SAME unit-of-work (both the age/global trim and the per-tree trim leg), pinned by `cluster_row_dies_with_members`.
- **E1 closure gap in the C1 class (fold round 7 — NOT a new class: the same fence, an unclosed value shape).** The value class `["']?[^"\s,}]+` excluded WHITESPACE, so a quoted value containing a space was masked only up to its first space — approver-verified leaks: `{"password": "correct horse battery staple"}` → `{*** horse battery staple}`; `{"db_password": "my secret phrase"}` → `{"db_*** secret phrase"}`; `password="two word secret"` → `*** word secret"`. A PASSPHRASE is the most common DB/API credential shape, and the exact-match scrubber does NOT help (stored values only — C1c: the pattern masker is the SOLE defence for a non-stored secret); the planted pin (`{"password": "hunter2"}`, single token) was GREEN with the hole open. FIX: the value class is QUOTE-AWARE — `(?:"[^"]*"|'[^']*'|\S+)` (closes all three leaks; unquoted form unchanged) — and the planted pin gains a MULTI-WORD QUOTED case asserting the FULL value is absent from every persisted column (a first-word-only needle would still pass on a partial mask). Folded in phase1-plan.md's scrub step 0 + planted pin.

---

## R7 — Migrations: two files, house rules, DOWN present-but-never-executed

**Decision.** TWO `.sql` files (one per table — never a shared file),
`daemon/migrations/versions/20261105_HHMMSS_create_anomaly_reports.sql` and
`..._create_anomaly_clusters.sql`, prefixes derived at implementation (> `20261031_120002`
is free; NEVER reuse; census 119 → 121). Rules: `IF NOT EXISTS` mandatory; TEXT everywhere
(never VARCHAR); index names byte-identical between the `.sql` and the model
`__table_args__` (house naming `idx_<table>_<cols>`, per `idx_notify_audit_agent_created`
at `models.py:135-146`); JSON columns per the house dual-face rule (SQLite JSON / PG
JSONBType); model registered into metadata BEFORE create_all (the `daemon/api.py`
late-import block); `-- UP` required; `-- DOWN` sections PRESENT but never executed —
documented why: rollback never runs migrations (the runner is SQLite-only and PG schema
arrives via `create_all` on new-binary boot; the DOWN text is audit documentation of the
inverse shape, not executable history).

**Justification.** The `.sql` file is the persistent audit record (runner executes it on
SQLite only); PG reality is create_all + `_ensure_postgres_columns`. Byte-identical index
names keep the drift tests and the staging manifest comparisons honest.

**Evidence.** Census verified: `ls daemon/migrations/versions/ | wc -l` = 119, latest
`20261031_120002_create_project_skill_usage_records.sql`; canonical prior art
`20261025_000001_create_notify_audit.sql` + `daemon/migrations/README.md`.

**Rejected.** *Single combined migration file:* violates one-file-per-table;
*executing DOWN on rollback:* no production caller exists and PG never runs the runner.

---

## R8 — Read-surface split: metric-shaped vs row-shaped; metrics door stays FROZEN

**Decision.** Three read lanes, separated by shape:
1. **Metric-shaped** (counts by category over time) → `MetricsQueryProvider` via the
   EXISTING `metrics-read` tools — ZERO edits to that category.
2. **Row-shaped** (incident/cluster/report rows) → NEW privileged category `nest-report-read`,
   an ops-trio through the anomaly service — never raw engine handles, never direct
   repository access from tool bodies.
3. **FE incident list** → NEW domain REST route `/api/nest-reports/*`, gated IDENTICALLY
   to the metrics door: the door's any-of QUERY scope {`maintenance:read` | `metrics:read`}
   (`daemon/routers/metrics.py:104-115` — `_SCOPE_READ` `:106` + `_SCOPE_READ_METRICS`
   `:115`; the door's TEXT-exposition route keeps `maintenance:read` ALONE and is NOT our
   model) + `503 AUTH_NOT_CONFIGURED` fail-closed (mirror `daemon/routers/metrics.py:106-137`).
   It is a DOMAIN route, not a second
   metrics query path; the metrics door stays FROZEN. Scope = the door's any-of query set
   {maintenance:read | metrics:read} — planner ruling on worker OQ1 2026-11-04 (rationale:
   identical gating; FE per-source consistency for pre-split operator tokens; accepting the
   superset token grants zero new capability — the scopes.py narrowing rationale governs
   token MINTING, not route acceptance).

**Justification.** The metrics query door (`/api/metrics/families|snapshot|series`) carries
a frozen contract (vocabulary, cursor, buckets) that row-shaped data must not distort.
Splitting by shape lets phase 3's family flow through the existing metric machinery while
incident lists get a purpose-built door with the same security posture — same scope gate,
same fail-closed 503, different payload schema.

**Evidence.** `daemon/routers/metrics.py` — any-of scope note `:32`/`:57`,
`_SCOPE_READ` `:106`, `_ERR_AUTH_NOT_CONFIGURED` `:121`, fail-closed gate `:137`;
provider read discipline `daemon/tools/metrics_read.py:44-49`.

**Rejected.**
- *Serving incident rows through the metrics door:* unfreezes a frozen contract.
- *nest-report-read tools holding raw Engine handles:* bypasses the service's scrub/rate/audit
  invariants; house pattern is service-mediated privileged tools.
- *FE calling the anomaly service in-process:* FE is browser-only; there is no in-process
  FE path — the REST route is the only honest seam.

---

## R9 — Naming adjudication: the two-layer `nest-` / `anomaly-` split

**Decision.** Identifiers split into TWO layers, matching the board item's own two-name
title "anomaly-report tool (nest-report)":

- **`nest-` layer (OPERATIONAL surfaces)**: tool category keys `nest-report` (write,
  non-privileged) + `nest-report-read` (read, privileged #18) — this RENAMES the working
  names used earlier in this file (R4's `anomaly-report` → `nest-report`; R8's
  `anomaly-read` → `nest-report-read`); config knobs `nest_report_*` / env
  `ENSEMBLE_NEST_REPORT_*` (renames R4's `AnomalyReportConfig` /
  `ENSEMBLE_ANOMALY_REPORT_*`); REST `/api/nest-reports/*`; FE slot `nest-reports`
  (label "Nest Reports" already ruled).
- **`anomaly-` layer (DATA + agent vocabulary)**: tool function name `report_anomaly`
  (the spec's verbatim seed call); read-tool function `anomaly_report_detail`; DB tables
  `anomaly_reports` / `anomaly_clusters`; metric names `ensemble_anomaly_reports_total`,
  `ensemble_anomaly_clusters_open`, `ensemble_anomaly_reports_rate_limited_total`.

**Collision analysis (surveyed facts on record).** `anomaly*` is saturated by the
RESOURCE-ANOMALY ENGINE: `daemon/services/resource_anomaly.py` (1,835 lines, `wc -l`
verified), the `/api/resources/anomalies` + `/{id}/evidence` REST door
(`daemon/routers/resources.py:20/:24`), FE `anomaly-overlay-plugin.ts` +
`anomalies-panel.component.ts` (`frontend/src/app/pages/metrics/charts/`,
`frontend/src/app/pages/metrics/resource/anomalies/`), config keys `anomaly_*`
(`daemon/config.py:905-1090`, 10 occurrences; `anomaly_enabled` at `:948`). `report*` is
taken in the route/live-view space (`report_injections`, `midflight_report`, the
`reports` live-view root). `category` is taken as a name in TWO other namespaces (the
tool-categories artifact; `critical_notes.category`). The OPERATORS' surfaces — config
env vars, REST paths, FE URLs, category keys — are where the confusion is REAL: those
take `nest-`. The DATA/vocabulary layer keeps the user's own words: the spec seeds
`report_anomaly(...)` and the family `anomaly_reports{category=…}` verbatim.

**Non-collision justifications for the KEPT `anomaly-` identifiers (one line each):**

- (i) `report_anomaly` — flat first-party tool namespace, zero existing tool matches;
  `report_injections`/`midflight_report` are distinct identifiers and the survey's
  `report*` collision is about ROUTE/live-view naming, not tool functions.
- (ii) tables — NO `anomaly*` table exists today (verified, R-16); the DB namespace is
  disjoint from config/route/FE.
- (iii) metrics — NO `ensemble_anomaly_*` metric exists in the registry
  (identifier-level); `daemon/tools/metrics_read.py:93-95` is a NON-GOALS clause binding
  the read tools' ORDERING contract ("There is NO anomaly-first ordering... the anomaly
  signal is ``metrics_history``'s ``delta_change``" — read verbatim) — it reserves the
  PHRASE for ordering semantics, not the registry namespace; the family's help text MUST
  disambiguate explicitly: name `delta_change` AND the resource-anomaly engine as
  different things.
- (iv) label key `category` — label keys are per-metric scoped (precedents: wedge
  `reason`, gateway `outcome`); no cross-metric collision is possible.

**Rejected alternatives (both recorded):**

- (a) keep-everything (category `anomaly-report` + `/api/anomaly-reports` + FE slot
  `anomalies`) — REJECTED: walks into the verified config-key substring family
  (`anomaly_report_*` vs `anomaly_*`), a REST path one word apart from
  `/api/resources/anomalies`, and an FE slot id colliding with the resource tab's
  Anomalies panel.
- (b) nest-everything (rename the tool to `report_nest_anomaly` + tables + metrics) —
  REJECTED: contradicts the user's verbatim seeds for the tool call and the family
  shape; the data layer is collision-free, so the rename buys nothing there.

**Namespace-virgin check (REQUIRED, recorded).** Executed in the worktree @ base
`1f6898ae2`:
`grep -rn "nest-report\|nest_report\|/api/nest" daemon/ frontend/src/ agents/ --include=*.py --include=*.ts --include=*.json | grep -v planning`
→ **0 identifier hits**; a supplementary unfiltered grep (all file types, excluding
`planning/` and `.md`) also returned 0. The `nest-` namespace is VIRGIN — free to claim.

---

## F23 — Family id F23, `source_idiom="registry"`, `durability="durable"`

**Decision.** The anomaly counts-by-category metric family is **F23**,
`source_idiom="registry"` (read-only pull from the registry/provider seam, same idiom as
the existing families), `durability="durable"` (the cluster table is the persistent
source; the family survives restarts because it reads rows, not RAM). Family registration
and the pull adapter are PHASE 3 (sibling); this entry fixes the identity so phases 1-2
store what F23 will read: per-cluster columns `report_count`, `max_severity`, canonical
`category`, `first_report_at`/`last_report_at` — the exact GROUP BY surface.
Durability cross-ref (advisory 4, fold round 3): the family's `durability="durable"`
reflects the TABLE-BACKED members (counts/clusters/open-gauge — read rows, survive
restarts); AM-13f's 4th member (`ensemble_anomaly_board_items_suppressed_total`) is
process-durable — see phase3's family-projection disclosure.

**Closed-space arithmetic (N1, fold round 5 — AUTHORITATIVE).** The 4-member state was
13 + 39 + 13 + 1 = **66** ⇒ global closed space 425 → **491** (the approver's iter-001
figure). With C3(i)'s 5th member (`ensemble_anomaly_capacity_exhausted_total`) the band is
13 + 39 + 13 + 1 + 1 = **67** and the global closed space is 425 → **492**. The ceremony
text derives the FINAL number from THIS line (the D-5 rule), never from a phase file.
C3(i) DEPENDENCY: the 5th member's registration is PHASE 3's (sibling) — the F23 pack must
count 5 members and the band must be re-declared with the vocabulary (AM-2a's
zero-headroom rule makes this mechanical).

**Disclosure (do NOT fix in this lane).** The registry idiom's known-live defects — R_i
(census gated polarity inverted vs the contract truth table) and R_ii (F1/F3
`capture_active` never wired, `api.py:2528-2533`) — are STILL LIVE at base `1f6898ae2`.
The registry idiom itself is UNAFFECTED by both (they are census-polarity and
capture-wiring defects, not pull-seam defects), so F23 proceeds on the registry idiom.
Verification in phases 3/6 must assert the family actually PUBLISHES series (not merely
registers) so a silent-dark F23 cannot ship looking green.

**Evidence.** F23 slot FREE: no `F23` registration anywhere under
`daemon/services/metrics_query/` (adjudication map covers F20/F21/F22 only,
`wiring/adjudication.py:75-77`; roster/w3 show F21). AM-2 allowed_values source: the R1
vocabulary.

**Rejected.** *A non-registry idiom (one-shot in-process counters):* non-durable, a second
collection path beside the one the door already serves — the exact duplication R8 rejects.

---

## Survey facts on record (leader fold item 10)

Carried as surveyed facts WITH anchors; the two contradictions are FLAGGED loudly.

- **(A) Duty-prompt mechanism — repo files are the loader's live fallback.** BOTH anchor
  sets recorded; they are complementary aspects of the SAME mechanism, not a conflict:
  survey anchors `daemon/loader.py:76-100` (the loader hook over the
  `PROMPT_ARTIFACTS` core set) + `daemon/services/prompt_version_service.py:711-716`
  (the human-lane gate); plan anchors `daemon/loader.py:333-343` (T10 overrides-only
  seeding — "the template file stays the LIVE FALLBACK"; resolver chain
  `selected_version.content → latest_approved.content → None` and only then reads the
  file) + `daemon/loader.py:896-926` (`load_agent_prompts`). All four ranges verified
  in-worktree.
- **(B) R_i still-live — citation CONFORMED to the survey's, where more precise.**
  `daemon/services/metrics_census.py:322-335` (the PUBLIC `gated` read-through of the
  existing `_gate()` — "a reader… must NOT read the kill-switch itself") vs
  `daemon/services/metrics_query/provider.py:110-121` (`census_enabled` — "Sourced from
  the PUBLIC ``CensusSampler.gated`` property… The query package NEVER reads the switch
  itself"). The plan previously cited `provider.py:117-120/:145` — same finding; the
  survey's citation governs.
- **(C) AM-2 closed vocabulary** — `daemon/services/metrics_query/wiring/registry.py:102-114`
  (already the plan's anchor ✓ — no change).
- **(D) Naming collision list** — recorded in full in R9 above (feeds it).
- **(E) No-all-agents-precedent census — CONTRADICTION FLAGGED.** The survey reports
  **33/41** agents carrying an explicit `tools.allow` precedent; the plan's worker found
  **40 explicit / 1 empty-array (`watcher`) / 0 no-block**. MACHINE-VERIFIED in this
  worktree @ `1f6898ae2` by direct enumeration of all 41 `agents/*/meta.json` (Python
  json walk over `tools.allow`): **40 explicit / 1 empty-array (`watcher`) / 0
  no-block**. The plan's 40-grant edit set STANDS; the survey's 33/41 is recorded as
  NOT REPRODUCING (noted possibility: a categories-vs-literal-names split of the allow
  entries — counting agents whose allow entries name CATEGORY keys vs literal tool names
  could produce the lower figure).
- **(F) No generic rate-limiter exists in the tree** — the lane builds a per-tool LEDGER
  BUDGET over its own reports table (the notify_user precedent,
  `daemon/tools/notify_user.py:1205-1330`), NOT a shared limiter; phase 2's enforcement
  is per-tool by construction (see phase2-plan.md Rate Limits).

---

## Leader decisions (fold round 2 — recorded verbatim-intent)

- **(a) AM-16 default-ON STANDS, CONDITIONAL.** The tool ships default-ON IFF AM-9 + AM-11 +
  AM-12 + AM-13 have landed AND are verified by the phase-6 tester gate (the gate expectation
  "the four security AMs are present and pinned" is added to phase 6); if any of the four is
  missing/unverified at gate time, the lane ships default-OFF + a mandatory morning-report
  disclosure line. Rationale: the user directive is "all agents get this tool" — default-OFF
  hollows the commission; the four AMs bound the abuse surface; the env kill-switch stays the
  operator escape. The per-writer deny list `ENSEMBLE_NEST_REPORT_DISABLED_AGENTS` lands.
- **(c) IMPLEMENTATION GATE NOTE (fold round 7): E1 + E2 MUST be present before implementation commit C1 lands** — they land in C1/C2 (the scrub fence and the excerpt-selection predicate are both C1-code), and the phase-6 gate must confirm both before the C1 boundary is declared GREEN; an E1/E2 regression at gate time REDs C1, not the lane.
- **(b) The `(reviewer, workflow)` pending-approval DB row stays USER-OWNED and disclosed
  (morning item U-2b)** — it cannot mask anything this lane lands: the duty home is `rule.md`
  and `artifact='rule'` has ZERO DB rows of any state for any agent (census below).

## R-01 prompt_versions census (fold round 2 — read-only; PGOPTIONS default_transaction_read_only=on; zero writes)

- ZERO approved+selected rows anywhere (`is_selected=0` on every row); `artifact='rule'` has
  ZERO rows of ANY state for ANY agent; table total **6 rows across exactly 2 keys** —
  `(ari, user)` 4 rows all approved [MEMORY class, DB-effective via latest-any-state, but
  `user.md` is OUTSIDE the edit set] and `(reviewer, workflow)` 2 rows [v1 archived, v2
  `pending_approval` since 2026-10-19 — the latent conditional shadow]. The census STEP
  remains a re-run-at-landing AC (confirm nothing changed).

## VOCAB arithmetic resolution (fold round 2)

NO discrepancy — the plan's per-bump 3→4 (C2) then 4→5 (C5) is CORRECT and
architect-confirmed ("two commits, never one 3→5 jump"); the architect's "3→5" is the LANE
TOTAL. Phrase every mention unambiguously from now on: **per-bump 3→4 (C2), 4→5 (C5);
lane-total 3→5; end state 59 categories @ v5**.

## Amendments index (AM-1..AM-18 — one line each + where folded)

Source: `architecture-recommendation.md` (SOUND-WITH-AMENDMENTS; binding, mechanical; no
ruling overturned).

| AM | One-line | Folded where |
|---|---|---|
| AM-1 | Metrics-router route-set pin (exactly `{/families, /snapshot, /series}`; a 4th route REDs — the census-only tripwire is degraded) | phase 3 route pack (sibling) |
| AM-2 | F23 fences: zero-headroom band (65 architect §4 pre-AM-13f → 66 @ 4 members (phase3-plan.md:196) → **67 @ 5 members with C3(i)'s capacity-exhausted counter; global closed space 425→492** — the authoritative line lives in the F23 section) re-declared with the vocabulary; `bands ⊇ registered` pin; raw-token fold — let the registry fold out-of-vocab (AM-2d); all-three F23 roster role pins + general roster-coverage pin; `monotonic` disclosure (AM-2f) | phase 3 packs (sibling) |
| AM-3 | Missed VOCAB bump sites: two test literals (`test_metrics_family_adjudication.py:419`, `test_metrics_vocabulary_amendment.py:26`) + `checkpoint_metrics.py:200-217` docstring history + exposition baseline re-run per bump | phase 3 packs (sibling); the C2-side test-literal update rides phase 1's bump commit |
| AM-4 | Fixed-width timestamps (`isoformat(timespec='microseconds')`) + Python-bound cutoffs (the phase-2 rate SQL ERRORS on PG as written) + `timestamp_shape_is_fixed_width` pin | decisions R5 ✓; phase1 models/config; phase2 rate counts |
| AM-5 | ONE-transaction unit-of-work + orphan TERMINALIZATION to `unclustered`, never reassignment; outcome vocab +1; accepted MEANS clustered | decisions R5 ✓; phase1 outcome enum; phase2 sections+pins |
| AM-6 | Retention DECREMENTS `report_count` per deleted member; invariant absolute; pins `report_count_equals_member_rows` / `no_orphan_accepted_rows` / `retention_decrements_counts` | decisions R5 ✓; phase2 retention+pins |
| AM-6c | FE incident list groups by `(tree_root_instance_id, category)` + occurrence badge — read-side affordance, never re-assignment | phase 4 (sibling) |
| AM-7 | Peak-severity labeling everywhere ("critical (peak)") + board item → `done` on cluster resolve (board-row write only) | decisions R3 ✓; phase2 board/maintenance |
| AM-8 | Harvest rewrite: transcript seam → `instance_read.py:889-909` bounded `raw_saver.aget` + `messages[-N:]` (N ≤ 20, `:154` precedent), cap AT the seam (M-1g reds at the seam); `families()` OUTSIDE any offload worker (registry lock; reentrancy guard `db_offload.py:379-385`); honest ceiling 5 off-loop CALLS (ancestor walk = O(depth ≤ 256) sequential PK reads in ONE call, NOT 1 round trip) — **C4 (fold round 5) re-derives 5 → ~7: the task→JobItem→MissionCommission chain is ≈3 queries on 3 tables**; `boot_epoch` pinned to NAIVE-UTC, never compared cross-shape; minor severity SKIPS the transcript read entirely | phase1 harvest table ✓ |
| AM-9 | Flood fences: refusal-ledger cap (no-row-beyond), pre-INSERT global/tree caps → `capacity-exhausted`, pre-harvest global accepted/hour, retention capacity trim oldest-first; pins (unbounded-refusal-loop leg, global-cap leg) | decisions R5b ✓; phase1 config+token; phase2 enforcement+pins |
| AM-10 | Budget is per agent TYPE; type key KEPT; `(agent_id, tree_root)` REJECTED; sharing disclosed in CATEGORY_DOC + field docs; key space not agent-expandable | decisions R5b ✓; phase1 docs |
| AM-11 | NEW `redact_secret_shapes` masker BEFORE `redact_exc_str` (untouched); excerpt FAIL-CLOSED on scrubber-absent; NON-STORED planted-secret pin; excerpt for major+critical only — **C1 (fold round 5): the JSON key/value shape (optional quote + quoted value) is added; the class the fence already named was NOT covered until C1** | decisions R6 ✓; phase1 scrub pipeline |
| AM-12 | Projection/injection pins: list projection EXCLUDES excerpt; detail behind `?include_excerpt=true`; FE ≤300-char reveal; NO innerHTML/bypassSecurityTrust (pinned, mutation leg); `top_description` deterministic (highest severity, tie `created_at ASC, id ASC`) + fixed board-body prefix "auto-filed — unverified agent prose" | phase 3/4 (sibling) EXCEPT AM-12e → phase2 board sink ✓ |
| AM-13 | Board ceilings (3/tree/hour, **20 global** — the W-6 correction; authority: decisions.md AM-13 fold-round-3 bullet above, `config.py` ships 3/20 and the values are pinned by `TestAM13ShippedCeilingValues`) + note-append fallback + suppression counter; TITLE carries cluster_id; single-reporter-HOLD REJECTED; `MAX_NOTES_PER_ITEM=20` disclosed+routed | decisions R3 ✓; phase2 board sink |
| AM-14 | 64-slot striped per-key locks replace the process-global lock; offload-gate pressure (~5 offloads/report vs the 25-slot strict-admission executor; burst ⇒ NULL evidence, report accepted) disclosed in CATEGORY_DOC | decisions R5 ✓; phase1 CATEGORY_DOC; phase2 locking |
| AM-15 | Idle-gated maintenance starvation (NR-7: the 24h retention NEVER runs on a continuously busy nest, `maintenance.py:258-262/:326-395` — the checkpoint-cleanup class) — disclosed + routed to the maintenance lane | phase2 retention disclosure ✓ |
| AM-16 | Conditional default-ON (Leader decision (a)) + per-writer deny list `ENSEMBLE_NEST_REPORT_DISABLED_AGENTS` | decisions R4 ✓ + Leader decisions ✓; phase1 config |
| AM-17 | Nine-commit ordered choreography C1-C9, each boundary GREEN (boot-scan, drift tests, privileged-set pins); per-commit smoke set binding; the 81-file single ceremony commit REJECTED | lane implementation / phase 6 |
| AM-18 | Phase-order CONFIRMED (P3 after P1+P2; P4 after P3; P5 after P1 + after P3's meta.json appends; VOCAB double-bump ordered) — no re-order, no fusion | record only (arch §8) |

---

## Watch-outline consultation (N7 — fold round 5)

Consulted `docs/architecture/metrics-watch-outline.md` (design-only; v1 PULL-ONLY per its
own ruling R3 — it plans "no tool, no category, no schedule row, no trigger vocabulary, no
delivery path").

- **APPLIES:** its delivery-primitives census (board = ALWAYS, notify = deliberate) is
  EXACTLY this lane's critical→board ruling — the anomaly path writes the board and never
  auto-notifies. Its chat bounds (N=1/turn ≥60s, M=6/rolling-hour) are NOT auto-called by
  this lane (nothing here sends chat), so they impose nothing on it.
- **EXPLICITLY DECLINED:** no triggers, no evaluator, no schedule arming — the watch/alert
  lane remains a separate PENDING USER DECISION; this lane adds a reporting surface only.
- **CROSS-CHECK:** the board-only choice is consistent with that census — the board is the
  guaranteed surface; the anomaly tool never voices.
