---
version: 1.0.1
category: execution
auto_load: false
include: [test-pack]
---

# End-to-End Testing

End-to-end (E2E) tests validate complete user journeys through the real system — UI, backend, database, external dependencies. They answer: "Does the user actually accomplish their goal?" Unlike integration tests (one seam) or unit tests (one function), E2E tests cross every layer of the stack.

## When to Apply

Use this skill when validating:

- **Critical user journeys** — signup → first action → paid conversion
- **Business-critical flows** — checkout, onboarding, password reset, KYC
- **Multi-system workflows** — actions that span frontend, backend, third parties
- **Regression detection on UI flows** — visual or interaction changes that broke behavior
- **Release gates** — last validation before deploy

Do NOT use this skill for:

- Backend logic or API contracts alone → use integration-test skill
- Function-level behavior → use unit-test skill
- Service isolation with mocks → use mock-test skill
- Every possible user path → too expensive; pick journeys that matter

E2E is the **most expensive test layer**. Run sparingly, on critical paths only.

## When E2E Is Appropriate

E2E tests earn their keep when:

1. **The journey crosses multiple systems** — UI → API → DB → payment processor
2. **A bug would be catastrophic** — broken signup blocks all new users; broken checkout loses revenue
3. **No cheaper test can cover it** — the bug is in the wiring, not any one component
4. **The flow has changed** — UI redesign, new feature rollout, major refactor
5. **The release is gated on it** — production deploy requires E2E green

E2E is **not** appropriate for:

- Edge cases in business logic (use unit/integration tests)
- Visual regression of every page (sample key pages instead)
- Every possible input combination (test the contract, not the cartesian product)
- Performance / load testing (use dedicated perf tools)
- New feature development on every commit (run on PR, not every push)

## Design E2E Test Scenarios

### Pick the Right Journeys

Choose journeys by:

- **Revenue impact** — checkout, subscription, payment
- **Frequency** — login, search, primary action (most users hit this daily)
- **Failure cost** — what happens if this breaks? (data loss? lockout?)
- **Risk of regression** — does this code path change often?

A typical product needs 5–15 E2E journeys. More than that and the suite becomes a maintenance burden.

### Scenario Structure

Each E2E test should describe one user story with concrete steps:

```
Scenario: New user signs up and reaches the dashboard
Given: a fresh browser context, no cookies
When:
  1. Visit /signup
  2. Fill email = "[email protected]"
  3. Fill password = "TestPass123!"
  4. Click "Create Account"
  5. Verify redirected to /onboarding
  6. Fill profile name
  7. Click "Continue"
  8. Verify redirected to /dashboard
  9. Verify dashboard shows user name
Then: account exists in DB, email verification sent (mocked)
```

Keep scenarios **stable** — UI text changes constantly; use stable selectors (`data-testid`) over text or XPath.

## Browser Automation with agent-browser

For web frontends, use the **agent-browser** skill to drive E2E flows.

### When to Use agent-browser

- Web UI must be exercised through a real browser
- Form submission, navigation, click-through flows
- Visual / interaction verification beyond DOM assertions
- Sessions require login state, cookies, or localStorage

### agent-browser Capabilities

- Navigate, fill forms, click buttons, take screenshots
- Extract DOM content, validate page structure
- Handle iframes, shadow DOM, modals, drag-and-drop
- Run scripts and assert on results
- Save artifacts (screenshots, traces) for debugging

### Integration with Test Packs

Each E2E scenario runs as a **pack**:

- **Naming**: `[module]_e2e_test.sh` or `tests/packs/e2e/[journey_name]/`
- **Timeout**: 5 minutes per pack (E2E hard cap)
- **Structure**: pack script invokes agent-browser commands; captures screenshots on failure
- **Output**: PASS / FAIL / TIMEOUT with screenshot path on failure

```
Example pack: tests/packs/auth_e2e_test.sh
- Login flow → expected dashboard
- Logout flow → expected login page
- Password reset flow → expected reset email sent
```

### Selector Strategy

Prefer selectors in this order:

1. **`data-testid="..."`** — explicit test contract; survives style/label changes
2. **`role="..."` + accessible name** — semantic; aligns with screen readers
3. **`label` text** — for form fields with proper labels
4. **`id` attribute** — if stable and semantic
5. **CSS / XPath** — last resort; brittle to layout changes

