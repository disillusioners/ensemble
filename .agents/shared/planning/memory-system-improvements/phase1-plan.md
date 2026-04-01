# Phase 1: Better File Naming + Word Limit Increase

## Objective
Change memory file naming from `{timestamp}_{classification_type}_{slug}.md` to `{datetime}-{description}.md` for human readability. Increase default `memory.md` word limit from 500 to 2000.

## Context
- Current naming: `20260401_1430_knowledge_remember_that_user_pref.md` — classification prefix adds noise
- Target naming: `20260401_1430-remember-user-prefers-terse-replies.md` — clean, descriptive, readable
- Word limit 500 is too restrictive for agents accumulating real project knowledge

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Update `_update_memories()` filename format | Remove classification type prefix, use hyphen-separated description | `daemon/tools/inner_soul.py` L336-374 |
| 2 | Update `_slugify()` function | Use hyphens instead of underscores, keep 50 char limit for description | `daemon/tools/inner_soul.py` L602-607 |
| 3 | Derive description from content | Use first meaningful words from the memory content (not classification type) | `daemon/tools/inner_soul.py` L345 |
| 4 | Update `_load_growth_rules()` default | Change `max_memory_words` default from 500 to 2000 | `daemon/tools/inner_soul.py` L578, L586 |
| 5 | Update baby template | Update `agents/_baby_template/growth.md` to document new default | `agents/_baby_template/growth.md` |

## Implementation Details

### Task 1-3: New filename format

**Current code** (`_update_memories()` L342-354):
```python
timestamp = now.strftime("%Y%m%d_%H%M")
desc = _slugify(request[:50])
class_prefix = classification["type"]
filename = f"{timestamp}_{class_prefix}_{desc}.md"
```

**New code:**
```python
timestamp = now.strftime("%Y%m%d_%H%M")
desc = _slugify(request[:80])  # More chars for better description
filename = f"{timestamp}-{desc}.md"
```

**Updated `_slugify()` (L602-607):**
```python
def _slugify(text: str) -> str:
    """Convert text to readable hyphenated slug."""
    text = text.lower()
    text = re.sub(r'[^a-z0-9]+', '-', text)  # Hyphens, not underscores
    text = text.strip('-')
    return text[:60] if text else "memory"  # Longer: 60 chars
```

**Example outputs:**
```
Old: 20260401_1430_knowledge_remember_that_user_pref.md
New: 20260401_1430-remember-user-prefers-terse-replies.md

Old: 20260401_1430_pattern_async_api_patterns.md
New: 20260401_1430-async-api-patterns-for-retry-logic.md

Old: 20260401_1430_event_deployment_to_prod_done.md
New: 20260401_1430-deployment-to-prod-completed.md
```

### Task 4: Word limit increase

**Change in `_load_growth_rules()` (L578 and L586):**
```python
# Before:
rules = {"max_memory_words": 500, ...}
# After:
rules = {"max_memory_words": 2000, ...}
```

Two places: the early return (L578) and the default dict (L586).

## Key Files
- `daemon/tools/inner_soul.py` — All changes in this file except task 5
  - `_update_memories()` (L336-374) — filename construction
  - `_slugify()` (L602-607) — slug generation
  - `_load_growth_rules()` (L573-599) — word limit defaults
- `agents/_baby_template/growth.md` — document new default

## Constraints
- Existing memory files with old naming convention should still be readable (they're just files on disk)
- The `classification_type` is still stored **inside** the file content, just not in the filename
- Don't change the duplicate counter logic (L351-354) — just update the base format

## Deliverables
- [ ] `_slugify()` uses hyphens, max 60 chars
- [ ] `_update_memories()` produces `{datetime}-{description}.md` filenames
- [ ] `_load_growth_rules()` defaults `max_memory_words` to 2000
- [ ] Baby template updated with new default
