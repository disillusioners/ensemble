"""Inner soul tool for agent self-modification.

This is the core intelligence for agent growth. It understands:
- What each .md file is for (soul=identity, user=preferences, memory=knowledge, etc.)
- How to classify requests semantically
- When to update multiple files together
"""

from datetime import datetime
from pathlib import Path
from langchain_core.tools import tool
from typing import TYPE_CHECKING, Literal
import re

if TYPE_CHECKING:
    from ..manager import SessionManager


# Semantic classification patterns
CLASSIFICATION_RULES = {
    "identity": {
        "patterns": [
            r"\bmy name is\b", r"\bi am called\b", r"\bi'm called\b",
            r"\bremember (my|your|the) name\b", r"\bcall me\b",
            r"\bi am a\b", r"\bi'm a\b", r"\bwho i am\b",
            r"\bmy purpose is\b", r"\bi exist to\b", r"\bmy mission is\b",
            r"\bthis is (now )?part of (who|what) i am\b",
        ],
        "targets": ["soul"],
        "description": "Core identity and self-definition"
    },
    "personality": {
        "patterns": [
            r"\bbe (more |less )?(friendly|cozy|warm|cold|formal|casual|concise|verbose|playful|serious|empathetic|assertive|curious|helpful)\b",
            r"\bact (more |less )?(friendly|cozy|warm|cold|formal|casual)\b",
            r"\bspeak (more |less )?(formally|casually|warmly|coldly)\b",
            r"\bmy (style|tone|voice) is\b",
            r"\bi (value|believe in|care about)\b",
            r"\bpersonality\b",
        ],
        "targets": ["soul", "user"],
        "description": "Personality traits and interaction style"
    },
    "user_preference": {
        "patterns": [
            r"\buser (likes|prefers|wants|needs|hates|dislikes|loves)\b",
            r"\b(user|they) (always|never|usually|often)\b",
            r"\bthe user's\b",
            r"\bmy user\b",
            r"\bfor the user\b",
        ],
        "targets": ["user"],
        "description": "User preferences and relationship"
    },
    "user_identity": {
        "patterns": [
            r"\buser'?s? name is\b",
            r"\bthe user is called\b",
            r"\b(user|they) (work|works) (as a|at|in)\b",
            r"\b(user|they) (use|uses|prefer|prefers)\b",
        ],
        "targets": ["user"],
        "description": "User identity and background"
    },
    "knowledge": {
        "patterns": [
            r"\bremember that\b",
            r"\bnote that\b",
            r"\bimportant (thing|fact|info):?\b",
            r"\bdon'?t forget\b",
            r"\bkeep in mind\b",
            r"\bi learned that\b",
            r"\bi (now )?know\b",
        ],
        "targets": ["memory", "memories"],
        "description": "Important knowledge to retain"
    },
    "pattern": {
        "patterns": [
            r"\bpattern:?\b",
            r"\bi noticed (that )?(when|if|every time)\b",
            r"\b(always|usually|often) when\b",
            r"\bit seems like\b",
            r"\bthis keeps happening\b",
        ],
        "targets": ["memories"],
        "description": "Observed patterns and insights"
    },
    "workflow": {
        "patterns": [
            r"\b(always|never) (do|check|verify|run|use)\b",
            r"\bbefore (doing|starting|beginning)\b",
            r"\bafter (doing|finishing|completing)\b",
            r"\bstep \d+:?\b",
            r"\bworkflow:?\b",
            r"\bmy process (is|should be)\b",
            r"\bfirst,?\b.*\bthen,?\b",
            r"\bnew rule:?\b",
        ],
        "targets": ["workflow"],
        "description": "Process and workflow changes"
    },
    "event": {
        "patterns": [
            r"\btoday\b",
            r"\bjust now\b",
            r"\bthis (morning|afternoon|evening|session)\b",
            r"\bwe (discussed|talked about|worked on)\b",
            r"\bthe user (said|asked|told)\b",
        ],
        "targets": ["memories"],
        "description": "Events and observations"
    },
    "skill": {
        "patterns": [
            r"\bi (can|learned to|now know how to)\b",
            r"\bnew skill:?\b",
            r"\bability:?\b",
            r"\bcapability:?\b",
        ],
        "targets": ["memories"],
        "description": "New skills and capabilities"
    },
    "mistake": {
        "patterns": [
            r"\bmistake:?\b",
            r"\bi (made a mistake|was wrong|shouldn't have)\b",
            r"\bdon'?t (do|make|repeat)\b.*\bagain\b",
            r"\blesson learned:?\b",
            r"\bavoid (doing|making)\b",
        ],
        "targets": ["memories"],
        "description": "Mistakes and lessons learned"
    },
}


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
        content: str | None = None,
        request: str | None = None,
        intent: Literal["remember", "learn", "change"] | None = None,
        target: Literal["memory", "workflow", "soul", "user"] | None = None
    ) -> str:
        """The core of agent growth - remember, learn, and evolve.
        
        This tool understands what you mean, not just what you say.
        It knows which files to update based on the semantic meaning
        of your request.
        
        ## Files and Their Purposes:
        - **soul.md** - Who you ARE (identity, personality, core beliefs)
        - **user.md** - Who the USER is (preferences, relationship)
        - **memory.md** - What you KNOW (important knowledge, always kept)
        - **memories/** - What happened (events, observations, timestamped)
        - **workflow.md** - HOW you work (processes, rules, steps)
        
        ## How It Works:
        If you don't specify intent/target, the tool will classify your
        request automatically and update the right file(s).
        
        Args:
            content: (Legacy) What to remember/learn/change. Alias for 'request'.
            request: What you want to remember/learn/change. Can be natural
                     language like "My name is Cody" or "User prefers concise responses"
            intent: (Optional) Explicit intent: "remember", "learn", or "change"
            target: (Optional) Explicit target: "memory", "workflow", "soul", "user"
        
        Returns:
            Confirmation of what was done, or error message
        
        Examples:
            # Natural language (auto-classified):
            inner_soul(request="My name is Cody")
            # → Updates soul.md (identity)
            
            inner_soul(request="User likes TypeScript")
            # → Updates user.md (user preference)
            
            inner_soul(request="Be cozy with the user")
            # → Updates soul.md + user.md (personality + relationship)
            
            inner_soul(request="Always check for tests before committing")
            # → Updates workflow.md (process)
            
            inner_soul(request="I learned that early testing catches bugs")
            # → Creates memory file (knowledge)
            
            # Legacy API (backward compatible):
            inner_soul(intent="remember", content="User prefers TypeScript")
            inner_soul(intent="change", target="workflow", content="Add review step")
        """
        try:
            agent_path = Path(agent_dir)
            
            # Support both 'request' and 'content' for backward compatibility
            actual_request: str = request or content or ""
            if not actual_request:
                return "ERROR: Must provide 'request' or 'content' parameter"
            
            # Validate content length
            if len(actual_request) > 2000:
                return "ERROR: Request exceeds 2000 character limit"
            
            # Load rules
            growth_rules = _load_growth_rules(agent_path)
            
            # Classify the request semantically
            classification = _classify_request(actual_request)
            
            # Determine targets
            if target:
                # Explicit target takes precedence
                targets = [target]
            elif intent == "remember" and not target:
                # Remember defaults to memories
                targets = ["memories"]
            elif intent == "learn" and not target:
                # Learn goes to memories + potentially memory.md
                targets = ["memories", "memory"]
            else:
                # Use semantic classification
                targets = classification["targets"]
            
            # Execute updates
            results = []
            for t in targets:
                result = _execute_update(
                    agent_path=agent_path,
                    request=actual_request,
                    target=t,
                    intent=intent,
                    rules=growth_rules,
                    manager=manager,
                    classification=classification
                )
                results.append(result)
            
            # Format response
            return _format_response(actual_request, results, classification)
            
        except Exception as e:
            return f"ERROR: {str(e)}"
    
    return inner_soul


