# Controls & Phasing — Observability, Kill Switches, Rollout

> **Status:** **RATIFIED P1 SHADOW-ONLY — Emperor ratification 2026-11-09: "yeah, look good, go ahead, shadow only for now".** Design of record. P1 implementation authorised (client + registry + shadow instrumentation); seams 2/3/4 unregistered; S4 shadow-only. (The leader's giter commits at finalize.)
> **Scope items:** 4 (observability) + 5 (kill switches) + 6 (phasing). **Normative source:** `scope-and-doctrine.md` — cited by §id, never re-quoted.
> **Base:** `docs/decision-module` @ `5da1021f2`

---

## §1 The control posture in one paragraph

The overlay is **optional at every level**: it may be absent by configuration (D1), it may be switched off per seam, and it may be shadowing rather than deciding (D3). Every control below exists to make one property provable: **switch-off ⇒ the ensemble never calls out ⇒ decisions are byte-identical to the pre-overlay system.** A control that cannot be shown to hold under that test is not a control.

---

## §2 Observability

### §2.1 What is greenfield — and what is not

**Greenfield applies only to NEW counter families.** The "all four seams have zero families" claim is **false** for S3 and S4 (verified; IP-4).

| Seam | Existing surface | Status |
|---|---|---|
| **S3** | `report_integrity_junk_report_total` — counter + INFO line + sink seam (`daemon/services/report_integrity_metrics.py:59`; name constant `daemon/constants.py:1621`), plus structured log telemetry from the `agent_repair_ghost` node (`daemon/graph.py:2738-2745` (`:4370-4378` is the **comment** describing it)) | **not greenfield** |
| **S4** | the `leader_completion_gate` row family (`daemon/services/attestation_gate.py:2095-2138`, **29 key=value fields** — 28 format slots + the literal `event=`) + the three promotion counters `dry_log_total` / `dry_log_deny_predicate_total` / `enforce_denied_total` (`daemon/services/attestation_resolver.py:123-130`) | **not greenfield** |
| **S1**, **S2** | none | greenfield (S2 deferred) |

**There is no Prometheus/OTel client in this repository** ("NO metrics infra", `daemon/services/report_integrity_metrics.py:8`). A "metric family" here is a **module counter / structured log row / sink seam**, so the overlay's families are specified in that same idiom. The new global families below follow the house registration convention: **registration-time kwargs** (`register_counter` / `register_gauge` / `register_histogram`, `daemon/checkpoint_metrics.py:1139-1243`) and the standard metadata — `family`, `source_idiom`, `durability`, `cardinality_weight`, `chart_role` (`:618-665`). Buffers are 15 s cadence ring buffers with pull adapters behind the `/api/metrics` door. **Roster rule.** The 13 chart-primary family roster invariant **fails loud** (`roster.py:259-282`). These families are **operational**, not chart-primary: they join the roster **only** if a chart genuinely needs them. Label vocabulary is derived from the existing code vocabulary (`wiring/vocab.py`), not invented.

### §2.2 Global families (registry-owned)

| Family | Type | Labels | Answers |
|---|---|---|---|
| `ensemble_decision_overlay_attempts_total` | counter | `seam_id`, `mode` (`shadow`\|`deciding`) | How many overlay calls were made? |
**Label-roster invariant (R-A):** the §6 table has **30 rows** — rows **1–5** are **pre-flight (suppressed — 3 of the 9-label closed vocabulary; see §2.2)** and rows **6–30** are the **25 failure values** carrying **19 DISTINCT** `failure_class` labels (`connect_error`, `timeout`, `http_400`, `http_401`, `http_403`, `http_404`, `http_405`, `http_413`, `http_500`, `http_502`, `http_504`, `http_other`, `malformed_body`, `unmatched_answer`, `malformed_answers`, `refusal`, `too_large`, `below_floor`, `seam_fault`). **`seam_fault` IS a member of `failure_class`** — it is a failure of the overlay machinery regardless of whether a socket was opened; **cost accounting is unchanged** (only socket-reached attempts bill). **Suppressions are CONTROL decisions where no consult was attempted** (switch off/unreadable, breaker open, rate/concurrency budget, pre-flight validation). **The DISTINCT label count — 19 — is what the metric-label roster invariant binds to.**

| `ensemble_decision_overlay_failures_total` | counter | `seam_id`, `failure_class`, **`mode`** | Which failure class, how often, in which mode? The `failure_class` label set is the **25 failure values** of `thin-client.md` §6 (**rows 6–30**; **19 distinct labels**, R-A) — the five pre-flight rows are **not** failures and live in the suppressed vocabulary. `mode` ∈ {`shadow`, `deciding`} — required so a shadow failure is never conflated with a deciding failure |
| `ensemble_decision_overlay_fallbacks_total` | counter | `seam_id`, **`mode`** | How often did the heuristic absorb a failure? **The identity (R-B, stated once) is `fallbacks_total == failures_total`, scoped to `mode=deciding`.** `floor_rejections_total` is **NOT** an additive term — it is a **labeled subset view**: `floor_rejections_total == failures_total{failure_class="below_floor"}`. `below_floor` is counted **once, as a failure**; there is no double-count. In `mode=shadow` the chat call still ships, so the heuristic absorbs nothing while `failures_total` increments — the `mode` label makes the identity checkable **per configuration** |
| `ensemble_decision_overlay_latency_seconds` | histogram | `seam_id` | Is p95 inside the per-lane budget? |
| `ensemble_decision_overlay_cost_total` | counter | `seam_id` | Cost per decision; feeds the global ceiling and the auto-trip |
| `ensemble_decision_overlay_floor_rejections_total` | counter | `seam_id`, **`mode`** | How often a **successful** call returned an answer that did not clear the seam's confidence floor (`thin-client.md` §8.2.1)? `below_floor` is **already counted once as a failure** in `failures_total`; this family is its **subset view** (R-B), not an additional count |
| `ensemble_decision_overlay_suppressed_total` | counter | `seam_id`, `reason` | How often was the overlay skipped? **Reason vocabulary (closed, 10):** `not_configured`, `invalid_request`, `candidate_set_too_small` (the **five** pre-flight rows, 1–5, of `thin-client.md` §6; `not_configured` covers rows 1–3), `switch_off`, `switch_unreadable`, `excluded_seam`, `breaker_open`, `rate_limited`, `concurrency_limited`, `pricing_unconfigured` (L8: no cost figure available for the model — see §5). **Disjoint from `failure_class`** — the two vocabularies never overlap |

The `fallbacks_total == failures_total` identity (**`mode=deciding` only**, R-B) is the **fail-open invariant made observable** (D2). A non-zero gap means some failure produced an error instead of the heuristic result — the one thing D2 forbids.

### §2.3 Shadow families (D3)

| Family | Type | Labels | Answers |
|---|---|---|---|
| `ensemble_decision_overlay_shadow_agreement_total` | counter | `seam_id`, `verdict` (`agree`\|`disagree`) | Does the overlay agree with the heuristic? (aggregated per seam) |
| `ensemble_decision_overlay_shadow_disagreement_total` | counter | `seam_id` | Where does it differ? (aggregated per seam) |
| `ensemble_decision_overlay_shadow_latency_seconds` | histogram | `seam_id` | Overlay-only latency, distinct from the end-to-end latency |

**`question_id`** is a **stable, opaque, non-content** identifier for the question (e.g. a hash of the seam id + a non-content discriminator) — it must never carry user text. It appears in the **shadow-log row** (`controls-and-phasing.md` §2.6), NOT as a metric label: the wiring registrar refuses a label key with no `allowed_values` set, and an unbounded per-question series blows `DEFAULT_CARDINALITY_CAP=2000` with excess increments silently dropped. The per-question A/B signal is shadow-log-only.

### §2.4 Per-seam families

| Seam | Extra family | Note |
|---|---|---|
| **S1** | `ensemble_decision_overlay_shadow_selection_agreement_total` (counter; labels `seam_id`) + `ensemble_decision_overlay_shadow_selection_overlap_ratio` (gauge) | The overlay's selected **set** vs the heuristic's selected set — set overlap and rank agreement. S1's mapping returns a **ranked set** (the choice answer's probability distribution), so the comparison is set-level, not head-only (see `seam-registry.md` §5.1) |
| **S2** | — | deferred; no families |
| **S3** | reuse `report_integrity_junk_report_total` (`daemon/services/report_integrity_metrics.py:59`; name constant `daemon/constants.py:1621`) | the family already exists; the overlay **extends** it rather than adding a parallel one |
| **S4** | reuse the **`event=leader_completion_gate` rows** (`daemon/services/attestation_gate.py:2095-2138`, **29 key=value fields — 28 format slots + the literal `event=`**) **plus** the promotion counters `dry_log_total` / `dry_log_deny_predicate_total` / `enforce_denied_total` (`daemon/services/attestation_resolver.py:123-130`) | **named A/B baseline source** — the overlay's shadow rows extend these, not a new family |

