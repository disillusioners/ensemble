# Approver Tracking: Maintenancer Agent

Plan: Maintenancer Agent (new centralized repair/maintenance agent)
Slug: maintenancer-agent
First approval cycle: 2026-09-08
Plan base: 9fe74bc7 (latest) — branch plan/maintenancer-agent

---

## Iteration 001 — 2026-09-08

**Verdict:** REJECTED
**Worker:** d5fb9f5e-d266-4cfa-b6f0-021edd660dd2 (plan-approval)
**Artifact paths verified:**
- /Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-maintenancer-agent/.agents/shared/planning/maintenancer-agent/plan-overview.md (440 lines)
- /Users/.../decisions.md (342 lines)
- /Users/.../architecture-recommendation.md (268 lines)

### Blocking Issues (1)

1. **Safety / Consistency — `system_restart` exclusion is not actually enforced by the resolved tool set.**
   - Sections affected: plan-overview.md Task 1.2 (acceptance), Task 2.8(b), Task 2.10(f), Success Criterion #9; decisions.md D8 / OPEN-A condition (a); architecture-recommendation.md §1 table OPEN A.
   - Expected: Resolved tool set for maintenancer EXCLUDES `system_restart`-derived live-restart tools, pinned by test.
   - Found: `system_restart` carries `@register_tool_category("system_upgrade")` (`daemon/tools/upgrade_tools.py:1435-1437`, alongside `system_upgrade` at `:1715-1717`). Explicit allow of the `"system_upgrade"` category therefore expands to every category tool — including `system_restart`. No resolution-time exclusion path exists in `daemon/tools/instance.py`, `_tool_registry.py`, or `_auth.py`; the only override is deny (deny-wins at `instance.py:316-322`), and task 1.2's deny-list is `["git_commit","edit_file","write_file"]` — `system_restart` is NOT in it. The plan's fallback ("code-refused per `upgrade_tools.py:1459-1465`") is a call-time, env-conditional refusal (`if self_env == "live"`), NOT a resolution-time exclusion — on dev/demo installs the tool resolves and executes.
   - Concrete fix: add `system_restart` to `tools.deny` in task 1.2; re-scope 2.8(b) / 2.10(f) / SC#9 to assert deny-enforcement plus the live-env call-time refusal.

### Notes (non-blocking — worker's classifications retained per Cardinal #4)

- **Stale test citation:** SC#4 and R2 cite `tests/unit/tools/test_db_tools.py:497` — that file does not exist. Detail-Phase Checklist #4 already orders the fix (`→ daemon/tools/db_tools.py:497`); the actual precedent suite to mirror is `tests/test_db_select_guard.py` (worker verified present and comprehensive).
- **Citation drift:** task 2.3 cites `CATEGORY_MODULES` at `_tool_registry.py:494`; the dict is defined at `:459` (architect cites it correctly). Cosmetic.
- **Cardinal #4 wording drift:** decisions.md D8 frames it as "Never run live restart/upgrade without 3-factor gate"; plan-overview 1.4 frames it as "Relay nonce verbatim." Task 4.4 owns final wording — same OPEN A substance, no material conflict.
- **Decision-count reconciliation:** architect §1 says "13 MADE decisions stand" vs decisions.md "19" — explained by decisions.md's own header lineage (D14–D18 fold-pass, D19 review-fix). Versioning artifact, not contradiction.
- **Pre-fold numbering:** architect §4.3 references "success criterion #2"; the folded SC table carries the de-scope criterion at #3. Substance aligned (base + versioned metas now enumerated).
- **Stale enrichment hook:** Phase-2 hook "whether `repair_log` needs a new migration or existing audit table" is superseded by architect §3.2's adjudication (new table via `SQLModel.metadata.create_all` + `_ensure_postgres_columns`, never the migration runner). Plan header assigns the architect file governing authority.
- **Pending operator items:** PG role privileges, `lock_timeout` re-tune, `pool_recycle × pre_ping × reset-on-return` verification, maintenance-worker split threshold — explicitly owned, operator-gated, roll into rollout gates.

### Worker Positive Observations (carried forward to next iteration)

