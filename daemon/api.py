"""FastAPI application factory for Ensemble Daemon.

This module contains the app factory, lifespan management, middleware,
and global error handlers. All API endpoints are in daemon/routers/.
"""

import warnings

# Suppress langchain Pydantic V1 compatibility warning on Python 3.14+
warnings.filterwarnings(
    "ignore",
    message="Core Pydantic V1 functionality isn't compatible with Python 3.14",
    category=UserWarning,
)

import time
import sys
import logging
from logging.handlers import RotatingFileHandler
import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from fastapi import FastAPI, Request, APIRouter
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import AsyncGenerator
from pathlib import Path

_LOG_LEVEL = os.environ.get("LOG_LEVEL", "info").upper()
_daemon_log_level = os.environ.get("LOG_LEVEL_DAEMON", "info").upper()
_root_log_level = getattr(logging, _LOG_LEVEL, logging.INFO)
_daemon_log_level = getattr(logging, _daemon_log_level, logging.INFO)

# Resolve log directory from env directly (no DaemonConfig import — avoids
# circular import, no dead config field).
_LOG_DIR = os.environ.get("DAEMON_LOG_DIR", "./data/logs")

# Shared format for all handlers.
_LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

# Stderr handler — format unchanged for backward compatibility.
_stderr_handler = logging.StreamHandler(sys.stderr)
_stderr_handler.setFormatter(logging.Formatter(
    fmt=_LOG_FORMAT,
    datefmt='%H:%M:%S',
))