### §2.5 The S1 import-graph constraint (CORRECTED — D6 row)

`daemon/services/snapshot_metrics_service.py:31-34` is **docstring prose only** — there is no import-time guard, no assertion, no `__all__`. The **only** enforcement is the test-pinned AST walk `test_no_snapshot_module_imports_metrics` (`tests/unit/tools/test_snapshot_v3.py:1620-1635`): an AST walk over every `daemon/services/snapshot*.py` and `daemon/repositories/snapshot/*.py`, matching on dotted components (`:1597-1618`), with anti-vacuity guards on the discovery glob and the excluded file (`:1591-1594`, `:1622-1625`); it WOULD catch a new metrics-service import in `snapshot_search_service.py`, including function-level lazy imports and `from pkg import mod` re-exports. A violation breaks a unit test, not an import.

**Build constraint — the `metrics` substring trap (B-3).** `snapshot_search_service.py` is under a WHOLE-FILE substring ban: `assert "metrics" not in text or "MONITORING" in text.upper()` (`tests/unit/tools/test_snapshot_v3.py:1645`; test `:1637-1648`). The file has ZERO metric/monitor occurrences today ⇒ strict branch ⇒ any module the ranking file IMPORTS must not carry the lowercase token `metrics`. The observer module must therefore be named without the substring (e.g. an overlay-owned counter module whose name contains no `metrics`).

**Sanctioned observer placement (L4).** The ranking file imports NO observer module: the observer is **INJECTED**. `SnapshotSearchService.__init__` (`daemon/services/snapshot_search_service.py:257-281`) gains an OPTIONAL 5th kwarg (default None); `daemon/manager.py:2872-2877` (the ONE production construction site) supplies it. This is the only shape that touches NEITHER pin (AST-walk import ban AND substring ban); it satisfies the import-graph constraint **structurally**, not by convention. The registry owns counter registration; the ranking module only calls a passed-in sink.
### §2.6 Shadow-log schema and content policy

The shadow log is the **A/B record** required by D3. It is a structured log, not free text.

| Field | Content | Policy |
|---|---|---|
| `ts` | timestamp | — |
| `seam_id` | seam identifier | — |
| `question_id` | opaque, non-content id | never user text |
| `mode` | `shadow` \| `deciding` | — |
| `overlay_outcome` | typed answer only — e.g. the picked id, the score, the probability | **ids, hashes, scores only** |
| `heuristic_outcome` | the heuristic's answer in the same shape | ids only |
| `verdict` | `agree` \| `disagree` \| `no_overlay` | — |
| `latency_ms` | overlay call latency | — |
| `failure_class` | one of the **19 distinct** labels (25 values across §6 rows 6–30; R-A) | empty on success |
| `model_id` | the configured purpose-id | not the provider — the provider is credential-bound and the client does not know it (PX-4) |

**Content policy — content-free by default.**

- **NEVER** log credentials, bearer tokens, API keys, any part of the auth header, the full request payload, or the raw response body.
- **NEVER** log raw user content. For S1 this is the sharp edge: the stage-3 prompt carries snapshot **titles, tags and summaries** (`:640-642`) — those are user content and are **excluded from the shadow log**. The overlay's answer is recorded as **snapshot ids**; the text it was derived from is not. Allowed: ids, hashes, scores, probabilities, counts, latencies, enum labels.
**A shadow-writer failure never blocks.** If the writer raises — sink unavailable, serialization error, disk full — the failure is **counted** (`ensemble_decision_overlay_shadow_write_failures_total`) and **swallowed**. The writer sits on the failure-tolerant side of the fence: it may lose a row, it may **never** fail a caller. This matters most in **C1**, where the shadow write is detached from the shipped result but still must not become the reason a search fails. **Retention bound.** The shadow log is bounded on **two axes** — a maximum age (e.g. 30 days) and a maximum row count — with the tighter bound winning. Rows are aggregate-compatible so that the log can be truncated without losing the A/B signal. A shadow log that is unbounded is a content-retention hazard, not an observability feature. **Shadow row assembled BY HAND (more verified facts).** At the S1 hook point 17 names are live and 15 carry user content or model text; `Snapshot.to_dict()` returns EVERY field (title/task_summary/domain_tags/full digest) — `model_dump()`/`vars()`/a generic dump helper all LEAK. The service is DUCK-TYPED ⇒ an id extractor must tolerate arbitrary stand-ins. NO content hash exists (compute your own, consistent with this §). `_parse_selection` accepts ANY non-empty string and unknown ids are logged VERBATIM into the agent-readable daemon log ⇒ **INTERSECT AGAINST `candidates` BEFORE WRITING ANY ROW, write VALIDATED ids only.** The overlay's "no answer" surfaces as a FAILURE, never an empty selection (the pre-existing malformed-JSON→empty path contradicts the module docstring and is unpinned).

