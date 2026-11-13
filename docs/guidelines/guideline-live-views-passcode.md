# guideline-live-views-passcode

**Status:** draft · **Feature:** live-views passcode public lane + session-bypass origin gate. Base lane (`feature/live-view-passcode`, merge `09d4c9490`, migration `20261017_193816_create_live_view_passcode_tables.sql`) recorded under CHANGELOG **`[0.22.0]` — 2026-10-18**; the `view_write`/`board_link` Phase-3 surface is CHANGELOG **`[0.26.0]`**; the session-bypass origin-gate rewrite at **v0.29.7** (tag: "views-session-bypass lane: loop-2 closure; both bypass classes dead (re-kill 3/3); no third class found"). · **Audience:** operator / agent

## 1. Purpose & doctrine

Narrow unauthenticated public sharing of live views via `/views/public/{root}/{rel}`, canonical passcode gate **daemon-side**, proxies **pure passthrough**. The one-line contract (runbook): "The public namespace is **exactly `PathPrefix /views/public`** — nothing narrower and nothing wider, in BOTH deployment states. … Never exempt `/views` or `/api`."

## 2. States

- **State A** (oauth2-proxy front): the exemption is ONE added HTTPRoute rule — `PathPrefix /views/public` → `ensemble-live:9797`, timeout 5m, ordered BEFORE the catch-all. Manifest lives in the **data-center-scripts** repo, branch `feature/ensemble-views-public-exemption`, commit `e198005816…` — **State A = merge + apply at the promote ceremony** (`git merge --no-ff` FIRST, then `kubectl diff` + `kubectl apply`; apply reads the working tree, so skipping the merge applies a tree without the rule).
- **State B** (in-house OAuth2/OIDC module): same namespace exempt — **zero operator action**; the module honors the exemption by construction.
- The OAuth-authenticated `/views/*` direct lane is unchanged in both states.

## 3. EXACT enablement steps (State A ceremony — do not reorder)

1. **Pin Envoy's ACTUAL `X-Forwarded-Host` output** on the `/views/public` rule — exact spelling AND port form (runbook §3.4 drill **H2**).
2. **Declare the front** to match that output exactly (`auth.allowed_origins` / `auth.external_redirect_base` / `live_views.external_base_url`).
3. **Re-run the ceremony probe** (drill **H**); archive the output.
4. Only then treat the front as declared. Leaving it UNDECLARED is safe and is the recorded posture: undeclared forwarded headers are inert — they can neither anchor nor veto.

## 4. The rules that bite

- **A front declared with mismatched XFH 403s the lane WHOLESALE** — every browser form POST fails while the interstitial still renders. Remedy = declaration matching the front, never a client-side workaround.
- **The origin gate is the VETO model** (`_same_host_origin_check`, `daemon/routers/live_views.py:1378`; the session-bypass rewrite — dated **2026-11-08** by the in-code docstring (`live_views.py:1382`, "REWRITE (views-session-bypass 2026-11-08)", commit `6398a29a3` 2026-11-08 23:24Z) and **2026-11-09** by the runbook §1.1 heading and the v0.29.7 tag date; both refer to the same rewrite). Anchors from OPERATOR CONFIG ONLY (operator audience ∪ declared bases ∪ the peer-gated exact-loopback carve: exact `localhost`/`127.0.0.1`/`::1` spelling AND loopback TCP peer). The raw `Host` header is NEVER an unconditional anchor — that shape was the G2.7 DNS-rebinding hole (attacker-controlled Host + Origin self-match minted a full view grant). Forwarded values (first `X-Forwarded-Host` / first RFC 7239 `host=`) **VETO, never vouch**. `Origin` absent or `null` ⇒ allow — unless the browser attests `Sec-Fetch-Site: cross-site` (browser-set, JS-unforgeable).
- **Session-bypass:** a valid OAuth session cookie short-circuits the passcode interstitial (`resolve_views_session`, `daemon/routers/live_views.py:1776`); the anonymous fallback IS the contract (probe failure ⇒ anonymous, never an error — the fail-toward-anonymous binding contract: the probe NEVER produces a forbidden, a 401, or an error page). Session touch is minute-batched (≤1 write/min/session, `TOUCH_MIN_INTERVAL_SECONDS = 60`); with `sliding_renewal=true` operator view-browsing deliberately extends the login.
- **Human pages are always HTML — never JSON**; the interstitial is a static `Content-Security-Policy: default-src 'none'` document. Plain-HTTP edge: `Secure` blocks cookie storage ⇒ the explicit **"Secure connection required"** page renders (no redirect loop). The gate leans on the `Secure` cookie attribute — **not HSTS**: no `Strict-Transport-Security` header is emitted anywhere in `daemon/` (grep-verified). UNVERIFIED: the commission's ".cc-not-HSTS note" — no in-repo note under that name was found; treat HSTS-absence as the verified core.
- **An undeclared extra vhost ⇒ 403 with ZERO passcode strikes** (the origin gate precedes form parsing and the constant-time compare); it burns one rate-limiter slot only — it can never lock a shared view.
- Percent-escape handling: the URL framework DECODES percent-escapes BEFORE the path validator runs — the DECODED form is what `is_well_formed_rel_path` checks (`daemon/services/live_views.py:269-270`, `:298-301`); there is no separate `%`-character rejection in the tool (the earlier CHANGELOG Phase-3 prose that says "`%` is rejected in the tool's own validation" does not match the code — quoted here as prose, not behavior). A passcode row is minted only AFTER a successful page write (`daemon/tools/view_write.py:29` "BEFORE the passcode mint") — a failed write can never orphan a row.
- Rate limiting (ADR D7): per-IP token bucket AHEAD of strikes, fail-open on limiter-internal error (the durable strike lock is the backstop), rightmost-XFF keying; 429 consumes NO strike; a wrong code consumes bucket AND strike.

