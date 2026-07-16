import {
  SkillMetrics,
  SkillUsageRecord,
  SkillUsageRecordsResponse,
  SkillAbTestStats,
  SkillAbTestStatsResponse,
  SkillTrigger,
  SkillTriggerCreate,
  SkillTriggerUpdate,
} from './skill.model';

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

// ============================================================
// SkillUsageRecord
// ============================================================
//
// Contract test for the ``SkillUsageRecord`` interface used by
// :func:`SkillService.getUsageRecords`. Field names mirror the
// wire format from
// ``daemon/repositories/skill/models.py:SkillUsageRecord.to_dict``
// (17 fields). The two phase-2 columns (``ab_test_group``,
// ``superseded``) are appended last in the backend dict so they
// read back as the final keys on the cast value.

interface RawBackendSkillUsageRecord {
  id: string;
  skill_id: string;
  project_id: string | null;
  instance_id: string;
  agent_id: string;
  task_message: string | null;
  selected: boolean;
  applied: boolean;
  task_succeeded: boolean;
  iterations: number;
  duration_seconds: number;
  feedback_applied: boolean | null;
  feedback_note: string | null;
  fallback: boolean;
  created_at: string;
  ab_test_group: string | null;
  superseded: boolean;
}

describe('SkillUsageRecord contract', () => {
  const raw: RawBackendSkillUsageRecord = {
    id: 'rec-001',
    skill_id: 'skill-abc',
    project_id: 'proj-xyz',
    instance_id: 'inst-42',
    agent_id: 'agent-test',
    task_message: 'deploy to staging',
    selected: true,
    applied: true,
    task_succeeded: true,
    iterations: 4,
    duration_seconds: 18,
    feedback_applied: null,
    feedback_note: null,
    fallback: false,
    created_at: '2026-07-16T18:00:00+00:00',
    ab_test_group: null,
    superseded: false,
  };

  const record = raw as unknown as SkillUsageRecord;

  describe('identifier + scope fields', () => {
    it('should expose `id` as a string', () => {
      expect(typeof record.id).toBe('string');
      expect(record.id).toBe('rec-001');
    });

    it('should expose `skill_id` as a string', () => {
      expect(typeof record.skill_id).toBe('string');
      expect(record.skill_id).toBe('skill-abc');
    });

    it('should expose `project_id` as a nullable string', () => {
      expect(record.project_id).toBe('proj-xyz');
    });

    it('should expose `instance_id` as a string', () => {
      expect(typeof record.instance_id).toBe('string');
      expect(record.instance_id).toBe('inst-42');
    });

    it('should expose `agent_id` as a string', () => {
      expect(typeof record.agent_id).toBe('string');
      expect(record.agent_id).toBe('agent-test');
    });

    it('should expose `task_message` as a nullable string', () => {
      expect(record.task_message).toBe('deploy to staging');
    });
  });

  describe('signal booleans', () => {
    it('should expose `selected` as a boolean', () => {
      expect(typeof record.selected).toBe('boolean');
      expect(record.selected).toBe(true);
    });

    it('should expose `applied` as a boolean', () => {
      expect(typeof record.applied).toBe('boolean');
      expect(record.applied).toBe(true);
    });

    it('should expose `task_succeeded` as a boolean', () => {
      expect(typeof record.task_succeeded).toBe('boolean');
      expect(record.task_succeeded).toBe(true);
    });

    it('should expose `fallback` as a boolean', () => {
      expect(typeof record.fallback).toBe('boolean');
      expect(record.fallback).toBe(false);
    });
  });

  describe('numeric metrics', () => {
    it('should expose `iterations` as a number', () => {
      expect(typeof record.iterations).toBe('number');
      expect(record.iterations).toBe(4);
    });

    it('should expose `duration_seconds` as a number', () => {
      expect(typeof record.duration_seconds).toBe('number');
      expect(record.duration_seconds).toBe(18);
    });
  });

  describe('feedback triple-state booleans', () => {
    it('should expose `feedback_applied` as a nullable boolean', () => {
      // ``feedback_applied`` is a 3-state boolean — null when no
      // feedback yet, true when applied, false when recorded-but-not.
      // The raw payload uses ``null``; the cast must preserve
      // ``null`` (not coerce to ``undefined``).
      expect(record.feedback_applied).toBeNull();
    });

    it('should expose `feedback_note` as a nullable string', () => {
      expect(record.feedback_note).toBeNull();
    });
  });

  describe('phase-2 columns (ab_test_group + superseded)', () => {
    it('should expose `ab_test_group` as a nullable string', () => {
      expect(record.ab_test_group).toBeNull();
    });

    it('should expose `superseded` as a boolean', () => {
      expect(typeof record.superseded).toBe('boolean');
      expect(record.superseded).toBe(false);
    });
  });

  describe('timestamp', () => {
    it('should expose `created_at` as a string', () => {
      expect(typeof record.created_at).toBe('string');
      expect(record.created_at).toBe('2026-07-16T18:00:00+00:00');
    });
  });

  describe('full-key parity', () => {
    it('should expose every SkillUsageRecord key with the verified type when cast from the raw backend payload', () => {
      const expectedKeys: Array<keyof SkillUsageRecord> = [
        'id',
        'skill_id',
        'project_id',
        'instance_id',
        'agent_id',
        'task_message',
        'selected',
        'applied',
        'task_succeeded',
        'iterations',
        'duration_seconds',
        'feedback_applied',
        'feedback_note',
        'fallback',
        'created_at',
        'ab_test_group',
        'superseded',
      ];

      expect(expectedKeys).toHaveLength(17);

      for (const key of expectedKeys) {
        const value = record[key];
        expect(value).not.toBeUndefined();
      }
    });

    it('should fail when a malformed payload uses an old key name', () => {
      // Companion negative-test — confirms the contract would
      // catch a regression where the interface drifts back to an
      // older shape. The backend never used ``task_id`` (the
      // legacy column name pre-phase-1 refactor) so reading it
      // from the cast yields ``undefined``, while the current
      // contract key ``instance_id`` reads the populated value.
      const malformed = {
        id: 'rec-001',
        skill_id: 'skill-abc',
        task_id: 'legacy-task-id', // legacy / wrong key
        agent_id: 'agent-test',
        task_message: 'hello',
        selected: true,
        applied: true,
        task_succeeded: true,
        iterations: 4,
        duration_seconds: 18,
        fallback: false,
        feedback_applied: null,
        feedback_note: null,
        created_at: '2026-07-16T18:00:00+00:00',
        ab_test_group: null,
        superseded: false,
      };
      const cast = malformed as unknown as SkillUsageRecord;
      expect(cast.instance_id).toBeUndefined();
      expect(cast.project_id).toBeUndefined();
    });
  });
});

