/**
 * The FE-local TYPED TAB MANIFEST (T1 phase 2, task 2.1).
 *
 * ── THE REGISTRATION-ONLY CONTRACT (W4 — stated HERE, ONCE) ─────────────
 *
 * **Adding a series to a wired module is REGISTRATION-ONLY.** It means
 * exactly TWO FE-local typed-table edits:
 *
 *   1. **the family binding** — this manifest, and
 *   2. **a metric→unit vocabulary entry** — the unit map (phase 6's 6.3).
 *
 * A genuinely new metric NAME with no vocabulary entry renders "unit
 * unknown" (never a guess) until the vocabulary names it — a typed-table
 * edit, not a code change. Only a NEW TAB costs an FE code commit (a tab
 * body + one line in the shell's component map).
 *
 * ── WHAT THE MANIFEST MAY AND MAY NOT CARRY ───────────────────────────
 *
 * The manifest is a BINDING table, never a second copy of the wire.
 * Everything the door derives is read at RUNTIME and NEVER duplicated
 * here:
 *
 *   * `chart_role` / `role`      — read from the catalog projection;
 *   * labels, buckets, help      — read from the catalog projection;
 *   * availability (+ its reason) — read from the catalog projection;
 *   * the metric names           — read from the catalog projection.
 *
 * What the manifest DOES carry is FE-local intent: WHICH family belongs
 * to WHICH module, which families feed a module's stat strip, which
 * window vocabulary a module may serve, and — for a module with no
 * source at all — the tracking note that names the gap and its owner.
 *
 * ── THE FOUR MODULE DISPOSITIONS (AM-8) ───────────────────────────────
 *
 * The type expresses all FOUR, so "registered but no series yet" and
 * "not instrumented" are never type-indistinguishable:
 *
 *   | disposition        | meaning                                        |
 *   |--------------------|------------------------------------------------|
 *   | `bound`            | the module IS a tab; its families are bound    |
 *   |                    | and availability-gated (live or dark)          |
 *   | `planned-unbound`  | a REGISTERED source exists but nothing binds   |
 *   |                    | it yet (`series_absent`)                       |
 *   | `hidden`           | NO source at all — renders NOTHING              |
 *   | `dropped`          | never planned                                  |
 *
 * A module with no family carries NO `familyIds` member at all (there is
 * no empty-array sentinel anywhere in this file): the `hidden` and the
 * `custom` variants simply have no such member, and the `bound` /
 * `planned-unbound` variants carry a NON-EMPTY tuple.
 *
 * ── THE COPY PRECEDENCE RULE (AM-20 / IMPL-19) ─────────────────────────
 *
 * The frame owns the STATE; the manifest may carry a per-tab **variant**
 * of a state's copy — and a variant is a KEY into the copy SSOT
 * (`MetricsDoorErrorCode`), NEVER free text. The copy SSOT itself (one
 * structured const per class) is phase 8's work (IMPL-18); this file
 * fixes only the precedence rule and its own role in it: the manifest
 * REFERENCES the state vocabulary, it never forks it.
 */

import { MetricsDoorErrorCode } from '../../models/metrics.model';
import { ResourcesWindow } from '../../models/resources.models';

/**
 * The REGISTERED family ids — the E-1 census (17 families).
 *
 * The census is re-derived from the registry SSOT
 * (`daemon/services/metrics_query/wiring/registry.py`'s `FAMILY_SPECS`
 * + `adjudication.py`'s `REGISTERED_FAMILIES`) at every lane freeze; a
 * family id that is not registered can never be bound here.
 */
export type MetricsRegisteredFamilyId =
  | 'F0.3'
  | 'F1'
  | 'F3'
  | 'F4'
  | 'F5'
  | 'F6'
  | 'F7'
  | 'F8'
  | 'F9'
  | 'F10'
  | 'F11'
  | 'F12'
  | 'F13'
  | 'F17'
  | 'F18'
  | 'F21'
  | 'F23';

/**
 * The families the adjudication ruled OUT — DROPPED, never planned, and
 * therefore never registered. Their names are force-absent from the
 * registry, so they can only ever appear in a `dropped` entry.
 */
export type MetricsDroppedFamilyId = 'F2' | 'F16' | 'F19' | 'F20' | 'F22';

/**
 * The CENTRAL door's window ladder. `2h` is the substrate ceiling
 * (ADJ-iii) — the door silently clamps a longer request, so the FE never
 * offers more than this on a central tab.
 */
export type MetricsCentralWindow = '15m' | '30m' | '1h' | '2h';

