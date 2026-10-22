// McpsGatewayComponent — the READ-ONLY operator page (Phase C).
//
// TestBed with a MOCK SERVICE (the house style — the precedent is
// `pages/resources/resources.component.spec.ts`): the real template
// renders against a mock service whose signals the test drives. The
// template bindings this page relies on (the three sections, the
// degraded states, the pager, the timestamp formatting) are therefore
// exercised for real — only the HTTP layer is stubbed.
//
// The negative assertion at the end matters most: v1 has NO write
// control anywhere in the DOM (no kill / lock-release / disconnect).

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { signal } from '@angular/core';
import { readFileSync } from 'fs';
import { join } from 'path';
import { McpsGatewayComponent } from './mcps-gateway.component';
import { McpsGatewayService } from './mcps-gateway.service';
import type {
  McpsGatewayAuditEntry,
  McpsGatewayError,
  McpsGatewayMachine,
  McpsGatewaySession,
} from '../../models';

// ── The mock service ───────────────────────────────────────────────────

class MockMcpsGatewayService {
  readonly machines = signal<McpsGatewayMachine[]>([]);
  readonly sessions = signal<McpsGatewaySession[]>([]);
  readonly audit = signal<McpsGatewayAuditEntry[]>([]);
  readonly loading = signal(false);
  readonly error = signal<McpsGatewayError | null>(null);
  readonly gatewayEnabled = signal(true);
  readonly auditOffset = signal(0);
  readonly auditLimit = signal(50);
  readonly canNextAuditPage = signal(false);
  readonly canPrevAuditPage = signal(false);
  readonly auditPageNumber = signal(1);
  readonly lastRefreshedAt = signal<Date | null>(null);

  refreshAll = jest.fn();
  nextAuditPage = jest.fn();
  prevAuditPage = jest.fn();
}

// ── Fixtures ───────────────────────────────────────────────────────────

const MACHINE: McpsGatewayMachine = {
  machine_id: 'mac-studio-01',
  presence: 'online',
  online: true,
  connected: true,
  last_seen_at: '2026-10-22T07:30:52.512000+00:00',
  session_id: 'sess-9f2c1a',
  agent_id: 'leader',
  busy: true,
};

const SESSION: McpsGatewaySession = {
  session_id: 'sess-9f2c1a',
  machine_id: 'mac-studio-01',
  agent_id: 'leader',
  instance_id: 'inst-42',
  state: 'live',
  machine_locked: true,
  started_at: '2026-10-22T07:20:04.512000+00:00',
  last_activity_at: '2026-10-22T07:20:04.512000+00:00',
  expires_at: '2026-10-22T08:20:04.512000+00:00',
  tools: ['computer_click', 'computer_type'],
};

const AUDIT_ENTRY: McpsGatewayAuditEntry = {
  timestamp: '2026-10-22T07:31:04.512000+00:00',
  agent_id: 'leader',
  instance_id: 'inst-42',
  machine_id: 'mac-studio-01',
  tool: 'computer_click',
  outcome: 'ok',
  session_id: 'sess-9f2c1a',
  error_code: null,
  args_hash: 'b1946ac92492d2347c6235b4d2611184b0b6f5b8a9f0e1d2c3b4a5968778695a4b',
};

// ── Suite ──────────────────────────────────────────────────────────────

