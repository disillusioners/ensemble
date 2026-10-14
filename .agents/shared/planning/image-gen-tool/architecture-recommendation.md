# Architecture Recommendation: Image-Gen Tool (`generate_image` via llmproxy `/v1/image_generation`)

Date: 2026-10-14T19:12Z (UTC)
Architect instance: this commission's architect (controller)
Analyst instances: 3d397623 (trade-off-analysis, D1 tiering) · 46c8db8a (data-flow-design, tool contract) · fe8f7335 (structural-design, seams/registry/phases)
Method: 3-worker parallel fan-out over plan files + worktree source (`/home/ensemble-worktrees/image-gen-tool`, branch `feature/image-gen-tool`, base latest @ 8c525315e) + read-only proxy source (`/home/nea/Code/opensource-projects/llm-supervisor-proxy`). All findings below are evidence-cited by the analyst workers; every claim was verified against actual source, not the plan's self-description.

---

## 0. Overall Verdict

**APPROVE WITH AMENDMENTS.** The plan is architecturally sound: both contested decisions (D1 tiering, D2 marker family) are independently CONFIRMED, all registry/allowlist/seal mechanics verified against source, and no over-engineering was found. The amendments (§6, AM1–AM13) are precise corrections — mostly contract-shape clarifications against the actual proxy relay behavior and one frontend feasibility fix — plus P1 day-1 gap-fills. None are structural; the planner can fold them in without re-planning. **A developer can start P1 immediately after the planner folds AM1–AM10 + AM12 into phase1-plan.md and AM11 into phase3-plan.md.**

---

## 1. D1 Verdict — CONFIRM NATIVE-CORE (High confidence)

**The planner's native-core recommendation is independently confirmed.** Weighted 5-axis comparison (analyst 3d397623):

| Approach | Complexity | Scalability | Maintainability | Risk | Cost | Weighted | Recommendation |
|----------|-----------|-------------|-----------------|------|------|----------|----------------|
| **A: Native-core** (`daemon/tools/image_gen.py`, category `image-gen`) | Low — single module, mirrors `chart_tools.py:407` (`@register_tool_category("chart")`) and `ens_env_tools.py:253` | High — extends the existing native LLM lane (stateless, fail-closed) | High — precedent parity, nothing new to learn | Low — no new seam | Low — zero new infra | **5.00** | ✅ **ADOPT** |
| B: Plugin path-B (Port-wrapping) | High — MANIFEST + port + adapter + tool factory + seam gate + sync runner | Med — room to grow, but ceremony for one capability | Low — 3-tier coupling for one endpoint | Med — pin ceremony with nothing to pin | High — plugin core ≈357 KB machinery for one call | 2.45 | Reject |
| C: Plugin path-C (Resource-izing) | Very high — manifest would contain only `own_ourtright` | Low — no resources to snapshot | Low | Med — no upstream-tracking signal | Very high — pure ceremony | 1.80 | Reject |

### Why the plugin tier is wrong here (evidence)

1. **Path B is falsified by the convention itself**: `plugins-convention/path_types.yaml` requires `lifted_symbol` + `entrypoint` + `ipc_version` for path-B declarations — llmproxy exposes none. The registry would refuse the declaration.
2. **llmproxy is existing native infra, not an external OSS subsystem**: `cmd/main.go:291` mounts `/v1/image_generation` on the SAME proxy the ensemble already natively integrates via `OPENAI_BASE_URL`/`OPENAI_API_KEY` (chat pool, embeddings via `skill_embedding_service.py:152,203`, OD vision lane). The plugin tier exists to vendor and snapshot external OSS **resources** — there is nothing to vendor.
3. **Postgres analogy holds**: Postgres is external OSS, native-core in `daemon/repositories/`, zero plugin-tier treatment. Same pattern fits any future capability (translation, OCR) added to the existing proxy lane.
4. **The OD counter-example defines the line**: opendesign is plugin-tier because of (a) tag-pinned tracked-by-ref upstream (`plugins/opendesign/MANIFEST.yaml` `tag_pin_per_class`), (b) 388 vendored resource files, (c) a real lifted symbol with an IPC contract (`ipc_version: 1`). llmproxy has none of (a)(b)(c).
5. `IsImageGen()` early-reject at `pkg/imggen/handler.go:455-458` is routing purity (keeps image models out of `/v1/chat/completions`), not a vendoring hint.

