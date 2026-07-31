# Review: `approver[v2]` Agent Prompt & System

**Subject:** `agents/approver[v2]/` — `meta.json`, `soul.md`, `rule.md`, `workflow.md`, `tools_note.md`, `skill-set.yaml`, `skills-template/*.md`
**Date:** 2026-07-31
**Status:** Review only — no changes applied
**Scope:** Two POV audit (Agent Master / system architect, and the Agent itself running a real task)

---

## POV 1 — Agent Master (system architect)

### What I like
- **Clean dispatcher-vs-worker split is the strongest v2 design.** "Approver never evaluates directly" is repeated as the spine (`soul.md:60-67`, `rule.md:8/110`, `workflow.md:1-8`, `approval-strategy.md:11`) and the worker skill contract follows logically.
- **Iteration loop DOES have a termination valve** (`rule.md:21`, `workflow.md:243-248`, `approval-strategy.md:216-221`): max 3 iterations → ESCALATED. Solves the recurring v2 theme that developer[v2] missed.
- **END TURN contract is more consistent than developer.** Three locations say the same thing nearly verbatim (`workflow.md:68-71`, `tools_note.md:36`, `rule.md:10`), and worker skills reinforce it (`plan-approval.md:67`, `decision-approval.md:67`). The "report is what I receive verbatim" phrasing is a genuinely good async contract.
- **Skill-ownership boundary is explicitly drawn.** `approval-strategy.md:118` ("Never send `approval-strategy` to workers") keeps the auto-loaded planning skill off worker dispatches — better than developer's muddy boundary.
- **Two-mode model** (Plan / Decision) with 1:1 skill mapping (`soul.md:50-56`) is a clean, small surface that resists scope creep.

### What I don't like
- **Massive cross-file duplication — the worst offender of all v2 themes so far.** Same content copied 3–4 times:
  - Verdict Format block: `soul.md:162-184` ≈ `workflow.md:283-303` (verbatim)
  - Dispatch Pattern (Python snippet): `workflow.md:30-66` ≈ `tools_note.md:11-34` ≈ `approval-strategy.md:126-142` (3 near-identical copies)
  - "Why END TURN After Dispatch": `workflow.md:68-71` ≈ `tools_note.md:36` (verbatim)
  - `todo_graph` fan-in (W3): `workflow.md:94-114` ≈ `approval-strategy.md:161-181` ≈ `tools_note.md:95`
  - Scale/scope matrix: `workflow.md:307-315` ≈ `approval-strategy.md:44-49` (format differs — drift already)
  - Common Approval Traps: `workflow.md:318-327` ≈ `plan-approval.md:110-118` ≈ `decision-approval.md:109-117` (3 copies)
  - Independence discipline: `soul.md:27-45` ≈ `rule.md:24-29` ≈ `workflow.md:75-90` ≈ `approval-strategy.md:15-24` (4 copies)
  - Verdict rules: `rule.md:35-49` ≈ `plan-approval.md:181-185` ≈ `decision-approval.md:179-183`
  - **Critically, `approval-strategy.md` (auto-loaded planning skill) and `workflow.md` are ~90% the same document.** Both own scope assessment, dispatch, fan-in, iteration. Neither is canonical.
- **`rule.md` has 35 numbered rules across 8 sections.** No cardinal-vs-guideline split. Rule №8 ("ALWAYS dispatch") is load-bearing but numbered identically to №13 ("Do not follow Leader's framing"). Dilution.
- **Fuzzy tool-permission boundary.** `meta.json:15` allow-lists `bash`, `filesystem`, `mcp`, `image` — broad, mutation-capable — while `rule.md:35` says "Never modify project source" and `tools_note.md:42-63` says "sparingly, read-only." No deny-list, only prose. Read-only enforcement lives in skill files (`plan-approval.md:15-26`), so a worker dispatched *without* a recognized skill gets full `bash` write access on an approval task. Dangerous gap.
- **`skill-set.yaml` versions drift from skill front-matter.** `approval-strategy.md:2` says `1.0.0`, `skill-set.yaml:4` declares `"1.1.0"`. Same for `plan-approval` (1.0.0 vs 1.2.0) and `decision-approval` (1.0.0 vs 1.2.0). Three drifts in one agent.
- **APPROVED-status handling is contradictory.** `workflow.md:336` ("active.md shows APPROVED → Plan already approved; confirm and update status") vs `approval-strategy.md:206` ("active.md missing OR Status: APPROVED → treat as new plan"). Two sources of truth for the same decision point — guaranteed bug.
- **No v1→v2 migration story.** `meta.json:7` bumps to `2.0.0` but nothing references `agents/approver/` (v1). No changelog, no deprecated-behavior notes.
- **Cross-agent skill-bank dependency has no fallback.** `plan-approval` / `decision-approval` live in `approver[v2]/skills-template/` but are `load_skill`-ed onto generic `worker` instances (`workflow.md:32`). If the skill bank is missing or the worker runtime can't resolve `approver`'s skill dir from a `worker` context, dispatch silently fails.
- **Calibration anchors are weak / line-count proxies.** `>500 lines` = BIG+. Line count is a poor proxy for plan risk; no worked examples.
- **Tone directive is implicit.** `soul.md:22` ("brief") and `rule.md:9` ("Be brief") are the only tone guidance. No dedicated tone section, no "what a rejected verdict sounds like" example prose.

