# Phase 1: Daemon Tool + Config + Registry + Allowlists + Prompt Notes

## Objective

Ship `generate_image` as a native-core agent tool in a NEW module
`daemon/tools/image_gen.py` under a NEW non-privileged tool category `"image-gen"`,
with purpose-bound env config that fails closed with a clean envelope, allowlisted to
exactly the 6 consumer agents, taught minimally in their `tools_note.md` files, with the
one pinned prompt file (`agents/developer[v2]/tools_note.md`) re-sealed per ceremony.

## Tool Design Contract (req#3 — binding, task-level)

**Name:** `generate_image`

**Args (all validated BEFORE any network call):**
| Arg | Type | Rules |
|-----|------|-------|
| `prompt` | str | REQUIRED; ≤1500 chars (upstream limit); reject empty/whitespace |
| `aspect_ratio` | str | optional; upstream default 1:1; MUTUALLY EXCLUSIVE with width/height |
| `width`/`height` | int | optional pair; each 512-2048, divisible by 8; MUST be given as a pair (upstream pair-only rule) |
| `n` | int | optional; capped **≤4** (D5); upstream allows 1-9 |
| `seed` | int | optional; int64 pass-through |
| `prompt_optimizer` | bool | optional; **default `False`** `[AM3]` |
| `response_format` | — | **NOT EXPOSED** `[AM7]` — upstream default `url` always used; `base64` never requested (the tool fetches bytes from OSS URLs itself) |
| `subject_reference` | — | OUT OF SCOPE v1 (I2I; noted for future) |

**Env resolution (OD purpose-bound resolver pattern —
`daemon/plugin_subsystem/opendesign/generate.py:697-742`; env-direct read, NEVER
`OPENAI_MODEL`):**
| Knob | Required | Default |
|------|----------|---------|
| `IMAGE_GEN_MODEL` | **YES** | none — unset ⇒ fail-closed `image_gen_not_configured`. Value = the proxy-registered CLIENT-FACING model id (e.g. `minimax-image-01`); the proxy rewrites it to its `InternalModel` upstream (`minimax.go:60-66`) `[AM4]` |
| `IMAGE_GEN_BASE_URL` | no | `OPENAI_BASE_URL` |
| `IMAGE_GEN_API_KEY` | no | `OPENAI_API_KEY` (existing key reused — never mint) |
| `IMAGE_GEN_TIMEOUT_S` | no | 240 (clamp to [30, 300]) |

Caps and limits live as MODULE CONSTANTS in `daemon/tools/image_gen.py` — explicitly NOT
in `daemon/constants.py` (pinned seal file; recon + [verified in worktree] seal list).

**Behavior sequence:**
1. Resolve env. If `IMAGE_GEN_MODEL` unset OR base URL/API key unresolvable → return
   fail-closed envelope `{"ok": false, "code": "image_gen_not_configured", "message": ...}`
   as an `"Error: ..."`-style string (image_tools convention) — NEVER raise, never a
   stack trace.
2. Validate args (table above). Validation failures → same envelope style, code
   `image_gen_invalid_args`.
3. POST `<base>/image_generation` (NOT `/v1/images/generations`) via `httpx.AsyncClient`
   with JSON `{model, prompt, ...sizing/n/seed/optimizer}`, headers:
   Bearer auth + identity headers `{x-proxy-app: ensemble, x-proxy-interleaved-thinking:
   True}` (house-parity with OD `generate.py:656-659`; harmless on this route — forwarded
   verbatim). Client timeout pinned `[review-fold 2]`: pass
   `httpx.Timeout(IMAGE_GEN_TIMEOUT_S, connect=10.0)` (house precedent
   `image_tools.py:199-204`); a truth-table case asserts the client timeout DERIVES
   FROM THE KNOB — httpx's default 5s read timeout would abort a live ~17s generation
   and misclassify it as a failure. `asyncio.wait_for(..., timeout=IMAGE_GEN_TIMEOUT_S)`
   — the tool OWNS its
   wall-clock cap (nothing kills ~150s native tool calls today; MCP 120s timeout is
   MCP-only). **Placement explicit `[AM6]`**: the 240s cap wraps POST + body-read +
   parse ONLY; each OSS byte fetch (step 5) sits OUTSIDE this cap with its own 30s
   `wait_for`. Worst-case total wall clock = 240 + 4×30 = **360s**; on the live lane
   the proxy 504 (default 120s) always fires first, so the tool never hangs.
