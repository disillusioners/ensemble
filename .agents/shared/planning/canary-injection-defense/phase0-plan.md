# Phase 0: Plan Review + Exemplar Read + the Guide-Compliance Checklist

## Objective

Clear the plan-review gate for the `canary-injection-defense` commission and produce the two
artifacts every later phase is audited against:

1. the **guide-compliance checklist** — one row per mechanically-checkable rule of
   `docs/agent-prompt-writing-guide.md`, each with the **named target artifact** it applies to; and
2. the **D1–D7 adopted-rulings table** — the architect's questions are *framed*, not decided, so
   this phase records the adopted rulings (the architect gate is closed), plus
   the **close-condition** for the plan set.

This phase is **read-only**. It writes no `agents/` file, no test, and no code.

---

## Entry Criteria

- The plan set exists: `plan-overview.md` + `phase0-plan.md` … `phase6-plan.md` under
  `.agents/shared/planning/canary-injection-defense/`.
- The branch is `feature/canary-injection-defense` at base `97b76e20e1d640c69b720149547e93565ab22b4c`
  (= `origin/latest` at lane start), in the lane worktree
  `/home/nea/ensemble-src-wt-canary-injection-defense`.
- The sibling-owned files exist or are in flight: `decisions.md` (D1–D7 + rulings),
  `technical-analysis.md` (design resolution), `requirements.md` (requirement set).
- The predecessor plan set is readable:
  `.agents/shared/planning/aegis-security-family/{plan-overview,phase0-plan…phase6-plan}.md`.

