/**
 * Shared helpers for the skill-trigger form.
 *
 * Pure functions and constants used by both the production
 * `SkillTriggerFormComponent` and its testable mirror
 * (`skill-trigger-form.component.spec.ts`). Centralising them here
 * eliminates drift between the two — historically each spec redeclared
 * copies that could silently fall out of sync.
 *
 * IMPORTANT: This is a pure move/refactor. Behaviour is preserved
 * exactly. Do NOT add or change semantics without updating every
 * consumer.
 */

/**
 * Supported condition types — restricted to the five built-in kinds the
 * backend knows how to evaluate. Order matches the canonical listing
 * documented on the model interface.
 */
export type ConditionType =
  | 'low_completion_rate'
  | 'high_fallback_rate'
  | 'consecutive_failures'
  | 'task_count_scan'
  | 'periodic_scan';

/**
 * Default field values per condition type — populated into the
 * dynamic FormControls whenever `condition_type` changes so the user
 * always sees sensible starting numbers.
 */
export const CONDITION_TYPE_DEFAULTS: Record<ConditionType, Record<string, number>> = {
  low_completion_rate: { threshold: 0.3, min_selections: 5 },
  high_fallback_rate: { threshold: 0.5, min_selections: 5 },
  consecutive_failures: { threshold: 3 },
  task_count_scan: { threshold: 20 },
  periodic_scan: { interval_days: 7 },
};

/**
 * Build the `condition_json` payload from the current form value
 * based on the active condition_type. Coerces numeric strings and
 * ensures numbers are emitted (the backend expects `int|float`,
 * not strings).
 */
export function buildConditionJson(
  conditionType: string,
  formValue: Record<string, unknown>,
): Record<string, unknown> {
  switch (conditionType as ConditionType) {
    case 'low_completion_rate':
    case 'high_fallback_rate':
      return {
        threshold: toNumber(formValue['threshold']),
        min_selections: toNumber(formValue['min_selections']),
      };
    case 'consecutive_failures':
    case 'task_count_scan':
      return { threshold: toNumber(formValue['threshold']) };
    case 'periodic_scan':
      return { interval_days: toNumber(formValue['interval_days']) };
    default:
      return {};
  }
}

/**
 * Coerce a form value to a number. Strings get parsed via `Number`,
 * anything else falls back to `0`.
 */
export function toNumber(value: unknown): number {
  if (typeof value === 'number') return value;
  if (typeof value === 'string') return Number(value);
  return 0;
}

/**
 * Pick a numeric value, falling back to a default. Used when seeding
 * the form from an existing trigger whose `condition_json` may omit
 * some keys (legacy rows, partial updates, etc.).
 */
export function pickNumber(value: unknown, fallback: number | null): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim() !== '' && Number.isFinite(Number(value))) {
    return Number(value);
  }
  return fallback;
}
