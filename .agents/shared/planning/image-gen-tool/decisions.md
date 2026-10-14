# Decisions: Image-Gen Tool Commission

Date: 2026-10-14
Status vocabulary: `proposed` (awaiting architect/council enrichment) or `accepted`
(validated by evidence in-repo). All ADRs live in this file per house convention
(shape reference: `.agents/shared/planning/chart-image-delivery/decisions.md`).

---

## D1 — Native-core tool vs plugin (three-tier philosophy)

**Status:** CONFIRMED — native-core (architect-verified, high confidence; evidence:
architecture-recommendation.md §1 — weighted 5-axis comparison: native-core 5.00 vs
path-B 2.45 vs path-C 1.80; path-B registry refusal is STRUCTURAL —
`plugins-convention/path_types.yaml` requires `lifted_symbol` + `entrypoint` +
`ipc_version`, none of which llmproxy exposes)

**Context.** The commission requires a new agent capability: image generation via
llmproxy `POST /v1/image_generation` (MiniMax upstream). The system's ratified plugin
philosophy (project metadata `plugin_architecture_philosophy` + `plugin_path_model`)
organizes external integrations into three tiers: native core, universal plugin wrapper
(tier 2), and plugins (tier 3) — where plugins are for treating external OSS projects as
RESOURCE subsystems (vendored snapshots, 3-class provenance manifests: copy-freely /
snapshot-with-drift-alarm / own-outright, snapshot-pin + sync/drift machinery). The
caller recommended native-core; this ADR validates that with honest counter-arguments.

**Decision. NATIVE-CORE** — a thin capability tool module `daemon/tools/image_gen.py`
under a new non-privileged registry category `"image-gen"`, in the same family as
`ens_env_read` and the OD raw-SDK proxy call lane (tier-1 capability wrappers over
endpoints the ensemble already natively integrates).

**Justification:**
1. **llmproxy is not an external OSS subsystem — it is the ensemble's EXISTING LLM
   lane.** Same base URL (`OPENAI_BASE_URL`), same credential (`OPENAI_API_KEY`,
   live-verified PASSING auth on this route), same identity-header house convention.
   The plugin tier exists to vendor and snapshot EXTERNAL projects; there is nothing to
   vendor here.
2. **Zero vendored resources.** No prompts, design systems, templates, or data files
   originate upstream — the endpoint is a pure capability call. The 3-class manifest
   would contain one `own-outright` entry (our own tool code) and nothing else: pure
   ceremony, zero benefit.
3. **Execution-on-own-core holds by construction.** The plugin philosophy's execution
   model (resources consumed via skills, execution on our resilient core) is trivially
   satisfied: there are no upstream resources, and the tool runs on our core with our
   failure conventions (fail-closed envelopes, never-raise, wall-clock caps).
4. **Precedent parity.** The chart-render pipeline — the closest sibling capability —
   is native-core (`daemon/tools/chart_tools`, category `"chart"`). Symmetry argues
   native.

**Counter-arguments (honest):**
- *Provider plurality:* if image-gen later grows multiple upstream providers (OpenAI
  images, local SD, etc.) with divergent request/response shapes, an adapter layer
  becomes real work. Counter: that is a tier-1 abstraction problem (provider strategy
  inside the tool), not a vendoring problem — a plugin manifest still buys nothing.
- *Resource-dominant drift:* if the capability grows upstream prompt packs or style
  systems worth snapshotting, the resource-izing path (C) becomes attractive.
- *Contract churn:* upstream request/response contract lives in the proxy; today it is
  probe-verified and stable. Snapshot-with-drift-alarm would only help if the upstream
  were an external repo we track — it is not.

**Flip conditions (what WOULD change this to plugin):**
1. ≥2 additional image providers with materially different contracts AND vendored
   per-provider prompt/config resources worth snapshotting.
2. Upstream publishes prompt/design resources we consume at the resource level.
3. The proxy endpoint moves OUT of the ensemble's own LLM lane into a third-party OSS
   service we track by ref.

**Consequences.** No manifest, no pin, no drift machinery. The tool inherits daemon
conventions directly (registry, allowlists, error envelopes, test packs). Registry
category isolation (D3) becomes the security boundary — which is why D3 is separate
and load-bearing.