### §2.7 S4's baseline A/B source

S4 is the only seam with **best-in-class existing observability**: the `event=leader_completion_gate` rows at `daemon/services/attestation_gate.py:2095-2138`. **Field-count precision.** The emitted row carries **29 key=value fields** (28 format slots + the literal `event=`), not 17. The "17" is the FR-10 **canonical log schema** — extended by `attestation_required` as the **11th of the 17** fields (at `daemon/services/attestation_gate.py:1250`, right after `live_descendants` `:1249`) (`.agents/shared/planning/leader-completion-attestation/requirements.md:731`; exported as `daemon.services.attestation_gate.CANONICAL_LOG_SCHEMA_FIELDS`) — and the delegation / marker / length diagnostics are explicitly **outside** that tuple. The canonical 17-name roster is **not restated in-file**, so **the 28-name list is the greppable truth**. Use it.
**The existing shadow precedent.** S4 already has a dry mode: it computes activation and emits dry rows **without denying** (`daemon/services/attestation_gate.py:2160-2180`). That is the code's own precedent for this design's shadow phase — compute the decision, record it, change nothing. These rows are the **baseline** the overlay's shadow rows are compared against — the A/B for S4 is "overlay rescue proposal vs the `leader_completion_gate` row for the same completion", not a separately invented record. This is why S4, though last in sequencing, has the **clearest** measurement story.

### §2.8 The observability contract — substrate, retention, access, audit trail

| Property | Ruling |
|---|---|
| **Substrate class** | The shadow log is a **structured daemon log row** (`event=decision_overlay_shadow`) written by the registry's shadow writer into the **daemon log file** — `DAEMON_LOG_DIR` (`daemon/logging_setup.py:72`) with a `RotatingFileHandler` on `ensemble.log` (`:147`, 10 MB). It is **not** a table and **not** an unbounded in-memory ring |
| **Home and access — truthfully** | The daemon log file, one row per consult. **There is NO operator-scoped log router in `daemon/routers/`.** The log is **readable by agents holding bash/filesystem**, and the **`system-log` tool category is granted to `worker` and `maintenancer`** (`agents/worker/meta.json:28`, `agents/maintenancer/meta.json:13`). **The real control is the WRITE-TIME CONTENT POLICY** (content-free at write, §2.6) — **not** read-time access control. The earlier "operator-scoped log/metrics surfaces / not agent-readable" claim was **false** and is deleted |
| **Retention** | **Bounded by the log pipeline's own rotation** (`RotatingFileHandler`, 10 MB), **plus** an explicit **row-count cap per seam per window** enforced **at the writer** — a rotating file has **no row-count truncation of its own**, so the cap is a writer responsibility; what it drops is counted in `ensemble_decision_overlay_shadow_rows_dropped_total` |
| **Decision-audit trail** | Because the shadow log is **agent-readable**, the audit trail needs a decision — **SETTLED by Q2 (2026-11-09): content-free v1 + write-time content policy**; a non-agent-readable sink stays a **named future (v2 build requirement)**. **Option A:** a **separate operator-scoped store** (a dedicated, non-agent-readable sink) becomes the audit trail, with the shadow log remaining the content-free working record. **Option B:** an **explicitly accepted agent-readable-with-content-free posture** — the shadow log *is* the trail, mitigated by write-time content-freedom and ids-only rows. A **non-agent-readable sink is named as a candidate BUILD REQUIREMENT for the implementation commission — this design does not claim one exists.** Under **either** option two consequences bind: (i) the retention cap must cover the seam's own **A/B window (P2) plus the audit horizon** — a cap shorter than the window would destroy the evidence the phase exists to collect; (ii) because the trail is durable, a consult's record **survives the process**, so a kill cannot erase what was consulted before it |
| **What the trail records** | Per consult: seam, mode, overlay answer, heuristic answer, verdict, latency, failure class, and the switch state that permitted the consult |

**A durable trail is also a re-identification surface.** Rows hold ids, not content — but ids are re-identifiable in context. That is precisely why the **write-time content policy** is the real control, and why Q2 (settled 2026-11-09) fixes the audit-trail posture as content-free v1.

### §2.9 Liveness — "enabled but never consults" must be VISIBLE

The overlay can be **silently inert** while every health signal reads green: the per-seam default is unspecified, the reused `OPENAI_API_KEY` inherits the proxy's **per-token model allow-list** (a non-empty list excluding the decision model ⇒ every consult 403 ⇒ fail open), `safety_identifier`/image input parts are 400-rejected on the System One path, and an empty question name 502s every call. Each is individually fail-open by design, so **none produces an error** — a deployment can consult nothing and look healthy.

| Observable | Type | Answers |
|---|---|---|
| `ensemble_decision_overlay_attempts_total` | counter (`seam_id`, `mode`) | did the seam ever consult? |
| `ensemble_decision_overlay_liveness_alarm_total` | counter (`seam_id`, `reason`) | **"configured/enabled but attempts = 0"** — `reason` ∈ {`never_consulted`, `provisioning_missing`, `allow_list_excluded`} |
| `ensemble_decision_overlay_provisioning_state` | gauge (`seam_id`, `state`) | boot-time provisioning state: `ok` \| `provisioning_missing` \| `allow_list_excluded` |

**Provisioning is verified by the OPERATOR, out-of-band — the daemon does not probe.** The client-facing surface cannot verify provisioning: the decision model is **hard-excluded from `GET /v1/models`** (`pkg/proxy/handler.go:343`) and the **adapter registry is a private Go map with no HTTP exposure** (`pkg/decision/registry.go:22`, `:45-48`); the admin surface exposes kind + credential refs + provider but **not** adapter registration and **not** exact credential resolvability (keys masked), on a different URL derivation. So the **operator** provisions the decision model row and verifies it via the proxy's **admin surface** before the seam is enabled. The client observes only **probe-provable** conditions — **404** (model absent, `pkg/decision/handler.go:261`) and **400 `decision_misroute`** (`:273`) — plus **401** (auth) and **403** (allow-list) as distinct reasons. **A 502 is never a provisioning verdict**: credential-unresolved, adapter-not-registered and upstream failures are **indistinguishable** to the client, so a transient upstream 502 must **never** park a seam as `NOT_ENABLED`. Liveness therefore rests on **attempts = 0** while enabled — which is visible, not silently green.

