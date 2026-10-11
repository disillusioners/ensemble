# Phase 4 — Machine-Bearer Lane (CLI, Scopes, Consumer Enablement)

**AMEND v1.4 — 2026-10-10 (user ruling: module auth lanes)**

**AMEND v1.3 — 2026-10-10 (approver iteration-001 fixes)**

> **AMEND v1.3 / F3 — Router-cage match target pinned.** Prior to v1.3 the cage wording referenced "28" `include_router(` mounts while the v1.2 text referenced 31 raw `include_router(` matches. D4.7 reworded to pin the EXACT cage match target = the **28 `api_router.include_router(` mounts at `daemon/api.py:3097-3142`**. The residual textual matches are documented separately (verified census 2026-10-10: 2 app-level `app.include_router(` calls — the `api_router` aggregate at `:3144` and the live-views router at `:3259` — plus 1 prose comment at `:3138`); they are NOT in the cage's match set.
>
> **AMEND v1.3 / F10 — T4.1 bootstrap consumption pinned to SENTINEL ROW.** Prior to v1.3 the v1.2 wording "consumed in the `auth_bearer_tokens` table metadata or a sentinel row" gave implementers a choice (test target ambiguous). T4.1 reworded to **SENTINEL ROW** in a dedicated `auth_bootstrap_consumption` table (one row per consumed `sha256(env_value)` + `consumed_at` timestamp + `actor` label). This makes the test target definite: a test mints, asserts the sentinel row exists, attempts a second mint with the same env value, asserts REJECT + the sentinel row unchanged.

> **AMEND v1.2 — 2026-10-10 (reviewer APPROVED-WITH-NOTES sweep)**

> **AMENDED v1.1 (C1 — router-mount scope-declaration regression cage):**
> because undecorated routes default to `*` (allow-any-authenticated),
> every future router mounted without an explicit scope declaration
> would silently ship weakest policy. Phase 4 carries a **regression-cage
> test that fails when `include_router()` mounts a router without an
> explicit scope declaration** — per-family manifest where each router
> family is either decorated (`@require_scope(...)`) or explicitly
> wildcarded in the manifest. The default-`*` posture itself stays for
> v1 (default-deny would force decorating all 28 router families in the
> first commission — wrong blast radius, per architect C1).
>
> **AMENDED v1.1 (C2 — bootstrap-token exfiltration surface), three
> hardening conditions (folded into OD-2):**
> - **(a) `ens_env_read` deny-list** — the `daemon/tools/ens_env_tools.py`
>   `ens_env_read(keys=[...])` override path MUST refuse
>   `ENSEMBLE_AUTH_*`-prefixed vars verbatim (mirroring the documented
>   POSTGRES-exclusion posture; today the override path reads ANY env
>   var, so any agent could read `ENSEMBLE_AUTH_BOOTSTRAP_TOKEN`).
> - **(b) one-shot bootstrap consumption** — the bootstrap token is
>   consumed at the first admin mint; subsequent reuse with the same
>   env value is rejected (not silently re-honored).
> - **(c) runbook removal step** — `docs/runbooks/oauth2-auth.md`
>   instructs the operator to remove the env var after the first admin
>   mint. (d) trust-class disclosure: an agent with bash + DB access
>   can still invoke the mint CLI directly — the deny-list does NOT
>   close that lane; same trust class as every other DB-adjacent agent
>   capability, and the runbook says so plainly.

## 1. Objective

Complete Lane B: the operator-facing token mint/revoke CLI
(`python -m daemon.auth.cli` — **AMENDED v1.1 / C2: one-shot
consumption**), the constant-time bearer verification path, the scope
taxonomy + per-route `@require_scope` application (**AMENDED v1.1 / C1:
regression-cage test fails when `include_router()` mounts a router
without an explicit scope declaration**), and the enablement story for
the 12 inventoried consumers ([REQ] §7 — 0 BLOCKED, 0 code-change).

Entry prerequisites: P3 (middleware dispatch + `@require_scope` exist).

## 2. Decisions

