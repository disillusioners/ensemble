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
| Canonical System One wire shape (v185 nested) | **v187 canonical required**: `choice` → `{"criteria": {label: description}}`; `score` → `{"criteria": ["label", ...]}` | load-bearing: 9router + v185 nested ⇒ 422; OpenRouter + v185 nested ⇒ 400 Zod "expected record … path [questions,probe_q1,criteria]"; both + v187 canonical ⇒ 200. Non-canonical on a working path ⇒ 4xx that fails open forever (silently dead overlay). |
| `daemon/config.py:5925` (allow-list member `decision`) and `.env.example:268-276` (doc block) | **verified still correct at HEAD `818411107`** | Verified by grep at HEAD 818411107: allow-list member at `:5925`, doc block at `:268-276`. The provider note's claim of `:4957` / `:208` does **not** match this worktree; the design's numbers stand. Commit verified against: `818411107b8836762f8182fca41194e0dc02747a`. |

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

## §6 Notes

- House-consistent front-matter: normative source is `scope-and-doctrine.md` (cited by §id, never re-quoted); this file is the decisions record, not doctrine.
- The design set now carries **zero** open questions (Q1 and Q2 settled 2026-11-09). Any future question must be raised as a new ruling, not re-opened here.
- Corrections to proxy anchors are **published here**, never silently patched into the design docs' anchor tables (house convention).