**Universal-wrapper clause resolution `[AM-D1]` (🔴 governance, binding for this
project):** the `plugin_path_model` clause "nothing external bypasses the wrapper
layer", read literally, would demand plugin-tier treatment for Postgres and every
native proxy call. **Resolution:** the clause is INSTRUMENTAL — its job is to gate
UNVENDORED EXTERNAL OSS RESOURCES (the OD case: tag-pinned tracked-by-ref upstream +
vendored resource files + a real lifted symbol with an IPC contract), NOT pre-existing
native capability calls on the ensemble's own egress lane. llmproxy entered via tier-1
native integration (`OPENAI_BASE_URL`/`OPENAI_API_KEY` — chat pool, embeddings, OD
vision lane) before tier 2 was ratified; the Postgres analogy holds
(`daemon/repositories/` is native-core over external OSS). This resolution is recorded
here so the clause cannot be weaponized later against native-core integrations
(evidence: architecture-recommendation.md §1 "Universal-wrapper clause resolution").

---

## D2 — Marker family regex + feature token grammar

**Status:** confirmed (byte-compat constraint accepted; architect §3 Option B + council 2026-10-14)

**Context.** `daemon/sources/dispatcher.py` extracts chart-render markers via a LOCKED,
byte-stable regex (`:29-32`, R5-gated: "DO NOT modify without an R5 architectural
review") plus a strip-only near-miss sweeper (`:50-53`). Image-gen needs the same
machinery for a second feature token.

**Decision.**
- Grammar: `ens-img:([a-z0-9-]+):([a-f0-9]{32})` — feature token is lowercase-kebab
  (`[a-z0-9-]+`), image id is 32-hex, full-line anchored with the exact
  `<!-- ... -->` comment envelope of the locked form.
- The LOCKED chart-render regex stays byte-identical (no relaxation); a NEW family
  regex runs as a second pass over lines the locked pass did not claim; the near-miss
  sweeper generalizes to the family (strip-only, never extracts, never matches
  well-formed markers).
- Pass order is invariant: locked → family → sweeper (extends the in-file F12
  invariant).
- Provenance delivery gate: explicit `ALLOWED_FEATURES = frozenset({"chart-render",
  "image-gen"})` — unknown future features are stripped-but-never-delivered
  (fail-closed).
- FE-side regex byte-matches the same grammar (P3 constants module).

**Consequences.** Chart-render behavior is provably unchanged (additive pass);
existing chart tests must pass UNTOUCHED — this is a DoD gate, not an aspiration.
Future features (e.g. chart-render variants) extend `ALLOWED_FEATURES` + the token
grammar without regex surgery.

**Load-bearing pass order `[AM13]`:** the generalized near-miss sweeper TEXTUALLY
MATCHES well-formed family markers (32-hex ⊂ `[^>\n]{0,64}`) — the safety property
lives ONLY in the enforced pass order locked → family → sweeper-on-unclaimed-lines.
The in-file invariant comment and the sweeper-last-on-residual test are the
correctness proof, not optional documentation; a pass-order regression silently
strips-and-loses well-formed markers.

**Pre-P2 leak window (bounded, accepted) `[AM13]`:** between P1 and P2 a well-formed
image-gen marker could leak as literal comment text into chat, but ONLY if {env
configured, agent invokes the tool, chat routing active, pre-P2 window} co-occur. The
natural gate is D4's fail-closed default — `IMAGE_GEN_MODEL` unset ⇒ no markers can
exist. No additional gating needed (D10 keeps e2e configuration post-P2).

---

## D3 — New category `"image-gen"` vs reusing `"image"`

**Status:** accepted (evidence verified in worktree)

**Context.** `daemon/tools/image_tools.py` already provides the category `"image"`
(`CATEGORY_MODULES["image"] = "daemon.tools.image_tools"`, `_tool_registry.py:595`).
Placing `generate_image` in the same category looks economical.

**Decision. NEW category `"image-gen"` — reuse is REJECTED.**

**Rationale (the critic leak):** `agents/critic/meta.json` grants
`allow = ['read_file', 'image', 'design']` and denies per-TOOL (`image_save` explicitly
listed) — [verified in worktree]. Category grants are wholesale: if `generate_image`
lived in category `"image"`, critic would receive a WRITE-capable, spend-capable
generation tool. The critic's design contract is read-only (resolved set fixed to
read-shaped tools; write-leak invariant). Per-tool deny patching (adding
`generate_image` to critic's deny) is whack-a-mole: every future image-category write
tool re-opens the leak.

**Consequences.** Allowlist wiring adds one token to exactly the 6 consumer agents
(explicit-add model confirmed — all 6 carry `"image"` today). A registry test pins
critic-NOT-granted. Frozen-names drift test updates (regen snippet
`_tool_registry.py:702-713`).

---

## D4 — Env knob set + defaults (purpose-bound config)

**Status:** confirmed (council + approver, 2026-10-14)

**Context.** Config must be purpose-bound and fail-closed. House precedent: OD
purpose-bound resolver (`daemon/plugin_subsystem/opendesign/generate.py:697-742`) reads
env directly and NEVER the shared `OPENAI_MODEL`; fail-closed envelope template
`_error_envelope` (`:1467-1507`).

**Decision.**
| Knob | Required | Default | Notes |
|------|----------|---------|-------|
| `IMAGE_GEN_MODEL` | YES | — | unset ⇒ fail-closed envelope `image_gen_not_configured`; NEVER falls back to `OPENAI_MODEL` |
| `IMAGE_GEN_BASE_URL` | no | `OPENAI_BASE_URL` | same proxy, same lane |
| `IMAGE_GEN_API_KEY` | no | `OPENAI_API_KEY` | reuse, never mint (commission prohibition) |
| `IMAGE_GEN_TIMEOUT_S` | no | 240 | clamp [30, 300]; owns the tool's wall-clock via `asyncio.wait_for` |

Identity headers `{x-proxy-app: ensemble, x-proxy-interleaved-thinking: True}` ride
every call (house-parity with `generate.py:649-658`; forwarded verbatim by the proxy —
harmless on this route, kept for parity).

**Consequences.** Zero-config deployments keep working once `IMAGE_GEN_MODEL` is set;
before that, agents get a clean `"Error: ... image_gen_not_configured"` envelope —
never a stack trace. No knob lives in `daemon/constants.py` (pinned seal file; caps
live in the tool module as module constants).

---

## D5 — `n` cap value

**Status:** confirmed (council + approver, 2026-10-14)

**Context.** Upstream allows n=1-9. Each image costs ~$0.0035 and ~17s (n=1 live
probe). Payload, spend, and latency all scale with n; the e2e budget is ≤5 images
TOTAL.

**Decision. Cap `n` at 4** (validate + clamp-or-reject: reject with
`image_gen_invalid_args`). Rationale: bounds a single call to ≈$0.014 and ~70s worst
case — safely under both the tool wall-clock cap (240s) and any sane per-turn spend;
still ample for agent use cases (illustrations, variants). n>4 is a deliberate future
knob change, not an agent-side decision.

**Consequences.** Unit truth-table pins n=4 rejection. e2e budget (5 images) can never
be exhausted by a single call.

---

## D6 — E2E lane A/B gating + budget

**Status:** accepted (dual-lane structure forced by the user-gated dependency)

**Context.** The live proxy requires a user-registered image-gen model row (OAuth'd FE
UI) before it will serve this route. The local dev proxy (`localhost:4123`, own sqlite
store, minimax key staged in proxy repo `.env-minimax`) can be seeded for a
real-upstream e2e without touching the live proxy.

**Decision.**
- **Lane A (unblocked):** dev-proxy real-upstream e2e — seed local store, reuse the
  staged `.env-minimax` key (NEVER mint), dev.sh boot (port 8079, fenced env),
  MockSourceAdapter captures; ≥1 live real image delivered natively.
- **Lane B (user-gated):** live-proxy e2e — probe first; run only if the user has
  registered the model row; otherwise SKIP-WITH-DISCLOSURE (first-class exit, recorded).
- **Budget:** ≤5 images TOTAL across ALL lanes (A + B + any probe), ~$0.05 cap
  ($0.0035/image), enforced by an in-script counter — the lane script hard-stops at 5;
  the counter assertion is itself part of DoD.
- Slack `files:write` workspace grant verified at smoke time; ungranted ⇒ disclosed
  text-only degradation (graceful path exists, `adapters/slack/adapter.py:638-676`).

**Consequences.** The commission never blocks on the user's registration; the live-lane
readiness report is delivered either way. Spend is bounded by construction.

---

## D7 — Wall-clock ownership: tool-body `asyncio.wait_for`

**Status:** accepted (evidence-based)

**Context.** Nothing today kills a ~150s native tool call (ToolNode runs bare under the
long-tool-nudge; the MCP 120s timeout is MCP-only). The proxy has its own deadline
(live: `image_gen_timeout` default 120s → 504; dev lane: locally raisable).

**Decision.** The tool OWNS its wall-clock cap: one `asyncio.wait_for` wrapping the
ENTIRE network phase (POST + body read + parse) at `IMAGE_GEN_TIMEOUT_S` (default 240).
Rationale: 240s > live proxy 120s deadline, so on timeout the PROXY fails first with a
clean 504, which the tool converts to an error envelope — a hung tool (worst outcome)
is structurally excluded. Timeout errors → `"Error: ..."` envelope, never raise.

**Consequences.** No daemon/constants knob; no reliance on outer machinery. Live-lane
120s ceiling documented in risks (raising the proxy knob is user-gated, report-only).

---

## D8 — Failure convention: envelopes, never raises

**Status:** accepted (house convention)

**Context.** `image_tools` failures are `"Error: ..."` strings; the OD lane uses
`{ok:false, code, message}` envelopes. This tool faces both agents (need clean strings)
and the dispatcher (needs predictable control flow).

**Decision.** Every failure path returns an `"Error: ..."`-style string carrying the
envelope (code ∈ `image_gen_not_configured`, `image_gen_invalid_args`,
`image_gen_upstream_error`, `image_gen_timeout`, `image_gen_fetch_failed`); the tool
NEVER raises. Partial success (upstream delivered fewer than n) is NOT an error:
`ok:true` with the delivered subset + disclosure in `usage_note`.

**Consequences.** Downstream consumers (loop breaker, report repair, agents) see the
same failure shape as every other image tool; no new failure taxonomy.

---

## D9 — Provenance + retention for generated images

**Status:** confirmed (council + approver, 2026-10-14)

**Context.** `image_save` supports `provenance` (feature/page/version) and
`retention_class` (normal/protected) (`daemon/tools/image_tools.py:671-836` [verified
in worktree]).

**Decision.** Every generated image saves with `feature="image-gen"`, `retention_class
="normal"` (30-day sweep eligible), provenance `source_agent` auto-stamped by the
substrate. `page`/`version` unused in v1. The dispatcher's delete-after-delivery
semantics still remove the blob after successful native chat delivery — retention
covers the un-delivered residue (web-only consumption path).

**Consequences.** `image_list(feature="image-gen")` gives observability; the tmp-image
cap counts generated images like all others; no new retention class needed.

---

## D10 — Phase scheduling: P2 ∥ P3 (spec-first) after P1

**Status:** accepted (architect §5 confirmation of the plan's phase structure)

**Context.** P3's FE constants module + specs consume the marker grammar frozen in D2;
P2's daemon family pass consumes the same grammar. A strict P2→P3 serial order would
idle the frontend lane for no correctness reason.

**Decision.** P2 and P3 may run IN PARALLEL after P1 (spec-first): P3 authors the
constants module + specs against D2's frozen grammar while P2 lands the daemon family
pass. Byte-mirror discipline holds because the contract is decided upfront (D2).
**Final integration commit must be JOINT — any grammar change after spec authoring is
a P2+P3 same-commit change.** P1 → P2 ordering stays coherent: markers are inert (and
cannot exist) until `IMAGE_GEN_MODEL` is configured, which bounds the pre-P2 leak
window (D2 `[AM13]`). P4 remains the fan-in for all lanes.

**Consequences.** Shorter wall-clock; grammar single-sourced in D2; joint-commit
discipline is the guard against FE/daemon grammar drift.

---

## Decision Index

| ID | Decision | Status |
|----|----------|--------|
| D1 | Native-core tool module (not plugin) | CONFIRMED (architect §1) + `[AM-D1]` clause resolution |
| D2 | Marker family grammar + 3-pass order + fail-closed feature gate | CONFIRMED (architect §3 + council 2026-10-14) + `[AM13]` pass-order/leak-window docs |
| D3 | New category `"image-gen"` (critic-leak isolation) | accepted |
| D4 | Purpose-bound env knobs + fail-closed envelope | CONFIRMED (council + approver 2026-10-14) |
| D5 | `n` cap = 4 | CONFIRMED (council + approver 2026-10-14) |
| D6 | Dual-lane e2e (A unblocked / B user-gated) + ≤5-image budget | accepted |
| D7 | Tool-owned wall-clock via `asyncio.wait_for` (240s) | accepted |
| D8 | Envelope failure convention, never raises | accepted |
| D9 | Provenance feature="image-gen", retention normal | CONFIRMED (council + approver 2026-10-14) |
| D10 | Scheduling: P2 ∥ P3 spec-first after P1; joint grammar commits | accepted (architect §5) |
