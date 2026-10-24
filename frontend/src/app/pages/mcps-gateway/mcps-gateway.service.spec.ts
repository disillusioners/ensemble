// McpsGatewayService — the Remote Hands / MCP Gateway READ surface.
//
// TestBed + HttpTestingController (the house style — the precedent is
// `frontend/src/app/services/snapshot.service.spec.ts`): the REAL
// service runs against a mock transport, so the URL, the verb, the
// query params, the payload shape and the error decoding are all
// exercised against the actual production code (no logic-mirror drift
// risk).
//
// The three lanes are read-only: every request must be a GET.

import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptorsFromDi } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { McpsGatewayService } from './mcps-gateway.service';
import type {
  McpsGatewayAuditResponse,
  McpsGatewayMachinesResponse,
  McpsGatewaySessionsResponse,
} from '../../models';

// ── Fixtures ───────────────────────────────────────────────────────────

const MACHINE_ROSTER: McpsGatewayMachinesResponse = {
  machines: [
    {
      machine_id: 'mac-studio-01',
      presence: 'online',
      online: true,
      connected: true,
      last_seen_at: '2026-10-22T07:30:52.512000+00:00',
      session_id: 'sess-9f2c1a',
      agent_id: 'leader',
      busy: true,
    },
  ],
  count: 1,
  gateway_enabled: true,
};

const SESSION_LIST: McpsGatewaySessionsResponse = {
  sessions: [
    {
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
    },
  ],
  count: 1,
  gateway_enabled: true,
};

function makeAuditPage(count: number): McpsGatewayAuditResponse {
  return {
    audit: Array.from({ length: count }, (_unused, index) => ({
      timestamp: `2026-10-22T07:${String(30 + Math.floor(index / 60)).padStart(2, '0')}:${String(index % 60).padStart(2, '0')}.512000+00:00`,
      agent_id: 'leader',
      instance_id: 'inst-42',
      machine_id: 'mac-studio-01',
      tool: 'computer_click',
      outcome: 'ok',
      session_id: 'sess-9f2c1a',
      error_code: null,
      args_hash: 'b1946ac92492d2347c6235b4d2611184b0b6f5b8a9f0e1d2c3b4a5968778695a4b',
    })),
    count,
    limit: 50,
    offset: 0,
    gateway_enabled: true,
  };
}

// ── Suite ──────────────────────────────────────────────────────────────

