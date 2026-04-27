# Knowledge

Domain expertise and tool knowledge for the tester agent.

---

## Knowledge Tools

You have access to two knowledge tools:

### `explore(query, mode?, project_id?)` — PRIMARY knowledge retrieval
**Always try explore() FIRST** when you need project knowledge, architecture info, or answers about a codebase.
- It queries the RAG knowledge base AND browses project files if needed
- It is faster and more comprehensive than manual file browsing
- It may also update the knowledge base asynchronously if it finds stale data
- **Do NOT run explore() in parallel with bash/file exploration.** Call explore(), evaluate the result, and ONLY use other tools if explore() was insufficient.

### `experience(text, project_id?)` — Record knowledge
Use to record project experience, architectural decisions, or learned knowledge into the RAG knowledge base.
- Returns immediately, processes in background

---

## Opencode Tools

### agent-browser

Browser automation tool for web frontend projects.

**Capabilities:**
- Automates browser interactions (clicking, typing, navigating)
- Can visually test and fix website bugs
- Takes screenshots and inspects DOM elements
- Handles forms, buttons, navigation flows

**When to Use:**
- Web frontend projects/sub-projects ONLY
- Visual testing and debugging
- UI interaction automation
- Screenshot-based bug fixing

**When NOT to Use:**
- Backend or API projects
- Non-web projects
- Headless/CLI applications

**Usage in Instructions:**
```raw
"Do browser automation (use agent-browser skill) to auto fix the website bug."
```

---

## Project Type Detection

When coordinating testing work, identify project type:

| Type | Indicators | Tool Recommendation |
|------|------------|---------------------|
| Web Frontend | HTML, CSS, React, Vue, Next.js | agent-browser available |
| Backend | Node.js, Python, Go, API routes | Standard tools only |
| Full-stack | Both frontend and backend | agent-browser for frontend parts |
| CLI/Headless | No UI, command-line | Standard tools only |