### Improvements (master)
1. **Collapse duplication to one canonical reference per concern.** Make `workflow.md` the single home for process; turn `approval-strategy.md` into a thin pointer or delete overlap. Verdict format, dispatch snippet, fan-in, scale matrix → each lives in exactly one place, others link.
2. **Split `rule.md` into Cardinal Rules (5–7) + Guidelines.** Promote №8 "always dispatch", №29 "never evaluate directly", №35 "never modify source", and tracking-bias rules to cardinal; demote the rest.
3. **Add an explicit deny-list in `meta.json`** (`git_commit`, `edit_file`, `apply_patch`, `write_file`, `db_*`) — don't rely on skill prose to enforce read-only.
4. **Fix the 3 version drifts** between `skill-set.yaml` and skill front-matter; pick one as source-of-truth.
5. **Reconcile APPROVED-status handling** — one rule, one location.
6. **Add a fallback path** for missing skill bank (e.g., "APPROVER itself reads the artifact read-only and delivers a `DEGRADED — no skill` verdict").
7. **Add 2–3 calibration anchors** with real-ish examples.

---

## POV 2 — Agent Itself running a real task

### What I like
- **I always know what to do first:** read `active.md`, identify type, scope-assess, dispatch one worker, END TURN. The happy path is unambiguous (`workflow.md:129-216`).
- **The dispatch message is copy-pasteable** (`workflow.md:36-43`), already including `skill_feedback` and the "deliver final report verbatim" instructions — I don't invent the async contract.
- **My independence is operationally concrete**, not aspirational: "do not read tracking file before dispatching" (`rule.md:13/31`, `workflow.md:197-199`) is an actionable step.
- **The verdict decision logic is mechanical** (`plan-approval.md:181-185`): empty blocking table → APPROVED; ≥1 → REJECTED. I can execute that without judgment calls.

### What I don't like
- **"Never evaluate" contradicts "extract/dedup findings."** I'm told never to evaluate, then told to "extract blocking issues from worker reasoning" (`workflow.md:346`) and "dedup findings, keep most specific" (`rule.md:18`). That IS evaluation. When a worker mis-classifies a Note as a non-issue, I'm stuck: I can't upgrade (risks "adding" a blocking issue). Aggregation step is underspecified relative to the "dispatcher not evaluator" rule.
- **The "reasonable delay" timeout (`workflow.md:344`) is unoperationalizable.** `rule.md:10` forbids polling/sleeping/bash-waiting. No retry count, no wall-clock threshold, no heartbeat. For a single-worker approval (default — `todo_graph` skipped per `rule.md:24`), there is no node to mark errored. I'll dead-end silently if a worker hangs.
- **Iteration count leaks bias.** I read `active.md` for "identity only" including the iteration number (`workflow.md:142`). Knowing I'm on `Iteration: 003` tells me two prior attempts REJECTED — that is framing, the very thing "fresh eyes" is supposed to avoid. The bias firewall has a hole.
- **Worker dispatch + read-only enforcement live in two different runtimes.** I spawn `agent="worker"` and trust the worker's `plan-approval` skill to forbid `edit_file`. If the worker is misconfigured or the skill fails to load, I've spawned a generic worker with full `bash` to read a plan it was told is sensitive. I have no way to verify the skill loaded before the worker acts.
- **The verdict has three forms, not two.** `soul.md:165` / `workflow.md:284` list `REJECTED — Max iterations reached` as a verdict string alongside APPROVED/REJECTED, while `rule.md:32` says "always APPROVE or REJECT." The compound verdict is a third state I must remember to emit only on iteration 3 ESCALATED.
- **`image`, `mcp`, `proc`, `time`, `shared_context` tools are allow-listed (`meta.json:15`) with zero guidance.** `tools_note.md` says nothing about them. If a worker needs an MCP call during verification, I don't know whether to allow it. I'll likely just never use them — which may be wrong.
- **`explorer` team member is listed (`meta.json:17`) but barely taught.** `tools_note.md:69-87` covers it, `rule.md:26` mentions it, but nowhere am I told *when* to call explorer vs `knowledge` directly. "Synthesis" is undefined — what counts as synthesis-worthy?
- **Aggregation when workers disagree is undefined.** `workflow.md:187-190` says the worker verdict is the input and I must not override — but if two parallel section-workers give conflicting findings on a shared dependency, there's no merge rule beyond "dedup." Conflict resolution is silent.

