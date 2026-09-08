# Decisions: Maintenancer Agent

Date: 2026-09-08 (fold-pass v2: 2026-09-08)
Author: planner via plan-creation worker (fold-pass v2: architect adjudication folded)
Status: Draft

> **Authoritative adjudication layer:** `.agents/shared/planning/maintenancer-agent/architecture-recommendation.md`. Where this document and the architect's file disagree, the architect's verdict governs. All five OPEN decisions below carry the architect's adjudicated verdict with refinement notes; **20 MADE decisions** stand (D1–D20: D1–D13 from v1 + D2 fold-pass note + D4/D6/D12 fold-pass refinements + D14–D18 new in fold-pass v2 + D19 added in review-fix pass + D20 added in approver-fix pass). **Reconciliation (approver-fix pass):** architect §1's "13 MADE" count is a pre-fold-pass v2 snapshot — D14–D19 were added during fold/review passes (worker-as-break-glass, self-surgery refusal, 3-wave deployment, release-tag pinning, dual-pin updates, db category grant), and D20 was added in this approver-fix pass (deny-list enforcement); the architect file is intentionally not edited to preserve its adjudication-layer integrity.

---

## MADE Decisions (with rationale)

### D1 — New agent, not a v2 of gaia

- **Decision:** Create `agents/maintenancer/` from scratch; gaia untouched.
- **Rationale:** gaia is a user-facing environment-setup assistant with `team_members: []` (leaf) — unrelated to maintenance. The user explicitly confirmed: "NEW agent (user decision) — gaia stays untouched."

### D2 — v2 pattern (skill_injection + dispatcher workers)

- **Decision:** Maintenancer follows the v2 pattern: `skill_injection: true`, `innate_skills: ["dynamic-skill", "todo", "chart"]`, dispatcher style with bounded workers.
- **Rationale:** Tester/developer[v2] use this pattern; it composes with the existing skill bank + worker agent + report-sanity scrutiny test (`tests/unit/test_report_integrity_prompts.py`). Matches the user's "v2 pattern: many skills + workers (like tester / developer[v2])".

### D3 — Registry singleton → restart-required activation

- **Decision:** Adding the agent directory requires a daemon restart.
- **Rationale:** `daemon/registry.py:1170-1175` is an import-time singleton — `_registry.discover()` runs once at first `get_registry()` call. Documented in rollout runbook. Spawn deny-by-default (`instance.py:1817-1848`) ensures stale-registry spawns fail LOUDLY, not silently.

### D4 — Local KB doc set under `agents/maintenancer/knowledge/` (TIERED DESIGN per architect §6.2)

- **Decision:** KB lives at `agents/maintenancer/knowledge/*.md` (not `.agents/maintenancer/kb/*.md`). Wired via a tiered delivery — **NOT** the original "kb-curator skill auto-records on startup" (architecturally impossible — skills are static prompt text; no startup execution hook exists).

  **Tier 1 (load-bearing, guaranteed-on):** KB INDEX embedded in `agents/maintenancer/memory.md` (the loader always includes `memory.md`; ~<1k chars; one-line-per-doc triggers + verification discipline).

  **Tier 2 (content path, on-demand):** full text of `agents/maintenancer/knowledge/*.md` via filesystem `read_file` when a specific doc is needed (~1-2k tokens per retrieval, exact + current).

  **Tier 3 (best-effort, cross-session):** RAG mirror via first-turn `experience()` duty in `workflow.md` (+ `knowledge` in `tools.allow`). Probabilistic compliance — pin via grep test that asserts the first-turn directive is present.

- **Rationale:**
  - (a) The daemon's prompt loader (`daemon/loader.py:436-441`) injects ONLY the SHARED `agents/_prompt_system/knowledge.md`; there is NO built-in per-agent KB slot.
  - (b) **Skill-content inclusion of all 6 docs (~12k chars ≈ 3k tokens/turn) is REJECTED** — 15-25% of a typical system prompt, compounding toward the 700k window / 80% compaction rung (architect §6.2).
  - (c) **No startup execution exists** — neither delivery path (static prompt sections via `load_agent_skills` `loader.py:287-316`; skill-bank auto_load per-turn injection `context_messages.py:869-940`) executes code; the kb-curator skill can only *instruct*.
  - (d) **`knowledge` must be in `tools.allow`** — otherwise `experience`/`explore` don't exist for maintenancer (`knowledge_tools.py:677,915`) and the RAG leg is structurally dead.
  - (e) **`no_force_explore: true` swaps in the no-force knowledge variant** (`loader.py:210-222, :649`) — the workflow must explicitly override `no_force_explore` for the one-time first-turn mirror.
  - (f) KB index never duplicates KB content (lists triggers + verification pointers only).
- **Caveat:** If RAG is unavailable, Tier 3 is a no-op; Tier 1 + Tier 2 still guarantee-on. OPEN E explores refinements if observability shows Tier 3 frequently failing.

### D5 — Privileged-category mechanism for exclusivity