| ID | Decision | Rationale / source | Rejected alternatives |
|----|----------|--------------------|------------------------|
| D4.1 | **AMENDED v1.1 / C2:** CLI mint (v1) `issue-token --label --ttl --scopes --bootstrap` reads `ENSEMBLE_AUTH_BOOTSTRAP_TOKEN`; bootstrap gate: empty env + `--bootstrap` → helpful failure ([REQ] §6.3 row 10); first-mint defaults `auth:admin` only (OD-14); **one-shot consumption** — the bootstrap token is consumed at the first admin mint; subsequent mint attempts with the same env value are REJECTED; plaintext printed ONCE; only `sha256(token)` persisted; `revoke --label` for rotation | [TA] OQ-2 + [REQ] OQ-REQ-6 (OD-2 default (a)); FR-16, AC-16; architect §1.3 C2 | HTTP mint endpoint (bootstrap problem; S-11 is the v2 seam); silent re-honor of consumed token (the exfiltration risk) |
| D4.2 | Scope taxonomy: the 7 fine-grained scopes from day 1 (`system:read`, `system:probe`, `instances:read/write`, `jobs:read/write`, `maintenance:read` + `mcp:read`, `sources:write`, `messages:write`, `auth:admin` as needed per [REQ] §7.3); conservative defaults per token at mint | OD-3 default (c) hybrid; FR-18 | two-scope broad taxonomy (under-privileged) |
| D4.3 | Bearer verification: constant-time hash lookup (compute sha256, single indexed PK hit; compare via store semantics — hash equality IS the comparison), expired/revoked rows rejected | [TA] §Machine-Client Lane; FR-17 | plaintext token column; LIKE scans |
| D4.4 | Insufficient scope → `403 SCOPE_INSUFFICIENT` (not 404); missing/invalid credential → 401/302 per FR-24 | [TA] §Machine-Client Lane (route in /api ⇒ 403 convention); AC-18 | silent downgrade / 404 masking |
| D4.5 | Consumer enablement = env-var bearer tokens ONLY; **zero consumer code changes** — the **6 YES-WITH-CONFIG consumers** (rows 7, 8, 9, 12, 13, 14 — AMEND v1.3 / F4 count reconciliation; **AMEND v1.4 / module-lanes: was 7 incl. row 17 — row 17 flipped to auth-INDEPENDENT / MODULE-LANE per decisions.md D19; [REQ] §7.2 = 18 total: 10 YES-UNCHANGED + 6 YES-WITH-CONFIG + 2 N/A**) read a token from env they already control; misconfigured consumer sees a loud 401, daemon unaffected | [REQ] §7.2 aggregate (0 WITH-CODE-CHANGE, 0 BLOCKED); §7.4 fail-loudly contract | exempting loopback (REJECTED — forge-lane risk); editing consumer scripts |
| D4.6 | **AMEND v1.4 / module-lanes — SUPERSEDED (webhooks are MODULE-LANE):** the v1.3 dual-credential rule ("`X-Webhook-Secret` sources pass the middleware when the secret validates — pre-shared-secret lane alongside bearer/cookie") is superseded per the user ruling / decisions.md D19 + [REQ] FR-22a: `/api/webhooks/*` is a MODULE-LANE path — the OAuth2 middleware makes NO auth decision on it and passes it through UNTOUCHED in BOTH auth states; the `X-Webhook-Secret` check is the route's OWN guard at webhooks.py:63-76 (`compare_digest` :69; open ingest when unset :67; 401 :70-76). An OAuth2 session/bearer is NOT accepted as a webhook credential. | [REQ] §5.0/§5.4 (v1.4), FR-39/FR-40/FR-42; decisions.md D19/D20; NFR-14 (unchanged) | requiring browser-style auth on machine-to-machine webhook callers; **also rejected (v1.4): middleware-side webhook-secret dispatch (the v1.3 framing) — it would make the OAuth2 middleware an auth decision-point for a module lane** |
| D4.7 (AMENDED v1.1 / C1 + AMEND v1.3 / F3) | **Router-mount scope-declaration regression cage** — every `api_router.include_router(...)` call in `daemon/api.py` mounts a router with an explicit scope declaration. The per-family manifest (in `tests/test_router_scope_manifest.py` or a sibling) lists each router family with either an explicit `@require_scope(...)` decoration or an explicit wildcard in the manifest. **AMEND v1.3 / F3 — EXACT match target pinned (28 vs 31 vs 27 drift reconciled; census verified 2026-10-10 against the live tree):** the cage greps `daemon/api.py` for `api_router.include_router(` (NOT the raw `include_router(` set). **The verified census: 31 raw textual `include_router(` matches in `daemon/api.py` = 28 `api_router.include_router(` router mounts at `:3097-3142` (the cage match universe) + 2 app-level `app.include_router(` call sites (`:3144` the `api_router` aggregate mount, `:3259` the live-views router) + 1 prose comment (`:3138`).** The app-level mounts are documented separately — their scope posture is fixed by the §5.1/§5.5 protection-matrix verdicts (live-views PROTECTED per UR-2; the aggregate mount inherits per-family manifest entries); they are NOT in the cage's match set. **AMEND v1.4 / P6 census correction (tester finding P-2, 2026-10-11):** the SHIPPED census at c8b6f21f8+ is **30 `api_router.include_router(` mounts @ `daemon/api.py:3385-3449`** (28 unconditional + 2 auth-ON-gated: the `/api/mcp/kb/keys` family + auth-self) over 78 declared routes — the cage test (`tests/unit/test_router_scope_manifest.py`, merge-gate riders `tests/unit/test_oauth2_merge_gate_riders.py`, `test_reserved_seams` family) is the AUTHORITY; the 28@:3097-3142 figure above is stale pre-P4 history (P4/T3.12 added the 2 gated mounts), preserved verbatim per annotate-don't-rewrite. | architect §1.3 C1; [REQ] FR-38 | per-route runtime inspection (cost on hot path); always-required decoration (default-deny would force decorating all 28 router families in the first commission — wrong blast radius) |