### Universal-wrapper clause resolution (record in D1 consequences)

🔴 **Governance risk — the "external process" misreading.** `plugin_path_model` says "nothing external bypasses the wrapper layer." Read literally, this clause would demand plugin-tier treatment for Postgres and every native proxy call. **Resolution (binding for this project)**: the clause is INSTRUMENTAL — its job is to gate **unvendored external OSS resources** (the OD case), not pre-existing native capability calls on the ensemble's own egress lane. llmproxy entered via tier-1 native integration before tier-2 was ratified. **Fold this resolution into decisions.md D1 Consequences** so the clause cannot be weaponized later against native-core integrations.

### Flip conditions (what WOULD justify re-tiering)

1. ≥2 additional image providers with materially different contracts **AND** vendored per-provider resources (prompt packs, style systems) — both conditions necessary.
2. Upstream starts publishing resource-level artifacts we consume at the resource level (path-C target appears).
3. The endpoint moves out of the ensemble's own LLM lane into a third-party tracked-by-ref service.
4. A tier-2 CI gate mandating port-wrapping for ALL external capabilities is introduced (does not exist; `path_types.yaml` is per-plugin-declaration, not per-capability-call).

**Assumption that would flip this verdict**: none realistic — the path-B registry refusal is structural, not judgment.

---

## 2. Tool Design Contract Audit — SOUND, with corrections (AM1–AM10)

Analyst 46c8db8a verified the contract against the proxy source and daemon conventions. Route/method, auth, timeout default (120s, `handler.go:42`), response single-object shape (live-verified `live_e2e_test.go:126-127`), string counts (`coerceCount` `handler.go:786-833`), `base_resp.status_code==0` gate (`minimax.go:116-143`), request limits (n 1-9, prompt ≤1500, 512-2048÷8 pair-only, aspect-wins), 10 RPM, OSS ~24h TTL — **all confirmed**. `image_save` signature, OD purpose-bound resolver pattern, MockTransport test pattern (real range `:325-478`, off-by-one immaterial), single-attempt-by-construction (httpx default 0 retries; proxy itself single-attempt `handler.go:140-141`), and the hybrid error convention (loop breaker `graph.py:1010-1049` does not inspect response strings; report repair unaffected) — **all sound**.

**Corrections required** (full amendment text in §6):

- **AM1 (🟡) Proxy error-body shapes**: the plan conflates upstream-shape with proxy-shape. Upstream MiniMax app errors ride HTTP 200 with non-zero `base_resp.status_code`, but the **proxy relays them as HTTP 502 with the upstream body preserved verbatim** (`handler.go:445-475`) — the tool must parse `base_resp.status_code`/`status_msg` out of a 502 body. Proxy **deadline** (504) produces a self-generated OpenAI envelope `{"error":{"type":"server_error","message":"upstream request failed: ..."}}` (`models/errors.go:43-53`) — the tool must parse `error.message`.
- **AM6 (🟡) Timeout placement is ambiguous in the plan**: "entire network phase" must be pinned to **POST + body read + parse ONLY**; each OSS fetch sits **OUTSIDE** the 240s `wait_for` with its own 30s cap. Worst-case total wall clock = 240 + 4×30 = **360s**. On the live lane the proxy 504 (120s) fires first, so the tool never hangs.
- **AM4 (🟡) `IMAGE_GEN_MODEL` value contract**: it holds the proxy-registered **client-facing** model id (e.g. `minimax-image-01`); the proxy rewrites to `cred.InternalModel` upstream (`minimax.go:60-66`).
- **AM2 (🟡)** No app-error code whitelisting — fail closed on ANY non-zero `base_resp.status_code` (1026 never exercised live). **AM3 (🟡)** `prompt_optimizer` defaults False. **AM5 (🟡)** empty `image_urls` on status 0 → `ok:true` with disclosure. **AM7–AM10 (🟢)**: `response_format` not exposed; content_type inference chain (header → extension → `image/jpeg`); single-line JSON envelope embedding; TmpImageStore cap is 1 GiB (`tmp_image_store.py:151`), 32 MiB is the SERVE-side cap (`live_views.py:137`) — n=4×15MB=60MB is safe by construction.

