# Blueprinter Evolution — Architecture Design

> **Status:** Draft — pending team discussion  
> **Date:** 2026-08-03  
> **Owner:** Leader (orchestrated with user)  

---

## Overview

The Blueprinter agent evolves from a simple trigger-based maintenance agent into a **skill-driven, multi-worker system** with two distinct workflows: **Rebuild** (full project scan) and **Incremental** (batch-process accumulated changes). The "Init" mode is removed — first build IS a rebuild.

---

## Key Decisions (Locked)

| Decision | Resolution |
|----------|-----------|
| **"Init" removed** | Only "rebuild" (first build = rebuild) and "incremental" |
| **Button** | Empty → "Rebuild Blueprints" (direct). Exists → "Update Blueprints" → popup: Incremental / Full Rebuild |
| **Skills** | Real skill files in `agents/blueprinter/skills/` (like v2 agents) — loaded via `skill-set.yaml` |
| **Pending queue** | New DB table — stores experience text (max 10k) + history events + timestamp. Cleared after incremental run |
| **Daily scan** | Runs incremental. If corpus empty or just bare core.md → triggers full rebuild instead |
| **Worker limit** | 4 max concurrent, two-phase fan-out/fan-in |
| **Worker coordination** | Fan-out/fan-in twice: explore → decide → craft → save |
| **Experience/history hooks** | `experience()` and `project_history_add()` (feature/milestone) both INSERT into pending table |

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────┐
│                    BLUEPRINTER AGENT                      │
│  Skill set loaded by trigger type:                       │
│  ┌─────────────┐  ┌──────────────────┐                  │
│  │  REBUILD     │  │  INCREMENTAL     │                  │
│  │  skills      │  │  skills          │                  │
│  └──────┬──────┘  └───────┬──────────┘                  │
│         │                  │                              │
│    FAN-OUT 1         FAN-OUT 1                           │
│    4 workers          4 workers                          │
│    (explore-for-      (explore-for-                      │
│     rebuild skill)     incremental skill)                │
│         │                  │                              │
│    FAN-IN 1           FAN-IN 1                            │
│    Blueprinter        Blueprinter                         │
│    decides what       decides what                        │
│    to keep            to update                           │
│         │                  │                              │
│    FAN-OUT 2         FAN-OUT 2                           │
│    4 workers          4 workers                          │
│    (build-blueprint   (build-blueprint                    │
│     skill)             skill)                             │
│         │                  │                              │
│    FAN-IN 2           FAN-IN 2                            │
│    Save blueprints    Save + clear pending                │
└─────────────────────────────────────────────────────────┘
```

---

## 1. Pending-Experience Queue

### New Table: `project_blueprint_pending_updates`

| Column | Type | Description |
|--------|------|-------------|
| id | str (PK) | UUID |
| project_id | str | Project scope |
| source | str | `'experience'` or `'history'` |
| text | str | Content (truncated to 10k chars) |
| created_at | str | ISO timestamp |

### Hooks

- **`experience()`** — Currently triggers sidecar blueprinter job → **CHANGE**: just INSERT into pending table (no blueprinter spawn). No keyword filtering — all experience text goes in.
- **`project_history_add()`** — **NEW**: also INSERT into pending table when `entry_type` is `feature` or `milestone`. Text truncated to 10k chars.

### Clearing

After incremental update completes successfully, DELETE all pending records for that project.

---

## 2. Skill Set Structure

```
agents/blueprinter/
├── meta.json
├── soul.md
├── rule.md
├── workflow.md          ← Two workflow branches: rebuild vs incremental
├── skill-set.yaml       ← Skill definitions + trigger conditions
└── skills/
    ├── explore-for-rebuild.md      ← Worker skill: explore project at overview level (directories, scope, modules)
    ├── explore-for-incremental.md   ← Worker skill: explore specific changed areas (from pending records)
    ├── build-blueprint.md           ← Worker skill: craft concise blueprint content from exploration report
    └── decide-changes.md            ← Blueprinter skill: analyze reports, decide what to create/update/discard
```

### Skill Descriptions

**explore-for-rebuild.md** — Given to workers during rebuild Phase 1. Worker explores the project at overview level: lists directories, understands module scope, identifies entry points, patterns, dependencies. Reports back a structured summary.

**explore-for-incremental.md** — Given to workers during incremental Phase 1. Worker explores specific areas that changed (from pending-experience records). Different focus: what changed, what blueprints are affected, what's stale. Reports back change-focused analysis.

**build-blueprint.md** — Given to workers during Phase 2 (both workflows). Worker takes exploration data + blueprint area assignment, crafts a concise (200-500 words) blueprint with file references and trigger queries. Returns formatted content.

**decide-changes.md** — Used by blueprinter itself (not workers) during fan-in. Analyzes exploration reports, decides which blueprints to create, update, or discard. Prioritizes high-value stable architectural knowledge.

### Loading Mechanism

The blueprinter's `workflow.md` instructs it to load the appropriate skill and pass it to workers via `send_message(..., load_skill="explore-for-rebuild")`. Workers receive the skill content as an injected context message.

---

## 3. Worker Coordination (Two-Phase Fan-Out/Fan-In)

### Rebuild Workflow

```
Phase 1 — EXPLORE (fan-out 4 workers)
  Blueprinter: List top-level directories → split into 4 groups
  Each worker gets: explore-for-rebuild skill + directory group
  Worker reports: module purpose, key files, entry points, patterns, dependencies

