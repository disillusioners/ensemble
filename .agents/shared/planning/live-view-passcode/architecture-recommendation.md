# ADR — Public Passcode Access for Live Views

**Status:** DECIDED (architect) — input to plan-overview.md and the security review
**Date:** 2026-10-10-17 (architect adjudication 2026-10-17T18:2xZ)
**Commission:** `liveview-passcode-1` (worktree `feature/live-view-passcode` @ `a3e9f1bda`)
**Provenance:** Governor council `architect-council-liveview-passcode` (b2a1fa1e-7617-4dfe-bd08-f8d26843459c), 2/2 councilors completed (models `agentic`+`coding`, skill `security-design`, read-only, worktree-verified). Architect re-verified every load-bearing anchor cited below in the worktree before acceptance; two recon corrections from the leader's brief are recorded in §0.
**Deliverable sibling:** `plan-overview.md` + `phase1-5` + `decisions.md` by the planner (written in parallel; this ADR is the architecture input). This file is the ADR of record.

---

## 0. Recon corrections (verified in worktree — carried into the record)

1. `infranstructure/k8s/ensemble/httproute.yaml` is a pure catch-all: one rule, **no `matches` block** → `oauth2-proxy:4180`, `timeouts.request: 30m`, hostnames `["www.ensem.dev"]`. The `ensemble-live` Service detail lives in `endpoint.yaml`; oauth2-proxy already upstreams to it (`oauth2-proxy.yaml:27` `--upstream=http://ensemble-live.ensemble.svc.cluster.local:9797`; also `:29 --cookie-expire=24h`, `:36 --upstream-timeout=10m`).
2. Token-bucket pattern file is `daemon/sources/rate_limiter.py` (`TokenBucketLimiter` :25) — **not** `rate_limit.py` (does not exist).

## Decision Summary

Open the public lane at **`/views/public/{root_name}/{rel_path}`** — a reserved-root, three-layer-guarded sub-namespace of the existing views family. Unauthenticated flow: uniform interstitial-or-404 → same-URL POST verify (Fernet-wrapped passcode, 31-symbol alphabet, constant-time compare) → **stateless Fernet grant cookie** bound to `(root, rel, generation)`, exact-path scoped, 24h TTL → view bytes through the identical serving path. Abuse controls: per-IP token bucket (rightmost-XFF keyed, fail-open) ahead of a **durable 10-strike per-view lock, no auto-unlock, refresh-only recovery**. The canonical gate is **daemon-side in both roadmap states**: State A = one `PathPrefix /views/public` HTTPRoute rule ordered before the catch-all (pure passthrough, bypasses oauth2-proxy); State B = **code-seeded PUBLIC prefix tag** via the existing `tag_routes_by_path` machinery (NOT the `anonymous_view_paths` knob — deterministic by code, not operator config). Storage = Fernet via `SYSTEM_ENCRYPTION_KEY` (reversible-at-rest is a **forced, documented deviation** — plaintext must re-display at every mint). Audit = two tables in one migration, atomic in-transaction appends, 64-bit keyed-HMAC IP. Backfill = lazy mint on first `view_link`.

---

## D1 — Namespace: `/views/public/{root_name}/{rel_path}`, reserved root `public`, 3-layer guard

**Call: RATIFIED (leader lean a), enriched.**

- **Route layer:** public sub-routes are declared on the existing `/views` router (`daemon/routers/live_views.py:499`) **before** the parametrized `/{root_name}/{rel_path:path}` handlers (GET `:718`, HEAD `:594`) — Starlette first-match-wins is the load-bearing mount discipline already documented app-level (`daemon/api.py` views mount precedes SPA catch-all).
- **Registry layer:** `"public"` is a *legal* root name under `_ROOT_NAME_REGEX` (`daemon/services/live_views.py:126`, lowercase-only → no case-variant collision), therefore a **reserved-name guard is required, not optional**: reject `"public"` at root registration and add `RESERVED_ROOT_NAMES` consulted by `is_well_formed_root_name` (`:182`) and `has_root` (`:820`).
- **Handler layer:** the generic (non-public) handler additionally 404s `root_name == "public"` defensively.
- Uniform-404 via `_uniform_404()` (`routers/live_views.py:451-464` — same body, no path/root disclosure) for ALL public-lane misses including bare `/views/public` and `/views/public/{root}`.
- Rejected `/public/views/...` (top-level fork): buys nothing, forks the mount + uniform-404 discipline.

