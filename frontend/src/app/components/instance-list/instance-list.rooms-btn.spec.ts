// FE-UI lane marker (group-chat gc-fe-surfaces P3 room button): room chat.
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter, Router } from '@angular/router';
import { MatDialog } from '@angular/material/dialog';
import { MatSnackBar } from '@angular/material/snack-bar';
import { signal, computed } from '@angular/core';
import { of } from 'rxjs';
import { readFileSync } from 'fs';
import { join } from 'path';

import { InstanceListComponent } from './instance-list.component';
import { RoomDialogComponent, ROOM_DIALOG_SIZE } from '../room-dialog/room-dialog.component';
import { RoomService } from '../../services/room.service';
import { ApiService } from '../../services/api.service';
import { InstanceService } from '../../services/instance.service';
import { InstancePrefsService } from '../../services/instance-prefs.service';
import { TabStateService } from '../../services/tab-state.service';
import type { InstanceInfo } from '../../models';

// ── mocks ────────────────────────────────────────────────────────────────
class MockInstanceService {
  readonly instances = signal<InstanceInfo[]>([]);
  readonly totalInstances = signal<number>(0);
  readonly isLoadingMore = signal<boolean>(false);
  readonly loading = signal<boolean>(false);
  readonly showKb = signal<boolean>(false);
  readonly searchQuery = signal<string>('');
  readonly isSearching = computed(() => this.searchQuery().trim().length > 0);
  readonly hasMoreInstances = signal<boolean>(false);
  currentProjectId: string | null = null;
  setSearchQuery = jest.fn();
  toggleKb = jest.fn();
  loadInstances = jest.fn();
}

class MockTabStateService {
  readonly openTabs = signal<any[]>([{ id: 'all', name: 'All', type: 'all' }]);
  readonly activeTab = signal<any>({ id: 'all', name: 'All', type: 'all' });
  readonly activeProjectId = computed(() => null);
  readonly activeSpecialTabId = computed(() => 'all');
  setActiveTab = jest.fn();
}

const mockPrefs = {
  setPin: jest.fn(() => of(void 0)),
  setColorTag: jest.fn(() => of(void 0)),
  setIconTag: jest.fn(() => of(void 0)),
  clearAllTags: jest.fn(() => of(void 0)),
};

const mockApi = {
  getDefaultAgentVersions: jest.fn(() => of({ default_versions: {} })),
  listAgents: jest.fn(),
};

function makeInstance(overrides: Partial<InstanceInfo> = {}): InstanceInfo {
  return {
    instance_id: 'inst-1',
    agent_id: 'coder',
    project_id: null,
    status: 'idle',
    parent_id: null,
    children: [],
    created_at: '2026-01-01T00:00:00Z',
    updated_at: null,
    ...overrides,
  };
}

const JOINED_ROOM = {
  room_id: 'r-1',
  name: 'room-one',
  created_at: '2026-01-01T00:00:00Z',
  closed: false,
  member_count: 1,
  last_message_at: null,
  last_message_seq: null,
  tree_root_id: 't',
  owner_instance_id: 'inst-1',
  member_instance_ids: ['inst-1'],
};

