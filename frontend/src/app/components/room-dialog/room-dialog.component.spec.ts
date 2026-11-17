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
    const roomService = TestBed.inject(RoomService);
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
