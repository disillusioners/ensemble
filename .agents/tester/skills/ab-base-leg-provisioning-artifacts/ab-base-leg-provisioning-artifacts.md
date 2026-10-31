# A/B Base-Leg Provisioning Artifacts — Tier-2 Gate Recipe (project canon)

Adjudicate BASE-only failures in A/B regression runs. Loaded by `.agents/tester/skills/regression-ab-partition-run/` (order-step 9).

> Canonical in-repo version. Supersedes dynamic-skill `08e67030-1c11-498b-a451-04da3e552982` (DB copy retained for searchability; SUPERSEDED preamble requested via `skill_fix`, pending evolution-lane enablement — not yet applied; canonized 2026-10-31, commission tiered-gate-doctrine review fold W1).

Use when a regression A/B partition shows nodes that FAIL/ERROR on the **BASE** leg but PASS on the **TIP** leg. Before reporting them as "fixed by the branch", prove the BASE worktree could actually run them. A BASE-only node is *not* a branch fix when its mechanism is environmental.

## The three known artifacts

**1. Checkout-family name assertion** (deterministic)
`tests/unit/plugin_subsystem/test_plugin_registry.py::TestLoadRegistry::test_default_plugins_root_is_repo_plugins_dir`
asserts `re.match(r"^ensemble-src(-wt-[a-z0-9-]+)?$", DEFAULT_PLUGINS_ROOT.parent.name)`.
A BASE worktree at `/tmp/<other-name>` fails 100% of the time.
Symptom: `AssertionError: plugins root parent '<name>' does not match the ensemble-src / ensemble-src-wt-* checkout family`.

**2. `<repo_root>/.venv` required by a fixture** (deterministic)
`tests/unit/plugin_subsystem/test_sync_runner.py::TestPinningTestReferentialIntegrity` uses the
module-scoped `collected_test_ids` fixture → `_collect_pytest_test_ids()` →
`subprocess.run([".venv/bin/python", "-m", "pytest", "--collect-only", "-q", "tests/unit/plugin_subsystem"], cwd=repo_root)`.
A BASE worktree without its own `.venv` ERRORs at setup.
Symptom: `FileNotFoundError: [Errno 2] No such file or directory: '.venv/bin/python'`.
Hits `test_every_pinning_test_pointer_resolves_to_a_real_test` and `test_pinning_test_pointers_use_full_path_format`.

**3. pytest assertion-rewrite cache writes into a "read-only" BASE tree**
`PYTHONDONTWRITEBYTECODE=1` and `-p no:cacheprovider` do NOT stop pytest writing
`__pycache__/*.cpython-3xx-pytest-X.Y.Z.pyc` into the tree. Gitignored → `git status --porcelain`
stays empty, but the tree is not byte-untouched. Attribute cache dirs by mtime: concurrent A/B
partition lanes share ONE base worktree, so most cache dirs in it are not yours.

## Adjudication procedure

For each BASE-only node:

1. Re-run it **3× in isolation** on the BASE leg: `pytest <node> --tb=short -q`.
   - 3/3 FAIL **and** the reason is a path/provisioning error → **BASE-LEG ARTIFACT**
     (deterministic, environmental — keep it out of the "fixed by the branch" column).
   - 3/3 PASS in isolation after a chunk failure → **FLAKY** (chunk-context).
2. Read the reason from the log — `FileNotFoundError` / a path-name `AssertionError` ⇒ artifact.
3. Cross-check the BASE worktree: does it have `.venv`? Is its dir name in the checkout family?

## Worked example (2026-10-21, hard-reboot P4 partition U3)

BASE `4c888fbbb` @ `/tmp/hrr-base-4c888fbb` vs TIP `4641b17d5` @ `…-wt-hard-reboot-recovery`;
66 files / 1605 tests. Net-new at TIP = ∅ (the branch introduced no failures). Six BASE-only nodes:

| node | BASE 3× | TIP 3× | bucket |
|---|---|---|---|
| `test_plugin_registry.py::TestLoadRegistry::test_default_plugins_root_is_repo_plugins_dir` | 3 FAIL | 3 PASS | artifact #1 |
| `test_sync_runner.py::TestPinningTestReferentialIntegrity::test_every_pinning_test_pointer_resolves_to_a_real_test` | 3 ERROR | 3 PASS | artifact #2 |
| `test_sync_runner.py::TestPinningTestReferentialIntegrity::test_pinning_test_pointers_use_full_path_format` | 3 ERROR | 3 PASS | artifact #2 |
| `test_auth_bearer_repository.py::TestHashOnlyStorage::test_plaintext_never_persisted[postgres]` | 1 F (chunk) + 3 P (iso) | 3 PASS | flaky |
| `test_auth_bearer_repository.py::TestGetByHash::test_revoked_rows_skipped[postgres]` | 1 E (chunk) + 3 P (iso) | 3 PASS | flaky |
| `test_auth_session_repository.py::TestSessionRoundTrip::test_insert_get[postgres]` | 1 E (chunk) + 3 P (iso) | 3 PASS | flaky |

## Two reporting traps found the same day

- A `-rf`-only pytest run **silently hides ERROR node ids**: they appear only as `E` in the progress
  line while the summary counts them ("4 failed … 4 errors"). Use `-ra` whenever a chunk reports errors.
  To recover ids from an existing `-rf` log, map progress-line character positions (72 per line) to
  the `--collect-only -q` node order.
- A chunk can be **nondeterministic across identical runs** (same chunk: 4F+4E then 3F+2E three times).
  Never adjudicate a chunk-level failure from a single run — re-run before reporting.
