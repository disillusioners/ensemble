# Agents Ensemble — Tester README

## Project Overview
Persistent multi-agent daemon built with LangGraph. Agents defined by markdown files with HTTP API, OpenAI-compatible LLM support, session hierarchy for agent spawning/communication, and SQLite checkpoints for crash recovery.

## Test Framework
- **pytest** with `tests/conftest.py` that mocks langgraph modules
- Integration tests under `tests/integration/` (require OPENAI_API_KEY for real LLM calls)
- Unit tests at `tests/test_*.py` and `tests/unit/`
- **conftest.py** pre-populates `sys.modules` with langgraph mocks — all unit tests use these

## Key Test Patterns
- `conftest.py` pre-populates `sys.modules` with langgraph mocks — all unit tests use these
- Tools tested by creating tool then calling `.invoke({"param": value})`
- Filesystem tests use `tmp_path` fixture
- Cache tests use `time.sleep(0.1)` between mtime changes
- Config tests use YAML fixtures and env var manipulation

## Test Structure
```
tests/
├── conftest.py              # Unit test fixtures (mocks langgraph)
├── test_*.py                # Unit tests (top-level)
├── unit/                    # Unit tests (subdirectory, no __init__.py needed)
├── integration/
│   ├── conftest.py          # Integration fixtures (real config, no langgraph mocks)
│   └── test_*.py            # Integration tests (skip without OPENAI_API_KEY)
├── job_queue/               # Job queue tests
├── message_queue_redesign/  # Message queue redesign tests (Phase 1-3)
│   ├── conftest.py          # MQ test fixtures (in-memory SQLite, test repos)
│   ├── test_event_repository.py   # Event repository tests
│   ├── test_stale_task_recovery.py # Stale task recovery tests
│   ├── test_task_repository.py    # Task repository + atomic claim tests
│   └── test_worker_pool.py        # Worker pool lifecycle tests
└── mock_*.py                # Mock test scripts
```

## Compaction Testing
- `daemon/compaction.py` — Full compaction engine
- `daemon/graph.py` — `SessionState(MessagesState)` with `compacted_at`
- `daemon/manager.py` — `_maybe_compact_context()` integration
- `daemon/config.py` — `CompactionConfig`
- `daemon/loader.py` — `estimate_messages_tokens()` (uses tiktoken)

### Key Types for Testing
- `CompactionContext` — Input container for compaction
- `CompactionResult` — Output with replacement_messages, tokens, type
- `MessageGroup` — Atomic group (single or tool_sequence)
- `SessionState(MessagesState)` — LangGraph state with `compacted_at`

### Important Function Signatures
- `identify_boundary_groups(messages: list[BaseMessage]) -> list[MessageGroup]`
- `select_compactable_groups(groups, recent_window, min_window, context_window, system_prompt_tokens, estimate_fn, config_threshold) -> (compactable, preserved, actual_window)`
- `emergency_truncate(messages, max_tokens, estimate_fn, max_tool_response_chars, max_human_message_chars) -> list[BaseMessage]`
- `_truncate_batch_to_fit(batch_groups, max_tokens, tokenizer_fn, max_tool_response_chars) -> list[MessageGroup]`
- `get_model_context_limit(model_name, config=None) -> int`
- `ContextCompactor._build_replacement_messages(compactable_groups, preserved_groups, summary) -> list[BaseMessage]`
- `ContextCompactor._is_recently_compacted(last_compacted_at) -> bool`
- `ContextCompactor.compact_state(context) -> CompactionResult | None`
- `ContextCompactor._merge_summaries(partial_summaries, context) -> SystemMessage`
- `ContextCompactor._call_summarization_llm(prompt, context) -> str`

## Test Results (Latest: 2026-05-28 resume-child-notification)

### Completion Report Idempotency Fix (2026-05-28)
- **Files**: `tests/unit/test_completion_report_idempotency.py` (11 tests)
- **New Tests**: 11/11 PASS (force_notify, stale report deletion, idempotency preserved, edge cases)
- **Regression**: 4,829/4,831 PASS (2 pre-existing failures, 0 regressions)
- **Quick Fixes**: 1 (manager wrapper missing `force_notify` parameter)
- **ensure.md**: PASS (dev.sh stable 30s)
- **Bug Tested**: After resume, child's completion report found by idempotency check, parent never notified
- **Fix Location**: `daemon/manager.py` + `daemon/services/child_reports.py`
- **Commit**: `4e01668`

### Idempotency Fix Status: ✅ READY (11 new tests, 4,829 regression tests, 0 regressions, dev.sh stable)

### Child Completion Notification in Resume Path (2026-05-28)
- **File**: `tests/unit/test_resume_child_notification.py`
- **New Tests**: 9/9 PASS (notification called in both branches of resume_processing_job)
- **Regression**: 85/85 PASS (child_resume + tree_aware + tree_traversal + pause_cascade + resume_waiting_children)
- **Quick Fixes**: 2 (stale test expectations in test_child_resume.py — not a regression)
- **ensure.md**: PASS (dev.sh stable 30s)
- **Bug Tested**: After resume, child completes but parent never gets notified
- **Fix Location**: `daemon/manager.py` — `_process_child_completion_and_notify_parent()` call added
- **Commits**: `8993d32` (new tests), `e2173c7` (stale expectation fix)

### Notification Status: ✅ READY (9 new tests, 85 regression tests, 0 failures, dev.sh stable)

### waiting_for > 0 Check in resume_processing_job — Round 2 (2026-05-28)
- **File**: `tests/unit/test_resume_waiting_children.py`
- **Updated Tests**: 8/8 PASS (Round 2: `waiting_for > 0` instead of status-based)
- **Regression**: 77/77 PASS (child_resume + tree_aware_pause_resume + tree_traversal + pause_cascade)
- **ensure.md**: PASS (dev.sh stable 30s)
- **Bug Tested**: Status is RUNNING during resume, not WAITING_CHILDREN — so status-based check failed
- **Fix Location**: `daemon/manager.py` — `waiting_for > 0` check before `complete_job()`
- **Commit**: 9ddb72f
- **Test Coverage**:
  1. `waiting_for=1`, `status=RUNNING` → **skip** (core bug scenario)
  2. `waiting_for=0` → complete normally
  3. `waiting_for=None` → treated as 0, completes
  4. `waiting_for=3` → skip (multiple children)
  5. Instance not found → falls through to complete
  6. Repository exception → exception handler allows completion
  7. Diagnostic log with correct values emitted
  8. Both conditions (`waiting_for=1` + WAITING_CHILDREN status) → skip

### Round 2 Status: ✅ READY (8 updated tests, 77 regression tests, 0 failures, dev.sh stable)

### Child Instance Resume — Message Appended (2026-05-27)
- **Branch**: `fix/child-resume-message`
- **Commits**: `9d454fe` (fix) + `52d1950` (tests)
- **New Child Resume Tests**: 8/8 PASS (`tests/unit/test_child_resume.py`)
- **Regression**: 58/58 PASS (resume append + tree-aware + tree traversal)
- **ensure.md**: PASS — dev.sh stable (30s timeout, clean shutdown)
- **Quick Fixes**: 1 (is_cancelled property access in test)
- **Fix**: `resume_processing_job()` else branch handles child instances (WorkerPool path)
- **Verified**: CancelledError, general exceptions, fresh UUID, message_source="cascade_resume"
- See `.agents/tester/RESULTS/2026-05-27-child-resume-message.md` for full report

### Child Resume Status: ✅ READY (8 new tests, 58 regression tests, 0 failures, dev.sh stable)

### Project-Aware Instance URL Routing (2026-05-27)
- **Branch**: `latest`
- **Commit**: c66075b (feature) + 86f46eb (test fix)
- **Frontend Unit Tests**: 800/800 PASS (77 new routing tests, 22 suites)
- **Browser E2E**: 4/4 PASS (URL routing, redirect, back-nav)
- **ensure.md**: PASS — dev.sh stable (30s timeout)
- **Quick Fixes**: 1 commit (variable scoping in home.component.spec.ts)
- **New Route**: `/projects/:projectId/instances/:instanceId` resolves correctly
- **Backward Compat**: Old `/instances/:id` redirects to `/projects/all/instances/:id`
- **All 10 navigation points** verified producing correct URLs
- See `.agents/tester/RESULTS/2026-05-27-project-aware-url-routing.md` for full report

### Project-Aware URL Routing Status: ✅ READY (77 new tests, 0 regressions, all E2E pass, dev.sh stable)

### Tree-Aware Pause/Resume — Phase 4 (2026-05-27)
- **Branch**: `latest`
- **Commits**: `56b76e7` (tree traversal tests) + `9f08b4c` (cascade lifecycle tests + quick fix)
- **New Tree Traversal Tests**: 23/23 PASS (`tests/unit/test_tree_traversal.py`) — real in-memory SQLite
- **New Cascade Lifecycle Tests**: 27/27 PASS (`tests/unit/test_tree_aware_pause_resume.py`) — waiting_for semantics verified
- **Existing Cascade Tests**: 19/19 PASS (regression)
- **API Regression**: 43/43 PASS
- **Total**: 112/112 PASS, 0 regressions
- **ensure.md**: PASS — dev.sh stable (30s timeout)
- **Quick Fixes**: 1 commit (try-except per node in resume matching pause behavior)
- **Critical Design Verified**: PAUSE resets all waiting_for=0, RESUME from root=all 0, RESUME from child=ancestors get 1
- See `.agents/tester/RESULTS/2026-05-27-tree-aware-pause-resume-phase4.md` for full report

