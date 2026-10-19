# Research: Live-Views Subsystem for ari-tools P1/P3 (view_write + live board)

Worktree: `/home/nea/ensemble-src-wt-ari-tools` (branch `feature/ari-tools`, base latest@630cefebb).
All anchors from THIS checkout. Read-only research, 2026-10-19.

---

## 1. LIVE_VIEWS SERVICE (daemon/services/live_views.py + daemon/routers/live_views.py)

### 1a. Root registry — where roots are DECLARED

- **Config-driven, NOT .env, NOT DB.** `LiveViewsConfig` at `daemon/config.py:2954`; the registry field is
  `roots: dict[str, LiveViewsRootConfig] = Field(default_factory=_seed_phase1_roots)` at `daemon/config.py:3061-3069`.
- Seeds built by `_seed_phase1_roots()` at `daemon/config.py:2916-2951`. **Three roots ship today:**
  1. `designer-artifact` — `type="project_scoped"`, `path=".agents/shared/planning"`,
     `required_rel_subpath=["design","mockups"]` (`daemon/config.py:2917-2935`).
     On-disk: `<project_workdir>/.agents/shared/planning/<feature>/design/mockups/…`.
     URL: `/views/designer-artifact/<project_shortname>/<feature>/design/mockups/<file>`.
  2. `planning` — `type="project_scoped"`, `path=".agents/shared/planning"` (`daemon/config.py:2936-2943`).
     On-disk: `<project_workdir>/.agents/shared/planning/<rel>`. URL: `/views/planning/<project_shortname>/<rel>`.
  3. `tmp-images` — `type="tmp_images"`, no path; shims the daemon-global `TmpImageStore`
     (`daemon/config.py:2944-2950`). On-disk: `<data_dir>/tmp_images/` (see §2). MIME from sidecar, not extension.
- `LiveViewsRootConfig` fields (`daemon/config.py:2675-2757`): `type` literal
  `"filesystem" | "project_scoped" | "tmp_images"` (:2700), `name` (auto-stamped from dict key, :2712),
  `path` (:2720), `enabled` (:2728), `allowed_extensions` (:2732), `required_rel_subpath` (:2740),
  `description` (:2754).
- **Registry is read-only at request time** — populated once at lifespan start from `config.live_views.roots`,
  immutable thereafter; "adding a fourth root = config edit + restart" (`daemon/services/live_views.py:23-28`).
- Reserved root name: `RESERVED_ROOT_NAMES = frozenset({"public"})` at `daemon/services/live_views.py:141`;
  `PUBLIC_NAMESPACE = "/views/public"` frozen constant at `daemon/services/live_views.py:155`.

### 1b. What it takes to add a NEW root

**Config-only + restart. No code change for the serving path.**

1. Add an entry under `live_views.roots:` in config.yaml (operator-editable; `daemon/config.py:2903-2914`).
   ⚠️ **Pydantic semantics: an explicit `live_views.roots:` block REPLACES the seeded dict** — the new block
   must re-list every seed root it wants to keep (`daemon/config.py:2903-2914` shows the canonical example).
2. Restart the daemon (values read once at lifespan start; restart-to-flip matches the `live_views.*` config
   precedent, `daemon/services/live_views.py:23-28`).
3. Do NOT name it `public` — config validator refuses to boot:
   `_reject_reserved_root_names` at `daemon/config.py:3098-3142` (3-layer guard: registry validator +
   route-order + handler mirror `daemon/routers/live_views.py:1163-1166,1318-1319`).
4. Tool-side: `view_link` needs **zero code change** — it iterates `service.root_names()` and works over any
   registered root (`daemon/tools/live_views.py:279-283`). Only note: `view_link` is privileged-category
   (`view-views`), allow-listed on ari/leader/designer meta.json `tools.allow` (`daemon/tools/live_views.py:11-20,199-209`).
5. Code change ONLY if the new root needs a NEW root `type` (discriminated union at `daemon/config.py:2700`
   + a resolver branch in `LiveViewsService`) or a new dedicated route. A `filesystem`-typed root pointing at a
   daemon-global reports dir (e.g. under `data_dir`) is pure config.