**Over-broad-tag safety (architect-verified):** `_template_prefix_matches` (`daemon/auth/route_meta.py:118-125`) truncates a template at its first `{` — the legacy template becomes `/views/`, which neither equals nor starts-with `/views/public`, so **the legacy family cannot be swept into a `/views/public` prefix tag**; `/views/livez` and `/views/assets/*` are likewise unaffected. The structurally-excluded over-broad exemption is still **pinned by test** (T3 below) because the matcher is shared code that can evolve.

## D2 — Existence leak: uniform 404 without a passcode row; interstitial only for minted views

**Call: uniform 404 for unminted paths (no row → one indexed SELECT on `(root_name, rel_path)` → `_uniform_404()`); interstitial renders only when a passcode row exists.**

- Zero durable rows for nonexistent views → no write amplification, no phantom-row storage-exhaustion vector, no interactive verify oracle on garbage paths.
- **Accepted residual (🟡 R3):** minted-vs-404 is a weak oracle revealing *which paths were shared* (not content) — bounded by the 29.7-bit passcode + 10-strike durable lock + path-guessing cost. Rejected interstitial-for-everything: it converts every probe into an interactive verify surface with lockout-counter obligations for rows that should not exist.
- Locked views render a locked-notice page (200, "ask for a fresh link") with **no remaining-attempt disclosure pre-lock** (never aid multi-IP grinding under the threshold). Timing not padded (🟢, residual).
- Post-lock 200 disclosure (R11) — **architect sign-off GRANTED**: an attacker who trips the lock already knows the view exists and is locked (they caused it); pre-lock boundary is intact; viewer-recovery clarity for legitimate stale-link holders outweighs the marginal leak. Condition: locked page carries no content, no remaining-attempts, no passcode-form (verify short-circuits).

## D3 — Two-state canonical gate: code-seeded PUBLIC prefix tag (NOT the `anonymous_view_paths` knob)

**Call: `tag_routes_by_path(app, path_prefix="/views/public")` at the create_app registration site.**

- The machinery exists today: `daemon/auth/route_meta.py:66-100` — `tag_routes_by_path` exists precisely for `include_router` families where route objects are not held individually; default tags PUBLIC; consumed by ordered tagged resolution **before** session validation (`daemon/middleware/auth.py:328-335`, `_resolve_tagged` `:403`).
- The requirement is explicit — *deterministic-by-code, not operator-config-dependent* — and `auth.anonymous_view_paths` (`daemon/config.py:2890-2896`, consulted at `auth.py:337-343`) is definitionally operator-config: forget-to-set kills the public lane in State B; mis-set widens the hole. **The knob ships EMPTY and stays empty** — it remains the reserved v2 `view_shares` seam and S-19 regression cage (`tests/integration/test_auth_middleware_dispatch.py:197-223`).
- The PUBLIC **exact-path** tuple (`daemon/api.py:3531-3556`) is untouched — its exact-template-only discipline protects the `/api/auth/admin/*` seam; a `/views`-scoped prefix tag carries no admin-seam risk.
- State A and State B both reduce to: *daemon enforces; fronting layer passes through*. See Deployment Note.

## D4 — Passcode storage + verify: Fernet, 31-symbol alphabet, CSPRNG, constant-time

**Call: one row per `(root_name, rel_path)` (per-file granularity — lockout of one file never takes out a root); `wrapped_passcode` Fernet via the `SecretCipher` pattern (`daemon/services/secret_service.py:274-361`), keyed `SYSTEM_ENCRYPTION_KEY`, fail-closed.**

