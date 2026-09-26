# Setup Guide

## agents-ensemble v0.3.6

A comprehensive installation and configuration guide for the agents-ensemble multi-agent orchestration system.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Quick Start](#quick-start)
3. [Installation Methods](#installation-methods)
4. [Configuration](#configuration)
5. [Database](#database)
6. [Frontend Setup](#frontend-setup)
7. [First Run](#first-run)
8. [Troubleshooting](#troubleshooting)
9. [Next Steps](#next-steps)

---

## Prerequisites

| Prerequisite | Version | Required | Notes |
|-------------|---------|----------|-------|
| Python | 3.11+ | Yes | Core runtime for the backend |
| Node.js | 18+ | Yes | Required for frontend build |
| uv | Latest | Recommended | Fast Python package manager |
| pip | Latest | Optional | Alternative to uv |
| OpenAI-compatible API | — | Yes | API key required for LLM access |
| Docker | Latest | Optional | Required only for LightRAG |
| Git | Any | Yes | For cloning the repository |

### Verifying Python Version

```bash
python --version
# Must output Python 3.13.x or higher
```

### Verifying Node.js Version

```bash
node --version
# Must output v18.x.x or higher
```

### Installing uv (Recommended)

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Or via pip
pip install uv
```

---

## Quick Start

Get agents-ensemble running in under 5 minutes:

```bash
# 1. Clone the repository
git clone <repository-url>
cd agents-ensemble

# 2. Create virtual environment and install dependencies
python -m venv .venv
source .venv/bin/activate
uv sync

# 3. Configure environment
cp .env.example .env
# Edit .env and set OPENAI_API_KEY=your-api-key-here

# 4. Start development server
./dev.sh
```

That's it! The server starts on port **8079**. Access the web UI at `http://localhost:8079`.

---

## Installation Methods

### Method 1: Development Setup (dev.sh)

Recommended for local development with hot-reload support.

#### Step 1: Clone the Repository

```bash
git clone <repository-url>
cd agents-ensemble
```

#### Step 2: Create and Activate Virtual Environment

```bash
python -m venv .venv
source .venv/bin/activate
```

> **Note:** On Windows, use `.venv\Scripts\activate` instead.

#### Step 3: Install Dependencies

```bash
uv sync
```

> **Alternative:** If not using uv, run `pip install -e .`

#### Step 4: Configure Environment Variables

```bash
cp .env.example .env
```

Edit `.env` with your preferred text editor:

```bash
nano .env  # or vim, code, etc.
```

**Required setting:**

```env
OPENAI_API_KEY=sk-your-actual-api-key
```

#### Step 5: Start Development Server

```bash
./dev.sh
```

The `dev.sh` script:

- Activates `.venv` if available
- Loads environment variables from `.env`
- Creates `./data_dev/` directory for development data
- Sets `PORT=8079`
- Runs uvicorn with `--reload` for hot-reload

#### Step 6: Verify Server is Running

```bash
curl http://localhost:8079/api/health
```

Expected response:

```json
{"status": "ok", "version": "0.3.6"}
```

---

### Method 2: Production Install (make install)

Complete production installation to `~/agents-ensemble/`.

#### Step 1: Clone and Prepare

```bash
git clone <repository-url>
cd agents-ensemble
```

#### Step 2: Run Production Install

```bash
make install
```

This command performs the following steps in order:

1. **Builds frontend** — Runs `make build` (npm install + Angular build)
2. **Builds PyInstaller binary** — Creates standalone executable
3. **Creates installation directory** — `~/agents-ensemble/`
4. **Copies required files:**
   - `agents/` — Agent definitions
   - `config.yaml` — Production configuration
   - `.env` — Environment variables (if exists)
   - `frontend/dist/` — Built frontend assets

#### Step 3: Configure for Production

```bash
cd ~/agents-ensemble
cp .env.example .env
nano .env
```

Set your production values:

```env
OPENAI_API_KEY=sk-your-api-key
PORT=8088
```

#### Step 4: Run Production Server

```bash
cd ~/agents-ensemble
./ensemble-prod
```

The production binary runs on port **8088** by default.

> **Note:** Ensure no other process is using port 8088 before starting.

---

### Method 3: PyInstaller Binary

Build a standalone executable without full installation.

#### Step 1: Build the Binary

```bash
make pyinstaller
```

Output: `dist/ensemble-prod`

#### Step 2: Prepare Runtime Directory

The binary requires these files/directories in its working directory:

```
working-dir/
├── config.yaml      # Configuration file
├── .env            # Environment variables
├── agents/         # Agent definitions
└── frontend/dist/  # Built frontend
```

#### Step 3: Run the Binary

```bash
./dist/ensemble-prod
```

---

### Method 4: Local Production Test (start.sh)

Test production-like setup locally without full installation.

```bash
./start.sh
```

This script:

- Loads `.env.prod` (falls back to `.env`)
- Uses port from environment (default 8079)
- Runs without hot-reload
- Uses `./data/` instead of `./data_dev/`

---

## Configuration

### Environment Variables

Environment variables are loaded from `.env` file and override config.yaml values.

| Variable | Type | Required | Default | Description |
|----------|------|----------|---------|-------------|
| `OPENAI_API_KEY` | string | **Yes** | — | Your OpenAI API key for LLM access |
| `OPENAI_BASE_URL` | string | No | `https://api.openai.com/v1` | API endpoint base URL (for proxies/custom endpoints) |
| `OPENAI_MODEL` | string | No | `gpt-4` | Default model for agent conversations |
| `OPENAI_MODEL_TITLE` | string | No | — | Cheaper model for generating conversation titles |
| `OPENAI_MODEL_VISION` | string | No | — | Vision-capable model for image analysis |
| `HOST` | string | No | `0.0.0.0` | Server bind address |
| `PORT` | integer | No | `8079` (dev) / `8088` (prod) | Server port |
| `LIGHTRAG_HOST` | string | No | `http://lightrag.lightrag.svc.cluster.local:9621` | LightRAG service host |
| `LIGHTRAG_API_KEY` | string | No | — | LightRAG authentication key |
| `LIGHTRAG_WORKSPACE` | string | No | — | LightRAG workspace identifier (optional, not in .env.example) |
| `LIGHTRAG_TIMEOUT` | integer | No | — | LightRAG request timeout in seconds (optional, not in .env.example) |

#### Creating .env File

```bash
cp .env.example .env
```

**Minimal .env for development:**

```env
OPENAI_API_KEY=sk-your-api-key-here
```

**Custom endpoint example:**

```env
OPENAI_API_KEY=your-api-key
OPENAI_BASE_URL=https://api.my-proxy.com/v1
OPENAI_MODEL=gpt-4o
```

---

### config.yaml Reference

The `config.yaml` file contains all application configuration. Environment variables can be referenced using `${VAR_NAME:-default}` syntax.

#### LLM Configuration

```yaml
llm:
  base_url: ${OPENAI_BASE_URL:-https://api.openai.com/v1}
  api_key: ${OPENAI_API_KEY}
  model: ${OPENAI_MODEL:-gpt-4}
  model_title: ${OPENAI_MODEL_TITLE:-}
  model_vision: ${OPENAI_MODEL_VISION:-}
  temperature: 0.7
  request_timeout: 610
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `llm.base_url` | string | `https://api.openai.com/v1` | Base URL for LLM API requests |
| `llm.api_key` | string | (from env) | API key for authentication |
| `llm.model` | string | `gpt-4` | Default model for agent conversations |
| `llm.model_title` | string | — | Model used for generating conversation titles (optional, can use cheaper model) |
| `llm.model_vision` | string | — | Vision-capable model for image analysis tasks |
| `llm.temperature` | float | `0.7` | Default temperature for LLM requests (0.0-2.0) |
| `llm.request_timeout` | integer | `610` | Timeout for LLM API requests in seconds |

#### Daemon Configuration

```yaml
daemon:
  host: ${HOST:-0.0.0.0}
  port: ${PORT:-8088}
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `daemon.host` | string | `0.0.0.0` | Network interface to bind to (`0.0.0.0` = all interfaces) |
| `daemon.port` | integer | `8088` | Port for the API server |

#### Limits Configuration

```yaml
limits:
  max_children_per_instance: 50
  instance_timeout_minutes: 60
  graph_recursion_limit: 200
  llm_concurrency: 10
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `limits.max_children_per_instance` | integer | `50` | Maximum child instances per parent instance |
| `limits.instance_timeout_minutes` | integer | `60` | Auto-shutdown timeout for idle instances (minutes) |
| `limits.graph_recursion_limit` | integer | `200` | Maximum recursion depth for LangGraph execution (global default) |
| `limits.llm_concurrency` | integer | `10` | Maximum concurrent LLM API requests |
| `limits.governor_recursion_guard_enabled` | boolean | `true` | Master kill-switch for the governor recursive-spawn guard. Override via `LIMITS_GOVERNOR_RECURSION_GUARD_ENABLED` (truthy values enable; `0` disables). Restart required — cached at boot. |
| `limits.max_governor_ancestors` | integer | `1` | Max governor count allowed in the parent ∪ ancestors chain (K). When the parent-inclusive count ≥ K, governor spawns are refused with a corrective HINT. Override via `LIMITS_MAX_GOVERNOR_ANCESTORS`. `0` disables the guard entirely. Restart required. |
| `ENSEMBLE_ORPHAN_F1_ENABLED` (env var — **no config.yaml key**) | env var (string) | unset → ON | **Pattern-f1 orphan-ACTIVE-JobItem recovery kill-switch** (f1-misfire fix batch, incident 2026-08-31, JobItem 69a34b35). **Env-only:** there is NO `services.orphan_f1_enabled` (or any other) config.yaml key — a yaml flip would be a silent no-op. When **ON (default)**, the drift reconciler's Pattern (f1) DEAD-finalizes `admission_state='active'` JobItems with no Task linked via `work_id` (past grace + subtree-alive guard). When **OFF**, the f1 sub-shape is fully inert — rows are skipped with an `orphan_active_skipped_f1_disabled` detail; Patterns (a)–(e) and the (f2) sub-shape are NOT gated. Truthy spellings: `1`/`true`/`yes`/`on`; falsy: `0`/`false`/`no`/`off`; blank/unset → ON (the instant-revert path for an ON-default switch is a falsy spelling). Read once at boot and cached — restart required to flip; a one-shot boot log names the resolved state. Related knob: `services.f1_tree_activity_max_age_seconds` (default 900s) — the subtree-alive guard window. |
| `WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED` (env var; config.yaml mirror `report_integrity.b_terminal_waiting_guard_enabled`) | boolean | `false` (0) | **(b) terminal-child-aware waiting guard** (wc-wake-report-integrity Wave 2, `feature/wc-wake-report-integrity`). **OFF (default, ship state) = LOG-ONLY mode**: the stage-ii `[ReportIntegrityGuard]` WARNING still fires at the parent-COMPLETED stamp sites (the soak log); **no notice is ever injected**. **ON = enforcement**: when the same-tx evaluation finds a declared-waiting violation at a completion stamp, ONE adjudication notice is injected to the parent via the durable enqueue path (source `system:report-integrity-guard`); it **never blocks completion** (fail-OPEN, D2.6) and the enforcement enqueue is bounded by a 5s budget. Restart required to flip (resolved once at boot + one-shot boot log). Truthy spellings: `1`/`true`/`yes`/`on`; falsy: `0`/`false`/`no`/`off`; unset/blank/unknown → OFF. Revert path: set the env to `0` (or blank) + restart. Flip policy is OPERATOR-OWNED (D2.5-FLIP): ≤2-week stage-ii log soak, then flip on first deploy — withheld on false-fires, immediate flip on any silent-death incident. **No auto-flip exists anywhere** — no code path enables the flag. An explicit config.yaml `false` vetoes an env flip (defense-in-depth); config.yaml `true` alone never enables. The reserved name `WC_REPORT_INTEGRITY_A_PREMATURE_TURN_GUARD_ENABLED` (candidate (a)) is **reserved-unused** — no config field consumes it (C2-D2.2/D2.3). **S3 scoping (council follow-up, 2026-08-30):** this flag gates ONLY (b) enforcement. It does NOT gate the (c) passive report-sanity marker, the NR-3 junk counter, or any other Wave-1 observability instrument — their rollback seam is `SANITY_FLAG_VERSION` in `daemon/constants.py` (always-on observability). Operators flipping this flag should expect zero change in (c) marker / NR-3 counter behavior. |
| `ENSEMBLE_LEADER_ATTESTATION_MODE` (env var — **no config.yaml key**) | env var (string) | unset → `enforce` | **Leader completion attestation mode** (leader-completion-attestation Phase 4, `feature/leader-completion-attestation`). **Env-only:** there is no `attestation.mode` (or any other) config.yaml key — a yaml flip would be a silent no-op. Tri-state value: `off` \| `dry` \| `enforce`. When **`enforce` (default at ship, operator override 2026-09-06 — flipped from `dry`; dry-at-ship D2 rationale superseded)**, the gate denies + injects the in-graph nudge per FR-3 (counter increments, escalation at the bound). When **`dry`**, the gate evaluates every would-be leader END, emits a structured `leader_completion_gate` log entry with `decision=dry_log`, and **allows the END** (zero side effects — no nudge, no counter change, no flag change); useful for observation-only operation. When **`off`**, the gate does not run — `off` is the instant-revert / kill-switch (legacy behavior preserved — pre-feature baseline). Invalid values (typos) **fail OPEN** to `enforce` with a one-shot WARN (Pattern C — `daemon/services/instance_messaging.py:114-191` WC-wake resolver precedent); the daemon never refuses to start on a typo'd attestation env. Read once at boot and cached — restart required to flip (no live flip). **Promotion SOP (task 4.6) — still applicable for any future flip back to `dry` for investigation:** the canonical promotion metrics (`dry_log_total`, `dry_log_deny_predicate_total`, `enforce_denied_total`) are the operator-observable adjudication surface; operators flipping back to `dry` (e.g. for incident triage) can watch deny rates under live enforcement via `enforce_denied_total`. Immediate revert path: set `ENSEMBLE_LEADER_ATTESTATION_MODE=off` (or `dry`) + restart. The legacy single-bool env surface (any prior canonical name) is FORBIDDEN per C-5 — only `ENSEMBLE_LEADER_ATTESTATION_MODE` is honored. |
| `ENSEMBLE_LEADER_ATTESTATION_WINDOW` (env var — **no config.yaml key**) | env var (int) | unset → `3` | **Leader completion attestation window N** (Phase 4 task 4.1, the scanner lookback window). **Env-only:** no config.yaml mirror. Values `< 1` and non-integers fail OPEN to `3` with a one-shot WARN (Pattern C). O1 boot assert: `WINDOW > MIN_RECENT_WINDOW` (compaction floor; `daemon/constants.py:MIN_RECENT_WINDOW=3`) emits a separate WARN log line at boot (`N_le_min_recent_window=WARN`) — the gate **continues running** with the configured WINDOW (WARN-only, never fail-closed). Default `3` matches the floor; raising above the floor fires the WARN. Read once at boot and cached — restart required to flip. |
| `ENSEMBLE_LEADER_ATTESTATION_DENY_BOUND` (env var — **no config.yaml key**) | env var (int) | unset → `3` | **Per-instance deny bound** (D5, the escalation bound for repeated denied completions). **Env-only:** no config.yaml mirror. Values `< 1` and non-integers fail OPEN to `3` with a one-shot WARN. After `deny_bound` consecutive `Decision.DENIED` evaluations on a single instance, the gate transitions to `Decision.TERMINAL_AFTER_BOUND` — the END is allowed, the `completion_gate_escalated` flag is set on the instance row, and the counter resets to 0 (the same single atomic UPDATE handles both columns per leader ruling 2). Read once at boot and cached — restart required to flip. |

> **Per-agent override:** the global `graph_recursion_limit` is a *base* — individual
> agents can exceed it via `recursion_limit_multiplier` (e.g. `5` = 5× the base) or an
> absolute `recursion_limit`, set in the agent's `meta.json`. `worker` and `coder`
> default to `5x` so long-running working agents get a larger step budget. See
> [Agent System Guide – meta.json Fields](AGENTS.md#metajson-fields).

#### Persistence Configuration

```yaml
persistence:
  db_path: ./data/instances.db
  checkpoint_interval: 1
  checkpoint_ttl_hours: 168
  checkpoint_cleanup_interval: 24
  checkpoint_max_count: 1000
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `persistence.db_path` | string | `./data/instances.db` | Path to main SQLite database |
| `persistence.checkpoint_interval` | integer | `1` | Create checkpoint every N state updates |
| `persistence.checkpoint_ttl_hours` | integer | `168` | Delete checkpoints older than this (1 week) |
| `persistence.checkpoint_cleanup_interval` | integer | `24` | Run cleanup every N hours |
| `persistence.checkpoint_max_count` | integer | `1000` | Maximum checkpoints per instance before cleanup |

> **Note:** In development mode (`dev.sh`), data paths use `./data_dev/` instead of `./data/`.

#### Agents Configuration

```yaml
agents:
  directory: ./agents
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `agents.directory` | string | `./agents` | Directory containing agent definitions |

#### Queue Configuration

```yaml
queue:
  discard_on_startup: false
  llm_retry_transient_attempts: 10
  llm_retry_timeout_attempts: 3
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `queue.discard_on_startup` | boolean | `false` | Clear pending jobs on server restart |
| `queue.llm_retry_transient_attempts` | integer | `10` | Retry attempts for transient LLM errors |
| `queue.llm_retry_timeout_attempts` | integer | `3` | Retry attempts for timeout errors |

#### Compaction Configuration

```yaml
compaction:
  enabled: true
  threshold: 0.80
  recent_message_window: 10
  min_recent_window: 3
  # Per-model context window overrides. Substring match; longest key wins.
  # Useful when the main model and a vision/specialized sub-model have
  # different context windows. Falls back to context_window_default, then
  # the built-in MODEL_CONTEXT_LIMITS registry, then 180k.
  context_window_default: 0
  context_window_overrides: {}
  target_ratio: 0.40
  summarization_model: ""
  min_messages_before_compaction: 10
  summarization_chunk_threshold: 0.60
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `compaction.enabled` | boolean | `true` | Enable automatic conversation compaction |
| `compaction.threshold` | float | `0.80` | Compact when context reaches 80% of window |
| `compaction.recent_message_window` | integer | `10` | Keep this many recent messages separate |
| `compaction.min_recent_window` | integer | `3` | Minimum recent messages to preserve |
| `compaction.context_window_default` | integer | `0` | Fallback context window when neither overrides nor the registry match. `0` = use built-in default (180k) |
| `compaction.context_window_overrides` | object | `{}` | Per-model context windows (`model_substring → tokens`). Substring match; longest key wins. Takes priority over the built-in registry |
| `compaction.target_ratio` | float | `0.40` | Target ratio after compaction (40%) |
| `compaction.summarization_model` | string | — | Model for summarization (empty = use default) |
| `compaction.min_messages_before_compaction` | integer | `10` | Minimum messages before first compaction |
| `compaction.summarization_chunk_threshold` | float | `0.60` | Chunk long histories at 60% of window |

Example: cap a smaller vision sub-model independently of the main model.

```yaml
compaction:
  context_window_default: 128000
  context_window_overrides:
    "gpt-4o-mini-vision": 32768   # tighter window for the vision model
    "claude-3-5-haiku": 50000     # haiku has a smaller effective window
```

#### Services Configuration

```yaml
services:
  task_timeout_minutes: 60
  max_task_retries: 3
  task_retry_backoff_base: 60
  task_retry_backoff_max: 3600
  stale_task_cancel_grace_seconds: 10
  graph_timeout_minutes: 55
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `services.task_timeout_minutes` | integer | `60` | Default timeout for service tasks |
| `services.max_task_retries` | integer | `3` | Maximum retry attempts for failed tasks |
| `services.task_retry_backoff_base` | integer | `60` | Base backoff time for retries (seconds) |
| `services.task_retry_backoff_max` | integer | `3600` | Maximum backoff time (seconds) |
| `services.stale_task_cancel_grace_seconds` | integer | `10` | Grace period before cancelling stale tasks |
| `services.graph_timeout_minutes` | integer | `55` | Timeout for LangGraph execution |

#### Job System Configuration

```yaml
job_system:
  job_retry_scheduler_enabled: null
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `job_system.job_retry_scheduler_enabled` | boolean/null | `null` | Enable scheduled job retry (null = auto) |

---

## Database

### Overview

#### Leader Completion Attestation (operational runbook)

Leader completion attestation is the in-graph gate that forces the leader to call `attest_completion` before the leader graph ENDs, preventing hallucinated completion while the actual work is unfinished. The gate runs **only** on the leader agent (`agents/leader/meta.json:tools.allow` opts in via the `attestation` category — fail-closed for non-leader parents).

The default mode at ship is **`enforce`** (operator override 2026-09-06 — flipped from `dry`; dry-at-ship D2 rationale superseded). Attestation is **conditional on delegation** (2026-09-06 amendment): the gate evaluates a turn-end ONLY when the leader has dispatched a child — a `send_message` tool call — since the last real user message; missions without such delegation (quick follow-ups, chart requests, plain answers) complete without the gate firing. On a delegated mission, if the leader did not call `attest_completion`, the gate denies + injects the in-graph nudge per FR-3 (counter increments, escalation at the bound). The canonical promotion metrics (`dry_log_total`, `dry_log_deny_predicate_total`, `enforce_denied_total`) remain the operator-observable surface for watching deny rates under live enforcement — flip back to `dry` (or `off`) for incident triage / observation-only operation.

**Boot log (one-shot INFO line on every daemon start)**

The line below is emitted exactly once per startup by `InstanceManager.__init__` (Pattern C resolver mirror — same shape as the WC-wake boot log at `daemon/services/instance_messaging.py:114-191`). Operators grep for this line to confirm the resolved configuration:

```
Leader completion attestation resolved: mode=<off|dry|enforce>
    window=<N> deny_bound=<N> attestation_enabled=<true|false>
    N_le_min_recent_window=<PASS|WARN> (env MODE=, WINDOW=, BOUND=).
    Restart required to flip. See docs/setup.md (ENSEMBLE_LEADER_ATTESTATION_MODE).
```

The O1 boot assert (`WINDOW > MIN_RECENT_WINDOW`) emits a **separate WARN line** at boot (operator log-aggregators filter WARN separately from INFO):

```
N_le_min_recent_window=WARN: WINDOW=<N> > min_recent_window=<floor>;
    attestation_denied_count risk: aggressive context pressure may fold the
    attestation tool_call (gate continues running — WARN-only, never fail-closed).
```

**Default mode (ship posture)**

| Env | Default | What the gate does |
|-----|---------|--------------------|
| `ENSEMBLE_LEADER_ATTESTATION_MODE` | `enforce` | Evaluate every would-be END; if no `attest_completion` call in the window, DENY + inject the in-graph nudge (counter increments, escalation at the bound) |
| `ENSEMBLE_LEADER_ATTESTATION_WINDOW` | `3` | Scanner inspects the last 3 `AIMessage`s for an `attest_completion` tool call |
| `ENSEMBLE_LEADER_ATTESTATION_DENY_BOUND` | `3` | After 3 consecutive denials, transition to `terminal_after_bound` (escalation) |

**Dry→enforce promotion SOP (the "instrumented dry-run")**

1. **Ship at `dry`.** Operators do NOT need to flip anything — `dry` is the ship default.
2. **Observe.** Grep the log for `event=leader_completion_gate decision=dry_log`. Every entry carries the full canonical schema (`event`, `decision`, `instance_id`, `attestation_present`, `denied_count`, `gate_location`, `leader_prompt_version`, `pending_children`, `queued_or_expected_wakeups`, `live_descendants`, `attestation_required`, `messages_scanned`, `scanned_window_size`, `mode`, `scanner_window_truncated`, `scanner_summary_seen`, `user_answer_pending` — 17 fields (Stage-3 retirement 2026-09-17 dropped the log-only `attest_seen_outside_window` diagnostic; was 18), exported as `daemon.services.attestation_gate.CANONICAL_LOG_SCHEMA_FIELDS` for drift tests; `live_descendants` added 2026-09-06 as the third R2 input closing the 809e2a59 waiting_children false-deny incident class — count of descendants whose status is NOT IN {COMPLETED, TERMINATED, ERROR, FAILED}, bounded BFS at `LIVE_DESCENDANTS_BFS_CAP = 500`; `attestation_required` added 2026-09-06 (Phase 6 fastfollow) as the conditional-attestation flag — `True` when the leader dispatched a child (`send_message` tool call) since the last real user message, `False` when the gate is OFF for this turn-end; `user_answer_pending` added 2026-09-16 (incident 6a0d60c9 FIX-2) as the FIFTH legitimate-pending input — see the runbook note below).

   **Supplementary diagnostic fields** (NOT in the canonical 17-field tuple but present in every log line for FR-3 conditionality audit): `last_real_user_found` (bool — `False` in degenerate no-real-user states), `last_real_user_index` (int, `-1` when none), `first_delegation_after_last_user_index` (int, `-1` when no `send_message` tool call at-or-after the last real user message), `delegation_tool_call_total` (int — normalized count of `send_message` calls in the walked tail), `delegation_since_last_user` (bool — the scanner's high-level verdict).
3. **Adjudicate false positives** using the canonical counters (task 4.6, `daemon/services/attestation_resolver.py`):
   - `dry_log_total` — every dry-mode evaluation that ran.
   - `dry_log_deny_predicate_total` — subdivision of `dry_log_total` whose R2-deny predicate would have fired under `enforce` (the "would-have-denied" subset — `attestation_required == True AND not attested AND pending_children == 0 AND queued_or_expected_wakeups == 0 AND live_descendants == 0` — the THREE-input R2 predicate plus the new conditional-attestation gate; `live_descendants` arm added 2026-09-06, `attestation_required` arm added 2026-09-06 Phase 6 fastfollow).
   - `enforce_denied_total` — every enforce-mode `Decision.DENIED` evaluation (escalation path is **not** counted here).

   False-positive rate = `dry_log_deny_predicate_total / dry_log_total`. If the rate is acceptable for the operator's tolerance, flip to `enforce`.
4. **Flip to `enforce`:** set `ENSEMBLE_LEADER_ATTESTATION_MODE=enforce` + restart. The boot log will name the new resolved state.
5. **Revert path (instant):** set `ENSEMBLE_LEADER_ATTESTATION_MODE=off` (or `dry`) + restart. There is **no live flip** — the resolver is restart-read Pattern C.

**Postmortem query — escalation flag**

The escalation flag is the per-instance row column `completion_gate_escalated`. The gate SETS it on `terminal_after_bound` (the deny bound was reached — the same atomic UPDATE that sets the flag also resets the counter; leader ruling 2); the gate CLEARS it on the next attested allow or on a fresh-episode revive (both route through the reset op, which clears the flag together with the counter). To find escalations post-incident:

```sql
-- PostgreSQL (production)
SELECT instance_id, attestation_denied_count, completion_gate_escalated, updated_at
FROM instances
WHERE completion_gate_escalated = TRUE
ORDER BY updated_at DESC;
```

The companion log event on the escalation path is `event=leader_completion_gate_terminal_after_bound` (emitted by the gate node in `daemon/graph.py`), carrying `instance_id`, `attestation_denied_count`, and `completion_gate_escalated=true`.

**Unverified-completion surface (incident 7d4a3bd9 fix, 2026-09-26 — Fix 1)**

An escalated terminal is an UNVERIFIED completion — the mission ended without an attested completion because the deny bound or the total-event cap fired. Since the 7d4a3bd9 fix cycle, every read surface renders the DISTINCT string (canonical constant `daemon.constants.COMPLETION_GATE_ESCALATED_DISPLAY`):

```
completed (gate escalated — unverified)
```

instead of plain `completed`, on: job events (`[JOB_EVENT] Job {id}... completed (gate escalated — unverified)` via `daemon/services/work_notifier.py`), `GET /api/jobs/{id}` (`jobs_crud._job_to_response`, via the additive `WorkRecord.completion_gate_escalated` flag), `get_mission`/`list_missions` (the `liveness` value + the machine-readable `completion_gate_escalated: true` flag; the canonical `liveness` stays clean for filters/await), and the FE job card (verbatim label, warning glyph, amber). The companion observer row ties the escalated terminal to the episode's REAL job when the job was already finalized inline (the `no_job` anomaly of 7d4a3bd9): `Observer: job linkage ... already_finalized_by=<job>... gate_escalated=True (already-terminal row — no state transition, reporting-only witness)`.

**Bound-exhaustion composition gate (incident 7d4a3bd9 fix, 2026-09-26 — user ruling, amendments v3)**

Bound counting is **UNCHANGED**: every deny counts toward the deny bound, timeouts included, and the predicate `deny_bound_exceeded(denied_count, bound)` is byte-identical to pre-amendment. What changed is what happens AT exhaustion. The node tracks, per epoch, whether ANY deny event carried a SUBSTANTIVE fused-judge verdict — `not_complete` — in the checkpointed `attestation_any_substantive_deny` state channel (timeout / error / unparsable / judge-disabled = the judge never spoke; the channel resets with the ledger on attested allow and terminal):

| Bound exhausted with… | Meaning | Behavior |
|---|---|---|
| ≥1 substantive (`not_complete`) verdict | the judge SPOKE and was overridden | `terminal_after_bound` stands exactly as before — the loud `completed (gate escalated — unverified)` terminal (see above); the terminal event row now also carries `any_substantive_deny=true`. |
| ZERO substantive verdicts (all timeouts / errors / never-spoke) | the judge NEVER spoke | **NOT COMPLETE, full stop**: NO terminal write from timeouts alone — the deny+nudge cycle CONTINUES. Counting is unchanged (every deny counts; the committed counter may rise past the bound). The leader's exits are the existing ones: `attest_completion` (the deterministic trust path), finishing the work, or asking the user. The audit row `event=leader_completion_gate_bound_exhausted_never_spoke` fires on each withheld terminal. |

⚠ **C1 note (user ruling — do not "fix" without re-ruling):** the never-spoke continuation is deliberately unbounded: an epoch whose judge never delivers a verdict denies forever (no terminal fallback). This SUPERSEDES C1's strict terminal-fallback boundedness FOR THE NEVER-SPOKE CASE ONLY (rare at the 180s judge timeout; the exits exist; the false-positive cost is accepted). C1 boundedness remains FULLY INTACT for the judge-spoke path. Recorded in decisions.md D-entry 2026-09-26 (which also flags the substantive-interpretation ruling for user veto).

**Directive nudge on no-progress repeat denies (incident 7d4a3bd9 fix, 2026-09-26 — Fix 3)**

At each deny the gate snapshots transcript progress (tool-call count in the checkpointed history → `attestation_deny_progress_tools`). On a deny that is the >= 2nd consecutive deny AND has ZERO new tool calls since the prior deny snapshot, the node injects the DIRECTIVE nudge instead of the standard one (same stable id — supersedes in place; `attestation_nudge_kind=directive` kwarg marks the shape). Canonical text, embedded verbatim (single source `daemon/graph.py:ATTESTATION_DIRECTIVE_NUDGE_TEXT`):

```
[Attestation Gate — Directive] The gate has denied completion more than once and you have taken no new action since the last denial. Choose exactly one now: (1) if the mission is truly complete, call attest_completion and deliver your final report; (2) if work remains, dispatch or finish it now (re-assign the pending work to a child or do it yourself); (3) if you are blocked or uncertain, ask the user for a decision. Continuing to hold without action will end this mission as COMPLETED-UNVERIFIED (gate escalated) once the judge delivers a verdict.
```

**Scanner row fidelity (incident 7d4a3bd9 fix, 2026-09-26 — Fix 5a)**

`final_word_count` / `length_trigger` on `leader_completion_gate` rows are now REAL measurements of the final AIMessage on EVERY evaluated path (including DENIED decisions and non-delegated allows). Episode A of leader 7d4a3bd9 (2026-09-25 17:39:15Z) logged `final_word_count=0 length_trigger=False messages_scanned=3` against a real 2313-char final AIMessage: root cause was neither the scan window nor marker scoping — the scan block is routing-gated and the row printed dataclass defaults (the episode was a non-delegated allow, `attestation_required=False`). Routing is unchanged by this fix: the R3/R4 no-delegation fold still fires before any suspicion routing, so a non-delegated turn still ALLOWS — only the log values are now honest.

**Fail-open posture (ruling 4 — Pattern C)**

Invalid env values (typo'd mode, non-integer window/bound) **fail OPEN to `enforce` (or `3`)** with a one-shot WARN log line. The daemon **never refuses to start** on a typo'd attestation env — this mirrors the WC-wake resolver's behavior (the explicit Pattern C precedent at `daemon/services/instance_messaging.py:114-191`). Note: invalid/typo'd mode now falls back to `enforce` (the canonical default — operator override 2026-09-06), which is an accepted consequence of using the default constant as the fail-open fallback.

**Stuck-leader postmortem checklist**

1. Check the boot log line — confirm `mode` is what you expected (operator error class).
2. Grep `event=leader_completion_gate decision=denied` — every deny prints the full R2 input fields (`attestation_required`, `pending_children`, `queued_or_expected_wakeups`, `live_descendants`, `attestation_present`) so you can see which predicate fired. If `attestation_required=False` appears in the log alongside `decision=denied`, that's a contradiction class — file a bug against the conditional-attestation scanner (the deny path requires `attestation_required=True` per `daemon/services/attestation_scanner.py::scan_delegation_after_last_user`).
3. Grep `event=leader_completion_gate decision=terminal_after_bound` — these are the escalation-path log entries (the bound was reached).
4. Check the instance row for `completion_gate_escalated=true` — find the escalation boundary.
5. If the gate is firing on legitimate completions (false positive), flip to `dry` or `off` for instant revert + restart.

**Inline-LLM completion judge (Phase 6 fastfollow 2026-09-07; fused + sole site since the Stage-2 flip 2026-09-16; legacy window-judge deleted by the Stage-3 retirement 2026-09-17)**

The fused judge is invoked by the graph node's fused block whenever the activation predicate fires (delegated mission, not attested, and one of: quiet tree / marker-length signal / child-report-check suspicion). It consumes the pre-capped, id-redacted U+A+B+C evidence bundle and answers: "is this a REAL completion — and did it actually FULFILL the user's request?" — outcomes, evidence, follow-ups (not a short summary, not mid-work status text). SOURCE U carries the user's original request for the mission (section U, incident 4dfded83 2026-09-18: the judge was intent-blind without it and nudged a leader whose informal answer had genuinely answered the ask); the prompt's intent-fulfillment instruction makes a message that genuinely answers/fulfills the request a completion report regardless of formality, and a formal-looking report that ignores the ask NOT complete. A `complete` verdict can rescue an otherwise-deny row (allow END without the toolcall, no counter increment). Every other verdict (not_complete / error / timeout / unparsable×2 after the one retry) maps conservatively per the band matrix above (deny band: deny+nudge bound-enforced; marker/A bands: pending → allow+hint, else conservative).

**Bundle-shape update (dual-autopsy B1 stale-A fix, 2026-09-20)** — SOURCE A advisories previously never cleared: superseded child reports accumulated and held `a_suspicion` into final quiet-tree evals (incident acbf5627: 4/4 final advisories stale; incident fba90db8: "still pending on my ledger" for work later APPROVED). Three evidence-quality changes, band structure unchanged (`a_suspicion` stays a band; deny-band semantics unchanged; judge-call budget unchanged): (1) **newest-A-only** — the A-scan keeps ONLY each child's LATEST `internal_report:` message; superseded reports from the same child drop wholesale (an earlier report's contradiction that the newest report does not repeat does not resurface — delivery supersedes promise); (2) **cross-resolution vs C** — advisories from a child whose tree-row status is `completed` are suppressed UNLESS that child's newest report still carries a genuine (non-operator-scoped) hit — a completed child that lied at the end still surfaces; terminated/error/failed children and children absent from the rows keep their advisories; (3) **operator-action scoping** — a catalog hit whose sentence also names rebuild/restart/redeploy ("pending: rebuild+restart activation") is the OPERATOR's action, not undelivered child work: excluded from `contradiction_flag` and disqualified from the lie exception (the advisory stays visible, demoted). The judge prompt's A/C-subordination line is softened to match (advisories from children that later delivered, or whose pending item is an operator action, are not evidence of undelivered work) while keeping subordination verbatim for GENUINE unresolved advisories — the false-rescue guard (child-lie class) does not regress. Section B's per-message clip is raised 1500 → 2500 chars (incident acbf5627's final report was clipped at 1500/2372, losing the evidence tail + activation note; the B-section external cap ≤6,000 and the ≤14,000 total are unchanged).

The judge is a pure inline chat completion — no instance spawn, no message persistence. Model resolution honors `OPENAI_MODEL_KEYWORDS` (= `config.llm.model_keywords`) with fallback to `OPENAI_MODEL` (= `config.llm.model`). This mirrors the existing `daemon/services/keyword_extraction.py` model-resolution semantics — set `OPENAI_MODEL_KEYWORDS=quick` (or similar) to pin the judge to a fast model; leave unset to inherit the main `OPENAI_MODEL`.

Kill-switch via `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED` (Pattern C restart-read resolver; default ON; `=0` / `=false` / `=no` / `=off` disables). When OFF, the judge is never invoked and the gate's `deny+nudge` fall-through runs as before — but OFF is NOT pre-feature byte-identical: the `attest_completion` mermaid block at `daemon/graph.py:2862-2864` (the `ReportJudge` decision node + its two edges) is rendered unconditionally in the leader prompt, so the prompt text itself is a Phase 6 fastfollow change. OFF toggles ATTESTATION BEHAVIOR only (judge call suppressed → existing deny+nudge path); the prompt-text change is permanent. The boot log surfaces the resolved state plus the resolved quick model plus the resolved wall-clock cap:

```
Leader completion attestation resolved: mode=enforce window=3 deny_bound=3 attestation_enabled=true llm_judge_enabled=true llm_judge_model=quick llm_judge_timeout_s=180.0 N_le_min_recent_window=PASS (env ENSEMBLE_LEADER_ATTESTATION_MODE=<unset>, ENSEMBLE_LEADER_ATTESTATION_WINDOW=<unset>, ENSEMBLE_LEADER_ATTESTATION_DENY_BOUND=<unset>, ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED=<unset>, ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S=<unset>). Restart required to flip. See docs/setup.md (ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED).
```

When the kill-switch is OFF, the line carries `llm_judge_model=<disabled>` so operators see at a glance that no judge call will fire. Bounds: `JUDGE_TIMEOUT_S=180.0s` wall-clock cap per attempt (default raised 25.0s → 180.0s by the 2026-09-26 7d4a3bd9 amendment — two 25s double-timeouts consumed deny slots 2+3 and drove a COMPLETED-UNVERIFIED escalation; the user accepts the worst-case trade for verdict reliability) (env-tunable via `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S`; minimum clamp 5.0s — values below clamp with a one-shot WARN; restart-read Pattern C resolver at `daemon/services/attestation_judge_timeout_resolver.py`); input side: the fused bundle arrives pre-capped (≤14,000 chars total — A ≤3,000 + B ≤6,000 + C ≤3,000 + U ≤2,000 additive, id-redacted; U = the user's original request, omitted with `user_message_included=false` on the eval row when no real user message anchors the mission) from `assemble_fused_bundle` — the judge does not re-truncate; output side: `FUSED_JUDGE_MAX_OUTPUT_CHARS=2048` chars of LLM response (a defensive ceiling against a runaway LLM that returns prose instead of JSON; sized for the fused verdict payload 5×120 evidence + 240 advisory + 240 rationale — the retired legacy judge's 400-char cap was the F-A incident class).

**Judge wall-clock cap (`ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S`)** — Pattern C restart-read resolver (sibling to the kill-switch resolver); default `180.0` seconds (2026-09-26 7d4a3bd9 amendment; was `25.0`); minimum clamp `5.0` seconds. Resolver home: `daemon/services/attestation_judge_timeout_resolver.py`. Failure / clamp policy (fail-OPEN):

| Env value | Resolved | One-shot WARN? |
|-----------|----------|----------------|
| unset / blank | `25.0` (default) | no |
| `=30` / `=15.5` | parsed float | no |
| `=abc` / `=1.5x` | `25.0` (default) | yes (invalid → default) |
| `=0` / `=-1` | `25.0` (default) | yes (invalid → default) |
| `=3` / `=4.9` | `5.0` (clamp) | yes (below clamp → floor) |

Rationale (operator tuning decision 2026-09-07 grounded in the tester live-LLM probe; evidence commits `b42f7237..2a43904c` on branch `feature/leader-completion-attestation`, captured at `.agents/tester/RESULTS/2026-09-07-lca-judge-live-probe*`): the hardcoded `JUDGE_TIMEOUT_S=10.0` was timeslicing genuine-report quick-model calls. Real quick-model latencies in the probe showed successes 2.6s–13.6s with **4/8 calls >15s** — the 10.0s cap was firing on a substantial fraction of genuine-report calls and silently flipping the gate to the conservative deny+nudge path, defeating the feature's purpose. The default was bumped from 10.0s → 25.0s and the env was exposed so operators can re-tune without a code change. **2026-09-26 (incident 7d4a3bd9): default raised 25.0s → 180.0s** — Episode B's two judge double-timeouts at 25s consumed deny slots 2+3 and the bound escalation ended the mission COMPLETED-UNVERIFIED; a delivered verdict (either of complete/not_complete) is what keeps the bound machinery honest, and a never-spoke judge now continues the deny+nudge cycle by user ruling (no terminal from timeouts alone).

Trade-off: a longer worst-case turn-end wait on the rare deny path (the judge runs only on the WOULD-BE-DENY branch, so the cost is bounded by the per-instance `deny_bound=3` escalation + the existing 3-deny escalation guard) vs fewer false nudges of genuine reports. **Worst case at the 180.0s default: retry-once means up to 2 × 180s = 360s (6 minutes) on a single turn-end's judge verdict — the user explicitly ACCEPTS this trade for verdict reliability.** The 25.0s default kept the wait bounded while letting the quick-model tail latency ride; it proved too tight for the slow tail. Operators with a known-fast quick-model can tighten via `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S=15` (or similar) for a faster worst-case bound; operators with a slow quick-model can loosen to `=40`. Restart required to flip (Pattern C — no live flip).

Observability fields (logged on every fused judge invocation):

| Field | Source | Purpose |
|-------|--------|---------|
| `event=leader_completion_gate_fused_judge` | graph node fused block | One-shot structured log line; emitted after the judge call (the ONLY judge event family post-Stage-3) |
| `verdict` | FusedJudgeResult | `complete` / `not_complete` / `error` / `timeout` / `unparsable` |
| `band` | activation snapshot | `deny` / `marker` / `a_suspicion` |
| `llm_judge_model` | FusedJudgeResult | The resolved quick model (or main model fallback) |
| `llm_judge_latency_ms` | FusedJudgeResult | Cumulative wall-clock latency across attempts |
| `llm_judge_attempt` | FusedJudgeResult | `1` or `2` (the retry-after-unparsable) |
| `llm_judge_first_unparsable_excerpt` | FusedJudgeResult | Truncated + redacted attempt-1 raw response on a 2-attempt row (98b59dd7 forensic contract) |
| `llm_judge_reason` | FusedJudgeResult | The LLM's one-sentence rationale (empty on error paths) |
| `llm_judge_error_class` | FusedJudgeResult | Exception class name on the error path; `<none>` otherwise |
| `judge_invoked` | derived | True iff a real invocation record exists |

These fields are diagnostic extras (NOT in the canonical 17-field `leader_completion_gate` tuple).

The judge is conservative (DP-5 REJECTED — no fail-safe allow anywhere): every failure path (timeout, exception, unparsable JSON ×2) returns `is_complete=False` so the band-mapped conservative outcome runs (deny band: deny+nudge bound-enforced). The 3-deny escalation bound caps worst-case misfires. A judge-wrapper-layer bug emits a separate `event=leader_completion_gate_fused_judge_error` log line with `error_class` and `decision=fail_safe_conservative`.

**Runbook note (2026-09-11, incident b08f40fe): `live_descendants` now means WORK-BEARING descendants.** The third R2 input no longer counts every non-terminal descendant. Two-set semantics: `RUNNING`/`WAITING`/`WAITING_CHILDREN`/`PAUSED` are unconditionally live; dormant `IDLE`/`QUEUED` descendants count live ONLY with work en route — a not-yet-processed `message_queue` row (`PENDING`/`READY`/`PROCESSING`/`RETRYING`) OR a not-yet-settled `job_queue_items` row (`admission_state` QUEUED | ACTIVE) targeting that descendant. IDLE orphans (spawned but never dispatched — no message row, no unsettled job) are EXCLUDED, so the deny/nudge ladder sees the true zero instead of branch (5) holding the gate open for a report that will never arrive. Operator reading of the canonical log row is unchanged: `live_descendants=0` now truthfully means "nothing in my subtree will ever wake me"; a `live_descendants=-1` row is the DB-error fail-open sentinel (gate allowed, signal unknown). Restart required after upgrading (code change, no env). Incident reference: leader `b08f40fe` completed 2026-09-11 14:50:10 UTC via `allowed_legitimate_pending_wakeup` on four never-dispatched IDLE-orphan grandchildren; regression-pinned by `tests/integration/test_attestation_idle_orphan_incident.py`.

## Database

### Overview

agents-ensemble uses two SQLite databases:

| Database | Purpose | Default Path |
|----------|---------|--------------|
| `instances.db` | Application data | `./data/` or `./data_dev/` |
| `checkpoints.db` | LangGraph state snapshots | `./data/` or `./data_dev/` |

### Database Files

#### instances.db

Stores persistent application state:

- Agent instances
- Job queue entries
- Projects
- Message history
- Configuration snapshots

#### checkpoints.db

Stores LangGraph state for crash recovery:

- Conversation states
- Execution checkpoints
- Transaction logs

### Database Creation

Databases are automatically created on first startup:

```bash
./dev.sh  # Creates ./data_dev/ directory and databases
```

Generated structure:

```
data_dev/
├── instances.db
└── checkpoints.db
```

### Migrations

Database schemas are automatically migrated on startup using Alembic-style migrations.

Migration files location: `daemon/migrations/versions/`

> **Note:** Migrations run automatically. Do not modify database files manually.

### Development vs Production Paths

| Mode | Database Path |
|------|---------------|
| Development (`dev.sh`) | `./data_dev/` |
| Production (`ensemble-prod`) | `./data/` |

---

## Frontend Setup

### Development Mode

#### Step 1: Install Dependencies

```bash
cd frontend
npm install
```

#### Step 2: Start Dev Server

```bash
npm start
```

This starts:

- Angular dev server on **port 4199**
- API proxy to backend at **localhost:8079**

#### Step 3: Access the UI

Open browser: `http://localhost:4199`

### Production Build

```bash
make build
```

This command:

1. Runs `npm install` in frontend directory
2. Runs Angular build (`ng build`)
3. Outputs to `frontend/dist/frontend/browser/`

### Proxy Configuration

During development, Angular proxies API calls:

```json
{
  "/api/*": {
    "target": "http://localhost:8079",
    "secure": false
  },
  "/ws/*": {
    "target": "ws://localhost:8079",
    "secure": false
  }
}
```

This allows the frontend dev server to communicate with the backend without CORS issues.

---

## First Run

### Verify Backend Health

```bash
curl http://localhost:8079/api/health
```

Expected response:

```json
{
  "status": "ok",
  "version": "0.3.6"
}
```

### Access the Web UI

| Environment | URL |
|-------------|-----|
| Development | `http://localhost:8079` |
| Production | `http://localhost:8088` |

### API Documentation

Interactive API docs (Swagger UI):

```
http://localhost:8079/docs
```

### Test LLM Connection

Create an agent instance and send a test message:

```bash
# Create instance
curl -X POST http://localhost:8079/api/instances \
  -H "Content-Type: application/json" \
  -d '{"project": "test", "agent_id": "leader"}'

# Send message
curl -X POST http://localhost:8079/api/instances/<instance-id>/messages \
  -H "Content-Type: application/json" \
  -d '{"content": "Hello, say hello back"}'
```

---

## Troubleshooting

### Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| `OPENAI_API_KEY not set` | Missing `.env` file | Create `.env` with `OPENAI_API_KEY=your-key` |
| `Address already in use` | Port occupied | Kill existing process: `lsof -ti:8079 \| xargs kill` |
| `Module not found` | Dependencies missing | Run `uv sync` or `pip install -e .` |
| `Frontend not built` | Missing build | Run `make build` |
| Database locked | Multiple instances | Only run one instance per database |
| LightRAG connection failed | Service unavailable | RAG tools disabled gracefully (non-critical) |

### Port Conflicts

Find what's using a port:

```bash
# macOS
lsof -i :8079

# Linux
ss -tlnp | grep 8079
```

Kill the process:

```bash
# By PID
kill <PID>

# Or by port
lsof -ti:8079 | xargs kill
```

### Virtual Environment Issues

```bash
# Recreate venv
rm -rf .venv
python -m venv .venv
source .venv/bin/activate
uv sync
```

### Clean Rebuild

```bash
# Remove build artifacts
make clean

# Remove databases
rm -rf data/ data_dev/

# Fresh install
make sync
./dev.sh
```

### Checking Logs

The server outputs logs to stdout. In production, redirect to a file:

```bash
./ensemble-prod > app.log 2>&1
```

### Verify Configuration

Check loaded environment variables:

```bash
source .venv/bin/activate
python -c "from daemon.config import Config; c = Config(); print(c.model_dump())"
```

---

## Next Steps

Once you have agents-ensemble running, explore these topics:

- **[Agents Architecture](architecture.md)** — Understanding how agents work
- **[API Reference](/docs)** — Interactive API documentation (Swagger UI)
- **[Creating Custom Agents](agents.md)** — Define your own agents
- **[Production Install](#method-2-production-install-make-install)** — Full production installation
- **[Configuration](#configuration)** — Environment variables and config.yaml reference
- **[Troubleshooting](#troubleshooting)** — Common issues and solutions

---

## Quick Reference

### Essential Commands

```bash
# Development
./dev.sh                           # Start dev server
uv sync                            # Install dependencies

# Production
make install                       # Full installation
./ensemble-prod                    # Run production

# Frontend
cd frontend && npm start           # Dev server
make build                         # Production build

# Utilities
make stop                          # Stop prod server
make clean                         # Clean build artifacts
```

### File Locations

| File/Directory | Purpose |
|---------------|---------|
| `config.yaml` | Application configuration |
| `.env` | Environment variables (API keys) |
| `agents/` | Agent definitions |
| `daemon/` | Backend source code |
| `frontend/` | Frontend source code |
| `data/` | Production database |
| `data_dev/` | Development database |

### Ports

| Port | Purpose |
|------|---------|
| 8079 | Development server |
| 8088 | Production server |
| 4199 | Frontend dev server |

---

*Last updated: Version 0.3.6*
ver |
| 4199 | Frontend dev server |

---

*Last updated: Version 0.3.6*

## Mid-work marker scan (LCA Phase 6.5 follow-up, 2026-09-11)

> **Stage-3 retirement note (2026-09-17).** The scanner itself (16-pattern catalog + length threshold) SURVIVES as an activation-signal producer for the unified resolver's `b_fires` term. The route plumbing documented below is HISTORICAL: the `(a)/(b)/(c)/(d)` routing, the `marker_path`/`marker_judge_*`/`trigger_source`/`trigger_suppressed_by` log fields, and the `*_marker_judge*` event family were DELETED (R5/R6/R7/R8 — decisions.md D-RES4). The fused judge + the band matrix above own the routing now.

> **2026-09-23 retirement note (incident b2f4dae9).** The Completion Check Note hint surface is RETIRED end-to-end along the (b)/(d)-with-pending route (the route STILL EXISTS on the resolver row as a logged decision — `resolver_outcome=allow_hint` + the `[AttestationGate]` log line's `would_be_route=allow_hint` — but emits NO message). The `Completion Check Note` constant, `_make_completion_check_note_message` factory, `_fused_hint_citation` helper, `_COMPLETION_CHECK_NOTE_TITLE` literal, the `marker_hint_message` `GateDecision` field, and the `completion_check_note` row in `_stable_id_for`'s canonical id-format table are ALL removed. The deny path + the HOLD + attest-first reminders (the Final Report Reminder family is DIFFERENT and STAYS untouched) are byte-identical. See decisions.md D-entry 2026-09-23 + the b2f4dae9 regression pin in `tests/unit/test_attestation_lca_note_removed.py`.

The completion gate's ALLOW branches (`Decision.ALLOWED` not-attested + `Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP`) trigger a cheap mid-work marker scan before the END. Markers fire → the existing inline-LLM judge runs (verdict) → verdict + R2 inputs drive the (a)/(b)/(c)/(d) routing. This is the incident b08f40fe-class kill: a leader whose final AIMessage reads mid-work phrasing while the R2 inputs are clean would otherwise complete silently.

### Marker catalog (16 patterns, 12-18 balance)

`ending turn`, `ending my turn`, `awaiting`, `then i aggregate`, `then i compile`, `will write`, `will aggregate`, `not a completion report`, `interim`, `in progress`, `not yet complete`, `still pending`, `to be continued`, `will report back`, `standby`, `stand by`.

Case-insensitive substring match against the AIMessage content; catalog is curated for high recall on the incident family. Excluded: `done`, `completed`, `finished`, `shipped`, `summary`, `results` alone (would false-positive on legitimate completions). The judge is the verdict — any false-positive marker hits are filtered by the LLM verdict.

### Routing

| Path | Conditions | Behavior |
|------|------------|----------|
| (a) | markers + judge-no + nothing pending | CONVERT TO DENY (existing nudge machinery: counter+1, nudge, route to agent) |
| (b) | markers + judge-no + real pending work | ALLOW + checkpoint-durable hint (NO counter, NO deny, NO re-route — turn still ends) |
| (c) | markers + judge-yes | ALLOW normally (log marker_hit + verdict; no action) |
| (d) | markers + judge-error/timeout/unparsable | (a)-behavior if nothing pending, (b)-behavior otherwise |

### Kill-switch coupling

The marker path respects the existing judge kill-switch `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED` (default ON; `=0` / `=false` / `=no` / `=off` disables). With the judge disabled, marker hits are logged but NO judge call fires; the gate falls through to plain ALLOW. Rationale: marker-only signal is too weak to deny — the LLM verdict disambiguates ambiguous marker hits; without the verdict, plain allow + log is the safer default.

**Stage-3 update:** with the judge disabled, the fused block emits `event=leader_completion_gate_fused_judge_disabled` with `verdict=<skipped>` plus the band and the raw B-signal fields (`marker_hit` / `length_trigger`). This row is the operator-observable signal that the gate did not call the judge (kill-switch open), did not inject a hint, and did not deny. (The historical `*_marker_judge_disabled` row name retired with R8.)

### Dry-mode marker logging

**Stage-3 update:** the marker scan still runs in `dry` mode (pure LOG-ONLY: `marker_hit` / `marker_terms` on the canonical row) — but ONLY on DELEGATED missions (the R4/D10 mirror skips suspicion scans on non-delegated turn-ends). No judge ever fires in dry (the fused node requires enforce mode), no hint, no deny, no counter. The retired `marker_path` sentinel no longer appears.

### Completion Check Note (path (b)) — RETIRED 2026-09-23

> **RETIRED 2026-09-23 (incident b2f4dae9).** The (b)/(d)-with-pending
> route STILL EXISTS as a logged decision — the resolver_eval row
> carries `resolver_outcome=allow_hint` + the `[AttestationGate]`
> log line carries `would_be_route=allow_hint` — but NO message is
> injected. The Completion Check Note constant (`COMPLETION_CHECK_NOTE_TEXT`),
> the `_make_completion_check_note_message` factory, the
> `_fused_hint_citation` D4 evidence-citation helper, the
> `_COMPLETION_CHECK_NOTE_TITLE` literal, the `marker_hint_message`
> `GateDecision` field, and the `completion_check_note` row in
> `_stable_id_for`'s canonical id-format table are ALL removed.

Pre-retirement (historical, 2026-09-11 → 2026-09-23): injected
alongside END on path (b). Canonical home `daemon/graph.py::COMPLETION_CHECK_NOTE_TEXT`.
The note was a `HumanMessage` with `[SYSTEM CONTEXT: Completion Check Note]`
header (reuses the existing prefix convention so `is_real_user_message` in
`attestation_scanner.py` recognized it as not-a-real-user-message). The
hint carried a **stable id per instance** minted via
`_stable_id_for("completion_check_note", instance_id=...)` — F1 Shape A
(2026-09-12).

Post-retirement (current, 2026-09-23 →): the (b)/(d)-with-pending route
resolves to ALLOW log-only. The route label survives on the resolver row
+ the `[AttestationGate]` log line so operators can distinguish
(b)/(d)-with-pending from plain (c) allow. NO message is injected, NO
context_kind is minted, NO stable-id is required. The deny path
(preventing) + the watchdog (acting at 1h) are the protection surfaces;
the note never prevented anything.

### Log schema (additive)

**Stage-3 update:** the canonical row carries the SURVIVING signal fields `marker_hit`, `marker_terms` (capped list, comma-joined; `<none>` when empty), `length_trigger`, `final_word_count`, `busy_descendants`. The route/judge-stamp fields (`marker_path`, `marker_judge_verdict`, `marker_judge_latency_ms`, `marker_judge_error_class`) and the `*_marker_judge*` event family are RETIRED — the fused judge family (`leader_completion_gate_fused_judge` / `_disabled` / `_error`) is the only judge event surface.

### Length trigger (2026-09-12, user request)

The completion gate's ALLOW branches also fire a word-count trigger on the LAST AIMessage. Mid-work ACKs are often SHORT with no marker words ("Understood, continuing." / "OK, waiting on the tester.") — brevity is an INDEPENDENT signal that the marker-only catalog misses. Real completion reports are normally detailed (hundreds of words); very-short prose on an ALLOW path is suspicious. The length trigger is the SECOND half of the two-stage disambiguator — orthogonal to the marker scan; the two halves compose via `OR` (`marker_hit OR length_trigger` → judge fires).

**Threshold:** 150 words (module-level constant `SHORT_REPORT_WORD_THRESHOLD` in `daemon/services/attestation_marker_scanner.py`). Below the threshold ⇒ trigger fires. The threshold is **NOT env-tunable by design** (one knob fewer; revisit at soak). Operators wanting to disable this trigger set `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED=0` (the existing judge kill-switch) and accept the marker-only signal as the trigger.

**Word count:** whitespace-split on the flattened LAST AIMessage content (mirrors `_flatten_ai_content` for list-of-blocks content). Pure function; no I/O.

**Hook scope:** identical to the marker scan — `Decision.ALLOWED` (not-attested) + `Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP` + `Decision.DRY_LOG`, with `attestation_present=False`. Attested allows still skip entirely. Cost control: NEITHER trigger fires ⇒ NO judge call.

**Interplay with markers:** markers catch phrasing ("ending turn", "awaiting"); length catches brevity. **Stage-3 update:** the composed signal is the predicate's `b_fires` term (`(marker_hit ∨ length_trigger) ∧ busy_descendants == 0`); the `trigger_source` log field is RETIRED — read the `marker_hit` / `length_trigger` values plus the resolver row's `terms_fired` for the class distribution.

**Dry-mode:** length trigger is logged side-effect-free exactly like markers — no judge call, no hint, no deny, no counter (the dry-mode `allow unconditionally` posture is preserved end-to-end).

**Kill-switch:** the length trigger respects the existing judge kill-switch `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED`. With the judge disabled, length triggers are logged but NO judge call fires; the gate falls through to plain ALLOW. Rationale: length-only signal is too weak to deny — without the LLM verdict, plain allow + log is the safer default. **Stage-3 update:** the disabled-signal row is `event=leader_completion_gate_fused_judge_disabled` (with `marker_hit=`/`length_trigger=` context fields).

**Log schema (additive, three new fields):**

* `length_trigger` (bool) — True when the LAST AIMessage word count is < `SHORT_REPORT_WORD_THRESHOLD`. False on degenerate empty / no-AI tail.
* `final_word_count` (int, ≥0) — word count of the flattened LAST AIMessage content. `0` on degenerate empty / no-AI tail.
**Stage-3 update:** `trigger_source` retired (R6); `length_trigger` + `final_word_count` SURVIVE. Canonical format-string placeholders: 27 (drift pin `test_length_log_placeholder_count_is_27`).

### LCA busy trigger suppression (2026-09-12, user request — false-positive hint fix)

A leader awaiting a healthy child (RUNNING/WAITING/WAITING_CHILDREN) writing a short mid-work ACK triggers BOTH the marker substring scan AND the length trigger on the ALLOW path — pre-fix this would call the judge, return `is_complete_report=false`, and inject a checkpoint-durable Completion Check Note on essentially every awaiting turn-end. The hint on healthy waits is noise. The LCA busy trigger suppression disarms the WHOLE trigger when at least one descendant is in the unconditional-busy subset `{RUNNING, WAITING, WAITING_CHILDREN}` — NO judge call, NO route-(b) hint, plain allow. The marker/length signal STAYS RECORDED on the canonical log row for forensics.

**2026-09-23 retirement note (incident b2f4dae9):** even WITHOUT busy descendants the (b)/(d)-with-pending route resolves to ALLOW log-only — NO hint injected (the hint surface is RETIRED end-to-end). The LCA busy suppression's behavioral shape (NO judge call, NO hint) is preserved end-to-end; the post-fix change is that the (b) route is log-only on BOTH branches (busy-muted OR not-busy). The marker/length signal + the `[AttestationGate]` log line's `would_be_route=allow_hint` + the resolver_eval row's `resolver_outcome=allow_hint` are the surviving forensic surfaces.

**Stage-3 update (R5):** the busy-mute now lives INSIDE the unified predicate's `b_fires` term — `(marker_hit ∨ length_trigger) ∧ busy_descendants == 0`. A busy tree mutes the marker band (no judge, no hint, plain allow); Source-A suspicion is deliberately NOT busy-muted (approved Δ2). The `trigger_suppressed_by`/`trigger_source`/`marker_path` fields retired; the suppression is observable via `busy_descendants>0` on the canonical row plus zero fused-judge rows and zero hints.

**Busy subset:** `InstanceManager.count_busy_descendants(instance_id) -> int` counts descendants in `{RUNNING, WAITING, WAITING_CHILDREN}` ONLY. PAUSED is NOT busy (suspect, not healthy — the trigger stays armed so a stuck child is caught). Conditional-live dormant `IDLE`/`QUEUED` are NOT busy either (no execution — work is merely en route; the trigger stays armed so en-route-only work is caught). Terminal `COMPLETED`/`TERMINATED`/`ERROR`/`FAILED` are excluded. The busy subset is derived from the SAME BFS as `count_live_descendants` via the shared private helper `InstanceManager._count_descendants_busy_and_live` — single source of truth for the descendant scan.

**Stage-3 update:** the graph-node short-circuit retired with the legacy judge block — the predicate's `b_fires=False` simply means the marker band does not fire (no judge call, no hint, no counter write, plain allow).

**Suspect-pending protections UNCHANGED:** PAUSED descendants (maybe stuck — trigger stays armed), en-route-only work (IDLE + pending message OR IDLE + unsettled job — maybe lost). The deny path + the two-set live semantics are UNTOUCHED. Deny with RUNNING children is structurally impossible — `live_descendants > 0` blocks it.

**Dry-mode:** busy suppression is log-only, NO judge call, NO hint, plain allow. **Stage-3 update:** grep `event=leader_completion_gate decision=dry_log busy_descendants=N` (N>0) with `marker_hit=True` to see dry rows where the busy-mute disarmed the marker band.

**Kill-switch:** busy suppression is independent of `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED`. The suppression disarms the trigger BEFORE the kill-switch check, so the kill-switch continues to apply on non-suppressed paths exactly as before. No new `ENSEMBLE_*` flags are introduced (fix/flag policy 7d5285aa — behavior fixes ship always-on).

**Log schema:** `busy_descendants` (int, ≥0) SURVIVES as a predicate input + forensic count (always stamped). `trigger_suppressed_by` retired (R5). Canonical placeholders: 27.

**Forensics recipe:**

```bash
# Stage-3: how often does the busy-mute disarm the marker band? (busy>0 + a marker/length signal present)
grep "event=leader_completion_gate" data/logs/ensemble.log | grep -E "marker_hit=True|length_trigger=True" | grep -Ev "busy_descendants=0" | wc -l

# Per-decision-class breakdown of those busy-muted rows:
grep "event=leader_completion_gate" data/logs/ensemble.log | grep -E "marker_hit=True|length_trigger=True" | grep -Ev "busy_descendants=0" \
  | awk '{for(i=1;i<=NF;i++) if ($i ~ /^decision=/) {print $i}}' | sort | uniq -c
```

### References

- `.agents/shared/planning/leader-completion-attestation/decisions.md` — D-ENTRY 2026-09-11 (marker scan); D-ENTRY 2026-09-12 (length trigger); D-ENTRY 2026-09-12 (LCA busy trigger suppression); F1 amendment 2026-09-12 (Shape A landed); D-ENTRY 2026-09-12 (import-typo fix); D-ENTRY 2026-09-23 (Completion Check Note RETIRED, incident b2f4dae9)
- `.agents/shared/planning/leader-completion-attestation/requirements.md` — AC-M1..AC-M16 (marker scan acceptance); AC-L1..AC-L19 (length trigger acceptance); AC-BUSY-1..AC-BUSY-15 (LCA busy trigger suppression acceptance); SUPERSESSION ENTRY 2026-09-23 (Completion Check Note (b)/(d)-with-pending → LOG-ONLY)
- `daemon/services/attestation_marker_scanner.py` — pure-function scanners (marker substring + length word-count) + `SHORT_REPORT_WORD_THRESHOLD` constant
- `daemon/services/attestation_gate.py` — gate integration + additive log fields (28 → 31 → 33 placeholders); busy trigger suppression at `evaluate():993-1037`. The `marker_hint_message` `GateDecision` field was RETIRED 2026-09-23 (along with the entire hint surface).
- `daemon/graph.py` — fused block + the historical marker-path wiring retired in Stage 3 (the OR-composition and the busy-mute live in the predicate's `b_fires` term). The `COMPLETION_CHECK_NOTE_TEXT` constant + `_make_completion_check_note_message` factory + `_fused_hint_citation` helper + the marker-hint emit site were RETIRED 2026-09-23 (along with the entire (b)/(d)-with-pending hint surface).
- `daemon/services/context_messages.py` — `_stable_id_for` for the surviving kinds (`project`, `shared_meta_kv`, `attestation_nudge`, `attestation_final_report_reminder`); the `completion_check_note` row was RETIRED 2026-09-23.
- `daemon/manager.py` — `count_busy_descendants` (new LCA input) + `_count_descendants_busy_and_live` shared BFS helper
- `tests/unit/test_attestation_marker_scanner.py` (54 tests; +16 length-trigger tests) + `tests/unit/test_attestation_marker_wiring.py` (40 tests; +11 LCA busy trigger suppression tests — the 2026-09-23 retirement re-anchored 3 tests from "hint content byte-pins" to "messages not in result + would_be_route=allow_hint"; the W2 Shape A stable-id tests were retired in `tests/unit/test_attestation_marker_supersede_lca.py`).
- `tests/unit/test_attestation_lca_note_removed.py` — the canonical 2026-09-23 retirement witness (6 tests: b2f4dae9 regression pin, suspect-pending PAUSED + en-route-only shapes, two negative census pins on the retired SYMBOLS + the needle title, and the `_stable_id_for` table rejection pin).


---

## LCA judge unparsable retry + forensic logging (2026-09-16, incident 98b59dd7)

The LCA inline-LLM completion-report judge (`daemon/services/attestation_report_judge.py`) now retries ONCE on `verdict="unparsable"`. The retry fires ONLY when the model responded with a body AND the body did not parse (the strict `_parse_fused_judge_response` returned `None`); timeout / HTTP / LLM errors do NOT trigger a retry (existing fail-safe semantics preserved).

### Retry semantics

| Outcome of attempt 1 | Retry fires? | `attempt` | `verdict` | `is_complete_report` |
|---|---|---|---|---|
| Parse success ("yes") | No | 1 | `yes` | `True` |
| Parse success ("no") | No | 1 | `no` | `False` |
| Unparseable body | **YES** (same input, fresh call) | 1 or 2 | retry's outcome | retry's outcome |
| `asyncio.TimeoutError` | **YES** (same input, fresh call — incident bc145c7e R1, 2026-09-19) | 2 | retry's outcome | retry's outcome |
| Any other exception | No | 1 | `error` | `False` |

### Worst-case latency bound

The retry uses its OWN per-attempt timeout window (`timeout_s`). Total worst-case wall-clock = `2 × timeout_s`. With the default 180.0s cap (`ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S`, raised from 25.0s by the 2026-09-26 7d4a3bd9 amendment), worst-case = **360.0s** (accepted trade for verdict reliability). The HA facade's `wall_clock_cap_s` and `asyncio.wait_for` bounds still apply per-attempt (the retry does NOT stack timeouts across attempts). Each retry attempt is a fresh LLM call — same prompt, same model, same config; no prompt mutation, no model swap, no backoff delay.

The `JUDGE_TIMEOUT_S` env knob is `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S` (Pattern C restart-read resolver at `daemon/services/attestation_judge_timeout_resolver.py`); default **180.0s** (2026-09-26 amendment; was 25.0s); minimum clamp 5.0s with a one-shot WARN. Operators with a known-fast quick-model can tighten via `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S=15` for a faster worst-case bound (with retry: 2 × 15 = 30s); operators with a slow quick-model can loosen to `=40` (with retry: 2 × 40 = 80s). Restart required to flip.

### New log-row fields (Stage-3: on `event=leader_completion_gate_fused_judge` — the two legacy rows retired)

Both log rows gain two additive placeholders:

| Field | Description |
|---|---|
| `llm_judge_attempt=%s` | `FusedJudgeResult.attempt` value (`1` for first-attempt; `2` for retry-after-unparsable) |
| `llm_judge_first_unparsable_excerpt=%s` | `FusedJudgeResult.first_unparsable_excerpt` value (the truncated + redacted raw response of attempt 1, capped at 400 chars, whitespace-normalized, secrets redacted). `<none>` when `attempt == 1` |

The `llm_judge_first_unparsable_excerpt` field closes the incident 98b59dd7 forensic gap — operators can now read the unparsable row's raw LLM output directly from the canonical log row without needing to re-run the probe.

### Reason field no longer empty on unparsable

The `rationale` field on `FusedJudgeResult` is never empty on `verdict="unparsable"` rows (carried over from the retired legacy judge's `reason` contract):

| Path | `reason` shape |
|---|---|
| Retry exhausted (both unparsable) | `"judge_response_unparsable on both attempts: <excerpt-head>"` (excerpt-head capped at 120 chars) |
| Retry-then-timeout (attempt 1 unparsable, attempt 2 `TimeoutError`) | `"judge_response_unparsable (attempt 1); timeout on attempt 2"` |
| Retry-then-error (attempt 1 unparsable, attempt 2 generic exception) | `"judge_response_unparsable (attempt 1); error on attempt 2"` |
| Retry-success (attempt 1 unparsable, attempt 2 parsed) | The retry's LLM rationale (unchanged from single-attempt success path) |
| Single-attempt success ("yes" / "no") | The LLM's one-sentence rationale (unchanged) |
| Single-attempt timeout / error | Empty string (unchanged — no response body exists) |

### Redaction contract

Secret-shaped substrings in `first_unparsable_excerpt` are redacted before logging:

* `bearer <token>` (Authorization header prefix + value)
* `api_key` / `api-key` / `apikey` in three shapes: query-style `api_key=value`, env-style `api_key=value`, JSON-style `"api-key": "value"`
* `token=value` (env / query) and `"token": "value"` (JSON)
* `secret=value` (env / query) and `"secret": "value"` (JSON)

Minimum token length is 8 chars (`{8,}`); prose words like "token economy" or "bearer of good news" are NOT redacted (false-positive guard). The redaction sentinel is `[REDACTED]`. Whitespace runs are collapsed to a single space; the excerpt is capped at `JUDGE_EXCERPT_MAX_CHARS=400` chars with a `[truncated]` tail marker (single source of truth, NOT env-tunable by design).

### Deny-path symmetry guard

The brief mandates: on `Decision.DENIED` with a long-form final AIMessage (>150 words), the nudge fires only after the retry is exhausted. This is AUTOMATIC via the retry semantics (no separate gate logic):

* (i) Long-form + unparsable on attempt 1 + unparsable on attempt 2 → `is_complete_report=False, attempt=2` → gate sees post-retry state → existing deny+nudge fires. Pinned by `test_deny_symmetry_long_form_unparsable_then_unparsable_nudge_after_retry`.
* (ii) Long-form + unparsable on attempt 1 + parse-success on attempt 2 → `is_complete_report=True, attempt=2` → gate flips to ALLOWED → NO nudge. Pinned by `test_deny_symmetry_long_form_unparsable_then_parse_success_no_nudge`.

The `>150 words` check uses the existing `SHORT_REPORT_WORD_THRESHOLD` constant from `daemon/services/attestation_marker_scanner.py` (no duplicate constant, no env-tunable knob — single source of truth).

### Kill-switch OFF = zero judge calls (including zero retries)

The kill-switch `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED=0` is checked at the graph layer in `daemon/graph.py:5483-5486` BEFORE the judge is called. When the kill-switch is OFF, the judge is never called → zero retries by construction. The existing pin `test_judge_not_called_when_env_kill_switch_off_real_resolver` (asserts `calls == []`) covers this; no new test was added because the architecture guarantees it.

### KB-trap: DENIED rows have default marker fields

On `Decision.DENIED` rows, the marker/length scanner NEVER runs (the scan is gated on `result.decision in (ALLOWED, ALLOWED_LEGITIMATE_PENDING_WAKEUP, DRY_LOG) AND not attestation_present` — see `daemon/services/attestation_gate.py:1100-1119`). The `marker_hit` / `length_trigger` / `final_word_count` fields on `GateDecision` therefore stay at their dataclass defaults (False / False / 0) on a DENIED row. Operators / future readers MUST NOT interpret those fields as measurements of the final AIMessage content on a DENIED row — they are noise on the deny path. Pinned by `TestDeniedRowsHaveDefaultMarkerFields` (4 tests) in `tests/unit/test_attestation_gate.py`.

### Operator quick-reference

* **Did the retry fire?** Grep `event=leader_completion_gate_fused_judge llm_judge_attempt=2`.
* **What did the first attempt return?** Check `llm_judge_first_unparsable_excerpt=<value>` — the truncated + redacted raw response of attempt 1 (capped at 400 chars, secrets replaced with `[REDACTED]`).
* **Was the retry exhausted?** `llm_judge_attempt=2` AND `verdict=unparsable` (or `timeout` / `error`).
* **Was the retry successful?** `llm_judge_attempt=2` AND `verdict=yes|no`.
* **Is this a false-positive retry-recovery?** `llm_judge_attempt=2` AND `verdict=yes` (incident 98b59dd7 class — the retry rescued a genuine 2328-char final report).

### References

- `.agents/shared/planning/leader-completion-attestation/decisions.md` — D-ENTRY 2026-09-16 (judge unparsable retry + forensic logging + deny-path symmetry guard)
- `.agents/shared/planning/leader-completion-attestation/requirements.md` — AC-JUDGE-RETRY-1..AC-JUDGE-RETRY-16 (incident 98b59dd7 acceptance)
- `daemon/services/attestation_report_judge.py` — Stage-3: `FusedJudgeResult` carries the retry contract (`attempt`, `first_unparsable_excerpt`); `_redact_secrets` / `_truncate_excerpt` / `_shape_unparsable_excerpt` helpers; `_AttemptOutcome` NamedTuple; the legacy `judge_completion_report_async` retry path retired (R7)
- `daemon/graph.py` — two log rows extended with `llm_judge_attempt=%s` + `llm_judge_first_unparsable_excerpt=%s`
- `tests/unit/test_attestation_report_judge.py` — 18 new tests + 2 updated tests + 1 new constants pin
- `tests/unit/test_attestation_gate.py` — new `TestDeniedRowsHaveDefaultMarkerFields` class with 4 tests

### Runbook note (2026-09-19, incident bc145c7e R1 — fused judge retry-once-on-timeout)

The LCA inline-LLM completion-report judge (`daemon/services/attestation_report_judge.py::judge_fused_bundle_async`) now retries ONCE on attempt-1 `asyncio.TimeoutError`, superseding the class-D (98b59dd7) decision that explicitly excluded timeout from the retry surface. Rationale: the suppression rule makes the deny-band rescuer judge load-bearing for EVERY delegated completion (no `attest` toolcall for non-deny-band rows; the judge is the ONLY way the deny band allows end-of-mission), and quick-model tail latency (documented 2.6–22s live, 25s cap) makes the timeout class recurring. Incident bc145c7e R1 itself: a delegated investigation produced a complete report-shaped answer; the rescuer judge timed out at exactly 25.000s (attempt 1 of 1) → conservative fail-safe deny → nudge round-trip → clean complete on the next turn. The retry RECOVERS this class — a complete report + clean rescue would allow end-of-mission without the nudge round-trip.

### Retry semantics (timeout path, incident bc145c7e R1)

| Outcome of attempt 1 | Retry fires? | `attempt` | `verdict` | `is_complete_report` |
|---|---|---|---|---|
| `asyncio.TimeoutError` | **YES** (same input, fresh call, same per-attempt timeout window) | 2 | retry's outcome (success → `complete`/`not_complete`; unparsable → `unparsable`; timeout → `timeout`; error → `error`) | retry's outcome |
| Any other exception (HTTP / API / failover) | No (unchanged — error retry excluded) | 1 | `error` | `False` |

### Worst-case latency (timeout retry)

The timeout retry uses the SAME per-attempt timeout window (`JUDGE_TIMEOUT_S`). Total worst-case wall-clock = `2 × JUDGE_TIMEOUT_S`. With the default 180.0s cap (`ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S`, raised from 25.0s by the 2026-09-26 7d4a3bd9 amendment), worst-case = **360.0s** (user-accepted; both retries stack on the same per-attempt window). The retry does NOT stack timeouts across attempts; each attempt is bounded independently.

### Distinct log discrimination tokens (timeout retry, incident bc145c7e R1)

Three tokens, one per state, emitted at the `attestation_report_judge.py` logger (NOT the graph-node canonical log row — forensic granularity for grep triage):

| Token | When | Format |
|---|---|---|
| `event=fused_judge_first_attempt_timeout` | First attempt timed out, retry pending | `timeout_s=%s latency_ms=%s will_retry=true` |
| `event=fused_judge_timeout_retry` | Retry was fired on the timeout path | `timeout_s=%s attempt=%s` |
| `event=fused_judge_timeout_post_retry` | Both attempts timed out (post-retry timeout, conservative deny) | `timeout_s=%s retry_latency_ms=%s` |

The graph-node canonical log row at `daemon/graph.py:5341` (`event=leader_completion_gate_fused_judge`) discriminates the timeout-retry outcome via `verdict` + `attempt` + `error_class`:
- `verdict=timeout attempt=2 error_class=TimeoutError` → post-retry timeout (fail-safe deny)
- `verdict=complete|not_complete attempt=2 error_class=None` → recovered retry (no nudge round-trip)

### DP-5 posture unchanged

Post-retry timeout still → conservative fail-safe deny → `Decision.DENIED` path → nudge. The judge is best-effort (no fail-safe allow anywhere, incident DP-5 REJECTED). The 3-deny escalation bound caps worst-case misfires.

### Kill-switch OFF = zero judge calls (including zero timeout retries)

The kill-switch `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED=0` is checked at the graph layer BEFORE the judge is called. When the kill-switch is OFF, the judge is never called → zero retries by construction (unparsable retry + timeout retry both suppressed).

### Operator quick-reference (timeout retry)

* **Did the timeout retry fire?** Grep `event=leader_completion_gate_fused_judge llm_judge_attempt=2` AND `verdict=timeout` (post-retry) OR `verdict=complete|not_complete` (recovered).
* **Was the first attempt a timeout?** Grep `event=fused_judge_first_attempt_timeout` (forensic surface inside the function).
* **Was the retry exhausted (both timeouts)?** `event=fused_judge_timeout_post_retry` AND `event=leader_completion_gate_fused_judge llm_judge_attempt=2 verdict=timeout error_class=TimeoutError`.
* **Tuning for a slow quick-model:** Loosen via `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S=40` (worst-case 2 × 40 = 80s with retry). Restart required to flip.
* **Tuning for a fast quick-model:** Tighten via `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S=15` (worst-case 2 × 15 = 30s with retry). Restart required to flip.

### References (timeout retry)

- `.agents/shared/planning/leader-completion-attestation/decisions.md` — D-ENTRY 2026-09-19 (incident bc145c7e R1)
- `daemon/services/attestation_report_judge.py` — `judge_fused_bundle_async` retry-once-on-timeout branch (three new logger.info tokens); module docstring + `timeout_s` parameter docstring updated
- `tests/unit/test_attestation_fused_judge.py` — 5 new tests (timeout-retries-once-recovered, timeout-post-retry-conservative-deny, timeout-then-error-post-retry-conservative, timeout-retry-log-discrimination, timeout-post-retry-log-discrimination); existing `test_timeout_no_retry_conservative` retired (its assertion — `timeout → 1 attempt, attempt=1` — was the inverse of the new contract)

### Runbook note (2026-09-16, incident 6a0d60c9 — marker-path bound enforcement + answer-gate blindness + nudge id stability)

Three fixes shipped always-on (fix/flag policy — no new `ENSEMBLE_*` flags; restart required after upgrading, code change only):

1. **`deny_bound` is now enforced on the marker path.** Previously the bound (`ENSEMBLE_LEADER_ATTESTATION_DENY_BOUND`, default 3) was consulted ONLY on the canonical `decide()` step-(6) deny path. The marker-path allow→deny conversions in `daemon/graph.py` — route (a) (judge verdict=no, nothing pending) and route (d) (judge timeout/error/unparsable, nothing pending) — incremented the deny counter WITHOUT consulting the bound, so a leader whose every turn-end tripped a mid-work marker could deny+nudge forever (incident 6a0d60c9: 123 gate evaluations / 115 deny+nudge injections over 27 min, `attestation_denied_count` climbing 0→122, ZERO `event=leader_completion_gate_terminal_after_bound` rows fleet-wide). Both conversion sites now consult the SHARED predicate `daemon.services.attestation_gate.deny_bound_exceeded` (the same predicate `decide()` step (6) uses). Past the bound the marker path produces the canonical terminal outcome — `set_escalated_and_reset` (flag set + counter reset in one atomic UPDATE) + the `event=leader_completion_gate_terminal_after_bound` operator event + plain allow END with NO nudge — mirroring the canonical bound semantics exactly. Operator impact: the bound is now a REAL backstop on every deny producer; `leader_completion_gate_terminal_after_bound` rows will appear for marker-path loops that previously ran unbounded.

2. **New canonical log field `user_answer_pending` (the 18th field at introduction, 2026-09-16; the canonical schema is 17 fields today — R1 retired the then-18th field `attest_seen_outside_window`, decisions.md D-RES4).** When the leader holds an OPEN awaiting-answer suspension handle at gate time (`task.suspension_reason='awaiting_answer'` + `resume_target_turn_id IS NOT NULL` + `status='paused'`, read via the DB-backed `InstanceManager.has_open_user_answer` → `TaskRepository.has_open_answer_handle_for_gate` — the SAME handle the answer endpoint's `answer_gate_existing_turn` resume consumes), the gate PLAIN ALLOWS before ANY trigger/judge work: no marker scan, no judge call, no nudge, no hint, no counter movement (`decision=allowed_legitimate_pending_wakeup` when the gate is otherwise armed, `decision=allowed` on the conditional-off path — in both cases `user_answer_pending=True` on the row). The pending party is the USER; the leader cannot progress alone. The handle self-clears when the answer is consumed (`ResumeTurn` flips `status='paused' → 'pending'` and nulls the handle columns in one atomic guarded UPDATE) — no stale-allow window — and a freshness guard (the handle must be the instance's NEWEST task row) expires any leaked pre-revive handle so the plain-allow can never become a permanent allow bypass. Grep: `user_answer_pending=True`.

3. **Stable attestation-nudge id.** The deny nudge `HumanMessage` previously minted a fresh `uuid4` per injection, so consecutive denies accumulated one nudge block per deny in the leader's context (incident 6a0d60c9: 115 accumulated blocks). The nudge now carries the stable per-instance id `attestation_nudge:{instance_id}` (minted via `_stable_id_for`); ALL deny producers — the plain `decide()` deny and BOTH marker-path conversions, which funnel through the single construction site — mint the SAME id, so LangGraph's `add_messages` reducer SUPERSEDES the prior nudge block in place and the channel holds exactly ONE nudge block regardless of how many denies fire. `additional_kwargs` (`attestation_nudge`, `injected_message`, `attestation_nudge_denied_count`) are unchanged; the surviving block carries the LATEST deny's counter stamp. Nudge text (`ATTESTATION_NUDGE_TEXT`) is unchanged.

> **2026-09-23 retirement note (incident b2f4dae9):** the
> ``completion_check_note`` F1 Shape A contract referenced in the
> historical nudge-id paragraph was RETIRED along with the entire
> (b)/(d)-with-pending hint surface. The deny-side
> ``attestation_nudge`` supersede contract is unchanged.

**Related files:** `daemon/services/attestation_gate.py` (shared `deny_bound_exceeded` helper; `decide()` arm 3.b; `user_answer_pending` GateDecision field + 17-field `CANONICAL_LOG_SCHEMA_FIELDS`); `daemon/graph.py` (marker-path bound consultations; stable nudge id); `daemon/manager.py` (`has_open_user_answer` facade); `daemon/repositories/task/repository.py` (`has_open_answer_handle_for_gate`); `daemon/services/context_messages.py` (`attestation_nudge` stable-id kind).

**Tests:** `tests/integration/test_attestation_marker_bound_enforcement_lca.py` (7), `tests/integration/test_attestation_user_answer_pending_lca.py` (7), `tests/unit/test_attestation_user_answer_pending_decide.py` (15 — incl. real-SQLite detection tests), `tests/integration/test_attestation_nudge_supersede_lca.py` (3 — real `add_messages` upsert).

**References:**
- `.agents/shared/planning/leader-completion-attestation/decisions.md` — D-ENTRY 2026-09-16 (incident 6a0d60c9 fix cycle FIX-1+2+3)
- `.agents/shared/planning/leader-completion-attestation/requirements.md` — AC-6A0D-1..AC-6A0D-14 (incident 6a0d60c9 acceptance)


## LCA child-terminal contradiction catalog (2026-09-16, advisory REMOVED 2026-09-18)

The LCA stack closes the *leader-side* hallucination bug (an LLM that emits a final assistant message without doing the actual work — gate denies the END, judge disambiguates, marker/length triggers + busy suppression keep FPs tolerable). The complementary *child-side* bug class — a child instance that emits a final report promising future work ("Then I aggregate and write RESULTS. Ending turn.") and then transitions to terminal — was historically caught at the SOURCE by an ADVISORY note attached to the parent's queue. **The advisory note mint was REMOVED 2026-09-18 (D-CTD-7, user decision)** because it was high-FP UX legacy predating the fused judge and its task-less mint caused the strand-wedge class (the only fix for that wedge is the now-also-removed mint-with-delivery seam). The 17-pattern catalog and the scanner function are KEPT — the LLM judge (fused-judge at the resolver) now subsumes the gate role at the cost of one extra quick judge call, and the broader catalog coverage catches more real child-lie cases per the user's explicit decline to tighten.

### What's KEPT (the catalog + scanner, unchanged)

The pure-function catalog and the substring scan function live in `daemon/services/attestation_marker_scanner.py`:

- **`CHILD_TERMINAL_PROMISE_MARKERS`** — 17 entries, FP-tight range 10–18 per the 2026-09-16 spec. The catalog has 17 entries vs. the leader-path `MID_WORK_MARKERS` (16 entries); the extra entry is the explicit `"awaiting"` seed the spec mandates despite its known FP cost. TIGHTER is a PER-ENTRY property, not a COUNT property — each entry is a more specific substring (e.g. `"will write"`, `"will aggregate"`) than the leader-path equivalents, so per-match FP rate is lower even though the catalog carries one more entry overall.
- **`scan_child_terminal_report_for_promises`** — pure substring scan over the catalog. Zero LLM involvement. Returns a `ChildTerminalPromiseScanResult` NamedTuple (`promise_hit`, `matched_terms`).
- **No LLM gating** — the catalog + scanner are independent of the LCA leader gate (decide(), judge service, marker/length triggers, busy suppression, deny-bound escalation). LCA kill-switches do not affect this catalog; it ships always-on (no new `ENSEMBLE_*` env flag).

### What's GONE (the note mint, 2026-09-18)

The advisory note mint site in `daemon/services/child_reports.py::_process_child_completion_db_sync` is REMOVED. The following are no longer present in production code:

- The `[SYSTEM CONTEXT: Child Report Check]` ``HumanMessage`` minted as a SECOND `MessageQueue` row on the parent's queue.
- The server-authored constant note body ("Child {child_id} completed while its final report promises future work (...) — likely premature completion. ...").
- The `child_report_check:{parent_id}:{child_id}` stable id format AND its `_stable_id_for` branch in `daemon/services/context_messages.py` (no remaining callers after the mint deletion). The `CONTEXT_KIND_CHILD_REPORT_CHECK` enum constant is KEPT — the resolver-side detector (``_is_child_report_check_note`` in `daemon/services/attestation_resolver_activation.py`) still uses it to recognize note-shaped messages that older agents may have preserved through compaction. The A-signal path stays untouched (D-CTD-7 (ii)).
- The `additional_kwargs.child_report_check=True` + `child_report_check_terms=[...]` marker kwargs.
- The SAVEPOINT-scope, the `PROCESS_MESSAGE` delivery `Task` mint, and the `notify_worker_pool` wake flag.
- The structured log events `event=leader_completion_gate_child_report_check_fired` / `event=leader_completion_gate_child_report_check_failed`. Operators who `grep` for either will see zero hits post-activation.

### Why the LLM judge subsumes the gate role

The advisory note was a UX-layer signal meant for the parent LLM to manually verify the child's work state. The fused LLM judge (Stage 2+, `attestation_report_judge.py::judge_fused_bundle_async`) is now the verdict for the same bug class — it sees the child's terminal report inline (via the A/B/C bundle) and can directly assess whether the report is a real completion or a promise-while-stopping artifact. Cost: one extra quick judge call per fire. Benefit: no false-positive note text spammed into the parent's queue, no advisory-only UX noise, no strand-wedge risk from the task-less mint.

### What does NOT change

The catalog/scanner removal does NOT touch the leader gate, the LCA judge service, the marker/length trigger logic, the busy-suppression logic, the deny-bound escalation predicate, the A-band activation trigger (the `a_suspicion` term in `activation_predicate`), the bundle assembly (`_build_a_section` with `BUNDLE_A_SECTION_MAX = 3000`), or any of the LCA kill-switches. The A-signal path through `collect_source_a_signals` is preserved verbatim — note-shaped messages that older agents may have preserved through compaction still feed the resolver detector. The catalog stays as the durable byte-identical substrate for any future re-attach point.

### Observability

The `event=leader_completion_gate_child_report_check_fired` log row no longer fires. Operators who relied on it for FP-rate tracking should grep `event=leader_completion_resolver_eval band=a_suspicion a_advisory_present=True` instead — the resolver's Source-A signal row captures the same signal at the gate-evaluation seam (the row also includes the bundle sha256 + size witnesses + per-section sizes, so a re-attach point can verify the A-section is empty / rendering `(no Child Report Check notes delivered)` as expected).

**No new `ENSEMBLE_*` env flag** — the catalog + scanner stay always-on (fix/flag policy 7d5285aa: bugfixes/improvements are not user-togglable). Operators wanting to disable the LCA subsystem as a whole can flip `ENSEMBLE_LEADER_ATTESTATION_MODE=off` (the existing LCA kill-switch). Restart required after upgrading, code change only.

**Related files:**
- `daemon/services/attestation_marker_scanner.py` — `CHILD_TERMINAL_PROMISE_MARKERS` catalog (17 entries, byte-identical), `ChildTerminalPromiseScanResult` NamedTuple, `scan_child_terminal_report_for_promises` pure function.
- `daemon/services/context_messages.py` — `CONTEXT_KIND_CHILD_REPORT_CHECK = "child_report_check"` enum constant KEPT (resolver detector); `_stable_id_for` child_report_check branch REMOVED.
- `daemon/services/attestation_resolver_activation.py` — `_is_child_report_check_note` detector KEPT (A-signal path untouched per D-CTD-7 (ii)); the Source A surface is dormant in production today (no notes to read) but the wiring stays as a future re-attach point.
- `daemon/services/child_reports.py` — mint block REMOVED; finalizer count-guard predicate hardening KEPT (defense-in-depth for any future task-less row producer).

**Tests:** `tests/unit/test_child_terminal_contradiction.py` (14 — pure-function matrix + source-level pins; hook + stable-id + mint+delivery tests deleted with the mint). `tests/unit/test_attestation_resolver_activation.py::TestDCTD7ASignalPathPins` (4 — A-signal path preservation pins; resurrection-loud companion).

**References:**
- `.agents/shared/planning/leader-completion-attestation/decisions.md` — D-CTD-1..D-CTD-6 (2026-09-16 child-terminal contradiction detection entry); D-CTD-7 (2026-09-18 advisory note REMOVED — the entry that supersedes D-CTD-1..6 mint-related parts).
- `.agents/shared/planning/leader-completion-attestation/requirements.md` — CTD-1..CTD-12 (CTD-2/3/6/7/9/10 marked SUPERSEDED 2026-09-18, pointer to D-CTD-7).

## LCA unified resolver — Stage 1 parallel-dry shadow (2026-09-16, additive)

The LCA stack's planned consolidation (fuse sources A/B/C into ONE resolver — spec: `.agents/shared/planning/leader-completion-attestation/resolver-unification.md`) ships its **Stage 1** as a purely additive **shadow**: a pure activation predicate (`daemon/services/attestation_resolver_activation.py`) evaluated in PARALLEL at the existing gate seam (`attestation_gate.evaluate`, canonical-path tail + the C-read DB-error fail-open branch), emitting ONE structured log row per gate evaluation. **Nothing routes through it** — the old gate paths remain authoritative and byte-identical; there are ZERO new LLM calls (the Stage-2 fused-node invocation seam exists structurally but is inert), zero new env flags, and zero nudge/hint/text changes.

### The dry-soak runbook (how to adjudicate)

The soak signal is the **divergence rate between the shadow's would-be outcome and the old path's actual decision**, counted per outcome class. Grep the structured log for:

```
event=leader_completion_resolver_eval
```

Each row carries: `fired`, `band` (`deny|marker|a_suspicion|<none>`), `terms_fired`, `bypass_reason` (`term0_scope_or_mode|meta_bypass|fail_open|none`), the C snapshot (`pending_children`, `queued_or_expected_wakeups`, `live_descendants`, `busy_descendants`), the B snapshot (`marker_hit`, `length_trigger`, `final_word_count`), the A snapshot (`a_advisory_present`, `a_notes`, `a_kwargs_seen`), `would_be_outcome` (`would_allow|would_hint|would_deny_nudge|would_terminal`), `old_decision_value`, `agreement`, `fail_open`, and — when the predicate fires — the fused evidence bundle's `bundle_sha256` + `bundle_size_chars` + per-section sizes (`bundle_a_chars`/`bundle_b_chars`/`bundle_c_chars`/`bundle_u_chars` + the `user_message_included` flag; section U is omitted and the flag logs `False` when no real user message anchors the mission), plus `judge_invoked=False` (the zero-LLM witness).

**Would-be-outcome mapping (no judge in Stage 1).** Deny band → `would_deny_nudge`, or `would_terminal` when the shared deny bound would be exceeded. Marker/A bands → `would_hint` when route-(b) pending work exists (pending ∨ wakeups ∨ live), else `would_allow` (marker-only signal is too weak to deny — the same posture as the judge kill-switch). Fail-open / not-fired → `would_allow`.

**Adjudication rules of thumb:**

1. **`agreement=False` on would-be-deny rows** (`would_deny_nudge`/`would_terminal` vs an allow-family `old_decision_value`) — the resolver would deny where the old path allowed. Investigate each: the deny band is the same R2 predicate family as `decide()` step (6), so a divergence here means an input-visibility difference, not a logic difference.
2. **`band=a_suspicion` rows** — the Δ2 shape (child-terminal contradiction suspicion fires with a non-quiet tree). These map to `would_hint` and NEVER agree at this seam (the old path has no hint outcome at `evaluate()`; its route-(b) hint rides the graph node's post-judge conversion) — count them per mission as the Δ2 spend estimate for Stage 2, do not triage them as gate divergence.
3. **`fail_open=True` rows** — the gate's own C facade reads failed (old behavior: whole-eval fail-open allow). The shadow row mirrors it; a rise in this counter is a DB-health signal, not an LCA signal.
4. **`bypass_reason=meta_bypass` with `fired=False`** on delegated missions — expected quiet shape (attested / answer-pending / no delegation). `bypass_reason=term0_scope_or_mode` rows should be absent entirely: the shadow never runs where the gate doesn't (off-mode / non-leader / master-off return before the seam).
5. **Divergence rate per outcome class** — compute `agreement=False` counts grouped by `would_be_outcome` and `band`. Healthy Stage-1: near-zero `would_deny_nudge`/`would_terminal` divergence; `would_hint` divergence tracks the marker/A-band fire rate (the Δ2/Δ4 soak counter). A rising `would_allow` vs `old_decision_value=denied` divergence means the OLD deny path fired where the resolver would not — check the gate log row's R2 inputs against the shadow row's C snapshot (they are read in the same evaluation, so they should never disagree).

**Error rows:** `event=leader_completion_resolver_eval_error` — a resolver-side exception, isolated from gate control flow (the gate decision is unaffected by construction). Any occurrence is a bug in the shadow; file it against the resolver module, never against the gate.

**Related files:** `daemon/services/attestation_resolver_activation.py` (predicate + bundle + event), `daemon/services/attestation_gate.py` §(vi) (the additive shadow seam), `tests/unit/test_attestation_resolver_activation.py` (63 — R4 short-circuit invariant, predicate matrix incl. the Δ2 row, §10.2 busy⊆live source pin, bundle caps/redaction/Δ1/Δ3, zero-LLM sentinel, row-shape + agreement, exception isolation).


## LCA unified resolver — FINAL (Stage 3 retirement executed 2026-09-17)

**There is EXACTLY ONE completion path.** The gate thread computes the activation predicate + fused evidence bundle (`daemon/services/attestation_resolver_activation.py`, attached to the `GateDecision`); the graph node's **fused block** (guarded only by the snapshot's presence — the `_LCA_STAGE2_RESOLVER_FLIP` constant was DELETED with the Stage-3 retirement) invokes **ONE fused judge** (`daemon/services/attestation_report_judge.judge_fused_bundle_async` — the single judge entry point; retry-once-on-unparsable preserved) and maps the verdict onto the EXISTING outcome machinery (allow / allow+hint / deny+nudge / terminal_after_bound). The two legacy judge sites (marker-path + would-be-deny), their route blocks, the legacy judge service entry points, the `(a)/(b)/(c)/(d)` route enum, the trigger-derivation log fields, the busy-suppressor log field, and the outside-window diagnostic are all DELETED (resolver-unification §7 R1–R8; decisions.md D-RES4). There is **no runtime toggle** (repo convention n); revert = redeploy an earlier build (runbook below).

**Final behavior map** (spec §4.3, DP-5 REJECTED — no fail-safe allow anywhere):

| Band | Judge fires? | verdict=complete | not_complete / error / timeout / unparsable×2 | kill-switch OFF |
|---|---|---|---|---|
| meta-bypass (no delegation / attested / answer-pending) | no — 0 LLM (suspicion scans SKIPPED on non-delegated missions — the D10 mirror) | — | — | — (plain allow) |
| deny band (un-attested ∧ quiet) | yes — on a would-be-DENY decision only (an at-bound TERMINAL decision gets NO judge — budget parity) | allow (rescue) | **deny+nudge via the existing machinery, bound-enforced** | **deny+nudge WITHOUT judge (Q1 parity)** |
| marker band (b_fires ∧ ¬quiet) | yes | plain allow | pending → allow+hint; else deny-flip (structurally unreachable) | plain allow |
| A band (Δ2 — a_suspicion alone, ¬quiet) | yes — the 0→1 row | plain allow | pending → **allow+hint citing A evidence (D4)**; else deny-flip (unreachable) | plain allow |
| dry mode | no — 0 LLM (computed + logged, node skipped) | — | — | — |

### What to grep now (final)

1. **Resolver decisions directly**:
   ```
   event=leader_completion_resolver_eval
   resolver_outcome=allow|allow_hint|deny_nudge|terminal_after_bound
   judge_invoked=True|False     # DERIVED from the real invocation flag
   judge_verdict=complete|not_complete|error|timeout|unparsable|<none>
   ```
2. **Fused judge rows — the ONLY judge event family** (the legacy `*_marker_judge*` names and the bare `leader_completion_gate_judge` row were removed with their call sites; dashboards grepping them must migrate):
   ```
   event=leader_completion_gate_fused_judge            # invocation + verdict
   event=leader_completion_gate_fused_judge_disabled   # kill-switch OFF, verdict=<skipped>
   event=leader_completion_gate_fused_judge_error      # wrapper-layer fault, decision=fail_safe_conservative
   ```
   Grep hygiene: `leader_completion_gate_fused_judge` is a PREFIX of the `_disabled` / `_error` rows — anchor greps on the trailing token (e.g. `grep 'leader_completion_gate_fused_judge '` with the trailing space) exactly like the `leader_completion_resolver_eval` vs `leader_completion_resolver_eval_error` prefix pair.
3. **Canonical gate row**: 17 schema fields; 27 format placeholders. Retired keys that will NEVER appear again: `attest_seen_outside_window=`, `marker_path=`, `marker_judge_verdict=`, `marker_judge_latency_ms=`, `marker_judge_error_class=`, `trigger_source=`, `trigger_suppressed_by=`. On non-delegated missions the suspicion fields (`marker_hit`/`length_trigger`/`final_word_count`) carry their False/0 defaults (the scans are skipped — D10 mirror).
4. **D4 hint citations**: hint rows may append a `Completion evidence cited by the completion judge:` block + `Advisory:` line (capped 5×120 + 240 chars). All id-bearing bundle text (incl. leader-prose excerpts) is UUID-redacted.
5. **Zero-LLM rows preserved**: meta-bypass / dry / not-fired / fail-open rows log `judge_invoked=False`.

### Revert runbook

Revert = **redeploy the earlier build** (frozen-PyInstaller, rebuild+restart discipline — the retirement is code deletion, no env lever exists by design). Pre-Stage-3 lineage restores the Stage-2 dead-but-present shape; pre-Stage-2 restores the legacy-authoritative shape. The kill-switch matrix to de-risk BEFORE reverting:

| Knob | Effect (final architecture) |
|---|---|
| `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED=0` (+restart) | Fused judge never fires: deny band → deny+nudge WITHOUT judge (Q1); marker/A bands → plain allow. Cheapest incident brake — prefer this over reverting. |
| `ENSEMBLE_LEADER_ATTESTATION_MODE=dry` (+restart) | Resolver computed + logged, node skipped, 0 LLM, allow-everything (passive observation). |
| `ENSEMBLE_LEADER_ATTESTATION_MODE=off` (+restart) | The whole gate is off (pre-feature baseline). |