### Phase 4 Status: ✅ READY (50 new tests, 0 regressions, waiting_for semantics verified, dev.sh stable)

### Resume — Message Appends Not Replaces (2026-05-26)
- **Branch**: `latest`
- **Commit**: `ab23b16` — fix: resume message now appends instead of replacing first message
- **New Resume Append Tests**: 8/8 PASS (`tests/unit/test_resume_message_append.py`)
- **API Regression**: 43/43 PASS
- **Core Regression**: 156/157 PASS (1 pre-existing title-generation CancelledError mock issue)
- **Frontend Unit Tests**: 723/723 PASS
- **ensure.md**: PASS — dev.sh stable (30s timeout)
- **Quick Fixes**: None needed
- See `.agents/tester/RESULTS/2026-05-26-resume-append-message.md` for full report

### Resume Append Status: ✅ READY (8 new tests pass, 0 regressions, dev.sh stable)

### Resume — Message Target Only (2026-05-26)
- **Branch**: `latest`
- **Commit**: `c3ce6cf` — fix: send resume message to target instance only, children resume silently
- **Backend Unit Tests**: 7458/7461 PASS (3 pre-existing/environmental)
- **Frontend Unit Tests**: 723/723 PASS
- **Browser E2E**: 5/5 steps PASS (create → pause → resume → verify target vs children → no zombies)
- **ensure.md**: PASS — dev.sh stable (231s+ uptime)
- **Quick Fixes**: None needed
- See `.agents/tester/RESULTS/2026-05-26-resume-target-message.md` for full report

### Resume Target Message Status: ✅ READY (0 regressions, E2E confirms target gets message, children silent)

### Resume — Re-execute Existing Job from Checkpoint — Latest Commit (2026-05-26)
- **Branch**: `feature/redesign-resume`
- **Commits**: fd8f6e2 → 0a3ec53 (quick fix: test mocks updated)
- **Backend Unit Tests**: 3256/3258 PASS (2 pre-existing/environmental)
- **Frontend Unit Tests**: 723/723 PASS (18 suites, 5.156s)
- **Browser Automation**: 6/6 steps PASS (pause during LLM → resume → complete → no zombies)
- **Job Queue Final**: 0 zombie PROCESSING jobs after resume
- **ensure.md**: PASS — dev.sh stable at 1817s uptime
- **Quick Fixes**: 1 commit (resume API test mocks + ResumeRequest in __all__)
- See `.agents/tester/RESULTS/2026-05-26-resume-redesign-latest.md` for full report

### Resume Redesign Latest Status: ✅ READY (3256 backend pass, 723 frontend pass, browser E2E pass, 0 regressions, dev.sh stable)

### Resume Redesign — Send Message on Resume — Full Testing (2026-05-26)
- **Branch**: `feature/redesign-resume`
- **Commits**: b8406f4 → 39dbba9 → 1250fd5 → fdb6c7b (quick fix) → 73a0c65 (mock test)
- **New Resume Tests**: 7/7 PASS (test_api.py)
- **API Regression**: 42/42 PASS
- **Pause Regression**: 60/60 PASS
- **Job Queue Regression**: 1144/1145 PASS (1 environmental)
- **Frontend Unit Tests**: 723/723 PASS
- **Mock Integration**: 22/22 PASS (default message, custom message, already-running)
- **ensure.md**: PASS — dev.sh stable on port 8079
- **Quick Fixes**: 1 (parameter ordering in instances router)
- See `.agents/tester/RESULTS/2026-05-26-resume-redesign.md` for full report

### Resume Redesign Status: ✅ READY (7 new tests pass, 22 mock assertions pass, 0 regressions, dev.sh stable)

### Fix Pause Causing Job to Complete — Full Testing (2026-05-26)
- **Commits**: 3a690da → 184abb6 → cleanup → 58c76e2
- **New Tests**: 12/12 PASS (test_pause_while_processing.py)
- **Pause Regression**: 57/57 PASS (8 instance_pause + 20 cascade + 29 job_processor)
- **Termination Regression**: 23/23 PASS
- **Job Queue Full Suite**: 1,144/1,144 PASS (1 environmental — port 8079 in use)
- **API + Core Tests**: 65/65 PASS
- **ensure.md**: PASS — dev.sh stable on port 8079
- **Total**: 1,300/1,300 code-related tests PASS
- See `.agents/tester/RESULTS/2026-05-26-fix-pause-job-complete.md` for full report

### Fix Pause Causing Job to Complete Status: ✅ READY (12 new tests pass, 0 regressions, dev.sh stable)

### Fix Instance Termination with Job Queue — Full Testing (2026-05-26)
- **New Tests**: 23/23 PASS (test_instance_termination_job_cleanup.py)
- **Mock Integration**: 6/6 scenarios PASS (terminate happy path, re-entrancy guard, jobs, parent-child, sequential)
- **Regression**: 57/57 pause tests + 1132/1133 job queue (1 environmental) + 47/47 API tests
- **ensure.md**: PASS — dev.sh stable on port 8079 (87s uptime)
- See `.agents/tester/RESULTS/2026-05-26-fix-terminate-instance.md` for full report

### Fix Instance Termination Status: ✅ READY (23 new tests pass, 6 mock scenarios pass, 0 regressions)

### Fix Pause Button — Full Testing (2026-05-26)
- **Commits**: 7b4116d → 2f4596b → fa61ace → 5e50031 → 7101ab7
- **Backend Unit Tests**: 3,102 run, 3,101 passed (1 environmental — port 8079 in use)
- **New Pause Tests**: 57/57 PASS (8 instance_pause + 19 cascade + 30 job_processor)
- **Frontend Unit Tests**: 723/723 PASS
- **Mock Integration**: 12/12 assertions PASS — Live dev server: pause, resume, idempotency, message queuing
- **Browser Automation**: ✅ Code verified, UI working (timing limitations for manual testing)
- **Quick Fixes**: 4 (3 test fixes + 1 sidebar visibility alignment)
- **ensure.md**: PASS — dev.sh stable on port 8079
- See `.agents/tester/RESULTS/2026-05-26-fix-pause-button.md` for full report

### Fix Pause Button Status: ✅ READY (57 new tests pass, 12 mock assertions pass, 4 quick fixes, dev.sh stable, 0 regressions)

### Project Delete Cleanup — Phase 1 (2026-05-25)
- **Commits**: 813e097 (initial) + 1ce9a04 (fixes)
- **Mock Tests**: 25/25 assertions PASS — Live dev server: 404, happy path, 409 protection, force delete, cascade verification, in-memory cleanup
- **Quick Fixes**: 2 (queue creation field name, 409 test instance state)
- **ensure.md**: PASS — dev.sh stable on port 8079
- See `.agents/tester/RESULTS/2026-05-25-project-delete-cleanup.md` for full report

### Project Delete Cleanup Status: ✅ READY (25 mock assertions pass, dev.sh stable, 0 regressions)

### Ensure System Queues — Phase 2 (2026-05-25)
- **Commit**: eb4bcc1 (feature), 8ccf4cc (tests), a7c2851 (bug fix), 1ce9a04 (cascade fix)
- **Unit Tests**: 9/9 PASS — Service layer (partial, all, none, idempotency) + API endpoint (200, 404, correctness, idempotency, partial)
- **Mock Tests**: 20/20 assertions PASS — Live dev server: discover project, create, ensure, idempotency, correctness, 404, cleanup
- **ensure.md**: PASS — dev.sh stable, healthy
- **Bugs Found & Fixed**: 2 (project repo not initialized for queues router, project delete cascade cleanup order)
- See `.agents/tester/RESULTS/2026-05-25-ensure-system-queues.md` for full report

### Ensure System Queues Status: ✅ READY (9 unit tests pass, 20 mock assertions pass, 2 bugs fixed, dev.sh stable, 0 regressions)

### Ensure System Queues — Phase 3 Frontend (2026-05-25)
- **Commits**: 67e4fdf (feature), 9716410 (jest fix), 3025e73 (tests)
- **Unit Tests**: 692/692 PASS (690 existing + 2 new for ensureSystemQueues service)
- **ensure.md**: PASS — dev.sh stable on port 8079
- **Quick Fixes**: 1 (jest.config.js e2e test exclusion)
- **New Tests**: 2 (ensureSystemQueues response correctness, array typing)
- See `.agents/tester/RESULTS/2026-05-25-ensure-system-queues-frontend.md` for full report

### Ensure System Queues Frontend Status: ✅ READY (692 unit tests pass, service layer covered, dev.sh stable, 0 regressions)

### Instance Sort — Full Browser Automation Verification (2026-05-25)
- **Build Check**: ✅ PASS — tsc --noEmit zero errors
- **Unit Tests**: 690/690 PASS (18/20 suites; 2 e2e Playwright suites excluded for Jest/Playwright config mismatch)
- **Browser Automation**: ✅ PASS — all 4 criteria met (new instance at TOP, each spawn pushes previous down, "Just now" text, compile clean)
- **ensure.md**: PASS — dev.sh stable ~113 seconds
- **Quick Fixes**: 0
- See `.agents/tester/RESULTS/2026-05-25-instance-sort-full-browser-verification.md` for full report