- Alphabet `23456789abcdefghjkmnpqrstuvwxyz` (31 symbols, drops `0/O/1/i/l`); case-insensitive entry → normalize `.strip().lower()`. Entropy: 31⁶ = 887,503,681 ≈ **29.7 bits** — acceptable **only** under the bounded-online-attack model (10 durable strikes + per-IP bucket ⇒ online success ≤ 10/31⁶ ≈ 1.1×10⁻⁸ per view per generation). State this bound in the plan; if the threat model ever includes unthrottled offline comparison, the alphabet/length must grow.
- Mint RNG: `secrets.choice` (stdlib CSPRNG), never `random.*`. Verify: decrypt → `hmac.compare_digest` (fixed 6-char lengths).
- **Hash-only infeasible (Deviation V1):** binding requirement forces plaintext re-display at every `view_link` mint. Dual-store (hash+cipher) rejected — two protections, same key, zero gain.
- **Rotation coupling GROWS:** `SYSTEM_ENCRYPTION_KEY` rotation must now also re-encrypt `live_view_passcodes.wrapped_passcode` (add to the rotation ceremony alongside the `secrets` walk; decrypt failure → typed "regeneration required", never a 500).

## D5 — Lockout: durable strikes, NO auto-unlock, refresh-only recovery, grants survive

**Call: RATIFIED (leader lean d) with one sharpening — idempotent `view_link` does NOT clear an active lock.**

- Atomic `UPDATE ... SET failed_attempts = failed_attempts + 1 RETURNING` inside `engine.begin()`; the 10th failure flips `locked=true` + emits the `lockout` audit row **in the same transaction** (`_append_audit_in_conn` idiom, `secret_service.py:1015-1063`, usage `:846-864`). Locked → verify short-circuits (no compare, no increment).
- **No time-boxed auto-unlock** (spec default ratified): auto-unlock degrades the lock into a rate limit.
- `view_link` idempotent call returns current passcode + `locked: true` + refresh hint — the owner decides. `refresh=True` is the ONLY unlock: new passcode, `generation += 1`, counters reset, audit `regenerate`; the generation bump silently revokes ALL outstanding grants.
- **Valid grant cookies persist through lockout** — lockout gates the entry lane, not established grants; invalidating holders would convert lockout-DoS into viewer-DoS. Cookie expiry during lockout ⇒ fresh link required.
- Concurrent wrong-verify race at the lock boundary (R9): serialize via `SELECT … FOR UPDATE` (PG) / `BEGIN IMMEDIATE` (SQLite) on the row; test pin.

## D6 — Grant cookie: stateless Fernet, exact-path scoped, generation-bound

**Call: RATIFIED (leader lean c); `Path` = the exact public view URL (tightest scope), NOT the `/views/public` prefix.**

- Claims JSON `{root_name, rel_path, generation, exp = now + 86400}` Fernet-encrypted; `Max-Age=86400` (matches `oauth2-proxy.yaml:29 --cookie-expire=24h` house precedent); `HttpOnly; Secure; SameSite=Lax`.
- Validation chain: decrypt (`InvalidToken` → interstitial + cookie clear) → claims match THIS request's `(root, rel)` → `generation == row.generation` → `exp > now`; any failure → interstitial. Mermaid-wrapper `?raw=1` same-origin fetch is covered (cookie Path matching is query-insensitive).
- Stateless over session-table: nothing to persist/purge/GC, restart-durable, revocation already exists (generation bump), per-view audience makes a table pure operational surface. CSRF: the grant changes only the requester's own browser; SameSite=Lax contains cross-site use; **adopt** the cheap same-host Origin/Referer check on the verify POST as defense-in-depth (no CSRF token).
- **POST shape: same-URL POST** (form action = the view URL) — view identity from the path, no reflected view parameter, shares uniform-404 naturally with GET. A central `/views/public/verify` endpoint is rejected (must carry a view identifier parameter). **This is the only POST route in the entire `/views` surface — pin it.**
- Plain-HTTP edge: `Secure` blocks cookie storage on direct `http://LAN:9797` → render an explicit "secure context required" message, never a redirect loop.

