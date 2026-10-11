# Phase 5 — Test Estate: Fake OIDC + Four Scenarios + Canaries + Perf + Seams

**AMEND v1.4 — 2026-10-10 (user ruling: module auth lanes)**

**AMEND v1.3 — 2026-10-10 (approver iteration-001 fixes)**

> **AMEND v1.4 / module-lanes — T5.11 negative lane-isolation + module ON/OFF equivalence.** New task T5.11 delivers (a) the NEGATIVE cross-lane token matrix (AC-40, FR-40 — each token type × each wrong lane → 401/403: webhook secret × MCP-KB mount/general API; MCP-KB key × webhook ingest/general API; OAuth2 cookie/bearer × webhook ingest = never a webhook credential) and (b) module-lane ON/OFF byte-equivalence (AC-41, FR-42 — `/api/webhooks/*` + `/api/mcp/kb*` behave identically with auth ON and OFF). New suites `tests/integration/test_module_lane_isolation.py` + `test_module_lanes.py` legs (P3 T3.10 groundwork); exit gate **G5.6**; SC-12 in plan-overview §3. Suite count +~10 cases.

> **AMEND v1.3 / F2 — SKIP LOCKED dialect guard note at T5.10.** The dialect-agnostic `with_for_update(skip_locked=True)` shape (SQLAlchemy's silent no-op on SQLite) is the v1 implementation per phase2 D2.4 / T2.3; the T5.10 sweeper-test leg asserts BOTH shapes: PG leg verifies the `FOR UPDATE SKIP LOCKED` SQL is issued; SQLite leg verifies the plain batched `DELETE` shape (test target is definite; no runtime dialect branch in production code).

> **AMEND v1.2 — 2026-10-10 (reviewer APPROVED-WITH-NOTES sweep)**

> **AMEND v1.2 — applied amendments in this file:**
> - **A1 (D17 propagation):** T5.10's "exit-78 boots for missing-field, CORS-wildcard, **SQLite combos**" wording — the SQLite leg is now the POSITIVE test ("auth-ON boots on SQLite") per the inert-tables posture (no boot-refusal exists; the test inverts from "refuses" to "boots + serves the flow"); the row in [REQ] §6.3 that asserted SQLite-refusal is retracted.
> - **A7 (restart-scenario precision):** T5.5 carries the public-probe-canary-against-post-restart-daemon step.

## 1. Objective

Deliver the full verification estate: the in-process fake OIDC provider,
the two-fixture app pattern, the consolidated unit/integration packs, the
4-scenario lifecycle e2e (`tests/e2e/test_auth_lifecycle.py` = AC-S1…S4),
the health-probe shell canary, the boot-e2e auth script, the perf smoke,
and the reserved-seam regression cage.

Entry prerequisites: P2 (store + OAuthClient), P3 (middleware), P4
(bearer lane — consumer verification depends on it).

## 2. Decisions

| ID | Decision | Rationale / source | Rejected alternatives |
|----|----------|--------------------|------------------------|
| D5.1 | Fake OIDC = pure ASGI sub-app at `tests/fakes/fake_oidc_provider.py`, five routes (discovery / authorize / token / jwks / logout), RSA test keypair at fixture setup, mounted at `/_test_oidc/` alongside the app under one `ASGITransport` composition | [TA] §Testability Architecture; [REQ] FR-33; pattern: `tests/e2e/mock_source_server.py`; pytest precedent `tests/test_api.py:196-227`, `tests/test_health_probes.py:41-89` | Dockerized Keycloak (violates NFR-10; sandbox boundary); mock-patch at the HTTP client layer (doesn't exercise real redirect/302 semantics) |
| D5.2 | Two app fixtures in `tests/conftest_auth.py`: `auth_disabled_app` (existing behavior, zero auth objects) + `auth_enabled_app` (fake mounted, JWKS override → fake) + `mint_test_token(label, scopes)` helper | [REQ] §7.5; FR-34; AC-34 | one fixture with runtime flag flip (mixes concerns; weakens the OFF-state proof) |
| D5.3 | AC-S4 restart test uses real process death semantics: spawn daemon (or app+uvicorn in subprocess) → login → SIGKILL → restart → revalidate cookie; NFR-4's 1000-session stress is a separate opt-in stress marker, not CI-blocking | [REQ] AC-S4 + NFR-4 measurement; keep CI < 5 min (AC-34) | killing the pytest process itself (contaminates the run) |
| D5.4 | Perf smoke (NFR-9/16) runs in CI as a bounded ASGITransport benchmark with generous thresholds; the 1000-RPS/60 s sustained numbers are recorded but only enforced in the opt-in perf lane | [REQ] NFR-9/16; CI budget discipline | full locust run per commit |
| D5.5 | Reserved-seam cage asserts ONLY the seam presence list from [REQ] §10 (S-1, S-2, S-5, S-6, S-7/S-11 route-shape, S-8, S-9 config fields + docstring-level stubs); `.sql` stubs for S-4/S-10/S-12/S-13 are EMPTY `-- UP`/`-- DOWN` placeholder files, checksummed like real migrations, explicitly labeled "reserved — no schema" | [REQ] FR-36/§10.1; keeps the migration audit trail honest (a stub is NOT a table) | creating real empty tables (schema lies); skipping the cage |

## 3. Tasks

| ID | Task | Files touched | What changes (plan level) | Satisfies |
|----|------|---------------|---------------------------|-----------|
| T5.1 | Fake OIDC provider module (D5.1): discovery doc, `/authorize` 302 with code+state (+PKCE enforcement on `/token`), signed test JWTs, `/jwks`, configurable failure modes (503-all / wrong-issuer / wrong-aud / bad-signature / wrong-kid toggles) | `tests/fakes/fake_oidc_provider.py` (new) | In-process IdP double | FR-33 |
| T5.2 | `tests/conftest_auth.py` fixtures + `mint_test_token` (D5.2); host-recorder pre-warm fixture helper ([TA] §Testability seam) | `tests/conftest_auth.py` (new) | Fixture estate | FR-34 |
| T5.3 | Unit packs finalized: `test_auth_service_flow.py`, `test_auth_service_session.py`, `test_oauth_client_*.py`, `test_auth_cli.py`, `test_reserved_seams.py` (cage, D5.5) targeting ~30 (AuthService) + ~15 (OAuthClient) + ~10 (Bearer/CLI) cases | `tests/unit/…` (extend P2-P4 files) | Unit depth | FR-34; AC-36/37 |
| T5.4 | Integration packs finalized: dispatch/UX/routes/IDP-error/off-state/gates-parity/bearer/scope-matrix/webhook/consumer-enablement (P3+P4 files completed against the real fake) | `tests/integration/…` | ~20 integration cases | FR-34 |
| T5.5 | `tests/e2e/test_auth_lifecycle.py` — the four canonical scenarios: `test_auth_off_identical_to_pre_build` (AC-S1), `test_auth_on_provider_healthy_full_workflow` (AC-S2), `test_auth_on_provider_down_degraded_state` (AC-S3), `test_auth_session_survives_restart` (AC-S4, D5.3) — **AMEND v1.2 / A7 (restart-scenario precision):** the `test_auth_session_survives_restart` leg additionally asserts the 5 public probes (`/livez`, `/readyz`, `/api/health`, `/api/info`, `/api/maintenance/checkpoint-cleanup/availability`) are reachable unauthenticated POST-restart, BEFORE any session revalidation — the pre-I/O fast path holds across the SIGKILL cycle; never-401 verified against the post-restart daemon. | `tests/e2e/test_auth_lifecycle.py` (new) | Scenario apex | AC-S1…S4; **AC-S4 / A7 public-probe-canary POST-restart precision** |
| T5.6 | `tests/e2e/auth_health_probes.sh` — 5 probes unauthenticated with auth ON (AC-22/NFR-3), assert NOT-401 semantics (`/readyz` may be 200-degraded) | `tests/e2e/auth_health_probes.sh` (new) | Probe canary | AC-22; NFR-3 |
| T5.7 | `frontend/scripts/boot-e2e-auth-daemon.sh` (full body): boot real daemon auth-ON against fake-or-real IdP config, run T5.6 probe set + one bearer call + one 302 browser-shaped call; mirrors `boot-e2e-snapshots-daemon.sh` structure | `frontend/scripts/boot-e2e-auth-daemon.sh` | Boot-e2e canary | FR-23/34; NFR-5 groundwork |
| T5.8 | Perf smoke: `tests/perf/test_auth_middleware_latency.py` (p50/p95/p99 delta) + `tests/perf/test_auth_sustained_rps.py` (sustained-throughput record) | `tests/perf/…` (new) | Perf budget | NFR-9/16 |
| T5.9 | Seam stubs: `daemon/migrations/versions/<ts>_create_grants.sql` + `…_create_subject_alias.sql` + `…_create_view_shares.sql` as EMPTY reserved placeholders (D5.5); docstring stubs where the cage expects code-shape presence (S-7/S-11 route group + scope gate present in `daemon/routers/auth.py`) | `daemon/migrations/versions/…` (3 stubs), `daemon/routers/auth.py` (admin route group shape) | Non-preclusion contract | FR-36; NFR-11 |
| T5.10 | Boot fail-fast canary legs: exit-78 boots for missing-field + CORS-wildcard combos (AC-26/27; [REQ] §6.3) exercised via the boot-e2e harness. **AMEND v1.2 / A1:** the original v1.1 "SQLite combos" leg is RETRACTED — D17 inert tables means no SQLite boot-refusal exists; the SQLite leg inverts to the POSITIVE test ("auth-ON boots on SQLite") — the auth-enabled SQLite fixtures (`auth_enabled_app` etc.) prove the inert-tables posture end-to-end; a separate SQLite boot-positive canary leg is added. The SQLite-refusal row in [REQ] §6.3 is retracted. **AMEND v1.3 / F2 — SKIP LOCKED dialect guard:** the sweeper-test leg (within `tests/unit/test_auth_sweeper.py` per §4) asserts BOTH shapes: PG leg verifies `FOR UPDATE SKIP LOCKED` is issued; SQLite leg verifies plain batched `DELETE` (test target is definite; no runtime dialect branch). | `frontend/scripts/boot-e2e-auth-daemon.sh` (negative legs + SQLite-positive leg) | Misconfig proof + SQLite-positive proof | FR-26/27; NFR-7/8 |
| **T5.11** (**AMEND v1.4 / module-lanes (NEW)** — negative lane-isolation matrix + module ON/OFF equivalence) | **(a) Negative cross-lane matrix (AC-40, FR-40):** table-driven — for each token type (webhook `X-Webhook-Secret` value, MCP-KB key (when the module-key mechanism exists; fixture-stubbed until T3.11 confirmation), OAuth2 session cookie, OAuth2 bearer) × each WRONG lane (MCP-KB mount, webhook ingest with configured secret, general PROTECTED `/api/*` route): assert 401/403 and NO partial honor; positive controls: correct credential per lane → 200. The OAuth2-cookie-at-webhook leg asserts the module guard's own 401 (cookie is NOT a webhook credential). **(b) Module ON/OFF equivalence (AC-41, FR-42):** same request set against `auth_disabled_app` and `auth_enabled_app` — byte-identical status+body for every module-lane request; `module_lane_path_set` logged identical in both fixtures. **(c) Masking-decision round-trip (AC-42):** GET /api/sources secret handling per the confirmed §12 item 4(c) decision; if mask-on-read: PUT-with-masked-read does not clobber the stored secret (sources.py:341 wholesale-replace hazard). | `tests/integration/test_module_lane_isolation.py` (new), `tests/integration/test_module_lanes.py` (P3 T3.10 legs, completed here), `tests/integration/test_sources_webhook_secret_masking.py` (new, conditional on §12 4(c) decision) | Lane isolation is PROVEN, not asserted (decisions.md D20) | FR-39/40/41/42; AC-40/41/42; SC-12; [REQ] §7.7 |

## 4. Tests to Write

(Coverage claims live in the tasks above; this section records the
acceptance-level suite map.)

| Suite | Maps to |
|-------|---------|
| `tests/e2e/test_auth_lifecycle.py::test_auth_off_identical_to_pre_build` | AC-S1, AC-1, NFR-1/2 — snapshot diff + `auth_*` tables empty + `git diff` config empty |
| `tests/e2e/test_auth_lifecycle.py::test_auth_on_provider_healthy_full_workflow` | AC-S2 — 302→login→cookie→200 + bearer 200 + 18 consumers (10 YES-UNCHANGED + 6 YES-WITH-CONFIG — **AMEND v1.4 / module-lanes: was 17 = 8+7; row 17 flipped module-lane-independent, row 18 added**) + 5 probes |
| `tests/e2e/test_auth_lifecycle.py::test_auth_on_provider_down_degraded_state` | AC-S3 — probes GREEN, old cookie validates PG-only, login 503, `/readyz` degraded sub-field |
| `tests/e2e/test_auth_lifecycle.py::test_auth_session_survives_restart` | AC-S4 — SIGKILL + restart + session row survives + validates |
| `tests/e2e/auth_health_probes.sh` | AC-22, NFR-3 — 5/5 reachable |
| `tests/test_reserved_seams.py` | AC-36/37, NFR-11 — seam presence |
| `tests/integration/test_module_lane_isolation.py` (**AMEND v1.4 / module-lanes** — T5.11) | AC-40 — negative cross-lane matrix (each token type × each wrong lane → 401/403; positive controls hold) |
| `tests/integration/test_module_lanes.py` + boot legs (**AMEND v1.4 / module-lanes** — T5.11) | AC-41 — module-lane ON/OFF byte-equivalence (`/api/webhooks/*`, `/api/mcp/kb*`); derivation-set identity across fixtures |
| `tests/integration/test_sources_webhook_secret_masking.py` (**AMEND v1.4 / module-lanes** — T5.11, conditional) | AC-42 — masking-decision round-trip on the OAuth2-governed source-CRUD surface |
| `tests/perf/*` | NFR-9/16 — latency + throughput budgets |

## 5. Exit Criteria (gated)

- G5.1: Full suite (`pytest tests/ -q`) green on a Docker-less host, total
  auth-related runtime < 5 min (AC-34, NFR-10).
- G5.2: AC-S1…S4 all pass; SC-1…SC-10 from plan-overview §3 demonstrable
  from suite output + canary logs.
- G5.3: The existing boot-e2e canaries (`boot-e2e-maintenance-daemon.sh`,
  `boot-e2e-snapshots-daemon.sh`) pass unmodified against an auth-ON
  daemon ([REQ] §7 rows 10-11; K3 closure).
- G5.4: Cage green: every [REQ] §10 seam asserted present.
- G5.5: Promote-loop rehearsal (sandbox, auth OFF) unchanged — the
  default-OFF promote story has zero new steps (K2 closure evidence).
- **G5.6** (**AMEND v1.4 / module-lanes** — T5.11): `test_module_lane_isolation.py` green (AC-40 — zero cross-lane acceptances) + module ON/OFF equivalence green (AC-41 — zero module-lane behavior diffs); SC-12 demonstrable from suite output.

**Size: L** — largest phase by count (~75 tests + fake provider + 2 shell
canaries + perf + cage); pure test/doc surface, zero production features.

## 6. Phase-Specific Risks

| Risk | Mitigation |
|------|------------|
| Fake-OIDC JWT/JWKS subtleties (alg confusion, kid mismatch) eat time | Fake ships with explicit failure-mode toggles (T5.1) so each validation branch is a table case, not an ad-hoc hack |
| Restart test (SIGKILL) flaky in CI | Subprocess harness with readiness polling + generous timeouts; D5.3 keeps the 1000-session stress out of CI |
| Perf numbers noisy on shared CI runners | CI thresholds generous (SC-10 upper bounds); strict budgets enforced only in the opt-in perf lane |
| Cage test brittleness (grep patterns break on refactor) | Cage asserts semantic shapes (symbol names, file existence), not line numbers; failures name the seam + its [REQ] §10 row |
| Canary scripts need a real IdP | `boot-e2e-auth-daemon.sh` supports a `--fake-idp` mode reusing the P5 fake served by a tiny uvicorn side process (still Docker-less) |
