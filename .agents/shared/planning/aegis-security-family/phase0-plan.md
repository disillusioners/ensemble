# Phase 0: Plan Review, Exemplar Read, and the Guide-Compliance Checklist

> **Status (2026-11-04, after the architect leg):** the **architect review is DONE** — verdict
> **SOUND-WITH-AMENDMENTS** (`architecture-recommendation.md` @ `f7204bd9d`), rulings Q1–Q4 CLOSED,
> amendments AM-1…AM-15 BINDING and folded into this plan set (see `decisions.md` §C2). **Phase 0 is
> NOT cleared** — the plan council + approver legs remain PENDING; this fold is not a self-approval.
> **Gate condition (review §6):** P1 must not begin until AM-1, AM-2, AM-3, AM-5, AM-6, AM-7, AM-9 and
> AM-13 are reflected in the plan set — **they now ARE** (the review named the deviations table —
> `decisions.md` §C — as the natural home; the fold is recorded in the adjacent §C2 amendments table)
> — and AM-4, AM-8, AM-10, AM-11, AM-12, AM-14 and AM-15 are
> recorded as phase-bound deltas (carried in the phase plans: AM-4 → P1/P2, AM-8 → P2/P3/P4, AM-10 →
> P2/P3/P4, AM-11 → P4/P5/P6, AM-12 → P1/P2/P3, AM-14 → P6, AM-15 → decisions.md §H — owner sets per A-c, plan council).

## Objective

Clear the **plan-review gate** for this commission (architect → council → approver), read the in-repo
exemplars the family's shape must fit, and produce the **guide-compliance checklist** that every
authoring phase (P1–P3) is audited against — and get the four architect questions (Q1–Q4) ruled so
Phase 1 authors against a settled tool matrix. *(Q1–Q4 are now ruled — RESOLVED; the council +
approver legs of task 1 remain.)*

This phase changes **nothing** in the repo except the plan set itself and the `decisions.md` ruling
record. It is read-only with respect to `agents/` and `tests/`.

---

## Entry Criteria

- The plan set exists: `plan-overview.md`, `phase0-plan.md` … `phase6-plan.md`, `decisions.md`, plus
  the two upstream inputs `research-findings.md` and `technical-analysis.md`.
- The branch `feature/aegis-security-family` is at base `1f6898ae2889ff002b4656486f6be3ee1048b649`
  (= `origin/latest` at lane start) and the worktree is clean apart from the planning dir.
- The worktree fence holds: every command runs with CWD inside
  `/home/nea/ensemble-src-wt-aegis-security-family`; `/home/nea/ensemble-src` (stale main checkout) and
  `/home/nea/agents-ensemble` (live install) are **read-only at most** and are not written to.

