# ensemble architecture chart color code (job-task system flow)

Reusable semantic color scheme for agents-ensemble architecture flowcharts (established 2026-09-01, job-task system diagram):

- **blue** = public/job path (API entry → JobItem → queue → dequeue → dispatch → finalize)
- **purple** = internal paths that create NO JobItem (agent send_message, cascade-resume, child reports, PROCESS_REPORT wake loop)
- **green** = terminal/happy path (Task COMPLETED → JobFeedbackObserver gates → _finalize_job → instance COMPLETED/WAITING_CHILDREN)
- **orange** = lifecycle transitions (pause/resume subgraph, terminate/revive subgraph) — give these subgraphs a tinted background `fill:#fff7ed`
- **gray dashed** = backstop sweeps (Pattern-f/f1, f2 drift, WC watchdog, ReportDeliveryRecoveryService) — `stroke-dasharray:5 5`, dashed edges `-.->`

Conventions that validated cleanly with mmdc:
- Use `→` (U+2192) instead of `->` inside quoted labels; avoid `|`, `;`, `<br>` in labels (no HTML in labels per charter rule).
- Frontmatter `--- title: ... ---` (no colon in the title text) works for flowchart titles.
- Keep shared convergence nodes (e.g. "Instance LangGraph turn runs") unstyled/neutral so both blue and purple paths visibly converge on them.
- Define nodes inside their subgraph BEFORE any cross-subgraph edge referencing them (Mermaid assigns membership at first mention).