**Over-engineering: none found.** All caps and exclusions are justified by upstream limits or arithmetic.

---

## 3. Marker-Family Design — Option B (parallel/3-pass) CONFIRMED

| Approach | Complexity | Scalability | Maintainability | Risk | Cost | Recommendation |
|----------|-----------|-------------|-----------------|------|------|----------------|
| A: Widen the locked regex | Lower LOC | Same | Same | 🔴 **Critical** — modifies a byte-pinned R5-gated regex; violates `dispatcher.py:27-28` in-file gate AND `tests/test_chart_image_delivery_audit.py:56` `LOCKED_MARKER_REGEX` audit pin; requires R5 re-review this commission cannot grant | Low | **REJECT** |
| **B: Parallel family pass** (plan's pick) | Slightly higher (one more regex + gate) | High — future features extend `ALLOWED_FEATURES` only | High — F12 invariant extended additively | Low — locked pass byte-identical by construction; chart corpus passes unmodified | Low | ✅ **ADOPT** |

**Load-bearing detail (🟡)**: the generalized near-miss sweeper `ens-img:[a-z0-9-]+:[^>\n]{0,64}` **would textually match well-formed family markers** (32-hex ⊂ `[^>\n]{0,64}`). Safety exists ONLY under the enforced pass order locked → family → sweeper-on-unclaimed-lines. The P2 invariant comment and the sweeper-last-on-residual test are not optional documentation — they are the correctness proof. Keep both (AM13).

**Pre-P2 leak window (bounded, accepted)**: the current sweeper is chart-render-only, so between P1 and P2 a well-formed image-gen marker would leak as literal comment text into chat — but ONLY if all four of {env configured, agent invokes tool, chat routing active, pre-P2 window} co-occur. The natural gate is D4's fail-closed default: `IMAGE_GEN_MODEL` unset ⇒ no markers can exist. No additional gating needed; note it in P1 (AM13).

**🟡 Frontend feasibility correction (AM11 — the one real design flaw found)**: phase3-plan.md says the scanner scans "RENDERED text nodes". **ngx-markdown/marked emits `<!-- ... -->` as HTML COMMENT nodes (`nodeType === 8`), not text nodes** — `textContent` scans will never see the marker. `scanForEnsImgMarkers()` must traverse `bubble.childNodes`, filter `nodeType === Node.COMMENT_NODE`, match `node.data` against the family regex, then `node.replaceWith(img)`. Without this correction P3's core task is unimplementable as written. (The existing chip scanner works on `<a>` elements — a different node kind — which is why the pattern didn't transfer.)

Delete-after-delivery reuse (`dispatcher.py:388-408`/`:508-528`), `delivered_image_ids`, both-seam wiring (`:348`/`:472`), and API/no-colon skip (`:302-304`) are compatible unchanged.

---

## 4. Category + Allowlist Mechanics — VERIFIED; 6-agent set is FINAL

All mechanics verified in-worktree: `CATEGORY_MODULES` (`_tool_registry.py:594-595`), `register_tool_category` (`:223-248`), frozen-names regen (`:702-713`), `KNOWN_TOOL_NAMES` (`:718+`), all 6 consumer meta.json files carry `"image"` (explicit-add model) and none are seal-pinned. Critic-leak evidence confirmed (`agents/critic/meta.json` allow `['read_file','image','design']` + per-tool `image_save` deny) — new category `"image-gen"` avoids the leak by construction.

