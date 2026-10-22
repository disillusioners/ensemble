import { Injectable, computed, inject, signal } from '@angular/core';
import {
  HttpClient,
  HttpErrorResponse,
  HttpHeaders,
  HttpParams,
} from '@angular/common/http';
import { EMPTY, Observable, catchError, defer, finalize, forkJoin, tap } from 'rxjs';
import type {
  McpsGatewayAuditEntry,
  McpsGatewayAuditResponse,
  McpsGatewayError,
  McpsGatewayErrorSource,
  McpsGatewayMachine,
  McpsGatewayMachinesResponse,
  McpsGatewaySession,
  McpsGatewaySessionsResponse,
} from '../../models';

/**
 * HTTP service for the Remote Hands / MCP Gateway READ surface.
 *
 * Wires the three READ-ONLY GET endpoints of
 * `daemon/routers/mcps_gateway_read.py` to typed Angular signals:
 *
 *   GET /api/mcps-gateway/machines → loadMachines()
 *   GET /api/mcps-gateway/sessions → loadSessions()
 *   GET /api/mcps-gateway/audit    → loadAudit(offset)
 *
 * **GET ONLY — there is no write lane here.** The gateway is driven by
 * the AGENT TOOLS (`mcp_rpc_*`), never by the browser; v1 of the page is
 * strictly observational. The kill / lock-release controls arrive with
 * the v0.2 per-tool ACL work, and they will land on the agent surface
 * first — the page only ever REPORTS the state the center keeps.
 *
 * The three lanes are INDEPENDENT: a failing lane never blanks the
 * others. Each lane owns its own error slot and the page shows the first
 * non-null one — so a 503 on the roster does not erase the audit tail.
 *
 * The URLs are relative (`/api/...`) — the dev proxy
 * (`frontend/proxy.conf.json`) forwards them to the daemon on :8079.
 * There is no hard-coded host or port anywhere in the FE.
 */
@Injectable({ providedIn: 'root' })
export class McpsGatewayService {
  private readonly http = inject(HttpClient);

  // ── URL constants (the single source of the FE ↔ BE surface) ────────

  static readonly MACHINES_URL = '/api/mcps-gateway/machines';
  static readonly SESSIONS_URL = '/api/mcps-gateway/sessions';
  static readonly AUDIT_URL = '/api/mcps-gateway/audit';

  /**
   * The defensive `Accept: application/json` header of every gateway
   * read (D3a, upstream-decided).
   *
   * The OAuth middleware's unauthenticated flow answers a browser-ish
   * request (a bare wildcard Accept header, or a missing one) with a
   * 302 to the login page; only an explicit `application/json` earns
   * the clean 401 `AUTH_REQUIRED` JSON body
   * (`daemon/middleware/auth.py`). The HttpClient default sends a
   * wildcard Accept, which sits ON the trap — this header is cheap
   * insurance that keeps every unauthenticated read on the JSON lane,
   * and it self-documents the trap right here at the call site.
   */
  static readonly JSON_HEADERS = new HttpHeaders({ Accept: 'application/json' });

  /**
   * The poll cadence of the page — **10 s**.
   *
   * Why 10 s: the machine heartbeat is 15 s
   * (`MCPS_GATEWAY_HEARTBEAT_INTERVAL_MS`) and the presence timeout is
   * 45 s (`MCPS_GATEWAY_HEARTBEAT_TIMEOUT_MS`), so presence only ever
   * changes on a heartbeat. A 10 s poll therefore keeps the presence
   * badge honest (worst-case staleness ≈ one poll, comfortably inside
   * the heartbeat interval) while costing three cheap GETs per tick.
   * A faster cadence (2–5 s) buys nothing — presence would not have
   * moved yet — and a slower one (30 s+) lets a machine flap between
   * `online` and `offline` for long stretches. The value is a
   * `static readonly` so the page, the spec and the doc quote ONE
   * number.
   */
  static readonly POLL_INTERVAL_MS = 10_000;

  /**
   * The audit page size — the BE default (`AUDIT_DEFAULT_LIMIT`).
   */
  static readonly AUDIT_PAGE_SIZE = 50;

  /**
   * The BE's hard maximum (`AUDIT_MAX_LIMIT`). A `limit` above it is a
   * 422 (FastAPI validates before the handler runs) — so the FE never
   * sends one.
   */
  static readonly AUDIT_MAX_LIMIT = 200;

