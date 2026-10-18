# Tracking: db-backed-settings

## Iteration 001 — 2026-10-18 — REJECTED
Worker: approve-worker-plan (613d7fd9-3044-4416-ba06-dfd43b14d0ca), skill: plan-approval

### Blocking Issues
1. **yaml_value derivation references non-existent state (Feasibility)** — phase1-plan.md t5 + decisions.md ruling c: rule requires raw limits dict "from load_config()'s processed config", but `load_config() -> Config` only (daemon/config.py:5403); `processed_config` is function-local (config.py:5444), never exposed; meanwhile daemon/config.py is pinned READ-ONLY in the plan's own Files-to-Touch (plan-overview.md:121). Without the raw literal, `source="yaml"` vs `source="default"` is indistinguishable; P4 t1(d) unimplementable as specified. Fix options (~5 lines): (a) minimal config.py edit adding raw-limits module accessor (precedent: _install_service_tool_enabled), or (b) lifespan re-derives raw limits via yaml parse with ENSEMBLE_CONFIG-aware resolution (config.py:5404-5407), or (c) capture raw limits section in load_config's return chain.
2. **Frozen GET contract (`value` = boot-applied truth) has no state carriage after post-boot DB changes (Consistency)** — phase2-plan.md t1/t3 vs snapshot `{default, env_value, yaml_value}` only (decisions.md:52, phase1 t5, arch blind-spot b, captured BEFORE mutation); runtime service wired `SettingsService(engine=manager.engine)` only (phase1 t6); app.state lacks merged Config; router has no manager reference. Failure case: boot with DB row=20 → applied 20 → operator DELETEs row → GET must show value=20 + source=env/yaml/default — nothing specified produces the 20. Fix (one line): extend per-key snapshot with `applied_value` captured AFTER mutation at boot (or wire live Config into snapshot/service setter); must land before Phase 2 t1 freezes the contract.

### Notes (non-blocking)
- Architect delta 5 placement (own task t5 instead of "phase4 t1/t2") — fine, content complete.
- P4 t6 single-.sql runner invocation implies temp-dir containing only that file — one sentence at implementation time.
- Unverified (self-declared in arch doc): uvicorn request-gating on lifespan completion; full thread-path accounting behind "cap = 7" claim (structure verified).

### Worker assessment context
~35 file:line citations verified against worktree, all held; all 5 commission requirements addressed; env-shadowing root cause verified with fail-designed test; safety posture strong (additive-only, fail-open boot read, rollback ≡ no overrides).

**Status: IN_PROGRESS — iteration 002 pending planner fix of the two seams (both one-liner-class on snapshot/mechanism).**

## Iteration 002 — 2026-10-18 — APPROVED (FINAL)
Worker: approve-worker-plan (3a726538-12da-4b0a-9a52-5bc638082ea7), skill: plan-approval

### Blocking Issues
(none)

### Notes (non-blocking)
1. Write-response wording drift: decisions.md:100 + plan-overview.md:84 say top-level `restart_required: true` on the PUT response; binding frozen write contract (phase2-plan.md t1) is the bare refreshed DaemonSettingEntry with restart semantics on the entry's own requires_restart/db_override fields. FE keys off the per-key field (phase2-plan.md t3) — doc-polish only; align wording at next touch.
2. decisions.md:44 ruling (b) code block shows 2-arg apply_db_settings_overrides; final signature adds optional raw_yaml_limits kwarg (phase1-plan.md t5, plan-overview.md:30). Self-consistent (kwarg optional, callers unchanged); decisions.md block predates it.
3. External provenance labels ("Adopted note 3/4/5/8b/9", "ruling O2/O3/O4", "P6a/P6b", "iteration-002 Shape A", "iteration-003 shape (a)") reference artifacts outside the set; content fully restated in-place, set is self-contained.
4. Boot log line `[Settings] applied N db override(s)` specified once (phase1-plan.md t6 acceptance) only.

### Worker assessment context
~30 file:line anchors verified against worktree @ e0e826866 — all true; all commission elements covered (completeness table incl. env-shadowing fail-designed test as acceptance centerpiece); safety: additive-only, fail-open boot read, write-time + boot-time validation, rollback ≡ no overrides. Post-verdict cross-check: iteration-001 blockers confirmed addressed (yaml raw-literal accessor feasible per config.py:5403 enclosure; GET contract carries boot-applied applied_value + live source re-resolve w/ divergence rendering).

**Status: APPROVED — plan set cleared for implementation (2026-10-18T00:30:00Z).**
