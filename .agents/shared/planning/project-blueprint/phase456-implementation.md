# Phase 4 / 5 / 6 — Detailed Implementation Plan

**Parent plan:** `.agents/shared/planning/project-blueprint/plan-overview.md` (Final, locked)
**Scope:** Phase 4 (Blueprinter Agent) + Phase 5 (Frontend UI) + Phase 6 (Evaluation & Tuning)
**Out of scope:** DB schema, matching engine, CRUD API, tool category registration (owned by Phases 0–3 workers — referenced by name only)
**Convention:** Agent prompt files follow `docs/agent-prompt-writing-guide.md`

---

## PHASE 4 — Blueprinter Agent

### 4.1 Objective

Deploy a fully automatic blueprint-maintenance agent (`blueprinter`) that detects architectural drift and revises the blueprint corpus without human-in-the-loop. Runs on the existing `system_background_queue`. Triggers: post-experience (filtered) + daily self-re-enqueue scan. Rate-limited with circuit breaker.

### 4.2 Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Add `blueprint_inactive` field to `AgentMetadata` + loader line in `discover()` | none | Field loads from meta.json; C6 silent-drop avoided |
| 2 | Create `agents/blueprinter/` with meta.json, soul.md, rule.md, workflow.md | T1 | Agent discovered by registry; prompt files pass prompt-writing-guide checklist |
| 3 | Implement `BlueprintRateLimiter` class | none | Windowed counter + circuit breaker; unit-tested |
| 4 | Implement post-experience sidecar hook in `_enqueue_experience_job()` | T1 | Keyword-filtered experience calls enqueue blueprinter on background queue; fire-and-forget |
| 5 | Implement daily scan via self-re-enqueue (metadata-based scheduling) | T2, T3 | Blueprinter re-enqueues itself with 24h delay; idempotency prevents duplicates |
| 6 | Implement admin REST endpoint `POST /admin/blueprints/scan` (external cron fallback) | T2 | Endpoint triggers an immediate blueprinter scan on background queue |
| 7 | Implement bootstrap seeding path | T2 | On first invocation, if corpus empty, bootstraps core.md from project critical-notes + context.md |
| 8 | End-to-end drift scenario test | T1–T7 | Blueprinter revises a stale blueprint correctly; rate limiter prevents thrash |

---

### 4.3 AgentMetadata field addition (Task 1)

**File:** `daemon/registry.py`

**4.3.1 — Pydantic model (after `context_injection` field, ~line 302):**

Add to `class AgentMetadata(BaseModel)`:

```python
blueprint_inactive: bool = Field(
    default=False,
    description=(
        "When true, this agent does NOT receive blueprint injection. "
        "Used for utility agents (kb-writer, blueprinter itself) where "
        "the skeleton is wasted tokens or self-referential."
    ),
)
```

**4.3.2 — discover() loader line (in the `AgentMetadata(...)` constructor call, ~line 543):**

Add after `recursion_limit=...`:

```python
blueprint_inactive=meta.get("blueprint_inactive", False),
```

> ⚠️ **C6 pattern — CRITICAL.** Forgetting the loader line means the Pydantic field defaults to `False` regardless of what meta.json says. The field silently drops. Every new AgentMetadata field requires BOTH the Pydantic declaration AND the `meta.get(...)` loader line. This has bitten the project before.

---

### 4.4 Agent directory: `agents/blueprinter/` (Task 2)

**Convention:** Follow `docs/agent-prompt-writing-guide.md` — 10 sections, file roles (soul/rule/workflow), composition order (`soul → rule → skills → tools_note → workflow → memory`), first-person voice, no system-internals leaking.

**Required files:** `meta.json`, `soul.md`, `rule.md`, `workflow.md`. Optional: `tools_note.md` (recommended — the tool surface is non-obvious).

#### 4.4.1 — `meta.json`

```json
{
  "id": "blueprinter",
  "name": "Blueprinter",
  "description": "Automatically maintains the project blueprint corpus by detecting architectural drift and revising blueprints",
  "icon": "📐",
  "color": "accent-purple",
  "version": "1.0.0",
  "tools": {
    "allow": ["blueprint", "knowledge", "filesystem", "time", "self", "help"]
  },
  "team_members": [],
  "blueprint_inactive": true
}
```

**Field rationale:**

| Field | Value | Rationale |
|-------|-------|-----------|
| `id` | `"blueprinter"` | Matches directory name; registry uses this for dispatch (`agent_id="blueprinter"`) |
| `name` | `"Blueprinter"` | Display name |
| `description` | (above) | One-line purpose for registry logging |
| `tools.allow` | `["blueprint", "knowledge", "filesystem", "time", "self", "help"]` | `blueprint` = read+write tools (blueprint_create, blueprint_update, blueprint_list, blueprint_get, blueprint_delete). `knowledge` = explore() to gather drift facts. `filesystem` = read project structure for drift detection. `time` = check schedule timestamps. `self` = self-introspection. `help` = tool docs. **No `bash`** — blueprinter is a knowledge agent, not a code executor. **No `proc`, `context`, `shared_context`** — not needed. |
| `team_members` | `[]` | Blueprinter works alone. It cannot spawn instances. (Per overview §7.1) |
| `blueprint_inactive` | `true` | Self-referential guard (overview §7.3, §13.7). Blueprinter does not inject blueprints into its own context — it generates them, not consumes them. |
| No `skill_injection` | (defaults `false`) | Blueprinter does not need dynamic skills injected |
| No `llm_model` | (defaults to global) | Uses the system default model |

#### 4.4.2 — `soul.md`

**Sections (per prompt-writing-guide composition):**

1. **Who I Am** — "I am the blueprinter. I maintain the project's blueprint corpus automatically. I detect architectural drift from knowledge-base entries and project structure changes, and I revise blueprints to keep the project skeleton accurate and useful for other agents."

2. **My Role** — Bullet list: detect drift → decide no-op/create/update/disable → generate content (200–500 words) + trigger queries + recompute embeddings → call blueprint tools → respect rate limit.

3. **What I Am NOT** — I do not write code, execute commands, spawn instances, or coordinate other agents. I do not edit blueprints for my own consumption (I am `blueprint_inactive: true`). I do not require approval — revisions are immediate.

4. **core.md priority** — "core.md is the most-injected blueprint. When I detect drift anywhere, I review core.md first. I never make self-referential edits to core.md based on my own behavior. I check for system-prompt duplication before writing."

5. **Output shape** — After each run, I report what I revised (created / updated / disabled / no-op) with blueprint names and reasons.

#### 4.4.3 — `rule.md`

**Cardinal rules (never-violate invariants, ≤7):**

1. **Fire-and-forget discipline.** My failures must never propagate to the caller that triggered me. I log and swallow errors, I do not crash the dispatching system.

2. **Rate limit.** I check `BlueprintRateLimiter.can_proceed(project_id)` before every write. If false, I stop and report "rate-limited" without writing.

