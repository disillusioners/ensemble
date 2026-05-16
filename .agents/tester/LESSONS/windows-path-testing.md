# Windows Path Testing Insights

## Date: 2026-05-16
## Branch: `feature/windows-path-fix`

### What Was Tested
- `_normed_contains(base, target)` — new helper for case-insensitive path containment
- `_is_within_workdir()` — workdir boundary validation with Windows temp dir support

### Key Testing Patterns

#### Windows Mocking on Unix
Since `pathlib.Path` on macOS/Linux treats `\\` as a literal path component (not a separator), Windows tests cannot use `Path()` objects for Windows paths. Instead:
- Use **string-based** containment checks for Windows-simulated tests
- Mock `os.name`, `os.path.normcase`, and `os.environ` to simulate Windows
- Use `unittest.mock.patch` context managers

#### Temp Directory Isolation
On macOS, `/tmp` → `/private/tmp` and temp files live under `/var/folders/...`. To test that `_is_within_workdir` correctly rejects paths outside workdir even when they're temp dirs:
- Create workdir outside the `/var/folders` hierarchy to prevent false positives from the temp-directory allowlist
- Use `/etc/passwd` as symlink escape target (definitively outside any temp dir)

#### Empty Environment Variable Bypass
The critical fix: when `TEMP=''` or `TMP=''`, the old code would treat empty string as a valid temp prefix, potentially bypassing validation. Test verifies empty strings are filtered out.

### Pre-Existing Failure Note
`TestInstanceStatus::test_instance_status_values` fails because a new status was added (8 vs 7). This is unrelated to filesystem changes — update expected count when convenient.
