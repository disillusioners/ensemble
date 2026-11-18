import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { RoomService } from './room.service';

/**
 * Contract spec for the P6 room read API client.
 *
 * Covers: the two GETs + their URL/param shape, the pinned response
 * shapes, extra-field tolerance, the fail-closed 404/403 paths, and the
 * increment-and-capture requestId guards on BOTH lanes (the rapid
 * instance-switch / room-switch race protection).
 */
describe('RoomService', () => {
  let service: RoomService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    service = TestBed.inject(RoomService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    httpMock.verify();
  });

  // ── the rooms lane ─────────────────────────────────────────────────────

  it('GETs /api/instances/{id}/rooms', () => {
    service.fetchRooms('inst-1').subscribe();
    const req = httpMock.expectOne('/api/instances/inst-1/rooms');
    expect(req.request.method).toBe('GET');
    req.flush({ rooms: [] });
  });

  it('publishes the rooms of the instance on success', () => {
    service.loadRooms('inst-1');
    httpMock.expectOne('/api/instances/inst-1/rooms').flush({
      rooms: [
        {
          room_id: 'r-1',
          name: 'capturer',
          created_at: '2026-11-16T10:00:00+00:00',
          closed: false,
          member_count: 3,
          last_message_at: '2026-11-16T10:05:00+00:00',
          last_message_seq: 12,
        },
      ],
    });
    expect(service.rooms().length).toBe(1);
    expect(service.rooms()[0].name).toBe('capturer');
    expect(service.rooms()[0].member_count).toBe(3);
    expect(service.roomsError()).toBeNull();
    expect(service.roomsLoading()).toBe(false);
  });

  it('tolerates extra fields on the room objects', () => {
    service.loadRooms('inst-1');
    httpMock.expectOne('/api/instances/inst-1/rooms').flush({
      rooms: [
        {
          room_id: 'r-1',
          name: 'capturer',
          created_at: '2026-11-16T10:00:00+00:00',
          closed: false,
          member_count: 2,
          last_message_at: null,
          last_message_seq: null,
          future_field: 'ignored',
          nested: { anything: true },
        },
      ],
      extra_envelope_field: 42,
    });
    expect(service.rooms().length).toBe(1);
    const room = service.rooms()[0];
    expect(room.room_id).toBe('r-1');
    // The known fields survive; the unknown ones are not carried into the
    // rendered view object.
    expect(Object.keys(room).sort()).toEqual([
      'closed',
      'created_at',
      'last_message_at',
      'last_message_seq',
      'member_count',
      'name',
      'room_id',
    ]);
  });

  it('fails closed with an honest error on 404', () => {
    service.loadRooms('inst-1');
    httpMock
      .expectOne('/api/instances/inst-1/rooms')
      .flush({ detail: 'not found' }, { status: 404, statusText: 'Not Found' });
    expect(service.rooms()).toEqual([]);
    expect(service.roomsErrorStatus()).toBe(404);
    expect(service.roomsError()).toContain('Not found');
    expect(service.roomsLoading()).toBe(false);
  });

  it('fails closed with an honest error on 403', () => {
    service.loadRooms('inst-1');
    httpMock
      .expectOne('/api/instances/inst-1/rooms')
      .flush({ detail: 'forbidden' }, { status: 403, statusText: 'Forbidden' });
    expect(service.rooms()).toEqual([]);
    expect(service.roomsErrorStatus()).toBe(403);
    expect(service.roomsError()).toContain('Access denied');
  });

  it('drops a stale rooms response when a newer request superseded it', () => {
    service.loadRooms('inst-old');
    service.loadRooms('inst-new');
    const requests = httpMock.match('/api/instances/inst-old/rooms');
    expect(requests.length).toBe(1);

    // The OLD response lands LAST — it must not overwrite the newer one.
    requests[0].flush({ rooms: [{ room_id: 'r-old', name: 'old' }] });
    const newer = httpMock.expectOne('/api/instances/inst-new/rooms');
    newer.flush({ rooms: [{ room_id: 'r-new', name: 'new' }] });

    expect(service.rooms().map((r) => r.room_id)).toEqual(['r-new']);
  });

  // ── the messages lane ───────────────────────────────────────────────────

  it('GETs /api/rooms/{id}/messages without after_seq on the first page', () => {
    service.fetchMessages('r-1').subscribe();
    const req = httpMock.expectOne((r) => r.url === '/api/rooms/r-1/messages');
    expect(req.request.params.get('limit')).toBe('50');
    expect(req.request.params.has('after_seq')).toBe(false);
    req.flush({ messages: [], next_after_seq: null });
  });

  it('GETs /api/rooms/{id}/messages with the after_seq cursor', () => {
    service.fetchMessages('r-1', 42).subscribe();
    const req = httpMock.expectOne((r) => r.url === '/api/rooms/r-1/messages');
    expect(req.request.params.get('after_seq')).toBe('42');
    expect(req.request.params.get('limit')).toBe('50');
    req.flush({ messages: [], next_after_seq: null });
  });

  it('clamps the limit into the contract window [1, 200]', () => {
    service.fetchMessages('r-1', null, 5000).subscribe();
    const tooBig = httpMock.expectOne((r) => r.url === '/api/rooms/r-1/messages');
    expect(tooBig.request.params.get('limit')).toBe('200');
    tooBig.flush({ messages: [], next_after_seq: null });

    service.fetchMessages('r-1', null, 0).subscribe();
    const tooSmall = httpMock.expectOne((r) => r.url === '/api/rooms/r-1/messages');
    expect(tooSmall.request.params.get('limit')).toBe('1');
    tooSmall.flush({ messages: [], next_after_seq: null });
  });

  it('publishes the conversation of the opened room', () => {
    service.openRoom('r-1');
    httpMock.expectOne((r) => r.url === '/api/rooms/r-1/messages').flush({
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
      next_after_seq: 2,
    });
    expect(service.messages().map((m) => m.seq)).toEqual([1, 2]);
    expect(service.messages()[0].member_name).toBe('capturer');
    expect(service.nextAfterSeq()).toBe(2);
    expect(service.messagesError()).toBeNull();
  });

  it('appends the next page on load-more and de-duplicates by seq', () => {
    service.openRoom('r-1');
    httpMock.expectOne((r) => r.url === '/api/rooms/r-1/messages').flush({
      messages: [
        { seq: 1, instance_id: 'i-1', member_name: 'a', content: 'one', created_at: '', kind: 'message' },
      ],
      next_after_seq: 1,
    });

    service.loadMessages('r-1', true);
    const page2 = httpMock.expectOne((r) => r.url === '/api/rooms/r-1/messages');
    expect(page2.request.params.get('after_seq')).toBe('1');
    page2.flush({
      messages: [
        // seq 1 repeats — the merge must not render it twice.
        { seq: 1, instance_id: 'i-1', member_name: 'a', content: 'one', created_at: '', kind: 'message' },
        { seq: 2, instance_id: 'i-2', member_name: 'b', content: 'two', created_at: '', kind: 'message' },
      ],
      next_after_seq: null,
    });

    expect(service.messages().map((m) => m.seq)).toEqual([1, 2]);
    expect(service.nextAfterSeq()).toBeNull();
  });

  it('does not request another page once the conversation is exhausted', () => {
    service.openRoom('r-1');
    httpMock.expectOne((r) => r.url === '/api/rooms/r-1/messages').flush({
      messages: [],
      next_after_seq: null,
    });
    service.loadMessages('r-1', true);
    httpMock.expectNone((r) => r.url === '/api/rooms/r-1/messages');
  });

  it('fails closed with an honest error on a 404 room', () => {
    service.openRoom('r-purged');
    httpMock
      .expectOne((r) => r.url === '/api/rooms/r-purged/messages')
      .flush({ detail: 'gone' }, { status: 404, statusText: 'Not Found' });
    expect(service.messages()).toEqual([]);
    expect(service.messagesErrorStatus()).toBe(404);
    expect(service.messagesError()).toContain('Not found');
  });

  it('drops a stale messages response when the room changed', () => {
    service.openRoom('r-old');
    service.openRoom('r-new');

    const stale = httpMock.expectOne((r) => r.url === '/api/rooms/r-old/messages');
    stale.flush({
      messages: [
        { seq: 1, instance_id: 'i-1', member_name: 'a', content: 'old', created_at: '', kind: 'message' },
      ],
      next_after_seq: null,
    });
    const fresh = httpMock.expectOne((r) => r.url === '/api/rooms/r-new/messages');
    fresh.flush({
      messages: [
        { seq: 1, instance_id: 'i-2', member_name: 'b', content: 'new', created_at: '', kind: 'message' },
      ],
      next_after_seq: null,
    });

    expect(service.messages().map((m) => m.content)).toEqual(['new']);
  });

  it('resetMessages clears the conversation and invalidates an in-flight request', () => {
    service.openRoom('r-1');
    service.resetMessages();
    expect(service.messages()).toEqual([]);
    expect(service.nextAfterSeq()).toBeNull();

    // The in-flight response lands AFTER the reset — it must be dropped.
    httpMock.expectOne((r) => r.url === '/api/rooms/r-1/messages').flush({
      messages: [
        { seq: 1, instance_id: 'i-1', member_name: 'a', content: 'late', created_at: '', kind: 'message' },
      ],
      next_after_seq: null,
    });
    expect(service.messages()).toEqual([]);
  });

  it('tolerates extra fields on the message objects', () => {
    service.openRoom('r-1');
    httpMock.expectOne((r) => r.url === '/api/rooms/r-1/messages').flush({
      messages: [
        {
          seq: 1,
          instance_id: 'i-1',
          member_name: 'capturer',
          content: 'shot 1 captured',
          created_at: '2026-11-16T10:00:00+00:00',
          kind: 'message',
          future_field: 'ignored',
          nested: { anything: true },
        },
      ],
      next_after_seq: null,
      extra_envelope_field: 42,
    });
    expect(service.messages().length).toBe(1);
    const message = service.messages()[0];
    expect(message.member_name).toBe('capturer');
    expect(Object.keys(message).sort()).toEqual([
      'content',
      'created_at',
      'instance_id',
      'kind',
      'member_name',
      'seq',
    ]);
  });

  it('drops a message row that carries no sequence number', () => {
    service.openRoom('r-1');
    httpMock.expectOne((r) => r.url === '/api/rooms/r-1/messages').flush({
      messages: [
        { seq: 1, instance_id: 'i-1', member_name: 'a', content: 'ok', created_at: '', kind: 'message' },
        { instance_id: 'i-2', member_name: 'b', content: 'no seq', created_at: '', kind: 'message' },
      ],
      next_after_seq: null,
    });
    expect(service.messages().map((m) => m.seq)).toEqual([1]);
  });


  // ── global lane (gc-fe-surfaces) ────────────────────────────────────

  it('fetchAllRooms global issues GET /api/rooms with no query', () => {
    service.fetchAllRooms(null).subscribe();
    const req = httpMock.expectOne('/api/rooms');
    expect(req.request.method).toBe('GET');
    expect(req.request.params.has('instance_id')).toBe(false);
    req.flush({ rooms: [], truncated: false });
  });

  it('fetchAllRooms joined issues GET /api/rooms?instance_id=<id>', () => {
    service.fetchAllRooms('inst-9').subscribe();
    const req = httpMock.expectOne((r) => r.url === '/api/rooms');
    expect(req.request.params.get('instance_id')).toBe('inst-9');
    req.flush({ rooms: [], truncated: false });
  });

  it('loadGlobalRooms global publishes rows + truncated', () => {
    service.loadGlobalRooms('global');
    httpMock.expectOne('/api/rooms').flush({
      rooms: [
        {
          room_id: 'r-1',
          name: 'room',
          tree_root_id: 't-1',
          owner_instance_id: 'o-1',
          state: 'active',
          member_count: 2,
          member_instance_ids: ['o-1', 'c-1'],
          last_message_at: null,
          last_message_seq: null,
          created_at: '2026-11-16T10:00:00+00:00',
        },
      ],
      truncated: true,
    });
    expect(service.globalRooms().length).toBe(1);
    expect(service.globalRooms()[0].member_instance_ids).toEqual(['o-1', 'c-1']);
    expect(service.globalRoomsTruncated()).toBe(true);
  });

  it('openRoom sets openRoomId and resetMessages clears it', () => {
    service.openRoom('r-1');
    httpMock.expectOne((r) => r.url === '/api/rooms/r-1/messages').flush({
      messages: [],
      next_after_seq: null,
    });
    expect(service.openRoomId()).toBe('r-1');
    service.resetMessages();
    expect(service.openRoomId()).toBeNull();
  });

  it('POLL_INTERVAL_MS is 10000', () => {
    expect(service.POLL_INTERVAL_MS).toBe(10_000);
  });

  it('loadGlobalRooms joined filters rows by member_instance_ids', () => {
    service.loadGlobalRooms('joined', 'inst-9');
    const req = httpMock.expectOne((r) => r.url === '/api/rooms');
    expect(req.request.params.get('instance_id')).toBe('inst-9');
    req.flush({
      rooms: [
        {
          room_id: 'r-joined',
          name: 'joined',
          tree_root_id: 't-1',
          owner_instance_id: 'o-1',
          state: 'active',
          member_count: 2,
          member_instance_ids: ['o-1', 'inst-9'],
          last_message_at: null,
          last_message_seq: null,
          created_at: '2026-11-16T10:00:00+00:00',
        },
        {
          room_id: 'r-other',
          name: 'other',
          tree_root_id: 't-1',
          owner_instance_id: 'inst-9',
          state: 'active',
          member_count: 1,
          member_instance_ids: ['o-2'],
          last_message_at: null,
          last_message_seq: null,
          created_at: '2026-11-16T10:01:00+00:00',
        },
      ],
      truncated: false,
    });
    // The SERVICE lane owns the joined filter (task 9(ii)) — every
    // consumer gets the same correct set.
    expect(service.globalRooms().map((r) => r.room_id)).toEqual(['r-joined']);
  });

  it('global mode does NOT filter by member_instance_ids', () => {
    service.loadGlobalRooms('global');
    httpMock.expectOne('/api/rooms').flush({
      rooms: [
        {
          room_id: 'r-any',
          name: 'any',
          tree_root_id: 't-1',
          owner_instance_id: 'o-1',
          state: 'active',
          member_count: 1,
          member_instance_ids: ['o-1'],
          last_message_at: null,
          last_message_seq: null,
          created_at: '2026-11-16T10:00:00+00:00',
        },
      ],
      truncated: false,
    });
    expect(service.globalRooms().map((r) => r.room_id)).toEqual(['r-any']);
  });

  it('A-12: a vanished open room keeps its selection and raises the absent flag', () => {
    service.openRoom('r-1');
    httpMock.expectOne((r) => r.url === '/api/rooms/r-1/messages').flush({
      messages: [],
      next_after_seq: null,
    });
    expect(service.openRoomId()).toBe('r-1');
    expect(service.openRoomAbsent()).toBe(false);

    // The refreshed list no longer contains r-1.
    service.loadGlobalRooms('global');
    httpMock.expectOne('/api/rooms').flush({
      rooms: [
        {
          room_id: 'r-2',
          name: 'still here',
          tree_root_id: 't-1',
          owner_instance_id: 'o-1',
          state: 'active',
          member_count: 1,
          member_instance_ids: ['o-1'],
          last_message_at: null,
          last_message_seq: null,
          created_at: '2026-11-16T10:00:00+00:00',
        },
      ],
      truncated: false,
    });
    // Selection preserved BY ID; the pane gets the honest absent state.
    expect(service.openRoomId()).toBe('r-1');
    expect(service.openRoomAbsent()).toBe(true);

    // It reappears ⇒ the flag clears.
    service.loadGlobalRooms('global');
    httpMock.expectOne('/api/rooms').flush({
      rooms: [
        {
          room_id: 'r-1',
          name: 'back',
          tree_root_id: 't-1',
          owner_instance_id: 'o-1',
          state: 'active',
          member_count: 1,
          member_instance_ids: ['o-1'],
          last_message_at: null,
          last_message_seq: null,
          created_at: '2026-11-16T10:00:00+00:00',
        },
      ],
      truncated: false,
    });
    expect(service.openRoomAbsent()).toBe(false);

    // A new selection resets the flag.
    service.openRoom('r-2');
    httpMock.expectOne((r) => r.url === '/api/rooms/r-2/messages').flush({
      messages: [],
      next_after_seq: null,
    });
    expect(service.openRoomAbsent()).toBe(false);
  });

  it('a successful poll clears stale; a failed poll sets it and polling keeps working', () => {
    service.openRoom('r-1');
    httpMock.expectOne((r) => r.url === '/api/rooms/r-1/messages').flush({
      messages: [],
      next_after_seq: null,
    });
    expect(service.stale()).toBe(false);

    service.refreshOpenRoom();
    httpMock
      .expectOne((r) => r.url === '/api/rooms/r-1/messages')
      .flush({ detail: 'boom' }, { status: 500, statusText: 'Server Error' });
    expect(service.stale()).toBe(true);

    // The in-flight flag was released (D17) — a second tick CAN issue.
    service.refreshOpenRoom();
    httpMock.expectOne((r) => r.url === '/api/rooms/r-1/messages').flush({
      messages: [{ seq: 1, instance_id: 'i-1', member_name: 'a', content: 'new', created_at: '', kind: 'message' }],
      next_after_seq: null,
    });
    expect(service.stale()).toBe(false);
    expect(service.messages().map((m) => m.seq)).toEqual([1]);
  });

  it('startPolling on a hidden tab issues no tick; a visible tab does', () => {
    jest.useFakeTimers();
    const hiddenSpy = jest.spyOn(document, 'hidden', 'get').mockReturnValue(true);
    try {
      service.openRoom('r-1');
      httpMock.expectOne((r) => r.url === '/api/rooms/r-1/messages').flush({
        messages: [],
        next_after_seq: null,
      });
      service.startPolling();
      jest.advanceTimersByTime(30_000);
      // Hidden tab ⇒ browserVisible initialized from document.hidden.
      httpMock.expectNone((r) => r.url === '/api/rooms/r-1/messages');
      service.stopPolling();
    } finally {
      hiddenSpy.mockRestore();
      jest.useRealTimers();
    }
  });

});
