"""Inner soul tool for agent self-modification."""

from datetime import datetime
from pathlib import Path
from langchain_core.tools import tool
from typing import TYPE_CHECKING, Literal
import re

if TYPE_CHECKING:
    from ..manager import SessionManager


def create_inner_soul_tool(
    manager: "SessionManager",
    agent_dir: str,
    session_id: str
):
    """Create inner_soul tool bound to a specific agent.
    
    Args:
        manager: SessionManager for cache invalidation
        agent_dir: Path to the agent directory
        session_id: Current session ID for logging
    
    Returns:
        The inner_soul tool function
    """
    
    @tool
    def inner_soul(
        intent: Literal["remember", "learn", "change"],
        content: str,
        target: Literal["memory", "workflow", "soul"] | None = None
    ) -> str:
        """Remember, learn, or change yourself.
        
        Use this tool to store memories, learn from experiences, or propose
        changes to your own configuration.
        
        Args:
            intent: What you want to do:
                - "remember": Store an observation/event as a timestamped file
                - "learn": Store a pattern (may trigger workflow evolution)
                - "change": Propose a change to workflow or soul
            content: What to remember/learn/change (max 1000 chars)
            target: For "change" intent - which file to modify:
                - "memory": Add to core memory.md
                - "workflow": Modify workflow.md
                - "soul": Modify soul.md (requires approval)
        
        Returns:
            Confirmation message or error description
        
        Examples:
            inner_soul(intent="remember", content="User prefers TypeScript")
            inner_soul(intent="learn", content="Iterative testing catches bugs earlier")
            inner_soul(intent="change", target="workflow", content="Add self-review step")
        """
        try:
            agent_path = Path(agent_dir)
            
            # Validate content length
            if len(content) > 1000:
                return "ERROR: Content exceeds 1000 character limit"
            
            # Load growth.md rules
            growth_rules = _load_growth_rules(agent_path)
            
            if intent == "remember":
                return _handle_remember(agent_path, content)
            
            elif intent == "learn":
                return _handle_learn(agent_path, content, growth_rules)
            
            elif intent == "change":
                return _handle_change(
                    agent_path, 
                    content, 
                    target, 
                    growth_rules,
                    manager
                )
            
            else:
                return f"ERROR: Unknown intent '{intent}'. Use 'remember', 'learn', or 'change'"
                
        except Exception as e:
            return f"ERROR: {str(e)}"
    
    return inner_soul


def _load_growth_rules(agent_path: Path) -> dict:
    """Parse growth.md for rules."""
    growth_file = agent_path / "growth.md"
    if not growth_file.exists():
        return {
            "max_memory_words": 500,
            "max_soul_chars": 2000,
            "soul_requires_approval": True,
        }
    
    content = growth_file.read_text()
    
    # Extract size limits from content
    rules = {
        "max_memory_words": 500,
        "max_soul_chars": 2000,
        "max_soul_statements": 20,
        "soul_requires_approval": True,
        "workflow_changes_per_tasks": 5,
        "soul_changes_per_tasks": 10,
    }
    
    # Simple parsing for limits
    import re
    if match := re.search(r"memory\.md.*?(\d+)\s*words", content, re.IGNORECASE):
        rules["max_memory_words"] = int(match.group(1))
    if match := re.search(r"soul\.md.*?(\d+)\s*characters", content, re.IGNORECASE):
        rules["max_soul_chars"] = int(match.group(1))
    
    return rules


def _handle_remember(agent_path: Path, content: str) -> str:
    """Create timestamped memory file."""
    memories_dir = agent_path / "memories"
    memories_dir.mkdir(exist_ok=True)
    
    # Generate filename: YYYYMMDD_HHMM_description.md
    now = datetime.now()
    timestamp = now.strftime("%Y%m%d_%H%M")
    
    # Create safe description from content
    desc = _slugify(content[:50])
    filename = f"{timestamp}_{desc}.md"
    
    filepath = memories_dir / filename
    
    # Don't overwrite existing files
    counter = 1
    while filepath.exists():
        filename = f"{timestamp}_{desc}_{counter}.md"
        filepath = memories_dir / filename
        counter += 1
    
    # Write memory file
    file_content = f"""# Memory

**Created:** {now.strftime("%Y-%m-%d %H:%M")}

{content}
"""
    filepath.write_text(file_content)
    
    return f"✓ Remembered: memories/{filename}"