/** A NON-EMPTY tuple — an empty array is not a legal binding. */
type NonEmpty<T> = readonly [T, ...T[]];

/**
 * A CENTRAL tab — its data comes from the central door
 * (`/api/metrics/families` · `/snapshot` · `/series`).
 */
export interface MetricsCentralTab {
  readonly disposition: 'bound';
  readonly slot: 'central';
  /** The stable slug — the `?tab=` value and the route's redirect target. */
  readonly id: string;
  readonly label: string;
  /** The families this module binds (the door's family ids). */
  readonly familyIds: NonEmpty<MetricsRegisteredFamilyId>;
  /**
   * The families whose STAT-role series feed this module's stat strip.
   *
   * ABSENT means "this module has no stat-role family" — there is no
   * empty-array sentinel (council fold coding-A-2: the member is typed
   * `NonEmpty` like `familyIds`, so `statFamilyIds: []` is not
   * expressible either). The strip is fed by
   * `GET /api/metrics/snapshot?family=<F>`.
   */
  readonly statFamilyIds?: NonEmpty<MetricsRegisteredFamilyId>;
  /** The window vocabulary this module may serve. */
  readonly allowedWindows: NonEmpty<MetricsCentralWindow>;
  /**
   * The window this module opens on — the snap target when the operator
   * switches to a tab that cannot serve the current window (3.2).
   *
   * The manifest OWNS it (the frame never owns a second list), and it is
   * always a member of `allowedWindows` — a snap can never land outside
   * the tab's own vocabulary.
   */
  readonly defaultWindowS: MetricsCentralWindow;
  /**
   * The per-tab copy VARIANT of a door state — a KEY into the copy SSOT,
   * never free text (AM-20 / IMPL-19).
   */
  readonly copyVariant?: MetricsDoorErrorCode;
}

/**
 * The CUSTOM tab — tab 1's own module over its own support endpoints.
 *
 * It has NO central family (its data is not the door's), so it carries
 * no `familyIds` member at all. Its window vocabulary is its OWN and is
 * a SUBSET of the frozen `ResourcesWindow` union — the door 400s
 * anything else (`daemon/routers/resources.py:120-130`), so the subset
 * relation is enforced AT COMPILE TIME by the `ResourcesWindow` type.
 */
export interface MetricsCustomTab {
  readonly disposition: 'bound';
  readonly slot: 'custom';
  readonly id: string;
  readonly label: string;
  /** The window vocabulary — typed as the frozen union, so drift fails the build. */
  readonly allowedWindows: NonEmpty<ResourcesWindow>;
  /**
   * The window this module opens on — the snap target when the operator
   * switches to a tab that cannot serve the current window (3.2).
   *
   * It matches the retired timeline panel's own default (the panel's
   * window-ladder constant was retired with the picker in T1 phase 5;
   * the manifest entry is now that default's only home), so the
   * relocated module opens on the same window it always did.
   */
  readonly defaultWindowS: ResourcesWindow;
}

/** A module that IS a tab. */
export type MetricsTabEntry = MetricsCentralTab | MetricsCustomTab;

/**
 * A REGISTERED source that is not bound to a module yet
 * (`series_absent` — the door knows the family, it publishes no series).
 */
export interface MetricsPlannedModule {
  readonly disposition: 'planned-unbound';
  readonly id: string;
  readonly label: string;
  /** The registered families this module will bind when it goes live. */
  readonly familyIds: NonEmpty<MetricsRegisteredFamilyId>;
  /** The gap — named, never hidden. */
  readonly trackingNote: string;
  /** The lane that owns the gap. */
  readonly owner: string;
}

/**
 * A module with NO registered source — HIDDEN entirely.
 *
 * It renders NOTHING: no tab, no panel, no route. The entry exists so
 * the gap is documented (and findable) instead of silently absent.
 */
export interface MetricsHiddenModule {
  readonly disposition: 'hidden';
  readonly id: string;
  readonly label: string;
  /** The gap — named, never hidden. */
  readonly trackingNote: string;
  /** The lane that owns the gap. */
  readonly owner: string;
}

/** A module that was DROPPED — never planned. */
export interface MetricsDroppedModule {
  readonly disposition: 'dropped';
  readonly id: string;
  readonly label: string;
  /** The families the adjudication ruled out. */
  readonly familyIds: NonEmpty<MetricsDroppedFamilyId>;
  /** Why it was dropped. */
  readonly reason: string;
}

/** Every module the manifest knows about — all four dispositions. */
export type MetricsModuleEntry =
  | MetricsTabEntry
  | MetricsPlannedModule
  | MetricsHiddenModule
  | MetricsDroppedModule;

