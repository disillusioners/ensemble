"""Project Queue API endpoints."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from daemon.repositories import SQLModelProjectRepository
from .schemas import ProjectResponse, ProjectListResponse, ProjectNotFoundResponse


router = APIRouter(prefix="/projects", tags=["projects"])

# Dependency to get SQLModelProjectRepository
# This will be set up in daemon/api.py during app initialization
_project_repo: Optional[SQLModelProjectRepository] = None


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


def _project_to_response(project) -> ProjectResponse:
    """Convert Project model to ProjectResponse."""
    return ProjectResponse(
        project_id=project.project_id,
        name=project.name,
        project_type=project.project_type,
        status=project.status,
        main_directory=project.main_directory,
        related_directories=project.related_directories,
        description=project.description,
        job_queue_paused=project.job_queue_paused,
        tags=project.tags,
        shortnames=project.shortnames,
        metadata=project.project_metadata,
        relationships=project.relationships,
        creator_session_id=project.creator_session_id,
        creator_agent_id=project.creator_agent_id,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


# ==================== Endpoints ====================


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
    project = repo.get(project_id)
    
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
    repo: SQLModelProjectRepository = Depends(get_project_repository),
) -> ProjectListResponse:
    """List all projects.
    
    Returns:
        200 with list of projects
    """
    projects = repo.list_projects()
    
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
    repo: SQLModelProjectRepository = Depends(get_project_repository),
) -> ProjectListResponse:
    """List all projects (trailing slash variant)."""
    projects = repo.list_projects()
    
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
    project = repo.get(project_id)
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
    updated = repo.update(project_id, job_queue_paused=paused)
    
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
    project = repo.get(project_id)
    if project is None:
        raise HTTPException(
            status_code=404,
            detail=ProjectNotFoundResponse(
                error="Project not found",
                project_id=project_id
            ).model_dump()
        )
    
    updated = repo.update(project_id, job_queue_paused=True)
    
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
    project = repo.get(project_id)
    if project is None:
        raise HTTPException(
            status_code=404,
            detail=ProjectNotFoundResponse(
                error="Project not found",
                project_id=project_id
            ).model_dump()
        )
    
    updated = repo.update(project_id, job_queue_paused=False)
    
    if updated is None:
        raise HTTPException(
            status_code=500,
            detail={"error": "Failed to update project", "message": "Update operation returned None"}
        )
    
    return _project_to_response(updated)