**TOOL_REQUIRED_AGENTS: correctly NOT needed** — `daemon/tools/_auth.py:35-51` maps only spawn-backed categories (chart → charter, image → image-reader); `generate_image` is a direct HTTP call, no agent spawn.

**Allowlist adjudication (discretionary clause)** — keep exactly the 6; all exclusions principled:

| Agent | Decision | Reason |
|---|---|---|
| ari, leader, developer[v2], reviewer, tester, worker | **INCLUDE** (the 6) | worker inclusion is CORRECT — ari dispatches worker, so the worker's deliverable surface needs generation; supervision pairing holds |
| sketcher | EXCLUDE | OD generation lane (sole `od.*` holder) — generic image-gen would blur the ratified generation-lane separation |
| designer | EXCLUDE | holds ZERO generation tools by ratified contract; orchestrator-only |
| critic | EXCLUDE | read-only design-QA gate; write-leak invariant |
| jober | EXCLUDE | job-queue management; unrelated |
| charter | EXCLUDE | chart-render is its own bounded lane; Mermaid only |
| giter, maintenancer, explorer, planner/v2, governor | EXCLUDE | no generation need in role |

Note: "guardian" does not exist in this tree (`governor` does). **No additions recommended beyond the 6.**

---

## 5. Phase Structure — CONFIRMED, with parallelization clarification

- **P2 ∥ P3 after P1: YES (spec-first).** The grammar is already frozen in decisions.md D2 — P3 can author the constants module + specs against D2 concurrently with P2's daemon work. Byte-mirror discipline holds because the contract is decided upfront. **Final integration commit must be joint (P2+P3 same-commit for any grammar change).**
- **P1 → P2 ordering: coherent.** Tool lands first; markers are inert (and cannot exist) until `IMAGE_GEN_MODEL` is configured; P4's e2e ordering completes the gate. The pre-P2 leak window is bounded by the fail-closed env default (§3).
- **Seal ceremony as tested P4 step: SOUND.** Both pinned-file trips (`dispatcher.py`, `developer[v2]/tools_note.md`) follow the established `:1226-1252` history pattern with `test_phase_d_does_not_modify_sealed_artifacts` (`:1316-1348`) as tripwire; diff-audit-before-re-pin constrains delta scope.
- **Dual-lane e2e (A dev-proxy / B user-gated-with-disclosure) + ≤5-image budget: SOUND as designed.**

---

## 6. Amendments for the Planner to Fold In

**phase1-plan.md / decisions.md (contract):**

- **AM1 (🟡)** Error-body parsing: proxy 502 (app error) = upstream body verbatim → parse `base_resp.status_code`/`status_msg` from the 502 body; proxy 504 (deadline) = OpenAI envelope → parse `error.message`. Replace the "app errors ride inside HTTP 200" wording with the proxy-shape truth.
- **AM2 (🟡)** Fail closed on ANY non-zero `base_resp.status_code`; no code whitelisting (1026 untested live).
- **AM3 (🟡)** Pin `prompt_optimizer` default = False in the args table.
- **AM4 (🟡)** `IMAGE_GEN_MODEL` holds the proxy-registered client-facing model id; proxy rewrites to InternalModel upstream.
- **AM5 (🟡)** Empty-success case: `status_code==0` ∧ `image_urls==[]` → `ok:true, images:[], usage_note` disclosure.
- **AM6 (🟡)** Timeout placement explicit: 240s `wait_for` wraps POST+body-read+parse ONLY; each OSS fetch outside with its own 30s `wait_for`; worst-case total 360s; live-lane proxy 504 fires first at 120s.
- **AM7 (🟢)** Pin `response_format` as not-exposed (upstream default `url`; never `base64`).
- **AM8 (🟢)** content_type inference: OSS response Content-Type header → URL extension sniff → default `image/jpeg` (precedent `image_tools.py:374`).
- **AM9 (🟢)** Envelope JSON embedded single-line after `"Error: "` (multiline trips `startswith("Error:")` consumers).
- **AM10 (🟢)** Cite the correct cap figure: TmpImageStore 1 GiB store cap; 32 MiB is serve-side (`live_views.py:137`); 4×15MB safe.
- **AM12 (🟢)** P1 day-1 gap-fills: module skeleton guidance (use `chart_tools.py` as structural template); meta.json edit shape (append `"image-gen"` adjacent to `"image"` + JSON-parse check); tools_note.md section template (charter chart-render section precedent); `.env.example` knob doc lines for all four IMAGE_GEN_* knobs.