/**
 * The 8 MODULE TABS (plan-overview §4.1 + the nest-anomaly lane's
 * phase 4), in tab order.
 *
 * The BRACKETED planned families are BOUND and gated by availability
 * (ADJ-vi). Both blocking defects are now FIXED, so they appear with
 * ZERO FE change: R-i (`capture_active`, the never-wired wedge-capture
 * fact) and R-ii (the census `gated` polarity — the `census inverted`
 * defect) both landed server-side. The F1/F3 families report live when
 * the wedge-capture watchdog is live (else dark/capture_off); F6 reports
 * live when the census is enabled AND running AND publishing (else
 * dark/census_gated).
 */
export const METRICS_TABS = [
  {
    disposition: 'bound',
    slot: 'custom',
    id: 'resource',
    label: 'Resources',
    // The custom module's OWN ladder (its own vocabulary, NOT the
    // door's) — a subset of the frozen `ResourcesWindow` union.
    allowedWindows: ['15m', '1h', '24h', '7d'],
    // The module opens on the retired timeline panel's own default.
    defaultWindowS: '1h',
  },
  {
    disposition: 'bound',
    slot: 'central',
    id: 'runtime',
    label: 'Runtime',
    // F9 (readiness composite) + [F1, F3] (the wedge-capture families).
    // Both blockers are FIXED: R-i wires `capture_active` from the
    // wedge-capture process fact, so F1/F3 report live whenever the
    // watchdog is live (else dark/capture_off).
    familyIds: ['F9', 'F1', 'F3'],
    // F3 is stat-primary (it is not in the chart-primary roster).
    statFamilyIds: ['F3'],
    allowedWindows: ['15m', '30m', '1h', '2h'],
    // The central ladder's default — the window the tab asked for before
    // the frame owned it (the hardcoded 1 h).
    defaultWindowS: '1h',
    copyVariant: 'auth_not_configured',
  },
  {
    disposition: 'bound',
    slot: 'central',
    id: 'db-pool',
    label: 'DB Pool',
    // F4, F5 (live) + [F6] (the PG census). R-ii fixed the `census
    // inverted` polarity, so F6 reports live whenever the census is
    // enabled AND running AND publishing (else dark/census_gated).
    familyIds: ['F4', 'F5', 'F6'],
    allowedWindows: ['15m', '30m', '1h', '2h'],
    // The central ladder's default — the window the tab asked for before
    // the frame owned it (the hardcoded 1 h).
    defaultWindowS: '1h',
    copyVariant: 'auth_not_configured',
  },
  {
    disposition: 'bound',
    slot: 'central',
    id: 'jobs',
    label: 'Jobs / Queue',
    familyIds: ['F10', 'F12'],
    // F12 (the sweep counters) is stat-primary.
    statFamilyIds: ['F12'],
    allowedWindows: ['15m', '30m', '1h', '2h'],
    // The central ladder's default — the window the tab asked for before
    // the frame owned it (the hardcoded 1 h).
    defaultWindowS: '1h',
    copyVariant: 'auth_not_configured',
  },
  {
    disposition: 'bound',
    slot: 'central',
    id: 'instances',
    label: 'Instances',
    familyIds: ['F13', 'F11'],
    allowedWindows: ['15m', '30m', '1h', '2h'],
    // The central ladder's default — the window the tab asked for before
    // the frame owned it (the hardcoded 1 h).
    defaultWindowS: '1h',
    copyVariant: 'auth_not_configured',
  },
  {
    disposition: 'bound',
    slot: 'central',
    id: 'checkpoint',
    label: 'Checkpoint',
    familyIds: ['F8'],
    allowedWindows: ['15m', '30m', '1h', '2h'],
    // The central ladder's default — the window the tab asked for before
    // the frame owned it (the hardcoded 1 h).
    defaultWindowS: '1h',
    copyVariant: 'auth_not_configured',
  },
  {
    disposition: 'bound',
    slot: 'central',
    id: 'mcp',
    label: 'MCP',
    familyIds: ['F17'],
    // F17 (the init-guard snapshot) is stat-primary.
    statFamilyIds: ['F17'],
    allowedWindows: ['15m', '30m', '1h', '2h'],
    // The central ladder's default — the window the tab asked for before
    // the frame owned it (the hardcoded 1 h).
    defaultWindowS: '1h',
    copyVariant: 'auth_not_configured',
  },
  {
    disposition: 'bound',
    slot: 'central',
    id: 'nest-reports',
    // The USER-VISIBLE label is deliberately NOT "Anomalies": the
    // Resources tab (tab 1) already renders an "Anomalies" panel
    // (the resource-anomaly family) and the two show unrelated
    // data. The slug IS `nest-reports` — it now MATCHES the label.
    label: 'Nest Reports',
    // F23 — the anomaly family (phase 3).
    familyIds: ['F23'],
    // F23's snapshot values feed the stat strip (the strip renders the
    // LIVE series of the named families — the shared body fetches
    // GET /api/metrics/snapshot?family=<F>). The family's closed
    // space is 67 series (decisions.md F23: 13 + 39 + 13 + 1 + 1 —
    // the fifth member is the capacity-exhausted counter), so the
    // strip's worst case is 67 cards, bounded by the closed
    // vocabulary; the LIVE count is the categories/severities
    // actually present.
    statFamilyIds: ['F23'],
    allowedWindows: ['15m', '30m', '1h', '2h'],
    // The central ladder's default — the window the tab asked for before
    // the frame owned it (the hardcoded 1 h).
    defaultWindowS: '1h',
    copyVariant: 'auth_not_configured',
  },
] as const satisfies readonly MetricsTabEntry[];

