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
import logging
import fcntl
import time
import os
import tempfile
from contextlib import contextmanager

from ._tool_registry import register_tool_category
from ..rag.config import is_rag_enabled

logger = logging.getLogger(__name__)

CATEGORY_NAME = "Self-Modification"
CATEGORY_DOC = """\
Remember, learn, or change agent behavior and access memories.

**Intent types**: `remember`, `learn`, `change`
**Target types**: `memory`, `workflow`, `soul`, `user`
"""

# Rate limiting for archive sweep (keyed by agent path, value is last sweep time)
_last_archive_sweep: dict[str, float] = {}

if TYPE_CHECKING:
    from ..manager import InstanceManager


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
    "project_knowledge": {
        "patterns": [
            # Project-specific paths and structures
            r"\btest/packs?\b", r"\bsrc/\b", r"\bconfig/\b", r"\bdocs/\b",
            r"\btest\s+pack\b", r"\btest\s+script\b", r"\bbash\s+script\b",
            # Specific project/tool names (external projects)
            r"\bllm-supervisor-proxy\b", r"\bagents-ensemble\b", r"\bmy\s+project\b",
            # Infrastructure and tech stack
            r"\bpostgresql\b", r"\bpostgres\b", r"\bmysql\b", r"\bsqlite\b",
            r"\bkubernetes\b", r"\bk8s\b", r"\bdocker\b", r"\bterraform\b",
            r"\baws\b", r"\bgcp\b", r"\bazure\b",
            # Project-specific configs
            r"\.env\b", r"\bconfig\.yaml\b", r"\bsettings\.py\b",
            r"\bpackage\.json\b", r"\brequirements\.txt\b", r"\bpyproject\.toml\b",
            # Database/server terminology (for projects)
            r"\bpostgres(ql)?://\b", r"\bredis://\b", r"\bmongo://\b",
            # Deployment/infrastructure
            r"\bdeployment\b", r"\bci/cd\b", r"\bgithub\s+actions\b",
            r"\bpipeline\b", r"\bhelm\s+chart\b",
        ],
        "targets": ["REJECT"],
        "description": "Project-specific knowledge - must NOT enter agent memory"
    },
}

# Targets that are handled by the RAG knowledge system
_RAG_TARGETS = {"memories", "memory"}

# Classification types that are knowledge-oriented (not self-modification)
_KNOWLEDGE_CLASSIFICATIONS = {
    "knowledge", "pattern", "event", "skill", "mistake", "project_knowledge"
}


def _should_redirect_to_rag(
    targets: list[str],
    classification: dict,
    explicit_target: bool,
) -> bool:
    """Determine if a request should be redirected to experience() instead of file-based memory.

    Redirect when:
    1. RAG is configured/enabled, AND
    2. ALL resolved targets are RAG targets (memories/memory), AND
    3. The classification is knowledge-oriented (not identity/personality/workflow)

    Do NOT redirect when:
    - RAG is not configured/enabled
    - Any target is soul/user/workflow (self-modification)
    - The request is identity/personality/user-related

    Args:
        targets: The resolved list of target strings.
        classification: The classification dict from _classify_request().
        explicit_target: Whether the user explicitly specified a target.

    Returns:
        True if request should redirect to experience().
    """
    # Guard: If RAG is not enabled, preserve old file-based behavior
    if not is_rag_enabled():
        return False

    class_type = classification.get("type", "")

    # Filter out "REJECT" from multi-match target merging.
    actual_targets = [t for t in targets if t != "REJECT"]

    # Special case: project_knowledge was REJECTED before — now redirect to RAG
    if class_type == "project_knowledge":
        return True

    # Check if ALL resolved targets are RAG-managed
    if not actual_targets or not all(t in _RAG_TARGETS for t in actual_targets):
        return False  # Has soul/user/workflow — self-modification

    # Check if classification is knowledge-oriented
    if class_type in _KNOWLEDGE_CLASSIFICATIONS:
        return True

    return False


