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
| `skill_feedback(skill_id, applied, note)` | Tell the system if an injected skill was helpful. |

## When to Use

- **Need a procedure or pattern you think might exist?** → `skill_search` first; auto-injection only surfaces the top few.
- **Discovered a reusable approach worth saving?** → `skill_create`. Keep it short (1–2 screens), concrete, and specific to one capability.
- **Injected skill seems wrong or outdated?** → `skill_fix` with a clear description of the problem.
- **Just used an injected skill?** → `skill_feedback` to record whether it helped (`applied=True` / `False`) and why. This is the main signal that shapes the skill corpus.

## Good Skills

A good skill states one specific capability, shows concrete input/output examples, and stays short. Vague skills ("help with code") or content that re-teaches general LLM knowledge waste the corpus — trim anything an LLM could derive on its own.