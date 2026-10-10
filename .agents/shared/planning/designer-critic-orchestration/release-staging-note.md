# Release Staging Note — designer-critic-orchestration

- **Branch:** `feature/designer-critic-orchestration` (worktree `/home/nea/ensemble-src-wt-designer-critic-orchestration`) — four phase commits, worktree-local, NOT pushed.
- **Head at staging:** see `git log --oneline -4` on the branch (phase 1 designer surgery → phase 2 critic build → phase 3 orchestration wiring → phase 4 planning reconcile + test rework).
- **Ships via:** the standard `stage + promote` pipeline; the agents-tree changes (designer reframe + new critic dir) go live only at the next promote ceremony.
- **next-action:** verify on next promote ceremony — 3-factor nonce gate, user-side. NO live promote from this commission.
- **Auto-promote is forbidden:** nobody should auto-promote this branch; the nonce gate is the only path.
- **Boundary:** all writes stayed inside the worktree; install dirs (`~/agents-ensemble*`, `INSTALL_DIR/releases/**`) untouched (constraint C5).
