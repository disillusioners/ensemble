SONIC-MODULE-BRIEF — script-hunt (M2, slot 2) — READ-ONLY INVESTIGATION
TASK: Find the shared "system_restart / pok / nest-" audit-style script shape that all three 2026-11-18 outage
killers ran, its author and references, and produce a fourth-occurrence watch list. NO writes anywhere except
your deliverable doc inside this worktree.
KNOWN SIGNATURE (from the live incident doc): a bash tool call beginning
  `cd /home/nea/ensemble-src && echo "=== system_restart / pok / nest-..."`
— occurrence #2 attribution: instance 784c0e90-4f18-47cc-baa7-a3d590ce0467 (dispatch source agent:ari), 09:14:13Z.
PRE-FINDING (mine): literal grep of /home/nea/ensemble-src and ALL /home/nea/ensemble-src-wt-* worktrees finds NO
file containing that phrase — the killers ran an INLINE ad-hoc body (echo header + composed systemctl), not a repo
file. Your hunt confirms/refutes and finds the ORIGIN of the shape.
HUNT SCOPE (all read-only):
1. Repo history: `git log --all -S'system_restart / pok / nest' --oneline` and -S variants (shorter tokens:
   'system_restart', 'nest- audit', 'pok / nest') across the full history; also grep the CURRENT trees of every
   worktree (list: git worktree list) including .agents/**, agents/** (prompts, skills-template, workflow/rule md),
   docs/**, scripts/**.
2. DB-versioned prompt artifacts: agent prompts live in the DB, not files. Read-only SQL against DEV
   (ensemble_dev @127.0.0.1:5432, user ensemble_dev_app, password in worktree .env) AND — for the watch list —
   LIVE (ensemble_prod @127.0.0.1:5432; the incident ran on live). SELECT ONLY, single statements, explicit
   -h 127.0.0.1 -p 5432 (NEVER trust ambient POSTGRES_*; `env -u POSTGRES_*` is a NO-OP — set/scrub per-variable).
   Hunt phrase in prompt-artifact tables, skill bodies, board items, job payloads, session/message transcripts.
3. /tmp fences: grep /tmp for the signature (READ ONLY — never delete anything; /tmp triage fences POK applies).
4. Fourth-occurrence watch list: from live DB (read-only) — every QUEUED/ACTIVE job_queue row, instance row, or
   pending dispatch whose payload/prompt references the audit shape or contains composed systemctl restart/stop
   patterns; include instance 784c0e90 (auto-resumed + re-dispatched to ari at 13:51 — TOP item) and the tidier
   lineage 019a9a9a -> 0269bb55 (occurrence #3 killer 72f8c435). For each: iid, agent, state, what it would run,
   recommended interim handling. You LIST only — the leader decides actions. NEVER message/pause/stop any instance.
DELIVERABLE (committed in-branch, your ONLY repo write):
  .agents/shared/planning/sonic-team/wi-a5ec5b3e/D2-script-source-hunt.md
  Sections: Findings (is there a shared script? inline shape origin), Author (git log -S evidence with shas),
  Reference map (where the phrase/pattern appears: prompts/skills/board/DB rows), Fourth-occurrence watch list,
  Method & evidence appendix (exact commands + timestamps + row counts). Commit message: docs(sonic): D2 script-source hunt — wi-a5ec5b3e.
NEVER: any write outside this worktree; any DB write; any instance messaging; restart/stop of anything; git add -A; push.
REPORT: [sonic-module-report-v2] with the watch-list TOP items inline (they are urgent) + pointer to the doc.
