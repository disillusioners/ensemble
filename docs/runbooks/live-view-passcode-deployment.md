# Live-View Passcode — Deployment Two-State Runbook (State A / State B)

**Commission:** *public passcode access for live views* — Phase 3 deliverable (D18,
`phase3-plan.md` T3.1). **ADR of record:** `.agents/shared/planning/live-view-passcode/architecture-recommendation.md`
(D3 exemption mechanism, D8 timeouts/rollout, V4 XFF, R2 over-broad = release blocker).

**The one-line contract this runbook exists to protect:**

> The public namespace is **exactly `PathPrefix /views/public`** — nothing narrower
> and nothing wider, in BOTH deployment states. The canonical passcode gate is
> **daemon-side**; proxies are **pure passthrough**. Never exempt `/views` or `/api`.

---

## 0. THE namespace line

`PathPrefix /views/public` is the ONLY thing that must stay public across the
State A → State B migration.

What rides under the prefix (all of it, nothing else):

- the interstitial (`GET /views/public/<root>/<rel>`),
- the verify POST (same-URL POST — the only POST route in the entire `/views` surface),
- the view bytes + `?raw=1` mermaid-wrapper fetch,
- the gate-free mermaid asset alias `/views/public/assets/mermaid/mermaid.min.js`.

What must NEVER ride it: `/views/<root>/<rel>` (the direct lane — OAuth-guarded, no
passcode), `/api/**` (the daemon API), `/views/livez` and `/views/assets/**` (already
PUBLIC exact-tagged in code — they need no proxy exemption).

The prefix is a **frozen code constant**, not config: `PUBLIC_NAMESPACE = "/views/public"`
(`daemon/services/live_views.py:155`), with `public` a reserved root name
(`RESERVED_ROOT_NAMES`, guarded at config validation + service + route layers). The
daemon and the proxy YAML must agree byte-for-byte (D11/D15).

**Invariant (ADR §📌 DEPLOYMENT NOTE):** regardless of fronting layer — Envoy +
oauth2-proxy (State A), in-house OAuth2/OIDC module (State B), or direct — the gate for
`/views/public/*` is the daemon: per-IP token bucket → 10-strike durable lock → Fernet
verify → grant cookie → audit. Any proposal to re-authenticate or broaden this prefix
at a proxy must revisit the canonical-gate contract first.

---

## 1. State A — external middle OAuth2 door (SHIP-TIME)

Today's topology: k8s Envoy Gateway + oauth2-proxy v7.6.0 (Casdoor OIDC) at
`door.ensem.dev` in front of `www.ensem.dev`. The daemon sits behind
Service `ensemble-live:9797`; oauth2-proxy already upstreams to it
(`oauth2-proxy.yaml:27`). The State A change is **ONE HTTPRoute rule, ordered FIRST**
(before the catch-all), routing `/views/public` **direct-to-backend**, bypassing
oauth2-proxy. Gateway API HTTPRoute rules are first-match-wins. House precedent:
the per-path `/v1` bypass in `infranstructure/k8s/llmproxy/rancher-local-ensem-dev/llm.ensem.dev-httproute.yaml:20-36`
(data-center-scripts repo).

### 1.1 State A Host-preservation (REQUIRED PROPERTY — T3.1-HOST)

The verify POST runs a same-host Origin/Referer check
(`_same_host_origin_check`, `daemon/routers/live_views.py:721`): it compares the
`Origin`/`Referer` **host** against the inbound **`Host` header**; a mismatch 403s
(headers present only — absent headers are not a block, ADR D6:77).

**Consequence:** the Envoy Gateway **MUST preserve the client-visible host** as the
`Host` header reaching the daemon (`www.ensem.dev`) — no internal-name rewrite
(e.g. `ensemble-live.ensemble.svc.cluster.local:9797` or pod IP). A Host rewrite makes
every legit verify POST cross-host → **every legit verify 403s**.

