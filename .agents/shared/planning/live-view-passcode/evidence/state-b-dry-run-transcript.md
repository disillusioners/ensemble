# T3.3(iii) State B dry-run — DRILL TRANSCRIPT (archived 2026-10-17T23:52:12Z)

- Worktree: /home/nea/ensemble-src-wt-liveview-passcode @ b683a545eb91f613fe1cac77d7fa6a0f728bc4e5 (branch feature/live-view-passcode)
- Mode: auth-ON (ENSEMBLE_AUTH_ENABLED=true, placeholder OIDC), anonymous_view_paths LEFT EMPTY (unset — the code-seeded prefix tag is the only exemption carrier)
- Fence: mktemp /tmp/liveview-drill.* (data dir + views root), drill-only SYSTEM_ENCRYPTION_KEY, dev PG ensemble_dev @ 127.0.0.1:5432, port 8079 (checked free), bounded boot, clean TERM shutdown
- Script: state-b-dry-run.sh (this directory); boot log: state-b-boot.log
- RESULT: ALL PROBES PASS — no daemon defect surfaced; ZERO daemon code changes in P3

Note: probe (c) returns the interstitial because the (drillroot, hello.md) row persisted from earlier drill runs in the shared dev DB; probe (c2) uses a guaranteed-unminted path and demonstrates the uniform-404 daemon answer (proof AuthMiddleware passed the request through — an unauthenticated middleware block would be a 302 to /api/auth/login, as probes j/k show for non-exempt paths).

## Boot-log corroboration (state-b-boot.log)

- `[Auth] derived public_path_set (18): [... '/views/livez', '/views/public/assets/mermaid/mermaid.min.js', '/views/public/{root_name}/{rel_path:path}', '/vscode']` — the AuthMiddleware's own boot derivation shows the code-seeded prefix tag landed on the public sub-routes (the `tag_routes_by_path(app, path_prefix="/views/public")` registration at daemon/api.py:4205).
- `[AuthAudit] event=request_denied_302 path=/views/drillroot/hello.md method=GET return_to=/views/drillroot/hello.md` — the direct lane default-denies with the in-house login redirect.
- `[LiveViews] passcode substrate ready: strike_ceiling=10 grant_ttl=86400s rate_limit=5/5-per-min` — the P2 substrate wired (fresh drill key accepted).
- `[OAuthClient] discovery attempt N failed: transport error: ConnectError` (WARN-not-fatal) — expected: placeholder OIDC issuer `https://drill.invalid`; discovery rides the background sweeper (deferred fetch, boot never blocks on it).

