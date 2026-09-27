# Phase 3 — Bootstrap + KMS-Lite (designer-agent implementation)

- **Date:** 2026-09-26
- **Author:** planner[v2] via plan-creation worker (phase-3 dispatch)
- **Source of truth:** `.agents/shared/planning/designer-agent/architecture-recommendation.md` (ratified 2026-09-26, D1–D6 LOCKED — never re-opened by this plan)
- **Status:** Draft — implements §7 of the architecture doc; covered clusters A (bootstrap flow), B (mint-only KMS-Lite), C (self-hosted OD installer as first user of the flow)
- **Companions:** `phase1-foundations.md` (P1), `phase2-parallel-builds.md` (P2), `plan-overview.md` + `decisions.md` (separate workers)
- **Hard constraints (worktree-scoped):** docs-only markdown; NO `git add/commit/push/merge`; NO daemon/test boot, NO venv/uv, NO DB/network access from worktree; read-only inspection allowed; D1–D6 never contradicted; §7.5a deferred machinery (policy store, OD Cloud brokering, rotate/revoke/audit surfaces, SSE event kinds, audit atomicity, DEK rotation) is **out of day-1 scope** and appears only as a "Deferred" list.

---

## 0. Phase Boundary (machine-checkable)

| | P3 owns | P3 does NOT own |
|---|---|---|
| Cluster A — Bootstrap flow | `requires:` front-matter parser; `capability_check` pre-flight; escalation envelope schema; `capabilities.yaml` registry; `install-*` skill authoring; `job_continue` resume lane contract | `designer` agent anatomy (P1), `vision` model wiring (P1), tmp_images substrate (P1), OpenDesign MCP builtin class (P2) |
| Cluster B — KMS-Lite | `__KMS_REF__` marker substitution at MCP stdio seam; `kms_request` mint primitive; fail-closed store evolution; one-time raw-row migration; root-key hardening (if §10.1 verdict = hardening WP); logging redaction policy | Policy store / allowlist / budget caps / TTL sweeps (deferred §7.5a); third-party OD Cloud brokering (deferred); `kms_rotate`/`kms_revoke`/`kms_audit` surfaces (deferred); DEK-rotation story (deferred) |
| Cluster C — OD installer skill | `install-opendesign` skill that closes the loop on P2's early manual install; first end-to-end autonomous run of the §7 Mermaid sequence | The OD builtin class implementation (P2 already delivered it) |

### P3 MAY ASSUME (cross-phase contracts — these are FIXED by dispatcher)

