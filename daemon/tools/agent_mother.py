"""Agent management tools for the Mother agent.

These tools allow the Mother agent to create, modify, list, and delete other agents.
They are only available to the Mother agent (agents/_mother/).

agent-prompt-immutability Phase B (T8/T9) — write-path regime
-------------------------------------------------------------

``agent_modify`` is no longer a direct file writer for prompt
artifacts. Two regimes:

* **DB submission** (``soul.md``, ``rule.md``, ``workflow.md``,
  ``tools_note.md``, ``tools.md``, ``skill.md``, and ``user.md``
  per decisions.md D5) — the full content is submitted via
  ``prompt_version_service.create_and_submit`` (A5 composite) and
  lands as a ``pending_approval`` row keyed
  ``(agent_id, version_tag, artifact)``. The file on disk is NOT
  touched; the draft takes effect only after a human approves it
  (F4 — user-only approver). **Whole-content replace** is the
  composition choice here: ``agent_modify`` already receives the
  complete new content from its caller, so there is nothing to
  compose against the effective chain — the submitted string IS
  the draft body (unlike inner_soul's append/learned mutations,
  which compose via the mutator callable).
* **File carve-out** (``memory.md`` only, per D6) — memory.md is
  prompt-loaded AND runtime-written with 80% compaction; it keeps
  the direct ``write_text`` path until decisions.md re-disposes it.

Everything else (``growth.md``, ``meta.json``, ...) keeps the
refusal shape Phase A landed.

**Preserved exceptions (T9 inventory)** — creation and deletion are
legitimate first-writes / whole-agent moves and still write files
directly, BY DESIGN:

1. ``agent_create`` (this module) — template copy from
   ``agents/_baby_template`` into the new agent dir (creation is
   the first write; the new agent's content auto-versions at its
   first post-creation submission per F1/D8 OVERRIDES-ONLY).
2. ``daemon/routers/agents.py`` ``POST /agents`` — the HTTP twin of
   the same template copy.
3. ``agent_delete`` (this module) — ``shutil.move`` of the whole
   agent dir to ``agents/_trash/`` (delete is a rename, not a
   content write).
4. ``daemon/routers/agents.py`` ``DELETE /agents/{id}`` — the HTTP
   twin of the same _trash move.

None of the four passes through the prompt-artifacts write guard
(``daemon.tools.prompt_artifacts.check_write``); tests pin this
(``tests/test_prompt_preserved_exceptions.py``).

Cache invalidation (D9): the memory.md file path still invalidates
the prompt cache with the resolved ``version_tag`` (the file content
changed under the cache). The DB path does NOT invalidate — a
``pending_approval`` row does not change effective content, and the
loader's fingerprint-bust hook (A8, ``get_effective_rows``) owns
cache busting when an approval/selection actually changes it.

Read side: ``agent_read`` remains a file reader in this phase.
Approved DB content reaches agents through the loader hook
(prompt lane). Surfacing approved overrides inside ``agent_read``
for user.md (whose content never enters the loader) is a documented
D5 follow-up, deliberately out of T8 scope.
"""

import asyncio
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from langchain_core.tools import tool

from ._tool_registry import register_tool_category

if TYPE_CHECKING:
    from ..manager import InstanceManager

CATEGORY_NAME = "Agent Management"
CATEGORY_DOC = """\
Mother agent specific tools for managing baby agent lifecycle.
"""

# Phase B T8: file names agent_modify routes to DB submission instead
# of direct writes, mapped to their DB artifact keys (loader convention
# — keys WITHOUT the ``.md`` extension). The first six mirror
# ``PROMPT_ARTIFACTS`` in ``daemon/repositories/prompt_version/models.py``
# / the loader hook's ``_HOOK_ARTIFACT_KEYS``. ``user.md`` → ``user``
# joins per decisions.md D5 (DB-versioned despite never being
# prompt-loaded; the row simply never enters the loader's prompts dict).
_DB_VERSIONED_FILES: dict[str, str] = {
    "soul.md": "soul",
    "rule.md": "rule",
    "workflow.md": "workflow",
    "tools_note.md": "tools_note",
    "tools.md": "tools",
    "skill.md": "skill",
    "user.md": "user",
}

