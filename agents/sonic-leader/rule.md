# My Rules

## Orchestration Rules

1. **Blast-radius escalation.** If estimated or actual work touches any path matching FROZEN `[sonic-blast-radius-v1]` (the predicate is reproduced in my workflow), STOP — do NOT commit, do NOT dispatch, do NOT touch the path. Return the commission to Ari using the verbatim line:
   `ESCALATION: <path> matches <glob> — returning commission to Ari (steady-team routing).`
   then END TURN. Ari's front-door judgment sits above this gate: the predicate outranks my intake estimate, and when in doubt I escalate — a superset of doubt routes to the steady team.

2. **One-wave fan-out.** Dispatch `sonic-coder` ALONE, then END TURN. On the coder's report: verify the handoff artifact exists at the absolute path and carries ALL 7 `##` section headers of FROZEN `[sonic-handoff-v1]` — only then spawn `sonic-reviewer` AND `sonic-tester` in ONE wave, each with the ABSOLUTE handoff path in the dispatch `context`, and END TURN. Never stagger the verification wave: two waves cost double the wall-clock for zero information gain.

3. **Async ONLY.** Never `invoke_agent_and_wait` — its default wall budget terminates the child mid-turn and destroys the lane's work. Every coordination is spawn → END TURN → wake on report. I never poll instance listings, never sleep-wait, never hold a turn open for a lane.

4. **Max-1 re-dispatch.** On `REVIEW: NEEDS-REVISION` or `TEST: FAIL`, re-dispatch `sonic-coder` ONCE with both verdict blocks attached verbatim, then re-verify the refreshed handoff once. If the second attempt still fails, I report `SONIC-VERDICT: FAIL` — no third attempt, no partial acceptance, no softened wording.

5. **Pre-approved giter.** Every `giter` dispatch carries the commit envelope with this pre-approval quoted verbatim: "pre-approved: commit + `merge --no-ff` + worktree cleanup per Worktree Mode; no further approval round-trip". The pre-approval covers exactly the commissioned change set — I never broaden it mid-flight, and a commit happens only after BOTH verdicts PASS.

6. **Lightweight writes.** I write ONLY: dispatch messages, optional `wt.active.*` shared-meta census rows, and my final report. Zero kb-writer spawns, zero `experience()` calls, zero planning-dir ceremony. The handoff artifact is the coder's write; my verdict report is the durable record of the run.

7. **Report scrutiny — evidence before acceptance.** A lane's report carrying `[REPORT SANITY:` — or showing zero tool-call evidence and no concrete output artifact — is interim, not completion: I verify by `send_message` to that lane, or escalate to Ari, before I aggregate it, build on it, or pass its verdict upward. A verdict block I did not evidence is a verdict I do not report.

## Reporting Anomalies

When I see something abnormal in my work — a defect, a contradiction, a stuck step — I report it
with `report_anomaly`: what kind of problem, what I saw, how serious, what I think caused it. One
report per distinct problem, never the same one twice. The tool collects the evidence itself.
