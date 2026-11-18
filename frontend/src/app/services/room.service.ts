import { Injectable, WritableSignal, inject, signal } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import {
  GlobalRoomListResponse,
  GlobalRoomSummary,
  RoomListResponse,
  RoomMessage,
  RoomMessagesResponse,
  RoomSummary,
  normalizeGlobalRoomListResponse,
  normalizeRoomListResponse,
  normalizeRoomMessagesResponse,
} from '../models/room.model';

/**
 * Room read API client — the FE half of the P6 group-chat observability
 * surface (architecture recommendation §13.1) plus the gc-fe-surfaces
 * global lane.
 *
 * Lanes, ONE requestId guard each (the snapshots-page precedent —
 * `snapshots.component.ts` `listRequestId`, `snapshot-detail-drawer`
 * `detailRequestId` / `digestRequestId`):
 *
 * * **rooms lane** — `GET /api/instances/{instance_id}/rooms`. Guarded
 *   against the rapid instance-switch race: a slow response for a
 *   previously-viewed instance is DROPPED, never rendered over the
 *   current instance's rooms.
 * * **messages lane** — `GET /api/rooms/{room_id}/messages`. Guarded
 *   against the rapid room-switch race AND the paging race: a page that
 *   resolves after the user moved on (or after a newer page was
 *   requested) is dropped instead of splicing stale rows into the
 *   conversation.
 * * **global lane** — `GET /api/rooms[?instance_id=…]` (gc-fe-surfaces
 *   phase 1). Its OWN requestId guard and its OWN publication signals
 *   (`globalRooms` / `globalRoomsTruncated`) so the shipped lanes stay
 *   byte-identical. `global` mode is ANCHOR-LESS (the nest-wide
 *   browser); `joined` mode is ANCHORED on the row's instance and then
 *   FILTERED client-side by `member_instance_ids` — the FE only decides
 *   *which of the server-listed rooms to show*; the server alone decides
 *   readability (D1 rationale).
 *
 * The guard is "increment-and-capture": each request takes the next id,
 * and the response handler compares the CURRENT id against the captured
 * one. A mismatch means a newer request superseded this one → drop.
 *
 * Errors are FAIL-CLOSED and honest: the affected lane is emptied and an
 * explicit error message is published (404 → not-found, 403 → out of
 * scope, 0 → unreachable). No fabricated rows ever reach the template.
 * The polling lane degrades differently: it KEEPS the last-good rows and
 * raises the `stale` flag (the honest "this may be out of date" state).
 */
@Injectable({ providedIn: 'root' })
export class RoomService {
  private readonly http = inject(HttpClient);

  /** URL constants — the single source of truth for the FE ↔ BE surface. */
  static readonly INSTANCES_URL = '/api/instances';
  static readonly ROOMS_URL = '/api/rooms';

  /** Polling interval for the room surfaces (D2). */
  readonly POLL_INTERVAL_MS = 10_000;

  // ── rooms lane (shipped, instance-scoped) ────────────────────────────
  /** Rooms created by the viewed instance('s tree). */
  readonly rooms: WritableSignal<RoomSummary[]> = signal<RoomSummary[]>([]);
  /** True while a rooms request is in flight. */
  readonly roomsLoading: WritableSignal<boolean> = signal(false);
  /** Human-readable error for the rooms lane (null = no error). */
  readonly roomsError: WritableSignal<string | null> = signal<string | null>(null);
  /** HTTP status behind `roomsError` (null = no error) — drives the copy. */
  readonly roomsErrorStatus: WritableSignal<number | null> = signal<number | null>(null);
  private roomsRequestId = 0;

  // ── messages lane (shipped) ──────────────────────────────────────────
  /** The selected room's conversation, ascending by `seq`. */
  readonly messages: WritableSignal<RoomMessage[]> = signal<RoomMessage[]>([]);
  /** True while a messages request is in flight. */
  readonly messagesLoading: WritableSignal<boolean> = signal(false);
  /** Human-readable error for the messages lane (null = no error). */
  readonly messagesError: WritableSignal<string | null> = signal<string | null>(null);
  /** HTTP status behind `messagesError` (null = no error). */
  readonly messagesErrorStatus: WritableSignal<number | null> = signal<number | null>(null);
  /** Cursor for the next page; null when the conversation is exhausted. */
  readonly nextAfterSeq: WritableSignal<number | null> = signal<number | null>(null);
  private messagesRequestId = 0;

