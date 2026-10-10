# Tools

My operational reference for the tools I hold. I use each within the boundaries in my rules; this file is the per-tool detail.

---

## OD Pipeline Tools

- **`od.compose_brief`** — assemble the page brief from text inputs (`page_prompt`, `brief_answers`, `brand_spec`). Pure formatter: no network, no latency. I pass the same core inputs every per-page call so multi-page runs stay consistent; only the page-specific content varies.
- **`od.generate`** — the long pole. Produces one self-contained HTML document per call, inline, at generation time. **Latency 130–170s is normal** — one call, waited out. The returned envelope is my evidence: `finish_reason` (verbatim), `usage` (prompt/completion/total tokens), `truncated`, and on gate failure a typed `error.code`. The completeness gates (empty / finish_reason / structural closing markers) are applied before I ever see a "success" — a truncated document cannot surface as success.
- **`od.lint`** — quality gate against the page ACs. Returns `pass` or `fail-N`. I record the verdict verbatim; fixing a lint failure is a re-dispatch decision for my orchestrator, not an in-turn prompt rewrite.
- **`od.save`** — write-through to the canonical mockup path from the brief. This is the developer deliverable; I never relocate or rename the path. Save happens immediately after the gate check passes.

**Latency discipline.** `od.generate` is the long pole. One call, wait it out — do not retry-storm. The tool's internal timeout is exactly `max(120.0, max_tokens / 370.0)` seconds — 370 tok/s is the conservative divisor derived from the live lane's 130-170 s observation at 64K tokens (64000 / 370 ≈ 173 s); the 120 s floor guarantees we never timeout a successful call. The legacy 600s MCP-pool ceiling is no longer load-bearing (the native tool's request_timeout is the bound).

**Envelope discipline.** The `error.code` values are a closed vocabulary and I report them verbatim: `truncation_detected` and `missing_artifact_marker` are the truncation class (one bounded regenerate); `upstream_bad_request` and `context_length_exceeded` are the overflow class (zero retries — report back); everything else is report-as-is.

---

## Image Tools

Two channels bring a reference image to me:

- **Attached pixels.** A dispatch can carry images directly — I see them in-turn (I am vision-pinned). This is the primary lane for reference digestion.
- **Substrate refs.** A dispatch may name stored captures instead. `image_get(ref)` fetches bytes + sidecar MIME; `image_list(feature=..., page=...)` re-finds captures by provenance; `explain_image(path)` re-digests a workdir draft into text.

Digestion output is always a **structured textual description** folded into the brief text — descriptions, never payloads (the brief inputs are text-only).

When I `image_save` anything (rare — only when handed a capture to persist), I populate the provenance tags (`feature`, `page`, `version`, `source_agent`) so the audit trail stays re-findable.

---

## Capture Procedure (agent-browser → substrate)

A canonical recipe for landing an external page capture onto the substrate with full provenance so the comparator and downstream consumers can re-find it by path. Use this whenever a workflow needs a routed frontend page captured into a trackable, durable form.

### When this procedure applies

- A design or audit step needs a real page rendered by the routed frontend (a settings view, a workspace view, an experimental branch).
- The capture must be re-findable later by provenance filter (`image_list(feature=..., page=..., source_agent=...)`) — not by ad-hoc filename.
- The capture must survive past incidental re-screens (set `retention_class="protected"` for baselines; default `normal` for one-off re-checks).

It does **not** apply to:

- Inline screenshots a worker pastes into a chat message (use the chat path; the description is what survives).
- Vision spot-checks of pixels the user already pasted (use `explain_image` on a workdir draft).
- Synthetic HTML pages I author myself for a side test (those are mine to file anywhere — they have no routed provenance).

### Step-by-step (agent-turn path — PRIMARY)

This is the path my own `image_save` tool takes; it carries the full provenance sidecar in one call. I run this procedure as part of my write-through (the capture lands right after my save passes the gate check), so the comparator and downstream consumers re-find the capture by provenance.

1. **Confirm agent-browser is installed.** The CLI lives at `~/services/agent-browser/` with Chrome for Testing under `~/.agent-browser/browsers/`. On this VM the CLI also needs `--args "--no-sandbox"` (rootless container constraint). A missing install means install from a local checkout (e.g. `cd ~/services/agent-browser && npm install`) — **never** bare `npm install -g` or unconstrained `npx` (remote-fetch + execute is a security trap; package `tsc` from npm registry is not TypeScript). Outside a package dir, `npx --no-install` is the loud-refusal idiom if a one-off binary is genuinely needed.
2. **Pick a routed target.** A real page the frontend serves (e.g. `/`, `/jobs`, a settings-style view). Confirm the route returns 200 first — `curl -sI <origin><route>` is enough; the capture will fail silently if the page 404s.
3. **Capture to an explicit ops-lane path.** Invocation shape:
   ```
   agent-browser open <url>
   agent-browser set viewport 1280 800   # optional; default is fine
   agent-browser screenshot <ops-path>   # PNG at <ops-path>
   ```
   The screenshot lands as a real PNG (verified: PNG signature + magic bytes). `<ops-path>` lives in `/tmp/<wp>/captures/` or any directory I have write access to — it is **not** on the substrate yet.
4. **Read the file → base64-encode → ingest.** Three lines of tool calls:
   - Read `<ops-path>` as bytes.
   - Base64-encode the bytes (no `data:image/png;base64,` prefix — raw payload).
   - Call `image_save(content_b64=<b64>, content_type="image/png", feature=<feature>, page=<page>, version=<version>, source_agent=<agent-id>, retention_class="normal" | "protected")`.
5. **Provenance sidecar — always populate.** Every call sets `{feature, page, version, source_agent}`. `source_agent` auto-stamps my id if I omit it; I prefer to pass it explicitly so the audit trail is unambiguous when an agent is acting on behalf of another. `feature` is the work-stream key (e.g. `designer-agent`, `checkout`); `page` is the route name (e.g. `home`, `settings`, `jobs`); `version` is the spec SHA or WP id under which the capture was taken.
6. **Persist the returned id.** The tool returns a JSON record containing `image_id` and `ref_url`. The `ref_url` (`/api/tmp_images/<id>`) is the canonical wire form for any downstream consumer (comparator, mid-flight re-digest).
7. **Verify the read-back.** `image_get(image_id)` returns the bytes + MIME (Content-Type comes from the sidecar). `image_list(feature=..., page=...)` returns the matching subset — torn sidecars are excluded, so a successful filter hit confirms the sidecar is intact.

### Step-by-step (non-LLM ops path — mechanical equivalent)

For ops-lane or scratch runs where the LLM agent turn is not available (e.g. proxy down, or a worker is offline), the same shape is achievable with the raw HTTP route plus a direct sidecar write. **No daemon code required** — the sidecar is the durable form of provenance the store reads on every read/list call.

1. **Capture the page** with `agent-browser` exactly as above; the PNG is at `<ops-path>`.
2. **POST the bytes** to `POST /api/tmp_images`:
   ```
   curl -sS -X POST http://127.0.0.1:<port>/api/tmp_images \
     -H 'Content-Type: application/json' \
     -d '{"images":[{"filename":"<basename>.png","content_type":"image/png","data_base64":"<b64>"}]}'
   ```
   Response shape (frozen API contract): `{ "uploads": [{ "image_id", "ref_url", "content_type", "size_bytes", "uploaded_at" }] }`. Note: the response does **not** echo provenance — the POST contract accepts `{filename, content_type, data_base64}` only (`extra="forbid"` on `TmpImageUpload`); provenance is written in the next step.
3. **Write the provenance sidecar** at `<data_dir>/tmp_images/<image_id>.json` (the sidecar's filename suffix is `.json`). The data dir resolves `ENSEMBLE_DATA_DIR > DATA_DIR > ./data` . For a throwaway dev boot the path is `./data/tmp_images/<id>.json`; for production it is whatever the install dir resolves to. Sidecar JSON shape (matching the in-tool writer's shape):
   ```json
   {
     "image_id": "<32 hex>",
     "content_type": "image/png",
     "size_bytes": 12345,
     "uploaded_at": "<ISO-8601 with offset>",
     "sha256_hex": "<full hex>",
     "provenance": {
       "feature": "<feature>",
       "page": "<page>",
       "version": "<version>",
       "source_agent": "<source_agent>"
     },
     "retention_class": "normal"
   }
   ```
   Older sidecars without `provenance` / `retention_class` parse cleanly (they become `None` / `"normal"` — forward-compatible defaults); the missing fields are forward-compatible.
4. **Verify the read-back.** `GET /api/tmp_images/<image_id>` returns 200 with the original bytes (the GET endpoint 404s if the sidecar is missing or torn — a successful 200 is the durable-form check). The header set (`Content-Type`, `Content-Length`, `ETag`, `X-Content-Type-Options: nosniff`, `Cache-Control: private, max-age=3600`, `Content-Disposition: inline; filename="<image_id>"`) all come from the sidecar. The ETag is the sha256[:16]; if I compute it client-side I can confirm byte-fidelity with `If-None-Match`. A direct `cat <data_dir>/tmp_images/<id>.json` confirms the four provenance keys are populated.

### Provenance tag policy

- `feature` — the work-stream key (e.g. `designer-agent`, `checkout`, `audit`). One per work-stream so a single `image_list(feature=...)` returns the whole stream's history.
- `page` — the route name (`/`, `settings`, `jobs`, `workspace/<id>`). Multiple captures per feature is normal — that's the audit trail.
- `version` — the spec SHA, branch name, or WP id (e.g. `p2-wp6`, `e67e5cd8`). I set this so a re-capture after a code change is distinguishable from a baseline.
- `source_agent` — the authoring agent id. I pass it explicitly when acting on behalf of another agent (or when a dispatched worker produced the capture on my behalf); otherwise I let `image_save` auto-stamp.
- `retention_class` — `protected` for baselines that must survive the periodic sweep (spec thumbnails, conformance baselines, audit screenshots); `normal` for one-off re-checks the sweep can clear.

### Failure modes I expect

- **`agent-browser` install missing.** First-time ops runs need to install from the local checkout at `~/services/agent-browser/` (`cd ~/services/agent-browser && npm install`). The tool errors loudly on invocation; the fix is the local install, not a remote fetch. **Never** teach `npm install -g <pkg>` or bare `npx <pkg>` as a remedy — both fetch from the registry and execute, which is the 2026-10-03 incident pattern (npm `tsc` package is a community tombstone, not TypeScript). The safe idiom is the package-local binary: `cd <pkg-dir> && ./node_modules/.bin/<bin> ...`; outside any package dir, `npx --no-install` (loud refusal, never fetches code).
- **`--no-sandbox` needed on this VM.** Rootless containers cannot let Chrome drop privileges; the CLI flag is `--args "--no-sandbox"`. A capture that returns blank pixels with no error usually means the sandbox drop failed and Chrome refused to start.
- **POST `extra="forbid"` 422.** Sending an extra field in the POST body (e.g. `provenance: {...}`) gets a 422 — the contract is locked. The mechanical-path sidecar write step is the documented workaround.
- **GET 404 after POST.** The blob write is `O_CREAT|O_EXCL` atomic, but a torn sidecar (write interrupted mid-rename) makes the GET 404. Re-running the sidecar write with the same `<image_id>` does not retry — uuid4 ids are not reusable; the capture has to be re-POSTed and a new id assigned. The sweep will reap the orphan blob on its next tick.
- **Store full (HTTP 507).** The 1 GiB default cap (`tmp_image_store_max_bytes`) counts both blobs and sidecars. Switch `retention_class` to `protected` only for baselines that must survive; otherwise the sweep will clear the oldest `normal` entries first.

---

## dynamic-skill

My pipeline skill auto-loads at turn start; it carries the operational pipeline contract and points at the vendored design resources (design systems, prompt templates) by reference. I never hand-copy resource content into dispatches or reports — I cite the reference and read what I need.

When a step needs depth beyond the auto-loaded skill (an unfamiliar brand-system lookup, a gate-code question), `skill_search` finds candidates and `skill_view` reads one. I keep my skill versions consistent: the frontmatter version is the source of truth, and any manifest listing a skill must match it.
