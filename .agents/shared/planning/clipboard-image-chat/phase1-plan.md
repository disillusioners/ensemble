# Phase 1: Tmp-image store + upload + serving endpoints

> **Backend — Phase 1 of 6** for `feature/clipboard-image-chat`.
> Phase owner: `plan-worker-backend` (this file).
> Branch baseline: `feature/clipboard-image-chat` @ `307db932` (planning-only).
> Cross-references: phase 2 (conversion + persistence), phase 3 (retention),
> phase 4 (FE upload UI), phase 5 (FE display), phase 6 (FE viewer + grace).

## Amendment log (architect rulings 2026-09-19)

Applied in this pass (per `.agents/shared/planning/clipboard-image-chat/architecture-recommendation.md` §8):

- **#1** — GET response headers: added `X-Content-Type-Options: nosniff` + `Content-Disposition: inline; filename="<image_id>"` (precedent: `daemon/routers/vscode_proxy.py:296-298`). Most consequential XSS lever.
- **#2** — Allowlist trimmed: dropped `bmp`/`tiff`; final allowlist = `png/jpg/jpeg/gif/webp`; explicit rejection message names the rejected type (forensics).
- **#3** — Store byte cap: added `tmp_image_store_max_bytes` default 1 GiB (`SERVICES_TMP_IMAGE_STORE_MAX_BYTES`); walkdir sum pre-write; 507 + rate-limited WARNING on trip.
- **#4** — Debug listing gated: `ENSEMBLE_TMP_IMAGE_DEBUG_LISTING` default OFF → 404 when disabled (route does not advertise itself).
- **#5** — Route docstring: PUBLIC-BY-OBSCURITY guard-rail block (verbatim from §6 Q4).
- **#6** — ADDED `DELETE /api/tmp_images/{image_id}` → 204 idempotent (per §2 required addition).

### Round 2 (architect post-review rulings 2026-09-19)

Applied in this pass (per `architecture-recommendation.md` §"Post-review rulings"):

- **#36 — Probe checklist: image-reader `quick` resolution.** Watcher resolver `graph.py:8551-8599` maps `"quick"` → `config.llm.model_keywords` with `""` fallback, but image-reader resolves at spawn via a separate `_resolve_model_override` path whose keyword handling is UNVERIFIED. The phase-1 vision probe (Task 1 below) MUST also verify the image-reader spawn log shows a **RESOLVED model name** (e.g. `gpt-4o-mini`), NOT the literal `quick`. Verbatim `quick` = FAIL probe + escalate R2-style (same posture as model_vision probe). This GATES phase-2 dispatch.
- **O5 — Anchor nits verified.** messages.py:454 = `str(uuid.uuid4())` (echo_id mint) ✓; `_BASE64_IMAGE_PATTERN` at `message.py:8` ✓; `_INJECTION_TTL_SECONDS` at `manager.py:2732` ✓; SPA catch-all `daemon/api.py:2612` returns `404 JSON` for `/api`/`/ws`/`/vscode` prefixes, `index.html` for non-API paths ✓. No corrections required — all prior citations are accurate.
- **O6 — DELETE∥sweep race test annotated PHASE-3-DEFERRED.** Phase-1 Task 4b acceptance criterion for the DELETE endpoint references a race test against the phase-3 sweep service; that test cannot land until phase 3 ships the `TmpImageCleanupService`. Annotated below.

### Polish pass (cycle-2 review APPROVED-WITH-NOTES; rulings final; no re-review)

