# Dynamic Skill

Skills are reusable knowledge and procedures that get injected into your context when relevant. When the system finds one, it appears as a `[System Inject]` message before the user's message — read and use it if it fits, ignore it if it doesn't. You may also see a brief **low-match** list of nearby skills; use `skill_search` or `skill_view` to explore them.

## Auto-Load Skills

Some skills are `auto_load` — the system loads them for you automatically before every task. These are foundational skills that should always be present.

Other skills are injected **on-demand** when your task context matches (via the BM25 → embedding → LLM search pipeline).

You can also search manually using `skill_search` to find skills relevant to your current task.

## Tools

| Tool | Purpose |
|---|---|
| `skill_search(query)` | Search skills by keyword — broader than auto-injection. |
| `skill_list()` | List all available skills. |
| `skill_view(skill_id)` | Read a skill's full content. |
| `skill_create(name, description, content)` | Save a new reusable skill. |
| `skill_fix(skill_id, issue_description)` | Request a fix for a broken or outdated skill. |
| `skill_feedback(skill_id, applied, usefulness, note, improvement_note)` | Record feedback on a skill's usefulness to drive skill evolution. |

### Feedback Guidance

The `skill_feedback` tool has evolved. Always provide these fields after consuming a skill:

| Field | What it means |
|-------|---------------|
| `applied` (bool) | Did you actually follow the skill's guidance? |
| `usefulness` (1-10) | How good was the skill? **1 = unusable, 10 = excellent.** Be honest — low scores are valuable, they trigger evolution. |
| `note` (str) | General context — what scenario did you run the skill in? |
| `improvement_note` (str) | **Specific, actionable** changes you'd make. e.g. *"Should mention PACKS.md lives at .agents/tester/ not test/packs/"* — not vague comments like "could be better." |

**Best practice:** Always provide `usefulness` and `improvement_note` after consuming a skill — this is the primary intelligence source for skill evolution. Low scores are GOOD — they tell the system exactly what to fix.

## When to Use

- **Need a procedure or pattern you think might exist?** → `skill_search` first; auto-injection only surfaces the top few.
- **Discovered a reusable approach worth saving?** → `skill_create`. Keep it short (1–2 screens), concrete, and specific to one capability.
- **Injected skill seems wrong or outdated?** → `skill_fix` with a clear description of the problem.
- **Just used an injected skill?** → `skill_feedback` to record whether it helped (`applied=True` / `False`) and how useful it was (`usefulness` 1-10). Your `improvement_note` tells the evolution system exactly what to fix — this directly shapes how skills improve over time.

## Good Skills

A good skill states one specific capability, shows concrete input/output examples, and stays short. Vague skills ("help with code") or content that re-teaches general LLM knowledge waste the corpus — trim anything an LLM could derive on its own.