## D7 — Rate limiting: per-IP token bucket ahead of strikes, fail-open, rightmost-XFF keying

**Call: RATIFIED (leader lean e); keying = rightmost XFF entry else socket peer (corrects the naive leftmost choice).**

- Adapt `TokenBucketLimiter` (`daemon/sources/rate_limiter.py:25`) into a per-IP map, LRU cap ~50k + idle eviction. Exact params (≈2 rps / burst 5 vs burst 10 / refill 1·min⁻¹) are planner-tunables.
- **Rightmost XFF:** behind a single trusted Envoy hop the rightmost entry is the address **appended by our proxy** = the real client; leftmost entries are client-supplied and spoofable (rotation voids the limiter). Rightmost dominates in every topology: proxy appends → rightmost trustworthy; proxy overwrites → single entry (leftmost == rightmost); unknown topology → degrades to a shared bucket = over-throttle, never bypass. **Runbook must pin Envoy XFF append/overwrite behavior (Deviation V4).**
- **Binding pin tests** (the requirement, independent of params): (a) 429 → NO strike increment; (b) successful verify → touches neither bucket nor strikes; (c) wrong passcode → bucket AND strike both.
- Fail-open on limiter-internal error (loud log) — the durable strike lock is the backstop; a limiter bug must not brick the lane. In-memory caveat: restart clears buckets (one fresh burst per attacker), strikes survive; multi-VM shared store explicitly out of scope (flagged).

## D8 — State A proxy: ONE added rule, FIRST, `PathPrefix /views/public` → `ensemble-live:9797`

**Call: RATIFIED — the HTTPRoute edit is the only delta.**

```yaml
# ensemble/httproute.yaml — add ABOVE the existing no-matches catch-all rule:
rules:
  - matches:
      - path: { type: PathPrefix, value: /views/public }
    backendRefs:
      - { name: ensemble-live, port: 9797 }
    timeouts: { request: 5m }   # small-file serving; 30m catch-all is the SSE budget — planner's pick
  # ... existing catch-all (oauth2-proxy:4180, 30m) unchanged below
```

- Gateway API evaluates rules in order — the same mechanism the house bypass precedent relies on (`llm.ensem.dev-httproute.yaml:18-66`). Service `ensemble-live` already exists and already backs oauth2-proxy's upstream — no new infra.
- Rollout: `kubectl apply` (no pod restarts); rollback: git revert + apply. Smoke: unauthenticated public URL → interstitial; `/views/<non-public>` → still Casdoor redirect.
- Not-coupling: pure passthrough; the canonical gate is entirely daemon-side (D3) and identical whether the front door is Envoy+oauth2-proxy, in-house OIDC, or direct. **Failure modes:** rule missing → public URLs fail CLOSED into Casdoor login (availability loss only); rule overbroad (`/views`) → strips OAuth from passcode-less views with no compensating control → **release blocker** (R2).

## D9 — Audit: one migration file, two tables, keyed-HMAC IP, atomic appends