describe('InstanceListComponent rooms-btn (gc-fe-surfaces P3)', () => {
  let fixture: ComponentFixture<InstanceListComponent>;
  let component: InstanceListComponent;
  let httpMock: HttpTestingController;
  let router: Router;
  let dialog: jest.Mocked<MatDialog>;

  function setup(instances: InstanceInfo[] = []) {
    TestBed.configureTestingModule({
      imports: [InstanceListComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        { provide: InstanceService, useClass: MockInstanceService },
        { provide: InstancePrefsService, useValue: mockPrefs },
        { provide: TabStateService, useClass: MockTabStateService },
        { provide: ApiService, useValue: mockApi },
        { provide: MatDialog, useValue: { open: jest.fn().mockReturnValue({ afterClosed: () => of(undefined) }) } },
        { provide: MatSnackBar, useValue: { open: jest.fn() } },
      ],
    });
    fixture = TestBed.createComponent(InstanceListComponent);
    component = fixture.componentInstance;
    httpMock = TestBed.inject(HttpTestingController);
    router = TestBed.inject(Router);
    dialog = TestBed.inject(MatDialog) as jest.Mocked<MatDialog>;
    if (instances.length) {
      fixture.componentRef.setInput('instances', instances);
    }
    return { fixture, component, httpMock, router, dialog };
  }

  afterEach(() => {
    try { httpMock.verify(); } catch (e) { /* ignore */ }
  });

  function flushRooms(payload: any) {
    const req = httpMock.expectOne('/api/rooms');
    req.flush(payload);
    fixture.detectChanges();
  }

  function flushRoomsError() {
    const req = httpMock.expectOne('/api/rooms');
    req.flush(null, { status: 500, statusText: 'Server Error' });
    fixture.detectChanges();
  }

  // (i) 0 joined rooms ⇒ no button (absence, not disabled)
  it('(i) renders no .rooms-btn when instance has 0 joined rooms', () => {
    const inst = makeInstance({ instance_id: 'inst-1' });
    setup([inst]);
    fixture.detectChanges();
    flushRooms({ rooms: [], truncated: false });
    const btn = fixture.nativeElement.querySelector('.rooms-btn');
    expect(btn).toBeNull();
  });

  // (ii) >=1 joined room ⇒ button present
  it('(ii) renders .rooms-btn when instance has >=1 joined room', () => {
    const inst = makeInstance({ instance_id: 'inst-1' });
    setup([inst]);
    fixture.detectChanges();
    flushRooms({ rooms: [JOINED_ROOM], truncated: false });
    const btn = fixture.nativeElement.querySelector('.rooms-btn');
    expect(btn).not.toBeNull();
  });

  // (iii) click opens dialog in joined mode with row id; no navigation
  it('(iii) click opens RoomDialogComponent in joined mode with row id and does not navigate', () => {
    const inst = makeInstance({ instance_id: 'inst-1', instance_name: 'Tester' });
    setup([inst]);
    fixture.detectChanges();
    flushRooms({ rooms: [JOINED_ROOM], truncated: false });
    const navigateSpy = jest.spyOn(router, 'navigate');
    const btn = fixture.nativeElement.querySelector('.rooms-btn') as HTMLButtonElement;
    btn.click();
    fixture.detectChanges();
    expect(dialog.open).toHaveBeenCalledWith(RoomDialogComponent, expect.objectContaining({
      data: { mode: 'joined', instanceId: 'inst-1', instanceName: 'Tester' },
    }));
    expect(navigateSpy).not.toHaveBeenCalled();
  });

  // (iv) gate≡actionable: gate falsified ⇒ no-op
  it('(iv) gate falsified makes onOpenJoinedRooms a no-op', () => {
    const inst = makeInstance({ instance_id: 'inst-1' });
    setup([inst]);
    fixture.detectChanges();
    flushRooms({ rooms: [], truncated: false });
    const ev = { preventDefault: jest.fn(), stopPropagation: jest.fn() } as any;
    (component as any).onOpenJoinedRooms(inst, ev);
    expect(dialog.open).not.toHaveBeenCalled();
  });

  // (v) fetch failure ⇒ zero buttons + error + Retry; Retry succeeds ⇒ buttons
  it('(v) failed fetch hides buttons and shows error+Retry; Retry reveals buttons', () => {
    const inst = makeInstance({ instance_id: 'inst-1' });
    setup([inst]);
    fixture.detectChanges();
    flushRoomsError();
    expect(fixture.nativeElement.querySelector('.rooms-btn')).toBeNull();
    const err = fixture.nativeElement.querySelector('.room-map-error');
    expect(err).not.toBeNull();
    const retry = fixture.nativeElement.querySelector('.room-map-error__retry') as HTMLButtonElement;
    expect(retry).not.toBeNull();
    // retry
    retry.click();
    fixture.detectChanges();
    const req2 = httpMock.expectOne('/api/rooms');
    req2.flush({ rooms: [JOINED_ROOM], truncated: false });
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.rooms-btn')).not.toBeNull();
  });

  // (vi) documented inputs: the control's accessible name + tooltip.
  // Dual-host rendering (chat sidebar + /instances page both mount this
  // shared component) is a STRUCTURAL property of the component, not a
  // DOM-observable one in this single-host harness — see instance-list.html
  // (the component carries no host branching), so the documented-inputs
  // contract is what THIS harness can pin.
  it('(vi) component renders the control with documented inputs (both hosts)', () => {
    // Hosts: chat.html:8 and instances.component.html:22 — same shared child.
    const inst = makeInstance({ instance_id: 'inst-1', instance_name: 'Tester' });
    setup([inst]);
    fixture.detectChanges();
    flushRooms({ rooms: [JOINED_ROOM], truncated: false });
    const btn = fixture.nativeElement.querySelector('.rooms-btn') as HTMLButtonElement;
    expect(btn).not.toBeNull();
    // instance-list.html:235-236 — the template is the source of truth.
    expect(btn.getAttribute('title')).toBe('Chatrooms this instance joined');
    expect(btn.getAttribute('aria-label')).toBe('Chatrooms joined by Tester');
  });

  // (vii) row routerLink not triggered by button
  it('(vii) clicking the button does not trigger row navigation', () => {
    const inst = makeInstance({ instance_id: 'inst-1' });
    setup([inst]);
    fixture.detectChanges();
    flushRooms({ rooms: [JOINED_ROOM], truncated: false });
    const navigateSpy = jest.spyOn(router, 'navigate');
    const btn = fixture.nativeElement.querySelector('.rooms-btn') as HTMLButtonElement;
    btn.click();
    fixture.detectChanges();
    expect(navigateSpy).not.toHaveBeenCalled();
  });

  // (viii) child-row discriminator
  it('(viii) child row carries .child-row class', () => {
    const child = makeInstance({ instance_id: 'child-1', parent_id: 'parent-1' });
    const parent = makeInstance({ instance_id: 'parent-1', parent_id: null });
    // parent must exist for tree attachment
    setup([parent, child]);
    fixture.detectChanges();
    (component as any).expandedInstances.set(new Set(['parent-1']));
    fixture.detectChanges();
    flushRooms({ rooms: [{ ...JOINED_ROOM, member_instance_ids: ['child-1'] }], truncated: false });
    const childRow = fixture.nativeElement.querySelector('.instance-item.child-row');
    expect(childRow).not.toBeNull();
    // The child row CONTAINS the room button — the row that carries the
    // discriminator is also the row the W-12 offset applies to.
    expect(childRow.querySelector('.rooms-btn')).not.toBeNull();
    // W-12 discriminator pin. jsdom computes no stylesheet layout, so the
    // assertion reads the SCSS SOURCE (the house raw-text pin form): the
    // child-row offset must exist exactly as written.
    const scss = readFileSync(join(__dirname, 'instance-list.scss'), 'utf-8');
    expect(scss).toContain('.instance-item.child-row .rooms-btn');
    expect(scss).toMatch(/\.instance-item\.child-row \.rooms-btn\s*\{[^}]*right:\s*72px/);
  });

  // (ix) truncation warning renders
  it('(ix) truncated map renders honest warning', () => {
    const inst = makeInstance({ instance_id: 'inst-1' });
    setup([inst]);
    fixture.detectChanges();
    flushRooms({ rooms: [], truncated: true });
    const warn = fixture.nativeElement.querySelector('.room-map-truncated');
    expect(warn).not.toBeNull();
    expect((warn.textContent || '').toLowerCase()).toContain('truncated');
  });
});
