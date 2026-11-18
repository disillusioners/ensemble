import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { MAT_DIALOG_DATA, MatDialogRef } from '@angular/material/dialog';
import { RoomDialogComponent } from './room-dialog.component';
import { RoomService } from '../../services/room.service';

/**
 * Behaviour spec for the P6 chatrooms modal.
 *
 * The flow under test: open → rooms load → pick a room → the
 * conversation renders → "load more" appends the next page. Plus the
 * honest failure states (404 / 403) and the empty-room case (a room with
 * zero messages is an EMPTY CONVERSATION, not an error).
 *
 * The HTTP layer is mocked at the wire (HttpTestingController) — the
 * backend endpoints are built in parallel by the backend lane, so the FE
 * gate is the mocked contract, not a live server.
 */
describe('RoomDialogComponent', () => {
  let fixture: ComponentFixture<RoomDialogComponent>;
  let component: RoomDialogComponent;
  let httpMock: HttpTestingController;
  const closeSpy = jest.fn();

  const ROOMS_URL = '/api/instances/inst-1/rooms';

  const ROOM_1 = {
    room_id: 'r-1',
    name: 'capturer',
    created_at: '2026-11-16T10:00:00+00:00',
    closed: false,
    member_count: 3,
    last_message_at: '2026-11-16T10:05:00+00:00',
    last_message_seq: 12,
  };

  const ROOM_2 = {
    room_id: 'r-2',
    name: 'editor',
    created_at: '2026-11-16T09:00:00+00:00',
    closed: true,
    member_count: 1,
    last_message_at: null,
    last_message_seq: null,
  };

  beforeEach(async () => {
    closeSpy.mockClear();
    await TestBed.configureTestingModule({
      imports: [RoomDialogComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: MAT_DIALOG_DATA, useValue: { instanceId: 'inst-1', instanceName: 'Tester' } },
        { provide: MatDialogRef, useValue: { close: closeSpy } },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(RoomDialogComponent);
    component = fixture.componentInstance;
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    httpMock.verify();
  });

  function text(): string {
    return (fixture.nativeElement as HTMLElement).textContent ?? '';
  }

  function roomItems(): HTMLElement[] {
    return Array.from(
      (fixture.nativeElement as HTMLElement).querySelectorAll<HTMLElement>('.room-item'),
    );
  }

  function messageItems(): HTMLElement[] {
    return Array.from(
      (fixture.nativeElement as HTMLElement).querySelectorAll<HTMLElement>('.room-message'),
    );
  }

  function clickRoom(index: number): void {
    roomItems()[index].click();
    fixture.detectChanges();
  }

  it('loads the rooms of the instance on open', () => {
    fixture.detectChanges();
    const req = httpMock.expectOne(ROOMS_URL);
    expect(req.request.method).toBe('GET');
    req.flush({ rooms: [ROOM_1, ROOM_2] });
    fixture.detectChanges();

    expect(roomItems().length).toBe(2);
    expect(text()).toContain('capturer');
    expect(text()).toContain('editor');
    // State + member count + last-message time are rendered.
    expect(text()).toContain('active');
    expect(text()).toContain('closed');
    expect(text()).toContain('3 members');
  });

  it('renders the empty state when the instance has no chatrooms', () => {
    fixture.detectChanges();
    httpMock.expectOne(ROOMS_URL).flush({ rooms: [] });
    fixture.detectChanges();

    expect(roomItems().length).toBe(0);
    expect(text()).toContain('No chatrooms for this instance yet.');
  });

  it('renders the conversation of the selected room', () => {
    fixture.detectChanges();
    httpMock.expectOne(ROOMS_URL).flush({ rooms: [ROOM_1] });
    fixture.detectChanges();

    clickRoom(0);
    const req = httpMock.expectOne((r) => r.url === '/api/rooms/r-1/messages');
    expect(req.request.params.has('after_seq')).toBe(false);
    req.flush({
      messages: [
        {
          seq: 1,
          instance_id: 'i-1',
          member_name: 'capturer',
          content: 'shot 1 captured',
          created_at: '2026-11-16T10:00:00+00:00',
          kind: 'message',
        },
        {
          seq: 2,
          instance_id: 'i-2',
          member_name: 'editor',
          content: 'on it',
          created_at: '2026-11-16T10:01:00+00:00',
          kind: 'message',
        },
      ],
      next_after_seq: null,
    });
    fixture.detectChanges();

    expect(messageItems().length).toBe(2);
    expect(text()).toContain('shot 1 captured');
    expect(text()).toContain('capturer');
    expect(text()).toContain('on it');
    // The conversation is rendered in ascending seq order.
    expect(messageItems()[0].textContent).toContain('shot 1 captured');
    expect(messageItems()[1].textContent).toContain('on it');
  });

  it('appends the next page on load-more', () => {
    fixture.detectChanges();
    httpMock.expectOne(ROOMS_URL).flush({ rooms: [ROOM_1] });
    fixture.detectChanges();

    clickRoom(0);
    httpMock.expectOne((r) => r.url === '/api/rooms/r-1/messages').flush({
      messages: [
        {
          seq: 1,
          instance_id: 'i-1',
          member_name: 'capturer',
          content: 'first',
          created_at: '2026-11-16T10:00:00+00:00',
          kind: 'message',
        },
      ],
      next_after_seq: 1,
    });
    fixture.detectChanges();

    const loadMore = (
      fixture.nativeElement as HTMLElement
    ).querySelector<HTMLElement>('.load-more-btn');
    expect(loadMore).toBeTruthy();
    loadMore!.click();
    fixture.detectChanges();

    const page2 = httpMock.expectOne((r) => r.url === '/api/rooms/r-1/messages');
    expect(page2.request.params.get('after_seq')).toBe('1');
    page2.flush({
      messages: [
        {
          seq: 2,
          instance_id: 'i-2',
          member_name: 'editor',
          content: 'second',
          created_at: '2026-11-16T10:02:00+00:00',
          kind: 'message',
        },
      ],
      next_after_seq: null,
    });
    fixture.detectChanges();

    expect(messageItems().length).toBe(2);
    expect(text()).toContain('second');
    // The cursor is exhausted → the load-more affordance disappears.
    expect(
      (fixture.nativeElement as HTMLElement).querySelector('.load-more-btn'),
    ).toBeNull();
  });

  it('renders an empty conversation (not an error) for a room with no messages', () => {
    fixture.detectChanges();
    httpMock.expectOne(ROOMS_URL).flush({ rooms: [ROOM_2] });
    fixture.detectChanges();

    clickRoom(0);
    httpMock
      .expectOne((r) => r.url === '/api/rooms/r-2/messages')
      .flush({ messages: [], next_after_seq: null });
    fixture.detectChanges();

    expect(messageItems().length).toBe(0);
    expect(text()).toContain('No messages in this chatroom yet.');
    expect(
      (fixture.nativeElement as HTMLElement).querySelector('.pane-state-error'),
    ).toBeNull();
  });

  it('renders an honest error state when the rooms request fails with 404', () => {
    fixture.detectChanges();
    httpMock
      .expectOne(ROOMS_URL)
      .flush({ detail: 'gone' }, { status: 404, statusText: 'Not Found' });
    fixture.detectChanges();

    expect(
      (fixture.nativeElement as HTMLElement).querySelector('.pane-state-error'),
    ).toBeTruthy();
    expect(text()).toContain('Not found');
    expect(roomItems().length).toBe(0);
  });

  it('renders an honest error state when the messages request fails with 403', () => {
    fixture.detectChanges();
    httpMock.expectOne(ROOMS_URL).flush({ rooms: [ROOM_1] });
    fixture.detectChanges();

    clickRoom(0);
    httpMock
      .expectOne((r) => r.url === '/api/rooms/r-1/messages')
      .flush({ detail: 'forbidden' }, { status: 403, statusText: 'Forbidden' });
    fixture.detectChanges();

    expect(
      (fixture.nativeElement as HTMLElement).querySelector('.pane-state-error'),
    ).toBeTruthy();
    expect(text()).toContain('Access denied');
    expect(messageItems().length).toBe(0);
  });

  it('drops a stale room list when a newer rooms request superseded it', () => {
    fixture.detectChanges();
    // The component's own load is request A; the explicit reload below is
    // request B — A's response lands LAST and must be dropped.
    // AM-2 — RE-POINTED (never weakened): the component declares
    // `providers: [RoomService]`, so the service it drives is the
    // COMPONENT-SCOPED one; the root singleton would be a different
    // instance. The guard this case proves is unchanged: a stale
    // response is still dropped.
    const roomService = fixture.debugElement.injector.get(RoomService);
    roomService.loadRooms('inst-1');

    const [stale, fresh] = httpMock.match(ROOMS_URL);
    expect(stale).toBeTruthy();
    expect(fresh).toBeTruthy();

    stale.flush({
      rooms: [
        {
          room_id: 'r-stale',
          name: 'stale-room',
          created_at: '',
          closed: false,
          member_count: 1,
          last_message_at: null,
          last_message_seq: null,
        },
      ],
    });
    fixture.detectChanges();
    expect(text()).not.toContain('stale-room');

    fresh.flush({ rooms: [] });
    fixture.detectChanges();
    expect(roomItems().length).toBe(0);
  });
});

// ─────────────────────────────────────────────────────────────────────────
// gc-fe-surfaces (P2) — the generalized room dialog: the nest-wide
// 'global' mode, the per-instance 'joined' mode, and the shipped
// 'instance' mode's regression guard.
//
// The shipped describe above is UNEDITED (except the AM-2 re-point);
// these are SIBLING describes so each one can mount the dialog with its
// OWN MAT_DIALOG_DATA (the mode is data-selected).
// ─────────────────────────────────────────────────────────────────────────

/** A nest-wide room row — the shape `GET /api/rooms` returns. */
const GLOBAL_ROOM_A = {
  room_id: 'r-a',
  name: 'room-a',
  created_at: '2026-11-16T10:00:00+00:00',
  closed: false,
  member_count: 3,
  last_message_at: '2026-11-16T10:05:00+00:00',
  last_message_seq: 12,
  tree_root_id: 't-1',
  owner_instance_id: 'o-1',
  member_instance_ids: ['o-1', 'i-2'],
};

const GLOBAL_ROOM_B = {
  room_id: 'r-b',
  name: 'room-b',
  created_at: '2026-11-16T09:00:00+00:00',
  closed: true,
  member_count: 1,
  last_message_at: null,
  last_message_seq: null,
  tree_root_id: 't-1',
  owner_instance_id: 'o-2',
  member_instance_ids: ['o-2'],
};

const MESSAGE_1 = {
  seq: 1,
  instance_id: 'i-1',
  member_name: 'capturer',
  content: 'shot 1 captured',
  created_at: '2026-11-16T10:00:00+00:00',
  kind: 'message',
};

function textOf(fixture: ComponentFixture<RoomDialogComponent>): string {
  return (fixture.nativeElement as HTMLElement).textContent ?? '';
}

function roomItemsOf(fixture: ComponentFixture<RoomDialogComponent>): HTMLElement[] {
  return Array.from(
    (fixture.nativeElement as HTMLElement).querySelectorAll<HTMLElement>('.room-item'),
  );
}

function messageItemsOf(fixture: ComponentFixture<RoomDialogComponent>): HTMLElement[] {
  return Array.from(
    (fixture.nativeElement as HTMLElement).querySelectorAll<HTMLElement>('.room-message'),
  );
}

function clickRoomIn(fixture: ComponentFixture<RoomDialogComponent>, index: number): void {
  roomItemsOf(fixture)[index].click();
  fixture.detectChanges();
}

describe('RoomDialogComponent — the shipped instance mode (regression guard)', () => {
  let fixture: ComponentFixture<RoomDialogComponent>;
  let httpMock: HttpTestingController;
  const closeSpy = jest.fn();
  const ROOMS_URL = '/api/instances/inst-1/rooms';
  // The payload object is MUTABLE on purpose: the defensive leg below
  // opens a second, scope-less instance-mode dialog.
  const dialogData: { mode?: string; instanceId?: string | null; instanceName?: string | null } = {
    instanceId: 'inst-1',
    instanceName: 'Tester',
  };

  beforeEach(async () => {
    jest.useFakeTimers();
    closeSpy.mockClear();
    dialogData.mode = undefined;
    dialogData.instanceId = 'inst-1';
    dialogData.instanceName = 'Tester';
    await TestBed.configureTestingModule({
      imports: [RoomDialogComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: MAT_DIALOG_DATA, useValue: dialogData },
        { provide: MatDialogRef, useValue: { close: closeSpy } },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(RoomDialogComponent);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    fixture.destroy();
    jest.useRealTimers();
    httpMock.verify();
  });

  it('(iii) issues exactly the tree-scoped rooms request, never /api/rooms, and starts NO polling', () => {
    fixture.detectChanges();

    const req = httpMock.expectOne(ROOMS_URL);
    expect(req.request.method).toBe('GET');
    req.flush({ rooms: [] });
    fixture.detectChanges();

    // The nest-wide route is NEVER used by the shipped mode.
    httpMock.expectNone((r) => r.url === '/api/rooms');
    // ... and no poll ever fires: the timer object does not exist in
    // this mode (Q4 — the shipped modal stays timer-free).
    jest.advanceTimersByTime(30_000);
    httpMock.expectNone((r) => r.url === '/api/rooms');
    httpMock.expectNone((r) => r.url.includes('/messages'));

    // A-10 — the defensive leg: an instance-mode payload WITHOUT an
    // instance id issues NO request at all (never one for `undefined`)
    // and shows an honest message instead.
    dialogData.instanceId = null;
    const orphan = TestBed.createComponent(RoomDialogComponent);
    orphan.detectChanges();
    httpMock.expectNone((r) => r.url.includes('/rooms'));
    expect(textOf(orphan)).toContain('No instance was supplied');
    orphan.destroy();
  });
});

describe('RoomDialogComponent — the nest-wide global mode', () => {
  let fixture: ComponentFixture<RoomDialogComponent>;
  let httpMock: HttpTestingController;
  const closeSpy = jest.fn();

  beforeEach(async () => {
    jest.useFakeTimers();
    closeSpy.mockClear();
    await TestBed.configureTestingModule({
      imports: [RoomDialogComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: MAT_DIALOG_DATA, useValue: { mode: 'global' } },
        { provide: MatDialogRef, useValue: { close: closeSpy } },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(RoomDialogComponent);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    fixture.destroy();
    jest.useRealTimers();
    httpMock.verify();
  });

  it('(i) GETs /api/rooms with no query, renders the nest-wide rows, and retries in the SAME mode', () => {
    fixture.detectChanges();

    const req = httpMock.expectOne((r) => r.url === '/api/rooms');
    expect(req.request.method).toBe('GET');
    expect(req.request.params.has('instance_id')).toBe(false);
    req.flush({ rooms: [GLOBAL_ROOM_A, GLOBAL_ROOM_B], truncated: false });
    fixture.detectChanges();

    expect(roomItemsOf(fixture).length).toBe(2);
    expect(textOf(fixture)).toContain('room-a');
    expect(textOf(fixture)).toContain('room-b');
    // The tree-scoped route is NEVER used by the nest-wide browser.
    httpMock.expectNone((r) => r.url.startsWith('/api/instances/'));

    // The retry path is MODE-DERIVED: a failed list re-issues /api/rooms,
    // never the tree-scoped route.
    const service = fixture.debugElement.injector.get(RoomService);
    service.refreshRooms();
    httpMock
      .expectOne((r) => r.url === '/api/rooms')
      .flush({ detail: 'boom' }, { status: 500, statusText: 'Server Error' });
    fixture.detectChanges();

    const retryBtn = (fixture.nativeElement as HTMLElement).querySelector<HTMLElement>(
      '.pane-retry-btn',
    );
    expect(retryBtn).toBeTruthy();
    retryBtn!.click();

    const retryReq = httpMock.expectOne((r) => r.url === '/api/rooms');
    expect(retryReq.request.params.has('instance_id')).toBe(false);
    retryReq.flush({ rooms: [], truncated: false });
    fixture.detectChanges();
  });

  it('(ii) uses the nest-wide empty copy (never the instance-scoped copy)', () => {
    fixture.detectChanges();
    httpMock.expectOne((r) => r.url === '/api/rooms').flush({ rooms: [], truncated: false });
    fixture.detectChanges();

    expect(roomItemsOf(fixture).length).toBe(0);
    expect(textOf(fixture)).toContain('No chatrooms in this nest yet.');
    expect(textOf(fixture)).not.toContain('No chatrooms for this instance yet.');
  });

  it('(iv) polls on the 10 s cadence, stops on destroy, and renders the honest stale copy when a tick fails', () => {
    fixture.detectChanges();
    httpMock
      .expectOne((r) => r.url === '/api/rooms')
      .flush({ rooms: [GLOBAL_ROOM_A], truncated: false });
    fixture.detectChanges();

    // Open a room so the tick has something to refresh.
    clickRoomIn(fixture, 0);
    httpMock
      .expectOne((r) => r.url === '/api/rooms/r-a/messages')
      .flush({ messages: [MESSAGE_1], next_after_seq: null });
    fixture.detectChanges();

    // One tick ⇒ exactly ONE refresh of the open room (the poll cursor is
    // the max loaded seq, never the load-more cursor).
    jest.advanceTimersByTime(10_000);
    const tick = httpMock.expectOne((r) => r.url === '/api/rooms/r-a/messages');
    expect(tick.request.params.get('after_seq')).toBe('1');
    tick.flush({ detail: 'boom' }, { status: 500, statusText: 'Server Error' });
    fixture.detectChanges();

    // N7 — the last-good conversation is retained AND the stale copy shows.
    expect(messageItemsOf(fixture).length).toBe(1);
    expect(textOf(fixture)).toContain('Refresh failed');
    expect(textOf(fixture)).toContain('last known messages');

    // The poll stops on destroy — no handle outlives the dialog.
    fixture.destroy();
    jest.advanceTimersByTime(30_000);
    httpMock.expectNone((r) => r.url.includes('/messages'));
  });

  it('(v) two open dialogs keep independent lanes (the component-scoped RoomService)', () => {
    const fixtureA = TestBed.createComponent(RoomDialogComponent);
    const fixtureB = TestBed.createComponent(RoomDialogComponent);

    fixtureA.detectChanges();
    fixtureB.detectChanges();

    const reqs = httpMock.match((r) => r.url === '/api/rooms');
    expect(reqs.length).toBe(2);

    // A's response lands LAST — it must not stomp B's list (and vice
    // versa): the two dialogs hold SEPARATE lane sets.
    reqs[1].flush({ rooms: [GLOBAL_ROOM_B], truncated: false });
    reqs[0].flush({ rooms: [GLOBAL_ROOM_A], truncated: false });
    fixtureA.detectChanges();
    fixtureB.detectChanges();

    expect(textOf(fixtureA)).toContain('room-a');
    expect(textOf(fixtureA)).not.toContain('room-b');
    expect(textOf(fixtureB)).toContain('room-b');
    expect(textOf(fixtureB)).not.toContain('room-a');
  });

  it('(vi) keeps the selection when the open room vanishes from the refreshed list, and says the room is gone', () => {
    fixture.detectChanges();
    httpMock
      .expectOne((r) => r.url === '/api/rooms')
      .flush({ rooms: [GLOBAL_ROOM_A], truncated: false });
    fixture.detectChanges();

    clickRoomIn(fixture, 0);
    httpMock
      .expectOne((r) => r.url === '/api/rooms/r-a/messages')
      .flush({ messages: [MESSAGE_1], next_after_seq: null });
    fixture.detectChanges();
    expect(messageItemsOf(fixture).length).toBe(1);

    // The list refreshes WITHOUT the open room.
    const service = fixture.debugElement.injector.get(RoomService);
    service.refreshRooms();
    httpMock.expectOne((r) => r.url === '/api/rooms').flush({ rooms: [], truncated: false });
    fixture.detectChanges();

    // The selection survives BY ID (the room is still named) and the pane
    // shows the honest absent state — never an empty conversation that
    // reads as live-but-silent (A-12).
    expect(textOf(fixture)).toContain('room-a');
    expect(textOf(fixture)).toContain('no longer available');
    expect(textOf(fixture)).not.toContain('No messages in this chatroom yet.');
  });

  it('(vii) renders the "more rooms not shown" notice only when the list is truncated', () => {
    fixture.detectChanges();
    httpMock
      .expectOne((r) => r.url === '/api/rooms')
      .flush({ rooms: [GLOBAL_ROOM_A, GLOBAL_ROOM_B], truncated: true });
    fixture.detectChanges();

    expect(textOf(fixture)).toContain('More rooms not shown');
    expect(textOf(fixture)).toContain('first 2 of the nest');

    // Not truncated ⇒ no notice at all.
    const service = fixture.debugElement.injector.get(RoomService);
    service.refreshRooms();
    httpMock
      .expectOne((r) => r.url === '/api/rooms')
      .flush({ rooms: [GLOBAL_ROOM_A], truncated: false });
    fixture.detectChanges();
    expect(textOf(fixture)).not.toContain('More rooms not shown');
  });
});

describe('RoomDialogComponent — the per-instance joined mode', () => {
  let fixture: ComponentFixture<RoomDialogComponent>;
  let httpMock: HttpTestingController;
  const closeSpy = jest.fn();

  const JOINED_ROOM = {
    ...GLOBAL_ROOM_A,
    room_id: 'r-joined',
    name: 'joined-room',
    member_instance_ids: ['inst-9', 'o-1'],
  };

  const NOT_JOINED_ROOM = {
    ...GLOBAL_ROOM_B,
    room_id: 'r-other',
    name: 'not-joined-room',
    member_instance_ids: ['o-2'],
  };

  beforeEach(async () => {
    jest.useFakeTimers();
    closeSpy.mockClear();
    await TestBed.configureTestingModule({
      imports: [RoomDialogComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        {
          provide: MAT_DIALOG_DATA,
          useValue: { mode: 'joined', instanceId: 'inst-9', instanceName: 'Tester' },
        },
        { provide: MatDialogRef, useValue: { close: closeSpy } },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(RoomDialogComponent);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    fixture.destroy();
    jest.useRealTimers();
    httpMock.verify();
  });

  it('(viii) GETs the ANCHORED list, shows only the JOINED rooms, and renders its own truncation notice', () => {
    fixture.detectChanges();

    // AM-9 — the joined popup is ANCHORED (the gate stays anchor-less).
    const req = httpMock.expectOne((r) => r.url === '/api/rooms');
    expect(req.request.params.get('instance_id')).toBe('inst-9');
    req.flush({ rooms: [JOINED_ROOM, NOT_JOINED_ROOM], truncated: true });
    fixture.detectChanges();

    // The FE narrows the anchored rows to the rooms THIS instance joined
    // (the server decides readability; the FE decides what to show).
    expect(roomItemsOf(fixture).length).toBe(1);
    expect(textOf(fixture)).toContain('joined-room');
    expect(textOf(fixture)).not.toContain('not-joined-room');

    // The joined popup carries its OWN truncation notice (N6). A-2: the
    // number is the POST-FILTER joined count (exactly 1 room is shown)
    // and the wording names THAT set — the cause stays named too.
    expect(textOf(fixture)).toContain('More rooms not shown');
    expect(textOf(fixture)).toContain('the readable list was truncated');
    expect(textOf(fixture)).toContain("showing 1 of this instance's joined chatrooms");
  });

  // ── A-2 (code council): the truncation notice is hoisted OUT of the
  //    non-empty branch, the empty copy is truncation-aware, and the
  //    notice counts the POST-FILTER joined rooms.

  it('(ix) A-2: an EMPTY joined view whose readable list was truncated does NOT read as "joined nothing" — and the notice still renders', () => {
    fixture.detectChanges();
    const req = httpMock.expectOne((r) => r.url === '/api/rooms');
    expect(req.request.params.get('instance_id')).toBe('inst-9');
    // Truncated, and the only row is NOT joined by this instance ⇒ the
    // joined view is empty even though the readable list was cut.
    req.flush({ rooms: [NOT_JOINED_ROOM], truncated: true });
    fixture.detectChanges();

    expect(roomItemsOf(fixture).length).toBe(0);
    // The empty copy is truncation-aware …
    expect(textOf(fixture)).toContain('the readable list was truncated');
    expect(textOf(fixture)).not.toContain('This instance has not joined any chatroom.');
    // … and the truncation notice renders in the EMPTY branch too (it is
    // hoisted out of the non-empty @else).
    expect(textOf(fixture)).toContain('More rooms not shown');
  });

  it('(x) A-2: an EMPTY joined view that is NOT truncated keeps the plain copy and shows NO notice', () => {
    fixture.detectChanges();
    const req = httpMock.expectOne((r) => r.url === '/api/rooms');
    req.flush({ rooms: [], truncated: false });
    fixture.detectChanges();

    expect(roomItemsOf(fixture).length).toBe(0);
    expect(textOf(fixture)).toContain('This instance has not joined any chatroom.');
    expect(textOf(fixture)).not.toContain('More rooms not shown');
    expect(textOf(fixture)).not.toContain('the readable list was truncated');
  });

  it('(xi) A-2: the truncation notice counts the POST-FILTER joined rooms (not the rows the server sent)', () => {
    fixture.detectChanges();
    const req = httpMock.expectOne((r) => r.url === '/api/rooms');
    const secondJoined = { ...JOINED_ROOM, room_id: 'r-joined-2', name: 'joined-room-2' };
    // Two joined + one not joined ⇒ the pane shows 2, so the copy must
    // say 2 (NOT the 3 rows the server sent).
    req.flush({ rooms: [JOINED_ROOM, NOT_JOINED_ROOM, secondJoined], truncated: true });
    fixture.detectChanges();

    expect(roomItemsOf(fixture).length).toBe(2);
    expect(textOf(fixture)).toContain("showing 2 of this instance's joined chatrooms");
    expect(textOf(fixture)).not.toContain('showing 3 of this instance');
  });
});