---

## §3 Kill switches

### §3.1 The switches

**Every switch this set operates is listed here**, with its resolution mode and how an operator flips it. (Long-tool and other unrelated daemon switches are out of scope and are not enumerated.)

| Switch | Scope | **Resolution mode** | **How the operator flips it** | Effect when off | Status |
|---|---|---|---|---|---|
| registry store `decision_global_enabled` | **global master — LIVE** | **live-read, DB-backed** | **`PUT /api/settings/daemon/{key}`** (gated `settings:write`) — **no restart** | the registry constructs no request; every seam is pure-heuristic | **proposed** |
| registry store `decision_seam_<id>_enabled` | per seam — **LIVE** | **live-read, DB-backed** | same channel — **no restart** | that seam alone is overlay-free; others unaffected | **proposed** |
| registry store `decision_seam_<id>_stage` = `shadow` \| `deciding` | per seam — **LIVE** | **live-read, DB-backed** | same channel — **no restart** | `shadow` logs but never acts | **proposed** |
| `DECISION_ENABLED` / `DECISION_MODEL` | **global master — env** | **boot-snapshot** | env/config edit + **daemon restart** | the **boot seed** for `decision_global_enabled` (see `seam-registry.md` §3.4) | `DECISION_MODEL` **pre-carved**; `DECISION_ENABLED` **proposed** |
| `DECISION_SEAM_<ID>` (env) | per seam — **boot default only** | **boot-snapshot** | env edit + **daemon restart** — ⚠️ **not the live kill path**; it only seeds the store | the **boot seed** for that seam's `decision_seam_<id>_enabled` | **proposed** |
| `ENSEMBLE_SYMPTOM_REPAIR_LADDER` | S3 **existing gate** | **boot-snapshot** (Pattern-C global) | env + restart | S3's ladder routing does not fire — and, by §3.2.2, the overlay consult inside that branch is not consulted either | **existing** |
| `ENSEMBLE_LEADER_ATTESTATION_MODE` | S4 **existing gate** (tri-state) | **boot-snapshot** (Pattern-C global) | env + restart | the activation predicate never fires | **existing** |
| `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED` | S4 **existing gate** | **boot-snapshot** (Pattern-C global) | env + restart | `fused_judge_on` is False — and, if the overlay sits inside that boolean (`seam-registry.md` §5.4), the overlay is not consulted either | **existing** |
| `gate_config["llm_judge_enabled"]` | S4 **existing gate** (per-gate config) | **boot-snapshot** — built by `build_gate_config` (`daemon/services/attestation_gate.py:1268`, `:1311`), read at `daemon/graph.py:6294` | config + restart | as the row above | **existing** |

### §3.1.1 The live kill channel — DB-backed, operator-scoped, read per consult

**The channel is the daemon-settings surface, not the tunable surface.** The house precedent is **`daemon/services/settings_service.py:200`** (`MANAGED_KEYS: dict[str, ManagedKeySpec]`) with **`PUT /api/settings/daemon/{key}`**, gated **`settings:write`** (`daemon/routers/settings.py:969`, `:1023`; the GET is `settings:read` at `:946`), and the generic resolver ladder **DB > env > yaml > registry default** (`daemon/services/settings_service.py:54`). The overlay's switch keys live in a **named settings namespace** (`decision_global_enabled`, `decision_seam_<id>_enabled`, `decision_seam_<id>_stage`). **(i) Live read — a build requirement.** The live kill channel **must be READ PER CONSULT**. Resolution happens at the call site on every path, so a flip takes effect on the **next consult** — **no restart, no deploy**. This is explicitly **not** the boot-snapshot/requires-restart posture of the env seeds. **(ii) The global master's holder.** The global master lives in the **daemon-global settings store** (the named settings namespace above) — **not** in `instance_metadata`: the agent-callable per-instance tunable surface writes **per-instance** `instance_metadata` and **cannot** hold a daemon-global master. **(iii) Value types.** The channel must carry `0`/`1` (enabled) and `shadow`/`deciding` (stage). The tunable tool's `[60, 1800]` **int** validation cannot carry those, which is a second reason the tunable surface is not the channel; the settings writer must accept the widened types. **(iv) Operator-only — provided, not asserted.** The live kill channel is the **`settings:write`-scoped HTTP surface**, reachable only by an authenticated operator. **the agent-callable per-instance tunable surface is agent-callable** (registered under the "instance" category and allowed by ~22 agent metas) and is therefore **NOT** the live kill channel: an agent could otherwise re-enable a killed overlay — the inverse of the failure class this design exists to prevent. **(v) Write protocol — fail-closed with read-back verification.** A kill is **write → read-back → verify**. If the write **fails**, or the read-back **mismatches**, the switch resolves **OFF** for that seam and a **`switch_unreadable`** suppressed row is emitted — the overlay never runs on an unverified switch state.  **Durability.** The store is DB-backed, therefore **durable**: a kill **survives a daemon restart**, and a killed seam **must not silently return** after a restart. The boot sequence re-reads the store and **re-verifies** every kill; the counter is **`ensemble_decision_overlay_switch_reverify_total`** (labels: `seam_id`, `outcome` = `verified_off` \| `verified_on` \| `unreadable`).  **The env value is the BOOT DEFAULT ONLY.** A running daemon **never re-reads** `DECISION_SEAM_*` / `DECISION_ENABLED` — they only seed the store at boot. That is precisely why the live store exists.  **Where a switch genuinely cannot be made live** — a boot-snapshot fact read only at boot — the rollback guarantee is **"flip + restart"**, and §3.4 says so. This design does not keep both claims: the live store carries *no deploy, no restart, immediate*; a boot-snapshot switch carries *flip + restart*.

### §3.1.1a Sanctioned kill-channel write path (implementation fact)