- **Decision:** Add `system-log` and `ens-db` to `PRIVILEGED_TOOL_CATEGORIES` at `daemon/tools/_tool_registry.py:106-108`. De-scope `system-log` from `agents/developer/meta.json` and `agents/wanderer/meta.json` `tools.allow`.
- **Rationale:** R-SR16 / P2.2 tool-api-design §3.5 establishes the privileged-category mechanism precisely for this use case. Adding to the frozenset strips these categories from the empty-allow + default-allow paths (`_strip_privileged_category_tools` at `instance.py:4497-4514`), so they reach ONLY agents with an explicit allow entry. Defensive and structurally enforced. **Note:** `TOOL_REQUIRED_AGENTS` (spawn-side) is the WRONG mechanism for this exclusivity — the privileged-category frozenset is the right one.

### D6 — SELECT-only default for `ens_db_postgres_select`; gated writer separate

- **Decision:** Two-tool split: (a) `ens_db_postgres_select` runs SELECT-only enforced by `_validate_select_only` (`db_tools.py:97`); (b) `ens_db_repair_execute` is a separate tool with three gates — explicit confirmation, audit stamp (`created_by=current_instance_id` per `infra.py:226-234`), dry-run-first toggle.
- **Rationale:** Mirrors the precedent at `db_tools.py` (read tools vs write tools are separate). The three gates are independent — all must clear.

### D7 — Worker dispatch contract: cap 3 concurrent, fan-in valve, report-sanity scrutiny

- **Decision:** `workflow.md` enforces: `send_message(load_skill="<one>")`, cap 3 concurrent workers, fan-in escape valve (1 re-dispatch cap + `[incomplete]` + `### Gaps`), report-sanity scrutiny conditioned on `[REPORT SANITY: …]` marker.
- **Rationale:** Writing guide §7 + tester pattern + `tests/unit/test_report_integrity_prompts.py` enforcement.

### D8 — Cardinal rules ≤7

- **Decision:** `rule.md` has 7 cardinals (proposed): (1) Read KB before action; (2) Confirm before destructive operation; (3) End turn after dispatch; (4) **Never run `system_restart` — `system_restart` is in `tools.deny` (resolution-level strip at `resolve_tool_filter` time per approver-fix pass; see D20)**; never bypass the 3-factor nonce gate on `system_upgrade`; relay any user nonce verbatim without agent-side fabrication (per OPEN A); (5) Report sanity scrutiny on every worker report; (6) SELECT-only on `ens_db_postgres_select`; (7) Audit-stamp every repair row.
- **Rationale:** Writing guide §3 — models obey short top-of-context rules better than long enumerated walls. Cardinal #4 now reflects the **resolution-level** enforcement point (deny-list strip) rather than prose-only ("never `system_restart`") so the rule survives category-grant expansions and dev/demo installs (approver-fix pass).

### D9 — Cross-reference hygiene (convention v2)

- **Decision:** All prompt-surface cross-references use section-name form (`See <Section>` for same-agent, `See <agent>'s <Section>` for cross-agent). No path tokens.
- **Rationale:** Writing guide §3 (convention v2, 2026-09-08) is mandatory; closure grep `\.md|workflow\.md|rule\.md|...` over prompt surfaces must return zero hits on cross-references.

### D10 — `meta.json` deny-list for safety-critical tools

- **Decision:** `agents/maintenancer/meta.json` carries `tools.deny: ["git_commit", "edit_file", "write_file"]` even though no skill should invoke these.
- **Rationale:** Writing guide §4 — defense-in-depth; a worker whose skill fails to load still cannot mutate state via these tools. Mirrors the precedent in tester/developer.

### D11 — KB docs ≤2k chars each; total ≤12k chars across six docs

- **Decision:** Each KB doc stays under ~2k chars; the six combined stay under 12k chars.
- **Rationale:** Mitigates the prompt-token-budget risk when Tier 2 reads are in effect. Forces tight digests over verbose tutorials. Combined with D4 (Tier 1 INDEX in `memory.md` only; full content via Tier 2 filesystem on-demand), the prompt never holds more than the ~<1k INDEX + the agent's actively-queried doc.

### D12 — `last-verified-against: <release-tag>` header on every KB doc (REFINED per architect §6.3)

- **Decision:** Every KB doc carries a header `last-verified-against: <release-tag>` at the top.
- **Rationale:** Mitigates R6 (KB rot). The pin test asserts the header equals the **merge-base release tag** at KB-file-change time — NOT a rolling SHA (this repo merges 20+/day; a rolling pin would warn perpetually and train the agent to ignore warnings). **Pin test at merge time is deterministic; runtime divergence is warn-only** (refuse-to-cite is unenforceable against an LLM — architect §6.3).

### D13 — `no_force_explore: true` in `meta.json`

- **Decision:** Set `no_force_explore: true` (mirroring worker + leader).
- **Rationale:** Writing guide §1 + worker/leader convention. Maintenance work is targeted and uses the loaded KB; force-explore on every prompt wastes tokens. **Fold-pass v2 caveat (architect §6.1):** the workflow must explicitly override `no_force_explore` for the one-time first-turn RAG mirror (`experience()` call for KB mirroring); the override is a workflow rule, not a meta.json setting.

