# SPEC — Group-Chat FE Fast-Follow Surfaces (`gc-fe-surfaces`)

Lane: `gc-fe-surfaces` · task id `gc-fe-20261117`
Branch: `feature/gc-fe-surfaces` · base `0181dadbf8641c60d7158ad59edfa4a4b36944c8` (v0.31.0)
Commission source: Emperor, Discord 2026-11-17

> This document is **verbatim-faithful only**. It records the commission text as
> issued; every interpretation, ruling and trade-off lives in
> `decisions.md` (the house decision ledger) and NOT here.

---

## 1. EMPEROR'S SPEC (verbatim)

### (a) CHAT-PAGE HEADER = the GLOBAL browser

> (a) CHAT-PAGE HEADER = the GLOBAL browser. The chat-page header (where v0.31.0 shipped the chatroom icon beside WATCHOVER) grows a HEADER MENU that opens the list of chatrooms + a DETAIL view of a selected room (its messages). Global whole-nest view, not per-instance.

### (b) INSTANCES-LIST ROW BUTTON (next to the WATCHOVER button) = PER-INSTANCE scoped

> (b) INSTANCES-LIST ROW BUTTON (next to the WATCHOVER button) = PER-INSTANCE scoped: shows the chatrooms THAT INSTANCE JOINED. Clicking opens a BIG POPUP (large modal — not a cramped tooltip/dropdown) listing that instance's rooms with their messages.

### (c) CONDITIONAL VISIBILITY

> (c) CONDITIONAL VISIBILITY: the near-watchover button renders ONLY if that instance has joined at least one chatroom — no dead icon on room-less instances.

---

## 2. BINDING CONSTRAINTS (all six, verbatim)

**1. Read predicate**

> 1. Read predicate: reuse the agents' room-read API predicate (members ∪ owner-ancestry, FAIL-CLOSED). Do NOT widen it for the FE; no leaking room titles for rooms the principal cannot read. If the FE needs a shape the agent API doesn't expose (likely: a list-rooms HTTP endpoint), it must be a READ-ONLY, FAIL-CLOSED addition with NO new auth surface (same session/auth middleware as existing FE-facing routes, same repository-layer predicate function — shared code, never duplicated logic) — and the report must disclose it.

**2. House FE discipline**

> 2. House FE discipline: ZERO new npm deps; respect the /metrics lazy-route budget + chunk discipline (read the CURRENT budget constants/guard in the repo — do not trust stale numbers); house SVG renderer patterns untouched; existing Angular component/service/SCSS/spec conventions, the same standard the v0.31.0 FE quality pass applied.

**3. Live updates**

> 3. Live updates: prefer the existing SSE/events surface (v0.31.0's item #7 already rides LiveEventHub) IF cheap; else the house polling pattern (bounded interval, supersede tokens, stop on tab-hide) used by the metrics tabs. The plan must make this decision ON EVIDENCE (read what room events actually reach the FE events surface today) and record choice + rationale.

**4. Stability**

> 4. Stability: video-team pilot is LIVE on this release exercising room machinery — the chat page must keep working throughout; no regression of v0.31.0's shipped header icon.

**5. Gates**

> 5. Gates: full house chain after planning (architect adjudication → reviewer council → fresh approver), then code council / tidier / tester as warranted; staged release; NO live promote.

**6. Concurrency**

> 6. Concurrency: in-flight lanes disjoint from frontend/**; only coordination = release-slot sequencing with other staging lanes.

---

## 3. Commission metadata

| Field | Value |
|---|---|
| Source | Emperor, Discord 2026-11-17 |
| Alignment anchor (project metadata) | `gc_fe_fast_follow_spec` |
| Alignment anchor (shared KV) | `wt.active.feature/gc-fe-surfaces.gc-fe-20261117` |
| Disposition | **staged release, NO live promote** |
| Scope | Group-chat FE fast-follow — global header room browser, per-instance joined-room popup, conditional visibility |
| Base | `0181dadbf8641c60d7158ad59edfa4a4b36944c8` (v0.31.0) |
| Branch | `feature/gc-fe-surfaces` |
| Worktree | `/home/nea/ensemble-src-wt-gc-fe-surfaces` |
| Planning dir | `.agents/shared/planning/gc-fe-surfaces/` |
