"""Project Queue API endpoints."""

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request

from daemon.constants import SYSTEM_DEFAULT_PROJECT_NAME
from daemon.repositories import SQLModelProjectRepository
from daemon.repositories.project import HistoryEntryType
from daemon.services.job_queue_mgmt_service import JobQueueMgmtService
from .schemas import (
    ProjectResponse, ProjectListResponse, ProjectNotFoundResponse, ProjectCreateRequest,
    ProjectHistoryEntryResponse, ProjectHistoryListResponse, ProjectHistoryAddRequest, ProjectHistorySearchResponse,
)

logger = logging.getLogger(__name__)


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


def _get_manager(request: Request) -> Any:
    """Get the InstanceManager from app state.
    
    Args:
        request: FastAPI request object.
        
    Returns:
        InstanceManager instance.
    """
    return request.app.state.manager


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


def _get_critical_notes_safe(repo: SQLModelProjectRepository, project_id: str) -> list[dict]:
    """Fetch critical notes for a project with graceful error handling.
    
    Args:
        repo: The project repository instance.
        project_id: The project ID to fetch notes for.
    
    Returns:
        List of critical note dicts, or empty list on error.
    """
    try:
        notes = repo.list_critical_notes(project_id)
        return [note.to_dict() for note in notes]
    except Exception as e:
        logger.warning(f"Failed to fetch critical notes for project {project_id}: {e}")
        return []


def _project_to_response(
    project,
    recent_history: list[dict] | None = None,
    critical_notes: list[dict] | None = None,
) -> ProjectResponse:
    """Convert Project model to ProjectResponse.
    
    Args:
        project: The project model instance.
        recent_history: Optional list of recent history entries to include.
        critical_notes: Optional list of critical notes to include (fetched from repo).
    
    Returns:
        ProjectResponse instance.
    """
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
        critical_notes=critical_notes or [],  # Use provided notes or empty list
        recent_history=recent_history,
        creator_instance_id=project.creator_instance_id,
        creator_agent_id=project.creator_agent_id,
        created_at=project.created_at,
        updated_at=project.updated_at,
        is_system=(project.name == SYSTEM_DEFAULT_PROJECT_NAME),
    )


def _get_recent_history_safe(repo: SQLModelProjectRepository, project_id: str, limit: int = 10) -> list[dict]:
    """Fetch recent history for a project with graceful error handling.
    
    Args:
        repo: The project repository instance.
        project_id: The project ID to fetch history for.
        limit: Maximum number of history entries to return.
    
    Returns:
        List of recent history entries, or empty list on error.
    """
    try:
        return repo.get_recent_history(project_id, limit=limit)
    except Exception as e:
        logger.warning(f"Failed to fetch recent history for project {project_id}: {e}")
        return []


def _fetch_project_critical_notes(repo: SQLModelProjectRepository, project) -> tuple:
    """Fetch critical notes for a single project.
    
    Args:
        repo: The project repository instance.
        project: Project instance.
    
    Returns:
        Tuple of (project_id, notes_list).
    """
    return project.project_id, _get_critical_notes_safe(repo, project.project_id)