describe('McpsGatewayService', () => {
  let service: McpsGatewayService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(withInterceptorsFromDi()),
        provideHttpClientTesting(),
      ],
    });
    service = TestBed.inject(McpsGatewayService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    httpMock.verify();
  });

  // ── (a) the URLs + the verb ──────────────────────────────────────────

  it('loadMachines() GETs /api/mcps-gateway/machines and fills the signal', () => {
    service.loadMachines();

    const req = httpMock.expectOne(McpsGatewayService.MACHINES_URL);
    expect(req.request.method).toBe('GET');
    req.flush(MACHINE_ROSTER);

    expect(service.machines().length).toBe(1);
    expect(service.machines()[0].machine_id).toBe('mac-studio-01');
    expect(service.gatewayEnabled()).toBe(true);
  });

  it('loadSessions() GETs /api/mcps-gateway/sessions and fills the signal', () => {
    service.loadSessions();

    const req = httpMock.expectOne(McpsGatewayService.SESSIONS_URL);
    expect(req.request.method).toBe('GET');
    req.flush(SESSION_LIST);

    expect(service.sessions().length).toBe(1);
    expect(service.sessions()[0].session_id).toBe('sess-9f2c1a');
    expect(service.sessions()[0].machine_locked).toBe(true);
  });

  it('loadAudit(offset) GETs /api/mcps-gateway/audit with the limit + offset params', () => {
    service.loadAudit(50);

    const req = httpMock.expectOne((r) => r.url === McpsGatewayService.AUDIT_URL);
    expect(req.request.method).toBe('GET');
    expect(req.request.urlWithParams).toContain('limit=50');
    expect(req.request.urlWithParams).toContain('offset=50');
    req.flush(makeAuditPage(2));

    expect(service.audit().length).toBe(2);
    expect(service.auditOffset()).toBe(50);
  });

  it('refreshAll() issues the three GETs and nothing else', () => {
    service.refreshAll();

    const machines = httpMock.expectOne(McpsGatewayService.MACHINES_URL);
    const sessions = httpMock.expectOne(McpsGatewayService.SESSIONS_URL);
    const audit = httpMock.expectOne((r) => r.url === McpsGatewayService.AUDIT_URL);

    expect(machines.request.method).toBe('GET');
    expect(sessions.request.method).toBe('GET');
    expect(audit.request.method).toBe('GET');

    machines.flush(MACHINE_ROSTER);
    sessions.flush(SESSION_LIST);
    audit.flush(makeAuditPage(1));

    expect(service.machines().length).toBe(1);
    expect(service.sessions().length).toBe(1);
    expect(service.audit().length).toBe(1);
  });

  it('the service never issues a write verb (the read surface is GET-only)', () => {
    service.refreshAll();
    const requests = [
      httpMock.expectOne(McpsGatewayService.MACHINES_URL),
      httpMock.expectOne(McpsGatewayService.SESSIONS_URL),
      httpMock.expectOne((r) => r.url === McpsGatewayService.AUDIT_URL),
    ];

    // Every recorded request is a GET — there is no write lane at all.
    for (const req of requests) {
      expect(req.request.method).toBe('GET');
    }

    requests[0].flush(MACHINE_ROSTER);
    requests[1].flush(SESSION_LIST);
    requests[2].flush(makeAuditPage(0));
  });

  // ── (b) the payload shape (incl. the nullable fields) ────────────────

  it('keeps the nullable machine fields null (last_seen_at / session_id / agent_id)', () => {
    service.loadMachines();
    httpMock.expectOne(McpsGatewayService.MACHINES_URL).flush({
      machines: [
        {
          machine_id: 'mac-mini-02',
          presence: 'offline',
          online: false,
          connected: false,
          last_seen_at: null,
          session_id: null,
          agent_id: null,
          busy: false,
        },
      ],
      count: 1,
      gateway_enabled: true,
    } satisfies McpsGatewayMachinesResponse);

    const machine = service.machines()[0];
    expect(machine.last_seen_at).toBeNull();
    expect(machine.session_id).toBeNull();
    expect(machine.agent_id).toBeNull();
    expect(machine.presence).toBe('offline');
  });

  it('keeps the nullable session + audit fields null', () => {
    service.loadSessions();
    httpMock.expectOne(McpsGatewayService.SESSIONS_URL).flush({
      sessions: [
        {
          session_id: 'sess-x',
          machine_id: 'mac-x',
          agent_id: 'coder',
          instance_id: 'inst-x',
          state: 'live',
          machine_locked: false,
          started_at: null,
          last_activity_at: null,
          expires_at: null,
          tools: [],
        },
      ],
      count: 1,
      gateway_enabled: true,
    } satisfies McpsGatewaySessionsResponse);

    service.loadAudit(0);
    httpMock.expectOne((r) => r.url === McpsGatewayService.AUDIT_URL).flush({
      audit: [
        {
          timestamp: null,
          agent_id: 'coder',
          instance_id: null,
          machine_id: 'mac-x',
          tool: 'computer_type',
          outcome: 'denied',
          session_id: null,
          error_code: null,
          args_hash: '',
        },
      ],
      count: 1,
      limit: 50,
      offset: 0,
      gateway_enabled: true,
    } satisfies McpsGatewayAuditResponse);

    expect(service.sessions()[0].started_at).toBeNull();
    expect(service.sessions()[0].expires_at).toBeNull();
    expect(service.audit()[0].timestamp).toBeNull();
    expect(service.audit()[0].session_id).toBeNull();
    expect(service.audit()[0].error_code).toBeNull();
  });

  it('renders the audit rows in the order the server returned them (no re-sort)', () => {
    service.loadAudit(0);
    httpMock.expectOne((r) => r.url === McpsGatewayService.AUDIT_URL).flush({
      audit: [
        {
          timestamp: '2026-10-22T07:31:04.512000+00:00',
          agent_id: 'leader',
          instance_id: 'inst-42',
          machine_id: 'mac-studio-01',
          tool: 'computer_click',
          outcome: 'ok',
          session_id: 'sess-9f2c1a',
          error_code: null,
          args_hash: 'aaa',
        },
        {
          timestamp: '2026-10-22T07:30:04.512000+00:00',
          agent_id: 'leader',
          instance_id: 'inst-42',
          machine_id: 'mac-studio-01',
          tool: 'computer_type',
          outcome: 'error',
          session_id: 'sess-9f2c1a',
          error_code: 'TOOL_FAILED',
          args_hash: 'bbb',
        },
      ],
      count: 2,
      limit: 50,
      offset: 0,
      gateway_enabled: true,
    } satisfies McpsGatewayAuditResponse);

    expect(service.audit().map((entry) => entry.tool)).toEqual([
      'computer_click',
      'computer_type',
    ]);
  });

  // ── (c) the gateway-disabled shape ───────────────────────────────────

  it('reports gateway_enabled:false with an empty roster (the degraded shape)', () => {
    service.loadMachines();
    httpMock.expectOne(McpsGatewayService.MACHINES_URL).flush({
      machines: [],
      count: 0,
      gateway_enabled: false,
    } satisfies McpsGatewayMachinesResponse);

    expect(service.gatewayEnabled()).toBe(false);
    expect(service.machines()).toEqual([]);
    expect(service.error()).toBeNull();
  });

  // ── (d) the error decoding ───────────────────────────────────────────

  it('decodes the 503 gateway_unavailable body into a typed error', () => {
    service.loadMachines();
    httpMock
      .expectOne(McpsGatewayService.MACHINES_URL)
      .flush(
        {
          detail: {
            error: 'gateway_unavailable',
            message: 'The remote-hands gateway service is not initialized',
            state: 'not_initialized',
          },
        },
        { status: 503, statusText: 'Service Unavailable' },
      );

    const error = service.error();
    expect(error).not.toBeNull();
    expect(error!.code).toBe('gateway_unavailable');
    expect(error!.message).toContain('not initialized');
    expect(error!.status).toBe(503);
    expect(error!.source).toBe('machines');
  });

  it('decodes the 403 SCOPE_INSUFFICIENT body (the flat { error } shape)', () => {
    // D3a: the server's message is preserved verbatim — no longer
    // discarded in favour of a hard-coded label. The FLAT shape has no
    // `required` list, so the message stands alone.
    service.loadSessions();
    httpMock
      .expectOne(McpsGatewayService.SESSIONS_URL)
      .flush(
        { error: 'SCOPE_INSUFFICIENT', message: 'missing scope' },
        { status: 403, statusText: 'Forbidden' },
      );

    const error = service.error();
    expect(error).not.toBeNull();
    expect(error!.code).toBe('SCOPE_INSUFFICIENT');
    expect(error!.message).toBe('missing scope');
    expect(error!.status).toBe(403);
  });

  it('decodes the 403 SCOPE_INSUFFICIENT body (the nested { detail } shape, the live-pinned decorator body)', () => {
    // D3a: the LIVE shape (daemon/auth/decorators.py) nests under
    // `detail` and carries the `required` scope list — the message
    // must preserve BOTH the server's message and the list.
    service.loadAudit(0);
    httpMock
      .expectOne((r) => r.url === McpsGatewayService.AUDIT_URL)
      .flush(
        {
          detail: {
            error: 'SCOPE_INSUFFICIENT',
            message: 'Token lacks required scope',
            required: ['mcp:read'],
          },
        },
        { status: 403, statusText: 'Forbidden' },
      );

    const error = service.error();
    expect(error).not.toBeNull();
    expect(error!.code).toBe('SCOPE_INSUFFICIENT');
    expect(error!.message).toBe('Token lacks required scope (needs mcp:read)');
    expect(error!.source).toBe('audit');
  });

  it('a 403 WITHOUT the SCOPE_INSUFFICIENT code renders the generic forbidden label (never SCOPE_INSUFFICIENT)', () => {
    // The CSRF guard / a reverse proxy also answer 403 — labelling
    // those "SCOPE_INSUFFICIENT" would send the operator hunting the
    // wrong credential.
    service.loadMachines();
    httpMock
      .expectOne(McpsGatewayService.MACHINES_URL)
      .flush(
        { error: 'CSRF_ORIGIN_MISMATCH' },
        { status: 403, statusText: 'Forbidden' },
      );

    const error = service.error();
    expect(error).not.toBeNull();
    expect(error!.code).toBe('forbidden');
    expect(error!.code).not.toBe('SCOPE_INSUFFICIENT');
    expect(error!.message).toContain('CSRF_ORIGIN_MISMATCH');
  });

  it('decodes the 401 AUTH_REQUIRED body (the flat middleware shape)', () => {
    // daemon/middleware/auth.py answers 401 with a FLAT
    // { error, message } body — this branch had NO pin at all before
    // D3a (a grep for 401|AUTH_REQUIRED over these specs was empty).
    service.loadMachines();
    httpMock
      .expectOne(McpsGatewayService.MACHINES_URL)
      .flush(
        { error: 'AUTH_REQUIRED', message: 'Authentication required' },
        { status: 401, statusText: 'Unauthorized' },
      );

    const error = service.error();
    expect(error).not.toBeNull();
    expect(error!.code).toBe('AUTH_REQUIRED');
    expect(error!.message).toBe('Authentication required');
    expect(error!.status).toBe(401);
  });

  it('an HTML body behind a followed 302 renders not_json — never http_200', () => {
    // The trap: a 302 to /api/auth/login followed by the browser yields
    // 200 + HTML. A transport that delivers it to the success path (or
    // one whose JSON parse fails into the error path) must render a
    // NON-http_200 label, not an "HTTP 200 success" lie.
    service.loadMachines();
    httpMock
      .expectOne(McpsGatewayService.MACHINES_URL)
      .flush('<!doctype html><html><body>login</body></html>', {
        status: 200,
        statusText: 'OK',
      });

    const error = service.error();
    expect(error).not.toBeNull();
    expect(error!.code).toBe('not_json');
    expect(error!.code).not.toBe('http_200');
    expect(error!.message).toContain('non-JSON');
    // The HTML never reaches the rows.
    expect(service.machines()).toEqual([]);
  });

  it('clears a lane error once that lane recovers', () => {
    service.loadMachines();
    httpMock
      .expectOne(McpsGatewayService.MACHINES_URL)
      .flush({ detail: { error: 'gateway_unavailable' } }, { status: 503, statusText: 'Service Unavailable' });
    expect(service.error()).not.toBeNull();

    service.loadMachines();
    httpMock.expectOne(McpsGatewayService.MACHINES_URL).flush(MACHINE_ROSTER);
    expect(service.error()).toBeNull();
  });

  // ── (d2) the degraded signal (D2) ──────────────────────────────────

  it('a body WITHOUT the degraded key renders as a healthy read (the optional-field default)', () => {
    // Decision B3b: `degraded` is OPTIONAL on the wire models, so the
    // 8 typed literals keep compiling — undefined must behave as
    // `false`, and this is the pin for that default.
    service.refreshAll();
    httpMock.expectOne(McpsGatewayService.MACHINES_URL).flush(MACHINE_ROSTER);
    httpMock.expectOne(McpsGatewayService.SESSIONS_URL).flush(SESSION_LIST);
    httpMock
      .expectOne((r) => r.url === McpsGatewayService.AUDIT_URL)
      .flush(makeAuditPage(2));

    expect(service.degraded()).toBe(false);
    expect(service.degradedSource()).toBeNull();
    expect(service.error()).toBeNull();
  });

  it('a degraded audit lane keeps the LAST page and raises the degraded flag', () => {
    // Flush a healthy page first…
    service.loadAudit(0);
    httpMock
      .expectOne((r) => r.url === McpsGatewayService.AUDIT_URL)
      .flush(makeAuditPage(2));
    expect(service.audit().length).toBe(2);
    expect(service.auditOffset()).toBe(0);

    // …then the outage shape: 200 + degraded:true (never 503). The
    // rows, the offset and the pager state MUST survive — the ledger
    // is unreadable, the page is not.
    service.loadAudit(50);
    httpMock
      .expectOne((r) => r.url === McpsGatewayService.AUDIT_URL)
      .flush({
        audit: [],
        count: 0,
        limit: 50,
        offset: 50,
        gateway_enabled: true,
        degraded: true,
        error: 'audit_unavailable',
      } satisfies McpsGatewayAuditResponse);

    expect(service.degraded()).toBe(true);
    expect(service.degradedSource()).toBe('audit');
    expect(service.error()).toBeNull();
    expect(service.audit().length).toBe(2); // the LAST page stays
    expect(service.auditOffset()).toBe(0); // the pager state stays
  });

  it('a recovered healthy read clears the degraded flag', () => {
    service.loadAudit(0);
    httpMock
      .expectOne((r) => r.url === McpsGatewayService.AUDIT_URL)
      .flush({
        audit: [],
        count: 0,
        limit: 50,
        offset: 0,
        gateway_enabled: true,
        degraded: true,
        error: 'audit_unavailable',
      } satisfies McpsGatewayAuditResponse);
    expect(service.degraded()).toBe(true);

    service.loadAudit(0);
    httpMock
      .expectOne((r) => r.url === McpsGatewayService.AUDIT_URL)
      .flush(makeAuditPage(1));
    expect(service.degraded()).toBe(false);
    expect(service.degradedSource()).toBeNull();
  });

  it('a not_json audit body CLEARS a stale degraded flag (no banner coexistence)', () => {
    // Degrade first…
    service.loadAudit(0);
    httpMock
      .expectOne((r) => r.url === McpsGatewayService.AUDIT_URL)
      .flush({
        audit: [],
        count: 0,
        limit: 50,
        offset: 0,
        gateway_enabled: true,
        degraded: true,
        error: 'audit_unavailable',
      } satisfies McpsGatewayAuditResponse);
    expect(service.degraded()).toBe(true);

    // …then the login-page shape: 200 + an HTML body. The degraded
    // slot must RESET (an ERROR is not a degraded read) so the
    // degraded banner and the error banner can never coexist (C11).
    service.loadAudit(0);
    httpMock
      .expectOne((r) => r.url === McpsGatewayService.AUDIT_URL)
      .flush('<!doctype html><html lang="en"><body>login</body></html>');

    expect(service.degraded()).toBe(false);
    expect(service.degradedSource()).toBeNull();
    expect(service.error()?.code).toBe('not_json');
    expect(service.error()?.source).toBe('audit');
  });

  it('a degraded machines body keeps the LAST roster and raises the flag (latent lane symmetry)', () => {
    service.loadMachines();
    httpMock.expectOne(McpsGatewayService.MACHINES_URL).flush(MACHINE_ROSTER);
    expect(service.machines().length).toBe(1);

    // The machines lane is IN-MEMORY and answers degraded:false today
    // (truthful symmetry); if it ever degrades, the semantics must
    // mirror the audit lane — keep the last good roster, raise the
    // flag, clear the error slot (C11).
    service.loadMachines();
    httpMock
      .expectOne(McpsGatewayService.MACHINES_URL)
      .flush({
        machines: [],
        count: 0,
        gateway_enabled: true,
        degraded: true,
      } satisfies McpsGatewayMachinesResponse);

    expect(service.degraded()).toBe(true);
    expect(service.degradedSource()).toBe('machines');
    expect(service.error()).toBeNull();
    expect(service.machines().length).toBe(1); // the LAST roster stays
  });

  it('sends the defensive Accept: application/json header on every read (the 302 trap)', () => {
    service.refreshAll();
    const requests = [
      httpMock.expectOne(McpsGatewayService.MACHINES_URL),
      httpMock.expectOne(McpsGatewayService.SESSIONS_URL),
      httpMock.expectOne((r) => r.url === McpsGatewayService.AUDIT_URL),
    ];
    for (const req of requests) {
      expect(req.request.headers.get('Accept')).toBe('application/json');
    }
    requests[0].flush(MACHINE_ROSTER);
    requests[1].flush(SESSION_LIST);
    requests[2].flush(makeAuditPage(0));
  });

  // ── (e) the pagination contract ──────────────────────────────────────

  it('enables Next only when the page came back full (count === limit)', () => {
    service.loadAudit(0);
    httpMock.expectOne((r) => r.url === McpsGatewayService.AUDIT_URL).flush(makeAuditPage(3));
    expect(service.canNextAuditPage()).toBe(false);

    service.loadAudit(0);
    httpMock.expectOne((r) => r.url === McpsGatewayService.AUDIT_URL).flush(makeAuditPage(50));
    expect(service.canNextAuditPage()).toBe(true);
  });

  it('advances one page on nextAuditPage() (offset += limit)', () => {
    service.loadAudit(0);
    httpMock.expectOne((r) => r.url === McpsGatewayService.AUDIT_URL).flush(makeAuditPage(50));
    expect(service.canNextAuditPage()).toBe(true);

    service.nextAuditPage();
    const req = httpMock.expectOne((r) => r.url === McpsGatewayService.AUDIT_URL);
    expect(req.request.urlWithParams).toContain('offset=50');
    expect(req.request.urlWithParams).toContain('limit=50');
    req.flush(makeAuditPage(50));

    expect(service.auditOffset()).toBe(50);
    expect(service.auditPageNumber()).toBe(2);
  });

  it('does NOT advance when the page was not full (the guard holds)', () => {
    service.loadAudit(0);
    httpMock.expectOne((r) => r.url === McpsGatewayService.AUDIT_URL).flush(makeAuditPage(3));

    service.nextAuditPage();
    // No request was issued — httpMock.verify() in afterEach proves it.
    expect(service.auditOffset()).toBe(0);
  });

  it('disables Previous on the first page and goes back one page after that', () => {
    service.loadAudit(0);
    httpMock.expectOne((r) => r.url === McpsGatewayService.AUDIT_URL).flush(makeAuditPage(50));
    expect(service.canPrevAuditPage()).toBe(false);

    service.nextAuditPage();
    httpMock.expectOne((r) => r.url === McpsGatewayService.AUDIT_URL).flush(makeAuditPage(50));
    expect(service.auditOffset()).toBe(50);
    expect(service.canPrevAuditPage()).toBe(true);

    service.prevAuditPage();
    const req = httpMock.expectOne((r) => r.url === McpsGatewayService.AUDIT_URL);
    expect(req.request.urlWithParams).toContain('offset=0');
    req.flush(makeAuditPage(50));
    expect(service.auditOffset()).toBe(0);
  });

  it('clamps an out-of-range limit/offset instead of earning a 422', () => {
    service.auditLimit.set(5000);
    service.loadAudit(-10);

    const req = httpMock.expectOne((r) => r.url === McpsGatewayService.AUDIT_URL);
    expect(req.request.urlWithParams).toContain('limit=200');
    expect(req.request.urlWithParams).toContain('offset=0');
    req.flush(makeAuditPage(0));
  });

  // ── (f) the polling + loading contract ───────────────────────────────

  it('pins the poll interval at 10 s', () => {
    expect(McpsGatewayService.POLL_INTERVAL_MS).toBe(10000);
  });

  it('pins the audit page size at 50 and the BE maximum at 200', () => {
    expect(McpsGatewayService.AUDIT_PAGE_SIZE).toBe(50);
    expect(McpsGatewayService.AUDIT_MAX_LIMIT).toBe(200);
    expect(service.auditLimit()).toBe(50);
  });

  it('reports loading while a request is in flight and clears it afterwards', () => {
    expect(service.loading()).toBe(false);

    service.loadMachines();
    expect(service.loading()).toBe(true);

    httpMock.expectOne(McpsGatewayService.MACHINES_URL).flush(MACHINE_ROSTER);
    expect(service.loading()).toBe(false);
  });

  it('does NOT stack overlapping refresh cycles', () => {
    service.refreshAll();
    // A second refresh while the first is in flight is DROPPED — no
    // second set of requests is issued (httpMock.verify() proves it).
    service.refreshAll();

    httpMock.expectOne(McpsGatewayService.MACHINES_URL).flush(MACHINE_ROSTER);
    httpMock.expectOne(McpsGatewayService.SESSIONS_URL).flush(SESSION_LIST);
    httpMock.expectOne((r) => r.url === McpsGatewayService.AUDIT_URL).flush(makeAuditPage(0));
  });
});
