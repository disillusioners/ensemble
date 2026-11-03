/**
 * The METRIC → UNIT VOCABULARY (T1 phase 4, task 4.15 — the mechanism;
 * phase 6, task 6.3 — the CONTENT).
 *
 * ── WHY A VOCABULARY AT ALL ─────────────────────────────────────────────
 *
 * A panel is composed per **metric-kind + unit group** (IMPL-10, R-1).
 * The KIND comes from the wire (`chart_role` per metric); the UNIT
 * cannot — the door's families carry `labels` and `role`, never units.
 * So the unit is an FE-LOCAL, TYPED table keyed on the METRIC NAME
 * (never on the family: a family mixes kinds and units).
 *
 * **A wrong unit is worse than no unit.** A metric with no vocabulary
 * entry renders `unit unknown` — never a guess, never a borrowed unit
 * from a sibling metric. Adding a genuinely new metric name is
 * therefore a ONE-LINE vocabulary edit, not a code change (the
 * manifest's registration-only contract).
 *
 * ── THE CONTENT IS PHASE 6's ───────────────────────────────────────────
 *
 * The table ships EMPTY in phase 4: the phase that owns the catalogue
 * (phase 6, task 6.3) is the one that populates it. The MECHANISM is
 * what phase 4 needs — the grouping, the fallback and the honest
 * `unit unknown` rendering — and it is exercised with the fallback.
 */

/** The placeholder a metric with no vocabulary entry renders. */
export const METRICS_UNIT_UNKNOWN = 'unit unknown';

/**
 * The metric-name → unit table.
 *
 * EMPTY by design (phase 6, task 6.3 populates it). The key is the
 * METRIC NAME — the same string the door serves in
 * `MetricsFamilyName.name`.
 */
export const METRICS_UNIT_VOCABULARY: Readonly<Record<string, string>> = {};

/**
 * The unit of a metric name.
 *
 * An unknown name renders `unit unknown` — never a guess (IMPL-10 (iii)).
 */
export function metricsUnitFor(
  metricName: string,
  vocabulary: Readonly<Record<string, string>> = METRICS_UNIT_VOCABULARY,
): string {
  const unit = vocabulary[metricName];
  return unit === undefined || unit === '' ? METRICS_UNIT_UNKNOWN : unit;
}

/**
 * The panel GROUP KEY — the metric-kind + unit pair.
 *
 * Two series share a panel only when BOTH their kind and their unit
 * match: a mixed-kind panel would break counter-honesty (a counter
 * rendered LINEAR asserts values that were never observed) and a
 * mixed-unit panel would falsify the unit label.
 */
export function metricsPanelGroupKey(kind: string, unit: string): string {
  return `${kind}::${unit}`;
}
