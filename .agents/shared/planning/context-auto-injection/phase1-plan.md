# Phase 1: Infrastructure — Context Injection Service

## Objective
Create a standalone, reusable service module (`daemon/services/context_injection.py`) with a clean public API for context auto-injection. The module provides `get_shared_context(context_key, query) -> str | None` that any caller (explore tool, MCP server, external integrations) can use. Internal matching and injection helpers stay private within the module.

## Coupling
- **Depends on**: None
- **Coupling type**: independent (root phase)
- **Shared files with other phases**: `daemon/services/context_injection.py` is consumed by Phase 3 (imported by knowledge_tools.py)
- **Shared APIs/interfaces**: The concise section format (`## Concise\n...`) is the contract with Phase 2. The public API `get_shared_context()` is the contract with Phase 3.
- **Why this coupling**: Phase 1 defines the service and its public API; Phase 2 produces the Concise format the service parses; Phase 3 wires the service into the explore flow.

## Context
Current state:
- Context files live at `{tempdir}/ensemble/context/{context_key}/{slug}_{timestamp}.md`
- Slug is query-derived: `re.sub(r'[^a-z0-9]+', '-', query.lower()).strip('-')[:80]`
- File content format: `# Explorer Result: {query}\n**Time**: ...\n**Project**: ...\n**Mode**: ...\n\n{result}`
- The `result` body contains `## Confidence: ...\n## Answer\n...\n## Related Experience\n...\n## Sources\n...`

## Public API

```python
# daemon/services/context_injection.py

def get_shared_context(context_key: str, query: str) -> str | None:
    """Get formatted injection text for matched context files.
    
    Scans the shared context directory for files matching the query,
    extracts tiered content (full answer / concise / first sentence),
    and returns a formatted markdown string for injection.
    
    Args:
        context_key: The context directory key (root instance ID).
        query: The user's query to match against file slugs.
    
    Returns:
        Formatted injection string, or None if no matches / errors.
        Never raises — all errors are logged and return None.
    """
```

**Design rationale for `str | None` return:**
- `None` = no injection (empty dir, no matches, or any error) — caller simply skips
- Non-empty string = injection text — caller appends to message
- This is the simplest possible contract. No dataclasses, no tuples, no complexity for callers.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create `daemon/services/context_injection.py` | New standalone module. Contains public `get_shared_context()` + private helpers. No dependency on knowledge_tools or agent system. | `daemon/services/context_injection.py` |
| 2 | Add `_tokenize_slug(slug: str) -> set[str]` | Extract meaningful tokens from filename slug (split on `-`, filter stop words, filter <2 chars). Private helper. | `daemon/services/context_injection.py` |
| 3 | Add `_tokenize_query(query: str) -> set[str]` | Same tokenization applied to raw query text. Shared stopword list. Private helper. | `daemon/services/context_injection.py` |
| 4 | Add `_match_score(query_tokens: set[str], slug_tokens: set[str]) -> float` | **Recall-oriented asymmetric scoring**: `len(intersection) / len(query_tokens)`. Falls back to Jaccard when both sets ≥3 tokens. Handle empty sets → 0.0. Private helper. | `daemon/services/context_injection.py` |
| 5 | Add `_extract_slug_from_filename(filename: str) -> str` | Strip timestamp suffix: remove `_YYYYMMDD_HHMMSS.md` pattern. Private helper. | `daemon/services/context_injection.py` |
| 6 | Add `_parse_sections(content: str) -> dict[str, str]` | Parse markdown file into sections by `## Heading` markers. Private helper. | `daemon/services/context_injection.py` |
| 7 | Add `_extract_first_sentence(text: str) -> str` | Split on `.`, `!`, `?` followed by space or end. Return first sentence. Private helper. | `daemon/services/context_injection.py` |
| 8 | Add `_truncate_to_tokens(text: str, max_tokens: int) -> str` | Rough token estimation (~4 chars per token). Truncate at sentence boundary if possible, hard cut at word boundary otherwise. Append `...` if truncated. Private helper. | `daemon/services/context_injection.py` |
| 9 | Add `_match_context_files(query: str, context_dir: Path) -> list[MatchedFile]` | Core matching. Scans up to 50 most recent .md files, per-file error handling. Private helper. | `daemon/services/context_injection.py` |
| 10 | Add `_format_injection(matched_files: list[MatchedFile]) -> str` | Tiered extraction + global token cap. Private helper. Returns empty string if no matches. | `daemon/services/context_injection.py` |
| 11 | Implement `get_shared_context(context_key, query) -> str | None` | Public API. Resolves context dir from key, calls `_match_context_files` + `_format_injection`, wraps everything in try/except returning None on any error. | `daemon/services/context_injection.py` |

