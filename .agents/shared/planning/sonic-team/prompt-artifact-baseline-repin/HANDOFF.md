# SONIC HANDOFF — prompt-artifact-baseline-repin (XS, phase-A pin-only)

Commission: clear the recurring PROMPT-ARTIFACT DRIFT stage WARN — exactly ONE
baseline row drifted (agents/maintenancer/workflow.md, introduced by maintenance
cure commit cf4db3d20). Regenerate with the HOUSE GENERATOR, land, push
origin/latest. STAGING HOLD IN FORCE (Ari, 2026-11-23): NO tag cut, NO stage.sh,
NO writes under /home/nea/agents-ensemble/releases/ (read-only census only).
Phase B comes as a separate dispatch after Ari's explicit all-clear.

Lane facts: base = origin/latest 207e1b4159060110b8053b7e61e05d8e68757437;
worktree /home/nea/ensemble-src-wt-prompt-baseline-repin on
feature/prompt-artifact-baseline-repin (no upstream), dev-fenced .env
(ENSEMBLE_SELF_ENV=dev, ensemble_dev @127.0.0.1:5432, DAEMON_PORT=8079,
POSTGRES_DB verified != ensemble_prod at creation). Generator invocation:
python3 scripts/protection/generate_prompt_artifact_baseline.py (bare, from the
worktree root; --check is verify-only and never writes).

## Files touched across modules
- module baseline-repin (single module, GENERATOR-RUN — no coder dispatched;
  zero source authored): agents/.prompt-artifacts.sha256 — exactly 1 row
  repinned, 83559820f596278383b5516ff0a354fb5d51b739e03765dd6514a7d7a69e8e25 ->
  8c7b6eaa071095318bb833d68fae2dfc4b31e119faa3799e99b0970729bef288 for
  agents/maintenancer/workflow.md. Row count 217 before and after; zero
  added/missing rows; git diff --numstat = 1 file, +1/-1.
- this handoff: .agents/shared/planning/sonic-team/prompt-artifact-baseline-repin/HANDOFF.md

## What changed
The committed prompt-artifact baseline now matches the post-cf4db3d20 content
of agents/maintenancer/workflow.md (current file sha verified 8c7b6eaa... at
origin/latest BEFORE regeneration, so the file had not moved under the lane).
This clears the drift that the stage.sh prompt_artifact_drift_leg and the
daemon boot lifespan check report for this artifact. No other file in the tree
changed (git status showed only agents/.prompt-artifacts.sha256 modified
between regenerate and commit).

## What is UNTESTED
- The drift leg against a REAL stage run: Phase B (tag cut + stage.sh live) is
  deferred by the Ari staging hold (releases/ at keep-3 capacity; staging now
  would evict v0.31.37 while .37-era instances may still run inside it). The
  WARN-clearance is therefore proven only by generator --check, sha256sum -c,
  and the pack-level leg tests — not by an actual stage.
- Daemon boot-side WARN path not exercised (out of scope for a pin-only lane).
- The single plan-open explore() knowledge call TIMED OUT (300 s, RAG backend);
  no stored-experience distillation rode into this lane — the plan was built
  from the commission, repo source, and POK instead. No knowledge write made
  (leader owns the commission-end write).

## Known risks
- RACE: the "Manual Central Maintenance Run" maintenancer instance (started
  2026-11-23T12:06Z) was RUNNING throughout; a further cure commit touching a
  versioned prompt artifact would re-introduce drift AFTER this push. Mitigated:
  generator + --check + delta-gate re-run immediately before commit AND the
  push was preceded by a fetch confirming origin/latest still 207e1b415 (unmoved
  since lane base). If drift re-appears, it is a NEW row owned by that lane.
- feature/video-mock, feature/video-mock-room, feature/video-mock-prompts carry
  UNMERGED agents/ changes (video agent prompts + seal re-pins); when they
  merge, extra baseline rows WILL appear — their lanes own those re-pins. This
  lane deliberately absorbed none (STOP-and-report fence held: not triggered).
- PRE-EXISTING stale seal rows in SEALED_SHA_BASELINES
  (tests/test_chart_image_delivery_e2e.py, 35 rows): daemon/constants.py and
  agents/leader/rule.md — proven IDENTICAL at BASE (207e1b415, throwaway
  detached worktree) and HEAD via failure-set diff; already routed to the
  seal/release lane; NOT this lane's debt. The sealed set has NO row for
  agents/maintenancer/workflow.md, so no seal re-pin is owed;
  agents/maintenancer/rule.md IS sealed and current (38820145875b...).
- W9 slot: next free version v0.31.43 (highest local tag v0.31.42, highest
  remote tag v0.31.42, live releases/ census max v0.31.42) — RE-DERIVE at stage
  time; nothing reserved.

## How to verify
On the MERGED tree (worktree root):
- python3 scripts/protection/generate_prompt_artifact_baseline.py --check --tree <root>
  -> "PROMPT-BASELINE: OK — 217 versioned artifact(s) match .prompt-artifacts.sha256" (rc 0)
- sha256sum -c agents/.prompt-artifacts.sha256 -> 217 ": OK" rows, rc 0
- grep 'agents/maintenancer/workflow.md' agents/.prompt-artifacts.sha256 -> 8c7b6eaa... row
- .venv/bin/python -m pytest tests/test_prompt_artifact_baseline.py
  tests/test_prompt_artifact_guards.py tests/test_prompt_preserved_exceptions.py -q
  -> 66 passed (env fence: ENSEMBLE_SELF_ENV=dev, POSTGRES_* scrubbed)
- bash tests/test_prompt_artifact_baseline_stage.sh -> 20 passed, 0 failed
- .venv/bin/python -m pytest "tests/test_chart_image_delivery_e2e.py::TestPhaseDDoesNotModifySealedArtifacts" -q
  -> 1 failed listing EXACTLY daemon/constants.py + agents/leader/rule.md
  (pre-existing; BASE-identical — not attributable to this change)

## Bash-raises used
none — every command ran under the default <=600 s cap (no sonic-leader raise consumed).

## Complexity self-assessment
low — XS pin-only commission: one generated row, zero source authored, no coder
wave dispatched (the only writers were the house generator and this handoff
heredoc). No handover raised; no override in play.