  // ── State ────────────────────────────────────────────────────────────

  /** The machine roster (empty when the gateway is off). */
  readonly machines = signal<McpsGatewayMachine[]>([]);

  /** The live sessions (empty when the gateway is off). */
  readonly sessions = signal<McpsGatewaySession[]>([]);

  /**
   * The audit tail — NEWEST FIRST, exactly as the server returned it.
   *
   * The page NEVER re-sorts this array: the ledger order IS the
   * contract (the server hands back the newest row first).
   */
  readonly audit = signal<McpsGatewayAuditEntry[]>([]);

  /**
   * The kill-switch state (`MCPS_GATEWAY_ENABLED`).
   *
   * `true` until a response says otherwise — the read routes are
   * registered unconditionally, so an empty roster with
   * `gateway_enabled: false` means "the gateway is OFF", not "the
   * daemon is broken".
   */
  readonly gatewayEnabled = signal(true);

  /** The offset of the audit page currently on screen. */
  readonly auditOffset = signal(0);

  /** The page size in use (the BE default; clamped to the BE bounds). */
  readonly auditLimit = signal(McpsGatewayService.AUDIT_PAGE_SIZE);

  /** The client-side stamp of the last completed refresh cycle. */
  readonly lastRefreshedAt = signal<Date | null>(null);

  // ── Per-lane degraded slots + the derived surface ────────────────────

  private readonly machinesDegraded = signal(false);
  private readonly sessionsDegraded = signal(false);
  private readonly auditDegraded = signal(false);

  /**
   * Did any lane answer its DEGRADED body (200 + `degraded: true`)?
   *
   * A degraded lane is NOT an error: the daemon answered, the other
   * lanes are healthy, and the last good page stays on screen — the
   * page renders a DISTINCT banner for it (only the audit lane can
   * degrade today; the machines/sessions lanes are in-memory and
   * permanently `false` — truthful symmetry, AD4).
   */
  readonly degraded = computed(
    () => this.machinesDegraded() || this.sessionsDegraded() || this.auditDegraded(),
  );

  /** The first degraded lane (for the banner's context), or `null`. */
  readonly degradedSource = computed<McpsGatewayErrorSource | null>(() => {
    if (this.machinesDegraded()) return 'machines';
    if (this.sessionsDegraded()) return 'sessions';
    if (this.auditDegraded()) return 'audit';
    return null;
  });

  // ── Per-lane error slots + the derived surface ───────────────────────

  private readonly machinesError = signal<McpsGatewayError | null>(null);
  private readonly sessionsError = signal<McpsGatewayError | null>(null);
  private readonly auditError = signal<McpsGatewayError | null>(null);

  /**
   * The first non-null lane error — the page renders exactly one banner.
   *
   * Derived from the per-lane slots so a lane that recovers CLEARS its
   * own error (a successful refresh resets the banner without a manual
   * dismiss).
   */
  readonly error = computed<McpsGatewayError | null>(
    () => this.machinesError() ?? this.sessionsError() ?? this.auditError(),
  );

  /** The number of requests currently in flight. */
  private readonly pending = signal(0);

  /** `true` while any read request is in flight. */
  readonly loading = computed(() => this.pending() > 0);

  /** The `count` of the LAST audit page — the PAGE LENGTH, not the total. */
  private readonly auditCount = signal(0);

  /**
   * Next is enabled IFF the last page came back FULL (`count === limit`).
   *
   * The read surface does not expose the ledger size, so a full page is
   * the only "there may be more" signal there is. An empty or short page
   * means the tail ends here.
   */
  readonly canNextAuditPage = computed(() => this.auditCount() === this.auditLimit());

  /** Previous is enabled IFF the current offset is past the head. */
  readonly canPrevAuditPage = computed(() => this.auditOffset() > 0);

  /** The 1-based page number the operator sees. */
  readonly auditPageNumber = computed(
    () => Math.floor(this.auditOffset() / Math.max(1, this.auditLimit())) + 1,
  );

  /**
   * The overlap guard — a refresh cycle that is still running blocks the
   * next one (a slow daemon + a 10 s tick must not pile up requests).
   */
  private refreshInFlight = false;

  // ── Public API ───────────────────────────────────────────────────────

