// Report (Central Maintenance System) — the READ-ONLY operator surface
// (Phase 4, D-E). Mirrors the BE wire contract VERBATIM:
//
//   GET /api/maintenance-reports  → JSON array of MaintenanceReportRun
//
// Conventions (house FE-model conventions — mcps-gateway.model.ts is the
// precedent):
//
// * Field names are the WIRE names (snake_case), exactly as they arrive.
// * Timestamps are ISO-8601 UTC strings.
// * View URLs are relative `/views/maintenance-reports/...` paths served
//   by the daemon's live_views root (BR-18) — the FE never rewrites them.

export type MaintenanceReportTriggerType = 'scheduled' | 'manual';
export type MaintenanceReportOverallStatus = 'ok' | 'degraded' | 'failed';

export interface MaintenanceReportRun {
  run_id: string;
  trigger_type: MaintenanceReportTriggerType | string;
  started_at: string;
  ended_at: string;
  overall_status: MaintenanceReportOverallStatus | string;
  report_view_url: string;
  receipt_view_url: string;
}
