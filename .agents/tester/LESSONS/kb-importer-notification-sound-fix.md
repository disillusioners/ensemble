# Lesson: KB-Importer Notification Sound Fix

**Date**: 2026-07-09
**Branch**: `fix/kb-importer-notification-sound`

## Issue
Frontend `SOUND_EXCLUDED_AGENT_IDS` set had a typo: `'kb-import'` instead of `'kb-importer'`, causing the sound exclusion gate to never match the real agent_id. Sound was playing for kb-importer completions.

## Fix
1. Frontend: `'kb-import'` → `'kb-importer'` (typo fix)
2. Backend: Added `KB_AGENT_IDS` guard to `emit_root_completion()` (defense-in-depth)

## Key Insight: Over-suppression is Design-Intentional

The backend KB guard in `emit_root_completion()` blocks ALL notifications for KB agents — not just sound. This is intentional design across 4 filtering layers:
- `emit_root_completion` → blocks SSE event
- `emit_instance_created` → blocks SSE event
- `stream_status_change` → blocks per-instance SSE
- API `listInstances(excludeKb=true)` → filters from list

KB agents are background processes. They only appear in the UI when user opts into `showKb=true` (60s polling).

## Gotcha: SOUND_EXCLUDED_AGENT_IDS is Now Dead Code for KB Agents

Since the backend blocks the SSE event entirely, the frontend `SOUND_EXCLUDED_AGENT_IDS` set containing `'kb-importer'` is technically dead code — the event never reaches the frontend sound gate. However:
- `'experiencer'` in the set IS still meaningful (those events DO arrive, just without sound)
- The set serves as defense-in-depth and documentation of intent
- Keeping it is correct

## Gotcha: MockJob work_id Attribute

Pre-existing test failure in `test_resume_child_notification.py`: `MockJob` fixture missing `work_id` attribute. Root cause: Phase 1 Virtual Job Management Surface refactor changed source to read `existing_task.work_id` but test fixture wasn't updated.

**Fix**: Add `self.work_id = job_id` to `MockJob.__init__`.

## Gotcha: Missing Test for New Code Path

When adding a new guard/filter to an existing method, always add a test mirroring the equivalent test for the existing pattern. The `emit_instance_created()` KB guard had tests but `emit_root_completion()` did not until we added them.

## Frontend Test Config
- The project uses `jest.config.js`, NOT `jest.config.ts` (common assumption error)
- Run: `cd frontend && npx jest --config jest.config.js src/app/services/notification.service.spec.ts --verbose`