### 1c. view_link tool mechanics

- Signature: `view_link(root_name: str, path: str, refresh: bool = False) -> str` at `daemon/tools/live_views.py:113-115`;
  factory `create_live_view_tools` :91; category `@register_tool_category("view-views")` :111.
- Validation chain (typed `"Error: ..."` envelopes, never raises, `daemon/tools/live_views.py:238-283`):
  service present/enabled → root_name/path non-empty → `is_well_formed_root_name` (kebab-case) →
  `is_reserved_root_name` → `is_well_formed_rel_path` (no `..`, no leading `/`, no control chars) →
  `service.has_root(root_name)`. Path-traversal guard surface (reject `..`, `%2e`, `%252e`, null bytes,
  backslash, realpath containment incl. symlink escapes) documented at `daemon/services/live_views.py:30-44`.
- Returns FIVE labeled lines (`daemon/tools/live_views.py:118-146`):
  `Public URL` (`/views/public/<root>/<path>`), `Passcode` (6-char), `Authenticated URL`
  (`/views/<root>/<path>`), `Generation`, `Locked` (+ refresh hint when locked).
- URL construction: `LiveViewsService.build_url` `daemon/services/live_views.py:1201`;
  `build_public_url` :1243-1289 → `f"{PUBLIC_NAMESPACE}/{root_name}/{rel_path}"` (:1285) with base from the
  resolver chain: operator `external_base_url` > Host-capture > bind evidence > path-relative (:1263-1266).
- DB side effects: `passcode_service.mint(root, path, actor)` first call (lazy-mint), idempotent repeat,
  `refresh=True` bumps generation + clears lock + audit row (`daemon/tools/live_views.py:160-171,327-335`).
- **32 MiB cap:** `MAX_SERVED_BYTES: int = 32 * 1024 * 1024` at `daemon/services/live_views.py:166`.
  Enforcement points: resolve-time `if size > MAX_SERVED_BYTES` (`daemon/services/live_views.py:1186`) and
  read-time re-check inside `_fd_read(path, MAX_SERVED_BYTES)` (`daemon/routers/live_views.py:385`, called at
  :1835 and :1956+) — closes the symlink-swap and size-cap TOCTOU races (:1942-1949).

### 1d. Passcode mechanism

- **Per-view-path, not per-root, not global.** One `live_view_passcodes` row per `(root_name, rel_path)`
  — unique index `uq_live_view_passcodes_root_rel` (`daemon/migrations/versions/20261017_193816_create_live_view_passcode_tables.sql:137-150`).
  Ciphertext = Fernet via `SYSTEM_ENCRYPTION_KEY` (`ViewPasscodeCipher`, wired `daemon/api.py:1722-1757`).
- **Generated per link**: lazy-mint on first `view_link` call; same passcode re-displayed on repeat;
  `refresh=True` mints a new code + generation+1 (silently revokes all grant cookies) + clears lock
  (`daemon/tools/live_views.py:153-170`). 6-char code, constant-time compare, 10-strike durable lockout
  (`passcode_lockout_threshold` default 10, `daemon/config.py:3039-3052`; NO auto-unlock — refresh is the
  only recovery). Grant TTL 24h (`passcode_grant_ttl_seconds` default 86400, `daemon/config.py:3053-3060`).