**phase3-plan.md:**

- **AM11 (🟡)** Scanner target correction: HTML comments render as `COMMENT_NODE` (nodeType 8) — traverse `bubble.childNodes`, filter `nodeType===8`, match `node.data`, `node.replaceWith(img)`. Update design wording + Task 2/3 acceptance criteria.

**phase2-plan.md / decisions.md D2:**

- **AM13 (🟡)** Document the load-bearing pass order (sweeper textually matches well-formed family markers; only ordering makes it safe) + the bounded pre-P2 leak window rationale (natural gate = unset `IMAGE_GEN_MODEL`).
- **AM-D1 (🔴 governance)** Record the universal-wrapper clause resolution in D1 Consequences (§1) — the clause gates unvendored external OSS resources, not native calls on the ensemble's own egress lane.

---

## 7. Risk Register (aggregated, deduped, severity-ordered)

| # | Sev | Risk | Mitigation |
|---|-----|------|-----------|
| 1 | 🔴 | Governance: "nothing external bypasses tier 2" read literally would demand plugin-tier for native proxy/Postgres calls | AM-D1: record the instrumental-clause resolution in D1 Consequences |
| 2 | 🟡 | FE scanner built on text-node assumption is unimplementable (comments are Comment nodes) | AM11 (must land before P3 starts) |
| 3 | 🟡 | Tool mis-parses proxy error bodies (502-verbatim vs 504-envelope) → opaque error strings | AM1 + truth-table tests for both shapes |
| 4 | 🟡 | Timeout placement ambiguity → either hung tool or truncated fetches | AM6 explicit placement + hanging-transport tests for both phases |
| 5 | 🟡 | Generalized sweeper strips well-formed markers if pass order regresses | AM13: invariant comment + sweeper-last-on-residual test (already in P2 risks — keep mandatory) |
| 6 | 🟡 | Pre-P2 marker leak window (4 co-conditions) | Natural gate: keep `IMAGE_GEN_MODEL` unset until P2 lands (AM13 note) |
| 7 | 🟡 | Seal ceremony drift (2 pinned files) | Existing plan mitigations verified sound (diff audit + tripwire test) |
| 8 | 🟢 | `prompt_optimizer` silently True; `response_format:base64`; wrong content_type; wrong cap figure | AM3/AM7/AM8/AM10 |

## 8. Decisions Pending / Open Questions

- **None blocking for the developer.** All user-gated items are already plan-tracked: live-proxy model-row registration (Lane B), proxy `image_gen_timeout` raise (report-only), live promote (3-factor nonce ceremony).
- Informational (out of scope, report-only): the proxy repo's own synthetic fixtures are stale (array-shape `data` in `handler_test.go:82,357,845` vs live object shape) and `minimax.go:14-15` docstring is outdated — the tool must mirror the LIVE shape (as the plan does). Upstream hygiene note for the user, not this commission.

**Confidence: High.** The one assumption that would flip any recommendation: live proxy relay behavior differing from source (`handler.go:445-475`) — statically verified from source, so low risk; Lane A's real-upstream e2e will exercise it.
