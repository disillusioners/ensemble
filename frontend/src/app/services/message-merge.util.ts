/**
 * Pure helpers for the chat message list. Centralizes the merge / eviction
 * / provisional-append logic so it can be unit-tested without Angular and
 * reused across the SSE mirror effect and the REST refetch path.
 *
 * Design reference: ``.agents/shared/planning/message-display-latency/
 * architecture-recommendation.md`` §4.3 items 9–14, §5 items 5–10, §6.
 */
import type { Message, InstanceStatus } from '../models';

/**
 * Terminal instance statuses. A ``status_change`` to any of these triggers
 * a purge of pending provisional entries (message-display-latency §5
 * item 9). Mirrors the backend ``InstanceStatus`` union declared in
 * ``frontend/src/app/models/index.ts`` — kept as a string allowlist here
 * so the helper has zero Angular dependencies and can be exercised by a
 * plain jest spec.
 */
const TERMINAL_STATUSES: ReadonlySet<InstanceStatus> = new Set<InstanceStatus>([
  'completed',
  'error',
  'terminated',
  'failed',
]);

/**
 * Returns ``true`` when ``status`` is terminal per the allowlist above.
 * Anything unknown (newer backend status, malformed payload, etc.) is
 * treated as NON-terminal — better to keep a stale provisional bubble
 * than to drop a real one on a status we don't recognize.
 */
export function isTerminalStatus(status: string | null | undefined): boolean {
  if (!status) return false;
  return TERMINAL_STATUSES.has(status as InstanceStatus);
}

/**
 * Return the earlier of two ISO timestamp strings by lexicographic
 * (i.e. chronological) order. When both sides are truthy, return the
 * earlier; when only one is truthy, return that one; when neither is
 * truthy (both empty / zero-like), return ``a`` so the merge stays
 * string-typed for ``Message.created_at``. Used by ``mergeMessagesById``
 * to keep the pre-refetch timestamp for confirmed entries (MIN-4) —
 * the GET re-stamp is later than the POST stamp, and re-sorting on
 * every refetch would push the bubble below inter-streamed assistant
 * messages.
 */
function earlierOf(a: string, b: string): string {
  if (a && b) return a <= b ? a : b;
  return a || b;
}

/**
 * Build a provisional user message from a POST response. The return is
 * NON-NULLABLE — ``messageId`` is required by the type. The caller
 * (``chat.component.ts:onSendMessage``) owns the null-guard: it
 * short-circuits the optimistic append entirely when ``message_id`` is
 * absent (old backend / PAUSED ``None``), degrading to today's
 * render-on-echo flow rather than shipping a content-matching
 * reconciler (message-display-latency §4.3 item 12).
 *
 * The returned message is keyed by the server-minted id (which the
 * id-keyed dedup collapses onto when the POST-time ``user_message`` SSE
 * echo lands and the drain-time re-emit later) and carries the POST
 * timestamp so FE sort-by-``created_at`` keeps the bubble in send
 * position relative to mid-stream assistant messages (§6 edge case 1).
 */
export function makeProvisionalMessage(input: {
  messageId: string;
  content: string;
  createdAt: string;
  instanceId: string;
  images?: string[];
}): Message {
  return {
    message_id: input.messageId,
    role: 'user',
    content: input.content,
    created_at: input.createdAt,
    instance_id: input.instanceId,
    images: input.images,
    pending: true,
  };
}

