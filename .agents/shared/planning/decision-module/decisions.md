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
| **L3** | Registration path: families register through the WIRING registrar (`wiring/registry.py:74-185`, `register_family_metric`), NOT raw `cm.register_*` — AM-2 forces `allowed_values` per label key (the enforcement the "30 rows → 25 failure values → 19 DISTINCT failure_class labels" roster needs) and AM-3 makes `source_idiom`/`durability`/`cardinality_weight` required kwargs. Costs an adjudication-pack amendment (`wiring/adjudication.py:90` `REGISTERED_FAMILIES`, the hardcoded 27-family set `:104-111`, `FAMILY_SERIES_BANDS` `:158`, global measured `<=2000` cap `:457-480`) — the implementation lane is landing it. `test_the_daemon_diff_is_confined_to_the_allowed_files` (`tests/unit/metrics/test_metrics_family_adjudication.py:215-234`) may already be RED at base (known-stale pin, `PACKS.md:216-219`). **UPDATE — the pack amendment LANDED and the primary path is what shipped:** all 19 families registered through the WIRING registrar (`register_family_metric`, `family="decision_overlay"`, `cluster="W5"`, `verdict="IN"`), so AM-2's `allowed_values`-per-label-key refusal and AM-3's required kwargs are STRUCTURAL; the raw `cm.register_*` fallback is GONE. The adjudication pack was amended (verdict roster, `REGISTERED_FAMILIES`, `FAMILY_SERIES_BANDS`) and the pack test's census expectations were TIGHTENED, not weakened (roster count 27→28, binding subset test renamed to the 18-of-28 shape, per the F23 precedent at `.agents/shared/planning/nest-anomaly-report/phase3-plan.md:232`). The new family's verdict id is **`decision_overlay`** (one id for all 19 series); the family is IN the census; `FAMILY_SERIES_BANDS["decision_overlay"] = 411` — the MEASURED closed space, zero-headroom (F23's convention), with the pack's own rule that **a vocabulary extension MUST re-declare that band in the SAME change** (a new seam id or a new failure class). | `decisions.md` (this row + metric roster section). |
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
| `thin-client.md` §6 row 10 ("a 400 ⇒ failure `http_400`") | CORRECTED — a shape-400 WITHOUT `code:"decision_misroute"` is the SUPPRESSED `invalid_request` class (proxy request-shape validation fires BEFORE model resolution); a 400 WITH `code:"decision_misroute"` keeps `http_400` + the `provisioning_missing` verdict | `http_400` remains a live failure label (carried by the misroute case); `invalid_request` was already in the suppressed vocabulary (pre-flight row 4), so this is a REASSIGNMENT of an existing label, not a new one. The two vocabularies stay disjoint and the roster's distinct-label count is unchanged. The 30-row table shape is intact (30 rows; 5 pre-flight suppressions; 25 failure values over 19 distinct labels) — only the CLASSIFICATION of the bare-400 case moves. |
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

Published from the implementation reports (core implementation lane + wiring round). Each item is a deviation from the design text, stated plainly with its source.

1. **The 400 taxonomy.** A shape-400 without `code:"decision_misroute"` is the SUPPRESSED `invalid_request` class (a reassignment of an existing label, not a new one); the `decision_misroute` 400 keeps `http_400` + `provisioning_missing`; a 404 carrying the proxy's error envelope is the first probe-provable provisioning verdict; 401/403/405 are their own classes; a 502 relays the upstream envelope verbatim, is classified by status alone, proves NOTHING about provisioning and must NEVER park a seam NOT_ENABLED. Cross-reference the D6 row (already published) — do not duplicate it.
2. **The census band is zero-headroom** (411 = the measured space) ⇒ any vocabulary extension MUST re-declare the band in the same change.
3. **The pack test now imports the decision module** (`from daemon.services.decision import metrics as decision_metrics`). This is a **cross-lane import inside the metrics pack**, forced by the census's own rule ("a family in the wiring registry must be in the pack's map") together with the fact that this family registers from OUTSIDE the wiring package. The alternatives were worse: importing the decision module from the wiring package's `__init__` inverts the dependency, and the raw registrar escaped the census altogether.
4. **The daemon-diff allowlist now lists the 10 new producer files** — the pin's intent is "no producer module grew an observe/inc call"; these are a NEW lane's own producers registered through the wiring registrar like every other family, so the entry is the same shape as the query package's own surfaces. The pin's verdict is UNCHANGED (still RED, still `daemon/__init__.py` first).
5. **`DECISION_MODEL` unset vs explicitly empty — an OPEN QUESTION for the Emperor/leader, recorded here with BOTH sources quoted.** The design's D1 and `thin-client.md` §3 say `DECISION_MODEL` unset ≡ empty ≡ disabled; the leader's L6 says it "defaults to `decision`". The implementation resolves the sent model id to the purpose-id `"decision"` when unset (never `OPENAI_MODEL`), and the field default stays `""` so the raw state is observable. **The difference is unobservable in P1** because the global master and the per-seam switch are both default OFF, so nothing is consulted either way. Record it as an open question for ratification — NOT as a settled fact.
6. **Unverifiable in P1, by design** — the live proxy's behaviour against a real decision model row (nothing was contacted; no credential, no proxy-store write); `AppSettingKillStore` / `SettingsService.set_override` against a live PostgreSQL (the tests use the in-memory store); the durable month-to-date `CostAccumulator` (that is the wiring round's file); and the real board transaction (the test uses a fake `BoardService` — the `db_offload` thread path is exercised, the live transaction is not).
7. **`AppSettingKillStore` is retained as the pre-wiring fallback** (it is the resolver's DB tier when no reader is injected); the wiring round binds the settings-service reader + writer so the production path resolves in one place. Record it as a deliberate retention, not an omission.

8. **No new metric label values and no new seam id:** the wiring used only `snapshot.l3.selection` (already inside the bounded known-seam set), so `FAMILY_SERIES_BANDS["decision_overlay"]` stays at **411** and the zero-headroom band rule was **not** triggered.
9. **The wiring round reported ZERO deviations** and stayed strictly within its assigned files.
10. **A developer-verification finding:** `daemon/services/decision/config.py:331` accessed `model_fields` **on an instance**, raising `PydanticDeprecatedSince211` — **removal in Pydantic v3**, so config resolution would break on the next major with no test failing today. Found by independent verification on the settled tree (commit `c0801c9cf`), not reported by either implementation lane; a behaviour-preserving fix plus a regression pin is in flight in the same branch. Status at the time of this pass: **fix in flight** (if it has landed when you commit, say **fixed (commit `<sha>`)**; if not, say **fix in flight** — either is honest, do not guess). Cross-reference the `AliasChoices`/`validation_alias` mechanism: the alias choices published in §12b ARE the env-name contract and are implemented via Pydantic's `validation_alias` (`AliasChoices`), so the `model_fields` access at `:331` is the one Pydantic-API call that must be corrected before the next major.

**OPEN QUESTION (explicitly labelled, distinct from the settled Q1/Q2):** `DECISION_MODEL` unset vs explicitly empty (item 5 above). The difference is unobservable in P1. Awaiting ratification.

**WIRING STATE (published as ACTUAL, not contract):** the settings-key table (§12) now records the real registered specs (value_type, bounds, allowed_values, requires_restart, sensitive, env_var, config_path) as registered; the S1 wiring facts (§13) and the implementation base state (§15) are published as measured facts (commit `c0801c9cf`). The wiring round reported ZERO deviations and stayed within its assigned files. The wiring state is no longer pending.

---

## §6 Notes

- House-consistent front-matter: normative source is `scope-and-doctrine.md` (cited by §id, never re-quoted); this file is the decisions record, not doctrine.
- The design set now carries **zero** open questions (Q1 and Q2 settled 2026-11-09). Any future question must be raised as a new ruling, not re-opened here.
- Corrections to proxy anchors are **published here**, never silently patched into the design docs' anchor tables (house convention).

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

## §12 Settings-key registration table (L7) — ACTUAL REGISTERED STATE (wiring round landed)

P1 registers these keys in `MANAGED_KEYS` (no new table, no migration; the generic `AppSetting` row model suffices):

| Key | value_type | default | bounds | allowed_values | requires_restart | sensitive | env_var | config_path |
|---|---|---|---|---|---|---|---|---|
| `decision_global_enabled` | int | 0 | ge=0 le=1 | — | False | False | `DECISION_ENABLED` | `decision.global_enabled` |
| `decision_seam_snapshot_l3_selection_enabled` | int | 0 | ge=0 le=1 | — | False | False | `DECISION_SEAM_SNAPSHOT_L3_SELECTION` | `decision.seams.snapshot_l3_selection_enabled` |
| `decision_seam_snapshot_l3_selection_stage` | str | `shadow` | — | `{shadow, deciding}` | False | False | `DECISION_SEAM_SNAPSHOT_L3_SELECTION_STAGE` | `decision.seams.snapshot_l3_selection_stage` |

- **The additive change is IN:** `ManagedKeySpec` gained an OPTIONAL `allowed_values` (`None` ⇒ today's behaviour, fully backward-compatible), surfaced in `constraint_desc()` as `allowed_values=['deciding', 'shadow']`, and membership is enforced in `parse_and_validate` — a stage typo is **422 at write time**. This is what makes the ratified kill semantics' stage key self-validating.
- **The invalidation hook is IN:** `SettingsService` gained `register_override_changed_callback(callback)`; **both** `set_override` and `clear_override` notify the registered callbacks **after** `session.commit()`. `settings_service.py` has **ZERO** imports from `daemon.services.decision` — no circular import; the callback is registered dynamically in `daemon/manager.py`.
- **The YAML tier is IN:** `daemon/config.py` gained `_RAW_YAML_DECISION` / `raw_yaml_decision()`, and the settings resolver's `_raw_yaml_leaf` reads it, so the settings service's yaml tier and the `Config.decision` object serve the SAME nested structure. The two keying schemes agree: the `config_path` walk (`config.decision.global_enabled`, `config.decision.seams.snapshot_l3_selection_{enabled,stage}`) and the store-key mapping rendered by `yaml_switch_tier()` / `seam_switch_tier()` (`decision_global_enabled`, `decision_seam_snapshot_l3_selection_{enabled,stage}`).

All three keys have `requires_restart=False` (live, no restart needed for a flip).

---

## §12b `Config.decision` shape (implementation fact)

`Config.decision` = `DecisionConfig(BaseSettings)` with `env_prefix="DECISION_"`, bound on `Config` as `decision: DecisionConfig = Field(default_factory=DecisionConfig)`.

| Field | Type / default | Env |
|---|---|---|
| `base_url` | `str = ""` ⇒ falls back to `OPENAI_BASE_URL` (reuse, never mint) | `DECISION_BASE_URL` |
| `api_key` | `str = ""` ⇒ `OPENAI_API_KEY` | `DECISION_API_KEY` |
| `model` | `str = ""` ⇒ resolves to the purpose-id `decision`; **never** `OPENAI_MODEL` | `DECISION_MODEL` |
| `timeout_s` | `float = 5.0` (gt 0) | `DECISION_TIMEOUT_S` |
| `cost_ceiling_usd` | `float = 50.0` (gt 0 — the ratified global ceiling) | `DECISION_COST_CEILING_USD` |
| `token_ceiling` | `int = 10_000_000` (gt 0 — the L8 fail-safe) | `DECISION_TOKEN_CEILING` |
| `pricing_usd_per_million_tokens` | `str = ""` (JSON object string — the operator-configured rate table) | `DECISION_PRICING_USD_PER_MILLION_TOKENS` |
| `global_enabled` | `bool = False` | `DECISION_ENABLED` **or** `DECISION_GLOBAL_ENABLED` |
| `seams` | `DecisionSeamsConfig` | — |

`DecisionSeamsConfig` (`env_prefix="DECISION_SEAM_"`): `snapshot_l3_selection_enabled: bool = False` (aliases `DECISION_SEAM_SNAPSHOT_L3_SELECTION` / `DECISION_SEAM_SNAPSHOT_L3_SELECTION_ENABLED`), `snapshot_l3_selection_stage: str = "shadow"` (alias `DECISION_SEAM_SNAPSHOT_L3_SELECTION_STAGE`).

The MANAGED-KEY `env_var` tier (`DECISION_ENABLED`, `DECISION_SEAM_SNAPSHOT_L3_SELECTION`) is deliberately **distinct** from the config namespace's env prefix — two surfaces, ONE ladder. Base/key **reuse** the `OPENAI_*` values; `"decision"` is **already** in `_ALLOWED_MODELS_DEFAULT` (not re-added).

The `config.yaml` `decision:` block (ratified defaults): `base_url: ""`, `api_key: ""`, `model: ""`, `timeout_s: 5.0`, `cost_ceiling_usd: 50.0`, `token_ceiling: 10000000`, `pricing_usd_per_million_tokens: ""`, `global_enabled: false`, `seams: {snapshot_l3_selection_enabled: false, snapshot_l3_selection_stage: "shadow"}`.

The `.env.example` additions: `DECISION_BASE_URL`, `DECISION_API_KEY`, `DECISION_MODEL`, `DECISION_TIMEOUT_S`, `DECISION_COST_CEILING_USD`, `DECISION_TOKEN_CEILING`, `DECISION_PRICING_USD_PER_MILLION_TOKENS`, `DECISION_ENABLED`, `DECISION_SEAM_SNAPSHOT_L3_SELECTION`, `DECISION_SEAM_SNAPSHOT_L3_SELECTION_STAGE`.

---

## §13 S1 hook site + coverage note + by-hand shadow-row rule (more verified facts)

**S1 hook site.** Exactly ONE point has the candidate set, the heuristic's selection, and the stage-3 prompt text simultaneously live: `daemon/services/snapshot_search_service.py:667-685`, inside `_llm_select` (def `:578`, `return picked` `:685`). RECOMMENDED HOOK `:683-685` (last point where `picked` is final AND the prompt/evidence is still live); earliest viable `:667` (`parsed` there is pre-dedup/pre-limit/unvalidated). Anchors: `candidates` = `reranked[:_LLM_SELECT_TOP_N]` `:625`; `parsed` `:667`; `snap_by_id` `:672`; `picked` `:673-677`; prompt chain `lines` `:628` → `candidates_block` `:644` → `system_prompt` `:646-655` → `user_prompt` `:656-661`; content lines `:640-642`. COVERAGE NOTE: the `:399-400` "no chat model configured" path bypasses `_llm_select` entirely ⇒ an overlay inside `_llm_select` never consults on that path. `daemon/tools/snapshot_tools.py:549` is NOT the seam — it is one of THREE consumers of `search()` (others `:671-677`, `:875-882`); a seam inside `SnapshotSearchService` covers all three with ZERO consumer change ⇒ the design's §5.1 pilot rationale is VERIFIED.

**Content-free hazard — the row must be assembled BY HAND.** At the hook point 17 names are live and 15 carry user content or model text. `Snapshot.to_dict()` (`daemon/repositories/snapshot/models.py:231-252`) returns EVERY field (title/task_summary/domain_tags/full digest) — `model_dump()`/`vars()`/a generic dump helper all LEAK. The service is DUCK-TYPED (`getattr(snap, "id", "?")` `:630`; test doubles are `FakeSnapshot` with only `.id`, `tests/unit/test_snapshot_search_service.py:83-101`) ⇒ an id extractor must tolerate arbitrary stand-ins. NO content hash exists: the only sha256 is the capture idempotency key (`daemon/services/snapshot_executor.py:587-592`, derived from the transcript, not persisted on the row); `git_sha` (`:214-216`) is a nullable repo anchor, NOT a content hash ⇒ compute your own hash (consistent with `controls-and-phasing.md` §2.6). `_parse_selection` accepts ANY non-empty string (`:806-809`) and unknown ids are logged VERBATIM into the agent-readable daemon log (`:679-682`) ⇒ `parsed` is NOT a set of candidate ids ⇒ INTERSECT AGAINST `candidates` BEFORE WRITING ANY ROW, write VALIDATED ids only. The pre-existing malformed-JSON→empty path (`:667-669` returns `[]` — NOT an exception, so `search()`'s `:410-415` fallback is NOT reached ⇒ `{"results": [], "error": None}`) contradicts the module docstring `:27-28` and is UNPINNED (the only fallback test, `tests/unit/test_snapshot_search_service.py:691-720`, uses a RAISED RuntimeError). ⇒ ADD to `controls-and-phasing.md` §2.6: the shadow row is assembled BY HAND, ids are validated against the candidate set BEFORE any row is written, and the overlay's "no answer" surfaces as a FAILURE, never an empty selection.

**S1 wiring facts (implementation, landed).** The hook lives at `daemon/services/snapshot_search_service.py:689-718` — the last point where `picked` is final and the prompt/evidence is still live. It calls **OUTWARD** and the shipped `picked` is returned **completely unaltered**; the consult is wrapped so a raise is non-fatal (logged, never propagated). It passes: the validated candidate ids, the heuristic's picked ids, the query, the candidates block, the system prompt, the user prompt and the limit — and it uses the core's `extract_snapshot_ids` (id-only, duck-tolerant). The file imports ONLY from `daemon.services.decision.port` — **no observer module, and no lowercase `metrics` token** (the whole-file substring ban at `tests/unit/tools/test_snapshot_v3.py:1645` is respected); the house import-graph pin passes 57. The coverage note is at `:401-405`: the `:399-400` "no chat model configured" path bypasses `_llm_select` entirely ⇒ no overlay consult on that path. **The manager wiring:** `daemon/manager.py` constructs `AppSettingCostAccumulator(engine)`, `DecisionClient`, `SeamRegistry` (with S1 registered via `build_s1_seam_record()`), `AppSettingKillStore` + `SettingsServiceKillStore`, `KillChannelResolver` (with `override_writer` → `SettingsService.set_override`, dispatched off-loop through `db_offload`), and `DecisionOverlay(registry, kill, shadow_writer, config, accumulator, board_service=self._board_service)`; injects `decision_overlay=` into `SnapshotSearchService`; registers the settings invalidation callback in `set_settings_service`; and calls `await self._decision_kill_resolver.reverify(["snapshot.l3.selection"])` in `initialize()` — the **boot re-verification** that makes a kill survive a restart. **The durable cost ledger** (`daemon/services/decision/cost_store.py`): `app_settings` rows via the `AppSetting` model, deliberately **NOT** in `MANAGED_KEYS` so the settings router 404s them — operator-invisible and un-editable (the operator must not be able to edit the meter). Period-keyed `decision_cost_ledger_{YYYY-MM}` so a new calendar month starts at zero with **no reset job**. Every consult updates the row, so a daemon restart reads the month back — **a restart cannot reset month-to-date spend and defeat the global ceiling**. All DB access through `daemon.services.db_offload.offload` — no sync DB on the event loop.

---

## §14 Final metric-family roster (19 names + family id + bounded label sets)

Published as the measured registered state. The family id is **`decision_overlay`** (one id for all 19 series; `chart_role=None` ⇒ **not** chart-primary ⇒ it does NOT join the chart roster). `FAMILY_SERIES_BANDS["decision_overlay"] = 411` — the MEASURED closed space, zero-headroom (F23's convention); a vocabulary extension MUST re-declare that band in the SAME change (a new seam id or a new failure class).

| Registered name | Label set |
|---|---|
| `ensemble_decision_overlay_attempts_total` | seam_id, mode |
| `ensemble_decision_overlay_failures_total` | seam_id, failure_class, mode |
| `ensemble_decision_overlay_fallbacks_total` | seam_id, mode |
| `ensemble_decision_overlay_latency_seconds` | seam_id |
| `ensemble_decision_overlay_cost_total` | seam_id |
| `ensemble_decision_overlay_cost_tokens_total` | seam_id (the L8 token meter) |
| `ensemble_decision_overlay_floor_rejections_total` | seam_id, mode |
| `ensemble_decision_overlay_suppressed_total` | seam_id, reason |
| `ensemble_decision_overlay_shadow_agreement_total` | seam_id, verdict (NO question_id) |
| `ensemble_decision_overlay_shadow_disagreement_total` | seam_id |
| `ensemble_decision_overlay_shadow_latency_seconds` | seam_id |
| `ensemble_decision_overlay_liveness_alarm_total` | seam_id, reason |
| `ensemble_decision_overlay_provisioning_state` | seam_id, state |
| `ensemble_decision_overlay_shadow_write_failures_total` | (none) |
| `ensemble_decision_overlay_shadow_rows_dropped_total` | seam_id |
| `ensemble_decision_overlay_switch_reverify_total` | seam_id, outcome |
| `ensemble_decision_overlay_cost_trip_total` | reason |
| `ensemble_decision_overlay_shadow_selection_agreement_total` | seam_id |
| `ensemble_decision_overlay_shadow_selection_overlap_ratio` | seam_id |

**Bounded label vocabularies:** `seam_id` (the 4 design catalog seams) · `mode` {shadow, deciding} · `failure_class` (**19**) · `reason` (**10** = the 9 + `pricing_unconfigured`) · `verdict` {agree, disagree, no_overlay} · `outcome` {verified_off, verified_on, unreadable} · `state` {ok, provisioning_missing, allow_list_excluded}. The `seam_id` label is bounded to the design's 4 catalog seams; a seam outside that set folds to the registry's reserved `other` — a future lane MUST extend the known-seam set when it registers a new seam.

---

## §15 Implementation base state (factual record, not a claim of success)

- `tests/unit/decision/` — **173 passed** (167 + the 6 new wiring/behaviour tests), 0 failed.
- `tests/unit/metrics/` — **531 passed, 6 failed, 1 error** — **byte-identical to its base state** (the same node names before and after the change). All six failures plus the error are **PRE-EXISTING**: the `test_the_daemon_diff_is_confined_to_the_allowed_files` pin is **RED AT BASE** on `daemon/__init__.py` (a committed change from another lane since the pin's `LANE_BASE` `644c2bfaaa48514ec7de79f7f4bcd078a700cdbe`; `PACKS.md:216-219` records it as a known-stale pin), the F21-journal plan assertion, 3× `AuthConfig` discovery/issuer errors, and the metrics scope-cage route census. After the change the pin's verdict is UNCHANGED and the 10 new files are **0 offenders** (measured: 101 `daemon/` changes vs `LANE_BASE`, 80 pre-existing offenders). The global series cap needed **no** change: `BASELINE_SERIES (89) + measured 817 = 906 ≤ 2000`.
- The house import-graph pin `tests/unit/tools/test_snapshot_v3.py` — **57 passed** · `tests/unit/test_snapshot_search_service.py` — **42 passed** · `tests/test_settings_resolution.py` — **36 passed**.
- **The developer's own independent verification on the settled tree** (commit `c0801c9cf`): the lane's change set is **38 files, +10,795/-98**; `git status` clean; `uv run pytest tests/unit/decision/ -q` ⇒ **173 passed, 0 failed in 5.97s**, matching the lane's own report exactly. Publish this as the independent-verification record.
- The 3 behavioural obligations and their test node ids (publish verbatim): `tests/unit/decision/test_s1_wiring_behaviour.py::test_s1_overlay_active_produces_identical_selection_and_writes_shadow_row` (overlay ACTIVE ⇒ selection IDENTICAL to heuristic-only `['snap-1','snap-3']`, exactly 1 proxy call, exactly 1 shadow row with `seam_id="snapshot.l3.selection"`, `mode="shadow"`) · `::test_s1_overlay_inactive_produces_byte_identical_results_and_no_call_made` (switch OFF/unconfigured ⇒ byte-identical `['snap-1']` and **0** socket calls) · `::test_kill_switch_flip_takes_effect_without_restart_and_survives_restart` (an operator override to 0 suppresses the **next** consult with **no restart** and freezes proxy calls at 1; a simulated reboot (cache dropped, resolver re-initialised over the DB) yields `reverify` outcome `verified_off` and still 0 new calls).

Publish these as the measured base state for the lane, with the pre-existing-red provenance quoted.