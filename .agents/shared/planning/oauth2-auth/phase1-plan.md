# Phase 1 — Config + Boot-Validation Foundation

**AMEND v1.3 — 2026-10-10 (approver iteration-001 fixes)**

> **AMEND v1.3 / F11 — T1.2 matrix count reconciled (12 vs 13 vs 14 drift).** Prior to v1.3 the v1.2 wording said "12 rows" (T1.2 description) but the Size note said "13-row validation matrix" — and the §6.3 table has 14 active rows (15 rows total, row 6 retracted per D17). **T1.2 reworded (NEW v1.3):** "14 active rows after row 6 retracted per D17 — AMEND v1.3 / F11 reconciliation." G1.1 + Size note synced to 14.

> **AMEND v1.2 — 2026-10-10 (reviewer APPROVED-WITH-NOTES sweep)**

> **AMENDED v1.1 (C4):** the exit-78 misconfig validation lane is re-sited
> from lifespan to the **boot preflight** beside `_boot_db_preflight`
> (exit-75 precedent, `daemon/__main__.py:319-324`, BEFORE
> `uvicorn.run`). A lifespan `RuntimeError` exits via uvicorn's generic
> path (exit 1 + traceback, AFTER the port bind), so the original lifespan
> placement was unreachable as specified. Discovery-fetch WARN stays at
> lifespan (network I/O stays out of config-time validators and the
> preflight). Promote interaction confirmed: boot-fail → version-verify
> gate fail → auto-rollback is the pipeline working as designed, and only
> bites operators who opted in.

> **AMEND v1.2 — applied amendments in this file:**
> - **A1 (D17 propagation):** risk-3 reworded to drop the residual SQLite-refusal framing.
> - **A2 (canonical tier-1 field):** T1.1 adds `external_redirect_base: str | None = None` as the canonical tier-1 operator override; `live_view_external_base_url_fallback` stays as the tier-2 fallback for the proxy pattern.

## 1. Objective

Introduce the `AuthConfig` BaseSettings section (env prefix
`ENSEMBLE_AUTH_`) with all 23 env vars from [REQ] §6.1, the pydantic
combination validators from [REQ] §6.2/§6.3, and the boot fail-fast
behavior (exit 78 + ERROR listing missing fields — **at boot preflight,
not lifespan**, AMENDED v1.1 / C4). OFF-state parsing of existing
configs MUST be untouched (NFR-2).

Entry prerequisites: none (first phase).

## 2. Decisions

| ID | Decision | Rationale / source | Rejected alternatives |
|----|----------|--------------------|------------------------|
| D1.1 | Follow the `LiveViewsConfig` template (`daemon/config.py:2554-2628`, `env_prefix="ENSEMBLE_LIVE_VIEWS_"`) for a new `auth:` section aggregated via `Field(default_factory=...)` into root `Config` (`config.py:3360-3391`) | [TA] §Current Patterns; consistent with the existing per-feature settings convention | flat top-level `ENSEMBLE_AUTH_*` fields without a section |
| D1.2 | Client secret + bootstrap token are `SecretStr`; never logged | [REQ] §6.1; NFR-12 redaction | plain `str` |
| D1.3 | `extra="forbid"` on the section so typo'd env vars fail loudly at boot | [REQ] §6.2 sketch | `extra="ignore"` (silent typo swallowing) |
| D1.4 | CSV/JSON list fields (`scopes`, `allowed_origins`) use `Annotated[list[str], NoDecode]` + `_parse_csv_or_json_list` (`config.py:63-96`) | [TA] §Current Patterns; [REQ] §6.1 rows | bespoke split(",") parsing |
| D1.5 | **AMENDED v1.1 / C4:** static misconfig validation runs at the **boot preflight** beside `_boot_db_preflight` (exit-75 precedent, `daemon/__main__.py:319-324`), NOT at lifespan. Pydantic `@model_validator(mode="after")` validates the section at config-load time (import-time fast-fail); the boot preflight is the authoritative exit-78 surface with the operator-actionable missing-field ERROR. Discovery-fetch WARN stays at lifespan (network I/O). | [REQ] FR-26/NFR-7 (reworded); [TA] Integration 12; architect §1.4 C4 | lifespan-only (unreachable as specified — uvicorn generic path exits 1 post-bind); validator-only (pydantic error text is less operator-actionable than an enumerated missing-field list) |

## 3. Tasks

