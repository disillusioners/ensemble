# Rules

## Must

- Follow project's test conventions (naming, structure, location)
- Write self-contained tests (no external deps unless mocked)
- Report actual error messages, not summaries
- Suggest fixes when tests fail
- **Check `.agents/tester/README.md` before testing a project**
- **Create `.agents/tester/` directory if it doesn't exist**
- **Document testing procedures in `.agents/tester/README.md`**
- **Record lessons learned in `.agents/tester/LESSONS.md`**
- **Keep documentation concise and actionable**

## Must Not

- Skip failing tests silently
- Test implementation details over behavior
- Leave commented-out code
- Over-test trivial code (getters/setters)
- **Ignore existing `.agents/tester/` documentation**
- **Write redundant documentation — check if info already exists**
- **Store temporary or throwaway files in `.agents/tester/`** — only permanent knowledge

---

## File Organization in `.agents/tester/`

### Required Files
- **README.md** — Always maintain. Quick start for testing this project

### Optional Files (create as needed)
- **GUIDE.md** — Detailed testing guidelines
- **WORKFLOWS.md** — Step-by-step procedures
- **LESSONS.md** — Lessons learned and gotchas
- **COVERAGE.md** — Coverage tracking and goals
- **RESULTS/** — Directory for historical reports

### Naming Convention
- Use UPPERCASE.md for standard docs
- Use descriptive names for specific topics (e.g., `API_TESTING.md`)
- Date historical reports: `RESULTS/2024-01-15-login-tests.md`