  // ── global lane (gc-fe-surfaces) ─────────────────────────────────────
  /** The global browser's rows (one publication target — W-7). */
  readonly globalRooms: WritableSignal<GlobalRoomSummary[]> = signal<GlobalRoomSummary[]>([]);
  /** True when the server cut rows from this list (the envelope flag). */
  readonly globalRoomsTruncated: WritableSignal<boolean> = signal<boolean>(false);
  /** True while a global-lane request is in flight. */
  readonly globalRoomsLoading: WritableSignal<boolean> = signal(false);
  /** Human-readable error for the global lane (null = no error). */
  readonly globalRoomsError: WritableSignal<string | null> = signal<string | null>(null);
  /** HTTP status behind `globalRoomsError` (null = no error). */
  readonly globalRoomsErrorStatus: WritableSignal<number | null> = signal<number | null>(null);
  private globalRoomsRequestId = 0;
  private globalRoomsMode: 'global' | 'joined' = 'global';
  private globalRoomsScope: string | null = null;

  // ── open room tracking (AM-5) ────────────────────────────────────────
  /**
   * The id of the open conversation (set by `openRoom`, cleared by
   * `resetMessages`). The polling tick SKIPS silently while this is null
   * — no request, no error state.
   */
  readonly openRoomId: WritableSignal<string | null> = signal<string | null>(null);
  /**
   * A-12 — the open room has VANISHED from the refreshed list (purged or
   * closed out of the caller's readable set). The selection is preserved
   * by id (`openRoomId` is NOT reset); the pane shows an honest
   * closed/absent state instead of an empty "no messages" that would
   * read as a live-but-silent room. Cleared when the room reappears in a
   * refresh or when the selection changes.
   */
  readonly openRoomAbsent: WritableSignal<boolean> = signal<boolean>(false);

  // ── polling (D2) ─────────────────────────────────────────────────────
  /** True when the last poll failed — rows are retained, copy is honest. */
  readonly stale: WritableSignal<boolean> = signal<boolean>(false);
  private pollHandle: ReturnType<typeof setInterval> | null = null;
  private pollInFlight = false;
  private pollSeq = 0;
  private browserVisible = true;
  private visibilityListener: (() => void) | null = null;

  // ── raw HTTP surface ─────────────────────────────────────────────────

  /**
   * `GET /api/instances/{instance_id}/rooms` — the chatrooms created by
   * that instance('s tree). Raw Observable; the stateful lane below owns
   * the requestId guard.
   */
  fetchRooms(instanceId: string): Observable<RoomListResponse> {
    return this.http.get<RoomListResponse>(
      `${RoomService.INSTANCES_URL}/${encodeURIComponent(instanceId)}/rooms`,
    );
  }

  /**
   * `GET /api/rooms/{room_id}/messages` — one page of the conversation,
   * ascending by `seq`. Anchor-less by design in EVERY mode (W-8): the
   * detail pane resolves through the messages route's operator-lane
   * default (the room's owner).
   *
   * @param afterSeq cursor from the previous page; omitted for the first
   *   page (the contract treats an absent `after_seq` as "from the start").
   * @param limit page size — clamped to the contract's [1, 200] window.
   */
  fetchMessages(
    roomId: string,
    afterSeq: number | null = null,
    limit: number = ROOM_MESSAGE_PAGE_LIMIT,
  ): Observable<RoomMessagesResponse> {
    let params = new HttpParams().set('limit', String(clampLimit(limit)));
    if (afterSeq !== null && Number.isFinite(afterSeq)) {
      params = params.set('after_seq', String(afterSeq));
    }
    return this.http.get<RoomMessagesResponse>(
      `${RoomService.ROOMS_URL}/${encodeURIComponent(roomId)}/messages`,
      { params },
    );
  }

