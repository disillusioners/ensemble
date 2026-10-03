/**
 * IANA timezone label helpers — shared between the Settings page's
 * user-timezone picker and the schedule-create dialog's zone picker.
 *
 * Both pickers feed the same backend endpoint
 * (``GET /api/settings/timezones``) and need to render the canonical
 * IANA name alongside a normalized UTC offset. Keeping the helpers
 * here means the two surfaces cannot drift: a fix to the offset
 * normalizer lands in one file and both pickers pick it up.
 *
 * The `intlAny.supportedValuesOf?.('timeZone')` probe in
 * `getBrowserNativeTimezones` follows the Settings spec — when the
 * browser-native list is unavailable (older runtime, deleted by a
 * test, callable-but-returns-empty) the helper exposes that fact so
 * callers can fall through to their text-input fallback.
 */

/**
 * Compute the current UTC offset string for an IANA zone, e.g.
 * `+07:00` for `Asia/Bangkok` or `-05:00` for `America/New_York`.
 * Uses `Intl.DateTimeFormat#formatToParts` with `shortOffset` and
 * normalizes browser output (`GMT+7`, `GMT-05:00`, `GMT`) into a
 * stable `±HH:MM` shape for the dropdown label.
 */
export function formatTimezoneOffset(zone: string): string {
  try {
    const fmt = new Intl.DateTimeFormat('en-US', {
      timeZone: zone,
      timeZoneName: 'shortOffset',
    });
    const parts = fmt.formatToParts(new Date());
    const raw = parts.find((p) => p.type === 'timeZoneName')?.value ?? '';
    return normalizeOffsetValue(raw);
  } catch {
    return '';
  }
}

/**
 * Normalize the raw `shortOffset` output from `Intl.DateTimeFormat`
 * to a stable `±HH:MM` shape.
 *
 *   "GMT" / "UTC" / "Z" -> "+00:00"
 *   "GMT+7"               -> "+07:00"
 *   "GMT-05:00"           -> "-05:00"
 *
 * Anything that does not match the `GMT(UTC)?[+-]HH(:?MM)?` shape is
 * returned unchanged (defensive — the offset is purely cosmetic in
 * the picker; the canonical IANA name is the saved value).
 */
export function normalizeOffsetValue(raw: string): string {
  if (!raw) return '';
  if (raw === 'GMT' || raw === 'UTC' || raw === 'Z') return '+00:00';
  const match = raw.match(/^(?:GMT|UTC)([+-])(\d{1,2})(?::?(\d{0,2}))?$/);
  if (!match) return raw;
  const [, sign, hh, mm] = match;
  const hhPadded = hh.padStart(2, '0');
  const mmPadded = (mm ?? '00').padStart(2, '0');
  return `${sign}${hhPadded}:${mmPadded}`;
}

/**
 * Probe the browser-native `Intl.supportedValuesOf('timeZone')` list.
 * Returns an empty array when the function is absent OR callable but
 * returning a non-array value OR returning an empty array. The empty
 * case is treated as "unsupported" — an empty dropdown is a dead end
 * with no way to enter a custom zone, so callers should hide the
 * picker and surface the text-input fallback (see 745afb13).
 */
export function getBrowserNativeTimezones(): string[] {
  const intlAny = Intl as unknown as {
    supportedValuesOf?: (kind: string) => number | string[];
  };
  const values = intlAny.supportedValuesOf?.('timeZone') ?? [];
  return Array.isArray(values) ? (values as string[]) : [];
}