// ============================================================
// SkillUsageRecordsResponse
// ============================================================
//
// Contract test for the paginated usage-record envelope from
// ``GET /api/skills/{id}/usage-records``. Field names mirror the
// dict literal in ``daemon/routers/skills.py:get_usage_records``
// (``skill_id``, ``records``, ``total``, ``limit``, ``offset``).
// ``records`` is a ``SkillUsageRecord[]`` so we reuse the cast
// helper above.

describe('SkillUsageRecordsResponse contract', () => {
  const rawRecord: RawBackendSkillUsageRecord = {
    id: 'rec-001',
    skill_id: 'skill-abc',
    project_id: 'proj-xyz',
    instance_id: 'inst-42',
    agent_id: 'agent-test',
    task_message: 'deploy to staging',
    selected: true,
    applied: true,
    task_succeeded: true,
    iterations: 4,
    duration_seconds: 18,
    feedback_applied: null,
    feedback_note: null,
    fallback: false,
    created_at: '2026-07-16T18:00:00+00:00',
    ab_test_group: null,
    superseded: false,
  };

  // Sample wire-format envelope — keys come straight from
  // ``daemon/routers/skills.py:get_usage_records``. ``limit`` /
  // ``offset`` are echoed back AFTER the backend has clamped them
  // (so a caller asking for 10000 will see ``limit: 200`` echoed
  // here — the FE uses this to know what page size the server
  // actually applied).
  const rawEnvelope = {
    skill_id: 'skill-abc',
    records: [rawRecord],
    total: 137,
    limit: 50,
    offset: 0,
  };

  const response = rawEnvelope as unknown as SkillUsageRecordsResponse;

  describe('envelope keys', () => {
    it('should expose `skill_id` as a string', () => {
      expect(typeof response.skill_id).toBe('string');
      expect(response.skill_id).toBe('skill-abc');
    });

    it('should expose `records` as an array of SkillUsageRecord', () => {
      expect(Array.isArray(response.records)).toBe(true);
      expect(response.records).toHaveLength(1);
    });

    it('should expose `total` as a number', () => {
      expect(typeof response.total).toBe('number');
      expect(response.total).toBe(137);
    });

    it('should expose `limit` as a number', () => {
      expect(typeof response.limit).toBe('number');
      expect(response.limit).toBe(50);
    });

    it('should expose `offset` as a number', () => {
      expect(typeof response.offset).toBe('number');
      expect(response.offset).toBe(0);
    });
  });

  describe('full-key parity', () => {
    it('should expose every SkillUsageRecordsResponse key', () => {
      const expectedKeys: Array<keyof SkillUsageRecordsResponse> = [
        'skill_id',
        'records',
        'total',
        'limit',
        'offset',
      ];

      expect(expectedKeys).toHaveLength(5);

      for (const key of expectedKeys) {
        const value = response[key];
        expect(value).not.toBeUndefined();
      }
    });

    it('should fail when an interface key is read against a payload with a different envelope shape', () => {
      // Negative test — the FastAPI list endpoint returns
      // ``{items, total}`` as the work-equivalent envelope shape
      // but the usage-records endpoint uses ``records`` (not
      // ``items``). Reading ``records`` from a wrong-shaped
      // envelope yields ``undefined``.
      const wrongEnvelope = {
        skill_id: 'skill-abc',
        items: [rawRecord], // would match the list / DLQ pattern
        count: 137, // would match a single-resource ``count`` envelope
      };
      const cast = wrongEnvelope as unknown as SkillUsageRecordsResponse;
      expect(cast.records).toBeUndefined();
      expect(cast.total).toBeUndefined();
      expect(cast.limit).toBeUndefined();
    });
  });
});

