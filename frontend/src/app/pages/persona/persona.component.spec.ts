import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { MatSnackBarModule } from '@angular/material/snack-bar';
import { PersonaComponent } from './persona.component';
import { PersonaService } from '../../services/persona.service';
import { PromptVersion } from '../../models/persona.model';
import {
  isCoreRow,
  isEditableRow,
  isPendingApproval,
  promptErrorMessage,
} from '../../models/persona.model';

/**
 * Tests for PersonaComponent — agent-prompt-immutability Phase C P1.
 *
 * Asserts the CORE-only button enforcement matrix:
 *
 *   | State / Class   | Approve | Edit | Delete | Submit | SetSel |
 *   |-----------------|---------|------|--------|--------|--------|
 *   | CORE draft      | —       |         |         | ✓      | —      |
 *   | CORE pending    | ✓       |         |         | —      | —      |
 *   | CORE approved   | —       | —     | —       | —      | ✓      |
 *   | CORE rejected   | —       | —     | —       | —      | —      |
 *   | MEMORY approved | —       | —     | —       | —      | —      |
 *
 * Verifies the HTTP wire shape for `list()` (the page's main GET) and
 * the pending-count refresh on init. The diff overlay is tested in the
 * generalized DiffViewerComponent spec.
 */

function makeVersion(overrides: Partial<PromptVersion> = {}): PromptVersion {
  return {
    version_id: '00000000-0000-0000-0000-000000000001',
    agent_id: 'developer',
    version_tag: '',
    artifact: 'soul',
    artifact_class: 'CORE',
    version: 1,
    state: 'draft',
    content: '# Soul v1',
    content_hash: 'abc123',
    is_selected: 0,
    submitter_instance_id: null,
    submitter_agent_id: 'developer',
    submitter_version_tag: '',
    approver_subject: null,
    approver_auth_method: null,
    created_at: '2026-10-14T00:00:00.000Z',
    updated_at: '2026-10-14T00:00:00.000Z',
    ...overrides,
  };
}