def _fetch_project_history(repo: SQLModelProjectRepository, project) -> tuple:
    """Fetch recent history for a single project.
    
    Args:
        repo: The project repository instance.
        project: Project instance.
    
    Returns:
        Tuple of (project_id, history_list).
    """
    return project.project_id, _get_recent_history_safe(repo, project.project_id)


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
    request: Request,
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
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")
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
    
    recent_history = _get_recent_history_safe(repo, project.project_id)
    critical_notes = _get_critical_notes_safe(repo, project.project_id)
    return _project_to_response(project, recent_history=recent_history, critical_notes=critical_notes)


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
    
    recent_history = _get_recent_history_safe(repo, project_id)
    critical_notes = _get_critical_notes_safe(repo, project_id)
    return _project_to_response(project, recent_history=recent_history, critical_notes=critical_notes)


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
    
    # Fetch recent history for each project in parallel
    history_results = await asyncio.gather(*[asyncio.to_thread(_fetch_project_history, repo, p) for p in projects])
    history_map = dict(history_results)
    
    # Fetch critical notes for each project in parallel
    notes_results = await asyncio.gather(*[asyncio.to_thread(_fetch_project_critical_notes, repo, p) for p in projects])
    notes_map = dict(notes_results)
    
    return ProjectListResponse(
        projects=[_project_to_response(p, recent_history=history_map.get(p.project_id), critical_notes=notes_map.get(p.project_id)) for p in projects],
        total=len(projects)
    )


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
    
    # Fetch recent history for each project in parallel
    history_results = await asyncio.gather(*[asyncio.to_thread(_fetch_project_history, repo, p) for p in projects])
    history_map = dict(history_results)
    
    # Fetch critical notes for each project in parallel
    notes_results = await asyncio.gather(*[asyncio.to_thread(_fetch_project_critical_notes, repo, p) for p in projects])
    notes_map = dict(notes_results)
    
    return ProjectListResponse(
        projects=[_project_to_response(p, recent_history=history_map.get(p.project_id), critical_notes=notes_map.get(p.project_id)) for p in projects],
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
    request: Request,
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
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")
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
    
    recent_history = _get_recent_history_safe(repo, project_id)
    critical_notes = _get_critical_notes_safe(repo, project_id)
    return _project_to_response(updated, recent_history=recent_history, critical_notes=critical_notes)


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
    request: Request,
    repo: SQLModelProjectRepository = Depends(get_project_repository),
) -> ProjectResponse:
    """Pause job queue for a project.
    
    Returns:
        200 with updated project
        404 if project doesn't exist
    """
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")
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
    
    recent_history = _get_recent_history_safe(repo, project_id)
    critical_notes = _get_critical_notes_safe(repo, project_id)
    return _project_to_response(updated, recent_history=recent_history, critical_notes=critical_notes)


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
    request: Request,
    repo: SQLModelProjectRepository = Depends(get_project_repository),
) -> ProjectResponse:
    """Resume job queue for a project.
    
    Returns:
        200 with updated project
        404 if project doesn't exist
    """
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")
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
    
    recent_history = _get_recent_history_safe(repo, project_id)
    critical_notes = _get_critical_notes_safe(repo, project_id)
    return _project_to_response(updated, recent_history=recent_history, critical_notes=critical_notes)


# ==================== Project History Endpoints ====================


@router.get(
    "/{project_id}/history",
    response_model=ProjectHistoryListResponse,
    responses={
        200: {"description": "List of project history entries"},
        404: {"model": ProjectNotFoundResponse, "description": "Project not found"},
    },
)
async def list_project_history(
    project_id: str,
    limit: int = 20,
    offset: int = 0,
    entry_type: str | None = None,
    repo: SQLModelProjectRepository = Depends(get_project_repository),
) -> ProjectHistoryListResponse:
    """List project history entries with optional filtering.
    
    Query params:
        limit: Maximum entries per page (default: 20)
        offset: Number of entries to skip (default: 0)
        entry_type: Optional filter by entry type
    
    Returns:
        200 with paginated list of history entries
        404 if project doesn't exist
    """
    # Validate project exists
    project = await asyncio.to_thread(repo.get, project_id)
    if project is None:
        raise HTTPException(
            status_code=404,
            detail=ProjectNotFoundResponse(
                error="Project not found",
                project_id=project_id
            ).model_dump()
        )
    
    # Validate entry_type if provided
    if entry_type is not None:
        valid_types = {e.value for e in HistoryEntryType}
        if entry_type not in valid_types:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": f"Invalid entry_type '{entry_type}'",
                    "valid_types": list(valid_types),
                }
            )
    
    result = await asyncio.to_thread(
        repo.list_history_entries,
        project_id,
        entry_type=entry_type,
        limit=limit,
        offset=offset,
    )
    
    return ProjectHistoryListResponse(
        entries=[ProjectHistoryEntryResponse(**e) for e in result["entries"]],
        total=result["total"],
        limit=result["limit"],
        offset=result["offset"],
    )