3. **Manual edit preservation.** When a blueprint's `source = "manual"`, I use a higher confidence threshold before overwriting. I prefer to leave manual content untouched unless drift is unambiguous.

4. **core.md highest priority.** When drift is detected in any area, I review core.md before area blueprints. I never auto-edit core.md based on my own behavior.

5. **Word limits.** Blueprint content: 200–500 words. core.md: 300–500 words. If content exceeds the limit, I split into area blueprints.

6. **No system-prompt duplication.** I check blueprint content for overlap with system-prompt material before writing. If I detect duplication, I trim or restructure.

7. **Daily scan scheduling.** At the end of every daily scan run, I re-enqueue myself for the next day unless already scheduled. I never enqueue more than one future daily scan.

#### 4.4.4 — `workflow.md`

**The maintenance workflow (step-by-step):**

```
PHASE 1: RECEIVE TRIGGER
  - Receive message with metadata.trigger ∈ {"post-experience", "daily-scan"}
  - Extract project_id from the message context

PHASE 2: GATHER CANDIDATE FACTS
  - For post-experience: parse the experience text from the message body
  - For daily-scan: query recent experience entries via explore()
  - Read project directory structure via filesystem (top-level dirs, new modules)
  - List existing blueprints via blueprint_list(project_id)
  - Identify drift signals:
      * New experience entries that contradict existing blueprint content
      * New high-level directories/services not in any blueprint
      * File references pointing to deleted/relocated files
      * Trigger queries with low match rate (from Phase 6 analytics)

PHASE 3: DECIDE
  - For core.md: if ANY drift detected → review core.md FIRST
  - For each candidate area: decide no-op / create / update / disable
  - Skip areas where current content is still accurate

PHASE 4: EXECUTE (for each create/update)
  - CHECK: BlueprintRateLimiter.can_proceed(project_id) → if false, STOP
  - Generate content (200–500 words, declarative, file references included)
  - Generate trigger queries (3–10 natural-language example queries)
  - Call blueprint_create(name, kind, content, tags, file_refs, trigger_queries)
    OR blueprint_update(name, content, tags, file_refs, trigger_queries)
  - Record success/failure in BlueprintRateLimiter

PHASE 5: DISABLE (for stale/inrelevant blueprints)
  - Call blueprint_delete(name) for blueprints with persistent low match rate

PHASE 6: SCHEDULE NEXT RUN (daily-scan only)
  - Check metadata["next_scan_at"] → if not set or past, re-enqueue self for +24h
  - Use idempotency_key to prevent duplicate scheduling

PHASE 7: REPORT
  - Summarize: created/updated/disabled/no-op, blueprint names, reasons
```

#### 4.4.5 — `tools_note.md` (recommended)

Tool-by-tool reference table:

| Tool | When I use it |
|------|---------------|
| `blueprint_list(project_id?)` | Phase 2 — gather existing blueprints to detect drift |
| `blueprint_get(name)` | Phase 2 — read current content to compare with drift signal |
| `blueprint_create(...)` | Phase 4 — create new area blueprint |
| `blueprint_update(...)` | Phase 4 — revise existing blueprint content |
| `blueprint_delete(name)` | Phase 5 — soft-delete stale blueprint |
| `explore(query)` | Phase 2 (daily-scan) — gather recent experience entries |
| `read_file` / `list_directory` | Phase 2 — read project structure for drift detection |
| `time` | Phase 6 — check schedule timestamps for daily scan |

---

### 4.5 Post-experience sidecar hook (Task 4)

**File:** `daemon/tools/knowledge_tools.py`
**Function:** `_enqueue_experience_job()` (lines 342–406)
**Insertion point:** AFTER the `await job_service.enqueue(...)` call for kb-writer (line ~397), BEFORE the outer `except Exception` block (line ~403).

**Architecture keyword list (per overview §7.4 — resolves O3):**

```python
_BLUEPRINT_TRIGGER_KEYWORDS = frozenset({
    "architecture", "pattern", "module", "service", "directory structure",
    "entry point", "lifecycle", "protocol", "schema", "migration",
    "queue", "directory", "component", "layer", "pipeline", "config",
    "convention", "endpoint", "api", "database", "model", "repository",
    "handler", "middleware", "decorator", "graph node", "state machine",
    "session", "checkpoint", "context injection", "tool registry",
})
```

**Edit shape (pseudo-code, inserted after line ~397):**

```python
        # ── Blueprinter post-experience trigger (filtered) ──────
        # Sidecar: if the experience text mentions architecture-domain
        # keywords, enqueue a blueprinter scan on system_background_queue.
        # Fire-and-forget — same pattern as the kb-writer enqueue above.
        # Errors logged + swallowed, never propagate.
        try:
            text_lower = text.lower()
            if any(kw in text_lower for kw in _BLUEPRINT_TRIGGER_KEYWORDS):
                bg_queue = await asyncio.to_thread(
                    job_service._queue_repo.get_by_name,
                    project_id, "system_background_queue",
                )
                if bg_queue is not None:
                    await job_service.enqueue(
                        agent_id="blueprinter",
                        message=(
                            "Post-experience drift signal detected.\n\n"
                            f"Experience text:\n{text[:2000]}\n\n"
                            f"Project: {project_id}\n\n"
                            "Analyze this knowledge for architectural drift. "
                            "If a blueprint area needs creation or revision, "
                            "do so. Respect the rate limit."
                        ),
                        source=f"blueprint-sidecar:{source_instance_id}",
                        project_id=project_id,
                        priority=8,  # lower than kb-writer (5); background
                        queue_id=bg_queue.queue_id,
                        idempotency_key=None,  # allow multiple signals
                        metadata={
                            "triggered_by": "post_experience_sidecar",
                            "trigger": "post-experience",
                            "source_instance_id": source_instance_id,
                            "text_preview": text[:100],
                        },
                    )
                    logger.debug(
                        "Enqueued blueprinter post-experience job for project %s",
                        project_id,
                    )
        except Exception as sidecar_err:
            # Fire-and-forget: sidecar failure must not affect experience()
            logger.warning(
                "Blueprinter sidecar enqueue failed (non-fatal): %s",
                sidecar_err,
            )
        # ── End blueprinter sidecar ─────────────────────────────
```

