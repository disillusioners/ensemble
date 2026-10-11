# Phase 2 — Session Store + Migrations + OAuth Client Service

**AMEND v1.3 — 2026-10-10 (approver iteration-001 fixes)**

> **AMEND v1.3 / B5 — G2.2 reworded (infeasible gate closure).** Prior to
> v1.3, G2.2 required migration runner UP/DOWN apply/revert on scratch PG
> — but the runner is deliberately NO-OP on non-SQLite (`daemon/manager.py:6068`,
> `:7545`; `daemon/migrations/runner.py:721-726`; blueprint invariant
> "runner is SQLite-only"). As written the gate could never pass or
> would induce a blueprint violation. **G2.2 reworded (NEW v1.3):**
> runner UP/DOWN apply/revert on scratch **SQLite** + PG leg via
> `SQLModel.metadata.create_all(engine)` on a scratch PG with an
> **index-parity test** (precedent: `tests/migration/test_drift_events_index_parity.py`).
> The G2.2 gate is now feasible and matches the v1.2 §6.3 + NFR-8
> measurement scope.
>
> **AMEND v1.3 / F1 — T2.7 declares `pyjwt[crypto]` explicitly** in the
> module dependency list. The transitive pull via `mcp` in `uv.lock` is
> not load-bearing — pinning `pyjwt[crypto]` directly in `pyproject.toml`
> mirrors the jsonschema-promotion precedent and removes the cross-package
> drift risk.
>
> **AMEND v1.3 / F2 — SKIP LOCKED dialect guard note** added to D2.4 + T2.3
> + G2.1 (and phase5 T5.10). `FOR UPDATE SKIP LOCKED` is PG-only; D17's
> single-code-path SQLite promise requires a portable fallback (plain
> batched DELETE on SQLite — already the current shape — with the 60 s
> sweep tick handling both backends via SQLAlchemy's dialect-agnostic
> delete). The guard note is documentation + a code comment; no extra
> runtime branch.

> **AMEND v1.2 — 2026-10-10 (reviewer APPROVED-WITH-NOTES sweep)**