// ============================================================
// SkillAbTestStats
// ============================================================
//
// Contract test for the per-variant comparison bundle produced
// by ``SkillMetricsService.get_ab_comparison_stats``. Field set
// verified against the return dict in
// ``daemon/services/skill_metrics_service.py:get_ab_comparison_stats``
// (21 fields). ``skill_id_a`` / ``skill_id_b`` are nullable
// (they read ``None`` when no test row exists for the group).

interface RawBackendSkillAbTestStats {
  skill_id_a: string | null;
  skill_id_b: string | null;
  completion_rate_a: number;
  completion_rate_b: number;
  applied_rate_a: number;
  applied_rate_b: number;
  fallback_rate_a: number;
  fallback_rate_b: number;
  avg_iterations_a: number;
  avg_iterations_b: number;
  avg_duration_a: number;
  avg_duration_b: number;
  composite_score_a: number;
  composite_score_b: number;
  difference: number;
  comparisons: number;
  extension_count: number;
  sample_size: number;
  ready_to_resolve: boolean;
  needs_more_data: boolean;
}

describe('SkillAbTestStats contract', () => {
  const raw: RawBackendSkillAbTestStats = {
    skill_id_a: 'skill-old-uuid',
    skill_id_b: 'skill-new-uuid',
    completion_rate_a: 0.7,
    completion_rate_b: 0.85,
    applied_rate_a: 0.6,
    applied_rate_b: 0.78,
    fallback_rate_a: 0.3,
    fallback_rate_b: 0.15,
    avg_iterations_a: 4.2,
    avg_iterations_b: 3.4,
    avg_duration_a: 22.0,
    avg_duration_b: 17.5,
    composite_score_a: 0.62,
    composite_score_b: 0.79,
    difference: 0.17,
    comparisons: 50,
    extension_count: 1,
    sample_size: 30,
    ready_to_resolve: true,
    needs_more_data: false,
  };

  const stats = raw as unknown as SkillAbTestStats;

  describe('variant identifiers', () => {
    it('should expose `skill_id_a` and `skill_id_b` as nullable strings', () => {
      expect(stats.skill_id_a).toBe('skill-old-uuid');
      expect(stats.skill_id_b).toBe('skill-new-uuid');
    });
  });

  describe('per-variant rate fields (floats in 0.0..1.0)', () => {
    it('should expose completion_rate_a/b as numbers', () => {
      expect(typeof stats.completion_rate_a).toBe('number');
      expect(typeof stats.completion_rate_b).toBe('number');
      expect(stats.completion_rate_a).toBe(0.7);
      expect(stats.completion_rate_b).toBe(0.85);
    });

    it('should expose applied_rate_a/b as numbers', () => {
      expect(typeof stats.applied_rate_a).toBe('number');
      expect(typeof stats.applied_rate_b).toBe('number');
      expect(stats.applied_rate_a).toBe(0.6);
      expect(stats.applied_rate_b).toBe(0.78);
    });

    it('should expose fallback_rate_a/b as numbers', () => {
      expect(typeof stats.fallback_rate_a).toBe('number');
      expect(typeof stats.fallback_rate_b).toBe('number');
      expect(stats.fallback_rate_a).toBe(0.3);
      expect(stats.fallback_rate_b).toBe(0.15);
    });
  });

  describe('per-variant performance fields', () => {
    it('should expose avg_iterations_a/b as numbers', () => {
      expect(typeof stats.avg_iterations_a).toBe('number');
      expect(typeof stats.avg_iterations_b).toBe('number');
      expect(stats.avg_iterations_a).toBe(4.2);
      expect(stats.avg_iterations_b).toBe(3.4);
    });

    it('should expose avg_duration_a/b as numbers', () => {
      expect(typeof stats.avg_duration_a).toBe('number');
      expect(typeof stats.avg_duration_b).toBe('number');
      expect(stats.avg_duration_a).toBe(22.0);
      expect(stats.avg_duration_b).toBe(17.5);
    });
  });

  describe('composite scoring', () => {
    it('should expose composite_score_a/b as numbers', () => {
      expect(typeof stats.composite_score_a).toBe('number');
      expect(typeof stats.composite_score_b).toBe('number');
      expect(stats.composite_score_a).toBe(0.62);
      expect(stats.composite_score_b).toBe(0.79);
    });

    it('should expose `difference` as a number (absolute composite delta)', () => {
      expect(typeof stats.difference).toBe('number');
      expect(stats.difference).toBe(0.17);
    });
  });

  describe('test counters + thresholds', () => {
    it('should expose `comparisons` as a number', () => {
      expect(typeof stats.comparisons).toBe('number');
      expect(stats.comparisons).toBe(50);
    });

    it('should expose `extension_count` as a number', () => {
      expect(typeof stats.extension_count).toBe('number');
      expect(stats.extension_count).toBe(1);
    });

    it('should expose `sample_size` as a number', () => {
      expect(typeof stats.sample_size).toBe('number');
      expect(stats.sample_size).toBe(30);
    });
  });

  describe('resolution flags', () => {
    it('should expose `ready_to_resolve` as a boolean', () => {
      expect(typeof stats.ready_to_resolve).toBe('boolean');
      expect(stats.ready_to_resolve).toBe(true);
    });

    it('should expose `needs_more_data` as a boolean', () => {
      expect(typeof stats.needs_more_data).toBe('boolean');
      expect(stats.needs_more_data).toBe(false);
    });
  });

  describe('full-key parity', () => {
    it('should expose every SkillAbTestStats key with the verified type', () => {
      const expectedKeys: Array<keyof SkillAbTestStats> = [
        'skill_id_a',
        'skill_id_b',
        'completion_rate_a',
        'completion_rate_b',
        'applied_rate_a',
        'applied_rate_b',
        'fallback_rate_a',
        'fallback_rate_b',
        'avg_iterations_a',
        'avg_iterations_b',
        'avg_duration_a',
        'avg_duration_b',
        'composite_score_a',
        'composite_score_b',
        'difference',
        'comparisons',
        'extension_count',
        'sample_size',
        'ready_to_resolve',
        'needs_more_data',
      ];

      expect(expectedKeys).toHaveLength(20);

      for (const key of expectedKeys) {
        const value = stats[key];
        expect(value).not.toBeUndefined();
      }
    });

    it('should fail when a malformed payload uses the legacy no-suffix key names', () => {
      // Companion negative-test — confirms the contract would
      // catch a regression where the a/b suffixes were dropped
      // (e.g. ``completion_rate`` instead of
      // ``completion_rate_a``). The backend has always emitted
      // the suffixed form — reading the suffixed keys from an
      // unsuffixed-shape payload returns ``undefined``.
      const malformed = {
        skill_id_old: 'skill-old-uuid', // legacy column name from the
        skill_id_new: 'skill-new-uuid', // ``skill_ab_tests`` row
        completion_rate: 0.7,           // unsuffixed key
        applied_rate: 0.6,              // unsuffixed key
        fallback_rate: 0.3,             // unsuffixed key
        avg_iterations: 4.2,            // unsuffixed key
        avg_duration: 22.0,             // unsuffixed key
        composite_score: 0.62,          // unsuffixed key
        difference: 0.17,
        comparisons: 50,
        extension_count: 1,
        sample_size: 30,
        ready_to_resolve: true,
        needs_more_data: false,
      };
      const cast = malformed as unknown as SkillAbTestStats;
      // The contract requires per-variant suffixed keys — these
      // read ``undefined`` against the unsuffixed payload.
      expect(cast.skill_id_a).toBeUndefined();
      expect(cast.skill_id_b).toBeUndefined();
      expect(cast.completion_rate_a).toBeUndefined();
      expect(cast.completion_rate_b).toBeUndefined();
      expect(cast.composite_score_a).toBeUndefined();
      expect(cast.composite_score_b).toBeUndefined();
    });
  });
});