| ID | Task | Files touched | What changes (plan level) | Satisfies |
|----|------|---------------|---------------------------|-----------|
| T1.1 | Add `AuthConfig` section per [REQ] §6.2 sketch: 23 fields, defaults per [REQ] §6.1 table, `SecretStr` for `client_secret`/`bootstrap_token`, `HttpUrl` for issuer/discovery. **AMENDED v1.1 / D2:** add `live_view_external_base_url_fallback: str | None = None` (NOT an env var; populated once at lifespan from `config.live_views.external_base_url`; the same one-shot-copy pattern as `bind_host`/`bind_port`). **AMEND v1.2 / A2 (canonical tier-1):** add `external_redirect_base: str | None = None` as the canonical tier-1 operator override for the redirect_uri base (env-populatable via `ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE`, scheme+host[:port] only, no path); the `live_view_external_base_url_fallback` remains the tier-2 fallback. **AMEND v1.2 / A3 (S-20 KB-MCP-auth seam placeholder):** add `kb_mcp_auth_enabled: bool = False` (NOT an env var; reserved seam only, default False; the v1 posture is `/api/mcp/kb` and `/api/mcp/kb/sse` PUBLIC per D1). **AMENDED v1.1 / S-19:** add `anonymous_view_paths: Annotated[tuple[str, ...], NoDecode] = ()` (S-19 seam; v1 ships empty + regression-cage entry). **AMENDED v1.1 / D17:** REMOVE the `require_sqlite_rejection` field — superseded by the inert-tables posture. | `daemon/config.py` | New settings class + aggregation into root `Config` | FR-1, FR-26; NFR-2 |
| T1.2 | Implement combination validators (invalid-combination matrix, [REQ] §6.3, **14 active rows** after row 6 retracted per D17 — AMEND v1.3 / F11 reconciliation): missing client_id/secret/issuer → error; http issuer without `allow_insecure_cookie` → error; wildcard-CORS+credentials with empty `allowed_origins` → error; samesite Strict → warn; scopes missing `openid` → error; redirect_uri cross-origin → error; discovery/issuer mismatch → error; `cookie_secure=false` without `allow_insecure_cookie` → error; plus 6 more (different-origin `external_redirect_base`, etc.) | `daemon/config.py` | Validator block; WARN path for Strict | FR-26, FR-27; NFR-7 |
| T1.3 | **AMENDED v1.1 / C4:** boot preflight misconfig lane — when `auth.enabled=true`, enumerate missing required fields and exit 78 (`EX_CONFIG`) with an ERROR that names each missing env var. Sits beside `_boot_db_preflight` (exit-75 precedent, `daemon/__main__.py:319-324`), BEFORE `uvicorn.run`. The discovery-fetch WARN stays at lifespan (network I/O does not belong in config-time validators). | `daemon/__main__.py` (boot preflight site, beside `_boot_db_preflight`); `daemon/config.py` (helper returning missing-field list) | Explicit exit-code path; no daemon start | FR-26, FR-27, FR-28(config part); NFR-7 |
| T1.4 | `config.yaml` composition examples ([REQ] §6.4): default `auth:` block (enabled: false) + commented operator-enable example | `config.yaml` | Docs-in-config only; zero behavior change when disabled | NFR-2; FR-35 groundwork |
| T1.5 | Public-path constant: `AUTH_DEFAULT_PUBLIC_PATHS` tuple (module-level, later consumed by P3 middleware construction; **AMENDED v1.1 / D1:** includes `/api/mcp/kb` and `/api/mcp/kb/sse` for v1) + scope-name constants (`system:read`, `instances:read`, … per [REQ] §7.3 taxonomy) | `daemon/constants.py` or `daemon/auth/__init__.py` (new package skeleton) | Constants only — no runtime behavior | FR-22 groundwork; FR-18 groundwork |

## 4. Tests to Write

| File | Asserts |
|------|---------|
| `tests/unit/test_auth_config.py` | Default `enabled=False` when `auth:` absent ([REQ] §6.3 row 9); OFF + everything-set boots normally, config silently ignored; all 23 env vars parse with table-listed defaults; CSV and JSON list forms both parse for `scopes`/`allowed_origins`; **`live_view_external_base_url_fallback` default `None`** (AMENDED v1.1 / D2) |
| `tests/unit/test_auth_config_validation.py` | The invalid-combination matrix from [REQ] §6.3 — **14 active rows** (15 table rows incl. the struck D17 SQLite row; AMEND v1.3 / F11 canonical count) — raises with the specified messages; `Strict` produces WARNING not error; exit-78 helper returns the enumerated missing-field list (unit-level via the helper, boot-level covered in P5 canary + P1 exit-78 preflight harness) |
| `tests/unit/test_auth_secret_hygiene.py` | `repr()`/`str()` of the parsed config never contains secret material (NFR-12 groundwork); `SecretStr` fields mask |
| `tests/integration/test_auth_boot_preflight.py` (AMENDED v1.1) | Subprocess harness: spawning the daemon binary with `auth.enabled=true` + missing `client_id` exits 78 with a structured ERROR listing the missing field (proves the preflight site, not the lifespan site). Mirrors the existing `_boot_db_preflight` test pattern. |

## 5. Exit Criteria (gated)

- G1.1: All four test files pass; the **14-row matrix** is fully covered (AMEND v1.3 / F11 reconciliation — was 12 pre-F11, the missing rows are different-origin `external_redirect_base` + the 2 "ENABLED=true + BOOTSTRAP_TOKEN/SCOPES empty" rows).
  (SQLite row retracted per D17).
- G1.2: The pre-existing config test pack passes UNCHANGED (no fixture
  edits anywhere — NFR-2 evidence).
- G1.3: Booting the daemon with default config produces zero auth-related
  log lines (OFF-state silence).
- G1.4 (AMENDED v1.1): Subprocess exit-78 harness green — boot with
  `auth.enabled=true` + missing field exits 78 with the structured
  ERROR before any port bind (proves the preflight site).

**Size: M** — one settings section, but the 14-row (active) validation
matrix + secret hygiene + boot-fail-fast lane make it more than a
skeleton pass.

## 6. Phase-Specific Risks

| Risk | Mitigation |
|------|------------|
| Root-`Config` aggregation change perturbs existing config parsing | Aggregation is additive (`Field(default_factory=AuthConfig)`); G1.2 guards |
| `extra="forbid"` collides with a future operator's stale env var | Error message names the offending key (validator path is the DX surface) |
| Exit-78 lane duplicated later by P2 SQLite check | Single helper function owns the exit; P2 calls it (no second exit site) — **AMEND v1.2 / A1:** no P2 SQLite-refusal check exists (D17 inert tables — auth tables created on BOTH backends, no boot-refusal, single code path); the residual P1 risk of duplicated exit lanes is closed by the absence of any P2 refusal site, not by an explicit cross-phase guard. |
| **AMENDED v1.1:** boot preflight exit-78 mis-interacts with the promote pipeline's version-verify gate | Documented as "pipeline working as designed" — boot-fail → version-verify gate fail → auto-rollback. Only operators who opted in to auth-ON + invalid config are affected. |
