# Plan Overview: Maintenancer Agent

Date: 2026-09-08 (fold-pass v2: 2026-09-08)
Author: planner via plan-creation worker (fold-pass v2: architect adjudication folded)
Status: Draft (fold-pass v2 — architect adjudication folded; see `architecture-recommendation.md` for the authoritative adjudication layer)

> **Authoritative adjudication layer:** `.agents/shared/planning/maintenancer-agent/architecture-recommendation.md`. Where this document and the architect's file disagree, the architect's verdict governs.

## Fold-Pass Delta (v1 → v2)

Material changes folded from `architecture-recommendation.md`:

1. **De-scope list (Scope + Phase 2.7):** added `agents/developer[v2]/meta.json` — versioned-meta resolution (`instance.py:4539-4541`) prefers `get_version(...) or get_resolved(...)`; de-scoping base developer only leaves `developer[v2]` instances holding logs (architect §4.3). Success criterion #3 extended to enumerate base + versioned metas.
2. **Phase sequencing (Phase 1.10 + Rollout Notes):** DROPPED the in-phase standalone restart. Restructured to **3 waves** (architect §2.3): W1 (P1∥P2∥P3, file-disjoint, **P1-before-P2 merge-order invariant**), W2 (P4), W3 (P5 + **ONE** restart window). Phase structure (P1–P5) preserved — waves govern execution + restart ordering.
3. **`kb-curator` skill (Phase 3.7):** REMOVED "startup recording via experience()" capability — architecturally impossible (skills are static prompt text; no startup hook). Reframed to **tiered design** (architect §6.1+§6.2): (i) KB INDEX in `memory.md` as load-bearing primary (task 1.7 expanded to host it); (ii) filesystem reads on demand as content path; (iii) RAG via first-turn `experience()` duty in `workflow.md` (best-effort, requires `knowledge` in `tools.allow`). Full skill-content inclusion rejected (12k chars ≈ 3k tokens/turn).
4. **OPEN A → Option 1-refined** (Phase 2.8 + Risk R1): grant `system_upgrade` ONLY (NEVER `system_restart` which refuses live at `upgrade_tools.py:1459-1465`); same 3-factor gate; gate is instance-bound and satisfiable via in-thread user nonce echo (`upgrade_tools.py:1870-1886`). Cardinal: relay the nonce verbatim, never fabricate/echo it agent-side.
5. **OPEN B → Option 1** (Phase 4.5 + Risk R5): keep worker `system-log`; formalize as designated break-glass (architect §4.5, §7.1); workers NEVER hold `ens-db` (DB write path closed at the worker layer; pin in 2.10 exclusivity test).
6. **OPEN C → Option 4′** (Phase 2.1 + Risk R2/R3): shared-engine SELECT + dedicated 2+3 repair pool (`pool_pre_ping=True`, `pool_recycle=3600`, connect_args `statement_timeout=60s`, `lock_timeout=10s`, `idle_in_transaction_session_timeout=60s`); per-tx `SET LOCAL statement_timeout` (NOT engine-wide connect_args, NOT synthetic ConnectionPoolManager registration — architect §3.1 exclusivity leak); audit `INSERT` in SAME transaction, fail-closed; idempotent `DO$$`-only; self-surgery refusal; repository-methods-first rule + dry-run warning.
7. **OPEN D → Option 1** (Risk R4): accept watcher strip (audit re-verified clean — architect §4.6).
8. **OPEN E → Modified Option 4 tiered** (Phase 3.7 + Risk R6): see #3 above. `knowledge` added to `tools.allow`. `last-verified-against` pinned to release tag (architect §6.3 — not rolling SHA; this repo merges 20+/day).
9. **Dual-pin updates** (Phase 2.6 + Risk R9): `tests/unit/tools/test_upgrade_registration.py:100` + `tests/unit/tools/test_attestation_registration.py:154` both assert `PRIVILEGED_TOOL_CATEGORIES == frozenset({"system_upgrade"})` — update both in the same PR (deliberate silent-additions-visible pins; architect §4.4).
10. **Rollback completeness** (Rollout Notes): frozenset / CATEGORY_MODULES / DYNAMIC_TOOL_NAMES / KNOWN_TOOL_NAMES / de-scopes / create_ens_db_tools wiring / dual-pin reverts. **Snapshot-before-repair** for executed repairs (architect §2.4) — recovery recipe via idempotent `DO$$` forward-fix from `repair_log.created_by`.
11. **New risks added** (R12–R17): kb-curator feasibility defect (resolved); ConnectionPoolManager registration hazard; repair DML partial application; PG role privilege gap; maintenance-worker split drift; lock_timeout / pool_recycle empirical verification.
12. **Autonomy ladder** (Research Insights + Phase 1 hooks): explicit 3-tier per architect §5.2 (autonomous / leader-confirmed / human-only) + live-rung gate boundary (§5.4).
13. **Pending items** (Open Questions): maintenance-worker trigger threshold; PG role privilege (Wave-1 operator); lock_timeout post-soak re-tune; pool_recycle × pre_ping × reset-on-return empirical verify.

---

## Objective

Introduce a centralized **maintenancer** agent that investigates, repairs, restarts, and upgrades the ensemble daemon — and gives advisory consultations on ensemble bug-fixing — by (a) consolidating log forensics and direct `ensemble_prod` DB access into one role, (b) carrying its own local KB of ensemble architecture and runbooks, (c) dispatching v2-style workers for bounded investigation/repair tasks, and (d) being on the leader's team so ensemble-project work can route to it.

**Outcome sentence (testable):** when an ensemble bug or operational anomaly surfaces, `leader` can dispatch **maintenancer** directly and trust that it has the tools (`system-log`, `ens_db_*`, advisory preparation), the context (local KB), and the discipline (v2 worker dispatch) to investigate, propose, and (per the autonomy decision) execute within the gate.

---

## Scope

### In Scope

- New `agents/maintenancer/` directory with v2-pattern prompt files (per `docs/agent-prompt-writing-guide.md` Appendix file quick-ref).
- **Registration:** leader `team_members += "maintenancer"`; daemon restart required because `_registry` is an import-time singleton (`daemon/registry.py:1170-1175`).
- **New tool family** `ens_db_*` (`ens_db_postgres_select`, `ens_db_inspect`, `ens_db_repair_execute` [gated], `ens_db_pool_status`) — direct access to the daemon's CURRENT shared engine (`InstanceManager.engine`, `daemon/manager.py:2043-2049`).
- **Promote** `system-log` **and** `ens-db` **to privileged categories** — add to `PRIVILEGED_TOOL_CATEGORIES` (`daemon/tools/_tool_registry.py:106-108`); enforcement at `_strip_privileged_category_tools` (`daemon/tools/instance.py:4497-4514`).
- **De-scope** `system-log` from `agents/developer/meta.json`, **`agents/developer[v2]/meta.json`** (versioned-meta resolution per `instance.py:4539-4541` prefers `get_version(...) or get_resolved(...)`; de-scoping base developer only leaves `developer[v2]` instances holding logs — see architect §4.3), and `agents/wanderer/meta.json` `tools.allow` lists.
- **Local KB doc set** at `agents/maintenancer/knowledge/*.md` + a `kb-curator` skill wired via the **tiered design** (see MADE D4 / OPEN E adjudicated): KB INDEX in `memory.md` (load-bearing primary) + filesystem full-text reads on demand (content path) + RAG via first-turn `experience()` duty in `workflow.md` (best-effort, requires `knowledge` in `tools.allow`). Full skill-content inclusion of all 6 docs (~12k chars ≈ 3k tokens/turn) is REJECTED.
- **v2 worker dispatch:** `team_members` per OPEN B resolution (recommendation: `["explorer", "worker", "coder"]`).
- **Skills:** `log-forensics`, `job-mission-repair`, `ens-db-repair`, `restart-upgrade-ops`, `bug-advisory`, `health-check`, `kb-curator` (final names pending Phase 4 walkthrough).
- **Pin tests:** registration pin (modeled on `tests/unit/test_project_manager_agent.py:275-281`), frozen-name regen (`tests/unit/tools/test_frozen_tool_name_discovery.py`), system-log exclusivity tests, `ens_db_*` SELECT-only enforcement tests, repair-flow integration tests, report-sanity scrutiny (modeled on `tests/unit/test_report_integrity_prompts.py`).

### Out of Scope

