// The TAB MANIFEST spec (T1 phase 2, task 2.1/2.2/2.3/2.12/2.14).
//
// House pattern (mirrors `metrics.bindings.pins.spec.ts`): a typed
// import of the production module + a source-grep leg over the frozen
// model files.
//
// The load-bearing pins:
//
//   * AM-8 — the manifest type expresses ALL FOUR dispositions, and each
//     HIDDEN module binds the NO-FAMILY variant (there is no empty-array
//     sentinel anywhere in the manifest);
//   * the family BINDING per tab (plan-overview §4.1) — including the
//     BRACKETED planned families, which are bound NOW and gated by
//     availability;
//   * the manifest carries NO wire-derivable field (the honest-drift
//     class the plan's risk table names);
//   * AM-19 — the custom slot's window vocabulary is a SUBSET of the
//     frozen `ResourcesWindow` union (derived from the model source,
//     never hardcoded here);
//   * AM-20 — a per-tab copy variant is a KEY of the copy SSOT, never
//     free text.

import { readFileSync } from 'fs';
import { join } from 'path';

import {
  METRICS_CENTRAL_ENTRIES,
  METRICS_CUSTOM_ENTRIES,
  METRICS_DEFAULT_TAB_ID,
  METRICS_DROPPED_MODULES,
  METRICS_HIDDEN_MODULES,
  METRICS_MODULES,
  METRICS_PLANNED_MODULES,
  METRICS_TABS,
  MetricsCentralTab,
  MetricsModuleEntry,
  isMetricsTabId,
  metricsCopyVariantFor,
  metricsTabById,
} from './tabs.manifest';
import { METRICS_DOOR_ERROR_COPY } from '../../models/metrics.model';

const metricsDir = join(__dirname);
const manifestSrc = readFileSync(join(metricsDir, 'tabs.manifest.ts'), 'utf-8');
const resourcesModelSrc = readFileSync(
  join(metricsDir, '..', '..', 'models', 'resources.models.ts'),
  'utf-8',
);

/**
 * The FROZEN `ResourcesWindow` union, DERIVED from the model source —
 * the house rule: a value duplicated in a spec can drift from the
 * frozen contract it pins.
 */
function frozenResourcesWindows(): string[] {
  const match = resourcesModelSrc.match(
    /export type ResourcesWindow\s*=\s*([^;]+);/,
  );
  if (!match) {
    throw new Error('the frozen ResourcesWindow union was not found in the model');
  }
  return [...match[1].matchAll(/'([^']+)'/g)].map((m) => m[1]);
}

const CENTRAL_TABS = METRICS_CENTRAL_ENTRIES;
const CUSTOM_TABS = METRICS_CUSTOM_ENTRIES;

