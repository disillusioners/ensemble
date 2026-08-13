# PM System Improvement — Master Plan

**Date:** 2026-08-13
**Status:** Draft v2 — Reviewer fixes applied (6 criticals + 5 warnings)
**Scope:** LARGE (4 phases, multi-module)

> **Entry point:** This file is a pointer. The full synthesized plan is in [`plan-overview.md`](plan-overview.md) — the **single source of truth**.

---

## What This Is

A multi-phase plan to upgrade the `project-manager` agent from stand-alone read-only advisory → strategic dispatcher with Plane integration, new analytical capabilities, leader dispatch, and hardened MCP tool layer.

## Documents

| Document | Role |
|----------|------|
| **[`plan-overview.md`](plan-overview.md)** | **Start here — SINGLE SOURCE OF TRUTH.** Canonical Cardinal/Guideline text, meta.json spec, Flow numbering, KV schema, unified task list, merge order, risk register, success criteria. |
| [`phase1-2-prompts-capabilities.md`](phase1-2-prompts-capabilities.md) | Phase 1 (prompt rewrites) + Phase 2 (Flows 6–8: Roadmap/Milestones/Burndown). Implementation detail; defers to overview for canonical text. |
| [`phase3-dispatch-integration.md`](phase3-dispatch-integration.md) | Phase 3 (PM→Leader dispatch). meta.json diff detail, dispatch protocol, lifecycle verification. Defers to overview for Cardinal/Guideline/Flow numbering. |
| [`phase4-mcp-improvements.md`](phase4-mcp-improvements.md) | Phase 4 (Plane MCP hardening). Retry + circuit breaker + caching + graceful degradation. |
| [`architecture-dispatch.md`](architecture-dispatch.md) | Pre-existing architect deep-review. Lifecycle verification that PM→leader dispatch requires zero daemon code. |

## Phase Map

| Phase | Name | Key Output | Merge |
|-------|------|------------|-------|
| 1 | PM Prompt Rewrites | soul/rule/workflow/tools_note rewritten; Cardinals ≤7 | **PR 1** (with Phases 2+3) |
| 2 | New Capabilities | Flows 6 (Roadmap), 7 (Milestones), 8 (Burndown) | **PR 1** (with Phases 1+3) |
| 3 | PM→Leader Dispatch | meta.json v2.0.0; dispatch protocol; shared_meta_kv registry | **PR 1** (with Phases 1+2) |
| 4 | Plane MCP Improvements | resilience.py, errors.py, PlaneResilienceConfig, hardened _lazy_coroutine | **PR 2** (AFTER PR 1) |

## Merge Order (W4)

> 🔴 **Phase 4 is NOT independent — it merges AFTER Phases 1+2+3.**
> - **PR 1:** Phases 1+2+3 together (prompts + capabilities + dispatch + meta.json)
> - **PR 2:** Phase 4 (MCP hardening — after PR 1, because PM prompts document the degradation behavior Phase 4 implements)

## Key Decisions

1. **Cardinal #2 replaces "no dispatch" with "dispatch to leader only"** — PM spawns leader, never developer/tester/reviewer directly. Count stays at 7.
2. **Cardinal #1 extends to "external systems (Plane)"** — PM is read-only on code, plans, configs, project state, AND Plane. Plane write tools denied by exact name (C2).
3. **Instance reuse via shared_meta_kv registry** — key `pm_leader_instances`, JSON array. Survives context compaction. Not conversation history.
4. **Deny charter + image-reader by exact name (C1)** — `chart`/`image` categories auto-derive these as spawnable; deny prevents PM spawning non-leader agents.
5. **Hybrid MCP resilience** — generic primitives (retry, circuit breaker, cache) + Plane-specific config. Other servers unaffected (opt-in).
6. **On-demand health probe (C5)** — NOT a background daemon. Probe inside `_lazy_coroutine` on HALF_OPEN circuit transition. No `health_monitor.py`.
7. **Zero daemon code for dispatch** — verified against `_auth.py`, `instance.py`, `instance_messaging.py`. PM→leader uses existing mechanisms.
8. **Plane graceful degradation at two layers** — tool layer returns structured fallback JSON (Phase 4); prompt layer documents behavior + forbids fabrication (Phase 1).
