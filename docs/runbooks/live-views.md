# Live-Views Subsystem — Phase 1 Runbook

- **Version:** 1.0 @ feature/live-views *(Phase 1 of the live-view subsystem commission, shipped with the post-v0.18.1 release vehicle)*
- **Canonical path:** `docs/runbooks/live-views.md` (matches the `docs/runbooks/` house convention; the other live-views planning lives in `.agents/shared/planning/live-views/`).
- **Scope:** the `GET/HEAD /views/<root>/<rel>` HTTP route family + the `view_link` agent tool + the per-app `LiveViewsService` registry. **No write / delete / list endpoints** — read-only by design. **No auth at the daemon** — the edge guard is the deployment's responsibility.

## 0. The single edge rule

**`/views/*` is public-by-obscurity and MUST sit behind the same OAuth proxy that fronts the daemon.** The daemon has no auth layer; the URL is the only access control. Three lines in the OAuth proxy (Caddy / nginx / Cloudflare Access) are sufficient:

```nginx
# Example (nginx) — adapt for your proxy of choice.
location /views/ {
    auth_request /oauth2/auth;       # or your proxy's auth directive
    proxy_pass http://127.0.0.1:9797; # the daemon
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

The URL is "public-by-obscurity" the same way `/api/tmp_images/<id>` already is — the convention lives in `.agents/shared/conventions.md`. **The difference**: tmp_images keys are 32-hex content-addressed ids (unguessable in practice). `view_link` URLs carry the file's repo-relative path (a deterministic name like `mockups/landing.html`), so an attacker who can guess the project + path shape can read the artifact. **This is by design — the URL is meant to be a stable reference an implement-brief can carry, not a secret.** Edge auth is the layer that keeps it scoped to the right team.

**Do NOT enable `/views/*` without edge auth in any internet-facing deployment.** Local dev (no proxy, direct daemon access) is fine; the URL is harmless on a laptop.

## 1. v1 scope (delivered)

* **Route family:** `GET /views/<root>/<rel>` and `HEAD /views/<root>/<rel>`. No POST, no DELETE, no LIST. Unknown root / unknown path / traversal / extension-not-allowed / torn sidecar / subsystem-off all return a uniform `404 {"error": "view not found"}` envelope (no body that distinguishes the cases, nosniff on every response).
* **Three seed roots** (config-driven; operator may add or disable any via `live_views.roots` in `config.yaml` + restart):
  * `designer-artifact` — project-scoped (REWORK 2026-10-07, M2). URL shape: `/views/designer-artifact/<project_shortname>/<feature>/design/mockups/<file>`. The first path segment after the root is the project shortname (must be a registered `Project.shortnames` entry), exactly like `planning`. The canonical mockups subtree is the `<project_workdir>/.agents/shared/planning/<feature>/design/mockups/` template; the resolver enforces the `*/design/mockups/*` shape by construction (M3) so the URL cannot serve arbitrary planning files under the `designer-artifact` name.
  * `planning` — project-scoped. URL shape: `/views/planning/<project_shortname>/<rel>`. The first path segment after the root is the project shortname (must be a registered `Project.shortnames` entry). The root is the per-project `.agents/shared/planning/` subtree.
  * `tmp-images` — delegates to the per-app `TmpImageStore` substrate. MIME comes from the sidecar record (architect risk #7 — NEVER extension-guessed). 32-hex image id only.
* **`view_link` tool** — agent-facing URL minter. **RESTRICTED first-release visibility (REWORK 2026-10-07, M1, user refinement #1).** The `view-views` category is in `PRIVILEGED_TOOL_CATEGORIES` (empty-allow agents do NOT get the tool); the three commissioned users (ari, leader, designer) opt in via `tools.allow: ["view-views"]` in their meta.json. The tool name stays in `KNOWN_TOOL_NAMES` (inventory, not the gate). Returns path-relative URLs by default; fully-qualified when `config.live_views.external_base_url` is set. Route stays general; extensibility is per-agent allow entries + per-root config.
* **Designer write-through** — the design artifacts table contract (`agents/designer/skills-template/design-strategy.md:71`) gains a `view_url` column populated via `view_link('designer-artifact', f"{shortname}/{<row.path>}")` (REWORK 2026-10-07, M2 — the first URL segment is the project shortname, the rest is the row's `path` value, e.g. `view_link('designer-artifact', "ens/feat/design/mockups/landing.html")`). The LLM writes the column; the tool is the surface.

**Out of v1 scope** (Phase 2 candidates, not in this slice):
* FE WebView rendering of served content
* Conversion (markdown → HTML, image resize)
* Auth at the daemon (the edge proxy is the layer)
* Write / delete / list endpoints (read-only by design)

## 2. Operator notes

### 2.1 Add a new root

```yaml
# config.yaml
live_views:
  enabled: true
  roots:
    designer-artifact:                  # existing — project_scoped (REWORK M2)
      type: project_scoped
      path: .agents/shared/planning
      enabled: true
    planning:                            # existing — left in place
      type: project_scoped
      path: .agents/shared/planning
      enabled: true
    tmp-images:                          # existing — left in place
      type: tmp_images
      enabled: true
    docs:                                # NEW — operator-supplied
      type: filesystem
      path: /var/ensemble/docs           # absolute path (filesystem roots require absolute)
      enabled: true
      allowed_extensions: [html, css, js, md, pdf, png, jpg, svg]
      description: "Operator-curated HTML docs served at /views/docs/<rel>"
```

Then **restart the daemon**. The registry is read once at lifespan start; there is no SIGHUP / hot-reload for the roots table. Per the `live_views.*` config precedent, every operator change is a restart-to-flip.

### 2.2 Disable a root

Set `enabled: false` in the same config block, then restart. The root is still listed in `view_link`'s error envelope (so an agent that guessed the name gets a clear "disabled" hint) but the route family returns the uniform 404 for any URL keyed on the disabled root.

### 2.3 Disable the whole subsystem

Set `live_views.enabled: false`, then restart. The router still mounts the route family, but every request returns the uniform 404 (the service-level guard). `view_link` returns `Error: live-views subsystem is disabled`.

### 2.4 Switch the URL base to fully-qualified

Set `live_views.external_base_url: https://ensemble.example.com`, then restart. `view_link` mints `https://ensemble.example.com/views/<root>/<rel>` instead of `/views/<root>/<rel>`. **The OAuth proxy MUST resolve the same hostname** — a mismatch (minted URL points to host A, the proxy is on host B) yields a confusing 404-from-proxy and a useful-but-hard-to-debug log line.

**Default (unset) is the recommended setting for behind-OAuth-proxy deployments** — the daemon has no canonical public hostname, so a path-relative URL is the safe mint. Operators that need fully-qualified URLs (e.g. a share-by-link feature in the FE) opt in explicitly.

### 2.5 Verify the subsystem is alive

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8079/views/livez
# 200 = subsystem enabled and wired
# 404 = subsystem disabled OR lifespan did not wire the service
```

The `/views/livez` route is gated by the same uniform-404 envelope as everything else; a `200` response is a positive signal, a `404` is ambiguous (it could be "disabled" or "not wired") — check the daemon log for the `[LiveViews] subsystem ready:` line in the latter case.

### 2.6 `required_rel_subpath` — the per-root subpath gate (M3)

`project_scoped` roots may declare a `required_rel_subpath` list to pin the URL's rel path to a specific sub-tree under the per-project `entry.path` directory. The gate is enforced at resolve time in `daemon/services/live_views.py:452-467` as a contiguous sliding-window match over the rel's `/`-separated parts. Three shapes are valid:

* `required_rel_subpath: []` (empty list — default) — the gate is **skipped**. The rel may name any path under the per-project `entry.path` tree. The seeded `planning` root uses this shape (it serves the whole `.agents/shared/planning/` subtree).
* `required_rel_subpath: [""]` (or any list containing an empty string) — **misconfiguration**. The resolver treats this as a uniform-404 root with no useful service, because an empty element can never match a real path segment. Operators hitting a uniform 404 on a `project_scoped` root with a non-empty `required_rel_subpath` should grep the running config for empty subpath entries.
* `required_rel_subpath: ["design", "mockups"]` (populated, non-empty) — the gate fires. The rel's `/`-separated parts must contain the configured sequence as a CONTIGUOUS subsequence. The seeded `designer-artifact` root uses this shape; examples:
  * `feat/design/mockups/landing.html` — passes (parts `design`+`mockups` are adjacent).
  * `feat/random.html` — uniform 404 (`design`+`mockups` not present).
  * `feat/Design/mockups/landing.html` — uniform 404 (case-sensitive; segment comparison is exact).
  * `feat/design/extra/mockups/landing.html` — uniform 404 (the required sequence must be CONTIGUOUS, not just present).

The gate exists to make a per-root URL namespace (`designer-artifact` ≠ `planning`) actually mean what it says: an operator cannot accidentally expose the parent planning tree under the `designer-artifact` name. A uniform 404 on a `project_scoped` root that LOOKS well-formed is the symptom; the config is the cure.

## 3. Troubleshooting

### 3.1 `/views/livez` returns 404 in dev

The lifespan did not wire the `LiveViewsService`. Two usual suspects:
* `live_views.enabled: false` in `config.yaml` — set to `true` and restart.
* An exception during service construction (typically a misconfigured root — bad path, missing project, etc.). The daemon log at `[LiveViews] subsystem ready:` carries the resolved state; absence of the line means construction never finished.

### 3.2 `view_link` returns `Error: live-views service not initialized`

The service was never wired into the manager. This should not happen in production (the lifespan always wires it). In tests, the `InstanceManager` constructor accepts `live_views_service=None` (a typed error envelope, not a crash). The fix in production is to confirm the lifespan path is being exercised; in tests, the test fixture should pass a real service.

### 3.3 A specific root returns 404 but the file is on disk

Likely causes, in order of frequency:
1. The root is `disabled: false` (operator disabled it).
2. The file's extension is not in the root's `allowed_extensions` (uniform 404 by design — see `daemon/services/live_views.py:603-609`).
3. The file is a symlink whose target escapes the resolved root (the containment check fires; uniform 404).
4. The file's path traverses a `..` segment (the shape check fires; uniform 404).
5. The file is larger than the 32 MiB soft cap (uniform 404; the cap protects against accidentally serving a multi-GB log).
6. The root is `project_scoped` with a non-empty `required_rel_subpath` (M3 subpath gate) and the rel path does not contain the configured sequence as a contiguous subsequence. See §2.6 for the three valid shapes (`[]` = gate skipped, `[""]` = **boot-time ValidationError (M11 loud-fail-at-load)**, populated list = sliding-window segment gate). The `[""]` case is now caught by the M11 validator in `daemon/config.py:_validate_root_shape` at config load — the daemon refuses to start with a single actionable error (`required_rel_subpath contains an empty segment — the sliding-window gate can never match a real path segment, so the root would uniformly 404 every URL`), not a uniform-404 at request time. A misconfig that boots is operationally worse than one that refuses to boot, so M11 surfaces it at load with the field name and the reason. The resolver-level uniform-404 fallback (`daemon/services/live_views.py:452-467`) is now a defense-in-depth layer; the symptom it would produce (a `project_scoped` root with a `required_rel_subpath` returning 404 on EVERY URL, even the canonical example path) is only reachable if a non-M11 path sneaks a broken subpath past the validator. Cure: correct the config (use a populated list like `["design", "mockups"]` for the seeded `designer-artifact` shape) and restart.
7. The root is `designer-artifact` (REWORK 2026-10-07 M2: project-scoped) and the URL's project shortname is not a registered `Project.shortnames` entry (uniform 404; check the project's shortnames list). Or the rel path is not under `*/design/mockups/*` (M3: the resolver enforces the mockups subtree prefix; uniform 404).
8. The root is `planning` and the URL's project shortname is not a registered `Project.shortnames` entry (uniform 404; check the project's shortnames list).

### 3.4 A specific URL returns the wrong MIME

`/views/<root>/<rel>` uses an explicit extension→MIME map (see `daemon/services/live_views.py:97-119`). Anything not in the map returns `application/octet-stream`. The map is **closed** — the daemon never consults the host `mimetypes` registry (`/etc/mime.types`): a packager who maps an agent-authored extension to `text/html` would let the browser execute the result on the daemon origin. The safe default for any extension outside the closed map is `application/octet-stream` (the spec's allowed fallback). If a deployment needs a new MIME for a common artifact, add it to `_EXT_TO_MIME` and file a PR.

The exception is `/views/tmp-images/<id>` — that path uses the sidecar MIME (architect risk #7), not the extension. The blob is extensionless on disk; the sidecar is the only source of truth.

## 4. Security notes

* **Uniform 404.** Every miss is the same body, the same code, the same `X-Content-Type-Options: nosniff`. A probing client cannot distinguish "root missing" from "traversal rejected" from "path missing" — this is the model the architect set for `/api/tmp_images/<id>` and we mirror it.
* **Read-only.** No `POST` / `DELETE` / `LIST` endpoints. The FastAPI router exposes GET + HEAD only. A request with any other method on `/views/<root>/<rel>` returns `405 Method Not Allowed` (the FastAPI default) — uniform 404 is reserved for the URL-level miss case.
* **No content sniffing.** Content-Type comes from the explicit map (filesystem / project_scoped roots) or the sidecar record (tmp-images root). Never `mimetypes.guess_type` on the read bytes. Never the magic-byte sniff the image tools use.
* **Path-traversal guards.** Every `resolve_for_instance` call runs `is_well_formed_rel_path` (rejects `..`, control chars, leading `/`, backslash, percent-encoded forms after decode) and a `realpath`-based containment check against the resolved root. The `tmp-images` root applies the same `^[a-f0-9]{32}$` regex as `/api/tmp_images/<id>`.
* **No auth at the daemon.** Same shape as `/api/tmp_images/<id>` (`.agents/shared/conventions.md`). The edge proxy is the layer. The `/views/livez` operator probe is NOT a public status — it reveals the subsystem state to anyone who knows the URL; do not expose `/views/*` directly to the internet without edge auth.

## 5. Cross-references

* `daemon/services/live_views.py` — the registry + resolver (single source of truth for path-traversal guards, content-type map, URL minting).
* `daemon/routers/live_views.py` — the HTTP route family. GET + HEAD only.
* `daemon/routers/tmp_images.py:130` — the same path-traversal regex the tmp-images root applies (defense in depth — the live-views service applies the same gate).
* `daemon/tools/image_tools.py:465-480` — the project-workdir resolution pattern the `designer-artifact` root mirrors.
* `daemon/tools/live_views.py` — the `view_link` tool factory.
* `agents/designer/skills-template/design-strategy.md:71` — the design artifacts table contract (now carries a `view_url` column).
* `.agents/shared/conventions.md` — the public-by-obscurity file-serving convention.