# File handler — same format with full date. Wrapped in try/except so
# unwritable log dir does NOT crash the daemon (graceful degradation).
_file_handler = None
try:
    os.makedirs(_LOG_DIR, exist_ok=True)
    _file_handler = RotatingFileHandler(
        filename=os.path.join(_LOG_DIR, "ensemble.log"),
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    _file_handler.setFormatter(logging.Formatter(
        fmt=_LOG_FORMAT,
        datefmt='%H:%M:%S',
    ))
except OSError as exc:
    # Log to stderr (the only handler guaranteed to work) and continue.
    print(f"WARNING: Could not set up file logging in {_LOG_DIR}: {exc}", file=sys.stderr)

# Configure root logger by adding handlers explicitly (NOT basicConfig).
_root_logger = logging.getLogger()
_root_logger.setLevel(_root_log_level)
_root_logger.addHandler(_stderr_handler)
if _file_handler is not None:
    _root_logger.addHandler(_file_handler)

# Daemon logger hierarchy: respects LOG_LEVEL_DAEMON for our app logs
daemon_logger = logging.getLogger("daemon")
daemon_logger.setLevel(_daemon_log_level)

# Suppress uvicorn access logs by default (our SelectiveAccessLogMiddleware handles selective logging)
uvicorn_access = logging.getLogger("uvicorn.access")
uvicorn_access.setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# Import routers
from daemon.routers import (
    agents_router,
    database_router,
    instances_router,
    jobs_router,
    messages_router,
    mappings_router,
    schedules_router,
    sources_router,
    webhooks_router,
    work_router,
    projects_router,
    queues_router,
    skills_router,        # /api/skills (Phase 6: skill management REST API)
    dlq_router,
    mcp_servers_router,
    notifications_router,
    migration_router,
    settings_router,       # /api/settings (Phase 1: user language preference)
    skill_bank_router,        # /api/skill-bank (Skill Bank CRUD)
    blueprints_router,        # /api/projects/{project_id}/blueprints (Project Blueprints CRUD)
    recovery_router,          # /api/recovery (Phase 2: pause-report-recovery crash-recovery endpoint)
)
from daemon.routers.workspace import router as workspace_router

from daemon.mcp import (
    create_kb_mcp_server,
    get_kb_mcp_http_app,
    get_kb_mcp_session_manager,
    get_kb_mcp_sse_app,
    set_kb_mcp_manager,
)

# Re-export validate_agent_id from utils for backward compatibility
from daemon.utils import validate_agent_id as validate_agent_id  # noqa: F401

# Re-export send_message for backward compatibility (tests/unit/test_vision.py imports it)
from daemon.routers.messages import send_message as send_message  # noqa: F401

from daemon import __version__
from daemon.models import ErrorCodes, ErrorResponse, HealthResponse
from daemon.ensemble_config import EnsembleConfig
from daemon.services.live_event_hub import LiveEventHub
from daemon.services.notification_broadcaster import get_notification_broadcaster
from daemon.services.editor_utils import get_editor_preference
from daemon.constants import SSE_TIMEOUT_S, SSE_PING_INTERVAL, SSE_QUEUE_MAXSIZE
from daemon.repositories.instance.models import InstanceStatus
from daemon import constants

# Determine the base path (use working directory for production)
# PyInstaller runs from INSTALL_DIR where frontend/dist is expected
import sys
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent.parent
# Angular builds to frontend/dist/frontend/browser/
FRONTEND_DIST = BASE_DIR / "frontend" / "dist" / "frontend" / "browser"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan context manager.
    
    Handles startup initialization and shutdown cleanup for all services.
    """
    # Import services here to avoid circular imports
    from daemon.manager import InstanceManager
    from daemon.config import load_config
    from daemon.services.job_queue_service import JobQueueService
    from daemon.services.job_lock_manager import JobLockManager
    from daemon.services.job_processor import JobProcessor
    from daemon.services.job_feedback_observer import JobFeedbackObserver
    from daemon.services.job_queue_mgmt_service import JobQueueMgmtService
    from daemon.services.dead_letter_service import DeadLetterService
    from daemon.services.job_recovery_service import JobRecoveryService
    from daemon.services.dispatch_event_bus import DispatchEventBus
    from daemon.sources.credentials import CredentialManager
    from daemon.repositories.job_queue.queue_repository import JobQueueRepository
    from daemon.repositories.job_queue.lock_repository import LockRepository
    from daemon.repositories.job_queue.dead_letter_repository import DeadLetterRepository
    from daemon.repositories.instance.repository import SQLModelInstanceRepository
    from daemon.repositories import create_job_repository
    
    # Load ensemble.json FIRST (database backend selection).
    # This is the chicken-and-egg resolution: ensemble_config picks the DB engine
    # *before* config.yaml is consulted. The default data_dir matches the
    # default location of the SQLite databases (`./data/`).
    # Precedence: ENSEMBLE_DATA_DIR > DATA_DIR > ./data. Falling back to DATA_DIR
    # keeps dev.sh (which historically only set DATA_DIR) working without changes.
    data_dir = Path(
        os.environ.get("ENSEMBLE_DATA_DIR")
        or os.environ.get("DATA_DIR")
        or "./data"
    )
    ensemble_config = EnsembleConfig.load_or_create(data_dir)
    app.state.ensemble_config = ensemble_config
    # Stash the data_dir so routers (e.g. database switch) can persist
    # ensemble.json updates to the same location the lifespan loaded it
    # from, without re-deriving the precedence chain themselves.
    app.state.data_dir = data_dir

    # Load config first
    config = load_config()

    # Apply LLM-specific class-level config that must be set before any
    # ThinkingChatOpenAI instance is created. Mirrors what __main__.py does
    # for the `python -m daemon` entry point so that `uvicorn daemon.api:app`
    # works the same way.
    from daemon.graph import ThinkingChatOpenAI
    ThinkingChatOpenAI.reasoning_echo_disabled_models = list(
        config.llm.reasoning_echo_disabled_models or []
    )
    daemon_logger.info(
        f"[Config] reasoning_echo_disabled_models={ThinkingChatOpenAI.reasoning_echo_disabled_models} "
        f"(models matching these patterns will NOT echo reasoning_content; all others echo)"
    )

    # Run RAG auto-test to verify LightRAG connectivity
    # This gracefully disables RAG if it's misconfigured (wrong API key, connection refused, etc.)
    # If RAG_IS_REQUIRED is set, the service will exit with an error on test failure.
    from daemon.rag import RAGRequiredError, auto_test_rag
    try:
        await auto_test_rag()
    except RAGRequiredError as e:
        daemon_logger.error(str(e))
        raise SystemExit(1)

    # Initialize CredentialManager BEFORE InstanceManager (BLOCKER 1 for Phase 2
    # of Database Tool Category). InstanceManager needs the singleton to wire
    # it into tool factories via closure injection.
    credential_manager = CredentialManager()

    # Initialize InstanceManager
    manager = InstanceManager(
        config, ensemble_config, credential_manager=credential_manager
    )
    await manager.initialize()

    # Execution Gate: clear any leases left behind by a previous
    # process that died mid-execution. Done HERE (and awaited) so
    # the first ``gate.run`` after startup is guaranteed to see a
    # clean state. Doing it inside ``setup_worker_pool`` would
    # schedule-and-return-0 under the running event loop, leaving up
    # to a 5-minute window where a fresh ``gate.run`` could
    # contend against a stale lease from the previous process.
    cleared_leases = await manager._execution_gate.recover_stale_leases()
    if cleared_leases:
        daemon_logger.warning(
            f"[Startup] Cleared {cleared_leases} stale execution lease(s) "
            "from previous process"
        )

    # Set up worker pool for message processing
    manager.setup_worker_pool()
    start_time = time.time()

    # Initialize MigrationWorker (SQLite → PostgreSQL migration orchestrator).
    # Must be created AFTER manager.initialize() so the worker can read the
    # live engine dialect, ensemble_config, and write guard from the manager.
    # The router resolves the worker from app.state.migration_worker.
    from daemon.services.migration_worker import MigrationWorker
    app.state.migration_worker = MigrationWorker(manager)
    
    # Initialize JobQueueService with shared engine from manager
    # Set create_tables=True to ensure job_queue_items table is created
    job_repository = create_job_repository(engine=manager.engine, create_tables=True)
    
    # Create LockRepository for job lock persistence
    lock_repo = LockRepository(engine=manager.engine)
    job_lock_manager = JobLockManager(lock_repo=lock_repo)

    # JobLockManager: clear any job locks left behind by a previous
    # process that died mid-execution (C12). Companion to the
    # Execution Gate's recover_stale_leases() above — same purpose,
    # different table. Done HERE (awaited) so the first
    # ``acquire_queue_lock`` after startup is guaranteed to see a
    # clean state. Wrapped in try/except so a sweep failure (e.g. a
    # pre-existing DB state that doesn't match the C12 query) does
    # not crash startup — the alternative would leave the daemon
    # unable to recover from a previously-broken state.
    try:
        cleared_locks = await job_lock_manager.recover_stale_job_locks()
        if cleared_locks:
            daemon_logger.warning(
                f"[Startup] Cleared {cleared_locks} stale job lock(s) "
                "from previous process"
            )
    except Exception as sweep_err:  # noqa: BLE001
        daemon_logger.warning(
            f"[Startup] Job lock recovery sweep failed (non-fatal): "
            f"{type(sweep_err).__name__}: {sweep_err}"
        )

    # Create queue repository for job queue management
    queue_repo = JobQueueRepository(engine=manager.engine)

    # Create watcher repository for job lifecycle subscriptions
    from daemon.repositories.job_queue.watcher_models import JobWatcher
    from daemon.repositories.job_queue.watcher_repository import JobWatcherRepository
    JobWatcher.metadata.create_all(manager.engine)
    watcher_repo = JobWatcherRepository(engine=manager.engine)
    manager._watcher_repo = watcher_repo  # Store on manager for tools access

    # Phase 2 (Batch 2) of feature/virtual-job-management-surface:
    # construct the WorkResolverService now that the JobRepository is
    # available. The resolver is the single point that looks up a
    # work_id across the ``task`` and ``job_queue_items`` tables, and
    # is the data source for the kind-agnostic
    # ``daemon.services.work_notifier.notify_work_watchers`` helper.
    #
    # The resolver needs three repos that already exist on the manager:
    # the TaskRepository (``manager._task_repo``, set up in
    # ``setup_worker_pool``), the JobRepository (created above as
    # ``job_repository``), and the InstanceRepository (created during
    # ``manager.initialize()`` at ``manager._instance_repository``).
    from daemon.services.work_resolver import WorkResolverService
    work_resolver = WorkResolverService(
        task_repo=manager._task_repo,
        job_repo=job_repository,
        instance_repo=manager._instance_repository,
    )
    manager._work_resolver = work_resolver  # Stored on manager for tools access
    logger.info("WorkResolverService constructed and wired to InstanceManager")

    # Phase 2 (Batch 2) — late-wire the watcher_repo + work_resolver
    # into the worker pool / task processor / stale-task recovery
    # components that were constructed in ``manager.setup_worker_pool``
    # (line 198) BEFORE the resolver and JobRepository were available.
    # All three accept None at construction (deferred-wiring pattern)
    # and expose a setter that fans out to live workers, so this call
    # is safe whether the pool was started in between or not.
    if manager._worker_pool is not None:
        manager._worker_pool.set_work_resolver(work_resolver)
        manager._worker_pool.set_watcher_repo(watcher_repo)
    if manager._task_processor is not None:
        manager._task_processor.set_work_resolver(work_resolver)
        manager._task_processor.set_watcher_repo(watcher_repo)
    if manager._stale_recovery is not None:
        manager._stale_recovery.set_notification_deps(
            instance_manager=manager,
            work_resolver=work_resolver,
            watcher_repo=watcher_repo,
        )
    logger.info(
        "Phase 2 Batch 2: watcher_repo + work_resolver wired to "
        "TaskProcessor / WorkerPool / StaleTaskRecovery"
    )

    # Create job queue management service for auto-provisioning
    job_queue_mgmt_service = JobQueueMgmtService(
        queue_repo=queue_repo,
        job_repo=job_repository,
        task_repo=manager._task_repo,
    )
    
    # Create DispatchEventBus for event-driven job dispatch
    dispatch_event_bus = DispatchEventBus()
    dispatch_event_bus.set_event_loop(asyncio.get_running_loop())
    
    # Set DispatchEventBus on JobQueueMgmtService
    job_queue_mgmt_service._dispatch_bus = dispatch_event_bus
    
    # Initialize JobQueueService
    job_queue_service = JobQueueService(
        repository=job_repository,
        lock_manager=job_lock_manager,
        queue_repo=queue_repo,
        instance_manager=manager,
    )
    job_queue_service.set_event_loop(asyncio.get_running_loop())
    job_queue_service.set_config(config.job_system)
    job_queue_service.set_watcher_repo(watcher_repo)
    job_queue_service.set_dispatch_bus(dispatch_event_bus)
    # Phase 2 (Batch 2) — wire the WorkResolverService so
    # ``JobQueueService.notify_watchers`` delegates to
    # ``notify_work_watchers`` (kind-agnostic) instead of the legacy
    # JobItem-only fallback path. The resolver is constructed above
    # in this lifespan.
    job_queue_service.set_work_resolver(work_resolver)
    
    # Wire retry engine into JobQueueService
    from daemon.services.job_retry_engine import JobRetryEngine
    retry_engine = JobRetryEngine(
        job_repo=job_repository,
        queue_repo=queue_repo,
        dlq_service=None,  # Will be set after dead_letter_service is created
        config=config.job_system,
        # F12 fix (Phase 3, 2026-07-01): wire TaskRepository into the
        # retry engine so ``maybe_retry`` can cancel stale PENDING
        # tasks on the retried instance before re-admission. Without
        # this, the retry flow leaves a leftover PENDING retry child
        # alive and the new Task and stale PENDING Task can contest
        # the same LangGraph checkpoint.
        task_repo=getattr(manager, "_task_repo", None),
    )
    job_queue_service.set_retry_engine(retry_engine)
    
    # Initialize and start RetryScheduler (if enabled)
    retry_scheduler = None
    lock_dir = Path(config.persistence.db_path).parent
    if config.job_system.job_retry_scheduler_enabled:
        from daemon.services.retry_scheduler import RetryScheduler
        retry_scheduler = RetryScheduler(
            retry_engine=retry_engine,
            queue_service=job_queue_service,
            poll_interval=60.0,
            lock_dir=lock_dir,
            dispatch_bus=dispatch_event_bus,
        )
        await retry_scheduler.start()
        logger.info("RetryScheduler started")
    else:
        logger.info("RetryScheduler disabled (job_retry_scheduler_enabled is not set)")
    
    # Wire services into InstanceManager
    manager.set_job_queue_service(job_queue_service)
    manager.set_job_queue_mgmt_service(job_queue_mgmt_service)

    # Set KB MCP manager reference (FastMCP instance already created in create_app())
    set_kb_mcp_manager(manager)
    
    # Wire InstanceManager into JobQueueService
    job_queue_service.set_instance_manager(manager)
    job_queue_service.set_project_repo(manager._project_repository)
    
    # Run startup recovery for orphaned PROCESSING jobs
    instance_repo = SQLModelInstanceRepository(engine=manager.engine)
    job_recovery = JobRecoveryService(
        job_repository=job_repository,
        lock_repository=lock_repo,
        instance_repository=instance_repo,
        job_queue_service=job_queue_service,  # For notify_watchers on Path 7
        # Phase 3 (F5/F10) — wire the TaskRepository and
        # StaleTaskRecovery so ``reconcile_drift_states`` can detect
        # and repair the dual-table drift patterns (P1 stuck pending,
        # F10 zombie task). Both are constructed in
        # ``manager.setup_worker_pool`` which runs earlier in this
        # lifespan, so the refs are guaranteed available here.
        task_repository=getattr(manager, "_task_repo", None),
        stale_task_recovery=getattr(manager, "_stale_recovery", None),
    )
    recovery_stats = await job_recovery.recover_on_startup()
    logger.info(f"Job recovery: {recovery_stats}")

    # ─────────────────────────────────────────────────────────────
    # Phase 3 (defer-seam bugfix, F5/F10): start the periodic
    # dual-table drift reconciler. The reconciler runs on its own
    # asyncio task (NOT gated on MaintenanceService._is_idle) because
    # drift states appear *during* active work — exactly the case
    # the idle-gated maintenance loop skips. StaleTaskRecovery's
    # thread is plain ``threading.Thread`` (not asyncio), so we
    # cannot piggyback on its loop without a thread→asyncio bridge.
    # A dedicated ``asyncio.create_task`` is simpler and gives us
    # configurable interval (default 300s) + clean shutdown.
    # ─────────────────────────────────────────────────────────────
    drift_interval = config.services.drift_reconcile_interval_seconds
    min_pending_age = config.services.drift_reconcile_min_pending_age_seconds
    drift_reconciler_task = asyncio.create_task(
        _periodic_drift_reconcile_loop(
            job_recovery=job_recovery,
            interval_seconds=drift_interval,
            min_pending_age_seconds=min_pending_age,
        ),
        name="drift-reconciler",
    )
    app.state.drift_reconciler_task = drift_reconciler_task
    logger.info(
        f"Drift reconciler started: interval={drift_interval}s, "
        f"min_pending_age={min_pending_age}s"
    )

    # Reconcile terminal watches — notify watchers for jobs that already reached terminal state
    reconciled = await job_queue_service.reconcile_terminal_watches()
    if reconciled > 0:
        logger.info(f"Reconciled {reconciled} terminal job watches")
    
    # Initialize DeadLetterService and set on retry engine
    dlq_repository = DeadLetterRepository(engine=manager.engine)
    dead_letter_service = DeadLetterService(
        job_repository=job_repository,
        dlq_repository=dlq_repository,
        job_queue_service=job_queue_service,  # For watcher notifications
        loop=asyncio.get_running_loop(),        # For async bridge
    )
    retry_engine._dlq_service = dead_letter_service
    retry_engine._job_queue_service = job_queue_service  # Wire for Path 6 notifications
    retry_engine._loop = asyncio.get_running_loop()       # Wire event loop

    # Phase 4 (Job as Queue Proxy): wire the DLQ service into
    # JobQueueService so ``_finalize_terminal`` can route the
    # ``Decision.DEAD_LETTER`` path through ``move_to_dlq_standalone``.
    # Done after the retry engine so the dead_letter_service is fully
    # constructed first.
    job_queue_service.set_dlq_service(dead_letter_service)

    # Wire dead_letter_service into InstanceManager
    manager.set_dead_letter_service(dead_letter_service)
    
    # Initialize and start JobFeedbackObserver
    job_feedback_observer = JobFeedbackObserver(
        job_queue_service=job_queue_service,
        event_bus=manager._event_bus,
        job_repo=job_repository,
        lock_repo=lock_repo,
        project_repo=manager._project_repository,
        instance_manager=manager,
    )
    await job_feedback_observer.start()
    logger.info("JobFeedbackObserver started")

    # Initialize and start the DependencyBus. Runs after
    # JobFeedbackObserver is created and before JobProcessor.start() so
    # the bus is live when jobs flow. The bus is ALWAYS instantiated —
    # it is the SOLE completion authority (CM was removed in Phase 5).
    # See ``daemon/services/dependency_bus.py`` for the bus API.
    await init_dependency_bus(app, manager)

    # Bootstrap system default project (Phase 1 of system_default_project feature)
    # This ensures the system project exists and has its queues provisioned
    # before any other services start using it. Must run BEFORE JobProcessor.start().
    try:
        system_project_id = manager._project_repository.ensure_system_default_project()
        constants.SYSTEM_DEFAULT_PROJECT_ID = system_project_id
        await job_queue_mgmt_service.auto_provision_system_queues(system_project_id)
        # Backfill legacy instances whose project_id is NULL/empty. Such
        # rows (created before the spawn_instance normalisation fix) are
        # invisible to project-scoped gates like the defer-queue idle
        # check, so a paused non-deferred instance on the system default
        # project fails to hold back system_defer_queue. Idempotent
        # (no-op on a clean database); runs every startup.
        try:
            backfilled = await asyncio.to_thread(
                manager._instance_repository.backfill_system_default_project_id,
                system_project_id,
            )
            if backfilled:
                logger.info(
                    f"Backfilled {backfilled} instance(s) with system default "
                    f"project_id {system_project_id}"
                )
        except Exception as backfill_err:
            logger.warning(
                f"Failed to backfill system default project_id on instances: {backfill_err}"
            )
        # Backfill legacy job_queue_items whose project_id is NULL/empty.
        # The SQLite migration ``20260424_000001`` does this on SQLite,
        # but the migration runner is a NO-OP on PostgreSQL, so PG rows
        # keep a NULL project_id. Those rows are invisible to the Jobs
        # UI's project-scoped refresh (``GET /api/jobs?project_id=…``):
        # a paused job on the system default project disappears on
        # refresh even though it shows on initial (unfiltered) page
        # load. Idempotent; runs every startup on both dialects.
        try:
            jobs_backfilled = await asyncio.to_thread(
                job_repository.backfill_system_default_project_id,
                system_project_id,
            )
            if jobs_backfilled:
                logger.info(
                    f"Backfilled {jobs_backfilled} job_queue_items row(s) with "
                    f"system default project_id {system_project_id}"
                )
        except Exception as backfill_err:
            logger.warning(
                f"Failed to backfill system default project_id on jobs: {backfill_err}"
            )
        logger.info(f"System default project bootstrapped: {system_project_id}")
    except Exception as e:
        logger.warning(f"Failed to bootstrap system default project: {e}")
    
    # Initialize and start JobProcessor
    job_processor = JobProcessor(
        queue_service=job_queue_service,
        instance_manager=manager,
        project_repo=manager._project_repository,
        queue_repo=queue_repo,
        poll_interval=30.0,
        dispatch_bus=dispatch_event_bus,
        event_dispatch_enabled=config.job_system.event_dispatch_enabled,
    )
    await job_processor.start()
    logger.info("JobProcessor started")
    
    # C7: Wire JobFeedbackObserver into JobProcessor so MESSAGE-type jobs
    # route through the observer → Task → WorkerPool path. Both the observer
    # and the processor are already constructed; the observer is already
    # started (line 354) and the bus-driven lifecycle path (report Task
    # PROCESS_REPORT → ``_process_event`` → ``_finalize_job``) is wired
    # (Phase 1 — CM removed; the lifecycle event is the SOLE completion
    # authority for parents with children tracked by the bus).
    job_processor.setup_job_feedback_observer(job_feedback_observer)
    logger.info("JobFeedbackObserver wired into JobProcessor")

    # Wire the JobFeedbackObserver onto the InstanceManager facade so the
    # observer's ``_process_event`` lifecycle handler can finalize the
    # parent job after a report turn emits its ``instance_lifecycle``
    # event (i.e. ``_process_event`` → ``_finalize_job``). The bus
    # itself is a pure state machine that only transitions PENDING →
    # FIRED watchers; it does NOT call back into the observer
    # directly — the report Task (PROCESS_REPORT) is the bridge that
    # drives the parent graph turn and produces the lifecycle event.
    manager.set_job_feedback_observer(job_feedback_observer)
    logger.info("JobFeedbackObserver wired into InstanceManager (report_lane=ON)")
    
    # Initialize LiveEventHub for live-only SSE streaming
    app.state.live_hub = manager._live_hub
    
    # Initialize NotificationBroadcaster and wire into InstanceManager
    notification_broadcaster = get_notification_broadcaster()
    manager.set_notification_broadcaster(notification_broadcaster)
    
    # Auto-provision system queues for existing projects
    try:
        projects = await asyncio.to_thread(manager._project_repository.list_projects)
        for project in projects:
            await job_queue_mgmt_service.auto_provision_system_queues(project.project_id)
        logger.info(f"Auto-provisioned system queues for {len(projects)} projects")
    except Exception as e:
        logger.warning(f"Failed to auto-provision system queues: {e}")
    
    # Start message sources
    await manager.start_sources()
    
    # Store references on app.state for health check endpoint
    app.state.manager = manager
    app.state.start_time = start_time
    app.state.credential_manager = credential_manager
    app.state.job_queue_service = job_queue_service
    app.state.job_processor = job_processor
    app.state.job_queue_mgmt_service = job_queue_mgmt_service
    app.state.retry_scheduler = retry_scheduler
    app.state.dispatch_event_bus = dispatch_event_bus
    
    # Store job feedback observer for cleanup
    app.state._job_feedback_observer = job_feedback_observer

    # --- VS Code Server Manager (Phase 3: editor integration) ---
    # Construct AFTER manager.initialize() but BEFORE router DI so
    # the settings router can read it via app.state. Do NOT auto-start
    # code-server — lazy start happens via PUT /api/settings/editor.
    # Crash recovery: adopt a running code-server from a stale PID
    # file if the daemon crashed and restarted.
    from daemon.services.vscode_server_manager import VSCodeServerManager
    from daemon.routers.vscode_proxy import create_vscode_proxy_app

    vscode_manager = None
    try:
        vscode_manager = VSCodeServerManager(
            config=config.vscode,
            data_dir=str(data_dir),
            workdir=None,
        )
        await vscode_manager.attach_existing()
    except Exception as e:
        logger.warning(f"VS Code manager initialization failed: {e}")
        vscode_manager = None

    if vscode_manager is not None:
        app.state.vscode_manager = vscode_manager

        # W3: Phase 3 owns the mount — mount the proxy sub-app here.
        # C1: pass project_repo so the proxy can validate ?folder= queries
        # against known project workdirs (prevents arbitrary FS access).
        vscode_app = create_vscode_proxy_app(
            vscode_manager, project_repo=manager._project_repository
        )
        app.mount("/vscode", vscode_app)

        # app.mount() appends to the end of app.routes, but the
        # catch-all /{path:path} (registered in create_app()) would
        # shadow the mount — every GET /vscode/* would be consumed
        # by the SPA fallback before reaching the proxy. Move the
        # mount to just before the catch-all so routing works.
        _routes = app.router.routes
        _vscode_idx = next(
            (
                i
                for i, r in enumerate(_routes)
                if getattr(r, "path", None) == "/vscode"
            ),
            None,
        )
        _catchall_idx = next(
            (
                i
                for i, r in enumerate(_routes)
                if getattr(r, "path", None) == "/{path:path}"
            ),
            None,
        )
        if (
            _vscode_idx is not None
            and _catchall_idx is not None
            and _vscode_idx > _catchall_idx
        ):
            _mount = _routes.pop(_vscode_idx)
            _routes.insert(_catchall_idx, _mount)

        logger.info("VS Code proxy mounted at /vscode")

        # Auto-start code-server if the user's stored editor preference is
        # "vscode". Non-fatal: a failure here MUST NOT prevent the daemon
        # from booting. The helper swallows any exception and logs it.
        await _auto_start_vscode_if_preferred(manager, vscode_manager)

    # Store dead letter service for router injection
    from daemon.routers.dlq import set_dead_letter_service
    set_dead_letter_service(dead_letter_service)
    
    # Also set for jobs_crud.py which has its own dependency instance
    from daemon.routers.jobs_crud import get_dead_letter_svc
    get_dead_letter_svc.set_service(dead_letter_service)
    
    # Store DLQ repository for router injection
    from daemon.repositories.job_queue.dead_letter_repository import set_dead_letter_repository
    set_dead_letter_repository(dlq_repository)
    
    # Set JobQueueService for jobs router injection
    from daemon.routers.jobs import set_job_queue_service
    set_job_queue_service(job_queue_service)
    
    # Set project repository for projects router injection
    from daemon.routers.projects import set_project_repository
    set_project_repository(manager._project_repository)

    # Set project repository for settings router injection (Phase 1: user language preference)
    from daemon.routers.settings import set_project_repository as set_settings_project_repo
    set_settings_project_repo(manager._project_repository)

    # Also set project repository for queues router (has its own dependency injection)
    from daemon.routers.queues import set_project_repository as set_queues_project_repository
    set_queues_project_repository(manager._project_repository)

    # Set project repository for workspace router injection (Phase 1: workspace viewer)
    from daemon.routers.workspace import set_project_repository as set_workspace_project_repo
    set_workspace_project_repo(manager._project_repository)

    # Set JobQueueMgmtService for projects and queues routers injection
    from daemon.routers.projects import set_job_queue_mgmt_service as set_proj_mgmt_service
    set_proj_mgmt_service(job_queue_mgmt_service)
    from daemon.routers.queues import set_job_queue_mgmt_service as set_queue_mgmt_service
    set_queue_mgmt_service(job_queue_mgmt_service)

    # Set WorkResolverService for work router injection.
    # Phase 4 (2026-06-27) of feature/virtual-job-management-surface:
    # GET /api/work relies on the same WorkResolverService that the
    # watcher repo + worker pool + task processor + stale-task
    # recovery already consume (constructed earlier in this lifespan
    # block). The router follows the queues.py DI pattern (module-
    # level global + setter + Depends factory).
    from daemon.routers.work import set_work_resolver
    set_work_resolver(work_resolver)

    # Wire skills router services (Phase 6 Task 2 — REST API surface).
    # The skills router uses 4 service DI accessors (created via
    # ``create_service_dependency``) plus module-level globals
    # (trigger repo, job dispatcher, usage repo, lineage repo).
    # Both patterns are pulled from manager attributes that were
    # initialized earlier in this lifespan block — see
    # ``manager._skill_*`` wiring in daemon/manager.py.
    from daemon.routers.skills import (
        get_store,
        get_search,
        get_metrics,
        get_evolution,
        set_skill_trigger_repo,
        set_skill_job_dispatcher,
        set_skill_usage_repo,
    )

    # Phase 2 services — create_service_dependency accessors.
    # Each one returns a closure whose .set_service() setter is what
    # we call here. Defensive getattr on manager attributes in case
    # a future refactor skips one of the service constructions —
    # missing services will surface as 503s at request time rather
    # than crashing the lifespan.
    if getattr(manager, "_skill_store_service", None) is not None:
        get_store.set_service(manager._skill_store_service)
    if getattr(manager, "_skill_search_service", None) is not None:
        get_search.set_service(manager._skill_search_service)
    if getattr(manager, "_skill_metrics_service", None) is not None:
        get_metrics.set_service(manager._skill_metrics_service)
    if getattr(manager, "_skill_evolution_service", None) is not None:
        get_evolution.set_service(manager._skill_evolution_service)

    # Trigger repository — module-level global + setter (no factory).
    if getattr(manager, "_skill_trigger_repo", None) is not None:
        set_skill_trigger_repo(manager._skill_trigger_repo)

    # Job dispatcher — module-level global + setter. This is the
    # chokepoint the ``POST /api/skills/{id}/fix`` endpoint calls into
    # to enqueue a ``skill_evolution`` JobItem on ``system_parallel_queue``.
    if getattr(manager, "_skill_job_dispatcher", None) is not None:
        set_skill_job_dispatcher(manager._skill_job_dispatcher)

    # Usage repository — module-level global + setter. The
    # ``GET /api/skills/{id}/usage-records`` endpoint reads through
    # this repo for per-event usage history (vs. the metrics
    # service's aggregate view).
    if getattr(manager, "_skill_usage_repo", None) is not None:
        set_skill_usage_repo(manager._skill_usage_repo)

    logger.info(
        "Skills router wired: store=%s search=%s metrics=%s evolution=%s "
        "triggers=%s dispatcher=%s usage_repo=%s",
        manager._skill_store_service is not None,
        manager._skill_search_service is not None,
        manager._skill_metrics_service is not None,
        manager._skill_evolution_service is not None,
        manager._skill_trigger_repo is not None,
        manager._skill_job_dispatcher is not None,
        manager._skill_usage_repo is not None,
    )

    # Start StreamableHTTP session manager within lifespan
    session_mgr = get_kb_mcp_session_manager()

    async with session_mgr.run():
        yield
    
    # Shutdown sequence
    # Stop RetryScheduler first
    if retry_scheduler is not None:
        await retry_scheduler.stop()
    
    # Stop JobFeedbackObserver before processor
    if hasattr(app.state, '_job_feedback_observer'):
        await app.state._job_feedback_observer.stop()

    # Stop the DependencyBus. The bus is flag-agnostic (always wired),
    # so this stops it whenever it was started. Safe to call when the
    # bus was never started (the helper is a no-op in that case).
    await shutdown_dependency_bus(app)

    # Stop JobProcessor
    if hasattr(app.state, 'job_processor') and app.state.job_processor:
        await app.state.job_processor.stop()
    
    # Shutdown LiveEventHub
    if hasattr(app.state, 'live_hub'):
        await app.state.live_hub.shutdown()
    
    # Shutdown NotificationBroadcaster
    if notification_broadcaster is not None:
        await notification_broadcaster.shutdown()
    
    # Stop the periodic drift reconciler (Phase 3 F5/F10). The
    # task is fire-and-forget under the lifespan; cancel and await
    # so the event loop drains cleanly. Mirrors the cancel/await
    # pattern used by ``MaintenanceService.stop``.
    if hasattr(app.state, "drift_reconciler_task"):
        drift_task = app.state.drift_reconciler_task
        if drift_task is not None and not drift_task.done():
            drift_task.cancel()
            try:
                await drift_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning(f"Drift reconciler shutdown error: {e}")
        app.state.drift_reconciler_task = None

    # --- VS Code Server shutdown ---
    # Stop the code-server process BEFORE the manager shuts down
    # (process cleanup before DB teardown).
    if hasattr(app.state, "vscode_manager"):
        try:
            await app.state.vscode_manager.stop()
        except Exception as e:
            logger.warning(f"VS Code server shutdown failed: {e}")

    # Call manager shutdown for graceful shutdown
    await manager.shutdown()


async def _auto_start_vscode_if_preferred(
    manager: "InstanceManager",
    vscode_manager: "VSCodeServerManager",
) -> None:
    """Boot code-server at daemon startup if the user's editor preference is "vscode".

    Reads the editor preference from the system default project metadata KV
    via ``get_editor_preference``. If the stored value is ``"vscode"``, calls
    ``vscode_manager.ensure_running()`` (idempotent — no-op if already
    running) and logs the assigned port.

    Non-fatal contract: any exception raised here (binary missing, port
    timeout, DB error while reading the preference, etc.) is logged as a
    warning and swallowed so the daemon continues to boot. The user can
    still start code-server manually via ``PUT /api/settings/editor`` once
    the daemon is up.

    Args:
        manager: The live ``InstanceManager`` (provides ``_project_repository``
            for the editor-preference lookup).
        vscode_manager: The constructed ``VSCodeServerManager``. Must be
            non-None — the caller (``lifespan``) guards this with an
            ``if vscode_manager is not None`` check before invoking.
    """
    try:
        editor_pref = await get_editor_preference(manager._project_repository)
        if editor_pref == "vscode":
            logger.info(
                "Editor preference is 'vscode' — auto-starting code-server at boot"
            )
            await vscode_manager.ensure_running()
            logger.info(
                f"code-server auto-started on port {vscode_manager.get_port()}"
            )
        else:
            logger.debug(
                f"Editor preference is '{editor_pref}' — skipping code-server auto-start"
            )
    except Exception as autostart_err:
        logger.warning(
            f"VS Code auto-start failed (daemon will continue): {autostart_err}"
        )


async def _periodic_drift_reconcile_loop(
    job_recovery: "JobRecoveryService",
    interval_seconds: int,
    min_pending_age_seconds: int,
) -> None:
    """Periodic asyncio loop driving ``JobRecoveryService.reconcile_drift_states``.

    Runs every ``interval_seconds`` (default 300s) until cancelled. The
    initial tick fires after a small startup delay so the very first
    reconcile happens AFTER the system has had a chance to register
    active work — running it at t=0 would just produce empty results
    (no drift to find yet).

    Cancellation contract: ``asyncio.CancelledError`` is the
    shutdown signal. The ``except asyncio.CancelledError`` clause
    propagates it so the asyncio runtime knows the task is done.

    The loop body is wrapped in a broad ``try/except Exception`` so a
    one-cycle failure (e.g. DB connection blip) doesn't kill the
    periodic loop — drift correction is best-effort and a missed
    cycle is recovered on the next tick.

    Args:
        job_recovery: The JobRecoveryService whose
            ``reconcile_drift_states`` method is invoked.
        interval_seconds: Sleep between ticks. Configurable via
            ``SERVICES_DRIFT_RECONCILE_INTERVAL_SECONDS``.
        min_pending_age_seconds: Forwarded to
            ``reconcile_drift_states``. Configurable via
            ``SERVICES_DRIFT_RECONCILE_MIN_PENDING_AGE_SECONDS``.
    """
    # Small startup delay so the first tick fires after the system
    # has stabilized (mirrors MaintenanceService._loop's 60s initial
    # delay). Without this, the first tick would always be a no-op
    # because no drift has had time to accumulate.
    try:
        await asyncio.sleep(min(30, interval_seconds))
    except asyncio.CancelledError:
        return

    while True:
        try:
            stats = await job_recovery.reconcile_drift_states(
                min_pending_age_seconds=min_pending_age_seconds,
            )
            reconciled = stats.get("reconciled", 0)
            if reconciled > 0:
                logger.info(
                    f"Drift reconciler: applied {reconciled} corrections "
                    f"(details={len(stats.get('details', []))})"
                )
        except asyncio.CancelledError:
            # Shutdown — propagate so the runtime knows the task is done.
            raise
        except Exception as e:
            # Best-effort: a single failed cycle is not fatal. The
            # next tick will retry. Log at ERROR so the operator has
            # visibility.
            logger.error(
                f"Drift reconciler cycle failed: {e}",
                exc_info=True,
            )

        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            return


async def init_dependency_bus(app, manager) -> None:
    """Initialize and start the DependencyBus.

    Runs after JobFeedbackObserver is created and before
    JobProcessor.start(), so the bus is live when jobs flow. The bus
    is ALWAYS instantiated — it is the SOLE completion authority
    (CM was removed in Phase 5).

    If the bus fails to initialize, log the error and leave the singleton
    ``None``. Call sites must treat ``None`` as a hard error (no fallback).

    Args:
        app: The FastAPI app (used to stash the instance on ``app.state``
            for later shutdown).
        manager: The InstanceManager (provides the shared SQLAlchemy
            ``engine`` for the watcher repository).
    """
    from daemon.repositories.dependency_bus import DependencyWatcherRepository
    from daemon.services.dependency_bus import (
        DependencyBus,
        set_dependency_bus,
    )

    try:
        watcher_repo = DependencyWatcherRepository(engine=manager._engine)
        bus = DependencyBus(watcher_repo)
        recovered = await bus.start()
        # C1 fix: process FIRED-but-unsent FollowUps from a prior
        # crash. Each ``(watch_id, FollowUp)`` tuple corresponds to a
        # row whose child task terminated and whose finalization step
        # was either never executed (process died mid-emit) or executed
        # but never stamped (process died after the finalization
        # call, before ``mark_enqueued``). The dedup is the
        # ``enqueued_at IS NULL`` filter in
        # :meth:`DependencyBus._recover_fired_unsent` — a second crash
        # does not double-trigger because the stamp survives the
        # restart.
        #
        # Post-FollowUp-removal semantics: the bus no longer enqueues
        # messages. The recovery step is therefore a finalization
        # step, not a message-delivery step. For each unique target
        # in the recovered set, we ask the bus whether the target
        # still has any PENDING watchers. If the answer is 0 (all
        # watchers fired before the crash), the recovery decides:
        #   * If the instance has any live work
        #     (``has_instance_busy`` — PENDING + RUNNING + PAUSED),
        #     stamp the row and defer — the task's lifecycle event
        #     will drive ``_process_event`` → ``_finalize_job``
        #     naturally when its turn ends (PAUSED tasks resume
        #     and complete too, so they still count as
        #     "will drive").
        #   * Otherwise, invoke ``_finalize_job`` directly via the
        #     single finalize path with the bus error override, so
        #     the parent job transitions PROCESSING → COMPLETED/ERROR.
        # The ``_retriggered_targets`` set dedupes the per-target
        # invocation when multiple recovered rows share the same
        # parent (e.g. a parent with N completed children, all FIRED
        # in the previous process).
        #
        # We deliberately stamp ``enqueued_at`` AFTER a successful
        # finalization attempt so a transient finalize failure
        # (e.g. observer not yet wired) does NOT lock the row out
        # of a future recovery — it will be picked up on the next
        # restart.
        if recovered:
            _retriggered_targets: set[str] = set()
            for watch_id, fu in recovered:
                target_id = fu.target_instance_id

                # C4 fix (Phase 6): if the target instance is PAUSED, the
                # crash window straddled a pause transition — the
                # child's job is in PAUSED state (not PROCESSING), so
                # the heavy work below would either find no PROCESSING
                # job (→ stamp + drop, the bug) or succeed in
                # finalizing a paused job (wrongly completing it).
                # Preserve the watcher for resume — do NOT stamp
                # (``enqueued_at`` stays NULL, so a future restart
                # will re-pick it via ``bus.start()``).
                _instance_repo = getattr(manager, "_instance_repository", None)
                if _instance_repo is not None:
                    try:
                        _target_instance = await asyncio.to_thread(
                            _instance_repo.get, target_id
                        )
                        if (
                            _target_instance is not None
                            and _target_instance.status
                            == InstanceStatus.PAUSED.value
                        ):
                            logger.info(
                                f"bus crash recovery: target="
                                f"{target_id[:8]}... is PAUSED — "
                                f"preserving watcher "
                                f"{watch_id[:8]}... for resume"
                            )
                            continue
                    except Exception as paused_check_err:
                        # Don't fail recovery on a transient lookup
                        # error — fall through to the normal path.
                        logger.warning(
                            f"bus crash recovery: PAUSED check "
                            f"failed target={target_id[:8]}...: "
                            f"{paused_check_err}"
                        )

                if target_id in _retriggered_targets:
                    # Already retried this target in this recovery
                    # pass; just stamp and move on.
                    try:
                        await bus.mark_enqueued(watch_id)
                    except Exception as stamp_err:
                        logger.warning(
                            f"bus crash recovery: stamp (dedup) "
                            f"failed watch_id={watch_id[:8]}...: "
                            f"{stamp_err}"
                        )
                    continue
                _retriggered_targets.add(target_id)
                try:
                    remaining = await bus.count_pending_for_target(target_id)
                except Exception as count_err:
                    logger.warning(
                        f"bus crash recovery: pending count failed "
                        f"target={target_id[:8]}...: {count_err}"
                    )
                    # Cannot decide — stamp the row to avoid
                    # endless recovery churn. The next child
                    # completion will retrigger if applicable.
                    try:
                        await bus.mark_enqueued(watch_id)
                    except Exception as stamp_err:
                        logger.warning(
                            f"bus crash recovery: stamp (post-fail) "
                            f"failed watch_id={watch_id[:8]}...: "
                            f"{stamp_err}"
                        )
                    continue
                if remaining > 0:
                    # Other PENDING watchers exist — the bus path
                    # is still in flight. Stamp the recovered row
                    # (it was successfully fired in the previous
                    # process; the dedup is the stamp) and skip
                    # finalization. The next terminal event on
                    # the remaining source task will retrigger
                    # the finalization via the normal path in
                    # ``_emit_terminal_via_bus``.
                    try:
                        await bus.mark_enqueued(watch_id)
                    except Exception as stamp_err:
                        logger.warning(
                            f"bus crash recovery: stamp "
                            f"(pending-remain) failed "
                            f"watch_id={watch_id[:8]}...: {stamp_err}"
                        )
                    logger.info(
                        f"bus crash recovery: target="
                        f"{target_id[:8]}... still has "
                        f"{remaining} PENDING watchers — "
                        f"skipping retrigger, stamping row"
                    )
                    continue
                # No PENDING watchers — this is the LAST watcher
                # for the target. The previous process fired it
                # but didn't get to retrigger finalization
                # (crash window). Trigger now and stamp the row.
                #
                # Phase 1 (2026-06-24, report-lane decoupling):
                # the deleted ``ChildReportsService._retrigger_parent_finalize``
                # direct-finalize path is replaced with a
                # **decision**: defer to the natural path when a
                # report Task is in flight (the task's lifecycle
                # event will finalize the parent itself when its
                # turn ends), finalize directly via the single
                # ``_finalize_job`` path only when nothing else
                # will (the genuine stuck-parent case).
                #
                # **Why the ``has_instance_busy`` guard matters**:
                # in the new design, finalization happens *after*
                # a report Task's turn ends. A naive "always
                # finalize on recovery" (what the deleted
                # ``_retrigger_parent_finalize`` did) would
                # finalize a job whose report Task is still
                # PENDING and about to run — re-introducing the
                # orphan-Task bug. The guard makes recovery
                # **defer** when ANY live Task exists (PENDING +
                # RUNNING + PAUSED), and **finalize** only when
                # nothing else will (the genuine stuck-parent case:
                # report Task already ran/crashed, no live task,
                # watcher fired, job still PROCESSING). PAUSED
                # Tasks count too — they will resume and complete,
                # so deferring lets the natural path drive the
                # finalize when the pause is lifted.
                #
                # **Known limitation (in-memory bus state)**: the
                # bus's ``_parent_errored`` and the new
                # ``_parent_error_message`` are in-memory dicts.
                # If a crash occurs after a child errors but
                # before finalize, crash recovery won't see
                # ``had_parent_error`` and will finalize as
                # COMPLETED instead of ERROR. Acceptable
                # edge case — a permanent fix would require
                # persisting per-parent error state to the DB.
                logger.info(
                    f"bus crash recovery: target="
                    f"{target_id[:8]}... has 0 PENDING watchers — "
                    f"deciding: defer if any live task, "
                    f"else finalize via single path "
                    f"(no FollowUp enqueue — bus path is internal "
                    f"plumbing, not an LLM message source)"
                )

                # ─── Decision: live-work check via has_instance_busy ───
                # Bug-1 fix (2026-08-12): the prior ``has_inflight_task``
                # call checked PENDING + RUNNING only — a PAUSED Task
                # would (incorrectly) pass the "no live work" check
                # and the reaper would finalize a parent whose report
                # Task was PAUSED about to resume, re-introducing the
                # orphan-Task bug. ``has_instance_busy`` widens the
                # status set to PENDING + RUNNING + PAUSED, matching
                # the canonical predicate the claim path and zombie
                # reaper now share.
                _task_repo = getattr(manager, "_task_repo", None)
                _observer = getattr(
                    manager, "_job_feedback_observer", None
                )
                _has_inflight = False
                if _task_repo is not None:
                    try:
                        _has_inflight = await asyncio.to_thread(
                            _task_repo.has_instance_busy, target_id
                        )
                    except Exception as inflight_err:
                        logger.warning(
                            f"bus crash recovery: has_instance_busy "
                            f"check failed target={target_id[:8]}...: "
                            f"{inflight_err} — defaulting to defer "
                            f"(safe: a task may still be in flight)"
                        )
                        # Treat as in-flight (defer) — the safe
                        # default; an extra recovery cycle is
                        # acceptable, a premature finalize is not.
                        _has_inflight = True
                else:
                    # No task_repo wired (test or partial init).
                    # Defer rather than finalize directly — the
                    # row will be re-picked on next restart.
                    _has_inflight = True

                if _has_inflight:
                    # A report Task is pending, running, or paused.
                    # The PENDING/RUNNING case: it will drive the
                    # parent's lifecycle event → finalize via the
                    # natural path (``_process_event``). The PAUSED
                    # case: it will resume and complete when the
                    # user unpauses, then drive the same natural
                    # finalize path. Either way, deferring lets the
                    # natural path do the work.
                    logger.info(
                        f"bus crash recovery: target="
                        f"{target_id[:8]}... has live task — "
                        f"deferring to natural finalize path, stamping row"
                    )
                    try:
                        await bus.mark_enqueued(watch_id)
                    except Exception as stamp_err:
                        logger.warning(
                            f"bus crash recovery: stamp (defer) "
                            f"failed watch_id={watch_id[:8]}...: "
                            f"{stamp_err}"
                        )
                    continue

                # ─── No in-flight task → finalize directly via the single path ───
                # The previous ``_retrigger_parent_finalize`` did
                # the same logic in a slightly different shape
                # (hardcoded COMPLETED + bus error override). The
                # decision-based approach here mirrors
                # ``_process_event``'s finalize branch: status =
                # COMPLETED unless ``bus.had_parent_error`` is True,
                # in which case ERROR + the bus's
                # ``parent_error_message``. The clear-after-finalize
                # also mirrors ``_process_event`` (Step 1.7) so a
                # revived instance doesn't inherit the sticky
                # error state.
                if _observer is None:
                    logger.warning(
                        f"bus crash recovery: observer not wired on "
                        f"manager; cannot finalize target="
                        f"{target_id[:8]}... (row will be re-picked "
                        f"on next restart once observer is wired)"
                    )
                    continue

                try:
                    _job = await _observer._get_processing_job_for_instance(
                        target_id
                    )
                except Exception as lookup_err:
                    logger.warning(
                        f"bus crash recovery: job lookup failed "
                        f"target={target_id[:8]}...: {lookup_err} "
                        f"(row will be re-picked on next restart)"
                    )
                    continue

                if _job is None:
                    logger.debug(
                        f"bus crash recovery: no PROCESSING job for "
                        f"{target_id[:8]}..., may already be finalized"
                    )
                    try:
                        await bus.mark_enqueued(watch_id)
                    except Exception as stamp_err:
                        logger.warning(
                            f"bus crash recovery: stamp (no-job) "
                            f"failed watch_id={watch_id[:8]}...: "
                            f"{stamp_err}"
                        )
                    continue

                # ─── Conservative "any error → error" rule (same source as ``_process_event``) ───
                # Delegates to the shared helper in
                # ``daemon.services.job_feedback_observer`` so the rule
                # lives in one place. The clear-after-finalize mirrors
                # ``_process_event`` (Step 1.7) so a revived instance
                # doesn't inherit the sticky error state.
                from daemon.services.job_feedback_observer import (
                    _resolve_finalize_status,
                )
                _final_status, _final_error = _resolve_finalize_status(
                    bus, target_id, InstanceStatus.COMPLETED.value
                )

                try:
                    await _observer._finalize_job(
                        _job,
                        target_id,
                        _final_status,
                        error=_final_error,
                    )
                    # Clear the sticky error state so a revived
                    # instance doesn't inherit it — mirrors
                    # ``_process_event`` (Step 1.7).
                    if bus.had_parent_error(target_id):
                        bus.clear_parent_error(target_id)
                except Exception as finalize_err:
                    logger.warning(
                        f"bus crash recovery: _finalize_job failed "
                        f"target={target_id[:8]}...: {finalize_err} "
                        f"(row will be re-picked on next restart)"
                    )
                    # Do NOT stamp — leave the row un-stamped so a
                    # future restart retries. _finalize_job is
                    # idempotent (atomic WHERE status=PROCESSING).
                    continue

                # Finalization dispatched — stamp the row so the
                # next restart does not re-trigger.
                try:
                    await bus.mark_enqueued(watch_id)
                except Exception as stamp_err:
                    logger.warning(
                        f"bus crash recovery: stamp (post-finalize) "
                        f"failed watch_id={watch_id[:8]}...: {stamp_err}"
                    )
        set_dependency_bus(bus)
        app.state._dependency_bus = bus
        logger.info(
            f"DependencyBus started (recovered={len(recovered)} "
            f"FIRED-but-unsent watchers)"
        )
    except Exception as e:
        logger.warning(
            f"Failed to start DependencyBus (continuing without it): {e}"
        )
        set_dependency_bus(None)


async def shutdown_dependency_bus(app) -> None:
    """Stop the DependencyBus and clear the module singleton.

    Called from the lifespan shutdown sequence. Safe to call when the
    bus was never started (``app.state._dependency_bus`` missing) — the
    helper logs at WARNING on failure but never raises, so the rest of
    the shutdown sequence proceeds.
    """
    from daemon.services.dependency_bus import set_dependency_bus

    bus = getattr(app.state, "_dependency_bus", None)
    if bus is not None:
        try:
            await bus.stop()
        except Exception as e:
            logger.warning(f"Error stopping DependencyBus: {e}")
        set_dependency_bus(None)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Ensemble Daemon",
        version=__version__,
        lifespan=lifespan
    )
    
    logger.info(f"Starting Ensemble v{__version__}")
    
    # Create API router with /api prefix
    api_router = APIRouter(prefix="/api")
    
    # Selective access log middleware - logs only paths we want to see
    class SelectiveAccessLogMiddleware:
        """Middleware that controls access logging via custom logic."""

        # Exact paths to HIDE (exclude from logging)
        HIDE_PATTERNS = [
            "/api/instances",
        ]

        # Exact (method, path) pairs to HIDE (exclude from logging)
        HIDE_METHOD_PATH = [
            ("GET", "/api/jobs"),
        ]

        # ANSI color codes
        RESET = "\033[0m"
        BOLD = "\033[1m"
        
        # Method colors
        COLORS = {
            "GET": "\033[92m",      # Green
            "POST": "\033[96m",     # Cyan
            "PUT": "\033[93m",      # Yellow
            "PATCH": "\033[93m",    # Yellow
            "DELETE": "\033[91m",   # Red
        }
        
        def status_color(self, code: int) -> str:
            if 200 <= code < 300:
                return "\033[92m"   # Green
            elif 300 <= code < 400:
                return "\033[94m"   # Blue
            elif 400 <= code < 500:
                return "\033[93m"   # Yellow
            else:
                return "\033[91m"   # Red

        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                await self.app(scope, receive, send)
                return

            method = scope.get("method", "")
            path = scope.get("path", "")
            client = scope.get("client")
            client_addr = f"{client[0]}:{client[1]}" if client else "unknown"

            status_code = 200

            async def custom_send(message):
                nonlocal status_code
                if message["type"] == "http.response.start":
                    status_code = message["status"]
                await send(message)

            await self.app(scope, receive, custom_send)

            if (
                path in self.HIDE_PATTERNS
                or (method, path) in self.HIDE_METHOD_PATH
            ):
                return

            method_color = self.COLORS.get(method, self.RESET)
            status_color = self.status_color(status_code)
            
            log_msg = (
                f"{self.BOLD}{client_addr}{self.RESET} "
                f"{method_color}{method}{self.RESET} "
                f"{path} "
                f"{status_color}{status_code}{self.RESET}"
            )
            logger.info(log_msg)

    # Add CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Add selective access log middleware
    app.add_middleware(SelectiveAccessLogMiddleware)

    # Global exception handler
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                code=ErrorCodes.INTERNAL_ERROR,
                message=str(exc)
            ).model_dump()
        )

    # Health check endpoint
    @api_router.get("/health", response_model=HealthResponse)
    async def health_check(request: Request):
        """Health check endpoint.

        Reports active database backend, whether PostgreSQL env vars are
        configured, and whether a SQLite→PostgreSQL migration can currently
        start. The ``ensemble_config`` and ``migration_worker`` are read
        from ``app.state`` once the lifespan wires them up; until then the
        new fields are ``None``.
        """
        start_time = getattr(request.app.state, 'start_time', None)
        ensemble_config = getattr(request.app.state, 'ensemble_config', None)
        migration_worker = getattr(request.app.state, 'migration_worker', None)

        # Derive migration availability from the worker if it's been wired
        # up. ``is_migration_available()`` is a pure read of
        # ``manager.engine.url`` + ``ensemble_config`` + env vars, so it's
        # safe to call on every health-check request.
        migration_available: bool | None = None
        if migration_worker is not None:
            try:
                migration_available = bool(
                    migration_worker.is_migration_available().get("can_migrate")
                )
            except Exception:
                logger.debug("Health check: migration availability probe failed", exc_info=True)
                migration_available = None

        return HealthResponse(
            status="healthy",
            uptime_seconds=time.time() - start_time if start_time else 0,
            version=__version__,
            current_database=ensemble_config.database if ensemble_config is not None else None,
            postgres_env_available=(
                ensemble_config.postgres_env_available
                if ensemble_config is not None else None
            ),
            migration_available=migration_available,
        )

    # Project info endpoint
    @api_router.get("/info")
    async def get_info():
        """Get basic project information."""
        return {
            "name": "agents-ensemble",
            "version": __version__,
            "description": "Multi-Agent AI Daemon with LangGraph",
        }

    # Include routers - order matters for route matching
    api_router.include_router(agents_router)        # /api/agents
    api_router.include_router(instances_router)      # /api/instances
    api_router.include_router(messages_router)      # /api/instances/{id}/messages, /api/instances/{id}/events
    api_router.include_router(sources_router)       # /api/sources
    api_router.include_router(mappings_router)      # /api/sources/{id}/mappings
    api_router.include_router(schedules_router)     # /api/schedules
    api_router.include_router(webhooks_router)      # /api/webhooks
    api_router.include_router(jobs_router)          # /api/jobs
    api_router.include_router(work_router)          # /api/work  (Phase 4: virtual job mgmt)
    api_router.include_router(projects_router)      # /api/projects
    api_router.include_router(queues_router)        # /api/queues
    api_router.include_router(skills_router)        # /api/skills (Phase 6: skill management REST API)
    api_router.include_router(dlq_router)           # /api/dlq
    api_router.include_router(mcp_servers_router)    # /api/mcp-servers
    api_router.include_router(notifications_router)   # /api/notifications
    api_router.include_router(migration_router)       # /api/migration
    api_router.include_router(database_router)        # /api/database
    api_router.include_router(settings_router)       # /api/settings (Phase 1: user language preference)
    api_router.include_router(skill_bank_router)        # /api/skill-bank (Skill Bank CRUD)
    api_router.include_router(blueprints_router)        # /api/projects/{project_id}/blueprints (Project Blueprints CRUD)
    api_router.include_router(workspace_router)         # /api/workspace (Phase 1: workspace viewer)
    api_router.include_router(recovery_router)          # /api/recovery (Phase 2: pause-report-recovery crash-recovery endpoint)

    app.include_router(api_router)

    # --- MCP KB server mounts (MUST be before catch-all SPA route) ---
    # Initialize KB MCP server first (creates FastMCP and session manager)
    kb_mcp = create_kb_mcp_server()
    # Mount SSE and StreamableHTTP apps
    app.mount("/api/mcp/kb/sse", get_kb_mcp_sse_app("/api/mcp/kb/sse"))
    app.mount("/api/mcp/kb", get_kb_mcp_http_app())

    # UI serving endpoints (production)
    @app.get("/")
    async def serve_ui():
        """Serve the frontend UI."""
        index_path = FRONTEND_DIST / "index.html"
        if index_path.exists():
            return FileResponse(str(index_path))
        return JSONResponse(
            status_code=404,
            content={"error": "UI not built. Run 'npm run build' in frontend directory."}
        )

    @app.get("/{path:path}")
    async def serve_ui_assets(path: str):
        """Serve frontend assets and SPA routing."""
        # Skip API routes
        # S1: 'vscode' prefix required — prevents /vscodefoo from
        # hitting SPA fallback. Starlette mount prefix matching does
        # NOT match /vscodefoo to the /vscode mount.
        if (path.startswith('api') or path.startswith('ws')
                or path.startswith('vscode')):
            return JSONResponse(
                status_code=404,
                content={"error": "Not found"}
            )
        
        asset_path = FRONTEND_DIST / path
        if asset_path.exists():
            return FileResponse(str(asset_path))
        # For SPA routing, serve index.html
        index_path = FRONTEND_DIST / "index.html"
        if index_path.exists():
            return FileResponse(str(index_path))
        return JSONResponse(
            status_code=404,
            content={"error": "Asset not found"}
        )

    return app


# Create app instance for convenience
app = create_app()
