---
version: 1.0.0
category: design
auto_load: false
---

# opendesign.list_systems

> **Canonical home.** The versioned source of this skill is the plugin skill
> `opendesign.list_systems` in the opendesign plugin's skill surface
> (vendored, fail-closed consumer gate). This template is the bank-side
> pointer; the plugin skill is authoritative.

Enumerate the design systems the opendesign plugin carries.

---

## The answer

**154 top-level entries** under the vendored design-systems tree at the
pinned upstream tag: 152 brand directories (e.g. `airbnb/`, `apple/`,
`agentic/`) + `_schema/` (the schema description for the design-system
data class) + `README.md` (the upstream class doc).

## How to enumerate

The count is a deterministic read of the tree the plugin skill's
`systems` reference points at: list the immediate children of that path;
the count is the number of entries (dirs + the README + `_schema/`).
No database lookup, no LLM-driven counting.

## Use during brief digestion

When a dispatched brief names a brand or design system, consult the
matching directory for palette, typography, and component signals and
fold the findings into the brief text as descriptions. Cite the system
by name in the report; never paste vendored file content into dispatches.
