import { Component, OnDestroy, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTooltipModule } from '@angular/material/tooltip';
import { RoomService } from '../../services/room.service';
import type { GlobalRoomSummary, RoomSummary } from '../../models/room.model';

/** The BIG-modal geometry (82vw × 82vh, capped 96vw/96vh) — ONE
 * definition: the chat component spreads it into ``dialog.open`` and
 * the spec asserts against it (byte-identical values in both lanes). */
export const ROOM_DIALOG_SIZE = {
  width: '82vw',
  height: '82vh',
  maxWidth: '96vw',
  maxHeight: '96vh',
} as const;

/**
 * The dialog's three list-source modes (D3.2).
 *
 * - ``instance`` — the SHIPPED mode: the chatrooms of one instance('s
 *   tree) (``GET /api/instances/{id}/rooms``). Unchanged — no timer,
 *   same copy, same requests.
 * - ``global`` — the NEST-WIDE browser: every chatroom in the nest
 *   (``GET /api/rooms``, anchor-less).
 * - ``joined`` — the per-instance popup: the chatrooms THAT INSTANCE
 *   joined (``GET /api/rooms?instance_id=<id>``, anchored).
 */
export type RoomDialogMode = 'instance' | 'global' | 'joined';

/**
 * Data payload for the chatrooms modal.
 *
 * - ``mode`` — optional: it DEFAULTS to ``'instance'`` when absent, so
 *   the shipped call site stays unedited (A-10).
 * - ``instanceId`` — required-by-contract in ``'instance'`` / ``'joined'``
 *   mode; ignored in ``'global'`` mode (the browser is nest-wide).
 * - ``instanceName`` — optional: shown under the title so the operator
 *   knows which instance the modal is about.
 */
export interface RoomDialogData {
  mode?: RoomDialogMode;
  instanceId?: string | null;
  instanceName?: string | null;
}

/**
 * Chatrooms modal — the P6 human observability surface for group chat
 * (architecture recommendation §13.1), generalized into the three-mode
 * room surface of the gc-fe-surfaces lane.
 *
 * Two levels inside ONE large modal (deliberately a modal, not a
 * tooltip/popover):
 *
 *   (a) the chatrooms of the current scope — the instance('s tree) in
 *       the shipped ``'instance'`` mode, the WHOLE NEST in the
 *       ``'global'`` mode, the instance's JOINED rooms in the
 *       ``'joined'`` mode;
 *   (b) the SELECTED room's conversation — messages ascending by `seq`
 *       (member name, content, time), paged via ``next_after_seq``.
 *
 * The modal rides the SAME room-read API as the agents' read tool (the
 * members ∪ owner-ancestry predicate is enforced server-side; the FE
 * surfaces 403/404 as honest error states and never fabricates rows).
 */
@Component({
  selector: 'app-room-dialog',
  standalone: true,
  imports: [
    CommonModule,
    MatDialogModule,
    MatButtonModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatTooltipModule,
  ],
  // D3.4 — every open gets its OWN RoomService instance: the nest-wide
  // browser and the per-instance popup can be open at the same time, and
  // the shared singleton lanes would stomp each other. (Disclosed: the
  // shipped mode's DI scope changed; its interaction behaviour did not.)
  providers: [RoomService],
  // Mode-scoped chrome hooks — the stylesheet carries ONLY rules under
  // these classes (the shipped rules stay untouched).
  host: {
    '[class.room-dialog--global]': 'isGlobalMode',
    '[class.room-dialog--joined]': 'isJoinedMode',
  },
  templateUrl: './room-dialog.component.html',
  styleUrl: './room-dialog.component.scss',
})
export class RoomDialogComponent implements OnInit, OnDestroy {
  protected readonly data = inject<RoomDialogData>(MAT_DIALOG_DATA);
  protected readonly dialogRef = inject<MatDialogRef<RoomDialogComponent>>(MatDialogRef);
  protected readonly roomService = inject(RoomService);

  // ── lanes (owned by the service — the requestId guards live there) ────
  protected readonly rooms = this.roomService.rooms;
  protected readonly roomsLoading = this.roomService.roomsLoading;
  protected readonly roomsError = this.roomService.roomsError;
  protected readonly messages = this.roomService.messages;
  protected readonly messagesLoading = this.roomService.messagesLoading;
  protected readonly messagesError = this.roomService.messagesError;
  protected readonly nextAfterSeq = this.roomService.nextAfterSeq;

  // ── the global lane (the nest-wide list — its OWN signals) ───────────
  protected readonly globalRooms = this.roomService.globalRooms;
  protected readonly globalRoomsTruncated = this.roomService.globalRoomsTruncated;
  protected readonly globalRoomsLoading = this.roomService.globalRoomsLoading;
  protected readonly globalRoomsError = this.roomService.globalRoomsError;
  protected readonly stale = this.roomService.stale;

  /**
   * The dialog's mode — ``'instance'`` when the payload omits it, so the
   * shipped call site resolves to the shipped mode with NO edit (A-10).
   */
  protected readonly mode: RoomDialogMode = this.data.mode ?? 'instance';
  protected readonly isInstanceMode: boolean = this.mode === 'instance';
  protected readonly isGlobalMode: boolean = this.mode === 'global';
  protected readonly isJoinedMode: boolean = this.mode === 'joined';