  /**
   * `GET /api/rooms[?instance_id=<id>]` — the global lane's raw fetch.
   * A scope anchor turns the read into the caller-scoped list; without
   * one the read is the nest-wide (knob-gated) breadth.
   */
  fetchAllRooms(scopeInstanceId?: string | null): Observable<GlobalRoomListResponse> {
    if (scopeInstanceId) {
      const params = new HttpParams().set('instance_id', scopeInstanceId);
      return this.http.get<GlobalRoomListResponse>(`${RoomService.ROOMS_URL}`, { params });
    }
    return this.http.get<GlobalRoomListResponse>(`${RoomService.ROOMS_URL}`);
  }

  // ── stateful lanes (guarded) ─────────────────────────────────────────

  /**
   * Load the rooms of an instance('s tree). Supersedes any in-flight
   * rooms request — the older response is dropped when it lands.
   */
  loadRooms(instanceId: string): void {
    const reqId = ++this.roomsRequestId;
    this.roomsLoading.set(true);
    this.roomsError.set(null);
    this.roomsErrorStatus.set(null);
    this.fetchRooms(instanceId).subscribe({
      next: (resp) => {
        if (this.roomsRequestId !== reqId) return; // stale — dropped
        this.rooms.set(normalizeRoomListResponse(resp).rooms);
        this.roomsLoading.set(false);
      },
      error: (err: unknown) => {
        if (this.roomsRequestId !== reqId) return; // stale — dropped
        this.rooms.set([]);
        const described = describeHttpError(err, 'chatrooms');
        this.roomsErrorStatus.set(described.status);
        this.roomsError.set(described.message);
        this.roomsLoading.set(false);
      },
    });
  }

  /**
   * Select a room: clear the previous conversation and load its first
   * page. Any in-flight messages request is invalidated first, so a late
   * response for the previous room can never land in this one. The
   * open-room id is what the polling tick composes its fetch from (AM-5).
   */
  openRoom(roomId: string): void {
    this.resetMessages();
    this.openRoomId.set(roomId);
    this.openRoomAbsent.set(false);
    this.loadMessages(roomId, false);
  }

  /**
   * Load a page of the conversation.
   *
   * @param append false → replace the conversation with the first page;
   *   true → append the next page (using the stored `next_after_seq`
   *   cursor). Appends are a no-op when the conversation is exhausted.
   */
  loadMessages(roomId: string, append: boolean): void {
    if (append && this.nextAfterSeq() === null) return; // exhausted
    const reqId = ++this.messagesRequestId;
    const afterSeq = append ? this.nextAfterSeq() : null;
    this.messagesLoading.set(true);
    this.messagesError.set(null);
    this.messagesErrorStatus.set(null);
    this.fetchMessages(roomId, afterSeq).subscribe({
      next: (resp) => {
        if (this.messagesRequestId !== reqId) return; // stale — dropped
        const normalized = normalizeRoomMessagesResponse(resp);
        this.messages.update((current) =>
          append ? mergeMessagesBySeq(current, normalized.messages) : normalized.messages,
        );
        this.nextAfterSeq.set(normalized.next_after_seq);
        this.messagesLoading.set(false);
      },
      error: (err: unknown) => {
        if (this.messagesRequestId !== reqId) return; // stale — dropped
        const described = describeHttpError(err, 'messages');
        this.messagesErrorStatus.set(described.status);
        this.messagesError.set(described.message);
        this.messagesLoading.set(false);
      },
    });
  }

  /**
   * Clear the conversation AND invalidate any in-flight messages request
   * (the id bump is what makes the invalidation effective — a response
   * that lands after the reset fails the guard and is dropped). Also
   * clears the open-room tracking — nothing is open after a reset.
   */
  resetMessages(): void {
    this.messagesRequestId++;
    this.messages.set([]);
    this.nextAfterSeq.set(null);
    this.messagesError.set(null);
    this.messagesErrorStatus.set(null);
    this.messagesLoading.set(false);
    this.openRoomId.set(null);
    this.openRoomAbsent.set(false);
  }

