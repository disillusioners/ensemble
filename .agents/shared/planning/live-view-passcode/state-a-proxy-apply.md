# State A — proxy exemption apply notes (`/views/public`)

**Lane:** data-center-scripts repo (cross-repo manifest edit for commission
*public passcode access for live views*).
**Status of application:** ⏸ **NOT APPLIED** — staged commit only. Application happens at the
user's promote ceremony, not now. This file is operator guidance, not evidence of a change.

## 1. What was changed (identity)

| Field | Value |
|---|---|
| Repo | `data-center-scripts` |
| Main checkout | `/home/nea/Code/data-center-scripts` (main working tree — `git-dir == git-common-dir == .git`) |
| Branch | `feature/ensemble-views-public-exemption` |
| Commit | `e198005816015ba82938e71d49dfc2d4614d77be` (`e198005`) |
| Base | `latest` @ `0b631c2` (clean tree at branch point) |
| File | `infranstructure/k8s/ensemble/httproute.yaml` |
| Object hash | pre-edit `59335b2` → post-edit `3611932` |

> Note: recon referenced `/home/nea/Code/data-center-scripts-wt-llm-supervisor-proxy-daoduc/`
> — that worktree **no longer exists** (`git worktree list` shows only the main checkout).
> The edit was made in the main checkout, which was clean on `latest`. No foreign worktree was touched.

## 2. Verbatim manifest diff

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

**Verbatim new rule** (the single load-bearing addition, rule[0], ordered BEFORE the catch-all):

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

**Shape provenance:** pinned by ADR D8 (`architecture-recommendation.md:90-107`); namespace
discipline D15; timeout D16 (`phase3-plan.md:36`) — 5m per ADR D8 (30m recorded as an acceptable
planner-tunable; the call is 5m). Backend `/v1`-bypass precedent:
`infranstructure/k8s/llmproxy/rancher-local-ensem-dev/llm.ensem.dev-httproute.yaml:20-36`.

**Backend identity (unchanged infra):** `Service ensemble-live` (selectorless, `clusterIP: None`)
with manual `Endpoints` → `192.168.1.151:9797` (`infranstructure/k8s/ensemble/endpoint.yaml`);
oauth2-proxy already upstreams to it (`--upstream=http://ensemble-live.ensemble.svc.cluster.local:9797`,
`oauth2-proxy.yaml:27`). The new rule points at the **same** backend, so the public lane and the
OAuth-guarded lane share the identical daemon process; only the proxy hop differs.

## 3. Exact apply command (at the promote ceremony — NOT now)

Applied from the main data-center-scripts checkout (branch `feature/ensemble-views-public-exemption`,
or after merge to `latest` per repo convention). Verify context FIRST:

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

Expected post-apply gate check (per T3.2 acceptance / AC14):

```bash
# unauthenticated public path → reaches daemon directly (interstitial / uniform-404), NO redirect to door.ensem.dev
curl -sSI https://www.ensem.dev/views/public/<root>/<file> | head -20

# every other path still redirects to the OAuth door
curl -sSI https://www.ensem.dev/views/<root>/<file> | head -20   # → 302 to door.ensem.dev
curl -sSI https://www.ensem.dev/api/instances | head -20          # → 302 to door.ensem.dev
```

## 4. Rollback command

Route-only change, no pod restarts, no data. Two equivalent paths:

```bash
# (a) revert the single file to the prior revision and re-apply
git revert e198005816015ba82938e71d49dfc2d4614d77be --no-edit
kubectl apply -f infranstructure/k8s/ensemble/httproute.yaml

# (b) or apply the pre-edit object directly
git show 0b631c2:infranstructure/k8s/ensemble/httproute.yaml > /tmp/httproute.pre.yaml
kubectl apply -f /tmp/httproute.pre.yaml
```

Rollback effect: public URLs fail **CLOSED** into the Casdoor login (availability loss only, no
security regression) — rule missing is a safe failure mode (ADR D8 §Failure modes).

## 5. Expected XFF behavior on direct-backend rules (Envoy Gateway) — runbook V4 / T3.3 drill

> ⚠️ **UNVERIFIED-until-applied.** The daemon rate-limiter keys its per-IP bucket on the
> **RIGHTMOST** `X-Forwarded-For` entry else the socket peer (ADR D7 / `architecture-recommendation.md:83-86`).
> That choice is only correct if Envoy **appends** the immediate peer to XFF (so the rightmost entry =
> the address our proxy saw) rather than **overwriting** it. This must be **empirically pinned at the
> T3.3 post-apply drill** — do not assume.

**Pin procedure (run once after State-A apply, capture as V4 evidence):**

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

**All three outcomes leave the 10-strike durable lock as the harder backstop** — the limiter is a
soft front (fail-open, ADR V6). Degradation direction is over-throttle, never bypass
(`architecture-recommendation.md:83-86`).

**Also record at the drill:** (i) interstitial renders with no OAuth redirect; (ii) wrong code ×10 →
locked page; (iii) fresh link after refresh → renders; (iv) `/views/<root>/<file>` still demands OAuth
(AC14 / requirement 5). Full drill transcript = phase3 T3.3 evidence.

## 6. Secure-context note (deployment caveat — NEVER a redirect loop)

- **Correct public entry point:** `https://www.ensem.dev/views/public/<root>/<path>` — HTTPS at the
  edge, so the grant cookie (`HttpOnly; Secure; SameSite=Lax`, 24h) is stored normally.
- **Caveat — direct plain-HTTP LAN access:** hitting the daemon directly at
  `http://192.168.1.151:9797/views/public/...` is a **non-secure context**; the browser will
  **refuse to store the `Secure` grant cookie**, so verification appears to "not stick" and the
  interstitial re-renders on each request. This is **expected** behavior, not a bug.
  - The public lane must render an explicit **"secure context required"** message in this case
    (ADR D6:79) — it must **NEVER** issue a redirect loop. Deployment note carries this caveat.
  - LAN direct access is a diagnostics/ops path only; end users must use the `https://www.ensem.dev/...`
    URL. When State B lands (middle server dropped, in-house OIDC), the same HTTPS-at-edge requirement
    holds.

## 7. Scope guard (release-blocker rule)

The exemption is **exactly** `PathPrefix /views/public` — **nothing wider, ever.**

- ❌ **NEVER** exempt `/views` (bare) — non-public views carry no passcode; OAuth is their only guard.
- ❌ **NEVER** exempt `/api` — the daemon API is unauthenticated at the app layer.
- ✅ Only `/views/public/*` (interstitial, verify POST, view bytes, `?raw=1`, and the
  `assets/mermaid/mermaid.min.js` alias) rides the direct lane.
- An over-broad rule (`/views`) strips OAuth from passcode-less views with no compensating control
  → **release blocker** (ADR R2 / `architecture-recommendation.md:138-142`).

## 8. Cross-references

- Plan: `.agents/shared/planning/live-view-passcode/` — `phase3-plan.md` (T3.2 this lane, T3.3 drill),
  `decisions.md` (D15/D16), `architecture-recommendation.md` (D8, D7, V4, R2).
- State-B counterpart: in-house OIDC exemption is **code-seeded** via
  `tag_routes_by_path(app, path_prefix="/views/public")` — **no operator action**, and
  `auth.anonymous_view_paths` stays EMPTY (ADR D3).
- House precedent: `infranstructure/k8s/llmproxy/rancher-local-ensem-dev/llm.ensem.dev-httproute.yaml:20-36`.
