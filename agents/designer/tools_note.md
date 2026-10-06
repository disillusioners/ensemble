# Tools

My operational reference for the tools I hold. The runtime enforces scope; this file states how I use each tool in practice.

---

## Filesystem — Read Broad, Write Scoped

- **Read:** any project file. C+ BROAD reads; I may annotate freely.
- **Write:** design files (`design-spec.md`, `design-review.md`, audit memos, mockups, templates), docs dir, design-system dir (tokens, audit captures). I do not write app source (components, templates, stylesheets, scripts).
- **Immutability:** `design-spec.md` is immutable once `status: approved`. A change is a new spec or an amendment file — never an in-place edit of an approved spec.

---

## Image Substrate (canonical)

The image substrate is my canonical image storage. Three tools I use:

- **`image_save(bytes|path, feature, page?, version?, source_agent=auto)`** — save with provenance. The store auto-stamps my id as `source_agent` when I do not supply one.
- **`image_list(feature=?, page=?, source_agent=?)`** — list by provenance. Filter combinations return exact subsets; torn entries are excluded.
- **`image_get(path|id)`** — fetch bytes + MIME. The sidecar is the MIME source of truth; blobs are extensionless so I never trust the extension.

### Provenance tags

Every save carries `{feature, page, version, source_agent}`. I always populate these when I save so my own audits and other agents can re-find the capture later.

### Protected retention class

Design baselines carry `retention_class: protected` — they survive the standard sweep until I explicitly release. I use protected for: spec thumbnails, conformance baselines, audit screenshots. For incidental captures (one-off re-checks) I leave the default so the standard sweep can clear them.

### Data-dir vs workdir caveat

The store lives in a daemon data directory — not the project workdir. My substrate tools are daemon-side and have data-dir access; they are **not** subject to `explain_image`'s project-workdir confinement. This is the reason the path→data-URI bridge exists for cross-process access.

---

## OpenDesign Plugin — OD Lane (default mockup source)

The OpenDesign plugin (`plugins/opendesign/`, tier-2 plugin subsystem first instance; slice ⑤ native tool family; upstream `open-design-mcp@0.16.1` retired at slice ⑦) is my **default** source for mockup artifacts. I bind four native `od.*` Port tools + the `opendesign.list_systems` plugin-skill (slice ④); the per-tool surface is:

- **`opendesign.list_systems`** — plugin-skill (slice ④). Used as the **lane-start probe** at the very start of the mockup lane (Phase 4 Step 0 in workflow) to determine whether the OD plugin is loaded and the BYOK lane is reachable. One skill lookup, no retry-storm. Probe result drives the lane decision and supplies the `fallback_reason` evidence when text is selected (skill not loaded → `tool-not-bound`; `od.generate` call error → `call-error`; BYOK env vars missing → `daemon-unavailable`).
- **`od.compose_brief`** — assemble the design brief from the spec sections in scope. Pure formatter (no network, no env vars). Pass the same `brief_answers` + `brand_spec` to every per-page generate call to enforce multi-page consistency.
- **`od.generate`** — produce **one self-contained HTML document per call, inline, at generation time**. Treat the returned HTML as the contract of record for that page. **Latency 130–170s** — one call, wait it out, no retry-storm. A `timeout` mid-call triggers text fallback for that page (record `fallback_reason: timeout`); other pages may stay on the OD lane. The Port's `finish_reason` + `usage` are visible to the caller; the inline completeness gates refuse to return a success on partial / empty / structurally-incomplete HTML.
- **`od.lint`** — quality gate against the AC and pages in scope. Returns `pass | fail-N`; a `fail` verdict must be fixed (re-call `od.generate` with a corrected brief) **before** freezing the spec — a `fail` never rides into the developer's brief.
- **`od.save`** — capture the generated HTML at the canonical mockup path (`.agents/shared/planning/{feature}/design/mockups/{page}.html`). This is the developer deliverable; the repo copy is daemon-independent and survives an OD outage after spec freeze.

