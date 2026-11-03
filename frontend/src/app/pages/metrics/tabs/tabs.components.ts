import { Type } from '@angular/core';

import { METRICS_TABS, MetricsTabEntry } from '../tabs.manifest';
import { ResourceTabComponent } from './resource-tab.component';
import { RuntimeTabComponent } from './runtime-tab.component';
import { DbPoolTabComponent } from './db-pool-tab.component';
import { JobsTabComponent } from './jobs-tab.component';
import { InstancesTabComponent } from './instances-tab.component';
import { CheckpointTabComponent } from './checkpoint-tab.component';
import { McpTabComponent } from './mcp-tab.component';

/**
 * The manifest → component MAP (T1 phase 2, task 2.4/2.5).
 *
 * The manifest is DATA (which family belongs to which module); the map
 * is CODE (which component renders which module). They are separate on
 * purpose — the manifest must stay importable by the tab bodies without
 * an import cycle — and the shell's spec asserts the map covers the
 * manifest EXACTLY: one entry per tab, no extra, no missing. A tab
 * added to the manifest without a body therefore fails the spec rather
 * than rendering an empty tab.
 */
export const METRICS_TAB_COMPONENTS: Readonly<Record<string, Type<unknown>>> = {
  resource: ResourceTabComponent,
  runtime: RuntimeTabComponent,
  'db-pool': DbPoolTabComponent,
  jobs: JobsTabComponent,
  instances: InstancesTabComponent,
  checkpoint: CheckpointTabComponent,
  mcp: McpTabComponent,
};

/** One tab of the shell — the manifest entry plus its body. */
export interface MetricsTabBinding {
  readonly entry: MetricsTabEntry;
  readonly component: Type<unknown>;
}

/** The shell's tab list — the manifest, in manifest order. */
export const METRICS_TAB_BINDINGS: readonly MetricsTabBinding[] = METRICS_TABS.map((entry) => ({
  entry,
  component: METRICS_TAB_COMPONENTS[entry.id],
}));