## Detailed Function Designs

### Data Structures

```python
from dataclasses import dataclass

@dataclass
class MatchedFile:
    filename: str              # Full filename (e.g., "auth-module-jwt-tokens_20260531_231255.md")
    slug: str                  # Slug part only (e.g., "auth-module-jwt-tokens")
    score: float               # Match score [0.0, 1.0] (asymmetric recall-oriented)
    sections: dict[str, str]   # Parsed sections from file content (key = heading name)
    first_sentence: str        # First sentence of Concise or Answer section

# Tier thresholds
TIER_HIGH = 0.80
TIER_MEDIUM = 0.60
TIER_LOW = 0.40

# Token limits per tier (individual file limits)
TOKEN_LIMIT_HIGH = 800
TOKEN_LIMIT_MEDIUM = 200
TOKEN_LIMIT_LOW = 50

# Global injection token cap — total injection content never exceeds this
# Prevents context window overflow: worst case 3×800 + N×200 + index could hit 3000-5000+ tokens
INJECTION_TOKEN_CAP = 2000

# Max files to inject at high tier
MAX_HIGH_TIER_FILES = 3
```

### Stop Words List
```python
_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "dare", "ought",
    "used", "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "as", "into", "through", "during", "before", "after", "above", "below",
    "between", "out", "off", "over", "under", "again", "further", "then",
    "once", "and", "but", "or", "nor", "not", "so", "yet", "both", "either",
    "neither", "each", "every", "all", "any", "few", "more", "most", "other",
    "some", "such", "no", "only", "own", "same", "than", "too", "very",
    "just", "because", "if", "when", "where", "how", "what", "which", "who",
})
```

### `_tokenize_slug(slug: str) -> set[str]`
```python
def _tokenize_slug(slug: str) -> set[str]:
    """Tokenize a filename slug into a set of meaningful tokens."""
    raw_tokens = slug.split("-")
    return {
        t for t in raw_tokens
        if len(t) >= 2 and t not in _STOP_WORDS
    }
```

### `_tokenize_query(query: str) -> set[str]`
```python
def _tokenize_query(query: str) -> set[str]:
    """Tokenize a query string into a set of meaningful tokens."""
    # Lowercase, replace non-alphanumeric with spaces, split
    normalized = re.sub(r'[^a-z0-9]', ' ', query.lower())
    raw_tokens = normalized.split()
    return {
        t for t in raw_tokens
        if len(t) >= 2 and t not in _STOP_WORDS
    }
```

### `_match_score(query_tokens: set[str], slug_tokens: set[str]) -> float`
```python
def _match_score(query_tokens: set[str], slug_tokens: set[str]) -> float:
    """Compute match score between query tokens and slug tokens.
    
    Uses recall-oriented asymmetric scoring: |intersection| / |query_tokens|.
    This avoids penalizing short queries against long slugs.
    Example: query={"auth","module"} vs slug={"auth","module","jwt","tokens"}
      → 2/2 = 1.0 (perfect recall) instead of Jaccard 2/4 = 0.5.
    
    Fallback: When both sets have ≥3 tokens, uses classic Jaccard
    (len(intersection) / len(union)) to reward specificity and avoid
    over-matching on vague short queries that happen to share one token.
    
    Returns 0.0 for empty query_tokens or slug_tokens.
    """
    if not query_tokens or not slug_tokens:
        return 0.0
    
    intersection = query_tokens & slug_tokens
    if not intersection:
        return 0.0
    
    # When both sets are substantive, use Jaccard to reward specificity
    if len(query_tokens) >= 3 and len(slug_tokens) >= 3:
        union = query_tokens | slug_tokens
        return len(intersection) / len(union)
    
    # Asymmetric recall: what fraction of query tokens appear in slug?
    return len(intersection) / len(query_tokens)
```