- **HTTP verification (public lane only):**
  - Routes: `GET/HEAD/POST /views/public/{root_name}/{rel_path:path}` (`daemon/routers/live_views.py:1744,1750,1756`).
  - GET gate order (`_public_resolve`, `daemon/routers/live_views.py:1271-1392`): ① well-formed root/rel →
    ② root configured+enabled → ③ passcode row exists (any miss → **uniform 404**, `_uniform_404` :474; no row
    ever created by GET) → ④ **grant cookie `view_grant` valid?** (`request.cookies.get("view_grant")` :1346;
    `parse_grant_cookie` binds to root+rel+generation :1352-1358; valid → serve) → ⑤ locked? → locked page.
    No grant → interstitial page (200, zero-JS form, field `code`).
  - POST verify (`_public_post`, `daemon/routers/live_views.py:1464-1660+`): per-IP token bucket (429 page,
    no strike consumed) → row lookup (404) → locked short-circuit → CSRF same-host Origin/Referer check
    (403, `_same_host_origin_check` :721) → form `code` → constant-time `verify()` → success:
    `issue_grant_cookie(...)` + audit `grant` + **303 See Other** PRG back to same URL (:1588-1660);
    failure: failure page + strike + audit `deny` (:1700-1742).
  - Cookie details: stateless Fernet token bound `(root, rel, generation)`; `Path` = EXACT queryless public
    view path (`_grant_cookie_path` :791, rationale :1635-1648); `Secure` on HTTPS — plain HTTP renders the
    explicit secure-context page instead of looping (:1656-1658, `view_passcode_pages.py:33-36`).
- **Unauthorized returns:** uniform 404 (unminted/unknown/disabled — no path disclosure), interstitial 200
  (minted, no/invalid grant), locked page 200 (locked), 429 page (rate-limited) — all built in
  `daemon/routers/live_views.py:474,551,637,654` from `daemon/services/view_passcode_pages.py` templates.

### 1e. Router + response headers (security surface)

- Router: `build_router()` at `daemon/routers/live_views.py:1046`; `APIRouter(prefix="/views", tags=["live_views"])`
  :1086. Mounted at `daemon/api.py:4437` (`app.include_router(_build_live_views_router())`) — **before the SPA
  catch-all** (route-order discipline, ADR D1). Pulls services from `app.state` at request time (:1049-1054).
- **Response headers by lane:**
  - `_HARDENING_HEADERS` (`daemon/routers/live_views.py:122-125`): `X-Content-Type-Options: nosniff` +
    `Cache-Control: private, max-age=60`. **This is ALL raw file bytes get — including raw `.html` files.
    NO CSP, NO X-Frame-Options on direct-lane HTML** — per the architect ruling quoted at :176-179:
    "The raw `.html` family … continue[s] to be served with the basic `_HARDENING_HEADERS` set and no CSP,
    per the architect ruling that legitimate HTML may include scripts."
  - `_ASSET_HEADERS` (:137-140): hardening + `Content-Security-Policy: default-src 'none'` — vendored mermaid
    asset only (`/views/assets/mermaid/mermaid.min.js` :150, served :1088/:1218).
  - `.md` wrapper: per-response nonce'd CSP built service-side (`render_markdown_wrapper`); loads pinned-CDN
    `marked` + DOMPurify with strict SRI; mermaid-fence variant loads the VENDORED (not CDN) mermaid bundle
    (`daemon/routers/live_views.py:153-227`; `daemon/services/live_views.py:1296-1302` provenance SHA-256-pinned).
  - Passcode pages: `page_headers(csp_nonce)` (`daemon/services/view_passcode_pages.py:68-89`) —
    `Content-Security-Policy: default-src 'none'; script-src 'none'; style-src 'nonce-…'; form-action 'self';
    base-uri 'none'; frame-ancestors 'none'` + nosniff + `Cache-Control: no-store` + `Referrer-Policy: no-referrer`.
    Zero-JS by design (:7-13).
- **Implication for chart.js/mermaid-via-CDN report pages:** raw `.html` under `/views/<root>/` is served with
  NO CSP today, so CDN scripts already function on the direct lane AND post-grant on the public lane
  (`_serve_resolved_target` :956-1045 applies the same hardening set, `public=True` only changes asset URL
  emission). A NEW server-rendered page chooses its own posture — all three precedents exist in-tree:
  no-CSP raw (filesystem roots), nonce'd CDN wrapper (markdown), script-src 'none' (passcode pages).

## 2. STORAGE SUBSTRATE PRECEDENT (daemon-global reports dir)

- **Daemon-side GLOBAL data dir:** `daemon/api.py:483-488` — `data_dir = Path(ENSEMBLE_DATA_DIR or DATA_DIR or "./data")`;
  stashed at `app.state.data_dir` (:493).