Document the selector strategy in `MOCK_TESTS.md` (or an equivalent E2E spec file) so future authors know which to prefer.

## Environment Setup

### Production-Like Environment

E2E needs an environment that matches production as closely as practical:

- **Real backend services** — not mocks (use mock-test skill if you need mocks)
- **Real database** — fresh seed data; never run against production DB
- **Real third-party integrations** — in sandbox/test mode (Stripe test keys, etc.)
- **Stable browser binaries** — pinned Chromium/Firefox versions
- **Network stability** — controlled latency, no flaky proxy

### Isolation

Each E2E run starts from a known clean state:

- Fresh database (truncate + seed before run, OR ephemeral DB per run)
- Fresh browser context (no cookies, no localStorage from previous runs)
- Reset external service stubs (e.g., Stripe test mode reset between runs)
- Document reset procedure in pack script comments

### Local vs CI

| Aspect | Local | CI |
|--------|-------|-----|
| Browser | Headed (visible) for debugging | Headless |
| Database | Local Postgres / SQLite | Ephemeral container |
| Third-party | Sandbox keys | Sandbox keys |
| Artifacts | Optional screenshots | Always: trace + screenshot on fail |

## Teardown and Cleanup

E2E tests must clean up after themselves, otherwise the suite becomes flaky and slow:

- **Browser** — close all contexts/pages; ensure no zombie browser processes
- **Database** — truncate seeded data, or drop the test DB
- **Ports** — free any ports opened (10000-19999 for test services)
- **Temp files** — delete screenshots, traces, downloads
- **External services** — reset Stripe test mode, delete test users from auth provider
- **Background processes** — kill any spawned services (use a process group + trap)

Pack scripts must register cleanup with `trap` (bash) or `try/finally` (Python) so cleanup runs even on failure.

## When E2E Fails

E2E failures are notoriously noisy. Triage before declaring a real bug:

1. **Capture the failure** — screenshot, DOM snapshot, console logs, network logs
2. **Check environment** — was the dev server running? Database fresh? Stale cookies?
3. **Retry once** — if it's a transient env issue, retry; if it fails twice, investigate
4. **Check flakiness** — run 3×; if mixed pass/fail → flaky-test-management skill
5. **Diagnose** — is it a real bug? Or a stale selector? Or env drift?
6. **Document** — fix the root cause; don't paper over with retries

## E2E Pack Lifecycle

### Authoring

1. Pick the journey (revenue/frequency/cost-driven)
2. Write the scenario in stable language (Given/When/Then or step-by-step)
3. Identify stable selectors (`data-testid` preferred)
4. Implement as a pack script that calls agent-browser
5. Register in `PACKS.md` under E2E packs
6. Document in `MOCK_TESTS.md` or dedicated `E2E_SCENARIOS.md` with preconditions

### Maintenance

- **Quarterly review** — drop journeys that no longer matter; update changed flows
- **Selector drift** — when a selector breaks, fix the application to expose a stable selector (`data-testid`); only fall back to CSS as last resort
- **Speed budget** — if any E2E pack grows past 5 min, split into smaller journeys
- **Coverage** — track which user journeys are covered; flag gaps to product/engineering

## Reporting E2E Results

E2E test reports should include:

- **Journey name** — what user flow was tested
- **Pack name** — exact pack path
- **Result** — PASS / FAIL / TIMEOUT
- **Screenshot path** — for failures, the captured screenshot
- **Steps executed** — which steps passed, which failed
- **Timing** — total runtime; per-step timing for slow flows
- **Environmental notes** — DB seed version, browser version, network conditions
- **Artifacts** — links to traces, logs, downloaded files

## Anti-Patterns

Avoid these E2E mistakes:

- **Testing implementation through UI** — if a unit test can cover it, don't push it to E2E
- **Brittle selectors** — text/XPath breaks on every redesign; use `data-testid`
- **Long sequential suites** — split into small, independent journeys
- **Running against production** — never; always sandboxed env
- **Sleeping instead of waiting** — wait for explicit conditions (`page.wait_for_selector`), not `time.sleep(3)`
- **Ignoring flakiness** — quarantine flaky tests, fix root causes (see flaky-test-management skill)
- **E2E for everything** — E2E is expensive; reserve for critical journeys
- **Hardcoded test data** — parameterize emails, names, IDs; never reuse the same user across tests