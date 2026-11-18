/**
 * Wire mirror for the P6 group-chat room read surface.
 *
 * Source of truth: `.agents/shared/planning/group-chat/architecture-recommendation.md`
 * §13.1 (the binding P6 UI direction) + §5 (HTTP role scope — the room-read
 * API is served at the `instances:read` tier and rides the SAME predicate as
 * the agents' read tool: members ∪ owner-ancestry, fail-closed).
 *
 * PINNED CONTRACT (the backend lane implements the same shape in parallel):
 *
 *   GET /api/instances/{instance_id}/rooms
 *     → 200 {"rooms":[{"room_id":str,"name":str,"created_at":iso,
 *                      "closed":bool,"member_count":int,
 *                      "last_message_at":iso|null,"last_message_seq":int|null}]}
 *
 *   GET /api/rooms/{room_id}/messages?after_seq=<int>&limit=<int>
 *     → 200 {"messages":[{"seq":int,"instance_id":str,"member_name":str,
 *                         "content":str,"created_at":iso,"kind":str}],
 *             "next_after_seq":int|null}
 *
 *   GET /api/rooms?instance_id=<id>&limit=<int>
 *     → 200 {"rooms":[{"room_id":str,"name":str,"tree_root_id":str,
 *                      "owner_instance_id":str,"state":str,
 *                      "member_count":int,"member_instance_ids":str[],
 *                      "last_message_at":iso|null,"last_message_seq":int|null,
 *                      "created_at":iso}],
 *             "truncated":bool}
 *
 *   The nest-wide route emits the lifecycle STATE (`"active"` /
 *   `"closed"`) — not the `closed` boolean the instance-scoped route
 *   emits; the boolean is DERIVED from the state (see `RoomSummary`).
 *
 * Two invariants the FE depends on:
 *
 * 1. EXTRA FIELDS ARE TOLERATED SILENTLY. The backend may add fields to
 *    either object; the FE renders the KNOWN fields only. The normalizers
 *    below copy the known fields into fresh objects, so an added field can
 *    never leak into the render path.
 * 2. FAIL-CLOSED. A malformed row (no `room_id` / no `seq`) is DROPPED
 *    rather than rendered half-formed — a room without an id cannot be
 *    selected, and a message without a sequence number cannot be ordered
 *    or paged. Dropping is honest; fabricating is not.
 */

/** A chatroom as listed for one instance('s tree). */
export interface RoomSummary {
  /** Stable room identifier — the key for the room-read API. */
  room_id: string;
  /** Human-readable room name (the naming cascade's resolved name). */
  name: string;
  /** ISO-8601 creation timestamp. */
  created_at: string;
  /**
   * True once the room is closed (closed rooms stay readable).
   *
   * For the GLOBAL row (`GET /api/rooms`) this boolean is DERIVED from
   * that route's `state` field (`state === 'closed'`) — that route
   * emits the lifecycle state, not this boolean.
   */
  closed: boolean;
  /** Active member count (the owner counts). */
  member_count: number;
  /** ISO-8601 timestamp of the last message, or null when the room is empty. */
  last_message_at: string | null;
  /** Sequence number of the last message, or null when the room is empty. */
  last_message_seq: number | null;
}

/** `GET /api/instances/{instance_id}/rooms` response envelope. */
export interface RoomListResponse {
  rooms: RoomSummary[];
}

/** A chatroom as listed by the nest-wide `GET /api/rooms` surface. */
export interface GlobalRoomSummary extends RoomSummary {
  /** The room's tree-root anchor (the scope the room was created in). */
  tree_root_id: string;
  /** The instance that owns the room (the owner-ancestry anchor). */
  owner_instance_id: string;
  /** The ACTIVE member instance ids (the room's roster). */
  member_instance_ids: string[];
}

/** `GET /api/rooms` response envelope (bounded). */
export interface GlobalRoomListResponse {
  rooms: GlobalRoomSummary[];
  truncated?: boolean;
}

/** A single room message (one member's post in a room). */
export interface RoomMessage {
  /** Per-room monotonic sequence number — the paging key. */
  seq: number;
  /** The authoring member's instance id. */
  instance_id: string;
  /** The authoring member's resolved room name (the naming cascade). */
  member_name: string;
  /** Message body (plain text). */
  content: string;
  /** ISO-8601 creation timestamp. */
  created_at: string;
  /** Message kind (e.g. `message` for a plain post). Rendered subtly. */
  kind: string;
}

/** `GET /api/rooms/{room_id}/messages` response envelope. */
export interface RoomMessagesResponse {
  /** Messages ascending by `seq`. */
  messages: RoomMessage[];
  /** Cursor for the next page; null when the conversation is exhausted. */
  next_after_seq: number | null;
}