def _handle_learn(agent_path: Path, content: str, rules: dict) -> str:
    """Store learning and check for pattern evolution."""
    # First, remember it
    result = _handle_remember(agent_path, content)
    
    # Check memories count for potential evolution
    memories_dir = agent_path / "memories"
    memory_count = len(list(memories_dir.glob("*.md"))) if memories_dir.exists() else 0
    
    if memory_count >= 3:
        return f"{result}\n→ {memory_count} memories recorded. Pattern tracking active."
    
    return result


def _handle_change(
    agent_path: Path,
    content: str,
    target: str | None,
    rules: dict,
    manager: "SessionManager"
) -> str:
    """Handle change request with validation."""
    
    if target is None:
        return "ERROR: For 'change' intent, specify target: 'memory', 'workflow', or 'soul'"
    
    if target == "memory":
        return _change_memory(agent_path, content, rules, manager)
    
    elif target == "workflow":
        return _change_workflow(agent_path, content, rules, manager)
    
    elif target == "soul":
        return _propose_soul_change(agent_path, content, rules)
    
    else:
        return f"ERROR: Unknown target '{target}'. Use 'memory', 'workflow', or 'soul'"


def _change_memory(agent_path: Path, content: str, rules: dict, manager: "SessionManager") -> str:
    """Add to memory.md with size validation."""
    memory_file = agent_path / "memory.md"
    
    current = memory_file.read_text() if memory_file.exists() else "# Core Memory\n\n"
    word_count = len(current.split())
    
    max_words = rules.get("max_memory_words", 500)
    if word_count >= max_words:
        return f"ERROR: memory.md at {word_count} words (max {max_words}). Use memories/ instead."
    
    # Find the insertion point (before the last line if it's the footer)
    lines = current.rstrip().split('\n')
    
    # Find where to insert (before footer comment if exists)
    insert_idx = len(lines)
    for i, line in enumerate(lines):
        if line.startswith("*For events") or line.startswith("<!--"):
            insert_idx = i
            break
    
    # Insert the new content
    new_line = f"\n- {content}"
    lines.insert(insert_idx, new_line)
    new_content = '\n'.join(lines)
    
    memory_file.write_text(new_content)
    
    # Invalidate prompt cache
    manager.prompt_cache.invalidate(agent_path)
    
    new_word_count = len(new_content.split())
    return f"✓ Added to memory.md ({new_word_count} words total, {max_words} max)"


def _change_workflow(
    agent_path: Path,
    content: str,
    rules: dict,
    manager: "SessionManager"
) -> str:
    """Modify workflow.md with the proposed change."""
    workflow_file = agent_path / "workflow.md"
    
    current = workflow_file.read_text() if workflow_file.exists() else "# Workflow\n\n"
    
    # Add learned section if not exists
    if "**Learned:**" not in current:
        current += "\n\n---\n\n**Learned:**\n"
    
    # Append the new learning
    new_workflow = f"{current}\n- {content}"
    workflow_file.write_text(new_workflow)
    
    # Invalidate prompt cache
    manager.prompt_cache.invalidate(agent_path)
    
    return f"✓ Workflow updated with: {content[:100]}{'...' if len(content) > 100 else ''}"


def _propose_soul_change(
    agent_path: Path,
    content: str,
    rules: dict,
) -> str:
    """Propose soul change - REQUIRES USER APPROVAL.
    
    Creates a proposed change file that must be manually approved.
    """
    # Create history directory for tracking
    history_dir = agent_path / "history"
    history_dir.mkdir(exist_ok=True)
    
    # Generate filename
    now = datetime.now()
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    filename = f"{timestamp}_soul_proposal.md"
    filepath = history_dir / filename
    
    # Write proposal
    proposal_content = f"""# Soul Change Proposal

**Created:** {now.strftime("%Y-%m-%d %H:%M:%S")}
**Status:** PENDING APPROVAL

## Proposed Addition

{content}

## To Apply

1. Review the content above
2. If approved, manually add to soul.md
3. Delete this file

## To Reject

Delete this file.
"""
    filepath.write_text(proposal_content)
    
    return (
        f"⚠ SOUL CHANGE PROPOSED (requires user approval):\n"
        f"```\n{content[:200]}{'...' if len(content) > 200 else ''}\n```\n\n"
        f"Created: history/{filename}\n"
        f"This change will NOT be applied automatically.\n"
        f"Review and manually apply if approved."
    )


def _slugify(text: str) -> str:
    """Convert text to URL-safe slug."""
    text = text.lower()
    text = re.sub(r'[^a-z0-9]+', '_', text)
    text = text.strip('_')
    return text[:30] if text else "memory"
