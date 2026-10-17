# Phase 3 — Deployment Two-State: State A proxy exemption, State B in-house-auth exemption, deployment note

## 1. Objective

Make the public lane survive BOTH deployment states with the daemon as the canonical gate
and proxies as pure passthrough (NO coupling to external proxy mechanics):

- **State A (ship-time):** external middle OAuth2 (k8s Envoy Gateway + oauth2-proxy v7.6.0
  / Casdoor OIDC at door.ensem.dev) stays in front. A NARROW `PathPrefix /views/public`
  rule routes that namespace DIRECT-to-backend, bypassing oauth2-proxy — the exact house
  exemption pattern already proven for `/v1` in the llmproxy route
  (`infranstructure/k8s/llmproxy/rancher-local-ensem-dev/llm.ensem.dev-httproute.yaml:20-36`
  in the data-center-scripts repo).
- **State B (later, user-driven manual migration):** middle server dropped, in-house
  OAuth2/OIDC module enabled (`auth.enabled`, default OFF at `daemon/config.py:2719`,
  field default False at `:2774-2779`). The public namespace stays exempt there via the
  **ADR-D3 code-seeded PUBLIC prefix tag** — `tag_routes_by_path(app,
  path_prefix="/views/public")` at the create_app registration site (machinery:
  `daemon/auth/route_meta.py:66-100`, which DOES support prefix tagging; consumed by
  ordered tagged resolution before session validation, `daemon/middleware/auth.py:328-335`).
  The `auth.anonymous_view_paths` knob is **NOT the mechanism** — it ships EMPTY and stays
  empty (deterministic-by-code, not operator-config: forget-to-set kills the lane; mis-set
  widens the hole). State B needs ZERO operator action and ZERO new code beyond the one
  registration line already planned in P2.

Deliverable: the deployment note doc stating EXACTLY which namespace must stay public
through the migration, plus the State A manifest diff spec (implemented in the
data-center-scripts repo in this phase's implementation step of the commission — this
planning run writes the SPEC only).

## 2. Decisions