// ─────────────────────────────────────────────────────────────────────────
// Normalizers — defensive wire → typed view.
//
// The FE never renders a raw response object: every response passes
// through a normalizer that (a) copies the KNOWN fields only, so backend
// additions cannot leak into the template, and (b) drops rows that cannot
// be rendered honestly (no id / no seq).
// ─────────────────────────────────────────────────────────────────────────

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object'
    ? (value as Record<string, unknown>)
    : null;
}

function asString(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback;
}

function asFiniteNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function asNullableString(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}

/**
 * Normalize one room summary. Returns null when the row cannot be
 * rendered honestly (missing/blank `room_id`).
 */
export function normalizeRoomSummary(raw: unknown): RoomSummary | null {
  const rec = asRecord(raw);
  if (!rec) return null;
  const roomId = asString(rec['room_id']).trim();
  if (!roomId) return null;
  const memberCount = asFiniteNumber(rec['member_count']);
  return {
    room_id: roomId,
    // A blank name falls back to the id — the operator still sees WHICH
    // room they are looking at (never an empty row).
    name: asString(rec['name']).trim() || roomId,
    created_at: asString(rec['created_at']),
    closed: rec['closed'] === true,
    member_count: memberCount === null ? 0 : memberCount,
    last_message_at: asNullableString(rec['last_message_at']),
    last_message_seq: asFiniteNumber(rec['last_message_seq']),
  };
}

/** Normalize the global (nest-wide) rooms envelope. */
export function normalizeGlobalRoomListResponse(raw: unknown): GlobalRoomListResponse {
  const rec = asRecord(raw);
  const list = rec && Array.isArray(rec['rooms']) ? (rec['rooms'] as unknown[]) : [];
  const rooms: GlobalRoomSummary[] = [];
  for (const entry of list) {
    const r = asRecord(entry);
    if (!r) continue;
    // The shared room-row mapping — the global route adds three fields
    // and DERIVES `closed` (it emits `state`, not `closed`).
    const base = normalizeRoomSummary(entry);
    if (!base) continue;
    const memberIds = Array.isArray(r['member_instance_ids'])
      ? (r['member_instance_ids'] as unknown[]).filter((x): x is string => typeof x === 'string')
      : [];
    const global: GlobalRoomSummary = {
      ...base,
      // The global route emits the lifecycle STATE, not the `closed`
      // boolean — the boolean is derived from it.
      closed: base.closed || r['state'] === 'closed',
      tree_root_id: asString(r['tree_root_id']),
      owner_instance_id: asString(r['owner_instance_id']),
      member_instance_ids: memberIds,
    };
    rooms.push(global);
  }
  const truncated = rec ? rec['truncated'] === true : false;
  return { rooms, truncated };
}

/** Normalize the rooms envelope — unknown fields are dropped, not rendered. */
export function normalizeRoomListResponse(raw: unknown): RoomListResponse {
  const rec = asRecord(raw);
  const list = rec && Array.isArray(rec['rooms']) ? (rec['rooms'] as unknown[]) : [];
  const rooms: RoomSummary[] = [];
  for (const entry of list) {
    const room = normalizeRoomSummary(entry);
    if (room) rooms.push(room);
  }
  return { rooms };
}

/**
 * Normalize one room message. Returns null when the row cannot be
 * rendered honestly (missing/non-numeric `seq` — the paging key).
 */
export function normalizeRoomMessage(raw: unknown): RoomMessage | null {
  const rec = asRecord(raw);
  if (!rec) return null;
  const seq = asFiniteNumber(rec['seq']);
  if (seq === null) return null;
  return {
    seq,
    instance_id: asString(rec['instance_id']),
    member_name: asString(rec['member_name']).trim() || 'unknown',
    content: asString(rec['content']),
    created_at: asString(rec['created_at']),
    kind: asString(rec['kind']),
  };
}

/** Normalize the messages envelope — unknown fields are dropped. */
export function normalizeRoomMessagesResponse(raw: unknown): RoomMessagesResponse {
  const rec = asRecord(raw);
  const list = rec && Array.isArray(rec['messages']) ? (rec['messages'] as unknown[]) : [];
  const messages: RoomMessage[] = [];
  for (const entry of list) {
    const message = normalizeRoomMessage(entry);
    if (message) messages.push(message);
  }
  return {
    messages,
    next_after_seq: rec ? asFiniteNumber(rec['next_after_seq']) : null,
  };
}

/** Build a membership map: instance_id -> has >= 1 joined room. */
export function buildMembershipMap(rows: GlobalRoomSummary[]): Map<string, boolean> {
  const map = new Map<string, boolean>();
  for (const row of rows) {
    for (const id of row.member_instance_ids || []) {
      map.set(id, true);
    }
  }
  return map;
}
