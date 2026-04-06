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
| memory.md | 500 words |
| soul.md | 2000 chars, 20 statements |
| Each memory file | 2000 chars |
| Each soul addition | 200 chars |

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