---

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | **Route the plan set for review** — hand `plan-overview.md` + `phase0…6-plan.md` + `decisions.md` to the architect, then the council, then the approver, per the standard lane. Record each verdict in `decisions.md` §Review trail. | — | The architect's verdict, the council's findings (with fold status), and the approver's iteration verdict are recorded. **No implementation starts before the approver clears the plan.** |
| 2 | **Obtain the Q1–Q4 rulings** from the architect and write each ruling, its rationale, and its consequence into `decisions.md` §Q (Q1 chart grant · Q2 findings storage · Q3 CVE-lookup surface · Q4 supervision without a `watchover` block). **DONE 2026-11-04** — rulings recorded in `decisions.md` §E (Q1: drop `chart`; Q2: off-repo ratified; Q3: upheld with premise corrected — live inventory per AM-3; Q4: omit the block, watchover is a tool-call gate per AM-5), with the amendment deltas in §C2. | 1 | ✅ **All four questions have a ruling, a recorded rationale, and the concrete plan delta written out (§C2). Q1–Q4 are RESOLVED — no longer a P1-entry blocker.** |
| 3 | **Read the exemplars** and extract the concrete shapes to copy: `agents/designer/{rule.md,workflow.md}` (dispatch doctrine, sole-lane rule, severity-gated round cap), `agents/sketcher/meta.json` + `agents/critic/{meta.json,workflow.md}` (leaf shape, mixed allow/deny idiom), `agents/charter/skills-template/install-mermaid-cli.{md,lib.sh}` (the only in-repo installer template + the file-invoked-mode precedent), `agents/architect/skills-template/security-design.md` and `agents/reviewer/skills-template/security-review.md` (adjacent surfaces — cross-reference, never duplicate). **AM-8 note:** charter's FENCES heading carries a provenance parenthetical ("preserved verbatim from…") and its `.lib.sh` header carries the "per … pattern, job …" shape — the exemplar is copied for its SKELETON, never for its provenance annotations; the three new bodies use a neutral `## Fences` heading. | 1 | A written list of the concrete patterns to reuse, each with its source path and the reason it is the right precedent. **Nothing new is invented that an exemplar already answers.** |
| 4 | **Author the guide-compliance checklist** — bind every rule of `docs/agent-prompt-writing-guide.md` (§1 forbidden internals, §2 one-concern-per-file + canonical home, §3 ≤7 cardinals + the path-token closure, §4 operational boundary statements, §5 tone block, §6 skill ownership + version consistency, §7 END TURN + fan-in ladder + report scrutiny, §8 org-chart rule, §10 pre-commit checklist) to a **named target artifact** — each rule × each of the 16 authored prompt files (4 agents × 4) and the 4 skill bodies. **Per the review §6 gate condition, the checklist ADDITIONALLY BINDS: (i) the AM-9/T18 token extension** — the review's wording ("AM-9/T18"); operationally this is the T18 forbidden-token extension of AM-8 (`preserved verbatim from` + the `per <agent> pattern, job <id>` shape) **together with AM-9's T12-union/report-scrutiny placement shape** — both are checklist rows; **(ii) the AM-10/T23 definition** — T23's primary target is the three `.md` FENCES bullet lists (bullet-list-only scope, C3 checksum bullet included, per-tool content excluded) with the `.lib.sh` keyword header as secondary. | 3 | The checklist exists as a file with a row per (rule × artifact) **plus the three AM-bound rows**. **Every rule has a named target; every authored file has at least one row.** This file is the gate artifact for P1–P3. |
| 5 | **Enumerate the guide §3 closure-grep exemptions explicitly** — the operational-path survivors that are *permitted* rather than accidental: the three `skills-template/*.lib.sh` read targets (charter precedent), the engagement-folder paths (`findings.json`, the report document, the evidence dir), and the sandbox-root paths. **W6 (plan council): the exemption list covers BOTH closure classes** — the `.md` token class AND the bare `agents/`-prefix class (guide §10 `:254`; `BARE_AGENTS_PREFIX_RE` in `tests/unit/tools/test_prompt_section_reference_integrity.py:251`; §12.5 #0: bare tokens are violations only as cross-references to prompt sections, not operational paths). Each survivor is justified as operational, per guide §3's scope carve-out. | 4 | A written exemption list covering both classes. **T15 must enumerate these rather than pass silently** — a silent pass is the failure mode the carve-out warns about. |
| 6 | **Freeze the file manifest and the two arithmetic rules** — the exact 28 new files + 1 modified file; the baseline-row rule (only depth-2 `soul/rule/workflow/tools_note` count ⇒ 161 → 177) and the agents-tree rule (338 + 28 = 366), each with its **re-derivation command** so the numbers are re-derived at P4 rather than trusted. | 4 | The manifest is written down; the re-derivation commands are recorded verbatim. **Explicitly recorded (AM-11): no `memory.md`, `growth.md`, `tools.md`, or `skill.md` on any new agent** (any of the four would add a baseline row and move 177), **and every baseline leg asserts the file EXISTS with exactly 177 rows — the generator's `--check` exits 0 even when the file is absent, so `rc=0` alone proves nothing.** |
| 7 | **Pre-register the test plan** — map each of T1–T28 to the phase that produces the artifact it pins, so no test is written against an artifact that does not exist yet, and no artifact ships unpinned. | 4, 6 | A T1–T28 → phase table exists and each test's target artifact is assigned to P1, P2, P3 or P4. |

---

## Gates

| Gate | Requirement | How it is checked |
|---|---|---|
| **G0.1 — Plan approved** | The architect, council and approver have each ruled, and every council finding is folded or explicitly routed with an owner. **Architect leg DONE** (SOUND-WITH-AMENDMENTS @ `f7204bd9d`; AM-1…AM-15 folded — `decisions.md` §F/§C2). **Council + approver still PENDING.** | The recorded verdicts in `decisions.md` §Review trail. |
| **G0.2 — Q1–Q4 ruled** | Each of the four questions has a ruling with its plan consequence written out. ✅ **DONE** — rulings recorded in `decisions.md` §E; amendment deltas in §C2. | `decisions.md` §E + §C2. |
| **G0.3 — Checklist complete** | Every guide rule has a named target artifact; every authored file has at least one checklist row. | The checklist file's row counts (no empty columns). |
| **G0.4 — Exemptions enumerated** | The §3 closure-grep survivors are listed and justified as operational. | The exemption list. |
| **G0.5 — Manifest frozen** | 28 new + 1 modified file; the 177-row and 366-file derivations recorded with re-derivation commands. | The manifest + the derivation rules. |
| **G0.6 — Nothing written outside the planning dir** | `git status --porcelain` shows only the planning dir. | `git -C /home/nea/ensemble-src-wt-aegis-security-family status --porcelain`. |

---

## Verification Commands

```bash
cd /home/nea/ensemble-src-wt-aegis-security-family

# fence: only the planning dir is touched
git status --porcelain

# branch base is the recorded base (the finalize guard re-checks this at merge time)
git rev-parse HEAD                       # expect 1f6898ae2889ff002b4656486f6be3ee1048b649
git log --oneline -1

# the current baseline row count (the 161 the plan starts from)
wc -l agents/.prompt-artifacts.sha256    # expect 161 (A-h: SNAPSHOT AT PLAN TIME — P4 re-derivation is mandated)

# the agents-tree file count (the 338 the plan starts from)
find agents -type f | wc -l              # expect 338 (A-h: SNAPSHOT AT PLAN TIME — P4 re-derivation is mandated)

# the leader one-liner target — current value, before the edit (P4)
python3 -c "import json;print(json.load(open('agents/leader/meta.json'))['team_members'])"
```

**No venv, no daemon boot, no test run in this phase.** The baseline generator is available but there
is nothing to regenerate yet.

---

## Coupling

- **Tight with:** P1, P2, P3 — the checklist **is** the gate artifact each of those phases is audited
  against (P1's gate: "the checklist is satisfied for `aegis` alone"; P2's: the checklist rows for the
  three children; P3's: the checklist rows for the three skill bodies).
- **Tight with:** P4 — the frozen manifest and the arithmetic rules are what P4's commit composition
  and its `--check` gate are verified against.
- **Loose with:** P5, P6 — independent; P5/P6 verify and ship what P1–P4 produced.
- **Independent of:** the daemon; nothing here touches `daemon/`.

---

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| **P0-R1** | The checklist becomes a rubber stamp — rows written from memory rather than from the guide text, so it misses a rule that then violates at P1. | Medium | Medium | Task 3 reads the guide and the exemplars *before* the checklist is authored; the checklist row text cites the guide section number, so a reviewer can re-derive each row from `docs/agent-prompt-writing-guide.md`. |
| **P0-R2** | ~~Q1–Q4 are not ruled in time, and Phase 1 authors against an unsettled allow matrix, then Phase 2/3/4 have to be redone.~~ **[RETIRED 2026-11-04 — Q1–Q4 are ruled]** The remaining Phase-0 risk is the same shape one leg later: the council/approver findings are not folded before P1. | High | Medium | Q1–Q4 are ruled (Gate G0.2 ✅). The council + approver legs remain the **P1-entry blockers** (Gate G0.1) — if a verdict is unavailable, Phase 1 does not start — the plan does not author against a guess. |
| **P0-R3** | The manifest drifts from reality (an extra prompt file, a missing `skill-set.yaml`), so the 177/366 numbers are wrong at P4. | Medium | Low | The numbers are **re-derived from the real tree at P4** using the recorded commands; the plan states derivation rules, not frozen expectations. |
| **P0-R4** | The exemplar read picks up an anti-pattern (e.g. copying sketcher's inert `watchover` block, or the designer's chart-category grant). | Medium | Medium | Task 3 records *why* each exemplar is the right precedent for each shape; the inert-`watchover` and implied-member traps are named exclusions in `decisions.md`. |

---

## Effort

**≈ 0.5 working day** — plan review routing + verdicts (the dominant cost; the review arc's wall-clock
is not the planner's to compress), the exemplar read, the checklist authoring, and the Q1–Q4 rulings.

---

## Exit Criterion

Phase 0 is done when **all six gates (G0.1–G0.6) are green**:

1. the plan set has cleared architect → council → approver, with every finding folded or routed
   *(architect leg done; council + approver pending)*;
2. Q1–Q4 are ruled and recorded in `decisions.md` §E with their plan consequences *(✅ done — §C2
   carries the amendment deltas)*;
3. the guide-compliance checklist exists with a named target artifact per (rule × artifact), plus the
   AM-bound rows (AM-8/T18 token extension, AM-9/T12 union + rule.md placement, AM-10/T23 definition);
4. the §3 closure-grep exemptions are enumerated and justified;
5. the file manifest and both arithmetic derivations are frozen with re-derivation commands
   (AM-11: baseline legs assert file-exists + exactly 177 rows);
6. the working tree contains nothing outside `.agents/shared/planning/aegis-security-family/`.

**Only then does Phase 1 begin.** The implementation arc (P1 → P4), the verification arc (P5) and the
staging arc (P6) follow the plan's phase order.