@router.post(
    "/{project_id}/history",
    response_model=ProjectHistoryEntryResponse,
    status_code=201,
    responses={
        201: {"description": "History entry created"},
        400: {"description": "Invalid entry type"},
        404: {"model": ProjectNotFoundResponse, "description": "Project not found"},
    },
)
async def add_project_history(
    project_id: str,
    request: Request,
    payload: ProjectHistoryAddRequest,
    repo: SQLModelProjectRepository = Depends(get_project_repository),
) -> ProjectHistoryEntryResponse:
    """Add a new history entry to a project.
    
    Request body:
        entry_type: Type of history entry (milestone, commit, phase, bugfix, deployment, note, config_change, feature, other)
        summary: Brief summary (max 300 chars)
        details: Optional detailed description (max 5000 chars)
        entry_metadata: Optional metadata dictionary
    
    Returns:
        201 with created history entry
        400 if entry_type is invalid
        404 if project doesn't exist
    """
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")
    # Validate project exists
    project = await asyncio.to_thread(repo.get, project_id)
    if project is None:
        raise HTTPException(
            status_code=404,
            detail=ProjectNotFoundResponse(
                error="Project not found",
                project_id=project_id
            ).model_dump()
        )
    
    # Validate entry_type
    valid_types = {e.value for e in HistoryEntryType}
    if payload.entry_type not in valid_types:
        raise HTTPException(
            status_code=400,
            detail={
                "error": f"Invalid entry_type '{payload.entry_type}'",
                "valid_types": list(valid_types),
            }
        )
    
    entry = await asyncio.to_thread(
        repo.add_history_entry,
        project_id=project_id,
        entry_type=payload.entry_type,
        summary=payload.summary,
        details=payload.details,
        source_agent=None,
        source_instance_id=None,
        entry_metadata=payload.entry_metadata,
    )
    
    return ProjectHistoryEntryResponse(**entry)


@router.get(
    "/{project_id}/history/search",
    response_model=ProjectHistorySearchResponse,
    responses={
        200: {"description": "Search results for project history entries"},
        404: {"model": ProjectNotFoundResponse, "description": "Project not found"},
    },
)
async def search_project_history(
    project_id: str,
    q: str,
    limit: int = 20,
    offset: int = 0,
    repo: SQLModelProjectRepository = Depends(get_project_repository),
) -> ProjectHistorySearchResponse:
    """Search project history entries by query string.
    
    Query params:
        q: Search query (required) - searches in summary and details
        limit: Maximum entries per page (default: 20)
        offset: Number of entries to skip (default: 0)
    
    Returns:
        200 with search results
        404 if project doesn't exist
    """
    # Validate project exists
    project = await asyncio.to_thread(repo.get, project_id)
    if project is None:
        raise HTTPException(
            status_code=404,
            detail=ProjectNotFoundResponse(
                error="Project not found",
                project_id=project_id
            ).model_dump()
        )
    
    result = await asyncio.to_thread(
        repo.search_history_entries,
        project_id,
        query=q,
        limit=limit,
        offset=offset,
    )
    
    return ProjectHistorySearchResponse(
        entries=[ProjectHistoryEntryResponse(**e) for e in result["entries"]],
        total=result["total"],
        limit=result["limit"],
        offset=result["offset"],
        query=result["query"],
    )


