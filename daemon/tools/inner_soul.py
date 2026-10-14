"""Inner soul tool for agent self-modification.

This is the core intelligence for agent growth. It understands:
- What each .md file is for (soul=identity, user=preferences, memory=knowledge, etc.)
- How to classify requests semantically
- When to update multiple files together

Write regime — DUAL-PATH (User Directive #3, 2026-10-14, RESOLUTION-CORE)
=========================================================================

Since the directive, ``inner_soul`` runs TWO write paths side by
side on a target-by-target basis:

* **CORE path** (``soul``, ``workflow``) — DB submission via
  :meth:`PromptVersionService.create_and_submit` (A5/C4 composite).
  The draft lands in ``state='pending_approval'`` (D4 auto-submit,
  no held draft) and a human approves it through the
  prompt-versions REST surface. The on-disk template keeps
  serving until approval (F6 precedence).

  - Composition (C4): the mutator composes on the EFFECTIVE
    chain (``selected > latest_approved > file_fallback``),
    then applies the target-specific mutation (soul
    replace/append, workflow ``**Learned:**`` append).
  - Pending-supersession (A5/G-4f): consecutive un-approved
    calls chain on
    ``max(version) WHERE state IN ('draft','pending_approval')``
    and archive the prior pending row (≤ 1 active pending per
    key).
  - Identity stamping (D10): audit rows stamped
    ``actor_subject=<instance_id>``,
    ``actor_auth_method="internal"`` via ``actor_from_instance``.
  - Response grammar (R7): ``📋 <target>: submitted as
    prompt-version v<N> (pending approval); takes effect once
    approved``.

* **MEMORY path** (``memory``, ``user``, ``growth``,
  ``memories/<slug>``) — direct persist via
  :meth:`PromptVersionService.create_memory_version`. The
  row is born-effective (``state='approved'``, transition-free,
  NO approval round-trip — the directive reverses the
  D5 pending-adjudication for ``user.md``). One ``CREATED``
  audit row stamped with
  ``payload.artifact_class='MEMORY'`` and
  ``via='create_memory_version'``.

  - **user.md supersession of D5**: ``user.md`` was tentatively
    bound for CORE submission per Phase B D5. The directive
    overturns that — it is MEMORY-class now, persists
    directly, no approval. The user's preferences take effect
    on the next prompt reload.
  - **memories per-slug chaining**: ``memories/`` writes use
    the key ``memories/<slug>`` (``<slug>`` = the legacy
    timestamped filename without the ``.md``). Consecutive
    writes on the same slug land as version 1, 2, 3, ... on
    the same key — the loader's selection pointer + latest
    row governs which version is "live". The legacy
    collision-counter that derived ``<filename>-<n>.md`` is
    retired; the version chain is the lineage.
  - **memory.md compaction as new version**: the 80%
    proactive compaction rule is preserved as a SEMANTIC
    guardrail — the mutator enforces the 2000-word cap on the
    composed draft and trims via the existing
    ``_compact_memory`` helper BEFORE persisting. The
    pre-compaction content stays in history (it's a prior
    version on the same ``memory`` key, ``latest_content``
    flips to the post-compaction row).
  - **Response grammar (R7)**: ``✓ <target>: persisted
    (memory version N)`` — keeps the two-space indent +
    ``✓`` marker + ``<target>:`` shape so prefix-parsing
    agents need only the new ``(memory version N)`` tail.

Failure posture: the DB-submission path is FAIL-CLOSED. If the
prompt-version service is unavailable the change is refused with a
``⚠`` result — the file is NEVER written as a fallback (that is the
point of the immutability commission). The Phase A write-guard shim
remains active on the ``write_file``/``edit_file`` tool surfaces as
defense-in-depth against any other file-write path.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from langchain_core.tools import tool
from typing import TYPE_CHECKING, Literal
import asyncio
import re
import logging
import fcntl
import time
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

from ._tool_registry import register_tool_category
from ..rag.config import is_rag_enabled
from ..repositories.prompt_version.models import (
    PROMPT_ARTIFACT_CLASSES,
    PROMPT_MEMORY_ARTIFACTS,
)
from ..repositories.prompt_version.repository import (
    ARTIFACT_CLASS_CORE,
    ARTIFACT_CLASS_MEMORY,
    class_for_artifact,
)

logger = logging.getLogger(__name__)

CATEGORY_NAME = "Self-Modification"
CATEGORY_DOC = """\
Reflect on your own persona, behavioral patterns, and user interaction preferences. NOT for project state, task progress, code, or working logs.