## 5. Verification & probe

Runbook drills: **H2** (pin XFH), **H** (ceremony probe: vouched same-origin Origin reaches the passcode compare; unvouched `http://evil.example` Origin ⇒ 403), the T3.3 XFF 3-outcome drill, the Host-preservation assertion. Tools: `view_write` returns the passcode-gated public URL, the passcode, the authenticated URL, the generation, the locked flag (pages over the **32 MiB** served cap refused BEFORE any write); `board_link` mints the passcode for the LIVE board (`/views/public/board/index.html` — rendered FRESH from the DB every request). An unrun drill means the two-state exemption is UNVERIFIED for that deployment — do not rely on the lane until the drills are archived.

## 6. Evidence discipline

Passcodes: fixed 6 characters drawn from a 31-symbol unambiguous alphabet that drops `0/O/1/i/l` (`LIVE_VIEW_PASSCODE_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"`, `daemon/repositories/view_passcodes/models.py:459`; 31^6 ≈ 8.9e8 ≈ 29.7 bits); Fernet `wrapped_passcode` via `SYSTEM_ENCRYPTION_KEY` (NO plaintext at rest); UUID4 immutable handles; `UNIQUE(root_name, rel_path)`; append-only `live_view_passcode_audit` with `ip_hmac` (raw IPs NEVER stored; domain-separated derived key — rotation breaks cross-period correlation). Wrong codes count durably: **10-strike ROW-level lockout** surviving restart (locking one file never takes out a root). `view_link` ALWAYS returns the passcode; minting idempotent per `(root_name, rel_path)` (first-store-wins; re-mint never clears an active lock). `board_link(refresh=True)` is the ONLY recovery after lockout. Grant cookie (ADR D6): stateless Fernet claims `{root_name, rel_path, generation, exp}`, Max-Age 86400, `HttpOnly; Secure; SameSite=Lax`, Path = the exact view URL; any validation failure ⇒ interstitial + cookie clear, NEVER a 500 — including under `SYSTEM_ENCRYPTION_KEY` rotation (`ViewPasscodeRotationRequired` fail-closed decrypt; the re-encrypt walk belongs to the standing rotation ceremony).

## 7. Rollback

No schema coupling beyond the additive passcode tables (v0.26-era migration). Proxy rollback = removing the ONE HTTPRoute rule (State A) or the module-level exemption (State B); the daemon-side gate is version-independent. `SYSTEM_ENCRYPTION_KEY` rotation orphans outstanding grant cookies + `wrapped_passcode` ciphertext until the re-encrypt walk runs (fail-closed interstitials, never 500s, in the window).

## 8. First real use

Public passcode sharing of board/report views in the live deployment; the State A merge+apply ceremony is staged against the user's promote ceremony (commit `e198005816…` merged only then). Broader first-use record — UNVERIFIED in the sources consulted.

## 9. Status

**Status: draft** (2026-11-13); verified against the v0.30.0-era tree (origin/latest @ `ee2b46d3b`). Gaps: the ".cc-not-HSTS" note and the broader first-use record are UNVERIFIED inline (§4, §8).

## 10. Source-of-truth pointers

`docs/runbooks/live-view-passcode-deployment.md` (§0 namespace line, §1.1 veto model, §1.2 verbatim YAML diff, §1.3 ceremony); `.agents/shared/planning/live-view-passcode/architecture-recommendation.md` (D6 grant cookie, D7 rate limit, D8 State A; AMENDED 2026-11-09 veto model); `.agents/shared/planning/live-view-passcode/evidence/views-session-bypass/` (closure evidence + canary-asserted mutation matrix); `docs/runbooks/live-views.md` §2.4/§6 (host-header caveat). CHANGELOG: the base passcode-lane entry sits under **`[0.22.0]` — 2026-10-18**; the Phase-3 `view_write`/`board_link` surface under **`[0.26.0]`**; **the v0.29.7 closure has NO CHANGELOG section** — its tag annotation is the release record.
