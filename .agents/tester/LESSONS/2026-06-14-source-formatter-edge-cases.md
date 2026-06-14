# Source Output Formatter Testing — Findings

## Date: 2026-06-14
## Branch: `feature/source-output-formatter`

## Known Bug: Dunder Identifier → Bold False Positive

**Symptom**: Python dunder identifiers like `__init__`, `__str__`, `__name__` are incorrectly converted to Slack bold formatting (`*init*`, `*str*`, `*name*`) by the `SlackMrkdwnFormatter`.

**Root Cause**: The `__bold__` regex in the formatter cannot reliably distinguish between:
- Markdown `__bold__` spans (should convert to `*bold*`)
- Python `__dunder__` identifiers (should stay unchanged)

**Scope**: Only affects regular prose text. Dunders inside code blocks and inline code ARE protected correctly.

**Mitigation**: 5 tests marked as `@pytest.mark.xfail(strict=False, raises=AssertionError)` documenting the bug. When fixed, these will XPASS and markers should be removed.

**Fix Difficulty**: Non-trivial — requires word-boundary detection or context-awareness to distinguish dunders from bold spans. Not a quick fix (> 20 lines, potential architecture change to the regex pipeline).

## Testing Notes

- The formatter correctly handles all 12 requested edge case scenarios
- Code block and inline code protection works perfectly
- Nested formatting (bold wrapping italic) handles correctly via placeholder substitution
- Tables, links with special chars, and empty inputs all handled gracefully
- No crashes or exceptions on any input
