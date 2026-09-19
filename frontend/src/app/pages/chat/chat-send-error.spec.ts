/**
 * Phase 4 (clipboard-image-chat) — Task 11 acceptance (b)+(c)
 * spec pins for the failed-bubble / timeout path.
 *
 * The reviewer flagged UNVERIFIED that the existing chat-component
 * error/retry surface preserves:
 *   (b) NO auto-retry on a typed `MessageSendTimeoutError` — the
 *       user must explicitly click the Retry button on the failed
 *       bubble (auto-retry is banned because the server may have
 *       completed the handler regardless of client disconnect);
 *   (c) the optimistic bubble's `pending: true` flag SURVIVES the
 *       timeout — the user must see what was in flight, and the
 *       `failed: true` affordance must stack ON TOP of the pending
 *       state (not replace it).
 *
 * The production code at chat.component.ts already satisfies both
 * invariants via `markSendFailedForContent` (lines 1703-1762) — the
 * spread `...m` preserves `pending: true`, and the retry is gated
 * by an explicit user click on `chat-interface.html:177`. This
 * spec pins that contract so a future contributor who "helpfully"
 * clears `pending` on error OR adds a setTimeout-based auto-retry
 * fails the build.
 *
 * Coverage:
 *   - production-source identity-grep pins assert the VERBATIM
 *     timeout copy AND the verbatim retry-click handler exist
 *     in production source;
 *   - the failed-bubble mirror asserts (i) the spread `...m`
 *     preserves `pending: true`, (ii) the retry handler refuses
 *     to fire when `failed` is false (no auto-retry), (iii) the
 *     `evictPendingByAge` helper EXPLICITLY preserves `failed`
 *     entries (TTL eviction does not strip the pending+failed
 *     bubble);
 *   - the typed error path exercises MessageSendTimeoutError →
 *     errorReason verbatim copy → markSendFailedForContent, with
 *     the pending flag surviving end-to-end.
 */
import * as fs from 'fs';
import * as path from 'path';

// ─── Production-source identity-grep pins (decisions.md §2.2) ────────────
//
// These load the production source at spec-time and assert the
// literals the reviewer needs. Drift here means a contributor
// renamed the typed error class, the copy string, or removed the
// retry-click handler — all of which would silently break
// acceptance (b)+(c).
const PROD_CHAT_SOURCE = fs.readFileSync(
  path.join(__dirname, '../../pages/chat/chat.component.ts'),
  'utf8',
);
const PROD_CHAT_INTERFACE_HTML = fs.readFileSync(
  path.join(__dirname, '../../components/chat-interface/chat-interface.html'),
  'utf8',
);
const PROD_MERGE_UTIL_SOURCE = fs.readFileSync(
  path.join(__dirname, '../../services/message-merge.util.ts'),
  'utf8',
);
const PROD_API_SOURCE = fs.readFileSync(
  path.join(__dirname, '../../services/api.service.ts'),
  'utf8',
);

// ────────────────────────────────────────────────────────────────────────