describe('the tab manifest — the 7 module tabs (2.1 / 2.2)', () => {
  it('carries exactly 7 tabs', () => {
    expect(METRICS_TABS).toHaveLength(7);
  });

  it('the tab ids are unique and stable', () => {
    const ids = METRICS_TABS.map((tab) => tab.id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(ids).toEqual([
      'resource',
      'runtime',
      'db-pool',
      'jobs',
      'instances',
      'checkpoint',
      'mcp',
    ]);
  });

  it('the default tab is tab 1 — Resources (the `?tab=` fallback)', () => {
    expect(METRICS_DEFAULT_TAB_ID).toBe('resource');
    expect(METRICS_TABS[0].id).toBe(METRICS_DEFAULT_TAB_ID);
    expect(METRICS_TABS[0].slot).toBe('custom');
  });

  it('the labels match the plan-overview §4.1 table', () => {
    expect(METRICS_TABS.map((tab) => tab.label)).toEqual([
      'Resources',
      'Runtime',
      'DB Pool',
      'Jobs / Queue',
      'Instances',
      'Checkpoint',
      'MCP',
    ]);
  });

  it('exactly ONE tab is the custom slot — the other six are central', () => {
    expect(CUSTOM_TABS).toHaveLength(1);
    expect(CENTRAL_TABS).toHaveLength(6);
  });

  it('binds the EXACT family roster per tab (plan-overview §4.1)', () => {
    const bindings = Object.fromEntries(
      CENTRAL_TABS.map((tab) => [tab.id, [...tab.familyIds]]),
    );
    expect(bindings).toEqual({
      runtime: ['F9', 'F1', 'F3'],
      'db-pool': ['F4', 'F5', 'F6'],
      jobs: ['F10', 'F12'],
      instances: ['F13', 'F11'],
      checkpoint: ['F8'],
      mcp: ['F17'],
    });
  });

  it('binds the BRACKETED planned families NOW (R-ii / R-i land with ZERO FE change)', () => {
    // [F1, F3] → Runtime; [F6] → DB Pool. They are dark today
    // (false-dark), but they are BOUND — the availability gate, not the
    // binding, is what hides them.
    const runtime = metricsTabById('runtime') as MetricsCentralTab;
    const dbPool = metricsTabById('db-pool') as MetricsCentralTab;
    expect(runtime.familyIds).toContain('F1');
    expect(runtime.familyIds).toContain('F3');
    expect(dbPool.familyIds).toContain('F6');
  });

  it('declares the stat-role families of each central tab', () => {
    const statFamilies = Object.fromEntries(
      CENTRAL_TABS.map((tab) => [tab.id, tab.statFamilyIds ?? []]),
    );
    expect(statFamilies).toEqual({
      runtime: ['F3'],
      'db-pool': [],
      jobs: ['F12'],
      instances: [],
      checkpoint: [],
      mcp: ['F17'],
    });
  });

  it('every bound family id is a REGISTERED family id (the E-1 census union)', () => {
    const bound = CENTRAL_TABS.flatMap((tab) => [...tab.familyIds]);
    const registered = new Set([
      'F0.3',
      'F1',
      'F3',
      'F4',
      'F5',
      'F6',
      'F7',
      'F8',
      'F9',
      'F10',
      'F11',
      'F12',
      'F13',
      'F17',
      'F18',
      'F21',
    ]);
    for (const id of bound) {
      expect(registered.has(id)).toBe(true);
    }
  });
});

describe('the tab manifest — the FOUR dispositions (AM-8, 2.1 / 2.3)', () => {
  it('every disposition is REPRESENTED in the data (none is a dead variant)', () => {
    const dispositions = new Set(METRICS_MODULES.map((module) => module.disposition));
    expect([...dispositions].sort()).toEqual([
      'bound',
      'dropped',
      'hidden',
      'planned-unbound',
    ]);
  });

  it('every HIDDEN module binds the NO-FAMILY variant (no empty familyIds)', () => {
    expect(METRICS_HIDDEN_MODULES.length).toBeGreaterThan(0);
    for (const module of METRICS_HIDDEN_MODULES) {
      expect(module.disposition).toBe('hidden');
      expect('familyIds' in module).toBe(false);
      expect((module as { familyIds?: unknown }).familyIds).toBeUndefined();
    }
  });

  it('the CUSTOM tab carries no familyIds either (its module is its own source)', () => {
    for (const tab of CUSTOM_TABS) {
      expect('familyIds' in tab).toBe(false);
    }
  });

  it('no entry anywhere carries an EMPTY family array (the empty-array sentinel)', () => {
    for (const module of METRICS_MODULES) {
      if ('familyIds' in module) {
        expect(module.familyIds.length).toBeGreaterThan(0);
      }
    }
    expect(manifestSrc).not.toMatch(/familyIds:\s*\[\s*\]/);
  });

  it('the PLANNED-unbound modules name their gap AND their owner', () => {
    expect(METRICS_PLANNED_MODULES.length).toBeGreaterThan(0);
    for (const module of METRICS_PLANNED_MODULES) {
      expect(module.disposition).toBe('planned-unbound');
      expect(module.trackingNote.length).toBeGreaterThan(10);
      expect(module.owner.length).toBeGreaterThan(0);
      expect(module.familyIds.length).toBeGreaterThan(0);
    }
  });

  it('the PLANNED-unbound roster is the plan\'s four (F7, F18, F21, F0.3)', () => {
    expect(METRICS_PLANNED_MODULES.flatMap((m) => [...m.familyIds]).sort()).toEqual([
      'F0.3',
      'F18',
      'F21',
      'F7',
    ]);
  });

  it('the HIDDEN roster is the plan\'s three no-source modules', () => {
    expect(METRICS_HIDDEN_MODULES.map((m) => m.id)).toEqual([
      'llm-token-usage',
      'llm-request-latency',
      'source-breakers',
    ]);
    for (const module of METRICS_HIDDEN_MODULES) {
      expect(module.trackingNote.length).toBeGreaterThan(10);
      expect(module.owner.length).toBeGreaterThan(0);
    }
  });

  it('the DROPPED roster names only OUT families (never planned)', () => {
    expect(METRICS_DROPPED_MODULES.flatMap((m) => [...m.familyIds]).sort()).toEqual([
      'F16',
      'F19',
      'F2',
      'F20',
      'F22',
    ]);
    for (const module of METRICS_DROPPED_MODULES) {
      expect(module.reason.length).toBeGreaterThan(10);
    }
  });

  it('no PLANNED / HIDDEN / DROPPED entry contributes a tab, a panel or a route', () => {
    const tabIds = new Set<string>(METRICS_TABS.map((tab) => tab.id));
    for (const module of [
      ...METRICS_PLANNED_MODULES,
      ...METRICS_HIDDEN_MODULES,
      ...METRICS_DROPPED_MODULES,
    ]) {
      expect(tabIds.has(module.id)).toBe(false);
      expect(isMetricsTabId(module.id)).toBe(false);
      expect(metricsTabById(module.id)).toBeNull();
    }
  });
});

describe('the tab manifest — it carries NO wire-derivable field', () => {
  const ALLOWED_KEYS: Record<string, readonly string[]> = {
    bound: ['disposition', 'slot', 'id', 'label', 'familyIds', 'statFamilyIds', 'allowedWindows', 'copyVariant'],
    'planned-unbound': ['disposition', 'id', 'label', 'familyIds', 'trackingNote', 'owner'],
    hidden: ['disposition', 'id', 'label', 'trackingNote', 'owner'],
    dropped: ['disposition', 'id', 'label', 'familyIds', 'reason'],
  };

  it('every entry carries ONLY the keys its disposition allows', () => {
    for (const module of METRICS_MODULES) {
      const allowed = ALLOWED_KEYS[module.disposition];
      expect(allowed).toBeDefined();
      for (const key of Object.keys(module)) {
        expect(allowed).toContain(key);
      }
    }
  });

  it('no entry carries an availability / role / labels / buckets field', () => {
    for (const module of METRICS_MODULES) {
      const keys = Object.keys(module);
      for (const wireField of [
        'availability',
        'role',
        'chart_role',
        'labels',
        'buckets',
        'live_series_count',
        'help',
      ]) {
        expect(keys).not.toContain(wireField);
      }
    }
  });

  it('the manifest source declares no availability-shaped member', () => {
    expect(manifestSrc).not.toMatch(/^\s*availability\s*:/m);
    expect(manifestSrc).not.toMatch(/^\s*chart_role\s*:/m);
    expect(manifestSrc).not.toMatch(/^\s*live_series_count\s*:/m);
  });
});

describe('the tab manifest — the window vocabulary (AM-19, 2.12)', () => {
  it('the custom slot\'s allowedWindows is a SUBSET of the frozen ResourcesWindow union', () => {
    const frozen = frozenResourcesWindows();
    expect(frozen).toEqual(['15m', '1h', '24h', '7d']);
    const custom = CUSTOM_TABS[0];
    expect(custom.allowedWindows.length).toBeGreaterThan(0);
    for (const window of custom.allowedWindows) {
      expect(frozen).toContain(window);
    }
  });

  it('the custom slot serves the module\'s OWN ladder (15m / 1h / 24h / 7d)', () => {
    expect([...CUSTOM_TABS[0].allowedWindows]).toEqual(['15m', '1h', '24h', '7d']);
  });

  it('the central slots serve the door ladder (15m / 30m / 1h / 2h)', () => {
    for (const tab of CENTRAL_TABS) {
      expect([...tab.allowedWindows]).toEqual(['15m', '30m', '1h', '2h']);
    }
  });

  it('the central ladder is its OWN vocabulary — only 15m / 1h overlap the custom one', () => {
    // The door 400s an out-of-union window, and the central door SILENTLY
    // clamps a window beyond its 2 h ceiling (ADJ-iii). So the central
    // ladder is deliberately NOT a subset of the resources vocabulary —
    // the frame renders the windows the selected tab cannot serve as
    // disabled (phase 3's control).
    const frozen = frozenResourcesWindows();
    const central = CENTRAL_TABS[0].allowedWindows;
    const shared = central.filter((window) => frozen.includes(window));
    const centralOnly = central.filter((window) => !frozen.includes(window));
    expect([...shared]).toEqual(['15m', '1h']);
    expect([...centralOnly]).toEqual(['30m', '2h']);
  });
});

describe('the tab manifest — the copy precedence rule (AM-20, 2.14)', () => {
  it('every per-tab copy VARIANT is a KEY of the copy SSOT (never free text)', () => {
    const known = Object.keys(METRICS_DOOR_ERROR_COPY);
    for (const tab of CENTRAL_TABS) {
      if (tab.copyVariant === undefined) {
        continue;
      }
      expect(known).toContain(tab.copyVariant);
    }
  });

  it('the resolver returns the variant for a known state and null for an unknown one', () => {
    const known = Object.keys(METRICS_DOOR_ERROR_COPY);
    const runtime = metricsTabById('runtime') as MetricsCentralTab;
    expect(metricsCopyVariantFor(runtime, known)).toBe('auth_not_configured');
    expect(metricsCopyVariantFor(runtime, ['scope_insufficient'])).toBeNull();
  });

  it('the custom slot declares no door-state variant (it has no door)', () => {
    const custom = CUSTOM_TABS[0];
    expect('copyVariant' in custom).toBe(false);
    expect(metricsCopyVariantFor(custom, Object.keys(METRICS_DOOR_ERROR_COPY))).toBeNull();
  });

  it('no manifest entry carries free-text copy', () => {
    for (const module of METRICS_MODULES as readonly MetricsModuleEntry[]) {
      const keys = Object.keys(module);
      for (const copyField of ['copy', 'message', 'text', 'hint']) {
        expect(keys).not.toContain(copyField);
      }
    }
  });
});

describe('the tab manifest — the id helpers', () => {
  it('resolves a known id and refuses an unknown / legacy one', () => {
    expect(metricsTabById('mcp')?.label).toBe('MCP');
    expect(metricsTabById('overview')).toBeNull();
    expect(metricsTabById('charts')).toBeNull();
    expect(metricsTabById('families')).toBeNull();
    expect(metricsTabById(null)).toBeNull();
    expect(metricsTabById(undefined)).toBeNull();
    expect(metricsTabById('')).toBeNull();
  });
});
