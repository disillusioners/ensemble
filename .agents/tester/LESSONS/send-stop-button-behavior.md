# Send/Stop Button Behavior — Critical Finding

## Date: 2026-05-15

## The Semantic Mismatch

The `isStreaming` signal in `sse.service.ts` means **"SSE connection is alive"**, NOT "instance is actively streaming a response".

### What This Means for Testing

1. **Stop button appears on page load** — SSE connects immediately, `isStreaming=true`
2. **Stop button stays after clicking stop** — `onStopInstance()` only calls API, doesn't disconnect SSE
3. **Send button only appears when SSE disconnects** — error, navigation away, or manual disconnect

### Code Path
```
Page load → handleInstanceIdChange() → loadInstanceMessages() → sseService.connect()
  → SSE 'connected' event → isStreaming.set(true) → Stop button rendered
```

### Selectors for E2E Tests
- Stop button: `app-message-input .stop-button`
- Send button: `app-message-input .send-button`
- Textarea: `app-message-input .input-textarea`
- Stop icon: `app-message-input .stop-button .stop-icon rect`

### Angular Probe Trick
To manually disconnect SSE in Playwright:
```typescript
await page.evaluate(() => {
  const appChat = document.querySelector('app-chat');
  const ngElement = (window as any).ng?.getComponent(appChat);
  ngElement?.sseService?.disconnect();
});
```

### Key Files
- `frontend/src/app/services/sse.service.ts` — SSE service with `isStreaming` signal
- `frontend/src/app/components/message-input/message-input.component.ts` — Button component
- `frontend/src/app/components/message-input/message-input.component.html` — Button template
- `frontend/src/app/pages/chat/chat.component.ts` — Chat page with `onStopInstance()`