- (1) Task 1/4/7 — `ref` → `ref_url` rename applied to `TmpImageUploadResponse` model field, POST response shape, and contract docstring (3 mentions at :162, :166, :170). Aligns with the ratified §2 contract; FE phase-4 builds against `ref_url`.
- (6) Probe escalation tree reworded "both" → "all four" checks (post-#36 the probe has 4 checks: `OPENAI_MODEL_VISION` set, log says vision-configured, image-reader latency <60s, `quick` resolves to a real model name).

---

## Objective

Land a backend-owned **transient image lifecycle** that lets the FE upload
clipboard / picker / drag-drop images as JSON base64, obtain a stable
**content-addressed image ref** (e.g. `tmpimg://<id>` or HTTP `/api/tmp_images/<id>`),
and re-fetch the bytes for display OR pre-turn conversion. The store lives
under the existing `data_dir`, follows the `data/logs/` mkdir convention,
and registers its serving endpoint **before** the SPA catch-all
(`daemon/api.py:2612`) so image requests never fall through to `index.html`.

A retention service (phase 3) reaps the store; phase 2 performs the
**image→text** conversion before the chat agent sees the message. Phase 1
itself does **not** convert — it only persists + serves.

---

## Scope

### In scope

- New on-disk store: `data/tmp_images/{id}` where `{id}` is server-minted.
- One HTTP endpoint per direction:
  - **POST** `/api/tmp_images` — accepts JSON `{filename, content_type, data_base64}`
    (NO multipart; matches existing `daemon/models/message.py:11-72` JSON style).
    Caps: ≤3 images per request (validated at the model layer),
    ≤10MB per image (base64-decoded size), allowlist MIME
    (`image/png|jpeg|jpg|gif|webp` only — `bmp`/`tiff` **dropped** per
    architect §3 amendment; explicit rejection message names the
    rejected type for forensics: `"content_type '<t>' rejected —
    only png/jpeg/gif/webp allowed (svg excluded: stored-XSS via
    direct navigation)"`).
  - **GET** `/api/tmp_images/{id}` — serves raw bytes with the original
    `Content-Type`. Registered **before** the SPA catch-all so the
    path cannot resolve to `index.html`.
- Stable ref string format — provisional contract
  (`tmpimg://<id>` form; also usable as `<id>` in HTTP ref strings).
  The exact format is **PROVISIONAL — pending architect sign-off** in
  `decisions.md`. The FE phase-4 worker builds against this contract;
  any refinement is additive (shape-stable envelope).
- Server-side id generation: `uuid4().hex` (matches the project's
  uuid4 convention seen at `daemon/routers/messages.py:454`).
- Filesystem layout & permissions: created under the manager's
  `data_dir` (`daemon/manager.py:2321-2330` —
  `return self.db_path.parent`) resolved at the lifespan seam
  (`daemon/api.py:232-244`:
  `ENSEMBLE_DATA_DIR > DATA_DIR > ./data`).
  The same precedence chain is reused; do NOT introduce a new env var.
- Self-test helpers (deterministic refs) so the tester's web-automation
  e2e (later phase) can `POST` a known 1×1 PNG and assert the round-trip
  GET returns byte-identical content.

### Out of scope (flagged for follow-up)

- **Conversion to text** — owned by phase 2.
- **Retention sweep** — owned by phase 3 (this phase writes no cleanup).
- **Display merge into chat transcript** — owned by phase 5 (FE).
- **Image viewer dialog (MatDialog)** — owned by phase 6 (FE).
- **Graceful onerror placeholder for cleaned-up files** — owned by
  phase 6 (FE).
- **Discord / Telegram / Slack source-side adoption** — the source-side
  ingestion continues to use `images=image_urls` (HTTPS URLs) on the
  `IncomingMessage` shape (`daemon/sources/adapters/discord/adapter.py:1049-1105`).
  This phase does NOT touch source adapters. Coexistence is phase 2's
  concern.
- **The 202-injection images-drop defect** (verified at
  `daemon/routers/messages.py:458` — `manager.set_injection` call has no
  `images` kwarg) — this is a pre-existing defect that phase 1 does
  not need to fix. The new flow's agent-facing payload is text (phase 2),
  so the defect only manifests if the FE uses the data-URI form on
  a RUNNING target — which the design direction routes through the new
  ref-based flow. Marked as a **flagged follow-up** in phase 2.

### Cross-phase note (architect amendment #14 — vision probe promotion + amendment #36 — image-reader `quick` resolution)

The **vision probe** (originally a phase-2 Task 0 verification of
`model_vision` config status) is **PROMOTED into the phase-1 window**
(architect §6 h6 ruling). Rationale: the probe is manual + minutes-cheap,
and its failure blocks the {phase-2, phase-3, phase-4} wave's
centerpiece — running it at phase-1 contract-freeze time means the
escalation decision lands **before dispatching phase 2, not after**
the integration gate. The probe checks:

1. `OPENAI_MODEL_VISION` env var is set in the target deployment
   (`.env` / `.env.prod` per `daemon/config.py:157`).
2. The latest prod log carries `[Graph] Vision model configured: <name>`
   (success) — NOT `[Graph] No vision model configured` (fallback).
3. A throwaway image-reader invocation completes in <60s on this
   deployment (proxies the 90s/image budget — if it's >60s, revisit
   the 90s ruling before phase-2 tests freeze per architect §12).

**🔴 Round-2 amendment #36 — image-reader `quick` resolution check (ADD one line):**

4. The image-reader spawn log shows a **RESOLVED model name** (e.g.
   `gpt-4o-mini`, `gpt-4-vision`), **NOT** the literal `"quick"`.
   Image-reader's `agents/image-reader/meta.json` declares
   `llm_model: "quick"`. The watcher resolver
   (`daemon/graph.py:8551-8599`) maps `quick` → `config.llm.model_keywords`
   (operator-pinned via `OPENAI_MODEL_KEYWORDS`) with `""` fallback;
   **but image-reader resolves at spawn via a separate
   `_resolve_model_override` path whose keyword handling is
   UNVERIFIED**. If the spawn log shows the literal `quick`, the probe
   **FAILS** and escalates R2-style — same posture as the model_vision
   check. This GATES phase-2 dispatch.

**Probe escalation tree (all four checks):** pass → proceed to {2,3,4} wave dispatch. FAIL → halt the wave, escalate to the architect with a one-line summary naming the failed check. Do NOT dispatch phase 2 on partial success.

Phase 2 retains Task 0 as the formal hook (conversion-time check) but
**does not own the probe itself** — phase 1's merge gate cannot ship
without it. Recorded as a hard pre-flight on the phase-overview's
activation checklist.

---

## Components (file:line anchors — verified)

| # | Component | Anchor | Notes |
|---|---|---|---|
| 1 | Data-dir resolution (read by lifespan + manager) | `daemon/api.py:232-244` | `ENSEMBLE_DATA_DIR > DATA_DIR > ./data`; stashed at `app.state.data_dir:244`. **Reuse — do NOT redefine.** |
| 2 | Manager-side data_dir anchor | `daemon/manager.py:2321-2330` | `data_dir` is `self.db_path.parent`; `opencode_db_path = self.data_dir / "opencode_sessions.db"` (`daemon/manager.py:1125`) is the canonical "place a sibling file under data_dir" pattern. |
| 3 | Existing data/logs mkdir convention | `daemon/api.py:59` (`_LOG_DIR = os.environ.get("DAEMON_LOG_DIR", "./data/logs")`) | The new `data/tmp_images/` directory follows the same convention (relative to resolved `data_dir`; `Path.mkdir(parents=True, exist_ok=True)` at lifespan startup). |
| 4 | SPA catch-all (image serving MUST register before this) | `daemon/api.py:2612` | `@app.get("/{path:path}")` at line 2612 with `FRONTEND_DIST = BASE_DIR / "frontend" / "dist" / "frontend" / "browser"` (`daemon/api.py:175`). The new `@app.get("/api/tmp_images/{image_id}")` MUST be registered before this in `create_app` body, otherwise path-matching order is undefined for `/api/tmp_images/abc`. (Starlette registers routes first-match-wins.) |
| 5 | Base64-image pattern allowlist | `daemon/models/message.py:8` (`_BASE64_IMAGE_PATTERN`) | Reuse the regex constant for the new upload model. |
| 6 | Per-image size cap (10MB) + count cap (≤3) | `daemon/models/message.py:29-61` (`validate_images`) | Reuse the same limits for the new endpoint. The new Pydantic model (`TmpImageUpload`) is structurally identical. |
| 7 | Error response envelope | `daemon/routers/messages.py:215-220` (`ErrorResponse(code=ErrorCodes.INVALID_REQUEST, ...)`) | Mirror the envelope for validation errors. |
| 8 | Stable id mint convention (uuid4 hex) | `daemon/routers/messages.py:454` (`echo_id = str(uuid.uuid4())`) | Reuse for `image_id`. |

---

## Tasks (ordered, with acceptance criteria)

| # | Task | Depends On | Acceptance |
|---|---|---|---|
| 1 | Add `daemon/models/tmp_image.py` with `TmpImageUpload` (`filename`, `content_type`, `data_base64`) and `TmpImageUploadResponse` (`image_id`, `ref_url`, `content_type`, `size_bytes`, `uploaded_at`). Validators: base64 well-formed; MIME in allowlist; ≤3 images per request; ≤10MB per image; content_type ↔ base64 magic-byte sniff cross-check (defense in depth). | none | Unit test: happy-path 1×1 PNG; rejection on 11MB blob (ValueError); rejection on unknown MIME; rejection on non-base64 data. |
| 2 | Add `daemon/services/tmp_image_store.py` (storage facade): `init(data_dir)` creates `data_dir / "tmp_images"` with `mkdir(parents=True, exist_ok=True)` at lifespan startup (idempotent, mirrors `data/logs/`); `save(image_id, content_bytes, content_type)` writes `<image_id>` with extension from MIME (or no extension if unknown — `.bin` fallback); `open(image_id) -> (bytes, content_type)` reads back; `delete(image_id)` removes the file. **NO expiration logic in this phase** — phase 3 owns that. | Task 1 | Unit test with `tmp_path` fixture: round-trip bytes; delete removes file; unknown id raises `TmpImageNotFound`. |
| 2b | **🔴 Store byte cap (architect §3 amendment #3)** — add `tmp_image_store_max_bytes: int = Field(default=1024**3, ge=1024**2)` to `ServicesConfig` (sibling to `job_lock_sweep_interval_seconds` at `daemon/config.py:1550-1568`); env override `SERVICES_TMP_IMAGE_STORE_MAX_BYTES`. Constructor reads the value; `save()` does `sum(entry.stat().st_size for entry in os.scandir(self._dir))` pre-write — if `current_total + new_size > max_bytes`, raise `TmpImageStoreFull` → router returns **507** + rate-limited WARNING (`_rate_limit(... , per=60s)` style). Prevents unbounded disk fill until phase 3 sweep ships; ~10 LOC. | Task 2 | Unit test: fill-to-cap → next `save()` raises `TmpImageStoreFull`; integration test: POST that would exceed cap → 507 + WARNING log; rate-limited WARNING: 100 sequential over-cap POSTs produce ≤1 WARNING per minute (assert via `caplog.records` count). |
| 3 | Wire `TmpImageStore` into the lifespan: resolve `data_dir` from `app.state.data_dir` (`daemon/api.py:244`); construct `TmpImageStore(data_dir)`; assign to `app.state.tmp_image_store`. Log a single `[TmpImages] ready: dir=<resolved> count=0 max_bytes=<GiB>` line at startup (mirrors `JobLockSweepService` boot log shape at `daemon/api.py:797-801`). | Tasks 2, 2b | Manual probe: boot the daemon; `ls <data_dir>/tmp_images` exists and is empty; `app.state.tmp_image_store` is non-None via a debug probe (optional). |
| 4 | Implement `POST /api/tmp_images` in a new router (suggested: `daemon/routers/tmp_images.py`); wire into `daemon/api.py` lifespan via `app.include_router(...)` **BEFORE** the SPA catch-all is defined. Request body: `{ "images": [TmpImageUpload, ...] }` (allows ≤3 per call, matching existing cap). Response: `{ "uploads": [TmpImageUploadResponse, ...] }`. Errors use the `ErrorResponse` envelope. Per-image write failures abort the whole request (atomic semantics — phase 2 assumes a successful POST yields valid refs). | Tasks 1, 3 | Integration test (FastAPI TestClient): POST 1 image → 200 with `{uploads: [{image_id, ref_url, content_type, size_bytes}]}`; POST 4 images → 422; POST 11MB → 422; round-trip GET returns identical bytes; nonexistent id → 404 with `ErrorResponse`. |
| 4b | **ADD `DELETE /api/tmp_images/{image_id}` (architect §3 amendment #6 / §2 required addition)** — 204 No Content, idempotent. Reuse the `^[a-f0-9]{32}$` regex (Task 5). `FileNotFoundError` swallowed silently (FE DELETE ∥ sweep race = expected traffic per architect §7). Same path-traversal safety as GET. No request body. The `ETag`/`Cache-Control` headers are not needed on DELETE responses (Starlette defaults). | Task 5 | Integration test: DELETE existing id → 204 + body empty; DELETE same id again → 204 (idempotent); DELETE malformed id → 404; DELETE `/../etc/passwd` path-shape → 404. **🟡 PHASE-3-DEFERRED (O6)**: the **race test** (parallel DELETE + phase-3 `TmpImageCleanupService.sweep_once()` of the same id → both succeed; either order wins, neither errors) is written when phase 3 lands — the sweep service does not exist in the phase-1 merge gate. Annotate the test file with a `phase-3-deferred` marker; the test goes green when phase-3 ships. |
| 5 | Implement `GET /api/tmp_images/{image_id}` in the same router. Path-traversal safety: `image_id` MUST match `^[a-f0-9]{32}$` (uuid4 hex) — reject any other shape with 404 (no body leak). Content-Type: stored MIME; `Content-Length`: exact bytes; `Cache-Control`: `private, max-age=3600` (display bubbles only live a few minutes, but a small cache helps repeated chat-panel opens within a session); `ETag`: weak ETag of the bytes (`W/"<sha256-hex[:16]>"`). **🔴 Hardening headers (architect §3 amendment #1, most consequential gap)** — every GET response MUST also carry `X-Content-Type-Options: nosniff` + `Content-Disposition: inline; filename="<image_id>"`. Without `nosniff`, a browser can sniff-to-`image/svg+xml`/`text/html` on direct navigation = same-origin XSS into the SPA (the daemon serves the FE itself — same origin, CORS `*` at `daemon/api.py:2422-2429`). Precedent for header hardening on byte routes: `daemon/routers/vscode_proxy.py:296-298` (sets CSP + X-Frame-Options on the same-origin vscode tunnel). | Task 4 | Integration test: GET valid id → 200 + bytes + correct Content-Type + BOTH hardening headers present (assert by header name); GET with `../etc/passwd` path-shape → 404 (path-traversal blocked); GET with non-hex id → 404; GET with ETag → 304 on re-request. **Negative test**: GET a `text/html`-tagged file → response carries `nosniff` so browser does not re-interpret. |
| 6 | Add `GET /api/tmp_images` (no id) for **health/debug — GATED (architect §3 amendment #4)**: returns `404 NOT_FOUND` by default; behind `ENSEMBLE_TMP_IMAGE_DEBUG_LISTING=1` returns `{ "count": <int>, "oldest_mtime": <iso or null> }`. The default-OFF posture means the route does not advertise itself to a casual probe. When enabled, returns only `count` + `oldest_mtime` — **no id leak** (ids appear in SSE, checkpoints, logs by design; do not amplify the surface). Recon-only exposure. | Tasks 2b, 5 | Integration test: GET without env flag → 404; GET with `ENSEMBLE_TMP_IMAGE_DEBUG_LISTING=1` → 200 + `count`/`oldest_mtime` (no `image_id` field present); after 2 uploads, count=2; after delete, count=1; response shape is byte-stable across toggles (only status code differs). |
| 7 | Pin the **FROZEN API CONTRACT** in a docstring at the top of `daemon/routers/tmp_images.py` and reference it from the plan-overview (this file's sibling). The contract shape: see "Provisional API contract" below. The contract was provisional in round-1 and is **FROZEN** in round-2 (architect §3 ratification): `ref_url` field is the canonical wire form (`/api/tmp_images/<id>` URL — `tmpimg://<id>` is **input-alias only** on GET). The router MUST implement BOTH formats: `ref_url` field returns the canonical URL form, but the GET endpoint accepts either the bare id OR the canonical `tmpimg://<id>` string OR the canonical `/api/tmp_images/<id>` URL (parse-tolerant input — FE phase-4 can pick a form without waiting on `decisions.md`). **🔴 Mandatory PUBLIC-BY-OBSCURITY guard-rail docstring (architect §6 amendment #5 — verbatim from §6 text):** at the top of `daemon/routers/tmp_images.py`, include the block — "PUBLIC-BY-OBSCURITY — daemon has no auth layer; ids are identifiers, not secrets. If any daemon endpoint gains auth, this MUST be the first file-serving route to gain it. Do not treat this as precedent for auth-free serving of sensitive content." | Task 5 | Docstring present (string-contains assertion in the same-PR test); GET accepts both `tmpimg://abc` and `abc` paths; unit test covers both. The guard-rail block is **NOT** optional — pin in the merge-gate checklist. |
| 8 | Add a sanity log line at lifespan startup (after the store is wired): `[TmpImages] dir=<resolved-path> backend=<posix> ready`. This is the boot anchor the tester will grep for in gate evidence. Mirrors `JobLockSweepService` boot line shape (`daemon/api.py:797-801`). | Task 3 | Boot log present; grep test passes. |
| 9 | Document the new endpoints in the OpenAPI schema via Pydantic models (auto-derived). No new doc file; rely on `/docs`. | Task 1 | `/docs` shows the new endpoints; Playwright smoke (later phase) can introspect. |
| 10 | Add a `data/tmp_images/.gitignore` (empty file) so accidental commits of test fixtures don't leak. Mirror the project's existing `.gitignore` patterns for `data/`. | none | File present; `git check-ignore` returns `true` for a sample path. |

---

## Provisional API contract (frozen, with §3 amendments)

> **Status**: FROZEN (architect §3 ratification). The `ref` field is
> renamed to **`ref_url`** (canonical = `/api/tmp_images/<id>` URL).
> `tmpimg://<id>` is **input-alias only** (parse-tolerated on GET; not
> returned in responses). The FE phase-4 worker builds against this
> shape. Errors match the existing `ErrorResponse` envelope at
> `daemon/routers/messages.py:215-220`. Allowlist = `png/jpeg/jpg/gif/webp`.

### `POST /api/tmp_images`

**Request**

```json
{
  "images": [
    {
      "filename": "screenshot.png",
      "content_type": "image/png",
      "data_base64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkAAIAAAoAAv/lxKUAAAAASUVORK5CYII="
    }
  ]
}
```

**Response 200**

```json
{
  "uploads": [
    {
      "image_id": "5f3a1b2c4d5e6f7a8b9c0d1e2f3a4b5c",
      "ref_url": "/api/tmp_images/5f3a1b2c4d5e6f7a8b9c0d1e2f3a4b5c",
      "content_type": "image/png",
      "size_bytes": 70,
      "uploaded_at": "2026-09-19T16:49:18+00:00"
    }
  ]
}
```

**Errors**

- `400 INVALID_REQUEST` — base64 malformed, MIME not in allowlist, count > 3,
  size > 10MB. Allowlist rejection message is verbatim:
  `"content_type '<t>' rejected — only png/jpeg/gif/webp allowed (svg excluded: stored-XSS via direct navigation)"`.
- `507 INSUFFICIENT_STORAGE` — `tmp_image_store_max_bytes` exceeded (architect amendment #3).
- `500 INTERNAL_ERROR` — write failure (filesystem full, permissions).

### `GET /api/tmp_images/{image_id_or_ref}`

- Accepts either the bare id (`5f3a1b2c4d5e6f7a8b9c0d1e2f3a4b5c`) or the
  input-alias `tmpimg://5f3a1b2c4d5e6f7a8b9c0d1e2f3a4b5c`. Strips the
  `tmpimg://` prefix server-side.
- Path traversal: id MUST match `^[a-f0-9]{32}$`. Otherwise `404`.
- Returns `200 image/<mime>` with bytes. **Required headers**:
  - `Content-Type: <stored-mime>`
  - `Content-Length: <exact bytes>`
  - `Cache-Control: private, max-age=3600`
  - `ETag: W/"<sha256-hex[:16]>"`
  - **`X-Content-Type-Options: nosniff`** (architect amendment #1 — same-origin XSS lever)
  - **`Content-Disposition: inline; filename="<image_id>"`** (architect amendment #1)
- `404 NOT_FOUND` — id not in store (cleaned up, never existed).
- `304 NOT_MODIFIED` — `If-None-Match` matches stored ETag.

### `DELETE /api/tmp_images/{image_id}`

- **204 No Content**, idempotent. No request body. Path-traversal safety
  identical to GET (`^[a-f0-9]{32}$`, else 404). `FileNotFoundError`
  swallowed silently (FE DELETE ∥ phase-3 sweep race = expected traffic).
- `404 NOT_FOUND` — id never existed (still returns 404 — the request
  was structurally invalid, not just missing-on-disk).

### `GET /api/tmp_images` (health/debug, GATED)

- **Default OFF** (`ENSEMBLE_TMP_IMAGE_DEBUG_LISTING` unset or `0`) → `404 NOT_FOUND`.
- **`ENSEMBLE_TMP_IMAGE_DEBUG_LISTING=1`** → `200 { "count": int, "oldest_mtime": iso8601|null }`. No `image_id` field — recon-only exposure.

---

## Dependencies

### Backward (depends on)

- Nothing. This phase introduces new code; it does not modify existing
  semantics.

### Forward (is depended on by)

- **Phase 2** (conversion) consumes refs from POST response and re-fetches
  bytes via GET during `explain_image` invocation. The ref format MUST be
  stable from phase 1 → phase 2.
- **Phase 3** (retention) reads `data/tmp_images/` via the `TmpImageStore`
  facade (`list_mtimes`, `delete`); phase 3 cannot ship before phase 1's
  store API exists.
- **FE phase 4** (paste + upload) calls `POST /api/tmp_images` and renders
  bubble placeholders using the returned refs. The provisional contract
  above is the FE's wire spec.
- **FE phase 5** (transcript merge) and **FE phase 6** (viewer dialog +
  onerror placeholder) call `GET /api/tmp_images/<ref>` for display and
  image click-through.

### Cross-cutting

- **Activated via rebuild + restart** (per `daemon` activation convention).
  No DB migration required — this phase touches the filesystem only.

---

## Test strategy

### Unit (per component)

| Test | File | Verifies |
|---|---|---|
| `test_tmp_image_model.py` | `tests/unit/models/` | `TmpImageUpload` validators: happy path, MIME allowlist, base64 well-formed, size cap, count cap, magic-byte cross-check. |
| `test_tmp_image_store.py` | `tests/unit/services/` | Store round-trip bytes; `delete` removes file; unknown id raises `TmpImageNotFound`; idempotent `init` (re-running mkdir on existing dir is a no-op). |
| `test_tmp_image_router.py` | `tests/unit/routers/` | POST happy-path + every rejection branch; GET path-traversal rejection (every malformed id shape including `..`, `/`, `\`, unicode); GET ETag/304 cycle; health endpoint count. |

### Integration

| Test | File | Verifies |
|---|---|---|
| `test_tmp_images_e2e.py` | `tests/integration/` | Real FastAPI TestClient + real `TmpImageStore` on `tmp_path`: round-trip POST → GET returns byte-identical PNG; multi-image POST (3 images); 4-image rejection. **Critical**: GET registers BEFORE SPA catch-all — assert by mounting the catch-all in a fixture and verifying `/api/tmp_images/abc` is NOT served as `index.html`. |
| `test_tmp_images_boot.py` | `tests/integration/` | Lifespan integration: boot `create_app`, assert `app.state.tmp_image_store` is non-None, assert `<data_dir>/tmp_images/` exists post-startup. Mirrors `tests/integration/test_job_driven_enqueue_work_id_facade.py` shape. |

### Facade-forwarding discipline

- **Not applicable** — phase 1 does not add new kwargs to
  `InstanceManager.enqueue_message` or `set_injection`. No facade
  forwarding guards required. (If phase 2 adds kwargs, it owns those
  guards.)

### Web-automation e2e (tester will run later)

The tester will need these seams to write a Playwright e2e:

- `POST /api/tmp_images` accepts JSON (no multipart) — JSON-stringifiable
  in `page.request.post(...)`.
- The health endpoint returns deterministic count.
- `GET /api/tmp_images/<id>` is reachable from the browser at the same
  origin (no CORS needed).
- Path traversal returns `404` (not `500`) — keeps Playwright assertions
  clean.

---

## Phase risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| 1 | SPA catch-all order — if `GET /api/tmp_images/{id}` is registered AFTER `app.get("/{path:path}")`, the path collides with the SPA `index.html` serve | High (image URLs render HTML, breaking every chat bubble that references them) | Medium (subtle; only matters when both routes' path patterns overlap, which they do here) | **Hard rule**: register the new router via `app.include_router(...)` BEFORE the SPA catch-all is added. Add an integration test (above) that mounts a real catch-all and asserts the new GET does NOT fall through to `index.html`. Mark this in the docstring + commit message. |
| 2 | Path traversal — a malformed `image_id` could read outside `data/tmp_images/` | High (RCE-class read) | Low (Starlette does not decode `%2F` to `/` inside `{image_id}` segments, but defensive regex locks it down) | Regex `^[a-f0-9]{32}$` BEFORE any filesystem call; reject everything else as `404 NOT_FOUND` (not `400`, to avoid leaking the regex shape). Test covers `..`, `/`, `\`, unicode, control chars. |
| 3 | Filesystem exhaustion under chat-heavy load — an agent loop that sends 3 images per turn × N turns/day could fill the disk before phase 3 ships | Medium | Medium | **(a)** Cap per upload at 10MB × 3 = 30MB; **(b)** cap total per session via the per-request count limit; **(c)** phase 3 ships immediately after phase 1 (same PR is fine). Add a `[TmpImages] dir=<...> count=<...>` health probe (Task 6) so dashboards can alert before exhaustion. |
| 4 | Concurrent writes to the same image_id — vanishingly unlikely with `uuid4` but possible if a client retries with the same id | Low (writes are atomic on POSIX; overwrites win) | Low | Each `save(image_id, ...)` does `os.open(O_CREAT|O_EXCL|O_WRONLY)` to fail on collision; if `FileExistsError`, return `409 CONFLICT` (caller bug — they should not reuse ids). |
| 5 | Concurrent delete + read in phase 3 — phase 1 only writes/deletes; reads happen via GET during display. If phase 3's sweep deletes a file between the FE's `POST` and the user's next page open, the FE gets `404`. | Medium (broken image bubble) | Low (retain 30 days; users rarely open a chat hours later) | Document the contract: "ids are valid for ≥30 days". The FE phase 6 ships an onerror placeholder for `404` (graceful degradation). Phase 3 logs every deletion so support can diagnose. |
| 6 | `data_dir` resolution drift — if a future change moves `app.state.data_dir` (currently `daemon/api.py:244`), `TmpImageStore` would silently write elsewhere | Low | Low | The store reads `data_dir` from `app.state.data_dir` at lifespan startup ONLY; logs the resolved absolute path in the boot anchor (Task 8); the integration test asserts the path matches. If the anchor disappears, the integration test fails loudly. |
| 7 | File extension leaks MIME information in the URL — `image_id.png` vs `image_id` — and could expose the underlying MIME to an attacker | Low | Low | Store files WITHOUT extensions (the GET endpoint always sets `Content-Type` from the stored MIME record, never from a filename suffix). Path-traversal regex rejects `.` in id anyway. |

---

## Open questions (escalated to `decisions.md`)

1. **Ref string format** — `tmpimg://<id>` vs bare `<id>` vs full URL
   `http://host/api/tmp_images/<id>`. Provisional: `tmpimg://<id>`.
   The GET endpoint accepts either bare or canonical (parse-tolerant)
   so FE phase-4 can pick. `decisions.md` will finalize.
2. **Cleanup interval** — 30 days default + daily sweep, vs shorter for
   privacy-sensitive operators. Phase 3 owns the config knob;
   phase 1 doesn't decide.
3. **Per-user / per-session scoping** — should `image_id` be unique
   across the daemon, or scoped to a session so cross-session leak is
   impossible? Provisional: daemon-global (simpler; cleanup reaps).
   Phase 2 may add per-session metadata; revisit in phase 2 risks.? Provisional: daemon-global (simpler; cleanup reaps).
   Phase 2 may add per-session metadata; revisit in phase 2 risks.