**Intent types**: `remember`, `learn`, `change`
**Target types**: `memory`, `workflow`, `soul`, `user`
"""

# Rate limiting for archive sweep (keyed by agent path, value is last sweep time)
_last_archive_sweep: dict[str, float] = {}

if TYPE_CHECKING:
    from ..manager import InstanceManager


# Target → artifact-class dispatch table (User Directive #3,
# 2026-10-14, RESOLUTION-CORE). Single source of truth for
# the DUAL-PATH writer selection. Mirrors ``class_for_artifact``
# in ``daemon/repositories/prompt_version/repository.py``;
# duplicated here as a fast dict lookup (the legacy carve-out
# for ``memory`` / ``user`` / ``growth`` is RETIRED; the
# directive flipped them to MEMORY-class).
_TARGET_CLASS_DISPATCH: dict[str, str] = {
    # CORE path (state-machine; pending_approval)
    "soul": ARTIFACT_CLASS_CORE,
    "workflow": ARTIFACT_CLASS_CORE,
    # MEMORY path (transition-free; born-effective)
    "memory": ARTIFACT_CLASS_MEMORY,
    "user": ARTIFACT_CLASS_MEMORY,
    "growth": ARTIFACT_CLASS_MEMORY,
    "memories": ARTIFACT_CLASS_MEMORY,  # per-slug keying
}


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
            # --- NEW: Git operations (compound: verb + git-noun) ---
            r"\bgit\s+(push|pull|commit|merge|rebase|checkout|clone|init)\b",
            r"\b(created?|merged?|pushed?|pulled?|checked\s?out|cloned?|rebased?)\s+(a|the|new)?\s*(branch|commit|pr|pull\s+request)\b",
            r"\bbranch\s+\S+\s+(created|deleted|merged|from)\b",
            r"\bpull\s+request\s+(created|merged|opened|closed|approved)\b",
            r"\bcommit(ted)?\s+(to|on|in)\s+\S+",
            # --- NEW: Task/work completion (compound: completion-verb + work-noun) ---
            r"\b(completed?|finished?|done\s+with|shipped?)\s+(a|the|my)?\s*(task|feature|build|deploy|deploy?ment|release|sprint|milestone)\b",
            r"\bsetup\s+(complete|done|finished)\b",
            # --- NEW: Code changes (compound: change-verb + code-noun) ---
            r"\b(refactored?|rewrote?|updated?|modified?|fixed|patched?|added?|removed?|deleted?)\s+(the|a|my)?\s*(\w+\s+)*?(code|api|endpoint|route|schema|database|table|model|function|class|method|component|service|controller|handler)\b",
            r"\bbug\s*(fix|fixed|fixing)\b",
            r"\b(code|api|database|schema)\s+(change|update|fix|refactor|migration)\b",
            r"\bcreated?\s+(a|the|new)\s+\w+\.(py|js|ts|tsx|jsx|md|json|yaml|yml|toml|sql|go|rs|java)\b",
            r"\bcreated?\s+(a\s+|the\s+)*new\s+\w+\.(py|js|ts|tsx|jsx|md|json|yaml|yml|toml|sql|go|rs|java)\b",
            # --- NEW: Deployment status (compound: deploy-verb + target) ---
            r"\bdeployed?\s+(to|on|in)\s+\S+",
            r"\bdeployed?\s+(a|the|new)\s+\S+",
            r"\bbuilt?\s+(and\s+)?deployed?\b",
            # --- NEW: Bare deployment/CI status reports (G1 fix) ---
            r"\bdeploy\s+(is\s+)?(done|complete|finished|live|successful)\b",
            r"\b(ci/cd|pipeline)\s+(is\s+)?(green|passing|broken|failing|down|up)\b",
            r"\bmigration\s+(is\s+)?(done|complete|finished|applied|successful)\b",
            r"\b(release|deployment)\s+(is\s+)?(done|complete|finished|live|successful)\b",
        ],
        "targets": ["REJECT"],
        "description": "Project-specific knowledge - must NOT enter agent memory"
    },
}

# Persona-intent prefixes that indicate self-reflection, not project reporting.
# When a request starts with one of these patterns, Stage 1 of the 3-stage
# classification flow flags the request as a potential persona statement. This
# flag does NOT bypass the project-knowledge pre-check (Stage 2 always runs);
# instead, when both a persona prefix AND a project pattern match, Stage 3 is
# also evaluated and a persona category match is required to accept the
# request. This prevents project content from hiding behind a persona prefix
# (e.g. "I should note that git push succeeded") while still allowing
# legitimate persona reflections that happen to mention project terms
# (e.g. "I should be more careful with deployments").
_PERSONA_INTENT_PREFIXES = [
    r"^\s*i\s+(should|need to|must|ought to|want to|tend to|always|never|usually)\b",
    r"^\s*i\s+(am|'m)\s+(a|an|the)?\s*\w*",  # "I am a DevOps agent"
    r"^\s*i\s+(learned|realized|discovered)\s+that\s+(i|my|being|early|the\s+user)",  # persona learning
    r"^\s*my\s+(approach|style|tone|voice|strategy|tendency|habit|philosophy|strength|weakness)\b",
    r"^\s*i\s+(value|believe|care\s+about|strive\s+to|aim\s+to)\b",
    r"^\s*be\s+(more|less|cozy|warm|cold|formal|casual|concise|verbose)\b",
    r"^\s*user\s+(likes|prefers|wants|needs|always|never)\b",
    r"^\s*remember\s+(my|your|the\s+user)"  # "remember my name" not "remember that docker..."
]

# Targets that are handled by the RAG knowledge system
_RAG_TARGETS = {"memories", "memory"}

# Classification types that are knowledge-oriented (not self-modification).
# Note: "knowledge" category was removed in Phase 1 of the inner-soul reform —
# its patterns caused too many false positives where project content leaked
# into agent memory. Knowledge is now handled by the RAG system via redirect
# when RAG is enabled, and the project-content pre-check prevents leaks when
# RAG is disabled.
_KNOWLEDGE_CLASSIFICATIONS = {
    "pattern", "event", "skill", "mistake", "project_knowledge"
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


def _format_project_rejection(request: str, classification: dict) -> str:
    """Format rejection message for project-related content.

    When content is classified as project_knowledge and RAG is disabled,
    we can't redirect to experience() — but we still reject with guidance.
    """
    truncated = request[:80] + ('...' if len(request) > 80 else '')
    lines = [
        f"⛔ Rejected: \"{truncated}\"",
        f"  Classification: project_knowledge ({classification.get('description', '')})",
        "",
        "This looks project-related. inner_soul is for persona/behavioral",
        "reflection ONLY — not project state, task progress, or code.",
        "",
        "→ For project events (features, fixes, deployments):",
        "  project_history_add(project_id=..., entry_type='feature', summary='...')",
        "",
        "→ For project knowledge (architecture, patterns, gotchas):",
        "  experience(text='...')",
        "",
        "→ inner_soul is ONLY for:",
        "  - Your persona changes ('Be more concise')",
        "  - User preferences ('User prefers TypeScript')",
        "  - Self-reflection ('I learned that early feedback improves outcomes')",
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
    f = open(lock_file, 'r+', encoding='utf-8')
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
    version_tag: str | None = None,
):
    """Create inner_soul tool bound to a specific agent.

    Args:
        manager: InstanceManager for cache invalidation
        agent_id: The agent identifier (e.g., "developer")
        instance_id: Current instance ID for logging
        version_tag: Optional version tag (e.g., ``"v2"``) used to resolve the
            versioned agent directory so persona/soul/workflow updates target
            the correct version's files. When ``None``, falls back to the base
            resolved agent meta. Threaded from
            ``daemon.tools.instance.create_instance_tools`` so versioned agents
            self-modify their own subtree (C1 fix — base/v1 was being written
            by v2 instances).

    Returns:
        The inner_soul tool function
    """
    # Resolve agent_id to path for internal use
    from ..registry import get_registry
    registry = get_registry()
    agent_meta = registry.get_version(agent_id, version_tag) or registry.get_resolved(agent_id)
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
        target: Literal["memory", "workflow", "soul", "user", "memories", "growth"] | None = None
    ) -> str:
        """Reflect on your persona, change behavioral patterns, or remember user interaction preferences. NOT for project state, task progress, code, or working logs. Use tool_help("inner_soul") for details."""
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

                # RAG redirect FIRST: when RAG is enabled, project_knowledge
                # and other knowledge-oriented classifications are routed to
                # experience() via _should_redirect_to_rag(). This must run
                # before the REJECT check below.
                if _should_redirect_to_rag(targets, classification, explicit_target=bool(target)):
                    return _format_rag_redirect(actual_request, classification, targets)

                # PRIMARY REJECT check (G2 critical fix): examine the
                # CLASSIFICATION's targets for "REJECT" rather than the
                # resolved targets. _resolve_targets() drops the REJECT
                # sentinel when an explicit `target` or `intent` is
                # provided — without this check, calls like
                # `inner_soul(intent="remember", content="git push
                # succeeded")` or `inner_soul(target="memory", content=
                # "database migration done")` would write project content
                # directly to memory.
                if "REJECT" in classification.get("targets", []):
                    return _format_project_rejection(actual_request, classification)

                # Secondary safety net: REJECT should already be caught
                # above (via classification["targets"]), but if a future
                # change ever reintroduces it in the resolved-targets path,
                # we still surface a rejection here.
                if "REJECT" in targets:
                    return _format_project_rejection(actual_request, classification)

                # Execute updates. Phase A T7: thread version_tag from
                # the ``create_inner_soul_tool`` closure so the post-write
                # ``manager.prompt_cache.invalidate`` busts the per-tag
                # key (F9 / decisions.md R10). Phase B T7: thread the
                # instance identity for D10 audit stamping on the
                # soul/user/workflow DB-submission path.
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
                        classification=classification,
                        version_tag=version_tag,
                        instance_id=instance_id,
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

                    # RAG redirect FIRST: must run before the REJECT check
                    # below, since when RAG is enabled, project_knowledge is
                    # routed to experience() via _should_redirect_to_rag().
                    if _should_redirect_to_rag(targets, classification, explicit_target=bool(target)):
                        rag_response = _format_rag_redirect(part, classification, targets)
                        compound_lines.append(f"  Part {idx}: \"{part[:50]}{'...' if len(part) > 50 else ''}\" → {classification['type']}")
                        compound_lines.append(f"    {rag_response.split(chr(10))[0]} (redirected to RAG)")
                        all_results.append({"part": part, "redirected": True, "response": rag_response})
                        continue

                    # PRIMARY REJECT check (G2 critical fix): examine the
                    # CLASSIFICATION's targets rather than the resolved
                    # targets. _resolve_targets() drops the REJECT sentinel
                    # when an explicit `target` or `intent` is provided,
                    # which would otherwise let project content slip into
                    # memory for this part of a compound request.
                    if "REJECT" in classification.get("targets", []):
                        rejection_msg = _format_project_rejection(part, classification)
                        compound_lines.append(f"  Part {idx}: \"{part[:50]}{'...' if len(part) > 50 else ''}\" → project_knowledge (REJECTED)")
                        compound_lines.append(f"    {rejection_msg.split(chr(10))[0]}")
                        all_results.append({"part": part, "rejected": True, "response": rejection_msg})
                        continue

                    # Secondary safety net: REJECT in resolved targets still
                    # surfaces a project rejection.
                    if "REJECT" in targets:
                        rejection_msg = _format_project_rejection(part, classification)
                        compound_lines.append(f"  Part {idx}: \"{part[:50]}{'...' if len(part) > 50 else ''}\" → project_knowledge (REJECTED)")
                        compound_lines.append(f"    {rejection_msg.split(chr(10))[0]}")
                        all_results.append({"part": part, "rejected": True, "response": rejection_msg})
                        continue

                    # Execute updates for this part. Phase A T7: thread version_tag
                    # from the closure (F9 / R10). Phase B T7: thread the
                    # instance identity for D10 audit stamping.
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
                            classification=classification,
                            version_tag=version_tag,
                            instance_id=instance_id,
                        )
                        results.append(result)
                    
                    # Build result lines for this part
                    truncated = f"{part[:50]}{'...' if len(part) > 50 else ''}"
                    compound_lines.append(f"  Part {idx}: \"{truncated}\" → {classification['type']} ({', '.join(targets)})")
                    
                    for r in results:
                        if r.get("success"):
                            if r.get("db") and r.get("memory"):
                                # MEMORY path (Ud#3): born-effective.
                                compound_lines.append(
                                    f"    ✓ {r.get('target', 'unknown')}: {r.get('message', 'persisted')}"
                                )
                            elif r.get("db"):
                                # Phase B T7: pending-approval grammar for
                                # DB-submitted CORE targets.
                                compound_lines.append(
                                    f"    📋 {r.get('target', 'unknown')}: {r.get('message', 'submitted')}"
                                )
                            else:
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

## ⚠️ What This Tool is NOT For

This tool is **INTENSELY PERSONAL** — it's about YOU (the agent) and the USER as personas.

**DO NOT use inner_soul for:**
- Project events or task progress → use `project_history_add()`
- Project knowledge, architecture, code insights → use `experience()`
- Git operations, branch names, deployment status → use `project_history_add()`
- Anything about code, configs, infrastructure, or the project itself → use `experience()`

**This tool WILL REJECT project-related content.**

If your content mentions branches, commits, deployments, code, files, configs,
databases, APIs, or project tasks, it will be rejected with guidance on which
tool to use instead.

## Files and Their Purposes:
- **soul.md** - Who you ARE (identity, personality, core beliefs)
- **user.md** - Who the USER is (preferences, relationship)
- **memory.md** - Persona insights (NOT project knowledge)
- **memories/** - Personal observations (NOT task logs)
- **workflow.md** - HOW you work (processes, rules, steps)

> Write regime (prompt-immutability Phase B): `soul`, `workflow` and
> `user` changes are submitted as versioned DB drafts that go straight
> to PENDING APPROVAL — they take effect once a human operator
> approves them; your files are not modified directly. You get a
> `📋 <target>: submitted as prompt-version v<N> (pending approval)`
> confirmation immediately. A rejected or superseded draft stays in
> the audit lineage; just call this tool again to submit a fresh
> version.

> Note: The DUAL-PATH regime (User Directive #3, 2026-10-14,
> RESOLUTION-CORE) keeps the five target types. CORE targets
> (`soul` / `workflow`) submit a DB draft that goes straight to
> PENDING APPROVAL — they take effect once a human operator
> approves them; your files are not modified directly. MEMORY
> targets (`memory` / `user` / `growth` / `memories/<slug>`)
> persist directly to the prompt-version DB as MEMORY-class
> rows — no approval round-trip, no badge hit. The on-disk
> file stays the cold-boot F6 fallback.
>
> MEMORY grammar (Ud#3): a successful write returns
> `✓ <target>: persisted (memory version N)` — born-effective,
> transition-free. CORE grammar: a successful write returns
> `📋 <target>: submitted as prompt-version v<N> (pending
> approval)` — the agent_self tool surface keeps the two
> markers (`✓` for born-effective, `📋` for pending-approval)
> so prefix-parsing agents can branch without touching the
> rest of the line.
>
> Persona-relevant reflections on personal observations should
> still target `memory` / `memories/` (MEMORY path is the
> historical semantic). Project knowledge (architecture,
> gotchas, deployment state) MUST go through the RAG knowledge
> system via `experience()` — the classification layers
> REJECT project content even behind persona prefixes.

## How It Works:
If you don't specify intent/target, the tool will classify your
request automatically and update the right file(s).

Args:
    content: (Legacy) What to remember/learn/change. Alias for 'request'.
    request: What you want to remember/learn/change. Can be natural
             language like "My name is Cody" or "User prefers concise responses"
    intent: (Optional) Explicit intent: "remember", "learn", or "change"
    target: (Optional) Explicit target: "memory", "memories",
             "workflow", "soul", "user", "growth" — the dispatch
             is class-aware (CORE: soul / workflow; MEMORY:
             memory / user / growth / memories/<slug>)

Returns:
    Confirmation of what was done, or error message

Examples:
    # Natural language (auto-classified):
    inner_soul(request="My name is Cody")
    # → Submits a soul change (pending approval)

    inner_soul(request="User likes TypeScript")
    # → Submits a user.md change (pending approval)

    inner_soul(request="Be cozy with the user")
    # → Submits soul.md + user.md changes (pending approval)

    inner_soul(request="Always check for tests before committing")
    # → Submits a workflow.md change (pending approval)

    inner_soul(request="I learned that early testing catches bugs")
    # → Redirected to RAG (project knowledge) — use experience() instead

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

    Uses a 3-stage flow:

    Stage 1 — Persona-intent detection (F1/G3):
        If the request starts with a self-reflection prefix
        (e.g., "I should...", "My approach...", "Be more..."), mark the
        request as having persona intent. This flag changes how Stage 2
        behaves — it does NOT skip Stage 2 (the blanket exemption was a
        leak: project content could hide behind a persona prefix).

    Stage 2 — Project-content pre-check (always runs, G3 fix):
        Check the `project_knowledge` patterns first. If any match, decide
        what to do based on persona intent:
        - No persona intent → REJECT (project content).
        - Persona intent present → also run Stage 3 to see if a persona
          category (identity, personality, user_preference, user_identity,
          workflow) ALSO matches. If yes, the request is a legitimate
          persona reflection that happens to mention project terms; accept
          and continue to Stage 3. If no persona category matches, the
          request is project content hiding behind a persona prefix; REJECT.

    Stage 3 — Normal semantic classification:
        Check each non-project classification type and merge targets. Fall
        back to event/memories if no pattern matches.

    Args:
        request: The user request to classify.
        intent: Optional explicit intent ("remember", "learn", "change").
    """
    request_lower = request.lower()

    # ========================================
    # STAGE 1: Persona-intent detection
    # ========================================
    # If the statement starts with a self-reflection prefix, flag it as a
    # potential persona request. The flag changes Stage 2 behavior; it does
    # NOT bypass the project-content check.
    persona_intent_matched = False
    for prefix_pattern in _PERSONA_INTENT_PREFIXES:
        if re.search(prefix_pattern, request_lower, re.IGNORECASE):
            persona_intent_matched = True
            break

    # ========================================
    # STAGE 2: Project-content pre-check (ALWAYS runs — G3 fix)
    # ========================================
    # Even persona-prefixed requests must be checked. The old behavior of
    # skipping Stage 2 on persona match was a leak: a request like
    # "I should note that git push succeeded" would be accepted as persona
    # content even though the actual intent is project reporting.
    project_patterns = CLASSIFICATION_RULES["project_knowledge"]["patterns"]
    project_pattern_matched = None
    for pattern in project_patterns:
        if re.search(pattern, request_lower, re.IGNORECASE):
            project_pattern_matched = pattern
            break

    if project_pattern_matched is not None:
        if persona_intent_matched:
            # Dual-match: persona prefix AND project pattern both matched.
            # Run Stage 3 to see if a persona category also matches — that
            # indicates a legitimate persona reflection that happens to
            # mention a project term (e.g. "I should be more careful with
            # deployments" — the persona is "be more careful", not the
            # deployment). If no persona category matches, treat it as
            # project content hiding behind a persona prefix.
            persona_categories = {
                "identity", "personality", "user_preference",
                "user_identity", "workflow",
            }
            has_persona_category = False
            for class_type, rules in CLASSIFICATION_RULES.items():
                if class_type == "project_knowledge":
                    continue
                if class_type not in persona_categories:
                    continue
                for p in rules["patterns"]:
                    if re.search(p, request_lower, re.IGNORECASE):
                        has_persona_category = True
                        break
                if has_persona_category:
                    break

            if not has_persona_category:
                # Project content hiding behind persona prefix — REJECT.
                return {
                    "type": "project_knowledge",
                    "targets": ["REJECT"],
                    "description": CLASSIFICATION_RULES["project_knowledge"]["description"],
                    "pattern_matched": project_pattern_matched,
                    "all_matches": ["project_knowledge"],
                }
            # else: legitimate persona reflection — fall through to Stage 3.
        else:
            # No persona intent, project matched — REJECT.
            return {
                "type": "project_knowledge",
                "targets": ["REJECT"],
                "description": CLASSIFICATION_RULES["project_knowledge"]["description"],
                "pattern_matched": project_pattern_matched,
                "all_matches": ["project_knowledge"],
            }

    # ========================================
    # STAGE 3: Normal semantic classification (existing logic)
    # ========================================
    # Check each non-project classification type
    matches = []
    for class_type, rules in CLASSIFICATION_RULES.items():
        if class_type == "project_knowledge":
            continue  # Already handled in Stage 2
        for pattern in rules["patterns"]:
            if re.search(pattern, request_lower, re.IGNORECASE):
                matches.append({
                    "type": class_type,
                    "targets": rules["targets"],
                    "description": rules["description"],
                    "pattern_matched": pattern
                })
                break

    if matches:
        # Merge all unique targets
        all_targets = []
        for m in matches:
            for t in m["targets"]:
                if t not in all_targets:
                    all_targets.append(t)

        best = matches[0]
        best["targets"] = all_targets
        best["all_matches"] = [m["type"] for m in matches]
        return best

    # Fallback (unchanged)
    if intent == "remember":
        return {"type": "event", "targets": ["memories"],
                "description": "Event or observation (remember intent fallback)",
                "all_matches": []}

    return {"type": "event", "targets": ["memories"],
            "description": "Event or observation", "all_matches": []}