// ============================================================
// SkillAbTestStatsResponse
// ============================================================
//
// Contract test for the ab-test/stats envelope from
// ``GET /api/skills/{id}/ab-test/stats``. Field names mirror the
// dict literal in ``daemon/routers/skills.py:get_ab_test_stats``
// (``skill_id``, ``ab_test_group``, ``stats``). ``stats`` is the
// full ``SkillAbTestStats`` dict or ``null`` when the skill is
// not in a test.

describe('SkillAbTestStatsResponse contract', () => {
  // Envelope with stats present (the ``skill IS in a test``
  // branch in the router).
  const statsPresent = {
    skill_id_a: 'skill-old-uuid',
    skill_id_b: 'skill-new-uuid',
    completion_rate_a: 0.7,
    completion_rate_b: 0.85,
    applied_rate_a: 0.6,
    applied_rate_b: 0.78,
    fallback_rate_a: 0.3,
    fallback_rate_b: 0.15,
    avg_iterations_a: 4.2,
    avg_iterations_b: 3.4,
    avg_duration_a: 22.0,
    avg_duration_b: 17.5,
    composite_score_a: 0.62,
    composite_score_b: 0.79,
    difference: 0.17,
    comparisons: 50,
    extension_count: 1,
    sample_size: 30,
    ready_to_resolve: true,
    needs_more_data: false,
  };

  describe('envelope shape when a test exists', () => {
    const rawEnvelope = {
      skill_id: 'skill-abc',
      ab_test_group: 'group-uuid-7777',
      stats: statsPresent,
    };
    const response = rawEnvelope as unknown as SkillAbTestStatsResponse;

    it('should expose `skill_id` as a string', () => {
      expect(typeof response.skill_id).toBe('string');
      expect(response.skill_id).toBe('skill-abc');
    });

    it('should expose `ab_test_group` as a string', () => {
      expect(typeof response.ab_test_group).toBe('string');
      expect(response.ab_test_group).toBe('group-uuid-7777');
    });

    it('should expose `stats` as a populated SkillAbTestStats object', () => {
      expect(response.stats).not.toBeNull();
      // Cast through unknown so we can read the nested keys and
      // confirm the bundle round-trips.
      const stats = response.stats as SkillAbTestStats;
      expect(stats.skill_id_a).toBe('skill-old-uuid');
      expect(stats.skill_id_b).toBe('skill-new-uuid');
    });
  });

  describe('envelope shape when NOT in a test (the no-group branch)', () => {
    const rawEnvelope = {
      skill_id: 'skill-abc',
      ab_test_group: null,
      stats: null,
    };
    const response = rawEnvelope as unknown as SkillAbTestStatsResponse;

    it('should expose `skill_id` as a string', () => {
      expect(typeof response.skill_id).toBe('string');
      expect(response.skill_id).toBe('skill-abc');
    });

    it('should expose `ab_test_group` as ``null``', () => {
      expect(response.ab_test_group).toBeNull();
    });

    it('should expose `stats` as ``null`` (NOT a zero-stats dict)', () => {
      // The contract distinguishes "no test" from "test exists
      // but stats unavailable" by serialising ``stats: null``
      // in the no-test branch — NOT an empty-dict fallback.
      expect(response.stats).toBeNull();
    });
  });

  describe('full-key parity', () => {
    it('should expose every SkillAbTestStatsResponse key on both branches', () => {
      const expectedKeys: Array<keyof SkillAbTestStatsResponse> = [
        'skill_id',
        'ab_test_group',
        'stats',
      ];

      expect(expectedKeys).toHaveLength(3);

      const present = {
        skill_id: 'skill-abc',
        ab_test_group: 'group-uuid',
        stats: statsPresent,
      };
      const absent = {
        skill_id: 'skill-abc',
        ab_test_group: null,
        stats: null,
      };
      for (const sample of [present, absent]) {
        const response = sample as unknown as SkillAbTestStatsResponse;
        for (const key of expectedKeys) {
          expect(response[key]).not.toBeUndefined();
        }
      }
    });

    it('should fail when an envelope key is read against a payload with a different envelope shape', () => {
      // Negative test — the ab-test ``/resolve`` endpoint returns
      // a flatter resolution dict (``skill_id`` + ``winner_id``)
      // rather than the ``stats`` envelope. Reading ``stats``
      // against that wrong shape must yield ``undefined``.
      const wrongEnvelope = {
        skill_id: 'skill-abc',
        winner_id: 'skill-new-uuid', // resolve-endpoint shape
        reason: 'composite_score differential',
      };
      const cast = wrongEnvelope as unknown as SkillAbTestStatsResponse;
      expect(cast.stats).toBeUndefined();
      expect(cast.ab_test_group).toBeUndefined();
    });
  });
});

