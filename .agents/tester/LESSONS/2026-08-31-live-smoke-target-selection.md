# Live smoke target selection: match instance lifecycle state to the feature under test

Date: 2026-08-31 · Context: final round, Branch A autocomplete smoke (RESULTS/2026-08-31-final-round-autocomplete-compact-on-completed.md §A3)

## Root cause
A live UI smoke for the `/compact` command created its instance, let the setup turn finish (→ `completed`), then expected a command card after submitting `/compact`. On any branch WITHOUT compact-on-completed, a terminal instance rejects `/compact` at ack (by design — SC14a pins it), so no card can ever appear. Initial verdict "SMK-3 FAIL" was a **test-design artifact**, not a FE defect: the FE had correctly fired exactly one POST `{"content":"/compact"}`.

Same class of issue on SMK-5: expected `/compact` (no trailing space) but the pinned contract is `/compact ` + zero sends (`message-input.component.autocomplete.spec.ts:211,214,233`, `slash-command-palette.util.spec.ts:147-148`, `slash-command-palette.util.ts:57-59`). Always adjudicate observed UI text against the PINNED unit expectations before calling a live deviation a defect.

## Rule of thumb
1. Before writing a live command-smoke, decide which instance status the feature needs: card acceptance needs a **non-terminal (running/idle)** instance pre-compact-on-completed; the completed-accept behavior is Branch B's live test.
2. Give the setup turn a long-running instruction ("Count slowly 1 to 30…") to open a ~20-40s RUNNING window, verify status via GET before acting.
3. When a live check "fails", first check (a) instance status at action time, (b) the pinned unit-test expectations for the exact string/behavior — then re-run under corrected conditions before reporting a defect.

## Operational notes (same session)
- Worker revive-once guard: a worker revived once that completes again is REFUSED a second revive ("spawn a replacement instead"). Plan multi-task reuse as ONE dispatch per worker lifetime, or spawn fresh per task wave.
- `ng serve` binds IPv6 `[::1]:4199` — readiness probes against `127.0.0.1:4199` fail; probe `localhost`.
- GNU `timeout` lives at `/opt/homebrew/bin/timeout` (macOS + coreutils) — pack wrappers work unmodified.