> **AMENDED v1.1 (D17):** auth tables are inert on SQLite — they are
> created on BOTH backends via the standard migration runner. There is
> NO `MANUAL: TRUE` marker, NO `require_sqlite_rejection` env var, and
> NO SQLite boot-refusal. Single code path serves both backends. The
> `require_sqlite_rejection` env-var row was REMOVED from [REQ] §6.1
> (it contradicted the migration runner's auto-apply behavior at
> `daemon/migrations/runner.py:107-113`); the SQLite-related row in
> [REQ] §6.3 is retracted.
>
> **AMENDED v1.1 sweeper conditions:** follow the readiness-refresher
> task pattern (create in lifespan, **cancel AND await on shutdown** —
> `daemon/api.py:1544-1555`, NOT the manager's fire-and-forget variant);
> per-batch connection acquisition (NEVER hold one connection across the
> sweep — 2026-10-10 checkpoint-pool incident sensitivity); per-batch
> statement timeout (5-10 s). Dual-instance same-DB (dev checkout on
> another port pointing at the same DB): no guard exists today; `SKIP
> LOCKED` + idempotent deletes make it safe — **document in the runbook
> (phase 6), do NOT build a guard**.

## 1. Objective

Stand up the persistence + provider-facing halves of the auth subsystem:

- PG-backed `auth_sessions`, `auth_states`, `auth_bearer_tokens` tables
  ([TA] §Session Model schema), the `daemon/repositories/auth_session/`
  repository (CRUD + CAS + sweep), and per-table migration files.
- `daemon/services/oauth_client.py`: OIDC discovery (TTL cache + 1s→4s→16s
  backoff + manual-endpoint fallback), code exchange, JWKS fetch + kid-miss
  one-shot refresh, ID-token validation (iss/aud/exp/nbf/JWKS).

Entry prerequisites: Phase 1 done (validators + TTL config fields exist).

## 2. Decisions

| ID | Decision | Rationale / source | Rejected alternatives |
|----|----------|--------------------|------------------------|
| D2.1 | **Three separate migration files** — `<ts>_create_auth_sessions.sql`, `<ts>_create_auth_states.sql`, `<ts>_create_auth_bearer_tokens.sql` — NOT the single `create_auth_tables.sql` name floated in [TA] §Session Model | Repo blueprint: "Every new SQLModel table requires its own `*.sql` file" (79-file history, zero exceptions); [REQ] A10 explicitly says single migration **per table**. [TA]'s single-file mention is a naming slip; blueprint rule wins | one combined file (breaks the per-table audit-trail convention) |
| D2.2 | **AMENDED v1.1 / D17 (SQLite inert-tables leader ruling):** auth tables are created on BOTH backends (PostgreSQL + SQLite) via the standard migration runner. There is NO `MANUAL: TRUE` marker, NO `require_sqlite_rejection` env var, and NO SQLite boot-refusal. Single code path serves both backends. **Rejected:** the original "auth-ON + SQLite refuses boot" via a P1 exit-78 helper — it contradicted the runner's auto-apply behavior at `daemon/migrations/runner.py:107-113`. | [TA] R4(a); [REQ] NFR-8 (reworded); architect §1.2 | SQLite fallback for auth tables (with refusal); MANUAL markers on auth tables (hidden second code path) |
| D2.3 | State consumption is a **single CAS** `pending → consumed` keyed on the `auth_states` PK; loser gets `400 STATE_REPLAYED` | [TA] §Session Model; [REQ] FR-6/FR-7 — exactly-once arbitration point | two-phase token table or in-memory dedup |
| D2.4 | **AMENDED v1.1 sweeper conditions:** sweeper is a background lifespan task (5 min interval, batches of 1000, `SELECT … FOR UPDATE SKIP LOCKED`); `auth_states` gets its own 60 s loop. **Sweeper task follows the readiness-refresher pattern** — created in lifespan, **cancelled AND awaited on shutdown** (`daemon/api.py:1544-1555` — NOT the manager's fire-and-forget variant). **Per-batch connection acquisition** — never hold one connection across the sweep (2026-10-10 checkpoint-pool incident sensitivity). **Per-batch statement timeout 5-10 s** (so a wedged backend does not stall the sweeper). **AMEND v1.3 / F2 — SKIP LOCKED dialect guard:** `FOR UPDATE SKIP LOCKED` is PG-only; D17's single-code-path SQLite promise requires a portable fallback. The sweeper uses SQLAlchemy's dialect-agnostic `with_for_update(skip_locked=True)` which SQLAlchemy silently no-ops to a plain `DELETE` on SQLite (no skip-locked semantics on SQLite — the portable fallback IS the plain batched delete; tests on SQLite assert the batched-delete shape, tests on PG assert the skip-locked shape). The 60 s state sweep runs the same dialect-agnostic delete. **No runtime dialect branch** — the abstraction lives at the SQLAlchemy query level. | [TA] §Session Model sweeper; decoupled from validate path (K4) | sweep-on-read (adds latency to hot path); single-connection-across-batches (pool-hold under PG degradation — R12); raw `FOR UPDATE SKIP LOCKED` literal string (PG-only — violates D17 single-code-path) |
| D2.5 | `OAuthClient` uses the existing `httpx.AsyncClient` lane (same pool discipline as LLM failover / DB external-conn clients); never-raises contract at the service boundary | [TA] §Scalability + `daemon/clients/plane_http_client.py` precedent | new connection pool; synchronous client |
| D2.6 | JWKS: lazy first fetch at first callback, process-lifetime cache, kid-miss → ONE forced refresh then `401 JWKS_UNAVAILABLE`; HTTPS-only, redirects refused | [TA] §Integration 3-5; [REQ] FR-31 | periodic polling (wasteful); accept-HTTP JWKS (JWKS-poisoning risk, [TA] R7) |

## 3. Tasks

| ID | Task | Files touched | What changes (plan level) | Satisfies |
|----|------|---------------|---------------------------|-----------|
| T2.1 | Author 3 migration files (`-- UP` / `-- DOWN`, SHA-checksummed) with the exact schema from [TA] §Session Model (indexes: `idx_auth_sessions_expires_at`, `idx_auth_bearer_tokens_expires_at`) | `daemon/migrations/versions/<ts>_create_auth_sessions.sql`, `…_create_auth_states.sql`, `…_create_auth_bearer_tokens.sql` | Schema only; portable column types (TEXT ids, TIMESTAMP) | FR-10; NFR-4/8 groundwork |
| T2.2 | SQLModel models: `AuthSession`, `AuthState`, `BearerToken` (fields per [TA] §Component Architecture repo box; `BearerToken.id_hash = sha256(token)`) | `daemon/repositories/auth_session/models.py` | Models only | FR-10/16 |
| T2.3 | Repository: session insert/get/touch/invalidate; state CAS-consume + sweep; bearer insert/get-by-hash/revoke; batch sweep (`with_for_update(skip_locked=True)` — **AMEND v1.3 / F2:** SQLAlchemy dialect-agnostic; PG → `FOR UPDATE SKIP LOCKED`, SQLite → plain batched `DELETE` via SQLAlchemy's silent no-op; tests assert the batched-delete shape on SQLite and the skip-locked shape on PG); SQLite-compat typing per repo convention (single code path serves BOTH backends — **AMEND v1.2 / A1:** no runtime PG-only refusal; auth tables are inert on SQLite, not rejected — D17 leader ruling) | `daemon/repositories/auth_session/repository.py`, `factory.py` per `daemon/repositories/<domain>/` convention | Persistence layer | FR-7/10/16/17; NFR-4 |
| T2.4 | Sweeper service: 5-min session/token loop + 60-s state loop, started in lifespan alongside existing background tasks (**AMEND v1.2 / Minor #2:** lifespan site is `daemon/api.py:1544-1555` — the readiness-refresher task pattern, NOT `daemon/manager.py:315-345`; line-drift corrected), config-gated by `auth.enabled` | `daemon/services/auth_session/sweeper.py` (new), lifespan wiring | Background hygiene | FR-10 (TTL hygiene); NFR-4 |
| T2.5 | `OAuthClient.discovery()` — boot-time fetch, 5 s timeout, 1s/4s/16s backoff (FR-29), TTL cache (`discovery_cache_ttl_seconds` default 3600), manual-endpoint fallback when `discover=false`-style overrides set (`authorization_endpoint`/`token_endpoint`/`jwks_uri` overrides from P1 config) | `daemon/services/oauth_client.py` (new) | Provider resolution | FR-29; NFR-7 (degraded-not-dead) |
| T2.6 | `OAuthClient.exchange_code()` — form-body POST (`grant_type`, `code`, `redirect_uri`, `client_id`, `client_secret`, `code_verifier`); non-2xx → WARNING log truncated to 256 chars + typed error (no provider-body leak) | `daemon/services/oauth_client.py` | Token exchange | FR-8 |
| T2.7 | `OAuthClient.fetch_jwks()/validate_id_token()` — JWKS cache, kid-miss one-shot refresh, constant-time `iss` compare, `aud` strict, `exp`/`nbf` ±`clock_skew_seconds`, signature verify; typed validation failures. **AMEND v1.3 / F1:** declare `pyjwt[crypto]` explicitly in `pyproject.toml` (the transitive pull via `mcp` in `uv.lock` is not load-bearing — the jsonschema-promotion precedent pins it directly to remove cross-package drift; `pyjwt[crypto]` pulls in `cryptography` for RS256 verification of ID-tokens). | `daemon/services/oauth_client.py` | ID-token trust boundary | FR-9, FR-31 |
| T2.8 | Wire sweeper + client lifecycles behind `settings.auth.enabled` (OFF → nothing constructed — extends the FR-2 surface to the service layer) | `daemon/manager.py` (lifespan), `daemon/services/oauth_client.py` | OFF-state = zero constructed auth objects | FR-2 |

## 4. Tests to Write

| File | Asserts |
|------|---------|
| `tests/unit/repositories/test_auth_session_repository.py` | Session CRUD round-trip; `last_used_at` batched touch; invalidate = soft `revoked_at`; sweep deletes only `expires_at < now()` rows in batches (seed 1500, assert batch + remainder) |
| `tests/unit/repositories/test_auth_state_cas.py` | CAS wins once (second consume raises/returns loser → maps to `400 STATE_REPLAYED`); expired state → `400 STATE_EXPIRED` path; concurrent consume via two tasks → exactly one winner |
| `tests/unit/repositories/test_auth_bearer_repository.py` | Hash-only storage (plaintext never persisted — grep row dump); get-by-hash hit/miss; revoke sets `revoked_at`; expired rows skipped by lookup |
| `tests/unit/test_oauth_client_discovery.py` | Discovery fetch + TTL cache; backoff schedule observed on 503-then-200 fake; persistent failure → cache invalidated + typed unreachable error; manual-endpoint override skips fetch |
| `tests/unit/test_oauth_client_token.py` | Exchange request body shape (form fields incl. `code_verifier`); non-2xx → typed error, log body truncated ≤256; secret never in log output |
| `tests/unit/test_oauth_client_jwks.py` | kid hit validates; kid miss triggers exactly ONE refetch (fake counts); second miss → `JWKS_UNAVAILABLE`; wrong `iss`/`aud`/`exp`/`nbf`/signature each reject (AC-9) |
| `tests/unit/test_auth_sweeper.py` | Sweeper interval config; state sweeper deletes consumed+expired; no interference with live rows |

## 5. Exit Criteria (gated)

- G2.1: All 7 unit files pass against **PG** (repo convention: repository
  tests run on real DB; **AMEND v1.2 / A1:** the auth tables are
  created on BOTH backends via the standard migration runner — auth-on
  with SQLite is INERT, not refused; the v1.1 SQLite-refusal phrasing is
  retracted per D17; auth-enabled fixtures run on SQLite and PG alike).
  **AMEND v1.3 / F2 — dialect guard:** the sweeper tests assert BOTH
  shapes — the PG leg asserts the `FOR UPDATE SKIP LOCKED` shape, the
  SQLite leg asserts the plain batched `DELETE` shape (the portable
  fallback; SQLAlchemy `with_for_update(skip_locked=True)` silently
  no-ops to a plain delete on SQLite, 60 s sweep tick unchanged on both) —
  no runtime dialect branch exists.
- **G2.2 (AMEND v1.3 / B5)** — `-- UP`/`-- DOWN` applied+reverted cleanly by the migration runner **on a scratch SQLite database** (`daemon/migrations/runner.py:107-113` is intentionally SQLite-only — the runner auto-applies on PG via SQLModel create_all during lifespan, not via the runner's PG path; this is the blueprint invariant "runner is SQLite-only"). The PG leg is verified SEPARATELY: `SQLModel.metadata.create_all(engine)` on a scratch PG database (the same path the lifespan uses) + an **index-parity test** that asserts the indexes defined in the auth `.sql` files (`idx_auth_sessions_expires_at`, `idx_auth_bearer_tokens_expires_at`) are materialized on the PG side — precedent: `tests/migration/test_drift_events_index_parity.py` (same shape: SQLite `runner UP/DOWN` + PG `create_all` + cross-backend index parity assertion). Checksums registered on the SQLite leg. The G2.2 gate can pass cleanly without violating the blueprint invariant. **NFR-8's measurement** (already in the v1.2 text) scopes SQLite correctly — only the gate wording was wrong; G2.2 is the fix.
- G2.3: With `auth.enabled=false`, none of the new services construct
  (introspection: no sweeper task, no client in app.state) — FR-2 extended.
- G2.4: No `random.` usage in any new module (`secrets` only — NFR-13).

**Size: L** — three new tables + repository + a stateful async HTTP
client with four distinct failure modes and a cryptographic validation
path; the largest production-code phase by surface.

## 6. Phase-Specific Risks

| Risk | Mitigation |
|------|------------|
| Migration checksum drift if schema edited post-landing | Schema frozen at T2.1 review; edits require a NEW migration file (repo convention) |
| CAS portability (PG vs SQLite semantics) | CAS implemented as single conditional UPDATE … WHERE consumed_at IS NULL (portable); **AMEND v1.2 / A1:** the v1.1 "PG-only refusal tested for auth-ON anyway" wording is retracted — D17 inert tables means no refusal test exists; auth-on works on SQLite the same way it works on PG (single code path, no MANUAL markers, no boot-refusal). |
| Backoff test flakiness (real sleeps) | Backoff delays injected via clock/sleeper seam in `OAuthClient` (tests pass a fake sleeper — no real 5 s waits) |
| JWKS fetch in unit tests reaching network | `jwks_uri_override` points at in-process fake (P5 fixture) or a local httpx MockTransport |
