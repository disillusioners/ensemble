# Knowledge

Domain expertise and tool knowledge for the tester agent.

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
