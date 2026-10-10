# Pass-6 fixture A — T5-mandated post-edit rule.md shape (agent-facing lines only)

Fixture encodes EXACTLY the lines T5 (phase1-plan.md) mandates into `agents/designer/rule.md`:
- `:15` — Cardinal #7 enum line (byte-identical preservation; the five tokens)
- `:35` — the re-keyed GUIDELINE (c) binding bullet (T5's verbatim binding text, WITH the
  Pass-6-added literal token `same-code-class`)
- `:38` — the re-keyed fallback-audit bullet (sketcher lane)

Nothing else from rule.md is material to the AC4/AC5 gate arithmetic, so it is omitted.
Lines are prefixed `NNN:` only for readability here; the greps below run on a de-numbered
copy built by pass6-run-fixtures.sh so the greps see the real post-edit file shape.
