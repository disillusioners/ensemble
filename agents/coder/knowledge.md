# Knowledge

Domain expertise and tool knowledge for the coder agent.

---

## Knowledge Tools

Use `explore(query)` to query project knowledge and `experience(text)` to record new knowledge.

- **explore(query)** — Search the RAG knowledge base for project-specific knowledge. Use this to recall past experiences, architectural decisions, and technical details about the current project.
- **experience(text)** — Record new knowledge about the current project to the RAG knowledge base. Use this when you learn something worth remembering for future sessions.

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
```
"Do browser automation (use agent-browser skill) to auto fix the website bug."
```

---

## Project Type Detection

When spawning opencode sessions, identify project type:

| Type | Indicators | Tool Recommendation |
|------|------------|---------------------|
| Web Frontend | HTML, CSS, React, Vue, Next.js | agent-browser available |
| Backend | Node.js, Python, Go, API routes | Standard tools only |
| Full-stack | Both frontend and backend | agent-browser for frontend parts |
| CLI/Headless | No UI, command-line | Standard tools only |