def _classify_request(request: str) -> dict:
    """Semantically classify a request to determine appropriate targets."""
    request_lower = request.lower()
    
    # Check each classification type
    matches = []
    for class_type, rules in CLASSIFICATION_RULES.items():
        for pattern in rules["patterns"]:
            if re.search(pattern, request_lower, re.IGNORECASE):
                matches.append({
                    "type": class_type,
                    "targets": rules["targets"],
                    "description": rules["description"],
                    "pattern_matched": pattern
                })
                break  # One match per type is enough
    
    if matches:
        # Merge all unique targets
        all_targets = []
        for m in matches:
            for t in m["targets"]:
                if t not in all_targets:
                    all_targets.append(t)
        
        # Return the best match (first one) with merged targets
        best = matches[0]
        best["targets"] = all_targets
        best["all_matches"] = [m["type"] for m in matches]
        return best
    
    # Default: treat as event/observation
    return {
        "type": "event",
        "targets": ["memories"],
        "description": "Event or observation",
        "all_matches": []
    }


def _execute_update(
    agent_path: Path,
    request: str,
    target: str,
    intent: str | None,
    rules: dict,
    manager: "SessionManager",
    classification: dict
) -> dict:
    """Execute an update to a specific target."""
    
    if target == "memories":
        return _update_memories(agent_path, request, classification)
    elif target == "memory":
        return _update_memory_md(agent_path, request, rules, manager)
    elif target == "soul":
        return _update_soul(agent_path, request, rules)
    elif target == "user":
        return _update_user(agent_path, request, manager)
    elif target == "workflow":
        return _update_workflow(agent_path, request, rules, manager)
    else:
        return {"success": False, "target": target, "error": f"Unknown target: {target}"}