**Key design points:**
- The sidecar is nested inside the existing `try/except` in `_enqueue_experience_job()`, so even if the kb-writer enqueue succeeds but the sidecar fails, the experience call is unaffected.
- `priority=8` (lower urgency than kb-writer's `priority=5`) — blueprinter maintenance is genuinely lower priority.
- `idempotency_key=None` — multiple drift signals within a short window are allowed; the rate limiter handles dedup at the blueprinter level.
- The background gate (`has_active_non_background_work`) ensures blueprinter only runs when the system is idle. This is correct and intended.
- The keyword filter prevents every trivial experience call from triggering a scan. The filter is intentionally broad — it's better to trigger a no-op scan than to miss real drift.

---

### 4.6 Daily scan via self-re-enqueue (Task 5)

**Constraint:** No scheduler/cron exists in the codebase. No `delay_until` / `execute_after` field exists on JobItem (confirmed by grep). Two options:

#### Option A (RECOMMENDED): Metadata-based self-re-enqueue

The blueprinter enqueues itself with a `next_scan_at` timestamp in metadata. On each run, it checks whether `next_scan_at` has passed and, if so, performs the scan AND re-enqueues the next one.

**Bootstrap:** A one-time admin trigger (or the first post-experience signal) starts the cycle. After that, it's self-sustaining.

**Self-enqueue in workflow Phase 6:**

```python
# At end of daily-scan workflow:
from datetime import datetime, timezone, timedelta

next_scan = datetime.now(timezone.utc) + timedelta(hours=24)

# Idempotency key: project_id + scheduled_date — one daily scan per project per day.
# Composition: f"{project_id}:{date.today().isoformat()}:daily_scan"
# This prevents duplicate future scans if the blueprinter re-enqueues before
# the scheduled time (e.g. due to a premature wake + re-enqueue cycle).
idem_key = f"{project_id}:{next_scan.strftime('%Y-%m-%d')}:daily_scan"

await job_service.enqueue(
    agent_id="blueprinter",
    message=(
        "Daily blueprint scan.\n\n"
        f"Project: {project_id}\n\n"
        "Perform a full drift scan. Review core.md first, then area blueprints. "
        "Respect the rate limit."
    ),
    source="blueprint-self-schedule",
    project_id=project_id,
    priority=9,  # lowest priority — pure background
    queue_id=bg_queue.queue_id,
    idempotency_key=idem_key,
    metadata={
        "trigger": "daily-scan",
        "scheduled_for": next_scan.isoformat(),
    },
)
```

**The "delay" problem:** JobItem has no `delay_until` field, so the job is immediately PENDING and could be picked up immediately. The blueprinter must check the `scheduled_for` timestamp in its workflow:

```
PHASE 0 (daily-scan only): Check if it's time yet
  - Parse metadata["scheduled_for"]
  - If datetime.now() < scheduled_for → NO-OP (report "not time yet") and END
  - This prevents premature execution without requiring a DB schema change
```

The job runs immediately (cheap — one LLM turn to check the timestamp and no-op), but the actual scan only fires when `scheduled_for` has passed. The background gate ensures these no-op checks don't interfere with real work.

**Why this is acceptable:** The no-op turn costs one LLM call (~1–2s, background queue, system idle). If this is deemed too wasteful, Option B (external cron) avoids it entirely. But the self-re-enqueue keeps everything in-system with zero external dependencies.

#### Option B: External cron + admin endpoint

**File:** New router in the admin API (e.g., `daemon/api/admin_blueprints.py`)

**Endpoint:** `POST /admin/blueprints/scan?project_id=<id>`

```python
@router.post("/admin/blueprints/scan")
async def trigger_blueprint_scan(
    project_id: str,
    manager: InstanceManager = Depends(get_manager),
):
    """Trigger an immediate blueprinter daily scan for a project.
    
    Intended for external cron (e.g., systemd timer, GitHub Actions schedule).
    Dispatches on system_background_queue.
    """
    job_service = manager._job_queue_service
    bg_queue = await asyncio.to_thread(
        job_service._queue_repo.get_by_name, project_id, "system_background_queue"
    )
    if bg_queue is None:
        raise HTTPException(404, "system_background_queue not found")
    
    job = await job_service.enqueue(
        agent_id="blueprinter",
        message=f"Daily blueprint scan (external trigger).\n\nProject: {project_id}",
        source="admin-endpoint",
        project_id=project_id,
        priority=9,
        queue_id=bg_queue.queue_id,
        metadata={"trigger": "daily-scan", "source": "admin-endpoint"},
    )
    return {"job_id": job.job_id, "status": "enqueued"}
```

**External cron example (crontab):**
```
0 3 * * * curl -X POST http://localhost:8079/admin/blueprints/scan?project_id=<PROJECT_ID>
```

**Recommendation:** Implement BOTH. Self-re-enqueue (Option A) is the default — it requires zero external setup and keeps the system self-contained. The admin endpoint (Option B) is a documented fallback for deployments that prefer cron-based control. The blueprinter workflow handles both triggers identically (metadata `trigger: "daily-scan"`).

---

### 4.7 Rate limiter + circuit breaker (Task 3)

**File:** `daemon/services/blueprint_rate_limiter.py` (new file)

**Class:** `BlueprintRateLimiter`

**Design:** In-process, per-project state. No DB persistence needed (rate limits reset on restart — acceptable for a background maintenance agent). Uses a windowed counter for revisions/hour and a consecutive-failure counter for the circuit breaker.

```python
"""
Blueprinter rate limiter and circuit breaker.

Prevents runaway blueprint maintenance: caps revisions per hour per project,
and trips a circuit breaker after N consecutive failures.

In-process only — no persistence. State resets on daemon restart, which is
acceptable since the blueprinter rebuilds its state naturally on the next run.
"""

import time
from collections import defaultdict
from dataclasses import dataclass, field
from threading import Lock
from datetime import datetime, timezone


@dataclass
class _ProjectState:
    """Per-project rate-limit state."""
    # Sliding window of revision timestamps (epoch seconds)
    revision_timestamps: list[float] = field(default_factory=list)
    # Consecutive failure count
    consecutive_failures: int = 0
    # Circuit breaker tripped until this epoch (0 = not tripped)
    cooldown_until: float = 0.0


class BlueprintRateLimiter:
    """Windowed counter + circuit breaker for blueprint revisions.

    Configuration:
        max_revisions_per_hour: Hard cap on revisions per project per hour.
        failure_threshold: Consecutive failures before circuit breaker trips.
        cooldown_seconds: How long the breaker stays tripped.

    Defaults are initial values — calibrate in Phase 6 (open item O4).
    """

    def __init__(
        self,
        max_revisions_per_hour: int = 5,
        failure_threshold: int = 3,
        cooldown_seconds: int = 600,  # 10 minutes
    ):
        self._max_per_hour = max_revisions_per_hour
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._state: dict[str, _ProjectState] = defaultdict(_ProjectState)
        self._lock = Lock()

    def can_proceed(self, project_id: str) -> bool:
        """Check if a revision is allowed for this project.

        Returns False if:
        - Rate limit exceeded (more than max_revisions_per_hour in the last hour)
        - Circuit breaker is tripped (cooldown period not elapsed)
        """
        with self._lock:
            state = self._state[project_id]
            now = time.time()

            # Check circuit breaker
            if state.cooldown_until > now:
                return False

            # Prune old timestamps (older than 1 hour)
            cutoff = now - 3600
            state.revision_timestamps = [
                ts for ts in state.revision_timestamps if ts > cutoff
            ]

            # Check rate limit
            if len(state.revision_timestamps) >= self._max_per_hour:
                return False

            return True

    def record_success(self, project_id: str) -> None:
        """Record a successful revision. Resets consecutive failure counter."""
        with self._lock:
            state = self._state[project_id]
            state.consecutive_failures = 0
            state.revision_timestamps.append(time.time())

    def record_failure(self, project_id: str) -> None:
        """Record a failed revision. Trips circuit breaker after threshold."""
        with self._lock:
            state = self._state[project_id]
            state.consecutive_failures += 1
            if state.consecutive_failures >= self._failure_threshold:
                state.cooldown_until = time.time() + self._cooldown_seconds

    def is_tripped(self, project_id: str) -> bool:
        """Check if the circuit breaker is currently tripped for this project."""
        with self._lock:
            state = self._state[project_id]
            return state.cooldown_until > time.time()
```

**Interface summary:**

| Method | Signature | Called when |
|--------|-----------|-------------|
| `can_proceed(project_id)` | `-> bool` | Before each blueprint write (workflow Phase 4) |
| `record_success(project_id)` | `-> None` | After a successful blueprint_create/blueprint_update |
| `record_failure(project_id)` | `-> None` | After a failed blueprint_create/blueprint_update |
| `is_tripped(project_id)` | `-> bool` | Diagnostic — check if breaker is tripped |

**Instantiation:** Singleton module-level instance in `daemon/services/blueprint_rate_limiter.py`, or injected via the service layer. The blueprinter agent accesses it through its tool calls (the blueprint write tools check the rate limiter internally, OR the blueprinter's system prompt instructs it to check before writing — the tool-level check is more robust).

> **Recommendation:** The rate limiter check should live INSIDE the `blueprint_create` and `blueprint_update` tool implementations (owned by Phase 3 worker), not in the agent prompt. This makes it impossible to bypass. The blueprinter's `rule.md` also instructs it to check, but the tool is the enforcement point.

---

### 4.8 core.md logic (Task 2 — embedded in prompt)

Per overview §10.5 and §7.3, core.md maintenance has special rules embedded in the blueprinter's workflow and rules:

1. **Highest priority.** When drift is detected anywhere, the blueprinter reviews core.md first before area blueprints. (workflow.md Phase 3)

2. **No self-referential edits.** The blueprinter's prompt (rule.md Cardinal #4) instructs it to never revise core.md based on its own behavior. It does not create a "blueprinter" blueprint.

3. **Manual edit preservation.** When `core.md` has `source = "manual"`, the blueprinter uses a higher confidence threshold. It prefers to leave manual content untouched unless drift is unambiguous. (rule.md Cardinal #3)

4. **Word limit enforcement.** core.md must stay 300–500 words. If the project outgrows the limit, the blueprinter splits overflow into area blueprints rather than exceeding the limit. (rule.md Cardinal #5)

5. **System-prompt duplication check.** The blueprinter checks blueprint content for overlap with system-prompt material before writing. If detected, it trims or restructures. (rule.md Cardinal #6)

---

### 4.9 Bootstrap Seeding Path (Task 7)

**Problem:** A brand-new project (or a project where blueprints were never created) has an empty corpus. Without a bootstrap path, the system has no `core.md` to inject, and the matching engine has nothing to match against — Blueprint adds zero value on first use.

**Solution (Option A — self-healing via blueprinter):** The blueprinter agent, on its first invocation (post-experience trigger or first daily-scan wake) for a project, checks whether the corpus is empty:

```python
# In blueprinter workflow (soul.md / workflow.md), first step:
core = await repository.get_core(project_id)
if core is None:
    # Corpus is empty — bootstrap core.md from project metadata.
    # This is self-healing: no manual seed required.
    await _bootstrap_core_blueprint(project_id, repository, project_meta)
    return  # one action per run (rate limit); area blueprints created on subsequent runs
```

**`_bootstrap_core_blueprint()` logic:**

1. Read the project's critical notes (via the existing critical-notes API / repository).
2. Read `.agents/shared/context.md` if it exists (project state summary).
3. Synthesize a 300–500 word `core.md` blueprint from the combined material:
   - Tech stack (from project metadata `metadata.dev_env` etc.)
   - Top-level directory structure
   - Entry points (e.g., `dev.sh` → port 8079)
   - Key architectural patterns (extracted from critical notes tagged `[pattern]`, `[decision]`)
   - File references (point to the critical-notes source and `context.md`)
4. Generate trigger queries (3–10) for the new `core.md`.
5. Compute embeddings.
6. Write via `repository.create(project_id=..., name="core", kind="core", content=..., ...)`.
7. Log the bootstrap event for observability.

**Why this works:**
- **Self-healing** — no manual seed step is required. The first blueprinter run for any project produces a baseline `core.md` automatically.
- **Uses existing data** — critical notes and `context.md` are already maintained; the blueprinter doesn't invent architecture, it summarizes what's already documented.
- **Respects rate limit** — bootstrap is one action per run; area blueprints are created on subsequent runs (next daily scan or next qualifying post-experience trigger).
- **Manual override** — if a user manually creates a `core.md` via the UI/API before the blueprinter runs, `get_core()` returns non-None and the bootstrap is skipped. Manual seeds always win.

**Source material priority:** critical notes (highest signal — curated, severity-tagged) > `context.md` (project state) > project metadata JSON (dev_env, tags). The blueprinter prompt instructs it to prefer `[pattern]`, `[decision]`, and `[constraint]` tagged notes for architectural facts.

---

### 4.10 Coupling

- **Tight with Phase 1 (matching engine):** Blueprinter writes `trigger_queries` and `embedding` fields that the matching engine reads at injection time. Contract: trigger queries must be 3–10 natural-language strings; embeddings must be vectors of the correct dimensionality.
- **Tight with Phase 3 (CRUD API + tools):** Blueprinter calls `blueprint_create` / `blueprint_update` / `blueprint_delete` tools. The rate limiter lives inside these tools. The tool signatures must match what the blueprinter expects.
- **Loose with Phase 2 (injection integration):** Blueprinter is unaware of injection — it only writes blueprints. Injection reads what blueprinter wrote.
- **Independent of Phase 5 (frontend):** Blueprinter operates entirely in the backend.

---

### 4.11 Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | Self-re-enqueue creates duplicate daily scans | Medium | Low | Idempotency key `blueprint-daily-{project_id}-{date}`; blueprinter checks for existing future scan before scheduling |
| 2 | Keyword filter too broad → excessive post-experience triggers | Medium | Medium | Rate limiter caps revisions/hour; blueprinter no-ops when no drift found (cheap); Phase 6 calibrates keyword list |
| 3 | Rate limiter state lost on restart | Low | High | Acceptable — blueprinter rebuilds naturally on next run; rate limiting is a safety net, not a correctness requirement |
| 4 | Blueprinter LLM generates low-quality trigger queries | Medium | Medium | Phase 6 trigger-query quality audit; blueprinter prompt instructs "3–10 diverse natural-language queries a user might ask" |
| 5 | Circuit breaker permanently stuck | Medium | Low | Cooldown is time-based (auto-recovers after 10 min); `is_tripped()` is diagnostic only, not blocking |

---

### 4.12 Exit Criterion

Blueprinter revises a stale blueprint correctly in a synthetic drift scenario (experience entry about a new module → blueprinter creates/updates the corresponding area blueprint). Rate limiter prevents thrash when N drift signals fire within one hour. Circuit breaker engages after 3 consecutive tool failures and auto-recovers after cooldown.

---
---

## PHASE 5 — Frontend UI

### 5.1 Objective

A per-project blueprint management panel in the Angular frontend, supporting full CRUD, markdown editing with live preview, tag/file-ref editing, and revision history browsing. Integrated into the existing project UI alongside the skills/jobs/schedules pages.

### 5.2 Frontend Stack (confirmed from codebase)

| Aspect | Technology |
|--------|-----------|
| Framework | **Angular 21** (standalone components, signals) |
| UI Library | Angular Material + ng-zorro-antd |
| Markdown rendering | **ngx-markdown** (already a dependency) |
| Code editor | **CodeMirror 6** (`@codemirror/lang-markdown` available) |
| State | Signals (component-local, matching SkillService/WorkService pattern) |
| HTTP | `HttpClient` with constructor injection |
| Routing | Lazy-loaded standalone components via `loadComponent` |

> **Note:** The task brief assumed React (.tsx files). The codebase is **Angular** (.ts + .html templates). All file extensions and component patterns below reflect the actual Angular stack.

### 5.3 Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Create blueprint model types | none | TypeScript interfaces matching Phase 3 API response shapes |
| 2 | Create `BlueprintService` | T1 | CRUD + revision history methods; follows SkillService signal pattern |
| 3 | Create blueprint components (list, detail, editor, tag editor, revision history, create form) | T1, T2 | All views functional; standalone Angular components |
| 4 | Wire routing — add blueprint routes to `app.routes.ts` | T3 | Routes resolve; lazy-loaded; per-project scoping |
| 5 | Add navigation entry (sidebar/nav menu) | T4 | Blueprint panel accessible from project UI |
| 6 | E2E test (Playwright) | T1–T5 | User can create, edit, view, delete a blueprint through the UI |

---

### 5.4 File additions

```
frontend/src/app/
├── models/
│   └── blueprint.model.ts                    # Task 1 — TypeScript interfaces
├── services/
│   ├── blueprint.service.ts                  # Task 2 — API service
│   └── blueprint.service.spec.ts             # Task 2 — unit test
├── components/
│   └── blueprints/
│       ├── blueprint-list/
│       │   ├── blueprint-list.component.ts   # Task 3
│       │   ├── blueprint-list.component.html
│       │   └── blueprint-list.component.scss
│       ├── blueprint-detail/
│       │   ├── blueprint-detail.component.ts # Task 3
│       │   ├── blueprint-detail.component.html
│       │   └── blueprint-detail.component.scss
│       ├── blueprint-editor/
│       │   ├── blueprint-editor.component.ts # Task 3 — CodeMirror + live preview
│       │   ├── blueprint-editor.component.html
│       │   └── blueprint-editor.component.scss
│       ├── blueprint-tag-editor/
│       │   ├── blueprint-tag-editor.component.ts   # Task 3
│       │   └── blueprint-tag-editor.component.html
│       ├── revision-history/
│       │   ├── revision-history.component.ts       # Task 3
│       │   ├── revision-history.component.html
│       │   └── revision-history.component.scss
│       └── create-blueprint-form/
│           ├── create-blueprint-form.component.ts  # Task 3
│           └── create-blueprint-form.component.html
└── pages/
    └── blueprints/
        ├── blueprints.component.ts            # Task 3 — page-level container
        ├── blueprints.component.html
        └── blueprints.component.scss
```

---

### 5.5 Model types (Task 1)

**File:** `frontend/src/app/models/blueprint.model.ts`

```typescript
export type BlueprintKind = 'core' | 'area';
export type BlueprintSource = 'auto' | 'manual';

export interface Blueprint {
  id: string;
  project_id: string;
  name: string;
  kind: BlueprintKind;
  content: string;
  file_refs: FileRef[];
  tags: string[];
  trigger_queries: string[];
  version: number;
  is_active: boolean;
  source: BlueprintSource;
  created_at: string;
  updated_at: string;
}

export interface FileRef {
  path: string;
  line?: number;
  function?: string;
  note?: string;
}

export interface BlueprintSummary {
  id: string;
  name: string;
  kind: BlueprintKind;
  version: number;
  tags: string[];
  is_active: boolean;
  source: BlueprintSource;
  updated_at: string;
}

export interface BlueprintRevision {
  id: string;
  blueprint_id: string;
  version: number;
  content_snapshot: string;
  change_source: 'auto_blueprinter' | 'manual_user' | 'manual_api';
  changed_by: string;
  reason: string | null;
  created_at: string;
}

export interface BlueprintCreate {
  name: string;
  kind: BlueprintKind;
  content: string;
  tags?: string[];
  file_refs?: FileRef[];
}

export interface BlueprintUpdate {
  content: string;
  tags?: string[];
  file_refs?: FileRef[];
  trigger_queries?: string[];
}

export interface BlueprintFilters {
  project_id?: string;
  kind?: BlueprintKind;
  is_active?: boolean;
}
```

---

### 5.6 API service (Task 2)

**File:** `frontend/src/app/services/blueprint.service.ts`

Follows the exact pattern of `SkillService` (constructor-injected `HttpClient`, signals for state, `catchError` on list returning empty array, re-throw on mutations).

```typescript
@Injectable({ providedIn: 'root' })
export class BlueprintService {
  private readonly http = inject(HttpClient);
  private readonly API_BASE = '/api/blueprints';

  // Signals — matches SkillService/WorkService shape
  readonly blueprints = signal<BlueprintSummary[]>([]);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);

  list(filters?: BlueprintFilters): Observable<BlueprintSummary[]> { ... }
  get(projectId: string, blueprintId: string): Observable<Blueprint> { ... }
  create(projectId: string, payload: BlueprintCreate): Observable<Blueprint> { ... }
  update(projectId: string, blueprintId: string, payload: BlueprintUpdate): Observable<Blueprint> { ... }
  delete(projectId: string, blueprintId: string): Observable<void> { ... }
  getRevisions(projectId: string, blueprintId: string): Observable<BlueprintRevision[]> { ... }
}
```

| Method | HTTP | Notes |
|--------|------|-------|
| `list(filters?)` | `GET /api/blueprints?project_id=...&kind=...&active_only=...` | Unwraps `{items}` envelope; pushes to `blueprints` signal; swallows error → `[]` |
| `get(projectId, id)` | `GET /api/blueprints/{projectId}/{id}` | Returns full content + trigger_queries + file_refs |
| `create(projectId, payload)` | `POST /api/blueprints` | Server generates trigger_queries + embeddings |
| `update(projectId, id, payload)` | `PUT /api/blueprints/{projectId}/{id}` | Sets `source = manual` server-side |
| `delete(projectId, id)` | `DELETE /api/blueprints/{projectId}/{id}` | Soft-delete (`is_active = false`) |
| `getRevisions(projectId, id)` | `GET /api/blueprints/{projectId}/{id}/revisions` | Paginated revision list |

---

### 5.7 Components (Task 3)

#### 5.7.1 — `BlueprintsComponent` (page container)

**Location:** `frontend/src/app/pages/blueprints/blueprints.component.ts`

**Responsibility:** Top-level page. Hosts the filter bar, list view, and routes to detail/edit/create sub-views. Mirrors the `SkillsComponent` pattern (signals, computed selectors, inline filter bar).

**Props/Inputs:** `projectId` (from route param). Internal state: `blueprints` signal (from `BlueprintService`), `loading`, `error`.

**Template structure:**
- Project selector (if not route-scoped) — reuse `SearchableSelectComponent`
- Filter bar: kind (core/area/all), active/inactive toggle
- Blueprint list (cards or table)
- "Create Blueprint" button → routes to create form

#### 5.7.2 — `BlueprintListComponent`

**Responsibility:** Renders a table/card list of `BlueprintSummary[]`. Shows name, kind badge, version, tags (chips), source badge (auto/manual), last updated. Click navigates to detail.

**Inputs:** `@Input() blueprints: BlueprintSummary[]`, `@Input() loading: boolean`.
**Outputs:** `@Output() select = EventEmitter<string>()` (blueprint id).

#### 5.7.3 — `BlueprintDetailComponent`

**Responsibility:** Read-only detail view of a single blueprint. Renders markdown content via `ngx-markdown`, displays file references as a list with clickable paths, shows tags as chips, shows version + source + lineage. Includes "Edit" and "View History" buttons.

**Inputs:** `@Input() blueprint: Blueprint`.
**Outputs:** `@Output() edit = EventEmitter<void>()`, `@Output() viewHistory = EventEmitter<void>()`.

**Markdown rendering:** Uses `<markdown [data]="blueprint.content""></markdown>` from `ngx-markdown` (already a dependency — no new library needed).

#### 5.7.4 — `BlueprintEditorComponent`

**Responsibility:** Markdown editor with live preview for creating/editing blueprint content. Uses **CodeMirror 6** (`@codemirror/lang-markdown` — already a dependency) for the editor pane and `ngx-markdown` for the live preview pane. Split-pane layout (editor | preview).

**Inputs:** `@Input() blueprint?: Blueprint` (undefined = create mode), `@Input() projectId: string`.
**Outputs:** `@Output() saved = EventEmitter<Blueprint>()`, `@Output() cancelled = EventEmitter<void>()`.

**Editor setup:**
```typescript
// CodeMirror extensions for markdown editing
const extensions = [
  markdown(),           // @codemirror/lang-markdown
  oneDark,              // @codemirror/theme-one-dark
  lineNumbers(),
  EditorView.lineWrapping,
];
```

**Form fields:**
- Name (text input, disabled in edit mode — name is immutable after creation)
- Kind (core/area selector, disabled in edit mode)
- Content (CodeMirror editor — main pane)
- Tags (`BlueprintTagEditorComponent`)
- File references (inline list editor — path, line, function, note)

**Live preview:** Right pane renders `markdown` component bound to the editor's content signal. Updates in real-time as the user types.

#### 5.7.5 — `BlueprintTagEditorComponent`

**Responsibility:** Tag chip input. Add/remove string tags. Uses Angular Material `MatChipsModule` with `MatChipInput`. Tags are user-editable (distinct from LLM-generated trigger_queries, which are read-only in the UI but visible in the detail view).

**Inputs:** `@Input() tags: string[]`.
**Outputs:** `@Output() tagsChange = EventEmitter<string[]>()`.

#### 5.7.6 — `RevisionHistoryComponent`

**Responsibility:** Displays the revision timeline for a blueprint. Shows version number, change_source (auto_blueprinter/manual_user/manual_api), changed_by, reason, timestamp. Optionally renders a diff between the selected revision and the current content.

**Inputs:** `@Input() blueprintId: string`, `@Input() projectId: string`.
**Behavior:** Calls `BlueprintService.getRevisions()` on init. Displays a timeline/list. Selecting a revision shows its `content_snapshot` in a read-only markdown view. "Restore" button copies the snapshot content into a new edit session (does not auto-rollback — user must explicitly save).

**Diff rendering (optional):** Use `@codemirror/merge` (already a dependency) for side-by-side diff between revision snapshot and current content.

#### 5.7.7 — `CreateBlueprintFormComponent`

**Responsibility:** Standalone form for creating a new blueprint. Simpler than the full editor — name, kind, content (textarea or CodeMirror), initial tags. Server generates trigger_queries + embeddings on POST.

**Inputs:** `@Input() projectId: string`.
**Outputs:** `@Output() created = EventEmitter<Blueprint>()`, `@Output() cancelled = EventEmitter<void>()`.

---

### 5.8 Routing (Task 4)

**File:** `frontend/src/app/app.routes.ts`

Add blueprint routes following the existing lazy-load `loadComponent` pattern. Per-project scoping matches the `projects/:projectId/...` convention already used for workspace and instances.

```typescript
// Add to routes array:
{
  path: 'projects/:projectId/blueprints',
  loadComponent: () => import('./pages/blueprints/blueprints.component').then(m => m.BlueprintsComponent),
  title: 'Project Blueprints',
},
{
  path: 'projects/:projectId/blueprints/:blueprintId',
  loadComponent: () => import('./components/blueprints/blueprint-detail/blueprint-detail.component').then(m => m.BlueprintDetailComponent),
  title: 'Blueprint Detail',
},
{
  path: 'projects/:projectId/blueprints/:blueprintId/edit',
  loadComponent: () => import('./components/blueprints/blueprint-editor/blueprint-editor.component').then(m => m.BlueprintEditorComponent),
  title: 'Edit Blueprint',
},
{
  path: 'projects/:projectId/blueprints/:blueprintId/history',
  loadComponent: () => import('./components/blueprints/revision-history/revision-history.component').then(m => m.RevisionHistoryComponent),
  title: 'Blueprint History',
},
```

**Navigation entry (Task 5):** Add a "Blueprints" link to the project navigation sidebar/menu, visible only when a project is selected. Link: `/projects/{{projectId}}/blueprints`.

---

### 5.9 State management

**Decision:** Component-local signals. No new store (no Redux/Zustand/NGRX in the project).

The codebase uses Angular signals (`signal()`, `computed()`) exclusively for component state. `BlueprintService` exposes `blueprints`, `loading`, `error` signals — same pattern as `SkillService` and `WorkService`. Individual detail/edit components manage their own local state. No global blueprint store needed — the list signal in the service is shared across the list and page container.

---

### 5.10 Coupling

- **Tight with Phase 3 (CRUD API):** Every service method maps 1:1 to a Phase 3 REST endpoint. The API response shapes must match the model types exactly.
- **Independent of Phases 1, 2, 4:** The frontend does not know about the matching engine, injection pipeline, or blueprinter. It only talks to the CRUD API.

---

### 5.11 Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | Markdown editor performance on large blueprints | Low | Low | Blueprints are capped at 200–500 words (~2K chars); CodeMirror handles this trivially |
| 2 | Trigger-query visibility confuses users | Low | Medium | UI labels: "Tags (editable)" vs "Trigger Queries (auto-generated)"; trigger queries visible read-only in detail view |
| 3 | Revision diff rendering complexity | Medium | Low | Start with read-only snapshot view; add CodeMirror merge diff as enhancement |

---

### 5.12 Exit Criterion

User can perform all CRUD operations (create, read, update, delete) through the UI. Revision history is browsable with snapshots visible. Markdown editor provides live preview. All operations are per-project scoped.

---
---

## PHASE 6 — Evaluation & Tuning

### 6.1 Objective

Calibrate matching thresholds, fusion weights, and blueprinter rate limits based on production behavior. Determine whether LLM rerank fallback is needed. This is the ongoing calibration phase — it starts after Phase 0 (contract spike) establishes initial values and continues indefinitely.

### 6.2 Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Build threshold calibration analysis script | Phase 1 structured logs | Script produces score distribution + recommended threshold |
| 2 | Run no-match rate analysis | T1, Phase 2 deployed | No-match rate measured; target threshold adjusted |
| 3 | Tune BM25/vector fusion weights (α, β) | T1 | Grid search or bandit over historical matches; optimal α, β selected |
| 4 | Trigger-query quality audit | Phase 4 deployed | Sample blueprints audited; low-quality trigger queries regenerated |
| 5 | Calibrate blueprinter rate limit | Phase 4 deployed | Revisions/hour tuned from observed drift frequency |
| 6 | LLM rerank fallback decision gate | T1–T3 | Decision documented: adopt or reject LLM rerank based on recall metrics |
| 7 | Compile metrics report | T1–T6 | All metrics measured against targets; tuning recommendations documented |

---

### 6.3 Threshold calibration (Task 1)

**Data source:** The `blueprint_match` structured log instrumented in Phase 1 (overview §5.4.1). Every match emits:

```python
logger.info("blueprint_match", extra={
    "instance_id": instance_id,
    "query_source": "task_only" | "task+context" | "task+context+skill",
    "query_length": len(query),
    "matched_count": len(matched),
    "matched_ids": [b["blueprint_id"] for b in matched[:5]],
    "top_score": matched[0]["score"] if matched else 0.0,
})
```

**Analysis script:** `scripts/blueprint_threshold_analysis.py`

**Input:** Aggregated `blueprint_match` log entries (collected over N matches in production — target N ≥ 200 before first calibration).

**Algorithm:**
1. Extract the `top_score` from every match event
2. Build a score distribution histogram
3. Identify the "elbow" — the score value where there's a natural separation between high-confidence matches (relevant) and low-confidence matches (noise)
4. Apply K-means (k=2) or a simple bimodal gap detection on the score distribution
5. Output: recommended threshold (the elbow score), confidence interval, percentage of matches that would be retained/dropped

**Decision rule:** The threshold is set at the elbow. Matches below the threshold are dropped (only core.md injected). The threshold is re-calibrated periodically (monthly initially, quarterly once stable).

```python
# scripts/blueprint_threshold_analysis.py (shape)
def find_threshold(scores: list[float]) -> float:
    """Find the elbow in the score distribution.
    
    Uses the largest gap method: sort scores, find the largest
    gap between adjacent percentiles in the 0.3–0.7 range
    (the likely boundary zone).
    """
    sorted_scores = sorted(scores)
    # Focus on the boundary zone
    lower = int(len(sorted_scores) * 0.3)
    upper = int(len(sorted_scores) * 0.7)
    boundary_zone = sorted_scores[lower:upper]
    
    max_gap = 0.0
    threshold = 0.5  # default
    for i in range(1, len(boundary_zone)):
        gap = boundary_zone[i] - boundary_zone[i - 1]
        if gap > max_gap:
            max_gap = gap
            threshold = (boundary_zone[i] + boundary_zone[i - 1]) / 2
    
    return threshold
```

---

### 6.4 No-match rate analysis (Task 2)

**Definition:** "No-match" = a first-message receipt where ONLY core.md was injected (no area blueprint cleared the threshold).

**Measurement:** From the `blueprint_match` log, `matched_count = 0` (or `matched_ids` is empty after threshold gate).

**Target:** < 20% no-match rate on tasks that genuinely relate to a represented architectural area. Higher no-match rate is acceptable for truly novel/generic tasks.

**Decision tree:**
```
Is no-match rate > 20%?
    → Yes: Are the missed blueprints' trigger queries adequate?
        → Yes: Lower the threshold (Task 1 calibration)
        → No: Improve trigger queries (Task 4 audit) → re-measure
    → No: Threshold is well-calibrated. Monitor.
```

**Query source breakdown:** The `query_source` field (`task_only` vs `task+context` vs `task+context+skill`) reveals whether the enrichment signals (context parameter, skill content) improve match quality. If `task+context+skill` has a significantly lower no-match rate, document the enrichment as valuable.

---

### 6.5 Fusion weight tuning — α (BM25) and β (vector) (Task 3)

**Current formula:** `score = α · normalized_bm25 + β · vector_similarity` (overview §5.1)

**Initial values (from Phase 0):** α = 0.5, β = 0.5 (equal weight).

**Tuning method — grid search over historical matches:**

If a labeled set exists (from Phase 0 contract spike or manual annotation of N matches as relevant/irrelevant):
1. Collect all historical matches with their BM25 score, vector score, and ground-truth label
2. Grid search over α ∈ {0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8} (β = 1 − α)
3. For each α, compute top-1 accuracy and top-4 coverage on the labeled set
4. Select the α that maximizes top-1 accuracy while keeping top-4 coverage ≥ 80%

**Tuning method — blueprinter feedback signals (if no labeled set):**

Use the blueprinter's own revision signals as a proxy: if the blueprinter frequently creates/updates a blueprint whose trigger queries never match, that blueprint's trigger queries are weak. This is indirect signal — use it to identify problem blueprints, not to tune α/β directly.

**Script:** `scripts/blueprint_fusion_tuning.py`

---

### 6.6 Trigger-query quality audit (Task 4)

**Process:**
1. Sample 10–20 blueprints across different areas (core + area)
2. For each blueprint, read its 3–10 trigger queries
3. Simulate: would these queries match against real task messages from recent traffic?
4. Rate each blueprint's trigger queries: Good / Marginal / Poor
5. For Poor/Marginal: call `blueprint_update` with regenerated trigger queries (the blueprinter can do this automatically, or a human can do it via the UI)

**Quality criteria for trigger queries:**
- Diversity: queries cover different phrasings (not 10 variations of the same sentence)
- Specificity: queries are specific enough to match this blueprint, not every blueprint
- Naturalness: queries sound like what a user or parent agent would actually write
- Coverage: queries cover the main use cases of the blueprint's architectural area

**Regeneration:** The blueprinter's prompt instructs it to generate queries in the form "How do I...?", "Where is the...?", "What pattern does the project use for...?" — natural task-message phrasings.

---

### 6.7 Rate limit calibration (Task 5)

**Open item O4:** "Blueprinter rate limit value (revisions/hour/project)"

**Measurement:** After Phase 4 is deployed, observe:
- How many revisions the blueprinter attempts per hour (from rate limiter logs)
- How many revisions are blocked by the rate limit
- Drift frequency (how often experience entries trigger the blueprinter)

**Decision:**
- If revisions are frequently blocked → increase `max_revisions_per_hour` (e.g., 5 → 10)
- If revisions rarely hit the cap → decrease (e.g., 5 → 3) to save resources
- If the circuit breaker trips frequently → investigate root cause (tool failures, not rate)

**Initial value:** `max_revisions_per_hour = 5`, `failure_threshold = 3`, `cooldown_seconds = 600` (from §4.7). Re-evaluate after 2 weeks of production data.

---

### 6.8 LLM rerank fallback decision gate (Task 6)

**Context:** The matching engine uses BM25 + vector + trigger-query embeddings (overview §5.1). The skill injection pipeline adds an LLM rerank stage. Blueprint deliberately omits LLM rerank at design scope. Phase 6 evaluates whether to add it.

**Decision gate:**

```
After Phase 6 tuning (threshold, fusion weights, trigger queries):

Is top-4 coverage ≥ 80% on labeled/audited matches?
    → Yes: LLM rerank NOT needed. BM25 + vector + triggers is sufficient.
           Document the decision. Close open item O5.
    → No:  Is the recall gap attributable to the fusion algorithm
           (fixable by tuning) or to semantic understanding
           (fixable only by LLM rerank)?
        → Semantic gap: Add LLM rerank as Stage 3 (after BM25 + vector).
           Cost: one LLM call per first-message receipt (match-once, so
           one call per instance lifetime — acceptable). Instrument and
           re-evaluate after 1 week.
        → Tuning gap: Re-iterate Tasks 1–4 before adding LLM rerank.
```

**If LLM rerank is adopted:**
- Insert as Stage 3 in the matching pipeline (after BM25 + vector fusion)
- Pass the top-K candidates (e.g., top-10) to the LLM with the query and ask it to rank by relevance
- Use the blueprinter's separately configured evolution model (cheaper model, per the skill evolution decisions) to minimize cost
- Cap candidates at 10 to bound token cost
- Re-run threshold calibration (Task 1) since LLM rerank changes the score distribution

---

### 6.9 Metrics table

| Metric | Definition | How to Measure | Target | Source |
|--------|-----------|----------------|--------|--------|
| No-match rate | % of first-message receipts where only core.md injected (no area match) | `blueprint_match` log: `matched_count = 0` | < 20% for area-relevant tasks | Phase 1 structured log |
| Top-1 accuracy | % of matches where the highest-scored blueprint is the correct one | Manual audit or labeled set from Phase 0 | ≥ 80% | Phase 0 + Phase 6 audit |
| Top-4 coverage | % of tasks where the correct blueprint appears in the top-4 matched set | Manual audit or labeled set | ≥ 80% | Phase 0 + Phase 6 audit |
| Blueprint revision quality | Qualitative assessment of blueprinter-generated content | Manual sample of 10 revisions per month | "Accurate and useful" on 8/10 sampled | Manual review |
| Token budget per first-turn | Total tokens in the persistent block on first message | Sum of all persistent HumanMessage tokens | < 10K tokens (per overview §6.5) | Checkpoint inspection |
| 5-slot cap impact | How often the 5-slot cap causes a relevant blueprint to be dropped | Compare top-4 matched vs top-8 BM25+vector results | < 5% of cases where #6–8 would have been relevant | Analysis script |
| Rate-limit blocks | How often the blueprinter is blocked by the rate limiter | Rate limiter logs | < 10% of revision attempts blocked (otherwise increase cap) | `BlueprintRateLimiter` logging |
| Trigger-query match contribution | How often trigger-query embeddings (vs content embeddings) drive a match | Instrument: which embedding source matched | Trigger queries contribute to ≥ 30% of matches | Phase 1 matcher instrumentation |
| Query source effectiveness | Does adding context/skill enrichment improve match quality? | Compare no-match rate by `query_source` | `task+context+skill` < `task_only` no-match rate | `blueprint_match` log `query_source` field |
| Single-area-blueprint injection | Verify single-area-blueprint projects still inject area content (BM25 norm collapses to 0 when only 1 candidate → score = β·vec only) | Check `blueprint_match` logs for projects with 1 area blueprint; verify `matched_count > 0` | ≥ 1 area match injected when the area is relevant | Phase 1 matcher instrumentation |

---

### 6.10 Coupling

- **Tight with Phase 0 (contract spike):** Phase 0 establishes initial threshold, fusion weights, and baseline recall. Phase 6 refines these in production.
- **Tight with Phase 1 (matching engine):** Phase 6 reads the `blueprint_match` structured log and may modify threshold + fusion weights.
- **Tight with Phase 4 (blueprinter):** Phase 6 calibrates the rate limiter and audits trigger-query quality (which the blueprinter generates).
- **Independent of Phase 5 (frontend):** Evaluation is backend-only.

---

### 6.11 Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | Insufficient labeled data for fusion tuning | Medium | Medium | Use blueprinter feedback signals as proxy; start with Phase 0 sample and grow manually |
| 2 | Threshold drifts over time as corpus grows | Medium | Medium | Re-calibrate monthly initially; build automated alerting on no-match rate |
| 3 | LLM rerank cost prohibitive | High | Low | Only adopt if recall is demonstrably insufficient; use cheaper evolution model; cap candidates at 10 |
| 4 | Trigger queries degrade as project evolves | Medium | Medium | Daily scan includes trigger-query quality check; blueprinter regenerates low-quality queries |

---

### 6.12 Exit Criterion

No-match rate, top-K recall, and revision quality meet targets defined in the metrics table (§6.9). LLM rerank decision (open item O5) is documented as adopted or rejected with evidence. Rate limit value (open item O4) is calibrated from production data. Fusion weights (open item O2) and threshold (open item O1) are set to production values with documented rationale.