### Instance Sort Full Browser Verification Status: ✅ READY (build clean, 690 unit tests pass, browser automation verified all criteria, dev.sh stable, 0 regressions)

### Job Status Enum/String Fix (2026-05-25)
- **Branch**: fix/job-status-str-enum (commit 45b4814)
- **New Tests**: 15/15 PASS — Status guard: enum .value extraction, string passthrough, job transitions, edge cases
- **Regression**: 1088/1088 PASS (1 pre-existing port conflict, unrelated to fix)
- **ensure.md**: PASS — dev.sh stable 30s+
- **Quick Fixes**: 0
- See `.agents/tester/RESULTS/2026-05-25-job-status-enum-string-fix.md` for full report

### Job Status Enum/String Fix Status: ✅ READY (15 new tests pass, 1088 regression pass, dev.sh stable, 0 regressions)

### Sort By Created At Desc Utility Tests (2026-05-25)
- **Commit**: bbe2da1
- **New Tests**: 9/9 PASS — sortByCreatedAtDesc: basic sort, null/undefined handling, merge scenario, pagination scenario, SSE out-of-order arrival, immutability, empty/single element
- **Regression**: 689/689 PASS (9 new + 680 existing, 0 failures)
- **ensure.md**: PASS — dev.sh stable 30s+
- **Quick Fixes**: 3 (test helper alignment)
- See `.agents/tester/RESULTS/2026-05-25-sort-by-created-at-desc-tests.md` for full report

### Sort By Created At Desc Utility Tests Status: ✅ READY (9 new tests pass, 689 suite pass, dev.sh stable, 0 regressions)

### Instance List Sorting Fix (2026-05-25)
- **Branch**: feature/new-instance-top (commit 9f28afd)
- **Change**: One-line fix — local-only instances prepended at top instead of appended at bottom in `mergeInstances()`
- **Unit Tests**: 680/680 PASS (existing test already covers sort order behavior)
- **Browser Automation**: ✅ PASS — new instance confirmed at TOP of list
- **ensure.md**: PASS — dev.sh stable 45+ minutes
- **Quick Fixes**: 0
- See `.agents/tester/RESULTS/2026-05-25-instance-sort-fix.md` for full report

### Instance List Sorting Fix Status: ✅ READY (680 unit tests pass, browser automation pass, dev.sh stable, 0 regressions)

### Instance Sort Order — Browser Automation Verification (2026-05-25)
- **Unit Tests**: 690/690 PASS (type check pass)
- **Browser Automation**: ✅ PASS — all 3 criteria met (new instance at TOP, each spawn pushes previous down, "Just now" text)
- **ensure.md**: PASS — dev.sh stable 21+ minutes
- **Quick Fixes**: 0
- See `.agents/tester/RESULTS/2026-05-25-instance-sort-browser-verification.md` for full report

### Instance Sort Order Browser Verification Status: ✅ READY (690 unit tests pass, browser automation verified all criteria, dev.sh stable, 0 regressions)

### Defer Job Race Condition Fix (2026-05-25)
- **Commit**: c4f6e17 (fix) + fead301 (tests)
- **New Tests**: 16/16 PASS — _select_next_eligible_job idle check, priority bypass, multiple defer queues, edge cases, _get_next_job integration
- **Regression**: 1089/1089 PASS (16 new + 1073 existing, 19 skipped, 0 failures)
- **ensure.md**: PASS — dev.sh stable 30s+
- **Quick Fixes**: 0
- See `.agents/tester/RESULTS/2026-05-25-defer-race-condition-fix.md` for full report

### Defer Job Race Condition Fix Status: ✅ READY (16 new tests pass, 1089 suite pass, dev.sh stable, 0 regressions)

### Instance List Scroll Fix (2026-05-25)
- **Branch**: feature/instance-list-scroll-fix (commits 06464dc + 5ec6cd2 + b1e3cd4)
- **New Tests**: 18/18 PASS — scroll save/restore, refresh button, polling interval, ngOnDestroy cleanup
- **Regression**: 679/679 PASS (661 existing + 18 new, 0 failures)
- **ensure.md**: PASS — dev.sh stable 30s+
- **Quick Fixes**: 0
- See `.agents/tester/RESULTS/2026-05-25-instance-list-scroll-fix.md` for full report

### Instance List Scroll Fix Status: ✅ READY (18 new tests pass, 679 total pass, dev.sh stable, 0 regressions)

### Project Metadata Table Separation (2026-05-25)
- **Branch**: feature/metadata-table (commits de4ad4f + d897fc8 + 0e59e21 + 9861040)
- **New Tests**: 42/42 PASS — CRUD, upsert, enrichment, create/update/delete, value types, migration
- **Regression**: 2593/2593 PASS (core 658 + api 201 + job_queue 1073 + frontend 661, 27 skipped)
- **ensure.md**: PASS — dev.sh stable 30s+
- **Quick Fixes**: 1 — test_models.py enum casing (running→RUNNING)
- See `.agents/tester/RESULTS/2026-05-25-project-metadata-table.md` for full report

### Project Metadata Table Status: ✅ READY (42 new tests pass, 2593 regression tests pass, dev.sh stable, 0 regressions)

### Message API → JobQueue (2026-05-25)
- **Commits**: 914adaa + ee3bdca + 20b61f0 + 215629c + daf846e
- **New Tests**: 29/29 PASS — All 10 test scenarios (job creation, concurrency gate, orphan recovery, cancellation, termination, backward compat, side effects, status endpoint, error handling, no-project-context)
- **Job Queue Suite**: 1073/1073 PASS (+29 new, 19 skipped, 0 regressions)
- **ensure.md**: PASS — dev.sh stable 30s+
- **Quick Fixes**: 2 — api.py init order fix + job_type migration
- See `.agents/tester/RESULTS/2026-05-25-message-api-job-queue.md` for full report

### Message API → JobQueue Status: ✅ READY (29 new tests pass, 1073 suite tests pass, dev.sh stable, 0 regressions)

### system_defer_queue Auto-Provision (2026-05-25)
- **Branch**: feature/defer-queue-ui (commits f38bf92 + 104e15f + 8c0d781)
- **Backend Unit Tests**: 4485/4485 PASS (+52 new, including 5 system_defer_queue auto-provision tests)
- **Frontend Unit Tests**: 661/661 PASS (0 regressions)
- **Browser Verification**: 6/6 PASS — system_defer_queue in sidebar, schedule icon, DEFER badge, start/stop toggle, no delete button for system queues, defer queue creation works, reserved name protection works
- **ensure.md**: PASS — dev.sh stable 30s+, auto-provisioned system queues for 34 projects
- **Quick Fixes**: 0
- **Regressions**: 0
- See `.agents/tester/RESULTS/2026-05-25-system-defer-queue-auto-provision.md` for full report

### system_defer_queue Auto-Provision Status: ✅ READY (4485 backend + 661 frontend tests pass, browser verified, no regressions)

### Defer Queue UI (2026-05-24)
- **Branch**: feature/defer-queue-ui (commits f38bf92 + 104e15f)
- **Unit Tests**: 661/661 PASS — All frontend tests pass (+45 new tests from 616)
- **Model Tests**: getQueueTypeIcon('defer') → 'schedule', getQueueTypeLabel('defer') → 'Defer' ✅
- **ensure.md**: PASS — dev.sh stable 30s+, API healthy, /jobs page loads
- **Quick Fixes**: 0 (all tests pass as-is)
- **Regressions**: 0
- See `.agents/tester/RESULTS/2026-05-24-defer-queue-ui.md` for full report

### QueueShutDown 500 Error Fix (2026-05-23)
- **Bugs Fixed**: 2 — QueueShutDown exception in live_event_hub.py + Session binding in instance_messaging.py
- **Unit Tests**: 45/45 PASS — LiveEventHub (40 existing + 5 new QueueShutDown tests)
- **API Integration**: ✅ PASS — POST /api/instances/:id/messages returns 200 (was 500)
- **ensure.md**: PASS — dev.sh stable 30s+
- **Quick Fixes**: 2 (new QueueShutDown tests + session binding capture)
- **Commits**: 1ca33ca (tests) + c1b86015 (session fix)
- See `.agents/tester/RESULTS/2026-05-23-queueshutdown-500fix.md` for full report

### QueueShutDown Fix Status: ✅ READY (45/45 unit tests pass, API returns 200, dev.sh stable)

### RAG Auto-Test on Startup (2026-05-22)
- **Feature**: RAG auto-test on startup — validates LightRAG connectivity, disables RAG gracefully on failure
- **New Tests**: 27/27 PASS — auto_test_rag(), disable_rag(), enable_rag(), from_env() resilience
- **Regression**: 95/95 PASS — RAG client (46) + tools (25) + workspace scoping (24), zero regressions
- **Lifespan Integration**: Verified — api.py calls auto_test_rag() as first startup step
- **ensure.md**: PASS — dev.sh stable 30s+
- **Quick Fixes**: 0 (all tests pass as-is)
- See `.agents/tester/RESULTS/2026-05-22-rag-auto-test.md` for full report

### RAG Auto-Test Status: ✅ READY (122/122 tests pass, lifespan verified, no regressions)