/**
 * Union-by-id merge: every entry in ``incoming`` upserts into ``existing``
 * by ``message_id``. Entries present only in ``existing`` (local-only
 * provisional / pending bubbles) are preserved — this is the hard
 * prerequisite for the optimistic-append path: any pre-drain refetch
 * (REST or reconnect catch-up) MUST NOT wipe the provisional bubble the
 * user just saw render.
 *
 * Symmetric with the SSE mirror effect at ``chat.component.ts:374-392``,
 * which already merges by id. Extracted here so the merge contract has a
 * single canonical implementation across the REST and SSE paths and a
 * single unit-tested surface.
 *
 * Top-level fields from ``incoming`` win on conflict (so SSE / server
 * can patch a tool_calls[].output in place), with two corrections:
 *
 * MIN-1b (pending-flag resurrection): the ``pending`` flag survives a
 * merge ONLY when BOTH copies are pending. The previous rule (clear
 * when the incoming copy is non-pending) left the reverse arrival
 * order broken: if the SSE echo lands BEFORE the HTTP 202 response,
 * the optimistic append would merge a ``pending: true`` provisional
 * over the already-confirmed server copy and resurrect the spinner
 * on a bubble the server had already confirmed. Now either side being
 * confirmed clears the flag, in both arrival orders.
 *
 * MIN-4 (GET ``created_at`` re-stamp): for server-confirmed (non-
 * pending) entries the merge keeps the EARLIER of the local/incoming
 * timestamps instead of incoming-wins. GET read-back re-stamps rows
 * with the checkpoint-commit timestamp (later than the original POST
 * stamp), which re-sorted the user bubble below inter-streamed
 * assistant messages on every refetch. Keeping the earlier stamp pins
 * the bubble in its original send position.
 */
export function mergeMessagesById(
  existing: readonly Message[],
  incoming: readonly Message[],
): Message[] {
  if (incoming.length === 0) return [...existing];
  const result: Message[] = [...existing];
  for (const msg of incoming) {
    const idx = result.findIndex(m => m.message_id === msg.message_id);
    if (idx >= 0) {
      // Top-level merge: incoming wins, but the provisional flag is
      // cleared when EITHER copy is confirmed — pending survives only
      // if both the existing and the incoming copy carry it (MIN-1b:
      // a pending copy merging over a confirmed one must NOT
      // resurrect the spinner).
      const merged = { ...result[idx], ...msg };
      const existingIsPending = result[idx].pending === true;
      if (!msg.pending || !existingIsPending) {
        delete merged.pending;
      }
      // MIN-4: for CONFIRMED (non-pending) entries keep the earlier of
      // the local/incoming timestamps so a GET re-stamp
      // (checkpoint-commit ts) cannot re-sort the bubble. Pending
      // merges keep incoming-wins so the 202 body stamp rules.
      if (!merged.pending) {
        merged.created_at = earlierOf(result[idx].created_at, msg.created_at);
      }
      result[idx] = merged;
    } else {
      result.push(msg);
    }
  }
  result.sort((a, b) => (a.created_at || '').localeCompare(b.created_at || ''));
  return result;
}

/**
 * Wall-clock TTL eviction for pending entries. Drops any message that
 * carries ``pending: true`` AND whose ``created_at`` is older than
 * ``maxAgeMs`` before ``nowMs`` (10 minutes per
 * ``architecture-recommendation.md`` §4.3 item 11 — multi-minute agent
 * turns are normal, so do NOT go shorter).
 *
 * Non-pending entries are never touched (server-confirmed history stays
 * intact). ``pending`` entries with an invalid / missing ``created_at``
 * are evicted conservatively — a provisional entry we cannot timestamp
 * is suspect and should not linger.
 *
 * Stateless and pure: callers can run this on every refetch / merge to
 * get lazy eviction without a background timer.
 */
export function evictPendingByAge(
  messages: readonly Message[],
  maxAgeMs: number,
  nowMs: number,
): Message[] {
  let touched = false;
  const result: Message[] = [];
  for (const msg of messages) {
    if (msg.pending) {
      const ts = Date.parse(msg.created_at);
      // Invalid timestamp OR older than the TTL: evict. NaN from
      // ``Date.parse`` means the backend sent something unparseable
      // (extremely unlikely) — treat as expired so we don't leak a
      // permanent provisional entry.
      if (Number.isNaN(ts) || nowMs - ts > maxAgeMs) {
        touched = true;
        continue;
      }
    }
    result.push(msg);
  }
  return touched ? result : [...messages];
}