## 3. Tasks

| ID | Task | Files touched | What changes (plan level) | Satisfies |
|----|------|---------------|---------------------------|-----------|
| T4.1 | `daemon/auth/cli.py`: argparse subcommands `issue-token` (`--label --ttl --scopes --bootstrap`), `revoke` (`--label`); bootstrap gate: empty `BOOTSTRAP_TOKEN` env + `--bootstrap` → helpful failure ([REQ] §6.3 row 10); first-mint defaults `auth:admin` only (OD-14). **AMENDED v1.1 / C2 (b):** one-shot consumption — after the first admin mint, subsequent `issue-token --bootstrap` calls with the same env value are REJECTED. **AMEND v1.3 / F10 — bootstrap consumption pinned to SENTINEL ROW in dedicated `auth_bootstrap_consumption` table** (NOT in `auth_bearer_tokens` metadata — the latter would conflate bearer-token rows with bootstrap bookkeeping): one row per consumed `sha256(env_value)` (sensitive value never stored plaintext — same hash-only discipline as the bearer table), columns `consumed_at` (timestamp), `actor_label` (the label of the first minted token), `consumed_by_pid` (process that performed the consumption — operator debugging). The CLI helper checks for a sentinel row before honoring the env value: existing sentinel row → REJECT with clear "bootstrap token already consumed at {consumed_at} for {actor_label}; rotate the env var" message; absent sentinel row → mint + insert sentinel row atomically (single TX). The schema lives in `daemon/migrations/versions/<ts>_create_auth_bootstrap_consumption.sql` (4th auth-table migration; per-table convention preserved per D14). | `daemon/auth/cli.py` (new; `python -m daemon.auth.cli`); `daemon/migrations/versions/<ts>_create_auth_bootstrap_consumption.sql` (new sentinel-table migration); `daemon/repositories/auth_bootstrap/` (new repo) | Mint surface + one-shot bookkeeping | FR-16; AC-16 |
| T4.2 | **AMENDED v1.1 / C2 (a):** `ens_env_read` deny-list — modify `daemon/tools/ens_env_tools.py` to refuse any key starting with `ENSEMBLE_AUTH_` on the override path (`keys=[...]` argument). Mirrors the existing POSTGRES-exclusion posture. Documented in the tool's docstring; a regression test asserts the refusal. | `daemon/tools/ens_env_tools.py`, `tests/unit/test_ens_env_tools.py` (deny-list coverage) | Tool surface — closes the bootstrap-token exfiltration lane | FR-16a; AC-16a |
| T4.3 | Bearer verify path in `AuthService` (`lookup_bearer`, constant-time per D4.3) + middleware branch consumption (P3 D3.4 bearer slot now real) | `daemon/services/auth_service.py`, `daemon/middleware/auth.py` | Lane B runtime | FR-17; AC-17 |
| T4.4 | **AMENDED v1.1 / C1:** scope application per [REQ] §5.4/§7.3 + **D4.7 router-mount scope-declaration regression cage** — decorate route families (system probes n/a-public; mcp-servers `mcp:read`; instances/messages/sources/jobs read+write splits; maintenance writes `maintenance:read`; DB switch/snapshot admin `auth:admin`/`snapshot:write`); the per-family manifest in `tests/test_router_scope_manifest.py` lists every family as decorated or explicitly wildcarded; the cage test fails when `include_router()` mounts a router without an entry. | `daemon/routers/*.py` (decorator lines only — behavior gated by auth ON); `tests/test_router_scope_manifest.py` (cage + manifest) | Least-privilege mapping + regression cage | FR-18, FR-38; AC-18; [REQ] §7.3 |
| T4.5 | **AMEND v1.4 / module-lanes — SUPERSEDED (no middleware dispatch work for webhooks):** the v1.3 task "webhook pre-shared-secret lane in middleware dispatch (D4.6)" is superseded — `/api/webhooks/*` is MODULE-LANE (FR-22a v1.4 / decisions.md D19): the OAuth2 middleware never makes an auth decision on it; the `X-Webhook-Secret` check is the route's OWN guard at webhooks.py:63-76 (pre-existing, unchanged). **Residual v1.4 scope in this task = zero new middleware code**; the v1.3 "read-only consult of source secret registry" in `daemon/middleware/auth.py` is DROPPED. What remains here: parity verification only — webhook without secret on a configured source → 401 (module guard); open-ingest source unchanged; OAuth2 cookie/bearer NOT accepted as a webhook credential (matrix leg lives in P5 T5.11 per AC-40). | `daemon/routers/webhooks.py` (read-only — guard is pre-existing); parity legs in `tests/integration/test_module_lanes.py` (P3 T3.10) | Module-lane parity (no dual-credential path) | NFR-14; [REQ] §5.0/§5.4 (v1.4), §7.1 row 17; AC-40 groundwork |
| T4.6 | Consumer enablement guide (feeds P6 runbook §Token mint): per-consumer env var + label + scope table straight from [REQ] §7.1 rows 7-9 (`mcpservers-test`, `e2e-pause-resume-test`, `e2e-mock-source`) + generic remote-VM pattern (row 14). **AMENDED v1.1 / C2 (c):** runbook includes the bootstrap env-var removal step. | `docs/runbooks/oauth2-auth.md` (draft section), `.agents/shared/planning/oauth2-auth/` notes | Operator path | FR-32; AC-32 groundwork |
| T4.7 | `frontend/scripts/boot-e2e-auth-daemon.sh` placeholder wiring (full body in P5): boots a daemon with auth ON + a minted canary token env and curls the 5 public probes + one bearer-gated route | `frontend/scripts/boot-e2e-auth-daemon.sh` (new; pattern of `boot-e2e-snapshots-daemon.sh`) | Boot-e2e vehicle | FR-23/34 groundwork |

