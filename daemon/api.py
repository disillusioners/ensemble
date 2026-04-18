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
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Request, APIRouter
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse
from typing import AsyncGenerator, Any
from pathlib import Path
import os
import secrets

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

from .models import (
    InstanceCreate,
    InstanceInfo,
    MessageCreate,
    MessageResponse,
    ErrorResponse,
    ErrorCodes,
    InstanceStatus,
    HealthResponse,
    InstanceListResponse,
    AgentInfo,
    AgentListResponse,
    AgentCreate,
    # Source models
    SourceCreate,
    SourceUpdate,
    SourceInfo,
    SourceListResponse,
    SourceActionResponse,
    SourceStatus,
    SourceType,
    SourceTestRequest,
    SourceTestResponse,
    # Mapping models
    InstanceMappingCreate,
    InstanceMappingInfo,
    InstanceMappingListResponse,
    DeleteResponse,
    # Schedule models
    ScheduleInfo,
    ScheduleListResponse,
    ScheduleUpdate,
    ScheduleExecutionInfo,
    ScheduleExecutionListResponse,
    ScheduleTriggerResponse,
)
from .manager import InstanceManager
from .config import Config, load_config
from .persistence import get_checkpointer
from .services.live_event_hub import LiveEventHub
from .sources.credentials import CredentialManager
from .services.job_queue_service import JobQueueService
from .services.job_lock_manager import JobLockManager
from .services.job_processor import JobProcessor
from .services.job_feedback_observer import JobFeedbackObserver
from .services.job_queue_mgmt_service import JobQueueMgmtService
from .services.dead_letter_service import DeadLetterService
from .services.job_recovery_service import JobRecoveryService
from .services.dispatch_event_bus import DispatchEventBus
from .repositories.job_queue.queue_repository import JobQueueRepository
from .repositories.job_queue.lock_repository import LockRepository
from .repositories.job_queue.dead_letter_repository import DeadLetterRepository
from .repositories.instance.repository import SQLModelInstanceRepository
from .repositories import create_job_repository, create_engine_from_config, DatabaseConfig
from .registry import get_registry
from .cancellation import CancellationReason
from . import __version__


def validate_agent_id(agent_id: str) -> tuple[str, Path]:
    """Validate agent_id exists and return agent_id with path.
    
    This is the preferred function for validating agent references.
    
    Args:
        agent_id: The agent identifier to validate.
        
    Returns:
        Tuple of (agent_id, resolved_absolute_path).
        
    Raises:
        HTTPException: If agent is invalid or not found.
    """
    registry = get_registry()
    
    # Check agent exists
    metadata = registry.get(agent_id)
    if metadata is None:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message=f"Agent not found: {agent_id}"
            ).model_dump()
        )
    
    return agent_id, metadata.path


async def _reject_scheduler_lifecycle(source_id: str) -> None:
    """Raise error if source is a scheduler type.
    
    Scheduler sources manage their own lifecycle automatically and cannot be
    controlled via API. This helper checks if a source is a scheduler and
    raises an HTTPException if so.
    
    Args:
        source_id: The source ID to check.
        
    Raises:
        HTTPException: If the source is a scheduler type.
    """
    source = await asyncio.to_thread(manager._source_repository.get_source_config, source_id)
    if source and source.source_type == "scheduler":
        raise HTTPException(
            status_code=400,
            detail={
                "code": "SCHEDULER_SOURCE_UPDATE_NOT_ALLOWED",
                "message": "Scheduler sources manage their own lifecycle and cannot be controlled via API."
            }
        )


# Determine the base path
BASE_DIR = Path(__file__).parent.parent
FRONTEND_DIST = BASE_DIR / "frontend" / "dist"

# Max size in bytes for credentials JSON
MAX_CREDENTIALS_SIZE = 4096

# Global state
manager: InstanceManager = None
start_time: float = None
credential_manager = CredentialManager()
job_queue_service: JobQueueService = None
job_processor: JobProcessor = None
job_queue_mgmt_service: JobQueueMgmtService = None
retry_scheduler = None
dispatch_event_bus: DispatchEventBus = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global manager, start_time, job_queue_service, job_processor, job_queue_mgmt_service, retry_scheduler, dispatch_event_bus
    config = load_config()
    manager = InstanceManager(config)
    await manager.initialize()  # Initialize async checkpointer within async context
    
    # Set up worker pool for message processing
    manager.setup_worker_pool(num_workers=4)
    start_time = time.time()
    
    # Initialize JobQueueService with shared engine from manager
    # Set create_tables=True to ensure job_queue_items table is created
    # The JobItem model is registered with SQLModel.metadata when
    # create_job_repository is imported (via its import chain)
    job_repository = create_job_repository(engine=manager._engine, create_tables=True)
    
    # Create LockRepository for job lock persistence
    lock_repo = LockRepository(engine=manager._engine)
    job_lock_manager = JobLockManager(lock_repo=lock_repo)
    
    # Reconcile locks from DB on startup to rebuild in-memory state
    await job_lock_manager.reconcile_locks()
    
    # Create queue repository for job queue management
    queue_repo = JobQueueRepository(engine=manager._engine)
    
    # Create job queue management service for auto-provisioning
    # Note: dispatch_bus will be set after DispatchEventBus is created
    job_queue_mgmt_service = JobQueueMgmtService(
        queue_repo=queue_repo,
        job_repo=job_repository,
    )
    
    # Create DispatchEventBus for event-driven job dispatch
    # Must be created BEFORE setting it on services
    dispatch_event_bus = DispatchEventBus()
    dispatch_event_bus.set_event_loop(asyncio.get_running_loop())
    
    # Set DispatchEventBus on JobQueueMgmtService for resume notifications
    job_queue_mgmt_service._dispatch_bus = dispatch_event_bus
    
    # Initialize JobQueueService with queue repository for per-queue locking
    job_queue_service = JobQueueService(
        repository=job_repository,
        lock_manager=job_lock_manager,
        queue_repo=queue_repo,
        instance_manager=manager,
    )
    
    # W6: Store the event loop for sync→async operations in complete_job_sync()
    job_queue_service.set_event_loop(asyncio.get_running_loop())
    
    # Set job system config for TTL and other settings
    job_queue_service.set_config(config.job_system)
    
    # Set DispatchEventBus on JobQueueService for enqueue notifications
    job_queue_service.set_dispatch_bus(dispatch_event_bus)
    
    # Set up dependency injection for jobs router
    from daemon.routers.jobs import set_job_queue_service
    set_job_queue_service(job_queue_service)
    
    # Set up dependency injection for projects router
    from daemon.routers.projects import set_project_repository, set_job_queue_mgmt_service
    set_project_repository(manager._project_repository)
    set_job_queue_mgmt_service(job_queue_mgmt_service)
    
    # Set up dependency injection for queues router
    from daemon.routers.queues import set_job_queue_mgmt_service
    set_job_queue_mgmt_service(job_queue_mgmt_service)
    
    # Set up dependency injection for DLQ router
    from daemon.repositories.job_queue.dead_letter_repository import set_dead_letter_repository
    dlq_repository = DeadLetterRepository(engine=manager._engine)
    set_dead_letter_repository(dlq_repository)
    
    dead_letter_service = DeadLetterService(
        job_repository=job_repository,
        dlq_repository=dlq_repository,
    )
    from daemon.routers.dlq import set_dead_letter_service
    set_dead_letter_service(dead_letter_service)
    from daemon.routers.jobs import set_dead_letter_service as set_jobs_dlq_service
    set_jobs_dlq_service(dead_letter_service)
    
    # Initialize JobRetryEngine for automatic retry with backoff
    from daemon.services.job_retry_engine import JobRetryEngine
    retry_engine = JobRetryEngine(
        job_repo=job_repository,
        queue_repo=queue_repo,
        dlq_service=dead_letter_service,
        config=config.job_system,
    )
    
    # Wire retry engine into JobQueueService so it can use maybe_retry on job failure
    job_queue_service.set_retry_engine(retry_engine)
    
    # Initialize and start RetryScheduler for background retry polling
    # Use the same data directory as persistence for the lock file
    from pathlib import Path
    lock_dir = Path(config.persistence.db_path).parent
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
    
    # Wire JobQueueService into InstanceManager for proper cleanup
    manager.set_job_queue_service(job_queue_service)
    
    # Wire InstanceManager into JobQueueService for cancellation cascade
    job_queue_service.set_instance_manager(manager)
    
    # Run startup recovery for orphaned PROCESSING jobs
    # This must run FIRST — clean up orphans before observer/processor start
    instance_repo = SQLModelInstanceRepository(engine=manager._engine)
    job_recovery = JobRecoveryService(
        job_repository=job_repository,
        lock_repository=lock_repo,
        instance_repository=instance_repo,
    )
    recovery_stats = await job_recovery.recover_on_startup()
    logger.info(f"Job recovery: {recovery_stats}")
    
    # Initialize and start JobFeedbackObserver (SECOND — observe lifecycle events)
    job_feedback_observer = JobFeedbackObserver(
        job_queue_service=job_queue_service,
        event_bus=manager._event_bus,
        job_repo=job_repository,
        lock_repo=lock_repo,
    )
    await job_feedback_observer.start()
    logger.info("JobFeedbackObserver started")
    
    # Initialize and start JobProcessor (THIRD — start processing new jobs)
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
    # Store in app.state for SSE endpoint to use
    app.state.live_hub = manager._live_hub
    
    # Auto-provision system queues for existing projects
    # This ensures all projects have their system queues (system_fifo_queue, system_parallel_queue)
    try:
        projects = await asyncio.to_thread(manager._project_repository.list_projects)
        for project in projects:
            await job_queue_mgmt_service.auto_provision_system_queues(project.project_id)
        logger.info(f"Auto-provisioned system queues for {len(projects)} projects")
    except Exception as e:
        logger.warning(f"Failed to auto-provision system queues: {e}")
    
    # Start message sources (loads adapters from DB and starts them)
    await manager.start_sources()
    
    yield
    
    # Stop RetryScheduler first (so it stops triggering new jobs)
    if retry_scheduler is not None:
        await retry_scheduler.stop()
    
    # Stop JobFeedbackObserver before processor (so observer handles completions while processor stops)
    if 'job_feedback_observer' in locals():
        await job_feedback_observer.stop()
    
    # Stop JobProcessor on shutdown
    await job_processor.stop()
    
    # Shutdown LiveEventHub
    if hasattr(app.state, 'live_hub'):
        await app.state.live_hub.shutdown()
    
    # Call manager shutdown for graceful shutdown sequence
    # This handles cancellation of active requests, consumers, SSE, stop_sources(), etc.
    await manager.shutdown()