- Wiring: `build_tmp_image_store(data_dir=data_dir, max_bytes=config.services.tmp_image_store_max_bytes)`
  at `daemon/api.py:538-558` (fail-soft: init failure → store None → endpoints 503, :530-551).
  Factory: `daemon/services/tmp_image_store.py:819-832`.
- **Directory layout** (`daemon/services/tmp_image_store.py:149-154,800-809`): `<data_dir>/tmp_images/<32-hex-id>`
  extensionless blob + `<id>.json` sidecar (`_METADATA_SUFFIX`). Sidecar carries original filename, MIME
  `content_type`, feature/page/version, provenance, retention_class. MIME ALWAYS from sidecar, never sniffed
  (:14-21, :76-84).
- **Atomicity:** blob `O_CREAT|O_EXCL`; sidecar tmp-file + `os.replace` — sidecar write is its own atomic step
  (:17-20, :35, :301+).
- **Retention classes** (:86-90, :104-107): `"normal"` (swept at 30 days) / `"protected"` (design baselines,
  exempt from sweep); store cap counts ALL bytes (`max_bytes` default 1 GiB, :143-146; config knob
  `tmp_image_store_max_bytes` `daemon/config.py:2378`, env `SERVICES_TMP_IMAGE_STORE_MAX_BYTES`).
- This is the exact precedent for a daemon-global `<data_dir>/reports/` (or DB-backed board) — same factory
  shape, same fail-soft boot init, same `app.state` stashing.

## 3. SERVER-RENDERED HTML FROM DB — PRECEDENT SCAN

**No existing route renders HTML from DB state as a data view.** Grep of `daemon/` for
`HTMLResponse|TemplateResponse|Jinja|text/html` — full inventory of HTML producers:

1. `daemon/routers/live_views.py` (:220, :255, :1806, :1858, :1873) — wrapper pages built from FILE bytes
   (.md/.mmd), not DB state.
2. `daemon/services/view_passcode_pages.py` — five passcode pages (interstitial/failure/locked/429/
   secure-context). **Templates are inline-strings + `str.format` — no Jinja, no build artifact, no
   frontend/ coupling** (:38-42). Closest structural precedent for a server-rendered page shell, but content
   is static notices, not DB rows.
3. `daemon/routers/tmp_images.py:425` — comment only (text/html XSS posture); endpoint is JSON.
4. `daemon/routers/vscode_proxy.py:699-702,896` — proxy rewrite of upstream text/html, not a renderer.
5. `daemon/middleware/auth.py:557,663` — Accept-header negotiation only.

All other routers are JSON; the UI is the Angular SPA. **A first server-rendered board page is greenfield.**
Recommended reuse:
- Template pattern: inline-string + `str.format` module, per `view_passcode_pages.py` (or a new module —
  growth policy: new self-contained concern gets its own module, `daemon/services/live_views.py:94-98`).
- Headers: `page_headers()`-style CSP or `_HARDENING_HEADERS` (pick a posture, §1e).
- Passcode plumbing: `ViewPasscodeService` on `app.state.view_passcode_service` (`daemon/api.py:1704-1801`,
  `manager.set_view_passcode_service` `daemon/manager.py:3092-3102`) + `issue_grant_cookie` /
  `parse_grant_cookie` from `daemon/services/view_passcode_service.py` — OR piggyback the existing public-lane
  gate by registering a root and minting rows for board URLs (works only if the board renders as a FILE;
  a DB-fresh route needs its own handler + gate).
- Mount: new route must be declared on the `/views` router (or a sibling) BEFORE any parametrized
  `/{root_name}/{rel_path:path}` catch-all — Starlette first-match-wins is load-bearing
  (`daemon/routers/live_views.py:1068-1078,1193-1205`), and the views mount precedes the SPA catch-all
  (`daemon/api.py:4437`).

## 4. PASSCODE / PROXY INVARIANT (new roots cannot break it)

