/**
 * The DOOR's client-side constants (T1 phase 2).
 *
 * These are the FE's view of the central query door's own limits. They
 * live here — NOT in the shell component — because the tab bodies need
 * them too, and a tab body that imported them from the shell would
 * close an import cycle (the shell mounts the tab bodies).
 */

import { MetricsCentralWindow } from './tabs.manifest';

/**
 * The door's selector ceiling — an over-ceiling request is REJECTED by
 * the door (never silently truncated).
 */
export const METRICS_SELECTOR_CEILING = 50;

/**
 * The central window ladder in MINUTES.
 *
 * The seconds are derived (`minutes * 60`) so no phase has to carry a
 * hard-coded seconds literal — phase 4's residue check greps the metrics
 * tree for the word-bounded ONE-HOUR literal and expects every hit
 * outside its allowlist row to be ZERO (this comment deliberately does
 * NOT spell the literal: naming it would be a hit).
 */
export const METRICS_CENTRAL_WINDOW_MINUTES: Readonly<Record<MetricsCentralWindow, number>> = {
  '15m': 15,
  '30m': 30,
  '1h': 60,
  '2h': 120,
};
