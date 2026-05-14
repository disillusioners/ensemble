"""Project Queue API endpoints."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks

from daemon.constants import SYSTEM_DEFAULT_PROJECT_NAME
from daemon.repositories import SQLModelProjectRepository
from daemon.services.job_queue_mgmt_service import JobQueueMgmtService
from .schemas import ProjectResponse, ProjectListResponse, ProjectNotFoundResponse, ProjectCreateRequest


router = APIRouter(prefix="/projects", tags=["projects"])

# Dependency to get SQLModelProjectRepository
# This will be set up in daemon/api.py during app initialization
_project_repo: SQLModelProjectRepository | None = None


def get_project_repository() -> SQLModelProjectRepository:
    """Get the SQLModelProjectRepository instance.
    
    Returns:
        SQLModelProjectRepository instance.
        
    Raises:
        HTTPException: If the repository is not initialized.
    """
    if _project_repo is None:
        raise HTTPException(
            status_code=503,
            detail={"error": "Project repository not initialized"}
        )
    return _project_repo


def set_project_repository(repo: SQLModelProjectRepository) -> None:
    """Set the SQLModelProjectRepository instance (called during app startup)."""
    global _project_repo
    _project_repo = repo


# Dependency to get JobQueueMgmtService
_job_queue_mgmt_service: JobQueueMgmtService | None = None


def get_queue_mgmt_service() -> JobQueueMgmtService:
    """Get the JobQueueMgmtService instance.
    
    Returns:
        JobQueueMgmtService instance.
        
    Raises:
        HTTPException: If the service is not initialized.
    """
    if _job_queue_mgmt_service is None:
        raise HTTPException(
            status_code=503,
            detail={"error": "Queue management service not initialized"}
        )
    return _job_queue_mgmt_service


def set_job_queue_mgmt_service(service: JobQueueMgmtService) -> None:
    """Set the JobQueueMgmtService instance (called during app startup)."""
    global _job_queue_mgmt_service
    _job_queue_mgmt_service = service


def _project_to_response(project) -> ProjectResponse:
    """Convert Project model to ProjectResponse."""
    return ProjectResponse(
        project_id=project.project_id,
        name=project.name,
        project_type=project.project_type,
        status=project.status,
        main_directory=project.main_directory,
        related_directories=project.related_directories or [],
        description=project.description,
        job_queue_paused=project.job_queue_paused,
        tags=project.tags or [],
        shortnames=project.shortnames or [],
        metadata=project.project_metadata or {},
        relationships=project.relationships or {},
        creator_instance_id=project.creator_instance_id,
        creator_agent_id=project.creator_agent_id,
        created_at=project.created_at,
        updated_at=project.updated_at,
        is_system=(project.name == SYSTEM_DEFAULT_PROJECT_NAME),
    )


# ==================== Endpoints ====================


@router.post(
    "",
    response_model=ProjectResponse,
    status_code=201,
    responses={
        201: {"description": "Project created"},
        409: {"description": "Project with this name already exists"},
    },
)
async def create_project(
    body: ProjectCreateRequest,
    background_tasks: BackgroundTasks,
    repo: SQLModelProjectRepository = Depends(get_project_repository),
) -> ProjectResponse:
    """Create a new project with auto-provisioned system queues.
    
    Creates system queues (system_fifo_queue, system_parallel_queue) via
    BackgroundTasks to avoid blocking the response.
    
    Request body:
        - name: Project name (required, unique)
        - project_type: Project type (default: "general")
        - main_directory: Main directory path (optional)
        - description: Project description (optional)
        - tags: List of tags (optional)
        
    Returns:
        201 with created project
        409 if project name already exists
    """
    try:
        project = await asyncio.to_thread(
            repo.create,
            name=body.name,
            project_type=body.project_type,
            main_directory=body.main_directory,
            description=body.description,
            tags=body.tags,
        )
    except ValueError as e:
        if "already exists" in str(e):
            raise HTTPException(
                status_code=409,
                detail={"error": str(e)}
            )
        raise HTTPException(
            status_code=400,
            detail={"error": str(e)}
        )
    
    # Auto-provision system queues in background
    queue_mgmt = get_queue_mgmt_service()
    background_tasks.add_task(
        queue_mgmt.auto_provision_system_queues,
        project.project_id
    )
    
    return _project_to_response(project)


@router.get(
    "/{project_id}",
    response_model=ProjectResponse,
    responses={
        200: {"description": "Project details"},
        404: {"model": ProjectNotFoundResponse, "description": "Project not found"},
    },
)
async def get_project(
    project_id: str,
    repo: SQLModelProjectRepository = Depends(get_project_repository),
) -> ProjectResponse:
    """Get project details including queue pause state.
    
    Returns:
        200 with project details
        404 if project doesn't exist
    """
    project = await asyncio.to_thread(repo.get, project_id)
    
    if project is None:
        raise HTTPException(
            status_code=404,
            detail=ProjectNotFoundResponse(
                error="Project not found",
                project_id=project_id
            ).model_dump()
        )
    
    return _project_to_response(project)


@router.get(
    "",
    response_model=ProjectListResponse,
)
async def list_projects(
    exclude_system: bool = False,
    repo: SQLModelProjectRepository = Depends(get_project_repository),
) -> ProjectListResponse:
    """List all projects.
    
    Query params:
        exclude_system: If True, excludes the system default project from results (default: False)
    
    Returns:
        200 with list of projects
    """
    projects = await asyncio.to_thread(repo.list_projects)
    
    if exclude_system:
        projects = [p for p in projects if p.name != SYSTEM_DEFAULT_PROJECT_NAME]
    
    return ProjectListResponse(
        projects=[_project_to_response(p) for p in projects],
        total=len(projects)
    )


# Also support trailing slash for compatibility
@router.get(
    "/",
    response_model=ProjectListResponse,
    include_in_schema=False,
)
async def list_projects_trailing(
    exclude_system: bool = False,
    repo: SQLModelProjectRepository = Depends(get_project_repository),
) -> ProjectListResponse:
    """List all projects (trailing slash variant)."""
    projects = await asyncio.to_thread(repo.list_projects)
    
    if exclude_system:
        projects = [p for p in projects if p.name != SYSTEM_DEFAULT_PROJECT_NAME]
    
    return ProjectListResponse(
        projects=[_project_to_response(p) for p in projects],
        total=len(projects)
    )


@router.patch(
    "/{project_id}/queue-status",
    response_model=ProjectResponse,
    responses={
        200: {"description": "Project queue status updated"},
        400: {"description": "Missing 'paused' field"},
        404: {"model": ProjectNotFoundResponse, "description": "Project not found"},
    },
)
async def set_queue_status(
    project_id: str,
    body: dict,
    repo: SQLModelProjectRepository = Depends(get_project_repository),
) -> ProjectResponse:
    """Pause or resume job queue for a project.
    
    Request body:
        - paused: boolean (required)
    
    Returns:
        200 with updated project
        400 if 'paused' field is missing
        404 if project doesn't exist
    """
    # Check project exists
    project = await asyncio.to_thread(repo.get, project_id)
    if project is None:
        raise HTTPException(
            status_code=404,
            detail=ProjectNotFoundResponse(
                error="Project not found",
                project_id=project_id
            ).model_dump()
        )
    
    # Validate 'paused' field
    paused = body.get("paused")
    if paused is None:
        raise HTTPException(
            status_code=400,
            detail={"error": "'paused' field required", "message": "Request body must include 'paused' boolean field"}
        )
    
    # Update the job_queue_paused field
    updated = await asyncio.to_thread(repo.update, project_id, job_queue_paused=paused)
    
    if updated is None:
        raise HTTPException(
            status_code=500,
            detail={"error": "Failed to update project", "message": "Update operation returned None"}
        )
    
    return _project_to_response(updated)


@router.post(
    "/{project_id}/pause-queue",
    response_model=ProjectResponse,
    responses={
        200: {"description": "Queue paused"},
        404: {"model": ProjectNotFoundResponse, "description": "Project not found"},
    },
)
async def pause_queue(
    project_id: str,
    repo: SQLModelProjectRepository = Depends(get_project_repository),
) -> ProjectResponse:
    """Pause job queue for a project.
    
    Returns:
        200 with updated project
        404 if project doesn't exist
    """
    project = await asyncio.to_thread(repo.get, project_id)
    if project is None:
        raise HTTPException(
            status_code=404,
            detail=ProjectNotFoundResponse(
                error="Project not found",
                project_id=project_id
            ).model_dump()
        )
    
    updated = await asyncio.to_thread(repo.update, project_id, job_queue_paused=True)
    
    if updated is None:
        raise HTTPException(
            status_code=500,
            detail={"error": "Failed to update project", "message": "Update operation returned None"}
        )
    
    return _project_to_response(updated)


@router.post(
    "/{project_id}/resume-queue",
    response_model=ProjectResponse,
    responses={
        200: {"description": "Queue resumed"},
        404: {"model": ProjectNotFoundResponse, "description": "Project not found"},
    },
)
async def resume_queue(
    project_id: str,
    repo: SQLModelProjectRepository = Depends(get_project_repository),
) -> ProjectResponse:
    """Resume job queue for a project.
    
    Returns:
        200 with updated project
        404 if project doesn't exist
    """
    project = await asyncio.to_thread(repo.get, project_id)
    if project is None:
        raise HTTPException(
            status_code=404,
            detail=ProjectNotFoundResponse(
                error="Project not found",
                project_id=project_id
            ).model_dump()
        )
    
    updated = await asyncio.to_thread(repo.update, project_id, job_queue_paused=False)
    
    if updated is None:
        raise HTTPException(
            status_code=500,
            detail={"error": "Failed to update project", "message": "Update operation returned None"}
        )
    
    return _project_to_response(updated)