### `_match_context_files(query: str, context_dir: Path) -> list[MatchedFile]`
```python
def _match_context_files(query: str, context_dir: Path) -> list[MatchedFile]:
    """Find and score context files relevant to the query.
    
    Scans up to 50 most recent .md files in context_dir.
    Returns list sorted by match score (highest first).
    Files below TIER_LOW threshold are excluded.
    Individual file read errors are caught per-file (skip + log debug).
    """
    if not context_dir.is_dir():
        return []
    
    query_tokens = _tokenize_query(query)
    if not query_tokens:
        return []
    
    # Get all .md files sorted by mtime (most recent first), cap at 50
    md_files = sorted(
        context_dir.glob("*.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:50]
    
    matches = []
    for md_file in md_files:
        try:
            slug = _extract_slug_from_filename(md_file.name)
            slug_tokens = _tokenize_slug(slug)
            
            if not slug_tokens:
                continue
            
            score = _match_score(query_tokens, slug_tokens)
            if score < TIER_LOW:
                continue
            
            # Parse file content
            content = md_file.read_text(encoding="utf-8", errors="replace")
            sections = _parse_sections(content)
            
            # Extract first sentence from Concise section (or Answer as fallback)
            concise = sections.get("Concise", "")
            answer = sections.get("Answer", "")
            source_text = concise or answer
            first_sentence = _extract_first_sentence(source_text) if source_text else ""
            
            matches.append(MatchedFile(
                filename=md_file.name,
                slug=slug,
                score=score,
                sections=sections,
                first_sentence=first_sentence,
            ))
        except Exception as e:
            # Per-file error: skip this file, don't abort the scan
            logger.debug("Failed to process context file %s: %s", md_file.name, e)
            continue
    
    # Sort by score descending
    matches.sort(key=lambda m: m.score, reverse=True)
    return matches
```

### `_format_injection(matched_files: list[MatchedFile]) -> str`
```python
def _format_injection(matched_files: list[MatchedFile]) -> str:
    """Format matched files into injection text for the explorer agent.
    
    Tiered content extraction:
    - HIGH (≥0.8): Answer section, truncated to 800 tokens, max 3 files
    - MEDIUM (≥0.6): Concise section only, ~200 tokens
    - LOW (≥0.4): First sentence of concise, ~50 tokens
    
    Enforces global token cap (INJECTION_TOKEN_CAP = 2000 tokens):
    - Estimates tokens as the injection is built
    - When cap is reached, proportionally reduces remaining tier limits
    - File index does NOT count toward the cap (it's always appended)
    
    Returns empty string if no matches.
    """
    if not matched_files:
        return ""
    
    parts = ["## Pre-loaded Context (auto-matched)\n"]
    estimated_tokens = 0  # Running total toward INJECTION_TOKEN_CAP
    
    high_count = 0
    for mf in matched_files:
        score_pct = int(mf.score * 100)
        
        # Calculate remaining budget
        remaining_budget = INJECTION_TOKEN_CAP - estimated_tokens
        if remaining_budget <= 0:
            break  # Global cap reached — stop adding content
        
        if mf.score >= TIER_HIGH and high_count < MAX_HIGH_TIER_FILES:
            # HIGH tier: Answer section, truncated
            answer = mf.sections.get("Answer", "")
            if answer:
                # Proportionally reduce if budget is tight
                effective_limit = min(TOKEN_LIMIT_HIGH, remaining_budget)
                content = _truncate_to_tokens(answer, effective_limit)
                parts.append(f"### {mf.slug} ({score_pct}% match)\n{content}\n")
                estimated_tokens += len(content) // 4  # Rough token estimate
                high_count += 1
            else:
                continue
        
        elif mf.score >= TIER_MEDIUM:
            # MEDIUM tier: Concise section
            concise = mf.sections.get("Concise", "")
            if concise:
                effective_limit = min(TOKEN_LIMIT_MEDIUM, remaining_budget)
                content = _truncate_to_tokens(concise, effective_limit)
                parts.append(f"### {mf.slug} ({score_pct}% match)\n{content}\n")
                estimated_tokens += len(content) // 4
            else:
                # Fallback: first sentence of Answer
                if mf.first_sentence:
                    parts.append(f"### {mf.slug} ({score_pct}% match)\n{mf.first_sentence}\n")
                    estimated_tokens += len(mf.first_sentence) // 4
        
        elif mf.score >= TIER_LOW:
            # LOW tier: first sentence only
            if mf.first_sentence:
                effective_limit = min(TOKEN_LIMIT_LOW, remaining_budget)
                sentence = mf.first_sentence[:effective_limit * 4]  # Hard char cap
                parts.append(f"### {mf.slug} ({score_pct}% match)\n{sentence}\n")
                estimated_tokens += len(sentence) // 4
    
    # File index (inlined — does NOT count toward token cap)
    all_for_index = matched_files[:30]  # Cap at 30 rows
    if all_for_index:
        parts.append("---\n\n## Available Context Files\n")
        parts.append("| File | Summary |\n|------|----------|\n")
        for mf in all_for_index:
            summary = mf.first_sentence[:80] + ("..." if len(mf.first_sentence) > 80 else "")
            parts.append(f"| {mf.slug} | {summary} |\n")
    
    return "".join(parts)
```

