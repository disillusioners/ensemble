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

### Recovery
- `git reflog` — Reference log
- `git reset --soft HEAD~1` — Undo last commit (keep changes)
- `git reset --hard HEAD~1` — Undo last commit (discard changes)
- `git stash` — Temporarily stash changes
- `git stash pop` — Restore stashed changes
