import { Anomaly } from '../../../../models/resources.models';

/**
 * The VISIBLE marker label (T1 phase 5, task 5.7 — ADJ-iv shape 1).
 *
 * The API words the endpoint already serves — `kind`, `severity`,
 * `series_key`, `peak` — become VISIBLE on the marker (in phase 4 and
 * earlier they existed only in a hover-only native `<title>`). This
 * composer is the words' SSOT: the house anomaly plugin draws the
 * string verbatim beside the marker dot, and the spec asserts every
 * field is present.
 */
export function anomalyMarkerLabel(anomaly: Anomaly): string {
  return `${anomaly.kind} · ${anomaly.shape} · severity ${anomaly.severity} · series ${anomaly.series_key} · peak ${anomaly.peak}`;
}
