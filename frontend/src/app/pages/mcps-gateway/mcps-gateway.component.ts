import { Component, DestroyRef, OnDestroy, OnInit, inject } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { CommonModule } from '@angular/common';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTooltipModule } from '@angular/material/tooltip';
import { timer } from 'rxjs';
import { McpsGatewayService } from './mcps-gateway.service';

/**
 * The refocus debounce (ms) — rapid tab switches collapse into ONE
 * immediate refresh (the jobs-page exemplar's discipline). A hidden tab
 * stops ISSUING polls; a refocus produces exactly ONE catch-up read.
 */
const REFOCUS_DEBOUNCE_MS = 2000;

/**
 * Remote Hands / MCP Gateway — the READ-ONLY operator page (Phase C).
 *
 * ONE component, THREE sections (machines / sessions / audit) — the
 * brief's "keep it one component" rule: three child table components
 * would triple the spec surface for a page whose whole job is to look
 * at three lists.
 *
 * **READ-ONLY v1 — there is deliberately no kill / lock-release /
 * disconnect control anywhere in this template.** The gateway is driven
 * by the AGENT TOOLS (`mcp_rpc_*`), never by the browser; the write
 * controls arrive with the v0.2 per-tool ACL work. A destructive button
 * on an operator page is a guard-rail problem, so v1 does not have one.
 *
 * The page polls every `McpsGatewayService.POLL_INTERVAL_MS` (10 s) and
 * stops on destroy (`takeUntilDestroyed`). The service owns the overlap
 * guard, so a slow daemon can never accumulate overlapping refreshes.
 *
 * The degraded states are first-class, not an afterthought:
 *
 * * gateway disabled (`gateway_enabled: false`) → a banner that names
 *   the enable steps and points at `docs/mcps-gateway.md`;
 * * 503 `gateway_unavailable` → an error banner (never a crash);
 * * 403 `SCOPE_INSUFFICIENT` → its own message — the SERVER's message
 *   with its `required` scope list ("Token lacks required scope
 *   (needs mcp:read)"); any other 403 renders the generic `forbidden`
 *   label, never this one;
 * * 401 `AUTH_REQUIRED` / a login-page redirect → the auth banner
 *   (`AUTH_REQUIRED` / `not_json`);
 * * a degraded audit lane (200 + `degraded: true`) → a DISTINCT
 *   degraded banner — the ledger could not be read, the last page
 *   stays on screen.
 */
@Component({
  selector: 'app-mcps-gateway',
  standalone: true,
  imports: [
    CommonModule,
    MatButtonModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatTooltipModule,
  ],
  templateUrl: './mcps-gateway.component.html',
  styleUrl: './mcps-gateway.component.scss',
})
export class McpsGatewayComponent implements OnInit, OnDestroy {
  private readonly service = inject(McpsGatewayService);
  private readonly destroyRef = inject(DestroyRef);

  /**
   * The browser tab's visibility — the POLL GATE. A hidden tab stops
   * ISSUING the 10 s gateway poll (the cadence is unchanged for VISIBLE
   * tabs).
   */
  private browserVisible = true;

  /**
   * The refocus debounce handle — rapid tab switches collapse into ONE
   * immediate refresh (the timer id is kept so a second focus cancels and
   * re-arms it instead of queueing a second refresh).
   */
  private refocusTimer: ReturnType<typeof setTimeout> | null = null;

  // ── The service's signals, exposed for the template ──────────────────
  // (the component reads them; it never writes them — the service owns
  // the state and the fetch lifecycle).

  readonly machines = this.service.machines;
  readonly sessions = this.service.sessions;
  readonly audit = this.service.audit;
  readonly loading = this.service.loading;
  readonly error = this.service.error;
  readonly degraded = this.service.degraded;
  readonly degradedSource = this.service.degradedSource;
  readonly gatewayEnabled = this.service.gatewayEnabled;
  readonly auditOffset = this.service.auditOffset;
  readonly auditLimit = this.service.auditLimit;
  readonly canNextAuditPage = this.service.canNextAuditPage;
  readonly canPrevAuditPage = this.service.canPrevAuditPage;
  readonly auditPageNumber = this.service.auditPageNumber;
  readonly lastRefreshedAt = this.service.lastRefreshedAt;

  ngOnInit(): void {
    // The first paint is a real read, not an empty shell.
    this.service.refreshAll();
    // Seed the gate from the document state at mount — a page opened in a
    // hidden browser tab never issues a poll tick until it is shown.
    this.browserVisible = document.visibilityState !== 'hidden';
    document.addEventListener('visibilitychange', this.onVisibilityChange);
    // The poll — every POLL_INTERVAL_MS (10 s), until the component dies.
    timer(
      McpsGatewayService.POLL_INTERVAL_MS,
      McpsGatewayService.POLL_INTERVAL_MS,
    )
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe(() => {
        // The VISIBILITY GATE — a hidden browser tab issues NO request.
        // The cadence itself is unchanged (the 10 s budget is the same for
        // a VISIBLE tab); a suppressed tick simply spends nothing. The
        // service's own overlap guard keeps a suppressed-and-then-refocused
        // cycle from piling up requests.
        if (!this.browserVisible) {
          return;
        }
        this.service.refreshAll();
      });
  }