## 4. Tests to Write

| File | Asserts |
|------|---------|
| `tests/unit/test_auth_cli.py` | Mint prints plaintext exactly once; DB row stores `sha256` hex + label + scopes + expiry (AC-16); `--bootstrap` without env → clear failure message; revoke flips `revoked_at`; revoked token rejected at lookup. **AMENDED v1.1 / C2 (b):** the second `issue-token --bootstrap` call with the same env value is REJECTED with a clear error message (one-shot consumption). **AMEND v1.3 / F10:** the test asserts the SENTINEL ROW in `auth_bootstrap_consumption` is created on first mint + unchanged on the rejected second mint — test target is definite. |
| `tests/unit/test_ens_env_tools_auth_denylist.py` (AMENDED v1.1 / C2 (a)) | `ens_env_read(keys=["ENSEMBLE_AUTH_BOOTSTRAP_TOKEN"])` returns an explicit denial (mirroring the POSTGRES-exclusion posture); `ens_env_read(keys=[])` (default path) is unchanged; `ens_env_read(keys=["PATH"])` (non-auth env var) still works. |
| `tests/unit/test_router_scope_manifest.py` (AMENDED v1.1 / C1 + AMEND v1.3 / F3) | The manifest lists every one of the **28** `api_router.include_router(` mounts in `daemon/api.py:3097-3142` (the canonical cage match target — NOT the raw `include_router(` textual set, which per the verified 2026-10-10 census also contains 2 app-level `app.include_router(` calls (`:3144` aggregate, `:3259` live-views router) + 1 prose comment (`:3138`); the app-level mounts are documented separately with their posture fixed by the §5.1/§5.5 verdicts). The cage test greps for the SPECIFIC `api_router.include_router(` pattern and asserts every mounted family has a manifest entry. Fails loudly on omission. |
| `tests/integration/test_bearer_lane.py` | Correct bearer → 200 + `auth_method="bearer"` context (AC-17); wrong bearer → 401; expired → 401; insufficient scope → 403 SCOPE_INSUFFICIENT (AC-18); both cookie+bearer → bearer wins (AC-13 re-pin at lane level) |
| `tests/integration/test_scope_matrix.py` | Table-driven: route family × token scope → expected 200/403 per the [REQ] §7.3 minimum-scope table (7 consumer labels × their routes) | **AMEND v1.4 / P6 count correction:** §7.3 now enumerates **8** token labels (the 8th is the generic `<vm-name>` per-VM pattern) — G4.1 verified the static diff EXACT 8/8 (`mcpservers-test, e2e-pause-resume-test, e2e-mock-source, boot-e2e-canary, frontend-spa, promote-rotate, operator-cli` + `vm-edge-01` as the `<vm-name>` representative); "7 labels" above is stale history.
| `tests/integration/test_webhook_dual_credential.py` | Valid `X-Webhook-Secret`, no bearer → 200 (auth ON); invalid secret + no bearer → 401; valid secret + Origin guard still enforced (NFR-14 composition) |
| `tests/integration/test_consumer_enablement.py` | For each of the 3 YES-WITH-CONFIG in-repo consumers: replay their exact request shapes with an env-var bearer → all succeed; same shapes without token → 401 with machine JSON body (fail-loudly contract, [REQ] §7.4) |

