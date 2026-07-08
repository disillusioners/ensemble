# OpenSpace-Skill

OpenSpace is a self-evolving skill engine and marketplace. The openspace-skill exposes 4 MCP tools for skill discovery, autonomous task delegation, skill repair/refinement, and skill publishing. Use it to find existing solutions before writing complex logic from scratch, delegate substantial multi-step work to OpenSpace's own LLM agent, repair skills that produced bad results, or publish a reusable skill to the OpenSpace community.

## Prerequisites

**OpenSpace installed**: The `openspace-ai` package must be installed (`pip install openspace-ai`). If not installed, the tools will return installation errors — that is expected and not a bug in ensemble.

**Environment variables**:

| Variable | Required? | Purpose |
|----------|-----------|---------|
| `OPENSPACE_LLM_API_KEY` | Yes | OpenSpace's own LLM credential (used internally by `execute_task`) |
| `OPENSPACE_API_KEY` | Optional | Cloud community access — required only for `upload_skill` to publish skills to the OpenSpace skill repository |

If `OPENSPACE_LLM_API_KEY` is missing, `execute_task` will return credential errors. `search_skills`, `fix_skill`, and `upload_skill` may also depend on it depending on backend mode.

## Tool Inventory

| Tool | Blocking? | Purpose |
|------|-----------|---------|
| `mcp_openspace_search_skills` | Yes | Search for existing skills (BM25 → embedding → lexical fallback) |
| `mcp_openspace_execute_task` | Yes | Delegate an entire task to OpenSpace's own LLM agent |
| `mcp_openspace_fix_skill` | Yes | Repair or refine an existing skill based on feedback or error analysis |
| `mcp_openspace_upload_skill` | Yes | Upload a skill to the OpenSpace skill repository/community |

All four tools are blocking calls. Treat them as round-trips — do not assume they return instantly.

## Tool Reference

### `mcp_openspace_search_skills`

Search the OpenSpace skill marketplace for existing solutions. Best-effort retrieval pipeline: BM25 → embedding → lexical fallback.

- **When to use:** Before writing complex logic from scratch. Someone may have already solved your problem.
- **Cost:** Low. Fast. Use freely to discover reusable skills.
- **Returns:** List of matching skills with names, descriptions, and metadata.

```python
# Discover skills for a specific problem
mcp_openspace_search_skills(query="pdf extraction with ocr")

# Discover skills for a workflow
mcp_openspace_search_skills(query="convert csv to parquet with schema validation")
```

**Default behavior:** Search first. If a skill matches, prefer to reuse it (or adapt its pattern) rather than writing new logic.

### `mcp_openspace_execute_task`

Delegate a complete task to OpenSpace's own LLM agent. The agent works autonomously — it can use file processing, API integration, data transformation, and other tools internally.

- **When to use:** Complex, multi-step tasks that benefit from autonomous execution.
- **Timeout:** Up to 900 seconds (15 minutes). Long-running by design.

```python
mcp_openspace_execute_task(
    task="Extract all email addresses from PDF files in /data/ and save to /out/emails.csv"
)
```

> **⚠️ COST WARNING — DOUBLE TOKEN BILL**
>
> `execute_task` spins up **its own LLM agent** internally. You pay for **BOTH** your tokens AND OpenSpace's tokens. This is **double cost** compared to doing the work yourself.
>
> **Only use for substantial tasks.** Never use for:
> - Quick lookups
> - Simple file reads
> - One-line transformations
> - Anything you can do in your own tools faster and cheaper
>
> If the task is small enough to fit in a single tool call, **do not delegate it**.

### `mcp_openspace_fix_skill`

Repair or refine an existing skill based on feedback or error analysis. Use this when a skill that produced bad results needs explicit correction — you provide the specifics, OpenSpace applies the fix.

- **When to use:** A skill from OpenSpace had errors, returned wrong output, or didn't match your expectations. Also for refining an existing skill based on a known improvement.
- **Purpose:** Provide explicit feedback so OpenSpace can correct or refine the skill.

```python
mcp_openspace_fix_skill(
    skill_name="pdf_extraction",
    feedback="OCR step failed on scanned pages; needs preprocessing with deskew + binarization"
)
```

`fix_skill` is the canonical mechanism for skill repair — use it when auto-evolution got the skill wrong and you know what the fix should be.

