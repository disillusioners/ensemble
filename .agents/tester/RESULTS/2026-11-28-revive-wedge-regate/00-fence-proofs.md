# Fence proofs — revive-wedge re-gate (leg 00)

Date: 2026-11-28
Lane: `fix/revive-wedge-terminalization`
Worktree: `/home/nea/ensemble-src-wt-revive-wedge`
Gate tip under test: `3a04a78b41344b8f3e7986d3b97672edc843d513`
Base (origin/latest, guard absent): `5cc5912f706fda7cc2793973aaf8521aceec19d1`

Source: independent worker `rw-gate-verify-tip` (instance `cf3059f1-f002-40ea-93fd-5ca0f383cd9f`), read-only.

## Verbatim proof block

```
#1  PASS  (hard-stop check — HEAD matches expected exactly)
$ cd /home/nea/ensemble-src-wt-revive-wedge && git rev-parse HEAD
3a04a78b41344b8f3e7986d3b97672edc843d513

#2  PASS  (branch matches)
$ git rev-parse --abbrev-ref HEAD
fix/revive-wedge-terminalization

#3  PASS  (top commit is the merge of origin/latest into the lane)
$ git log --oneline -8
3a04a78b4 merge: origin/latest into fix/revive-wedge-terminalization (landing prep 2)
5cc5912f7 chore(release): bump version to v0.31.64
81fa6e49c chore(packs): re-stamp skill_evolution_resolve_atomic_unit_test census to 12 nodes
adf2b4a79 test(skill-evolution): pin exactly-once resolve_atomic on the sanctioned A/B path (F2)
bfde4e209 Merge remote-tracking branch 'origin/latest' into fix/skill-evolution-r1-frontdoor
291bb9e59 test(skill-evolution): de-vacuate the F3 row-count assertion (total, project-scoped)
1d58e0e4c test(skill-evolution): pin the corrective half of the F3 non-servable guard (red under Mutation A)
c589c733d test(skill-evolution): red-capable pin — resolve_atomic reactivates a deactivated winner (B2)

#4  PASS  (origin/latest sha + subject match)
$ git rev-parse origin/latest && git log --oneline -1 origin/latest
5cc5912f706fda7cc2793973aaf8521aceec19d1
5cc5912f7 chore(release): bump version to v0.31.64

#5  PASS  (child_reports.py sha256 matches)
$ sha256sum daemon/services/child_reports.py
717c9fc4cf00dbb0801346b30316f71763d7aad08a187288b26ea61b508a22a5  daemon/services/child_reports.py

#6  PASS  (3.14.x)
$ .venv/bin/python -V
Python 3.14.7

#7  PASS  (daemon + child_reports + exe all resolve under the worktree, NOT /home/nea/ensemble-src)
$ .venv/bin/python -c "import daemon, daemon.services.child_reports as cr, sys; print('daemon_file=', daemon.__file__); print('cr_file=', cr.__file__); print('exe=', sys.executable)"
daemon_file= /home/nea/ensemble-src-wt-revive-wedge/daemon/__init__.py
cr_file= /home/nea/ensemble-src-wt-revive-wedge/daemon/services/child_reports.py
exe= /home/nea/ensemble-src-wt-revive-wedge/.venv/bin/python
[stderr] /home/nea/ensemble-src-wt-revive-wedge/.venv/lib/python3.14/site-packages/langchain_core/_api/deprecation.py:25: UserWarning: Core Pydantic V1 functionality isn't compatible with Python 3.14 or greater.
  from pydantic.v1.fields import FieldInfo as FieldInfoV1

#8  PASS  (empty output — tracked tree clean)
$ git status --porcelain --untracked-files=no
<no output>

#9  REPORT  (82 entries total; all untracked, zero tracked modifications)
$ git status --porcelain | wc -l && git status --porcelain | awk '{print $1}' | sort | uniq -c
82
     82 ??

#10 PASS (with note)  — all 82 untracked files are the prior gate's raw outputs; count is 82, not ~89
$ git status --porcelain | grep -c '2026-11-26-revive-wedge-gate'
82

#11 REPORT  — discrepancy confirmed: pyproject 0.31.64 vs installed dist-info 0.31.60 (as commission stated)
$ grep -n '^version' pyproject.toml; ls .venv/lib/python3*/site-packages/ | grep -i ensemble
3:version = "0.31.64"
_editable_impl_ensemble.pth
ensemble-0.31.60.dist-info

#12 PASS  (no diff output at all — uv.lock / pyproject.toml / whole tree clean)
$ sha256sum uv.lock && git diff --stat -- uv.lock pyproject.toml && git diff --stat
82c785f5de8383eb5968c9ea3f9db088a8f7333b5fe15c6dc460ca0baf912389  uv.lock
<no diff output>

#13 REPORT  — extra gate worktree ensemble-src-wt-revive-gate-base does NOT yet exist
$ git worktree list
  ... (60 worktrees listed; /home/nea/ensemble-src-wt-revive-wedge 3a04a78b4 [fix/revive-wedge-terminalization];
       the only gate-base tree present is the unrelated ensemble-src-wt-gate-base-ed8ab508c)

#14 PASS  (origin/latest is an ancestor of HEAD)
$ git merge-base --is-ancestor 5cc5912f706fda7cc2793973aaf8521aceec19d1 HEAD && echo ANCESTOR_OK
ANCESTOR_OK

#15 REPORT  (merge commit contents — origin/latest folded into the lane)
$ git show --stat --oneline HEAD | head -40
3a04a78b4 merge: origin/latest into fix/revive-wedge-terminalization (landing prep 2)
 .../.agents/tester/PACKS.md                                  |   42 +-
 .../daemon/services/skill_evolution_service.py              |  720 +++--
 .../daemon/services/skill_job_dispatcher.py                 |  249 ++-
 .../daemon/services/tool_usage_digest.py                    |  716 +++-
 .../daemon/tools/filesystem.py                              |  425 +-
 .../daemon/tools/instance.py                                |   34 +
```