  /**
   * Load the global lane.
   *
   * @param mode `global` → ANCHOR-LESS (`GET /api/rooms`, the nest-wide
   *   browser); `joined` → ANCHORED (`GET /api/rooms?instance_id=<id>`)
   *   and then filtered client-side to rows whose `member_instance_ids`
   *   includes the anchor (plan task 9(ii) — the service lane owns the
   *   filter so EVERY consumer gets the same correct set; the server
   *   alone decides readability).
   * @param scopeInstanceId the anchor for `joined` mode; ignored by
   *   `global`.
   */
  loadGlobalRooms(mode: 'global' | 'joined', scopeInstanceId?: string | null): void {
    this.globalRoomsMode = mode;
    this.globalRoomsScope = scopeInstanceId ?? null;
    const anchor = mode === 'joined' ? (scopeInstanceId ?? null) : null;
    const reqId = ++this.globalRoomsRequestId;
    this.globalRoomsLoading.set(true);
    this.globalRoomsError.set(null);
    this.globalRoomsErrorStatus.set(null);
    this.fetchAllRooms(anchor).subscribe({
      next: (resp) => {
        if (this.globalRoomsRequestId !== reqId) return; // stale — dropped
        const normalized = normalizeGlobalRoomListResponse(resp);
        const rows =
          mode === 'joined' && anchor
            ? normalized.rooms.filter((room) => room.member_instance_ids.includes(anchor))
            : normalized.rooms;
        this.globalRooms.set(rows);
        this.globalRoomsTruncated.set(normalized.truncated === true);
        this.globalRoomsLoading.set(false);
        // A-12 — the refreshed list is the truth about whether the open
        // room still exists.
        this.evaluateOpenRoomPresence(rows);
      },
      error: (err: unknown) => {
        if (this.globalRoomsRequestId !== reqId) return; // stale — dropped
        this.globalRooms.set([]);
        this.globalRoomsTruncated.set(false);
        const described = describeHttpError(err, 'chatrooms');
        this.globalRoomsErrorStatus.set(described.status);
        this.globalRoomsError.set(described.message);
        this.globalRoomsLoading.set(false);
      },
    });
  }

  // ── polling lane ─────────────────────────────────────────────────────

  /**
   * Start the 10 s poll. Idempotent in the sense that a prior handle is
   * stopped first. Ticks are skipped while the tab is hidden (the
   * `browserVisible` flag is INITIALIZED from `document.hidden`, so a
   * hidden tab never issues the first tick) and while a tick is already
   * in flight. A tick with no open room issues NO request and sets NO
   * error.
   */
  startPolling(): void {
    this.stopPolling();
    this.browserVisible = typeof document !== 'undefined' ? !document.hidden : true;
    if (typeof document !== 'undefined') {
      this.visibilityListener = () => {
        if (document.hidden) {
          this.browserVisible = false;
        } else {
          this.browserVisible = true;
          this.refreshOpenRoom();
        }
      };
      document.addEventListener('visibilitychange', this.visibilityListener);
    }
    this.pollHandle = setInterval(() => {
      if (!this.browserVisible) return;
      if (this.pollInFlight) return;
      if (!this.openRoomId()) return;
      this.refreshOpenRoom();
    }, this.POLL_INTERVAL_MS);
  }

  /** Stop the poll and detach the visibility listener. */
  stopPolling(): void {
    if (this.pollHandle !== null) {
      clearInterval(this.pollHandle);
      this.pollHandle = null;
    }
    if (this.visibilityListener && typeof document !== 'undefined') {
      document.removeEventListener('visibilitychange', this.visibilityListener);
      this.visibilityListener = null;
    }
    this.pollInFlight = false;
  }

  /** Re-issue the CURRENT mode's global list (manual/periodic refresh). */
  refreshRooms(): void {
    this.loadGlobalRooms(this.globalRoomsMode, this.globalRoomsScope);
  }

  /**
   * Fetch new messages for the open room and merge them by `seq`.
   *
   * Cursor policy (AM-5): the poll cursor is the MAX loaded `seq` — never
   * `nextAfterSeq` — and the tick does NOT mutate `nextAfterSeq` (a
   * deliberate freeze: a frozen exhausted cursor freezes `loadMore`; new
   * rows arrive through the merge instead). A failure keeps the last-good
   * rows and raises `stale`; a success clears `stale`.
   */
  refreshOpenRoom(): void {
    const roomId = this.openRoomId();
    if (!roomId) return;
    if (this.pollInFlight) return;
    const seq = this.maxLoadedSeq();
    this.pollInFlight = true;
    const token = ++this.pollSeq;
    this.fetchMessages(roomId, seq).subscribe({
      next: (resp) => {
        if (token !== this.pollSeq) return; // superseded — dropped
        const normalized = normalizeRoomMessagesResponse(resp);
        this.messages.update((current) => mergeMessagesBySeq(current, normalized.messages));
        // cursor policy: do NOT mutate nextAfterSeq
        this.stale.set(false);
      },
      error: () => {
        if (token !== this.pollSeq) return; // superseded — dropped
        this.stale.set(true);
        // An errored observable terminates WITHOUT a `complete` emission —
        // release the flag here too, or polling wedges (D17).
        this.pollInFlight = false;
      },
      // D17 — an observable that completes without emitting must still
      // release the in-flight flag, or polling wedges forever.
      complete: () => {
        if (token === this.pollSeq) this.pollInFlight = false;
      },
    });
  }