4. Parse response defensively — error-body shapes are PROXY-specific `[AM1]`:
   - HTTP 502 (proxy relay of an upstream app error): the upstream body is preserved
     VERBATIM (`handler.go:445-475`) → parse `base_resp.status_code` / `status_msg`
     out of the 502 body.
   - HTTP 504 (proxy deadline): self-generated OpenAI envelope
     `{"error":{"type":"server_error","message":"upstream request failed: ..."}}`
     (`models/errors.go:43-53`) → parse `error.message`.
   - Other non-200 → error envelope with status.
   - Body: `data` is a SINGLE OBJECT `{image_urls: [...], image_base64?}` — NOT an array.
   - `metadata.success_count` / `failed_count` are STRINGS — coerce with `int()`.
   - Success ONLY `base_resp.status_code == 0`; fail closed on ANY non-zero
     `base_resp.status_code` — NO code whitelisting (1026 never exercised live)
     `[AM2]`. The historically observed app-error codes (2013/1002/1004/1008/1026/2049)
     are documentation of observed values only, never a gate.
   - Empty success `[AM5]`: `status_code == 0` ∧ `image_urls == []` → `ok: true`,
     `images: []`, with an explicit usage_note disclosure (upstream returned no images).
   - Partial success native: `failed_count > 0` AND `status_code == 0` → proceed with
     the URLs that exist; disclose count in usage_note.
5. Fetch EACH `image_urls[i]` IMMEDIATELY (Aliyun OSS v1-signed, ~24h TTL): per-image
   `wait_for` cap 30s, OUTSIDE the 240s POST cap (see step 3 `[AM6]`); size sanity
   ≤15MB per image — 4×15MB=60MB is safe against the TmpImageStore **1 GiB STORE cap**
   (`tmp_image_store.py:151`); the 32 MiB figure is the SERVE-side cap
   (`live_views.py:109`) `[AM10]`; content_type inference chain: OSS response
   Content-Type header → URL extension sniff → default `image/jpeg` (precedent
   `image_tools.py:374`) `[AM8]`; first failure skips that image with disclosure, does
   not abort the batch.
6. `image_save` each fetched blob to tmp_images (`daemon/tools/image_tools.py:671`):
   `content_type` from response, `feature="image-gen"`, `page=None`, `retention_class="normal"`.
7. Return JSON payload:
   ```json
   {
     "ok": true,
     "images": [{"image_id": "<32hex>", "content_type": "...", "size_bytes": N,
                 "sha256_hex": "...", "view_url": "/views/tmp-images/<32hex>"}],
     "markers": ["<!-- ens-img:image-gen:<32hex> -->"],
     "usage_note": "Paste each marker line VERBATIM (one per line, own line) where the image should appear in your reply. Do not wrap, strip, or reformat markers."
   }
   ```
   One marker line per successfully saved image, marker id == saved image_id.