### D14 — Worker formalized as designated `system-log` break-glass (architect §4.5, §7.1)

- **Decision:** Worker keeps `system-log` in its allow-list — formally documented as the **designated break-glass**, not as a "leak." Workers NEVER hold `ens-db` (DB write path closed at the worker layer; pin in exclusivity test).
- **Rationale:**
  - (a) Exclusivity's goal is "maintenancer HAS the tools," not "logs are unreadable elsewhere" (architect §4.5).
  - (b) Stripping worker breaks log-driven debugging for 12 dispatcher agents; a dedicated `maintenance-worker` (Opt 3/4) buys prompt-discipline that code cannot enforce.
  - (c) Worker retention is the SPOF backstop at incident time when maintenancer is ERROR or its instance row was killed.
  - (d) Runbook formalizes the break-glass role: leader re-spawns on maintenancer ERROR (revive semantics re-register watchers); re-grant = meta allow + restart via pause-first runbook; raw-bash fallback documented WITH the caveat that raw reads bypass `ens_system_log_*` redaction.
  - (e) Workers NEVER get `ens-db` — DB write path is closed at the worker layer; pin in `tests/unit/tools/test_privileged_category_system_log.py` (architect §4.5 final paragraph).

### D15 — Self-surgery refusal in `ens_db_repair_execute` (architect §7.2)