def _update_memories(agent_path: Path, request: str, classification: dict) -> dict:
    """Create timestamped memory file."""
    memories_dir = agent_path / "memories"
    memories_dir.mkdir(exist_ok=True)
    
    now = datetime.now()
    timestamp = now.strftime("%Y%m%d_%H%M")
    
    # Create safe filename
    desc = _slugify(request[:50])
    class_prefix = classification["type"]
    filename = f"{timestamp}_{class_prefix}_{desc}.md"
    
    filepath = memories_dir / filename
    
    # Don't overwrite
    counter = 1
    while filepath.exists():
        filename = f"{timestamp}_{class_prefix}_{desc}_{counter}.md"
        filepath = memories_dir / filename
        counter += 1
    
    # Write memory file with classification metadata
    file_content = f"""# Memory

**Created:** {now.strftime("%Y-%m-%d %H:%M")}
**Type:** {classification["type"]}
**Description:** {classification["description"]}

{request}
"""
    filepath.write_text(file_content)
    
    return {
        "success": True,
        "target": "memories",
        "file": f"memories/{filename}",
        "type": classification["type"]
    }


def _update_memory_md(agent_path: Path, request: str, rules: dict, manager: "SessionManager") -> dict:
    """Add to core memory.md."""
    memory_file = agent_path / "memory.md"
    
    current = memory_file.read_text() if memory_file.exists() else "# Memory\n\n"
    word_count = len(current.split())
    
    max_words = rules.get("max_memory_words", 500)
    if word_count >= max_words:
        return {
            "success": False,
            "target": "memory",
            "error": f"memory.md at {word_count} words (max {max_words}). Saved to memories/ instead."
        }
    
    # Find insertion point
    lines = current.rstrip().split('\n')
    insert_idx = len(lines)
    for i, line in enumerate(lines):
        if line.startswith("*For events") or line.startswith("<!--"):
            insert_idx = i
            break
    
    # Insert
    lines.insert(insert_idx, f"\n- {request}")
    new_content = '\n'.join(lines)
    
    memory_file.write_text(new_content)
    manager.prompt_cache.invalidate(agent_path)
    
    new_word_count = len(new_content.split())
    return {
        "success": True,
        "target": "memory",
        "file": "memory.md",
        "words": f"{new_word_count}/{max_words}"
    }