### MCP Cold-Load Race Condition Fix (2026-05-22)
- **Branch**: feature/fix-mcp-cold-load (commits cfe5416 + cbab340)
- **Unit Tests**: 4433/4433 PASS (0 failures, 27 skipped)
- **New Race Condition Tests**: 6/6 PASS — cold-load preload ordering, hot path no-preload, graceful degradation
- **E2E MCP Tests**: 24/24 PASS — 8 (tools available) + 16 (restore after restart)
- **ensure.md**: PASS — dev.sh stable 30s+
- **Quick Fixes**: 2 (import path fix in daemon/persistence.py + conftest mock)
- See `.agents/tester/RESULTS/2026-05-22-mcp-cold-load-race-fix.md` for full report

### MCP Cold-Load Fix Status: ✅ READY (all tests pass, race condition verified fixed)

### Project History E2E (2026-05-22)
- **Feature**: project_history (4 phases: model, repository, tools, API, injection)
- **Existing Tests**: 86/86 PASS — 27 repo + 26 API + 33 integration (no regressions)
- **New Tool Tests**: 38/38 PASS — add (15), list (8), search (8), delete (4), constants (2)
- **New Injection Tests**: 28/28 PASS — rendering (22), serialization (6)
- **Total**: 152/152 PASS across 5 test files
- **ensure.md**: PASS — dev.sh stable 30s+
- **Quick Fixes**: 0 (all tests pass as-is)
- See `.agents/tester/RESULTS/2026-05-22-project-history-e2e.md` for full report

### Project History E2E Status: ✅ READY (all 152 tests pass, no regressions)

### MCP Disable Flags (2026-05-22)
- **Branch**: feature/mcp-disable-flags (commit 5b7fe77 + cf9a247)
- **New Tests**: 74/74 PASS — is_builtin_disabled helper, bootstrap disable/enable, API protection, config validation
- **MCP Regression**: 251/251 PASS — all 7 MCP test files pass, zero regressions
- **ensure.md**: PASS — dev.sh stable 30s+
- **Quick Fixes**: 0 (all tests pass as-is)
- See `.agents/tester/RESULTS/2026-05-22-mcp-disable-flags.md` for full report

### MCP Disable Flags Status: ✅ READY (all tests pass, no regressions)

### MCP Localhost Config Fix (2026-05-21)
- **Branch**: feature/fix-mcp-localhost-block (commit 258b801 + ac310ed)
- **BE MCP Tests**: 68/68 SSRF + 55/55 CRUD + 40/40 warmup + 19/19 connection + 25/25 service + 25/25 context7 + 44/44 gaia
- **SSRF Verification**: 9/9 PASS — localhost/127.0.0.1/10.x/192.168.x accepted, 169.254.169.254 blocked, strict mode works
- **Core Unit Tests**: 1760 passed (3 pre-existing failures unrelated)
- **Browser Automation**: PASS — localhost URL accepted in MCP dialog (401 not SSRF block)
- **ensure.md**: PASS — dev.sh stable 30s+
- **Quick Fixes**: 1 (frontend dist path in daemon/api.py)
- See `.agents/tester/RESULTS/2026-05-21-mcp-localhost-fix.md` for full report

### MCP Localhost Fix Status: ✅ READY (all tests pass, browser verified, no regressions)

### MCP Test Connection Button (2026-05-21)
- **Branch**: feature/mcp-test-button (commit 75bc70c)
- **BE New Tests**: 60/60 PASS — SSRF validation (42), endpoint logic (11), helper function (5)
- **FE New Tests**: 23 new (577/577 total PASS) — dialog (15), service (8)
- **FE Build**: PASS (4.27s)
- **Browser Automation**: 7/7 PASS — button visibility, loading, result, auto-clear
- **ensure.md**: PASS — dev.sh stable 30s+
- **Quick Fixes**: 2 (SSRF validation order + indentation bug)
- See `.agents/tester/RESULTS/2026-05-21-mcp-test-button.md` for full report

### MCP Test Connection Button Status: ✅ READY (all tests pass, browser verified, no regressions)

### MCP Restore After Daemon Restart (2026-05-21)
- **Branch**: fix/mcp-tools-not-available-to-llm (commit 43e208b + quick fix e36d76e)
- **E2E Restore Test**: 16/16 PASS — MCP tools survive daemon restart on same instance
- **Unit Tests**: 224/224 MCP-related PASS (642/653 core — 11 pre-existing langgraph import failures)
- **ensure.md**: PASS — dev.sh stable 41s+, MCP warmup pool works
- **Quick Fixes**: 1 (docstring indentation in ensure_mcp_preloaded)
- **Root Cause**: ensure_mcp_preloaded() skipped all in-memory instances, even those without cached MCP tools
- **New Test**: tests/e2e/test_mcp_tools_restore.py (full restore E2E test)
- See `.agents/tester/RESULTS/2026-05-21-mcp-restore-after-restart.md` for full report

### MCP Restore After Restart Status: ✅ READY (all tests pass, E2E verified, no regressions)

### MCP Warmup Pool Tool Adaptation + Scan Ordering Fix (2026-05-20)
- **Branch**: fix/mcp-tools-not-available-to-llm (commit 73be23f)
- **Unit Tests**: 251/251 PASS across 8 MCP packs (warmup_pool, core, gaia, service, connection, runtime, context7, crud)
- **E2E Test**: 8/8 PASS — MCP tools verified in API (mcp_context7_*, mcp_webfetch_*) + LLM response mentions MCP
- **ensure.md**: PASS — dev.sh stable 30s+, MCP servers warm up (context7 + webfetch)
- **Quick Fixes**: 0 (none needed)
- **Root Cause**: warmup_pool skipped adapt_mcp_tools() + wrong tool scan ordering
- See `.agents/tester/RESULTS/2026-05-20-mcp-warmup-adapt-scan-ordering.md` for full report

### MCP Warmup Pool Tool Adaptation Fix Status: ✅ READY (all tests pass, E2E verified, no regressions)

### MCP Tools Visible to LLM Fix (2026-05-20)
- **Branch**: fix/mcp-tools-not-available-to-llm (commits b2cd271, 2af8f97)
- **Unit Tests**: 1,179 tests run, all branch-relevant PASS (27 pre-existing failures unrelated)
- **MCP Tool Filter**: 22/22 PASS (core fix verified)
- **Gaia Agent**: 44/44 PASS (2 pre-existing failures FIXED by this branch!)
- **E2E Test**: 8/8 PASS — MCP tools verified in API (`mcp_context7_*`, `mcp_webfetch_*`) + LLM response mentions MCP
- **ensure.md**: PASS — dev.sh stable 30s+, MCP servers warm up (context7 + webfetch)
- **Quick Fixes**: 0 (none needed)
- **Root Cause**: `resolve_tool_filter()` called without `all_tool_names` so "mcp" category couldn't expand; cache key mismatch without MCP names
- See `.agents/tester/RESULTS/2026-05-20-mcp-tools-visible-to-llm.md` for full report

### MCP Tools Visible to LLM Fix Status: ✅ READY (all tests pass, E2E verified, no regressions)

### MCP Stdio Connection Root Cause Fix (2026-05-20)
- **Branch**: fix/mcp-stdio-connection-init (commits 3981088, 9ef15d7)
- **MCP Unit Tests**: 147/147 PASSED (warmup_pool 40 + connection_manager 19 + mcp_service 25 + runtime 16 + context7 25 + tool_filter 22)
- **E2E Startup**: ✅ dev.sh runs 30s, both context7 & webfetch warm up successfully (1/1 connections)
- **Regression Check**: 4,162 passed, 20 pre-existing failures (unrelated)
- **Root Cause**: ClientSession created without entering async context manager → receive loop never ran
- **Fix**: ManagedClientSession with explicit start()/stop() lifecycle
- **Quick Fixes**: 1 (conftest.py is_mcp_tool mock fix, commit 07beba7)
- See `.agents/tester/RESULTS/2026-05-20-mcp-stdio-connection-init.md` for full report

### MCP Stdio Connection Fix Status: ✅ READY (147 tests pass, daemon verified, no regressions)

### MCP Warmup Pool — Logging + Retry Logic (2026-05-20)
- **Branch**: fix/mcp-warmup-pool-logging-logic (commit 9d42d41, test commit 78b0392)
- **Existing MCP Tests**: 84/84 PASSED (0 regressions)
- **New Retry Tests**: 8/8 PASSED (retry succeeds 2nd/3rd, exhausted, backoff, timeout, CancelledError, log levels)
- **Restart Verification**: ✅ dev.sh runs 30s, correct log levels (INFO/WARNING/ERROR), retry visible in logs
- **Log Fix Verified**: Failed warmup now shows `ERROR - Failed to warm up pool for 'X' (0/1 connections created)`
- **Minor Note**: "connections" vs "connections created" inconsistency across log messages (cosmetic)
- **Quick Fixes**: 2 (asyncio.sleep recursion in test mocks)
- See `.agents/tester/RESULTS/2026-05-20-mcp-warmup-pool-logging-retry.md` for full report

### MCP Warmup Pool Logging+Retry Status: ✅ READY (84 tests pass, daemon verified, retry working)