- **From P1:** designer live as craft-class sub-team lead with `team_members: ["worker"]` (no change); `vision` model added to daemon-global `allowed_models` + restart landed; spawn-chain model override generalized in `daemon/services/instance_lifecycle.py:1780-1829`; tmp_images substrate with provenance tags + path→data-URI bridge live.
- **From P2:** self-hosted OpenDesign MCP **already installed EARLY via the manual/ops lane** (P2's early-deliverable posture — see plan-overview §); the capture adopt-or-build verdict artifact exists; OD builtin class available for `configure-builtin` reuse.

### P3 LEAVES BEHIND (reusable surface for ANY future "skill with `requires:`")

- The `requires:` front-matter parser + `capability_check` pre-flight is **generic** — any skill in any agent declaring `requires:` is auto-eligible for the same escalation flow.
- KMS-Lite mint-only is **generic** — handles-not-secrets discipline applies to every KMS-issued credential from day 1.
- The `install-opendesign` skill is the **first proven user** of the generic pattern; future installers (`install-notion`, `install-figma`, etc.) reuse the same skeleton + the same `capabilities.yaml` schema.

---

## 1. Phase Objective (1 sentence, testable)

A worker that detects a missing/unconfigured capability can autonomously escalate to designer, trigger the right installer skill, complete the install via the daemon HTTP API (with secrets flowing only through KMS markers), mint a credential via KMS-Lite, and **resume its original job via `job_continue` with all new MCP tools live** — with no human in the loop and no plaintext secret ever transiting an LLM prompt, message, checkpoint, log, or audit line.

---

## 2. Work-Package Index (P3-WPn IDs)

| WP ID | Title | Cluster | Touchpoints | Deps | Risks (from §8 🔴) |
|---|---|---|---|---|---|
| **P3-WP1** | Skill `requires:` front-matter + parser | A | `agents/*/skill-set.yaml` schema; skill-loader in `daemon/services/skill_store_service.py` (TBD exact line — spot-verify at implement); new module `daemon/services/capability_resolver.py` | P1 (designer live), P2 (OD available to declare as `requires`) | none direct |
| **P3-WP2** | `capability_check(capability_id)` pre-flight helper + mandatory-first-instruction rule | A | `daemon/services/capability_resolver.py`; `mcp_servers` lookup at `daemon/repositories/mcp_server/models.py:12-33`; agent-context evaluation pipeline | WP1 | R1 (KMS plaintext leak) — partial; WP2 only reads tool surfaces, does not touch secrets |
| **P3-WP3** | Escalation envelope exact-schema implementation + child-report lane wiring | A | `daemon/services/child_reports.py:3264` (`internal_report:{iid}:{mid}` source); envelope schema validation; `Result:` JSON prefix; `ts` ISO-8601 field | WP2 | none direct |
| **P3-WP4** | `capabilities.yaml` registry co-located with `dynamic-skill` + per-MCP entry schema | A | new file `agents/dynamic-skill/capabilities.yaml` (co-located); per-MCP entry shape (`installer_skill`, `builtin_mcp_class`, `schema_version`, `requires_secret`, `kms_service_id`); loader wired into `daemon/services/skill_store_service.py` | WP2 | R3 (raw-row migration) — gates on WP9 |
| **P3-WP5** | `install-opendesign` skill (first user; generalizes the installer pattern) | A + C | new skill body under `agents/designer/skills/install-opendesign.md` (or `agents/worker/skills/` — verify at implement, prefer the worker agent since install is operational work); HTTP API surface at `daemon/routers/mcp_servers.py:344` (`/configure-builtin`), `:173` (`/test-connection`); idempotency_key writer to `mcp_servers.instance_metadata` | WP1, WP2, WP4, P2 (OD builtin class) | R2 (KMS plaintext leak via post-resolution persist) — gates on WP6 + WP7 |
| **P3-WP6** | `__KMS_REF__` marker substitution at MCP stdio seam (in-RAM only) | B | `daemon/mcp/config.py:190-201` (McpStdioConfig.env); `daemon/mcp/connection_manager.py:206-210` (StdioServerParameters construction); `daemon/mcp/config.py:260` (HTTP/SSE headers same treatment); new `daemon/services/kms_resolver.py` invoked at spawn time, NOT at config-load | WP4 (capabilities.yaml schema), P1 (MCP infra live) | **R1 (🔴 plaintext leak)** — primary mitigation site |
| **P3-WP7** | KMS-Lite mint primitive `kms_request(service, reason) → {handle, fingerprint}` | B | new `daemon/services/kms_lite.py`; `daemon/sources/credentials.py:75-97` (fail-soft fallback → fail-closed evolution); new tool category additions to `daemon/tools/infra.py` (e.g. `kms_request`); handle/fingerprint shape; bind-table to track handle→mcp_server_id | WP6 | **R2 (🔴 fail-soft CredentialManager)** — primary mitigation site; **R4 (🔴 no logging redaction)** — partial |
| **P3-WP8** | One-time raw-row migration: existing `mcp_servers.config.env` rows with plaintext → KMS-marker rewrite | B | `daemon/repositories/mcp_server/models.py:17-26` (McpServer.config); one-shot migration script (lives at `scripts/migrations/kms_lite_raw_row_migrate.py` per house convention — confirm path at implement); audit line per rewritten row | WP7 (mint primitive must exist before migrate can re-mint) | **R3 (🔴 DB stores raw)** — primary mitigation site |
| **P3-WP9** | Fail-closed store evolution: absent `SYSTEM_ENCRYPTION_KEY` ⇒ REFUSE to mint (never plaintext) | B | `daemon/sources/credentials.py:75-97` (current fail-soft); new exception class `KMSUnavailableError`; callers in WP7 fail closed with escalation envelope `kind=installed_but_unconfigured` + `detection_evidence=kms_key_absent` (downgrades to "policy-denied" wording only after §7.5a lands — strictly schema-forward-compat) | WP7 | **R2 (🔴 fail-soft)** — closed here |
| **P3-WP10** | Logging redaction policy + repo-wide enforcement test | B | `daemon/__init__.py` logger config; new `daemon/util/log_redaction_filter.py` (or equivalent); audit invariant — no `KMS_HANDLE_*` value or plaintext credential appears in any log line under any handler | WP6, WP7 | **R4 (🔴 no logging redaction)** — primary mitigation site |
| **P3-WP11** | `job_continue` resume envelope `[resume]` convention ratification | A | `daemon/tools/job_queue.py:1768+` (`job_continue` impl); resume message shape `{capability_id, status, tools_now_available, resume_from}`; ratifies the `[resume]` tag as a convention (not a new tool param) | WP5 (install skill must exist to resume into) | none direct |
| **P3-WP12** | End-to-end autonomous cycle: OD install + KMS mint + resume (the §7 Mermaid, no human) | C | entire phase integrated; verification checklist below (§6) | WP1–WP11 | risks aggregate here; acceptance is provable by the §6 checklist |
| **P3-WP13** | KMS root-key hardening OR explicit deferral (your §10.1 verdict) | B | either: harden `SYSTEM_ENCRYPTION_KEY` file perms / age / OS keychain integration (option WP13a); OR explicit deferral with revisit trigger (option WP13b) | WP9 (key must exist to harden) | out-of-band of the 4 🔴 but architecturally load-bearing for production |

---

## 3. Detailed Work Packages

> Format: **Objective → Touchpoints (file:line cited from arch doc) → Dependencies → Risks carried from §8 → Acceptance criteria (provable).** Each WP is independently completable; the phase exit criterion (§6) requires WPs 1–11 to be green plus WP12 e2e cycle to pass; WP13 is a single decision with one of two follow-up tracks.

### P3-WP1 — Skill `requires:` front-matter + parser

- **Objective:** A skill's `skill-set.yaml` entry MAY declare a top-level `requires:` block listing `mcp`, `tools`, and `env` capabilities. The skill-loader parses it; the parsed shape is exposed to `capability_check` (WP2). All other skill front-matter remains advisory (D6 philosophy preserved).
- **Touchpoints:**
  - New schema field on skill-set entries: see `agents/coder/skill-set.yaml` for the canonical entry shape (no `requires:` key today — greenfield field).
  - Skill-loader: spot-verify the exact loader path at implement (likely under `daemon/services/skill_store_service.py` or wherever `skill-set.yaml` is parsed today — confirm before implementing).
  - New module `daemon/services/capability_resolver.py` owns the parsed `CapabilityRequirement` dataclass.
- **Dependencies:** none from P3; cross-phase P1 (designer is the first agent whose skills will declare `requires:`); cross-phase P2 (OD available as a target capability to require).
- **Risks from §8:** none direct. The risk this WP opens is **drift between declared and detected capabilities** — mitigated by WP2 + WP4 (the only truth source is the registry + the runtime check).
- **Acceptance:**
  - A skill entry declaring `requires: { mcp: [opendesign], tools: [bash], env: [OPEN_DESIGN_LICENSE] }` parses without error.
  - A skill entry declaring an unknown key under `requires:` (e.g. `docker: ...`) parses with a lint warning — DOES NOT hard-fail (D6 philosophy: only `pinned_spec_sha` is hard; everything else advisory).
  - The parsed shape is JSON-serializable and round-trips (load → dump → diff = empty).
  - Unit tests cover: parse-valid, parse-unknown-key-warning, parse-malformed (must hard-fail with line number), missing-file (must default to "no requires" — back-compat with all existing skills).

### P3-WP2 — `capability_check(capability_id)` pre-flight + mandatory-first-instruction rule

- **Objective:** `capability_check(capability_id)` is a synchronous helper that returns a tri-state result: `present`, `missing`, `unconfigured`. It is invoked as the FIRST instruction of every skill that declares `requires:` — before any LLM turn — and on miss returns the structured `CapabilityMiss` object the escalation envelope consumes (WP3).
- **Touchpoints:**
  - `daemon/services/capability_resolver.py` (new).
  - `daemon/repositories/mcp_server/models.py:12-33` — `mcp_servers` lookup; spot-verify that `is_active=true` filter is the canonical "live" predicate at implement.
  - Agent-context evaluation pipeline — confirm the site at implement; should be a single function call from the skill-body instruction loader, not from per-turn runtime.
- **Dependencies:** WP1.
- **Risks from §8:** R1 (KMS plaintext leak) — partial. WP2 reads tool surfaces only; it does NOT touch secret material. The risk surface begins at WP6.
- **Acceptance:**
  - `capability_check("opendesign")` returns `present` when a row in `mcp_servers` with `name=opendesign, is_active=true` exists AND its config env declares the required keys (per `capabilities.yaml`).
  - Returns `missing` when no row exists.
  - Returns `unconfigured` when a row exists but a required env key is absent OR the row's `requires_secret=true` in `capabilities.yaml` but no handle is bound.
  - The check completes in <50ms p95 (DB lookup only — no LLM call, no subprocess).
  - Mandatory-first-instruction rule: a skill body declaring `requires:` that does NOT begin with `capability_check(...)` is rejected by the skill-loader with a clear error pointing at the skill name + the front-matter section.
  - Unit tests cover: present / missing / unconfigured across all three (`mcp`, `tools`, `env`); mandatory-first-instruction violation.

### P3-WP3 — Escalation envelope exact-schema implementation + child-report lane wiring

- **Objective:** Worker → designer escalation uses the `internal_report:{iid}:{mid}` child-report lane with a `Result:`-prefixed JSON envelope matching the §7.2 schema exactly. Day-1 firing kinds = `capability_missing` (→ spawn installer) + `installed_but_unconfigured` (→ mint key + resume). `policy_denied` stays schema-only (forward-compat, cannot fire until §7.5a).
- **Touchpoints:**
  - `daemon/services/child_reports.py:3264` — `source=f"internal_report:{instance.instance_id}:{completed_message_id}"` source-pattern (exact format).
  - Envelope schema (Pydantic): `kind: Literal["capability_missing", "installed_but_unconfigured", "policy_denied"]`, `capability: str`, `installer_skill: str`, `detection_evidence: str`, `blocker_scope: Literal["this_turn", "this_task"]`, `resume_hint: str`, `policy_denied_reason: str | None`, `ts: str` (ISO-8601).
  - The `Result:` JSON prefix convention — spot-verify the exact wire format at `daemon/services/child_reports.py:4348-4421` (the byte-exact 55-byte Result-less envelope comment block — confirm and follow the convention).
- **Dependencies:** WP2.
- **Risks from §8:** none direct. The forward-compat `policy_denied` kind is a deliberate schema-only reservation — it MUST NOT fire from day-1 code paths. Lint test enforces this: a day-1 PR that emits `kind=policy_denied` fails the envelope conformance test.
- **Acceptance:**
  - `kind ∈ {capability_missing, installed_but_unconfigured, policy_denied}` accepted by the schema; `policy_denied` not produced by any day-1 code path (static check).
  - All seven fields are present on every envelope; `policy_denied_reason` is `null` for non-`policy_denied` kinds.
  - The envelope rides the existing `internal_report:{iid}:{mid}` child-report lane — no new transport.
  - Lint test asserts no day-1 code path emits `kind=policy_denied` (the schema accepts it but day-1 doesn't produce it).
  - Unit tests cover: round-trip parse, all three kinds, missing-field rejection, `Result:` prefix parse, child-report lane destination = designer instance.

### P3-WP4 — `capabilities.yaml` registry co-located with `dynamic-skill` + per-MCP entry schema

- **Objective:** A single source of truth that maps `(capability_id)` → `(installer_skill, builtin_mcp_class, schema_version, requires_secret, kms_service_id)`. Loaded by both the capability resolver (read) and the installer skill (read+write).
- **Touchpoints:**
  - New file: `agents/dynamic-skill/capabilities.yaml` (co-located per arch doc §7.3 — `dynamic-skill` is the innate skill that owns dynamic capability injection).
  - Schema per entry (YAML):
    ```yaml
    - capability_id: opendesign
      installer_skill: install-opendesign
      builtin_mcp_class: OpenDesignMCP   # resolved against daemon/mcp/builtin_servers/
      schema_version: "1"
      requires_secret: true
      kms_service_id: opendesign
    ```
  - Loader: spot-verify the exact loader at implement; should sit alongside `skill-set.yaml` parsing.
- **Dependencies:** WP2.
- **Risks from §8:** R3 (raw-row migration) — this WP defines the schema; the actual migration is WP8. WP4 is the precondition for R3 closure.
- **Acceptance:**
  - `capabilities.yaml` parses; an entry missing any required field fails the parse with a clear field-level error.
  - `installer_skill` MUST match a skill name in some agent's `skill-set.yaml` (cross-reference check at load time).
  - `builtin_mcp_class` MUST resolve to a `BuiltinServerDefinition` subclass (spot-verify `daemon/mcp/builtin_servers/base.py:12`).
  - `requires_secret=true` MUST have a non-empty `kms_service_id`.
  - Unit tests cover: valid entry, missing-field rejection, dangling installer_skill, dangling builtin_mcp_class, `requires_secret=true` w/ empty kms_service_id.

### P3-WP5 — `install-opendesign` skill (first user of the bootstrap pattern)

- **Objective:** A skill that, when spawned by designer, drives the full OD install via the daemon HTTP API — no generic `bash install` in workers (arch doc §7.4 invariant). Authored once, then generalized for any future installer.
- **Touchpoints:**
  - Skill body location: verify at implement — natural home is `agents/worker/skills/install-opendesign.md` (worker = the operational agent that already does install-style work; designer is sub-team lead that spawns it). Architecturally, ANY agent with the skill can execute it; the skill itself is the unit of dispatch.
  - HTTP API surfaces used (verified):
    - `POST /api/v1/mcp_servers/configure-builtin` (`daemon/routers/mcp_servers.py:344`) — server creation/update.
    - `POST /api/v1/mcp_servers/test-connection` (`daemon/routers/mcp_servers.py:173`) — verifies the install.
  - Idempotency: `idempotency_key = sha256(name + schema_version + sorted(config))` written to `mcp_servers.instance_metadata` (arch doc §7.4).
  - Audit: one line per install attempt → `planning/{feature}/install-audit.jsonl` (arch doc §7.4 audit shape).
- **Dependencies:** WP1, WP2, WP4; cross-phase P2 (OD builtin class exists for `/configure-builtin`).
- **Risks from §8:** R2 (plaintext leak via post-resolution persist) — closed only when WP6 marker substitution + WP7 mint primitive + WP8 raw-row migration are ALL landed. WP5 by itself writes `__KMS_REF__` markers in the install row (per-day-1 spec), but the persistence leak risk is the AGGREGATE of WP5's API call + WP6's resolver + WP7's mint.
- **Acceptance:**
  - Skill body declares `requires: { mcp: [opendesign-installed], tools: [bash, instance], env: [] }` — the `mcp: [opendesign-installed]` is the post-install self-check; pre-flight requires it but install makes it true (this is the bootstrap loop). Verify the loop's well-formedness at implement.
  - The install writes ONLY `__KMS_REF__` markers to the DB config (NOT plaintext). Acceptance test: post-install `mcp_servers.config.env` JSON contains `__KMS_REF__opendesign_api_key__` (or whatever exact marker format WP6 ratifies) — ZERO plaintext substrings matching the secret.
  - The install uses the existing `configure-builtin` route (not raw `INSERT` into the DB).
  - `idempotency_key` is computed and stored in `instance_metadata`; a re-run with the same config returns the same row (no duplicate INSERT).
  - `prev_config_snapshot` is captured before update; 7-day TTL (arch doc §7.4); uninstall = STOP then DELETE (verify the existing DELETE flow at `daemon/routers/mcp_servers.py:564` handles this).
  - Skill body issues an audit line per install attempt in `install-audit.jsonl` with the exact field set from §7.4.
  - Unit tests cover: install-fresh, install-update, install-idempotent, install-audit-line-shape, schema-version-mismatch-refused.

### P3-WP6 — `__KMS_REF__` marker substitution at MCP stdio seam (in-RAM only)

- **Objective:** DB config and child-report envelopes carry `__KMS_REF__<handle_id>__` markers. At MCP spawn time, the resolver substitutes the marker → plaintext IN-RAM immediately before `StdioServerParameters(...)` is constructed. Plaintext reaches ONLY the subprocess env. The stored config and any path that re-serializes it ALWAYS retains the marker.
- **Touchpoints (verified):**
  - `daemon/mcp/config.py:190-201` — `McpStdioConfig` with `env: dict[str, str]` (exact line 196).
  - `daemon/mcp/config.py:204` — `McpSseConfig` with `headers`.
  - `daemon/mcp/config.py:245` — `McpStreamableHttpConfig` (HTTP headers same treatment).
  - `daemon/mcp/connection_manager.py:206-210` — `StdioServerParameters(command=..., args=..., env=config.env)` (line 206, exact field verified).
  - HTTP/SSE headers at `daemon/mcp/config.py:260` (cited in arch doc §7.5) — verify at implement.
  - New `daemon/services/kms_resolver.py` — invoked at spawn time (NOT at config-load), reads the marker, asks the KMS store for the plaintext, returns the substituted dict, holds it only in-RAM.
- **Dependencies:** WP4 (capabilities.yaml schema); cross-phase P1 (MCP infra live).
- **Risks from §8:** **R1 (🔴 KMS plaintext leak via post-resolution persist)** — primary mitigation site. The test is the one called out in the arch doc §8: "StdioServerParameters.env is plaintext at spawn while the stored config retains the marker."
- **Acceptance:**
  - Marker format `__KMS_REF__<handle_id>__` is the ONLY format accepted in `mcp_servers.config.env`; non-marker values are accepted as plaintext (back-compat for non-KMS-bearing keys like `LOG_LEVEL`).
  - At spawn, `StdioServerParameters.env` (the value actually passed to the subprocess) contains plaintext; `mcp_servers.config.env` (the persisted row) contains the marker.
  - **Critical invariant:** any code path that round-trips `mcp_servers.config` (e.g. an UPDATE that re-serializes) MUST read the row fresh from the DB, NOT cache + rewrite — cached plaintext rewrites the marker to plaintext. Verify by audit of all `update_mcp_server` call sites and add an invariant test that detects this.
  - The resolver is invoked at spawn time ONLY (not at config-load, not at API list-time — `redact_secrets` at `daemon/routers/mcp_servers.py:57-111` stays presentation-only and is the explicit boundary).
  - Unit test (the load-bearing one): spawn-time `StdioServerParameters.env["OPEN_DESIGN_API_KEY"]` is plaintext; `mcp_servers.config.env["OPEN_DESIGN_API_KEY"]` is the marker. Both checked in the same test.
  - Integration test: spawn → query DB → assert marker; spawn again after restart → assert plaintext-at-spawn + marker-in-DB.

### P3-WP7 — KMS-Lite mint primitive `kms_request(service, reason) → {handle, fingerprint}`

- **Objective:** The single mint entry point for KMS-Lite. Self-hosted OD day 1 — no third-party keys, no policy layer, hard-coded sane defaults. Returns `{handle: KMS_HANDLE_<id>, fingerprint: sha256(plaintext)[:16]}`; the plaintext is held in the store and NEVER returned.
- **Touchpoints:**
  - New module `daemon/services/kms_lite.py`.
  - `daemon/sources/credentials.py:75-97` — current fail-soft fallback that returns `json.dumps(credentials)` when `_fernet is None`. **Day-1 evolution: when no key is configured, `kms_request` MUST raise `KMSUnavailableError` (WP9) — never write plaintext.**
  - New tool additions to `daemon/tools/infra.py` (e.g. `kms_request`, `kms_lookup_handle`); register under the existing `infra` category (`daemon/tools/infra.py:216, 331, ...`); spot-verify the exact registration site at implement.
  - Handle→mcp_server_id binding table — design choice: a new `kms_handle_bindings` table OR a JSONB column on `mcp_servers`. Lean toward JSONB column (`mcp_servers.instance_metadata.bound_handles`) to avoid a new migration; architecturally equivalent for day-1 scope. **Confirm at implement.**
- **Dependencies:** WP6.
- **Risks from §8:** **R2 (🔴 fail-soft CredentialManager)** — primary mitigation site; **R4 (🔴 no logging redaction)** — partial (WP10 closes it).
- **Acceptance:**
  - `kms_request("opendesign", "capability_install")` returns `{handle, fingerprint}`; fingerprint is `sha256(plaintext)[:16]` hex.
  - The plaintext is held ONLY in the encrypted store; the caller never receives it.
  - The handle format is `KMS_HANDLE_<uuid>` or whatever shape WP6 ratifies — must match the marker format in WP6.
  - Tool is registered under the `infra` tool category (home per arch doc §7.5 verified at `_tool_registry.py:552`); devops already carries `infra` in `tools.allow` (no per-agent allowlist change needed).
  - Unit tests cover: mint happy path, fingerprint determinism, plaintext-never-returned invariant, handle-format, re-mint-with-different-reason (new handle, old handle remains valid until explicitly revoked — and revocation is §7.5a deferred, so day-1 just appends).

### P3-WP8 — One-time raw-row migration: existing `mcp_servers.config.env` plaintext → KMS-marker rewrite

- **Objective:** Any `mcp_servers.config.env` row carrying plaintext secret values (per `redact_secrets` key matching at `daemon/routers/mcp_servers.py:67-86`) is migrated: mint a KMS handle for the plaintext, rewrite the row with the marker, audit-line the rewrite. Idempotent — re-running on an already-migrated row is a no-op.
- **Touchpoints:**
  - `daemon/repositories/mcp_server/models.py:17-26` (`config: dict` JSONB column).
  - Migration script — house convention places one-shot migrations under `scripts/migrations/`; confirm exact path at implement.
  - Per-row audit line: `event=migration_rewrite name=<cap> actor=<id> secret_ref=<handle> idempotency_key=<hash> trace_id=<uuid>` — same shape as the install audit line.
- **Dependencies:** WP7 (mint primitive must exist before any rewrite).
- **Risks from §8:** **R3 (🔴 DB stores env RAW today)** — primary mitigation site. **Day 1 + WP5 keep new rows marker-based; WP8 closes the pre-existing hole.**
- **Acceptance:**
  - Pre-migration: `SELECT count(*) FROM mcp_servers WHERE config->'env' ?| array[<known secret keys>]` returns N>0 (some plaintext rows exist).
  - Post-migration: same query returns 0.
  - Post-migration: `redact_secrets` output is unchanged (still redacts — proves the marker format ALSO triggers the redaction key match, OR add a marker-aware redaction entry — confirm at implement).
  - The migration is idempotent: a second run produces zero additional mint calls + zero additional audit lines.
  - Unit + integration tests cover: fresh-migrate, idempotent-rerun, mixed-rows-some-already-migrated, audit-line-shape.

### P3-WP9 — Fail-closed store evolution

- **Objective:** When `SYSTEM_ENCRYPTION_KEY` is absent, `kms_request` MUST raise `KMSUnavailableError` — never the current fail-soft plaintext fallback at `daemon/sources/credentials.py:75-97`. Callers (worker via escalation envelope) escalate with `kind=installed_but_unconfigured` and `detection_evidence=kms_key_absent`.
- **Touchpoints:**
  - `daemon/sources/credentials.py:75-97` — current `if self._fernet is None: return json.dumps(credentials)` path; day-1 evolution: raise instead.
  - New exception class `daemon/services/kms_lite.py::KMSUnavailableError` (subclass of a base that the escalation envelope recognizes).
  - Callers in WP7 must catch and re-raise as the escalation envelope's `installed_but_unconfigured` variant.
- **Dependencies:** WP7.
- **Risks from §8:** **R2 (🔴 fail-soft CredentialManager)** — closed here.
- **Acceptance:**
  - With `SYSTEM_ENCRYPTION_KEY` unset: `kms_request(...)` raises `KMSUnavailableError`; no plaintext row is ever written; no audit line is emitted that would have leaked a secret.
  - With `SYSTEM_ENCRYPTION_KEY` set (Fernet-valid): happy path unchanged.
  - Boot-time probe in WP13 (root-key hardening) asserts the key is present and at the documented file permissions; failure logs a structured error and refuses to issue (does NOT crash the daemon — KMS is opt-in for the bootstrap flow).
  - Unit tests cover: key-absent refusal, key-present happy path, malformed-key (Fernet raises on decode) refusal.

### P3-WP10 — Logging redaction policy + repo-wide enforcement test

- **Objective:** NO plaintext KMS-issued secret or `KMS_HANDLE_*` plaintext ever appears in any log line under any handler. Two-pronged: (a) install a `logging.Filter` that scrubs known markers + known plaintext prefixes; (b) audit the repo for any secret-bearing `f"..."` format strings and refactor them to use scrubbed lazy formatting.
- **Touchpoints:**
  - `daemon/__init__.py` — logger configuration root.
  - New `daemon/util/log_redaction_filter.py` — `logging.Filter` subclass that redacts `__KMS_REF__<id>__` (no-op since markers are safe) and any plaintext that matches the canonical secret-key list.
  - Repo audit — grep for `f"..*{KEY|TOKEN|SECRET}.."` style format strings touching values that could be plaintext.
- **Dependencies:** WP6, WP7.
- **Risks from §8:** **R4 (🔴 no logging redaction)** — primary mitigation site.
- **Acceptance:**
  - An integration test injects a fake plaintext secret at a known log call site and asserts the captured log record does NOT contain the plaintext substring.
  - A static check (custom linter or simple grep-based test) enumerates all `logging.*` calls under `daemon/` and flags any whose first arg is an f-string interpolating a variable whose name matches `{KEY,TOKEN,SECRET,PASSWORD}` AND whose source file is one of the KMS-touched paths. False positives are acceptable (allow-listed with a comment); false negatives are not.
  - Unit tests cover: filter on every handler, marker pass-through (markers are safe), plaintext scrub, format-string lazy evaluation safety.

### P3-WP11 — `job_continue` resume envelope `[resume]` convention ratification

- **Objective:** Ratify the resume message shape `{capability_id, status, tools_now_available, resume_from}` as the `[resume]` convention. The skill body re-runs `capability_check(capability_id)` first, then continues from `resume_from` — no state replay. `job_continue` itself is unchanged (`daemon/tools/job_queue.py:1768+`); the convention lives in the skill author / installer author.
- **Touchpoints:**
  - `daemon/tools/job_queue.py:1768+` — `job_continue(old_job_id, message)` impl (verified — no behavior change needed).
  - Resume message shape: documented in the install-opendesign skill body (WP5) AND in the worker skill body that consumes the resume.
  - Convention ratifies the `[resume]` tag as a body marker (similar to the `Result:` JSON convention in WP3) — the worker skill body parses the tag, extracts the structured fields, runs `capability_check` again, continues.
- **Dependencies:** WP5 (install skill must exist to resume into).
- **Risks from §8:** none direct.
- **Acceptance:**
  - The convention is documented in BOTH the install skill and the worker skill body (cross-reference).
  - The convention is a documented convention, not a new code path — `job_continue` itself is untouched. (Reviewer defense: this is a comment-level ratification; if a reviewer demands code-level enforcement, that is a scope expansion and gets flagged back.)
  - Unit tests cover: `capability_check` re-runs, `resume_from` honored, malformed-resume-message rejected (escalation `kind=capability_missing`).

### P3-WP12 — End-to-end autonomous cycle: OD install + KMS mint + resume (the §7 Mermaid, no human)

- **Objective:** The full §7 Mermaid sequence (lines 287-353 of the arch doc) runs end-to-end with NO human in the loop. Verification checklist in §6.
- **Touchpoints:** All WP1–WP11 integrated.
- **Dependencies:** WP1–WP11.
- **Risks from §8:** Aggregate — WP12 is the integration target; each 🔴 risk is closed by the WP that mitigates it (see the WP×Risk mapping in §4).
- **Acceptance:** see §6 (Phase Exit Criterion). The full checklist must pass.

### P3-WP13 — KMS root-key hardening OR explicit deferral (§10.1 triage verdict)

- **Objective:** Decide between hardening `SYSTEM_ENCRYPTION_KEY` (file perms, age, OS keychain) versus explicit deferral with a revisit trigger.
- **Touchpoints:**
  - If WP13a (harden): `daemon/sources/credentials.py:14-17` (`SYSTEM_ENCRYPTION_KEY_ENV`), read at boot; new `daemon/util/key_hardening.py` validates file mode + age + (optional) OS keychain probe.
  - If WP13b (defer): document the deferral in `decisions.md` (the plan-overview worker's deliverable) with a revisit trigger (e.g. "when KMS-Lite gains policy/audit/rotation, harden in the same commission").
- **Dependencies:** WP9.
- **Risks from §8:** out-of-band of the four 🔴; architecturally load-bearing for production-grade secret hygiene.
- **Triage rationale (your §10.1 verdict, mine by default unless told otherwise):**
  - **VERDICT: WP13a (lightweight hardening, no OS-keychain dependency on day 1).** Reasoning: zero vault infra exists; OS-keychain integration is platform-specific (macOS Keychain ≠ Linux libsecret ≠ Windows DPAPI) and would explode the day-1 surface; a lightweight file-perm + age check covers the realistic threat model (unattended daemon, world-readable key file); revisit when KMS-Lite gains policy/rotation (§7.5a) — that's the moment OS-keychain becomes load-bearing.
  - **WP13a scope (compact):** at boot, if `SYSTEM_ENCRYPTION_KEY` is set inline OR sourced from a file (`SYSTEM_ENCRYPTION_KEY_FILE`), assert file mode ≤ `0o600` and mtime < N days (default 90, configurable); refuse to mint on violation with a clear structured error. No OS-keychain integration day 1.
- **Acceptance (WP13a):**
  - A test that writes the key to a `0o644` file, runs the boot probe, asserts refusal + clear log line.
  - A test that writes the key to a `0o600` file with old mtime, asserts refusal (configurable threshold).
  - A test that passes both, asserts the mint path proceeds.
  - Documentation in `docs/operations/kms-lite-hardening.md` (new file; confirm naming convention at implement).

---

## 4. §8 Risks × WP Mapping (the four 🔴 MUST each map to a WP + acceptance test)

| §8 Risk | Severity | Primary WP | Secondary WPs | Acceptance test surface |
|---|---|---|---|---|
| **R1** — KMS plaintext leak via post-resolution persist | 🔴 | **WP6** (marker substitution in-RAM only; cached rewrites banned) | WP5 (writes markers, not plaintext), WP10 (logging scrub) | WP6 acceptance: spawn-time plaintext vs. DB-row marker; round-trip-update-must-re-read test |
| **R2** — Fail-soft `CredentialManager` (no key ⇒ plaintext fallback) | 🔴 | **WP9** (fail-closed evolution) | WP7 (mint primitive never sees plaintext path) | WP9 acceptance: key-absent refusal + no plaintext write + no audit leak |
| **R3** — MCP config stores env RAW today (`redact_secrets` presentation-only) | 🔴 | **WP8** (one-time raw-row migration) | WP5 (new installs are marker-based day 1), WP6 (substitution invariant) | WP8 acceptance: pre-count vs. post-count of plaintext env rows = 0 |
| **R4** — Zero logging redaction repo-wide | 🔴 | **WP10** (logging filter + format-string audit) | WP6 (markers are safe in logs by construction) | WP10 acceptance: integration test with injected plaintext; static grep audit |
| R5 — `POST /agents` permissive-default trap | 🔴 | NOT in P3 scope | P1 owns `agents/designer/` directory creation | P1 acceptance (cross-ref): agent dir created by hand, not via API |
| **R6** — allowed_models silent fallback (`vision` missing) | 🔴 | NOT in P3 scope | P1 owns the global `allowed_models` entry + restart | P1 acceptance (cross-ref): `vision` present in `config.yaml:82` / `OPENAI_SELECTABLE_MODELS` |

> **Hard rule:** any acceptance test for a 🔴 risk that fails blocks phase exit (see §6). P3 owns R1, R2, R3, R4 directly; R5 and R6 are owned by P1 and are listed here for traceability — phase exit requires their acceptance tests to be green (cross-phase contract).

---

## 5. Coupling Map (P3 × rest of plan)

| | P1 Foundations | P2 Parallel builds | P3 Bootstrap+KMS-Lite (this phase) | P4 Overview+Decisions (later) |
|---|---|---|---|---|
| **P1** | — | tight (shared `designer` agent dir + model wiring + tmp_images substrate) | tight (P3 reads P1's contracts: `designer` lives, `vision` in allowlist, sub-team-lead wiring, MCP infra live) | independent |
| **P2** | tight | — | tight (P3 reads P2's contracts: OD builtin class live, OD installed via early manual lane, capture adopt-or-build verdict exists) | independent |
| **P3 (this)** | tight (consumes contracts) | tight (consumes contracts) | — | independent (P4 reads P3's WP×Risk map + Exit Criterion) |
| **P4** | independent | independent | independent | — |

**Cross-phase invariants that P3 LEAVES BEHIND (the reusable surface):**
1. `requires:` front-matter + `capability_check` + escalation envelope — reusable by any agent's skill in any project.
2. KMS-Lite mint-only + `__KMS_REF__` markers + fail-closed store — reusable by any future KMS-issued credential (not just OD).
3. The installer skill pattern (`install-<capability>`) — first proven user is OD; future installers reuse the skeleton + `capabilities.yaml` schema.

---

## 6. Phase Exit Criterion (machine-checkable end-to-end)

The phase exits green when ALL of the following are provably true (each row has a verification method):

| # | Check | Verification method | Pass threshold |
|---|---|---|---|
| 1 | First fully-autonomous self-install + mint + resume cycle (the §7 Mermaid, no human) | Integration test: worker skill body → escalation envelope → designer spawns installer → installer calls `/configure-builtin` → worker resumes via `job_continue` → mcp_opendesign tools live | All 6 swimlane steps complete in one test run; no human invocation; final state: worker holds only `{handle, fingerprint}`; mcp_opendesign tool list populated |
| 2 | Audit lines present for `mcp_install` AND `kms_issue` | Assert `install-audit.jsonl` contains lines with `event=mcp_install` and `event=kms_issue` from the same test run | Both event types present; one line each; correct field set per §7.4 |
| 3 | Stored config contains markers ONLY (no plaintext) | After test run: `SELECT config->'env'->>'OPEN_DESIGN_API_KEY' FROM mcp_servers WHERE name='opendesign'` | Value starts with `__KMS_REF__`; does NOT match the plaintext format |
| 4 | Fail-closed proof: absent key ⇒ refusal, no plaintext fallback | Test: unset `SYSTEM_ENCRYPTION_KEY`, run mint, assert `KMSUnavailableError` raised; assert no plaintext row written; assert no plaintext in any log line of the test run | Exception raised; DB unchanged; log scan clean |
| 5 | Plaintext-never-in-context proof: checkpoint / log scan | Scan the test run's checkpoint + log files for the plaintext substring | Zero matches in checkpoints; zero matches in logs |
| 6 | All WP1–WP11 acceptance tests green | Run unit + integration suites | 100% pass; no skips |
| 7 | WP13 (root-key hardening) decided | Either WP13a landed (lightweight hardening) OR WP13b ratified in `decisions.md` with revisit trigger | One or the other, documented |
| 8 | Cross-phase contract compliance: R5 + R6 from P1 are green | Read P1's exit criterion; assert `agents/designer/` exists + `vision` in `config.yaml:82` | Both green (cross-phase read-only check) |

**Verification method recap:** items 1–5 are the §7 Mermaid walkthrough; item 6 is the unit/integration suite; items 7–8 are document reads.

---

## 7. Cross-Cutting — Spec front-matter lint coherence

| Front-matter key | Day-1 enforcement | Where enforced |
|---|---|---|
| `pinned_spec_sha` | **HARD RULE** (D6) — every conformance verdict references it; absent ⇒ lint failure | Conformance review verifier (P1 owns) |
| `requires:` (new) | **FAIL-FAST PRE-FLIGHT** (capability-side) — `capability_check` first instruction; miss ⇒ escalation | WP2 (capability resolver) |
| All other skill front-matter | advisory / lint warning only | Existing skill loader |

**The distinction this phase draws explicit:** D6's "one hard rule" principle applies to SPEC front-matter (the design-spec.md hard rule). The `requires:` block is a CAPABILITY front-matter (skill-side) and is enforced by capability-side pre-flight (`capability_check`), not by spec lint. The two never collide: a skill body can declare `requires:` AND have its parent's design-spec carry `pinned_spec_sha`; the lint stack does not need to know about `requires:` because the enforcement lives at skill-load time, not at spec-conformance-verdict time.

**Lint philosophy statement (for the lint maintainer / P1 conformance reviewer):** "Spec front-matter" = the front-matter of `planning/{feature}/design-spec.md` (designer-owned, conformance-verdict-bearing). "Skill front-matter" = the `requires:` block of `skill-set.yaml` entries. They are different surfaces with different enforcement lives. D6's `pinned_spec_sha` hard rule does not extend to `requires:` — and adding a second spec-side hard rule would silently dilute D6's "ONE hard rule" verdict.

---

## 8. Deferred — explicitly NOT planned here (§7.5a from arch doc + related)

These appear in `decisions.md` (the P4 worker's deliverable) as a single "Deferred (from §7.5a, planned at later-from-usage)" section so future commissions can pick them up. They are listed here ONLY for traceability — no WP below plans them:

- Policy store (service allowlist, budget caps, TTL/scoping) and `policy_denied` activation.
- Third-party OD Cloud brokering under one-time human policy approval (`ensemble policy set …`).
- `kms_rotate` / `kms_revoke` / `kms_audit` tool surfaces.
- SSE secret/credential event kinds (no `SseEvent.SECRET_*` exists today).
- Full audit atomicity template (atomic temp+`mv` + torn-write scan per upgrade-journal template).
- DEK-wrap key-rotation story (current day-1 uses a single Fernet master; rotation = re-encrypt every row).
- The `policy_denied` envelope kind firing — schema exists in WP3 but cannot fire from day-1 code (lint-enforced).
- `install-audit.jsonl` growth path beyond one-line-per-issue (actor-stamped diffs; SHA-256 tamper-evidence).
- Future installer skills beyond OD (`install-notion`, `install-figma`, etc.) — only the PATTERN is generalizable from WP5; the individual skills are out of scope.
- Designer-initiated audit cadence without a daemon cron (architectural OQ — owned by the user / leader workflow decisions).

---

## 9. Open Questions YOU OWN — Triage (per dispatcher spec)

| # | Open Question | Triage verdict | Rationale (1 line) |
|---|---|---|---|
| 1 | KMS-Lite root-key custody on live host (`SYSTEM_ENCRYPTION_KEY` hardening: file perms, age, OS keychain) | **WP13a (lightweight file hardening) — landing WP** | Vault infra doesn't exist; OS-keychain integration is platform-specific and explodes day-1 surface; lightweight file-perm + age check covers the realistic threat model; revisit when KMS-Lite gains policy/rotation (§7.5a) |
| 2 | One-time migration design for raw secrets already in `mcp_servers.config.env` | **WP8 (landing WP)** | Architecturally load-bearing for R3 closure; cannot be deferred because day-1 installs are marker-based but pre-existing rows stay raw without it (silent plaintext persistence); idempotency + audit-line shape are the non-obvious correctness requirements |
| 3 | `job_continue` resume-message envelope — ratify `[resume]` shape as convention | **WP11 (landing WP)** | The resume primitive already exists (`job_continue` is unchanged); the convention ratification is comment-level + cross-skill-body documentation, no code change; deferring leaves install skills writing ad-hoc resume shapes and breaks the bootstrap loop's predictability |

---

## 10. Open Questions YOU DO NOT OWN (flagged for awareness)

- OD `agent-browser` skill adoption verification (gates capture-tool build — P2's responsibility; this plan assumes the verdict artifact exists by the time P3 ships).
- Path→data-URI bridge design against tmp_images deployment facts (P1's responsibility; P3 only consumes the bridge).
- `send_message` `images` param (GAP-3) — P1 owned.
- Designer-initiated audit cadence (no daemon cron) — user / leader workflow.
- Comparator A→C consolidation triggers — kept on record, not day-1.

---

## 11. File & Path Conventions (verify at implement)

| Path | Purpose | Convention source |
|---|---|---|
| `agents/dynamic-skill/capabilities.yaml` | Capability registry | Arch doc §7.3 — co-located with dynamic-skill |
| `agents/worker/skills/install-opendesign.md` | First installer skill | Worker = the operational install-execution agent (confirm at implement) |
| `daemon/services/capability_resolver.py` | `requires:` parser + `capability_check` | New file — convention under `daemon/services/` |
| `daemon/services/kms_resolver.py` | `__KMS_REF__` marker substitution at MCP spawn | New file |
| `daemon/services/kms_lite.py` | `kms_request` mint primitive + `KMSUnavailableError` | New file |
| `daemon/util/log_redaction_filter.py` | Logging scrub filter | New file — convention under `daemon/util/` |
| `daemon/util/key_hardening.py` | WP13a file-perm + age check | New file |
| `scripts/migrations/kms_lite_raw_row_migrate.py` | WP8 migration script | House convention (`scripts/migrations/`) — confirm at implement |
| `docs/operations/kms-lite-hardening.md` | WP13a operator doc | New file — house convention (`docs/operations/`) — confirm at implement |
| `planning/{feature}/install-audit.jsonl` | Per-feature audit log | Arch doc §7.4 |

**Spot-verification at implement (read-only) is REQUIRED for:**
- Skill-loader exact path (likely `daemon/services/skill_store_service.py` — confirm before WP1).
- `mcp_servers.instance_metadata` column existence (WP5 idempotency_key + WP8 bound_handles both write here — confirm schema at implement; if absent, add via the column-ensure path per house migration convention).
- `daemon/mcp/builtin_servers/` resolution site for `builtin_mcp_class` strings (WP4 cross-reference — confirm `get_registry().get_by_name(...)` is the canonical resolution).
- `daemon/mcp/config.py:260` (HTTP/SSE headers treatment — confirm exact line at implement; arch doc cites `:260` but the exact range depends on the source layout).

---

## 12. Rollout & Verification (one-shot story)

The single load-bearing rollout is the **end-to-end autonomous cycle** from §6 row 1. Each WP above is individually provable via its acceptance test; the phase exit criterion is the integration of all WP tests into one Mermaid walkthrough.

**Rollout sequence (per WP, not per commit — implementation ordering is for the developer, not this plan):**
1. WP1 + WP4 + WP11 (data-shape and convention — no behavior)
2. WP2 (capability_check reads the shape)
3. WP3 (escalation envelope writes the shape)
4. WP6 (marker substitution at MCP spawn — the load-bearing invariant)
5. WP7 (mint primitive)
6. WP9 (fail-closed evolution — closes R2)
7. WP10 (logging redaction — closes R4)
8. WP5 (install-opendesign skill — depends on the above)
9. WP8 (raw-row migration — depends on WP7)
10. WP13 (root-key hardening — depends on WP9)
11. WP12 (e2e test — integrates all)

> Cross-phase dependencies from P1 and P2 must be green before this sequence starts (the dispatcher enforces this — see "P3 MAY ASSUME" in §0).

**Verification artifacts (each WP produces a `docs/verification/{wp-id}.md` per house convention — confirm at implement):**
- Per-WP acceptance tests green
- §6 Phase Exit Criterion checklist 100% green
- `decisions.md` (P4 worker's deliverable) reads this WP×Risk map and references it

---

## 13. Risks Carried Into Phase Execution

(Phase-internal risks on top of the arch doc §8 four 🔴 which are already mapped in §4.)

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| PR1 | WP13a scope creep — operators expect OS-keychain integration; we ship only file hardening | Medium | Medium | Document the deferral in `docs/operations/kms-lite-hardening.md`; flag in `decisions.md` with revisit trigger |
| PR2 | `install-opendesign` skill location ambiguity (designer vs worker vs a new "installer" agent) | Low | Medium | Spot-verify at implement; default to `agents/worker/skills/` because install is operational work; if reviewer disputes, escalate to user |
| PR3 | Marker format `__KMS_REF__<handle_id>__` collides with legitimate env values | Low | Low | Lint test asserts the marker prefix is reserved; legitimate values starting with `__KMS_REF__` are practically nonexistent |
| PR4 | The bootstrap loop's self-reference (`install-opendesign` requires `mcp: [opendesign-installed]` which it itself installs) is hard to test deterministically | Medium | Medium | Test in two halves: (a) install with explicit gate ("operator simulates missing-OD state"), (b) install with OD already present (idempotency check) |
| PR5 | `job_continue` resume without state replay means the worker has to re-derive "where was I" from the resume envelope — fragile if the envelope shape drifts | Medium | Low | WP11 convention ratification includes a "resume envelope is the SOLE source of truth for `resume_from`" statement; reviewer defense against future drift |
| PR6 | WP8 migration script touches existing `mcp_servers.config` rows in-place — if interrupted mid-run, partial state | High | Low | Atomic per-row: rewrite config inside a transaction with `BEGIN/COMMIT`; on error, the row is untouched; per-row audit + idempotency ensure re-run is safe |
| PR7 | Cross-phase contract verification at §6 row 8 (R5 + R6 from P1) is read-only at phase exit but if P1 hasn't shipped, P3 cannot proceed | Medium | Medium | Dispatcher enforces the cross-phase "MAY ASSUME" list in §0; P3 worker verifies at WP12 entry |
| PR8 | `install-audit.jsonl` grows per feature — no global retention class | Low | Low | Day-1 acceptable; document the deferral; revisit when §7.5a audit atomicity lands |