### `get_shared_context(context_key: str, query: str) -> str | None`
```python
def get_shared_context(context_key: str, query: str) -> str | None:
    """Get formatted injection text for matched context files.
    
    Scans the shared context directory for files matching the query,
    extracts tiered content (full answer / concise / first sentence),
    and returns a formatted markdown string for injection.
    
    This is the PUBLIC API of the context injection service.
    Callers: explore() tool, MCP server, external integrations.
    
    Args:
        context_key: The context directory key (root instance ID).
        query: The user's query to match against file slugs.
    
    Returns:
        Formatted injection string, or None if no matches / errors.
        Never raises — all errors are logged and return None.
    """
    try:
        context_dir = Path(tempfile.gettempdir()) / "ensemble" / "context" / context_key
        
        matched = _match_context_files(query, context_dir)
        if not matched:
            return None
        
        injection = _format_injection(matched)
        if not injection:
            return None
        
        logger.debug(
            "Context auto-injection: %d files matched for query '%s'",
            len(matched), query[:50],
        )
        return injection
    
    except Exception as e:
        logger.debug("Context auto-injection failed (non-critical): %s", e)
        return None
```

### Phase 3 Integration (how explore() calls this)

```python
# In daemon/tools/knowledge_tools.py explore() tool:
from daemon.services.context_injection import get_shared_context

# After building explorer_message, before invoke_agent_and_wait():
if context_key:
    context_dir_path = Path(tempfile.gettempdir()) / "ensemble" / "context" / context_key
    explorer_message += f"\nShared context dir: {str(context_dir_path)}"
    
    # Auto-inject via reusable service (runs on thread pool to avoid blocking)
    try:
        injection = await asyncio.to_thread(get_shared_context, context_key, query)
        if injection:
            explorer_message += f"\n\n{injection}"
    except Exception as e:
        logger.debug("Context auto-injection failed (non-critical): %s", e)
```

## Key Files
- `daemon/services/context_injection.py` — **New module.** Public API + all private helpers. Self-contained, no dependency on knowledge_tools or agent system.

## Constraints
- **Standalone module** — `daemon/services/context_injection.py` has zero imports from `daemon/tools/` or agent code. Only stdlib dependencies (`re`, `pathlib`, `dataclasses`, `tempfile`, `logging`).
- **Single public function** — `get_shared_context()` is the only export. Everything else is private (`_` prefixed).
- **Never raises** — `get_shared_context()` catches all exceptions internally and returns `None`.
- All internal functions are **pure** (no I/O side effects except `_match_context_files` which reads files)
- File reading uses `errors="replace"` to handle encoding issues gracefully
- Maximum 50 files scanned per call to prevent latency
- Global injection token cap of 2000 prevents context window overflow
- Individual file read errors in `_match_context_files` are caught per-file (skip + debug log, don't abort scan)
- `_truncate_to_tokens` hard-cut fallback splits at word boundaries (last space before char limit)

## Deliverables
- [ ] `daemon/services/context_injection.py` created
- [ ] `get_shared_context()` public API implemented (never raises, returns `str | None`)
- [ ] `_tokenize_slug()` implemented and tested
- [ ] `_tokenize_query()` implemented and tested
- [ ] `_match_score()` implemented and tested (asymmetric recall + Jaccard fallback)
- [ ] `_extract_slug_from_filename()` implemented and tested
- [ ] `_parse_sections()` implemented and tested
- [ ] `_extract_first_sentence()` implemented and tested
- [ ] `_truncate_to_tokens()` implemented and tested (including word-boundary hard cut)
- [ ] `_match_context_files()` implemented and tested (including per-file error handling)
- [ ] `_format_injection()` implemented and tested (including global token cap)
- [ ] `MatchedFile` dataclass defined with `dict[str, str]` type hint on sections
- [ ] Tier constants, token caps, and stop words defined