### Critical Experience Feature — Phase 5 (2026-05-20)
- **New CE Tests**: 82/82 PASSED (36 tool + 14 injection + 20 schema + 13 API)
- **Tool Logic**: Add (10 tests), Merge (10 tests), Eviction (6 tests), List (3 tests), Remove (5 tests), Constants (2 tests)
- **Injection**: format_project_context with priority icons 🔴🟡🟢⚪, deduplication, non-dict skip
- **Schema**: CriticalExperience model validation, Project.to_dict(), migration file
- **API**: GET /projects/{id} and GET /projects include critical_experience
- **Full Suite**: 2,867 passed, 0 regressions
- **ensure.md**: ✅ dev.sh runs 30s without crash, migration 20260520_000001 applied
- **Quick Fixes**: 1 (API tests commit `77aa78f`)
- See `.agents/tester/RESULTS/2026-05-20-critical-experience-phase5.md` for full report

### Critical Experience Phase 5 Status: ✅ READY (82 tests pass, 0 regressions, daemon clean startup)

### MCP STDIO Server Warm-Up Pool (2026-05-19)
- **Full Test Suite**: 4,036 passed, 0 new regressions, 27 skipped
- **New Pool Tests**: 78/78 PASSED (24 warmup_pool + 19 connection_manager + 35 mcp_service)
- **Existing MCP Tests**: 147/147 PASSED (63 CRUD + 16 runtime + 43 builtin + 25 context7)
- **Daemon Boot**: ✅ Runs 30s without crash (ensure.md PASS)
- **Pool Warm-Up**: ✅ "MCP warm-up pool initialized: 2 server(s) registered, warmup running in background"
- **Resource Leaks**: ✅ 0 orphaned processes after shutdown
- **Quick Fixes**: 3 (MCP SDK mocks in conftest, mock tool patches, config fixture fix)
- See `.agents/tester/RESULTS/2026-05-19-mcp-server-pool.md` for full report

### MCP Server Warm-Up Pool Status: ✅ READY (78 pool tests pass, daemon verified, no regressions)

### Unified Memory Architecture — All Phases (2026-05-19)
- **Full Test Suite**: 3,982 passed, 0 real failures, 27 skipped (17 ordering issues that pass individually)
- **New Feature Tests**: 253/253 PASSED (85 redirect + 48 compound + 42 compaction + 29 archive + 49 memory system)
- **Edge Case Tests**: 48/48 PASSED (path traversal, symlinks, rate limiting, dedup, collision, concurrent writes, unicode)
- **Regression**: 0 regressions (all existing tests continue to pass)
- **Daemon Boot**: ✅ Runs 30s without crash (ensure.md PASS)
- **Live Integration**: ✅ inner_soul calls verified in running daemon (remember, compound, workflow, soul)
- **Quick Fixes**: 0 (no bugs found in implementation)
- See `.agents/tester/RESULTS/2026-05-19-unified-memory-architecture.md` for full report

### Unified Memory Architecture Status: ✅ READY (301 feature tests pass, daemon verified, all 6 phases complete)

### Built-in MCP Servers — All Phases (2026-05-18)
- **Backend Tests**: 3,737/3,752 PASSED (15 failures pre-existing, unrelated; 34 skipped)
- **Frontend Build**: ✅ npm run build succeeds
- **Frontend Tests**: 518/518 PASSED (146 MCP-specific)
- **MCP-Specific Unit Tests**: 152/152 PASSED (builtin framework + webfetch + CRUD)
- **API Integration**: 9/9 PASSED (daemon startup, all endpoints, 403 protection, boolean roundtrip)
- **Key Correctness**: 8/8 VERIFIED (boolean roundtrip, parse_config, proxy validation, null handling, 403, double-submit)
- **Daemon Startup**: ✅ Runs 30s without crash, "Bootstrapping 1 built-in MCP servers..."
- **Quick Fixes**: 1 (migration schema fix `8a41ca7`); 1 test script created (`0515596`)
- See `.agents/tester/RESULTS/2026-05-18-builtin-mcp-servers-complete.md` for full report

### Built-in MCP Servers Status: ✅ READY (All 3 phases tested, 3,737 tests pass, daemon clean startup)

### MCP Runtime Integration (2026-05-17)
- **MCP Tests**: 161/161 PASSED (145 existing + 16 new runtime integration tests)
- **Unit Tests**: 2,362/2,362 PASSED (0 failures after fixes)
- **ensure.md**: ✅ dev.sh runs 30s without crash
- **Quick Fixes**: 5 issues fixed (spawn_instance mocks, project_id attributes, migration naming, runtime import, mapper update)
- **Commits**: `d195b5a` (test fixes + new integration tests)
- See `.agents/tester/RESULTS/2026-05-17-mcp-runtime-integration.md` for full report

### MCP Runtime Integration Status: ✅ READY (161 MCP tests, all passing)

### MCP Server CRUD Feature (2026-05-16)
- **Backend Tests**: 55/55 PASSED (models, schemas, repository, router, integration)
- **Frontend Tests**: 134/134 PASSED (service, list component, dialog component)
- **ensure.md**: ✅ dev.sh runs 30s without crash
- **Quick Fix**: Exported missing `create_mcp_server_repository` from `daemon/repositories/__init__.py` (commit `60390b4`)
- **Commits**: `3b9723a` (backend tests), `6af7750` (frontend tests), `60390b4` (fix)
- See `.agents/tester/RESULTS/2026-05-16-mcp-server-crud-tests.md` for full report

### MCP Server CRUD Status: ✅ READY (189 tests, all passing)

### Pause TTL + Cold Resume — E2E (2026-05-16)
- **E2E Tests**: 9/9 PASSED (daemon restart, DB checks, API calls)
- **Pause**: ✅ `paused_at` correctly set in SQLite DB
- **Cold Resume**: ✅ Graph rebuilt from checkpoint after daemon restart (new PID confirmed)
- **Status Transitions**: ✅ paused → running → completed (not stuck at paused)
- **paused_at Clearing**: ✅ Set to NULL after resume
- **Test script**: `test/packs/pause_ttl_cold_resume_e2e_test.py`
- See `.agents/tester/RESULTS/2026-05-16-pause-ttl-cold-resume-e2e.md` for full report

### Pause TTL + Cold Resume Status: ✅ READY (cold resume works correctly)

### SSE Stop Button Fix — E2E Browser Automation (2026-05-15)
- **E2E Tests**: 6/6 PASSED (Playwright, browser automation with timing measurements)
- **Stop Button Fix**: ✅ WORKING — appears within ~100ms of SSE status_change event
- **Direct Navigation Fix**: ✅ 114ms (was completely broken before)
- **Root cause**: `@Input()` decorator creates plain property, not signal — computed couldn't track it
- **Fix applied**: Convert `@Input() instanceStatus` to `readonly instanceStatus = input<InstanceStatus | null>(null)` in MessageInputComponent
- **Additional fix**: Add fetched instance to `instanceService.instances()` on direct navigation
- **Commits**: `751dd43` (signal fix), `0ed06e5` (direct nav fix), `2d0e277` (E2E rewrite)
- **dev.sh**: ✅ PASS (30s no crash)
- See `.agents/tester/RESULTS/2026-05-15-sse-stop-button-fix-e2e.md` for full report

### SSE Stop Button Fix Status: ✅ READY (Stop button appears in 114ms on direct navigation)

### SSE Real-Time Status Updates E2E (2026-05-15 — SUPERSEDED)
- **E2E Tests**: 7/7 PASSED (Playwright, timing-measurement tests)
- **Critical finding**: Backend SSE events emitted correctly (7ms latency) but **Stop button never appears in UI**
- **Root cause**: Frontend `ChatComponent.currentInstance` computed doesn't propagate SSE status changes
- **SSE streaming**: ✅ No regression (3 messages visible)
- **Fix needed**: Frontend architecture change (NOT quick-fixable)
- **Commit**: `9250a52`
- **Test file**: `frontend/e2e/send-stop-button.spec.ts` (rewritten for SSE timing)
- See `.agents/tester/RESULTS/2026-05-15-sse-realtime-status-e2e.md` for full report

### SSE Real-Time Status Status: ✅ FIXED (see SSE Stop Button Fix above)

### Send/Stop Button UX Fix — Instance-Status-Based (2026-05-15)
- **E2E Tests**: 4/6 PASSED (2 PARTIAL due to 10s polling interval timing)
- **Unit Tests**: 28 passed, 0 failed (all `isInstanceRunning` statuses tested)
- **Behavior verified**: Idle → Send button, Running → Stop button (correct toggle)
- **Known limitation**: If LLM responds within 10s polling cycle, Stop button may not appear in UI
- **Bug found & fixed**: Accidentally removed properties restored (MAX_IMAGES, color, etc.)
- **Commits**: `8e25a22` (e2e rewrite), `781a5c2` (fix)
- **Test file**: `frontend/e2e/send-stop-button.spec.ts`
- See `.agents/tester/RESULTS/2026-05-15-send-stop-button-ux-fix.md` for full report

### Send/Stop Button UX Fix Status: ✅ READY (core behavior verified, timing limitation documented)

### Send/Stop Button Toggle E2E (OLD — SSE-based, pre-fix)
- **7 E2E tests passed**, 0 failed (Playwright browser automation)
- **Critical discovery**: `isStreaming` signal means "SSE connected", NOT "actively streaming response"
- **Behavior documented**: Stop button shows on page load (SSE connects immediately), stays after clicking stop
- **Send button only appears when SSE disconnects** (error, navigation away, manual disconnect)
- **Visual checks passed**: Stop button has proper square icon, correct dimensions, red color
- **Angular probe technique**: Used `window.ng.getComponent()` to manually disconnect SSE in tests
- **Test file**: `frontend/e2e/send-stop-button.spec.ts` (now superseded by instance-status-based version)
- See `.agents/tester/RESULTS/2026-05-15-send-stop-button-toggle.md` for full report