- ADR of record: `.agents/shared/planning/live-view-passcode/architecture-recommendation.md` (DECIDED;
  D1 three-layer guard, D8 rollout, D11 namespace/logging hygiene).
- Runbook: `docs/runbooks/live-view-passcode-deployment.md`. **The one-line contract (:7-11):**
  > The public namespace is **exactly `PathPrefix /views/public`** — nothing narrower and nothing wider, in
  > BOTH deployment states. The canonical passcode gate is **daemon-side**; proxies are **pure passthrough**.
  > Never exempt `/views` or `/api`.
- What rides the public prefix (:20-25): interstitial GET, same-URL verify POST, view bytes + `?raw=1`,
  gate-free mermaid asset alias `/views/public/assets/mermaid/mermaid.min.js`. What must NEVER ride it:
  the direct lane `/views/<root>/<rel>` (OAuth-guarded) and `/api/**`. **`/views/assets/**` stays
  default-deny under auth-ON** — pinned by
  `tests/integration/test_auth_public_lane_tag.py::TestAuthOnPublicLaneExemption` (:31-34).
- The prefix is a **frozen code constant**, not config: `PUBLIC_NAMESPACE` (`daemon/services/live_views.py:155`),
  with `public` reserved (`RESERVED_ROOT_NAMES` :141; config boot-refusal `daemon/config.py:3098-3142`).
- Over-broad-tag safety: `_template_prefix_matches` (`daemon/auth/route_meta.py:118-125`) truncates templates
  at the first `{`, so the legacy `/views/` family can never be swept into a `/views/public` prefix tag (ADR D1).
- **New-root interaction:** a root named `reports` (or `board`) requires ZERO proxy change. Direct lane
  `/views/reports/...` stays behind OAuth in State A and behind the in-house auth module in State B; public
  lane `/views/public/reports/...` rides the ALREADY-exempt prefix with the daemon-side gate
  (per-IP bucket → 10-strike lock → Fernet verify → grant cookie → audit) applied automatically because the
  gate is keyed on `(root_name, rel_path)` rows, not on a root list. State A infra (HTTPRoute rule
  `feature/ensemble-views-public-exemption` @ e198005) and State B (code-seeded public prefix tag) both
  already cover ANY root under the prefix.
- Guardrails for the new work: never mint passcode rows or mount gate-free routes OUTSIDE `/views/public/**`;
  never name a root `public`; never widen the exemption to `/views/assets` (`daemon/routers/live_views.py:1214-1217`);
  keep Host preservation (no internal rewrite) so the same-host Origin/Referer CSRF check keeps working
  (runbook §1.1, `daemon/routers/live_views.py:721`).

## Key file map

| Concern | Anchor |
|---|---|
| Config model + seeds | `daemon/config.py:2675-2951` (RootConfig :2675, LiveViewsConfig :2954, reserved-guard :3098) |
| Service (registry/resolve/URL/mime/MD-render) | `daemon/services/live_views.py` (RESERVED :141, PUBLIC_NAMESPACE :155, MAX_SERVED_BYTES :166, build_url :1201, build_public_url :1243) |
| Router (all /views routes + headers) | `daemon/routers/live_views.py` (headers :122-140, pages :551-686, build_router :1046, public gates :1271-1742, direct GET :1886) |
| Passcode pages templates | `daemon/services/view_passcode_pages.py` (headers :68-89, inline templates :38-42) |
| view_link tool | `daemon/tools/live_views.py` (:113 signature, :238-283 validation, :327-335 mint/refresh) |
| Passcode service + tables | `daemon/services/view_passcode_service.py`, `daemon/api.py:1704-1801`, migration `20261017_193816_create_live_view_passcode_tables.sql` |
| tmp_images substrate | `daemon/services/tmp_image_store.py` (:149-154 layout, :819-832 factory), `daemon/api.py:483-558` (data_dir + wiring) |
| Runbook / ADR | `docs/runbooks/live-view-passcode-deployment.md`, `.agents/shared/planning/live-view-passcode/architecture-recommendation.md` |
