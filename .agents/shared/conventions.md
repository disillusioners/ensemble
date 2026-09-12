# Conventions

Repo-wide carrier conventions. Add a bullet here only when the default applies
broadly enough to be silent for callers that don't re-read the skill at
decision time.

- **`generate_chart` defaults to per-caller charter reuse** (as of the
  `feature/generate-chart-charter-reuse` merge). Successive awaited
  `generate_chart()` calls from the same caller continue the same charter
  specialist so iterative refinement carries prior context. Carriers that
  rely on per-call independence (isolated histories, parallel charts) must
  pass `fresh=True` explicitly. Concrete case: **doc-writer** enriches
  multiple sections of one document with `generate_chart` diagrams
  (`agents/doc-writer/rule.md:7`, `soul.md:6`, `soul.md:22`, `soul.md:63`,
  `workflow.md:20`) — its awaited successive calls share one charter
  sequentially; if it ever needs isolated chart histories per section,
  that is the pass-`fresh=True` case. Charter escalation surfaces:
  `Error: Charter busy; pass fresh=True for parallel charts.` and
  `Error: Charter is paused; resume it or pass fresh=True for a new charter.`
  See `agents/_prompt_system/innate-skills/chart/skill.md` for the full
  contract.
