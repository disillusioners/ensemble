---
version: 1.0.0
category: workflow
auto_load: true
---

# KB Curator

Three responsibilities — KB INDEX lookup (a), filesystem read (b), first-turn RAG mirror (c). Skills are static text; the loader cannot run code. The LLM executes the duties; guarantees live in `workflow.md` or a pin test.

## (a) Index responsibility

KB INDEX (one-line-per-doc trigger + verification discipline) lives in my Memory section (loader always includes it). Match the request to a doc by its trigger line, read that doc. Do NOT read all six KB docs every turn — ~3k tokens of idle overhead (architect §6.2).

## (b) Content path

When INDEX is ambiguous or I need the full runbook, `read_file` the specific doc. Quote the file path and verified anchor; do NOT paraphrase the anchor away.

## (c) First-turn RAG mirror

On first turn of a fresh session, override `no_force_explore` and call `experience(text=<each KB doc>)` for the six KB docs. Best-effort: if RAG is unavailable or `knowledge` is stripped, mirror is a no-op; Tier 1 + Tier 2 still guarantee-on. Directive is pinned in `workflow.md`.

## What this skill is NOT

- NOT a startup recorder (no startup hook).
- NOT a full-KB loader (architect §6.2 — 3k idle overhead).
- NOT a loader bypass — INDEX lives in my Memory section.

## Verification discipline

Each KB doc carries `last-verified-against: v0.12.4`. Runtime divergence is warn-only. Re-verify an anchor before quoting it if the investigation depends on the exact line; working-tree `git diff` is ground truth.

## When to apply

- Session start → (c) first-turn RAG mirror.
- Any maintenance task → (a) INDEX lookup first.
- Verification / full content → (b) `read_file`.

## Mandatory output format

```
KB: §<doc-id> <trigger-line verbatim>
     verified-against: <release-tag>  # only if I quoted an anchor
     content-path: read_file          # (b) only
     rag-mirror: yes/no              # (c) only on first turn
```