  /**
   * The rows the rooms pane lists.
   *
   * ``'global'`` ⇒ every chatroom in the nest. ``'joined'`` ⇒ the
   * anchored response narrowed to the rooms THAT INSTANCE joined — the
   * FE filter never decides readability (the server does), it only
   * narrows what the surface shows (AM-9).
   */
  protected readonly listedRooms = computed<GlobalRoomSummary[]>(() => {
    const rows = this.globalRooms();
    if (!this.isJoinedMode) return rows;
    const instanceId = this.data.instanceId;
    if (!instanceId) return [];
    return rows.filter((room) => (room.member_instance_ids || []).includes(instanceId));
  });

  /** The room whose conversation is open (null = nothing selected yet). */
  protected readonly selectedRoomId = signal<string | null>(null);

  /**
   * The selected room's NAME — kept so the pane can still name a room
   * that vanished from the refreshed list (A-12).
   */
  protected readonly selectedRoomLabel = signal<string | null>(null);

  /** The selected room's summary (null until a room is picked). */
  protected readonly selectedRoom = computed<RoomSummary | null>(() => {
    const rows = this.isInstanceMode ? this.rooms() : this.listedRooms();
    return rows.find((room) => room.room_id === this.selectedRoomId()) ?? null;
  });

  /**
   * The defensive no-request error state: ``'instance'`` / ``'joined'``
   * mode WITHOUT an ``instanceId`` issues NO request and shows an honest
   * message — never a request for ``undefined``.
   */
  protected readonly scopeError = signal<string | null>(null);

  /**
   * The rooms-pane empty copy for the non-instance modes.
   *
   * A-2 — the copy is TRUNCATION-AWARE. An empty JOINED view whose
   * readable list was truncated must NOT read as "this instance joined
   * nothing": the joined rooms it DID join may lie past the server's
   * cut. The global mode gets the same treatment.
   */
  protected get globalEmptyCopy(): string {
    if (this.isJoinedMode) {
      return this.globalRoomsTruncated()
        ? 'No joined chatrooms are shown — the readable list was truncated, so some may be missing.'
        : 'This instance has not joined any chatroom.';
    }
    return this.globalRoomsTruncated()
      ? 'No chatrooms are shown — the list was truncated.'
      : 'No chatrooms in this nest yet.';
  }

  /**
   * The honest truncation copy — the list is a PREFIX of the nest, never
   * the whole thing (N8/N6).
   *
   * A-2 — the number is the POST-FILTER count (the joined rooms this
   * pane actually shows) and the wording names THAT set, so the number
   * and the sentence describe the same thing. The cause stays named:
   * it was the READABLE list the server cut.
   */
  protected get truncationCopy(): string {
    if (this.isJoinedMode) {
      return (
        'More rooms not shown — the readable list was truncated; showing ' +
        `${this.listedRooms().length} of this instance's joined chatrooms.`
      );
    }
    return `More rooms not shown — showing the first ${this.listedRooms().length} of the nest.`;
  }

  ngOnInit(): void {
    // Drop any conversation left over from a previous opening of this
    // modal, then load the list for THIS mode.
    this.roomService.resetMessages();

    if (this.isGlobalMode) {
      // The nest-wide browser — ANCHOR-LESS (AM-9). It polls (D2.2).
      this.loadRoomsForMode();
      this.roomService.startPolling();
      return;
    }

    const instanceId = this.data.instanceId;
    if (!instanceId) {
      // Defensive: no scope ⇒ NO request at all, an honest message.
      this.scopeError.set('No instance was supplied for this chatroom view.');
      return;
    }

    if (this.isJoinedMode) {
      // The per-instance popup — ANCHORED (AM-9). It polls (D2.2).
      this.loadRoomsForMode();
      this.roomService.startPolling();
      return;
    }

    // The SHIPPED 'instance' mode — the same request as before, and
    // TIMER-FREE (Q4: the timer object never exists in this mode).
    this.loadRoomsForMode();
  }

  /**
   * The mode → fetch dispatch — the ONE place that decides WHICH room
   * list THIS mode reads (NO polling here: the poll is the caller's
   * concern).
   */
  private loadRoomsForMode(): void {
    if (this.isGlobalMode) {
      this.roomService.loadGlobalRooms('global', null);
      return;
    }
    const instanceId = this.data.instanceId;
    if (!instanceId) return;
    if (this.isJoinedMode) {
      this.roomService.loadGlobalRooms('joined', instanceId);
      return;
    }
    this.roomService.loadRooms(instanceId);
  }

  ngOnDestroy(): void {
    // Stop the poll FIRST (the interval must never outlive the dialog),
    // then clear the conversation when the modal closes so the next
    // opening never shows the previous room's messages.
    this.roomService.stopPolling();
    this.roomService.resetMessages();
  }

  /** Open a room's conversation (first page). */
  protected selectRoom(room: RoomSummary): void {
    this.selectedRoomId.set(room.room_id);
    this.selectedRoomLabel.set(room.name);
    this.roomService.openRoom(room.room_id);
  }

  /** Append the next page of the open conversation. */
  protected loadMore(): void {
    const roomId = this.selectedRoomId();
    if (!roomId) return;
    this.roomService.loadMessages(roomId, true);
  }

  /**
   * Re-fetch the rooms list after a failure — the SAME mode's fetch (a
   * nest-wide dialog must never silently revert to tree scope).
   */
  protected retryRooms(): void {
    this.loadRoomsForMode();
  }

  /** Re-fetch the open conversation's first page after a failure. */
  protected retryMessages(): void {
    const roomId = this.selectedRoomId();
    if (!roomId) return;
    this.roomService.loadMessages(roomId, false);
  }

  /** Close the modal. */
  protected close(): void {
    // The poll stops on the CLOSE path too, not only on destroy.
    this.roomService.stopPolling();
    this.dialogRef.close();
  }
}