| ID | Decision | Rationale (anchors) | Rejected alternatives |
|----|----------|---------------------|------------------------|
| D15 | The exempted namespace in BOTH states is exactly `PathPrefix /views/public` — nothing narrower (per-view paths) and nothing wider (never `/views/assets`, never `/views/*`) | Narrower = proxy config churn per view (breaks "cheap mint" UX); wider = the direct lane (`/views/<root>/<path>`) would leak past OAuth, violating the unchanged-OAuth-path requirement 5 and re-opening the public-by-obscurity file family to the internet | Exempting `/views/livez`-style exact paths only (insufficient — customers need the interstitial + verify POST + content + mirrored assets, all under the one prefix, D7); host-based exemption (over-broad) |
| D16 | State A manifest change = ONE new rule INSERTED ABOVE the existing catch-all in `ensemble-ensem-dev-route` (`infranstructure/k8s/ensemble/httproute.yaml` — ADR §0 recon: the current route is a PURE catch-all, no `matches` block): `matches: [{path: {type: PathPrefix, value: /views/public}}]` → `backendRefs: [{name: ensemble-live, port: 9797}]` + `timeouts: {request: 5m}` per ADR D8 (30m acceptable — planner tunable; ADR rationale: small-file serving; the 30m catch-all is the SSE budget); catch-all → oauth2-proxy:4180 (30m) unchanged below; http→https redirect route unchanged; `ensemble-live` already exists and already backs oauth2-proxy's upstream (`oauth2-proxy.yaml:27`) — no new infra | Gateway API HTTPRoute rules are first-match-wins; the llmproxy route proves the per-path direct-backend pattern in this exact gateway; ADR D8 pins the exact YAML shape | A separate HTTPRoute object for the public path (extra object to keep in sync; rule-order inside the existing route is simpler and mirrors llmproxy); path rewrites (none needed — daemon owns the prefix); 30m on the public rule (ADR default 5m; the tunable stays open but 5m is the recorded call) |
| D17 | **SUPERSEDED BY ADR D3 (fold 2026-10-17):** State B = the code-seeded PUBLIC prefix tag — `tag_routes_by_path(app, path_prefix="/views/public")` called at the create_app registration site AFTER `include_router` (the existing PUBLIC exact-tag loop at `daemon/api.py:3526-3560` runs BEFORE `include_router` at `:4099`, so a SECOND, prefix-mode call is required). The `auth.anonymous_view_paths` knob ships EMPTY and STAYS EMPTY — it remains the reserved v2 `view_shares` seam and the S-19 regression cage. `tag_routes_by_path` DOES prefix-tag (`path_prefix` mode, `daemon/auth/route_meta.py:66-100`) — this plan's earlier "exact-template lane" characterization was wrong and is corrected here. The PUBLIC exact-path tuple (`daemon/api.py:3531-3556`) is untouched either way | Requirement is explicit: deterministic-by-code, not operator-config-dependent (ADR D3:48 — forget-to-set kills the lane; mis-set widens the hole); the machinery exists and is consumed before session validation (`middleware/auth.py:328-335`); a `/views`-scoped prefix tag carries no admin-seam risk (ADR D3:49) | Operator sets `anonymous_view_paths=["/views/public"]` (REJECTED by ADR — definitionally operator-config); adding `/views/public` to the PUBLIC exact-path tuple (wrong mechanism on its own terms: EXACT-template lane, and would bypass the middleware's prefix semantics); hardcoding the exemption inside `middleware/auth.py` (silent, unconfigurable, breaks the v1-ships-empty regression cage) |
| D18 | The deployment note is a NEW runbook doc `docs/runbooks/live-view-passcode-deployment.md` + a pointer section appended to `docs/runbooks/live-views.md` (which exists today and is the subsystem's edge-guard doc home); the note names the namespace, both states — **State B item reads "no operator action; the knob remains empty" (ADR D3)** — plus the cookie/rotation coupling and the restart caveat for the in-memory bucket | `docs/runbooks/live-views.md` already documents the family's edge guard (router docstring cites it); a dedicated sibling keeps the migration checklist greppable at promote time | One merged giant section in live-views.md (migration checklist gets lost); note only in planning dir (planning dirs are feature-scoped, runbooks are operational) |

## 3. Tasks

| ID | Task | Outcome (testable) | Touchpoints (files) | Depends on | Risks carried | AC |
|----|------|--------------------|---------------------|------------|----------------|----|
| T3.1 | Write `docs/runbooks/live-view-passcode-deployment.md`: (a) THE namespace line — `PathPrefix /views/public` is the ONLY thing that must stay public; (b) State A checklist — exact YAML diff per D16 (one rule ordered FIRST above the pure catch-all — ADR §0 recon: the current route has NO `matches` block — `ensemble-live:9797`, 5m request timeout per ADR D8, 30m acceptable planner-tunable), apply + verify steps (`curl -I https://www.ensem.dev/views/public/...` → 200/404 from daemon, NOT a redirect to oauth2-proxy), rollout/rollback = `kubectl apply` / git revert + apply (no pod restarts, ADR D8:106); (c) State B checklist — **NO operator action; `auth.anonymous_view_paths` remains EMPTY** (exemption is the code-seeded prefix tag, ADR D3); verify with an auth-ON smoke that `/views/public/*` passes and the direct lane default-denies; (d) couplings — `SYSTEM_ENCRYPTION_KEY` rotation kills passcodes + outstanding grants (R4 walk, ADR D4:59) + breaks historical `ip_hmac` correlation by design (ADR D9:117), in-memory bucket restart caveat (R2/ADR-R6), locked-page semantics (D10/ADR V3), Envoy XFF append/overwrite behavior pinned (ADR V4); (e) explicit warning that `/views/<root>/<path>` (direct lane) is and remains OAuth-guarded — never exempt `/views` or `/api` (ADR R2: over-broad exemption = release blocker) | Doc review: every claim matches shipped code/constants (`PUBLIC_NAMESPACE`, tag mechanism, service/port names); checklists are copy-paste executable; State-B section contains NO operator step | `docs/runbooks/live-view-passcode-deployment.md`, `docs/runbooks/live-views.md` (pointer section) | P2 | R2, R4 | AC14, AC15 |
| T3.2 | State A manifest change (data-center-scripts repo lane — its own worktree/commit, NOT this ensemble worktree): apply the D16 diff to `infranstructure/k8s/ensemble/httproute.yaml`; commit message references this planning dir + the commission | `kubectl get httproute ensemble-ensem-dev-route -o yaml` shows the public-prefix rule BEFORE the catch-all; daemon-reachable check from outside the cluster passes; oauth2-guarded paths still redirect to door.ensem.dev | data-center-scripts: `infranstructure/k8s/ensemble/httproute.yaml` | T3.1 | R3 (XFF now actually flows), R11 | AC14 |
| T3.3 | End-to-end verification drill (documented IN the runbook, executed at implementation): (i) State A anonymous customer path — browser/curl through door.ensem.dev: `/views/public/<root>/<file>` → interstitial renders (no OAuth redirect), wrong code ×10 → locked page, fresh link after refresh → renders; (ii) State A OAuth path — `www.ensem.dev/views/<root>/<file>` still demands oauth2 login (requirement 5 at the proxy layer); (iii) State B dry-run — local boot with auth-ON and `anonymous_view_paths` LEFT EMPTY: `/views/public/*` passes `AuthMiddleware` via the code-seeded prefix tag (ADR T2 pin), direct URL 401s/redirects to in-house login, `/views/livez` + `/views/assets/*` unaffected | Drill transcript captured as evidence for the review pack (P4) and stage-RC report (P5) | `docs/runbooks/live-view-passcode-deployment.md` (evidence appendix), test evidence files | T3.2, P2 | R1, R3 | AC7, AC14, AC15 |
| T3.4 | Deployment note also lands as the commission's mandated deliverable in the final report + project records (pointer from `.agents/shared/context.md` update is OUT of this plan's write scope — the leader owns context.md; this plan only guarantees the runbook exists and is cited) | Report + runbook cross-reference consistent; no context.md writes from this plan | final report (dispatcher-facing) | T3.1 | — | AC15 |