@router.delete(
    "/{project_id}/history/{entry_id}",
    status_code=200,
    responses={
        200: {"description": "History entry deleted"},
        404: {"description": "Project or entry not found"},
    },
)
async def delete_project_history(
    project_id: str,
    entry_id: str,
    request: Request,
    repo: SQLModelProjectRepository = Depends(get_project_repository),
) -> dict:
    """Delete a project history entry.
    
    Returns:
        200 with success message
        404 if project or entry not found
    """
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")
    # Validate project exists
    project = await asyncio.to_thread(repo.get, project_id)
    if project is None:
        raise HTTPException(
            status_code=404,
            detail=ProjectNotFoundResponse(
                error="Project not found",
                project_id=project_id
            ).model_dump()
        )
    
    # Fetch entry and validate it belongs to this project
    entry = await asyncio.to_thread(repo.get_history_entry, entry_id)
    if entry is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "History entry not found", "entry_id": entry_id}
        )
    
    if entry["project_id"] != project_id:
        raise HTTPException(
            status_code=404,
            detail={"error": "History entry not found in this project", "entry_id": entry_id, "project_id": project_id}
        )
    
    await asyncio.to_thread(repo.delete_history_entry, entry_id, project_id=project_id)
    
    return {"message": "History entry deleted", "entry_id": entry_id}


@router.delete(
    "/{project_id}",
    status_code=200,
    responses={
        200: {"description": "Project deleted successfully"},
        404: {"description": "Project not found"},
        409: {"description": "Cannot delete project with active instances or running jobs"},
    },
)
async def delete_project(
    project_id: str,
    request: Request,
    force: bool = False,
    repo: SQLModelProjectRepository = Depends(get_project_repository),
) -> dict:
    """Delete a project with full cascade cleanup.
    
    Deletes the project and ALL related data including:
    - Job watches
    - Job locks
    - Dead letter queue items
    - Job queue items
    - Job queues
    - Instances and instance hierarchy
    - Tags, shortnames, metadata, history, critical notes
    
    Also cleans up in-memory state in InstanceManager:
    - instances dict
    - graph tasks
    - request registry
    
    Safety checks (unless force=True):
    - Fails if project has active (non-idle) instances
    - Fails if project has running or processing jobs
    
    Query params:
        force: Bypass safety checks (default: False)
    
    Returns:
        200 with deletion summary
        404 if project not found
        409 if active instances or running jobs exist (and force=False)
    """
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")
    try:
        # BUG 4 FIX: Collect instance IDs BEFORE DB deletion
        # Clean up in-memory state first, then do DB deletion
        instance_ids = []
        if manager and hasattr(manager, '_instance_repository') and manager._instance_repository:
            try:
                # Get all instances for this project from DB (before deletion!)
                instances, _ = manager._instance_repository.list(
                    project_id=project_id,
                    limit=10000,  # Get all
                    offset=0,
                    exclude_kb=False,
                )
                instance_ids = [inst.instance_id for inst in instances]
                
                # Clean up in-memory state using those IDs
                for instance_id in instance_ids:
                    # Cancel active requests
                    if hasattr(manager, '_request_registry') and manager._request_registry:
                        manager._request_registry.cancel_by_instance(instance_id)
                    
                    # Cancel and remove graph task
                    task = manager._graph_tasks.pop(instance_id, None)
                    if task and not task.done():
                        task.cancel()
                    
                    # Remove from instances dict
                    if instance_id in manager.instances:
                        del manager.instances[instance_id]
                    
                    logger.info(f"Cleaned up in-memory state for instance {instance_id[:8]}...")
            except Exception as e:
                logger.warning(f"Failed to clean up in-memory state for project {project_id}: {e}")
        
        # Now perform DB deletion (instances already cleaned from memory)
        result = await asyncio.to_thread(
            repo.delete,
            project_id,
            force=force,
        )
        
        return result
    except ValueError as e:
        if "not found" in str(e).lower():
            raise HTTPException(
                status_code=404,
                detail=ProjectNotFoundResponse(
                    error=str(e),
                    project_id=project_id
                ).model_dump()
            )
        raise HTTPException(
            status_code=400,
            detail={"error": str(e)}
        )
    except RuntimeError as e:
        raise HTTPException(
            status_code=409,
            detail={"error": str(e)}
        )