def _format_rag_redirect(request: str, classification: dict, targets: list[str]) -> str:
    """Format response for requests redirected to RAG knowledge system.

    Instead of writing to files, guides the agent to use the experience() tool.
    """
    truncated = request[:80] + ('...' if len(request) > 80 else '')
    class_type = classification.get("type", "unknown")

    lines = [
        f"📋 Redirected to Knowledge System: \"{truncated}\"",
        f"  Classification: {class_type} ({classification.get('description', '')})",
        f"  Original targets: {', '.join(targets)} → now handled by RAG",
        "",
        "This knowledge is better stored in the RAG knowledge base where it can be",
        "queried and cross-referenced with other project knowledge.",
        "",
        "→ Use the `experience()` tool to record this knowledge:",
        f'  experience(text="{request.replace(chr(34), chr(92)+chr(34))}")',
        "",
        "→ Use the `explore()` tool to query existing knowledge.",
    ]

    return "\n".join(lines)


@contextmanager
def _lock_memory_file(filepath: Path, timeout: float = 5.0):
    """Acquire exclusive lock on memory file with timeout.
    
    Uses a separate .lock file to avoid modifying the actual memory file.
    Lock file is cleaned up in the finally block after releasing the flock.
    """
    lock_file = filepath.with_suffix('.lock')
    lock_file.touch(exist_ok=True)
    f = open(lock_file, 'r+')
    deadline = time.monotonic() + timeout
    acquired = False
    try:
        while time.monotonic() < deadline:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except (IOError, OSError, BlockingIOError):
                time.sleep(0.1)
        if not acquired:
            raise TimeoutError(f"Could not acquire lock on {filepath} within {timeout}s")
        yield
    finally:
        if acquired:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        f.close()
        # Clean up lock file after releasing the flock
        lock_file.unlink(missing_ok=True)


def _atomic_write_memory(filepath: Path, content: str):
    """Write to memory file atomically. MUST be called inside _lock_memory_file().
    
    Sequence: write tmp -> rename current to .bak -> rename tmp to current -> delete .bak
    Uses tempfile for the tmp file to avoid conflicts.
    """
    parent = filepath.parent
    backup = filepath.with_suffix('.bak')
    
    # Write to temp file first
    with tempfile.NamedTemporaryFile(
        mode='w', dir=parent, suffix='.tmp', delete=False, encoding='utf-8'
    ) as tmp:
        tmp.write(content)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)
    
    try:
        # Rename current to backup (only if exists)
        if filepath.exists():
            filepath.replace(backup)
        
        # Rename temp to current (atomic on POSIX)
        tmp_path.replace(filepath)
        
        # Remove backup on success
        if backup.exists():
            backup.unlink()
    except Exception:
        # Rollback: restore from backup if anything failed
        if not filepath.exists() and backup.exists():
            backup.replace(filepath)
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def _normalize_list_item(line: str) -> str | None:
    """Normalize a list item line for deduplication.
    
    Strips common list markers (-, *, 1., 2., etc.) and whitespace.
    
    Args:
        line: The line to normalize.
        
    Returns:
        Lowercased content without the list marker, or None if not a list item.
    """
    stripped = line.strip()
    # Match common list markers: "- ", "* ", "1. ", "2. ", etc.
    match = re.match(r'^\s*(?:[-*]|\d+\.)\s+(.+)$', stripped, re.IGNORECASE)
    if match:
        return match.group(1).lower()
    return None


def _compact_memory(content: str) -> str:
    """Simple deduplication: remove duplicate lines, keep most recent version.
    
    Preserves structure (headers, blank lines). Deduplicates non-structural lines
    by keeping the most recent occurrence (bottom of file = most recent).
    """
    lines = content.strip().split('\n')
    seen = set()
    unique_lines = []
    
    # Process from newest (bottom) to oldest (top)
    for line in reversed(lines):
        normalized = line.strip().lower()
        if not normalized:
            # Preserve blank lines
            unique_lines.append(line)
            continue
        if normalized.startswith('#'):
            # Preserve headers
            unique_lines.append(line)
            continue
        
        # Check if this is a list item (any marker: -, *, 1., 2., etc.)
        list_content = _normalize_list_item(line)
        if list_content is not None:
            # Deduplicate list items by their content
            if list_content not in seen:
                seen.add(list_content)
                unique_lines.append(line)
        else:
            # Preserve non-list lines (they're structural)
            unique_lines.append(line)
    
    # Reverse back to original order
    unique_lines.reverse()
    
    # Remove excessive blank lines (max 1 consecutive)
    result = []
    prev_blank = False
    for line in unique_lines:
        is_blank = not line.strip()
        if is_blank and prev_blank:
            continue
        result.append(line)
        prev_blank = is_blank
    
    return '\n'.join(result)


