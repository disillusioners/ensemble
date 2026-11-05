# Phase 1: Storage + write tool + evidence harvest + scrub + category registration

Lane: nest-anomaly-report (wi-2d691ed2) · Author: plan-creation worker · Date: 2026-11-04T22:30Z
Rulings binding this phase: R1 (vocabulary), R4 (category/grant/registration), R5 (model
columns serving clustering), R5b (rate-limited row shape — model only; enforcement lands in
phase 2), R6 (scrub + caps), R7 (migrations). **Architect fold round 2: SOUND-WITH-AMENDMENTS
(`architecture-recommendation.md` — binding): AM-4, AM-5, AM-8, AM-9, AM-10, AM-11, AM-14,
AM-16 fold HERE** (trails recorded in decisions.md). Worktree:
`/home/nea/ensemble-src-wt-nest-anomaly-report` — NEVER touch `/home/nea/ensemble-src`.

## Objective

An agent can call `report_anomaly(...)`; the report is normalized (R1), scrubbed (R6),
bounded-evidence-harvested, and persisted to `anomaly_reports`. The `nest-report`
category is registered end-to-end (10-step checklist + artifact regen + VOCABULARY_VERSION
3→4) so empty-allow agents can call it immediately (R4). Clustering/rate-limits/board are
phase 2; this phase's accept path writes a DEGENERATE cluster tail (see Sequencing).

## File Inventory

**New**
| Path | Content |
|---|---|
| `daemon/repositories/anomaly/__init__.py` | package exports |
| `daemon/repositories/anomaly/models.py` | `AnomalyReport`, `AnomalyCluster` (SQLModel, spec below) |
| `daemon/repositories/anomaly/repository.py` | insert report; count queries (phase-2 ready); cluster lookup/insert helpers; batched retention delete helper |
| `daemon/services/anomaly_service.py` | normalize → scrub → harvest → persist orchestration (phase 2 extends with clustering/rate/board/retention) |
| `daemon/tools/nest_report.py` | `create_nest_report_tools(...)` factory + the `report_anomaly` tool + CATEGORY_DOC |
| `daemon/migrations/versions/20261105_HHMMSS_create_anomaly_reports.sql` | R7 |
| `daemon/migrations/versions/20261105_HHMMSS_create_anomaly_clusters.sql` | R7 |

**Edited**
| Path | Change |
|---|---|
| `daemon/config.py` | `NestReportConfig(BaseSettings)` beside `NotifyUserConfig` (`:3085`), wired into top-level Config (pattern: `report_repair`, `notify_user`) |
| `daemon/services/metrics_query/wiring/vocab.py` | `ANOMALY_CATEGORIES` tuple + `ANOMALY_SEVERITIES` + `__all__` entries (`:296` block) |
| `daemon/tools/_tool_registry.py` | CATEGORY_MODULES, DYNAMIC_TOOL_NAMES, KNOWN_TOOL_NAMES regen (checklist steps 2-4) |
| `daemon/tools/instance.py` | the CRITICAL list-append inside `create_instance_tools`, BEFORE `create_help_tool` / `scan_tools_for_full_docs` / `_apply_tool_filter` |
| `daemon/loader.py` | warm import + stub construction (`:519-528` pattern, `:560-566` stub pattern) |
| `daemon/checkpoint_metrics.py` | `VOCABULARY_VERSION: int = 3` → `4` (`:218`) — SAME change-set as the artifact |
| `.agents/shared/metrics/tool-categories.json` | regenerated via the env-var recipe (never hand-edited) |
| `daemon/api.py` | late-import block: anomaly models registered into metadata BEFORE `create_all` |

**Never touched this phase:** `agents/**` (phase 5 owns meta.json); `PRIVILEGED_TOOL_CATEGORIES`
(R4: untouched); `daemon/routers/metrics.py` (R8: frozen); `notify_user`/board tools (R3).

## Tool Signature + Arguments

```python
@tool
def report_anomaly(
    category: str,
    description: str,
    severity: str = "major",
    suspected_cause: str | None = None,
) -> str: ...
```

| Arg | Type | Required | Cap | Semantics |
|---|---|---|---|---|
| `category` | str | yes | raw ≤64 chars (truncate; raw preserved as `category_raw`) | normalized per R1 (lowercase → strip → exact-or-synonym) into `ANOMALY_CATEGORIES`; no match → `other`; fold recorded in `category_fold_notice` |
| `description` | str | yes | ≤1000 chars (truncate; envelope notes `truncated=true`) | what is wrong; scrubbed before persist; empty/whitespace → `invalid-input` refusal |
| `severity` | str | no (default `"major"`) | — | normalize lowercase/strip; must be in `minor\|major\|critical` else `invalid-input` refusal (R3: never silently fold severity) |
| `suspected_cause` | str \| None | no | ≤500 chars (truncate) | reporter's hypothesis; scrubbed; nullable column |