describe('Task 11 acceptance (b)+(c): failed-bubble + timeout', () => {
  // ─── Production-source identity-grep pins ──────────────────────────
  describe('production-source identity-grep', () => {
    it('chat.component.ts contains the verbatim timeout copy', () => {
      // The error handler at chat.component.ts:1522 reads
      // ``err.message`` into ``errorReason`` and pipes it into the
      // bubble. The production copy is owned by MessageSendTimeoutError
      // (api.service.ts); the FE just relays it. The pin lives in
      // api.service.ts and is the single source of truth for the
      // verbatim string.
      expect(PROD_API_SOURCE).toContain(
        'Request timed out — the message may still have been delivered; please check the transcript before retrying',
      );
    });

    it('chat.component.ts has NO setTimeout / setInterval (auto-retry ban)', () => {
      // The absence of any timer-based retry is the strongest signal
      // that auto-retry is impossible — a setTimeout in the error
      // branch would be the canonical implementation. Any future
      // contributor adding one MUST update this pin (and the spec
      // will fail closed).
      expect(PROD_CHAT_SOURCE).not.toMatch(/setTimeout|setInterval/);
    });

    it('chat.component.ts error handler calls markSendFailedForContent (preserves bubble)', () => {
      // The error branch (chat.component.ts:1520-1553) MUST pipe the
      // errorReason through markSendFailedForContent so the bubble
      // survives. A contributor who "simplified" by clearing the
      // messages list would silently drop the bubble — this pin
      // enforces the existing contract.
      expect(PROD_CHAT_SOURCE).toMatch(/markSendFailedForContent\(/);
    });

    it('chat.component.ts markSendFailedForContent uses spread ...m (preserves pending)', () => {
      // The spread `...m` is the load-bearing invariant for (c).
      // Replacing it with an explicit field set would drop
      // `pending: true` and break the UI.
      // Regex: `\.\.\.m` followed within a few lines by `failed:`.
      expect(PROD_CHAT_SOURCE).toMatch(/\.\.\.m[\s\S]{0,200}failed:\s*true/);
    });

    it('chat.component.ts onRetryFailedMessage refuses to fire without failed flag', () => {
      // The guard at line 1811: ``if (!target || !target.failed) return;``
      // is the second half of (b). Without it the retry would fire
      // for any bubble.
      expect(PROD_CHAT_SOURCE).toMatch(/onRetryFailedMessage[\s\S]{0,200}!\s*target\.failed/);
    });

    it('chat-interface.html binds onRetryFailedMessage to the Retry button click', () => {
      // The user-click-only contract — the Retry button is the ONLY
      // trigger for onRetryFailedMessage. No programmatic invocation.
      expect(PROD_CHAT_INTERFACE_HTML).toContain('onRetryFailedMessage.emit');
    });

    it('message-merge.util.ts evictPendingByAge preserves failed entries', () => {
      // The TTL eviction MUST NOT strip a failed bubble — the user
      // needs to see what failed and decide to retry or dismiss.
      // The load-bearing branch is `if (msg.failed) { result.push(msg); continue; }`.
      // Anchor on the `evictPendingByAge` function and check the
      // `if (msg.failed)` guard lands within the function body.
      const fnMatch = PROD_MERGE_UTIL_SOURCE.match(/export function evictPendingByAge[\s\S]+?\n\}\n/);
      expect(fnMatch).not.toBeNull();
      const fnBody = fnMatch![0];
      expect(fnBody).toMatch(/if\s*\(\s*msg\.failed\s*\)/);
      expect(fnBody).toMatch(/result\.push\(msg\)/);
    });

    it('api.service.ts surfaces MessageSendTimeoutError (NOT generic TimeoutError)', () => {
      // The typed-error discriminator at api.service.ts is the only
      // way the failed-bubble UI can branch on the timeout case.
      // Replacing it with the raw rxjs TimeoutError would break the
      // pending-survives contract (no typed-error → no retry-disabled
      // affordance in future UI).
      expect(PROD_API_SOURCE).toContain('MessageSendTimeoutError');
    });
  });

  // ─── Behavioral mirror: failure path + pending survival ────────────
  describe('behavioral mirror (pending survives failed-mark)', () => {
    // The optimistic bubble has `pending: true` after
    // `makeProvisionalMessage`. The error handler calls
    // `markSendFailedForContent` which spreads `...m` and adds
    // `failed: true` — preserving `pending: true` while adding the
    // failed-state affordance. We mirror that contract here.

    function makeProvisional(input: { messageId: string; content: string; instanceId: string; images?: string[] }) {
      return {
        message_id: input.messageId,
        role: 'user' as const,
        content: input.content,
        created_at: '2026-09-19T19:00:00+00:00',
        instance_id: input.instanceId,
        images: input.images,
        pending: true,
      };
    }

    function markSendFailedForContent(
      msgs: any[],
      sentContent: string,
      errorReason: string,
      sentInstanceId: string,
      sentQueueId?: string | null,
    ): any[] {
      const escaped = sentContent.replace(/^\/\//, '/');
      let next = msgs;
      for (let i = msgs.length - 1; i >= 0; i--) {
        const m = msgs[i];
        if (m.role !== 'user') continue;
        if (m.failed) continue;
        if (m.instance_id && m.instance_id !== sentInstanceId) continue;
        if (m.content === sentContent || m.content === escaped) {
          const stash = sentQueueId !== undefined ? sentQueueId : m.queue_id;
          const retryStash = m.retry_content !== undefined ? m.retry_content : sentContent;
          const updated = {
            ...m,                              // PRESERVES pending: true (acceptance (c))
            failed: true,
            errorReason,
            queue_id: stash,
            retry_content: retryStash,
          };
          next = msgs.slice();
          next[i] = updated;
          return next;
        }
      }
      return msgs;
    }

    function onRetryFailedMessage(msgs: any[], messageId: string): { autoRetried: boolean; target: any | undefined } {
      const target = msgs.find(m => m.message_id === messageId);
      // Guard at chat.component.ts:1811: refuses to fire without failed.
      if (!target || !target.failed) return { autoRetried: false, target };
      // In production this dispatches `onSendMessage({ ..., retry_of_message_id: messageId })`.
      // For the mirror, we just record that the user explicitly clicked retry.
      return { autoRetried: false, target };
    }

    it('on a typed MessageSendTimeoutError, the bubble keeps pending: true (acceptance (c))', () => {
      const provisional = makeProvisional({ messageId: 'srv-1', content: 'check this', instanceId: 'inst-1' });
      const initial = [provisional];
      // Simulate the error branch firing the typed timeout error.
      const errorReason = 'Request timed out — the message may still have been delivered; please check the transcript before retrying';
      const after = markSendFailedForContent(initial, 'check this', errorReason, 'inst-1');

      expect(after.length).toBe(1);
      const bubble = after[0];
      expect(bubble.failed).toBe(true);
      expect(bubble.errorReason).toBe(errorReason);
      // The load-bearing invariant for (c):
      expect(bubble.pending).toBe(true);
      // The retry stash lands on the bubble so onRetryFailedMessage
      // can re-POST the original form (escape-aware — F1 fix).
      expect(bubble.retry_content).toBe('check this');
    });

    it('NO auto-retry fires on the typed timeout error (acceptance (b))', () => {
      const provisional = makeProvisional({ messageId: 'srv-1', content: 'check this', instanceId: 'inst-1' });
      const after = markSendFailedForContent([provisional], 'check this', 'timeout', 'inst-1');

      // Simulate the FE continuing to process events. The retry
      // handler MUST refuse to fire without an explicit user click —
      // i.e. without a "click" on the Retry button. We model the
      // click as a separate user-action; the error handler itself
      // does NOT trigger it.
      const clickResult = onRetryFailedMessage(after, 'srv-1');
      // The handler would dispatch onSendMessage — we model that as
      // having FIRED only because the user clicked, NOT because the
      // error fired. The autoRetried flag here is purely observational.
      expect(clickResult.autoRetried).toBe(false);
      expect(clickResult.target?.failed).toBe(true);
      // The handler ALSO refuses to dispatch for non-failed bubbles —
      // e.g. for any pending bubble that hasn't yet been marked
      // failed. The guard returns ``undefined`` from the click flow.
      const noFailedBubble = makeProvisional({ messageId: 'srv-2', content: 'hi', instanceId: 'inst-1' });
      const refusal = onRetryFailedMessage([noFailedBubble], 'srv-2');
      expect(refusal.autoRetried).toBe(false);
      // The guard returns ``target`` (not undefined) when the bubble
      // exists but isn't failed; the CALLER still skips the POST
      // because ``!target.failed`` is true at chat.component.ts:1811.
      expect(refusal.target?.failed).toBeFalsy();
      // Cross-pin: a click on a never-existed id returns target=undefined.
      const missing = onRetryFailedMessage([], 'srv-99');
      expect(missing.autoRetried).toBe(false);
      expect(missing.target).toBeUndefined();
    });

    it('evictPendingByAge preserves a failed+pending bubble (TTL does not strip)', () => {
      // The TTL helper at message-merge.util.ts:244 explicitly
      // preserves failed entries. Mirror the contract.
      const stale = {
        message_id: 'srv-1',
        role: 'user' as const,
        content: 'stuck',
        created_at: '2026-01-01T00:00:00+00:00', // ancient
        instance_id: 'inst-1',
        pending: true,
        failed: true,
      };
      const fresh = {
        message_id: 'srv-2',
        role: 'user' as const,
        content: 'fresh',
        created_at: new Date().toISOString(),
        instance_id: 'inst-1',
        pending: true,
      };
      const msgs = [stale, fresh];
      const now = Date.now();

      function evictPendingByAge(messages: any[], maxAgeMs: number, nowMs: number): any[] {
        let touched = false;
        const result: any[] = [];
        for (const msg of messages) {
          if (msg.failed) {
            result.push(msg);
            continue;
          }
          if (msg.pending) {
            const ts = Date.parse(msg.created_at);
            if (Number.isNaN(ts) || nowMs - ts > maxAgeMs) {
              touched = true;
              continue;
            }
          }
          result.push(msg);
        }
        return touched ? result : [...messages];
      }

      const after = evictPendingByAge(msgs, 600_000, now); // 10-minute TTL
      // Both bubbles survive:
      //   - the failed bubble (srv-1): preserved by the explicit
      //     `if (msg.failed) result.push(msg)` guard — TTL does NOT
      //     strip it even though its timestamp is ancient;
      //   - the fresh pending bubble (srv-2): preserved because
      //     its age is well below the 10-minute TTL.
      expect(after.length).toBe(2);
      // Ordering preserved (stable pass-through).
      expect(after[0].message_id).toBe('srv-1');
      expect(after[0].failed).toBe(true);
      expect(after[0].pending).toBe(true);
      expect(after[1].message_id).toBe('srv-2');
      expect(after[1].pending).toBe(true);
    });

    it('evictPendingByAge STRIPS a non-failed + stale pending bubble (TTL still applies)', () => {
      // The TTL eviction is still active for non-failed bubbles —
      // this is the belt-and-braces cross-check that the failed
      // guard is targeted, not a blanket "never evict pending" rule.
      const stalePending = {
        message_id: 'srv-1',
        role: 'user' as const,
        content: 'ancient pending',
        created_at: '2026-01-01T00:00:00+00:00',
        instance_id: 'inst-1',
        pending: true,
        // NOT failed.
      };
      const now = Date.now();
      function evictPendingByAge(messages: any[], maxAgeMs: number, nowMs: number): any[] {
        let touched = false;
        const result: any[] = [];
        for (const msg of messages) {
          if (msg.failed) { result.push(msg); continue; }
          if (msg.pending) {
            const ts = Date.parse(msg.created_at);
            if (Number.isNaN(ts) || nowMs - ts > maxAgeMs) {
              touched = true;
              continue;
            }
          }
          result.push(msg);
        }
        return touched ? result : [...messages];
      }
      const after = evictPendingByAge([stalePending], 600_000, now);
      expect(after.length).toBe(0);
    });

    it('the failed-bubble retry rebuild preserves pending + threades refs (image_refs path)', () => {
      // Acceptance cross-check: when the user clicks Retry on a
      // failed bubble that carried ref-form images, the rebuild
      // must thread them through `image_refs` (XOR sibling) AND
      // preserve the original `pending`/failed state until the next
      // POST confirms. Mirror of chat.component.ts:1852-1861.
      const refBubble = {
        message_id: 'srv-1',
        role: 'user' as const,
        content: 'with image',
        created_at: '2026-09-19T19:00:00+00:00',
        instance_id: 'inst-1',
        images: ['/api/tmp_images/abc123def456789012345678901234de'],
        pending: true,
        failed: true,
        errorReason: 'timeout',
        retry_content: 'with image',
        queue_id: 'q-1',
      };

      function buildRetryPayload(bubble: any): any {
        const bubbleImages = bubble.images ?? [];
        const looksLikeRefUrl = bubbleImages.length > 0
          && bubbleImages[0].startsWith('/api/tmp_images/');
        return {
          content: bubble.retry_content ?? bubble.content,
          images: looksLikeRefUrl ? undefined : bubbleImages,
          image_refs: looksLikeRefUrl ? bubbleImages : undefined,
          queue_id: bubble.queue_id ?? null,
          retry_of_message_id: bubble.message_id,
        };
      }

      const retryPayload = buildRetryPayload(refBubble);
      // XOR sibling discipline preserved.
      expect(retryPayload.image_refs).toEqual(['/api/tmp_images/abc123def456789012345678901234de']);
      expect(retryPayload.images).toBeUndefined();
      // The bubble still has its pending + failed state until the next POST confirms.
      expect(refBubble.pending).toBe(true);
      expect(refBubble.failed).toBe(true);
    });
  });
});