**Operational boundary.** The `od.*` tools are bound only when the `opendesign` plugin is loaded (the `od.generate` Port tool is in my toolset, the `opendesign.list_systems` skill is loaded) AND the BYOK env vars (OPENAI_BASE_URL / OPENAI_API_KEY) are set. When the probe fails (binding gap surfaces at dispatch time, not after a hand-authored HTML), the text-native lane fires with a recorded `fallback_reason` — Cardinal #7 applies. I do not assume the tools are available; the probe confirms it before any spec authoring for mockups.

**Latency discipline.** `od.generate` is the long pole. One call, wait it out — do not retry-storm. The tool's internal timeout is exactly `max(120.0, max_tokens / 370.0)` seconds — 370 tok/s is the conservative divisor derived from the live lane's 130-170 s observation at 64K tokens (64000 / 370 ≈ 173 s); the 120 s floor guarantees we never timeout a successful call. The legacy 600s MCP-pool ceiling is no longer load-bearing (the native tool's request_timeout is the bound).

**Provenance vs deliverable.** The repo copy at `.agents/shared/planning/{feature}/design/mockups/{page}.html` (written at generation time) is the developer deliverable. Per §4.1: text mockups never claim pixel fidelity; OD mockups claim what `od.lint` supports. OD-UI provenance from the previous `od_save_artifact` / `od_save_project_file` MCP tools is dropped at slice ⑤ (REC §4.3 row ⑤); the OD-UI reference retires entirely at slice ⑦.

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

This is the path my own `image_save` tool takes; it carries the full provenance sidecar in one call.

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
   Response shape (frozen API contract — see `daemon/routers/tmp_images.py:212` docstring): `{ "uploads": [{ "image_id", "ref_url", "content_type", "size_bytes", "uploaded_at" }] }`. Note: the response does **not** echo provenance — the POST contract accepts `{filename, content_type, data_base64}` only (`extra="forbid"` on `TmpImageUpload`); provenance is written in the next step.
3. **Write the provenance sidecar** at `<data_dir>/tmp_images/<image_id>.json` (the sidecar's filename suffix is `.json`; see `daemon/services/tmp_image_store.py:79`). The data dir resolves `ENSEMBLE_DATA_DIR > DATA_DIR > ./data` (see `daemon/api.py:234-238`). For a throwaway dev boot the path is `./data/tmp_images/<id>.json`; for production it is whatever the install dir resolves to. Sidecar JSON shape (matching the in-tool writer at `daemon/tools/image_tools.py:798-808`):
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
   Older sidecars without `provenance` / `retention_class` parse cleanly (they become `None` / `"normal"` per `daemon/services/tmp_image_store.py:109-111`); the missing fields are forward-compatible.
4. **Verify the read-back.** `GET /api/tmp_images/<image_id>` returns 200 with the original bytes (the GET endpoint 404s if the sidecar is missing or torn — a successful 200 is the durable-form check). The header set (`Content-Type`, `Content-Length`, `ETag`, `X-Content-Type-Options: nosniff`, `Cache-Control: private, max-age=3600`, `Content-Disposition: inline; filename="<image_id>"`) all come from the sidecar. The ETag is the sha256[:16]; if I compute it client-side I can confirm byte-fidelity with `If-None-Match`. A direct `cat <data_dir>/tmp_images/<id>.json` confirms the four provenance keys are populated.

### Provenance tag policy

- `feature` — the work-stream key (e.g. `designer-agent`, `checkout`, `audit`). One per work-stream so a single `image_list(feature=...)` returns the whole stream's history.
- `page` — the route name (`/`, `settings`, `jobs`, `workspace/<id>`). Multiple captures per feature is normal — that's the audit trail.
- `version` — the spec SHA, branch name, or WP id (e.g. `p2-wp6`, `e67e5cd8`). I set this so a re-capture after a code change is distinguishable from a baseline.
- `source_agent` — the authoring agent id. I pass it explicitly when acting on behalf of another agent (or when a worker produced the capture on the designer's behalf); otherwise I let `image_save` auto-stamp.
- `retention_class` — `protected` for baselines that must survive the periodic sweep (spec thumbnails, conformance baselines, audit screenshots); `normal` for one-off re-checks the sweep can clear.

### Failure modes I expect

- **`agent-browser` install missing.** First-time ops runs need to install from the local checkout at `~/services/agent-browser/` (`cd ~/services/agent-browser && npm install`). The tool errors loudly on invocation; the fix is the local install, not a remote fetch. **Never** teach `npm install -g <pkg>` or bare `npx <pkg>` as a remedy — both fetch from the registry and execute, which is the 2026-10-03 incident pattern (npm `tsc` package is a community tombstone, not TypeScript). The safe idiom is the package-local binary: `cd <pkg-dir> && ./node_modules/.bin/<bin> ...`; outside any package dir, `npx --no-install` (loud refusal, never fetches code).
- **`--no-sandbox` needed on this VM.** Rootless containers cannot let Chrome drop privileges; the CLI flag is `--args "--no-sandbox"`. A capture that returns blank pixels with no error usually means the sandbox drop failed and Chrome refused to start.
- **POST `extra="forbid"` 422.** Sending an extra field in the POST body (e.g. `provenance: {...}`) gets a 422 — the contract is locked. The mechanical-path sidecar write step is the documented workaround.
- **GET 404 after POST.** The blob write is `O_CREAT|O_EXCL` atomic, but a torn sidecar (write interrupted mid-rename) makes the GET 404. Re-running the sidecar write with the same `<image_id>` does not retry — uuid4 ids are not reusable; the capture has to be re-POSTed and a new id assigned. The sweep will reap the orphan blob on its next tick.
- **Store full (HTTP 507).** The 1 GiB default cap (`tmp_image_store_max_bytes`) counts both blobs and sidecars. Switch `retention_class` to `protected` only for baselines that must survive; otherwise the sweep will clear the oldest `normal` entries first.

---

## Two-Channel Image Reality

There are two paths for an image to reach me. I treat them differently.

**Channel 1 — chat path (descriptions, not pixels).** When upstream includes a clipboard-style `tmpimg://` reference in a message, the chat-path normalization converts it to a **text description**; the pixels are cleared on the chat lane (pre-dispatch hook). If I need the pixels I cannot get them here — I see only the description.

**Channel 2 — substrate + direct base64 (real pixels).**

- *Substrate path:* upstream relays the path or ref inline. I read it via `image_get` (bytes + sidecar MIME) or via `explain_image(path)` for a text-out re-digest of a capture I saved into the project workdir (design drafts only).
- *Direct base64 dispatch:* a per-turn vision call passes `images=[data_uri]` in one message and the vision model sees the pixels. This is the compact lane when both surfaces are needed.

Default flow: upstream relays the conversion text inline + the substrate path; I re-digest via `image_get` or `explain_image`. Only when pixel intent is unambiguous and the description is missing do I reach for a direct base64 dispatch.

---

## Vision Input — `explain_image`

- Reads via a project-workdir-confined path. I use it for screenshots I save into the project workdir (design drafts); I do not use it as a substrate-path reader (the data-dir caveat above means the read would fail).
- Output is text — a description suitable for spec amendment language. Not a pixel diff.

---

## Sub-Team Dispatch

- **Workers only.** I spawn `worker` (recursion guard). I never spawn another `designer`.
- **`send_message(instance_id, message, load_skill?)` + end turn.** Hold no turn open.
- **Resume lane for paused workers is the job-queue continuation primitive, not `send_message`.** A `send_message` to a paused worker is rejected.
- **Error / FAILED revives consume a one-revive budget.** On a second failure I escalate to the leader with the original brief context for re-delegation.
- **`shared_meta_kv`** for in-flight state (`design.<task-id>.*`).
- **`midflight`** to surface progress + decision-required findings without pausing the turn.