---

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | **Read the binding spec** — `docs/agent-prompt-writing-guide.md` in full, and extract every **mechanically checkable** rule into the checklist skeleton (§3 cardinal/guideline split, §1 one canonical home, §5 the `.md` path-token class, §7/§8 the system-internals and bare-`agents/`-prefix closure classes, §10 the tone block). | — | Every rule the predecessor lane mechanised is present as a checklist row, each naming the **target artifact** it will audit (canary's 5 prompt files; the 6 edited persona files; the skill body + mirrors; the extended family test file). |
| 2 | **Read the persona exemplars** — `agents/aegis/{soul,rule,workflow,tools_note}.md`, the three children (`agents/hacker|cracker|sweeper/rule.md`), and `agents/worker/rule.md` — and record the **shape each target persona must fit**: aegis ends at guideline `(f)` (`agents/aegis/rule.md:46-50`) at a **7/7 cardinal cap** ⇒ its doctrine lands as `### (g)`; the three children end at `(e)` ⇒ they append `### (f)`; `worker` uses the **legacy** `## Must` / `### 🚨 CRITICAL:` shape (`agents/worker/rule.md:5-65`) and `## Must Not` (`:202`). | 1 | A **shape table**: persona → current last section → where the doctrine block goes → the anchor line that must remain untouched. |
| 3 | **Read the skill exemplars** — `agents/aegis/skill-set.yaml` + `agents/aegis/skills-template/aegis-strategy.md` (the **seeded** precedent) and the file-invoked installer precedent (`agents/worker/skills-template/fuzzing-toolchain.md` + `.lib.sh`). Record the seeding mechanics from `daemon/services/skill_seed_service.py`: the manifest is enumerated (`:340`), the body must live at the **same-dir** `skills-template/<name>.md` (`:348-359`), a `requires:` entry forces the body to **start** with `capability_check(...)` (`:361-381`), and the **W4 version guard** means a same-version edit **never re-seeds** (`:436-468`) ⇒ a **version bump is the propagation lever**. | 1 | A **seeding decision record** stating, per adopted ruling D2-B, the screened roster, the canonical copy, the mirror obligation, and the propagation mechanism. |
| 4 | **Read the test exemplars** — `tests/unit/agents/test_aegis_security_family.py` (the 3409-line lane file: T1–T30, the `_LEGALITY_MATRIX` at `:158`, `LEGALITY_CELLS` at `:194`, the completeness leg `len == 20` at `:782`, the single-site self-proof at `:771`) and `tests/unit/test_report_integrity_prompts.py` (the empty-team skip at `:242`). | 1 | The **fold list** is exact: which shared legs change value (T4 team set; T29 matrix + completeness count) and which stay **byte-stable**; plus the report-integrity posture under adopted ruling D1-A (canary = leaf ⇒ skipped). |
| 5 | **Read the fence precedent** — `daemon/graph.py::_frame_injected_report` (`:343-373`), the only model-visible untrusted-data frame in the system, plus the intake-surface inventory (bash `daemon/tools/bash.py:777`; `read_file` absolute-path handling `daemon/tools/filesystem.py:99,:162-168,:88`; MCP verbatim results `daemon/mcp/tool_adapter.py:105-111`; RAG `daemon/tools/knowledge_tools.py:678-679`; context auto-injection `daemon/services/context_tools.py:225-259`; the 4000-char task-context cap `daemon/tools/instance.py:116`). | — | The **surface list** is confirmed first-hand (not from memory) and each entry records the owning agent set. P1 and P3 consume this list; P3 turns it into the campaign map. |
| 6 | **Record the adopted rulings (D1–D7)** — the architect gate is **CLOSED** (`architecture-recommendation.md`, G0 complete). Replace the pre-ruling "default assumption" table with an **adopted rulings** table: one row per D with ruling ID, ruling letter, ruling summary, and the phase it folds into (D1=A lean compose-and-hand; D2=B corrected auto_load; D3=A landing sites + two-layer N; D4=A corpus path; D5=A family file grows; D6=A core 5 legs; D7=A tool surface + payload inertness). Verify each row against `architecture-recommendation.md` §0 digest and §11 fold-cost. | — | The adopted-rulings table exists in `plan-overview.md` §"Architect-question dependency map (D1–D7)"; every phase's "consumes D" line agrees with it; NC-1…NC-5 are marked CLOSED-BY-RULING. |
| 7 | **Verify the merge-overlap premise** — confirm the memoir lane's state read-only: `git branch -a --list '*memoir*'`, `git diff --name-only 1f6898ae2..origin/feature/ari-memoir-store -- agents/` (expected: **empty**), and the migration census (`ls daemon/migrations/versions/*.sql | wc -l` ⇒ 119 at base). **No branch, worktree, file or migration of the memoir lane is touched.** | — | The premise is confirmed **first-hand** (zero `agents/` paths today) and the count is re-derived, not assumed. The §Merge-Overlap Strategy in the plan-overview stands or is corrected. |

---

## Gates

| Gate | Criterion |
|------|-----------|
| **G0.1** | The guide-compliance checklist exists with **one row per mechanically-checkable rule** and a **named target artifact** per row — no row without a target. |
| **G0.2** | The persona **shape table** exists and is consistent with the on-disk anchors (`aegis/rule.md:46-50`; the children's `(e)`; `worker/rule.md:5-65` / `:202`). |
| **G0.3** | The **D1–D7 adopted-rulings table** exists, and every phase file's "consumes D" line agrees with it. **No phase task hard-depends on an unanswered D.** |
| **G0.4** | The architect gate is **CLEARED** — rulings landed in `architecture-recommendation.md` (G0 complete); the adopted-rulings table is recorded; **plan-council review is the next gate**. |
| **G0.5** | The merge-overlap premise is confirmed first-hand (zero `agents/` paths on the memoir lane today; 119 migrations at base). |
| **G0.6** | The plan set is **committed** to the branch (planning docs only) — the house commits the planning set; the payload commit comes at P4. |

---

## Verification Commands

```bash
# The lane is on its own branch at the recorded base, in the lane worktree only.
cd /home/nea/ensemble-src-wt-canary-injection-defense
git rev-parse --abbrev-ref HEAD          # feature/canary-injection-defense
git rev-parse HEAD                        # 97b76e20e… (base; moves only by the lane's own commits)
git status --porcelain                    # the lane's own planning set only

# --- DERIVATION 1: the agent-tree and baseline numbers the whole plan set derives from ---
# base agents tree = 366 files (measured); canary adds exactly 4 prompt artifacts.
find agents -type f | wc -l
#   base            = 366
#   + canary tree     = +8  (meta.json, soul.md, rule.md, workflow.md, tools_note.md, skill-set.yaml,
#                              skills-template/injection-screen.md, skills-template/injection_scan.py)
#   + aegis mirror    = +1  (skills-template/injection-screen.md)      [aegis/skill-set.yaml MODIFIED]
#   + worker mirror   = +1  (skills-template/injection-screen.md)      [worker/skill-set.yaml MODIFIED]
#   + sweeper mirror  = +1  (skills-template/injection-screen.md) +1  (skill-set.yaml -- new)
#   (hacker + cracker: DOCTRINE ONLY under adopted ruling D2-B -- no skill copy, no manifest)
#   => expected P4 total = 378   (8 + 1 + 1 + 2 = +12; RE-DERIVE at P4 from the real file list;
#                                 the plan states the RULE)

wc -l < agents/.prompt-artifacts.sha256
#   base = 177 rows
#   + canary's 4 prompt artifacts (soul/rule/workflow/tools_note) = +4
#   => expected P4 total = 181   (collector is depth-2; meta.json/skill-set.yaml/skills-template
#                                   contribute ZERO; edited files change HASHES, not COUNTS)
#   CONDITIONAL: D1's Option C (a decoy persona) would add +4 rows => 185.  181 holds under D1 = A.

# --- DERIVATION 2: discoverable agents (boot-scan census) ---
ls -d agents/*/ | grep -v '/_' | wc -l
#   base = 43 discoverable (47 on disk, 4 '_'-prefixed skipped)
#   + canary = 44

# --- DERIVATION 3: persona shape anchors the doctrine blocks must fit ---
grep -n '^### ' agents/aegis/rule.md | tail -1      # '### (f) Compliance With Project Conventions' (line 46)
for a in hacker cracker sweeper; do grep -n '^### ' agents/$a/rule.md | tail -1; done
                                                  # each ends at '### (e) Compliance With Project Conventions'
grep -n '^## ' agents/worker/rule.md | head -3       # '## Must' at :3, '## Must Not' at :202

# --- DERIVATION 4: the shared pins this lane folds (values at base) ---
grep -n '_LEGALITY_MATRIX\|LEGALITY_CELLS\|== 20' tests/unit/agents/test_aegis_security_family.py | head
grep -n 'test_aegis_team_members_exact' -A2 tests/unit/agents/test_aegis_security_family.py
                                                  # asserts == {hacker,cracker,sweeper,worker}  -> 5-set at HEAD

# --- DERIVATION 5: merge-overlap premise (READ-ONLY; the memoir lane is never touched) ---
git diff --name-only 1f6898ae2..origin/feature/ari-memoir-store -- agents/ | wc -l   # expected 0
ls daemon/migrations/versions/*.sql | wc -l                                          # 119 at base

# --- DERIVATION 6: the baseline checker's own caveat (it exits 0 when the file is ABSENT) ---
python3 scripts/protection/generate_prompt_artifact_baseline.py --check; echo "rc=$?"
#   rc=0 proves nothing on its own -> EVERY leg must ALSO assert existence + the exact row count
```

---

## Coupling

| Direction | Coupling | Notes |
|---|---|---|
| **in** | — | The plan set + the sibling files. Read-only against the repo. |
| **out** | **P1** (tight) | P1 consumes the skill exemplars (task 3) and the fence precedent + surface list (task 5). |
| **out** | **P2** (tight) | P2 consumes the persona shape table (task 2) and the guide checklist (task 1). |
| **out** | **P3** (tight) | P3 consumes the shape table, the seeding record, the surface list and adopted ruling D1-A. |
| **out** | **P4** (loose) | P4 consumes the fold list (task 4) and the derived counts (tasks 6–7). |
| **out** | **P5**, **P6** | independent — the gate definitions live in the plan-overview. |

**Close-condition for the plan set:** the architect gate is cleared (`architecture-recommendation.md`, G0 complete), the adopted-rulings table and the checklist exist, and the plan set is committed. Plan-council review is the next gate. P1 may start.
