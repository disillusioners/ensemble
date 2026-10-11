# Plan Overview: OAuth2 Authentication Support (v1)

**AMEND v1.4 — 2026-10-10 (user ruling: module auth lanes)**

**AMEND v1.3 — 2026-10-10 (approver iteration-001 fixes)**

Date: 2026-10-10
Author: planner[v2] via plan-creation worker (final synthesis wave of the 3-wave planning cycle)
Status: Draft — ready for user approval of the follow-up implementation commission
Repo: `/home/nea/ensemble-src`

> **Amendment pass v1.4, 2026-10-10 (user ruling: module auth lanes — sole authority: the dispatch's 3-point ruling).** Webhook and MCP-KB modules sit beyond/separate from OAuth2 (own UI + own API-key management; NOT human-OAuth2 surfaces). New MODULE-LANE matrix class ([REQ] §5.0 inventory incl. two NOT-FOUND facts; §5.6 counts reconciled: PUBLIC 12→11, MODULE-LANE 2, PROTECTED 30 legacy + 1 new `/api/mcp/kb/keys` family). Middleware derives THREE path classes (D18 extended; supersedes the v1.1 `AUTH_DEFAULT_PUBLIC_PATHS` placement of `/api/mcp/kb*`). Lane isolation is a HARD requirement (D20; AC-40 negative cross-lane matrix + AC-41 ON/OFF equivalence). New FR-39…FR-42 + AC-40…AC-42 (FR census 38→42; §7 census 17→18 rows; row 17 flipped auth-INDEPENDENT). S-20 semantics SUPERSEDED to "module-lane classification + own-key enforcement" (decisions.md D19; **NFR-11 seam count explicitly unchanged at 20/20**). §6 risks gain ML-1…ML-4 (incl. the research finding: `webhook_secret` returned UNMASKED via GET /api/sources, sources.py:81 — pre-existing). **ZERO OAuth2 config delta** ([REQ] §6.6). `architecture-recommendation.md` FROZEN; technical-analysis.md untouched this pass. Every v1.4 edit is greppably marked `AMEND v1.4 / module-lanes`.

**AMEND v1.3 — 2026-10-10 (approver iteration-001 fixes — see `.agents/approver/oauth2-auth-tracking.md`).** This brief is the sole v1.3 authority. All 5 BLOCKING issues fixed in plan-text only (no architecture reversal):
> - **B1** — `/vscode` matrix aligned to `ENSEMBLE_AUTH_VSCODE_PROTECTED` flag default `false` (loopback posture unchanged in v1; PROTECTED only when flag opt-in `true`); §5.6 mount count fixed to 1 (the `/vscode` Starlette Mount, conditional on the flag; KB Mount moved PUBLIC per D1); new §7.6 /vscode consumer inventory closes the loop with "N/A-loopback" rationale (no repo machine consumer hits `/vscode`).
> - **B2** — FR-2 reworded to "auth tables exist as inert schema on both backends per D17; when `auth.enabled = false` they are NEVER read or written by any v1 code path and the auth middleware is NOT registered"; overview §8 self-contradicting sentence fixed (single coherent default-OFF statement).
> - **B3** — §1.2 (15 FR anchor cells), §2.2, §2.3, §2.5 stale FR anchors mechanically re-pointed to current IDs.
> - **B4** — P3 (Phase 3) now carries a `T3.9` task wiring the `auth_dependency_status` sub-field onto `ReadyzResponse` via the readiness composite refresher pattern at `daemon/api.py:1544-1555` / `daemon/services/readiness.py:208-233`; coverage maps updated; technical-analysis.md:989 aligned to FR-30 enum.
> - **B5** — Phase 2 G2.2 reworded: migration runner UP/DOWN apply/revert on scratch SQLite ONLY (no PG runner); PG leg via `SQLModel create_all` on scratch PG + index-parity test (precedent: `tests/migration/test_drift_events_index_parity.py`).
> - **12 non-blocking folds (F1–F12):** `pyjwt[crypto]` declared explicitly in T2.7; SKIP LOCKED dialect-guard note at phase2 D2.4/T2.3 + G2.1 + phase5 T5.10; router-cage match-set reconciled (28 api_router mounts pinned as the cage target; residual textual matches documented separately — 2 app-level calls + 1 prose comment, census verified 2026-10-10; phase4 D4.7 pins the EXACT match target); consumer-count / seam-count / tally reconciliation; **AC-38 numbered into the skipped slot for FR-38** (v1.2 register jumped AC-37 → AC-39; the interim "AC-40" numbering is collapsed into AC-38 so the AC sequence is gapless); TA Debt #7 marked SUPERSEDED; TA lifespan-site anchor corrected; §9.2–9.9 RESOLVED banners + §5.2 oauth2-redirect row moot + CSRF residue text superseded by D8 v1.2; T4.1 bootstrap consumption pinned to SENTINEL ROW; T1.2 matrix count reconciled (14 active rows in §6.3 after SQLite row retracted); overview "37 FRs" → "38 FRs" + line count to actual (1,836 at v1.3 close).
> - **`architecture-recommendation.md` AND `.agents/approver/oauth2-auth-tracking.md` remain FROZEN** — never edited in this sweep.

> **Amendment pass v1.2, 2026-10-10 (reviewer APPROVED-WITH-NOTES sweep, final pre-approver).** This brief is the sole v1.2 authority (no reviewer artifact on disk). 1 CRITICAL + 8 MAJOR + 12 MINOR amendments applied:
> - **C1 (CRITICAL) — CSRF predicate rewrite** — see `decisions.md D8` and `phase3-plan.md T3.4`: CSRF enforcement triggers ONLY when the request carries Origin or Referer headers (browser lane — presence-based branching, mirroring `daemon/routers/maintenance_origin_guard.py` rule-1). Headerless machine/bearer requests are EXEMPT. NFR-18 (zero `frontend/src/**` changes) stays INTACT. New **AC-39** asserts SPA mutation from the unchanged SPA succeeds — INV-3/SPA-lockout closed BY CONSTRUCTION. The previous v1.1 double-submit-only text in D8 is SUPERSEDED where it conflicts.
> - **A1 — D17 (inert tables) propagation** — App-A A4/A10 in `requirements.md`; T2.3, G2.1, risk-2 in `phase2-plan.md`; T5.10 SQLite leg inverts from "refuses" to POSITIVE "auth-ON boots on SQLite" in `phase5-plan.md`; risk-3 in `phase1-plan.md`. All "SQLite refusal/rejection/PG-only" wording aligned to D17.
> - **A2 — canonical `external_redirect_base` tier-1 field** — `ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE` env var added to `requirements.md §6.1`; `external_redirect_base: str | None = None` field added to `AuthConfig` sketch in §6.2; chain: tier-1 `external_redirect_base` > tier-2 `live_view_external_base_url_fallback` > Host-capture > bind. `ENSEMBLE_AUTH_REDIRECT_URI` reconciled to exact-URI override ONLY. §6.3 row 7 fixed (proxy deployment via env is NOT refused).
> - **A3 — ONE canonical seam-cage list** — KB-MCP-auth seam added as **§10 row S-20** in `requirements.md`; §10.1 SEAMS dict gains `kb_mcp_auth_enabled` + `kb_mcp_auth branch` entries; **seam count is now 20** (19 in v1.1 + KB-MCP-auth as a canonical row); NFR-11 row reworded.
> - **A4 — mechanical FR re-anchor in `requirements.md` §2.x** — UR-4→FR-36/37/38+AC-36/37; UR-1→FR-1/26/27; UR-2→FR-12/13/14+FR-32; UR-3→FR-15/22/23+NFR-3. §3.1 summary table gains **FR-16a**, **FR-22a**, **FR-38** rows.
> - **A5 — FR-19/AC-19 four-middleware sequence** — FR-19 corrected: registration CORS→SelectiveAccessLog→HostCapture→Auth (Starlette `insert(0)` LIFO ⇒ execution Auth→HC→SAL→CORS). AC-19 asserts the FULL four-step execution sequence.
> - **A6 — external webhook senders = YES-WITH-CONFIG row** — `requirements.md §7` row 17 added (Telegram/Slack/Discord/WhatsApp sources without `webhook_secret` configured at `webhooks.py:63-74`); §7.2 aggregate verdict updated (5→6 YES WITH CONFIG); phase6 T6.1 runbook Enable-step instructs per-source `webhook_secret` setup BEFORE enabling auth.
> - **A7 — restart-scenario precision** — AC-S4 in `requirements.md` + T5.5 in `phase5-plan.md`: 5 public probes are reachable unauthenticated POST-restart BEFORE any session revalidation.
> - **A8 — zero DECISION-NEEDED leftovers** — `requirements.md §5.2` rows for `/docs`, `/redoc`, `/openapi.json` are now **PUBLIC (kill-switch per OD-1)** with `ENSEMBLE_AUTH_DOCS_ENABLED`; §9.1 (OQ-REQ-1) is **RESOLVED with OD-1 pointer**.
> - **12 MINORs folded opportunistically:** (1) stale tallies 28→31 routers (audit `daemon/api.py:3097-3142`); (2) phase2 T2.4 anchor `manager.py:315-345`→`api.py:1544-1555`; (3) K10 vs D17 wording consistency; (4) `requirements.txt` reference→`pyproject`/`uv.lock`; (5) SPA Accept-header caveat; (6) SSE authenticates at initial GET only — risk-register line in FR-12; (7) §6.2 `live_view_external_base_url_fallback` comment that the field is env-populatable at lifespan; (8) plan-overview §8 registration-vs-execution order clarity; (9) phase3 risk-2 strike pre-D18 hand-listed-frozenset wording; (10) TA rule-3 CORS-preflight rationale precision; (11) AC-16a (phantom reference) defined explicitly; (12) T4.5→T4.6 cross-reference in phase6-plan.md T6.1.
> - **`architecture-recommendation.md` is FROZEN** — never edited in this sweep.

> **Amendment pass v1.1, 2026-10-10 (architect CONDITIONAL PASS).** This
> plan carries the architect's CONDITIONAL PASS amendments. The amendment
> log per file:
> - `decisions.md`: 15 OD rulings folded to DECIDED (plain for OD-1/4/5/7/8/10/11/13/14/15; conditional for OD-2/3/6/9/12 with conditions folded in); D17 added (SQLite inert tables); D18 added (route-metadata public-set derivation); D6 field name `live_view_external_base_url_fallback` ratified; line-drift fix applied to D7 and D10; OPEN items table retired.
> - `technical-analysis.md`: D1 (Mount fix) — rule 6 amended; D2 (redirect chain wiring) — pseudocode `:749-754` amended; C3 (route-metadata derivation) — public_path_set derived; C4 (boot preflight exit-78); C1+C2 referenced; SC-4/AC-S3 publicness precision; line-drift fixes.
> - `requirements.md`: FR-22 / FR-22a / FR-26 / FR-36 / FR-38 / FR-16a / NFR-7 / NFR-8 / NFR-11 amended or added; S-19 added to §10 seam table; §10.2 names S-19 as the reserved branch point; `require_sqlite_rejection` env-var row REMOVED (D17); `live_view_external_base_url_fallback` field added; `ENSEMBLE_AUTH_ANONYMOUS_VIEW_PATHS` env var added; line-drift fixes.
> - `phase1-plan.md`: exit-78 misconfig lane re-sited to boot preflight beside `_boot_db_preflight` (C4).
> - `phase2-plan.md`: sweeper conditions paragraph added (per-batch connection acquisition + 5-10 s statement timeout — 2026-10-10 checkpoint-pool incident sensitivity; dual-sweeper same-DB runbook note).
> - `phase3-plan.md`: D18 route-metadata public set replaces hand-listed frozenset (C3); `/api/mcp/kb` and `/api/mcp/kb/sse` in `AUTH_DEFAULT_PUBLIC_PATHS` v1 (D1); Mount-traversal one-line regression test + framework pin (D1); KB-MCP-auth seam reserved.
> - `phase4-plan.md`: C1 router-mount scope-declaration cage test added; C2 conditions referenced (deny-list on `ens_env_read` override path, one-shot bootstrap consumption, runbook removal step).
> - `phase6-plan.md`: KB-MCP loopback trust rationale added (D1); bootstrap env-var removal + plain statement that bash+DB agents can still reach the mint CLI (C2); dual-sweeper same-DB note (resilience).
> - **THIS FILE:** header note added; §7 OD ledger closed (all DECIDED); §6 risks R3 added (source-forgery follow-up); SC-4/AC-S3 wording precision; phase table amended with `AMENDED v1.1` markers where touched.
>
> **Phase table amendments:** P1 (exit-78 preflight re-site), P3
> (route-metadata derivation, /api/mcp/kb PUBLIC, framework pin, Mount-
> traversal regression test), P4 (C1 cage test, C2 conditions).

> **Inputs (frozen, DO NOT re-litigate).** This plan synthesizes two
> sibling artifacts already on disk and cites them by §:
> - `.agents/shared/planning/oauth2-auth/technical-analysis.md` (1,386 lines) — **[TA]** — architecture.
> - `.agents/shared/planning/oauth2-auth/requirements.md` (1,836 lines at v1.3 close — line-drift corrected v1.3 / F12) — **[REQ]** — FRs / NFRs / ACs / matrix / config spec / compatibility table.
>
> Where [TA] and [REQ] disagree, [REQ] wins on *what to build and how to
> verify*; [TA] wins on *how it is wired architecturally*; the one schema
> convention conflict (single vs per-table migration files) is resolved in
> `decisions.md §D14` in favor of the repo-wide per-table blueprint rule.
> All OQs from both artifacts are rolled into the Open Decisions Ledger
> below (OD-1…OD-15) with recommended defaults; the implementation
> commission may proceed on defaults if the user does not object.

---

## 1. Objective

Add an **opt-in, default-OFF** OAuth2 authentication layer to the ensemble
daemon's HTTP surface:

- **Lane A (browser / Angular SPA):** OAuth2 Authorization Code + PKCE
  against an OIDC-compliant IdP, server-side PG-backed sessions,
  `ensemble_sid` HttpOnly cookie, 302-redirect for browsers / 401 +
  `WWW-Authenticate` for machines ([TA] §Flow Choice; [REQ] §1.1).
- **Lane B (machine / loopback):** operator-minted static bearer tokens,
  stored hash-only (`sha256(token)`), scope-tagged, consumed by the 12
  inventoried loopback/same-host consumers without code change ([REQ] §7).

Hard invariants carried from the user requirements ([REQ] §2, UR-1…UR-4):

1. **UR-1:** default OFF = byte-identical behavior, provable (NFR-1/2, AC-1/2, AC-S1).
2. **UR-2:** when ON, the full endpoint protection matrix is enforced ([REQ] §5).
3. **UR-3:** `/livez`, `/readyz`, `/api/health`, `/api/info`,
   `/api/maintenance/checkpoint-cleanup/availability` stay PUBLIC always —
   restart/upgrade machinery depends on them (NFR-3, AC-22, AC-S3).
4. **UR-4:** future /views share tokens, password-gated views, and anonymous
   roots are architecturally accommodated (**20 reserved seams in v1.2 / A3** — was 18 in v1.0, 19 in v1.1, **20 in v1.2 incl. KB-MCP-auth seam as a canonical §10 row**; see [REQ] §10) but NOT built.
5. **Module auth lanes (AMEND v1.4 / module-lanes — user ruling):** webhook
   and MCP-KB modules sit beyond/separate from OAuth2 — MODULE-LANE
   classification ([REQ] §5.0), lane isolation as a HARD requirement
   (decisions.md D20), OAuth2 governs ONLY their token-management CRUD
   (decisions.md D19); module lanes behave identically with auth ON and OFF
   (FR-42).

**Definition of done:** all 42 FRs (38 through v1.3 incl. FR-16a + FR-22a + FR-38; **+4 module-lane FRs — AMEND v1.4 / module-lanes**; was "38 FRs" pre-v1.4) + 18 NFRs satisfied per their ACs;
the four cross-cutting scenarios AC-S1…AC-S4 pass as
`tests/e2e/test_auth_lifecycle.py`; the runbook
`docs/runbooks/oauth2-auth.md` ships with the implementation; zero
regression in the existing test suite with auth OFF.

## 2. Scope

### 2.1 In scope

| # | Capability | Anchors |
|---|------------|---------|
| S1 | AuthConfig section (`ENSEMBLE_AUTH_*`, 23 env vars) + boot fail-fast validation | [REQ] §6; FR-1/2/26/27/28; NFR-7/8 |
| S2 | PG store: `auth_sessions` + `auth_states` + `auth_bearer_tokens` tables, repository, sweepers | [TA] §Session Model; FR-7/10; NFR-4/8 |
| S3 | OAuthClient service: OIDC discovery (TTL cache + backoff), code exchange, JWKS + kid-miss refresh, ID-token validation | [TA] §Integration 3-5; FR-4/5/8/9/29/30/31 |
| S4 | `AuthMiddleware` (pure ASGI, LIFO-outermost) + `AuthService` + auth router (`/api/auth/login|callback|logout|me`) | [TA] §Component Architecture; FR-12/13/19-25 |
| S5 | Endpoint protection matrix wiring (PUBLIC set + PROTECTED default + SPA PUBLIC-ASSETS rule + docs trio + maintenance canary) | [REQ] §5; FR-22/23 |
| S6 | Machine lane: bearer mint CLI (`daemon.auth.cli issue-token|revoke`), scopes + `@require_scope`, consumer enablement | [TA] §Machine-Client Lane; [REQ] §7; FR-16/17/18/32 |
| S7 | In-process fake OIDC provider + two-fixture test estate + 4-scenario lifecycle e2e + boot-e2e auth canary + perf smoke | [TA] §Testability; [REQ] §7.5, §8.3; FR-33/34 |
| S8 | Runbook `docs/runbooks/oauth2-auth.md` + CHANGELOG + rollout note | FR-35; NFR-17 |
| S9 | Reserved-seam regression cage (`tests/test_reserved_seams.py`) | FR-36/37; NFR-11 |
| S10 | Module auth lanes (**AMEND v1.4 / module-lanes (NEW)**): three-class route-metadata derivation + module-lane middleware pass-through (both auth states); MCP-KB mount key-check mechanism (confirm-at-implementation); NEW small `/api/mcp/kb/keys` CRUD family under OAuth2 human lane; webhook-secret management annotated as the EXISTING `/api/sources*` CRUD (no new family); lane-isolation enforcement + negative test matrix | [REQ] §5.0, §6.6, §7.7, FR-39..42; AC-40..42; decisions.md D19/D20 |

### 2.2 Out of scope (reserved seams — explicitly future, NOT precluded)

Per [REQ] §1.3 / §10 and [TA] §Reserved Seams: multi-IdP routing (S-9),
federated logout (S-8), per-view share tokens (S-12/S-13), anonymous
/views roots (S-3/S-4), /vscode WebSocket auth (S-6), self-service token
mint HTTP endpoint (S-11), subject aliasing (S-10), CORS broad hardening
(OOS-5 — separate ticket; only the auth-ON boot-refusal guard FR-27 is in
scope), bot IP bans (S-14), DB-encrypt-at-rest for session payloads,
grants/view_shares/subject_alias migration stubs beyond the
regression-cage grep contract (the `.sql` stubs are created as EMPTY
`-- UP`/`-- DOWN` placeholders only where the cage test requires file
presence — see phase5 task T5.9).

## 3. Success Criteria (mapped to AC-S1…S4 + key FRs/NFRs)

| # | Criterion | How measured | Threshold |
|---|-----------|--------------|-----------|
| SC-1 | **Default-OFF zero disruption** (AC-S1, AC-1, NFR-1/2) | Full existing pytest suite + boot-e2e canaries with the auth module present but `enabled=false`; response-body snapshot diff on `/api/*`, `/views/*`, `/livez`, `/readyz`, `/api/info` vs pre-build | 0 diff; 0 fixture/config edits; `auth_*` tables untouched |
| SC-2 | **Middleware not registered when OFF** (AC-2, FR-2) | Introspection test: `AuthMiddleware` absent from `app.user_middleware`; no cookie ever set | Assert holds |
| SC-3 | **Auth ON + provider healthy** (AC-S2) | Full browser flow via in-process fake OIDC + bearer curl + all **18** consumers (**AMEND v1.4 / module-lanes: was 17 per v1.3 / F4 — now 10 YES-UNCHANGED + 6 YES-WITH-CONFIG + 2 N/A; row 17 flipped auth-INDEPENDENT, row 18 MCP-KB clients added**; was 12 pre-v1.2 / A6, was 13 post-v1.1 typo) ([REQ] §7) | All 200; SPA `frontend/src` diff = 0 (NFR-18) |
| SC-4 | **Health-probe public invariant under every scenario** (AC-22, AC-S3, NFR-3) | `tests/e2e/auth_health_probes.sh` — 5 probes unauthenticated, auth ON, IdP DOWN | 5/5 reachable (**PUBLICNESS — never 401, no auth dependency**; `/readyz` may be 200-degraded or 503-during-PG-outage via the pre-existing `database` component — that is the readiness contract working, NOT an auth regression. AMENDED v1.1) |
| SC-5 | **Restart survival** (AC-S4, NFR-4) | Mint sessions → SIGKILL → restart → revalidate | ≥99.9% pre-restart sessions still validate |
| SC-6 | **Replay/CSRF/cookie defense** (AC-6/7/11/25, NFR-6) | State-CAS replay, expired state, cookie-flag inspection, `return_to` sanitization unit pack | All pass |
| SC-7 | **Boot fail-fast matrix** (AC-26/27, NFR-7/8) | Invalid-combination boot tests ([REQ] §6.3 — **14 active rows** per AMEND v1.3 / F11 reconciliation; 15 table rows incl. the struck D17 SQLite row) | exit 78 + ERROR with missing-field list, no partial boot |
| SC-8 | **Machine lane** (AC-16/17/18, FR-16-18) | CLI mint/revoke + hash-only storage + scope 403 path | All pass; `grep` plaintext-token leak = 0 (NFR-12) |
| SC-9 | **Seam non-preclusion** (AC-36/37, NFR-11) | `tests/test_reserved_seams.py` static presence check | All listed seams present |
| SC-10 | **Latency/throughput budget** (NFR-9/16) | `tests/perf/` ASGITransport benchmark | p99 delta < 5 ms; ≥1000 RPS validate path |
| SC-11 | **Docs** (AC-35, NFR-17) | `docs/runbooks/oauth2-auth.md` 7 required sections present | 7/7 |
| SC-12 | **Module-lane isolation + equivalence** (**AMEND v1.4 / module-lanes (NEW)** — AC-40/41, FR-39/40/42) | `tests/integration/test_module_lane_isolation.py` negative matrix (webhook secret × MCP-KB mount × general API × OAuth2 cookie/bearer, each token in each wrong lane → 401/403) + module-lane byte-identical behavior auth-OFF vs auth-ON | 0 cross-lane acceptances; 0 module-lane behavior diffs across auth states |

## 4. Phase Summary

| Phase | Name | Objective | Deliverables | Exit criteria | Size | Depends on |
|-------|------|-----------|--------------|---------------|------|------------|
| 1 | Config + boot-validation foundation | `AuthConfig` BaseSettings section, 23 `ENSEMBLE_AUTH_*` vars, invalid-combination fail-fast (exit 78 — **AMENDED v1.1 / C4: re-sited to boot preflight beside `_boot_db_preflight`, exit-75 precedent `daemon/__main__.py:319-324`**), `config.yaml` composition | `daemon/config.py` AuthConfig + validators; config tests | Validation-matrix tests ([REQ] §6.3) green; OFF-state config parsing unchanged | **M** — one settings section but a **14-row** invalid-combination matrix (AMEND v1.3 / F11 — was 13 in v1.2, was 12 in v1.1 pre-D17) + SecretStr hygiene | — |
| 2 | Session store + migrations + OAuth client | PG-backed `auth_sessions`/`auth_states`/`auth_bearer_tokens`, repository + sweepers, `OAuthClient` (discovery/cache/backoff, token exchange, JWKS + kid-miss, ID-token validation). **AMENDED v1.1 / D17:** auth tables inert on SQLite (no `MANUAL: TRUE` markers, single code path, `require_sqlite_rejection` env var REMOVED). **AMENDED v1.1 sweeper conditions:** per-batch connection acquisition + 5-10 s statement timeout (2026-10-10 checkpoint-pool incident sensitivity); dual-instance same-DB (dev checkout) is safe via SKIP LOCKED + idempotent deletes — documented in runbook, no guard built. | 3 migration `.sql` files; `daemon/repositories/auth_session/`; `daemon/services/oauth_client.py`; sweeper wiring | Unit packs for repo CRUD/CAS/sweep + OAuthClient (discovery TTL, JWKS rotation) green on PG | **L** — 3 new tables + a stateful HTTP client with 4 failure modes | P1 |
| 3 | Middleware + auth routes + protection matrix | `AuthMiddleware` (conditionally registered, LIFO outermost), `AuthService`, 4 auth routes, redirect_uri via `BaseURLResolver` (with `live_view_external_base_url_fallback` tier-1 wiring per D2), **AMENDED v1.1 / D18: public-path set derived from route metadata** (routes carry `public-probe` tag; construction walks `app.routes`; LOGS derived set at startup), **`/api/mcp/kb` and `/api/mcp/kb/sse` in `AUTH_DEFAULT_PUBLIC_PATHS` v1 (D1)**, **Mount-traversal one-line regression test + framework pin (Starlette 0.52.1 / FastAPI 0.133.1)**, **KB-MCP-auth seam RESERVED**, `AuthContext` + `@require_scope` seams (incl. S-19 anonymous_view_paths consult before session validation), **AMEND v1.3 / B4: T3.9 readiness sub-field wiring (`ReadyzResponse.auth_dependency_status`, FR-30 enum, verdict never flips)** — **AMEND v1.4 / module-lanes: T3.10 three-class route-metadata derivation (public / module-lane / oauth2-protected) + module-lane middleware pass-through in BOTH auth states; T3.11 MCP-KB mount key-check mechanism (confirm-at-implementation); T3.12 NEW `/api/mcp/kb/keys` key-CRUD router (OAuth2 human lane); supersedes the v1.1 "/api/mcp/kb in AUTH_DEFAULT_PUBLIC_PATHS" placement with the module-lane class; webhooks family reclassified MODULE-LANE in the T3.8 wiring + D4.7 scope manifest** | `daemon/middleware/auth.py`; `daemon/services/auth_service.py`; `daemon/routers/auth.py`; `daemon/services/readiness.py` (additive `detail` sub-field); `create_app()` wiring | Dispatch-order + 401/302 + LOGIN_NOT_CONFIGURED + preflight/WS pass-through tests green; OFF-state app object graph unchanged (SC-2); readiness sub-field enum + verdict-freeze green (G3.7) | **L** — the core gating surface; every route family touches it | P1, P2 |
| 4 | Machine-bearer lane | `daemon.auth.cli` (issue-token/revoke/bootstrap, **AMENDED v1.1 / C2: one-shot consumption**, **AMEND v1.3 / F10: sentinel row in `auth_bootstrap_consumption` table**), bearer verification + scope enforcement, **AMENDED v1.1 / C1 + AMEND v1.3 / F3: router-mount scope-declaration regression cage test** (per-family manifest, 28 `api_router.include_router(` mounts pinned as canonical match target — the 3 app-level Mounts documented separately), **AMENDED v1.1 / C2: `ens_env_read` deny-list `ENSEMBLE_AUTH_*` on the override path**, consumer enablement (env-var bearers for the **7 YES-WITH-CONFIG consumers** — AMEND v1.3 / F4 count) | CLI module; bearer verify path in middleware; `@require_scope` applied to route families per [REQ] §7.3 | AC-16/17/18 green; consumer enablement documented; misconfigured consumer → visible 401 (fail loudly) | **M** — one CLI + one lookup path + decorator, but consumer matrix verification is broad | P3 |
| 5 | Test estate: fake OIDC + 4 scenarios + canaries | `tests/fakes/fake_oidc_provider.py`, `auth_enabled_app`/`auth_disabled_app` fixtures, unit/integration packs, `tests/e2e/test_auth_lifecycle.py` (AC-S1…S4), `auth_health_probes.sh`, `boot-e2e-auth-daemon.sh`, perf smoke, reserved-seam cage (incl. S-19 anonymous_view_paths consult entry) — **AMEND v1.4 / module-lanes: T5.11 negative lane-isolation matrix (AC-40: each token type × each wrong lane) + module-lane ON/OFF equivalence (AC-41)** | Fake provider; ~75 tests; 2 shell canaries; seam cage | SC-1…SC-10 green; full suite < 5 min without Docker (NFR-10) | **L** — largest single phase by test count; must run Docker-less | P2, P3, P4 |
| 6 | Runbook + CHANGELOG + rollout | `docs/runbooks/oauth2-auth.md` (7 sections, live-views v-pattern), **AMENDED v1.1: KB-MCP loopback trust rationale section (D1); bootstrap env-var removal step + bash+DB mint-CLI trust-class disclosure (C2); dual-sweeper same-DB runbook note (resilience)**, CHANGELOG entry, default-OFF rollout verification note, CN cross-links (incl. R3 source-forgery follow-up ticket cross-link) — **AMEND v1.4 / module-lanes: §Module-lane key management section (module keys independent of OAuth2; OAuth2 does NOT govern module endpoints; current open-MCP-KB state + improvement note; webhook_secret = best practice, not auth config — supersedes the v1.2/A6 "set webhook_secret BEFORE enabling auth" Enable-step framing)** | Runbook; CHANGELOG; CN notes | AC-35 section check; AC-S1 curl walkthrough documented | **S** — documentation + final verification only | P5 |

**Phase-ordering rationale.** Config first (everything reads it).
Migrations/repository before the middleware that reads sessions (P2 → P3).
Machine lane after the middleware exists (its verification path is the
middleware dispatch). Fake OIDC before the flow tests that consume it
(P5 after P2/P3). The 4-scenario lifecycle e2e is the integration apex,
so it lands after all production code (P5). Docs/rollout last (P6).

**Parallelism note.** P4 (bearer lane) can run in parallel with P5's
fake-OIDC authoring once P3 lands, if two executors are staffed; the
dispatch should treat the default order as serial.

## 5. Coupling Map

| | P1 | P2 | P3 | P4 | P5 | P6 |
|---|---|---|---|---|---|---|
| P1 | — | tight (AuthConfig consumed by OAuthClient + sweeper TTLs) | tight (middleware construction reads `settings.auth.enabled`) | tight (CLI reads AuthConfig TTLs/bootstrap) | loose (fixtures set config) | independent |
| P2 | tight | — | tight (middleware calls `AuthService` → repo) | tight (bearer table) | tight (flow tests need store) | independent |
| P3 | tight | tight | — | tight (bearer dispatch inside middleware) | tight (dispatch tests) | loose (runbook describes it) |
| P4 | tight | tight | tight | — | tight (consumer verification) | loose |
| P5 | loose | tight | tight | tight | — | tight (canary evidence feeds runbook) |
| P6 | independent | independent | loose | loose | tight | — |

Shared-file hotspots (review the diff as a set):
- `daemon/api.py` `create_app()` — P3 wiring (conditional `add_middleware`),
  P5 fixture imports. LIFO registration block per [TA] §Registered Middleware Order.
- `daemon/config.py` — P1 AuthConfig; P2/P4 consume.
- `daemon/services/live_views.py` — **read-only reuse** of `BaseURLResolver`
  (P3); no edit to live_views (DI seam per [TA] §Technical Debt #3).
- `frontend/scripts/boot-e2e-maintenance-daemon.sh` — **no code change**
  (canary route is PUBLIC per [REQ] §7 row 11); P5 runs it as-is against an
  auth-ON daemon.

## 6. Key Risks + Mitigations (deduped from [TA] §Risks + [REQ]; highest severity first)

| # | Risk | Impact | Likelihood | Mitigation | Source |
|---|------|--------|------------|------------|--------|
| K1 | **CORS wildcard+credentials × cookie auth** — the daemon today runs `allow_credentials=True` with wildcard origins (`daemon/api.py:3015-3021`, KNOWN RISK 2026-10-10); cookie sessions compound it | High | Medium | Auth-ON + wildcard+credentials + empty `auth.allowed_origins` → refuse to boot, exit 78 (FR-27, AC-27). Broader CORS hardening stays OOS-5 (separate ticket); link both in decisions + CN | [TA] R1, OQ-10; [REQ] FR-27, OOS-5 |
| K2 | **Default-OFF regression** — the build silently changes off-state behavior | High | Medium | Conditional registration (not registered ≠ registered-no-op, [TA] §Middleware Placement rule 7); two-fixture test estate proves both halves; snapshot diff SC-1; promote-loop e2e unchanged | [TA] R11; [REQ] AC-1/2, NFR-1/2 |
| K3 | **boot-e2e canary breakage** — `/api/maintenance/checkpoint-cleanup/availability` 401s when auth ON, blocking the maintenance boot-e2e gate | High | Low | Route is PUBLIC in `public_path_set` (FR-22, [REQ] §5.4 maintenance row); canary runs unchanged in P5 as the structural proof; `frontend/scripts/boot-e2e-maintenance-daemon.sh:178-182` untouched | [TA] Debt #7; [REQ] §7 row 11 |
| K4 | **Session table = new DB dependency on the auth path** (PG outage) | Medium | Low | Public paths structurally unaffected (zero-I/O fast path); protected paths fail closed `503 AUTH_TEMPORARILY_UNAVAILABLE` + `Retry-After: 30`; `/readyz` carries `auth_dependency_status` sub-field, verdict unchanged (OQ-1 → NFR-15); sweep decoupled from validate path | [TA] R4 + §Failure Modes |
| K5 | **Host-recorder first-login cold start** — `redirect_uri` minted before any Host captured on a fresh boot → resolver returns None | Medium | Medium | `503 LOGIN_NOT_CONFIGURED` (never a relative-URL mint, RFC 6749 §3.1.2); operator override `ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE`/`ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL` documented in runbook §Enable; SPA hit on `/` pre-warms in practice | [TA] §Redirect URI Resolution; FR-28, AC-28 |
| K6 | **Secure cookie + plain-HTTP LAN** | Medium | High (LAN use) | `Secure` default ON; `allow_insecure_cookie=true` explicit operator opt-in; runbook states the trade | [TA] R2; [REQ] §6.1 |
| K7 | **SameSite × OAuth redirect-back** — `Strict` breaks the top-level callback | Medium | Low | Default `Lax`; `Strict` allowed but boot-warned ([REQ] §6.3 row 12); **AMEND v1.3 / F9 — CSRF residue superseded:** the v1.1 "double-submit CSRF as belt-and-suspenders" wording is RETRACTED; v1 uses the Origin-guard PREDICATE per `decisions.md D8 v1.2` (presence-based branching; headerless exemption). SameSite=Lax + the Origin-guard predicate covers the residual cross-site risk; token-echo validation against `ensemble_csrf` cookie is reserved as v2 defense-in-depth. | [TA] R3, OQ-7 |
| K8 | **Bearer-token leak / replay** | High | Low | Hash-only storage (`sha256`), plaintext printed once at mint; CLI `revoke`; sliding renewal OFF keeps forensics stable; revoke admin route is a reserved seam (S-7) with CLI covering v1 | [TA] R8; [REQ] FR-16 |
| K9 | **Session fixation via pre-set cookie** | Medium | Low | Server-side session store (attacker-set cookie = row miss); fresh cookie minted at every callback; middleware rotates on auth-state change | [TA] R6 |
| K10 | **SQLite deployments + auth** — blueprint keeps SQLite compat for existing tables; auth tables exist on BOTH backends | Medium | Low | **AMENDED v1.1 (D17 leader ruling) + AMEND v1.2 / A3 wording consistency:** INERT AUTH TABLES — tables exist on BOTH backends via the migration runner (`daemon/migrations/runner.py:107-113`); single code path, no `MANUAL: TRUE` markers, no `require_sqlite_rejection` env var, no boot-refusal. The `require_sqlite_rejection` row was REMOVED from [REQ] §6.1 (it contradicted the runner). SQLite-auth fixtures (`auth_enabled_app` etc.) keep working. NFR-8 wording: feature gate is the absence of `auth.enabled`, not a runtime refusal. **The K10 / D17 inconsistency flagged in v1.1 (K10 said "PG-only" while D17 said "inert on both") is resolved — K10 now mirrors D17 verbatim.** | [REQ] NFR-8 (reworded) |
| K11 | **SPA asset gating** — auth middleware eating SPA static assets breaks the SPA | High | Low | Implicit PUBLIC-ASSETS rule ([REQ] §5.7): GET `/` + non-carved `/{path:path}` are public; carve-outs mirror the SPA's own `api*|ws*|vscode*|views*` prefixes; AC-33 guards | [REQ] §5.7, OQ-REQ-9 |
| K12 | **Test-suite runtime/Docker creep** — 75 new tests + fake OIDC could slow CI or drag in Docker | Medium | Medium | In-process ASGI fake (no Docker, no port), NFR-10 < 5 min budget pinned by AC-34 | [TA] §Testability; [REQ] FR-33/34 |
| **R3** (ADDED v1.1, file-as-follow-up) | **Source-forgery lanes persist post-auth** — a stolen `jobs:write` bearer still forges `source=api` (client-supplied `source=body.source` at `daemon/routers/jobs_crud.py:595`; hard-coded `source="api"` at `daemon/routers/messages.py:690`). **Auth does NOT close the forge-source lane.** | Medium | Medium | **FILE AS SEPARATE FOLLOW-UP TICKET** "R3: derive source from `AuthContext.subject` when auth is ON (ignore/merge `body.source`)" — cross-link from the project critical note `OAuth2 planning follow-ups (R3): source-forgery lanes NOT closed by auth plan`. Wherever forge-lane risk is cited in this plan (D10, [TA] §Machine-Client Lane, [REQ] §7), add the "not closed by this plan" clarifier. The auth commission ships with the ticket filed, not closed. | D10; architect §1.3 R3; `daemon/routers/jobs_crud.py:595`; `daemon/routers/messages.py:690` |
| **C1** (AMENDED v1.1 + v1.3 / F3 pin) | **Undecorated-route default-`*` least-privilege hazard** — every future router mount without an explicit scope declaration silently ships weakest policy | Medium | Low | Regression-cage test (FR-38) that fails when a router mount lacks an explicit scope declaration; per-family manifest. **Cage match target pinned (AMEND v1.3 / F3, census verified 2026-10-10): the 28 `api_router.include_router(` mounts at `daemon/api.py:3097-3142`** — NOT the 31 raw textual `include_router(` matches (which also contain the `:3144` api_router aggregate + the `:3259` live-views router mounts + 1 prose comment; app-level mounts documented separately in phase4 D4.7). The default-`*` posture itself stays for v1 (default-deny would force decorating all 28 router families in the first commission — wrong blast radius, per architect C1). | D9; [REQ] FR-38; architect §1.3 C1 |
| **C2** (AMENDED v1.1) | **Bootstrap-token exfiltration surface** — `daemon/tools/ens_env_tools.py` `ens_env_read(keys=[...])` override path reads ANY env var verbatim; an agent could read `ENSEMBLE_AUTH_BOOTSTRAP_TOKEN` | Medium | Low | Three hardening conditions (folded into OD-2): (a) deny-list `ENSEMBLE_AUTH_*` on the override path (mirroring the POSTGRES-exclusion posture); (b) one-shot bootstrap consumption at first admin mint; (c) runbook step removes the env var after first admin mint. (d) Trust-class disclosure in runbook: bash+DB agent can still reach the mint CLI directly — same trust class as other DB-adjacent agent capabilities. | D8, D9; [REQ] FR-16a; architect §1.3 C2 |
| **C3** (AMENDED v1.1) | **Public-path-set drift vs scheduled `/drainz`** — hand-listed frozenset is a SECOND list that can drift from route registration; the reserved `draining` field means a future probe would 401 at the promote gate | Low | Medium | **Public-path set derived from route metadata at middleware construction** (D18); routes carry a `public-probe` tag/attribute; construction walks `app.routes` and LOGS the derived set at startup. Request-time behavior byte-identical. AC-22's curl canary stays as the runtime backstop. | D11, D18; [REQ] FR-22a; architect §1.4 C3 |
| **C4** (AMENDED v1.1) | **NFR-7 exit-78 unreachable as written at lifespan** — a lifespan `RuntimeError` exits via uvicorn's generic path (exit 1 + traceback, post-bind) | Low | Low | Static misconfig validation moved to **boot preflight** beside `_boot_db_preflight` (exit-75 precedent, `daemon/__main__.py:319-324`); discovery-fetch WARN stays at lifespan. Promote interaction confirmed: boot-fail → version-verify gate fail → auto-rollback is the pipeline working as designed. | D1 (T1.3); [REQ] NFR-7, FR-26; architect §1.4 C4 |
| **D1** (AMENDED v1.1; **AMEND v1.4 / module-lanes: mitigation SUPERSEDED**) | **KB-MCP Mount 401s on auth-ON** — Starlette 0.52.1 Mounts traverse parent middleware (`build_middleware_stack` wraps `self.router`); unamended, the plan would 401 every KB-MCP consumer (local agents) on day 1 | High | High | `/api/mcp/kb` and `/api/mcp/kb/sse` in `AUTH_DEFAULT_PUBLIC_PATHS` v1 (preserves today's loopback posture verbatim); loopback trust rationale documented in runbook; KB-MCP-auth seam RESERVED for a future commission. Plan pins Starlette 0.52.1 / FastAPI 0.133.1; phase 3 carries a one-line Mount-traversal regression test. **v1.4: the Mount-traversal FACT and the 401-avoidance goal stand; the mitigation is re-expressed as MODULE-LANE classification + pass-through (decisions.md D19, D4 v1.4 note) — the paths leave the public set; the traversal regression test now lives in the module-lane legs (phase3 G3.6/G3.8).** | D4, D11, D18; architect §1.1 D1; **decisions.md D19 (v1.4)** |
| **D2** (AMENDED v1.1) | **Redirect chain wiring bug** — operator sets only `ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL`; the resolver's tier-1 argument would be silently skipped, never consulting the operator override | Medium | High | One-shot lifespan copy of `config.live_views.external_base_url` into `AuthConfig.live_view_external_base_url_fallback`; `AuthService` passes that string as `BaseURLResolver`'s tier-1. DI-clean: `AuthService` never imports `live_views`. | D6; [REQ] §6.1; architect §1.1 D2 |
| **S-19** (ADDED v1.1) | **v2 share-tokens / password-gated views blocked without anonymous-public seam** — the v1 401-contract always 302s browser-Accept requests, foreclosing S-12/S-13 v2 features that need to reach a router-level handler | Medium | Low | **S-19 added to [REQ] §10** as the reserved branch point; `AuthConfig.anonymous_view_paths: tuple[str, ...]` consulted by `AuthMiddleware` BEFORE session validation (startswith on `scope["path"]`); v1 ships empty + regression-cage entry. §10.2 names S-19 as the reserved branch point. | D11; [REQ] S-19, §10.2; architect §1.5 |
| **S-20** (AMEND v1.2 / A3 — KB-MCP-auth seam; was prose-only pre-v1.2; **AMEND v1.4 / module-lanes: semantics SUPERSEDED**) | **KB-MCP-auth seam canonicalized** — the per-surface credential for KB-MCP agent callers is reserved for a future commission; in v1 the `/api/mcp/kb` and `/api/mcp/kb/sse` paths stay PUBLIC per D1, but the seam presence is now a canonical §10 row + §10.1 SEAMS dict entry, not prose-only | Medium | Low | **S-20 added to [REQ] §10** as the canonical row (was prose-only pre-v1.2); `AuthConfig.kb_mcp_auth_enabled: bool = False` placeholder flag + middleware surface branch hook in `daemon/middleware/auth.py`; v1 ships default-False + regression-cage entry. The seam-cage test asserts presence. **v1.4: the paths no longer "stay PUBLIC" — they are MODULE-LANE (beyond/separate from OAuth2); the seam's meaning is re-labeled "module-lane classification + own-key enforcement" (decisions.md D19); row count and grep assertions unchanged (NFR-11 = 20/20); see [REQ] §10 S-20 row annotation.** | D1, D11; [REQ] S-20, §10.1 SEAMS dict; architect §1.1 D1 reserved; **decisions.md D19 (v1.4)** |
| **C1 v1.2** (CRITICAL — CSRF vs NFR-18 zero-SPA-change contradiction) | **Mandatory `X-CSRF-Token` on state-changing routes would 403 every SPA mutation day 1** — the unchanged SPA has no CSRF interceptor; NFR-18 mandates zero `frontend/src/**` source changes | **High** | **High** | **C1 ADOPTED FIX (leader):** Origin-guard PREDICATE — CSRF enforcement triggers ONLY when the request carries `Origin` or `Referer` headers (presence-based branching, mirroring rule-1 precedent at `daemon/routers/maintenance_origin_guard.py:24-43`); for such browser-lane state-changing requests, validate same-origin Origin/Referer (and token-echo when a CSRF header is present). Headerless machine/bearer requests are EXEMPT. NFR-18 stays INTACT. SameSite=Lax + the Origin-guard predicate covers the residual cross-site risk. New **AC-39** asserts SPA mutation from the unchanged SPA (browser sends Origin, no explicit CSRF header) succeeds — INV-3 / SPA-lockout closed BY CONSTRUCTION. **Status:** RESOLVED in `decisions.md D8` (v1.2 supersedes the v1.1 double-submit-only text where it conflicts) + `phase3-plan.md T3.4`. | D8 (v1.2); FR-11/14; NFR-6/18; AC-11/25; AC-39 (NEW v1.2); rule-1 precedent `daemon/routers/maintenance_origin_guard.py:24-43` |
| **SSE v1.2** (AMEND v1.2 / Minor #6 — SSE mid-stream revocation posture) | **Open SSE streams survive mid-stream session/token revocation** — the middleware authenticates the initial `GET` request; open event streams then run to natural close without per-event credential checks; a session revoked mid-stream continues delivering to that client until reconnect | Medium | Low | **ACCEPTABLE for v1** — server-side cookie/bearer row is checked at connect time; revocation propagates to NEW connections (the client reconnects, the new connection 401s). In-flight connections survive until natural close (or client reconnect). The `SSE mid-stream revocation posture` Confirm-at-implementation item in `plan-overview.md §12` carries the verification step (acceptable for v1; the S-18 replay-cache + per-event re-validate are v2 seams if needed). | FR-12 (NEW v1.2 / Minor #6 paragraph); plan-overview §12 Confirm-at-implementation |
| **External webhooks v1.2** (AMEND v1.2 / A6 — leader ruling: external webhook senders = YES-WITH-CONFIG) | **Webhook sources without `webhook_secret` configured become an un-inventoried consumer lane on auth-ON day 1** — `webhooks.py:63-74` makes `X-Webhook-Secret` an OPTIONAL guard; on auth-ON, sources registered without a secret have no other credential lane (the middleware checks `X-Webhook-Secret` AND auth, but if no secret is configured, the source-side credential is empty/missing → 401 from the existing guard) | High | Medium | **A6 ADOPTED FIX (leader):** added §7 row 17 (external webhook senders = YES-WITH-CONFIG); runbook §Enable procedure in phase6 T6.1 explicitly instructs per-source `webhook_secret` setup BEFORE enabling auth; sources that cannot supply a secret get runbook-documented mitigation (loopback-only source registration via dev VPN, or source-side deny-by-source registration). The K6 runbook enables step covers this row. **AMEND v1.4 / module-lanes — the RISK is DISSOLVED by the user ruling (webhook ingest is MODULE-LANE, never OAuth2-gated, so no auth-ON day-1 lockout exists); the A6 mitigation text above is superseded — `webhook_secret` remains existing best practice for the pre-existing open-ingest posture (webhooks.py:67), not auth enablement config (see ML-4); row 17 verdict flipped YES UNCHANGED (§7.1).** | D4.6 + NFR-14; [REQ] §7 row 17; phase6 T6.1; `daemon/routers/webhooks.py:63-74` |
| **ML-1** (**AMEND v1.4 / module-lanes (NEW)**) | **Cross-lane token confusion** — a webhook secret replayed at an MCP-KB mount or general API route (or an MCP-KB key / OAuth2 bearer replayed at webhook ingest) authenticates an unintended lane | High | Low | HARD lane isolation (decisions.md D20): separate stores (webhook secret in `source_configs.config`; MCP-KB keys in their own module store; OAuth2 in `auth_*` tables), distinct credential formats/namespaces, three-class middleware routing; NEGATIVE cross-lane matrix REQUIRED (AC-40, phase5 T5.11 — every token type × every wrong lane → 401/403); `compare_digest` constant-time comparison preserved (webhooks.py:69) | [REQ] FR-40, §7.7; AC-40; decisions.md D19/D20 |
| **ML-2** (**AMEND v1.4 / module-lanes (NEW)**) | **Middleware misclassification of module paths** — the three-class derivation puts a module-lane path in the wrong set (e.g. KB mount left in `public_path_set`, or a future module route forgotten) → silently OAuth2-gated (401 lockout) or silently public | High | Low | Route-metadata derivation (D18 extended) — classification is derived from route tags at construction and LOGGED at startup (reviewable in promote transcripts), not hand-listed; Mounts are EXPLICITLY tagged module-lane (Mounts DO traverse middleware — the classifier must tag them, per D4 v1.4 note); Mount-traversal regression test retained (phase3 G3.6); derivation test asserts class disjointness + module-lane membership for both mount families (AC-22a v1.4 leg); phase3 risk-table row mirrors this | [REQ] FR-22a v1.4, AC-22a; decisions.md D4 note, D19; phase3 T3.10 |
| **ML-3** (**AMEND v1.4 / module-lanes (NEW) — research finding, pre-existing condition**) | **`webhook_secret` returned UNMASKED in plaintext via GET /api/sources** — the generic source CRUD returns `config` verbatim (sources.py:81 `config=source.config`; storage repositories/source/models.py:62-65), so every GET list/detail leaks the webhook secret in plaintext; under v1.4 this surface IS the OAuth2-governed webhook token-management lane | Medium | Medium (post-auth: any logged-in human/bearer sees every source's secret) | Mask on read under the OAuth2-governed management surface (e.g. `has_webhook_secret: true` + redacted field, write-through preserved on PUT — config is replaced wholesale at sources.py:341, the clobber hazard) — **confirm-at-implementation** (AC-42); PUT/write paths unchanged; module-lane ingest never echoes the secret; PRE-EXISTING condition (not introduced by this plan) — surfaced as plan risk per the v1.4 dispatch | [REQ] §5.0 row 2, FR-41, AC-42; decisions.md D19; `daemon/routers/sources.py:81` |
| **ML-4** (**AMEND v1.4 / module-lanes (NEW) — pre-existing posture**) | **MCP-KB mounts are fully open today** — `/api/mcp/kb` + `/api/mcp/kb/sse` carry ZERO auth (kb_server.py:214-223; 6 tools incl. WRITE `ensemble_kb_experience` + context reads; no key surface exists — §5.0 NF-1); v1.4 keeps them reachable (module-lane) so this posture persists until the module key mechanism lands | Medium | High (today) | Module-lane key-check mechanism at/around the mount (validation of own keys) scoped confirm-at-implementation (FR-41) — a security improvement INDEPENDENT of OAuth2 (works with auth OFF too, per FR-42); until then the posture is unchanged-by-design per the ruling; runbook discloses the current state + improvement note (phase6 T6.1) | [REQ] §5.0 rows 3-4, FR-41/42; decisions.md D19; phase3 T3.11; phase6 T6.1 |

## 7. Open Decisions Ledger (OD-1…OD-15) — CLOSED v1.1

> **AMENDED v1.1:** all 15 OD rulings from `architecture-recommendation.md §2`
> have been folded into DECIDED status in `decisions.md`. The
> implementation commission may proceed without user sign-off on any of
> these items; the user may override any of them at the implementation
> stage. **Nothing remains OPEN in this ledger.**

The original ledger is preserved below for traceability; every row's
status is now DECIDED. See `decisions.md` for the full rationale and
folded conditions.

| ID | Decision | Options considered | Status | Folded into |
|----|----------|--------------------|--------|------------|
| OD-1 | FastAPI docs trio (`/docs`, `/redoc`, `/openapi.json`) stance | (a) public unconditionally (b) public + `ENSEMBLE_AUTH_DOCS_ENABLED` kill switch (c) PROTECTED | **DECIDED (ENDORSE)** — (b) flag default `true` preserves pre-build behavior; operators can 404 the trio | D15 cross-ref |
| OD-2 | Bootstrap-token mint surface (first admin bearer) | (a) CLI + `ENSEMBLE_AUTH_BOOTSTRAP_TOKEN` (b) loopback HTTP endpoint (c) one-time URL in logs | **DECIDED (ENDORSE-WITH-CONDITION)** — (a) with C2 conditions folded: deny-list on `ens_env_read` override + one-shot consumption + runbook removal step | D8, D9 |
| OD-3 | Scope granularity | (a) 2 broad scopes (b) 7 fine-grained only (c) 7 fine-grained, conservative token defaults | **DECIDED (ENDORSE-WITH-CONDITION)** — (c) hybrid; with C1 router-mount scope-declaration cage test folded | D9 |
| OD-4 | CORS tightening policy when auth ON | (a) full hardening now (b) boot-refusal guard only + separate ticket | **DECIDED (ENDORSE)** — (b) FR-27 boot-refusal + `auth.allowed_origins`; hardening OOS-5 ticket | D15 |
| OD-5 | Cookie `Secure` under plain-HTTP LAN | (a) always Secure (b) `allow_insecure_cookie` opt-in (c) auto-detect | **DECIDED (ENDORSE)** — (b) default Secure ON, explicit LAN opt-in, runbook warning | D8 |
| OD-6 | Auth base-URL override vs reuse of `ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL` | (a) auth-specific override only (b) live-view var only (c) auth override > live-view var > Host-capture > bind | **DECIDED (ENDORSE-WITH-AMENDMENT)** — (c) with D2: `live_view_external_base_url_fallback` lifespan copy; D6 field name ratified | D6 |
| OD-7 | `/vscode` WebSocket stance | (a) protect in v1 (b) keep loopback posture, seam reserved | **DECIDED (ENDORSE)** — (b) WebSocket pass-through (FR-21); `ENSEMBLE_AUTH_VSCODE_PROTECTED=false` ships the seam pre-wired | D4 rule 4 |
| OD-8 | Multi-IdP | (a) v1 (b) single issuer + `idp_alias` seam | **DECIDED (ENDORSE)** — (b) single issuer in v1; S-9 seam | D14, S-9 |
| OD-9 | `/readyz` composite when auth degraded | (a) flip verdict to 503 (b) body sub-field only | **DECIDED (ENDORSE)** — (b) `auth_dependency_status` sub-field, verdict stays 200; verdict-freeze test + auth sub-field added to /readyz no-composite fail-closed branch | D11 |
| OD-10 | Logout endpoint shape | (a) POST only (b) DELETE only (c) both | **DECIDED (ENDORSE)** — (c) POST for browsers, DELETE alias for API clients | D8, phase 3 T3.6 |
| OD-11 | Cookie name | `ensemble_sid` / `ENS_SID` / `auth_session_id` | **DECIDED (ENDORSE)** — `ensemble_sid` | D8 |
| OD-12 | SPA first-load UX on session expiry | (a) middleware 302 immediately (b) SPA-orchestrated login UI | **DECIDED (ENDORSE-WITH-CONDITION)** — (a) with `return_to` normalizes `\` → `/` BEFORE same-origin prefix check; AC-25 keeps backslash cases | D12 |
| OD-13 | SPA asset publicness rule | (a) implicit PUBLIC-ASSETS (b) operator path list (c) public proxy route | **DECIDED (ENDORSE)** — (a) mirrors SPA carve-out prefixes | D4, D11 |
| OD-14 | First-admin-token default scopes | (a) `auth:admin` only (b) admin + all data scopes (c) CLI `--scopes` per mint, default `auth:admin` | **DECIDED (ENDORSE)** — (c) | D9 |
| OD-15 | CSRF double-submit timing | (a) now (~10 LoC) (b) defer to v2 | **DECIDED (ENDORSE)** — (a) now — protects against future CORS-whitelist regressions | D8 |

## 8. Default-OFF Zero-Disruption Verification Strategy

**Mechanism (structural, not behavioral).** When
`settings.auth.enabled is false`, `create_app()` **skips the
`app.add_middleware(AuthMiddleware, ...)` call entirely** — the
middleware is not registered, not "registered as a pass-through"
([TA] §Middleware Placement rule 7; [REQ] FR-2). Consequences that are
true by construction:

- The request path is byte-for-byte the pre-build ASGI stack (CORS →
  access-log → host-capture → routes). Zero added per-request branching.
  **AMEND v1.2 / Minor #8 (registration-vs-execution order clarity):**
  this is the **execution order** (outermost-first), which equals the
  reverse of the **registration order** because Starlette `add_middleware`
  uses `insert(0)` LIFO. Registration list in `create_app()`:
  CORS → SelectiveAccessLog → HostCapture → Auth (Auth last =
  outermost); execution on the wire: Auth → HostCapture → SelectiveAccessLog
  → CORS. **The registration list (in source order) is NOT the execution
  order (on the wire)** — that asymmetry is the load-bearing detail for
  FR-19/AC-19 (the four-middleware sequence) and for [TA] §Registered
  Middleware Order binding rule 1 ("last registered is outermost").
- The auth tables (`auth_sessions` / `auth_states` / `auth_bearer_tokens`) exist as inert schema on both backends per D17 (the migration runner auto-applies the 3 `.sql` files at boot: `daemon/migrations/runner.py:107-113`). When `auth.enabled = false`, the auth tables are NEVER read or written by any v1 code path; the migration runner has applied them (they exist as inert schema on both backends), but the OFF-state code paths do not touch them — and boot-refusal logic is `enabled`-gated so OFF-state boot ignores auth entirely. **AMEND v1.3 / B2 — single coherent statement:** the default-OFF on-disk state is "auth tables exist as inert schema on both backends per D17; zero behavioral surface (no middleware, no routes, no service objects, no cookie/bearer validation, no read or write of auth tables)."
- The only OFF-state artifacts are inert code + config defaults.

**Proof obligations (phases 3 + 5):**

1. `tests/test_auth_config.py` (P1): default `enabled=False`; absent
   `auth:` block parses clean; [REQ] §6.3 row "ENABLED=false + everything
   else set → boot normally, config silently ignored".
2. Introspection test (P3, AC-2): with auth disabled,
   `AuthMiddleware` is absent from `app.user_middleware`; no auth cookie
   in any recorded response.
3. Snapshot diff (P5, AC-1/AC-S1, NFR-1): full existing suite + the
   boot-e2e canaries run against the OFF-state app; response bodies for
   `/api/*`, `/views/*`, `/livez`, `/readyz`, `/api/info` are captured and
   diffed against the pre-build baseline → 0 diff.
4. Consumer walk (P5/P6, AC-32, AC-S1): the 8 YES-UNCHANGED consumers
   ([REQ] §7 rows 1-6, 10, 11 — AMEND v1.3 / F4 count reconciled) run
   unmodified against an OFF-state daemon, then against an
   ON-state daemon with tokens — the promote/rollback/restart/status
   scripts exercise the health gates end-to-end (SC-4). **AMEND v1.4 /
   module-lanes: the walk extends to the two module-lane consumer rows
   ([REQ] §7 rows 17-18 — external webhook senders, MCP-KB clients):
   they run with NO OAuth2 credential in BOTH states and must behave
   identically (AC-41); the YES-UNCHANGED census is 10 rows post-v1.4.**

## 9. Health-Probe Public Invariant (structural summary)

**Mechanism.** `AuthMiddleware.__call__` evaluates
`scope["path"] in self._public_path_set` FIRST — a construction-time
frozenset lookup, O(1), pure CPU, executed BEFORE any I/O: no DB, no IdP,
no cookie parse, no clock check ([TA] §Failure Modes "Structural
guarantee"). `/livez` + `/readyz` therefore answer within the same
event-loop tick as today (`daemon/api.py:3172-3186`, `:3188-3238`).
The set is baked at middleware construction from the route inventory —
never a per-request exemption computation — and contains at minimum
([REQ] FR-22): `/livez`, `/readyz`, `/api/health`, `/api/info`,
`/api/maintenance/checkpoint-cleanup/availability`, `/api/auth/*`
(login/callback/logout/me), SPA shell + assets (PUBLIC-ASSETS rule),
`/views/livez`.

**Every-scenario guarantee.** Per the [TA] §Failure Modes table:
auth OFF → unchanged; ON+healthy → unchanged; ON+IdP down → probes GREEN
(sessions validate PG-only; login → `503 IDP_UNREACHABLE`);
ON+session-table down → `/livez` GREEN, `/readyz` 200 with degraded
sub-field (verdict never flipped per OD-9); restart mid-session → GREEN;
replay/misconfig → GREEN. The middleware cannot make a public path fail
because publicness is decided before any dependency is consulted —
verified end-to-end by AC-22 / AC-S3 (`tests/e2e/auth_health_probes.sh`).

## 10. Research Provenance + Artifact Index

```
Wave 1  3 × explorer          → raw repo research (middleware inventory, URL
                                resolver chain, config patterns, consumer census)
Wave 2  2 × analysis workers  → technical-analysis.md  [TA]  (architecture)
                              → requirements.md        [REQ]  (FR/NFR/AC/matrix)
Wave 3  plan-creation worker  → THIS PLAN (plan-overview.md + phase1..6-plan.md
                                + decisions.md) — synthesis only, no new research
```

| Artifact | Role |
|----------|------|
| `technical-analysis.md` | Architecture input: flow choice, module boundaries, LIFO placement, redirect_uri strategy, session model, failure modes, machine lane, 401 contract, seams, testability, risks, OQ-1…10 |
| `requirements.md` | Requirements input: **42 FRs** (37 base + FR-16a + FR-22a + FR-38, ADDED v1.1; AMEND v1.3 tally fix; **+ FR-39…FR-42 module-lane — AMEND v1.4 / module-lanes**), 18 NFRs, **AC register gapless AC-1…AC-42 + AC-16a + AC-22a + 9 NFR-specific + AC-S1…S4** (AC-16a added v1.2; AC-22a added v1.3; AC-38 ADDED v1.3 for FR-38 — fills the skipped number, every FR maps to ≥1 AC; **AC-40…AC-42 module-lane added v1.4**), protection matrix (**AMEND v1.4 / module-lanes: three classes — 11 PUBLIC / MODULE-LANE 2 families / 30 api_router-mounted PROTECTED families + 1 new `/api/mcp/kb/keys` family + sub-families**; was "12 PUBLIC / 28 PROTECTED" census v1.3 / F3, "12 PUBLIC / 31 PROTECTED" in v1.2, 11/27 pre-v1.1), 23+1 env-var config spec (EXTERNAL_REDIRECT_BASE added v1.2 / A2; **ZERO auth-config delta from v1.4 — §6.6**), **18-consumer** compatibility table (rows 1–18; row 17 external webhook senders added v1.2 / A6, **verdict flipped auth-INDEPENDENT in v1.4**; row 18 MCP-KB clients added v1.4; AMEND v1.3 / F4 counts reconciled: 8 YES-UNCHANGED + 7 YES-WITH-CONFIG + 2 N/A → **v1.4: 10 YES-UNCHANGED + 6 YES-WITH-CONFIG + 2 N/A**), 4 scenarios, OQ-REQ-1…9, **20 seams in v1.2 / A3 (was 19 in v1.1, 18 in v1.0; count UNCHANGED in v1.4 — S-20 semantics superseded to module-lane, decisions.md D19)** |
| `plan-overview.md` (this file) | Synthesis: phases, sizes, risks, ledger, verification strategy |
| `phase1-plan.md` … `phase6-plan.md` | Per-phase execution contracts (tasks with files/FR-anchors, tests, gated exits) |
| `decisions.md` | Decision register: DECIDED-by-artifacts vs OPEN-for-user (OD cross-refs), with citations |

**CN cross-links to add when this plan lands** (owner: P6): AUTH DESIGN
v1 decision note; CORS KNOWN-RISK ↔ `auth.allowed_origins` link; bearer
tokens operator-mint-only risk note ([TA] §Citations section).

## 11. Assumptions (inherited — [TA] §Assumptions A1-A10; [REQ] Appendix A)

A1 IdP supports OIDC discovery · A2 operator can register
client_id/secret/redirect_uri · A3 HTTPS available in prod (A3-LAN:
`allow_insecure_cookie` documented) · A4 PostgreSQL primary · A5 the 12
consumers are operator-controlled · A6 SPA needs no source change · A7
IdP preserves callback query params · A8 operator restarts on config
change (no live toggle) · A9 httpx available · A10 per-table migration
convention · A11 OQ approvals consolidated into this ledger (§7) · A12
incremental-write resilience (satisfied — this plan was written
skeleton-first per the dispatch).

## 12. Confirm at Implementation (NEW v1.2)

Three items the implementation commission MUST verify during the first
vertical slice and either confirm or surface as a follow-up. None are
self-evident from the plan; each depends on a runtime detail the plan does
not author.

1. **Checksummed-empty reserved `.sql` stubs behavior on SQLite (D17 inert tables).** The 3 reserved-seam stubs (`<ts>_create_grants.sql`, `<ts>_create_subject_alias.sql`, `<ts>_create_view_shares.sql`) are EMPTY `-- UP`/`-- DOWN` placeholders per D5.5; verify that the migration runner accepts placeholder stubs on BOTH backends, or that the `tests/test_reserved_seams.py` cage test accommodates (e.g., the stub-file check uses a SHA-256 of the empty body or a different sentinel). If the runner requires a non-empty body for checksum registration, the cage test must adapt to "file exists + checksum registered" rather than "file contains CREATE TABLE". This is the D17 inert-tables posture's edge case at the migration-runner seam.
   **✅ RESOLVED 2026-10-11 (P2 implementation, option (a) — the runner accepts labeled placeholder stubs).** Empirically verified on scratch SQLite (and pinned by `tests/migration/test_auth_tables_g2_2.py::TestEmptyStubBehaviorPlanOverview12Item1`): a D5.5-form comment-labeled stub (`-- UP` / `-- DOWN` sections carrying ONLY label comments) parses cleanly, applies as a NO-OP that still records its ledger row with the file's TRUE whole-file SHA-256 checksum in `schema_migrations`, rolls back cleanly (ledger row deleted, zero SQL executed — a comment-only DOWN strips to zero statements), and re-applies cleanly. On PG the question is vacuous by design: `run_pending_migrations` is a NO-OP on non-SQLite engines (`daemon/migrations/runner.py:721-726`, blueprint invariant), so nothing needs to be verified there — the D5.5 stubs are a SQLite-lane artifact only. ONE caveat for the P5/P6 stub authors: the `-- DOWN` section MUST carry its label comment — a ZERO-BYTE DOWN section parses `down_sql == ""` and `rollback_migration` refuses with `MigrationError: … has no DOWN section`. The runner-parser also forbids `;` inside INLINE (trailing) comments on DDL lines (statement splitting is semicolon-based after full-line-comment stripping) — noted in the auth migration headers. No cage-test adaptation needed: "file exists + checksum registered" holds as-is.

2. **Actual SPA Accept-header behavior on session/token expiry.** The plan's FR-24/Minor #5 caveat states that the SPA's `HttpClient` sends `Accept: application/json` on XHRs (XHR lane → 401) while browser top-level navigations send `Accept: text/html` (text/html lane → 302). Verify in implementation: does the production `frontend/src` Angular `HttpClient` (and any interceptor) actually send `Accept: application/json` for protected API calls? If the SPA sends `Accept: */*` (the path-heuristic fallback), the discrimination falls to the path-family heuristic — confirm this is acceptable and that the path-family heuristic correctly identifies the protected routes the SPA calls. If the SPA sends neither, both lanes yield 401 (machine-style), and the SPA needs an explicit redirect-to-login handler — contradicting NFR-18 unless the SPA already has one.

3. **SSE mid-stream revocation posture.** Open SSE streams (5 families: `/api/instances/{id}/events`, `/api/jobs/{work_id}/events`, `/api/notifications/stream`, `/api/migration/events`, `/api/workspace/events`) survive mid-stream session/token revocation per FR-12 (NEW v1.2 / Minor #6). Confirm acceptable for v1 by either: (a) explicitly documenting the posture in the runbook + CN with the "v2 seam = S-18 replay-cache + per-event re-validate" pointer, or (b) implementing a per-event re-validate at the route handler level if v1 cannot tolerate the window. The default is (a) — the open stream is a long-lived HTTP response, the credential is checked at connect time, the next reconnect re-validates.

4. **Module-lane mechanism confirmations (**AMEND v1.4 / module-lanes (NEW)**).** Three confirm-at-implementation decisions belong to the module lanes (all independent of `auth.enabled`, per FR-42): (a) **MCP-KB mount key-check mechanism** — where the own-key validation lives (at the FastMCP layer vs a wrapper around the mounts), the key storage location (module-owned table/config/env — NOT `AuthConfig`, per [REQ] §6.6), and the key format; until confirmed, the mounts stay exactly as today (zero auth — pre-existing posture, risk ML-4). (b) **`/api/mcp/kb/keys` management family shape** — route set (list/get/create/update/delete/revoke), authz one-liner (which scope guards it), and whether mint/rotate is CLI-adjacent like the bearer lane; plan level deliberately invents NO UX (FR-41). (c) **`webhook_secret` masking decision on GET /api/sources** — mask-on-read with write-through (sources.py:341 wholesale-config-replace is the clobber hazard) vs explicitly retained plaintext read-back; either way the decision + rationale land in the runbook (AC-42, risk ML-3).

Items resolved during implementation SHOULD be moved to a §11 amendment log entry with date + worker. Items unresolved MUST surface as a follow-up ticket before the implementation commission declares done.

 implementation commission declares done.