### Send/Stop Button Toggle Status: ✅ READY (behavior documented, UX concern noted)

### Stop Instance with Child Cascade (main branch)
- **901 tests passed**, 0 failed (8 skipped)
- **14 stop-cascade tests** — ALL PASS (11 unit + 2 API + 1 delegation)
- **Mock accuracy verified** — All test mocks match real service/repository interfaces
- **Integration testing** — Daemon spun up, API endpoint tested end-to-end:
  - Stop parent with children → all cascade to idle ✅
  - Stop non-existent instance → 404 ✅
  - Already-idle → graceful no-op ✅
  - Response format correct ✅
- **Edge cases verified in real code**: circular refs, exceptions during child stop, depth limit, resumability
- **dev.sh validated** — runs for 30 seconds without crash ✅
- **0 quick fixes needed** — Clean implementation
- **Minor findings** (non-blocking): mutual circular ref not tested, no try/except around update_status in real code
- See `.agents/tester/RESULTS/2026-05-15-stop-instance-cascade.md` for full report

### Stop Instance with Child Cascade Status: ✅ READY

### KB Tools Conditional Disabling (branch feature/kb-disable-when-no-lightrag)
- **1,029 tests passed**, 0 failed (2 pre-existing unrelated failures)
- **110 new feature tests** (61 loader + 49 knowledge tools) — ALL PASS
- **~15 gap coverage tests added** — tool list verification, cache toggle, H1 stripping, edge cases
- **6/6 test scenarios validated**: Tool availability, Prompt assembly, Cache behavior, Per-agent files, Edge cases, Backward compat
- **dev.sh validated** — runs for 30 seconds without crash ✅
- **1 quick fix applied** — test assertion fix for H1 stripping test (commit e4a2fbd)
- **Minor finding**: Whitespace-only LIGHTRAG_HOST treated as enabled (documented, not blocking)
- See `.agents/tester/RESULTS/2026-05-07-kb-disable-when-no-lightrag.md` for full report

### KB Tools Conditional Disabling Status: ✅ READY

### Reasoning Content Fallback Bug Fixes (branch fix/reasoning-content-bugs)
- **21 reasoning tests passed** (8 roundtrip + 6 edge cases + 7 fallback) — ALL PASS
- **7 new tests** covering all 4 bug fixes: fallback chain, empty string preservation, streaming reasoning key, logging safety
- **0 regressions** in existing tests
- **dev.sh validated** — runs for 30 seconds without crash ✅
- **0 quick fixes needed** — Clean implementation
- See `.agents/tester/RESULTS/2026-05-05-reasoning-content-fallback.md` for full report

### Reasoning Content Fallback Bug Fixes Status: ✅ READY

### RAG Tools 5 Bug Fixes (branch fix/rag-tools-5-bugs)
- **93 RAG tests passed** (43 client + 25 workspace scoping + 25 tools) — ALL PASS
- **5/5 bugs validated**: updated_name forwarding, rag_get_entity tool, docs fixes, delete endpoint
- **1000+ full suite tests passed** (3 pre-existing failures unrelated to RAG)
- **dev.sh validated** — runs for 30 seconds without crash ✅
- **1 quick fix applied** — added rag_get_entity unit tests + fixed tool count 15→16 (commit 98ce3cb)
- See `.agents/tester/RESULTS/2026-05-04-rag-tools-5-bug-fixes.md` for full report

### RAG Tools 5 Bug Fixes Status: ✅ READY

### RAG Search Workspace Mismatch Fix
- **68 RAG tests passed** (43 client + 25 workspace scoping) — ALL PASS, includes 2 new header behavior tests
- **9 integration checks passed** — workspace defaults, header behavior, request overrides
- **4 edge case checks passed** — whitespace-only, tabs, leading/trailing spaces
- **3306/3308 full suite tests passed** (2 pre-existing failures in test_invoked_as_tool.py, unrelated)
- **dev.sh validated** — runs for 30 seconds without crash ✅
- **1 quick fix applied** — strip whitespace from workspace param in _request() (commit fe1e826)
- See `.agents/tester/RESULTS/2026-05-04-rag-workspace-mismatch-fix.md` for full report

### RAG Search Workspace Mismatch Status: ✅ READY

### Reasoning Content Passback Fix
- **14 reasoning tests passed** (8 roundtrip + 6 edge cases) — ALL PASS
- **520+ full unit tests passed**, 1 pre-existing failure (unrelated: jober watch)
- **0 regressions** in existing tests
- **dev.sh validated** — runs for 30 seconds without crash ✅
- **Known gap documented**: `additional_kwargs["reasoning"]` alternate key not injected (low risk)
- **0 quick fixes needed** — Clean fix, no issues
- See `.agents/tester/RESULTS/2026-05-04-reasoning-content-passback.md` for full report

### Reasoning Content Passback Status: ✅ READY

### LightRAG Workspace Scoping (refactor: use project name for LIGHTRAG-WORKSPACE)
- **117 RAG tests passed** (workspace scoping 24 + rag_tools 23 + rag_client 42 + completion_registry 28)
- **24 new tests** — ALL PASS (sanitize_workspace, get_project_workspace, edge cases, integration)
- **0 regressions** in existing RAG tests
- **dev.sh validated** — runs for 30 seconds without crash ✅
- **0 quick fixes needed** — Clean refactor, no issues
- See `.agents/tester/RESULTS/2026-05-02-rag-workspace-scoping.md` for full report

### LightRAG Workspace Scoping Status: ✅ READY

### Instance Title Generation Trigger Fix (branch fix/instance-list-title)
- **117 existing tests passed** — no regressions (2 pre-existing failures in knowledge_tools async mocking, unrelated)
- **13 new tests** — ALL PASS (trigger method, 3 completion paths, non-blocking, idempotency, edge cases, fire-and-forget)
- **dev.sh validated** — runs for 30 seconds without crash ✅
- **0 quick fixes needed** — Clean fix, no issues
- See `.agents/tester/RESULTS/2026-05-01-title-generation-trigger.md` for full report

### Title Generation Trigger Status: ✅ READY

### Experiencer Fire-and-Forget Feature
- **47 knowledge_tools tests passed**, 0 failed — ALL PASS
- **991 job_queue + 47 API tests** — no regressions
- **5/5 verification points passed** — fire-and-forget, queue routing, edge cases, idempotency keys
- **dev.sh validated** — runs for 30 seconds without crash ✅
- **0 quick fixes needed** — Clean feature, no issues
- See `.agents/tester/RESULTS/2026-04-29-experiencer-kb-queue.md` for full report

### Experiencer Fire-and-Forget Status: ✅ READY

### KB-FIFO Queue Feature
- **1,418 tests passed**, 0 failed, 27 skipped — ALL PASS (no regressions)
- **3 test packs**: job_queue (991), core (624), api (193) — all pass
- **New system_kb_fifo_queue** — auto-provisioning, reserved name, KB job routing, FIFO properties verified
- **Quick fix**: Pre-existing API test modernization (commit 3326259)
- **dev.sh validated** — runs for 30 seconds without crash ✅
- See `.agents/tester/RESULTS/2026-04-29-kb-fifo-queue.md` for full report

### KB-FIFO Queue Status: ✅ READY

### Per-Agent LLM Model Override Feature
- **3,205 tests passed**, 0 failed, 27 skipped — ALL PASS (no regressions)
- **9 new tests** — Registry llm_model parsing (3), _build_llm_config (4), spawn_instance integration (2)
- **dev.sh validated** — runs for 30 seconds without crash ✅
- **0 quick fixes needed** — Clean feature, no issues
- See `.agents/tester/RESULTS/2026-04-27-agent-llm-model-override.md` for full report

### Per-Agent LLM Model Override Status: ✅ READY

### RAG Knowledge Toolset Feature
- **3,097 tests passed**, 0 failed, 176 skipped — ALL PASS
- **177 RAG-specific tests** — CompletionRegistry, RAG client, 15 RAG tools, knowledge tools, inner_soul redirect
- **Agent definitions verified** — Explorer (rag+filesystem), Experiencer (rag), all others (knowledge) ✅
- **dev.sh validated** — runs for 30 seconds without crash ✅
- **0 quick fixes needed** — Clean feature, no issues
- See `.agents/tester/RESULTS/2026-04-26-rag-knowledge-toolset.md` for full report

### RAG Feature Status: ✅ READY

### Phase 3: Jober Agent Watch System Integration & Testing
- **38 Phase 3 tests** — ALL PASS (0 failed)
- **986 job_queue tests** — ALL PASS (19 skipped, 0 failed) — no regressions
- **120 tools/registry/loader tests** — ALL PASS
- **2 benign bugs found** — duplicate `add_watch()` calls in `watch_job` and `watch_jobs` tools
- **dev.sh validated** — runs for 30 seconds without crash
- See `.agents/tester/RESULTS/2026-04-24-phase3-jober-watch-integration.md` for full report

### Phase 3 Status: ✅ READY