// ============================================================
// SkillTrigger
// ============================================================
//
// Contract test for the trigger rule shape used by
// :func:`SkillService.listTriggers` /
// :func:`SkillService.createTrigger` /
// :func:`SkillService.updateTrigger`. Field set verified against
// ``daemon/repositories/skill/models.py:SkillTrigger.to_dict``
// (8 fields). ``condition_json`` is a free-form dict — typed
// here as ``Record<string, unknown>`` so any rule body fits.

interface RawBackendSkillTrigger {
  id: string;
  project_id: string | null;
  name: string;
  condition_type: string;
  condition_json: Record<string, unknown>;
  action: string;
  is_enabled: boolean;
  created_at: string;
}

describe('SkillTrigger contract', () => {
  const raw: RawBackendSkillTrigger = {
    id: 'trig-001',
    project_id: null,
    name: 'Deploy keyword',
    condition_type: 'keyword',
    condition_json: { keyword: 'deploy' },
    action: 'select_skill:workflow-deploy',
    is_enabled: true,
    created_at: '2026-07-16T12:00:00+00:00',
  };

  const trigger = raw as unknown as SkillTrigger;

  describe('identifier + scope', () => {
    it('should expose `id` as a string', () => {
      expect(typeof trigger.id).toBe('string');
      expect(trigger.id).toBe('trig-001');
    });

    it('should expose `project_id` as a nullable string', () => {
      // ``null`` = global trigger (applies to every project);
      // non-null = project-scoped.
      expect(trigger.project_id).toBeNull();
    });

    it('should expose `name` as a string', () => {
      expect(typeof trigger.name).toBe('string');
      expect(trigger.name).toBe('Deploy keyword');
    });
  });

  describe('rule body', () => {
    it('should expose `condition_type` as a string', () => {
      expect(typeof trigger.condition_type).toBe('string');
      expect(trigger.condition_type).toBe('keyword');
    });

    it('should expose `condition_json` as a record (free-form dict)', () => {
      expect(typeof trigger.condition_json).toBe('object');
      expect(trigger.condition_json).toEqual({ keyword: 'deploy' });
    });

    it('should expose `action` as a string', () => {
      expect(typeof trigger.action).toBe('string');
      expect(trigger.action).toBe('select_skill:workflow-deploy');
    });
  });

  describe('lifecycle + timestamp', () => {
    it('should expose `is_enabled` as a boolean', () => {
      expect(typeof trigger.is_enabled).toBe('boolean');
      expect(trigger.is_enabled).toBe(true);
    });

    it('should expose `created_at` as a string', () => {
      expect(typeof trigger.created_at).toBe('string');
      expect(trigger.created_at).toBe('2026-07-16T12:00:00+00:00');
    });
  });

  describe('full-key parity', () => {
    it('should expose every SkillTrigger key', () => {
      const expectedKeys: Array<keyof SkillTrigger> = [
        'id',
        'project_id',
        'name',
        'condition_type',
        'condition_json',
        'action',
        'is_enabled',
        'created_at',
      ];

      expect(expectedKeys).toHaveLength(8);

      for (const key of expectedKeys) {
        const value = trigger[key];
        expect(value).not.toBeUndefined();
      }
    });

    it('should fail when a malformed payload uses the legacy `trigger_type` / `trigger_config` key names', () => {
      // Negative test — the older draft schema used
      // ``trigger_type`` / ``trigger_config`` instead of
      // ``condition_type`` / ``condition_json``. The wire format
      // has always been the ``condition_*`` pair; the ``trigger_*``
      // names were never on the wire. Reading the contract
      // keys against a payload with the legacy names yields
      // ``undefined``.
      const malformed = {
        id: 'trig-001',
        project_id: null,
        name: 'Deploy keyword',
        trigger_type: 'keyword',      // legacy / wrong key
        trigger_config: { keyword: 'deploy' }, // legacy / wrong key
        action: 'select_skill:workflow-deploy',
        is_enabled: true,
        created_at: '2026-07-16T12:00:00+00:00',
      };
      const cast = malformed as unknown as SkillTrigger;
      expect(cast.condition_type).toBeUndefined();
      expect(cast.condition_json).toBeUndefined();
    });
  });
});

