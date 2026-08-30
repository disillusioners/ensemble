/**
 * Pure helpers for the chat message list. Centralizes the merge / eviction
 * / provisional-append logic so it can be unit-tested without Angular and
 * reused across the SSE mirror effect and the REST refetch path.
 *
 * Design reference: ``.agents/shared/planning/message-display-latency/
 * architecture-recommendation.md`` §4.3 items 9–14, §5 items 5–10, §6.
 */
import type { Message } from '../models';

/**
 * Terminal instance statuses. A ``status_change`` to any of these triggers
 * a purge of pending provisional entries (message-display-latency §5
 * item 9). Mirrors the backend ``InstanceStatus`` union declared in
 * ``frontend/src/app/models/index.ts`` — kept as a string allowlist here
 * so the helper has zero Angular dependencies and can be exercised by a
 * plain jest spec.
 */
const TERMINAL_STATUSES: ReadonlySet<string> = new Set<string>([
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
  return TERMINAL_STATUSES.has(status);
}

/**
 * Build a provisional user message from a POST response. Returns ``null``
 * when ``messageId`` is absent so the caller can short-circuit the
 * optimistic append entirely (message-display-latency §4.3 item 12:
 * skip when ``message_id`` is absent — degrade to today's render-on-echo
 * behavior rather than ship a content-matching reconciler).
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
 * can patch a tool_calls[].output in place), but the ``pending`` flag
 * is cleared when the incoming copy is NOT itself pending — i.e. the
 * SSE echo / GET-refetch / drain-re-emit supersedes the provisional
 * visual state. This is the dedup-collapse behavior (behavior #1): a
 * POST-echo + drain-echo pair with the same id produces a single bubble
 * with ``pending: undefined`` and the POST timestamp.
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
      // Top-level merge: incoming wins, but explicitly clear the
      // provisional flag if the incoming copy is not itself pending
      // (server-side copies are never pending — only the optimistic
      // append carries the flag).
      const merged = { ...result[idx], ...msg };
      if (!msg.pending) {
        delete (merged as { pending?: boolean }).pending;
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

/**
 * Strip ``pending: true`` from every entry. Used when the server has
 * definitively acknowledged the round-trip (e.g. a fresh
 * ``injection_consumed`` event, or a refetch where every server row
 * has the same id as a local pending — the merge above already handles
 * this case-by-case, but a terminal-status purge is broader: drop the
 * provisional state across the board so the "Message queued" /
 * dim-bubble affordances go away when the instance shuts down).
 */
export function clearPendingFlags(messages: readonly Message[]): Message[] {
  let touched = false;
  const result: Message[] = [];
  for (const msg of messages) {
    if (msg.pending) {
      touched = true;
      const { pending: _pending, ...rest } = msg;
      void _pending;
      result.push(rest as Message);
    } else {
      result.push(msg);
    }
  }
  return touched ? result : [...messages];
}
