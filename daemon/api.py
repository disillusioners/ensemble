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
import logging
import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from fastapi import FastAPI, Request, APIRouter
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import AsyncGenerator
from pathlib import Path

# Configure logging for daemon modules
# This ensures our logs are visible when running via uvicorn
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)

# Suppress uvicorn INFO-level access logs (our SelectiveAccessLogMiddleware handles selective logging)
uvicorn_access = logging.getLogger("uvicorn.access")
uvicorn_access.setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# Import routers
from daemon.routers import (
    agents_router,
    instances_router,
    messages_router,
    mappings_router,
    schedules_router,
    sources_router,
    webhooks_router,
    jobs_router,
    projects_router,
    queues_router,
    dlq_router,
)

# Re-export validate_agent_id from utils for backward compatibility
from daemon.utils import validate_agent_id as validate_agent_id  # noqa: F401

# Re-export send_message for backward compatibility (tests/unit/test_vision.py imports it)
from daemon.routers.messages import send_message as send_message  # noqa: F401

from daemon import __version__
from daemon.models import ErrorCodes, ErrorResponse, HealthResponse
from daemon.services.live_event_hub import LiveEventHub
from daemon.constants import SSE_TIMEOUT_S, SSE_PING_INTERVAL, SSE_QUEUE_MAXSIZE
import daemon.constants

# Determine the base path (use working directory for production)
# PyInstaller runs from INSTALL_DIR where frontend/dist is expected
import sys
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent.parent
FRONTEND_DIST = BASE_DIR / "frontend" / "dist"


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
    
    # Load config first
    config = load_config()
    
    # Initialize InstanceManager
    manager = InstanceManager(config)
    await manager.initialize()
    
    # Set up worker pool for message processing
    manager.setup_worker_pool()
    start_time = time.time()
    
    # Initialize JobQueueService with shared engine from manager
    # Set create_tables=True to ensure job_queue_items table is created
    job_repository = create_job_repository(engine=manager._engine, create_tables=True)
    
    # Create LockRepository for job lock persistence
    lock_repo = LockRepository(engine=manager._engine)
    job_lock_manager = JobLockManager(lock_repo=lock_repo)
    
    # Create queue repository for job queue management
    queue_repo = JobQueueRepository(engine=manager._engine)

    # Create watcher repository for job lifecycle subscriptions
    from daemon.repositories.job_queue.watcher_models import JobWatcher
    from daemon.repositories.job_queue.watcher_repository import JobWatcherRepository
    JobWatcher.metadata.create_all(manager._engine)
    watcher_repo = JobWatcherRepository(engine=manager._engine)
    manager._watcher_repo = watcher_repo  # Store on manager for tools access

    # Create job queue management service for auto-provisioning
    job_queue_mgmt_service = JobQueueMgmtService(
        queue_repo=queue_repo,
        job_repo=job_repository,
    )
    
    # Create DispatchEventBus for event-driven job dispatch
    dispatch_event_bus = DispatchEventBus()
    dispatch_event_bus.set_event_loop(asyncio.get_running_loop())
    
    # Set DispatchEventBus on JobQueueMgmtService
    job_queue_mgmt_service._dispatch_bus = dispatch_event_bus
    
    # Initialize CredentialManager
    credential_manager = CredentialManager()
    
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
    
    # Wire retry engine into JobQueueService
    from daemon.services.job_retry_engine import JobRetryEngine
    retry_engine = JobRetryEngine(
        job_repo=job_repository,
        queue_repo=queue_repo,
        dlq_service=None,  # Will be set after dead_letter_service is created
        config=config.job_system,
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
    
    # Wire InstanceManager into JobQueueService
    job_queue_service.set_instance_manager(manager)
    job_queue_service.set_project_repo(manager._project_repository)
    
    # Run startup recovery for orphaned PROCESSING jobs
    instance_repo = SQLModelInstanceRepository(engine=manager._engine)
    job_recovery = JobRecoveryService(
        job_repository=job_repository,
        lock_repository=lock_repo,
        instance_repository=instance_repo,
        job_queue_service=job_queue_service,  # For notify_watchers on Path 7
    )
    recovery_stats = await job_recovery.recover_on_startup()
    logger.info(f"Job recovery: {recovery_stats}")

    # Reconcile terminal watches — notify watchers for jobs that already reached terminal state
    reconciled = await job_queue_service.reconcile_terminal_watches()
    if reconciled > 0:
        logger.info(f"Reconciled {reconciled} terminal job watches")
    
    # Initialize DeadLetterService and set on retry engine
    dlq_repository = DeadLetterRepository(engine=manager._engine)
    dead_letter_service = DeadLetterService(
        job_repository=job_repository,
        dlq_repository=dlq_repository,
        job_queue_service=job_queue_service,  # For watcher notifications
        loop=asyncio.get_running_loop(),        # For async bridge
    )
    retry_engine._dlq_service = dead_letter_service
    retry_engine._job_queue_service = job_queue_service  # Wire for Path 6 notifications
    retry_engine._loop = asyncio.get_running_loop()       # Wire event loop

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
    
    # Bootstrap system default project (Phase 1 of system_default_project feature)
    # This ensures the system project exists and has its queues provisioned
    # before any other services start using it. Must run BEFORE JobProcessor.start().
    try:
        system_project_id = manager._project_repository.ensure_system_default_project()
        constants.SYSTEM_DEFAULT_PROJECT_ID = system_project_id
        await job_queue_mgmt_service.auto_provision_system_queues(system_project_id)
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
    
    # Initialize LiveEventHub for live-only SSE streaming
    app.state.live_hub = manager._live_hub
    
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
    
    # Set JobQueueMgmtService for projects and queues routers injection
    from daemon.routers.projects import set_job_queue_mgmt_service as set_proj_mgmt_service
    set_proj_mgmt_service(job_queue_mgmt_service)
    from daemon.routers.queues import set_job_queue_mgmt_service as set_queue_mgmt_service
    set_queue_mgmt_service(job_queue_mgmt_service)
    
    yield
    
    # Shutdown sequence
    # Stop RetryScheduler first
    if retry_scheduler is not None:
        await retry_scheduler.stop()
    
    # Stop JobFeedbackObserver before processor
    if hasattr(app.state, '_job_feedback_observer'):
        await app.state._job_feedback_observer.stop()
    
    # Stop JobProcessor
    if hasattr(app.state, 'job_processor') and app.state.job_processor:
        await app.state.job_processor.stop()
    
    # Shutdown LiveEventHub
    if hasattr(app.state, 'live_hub'):
        await app.state.live_hub.shutdown()
    
    # Call manager shutdown for graceful shutdown
    await manager.shutdown()


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

            if path in self.HIDE_PATTERNS:
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
        """Health check endpoint."""
        start_time = getattr(request.app.state, 'start_time', None)
        return HealthResponse(
            status="healthy",
            uptime_seconds=time.time() - start_time if start_time else 0,
            version=__version__
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
    api_router.include_router(projects_router)      # /api/projects
    api_router.include_router(queues_router)        # /api/queues
    api_router.include_router(dlq_router)           # /api/dlq
    
    app.include_router(api_router)

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
        if path.startswith('api') or path.startswith('ws'):
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