  /**
   * Refresh all three lanes at once.
   *
   * The lanes are independent (one failure never blanks the others) but
   * the CYCLE is guarded: while a cycle is in flight a new one is
   * dropped, so a slow daemon cannot accumulate overlapping refreshes.
   */
  refreshAll(): void {
    if (this.refreshInFlight) {
      return;
    }
    this.refreshInFlight = true;
    forkJoin({
      machines: this.fetchMachines(),
      sessions: this.fetchSessions(),
      audit: this.fetchAudit(this.auditOffset()),
    })
      .pipe(
        finalize(() => {
          this.refreshInFlight = false;
          this.lastRefreshedAt.set(new Date());
        }),
      )
      .subscribe();
  }

  /** `GET /api/mcps-gateway/machines`. */
  loadMachines(): void {
    this.fetchMachines().subscribe();
  }

  /** `GET /api/mcps-gateway/sessions`. */
  loadSessions(): void {
    this.fetchSessions().subscribe();
  }

  /**
   * `GET /api/mcps-gateway/audit?limit=&offset=`.
   *
   * The offset is clamped to `>= 0` and the limit to the BE bounds
   * (`1..200`) — an out-of-range value is a 422, and the FE never
   * earns one.
   */
  loadAudit(offset: number = this.auditOffset()): void {
    this.fetchAudit(offset).subscribe();
  }

  /** Advance one page — a no-op unless the last page came back full. */
  nextAuditPage(): void {
    if (!this.canNextAuditPage()) {
      return;
    }
    this.loadAudit(this.auditOffset() + this.auditLimit());
  }

  /** Go back one page — a no-op on the first page. */
  prevAuditPage(): void {
    if (!this.canPrevAuditPage()) {
      return;
    }
    this.loadAudit(Math.max(0, this.auditOffset() - this.auditLimit()));
  }

  // ── Lane fetchers (private) ──────────────────────────────────────────

  private fetchMachines(): Observable<McpsGatewayMachinesResponse> {
    return this.track(() =>
      this.http.get<McpsGatewayMachinesResponse>(McpsGatewayService.MACHINES_URL, {
        headers: McpsGatewayService.JSON_HEADERS,
      }),
    ).pipe(
      tap((res) => {
        if (this.bodyIsUnexpected(res)) {
          // Lane hygiene (C11): a non-JSON body is an ERROR, not a
          // degraded read — clear the stale degraded slot so the two
          // banners can never coexist.
          this.machinesDegraded.set(false);
          this.machinesError.set(this.notJsonError('machines', 200));
          return;
        }
        if (res.degraded === true) {
          // Latent symmetry with the audit lane (C11): the machines
          // lane is IN-MEMORY and answers degraded:false today, but if
          // it ever degrades the semantics match the audit lane — keep
          // the last good roster, raise the degraded flag, clear the
          // error slot.
          this.gatewayEnabled.set(res.gateway_enabled !== false);
          this.machinesDegraded.set(true);
          this.machinesError.set(null);
          return;
        }
        this.machines.set(res.machines ?? []);
        this.gatewayEnabled.set(res.gateway_enabled !== false);
        this.machinesDegraded.set(res.degraded === true);
        this.machinesError.set(null);
      }),
      catchError((err) => {
        this.machinesDegraded.set(false);
        this.machinesError.set(this.decodeError(err, 'machines'));
        return EMPTY;
      }),
    );
  }

  private fetchSessions(): Observable<McpsGatewaySessionsResponse> {
    return this.track(() =>
      this.http.get<McpsGatewaySessionsResponse>(McpsGatewayService.SESSIONS_URL, {
        headers: McpsGatewayService.JSON_HEADERS,
      }),
    ).pipe(
      tap((res) => {
        if (this.bodyIsUnexpected(res)) {
          // Lane hygiene (C11): a non-JSON body is an ERROR, not a
          // degraded read — clear the stale degraded slot so the two
          // banners can never coexist.
          this.sessionsDegraded.set(false);
          this.sessionsError.set(this.notJsonError('sessions', 200));
          return;
        }
        if (res.degraded === true) {
          // Latent symmetry with the audit lane (C11): the sessions
          // lane is IN-MEMORY and answers degraded:false today, but if
          // it ever degrades the semantics match the audit lane — keep
          // the last good list, raise the degraded flag, clear the
          // error slot.
          this.gatewayEnabled.set(res.gateway_enabled !== false);
          this.sessionsDegraded.set(true);
          this.sessionsError.set(null);
          return;
        }
        this.sessions.set(res.sessions ?? []);
        this.gatewayEnabled.set(res.gateway_enabled !== false);
        this.sessionsDegraded.set(res.degraded === true);
        this.sessionsError.set(null);
      }),
      catchError((err) => {
        this.sessionsDegraded.set(false);
        this.sessionsError.set(this.decodeError(err, 'sessions'));
        return EMPTY;
      }),
    );
  }

