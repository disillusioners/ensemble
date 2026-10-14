# Mermaid: true side-by-side contrast panels (NOW vs AFTER pattern)

Learned 2026-09-01 while building the "Fix A — work_id linkage NOW vs AFTER" flowchart.

## The recipe that works

Top-level `flowchart LR` + each panel as a `subgraph` with internal `direction TB`
+ **subgraph-level** cross-panel edges only:

```text
flowchart LR
    subgraph P1["panel one"]  direction TB  ...internal chain...  end
    subgraph P2["panel two"]  direction TB  ...internal chain...  end
    P1 -.-> BRIDGE[annotation] -.-> P2      %% subgraph IDs, NOT internal nodes
```

Result: two vertical top-down columns side by side, bridge node centered between
them, correct left-to-right reading order. Verified via absolute node coords
parsed from the rendered SVG.

## The trap (cost me one validation round)

A **node-level** edge from inside a subgraph to the outside (e.g. `A8 -.-> MID`)
makes Mermaid ignore that subgraph's `direction TB` — it inherits the parent
direction. Under an LR parent the internal chain sprawls horizontally
(8-node chain spread ~2500px wide). Subgraph-level edges (`P1 -.-> MID`) do NOT
trigger this; internal TB is preserved for both panels.

## Other notes

- SVG coordinate debugging: node `transform="translate(x,y)"` is RELATIVE to the
  enclosing cluster `<g>` for subgraph members — walk the XML accumulating
  parent transforms, or panel positions look overlapping/wrong.
- Frontmatter `---\ntitle: "..."---` renders on flowcharts (mermaid v11 / mmdc);
  quote YAML values containing colons.
- macOS/BSD `mktemp` requires the template to END in the X's —
  `mktemp /tmp/x_XXXXXX.mmd` returns the literal string. Use
  `TMP=$(mktemp /tmp/x_XXXXXX)` then append extensions yourself.