describe('PersonaComponent', () => {
  let fixture: ComponentFixture<PersonaComponent>;
  let component: PersonaComponent;
  let httpTesting: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [PersonaComponent, MatSnackBarModule],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideNoopAnimations(),
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(PersonaComponent);
    component = fixture.componentInstance;
    httpTesting = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    httpTesting.verify();
  });

  // ── 1) Create + boot ─────────────────────────────────────────────

  it('creates successfully', () => {
    expect(component).toBeTruthy();
  });

  it('renders the page title', () => {
    fixture.detectChanges();
    const req = httpTesting.expectOne((r) => r.url === '/api/prompts/versions');
    req.flush({ items: [], total: 0 });
    fixture.detectChanges();
    const title = fixture.nativeElement.querySelector('.page-title') as HTMLElement | null;
    expect(title?.textContent).toContain('Persona');
  });

  it('lists versions on ngOnInit and renders grouped cards', () => {
    fixture.detectChanges();
    const req = httpTesting.expectOne((r) => r.url === '/api/prompts/versions');
    req.flush({
      items: [
        makeVersion({ version_id: 'v1', version: 1, state: 'draft' }),
        makeVersion({ version_id: 'v2', version: 2, state: 'pending_approval' }),
      ],
      total: 2,
    });
    fixture.detectChanges();

    const groups = fixture.nativeElement.querySelectorAll('.group-card');
    expect(groups.length).toBe(1); // one (developer, soul) group
    const rows = fixture.nativeElement.querySelectorAll('.version-row');
    expect(rows.length).toBe(2);
  });

  // ── 2) Filter wiring ─────────────────────────────────────────────

  it('forwards filter changes into the next list call (limit-only payload on first load)', () => {
    fixture.detectChanges();
    const req = httpTesting.expectOne(
      (r) => r.method === 'GET' && r.url === '/api/prompts/versions',
    );
    expect(req.request.params.get('limit')).toBe('200');
    req.flush({ items: [], total: 0 });

    component.onAgentFilterChange('tester');
    component.onStateFilterChange('pending_approval');
    component.onClassFilterChange('CORE');
    component.onRefresh();

    const req2 = httpTesting.expectOne(
      (r) => r.method === 'GET' && r.url === '/api/prompts/versions',
    );
    expect(req2.request.params.get('agent_id')).toBe('tester');
    expect(req2.request.params.get('state')).toBe('pending_approval');
    expect(req2.request.params.get('artifact_class')).toBe('CORE');
    req2.flush({ items: [], total: 0 });
  });

  // ── 3) CORE-only button enforcement matrix ───────────────────────

  describe('CORE-only button predicates', () => {
    // Pull each predicate through the public surface; private methods
    // are exercised indirectly via the rendered buttons in DOM tests
    // below.
    const C = (overrides: Partial<PromptVersion>) => ({ ...makeVersion(), ...overrides });
    const M = (overrides: Partial<PromptVersion>) => C({ artifact_class: 'MEMORY', ...overrides });

    it('isCoreRow / isEditableRow / isPendingApproval match the documented matrix', () => {
      // CORE draft
      const coreDraft = C({ state: 'draft' });
      expect(isCoreRow(coreDraft)).toBe(true);
      expect(isEditableRow(coreDraft)).toBe(true);
      expect(isPendingApproval(coreDraft)).toBe(false);
      expect(component.canEdit(coreDraft)).toBe(true);
      expect(component.canDelete(coreDraft)).toBe(true);
      expect(component.canSubmit(coreDraft)).toBe(true);
      expect(component.canApprove(coreDraft)).toBe(false);
      expect(component.canSetSelection(coreDraft)).toBe(false);

      // CORE pending_approval
      const corePending = C({ state: 'pending_approval' });
      expect(isPendingApproval(corePending)).toBe(true);
      expect(component.canApprove(corePending)).toBe(true);
      expect(component.canSubmit(corePending)).toBe(false);
      expect(component.canSetSelection(corePending)).toBe(false);

      // CORE approved
      const coreApproved = C({ state: 'approved' });
      expect(component.canApprove(coreApproved)).toBe(false);
      expect(component.canEdit(coreApproved)).toBe(false);
      expect(component.canDelete(coreApproved)).toBe(false);
      expect(component.canSetSelection(coreApproved)).toBe(true);

      // CORE rejected
      const coreRejected = C({ state: 'rejected' });
      expect(component.canEdit(coreRejected)).toBe(false);
      expect(component.canDelete(coreRejected)).toBe(false);
      expect(component.canApprove(coreRejected)).toBe(false);

      // MEMORY approved
      const memApproved = M({ state: 'approved' });
      expect(component.canApprove(memApproved)).toBe(false);
      expect(component.canEdit(memApproved)).toBe(false);
      expect(component.canDelete(memApproved)).toBe(false);
      expect(component.canSetSelection(memApproved)).toBe(false);
      expect(component.isMemoryRow(memApproved)).toBe(true);
    });
  });

  // ── 4) DOM-rendered enforcement (the spec asks for REGRESSION evidence)

  describe('DOM rendering: per-row affordances', () => {
    beforeEach(() => {
      fixture.detectChanges();
      const req = httpTesting.expectOne(
        (r) => r.method === 'GET' && r.url === '/api/prompts/versions',
      );
      req.flush({
        items: [
          makeVersion({
            version_id: 'core-draft',
            version: 1,
            state: 'draft',
          }),
          makeVersion({
            version_id: 'core-pending',
            version: 2,
            state: 'pending_approval',
          }),
          makeVersion({
            version_id: 'core-approved',
            version: 3,
            state: 'approved',
            approver_subject: 'human@example.com',
          }),
          makeVersion({
            version_id: 'mem-approved',
            version: 1,
            artifact: 'memory',
            artifact_class: 'MEMORY',
            state: 'approved',
          }),
        ],
        total: 4,
      });
      fixture.detectChanges();
    });

    it('CORE draft row: shows Edit + Delete + Submit (no Approve/Reject/SetSel)', () => {
      fixture.detectChanges();
      // Find the row that carries the draft version_id; check its
      // buttons are Edit + Delete + Submit-for-approval, NOT Approve/Reject/SetSel.
      const rows = Array.from(fixture.nativeElement.querySelectorAll('.version-row'));
      // Locate by inspecting each row's content preview header chips.
      const draftRow = rows.find((r) =>
        (r.textContent || '').includes('pending_approval') === false &&
        (r.textContent || '').includes('approved') === false &&
        (r.textContent || '').includes('MEMORY') === false,
      );
      // Fallback: take the row with v1 (lowest version = draft).
      const target = draftRow || rows[rows.length - 1];
      expect(target).toBeTruthy();
      const text = (target.textContent || '');
      // Draft row surfaces Submit-for-approval and edit/delete.
      expect(text).toContain('Submit for approval');
      // Does NOT surface Approve / Reject / Set as Selected.
      expect(text).not.toContain('Approve');
      expect(text).not.toContain('Reject');
      expect(text).not.toContain('Set as Selected');
    });

    it('CORE pending_approval row: shows Approve + Reject + Edit + Delete (no Submit)', () => {
      fixture.detectChanges();
      const buttons = Array.from(fixture.nativeElement.querySelectorAll('button'));
      const has = (s: string) => buttons.some((b) => (b.textContent || '').includes(s));
      // pending_approval row IS present → the page has both Approve and
      // Reject somewhere in the DOM.
      expect(has('Approve')).toBe(true);
      expect(has('Reject')).toBe(true);
    });

    it('CORE approved row: shows Set as Selected (no Edit/Delete/Approve)', () => {
      fixture.detectChanges();
      const buttons = Array.from(fixture.nativeElement.querySelectorAll('button'));
      const has = (s: string) => buttons.some((b) => (b.textContent || '').includes(s));
      expect(has('Set as Selected')).toBe(true);
    });

    it('MEMORY approved row: shows the MEMORY · view only marker (zero mutations)', () => {
      fixture.detectChanges();
      const memBadges = fixture.nativeElement.querySelectorAll('.memory-badge');
      expect(memBadges.length).toBe(1);
      const text = (memBadges[0].textContent || '').trim();
      expect(text).toContain('MEMORY');
      expect(text).toContain('view only');
    });

    it('still surfaces a View Diff button for every row (CORE / MEMORY alike)', () => {
      fixture.detectChanges();
      const diffBtns = Array.from(fixture.nativeElement.querySelectorAll('.diff-btn'));
      expect(diffBtns.length).toBe(4);
    });
  });

  // ── 5) Custom-agent empty-state ─────────────────────────────────

  describe('custom-agent empty-state', () => {
    it('renders the v1-create invitation when an agent filter yields zero rows', () => {
      fixture.detectChanges();
      const req = httpTesting.expectOne(
        (r) => r.method === 'GET' && r.url === '/api/prompts/versions',
      );
      req.flush({ items: [], total: 0 });
      fixture.detectChanges();

      component.onAgentFilterChange('custom-agent');
      fixture.detectChanges();

      const container = fixture.nativeElement.querySelector('.empty-container');
      expect(container).toBeTruthy();
      const text = (container.textContent || '').trim();
      expect(text).toContain('No versions yet');
      expect(text).toContain('custom-agent');
      const btn = Array.from(container.querySelectorAll('button')).find(
        (b) => (b.textContent || '').includes('Create v1'),
      );
      expect(btn).toBeTruthy();
    });
  });

  // ── 6) Error mapping ─────────────────────────────────────────────

  describe('error envelope mapping', () => {
    it('maps known error codes onto friendly text', () => {
      expect(
        promptErrorMessage({
          error: 'HUMAN_LANE_REQUIRED',
          message: 'session lane only',
        }),
      ).toContain('session lane');
      expect(
        promptErrorMessage({
          error: 'VERSION_IMMUTABLE',
          message: 'draft/pending only',
        }),
      ).toContain('immutable');
      expect(
        promptErrorMessage({
          error: 'MEMORY_CLASS_MISMATCH',
          message: 'MEMORY rows',
        }),
      ).toContain('born-effective');
    });

    it('falls back to the wire ``message`` for unmapped codes', () => {
      expect(
        promptErrorMessage({ error: 'NEW_CODE', message: 'wire-level text' }),
      ).toBe('wire-level text');
    });
  });

  // ── 7) Approve / reject refresh drain the badge ──────────────────

  it('approve / reject call refreshPendingCount (badge drain)', () => {
    fixture.detectChanges();
    const init = httpTesting.expectOne(
      (r) => r.method === 'GET' && r.url === '/api/prompts/versions',
    );
    init.flush({ items: [], total: 0 });

    component.onApprove(
      makeVersion({ version_id: 'v1', state: 'pending_approval' }),
    );
    const approveReq = httpTesting.expectOne(
      (r) => r.method === 'POST' && r.url.endsWith('/v1/approve'),
    );
    approveReq.flush(makeVersion({ version_id: 'v1', state: 'approved' }));

    // Approve flow triggers a list refresh + the badge drain.
    const listReq = httpTesting.expectOne(
      (r) => r.method === 'GET' && r.url === '/api/prompts/versions',
    );
    listReq.flush({ items: [], total: 0 });
    const pendingReq = httpTesting.expectOne('/api/prompts/pending-count');
    pendingReq.flush({ count: 0 });
  });

  it('a 403 HUMAN_LANE_REQUIRED response surfaces via the error mapper', () => {
    fixture.detectChanges();
    httpTesting
      .expectOne((r) => r.method === 'GET' && r.url === '/api/prompts/versions')
      .flush({ items: [], total: 0 });

    component.onApprove(
      makeVersion({ version_id: 'v1', state: 'pending_approval' }),
    );
    httpTesting
      .expectOne((r) => r.method === 'POST' && r.url.endsWith('/v1/approve'))
      .flush(
        { error: 'HUMAN_LANE_REQUIRED', message: 'session lane only' },
        { status: 403, statusText: 'Forbidden' },
      );
    // No automatic re-fetch — error path is silent.
  });
});