### Improvements (runtime)
1. **Give me a heartbeat/timeout primitive** so `workflow.md:344` is actually reachable without me polling. E.g., runtime-owned staleness signal.
2. **Define the aggregation judgment band explicitly:** "you MAY downgrade a worker's Blocking to a Note with a stated reason; you MAY NOT upgrade a Note to Blocking." Resolves the "am I evaluating?" tension.
3. **Stop leaking iteration count into pre-dispatch identity reads.** Expose only `Status` pre-dispatch; `Iteration` written post-verdict, or masked.
4. **Give me a skill-load confirmation hook.** A one-shot `get_instance_info(skill_loaded=...)` before trusting the worker as read-only — explicitly NOT counted as polling.
5. **One canonical verdict vocabulary.** APPROVED / REJECTED / (ESCALATED is a state in `active.md`, not a verdict string). Drop `REJECTED — Max iterations reached`.
6. **Tell me what to do with `explorer`** with a concrete trigger, e.g. "call explorer when the plan references >3 external dependencies."

---

## Top 3 fixes to ship first
1. **Deduplicate the planning layer.** `workflow.md` and `approval-strategy.md` are ~90% the same; `soul.md`, `tools_note.md` each re-host the verdict block, dispatch snippet, fan-in, scale matrix. Pick `workflow.md` as canonical, reduce skill and `tools_note.md` to pointers + their unique content. Single change removes version-drift risk for every duplicated block and fixes the APPROVED-status contradiction.
2. **Make read-only enforcement structural, not prose.** Add a `tools.deny` list to `meta.json` so a worker that fails to load its skill still cannot mutate state. Right now read-only lives in skill prose bypassed the moment the skill bank misses.
3. **Fix the unreachable timeout + aggregation ambiguity.** Define a runtime-owned staleness signal (so `workflow.md:344ry delay" is real without violating `rule.md:10`'s no-poll rule), AND write the aggregation judgment band (downgrade-yes, upgrade-no, conflict-merge rule).

---

## Open questions
1. Who owns the canonical home for the verdict format / dispatch snippet / scale matrix — `soul.md`, `workflow.md`, or `approval-strategy.md`? Three host near-verbatim copies. Need a single source + `AGENTS.md`-level policy.
2. Is `approval-strategy.md` intended to fully duplicate `workflow.md`? If skills load into prompt context, the approver's prompt contains the same planning logic twice.
3. How should `skill_set.yaml` versions stay in sync with skill file front-matter? All three are drifted. Is there a build step that asserts equality, or should yaml be generated from front-matter?
4. What is the v1→v2 migration contract? `version: 2.0.0`; is `agents/approver/` (v1) still live? No deprecation note exists.
5. If `load_skill` resolves onto a `worker` instance but the skill lives in `approver[v2]/skills-template/`, how does the worker runtime discover it — and what's the fallback if it can't?
6. Is iteration-number exposure pre-dispatch acceptable given the "fresh eyes" thesis, or an oversight?
7. Why are `image`, `mcp`, `proc`, `time`, `shared_context` allow-listed with no usage guidance anywhere? Vestigial or intended use case?
8. What happens when parallel section-workers give conflicting blocking findings on a shared dependency? Conflict resolution is silent.
9. Should "REJECTED — Max iterations reached" be a verdict string or an `active.md` state with a plain REJECTED verdict?