# Phase B T8 / D6: the only file agent_modify still writes DIRECTLY.
# memory.md is prompt-loaded AND runtime-written with 80% proactive
# compaction + dedup — the DB approval round-trip would break the
# compaction semantics, so it stays file-based until decisions.md
# re-disposes it (no schema change needed if it later joins).
_DIRECT_WRITE_FILES: list[str] = ["memory.md"]

# Base directory for agents (handle PyInstaller frozen state)
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent.parent.parent


def create_mother_tools(manager: "InstanceManager", current_instance_id: str):
    """Create agent management tools for the Mother agent.
    
    Args:
        manager: The InstanceManager instance
        current_instance_id: The current instance ID
        
    Returns:
        List of tool functions for agent management
    """
    
    @register_tool_category("mother")
    @tool
    def agent_list(include_system: bool = False) -> list[dict]:
        """List all available agents. Use tool_help("agent_list") for details."""
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
                    with open(meta_path, "r", encoding="utf-8") as f:
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
                    content = soul_path.read_text(encoding="utf-8")
                    # Look for purpose line
                    for line in content.split("\n"):
                        if "purpose" in line.lower() and ":" in line:
                            agent_info["purpose"] = line.split(":", 1)[-1].strip()
                            break
                except Exception:
                    pass
            
            agents.append(agent_info)
        
        return agents
    
    agent_list._full_doc_ = """List all available agents.

Args:
    include_system: If True, include system agents (starting with _)

Returns:
    List of agent info dictionaries with id, name, description, purpose
"""
    
    @register_tool_category("mother")
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
        """Create a new agent with the specified configuration. Use tool_help("agent_create") for details."""
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
            
            with open(new_agent_dir / "meta.json", "w", encoding="utf-8") as f:
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
            with open(new_agent_dir / "soul.md", "w", encoding="utf-8") as f:
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
                with open(new_agent_dir / "workflow.md", "w", encoding="utf-8") as f:
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
                with open(new_agent_dir / "rule.md", "w", encoding="utf-8") as f:
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
                with open(new_agent_dir / "tools.md", "w", encoding="utf-8") as f:
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
    
    agent_create._full_doc_ = """Create a new agent with the specified configuration.

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
    
    @register_tool_category("mother")
    @tool
    def agent_read(agent_name: str, file: str = "soul.md") -> dict:
        """Read an agent's file contents. Use tool_help("agent_read") for details."""
        # Protect system agents except _mother (self-read)
        if agent_name.startswith("_") and agent_name != "_mother":
            return {
                "success": False,
                "error": "Cannot read system agents except _mother"
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
            content = file_path.read_text(encoding="utf-8")
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
    
    agent_read._full_doc_ = """Read an agent's file contents.

Args:
    agent_name: The agent identifier (e.g., "developer", "leader", "_mother")
    file: The file to read (soul.md, workflow.md, rule.md, user.md, memory.md, tools.md)

Returns:
    Dict with success status and file content, or error message
"""
    
    @register_tool_category("mother")
    @tool
    async def agent_modify(agent_name: str, file: str, content: str) -> dict:
        """Modify an agent's file contents. Use tool_help("agent_modify") for details."""
        # Protect system agents except _mother (self-mod)
        if agent_name.startswith("_") and agent_name != "_mother":
            return {
                "success": False,
                "error": "Cannot modify system agents except _mother"
            }

        # ── Phase B T8 write-path classification ─────────────────────
        # Versioned-immutable artifacts → DB submission (create_and_submit,
        # A5) — the file on disk is NEVER touched; a human must approve
        # the draft before it becomes effective (F4). memory.md → direct
        # write (D6 carve-out). Everything else → the refusal shape
        # Phase A landed.
        artifact_key = _DB_VERSIONED_FILES.get(file)
        is_direct_write = file in _DIRECT_WRITE_FILES
        if artifact_key is None and not is_direct_write:
            allowed = _DIRECT_WRITE_FILES + sorted(_DB_VERSIONED_FILES)
            return {
                "success": False,
                "error": (
                    f"Cannot modify '{file}'. Directly writable files: "
                    f"{', '.join(_DIRECT_WRITE_FILES)}. All other prompt "
                    f"artifacts are versioned-immutable and must be submitted "
                    f"for approval by passing one of: {', '.join(sorted(_DB_VERSIONED_FILES))}."
                ),
            }

        agent_dir = BASE_DIR / "agents" / agent_name

        if not agent_dir.exists():
            return {
                "success": False,
                "error": f"Agent '{agent_name}' not found"
            }

        file_path = agent_dir / file

        # Resolve the version tag ONCE — used by the DB key
        # ``(agent_id, version_tag, artifact)`` and by the cache
        # bust on the direct-write path (D9 threading).
        from ..registry import _parse_agent_dir_name
        base_id, resolved_tag = _parse_agent_dir_name(agent_name)

        if artifact_key is not None:
            # ── DB submission path (T8) ─────────────────────────────
            # file_fallback = the CURRENT on-disk content (or None) —
            # the bottom of the F6 precedence chain the service
            # composes against for mutator callers and records for
            # lineage. agent_modify passes whole content (see module
            # docstring: replace, not compose — the caller already
            # sent the full body).
            try:
                file_fallback = (
                    file_path.read_text(encoding="utf-8")
                    if file_path.exists()
                    else None
                )

                from ..repositories.prompt_version.repository import (
                    actor_from_instance,
                )

                actor = actor_from_instance(
                    current_instance_id,
                    "_mother",  # this factory only mounts for agents/_mother
                )

                svc = getattr(manager, "_prompt_version_service", None)
                if svc is None:
                    return {
                        "success": False,
                        "error": (
                            "Prompt-version service unavailable — cannot "
                            f"submit '{file}'. The write was NOT performed "
                            "(fail-closed; the file is immutable)."
                        ),
                    }

                # svc.create_and_submit is ALREADY an async facade
                # (it does its own asyncio.to_thread hop around the
                # sync repo composite per A4/A5) — await it directly;
                # wrapping it in another to_thread would return an
                # un-awaited coroutine.
                row = await svc.create_and_submit(
                    base_id,
                    resolved_tag,
                    artifact_key,
                    content,
                    actor,
                    file_fallback=file_fallback,
                )
            except Exception as e:
                return {
                    "success": False,
                    "error": f"Failed to submit prompt-version draft: {str(e)}"
                }

            return {
                "success": True,
                "agent": agent_name,
                "file": file,
                "message": (
                    f"Submitted {file} for agent '{agent_name}' as "
                    f"version {row.version} (state: {row.state}). It takes "
                    f"effect for the agent's prompt only after a human "
                    f"approves it; the file on disk was not changed."
                ),
                "version_id": row.version_id,
                "version": row.version,
                "state": row.state,
                "submitted": True,
                "file_fallback_used": file_fallback is not None,
            }

        # ── Direct-write path — memory.md only (D6) ──────────────────
        try:
            # Write new content
            file_path.write_text(content, encoding="utf-8")

            # Phase A T7 / D9: thread the resolved version_tag so the
            # cache bust reaches the per-tag key (``developer[v2]``).
            manager.prompt_cache.invalidate(agent_name, version_tag=resolved_tag)

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

    agent_modify._full_doc_ = """Modify an agent's file contents.

Phase B (T8): prompt artifacts (soul.md, rule.md, workflow.md,
tools_note.md, tools.md, skill.md, user.md) are NOT written to disk —
the content is submitted to the prompt-version approval pipeline as a
pending_approval row and takes effect only after a human approves it
(file fallback stays live until then). memory.md remains a direct
write (runtime memory state with compaction, per the memory.md
carve-out).

Args:
    agent_name: The agent identifier (e.g., "developer", "developer[v2]", "_mother")
    file: The file to modify (soul.md, workflow.md, rule.md, user.md,
        memory.md, tools_note.md, tools.md, skill.md)
    content: The new content for the file (whole-content replace for
        submitted artifacts)

Returns:
    Dict with success status. For submitted artifacts: version_id /
    version / state of the new pending_approval row. For memory.md:
    the direct-write confirmation.
"""
    
    @register_tool_category("mother")
    @tool
    def agent_delete(agent_name: str, confirm: bool = False) -> dict:
        """Delete an agent (move to _trash). Use tool_help("agent_delete") for details."""
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
    
    agent_delete._full_doc_ = """Delete an agent (move to _trash).

Args:
    agent_name: The agent identifier to delete
    confirm: Must be True to actually delete (safety check)

Returns:
    Dict with success status, or error message
"""
    
    return [
        agent_list,
        agent_create,
        agent_read,
        agent_modify,
        agent_delete,
    ]