def _archive_memory_file(agent_path: Path, filename: str) -> bool:
    """Move a memory file from memories/ to memories/archive/YYYY/MM/.
    
    Args:
        agent_path: Path to the agent directory
        filename: Just the filename (e.g., "20260517_0930-some-memory.md")
    
    Returns:
        True if archived successfully, False otherwise.
    """
    source = agent_path / "memories" / filename
    if not source.exists() or not source.is_file():
        return False
    
    now = datetime.now()
    archive_dir = agent_path / "memories" / "archive" / f"{now.year:04d}" / f"{now.month:02d}"
    
    try:
        archive_dir.mkdir(parents=True, exist_ok=True)
        
        dest = archive_dir / filename
        # Handle name collision in archive
        counter = 1
        while dest.exists():
            stem = Path(filename).stem
            suffix = Path(filename).suffix
            dest = archive_dir / f"{stem}-{counter}{suffix}"
            counter += 1
        
        source.rename(dest)
        return True
    except OSError:
        return False


def _archive_old_memories(agent_path: Path, ttl_days: int = 90) -> int:
    """Archive memory files older than TTL days.
    
    Scans memories/ for .md files older than ttl_days and moves them to
    memories/archive/YYYY/MM/. Uses rate limiting to avoid scanning too often.
    
    Args:
        agent_path: Path to the agent directory
        ttl_days: Minimum age in days before archiving (0 = disabled)
    
    Returns:
        Number of files archived.
    """
    if ttl_days <= 0:
        return 0
    
    # Rate limiting: only sweep if at least 5 minutes have passed
    key = str(agent_path)
    now = time.monotonic()
    if key in _last_archive_sweep and now - _last_archive_sweep[key] < 300:
        return 0  # Skip — too soon
    _last_archive_sweep[key] = now
    
    memories_dir = agent_path / "memories"
    if not memories_dir.exists():
        return 0
    
    cutoff = datetime.now().timestamp() - (ttl_days * 86400)  # seconds in a day
    archived = 0
    
    for f in sorted(memories_dir.iterdir()):
        if not f.is_file() or f.suffix != ".md" or f.is_symlink():
            continue
        # Use modification time as age indicator
        if f.stat().st_mtime < cutoff:
            if _archive_memory_file(agent_path, f.name):
                archived += 1
    
    return archived