`SettingsService.set_override(self, key: str, value: Any, updated_by: str | None = None) -> None` (`daemon/services/settings_service.py:1006`) is **registry-gated** (`MANAGED_KEYS.get(key)` → `UnknownManagedKeyError`), **validates before writing** (`SettingsValidationError` ⇒ the router's 422), takes the **registry-typed value** (an `int` for `_enabled`, a `str` for `_stage`), and is **SYNCHRONOUS**. It writes **NO audit line of its own** — the audit line is the **ROUTER's** (`daemon/routers/settings.py:1011`, `logger.info("[Settings] db override set key=%s value=%r subject=%s")`, with the `sensitive=True` ⇒ `<redacted>` tripwire), and the router calls it off-loop via `asyncio.to_thread`. The daemon-side overlay write therefore emits **its own** audit line **in the router's exact format** so one grep covers both surfaces, and it runs through `db_offload` (a sync DB call must never sit on the event loop). The DB tier READ is likewise injected so the ladder resolves in **ONE** path.

### §3.1.2 How "read per consult" is satisfied (L5)

`get_overrides()` (`settings_service.py:960-965`) opens a NEW Session and queries EVERY call with NO cache, and it is SYNC — a sync DB read per consult would put a DB round-trip on the hot path and violate the house event-loop rule. ⇒ the live read is served from an **IN-PROCESS CACHE of the RESOLVED state**, invalidated by the settings write path (`set_override` / `clear_override`), **RE-VERIFIED AT BOOT**. The ratified control is preserved exactly: a kill written through the live settings channel takes effect WITHOUT a daemon restart (invalidation on write) and SURVIVES a restart (boot re-verification); the daemon is the only writer of that DB tier ⇒ write-path invalidation suffices. Fail-closed: state unresolvable (cache cold AND read fails) ⇒ seam resolves OFF, NO call, a `switch_unreadable` suppressed row is emitted (FLAG12).

### §3.1.3 Managed-key registration constraints (B-4)

The managed-key registration MUST point at a real `Config` attribute: `_get_config_value` walks `config_path` with bare `getattr` (`settings_service.py:1101-1105`) ⇒ AttributeError; the apply path touches config only when the DB tier wins (`:1201`, gate `:1204`), so a bad `config_path` is LATENT at registration and CRASHES the first time an operator writes a DB override. The seam id used in the KEY and in `config_path` MUST be the **UNDERSCORE-FLATTENED** form (the config_path walk dot-splits — a dotted seam id would walk into a non-existent attribute). The human-readable `snapshot.l3.selection` stays as the registry record's `seam_id`. See `decisions.md` for the settings-key registration table.

### §3.2 The property each switch must have

> **Switch-off ≡ never calls out ≡ byte-identical DECISIONS; observability differs.** A suppressed row plus `ensemble_decision_overlay_suppressed_total` deltas are **expected** after a kill — they are the operator's **confirmation signal**, not a regression.

**Scoped per configuration.** In **C0** and **C1** a kill ⇒ the decisions are **byte-identical** to the pre-overlay system. In **C2** a kill ⇒ the **baseline chat selection** (**O3**) is restored — byte-identical to pre-overlay **production** behaviour — **conditional on the retention build requirement** (`seam-registry.md` §5.1.1). The conditionality is explicit, never implied: without the retained path, C2 offers only O1 and O2, and its kill can only *degrade* to O2.

> **In C2 the overlay replaces the chat call ON THE DECIDING PATH; the baseline chat selection is RETAINED (not deleted) as the KILL TARGET.**

Three obligations, each testable:

1. **Never calls out.** The switch is evaluated **before** any socket is opened. No request is constructed, so no connection is attempted, so an unreachable proxy cannot even produce a timeout — the "not configured" path (`thin-client.md` §6 rows 1–3) is the pre-flight path, not an error path.
2. **Byte-identical DECISIONS; observability differs.** The heuristic path is not merely "still reachable" — it is the *same* code path, unchanged, with the overlay call removed from it. A switch must not select a "simplified" heuristic branch. What differs after a kill is the observability (suppressed row + `suppressed_total` deltas) — expected, and the operator's confirmation signal.
3. **Provable.** A regression check compares the seam's output with the overlay switched off against the pre-overlay baseline. This check is a **gate** on the phase that introduces the overlay (see §4). **Acceptance artefact:** the **P1 exit criteria** in §4 of this document — the "switch-off produces byte-identical decisions" item — is the artefact of record for this obligation, and is where the live-channel kill test and the restart-survival test are exercised.

### §3.2.1 Switch polarity — fail-CLOSED for overlay switches

An overlay switch is **fail-closed**: if its state is **unreadable, unparseable, or unknown**, the overlay is **OFF** for that seam. ⚠️ **This deliberately does NOT reuse the house Pattern-C resolver semantics.** `daemon/services/attestation_judge_resolver.py:85-101` fails **OPEN** to ENABLED on unrecognized input — because that resolver answers a **different control class** ("should I attempt the judge at all?"). An overlay switch must never fail to ENABLED. **The house call-site behaviour an implementer should copy is the fail-closed fault path**: at `daemon/graph.py:6296-6297` a resolver exception sets `fused_judge_on = False` — the fault **disables** the path rather than enabling it. **Why this is not a D2 violation.** D2's fail-open governs **overlay failure** — the call failed ⇒ the heuristic result. *Switch-unreadable is not an overlay failure*: it means the overlay **never runs**, so the outcome is purely heuristic. **Both directions leave the pre-overlay path in charge**; the only difference is whether the overlay was attempted.

### §3.2.2 Gate composition — the overlay needs BOTH switches ON

**Composition rule (canonical — stated once, applied to S3 and S4).** *The overlay is consulted only if BOTH the registry's live per-seam switch is ON AND the seam's existing gate switch is ON; if EITHER is OFF or unreadable ⇒ the overlay is NOT consulted (no call).* This rule is **fail-closed**: an unreadable gate switch means the overlay does not run. It is **not a D2 conflict** — D2 governs **overlay failure** (the call failed ⇒ the heuristic result), whereas a gate-off or gate-unreadable seam **never calls out at all**. Both directions leave the pre-overlay path in charge. Applied to **S3** (`seam-registry.md` §5.3) and **S4** (`seam-registry.md` §5.4).

### §3.3 Coverage matrix

The switches must cover **every** path that could reach the proxy — including the paths that are *not* on the obvious synchronous call chain.

**S3's overlay consult is placed in the router's ladder-routing branch (`daemon/graph.py:4261` `should_continue`, ghost block `:4380-4402`, master gate `:4384`), inside the branch that `ENSEMBLE_SYMPTOM_REPAIR_LADDER` gates — so that switch gates the overlay as well as the routing.**

| Call path that could reach the proxy | Global master | Per-seam switch | Notes |
|---|---|---|---|
| S1 — typed selection inside `_llm_select` (`snapshot_search_service.py:663-665`/`750-757`) | covered | `snapshot.l3.selection` | the synchronous overlay call site |
| S2 — none today | n/a | n/a | deferred (`seam-registry.md` §5.2) |
| S3 — the consult sits in the **router's** ladder-routing branch (`graph.py:4261` `should_continue`, ghost block `:4380-4402`, master gate `:4384`) | covered | `ghost_promise.detection` | **one placement, and the switch that actually gates it**: the consult is **inside the branch `ENSEMBLE_SYMPTOM_REPAIR_LADDER` gates** (`daemon/config.py:6356-6410`), so that switch gates the overlay as well as the routing. The `attestation_gate_node` (`:5932`) is a **different node** (S4's surface) and is **not** S3's hook |
| **The global master** (`decision_global_enabled`) | covered | all seams | evaluated **before** the per-seam switch: master OFF ⇒ no seam is consulted, whatever the per-seam keys say |
| **The stage key** (`decision_seam_<id>_stage`) | covered | all seams | `shadow` ⇒ consult and log but never act; `deciding` ⇒ the overlay may act (subject to §3.2) |
| S4 — the judge-call gate (`graph.py:6289-6296`) → the judge call at `:6326` | covered | `attestation.rescue` | existing switches: `ENSEMBLE_LEADER_ATTESTATION_MODE` (tri-state; "off" ⇒ the activation predicate never fires, `attestation_resolver_activation.py:962`) and `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED` (default ON — `daemon/services/attestation_judge_resolver.py:40-41`, `90-100`) |
| **S4 — the canonical judge call** (`attestation_report_judge.py:703`, invoked `graph.py:6326`) | covered | `attestation.rescue` | ⚠️ **the earlier "async path" premise was false.** `judge_fused_bundle_async` has **exactly one production call site** (`graph.py:6326`), awaited **inline** behind the same `fused_judge_on` gate — there is no separate async entry point to cover. The **genuine residual** is the judge's **own second HTTP attempt** (`attestation_report_judge.py:120` — "attempt 1 and attempt 2"; per-attempt timeout, worst case 2 × `timeout_s` at `:725-730`), which sits **inside the judge's own budget** and is not a separate overlay path |
| **The shadow writer** | **not suppressed by default** | not suppressed | the shadow writer does **not** call out, and it keeps writing rows so that "switched off" stays observable. After a kill it writes the **suppression** row (`reason=switch_off`) — **no consult row**, because no consult happened; the suppression row **is** the record. |
| **S4 — `gate_config["llm_judge_enabled"]`** (`daemon/services/attestation_gate.py:1268`, `:1311`; read at `daemon/graph.py:6294`) | covered | `attestation.rescue` | a **named third switch surface** for S4, alongside `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED`; boot-snapshot, and it reaches the overlay only through the `fused_judge_on` boolean (`seam-registry.md` §5.4) |
| The metrics/registry layer | covered | covered | the registry resolves the switch and passes the decision down, so no seam can bypass it |
| **Switch resolution itself** | covered | covered | resolution is consulted **at the call site on EVERY path** — a resolution failure ⇒ **no call** + a suppressed row with `reason=switch_unreadable` |
| **An overlay call already in flight when the switch flips** | n/a — it is not cancelled | n/a | the in-flight call is **not** cancelled (PX-6: client disconnect does not cancel upstream; the attempt bills) — it completes and its result is **discarded**. **"Immediate" means: no new call is initiated after the flip** |