def _update_soul(agent_path: Path, request: str, rules: dict) -> dict:
    """Propose soul.md change - requires approval."""
    history_dir = agent_path / "history"
    history_dir.mkdir(exist_ok=True)
    
    now = datetime.now()
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    filename = f"{timestamp}_soul_proposal.md"
    filepath = history_dir / filename
    
    proposal_content = f"""# Soul Change Proposal

**Created:** {now.strftime("%Y-%m-%d %H:%M:%S")}
**Status:** PENDING APPROVAL

## Proposed Change

{request}

## Why This Matters

This is a change to your core identity. It affects who you ARE.

## To Apply

1. Review the content above
2. If approved, add to soul.md in an appropriate section
3. Delete this file

## To Reject

Simply delete this file.
"""
    filepath.write_text(proposal_content)
    
    return {
        "success": True,
        "target": "soul",
        "file": f"history/{filename}",
        "status": "pending_approval",
        "message": "Soul changes require user approval"
    }


def _update_user(agent_path: Path, request: str, manager: "SessionManager") -> dict:
    """Add user information to user.md."""
    user_file = agent_path / "user.md"
    
    current = user_file.read_text() if user_file.exists() else "# User\n\n"
    
    # Remove placeholder
    if "(To be filled" in current:
        current = current.split("(To be filled")[0].rstrip()
    
    # Append
    new_content = f"{current}\n- {request}"
    user_file.write_text(new_content)
    manager.prompt_cache.invalidate(agent_path)
    
    return {
        "success": True,
        "target": "user",
        "file": "user.md"
    }


def _update_workflow(agent_path: Path, request: str, rules: dict, manager: "SessionManager") -> dict:
    """Add workflow change."""
    workflow_file = agent_path / "workflow.md"
    
    current = workflow_file.read_text() if workflow_file.exists() else "# Workflow\n\n"
    
    if "**Learned:**" not in current:
        current += "\n\n---\n\n**Learned:**\n"
    
    new_workflow = f"{current}\n- {request}"
    workflow_file.write_text(new_workflow)
    manager.prompt_cache.invalidate(agent_path)
    
    return {
        "success": True,
        "target": "workflow",
        "file": "workflow.md"
    }


def _format_response(request: str, results: list, classification: dict) -> str:
    """Format the response to show what was done."""
    lines = [f"✓ Processed: \"{request[:80]}{'...' if len(request) > 80 else ''}\""]
    lines.append(f"  Classification: {classification['type']} ({classification['description']})")
    lines.append("")
    
    for r in results:
        if r.get("success"):
            target = r.get("target", "unknown")
            file = r.get("file", "")
            status = r.get("status", "")
            
            if status == "pending_approval":
                lines.append(f"  📝 {target}: {file}")
                lines.append(f"     → Awaiting user approval")
            else:
                msg = f"  ✓ {target}: {file}"
                if "words" in r:
                    msg += f" ({r['words']} words)"
                lines.append(msg)
        else:
            lines.append(f"  ⚠ {r.get('target', 'unknown')}: {r.get('error', 'Unknown error')}")
    
    return "\n".join(lines)


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
    
    rules = {
        "max_memory_words": 500,
        "max_soul_chars": 2000,
        "max_soul_statements": 20,
        "soul_requires_approval": True,
        "workflow_changes_per_tasks": 5,
        "soul_changes_per_tasks": 10,
    }
    
    if match := re.search(r"memory\.md.*?(\d+)\s*words", content, re.IGNORECASE):
        rules["max_memory_words"] = int(match.group(1))
    if match := re.search(r"soul\.md.*?(\d+)\s*characters", content, re.IGNORECASE):
        rules["max_soul_chars"] = int(match.group(1))
    
    return rules


def _slugify(text: str) -> str:
    """Convert text to URL-safe slug."""
    text = text.lower()
    text = re.sub(r'[^a-z0-9]+', '_', text)
    text = text.strip('_')
    return text[:30] if text else "memory"