- ~30 load-bearing file:line claims independently reproduced against the worktree and verified accurate (privileged-category mechanism, dual exact-equality pins, 3-factor gate mechanics, registry singleton, engine topology, loader/KB constraints, watcher audit, de-scope targets, "12 dispatchers" count).
- Watcher audit clean: zero `system-log` / `ens_system_log` refs across exactly 6 `agents/watcher/` files + `watchover_service.py`.
- Dual exact-equality pins reproduced at `test_upgrade_registration.py:100` and `test_attestation_registration.py:154`.
- 5+10 pre-ping pool at `factory.py:202-208`; `DO$$` recipe at `manager.py:4996-5012`; migration-runner SQLite-only behavior at `runner.py:486-491`; gate primitives at `upgrade_journal.py:986-990` / `:1076-1084`, `promote.sh:103`, `lib.sh:327-341` exit 78, `ledger_check.py:277`.
- "12 dispatchers" count verified: exactly 12 agents hold `worker` in `team_members`.
- Architect layer caught and fixed 3 real planner defects (developer[v2] de-scope gap — confirmed real in repo; two-restart window; impossible "startup recording" KB design); fold-pass folded all 11 corrections back consistently.
- Safety design strong: same-transaction fail-closed audit, idempotent `DO$$`-only repairs with self-surgery refusal, R13 no-synthetic-`ensemble_prod` guard, snapshot-before-repair, complete three-tier rollback, kill-switch checklist item with flag-OFF byte-identical pin.

### Iteration Counter
- 001 → REJECTED (this entry)
- Next: 002 (to be dispatched after the planner revises Task 1.2 to add `system_restart` to deny and re-scopes 2.8(b) / 2.10(f) / SC#9 accordingly)

## Iteration 002 — 2026-09-09 (UTC)

**Verdict:** APPROVED
**Workers (parallel, section-partitioned, cold context — no tracking history passed):**
- approve-worker-overview: 26b4a019-07c5-4524-b622-0913d5fe7ad3 (plan-approval) → plan-overview.md (+ cross-refs)
- approve-worker-decarch: 8fb0c151-2dd5-4bf5-84fe-44cfa269ae60 (plan-approval) → decisions.md + architecture-recommendation.md (+ cross-doc alignment)

**Result:** 0 blocking issues from either worker. Both verdicts APPROVED.

**Iteration-001 blocking issue — RESOLVED (verified fresh this cycle):**
- `system_restart` exclusion now enforced at resolution level: D20 deny-list (`tools.deny: ["system_restart"]`) strips post-expansion — verified live at daemon/tools/instance.py:316-319, applied AFTER resolve_tool_filter category expansion (:311-312); the call-time `if self_env == "live"` refusal (upgrade_tools.py:1458-1465) is correctly demoted to defense-in-depth; dev/demo negative case covered.

**Deduplicated non-blocking notes carried to implementation:**
1. Wave-1 operator pre-flight gates: PG role privileges (R15, arch §7.6); pool_recycle × pool_pre_ping × reset-on-return empirical check on disposable PG (R17, rollout step 9) — both BEFORE the W3 restart.
2. Council disagreement on created_by audit-stamp precedent (infra.py:226-234 vs infra/models.py:256 + infra/repository.py:540-541) — implementer to adjudicate (recommend git log -L) before authoring the audit-stamp pin test.
3. Test filename typo task 2.10(d): test_ens_db_repair_idempotent_doller.py → rename.
4. Decision-count delta (arch §1 "13 MADE" vs decisions.md D1–D20) documented as preserved adjudication snapshot — worth a line in the rollout PR description.
5. Tier 3 RAG-mirror KB probabilistic; Tiers 1–2 load-bearing (in-plan acknowledgment).
6. lock_timeout=10s initial; post-soak re-tune (R17).
7. Release-tag source semantics for task 3.1/3.8 pin test may need one-line clarification at 3.8 acceptance.
8. Pool isolation ≠ lock isolation (arch §3.1) — acknowledged in-plan.
9. Worker retains system-log break-glass (OPEN B) — centralization best-effort/leaky, bounded; worker = SPOF backstop.

**Verification quality:** both workers ran HEAD rev-parse (plan/maintenancer-agent @ 9fe74bc7, no drift), read all 3 docs end-to-end, independently spot-checked 6 (A) + 5 (B) load-bearing repo claims — all clean, incl. both dual-pin exact-equality tests (test_upgrade_registration.py:100, test_attestation_registration.py:154) and the D20 enforcement path.

### Iteration Counter (running)
- 001 → REJECTED (system_restart deny-list gap)
- 002 → APPROVED (this entry) — approved for implementation; R15/R17 operator gates enforced at rollout level, not plan level.