def create_inner_soul_tool(
    manager: "InstanceManager",
    agent_id: str,
    instance_id: str,
):
    """Create inner_soul tool bound to a specific agent.
    
    Args:
        manager: InstanceManager for cache invalidation
        agent_id: The agent identifier (e.g., "coder")
        instance_id: Current instance ID for logging
    
    Returns:
        The inner_soul tool function
    """
    # Resolve agent_id to path for internal use
    from ..registry import get_registry
    registry = get_registry()
    agent_meta = registry.get(agent_id)
    agent_path = agent_meta.path if agent_meta else Path(agent_id)
    
    # Run archival sweep if configured
    if agent_path:
        growth_rules = _load_growth_rules(agent_path)
        ttl_days = growth_rules.get("memory_archive_ttl_days", 90)
        if ttl_days > 0:
            _archive_old_memories(agent_path, ttl_days)
    
    @register_tool_category("self")
    @tool
    def inner_soul(
        content: str | None = None,
        request: str | None = None,
        intent: Literal["remember", "learn", "change"] | None = None,
        target: Literal["memory", "workflow", "soul", "user", "memories"] | None = None
    ) -> str:
        """Remember, learn, or change yourself. Use tool_help("inner_soul") for details."""
        try:
            # Support both 'request' and 'content' for backward compatibility
            actual_request: str = request or content or ""
            if not actual_request:
                return "ERROR: Must provide 'request' or 'content' parameter"
            
            # Validate content length
            if len(actual_request) > 2000:
                return "ERROR: Request exceeds 2000 character limit"
            
            # Load rules
            growth_rules = _load_growth_rules(agent_path)
            
            # Check for compound requests and split if needed
            request_parts = _split_compound_request(actual_request)
            
            # Filter out whitespace-only parts
            request_parts = [p for p in request_parts if p.strip()]
            
            # Check for empty/meaningless request
            if not request_parts:
                return "ERROR: Request is empty after processing"
            
            # Check if single part is essentially just split markers
            if len(request_parts) == 1:
                stripped = request_parts[0].strip().upper()
                if stripped in ("AND", ";", "OR"):
                    return "ERROR: Request is empty after processing"
            
            if len(request_parts) == 1:
                # Single request - use existing flow
                classification = _classify_request(actual_request, intent=intent)
                
                # Determine targets using helper
                targets = _resolve_targets(target, intent, classification)
                
                # Check if this should redirect to RAG
                if _should_redirect_to_rag(targets, classification, explicit_target=bool(target)):
                    return _format_rag_redirect(actual_request, classification, targets)
                
                # Execute updates
                results = []
                for t in targets:
                    result = _execute_update(
                        agent_id=agent_id,
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
            else:
                # Compound request - process each part independently
                compound_lines = [f"✓ Compound request split into {len(request_parts)} parts:"]
                all_results = []
                
                for idx, part in enumerate(request_parts, 1):
                    # Classify this part independently
                    classification = _classify_request(part, intent=intent)
                    
                    # Determine targets for this part using helper
                    targets = _resolve_targets(target, intent, classification)
                    
                    # Check for RAG redirect
                    if _should_redirect_to_rag(targets, classification, explicit_target=bool(target)):
                        rag_response = _format_rag_redirect(part, classification, targets)
                        compound_lines.append(f"  Part {idx}: \"{part[:50]}{'...' if len(part) > 50 else ''}\" → {classification['type']}")
                        compound_lines.append(f"    {rag_response.split(chr(10))[0]} (redirected to RAG)")
                        all_results.append({"part": part, "redirected": True, "response": rag_response})
                        continue
                    
                    # Execute updates for this part
                    results = []
                    for t in targets:
                        result = _execute_update(
                            agent_id=agent_id,
                            agent_path=agent_path,
                            request=part,
                            target=t,
                            intent=intent,
                            rules=growth_rules,
                            manager=manager,
                            classification=classification
                        )
                        results.append(result)
                    
                    # Build result lines for this part
                    truncated = f"{part[:50]}{'...' if len(part) > 50 else ''}"
                    compound_lines.append(f"  Part {idx}: \"{truncated}\" → {classification['type']} ({', '.join(targets)})")
                    
                    for r in results:
                        if r.get("success"):
                            file_name = r.get("file", "")
                            compound_lines.append(f"    ✓ {r.get('target', 'unknown')}: {file_name}")
                        else:
                            compound_lines.append(f"    ⚠ {r.get('target', 'unknown')}: {r.get('error', 'Unknown error')}")
                    
                    all_results.append({"part": part, "results": results, "classification": classification})
                
                return "\n".join(compound_lines)
            
        except Exception as e:
            return f"ERROR: {str(e)}"
    
    inner_soul._full_doc_ = """The core of agent growth - remember, learn, and evolve.

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
    target: (Optional) Explicit target: "memory", "memories", "workflow", "soul", "user"

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
    
    return inner_soul


def _resolve_targets(target: str | None, intent: str | None, classification: dict) -> list[str]:
    """Resolve the target(s) for a request based on explicit params and classification.
    
    Args:
        target: Explicitly specified target, or None.
        intent: Explicitly specified intent, or None.
        classification: The classification dict from _classify_request().
        
    Returns:
        List of target strings to update.
    """
    if target:
        return [target]
    elif intent == "remember":
        return ["memories"]
    elif intent == "learn":
        return ["memories", "memory"]
    else:
        return classification["targets"]


def _split_compound_request(request: str) -> list[str]:
    """Split compound requests into individual parts for independent processing.
    
    Attempts splitting in order of preference:
    1. AND keyword (uppercase only, word boundary)
    2. Semicolons
    3. Sentence boundaries (period followed by uppercase)
    4. Single request (no split)
    
    Args:
        request: The potentially compound request string.
        
    Returns:
        List of individual request strings (empty strings filtered out).
    """
    # Try splitting on AND keyword first (uppercase only)
    parts = re.split(r'\s+AND\s+', request)
    
    if len(parts) == 1:
        # No AND found, try semicolons
        parts = re.split(r'\s*;\s*', request)
    
    if len(parts) == 1:
        # No semicolons found, try sentence boundaries
        parts = re.split(r'\.\s+(?=[A-Z])', request)
    
    # Strip whitespace and filter empty strings
    parts = [p.strip() for p in parts if p.strip()]
    
    # Return parts if we have them, otherwise check fallback
    if parts:
        return parts
    
    # No parts after split - check if original request is essentially empty or just split markers
    stripped = request.strip()
    if not stripped or stripped.upper() in ("AND", ";", "OR"):
        return []
    return [stripped]


def _classify_request(request: str, intent: str | None = None) -> dict:
    """Semantically classify a request to determine appropriate targets.
    
    Args:
        request: The user request to classify.
        intent: Optional explicit intent ("remember", "learn", "change").
    """
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
    
    # Classification failed - determine fallback based on intent
    # Default fallback: treat as event/observation.
    # Note: Natural phrasing like "Context7 is built-in MCP server" falls through here
    # because regex patterns can't match arbitrary factual statements.
    # Phase 5 will add LLM-based classification fallback to handle these edge cases.
    if intent == "remember":
        logger.debug("Classification fell back to memories/ for remember intent")
        return {
            "type": "event",
            "targets": ["memories"],
            "description": "Event or observation (remember intent fallback)",
            "all_matches": []
        }
    
    # No pattern matched and no remember intent
    logger.debug("Classification inconclusive - no pattern matched, defaulting to event/memories")
    return {
        "type": "event",
        "targets": ["memories"],
        "description": "Event or observation",
        "all_matches": []
    }


def _execute_update(
    agent_id: str,
    agent_path: Path,
    request: str,
    target: str,
    intent: str | None,
    rules: dict,
    manager: "InstanceManager",
    classification: dict
) -> dict:
    """Execute an update to a specific target."""
    
    if target == "memories":
        return _update_memories(agent_id, agent_path, request, classification, manager)
    elif target == "memory":
        return _update_memory_md(agent_id, agent_path, request, rules, manager)
    elif target == "soul":
        return _update_soul(agent_id, agent_path, request, rules, manager)
    elif target == "user":
        return _update_user(agent_id, agent_path, request, manager)
    elif target == "workflow":
        return _update_workflow(agent_id, agent_path, request, rules, manager)
    else:
        return {"success": False, "target": target, "error": f"Unknown target: {target}"}


def _update_memories(agent_id: str, agent_path: Path, request: str, classification: dict, manager: "InstanceManager" | None = None) -> dict:
    """Create timestamped memory file."""
    memories_dir = agent_path / "memories"
    memories_dir.mkdir(exist_ok=True)
    
    now = datetime.now()
    timestamp = now.strftime("%Y%m%d_%H%M")
    
    # Create safe filename
    desc = _slugify(request[:80])  # More chars for better description
    filename = f"{timestamp}-{desc}.md"
    
    filepath = memories_dir / filename
    
    # Don't overwrite
    counter = 1
    while filepath.exists():
        filename = f"{timestamp}-{desc}-{counter}.md"
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
    
    # Invalidate prompt cache so new memory appears in next prompt
    if manager:
        manager.prompt_cache.invalidate(agent_id)
    
    return {
        "success": True,
        "target": "memories",
        "file": f"memories/{filename}",
        "type": classification["type"]
    }


def _format_rejection(target: str, max_words: int, word_count: int, rules: dict | None = None) -> dict:
    """Format rejection message for memory limit exceeded."""
    return {
        "success": False,
        "target": target,
        "error": f"Memory limit exceeded ({word_count} >= {max_words} words). Content was not saved."
    }


def _update_memory_md(agent_id: str, agent_path: Path, request: str, rules: dict, manager: "InstanceManager" | None = None) -> dict:
    """Add to core memory.md with file locking and compaction support."""
    memory_file = agent_path / "memory.md"
    
    try:
        with _lock_memory_file(memory_file):
            # Read current content inside lock
            current = memory_file.read_text() if memory_file.exists() else "# Memory\n\n"
            word_count = len(current.split())
            max_words = rules.get("max_memory_words", 2000)
            
            # Check proactive compaction threshold (80%)
            should_compact = word_count > max_words * 0.8
            
            if word_count >= max_words:
                # Always try compaction when at capacity
                current = _compact_memory(current)
                word_count = len(current.split())
                # Re-check after compaction
                if word_count >= max_words:
                    return _format_rejection("memory", max_words, word_count, rules)
            elif should_compact:
                # Proactively compact when approaching capacity
                current = _compact_memory(current)
                word_count = len(current.split())
            
            # Find insertion point (before "*For events" marker or HTML comments)
            lines = current.rstrip().split('\n')
            insert_idx = len(lines)
            for i, line in enumerate(lines):
                if line.startswith("*For events") or line.startswith("<!--"):
                    insert_idx = i
                    break
            
            # Check if the new entry would duplicate
            new_entry = f"\n- {request}"
            normalized_new = request.strip().lower()
            for line in lines:
                if line.strip().lower() == f"- {normalized_new}":
                    return {
                        "success": True,
                        "action": "skipped",
                        "target": "memory",
                        "message": "Entry already exists in memory.md",
                        "compact": False,
                    }
            
            lines.insert(insert_idx, new_entry)
            new_content = '\n'.join(lines)
            
            # Use atomic write instead of direct write
            _atomic_write_memory(memory_file, new_content)
            
            # Invalidate prompt cache so next prompt sees the updated memory
            if manager and hasattr(manager, 'prompt_cache'):
                manager.prompt_cache.invalidate(agent_id)
            
            return {
                "success": True,
                "action": "updated",
                "target": "memory",
                "message": f"Added to memory.md",
                "compact": should_compact,
            }
    except TimeoutError:
        return {
            "success": False,
            "action": "error",
            "target": "memory",
            "message": "Could not acquire lock on memory.md - please retry",
        }


def _update_soul(agent_id: str, agent_path: Path, request: str, rules: dict, manager: "InstanceManager" | None = None) -> dict:
    """Apply soul.md change directly - identity updates are applied immediately."""
    soul_file = agent_path / "soul.md"
    history_dir = agent_path / "history"
    history_dir.mkdir(exist_ok=True)
    
    now = datetime.now()
    
    try:
        with _lock_memory_file(soul_file):
            # Read current soul inside lock
            if soul_file.exists():
                current = soul_file.read_text()
            else:
                current = "# Who I Am\n\n"
            
            # Check size constraints
            max_chars = rules.get("max_soul_chars", 2000)
            if len(current) >= max_chars:
                return {
                    "success": False,
                    "target": "soul",
                    "error": f"soul.md at {len(current)} chars (max {max_chars}). Cannot add more."
                }
            
            # Determine where to add the change
            lines = current.rstrip().split('\n')
            
            # Format the change based on request type
            request_lower = request.lower()
            is_name_change = any(p in request_lower for p in ["my name is", "i am called", "call me", "remember my name", "remember your name"])
            
            if is_name_change:
                # Extract name and format nicely
                name = request.split('name is')[-1].split('called')[-1].strip().rstrip('.')
                formatted = f"**My name is {name}**"
                # Insert right after main header
                insert_idx = 1  # After first line (header)
                while insert_idx < len(lines) and lines[insert_idx].strip() == "":
                    insert_idx += 1
                # Check if name already exists and update it
                for i, line in enumerate(lines):
                    if line.startswith("**My name is"):
                        lines[i] = formatted
                        formatted = None  # Flag that we updated, not inserted
                        break
            else:
                formatted = f"- {request}"
                # Append at the end
                insert_idx = len(lines)
            
            # Insert the change (if not already updated)
            if formatted:
                lines.insert(insert_idx, formatted)
            new_content = '\n'.join(lines)
            
            # Use atomic write inside lock
            _atomic_write_memory(soul_file, new_content)
    except TimeoutError:
        return {
            "success": False,
            "target": "soul",
            "error": "Could not acquire lock on soul.md - please retry"
        }
    
    # Invalidate cache if manager provided
    if manager:
        manager.prompt_cache.invalidate(agent_id)
    
    # Log to history for audit trail
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    history_file = history_dir / f"{timestamp}_soul_change.md"
    history_content = f"""# Soul Change Applied

**Applied:** {now.strftime("%Y-%m-%d %H:%M:%S")}
**Status:** APPLIED

## Change

{request}

## Previous State

```
{current[:500]}{'...' if len(current) > 500 else ''}
```
"""
    history_file.write_text(history_content)
    
    return {
        "success": True,
        "target": "soul",
        "file": "soul.md",
        "status": "applied",
        "chars": f"{len(new_content)}/{max_chars}",
        "message": "Soul change applied directly"
    }


def _update_user(agent_id: str, agent_path: Path, request: str, manager: "InstanceManager" | None = None) -> dict:
    """Add user information to user.md."""
    user_file = agent_path / "user.md"
    
    try:
        with _lock_memory_file(user_file):
            current = user_file.read_text() if user_file.exists() else "# User\n\n"
            
            # Remove placeholder
            if "(To be filled" in current:
                current = current.split("(To be filled")[0].rstrip()
            
            # Append
            new_content = f"{current}\n- {request}"
            
            # Use atomic write inside lock
            _atomic_write_memory(user_file, new_content)
    except TimeoutError:
        return {
            "success": False,
            "target": "user",
            "error": "Could not acquire lock on user.md - please retry"
        }
    
    if manager:
        manager.prompt_cache.invalidate(agent_id)
    
    return {
        "success": True,
        "target": "user",
        "file": "user.md"
    }


def _update_workflow(agent_id: str, agent_path: Path, request: str, rules: dict, manager: "InstanceManager" | None = None) -> dict:
    """Add workflow change."""
    workflow_file = agent_path / "workflow.md"
    
    try:
        with _lock_memory_file(workflow_file):
            current = workflow_file.read_text() if workflow_file.exists() else "# Workflow\n\n"
            
            if "**Learned:**" not in current:
                current += "\n\n---\n\n**Learned:**\n"
            
            new_workflow = f"{current}\n- {request}"
            
            # Use atomic write inside lock
            _atomic_write_memory(workflow_file, new_workflow)
    except TimeoutError:
        return {
            "success": False,
            "target": "workflow",
            "error": "Could not acquire lock on workflow.md - please retry"
        }
    
    if manager:
        manager.prompt_cache.invalidate(agent_id)
    
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
            
            msg = f"  ✓ {target}: {file}"
            if "chars" in r:
                msg += f" ({r['chars']} chars)"
            if "words" in r:
                msg += f" ({r['words']} words)"
            lines.append(msg)
        else:
            lines.append(f"  ⚠ {r.get('target', 'unknown')}: {r.get('error', 'Unknown error')}")
    
    return "\n".join(lines)


def _load_growth_rules(agent_path: Path) -> dict:
    """Parse growth.md for rules."""
    growth_file = agent_path / "growth.md"
    
    # Default rules - returned when growth.md doesn't exist
    rules = {
        "max_memory_words": 2000,
        "max_soul_chars": 2000,
        "max_soul_statements": 20,
        "soul_requires_approval": True,
        "workflow_changes_per_tasks": 5,
        "soul_changes_per_tasks": 10,
        "memory_archive_ttl_days": 90,
    }
    
    if not growth_file.exists():
        return rules
    
    content = growth_file.read_text()
    
    if match := re.search(r"memory\.md.*?(\d+)\s*words", content, re.IGNORECASE):
        rules["max_memory_words"] = int(match.group(1))
    if match := re.search(r"soul\.md.*?(\d+)\s*characters", content, re.IGNORECASE):
        rules["max_soul_chars"] = int(match.group(1))
    if match := re.search(r"archive.*?(\d+)\s*days?", content, re.IGNORECASE):
        rules["memory_archive_ttl_days"] = int(match.group(1))
    
    return rules


def _slugify(text: str) -> str:
    """Convert text to readable hyphenated slug."""
    text = text.lower()
    text = re.sub(r'[^a-z0-9]+', '-', text)  # Hyphens, not underscores
    text = text.strip('-')
    return text[:60] if text else "memory"  # Longer: 60 chars