Factory (metrics-read parity, `daemon/tools/metrics_read.py:1332`):
`create_nest_report_tools(manager, *, current_instance_id="", agent_id="", agent_tag=None) -> list`.
`agent_id` is captured at factory time (the layer that phase 2's per-agent rate limits key on).
The tool body dereferences `manager` handles PER CALL — a degraded boot leaves the tool
PRESENT and refusing `tool-disabled`, never silently vanished (the metrics-read RULING-1 shape).

## Refusal Ladder + Envelopes (house shape)

Refusal: `Error: {label} REFUSED — reason={token}: {message}` (house `REFUSED —` idiom,
e.g. `daemon/tools/filesystem.py:103`). Tokens, checked in this order:

| # | Token | Condition | Ledger row? |
|---|---|---|---|
| 1 | `tool-disabled` | kill-switch `ENSEMBLE_NEST_REPORT_ENABLED=false` or config absent, OR the writer's agent-type id is on the AM-16 deny list `ENSEMBLE_NEST_REPORT_DISABLED_AGENTS` (comma-separated; checked PRE-rate-gate; that writer only; restart-required, documented) | no |
| 2 | `invalid-input` | empty description; severity out of set; non-string arg types | no (authored-content validation; nothing persisted) |
| 3 | `rate-limited` | phase 2 (R5b); ledger row ONLY up to `refusal_ledger_max_per_agent` (default **10**/10min — AM-9): beyond it the SAME envelope returns with **NO ledger row** (the metric is the scalable signal; the ledger is for morning review) | yes (phase 2), CAPPED: outcome=rate_limited, harvest columns NULL, cluster_id NULL |
| 4 | `capacity-exhausted` | **AM-9** pre-INSERT caps exceeded: `max_rows_global` (**50,000**) or `max_rows_per_tree_root` (**2,000**) (served by `idx_anomaly_reports_outcome_created` / `idx_anomaly_reports_tree_root_created`); also the AM-9 pre-harvest global ceiling `max_accepted_per_hour_global` (**5,000**/hour, capacity family); **F-1: the global TOTAL-row bound (accepted + refused) fires HERE on the refusal path too — a rate-limited call at/over the ceiling returns `capacity-exhausted`, NO ledger row** | **no ledger row** |
| 5 | `persist-failed` | INSERT failed; ONE minimal retry row (evidence NULL, outcome=persist-failed, refusal_reason=error token); if that also fails → refusal + WARN, no ledger | yes (best-effort) |

Success envelope (single string, field-per-line):

```
OK: anomaly accepted
report_id=<uuid>
cluster_id=<uuid | pending-phase-2>
cluster_report_count=<1 in phase 1; real in phase 2>
cluster_first_report_at=<ISO-8601>
category_fold_notice=<ONLY when canonical != raw: 'category "<raw>" folded to "<canonical>"'>
[truncated=true note when description/suspected_cause/category_raw were capped]
```

## Config Knobs — `NestReportConfig` (`daemon/config.py`, beside NotifyUserConfig `:3085`)

`model_config = SettingsConfigDict(env_prefix="ENSEMBLE_NEST_REPORT_")` — the N8 fold-2
naming convention (`NotifyUserConfig` docstring verbatim precedent); the kill-switch field
`enabled` yields `ENSEMBLE_NEST_REPORT_ENABLED` exactly as briefed.

| Field | Default | Env (`ENSEMBLE_NEST_REPORT_*`) | Ruling |
|---|---|---|---|
| `enabled` | `True` | `..._ENABLED` | kill-switch (N9 mirror) |
| `rate_window_seconds` | 600 | `..._RATE_WINDOW_SECONDS` | R5b 10-min window |
| `rate_max_per_agent` | 5 | `..._RATE_MAX_PER_AGENT` | R5b minor/major cap |
| `critical_window_seconds` | 3600 | `..._CRITICAL_WINDOW_SECONDS` | R5b critical window |
| `critical_max_per_agent` | 20 | `..._CRITICAL_MAX_PER_AGENT` | R5b critical cap |
| `excerpt_max_messages` | 5 | `..._EXCERPT_MAX_MESSAGES` | R6 |
| `excerpt_max_chars_per_message` | 500 | `..._EXCERPT_MAX_CHARS_PER_MESSAGE` | R6 |
| `excerpt_max_total_chars` | 2000 | `..._EXCERPT_MAX_TOTAL_CHARS` | R6 |
| `cluster_window_seconds` | 900 | `..._CLUSTER_WINDOW_SECONDS` | R5 W=15min |
| `resolve_window_seconds` | 3600 | `..._RESOLVE_WINDOW_SECONDS` | R5 1h derived resolution |
| `wedge_match_window_seconds` | 300 | `..._WEDGE_MATCH_WINDOW_SECONDS` | captured_at ± window match |
| `description_max_chars` | 1000 | `..._DESCRIPTION_MAX_CHARS` | arg cap |
| `suspected_cause_max_chars` | 500 | `..._SUSPECTED_CAUSE_MAX_CHARS` | arg cap |
| `retention_days` | 30 | `..._RETENTION_DAYS` | R6 |
| `retention_batch_size` | 500 | `..._RETENTION_BATCH_SIZE` | R6 batched DELETE |
| `refusal_ledger_max_per_agent` | 10 | `..._REFUSAL_LEDGER_MAX_PER_AGENT` | AM-9 flood fence (no-row-beyond) |
| `max_rows_global` | 50000 | `..._MAX_ROWS_GLOBAL` | AM-9 **SINGLE TOTAL-row bound (accepted + refused — F-1); also gates the refusal-ledger INSERT** |
| `max_rows_per_tree_root` | 2000 | `..._MAX_ROWS_PER_TREE_ROOT` | AM-9 pre-INSERT cap (accept path only — inapplicable to NULL-tree_root refusal rows, F-1) |
| `max_accepted_per_hour_global` | 5000 | `..._MAX_ACCEPTED_PER_HOUR_GLOBAL` | AM-9 pre-harvest ceiling |
| `disabled_agents` | `""` | `ENSEMBLE_NEST_REPORT_DISABLED_AGENTS` (special full-env form per AM-16) | comma-separated agent-type ids; per-writer deny; restart-required |
| `max_board_items_per_tree_root_per_hour` | 3 | `..._MAX_BOARD_ITEMS_PER_TREE_ROOT_PER_HOUR` | AM-13 (enforced in phase 2's board sink) |
| `max_board_items_per_hour_global` | 20 | `..._MAX_BOARD_ITEMS_PER_HOUR_GLOBAL` | AM-13 (enforced in phase 2's board sink); **W-6: 50→20 — 20/h ⇒ 480/day matches the architect's stated harm bound and trips under a uniform multi-tree flood** |

AM-10 field-doc obligation: the budget is per agent **TYPE** (`agent_id` = the type,
`instance.py:3015-3023`) — spawning does NOT multiply it; a type's instances SHARE one
budget (noisy neighbour); the key space is NOT agent-expandable. `NestReportConfig` field
docs + CATEGORY_DOC state this explicitly. AM-16: the `enabled` field doc carries the
conditional default-ON note (cross-ref decisions.md Leader decision (a)).

## SQLModel Models — VERBATIM-SPEC

Timestamps: TEXT via a local `_utc_now_iso` writing the **FIXED-WIDTH** form
`datetime.now(timezone.utc).isoformat(timespec='microseconds')` — always 32 chars, always
`+00:00` (**AM-4**: a DOCUMENTED divergence from the notify_audit sibling
(`models.py:67`/`:158`) — whose shape IS the hazard; lexicographic compare == chronological
for this single producer, two workers independently verified; pin
`timestamp_shape_is_fixed_width`, regex `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\+00:00$`).
ALL cutoffs are computed in Python and bound as parameters — never SQL `now() - interval`
(AM-4; house idiom `notify_audit/repository.py:246-253`).
Index names: `idx_<table>_<cols>`, byte-identical `.sql` ↔ `__table_args__` (R7;
notify pattern `idx_notify_audit_agent_created` at `:135-146`).

### `anomaly_reports` (table=True)

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | str (uuid4 hex), PK | no | factory | |
| `created_at` | TEXT | no | `_utc_now_iso` | |
| `agent_id` | TEXT | no | — | factory-captured reporter agent |
| `instance_id` | TEXT | no | — | factory-captured reporter instance |
| `tree_root_instance_id` | TEXT | **YES** | NULL | harvest product; NULL on rate_limited/persist-failed rows (R5b: refused = no ancestor walk) |
| `parent_chain` | JSON | no | `[]` | ancestor instance ids **parent → root, SELF EXCLUDED** (advisory 7, fold round 3 — the helper's actual behavior: `get_ancestor_ids` appends `parent_id` only, never the starting id, `repository.py:1217-1235`; the reporter's own id is already the `instance_id` column); empty list when no parent |
| `parent_chain_agents` | JSON | no | `[]` | aligned agent labels per chain entry (`get_agent_ids_for` `:1237`) |
| `category` | TEXT | no | — | CANONICAL only (R1) |
| `category_raw` | TEXT | no | — | ≤64, verbatim input |
| `severity` | TEXT | no | — | `minor\|major\|critical` (R3) |
| `description` | TEXT | no | — | scrubbed, ≤1000 |
| `suspected_cause` | TEXT | yes | NULL | scrubbed, ≤500 |
| `excerpt` | TEXT | yes | NULL | scrubbed, ≤2000 total |
| `excerpt_sha256` | TEXT | yes | NULL | of the STORED excerpt |
| `excerpt_length` | int | yes | NULL | of the STORED excerpt |
| `job_id` | TEXT | yes | NULL | from the reporter's active task row (harvest) |
| `mission_id` | TEXT | yes | NULL | same |
| `dispatch_chain` | JSON | yes | NULL | `DISPATCH_CHAIN_KEY` from task metadata (`job_queue_service.py:82`, minted `:1461-1463`, read `job_processor.py:57`) |
| `boot_epoch` | TEXT | yes | NULL | `get_boot_epoch()` (`boot_epoch.py:70`) ISO-formatted |
| `registry_summary_sha256` | TEXT | yes | NULL | sha256 of the families() summary JSON (sort_keys), NEVER payloads |
| `registry_captured_at` | TEXT | yes | NULL | when the summary was hashed |
| `wedge_capture_ids` | JSON | yes | NULL | capture id refs ONLY — payloads never read (R6/R5b) |
| `cluster_id` | TEXT | yes | NULL | set on accept (phase 2 real assignment); FK-ish (no DB FK constraint — house pattern) |
| `outcome` | TEXT | no | — | `accepted \| rate_limited \| persist-failed \| unclustered` (AM-5 adds `unclustered`: the TERMINAL orphan state — `accepted` + `cluster_id IS NULL` rows are terminalized by the phase-2 sweep, NEVER reassigned) |
| `refusal_reason` | TEXT | yes | NULL | token + short detail when refused |

Indexes (`__table_args__`): `idx_anomaly_reports_agent_created (agent_id, created_at)` ·
`idx_anomaly_reports_cluster_id (cluster_id)` ·
`idx_anomaly_reports_tree_root_created (tree_root_instance_id, created_at)` ·
`idx_anomaly_reports_category_created (category, created_at)` ·
`idx_anomaly_reports_outcome_created (outcome, created_at)`.
(The agent+created index also serves phase 2's rate-limit COUNT; the outcome+created index
serves retention and refusal-census queries.)

### `anomaly_clusters` (table=True)

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | str (uuid4 hex), PK | no | factory | assigned ONCE (R5 stability) |
| `cluster_key_hash` | TEXT | no | — | sha256 of `"{tree_root}|{canonical category}"` |
| `tree_root_instance_id` | TEXT | no | — | key component |
| `category` | TEXT | no | — | canonical; key component |
| `first_report_at` | TEXT | no | — | |
| `last_report_at` | TEXT | no | — | sliding-window anchor (R5) |
| `report_count` | int | no | 1 | incremental |
| `max_severity` | TEXT | no | — | max-rank rollup (`minor<major<critical`) |
| `board_item_id` | TEXT | yes | NULL | set by phase 2's critical gate |
| `created_at` | TEXT | no | `_utc_now_iso` | |

Index (the R5 ONE pick, justified in decisions R5):
`idx_anomaly_clusters_key_window (tree_root_instance_id, category, last_report_at)` —
serves the write-time lookup (`key equality + last_report_at >= cutoff, ORDER BY
last_report_at DESC LIMIT 1`) as a single seek. `UNIQUE(cluster_key_hash)` REJECTED: the
sliding window legitimately INSERTs a second cluster for the same key after a quiet period
(a re-incident); a full unique would IntegrityError on exactly that path, and a partial
unique is impossible because resolution is DERIVED (no open flag exists to index).

## Evidence Harvest Table (DB-touching seams OFF-LOOP via `db_offload.offload`, `daemon/services/db_offload.py:386` — the v0.27.14 discipline; **AM-8b exception: `families()` runs IN-LINE, never inside an offload worker**)

| Fact | Seam (file:line) | Cost | Bound | Failure mode |
|---|---|---|---|---|
| ancestor chain + tree_root | `daemon/repositories/instance/repository.py:1217 get_ancestor_ids` (cap `_MAX_TRAVERSAL_DEPTH=256`, `:50`) | 1 off-loop call | ≤256 hops; returns **parent→root, self excluded** (advisory 7); chain truncated at cap, tree_root = **last element of the chain, or the reporter itself when the chain is empty** + WARN in log (not in report) | exception → chain `[]`, tree_root NULL, report still persists (evidence-loss ≠ refusal) |
| agent labels | `repository.py:1237 get_agent_ids_for` (ONE query) | 1 off-loop call | chain length | exception → `""` entries; labels never block |
| job/mission/dispatch_chain | reporter's active task row lookup; metadata key `job_queue_service.py:82`, minted `:1461-1463`, read `job_processor.py:57` | 1 off-loop query | one row | no active task → all NULL |
| transcript excerpt | **AM-8a SEAM SWAP** — the bounded `instance_read.py:889-909` pattern: `manager.checkpointer.raw_saver.aget(...)` + `messages[-N:]` (N ≤ 20, `MAX_LAST_MESSAGES` precedent `instance_read.py:154`) + per-message truncation. The plan's former `get_instance_messages` seam is WRONG (full materialize + full serialize + full prompt reconstruction; adjacent to the O(N²) family). The read cap is enforced AT THE READ SEAM — M-1g (remove the cap) must red against the seam, not a post-read trim. **SKIPPED ENTIRELY for minor severity** (AM-8e: no harvest, no column, no cost — excerpt persists for major + critical ONLY) | 1 off-loop call (`aget` materializes checkpoint state; only the tail slice is kept) | ≤20 messages sliced, per-message truncation, ≤500 chars/part, ≤2000 total | read fail/empty → excerpt NULL (fail-closed rules in the scrub section apply) |
| registry snapshot ref | `manager.metrics_query_provider.families()` per the provider discipline (`metrics_read.py:44-49`) — hash `json.dumps(summary, sort_keys=True)` → sha256 + captured_at; payloads NEVER copied (holds — `provider.py:211`/`checkpoint_metrics.py:1067-1069`) | **AM-8b: called IN-LINE, OUTSIDE any offload worker** — it takes the REGISTRY-LEVEL lock; running it inside a worker holds a scarce 25-slot worker behind that lock (reentrancy guard family `db_offload.py:379-385`) | one hash + one timestamp | provider absent/raises → both NULL + WARN |
| wedge cross-link | `daemon/repositories/wedge_captures/repository.py:94 list_recent(limit=50)`; match `captured_at` within ±`wedge_match_window_seconds` AND same `boot_epoch` — **AM-8d: `boot_epoch` canonicalized to NAIVE-UTC on BOTH sides before any compare** (the wedge subsystem has TWO shapes: watchdog naive `wedge_capture/watchdog.py:591`; boot-record `isoformat()+'Z'` `boot_record.py:122-133`); store capture_id refs ONLY | 1 off-loop query (index-served) | ≤50 scanned; ≤ matched ids stored | repo absent → NULL |
| boot epoch | `daemon/services/boot_epoch.py:70 get_boot_epoch()` — **AM-8d: pinned to the NAIVE-UTC form (no offset — `:70-75`, the watchdog writer's shape)**; stored as-is; NEVER compared against `+00:00`/`Z`-shaped values as raw strings | free (O(1) in-RAM) | one value | None → NULL |
| timestamps | local `_utc_now_iso` (AM-4 fixed-width form) | free | — | — |

**Honest cost ceiling (AM-8c — supersedes the former "≤4 round-trips" claim): 5 off-loop
CALLS.** The ancestor walk is **O(chain-depth ≤ 256) SEQUENTIAL PK reads inside ONE call**
(`instance/repository.py:1217-1235` — disclosed, NOT one round trip); agent labels 1;
task-row lookup 1; excerpt `aget` 1; wedge `list_recent` 1 (index-served); `boot_epoch`
O(1) in-RAM; `families()` in-line off-worker. Every harvest failure degrades THAT field to
NULL and the report still persists — evidence harvest NEVER refuses a report (only tokens
1/2/4 refuse, pre-harvest). **AM-14/NR-10 disclosure:** ~5 offloads/report against the
25-slot strict-admission executor — under a burst, offload-gate refusal ⇒ evidence fields
NULL, report still accepted (CATEGORY_DOC disclosure; systemic pressure → the
DB-pool/offload lane).

## Scrub Pipeline (R6 + AM-11 rewrite, order fixed)

For every persisted TEXT (description, suspected_cause, excerpt parts):
0. **`redact_secret_shapes(text, limit)` (AM-11, NEW additive function in
   `daemon/_redact.py`)** — masks the empirically-surviving shapes: `sk-[A-Za-z0-9_\-]{8,}`,
   `Bearer\s+\S+`, `(?i)(api[_-]?key|token|password|secret|access[_-]?key)\s*[:=]\s*\S+`,
   `(?i)\?[^\s]*(key|token|secret)=[^&\s]+`, and env-var shapes `*_API_KEY=` / `*_TOKEN=` /
   `*_PASSWORD=` / `*_SECRET=`. Applied FIRST. `redact_exc_str` itself is UNTOUCHED (its
   callers are pinned).
1. `redact_exc_str(text, limit=<field cap>)` — `daemon/_redact.py:74-98`; pure shape
   masker (URI netloc / libpq DSN / KV DSN / IPv4:port); never raises; limit AFTER redaction.
2. `scrub_enqueued_body(text, actor="nest_report")` —
   `daemon/services/secret_persistence_scrub.py:1250` (exact-match on STORED secret values).
3. Length-cap truncate to the field cap (post-scrub).
Excerpt additionally: assemble ≤5 parts ≤500 chars each within ≤2000 total, then compute
`excerpt_sha256` + `excerpt_length` over the STORED text.
Scrubber unavailable (`get_process_scrubber()` None, `:263`) — **AM-11 SPLIT posture**:
the **EXCERPT column flips FAIL-CLOSED** (persist NULL + WARN) — it is raw tool stdout
(`bash.py:1152` returns `STDOUT:` verbatim, 150k cap) and is the highest-risk field;
**description/suspected_cause KEEP the fail-open shape** (steps 0+1 still apply — the
pattern masker is the always-on floor) + WARN; the CATEGORY_DOC disclosure states both
legs. Scrub-gap seams (nomask `system.py:357-422`, env-inherit `bash.py:287-302`,
`proc_tools.py:496`) remain known-open, disclosed in decisions R6, NOT fixed here.
**Planted-secret pin (AM-11.4): plants a NON-STORED shape** (e.g. `sk-proj-…` inside a
transcript tool message) — a `secret_store`-planted value is satisfied by the exact-match
path and proves nothing about the real risk; mutation legs per the new pattern.

## 10-Step Registration Checklist (applied; source `daemon/tools/upgrade_tools.py:150-184`)

1. `daemon/tools/nest_report.py` — factory builds the `@tool` inside the closure; each
   tool carries `@register_tool_category("nest-report")` ABOVE `@tool`. CATEGORY_DOC
   header documents: purpose; the refusal ladder incl. **the AM-11 split disclosure**
   (excerpt fail-CLOSED, description/suspected_cause fail-open on scrubber-absent);
   **the AM-10 per-TYPE budget semantics** (agent_id = the type; instances share one
   budget; spawning does not multiply it; key space not agent-expandable); **the AM-14
   offload-gate pressure** (~5 offloads/report vs the 25-slot strict-admission executor;
   burst ⇒ NULL evidence fields, report still accepted); **the AM-16 conditional
   default-ON** (cross-ref decisions.md Leader decision (a)); closed vocabulary; severity
   tiers.
2. `daemon/tools/_tool_registry.py` CATEGORY_MODULES += `"nest-report":
   "daemon.tools.nest_report"`.
3. `daemon/tools/_tool_registry.py` DYNAMIC_TOOL_NAMES += `{"report_anomaly"}`.
4. KNOWN_TOOL_NAMES REGENERATE via `discover_source_only_tool_names()` and paste; drift
   test `tests/unit/tools/test_frozen_tool_name_discovery.py::
   test_known_tool_names_matches_source_exactly_no_drift` must pass.
5. `daemon/tools/instance.py` `create_instance_tools` — the CRITICAL list-append:
   `tools.extend(create_nest_report_tools(manager, current_instance_id=..., agent_id=...,
   agent_tag=version_tag))` placed BEFORE `create_help_tool` / `scan_tools_for_full_docs` /
   `_apply_tool_filter` (decorator-only registration is SILENTLY INVISIBLE — precedent
   block `:6624-6663` states this verbatim).
6. `tools.allow` expansion — no code change per category (checklist step 6).
7. `PRIVILEGED_TOOL_CATEGORIES` UNTOUCHED — R4: `nest-report` is NON-privileged;
   empty-allow agents inherit via the default universe (`instance.py:551-561`). Document
   the inversion in the category comment (privileged checklist step 7 does not apply).
8. meta.json `tools.allow` += `"nest-report"` — DEFERRED to phase 5 (R4). This phase
   touches NO `agents/**` file.
9. Tool body + service + repository (the deliverables above) — write-side helpers live in
   `daemon/services/anomaly_service.py`, not in the tool body.
10. Docs — no docs/ catalog to update; `tool_help` reads `_full_doc_` (checklist step 10).

**Plus the two lane-critical steps (R4, non-negotiable, same change-set):**

- **Cold-boot warm list:** `daemon/loader.py` — add `from .tools.nest_report import
  create_nest_report_tools` to the warm-import block (`:519-528` pattern) and a stub
  construction `create_nest_report_tools(None)` in the dummy-instance block
  (`:560-566` pattern) so the cold-boot metadata scan registers the category before the
  no-TTL prompt cache pins tool docs (the W1-FIX-NOW class).
- **Artifact + vocabulary:** run
  `ENSEMBLE_REGENERATE_TOOL_CATEGORY_ARTIFACT=/tmp/tool-categories.json uv run python -m
  pytest tests/unit/tools/test_tool_category_artifact.py -q` then copy the artifact
  (recipe verbatim at `test_tool_category_artifact.py:17-21`) AND bump
  `VOCABULARY_VERSION` 3→4 at `daemon/checkpoint_metrics.py:218` in the SAME commit —
  the v0.28.9 F-1 trap (stale committed artifact → net-new reds in every later gate).

## Migrations (R7)

- `20261105_HHMMSS_create_anomaly_reports.sql` + `20261105_HHMMSS_create_anomaly_clusters.sql`
  (prefixes derived at implementation; > `20261031_120002`; census 119→121, 121 distinct).
- Canonical shape: `20261025_000001_create_notify_audit.sql` + `daemon/migrations/README.md`.
- `IF NOT EXISTS` everywhere; TEXT everywhere; `-- UP` required; `-- DOWN` present but
  never executed (rollback never runs migrations — runner is SQLite-only, PG arrives via
  create_all; the DOWN text is audit documentation); JSON columns dual-face
  (SQLite JSON / PG JSONBType); index names byte-identical to `__table_args__`.
- Model late-import into `daemon/api.py`'s metadata block BEFORE `create_all`.

## Sequencing Note (explicit, disclose in review)

Phase 1 ships WITHOUT rate limits, real clustering, board, and retention — those are
phase 2 per the dispatch split. The phase-1 accept path writes `cluster_id=NULL` /
`cluster_report_count=1` / `cluster_first_report_at=created_at` and the envelope carries
`cluster_id=pending-phase-2`. **The exposure window is bounded to this lane: phases 1-2
stage TOGETHER at phase 6 — no interim promote exists, so the unrate-limited window never
reaches a live binary.** The `rate-limited` token and the ledger row shape are defined NOW
(model + envelope) so phase 2 is enforcement-only.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Models + repository (`daemon/repositories/anomaly/`) per the verbatim spec | none | both classes import; `create_all` on scratch SQLite + scratch PG yields both tables with all 7+1 indexes, names byte-identical to the `.sql` files |
| 2 | Migration files (R7) | task 1 | census 121 files / 121 distinct prefixes; fresh SQLite applies both; prefixes > 20261031_120002 |
| 3 | `NestReportConfig` in `daemon/config.py` + Config wiring | none | kill-switch env flips behavior; ALL AM-9/AM-13/AM-16 knobs present (`refusal_ledger_max_per_agent`, `max_rows_global`, `max_rows_per_tree_root`, `max_accepted_per_hour_global`, `disabled_agents`, board ceilings); AM-10 sharing disclosure + AM-16 conditionality in field docs; `_TRACKED_ENV_PREFIXES`-style coverage per NotifyUserConfig docstring precedent |
| 4 | `ANOMALY_CATEGORIES` + severities in `wiring/vocab.py` | none | tuple + `__all__`; normalization unit map (synonyms → canonical; unknown → `other`) green |
| 5 | `anomaly_service.py` accept path: validate → scrub (AM-11 pipeline) → harvest (AM-8 seams) → persist | tasks 1,3,4 | unit packs: each harvest seam failure → field NULL + accepted row; scrub probe plants a NON-STORED shape, absent from every column; excerpt fail-closed / description fail-open split proven; minor severity skips the transcript read |
| 6 | Tool + factory `daemon/tools/nest_report.py` with CATEGORY_DOC | task 5 | signature/caps/refusal ladder per spec; `tool-disabled` and `invalid-input` legs typed and pinned |
| 7 | Registration checklist steps 1-7,10 (`_tool_registry.py`, `instance.py` extend, warm list) | task 6 | drift test green; extend precedes help/scan/filter (order assertion); empty-allow agent sees the tool in its resolved set |
| 8 | Regenerate the tool-categories artifact via the HOUSE GENERATOR: `ENSEMBLE_REGENERATE_TOOL_CATEGORY_ARTIFACT=/tmp/tool-categories.json uv run python -m pytest tests/unit/tools/test_tool_category_artifact.py -q`, then copy from the scratch path into `.agents/shared/metrics/tool-categories.json` — NEVER hand-edit (the F-1 trap; `tests/unit/tools/test_tool_category_artifact.py:14-21`). MUST land in the SAME commit as task 9 | task 7 | artifact drift check green WITHOUT hand-edit; scratch-path recipe executed verbatim |
| 9 | Bump `VOCABULARY_VERSION` 3→4 at `daemon/checkpoint_metrics.py:218` — SEPARATE numbered action from task 8, but the SAME commit (artifact + vocabulary are one atomic change-set; the v0.28.9 F-1 trap class) | task 8 (same commit) | `VOCABULARY_VERSION == 4` asserted in pack; one commit carries both task 8 and task 9 |
| 10 | Tier-1 gate pack | tasks 1-9 | pack green; net-new reds vs TRUE base `1f6898ae2` = 0 (adjudicated) |

## Gate Class + Acceptance Criteria

**Gate class:** Tier-1 (scoped packs on the lane surface; the full whole-tree/Tier-2 and
staging gates belong to phase 6). Net-new-red discipline measured against the TRUE base
`1f6898ae2` (the worktree HEAD), not any intermediate lane state.

**Acceptance (phase-scoped):**
- AC1: signature + caps exact (description ≤1000, suspected_cause ≤500, category_raw ≤64);
  over-length inputs truncate with an envelope note, never refuse.
- AC2: refusal legs `tool-disabled` (kill-switch on/off **+ the AM-16 deny-list leg**: a
  deny-listed writer gets `tool-disabled` while a non-listed writer in the same boot is
  unaffected) and `invalid-input` (empty description; `severity="catastrophic"`) produce
  the exact house envelope strings and persist NOTHING; `capacity-exhausted` defined and
  token-shaped (phase 2 enforces).
- AC3: success envelope exact-field parse on a scratch-DB boot through the REAL factory;
  row outcome=accepted; `category_fold_notice` present iff a fold happened; absent otherwise.
- AC4: scrub probe (AM-11) — plants a **NON-STORED** secret shape (`sk-proj-…` inside a
  transcript tool message): it never appears in any persisted column; redact shapes
  (DSN/URI/IPv4 + the AM-11 pattern set) masked; with the scrubber forced absent
  (`get_process_scrubber` → None): **excerpt persists NULL + WARN (fail-CLOSED) while
  description/suspected_cause still persist (pattern-masked, fail-open)** — both legs
  asserted.
- AC5: registration completeness — frozen-name drift test green; tool present for an
  empty-allow agent (default universe) and ABSENT for an explicit-allow agent without the
  category (until phase 5); artifact regen + vocabulary bump landed in one commit.
- AC6: migrations — 121/121 distinct prefixes; fresh SQLite boot applies both; scratch PG
  create_all yields both tables; `.sql`↔model index-name diff empty.
- AC7 (AM-4 + AM-8d): every persisted timestamp matches
  `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\+00:00$` (`timestamp_shape_is_fixed_width`);
  the stored `boot_epoch` is NAIVE-UTC (no offset) and the wedge-match canonicalization
  handles the `Z`-shaped sibling without raw-string compare.

## Risks (phase-specific)

| Risk | Impact | Mitigation |
|---|---|---|
| Instance.py extend placed after help/scan/filter → tool invisible or filter-bypassing | High | order assertion in the Tier-1 pack (precedent comment `:6624-6663` documents the failure class) |
| F-1 trap recurrence (artifact/vocabulary drift) | Medium | regen + bump mandated SAME change-set; AC5 asserts both |
| Harvest seam latency on the event loop | High | every DB seam via `db_offload.offload` (`db_offload.py:386`); the v0.27.14 incident class is a named review check |
| Scrub-gap leakage into excerpt | Medium | redact-first + caps + hash/length; gaps disclosed (R6), never silently widened |
| tree_root NULL on refusal rows confuses later queries | Low | phase 2 lookups always bind tree_root (NULL rows never match); retention/census queries key on outcome+created |