/** The DEFAULT tab — tab 1 (Resources). */
export const METRICS_DEFAULT_TAB_ID = 'resource';

/**
 * The PLANNED-unbound modules — a registered source with no series yet.
 *
 * They contribute NO tab, NO panel and NO route: they exist so the gap
 * (and its owner) is documented.
 */
export const METRICS_PLANNED_MODULES: readonly MetricsPlannedModule[] = [
  {
    disposition: 'planned-unbound',
    id: 'checkpoint-op-bytes',
    label: 'Checkpoint op bytes',
    familyIds: ['F7'],
    trackingNote:
      'F7 publishes no series yet — the door reports `series_absent`; the op-byte counters are wired but unpublished.',
    owner: 'metrics lane (Tier-3)',
  },
  {
    disposition: 'planned-unbound',
    id: 'mcps-gateway-sessions',
    label: 'MCP gateway sessions',
    familyIds: ['F18'],
    trackingNote:
      'F18 publishes no series yet — the door reports `series_absent`; the gateway counters are wired but unpublished.',
    owner: 'metrics lane (Tier-3)',
  },
  {
    disposition: 'planned-unbound',
    id: 'upgrade-journal-twin',
    label: 'Upgrade journal twin',
    familyIds: ['F21'],
    trackingNote:
      'F21 publishes no series yet — the door reports `series_absent`; the journal twin needs an install dir.',
    owner: 'metrics lane (Tier-3)',
  },
  {
    disposition: 'planned-unbound',
    id: 'resource-spine',
    label: 'Resource spine',
    familyIds: ['F0.3'],
    trackingNote:
      'F0.3 is a stat-only row-count family — a spine row count, never a resource measurement.',
    owner: 'metrics lane (Tier-3)',
  },
];

/**
 * The HIDDEN modules — no source at all. They render NOTHING.
 */
export const METRICS_HIDDEN_MODULES: readonly MetricsHiddenModule[] = [
  {
    disposition: 'hidden',
    id: 'llm-token-usage',
    label: 'LLM token usage',
    trackingNote:
      'F14 — adjudicated OUT: `usage_metadata` is captured then dropped, "zero consumers ref-wide". No registered family exists, so there is no family to bind.',
    owner: 'metrics lane (Tier-3)',
  },
  {
    disposition: 'hidden',
    id: 'llm-request-latency',
    label: 'LLM request latency',
    trackingNote:
      'F15 — adjudicated OUT: only WARNING log lines exist; no request-latency observe site was ever registered.',
    owner: 'metrics lane (Tier-3)',
  },
  {
    disposition: 'hidden',
    id: 'source-breakers',
    label: 'Source circuit-breakers / token buckets',
    trackingNote:
      'Uncatalogued — the denials are uncounted and both the circuit breaker and the token bucket are process-local.',
    owner: 'metrics lane (Tier-3)',
  },
];

/**
 * The DROPPED modules — never planned.
 */