## 5. Exit Criteria (gated)

- G4.1: All 5 test files pass; the scope matrix has a row for every
  consumer label in [REQ] §7.3.
- G4.2: Log-leak grep: no plaintext token in any log line or DB row dump
  (NFR-12 extension of G2.4).
- G4.3: The 8 YES-UNCHANGED consumers ([REQ] §7 rows 1, 2, 3, 4, 5, 6, 10, 11 — AMEND v1.3 / F4 count reconciliation) verified
  tokenless against an auth-ON daemon (public probes only) — recorded in
  the phase report as AC-32 partial evidence. **AMEND v1.4 / module-lanes — census 8 → 10:** [REQ] §7.2 now counts **18 consumers total = 10 YES-UNCHANGED + 6 YES-WITH-CONFIG + 2 N/A**; the two added YES-UNCHANGED rows are module-lane and verified tokenless the same way — row 17 (external webhook senders, flipped auth-INDEPENDENT per D19) + row 18 (MCP-KB clients) (AC-41 legs; full matrix in P5 T5.11).
- G4.4: Zero diffs under `frontend/src/**` and zero edits to the 12
  consumer scripts (NFR-18 + FR-32 "no code change" proof by `git status`).
- G4.5 (AMENDED v1.1 / C1): `tests/unit/test_router_scope_manifest.py` green — every `include_router()` in `daemon/api.py` has a manifest entry; the cage test fails on omission.
- G4.6 (AMENDED v1.1 / C2): `tests/unit/test_ens_env_tools_auth_denylist.py` green; CLI one-shot consumption test green; runbook §Token mint documents the env-var removal step.

**Size: M** — one CLI + one lookup path + decorator application, but the
consumer-matrix verification is broad and webhook dual-credential adds a
dispatch branch.

> **AMEND v1.4 / module-lanes (P6 discharge of approver note 1, 2026-10-11):** the prose above ("webhook dual-credential adds a dispatch branch") is STALE — superseded by phase3 T3.10 + decisions.md D19: webhook ingest is MODULE-LANE, the OAuth2 middleware makes NO auth decision on it, and there is NO dual-credential dispatch branch (see the AMEND v1.4 SUPERSEDED markers already carried by rows D4.6 and T4.5; the P4 residual scope was parity verification only). Text preserved verbatim per annotate-don't-rewrite.

## 6. Phase-Specific Risks

| Risk | Mitigation |
|------|------------|
| Decorator application to the 28 api_router-mounted families touches many files → merge noise | Decorator lines are inert when auth OFF (middleware never populates context ⇒ default `*` semantics); apply per-router commits grouped by family for review — **AMEND v1.3 / F3 count fix:** was "27 routers" pre-v1.3; the canonical count is the 28 `api_router.include_router(` mounts (verified census in D4.7) |
| Scope map drifts from [REQ] §7.3 | `test_scope_matrix.py` IS the map (table-driven); changes to scopes require a table row change — visible in review |
| Bootstrap token mishandling (logged, shell history) | CLI warns on `--bootstrap` use; runbook documents `read -s` pattern; plaintext never written by the daemon side |
| Webhook lane bypassing scope checks entirely | Dual-credential path attaches a `bearer`-equivalent `AuthContext` with `sources:write`-only scopes so downstream decorators still apply |
