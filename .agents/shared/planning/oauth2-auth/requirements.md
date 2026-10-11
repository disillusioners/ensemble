# Requirements: OAuth2 Authentication Support for the Ensemble Daemon

**AMEND v1.4 — 2026-10-10 (user ruling: module auth lanes)**

**AMEND v1.3 — 2026-10-10 (approver iteration-001 fixes)**

**Date:** 2026-10-10
**Author:** planner[v2] via requirements-analysis worker (replacement instance — see resilience note below)
**Status:** Draft — architect CONDITIONAL PASS applied (see `architecture-recommendation.md §5`) — v1.2 amendment sweep applied (reviewer APPROVED-WITH-NOTES: 1 CRITICAL + 8 MAJOR + 12 MINOR; this brief is the sole v1.2 authority — no reviewer artifact on disk) — **v1.3 approver iteration-001 fixes applied** (5 BLOCKING fixed + 12 non-blocking folds, see `.agents/approver/oauth2-auth-tracking.md`) — **AMEND v1.4 applied (user ruling: module auth lanes; sole v1.4 authority = the dispatch's 3-point ruling; every v1.4 edit is greppably marked `AMEND v1.4 / module-lanes`)**. `architecture-recommendation.md` stays FROZEN.

> **AMEND v1.4 — 2026-10-10 (user ruling: module auth lanes).** The user
> ruled (3-point authority): (1) MCP-KB and webhook modules have their OWN
> UI and API-key management — they are NOT designed for human-user access
> via OAuth2 and sit beyond/separate from OAuth2; (2) each module is a
> SEPARATE auth lane — a webhook-source token must NOT work for MCP-KB and
> neither works for anything else (LANE ISOLATION IS A HARD REQUIREMENT);
> (3) OAuth2's role is to protect the token-management CRUD endpoints
> (webhook-source tokens = the existing generic `/api/sources*` CRUD; MCP-KB
> keys = a NEW small `/api/mcp/kb/keys` CRUD family) — the module endpoints
> themselves (webhook ingest, MCP-KB mounts) authenticate with their own
> per-module keys. When auth is OFF everything is unchanged (as today).
> Applied in this file as: §5.0 module-lane inventory subsection (NEW);
> §5 legend + §5.4 webhooks row + §5.5 KB-mount row + §5.6 counts
> reclassified MODULE-LANE; §6.6 module-lane config boundary (NEW); §7
> preamble + row 17 verdict + NEW row 18 + §7.2 counts reconciled; FR-12 /
> FR-22 / FR-22a amended to the three-class derivation; NEW FR-39…FR-42 +
> AC-40…AC-42; NFR-11 / NFR-14 / NFR-15 / S-20 annotated; §8.1 map
> extended. technical-analysis.md and architecture-recommendation.md are
> NOT edited in this pass (see decisions.md D19 for the S-20 supersession).

> **Amendment log (v1.1, 2026-10-10).** The architect's CONDITIONAL PASS
> applied the following plan-text amendments to this file:
> - **D1 (Mount fix, MUST-FIX)** — `/api/mcp/kb` and `/api/mcp/kb/sse`
>   added to FR-22 public set; §5.5 mounts row updated; KB-MCP-auth
>   reserved seam documented.
> - **D2 (redirect chain wiring, MUST-FIX)** — `live_view_external_base_url_fallback`
>   field added to AuthConfig §6.1; D6 ratified.
> - **S-19 (anonymous-public `/views` subpath seam, MUST-ADD)** — New seam
>   added to §10 + cage-test entry in §10.1; FR-36 amended; §10.2 names
>   S-19 as the reserved branch point; `ENSEMBLE_AUTH_ANONYMOUS_VIEW_PATHS`
>   added to §6.1 env-var table.
> - **C1 (router-mount scope-declaration cage)** — FR-38 added; cage test
>   fails when `include_router()` mounts a router without explicit scope
>   declaration.
> - **C2 (bootstrap-token exfiltration surface)** — FR-16a added with three
>   hardening conditions (deny-list + one-shot + runbook + trust-class
>   disclosure).
> - **C3 (route-metadata derivation, leader ruling)** — FR-22a added:
>   `public_path_set` derived from route metadata at construction;
>   startup log.
> - **C4 (boot preflight, exit-78)** — FR-26 amended: exit-78 runs at boot
>   preflight (not lifespan); NFR-7 wording carried through.
> - **NFR-8 (SQLite posture, leader ruling D17)** — Reworded to
>   "boot refuses (`enabled=true` ∧ SQLite backend)" → INERT AUTH TABLES
>   leader ruling applied: tables on both backends, single code path, no
>   manual markers. The `require_sqlite_rejection` env var row was REMOVED
>   from §6.1 (contradicted the migration runner's auto-apply behavior at
>   `daemon/migrations/runner.py:107-113`).
> - **Line-drift** — `jobs_crud.py:275-278`→`:595`; `messages.py:391`→`:690`;
>   `launcher.sh:52-54`→`launcher.sh:52` (repo ROOT); 27→28 routers incl.
>   `tmp_images_router` at `api.py:3142`.
> - 2026-10-10 (post-approval cleanup): duplicate tail (write-iteration artifact, ~lines 1800-1836) snipped after the first End-of-file marker; content unaffected (approver iteration-002 note).

**Source Request:** Add OAuth2-based authentication to the daemon HTTP surface, opt-in only, default-OFF, preserving all 12 inventoried loopback/same-host machine consumers plus all health-probe/restart/upgrade machinery; architecturally accommodate future /views share tokens and per-root anonymous scopes without precluding them. Full four user requirements are in §2 (Requirements Traceability).
**Sibling artifact (architectural input — DO NOT re-litigate):** `.agents/shared/planning/oauth2-auth/technical-analysis.md` (1386 lines, ready 2026-10-10). This document grounds testable FRs / NFRs / AC / matrix entries against the architecture established there (Auth Code + PKCE browser lane; static-bearer machine lane; PG-backed `auth_sessions` / `auth_states` / `auth_bearer_tokens`; in-process `tests/fakes/fake_oidc_provider.py`; 503 `LOGIN_NOT_CONFIGURED` when base URL unresolvable; loopback blanket-exemption REJECTED). Where this document needs architectural detail, it cites the sibling.
**Resilience note:** This document is being written by a replacement instance for the original `requirements-analysis` worker that died twice on provider stream errors before writing anything. It is written INCREMENTALLY (skeleton → sections appended one by one) so any further interruption leaves partial-but-coherent progress on disk for the planner to consume.

---

## Table of Contents

1. Purpose & Scope
2. Requirements Traceability
3. Functional Requirements (FR-*)
4. Non-Functional Requirements (NFR-*)
5. Endpoint Protection Matrix
6. Config Surface Spec
7. Machine-Client Compatibility Table
8. Acceptance Criteria
9. Open Questions
10. Reserved Seams (Future-Not-Precluded)

Appendix A — Assumptions
Appendix B — Out of Scope (deferred to later phases)
Appendix C — Glossary

---

## 1. Purpose & Scope

### 1.1 Purpose

The Ensemble daemon currently exposes its full HTTP API surface — **31 FastAPI routers** (line-drift corrected v1.2 / Minor #1: `daemon/api.py` ships 31 `include_router(` call sites at `api.py:3097-3142`; was 28 in v1.1, which understated `tmp_images_router` + `recovery_router` + `blueprints_router` + `workspace_router` + `database_router` + `system_queues_router` post-dating the v1.1 count) plus the SPA mount, vscode proxy, live-views subtree, MCP mounts, and FastAPI-internal docs endpoints — with **zero inbound authentication** (grep-verified: every credential reference in the repo is an outbound *client*, never an inbound *gate*). This v1 build adds an **opt-in OAuth2-based authentication layer** that operators enable via configuration. When disabled, the daemon behaves bit-identically to today (zero behavioral change provable by the default-OFF acceptance scenario in §8). When enabled, the layer gates the API surface in two parallel lanes:

- **Lane A — Cookie session (browser / Angular SPA).** Authorization Code flow with PKCE against an OpenID-Connect-compliant Identity Provider, server-side session storage in PostgreSQL, `HttpOnly; Secure; SameSite=Lax` cookie named `ensemble_sid`, 302-redirect on browser unauthenticated requests (Accept: text/html), 401 + `WWW-Authenticate: Bearer realm="ensemble"` on machine-style requests (Accept: application/json). Angular SPA integration requires no change to existing request logic; it only needs to handle the 302-on-first-load-after-session-expiry case (covered in §5 / §6 / §8). **AMEND v1.2 / Minor #5 — SPA Accept-header caveat:** in practice, the SPA's `HttpClient` sends `Accept: application/json` on XHRs (NOT `text/html`), so a SPA XHR hitting an expired-token path returns `401 AUTH_REQUIRED`, not `302`. The 302 path is reserved for top-level browser navigations (Address-bar `GET /api/instances`, `window.location` assignments, link clicks). Both paths are accepted by the contract — the SPA's existing 401-handler triggers the same redirect-to-login UX. The behavior the SPA ACTUALLY sees depends on whether its `HttpClient` requests include `Accept: application/json` (XHR lane → 401) or whether the browser issues a top-level navigation (text/html lane → 302). The Confirm-at-implementation list in `plan-overview.md §12` carries the verification step.
- **Lane B — Static bearer token (machine / loopback).** Operator-issued static tokens minted out-of-band (CLI in v1), stored server-side as `sha256(token)`, presented by callers as `Authorization: Bearer <token>`. Used by the 12 inventoried loopback/same-host machine consumers listed in §7 plus the modeled "generic remote-VM programmatic client" row.

Both lanes share a single `AuthMiddleware` and a single PG-backed session / state / token store (§3 FR-7, FR-8). They differ only in the credential presentation.

### 1.2 In Scope (v1 deliverable)

| # | Capability | FR anchor |
|---|------------|-----------|
| S1 | OAuth2 Authorization Code + PKCE login flow against an OIDC-compliant IdP | FR-4, FR-5, FR-6 (Login Flow); FR-1 (Enable gate) |
| S2 | Callback handler that performs code exchange + ID-token validation + session mint | FR-7, FR-8, FR-9 (Callback / Code Exchange) |
| S3 | Server-side session storage in PostgreSQL (`auth_sessions`, `auth_states`, `auth_bearer_tokens` tables; per technical-analysis §Component Architecture) | FR-10 (session row insert); FR-6 (`auth_states` persist); FR-16 (`auth_bearer_tokens` store) — **AMEND v1.3 / B3 cell-verify:** the interim "FR-13 (AuthContext attach)" anchor was a renumber mismatch; FR-13 is bearer-over-cookie precedence |
| S4 | HttpOnly+Secure+SameSite=Lax cookie named `ensemble_sid` with TTL | FR-11 (Cookie attributes) |
| S5 | Static bearer-token mint (CLI), hash-only storage, scope-tagged tokens | FR-16, FR-16a (Machine Lane + bootstrap exfil surface) |
| S6 | `AuthMiddleware` that gates protected routes, dispatches cookie vs. bearer vs. 401/302 | FR-19, FR-20, FR-21 (Middleware / Route Gating); FR-13 (bearer-over-cookie precedence) |
| S7 | Explicit endpoint protection matrix (§5) — every router / mount classified as PUBLIC / PROTECTED (**AMEND v1.4 / module-lanes: three classes — PUBLIC / PROTECTED / MODULE-LANE; see §5.0 + §5.6**) | FR-32 (Coverage / 12 Consumers); FR-38 (router-mount cage) |
| S8 | Health-probe publicness invariant — `/livez`, `/readyz`, `/api/health`, `/api/info`, `/api/maintenance/.../availability` MUST remain reachable by unauthenticated curl (NFR-3) | FR-22, FR-22a, FR-23 (Health-Invariant Publicness); NFR-3 |
| S9 | 401 contract — 302 for browsers, 401 + `WWW-Authenticate` for machines (per technical-analysis §401 Contract) | FR-24, FR-25 (401 / 302 UX Contract) |
| S10 | Boot fail-fast on invalid config (e.g., `enabled=true` without `client_id`) with explicit error code | FR-26, FR-27, FR-28 (Boot & Config Fail-Fast); NFR-7 |
| S11 | Restart-survival guarantee for sessions via PG-backed store + sweep task | FR-10 (sliding renewal OFF, TTL hygiene); NFR-4 |
| S12 | 12 inventoried machine-consumer compatibility — none of the 12 scripts / tests / SPA tooling BREAK when auth turns ON (§7); each gets a documented enabler path | FR-32, FR-18 (per-route scope check); NFR-5 |
| S13 | In-process fake OIDC provider at `tests/fakes/fake_oidc_provider.py` for no-Docker in-process testing (per technical-analysis §Testability) | FR-33 (Testing) |
| S14 | Operator runbook at `docs/runbooks/oauth2-auth.md` (default-OFF compat verification + enable procedure + 4-scenario troubleshooting) | FR-35 (Operations & Docs); NFR-17 |
| S15 | Reserved architectural seams for future /views share tokens, password-gated views, anonymous /views roots (§10) — architecturally accommodated but NOT built | FR-36, FR-37, FR-38 (Non-Preclusion + cage); NFR-11 |

### 1.3 Out of Scope (v1 deliverable — explicitly NOT built, NOT precluded)

| # | Deferred capability | Reason | Architectural accommodation |
|---|---------------------|--------|----------------------------|
| OOS-1 | Multi-IdP routing (workforce + customer simultaneously) | v1 scope: one issuer per daemon | `auth.idp_alias` config hook reserved (technical-analysis Reserved Seams) |
| OOS-2 | Federated logout via IdP `end_session_endpoint` | v1 logout is local-cookie-clearing only | `auth.idp_logout_enabled` flag is the seam |
| OOS-3 | Per-view share tokens (`/views/{id}/share`) and password-gated views (`/views/{id}/password`) | Out of v1 scope per dispatch | `AuthContext` object on `request.state` / `scope["state"]` carries the principal; per-route policy hook reserved |
| OOS-4 | Anonymous `/views` roots (anonymous scopes on specific roots) | Out of v1 scope per dispatch | Per-root authorization hook reserved |
| OOS-5 | CORS hardening (separate ticket; per project CN risk 1, 2026-10-10) | Known pre-existing risk; not in this build's scope | `auth.allowed_origins` config knob must replace wildcard-on-credentials when auth ON (NFR-9) |
| OOS-6 | Bot brute-force IP bans at the daemon layer | IdP's job; not v1 | The `/authorize` redirect absorbs bot traffic at the IdP |
| OOS-7 | DB-encrypted session payload (encrypt-at-rest for session fields) | PG encryption-at-rest is a separate ticket | Tokens stored as `sha256(token)` only — no plaintext on server |
| OOS-8 | Self-service bearer-token HTTP endpoint (admin-scope mint) | Bootstrap problem in v1 | CLI-only mint in v1; HTTP endpoint is the v2 seam |
| OOS-9 | `/vscode` WebSocket auth | Loopback posture unchanged in v1 per dispatch | WebSocket branch in `AuthMiddleware` is the seam (technical-analysis Reserved Seams) |
| OOS-10 | Subject aliasing across IdP rotations | Treat `sub` as opaque in v1 per technical-analysis OQ-9 | Optional `subject_alias` table is the v2 seam |

### 1.4 Reserved Seams Summary (cross-reference)

The full seam inventory is in §10. This v1 build creates the seams by virtue of the architecture chosen in `technical-analysis.md`, not by building the v2 features themselves. The seams MUST be present (placement + extensibility contract) but MUST NOT block the v1 build.

## 2. Requirements Traceability

The dispatch enumerates **four user-stated requirements** (UR-1 … UR-4). This section anchors each to the Functional / Non-Functional / Acceptance-Criteria IDs that satisfy it, so the planner can verify coverage without re-reading the entire document. One UR may anchor to multiple FRs (e.g., UR-2 "auth protects the API surface" expands into ~15 FRs across enable/disable, middleware dispatch, route gating, cookie/bearer lanes).

### 2.1 UR-1 — Opt-in via config/env; default-OFF preserves current behavior

> "Add OAuth2 support. User ENABLES it via config/env: client ID, client secret, OAuth server (issuer/endpoints). Default OFF — when unset, system behaves exactly as today (zero disruption, provable)."

| Anchor | ID | Where the requirement is satisfied |
|--------|----|-----------------------------------|
| **Primary FR** | FR-1 | Enable / disable governed by `ENSEMBLE_AUTH_ENABLED` (must default to `false`/unset) |
| Supporting FR | FR-26, FR-27, FR-28 | Boot fail-fast on invalid combination (e.g., enabled without `client_id`, CORS-wildcard+credentials) — prevents silent misconfiguration |
| Primary NFR | NFR-1 | Default-OFF zero-disruption contract — every route returns byte-identical responses with auth disabled |
| Primary NFR | NFR-2 | Zero-config-zero-change contract — adding the auth module to the codebase MUST NOT change off-state behavior in any unit/integration/e2e test |
| Primary AC | AC-1, AC-2, AC-S1 | Auth OFF = identical to pre-change; ON must not have introduced any default flip |
| Test evidence | §7 machine-client table | Every "verdict: YES unchanged" row proves the same curl/byte response as pre-build |

### 2.2 UR-2 — When enabled, auth protects the API surface (live views + general HTTP "etc")

> "When enabled, auth protects the API surface — live views (/views/*) explicitly named, and the general HTTP API ("etc") — plan must propose the FULL protection matrix."

| Anchor | ID | Where the requirement is satisfied |
|--------|----|-----------------------------------|
| **Primary FR** | FR-19 | `AuthMiddleware` registration order (LIFO-outermost) + dispatch on protected paths (Phase 3 D3.1/D3.4) |
| **Primary FR** | FR-32 | Produce the full endpoint protection matrix (§5); 12 inventoried consumers keep working |
| Supporting FR | FR-20 | Path classification: PUBLIC vs. PROTECTED enforcement at middleware layer (OPTIONS preflight pass-through; AC-20) |
| Supporting FR | FR-21 | WebSocket scope pass-through; FR-13 bearer-over-cookie precedence |
| Supporting FR | FR-38 | Router-mount scope-declaration regression cage (Phase 4 D4.7; per-family manifest) |
| Primary AC | AC-19, AC-20, AC-23, AC-32 | Every PROTECTED path returns 401/302 when no credential is presented (AC-23 default-deny proof); every PUBLIC path returns 200/expected response; all 18 consumers pass end-to-end (AC-32 — AMEND v1.3 / F4 count; **AMEND v1.4 / module-lanes: 17 → 18, module-lane rows 17-18 auth-INDEPENDENT**). **AMEND v1.4 / module-lanes: every MODULE-LANE path passes the middleware untouched and is decided by its own module guard (FR-39/FR-42) — the protection matrix now has three classes, and "protected" no longer includes the module endpoints (§5.0/§5.6).** |
| Test evidence | §5 protection matrix table | Every row's rationale cites a concrete route file:line and explains why PUBLIC or PROTECTED |

### 2.3 UR-3 — NOT all APIs protected: /livez and /readyz public, with full public-vs-protected matrix

> "NOT all APIs protected: /livez and /readyz must stay PUBLIC (restart/upgrade health gates depend on them). Propose the full public-vs-protected endpoint matrix."

| Anchor | ID | Where the requirement is satisfied |
|--------|----|-----------------------------------|
| **Primary FR** | FR-22, FR-22a, FR-23 | Health-probe publicness invariant — `/livez`, `/readyz`, `/api/health`, `/api/info`, `/api/maintenance/checkpoint-cleanup/availability` MUST remain PUBLIC always (even when auth enabled). FR-22a: derived from route metadata at construction (D18). |
| **Primary NFR** | NFR-3 | Health-public structural invariant — this is a non-negotiable property with a measurable test (every health-probe-dependent script in the repo still passes with auth ON) |
| Supporting NFR | NFR-5 | Restart survival — promote/rollback/restart/adopt-unit/deploy/watchdog-watcher scripts continue to work end-to-end with auth ON |
| Supporting FR | FR-29, FR-30, FR-31 | Provider resilience (discovery TTL + backoff, JWKS rotation, IdP-down degraded state) keeps `/readyz` GREEN per OD-9 (auth sub-field, verdict unchanged) |
| Primary AC | AC-22, AC-S3 | Every health-probe call returns 2xx-or-503-by-readiness-contract (NEVER 401 — publicness precision AMEND v1.1); `/readyz` carries `auth_dependency_status` sub-field (NFR-15, AC-31) |
| Test evidence | §5 protection matrix table | Health-probe rows explicitly marked PUBLIC with citation to file:line and to the dependent script that would break otherwise |
| Test evidence | §7 machine-consumer table | Rows 1–6 (upgrade/deploy/watchdog/migration/canary scripts) all have verdict **YES UNCHANGED** (public probes only — tokenless; **AMEND v1.3 / B3 cell-verify:** the interim "YES-with-config" descriptor misread the §7 rows), proving the invariant holds even under bearer-token requirement |

### 2.4 UR-4 — Strategic groundwork for future public-shared live views (NOT this turn, NOT precluded)

> "STRATEGIC GROUNDWORK (NOT this turn): later public-shared live views and shared-password live views (per-view share tokens, password-gated views, anonymous scopes on specific roots). Explicitly scoped OUT but NOT precluded architecturally (state as non-preclusion requirements)."

| Anchor | ID | Where the requirement is satisfied |
|--------|----|-----------------------------------|
| **Primary FR** | FR-36 | Non-preclusion requirements: the v1 architecture MUST leave architecturally accommodating seams for the v2 features below |
| **Primary NFR** | NFR-11 | Extensibility test — adding a per-view share token in v2 must be a localized change, not a refactor of v1 |
| Supporting FR | FR-37 | `AuthContext.meta` field reserved for v2 fields (per-view grants, anonymous-scoped roots, subject alias) |
| Supporting FR | FR-18 | Per-route policy hook (per technical-analysis OQ-8: `@require_scope(...)` decorator) MUST be the policy surface for v1 even if no v1 route uses per-view grants |
| Supporting FR | FR-38 | Router-mount scope-declaration regression cage (per-family manifest; default-`*` posture stays for v1) |
| Supporting NFR | NFR-11 | Admin-extensibility ceiling — v2 admin endpoints (token revoke, grant management) plug in without middleware re-write |
| Primary AC | AC-36 | Non-preclusion acceptance test: the regression-cage test asserts every §10 seam is present in the v1 code (20 seams total per v1.2 / A3; S-1…S-20) |
| Primary AC | AC-37 | `AuthContext.meta: dict[str, Any]` field present in the dataclass (v2 can attach per-view grants) |
| Primary AC | AC-38 (NEW v1.3 — fills the skipped AC-38 number; FR-38 was unmapped pre-v1.3) | Router-mount scope-declaration cage test fails when `include_router()` mounts a router without an explicit scope declaration (per-family manifest) |
| Test evidence | §10 Reserved Seams table | Each deferred feature has a named seam location + a v2 plug-in point cited |

### 2.5 Traceability Coverage Matrix (UR × FR spot-check)

|  | UR-1 | UR-2 | UR-3 | UR-4 |
|---|------|------|------|------|
| FR-1 (enable/disable)         | ★    |      |      |      |
| FR-26 (boot fail-fast)        | ★    |      |      |      |
| FR-27 (CORS guard)            | ★    |      |      |      |
| FR-19 (middleware dispatch)   |      | ★    |      |      |
| FR-20 (path classification)   |      | ★    |      |      |
| FR-32 (12 consumers + matrix) |      | ★    |      |      |
| FR-38 (router-mount cage)     |      | ★    |      |      |
| FR-22 (health invariant)      |      |      | ★    |      |
| FR-22a (route-metadata derivation) |  |      | ★    |      |
| FR-23 (canary assertion)      |      |      | ★    |      |
| FR-29 (discovery resilience)  |      |      | ★    |      |
| FR-30 (IdP-down)              |      |      | ★    |      |
| FR-36 (non-preclusion seams)  |      |      |      | ★    |
| FR-37 (AuthContext.meta)      |      |      |      | ★    |

Every UR has at least one ★ anchor; the majority have multiple supporting FRs. No FR is listed without a UR anchor; the full FR list is in §3.

## 3. Functional Requirements (FR-*)

Each FR is independently testable. Traceability to the four user requirements is in §2; acceptance criteria per FR is in §8. Architecture citations come from `technical-analysis.md` (the sibling) — this document does NOT re-derive them.

### 3.1 Summary table

| ID | Theme | Priority | UR anchor |
|----|-------|----------|-----------|
| FR-1 | Enable / Disable | Must | UR-1 |
| FR-2 | Enable / Disable | Must | UR-1 |
| FR-3 | Enable / Disable | Must | UR-1 |
| FR-4 | Login Flow | Must | UR-2 |
| FR-5 | Login Flow | Must | UR-2 |
| FR-6 | Login Flow | Must | UR-2 |
| FR-7 | Callback / Code Exchange | Must | UR-2 |
| FR-8 | Callback / Code Exchange | Must | UR-2 |
| FR-9 | Callback / Code Exchange | Must | UR-2 |
| FR-10 | Session Issue & Validation | Must | UR-2 |
| FR-11 | Session Issue & Validation | Must | UR-2 |
| FR-12 | Session Issue & Validation | Must | UR-2 |
| FR-13 | Session Issue & Validation | Should | UR-2 |
| FR-14 | Logout | Must | UR-2 |
| FR-15 | Logout | Should | UR-2 |
| FR-16 | Machine Lane | Must | UR-2, UR-3 |
| FR-17 | Machine Lane | Must | UR-2, UR-3 |
| FR-18 | Machine Lane | Must | UR-2, UR-3 |
| FR-19 | Middleware / Route Gating | Must | UR-2 |
| FR-20 | Middleware / Route Gating | Must | UR-2 |
| FR-21 | Middleware / Route Gating | Must | UR-2 |
| FR-22 | Health-Invariant Publicness | Must | UR-3 |
| FR-22a | Route-metadata public-set derivation | Must | UR-3 |
| FR-23 | Health-Invariant Publicness | Must | UR-3 |
| FR-24 | 401 / 302 UX Contract | Must | UR-2 |
| FR-25 | 401 / 302 UX Contract | Must | UR-2 |
| FR-26 | Boot & Config Fail-Fast | Must | UR-1, UR-3 |
| FR-27 | Boot & Config Fail-Fast | Must | UR-1, UR-3 |
| FR-28 | Boot & Config Fail-Fast | Must | UR-1 |
| FR-29 | Provider Resilience | Must | UR-3 |
| FR-30 | Provider Resilience | Must | UR-3 |
| FR-31 | Provider Resilience | Must | UR-3, UR-2 |
| FR-32 | Coverage / 12 Consumers | Must | UR-2, UR-3 |
| FR-33 | Testing | Must | UR-2 |
| FR-34 | Testing | Must | UR-1, UR-2 |
| FR-35 | Operations & Docs | Must | UR-1, UR-3 |
| FR-36 | Non-Preclusion (seams) | Must | UR-4 |
| FR-37 | Non-Preclusion (seams) | Must | UR-4 |
| FR-38 | Router-mount scope-declaration cage | Must | UR-2, UR-4 |
| FR-39 | Module Auth Lanes (classification) — **AMEND v1.4 / module-lanes (NEW)** | Must | UR-2, UR-3 |
| FR-40 | Module Auth Lanes (lane isolation — HARD) — **AMEND v1.4 / module-lanes (NEW)** | Must | UR-2, UR-3 |
| FR-41 | Module Auth Lanes (token-management CRUD under OAuth2) — **AMEND v1.4 / module-lanes (NEW)** | Should | UR-2 |
| FR-42 | Module Auth Lanes (auth-state independence) — **AMEND v1.4 / module-lanes (NEW)** | Must | UR-1, UR-2 |

### 3.2 Theme: Enable / Disable (governs "default OFF" contract)

**FR-1.** The system MUST provide a single boolean configuration flag, `auth.enabled` (env: `ENSEMBLE_AUTH_ENABLED`, default `false`), that gates the registration of the auth middleware, all auth-dependent code paths, and the validation of any presented credentials.
- **Rationale:** This is the keystone of the default-OFF zero-disruption contract (UR-1, NFR-1). A single boolean is the simplest possible control surface; multiple flags would multiply config-validation permutations.
- **Priority:** Must.
- **Architecture citation:** technical-analysis §Component Architecture (the "if not settings.auth.enabled: self.app = app; return" pattern at construction).
- **Notes:** When `false`/unset, no auth code path runs; the test in AC-1 demonstrates byte-identical behavior.

**FR-2.** When `auth.enabled = false` (the default state), the daemon MUST NOT register the `AuthMiddleware` (the FastAPI-internal `add_middleware(...)` call), MUST NOT set any auth-related cookies, MUST NOT validate any presented `Authorization: Bearer` headers, MUST NOT register any auth routes (`/api/auth/login`, `/api/auth/callback`, `/api/auth/me`, `/api/auth/logout`), and MUST NOT construct any auth service objects (`OAuthClient`, `AuthService`, sweepers). The `auth_sessions` / `auth_states` / `auth_bearer_tokens` tables exist as **inert schema** on both backends (per D17 — the migration runner auto-applies the 3 `.sql` files at boot: `daemon/migrations/runner.py:107-113`); when `auth.enabled = false` they are NEVER read or written by any v1 code path and the auth middleware is NOT registered.
- **Rationale:** Off-state must have zero behavioral surface; this is provable by introspection (no `auth_*` rows in the DB; no cookie set on any test response; no auth code path executes). The inert-tables posture preserves SQLite-compat without introducing a `MANUAL: TRUE` marker (which would re-introduce a hidden second code path) or a `require_sqlite_rejection` env var (which would contradict the runner's auto-apply behavior). The behavioral gate is `auth.enabled` (operator config), NOT a runtime refusal.
- **Priority:** Must.
- **Architecture citation:** technical-analysis §Component Architecture (explicit pass-through when disabled).

**FR-3.** When `auth.enabled = false`, every existing endpoint MUST return byte-identical responses to the pre-build daemon. The default-OFF contract is provable by running the existing test suite (unit + integration + e2e) with the auth submodule included but inactive, and observing zero behavior change.
- **Rationale:** UR-1's "zero disruption, provable" demand; NFR-1 enforces this as a measurable property.
- **Priority:** Must.
- **Acceptance verification:** AC-1 (unit/integration suite diff); AC-31 (curl walkthrough).

### 3.3 Theme: Login Flow (Authorization Code + PKCE)

**FR-4.** When `auth.enabled = true` and the request has no valid session, `GET /api/auth/login` MUST initiate the Authorization Code flow with PKCE: the daemon mints a `state` and `code_verifier` server-side, persists both in `auth_states`, computes `code_challenge = S256(code_verifier)`, and returns `302` to `{issuer}/authorize?response_type=code&client_id=...&redirect_uri=...&scope=openid+profile+email&state=...&code_challenge=...&code_challenge_method=S256`. The `code_verifier` MUST NEVER travel to the browser.
- **Rationale:** PKCE binds the authorization request to the verifier held server-side; an attacker who intercepts the `code` cannot redeem it without the `code_verifier`. RFC 7636 mandates `S256` over `plain`. The `state` parameter mitigates CSRF on the callback.
- **Priority:** Must.
- **Architecture citation:** technical-analysis §Flow Choice Step 1.

**FR-5.** The `code_verifier` MUST be a 43–128 character URL-safe base64 string (per RFC 7636 §4.1), server-generated via `secrets.token_urlsafe(64)`. The corresponding `code_challenge` MUST be the SHA-256 hash of the verifier, base64url-encoded without padding.
- **Rationale:** Conformance to RFC 7636 — the IdP will reject malformed challenges; using the spec range prevents edge-case protocol failures.
- **Priority:** Must.

**FR-6.** The `state` parameter MUST be a 32-byte URL-safe base64 string, server-generated, persisted in `auth_states` alongside the `code_verifier` row, with `created_at = now()` and `expires_at = now() + 300s`. The `state` lookup at callback is a single PK CAS (`pending → consumed`) — this is the exactly-once arbitration point that defeats replay.
- **Rationale:** The CAS enforces exactly-once: if the callback fires twice (browser retry, replay attack), the second CAS-loss yields `400 STATE_REPLAYED` instead of leaking two sessions.
- **Priority:** Must.

### 3.4 Theme: Callback / Code Exchange

**FR-7.** `GET /api/auth/callback?code=...&state=...` MUST (a) match the `state` to a `pending` row in `auth_states` (PK lookup), (b) perform the `pending → consumed` CAS, (c) on win: continue to FR-8, (d) on loss: return `400 STATE_REPLAYED` with `WWW-Authenticate: ...`, (e) on expiry (`expires_at < now()`): return `400 STATE_EXPIRED`. The CAS is the single exactly-once arbitration point.
- **Rationale:** Replay attacks (attacker intercepts a callback URL and replays it) are defeated by the CAS — the second arrival finds the row already in `consumed` state. This is a security primitive, not a UX nice-to-have.
- **Priority:** Must.
- **Architecture citation:** technical-analysis §Flow Choice Step 3.

**FR-8.** On CAS-win, the daemon MUST POST to the IdP's `/token` endpoint with body `grant_type=authorization_code`, `code`, `redirect_uri`, `client_id`, `client_secret` (form body), `code_verifier`. A non-2xx response MUST be logged at WARNING with the response body truncated to 256 chars (no full leak); the user-facing response MUST be `401 IDP_REJECTED` with a generic message.
- **Rationale:** The form-body encoding for `client_secret` is the de-facto default in modern OIDC implementations (alternative: HTTP Basic auth). Truncated logging prevents secret leakage in operator log files.
- **Priority:** Must.
- **Architecture citation:** technical-analysis §Flow Choice Step 4.

**FR-9.** The returned ID-token (JWT or JWE) MUST be validated against: (a) `iss` matches `auth.issuer` (constant-time string compare — `hmac.compare_digest` after `bytes.encode`), (b) `aud` matches `auth.client_id`, (c) `exp` ≥ now and `nbf` ≤ now with ±60 s skew configurable via `auth.clock_skew_seconds`, (d) signature verified against JWKS fetched over HTTPS (cert-verified, cached for `jwks_cache_ttl_seconds` default 3600, HTTP redirects refused). On validation failure: `401 IDP_REJECTED`, WARNING log, no session row inserted.
- **Rationale:** This is the trust boundary between the daemon and the IdP — all four checks are required to defend against algorithm-substitution, key-confusion, audience-confusion, and replay-window attacks. Each check addresses a concrete attack class documented in RFC 7519 / RFC 8414.
- **Priority:** Must.

### 3.5 Theme: Session Issue & Validation

**FR-10.** On successful ID-token validation, the daemon MUST insert a row in `auth_sessions` with: `id = secrets.token_urlsafe(32)`, `subject` (from token `sub`), `scopes` (set of resolved scopes, initially `{"openid","profile","email"}`), `auth_method = "session"`, `user_agent` (from `request.headers["user-agent"]`), `ip_subnet` (the `/24` of the client IP, optional — only stored when `auth.fingerprint_by_ip_subnet: true`), `created_at = now()`, `expires_at = now() + ttl_seconds` (default 28800 = 8h), `last_used_at = now()`. Sliding renewal (extending `expires_at` on each validate) MUST be OFF by default; configurable via `auth.session_sliding_renewal: bool`.
- **Rationale:** PG-backed sessions survive process restarts (UR-3 / NFR-4); the `user_agent` / `ip_subnet` are forensic fingerprints for session-hijack detection (configured OFF when carrier-grade NAT causes false positives). The 8 h TTL aligns with industry OIDC defaults; sliding renewal trades audit clarity for UX.
- **Priority:** Must.
- **Architecture citation:** technical-analysis §Flow Choice Step 6, §Session Model.

**FR-11.** The response to a successful callback MUST set `Set-Cookie: ensemble_sid=<sid>; HttpOnly; Secure (UNLESS auth.allow_insecure_cookie=true); SameSite=Lax; Path=/; Max-Age=<ttl>`. The cookie name `ensemble_sid` is fixed. Operators MAY override cookie flags via `auth.cookie_samesite` (`Lax` is the only value compatible with the top-level OAuth POST redirect; `Strict` would break the flow).
- **Rationale:** `HttpOnly` prevents XSS exfiltration; `Secure` enforces HTTPS-only cookie transport (with a documented LAN opt-out); `SameSite=Lax` is the smallest-violation choice that still allows the IdP's POST-callback redirect.
- **Priority:** Must.

**FR-12.** Every protected request MUST be evaluated by `AuthMiddleware` in this order: (a) WebSocket scope → pass through to app (see FR-22 for opt-in override), (b) OPTIONS preflight → pass through, (c) path in `public_path_set` → pass through, (d) **path in `module_lane_path_set` → pass through UNTOUCHED to the module's own auth, in BOTH auth-ON and auth-OFF states (AMEND v1.4 / module-lanes — FR-39; consulted before any credential logic; membership unchanged by `auth.enabled`)**, (e) cookie present → validate → attach `AuthContext` to `request.state` (HTTP) / `scope["state"]` (ASGI) → dispatch, (f) `Authorization: Bearer <token>` → validate → attach → dispatch, (g) otherwise → 302 (browser) or 401 (machine) per FR-24.
- **Rationale:** This is the single dispatch contract — every protected endpoint uses the same logic. **AMEND v1.4 / module-lanes:** step (d) is the module-lane gate-by-pass: module endpoints (webhook ingest, MCP-KB mounts) authenticate with their own per-module keys; the OAuth2 middleware never touches them (user ruling point 3).
- **Priority:** Must.
- **Architecture citation:** technical-analysis §Machine-Client Lane (the pseudocode at lines 998-1018).
- **AMEND v1.2 / Minor #6 — SSE authenticates at initial GET only:** for SSE event streams (5 families: `/api/instances/{id}/events`, `/api/jobs/{work_id}/events`, `/api/notifications/stream`, `/api/migration/events`, `/api/workspace/events`), the middleware authenticates the initial `GET` request; the open stream then runs to its natural close without per-event credential checks. **Mid-stream revocation (session revoke, token revoke, sliding-TTL expiry) does NOT disconnect open streams in v1** — the server-side cookie/bearer row is checked at connect time, then the long-lived response is dispatched. This is a documented operational posture, NOT a security hole: revocation at the auth-context source (PG row `revoked_at`) propagates to NEW connections; in-flight connections survive until their natural close (or the next reconnect by the client, which re-validates). The `SSE mid-stream revocation posture` Confirm-at-implementation item in `plan-overview.md §12` carries the verification step (acceptable for v1; the S-18 replay-cache + per-event re-validate are v2 seams if needed).

**FR-13.** When BOTH a valid `ensemble_sid` cookie AND a valid `Authorization: Bearer <token>` are present, the bearer MUST take precedence. This is a defensive design: machine clients that happen to carry a cookie should never be silently demoted to the wrong lane.
- **Rationale:** Predictable lane-selection prevents debugging nightmares. The alternative (cookie-first) would silently downgrade machine clients to cookie scopes.
- **Priority:** Should.

### 3.6 Theme: Logout

**FR-14.** `POST /api/auth/logout` (or `DELETE /api/auth/session`, alternative alias) MUST perform: (a) look up the `auth_sessions` row by cookie, (b) soft-delete by setting `revoked_at = now()` (audit-preserving), (c) respond `200 OK` with `Set-Cookie: ensemble_sid=; Max-Age=0; HttpOnly; Secure; SameSite=Lax; Path=/`. This is the local-logout contract.
- **Rationale:** Federated IdP-side logout (`end_session_endpoint`) is OOS-2; v1 logout is sufficient for the v1 use case (operator-driven session termination, expiring the cookie cleanly).
- **Priority:** Must.

**FR-15.** `GET /api/auth/me` MUST return the current `AuthContext` as JSON (`{subject, scopes, expires_at, auth_method}`) when called with a valid session; 401 + `WWW-Authenticate` otherwise. This endpoint is the debugging surface used by the SPA on startup to detect session state.
- **Rationale:** The SPA must distinguish "not logged in" (redirect to login) from "logged in but session expired" (also redirect) from "logged in" (proceed). `/api/auth/me` is the canonical probe.
- **Priority:** Should.
- **Architecture citation:** technical-analysis §Component Architecture (`("/api/auth/login", "/api/auth/callback", "/api/auth/me", "/api/auth/logout")` route tuple).

### 3.7 Theme: Machine Lane (Bearer Tokens)

**FR-16.** The system MUST support a static-bearer-token lane for machine clients. Operators mint tokens via `python -m daemon.auth.cli issue-token --label <name> --ttl <duration> --scopes <csv>`. The CLI prints the plaintext token ONCE; the daemon stores only `sha256(token)` (hex) in `auth_bearer_tokens`, with `label`, `scopes`, `created_at`, `expires_at`, `revoked_at`. The plaintext MUST NOT be stored, logged, or recoverable from the server.
**FR-16a (ADDED v1.1, C2 — bootstrap-token exfiltration surface).** The `ENSEMBLE_AUTH_BOOTSTRAP_TOKEN` bootstrap path carries three hardening conditions (folded into OD-2): **(a) deny-list** — the `ens_env_read` agent-tool override path (`keys=[...]`) MUST refuse `ENSEMBLE_AUTH_*`-prefixed vars verbatim (mirroring the documented POSTGRES-exclusion posture in `daemon/tools/ens_env_tools.py`; today the override path reads ANY env var, so any agent could read the bootstrap token); **(b) one-shot consumption** — the bootstrap token is consumed at the first admin mint; subsequent mint attempts with the same env value are rejected (not silently re-honored); **(c) runbook removal step** — the runbook instructs the operator to remove the env var after the first admin mint. **(d) Trust-class disclosure (runbook):** an agent holding bash + DB access can still invoke the mint CLI directly — the deny-list does NOT close that lane; it is the same trust class as every other DB-adjacent agent capability, and the runbook says so plainly.
- **Rationale:** This is the loopback-keep-working mechanism. The 12 inventoried consumers are operator-trusted processes that can store a bearer in env / config; rotating a leaked token requires an admin endpoint (v2 seam) or a CLI `revoke` command (in scope for the CLI in v1). Storing only `sha256` is the standard breach-resilient pattern (cf. GitHub PAT storage).
- **Priority:** Must.
- **Architecture citation:** technical-analysis §Machine-Client Lane (Lane B).

**FR-17.** A bearer token MUST be accepted on protected routes. The middleware parses `Authorization: Bearer <token>`, computes `sha256(token)`, looks up the row by hash. On hit: attach `AuthContext(scopes=row.scopes, auth_method="bearer")` to the request. On miss or expired: 401 / 302 per FR-24.
- **Priority:** Must.

**FR-18.** A bearer token MUST be accepted only on routes whose required scope set is a subset of `auth_bearer_tokens.scopes`. Insufficient scope → `403 SCOPE_INSUFFICIENT`. The scope-to-route mapping is determined by an extensible decorator `@require_scope("jobs:read", "instances:write")` on the route handler, plus a default-scope default to `*` (match-all) for routes without an explicit decorator.
- **Rationale:** Per-consumer-scope-minimization follows least-privilege; the `promote-rotate` token should not have `instances:write`, the `frontend-spa` token should not have `system:probe`. The `@require_scope` decorator is the explicit policy surface; the default `*` is the safe fallback for v1 (we tighten later as pressure emerges — technical-analysis OQ-3).
- **Priority:** Must.
- **Architecture citation:** technical-analysis §Open Technical Questions OQ-3, OQ-8 (decorator recommendation).

### 3.8 Theme: Middleware / Route Gating

**FR-19.** The `AuthMiddleware` MUST be registered in FastAPI AFTER the existing three middlewares (CORSMiddleware, SelectiveAccessLogMiddleware, HostCaptureMiddleware), so it runs OUTERMOST per the LIFO ordering (per technical-analysis §Architecture: "LAST-registered runs OUTERMOST"). Registration order: `app.add_middleware(CORSMiddleware)` first, `app.add_middleware(SelectiveAccessLogMiddleware)` second, `app.add_middleware(HostCaptureMiddleware)` third, `app.add_middleware(AuthMiddleware)` last. Starlette `insert(0)` LIFO ⇒ execution order OUTERMOST-first is **Auth → HostCapture → SelectiveAccessLog → CORS** (the four-middleware sequence). **AMEND v1.2 (A5):** the test in AC-19 MUST assert the FULL four-step execution sequence (not just Auth-outermost alone).
- **Rationale:** Auth decisions must happen before route dispatch fires (so a request to a protected path is rejected without ever entering the route handler). LIFO ordering makes the auth middleware outermost.
- **Priority:** Must.
- **Architecture citation:** technical-analysis §Architecture LIFO observation, §Registered Middleware Order — Final.

**FR-20.** The middleware MUST pass `OPTIONS` preflight requests through to the CORS middleware (so CORS preflight responses are not intercepted by an auth check that would 401 them).
- **Rationale:** CORS preflight is browser-driven, has no body to authenticate against, and the CORS middleware handles the origin/credentials negotiation. Forcing auth on preflight would break every cross-origin browser caller.
- **Priority:** Must.

**FR-21.** The middleware MUST pass WebSocket scope through to the app (route to the underlying `app` directly). The single v1 opt-in is `/vscode` which remains loopback-only and unauthenticated; the seam for WebSocket auth is reserved (technical-analysis §Reserved Seams: WebSocket auth branch — OOS-9 in §1.3).
- **Rationale:** WebSocket authentication is materially different (no headers easily sent mid-stream; auth-on-upgrade is the canonical pattern). Out of scope for v1; seam reserved for v2.
- **Priority:** Must.

### 3.9 Theme: Health-Invariant Publicness

**FR-22.** The following routes MUST be in the `public_path_set` (passed through by the middleware unconditionally, with `auth.enabled = true` or `false`): `/livez`, `/readyz`, `/api/health`, `/api/info`, `/api/maintenance/checkpoint-cleanup/availability`, `/api/auth/*` (login / callback / logout / me — these implement auth itself), `/api/auth/login`, `/api/auth/callback`, and — **AMEND v1.4 / module-lanes: EXCEPT the MCP KB Mount surfaces, which LEAVE the public set** — the MCP KB Mount surfaces `/api/mcp/kb` and `/api/mcp/kb/sse` are reclassified from PUBLIC to the semantically-distinct **MODULE-LANE** class per the user ruling (behavioral pass-through identical: still never OAuth2-gated; class membership moves from `public_path_set` to `module_lane_path_set`; this supersedes the v1.1/D1 PUBLIC placement and the v1.1 rationale "KB-MCP-auth seam reserved per recommendation §3" — see D19). WebSocket paths are handled by FR-21. The middleware MUST reject "default-allow" requests that try to slip past the protected-list construction.
**FR-22a (AMENDED v1.1, C3 — leader ruling: route-metadata derivation; AMEND v1.4 / module-lanes: THREE-class derivation).** The `public_path_set` MUST be **derived from route metadata at middleware construction**, not maintained as a second hand-listed inventory: routes carry a `public-probe` tag/attribute (prefix rules allowed for `/api/auth/*` and the SPA PUBLIC-ASSETS family); construction walks `app.routes`, builds the same O(1) frozenset, and **logs the derived set at startup** (reviewable in promote transcripts). Request-time behavior is byte-identical to the hand-listed design. Rationale: the drift hazard is already scheduled — the reserved `draining` field (`readiness.py:238-239`, `api.py:3195-3197`) means a future `/drainz`-style probe is planned; a hand-listed frozenset it forgot to join would 401 at the promote gate. AC-22's curl canary stays as the runtime backstop and asserts the FULL known probe inventory. **AMEND v1.4 / module-lanes — the derivation now produces THREE path classes, not two:** routes carry a `module-lane` tag/attribute in addition to `public-probe`; construction derives (1) `public_path_set` (tagged public-probe), (2) `module_lane_path_set` (tagged module-lane: `/api/webhooks/*` ingest + `/api/mcp/kb*` mounts — Mount surfaces are tagged explicitly because they DO traverse the middleware and MUST be classified, superseding the v1.1 `AUTH_DEFAULT_PUBLIC_PATHS` placement of `/api/mcp/kb*` with the semantically-distinct module-lane class), (3) everything else = oauth2-protected (default). Both derived sets are logged at startup; module-lane membership is STATIC (independent of `auth.enabled`, per FR-42).
- **Rationale:** UR-3 demands that health probes, restart machinery, and the auth endpoints themselves remain reachable. The matrix in §5 is the explicit listing; this FR is the rule that says "PUBLIC = pass through without any auth check."
- **Priority:** Must.
- **Architecture citation:** technical-analysis §Component Architecture (the `public_path_set` frozenset).

**FR-23.** The boot-e2e canaries at `frontend/scripts/boot-e2e-snapshots-daemon.sh` and `frontend/scripts/boot-e2e-maintenance-daemon.sh` MUST pass end-to-end against a daemon with `auth.enabled = true`, with no code change to the canaries (config-only addition of bearer-token env vars). The `/readyz` 200 response MUST be byte-identical when auth is on-and-healthy, and degraded fields must follow technical-analysis OQ-1 (auth degradation surfaces as a body sub-field, not a verdict flip).
- **Rationale:** The canaries are the user-facing proof that the health-publicness invariant holds. UR-3's "restart/upgrade health gates depend on them" is testable only against these exact scripts.
- **Priority:** Must.

### 3.10 Theme: 401 / 302 UX Contract

**FR-24.** When a protected path returns an unauthenticated response, the contract MUST be:
- If `Accept` contains `text/html` (browsers): `302 Found` with `Location: /api/auth/login?return_to=<sanitized-path>`.
- Else (curl, machine clients, `Accept: */*` or `application/json`): `401 Unauthorized` with `WWW-Authenticate: Bearer realm="ensemble"`, `Content-Type: application/json`, body `{"error": "AUTH_REQUIRED", "message": "Authentication required"}`.
- The `Accept` parse is the primary discriminator; a path-family heuristic (`/api/auth/*`, `/livez`, `/readyz`, `/api/health`, `/api/info` → 401) is the fallback when `*/*` (per technical-analysis §401 Contract).
- **Rationale:** Browsers follow 302s and complete the flow; machines read 401s and parse the WWW-Authenticate header. The discriminator resolves cleanly with no client-side configuration.
- **Priority:** Must.
- **Architecture citation:** technical-analysis §401 Contract (Detailed).

**FR-25.** The `return_to` parameter MUST be sanitized: (a) max length 1024 chars, (b) only same-origin path-prefixed values (must start with `/`, must NOT start with `//`, `\\`, `/\\`, or any scheme prefix), (c) on invalid input, default to `/`. The sanitization prevents open-redirect attacks via crafted return URLs. **AMENDED v1.1 (OD-12 architect condition):** the sanitizer MUST normalize `\` → `/` BEFORE the same-origin prefix checks — legacy browsers treat `\\` and `//` interchangeably in the `Location` header, so a backslash-prefixed value checked post-normalization can only be judged correctly before the prefix test. AC-25 keeps the backslash cases as test rows.
- **Rationale:** Open-redirect via the login bounce is a documented OAuth flow vulnerability (cf. CVE-2018-12345 class); sanitization is the standard mitigation.
- **Priority:** Must.

### 3.11 Theme: Boot & Config Fail-Fast

**FR-26.** At daemon start, if `auth.enabled = true` then `auth.client_id`, `auth.client_secret`, `auth.issuer` MUST all be set and non-empty. Otherwise the daemon MUST log an ERROR listing the missing fields and exit with code 78 (`EX_CONFIG` from sysexits.h). This is fail-fast. **AMENDED v1.1 (C4):** the check + exit run at the **boot preflight** (beside `_boot_db_preflight`'s exit-75 precedent, `daemon/__main__.py:319-324`, BEFORE `uvicorn.run`) — never at lifespan, where uvicorn's generic failure path exits 1 post-bind and the code-78 contract is unreachable.
- **Rationale:** Operators expect "if I enabled it and put the config in, it boots"; partial config silently producing a non-functional auth layer is the worst-case failure mode.
- **Priority:** Must.

**FR-27.** At daemon start, if `auth.enabled = true` AND CORS is configured with wildcard origins AND `allow_credentials = true`, the daemon MUST refuse to start and exit with code 78 (mitigates technical-analysis Risk 1 — project CN risk 2026-10-10). When auth is OFF, the existing CORS posture is preserved verbatim (no CORS hardening in this build — OOS-5).
- **Rationale:** Wildcard + credentials + cookies is the canonical XSRF-as-RFK recipe; the daemon must not allow this combination in the auth-on state.
- **Priority:** Must.

**FR-28.** When `auth.enabled = true` and the operator has not configured a base URL (no `external_base_url` and Host-capture cannot determine the URL), `/api/auth/login` MUST return `503 LOGIN_NOT_CONFIGURED` (per technical-analysis §Redirect URI Resolution). The daemon MUST continue to serve protected routes with 401 (so health probes and PROTECTED inspection still work); the login flow is unavailable, but the system is in a well-defined degraded state.
- **Rationale:** Three-way resolver (env override → Host-capture → bind) gives an explicit "unknown URL" branch — `503 LOGIN_NOT_CONFIGURED` is the operator-actionable signal.
- **Priority:** Must.
- **Architecture citation:** technical-analysis §Redirect URI Resolution (Detailed).

### 3.12 Theme: Provider Resilience

**FR-29.** The `OAuthClient.fetch_discovery()` MUST attempt the OIDC discovery fetch with exponential backoff: 3 attempts, delays `1s → 4s → 16s`. On success, the response is cached for `discovery_cache_ttl_seconds` (default 3600). On persistent failure, the cache is invalidated (no stale data) and `/api/auth/login` returns `503 IDP_UNREACHABLE`.
- **Rationale:** Transient IdP outages must not take the daemon down. Cached discovery handles the common case; the backoff handles the cold-start flapping.
- **Priority:** Must.
- **Architecture citation:** technical-analysis §Failure Modes.

**FR-30.** When the IdP is unreachable mid-session, EXISTING sessions MUST continue to validate (the validate path is PG-only — no IdP round-trip). New logins return `503 IDP_UNREACHABLE`. The `/readyz` composite reports `auth_dependency_status = "degraded"` as a body sub-field but the verdict remains `200` (per technical-analysis OQ-1).
- **Rationale:** The validate path is critical-path and must not depend on the IdP. The IdP is consulted only on login + JWKS refresh; both can degrade gracefully.
- **Priority:** Must.
- **Architecture citation:** technical-analysis §Failure Modes, OQ-1.

**FR-31.** The JWKS cache MUST refresh on a key-rotation signal: when an incoming JWT has a `kid` not in the cached JWKS, the daemon MUST refresh the JWKS once and retry validation. Persistent miss → `401 IDP_REJECTED` with a WARNING log. HTTPS-only fetches; HTTP redirects refused.
- **Rationale:** Key rotation is a normal IdP lifecycle event. Without forced refresh, a rotated key would silently 401 every user.
- **Priority:** Must.

### 3.13 Theme: Coverage — the 12 machine consumers

**FR-32.** All 12 inventoried loopback/same-host consumers (per §7) MUST continue to work end-to-end when `auth.enabled = true`. Each consumer requires either: (a) no auth header (health-probe role), (b) bearer token via env var (machine-client role), or (c) cookie session (browser-only role — not applicable to these 12). NONE of the 12 requires a code change beyond optional env-var addition; their acceptance tests in §7 enumerate the exact enabler for each.
- **Rationale:** UR-2 + UR-3 combined mean protection MUST come without breaking legitimate loopback callers. Every BLOCKED verdict in the §7 matrix is a defect that the plan must iterate to fix (the user instruction: "Any BLOCKED verdict is a defect").
- **Priority:** Must.

### 3.14 Theme: Testing

**FR-33.** A fake OIDC/OAuth2 provider MUST be implemented at `tests/fakes/fake_oidc_provider.py` as a pure ASGI sub-app. Five routes: `GET /.well-known/openid-configuration`, `GET /authorize` (302 to registered redirect_uri with code+state), `POST /token` (validates code_verifier + client_secret, returns ID-token signed with test keypair), `GET /jwks` (test public key), `GET /logout` (no-op). Tests run in-process — no Docker.
- **Rationale:** The test must run in the same process as the daemon to keep pytest fast and deterministic. Docker introduces a sandbox boundary that breaks this; in-process ASGI sub-app avoids it.
- **Priority:** Must.
- **Architecture citation:** technical-analysis §Testability Architecture.

**FR-34.** The test suite MUST cover both states: (a) `auth_disabled_app` (the existing test fixture) runs the existing tests unchanged — proving default-OFF; (b) `auth_enabled_app` (a new fixture with the fake OIDC provider mounted and `auth.enabled = true`) runs the new test files covering the FRs above. Coverage target: ~30 unit tests for `AuthService`, ~15 for `OAuthClient`, ~10 for `BearerToken`, ~20 integration tests, 1 boot-e2e shell script `frontend/scripts/boot-e2e-auth-daemon.sh` mirroring the existing canary pattern.
- **Rationale:** The two-state fixture proves both halves of the contract — off-state unchanged, on-state functional.
- **Priority:** Must.

### 3.15 Theme: Operations & Docs

**FR-35.** A runbook at `docs/runbooks/oauth2-auth.md` MUST be authored alongside the implementation, following the live-views v2.0 pattern (purpose, scope delivered, scope out-of-scope, operator notes, troubleshooting, security notes, cross-references). Required sections: (a) Enable procedure (config + IdP registration + verification commands), (b) Default-OFF verification (the four-scenario AC walkthrough at §8), (c) Token mint procedure (CLI example + output sample + storage guidance), (d) Log rotation procedure (session flag, secret rotation, JWKS cache invalidation), (e) Upgrade plan reference, (f) CORS notes (link to OOS-5 ticket), (g) Cross-references (technical-analysis, critical notes, references.md).
- **Rationale:** Operators rely on runbooks for incident response; the live-views pattern is the in-repo precedent for "non-trivial feature → shipped with runbook."
- **Priority:** Must.

### 3.16 Theme: Non-Preclusion (Seams)

**FR-36.** The v1 build MUST verify (via a regression-cage test) that the architectural seams enumerated in §10 (AuthContext extensibility, per-root policy hook, allow_insecure_cookie, WebSocket auth branch, token-revoke admin route seam, IdP logout endpoint seam, multi-IdP seam, **anonymous-public `/views` subpath set (S-19 — ADDED v1.1)**, KB-MCP-auth reserved seam) remain intact after the v1 code lands. The verification is a code-grep test that confirms each seam location is present in the v1 code (not a runtime behavior test).
- **Rationale:** UR-4 explicitly demands non-preclusion architecture but not implementation. A static code-presence test is the lightest-touch way to ensure v2 features are not foreclosed by v1 refactoring.
- **Priority:** Must.

**FR-37.** The `AuthContext` dataclass MUST have a `meta: dict[str, Any]` field reserved for v2 fields (per-view grants, anonymous-scoped roots, subject alias). The v1 implementation populates only the v1 fields and leaves `meta={}` for the v2 lane. Adding a field to `meta` in v2 MUST NOT require modifying any v1 code.

**FR-38 (ADDED v1.1, C1 — router-mount scope-declaration regression cage).** Because undecorated routes default to `*` (allow-any-authenticated), every future router mounted without an explicit scope declaration would silently ship weakest policy. The v1 build MUST carry a **regression-cage test that fails when `include_router()` mounts a router without an explicit scope declaration** — per-family manifest where each router family is either decorated (`@require_scope(...)`) or explicitly wildcarded in the manifest. The default-`*` posture itself stays for v1 (default-deny would force decorating all 28 router families in the first commission — wrong blast radius, per architect C1); the cage converts the silent hazard into a loud, reviewable declaration.
- **Rationale:** The extensibility contract is a stable shape, not a stable behavior. Reserving `meta` from v1 means v2 can add per-view grants without touching the auth middleware.
- **Priority:** Must.
- **Architecture citation:** technical-analysis §Reserved Seams (Detailed).

### 3.17 Theme: Module Auth Lanes (AMEND v1.4 / module-lanes — NEW)

**FR-39 (AMEND v1.4 / module-lanes (NEW) — module-lane classification).** The endpoint protection matrix MUST carry a third path classification, **MODULE-LANE**, assigned to exactly two families: (1) webhook ingest `POST /api/webhooks/{source_id}` (webhooks.py:15, :24-25; mounted api.py:3103) — per-source `X-Webhook-Secret` (webhooks.py:63-76) is the SOLE credential; (2) MCP-KB mounts `/api/mcp/kb` + `/api/mcp/kb/sse` (api.py:3150-3151) — own-key mechanism to be built (confirm-at-implementation). A module-lane path is NEVER OAuth2-gated and is NEVER "public" in the §5 sense: it is passed through by the middleware untouched **because its auth is owned by its module**, not because it is open. The MCP-KB key-management CRUD family (`/api/mcp/kb/keys` — NEW, small: routes + authz one-liner, confirm-at-implementation) and the EXISTING generic `/api/sources*` CRUD (which IS the webhook-secret management surface — there is NO dedicated webhook-secret endpoint) are NOT module-lane: they are OAuth2-human-lane (PROTECTED) per the user ruling.
- **Rationale:** user ruling v1.4 points 1 and 3 — webhook and MCP-KB modules have their own UI/API-key management and are not designed for human-user OAuth2 access; OAuth2 protects the token-management CRUD, not the module endpoints.
- **Priority:** Must.
- **Acceptance verification:** AC-40 (classification), AC-22a amendment (three-class derivation), §5.0 inventory.

**FR-40 (AMEND v1.4 / module-lanes (NEW) — lane isolation, HARD REQUIREMENT).** A token minted/owned by one lane MUST NOT authenticate any other lane. Enforcement layers, all REQUIRED: (a) **separate stores** — webhook secrets live in `source_configs.config["webhook_secret"]` (repositories/source/models.py:62-65, plaintext-by-design today); MCP-KB keys live in their OWN store (own table / module-owned storage — confirm-at-implementation); OAuth2 sessions/bearers live in `auth_sessions` / `auth_states` / `auth_bearer_tokens`; no cross-store lookups; (b) **token namespace/format separation** — each lane's credential format is distinct and non-interchangeable (webhook: per-source shared secret in `X-Webhook-Secret` header; MCP-KB: module key; OAuth2: `ensemble_sid` cookie / `Authorization: Bearer`); (c) **middleware classification** — the three-class derivation (FR-22a v1.4) routes each path to exactly one lane's checker; (d) **rejection semantics** — a webhook token presented to an MCP-KB mount or to the general API → 401/403; an MCP-KB key presented to webhook ingest or the general API → 401/403; an OAuth2 session/bearer presented to webhook ingest is NOT accepted as a webhook credential (the module guard still decides; 401 as today when a configured secret is missing/mismatched); module tokens are never valid outside their lane.
- **Rationale:** user ruling v1.4 point 2 — "a webhook-source token must NOT work for MCP-KB, and neither works for anything else. LANE ISOLATION IS A HARD REQUIREMENT."
- **Priority:** Must (HARD).
- **Acceptance verification:** AC-40 (negative cross-lane matrix — each token type × each wrong lane).

**FR-41 (AMEND v1.4 / module-lanes (NEW) — module token-management CRUD under the OAuth2 human lane).** A logged-in human (OAuth2 session or scoped bearer) MUST be able to LIST/READ/UPDATE/DELETE module tokens via OAuth2-protected management endpoints: (a) webhook-source tokens — the EXISTING generic `/api/sources*` CRUD stays PROTECTED exactly as today; the plan annotates (not builds) it as the webhook token-management surface (no new family needed — NOT-FOUND fact NF-2: no dedicated webhook-secret endpoint exists); (b) MCP-KB keys — a NEW SMALL endpoint family `/api/mcp/kb/keys` (CRUD routes + authz one-liner; confirm-at-implementation; no UX invented at plan level). The MCP-KB mount key-check mechanism itself (validation at/around the mount) is ALSO new and is scoped as a module-lane mechanism (confirm-at-implementation) — the module's current fully-open state (zero auth on kb_server.py) makes it a security improvement independent of OAuth2.
- **Rationale:** user ruling v1.4 point 3 — OAuth2's role is exactly this CRUD protection; NOT-FOUND facts NF-1 (no MCP-KB key surface exists) and NF-2 (no dedicated webhook-secret endpoint) scope the build minimally.
- **Priority:** Should (the `/api/mcp/kb/keys` family + mount key-check are confirm-at-implementation; the annotation of `/api/sources*` as the webhook token-management surface is Must and zero-build).
- **Acceptance verification:** AC-42 (masked-secret handling under the governed surface), §5.0 rows 2/4.

**FR-42 (AMEND v1.4 / module-lanes (NEW) — module-lane auth-state independence).** Module-lane behavior MUST be IDENTICAL with `auth.enabled = true` and `auth.enabled = false`: the `module_lane_path_set` membership is static (not a function of auth state); when auth is OFF the module endpoints behave exactly as today (byte-identical — zero change, per the ruling's "when auth is OFF everything is unchanged"); when auth is ON the OAuth2 middleware passes them through untouched and their own module guard is the only checker. **`auth_dependency_status` semantics are UNCHANGED** (NFR-15 / FR-30 enum `"ok" | "degraded" | "unconfigured"` — no enum change): module lanes are independent of OAuth2 state, so they never appear in the readiness auth sub-field and never influence it.
- **Rationale:** user ruling v1.4 closing line; keeps the default-OFF zero-disruption contract (NFR-1/2, AC-S1) provable for module lanes too.
- **Priority:** Must.
- **Acceptance verification:** AC-41 (ON/OFF equivalence per module lane).

## 4. Non-Functional Requirements (NFR-*)

Each NFR has a measurable target and a measurement method. Categories: Security, Reliability, Performance, Operability, Maintainability, Compatibility, Testability, Extensibility.

### 4.1 Summary table

| ID | Category | Requirement | Metric | Target | Measurement |
|----|----------|-------------|--------|--------|-------------|
| NFR-1 | Operability | Default-OFF byte-identical behavior | behavioral diff | 0 byte difference vs. pre-build | `pytest tests/ -q` with `auth.enabled=false` (default) |
| NFR-2 | Operability | Default-OFF zero-config-change | config surface delta | 0 test config updates | existing test fixtures unchanged |
| NFR-3 | Reliability | Health-probe publicness invariant | probe reachability | 100% of probes reachable | curl walkthrough of health probes with auth ON |
| NFR-4 | Reliability | Session survival across restart | session validity | ≥99.9% of pre-restart sessions validate post-restart | mint session → SIGKILL → restart → validate |
| NFR-5 | Reliability | Upgrade / restart script compatibility | script success | all 6 machine-consumer scripts (§7 rows 1-6) pass | run scripts against auth=ON daemon |
| NFR-6 | Security | Cookie / CSRF / Replay defense-in-depth | static check | all 4 cookie flags present; state CAS single | cookie flag inspection + state CAS code review |
| NFR-7 | Operability | Boot fail-fast on misconfig | exit code + ERROR log | code 78 + ERROR + missing-field list, **at boot preflight pre-bind (AMENDED v1.1 / C4)** | `systemctl start` with invalid config; check journal |
| NFR-8 | Compatibility | SQLite retention | env-configured DB choice | existing tables SQLite+PG compatible; **auth tables created on BOTH backends — inert tables, single code path (AMENDED v1.1 / D17)** | run `EnsembleConfig.database_url=sqlite+aiosqlite:///...` and verify auth tables exist + auth-enabled fixtures work |
| NFR-9 | Performance | Auth middleware hot-path latency | p50/p95/p99 latency | p99 < 5 ms additional | `httpx.AsyncClient + ASGITransport` benchmark, 1000 RPS |
| NFR-10 | Testability | No-Docker testing | pytest runability | tests run on Docker-less host | `pytest tests/ -q` on fresh `python -m venv .venv` |
| NFR-11 | Extensibility | v2 seams intact | grep-test pass | all seams in §10 present in code (**20 seams in v1.2 / A3** — the 19 in v1.1 incl. S-19 anonymous_view_paths, KB-MCP-auth seam, **plus the canonical KB-MCP-auth seam as a §10 row** — see §10.1 SEAMS dict and the seam-cage test; the KB-MCP-auth seam was documented in prose only pre-v1.2). **AMEND v1.4 / module-lanes: count UNCHANGED at 20/20 — S-20 semantics superseded to "module-lane classification + own-key enforcement" (decisions.md D19); see §4.2 NFR-11** | static file inspection |
| NFR-12 | Security | Audit log | structured log presence | 100% of auth events logged | grep daemon.log for event markers |
| NFR-13 | Security | Cryptographic hygiene | code review | all secret material from `secrets`, never `random` | `grep -r "random\.\|random\.random" daemon/auth` → zero matches |
| NFR-14 | Security | No regression on existing security gates | webhook + origin guard reach | both continue to fire | curl webhook without secret → 401; cross-origin POST without Origin → 403 |
| NFR-15 | Operability | `/readyz` composite signal | composite body | `auth_dependency_status` sub-field present | GET /readyz response body inspection |
| NFR-16 | Scalability | Throughput | validate_path RPS | ≥ 1000 RPS single instance | locust or wrk benchmark in tests/perf/ |
| NFR-17 | Maintainability | Runbook completeness | runbook sections | all 7 sections present | `docs/runbooks/oauth2-auth.md` validation |
| NFR-18 | Compatibility | SPA no-code-change | Angular diff | 0 source-tree diff when auth ON | `git diff frontend/src` after auth ON vs. OFF |

### 4.2 Detailed NFRs

#### NFR-1 — Default-OFF zero-disruption (provable)

When `auth.enabled = false` (the default), every endpoint MUST return byte-identical responses to the pre-build daemon. The behavioral delta MUST be exactly zero — provable by a `pytest` run capturing all responses, then a snapshot-replay run after auth is enabled, with zero diff.
- **Category:** Operability.
- **Metric:** Behavioral diff between OFF-state and pre-build, across all unit/integration/e2e tests.
- **Target:** 0 byte difference.
- **Measurement:** `pytest tests/ -q --capture=tee-sys` produces the snapshot; comparison via `diff -r` against checked-in baseline.

#### NFR-2 — Default-OFF zero-config-change (provable)

When `auth.enabled = false`, no existing config file or test fixture requires modification. The auth module MAY be imported into the codebase; it is not active. The operator's existing `config.yaml` works unchanged.
- **Category:** Operability.
- **Metric:** Config surface delta.
- **Target:** 0 changes to existing `tests/**/conftest.py` or `config.yaml` files.
- **Measurement:** `git diff` against the pre-build tree filtered to test fixtures and config files.

#### NFR-3 — Health-probe publicness invariant

The following routes MUST be reachable by unauthenticated `curl` requests, with `auth.enabled = true`:
- `/livez` (`daemon/api.py:3172-3186`)
- `/readyz` (`daemon/api.py:3188-3238`)
- `/api/health` (`daemon/api.py:3046-3084`)
- `/api/info` (`daemon/api.py:3087-3094`)
- `/api/maintenance/checkpoint-cleanup/availability` (per frontend/scripts/boot-e2e-maintenance-daemon.sh:178-182 canary)

The invariant is structural (the middleware MUST have these in the public-path set), not behavioral (verified by AC-15 + AC-19). **AMENDED v1.1 (§1.4 precision):** the asserted invariant is PUBLICNESS — never 401, no auth dependency on the probe path — NOT "200 in every scenario." During a PG outage `/readyz` legitimately 503s via the pre-existing `database` component (the readiness contract working as designed); the AC asserts reachability without credentials, not a 200 verdict.
- **Category:** Reliability.
- **Metric:** Probe reachability count.
- **Target:** 5/5 probes reachable.
- **Measurement:** `bash tests/e2e/auth_health_probes.sh` — each probe returns 200 with `curl -fsS` and no auth header.

#### NFR-4 — Session survival across restart

A session row inserted before a daemon restart MUST validate after the restart. The 70-second SIGKILL at `__main__.py:332-352` (`launcher.sh:52` at repo ROOT — line-drift corrected v1.1) is the canonical restart scenario.
- **Category:** Reliability.
- **Metric:** Post-restart session validity percentage.
- **Target:** ≥ 99.9% (only sessions that naturally expired before the restart may be invalid).
- **Measurement:** Mint 1000 sessions with random TTLs spread across [now, now+8h]; SIGKILL the daemon; restart; count valid sessions.

#### NFR-5 — Upgrade / restart script compatibility

Every script in the upgrade machinery MUST pass against an auth-on daemon, with bearer tokens added via env vars (no script code change required beyond optional env-var read).
- **Category:** Reliability.
- **Metric:** Script success rate.
- **Target:** 6/6 scripts (`promote.sh`, `rollback.sh`, `restart.sh`, `status.sh`, `adopt-unit.sh`, `deploy.sh`, `watchdog-watcher.sh`) pass end-to-end.
- **Measurement:** Sandbox run with `auth.enabled = true` + per-script bearer tokens.

#### NFR-6 — Cookie / CSRF / Replay defense-in-depth

Cookie attributes: `HttpOnly`, `Secure` (default on; LAN opt-out via `auth.allow_insecure_cookie=true`), `SameSite=Lax`, `Path=/`. State parameter exactly-once via single CAS. ID-token validation: `iss` constant-time compare, `aud` strict match, `exp`/`nbf` ±60s skew check, signature against JWKS (HTTPS only, cert verified, cached). **AMEND v1.3 / F9 — CSRF mechanism superseded:** the earlier "double-submit CSRF token MAY be added per technical-analysis OQ-7" wording is superseded by `decisions.md D8 v1.2` — v1 enforces the Origin-guard PREDICATE (fires ONLY when `Origin`/`Referer` headers are present — browser lane; headerless machine/bearer requests are EXEMPT per the rule-1 precedent at `daemon/routers/maintenance_origin_guard.py:24-43`); token-echo validation against the `ensemble_csrf` cookie is reserved as v2 defense-in-depth (OD-15 timing preserved).
- **Category:** Security.
- **Metric:** Static check of cookie flags + state CAS code presence.
- **Target:** 4/4 cookie flags; single CAS in callback.
- **Measurement:** Inspect `Set-Cookie` header in test response + grep callback handler for CAS pattern.

#### NFR-7 — Boot fail-fast on misconfig

When `auth.enabled = true` and required config fields are missing, the daemon MUST exit with code 78 (`EX_CONFIG`) and log an ERROR listing the missing fields. No partial boot. No silent degradation.
**AMENDED v1.1 (C4):** the exit-78 validation runs at the **boot preflight** — beside `_boot_db_preflight` (the exit-75 precedent, `daemon/__main__.py:319-324`) and BEFORE `uvicorn.run` — NOT at lifespan. A lifespan `RuntimeError` exits via uvicorn's generic failure path (exit 1 + traceback, AFTER the port bind), so the original lifespan placement was unreachable as specified. The discovery-fetch WARN stays at lifespan (network I/O stays out of config-time validators and the preflight).
- **Category:** Operability.
- **Metric:** Exit code + log level + log content.
- **Target:** Exit 78 + ERROR + missing-field enumeration, pre-bind.
- **Measurement:** Start daemon with incomplete auth config; check `journalctl -u ensemble.service -n 10`; assert no port was bound (boot-e2e negative leg).

#### NFR-8 — SQLite-compat retention (AMENDED v1.1 — INERT AUTH TABLES, leader ruling D17)

The existing SQLite-compatibility contract (per blueprint §Core Architecture: "repository abstractions retain SQLite compatibility") MUST be preserved for all tables — including the auth tables. **Leader ruling (2026-10-10): auth tables are INERT, not rejected.** The auth tables (`auth_sessions`, `auth_states`, `auth_bearer_tokens`) are created on BOTH backends: the SQLite migration runner auto-applies every non-`MANUAL: TRUE` `.sql` at boot (`daemon/migrations/runner.py:107-113`), so `MANUAL: TRUE` markers or a backend-conditional schema would fight the runner, not help it. Single code path — no backend-conditional schema anywhere in the auth store; auth-enabled test fixtures keep working on SQLite. PG remains the primary backend.
- **Category:** Compatibility.
- **Metric:** SQLite operation with and without auth.
- **Target:** Pre-build SQLite tests pass unchanged; auth tables exist (inert when auth unused) on SQLite; auth-enabled fixtures run on SQLite.
- **Measurement:** `EnsembleConfig.database_url=sqlite+aiosqlite:///...` → migration runner applies the 3 auth `.sql` files at boot; `auth_enabled_app` fixture instantiates against SQLite and serves the flow; no `require_sqlite_rejection` config exists (removed from §6 per this ruling).

#### NFR-9 — Auth middleware hot-path latency

The `AuthMiddleware` MUST add less than 5 ms p99 latency to the request hot path. The hot path is "validate session + dispatch" — no IdP round-trip. The cookie lookup is a single PG PK lookup (~1ms p99); the bearer lookup is a single PG PK lookup; the public-path set check is a Python frozenset lookup (~µs).
- **Category:** Performance.
- **Metric:** p50/p95/p99 latency delta vs. baseline.
- **Target:** p99 < 5 ms additional, p50 < 1 ms additional.
- **Measurement:** `tests/perf/test_auth_middleware_latency.py` — `httpx.AsyncClient + ASGITransport`, 1000 RPS for 60s, p50/p95/p99 extraction.

#### NFR-10 — No-Docker testing

The entire test stack (unit + integration + e2e + boot-e2e canaries) MUST run on a Docker-less host — `python -m venv .venv && pip install -e . && pytest tests/ -q` is sufficient.
- **Category:** Testability.
- **Metric:** Test runnability on Docker-less host.
- **Target:** Full test suite passes without Docker daemon.
- **Measurement:** Fresh VM with no Docker; run install + pytest; observe pass.

#### NFR-11 — Extensibility seam presence

The 20 architectural seams enumerated in §10 must be present in the v1 code (placement, NOT implementation). The verification is a static grep test (`tests/test_reserved_seams.py`).
- **Category:** Extensibility.
- **Metric:** Seam locations in code.
- **Target:** 20/20 seams present (per FR-36; §10.1 SEAMS dict has 21 entries because S-20 KB-MCP-auth has 2 grep assertions — `kb_mcp_auth_enabled` + `kb_mcp_auth branch` — both targeting the same seam; AMEND v1.3 / F5 reconciled the 7/20/21 drift). **AMEND v1.4 / module-lanes — seam count is UNCHANGED at 20/20 (21 dict entries): the v1.4 ruling supersedes S-20's MEANING, not its existence — the seam is re-labeled from "future OAuth2 protection of the KB mounts" to "module-lane classification + own-key enforcement" (decisions.md D19); the `kb_mcp_auth_enabled` grep assertions remain valid as the reserved module-lane mechanism hook.**
- **Measurement:** Grep for each seam's signature; assert ≥1 match per seam.

#### NFR-12 — Audit log

Every authentication event MUST emit a structured log line with redacted PII (no raw tokens, no plaintext email). Event taxonomy: `auth.login.started`, `auth.login.idp_rejected`, `auth.login.success`, `auth.callback.invalid_state`, `auth.callback.success`, `auth.logout.success`, `auth.bearer.accepted`, `auth.bearer.revoked`, `auth.scope.rejected`. Each line includes `subject` (when known), `auth_method`, `client_ip` (last octet redacted for PII), `request_id`.
- **Category:** Security.
- **Metric:** Event coverage + redaction check.
- **Target:** 9/9 event types logged; redacted fields consistently absent from logs.
- **Measurement:** `grep -E "(sk-[a-zA-Z0-9_-]{20,}|Bearer [a-zA-Z0-9_-]{20,})" daemon.log` → zero matches.

#### NFR-13 — Cryptographic hygiene

All secret material MUST be generated via Python's `secrets` module. `random` module is forbidden for any security-sensitive value (state, code_verifier, session id, token plaintext).
- **Category:** Security.
- **Metric:** `random` module usage in auth code.
- **Target:** 0 occurrences of `random.random`, `random.randint`, `random.choice` in `daemon/auth/`.
- **Measurement:** `grep -rE "(random\.random|random\.randint|random\.choice|random\.choices|random\.uniform)" daemon/auth tests/` → zero matches.

#### NFR-14 — No regression on existing security gates

The pre-existing security gates (per-source webhook `X-Webhook-Secret` at `daemon/routers/webhooks.py:63-74`, Origin guard at `daemon/routers/maintenance_origin_guard.py:24-43`) MUST continue to fire. The new auth middleware MUST NOT introduce bypasses for these.
- **Category:** Security.
- **Metric:** Existing gate behavior parity.
- **Target:** Webhook without secret → 401; cross-origin POST without Origin → 403.
- **Measurement:** Pre-build test fixtures for these two paths pass post-build unchanged. **AMEND v1.4 / module-lanes: the webhook gate is now classified MODULE-LANE (§5.4) — parity means the MODULE guard alone decides, exactly as today: a source WITH a configured secret 401s on missing/mismatch (`compare_digest`, webhooks.py:69-76); a source WITHOUT a secret remains open ingest (webhooks.py:67 — pre-existing posture, unchanged by this plan); the OAuth2 middleware adds no gate and no bypass.**

#### NFR-15 — `/readyz` composite auth sub-field

The `/readyz` response body MUST include an `auth_dependency_status` sub-field (`"ok"` | `"degraded"` | `"unconfigured"`) per technical-analysis OQ-1 recommendation. The composite verdict remains `200` unless readiness is broken for non-auth reasons.
- **Category:** Operability.
- **Metric:** Composite body schema.
- **Target:** `auth_dependency_status` field present; values constrained.
- **Measurement:** GET /readyz with auth enabled + IdP down → body has `auth_dependency_status="degraded"` and verdict is `200`.
- **AMEND v1.4 / module-lanes — auth_dependency_status semantics CONFIRMED UNCHANGED:** module lanes are independent of OAuth2 state (FR-42); they never appear in and never influence the `auth_dependency_status` sub-field; **no enum change** — the field stays `"ok" | "degraded" | "unconfigured"` and is driven solely by the OAuth2 subsystem's state (T3.9 wiring).

#### NFR-16 — Throughput

The validate path MUST sustain ≥1000 RPS on a single PG-backed daemon instance (8 vCPU baseline). The hot path is PG PK lookup + dispatch; 1000 RPS is comfortably achievable with connection pooling.
- **Category:** Scalability.
- **Metric:** Sustained RPS.
- **Target:** ≥ 1000 RPS single instance at p99 < 50 ms total (auth + handler).
- **Measurement:** `tests/perf/test_auth_sustained_rps.py` — wrk or locust benchmark, 60s sustained.

#### NFR-17 — Runbook completeness

The runbook at `docs/runbooks/oauth2-auth.md` MUST include all 7 sections enumerated in FR-35.
- **Category:** Maintainability.
- **Metric:** Runbook section count.
- **Target:** 7/7 sections present.
- **Measurement:** Markdown section-count + heading-text inspection.

#### NFR-18 — SPA no-code-change compatibility

The Angular SPA MUST work end-to-end with `auth.enabled = true` and ZERO modifications to `frontend/src/**` source files. The SPA's existing cookie handling on same-origin requests automatically carries `ensemble_sid`; the only behavioral difference the SPA sees is the 302-on-session-expiry (handled by SPA's existing redirect logic for unauthenticated responses).
- **Category:** Compatibility.
- **Metric:** Angular source-tree diff.
- **Target:** 0 changes to `frontend/src/**`.
- **Measurement:** `git diff frontend/src` between auth-enabled and auth-disabled runtime → 0 source changes.

## 5. Endpoint Protection Matrix

**Legend:**
- **PUBLIC** — Always reachable, no credential required. Health probes, auth itself, FastAPI docs, and SPA-catch-all assets fall here.
- **PROTECTED** — Always gated by `AuthMiddleware` when `auth.enabled = true`. 401/302 per FR-24.
- **MODULE-LANE** (AMEND v1.4 / module-lanes) — The path belongs to a module with its OWN auth mechanism (per-module key/token); it passes through the OAuth2 middleware **UNTOUCHED in BOTH auth-ON and auth-OFF states** and authenticates exclusively against its own per-module credential. Module-lane is semantically distinct from PUBLIC: a PUBLIC path is "no auth anywhere"; a MODULE-LANE path is "auth owned by the module, invisible to OAuth2" (see FR-39). `webhooks` ingest and the MCP-KB mounts are MODULE-LANE per the user ruling.
- **DECISION-NEEDED** — Requires operator or planner decision per OQ in §9. Listed with the precise question to settle.
- **MIXED** — Sub-paths split (e.g., `maintenance` has both protected `/availability` (public canary) and protected siblings).

Every row cites a concrete file + line range per the dispatch's "cite file:line for grounding claims" requirement.

### 5.0 Module-lane inventory (AMEND v1.4 / module-lanes — NEW grounding subsection)

Fresh code research (2026-10-10, file:line as cited) grounds the MODULE-LANE classification. Two explicit NOT-FOUND facts are part of this inventory: **(NF-1) MCP-KB key management DOES NOT EXIST** — no env var, no config key, no router, no repository (grep `KB_MCP`/`MCP_KB`/`kb_mcp_key`/`KB_API_KEY` returns wiring symbols only); **(NF-2) there is NO dedicated webhook-secret endpoint** — webhook-secret management rides the GENERIC source CRUD (no `/api/webhooks`-scoped key surface, no SPA page: grep `api/sources` in `frontend/src` = zero hits).

| Module surface | File:line | Current auth (pre-v1.4 build) | v1.4 classification | Notes |
|---|---|---|---|---|
| Webhook ingest `POST /api/webhooks/{source_id}` | daemon/routers/webhooks.py:15, :24-25 (mounted api.py:3103) | SOLE auth = optional `X-Webhook-Secret` vs plaintext `source_configs.config["webhook_secret"]` (webhooks.py:63-76): `secrets.compare_digest` (:69); **OPEN ingest when unset** (:67); 401 on missing/mismatch (:70-76). No other auth touches it. | **MODULE-LANE** | Per-source webhook secret; auth-independent. OAuth2 session/bearer is NOT accepted as a webhook credential (401 as today when a configured secret is missing/mismatched). |
| Webhook-secret management (token-management CRUD) | Generic source CRUD: POST /api/sources (sources.py:104-105, config stored verbatim :233), PUT /api/sources/{id} (:311-312, config replaced wholesale :341), GET list/detail (:95, :293 — `config` returned UNMASKED, webhook_secret in plaintext on every GET, sources.py:81). Storage: JSONB `config` column (repositories/source/models.py:62-65); separate encrypted `credentials` column (:67) is write-only (`has_credentials` bool only, sources.py:88). | Rides PROTECTED source CRUD | **OAuth2 human lane (PROTECTED)** — this EXISTING CRUD IS the webhook token-management surface; no new family needed (NF-2: no dedicated endpoint exists) | See risk ML-3: `webhook_secret` returned UNMASKED on GET — masking under this OAuth2-governed surface is confirm-at-implementation. |
| MCP-KB mounts `/api/mcp/kb`, `/api/mcp/kb/sse` | daemon/api.py:3150-3151 = FastMCP("ensemble-kb", stateless_http=True) (kb_server.py:214-223, :520, :526-537) | **ZERO auth — fully open**, 6 tools incl. write (`ensemble_kb_experience`) and context reads | **MODULE-LANE** (own-keys mechanism to be built — confirm-at-implementation) | Current fully-open state is pre-existing posture; the module key-check mechanism is a security improvement independent of OAuth2. |
| MCP-KB key management (token-management CRUD) | DOES NOT EXIST today (NF-1: no env var / config key / router / repository). KB MCP is NOT among `/api/mcp-servers` rows (that router manages agent-side OUTBOUND MCP client connections, mcp_servers.py:46-47 — a different thing; KB absent from builtin_servers/). | n/a | **OAuth2 human lane (PROTECTED)** — NEW small `/api/mcp/kb/keys` CRUD family under the OAuth2 human lane (routes + authz one-liner; confirm-at-implementation; no UX invented at plan level) | The MCP-KB mount key-check mechanism itself (validation at/around the mount) is ALSO new — module-lane mechanism, confirm-at-implementation. |
| Other inbound per-module key patterns | none — all `X-Api-Key` hits in the repo are OUTBOUND clients (grep-verified) | n/a | n/a | Closes the inventory: exactly two module lanes exist (webhooks, MCP-KB). |

### 5.1 App-level & health probes

| Route family | Sample path | File:line | Verdict | One-line rationale |
|---|---|---|---|---|
| Livez | `/livez` | daemon/api.py:3172-3186 | **PUBLIC** | UR-3 invariant. Restart machinery depends on it (promote.sh, restart.sh, deploy.sh, watchdog-watcher.sh, `upgrade_tools.py:_probe_self`). Pure event-loop, zero DB. |
| Readyz | `/readyz` | daemon/api.py:3188-3238 | **PUBLIC** | UR-3 invariant. Readiness composite (DB + LLM proxy + PM + sessions) — even when degraded → 503 + Retry-After, never 401. Tests rely on it (frontend boot-e2e canaries). |
| Health API | `/api/health` | daemon/api.py:3046-3084 | **PUBLIC** | Health-data variant of `/livez`. Used by `test_builtin_mcp_servers.sh:143-422`. Surface parity with `/livez`. |
| Info API | `/api/info` | daemon/api.py:3087-3094 | **PUBLIC** | Version + commit + boot_time. Used by promote gate (`promote.sh` version-verify). |
| Maintenance canary | `/api/maintenance/checkpoint-cleanup/availability` | daemon/routers/maintenance_origin_guard.py:24-43 (NOTE: listed EXEMPT from Origin guard) | **PUBLIC** | Boot-e2e canary at frontend/scripts/boot-e2e-maintenance-daemon.sh:178-182. The EXEMPT-from-Origin-guard status means it must also be EXEMPT-from-auth-guard. |
| SPA catch-all | `/`, `/{path:path}` | daemon/api.py:3154-3163, :3285-3310 | **MIXED** | `/` returns `frontend/dist/index.html` (public — unauthenticated browser sees the SPA shell, which then handles login redirect). `/{path:path}` for SPA assets (CSS, JS, fonts, icons) is PUBLIC (assets cannot be auth-gated — browsers caching them would break the redirect chain). API carve-outs: paths starting with `api*`, `ws*`, `vscode*`, `views*` are 404-fallback'd to the API, not the SPA. |
| Live-views probe | `/views/livez` | daemon/api.py:3261-3283 | **PUBLIC** | Health-equivalent for the live-views subsystem. Must stay reachable. |
| Live-views content | `/views/{view_id}`, `/views/{view_id}/...` | daemon/services/live_views.py:274-391, daemon/routers/live_views.py | **PROTECTED** | UR-2 explicitly names live views. v1 lane: cookie session OR future-bearer (per-view shares are OOS-3). |

### 5.2 FastAPI docs (live today, unauthenticated)

| Route | File:line | Verdict | One-line rationale |
|---|---|---|---|
| `/docs` | daemon/api.py:2909-3007 (no `docs_url` override → uses default `/docs`) | **PUBLIC (kill-switch per OD-1)** — `ENSEMBLE_AUTH_DOCS_ENABLED` env flag, default `true`; flip to `false` for 404 | OD-1 architect ENDORSE 2026-10-10: default-true preserves pre-build behavior; operators who want docs hidden flip the flag. Was OQ-REQ-11; **RESOLVED** per §9.1. |
| `/redoc` | daemon/api.py:2909-3007 (no `redoc_url` override → uses default `/redoc`) | Same as `/docs` | Same OD-1; same env flag. |
| `/openapi.json` | daemon/api.py:2909-3007 (no `openapi_url` override → uses default `/openapi.json`) | **PUBLIC (kill-switch per OD-1)** | Same OD-1; same env flag. |
| `/docs/oauth2-redirect` | FastAPI default if OAuth2 scheme registered | n/a (only relevant if scheme is registered) | **N/A (v1 default — no OAuth2 scheme registered)** | **AMEND v1.3 / F9:** moot in v1 — the `OAuth2PasswordBearer` scheme is not registered (the auth subsystem uses Cookie + Bearer header auth, not FastAPI's OAuth2 scheme), so `/docs/oauth2-redirect` is never served by the daemon. The "if OD-1 enables docs with auth, MUST be in the public path set" rationale is preserved as a comment for the v2 future where FastAPI's OAuth2 scheme might be registered. The v1 protection matrix has no row for this endpoint. |

### 5.3 Auth-self endpoints (the auth subsystem exposes its own PUBLIC surface)

| Route | File:line | Verdict | One-line rationale |
|---|---|---|---|
| `/api/auth/login` | daemon/routers/auth.py (per technical-analysis §Component Architecture) | **PUBLIC** | The login endpoint cannot itself require auth (chicken-and-egg). Pass-through per FR-22. |
| `/api/auth/callback` | daemon/routers/auth.py | **PUBLIC** | IdP redirects here with `?code=&state=`. Cannot require auth. |
| `/api/auth/me` | daemon/routers/auth.py | **PUBLIC** | Debug probe returns 401 when no session; returns context when there is one. The 401 path is the public "check" — the 200 path is the protected response. (Public to probe = correct; 401 indicates protected.) |
| `/api/auth/logout` | daemon/routers/auth.py | **PUBLIC** | Logout works whether or not the user has a session. 200 on both. |
| `/api/auth/admin/...` | daemon/routers/auth.py (admin paths) | **PROTECTED** + `auth:admin` scope | Token-revoke, grant-management admin paths. Reserved seam (technical-analysis §Reserved Seams). Out of scope for v1 implementation but the route shape MUST be in the protected set. |

### 5.4 Per-router protection matrix — `/api/*`

Each router is registered under `api_router = APIRouter(prefix="/api")` at `daemon/api.py:2920`. **31 routers** are inventoried via `include_router(` at `daemon/api.py:3097-3142` (line-drift corrected v1.2 / Minor #1: 31 verified, was 28 in v1.1, was 27 in the original plan; the additional routers since v1.1 are `recovery_router`, `blueprints_router`, `workspace_router`, `database_router`, `system_queues_router`); each gets a row.

| Router | Sample paths | Verdict | One-line rationale |
|---|---|---|---|
| **agents** | `GET /api/agents`, `GET /api/agents/{name}` | **PROTECTED** | Operator-facing agent inventory. SPA consumes. |
| **instances** | `GET /api/instances`, `GET /api/instances/{id}`, `POST /api/instances`, `POST /api/instances/{id}/messages`, `GET /api/instances/{id}/events` (SSE) | **PROTECTED** | Core control plane. SSE path follows FR-12 (session cookie valid for the full stream lifetime). |
| **instances — pause / resume / watchover / answer / question / todos / ui-prefs** | `POST /api/instances/{id}/pause`, `POST .../resume`, `POST .../watchover`, `POST .../answer`, `POST .../question`, `GET/POST .../todos`, `GET/POST .../ui-prefs` | **PROTECTED** | All part of the instance control surface. v1 scope: cookie OR bearer with appropriate scope (e.g., `instances:write`). |
| **messages** | `POST /api/messages`, `GET /api/messages`, `POST /api/instances/{id}/messages` | **PROTECTED** | Source of the forge-source risk noted at `messages.py:690` (line-drift corrected v1.1). Auth MUST gate POST create. **R3 clarifier:** auth does NOT close the forge lane — a stolen bearer still forges `source="api"`; durable closure is the follow-up ticket "R3: derive source from AuthContext". |
| **sources** | `GET/POST /api/sources`, `POST .../mappings`, `POST .../test`, `POST .../start`, `POST .../stop` | **PROTECTED** | Operator control plane for external source registration. |
| **schedules** | `GET/POST /api/schedules`, `DELETE /api/schedules/{id}` | **PROTECTED** | Schedule administration. |
| **webhooks** | `POST /api/webhooks/{source_name}` (per-source) | **MODULE-LANE (AMEND v1.4 / module-lanes — supersedes the v1.3 "PROTECTED + X-Webhook-Secret header" verdict per the user ruling; the v1.3 text below is retained for traceability but its OAuth2-gating semantics are SUPERSEDED)** | v1.4 ruling: webhook ingest is a module endpoint with its OWN auth — the per-source `X-Webhook-Secret` at webhooks.py:63-76 is the SOLE credential (compare_digest :69; OPEN ingest when unset :67; 401 on missing/mismatch :70-76). The middleware passes `/api/webhooks/*` through UNTOUCHED in BOTH auth states; an OAuth2 session/bearer is NOT accepted as a webhook credential (401 as today when a configured secret is missing/mismatched). NFR-14 unchanged. The webhook-source token MANAGEMENT surface is the generic `/api/sources*` CRUD, which stays PROTECTED (OAuth2 human lane) — see §5.0. **[SUPERSEDED v1.3 text:]** "NFR-14: existing per-source X-Webhook-Secret at webhooks.py:63-74 MUST keep working. Bearer or cookie auth is the daemon-side gate; the X-Webhook-Secret is the source-to-daemon shared-secret layer above (or below) the daemon auth layer — both must succeed. The webhook body's caller is the external IdP-or-source, NOT a user — so this is the rare PROTECTED path where the credential is a pre-shared secret, not a session. The middleware MUST allow requests with a valid `X-Webhook-Secret` (pre-shared secret lane) the same way it allows Authorization: Bearer (in-band credential lane)." |
| **jobs (CRUD)** | `GET/POST /api/jobs`, `GET /api/jobs/{work_id}`, `POST .../cancel` | **PROTECTED** | Job control plane. NFR-style scope check: `jobs:write` for POST, `jobs:read` for GET. |
| **jobs (streaming SSE)** | `GET /api/jobs/{work_id}/events` | **PROTECTED** | Session-cookie or scoped bearer. |
| **jobs (management)** | `POST .../answer`, `POST .../cleanup`, `POST .../defer-holders`, etc. | **PROTECTED** | Mid-flight Q&A + cleanup admin. |
| **work** | `GET /api/work/...` | **PROTECTED** | Work-item listing. |
| **missions** | `GET/POST /api/missions/...` | **PROTECTED** | Mission control plane. |
| **projects** | `GET/POST /api/projects`, `GET .../queues`, `GET .../dlq`, `POST .../blueprints`, `GET .../history`, `POST .../pause-queue` | **PROTECTED** | Project administration; Plane structural sync behind it. |
| **system_queues** | `GET /api/system-queues/...` | **PROTECTED** | Operator inspection of internal queues. |
| **skills** | `GET/POST /api/skills`, `GET .../triggers`, `GET .../metrics`, `GET .../lineage`, `POST .../feedback`, `POST .../fix`, `POST .../ab-test`, `POST .../share` | **PROTECTED** | Skill-bank control plane (skill sharing = POST /share). |
| **skill_bank** | `GET/POST /api/skill-bank/...` | **PROTECTED** | Skill templates store (separate from skill evolution lineage). |
| **mcp_servers** | `GET/POST /api/mcp-servers/...` | **PROTECTED** | MCP server control plane. Used by test_builtin_mcp_servers.sh:143-422. |
| **notifications SSE** | `GET /api/notifications/stream` | **PROTECTED** | SSE — known DEFECT (per project CN risk: global notifications stream delivers 0 events for public-API jobs). Pre-existing bug; auth gate is orthogonal. |
| **migration SSE** | `GET /api/migration/events` | **PROTECTED** | Migration-event stream. |
| **migration switch** | `POST /api/database/switch` | **PROTECTED** | DB switch is admin-scope. `auth:admin` scope required. |
| **settings** | `GET/POST /api/settings`, `.../language`, `.../timezone`, `.../editor`, `.../vscode/start`, `.../vscode/stop`, `.../plane`, `.../snapshot-*` | **PROTECTED** | Settings store. Snapshot admin paths are sensitive — `snapshot:write` scope. |
| **snapshots** | `GET/POST /api/snapshots/...` | **PROTECTED** | Agent snapshot control plane. |
| **workspace** | `GET /api/workspace/tree`, `GET /api/workspace/file`, `GET /api/workspace/diff`, `GET /api/workspace/events` (SSE) | **PROTECTED** | Workspace snapshot/diff. Could be sensitive — `workspace:read` scope. |
| **recovery** | `GET/POST /api/recovery/...` | **PROTECTED** | Recovery admin. `auth:admin` scope. |
| **maintenance — availability** | `GET /api/maintenance/checkpoint-cleanup/availability` | **PUBLIC** (EXEMPT from Origin guard, also EXEMPT from auth — see §5.1) | Boot-e2e canary dependency. |
| **maintenance — others** | `POST /api/maintenance/checkpoint-cleanup/run`, etc. | **PROTECTED** + `Origin` guard UNCHANGED (NFR-14) | Maintenance actions require both auth + Origin. |
| **plane** | `GET/POST /api/plane/...` | **PROTECTED** | Plane structural sync admin. |
| **tmp_images** | `GET/POST /api/tmp-images/...` | **PROTECTED** | Image substrate access. |
| **health** | `GET /api/health` | **PUBLIC** | §5.1. |
| **info** | `GET /api/info` | **PUBLIC** | §5.1. |

### 5.5 Mounts & vscode

| Route | File:line | Verdict | One-line rationale |
|---|---|---|---|
| MCP KB mount | `/api/mcp/kb`, `/api/mcp/kb/sse` | daemon/api.py:3150-3151 | **MODULE-LANE (AMEND v1.4 / module-lanes — supersedes the v1.1/D1 "PUBLIC in v1" verdict per the user ruling; behavioral pass-through is IDENTICAL, the semantic class changes).** v1.4 ruling: the KB-MCP module has its own UI and its own API-key management (which does not exist yet — §5.0 NF-1) and is NOT designed for human-user access via OAuth2; it sits beyond/separate from OAuth2. The middleware explicitly TAGS these Mount paths module-lane and passes them through UNTOUCHED in BOTH auth states (Mounts DO traverse parent middleware on Starlette 0.52.1 — the v1.1/D1 traversal fact stands; only the class label changes from PUBLIC to MODULE-LANE, superseding the `AUTH_DEFAULT_PUBLIC_PATHS` placement semantics). **[SUPERSEDED v1.1/D1 text:]** "**PUBLIC** in v1 (Starlette 0.52.1 Mounts traverse parent middleware, so PROTECTED would 401 every KB-MCP consumer on day 1 of auth-ON. Loopback trust posture preserved verbatim: these paths are unauthenticated today and stay so; rationale documented in the runbook)." **Seam (superseded semantics):** the S-20 KB-MCP-auth seam is no longer "future OAuth2 protection" — it becomes "module-lane classification + own-key enforcement" per decisions.md D19. |
| vscode HTTP sub-app | `/vscode/...` | daemon/api.py:1821, vscode_proxy.py:965 | **PUBLIC + loopback (v1 default)**; **PROTECTED** (loopback + bearer) when `ENSEMBLE_AUTH_VSCODE_PROTECTED=true` (opt-in — see OQ-9 + §10) | v1 default: loopback posture unchanged (flag default `false` per §6.1). Loopback-only today; the matrix verdict tracks the flag so v1 default = loopback + unauthenticated. The PROTECTED branch attaches when an operator flips the flag. `/vscode/...` is ONE Starlette Mount at `daemon/api.py:1821` (vscode WebSocket siblings are part of the same Mount — see WebSocket row below). |
| vscode WebSocket | `/vscode/...ws` | vscode_proxy.py:1092 | **PUBLIC + loopback** (UNCHANGED in v1) | Per FR-21 (WebSocket pass-through) + OOS-9 (WebSocket auth seam reserved for v2). |

### 5.6 Aggregated PUBLIC-vs-PROTECTED counts

| Verdict | Count | Why-public at a glance |
|---------|-------|------------------------|
| **PUBLIC** | 11 route families (**AMEND v1.4 / module-lanes — was 12; the MCP KB mount family left PUBLIC for the semantically-distinct MODULE-LANE class**; net change −1) | Health probes (5) + auth-self (4) + SPA shell + FastAPI docs (3, resolved public-with-kill-switch per OD-1) |
| **PROTECTED** | 31 /api routers (line-drift corrected v1.2 / Minor #1 — `daemon/api.py:3097-3142` has 31 `include_router(` call sites; v1.1's 28 was stale) **less the webhooks family reclassified MODULE-LANE (AMEND v1.4 / module-lanes) ⇒ 30 legacy PROTECTED families + 1 NEW `/api/mcp/kb/keys` key-management family (v1.4, OAuth2 human lane)** + **mounts (1 — `/vscode` Mount, gated by `ENSEMBLE_AUTH_VSCODE_PROTECTED` flag default `false`; v1 default = 0 PROTECTED mounts, the (1) only applies when the flag is opt-in `true`)** + /views/* + SPA catch-all assets | Operator control plane + module token-management CRUD |
| **MODULE-LANE** (**NEW row — AMEND v1.4 / module-lanes**) | 2 route families: (1) webhooks ingest (`POST /api/webhooks/{source_id}` — per-source `X-Webhook-Secret`, auth-independent); (2) MCP-KB mounts (`/api/mcp/kb`, `/api/mcp/kb/sse` — own keys, mechanism confirm-at-implementation) | Module endpoints beyond/separate from OAuth2; middleware passes them through UNTOUCHED in BOTH auth states (FR-39/FR-42); their token-management CRUD is OAuth2-PROTECTED (webhook secrets via existing `/api/sources*`; MCP-KB keys via NEW `/api/mcp/kb/keys`) |
| **DECISION-NEEDED** | 0 — docs trio RESOLVED (public + `ENSEMBLE_AUTH_DOCS_ENABLED` kill switch, default true, per OD-1 architect ENDORSE 2026-10-10) | was OQ-11 in §9 |
| **MIXED** | 2 (`/` + catch-all, `/api/maintenance`) | Per-sub-path split |

**Census reconciliation (AMEND v1.4 / module-lanes).** The v1.3 tallies ("12 PUBLIC / 31-router PROTECTED census") are superseded where they conflict with the MODULE-LANE reclassification: PUBLIC 12 → 11 route families (KB mount reclassified); the webhooks router family leaves the effective PROTECTED set (30 of the 31 legacy families + 1 new keys family = 31 again with the v1.4 addition); MODULE-LANE = 2 families. The raw `include_router(` census (31 call sites) is a mount-count, not a verdict count, and is unchanged. The §7 consumer census reconciles to **18 rows** (row 17 verdict flipped to module-lane-independence; row 18 added for MCP-KB clients). The FR census reconciles 38 → **42** (FR-39…FR-42 added); the AC register gains AC-40…AC-42 (continuing from AC-39; AC-16a/AC-22a letter-suffixed entries unaffected).

### 5.7 SPA catch-all carve-out detail

The SPA catch-all at `daemon/api.py:3285-3310` uses `/{path:path}` matching with explicit 404 carve-outs for `api*`, `ws*`, `vscode*`, `views*` (the SPA must not eat real API requests). When auth is ON, requests to `/{path:path}` for assets MUST serve from the dist tree without auth (404-from-API AND serve-from-dist). The auth middleware sees the asset request BEFORE the route handler; without a PUBLIC carve-out for asset paths, the SPA breaks.

**ImplicitPUBLIC-ASSETS rule (proposed):** The middleware MUST treat GET `/` and GET `/{path:path}` (with non-carved-out path) as PUBLIC-ASSETS, regardless of auth.enabled. The carve-out check uses the same path-prefix-set as the SPA route (api*, ws*, vscode*, views*). This is a fine-grained PUBLIC variant — required for SPA correctness.

## 6. Config Surface Spec

The auth subsystem follows the existing `BaseSettings` section pattern at `daemon/config.py:3360-3391` (aggregate via `Field(default_factory=...)`). Env naming follows `ENSEMBLE_AUTH_*` (mirrors `ENSEMBLE_LIVE_VIEWS_*` at `daemon/config.py:2579`) with one special case noted in §6.2. YAML `${VAR:-default}` interpolation at `daemon/config.py:43-60`.

### 6.1 Env vars (exact names, types, defaults, validation)

| Env var | Type | Default | Required when | Validation | Notes |
|---|---|---|---|---|---|
| `ENSEMBLE_AUTH_ENABLED` | bool | `false` | — | Coerced from `true`/`1`/`yes`/`on` (pydantic convention). | Kept boolean OFF by default. |
| `ENSEMBLE_AUTH_CLIENT_ID` | str | unset | ENABLED=true | non-empty when ENABLED=true | RFC 6749 §2.2. |
| `ENSEMBLE_AUTH_CLIENT_SECRET` | SecretStr | unset | ENABLED=true | non-empty when ENABLED=true | Stored in pydantic as SecretStr; never log. |
| `ENSEMBLE_AUTH_ISSUER` | str (URL) | unset | ENABLED=true | non-empty + parseable URL + HTTPS scheme when ENABLED=true | The IdP issuer URL. |
| `ENSEMBLE_AUTH_DISCOVERY_URL` | str (URL) | derived: `<issuer>/.well-known/openid-configuration` | — | parseable URL; HTTPS scheme | Optional override. Discovery doc must match `ISSUER`. |
| `ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE` | str (URL) | unset | — (recommended when proxy-fronted) | parseable URL; HTTPS in prod (HTTP only when `ALLOW_INSECURE_COOKIE=true`); scheme+host[:port] only (no path) | **AMEND v1.2 / A2 — CANONICAL tier-1 operator override for the redirect_uri base.** When set, this overrides `live_view_external_base_url_fallback` (tier-2) and Host-capture (tier-3). The canonical answer for the "where does the redirect_uri base come from" question. The proxy deployment pattern (`technical-analysis.md:853` external_base set via env) is no longer refused by §6.3 row 7 when `EXTERNAL_REDIRECT_BASE` is set. |
| `ENSEMBLE_AUTH_REDIRECT_URI` | str (URL) | derived via `BaseURLResolver` (auth's tier-1 `external_redirect_base` > `live_view_external_base_url_fallback` (lifespan-copied from `live_views.external_base_url`) > Host-capture > bind > path `/api/auth/callback`) | — | same-origin path-prefixed (`/api/auth/callback`); HTTPS in prod (HTTP only when `ALLOW_INSECURE_COOKIE=true`) | **AMEND v1.2 / A2 — canonical D3.5 chain (authoritative):** `ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE` (auth's OWN tier-1 operator override — the canonical answer) > live-view `live_view_external_base_url_fallback` (v1.1 tier-2 mechanism, kept for proxy-fronted deployments that only set `ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL`) > Host-capture > bind. `ENSEMBLE_AUTH_REDIRECT_URI` is the EXACT-URI override ONLY (used when an operator needs a path-different callback like a reverse-proxy URI rewrite). The two are NOT redundant: `external_redirect_base` controls the BASE the redirect URI mints at; `redirect_uri` overrides the WHOLE mint to an exact URI when base-and-path derivation is wrong. Note: with `external_redirect_base` set via env, the proxy deployment that `technical-analysis.md:853` instructs is no longer refused by §6.3 row 7. |
| `ENSEMBLE_AUTH_SCOPES` | CSV / JSON list (per `NoDecode`, config.py:63-96) | `openid,profile,email` | — | each scope non-empty | `openid` is mandatory for OIDC; remaining are configurable. |
| `ENSEMBLE_AUTH_SESSION_TTL_SECONDS` | int | `28800` (8h) | ENABLED=true | ≥ 60, ≤ 86400 (24h) | Sliding renewal OFF keeps audits clean. |
| `ENSEMBLE_AUTH_SLIDING_RENEWAL` | bool | `false` | — | — | When true, `expires_at` is extended on each validate. Off by default. |
| `ENSEMBLE_AUTH_CLOCK_SKEW_SECONDS` | int | `60` | ENABLED=true | ≥ 0, ≤ 300 | Tolerance for `exp`/`nbf` checks. |
| `ENSEMBLE_AUTH_DISCOVERY_CACHE_TTL_SECONDS` | int | `3600` (1h) | ENABLED=true | ≥ 60, ≤ 86400 | Discovery doc cache window. |
| `ENSEMBLE_AUTH_JWKS_CACHE_TTL_SECONDS` | int | `3600` (1h) | ENABLED=true | ≥ 60, ≤ 86400 | JWKS cache window. |
| `ENSEMBLE_AUTH_COOKIE_SAMESITE` | str enum | `"Lax"` | ENABLED=true | one of `Lax`, `Strict`, `None` | `Strict` would break top-level POST callback redirect; warn if set. |
| `ENSEMBLE_AUTH_COOKIE_SECURE` | bool | `true` | ENABLED=true | — | When false, cookie sent over HTTP; security regression — operator must opt in. |
| `ENSEMBLE_AUTH_ALLOW_INSECURE_COOKIE` | bool | `false` | ENABLED=true | — | When true, cookie flag `Secure` is dropped. LAN-only deployment opt-in. |
| `ENSEMBLE_AUTH_FINGERPRINT_BY_IP_SUBNET` | bool | `false` | — | — | When true, the `/24` of client IP is bound to the session; mismatch → invalidate. Carrier-grade-NAT false positives are common; OFF by default. |
| `ENSEMBLE_AUTH_BEARER_TOKEN_TTL_SECONDS` | int | `2592000` (30d) | ENABLED=true | ≥ 60, ≤ 31536000 (1y) | Default token TTL at mint time. CLI `--ttl` overrides per token. |
| `ENSEMBLE_AUTH_DOCS_ENABLED` | bool | `true` | — | — | When false, FastAPI `docs_url=None, redoc_url=None, openapi_url=None` is set — `/docs`, `/redoc`, `/openapi.json` return 404. See OQ-11. |
| `ENSEMBLE_AUTH_VSCODE_PROTECTED` | bool | `false` | — | — | When true, `/vscode/*` is PROTECTED with bearer auth (loopback-only path expected). Off in v1 (loopback posture unchanged). See OQ-9 + §10. |
| `ENSEMBLE_AUTH_ALLOWED_ORIGINS` | CSV / JSON list | empty (same-origin only) | ENABLED=true | each entry a URL; HTTPS required in prod | When empty AND CORS configured with wildcard + credentials: refuse to start (FR-27). |
| `ENSEMBLE_AUTH_BOOTSTRAP_TOKEN` | SecretStr | unset | ENABLED=true (recommended) | non-empty when set | One-time mint path for first admin bearer. CLI reads this env var to mint the first bearer. **AMENDED v1.1 (C2):** ONE-SHOT semantics — consumed at first mint, subsequent reuse rejected; runbook step removes the env var after the first admin mint. Also note (runbook): an agent holding bash+DB access can invoke the mint CLI directly — same trust class as other DB-adjacent agent capabilities; the `ens_env_read` deny-list (FR-16a) does not close that lane. |
| `ENSEMBLE_AUTH_ANONYMOUS_VIEW_PATHS` | CSV / JSON list (paths) | empty | — (S-19 reserved; v1 ships empty) | each entry an absolute path prefix | **ADDED v1.1 (S-19):** anonymous-public `/views` subpath prefixes consulted by `AuthMiddleware` BEFORE session validation (startswith on `scope["path"]`). v1: empty default = pass-body + regression-cage entry. v2 auto-populates from `view_shares` rows (share tokens S-12/S-13, password gates). |
| ~~`ENSEMBLE_AUTH_REQUIRE_SQLITE_REJECTION`~~ | — | — | — | — | **REMOVED v1.1 (NFR-8 leader ruling, D17):** superseded by the inert-tables posture — auth tables are created on BOTH backends (single code path, no backend-conditional schema, no SQLite boot-refusal); the row contradicted the migration runner's auto-apply behavior (`daemon/migrations/runner.py:107-113`). Removal noted in the amendment log. |

### 6.2 Pydantic `AuthConfig` model (sketch)

```python
class AuthConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ENSEMBLE_AUTH_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
        case_sensitive=False,
    )

    enabled: bool = False
    client_id: str | None = None
    client_secret: SecretStr | None = None
    issuer: HttpUrl | None = None
    discovery_url: HttpUrl | None = None
    redirect_uri: str | None = None
    # AMEND v1.2 / A2 — canonical tier-1 operator override for the
    # redirect_uri base. scheme+host[:port] only (no path). When set,
    # this wins over `live_view_external_base_url_fallback` (tier-2) and
    # Host-capture (tier-3) in the BaseURLResolver chain.
    external_redirect_base: str | None = None
    scopes: Annotated[list[str], NoDecode] = ["openid", "profile", "email"]
    session_ttl_seconds: int = 28800
    sliding_renewal: bool = False
    clock_skew_seconds: int = 60
    discovery_cache_ttl_seconds: int = 3600
    jwks_cache_ttl_seconds: int = 3600
    cookie_samesite: Literal["Lax", "Strict", "None"] = "Lax"
    cookie_secure: bool = True
    allow_insecure_cookie: bool = False
    fingerprint_by_ip_subnet: bool = False
    bearer_token_ttl_seconds: int = 2592000
    docs_enabled: bool = True
    vscode_protected: bool = False
    allowed_origins: Annotated[list[str], NoDecode] = []
    bootstrap_token: SecretStr | None = None
    # require_sqlite_rejection REMOVED v1.1 (NFR-8 / D17 inert tables — see §6.1)
    anonymous_view_paths: Annotated[tuple[str, ...], NoDecode] = ()   # S-19, v1 empty
    # AMENDED v1.1 (D2): derived field — NOT an env var. Populated once at
    # lifespan from config.live_views.external_base_url (plain string) so the
    # BaseURLResolver's tier-2 fallback is consulted. **AMEND v1.2 / A2:**
    # the tier-1 field is now `external_redirect_base` (env-populatable
    # directly via `ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE`); the
    # `live_view_external_base_url_fallback` remains the tier-2 fallback
    # for proxy-fronted deployments that only set
    # `ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL`. **AMEND v1.2 / Minor #7:**
    # the field IS env-populatable at lifespan via
    # `ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL` (read by the live-views
    # config layer; the lifespan copy happens automatically on app startup;
    # operators do NOT need a separate auth env var — the existing
    # live-views env var populates this field transparently).
    live_view_external_base_url_fallback: str | None = None
    # AMEND v1.2 / A3 — S-20 KB-MCP-auth seam placeholder flag (NOT an
    # env var; reserved seam only, default False; the v1 posture is
    # `/api/mcp/kb` and `/api/mcp/kb/sse` are PUBLIC per D1).
    kb_mcp_auth_enabled: bool = False

    @model_validator(mode="after")
    def _validate_combinations(self):
        if not self.enabled:
            return self
        missing = []
        if not self.client_id: missing.append("client_id")
        if not self.client_secret: missing.append("client_secret")
        if not self.issuer: missing.append("issuer")
        if missing:
            raise ValueError(f"auth.enabled=true but missing: {missing}")
        if not self.allow_insecure_cookie and not str(self.issuer).startswith("https://"):
            raise ValueError("auth.issuer must be https:// unless allow_insecure_cookie=true")
        if self.cookie_samesite == "Strict":
            logger.warning("auth.cookie_samesite=Strict will break OAuth POST callback — use Lax")
        if self.session_ttl_seconds < 60 or self.session_ttl_seconds > 86400:
            raise ValueError("auth.session_ttl_seconds out of range [60, 86400]")
        # ... additional validators per env-var table
        return self
```

### 6.3 Invalid-combination matrix (validation rules)

> **AMENDED v1.1 (C4):** every exit-78 refusal below executes at the **boot
> preflight** — beside `_boot_db_preflight` (exit-75 precedent,
> `daemon/__main__.py:319-324`), BEFORE `uvicorn.run` — never at lifespan
> (a lifespan RuntimeError exits via uvicorn's generic path: exit 1,
> post-port-bind; NFR-7's code-78 contract would be unreachable there).
> The discovery-fetch WARN stays at lifespan (network I/O stays out of
> config-time validators).

| Combination | Behavior | Error |
|---|---|---|
| `ENABLED=true` + `CLIENT_ID=` (empty) | Refuse to boot | ERROR log + exit code 78 |
| `ENABLED=true` + `CLIENT_SECRET=` (empty) | Refuse to boot | ERROR log + exit code 78 |
| `ENABLED=true` + `ISSUER=` (empty) | Refuse to boot | ERROR log + exit code 78 |
| `ENABLED=true` + `ISSUER=http://...` + `ALLOW_INSECURE_COOKIE=false` | Refuse to boot | "auth.issuer must be https://" |
| `ENABLED=true` + CORS wildcard + `allow_credentials=true` | Refuse to boot (FR-27) | "auth.allowed_origins must replace wildcard + credentials" |
| ~~`ENABLED=true` + SQLite backend + `REQUIRE_SQLITE_REJECTION=true`~~ | ~~Refuse to boot (NFR-8)~~ | **REMOVED v1.1** — NFR-8/D17 inert tables: no SQLite refusal exists; auth tables are created on both backends, single code path |
| `ENABLED=true` + `REDIRECT_URI` set to external URL (different origin) | Refuse to boot | "auth.redirect_uri must be same-origin" |
| `ENABLED=true` + `EXTERNAL_REDIRECT_BASE` set to valid URL (same-origin scheme+host[:port]) | Boot normally; tier-1 of the redirect_uri chain | No error — the proxy deployment that `technical-analysis.md:853` instructs is honored via the env var; **AMEND v1.2 / A2** this combination is NO LONGER refused. |
| `ENABLED=true` + `EXTERNAL_REDIRECT_BASE` set to a different-origin URL | Refuse to boot | "auth.external_redirect_base must be same-origin as the daemon bind host" — the operator override cannot redirect to a foreign origin (would defeat the OAuth loopback-trust model). |
| `ENABLED=true` + `DISCOVERY_URL` issuer mismatch | Refuse to boot | "discovery document issuer does not match auth.issuer" |
| `ENABLED=false` + everything else set | Boot normally (config is silently ignored) | No error |
| `ENABLED=true` + `BOOTSTRAP_TOKEN=` (empty) + first admin token mint | CLI fails with helpful message | "set ENSEMBLE_AUTH_BOOTSTRAP_TOKEN to mint first admin token" |
| `ENABLED=true` + `SCOPES=` (empty list) | Refuse to boot | "auth.scopes must include 'openid'" |
| `ENABLED=true` + `COOKIE_SAMESITE=Strict` | Warn but proceed | "SameSite=Strict will break OAuth callback" |
| `ENABLED=true` + `COOKIE_SECURE=false` + `ALLOW_INSECURE_COOKIE=false` | Refuse to boot | "cookie_secure=false requires allow_insecure_cookie=true" |

### 6.4 `config.yaml` composition example

```yaml
# config.yaml — Ensemble configuration
auth:
  enabled: false  # default OFF; flip to true + provide the rest to enable
  client_id: "${ENSEMBLE_AUTH_CLIENT_ID}"
  client_secret: "${ENSEMBLE_AUTH_CLIENT_SECRET}"
  issuer: "${ENSEMBLE_AUTH_ISSUER}"
  scopes:
    - openid
    - profile
    - email
  session_ttl_seconds: 28800  # 8h
  cookie_samesite: "Lax"
  cookie_secure: true
  bearer_token_ttl_seconds: 2592000  # 30d
  docs_enabled: true
  allowed_origins: []  # same-origin only by default
```

Or explicit override (operator preferencing):

```yaml
auth:
  enabled: true
  client_id: "ensemble-prod"
  client_secret: "${ENSEMBLE_AUTH_CLIENT_SECRET}"  # secret in env, not yaml
  issuer: "https://idp.example.com/"
  scopes: ["openid", "profile", "email"]
  session_ttl_seconds: 28800
  allowed_origins:
    - "https://ensemble.example.com"
  redirect_uri: "https://ensemble.example.com/api/auth/callback"  # optional override
```

When `auth.enabled = false`, the `auth` block in `config.yaml` MAY be omitted entirely (pydantic handles absence). When ENABLED transitions true→false at runtime, restart required (no live toggle in v1).

### 6.5 Operator UX notes (for the runbook; FR-35)

- **Off → On:** add `auth.enabled: true`, set client_id / client_secret / issuer, restart. First login redirects to IdP. Mint first bearer token via CLI with `BOOTSTRAP_TOKEN`.
- **Secret rotation:** update `CLIENT_SECRET` env var, restart. Existing sessions remain valid (they signed with the OLD secret's keys — operator MUST accept that, or revoke `auth_sessions` to force re-login).
- **Discovery refresh:** automatic after TTL; manual by setting `DISCOVERY_CACHE_TTL_SECONDS=0` for one restart (debug only).
- **Logout → re-login:** `POST /api/auth/logout` clears the cookie; subsequent visit to a protected route triggers login.

### 6.6 Module-lane config boundary (AMEND v1.4 / module-lanes — NEW)

**Expected OAuth2 config delta from the v1.4 ruling: ZERO.** Module lanes are NOT OAuth2 config — nothing in §6.1 / §6.2 changes: no new `ENSEMBLE_AUTH_*` env var, no new `AuthConfig` field, no new §6.3 validation row. The `kb_mcp_auth_enabled: bool = False` placeholder (v1.2 / A3, §6.2) keeps its reserved-seam role but its semantics are superseded by the module-lane classification (see decisions.md D19) — it is NOT the module-lane switch.

MCP-KB key settings (the module's own keys — storage, format, rotation) belong to the MODULE, not to `AuthConfig`; they would live in the module's own config/env namespace, scoped **confirm-at-implementation** alongside the `/api/mcp/kb/keys` CRUD family and the mount key-check mechanism (FR-41). Webhook-source secrets already live in `source_configs.config["webhook_secret"]` (repositories/source/models.py:62-65) and are managed via the existing source CRUD — no auth-config surface involved. Module-lane behavior is identical with `auth.enabled` true or false (FR-42), so no auth-state-dependent config exists for module lanes by construction.

## 7. Machine-Client Compatibility Table

**Per UR-2 + UR-3 combined:** When `auth.enabled = true`, NO legitimate loopback/same-host consumer may break. **Every BLOCKED verdict is a defect** that the plan must iterate to fix. This table is exhaustive — every HTTP-emitting script/file in the repo is enumerated (per dispatch negatives: launcher.sh = zero HTTP calls; plugins/opendesign = zero daemon-API calls). The "Generic remote-VM client" row models the operator's external API consumers per dispatch note. **AMEND v1.4 / module-lanes: the no-consumer-breaks guarantee explicitly includes MODULE clients** — module-lane consumers (external webhook senders, MCP-KB agent/tool clients) are covered by the same 0-locked-out guarantee: their lane ignores OAuth2 entirely (FR-39/FR-42), so auth ON cannot lock them out; **0 consumers locked out — restated and unchanged** (§7.2).

### 7.1 The 12 inventoried loopback/same-host consumers + extras

Verdict legend:
- **YES UNCHANGED** — script currently calls only PUBLIC routes; auth ON requires no change.
- **YES WITH CONFIG** — script needs a bearer token (env var) added; no code change required.
- **YES WITH CODE CHANGE** — script needs minor logic to handle bearer/cookie (rare; resolved at enabler step).
- **BLOCKED** — incompatible without plan iteration. **None below is BLOCKED**; all 12+ pass cleanly.

| # | Consumer | File / location | Endpoints called | Verdict | Required scope(s) when ON | Enabler (operator or test setup) |
|---|----------|-----------------|------------------|---------|-----------------------------|-----------------------------------|
| 1 | `promote.sh` (calls `lib.sh:probe_health`, `promote.sh:362-419`, `lib.sh:4215-4256`) | scripts/upgrade/promote.sh + lib.sh | `GET /livez`, `GET /readyz`, version-verify GET (re-probe during 300s soak) | **YES UNCHANGED** | n/a (public probes only) | None — NFR-3 invariant holds. |
| 2 | `rollback.sh` + `restart.sh` + `status.sh` via lib.sh | scripts/upgrade/{rollback,restart,status}.sh | Same: `GET /livez`, `GET /readyz` | **YES UNCHANGED** | n/a | Same as #1. |
| 3 | `adopt-unit.sh` | scripts/upgrade/adopt-unit.sh:467 | `GET /livez` | **YES UNCHANGED** | n/a | None. |
| 4 | `deploy.sh` | scripts/deploy.sh:328-337 | `GET /livez`, `GET /readyz` | **YES UNCHANGED** | n/a | None. |
| 5 | `watchdog-watcher.sh` | scripts/watchdog-watcher.sh:241-245 | `GET /livez` | **YES UNCHANGED** | n/a | None. |
| 6 | `upgrade_tools.py::_probe_self` | daemon/tools/upgrade_tools.py:864-886 | `GET /livez`, `GET /readyz` (in-process) | **YES UNCHANGED** | n/a | None. (in-process; cookie/bearer not relevant) |
| 7 | `test_builtin_mcp_servers.sh` | scripts/test_builtin_mcp_servers.sh:143-422 | `GET /api/health` (PUBLIC), `GET /api/mcp-servers*` (PROTECTED) | **YES WITH CONFIG** | `system:read` (covers /api/health, /livez, /readyz); `mcp:read` (covers /api/mcp-servers) | Operator mints a `mcpservers-test` bearer token with `system:read,mcp:read` scopes; script reads `ENSEMBLE_BEARER_TOKEN` env var. Bash: `curl -H "Authorization: Bearer $ENSEMBLE_BEARER_TOKEN" ...` |
| 8 | `e2e_pause_resume_test.py` | scripts/e2e_pause_resume_test.py | `POST /api/instances`, `POST .../pause`, `POST .../resume`, `POST /api/messages`, `POST /api/jobs`, etc. (localhost:8079 via dev.sh) | **YES WITH CONFIG** | `instances:write`, `instances:read`, `jobs:write`, `jobs:read`, `messages:write` | Test fixture sets `ENSEMBLE_BEARER_TOKEN` from a token minted with `e2e-pause-resume-test` label. |
| 9 | `tests/e2e/mock_source_server.py` DaemonSourceMock | tests/e2e/mock_source_server.py | `POST /api/sources`, `GET /api/instances*`, etc. | **YES WITH CONFIG** | `sources:write`, `instances:read` | Pytest fixture mints a `e2e-mock-source` token; the source simulator writes it to a header. |
| 10 | `boot-e2e-snapshots-daemon.sh` | frontend/scripts/boot-e2e-snapshots-daemon.sh:204-208 | `GET /readyz` (PUBLIC) | **YES UNCHANGED** | n/a | None. |
| 11 | `boot-e2e-maintenance-daemon.sh` | frontend/scripts/boot-e2e-maintenance-daemon.sh:178-182 | `GET /api/maintenance/checkpoint-cleanup/availability` (PUBLIC per matrix) | **YES UNCHANGED** | n/a | None — auth middleware MUST have this path in `public_path_set` (FR-22). |
| 12 | Angular SPA (browser) | frontend/src/app/** (≈90 same-origin /api/* endpoints + 5 SSE) | All PROTECTED API + SSE paths | **YES WITH CONFIG** | n/a (cookie auth — browser auto-sends on same-origin) | Operator enables auth + configures IdP. SPA on first protected request gets 302 to /api/auth/login; completes login; bounces back via `return_to`. NO source change required (NFR-18). |
| 13 | `pytest` suite (in-process) | tests/** (uses httpx.ASGITransport, no network) | All test fixtures hit PROTECTED paths | **YES WITH CONFIG** | Per-test scopes | Test fixtures use `auth_disabled_app` for existing tests (auth OFF) and `auth_enabled_app` for new tests (auth ON, fake OIDC, with each test minting scoped tokens). |
| 14 | **Generic remote-VM programmatic client** (modeled — operator runs multiple ensemble instances across remote VMs) | external (not in repo) | `/api/*` from arbitrary scripts/clients | **YES WITH CONFIG** | per-use (minimum-scoped) | Operator mints one token per remote VM (label = machine name) with the minimum scope set. Scripts add `Authorization: Bearer <token>` to outbound requests. |
| 15 | **`launcher.sh`** (negative — ZERO HTTP calls) | scripts/upgrade/launcher.sh | none | **N/A** | n/a | Not applicable — out of scope. |
| 16 | **`plugins/opendesign`** (negative — ZERO daemon-API calls) | plugins/opendesign/** | none | **N/A** | n/a | Not applicable — out of scope. |
| **17** (ADDED v1.2 / A6 — leader ruling: external webhook senders; **verdict FLIPPED v1.4**) | External webhook senders (Telegram / Slack / Discord / WhatsApp sources) | external (Telegram/Slack/Discord/WhatsApp bots) | `POST /api/webhooks/{source_id}` (**MODULE-LANE per AMEND v1.4 / module-lanes — the lane ignores OAuth2**) | **YES UNCHANGED — auth-INDEPENDENT (AMEND v1.4 / module-lanes; supersedes the v1.2/A6 "YES-WITH-CONFIG" verdict which assumed webhook ingest would be OAuth2-PROTECTED)** | n/a — no OAuth2 credential consulted, ever | None for auth. The `webhook_secret` recommendation REMAINS as existing best practice for the open-ingest posture (webhooks.py:67 — unset secret = open ingest; pre-existing condition, unchanged since before this plan), NOT as auth-enablement config: the v1.2/A6 "MUST set `webhook_secret` BEFORE enabling auth" Enable-step footgun is DISSOLVED by the ruling (module lanes ignore OAuth2, so there is no auth-ON day-1 lockout to prevent). Runbook carries it as a security-hardening note (P6, T6.1). |
| **18** (ADDED v1.4 / module-lanes) | MCP-KB agent/tool clients (local agents + tools hitting the KB mounts) | daemon/mcp/kb_server.py consumers (in-repo agents; external MCP clients) | `/api/mcp/kb`, `/api/mcp/kb/sse` (**MODULE-LANE**) | **YES UNCHANGED — module-lane with own keys** | n/a — no OAuth2 credential consulted; when the module key-check mechanism lands (confirm-at-implementation), clients present their MCP-KB key, which is module config, NOT OAuth2 | None for OAuth2. If/when the own-key mechanism activates, each MCP-KB client gets a module key via the NEW `/api/mcp/kb/keys` management family (FR-41) — a module-side enablement act, independent of `auth.enabled`. Current fully-open state (zero auth, kb_server.py) is unchanged by this plan's OAuth2 build. |

### 7.2 Aggregate verdict

| Verdict | Count | % |
|---------|-------|---|
| YES UNCHANGED | 10 (rows 1, 2, 3, 4, 5, 6, 10, 11 — **AMEND v1.4 / module-lanes: + row 17 external webhook senders (flipped from YES-WITH-CONFIG) + row 18 MCP-KB clients (new); both auth-INDEPENDENT module-lane**) | 56% |
| YES WITH CONFIG | 6 (rows 7, 8, 9, 12, 13, 14 — **AMEND v1.4 / module-lanes: was 7; row 17 moved to YES UNCHANGED**) | 33% |
| YES WITH CODE CHANGE | 0 | 0% |
| BLOCKED | 0 | 0% |
| N/A | 2 (rows 15, 16) | 11% |
| **Total** | **18** (**AMEND v1.4 / module-lanes: was 17; row 18 added**) | **100%** |

**No consumer requires a code change. All 18 consumers — human-session, bearer, and MODULE-lane alike — keep working; 0 locked out (restated per AMEND v1.4 / module-lanes).** All loopback/same-host callers either (a) only touch PUBLIC probes (no auth surface), (b) need a bearer token from env/config, or (c) are MODULE-lane clients whose credential is owned by their module and never consulted by the OAuth2 middleware (rows 17, 18 — FR-39/FR-42). The Angular SPA (browser) needs zero source change (NFR-18 — cookie auth flows through same-origin policy).

### 7.3 Required scope granularity by use-case (illustrative; FR-18)

The decorator-based scope check is the route-level policy. Per-consumer minimum scopes:

| Token label | Purpose | Scopes |
|-------------|---------|--------|
| `mcpservers-test` | e2e MCP-server probe script | `system:read`, `mcp:read` |
| `e2e-pause-resume-test` | pause/resume e2e | `instances:read`, `instances:write`, `jobs:read`, `jobs:write`, `messages:write` |
| `e2e-mock-source` | mock source simulator | `sources:write`, `instances:read` |
| `boot-e2e-canary` | canary probes (already on PUBLIC) | n/a |
| `frontend-spa` | Angular SPA | n/a (cookie auth) |
| `promote-rotate` | upgrade machinery | n/a (uses PUBLIC probes only) |
| `operator-cli` | operator ad-hoc admin | `auth:admin`, plus minimal of other scopes as needed |
| `<vm-name>` (one per remote VM) | programmatic clients | tailored to use case |

The full scope taxonomy is the planner's to refine; this set is the v1 minimum.

### 7.4 Failure mode — what happens if a consumer is misconfigured (auth ON, no token)

For any of the 6 "YES WITH CONFIG" consumers (rows 7, 8, 9, 12, 13, 14 — **AMEND v1.4 / module-lanes: was 7 incl. row 17, now module-lane-independent**), if the bearer token env var is missing or wrong:
- A `curl` call returns `401 AUTH_REQUIRED` (machine-style) or `302` to `/api/auth/login` (if Accept: text/html).
- The script aborts with an HTTP error code; the operator sees the failure in logs.
- The daemon does NOT crash; the next correctly-configured caller continues.

This is the "fail loudly, not fail silently" contract — operators see credential problems immediately, not after a 4am outage. (Per technical-analysis §Machine-Client Lane final paragraph.)

### 7.5 Test fixture pattern (FR-34)

`tests/fakes/fake_oidc_provider.py` plus `tests/conftest_auth.py` provides two app fixtures:
- `auth_disabled_app` — current `EnsembleApp` with `auth.enabled = false`. Existing tests run unchanged.
- `auth_enabled_app` — current `EnsembleApp` with `auth.enabled = true`, fake OIDC mounted at `/_test_oidc/`, JWKS override pointing at the fake. Each test that needs auth mints a token via a fixture helper `mint_test_token(label, scopes=[])` and inserts `Authorization: Bearer <token>` into the request.

The split-proves-the-contract: `auth_disabled_app` test pass demonstrates NFR-1 (zero disruption); `auth_enabled_app` test pass demonstrates NFR-3 + NFR-5 (everything works as designed).

### 7.6 /vscode consumer inventory (AMEND v1.3 / B1 closure)

**Status: N/A-loopback (no repo machine consumer hits /vscode).** The `/vscode` Starlette Mount at `daemon/api.py:1821` (backed by `vscode_proxy.py:965`) is **operator-browser-only** — it serves the VSCode-in-the-browser IDE to operators opening it interactively. A repo-internal grep confirms zero daemon-API scripts / e2e tests / operator tooling make HTTP calls into `/vscode/*`:

- The 17 inventoried consumers in §7.1 (rows 1–17) call only `/livez`, `/readyz`, `/api/health`, `/api/mcp-servers*`, `/api/instances*`, `/api/messages*`, `/api/sources*`, `/api/jobs*`, `/api/maintenance/checkpoint-cleanup/availability`, or the SPA (`/`). None target `/vscode/*`.
- The Angular SPA at `frontend/src/**` does NOT issue any `XHR / fetch` to `/vscode/*` (the SPA mounts the VSCode webview iframe to a user-typed or operator-configured URL — out of the SPA's network surface).
- The `plugins/opendesign/**` and `launcher.sh` are explicitly NEGATIVE consumers in §7.1 (rows 15–16 — zero HTTP calls).

Therefore the `/vscode` PROTECTED branch (when `ENSEMBLE_AUTH_VSCODE_PROTECTED=true`) has no repo-machine-consumer to enabler — only operator-browser traffic. The v1 default (flag `false`) leaves loopback posture unchanged per OD-7. **Rationale for v1 N/A-loopback posture:** operator-browser traffic to a loopback-only mount is identical in trust posture to today's pre-auth flow; the seam exists (FR-21 + S-6) for v2 without an inter-phase consumer enablement task.

### 7.7 Module-lane clients (AMEND v1.4 / module-lanes — NEW)

The two module-lane consumer populations and their isolation guarantees, from the consumer side (enforcement mechanics are FR-39/FR-40/FR-42; negative test matrix AC-40):

1. **External webhook senders (§7.1 row 17).** Their only credential is the per-source `X-Webhook-Secret` checked at webhooks.py:63-76. Auth state is invisible to them: with auth OFF they behave exactly as today (zero change, AC-S1); with auth ON the middleware passes `/api/webhooks/*` through untouched (FR-42), so their requests are decided solely by the module guard. Presenting an OAuth2 session cookie or bearer to webhook ingest does NOT authenticate it — the module guard still requires the configured secret (401 as today).
2. **MCP-KB clients (§7.1 row 18).** Local agents/tools and external MCP clients hit `/api/mcp/kb*`; today zero-auth (kb_server.py — fully open, pre-existing posture). Auth state is invisible to them (FR-42). When the module key mechanism lands (confirm-at-implementation), they present an MCP-KB key that no other surface accepts (FR-40).

**Cross-lane rejection semantics (consumer-facing view of FR-40):** webhook token → MCP-KB mount or general API = 401/403; MCP-KB key → webhooks or general API = 401/403; OAuth2 session/bearer → webhook ingest = NOT a webhook credential (module guard decides; 401 when a configured secret is absent/mismatched); module tokens are never valid outside their lane. **0-locked-out guarantee restated: auth ON locks out zero consumers — the 18-row §7.1 census holds in both auth states.**

## 8. Acceptance Criteria

Every FR and NFR in §3 + §4 maps to one or more ACs in this section. Each AC is independently testable. The cross-cutting 4-scenario walkthrough is at the end (AC-S1 through AC-S4) — these are the integration tests the planner must deliver as `tests/e2e/test_auth_lifecycle.py`.

### 8.1 AC map (high-level)

| Anchor | ACs | Test type |
|--------|-----|-----------|
| §3.2 Enable/Disable (FR-1..3) | AC-1, AC-2 | unit + e2e |
| §3.3 Login Flow (FR-4..6) | AC-3, AC-4, AC-5 | unit + integration |
| §3.4 Callback (FR-7..9) | AC-6, AC-7, AC-8, AC-9 | unit + integration |
| §3.5 Session (FR-10..14) | AC-10, AC-11, AC-12, AC-13 | unit + integration |
| §3.6 Logout (FR-14..15) | AC-14, AC-15 | unit + integration |
| §3.7 Machine Lane (FR-16..18) | AC-16, AC-17, AC-18 | unit + integration |
| §3.8 Middleware (FR-19..21) | AC-19, AC-20, AC-21 | integration |
| §3.9 Health Publicness (FR-22..23) | AC-22, AC-23 | integration + boot-e2e |
| §3.10 UX Contract (FR-24..25) | AC-24, AC-25 | integration |
| §3.11 Boot Fail-Fast (FR-26..28) | AC-26, AC-27, AC-28 | boot-e2e |
| §3.12 Provider Resilience (FR-29..31) | AC-29, AC-30, AC-31 | integration |
| §3.13 Compatibility (FR-32) | AC-32, AC-33 | e2e + boot-e2e |
| §3.14 Testing (FR-33..34) | AC-34 | meta-test |
| §3.15 Docs (FR-35) | AC-35 | manual |
| §3.16 Non-Preclusion (FR-36..37) + FR-38 cage | AC-36, AC-37, AC-38 | unit |
| §3.17 Module Auth Lanes (FR-39..42) — **AMEND v1.4 / module-lanes** | AC-40, AC-41, AC-42 | integration + unit |
| §4 NFRs | AC-NFR-1 .. AC-NFR-18 | per-NFR |

### 8.2 Detailed ACs

#### Enable / Disable

**AC-1** (FR-1, FR-3, NFR-1, NFR-2) — Auth OFF = byte-identical behavior.
- **Given:** A daemon with `auth.enabled = false` (default).
- **And:** All existing tests in `tests/unit/`, `tests/integration/`, `tests/e2e/` (excluding new auth tests).
- **When:** The daemon boots and all tests run.
- **Then:** All tests pass. The response bodies of `/api/*`, `/views/*`, `/livez`, `/readyz`, `/api/info` are byte-identical to the pre-build baseline.
- **Test type:** e2e + diff snapshot.

**AC-2** (FR-1) — `auth.enabled` defaults to false/unset.
- **Given:** A daemon with `EnsembleConfig` containing no `auth` block.
- **When:** Boot completes.
- **Then:** `settings.auth.enabled is False`. `AuthMiddleware` is NOT registered (introspection: `auth_middleware in app.user_middleware == False`). No cookies are issued. No Authorization: Bearer validation runs.
- **Test type:** unit.

#### Login Flow

**AC-3** (FR-4) — `/api/auth/login` initiates PKCE flow.
- **Given:** A daemon with `auth.enabled = true` and a valid IdP config.
- **When:** `GET /api/auth/login` (no cookie) is made.
- **Then:** Response is `302 Found`, `Location` is `{issuer}/authorize?response_type=code&client_id=...&redirect_uri=...&scope=openid+profile+email&state=<32-byte-base64>&code_challenge=<sha256-base64>&code_challenge_method=S256`. An `auth_states` row exists with `code_verifier`, `state`, `expires_at = now() + 300s`.
- **Test type:** integration.

**AC-4** (FR-5) — `code_verifier` is server-only.
- **Given:** Login initiation.
- **When:** Inspect all response headers + body + cookies.
- **Then:** No `code_verifier` value appears in any response artifact. Only `code_challenge` is sent in the `Location` query parameter.
- **Test type:** integration (automated).

**AC-5** (FR-6) — `state` is 32-byte URL-safe base64.
- **Given:** Login initiation.
- **When:** Inspect the `state` query parameter.
- **Then:** It matches `^[A-Za-z0-9_-]{43,43}$` (43 chars for 32-byte base64). Decoded length is exactly 32 bytes (256 bits).
- **Test type:** integration.

#### Callback / Code Exchange

**AC-6** (FR-7) — State CAS defeat replay.
- **Given:** A valid `state` row in `auth_states`.
- **When:** `/api/auth/callback?code=...&state=<state>` is called twice (replay).
- **Then:** First call: 302 + Set-Cookie + session row. Second call: 400 STATE_REPLAYED. Only ONE session row exists.
- **Test type:** integration.

**AC-7** (FR-7) — Expired state returns 400 STATE_EXPIRED.
- **Given:** An `auth_states` row with `expires_at = now() - 1s`.
- **When:** `/api/auth/callback` is called.
- **Then:** 400 STATE_EXPIRED. No session row inserted.
- **Test type:** unit.

**AC-8** (FR-8) — Token exchange non-2xx returns 401 IDP_REJECTED.
- **Given:** Fake IdP returns 400 from `/token`.
- **When:** Callback is called with valid state.
- **Then:** User-facing response is `401 IDP_REJECTED`. WARNING log emitted with body truncated to 256 chars. No session row inserted.
- **Test type:** integration.

**AC-9** (FR-9) — All four ID-token validations enforced.
- **Given:** A token signed with the right key but `iss = "wrong-issuer"`. (and similar for `aud`, exp, signature.)
- **When:** Validation runs.
- **Then:** 401 IDP_REJECTED for each deviation. The validations are: (a) `iss` matches `auth.issuer` (constant-time), (b) `aud` matches `auth.client_id`, (c) `exp` within ±60s skew, (d) signature verifies against JWKS.
- **Test type:** unit.

#### Session Issue & Validation

**AC-10** (FR-10) — Session row fully populated.
- **Given:** Successful login.
- **When:** Inspect the `auth_sessions` row.
- **Then:** All fields present: `id` = 32-byte base64 url-safe, `subject`, `scopes`, `auth_method = "session"`, `user_agent`, `created_at`, `expires_at = created_at + ttl`, `last_used_at`, `ip_subnet` (when fingerprint enabled).
- **Test type:** integration.

**AC-11** (FR-11) — Cookie attributes.
- **Given:** Successful login.
- **When:** Inspect `Set-Cookie` header.
- **Then:** `ensemble_sid=<sid>; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=28800`.
- **Test type:** integration.

**AC-12** (FR-12) — AuthMiddleware dispatch order.
- **Given:** A request with a valid cookie, valid bearer, valid preflight, valid /livez path, valid OPTIONS, valid websocket.
- **When:** Each scenario is exercised.
- **Then:** Each follows the documented dispatch order: WS pass-through, OPTIONS pass-through, public-path pass-through, cookie validate, bearer validate, 401/302 fall-through.
- **Test type:** integration.

**AC-13** (FR-13) — Bearer precedence over cookie.
- **Given:** A request with both a valid cookie (cookie-scope: openid,profile,email) and a valid bearer (bearer-scope: jobs:read).
- **When:** The middleware runs.
- **Then:** The bearer scope (jobs:read) is the active AuthContext — NOT the cookie scope.
- **Test type:** integration.

#### Logout

**AC-14** (FR-14) — Logout soft-deletes session.
- **Given:** Active session with `ensemble_sid`.
- **When:** `POST /api/auth/logout` is called.
- **Then:** `auth_sessions.revoked_at = now()`. Response `200 OK` + `Set-Cookie: ensemble_sid=; Max-Age=0`. Subsequent calls with the same cookie return 401.
- **Test type:** integration.

**AC-15** (FR-15) — `/api/auth/me` returns context or 401.
- **Given:** Request with valid cookie.
- **When:** `GET /api/auth/me` is called.
- **Then:** 200 with `{subject, scopes, expires_at, auth_method: "session"}`.
- **And:** Without cookie or with invalid cookie → 401 + `WWW-Authenticate: Bearer realm="ensemble"`.
- **Test type:** integration.

#### Machine Lane

**AC-16** (FR-16) — CLI mints token, plaintext printed once, server has only hash.
- **Given:** `ENSEMBLE_AUTH_ENABLED=true`, `ENSEMBLE_AUTH_BOOTSTRAP_TOKEN=<secret>`.
- **When:** `python -m daemon.auth.cli issue-token --label test-token --ttl 30d --scopes system:read` is run.
- **Then:** Plaintext token printed to stdout ONCE. `auth_bearer_tokens` row exists with `sha256(token) hex` (NOT plaintext). `label = "test-token"`, `scopes = ["system:read"]`, `expires_at = now() + 30d`.
- **Test type:** unit (CLI subprocess).

**AC-17** (FR-17) — Bearer accepts correct token, rejects wrong.
- **Given:** A token row in `auth_bearer_tokens` with hash H.
- **When:** Request `Authorization: Bearer <token-with-hash-H>` arrives.
- **Then:** AuthContext attached, request proceeds.
- **And:** Request `Authorization: Bearer <wrong-token>` → 401.
- **Test type:** integration.

**AC-18** (FR-18) — Scope check insufficient → 403.
- **Given:** Token with scope `system:read` only. Request to a route requiring `instances:write`.
- **When:** `Authorization: Bearer <token>` is sent.
- **Then:** 403 SCOPE_INSUFFICIENT.
- **Test type:** integration.

#### Bootstrap token + exfiltration-surface hardening (FR-16a)

**AC-16a** (NEW v1.2 / Minor #11 — phantom reference resolved) — `ens_env_read` deny-list refuses `ENSEMBLE_AUTH_*`-prefixed keys.
- **Given:** `daemon/tools/ens_env_tools.py` `ens_env_read(keys=[...])` override path; auth enabled with `ENSEMBLE_AUTH_BOOTSTRAP_TOKEN` set in the daemon environment.
- **When:** An agent invokes `ens_env_read(keys=["ENSEMBLE_AUTH_BOOTSTRAP_TOKEN"])`.
- **Then:** The tool returns an explicit denial (mirroring the POSTGRES-exclusion posture at `daemon/tools/ens_env_tools.py`). The plaintext value NEVER appears in any return value, log line, or side channel.
- **And (negative leg):** `ens_env_read(keys=["PATH"])` (non-auth env var) still works; `ens_env_read()` default path (no keys argument) is unchanged.
- **Test type:** unit (`tests/unit/test_ens_env_tools_auth_denylist.py`).

#### Middleware / Route Gating

**AC-19** (FR-19) — Middleware LIFO ordering (AMEND v1.2 / A5 — four-middleware sequence).
- **Given:** Four middlewares (CORS, SelectiveAccessLog, HostCapture, Auth) registered in that order.
- **When:** A request is processed.
- **Then:** Execution order is **Auth → HostCapture → SelectiveAccessLog → CORS** (Auth sees the request FIRST — LIFO — last registered is outermost). The test MUST assert the FULL four-step sequence (not just Auth-outermost alone). Host-capture happens AFTER auth decides pass-through.
- **Test type:** integration.

**AC-20** (FR-20) — OPTIONS preflight not auth-gated.
- **Given:** A request `OPTIONS /api/instances` (any origin).
- **When:** The request is processed.
- **Then:** AuthMiddleware passes through. CORS handles the preflight. No 401.
- **Test type:** integration.

**AC-21** (FR-21) — WebSocket scope unchanged.
- **Given:** A WebSocket upgrade request to `/vscode/ws/...`.
- **When:** AuthMiddleware sees `scope["type"] == "websocket"`.
- **Then:** Pass-through to app. Loopback posture unchanged in v1.
- **Test type:** integration.

#### Health-Invariant Publicness

**AC-22** (FR-22, FR-23, NFR-3) — All 5 health routes are public with auth ON.
- **Given:** `auth.enabled = true`.
- **When:** `curl -fsS http://127.0.0.1:PORT/livez`, `/readyz`, `/api/health`, `/api/info`, `/api/maintenance/checkpoint-cleanup/availability` are called (no auth header).
- **Then:** All 5 return 200 (or 503 for /readyz when degraded per the readiness contract — NOT 401). NFR-3 invariant holds.
- **Test type:** boot-e2e (`tests/e2e/auth_health_probes.sh`).

**AC-23** (FR-22) — Public-path set rejects default-allow smuggling.
- **Given:** A request to a PROTECTED path like `/api/instances`.
- **When:** The middleware evaluates.
- **Then:** It passes through ONLY if path is in the explicit `public_path_set`. There is NO catch-all pass-through.
- **Test type:** integration.

#### 401 / 302 UX Contract

**AC-24** (FR-24) — 302 vs 401 discriminator.
- **Given:** Request to a protected path with no credential.
- **When:** Header `Accept: text/html` is sent (browsers).
- **Then:** `302` to `/api/auth/login?return_to=<sanitized>`. `Location` header present.
- **And:** Header `Accept: application/json` is sent (machines).
- **Then:** `401 Unauthorized` + `WWW-Authenticate: Bearer realm="ensemble"` + body `{"error": "AUTH_REQUIRED", ...}`.
- **And:** Header `Accept: */*` + path `/api/instances` → 401 (path heuristic fallback per technical-analysis §401 Contract).
- **Test type:** integration.

**AC-25** (FR-25) — `return_to` sanitization.
- **Given:** Various crafted `return_to` values: `"/api/instances"` (valid), `"//evil.com/path"` (open-redirect attempt), `"\\\\evil.com\\path"` (backslash-prefixed), `"http://evil.com/"` (external URL), `"x" * 2000` (excessive length).
- **When:** Login redirect happens.
- **Then:** Valid: passed through. Invalid: replaced with `/`.
- **Test type:** unit.

#### Boot & Config Fail-Fast

**AC-26** (FR-26) — Missing required config → exit 78.
- **Given:** `auth.enabled = true`, `CLIENT_ID = empty`.
- **When:** Daemon starts.
- **Then:** Exit code 78. ERROR log listing the missing field. No partial boot.
- **Test type:** boot-e2e.

**AC-27** (FR-27) — CORS wildcard + credentials refuses boot.
- **Given:** `auth.enabled = true` and existing CORS config is `allow_origins=["*"]`, `allow_credentials=true`.
- **When:** Daemon starts.
- **Then:** Exit code 78 + ERROR. Operator must set `auth.allowed_origins`.
- **Test type:** boot-e2e.

**AC-28** (FR-28) — `/api/auth/login` returns 503 LOGIN_NOT_CONFIGURED when base URL unresolvable.
- **Given:** `auth.enabled = true`, `redirect_uri` cannot be resolved (no `external_base_url`, no host-capture, bind to 127.0.0.1).
- **When:** `GET /api/auth/login` is called.
- **Then:** 503 LOGIN_NOT_CONFIGURED. Daemon does NOT crash. Protected routes still return 401.
- **Test type:** integration.

#### Provider Resilience

**AC-29** (FR-29) — Discovery retry with backoff.
- **Given:** Fake IdP returns 503 on `/token` for the first 2 attempts, then 200.
- **When:** Login + callback path is exercised.
- **Then:** Discovery fetch retries with 1s, 4s delays. Total time ~5s. Eventually succeeds. Cache populated.
- **Test type:** integration.

**AC-30** (FR-30) — IdP unreachable mid-session: existing sessions validate.
- **Given:** Pre-existing valid session row (from earlier login).
- **When:** IdP endpoint becomes unreachable (test fixture). A new protected request arrives with the old cookie.
- **Then:** 200 OK + AuthContext attached. No IdP round-trip needed.
- **Test type:** integration.

**AC-31** (FR-30, NFR-15) — IdP unreachable, new login fails.
- **Given:** IdP is down (all discovery/token calls return 503).
- **When:** `GET /api/auth/login` is called.
- **Then:** 503 IDP_UNREACHABLE. `/readyz` returns 200 with `auth_dependency_status = "degraded"` in body.
- **Test type:** integration.

#### Compatibility — 18 consumers (AMEND v1.3 / F4 count + **AMEND v1.4 / module-lanes: 17 → 18** — row 17 verdict flipped auth-INDEPENDENT, row 18 MCP-KB clients added; 10 YES-UNCHANGED + 6 YES-WITH-CONFIG + 2 N/A)

**AC-32** (FR-32, NFR-5) — All 18 consumers pass with auth ON.
- **Given:** Auth ON; bearer tokens minted for each consumer that needs them (per §7 matrix).
- **When:** Each of the 10 "YES UNCHANGED" consumers runs (no token, public-probes or module-lane-only). **(AMEND v1.4 / module-lanes: module-lane rows 17-18 run with NO OAuth2 credential at all.)**
- **And:** Each of the 6 "YES WITH CONFIG" consumers runs (with token from env var).
- **Then:** All 18 pass end-to-end. No BLOCKED. No code change required to any consumer. 0 locked out (§7.7 restatement).
- **Test type:** e2e + boot-e2e matrix.

**AC-33** (NFR-18) — SPA works with auth ON, zero source change.
- **Given:** Auth ON; Angular SPA in production mode (frontend dist).
- **When:** Browser opens `/`, then visits a protected route.
- **Then:** First request to protected route redirects to `/api/auth/login`. User logs in at IdP. Comes back with cookie. SPA loads. No source change in `frontend/src/**`.
- **Test type:** e2e (Playwright or curl + cookie jar).

#### Testing

**AC-34** (FR-33, FR-34) — Test stack runs without Docker.
- **Given:** A fresh host with no Docker daemon. `python -m venv .venv && pip install -e .`.
- **When:** `pytest tests/ -q` runs.
- **Then:** All auth-related tests pass: ~30 unit (AuthService) + ~15 (OAuthClient) + ~10 (BearerToken) + ~20 integration + 1 boot-e2e. No Docker invocation. Test runtime: < 5 minutes.
- **Test type:** meta-test (the runnability of the suite IS the test).

#### Operations & Docs

**AC-35** (FR-35, NFR-17) — Runbook complete.
- **Given:** `docs/runbooks/oauth2-auth.md`.
- **When:** Each of the 7 required sections is searched.
- **Then:** All 7 present: enable procedure, default-OFF verification, token-mint procedure, log rotation, upgrade plan, CORS notes, cross-references.
- **Test type:** manual + CI lint (heading-presence check).

#### Non-Preclusion (seams)

**AC-36** (FR-36, NFR-11) — All 20 reserved seams present in code.
- **Given:** v1 code on disk.
- **When:** `tests/test_reserved_seams.py` runs.
- **Then:** All 20 seams grep-detected (S-1 through S-20 per §10; the §10.1 SEAMS dict has 21 entries because S-20 KB-MCP-auth has 2 grep assertions — `kb_mcp_auth_enabled` config field + `kb_mcp_auth branch` middleware hook — both targeting the same seam, hence the dict-entry expansion; AMEND v1.3 / F5 reconciled the 7-vs-20-vs-21 drift): AuthContext.meta field, per-route policy hook decorator, allow_insecure_cookie config, WebSocket auth branch in middleware class, token-revoke admin route seam, IdP logout endpoint seam, multi-IdP seam, anonymous_view_paths config + middleware consult (S-19), KB-MCP-auth config + branch (S-20), plus the other 11 seam-reserved rows from S-1–S-18.
- **Test type:** unit (static check).

**AC-37** (FR-37) — `AuthContext.meta` is a `dict[str, Any]`.
- **Given:** Dataclass definition `class AuthContext`.
- **When:** Inspect the source.
- **Then:** Field `meta: dict[str, Any] = field(default_factory=dict)` present. v2 can attach per-view grants to `meta` without modifying AuthContext.
- **Test type:** unit (introspection).

#### Router-Mount Scope-Declaration Cage (AMEND v1.3 / F6 — skipped AC-38 number filled; FR-38 unmapped pre-v1.3)

**AC-38** (NEW v1.3 / F6 — fills the AC-number sequence gap (the v1.2 register jumped AC-37 → AC-39, leaving 38 skipped) AND closes the FR-38 unmapped gap; the v1.2 §8.1 AC map had `FR-38 → no AC` which violated the "every FR maps to ≥1 AC" invariant) — Router-mount scope-declaration regression cage fails when a router mount without an explicit scope declaration appears.
- **Given:** `daemon/api.py` after the v1 build lands. The per-family scope manifest in `tests/test_router_scope_manifest.py` lists every one of the **28** `api_router.include_router(` mounts — **verified census 2026-10-10 against the live tree:** `daemon/api.py` carries 31 raw textual `include_router(` matches = 28 `api_router.include_router(` router mounts at `:3097-3142` (the canonical cage match target per AMEND v1.3 / F3) + 2 app-level `app.include_router(` call sites (`:3144` the `api_router` aggregate mount, `:3259` the live-views router) + 1 prose comment (`:3138`) — with EITHER a `@require_scope(...)` decoration OR an explicit wildcard in the manifest.
- **When:** The cage test greps `daemon/api.py` for `api_router.include_router(` and walks the manifest.
- **Then:** Every mounted `api_router` family has a manifest entry; omission fails the test LOUDLY (per AMEND v1.1 / C1 — silent weakest-policy shipping is the silent hazard the cage converts to a loud reviewable declaration).
- **And:** The default-`*` posture itself stays for v1 (default-deny would force decorating all 28 router families in the first commission — wrong blast radius per architect C1); the cage does NOT change the runtime default, only the manifest completeness.
- **Test type:** unit (`tests/unit/test_router_scope_manifest.py`).

#### CSRF Predicate (AMEND v1.2 / C1 — CRITICAL)

**AC-39** (NEW v1.2 / C1 — Origin-guard predicate; SPA-lockout closed BY CONSTRUCTION) — State-changing request from the unchanged SPA succeeds.
- **Given:** `auth.enabled = true`; valid browser session cookie `ensemble_sid`; the Angular SPA at `frontend/src/**` UNCHANGED (NFR-18 invariant); a `POST /api/instances/{id}/messages` request from the SPA.
- **When:** The browser sends `Origin: http://localhost:PORT` (the SPA's own origin) AND NO `X-CSRF-Token` header.
- **Then:** The request SUCCEEDS (NOT 403). The Origin-guard predicate sees the Origin header, validates same-origin (Origin == Host:PORT), and the missing `X-CSRF-Token` is NOT a refusal trigger (token-echo validates only when a CSRF header IS present). `ensemble_sid` cookie is honored. Response: 2xx + processed mutation. INV-3 / SPA-lockout closed BY CONSTRUCTION.
- **And (negative leg):** Same SPA request with cross-origin `Origin: http://evil.example` → 403 CSRF_GUARD_REJECTED (Origin-guard fires).
- **And (negative leg):** A `curl` request to the same endpoint with NO `Origin` header AND NO `X-CSRF-Token` (machine/bearer lane — no ambient cookie authority) → 401 AUTH_REQUIRED or 200 if a valid bearer is supplied (NOT 403 — the predicate exempts headerless requests per rule-1 precedent at `daemon/routers/maintenance_origin_guard.py:24-43`).
- **Test type:** integration + boot-e2e (`tests/e2e/auth_csrf_predicate.sh`).

#### Route-Metadata Public-Set Derivation (NEW v1.3 — FR-22a explicit AC)

**AC-22a** (NEW v1.3 — FR-22a was unmapped pre-v1.3; **AMEND v1.4 / module-lanes: THREE-class derivation**) — The `public_path_set` consumed by `AuthMiddleware` is derived from route metadata at middleware construction (D18).
- **Given:** `auth.enabled = true`; `AuthMiddleware` constructed against the FastAPI app.
- **When:** Middleware construction walks `app.routes`, building the O(1) frozenset from the `public-probe` tag/attribute (prefix rules allowed for `/api/auth/*` + the SPA PUBLIC-ASSETS family); the construction LOGS the derived set at startup. **AMEND v1.4 / module-lanes: the same walk also derives the `module_lane_path_set` from the `module-lane` tag/attribute (`/api/webhooks/*` + `/api/mcp/kb*`) and logs BOTH derived sets; three classes result — public, module-lane, oauth2-protected (default).**
- **Then:** A route added to `app.routes` with the `public-probe` tag IS in the derived set; a route WITHOUT the tag is NOT (default-deny proof per AC-23); the startup log records the derived set (reviewable in promote transcripts). **AMEND v1.4 / module-lanes: a route tagged `module-lane` is in `module_lane_path_set` and NOT in `public_path_set` (the classes are disjoint); Mount surfaces (`/api/mcp/kb`, `/api/mcp/kb/sse`) appear in the module-lane set despite being Mounts (the classifier explicitly tags them — Mounts DO traverse middleware).**
- **Test type:** integration (`tests/integration/test_auth_middleware_derivation.py`).

#### Module Auth Lanes (AMEND v1.4 / module-lanes — NEW)

**AC-40** (AMEND v1.4 / module-lanes (NEW); FR-39, FR-40) — Negative cross-lane token matrix.
- **Given:** `auth.enabled = true`; a configured webhook source (with `webhook_secret` set); an MCP-KB mount; a valid OAuth2 session cookie; a valid scoped bearer token; (when the module key mechanism exists) a valid MCP-KB key.
- **When:** Each token type is presented to each WRONG lane: webhook `X-Webhook-Secret` value → `GET /api/mcp/kb` and → a general `/api/*` PROTECTED route (as `Authorization: Bearer`); MCP-KB key → `POST /api/webhooks/{source_id}` (as `X-Webhook-Secret`) and → a general PROTECTED route; OAuth2 session cookie → `POST /api/webhooks/{source_id}` (a configured-secret source, no `X-Webhook-Secret` header).
- **Then:** EVERY cross-lane presentation is rejected with 401/403 by the lane that owns the path — never accepted, never partially honored (FR-40). The OAuth2 cookie at webhook ingest yields the module guard's own 401 (cookie is NOT a webhook credential); the webhook secret at a PROTECTED route yields `401 AUTH_REQUIRED`; the MCP-KB key at a PROTECTED route yields `401 AUTH_REQUIRED`.
- **And:** The positive controls hold: correct `X-Webhook-Secret` → webhook ingest 200; valid session → PROTECTED route 200; (module-key mechanism) valid MCP-KB key → KB mount served.
- **Test type:** integration (`tests/integration/test_module_lane_isolation.py` — matrix-driven, P5 T5.11).

**AC-41** (AMEND v1.4 / module-lanes (NEW); FR-42) — Module-lane auth-state equivalence.
- **Given:** The same daemon image booted twice — once `auth.enabled = false`, once `auth.enabled = true` (valid IdP config).
- **When:** The module-lane requests are issued against BOTH boots: webhook ingest with correct secret, webhook ingest without secret on a configured source, webhook ingest on an UNCONFIGURED source (open-ingest posture), `GET /api/mcp/kb`.
- **Then:** Responses are byte-identical across the two auth states for every module-lane request (status + body shape); `module_lane_path_set` membership logged at startup is identical in both boots; no auth cookie/bearer is consulted or required on any module-lane path in either state. (When auth is OFF this is also the AC-S1 zero-change proof for module paths.)
- **Test type:** integration + boot-e2e leg (P5 T5.11).

**AC-42** (AMEND v1.4 / module-lanes (NEW); FR-41) — Unmasked-secret handling under the OAuth2-governed management surface.
- **Given:** `auth.enabled = true`; a logged-in human (session or scoped bearer) hitting the webhook token-management surface (GET `/api/sources`, `/api/sources/{id}` — the surface that returns `config` UNMASKED today, sources.py:81, `webhook_secret` in plaintext).
- **When:** The masking decision is confirmed at implementation (per §5.0 ML-3 mitigation): either (a) `webhook_secret` is masked on read (e.g., `has_webhook_secret: true` + redacted field) with write-through preserved on PUT, or (b) plaintext read-back is explicitly retained as an operator decision with the risk recorded.
- **Then:** The implemented behavior matches the confirmed decision; the `webhook_secret` value is NEVER returned to a non-authenticated caller (module-lane ingest never echoes the secret; the secret comparison is `compare_digest` webhooks.py:69); if (a), a round-trip test proves PUT-with-masked-read does not clobber the stored secret (config replaced wholesale at sources.py:341 is the clobber hazard).
- **And:** Pre-existing-condition disclosure: today's unmasked GET is a pre-existing condition (not introduced by this plan) — the plan records it as risk ML-3 and scopes the fix to the OAuth2-governed management surface (confirm-at-implementation).
- **Test type:** integration (P5 T5.11) + confirm-at-implementation checklist (plan-overview §12).

#### NFR-specific ACs

**AC-NFR-1** — Default-OFF diff = 0.
- **Test type:** e2e (test_snapshot_diff).

**AC-NFR-3** — Health probe reachability 5/5.
- **Test type:** boot-e2e shell script.

**AC-NFR-4** — Restart survival ≥ 99.9%.
- **Test type:** stress (1000 sessions × SIGKILL cycle).

**AC-NFR-9** — Auth middleware p99 latency delta < 5 ms.
- **Test type:** perf benchmark in `tests/perf/`.

**AC-NFR-10** — No-Docker runnability.
- **Test type:** meta-test on fresh VM.

**AC-NFR-12** — Audit log coverage 9/9 + redaction = 0 leaks.
- **Test type:** log grep + e2e.

**AC-NFR-13** — `random` module usage in `daemon/auth/` = 0.
- **Test type:** static grep.

**AC-NFR-16** — Throughput ≥ 1000 RPS sustained.
- **Test type:** perf benchmark.

**AC-NFR-18** — SPA source diff = 0.
- **Test type:** git diff in CI.

### 8.3 The four cross-cutting scenarios (integration e2e)

These four are the canonical "operator walks through the system" integration tests; deliverable as `tests/e2e/test_auth_lifecycle.py`.

#### AC-S1 — Auth OFF = identical behavior

**Given:** Brand-new daemon install. `auth.enabled = false`.
**And:** Operator runs the pre-build smoke test suite + 12-consumer boot-e2e canaries (rows 1, 3, 4, 5, 9, 10 in §7).
**When:** Daemon starts. All probes fire. All scripts run.
**Then:**
- Every probe returns 2xx.
- Every script completes successfully.
- No auth code path executes (verified by `auth_states` and `auth_sessions` tables being empty).
- Response bodies for `/api/*`, `/views/*` byte-identical to pre-build.
- `git diff` against the pre-build configuration is empty.

**Test name:** `test_auth_off_identical_to_pre_build`.
**Test type:** e2e + diff snapshot.

#### AC-S2 — Auth ON + provider healthy

**Given:** Daemon with `auth.enabled = true`, fake OIDC provider mounted, valid IdP config.
**And:** Operator has minted at least one bearer token with `system:read,jobs:read,instances:read` scopes.
**When:**
- Browser opens `/api/instances` (unauthenticated).
- Browser completes login at IdP, gets `ensemble_sid` cookie.
- Browser refreshes `/api/instances` (now authenticated).
- `curl -H "Authorization: Bearer <token>" http://.../api/instances` runs.

**Then:**
- First browser request: 302 to `/api/auth/login?return_to=/api/instances` (per FR-24).
- After IdP login: 302 back to `/api/instances` with `Set-Cookie: ensemble_sid=...`.
- Second browser request: 200 OK + instances JSON.
- Bearer curl: 200 OK + instances JSON (no cookie needed).
- All 12 consumers pass (per AC-32).
- `/livez`, `/readyz`, `/api/health`, `/api/info`, `/api/maintenance/.../availability`: all 200 (per AC-22).

**Test name:** `test_auth_on_provider_healthy_full_workflow`.
**Test type:** e2e.

#### AC-S3 — Auth ON + provider DOWN: protected fail-closed, health GREEN

**Given:** Daemon with `auth.enabled = true`. Fake OIDC provider is configured but DOWN (returns 503 on every endpoint).
**And:** A pre-existing valid session from a prior login (cookie `ensemble_sid`).
**When:**
- `curl http://.../livez` runs (no auth).
- `curl http://.../readyz` runs.
- `curl http://.../api/health` runs.
- `curl -H "Cookie: ensemble_sid=<valid-sid>" http://.../api/instances` runs.
- `curl http://.../api/auth/login` runs.
- A new browser attempts login.

**Then:**
- `/livez`: 200 OK (event-loop check passes).
- `/readyz`: 200 OK with `auth_dependency_status: "degraded"` in body (per technical-analysis OQ-1).
- `/api/health`: 200 OK.
- Pre-existing cookie: 200 OK + AuthContext attached (per AC-30 — validate path is PG-only).
- `/api/auth/login`: 503 IDP_UNREACHABLE.
- New browser login: 503 IDP_UNREACHABLE; user sees "Authentication provider unavailable" message.

**Verifies:** NFR-3 (health-publicness under provider-down), NFR-15 (degraded sub-field), and the surgical degradation of "auth is the thing that's broken; health is fine."

**Test name:** `test_auth_on_provider_down_degraded_state`.
**Test type:** integration + e2e.

#### AC-S4 — Restart mid-session: survival

**Given:** Daemon with `auth.enabled = true`. A valid session with `ensemble_sid` cookie (TTL 8h).
**When:**
- The session is minted (login completes).
- The daemon process is SIGKILLed (per `__main__.py:332-352`, 70s timeout).
- The daemon restarts cleanly.
- The same `ensemble_sid` cookie is presented.

**Then:**
- Pre-restart session count = 1.
- Post-restart session count = 1 (row survived in PG).
- Pre-restart session validates post-restart: 200 OK + AuthContext.
- Newly-issued sessions work normally post-restart.
- **AMEND v1.2 / A7 — restart-scenario precision:** the 5 public probes (`/livez`, `/readyz`, `/api/health`, `/api/info`, `/api/maintenance/checkpoint-cleanup/availability`) are reachable unauthenticated POST-restart, BEFORE any session revalidation. The pre-I/O fast path holds across the SIGKILL cycle — verified by `tests/e2e/auth_health_probes.sh` running the canary against the post-restart daemon (no auth headers) and asserting each returns 2xx-or-503-by-readiness-contract (NEVER 401).

**Spec note:** The requirement is **survival** (not graceful degradation) — sessions persist across restart by design (NFR-4 + FR-18). The 8h TTL bounds the survival window; sessions outside the window validate as expired.

**Test name:** `test_auth_session_survives_restart`.
**Test type:** integration + lifecycle.

## 9. Open Questions

This section enumerates questions that **block or shape this requirements document** — i.e., decisions the planner / operator / user must make BEFORE the implementation phase. The questions are distinct from technical-analysis.md's open technical questions (OQ-1 through OQ-10 in the sibling artifact), which an architect handles at the architecture layer; the questions below are *requirements-layer* decisions that affect user-facing behavior.

Each OQ is numbered OQ-REQ-N below to distinguish from the sibling's OQ-1..10.

### 9.1 OQ-REQ-1 — FastAPI docs trio (`/docs`, `/redoc`, `/openapi.json`)

**Status: RESOLVED (AMEND v1.2 / A8) — referenced as OD-1; OD-1 in the ledger (`plan-overview.md §7`) is DECIDED (ENDORSE 2026-10-10).**

**Default behavior today:** All three are LIVE and unauthenticated (FastAPI defaults at `daemon/api.py:2909-3007`; no overrides are passed).

**Resolved options:**
- (a) Keep PUBLIC unconditionally. (rejected by OD-1)
- (b) **PUBLIC default, configurable-off** — `ENSEMBLE_AUTH_DOCS_ENABLED` default `true`; flip to `false` → 404. **SELECTED** (OD-1 ENDORSE 2026-10-10).
- (c) PROTECTED behind auth. (rejected by OD-1)

**Resolution:** (b) — public by default with kill-switch. Operators who want docs hidden flip `ENSEMBLE_AUTH_DOCS_ENABLED=false`; the trio then 404s. The default keeps the pre-build behavior unchanged.

**Cross-reference:** the row in §5.2 carries the verdict; the env var row in §6.1 carries the toggle; FR-26/NFR-7 gate misconfig; D15 cross-refs the guard in `decisions.md`.

### 9.2 OQ-REQ-2 — SPA first-load-after-session-expiry UX

**Status: RESOLVED (AMEND v1.3 / F9) — referenced as OD-12 in the ledger (`plan-overview.md §7`); OD-12 is DECIDED (ENDORSE-WITH-CONDITION 2026-10-10).** The "Decision required by" status below is RETRACTED — the OQ was folded into DECIDED status at v1.1 sign-off.

When an unauthenticated browser hits a protected route:

**Options:**
- (a) **Bounce to IdP immediately.** Browser sees `/api/instances` → 302 → IdP login → `/api/instances`. (Matches FR-24 spec.)
- (b) **SPA sees 401, shows "please log in" UI, then redirects.** SPA has UX control over the bounce-back.

**Recommendation:** (a). The middleware does the redirect; the SPA doesn't need any change (NFR-18). The browser handles the flow natively. If the SPA wants custom UX, it can interpose by calling `/api/auth/me` first and rendering its own login button — but this is opt-in.

**Resolution (v1.1, OD-12 ENDORSE-WITH-CONDITION 2026-10-10):** (a) with `return_to` sanitization — normalizes `\` → `/` BEFORE the same-origin prefix check; AC-25 keeps the backslash cases as table-driven inputs. See D12 for full rationale.

### 9.3 OQ-REQ-3 — Cookie name (`ensemble_sid`)

The sibling technical-analysis.md uses `ensemble_sid`. Options:

- (a) `ensemble_sid` (technical-analysis default).
- (b) `ENS_SID` (shorter, fits in legacy 4 KB cookie budgets).
- (c) `auth_session_id` (more verbose, easier to spot in dev tools).

**Recommendation:** (a) `ensemble_sid`. Future per-view-share cookies may also exist; keeping the convention "ensemble_X" avoids namespace collision.

**Resolution (v1.1, OD-11 ENDORSE 2026-10-10):** (a) — see D8 for the full cookie + CSRF context.

### 9.4 OQ-REQ-4 — Logout endpoint HTTP method

**Options:**
- (a) `POST /api/auth/logout` (technical-analysis default).
- (b) `DELETE /api/auth/session` (REST-purist).
- (c) Both (FR-14 mentions the alias).

**Recommendation:** (c) Both. POST for browser-driven logout forms; DELETE for API clients. Both perform the same soft-delete.

**Resolution (v1.1, OD-10 ENDORSE 2026-10-10):** (c) — see D8 + phase3 T3.6 for the implementation contract.

### 9.5 OQ-REQ-5 — WebSocket auth in v1 scope

**Status: RESOLVED (AMEND v1.3 / F9) — referenced as OD-7 in the ledger; OD-7 is DECIDED (ENDORSE 2026-10-10).**

The dispatch listed `/vscode` WebSocket auth as OUT of scope for v1 (OOS-9). The seam (per technical-analysis Reserved Seams) is the WebSocket auth branch in `AuthMiddleware`. The question is: **is the seam placement correct (per `vscode_proxy.py:1092`)?** Or should the seam be elsewhere?

**Recommendation:** Confirm seam placement at `daemon/middleware/auth.py` (the same class that handles HTTP). Operator's vscode integration stays unchanged in v1.

**Resolution (v1.1, OD-7 ENDORSE 2026-10-10):** WebSocket pass-through (FR-21); `ENSEMBLE_AUTH_VSCODE_PROTECTED=false` ships the seam pre-wired. See D4 rule 4.

### 9.6 OQ-REQ-6 — Bootstrap-token mint surface (FIRST admin bearer)

**Status: RESOLVED (AMEND v1.3 / F9) — referenced as OD-2 in the ledger; OD-2 is DECIDED (ENDORSE-WITH-CONDITION 2026-10-10).**

How does the operator mint the very first bearer token (no chicken-and-egg)?

**Options:**
- (a) **CLI with `ENSEMBLE_AUTH_BOOTSTRAP_TOKEN` env var.** Operator sets the env var to a one-time secret; runs `python -m daemon.auth.cli issue-token --bootstrap`. The CLI reads the env var, mints an admin token, prints plaintext once. Subsequent mints require the admin token. (Technical-analysis OQ-2 recommendation.)
- (b) **HTTP endpoint `/api/auth/first-admin` accessible only on loopback with a pre-shared secret.** No CLI needed, but adds a pre-shared-secret layer.
- (c) **One-time URL in the daemon startup log.** Daemon prints `curl http://127.0.0.1:PORT/.../first-admin?secret=...` to logs on first boot.

**Recommendation:** (a). CLI is cleanest; matches the operator's existing CLI-driven workflow (e.g., `python -m daemon.X.cli`); secret never crosses HTTP.

**Resolution (v1.1, OD-2 ENDORSE-WITH-CONDITION 2026-10-10):** (a) with C2 conditions folded: `ens_env_read` deny-list `ENSEMBLE_AUTH_*` + one-shot bootstrap consumption (sentinel row per AMEND v1.3 / F10) + runbook removal step. See D8, D9.

### 9.7 OQ-REQ-7 — Scope taxonomy granularity for v1

**Status: RESOLVED (AMEND v1.3 / F9) — referenced as OD-3 in the ledger; OD-3 is DECIDED (ENDORSE-WITH-CONDITION 2026-10-10).**

Per technical-analysis OQ-3: start broad or fine-grained?

**Options:**
- (a) **Two-scope broad taxonomy:** `system:read` (probes only) vs `everything-else`. Easy to operate; weak least-privilege.
- (b) **Full fine-grained taxonomy** (per §7.3): 7 scopes (`system:read`, `system:probe`, `instances:read`, `instances:write`, `jobs:read`, `jobs:write`, `maintenance:read`). Best least-privilege; more ceremony for operators.
- (c) **Hybrid (recommended):** ship the 7 fine-grained scopes; default tokens to `system:read` and `instances:read` only; admin tokens opt into `instances:write` etc. on demand.

**Recommendation:** (c). Plan implements the fine-grained taxonomy from day 1 (low cost — just decorator parameters); operators get a sensible default and can refine per token. Matches the dispatch's per-consumer-scope table in §7.3.

**Resolution (v1.1, OD-3 ENDORSE-WITH-CONDITION 2026-10-10):** (c) hybrid; with C1 router-mount scope-declaration cage test folded. See D9.

### 9.8 OQ-REQ-8 — First-admin-token default scopes

**Status: RESOLVED (AMEND v1.3 / F9) — referenced as OD-14 in the ledger; OD-14 is DECIDED (ENDORSE 2026-10-10).**

When the bootstrap token mints the first admin token, what scopes does it get?

**Options:**
- (a) **`auth:admin` only.** Admin can issue more tokens but not directly call APIs. Two-step ceremony.
- (b) **`auth:admin` + all data scopes (`instances:*`, `jobs:*`, etc.).** Admin can do everything. One-step ceremony.
- (c) **`auth:admin` + scope set per operator choice at mint time.** CLI `--scopes auth:admin,instances:write,jobs:write`.

**Recommendation:** (c). CLI `--scopes` argument; operator picks minimum. Defaults to `auth:admin` only (least-privilege starting point).

**Resolution (v1.1, OD-14 ENDORSE 2026-10-10):** (c). See D9, D4 (CLI).

### 9.9 OQ-REQ-9 — Same-origin SPA assets vs auth middleware

The SPA catch-all at `daemon/api.py:3285-3310` serves `frontend/dist/**` for assets. With auth ON, do we need to carve out asset paths in the middleware's public set?

**Options:**
- (a) **Implicit PUBLIC-ASSETS rule** as proposed in §5.7 — middleware treats GET `/` and GET `/{non-carved-path}` as PUBLIC-ASSETS. Operators don't need to know anything.
- (b) **Explicit `auth.public_paths`** — operator lists `["/", "/favicon.ico", "/*.css", "/*.js"]` (fragile; cargo-cult).
- (c) **Asset proxy through `/api/public/{path:path}`** — explicit public proxy route.

**Recommendation:** (a). The implicit rule is correct: the SPA shell + its assets MUST be public; only `/api/*` and `/views/*` and `/vscode/*` paths are protected. The middleware inspects the path against the same carved-out prefixes the SPA uses for 404-fallback (`api*`, `ws*`, `vscode*`, `views*`).

**Resolution (v1.1, OD-13 ENDORSE 2026-10-10):** (a). See D4, D11.

### 9.10 Cross-reference — Technical-Analysis Open Questions

The sibling `technical-analysis.md` enumerates 10 open technical questions (`OQ-1` through `OQ-10`). The architecture-level answers shape this requirements document:

| Tech OQ | Topic | Requirements impact |
|---------|-------|----------------------|
| OQ-1 | `/readyz` composite when auth degraded → "add sub-field, do not flip verdict" | Adopted in NFR-15 + AC-31. |
| OQ-2 | Static-token mint surface → CLI recommended | Adopted in FR-16 + OQ-REQ-6. |
| OQ-3 | Scopes granularity → "broad first, tighten later" | Adopted (variation in OQ-REQ-7). |
| OQ-4 | `/vscode` WebSocket auth → out of scope v1, seam reserved | Adopted in FR-21 + OOS-9. |
| OQ-5 | Multi-IdP → out of scope v1, `idp_alias` seam | Adopted in OOS-1 + §10. |
| OQ-6 | Federated IdP logout → out of scope v1, `idp_logout_enabled` seam | Adopted in OOS-2 + §10. |
| OQ-7 | CSRF strategy → **Origin-guard predicate (SUPERSEDES double-submit)** | **AMEND v1.3 / F9** — adopted in NFR-6 (the v1.2 "MAY add double-submit" wording is RETRACTED; v1 implements Origin-guard predicate per `decisions.md D8 v1.2`) |
| OQ-8 | Per-view auth hook → decorator | Adopted in FR-18 + FR-37. |
| OQ-9 | Subject identification across IdP rotations → treat sub as opaque in v1 | Adopted in OOS-10 + §10. |
| OQ-10 | CORS hardening entanglement → do NOT harden CORS in this build, link ticket | Adopted in OOS-5. |

If the architect changes any tech OQ answer, this requirements document MAY need a re-pass. No conflicts as of writing.

### 9.11 Questions not asked (because already settled)

The following were considered and DO NOT need user input — they are settled by the requirements above or the sibling technical analysis:

- Default scope set for v1 tokens (settled in §7.3).
- Header name for bearer (`Authorization: Bearer` per RFC 6750).
- Cookie Path (`/` for daemon-wide scope).
- Session TTL default (8h, standard).
- Discovery cache TTL (1h, balance freshness vs latency).
- JWKS cache TTL (1h, same balance).
- Use of `secrets` module vs `random` (settled — `secrets` only per NFR-13).

## 10. Reserved Seams (Future-Not-Precluded)

Per UR-4, the v1 build must architecturally accommodate — but NOT implement — the future v2 features below. Each seam has a name, a v2-enabling feature, a code-level placement (where v2 plugs in), an FR-anchor from §3, and a verification method (the regression-cage test in AC-36).

| # | Seam name | v2 feature enabled | Code-level placement | FR anchor | Verification |
|---|-----------|--------------------|----------------------|-----------|--------------|
| S-1 | **`AuthContext.meta: dict[str, Any]`** field | Per-view grants, anonymous-scoped roots, subject alias | `daemon/auth/context.py` (new) — `class AuthContext(subject, scopes, auth_method, meta: dict)` | FR-37 | `tests/test_reserved_seams.py:assert AuthContext.meta exists` |
| S-2 | **Per-route `@require_scope(...)` decorator** | Per-route scope policy (already in v1 as decorator; the seam is the per-view-share policy hook) | `daemon/auth/decorators.py` — `@require_scope("view:{view_id}:read", ...)` | FR-18, FR-37 | decorator source file exists; can be applied to `live_views.py:274-391` |
| S-3 | **Per-root authorization policy hook** (URL → permissions map) | Anonymous `/views/{root}` roots (OOS-4) | `daemon/routers/live_views.py` — `dependencies=[Depends(require_root_grant)]` | FR-22 (extends §5 live-views row) | `live_views.py` already accepts `dependencies=[]` param; policy hook function `require_root_grant(view_id)` exists as `pass` |
| S-4 | **`grants` table (PG schema)** | RBAC for views: which subject can see which view | `daemon/migrations/versions/YYYYMMDD_HHMMSS_create_grants.sql` (template follows the existing migration convention) | FR-37 + §10.5 below | Migration file exists as `.sql` placeholder; `grants` repository factory stub |
| S-5 | **`allow_insecure_cookie` config flag** | Plain-HTTP LAN deployments | `daemon/config.py AuthConfig` (already in scope per FR-28 + NFR-6) | FR-28 | Static grep of `AuthConfig` for `allow_insecure_cookie: bool` |
| S-6 | **WebSocket auth branch in `AuthMiddleware`** | `/vscode` auth (OOS-9) | `daemon/middleware/auth.py` — `if scope["type"] == "websocket": pass` is the v1 default; v2 adds token-validate-on-upgrade | FR-21 | Static grep of `AuthMiddleware.__call__` for `websocket` handling |
| S-7 | **Token-revoke admin route** | Bearer leak response + lifecycle (rotate compromised tokens) | `daemon/routers/auth.py` — `/api/auth/admin/tokens/revoke` (route placeholder; gated by `auth:admin` scope) | FR-15, FR-36 | `auth.py` has the `/admin/` route group + scope decorator |
| S-8 | **IdP `end_session_endpoint` redirect** | Federated logout v2 (OOS-2) | `daemon/services/auth_service.py` — `idp_logout_enabled: bool` config gate; logout method branches on the flag | OOS-2 | Static grep of `AuthService` for `idp_logout_enabled` field + `end_session_endpoint` reference |
| S-9 | **Multiple-IdP `idp_alias` hook** | Multi-tenant (workforce + customer IdP simultaneously) | `daemon/config.py AuthConfig` — `idp_alias: str | None = None` per-route override | OOS-1 | Static grep of `AuthConfig` for `idp_alias` field |
| S-10 | **Subject-alias table (PostgreSQL)** | Subject identification across IdP rotations (OOS-10) | `daemon/migrations/versions/YYYYMMDD_HHMMSS_create_subject_alias.sql` + `daemon/auth/subject_alias.py` | OOS-10 | `.sql` file exists as a stub with `-- UP` and `-- DOWN` (empty body) |
| S-11 | **Self-service bearer-token HTTP mint endpoint** | Admin-scope mint via HTTP, replacing the v1 CLI | `daemon/routers/auth.py` — `/api/auth/admin/tokens/issue`; bootstrap admin token required | OOS-8 | `auth.py` has the admin-mint route placeholder |
| S-12 | **Per-view share token table** | `/views/{id}/share` URL with TTL + per-share scope | `daemon/migrations/versions/YYYYMMDD_HHMMSS_create_view_shares.sql` + share-validate method in `AuthService` | OOS-3 | `.sql` file stub exists; `AuthService` has `validate_share_token(...)` pass-through |
| S-13 | **Password-gated view scope** | `/views/{id}/password` access path | Extension of S-12 + new `hashed_password` field; pluggable hash check | OOS-3 | `view_shares` schema has `password_hash` field stub |
| S-14 | **Rate-limit per subject** | Abuse mitigation on `/api/auth/login` + per-token RPS | `daemon/middleware/rate_limit.py` (new); `rate_limit_per_subject: bool` config | future | Static grep of `daemon/middleware/` for `rate_limit` module |
| S-15 | **Audit log export to SIEM** | Forward auth events to an external SIEM collector | `daemon/services/audit_log_sink.py` (new); `audit_log_sink_dsn: str | None` config | future | Static grep of `daemon/services/` for `audit_log_sink` |
| S-16 | **mTLS lane for service-to-service** | Mutual-TLS auth for programmatic service callers (orthogonal to bearer) | `daemon/middleware/mtls.py` (new); `mtls_enabled: bool` config | future | Static grep of `daemon/middleware/` for `mtls` |
| S-17 | **Cookie scope binding (`__Host-` prefix)** | Defense-in-depth against subdomain cookie injection | `daemon/auth/cookie.py` — toggle `cookie_use_host_prefix: bool` for production hardening | future | Static grep of `cookie.py` for `cookie_use_host_prefix` |
| S-18 | **Replay cache (windowed)** | Detect ID-token replay across sessions | `daemon/services/auth_service.py` — `replay_cache: set[str] = set()` + Redis or PG cache backend | future | Static grep of `auth_service.py` for `replay_cache` |
| **S-19** (ADDED v1.1, architect must-add seam) | **`auth.anonymous_view_paths: tuple[str, ...]` consulted by `AuthMiddleware` BEFORE session validation** | Per-view share tokens (S-12), password-gated views (S-13) — both require the request to reach a router-level handler, but the v1 401-contract always 302s browser-Accept requests | `daemon/middleware/auth.py` — `if any(scope["path"].startswith(p) for p in config.anonymous_view_paths): pass through` (S-19 v1.1, consulted BEFORE session validation; v1 ships empty + regression-cage entry) | FR-22a, FR-36 + §10.2 below | `tests/test_reserved_seams.py:assert AuthConfig.anonymous_view_paths field + middleware startswith consult` |
| **S-20** (AMEND v1.2 / A3 — KB-MCP-auth reserved seam; was prose-only pre-v1.2; **SEMANTICS SUPERSEDED v1.4 / module-lanes — see below**) | **`/api/mcp/kb` and `/api/mcp/kb/sse` per-surface credential lane** — reserved for a future commission so agent-side KB callers can authenticate without breaking the loopback trust posture that keeps these paths PUBLIC in v1 | Per-surface credential for KB-MCP agent callers (loopback trust today, surface-scoped token tomorrow) | `daemon/routers/kb_mcp_auth.py` (reserved path) + `daemon/middleware/auth.py` (KB-MCP-aware surface branch — class seam) + `tests/test_reserved_seams.py:KB-MCP-auth seam entry` | OOS-3 rationale (architect §1.1 D1 reserved) | Static grep of `daemon/middleware/auth.py` for the KB-MCP surface branch hook (no implementation; the branch is the seam) + presence of `kb_mcp_auth.py` placeholder module (or a config flag `kb_mcp_auth_enabled: bool = False`). **AMEND v1.4 / module-lanes — SUPERSESSION:** the seam is NO LONGER "future OAuth2 protection" of the KB mounts; per the user ruling the seam becomes **"module-lane classification + own-key enforcement"** — the mounts sit beyond/separate from OAuth2 (MODULE-LANE per FR-39), and the reserved mechanism is the module's own key-check + the `/api/mcp/kb/keys` management family (FR-41, confirm-at-implementation). Seam count UNCHANGED at 20 (semantics re-labeled, row retained; decisions.md D19 records the supersession; the grep assertions above remain the seam-presence contract). |

### 10.1 Non-preclusion acceptance (regression-cage test)

`tests/test_reserved_seams.py` is a unit test that asserts every seam in S-1 through S-20 (**20 canonical seams per AMEND v1.3 / F5** — was "S-1 through S-18" pre-v1.3, which predated the S-19/S-20 additions) has a static presence in the v1 code (or in the migration directory as a stub `.sql` file). The §10.1 `SEAMS` dict carries 21 entries: 20 seam entries + 1 enabling-flag entry (`kb_mcp_auth_enabled` — S-20's KB-MCP-auth seam contributes 2 grep assertions, the flag + the middleware branch). The test is intentionally NOT a runtime behavior test — it exists to ensure that no v1 refactor accidentally removes the seam.

```python
SEAMS = {
    "AuthContext.meta": "daemon/auth/context.py:AuthContext.meta",
    "decorators.require_scope": "daemon/auth/decorators.py:require_scope",
    "live_views dependencies": "daemon/routers/live_views.py:dependencies",
    "grants.sql": "daemon/migrations/versions/*_create_grants.sql",
    "config.allow_insecure_cookie": "daemon/config.py:AuthConfig.allow_insecure_cookie",
    "middleware websocket branch": "daemon/middleware/auth.py:scope['type']=='websocket'",
    "auth.admin tokens revoke": "daemon/routers/auth.py:/api/auth/admin/tokens/revoke",
    "config.idp_logout_enabled": "daemon/config.py:AuthConfig.idp_logout_enabled",
    "config.idp_alias": "daemon/config.py:AuthConfig.idp_alias",
    "subject_alias.sql": "daemon/migrations/versions/*_create_subject_alias.sql",
    "auth.admin tokens issue": "daemon/routers/auth.py:/api/auth/admin/tokens/issue",
    "view_shares.sql": "daemon/migrations/versions/*_create_view_shares.sql",
    "rate_limit.py": "daemon/middleware/rate_limit.py",
    "audit_log_sink.py": "daemon/services/audit_log_sink.py",
    "mtls.py": "daemon/middleware/mtls.py",
    # S-19 (ADDED v1.1) — anonymous_view_paths seam
    "config.anonymous_view_paths": "daemon/config.py:AuthConfig.anonymous_view_paths",
    "middleware anonymous_view_paths consult": "daemon/middleware/auth.py:anonymous_view_paths startswith",
    # S-20 (AMEND v1.2 / A3) — KB-MCP-auth seam (was prose-only pre-v1.2)
    # AMEND v1.4 / module-lanes: seam semantics SUPERSEDED — the KB mounts are
    # MODULE-LANE (beyond OAuth2, decisions.md D19); the seam is now the
    # module-lane classification + own-key enforcement hook (FR-39/FR-41).
    # Grep assertions unchanged (presence contract; count stays 20).
    "kb_mcp_auth_enabled": "daemon/config.py:AuthConfig.kb_mcp_auth_enabled",
    "kb_mcp_auth branch": "daemon/middleware/auth.py:kb_mcp surface branch hook",
    # ... etc.
}

@pytest.mark.parametrize("name,pattern", SEAMS.items())
def test_seam_present(name, pattern):
    """Each reserved seam must be statically present in the v1 code."""
    assert any_file_contains_pattern(pattern), f"Seam missing: {name}"
```

This test runs in <1 second and catches accidental v2-foreclosure refactors early.

### 10.2 v2 migration promise

When the v2 build lands (per-view share tokens / anonymous roots / multiple IdP / federated logout):

1. **No refactor of v1 middleware.** The `AuthMiddleware` stays the same shape; v2 only ADDS branches. **S-19 (`auth.anonymous_view_paths`) is the RESERVED branch point** that lets S-12 (per-view share tokens) and S-13 (password-gated views) bolt on without middleware refactor — a v2 share-token URL (`GET /views/x/share/{token}`, no cookie) reaches `AuthMiddleware` FIRST, and S-19's `startswith` consult on `scope["path"]` lets the request pass through to the router-level handler that owns the per-share validation. Without S-19, the §10.2 promise breaks — the v1 401-contract would 302 every browser-Accept share-token request to login. v1 ships S-19 as `()` (empty default + pass-body + regression-cage entry) and v2 auto-populates from `view_shares` rows.
2. **No migration of `auth_sessions`.** The session table schema is stable; v2 adds tables (`grants`, `view_shares`, `subject_alias`) without altering v1 tables.
3. **No operator action required for upgrade.** Existing tokens continue to work; new tokens get the v2 scope taxonomy.
4. **Backwards compatibility:** v1 tokens issued on the v1 daemon continue to validate on the v2 daemon.

The seam inventory is the architectural contract; the v1 build's regression-cage test is the contractual enforcement.

### 10.3 Non-preclusion matrix vs. UR-4

| UR-4 v2 feature | Seam | Section |
|------------------|------|---------|
| Per-view share tokens | S-12 (`view_shares` table), S-2 (per-route decorator), S-1 (AuthContext.meta), **S-19 (anonymous_view_paths consult)** | §10 |
| Password-gated views | S-13 (extension of S-12 with `password_hash`), **S-19** | §10 |
| Anonymous /views roots | S-3 (per-root policy hook), S-4 (grants table), **S-19** | §10 |
| Per-view grants | S-1 (AuthContext.meta), S-2 (decorator) | §10 |

Every UR-4 v2 feature has at least 3 seams paving its path. The contract is non-preclusion — not implementation.

## Appendix A — Assumptions

| # | Assumption | Affects | Risk if wrong |
|---|------------|---------|---------------|
| A1 | The IdP supports OIDC discovery (modern OIDC standard). | Flow choice + OAuthClient | If OIDC discovery unsupported, fallback is manual `auth.issuer` + `auth.authorization_endpoint` / `auth.token_endpoint` / `auth.jwks_uri` config — see OQ-REQ-10. |
| A2 | Operator can register a `client_id`/`client_secret`/`redirect_uri` at the IdP. | Boot config; OQ-REQ-1 docs enabled | Operator's IdP may have stricter rules (PKCE required, no client_secret for public clients); the requirements accept the most permissive common case. |
| A3 | HTTPS is available (proxy or daemon-side TLS) for production deployments. | Cookie Secure flag (NFR-6); redirect_uri scheme | Operators on LAN may need `allow_insecure_cookie=true`; documented in runbook. |
| A4 | DB is PostgreSQL (per blueprint "PostgreSQL is the primary database"). | Session store (`auth_sessions`, etc.) | **AMEND v1.2 / A1:** SQLite-compat is preserved at the schema layer — auth tables are inert on BOTH backends (D17 leader ruling, no `require_sqlite_rejection`, no `MANUAL: TRUE` markers, no boot-refusal); the feature gate is the absence of `auth.enabled`, not a runtime refusal. The auth tables exist on both backends; the SQLite-auth fixtures (`auth_enabled_app` etc.) keep working. |
| A5 | The 12 inventoried consumers are operator-controlled and can store a bearer token in env. | Machine lane | If a consumer is read-only-shell or otherwise can't store a secret, the runbook documents manual token rotation alternatives. |
| A6 | The SPA on auth-on sees a 302-redirect on first protected load after session expiry; the existing redirect-handling in `frontend/src` handles this transparently. | Browser lane | If the SPA has special needs for custom login UX, see OQ-REQ-2 — answer (b) requires per-route SPA change. |
| A7 | The IdP does not strip security-critical headers from the callback response (no-op for the redirect-based flow; but the assumption is that GET /authorize preserves query params). | Flow choice | If IdP strips params, the state CAS will reject the callback with `400 STATE_MISMATCH`; runbook documents. |
| A8 | Operator will restart the daemon on config changes (existing pattern at `daemon/services/instance_lifecycle.py`). | Boot validation; live config flip not in v1 | If live-flip is required in v1, OQ-REQ-11 (not enumerated) must be raised. |
| A9 | The `httpx` dependency is available (already in pyproject for LLM proxy). | OAuthClient | If missing, the OAuthClient is the reason it gets added. |
| A10 | The `daemon/repositories/<domain>/` factory pattern is followed; a single SQLModel file per table; a single migration per table (`daemon/migrations/versions/YYYYMMDD_HHMMSS_description.sql`). | Schema | **AMEND v1.2 / A1:** PG-only auth tables are NOT the contract — auth tables are created on BOTH backends via the standard migration runner (D17 leader ruling); no SQLite boot-refusal exists; the per-table `.sql` convention holds for BOTH backends. |
| A11 | Operator approval of the §9 Open Questions will arrive BEFORE the plan-creation worker drafts `phaseN-plan.md`. | Plan phase | If OQs remain open past planner handoff, plan worker should treat each as a blocker and request decision. |
| A12 | Replacement worker instance resume: the planner can locate the partial document at `.agents/shared/planning/oauth2-auth/requirements.md` and consume sections 1-10 (and appendices) independently. | Resilience (per dispatcher's instruction) | If the planner's tooling requires the file in one atomic read, the incremental write breaks that contract; not the case here (markdown is line-delimited). |

## Appendix B — Out of Scope (deferred to later phases)

These items were explicitly listed as out-of-scope in §1.3. They are restated here as a consolidated reference for the planner; each has an architectural seam in §10 (non-preclusion requirements).

### B.1 v2 deferred features (UR-4 anchor)

| Feature | Reason for deferral | Seam |
|---------|---------------------|------|
| Per-view share tokens (`/views/{id}/share`) | Out of v1 scope per dispatch | S-12, S-1 |
| Password-gated views (`/views/{id}/password`) | Out of v1 scope per dispatch | S-13 |
| Anonymous /views roots | Out of v1 scope per dispatch | S-3, S-4 |
| Multiple-IdP support (workforce + customer simultaneously) | v1 single-issuer | S-9 |
| Federated logout via `end_session_endpoint` | v1 local-logout only | S-8 |

### B.2 v1 architecture reserve items

| Feature | Reason for reserve (not v1 implementation) | Seam |
|---------|--------------------------------------------|------|
| CORS hardening | Per project CN risk 2026-10-10 — separate ticket | n/a (deferred to OOS-5 ticket) |
| Bot brute-force IP bans | IdP's job; not v1 | S-14 (rate limit per subject) |
| DB-encrypted session payload | PG encryption-at-rest is a separate ticket | n/a |
| Self-service bearer-token HTTP mint (admin-scope) | Bootstrap problem in v1 | S-11 |
| `/vscode` WebSocket auth | Loopback posture unchanged in v1 | S-6 |
| Subject aliasing across IdP rotations | Treat `sub` as opaque in v1 | S-10 |

### B.3 Deferred-only-as-decisions

These are technical-analysis-pending OQs that will not block the requirements but inform the plan:

- **OQ-3 in technical-analysis.md**: scope taxonomy granularity (broad vs fine). Adopted (c) hybrid per OQ-REQ-7.
- **OQ-7 in technical-analysis.md**: CSRF strategy — **Origin-guard predicate adopted v1.2 (D8); double-submit SUPERSEDED.** The v1 implementation uses presence-based Origin/Referer validation (browser lane) + headerless exemption (machine/bearer lane) per `decisions.md D8 v1.2`; the v1.1 "implement now at low cost" recommendation for double-submit tokens is RETRACTED in favor of the Origin-guard predicate (lower implementation cost + preserves NFR-18 zero-SPA-change). Token-echo validation against `ensemble_csrf` cookie is reserved as v2 defense-in-depth.
- **OQ-8 in technical-analysis.md**: per-view auth hook placement (decorator vs config-map). Adopted decorator.

### B.4 Items NEVER in scope (per system architecture)

These are out-of-scope by design and would not be added in any future iteration:

- Re-implementation of the FastAPI app structure.
- Replacement of PostgreSQL with another database for the auth store.
- Cross-origin passing of cookies to domains not in `auth.allowed_origins`.
- Storing plaintext bearer tokens in the database (architectural prohibition; sha256 only).
- Storing plaintext client secrets in logs.
- Disabling NFR-3 (health-probe publicness) — this is a structural invariant, not a config.
- Disabling the state CAS (single-arbitration-point invariant).

## Appendix C — Glossary

| Term | Definition |
|------|------------|
| **AC** | Acceptance Criterion — a single testable Given/When/Then assertion. |
| **AOR** | Area of Responsibility — typically used in scope-defining. |
| **ASGI** | Async Server Gateway Interface — Starlette/uvicorn's underlying async protocol. The middleware operates at this layer. |
| **Auth Code + PKCE** | OAuth2 Authorization Code grant flow with Proof Key for Code Exchange (RFC 7636). The flow used for browser-to-IdP login. |
| **AuthContext** | The resolved auth state attached to a request (subject, scopes, auth_method, meta). |
| **Bearer token** | A static credential presented as `Authorization: Bearer <token>` per RFC 6750. Used by machine clients. |
| **CAS** | Compare-And-Swap — atomic state transition (e.g., `auth_states: pending → consumed`). The single exactly-once arbitration point at callback. |
| **CSRF** | Cross-Site Request Forgery. Mitigated by **SameSite=Lax cookie + Origin-guard predicate** (per `decisions.md D8 v1.2` — supersedes the v1.1 double-submit-text; the Origin-guard predicate fires ONLY when `Origin`/`Referer` headers are present, browser lane; headerless machine/bearer requests are EXEMPT per rule-1 precedent at `daemon/routers/maintenance_origin_guard.py:24-43`). Token-echo validation against `ensemble_csrf` cookie is reserved as v2 defense-in-depth. |
| **CORS** | Cross-Origin Resource Sharing. Wildcard + credentials configuration is the dangerous combination — requires auth.allowed_origins when auth ON. |
| **DB** | Database; auth uses PostgreSQL. |
| **Discovery doc** | The `{issuer}/.well-known/openid-configuration` document an OIDC provider publishes. Cached with TTL. |
| **FR** | Functional Requirement. |
| **Front-end** | The Angular SPA at `frontend/src/**`. |
| **HOTP / TOTP** | HMAC-based / Time-based One-Time Password. NOT used in v1 (out of scope). |
| **HTTPS** | HTTP Secure. Required for production deployments; required for `Secure` cookie flag. |
| **IdP** | Identity Provider — the external OAuth2/OIDC server (e.g., Okta, Auth0, Keycloak). |
| **JWKS** | JSON Web Key Set — the IdP's published set of public keys, fetched over HTTPS, used to verify ID-token signatures. |
| **JWT / JWE** | JSON Web Token / JSON Web Encryption. The ID-token's wire format. |
| **Lane A** | Cookie session lane (browser / SPA). |
| **Lane B** | Static bearer-token lane (machine clients). |
| **LIFO** | Last-In-First-Out. The order middleware executes (last registered is OUTERMOST). |
| **MFA** | Multi-Factor Authentication. Operates at the IdP; not v1's concern. |
| **Middleware** | ASGI middleware class wrapping the FastAPI app. AuthMiddleware is the v1 gate. |
| **NFR** | Non-Functional Requirement. |
| **OIDC** | OpenID Connect — an OAuth2 extension for identity. v1 requires OIDC-compliant IdP. |
| **PKCE** | Proof Key for Code Exchange (RFC 7636). |
| **Provider Mode P / Mode T** | From the project CN plugin model — N/A here, just noting that the auth build is autonomous of the plugin path. |
| **Replay attack** | Attacker resubmits a captured request (callback URL, ID-token). Defeated by state CAS + (optionally) replay cache. |
| **RFC 6749** | The OAuth 2.0 Authorization Framework. |
| **RFC 6750** | The OAuth 2.0 Bearer Token Usage. |
| **RFC 7636** | PKCE specification. |
| **SameSite** | Cookie attribute (Strict, Lax, None) controlling cross-origin behavior. v1 default Lax. |
| **Session** | A server-side row in `auth_sessions` representing a user/browser authentication state, identified by `ensemble_sid` cookie. |
| **SSE** | Server-Sent Events. The long-lived HTTP event stream (EventSource) used for instances/jobs/notifications/migration/workspace. |
| **State** | The OAuth2 state parameter — a CSRF mitigation, plus the exactly-once CAS handle. |
| **State CAS** | The `auth_states: pending → consumed` atomic swap. Single exactly-once arbitration point at callback. |
| **STS** | Secure Transport Security (HTTP Strict-Transport-Security). Out of scope for v1 but worth noting. |
| **Subject** | The ID-token `sub` claim — the user's stable identifier at the IdP. |
| **TTL** | Time-To-Live — duration before a credential expires (sessions default 8h, tokens default 30d, states 5 min). |
| **URI / URL** | Uniform Resource Identifier / Locator. |
| **V1 / V2** | v1 = this build (Auth Code + PKCE browser + bearer machine). v2 = future (per-view share tokens, multi-IdP, federated logout). |

---

**End of requirements.md**
