# Rules

## Must

### Safety & Confirmation

- **Always show status before operations** — run `git status` and `git diff` so user sees what will be affected
- **Confirm destructive operations** — force push, branch deletion, reset --hard require explicit user approval
- **Warn before force operations** — explain risks before any `--force` or `-f` flags
- **Offer dry-run options** — show what WOULD happen before making permanent changes
- **Preserve work** — never `git clean -fd` without confirming all untracked files are expendable

### Commit Best Practices

- **Use conventional commit format** — `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`, `perf:`, `ci:`, `build:`
- **Write meaningful messages** — first line under 72 chars, describe WHY not WHAT if unclear
- **Stage intentionally** — suggest `git add -p` for partial staging when appropriate
- **Commit atomically** — one logical change per commit when possible
- **Amend only if recent** — warn when amending commits that have been pushed

### Branch Management

- **Suggest branch names** — use format `type/short-description` (e.g., `feat/user-auth`, `fix/login-bug`)
- **Verify branch before switching** — warn about uncommitted changes before checkout
- **Clean up merged branches** — offer to delete feature branches after merge
- **Protect main branches** — never force push to `main`, `master`, or `develop` without warning
- **Verify "latest" branch before use** — when working with `latest` (e.g., before creating branch from it), always verify it is truly the newest, most recent state. Use `git branch -a --sort=-committerdate` to list branches by recency and confirm `latest` is at the top. Fetch, compare commit histories, and check for divergence. **Never trust the local or remote `latest` branch blindly**. If problems are found (diverged from main, missing commits, corruption, not actually latest), **STOP all operations immediately** and report back to user with findings.
- **Default base branch is `latest`** — when creating a new feature branch, default to branching from `latest` unless overridden. Accept overrides from: (1) the leader's message specifying a different base, or (2) explicit instruction to use a different base. This makes "branch from latest" the standard behavior.

### Conflict Resolution

- **Identify conflicts early** — run fetch/merge and report conflicts immediately
- **Show conflict markers** — display which files have conflicts
- **Offer resolution strategies** — accept ours/theirs//manual, explain tradeoffs
- **Never auto-resolve without permission** — for complex conflicts, show options

### Sync Operations

- **Fetch before merge** — always fetch latest to see true state
- **List branches by recency** — use `git branch -a --sort=-committerdate` to verify which branch is truly latest
- **Warn about unpushed commits** — alert when operations might lose local work
- **Push to matching branches** — prefer `git push` over `git push origin branch` when appropriate
- **Set upstream on new branches** — offer to track remote branches

## Must Not

- **Force push without warning** — always explain why it's necessary
- **Delete branches without confirmation** — even with `-d`, confirm first
- **Hard reset without backup plan** — ensure user understands consequences
- **Skip `git status` checks** — always show current state first
- **Assume remote is accessible** — handle network errors gracefully
- **Proceed with conflicted state** — do not continue if merge/pull has unresolved conflicts
- **Push to wrong remote** — verify remote name before push (origin vs upstream)
- **Commit secrets or credentials** — warn if common secret files are being staged
- **Squash published commits unannounced** — interactive rebase on pushed commits requires warning
- **Use `git clean` without `-n` first** — always dry-run to show what will be deleted
- **Proceed with problematic "latest" branch** — if latest branch has divergence, missing commits, or integrity issues, do NOT proceed with any operations. Stop immediately and report.

## Workflow

### Before Any Operation

1. Check `git status` — know current state
2. Check `git branch` — know available branches
3. Check remote state — `git fetch` to update remotes
4. Present findings to user
5. Proceed based on user's confirmation

### Commit Workflow

1. Show unstaged changes (`git diff`)
2. Show staged changes (`git diff --cached`)
3. Discuss commit scope with user
4. Suggest conventional commit type
5. Write message, confirm with user
6. Execute `git commit`
7. Verify with `git log -1`

### Branch Workflow

1. List current branches
2. Understand user's goal
3. Suggest branch naming convention
4. Create or switch as appropriate
5. Verify with `git branch` or `git checkout`
- >=2 fresh wt.active rows -> sibling worktree; see workflow.md -> Worktree Mode.

### Conflict Workflow

1. Attempt merge/pull
2. If conflicts, STOP and report
3. Show conflicted files
4. Explain conflict markers
5. Offer resolution options
6. Only proceed after user chooses

## Error Handling

- **Network errors** — retry with backoff, suggest checking credentials
- **Detached HEAD** — explain state, offer to checkout a branch
- **Binary conflicts** — cannot merge, must choose version
- **Reflog available** — if something goes wrong, reflog can recover most states

## Quick Reference

| Operation | Risk Level | Confirmation |
|-----------|------------|--------------|
| `git commit` | Low | Optional message review |
| `git push` | Medium | Show what will be pushed |
| `git branch -d` | Medium | Confirm branch name |
| `git merge` | Medium | Show merge plan |
| `git rebase -i` | High | Full explanation required |
| `git force push` | Critical | Explicit approval + explanation |
| `git reset --hard` | Critical | Explicit approval + backup suggestion |
| `git clean -fd` | Critical | Show dry-run first |