Phase 1 — DECIDE (fan-in, blueprinter alone)
  Blueprinter: Review 4 reports + decide what blueprints to create
  Uses decide-changes skill internally

Phase 2 — CRAFT (fan-out 4 workers)
  Blueprinter: Assign 1 blueprint per worker
  Each worker gets: build-blueprint skill + exploration data for that area
  Worker returns: formatted blueprint content (200-500 words) + file refs + trigger queries

Phase 2 — SAVE (fan-in, blueprinter alone)
  Blueprinter: Save all crafted blueprints via blueprint_create/update tools
```

### Incremental Workflow

```
Phase 0 — READ PENDING
  Blueprinter: Load pending-experience records for project
  If empty → exit (nothing to update)
  If corpus empty/bare-core → switch to rebuild workflow

Phase 1 — EXPLORE (fan-out, max 4 workers)
  Split pending records into groups (by topic/module similarity)
  Each worker gets: explore-for-incremental skill + pending texts + current blueprint content
  Worker reports: what changed, what blueprints need updating

Phase 1 — DECIDE (fan-in)
  Blueprinter: Decide which blueprints to update

Phase 2 — CRAFT (fan-out, max 4 workers)
  Each worker gets: build-blueprint skill + current blueprint + change report
  Worker returns: updated blueprint content

Phase 2 — SAVE + CLEAR (fan-in)
  Blueprinter: Save updated blueprints + DELETE pending records
```

---

## 4. API Changes

### Replace `/initialize` with two endpoints:

```
POST /api/projects/{project_id}/blueprints/rebuild
  → 202 Accepted (enqueues blueprinter with trigger: "rebuild")
  → 409 if a rebuild is already in progress

POST /api/projects/{project_id}/blueprints/update
  → 202 Accepted (enqueues blueprinter with trigger: "incremental")
  → 409 if already in progress
```

---

## 5. Frontend Changes

### Button Logic

```
if (blueprints.length === 0):
    Show "Rebuild Blueprints" button → calls /rebuild directly
    
if (blueprints.length > 0):
    Show "Update Blueprints" button → shows popup:
        ┌─────────────────────────────┐
        │  Update Blueprints           │
        │                              │
        │  ○ Incremental Update        │
        │    (Process recent changes)  │
        │                              │
        │  ○ Full Rebuild              │
        │    (Re-scan entire project)  │
        │                              │
        │  [Cancel]  [Start]           │
        └─────────────────────────────┘
```

---

## 6. Daily Scan Logic

```
Daily scan triggers:
  1. Read pending-experience count for project
  2. Check existing blueprint corpus:
     - Empty (0 blueprints) → trigger REBUILD
     - Only bare core.md (1 blueprint, kind=core) → trigger REBUILD
     - Has blueprints + has pending records → trigger INCREMENTAL
     - Has blueprints + no pending records → skip (nothing to do)
```

---

## Open Questions (For Team Discussion)

1. **Worker skill loading** — The blueprinter passes skills to workers via `load_skill`. But workers have their own skill system. Should the blueprint skills live in `agents/blueprinter/skills/` (blueprinter-owned, passed as `load_skill`) or in a shared location? Current thinking: blueprinter-owned.

2. **Rebuild in-progress guard** — Should we prevent concurrent rebuilds for the same project? (Current thinking: yes — 409 Conflict if a job is already queued/running)

3. **Existing `/initialize` endpoint** — Replace entirely with `/rebuild` and `/update`, or keep `/initialize` as an alias for backward compatibility?

4. **Blueprinter's `llm_model`** — Currently `quick`. For the decide-changes phase, should it use a smarter model? The exploration is done by workers (which use their own models), but the blueprinter needs to make good decisions about what to keep/discard.

5. **Pending queue growth** — What happens if pending records accumulate but daily scan hasn't run for a while (e.g., daemon restart)? Should there be a max pending count that forces an earlier incremental trigger?

6. **Worker report format** — Should the worker reports follow a strict structured format (JSON schema) or free-form text? Structured is easier for the blueprinter to parse; free-form is more flexible for the worker LLM.

7. **Blueprint deletion during rebuild** — During a full rebuild, should old area blueprints be deleted before creating new ones? Or should the blueprinter compare old vs new and only update changed ones?

---

## Implementation Phases (Proposed)

| Phase | Name | Deliverables |
|-------|------|-------------|
| 1 | Pending-experience queue | DB table, hooks in `experience()` and `project_history_add()`, repository methods |
| 2 | Blueprinter skill set | Create skill files, skill-set.yaml, update soul.md/workflow.md for two-workflow model |
| 3 | API changes | `/rebuild` and `/update` endpoints, remove or alias `/initialize` |
| 4 | Frontend | Update button logic (dual-mode + popup), remove single init button |
| 5 | Daily scan update | Smart trigger logic (rebuild vs incremental vs skip) |
| 6 | Testing | E2E: rebuild flow, incremental flow, pending queue lifecycle, worker coordination |