**The byte-identical caveat for S3's passive detectors.** Detectors (A)/(B)/(C) are **always-on** observability; their only rollback seam is `SANITY_FLAG_VERSION` (`daemon/constants.py:1614`; the seam is documented at `:1587-1596`). They never route, so **decisions** stay identical with the overlay removed — but the (C) marker suffix still alters report **content** while `SANITY_FLAG_VERSION == 1`. The switch-off property in §3.2 is therefore a statement about **decisions**, and it must not be over-read as "the report bytes are unchanged".

**Why the async path is called out explicitly.** An async judge that is still "live" after the switch is off would produce exactly the failure this design is built to prevent: a call out that the operator believes cannot happen, on a path that is invisible in the synchronous call chain. The coverage matrix exists so that the switch is checked **at the point of the call**, on **every** path, including the ones added later.

### §3.4 Operator procedure
**Trip conditions (any one ⇒ switch off the affected seam):**

| # | Condition | Threshold (proposed) |
|---|---|---|
| T1 | A dispatch-blocking incident attributable to the overlay | any occurrence — trip immediately |
| T2 | Fallback rate (fail-open share of attempts) | > 20 % over 1 h |
| T3 | Shadow agreement rate collapse | < 50 % over the A/B window |
| T4 | p95 overlay latency | above the seam's budget (`thin-client.md` §4.2) |
| T5 | Cost per decision | above the global ceiling (§5) — cumulative month-to-date spend across all seams (from the cost the proxy reports), compared against 50 USD, evaluated on **every consult** (a consult is the only moment spend accrues — no scheduler needed); trips the module to shadow-off when crossed |
| T6 | Any non-fail-open event (an error reaching a caller instead of the heuristic) | any occurrence — this is a D2 violation, trip immediately |

**Procedure:**
**Auto-trip mechanism (cost ceiling — FLAG3).** Cumulative month-to-date spend across all seams is accumulated from the cost the proxy reports (the `usage` block; `ensemble_decision_overlay_cost_total` per seam, summed). A **consult is the only moment spend accrues** — there is no scheduler, no background poll: the check runs at the end of every consult, comparing the month-to-date total against the 50 USD global ceiling. When crossed, the module auto-trips to **shadow-off** (§3.2). The trip target is **both keys per seam** — `decision_seam_<id>_stage := 'shadow'` AND `decision_seam_<id>_enabled := 0` — **plus the GLOBAL master OFF** (`decision_global_enabled := 0`), because the ceiling is global: a trip on the global ceiling stops every seam, not just the one that pushed it over. In P1 no seam decides, so off and shadow are observationally identical; the ruling just removes the ambiguity.
**Operator notification on trip (L9 — board sink settled).** The BOARD SINK is the channel; the fallback is NOT needed. `BoardService` (`daemon/services/board_service.py`, class `:111`) `create_item(*, title, body_md="", project_id=None, tags=None, due_at=None, priority=None, created_by_instance)` `:129` is SYNC, opens its own Session and its own transaction, guarded by a process-level `threading.RLock` (`self._write_lock` `:132`) ⇒ SAFE from a background thread (no await, no event loop, no request context; only requirement is the engine). Reached via `manager.board_service` (`daemon/manager.py:16076-16090`; constructed once `:1811`). Mirror the precedents at `daemon/services/anomaly_service.py:1179-1196` (create) and `:1090-1104` (note), but call `board.create_item(...)` DIRECTLY (sync) — do NOT use the async `_offload` wrapper. House doctrine `anomaly_service.py:60-68`: "board is the ALWAYS surface; notify is the deliberate MAY voice" ⇒ a board item + a daemon-log WARNING row + a dedicated counter. `notify_user` MUST NOT be used (refuses any caller whose agent_id != "ari" `:702-708`; refuses a session with no chat original_source `:44-46` — a daemon service cannot use it). NO outbound webhook sender in `daemon/`, NO email path; chat/push stays a NAMED FUTURE, not a P1 claim. (The FLAG4 "fallback if the board sink is not reusable" is upgraded to this settled ruling.)
**Procedure:**