```
== drill worktree: /home/nea/ensemble-src-wt-liveview-passcode
== fenced env: data_dir=/tmp/liveview-drill.IMzYv2/data port=8079 views_root=/tmp/liveview-drill.IMzYv2/views
== auth: ENSEMBLE_AUTH_ENABLED=true anonymous_view_paths=<unset>
== port 8079 free
== booting daemon pid=3728417 (log: state-b-boot.log)
== daemon UP (waited 19s)

--- PROBE: a) GET /views/livez (PUBLIC exact tag — unaffected)
HTTP/1.1 200 OK
content-type: application/json
    body-bytes: 52 first-line: {"status":"ok","enabled":true,"roots":["drillroot"]}

--- PROBE: b) GET /views/public/assets/mermaid/mermaid.min.js (gate-free alias)
HTTP/1.1 200 OK
content-type: application/javascript; charset=utf-8
    body-bytes: 5493176 first-line: "use strict";var __esbuild_esm_mermaid_nm;(__esbuild_esm_mermaid_nm||={}).mermaid=(()=>{var iSn=Object.create;var nee=Ob

--- PROBE: c) GET /views/public/drillroot/hello.md UNMINTED (expect uniform 404 FROM DAEMON, NOT a login redirect)
HTTP/1.1 200 OK
content-type: text/html; charset=utf-8
    body-bytes: 1771 first-line: <!DOCTYPE html> <html lang="en"> <head>   <meta charset="utf-8">   <meta name="viewport" content="width=device-width, in

--- PROBE: c2) GET /views/public/drillroot/absent-file.md GUARANTEED-UNMINTED (expect uniform 404 from daemon — the row-gating posture)
HTTP/1.1 404 Not Found
content-type: application/json
    body-bytes: 26 first-line: {"error":"view not found"}
/home/nea/ensemble-src-wt-liveview-passcode/.venv/lib/python3.14/site-packages/langchain_core/_api/deprecation.py:25: UserWarning: Core Pydantic V1 functionality isn't compatible with Python 3.14 or greater.
  from pydantic.v1.fields import FieldInfo as FieldInfoV1
== mint -> ViewPasscodeRotationRequired; regenerating via refresh (documented recovery)
MINT passcode=q38eae generation=4 created=False locked=False

--- PROBE: e) GET /views/public/drillroot/hello.md (interstitial after mint)
HTTP/1.1 200 OK
content-type: text/html; charset=utf-8
    body-bytes: 1771 first-line: <!DOCTYPE html> <html lang="en"> <head>   <meta charset="utf-8">   <meta name="viewport" content="width=device-width, in

--- PROBE: f) POST verify wrong code + SAME-HOST Origin (must NOT 403 — proceeds to compare/deny)
HTTP/1.1 200 OK
content-type: text/html; charset=utf-8
    body-bytes: 1822 first-line: <!DOCTYPE html> <html lang="en"> <head>   <meta charset="utf-8">   <meta name="viewport" content="width=device-width, in

--- PROBE: g) POST verify + CROSS-HOST Origin (MUST 403 — Host-preservation assertion counterpart)
HTTP/1.1 403 Forbidden
content-type: application/json
    body-bytes: 21 first-line: {"error":"forbidden"}

--- PROBE: h-plain) POST verify CORRECT code over PLAIN HTTP (expect the secure-context page — D6:79/S11 — NO cookie, NO redirect loop)
HTTP/1.1 200 OK
content-type: text/html; charset=utf-8
    body-bytes: 1398 first-line: <!DOCTYPE html> <html lang="en"> <head>   <meta charset="utf-8">   <meta name="viewport" content="width=device-width, in
    secure-context page copy present? 2

--- PROBE: h-https) POST verify CORRECT code + X-Forwarded-Proto: https (edge-emulated; expect 303 PRG + Set-Cookie view_grant)
HTTP/1.1 303 See Other
location: /views/public/drillroot/hello.md
set-cookie: view_grant=gAAAAABq1AoO2kKbTOMgi-vcZgMSx7XYvMSKDVd77PrMv4nLbnr5Kb6sToo3J_qhvnLtHfBL-ivgebUzSbNsYuxDAkpD_AXo2lIS2CWbBkIRbyy2WZ2nol4TI76fbCBxNjl_e0hkQsGkWXteuhFaPsnS9NZuIIbB3h6P69f9Ppqsl4m6iXo59nOzrThRI1xytDu20e9bUrUs; Path=/views/public/drillroot/hello.md; HttpOnly; Secure; SameSite=Lax; Max-Age=86400
    body-bytes: 0 first-line: 
    captured cookie: view_grant=gAAAAABq1AoO2...(len 215)

--- PROBE: i2) GET with captured grant cookie (expect view bytes through the gate)
HTTP/1.1 200 OK
content-type: text/html; charset=utf-8
    body-bytes: 3340 first-line: <!DOCTYPE html> <html lang="en"> <head> <meta charset="utf-8"> <title>hello.md - live view</title> <meta name="viewport"

--- PROBE: j) GET /views/drillroot/hello.md DIRECT LANE unauthenticated (expect 302 to /api/auth/login)
HTTP/1.1 302 Found
content-type: text/plain; charset=utf-8
location: /api/auth/login?return_to=/views/drillroot/hello.md
    body-bytes: 0 first-line: 

--- PROBE: k) GET /api/instances (expect 302/401 — still guarded)
HTTP/1.1 302 Found
content-type: text/plain; charset=utf-8
location: /api/auth/login?return_to=/api/instances
    body-bytes: 0 first-line: 

== shutting down (TERM → wait → KILL fallback)
== daemon stopped; drill complete
```