  private fetchAudit(offset: number): Observable<McpsGatewayAuditResponse> {
    const limit = this.clampLimit(this.auditLimit());
    const safeOffset = Math.max(0, Math.floor(offset));
    const params = new HttpParams()
      .set('limit', String(limit))
      .set('offset', String(safeOffset));
    return this.track(() =>
      this.http.get<McpsGatewayAuditResponse>(McpsGatewayService.AUDIT_URL, {
        params,
        headers: McpsGatewayService.JSON_HEADERS,
      }),
    ).pipe(
      tap((res) => {
        if (this.bodyIsUnexpected(res)) {
          // Lane hygiene (C11): a non-JSON body is an ERROR, not a
          // degraded read — clear the stale degraded slot so the
          // degraded banner and the error banner can never coexist.
          this.auditDegraded.set(false);
          this.auditError.set(this.notJsonError('audit', 200));
          return;
        }
        if (res.degraded === true) {
          // A degraded tail must NOT blank the last good page: the
          // ledger is unreadable, the page is not — keep the rows, the
          // page number and the pager state, and let the banner say
          // what happened. Only a HEALTHY read reassigns them.
          this.gatewayEnabled.set(res.gateway_enabled !== false);
          this.auditDegraded.set(true);
          this.auditError.set(null);
          return;
        }
        this.audit.set(res.audit ?? []);
        this.auditCount.set(res.count ?? (res.audit ?? []).length);
        this.auditLimit.set(limit);
        this.auditOffset.set(safeOffset);
        this.gatewayEnabled.set(res.gateway_enabled !== false);
        this.auditDegraded.set(false);
        this.auditError.set(null);
      }),
      catchError((err) => {
        this.auditDegraded.set(false);
        this.auditError.set(this.decodeError(err, 'audit'));
        return EMPTY;
      }),
    );
  }

  // ── Helpers ──────────────────────────────────────────────────────────

  /**
   * Wrap a request in the in-flight counter so `loading` is exact.
   *
   * `defer` keeps the request itself lazy (nothing fires until the
   * observable is subscribed) and lets the counter increment at
   * subscribe-time; `finalize` decrements it on EVERY exit path
   * (complete / error / unsubscribe).
   */
  private track<T>(factory: () => Observable<T>): Observable<T> {
    return defer(() => {
      this.pending.update((n) => n + 1);
      return factory();
    }).pipe(
      finalize(() => {
        this.pending.update((n) => Math.max(0, n - 1));
      }),
    );
  }

  /** Clamp a page size into the BE's accepted band (`1..200`). */
  private clampLimit(limit: number): number {
    if (!Number.isFinite(limit)) {
      return McpsGatewayService.AUDIT_PAGE_SIZE;
    }
    const rounded = Math.floor(limit);
    if (rounded < 1) {
      return 1;
    }
    if (rounded > McpsGatewayService.AUDIT_MAX_LIMIT) {
      return McpsGatewayService.AUDIT_MAX_LIMIT;
    }
    return rounded;
  }

