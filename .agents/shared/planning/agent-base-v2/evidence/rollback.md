# Rollback Story — agent-base-v2 (Phase E record)

**Per-phase rollback:** `git revert <sha>` of the offending phase's commit on `feature/agent-base-v2`. Chain: `495068d8b` planning → `97f3ad43c` A → `b3f37a59c` B → `2e3bf2b2a` C/1 → `7b0a102c3` C/2 → `300ac0ba4` C/3 → `ca222f99f` C/4 → `ba279f813` C/5 → `8cefcecf3` D → (Phase E evidence commits).

**Whole-mission rollback:** fresh branch from `885a19d4f`. Everything the mission added (legacy/ archive, base copy, Phase C daemon edits, Phase D docs, Phase E evidence) is additive or git-preserved — cleanly abandonable by deleting the branch.

**Binding rollback order (innate-skill loader constraint):** reverse D first (restore docs), then C (re-instate the opencode stack if reverted), then B; reverse A last or skip (legacy/ is additive and safe to leave). Post-B base agents do NOT declare `["opencode"]`, so the loader does not require the skill while B stands alone — but B+C must move together if C is reverted while B stands (C removed the skill dir C.2/C.3).

**Per-pair rollback grep-guard (plan §7):** name-allowlisted `git grep -niE 'opencode' -- "agents/${name}/" "agents/${name}[v2]/"` + `git log --oneline -- "agents/${name}/"` before any `git checkout <sha> -- "agents/${name}/"`. Never run `git clean -fdx` in the worktree (S7: untracked evidence + orphan DB record).

**NO promote performed (D8).** Zero live exposure. The mission rides the next promote ceremony via the fast pipeline; stage-time manifest machinery (`agents_tree_sha256` / `agents_manifest`) recomputes from the tree automatically — no pinned values to update. Post-promote free verification: `default_agent_versions` carries through unchanged (proven in dev lane, DoD #6).
