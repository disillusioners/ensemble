import { Component, OnDestroy, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTooltipModule } from '@angular/material/tooltip';
import { RoomService } from '../../services/room.service';
import type { RoomSummary } from '../../models/room.model';

/**
 * Data payload for the chatrooms modal.
 *
 * - ``instanceId`` — required: the instance whose tree's chatrooms are
 *   listed (the backend resolves the tree scope server-side).
 * - ``instanceName`` — optional: shown under the title so the operator
 *   knows which instance the modal is about.
 */
export interface RoomDialogData {
  instanceId: string;
  instanceName?: string | null;
}

/**
 * Chatrooms modal — the P6 human observability surface for group chat
 * (architecture recommendation §13.1).
 *
 * Two levels inside ONE large modal (deliberately a modal, not a
 * tooltip/popover):
 *
 *   (a) the chatrooms created by the instance('s tree) — name, state
 *       (active/closed), member count, last-message time;
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

  /** The room whose conversation is open (null = nothing selected yet). */
  protected readonly selectedRoomId = signal<string | null>(null);

  /** The selected room's summary (null until a room is picked). */
  protected readonly selectedRoom = computed<RoomSummary | null>(
    () => this.rooms().find((room) => room.room_id === this.selectedRoomId()) ?? null,
  );

  ngOnInit(): void {
    // Drop any conversation left over from a previous opening of this
    // modal, then load the rooms of this instance('s tree).
    this.roomService.resetMessages();
    this.roomService.loadRooms(this.data.instanceId);
  }

  ngOnDestroy(): void {
    // Clear the conversation when the modal closes so the next opening
    // never shows the previous room's messages.
    this.roomService.resetMessages();
  }

  /** Open a room's conversation (first page). */
  protected selectRoom(room: RoomSummary): void {
    this.selectedRoomId.set(room.room_id);
    this.roomService.openRoom(room.room_id);
  }

  /** Append the next page of the open conversation. */
  protected loadMore(): void {
    const roomId = this.selectedRoomId();
    if (!roomId) return;
    this.roomService.loadMessages(roomId, true);
  }

  /** Re-fetch the rooms list after a failure. */
  protected retryRooms(): void {
    this.roomService.loadRooms(this.data.instanceId);
  }

  /** Re-fetch the open conversation's first page after a failure. */
  protected retryMessages(): void {
    const roomId = this.selectedRoomId();
    if (!roomId) return;
    this.roomService.loadMessages(roomId, false);
  }

  /** Close the modal. */
  protected close(): void {
    this.dialogRef.close();
  }
}