**Call: `YYYYMMDD_HHMMSS_create_live_view_passcode_tables.sql` grouping both tables (single-event multi-table precedent: `20261017_000001_create_secrets_tables.sql`). Names `live_view_passcodes` + `live_view_passcode_audit` (family-consistent; `view_passcodes*` equally valid — planner's pick).**

- Passcode row: `id, root_name, rel_path UNIQUE, wrapped_passcode BYTEA, generation INT DEFAULT 1, failed_attempts INT DEFAULT 0, locked BOOL DEFAULT false, created_at, last_attempt_at` (+ partial index `WHERE locked`).
- Audit row: `id, at, action, root_name, rel_path, generation, ip_hmac NULLable, actor NULLable` (privileged mint/regenerate carry the calling agent; public actions NULL). **`action` = TEXT, not a DB enum** — additive emitters never require a migration.
- **Minimal closed action set `{mint, regenerate, grant, deny, lockout}`** — per-request `cookie_invalid`/`grant_expired` are log-only (write-amplification); idempotent re-display is not audited (travels in the privileged transcript).
- **IP = keyed HMAC, 64 bits:** `HMAC-SHA256(ip, key)[:16 hex]`. Key = domain-separated derivation from `SYSTEM_ENCRYPTION_KEY` (`HKDF(..., info="live-view-audit-ip-v1")` or `SHA256(SEK || "live-view-audit-ip-v1")`) — **never** the raw Fernet key as HMAC key. Rejected: truncation (leaks /24 topology), omit (kills lockout-storm forensics), plaintext.
- Rotation caveat: rotation changes the derived key → historical `ip_hmac` rows remain but cross-period IP correlation breaks (raw IPs never stored; re-computation impossible) — extend the rotation ceremony note (with the D4 `wrapped_passcode` walk).

## D10 — `view_link` result: stays `str`, enriched; always-passcode; category unchanged

**Call:** returns `{public_url, passcode, authenticated_url, generation, locked}` (+ "pass refresh=True" hint when locked); serialization (line-block vs JSON-string) = planner's pick; the `-> str` tool contract (`daemon/tools/live_views.py:97`) and privileged category `"view-views"` (ari/leader/designer) are unchanged — no privilege expansion. **Always-passcode holds in all three cases** (first mint / idempotent / locked — the owner must see why refresh is needed).

**Doc debt (schedule before merge):** the tool docstring "Read-only — no filesystem or database side effects" (`tools/live_views.py:63-64`) becomes FALSE (minting is a DB write); the router family docstring "no POST routes" (`routers/live_views.py:13-18`) is amended for the public sub-namespace; lockstep grep across `agents/*/` for URL-shape assumptions in seal-pinned prompts, re-pin explicitly (R7).

## D11 — Backfill + posture: LAZY mint; zero-JS daemon-served interstitial

**Call: lazy mint on first `view_link`** — keeps the migration pure DDL; eager backfill would mint public URLs for files never shared (privacy surprise). Idempotency + `refresh=True` covers "legacy views = simple backfill".

- Interstitial: standalone daemon-served HTML (SPA has no `/views` routes; mount precedes SPA catch-all), mobile viewport (mermaid-wrapper precedent `daemon/services/live_views.py:1707-1810`), **zero JavaScript**. CSP: `default-src 'none'; script-src 'none'; style-src 'nonce-…'`; `form-action 'self'; base-uri 'none'; frame-ancestors 'none'`; `X-Content-Type-Options: nosniff`; `Cache-Control: no-store`; `Referrer-Policy: no-referrer`. Form: same-URL POST, `autocomplete=off`, **no reflection** of the attempted code; `html.escape` any echo.
- **Public asset alias** `/views/public/assets/mermaid/mermaid.min.js` → the SAME vendored bundle under the existing `_ASSET_HEADERS` lockdown (`routers/live_views.py:115-118`), no gate — do NOT widen the exemption to `/views/assets`. Markdown wrapper's SRI-pinned CDN needs nothing.
- Logging hygiene: NEVER log passcodes, grant tokens, or ciphertexts; access logs carry path + `ip_hmac` + status only; wrong-passcode lines carry the counter snapshot, never the submitted value (mirror `SelectiveAccessLogMiddleware` patterns, `daemon/api.py:3666-3683`).
- Serving path inherits realpath containment + 32 MiB cap + `O_NOFOLLOW` fd-read TOCTOU containment (`daemon/services/live_views.py:1095-1127`, `:137`; `routers/live_views.py:66-78`) — the grant cookie is never consulted by `AuthMiddleware`; no path from grant to session.

---

## 📌 DEPLOYMENT NOTE — the exact namespace that must stay public across State A → B

> **The public namespace is exactly `PathPrefix /views/public` — nothing broader, ever.**
>
> - **State A (today):** `ensemble/httproute.yaml` (hostnames `www.ensem.dev`) gains ONE rule, ordered FIRST: `PathPrefix /views/public` → Service `ensemble-live:9797`, bypassing oauth2-proxy. Everything under the prefix rides it: interstitial, verify POST, view bytes, `?raw=1`, and the `/views/public/assets/mermaid/mermaid.min.js` alias. **Never** exempt `/views` (whole family) or `/api` — non-public views have no passcode; OAuth is their only guard.
> - **State B (user-driven manual middle-server drop):** the in-house OAuth2 `AuthMiddleware` (auth-ON, `daemon/config.py:2774`) keeps `/views/public/*` exempt via the **code-seeded public prefix tag (D3)** — `anonymous_view_paths` is NOT the mechanism and must remain empty. No operator action required; no proxy coupling to unwind.
> - **Invariant:** regardless of fronting layer (Envoy+oauth2-proxy / in-house OIDC / direct), the canonical gate for `/views/public/*` is the daemon: bucket + strike lock + Fernet verify + grant cookie + audit. Proxies are pure passthrough. Any future proposal to re-authenticate or broaden this prefix at a proxy must revisit the canonical-gate contract first.

## Deviations from user spec (explicit, all six)

| # | Deviation | Reason |
|---|---|---|
| V1 | Reversible Fernet at rest, not hash-only | Binding "always re-display at mint"; same ratified class as agent-secrets plaintext-at-mint (2026-10-17) |
| V2 | POST enters the `/views` family (public sub-namespace only) | Forced by "never code-in-URL"; authenticated family stays GET/HEAD byte-identical; docstring amended |
| V3 | Locked-page HTTP 200 with notice, not 423/404 | Browser UX; pre-lock boundary stays uniform 404; architect sign-off granted (D2) |
| V4 | XFF single-hop trust (rightmost entry) for limiter keying | Rightmost = proxy-appended real client; runbook pins Envoy XFF behavior |
| V5 | Idempotent `view_link` does not clear locks; valid grants persist through lockout | Availability + owner agency rulings |
| V6 | Rate-limiter fail-open | Durable strike lock is the backstop; limiter bug must not brick the lane |

## Binding test pins (minimum set)

1. **T1 route order/guard:** `/views/public/{root}/{rel}` hits the public lane; `/views/{root}/{rel}` byte-identical to today (GET/HEAD only); bare `/views/public`, `/views/public/{root}` → uniform 404; root named `public` rejected at registration (3 layers).
2. **T2 auth-ON exemption (State B):** `/views/public/*` passes `AuthMiddleware` with the code-seeded tag; `/views/{root}/{rel}` (non-public) stays **default-deny**; `/views/livez`, `/views/assets/*` unaffected; `anonymous_view_paths` empty in every test (extend `TestS19AnonymousViewPaths`, `tests/integration/test_auth_middleware_dispatch.py:197-223`).
3. **T3 over-broad-tag exclusion:** the legacy parametrized template is NOT matched by the `/views/public` prefix tag (matcher-semantics pin against `route_meta.py` evolution).
4. **T4 lockout:** 10th wrong verify flips `locked` + `lockout` audit atomically; locked verify short-circuits; no auto-unlock after any TTL; `refresh=True` → new passcode + `generation+1` + counters reset + old grant cookie rejected; concurrent 10th/11th race serialized (R9).
5. **T5 rate-limit interplay:** 429 → no strike; success → neither bucket nor strike; wrong → both.
6. **T6 cookie:** exact-path scope; decrypt-fail → interstitial; generation mismatch → interstitial; Secure-flag plain-HTTP message path.
7. **T7 verify shape:** unminted path → uniform 404 with NO row created (write-amplification pin); locked page has no content/no attempts disclosure.
8. **T8 view_link:** always-passcode across first-mint / idempotent / locked; docstring + family docstring updated; no passcode/token in any log line.

## Risk register (council-merged, architect-adopted)

| ID | Risk | Sev | Handling |
|---|---|---|---|
| R1 | First unauthenticated surface = weakest link | 🔴 design-contained | Three independent controls (bucket + durable lock + uniform-404); no design-inherent 🔴 |
| R2 | Operational over-broad exemption (`/views`, `/api`) | 🔴 | Exact-prefix rule + this deployment note; any widening diff = release blocker; T2/T3 pins |
| R3 | Minted-vs-404 existence oracle | 🟡 accepted residual | Low value behind passcode + strike lock |
| R4 | Lockout-DoS inherent to 10-strike spec | 🟡 | Bucket blunts; `refresh` recovers; audit visibility; grants survive |
| R5 | `SYSTEM_ENCRYPTION_KEY` rotation coupling grows | 🟡 | `wrapped_passcode` re-encrypt walk + `ip_hmac` unlinkability note in the ceremony |
| R6 | In-memory limiter reset on restart; XFF topology change | 🟡 | Durable strikes backstop; single-VM assumption; revisit on multi-VM |
| R7 | `view_link` shape change breaks seal-pinned prompts | 🟡 | Lockstep grep + explicit re-pin before merge |
| R8 | Mermaid asset outside exempt prefix → dead-load | 🟡 | Public asset alias (D11), same vendoring pin |
| R9 | Concurrent wrong-verify race at lock boundary | 🟡 | Row serialization + T4 pin |
| R10 | Interstitial reflected-content XSS | 🟢 | Zero-JS, CSP `default-src 'none'`, no reflection, escaped echo |
| R11 | Locked-page existence disclosure (post-lock 200) | 🟢 signed off | D2 ruling; pre-lock boundary intact |
| R12 | Fail-open limiter during hiccup | 🟢 | Durable strike lock backstop |

## Attack surface (complete enumeration)

1. `GET/HEAD /views/public/{root}/{rel}` (+`?raw=1`) — cookie gate; serving path identical to authenticated family.
2. `POST /views/public/{root}/{rel}` — verify: bucket → 404 (unminted) → locked short-circuit → constant-time compare → strike++ / grant. **Only POST in the `/views` surface.**
3. `GET /views/public/assets/mermaid/mermaid.min.js` — static vendored alias, asset-header lockdown, no gate.
4. Cookie parse/verify surface — Fernet + claims checks; all failures collapse to interstitial.
5. `live_view_passcodes` / `live_view_passcode_audit` — ciphertext + counters only; no plaintext-at-rest column.
6. `view_link(refresh=…)` — privileged, lazy-mints.
7. State A HTTPRoute rule + State B code-seeded prefix tag — the only two places the exemption exists; both named exactly (Deployment Note).

Unchanged: `/views/{root}/{rel}` family (GET/HEAD byte-identical), `/views/livez`, legacy `/views/assets/...`, all `/api/*`.

## Governor-ruling adjudication (architect — final)

All five governor rulings **RATIFIED** after independent verification: (1) code-seeded tag over the knob — requirement text decisive, machinery verified `route_meta.py:66-100`; (2) rightmost-XFF — dominates in every topology; (3) exact-path cookie scope; (4) same-URL POST; (5) 5m route timeout (30m acceptable). Audit action set: merged-minimal (TEXT column). R11 merge-gate sign-off: **granted** (D2). No council position required override.

## Decisions pending (planner / leader tunables — none blocking the architecture)

- Exact bucket params (pin tests T5 are the binding part); interstitial visual design (CSP contract is binding).
- Audit table naming (`live_view_passcodes*` vs `view_passcodes*`); `view_link` serialization shape.
- Proxy rule timeout 5m vs 30m; Envoy XFF append/overwrite runbook pin (V4).
- Multi-VM shared limiter store — deferred, out of scope.

## Confidence

**High.** Every load-bearing anchor re-verified in the worktree by the architect (route/tag machinery, matcher semantics, router/service/tools line anchors, proxy configs, test precedent). The assumption that would flip the recommendation: a threat model requiring unthrottled offline comparison (would force alphabet/length growth, D4) or a future multi-VM deployment (would force a shared limiter store, D7).