### Code Quality Refactoring Phase 5 — Jobs Router Cleanup & Lock Deduplication
- **2,185 backend tests** — ALL PASS (27 skipped, 0 failed) — no regressions
- **73 new Phase 4 tests** — ALL PASS (facade delegation, module-level functions, inner classes, service DI, fuzzy matching, cancellation service, title generation, circular imports)
- **278 frontend tests** — ALL PASS (0 failed)
- **dev.sh validated** — Server runs cleanly for 30 seconds with decomposed manager
- **2 minor test fixes** — test attribute check approach + AsyncMessageResult field
- See `.agents/tester/RESULTS/2026-04-23-phase4-manager-decomposition.md` for full report

### Phase 4 Status: ✅ READY

### Code Quality Refactoring Phase 5 — Jobs Router Cleanup & Lock Deduplication
- **2,327 backend tests** — ALL PASS (27 skipped, 0 failed) — no regressions
- **34 new Phase 5 tests** — ALL PASS (route registration, _release_job_lock scenarios, backward compat, service dependency, sub-router structure)
- **278 frontend tests** — ALL PASS (0 failed)
- **dev.sh validated** — Server runs cleanly for 30 seconds
- **0 quick fixes needed** — Clean refactoring, no issues
- See `.agents/tester/RESULTS/2026-04-23-phase5-jobs-router-cleanup.md` for full report

### Phase 5 Status: ✅ READY

### Code Quality Refactoring Phase 3 — API Router Extraction
- **2,151 backend tests** — ALL PASS (27 skipped, 0 failed) — no regressions
- **47 new Phase 3 tests** — ALL PASS (route registration, app.state, backward compat, _validate_instance_mode, _get_manager DI, router structure, API size)
- **278 frontend tests** — ALL PASS (0 failed)
- **dev.sh validated** — Server runs cleanly for 30 seconds
- **Live API validation** — All 12 endpoint groups respond correctly
- **2 quick fix commits** — Missing Any import + test fixture updates for app.state migration
- See `.agents/tester/RESULTS/2026-04-23-phase3-api-router-extraction.md` for full report

### Phase 3 Status: ✅ READY
- **1,968 backend tests** — ALL PASS (19 skipped, 0 failed) — no regressions
- **30 new Phase 2 tests** — ALL PASS (backward compat, __all__, cross-module refs, instantiation, HealthResponse, Pydantic behavior)
- **dev.sh validated** — Server runs cleanly for 30 seconds
- **0 quick fixes needed** — Clean split, no issues
- See `.agents/tester/RESULTS/2026-04-23-phase2-models-split.md` for full report

### Phase 2 Status: ✅ READY

### Code Quality Refactoring Phase 1 — Constants & Utilities Foundation
- **1,359 backend tests** — ALL PASS (19 skipped, 0 failed) — no regressions
- **68 new Phase 1 tests** — ALL PASS (constants, utils, backward compat, HTTP helpers, service dependency)
- **dev.sh validated** — Server runs cleanly for 30 seconds
- **0 quick fixes needed** — Clean refactoring, no issues
- See `.agents/tester/RESULTS/2026-04-23-phase1-constants-utilities.md` for full report

### Phase 1 Status: ✅ READY

### Vision Frontend Phase 2 — Image Upload UI (commits f4a3a93 + 6bdae97)
- **278 frontend tests** — ALL PASS (0 failed)
- **2,074 backend tests** — ALL PASS (0 failed, 27 skipped) — no regressions
- **Angular build** — SUCCESS (no compilation errors)
- **Web automation** — PASS (6/7 full, 1 partial due to instance state, not UI bug)
  - Chat input renders ✅ | Attach button (📎) present ✅ | Textarea ✅
  - Drag-drop zone ✅ | Image preview thumbnails ✅ | Remove button ✅
- **2 backend quick fixes** — project_list assertion + FIFO order in pending queries
- **dev.sh validated** — Server runs cleanly for 30 seconds
- See `.agents/tester/RESULTS/2026-04-20-vision-frontend-phase2.md` for full report

### Vision Frontend Phase 2 Status: ✅ READY

### Backend Vision Pipeline Phase 1 (commits 8ec692c + 650eef5)
- **45 vision unit tests** — ALL PASS (37 original + 8 edge-case additions)
- **All test packs pass** — No regressions from vision changes
- **2 quick fixes applied** — test_api.py images=None assertion + stale test file references
- **Tool binding verified** — Tools work without vision model configured
- **Text-only backward compatibility** — No regression
- **dev.sh validated** — Server starts and runs cleanly for 30 seconds
- See `.agents/tester/RESULTS/2026-04-20-vision-backend-pipeline.md` for full report

### Backend Vision Pipeline Status: ✅ READY

### Internal Source Log Level Fix (commit 611ddcb)
- **12 new dispatcher tests** — Internal source log levels (dispatch_completed + dispatch_message paths) — ALL PASS
- **2515 total tests pass** (22 skipped, 0 failed) — no regressions
- **1 quick fix applied** — Updated version assertion in test_api.py ("0.1.0" → "0.1.1")
- **dev.sh validated** — Server starts and runs cleanly for 30 seconds
- See `.agents/tester/RESULTS/2026-04-19-internal-source-log-level.md` for full report

### Internal Source Log Level Fix Status: ✅ READY

### Job Soft Delete Feature (branch feature/job-soft-delete)
- **34 new BE tests** — Repository (13) + API (11) + Scheduler safety (8) + Integration (2) — ALL PASS
- **35 new FE tests** — Model (7) + Service (11) + Component (17) — ALL PASS
- **953 job_queue tests pass** (14 skipped, 0 failed) — no regressions
- **267 FE tests pass** (10 suites, 0 failed) — no regressions
- **2 quick fixes** — Updated test files for renamed `hard_delete` methods
- **dev.sh validated** — Server starts and runs cleanly for 30 seconds
- **CRITICAL**: All 9 execution-path methods verified to exclude soft-deleted jobs
- See `.agents/tester/RESULTS/2026-04-19-job-soft-delete.md` for full report

### Job Soft Delete Status: ✅ READY FOR MERGE

### Job Processor project_id injection (branch feature/job-autoinject-project-id)
- **8 new unit tests** — ALL PASS (project_id propagation, edge cases, no regressions)
- **865 job_queue tests pass** (14 skipped, 0 failed) — no regressions
- **ensure.md validated** — dev.sh ran clean for 30 seconds
- See `.agents/tester/RESULTS/2026-04-18-job-processor-project-id.md` for full report

### Job Processor project_id injection Status: ✅ READY FOR MERGE

### Merge access_memory into self (branch feature/merge-access-memory-self)
- **2407 non-integration tests pass** (0 failed, 22 skipped) — no regressions
- **Integration verification: 4/4 checks PASS** — self category has both tools, ToolFilter resolves correctly, startup validation works
- **dev.sh validated** — Server starts and runs cleanly for 30 seconds
- **1 pre-existing integration test failure** (test_instance_title_generation_e2e — unrelated to branch)
- See `.agents/tester/RESULTS/2026-04-18-merge-access-memory-self.md` for full report

### Merge access_memory into self Status: ✅ READY FOR MERGE

### Per-Agent Tool Control Feature (branch feature/per-agent-tools, commits 5de34b0, 10fd317)
- **35 new tool filter tests** — ALL PASS
- **2410 total tests pass** (0 failed, 22 skipped) — no regressions
- **Integration validation**: All imports, category counts, smoke tests PASS
- **Edge cases**: All 5 verified (backward compat, deny-wins, category expansion, _mother)
- **dev.sh validated** — Server starts and runs cleanly for 30 seconds
- See `.agents/tester/RESULTS/2026-04-19-per-agent-tool-control.md` for full report

### Per-Agent Tool Control Status: ✅ READY FOR MERGE

### DLQ Retry Feature (commits 4b2f5c2, 8decef9)
- **19 new backend tests** — Retry DEAD_LETTER job (9) + Bulk replay-all (10) — ALL PASS
- **16 new frontend tests** — DeadLetterItem model (7) + DLQ service methods (9) — ALL PASS
- **2362 total backend tests** (2340 passed, 22 skipped, 0 failed) — no regressions
- **232 total frontend tests** (10 suites, all pass) — no regressions
- **dev.sh validated** — Server starts and runs cleanly for 30 seconds
- See `.agents/tester/RESULTS/2026-04-18-dlq-retry-feature.md` for full report

### DLQ Retry Feature Status: ✅ READY FOR MERGE

### Child-Parent Source Propagation Fix (commit 21ad4e1)
- **7 new tests added** to `tests/test_progressive_dispatch.py` — all PASS
- **32 total progressive dispatch tests** — ALL PASS
- **704 existing tests pass** (125 sources + 579 core) — no regressions
- **1 quick fix applied**: Narrowed `startswith("internal_")` to exact match on `internal_report`/`internal_error_report` only
- **dev.sh validated** — Server starts and runs cleanly for 30 seconds
- See `.agents/tester/RESULTS/2026-04-17-child-parent-source-propagation.md` for full report

### Child-Parent Source Propagation Status: ✅ READY FOR MERGE

### Progressive Message Delivery — Initial (commit 388d64c)
- **17 new progressive dispatch tests** — ALL PASS (dispatcher routing, skip rules, dedup, cleanup, error handling, manager streaming)
- **704 existing tests pass** (125 sources + 579 core) — no regressions
- **1 quick fix applied**: Added try/except around adapter.send() in dispatch_message() for error resilience
- **dev.sh validated** — Server starts and runs cleanly for 30 seconds
- See `.agents/tester/RESULTS/2026-04-17-progressive-message-delivery.md` for full report