app = FastAPI(
    title="Ensemble Daemon",
    version=__version__,
    lifespan=lifespan
)

logger.info(f"Starting Ensemble v{__version__}")

# API Router with /api prefix
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
    
    # Status colors
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

        # Extract request info before processing
        method = scope.get("method", "")
        path = scope.get("path", "")
        client = scope.get("client")
        client_addr = f"{client[0]}:{client[1]}" if client else "unknown"

        status_code = 200  # default

        async def custom_send(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        # Process the request
        await self.app(scope, receive, custom_send)

        # Skip logging if path exactly matches hide patterns
        if path in self.HIDE_PATTERNS:
            return

        # Colorize log output
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

# Add selective access log middleware (must be added AFTER CORS)
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


# 1. GET /health - Health check
@api_router.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint."""
    return HealthResponse(
        status="healthy",
        uptime_seconds=time.time() - start_time,
        version=__version__
    )


# 1.1. GET /info - Project info
@api_router.get("/info")
async def get_info():
    """Get basic project information."""
    return {
        "name": "agents-ensemble",
        "version": __version__,
        "description": "Multi-Agent AI Daemon with LangGraph",
    }


# 1.5. GET /agents - List available agents
@api_router.get("/agents", response_model=AgentListResponse)
async def list_agents():
    """List all available agents by scanning the agents directory."""
    import json
    
    agents_dir = BASE_DIR / "agents"
    agents = []
    
    if agents_dir.exists():
        for agent_path in sorted(agents_dir.iterdir()):
            # Skip hidden directories (starting with .) and non-directories
            if not agent_path.is_dir() or agent_path.name.startswith("."):
                continue
            
            # Skip special internal directories that aren't agents
            if agent_path.name in ("_trash", "_baby_template"):
                continue
            
            # Skip internal agents (starting with _)
            if agent_path.name.startswith("_"):
                continue
            
            meta_path = agent_path / "meta.json"
            if meta_path.exists():
                try:
                    with open(meta_path, "r") as f:
                        meta = json.load(f)
                    
                    agents.append(AgentInfo(
                        id=meta.get("id", agent_path.name),
                        name=meta.get("name", agent_path.name.title()),
                        description=meta.get("description", ""),
                        icon=meta.get("icon", "🤖"),
                        color=meta.get("color", "accent-blue"),
                        version=meta.get("version"),
                        agent_dir=f"./agents/{agent_path.name}",
                        system=meta.get("system", False),
                    ))
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning(f"Failed to load meta.json for {agent_path.name}: {e}")
    
    return AgentListResponse(agents=agents)


# 1.6. POST /agents - Create new agent
@api_router.post("/agents", response_model=AgentInfo, status_code=201)
async def create_agent(agent_create: AgentCreate):
    """Create a new agent from template."""
    import json
    import shutil
    
    agents_dir = BASE_DIR / "agents"
    template_dir = BASE_DIR / "agents" / "_baby_template"
    new_agent_dir = agents_dir / agent_create.id
    
    # Validate ID
    if not agent_create.id.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message="Agent ID must contain only alphanumeric characters, hyphens, and underscores"
            ).model_dump()
        )
    
    # Check if agent already exists
    if new_agent_dir.exists():
        raise HTTPException(
            status_code=409,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message=f"Agent with ID '{agent_create.id}' already exists"
            ).model_dump()
        )
    
    # Check template exists
    if not template_dir.exists():
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                code=ErrorCodes.INTERNAL_ERROR,
                message="Agent template not found"
            ).model_dump()
        )
    
    try:
        # Create agent directory
        new_agent_dir.mkdir(parents=True, exist_ok=True)
        
        # Copy template files (exclude history and memories directories)
        for item in template_dir.iterdir():
            if item.name in ("history", "memories"):
                continue
            if item.is_file():
                shutil.copy2(item, new_agent_dir / item.name)
        
        # Create meta.json
        meta = {
            "id": agent_create.id,
            "name": agent_create.name,
            "description": agent_create.description,
            "icon": agent_create.icon,
            "color": agent_create.color,
            "version": "1.0.0"
        }
        
        with open(new_agent_dir / "meta.json", "w") as f:
            json.dump(meta, f, indent=2)
        
        # Create empty directories
        (new_agent_dir / "history").mkdir(exist_ok=True)
        (new_agent_dir / "memories").mkdir(exist_ok=True)
        
        return AgentInfo(
            id=agent_create.id,
            name=agent_create.name,
            description=agent_create.description,
            icon=agent_create.icon,
            color=agent_create.color,
            version="1.0.0",
            agent_dir=f"./agents/{agent_create.id}",
        )
    except Exception as e:
        # Cleanup on failure
        if new_agent_dir.exists():
            shutil.rmtree(new_agent_dir)
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                code=ErrorCodes.INTERNAL_ERROR,
                message=f"Failed to create agent: {str(e)}"
            ).model_dump()
        )


# 1.7. DELETE /agents/{agent_id} - Move agent to trash
@api_router.delete("/agents/{agent_id}")
async def delete_agent(agent_id: str):
    """Move an agent to trash (soft delete)."""
    import shutil
    from datetime import datetime
    
    agents_dir = BASE_DIR / "agents"
    agent_dir = agents_dir / agent_id
    trash_dir = agents_dir / "_trash"
    
    # Check agent exists
    if not agent_dir.exists():
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message=f"Agent not found: {agent_id}"
            ).model_dump()
        )
    
    # Don't allow deleting internal directories
    if agent_id.startswith("_"):
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message="Cannot delete internal agents"
            ).model_dump()
        )
    
    try:
        # Create trash directory if needed
        trash_dir.mkdir(exist_ok=True)
        
        # Generate unique name with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        trashed_name = f"{agent_id}_{timestamp}"
        trashed_path = trash_dir / trashed_name
        
        # If target already exists, add suffix
        suffix = 1
        while trashed_path.exists():
            trashed_path = trash_dir / f"{trashed_name}_{suffix}"
            suffix += 1
        
        # Move agent to trash
        shutil.move(str(agent_dir), str(trashed_path))
        
        return {"deleted": True, "agent_id": agent_id, "trashed_as": trashed_path.name}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                code=ErrorCodes.INTERNAL_ERROR,
                message=f"Failed to delete agent: {str(e)}"
            ).model_dump()
        )


# 2. POST /instances - Spawn instance
@api_router.post("/instances", response_model=InstanceInfo, status_code=201)
async def create_instance(instance_create: InstanceCreate):
    """Spawn a new instance."""
    try:
        # Prefer agent_id over agent_dir
        instance_id = manager.spawn_instance(
            agent_id=instance_create.agent_id,
            instance_id=instance_create.instance_id,
        )
    except ValueError as e:
        error_msg = str(e)
        if "Max instances limit" in error_msg:
            raise HTTPException(
                status_code=429,
                detail=ErrorResponse(
                    code=ErrorCodes.MAX_INSTANCES_EXCEEDED,
                    message=error_msg
                ).model_dump()
            )
        else:
            raise HTTPException(
                status_code=400,
                detail=ErrorResponse(
                    code=ErrorCodes.INVALID_REQUEST,
                    message=error_msg
                ).model_dump()
            )

    # Get instance info from database
    instance_meta = manager.get_instance_info(instance_id)
    return InstanceInfo(
        instance_id=instance_meta["instance_id"],
        agent_id=instance_meta["agent_id"],
        agent_dir=instance_meta["agent_dir"],
        status=InstanceStatus(instance_meta["status"]),
        parent_id=instance_meta.get("parent_id"),
        children=instance_meta.get("children", []),
        created_at=datetime.fromisoformat(instance_meta["created_at"]).replace(tzinfo=timezone.utc) if isinstance(instance_meta["created_at"], str) else instance_meta["created_at"],
        updated_at=datetime.fromisoformat(instance_meta["updated_at"]).replace(tzinfo=timezone.utc) if instance_meta.get("updated_at") and isinstance(instance_meta["updated_at"], str) else instance_meta.get("updated_at"),
    )


# 3. GET /instances - List instances
@api_router.get("/instances", response_model=InstanceListResponse)
async def list_instances(
    limit: int = 20,
    offset: int = 0
):
    """List instances with pagination.
    
    Args:
        limit: Maximum number of instances to return (default: 20, max: 100).
        offset: Number of instances to skip (default: 0, min: 0).
    """
    # Input validation
    limit = max(1, min(limit, 100))  # Clamp to 1-100
    offset = max(0, offset)  # Ensure non-negative
    
    instances_data, total = manager.list_instances(limit=limit, offset=offset)
    instances = []
    for inst in instances_data:
        instances.append(InstanceInfo(
            instance_id=inst["instance_id"],
            agent_id=inst["agent_id"],
            agent_dir=inst["agent_dir"],
            status=InstanceStatus(inst["status"]),
            parent_id=inst.get("parent_id"),
            children=inst.get("children", []),
            title=inst.get("title"),
            created_at=datetime.fromisoformat(inst["created_at"]).replace(tzinfo=timezone.utc) if isinstance(inst["created_at"], str) else inst["created_at"],
            updated_at=datetime.fromisoformat(inst["updated_at"]).replace(tzinfo=timezone.utc) if inst.get("updated_at") and isinstance(inst["updated_at"], str) else inst.get("updated_at"),
        ))
    
    has_more = (offset + limit) < total
    
    return InstanceListResponse(
        instances=instances,
        total=total,
        limit=limit,
        offset=offset,
        has_more=has_more
    )


# 4. GET /instances/{instance_id} - Get instance info
@api_router.get("/instances/{instance_id}", response_model=InstanceInfo)
async def get_instance(instance_id: str):
    """Get instance information."""
    try:
        instance_meta = manager.get_instance_info(instance_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.INSTANCE_NOT_FOUND,
                message=f"Instance not found: {instance_id}"
            ).model_dump()
        )

    return InstanceInfo(
        instance_id=instance_meta["instance_id"],
        agent_id=instance_meta["agent_id"],
        agent_dir=instance_meta["agent_dir"],
        status=InstanceStatus(instance_meta["status"]),
        parent_id=instance_meta.get("parent_id"),
        children=instance_meta.get("children", []),
        title=instance_meta.get("title"),
        created_at=datetime.fromisoformat(instance_meta["created_at"]) if isinstance(instance_meta["created_at"], str) else instance_meta["created_at"],
        updated_at=datetime.fromisoformat(instance_meta["updated_at"]) if instance_meta.get("updated_at") and isinstance(instance_meta["updated_at"], str) else instance_meta.get("updated_at"),
    )


# 5. DELETE /instances/{instance_id} - Terminate instance
@api_router.delete("/instances/{instance_id}")
async def terminate_instance(instance_id: str):
    """Terminate an instance."""
    # Check instance exists
    try:
        manager.get_instance(instance_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.INSTANCE_NOT_FOUND,
                message=f"Instance not found: {instance_id}"
            ).model_dump()
        )

    await manager.terminate_instance(instance_id)
    
    return {"terminated": True}


@api_router.post("/instances/{instance_id}/stop")
async def stop_instance(instance_id: str) -> dict:
    """Stop an instance by cancelling pending requests."""
    try:
        manager.get_instance(instance_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Instance not found")
    cancelled_count = manager.cancel_instance_requests(instance_id, CancellationReason.USER_STOPPED)
    return {"stopped": True, "cancelled_requests": cancelled_count}


# 6. POST /instances/{instance_id}/messages - Send message
@api_router.post("/instances/{instance_id}/messages", response_model=MessageResponse)
async def send_message(instance_id: str, message: MessageCreate):
    """Send a message to an instance (async via queue)."""
    # Check instance exists
    try:
        manager.get_instance(instance_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.INSTANCE_NOT_FOUND,
                message=f"Instance not found: {instance_id}"
            ).model_dump()
        )

    # Enqueue the message (non-blocking)
    try:
        result = await manager.enqueue_message(
            instance_id=instance_id,
            message=message.content,
            source="api"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                code=ErrorCodes.INTERNAL_ERROR,
                message=f"Failed to enqueue message: {str(e)}"
            ).model_dump()
        )

    # Create response with queued status
    now = datetime.now(timezone.utc)

    return MessageResponse(
        message_id=result.message_id,
        role="assistant",
        content="",  # Response will come async
        thinking=None,
        thinking_extracted=None,
        tool_calls=None,
        created_at=now,
    )


# 7. GET /instances/{instance_id}/messages/{message_id} - Get message status
@api_router.get("/instances/{instance_id}/messages/{message_id}")
async def get_message_status(instance_id: str, message_id: str):
    """Get the status of a queued message."""
    # Check instance exists
    try:
        manager.get_instance(instance_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.INSTANCE_NOT_FOUND,
                message=f"Instance not found: {instance_id}"
            ).model_dump()
        )
    
    # Get queue stats
    stats = manager.get_queue_stats(instance_id)
    
    return {
        "message_id": message_id,
        "instance_id": instance_id,
        "queue_stats": {
            "pending_count": stats.pending_count,
            "processing_count": stats.processing_count,
            "oldest_message_age_seconds": stats.oldest_message_age_seconds,
        }
    }


# 8. GET /instances/{instance_id}/messages - Get message history
@api_router.get("/instances/{instance_id}/messages")
async def get_messages(instance_id: str):
    """Get message history for an instance."""
    # Check instance exists
    try:
        manager.get_instance(instance_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.INSTANCE_NOT_FOUND,
                message=f"Instance not found: {instance_id}"
            ).model_dump()
        )

    # Get message history from LangGraph checkpoints
    return await manager.get_messages(instance_id)


# 9. GET /instances/{instance_id}/events - SSE stream
@api_router.get("/instances/{instance_id}/events")
async def stream_events(instance_id: str, request: Request):
    """SSE stream delivering checkpoint events."""
    if manager.is_shutting_down:
        raise HTTPException(status_code=503, detail="Server is shutting down")
    
    try:
        manager.get_instance(instance_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Instance not found: {instance_id}")
    
    live_hub: LiveEventHub = request.app.state.live_hub
    
    async def event_generator() -> AsyncGenerator[dict, None]:
        # 1. Connected event
        yield {
            "event": "connected",
            "data": json.dumps({"instance_id": instance_id}),
        }
        
        # 2. Create a queue for this connection
        queue: asyncio.Queue = asyncio.Queue(maxsize=50)
        await live_hub.add_connection(instance_id, queue)
        
        try:
            while True:
                if await request.is_disconnected():
                    break
                
                if manager.is_shutting_down:
                    yield {"event": "error", "data": json.dumps({"error": "server_shutdown"})}
                    break
                
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                except asyncio.TimeoutError:
                    yield {"event": "keepalive", "data": "{}"}
                    continue
                
                yield {
                    "event": event["event_type"],
                    "id": event.get("event_id", ""),
                    "data": json.dumps(event),
                }
        finally:
            await live_hub.remove_connection(instance_id, queue)
    
    return EventSourceResponse(event_generator(), ping=30)


# ==================== Source Management Endpoints ====================


# GET /sources - List all sources
@api_router.get("/sources", response_model=SourceListResponse)
async def list_sources():
    """List all configured message sources."""
    sources_data = await asyncio.to_thread(manager._source_repository.list_source_configs)
    sources = []
    for src in sources_data:
        sources.append(SourceInfo(
            source_id=src.source_id,
            source_type=SourceType(src.source_type),
            name=src.name,
            config=src.config,
            enabled=src.enabled,
            status=SourceStatus(src.status),
            error_message=src.error_message,
            created_at=datetime.fromisoformat(src.created_at).replace(tzinfo=timezone.utc) if isinstance(src.created_at, str) else src.created_at,
            updated_at=datetime.fromisoformat(src.updated_at).replace(tzinfo=timezone.utc) if src.updated_at and isinstance(src.updated_at, str) else src.updated_at,
        ))
    return SourceListResponse(sources=sources)


# POST /sources - Create new source
@api_router.post("/sources", response_model=SourceInfo, status_code=201)
async def create_source(source_create: SourceCreate):
    """Create a new message source."""
    # Check if source already exists
    existing = await asyncio.to_thread(manager._source_repository.get_source_config, source_create.source_id)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=ErrorResponse(
                code=ErrorCodes.SOURCE_ALREADY_EXISTS,
                message=f"Source already exists: {source_create.source_id}"
            ).model_dump()
        )
    
    # Validate source type is supported
    supported_types = {"telegram", "webhook", "whatsapp", "discord", "scheduler"}
    if source_create.source_type.value not in supported_types:
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=ErrorCodes.SOURCE_TYPE_NOT_SUPPORTED,
                message=f"Source type not supported: {source_create.source_type}. Supported: {supported_types}"
            ).model_dump()
        )
    
    # For scheduler sources, validate instance_mode in config
    instance_mode = source_create.config.get("instance_mode")
    validated = validate_instance_mode(
        instance_mode=instance_mode,
        config=source_create.config
    )
    final_config = {**source_create.config, **validated}
    
    # If instance_mode is reuse_instance, enforce max_concurrent = 1
    if final_config.get("instance_mode") == "reuse_instance":
        current_max = final_config.get("max_concurrent")
        if current_max is not None and current_max != 1:
            logger.info(f"Adjusting max_concurrent from {current_max} to 1 for reuse_instance mode")
            final_config["max_concurrent"] = 1
    
    # Validate and encrypt credentials
    credentials_json = None
    if source_create.credentials:
        # Validate credentials size
        cred_str = json.dumps(source_create.credentials)
        if len(cred_str) > MAX_CREDENTIALS_SIZE:
            raise HTTPException(
                status_code=400,
                detail=ErrorResponse(
                    code=ErrorCodes.INVALID_REQUEST,
                    message=f"Credentials too large (max {MAX_CREDENTIALS_SIZE} bytes)"
                ).model_dump()
            )
        credentials_json = credential_manager.encrypt(source_create.credentials)
    
    # Create source config using repository
    source = await asyncio.to_thread(
        manager._source_repository.create_source_config,
        source_type=source_create.source_type.value,
        name=source_create.name,
        config=final_config,
        credentials=credentials_json,
        enabled=source_create.enabled,
        source_id=source_create.source_id,
    )
    
    # Auto-start enabled sources (start adapter immediately for running daemon)
    if source.enabled:
        try:
            await manager.source_registry.start_adapter(source.source_id)
        except Exception as e:
            logger.warning(f"Failed to auto-start source {source.source_id}: {e}")
    
    return SourceInfo(
        source_id=source.source_id,
        source_type=SourceType(source.source_type),
        name=source.name,
        config=source.config,
        enabled=source.enabled,
        status=SourceStatus(source.status),
        error_message=source.error_message,
        created_at=datetime.fromisoformat(source.created_at).replace(tzinfo=timezone.utc) if isinstance(source.created_at, str) else source.created_at,
        updated_at=datetime.fromisoformat(source.updated_at).replace(tzinfo=timezone.utc) if source.updated_at and isinstance(source.updated_at, str) else None,
    )


# POST /sources/test - Test source configuration
@api_router.post("/sources/test", response_model=SourceTestResponse)
async def test_source(test_request: SourceTestRequest):
    """Test a source configuration without saving it.
    
    Validates credentials by attempting to connect to the external service.
    """
    from .sources.base import SourceConfig
    
    # Create a temporary config for testing
    temp_config = SourceConfig(
        source_id="test",
        source_type=test_request.source_type.value,
        name="Test",
        config=test_request.config,
        credentials=test_request.credentials,
        enabled=True,
    )
    
    # Get the appropriate adapter class
    if test_request.source_type == SourceType.telegram:
        from .sources.adapters.telegram import TelegramAdapter
        success, message = await TelegramAdapter.test_connection(temp_config)
    elif test_request.source_type == SourceType.webhook:
        # Webhook doesn't require external connection test
        success, message = True, "Webhook sources don't require connection testing"
    elif test_request.source_type == SourceType.whatsapp:
        # WhatsApp not implemented yet
        success, message = False, "WhatsApp adapter not yet implemented"
    elif test_request.source_type == SourceType.discord:
        # Discord not implemented yet
        success, message = False, "Discord adapter not yet implemented"
    elif test_request.source_type == SourceType.scheduler:
        # Scheduler doesn't require external connection test
        success, message = True, "Scheduler sources don't require connection testing"
    else:
        success, message = False, f"Unknown source type: {test_request.source_type}"
    
    return SourceTestResponse(success=success, message=message)


# GET /sources/{source_id} - Get source info
@api_router.get("/sources/{source_id}", response_model=SourceInfo)
async def get_source(source_id: str):
    """Get a specific message source."""
    source = await asyncio.to_thread(manager._source_repository.get_source_config, source_id)
    if not source:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.SOURCE_NOT_FOUND,
                message=f"Source not found: {source_id}"
            ).model_dump()
        )
    
    return SourceInfo(
        source_id=source.source_id,
        source_type=SourceType(source.source_type),
        name=source.name,
        config=source.config,
        enabled=source.enabled,
        status=SourceStatus(source.status),
        error_message=source.error_message,
        created_at=datetime.fromisoformat(source.created_at).replace(tzinfo=timezone.utc),
        updated_at=datetime.fromisoformat(source.updated_at).replace(tzinfo=timezone.utc) if source.updated_at and isinstance(source.updated_at, str) else None,
    )


# PUT /sources/{source_id} - Update source
@api_router.put("/sources/{source_id}", response_model=SourceInfo)
async def update_source(source_id: str, source_update: SourceUpdate):
    """Update a message source configuration."""
    existing = await asyncio.to_thread(manager._source_repository.get_source_config, source_id)
    if not existing:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.SOURCE_NOT_FOUND,
                message=f"Source not found: {source_id}"
            ).model_dump()
        )
    
    # Scheduler sources cannot be enabled/disabled
    if existing.source_type == "scheduler" and source_update.enabled is not None:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "SCHEDULER_SOURCE_UPDATE_NOT_ALLOWED",
                "message": "Scheduler sources manage their own lifecycle and cannot be controlled via API."
            }
        )
    
    # Merge updates
    updated_name = source_update.name if source_update.name is not None else existing.name
    updated_config = source_update.config if source_update.config is not None else existing.config
    updated_enabled = source_update.enabled if source_update.enabled is not None else existing.enabled
    
    # Handle credentials separately (dict from request vs encrypted string from DB)
    credentials_json = None
    if source_update.credentials is not None:
        # New credentials provided - validate and encrypt
        if source_update.credentials:  # Non-empty dict
            cred_str = json.dumps(source_update.credentials)
            if len(cred_str) > MAX_CREDENTIALS_SIZE:
                raise HTTPException(
                    status_code=400,
                    detail=ErrorResponse(
                        code=ErrorCodes.INVALID_REQUEST,
                        message=f"Credentials too large (max {MAX_CREDENTIALS_SIZE} bytes)"
                    ).model_dump()
                )
            credentials_json = credential_manager.encrypt(source_update.credentials)
        # else: empty dict means clear credentials (credentials_json stays None)
    else:
        # Keep existing encrypted credentials
        credentials_json = existing.credentials
    
    # Update source config using repository
    updated = await asyncio.to_thread(
        manager._source_repository.update_source_config,
        source_id=source_id,
        source_type=existing.source_type,
        name=updated_name,
        config=updated_config,
        credentials=credentials_json,
        enabled=updated_enabled,
    )
    
    return SourceInfo(
        source_id=updated.source_id,
        source_type=SourceType(updated.source_type),
        name=updated.name,
        config=updated.config,
        enabled=updated.enabled,
        status=SourceStatus(updated.status),
        error_message=updated.error_message,
        created_at=datetime.fromisoformat(updated.created_at).replace(tzinfo=timezone.utc),
        updated_at=datetime.fromisoformat(updated.updated_at).replace(tzinfo=timezone.utc),
    )


# DELETE /sources/{source_id} - Delete source
@api_router.delete("/sources/{source_id}", response_model=DeleteResponse)
async def delete_source(source_id: str):
    """Delete a message source."""
    # Get source to check type first
    existing = await asyncio.to_thread(manager._source_repository.get_source_config, source_id)
    if not existing:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.SOURCE_NOT_FOUND,
                message=f"Source not found: {source_id}"
            ).model_dump()
        )
    
    # Stop and unregister adapter if running
    try:
        adapter = manager.source_registry.get(source_id)
        if adapter:
            await manager.source_registry.stop_adapter(source_id)
            manager.source_registry.unregister(source_id)
            logger.info(f"Stopped and unregistered adapter: {source_id}")
    except Exception as e:
        logger.warning(f"Failed to stop adapter during delete {source_id}: {e}")
    
    # Delete from database
    result = await asyncio.to_thread(manager._source_repository.delete_source_config, source_id)
    
    return DeleteResponse(deleted=True, message=f"Source {source_id} deleted")


# POST /sources/{source_id}/start - Start a source
@api_router.post("/sources/{source_id}/start", response_model=SourceActionResponse)
async def start_source(source_id: str):
    """Start a message source adapter."""
    from .sources.base import SourceConfig
    
    source = await asyncio.to_thread(manager._source_repository.get_source_config, source_id)
    if not source:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.SOURCE_NOT_FOUND,
                message=f"Source not found: {source_id}"
            ).model_dump()
        )
    
    # Reject lifecycle operations for scheduler sources
    await _reject_scheduler_lifecycle(source_id)
    
    if not source.enabled:
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message=f"Source {source_id} is disabled. Enable it first."
            ).model_dump()
        )
    
    # Check if registry has the source
    if manager.source_registry:
        try:
            # Check if adapter is already registered
            existing_adapter = manager.source_registry.get(source_id)
            
            if existing_adapter is None:
                # Create adapter from config
                source_type = source.source_type
                credentials = source.credentials
                
                # Decrypt credentials if encrypted
                if credentials and isinstance(credentials, str):
                    credentials = credential_manager.decrypt(credentials)
                
                config = SourceConfig(
                    source_id=source.source_id,
                    source_type=source_type,
                    name=source.name,
                    config=source.config or {},
                    credentials=credentials,
                    enabled=source.enabled,
                )
                
                # Create the appropriate adapter
                if source_type == "telegram":
                    from .sources.adapters.telegram import TelegramAdapter
                    # Create callback wrapper that includes source_id
                    async def on_message(msg):
                        await manager.source_registry._handle_message(source_id, msg)
                    adapter = TelegramAdapter(config, on_message)
                else:
                    raise ValueError(f"Source type '{source_type}' adapter not yet implemented")
                
                # Register the adapter
                manager.source_registry.register(adapter)
            
            # Start the adapter
            await manager.source_registry.start_adapter(source_id)
            await asyncio.to_thread(manager._source_repository.update_source_status, source_id, "running")
            return SourceActionResponse(
                source_id=source_id,
                status=SourceStatus.running,
                message=f"Source {source_id} started successfully"
            )
        except Exception as e:
            await asyncio.to_thread(manager._source_repository.update_source_status, source_id, "error", str(e))
            return SourceActionResponse(
                source_id=source_id,
                status=SourceStatus.error,
                message=f"Failed to start source: {str(e)}"
            )
    
    return SourceActionResponse(
        source_id=source_id,
        status=SourceStatus.stopped,
        message="Source registry not available"
    )


# POST /sources/{source_id}/stop - Stop a source
@api_router.post("/sources/{source_id}/stop", response_model=SourceActionResponse)
async def stop_source(source_id: str):
    """Stop a message source adapter."""
    source = await asyncio.to_thread(manager._source_repository.get_source_config, source_id)
    if not source:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.SOURCE_NOT_FOUND,
                message=f"Source not found: {source_id}"
            ).model_dump()
        )
    
    # Reject lifecycle operations for scheduler sources
    await _reject_scheduler_lifecycle(source_id)
    
    # Check if registry has the source
    if manager.source_registry:
        try:
            await manager.source_registry.stop_adapter(source_id)
            await asyncio.to_thread(manager._source_repository.update_source_status, source_id, "stopped")
            return SourceActionResponse(
                source_id=source_id,
                status=SourceStatus.stopped,
                message=f"Source {source_id} stopped successfully"
            )
        except Exception as e:
            await asyncio.to_thread(manager._source_repository.update_source_status, source_id, "error", str(e))
            return SourceActionResponse(
                source_id=source_id,
                status=SourceStatus.error,
                message=f"Failed to stop source: {str(e)}"
            )
    
    await asyncio.to_thread(manager._source_repository.update_source_status, source_id, "stopped")
    return SourceActionResponse(
        source_id=source_id,
        status=SourceStatus.stopped,
        message=f"Source {source_id} marked as stopped"
    )


# ==================== Instance Mapping Endpoints ====================


# GET /sources/{source_id}/mappings - List mappings for a source
@api_router.get("/sources/{source_id}/mappings", response_model=InstanceMappingListResponse)
async def list_mappings(source_id: str):
    """List all instance mappings for a source."""
    # Check source exists
    source = await asyncio.to_thread(manager._source_repository.get_source_config, source_id)
    if not source:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.SOURCE_NOT_FOUND,
                message=f"Source not found: {source_id}"
            ).model_dump()
        )
    
    mappings_data = await asyncio.to_thread(manager._source_repository.list_instance_mappings, source_id)
    mappings = []
    for m in mappings_data:
        mappings.append(InstanceMappingInfo(
            mapping_id=m.mapping_id,
            source_id=m.source_id,
            external_user_id=m.external_user_id,
            agent_instance_id=m.agent_instance_id,
            agent_id=m.agent_id,
            agent_dir=m.agent_dir,
            metadata=m.mapping_metadata,
            last_message_at=datetime.fromisoformat(m.last_message_at).replace(tzinfo=timezone.utc) if m.last_message_at and isinstance(m.last_message_at, str) else m.last_message_at,
            created_at=datetime.fromisoformat(m.created_at).replace(tzinfo=timezone.utc) if isinstance(m.created_at, str) else m.created_at,
        ))
    return InstanceMappingListResponse(mappings=mappings)


# POST /sources/{source_id}/mappings - Create or update a mapping
@api_router.post("/sources/{source_id}/mappings", response_model=InstanceMappingInfo, status_code=201)
async def create_mapping(source_id: str, mapping_create: InstanceMappingCreate):
    """Create an instance mapping for an external user."""
    import uuid
    
    # Validate agent_id
    resolved_agent_id, agent_path = validate_agent_id(mapping_create.agent_id)
    
    # Check source exists
    source = await asyncio.to_thread(manager._source_repository.get_source_config, source_id)
    if not source:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.SOURCE_NOT_FOUND,
                message=f"Source not found: {source_id}"
            ).model_dump()
        )
    
    # Check if mapping already exists
    existing = await asyncio.to_thread(manager._source_repository.get_instance_mapping, source_id, mapping_create.external_user_id)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=ErrorResponse(
                code=ErrorCodes.MAPPING_ALREADY_EXISTS,
                message=f"Mapping already exists for user {mapping_create.external_user_id}"
            ).model_dump()
        )
    
    # Generate IDs (use standard UUID format for consistency)
    mapping_id = f"{source_id}:{mapping_create.external_user_id}"
    # Let manager auto-generate a valid UUID instance_id
    instance_id = None
    
    # Spawn the agent instance
    try:
        instance_id = manager.spawn_instance(
            agent_id=resolved_agent_id,
            instance_id=instance_id,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                code=ErrorCodes.INTERNAL_ERROR,
                message=f"Failed to spawn instance: {str(e)}"
            ).model_dump()
        )
    
    # Save the mapping with rollback on failure
    try:
        await asyncio.to_thread(
            manager._source_repository.create_instance_mapping,
            source_id=source_id,
            external_user_id=mapping_create.external_user_id,
            agent_instance_id=instance_id,
            agent_id=resolved_agent_id,
            agent_dir=str(agent_path),
            metadata=mapping_create.metadata,
            mapping_id=mapping_id,
        )
    except Exception as e:
        # Rollback: terminate the orphaned instance
        try:
            await manager.terminate_instance(instance_id)
        except Exception:
            pass  # Best effort cleanup
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                code=ErrorCodes.INTERNAL_ERROR,
                message=f"Failed to save mapping: {str(e)}"
            ).model_dump()
        )
    
    # Get the saved mapping
    saved = await asyncio.to_thread(manager._source_repository.get_instance_mapping, source_id, mapping_create.external_user_id)
    return InstanceMappingInfo(
        mapping_id=saved.mapping_id,
        source_id=saved.source_id,
        external_user_id=saved.external_user_id,
        agent_instance_id=saved.agent_instance_id,
        agent_id=saved.agent_id,
        agent_dir=saved.agent_dir,
        metadata=saved.mapping_metadata,
        last_message_at=datetime.fromisoformat(saved.last_message_at).replace(tzinfo=timezone.utc) if saved.last_message_at and isinstance(saved.last_message_at, str) else saved.last_message_at,
        created_at=datetime.fromisoformat(saved.created_at).replace(tzinfo=timezone.utc),
    )


# DELETE /sources/{source_id}/mappings/{mapping_id} - Delete a mapping
@api_router.delete("/sources/{source_id}/mappings/{mapping_id}", response_model=DeleteResponse)
async def delete_mapping(source_id: str, mapping_id: str):
    """Delete an instance mapping."""
    # URL decode the mapping_id if needed
    # mapping_id format is "source_id:external_user_id"
    
    result = await asyncio.to_thread(manager._source_repository.delete_instance_mapping, mapping_id)
    if not result.get("deleted"):
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.MAPPING_NOT_FOUND,
                message=f"Mapping not found: {mapping_id}"
            ).model_dump()
        )
    
    return DeleteResponse(deleted=True, message=f"Mapping {mapping_id} deleted")


# ==================== Scheduler-Specific Endpoints ====================


# GET /schedules - List only scheduler sources
@api_router.get("/schedules", response_model=ScheduleListResponse)
async def list_schedules():
    """List all configured scheduler sources.
    
    This endpoint filters sources to only return those with source_type='scheduler'.
    Returns schedules in the format expected by the frontend.
    """
    all_sources = await asyncio.to_thread(manager._source_repository.list_source_configs)
    schedules = []
    for src in all_sources:
        if src.source_type == "scheduler":
            # Calculate next_run_at from adapter if available
            next_run_at = None
            adapter = manager.source_registry.get(src.source_id) if manager.source_registry else None
            if adapter and hasattr(adapter, '_get_next_trigger_time'):
                try:
                    next_run_at = adapter._get_next_trigger_time()
                except Exception:
                    pass
            
            schedules.append(ScheduleInfo(
                id=src.source_id,
                name=src.name,
                config=src.config,
                status=SourceStatus(src.status),
                created_at=datetime.fromisoformat(src.created_at).replace(tzinfo=timezone.utc) if isinstance(src.created_at, str) else src.created_at,
                updated_at=datetime.fromisoformat(src.updated_at).replace(tzinfo=timezone.utc) if src.updated_at and isinstance(src.updated_at, str) else None,
                next_run_at=next_run_at,
            ))
    return ScheduleListResponse(schedules=schedules)


def validate_instance_mode(instance_mode: str | None, schedule_type: str | None = None, config: dict | None = None) -> dict[str, Any]:
    """Validate instance_mode and return processed config.
    
    Args:
        instance_mode: The instance mode to validate ('new_instance', 'reuse_instance', or None).
        schedule_type: The schedule type ('cron', 'interval', 'one_time') if known.
        config: The schedule config dict to potentially modify.
        
    Returns:
        Processed config dict with instance_mode set appropriately.
        
    Raises:
        HTTPException: If instance_mode is invalid.
    """
    VALID_INSTANCE_MODES = {"new_instance", "reuse_instance"}
    default_instance_mode = "new_instance"
    
    # Determine schedule type from config if not provided
    if schedule_type is None and config:
        if "run_at" in config and config["run_at"]:
            schedule_type = "one_time"
        elif "interval_seconds" in config:
            schedule_type = "interval"
        elif "schedule" in config:
            schedule_type = "cron"
    
    # For one_time schedules: ALWAYS force to new_instance
    if schedule_type == "one_time":
        if instance_mode is not None and instance_mode != "new_instance":
            logger.info("Forcing instance_mode to 'new_instance' for one_time schedule")
        return {"instance_mode": "new_instance"}
    
    # Validate instance_mode if provided
    if instance_mode is not None and instance_mode not in VALID_INSTANCE_MODES:
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message=f"Invalid instance_mode: '{instance_mode}'. Valid options: {list(VALID_INSTANCE_MODES)}"
            ).model_dump()
        )
    
    # Use provided value or default
    resolved_mode = instance_mode if instance_mode is not None else default_instance_mode
    
    return {"instance_mode": resolved_mode}


# PUT /schedules/{schedule_id} - Update a schedule
@api_router.put("/schedules/{schedule_id}", response_model=ScheduleInfo)
async def update_schedule(schedule_id: str, schedule_update: ScheduleUpdate):
    """Update a schedule configuration."""
    # Check source exists and is a scheduler
    existing = await asyncio.to_thread(manager._source_repository.get_source_config, schedule_id)
    if not existing:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.SOURCE_NOT_FOUND,
                message=f"Schedule not found: {schedule_id}"
            ).model_dump()
        )
    
    if existing.source_type != "scheduler":
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message=f"Source {schedule_id} is not a scheduler (type: {existing.source_type})"
            ).model_dump()
        )
    
    # Merge updates
    updated_name = schedule_update.name if schedule_update.name is not None else existing.name
    updated_config = schedule_update.config if schedule_update.config is not None else existing.config
    
    # Handle partial config update (merge with existing config)
    if schedule_update.config is not None and existing.config:
        # Merge partial config with existing config
        merged_config = {**existing.config, **schedule_update.config}
        updated_config = merged_config
    
    # Validate and process instance_mode
    instance_mode_config = validate_instance_mode(
        instance_mode=schedule_update.instance_mode,
        config=updated_config
    )
    updated_config["instance_mode"] = instance_mode_config["instance_mode"]
    
    # If instance_mode is reuse_instance, enforce max_concurrent = 1
    if updated_config.get("instance_mode") == "reuse_instance":
        current_max = updated_config.get("max_concurrent")
        if current_max is not None and current_max != 1:
            logger.info(f"Adjusting max_concurrent from {current_max} to 1 for reuse_instance mode")
            updated_config["max_concurrent"] = 1
    
    # Update source config using repository
    updated = await asyncio.to_thread(
        manager._source_repository.update_source_config,
        source_id=schedule_id,
        source_type=existing.source_type,
        name=updated_name,
        config=updated_config,
        credentials=existing.credentials,
        enabled=existing.enabled,
    )
    
    # Calculate next_run_at from adapter if available
    next_run_at = None
    adapter = manager.source_registry.get(updated.source_id)
    if adapter and hasattr(adapter, '_get_next_trigger_time'):
        try:
            next_run_at = adapter._get_next_trigger_time()
        except Exception:
            pass
    
    return ScheduleInfo(
        id=updated.source_id,
        name=updated.name,
        config=updated.config,
        status=SourceStatus(updated.status),
        created_at=datetime.fromisoformat(updated.created_at).replace(tzinfo=timezone.utc) if isinstance(updated.created_at, str) else updated.created_at,
        updated_at=datetime.fromisoformat(updated.updated_at).replace(tzinfo=timezone.utc) if updated.updated_at and isinstance(updated.updated_at, str) else None,
        last_run_at=None,
        next_run_at=next_run_at,
    )


# POST /schedules/{schedule_id}/trigger - Manually trigger a schedule
@api_router.post("/schedules/{schedule_id}/trigger", response_model=ScheduleTriggerResponse)
async def trigger_schedule(schedule_id: str):
    """Manually trigger a scheduled job.
    
    Triggers the schedule immediately, regardless of its configured schedule.
    """
    from .sources.base import SourceConfig
    
    # Check source exists and is a scheduler
    source = await asyncio.to_thread(manager._source_repository.get_source_config, schedule_id)
    if not source:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.SOURCE_NOT_FOUND,
                message=f"Schedule not found: {schedule_id}"
            ).model_dump()
        )
    
    if source.source_type != "scheduler":
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message=f"Source {schedule_id} is not a scheduler (type: {source.source_type})"
            ).model_dump()
        )
    
    # Check if registry has the source
    if not manager.source_registry:
        raise HTTPException(
            status_code=503,
            detail=ErrorResponse(
                code=ErrorCodes.INTERNAL_ERROR,
                message="Source registry not available"
            ).model_dump()
        )
    
    adapter = manager.source_registry.get(schedule_id)
    if not adapter:
        raise HTTPException(
            status_code=503,
            detail=ErrorResponse(
                code=ErrorCodes.INTERNAL_ERROR,
                message=f"Schedule adapter not running: {schedule_id}"
            ).model_dump()
        )
    
    # Trigger the schedule
    try:
        execution_id = await adapter.manual_trigger()
        # Note: Execution is recorded by the scheduler's execution_callback,
        # not here, to avoid duplicate records
        
        return ScheduleTriggerResponse(
            execution_id=execution_id,
            schedule_id=schedule_id,
            message="Schedule triggered successfully"
        )
    except Exception as e:
        logger.error(f"Failed to trigger schedule {schedule_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                code=ErrorCodes.INTERNAL_ERROR,
                message=f"Failed to trigger schedule: {str(e)}"
            ).model_dump()
        )


# POST /schedules/{schedule_id}/start - Start a scheduler
@api_router.post("/schedules/{schedule_id}/start", response_model=SourceActionResponse)
async def start_schedule(schedule_id: str):
    """Start a scheduler source."""
    # Check source exists
    source = await asyncio.to_thread(manager._source_repository.get_source_config, schedule_id)
    if not source:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.SOURCE_NOT_FOUND,
                message=f"Schedule not found: {schedule_id}"
            ).model_dump()
        )
    
    # Verify it's a scheduler source
    if source.source_type != "scheduler":
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message=f"Source {schedule_id} is not a scheduler (type: {source.source_type})"
            ).model_dump()
        )
    
    # Start the scheduler adapter
    try:
        await manager.source_registry.start_adapter(schedule_id)
        adapter = manager.source_registry.get(schedule_id)
        status = adapter.status if adapter else None
        return SourceActionResponse(
            source_id=schedule_id,
            status=status,
            message=f"Scheduler {schedule_id} started successfully"
        )
    except Exception as e:
        logger.error(f"Failed to start scheduler {schedule_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                code=ErrorCodes.INTERNAL_ERROR,
                message=f"Failed to start scheduler: {str(e)}"
            ).model_dump()
        )


# POST /schedules/{schedule_id}/stop - Stop a scheduler
@api_router.post("/schedules/{schedule_id}/stop", response_model=SourceActionResponse)
async def stop_schedule(schedule_id: str):
    """Stop a scheduler source."""
    # Check source exists
    source = await asyncio.to_thread(manager._source_repository.get_source_config, schedule_id)
    if not source:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.SOURCE_NOT_FOUND,
                message=f"Schedule not found: {schedule_id}"
            ).model_dump()
        )
    
    # Verify it's a scheduler source
    if source.source_type != "scheduler":
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message=f"Source {schedule_id} is not a scheduler (type: {source.source_type})"
            ).model_dump()
        )
    
    # Stop the scheduler adapter
    try:
        await manager.source_registry.stop_adapter(schedule_id)
        return SourceActionResponse(
            source_id=schedule_id,
            status=SourceStatus.STOPPED,
            message=f"Scheduler {schedule_id} stopped successfully"
        )
    except Exception as e:
        logger.error(f"Failed to stop scheduler {schedule_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                code=ErrorCodes.INTERNAL_ERROR,
                message=f"Failed to stop scheduler: {str(e)}"
            ).model_dump()
        )


# GET /schedules/{schedule_id}/executions - Get execution history
@api_router.get("/schedules/{schedule_id}/executions", response_model=ScheduleExecutionListResponse)
async def get_schedule_executions(
    schedule_id: str,
    limit: int = 100,
    offset: int = 0
):
    """Get execution history for a scheduled job.
    
    Args:
        schedule_id: The schedule to get executions for.
        limit: Maximum number of executions to return (default: 100).
        offset: Number of executions to skip (default: 0).
    """
    # Check source exists and is a scheduler
    source = await asyncio.to_thread(manager._source_repository.get_source_config, schedule_id)
    if not source:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.SOURCE_NOT_FOUND,
                message=f"Schedule not found: {schedule_id}"
            ).model_dump()
        )
    
    if source.source_type != "scheduler":
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message=f"Source {schedule_id} is not a scheduler (type: {source.source_type})"
            ).model_dump()
        )
    
    # Input validation
    limit = max(1, min(limit, 1000))  # Clamp to 1-1000
    offset = max(0, offset)  # Ensure non-negative
    
    # Get executions from repository
    executions_data = await asyncio.to_thread(
        manager._source_repository.list_schedule_executions,
        schedule_id=schedule_id,
        limit=limit,
        offset=offset
    )
    
    # Get total count (approximate - using len for now)
    # For accurate total, we'd need a count method in the repository
    total = len(executions_data)
    
    executions = []
    for exec_data in executions_data:
        executions.append(ScheduleExecutionInfo(
            execution_id=exec_data.execution_id,
            schedule_id=exec_data.schedule_id,
            triggered_at=datetime.fromisoformat(exec_data.triggered_at).replace(tzinfo=timezone.utc) if isinstance(exec_data.triggered_at, str) else exec_data.triggered_at,
            instance_id=exec_data.instance_id,
            status=exec_data.status,
            error_message=exec_data.error_message,
            completed_at=datetime.fromisoformat(exec_data.completed_at).replace(tzinfo=timezone.utc) if exec_data.completed_at and isinstance(exec_data.completed_at, str) else exec_data.completed_at,
        ))
    
    return ScheduleExecutionListResponse(
        executions=executions,
        total=total
    )


# ==================== Webhook Receiver Endpoint ====================


# POST /webhooks/{source_id} - Receive webhook from external source
@api_router.post("/webhooks/{source_id}")
async def receive_webhook(source_id: str, request: Request):
    """Receive a webhook from an external message source."""
    # Check source exists
    source = await asyncio.to_thread(manager._source_repository.get_source_config, source_id)
    if not source:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.SOURCE_NOT_FOUND,
                message=f"Source not found: {source_id}"
            ).model_dump()
        )
    
    # Check source type is webhook-compatible
    if source.source_type not in ("webhook", "telegram"):
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message=f"Source type {source.source_type} does not support webhooks"
            ).model_dump()
        )
    
    # Verify webhook secret if configured
    source_config = source.config or {}
    configured_secret = source_config.get("webhook_secret")

    if configured_secret:
        provided_secret = request.headers.get("X-Webhook-Secret")
        if not provided_secret or not secrets.compare_digest(provided_secret, configured_secret):
            raise HTTPException(
                status_code=401,
                detail=ErrorResponse(
                    code=ErrorCodes.INVALID_REQUEST,
                    message="Invalid or missing webhook secret"
                ).model_dump()
            )

    # Get the adapter from registry
    if not manager.source_registry:
        raise HTTPException(
            status_code=503,
            detail=ErrorResponse(
                code=ErrorCodes.INTERNAL_ERROR,
                message="Source registry not available"
            ).model_dump()
        )
    
    adapter = manager.source_registry.get(source_id)
    if not adapter:
        raise HTTPException(
            status_code=503,
            detail=ErrorResponse(
                code=ErrorCodes.INTERNAL_ERROR,
                message=f"Source adapter not running: {source_id}"
            ).model_dump()
        )
    
    # Check if adapter supports webhooks
    if not hasattr(adapter, 'handle_webhook'):
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message=f"Source adapter does not support webhooks"
            ).model_dump()
        )
    
    # Parse the webhook payload
    try:
        payload = await request.json()
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message=f"Invalid JSON payload: {str(e)}"
            ).model_dump()
        )
    
    # Get headers
    headers = dict(request.headers)
    
    # Forward to adapter
    try:
        await adapter.handle_webhook(payload, headers)
        return {"received": True, "source_id": source_id}
    except Exception as e:
        # Log but don't expose internal errors
        logger.error(f"Webhook processing error for {source_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                code=ErrorCodes.INTERNAL_ERROR,
                message=f"Webhook processing failed"
            ).model_dump()
        )


# Include API router with /api prefix (must be after all routes are defined)
from daemon.routers.jobs import router as jobs_router
from daemon.routers.projects import router as projects_router
from daemon.routers.queues import router as queues_router
from daemon.routers.dlq import router as dlq_router
api_router.include_router(jobs_router)
api_router.include_router(projects_router)
api_router.include_router(queues_router)
api_router.include_router(dlq_router)
app.include_router(api_router)


# Static file serving for production UI (served at root - catch-all, must be last)
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