> ⚠️ **This property is NOT stated by Envoy Gateway defaults in the operator notes**
> (`state-a-proxy-apply.md` does not pin Envoy's Host behavior) — it is therefore a
> **REQUIRED property with an explicit ceremony-time verification step** (§3 drill
> step H), not an assumed silent default. Envoy Gateway's HTTPRoute handling
> traditionally preserves the original Host, but **verify, don't assume**. If the
> ceremony check fails, fix at the proxy (preserve Host on the `/views/public` rule)
> — never by loosening the daemon-side check.

The same requirement carries into State B (§2.2): the in-house auth module must
equally preserve Host.

### 1.2 The exact YAML diff (D16 — folded VERBATIM from the operator notes)

Manifest: `infranstructure/k8s/ensemble/httproute.yaml` in the **data-center-scripts**
repo (`/home/nea/Code/data-center-scripts`, branch
`feature/ensemble-views-public-exemption`, commit `e198005816015ba82938e71d49dfc2d4614d77be`,
base `latest` @ `0b631c2`; object hash pre `59335b2` → post `3611932`).

```diff
diff --git a/infranstructure/k8s/ensemble/httproute.yaml b/infranstructure/k8s/ensemble/httproute.yaml
index 59335b2..3611932 100644
--- a/infranstructure/k8s/ensemble/httproute.yaml
+++ b/infranstructure/k8s/ensemble/httproute.yaml
@@ -1,7 +1,9 @@
 # www.ensem.dev -> ensemble-vm live UI/API, all paths guarded by oauth2-proxy.
 # Listener sectionName https-gateway-ensem-dev (*.ensem.dev, cert ensem-dev-cert).
-# NOTE: unlike llm-ensem-dev-route, there is NO direct-to-backend rule — the
-# ensemble daemon API is unauthenticated, so everything flows through the guard.
+# NOTE: one NARROW direct-to-backend exemption exists (mirrors llm-ensem-dev-route's
+# per-path bypass): /views/public ONLY — the passcode-gated live-view public lane,
+# whose canonical gate is daemon-side. Everything else — the ensemble daemon API,
+# /views (non-public), /api — stays behind the oauth2-proxy guard.
 apiVersion: gateway.networking.k8s.io/v1
 kind: HTTPRoute
 metadata:
@@ -14,6 +16,20 @@ spec:
       namespace: gateway
       sectionName: https-gateway-ensem-dev
   rules:
+    # Public live-view lane — canonical passcode gate is daemon-side; this rule is
+    # pure passthrough that bypasses oauth2-proxy. PathPrefix /views/public ONLY
+    # (never /views bare, never /api). Timeout 5m (small-file serving) — the
+    # catch-all below keeps the 30m SSE budget.
+    - matches:
+        - path:
+            type: PathPrefix
+            value: /views/public
+      backendRefs:
+        - name: ensemble-live
+          port: 9797
+      timeouts:
+        request: 5m
+    # Everything else stays OAuth-guarded (daemon API, /views non-public, /api).
     - backendRefs:
         - name: oauth2-proxy
           port: 4180
```

**Verbatim new rule** (the single load-bearing addition, `rules[0]`, ordered BEFORE
the catch-all):

```yaml
    - matches:
        - path:
            type: PathPrefix
            value: /views/public
      backendRefs:
        - name: ensemble-live
          port: 9797
      timeouts:
        request: 5m
```

Shape provenance: ADR D8 (`architecture-recommendation.md:90-107`); namespace
discipline D15; timeout 5m per ADR D8 (30m recorded as an acceptable planner-tunable;
the call is 5m — the 30m catch-all below keeps the SSE budget). Backend identity is
UNCHANGED infra: `Service ensemble-live` (selectorless, `clusterIP: None`) with manual
Endpoints → `192.168.1.151:9797` (`infranstructure/k8s/ensemble/endpoint.yaml`). The
new rule points at the **same** backend oauth2-proxy already upstreams to — the public
lane and the OAuth-guarded lane share the identical daemon process; only the proxy hop
differs. No new infra.

### 1.3 THE CEREMONY — merge + apply + drill (quote-faithful, T3.1-CEREMONY)

The infra manifest **stays on its BRANCH** (`feature/ensemble-views-public-exemption`,
commit `e198005816…`) **until the user's promote ceremony.** It is NOT applied now.

**Invariant:** *data-center-scripts `latest` == what's applied to the cluster.* The
apply command below reads the file from the **working tree** — it therefore assumes
the rule is already in the tree. That is why the **merge step is EXPLICIT and comes
FIRST**; skipping it applies a tree without the rule (no-op apply) or, worse, applies
a stale tree.

**Ceremony sequence (at the user's promote ceremony):**

1. **MERGE the manifest branch into `latest`** (explicit step — do not skip):

   ```bash
   git -C /home/nea/Code/data-center-scripts merge --no-ff feature/ensemble-views-public-exemption
   ```

   (Brings commit `e198005816015ba82938e71d49dfc2d4614d77be` — the D16 HTTPRoute
   rule — into `latest`, restoring the invariant *latest == applied*.)

2. **APPLY per the operator notes** (§1.4 below / `state-a-proxy-apply.md` §3) —
   `kubectl diff` first, then `kubectl apply -f infranstructure/k8s/ensemble/httproute.yaml`.

3. **RUN the T3.3 XFF drill** (§3 below — the 3-outcome XFF pin, procedure folded
   VERBATIM from `state-a-proxy-apply.md` §5) plus the AC14 gates (§1.5) and the
   Host-preservation assertion (§1.1/§3 step H).

### 1.4 Apply + verify (folded VERBATIM from `state-a-proxy-apply.md` §3)

Applied from the main data-center-scripts checkout (after the §1.3 merge, branch
`latest`). Verify context FIRST:

```bash
# 0. confirm you are pointed at the ensem-dev cluster (NOT prod)
kubectl config current-context

# 1. server-side dry-run + diff (no mutation) — review before applying
kubectl diff -f infranstructure/k8s/ensemble/httproute.yaml

# 2. apply the manifest (declarative; no pod restarts — pure route change)
kubectl apply -f infranstructure/k8s/ensemble/httproute.yaml

# 3. confirm rule ORDER: /views/public rule must precede the catch-all
kubectl get httproute ensemble-ensem-dev-route -n ensemble -o yaml \
  | grep -nE 'name: (ensemble-ensem-dev-route|oauth2-proxy|ensemble-live)|value: /views/public|request:'
```

**Post-apply gate check (per T3.2 acceptance / AC14):**

```bash
# unauthenticated public path → reaches daemon directly (interstitial / uniform-404), NO redirect to door.ensem.dev
curl -sSI https://www.ensem.dev/views/public/<root>/<file> | head -20

# every other path still redirects to the OAuth door
curl -sSI https://www.ensem.dev/views/<root>/<file> | head -20   # → 302 to door.ensem.dev
curl -sSI https://www.ensem.dev/api/instances | head -20          # → 302 to door.ensem.dev
```

Acceptance: `/views/public/...` answers **200 (interstitial) / 404 (uniform) from the
daemon** — NOT a 302 to `door.ensem.dev`. Every other path **302s to the OAuth door**.

### 1.5 Rollout / rollback (folded VERBATIM from `state-a-proxy-apply.md` §4)

Route-only change — **no pod restarts, no data**. Rollout = `kubectl apply` (§1.4).
Rollback, two equivalent paths:

```bash
# (a) revert the single file to the prior revision and re-apply
git revert e198005816015ba82938e71d49dfc2d4614d77be --no-edit
kubectl apply -f infranstructure/k8s/ensemble/httproute.yaml

# (b) or apply the pre-edit object directly
git show 0b631c2:infranstructure/k8s/ensemble/httproute.yaml > /tmp/httproute.pre.yaml
kubectl apply -f /tmp/httproute.pre.yaml
```

Rollback effect: public URLs **fail CLOSED** into the Casdoor login (availability loss
only, no security regression) — rule missing is a safe failure mode (ADR D8 §Failure
modes).

### 1.6 State A scope guard (release-blocker rule — folded VERBATIM)

The exemption is **exactly** `PathPrefix /views/public` — **nothing wider, ever.**

- ❌ **NEVER** exempt `/views` (bare) — non-public views carry no passcode; OAuth is
  their only guard.
- ❌ **NEVER** exempt `/api` — the daemon API is unauthenticated at the app layer.
- ✅ Only `/views/public/*` (interstitial, verify POST, view bytes, `?raw=1`, and the
  `assets/mermaid/mermaid.min.js` alias) rides the direct lane.
- An over-broad rule (`/views`) strips OAuth from passcode-less views with no
  compensating control → **release blocker** (ADR R2, `architecture-recommendation.md:138-142`).

### 1.7 State A secure-context note (folded VERBATIM from `state-a-proxy-apply.md` §6)

- **Correct public entry point:** `https://www.ensem.dev/views/public/<root>/<path>` —
  HTTPS at the edge, so the grant cookie (`HttpOnly; Secure; SameSite=Lax`, 24h) is
  stored normally.
- **Caveat — direct plain-HTTP LAN access:** hitting the daemon directly at
  `http://192.168.1.151:9797/views/public/...` is a **non-secure context**; the browser
  will **refuse to store the `Secure` grant cookie**, so verification appears to
  "not stick" and the interstitial re-renders on each request. This is **expected**
  behavior, not a bug.
  - The public lane renders an explicit **"secure context required"** message in this
    case (`_render_secure_context_page`, `daemon/routers/live_views.py:671`; ADR D6:79)
    — it **NEVER** issues a redirect loop.
  - LAN direct access is a diagnostics/ops path only; end users must use the
    `https://www.ensem.dev/...` URL. When State B lands, the same HTTPS-at-edge
    requirement holds.

---

## 2. State B — in-house OAuth2/OIDC module (LATER, user-driven manual migration)

State B = the user manually drops the middle server (Envoy Gateway + oauth2-proxy)
and enables the in-house OAuth2 module (`auth.enabled`, default OFF,
`daemon/config.py:2881`).

### 2.1 State B checklist — NO operator action

**There is NO operator step. The exemption is code-seeded and ships active:**

- `tag_routes_by_path(app, path_prefix="/views/public")` is called at the
  `create_app` registration site **AFTER** `include_router`
  (`daemon/api.py:4205`, after the include at `:4180`) — the code-seeded PUBLIC
  prefix tag (ADR D3:43-50). The pre-`include_router` PUBLIC **exact**-tag loop
  (`daemon/api.py:3610-3648`) is untouched and orthogonal.
- The machinery (`daemon/auth/route_meta.py:66-100`) prefix-tags the route table;
  `AuthMiddleware` consumes tagged resolution **before** session validation
  (`daemon/middleware/auth.py:328-335`) — `/views/public/*` passes the middleware,
  everything else default-denies.
- **`auth.anonymous_view_paths` ships EMPTY and STAYS EMPTY** (default `()`,
  `daemon/config.py:2997-2999`). It is NOT the mechanism — it remains the reserved
  v2 `view_shares` seam and the S-19 regression cage. Do not set it for this feature;
  mis-setting it widens the hole and is exactly what ADR D3 rejected.

**State B verify (auth-ON smoke):** boot with `auth.enabled=true` and
`anonymous_view_paths` untouched; then:

| Probe | Expected | Why |
|---|---|---|
| `GET /views/public/<root>/<file>` | daemon answer (interstitial 200 / uniform 404) — **no** 302 to `/api/auth/login` | code-seeded prefix tag passes `AuthMiddleware` |
| `GET /views/<root>/<file>` (direct lane) | **302 → `/api/auth/login?return_to=…`** (browser) — default-deny | direct lane stays OAuth-guarded |
| `GET /views/livez` | 200 (PUBLIC exact tag) | unaffected |
| `GET /views/assets/...` | served (asset route) — *from OAuth'd context or exact-tagged paths only; the alias under the public prefix is `/views/public/assets/...`* | unaffected |

Executed drill + transcript: **§3.3 (State B dry-run) — DONE locally, evidence at
`.agents/shared/planning/live-view-passcode/evidence/`.**

### 2.2 State B Host-preservation (REQUIRED PROPERTY — T3.1-HOST)

The same-host Origin/Referer check (§1.1) applies unchanged in State B: the **in-house
auth module must preserve the `Host` header** (no internal-name rewrite) on requests
it passes through, or every legit verify POST 403s. Whatever fronts the daemon in
State B (in-house OIDC middleware, plain TLS terminator, direct bind), the property is:

> `Host` seen by the daemon == the host in the customer's browser URL bar.

If State B introduces any proxy hop, add the §3 step-H Host assertion to that hop's
acceptance checklist.

---

## 3. Drills (T3.3)

### 3.1 Drill (i) — State A anonymous customer path (CEREMONY-TIME, cluster-side)

Execute after §1.3 step 2, as part of step 3:

1. `curl -sS https://www.ensem.dev/views/public/<root>/<file> -D - -o /dev/null`
   → interstitial renders (HTTP 200, zero-JS HTML), **no OAuth redirect**.
2. Wrong code ×10 (POST the form field to the same URL) → **locked page** (HTTP 200
   with the locked notice — locked-page semantics are HTTP 200 by DECISION, ADR V3;
   NOT 423/404. Pre-lock, unminted/garbage paths stay uniform 404).
3. Fresh link after `view_link(refresh=True)` (regeneration clears the lock, bumps
   generation, revokes outstanding grants) → interstitial → correct code → view bytes.

### 3.2 Drill (ii) — State A OAuth path (CEREMONY-TIME, cluster-side)

`curl -sSI https://www.ensem.dev/views/<root>/<file>` → **302 to `door.ensem.dev`**
(requirement 5 at the proxy layer: the direct lane still demands OAuth).
Also spot-check `/api/instances` → 302 to the door. (AC14.)

### 3.3 Drill (iii) — State B dry-run (EXECUTED LOCALLY 2026-10-17 — evidence archived)

**Procedure (reproducible):** worktree `/home/nea/ensemble-src-wt-liveview-passcode`
(branch `feature/live-view-passcode`), fenced `.env` (dev PG `ensemble_dev` @
`127.0.0.1:5432`, `DAEMON_PORT=8079`), fresh drill-only `SYSTEM_ENCRYPTION_KEY`
(NOT the live key), `ENSEMBLE_AUTH_ENABLED=true` with placeholder OIDC values
(discovery fetch is deferred/WARN-only), `ENSEMBLE_LIVE_VIEWS_ROOTS` pinned to a
drill-local filesystem root, `anonymous_view_paths` left EMPTY (the whole point),
bounded-timeout boot, curl sequence, clean shutdown.

**Executed transcript:** `.agents/shared/planning/live-view-passcode/evidence/state-b-dry-run-transcript.md`
(drill script: `evidence/state-b-dry-run.sh`; boot log: `evidence/state-b-boot.log`).
**Outcome (2026-10-17, HEAD `b683a545e`): ALL PROBES PASS** — boot log derives
`/views/public/{root_name}/{rel_path:path}` into the middleware's public path set;
unminted public path → uniform 404 from the daemon (NOT a login redirect); cross-host
Origin verify → 403; plain-HTTP correct-code verify → "Secure connection required"
page, no cookie, no redirect loop; edge-emulated (`X-Forwarded-Proto: https`) correct
verify → 303 + `view_grant` cookie → bytes through the gate; direct lane + `/api/*`
→ 302 to `/api/auth/login`. **No daemon defect surfaced; zero daemon code changes in P3.**

**Host-preservation assertion (local counterpart of §3 step H):** the verify POST is
exercised with (a) a **same-host** `Origin` (`http://127.0.0.1:8079` == the inbound
`Host`) → the request proceeds past the Origin check into the passcode comparison
(deny/allow as coded), and (b) a **cross-host** `Origin` (`http://evil.example`) →
**403** — proving the check fires on header presence and compares against the inbound
`Host`. In State A/B production the equivalent assertion is: **the proxy must preserve
Host so legit requests land in case (a), never case (b).**

### 3.4 Drill step H — Host-preservation + XFF pin (CEREMONY-TIME, cluster-side)

After the §1.3 apply, run BOTH pins once and archive the outputs with the ceremony
evidence:

**H — Host preservation** (REQUIRED property, §1.1 — Envoy behavior is NOT pre-pinned
in the operator notes):

```bash
# Same-host verify probe: Origin host == Host header the daemon should receive.
# Mint a view first (view_link), then:
curl -sS 'https://www.ensem.dev/views/public/<root>/<file>' \
  -X POST -d 'passcode=wrong-on-purpose' \
  -H 'Origin: https://www.ensem.dev' -D - -o /dev/null
# EXPECT: NOT 403 — a 200 (interstitial/locked re-render) or the deny path
# (strike/bucket). A 403 here = Host rewritten in flight → STOP and fix the
# proxy (preserve Host); do NOT loosen the daemon check.
```

**X — XFF 3-outcome pin** (folded VERBATIM from `state-a-proxy-apply.md` §5;
ADR V4 / D7 rightmost-XFF keying, `daemon/routers/live_views.py:690-711`):

> ⚠️ **UNVERIFIED-until-applied.** The daemon rate-limiter keys its per-IP bucket on
> the **RIGHTMOST** `X-Forwarded-For` entry else the socket peer. That choice is only
> correct if Envoy **appends** the immediate peer to XFF (so the rightmost entry = the
> address our proxy saw) rather than **overwriting** it. Empirically pin at this drill.

```bash
# 1. Send a request with an attacker-supplied leftmost XFF through the public lane.
#    Public lane is daemon-reachable — a strike/limiter probe on a minted view exposes the key.
curl -sS https://www.ensem.dev/views/public/<root>/<file> \
  -H 'X-Forwarded-For: 10.0.0.1' -D - -o /dev/null

# 2. From the daemon side (live :9797), read what the app actually received:
#    - if XFF == "<spoofed-leftmost>, <real-client>"  → Envoy APPENDS → rightmost-keying is CORRECT (proceed)
#    - if XFF == "<real-client>"                       → Envoy OVERWRITES → appends-once == overwrite,
#                                                         single entry, rightmost == leftmost (still safe)
#    - if XFF == "<spoofed-leftmost>"                  → Envoy TRUSTS client XFF verbatim → keying is
#                                                         SPOOFABLE → STOP, report (limiter void; durable
#                                                         strike lock remains the backstop per ADR V6)
```

All three outcomes leave the 10-strike durable lock as the harder backstop — the
limiter is a soft front (fail-open, ADR V6). Degradation direction is over-throttle,
never bypass. Also record at the drill: (i) interstitial renders with no OAuth
redirect; (ii) wrong code ×10 → locked page; (iii) fresh link after refresh → renders;
(iv) `/views/<root>/<file>` still demands OAuth (AC14 / requirement 5).

---

## 4. Couplings & operational caveats

### 4.1 `SYSTEM_ENCRYPTION_KEY` rotation (R4 walk — three victims, by design)

Rotating `SYSTEM_ENCRYPTION_KEY` without the live-view walk:

1. **kills every passcode row** (`live_view_passcodes.wrapped_passcode` is Fernet
   ciphertext under the old key → `ViewPasscodeRotationRequired` on next verify;
   customers see the interstitial's generic error, never a 500; owners recover via
   `view_link(refresh=True)` → regenerate);
2. **kills every outstanding grant cookie** (grant tokens are Fernet under the same
   key; old cookies fail validation → interstitial re-renders — fail-closed, no
   dangling access);
3. **breaks historical `ip_hmac` correlation BY DESIGN** (audit IPs are keyed-HMAC
   under a domain-separated key derived from `SYSTEM_ENCRYPTION_KEY`; rotation changes
   the derived key → cross-period correlation becomes impossible; raw IPs were never
   stored and cannot be re-computed — accepted audit trade, ADR D9:117).

The rotation ceremony (`SYSTEM_ENCRYPTION_KEY` coupling note in the critical notes /
agent-secrets plan) MUST re-encrypt the `live_view_passcodes` walk alongside the
`db_connections` walk; outstanding grant cookies are intentionally NOT migrated
(they die — re-verify required).

### 4.2 In-memory bucket restart caveat (ADR R6/V6)

The per-IP token bucket is **in-memory** (single-VM assumption): a daemon restart
clears all buckets — each attacker gets one fresh burst. **The durable 10-strike
lock (PG-backed row columns) is the authoritative abuse control**, and it survives
restarts. Fail-open on limiter-internal error (loud log) — a limiter bug must not
brick the lane; degradation direction is over-throttle, never bypass.

### 4.3 Locked-page semantics (D10 / ADR V3)

A locked view renders the "locked — ask for a fresh link" page with **HTTP 200** (not
423/404 — DECIDED; browser-UX posture, sign-off ADR D2/R11). Pre-lock, the
unminted/unknown-path class stays **uniform 404** (`_uniform_404`). Existence
disclosure of the locked state is a consciously accepted, explicitly adjudicated
extension (D10). Recovery is owner-side only: `view_link(refresh=True)` (regenerate +
clear lock + revoke grants). Idempotent `view_link` without `refresh` does NOT clear
an active lock (S1).

### 4.4 Envoy XFF append/overwrite behavior (ADR V4)

The limiter keys on the **rightmost XFF entry else socket peer**
(`daemon/routers/live_views.py:690-711`). Correct under append OR overwrite-once
topologies; spoofable only if a proxy trusts client-supplied XFF verbatim. The §3.4
X-pin settles which topology State A actually is — **empirically, at the ceremony,
never by assumption.**

---

## 5. Explicit warning — the direct lane stays OAuth-guarded (ADR R2)

> **`/views/<root>/<path>` (the direct lane) is and remains OAuth-guarded in BOTH
> states. NEVER exempt `/views` or `/api` at any proxy layer, ever.**
>
> Non-public live views carry no passcode — OAuth is their ONLY guard. `/api` is
> unauthenticated at the app layer by design. An over-broad exemption strips OAuth
> from passcode-less views with no compensating control → **RELEASE BLOCKER**
> (ADR R2). The public namespace is exactly `PathPrefix /views/public` — if a diff
> proposes widening it, refuse the diff and re-read §0.

---

## 6. Cross-references

- Plan pack: `.agents/shared/planning/live-view-passcode/` — `phase3-plan.md`
  (T3.1–T3.4), `decisions.md` (D15–D18, D-A1 fold), `architecture-recommendation.md`
  (ADR of record: D3, D7, D8, D9, V4, R2), `state-a-proxy-apply.md` (operator notes —
  the verbatim source of §1.2/§1.4/§1.5/§1.7/§3.4-X).
- State A manifest commit: `e198005816015ba82938e71d49dfc2d4614d77be`
  (branch `feature/ensemble-views-public-exemption`, data-center-scripts repo —
  NOT applied until the §1.3 ceremony).
- Subsystem summary + operator smoke: `docs/runbooks/live-views.md` §9.
- Code anchors (verified at HEAD `b683a545e`): `PUBLIC_NAMESPACE`
  (`daemon/services/live_views.py:155`); prefix tag call
  (`daemon/api.py:4205`, after `include_router` `:4180`); PUBLIC exact-tag loop
  (`daemon/api.py:3610-3648`); tagged resolution before session validation
  (`daemon/middleware/auth.py:328-335`); `anonymous_view_paths` default `()`
  (`daemon/config.py:2997-2999`); same-host Origin/Referer check
  (`daemon/routers/live_views.py:721`); rightmost-XFF keying
  (`daemon/routers/live_views.py:690-711`); secure-context page
  (`daemon/routers/live_views.py:671`).
