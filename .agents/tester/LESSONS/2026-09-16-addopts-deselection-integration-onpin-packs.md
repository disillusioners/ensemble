# 2026-09-16 — addopts silently DESELECTS integration-marked files from multi-file packs (partial-deselection trap)

**Discovered**: skill-capture kill-switch gate (`feature/disable-skill-capture` @ `54a34199`), pack `skill_capture_killswitch_unit_test`.

## Trap
pyproject default `addopts = "-m 'not integration and not postgres'"` DESELECTS integration-marked test files in a grep-assembled multi-file pack WITHOUT any collection error — the pack runs green but smaller than intended. This is the partial-run sibling of the ZERO-SELECTED adjudication rule: a green run over a silently-shrunk file set proves nothing about the deselected files.

Concrete case: the pack's 5-file set included 2 integration-marked autouse ON-pin suites (`tests/integration/test_skill_capture.py`, `tests/integration/test_skill_cross_phase_flow_c.py`). Under default addopts they would have been deselected → the "4 pinned suites stay green" claim would have been verified for only 2 of 4, with a green 117→~70-test run looking completely healthy.

## Detection
- Compare expected file list against per-file collected/selected counts (COUNT-CLAIM DECOMPOSITION applies to pack composition too).
- `uv run python -m pytest --collect-only -q <files>` with and without `--override-ini="addopts="` — the delta is the silently-deselected set.

## Fix pattern
Wrap the pytest invocation with `--override-ini="addopts="` AFTER verifying the set contains zero `postgres`-marked tests (grep `@pytest.mark.postgres` over the file list) — then no live PG is needed and integration-marked files execute. Precedent: `skill_captured_regression_unit_test`; now `test/packs/skill_capture_killswitch_unit_test.sh` (commit `68a284d0`).

## Rule
Any pack mixing marker-gated files (integration/postgres) with unit files MUST either split by marker or override addopts deliberately — and must prove selected-count decomposes to the full intended file list. Never report PASS on a run whose selected count doesn't account for every file in the pack definition.
