# Plan Overview: User Timezone Setting

## Objective
Allow users to set a preferred IANA timezone (e.g. `Asia/Bangkok`) via a backend API + frontend UI. The setting mirrors the existing **user language preference** end-to-end: a GLOBAL user-preference singleton (metadata key `user_timezone` — one row in the `project_metadata_records` table keyed `SYSTEM_DEFAULT_PROJECT_ID`), because the ensemble has **no user/auth identity layer**. When set, the timezone becomes rung 2 of the scheduling timezone resolution chain (beats the daemon-wide env default, loses only to an explicit tool param) and is injected into every agent's system prompt ("Current Time" section) so agents reason in the user's local time.

Stored value = **raw IANA name**. Unset = fall through the resolution chain — behavior is byte-identical to today. Zero schema change (reuses the existing metadata storage).

## Scope Assessment
MEDIUM — mirrors `user-language-preference` across 5 surfaces: backend setting + API, scheduling resolution-chain rung, system-prompt injection, frontend picker, tests + docs. No schema change, no new dependencies (stdlib `zoneinfo` server-side; browser-native `Intl.supportedValuesOf` client-side). Estimated 1 day of developer work.

## Context
- Project: agents-ensemble
- Working Directory: `/home/nea/ensemble-worktrees/user-timezone-setting` (dedicated feature worktree; the main checkout is occupied)
- Branch: `feature/user-timezone-setting` (branched from latest @ `e73ae6ff` — the scheduled-tasks feature merge, which introduced the timezone chain this feature extends)
- Predecessor feature: `user-language-preference` — the pattern being mirrored (`.agents/shared/planning/user-language-preference/`)

## API Contract (final)

| Method | Path | Body | Response | Notes |
|--------|------|------|----------|-------|
| GET | `/api/settings/timezone` | — | `{"timezone": "Asia/Bangkok", "utc_offset": "+07:00"}` | Both fields `null` when unset |
| PUT | `/api/settings/timezone` | `{"timezone": string \| null}` | Same shape as GET | `null` or `""` clears; invalid IANA name → 4xx |

`utc_offset` is a convenience echo computed from the stored IANA name at read time (e.g. `+07:00` for `Asia/Bangkok`) — it is not stored.

## Key Architecture Findings

### Storage — identical mechanism to language (zero schema change)
- `ProjectMetadataRecord` table at `daemon/repositories/project/models.py:170` — the existing key-value store
- CRUD already on `SQLModelProjectRepository`: `set_metadata()`, `get_metadata_record()`, `list_metadata_records()`
- `SYSTEM_DEFAULT_PROJECT_ID` at `daemon/constants.py:88`, bootstrapped at startup — provides the "global" scope
- Key: `user_timezone`; value: raw IANA name; missing row = unset

### Timezone resolution chain (`daemon/util/tz.py:283-348` `resolve_timezone`)
- Current chain is 4 rungs: explicit param → `SchedulingConfig.default_timezone` (env `ENSEMBLE_SCHEDULING_DEFAULT_TIMEZONE`) → host-local auto-detect → stdlib-UTC + loud warning
- New chain inserts the **user-setting rung between explicit param and env** (5 rungs total, see `decisions.md` D2)
- The user-setting read lives in the scheduling wrapper `_resolve_schedule_timezone` (`daemon/services/scheduling_service.py:332`) — NOT inside `tz.py` itself, keeping the resolver free of preference-storage lookups
- Terminal rung uses `datetime.timezone.utc` — **never** `ZoneInfo('UTC')` (existing invariant, preserved)

### Warning semantics on the new rung
- User setting **set + valid** → clean resolution, **no** `tz_warning` (it beats the env default silently)
- User setting **unset or invalid** → falls through the chain silently (no warning either — the loud warning remains terminal-rung-only)

### System-prompt injection (`daemon/services/instance_lifecycle.py:775` `append_current_time`)
- Post-processing step, runs AFTER the cached prompt load — setting changes do NOT invalidate the prompt cache (same pattern as `append_context_key` / `append_user_language`)
- When user tz is set, the "Current Time" section gains exactly two lines between the `Human:` line and the `Use the \`time\` tool...` line:

  ```
  User timezone: Asia/Bangkok (UTC+07:00)
  User local time: Friday, 2026-10-02 16:42:43 UTC+07:00 (Asia/Bangkok)
  ```

- When unset: output is **byte-identical** to the current format (UTC lines only)

