"""FastAPI application setup for the Ensemble Daemon."""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .. import __version__
from ..config import load_config
from ..manager import InstanceManager
from ..models import ErrorResponse, ErrorCodes
from ..persistence import get_checkpointer
from ..repositories import create_job_repository
from ..repositories.job_queue.dead_letter_repository import DeadLetterRepository
from ..repositories.job_queue.lock_repository import LockRepository
from ..repositories.job_queue.queue_repository import JobQueueRepository
from ..repositories.instance.repository import SQLModelInstanceRepository
from ..services.dead_letter_service import DeadLetterService
from ..services.dispatch_event_bus import DispatchEventBus
from ..services.job_lock_manager import JobLockManager
from ..services.job_processor import JobProcessor
from ..services.job_queue_mgmt_service import JobQueueMgmtService
from ..services.job_queue_service import JobQueueService
from ..services.job_recovery_service import JobRecoveryService
from ..sources.credentials import CredentialManager

from .middleware import SelectiveAccessLogMiddleware
from . import routes

logger = logging.getLogger(__name__)

# Global state
manager = None
start_time = None
credential_manager = CredentialManager()
job_queue_service = None
job_processor = None
job_queue_mgmt_service = None
retry_scheduler = None
dispatch_event_bus = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global manager, start_time, job_queue_service, job_processor, job_queue_mgmt_service, retry_scheduler, dispatch_event_bus
    
    config = load_config()
    manager = InstanceManager(config)
    await manager.initialize()
    
    manager.setup_worker_pool(num_workers=4)
    start_time = time.time()
    
    # Update global state in routes module
    routes.manager = manager
    routes.start_time = start_time
    
    job_repository = create_job_repository(engine=manager._engine, create_tables=True)
    
    lock_repo = LockRepository(engine=manager._engine)
    job_lock_manager = JobLockManager(lock_repo=lock_repo)
    
    queue_repo = JobQueueRepository(engine=manager._engine)
    
    job_queue_mgmt_service = JobQueueMgmtService(
        queue_repo=queue_repo,
        job_repo=job_repository,
    )
    
    dispatch_event_bus = DispatchEventBus()
    dispatch_event_bus.set_event_loop(asyncio.get_running_loop())
    
    job_queue_mgmt_service._dispatch_bus = dispatch_event_bus
    
    job_queue_service = JobQueueService(
        repository=job_repository,
        lock_manager=job_lock_manager,
        queue_repo=queue_repo,
        instance_manager=manager,
    )
    
    job_queue_service.set_event_loop(asyncio.get_running_loop())
    job_queue_service.set_config(config.job_system)
    job_queue_service.set_dispatch_bus(dispatch_event_bus)
    
    from daemon.routers.jobs import set_job_queue_service
    set_job_queue_service(job_queue_service)
    
    from daemon.routers.projects import set_project_repository, set_job_queue_mgmt_service
    set_project_repository(manager._project_repository)
    set_job_queue_mgmt_service(job_queue_mgmt_service)
    
    from daemon.routers.queues import set_job_queue_mgmt_service
    set_job_queue_mgmt_service(job_queue_mgmt_service)
    
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
    
    from daemon.services.job_retry_engine import JobRetryEngine
    retry_engine = JobRetryEngine(
        job_repo=job_repository,
        queue_repo=queue_repo,
        dlq_service=dead_letter_service,
        config=config.job_system,
    )
    
    job_queue_service.set_retry_engine(retry_engine)
    
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
    
    manager.set_job_queue_service(job_queue_service)
    manager.set_job_queue_mgmt_service(job_queue_mgmt_service)
    manager.set_dead_letter_service(dead_letter_service)
    job_queue_service.set_instance_manager(manager)
    job_queue_service.set_project_repo(manager._project_repository)
    
    instance_repo = SQLModelInstanceRepository(engine=manager._engine)
    job_recovery = JobRecoveryService(
        job_repository=job_repository,
        lock_repository=lock_repo,
        instance_repository=instance_repo,
    )
    recovery_stats = await job_recovery.recover_on_startup()
    logger.info(f"Job recovery: {recovery_stats}")
    
    from daemon.services.job_feedback_observer import JobFeedbackObserver
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
    
    app.state.live_hub = manager._live_hub
    
    try:
        projects = await asyncio.to_thread(manager._project_repository.list_projects)
        for project in projects:
            await job_queue_mgmt_service.auto_provision_system_queues(project.project_id)
        logger.info(f"Auto-provisioned system queues for {len(projects)} projects")
    except Exception as e:
        logger.warning(f"Failed to auto-provision system queues: {e}")
    
    await manager.start_sources()
    
    yield
    
    if retry_scheduler is not None:
        await retry_scheduler.stop()
    
    if 'job_feedback_observer' in locals():
        await job_feedback_observer.stop()
    
    await job_processor.stop()
    
    if hasattr(app.state, 'live_hub'):
        await app.state.live_hub.shutdown()
    
    await manager.shutdown()


app = FastAPI(
    title="Ensemble Daemon",
    version=__version__,
    lifespan=lifespan
)

logger.info(f"Starting Ensemble v{__version__}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(SelectiveAccessLogMiddleware)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            code=ErrorCodes.INTERNAL_ERROR,
            message=str(exc)
        ).model_dump()
    )


from daemon.routers.jobs import router as jobs_router
from daemon.routers.projects import router as projects_router
from daemon.routers.queues import router as queues_router
from daemon.routers.dlq import router as dlq_router

routes.api_router.include_router(jobs_router)
routes.api_router.include_router(projects_router)
routes.api_router.include_router(queues_router)
routes.api_router.include_router(dlq_router)
app.include_router(routes.api_router)

routes.setup_static_routes(app)
