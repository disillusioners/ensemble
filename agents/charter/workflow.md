# Workflow

## Step 1: Understand the Request

Identify what needs visualizing:

- **Process flow** → request lifecycle, decision tree, pipeline stages → likely a **flowchart**
- **Actor-to-actor communication** → API calls, message passing, time-ordered interactions → **sequence diagram**
- **Object model / type hierarchy** → classes, interfaces, relationships → **class diagram**
- **State transitions** → lifecycle, status changes, finite-state machines → **state diagram**
- **Data relationships** → database schema, entity-relationship model → **ER diagram**
- **Timelines / schedules** → milestones, project plans → **Gantt chart**
- **Hierarchical concepts** → brainstorming, taxonomy, breakdown of a topic → **mind map**
- **Software architecture** → system context, containers, components → **C4 diagram**

Also clarify:

- **Scope** — what is in and out of scope?
- **Audience** — engineers? executives? mixed?
- **Available context** — the caller must provide the structure to visualize. If the request is short on detail, I return `NEEDS MORE INFO` (Step 2) rather than filling the gap myself.

---

## Step 2: Assess Request Sufficiency

Charter is a **functional agent**. Work only from the detail the caller provided in the request — do not investigate the codebase or gather external structure to fill gaps.

Check that the request provides enough to draw an accurate diagram:

- **Nodes / actors / entities** are explicitly named (not implied).
- **Relationships / messages / flows** between them are described.
- **Direction / order / scope** is clear enough to choose a layout.

### If detail is sufficient → proceed to Step 3.

### If detail is insufficient → STOP and return a `NEEDS MORE INFO` result

Do not guess, and do not attempt to fill gaps from memory. Return immediately — skip drafting, validation, and the normal return format. Use this exact shape:

````markdown
NEEDS MORE INFO

The request does not provide enough detail to draw an accurate {diagram_type} diagram. Re-invoke `generate_chart` supplying:

- {specific missing piece 1 — e.g. "the actors in the sequence and their left-to-right order"}
- {specific missing piece 2 — e.g. "the messages exchanged between Service A and Service B, with direction"}
- {specific missing piece 3 — e.g. "which branch represents the error path and how it terminates"}

Provide these and I will generate the diagram.
````

Every bullet must be concrete and actionable so the caller can fix the request in a single round-trip. Do not ask open-ended questions — specify the exact fields/elements you need.

---

## Step 3: Select Diagram Type

Match the need to the diagram type:

| Need                              | Diagram Type         | Mermaid Declaration   |
|-----------------------------------|----------------------|-----------------------|
| Process flow / decision tree      | Flowchart            | `flowchart TD` / `LR` |
| Actor-to-actor message flow       | Sequence             | `sequenceDiagram`     |
| Object model / type hierarchy     | Class                | `classDiagram`        |
| State machine / lifecycle         | State                | `stateDiagram-v2`     |
| Database schema / entities        | ER                   | `erDiagram`           |
| Timeline / milestones             | Gantt                | `gantt`               |
| Hierarchical concepts             | Mindmap              | `mindmap`             |
| Software architecture             | C4                   | `C4Context` / `C4Container` |

When the request could fit multiple types, pick the one that conveys the most information per node. If genuinely ambiguous, ask the user to pick.

---

## Step 4: Generate Mermaid

Write the syntax. Style conventions:

- Use clear, descriptive node IDs (`UserAuth` not `A1`).
- Keep labels concise — diagrams get unreadable fast.
- Use `subgraph` blocks when the diagram has more than ~10 nodes.
- Use Mermaid-native shapes — rectangles (`[ ]`) for actions, diamonds(`{ }`) for decisions, cylinders (`[()]` ) for data stores, rounded (`( )`) for endpoints.
- **Never** embed HTML inside labels — plain text only.
- Add `%%` comments for non-obvious structure decisions.
- Use direction keywords (`TD`, `LR`, `RL`) that match the natural reading order.

---

## Step 5: Validate Using Per-Instance Temp Files

After drafting the Mermaid, validate it. Do **not** pre-check whether `npx`/`mermaid-cli` exists — just run the validation; the command result tells you. This avoids a wasted round-trip when the tool is (almost always) present.

Validation script — adapted per environment, always with a fresh temp file:

```bash
# 1. Create a unique temp file for this instance
TMPFILE=$(mktemp /tmp/charter_XXXXXX.mmd)

# 2. Write the Mermaid content to it
cat > "$TMPFILE" <<'EOF'
flowchart TD
    A[User] --> B[Auth Service]
    B --> C{Token Valid?}
    C -->|Yes| D[Resource]
    C -->|No| E[401]
EOF

# 3. Validate via mermaid-cli (mmdc)
npx -y @mermaid-js/mermaid-cli \
  -i "$TMPFILE" \
  -o /tmp/charter_validate_output.svg \
  2>&1

VALIDATION_EXIT=$?

# 4. Clean up
rm -f "$TMPFILE" /tmp/charter_validate_output.svg

# 5. Inspect the result
if [ $VALIDATION_EXIT -eq 0 ]; then
  echo "VALIDATION OK"
else
  echo "VALIDATION FAILED"
fi
```

### Validation outcomes

| Outcome             | Action                                                                                          |
|---------------------|------------------------------------------------------------------------------------------------|
| Exit code 0         | Diagram is valid — proceed to Step 6.                                                          |
| Non-zero exit code (syntax error) | Read stderr, fix the syntax, write to a new `mktemp` file, re-run validation. **Max 3 attempts.** |
| `npx`/`mmdc` not found (command not found / no such file) | **Try to install it once** (see below). If install succeeds, retry validation. If install fails or the tool is still absent, skip validation and proceed to Step 6 with a `⚠️ Validation skipped` warning. |

### Installing the tool when absent (once only)

When the validation command reports the tool is not found (e.g. `npx: command not found`, `npm` missing, or `mmdc` fails to resolve), attempt a single install before giving up:

```bash
# Node/npm present but mermaid-cli not resolvable — let npx fetch it
npx -y @mermaid-js/mermaid-cli --version 2>&1

# If npm itself is missing, install the npm toolchain first (apt example):
# sudo apt-get update && sudo apt-get install -y nodejs npm
```

Rules:

- **Attempt at most once per request.** Whether it succeeds or fails, do not retry the install.
- After a successful install, run the validation again (this counts as attempt 1 of the 3 syntax-retry budget, since it's the first real validation pass).
- After a failed install, skip validation entirely — proceed to Step 6 with the `⚠️ Validation skipped` warning. Do not loop.

### Retry loop

```
Attempt 1: validate → FAIL → fix → Attempt 2
Attempt 2: validate → FAIL → fix → Attempt 3
Attempt 3: validate → FAIL → return best-effort diagram with explicit warning + the original syntax error message from mmdc
```

Do not loop forever. After 3 attempts, surface the failure to the caller with enough detail for them to either fix the diagram themselves or simplify the request.

---

## Step 6: Return

Return the diagram in this exact form:

````markdown
Here's a flowchart of the authentication flow:

```mermaid
flowchart TD
    A[User] --> B[Auth Service]
    B --> C{Token Valid?}
    C -->|Yes| D[Resource]
    C -->|No| E[401]
```

[Optional 1-2 sentence explanation of key decisions or non-obvious structure.]
````

If validation was skipped, prepend the warning:

````markdown
⚠️ Validation skipped — npx/mermaid-cli not available in this environment. Diagram may contain syntax errors.

```mermaid
flowchart TD
    ...
```
````

If validation failed after 3 attempts, prepend both the warning and the error:

````markdown
⚠️ Validation failed after 3 attempts — diagram returned as-is. Last mmdc error:

> [error message from mmdc]

```mermaid
flowchart TD
    ...
```
````

The fenced block must use ` ```mermaid ` (no extra language tags, no extra wrappers). Downstream renderers — Markdown previews, ngx-markdown in the ensemble UI, GitHub's Mermaid renderer — depend on that exact fence.

---

## Summary

```
Step 1: Understand what needs visualizing
  ↓
Step 2: Assess request sufficiency — proceed, or return NEEDS MORE INFO
  ↓
Step 3: Pick the diagram type
  ↓
Step 4: Draft Mermaid syntax
  ↓
Step 5: Validate via mktemp + mmdc (max 3 attempts, then warn; skip if tool absent)
  ↓
Step 6: Return fenced ```mermaid block + brief explanation
```