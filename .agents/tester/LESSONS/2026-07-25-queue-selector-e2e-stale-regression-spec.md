# Queue Selector E2E — Stale Regression Spec + Screenshot Path Quirk

**Date:** 2026-07-25
**Feature:** queue-select-message / queue selector UI
**Found During:** Final browser E2E verification (run/ref ID 1784968639656)
**Severity:** 🟡 Medium (test-maintenance — does NOT block the feature; feature is verified PASS)

## Finding 1: Regression spec is STALE — reports false failures

The existing Playwright spec `frontend/e2e/queue-selector-regression.spec.ts`
fails Tests 1 & 2 with `waitForSelector` timeout, but **NOT because of a real
queue-selection bug**.

### Root cause
The spec hardcodes:
```ts
const INSTANCE_ID = 'eb79d594-648f-400e-8df8-30893de1ef80';
```
This is the *target* instance, which is now in `waiting_children` status (not
`idle`). The queue selector is conditionally rendered only when:
```html
<!-- message-input.html:124 -->
@if (isIdle() && queues().length > 0) { ... }
```
So on a running instance, the `<select>` is **permanently hidden** → the
`waitForSelector('label.queue-selector select', { timeout: 20000 })` call
always times out.

### Additional staleness
The spec's KNOWN-FAIL note on Test 1 ("default shows system_background_queue
instead of system_parallel_queue") is **obsolete**. The bug was fully fixed
across two commits:
- `fd876cfb` — moved `[value]` from `<select>` to `[selected]` per `<option>` (HTML binding fix)
- `2567af9e` — "fix(message-input): default to system_parallel_queue by name, not id" (logic fix; line 126 now compares `q.queue_name === 'system_parallel_queue'` instead of UUID-vs-name)

### Recommended fix (NOT done — out of verification scope)
Update the spec to **spawn a fresh IDLE instance** for Tests 1 & 2, exactly
as Test 3 already does. Then remove the stale KNOWN-FAIL marker on Test 1.

```ts
// Pattern to adopt (Test 3 already does this):
const spawn = await apiCtx.post('/api/instances', {
  data: { agent_id: 'leader', project_id: PROJECT_ID },
});
const freshInstance = (await spawn.json()).instance_id;
// ... use freshInstance instead of the hardcoded INSTANCE_ID ...
// cleanup: await apiCtx.delete(`/api/instances/${freshInstance}`);
```

### Lesson
> When an E2E spec hardcodes an `INSTANCE_ID` and the feature under test is
> **conditionally rendered based on instance state** (e.g. `@if (isIdle())`),
> the spec will silently rot as soon as that instance's state changes. E2E
> specs should spawn their own fresh instances in a known state, or read the
> state first and skip if not applicable.

## Finding 2: Screenshot path resolves ABOVE the frontend root

The regression spec computes its screenshots dir as:
```ts
const SHOTS_DIR = path.join(__dirname, '..', '..', 'e2e-shots', 'queue-selector');
```
`__dirname` is `frontend/e2e/`, so `../../e2e-shots` resolves to
**`<project-root>/e2e-shots`** — NOT `frontend/e2e-shots/`.

### Impact
Screenshots are written outside the `frontend/` tree. A reviewer looking in
`frontend/e2e-shots/` will not find them. This is harmless functionally but
confusing for artifact discovery.

### Recommended fix
Use a path rooted at `process.cwd()` or a fixed location:
```ts
const SHOTS_DIR = path.join(process.cwd(), 'e2e-shots', 'queue-selector');
// or, if run from frontend/:
const SHOTS_DIR = path.join(__dirname, '..', 'e2e-shots', 'queue-selector');
```

## Artifacts
- Full report: `.agents/tester/RESULTS/2026-07-25-queue-selector-e2e-final-verification.md`
- Screenshots: `<project-root>/e2e-shots/queue-selector/final-*.png`
- Fix commits (feature bug, already applied): `fd876cfb`, `2567af9e`
- KB entries: recorded via `experience()` during the verification run