export const METRICS_DROPPED_MODULES: readonly MetricsDroppedModule[] = [
  {
    disposition: 'dropped',
    id: 'thread-cpu',
    label: 'Thread CPU / RSS',
    familyIds: ['F2'],
    reason: 'Adjudicated OUT by the value ruling (AM-25 / LR-2) — prod is Darwin-primary.',
  },
  {
    disposition: 'dropped',
    id: 'force-abort',
    label: 'Force-abort counter',
    familyIds: ['F16'],
    reason: 'Adjudicated OUT — the name is force-absent from the registry.',
  },
  {
    disposition: 'dropped',
    id: 'compaction',
    label: 'Compaction counters',
    familyIds: ['F19'],
    reason: 'Adjudicated OUT — no pull-able snapshot; a new observe in the compaction hot path was never registered.',
  },
  {
    disposition: 'dropped',
    id: 'turn-tap',
    label: 'Turn tap',
    familyIds: ['F20'],
    reason: 'Adjudicated OUT — the name is force-absent from the registry.',
  },
  {
    disposition: 'dropped',
    id: 'textfile-writer',
    label: 'Textfile writer',
    familyIds: ['F22'],
    reason: 'Adjudicated OUT — an out-of-process textfile writer was never registered.',
  },
];

/** Every module the manifest knows about — the four dispositions together. */
export const METRICS_MODULES: readonly MetricsModuleEntry[] = [
  ...METRICS_TABS,
  ...METRICS_PLANNED_MODULES,
  ...METRICS_HIDDEN_MODULES,
  ...METRICS_DROPPED_MODULES,
];

/** The manifest's tab entries — the ONLY entries that render a tab. */
export const METRICS_TAB_IDS: readonly string[] = METRICS_TABS.map((tab) => tab.id);

/**
 * The CENTRAL entries — the tab bodies' bindings.
 *
 * The filter's inferred type predicate narrows the literal manifest
 * shape to its central members, so this stays in step with the data
 * (no hand-maintained second list).
 */
export const METRICS_CENTRAL_ENTRIES: readonly MetricsCentralTab[] = METRICS_TABS.filter(
  (tab) => tab.slot === 'central',
);

/** The CUSTOM entry — the module's own source (there is exactly one). */
export const METRICS_CUSTOM_ENTRIES: readonly MetricsCustomTab[] = METRICS_TABS.filter(
  (tab) => tab.slot === 'custom',
);

/**
 * The id of ANY manifest tab (central or custom) — derived from the
 * manifest's own literal shape (the same derivation as
 * `MetricsCentralTabId` below), so the shell's component map can key
 * itself on the REAL vocabulary: a missing or extra map entry is a
 * COMPILE error, never a runtime `undefined` component.
 */
export type MetricsTabId = (typeof METRICS_TABS)[number]['id'];

/**
 * The id of a CENTRAL tab — derived from the manifest's own literal
 * shape, so a typo in a tab body's entry lookup is a COMPILE error.
 */
export type MetricsCentralTabId = Extract<
  (typeof METRICS_TABS)[number],
  { readonly slot: 'central' }
>['id'];

/**
 * The manifest entry of a CENTRAL tab — the typed lookup a tab body
 * uses to bind its own module.
 */
export function metricsCentralTab(id: MetricsCentralTabId): MetricsCentralTab {
  const found = METRICS_TABS.find((tab) => tab.id === id && tab.slot === 'central');
  if (!found || found.slot !== 'central') {
    throw new Error(`the manifest has no central tab '${id}'`);
  }
  return found;
}

/**
 * The manifest entry of the CUSTOM tab — the module's own source (there
 * is exactly one; the resource module is it).
 */
export function metricsCustomTab(): MetricsCustomTab {
  const found = METRICS_TABS.find((tab) => tab.slot === 'custom');
  if (!found || found.slot !== 'custom') {
    throw new Error('the manifest has no custom tab');
  }
  return found;
}

/** The manifest entry of a tab id — `null` for an unknown id. */
export function metricsTabById(id: string | null | undefined): MetricsTabEntry | null {
  if (!id) {
    return null;
  }
  return METRICS_TABS.find((tab) => tab.id === id) ?? null;
}

/** Whether the id is one of the manifest's tabs. */
export function isMetricsTabId(id: string | null | undefined): boolean {
  return metricsTabById(id) !== null;
}

/**
 * The per-tab copy VARIANT of a door state — the resolved key, or
 * `null` when the module declares no variant.
 *
 * The resolution NEVER falls back to free text: an unknown key is a
 * `null`, so a stale manifest entry can never smuggle copy in.
 */
export function metricsCopyVariantFor(
  tab: MetricsTabEntry,
  knownStates: readonly string[],
): MetricsDoorErrorCode | null {
  if (tab.disposition !== 'bound' || tab.slot !== 'central') {
    return null;
  }
  const variant = tab.copyVariant;
  if (!variant || !knownStates.includes(variant)) {
    return null;
  }
  return variant;
}