8. Error convention throughout: every failure path returns an `"Error: ..."` string
   (never raises), matching `image_tools` conventions; envelope JSON is embedded
   SINGLE-LINE after `"Error: "` — multiline payloads trip `startswith("Error:")`
   consumers `[AM9]` — per the `_error_envelope` template (`generate.py:1467-1507`
   pattern).

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Create `daemon/tools/image_gen.py` with `generate_image` per the contract above, using `daemon/tools/chart_tools.py` as the STRUCTURAL TEMPLATE (module shape, decorator placement, module-constant block) `[AM12]` (module constants for caps: MAX_N=4, MAX_PROMPT_CHARS=1500, SIZE range/divisor, PER_IMAGE_FETCH_TIMEOUT_S=30, MAX_IMAGE_BYTES=15MB, DEFAULT_TIMEOUT_S=240) | none | Module imports clean; `@tool` + `@register_tool_category("image-gen")` decorate; no `daemon/constants.py` edit |
| 2 | Env resolver + fail-closed envelope (unset IMAGE_GEN_MODEL → `image_gen_not_configured`; unresolvable base/key → same class; timeout clamp) | 1 | Unit: unset-model call returns `"Error:"` string w/ envelope, zero network attempts (transport spy) |
| 3 | Wire registry: `CATEGORY_MODULES["image-gen"] = "daemon.tools.image_gen"` (`_tool_registry.py:594-595` area), `KNOWN_TOOL_NAMES += "generate_image"`; run frozen-names regen per `_tool_registry.py:702-713` snippet; update `tests/unit/tools/test_frozen_tool_name_discovery.py` fixture | 1 | `tests/unit/tools/test_frozen_tool_name_discovery.py` passes; AST discovery finds `generate_image` in category `image-gen` |
| 4 | Allowlist wiring: add `"image-gen"` token to `agents/{ari,leader,developer[v2],reviewer,tester,worker}/meta.json` `tools.allow` — edit shape: APPEND `"image-gen"` immediately ADJACENT to the existing `"image"` entry `[AM12]` (each already carries `"image"` — explicit-add model [verified in worktree]); JSON-parse check all 6 files post-edit | 3 | Registry resolution test: 6/6 agents granted; critic NOT granted (allow `['read_file','image','design']` unchanged); all 6 meta.json parse as valid JSON |
| 5 | Prompt-note additions to the same 6 agents' `tools_note.md` per `docs/agent-prompt-writing-guide.md` (canonical home; write as the agent), using the charter agent's chart-render marker teaching as the SECTION TEMPLATE — precedent: `agents/charter/soul.md:26` (byte-exact `<!-- ens-img:chart-render:<id> -->` marker discipline) + `agents/charter/workflow.md:96-108` (render+marker gated on RENDER_IMAGE directive) `[AM12]` `[review-fold 1: prior citation agents/charter/tools_note.md is DEAD — that file does not exist]`: when to use, paste marker lines VERBATIM on their own lines, never re-wrap/strip/reformat, cost note (~$0.0035/image, n≤4) | 4 | Each tools_note.md gains one focused section mirroring the charter marker-teaching shape; guide conventions honored (no version drift, one canonical home) |
| 6 | Seal re-pin for `agents/developer[v2]/tools_note.md` (the ONLY pinned file P1 touches): (a) git diff audit proving deltas are EXACTLY the commission edit; (b) regenerate `agents/.prompt-artifacts.sha256` via `scripts/protection/generate_prompt_artifact_baseline.py`; (c) refresh SHA in `SEALED_SHA_BASELINES` + append history entry in `tests/test_chart_image_delivery_e2e.py` (:1226-1252 pattern: date, commission, exact delta description, evidence pointer); (d) evidence pointer to P4 results file | 5 | `test_phase_d_does_not_modify_sealed_artifacts` green; baseline SHA matches new file bytes |
| 7 | Document all four `IMAGE_GEN_*` knobs in `.env.example` — one commented doc line per knob, no secret values: `IMAGE_GEN_MODEL` (required; proxy-registered client-facing id `[AM4]`), `IMAGE_GEN_BASE_URL`, `IMAGE_GEN_API_KEY`, `IMAGE_GEN_TIMEOUT_S` `[AM12]` | 2 | `.env.example` carries one doc line per knob; no live values committed |

## Files Touched (complete list)

- `daemon/tools/image_gen.py` — **NEW** (not pinned)
- `daemon/tools/_tool_registry.py` — CATEGORY_MODULES + KNOWN_TOOL_NAMES (not pinned
  [verified in worktree])
- `tests/unit/tools/test_frozen_tool_name_discovery.py` — fixture regen (not pinned)
- `agents/ari/meta.json`, `agents/leader/meta.json`, `agents/developer[v2]/meta.json`,
  `agents/reviewer/meta.json`, `agents/tester/meta.json`, `agents/worker/meta.json` —
  allow token (none pinned [verified in worktree]; only `agents/charter/meta.json` is)
- `agents/{ari,leader,reviewer,tester,worker}/tools_note.md` — prompt notes (none pinned)
- `agents/developer[v2]/tools_note.md` — prompt note **+ SEAL RE-PIN** ⚠️
- `tests/test_chart_image_delivery_e2e.py` — history entry + SHA refresh (the seal test
  itself; not self-pinned)
- `agents/.prompt-artifacts.sha256` — regenerated artifact
- `.env.example` doc lines for all four `IMAGE_GEN_*` knobs (Task 7 `[AM12]`) — docs
  only, no secret values in tree
- `tests/unit/tools/test_image_gen_tool.py` — **NEW** (P4 owns content; file appears in P1
  only if fail-closed unit lands early)

## Coupling

- **Tight with Phase 2** — marker grammar `'<!-- ens-img:image-gen:<32hex> -->'` is the
  contract P2's family gate and P3's FE regex consume; freeze the marker format in this
  phase (D2).