### Echo contract (unchanged)
Every scheduling response keeps the LOCAL+UTC triple `next_run_at_local` / `next_run_at_utc` / `tz_warning`. The only behavior shift: when the user tz is set, LOCAL defaults to the user's timezone instead of env/host-local.

### Frontend
- The settings page exists (landed with user-language-preference): dropdown + custom-input pattern, `SettingsService` GET/PUT calls, localStorage caching, snackbar feedback
- Timezone picker mirrors it: `<select>` populated via browser-native `Intl.supportedValuesOf('timeZone')`, custom-entry fallback, `utc_offset` echoed as a read-only helper line

### Test landscape (files to extend / mirror)
- `tests/test_settings_api.py` — language endpoint pack (postgres-marked); timezone endpoints join the same pattern (valid set, null-clear, empty-clear, invalid → 4xx)
- `tests/unit/test_tz_resolver.py` — resolver unit pack; unchanged if the new rung lives in the scheduling wrapper
- `tests/unit/services/test_scheduling_service.py` + `tests/unit/tools/test_scheduling_tools.py` — chain-order tests: explicit beats user-setting beats env beats host-local
- Prompt-injection tests mirror the `append_user_language` tests (set → exact two lines; unset → byte-identical)

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Backend setting + API | `GET/PUT /api/settings/timezone` on the settings router; `user_timezone` metadata row; IANA validation at write time; `utc_offset` echo at read time | None | — | 2h |
| 2 | Scheduling chain rung | `_resolve_schedule_timezone` reads the user tz between explicit-param and env rungs; silent fall-through semantics; warning contract held | Phase 1 | tight | 2h |
| 3 | System-prompt injection | `append_current_time` gains the two `User timezone:` / `User local time:` lines when set; byte-identical when unset; spawn + restore paths | Phase 1 | loose | 2h |
| 4 | Frontend picker | Timezone selector on the settings page beside the language selector; `Intl.supportedValuesOf('timeZone')` list + custom fallback; `utc_offset` helper | Phase 1 | loose | 2h |
| 5 | Tests + docs | Unit packs per surface; chain-order tests; docs (`scheduling.md`, `api-reference.md`, agent `tools_note.md`) | Phases 1-3 | loose | 1h |

### Coupling Assessment

| Phase Pair | Coupling | Reasoning |
|------------|----------|-----------|
| 1 → 2 | tight | Phase 2 reads the stored value written by Phase 1 (interface: get-or-None) |
| 1 → 3 | loose | Phase 3 needs the same read interface only |
| 1 → 4 | loose | Phase 4 calls the Phase-1 API (Angular ↔ Python, different codebases) |
| 2 ↔ 3 | independent | Chain resolution and prompt injection never touch each other |
| 2 → 5 | loose | Chain-order tests assert observable resolution, not internals |

**Parallelization**: Phases 2, 3, 4 can proceed in parallel once Phase 1's read interface is defined. Phases 2 and 3 touch disjoint files.

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Stored IANA name becomes invalid later (tzdata regression, renamed zone) | low | Chain treats invalid stored value like unset — silent fall-through; PUT validates at write time so bad values never enter in the first place |
| `tz_warning` emitted on the user-tz rung (would spam every response) | medium | Set+valid user tz is a CLEAN resolution — no warning; explicit tests pin warning-absence on this rung |
| Prompt-format regression when tz unset (existing tests / agent behavior drift) | medium | Unset path must be byte-identical; exact-string assertions on the Current Time section pin this |
| Echo-contract drift (consumers expect LOCAL+UTC triple) | low | Contract unchanged by design; LOCAL semantics only shift when the user tz is set; docs updated in the same change |
| DST-correct local anchoring broken by the new rung | low | User tz flows into the existing `anchor_local_to_utc` DST semantics unchanged — the rung only changes WHERE the zone comes from, not how local times anchor |
| System default project missing on old deployments | low | `ensure_system_default_project()` runs at startup; if unavailable, the setting reads as unset and the chain behaves exactly as today |
| Frontend timezone list too long / browser support for `Intl.supportedValuesOf` | low | Native API with wide support (Chrome 81+, FF 93+, Safari 14.1+); custom-entry text fallback mirrors the language picker's pattern |
| Agents told about storage internals (metadata tables, repository paths) in agent-facing docs | low | Agent-facing docs state only "the user's configured timezone preference, when set" — storage detail stays in developer docs (agent prompt-writing guide §1) |
