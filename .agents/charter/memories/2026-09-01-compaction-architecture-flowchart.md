# Compaction Architecture Flowchart (agents-ensemble, post /compact)

Reusable pattern notes:
- "N entry paths → one engine → shared write semantics" layout: put callers in a `direction LR` subgraph, converge all on a single engine node inside its own subgraph, then fan into a highlighted decision subgraph.
- Highlight the "answer" subgraph via `style <subgraphId> fill:#fff8e1,stroke:#b8860b,stroke-width:2px`; mark rejected alternatives with a dashed red `classDef` (`stroke-dasharray: 5 5`) so they read as annotations, not flow.
- mmdc accepts `<` inside quoted edge labels (e.g. `|"compacted_at < 60s"|`) — no escaping needed when quoted.
- Keep all labels single-line plain text (no `<br/>`, no HTML) — use commas/`+`/`to` instead of arrows.