- **Decision:** `ens_db_repair_execute` guard refuses targets matching the calling instance's own rows (e.g., refusals to write into `instances` rows where `id == current_instance_id`). Workflow rule forbids repairing own state.
- **Rationale:** Self-governance — repair summaries route to leader/user in every final report (report-sanity scrutiny); `repair_log.created_by` rows have no other reviewer. The cardinal rule "confirm before destructive operation" (D8 Cardinal #2) covers user-facing confirm; self-surgery refusal covers the LLM-against-itself case.

### D16 — 3-wave deployment; P1-before-P2 merge-order invariant; ONE restart window (architect §2.3)

- **Decision:** Phase structure (P1–P5) preserved for planning. **Wave structure** governs execution + restart ordering:

  | Wave | Scope | Restart? | Merge-order invariant |
  |------|-------|----------|----------------------|
  | W1 | P1 ∥ P2 ∥ P3 (file-disjoint) | NO | **P1 must merge before P2** — any crash-restart mid-wave lands coherent state |
  | W2 | P4 (skills + team_members + dispatcher pins) | NO | needs P1's `team_members` + P3's section names |
  | W3 | P5 (docs + runbook + integration test) | YES (single restart) | all pin tests + exclusivity asserts + smoke-spawn |

- **Rationale:** A standalone restart inside Phase 1 (the v1 plan's task 1.10) forces two restarts and creates a 4-holder exclusivity window between the two restarts — restart #1 registers maintenancer WITH `system-log` while developer/wanderer/worker still hold it (architect §2.2). The 3-wave structure collapses to ONE restart window in Wave 3 and honors the P1-before-P2 merge-order invariant so any crash-restart mid-wave lands coherent state.

### D17 — Release-tag pinning for KB doc headers (architect §6.3)

- **Decision:** KB doc `last-verified-against` headers pin to release tags (immutable), not rolling SHAs.
- **Rationale:** This repo merges 20+/day; a rolling-SHA pin would warn perpetually and train the agent to ignore warnings. Pin test at merge time is deterministic; runtime divergence is warn-only.

### D18 — Dual-pin same-PR updates (architect §4.4)

- **Decision:** When the `PRIVILEGED_TOOL_CATEGORIES` frozenset is extended, both exact-equality pin tests are updated in the SAME PR as the frozenset change:

  - `tests/unit/tools/test_upgrade_registration.py:100` — assert `PRIVILEGED_TOOL_CATEGORIES == frozenset({"system_upgrade"})` → update to `frozenset({"system_upgrade", "system-log", "ens-db"})`.
  - `tests/unit/tools/test_attestation_registration.py:154` — same update.
- **Rationale:** These are deliberate "silent additions visible" pins (architect §4.4). Updating them in the same PR is the mechanism working as designed, not test debt.

### D19 — Grant `db` category per user verbatim clause (review-fix pass)

- **Decision:** `agents/maintenancer/meta.json` `tools.allow` INCLUDES `"db"` (in addition to `system-log`, `ens-db`, `knowledge`, `system_upgrade`).
- **Rationale:** User verbatim clause from the original dispatch: **"it have general db tools too"**. The `db` category provides general database tooling (existing `db_conn_add/delete/list/test + db_postgres_dml_select` per `daemon/tools/db_tools.py`).
- **Standing MUST-NOT (Risk R13, preserved):** **`ensemble_prod` is NEVER registered into `ConnectionPoolManager`** — the `db` category resolves by `connection_name` against the **user-facing** connection registry (external user-registered connections). A synthetic `ensemble_prod` registration would make the daemon's own DB SELECT-able by every `db`-category holder — bypassing the `ens-db` exclusivity this plan exists to build. The daemon's own DB is reached ONLY via `ens_db_*` tools on the shared engine.
- **Pin test:** `tests/integration/test_maintenancer_spawn_resolves_tools.py` (task 2.10) asserts (a) `db` category resolves for maintenancer (external connections available), (b) `db` tool set does NOT include any synthetic `ensemble_prod` connection (Risk R13 standing guard), (c) `ens_db_*` tools remain the ONLY path to `ensemble_prod`.

### D20 — `system_restart` denial is the resolution-level enforcement (approver-fix pass)

- **Decision:** `agents/maintenancer/meta.json` `tools.deny` INCLUDES `"system_restart"` (in addition to `"git_commit"`, `"edit_file"`, `"write_file"`). The deny-list strip at `resolve_tool_filter` time is the **primary** enforcement; the call-time refusal at `daemon/tools/upgrade_tools.py:1458-1465` (`if self_env == "live"`) is **defense-in-depth only**.
- **Rationale (verified at source):** `system_restart` carries `@register_tool_category("system_upgrade")` at `daemon/tools/upgrade_tools.py:1435` — granting the `system_upgrade` category in `tools.allow` therefore expands to include `system_restart`. The deny-list is the **only** resolution-level mechanism that survives category-grant expansions. The call-time refusal at `upgrade_tools.py:1458-1465` is `if self_env == "live"` — env-conditional — and does NOT fire on dev/demo installs (where the tool would otherwise resolve AND execute).
- **Survives:** (a) category-grant expansions of `system_upgrade`; (b) dev/demo installs where `if self_env == "live"` does not fire; (c) future additions to the `system_upgrade` category that inherit `system_restart`'s tag.
- **Pin test:** `tests/integration/test_maintenancer_spawn_resolves_tools.py` (task 2.10(f)) asserts `system_restart` is NOT in the resolved tool set on BOTH dev/demo AND live fixtures (approver-fix pass directive). The call-time test asserts the live-env refusal message is still emitted as belt + suspenders.
- **Cross-references:** task 1.2 (deny-list authoring), task 2.8(b) (verification step), task 2.10(f) (pin test), SC#9 (success criterion), D8 Cardinal #4 (agent-facing rule wording), OPEN A condition (a) (architect-adjudicated recommendation), Rollout/rollback sections (operational enforcement point).

---

## OPEN Decisions (for the user)

> All five OPEN decisions carry the architect's adjudicated verdict with refinement notes (architect §1 table + §3.1+§3.2+§3.4+§4.5+§4.6+§5.1+§5.3+§6.1+§6.2). Verdicts below reflect the architect's final adjudication; the user's `Approve all defaults` response accepts these.

### OPEN A — Restart/Upgrade autonomy for maintenancer (ADJUDICATED per architect §5.1+§5.3)

**Context.** Today, live `system_restart` and `system_upgrade` are ari-only via the 3-factor gate (`daemon/tools/upgrade_tools.py:1877-2036`): (F1) `user_confirmed`; (F2) per-instance user-origin window stamped ONLY from dispatch-funnel message **source** (`manager.py:6848-6853` → `:3238-3275`; whitelist = exact `api` + `telegram:/webhook:/whatsapp:/discord:/slack:` prefixes, `upgrade_journal.py:1076-1084`); (F3) single-use, 15-min-TTL, **instance-bound** (`issued_to_instance`), **action-bound** (kind/env/target) nonce found in the triggering message row content. `system_restart` deliberately refuses live (`upgrade_tools.py:1459-1465`, before any gate logic). The user said "auto restart, upgrade" for maintenancer, but the existing 3-factor gate is the load-bearing safety mechanism — removing it for one agent defeats its purpose system-wide.

**Decisive nuance (architect §5.1):** the gate is satisfiable by a legitimate maintenancer — `dry_run` mints a nonce bound to ITS OWN instance (`upgrade_tools.py:1870-1886`); a user message to maintenancer within 15 min echoing the nonce arms the live upgrade. So Option 1 grants real per-op-authorized autonomy: maintenancer detects → prepares → dry-runs → mints nonce → asks the user to echo it in-thread → executes. This matches the user's "auto restart, upgrade" intent while every live action remains human-authorized per-op.

**Options:**

1. **Share the privileged category with the same nonce gate** (architect refinement of original Option 1) — grant `system_upgrade` (NEVER `system_restart` which refuses live at `upgrade_tools.py:1459-1465`); live calls still require the 3-factor gate. "Auto restart" means **auto prepare + queue + prompt ari to confirm** rather than auto execute.
   - *Trade-off:* Centralizes maintenance while honoring the existing safety mechanism. ari remains the only "user-confirmed" path. Risk: nonce-replay window if the gate is mis-implemented. **Architect's monitor requirement:** watch `upgrade_promote_refusal` journal events for any deviation.

2. **Dry-run / prepare only.** Maintenancer can run `upgrade_dry_run`, prepare transactions, sweep stale state, but every live action requires a human relay through ari.
   - *Trade-off:* Safe. No real "auto restart" — only "auto prepare". Operational latency.
   - *Risk:* Doesn't fully match user intent.

3. **Full autonomy.** Maintenancer calls `system_restart`/`system_upgrade` with a confirmation prompt UI only; bypass the 3-factor gate.
   - *Trade-off:* Matches user intent literally.
   - *Risk: HIGH.* The 3-factor gate was specifically designed to prevent autonomous restart/upgrade; removing it for one agent defeats its purpose across the system. **NOT recommended.**

**Recommendation (architect §5.3 + approver-fix pass):** **Option 1 with conditions:**
- (a) grant `system_upgrade` ONLY — NEVER `system_restart`. **Enforcement point (approver-fix pass):** `system_restart` carries `@register_tool_category("system_upgrade")` at `daemon/tools/upgrade_tools.py:1435` — a category-level grant of `system_upgrade` therefore expands to include `system_restart`. The **resolution-level enforcement** is the `tools.deny: [..., "system_restart"]` entry in `agents/maintenancer/meta.json` (per D20) — `resolve_tool_filter` strips it before any call-time check runs. The call-time refusal at `upgrade_tools.py:1458-1465` (`if self_env == "live"`) is **defense-in-depth only** because it is env-conditional and does NOT fire on dev/demo installs.
- (b) Cardinal rule: relay the nonce verbatim, never fabricate/echo it agent-side;
- (c) monitor `upgrade_promote_refusal` journal events;
- (d) any future gate loosening (instance-binding removal, TTL extension, bulk row reads) must re-run the full refusal test suite.
- Option 3 remains rejected (planner correct).

**Upgrade-ladder boundary (architect §5.4):** maintenancer stays strictly below Layer 1 of the live-rung gate — it may read journal/state, run `upgrade_status`/`upgrade_dry_run` (demo/sandbox only — cross-env structurally refused); it can NEVER set `ENSEMBLE_UPGRADE_LIVE` (executor env allowlist strips it, `upgrade_journal.py:986-990`), pass `--f2-verified-closed` (argv-only, `promote.sh:103`), write ledger cycle state (`ledger_check.py:277` hard-BLOCKS on F2-open before count logic), or run S4–S6. Defense-in-depth holds even under full gate compromise: the executor spawns through the env-stripped allowlist and `require_live_guard` exits 78 (`lib.sh:327-341`).

**Autonomy ladder (architect §5.2):**
- **Tier 1 (fully autonomous):** diagnose; `ens_db_postgres_select`/`ens_db_inspect` (SELECT-guarded); job/mission read-model repair prep; DLQ **inspection**; upgrade dry-run/prep (demo/sandbox); KB updates; worker dispatch (cap 3).
- **Tier 2 (leader/user-confirmed):** `ens_db_repair_execute` DML/DDL (nonce+TTL+audit gates, §3.2-3.3); `terminate_instance` on stuck instances (existing tool, `instance.py:4113`); DLQ **replay** (DEAD→QUEUED `job_state_machine.py:60` re-delivers user-visible content); self-pause via `ask_user`.
- **Tier 3 (human-only):** live restart (code-refused); live upgrade arm (nonce = human origin); `promote.sh` S4–S6 (ADR-017); upgrade-ledger ops (`--f2-verified-closed`, cycle state); HTTP pause-first on OTHER instances (`routers/instances.py:647-669` — no tool wraps `pause_instance_cascade`).

**Recommended default if no response:** Option 1 with conditions (a)-(d) above.

---

### OPEN B — Log exclusivity scope (worker agent) (ADJUDICATED per architect §4.5)

**Context.** Removing `system-log` from developer/wanderer/developer[v2] closes the obvious leak — but the generic **worker agent** (`agents/worker/meta.json`) holds `system-log` via explicit allow AND is spawned by EVERY dispatcher including maintenancer's own. Worker also holds a broad allow-list including `bash` / `proc` / `filesystem`. So even if maintenancer has log forensics centralized, its own dispatched workers can read logs directly. The **watcher** agent (empty allow → default-open hole closes when `system-log` becomes privileged) is the R-SR16 design intent.

**Architect's five-axis adjudication:**

| Option | Complexity | Scalability | Maintainability | Risk | Cost |
|---|---|---|---|---|---|
| 1 keep worker, accept leak | Low | High | High | Low-Med (leak bounded) | Low |
| 2 strip worker | Low | Med | Med | Med (12 dispatchers lose debugging loops) | Low |
| 3 dedicated maintenance-worker | Med | High | Med | Low | Med-High |
| 4 hybrid | Med-High | High | Low-Med (soft discipline) | Low-Med | Med |

**Options:**

1. **Keep worker as-is; formalize as designated break-glass.** Worker remains the universal escape hatch; "centralization" is best-effort. **Workers NEVER get `ens-db`** (DB write path closed at the worker layer — pin in `tests/unit/tools/test_privileged_category_system_log.py`).
   - *Trade-off:* Simple; worker is intentionally generic. Worker retention is ALSO the SPOF backstop at incident time (§7.1).
   - *Risk:* A worker dispatched by maintenancer to do X may incidentally pull logs; the centralization is leaky (low-med, bounded).

2. **Strip `system-log` from worker.** Worker becomes log-blind.
   - *Trade-off:* Forces all log work through maintenancer.
   - *Risk:* A worker dispatched by developer (non-maintenance) can no longer self-investigate logs; developer must first dispatch through maintenancer or through a dedicated log-worker.

3. **Create a dedicated `maintenance-worker` agent** (spawn-cap only by maintenancer via `team_members`) with `system-log` + `ens-db`; worker remains general but log-blind.
   - *Trade-off:* Clean separation.
   - *Risk:* Another agent directory + registration + restart; cost-benefit needs justification (deferred to usage-evidence-triggered follow-up).

4. **Hybrid:** Keep worker with `system-log` but document that any worker reading logs should report evidence back to its dispatcher; maintenancer dispatches `maintenance-worker` for log-touching tasks; everyone else goes through general worker.
   - *Trade-off:* Best of both worlds.
   - *Risk:* More wiring; two worker types to track.

**Recommendation (architect §4.5):** **Option 1** — keep worker with `system-log`; formalize as designated break-glass. Exclusivity's goal is "maintenancer HAS the tools," not "logs are unreadable elsewhere." Worker retention is simultaneously the incident-time break-glass. **File the `maintenance-worker` split as a usage-evidence-triggered follow-up** (trigger threshold PENDING — architect §9). Consequence for the plan: `team_members: ["explorer", "worker", "coder"]` stands; worker keeps `system-log` but NEVER `ens-db` (workers cannot write DB at all — pin in the 2.10 exclusivity test).

**Recommended default if no response:** Option 1 — keep worker with `system-log` for now (lowest cost), file a follow-up to revisit when the `maintenance-worker` split is justified by usage.

---

### OPEN C — `ens_db_*` write policy and pool sizing (ADJUDICATED per architect §3.1+§3.2+§3.3)

**Context.** The shared sync engine (`daemon/manager.py:438-460`, psycopg pool 5+10 pre-ping via `repositories/factory.py:202-208`) serves ~16 repositories. Adding `ens_db_*` tools that hammer this engine risks repository-side pool starvation. Repairs may also need controlled DDL/DML — pure SELECT-only blocks legitimate maintenance work.

**Engine topology (architect §3.1, "Option 4′"):**

| Path | Engine | Pool | Timeouts |
|------|--------|------|----------|
| `ens_db_postgres_select`, `ens_db_inspect` | shared `manager.engine` (ONE engine daemon-wide) | existing 5+10 pre-ping | per-tx `SET LOCAL statement_timeout` inside the tool's own connection block + client-side `asyncio.wait_for` belt |
| `ens_db_repair_execute` | **dedicated** `create_engine` | **2+3**, `pool_pre_ping=True`, `pool_recycle=3600` | engine-level connect_args: `statement_timeout=60s`, `lock_timeout=10s` (initial — re-tune per §10), `idle_in_transaction_session_timeout=60s` |
| audit | **same connection as the repair** | — | same transaction; **fail-closed** |

**Why not the ConnectionPoolManager/asyncpg path** (the `db_postgres_dml_select` precedent, `db_tools.py:487-530`): those tools resolve by `connection_name` against the user-facing connection registry, and the `db` category is NOT privileged. A synthetic `ensemble_prod` registration would make the daemon's own DB SELECT-able by every `db`-category holder — bypassing the `ens-db` exclusivity this plan exists to build. Rejected unless a hidden-connection mechanism is added (not justified at this scope).

**Thread-bridge caveats** (shared-engine reads): `asyncio.wait_for(asyncio.to_thread(...))` cancels the awaitable but does NOT abort the blocking psycopg call — the worker thread runs to server-side timeout. Hence server-side `statement_timeout` is the real cap (scoped via `SET LOCAL` in the tool's own tx — never engine-wide connect_args on the SHARED engine, which would cap every repository operation). `CancelledError` propagates via bare awaits (message_tap precedent); never wrap in `except BaseException`.

**Pool isolation does not isolate locks:** DDL holds `ACCESS EXCLUSIVE` — lock waits block all sessions regardless of pool. The dedicated pool isolates *connection* starvation only; the timeout posture above is what bounds lock blast radius. `pool_recycle=3600` + periodic `ANALYZE` mitigates the inherited PG planner-cache trap (generic-plan seq-scans on long-lived connections, `docs/runbooks/checkpoint-blob-prune-restore.md` §10).

**Audit trail (architect §3.2) — SAME TRANSACTION, fail-closed:** mirror the `InfraAssetHistory` pattern (`daemon/repositories/infra/models.py:301-394`: append-only, JSONB snapshot, `changed_by`, indexed on `(target, timestamp)`). New `repair_log` table via `SQLModel.metadata.create_all` + `_ensure_postgres_columns` — NEVER via the migration runner (SQLite-only by design, `daemon/migrations/runner.py:486-491`). **Audit INSERT in the SAME `BEGIN/COMMIT` as the repair, same connection.** A separate audit connection creates two fatal states — repair committed + audit failed (un-audited prod change), or repair rolled back + audit committed (false attribution). **Audit-write failure ⇒ rollback. Fail-closed.**

**Options:**

1. **Shared engine, SELECT-only.** All `ens_db_*` tools go through the same engine; SELECT enforced by `_validate_select_only`.
   - *Trade-off:* Safest; no risk to repositories.
   - *Drawback:* Cannot run repairs at all — a maintenance agent that can't write is a reader.

2. **Shared engine, SELECT + gated DML/DDL.** As D6 above (gated repair tool).
   - *Trade-off:* Maintenance works; pool still shared.
   - *Risk:* Pool starvation on heavy repairs.

3. **Dedicated small pool.** `ens_db_*` tools use a separate `create_engine` mirror with smaller pool (e.g. 2+3).
   - *Trade-off:* Isolation from repositories.
   - *Risk:* Two engines = two connection pools to the same DB; transaction coordination harder.

4. **Hybrid: shared engine for SELECT, dedicated small pool for repair writer + same-tx audit.**
   - *Trade-off:* Cleanest separation; one writer doesn't starve N readers; audit atomicity preserved.
   - *Risk:* Slight complexity.

**Recommendation (architect §3.1+§3.2):** **Option 4′ (refined hybrid)** — shared engine for `ens_db_postgres_select` (read-heavy, no starvation concern); dedicated small pool (2+3) for `ens_db_repair_execute` (write-heavy, isolated). Audit on the same connection as the repair, same transaction, fail-closed. `ens_db_inspect` exposed to the shared engine (read-only, no audit needed).

**Recommended default if no response:** Option 4′.

---

### OPEN D — Watcher default-universe change side-effect (ADJUDICATED per architect §4.6)

**Context.** Watcher has empty `tools.allow` (`agents/watcher/meta.json`); under R-SR16 the default-allow path grants all categories EXCEPT privileged ones. When `system-log` becomes privileged, watcher silently loses it. This is by design — watcher should NOT have `system-log` access without explicit intent.

**Options:**

1. **Accept the change.** Watcher no longer has `system-log`; no other side effects.
   - *Trade-off:* Correct under R-SR16.
   - *Risk:* If any watcher path actually depended on `system-log` (none currently do per the architect's audit), it would break silently.

2. **Compensate by adding `system-log` to watcher's explicit allow.**
   - *Trade-off:* Preserves prior behavior.
   - *Drawback:* Watcher is a security evaluator — granting it log forensics is the OPPOSITE of the centralization intent.

3. **Audit watcher code paths for log-tool use first, then decide.**
   - *Trade-off:* Most thorough; adds work.
   - *Risk:* Time.

**Recommendation (architect §4.6):** **Option 3 → Option 1** — audit first (cheap grep), then Option 1 unless a real dependency is found. The audit is a one-time grep across `agents/watcher/` (all 6 files), `daemon/services/watchover_service.py`, and any watcher-related tests, plus a commit-comment recording the result. **Architect's audit result:** zero `system-log`/`ens_system_log` references across `agents/watcher/` (all 6 files) and `daemon/services/watchover_service.py`. Watcher's empty allow strips it at `:302-308`/`:4547`/`:4583`. Record the one-line commit comment; nothing else.

**Recommended default if no response:** Option 1 — accept the change (audit re-verified clean per architect §4.6).

---

### OPEN E — KB doc-set composition path (ADJUDICATED per architect §6.1+§6.2; tiered design)

**Context.** The daemon's prompt loader (`daemon/loader.py:436-441`) injects ONLY the shared `agents/_prompt_system/knowledge.md`. There is NO built-in per-agent "knowledge slot". **Skills are STATIC prompt text** (delivered via `load_agent_skills` `loader.py:287-316`; or skill-bank auto_load per-turn injection `context_messages.py:869-940`) — **no startup execution hook exists**. The original "auto-record on startup" capability is architecturally impossible and is removed.

Options for getting KB docs into the prompt:

- **(a) Skill content inclusion** — ship the KB docs as `skills-template/*.md` content; the loader assembles them at `daemon/loader.py:407-413`. **REJECTED for full content** — 12k chars ≈ 3k tokens/turn = 15-25% of a typical system prompt, compounding toward the 700k window / 80% compaction rung.
- **(b) RAG `experience()`** — requires `knowledge` in `tools.allow`; `no_force_explore: true` must be overridden for the one-time first-turn mirror.
- **(c) Filesystem reads by the agent** — the agent reads `agents/maintenancer/knowledge/*.md` via bash/filesystem tools when it needs them. No prompt-size cost; requires the agent to know the path.
- **(d) Tiered combination** — primary load-bearing INDEX in `memory.md` + filesystem full-text + RAG first-turn mirror.

**Options:**

1. **Skill content only.** Guaranteed-on; no RAG dependency; consumes prompt tokens (rejected by §6.2 for full 12k content).
2. **RAG only.** Cross-session; depends on RAG backend.
3. **Filesystem reads only.** No prompt cost; relies on agent remembering the path.
4. **All three at full strength.** Maximum coverage; highest maintenance cost (rejected — startup recording impossible, full inclusion expensive).
5. **Modified Option 4 (tiered).** Primary load-bearing INDEX in `memory.md` + filesystem full-text + RAG first-turn duty (requires `knowledge` in allow).

**Recommendation (architect §6.1+§6.2):** **Modified Option 4 (tiered)** — three roles:

  | Tier | Mechanism | Role | Cost |
  |---|---|---|---|
  | **Primary (load-bearing)** | KB **INDEX** embedded in `memory.md` (task 1.7 slot): doc map + one-line-per-doc triggers + verification discipline | guaranteed-on, ~<1k chars | negligible |
  | **Content path** | `agents/maintenancer/knowledge/*.md` full text via `read_file` on demand | exact, current, zero idle cost | ~1-2k tokens per retrieval |
  | **Best-effort** | RAG mirror via first-turn `experience()` duty in `workflow.md` (+ `knowledge` category in allow) | cross-session recall | probabilistic |

**Required adjustments to enable this design:**
- **Add `knowledge` to `tools.allow`** — otherwise `experience`/`explore` don't exist for maintenancer (`knowledge_tools.py:677,915`).
- **Override `no_force_explore: true`** for the one-time first-turn mirror in `workflow.md` (`loader.py:210-222, :649` swaps in the no-force knowledge variant).
- **Index never duplicates KB content** — it lists triggers + verification pointers only.
- **KB `last-verified-against` headers pin to RELEASE TAGS** (architect §6.3), not rolling SHAs.

**Recommended default if no response:** Modified Option 4 (tiered).


---

## Open Question Summary (for the user)

| # | Decision | Architect verdict | Default if no response |
|---|----------|-------------------|------------------------|
| **A** | Restart/upgrade autonomy | **Option 1-refined** — grant `system_upgrade` ONLY (never `system_restart`); same 3-factor gate; instance-bound, satisfiable via in-thread user nonce echo (`upgrade_tools.py:1870-1886`); conditions: (a) never `system_restart`, (b) Cardinal rule: relay nonce verbatim, never fabricate, (c) monitor `upgrade_promote_refusal` events, (d) any gate loosening re-runs full refusal test suite | Option 1-refined |
| **B** | Log exclusivity scope (worker) | **Option 1** — keep worker's `system-log` (designated break-glass); workers NEVER `ens-db` (DB write path closed at worker layer — pin in exclusivity test); `maintenance-worker` split deferred to usage-evidence-triggered follow-up (trigger threshold PENDING) | Option 1 |
| **C** | `ens_db_*` write + pool | **Option 4′ (refined hybrid)** — shared-engine SELECT + dedicated 2+3 repair pool (`pool_pre_ping=True`, `pool_recycle=3600`, connect_args `statement_timeout=60s`, `lock_timeout=10s` initial, `idle_in_transaction_session_timeout=60s`); per-tx `SET LOCAL statement_timeout`; audit SAME TRANSACTION fail-closed (drops separate audit connection); idempotent `DO$$`-only; self-surgery refusal; repository-methods-first rule + dry-run warning; NO synthetic `ensemble_prod` ConnectionPoolManager registration | Option 4′ |
| **D** | Watcher side-effect | **Option 1** — accept (architect audit re-verified clean — zero `system-log`/`ens_system_log` references across `agents/watcher/` (all 6 files) and `daemon/services/watchover_service.py`); record one-line commit comment | Option 1 |
| **E** | KB composition path | **Modified Option 4 (tiered)** — KB INDEX in `memory.md` (load-bearing, ~<1k chars) + filesystem full-text reads (content path) + RAG first-turn `experience()` duty (best-effort, requires `knowledge` in allow); full 12k-char skill-content inclusion REJECTED (~3k tokens/turn); `last-verified-against` pinned to release tag (not rolling SHA) | Modified Option 4 |

**Pending items (architect §9, §10 — deferred or operator-side):**

- **Maintenance-worker split trigger threshold** — define the usage-evidence threshold (e.g. N log-touching worker dispatches/month by non-maintenancer dispatchers) that justifies the split.
- **PG role privilege check** — Wave-1 operator must confirm daemon PG role holds CREATE/ALTER/INSERT/UPDATE/DELETE on `ensemble_prod` BEFORE repair DDL is enabled.
- **`lock_timeout` post-soak re-tune** — initial 10s; re-tune against prod lock-wait distribution.
- **`pool_recycle × pool_pre_ping × reset-on-return` empirical verification** — verify on disposable PG (SQLAlchemy 2.x documented-correct, not yet verified here).

Please respond with `Approve all defaults` (or list overrides) so the implementer can proceed without re-asking.
