# Worktree daemon.__file__ check — cwd trap (2026-09-05)

## Context
ReviveGuard-scope empirical gate, base regression-proof scratch worktree. The standard editable-install verification `python -c "import daemon; print(daemon.__file__)"` **false-positived**: it printed the MAIN repo path even though the scratch venv's editable `.pth` correctly pointed at the scratch root.

## Root cause
The check ran from the session cwd (the main repo checkout). Python's `sys.path[0] = ''` (cwd) resolves a `daemon/` package sitting in cwd BEFORE the venv's editable `.pth` entry. The venv was correctly isolated — the probe was not.

## Rule
Always run the `daemon.__file__` isolation check **from inside the target worktree's directory** (cd into the worktree, or use the worktree as cwd), using the worktree's own interpreter (`<worktree>/.venv/bin/python`). A path printed from a foreign cwd is meaningless.

## Detection signal
Printed path != target worktree root while `.venv/bin/python -c "import sys; print([p for p in sys.path if 'site-packages' in p or p == ''])"` shows cwd shadowing. Re-run from the correct cwd before concluding a stale editable install; do not `uv sync` blindly.

## Discovered by
Worker `996cd5b1` (revive-gate-regproof), 2026-09-05. Also captured as skill `worktree-isolation-daemon-import-check` (eb768c87).
