# Branch Tip: Commits on `latest` vs Feature Branches

**Date**: 2026-04-11

## Finding
When asked to test changes on a feature branch, the commits may actually be on `latest`. 

For the `task_timeout_minutes` change (15→30→35), commits `c31884d` and `b4c40d2` were on `latest`, not on `feature/cleanup-config-settings`.

## Lesson
Always verify commit locations with `git branch -a --contains <hash>` before testing. The user may reference a branch name that doesn't contain the expected commits.