- **Implementation of any tool body or prompt text** in this plan artifact. (Lists the files; doesn't write them.)
- **gaia changes** — gaia is the user-facing environment-setup assistant; user explicitly decided: new agent, gaia untouched.
- **Ari promotion** — ari remains the privileged `system_upgrade` user with the 3-factor gate (`daemon/tools/upgrade_tools.py:2009-2036`). Maintenancer's relationship with restart/upgrade is decided per OPEN A.
- **Live (prod) execution of restart/upgrade by maintenancer without human-in-the-loop** — explicitly gated by OPEN A.
- **Modifying `system-log` tool bodies** — only the registry entry and the allow-list membership change.
- **De-scoping `db` (external connection) category from anyone** — `ens-db` is a separate, privileged category; existing `db` tools remain available to whoever currently holds them.
- **Removing `system-log` from `worker` agent** — explicitly listed in OPEN B; current default keeps worker with `system-log` until B is decided.
- **Watcher re-scoping beyond the automatic R-SR16 side-effect** — when `system-log` becomes privileged, watcher silently loses it (empty-allow default-open hole closes); by design (OPEN D).
- **Authoring migrations via `ens_db_repair_execute`** — the migrations runner is SQLite-only by design (`runner.py:486-491`); PG schema uses `create_all + _ensure_postgres_columns`. Repairs are idempotent `DO$$` recipes only.

---

## Phases

| Phase | Name | Objective | Tasks | Coupling | Status |
|-------|------|-----------|-------|----------|--------|
| 1 | Agent scaffolding + registration | Author v2 prompt files; add leader `team_members`; restart to register; pin test | 9 | independent | pending |
| 2 | Tool layer + exclusivity | Add `ens-db` category + tools; promote `system-log` + `ens-db` to privileged; de-scope from developer/wanderer | 10 | tight (P1, P3, P4) | pending |
| 3 | Local KB authoring | Author `agents/maintenancer/knowledge/*.md` doc set; wire `kb-curator` skill | 8 | loose (P2, P4) | pending |
| 4 | Skills + workers | Define 6–7 skills; finalize `team_members`; pin dispatcher tests | 7 | tight (P1, P3) | pending |
| 5 | Docs + prompt-guide compliance + rollout | README + §10 checklist + leader routing line + integration test + rollout runbook | 5 | tight (P1, P2, P3, P4) | pending |

---

## Coupling Map

|       | P1  | P2      | P3  | P4      | P5  |
|-------|-----|---------|-----|---------|-----|
| **P1**| —   | tight   | indep | tight  | tight |
| **P2**| tight | —     | loose | loose  | tight |
| **P3**| indep | loose | —   | tight  | loose |
| **P4**| tight | loose | tight | —     | loose |
| **P5**| tight | tight | loose | loose  | —   |

**Tight (shared contracts/data):**
- P1 ↔ P2: `meta.json` `tools.allow` must list the new categories (P2) at scaffolding time (P1).
- P1 ↔ P4: `team_members` declared in P1 is the dispatcher contract used in P4.
- P2 ↔ P5: rollout runbook calls out the privileged-category promotion.
- P3 ↔ P4: skills reference KB docs by section name (convention v2 cross-refs).
- P1 ↔ P5: README + §10 checklist cover every prompt file authored in P1.

**Loose (shared domain, independent files):**
- P2 ↔ P3: tool descriptions may surface KB pointers.
- P2 ↔ P4: workers use `ens_db_*` (skill bodies reference the tool names).
- P5 ↔ P3, P4: README references KB index + skill list.

**Independent:** P1 ↔ P3 (scaffolding precedes KB authoring; no contract between them).

---

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | Maintenancer runs live `system_restart` / `system_upgrade` without human gate | High | Medium | Locked behind OPEN A; recommended default: share the privileged category but enforce the same 3-factor gate ari uses (`upgrade_tools.py:2009-2036`). Cardinal rule in `rule.md`. Pin with an integration test that asserts the gate refuses requests missing the nonce. |
| 2 | `ens_db_*` write tools corrupt prod data (un-audited DDL/DML) | High | Medium | Two-tool split (MADE D6): `ens_db_postgres_select` is SELECT-only via `_validate_select_only` (`db_tools.py:97`); `ens_db_repair_execute` is a separate tool with three gates — explicit confirmation, audit stamp (`created_by=current_instance_id` per `infra.py:226-234`), dry-run-first toggle. Pin with rollback-on-failure integration test. |
| 3 | `ens_db_*` exhausts the shared sync engine pool (5+10, `manager.py:448-451`) → slows repositories | Medium | Medium | OPEN C; recommended default: dedicated small pool (2+3) for the repair writer; shared engine for read-heavy SELECT. Pin with a pool-saturate test that asserts repo-side latency stays bounded while `ens_db_*` is hammered. |
| 4 | `system-log` promotion strips watcher silently (empty-allow default-open hole closes) | Low | High | By design — watcher should NOT have `system-log` access without explicit allow. OPEN D; recommended default: accept the change (architect §4.6 audit re-verified clean — zero `system-log`/`ens_system_log` references across `agents/watcher/` (all 6 files) and `daemon/services/watchover_service.py`). Record the one-line commit comment. |
| 5 | Worker agent keeps `system-log` → designated break-glass (architect §4.5, §7.1) | Low | High | Formalize worker as break-glass in the rollout runbook: NEVER strip without replacement; leader re-spawns on maintenancer ERROR (revive semantics re-register watchers); re-grant = meta allow + restart via pause-first runbook; raw-bash fallback documented WITH the caveat that raw reads bypass `ens_system_log_*` redaction. **Workers NEVER hold `ens-db`** — DB write path closed at the worker layer; pin in the 2.10 exclusivity test. Revisit `maintenance-worker` split on usage evidence (pending trigger threshold). |
| 6 | Local KB doc set grows stale; maintenancer cites wrong architecture | Medium | High | **Tiered KB design (architect §6.2):** (i) **Primary load-bearing** — KB INDEX embedded in `memory.md` (one-line-per-doc triggers + verification discipline, ~<1k chars, guaranteed-on via the loader); (ii) **Content path** — `agents/maintenancer/knowledge/*.md` full text via `read_file` on demand (~1-2k tokens per retrieval, exact + current); (iii) **Best-effort** — RAG mirror via first-turn `experience()` duty in `workflow.md` (+ `knowledge` in allow). **Full skill-content inclusion of all 6 docs (~12k chars ≈ 3k tokens/turn) is REJECTED** — 15-25% of a typical system prompt, compounding toward the 700k window / 80% compaction rung. **Pin `last-verified-against` to a RELEASE TAG** (immutable), not a rolling SHA — this repo merges 20+/day; rolling pin would warn perpetually. (i)+(ii) are guaranteed; (iii) is probabilistic, pinned by first-turn workflow rule + grep test. |
| 7 | Registry singleton (`registry.py:1170-1175`) doesn't pick up `agents/maintenancer/` → silent spawn failures | High | Low | Daemon restart is mandatory; documented in rollout runbook. Spawn deny-by-default (`instance.py:1817-1848`) ensures stale-registry spawn fails LOUDLY (good — not silent). |
| 8 | Prompt-guide violations in authored files (system internals leaked, parallel copies, >7 cardinals, path tokens in prose) | Medium | High | Pre-commit checklist from `docs/agent-prompt-writing-guide.md` §10 enforced by reviewer on every P1/P5 PR. Pin with `tests/unit/test_maintenancer_prompt_compliance.py` that greps for forbidden tokens (`meta.json`, `daemon/`, `_tool_registry`, `tools.allow`, `skill-set.yaml`, `seed_all`, `innate_skills`, `default_agent_versions`) — must return zero hits in prompt prose. |
| 9 | Privileged-category change breaks other agents whose allow-lists previously worked — AND breaks two exact-equality privileged pins | Medium | High | After promotion: any agent whose `tools.allow` listed individual system-log tool NAMES (not the category) is unaffected (allow resolves individual tool names). Pin: integration test asserting resolved tool sets for developer/wanderer/worker/watcher after the change. **PLUS (architect §4.4) two exact-equality pins will BREAK by design and MUST be consciously updated in the same PR:** `tests/unit/tools/test_upgrade_registration.py:100` and `tests/unit/tools/test_attestation_registration.py:154` both assert `PRIVILEGED_TOOL_CATEGORIES == frozenset({"system_upgrade"})` — these are deliberate "silent additions visible" pins; updating them is the mechanism working as designed, not test debt. Pin test in task 2.10 enumerates base AND versioned metas (standing guard for future `developer[v3]`-class additions). |
| 10 | Migration-runner trap (SQLite-only by design, `runner.py:486-491`) bites when maintenancer authors a migration | High | Medium | KB doc captures the trap (`04-known-traps.md`); `ens-db-repair` skill enforces idempotent `DO$$` recipes only (`manager.py:4996-5012` is the canonical example); repair tool refuses anything resembling a migration file. |
| 11 | Pin test for `KNOWN_TOOL_NAMES` regen fails because the four new tool names were not added to `_tool_registry.DYNAMIC_TOOL_NAMES` | Low | High | The full 10-step registration checklist (`upgrade_tools.py:110-142`) is mirrored in Phase 2 tasks; the regen step is task 2.5 and its acceptance is the drift test passing. |
| 12 | `kb-curator` skill content blows the prompt token budget when concatenated (loader.py:407-413) — and the "startup recording" path was architecturally impossible | Low → resolved | Medium | Resolved by architect §6.1+§6.2. **The original "startup recording via experience()" capability is REMOVED** — skills are static prompt text; neither delivery path (static prompt sections via `load_agent_skills`; skill-bank auto_load per-turn injection) executes code. Reframed to tiered design (see R6). KB index lives in `memory.md` (~<1k chars, load-bearing); full content via filesystem reads on demand; RAG via first-turn workflow duty. |
| 13 | Synthetic `ensemble_prod` registration in `ConnectionPoolManager` (architect §3.1) — would bypass the `ens-db` exclusivity this plan exists to build | High | Medium | `db_postgres_dml_select` resolves by `connection_name` against user-facing registry and is in the `db` category (NOT privileged). A synthetic registration would make the daemon's own DB SELECT-able by every `db`-category holder. **MUST NOT be registered in ConnectionPoolManager.** Read path stays on the shared engine with per-tx `SET LOCAL statement_timeout` (NOT engine-wide connect_args, which would cap every repository operation). Belt: server-side timeout is the real cap (thread-bridge cancels the awaitable but does NOT abort the blocking psycopg call). |
| 14 | Repair DML partial application — mid-repair pause does NOT roll back executed DML | High | Medium | Pause of another instance is HTTP/operator-only (`routers/instances.py:647-669`); no tool wraps `pause_instance_cascade`. A mid-repair pause cancels the graph task at a node boundary but does NOT roll back executed DML. **Idempotent `DO$$`-only rule is load-bearing** (R10) and partial application must be reconstructible from `repair_log`: record shape includes `before_snapshot (JSONB)`, `after_snapshot (JSONB)`, `outcome ('previewed'|'committed'|'rolled_back'|'error')`, `target_table`, `nonce`. Dry-run-then-confirm gate (single-use nonce + 5-min TTL + action-bound) prevents mid-flight cancellation from being a recovery scenario. |
| 15 | PG role lacks CREATE/ALTER/INSERT/UPDATE/DELETE for repairs | High | Medium | **Wave-1 operator checklist item (architect §7.6):** verify the daemon's PG role holds CREATE/ALTER on `ensemble_prod` BEFORE enabling repair DDL. If not, `ens_db_repair_execute` must refuse with a clear error (never silently fail). Re-verified at every PG-role change. |
| 16 | Maintenance-worker split drift — break-glass assumption unverified at scale | Low | Medium | Decision OPEN B Option 1 keeps worker as break-glass. Trigger threshold for revisiting the split (creating `maintenance-worker`) is PENDING (architect §9): define the usage-evidence threshold (e.g. N log-touching worker dispatches/month by non-maintenancer dispatchers) that would justify the split. Until then, the plan stands on `team_members: ["explorer", "worker", "coder"]`. |
| 17 | `lock_timeout` / `pool_recycle` × `pool_pre_ping` × `reset-on-return` interaction on the repair pool (architect §3.1, §10) | Medium | Medium | Initial proposal: `lock_timeout=10s`, `pool_recycle=3600`, `pool_pre_ping=True`. **Post-soak re-tune required** for `lock_timeout` against prod lock-wait distribution; **empirical verify `pool_recycle × pool_pre_ping × reset-on-return` interaction on a disposable PG** (SQLAlchemy 2.x documented-correct, not yet verified here). Both are OPEN; pin the initial values; the soak verifies. |

---

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| 1 | Leader can dispatch to `maintenancer` | `agents/leader/meta.json` `team_members` contains `"maintenancer"`; spawn returns no deny-by-default error from `instance.py:1817-1848` | spawn succeeds in integration test |
| 2 | Maintenancer has the new tools | `ens_db_postgres_select` + `system-log` tools resolved in `resolve_tool_filter` for maintenancer only | frozen-name regen test + a per-agent resolution test (maintenancer has both; developer has neither; worker per OPEN B) |
| 3 | Developer + developer[v2] + wanderer have lost `system-log` | Their resolved tool set (base + versioned metas) no longer contains any `ens_system_log_*` tool name | integration test: assert tool names absent for developer base + developer[v2] + wanderer |
| 4 | `ens_db_postgres_select` blocks non-SELECT | Every non-SELECT query shape (INSERT / UPDATE / DELETE / DDL) raises `SelectOnlyViolation` | unit test per shape (mirror `tests/unit/tools/test_db_tools.py:497`) |
| 5 | Restart is required for activation | A daemon started before `agents/maintenancer/` exists cannot spawn maintenancer (registry doesn't see it); wave structure (W1∥W2 → W3 with ONE restart) honored | integration test: pre-add daemon → spawn fails loud; post-restart → spawn succeeds; rollout runbook enforces single deploy window in Wave 3 (no in-wave restarts in W1 or W2 — architect §2.3) |
| 6 | Worker fan-in escape valve present in `workflow.md` | End-of-turn-discipline cardinal (per task 1.4) + ladder (1 re-dispatch cap + `[incomplete]` + `### Gaps`); grep `workflow.md` for the pattern | prompt-compliance test (modeled on `tests/unit/test_report_integrity_prompts.py`) |
| 7 | KB covers known traps | KB grep matches: `time-bracket`, `data/instances.db`, `runner.py:486-491`, `feb5e915`, `pause-first`, `DROP NOT NULL`, `report_injections.content`, `sentinel` | pin test enumerates KB headings and asserts each trap reference is present |
| 8 | KB reaches the agent prompt | Tiered delivery per architect §6.2: (i) KB INDEX in `memory.md` (load-bearing, guaranteed-on via loader, ~<1k chars); (ii) full text via filesystem `read_file` on demand (content path, ~1-2k tokens per retrieval); (iii) RAG mirror via first-turn `experience()` duty in `workflow.md` (best-effort, requires `knowledge` in `tools.allow`). Full skill-content inclusion (~3k tokens/turn) is REJECTED. | integration test: spawn an instance and assert the KB INDEX header is reachable in the assembled prompt (i); assert a sample KB doc header is reachable via filesystem (ii); assert the first-turn workflow directive is present (iii) |
| 9 | `system_upgrade` granted under 3-factor nonce gate; `system_restart` excluded via `tools.deny` resolution-level strip | (i) **RESOLUTION-LEVEL:** resolved tool set INCLUDES `upgrade_*` (non-restart upgrade tools) AND EXCLUDES `system_restart` because `tools.deny` strips it at `resolve_tool_filter` time even though the category allow would otherwise expand to include it (approver-fix pass — `system_restart` carries `@register_tool_category("system_upgrade")` at `daemon/tools/upgrade_tools.py:1435`; deny-list is the only resolution-level enforcement); (ii) **DEFENSE-IN-DEPTH:** the live-env call-time refusal at `upgrade_tools.py:1458-1465` (`if self_env == "live"`) is NOT primary enforcement (it does not fire on dev/demo); pin test asserts (i) and (ii) are both present, but (i) is what makes it work on dev/demo | (i) unit test asserts `system_restart` NOT in resolved set on dev/demo AND live fixtures; integration test asserts a `system_upgrade` request without a user nonce echoes `factor_failures: user-confirmation-missing` (architect §5.3); (ii) call-time test asserts the `if self_env == "live"` refusal message is still emitted on live envs as belt + suspenders |
| 10 | Repair operations logged with instance-id audit | `repair_log` rows stamped `created_by=current_instance_id` per `infra.py:226-234` precedent | integration test invokes repair dry-run and asserts the row's `created_by` matches the spawning instance id |
| 11 | Prompt-guide §10 checklist sign-off | Reviewer signs off the checklist on the agent directory; no forbidden tokens in prompt prose | reviewer sign-off recorded in PR |
| 12 | KB `last-verified-against` header matches release tag at merge time | pin test compares header to the **merge-base release tag** (NOT `git rev-parse HEAD` — rolling SHA is brittle in CI per architect §6.3) | test passes when header equals the merge-base tag at KB-file-change time |

---

## Research Insights

- **R-SR16 / P2.2 tool-api-design §3.5** (`daemon/tools/_tool_registry.py:94-108`): privileged categories are opt-in-only; adding `system-log` and `ens-db` to the frozenset makes the default-allow paths (empty allow, watcher) stop granting them. Enforcement sites: `daemon/tools/instance.py:4497-4514` (`_strip_privileged_category_tools`), called at `:4547` and `:4583`.
- **Registration seam** (full 10-step checklist) at `daemon/tools/upgrade_tools.py:110-142`. For `ens-db`, only steps 1–7 apply (no per-agent prompt fragment needed for category tools; ari gets its `system_upgrade` fragment, maintenancer does NOT under OPEN A recommendation).
- **`_validate_select_only`** (`daemon/tools/db_tools.py:97`, called at `:497`) is the SELECT-only guard precedent — apply to every `ens_db_postgres_select` body.
- **Sync engine is ONE shared sync engine** (`manager.py:448-451`, PG via `factory.py:168-202` psycopg pool 5+10 pre-ping); bound to ~16 repositories. Recommendation (OPEN C): dedicated small pool for the repair writer; shared engine for read-heavy SELECT.
- **Audit-stamped writer precedent:** `infra.py:226-234` (`created_by=current_instance_id`); apply to `ens_db_repair_execute` controlled writes.
- **`asyncio.to_thread` bridge precedent** (`daemon/services/maintenance.py:230, 282`): sync engine used inside async tool body.
- **3-factor live upgrade gate** at `daemon/tools/upgrade_tools.py:2009-2036`: user_confirmed + single-use nonce + nonce echoed in a real user-origin message row. The same gate is recommended for any maintenancer-held live upgrade tool (OPEN A).
- **KB composition** (architect §6.1+§6.2 tiered design) — there is NO built-in per-agent "knowledge slot" loader. `daemon/loader.py:436-441` only injects the SHARED `agents/_prompt_system/knowledge.md` (the RAG tool usage guide). Skills are STATIC prompt text (delivered via `load_agent_skills`, `loader.py:287-316`; or skill-bank auto_load per-turn injection, `context_messages.py:869-940`) — **no startup execution hook exists**. The original "auto-record on startup" capability is architecturally impossible and is removed. Tiered design: (i) KB INDEX in `memory.md` as load-bearing guarantee (~<1k chars, always present); (ii) full content via filesystem reads on demand; (iii) RAG via first-turn `experience()` duty in `workflow.md` — requires `knowledge` in `tools.allow` (otherwise the RAG leg is structurally dead — `knowledge_tools.py:677,915`). Full-text skill-content inclusion of all 6 docs (~12k chars ≈ 3k tokens/turn) is rejected. `no_force_explore: true` (`loader.py:210-222, :649`) must be overridden for the one-time first-turn mirror.
- **v2 worker dispatcher contract** (per writing guide §7 + tester pattern): `send_message` with `load_skill="<one skill>"`, cap 3 concurrent, fan-in escape valve (1 re-dispatch cap + `[incomplete]` + `### Gaps`), report-sanity scrutiny conditioned on `[REPORT SANITY: …]` marker. Pin test: `tests/unit/test_report_integrity_prompts.py`.
- **Pause-first then quiesce convention** — features needing quiescent instance follow `pause_instance_cascade` → bounded quiescence → mutate → resume. First proven consumer: `WatchoverService.activate_watchover`. The convention is a load-bearing prerequisite for any restart-class operation maintenancer performs.
- **`PRIVILEGED_TOOL_CATEGORIES` current value:** `frozenset({"system_upgrade"})` at `_tool_registry.py:106-108`. Adding `system-log` and `ens-db` to this set is the EXCLUSIVITY MECHANISM; `TOOL_REQUIRED_AGENTS` (spawn-side) is the WRONG mechanism.

---

## Open Questions (mirrored in `decisions.md` for user resolution)

All five OPEN decisions have architect adjudications in `architecture-recommendation.md` §1:

- **OPEN A** — Restart/upgrade autonomy: architect §5.3 → **Option 1-refined** (grant `system_upgrade` ONLY, never `system_restart`; same 3-factor gate; instance-bound, satisfiable via in-thread user nonce echo). → `decisions.md` OPEN A.
- **OPEN B** — Log exclusivity scope (worker agent): architect §4.5 → **Option 1** (keep worker `system-log`; formalize as break-glass; workers NEVER `ens-db`). → `decisions.md` OPEN B.
- **OPEN C** — `ens_db_*` write policy + pool sizing: architect §3.1+§3.2 → **Option 4′** (shared-engine SELECT + dedicated 2+3 repair pool; audit SAME-TRANSACTION fail-closed; idempotent `DO$$`-only). → `decisions.md` OPEN C.
- **OPEN D** — Watcher side-effect: architect §4.6 → **Option 1** (accept; audit re-verified clean). → `decisions.md` OPEN D.
- **OPEN E** — KB doc-set composition path: architect §6.1+§6.2 → **Modified Option 4 tiered** (KB INDEX in `memory.md` = load-bearing; filesystem full-text reads = content path; RAG = first-turn duty; `knowledge` added to allow list). → `decisions.md` OPEN E.

**Pending items from architect (deferred or operator-side):**
- **Maintenance-worker split trigger threshold** — define the usage-evidence threshold (e.g. N log-touching worker dispatches/month by non-maintenancer dispatchers) that justifies the split. (architect §9)
- **PG role privilege check** — Wave-1 operator must confirm daemon PG role holds CREATE/ALTER/INSERT/UPDATE/DELETE on `ensemble_prod` before repair DDL is enabled (architect §7.6, §9).
- **`lock_timeout` post-soak re-tune** — initial 10s; re-tune against prod lock-wait distribution (architect §3.1, §10).
- **`pool_recycle × pool_pre_ping × reset-on-return` empirical verification** — verify on disposable PG (SQLAlchemy 2.x documented-correct, not yet verified here; architect §10).

---

# Phase 1: Agent Scaffolding + Registration

## Objective

Author the v2-pattern prompt files for `agents/maintenancer/` and register the agent with leader. Daemon restart is required because the registry is an import-time singleton.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1.1 | Create `agents/maintenancer/` directory with files per `docs/agent-prompt-writing-guide.md` Appendix file quick-ref: `meta.json`, `soul.md`, `rule.md`, `tools_note.md`, `workflow.md`, `memory.md`, `growth.md`, `skill-set.yaml`, `skills-template/.gitkeep`, `knowledge/.gitkeep` | none | directory layout matches tester pattern (`agents/tester/`) |
| 1.2 | Author `meta.json`: `id: maintenancer`, `name: Maintenancer`, `version: 1.0.0`, `skill_injection: true`, `innate_skills: ["dynamic-skill", "todo", "chart"]`, `no_force_explore: true` (per worker/leader convention), `tools.allow: ["system-log", "ens-db", "knowledge", "system_upgrade", "db"]` (`system_upgrade` GRANTED under 3-factor nonce gate per architect §5.1+§5.3 OPEN A Option 1-refined — instance-bound, satisfiable via in-thread user nonce echo `upgrade_tools.py:1870-1886`. **STRUCTURAL DEFECT (approver-fix pass):** `system_restart` carries `@register_tool_category("system_upgrade")` at `daemon/tools/upgrade_tools.py:1435` — a category-level grant of `system_upgrade` expands to ALL `system_upgrade`-tagged tools INCLUDING `system_restart`. The call-time refusal at `upgrade_tools.py:1458-1465` (`if self_env == "live"`) is **env-conditional** — on dev/demo installs the tool resolves AND would execute. **RESOLUTION-LEVEL enforcement:** `system_restart` MUST appear in `tools.deny` (per approver-fix pass directive) so the deny-list strips it at `resolve_tool_filter` time, BEFORE the call-time check ever runs. **`db` category GRANTED per user verbatim clause ("it have general db tools too")** — for external user-registered connections only; ensemble_prod is NEVER registered in ConnectionPoolManager (Risk R13 standing guard).), `tools.deny: ["git_commit", "edit_file", "write_file", "system_restart"]` (defense-in-depth + **resolution-level stripping of `system_restart`** per approver-fix pass; deny-list strip happens at `resolve_tool_filter` regardless of category allow), `team_members: ["explorer", "worker", "coder"]` (per OPEN B Option 1) | 1.1 | meta.json validates against schema; allow-list INCLUDES `system_upgrade` + `db` (pin test); `system_restart` is in `tools.deny` and is therefore EXCLUDED from the resolved tool set at `resolve_tool_filter` time even though the category allow would otherwise expand to include it (pin test 2.10(f) asserts this — dev/demo AND live); `db` category resolves to user-registered external connections only (NOT ensemble_prod, Risk R13 standing guard) |
| 1.3 | Author `soul.md` per writing guide §1 (write-as-the-agent), §5 (tone directive), §2 (one concern). Identity: centralized repair agent for the ensemble daemon. Tone block covers: voice to caller (terse, evidence-cited, severity-labeled), voice in dispatch prompts (imperative, self-contained), per-severity framing (🔴 non-negotiable / 🟢 advisory). Output template shape lives here ONLY (canonical home). | 1.1 | tone block present; no `meta.json` / `daemon/` / `tools.allow` / `_tool_registry` / `skill-set.yaml` / `innate_skills` tokens in prose |
| 1.4 | Author `rule.md` with ≤7 Cardinal rules + Guidelines (writing guide §3). Proposed Cardinals: (1) Read KB before any action; (2) Confirm before any destructive operation; (3) End turn after dispatch (writing guide §7); (4) **Relay nonce verbatim — never fabricate/echo agent-side** (per OPEN A Option 1-refined + architect §5.3); (5) Report sanity scrutiny on every worker report (writing guide §7); (6) SELECT-only on `ens_db_postgres_select`; (7) Audit-stamp every repair row. Guidelines numbered, secondary. | 1.1, OPEN A resolved | grep `\.md` returns zero path tokens in prompt text; cross-references resolve; ≤7 Cardinal rules |
| 1.5 | Author `workflow.md`: investigation flow → dispatch pattern (single-skill worker per spawn, cap 3 concurrent, END TURN after batch) → fan-in escape valve ladder (stuck confirm → 1 re-dispatch → `[incomplete]` + `### Gaps`; max 1 re-dispatch) → report-sanity scrutiny conditioned on `[REPORT SANITY: …]` marker → END TURN contract stated ONCE. | 1.3 | escape valve ladder present; END TURN stated once; dispatch snippets concrete |
| 1.6 | Author `tools_note.md`: per-tool allow-list table (operational, not registry-referencing — writing guide §4); `ens_db_*` usage notes (`ens_db_postgres_select` SELECT-only; `ens_db_repair_execute` 3 gates; `ens_db_inspect` schema/range); `system-log` exclusivity reminder (time-bracket rule applies); `system_upgrade` gate reminder (3-factor nonce gate per OPEN A — Cardinal rule #4 in 1.4); `db` category reminder (external connections only — NEVER `ensemble_prod` per Risk R13); `ens-db` vs `db` separation (daemon's own DB via `ens_db_*` only; user-registered external DBs via `db` only) | 1.2 | allow-list table matches `meta.json` `tools.allow` (no drift); no registry-prose leakage |
| 1.7 | Author `memory.md`: calibration tables, trigger checklists, KB index, fallback ladder per writing guide §8 (fallback stays within `team_members`) | 1.5 | no process mechanics duplicated from workflow.md |
| 1.8 | Author `growth.md` + empty `skill-set.yaml` (populated in Phase 4) + `skills-template/.gitkeep` | 1.1 | file structure matches tester |
| 1.9 | Edit `agents/leader/meta.json`: `team_members` += `"maintenancer"` (list now length 14) | 1.1 | leader meta.json valid |
| 1.10 | **MERGE-ORDER INVARIANT (no in-phase restart):** the P1 PR must merge **before** the P2 PR. Any crash-restart between the two merges lands coherent state — the running process keeps the old frozenset, so de-scopes have no exclusivity gap; the new category strings in 1.2's `tools.allow` are inert until P2 lands (validation warning only, `registry.py:1178-1180`). Restart is deferred to Wave 3 (single deploy window) per architect §2.3. | 1.9, 2.7 | P1 lands first in the merge queue; merge order recorded in PR description |
| 1.11 | Pin test: `tests/unit/test_maintenancer_agent.py` modeled on `tests/unit/test_project_manager_agent.py:275-281`. Asserts: agent_id resolves, version resolves, `team_members` matches `meta.json`, prompt files load, no forbidden tokens (`meta.json` / `daemon/` / `_tool_registry` / `tools.allow` / `skill-set.yaml` / `seed_all` / `innate_skills` / `default_agent_versions`) in prompt prose, ≤7 Cardinal rules, END TURN + escape valve + report-sanity markers present in workflow.md. **EXTENDED (architect §4.3):** also enumerates base + versioned metas (standing guard for `developer[v3]`-class future additions). | 1.10 | test passes locally + in CI |

## Coupling

- **Tight with:** P5 (docs reference these files by section name); P4 (`team_members` declared here drives worker dispatcher contract).
- **Independent of:** P2, P3 (scaffolding precedes tool wiring and KB authoring).

## Risks

- R7 (registry singleton mis-pickup) — mitigated by restart + deny-by-default loud-fail at `instance.py:1817-1848`.
- R8 (prompt-guide violations) — mitigated by the pin test in 1.11.

## Exit Criterion

`pytest tests/unit/test_maintenancer_agent.py -v` passes; leader can spawn maintenancer (smoke test in dev); tools allowed match `meta.json`; no forbidden tokens in prompt prose.

## Architect Enrichment Hooks

- Decide the exact Cardinal #4 wording once Phase 2 lands — does the agent describe the 3-factor nonce gate concretely in `rule.md`, or refer to it abstractly (writing guide §1: prefer agent-POV, but operational detail is OK)?
- Decide whether `meta.json` should add `context_injection.heuristic_match_shared_md_files: true` (mirroring worker/wanderer) — affects whether the agent pulls in shared planning docs.
- Decide the `meta.json` `capabilities` list (tester has none; wanderer has none — but if maintenancer exposes itself in the FE picker, capabilities may be desired).
- Decide the exact wording of the Cardinal #1 KB-read-before-action rule (must it require a specific KB section, or trust the `kb-curator` skill to surface it)?

---

# Phase 2: Tool Layer + Exclusivity

## Objective

Author the new `ens-db` tool family; promote `system-log` and `ens-db` to privileged categories; de-scope `system-log` from developer/wanderer.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 2.1 | Author `daemon/tools/ens_db_tools.py` with `create_ens_db_tools(manager, current_instance_id)` factory (mirrors `create_system_log_tools` at `daemon/tools/system_log_tools.py:256`); `@register_tool_category("ens-db")` ABOVE `@tool` for every closure (per `upgrade_tools.py:117` step 1); tools: (a) `ens_db_postgres_select` — SQL via shared engine (`manager.engine`, `manager.py:438-460`), `_validate_select_only` enforced, **per-tx `SET LOCAL statement_timeout` inside the tool's own connection block + client-side `asyncio.wait_for(asyncio.to_thread(...))` belt** (architect §3.1; thread-bridge cancels the awaitable but does NOT abort the blocking psycopg call — server-side `statement_timeout` is the real cap); (b) `ens_db_inspect` — public-schema tables only (`table_schema='public'`), `pg_stat_user_tables.n_live_tup` row counts, column types, indexes, FK chains, pool status, last-ANALYZE/autovacuum (architect §3.5; withhold credentials/role grants/`pg_catalog`); (c) `ens_db_repair_execute` — dedicated `create_engine` with pool **2+3**, `pool_pre_ping=True`, `pool_recycle=3600`, engine-level connect_args `statement_timeout=60s, lock_timeout=10s, idle_in_transaction_session_timeout=60s` (initial — re-tune after first soak per architect §3.1); audit `INSERT` in the SAME `BEGIN/COMMIT` as the repair, same connection — **fail-closed on audit-write failure** (architect §3.2); dry-run: DDL = `BEGIN; <DDL>; ROLLBACK;`, DML = same with row-count projection + first ~100 affected-row preview; confirm gate = single-use nonce + 5-min TTL + action-bound (kind='repair', target=<table/operation>), without the human-relay factor (architect §3.3); **idempotent `DO$$`-only DML** + tool refuses any input resembling a migration filename (architect §3.2, R10); repository-methods-first rule encoded as a dry-run warning when a service method exists (architect §3.4); **self-surgery refusal** — guard refuses targets matching the calling instance's own rows (architect §7.2); (d) `ens_db_pool_status` — diagnostic, returns pool counts | none | tool bodies respect §3 SELECT guard; per-tx `SET LOCAL` applied; repair body has 3 gates + same-tx audit + idempotent `DO$$`-only; async→sync bridge via `asyncio.to_thread` (`maintenance.py:230, 282` precedent) |
| 2.2 | Wire `create_ens_db_tools` into `daemon/tools/instance.py:create_instance_tools` (~line 4364-4438 per digest) — `tools.extend(create_ens_db_tools(manager, current_instance_id))` per `upgrade_tools.py:129` step 5 | 2.1 | wiring matches the `upgrade_tools.py:115-129` checklist steps 1-5 |
| 2.3 | Add to `daemon/tools/_tool_registry.py:494` CATEGORY_MODULES: `"ens-db": "daemon.tools.ens_db_tools"` (step 2) | 2.1 | registry entry added |
| 2.4 | Add the four tool names to `_tool_registry.py` `DYNAMIC_TOOL_NAMES` (step 3) | 2.2 | factory-created names registered |
| 2.5 | Regen `KNOWN_TOOL_NAMES` via `discover_source_only_tool_names()` (step 4); pin test `tests/unit/tools/test_frozen_tool_name_discovery.py::test_known_tool_names_matches_source_exactly_no_drift` passes | 2.4 | drift test passes |
| 2.6 | Edit `daemon/tools/_tool_registry.py:106-108`: `PRIVILEGED_TOOL_CATEGORIES = frozenset({"system_upgrade", "system-log", "ens-db"})` — adds `system-log` and `ens-db` to the privileged set | none | `_strip_privileged_category_tools` (`instance.py:4497-4514`) now strips both for empty-allow paths; comment block above updated to reflect three categories |
| 2.7 | De-scope `system-log` from `agents/developer/meta.json`, `agents/developer[v2]/meta.json`, and `agents/wanderer/meta.json` `tools.allow` (remove the entry). Worker keeps `system-log` per OPEN B recommendation. | 2.6 | resolved tool sets for developer base + developer[v2] + wanderer exclude `ens_system_log_*` |
| 2.8 | **VERIFICATION + ASSERTION ONLY** (post-P1 merge) — does NOT edit `agents/maintenancer/meta.json` (already authored in task 1.2 with allow-list `["system-log", "ens-db", "knowledge", "system_upgrade", "db"]` + deny-list `["git_commit", "edit_file", "write_file", "system_restart"]`). Resolves the allow-list via `resolve_tool_filter` and asserts (a) `system_upgrade` is in resolved set, (b) **`system_restart` is EXCLUDED from the resolved set via `tools.deny` stripping at `resolve_tool_filter` time** (approver-fix pass — RESOLUTION-LEVEL enforcement; the call-time refusal at `upgrade_tools.py:1458-1465` `if self_env == "live"` is **defense-in-depth only** because dev/demo installs would otherwise resolve AND execute `system_restart`), (c) `db` category resolves to user-registered external connections only — NOT `ensemble_prod` (Risk R13 standing guard), (d) `ens-db` + `system-log` + `knowledge` resolve cleanly. | 1.2 (P1 must merge first per 1.10), 2.6, 2.7 | resolution pin test passes; (b) asserts the deny-list strips `system_restart` on dev/demo AND live; documented as "verification only — meta.json authoritative source is task 1.2" |
| 2.9 | **VERIFICATION + ASSERTION ONLY** (post-P1 merge) — does NOT edit `agents/maintenancer/meta.json` (deny-list already authored in task 1.2). Asserts the deny-list is enforced at tool-resolution time and that a worker whose skill fails to load still cannot mutate state via `git_commit`/`edit_file`/`write_file`. | 1.2 (P1 must merge first per 1.10) | deny-list pin test passes; documented as "verification only — meta.json authoritative source is task 1.2" |
| 2.10 | Tests: (a) `tests/unit/tools/test_ens_db_tools_select_only.py` (mirror `daemon/tools/db_tools.py:497` test — INSERT / UPDATE / DELETE / DDL all raise; **also verify per-tx `SET LOCAL statement_timeout` is applied INSIDE the tool's own connection block** per architect §3.1); (b) `tests/unit/tools/test_ens_db_repair_audit.py` (asserts `created_by=current_instance_id` stamp on `repair_log` row AND that audit INSERT is in the SAME transaction as the repair — fail-closed on audit-write failure per architect §3.2; **council disagreement noted**: `infra.py:226-234` vs `infra/models.py:256 + infra/repository.py:540-541` — implementer MUST pin the exact precedent location when fixing); (c) `tests/unit/tools/test_ens_db_repair_idempotent.py` (**NEW per checklist** — run each repair SQL twice, assert net state change == 0 — pins R14 idempotent `DO$$`-only rule); (d) `tests/unit/tools/test_ens_db_repair_idempotent_doller.py` (asserts repair tool refuses non-idempotent DML and any `*.sql` filename input — migration-runner trap); (e) `tests/unit/tools/test_privileged_category_system_log.py` (asserts developer base + developer[v2] + wanderer lose `system-log`; worker keeps `system-log` only — NEVER `ens-db` — per OPEN B Option 1; watcher loses it per R-SR16; architect §4.3 enumerates versioned metas); (f) `tests/integration/test_maintenancer_spawn_resolves_tools.py` (asserts maintenancer gets `ens-db` + `system-log` + `system_upgrade` (non-`system_restart` subset) + `knowledge` + `db` (D19) — with `system_upgrade` granted AND **`system_restart` EXCLUDED via `tools.deny` resolution-level strip at `resolve_tool_filter` time (approver-fix pass — RESOLUTION-LEVEL enforcement)**; `db` resolves ONLY to user-registered external connections (NOT `ensemble_prod`, Risk R13); developer + developer[v2] get NEITHER `system-log` NOR `ens-db`; worker gets `system-log` only); (g) `tests/integration/test_maintenancer_upgrade_gate_refuses.py` (architect §5.3 — assert a `system_upgrade` live request without a user nonce echoes `factor_failures: user-confirmation-missing`; mirror ari pattern); (h) **dual-engine CI coverage (architect §7.5)** — both (a)-(g) above must pass on **SQLite-backed AND PG-backed** test fixtures. | 2.7, 2.8 | all tests pass; integration test on a freshly-restarted daemon confirms gate behavior + dual-engine coverage |

## Coupling

- **Tight with:** P1 (`meta.json` allow-list must list new categories at scaffolding time).
- **Loose with:** P3 (tools may surface KB pointers via tool descriptions); P4 (workers use `ens_db_*`).

## Risks

- R3 (shared engine pool exhaustion) — OPEN C; dedicated small pool recommendation.
- R4 (watcher silent loss of `system-log`) — OPEN D; accept by default.
- R9 (allow-list drift) — pin test in 2.10 catches.
- R11 (frozen-name drift) — pin test in 2.5 catches.

## Exit Criterion

`pytest tests/unit/tools/test_frozen_tool_name_discovery.py tests/unit/tools/test_ens_db_tools_select_only.py tests/unit/tools/test_ens_db_repair_audit.py tests/unit/tools/test_privileged_category_system_log.py tests/integration/test_maintenancer_spawn_resolves_tools.py -v` all pass; developer/wanderer resolved tool sets confirmed `system-log`-free in CI; watcher's resolved tool set confirmed `system-log`-free (by R-SR16 side-effect).

## Architect Enrichment Hooks

- The exact contract for `ens_db_repair_execute` confirmation gate — UI prompt? Tool returns ASK + reason code and awaits caller confirmation? Mirror `upgrade_tools.py:2009-2036` 3-factor semantics, or simpler single-factor for repairs? (Architect detail to be settled before 2.1 closes.)
- Pool sizing for the dedicated `ens_db_*` engine (per OPEN C).
- Whether `ens_db_inspect` exposes schema metadata (column types, FK chains) or just row counts.
- Whether the `repair_log` audit table needs a new migration or can use an existing `infra_log` / audit table (check `daemon/migrations/` for prior art).
- Whether the `ens-db` category name in CATEGORY_MODULES should mirror the kebab-case of `system-log` (yes — convention) or use underscores.
- Whether the migration-runner-trap refusal is enforced at the tool layer (refuse any `*.sql` filename in repair input) or in the skill (refuse to author migrations). Recommend tool-layer enforcement + skill-layer refusal (belt + suspenders).

---

# Phase 3: Local KB Authoring

## Objective

Author the per-agent KB doc set under `agents/maintenancer/knowledge/` covering architecture, known traps, repair runbooks, and the autonomy model. Wire a `kb-curator` skill via the **tiered design** (architect §6.1+§6.2): (i) KB INDEX in `memory.md` (load-bearing primary); (ii) filesystem full-text reads on demand (content path); (iii) RAG mirror via first-turn `experience()` duty (best-effort). Full skill-content inclusion of all 6 docs is REJECTED (~3k tokens/turn).

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 3.1 | Author `agents/maintenancer/knowledge/01-architecture-overview.md` (~1.5k chars) — digest of: daemon manager facade, graph + middleware slots, repositories (sync engine bound to ~16), checkpoint adapter, prompt loader (`daemon/loader.py` 11-section composition), registry singleton (import-time); cross-refs to existing `docs/` (NO duplication); `last-verified-against: v<version>` header (release-tag form per D17 + architect §6.3; tag source = `pyproject.toml` version) | none | digest accurate against the release-tag version; cross-refs resolve |
| 3.2 | Author `agents/maintenancer/knowledge/02-jobs-missions-admission-state.md` (~1.5k chars) — Job/task/mission model with the 4-value `AdmissionState` (QUEUED/ACTIVE/DONE/DEAD); legacy `JobStatus` shim; receipts vs stateful proxies; `daemon/services/job_state_machine.py` transitions (retries, dead-lettering, replay, cancellation, post-commit re-arming); lock-first concurrency | none | digest cites `daemon/services/job_state_machine.py`; cross-refs resolve |
| 3.3 | Author `agents/maintenancer/knowledge/03-log-forensics.md` (~1k chars) — **time-bracket forensics rule** (line numbers are NOT chronological; interleaved append regions); `ens_system_log_*` tool usage (`list` / `read` / `search` / `tail`); rotation policy; redaction caveat (API keys, tokens, Bearer → `[REDACTED]`); SSRF / size cap awareness | none | digest cites the 2026-09-04 log-trap critical note; rule is operational |
| 3.4 | Author `agents/maintenancer/knowledge/04-known-traps.md` (~2k chars) — digest of project critical notes most relevant to maintenance, each with trap + fix recipe: (i) stale SQLite relic at `data/instances.db` (prod = `ensemble_prod` PG only); (ii) migrations runner is SQLite-only by design (`runner.py:486-491`) — PG schema uses `create_all + _ensure_postgres_columns`; (iii) idempotent `DO$$ DROP NOT NULL` recipe (`manager.py:4996-5012`); (iv) pause-first then quiesce convention; (v) auto-promote kill-switch default ON (`feb5e915`); (vi) sentinel `''` bridge for `report_injections.content` NOT-NULL drift (4th defect on this table DDL must eliminate drift) | none | digest enumerates each trap with the fix recipe and a file:line anchor |
| 3.5 | Author `agents/maintenancer/knowledge/05-repair-runbooks.md` (~2k chars) — pause-first quiesce sequence; idempotent DROP NOT NULL recipe; CHECKPOINT-aware migration recipe; `MaintenanceJob` registry usage (`daemon/services/maintenance.py:68`, 15-min interval); `StaleTaskRecovery` (`stale_task_recovery.py:42`); orphan ACTIVE job sweep (api.py:517-526, ≤20min) | none | each runbook has trigger + steps + verification |
| 3.6 | Author `agents/maintenancer/knowledge/06-restart-upgrade-runbook.md` (~1.5k chars) — pause-first quiesce; journal sweep; live gate via 3-factor (`upgrade_tools.py:2009-2036`); `adopt_stale_txn`; atomic flip (`lib.sh:~1126`); DR-1 frozen-binary preflight (`daemon/__main__.py:104`); cycle ledger (`scripts/upgrade/ledger_check.py`); restart-required kill-switch activation recipe (per `kv-ambient-awareness-fix` rollout) | none | digest cites the upgrade pipeline blueprint |
| 3.7 | Author `agents/maintenancer/skills-template/kb-curator.md` — **TIERED design per architect §6.1+§6.2** (the original "startup recording via experience()" capability is REMOVED — skills are static prompt text; no startup execution hook exists). The skill instructs the LLM on three responsibilities: (a) **Index responsibility** — the KB INDEX (one-line-per-doc triggers + verification discipline) is authored in `memory.md` (task 1.7 slot) — guaranteed-on via the loader; (b) **Content path responsibility** — full KB doc retrieval via filesystem `read_file` on demand when a specific doc is needed; (c) **First-turn RAG mirror duty** — on the agent's first turn, override `no_force_explore` and call `experience(text=<each KB doc>)` to mirror the KB into RAG for cross-session recall. The first-turn duty is a workflow rule (not a skill capability) — pin in `workflow.md` and enforce via a grep test that asserts the directive is present. Frontmatter `version: 1.0.0`; manifest version must match. | 3.1–3.6 | skill body has one example per responsibility; frontmatter version matches manifest; no startup-hook claims |
| 3.8 | Pin test: `tests/unit/test_maintenancer_kb_coverage.py` — enumerates the 6 KB docs and asserts each contains its trap reference by string (`time-bracket`, `data/instances.db`, `runner.py:486-491`, `feb5e915`, `pause-first`, `DROP NOT NULL`, `report_injections.content`, `sentinel`, `3-factor`, `adopt_stale_txn`) | 3.1–3.6 | test passes; the `last-verified-against` header in each KB doc equals the merge-base release tag at KB-file-change time (NOT a rolling SHA — D17, architect §6.3) |

## Coupling

- **Loose with:** P2 (tools may surface KB content via tool descriptions).
- **Tight with:** P4 (skills reference KB docs by section name in dispatch prompts).

## Risks

- R6 (KB rot) — mitigated by `kb-curator` skill (first-turn RAG duty, Tier 3) + tiered design (Tier 1 INDEX in `memory.md`, Tier 2 filesystem reads) + `last-verified-against` release-tag pin + pin test.
- R12 (prompt token budget) — mitigated by ≤2k chars per KB doc; total ≤12k chars across the six docs.

## Exit Criterion

`pytest tests/unit/test_maintenancer_kb_coverage.py -v` passes; KB INDEX present in `memory.md` (Tier 1, load-bearing); filesystem reads on demand work (Tier 2); first-turn RAG mirror runs via `workflow.md` rule (Tier 3, best-effort, requires `knowledge` in allow); per-architect resolution of OPEN E settled (Modified Option 4 tiered).

## Architect Enrichment Hooks

- The exact location of the KB doc set — `agents/maintenancer/knowledge/*.md` (recommendation) vs `.agents/maintenancer/kb/*.md` (alternative). Architect decides based on whether the daemon loader gains a per-agent KB slot in the meantime.
- The `kb-curator` skill's recording cadence — startup-only (default), or per-turn refresh (mirrors the `kv-ambient-awareness` kill-switch `ENSEMBLE_AMBIENT_KV_FRESH`).
- Whether KB docs are loaded as a single concatenated content block or as separate sections in the prompt.
- Whether the `last-verified-against` header should pin against `latest` git ref (rolling) or a release tag (immutable per release).

---

# Phase 4: Skills + Workers

## Objective

Define the v2-style skill set and worker `team_members` for maintenancer; author `skills-template/*.md` for each skill; pin dispatcher-pattern tests.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 4.1 | Finalize skill list (proposed): `log-forensics` (cross-refs §3 KB), `job-mission-repair` (cross-refs §2 KB), `ens-db-repair` (cross-refs §4 + §5 KB), `restart-upgrade-ops` (cross-refs §6 KB), `bug-advisory` (consultant role — analyze a bug report, propose root cause + fix sketch), `health-check` (MaintenanceJob registry + StaleTaskRecovery sweep), `kb-curator` (Phase 3.7) | P3 | 6–7 skills; each has 1-line purpose documented in `memory.md` |
| 4.2 | For each skill, author `agents/maintenancer/skills-template/<name>.md` per tester pattern — frontmatter `version: 1.0.0`, contract (what worker does on `load_skill`), focus areas, mandatory output format; ensure NO cross-references to forbidden tokens per writing guide §1 (no `meta.json` / `daemon/` / `_tool_registry` / `tools.allow` in prose) | 4.1 | each skill ≤2k chars; frontmatter version matches yaml version (writing guide §6) |
| 4.3 | Update `agents/maintenancer/skill-set.yaml` to mirror all skill versions (one entry per skill; `auto_load: true` for `kb-curator`; others are worker-loaded via `load_skill`) | 4.2 | yaml version == frontmatter version per skill (writing guide §6) |
| 4.4 | Refine `agents/maintenancer/rule.md` Cardinal #3 / #4 wording now that the skill list is fixed (refine from Phase 1.4 placeholder) | 4.1 | cardinals ≤7; no skill names leaked into cardinals (use role descriptions) |
| 4.5 | Finalize `agents/maintenancer/meta.json` `team_members` per OPEN B Option 1: `["explorer", "worker", "coder"]` — `worker` for dynamic-skill execution (designated `system-log` break-glass, **NEVER** `ens-db`); `explorer` for read-only deep research; `coder` for bounded code-fix. `maintenance-worker` split deferred — file as usage-evidence-triggered follow-up (architect §4.5, §9). | P1, OPEN B resolved | meta.json valid; worker holds `system-log` only; `ens-db` not on worker's allow list (exclusivity test asserts this) |
| 4.6 | Author `workflow.md` dispatcher pattern: `send_message` with `load_skill="<one skill>"` (one skill per spawn), max 3 concurrent workers, fan-in escape valve (1 re-dispatch cap + `[incomplete]` + `### Gaps`), report-sanity scrutiny conditioned on `[REPORT SANITY: …]` marker — model the language on tester workflow.md | 1.5, 4.5 | escape valve present; scrutiny rule stated once (writing guide §7); cross-refs to KB docs use section-name form |
| 4.7 | Tests: `tests/unit/test_maintenancer_skill_versions_consistent.py` (frontmatter == yaml), `tests/unit/test_maintenancer_report_scrutiny.py` (modeled on `tests/unit/test_report_integrity_prompts.py`), `tests/unit/test_maintenancer_fan_in_valve.py` (asserts workflow.md contains the ladder and the cap), `tests/unit/test_maintenancer_team_members_within_team.py` (asserts fallback references in skills stay within `team_members` per writing guide §8) | 4.2, 4.6 | all tests pass |

## Coupling

- **Tight with:** P1 (`team_members` declared there).
- **Tight with:** P3 (skills reference KB docs by section name).

## Risks

- Skill-bank miss on `load_skill` — writing guide §8 fallback ("DEGRADED — skill bank miss (`<name>`)") + escalation within `team_members`.
- R8 (prompt-guide violations) — Phase 1.11 test + reviewer sign-off on every skill.

## Exit Criterion

`pytest tests/unit/test_maintenancer_skill_versions_consistent.py tests/unit/test_maintenancer_report_scrutiny.py tests/unit/test_maintenancer_fan_in_valve.py tests/unit/test_maintenancer_team_members_within_team.py -v` all pass; spawning a worker with `load_skill="log-forensics"` succeeds in integration test.

## Architect Enrichment Hooks

- Whether `bug-advisory` is a separate skill or a mode of `restart-upgrade-ops` (consultant vs operator distinction).
- Whether `health-check` is a worker-loaded skill or a scheduled job (cron-like via `MaintenanceJob` registry at `maintenance.py:68`) — both shapes are valid; recommend worker-loaded for ad-hoc + scheduled for periodic.
- Whether `kb-curator` should auto-load (`auto_load: true` in `skill-set.yaml`) or be loaded on-demand.
- Whether `coder` should be in `team_members` (tester has `[explorer, worker]`, developer[v2] has `[coder, worker]` — pattern says yes if code-fix is in scope).
- Whether `restart-upgrade-ops` skill should refuse if OPEN A resolves against sharing `system_upgrade` (in which case the skill is a preparation-only advisory).

---

# Phase 5: Docs + Prompt-Guide Compliance + Rollout

## Objective

Document the agent for humans and reviewers; enforce prompt-guide §10 checklist; integrate with leader routing; finalize rollout runbook (restart-required activation).

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 5.1 | Author `agents/maintenancer/README.md` (humans-facing) — purpose, when leader dispatches, what it can/can't do (per OPEN A/B/C/D resolution), restart-required note, KB index (six docs by name), skill list (six–seven by name), rollout link | 1–4 | README accurate and concise; KB index matches `knowledge/` directory; skill list matches `skills-template/` directory |
| 5.2 | Run pre-commit checklist from `docs/agent-prompt-writing-guide.md` §10 against the new directory; fix every violation | 1–4 | checklist sign-off recorded in PR; pin test from 1.11 passes |
| 5.3 | Update `agents/leader/soul.md` (or the relevant prompt surface — exact home per leader's canonical-home rule) to add a routing line: "For ensemble system maintenance (log forensics, ensemble_prod repair preparation, restart/upgrade preparation), dispatch to `maintenancer`" — section-name ref per convention v2 (writing guide §3) | 1.9 | leader prompt has the routing line; convention v2 clean (no path tokens) |
| 5.4 | Rollout runbook (in `agents/maintenancer/README.md` §Rollout or a sibling `ROLLOUT.md`) — **3-WAVE STRUCTURE per architect §2.3:** (W1) P1∥P2∥P3 land as file-disjoint PRs with **P1-before-P2 merge-order invariant**; NO in-wave restart. (W2) P4 (skills + team_members + dispatcher pins) lands — NO restart. (W3) P5 (docs + runbook + integration test) lands, then the **SINGLE DEPLOY WINDOW**: pause-first quiesce → restart daemon → run all pin tests → smoke-spawn a worker via `send_message` → verify `system-log` / `ens-db` / `system_upgrade` resolved → document the migration-runner trap (NEVER author a migration via `ens_db_repair_execute`) → **snapshot-before-repair for any executed repairs** (snapshot = `pg_dump --schema-only` + targeted table dumps; recoverable via idempotent `DO$$` forward-fix from `repair_log.created_by` per architect §2.4). **Worker formalized as break-glass (architect §4.5, §7.1):** NEVER strip without replacement; leader re-spawns on maintenancer ERROR (revive semantics re-register watchers); re-grant = meta allow + restart via pause-first runbook; raw-bash fallback documented WITH the caveat that raw reads bypass `ens_system_log_*` redaction. **Wave-1 operator checklist:** PG role privilege check — confirm daemon PG role holds CREATE/ALTER/INSERT/UPDATE/DELETE on `ensemble_prod` BEFORE repair DDL is enabled (architect §7.6, §9). | 1.10, 2.8 | runbook has step-by-step with verification commands; wave structure is explicit; rollback section completes the architect §2.4 list |
| 5.5 | Integration test: `tests/integration/test_maintenancer_end_to_end.py` — leader spawns maintenancer → maintenancer loads KB INDEX from `memory.md` (load-bearing) → filesystem-reads a sample KB doc on demand (content path) → first-turn RAG mirror runs `experience()` on each doc (best-effort) → spawns worker with `load_skill="log-forensics"` → worker returns evidence → report-sanity scrutiny passes → worker reports back; assert no tool-resolution errors and the final report carries evidence; assert the 3-factor gate refuses a `system_upgrade` request missing the nonce (architect §5.3 — mirrors ari test pattern; the gate is instance-bound and satisfiable by in-thread user nonce echo per §5.1). | 1–4 | integration test passes on a fully-restarted daemon (single deploy window per wave structure) |

## Coupling

- **Tight with:** P1, P2, P3, P4 (rollout closes all phases).

## Risks

- R7 (registry singleton restart-required) — rollout runbook calls this out explicitly.
- R8 (prompt-guide violations) — §10 checklist is the closure.

## Exit Criterion

All five pin tests from P1–P4 + the integration test from 5.5 pass; reviewer sign-off on §10 checklist; rollout runbook executed once on dev without error; leader routes a sample ensemble-maintenance prompt to maintenancer successfully.

## Architect Enrichment Hooks

- Whether leader routing belongs in `soul.md` (likely) or in a leader strategy skill (e.g. `coordination-strategy` if one exists) — architect decides based on canonical-home rule.
- Whether the rollout runbook should also cover the migration-runner trap (recommended yes — SQLite-only by design, do NOT run a migration via `ens_db_repair_execute`).
- Whether the integration test should also exercise the 3-factor gate (rejects a request without nonce) — recommended yes, mirrors the ari test pattern.
- Whether `agents/maintenancer/README.md` should be the only humans-facing doc, or whether `docs/agents/maintenancer.md` (top-level docs) is also needed.
- Whether the KB index in `README.md` should deep-link to `agents/maintenancer/knowledge/*.md` or just enumerate by name (deep-link risks path-token leakage into a humans-facing doc; enumerate is safer).


---

## Rollout Notes (3-wave procedure per architect §2.3)

**W1 (P1 ∥ P2 ∥ P3, file-disjoint, NO in-wave restart):**

1. Land **P1** (agent scaffolding + leader team_members) — **P1 PR merges BEFORE P2 PR** (merge-order invariant).
2. Land **P2** (tool layer + exclusivity + frozenset + same-PR dual-pin updates + `test_upgrade_registration.py:100` / `test_attestation_registration.py:154`).
3. Land **P3** (KB docs + `kb-curator` skill refactor + KB INDEX in `memory.md`).
4. Any crash-restart mid-W1 lands coherent state (old frozenset keeps explicit allows working; the only contract is the literal category strings in 1.2's `tools.allow`).

**W2 (P4, NO restart):**

5. Land **P4** (skills + team_members finalization + dispatcher pins). Worker keeps `system-log` (break-glass); `ens-db` is NOT on worker's allow list; `db` category added per D19.

**W3 (P5 + ONE restart window):**

6. Land **P5** (docs + §10 checklist + leader routing line + integration test + runbook).
7. **Pause-first quiesce** any instance whose work overlaps the new agent. Verify with: `curl -s -X POST http://localhost:8079/api/instances/<id>/pause` and confirm `is_paused=true` in `instances` row (per pause-first convention; `routers/instances.py:647-669`).
8. **Restart the daemon** — required because `_registry` is an import-time singleton (`daemon/registry.py:1170-1175`); `_registry.discover()` runs once. The single restart atomically activates: new agent dirs, all meta.json edits (allows, de-scopes including developer[v2], `db` per D19, `team_members`), the frozenset change, and the category-module wiring.
9. **Pool-config pre-flight on disposable PG** — verify `pool_recycle × pool_pre_ping × reset-on-return` interaction on a disposable PG before W3 restart (architect §3.1, §10).
10. **Run all pin tests** — Phase 1.11, Phase 2.10 (a)-(h), Phase 3.8, Phase 4.7, integration test 5.5.
11. **Smoke-spawn** — `leader` sends a small maintenance prompt to `maintenancer`; verify KB INDEX load + filesystem KB read + first-turn RAG mirror + skill dispatch work end-to-end; verify `system-log` / `ens-db` / `system_upgrade` (non-`system_restart` subset) / `knowledge` / `db` (external only) resolved. **Exclusivity/autonomy invariant (approver-fix pass, D20):** verify `system_restart` is NOT in the resolved tool set on this install's env (whether dev, demo, or live) — the `tools.deny` entry in `agents/maintenancer/meta.json` is the resolution-level enforcement point; the call-time refusal at `upgrade_tools.py:1458-1465` (`if self_env == "live"`) is defense-in-depth only and is env-conditional.
12. **Monitor** — first 24h: watch `ens_system_log_tail` for any `spawn_instance` denials (deny-by-default should fire LOUDLY per `instance.py:1817-1848` if anything is misconfigured); also watch for `upgrade_promote_refusal` journal events (architect §5.3 monitoring); also watch for `db`-category resolution of any synthetic `ensemble_prod` connection (Risk R13 standing guard).
13. **Worker break-glass verified** — incident-time raw-bash fallback path documented; leader re-spawns on maintenancer ERROR (revive semantics re-register watchers).

**Rollback (complete per architect §2.4):**

- **Pre-P2 (anytime in W1):** revert the P1 / P2 / P3 PRs as appropriate; no DB or restart side effects yet.
- **Post-P2 (after the dual pins are updated and frozenset is changed):** must revert — the `PRIVILEGED_TOOL_CATEGORIES` frozenset entry (restore `frozenset({"system_upgrade"})`); the `CATEGORY_MODULES` entry (`ens-db` removal); `DYNAMIC_TOOL_NAMES` / `KNOWN_TOOL_NAMES` regen (else `tests/unit/tools/test_frozen_tool_name_discovery.py` stays red and masks real regressions); the developer + developer[v2] + wanderer de-scopes; the `agents/maintenancer/meta.json` allow-list additions (including `db` per D19); **the `tools.deny` entries (remove `"system_restart"` per D20 + revert `["git_commit","edit_file","write_file","system_restart"]` back to the pre-PR state)** — the deny-list is the resolution-level enforcement point for the `system_restart` exclusion and MUST be reverted alongside the allow-list change; the `create_ens_db_tools` wiring in `instance.py`; the same-PR dual pin updates in `tests/unit/tools/test_upgrade_registration.py:100` and `tests/unit/tools/test_attestation_registration.py:154`. Then `rm -rf agents/maintenancer/` + revert `agents/leader/meta.json` `team_members`; daemon restart.
- **Executed repairs have NO structural rollback** — DB mutations live outside graph checkpoints. Recovery recipe: **snapshot-before-repair step** (snapshot = `pg_dump --schema-only` + targeted table dumps at repair time, captured in `repair_log.before_snapshot (JSONB)` + `after_snapshot (JSONB)` per architect §3.2 record shape); compensating idempotent `DO$$` forward-fix is the recovery path, discoverable via `repair_log.created_by`. Partial application is reconstructible from `repair_log.outcome ∈ {'previewed','committed','rolled_back','error'}` + `target_table` + `nonce` + `before_snapshot`/`after_snapshot`.

**Rollout is gated on:** architect adjudication A–E approved (§1 table in `architecture-recommendation.md`); D19 (`db` grant) approved; D20 (`system_restart` deny-list resolution-level enforcement) approved; prompt-guide §10 checklist sign-off; all pin tests green (including new idempotent test 2.10(c) + dual-engine coverage 2.10(h) + `system_restart` exclusion pin test 2.10(f) on dev/demo AND live); integration test green; Wave-1 operator checklist (PG role privilege) confirmed (architect §7.6, §9); pool-config pre-flight on disposable PG passed (checklist #5).

---

## Detail-Phase Checklist (review-fix pass)

Implementation-detail items to settle before / during the corresponding phases. One line each, no rework.

1. **Kill-switch for `ens_db_repair_execute`** (Phase 2.1) — env flag (e.g. `ENSEMBLE_REPAIR_ENABLED`) + flag-OFF byte-identical pin per repo convention (writing guide §4 + testing convention "kill-switch OFF = byte-identical regression test").
2. **Dual-engine CI coverage in task 2.10** (Phase 2.10) — all repair + SELECT tests pass on BOTH SQLite-backed AND PG-backed fixtures (architect §7.5).
3. **New test `tests/unit/tools/test_ens_db_repair_idempotent.py`** (Phase 2.10(c)) — run each repair SQL twice, assert net state change == 0 — pins R14 idempotent `DO$$`-only rule.
4. **Citation fixes** (Phase 2.10) — `tests/unit/tools/test_db_tools.py:497` → `daemon/tools/db_tools.py:497`; pin exact location of `created_by` audit-stamp precedent (council disagreement: `infra.py:226-234` vs `infra/models.py:256 + infra/repository.py:540-541` — implementer MUST pin the exact location when fixing).
5. **Pool-config pre-flight** (W3 rollout step 9) — verify `pool_recycle × pool_pre_ping × reset-on-return` interaction on a disposable PG BEFORE W3 restart (architect §3.1, §10).
6. **KB-duplication assertion + explicit pause-first verify command** (Phase 3.8 + Phase 5 task 5.4 rollout step 7) — pin test 3.8 asserts KB INDEX in `memory.md` does NOT duplicate KB content (only lists triggers + verification pointers); rollout step 7 carries the explicit `curl -s -X POST http://localhost:8079/api/instances/<id>/pause` command.
