"""Agent management API endpoints."""

import json
import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException

from daemon.models import AgentInfo, AgentListResponse, AgentCreate, ErrorResponse, ErrorCodes
from daemon.registry import get_registry

logger = logging.getLogger(__name__)

# Determine the base path (use working directory for production)
# PyInstaller runs from INSTALL_DIR where frontend/dist is expected
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent.parent.parent

# Create router with /api/agents prefix
router = APIRouter(prefix="/agents", tags=["agents"])


# ==================== Endpoints ====================


@router.get("", response_model=AgentListResponse)
async def list_agents():
    """List all available agents with version information."""
    registry = get_registry()
    grouped = registry.list_all_grouped()

    result = []
    for agent_id, versions in sorted(grouped.items()):
        # C4: Apply _ prefix filter to match pre-refactor router behavior
        if agent_id.startswith("_"):
            continue

        available_tags = sorted(
            [v.version_tag for v in versions],
            key=lambda tag: (tag is not None, tag or ""),
        )
        for meta in versions:
            result.append(AgentInfo(
                id=meta.id,
                name=meta.name,
                description=meta.description,
                icon=meta.icon,
                color=meta.color,
                version=meta.version,
                agent_dir=str(meta.path),
                system=meta.system,
                version_tag=meta.version_tag,
                available_versions=available_tags,
            ))

    return AgentListResponse(agents=result)


@router.post("", response_model=AgentInfo, status_code=201)
async def create_agent(agent_create: AgentCreate):
    """Create a new agent from template."""
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
        
        with open(new_agent_dir / "meta.json", "w", encoding="utf-8") as f:
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


@router.delete("/{agent_id}")
async def delete_agent(agent_id: str):
    """Move an agent to trash (soft delete)."""
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
