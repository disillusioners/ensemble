# Dynamic Skill System

Dynamic skills are living, DB-stored capabilities that can be searched, created,
evolved, and improved based on real usage. Unlike innate skills (static markdown
files you ship with), dynamic skills are **persisted to the database**, scoped
per-project (or shared globally), and evolve over time.

## Opt-in via meta.json

An agent gets the dynamic-skill tools by declaring `dynamic-skill` in
`innate_skills`. To get **automatic injection** of relevant skills into the
conversation, the agent's `meta.json` must additionally set:

```json
{
  "skill_injection": true
}
```

Without that flag, the tools are still available but skills are not
auto-prepended before each user message.

## Automatic Injection

When `skill_injection: true` is set, the system runs a 3-stage search pipeline
(BM25 → embedding re-rank → LLM selection) before each user message and
prepends up to `max_inject_skills` (default `2`) of the most relevant skills as
a `HumanMessage`. Low-confidence candidates (above zero but below the inject
bar) are listed briefly so you know they exist.

If nothing relevant is found, the message is sent through unchanged — silent
opt-out, not an error.

## Tools

Six tools are available. `project_id` is auto-injected from your instance; you
don't pass it explicitly.

### `skill_search(query, limit=10)`

Search skills by natural-language query. Returns a JSON payload:

```json
{
  "injected": [{"skill": {...}, "score": 0.87}, ...],
  "low_match": [{"name": "...", "score": 0.12, "description": "..."}, ...]
}
```

Use this when auto-injection didn't surface what you need, or when the task is
explicitly about finding skill content.

### `skill_list(category=None, active_only=True)`

Enumerate every active skill in your project (plus global). Returns a human-
readable bullet list with short ids:

```
Skills (24 of 24 total):
- [a1b2c3d4] prompt-style-concise — workflow — active
- [e5f6a7b8] api-error-handling — workflow — active
```

Use this to discover what's available before guessing names with `skill_view`.

### `skill_view(skill_id)`

Read one skill's full body, metadata, and lineage (parents / children). Returns
a Markdown doc; the body is truncated at 8000 chars. Pass the **id**, not the
name — get ids from `skill_list` or from `skill_search` results.

### `skill_create(name, description, content, category="workflow")`

Create a new skill manually. Body is plain markdown. Use this when you want
to encode a reusable pattern you've discovered; the evolution engine will then
score it on real usage.

A good skill:

- **States a specific capability** ("Author a focused PR description from a
  diff" beats "Help with code").
- **Shows concrete examples** — input pairs / expected output beats abstract
  prose.
- **Stays short.** Aim for 1–2 screens. Trim anything an LLM could derive
  from its general training.

### `skill_fix(skill_id, issue_description, suggested_fix=None)`

Record a **request** for evolution — never performs the fix inline. The
skill-keeper agent picks it up at its next pass and decides whether to FIX
(tweak + A/B), DERIVE (specialize), or skip.

Pass `suggested_fix` when you have a concrete proposal; leave it `None` to let
the keeper derive one from `issue_description`.

### `skill_feedback(skill_id, applied=None, note="")`

Report whether a skill helped. This is the **primary signal** driving
evolution:

| Value | Meaning |
|---|---|
| `applied=True` | Skill was directly useful — bumps metrics toward reinforcement |
| `applied=False` | Skill was misleading or irrelevant — counts toward fallback |
| `applied=None` (omit) | Unsure, just leaving a note |

The feedback is stamped onto the latest `SkillUsageRecord` for
`(skill_id, instance_id)`. Repeated `False` feedback triggers automatic FIX
evolution; consistent `True` keeps a skill active and dominant.

## A/B Testing

When the keeper FIX-evolves a skill, it creates a tweaked variant and starts an
A/B test against the original. Both versions run in parallel for
`ab_sample_size` comparisons (default `10`). The version with the better
completion rate wins once the minimum difference threshold is met; the loser
is deactivated. You may see two skills with the same name and different
short-ids during a live test — that's expected.

You don't trigger A/B tests yourself; `skill_feedback` and successful task
completions drive them.

## Best Practices

- **Trust injection for the obvious case.** If 1–2 skills auto-inject and one
  obviously matches, apply it.
- **Use `skill_search` when the task is ambiguous.** Auto-injection has a tight
  top-k cap; `skill_search` is broader.
- **Always call `skill_feedback` after consuming an injected skill.** Even a
  one-word note ("worked" / "wrong trigger") compounds into skill quality.
- **Prefer `skill_view` over re-deriving content.** Skills encode hard-won
  patterns; trust the corpus.
- **When authoring a new skill, prefer `skill_create` over `skill_fix` on an
  unrelated existing skill.** Fix is for *improvements to* a skill; create is
  for *new* skills.
- **Don't spam `skill_fix`.** Each request costs an evolution pass; group
  related issues into one call with a clear `issue_description`.

## When Skills Aren't Loaded Yet

If the dynamic-skill services are mid-wiring, tools soft-fail with a
deterministic string (e.g. `"Skill search service not yet available..."`)
rather than raising. Treat that as "not yet actionable" — fall back to your
own knowledge and move on.
