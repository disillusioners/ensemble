import { Injectable, computed, inject, signal } from '@angular/core';
import { HttpClient, HttpErrorResponse, HttpParams } from '@angular/common/http';
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
    return this.track(() => this.http.get<McpsGatewayMachinesResponse>(McpsGatewayService.MACHINES_URL)).pipe(
      tap((res) => {
        this.machines.set(res.machines ?? []);
        this.gatewayEnabled.set(res.gateway_enabled !== false);
        this.machinesError.set(null);
      }),
      catchError((err) => {
        this.machinesError.set(this.decodeError(err, 'machines'));
        return EMPTY;
      }),
    );
  }

  private fetchSessions(): Observable<McpsGatewaySessionsResponse> {
    return this.track(() => this.http.get<McpsGatewaySessionsResponse>(McpsGatewayService.SESSIONS_URL)).pipe(
      tap((res) => {
        this.sessions.set(res.sessions ?? []);
        this.gatewayEnabled.set(res.gateway_enabled !== false);
        this.sessionsError.set(null);
      }),
      catchError((err) => {
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
      this.http.get<McpsGatewayAuditResponse>(McpsGatewayService.AUDIT_URL, { params }),
    ).pipe(
      tap((res) => {
        this.audit.set(res.audit ?? []);
        this.auditCount.set(res.count ?? (res.audit ?? []).length);
        this.auditLimit.set(limit);
        this.auditOffset.set(safeOffset);
        this.gatewayEnabled.set(res.gateway_enabled !== false);
        this.auditError.set(null);
      }),
      catchError((err) => {
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
   * renders.
   *
   * The daemon answers with TWO body shapes and the page must accept
   * both:
   *
   * * `{"error": "...", "message": "..."}` — the flat shape (the scope
   *   decorator's 403);
   * * `{"detail": {"error": "...", "message": "..."}}` — the FastAPI
   *   `HTTPException` shape (the 503 `gateway_unavailable`).
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
    const rawMessage = this.asString(detail['message']) ?? this.asString(body?.['message']);

    if (rawCode === 'SCOPE_INSUFFICIENT' || status === 403) {
      return {
        code: 'SCOPE_INSUFFICIENT',
        message: 'credential lacks mcp:read',
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

  /** A plain object (not an array), or `null`. */
  private asRecord(value: unknown): Record<string, unknown> | null {
    return value && typeof value === 'object' && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : null;
  }

  /** A string, or `undefined`. */
  private asString(value: unknown): string | undefined {
    return typeof value === 'string' ? value : undefined;
  }
}
