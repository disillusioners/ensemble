# Decisions — Ensemble Decision Module (Thin Client + Seam Registry)

> **Status:** **RATIFIED P1 SHADOW-ONLY — Emperor ratification 2026-11-09: "yeah, look good, go ahead, shadow only for now".** Design of record. P1 implementation authorised (client + registry + shadow instrumentation); seams 2/3/4 unregistered; S4 shadow-only. (The leader's giter commits at finalize.)
> **Scope:** `.agents/shared/planning/decision-module/` — `scope-and-doctrine.md`, `thin-client.md`, `seam-registry.md`, `controls-and-phasing.md`, `architecture-recommendation.md`, `anchor-audit.md`, this file.
> **Normative source:** `scope-and-doctrine.md` — cited by §id, never re-quoted. This file records the decisions that the design set depends on but that are **not** doctrine (module surface, leader rulings, anchor corrections, scope), so the design docs never have to re-derive them.

---

## §1 Module surface (FLAG8 — published by the leader's ruling)

The contract names **no** ensemble-side module. Per house convention the module lands under `daemon/services/`. The surface is **FIXED** — implement exactly this:

| File | Symbol(s) | Role |
|---|---|---|
| `daemon/services/decision/types.py` | `DecisionQuestion`, `DecisionRequest`, `DecisionAnswer`, `QuestionKind`, `Monotonicity`, `DecisionStage`, `FailureClass`, `SuppressionReason`, `OverlayOutcome` | typed domain model |
| `daemon/services/decision/config.py` | `DecisionConfig` (`DECISION_BASE_URL`→`OPENAI_BASE_URL`, `DECISION_API_KEY`→`OPENAI_API_KEY`, `DECISION_MODEL`="decision", `DECISION_TIMEOUT_S`) | configuration / reuse rules |
| `daemon/services/decision/client.py` | `DecisionClient` (HTTP transport + typed mapper + fail-open wrapper) | thin client (`thin-client.md`) |
| `daemon/services/decision/registry.py` | `SeamRecord`, `SeamRegistry` (records, admission, per-direction monotonicity, `denied_seam_roles` from EX-1..EX-5, kill matrix, D3 transition refusal) | seam registry (`seam-registry.md`) |
| `daemon/services/decision/kill.py` | `KillChannelResolver` (settings namespace, read per consult, write→read-back→verify, fail-closed) | live kill channel (`controls-and-phasing.md` §3.1.1) |
| `daemon/services/decision/shadow.py` | `ShadowWriter` (content-free; never fails a caller) | shadow log (`controls-and-phasing.md` §2.6) |
| `daemon/services/decision/metrics.py` | metric family registration + counters | observability (`controls-and-phasing.md` §2.2) |
| `daemon/services/decision/overlay.py` | `DecisionOverlay` orchestrator (consult, shadow row, metrics, cost accounting, auto-trip) | orchestration |
| `daemon/services/decision/port.py` | `DecisionOverlayPort` protocol (the narrow OUTWARD seam the snapshot side calls) | port (outward seam) |

**How the 5 capability seams map onto it:**

| Capability seam (design) | Module file(s) |
|---|---|
| (1) thin client | `client.py` (+ `types.py` / `config.py`) |
| (2) seam registry | `registry.py` |
| (3) overlay orchestrator | `overlay.py` |
| (4) kill-channel resolver | `kill.py` |
| (5) shadow writer | `shadow.py` |

**Import-graph constraint (how it is honoured).** `daemon/services/snapshot_metrics_service.py:31-34` structurally forbids ranking modules (`snapshot_search`, `snapshot_embedding`) from importing the metrics service (test-pinned by `TestMonitoringOnlyPin`, `tests/unit/tools/test_snapshot_v3.py:1559`). The overlay is therefore **NOT** imported into the snapshot ranking module. The snapshot side gets a narrow seam it calls **OUTWARD** to (`port.py`), and the ranking module only ever calls a passed-in sink. The registry owns counter registration; the ranking module only calls a passed-in sink.

---

## §2 Leader rulings as applied (FLAG1..FLAG12)

| Ruling | What it changed | Where it landed |
|---|---|---|
| **FLAG1** | Cost figure written into the design: **USD 50 per month, GLOBAL (owner: the Emperor)**, as the BINDING ceiling; per-seam share retained as the guard. | `controls-and-phasing.md` §5 (new binding-ceiling bullets); §3.4 T5 row (cross-ref). |
| **FLAG2** | Trip target specified: "shadow-off" = BOTH keys (`decision_seam_<id>_stage := 'shadow'` AND `decision_seam_<id>_enabled := 0`) PLUS the GLOBAL master OFF. In P1 off and shadow are observationally identical. | `controls-and-phasing.md` §5 (auto-trip bullet) and §3.4 (T5 row + auto-trip paragraph). |
| **FLAG3** | Auto-trip mechanism specified: cumulative month-to-date spend across all seams (from the proxy-reported cost), compared against 50 USD, evaluated on EVERY consult (a consult is the only moment spend accrues — no scheduler); trips to shadow-off when crossed. §3.4 T5 row's quantity corrected (was "cost per decision", the wrong quantity for a global monthly ceiling). | `controls-and-phasing.md` §3.4 (T5 row rewritten; auto-trip paragraph added) and §5. |
| **FLAG4** | Operator notify specified: preferred daemon-side board sink (`board_items`/`board_notes`) on trip + daemon-log WARNING + dedicated counter/metric; fallback (if the board sink is not reusable from a background service) is counter + log + trip status on the daemon-settings detail read, disclosed as a downgrade; chat/push is a named future, not a P1 claim. | `controls-and-phasing.md` §3.4 (operator-notification paragraph) and §5. |
| **FLAG5** | Superset-only is DIRECTION-SCOPED, not global: per-direction monotonicity documented — `TOWARD_COMPLETION` (S4) MUST return a superset (subset refused at registration AND at apply time); `WITHIN_SET` (S1) stays within the candidate set and never empties it (may replace/reorder); universal rule remains the weaker "apply may not widen the outcome space". | `seam-registry.md` §2.1 (rule 3 clarified), §2.2 (per-direction monotonicity paragraph); `thin-client.md` §8.4 (scope note). |
| **FLAG6** | Stale headers updated to the ratified P1 SHADOW-ONLY state with ratification date 2026-11-09 and the Emperor's quote; `architecture-recommendation.md` §6 Status rewritten; `scope-and-doctrine.md:3` "no daemon code, no staging, no promote, no git commits" sentence dropped (kept "the leader's giter commits at finalize"). | All six files' status headers; `architecture-recommendation.md` §6; `scope-and-doctrine.md` §0/line 25. |
| **FLAG7** | Q1 and Q2 rewritten as SETTLED (not "open question / default position (proposed)"); Q2(b) redaction branch foreclosed — v1 content-free by write-time policy, non-agent-readable sink stays a named future (v2). Cross-references in `architecture-recommendation.md` fixed. | `controls-and-phasing.md` §5 (renamed), §6 (renamed), §2.8 (audit-trail row + closing sentence); `architecture-recommendation.md` §5 (renamed). |
| **FLAG8** | Module surface published (this §1) — `daemon/services/decision/*`, fixed file list, 5-seam mapping, import-graph constraint. | This file, §1. |
| **FLAG9** | PX drift published as the D6 corrections table (§3 below) — proxy v187 (`43fb7e5`) internal anchors drifted; client-facing contract confirmed unchanged. Corrections are PUBLISHED, never silently patched. | This file, §3. |
| **FLAG10** | Leaked excluded mechanism corrected: `anchor-audit.md:310` PASS-2 row R2 marked SUPERSEDED by PASS-3 row B5; literal token neutralised in `controls-and-phasing.md:141`; token added to the §8 removed-strings list. | `anchor-audit.md` (R2 row rewritten; §8 list extended); `controls-and-phasing.md` §3.1.1. |
| **FLAG11** | P1 exclusions written as ratified scope text: seams 2/3/4 UNREGISTERED (structurally absent, not merely disabled-by-default); S4 shadow capability may be built but nothing decides; S3's hook site recorded as DECLARED METADATA for P4, not wired in P1. | `controls-and-phasing.md` §4 (P1 section, new bullet). |
| **FLAG12** | **CONSULT vs APPLY** — the admission rule conflated them. (1) **CONSULT gate** = global master ON AND per-seam enabled ON AND the seam's own gate switch ON AND the overlay is configured (`DECISION_BASE_URL` + `DECISION_API_KEY` present); **stage does NOT gate consultation** (a shadow-stage seam IS consulted — that is how the A/B record is produced). (2) **APPLY gate**: a consulted answer is applied ONLY when stage == `deciding`; at stage == `shadow` it is logged (the A/B record) and the heuristic outcome is returned UNCHANGED, byte-identical to today. Any CONSULT-gate condition OFF or UNREADABLE ⇒ NO CALL AT ALL plus a `switch_unreadable` suppressed row. (3) D3 made concrete: the registry REFUSES a shadow→deciding transition absent review+ratification. Consequence: a freshly registered seam is consulted nothing until an operator flips its per-seam key ON (and provisions the credential) — that IS the Emperor's "per-seam default OFF". | `seam-registry.md` §3.4 (admission rule rewritten as two gates); `decisions.md` §2 (this row). |
| **B-1 / L1** | Metric names REJECTED by the house registrar: rule `^ensemble_[a-z0-9_]+$` (`daemon/checkpoint_metrics.py:110`); the validator RAISES ValueError (`:512-516`). Every metric name the design proposed is renamed to `ensemble_decision_overlay_*`; the `family=` kwarg is free-form and stays the family id `decision_overlay`. Switch keys (`decision_global_enabled` / `decision_seam_*_enabled` / `decision_seam_*_stage`), the family id, and the shadow-log event name stay UNCHANGED. | `controls-and-phasing.md` §2.2/§2.3/§2.4 (renamed); §2.2 (one-line family-id note). |
| **B-2 / L2** | `question_id` CANNOT be a metric label: the wiring registrar refuses a label key with no `allowed_values` set (`wiring/registry.py:104-113`), and the unbounded per-question series blows `DEFAULT_CARDINALITY_CAP=2000` (`checkpoint_metrics.py:129`) with excess increments silently DROPPED (`:832-845`). `ensemble_decision_overlay_shadow_agreement_total` / `_disagreement_total` lose the `question_id` label; the per-question A/B signal goes to the SHADOW LOG (`controls-and-phasing.md` §2.6, whose row already carries `question_id`). Aggregated metrics only. | `controls-and-phasing.md` §2.3. |
| **B-3** | The `metrics` substring trap added as a build constraint beside the import-graph constraint (`controls-and-phasing.md` §2.5): `snapshot_search_service.py` is under a WHOLE-FILE substring ban (`assert "metrics" not in text …`, `tests/unit/tools/test_snapshot_v3.py:1645`); any module the ranking file IMPORTS must not carry the lowercase token `metrics`. | `controls-and-phasing.md` §2.5. |
| **B-4** | `ManagedKeySpec.config_path` MUST be a real `Config` attribute (`_get_config_value` bare `getattr`, `settings_service.py:1101-1105`) ⇒ AttributeError; the apply path touches config only when the DB tier wins (`:1201`, gate `:1204`), so a bad `config_path` is LATENT at registration and CRASHES the first operator write. The seam id in the KEY and in `config_path` MUST be the UNDERSCORE-FLATTENED form (the walk dot-splits). | `controls-and-phasing.md` §3.1.3. |
| **L3** | Registration path: families register through the WIRING registrar (`wiring/registry.py:74-185`, `register_family_metric`), NOT raw `cm.register_*` — AM-2 forces `allowed_values` per label key (the enforcement the "30 rows → 25 failure values → 19 DISTINCT failure_class labels" roster needs) and AM-3 makes `source_idiom`/`durability`/`cardinality_weight` required kwargs. Costs an adjudication-pack amendment (`wiring/adjudication.py:90` `REGISTERED_FAMILIES`, the hardcoded 27-family set `:104-111`, `FAMILY_SERIES_BANDS` `:158`, global measured `<=2000` cap `:457-480`) — the implementation lane is landing it. `test_the_daemon_diff_is_confined_to_the_allowed_files` (`tests/unit/metrics/test_metrics_family_adjudication.py:215-234`) may already be RED at base (known-stale pin, `PACKS.md:216-219`). | `decisions.md` (this row + future adjudication note). |
| **L4** | Observer placement: the ranking file imports NO observer module — the observer is INJECTED. `SnapshotSearchService.__init__` (`snapshot_search_service.py:257-281`) gains an OPTIONAL 5th kwarg (default None); `manager.py:2872-2877` (the ONE production construction site) supplies it. The only shape touching NEITHER pin; satisfies the import-graph constraint STRUCTURALLY. | `controls-and-phasing.md` §2.5. |
| **L5** | "Read per consult" satisfied by an IN-PROCESS CACHE of the RESOLVED state, invalidated by the settings write path, RE-VERIFIED AT BOOT — not a sync DB read per consult (event-loop rule). Fail-closed: unresolvable ⇒ seam OFF, no call, `switch_unreadable` suppressed row. | `controls-and-phasing.md` §3.1.2. |
| **L6** | Config namespace: `DECISION_*` cannot ride the `OPENAI_` prefixed resolution (`LLMConfig.env_prefix="OPENAI_"`, `daemon/config.py:142`). Separate namespace: `DecisionConfig(BaseSettings)` with `env_prefix="DECISION_"`, bound as `decision: DecisionConfig = Field(default_factory=DecisionConfig)` on `Config`, plus a `decision:` block in `config.yaml` AND an explicit `config_dict["decision"] = …` pass-through in `load_config` (a yaml block without the pass-through is SILENTLY IGNORED, stated in-repo for nest_report at `daemon/config.py:5753-5755`; mind the init-kwarg-beats-env inversion `:7493`, strip None values `:7546-7555`). Shape precedent `NotifyUserConfig` (`:3166-3224`, 5 fields, bound `:5749`). Reuse-not-mint for values: `daemon/tools/image_gen.py:263-292` — `base_url = (src.get("DECISION_BASE_URL") or src.get("OPENAI_BASE_URL") or "").strip()`; `api_key = (src.get("DECISION_API_KEY") or src.get("OPENAI_API_KEY") or "").strip()`; NEVER reads `OPENAI_MODEL`. `DECISION_MODEL` defaults to `"decision"` (already in `_ALLOWED_MODELS_DEFAULT`, `daemon/config.py:5925` — do NOT re-add); `DECISION_TIMEOUT_S` proposed. ADD `DECISION_BASE_URL`/`DECISION_API_KEY` to `.env.example` (only `DECISION_MODEL` pre-carved today, `.env.example:266-275`). | `decisions.md` (this row); `thin-client.md` §3 (config table). |
| **L7** | Managed-key registration: every overlay switch key MUST be statically enumerated in `MANAGED_KEYS` — NO generic KV fallback (`UnknownManagedKeyError` `settings_service.py:802-804`; router 404s unregistered keys `routers/settings.py:981-985`; an unregistered DB row is SKIPPED on read `:882-890`). No new table, no migration (the generic `AppSetting` row model suffices, `daemon/models/app_settings.py:53-90`), but code edits ARE required. `_enabled` → `value_type="int", ge=0, le=1`; `_stage` → `value_type="str"`. S2/S3/S4 per-seam keys register ONLY when those seams register (P4 lane) — in P1 they stay UNREGISTERED. `ManagedKeySpec` has NO `allowed_values` field, so `parse_and_validate` (`:571-607`) accepts ANY string for the stage key and a typo is writable ⇒ an OPTIONAL `allowed_values` is added with membership enforced in `parse_and_validate` (None = today's behaviour, fully backward-compatible), making the stage key self-validating at write time (422 on a typo). Routes: GET `/api/settings/daemon` (`:943-964`, `settings:read`); PUT `/api/settings/daemon/{key}` (`:966-1018`, `settings:write`; 404 unregistered `:981-985`; 422 on parse failure); DELETE `/api/settings/daemon/{key}` (`:1020-1048`, `settings:write`; 404 unregistered `:1034-1038`; clears the override so the key falls back down the ladder env > yaml > default; idempotent on a missing row). Settings-key registration table published in `decisions.md`. | `controls-and-phasing.md` §3.1.3; `decisions.md` (settings-key table). |
| **L8** | Cost ceiling unit: NO PRICING TABLE exists anywhere in the ensemble OR the proxy; the proxy meters TOKENS, not dollars; the decision endpoint's `usage` block is TOKENS-ONLY, metered ON SUCCESS ONLY. System One upstream responses carry a `cost` field (9router cost=0, openrouter ~0.0000119) — an upstream field, NOT part of the proxy's decision usage contract. ⇒ ceiling computable WITHOUT a fake pricing table: (a) accumulate USD when the provider RESPONDS with a cost; (b) otherwise convert via an OPERATOR-CONFIGURED per-model USD-per-million-token rate (managed key / config entry — NOT a hardcoded constant); (c) if neither available, count in TOKENS, emit a `pricing_unconfigured` suppressed row, enforce a configured TOKEN ceiling as fail-safe so the control is NEVER ABSENT. Counter incremented BEFORE the outcome is known (§3.5.3). Trip: cumulative MTD USD (or token fallback) vs ceiling ⇒ `decision_seam_<id>_stage := "shadow"` AND `decision_seam_<id>_enabled := 0` AND global master `decision_global_enabled := 0` (FLAG2) ⇒ operator notify (L9: board sink). `pricing_unconfigured` added as the 10th suppressed reason. | `controls-and-phasing.md` §5 (spend-unit bullet) + §2.2 (10th reason). |
| **L9** | Operator notify: the BOARD SINK; the fallback is NOT needed. `BoardService` (`daemon/services/board_service.py`, class `:111`) `create_item` `:129` is SYNC, opens its own Session and transaction, guarded by a process-level `threading.RLock` (`self._write_lock` `:132`) ⇒ SAFE from a background thread. Reached via `manager.board_service` (`daemon/manager.py:16076-16090`; constructed once `:1811`). Mirror precedents `anomaly_service.py:1179-1196` (create) and `:1090-1104` (note), but call `board.create_item(...)` DIRECTLY (sync) — do NOT use the async `_offload` wrapper. House doctrine `anomaly_service.py:60-68`: "board is the ALWAYS surface; notify is the deliberate MAY voice" ⇒ a board item + a daemon-log WARNING row + a dedicated counter. `notify_user` MUST NOT be used. NO outbound webhook sender in `daemon/`, NO email path; chat/push stays a NAMED FUTURE. The FLAG4 "fallback if the board sink is not reusable" is upgraded to this settled ruling. | `controls-and-phasing.md` §3.4 + §5 (operator-notify text upgraded). |

---

## §3 D6 corrections table (FLAG9 — proxy v187 anchor drift)

Published, never silently patched. The client-facing contract is **confirmed unchanged** at proxy v187 (`43fb7e5`); what drifted is upstream-INTERNAL evidence anchors. The original citations remain in the design docs; the corrections live here.

| Anchor as originally stated | Corrected | Note |
|---|---|---|
| `pkg/decision/typesafe.go:162-165` (empty question name rejection, surfaced as 502) | `pkg/decision/typesafe.go:199` | site moved; contract unchanged |
| `pkg/decision/typesafe.go:700-703` (`confidence` synthesized on score answers) | `pkg/decision/typesafe.go:638` / `:738` | site moved/split |
| `Criteria` construct | **GONE** | no longer present in v187 |
| `pkg/decision/handler.go` 502 sites `:366`/`:433`/`:465`/`:478`/`:497` | `:371`/`:438`/`:470`/`:483`/`:502` | all five shifted |
| `pkg/decision/registry.go:22` / `:45-48` (adapter registry, private Go map) | moved | location changed; still no HTTP exposure |
| Client wire shape (OpenAI-decisions) | **UNCHANGED 185→187** — the client emits the OpenAI-decisions shape (`choices[]` = `{value, description?}`, `levels[]` = `{label, description?}`); it never constructs `criteria`. The R3/R4 canonicalization is the **PROXY's** job (System One adapter builds `criteria` from the client's choices/levels). The 422/400 figures are **UPSTREAM responses**, not proxy-side client-body validation: 9router + v185 nested ⇒ 422; OpenRouter + v185 nested ⇒ 400 Zod "expected record … path [questions,probe_q1,criteria]"; both + v187 canonical ⇒ 200. The proxy **CANONICALIZES and does not reject a client body for shape**. The proxy's own 400s are a **DIFFERENT edge class**: `pkg/decision/handler.go:218/227/244/273/353` (invalid JSON, missing model, and the validator/preflight for `safety_identifier` / image input). So there is **NO client requirement to send R3/R4**. The v187 `typesafe.go` change ("the previous nested 'choice'/'score' wrapper objects are gone", `pkg/decision/typesafe.go:96-113` — criteria at the TOP level: choice → `map[string]string`, score → `[]string`) is an **UPSTREAM-INTERNAL change** — which is exactly why the client-facing contract is unchanged. |
| `daemon/config.py:5925` (allow-list member `decision`) and `.env.example:268-276` (doc block) | **verified still correct at HEAD `818411107`** | Verified by grep at HEAD 818411107: allow-list member at `:5925`, doc block at `:268-276`. The provider note's claim of `:4957` / `:208` does **not** match this worktree; the design's numbers stand. Commit verified against: `818411107b8836762f8182fca41194e0dc02747a`. |
| `controls-and-phasing.md` §2.5 "structurally forbids" (import-graph constraint) | CORRECTED — enforced by a test-pinned AST walk, NOT an import-time guard | `snapshot_metrics_service.py:31-34` is docstring prose only; the only enforcement is `test_no_snapshot_module_imports_metrics` (`tests/unit/tools/test_snapshot_v3.py:1620-1635`), an AST walk over `daemon/services/snapshot*.py` + `daemon/repositories/snapshot/*.py`. Corrections are PUBLISHED, never silently patched. |
| `controls-and-phasing.md` §3.1.1 cites `settings_service.py:1023` in a PUT context | ATTRIBUTION NIT — `:1023` is the DELETE decorator's scope line | Not functional drift; the PUT decorator is at `:966`. Corrections row only. |
| `pkg/decision/types.go` in the v185→v187 diff | **ABSENT** — independent corroboration that the client-facing contract is byte-identical | The v185→v187 diff touches 10 files (+792/-118: `typesafe.go` +204, `validators.go`, `registry.go`, `handler.go`, `test_exec.go`, new `r3r4_canonical_shape_test.go` +409, `openrouter_decision_test.go` +214) — the client contract held while only the upstream wire moved. |
| Ratified invariant (verbatim) | *"Decision contract UNCHANGED 185 to v187 (route /v1/decisions; answers[].name joins questions[].name). Client sends the OpenAI-decisions shape; proxy translates to canonical R3/R4 for System One (v185 nested = 422 upstream)."* | Body of the re-derived POK invariant `decision-client-contract-invariant-185-v187` (llmproxy store; pending in this project's POK store). |

---

## §4 P1 scope statement (FLAG11)

- **Seams 2/3/4 are UNREGISTERED in P1** — structurally absent from the registry, not merely disabled-by-default. No registration, no call path, no switch row for them in P1.
- **S4 shadow capability may be built** (shadow writer + baseline instrumentation) but **nothing decides** — S4 stays SHADOW-ONLY.
- **S3's intended hook site** (`daemon/graph.py:4261` `should_continue`, ghost block `:4380-4402`, master gate `:4384`) is **DECLARED METADATA for the P4 lane, NOT wired in P1** — no overlay consult is placed there in P1.
- P1 builds only the client + registry + shadow instrumentation + S1 in C1 (shadow). P2 runs the A/B window. P3 is the Emperor gate → S1 deciding. P4 brings seams one at a time (S3 → S2 → S4), each with its own shadow cycle.

---

## §5 Deviations from the design text

*Completed from the implementation report — to be filled when the P1 implementation lands. No deviations are recorded yet; do not invent any.*

---

## §7 Named futures (recorded, NOT built — do not assume any of them)

- **F1** — an openrouter-bound decision model row (credentials.id=openrouter, internal_model=typesafe/jev-1.13, internal_base_url=https://openrouter.ai/api). One operator action, NO new key.
- **F2** — enabling `decision-zen` (the row exists, enabled=false; its credential and upstream are ALIVE — the proxy's own admin test returns 200 with p=0.99).
- **F3** — the 9router repoint (credential `provider` column openai → typesafe, plus a decision row with internal_model=oc/jev-1.13). Note 9router is egress-IP dependent from outside the cluster (Cloudflare 1010 from this host, 200 from inside).
- **9router is NOT a client surface for the ensemble host** — its pod IP is filtered from the ensemble host, its ClusterIP times out, and its public edge sits behind oauth2-proxy (the 404 there is oauth2-proxy's, not 9router's).

These are named futures only. None is assumed by P1; none is built by this commission.

---

## §8 Current provisioning state (corrected — supersedes the design's provisioning framing)

P1 needs **NO new credential and NO proxy-store write**: the proxy store already holds a working, funded OpenRouter key that demonstrably serves System One decisions (~USD 0.0000119/call), and the `decision` purpose-id row is **ENABLED**. The openrouter lane's gap is a **MISSING MODEL ROW**, not a credential. The design's out-of-band operator-provisioning doctrine (§7 of `thin-client.md`) stands; this records the corrected current state alongside it. Do not rewrite the doctrine.

---

## §9 Confirmed live error taxonomy (refines the 30-row table; does not replace it)

- **404 `unknown model`** = probe-PROVABLE provisioning verdict (a transport-level missing path is a plain `404 Not Found`).
- **400 with `code:"decision_misroute"`** = the second probe-PROVABLE provisioning verdict.
- **400 WITHOUT that code** (e.g. "at least one question is required") = proxy request-shape validation, firing BEFORE model resolution ⇒ **NOT** a provisioning verdict ⇒ suppressed `invalid_request`.
- **502** relays the UPSTREAM envelope VERBATIM with the upstream's own `type` ⇒ branch on STATUS ONLY, treat the body as opaque; a 502 MUST NEVER park a seam NOT_ENABLED.
- **401** `authentication_error`; **403** `access_denied` (non-standard body); **405** plain text on GET.

---

## §10 The two traps (implementation-mandate facts the design does not carry)

1. **The upstream REQUIRES `instructions` for `predicate`/`score`** even though the design marks it optional — omitting it yields a 502 `Missing required parameter: 'questions[0].instructions'` on a fully working path, i.e. an invisible 502 that fails open forever and never parks the seam (a silently dead overlay). The client must always send a non-empty `instructions` for those kinds.
2. **The proxy's System One answer synthesizer has never been observed relaying a 9router/OpenRouter real answer** — those upstreams return `type:"noul"` (outside the OpenAI answer union), a `legend` map, and `probabilities` as a MAP where PX-3 describes `probabilities[]` an ARRAY. The mapper must treat an unexpected answer type as a content failure that **fails OPEN**, and must tolerate a map-shaped `probabilities` by degrading to fail-open, never raising.

---

## §11 Cost implementation requirements (from the provider verdict)

- The `decision` row routes to a PAID upstream (api.openai.com) while the per-seam default is OFF ⇒ spend is zero until an operator opts in ⇒ the USD 50/month global ceiling + auto-trip must be **CORRECT FROM THE FIRST CALL**, not merely at scale.
- A missing `usage` block must degrade to a **CONSERVATIVE, NEVER-ZERO** per-call cost estimate (a zero estimate would silently defeat the ceiling).
- The month-to-date accumulator must be **DURABLE across a daemon restart** (a RAM-only counter would let a restart reset month-to-date spend and defeat the global ceiling).

---

## §12 Settings-key registration table (L7)

P1 registers these keys in `MANAGED_KEYS` (no new table, no migration; the generic `AppSetting` row model suffices):

| Key | Type | Default | Boot seed | config_path (underscore-flattened) |
|---|---|---|---|---|
| `decision_global_enabled` | int (0/1) | 0 (OFF) | `DECISION_ENABLED` (unset ⇒ OFF) | `decision.global_enabled` |
| `decision_seam_snapshot_l3_selection_enabled` | int (0/1) | 0 (OFF) | `DECISION_SEAM_SNAPSHOT_L3_SELECTION` (unset ⇒ OFF) | `decision.seam_snapshot_l3_selection_enabled` |
| `decision_seam_snapshot_l3_selection_stage` | str (`shadow`/`deciding`) | `shadow` | `DECISION_SEAM_SNAPSHOT_L3_SELECTION_STAGE` (unset ⇒ `shadow`) | `decision.seam_snapshot_l3_selection_stage` |

S2/S3/S4 per-seam keys register ONLY when those seams register (P4 lane); in P1 they stay UNREGISTERED. `_enabled` → `value_type="int", ge=0, le=1`; `_stage` → `value_type="str"`. The `allowed_values` on the stage key is an OPTIONAL additive change to `ManagedKeySpec` (None = today's behaviour, fully backward-compatible). Use the underscore-flattened seam id in the KEY and in `config_path`; keep the human-readable `snapshot.l3.selection` as the registry record's `seam_id`.

---

## §13 S1 hook site + coverage note + by-hand shadow-row rule (more verified facts)

**S1 hook site.** Exactly ONE point has the candidate set, the heuristic's selection, and the stage-3 prompt text simultaneously live: `daemon/services/snapshot_search_service.py:667-685`, inside `_llm_select` (def `:578`, `return picked` `:685`). RECOMMENDED HOOK `:683-685` (last point where `picked` is final AND the prompt/evidence is still live); earliest viable `:667` (`parsed` there is pre-dedup/pre-limit/unvalidated). Anchors: `candidates` = `reranked[:_LLM_SELECT_TOP_N]` `:625`; `parsed` `:667`; `snap_by_id` `:672`; `picked` `:673-677`; prompt chain `lines` `:628` → `candidates_block` `:644` → `system_prompt` `:646-655` → `user_prompt` `:656-661`; content lines `:640-642`. COVERAGE NOTE: the `:399-400` "no chat model configured" path bypasses `_llm_select` entirely ⇒ an overlay inside `_llm_select` never consults on that path. `daemon/tools/snapshot_tools.py:549` is NOT the seam — it is one of THREE consumers of `search()` (others `:671-677`, `:875-882`); a seam inside `SnapshotSearchService` covers all three with ZERO consumer change ⇒ the design's §5.1 pilot rationale is VERIFIED.

**Content-free hazard — the row must be assembled BY HAND.** At the hook point 17 names are live and 15 carry user content or model text. `Snapshot.to_dict()` (`daemon/repositories/snapshot/models.py:231-252`) returns EVERY field (title/task_summary/domain_tags/full digest) — `model_dump()`/`vars()`/a generic dump helper all LEAK. The service is DUCK-TYPED (`getattr(snap, "id", "?")` `:630`; test doubles are `FakeSnapshot` with only `.id`, `tests/unit/test_snapshot_search_service.py:83-101`) ⇒ an id extractor must tolerate arbitrary stand-ins. NO content hash exists: the only sha256 is the capture idempotency key (`daemon/services/snapshot_executor.py:587-592`, derived from the transcript, not persisted on the row); `git_sha` (`:214-216`) is a nullable repo anchor, NOT a content hash ⇒ compute your own hash (consistent with `controls-and-phasing.md` §2.6). `_parse_selection` accepts ANY non-empty string (`:806-809`) and unknown ids are logged VERBATIM into the agent-readable daemon log (`:679-682`) ⇒ `parsed` is NOT a set of candidate ids ⇒ INTERSECT AGAINST `candidates` BEFORE WRITING ANY ROW, write VALIDATED ids only. The pre-existing malformed-JSON→empty path (`:667-669` returns `[]` — NOT an exception, so `search()`'s `:410-415` fallback is NOT reached ⇒ `{"results": [], "error": None}`) contradicts the module docstring `:27-28` and is UNPINNED (the only fallback test, `tests/unit/test_snapshot_search_service.py:691-720`, uses a RAISED RuntimeError). ⇒ ADD to `controls-and-phasing.md` §2.6: the shadow row is assembled BY HAND, ids are validated against the candidate set BEFORE any row is written, and the overlay's "no answer" surfaces as a FAILURE, never an empty selection.

---

## §6 Notes

- House-consistent front-matter: normative source is `scope-and-doctrine.md` (cited by §id, never re-quoted); this file is the decisions record, not doctrine.
- The design set now carries **zero** open questions (Q1 and Q2 settled 2026-11-09). Any future question must be raised as a new ruling, not re-opened here.
- Corrections to proxy anchors are **published here**, never silently patched into the design docs' anchor tables (house convention).