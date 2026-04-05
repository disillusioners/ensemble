# Workflow

## Standard Git Operations

### 1. Status Check (Always First)

Before ANY git operation, establish the current state:

```bash
# Always run these first
git status
git branch
```

This tells us:
- What files are modified, staged, or untracked
- Which branch we're on
- Whether there are unpushed commits

### 2. Operation Types

#### A. Commit Flow

```
1. git status → See current state
2. git diff → Show unstaged changes
3. git diff --cached → Show staged changes
4. Discuss scope with user
5. Suggest commit type (feat/fix/docs/etc)
6. Write message (first line <72 chars)
7. Execute: git commit -m "type: description"
8. Verify: git log -1
```

#### B. Branch Flow

```
1. git branch → List branches
2. Understand goal (feature/fix/hotfix)
3. Suggest name: type/short-description
4. For new branch: git checkout -b type/description
5. For switch: git checkout branch-name
6. Verify: git branch (current branch marked *)
```

#### C. Sync Flow

```
1. git fetch origin → Update remote refs
2. git status → Check for changes
3. git log HEAD..origin/latest → See unpulled commits
4. If merge needed: git merge origin/latest
5. If conflicts: STOP, report, resolve with user
6. If push needed: git push
7. Verify: git status (should be clean)
```

#### D. Conflict Resolution Flow

```
1. git fetch → Get latest
2. git merge origin/latest → Attempt merge
3. If CONFLICTS:
   a. STOP - do not proceed
   b. git status → Show conflicted files
   c. Show conflict markers in files
   d. Explain options:
      - Accept ours (keep local)
      - Accept theirs (use remote)
      - Manual merge
   e. User chooses strategy
   f. Edit files to resolve
   g. git add <resolved-files>
   h. git commit → Complete merge
```

### 3. Common Scenarios

#### Scenario: "Commit my changes"

```
Step 1: git status
Step 2: git diff (show what's changed)
Step 3: git diff --cached (if anything staged)
Step 4: Ask user - which files to include?
Step 5: Suggest commit type based on changes
Step 6: Draft commit message
Step 7: Get user confirmation on message
Step 8: git add <files>
Step 9: git commit -m "message"
Step 10: Show git log -1 to confirm
```

#### Scenario: "Ensure latest branch exists"

```
Step 1: git branch (show all branches)
Step 2: If 'latest' exists:
   - git checkout latest
   - git pull origin latest
Step 3: If 'latest' does NOT exist:
   - git checkout main (or master)
   - git pull origin main
   - git checkout -b latest
   - git push -u origin latest
Step 4: Inform user latest is ready
```

#### Scenario: "Create a feature branch from latest"

```
Step 1: Ensure latest exists (see above)
Step 2: git branch (show current state)
Step 3: git status (confirm clean or staged)
Step 4: Ask user for branch purpose
Step 5: Suggest name: feat/user-auth
Step 6: git checkout -b feat/user-auth
Step 7: git branch (verify new branch)
Step 8: Inform user ready to work
```

#### Scenario: "Merge feature into latest"

```
Step 1: git status (confirm on feature branch)
Step 2: git checkout latest
Step 3: git pull origin latest (update latest)
Step 4: git merge feature/branch-name
Step 5: If conflicts → resolve (see conflict flow)
Step 6: git push origin latest
Step 7: git push origin feature/branch-name (keep feature branch up-to-date)
Step 8: Inform user merge complete
```

#### Scenario: "Push my branch"

```
Step 1: git status (confirm branch)
Step 2: git branch (show current branch)
Step 3: git push -u origin current-branch
Step 4: Verify push worked
Step 5: Show remote tracking info
```

### 4. Risk-Based Confirmation

| Risk | Example | Confirmation |
|------|---------|--------------|
| Low | `git commit` | Show message, proceed |
| Medium | `git push` | Show what's being pushed |
| High | `git branch -D` | Confirm branch name |
| Critical | `git push --force` | Explicit yes/no + explanation |

### 5. Error Recovery

#### Detached HEAD
```
Problem: "You are in 'detached HEAD' state"
Solution:
  1. Explain what this means
  2. git branch (show commits)
  3. git checkout latest (or desired branch)
  4. Optionally cherry-pick commits
```

#### Push Rejected
```
Problem: "Updates were rejected because the tip of your current branch is behind"
Solution:
  1. git fetch origin
  2. Show: git log HEAD..origin/latest
  3. Options:
     a. git pull --rebase (if safe)
     b. git merge origin/latest (if prefer merge)
     c. Force (with warning)
```

#### Merge Conflict
```
Problem: "Merge conflict in file X"
Solution:
  1. STOP - do not proceed
  2. git status (show conflicted files)
  3. Open file, show conflict markers
  4. Explain: <<<<<< HEAD ... ======= ... >>>>>>> branch
  5. User chooses resolution
  6. Edit file, remove markers
  7. git add file
  8. Continue or commit
```

### 6. Conventional Commits

Format: `<type>: <description>`

Types:
- **feat:** New feature
- **fix:** Bug fix
- **docs:** Documentation only
- **style:** Formatting, no code change
- **refactor:** Code change that neither fixes bug nor adds feature
- **test:** Adding tests
- **chore:** Maintenance tasks
- **perf:** Performance improvement
- **ci:** CI changes
- **build:** Build system changes

Examples:
- `feat: add user authentication`
- `fix: resolve login redirect issue`
- `docs: update README installation steps`
- `refactor: extract payment processing module`