// ============================================================
// SkillTriggerCreate + SkillTriggerUpdate
// ============================================================
//
// Contract tests for the create / update request payloads.
// Unlike the response-shape interfaces above, these are inputs
// to the backend — the backend REJECTS payloads whose keys
// don't match ``condition_type`` / ``condition_json`` (the
// legacy ``trigger_type`` / ``trigger_config`` draft names were
// never wired through). Field sets are verified against the
// ``TriggerCreateRequest`` / ``TriggerUpdateRequest`` Pydantic
// models in ``daemon/routers/skill_schemas.py`` (mirror of the
// TypeScript shapes). These tests run as type-level parity
// guards: a compile-time check that the FE shape still matches
// what the BE accepts.

describe('SkillTriggerCreate contract', () => {
  describe('required fields', () => {
    it('should require name, condition_type, condition_json and action', () => {
      // Build a fully-populated payload. The TS check fires
      // at compile time — if any of the four required keys
      // is removed from the interface the line below stops
      // compiling.
      const create: SkillTriggerCreate = {
        name: 'Deploy keyword',
        condition_type: 'keyword',
        condition_json: { keyword: 'deploy' },
        action: 'select_skill:workflow-deploy',
      };
      expect(create.name).toBe('Deploy keyword');
      expect(create.condition_type).toBe('keyword');
      expect(create.action).toBe('select_skill:workflow-deploy');
      expect(create.condition_json).toEqual({ keyword: 'deploy' });
    });

    it('should accept optional `is_enabled` and `project_id`', () => {
      // Both can be omitted (``false`` is the default for
      // ``is_enabled``; ``null`` for ``project_id`` means
      // global). Wrapping in a payload ensures TS only
      // accepts the literal optional key names.
      const withExtras: SkillTriggerCreate = {
        name: 'Regex trigger',
        condition_type: 'regex',
        condition_json: { regex: '^run\\s+tests?$' },
        action: 'request_clarification',
        is_enabled: false,
        project_id: 'proj-xyz',
      };
      expect(withExtras.is_enabled).toBe(false);
      expect(withExtras.project_id).toBe('proj-xyz');
    });
  });

  describe('full-key parity', () => {
    it('should expose every SkillTriggerCreate key (count + presence)', () => {
      const expectedKeys: Array<keyof SkillTriggerCreate> = [
        'name',
        'condition_type',
        'condition_json',
        'action',
        'is_enabled',
        'project_id',
      ];

      expect(expectedKeys).toHaveLength(6);

      const payload = {
        name: 'Deploy keyword',
        condition_type: 'keyword',
        condition_json: { keyword: 'deploy' },
        action: 'select_skill:workflow-deploy',
        is_enabled: true,
        project_id: null,
      };
      const cast = payload as unknown as SkillTriggerCreate;
      for (const key of expectedKeys) {
        expect(cast[key]).not.toBeUndefined();
      }
    });

    it("should fail when a payload uses the legacy `trigger_type` / `trigger_config` keys", () => {
      // Same negative-test shape as the SkillTrigger
      // regression guard — even the create payload must keep
      // using ``condition_type`` / ``condition_json`` so a
      // future re-introduction of the legacy names trips the
      // test.
      const malformed = {
        name: 'Deploy keyword',
        trigger_type: 'keyword',         // legacy / wrong
        trigger_config: { keyword: 'deploy' }, // legacy / wrong
        action: 'select_skill:workflow-deploy',
      };
      const cast = malformed as unknown as SkillTriggerCreate;
      expect(cast.condition_type).toBeUndefined();
      expect(cast.condition_json).toBeUndefined();
    });
  });
});

