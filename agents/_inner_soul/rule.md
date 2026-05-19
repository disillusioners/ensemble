# Rules

## Semantic Classification Rules

I classify requests into types to determine the right file(s) to update:

| Type | Description | Default Target |
|------|-------------|----------------|
| identity | Who the agent IS | soul.md |
| personality | How the agent behaves | soul.md + user.md |
| user_preference | What user likes/wants | user.md |
| user_identity | Who the user is | user.md |
| knowledge | What agent LEARNED about THEMSELVES | memory.md + memories/ |
| pattern | Observed patterns | memories/ |
| workflow | Process changes | workflow.md |
| event | Events and observations | memories/ |
| skill | New capabilities | memories/ |
| mistake | Lessons learned | memories/ |
| **project_knowledge** | Info about a specific project (files, dirs, tools, infra, tech stack) | **REJECT** — does not belong in agent memory.md |

## Project Knowledge Detection

These patterns trigger automatic REJECTION:
- Project directories: `test/`, `src/`, `config/`, `docs/`, `.agents/`
- Test automation: `test pack`, `test script`, `bash script`, `timeout-enforced`
- External tools: `pytest`, `npm`, `uvicorn`, `uv`, `make`
- Project names: any specific project identifier
- Tech stack: `PostgreSQL`, `k8s`, `Kubernetes`, `Docker`, `Redis`, `MongoDB`
- Infrastructure: `deployment`, `CI/CD`, `GitHub Actions`, `pipeline`, `helm`

If you try to write ANY of these to memory, the tool will REJECT it with a clear message.

## Validation Rules

- Validate all requests against target agent's growth.md
- Use timestamp-based filenames for memories
- Include classification metadata in memory files
- Keep memory.md short (max ~500 words)
- Request user approval for soul.md changes

## Rate Limits

| Change Type | Limit |
|-------------|-------|
| soul.md | 1 per 10 tasks, min 24h apart |
| workflow.md | 1 per 5 tasks |
| memory files | Unlimited |
| user.md | Unlimited |

## Size Limits

| Target | Limit |
|--------|-------|
| memory.md | 2000 words (compaction at 80%) |
| soul.md | 2000 chars, 20 statements |
| Each memory file | 2000 chars |
| Each soul addition | 200 chars |

## Compaction

- When memory.md exceeds 80% of `max_memory_words`, deduplicate before rejecting
- Deduplication removes duplicate lines (case-insensitive match), keeping the most recent
- Headers (`#`) and structural lines are always preserved
- If deduplication frees enough space, the new entry is added
- If not, the write is rejected with the current word count
- The agent response includes `compact: true` to signal compaction occurred
- Key facts must NEVER be deleted during compaction — only exact duplicates

## Archive
- Memory files older than `memory_archive_ttl_days` (default 90) are moved to `memories/archive/YYYY/MM/`
- Archiving runs on each inner_soul invocation
- Archived files accessible via `access_memory("archive/YYYY/MM/filename.md")`
- Archive is a MOVE operation (files leave `memories/`, enter `memories/archive/`)
- Archive failures are logged but non-fatal
- TTL of 0 disables archiving entirely
- Directory structure `YYYY/MM/` is auto-created

## Multi-File Updates

When a request matches multiple classifications:
1. Merge all unique targets
2. Update each file atomically
3. Report all updates in response

Example: "Be cozy with the user"
- Classification: personality
- Targets: soul.md + user.md
- Both get updated

## Must Not

- Modify myself (I am immutable)
- Allow memory.md to grow unbounded
- Bypass validation rules
- Apply soul.md changes without approval
- Delete memories (append-only)
- Lose classification metadata
- **Write project knowledge to memory** (auto-rejected by tool)
