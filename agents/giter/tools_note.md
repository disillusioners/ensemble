# Tool Usage Notes

## Primary Tool

### bash
Execute git commands directly through bash terminal.

**Usage:** All git operations use bash to execute git commands.

```bash
# Check status
git status

# Stage files
git add filename
git add -A

# Commit
git commit -m "message"

# Push
git push

# Branch operations
git branch feature-name
git checkout feature-name
git merge feature-name

# View history
git log --oneline -10
git diff

# Sync
git fetch
git pull
```

---

## Always Available

These tools are always available:

- **bash** — Execute git commands
- **time** — Get current time/date
- **read_file** — Read files for context
- **list_directory** — List directory contents
- **glob_files** — Find files by pattern
- **inner_soul** — Remember and evolve

## Git Commands Reference

### Status & Info
- `git status` — Current state of working directory
- `git diff` — Unstaged changes
- `git diff --cached` — Staged changes
- `git log --oneline -n` — Recent commits
- `git branch -a` — All branches
- `git remote -v` — Remote repositories

### Staging & Committing
- `git add <file>` — Stage specific file
- `git add -A` — Stage all changes
- `git add -p` — Interactive partial staging
- `git commit -m "<type>: message"` — Commit with message
- `git commit --amend` — Modify last commit

### Branching
- `git branch <name>` — Create branch
- `git checkout <branch>` — Switch branch
- `git checkout -b <branch>` — Create and switch
- `git branch -d <branch>` — Delete branch (safe)
- `git branch -D <branch>` — Delete branch (force)

### Merging & Rebasing
- `git merge <branch>` — Merge branch into current
- `git rebase <branch>` — Rebase onto branch
- `git rebase -i HEAD~n` — Interactive rebase

### Syncing
- `git fetch` — Fetch from remote
- `git pull` — Fetch and merge
- `git push` — Push to remote
- `git push -u origin <branch>` — Push and set upstream

### Worktrees
list --porcelain: pre-check + reconcile. add -b: new branch only. add: reuse. remove: cleanup (prune NO-OP). prune: foreign-only. stash push -- <files> (bare stash strands workers).
Creation duties: write the fenced .env (verbatim spec: Worktree Mode) into <wt>/.env BEFORE any daemon run; NO venv at creation.
Lazy venv: `cd <wt> && uv sync` ONLY when a task must run tests there; NEVER reuse the main checkout's .venv from a worktree (editable install pins the original checkout — tests import the wrong branch's daemon).
Branch delete after merge: `git branch -d <branch>` FROM A CHECKOUT ON latest — with no upstream configured, -d checks merged-ness against the CURRENT HEAD, so running it elsewhere refuses (or reports the wrong verdict).
### Recovery
- `git reflog` — Reference log
- `git reset --soft HEAD~1` — Undo last commit (keep changes)
- `git reset --hard HEAD~1` — Undo last commit (discard changes)
- `git stash` — Temporarily stash changes
- `git stash pop` — Restore stashed changes
