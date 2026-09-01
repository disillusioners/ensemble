# Lesson: message-display-latency e2e workarounds + coverage debt (2026-08-31)

Gate: feature/message-display-latency @ b8c7a611 — PASS. Two durable takeaways for future test work in this repo.

## 1. FE e2e automation gotchas (verified live, Playwright 1.60 + Chromium)

1. **SEND_COOLDOWN_MS = 3000** (chat.component.ts:148) silently drops a send fired < 3 s after the previous one — no POST in the network log, no error. Any e2e that sends a follow-up must wait ≥ 5 s. Symptom looks like "button dead".
2. **CDP network-offline blocks `page.goto`** (SPA can't fetch HTML). For instance switching under offline conditions, click the sidebar `<a class="instance-item" [routerLink]>` — SPA router navigation needs no network.
3. **`GET /api/instances/{id}/messages` returns a top-level JSON list**, not `{messages: [...]}`. `response.messages || []` silently yields `[]`. Use `Array.isArray(response) ? response : response.messages || []`.
4. **Chat hydration takes ~8 s for 50+ messages after page load** — 3 s DOM polls underreport. Always wait for full hydration before counting bubbles.
5. **No `data-testid` attributes in the FE.** Working selectors: `textarea.input-textarea`, `button.send-button`, `button[aria-label="Send to running instance"]`, `.message-row.user-message .message-bubble`, `a.instance-item[href*="<instance_id>"]`.
6. **SSE disconnect emulation that actually arms the FE reconnect logic is non-trivial**: CDP `EmulateNetworkConditions offline` produced the offline UI state, but the reconnect refetch-effect (chat.component.ts:437-444) never fired in the scenario-D run (0 refetches in network log). If a test needs the armed error→connected→refetch path, force a real EventSource error (e.g. kill/restore the BE connection) rather than relying on CDP emulation alone.

## 2. Mock-fidelity coverage debt (from the gate's §3 audit — none blocking, all follow-ups)

- `TestSseService` surrogate in sse.service.spec.ts pins a `'checkpoint'` listener that production `connectInternal()` no longer wires (stale pin, spec:177-197/:332-386) — surrogate-rot class the N2 real-service block was built to catch; N2 itself only covers connect() early-return semantics so far.
- Production `injection_pending` / `injection_consumed` handlers (sse.service.ts:512-560) have ZERO FE spec coverage despite being first-class events in the injection contract.
- `TestableChatComponent` surrogate forwards `sendMessage` with 3 args while production passes 4 (queue_id, chat.component.ts:1293) — a queue_id-forwarding regression would pass current specs.
- Component-level reconnect wiring (refetchRequest → loadInstanceMessages(merge:true), chat.component.ts:439-443) unpinned; the spec's loadInstanceMessages mirror uses pre-merge `set()` semantics.
- The 3-hop echo-id continuity chain (POST echo == 202 body id == drain id == GET id) is stitched across test_injection_api.py (mocked manager) + integration file (real graph) — no single live-path assertion.

## 3. Base-attribution pattern re-confirmed

`git worktree add` at merge-base + single-file pytest + `git worktree remove --force` settled an 8-failure adjudication in < 10 s of runtime with zero risk to the read-only checkout. job_queue_proxy_phase1 ×8 is PRE-EXISTING at latest (97b0f0b3) — QUARANTINE.md row stamped.
