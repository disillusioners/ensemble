# Architecture Decisions

## D1: Storage — Global Singleton Mirroring `user_language`

**Decision**: Store the timezone as a GLOBAL user-preference singleton — metadata key `user_timezone`, one row in the existing `project_metadata_records` table keyed `SYSTEM_DEFAULT_PROJECT_ID`. No new table, no schema change.

**Rationale**:
- The ensemble has **no user/auth identity layer** — there is no User model, no session table, no auth middleware. A per-user table would have nothing to key it on. "Per-user" here means the same thing it means for the language setting: the single operator preference for the whole deployment.
- **All-lane reachability**: the singleton is readable from every lane (agent tools, REST, prompt assembly) because it hangs off the always-bootstrapped system default project.
- **Zero schema change**: `ProjectMetadataRecord` (`daemon/repositories/project/models.py:170`) plus `set_metadata()` / `get_metadata_record()` already exist with dialect-aware upserts (SQLite + PostgreSQL).
- **Storage-reuse requirement**: the feature explicitly requires reusing this storage; a new table was ruled out up front.

**Alternative considered**: new `UserPreferences` table (`user_id`, `timezone`, ...).
- **Rejected**: no identity layer to supply `user_id`; over-engineering for a single preference; adds migration burden for zero capability. Identical verdict to the language setting's D1 — the two features share one storage model deliberately.

Stored value is the **raw IANA name** (e.g. `Asia/Bangkok`); a missing row = unset = fall through the resolution chain.

---

## D2: Final Timezone Resolution Chain (5 rungs)

**Decision**: The user-timezone setting is rung 2 of the scheduling timezone resolution chain — inserted AFTER the explicit tool param, BEFORE the daemon-wide env default. The chain, ordered, with file:function per rung:

1. **Explicit tool param** (`task_schedule*` `timezone` argument) — always wins.
2. **User timezone setting** (`user_timezone` preference row; read in `daemon/services/scheduling_service.py` wrapper `_resolve_schedule_timezone`) — set + valid **beats the env default, with NO warning**; unset/invalid falls through **silently**.
3. **`ENSEMBLE_SCHEDULING_DEFAULT_TIMEZONE` env** (`SchedulingConfig.default_timezone`; resolver `daemon/util/tz.py:283-348` `resolve_timezone`).
4. **Host-local auto-detect**.
5. **stdlib-UTC + loud warning** (terminal; never `ZoneInfo('UTC')`).

**Rationale**:
- Explicit param stays rung 1: a caller who names a zone meant that zone.
- The user setting slots between param and env because it is more specific than a daemon-wide env default (a person's preference outranks a machine's default) but less specific than a per-call argument.
- **No warning on rung 2**: a valid user preference is a clean, intentional resolution — warning on it would spam every scheduling response once the feature ships. The loud warning stays reserved for the terminal UTC fallback, which signals genuine misconfiguration.
- **Silent fall-through**: unset/invalid stored value behaves exactly like today's chain (byte-compatible with pre-feature behavior).
- The read lives in the `_resolve_schedule_timezone` wrapper, not inside `resolve_timezone` itself — the resolver in `daemon/util/tz.py` stays a pure chain over passed-in candidates with no preference-storage dependency; the wrapper supplies rung 2's candidate.

**Echo behavior (unchanged contract)**: responses keep the LOCAL+UTC JSON triple `next_run_at_local` / `next_run_at_utc` / `tz_warning`. When the user tz is set, LOCAL defaults to the user's timezone; the triple shape and the terminal-fallback warning are untouched.

---

## D3: System-Prompt Injection Format

**Decision**: When the user tz is set, the "## Current Time" section (`daemon/services/instance_lifecycle.py` `append_current_time`) gains, **between the `Human:` line and the `Use the \`time\` tool...` line**, exactly:

```
User timezone: Asia/Bangkok (UTC+07:00)
User local time: Friday, 2026-10-02 16:42:43 UTC+07:00 (Asia/Bangkok)
```

When unset: the section is **byte-identical to the current format** (UTC lines only).

**Rationale**:
- Injecting in `append_current_time` (post-processing, after the cached prompt load) follows the established pattern for per-instance dynamic content — setting changes never invalidate the prompt cache, and take effect on the next spawn/restore.
- The two lines give the agent both the zone identity (`Asia/Bangkok`) and its offset spelling (`UTC+07:00`) plus the wall-clock local time — enough to answer "what time is it for the user" and to sanity-check any scheduling math without a tool round-trip.
- Byte-identical-when-unset is a hard requirement: deployments that never set the preference must not see ANY prompt change (existing prompt-format assertions and downstream agent behavior stay stable).

---

## D4: No New Dependencies

**Decision**: Zero new dependencies, both sides.

**Rationale**:
- **Server**: timezone math uses stdlib `zoneinfo` (already the basis of `daemon/util/tz.py`) — IANA validation is `ZoneInfo(name)` construction, offset echo is arithmetic on the constructed zone.
- **Browser**: the picker list uses `Intl.supportedValuesOf('timeZone')` — browser-native, no package. Wide support (Chrome 81+, Firefox 93+, Safari 14.1+); older browsers get the custom-entry text fallback (same pattern the language picker uses).

**Alternative considered**: a timezone-picker npm package (e.g. `moment-timezone`-based lists) or a server-provided zone list endpoint.
- **Rejected**: both add dependency/maintenance surface for a list the platform already provides; the custom-entry fallback covers the residual browser gap.