### `mcp_openspace_upload_skill`

Upload a skill to the OpenSpace skill repository/community. Promote a reusable skill pattern into the shared library so other agents and OpenSpace instances can discover and reuse it.

- **When to use:** You have built a skill that solves a problem likely to recur — uploading makes it discoverable via `search_skills` for the wider OpenSpace community.
- **Requirement:** `OPENSPACE_API_KEY` must be set (cloud publishing requires authentication).

```python
mcp_openspace_upload_skill(
    skill_name="pdf_extraction_v2",
    skill_path="/path/to/skill/",
    description="OCR-based PDF text extraction with deskew + binarization preprocessing"
)
```

## Decision Guide: Search vs Delegate vs Repair vs Publish vs Do-It-Yourself

OpenSpace's 4 tools have very different cost profiles. Pick the right one:

| Situation | Action | Why |
|-----------|--------|-----|
| You're about to write complex logic (parser, pipeline, integration) | `search_skills` first | Cheap. May find an existing solution. |
| Task is multi-step, autonomous-execution friendly, and substantial | `execute_task` | High cost, but worth it for hard work. |
| A skill you ran produced bad output and you know what's wrong | `fix_skill` | Manual correction beats auto-evolution when you have specifics. |
| You have a reusable skill worth sharing with the community | `upload_skill` | Requires `OPENSPACE_API_KEY`; promotes local skill into the shared library. |
| Task is a quick lookup, simple file read, or one-line transform | **Do it yourself** | Delegation is overkill — double token cost for trivial work. |
| Task fits in a single tool call you already have | **Do it yourself** | No reason to spin up another agent. |

**Key principle:** Search is cheap, delegation is expensive (double token cost). Default to **search-then-execute-locally**. Only delegate when the task is complex enough to justify the cost.

**Workflow:**

1. **Search first** — `mcp_openspace_search_skills(query="...")` before writing complex logic.
2. **Reuse or adapt** — If a skill matches, use it or adapt its pattern.
3. **Do it yourself** — For simple tasks, use your own tools.
4. **Delegate** — Only for substantial multi-step work where OpenSpace's agent can work autonomously.
5. **Fix** — If a delegated skill went wrong, use `fix_skill` with specific feedback to refine it.
6. **Publish** — If you've built a reusable skill, consider `upload_skill` (requires `OPENSPACE_API_KEY`).

## Error Handling

**"OpenSpace not installed" / `ModuleNotFoundError`**: The `openspace-ai` package is missing. Run `pip install openspace-ai` in the ensemble environment.

**"Missing OPENSPACE_LLM_API_KEY"**: OpenSpace's internal LLM credential is not set. Set `OPENSPACE_LLM_API_KEY=<your-key>` in the environment.

**"Missing OPENSPACE_API_KEY"** (on `upload_skill`): Cloud publishing requires the cloud key. Set `OPENSPACE_API_KEY` or skip publishing the skill.

**`execute_task` times out (>900s)**: The task is too large for a single delegation. Break it into smaller pieces and call `execute_task` for each.

**`search_skills` returns no results**: The marketplace doesn't have a matching skill yet. Either write it yourself, or use `upload_skill` to publish your version once you build it.

**`fix_skill` doesn't improve output**: Provide more specific feedback — point to the exact step, error message, or expected vs. actual behavior. Vague feedback like "doesn't work" won't help.

**`upload_skill` returns 401/403**: `OPENSPACE_API_KEY` is missing or invalid. Verify the cloud key in `.env`.

## Agent Configuration Note

This skill provides **documentation and prompt context**, but the 4 OpenSpace MCP tools require **explicit tool access** to be granted in the agent's `meta.json`:

```json
{
  "tools": {
    "allow": [
      "mcp_openspace_search_skills",
      "mcp_openspace_execute_task",
      "mcp_openspace_fix_skill",
      "mcp_openspace_upload_skill"
    ]
  }
}
```

For full setup details (OpenSpace server registration, transport config, credential injection), see `docs/features/openspace-skill-engine.md`.

## Related

- **OpenSpace project** — the self-evolving skill engine behind these tools
- **Other innate skills** — `opencode/` (code delegation), `chart/` (diagram generation), `job-orchestration/`, `coordination/`
