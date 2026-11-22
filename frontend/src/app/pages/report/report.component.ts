import { Component, inject, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { ReportService } from './report.service';
import type { MaintenanceReportRun } from '../../models';

/**
 * Report — the READ-ONLY operator page for the Central Maintenance
 * System (Phase 4, D-E / BR-18).
 *
 * Reads the run list from `GET /api/maintenance-reports` and renders a
 * selected run's report via its `report_view_url` (live_views
 * `maintenance-reports` root). The page never writes: the report is
 * authored by the daemon's `run-report-writer` worker child.
 *
 * GEAR MENU ONLY — there is NO top-level header link (the header nav
 * carries the primary destinations only). Lazy route registered before
 * the catch-all wildcard in `app.routes.ts`.
 */
@Component({
  selector: 'app-report',
  standalone: true,
  imports: [
    MatButtonModule,
    MatIconModule,
    MatProgressSpinnerModule,
  ],
  templateUrl: './report.component.html',
  styleUrl: './report.component.scss',
})
export class ReportComponent {
  readonly service = inject(ReportService);

  readonly runs = this.service.runs;
  readonly loading = this.service.loading;
  readonly error = this.service.error;

  /** The run whose report is currently rendered, or `null`. */
  readonly selected = signal<MaintenanceReportRun | null>(null);

  constructor() {
    this.service.loadRuns().subscribe();
  }

  select(run: MaintenanceReportRun): void {
    this.selected.set(run);
  }

  clearSelection(): void {
    this.selected.set(null);
  }

  trackByRunId(_index: number, run: MaintenanceReportRun): string {
    return run.run_id;
  }
}
