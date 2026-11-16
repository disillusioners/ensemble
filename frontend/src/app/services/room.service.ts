import { Injectable, WritableSignal, inject, signal } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import {
  RoomListResponse,
  RoomMessage,
  RoomMessagesResponse,
  RoomSummary,
  normalizeRoomListResponse,
  normalizeRoomMessagesResponse,
} from '../models/room.model';

/**
 * Room read API client — the FE half of the P6 group-chat observability
 * surface (architecture recommendation §13.1).
 *
 * Two lanes, ONE requestId guard each (the snapshots-page precedent —
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
 *
 * The guard is "increment-and-capture": each request takes the next id,
 * and the response handler compares the CURRENT id against the captured
 * one. A mismatch means a newer request superseded this one → drop.
 *
 * Errors are FAIL-CLOSED and honest: the affected lane is emptied and an
 * explicit error message is published (404 → not-found, 403 → out of
 * scope, 0 → unreachable). No fabricated rows ever reach the template.
 */
@Injectable({ providedIn: 'root' })
export class RoomService {
  private readonly http = inject(HttpClient);

  /** URL constants — the single source of truth for the FE ↔ BE surface. */
  static readonly INSTANCES_URL = '/api/instances';
  static readonly ROOMS_URL = '/api/rooms';

  // ── rooms lane ──────────────────────────────────────────────────────────
  /** Rooms created by the viewed instance('s tree). */
  readonly rooms: WritableSignal<RoomSummary[]> = signal<RoomSummary[]>([]);
  /** True while a rooms request is in flight. */
  readonly roomsLoading: WritableSignal<boolean> = signal(false);
  /** Human-readable error for the rooms lane (null = no error). */
  readonly roomsError: WritableSignal<string | null> = signal<string | null>(null);
  /** HTTP status behind `roomsError` (null = no error) — drives the copy. */
  readonly roomsErrorStatus: WritableSignal<number | null> = signal<number | null>(null);
  private roomsRequestId = 0;

  // ── messages lane ───────────────────────────────────────────────────────
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

  // ── raw HTTP surface ────────────────────────────────────────────────────

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
   * ascending by `seq`.
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

  // ── stateful lanes (guarded) ────────────────────────────────────────────

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
   * response for the previous room can never land in this one.
   */
  openRoom(roomId: string): void {
    this.resetMessages();
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
   * that lands after the reset fails the guard and is dropped).
   */
  resetMessages(): void {
    this.messagesRequestId++;
    this.messages.set([]);
    this.nextAfterSeq.set(null);
    this.messagesError.set(null);
    this.messagesErrorStatus.set(null);
    this.messagesLoading.set(false);
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