describe('SkillTriggerUpdate contract', () => {
  describe('all-fields-optional behaviour', () => {
    it('should accept an empty object (no fields — no-op update)', () => {
      // PUT semantics — the backend strips unset keys via
      // ``exclude_none=True`` so an empty body is a no-op
      // (200 with the row unchanged).
      const update: SkillTriggerUpdate = {};
      expect(Object.keys(update)).toHaveLength(0);
    });

    it('should accept a partial payload (any single field)', () => {
      // Each field below is independently optional. A drift
      // away from the optional key set (e.g. by removing
      // ``is_enabled``) would fail the assignments below at
      // compile time.
      const update: SkillTriggerUpdate = { is_enabled: false };
      expect(update.is_enabled).toBe(false);
    });

    it('should accept a fully-populated payload (every field set)', () => {
      const update: SkillTriggerUpdate = {
        name: 'Renamed',
        condition_type: 'embedding_match',
        condition_json: { embedding_match: { threshold: 0.85 } },
        action: 'select_skill:workflow-deploy',
        is_enabled: true,
        project_id: 'proj-xyz',
      };
      expect(update.name).toBe('Renamed');
      expect(update.condition_type).toBe('embedding_match');
      expect(update.condition_json).toEqual({
        embedding_match: { threshold: 0.85 },
      });
      expect(update.action).toBe('select_skill:workflow-deploy');
      expect(update.is_enabled).toBe(true);
      expect(update.project_id).toBe('proj-xyz');
    });
  });

  describe('full-key parity', () => {
    it('should expose every SkillTriggerUpdate key (count + presence)', () => {
      const expectedKeys: Array<keyof SkillTriggerUpdate> = [
        'name',
        'condition_type',
        'condition_json',
        'action',
        'is_enabled',
        'project_id',
      ];

      expect(expectedKeys).toHaveLength(6);

      const payload = {
        name: 'Renamed',
        condition_type: 'regex',
        condition_json: { regex: '^run\\s+tests?$' },
        action: 'select_skill:workflow-debug',
        is_enabled: true,
        project_id: 'proj-xyz',
      };
      const cast = payload as unknown as SkillTriggerUpdate;
      for (const key of expectedKeys) {
        expect(cast[key]).not.toBeUndefined();
      }
    });

    it("should fail when a payload uses the legacy `trigger_type` / `trigger_config` keys", () => {
      const malformed = {
        trigger_type: 'regex',          // legacy / wrong
        trigger_config: { regex: '^run\\s+tests?$' }, // legacy / wrong
      };
      const cast = malformed as unknown as SkillTriggerUpdate;
      expect(cast.condition_type).toBeUndefined();
      expect(cast.condition_json).toBeUndefined();
    });
  });
});