  /**
   * Decode a failed read into the typed `{ code, message }` the page
   * renders (D3a — the BODY is decoded FIRST, the status last).
   *
   * The daemon answers with THREE body shapes and the page must accept
   * them all:
   *
   * * `{"error": "...", "message": "..."}` — the flat shape (the auth
   *   middleware's 401 `AUTH_REQUIRED`; the CSRF guard's 403);
   * * `{"detail": {"error": "...", "message": "...", "required": [...]}}`
   *   — the FastAPI `HTTPException` shape (the scope decorator's 403,
   *   which nests under `detail` and carries the `required` scopes);
   * * a NON-JSON body — a 302 to the login page followed by the
   *   browser yields 200 + HTML, which the transport surfaces as a
   *   parse failure; that must render `not_json`, never `http_200`.
   *
   * The repo's existing detail-unwrapping precedent is
   * `pages/settings/settings.component.ts` (`err?.error?.detail`).
   */
  private decodeError(err: unknown, source: McpsGatewayErrorSource): McpsGatewayError {
    const response = err as Partial<HttpErrorResponse> | null;
    const status = typeof response?.status === 'number' ? response.status : 0;
    const body = this.asRecord(response?.error);
    const detail = this.asRecord(body?.['detail']) ?? body ?? {};
    const rawCode = this.asString(detail['error']) ?? this.asString(body?.['error']);
    const rawMessage =
      this.asString(detail['message']) ?? this.asString(body?.['message']);
    const required = this.asStringList(detail['required'] ?? body?.['required']);

    // The BODY branches come FIRST: a 403 whose body names
    // SCOPE_INSUFFICIENT keeps the server's message AND its required
    // scope list — and any OTHER 403 (CSRF guard, reverse proxy) must
    // NOT wear this label.
    if (rawCode === 'SCOPE_INSUFFICIENT') {
      const base = rawMessage ?? 'Token lacks required scope';
      const message =
        required.length > 0 ? `${base} (needs ${required.join(', ')})` : base;
      return { code: 'SCOPE_INSUFFICIENT', message, status, source };
    }
    if (rawCode === 'AUTH_REQUIRED' || status === 401) {
      return {
        code: 'AUTH_REQUIRED',
        message: rawMessage ?? 'Authentication required',
        status,
        source,
      };
    }
    // A 2xx that reaches the ERROR path is a JSON parse failure — the
    // classic shape is the login page's HTML behind a 302 the browser
    // followed. `http_200` would be a lie.
    if (this.bodyLooksLikeHtml(response?.error) || (status >= 200 && status < 300)) {
      return this.notJsonError(source, status);
    }
    if (status === 403) {
      return {
        code: 'forbidden',
        message: rawMessage ?? rawCode ?? 'The request was forbidden.',
        status,
        source,
      };
    }
    if (rawCode === 'gateway_unavailable' || status === 503) {
      return {
        code: rawCode ?? 'gateway_unavailable',
        message: rawMessage ?? 'The remote-hands gateway service is not initialized.',
        status,
        source,
      };
    }
    if (status === 0) {
      return {
        code: 'network_error',
        message: rawMessage ?? 'The daemon is unreachable.',
        status,
        source,
      };
    }
    return {
      code: rawCode ?? `http_${status}`,
      message: rawMessage ?? `The read failed with HTTP ${status}.`,
      status,
      source,
    };
  }

  /**
   * The shared `not_json` failure — the daemon answered something that
   * is not the gateway's JSON (the login page's HTML is the expected
   * case). Rendered with a NON-`http_200` label.
   */
  private notJsonError(source: McpsGatewayErrorSource, status: number): McpsGatewayError {
    return {
      code: 'not_json',
      message:
        'The daemon answered a non-JSON body (likely the login page), ' +
        'not the gateway read.',
      status,
      source,
    };
  }

  /**
   * The success-path guard: a 200 whose body is not a JSON object is
   * the login-page redirect (the mock/JSON-parse-tolerant transports
   * deliver the HTML as-is instead of failing). Route it to the SAME
   * `not_json` label instead of silently assigning empty rows.
   */
  private bodyIsUnexpected(res: unknown): boolean {
    return !this.asRecord(res) || this.bodyLooksLikeHtml(res);
  }

  /** Does the value look like an HTML document (the login page)? */
  private bodyLooksLikeHtml(value: unknown): boolean {
    if (typeof value !== 'string') {
      return false;
    }
    const trimmed = value.trimStart().slice(0, 64).toLowerCase();
    return trimmed.startsWith('<!doctype') || trimmed.startsWith('<html');
  }

  /** A plain object (not an array), or `null`. */
  private asRecord(value: unknown): Record<string, unknown> | null {
    return value && typeof value === 'object' && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : null;
  }

  /** A list of strings, or `[]`. */
  private asStringList(value: unknown): string[] {
    return Array.isArray(value)
      ? value.filter((entry): entry is string => typeof entry === 'string')
      : [];
  }

  /** A string, or `undefined`. */
  private asString(value: unknown): string | undefined {
    return typeof value === 'string' ? value : undefined;
  }
}