- **Tight with Phase 4** — P4 is the primary test consumer of everything built here.
- **Loose with Phase 3** — P3 consumes saved image ids via `/views/tmp-images/`, not the
  tool itself.
- **Independent of** adapter code (zero adapter edits — native upload already shipped).

## Risks (phase-specific)

- Category `"image"` reuse temptation → critic leak (deny is per-tool `image_save` only;
  critic allow carries `"image"` [verified in worktree]). Mitigation: new category +
  registry test (D3).
- `asyncio.wait_for` placement `[AM6]`: the 240s cap wraps POST + body-read + parse ONLY
  (a stalled body read must also trip the cap); OSS fetches sit OUTSIDE it with their own
  30s caps. Mitigation: single wait_for around an inner async function covering
  POST+read+parse; separate hanging-transport unit tests for the POST phase and the
  fetch phase.
- Allowlist edit typos break agent boot (meta.json is load-bearing). Mitigation: JSON
  parse check in the allowlist unit test for all 6 files.

## Definition of Done (per-phase DoD)

1. `tests/unit/tools/test_image_gen_tool.py` green: env fail-closed
   (`image_gen_not_configured`), arg validation (1500-char cap, aspect/width-height
   mutex + pair-only + 512-2048÷8, n≤4), wire-shape via `httpx.MockTransport` (canonical
   pattern `tests/unit/test_llm_failover_v2.py:326-478` — mock INSIDE the wrapper under
   test) asserting POST `<base>/image_generation`, request JSON, identity headers,
   single-attempt (no retry), response parsing truth-tables (single-object `data`,
   string counts, `base_resp.status_code==0` gate, ANY non-zero status_code → envelope
   (fail-closed, no whitelisting `[AM2]`), proxy 502 = upstream body verbatim → parse
   `base_resp` from the 502 body / proxy 504 = OpenAI envelope → parse `error.message`
   `[AM1]`, empty-success case `[AM5]`, partial
   success, OSS fetch + size guard, image_save provenance
   `feature="image-gen"`).
2. `tests/unit/tools/test_frozen_tool_name_discovery.py` green post-regen.
3. Registry/allowlist test green: category resolves; 6/6 consumer agents granted;
   critic NOT granted.
4. `test_phase_d_does_not_modify_sealed_artifacts` green (post re-pin).
5. `.prompt-artifacts.sha256` regenerated; artifacts consistent.
6. No edits to: `daemon/constants.py`, `daemon/sources/**` (P2's lane), `releases/**`.

## Seal-Trip Subsection

**Pinned files touched by P1: exactly ONE — `agents/developer[v2]/tools_note.md`**
(baseline `f8df8132...` [verified in worktree at
`tests/test_chart_image_delivery_e2e.py:1282`]).

Ceremony steps (in order):
1. Land the prompt-note edit (Task 5).
2. `git diff agents/developer[v2]/tools_note.md` audit: deltas must be EXACTLY the
   commission's generate_image section — nothing else. If any unrelated drift appears,
   STOP and investigate before re-pinning.
3. Regenerate `agents/.prompt-artifacts.sha256` via
   `scripts/protection/generate_prompt_artifact_baseline.py`.
4. Compute new sha256; refresh the dict entry in `SEALED_SHA_BASELINES`.
5. Append a history entry in the test file comment block (:1226-1252 pattern): date,
   commission (image-gen-tool), exact delta description, evidence pointer (P4 results
   file path).
6. Run the seal test — must be green.

## Exit Criterion

`generate_image` callable end-to-end in-process against a MockTransport (no live
network): fail-closed without env, correct wire shape with env, saved image ids
returned with markers; registry + allowlists + prompts landed; developer[v2] seal
re-pinned and green. P2 may start.

## Reviewer implementation-time notes `[review-notes]`

- (a) Add the `.env.example` `IMAGE_GEN_*` doc lines (Task 7) to the P1 DoD list as an
  explicit checked item.
- (b) Make the `KNOWN_TOOL_NAMES` regen sequence explicit: discover → merge → paste →
  frozen-test green.
- (c) The CHART_CAPABLE_AGENTS audit scans agent .md files; additive edits are verified
  safe.
- (d) Add an explicit malformed-HTTP-200-body row to the parse truth-table.
 allowlists + prompts landed; developer[v2] seal
re-pinned and green. P2 may start.