1. Flip the **per-seam** switch off (global only if the fault is suspected registry-wide).
2. **Confirm** via `ensemble_decision_overlay_attempts_total` that attempts for that seam have stopped, and via `ensemble_decision_overlay_suppressed_total` that the suppression reason is the switch.
3. **Confirm** the decisions are byte-identical: run the seam's regression check against the pre-overlay baseline.
4. **Record** the trip in the change journal with the metric snapshot that triggered it.
5. Decide: fix and restart in **shadow** (never straight back to `deciding`), or leave the seam deferred.
6. A trip on a `deciding` seam returns it to `shadow`, not to `deciding`. The `deciding` stage is re-earned through the Emperor gate (§4, P3).

### §3.5 The throttle — breaker, rate and concurrency budgets (B2)

**The cost CEILING is the backstop. The BREAKER and the rate/concurrency budgets are the THROTTLE.** The ceiling bounds spend *after the fact*; the throttle bounds the damage *while it happens*. Neither substitutes for the other — and the **dead lane** failure mode they jointly prevent is specific: **every snapshot search paying a full 5 s timeout and a billed attempt, indefinitely, relieved only by a human noticing a dashboard.** (1) and (2) below are what prevent it.

#### §3.5.1 The automatic breaker

Per seam **and** global:

| Parameter | Meaning | Proposed value |
|---|---|---|
| **Consecutive-failure trigger** | N consecutive failures ⇒ the seam opens | **N = 5** |
| **Fallback-rate trigger** | the fail-open share of attempts over a window ⇒ the seam opens | **> 50 % over 10 minutes** |
| **Open duration (T)** | how long the seam stays short-circuited | **T = 5 minutes** |
| **Probe** | after T, **one** probe attempt is allowed through | one attempt: success closes the breaker, failure re-opens it for another T |

While the breaker is **open**, the registry returns the heuristic result **without calling out**. It writes a **suppressed shadow row** with `reason=breaker_open`, and — because **no call is made** — it incurs **no cost**.

**A timeout counts toward BOTH triggers**: it advances the **N = 5 consecutive-failure** counter, and it is a **fallback** for the **> 50 % window** calculation.