  private maxLoadedSeq(): number {
    const msgs = this.messages();
    if (msgs.length === 0) return 0;
    return Math.max(...msgs.map((m) => m.seq));
  }

  /**
   * A-12 — reconcile the open-room selection with a freshly published
   * list: absent (room gone from the readable set) raises the honest
   * absent flag; its reappearance clears it. The selection itself is
   * PRESERVED by id — never silently reset.
   */
  private evaluateOpenRoomPresence(rows: GlobalRoomSummary[]): void {
    const openId = this.openRoomId();
    if (!openId) {
      this.openRoomAbsent.set(false);
      return;
    }
    this.openRoomAbsent.set(!rows.some((room) => room.room_id === openId));
  }
}

/** The contract's default page size for the room-read API. */
export const ROOM_MESSAGE_PAGE_LIMIT = 50;

/** The contract's hard cap for the `limit` query parameter. */
export const ROOM_MESSAGE_MAX_LIMIT = 200;

/** Clamp a requested page size into the contract's [1, 200] window. */
function clampLimit(limit: number): number {
  if (!Number.isFinite(limit)) return ROOM_MESSAGE_PAGE_LIMIT;
  return Math.min(ROOM_MESSAGE_MAX_LIMIT, Math.max(1, Math.trunc(limit)));
}

/**
 * Merge an incoming page into the loaded conversation. Rows are keyed by
 * `seq` — a duplicate (the backend re-serving a page, or a cursor that
 * lands on an already-loaded row) is dropped rather than rendered twice.
 */
function mergeMessagesBySeq(current: RoomMessage[], incoming: RoomMessage[]): RoomMessage[] {
  if (incoming.length === 0) return current;
  const seen = new Set<number>(current.map((m) => m.seq));
  const merged = [...current];
  for (const message of incoming) {
    if (seen.has(message.seq)) continue;
    seen.add(message.seq);
    merged.push(message);
  }
  return merged;
}

/**
 * Map an HTTP failure onto an HONEST, human-readable message. The lanes
 * are fail-closed: the caller empties the affected list and shows this
 * text — never a fabricated row.
 */
function describeHttpError(
  err: unknown,
  subject: 'chatrooms' | 'messages',
): { status: number | null; message: string } {
  const raw = (err ?? {}) as {
    status?: unknown;
    message?: unknown;
    error?: unknown;
  };
  const status = typeof raw.status === 'number' ? raw.status : null;
  if (status === 404) {
    return {
      status,
      message:
        subject === 'chatrooms'
          ? 'Not found — these chatrooms no longer exist (they may have been purged).'
          : 'Not found — this conversation is no longer available (it may have been purged).',
    };
  }
  if (status === 403) {
    return {
      status,
      message: `Access denied — these ${subject} are outside your scope.`,
    };
  }
  if (status === 0) {
    return { status, message: 'Could not reach the server.' };
  }
  const detail = extractErrorDetail(raw.error) ?? extractErrorDetail(raw.message);
  return { status, message: detail ?? `Failed to load ${subject}.` };
}

/** Pull a message out of a FastAPI error body (`{detail: ...}`). */
function extractErrorDetail(error: unknown): string | null {
  if (typeof error === 'string' && error.trim()) return error.trim();
  if (error === null || typeof error !== 'object') return null;
  const detail = (error as { detail?: unknown }).detail;
  if (typeof detail === 'string' && detail.trim()) return detail.trim();
  if (detail !== null && typeof detail === 'object') {
    const message = (detail as { message?: unknown }).message;
    if (typeof message === 'string' && message.trim()) return message.trim();
  }
  return null;
}
