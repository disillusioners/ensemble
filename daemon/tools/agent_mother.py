"""Agent management tools for the Mother agent.

These tools allow the Mother agent to create, modify, list, and delete other agents.
They are only available to the Mother agent (agents/_mother/).
"""

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from langchain_core.tools import tool

if TYPE_CHECKING:
    from ..manager import SessionManager

# Base directory for agents
BASE_DIR = Path(__file__).parent.parent.parent


def create_mother_tools(manager: "SessionManager", current_session_id: str):
    """Create agent management tools for the Mother agent.
    
    Args:
        manager: The SessionManager instance
        current_session_id: The current session ID
        
    Returns:
        List of tool functions for agent management
    """
    
    @tool
    def agent_list(include_system: bool = False) -> list[dict]:
        """List all available agents.
        
        Args:
            include_system: If True, include system agents (starting with _)
        
        Returns:
            List of agent info dictionaries with id, name, description, purpose
        """
        agents_dir = BASE_DIR / "agents"
        agents = []
        
        if not agents_dir.exists():
            return []
        
        for agent_path in sorted(agents_dir.iterdir()):
            # Skip non-directories
            if not agent_path.is_dir():
                continue
            
            # Skip system agents unless explicitly requested
            if agent_path.name.startswith("_") and not include_system:
                continue
            
            # Skip _trash
            if agent_path.name == "_trash":
                continue
            
            agent_info = {
                "id": agent_path.name,
                "path": str(agent_path),
            }
            
            # Load meta.json if exists
            meta_path = agent_path / "meta.json"
            if meta_path.exists():
                try:
                    with open(meta_path, "r") as f:
                        meta = json.load(f)
                    agent_info["name"] = meta.get("name", agent_path.name)
                    agent_info["description"] = meta.get("description", "")
                    agent_info["icon"] = meta.get("icon", "🤖")
                except (json.JSONDecodeError, KeyError):
                    agent_info["name"] = agent_path.name
            else:
                agent_info["name"] = agent_path.name
            
            # Extract purpose from soul.md
            soul_path = agent_path / "soul.md"
            if soul_path.exists():
                try:
                    content = soul_path.read_text()
                    # Look for purpose line
                    for line in content.split("\n"):
                        if "purpose" in line.lower() and ":" in line:
                            agent_info["purpose"] = line.split(":", 1)[-1].strip()
                            break
                except Exception:
                    pass
            
            agents.append(agent_info)
        
        return agents
    
    @tool
    def agent_create(
        name: str,
        purpose: str,
        personality: str = "helpful and professional",
        workflow: str | None = None,
        rules_must: list[str] | None = None,
        rules_must_not: list[str] | None = None,
        tools_extra: list[str] | None = None,
        description: str = "",
        icon: str = "🤖",
        color: str = "accent-blue",
    ) -> dict:
        """Create a new agent with the specified configuration.
        
        Args:
            name: Agent identifier (lowercase, underscores, e.g., "code_reviewer")
            purpose: What the agent does - its main goal
            personality: How the agent communicates (e.g., "friendly", "formal")
            workflow: Optional workflow steps the agent should follow
            rules_must: List of things the agent must always do
            rules_must_not: List of things the agent must never do
            tools_extra: Additional tools the agent needs (beyond basics)
            description: Human-readable description for meta.json
            icon: Emoji icon for the agent
            color: Color theme for the agent UI
        
        Returns:
            Dict with success status and agent info, or error message
        """
        # Validate name
        if not name.replace("_", "").replace("-", "").isalnum():
            return {
                "success": False,
                "error": "Agent name must contain only alphanumeric characters, hyphens, and underscores"
            }
        
        if name.startswith("_"):
            return {
                "success": False,
                "error": "Agent name cannot start with underscore (reserved for system agents)"
            }
        
        agents_dir = BASE_DIR / "agents"
        template_dir = agents_dir / "_baby_template"
        new_agent_dir = agents_dir / name
        
        # Check if agent already exists
        if new_agent_dir.exists():
            return {
                "success": False,
                "error": f"Agent '{name}' already exists"
            }
        
        # Check template exists
        if not template_dir.exists():
            return {
                "success": False,
                "error": "Agent template (_baby_template) not found"
            }
        
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
                "id": name,
                "name": name.replace("_", " ").title(),
                "description": description or purpose,
                "icon": icon,
                "color": color,
                "version": "1.0.0",
                "created_by": "mother",
                "created_at": datetime.now().isoformat(),
            }
            
            with open(new_agent_dir / "meta.json", "w") as f:
                json.dump(meta, f, indent=2)
            
            # Create empty directories
            (new_agent_dir / "history").mkdir(exist_ok=True)
            (new_agent_dir / "memories").mkdir(exist_ok=True)
            
            # Customize soul.md
            soul_content = f"""# Who I Am

**My name:** {name.replace("_", " ").title()}

**My purpose:** {purpose}

**My personality:** {personality}

I learn and grow through experience.
"""
            with open(new_agent_dir / "soul.md", "w") as f:
                f.write(soul_content)
            
            # Customize workflow.md if provided
            if workflow:
                workflow_content = f"""# Workflow

## Task Processing

{workflow}

---

## Decision Points

- If uncertain → ask for clarification
- If blocked → report blocker and suggest alternatives
- If task complete → summarize and record learnings
"""
                with open(new_agent_dir / "workflow.md", "w") as f:
                    f.write(workflow_content)
            
            # Customize rule.md if rules provided
            if rules_must or rules_must_not:
                rule_content = "# Rules\n\n"
                if rules_must:
                    rule_content += "## Must\n"
                    for rule in rules_must:
                        rule_content += f"- {rule}\n"
                    rule_content += "\n"
                if rules_must_not:
                    rule_content += "## Must Not\n"
                    for rule in rules_must_not:
                        rule_content += f"- {rule}\n"
                with open(new_agent_dir / "rule.md", "w") as f:
                    f.write(rule_content)
            
            # Customize tools.md if extra tools specified
            if tools_extra:
                tools_content = f"""# Tools

## Special Tools

This agent has access to these additional tools:

{chr(10).join(f'- {t}' for t in tools_extra)}

---

*Common tools (bash, time, read_file, list_directory, glob_files, inner_soul) are automatically loaded.*
"""
                with open(new_agent_dir / "tools.md", "w") as f:
                    f.write(tools_content)
            
            return {
                "success": True,
                "agent": {
                    "id": name,
                    "name": name.replace("_", " ").title(),
                    "purpose": purpose,
                    "path": str(new_agent_dir),
                }
            }
            
        except Exception as e:
            # Cleanup on failure
            if new_agent_dir.exists():
                shutil.rmtree(new_agent_dir)
            return {
                "success": False,
                "error": f"Failed to create agent: {str(e)}"
            }
    
    @tool
    def agent_read(agent_name: str, file: str = "soul.md") -> dict:
        """Read an agent's file contents.
        
        Args:
            agent_name: The agent identifier (e.g., "coder", "leader", "_mother")
            file: The file to read (soul.md, workflow.md, rule.md, user.md, memory.md, tools.md)
        
        Returns:
            Dict with success status and file content, or error message
        """
        # Protect system agents except _mother (self-read) and _inner_soul (mother can modify)
        if agent_name.startswith("_") and agent_name not in ("_mother", "_inner_soul"):
            return {
                "success": False,
                "error": "Cannot read system agents except _mother and _inner_soul"
            }
        
        valid_files = ["soul.md", "workflow.md", "rule.md", "user.md", "memory.md", "tools.md", "growth.md", "meta.json"]
        if file not in valid_files:
            return {
                "success": False,
                "error": f"Invalid file. Must be one of: {', '.join(valid_files)}"
            }
        
        agent_dir = BASE_DIR / "agents" / agent_name
        file_path = agent_dir / file
        
        if not agent_dir.exists():
            return {
                "success": False,
                "error": f"Agent '{agent_name}' not found"
            }
        
        if not file_path.exists():
            return {
                "success": False,
                "error": f"File '{file}' not found in agent '{agent_name}'"
            }
        
        try:
            content = file_path.read_text()
            return {
                "success": True,
                "agent": agent_name,
                "file": file,
                "content": content,
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to read file: {str(e)}"
            }
    
    @tool
    def agent_modify(agent_name: str, file: str, content: str) -> dict:
        """Modify an agent's file contents.
        
        Args:
            agent_name: The agent identifier (e.g., "coder", "leader", "_mother")
            file: The file to modify (soul.md, workflow.md, rule.md, user.md, memory.md)
            content: The new content for the file
        
        Returns:
            Dict with success status, or error message
        """
        # Protect system agents except _mother (self-mod) and _inner_soul (mother can modify)
        if agent_name.startswith("_") and agent_name not in ("_mother", "_inner_soul"):
            return {
                "success": False,
                "error": "Cannot modify system agents except _mother and _inner_soul"
            }
        
        # Only allow modifying specific files
        modifiable_files = ["soul.md", "workflow.md", "rule.md", "user.md", "memory.md", "tools.md"]
        if file not in modifiable_files:
            return {
                "success": False,
                "error": f"Cannot modify '{file}'. Allowed files: {', '.join(modifiable_files)}"
            }
        
        agent_dir = BASE_DIR / "agents" / agent_name
        file_path = agent_dir / file
        
        if not agent_dir.exists():
            return {
                "success": False,
                "error": f"Agent '{agent_name}' not found"
            }
        
        try:
            # Write new content
            file_path.write_text(content)
            
            # Invalidate prompt cache so changes take effect immediately
            manager.prompt_cache.invalidate(agent_dir)
            
            return {
                "success": True,
                "agent": agent_name,
                "file": file,
                "message": f"Updated {file} for agent '{agent_name}'",
                "cache_invalidated": True,
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to modify file: {str(e)}"
            }
    
    @tool
    def agent_delete(agent_name: str, confirm: bool = False) -> dict:
        """Delete an agent (move to _trash).
        
        Args:
            agent_name: The agent identifier to delete
            confirm: Must be True to actually delete (safety check)
        
        Returns:
            Dict with success status, or error message
        """
        # Protect system agents
        if agent_name.startswith("_"):
            return {
                "success": False,
                "error": "Cannot delete system agents (starting with _)"
            }
        
        if not confirm:
            return {
                "success": False,
                "error": "Must set confirm=True to delete. This action moves the agent to _trash.",
                "warning": f"This will delete agent '{agent_name}'. Set confirm=True to proceed."
            }
        
        agents_dir = BASE_DIR / "agents"
        agent_dir = agents_dir / agent_name
        trash_dir = agents_dir / "_trash"
        
        if not agent_dir.exists():
            return {
                "success": False,
                "error": f"Agent '{agent_name}' not found"
            }
        
        try:
            # Create trash directory if needed
            trash_dir.mkdir(exist_ok=True)
            
            # Generate unique trash name with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            trash_name = f"{agent_name}_{timestamp}"
            trash_path = trash_dir / trash_name
            
            # Move to trash
            shutil.move(str(agent_dir), str(trash_path))
            
            return {
                "success": True,
                "agent": agent_name,
                "message": f"Agent '{agent_name}' moved to _trash/{trash_name}",
                "trash_path": str(trash_path),
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to delete agent: {str(e)}"
            }
    
    return [
        agent_list,
        agent_create,
        agent_read,
        agent_modify,
        agent_delete,
    ]