## 4. Acceptance Criteria

| ID | Criterion |
|----|-----------|
| AC14 | State A: an unauthenticated request to `https://www.ensem.dev/views/public/...` reaches the daemon directly (no oauth2-proxy hop — no redirect to door.ensem.dev), while every other `www.ensem.dev` path still requires OAuth |
| AC15 | State B: with in-house auth ON and `auth.anonymous_view_paths` EMPTY (it stays empty — ADR D3), the public lane passes `AuthMiddleware` via the code-seeded `tag_routes_by_path(app, path_prefix="/views/public")` prefix tag (`daemon/auth/route_meta.py:66-100`, consumed before session validation at `daemon/middleware/auth.py:328-335`); the direct lane stays default-deny; `/views/livez` + `/views/assets/*` unaffected; PUBLIC exact-path tuple unchanged |

## Exit criteria (phase gate)

- Runbook merged in the ensemble worktree; State A diff applied in the data-center-scripts
  lane; drill transcript archived.
- `daemon/api.py` gains EXACTLY ONE expected route-tag line — the
  `tag_routes_by_path(app, path_prefix="/views/public")` registration AFTER
  `include_router` (ADR D3; the pre-`include_router` PUBLIC exact-tag loop at
  `:3526-3560` is untouched) — and ZERO changes to
  `daemon/middleware/auth.py`; `auth.anonymous_view_paths` ships/stays empty
  (all provable by `git diff --stat`).

## Risks (phase-local emphasis)

- R3: XFF becomes real traffic-shaping input once State A routes direct — ADR D7 keys the
  bucket on the RIGHTMOST XFF entry (proxy-appended = real client); the drill (T3.3) must
  pin Envoy's XFF append/overwrite behavior on the exempted path in the runbook (ADR V4).
  If Envoy overwrites rather than appends, rightmost collapses to the single entry —
  still correct; the failure mode is over-throttling, never bypass.
- R11 (new, phase-local): the data-center-scripts manifest change is in a DIFFERENT repo —
  a sync failure leaves State A broken (public lane 401s through oauth2-proxy). Mitigation:
  the runbook's State A checklist includes the post-apply verification curl; the stage-RC
  report (P5) lists the manifest commit hash as evidence.