# ============================================================
# Prompt-version DB submission (Phase B T7 — soul/workflow/user)
# ============================================================


class PromptMutationRejected(ValueError):
    """Raised by a target mutator to refuse the composed draft.

    The mutator runs INSIDE ``create_and_submit``'s pre-transaction
    composition step; raising here aborts the submission before any
    DB row is written. Converted to the standard ``⚠ <target>:``
    result shape by :func:`_submit_prompt_version`.
    """


def _run_coro_sync(coro):
    """Drive an async service coroutine from this sync tool context.

    The inner_soul tool function is deliberately SYNC (LangChain
    StructuredTool + the existing test suite invoke it via
    ``.invoke()``), but the prompt-version service facade is async
    (A4: the ``asyncio.to_thread`` facade serves router +
    inner_soul/agent_mother callers). Two cases (mirrors the
    battle-tested ``plane_sync._drive_async`` semantics):

    1. No event loop running in this thread → ``asyncio.run``.
    2. A loop IS running in this thread (sync tool reached from a
       live loop) → block a worker thread on a fresh loop instead;
       ``run_until_complete`` is illegal inside a running loop.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(asyncio.run, coro).result()


def _get_prompt_version_service(manager: "InstanceManager | None"):
    """Fetch the manager's prompt-version service (or ``None``).

    The manager constructs the singleton at boot
    (``manager._prompt_version_service``); the attribute is read
    defensively so mocks/legacy callers without one degrade to the
    fail-closed path instead of AttributeError.
    """
    if manager is None:
        return None
    return getattr(manager, "_prompt_version_service", None)


def _submit_prompt_version(
    agent_id: str,
    version_tag: str | None,
    artifact: str,
    mutator,
    request: str,
    instance_id: str | None,
    agent_path: Path,
    manager: "InstanceManager | None",
    target: str,
) -> dict:
    """Submit a composed prompt-version draft for one target (D4/A5/C4).

    Shared core of the Phase B ``_update_soul`` / ``_update_user`` /
    ``_update_workflow`` rewires. FAIL-CLOSED: when the service or the
    instance identity is unavailable the change is refused — the file
    is never written as a fallback.

    Args:
        agent_id: Resolved base agent id (closure).
        version_tag: Version tag of the owning instance (closure;
            ``None`` normalizes to ``""`` in the repo).
        artifact: Prompt artifact key (``"soul"`` / ``"workflow"`` /
            ``"user"``).
        mutator: Callable ``(effective_content: str | None) -> str``
            applied INSIDE ``create_and_submit`` to the effective
            chain (selected > latest_approved > file_fallback).
        request: The original request text (for error messages).
        instance_id: Submitting instance id (D10 audit identity).
        agent_path: Agent directory — read-only source of the
            template ``file_fallback``.
        manager: InstanceManager (service + prompt_cache source).
        target: Response-grammar target name (``"soul"`` etc.).

    Returns:
        Result dict in the ``_execute_update`` shape; success rows
        carry ``db=True``, ``version``, ``version_id``, ``state``.
    """
    service = _get_prompt_version_service(manager)
    if service is None:
        return {
            "success": False,
            "target": target,
            "error": (
                "prompt-version service unavailable — change NOT applied "
                f"({target} is an immutable prompt artifact; file "
                "fallback is disabled)"
            ),
        }
    if not instance_id:
        return {
            "success": False,
            "target": target,
            "error": (
                "instance identity unavailable — change NOT submitted "
                "(prompt-version drafts are stamped with the submitter "
                "instance id)"
            ),
        }

    # Read-only template snapshot: the bottom of the F6 effective
    # chain. No lock — the DB transaction is the serialization point;
    # a torn read here only degrades the fallback, never correctness.
    fallback_file = agent_path / f"{artifact}.md"
    try:
        file_fallback = (
            fallback_file.read_text(encoding="utf-8")
            if fallback_file.exists()
            else None
        )
    except OSError:
        file_fallback = None

    from ..repositories.prompt_version.repository import actor_from_instance

    actor = actor_from_instance(instance_id, agent_id, version_tag)
    try:
        row = _run_coro_sync(
            service.create_and_submit(
                agent_id,
                version_tag or "",
                artifact,
                mutator,
                actor,
                file_fallback=file_fallback,
            )
        )
    except PromptMutationRejected as exc:
        return {"success": False, "target": target, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 — surface per-target, keep compound alive
        return {
            "success": False,
            "target": target,
            "error": f"prompt-version submission failed: {exc}",
        }

    # Invalidate the prompt-cache key for THIS (agent_id, version_tag)
    # (D9 threading — Phase A T7). The loader's db_versions_fingerprint
    # (A8) self-heals regardless; the invalidate is belt-and-braces
    # latency hygiene, same as the file-write sites.
    if manager is not None and hasattr(manager, "prompt_cache"):
        manager.prompt_cache.invalidate(agent_id, version_tag=version_tag)

    return {
        "success": True,
        "target": target,
        "file": f"prompt-version v{row.version}",
        "db": True,
        "version": row.version,
        "version_id": row.version_id,
        "state": row.state,
        "message": (
            f"submitted as prompt-version v{row.version} (pending "
            "approval); takes effect once approved"
        ),
    }


# ============================================================
# Prompt-version DB submission — MEMORY path (User Directive #3,
# 2026-10-14, RESOLUTION-CORE)
# ============================================================


def _submit_memory_version(
    agent_id: str,
    version_tag: str | None,
    artifact: str,
    content: str,
    request: str,
    instance_id: str | None,
    manager: "InstanceManager | None",
    target: str,
) -> dict:
    """Persist a MEMORY-class row directly (Ud#3 binding blocks 1+2).

    Shared core of the DUAL-PATH rewrite for ``memory`` / ``user``
    / ``growth`` / ``memories/<slug>`` targets. FAIL-CLOSED like
    the CORE counterpart: when the service or the instance identity
    is unavailable, the change is refused — the file is NEVER
    written as a fallback.

    The caller has already COMPOSED the content string
    (file_fallback + delta / append-bullet / dedup-then-append).
    The MEMORY creator takes the whole content; no
    ``file_fallback`` argument (transition-free; the row IS the
    live state).

    Args:
        agent_id: Resolved base agent id (closure).
        version_tag: Version tag of the owning instance
            (``None`` normalizes to ``""`` in the repo).
        artifact: The DB artifact key for the row. For
            ``memories/<slug>`` writes this is the per-slug key
            (e.g. ``"memories/20260401_1430-foo"`` — without the
            ``.md`` extension); for ``memory`` / ``user`` /
            ``growth`` it is the canonical artifact string.
        content: The composed row body (the pre-compaction /
            pre-dedup logic already ran in the caller).
        request: The original request text (for error messages).
        instance_id: Submitting instance id (D10 audit identity).
        manager: InstanceManager (service + prompt_cache source).
        target: Response-grammar target name (``"memory"`` /
            ``"user"`` / ``"growth"`` / ``"memories"``).

    Returns:
        Result dict in the ``_execute_update`` shape; success rows
        carry ``db=True``, ``memory=True``, ``version``,
        ``version_id``, ``state``. The response-grammar field
        uses the "persisted (memory version N)" shape so
        prefix-parsing agents get a stable tail token.
    """
    service = _get_prompt_version_service(manager)
    if service is None:
        return {
            "success": False,
            "target": target,
            "error": (
                "prompt-version service unavailable — change NOT applied "
                f"({target} is an immutable MEMORY-class prompt artifact; "
                "file fallback is disabled)"
            ),
        }
    if not instance_id:
        return {
            "success": False,
            "target": target,
            "error": (
                "instance identity unavailable — change NOT persisted "
                "(MEMORY-class rows are stamped with the submitter "
                "instance id)"
            ),
        }

    from ..repositories.prompt_version.repository import actor_from_instance

    actor = actor_from_instance(instance_id, agent_id, version_tag)
    try:
        row = _run_coro_sync(
            service.create_memory_version(
                agent_id,
                version_tag or "",
                artifact,
                content,
                actor,
            )
        )
    except Exception as exc:  # noqa: BLE001 — surface per-target, keep compound alive
        return {
            "success": False,
            "target": target,
            "error": f"prompt-version memory persist failed: {exc}",
        }

    # Invalidate the prompt-cache key for THIS (agent_id, version_tag).
    # The loader's db_versions_fingerprint (A8) self-heals regardless;
    # the invalidate is belt-and-braces latency hygiene, same as the
    # CORE path. MEMORY rows affect the loader's MEMORY-family
    # resolution (memory / user / growth) — the fingerprint bust
    # reaches them all in one cache.set() per load_and_cache_prompt.
    if manager is not None and hasattr(manager, "prompt_cache"):
        manager.prompt_cache.invalidate(agent_id, version_tag=version_tag)

    return {
        "success": True,
        "target": target,
        "file": f"memory-version v{row.version}",
        "db": True,
        "memory": True,
        "version": row.version,
        "version_id": row.version_id,
        "state": row.state,
        "message": (
            f"persisted (memory version {row.version})"
        ),
    }


def _make_soul_mutator(request: str, rules: dict):
    """Build the soul mutator — byte-preserving port of the pre-B logic.

    ``current`` is the effective-chain content (``None`` when no DB
    row and no template file exist); name-change requests replace the
    ``**My name is ...**`` line in place, everything else appends a
    bullet. The ``max_soul_chars`` cap raises
    :class:`PromptMutationRejected` (no row, no file write).
    """

    def mutator(current: str | None) -> str:
        if current is None:
            current = "# Who I Am\n\n"

        max_chars = rules.get("max_soul_chars", 2000)
        if len(current) >= max_chars:
            raise PromptMutationRejected(
                f"soul.md at {len(current)} chars (max {max_chars}). Cannot add more."
            )

        lines = current.rstrip().split("\n")

        request_lower = request.lower()
        is_name_change = any(
            p in request_lower
            for p in [
                "my name is",
                "i am called",
                "call me",
                "remember my name",
                "remember your name",
            ]
        )

        if is_name_change:
            name = (
                request.split("name is")[-1]
                .split("called")[-1]
                .strip()
                .rstrip(".")
            )
            formatted = f"**My name is {name}**"
            insert_idx = 1  # After first line (header)
            while insert_idx < len(lines) and lines[insert_idx].strip() == "":
                insert_idx += 1
            for i, line in enumerate(lines):
                if line.startswith("**My name is"):
                    lines[i] = formatted
                    formatted = None  # Updated in place, not inserted
                    break
        else:
            formatted = f"- {request}"
            insert_idx = len(lines)

        if formatted:
            lines.insert(insert_idx, formatted)
        return "\n".join(lines)

    return mutator


def _make_user_mutator(request: str):
    """Build the user mutator (D5) — placeholder-strip + append bullet.

    Byte-preserving port of the pre-B ``_update_user`` body; the
    ``None`` current (no DB row, no template) seeds the historical
    ``# User\\n\\n`` default.
    """

    def mutator(current: str | None) -> str:
        if current is None:
            current = "# User\n\n"

        # Remove placeholder
        if "(To be filled" in current:
            current = current.split("(To be filled")[0].rstrip()

        return f"{current}\n- {request}"

    return mutator


def _make_workflow_mutator(request: str):
    """Build the workflow mutator — ensure ``**Learned:**`` + append bullet.

    Byte-preserving port of the pre-B ``_update_workflow`` body.
    """

    def mutator(current: str | None) -> str:
        if current is None:
            current = "# Workflow\n\n"

        if "**Learned:**" not in current:
            current += "\n\n---\n\n**Learned:**\n"

        return f"{current}\n- {request}"

    return mutator


def _execute_update(
    agent_id: str,
    agent_path: Path,
    request: str,
    target: str,
    intent: str | None,
    rules: dict,
    manager: "InstanceManager",
    classification: dict,
    version_tag: str | None = None,
    instance_id: str | None = None,
) -> dict:
    """Execute an update to a specific target.

    DUAL-PATH dispatch (User Directive #3, 2026-10-14,
    RESOLUTION-CORE):

    * CORE (soul / workflow) — :func:`_submit_prompt_version` →
      DB draft, ``pending_approval``, no file write.
    * MEMORY (memory / user / growth / memories/<slug>) —
      :func:`_submit_memory_version` → MEMORY-class row,
      born-effective, NO approval. ``memory.md`` and
      ``memories/<slug>`` writes use the cold-boot file as
      the F6 chain source; ``user.md`` / ``growth.md`` follow
      the same pattern (their files are also cold-boot
      fallbacks).

    ``instance_id`` threads the D10 audit identity; ``version_tag``
    threads the F9 / R10 cache-bust key.
    """
    if target == "memories":
        return _update_memories(
            agent_id, agent_path, request, classification,
            manager, version_tag=version_tag, instance_id=instance_id,
        )
    elif target == "memory":
        return _update_memory_md(
            agent_id, agent_path, request, rules,
            manager, version_tag=version_tag, instance_id=instance_id,
        )
    elif target == "soul":
        return _update_soul(
            agent_id, agent_path, request, rules, manager,
            version_tag=version_tag, instance_id=instance_id,
        )
    elif target == "user":
        return _update_user(
            agent_id, agent_path, request, manager,
            version_tag=version_tag, instance_id=instance_id,
        )
    elif target == "workflow":
        return _update_workflow(
            agent_id, agent_path, request, rules, manager,
            version_tag=version_tag, instance_id=instance_id,
        )
    elif target == "growth":
        # Growth target (Ud#3 supersedes the implicit growth
        # gate): MEMORY-class, whole-content replace via the
        # user mutator equivalent. The caller composes the
        # new growth.md body — typically a config knob
        # update. The legacy `_load_growth_rules` parser
        # runs against the cold-boot file fallback; the
        # DB row is the live source when present.
        return _update_growth(
            agent_id, agent_path, request,
            manager, version_tag=version_tag, instance_id=instance_id,
        )
    else:
        return {"success": False, "target": target, "error": f"Unknown target: {target}"}


def _update_memories(
    agent_id: str,
    agent_path: Path,
    request: str,
    classification: dict,
    manager: "InstanceManager" | None = None,
    version_tag: str | None = None,
    instance_id: str | None = None,
) -> dict:
    """Persist a per-slug memory row (User Directive #3, 2026-10-14).

    Pre-rewrite this wrote a timestamped ``.md`` file under
    ``<agent>/memories/``. Since the DUAL-PATH rewrite, the
    canonical state is a MEMORY-class row keyed
    ``memories/<slug>`` where ``<slug>`` is the legacy timestamped
    filename WITHOUT the ``.md`` extension.

    Consecutive writes on the same slug land as version 1, 2, 3,
    ... on the same key — the legacy collision-counter that
    derived ``<filename>-<n>.md`` is retired; the version chain
    IS the lineage. The on-disk file under ``memories/`` is
    NEVER touched by this path; it remains the cold-boot F6
    fallback (read by ``access_memory`` when no DB row exists).

    The row body preserves the legacy file-shape header
    (Created / Type / Description / content) for parity with
    the historical rendering that the file writer produced.
    """
    now = datetime.now()
    timestamp = now.strftime("%Y%m%d_%H%M")
    desc = _slugify(request[:80])
    slug = f"{timestamp}-{desc}"

    # Same header shape as the legacy file-writer for content
    # parity (Created / Type / Description / body).
    content = (
        f"# Memory\n\n"
        f"**Created:** {now.strftime('%Y-%m-%d %H:%M')}\n"
        f"**Type:** {classification['type']}\n"
        f"**Description:** {classification['description']}\n\n"
        f"{request}\n"
    )

    return _submit_memory_version(
        agent_id=agent_id,
        version_tag=version_tag,
        artifact=f"memories/{slug}",
        content=content,
        request=request,
        instance_id=instance_id,
        manager=manager,
        target="memories",
    )


def _format_rejection(target: str, max_words: int, word_count: int, rules: dict | None = None) -> dict:
    """Format rejection message for memory limit exceeded."""
    return {
        "success": False,
        "target": target,
        "error": f"Memory limit exceeded ({word_count} >= {max_words} words). Content was not saved."
    }


def _update_memory_md(
    agent_id: str,
    agent_path: Path,
    request: str,
    rules: dict,
    manager: "InstanceManager" | None = None,
    version_tag: str | None = None,
    instance_id: str | None = None,
) -> dict:
    """Persist a memory.md change as a MEMORY-class row (Ud#3).

    Pre-rewrite this flock-locked ``memory.md`` and applied the
    80% proactive compaction + dedup. Since the DUAL-PATH
    rewrite, the canonical state is a MEMORY-class row keyed
    ``memory``; the on-disk ``memory.md`` is NEVER touched by
    this path.

    Compaction as a new version (Ud#3): the 80% proactive
    compaction rule is preserved as a SEMANTIC guardrail — the
    composer's job is to keep the ``current`` chain under the
    ``max_memory_words`` cap. The pre-compaction content
    survives in history: it's a prior version on the same
    ``memory`` key, the loader's ``latest_content`` flips to
    the post-compaction row.

    The chain source: ``file_fallback`` (the on-disk file
    content read at submit time, for cold-boot with zero DB
    rows) is folded into the new row when present. Dedup is
    preserved — a duplicate bullet short-circuits to a no-op
    success result WITHOUT persisting a new version (the
    chain stays at its current max).
    """
    memory_file = agent_path / "memory.md"
    try:
        # Read the on-disk file as the cold-boot F6 fallback.
        # No flock — the DB transaction is the serialization
        # point; a torn read here only degrades the chain
        # source, never correctness.
        current = (
            memory_file.read_text(encoding="utf-8")
            if memory_file.exists() else "# Memory\n\n"
        )
    except OSError:
        current = "# Memory\n\n"

    word_count = len(current.split())
    max_words = rules.get("max_memory_words", 2000)

    # 80% proactive compaction guardrail (preserved verbatim
    # from the pre-rewrite logic — Ud#3: compaction lives in
    # the composer, not in a post-insert hook).
    should_compact = word_count > max_words * 0.8
    if word_count >= max_words:
        current = _compact_memory(current)
        word_count = len(current.split())
        if word_count >= max_words:
            return _format_rejection("memory", max_words, word_count, rules)
    elif should_compact:
        current = _compact_memory(current)
        word_count = len(current.split())

    lines = current.rstrip().split('\n')
    # Find insertion point (before "*For events" marker or HTML comments)
    insert_idx = len(lines)
    for i, line in enumerate(lines):
        if line.startswith("*For events") or line.startswith("<!--"):
            insert_idx = i
            break

    # Dedup check (preserved) — short-circuit before persisting.
    normalized_new = request.strip().lower()
    for line in lines:
        if line.strip().lower() == f"- {normalized_new}":
            return {
                "success": True,
                "action": "skipped",
                "target": "memory",
                "message": "Entry already exists in memory.md (dedup)",
                "compact": False,
            }

    lines.insert(insert_idx, f"\n- {request}")
    new_content = '\n'.join(lines)

    result = _submit_memory_version(
        agent_id=agent_id,
        version_tag=version_tag,
        artifact="memory",
        content=new_content,
        request=request,
        instance_id=instance_id,
        manager=manager,
        target="memory",
    )
    # Tail the success body with the compaction flag (parity
    # with the pre-rewrite contract; the agent_self tool
    # contract surface stays the same). The CORE
    # response-grammar field stays as the canonical
    # ``persisted (memory version N)`` — the parity tail lives
    # on a SEPARATE field (``memory_file_hint``) so the
    # prefix-parsing agents only need the standard tail.
    if result.get("success"):
        result["action"] = "updated"
        result["compact"] = should_compact
        result["memory_file_hint"] = "memory.md"
    return result


def _update_soul(agent_id: str, agent_path: Path, request: str, rules: dict, manager: "InstanceManager" | None = None, version_tag: str | None = None, instance_id: str | None = None) -> dict:
    """Submit a soul.md change as a prompt-version draft (Phase B T7).

    Pre-B this wrote ``soul.md`` atomically and appended a
    ``history/*_soul_change.md`` audit file. Since Phase B the change
    is submitted to the prompt-version DB (D4 auto-submit →
    ``pending_approval``) and the file is NEVER touched:

    * Composition (C4): the mutator (:func:`_make_soul_mutator`)
      applies the name-change/append semantics to the EFFECTIVE chain
      (selected > latest_approved > file_fallback).
    * Audit (D10): the ``history/`` file is retired — the DB audit
      events (CREATED/SUBMITTED + ARCHIVED on supersession) stamped
      with the submitter instance identity replace it; ``history/``
      is no longer created.
    * Fail-closed: service/identity unavailable → ``⚠`` refusal, no
      file fallback. The Phase A write-guard shim stays on the
      ``write_file``/``edit_file`` surfaces as defense-in-depth.
    """
    return _submit_prompt_version(
        agent_id=agent_id,
        version_tag=version_tag,
        artifact="soul",
        mutator=_make_soul_mutator(request, rules),
        request=request,
        instance_id=instance_id,
        agent_path=agent_path,
        manager=manager,
        target="soul",
    )


def _update_user(agent_id: str, agent_path: Path, request: str, manager: "InstanceManager" | None = None, version_tag: str | None = None, instance_id: str | None = None) -> dict:
    """Persist a user.md change as a MEMORY-class row (Ud#3 SUPERSEDES D5).

    Pre-B this appended to ``user.md`` in place. Phase B D5
    tentatively bound user.md to the CORE path (state machine
    + ``pending_approval``); User Directive #3 (2026-10-14)
    SUPERSEDES that — user.md is MEMORY-class now, persists
    directly via ``create_memory_version``, NO approval
    round-trip. The user's preferences take effect on the next
    prompt reload.

    The body still uses the placeholder-strip + append-bullet
    composition, but it is computed in the caller (we read
    the on-disk file as the F6 fallback for cold boots) and
    persisted as a whole-content row via the MEMORY creator.
    The ``agent_mother.agent_modify`` bypass closes for
    user.md too — the same class-aware dispatch routes it
    through ``create_memory_version``.
    """
    user_file = agent_path / "user.md"
    try:
        current = (
            user_file.read_text(encoding="utf-8")
            if user_file.exists() else "# User\n\n"
        )
    except OSError:
        current = "# User\n\n"

    # Reuse the byte-preserving user mutator (placeholder-strip
    # + append bullet) on the cold-boot F6 source.
    new_content = _make_user_mutator(request)(current)

    return _submit_memory_version(
        agent_id=agent_id,
        version_tag=version_tag,
        artifact="user",
        content=new_content,
        request=request,
        instance_id=instance_id,
        manager=manager,
        target="user",
    )


def _update_workflow(agent_id: str, agent_path: Path, request: str, rules: dict, manager: "InstanceManager" | None = None, version_tag: str | None = None, instance_id: str | None = None) -> dict:
    """Submit a workflow.md change as a prompt-version draft (Phase B T7).

    Pre-B this appended to ``workflow.md``'s ``**Learned:**`` section
    in place. Since Phase B the change is submitted to the
    prompt-version DB (D4 auto-submit → ``pending_approval``); the
    mutator (:func:`_make_workflow_mutator`) ensures the
    ``**Learned:**`` section exists on the EFFECTIVE chain and
    appends the bullet. Fail-closed on missing service/identity.
    """
    return _submit_prompt_version(
        agent_id=agent_id,
        version_tag=version_tag,
        artifact="workflow",
        mutator=_make_workflow_mutator(request),
        request=request,
        instance_id=instance_id,
        agent_path=agent_path,
        manager=manager,
        target="workflow",
    )


def _update_growth(
    agent_id: str,
    agent_path: Path,
    request: str,
    manager: "InstanceManager" | None = None,
    version_tag: str | None = None,
    instance_id: str | None = None,
) -> dict:
    """Persist a growth.md change as a MEMORY-class row (Ud#3).

    Pre-rewrite growth.md was config-only — read by
    ``_load_growth_rules`` to derive ``max_memory_words`` /
    ``max_soul_chars`` / etc., and otherwise dormant. The
    directive re-disposes it as MEMORY-class: the cold-boot
    F6 file is the fallback when no DB row exists, and the DB
    row is the live source otherwise.

    Whole-content replace semantics: the caller passes the
    new growth.md body in ``request`` (a free-form config
    block; the parser only consumes the ``max_memory_words``
    / ``max_soul_chars`` / ``archive ... days`` regex
    matches). The file fallback is read for cold boots.
    """
    growth_file = agent_path / "growth.md"
    try:
        current = (
            growth_file.read_text(encoding="utf-8")
            if growth_file.exists() else "# Growth\n\n"
        )
    except OSError:
        current = "# Growth\n\n"

    # Whole-content replace (the caller sends the new body).
    # For empty/append-style requests, fall back to
    # append-on-the-chain semantics matching the file's
    # historical "config knobs" shape.
    new_body = (request or "").strip()
    if not new_body:
        new_content = current
    elif new_body.startswith("#") or "\n" in new_body or len(new_body) > 200:
        # Full block — replace the file body.
        new_content = new_body
    else:
        # Single line — append under the existing chain.
        if current.rstrip() == "":
            new_content = "# Growth\n\n" + new_body + "\n"
        else:
            new_content = current.rstrip() + "\n" + new_body + "\n"

    return _submit_memory_version(
        agent_id=agent_id,
        version_tag=version_tag,
        artifact="growth",
        content=new_content,
        request=request,
        instance_id=instance_id,
        manager=manager,
        target="growth",
    )


def _format_response(request: str, results: list, classification: dict) -> str:
    """Format the response to show what was done.

    Per-target line grammar (DUAL-PATH, Ud#3 / R7):

    * CORE DB-submitted targets (soul/workflow): ``  📋 <target>:
      submitted as prompt-version v<N> (pending approval); takes
      effect once approved`` — the D4 pending-approval grammar.
      The line keeps the two-space-indent + marker + ``<target>:``
      shape so prefix-parsing agents only need the new marker
      (R7; exact strings pinned in
      ``tests/test_inner_soul_db.py``).
    * MEMORY DB-submitted targets (memory/user/growth/
      memories/<slug>): ``  ✓ <target>: persisted (memory
      version N)`` — the born-effective grammar. ``✓`` marker
      chosen for "no approval needed" (the file was the prior
      state; the row is the new state, and the row is live
      immediately). The two-space indent + ``<target>:`` shape
      is preserved for prefix-parsing.
    * Failures: ``  ⚠ <target>: <error>`` — unchanged.
    """
    lines = [f"✓ Processed: \"{request[:80]}{'...' if len(request) > 80 else ''}\""]
    lines.append(f"  Classification: {classification['type']} ({classification['description']})")
    lines.append("")

    for r in results:
        if r.get("success"):
            target = r.get("target", "unknown")
            file = r.get("file", "")
            status = r.get("status", "")

            if r.get("db") and r.get("memory"):
                # MEMORY path (Ud#3): born-effective, no approval.
                lines.append(f"  ✓ {target}: {r.get('message', 'persisted')}")
                continue

            if r.get("db"):
                # CORE path: DB-submitted draft — pending-approval grammar.
                lines.append(f"  📋 {target}: {r.get('message', 'submitted')}")
                continue

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
    
    content = growth_file.read_text(encoding="utf-8")
    
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
