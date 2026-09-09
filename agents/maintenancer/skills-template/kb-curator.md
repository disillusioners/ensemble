---
version: 1.0.0
category: workflow
auto_load: true
---

# KB Curator

Three responsibilities. I do not have a startup hook — skills are
static text delivered via the prompt loader; the loader cannot run
code at load time. The LLM (me) is what executes the duties. If I
want a guarantee, it lives in `workflow.md` or in a pin test.

## (a) Index responsibility — read the KB INDEX first

The KB INDEX (one-line-per-doc trigger + verification discipline)
lives in Maintenancer's Memory section (load-bearing slot; the loader
always includes that section). When a maintenance task lands, I read
the INDEX first and match the user's request to a doc by its trigger
line. I do NOT read all six KB docs on every turn — that costs ~3k
tokens of idle prompt overhead (rejected by architect §6.2).

**Example:**

> User asks "how do we recover from an orphan ACTIVE job".
>
> I read the KB INDEX in Maintenancer's Memory section → see the line:
> `orphan ACTIVE JobItem → §05 R6 (sweep, ≤15min default)`.
>
> I read `agents/maintenancer/knowledge/05-repair-runbooks.md` only.

## (b) Content path — `read_file` retrieval

When the INDEX is ambiguous or I need the full runbook (verification
commands, exact SQL, exact code anchors), I call `read_file` against
the specific doc. I quote the file path and the verified anchor; I do
NOT paraphrase the anchor away.

**Example:**

> "The recipe is in `daemon/manager.py:4996-5012` — let me read the
> exact `DO $$` block before paraphrasing."

## (c) First-turn RAG mirror duty — `experience()`

On my first turn of a fresh session, I override `no_force_explore`
and call `experience(text=<each KB doc>)` for the six KB docs. This
mirrors the KB into RAG for cross-session recall. The duty is best-
effort: if RAG is unavailable or the `knowledge` category is stripped,
the mirror is a no-op and Tier 1 + Tier 2 still guarantee-on.

The first-turn directive is pinned in `workflow.md` (the workflow is
where execution rules live; the skill can only instruct).

**Example:**

> First turn of the session:
> ```
> experience(text=<contents of 01-architecture-overview.md>)
> experience(text=<contents of 02-jobs-missions-admission-state.md>)
> experience(text=<contents of 03-log-forensics.md>)
> experience(text=<contents of 04-known-traps.md>)
> experience(text=<contents of 05-repair-runbooks.md>)
> experience(text=<contents of 06-restart-upgrade-runbook.md>)
> ```

## What this skill is NOT

- I am NOT a startup recorder. I do not claim to "auto-record the KB
  on startup" — there is no startup execution hook.
- I do NOT auto-include all six KB docs on every turn (rejected by
  architect §6.2 — 3k tokens of idle overhead).
- I do NOT bypass the loader. The KB INDEX lives in Maintenancer's
  Memory section, which the loader always includes.

## Verification discipline

Every KB doc carries a `last-verified-against: v0.12.4` header.
Runtime divergence is warn-only (refuse-to-cite is unenforceable
against an LLM). I re-verify an anchor before quoting it if the
investigation depends on the exact line; the working-tree `git diff`
is ground truth.

## When to Apply

- At session start: tier (c) — first-turn RAG mirror.
- On any maintenance task: tier (a) — INDEX lookup first.
- When a doc is needed for verification or full content: tier (b) —
  `read_file` retrieval.

## Mandatory Output Format

When I report which KB docs I consulted:

```
KB: §<doc-id> <trigger-line verbatim>
     verified-against: <release-tag>  # only if I quoted an anchor
     content-path: read_file  # tier (b) only
     rag-mirror: yes/no      # tier (c) only on first turn
```