⚠️ **What the breaker does NOT catch.** It is **error / fallback-rate only**. A **slow-but-successful** overlay — 4.8 s of a 5 s budget, returning a valid answer — **never trips it**: it is neither a failure nor a fallback. In C2 such a lane is relieved only by the operator trip **T4** (p95 overlay latency above the seam's budget). An **automated p95 trip is FUTURE work** and is not part of this design.

#### §3.5.2 Rate and concurrency budgets

| Budget | Scope | Enforcement point |
|---|---|---|
| **Call rate** | per seam **and** global (calls per minute) | **registry-side, PRE-SOCKET** — before any network call is constructed |
| **Concurrency** | per seam **and** global (in-flight overlay calls) | **registry-side, PRE-SOCKET** — a call is admitted only if a slot is free |

Both are enforced **at the registry, before the socket** — the same pre-flight position as the configuration checks (`thin-client.md` §6 rows 1–5). A request that would exceed a budget is **not sent**: the registry answers from the heuristic and writes a suppressed row (`reason=rate_limited` / `reason=concurrency_limited`). **No call ⇒ no cost.**

#### §3.5.3 Cost accounting for attempts that were billed but not delivered

A client timeout does **not** cancel the upstream call (PX-6) — the upstream attempt **bills** even though the client gave up. Therefore:

> **Every attempt that reached the socket is counted in `ensemble_decision_overlay_cost_total`, whether or not it returned an answer.** A timeout is a **failed attempt that still cost money**, not a free attempt.  Conversely, a consult **suppressed before the socket** (breaker open, rate-limited, concurrency-limited, switch off, switch unreadable, not configured, excluded seam) makes **no attempt**, so it is **never** counted in the cost counter — only in `ensemble_decision_overlay_suppressed_total` with its reason.



---

## §4 Phasing

Each phase is a gate, not a task. **No phase assumes the next one's outcome.**

### P1 — Client + registry + shadow instrumentation (no overlay acts)

- Build the client (`thin-client.md`) and the registry (`seam-registry.md` §1–§4) with **zero** overlay influence on any decision.
- Register the **baseline instrumentation first** — the global and shadow families of §2.2–§2.4 must be live and populated **before** the first overlay call, so the A/B baseline is measured against a known-good heuristic baseline rather than against nothing.
- Wire **S1 in C1 (shadow) only** (`seam-registry.md` §5.1.1). The chat call ships **unchanged**; the overlay is called **detached**, its answer is logged next to the heuristic's, and **the chat call still decides**.
- **P1 exclusions (ratified 2026-11-09, FLAG11): seams 2/3/4 are UNREGISTERED** — structurally absent from the registry, not merely disabled-by-default; no registration, no call path, no switch row for them in P1. **S4 shadow capability may be built** (the shadow writer + baseline instrumentation) but **nothing decides** — S4 stays SHADOW-ONLY. **S3's intended hook site** (`daemon/graph.py:4261` `should_continue`, ghost block `:4380-4402`, master gate `:4384`) is **DECLARED METADATA for the P4 lane, NOT wired in P1** — no overlay consult is placed there in P1.
- Exit criteria: the fail-open identity holds (`fallbacks_total == failures_total`); the 30 failure-table rows each fail open or suppress correctly in a test; switch-off produces byte-identical decisions; CI is green with the overlay unconfigured (D1's CI row); **and the two kill acceptance criteria hold — (a) "kill via the live channel ⇒ `ensemble_decision_overlay_attempts_total` for that seam stops increasing WITHOUT a daemon restart", and (b) "the kill survives a daemon restart and is re-verified at boot".**

### P2 — A/B window

- Run S1 in **C1** for an agreed window with real traffic (2 outbound calls per search: 1 chat + 1 detached overlay).
- Measure: **agreement rate** (seam × question), **overlay latency p95** against the 5 s budget, **cost per decision**, and **zero dispatch-blocking incidents** (T1).
- Exit criteria: agreement rate acceptable to the Emperor; p95 inside budget; cost/decision inside the ceiling; no T1/T6 events.

### P3 — **EMPEROR GATE** → S1 deciding

- **The Emperor ratifies the transition of S1 from `shadow` to `deciding`.** No seam may enter `deciding` without this gate (D3).
- On ratification, S1's stage flips to `deciding` for that seam only and the configuration flips **C1 → C2**: the overlay becomes the deciding call **on the deciding path** (subject to the confidence floor, the candidate-set intersection, and fail-open to the deterministic floor). **The baseline chat selection is retained as the kill target** — the build requirement in `seam-registry.md` §5.1.1. P3's exit criteria must compare **O2** (the C2 fail-open floor) against the C0 baseline, **not only O1** — O2 is a lexical ranking, materially different from the chat LLM's selection (`seam-registry.md` §5.1.1).

### P4 — One seam at a time, each with its own shadow cycle

- A new seam repeats **P1 → P2 → P3** in full. There is no batch promotion.
- The sequencing the doctrine implies: **S1** (pilot, smallest blast radius) → **S3** → **S2** (blocked on missing inputs) → **S4** (last, rescue-band-only).
- **S4 requires the D4 band discipline** — the overlay may only add completions above the high-confidence floor, and the `DENY` rung stays heuristic-owned (`seam-registry.md` §2.2). **S4 enters the deciding sequence only in the ADDITIVE form** (the canonical judge is consulted first; the overlay may only add above the floor). If the additive form cannot be implemented, **S4 remains SHADOW-ONLY and is dropped from this sequence** (`seam-registry.md` §5.4). Its shadow phase follows the code's existing **`dry` mode** precedent — compute the decision, record it, change nothing (`daemon/services/attestation_gate.py:2160-2180`).

### §4.1 Rollback

**Rollback = kill switch + unregister.** Two steps, in this order:

1. **Kill switch** — write the per-seam switch off in the **live registry switch store** (§3.1.1).
2. **Unregister** — remove the seam's registration from the registry. The registry then has **no record** of the seam and **cannot call out on its behalf**, even if the switch were re-enabled by mistake. Unregistration is the durable half of the rollback: the kill is immediate, the unregister is what makes it stick against a mistaken re-enable. **No new call is initiated after the flip**; an in-flight call is not cancelled and its result is discarded. Per the kill contract (`seam-registry.md` §5.1.1): in **C0/C1** the chat call ships as today and the decisions are **byte-identical** to the pre-overlay system; in **C2** the kill **restores the baseline chat selection** (O3) — byte-identical to pre-overlay **production** behaviour, **conditional on the retention build requirement**. This step is **always available**, requires no deploy and no restart, and is the primary rollback. Observability **does** differ after a kill: a suppressed row and `ensemble_decision_overlay_suppressed_total` deltas are **expected** — they are the operator's confirmation signal.  2. **Unregister** — remove the seam's registration from the registry. The registry then has no record of the seam and cannot call out on its behalf even if the switch were re-enabled by mistake.  **Rollback is not a deploy.** No code change, no image, no restart is required to roll back an overlay. This is what makes the P1 exit criteria (switch-off ⇒ byte-identical) load-bearing rather than decorative.

---

## §5 Cost ceiling — SETTLED (Q1 ratified 2026-11-09)

The overlay costs money per call. The ceiling and auto-trip are **ratified**, not open.

- **Binding ceiling: USD 50 per month, GLOBAL (owner: the Emperor).** A single global monthly ceiling across all seams — not per-seam, not operator-set-without-trip.
- **Auto-trip (§3.4):** cumulative month-to-date spend across all seams (from the cost the proxy reports) compared against 50 USD, evaluated on every consult; trips the module to shadow-off (both keys per seam — `decision_seam_<id>_stage := 'shadow'` AND `decision_seam_<id>_enabled := 0` — plus the GLOBAL master OFF) when crossed. No scheduler; a consult is the only moment spend accrues.
- **Per-seam share retained as the guard** that stops one seam consuming the whole global budget — the design's default position, now ratified (FLAG1). The global ceiling is the backstop; the per-seam share is the allocation guard; the breaker/rate/concurrency budgets (§3.5) are the throttle.
- **Operator notification (L9 — board sink settled):** on trip — a daemon-side board item (`board_items`/`board_notes`) via `BoardService.create_item` (sync, safe from a background thread) plus a daemon-log WARNING plus a dedicated counter (`ensemble_decision_overlay_cost_ceiling_tripped_total`). `notify_user` MUST NOT be used; NO outbound webhook/email; chat/push stays a named future, not a P1 claim. The FLAG4 "fallback if the board sink is not reusable" is upgraded to this settled ruling.
- The ceiling is enforced by `ensemble_decision_overlay_cost_total` and the trip is condition T5 (§3.4).
- **Spend unit (L8 — no pricing table exists).** NO PRICING TABLE exists anywhere in the ensemble OR the proxy; the proxy meters TOKENS, not dollars; the decision endpoint's `usage` block is TOKENS-ONLY, metered ON SUCCESS ONLY. The System One upstream responses carry a `cost` field (9router cost=0, openrouter ~0.0000119) — an upstream field, NOT part of the proxy's decision usage contract. ⇒ the ceiling is computable WITHOUT inventing a fake pricing table: (a) accumulate spend in USD when the provider RESPONDS with a cost; (b) otherwise convert via an OPERATOR-CONFIGURED per-model USD-per-million-token rate (a managed key / config entry — NOT a hardcoded constant: pricing differs per provider and per model, and a hardcoded table is false precision that would either trip falsely or never trip); (c) if neither is available for the model, count the consult in TOKENS, emit a `pricing_unconfigured` suppressed row, and enforce a configured TOKEN ceiling as the fail-safe so the control is NEVER ABSENT — the control degrades VISIBLY rather than silently vanishing. The counter is incremented BEFORE the outcome is known (§3.5.3 — every attempt that reached the socket is counted whether or not it returned an answer; a timeout still bills). `Metric.inc(value)` accepts a non-1 float, so spend accumulates directly. Trip: cumulative month-to-date USD (or the token fallback) vs the ceiling ⇒ `decision_seam_<id>_stage := "shadow"` AND `decision_seam_<id>_enabled := 0` AND the global master `decision_global_enabled := 0` (FLAG2's both-keys ruling) ⇒ operator notify (L9: board sink).
---

## §6 Shadow-log content stance — SETTLED (Q2 ratified 2026-11-09)

- **Ratified stance: content-free (v1).** Ids, hashes, scores, probabilities, counts, latencies, enum labels only. Never credentials, never user text, never request/response payloads (§2.6). The Q2(b) redaction branch is **foreclosed**: v1 is content-free by the WRITE-TIME content policy (§2.6); a non-agent-readable sink stays a **named future (v2 build requirement)** — this design does not claim one exists.
- **Audit-trail posture (§2.8) follows:** the shadow log IS the audit trail; the real control is the write-time content policy, not read-time access control (the earlier "operator-scoped, not agent-readable" claim was false and is deleted). A non-agent-readable sink is a candidate build requirement for the implementation commission, not a P1 claim.