  ngOnDestroy(): void {
    // The visibility listener is detached (no leak) and a pending refocus
    // refresh is dropped (a destroyed component must not fire a request).
    // The rxjs subscription is already torn down by ``takeUntilDestroyed``.
    document.removeEventListener('visibilitychange', this.onVisibilityChange);
    if (this.refocusTimer !== null) {
      clearTimeout(this.refocusTimer);
      this.refocusTimer = null;
    }
  }

  /**
   * The visibility gate — a hidden browser tab stops the gateway poll,
   * and a refocus produces EXACTLY ONE immediate refresh (debounced by
   * ``REFOCUS_DEBOUNCE_MS`` so a rapid tab-switch storm cannot issue a
   * burst of reads). No catch-up burst is ever queued: the single refocus
   * refresh IS the catch-up.
   */
  private readonly onVisibilityChange = (): void => {
    const visible = document.visibilityState !== 'hidden';
    const wasHidden = !this.browserVisible;
    this.browserVisible = visible;
    if (!visible || !wasHidden) {
      return;
    }
    if (this.refocusTimer !== null) {
      clearTimeout(this.refocusTimer);
    }
    this.refocusTimer = setTimeout(() => {
      this.refocusTimer = null;
      if (!this.browserVisible) {
        return;
      }
      this.service.refreshAll();
    }, REFOCUS_DEBOUNCE_MS);
  }

  // ── Actions ──────────────────────────────────────────────────────────

  /** The manual Refresh button — an immediate `refreshAll()`. */
  onRefresh(): void {
    this.service.refreshAll();
  }

  /** The audit pager — forward one page (the service guards the bound). */
  onNextAuditPage(): void {
    this.service.nextAuditPage();
  }

  /** The audit pager — back one page (the service guards the bound). */
  onPrevAuditPage(): void {
    this.service.prevAuditPage();
  }

  // ── Formatting helpers ───────────────────────────────────────────────

  /**
   * Format an ISO-8601 stamp as a readable UTC string
   * (`2026-10-22 07:30:52 UTC`).
   *
   * Two robustness notes:
   *
   * * The BE emits Python `datetime.isoformat()` stamps, which carry SIX
   *   fractional digits (`...T07:30:52.512000+00:00`). The ECMA-262
   *   Date Time String Format allows only three, and a strict parser
   *   would answer `Invalid Date` — so the fraction is trimmed to
   *   milliseconds before parsing.
   * * The fields are read with the `getUTC*` accessors, so the rendered
   *   stamp is UTC regardless of the browser's timezone (an operator in
   *   +07 sees the SAME string as one in UTC — the daemon is the clock).
   *
   * A missing or unparsable stamp renders as an em dash — never
   * "Invalid Date".
   */
  formatUtc(iso: string | null | undefined): string {
    if (!iso) {
      return '—';
    }
    const normalized = iso.replace(/(\.\d{3})\d+/, '$1');
    const parsed = new Date(normalized);
    if (Number.isNaN(parsed.getTime())) {
      return '—';
    }
    return McpsGatewayComponent.formatDate(parsed);
  }

  /** The same rendering for a client-side `Date` (the refresh stamp). */
  formatUtcDate(value: Date | null | undefined): string {
    if (!value) {
      return '—';
    }
    return McpsGatewayComponent.formatDate(value);
  }

  /** The badge class of a presence value — unknown values stay neutral. */
  presenceBadgeClass(presence: string): string {
    switch (presence) {
      case 'online':
        return 'badge badge-online';
      case 'stale':
        return 'badge badge-stale';
      case 'offline':
        return 'badge badge-offline';
      default:
        return 'badge badge-unknown';
    }
  }

  /** The badge class of an audit outcome — unknown values stay neutral. */
  outcomeBadgeClass(outcome: string): string {
    switch (outcome) {
      case 'ok':
        return 'badge badge-online';
      case 'error':
        return 'badge badge-error';
      case 'denied':
        return 'badge badge-stale';
      case 'timeout':
        return 'badge badge-timeout';
      default:
        return 'badge badge-unknown';
    }
  }

  /** The args hash, truncated for the cell — the full value is the title. */
  truncateHash(hash: string | null | undefined): string {
    if (!hash) {
      return '—';
    }
    return hash.length > 12 ? `${hash.slice(0, 12)}…` : hash;
  }

  /**
   * The empty-state copy of a section.
   *
   * The gateway-disabled case gets its own wording: an empty roster
   * while the gateway is OFF is EXPECTED (the read routes stay
   * registered), and saying "nothing is enrolled" would be a lie.
   */
  emptyCopy(subject: 'machines' | 'sessions' | 'audit'): string {
    if (!this.gatewayEnabled()) {
      return `The remote-hands gateway is disabled — no ${subject} are reported.`;
    }
    return `No ${subject} to show yet.`;
  }

  /** The shared UTC formatter (a static so both helpers use one code path). */
  private static formatDate(value: Date): string {
    const pad = (n: number): string => String(n).padStart(2, '0');
    return (
      `${value.getUTCFullYear()}-${pad(value.getUTCMonth() + 1)}-${pad(value.getUTCDate())} ` +
      `${pad(value.getUTCHours())}:${pad(value.getUTCMinutes())}:${pad(value.getUTCSeconds())} UTC`
    );
  }
}
