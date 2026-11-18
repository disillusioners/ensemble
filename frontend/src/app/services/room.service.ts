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
 * surface (architecture recommendation §13.1).
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
  readonly rooms: WritableSignal<RoomSummary[]> = signal<RoomSummary[]>([]);
  readonly roomsLoading: WritableSignal<boolean> = signal(false);
  readonly roomsError: WritableSignal<string | null> = signal<string | null>(null);
  readonly roomsErrorStatus: WritableSignal<number | null> = signal<number | null>(null);
  private roomsRequestId = 0;

  // ── messages lane (shipped) ──────────────────────────────────────────
  readonly messages: WritableSignal<RoomMessage[]> = signal<RoomMessage[]>([]);
  readonly messagesLoading: WritableSignal<boolean> = signal(false);
  readonly messagesError: WritableSignal<string | null> = signal<string | null>(null);
  readonly messagesErrorStatus: WritableSignal<number | null> = signal<number | null>(null);
  readonly nextAfterSeq: WritableSignal<number | null> = signal<number | null>(null);
  private messagesRequestId = 0;

  // ── global lane (gc-fe-surfaces) ─────────────────────────────────────
  readonly globalRooms: WritableSignal<GlobalRoomSummary[]> = signal<GlobalRoomSummary[]>([]);
  readonly globalRoomsTruncated: WritableSignal<boolean> = signal<boolean>(false);
  readonly globalRoomsLoading: WritableSignal<boolean> = signal(false);
  readonly globalRoomsError: WritableSignal<string | null> = signal<string | null>(null);
  readonly globalRoomsErrorStatus: WritableSignal<number | null> = signal<number | null>(null);
  private globalRoomsRequestId = 0;
  private globalRoomsMode: 'global' | 'joined' = 'global';
  private globalRoomsScope: string | null = null;

  // ── open room tracking (AM-5) ────────────────────────────────────────
  readonly openRoomId: WritableSignal<string | null> = signal<string | null>(null);

  // ── polling (D2) ─────────────────────────────────────────────────────
  readonly stale: WritableSignal<boolean> = signal<boolean>(false);
  private pollHandle: ReturnType<typeof setInterval> | null = null;
  private pollInFlight = false;
  private pollSeq = 0;
  private browserVisible = true;
  private visibilityListener: (() => void) | null = null;

  // ── raw HTTP surface ─────────────────────────────────────────────────

  fetchRooms(instanceId: string): Observable<RoomListResponse> {
    return this.http.get<RoomListResponse>(
      `${RoomService.INSTANCES_URL}/${encodeURIComponent(instanceId)}/rooms`,
    );
  }

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

  fetchAllRooms(scopeInstanceId?: string | null): Observable<GlobalRoomListResponse> {
    if (scopeInstanceId) {
      const params = new HttpParams().set('instance_id', scopeInstanceId);
      return this.http.get<GlobalRoomListResponse>(`${RoomService.ROOMS_URL}`, { params });
    }
    return this.http.get<GlobalRoomListResponse>(`${RoomService.ROOMS_URL}`);
  }

  // ── stateful lanes (guarded) ─────────────────────────────────────────

  loadRooms(instanceId: string): void {
    const reqId = ++this.roomsRequestId;
    this.roomsLoading.set(true);
    this.roomsError.set(null);
    this.roomsErrorStatus.set(null);
    this.fetchRooms(instanceId).subscribe({
      next: (resp) => {
        if (this.roomsRequestId !== reqId) return;
        this.rooms.set(normalizeRoomListResponse(resp).rooms);
        this.roomsLoading.set(false);
      },
      error: (err: unknown) => {
        if (this.roomsRequestId !== reqId) return;
        this.rooms.set([]);
        const described = describeHttpError(err, 'chatrooms');
        this.roomsErrorStatus.set(described.status);
        this.roomsError.set(described.message);
        this.roomsLoading.set(false);
      },
    });
  }

  openRoom(roomId: string): void {
    this.resetMessages();
    this.openRoomId.set(roomId);
    this.loadMessages(roomId, false);
  }

  loadMessages(roomId: string, append: boolean): void {
    if (append && this.nextAfterSeq() === null) return;
    const reqId = ++this.messagesRequestId;
    const afterSeq = append ? this.nextAfterSeq() : null;
    this.messagesLoading.set(true);
    this.messagesError.set(null);
    this.messagesErrorStatus.set(null);
    this.fetchMessages(roomId, afterSeq).subscribe({
      next: (resp) => {
        if (this.messagesRequestId !== reqId) return;
        const normalized = normalizeRoomMessagesResponse(resp);
        this.messages.update((current) =>
          append ? mergeMessagesBySeq(current, normalized.messages) : normalized.messages,
        );
        this.nextAfterSeq.set(normalized.next_after_seq);
        this.messagesLoading.set(false);
      },
      error: (err: unknown) => {
        if (this.messagesRequestId !== reqId) return;
        const described = describeHttpError(err, 'messages');
        this.messagesErrorStatus.set(described.status);
        this.messagesError.set(described.message);
        this.messagesLoading.set(false);
      },
    });
  }

  resetMessages(): void {
    this.messagesRequestId++;
    this.messages.set([]);
    this.nextAfterSeq.set(null);
    this.messagesError.set(null);
    this.messagesErrorStatus.set(null);
    this.messagesLoading.set(false);
    this.openRoomId.set(null);
  }

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
        if (this.globalRoomsRequestId !== reqId) return;
        const normalized = normalizeGlobalRoomListResponse(resp);
        this.globalRooms.set(normalized.rooms);
        this.globalRoomsTruncated.set(normalized.truncated === true);
        this.globalRoomsLoading.set(false);
      },
      error: (err: unknown) => {
        if (this.globalRoomsRequestId !== reqId) return;
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

  startPolling(): void {
    this.stopPolling();
    this.browserVisible = true;
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

  refreshRooms(): void {
    this.loadGlobalRooms(this.globalRoomsMode, this.globalRoomsScope);
  }

  refreshOpenRoom(): void {
    const roomId = this.openRoomId();
    if (!roomId) return;
    if (this.pollInFlight) return;
    const seq = this.maxLoadedSeq();
    this.pollInFlight = true;
    const token = ++this.pollSeq;
    this.fetchMessages(roomId, seq).subscribe({
      next: (resp) => {
        if (token !== this.pollSeq) {
          this.pollInFlight = false;
          return;
        }
        const normalized = normalizeRoomMessagesResponse(resp);
        this.messages.update((current) => mergeMessagesBySeq(current, normalized.messages));
        // cursor policy: do NOT mutate nextAfterSeq
        this.pollInFlight = false;
      },
      error: () => {
        if (token !== this.pollSeq) {
          this.pollInFlight = false;
          return;
        }
        this.stale.set(true);
        this.pollInFlight = false;
      },
    });
  }

  private maxLoadedSeq(): number {
    const msgs = this.messages();
    if (msgs.length === 0) return 0;
    return Math.max(...msgs.map((m) => m.seq));
  }
}

export const ROOM_MESSAGE_PAGE_LIMIT = 50;
export const ROOM_MESSAGE_MAX_LIMIT = 200;

function clampLimit(limit: number): number {
  if (!Number.isFinite(limit)) return ROOM_MESSAGE_PAGE_LIMIT;
  return Math.min(ROOM_MESSAGE_MAX_LIMIT, Math.max(1, Math.trunc(limit)));
}

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

function describeHttpError(
  err: unknown,
  subject: 'chatrooms' | 'messages',
): { status: number | null; message: string } {
  const raw = (err ?? {}) as { status?: unknown; message?: unknown; error?: unknown };
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
