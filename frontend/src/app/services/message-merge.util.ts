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
 * to stabilize the DISPLAYED stamp for confirmed entries (MIN-4): the
 * GET re-stamp for metadata-less checkpoint rows is a moving value
 * (latest checkpoint-commit time), and earliest-seen is the stablest
 * available display heuristic.
 *
 * Positional note (stale-message fix, 2026-09-05): this helper has NO
 * effect on list order. Transcript order is array-order-based
 * everywhere (server seed + arrival appends); before this fix the
 * helper co-existed with a full ``created_at`` re-sort it was pinning
 * positions against — that re-sort is gone, so a wrong-derived stamp
 * can no longer freeze a wrong position. It only affects what the
 * bubble displays.
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
 * timestamp for ``earlierOf`` display-stamp stabilization; send
 * position is preserved by array-order semantics in
 * ``mergeMessagesById`` (§6 edge case 1).
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
 * ORDERING (stale-message fix, 2026-09-05): the merged list preserves
 * the EXISTING array order — server-known rows upsert IN PLACE,
 * genuinely-new rows append at the end in incoming array order. There
 * is NO ``created_at`` re-sort. The previous implementation re-sorted
 * the whole transcript after every merge, which let UNSTABLE server
 * stamps reorder history: the backend re-stamps metadata-less
 * checkpoint messages with the latest checkpoint-commit time on every
 * read (a moving value), so an old row could time-travel to directly
 * above the freshly sent message — and a reload (server array order)
 * would then disagree with what the user saw. Server ARRAY order is
 * checkpoint order and is the authority; ``created_at`` is display
 * metadata and must never drive position. (Trade-off: a locally
 * scrambled order — possible only from a pre-fix session — heals on
 * the next replace-mode load, not on merge.)
 *
 * Top-level fields from ``incoming`` win on conflict (so SSE / server
 * can patch a tool_calls[].output in place), with corrections:
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
 * MIN-4 (display-stamp stabilization): for server-confirmed (non-
 * pending) entries the merge keeps the EARLIER of the local/incoming
 * timestamps. GET read-back re-stamps rows with the checkpoint-commit
 * timestamp — a MOVING value for metadata-less rows — so keeping the
 * earliest-seen stamp stops the displayed time from drifting forward
 * on every refetch. Because the ordering contract above removed the
 * ``created_at`` re-sort, this rule can no longer affect POSITION
 * (its original purpose was pinning send-position under the old sort
 * regime); it now only stabilizes what the bubble displays. Pending
 * merges keep incoming-wins so the 202 body stamp rules.
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
      // Defect #5 (2026-08-31): the ``failed`` flag is server-blind —
      // a failed POST never reached the server, so an SSE echo /
      // refetch MUST NOT silently clear the user-visible error state
      // and re-render the bubble as delivered. If the existing copy
      // is failed, keep the failed flag (and the errorReason) so the
      // user can retry / dismiss. Incoming failed flags are
      // preserved too (uniform behavior — the server cannot have a
      // message we never sent).
      if (result[idx].failed) {
        merged.failed = true;
        if (result[idx].errorReason) merged.errorReason = result[idx].errorReason;
      }
      // Defect #5 retry stash (must-fix #2, 2026-08-31): the
      // ``queue_id`` on a failed bubble is the original-send queue
      // context the retry must re-use. The server has no notion of
      // queue_id (client-side routing), but incoming echoes that
      // explicitly carry ``queue_id: undefined`` would clobber the
      // stash via spread; pin the existing copy through every merge
      // pass so a retry that races an SSE echo still finds the stash.
      if (result[idx].queue_id !== undefined) {
        merged.queue_id = result[idx].queue_id;
      }
      // F1 escape-retry stash (2026-08-31): the ``retry_content`` on a
      // failed bubble is the ORIGINAL-send content the retry must
      // re-POST verbatim (preserves the RAW ``//x`` form for escape
      // messages whose bubble carries the delivered ``/x`` form).
      // The server has no notion of ``retry_content`` (purely
      // client-side retry correctness), but incoming echoes that
      // explicitly carry ``retry_content: undefined`` would clobber
      // the stash via spread; pin the existing copy through every
      // merge pass so a retry that races an SSE echo still finds
      // the stash.
      if (result[idx].retry_content !== undefined) {
        merged.retry_content = result[idx].retry_content;
      }
      // MIN-4: for CONFIRMED (non-pending) entries keep the earlier of
      // the local/incoming timestamps so the moving GET re-stamp
      // (checkpoint-commit ts) cannot churn the displayed time. This
      // is display-metadata stabilization ONLY — with the ``created_at``
      // re-sort removed (ordering contract in the doc above) it cannot
      // affect bubble position. Pending merges keep incoming-wins so
      // the 202 body stamp rules.
      if (!merged.pending) {
        merged.created_at = earlierOf(result[idx].created_at, msg.created_at);
      }
      result[idx] = merged;
    } else {
      result.push(msg);
    }
  }
  // NO re-sort here. Transcript order = existing array order (seeded
  // from the server's array = checkpoint order) + arrival-order
  // appends above. Re-sorting by ``created_at`` let unstable
  // checkpoint re-stamps time-travel old rows around the freshly sent
  // message — the stale-message display bug this fixes.
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
 * Defect #5 (2026-08-31): ``failed`` entries are NEVER evicted by this
 * pass — a failed send is a user-actionable state (retry / dismiss) and
 * dropping it silently would re-introduce the "dishonest bubble" bug
 * (a user who looks away from the screen and back would see the error
 * vanish without acknowledgement).
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
    if (msg.failed) {
      // Never evict a failed entry — leave it for the user to retry
      // or dismiss. The chat component removes the entry explicitly
      // when the user clicks "Retry" (which re-attempts the POST) or
      // "Dismiss" (which removes the bubble from the list).
      result.push(msg);
      continue;
    }
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

