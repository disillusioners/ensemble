# LESSON: E2E chat navigation recipe + agent-card selector gotcha

Date: 2026-07-25
Feature: system prompt toggle e2e test (`feature/fe-toggle-system-prompt @ df56403b`)

## Problem
When writing Playwright e2e tests that need to enter a chat view from the Angular frontend home/instances page, clicking the agent card `<span>` alone does **not** navigate reliably — it can trigger the vite-error-overlay pointer-interception overlay, blocking the click.

## Root Cause
The agent card's clickable affordance is the `.start-agent` button (with `aria-label="Start new chat with {AgentName}"`), not the outer card span. Clicking the span without hitting the button does not fire the router navigation.

## Fix / Recipe
Navigate to a chat view by targeting the `.start-agent` button directly:

```ts
await page.locator('.start-agent').first().click();
await page.waitForSelector('.chat-header');
```

- Prefer `.start-agent` (button) over the agent-card `<span>`.
- Use `waitUntil:'domcontentloaded'` on `page.goto` (NEVER `networkidle` — SSE keeps network active).
- `waitForSelector('.chat-header')` confirms the chat view rendered.

## Applies To
Any future e2e pack that needs to reach the chat view (toggles, message send/stop, instance chat features). Pick a 'leader' or 'developer' instance — wanderer/KB instances may be filtered from `instanceService.instances()`.

## Context
Discovered during the system-prompt-toggle e2e pack; worker recorded the insight as skill feedback too.