### Progressive Message Delivery Status: ✅ READY FOR MERGE

### feature/sse-message-unification branch (commit 7f39b28)
- **1787 tests pass** (22 skipped, 0 failed) excluding integration
- **16 new message_service unit tests** — MessageService, UnifiedMessage, ToolCallInfo
- **24 new mock tests** — SSE critical paths (emit, error isolation, duplicate prevention, edge cases)
- **197 frontend tests pass** — no regressions
- **dev.sh validated** — Server starts and runs cleanly for 30 seconds
- **4 quick fixes applied** (commit 7f39b28): async mock fixes, status count update
- See `.agents/tester/RESULTS/2026-04-12-sse-message-unification.md` for full report

### SSE Message Unification Status: ✅ READY FOR MERGE

### feature/worker-pool-followup branch (commit 3c396b8)
- **1789 tests pass** (22 skipped, 0 failed) excluding integration
- **13 notification tests pass × 3 runs** — flakiness check, all deterministic
- **5 new integration tests** — real Worker threads with threading.Event coordination
- **Spurious wakeup defense verified** — while loop + monotonic elapsed tracking works
- **Stop event check verified** — fast shutdown in wait_for_work()
- **ensure.sh validated** — Server starts and runs cleanly for 30 seconds
- **No regressions** from base branch
- See `.agents/tester/RESULTS/2026-04-11-worker-pool-followup.md` for full report

### Worker Pool Followup Status: ✅ READY FOR MERGE

### feature/worker-pool-optimization branch (previous)
- **1749 tests pass** (22 skipped, 0 failed) excluding integration
- **31 notification tests pass** — 8 original + 23 new edge case tests ALL pass
- **ensure.sh validated** — Server starts and runs cleanly for 30 seconds
- **Notification mechanism verified**: notify_work() → wait_for_work(), 3s safety-net timeout, metrics tracking
- **Edge cases verified**: rapid notifications, callback exceptions, shutdown, schedule_retry integration
- No regressions from base branch
- 1 quick fix applied: integration tests updated to use _event_bus API
- See `.agents/tester/RESULTS/2026-04-11-worker-pool-optimization.md` for full report

### Worker Pool Optimization Status: ✅ READY FOR MERGE (followup tested)

### Previous Results (feature/message-queue-redesign branch)
- **1704 tests pass** (22 skipped, 0 failed, 0 errors) excluding integration
- **290 message_queue_redesign tests pass** — Phase 1-6 redesign tests ALL pass
- **ensure.sh validated** — Server starts and runs cleanly for 30 seconds
- **Config loads correctly** — timeout=15.0min, retries=3, backoff=60s/3600s, grace=10s
- **All E2E critical paths verified**: timeout→retry→complete, max retries→permanent failure, exponential backoff
- No regressions, no quick fixes needed
- See `.agents/tester/RESULTS/2026-04-11-phase6-config-wiring-final.md` for full report

### Phase 6 Config & Wiring Status: ✅ READY — FEATURE COMPLETE

### Previous Results
- Phase 5: 1689 tests pass ✅ (22 skipped, 0 failed), 275 MQ tests pass
- Phase 4: 1623 tests pass ✅ (22 skipped, 0 failed), 132 MQ tests pass
- **34 new tests added** for Phase 4 (test_event_bus.py: DB-backed EventBus, cursor-based SSE)
- dev.sh validated and working (ensure.md: PASS)
- **Critical path gap**: Missing Last-Event-ID header/reconnection test (3/4 covered)
- See `.agents/tester/RESULTS/2026-04-09-phase4-sse-events-tests.md` for full report

### feature/job-queue-management branch (previous)
- **1492 tests pass** (22 skipped, 0 failed) excluding integration
- **402 job_queue tests pass** (14 skipped, 0 failed) — all Phase 1+2+3 tests pass
- **35 queue router API tests pass** — Phase 3 queue CRUD, IDOR, start/stop endpoints
- **197 frontend tests pass** (10 test suites) — including new queue service/model tests
- dev.sh validated and working (ensure.md: PASS)
- Review fix commit `98a6e7a` — all 7 fixes verified, no regressions, no test updates needed
- Integration tests have pre-existing failures (require OPENAI_API_KEY) — not Phase issues
- See `.agents/tester/RESULTS/2026-04-08-phase3-post-review-retest.md` for re-test details
- See `.agents/tester/RESULTS/2026-04-08-phase3-api-frontend-integration.md` for original Phase 3 details

## Frontend Tests (Angular 21)

- **Framework:** Jest with `jest-preset-angular`
- **Config:** `frontend/jest.config.js` + `frontend/setup-jest.ts`
- **Run:** `cd frontend && npx jest` (or `npm test`)
- **Execution time:** ~2.5s for all tests
- **Test helpers:** `frontend/src/app/testing/job-test-helpers.ts`

### Frontend Test Files
| File | Scope |
|------|-------|
| `frontend/src/app/models/job.model.spec.ts` | Job model types, helper functions (isTerminalStatus, getStatusColor, getPriorityColor) |
| `frontend/src/app/models/job-queue.model.spec.ts` | Queue model types, helper functions (getQueueStatusColor, getQueueTypeIcon, etc.) |
| `frontend/src/app/services/job.service.spec.ts` | HTTP calls (list, get, create, cancel, retry) |
| `frontend/src/app/services/job-sse.service.spec.ts` | SSE connection, events, reconnection |
| `frontend/src/app/services/queue.service.spec.ts` | Queue HTTP calls (list, create, get, update, delete, start, stop) |
| `frontend/src/app/pages/jobs/jobs.component.spec.ts` | Filters, job actions, drawer, project pause |
| `frontend/src/app/components/job-detail-drawer/job-detail-drawer.component.spec.ts` | Computed properties, template rendering |

## Current Focus
**Internal Source Log Level Fix — TESTING COMPLETE**

### Status: ✅ READY

**Latest:** 12 new tests pass (internal source log levels), 2515 total tests pass, dev.sh validated
**Key verified:** Internal sources (internal_*) → DEBUG, non-internal → ERROR, edge cases covered
**Commit:** `611ddcb`
**See RESULTS/2026-04-19-internal-source-log-level.md for full report**

### Previous Focus: Job Soft Delete Feature — TESTING COMPLETE

### Status: ✅ READY FOR MERGE

**Latest:** 34 BE tests pass (repository + API + scheduler safety), 35 FE tests pass (model + service + component), dev.sh validated
**Branch:** feature/job-soft-delete
**Commits:** `2cc8998` → `34cf89e` → `740efbf` → `4421c02` → `ae2b4f6` (implementation) + `9185a08` → `45b4bae` (tests)
**Key verified:** All 9 execution-path methods exclude deleted jobs, soft_delete() idempotent, API soft-deletes terminal / cancels active, restore works, scheduler never picks up deleted PENDING jobs
**See RESULTS/2026-04-19-job-soft-delete.md for full report**

### Previous Phase: Phase 2 — Task↔Job Feedback Loop — COMPLETE
**799 job_queue tests pass (14 skipped, 0 failed), 1138 core tests pass (8 skipped, 0 failed), dev.sh validated**

### Phase 6 Test File
- **test_timeout_retry_e2e.py** (10 tests): Config flow, timeout→retry→complete, max retries→permanent failure, exponential backoff, multiple timeouts→success, default config, env var overrides, stale recovery config threshold, real repo integration

### Phase 1-6 Test Files (13 test modules, 290 tests)
- **test_event_bus.py** (34 tests): Phase 4 — DB-backed EventBus, cursor-based SSE
- **test_event_repository.py** (18 tests): Event logging, message linking
- **test_message_flow.py** (23 tests): Phase 3 — enqueue_message_v2, completion checks, idempotency
- **test_stale_recovery_v2.py** (24 tests): Phase 5 — 5-step recovery protocol, graceful/force
- **test_stale_task_recovery.py** (19 tests): Phase 3 — Stale task detection and reset
- **test_task_repository.py** (25 tests): Phase 1-3 — Task CRUD, atomic claim, retry chain
- **test_task_retry_models.py** (28 tests): Phase 5 — Retry policy models, exponential backoff
- **test_task_retry_repository.py** (31 tests): Phase 5 — Retry scheduling, retry_scheduled guard
- **test_timeout_monitor.py** (18 tests): Phase 5 — Timeout detection, grace period
- **test_timeout_retry_e2e.py** (10 tests): Phase 6 — E2E config flow, timeout/retry chains
- **test_worker_pool.py** (13 tests): Phase 2 — Worker pool lifecycle
- **test_worker_timeout.py** (27 tests): Phase 5 — Worker timeout handling

**Branch:** feature/message-queue-redesign
- **Phase 6 (FINAL):** 1704 tests passed ✅ (290 in message_queue_redesign/, 10 new Phase 6 E2E tests)
- **Phase 5:** 1689 tests passed ✅ (275 in message_queue_redesign/)
- **Phase 4:** 1623 tests passed ✅ (132 in message_queue_redesign/, 34 new Phase 4 tests)
- **Phase 3:** 1581 tests passed ✅ (89 in message_queue_redesign/, 21 new tests)
