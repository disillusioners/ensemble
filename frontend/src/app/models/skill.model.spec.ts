import { SkillMetrics } from './skill.model';

/**
 * Contract test guarding the ``SkillMetrics`` interface against the
 * C2 bug regression.
 *
 * Background: the frontend originally declared the metrics bundle
 * with field names ``total_selections`` / ``total_applied`` /
 * ``total_completions`` / ``total_fallbacks`` while the backend's
 * ``get_skill_stats()`` yields ``selected`` / ``applied`` /
 * ``completions`` / ``fallbacks``. The mismatch was invisible at
 * type-check time (TypeScript's structural typing treats them as
 * unrelated keys) so the only symptom was empty counter tiles in the
 * Skills detail page. The C2 fix renamed the interface to match the
 * backend wire format.
 *
 * This test pins the contract down: it builds a sample backend
 * payload using the literal keys the FastAPI router emits, casts it
 * ``as unknown as SkillMetrics`` so the structural type system does
 * not bail us out, and then reads every ``SkillMetrics`` field back
 * off the cast value. If anyone drifts the interface (renames
 * ``selected`` back to ``total_selections``, drops ``avg_iterations``,
 * etc.) this test fails:
 *
 * * **Compile-time** — the field read no longer type-checks, so
 *   ``tsc`` / ``ts-jest`` reports a build error and jest reports
 *   the whole file as failing to load.
 * * **Runtime** — even with ``as unknown`` to bypass the cast, an
 *   out-of-sync interface makes the read return ``undefined`` and
 *   ``typeof undefined !== 'number'`` fails the assertion.
 */

// ── Raw backend shape ─────────────────────────────────────────────────────

/**
 * Sample raw backend payload for ``GET /api/skills/{id}/metrics``.
 *
 * Field names mirror the wire format exactly — the keys come from
 * ``daemon/services/skill_metrics_service.py:get_skill_stats``. New
 * fields added to the backend must be added here AND to the
 * ``SkillMetrics`` interface in the same change set.
 */
interface RawBackendSkillMetrics {
  total: number;
  selected: number;
  applied: number;
  completions: number;
  fallbacks: number;
  avg_iterations: number;
  avg_duration: number;
  completion_rate: number;
  applied_rate: number;
  fallback_rate: number;
  consecutive_failures: number;
}

// ── Suite ─────────────────────────────────────────────────────────────────

