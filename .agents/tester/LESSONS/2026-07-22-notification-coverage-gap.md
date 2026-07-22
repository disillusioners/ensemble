# Lesson: Notification SSE Handler Coverage Gap

**Branch:** `feature/notification-ui-improvements`
**Date:** 2026-07-22
**Severity:** Non-blocking (mitigated), but worth tracking

## Finding
The jest worker (fe-jest-test) discovered that `notification.service.ts` lines 126–144 — the
`instance_created` SSE handler that maps `project_id` and `instance_name` — is the branch's
headline feature code, yet has **zero direct unit test coverage**.

The existing `notification.service.spec.ts` only covers: WAV audio decoding, audio-unlock logic,
signal behavior (add/limit/unreadCount), cleanup, and sound exclusion.

## Mitigation Applied
The mock SSE flow test (`test/packs/notification_mock_sse_test.py`, 37 scenarios) validates this
logic path via:
1. Pure-logic simulation of the Angular service's mapping
2. Live mock SSE server on port 10080 delivering real `instance_created` events

## Root Cause
Feature code was added without a corresponding spec update. The 1237 frontend tests all pass, but
the new mapping code was never directly exercised by jest.

## Recommendation
Before merging, add a dedicated spec in `notification.service.spec.ts` covering:
- `instance_created` event → notification added with correct `project_id`/`instance_name`
- Title priority chain (`instance_name > name > agent_id`)
- KB agent filtering when `showKb=False`
- Edge cases: `project_id=None`, `instance_name=None`, empty strings

This provides long-term regression protection at the unit level (faster than the mock test).

## Pattern to Watch
Frontend feature branches that modify a `.service.ts` SSE handler should always update the
corresponding `.spec.ts`. The jest suite being green does not mean the new code is covered.