## Derived facts

| Fact | Value | Status |
|---|---|---|
| Gate tip | `3a04a78b41344b8f3e7986d3b97672edc843d513` | MATCHES commission |
| Branch | `fix/revive-wedge-terminalization` | MATCHES |
| `daemon/services/child_reports.py` sha256 | `717c9fc4cf00dbb0801346b30316f71763d7aad08a187288b26ea61b508a22a5` | MATCHES commission |
| `daemon.__file__` / `child_reports.__file__` | under `/home/nea/ensemble-src-wt-revive-wedge` | NOT the main checkout |
| Python | 3.14.7 (`.venv/bin/python`) | MATCHES |
| Tracked tree | clean (`git status --porcelain --untracked-files=no` empty) | CLEAN |
| Untracked entries | 82 — all under `.agents/tester/RESULTS/2026-11-26-revive-wedge-gate/` | prior gate raw output, UNTOUCHED |
| `uv.lock` sha256 | `82c785f5de8383eb5968c9ea3f9db088a8f7333b5fe15c6dc460ca0baf912389` | unchanged, no diff |
| base `5cc5912f7` ancestry | ANCESTOR_OK of HEAD | confirmed |

## Deviations from the commission text (both benign)

1. **Prior-gate untracked count is 82, not ~89.** All 82 entries match the `2026-11-26-revive-wedge-gate` path pattern; zero tracked modifications. No prior-gate file was discarded or modified.
2. **venv `dist-info` = 0.31.60 vs pyproject/uv.lock = 0.31.64.** Confirmed present as the commission predicted. `uv sync` was NOT run (it may rewrite a tracked `uv.lock`); `git diff --stat` is empty for the whole tree, so no import-level fix was needed for any leg run so far.

## Non-fatal noise

`langchain_core` deprecation warning ("Core Pydantic V1 functionality isn't compatible with Python 3.14 or greater") is emitted on import by every leg; it is not a test failure and is present at base as well.