describe('McpsGatewayComponent', () => {
  let fixture: ComponentFixture<McpsGatewayComponent>;
  let mock: MockMcpsGatewayService;

  /**
   * Build the component against a FRESH mock service.
   *
   * The mock is returned so a test can drive its signals AFTER the
   * component exists (the signals are read by the template on every
   * change-detection pass).
   */
  async function build(): Promise<MockMcpsGatewayService> {
    const mockService = new MockMcpsGatewayService();
    await TestBed.configureTestingModule({
      imports: [McpsGatewayComponent],
      providers: [
        provideNoopAnimations(),
        { provide: McpsGatewayService, useValue: mockService },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(McpsGatewayComponent);
    fixture.detectChanges();
    mock = mockService;
    return mockService;
  }

  function text(): string {
    return (fixture.nativeElement as HTMLElement).textContent ?? '';
  }

  afterEach(() => {
    jest.useRealTimers();
  });

  // ── (a) the three sections ───────────────────────────────────────────

  it('renders the three section headings', async () => {
    await build();

    const headings = fixture.debugElement
      .queryAll(By.css('h2'))
      .map((h) => (h.nativeElement.textContent ?? '').trim());
    expect(headings).toEqual(['Machines', 'Sessions', 'Audit']);
  });

  it('renders the page title and the read-only note', async () => {
    await build();

    expect(
      (fixture.nativeElement as HTMLElement).querySelector('h1')?.textContent?.trim(),
    ).toBe('Remote Hands / MCP Gateway');
    expect(text()).toContain('read-only v1');
  });

  // ── (b) the rows ─────────────────────────────────────────────────────

  it('renders a machine row with its presence badge and active session', async () => {
    await build();
    mock.machines.set([MACHINE]);
    fixture.detectChanges();

    const row = fixture.debugElement.query(By.css('[data-machine-id="mac-studio-01"]'));
    expect(row).not.toBeNull();
    const rowText = (row.nativeElement as HTMLElement).textContent ?? '';
    expect(rowText).toContain('mac-studio-01');
    expect(rowText).toContain('online');
    expect(rowText).toContain('sess-9f2c1a');
    expect(rowText).toContain('leader');
    expect(rowText).toContain('busy');
  });

  it('renders an em dash for a null last_seen_at', async () => {
    await build();
    mock.machines.set([
      {
        ...MACHINE,
        machine_id: 'mac-mini-02',
        last_seen_at: null,
        session_id: null,
        agent_id: null,
      },
    ]);
    fixture.detectChanges();

    const row = fixture.debugElement.query(By.css('[data-machine-id="mac-mini-02"]'));
    const cells = row.queryAll(By.css('td')).map((td) => (td.nativeElement.textContent ?? '').trim());
    expect(cells[4]).toBe('—');
    expect(cells[5]).toContain('—');
  });

  it('renders a session row with the lock indicator and the tool chips', async () => {
    await build();
    mock.sessions.set([SESSION]);
    fixture.detectChanges();

    const row = fixture.debugElement.query(By.css('[data-session-id="sess-9f2c1a"]'));
    expect(row).not.toBeNull();
    const rowText = (row.nativeElement as HTMLElement).textContent ?? '';
    expect(rowText).toContain('sess-9f2c1a');
    expect(rowText).toContain('leader');
    expect(rowText).toContain('inst-42');
    expect(rowText).toContain('live');
    expect(rowText).toContain('locked');
    expect(rowText).toContain('computer_click');
    expect(rowText).toContain('computer_type');
  });

  it('renders the audit rows in the order the service holds them (no re-sort)', async () => {
    await build();
    mock.audit.set([
      { ...AUDIT_ENTRY, tool: 'computer_click', timestamp: '2026-10-22T07:31:04.512000+00:00' },
      { ...AUDIT_ENTRY, tool: 'computer_type', timestamp: '2026-10-22T07:30:04.512000+00:00' },
    ]);
    fixture.detectChanges();

    const cells = fixture.debugElement
      .queryAll(By.css('[data-test="audit-table"] tbody tr'))
      .map((row) => (row.nativeElement.textContent ?? '').trim());
    expect(cells.length).toBe(2);
    expect(cells[0]).toContain('computer_click');
    expect(cells[1]).toContain('computer_type');
  });

  it('truncates the args hash in the cell and keeps the full value in the title', async () => {
    await build();
    mock.audit.set([AUDIT_ENTRY]);
    fixture.detectChanges();

    const cell = fixture.debugElement.query(
      By.css('[data-test="audit-table"] tbody tr td:last-child'),
    );
    expect((cell.nativeElement as HTMLElement).textContent?.trim()).toBe('b1946ac92492…');
    expect((cell.nativeElement as HTMLElement).getAttribute('title')).toBe(AUDIT_ENTRY.args_hash);
  });

  // ── (c) the timestamp formatting ─────────────────────────────────────

  it('formats the ISO-8601 stamps as readable UTC (the 6-digit fraction included)', async () => {
    await build();
    mock.machines.set([MACHINE]);
    mock.sessions.set([SESSION]);
    mock.audit.set([AUDIT_ENTRY]);
    fixture.detectChanges();

    expect(text()).toContain('2026-10-22 07:30:52 UTC');
    expect(text()).toContain('2026-10-22 07:20:04 UTC');
    expect(text()).toContain('2026-10-22 08:20:04 UTC');
    expect(text()).toContain('2026-10-22 07:31:04 UTC');
  });

  it('never renders "Invalid Date" for a malformed stamp', async () => {
    await build();
    mock.machines.set([{ ...MACHINE, last_seen_at: 'not-a-timestamp' }]);
    fixture.detectChanges();

    expect(text()).not.toContain('Invalid Date');
  });

  // ── (d) the degraded states ──────────────────────────────────────────

  it('renders the gateway-disabled banner and the disabled empty state', async () => {
    await build();
    mock.gatewayEnabled.set(false);
    fixture.detectChanges();

    const banner = fixture.debugElement.query(By.css('[data-test="gateway-disabled-banner"]'));
    expect(banner).not.toBeNull();
    const bannerText = (banner.nativeElement as HTMLElement).textContent ?? '';
    expect(bannerText).toContain('MCPS_GATEWAY_ENABLED=1');
    expect(bannerText).toContain('docs/mcps-gateway.md');

    const empty = fixture.debugElement.query(By.css('[data-test="machines-empty"]'));
    expect((empty.nativeElement as HTMLElement).textContent).toContain(
      'The remote-hands gateway is disabled',
    );
  });

  it('renders the plain empty state when the gateway is on but nothing is enrolled', async () => {
    await build();

    const empty = fixture.debugElement.query(By.css('[data-test="machines-empty"]'));
    expect((empty.nativeElement as HTMLElement).textContent).toContain('No machines to show yet.');
  });

  it('renders the error banner for the 503 gateway_unavailable failure', async () => {
    await build();
    mock.error.set({
      code: 'gateway_unavailable',
      message: 'The remote-hands gateway service is not initialized.',
      status: 503,
      source: 'machines',
    });
    fixture.detectChanges();

    const banner = fixture.debugElement.query(By.css('[data-test="error-banner"]'));
    expect(banner).not.toBeNull();
    const bannerText = (banner.nativeElement as HTMLElement).textContent ?? '';
    expect(bannerText).toContain('gateway_unavailable');
    expect(bannerText).toContain('not initialized');
  });

  it('renders the SCOPE_INSUFFICIENT message for a 403', async () => {
    await build();
    mock.error.set({
      code: 'SCOPE_INSUFFICIENT',
      message: 'credential lacks mcp:read',
      status: 403,
      source: 'audit',
    });
    fixture.detectChanges();

    expect(text()).toContain('SCOPE_INSUFFICIENT');
    expect(text()).toContain('credential lacks mcp:read');
  });

  // ── (e) the pager ────────────────────────────────────────────────────

  it('disables Previous on the first page and enables Next only on a full page', async () => {
    await build();
    mock.canNextAuditPage.set(false);
    mock.canPrevAuditPage.set(false);
    fixture.detectChanges();

    const prev = fixture.debugElement.query(By.css('[data-test="audit-prev"]'));
    const next = fixture.debugElement.query(By.css('[data-test="audit-next"]'));
    expect((prev.nativeElement as HTMLButtonElement).disabled).toBe(true);
    expect((next.nativeElement as HTMLButtonElement).disabled).toBe(true);

    mock.canNextAuditPage.set(true);
    mock.canPrevAuditPage.set(true);
    fixture.detectChanges();
    expect((prev.nativeElement as HTMLButtonElement).disabled).toBe(false);
    expect((next.nativeElement as HTMLButtonElement).disabled).toBe(false);
  });

  it('shows the page number and the page size', async () => {
    await build();
    mock.auditPageNumber.set(3);
    fixture.detectChanges();

    const label = fixture.debugElement.query(By.css('[data-test="audit-page-label"]'));
    expect((label.nativeElement as HTMLElement).textContent?.trim()).toBe('page 3 · 50 per page');
  });

  it('delegates the pager to the service', async () => {
    await build();
    // A disabled button fires no click — enable both pager buttons first.
    mock.canNextAuditPage.set(true);
    mock.canPrevAuditPage.set(true);
    fixture.detectChanges();

    fixture.debugElement.query(By.css('[data-test="audit-next"]')).nativeElement.click();
    fixture.debugElement.query(By.css('[data-test="audit-prev"]')).nativeElement.click();

    expect(mock.nextAuditPage).toHaveBeenCalledTimes(1);
    expect(mock.prevAuditPage).toHaveBeenCalledTimes(1);
  });

  // ── (f) the polling + the manual refresh ─────────────────────────────

  it('refreshes once on init', async () => {
    await build();

    expect(mock.refreshAll).toHaveBeenCalledTimes(1);
  });

  it('polls every POLL_INTERVAL_MS and stops on destroy', async () => {
    jest.useFakeTimers();
    await build();
    expect(mock.refreshAll).toHaveBeenCalledTimes(1);

    jest.advanceTimersByTime(McpsGatewayService.POLL_INTERVAL_MS);
    expect(mock.refreshAll).toHaveBeenCalledTimes(2);

    jest.advanceTimersByTime(McpsGatewayService.POLL_INTERVAL_MS * 2);
    expect(mock.refreshAll).toHaveBeenCalledTimes(4);

    fixture.destroy();
    jest.advanceTimersByTime(McpsGatewayService.POLL_INTERVAL_MS * 3);
    expect(mock.refreshAll).toHaveBeenCalledTimes(4);
  });

  it('pins the poll interval at 10 s', () => {
    expect(McpsGatewayService.POLL_INTERVAL_MS).toBe(10000);
  });

  it('the manual refresh button triggers an immediate refreshAll()', async () => {
    await build();
    expect(mock.refreshAll).toHaveBeenCalledTimes(1);

    fixture.debugElement
      .query(By.css('[data-test="refresh-button"]'))
      .nativeElement.click();
    expect(mock.refreshAll).toHaveBeenCalledTimes(2);
  });

  // ── (g) the read-only guarantee (the negative assertion) ─────────────

  it('exposes NO write / kill / lock-release control in the DOM', async () => {
    await build();
    mock.machines.set([MACHINE]);
    mock.sessions.set([SESSION]);
    mock.audit.set([AUDIT_ENTRY]);
    fixture.detectChanges();

    const buttons = fixture.debugElement.queryAll(By.css('button'));
    expect(buttons.length).toBeGreaterThan(0);
    for (const button of buttons) {
      const element = button.nativeElement as HTMLButtonElement;
      const label = `${element.textContent ?? ''} ${element.getAttribute('aria-label') ?? ''}`;
      expect(label).not.toMatch(/kill|release|revoke|terminate|disconnect|unlock|delete/i);
    }

    // The only buttons are the refresh + the two pager buttons.
    const tests = buttons.map((button) => button.attributes['data-test'] ?? '');
    expect(tests).toEqual(['refresh-button', 'audit-prev', 'audit-next']);
  });

  it('issues no write verb from the service (the read surface is GET-only)', () => {
    const serviceSrc = readFileSync(join(__dirname, 'mcps-gateway.service.ts'), 'utf-8');
    expect(serviceSrc).not.toMatch(/\.(post|put|delete|patch)\(/);
  });
});