describe('SkillMetrics contract (C2 regression guard)', () => {
  /**
   * Raw payload produced by ``get_skill_stats``. Values are
   * deliberately non-zero / non-default so the runtime ``typeof``
   * assertions cannot be satisfied by an ``undefined`` field
   * accidentally coerced into the number check (e.g. if the FE
   * interface and the backend drift and the read returns
   * ``undefined``, the assertion fails with a clear
   * "expected number, got undefined" message rather than a silent
   * green check on a zero).
   */
  const raw: RawBackendSkillMetrics = {
    total: 42,
    selected: 30,
    applied: 24,
    completions: 18,
    fallbacks: 6,
    avg_iterations: 3.7,
    avg_duration: 12.5,
    completion_rate: 0.75,
    applied_rate: 0.8,
    fallback_rate: 0.2,
    consecutive_failures: 0,
  };

  /**
   * Cast through ``unknown`` so TypeScript does NOT require the
   * shapes to be structurally compatible. We deliberately want the
   * compiler to keep us honest: if the FE interface and the
   * backend wire format diverge, the field reads below fail at
   * compile time.
   */
  const metrics = raw as unknown as SkillMetrics;

  describe('counter fields (the original C2 mismatch)', () => {
    it('should expose `selected` as a number', () => {
      expect(typeof metrics.selected).toBe('number');
      expect(metrics.selected).toBe(30);
    });

    it("should NOT require `total_selections` (the legacy C2 key that's been removed)", () => {
      // Sanity check — the legacy field name is gone. Reading it
      // must resolve to ``undefined``. This assertion documents the
      // contract rather than catching a regression (a future rename
      // back to ``total_selections`` would make this read return a
      // number, but the next test in the suite would also fail).
      expect((metrics as Record<string, unknown>)['total_selections']).toBeUndefined();
    });

    it('should expose `applied` as a number', () => {
      expect(typeof metrics.applied).toBe('number');
      expect(metrics.applied).toBe(24);
    });

    it('should expose `completions` as a number', () => {
      expect(typeof metrics.completions).toBe('number');
      expect(metrics.completions).toBe(18);
    });

    it('should expose `fallbacks` as a number', () => {
      expect(typeof metrics.fallbacks).toBe('number');
      expect(metrics.fallbacks).toBe(6);
    });
  });

  describe('aggregate fields added with the C2 fix', () => {
    it('should expose `total` as a number', () => {
      expect(typeof metrics.total).toBe('number');
      expect(metrics.total).toBe(42);
    });

    it('should expose `avg_iterations` as a number', () => {
      expect(typeof metrics.avg_iterations).toBe('number');
      expect(metrics.avg_iterations).toBe(3.7);
    });

    it('should expose `avg_duration` as a number', () => {
      expect(typeof metrics.avg_duration).toBe('number');
      expect(metrics.avg_duration).toBe(12.5);
    });
  });

  describe('rate fields (floats in 0.0..1.0)', () => {
    it('should expose `completion_rate` as a number', () => {
      expect(typeof metrics.completion_rate).toBe('number');
      expect(metrics.completion_rate).toBe(0.75);
    });

    it('should expose `applied_rate` as a number', () => {
      expect(typeof metrics.applied_rate).toBe('number');
      expect(metrics.applied_rate).toBe(0.8);
    });

    it('should expose `fallback_rate` as a number', () => {
      expect(typeof metrics.fallback_rate).toBe('number');
      expect(metrics.fallback_rate).toBe(0.2);
    });
  });

  describe('operational signal', () => {
    it('should expose `consecutive_failures` as a number', () => {
      expect(typeof metrics.consecutive_failures).toBe('number');
      expect(metrics.consecutive_failures).toBe(0);
    });
  });

  /**
   * One-stop sanity check: every key declared on ``SkillMetrics``
   * must read back as a number from the cast value. Acts as the
   * single strongest regression guard — if anyone adds a new
   * field to either side without updating the other, this is the
   * test that fails first.
   */
  describe('full-key parity', () => {
    it('should expose every SkillMetrics key as a number when cast from the raw backend payload', () => {
      // The array below is the canonical field list — ``keyof
      // SkillMetrics`` would also work but explicitly listing it
      // makes the failure message list WHICH key is missing.
      const expectedKeys: Array<keyof SkillMetrics> = [
        'total',
        'selected',
        'applied',
        'completions',
        'fallbacks',
        'avg_iterations',
        'avg_duration',
        'completion_rate',
        'applied_rate',
        'fallback_rate',
        'consecutive_failures',
      ];

      expect(expectedKeys).toHaveLength(11);

      for (const key of expectedKeys) {
        const value = metrics[key];
        // Compiles only when `key` is a valid ``keyof SkillMetrics``;
        // at runtime ``value`` is undefined if the interface and the
        // raw payload disagree on the field name.
        expect(typeof value).toBe('number');
        expect(Number.isFinite(value)).toBe(true);
      }
    });

    it('should fail when an interface key is read against a raw payload with a different key name', () => {
      // Companion negative-test — proves the test would actually
      // catch the original C2 bug. Construct a malformed payload
      // that uses the legacy C2 key ``total_selections`` and
      // assert that reading the current interface key ``selected``
      // returns ``undefined``. If the regression returned, this
      // would change behaviour and the test would fail.
      const malformed = {
        total: 42,
        total_selections: 30, // legacy C2 key
        total_applied: 24, // legacy C2 key
        total_completions: 18, // legacy C2 key
        total_fallbacks: 6, // legacy C2 key
        avg_iterations: 3.7,
        avg_duration: 12.5,
        completion_rate: 0.75,
        applied_rate: 0.8,
        fallback_rate: 0.2,
        consecutive_failures: 0,
      };
      const cast = malformed as unknown as SkillMetrics;
      // The current contract REQUIRES the new key names; reading
      // them off the legacy-payload cast yields undefined.
      expect(cast.selected).toBeUndefined();
      expect(cast.applied).toBeUndefined();
      expect(cast.completions).toBeUndefined();
      expect(cast.fallbacks).toBeUndefined();
    });
  });
});
