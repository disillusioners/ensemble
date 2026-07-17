---
version: 1.0.0
category: maintenance
auto_load: false
---

# Flaky Test Management

A flaky test passes and fails across multiple runs with no code changes. Flakes erode trust in the test suite — when a pack fails, no one knows if it's a real bug or just noise. This skill describes how to detect, quarantine, and resolve flaky tests without letting them block pack results.

## Lifecycle Overview

```
Test fails on run 1 → suspect flakiness
  → Run retry budget (3× with no code change)
    → ≥1 pass AND ≥1 fail → confirm flaky
      → Add to QUARANTINE.md
      → Mark test as skipped in its pack
      → Document in LESSONS/
      → Pack stays green; flaky test auto-skipped across future runs
        → Fix attempted
          → Update QUARANTINE.md (status → RESOLVED)
          → Re-enable test
          → Run 3× clean to confirm
            → All pass → resolved
            → Any fail → re-quarantine
```

## Detection (Retry Budget)

### When to Suspect Flakiness

- A test fails on run 1 but passes on run 2 with no fix applied
- The same test shows different results across recent CI runs
- A test fails "intermittently" or "randomly" without code changes
- Failure timing correlates with CI load, network latency, or shared state

### Retry Budget Protocol

1. Pick the suspect test (single test, or test class/scenario)
2. Run it **3 times consecutively** with no code changes
3. Tally results: `passes / fails`
4. Confirm flaky if: **at least 1 pass AND at least 1 fail** (e.g., 2P/1F, 1P/2F, 3P/3F...)

If all 3 pass → not flaky; investigate the original failure as a real issue.
If all 3 fail → not flaky; the test is genuinely broken.

### What to Capture During the Retry Budget

- All 3 run timestamps
- Exit codes per run
- Relevant logs / output snippets (especially the differing behavior)
- Environment conditions (CI vs local, load level, time of day)
- Test invocation command and arguments

## Quarantine Process

Once a test is confirmed flaky:

### Step 1: Add to `.agents/tester/QUARANTINE.md`

```markdown
# Quarantined Tests

## Active

| Test | Pack | Date Quarantined | Reason | Retry Budget | Attempts (P/F) | Status |
|------|------|------------------|--------|--------------|----------------|--------|
| tests/test_auth.py::test_login_token_expiry | auth_unit_test | 2026-07-15 | Token expiry race condition vs system clock | 3 | 2P/1F | QUARANTINED |
```

Fields:
- **Test**: full test ID (module + class + function)
- **Pack**: the pack that runs it
- **Date Quarantined**: ISO date
- **Reason**: failure pattern / suspected cause
- **Retry Budget**: always 3 (per protocol)
- **Attempts (P/F)**: actual results from the retry budget
- **Status**: `QUARANTINED`

### Step 2: Mark as Skipped in the Pack Script

Pack scripts must skip quarantined tests so they don't count toward PASS/FAIL:

- Use the test framework's skip mechanism (e.g., `@pytest.mark.skip(...)` with a conditional, `--deselect` in pytest, `t.Skip` in Go)
- The skip condition reads `QUARANTINE.md` (parsed at pack start) OR uses an environment flag set per the QUARANTINE.md entries
- Quarantined tests still run in a separate "quarantine verification" mode (optional), but never in the normal pack run

### Step 3: Document in `.agents/tester/LESSONS/`

Create a file like `flaky-test-[test-name].md` with:

- The failure pattern observed (intermittent, time-dependent, load-dependent)
- The retry budget results (3 attempts, what passed/failed)
- Suspected root cause (e.g., race condition, shared state, network timing, system clock)
- Any investigation done so far
- Linked QUARANTINE.md entry

### Step 4: Report to Leader

Surface the quarantine action in the next report:

- "Quarantined test X in pack Y (retry budget: 2P/1F). Pack remains green. Root cause suspected: [reason]. Investigation pending."

## Auto-Skip (Until Resolved)

Quarantined tests stay skipped across all future runs:

- The pack script reads QUARANTINE.md at start; deselects all listed tests
- A pack PASSES if all non-quarantined tests pass; quarantined tests are not counted
- The pack does NOT re-evaluate quarantine status per run (no "did the flake heal itself?" check)
- Track the rising quarantine count as a quality signal — a growing list means tests are degrading, not improving

### Why Auto-Skip Without Re-Evaluation

- **Predictability** — test results are stable across runs
- **Trust** — a green pack is genuinely green (modulo the quarantined surface)
- **Tracking** — quarantined tests are visible, not hidden; the report shows the count
- **Discipline** — un-quarantine requires a real fix + 3× clean re-run (see below)

## Un-Quarantine (After a Fix)

Once a fix targets the suspected root cause:

### Step 1: Fix Is Attempted

- Quick fix (see quick-fix skill) if the fix is small (< 20 lines, obvious)
- Full workflow if larger or architectural

### Step 2: Update QUARANTINE.md

Move the entry from the **Active** table to the **Resolved** table:

```markdown
## Resolved (history)

| Test | Pack | Date Resolved | Fix | Confirming Runs |
|------|------|---------------|-----|-----------------|
| tests/test_auth.py::test_login_token_expiry | auth_unit_test | 2026-07-20 | Use freezegun.freeze_time for deterministic token expiry | 3× PASS |
```

### Step 3: Re-enable the Test

Remove the skip in the pack script (or remove from the dynamic skip list).

### Step 4: 3× Clean Re-Run

Run the un-quarantined test 3 times consecutively with no code changes.

- **All 3 pass** → resolution confirmed. QUARANTINE.md status → RESOLVED.
- **Any run fails** → re-quarantine. The fix did not resolve the flakiness. Document new findings in LESSONS/, update QUARANTINE.md with new retry budget results.

## Reporting

The final report must include:

- **Quarantined count** — total active quarantines
- **List of quarantined tests** — full test IDs, with pack and date
- **Coverage impact** — "X tests skipped (see QUARANTINE.md)"
- **Trend** — is the count rising? (rising = quality risk to flag)
- **Recent resolutions** — recently un-quarantined tests (transparency)

When the quarantine count rises significantly (e.g., +5 in a week), surface this as a quality signal to the leader. A growing quarantine list indicates test debt that needs dedicated attention.

## Common Root Causes

When investigating a flaky test, suspect:

| Symptom | Likely Root Cause |
|---------|-------------------|
| Fails under CI load, passes locally | Shared state, race condition, resource contention |
| Fails intermittently with timing error | Sleep/poll without proper wait condition |
| Fails when run with other tests, passes alone | Order dependency, shared mutable state, port conflict |
| Fails on first run of the day, passes after | Stale cache, expired tokens, lazy initialization |
| Fails with "connection refused" intermittently | Service not warmed up, port not yet bound |
| Time-dependent failures | Real `datetime.now()` instead of injected clock |
| Network-dependent failures | Real HTTP calls instead of mocks (use mock-test skill) |
| Database-dependent failures | Missing transactions, shared DB state |

## Anti-Patterns

Avoid these mistakes:

- **Deleting flaky tests to make a pack green** — never. Quarantine instead.
- **Increasing retries without root-cause fix** — masks the problem; eventually surfaces worse
- **Un-quarantining without 3× clean re-run** — unverified resolution; risk of recurrence
- **Quarantining tests that are consistently failing** — those are broken, not flaky; fix them
- **Letting QUARANTINE.md grow unbounded** — track the count; surface rising trends
- **Ignoring the underlying root cause** — quarantine buys time, not resolution

## Decision Flow

```
Test failed?
├─ Did code change since last green?
│  ├─ Yes → likely real bug; investigate
│  └─ No → suspect flaky
│           └─ Run retry budget (3× no code change)
│                ├─ All pass → not flaky; investigate original failure
│                ├─ All fail → not flaky; test is broken; fix
│                └─ Mixed → flaky
│                     ├─ Add to QUARANTINE.md (status: QUARANTINED)
│                     ├─ Mark as skipped in pack script
│                     ├─ Document in LESSONS/
│                     └─ Investigate root cause (full workflow)
```