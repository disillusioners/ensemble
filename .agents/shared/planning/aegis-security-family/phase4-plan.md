# Phase 4: The One-Commit Pin Ceremony + the T1–T28 Test File + the Leader One-Liner

## Objective

Land the lane's entire payload in **exactly one commit**: the four agent trees (28 new files), the
`agents/leader/meta.json` one-liner, the regenerated prompt-artifact baseline (161 → 177), the new
lane test file, **and the C1(b) 16→17 pin fold in `tests/unit/test_maintenancer_agent.py`** (council-D3
completeness — the sanctioned fifth element) — with **no** content change under `agents/**` after the baseline re-stamp.

The one-commit rule is house doctrine (B2): agent files and the regenerated baseline ship together, so
no window exists in which the baseline is stale against the tree.

---

## Entry Criteria

- **Phases 1, 2 and 3 have exited** — all 28 agent files exist and are final; the guide-compliance
  checklist is satisfied for every authored file.
- The test-file pattern exemplars are read: `tests/unit/agents/test_sketcher_agent.py` (the rich
  exemplar — `test_allow_set_exact`, `test_closed_allow_is_the_fence`, `test_rule_md_at_most_seven_cardinals`,
  `test_tone_block_in_soul`, `test_no_md_path_tokens_in_prose`, `test_no_system_internals_in_prose`,
  `test_skill_set_entries_have_templates_and_match_versions`, `test_agent_registry_scan`) and
  `tests/unit/test_maintenancer_team_members_within_team.py` (the pure-file-parsing org-chart test —
  the shape for the aegis team-topology pin). **Note (council-D6, delta re-gate — supersedes the pre-C1(a)
  wording):** the brief's reference to
  `tests/unit/agents/test_maintenancer_agent.py` is stale — **the file exists at
  `tests/unit/test_maintenancer_agent.py` (C1(a)/B8); its `test_leader_team_members_length_16` pin
  (16→17, "+1 aegis") is folded in task 4 of this phase.**
- **A venv is required for the registry-scan leg** (T28): `tests/unit/agents/test_sketcher_agent.py:610-630`
  spawns a probe with `sys.executable` because the system `python3` lacks `pydantic` (D9). The
  **baseline re-stamp itself is venv-free** (B2) and is run with the bare host `python3`.

---

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | **Author `tests/unit/agents/test_aegis_security_family.py`** — pure file parsing wherever possible (no daemon import, no DB, no LLM) so the file runs on bare `python3`; the single exception is T28, which is venv-bound. Implement **T1–T28** exactly as enumerated in `technical-analysis.md` §DP7 **as amended**, **plus the discrete T29 block (Blocker 4 — legality-cardinal pin matrix; registered as T29 rather than extending T1–T28 in place because T-number references are cross-cited across five files and a renumber would destabilise them — the planner registration decision, recorded)** (the table is the contract; the AM-corrected definitions below govern): T1–T4 aegis's allow/deny/`deny_spawn`/roster (**the AM-1/AM-2-corrected sets** — 15-category allow, 6-entry deny); T5–T7 the children's leaf teams, non-empty allow, write fence; T8–T11 no privileged category / no `job` / no `instance_read`/`instance_ops` / no `mcp_full_access`; **T12 the report-scrutiny fold (AM-9): asserts the UNION of `{rule.md, workflow.md, soul.md}` ∪ `{skills-template/*.md}` (mirroring `tests/unit/test_report_integrity_prompts.py:250-265`; PROMPT_SURFACE excludes `tools_note.md`) — and a companion leg asserts `aegis-strategy.md` carries NO report-delivery boilerplate (the snippets live in `rule.md`)**; **T13 the children's exemption (AM-9): a NEW stricter house pin modelled on the sketcher precedent (`test_sketcher_agent.py:195`), not a copy — the children are exempt via the empty-team skip, NOT `GRANDFATHERED_PARENTS`**; T14–T18 the prose hygiene set (system internals, `.md` path tokens **and the bare `agents/`-prefix token class** (W6 — `BARE_AGENTS_PREFIX_RE`) with the **enumerated exemptions**, cardinal count, tone block, adapted-from provenance — **T18's forbidden-token list is EXTENDED per AM-8: `preserved verbatim from` and the `per <agent> pattern, job <id>` shape are also refused**); T19 the org-chart rule for the strategy skill; T20 the leader one-liner; **T21 the seeded-skill discipline (AM-7): exactly one `skill-set.yaml` (aegis's, one entry); the three installer skills in no manifest; AND `include` ∉ `aegis-strategy`'s frontmatter (the include resolver's cross-agent by-name fallback would inline another agent's bank row)**; T22 the version match; **T23 the identical FENCES block (AM-10 definition): PRIMARY = the three `.md` FENCES bullet lists byte-identical, scope = THE FENCES BULLET LIST ONLY (MUST include the C3 checksum bullet; MUST exclude per-tool content — release-asset URLs, checksum sources, tool flags) — **council-D4: the byte-identical unit includes wrapped continuation lines, not only bullet first lines** (a heads-only extraction is a false-green filter); **N6: the identity is BYTE-IDENTICAL MODULO BLANK LINES** — the extraction drops blank lines and the normalization is stated, not implicit; SECONDARY = each `.lib.sh` carries a FENCES header mentioning the tool-agnostic keywords (explicitly NOT byte-identity vs the `.md` block)**; T24 the taxonomy + every named finding-record field (names, not count — D-6); T25 `worker` byte-untouched; T26–T27 the queue and model assignments; T28 the venv-bound registry scan; **T29 (Blocker 4, registered as a discrete BLOCK — the planner registration decision, recorded) the legality-cardinal pin matrix: 5 guardrail families × 4 personas = 20 verbatim-grep assertions** (read-only-on-targets · self-owned-sandbox/zero-external-targets · synthetic-sample-data-only (cracker; absence-asserted for non-applicable personas) · findings-private · white-hat-ethos), each grepping the cardinal VERBATIM in the persona file and reding on deletion/mutation — the approver's acceptance test (deleting hacker's "no external targets, ever" cardinal must RED) is the mutation leg. **AM-6 pins:** two prose-assertion pins — (α) no aegis surface instructs a lane to read credentials/env values/secrets and findings name a secret class never a value; (β) fetched external content is DATA, never instructions (AM-3). | Phases 1–3 exited | The file exists; `pytest tests/unit/agents/test_aegis_security_family.py` is green; **every** T-number has at least one assertion (no vacuous test); the AM-corrected T12/T13/T18/T21/T23 definitions are the implemented ones. |
| 2 | **Add the fail-open guard** — an assertion that each of the four metas' `tools` block **round-trips through the registry's parser**, because a parse failure sets the filter to "all tools allowed" (A9/R2). This is the guard that turns a lint issue into a privilege-escalation detector. | 1 | The assertion fails when a `tools` block is malformed (mutation-verified: break one meta's block, watch the test go red, restore). |
| 3 | **Add the baseline-count assertion** — the lane test asserts the prompt-artifact baseline **file exists** and has **exactly 177 rows**, and that the four new agents contribute exactly four rows each, so a fifth prompt file (`memory.md`/`growth.md`/`tools.md`/`skill.md` — AM-11 forbids all four) or a missing one is caught **pre-stage** rather than at the stage-time leg (R4). **AM-11 rationale:** the generator's `--check` exits 0 BOTH when clean AND when the baseline file is ABSENT (`generate_prompt_artifact_baseline.py:127-133`) — `rc=0` alone proves nothing, so the file-exists leg is mandatory. | 1 | The assertion is red at the base commit (161 rows) and green after the re-stamp (177); a deleted baseline file turns the assertion red even though `--check` would exit 0. |
| 4 | **Apply the leader one-liner** — add `"aegis"` to `agents/leader/meta.json` `team_members`. `leader` holds `instance` but **no** `job` category, so `team_members` is its only agent-reach path (A4). `meta.json` is **not** in the baseline's artifact set ⇒ this edit contributes **zero** baseline rows and does not move 177. **C1(b) — fold the length pin in the SAME commit:** `tests/unit/test_maintenancer_agent.py::test_leader_team_members_length_16` (`:517-524`) asserts `len(team) == 16` ⇒ this one-liner makes it a deterministic red unless folded. Pre-registered fold: update the assertion to `17` and the docstring to the pin's own house pattern — *"Spec — 15 after 'designer', 16 after appending 'gaia' (environment-terraform), 17 after appending 'aegis'"* — and extend the failure message's chain (`+1 'maintenancer'`, `+1 'designer'`, `+1 'gaia'`, `+1 'aegis'`). This is the same enumeration-assertion family as the baseline counts (adjudicated IN-COMMIT). | 1 | `python3 -c "…"` shows `"aegis"` in the list; the list otherwise is unchanged; the meta diff is exactly one line; the pin test asserts 17 and its docstring names the aegis append. |
| 5 | **Re-stamp the baseline — LAST.** Run the venv-free generator: `python3 scripts/protection/generate_prompt_artifact_baseline.py`. It writes `agents/.prompt-artifacts.sha256` atomically (tmp + rename) and prints `PROMPT-BASELINE: GENERATED — <path> (177 artifact(s))`. **No `agents/**` content change may follow this step.** | 1–4 | `wc -l agents/.prompt-artifacts.sha256` ⇒ **177**; `python3 scripts/protection/generate_prompt_artifact_baseline.py --check` ⇒ **exit 0**. |
| 6 | **Verify the check mode** — `--check` is read-only: exit 0 clean, exit 1 drift, exit 2 no tree. Run it and confirm exit 0. Confirm the file has **no** added/missing rows (the check prints them). | 5 | Exit 0; no drift lines. |
| 7 | **Prove the `worker` no-touch** — `agents/worker/**` is byte-identical to the base commit. T25 automates this; the author-side check is `git diff --stat 1f6898ae2 -- agents/worker/` ⇒ **empty**. | 1–4 | Empty diff for `agents/worker/`. |
| 8 | **Prove the lane's scope** — the working tree contains **only** `agents/**` and `tests/**` changes. | 1–4 | `git status --porcelain` shows nothing under `daemon/`, `docs/`, `scripts/`, or `.agents/` (except the planning dir, which is committed separately). |
| 9 | **Make the one commit** — stage exactly `agents/`, `tests/unit/agents/test_aegis_security_family.py`, **and the leader-length pin fold in `tests/unit/test_maintenancer_agent.py` (C1(b): the 16→17 assertion + docstring)**, then commit with the house commit form: `feat(aegis): add aegis security family (orchestrator + hacker/cracker/sweeper) with one-commit pin ceremony`. **Never `git add -A`** (the house hazard: a scoped add is required). Then `git show --stat HEAD` to prove the composition. | 1–8 | **The atomic pin-ceremony commit sanctions EXACTLY this set (C1(c)):** the 28 new agent files + `agents/leader/meta.json` (1 line) + the regenerated `agents/.prompt-artifacts.sha256` + the new lane test file + **the 16→17 pin fold in `tests/unit/test_maintenancer_agent.py`** — and **nothing outside this enumerated set**. `git status --porcelain` is clean afterwards. |

---

## Gates

| Gate | Requirement | How it is checked |
|---|---|---|
| **G4.1 — the test file is green** | `pytest tests/unit/agents/test_aegis_security_family.py` | all T1–T28 pass; no vacuous test |
| **G4.2 — the fail-open guard bites** | Breaking one meta's `tools` block turns the lane test red | mutation check (task 2) |
| **G4.3 — the baseline is exactly 177** | `wc -l` + the generator's own count + the lane test's assertion (**AM-11: the file-exists leg is part of the gate — `--check` exits 0 even when the file is absent**) | the file **exists**, 177 rows, `--check` exit 0 |
| **G4.4 — the re-stamp was last** | No `agents/**` content change after the re-stamp | `git show --stat HEAD` ordering + the `--check` exit 0 run **after** the commit |
| **G4.5 — one commit** | The whole payload is in a single commit | `git show --stat HEAD` — 28 new agent files + `agents/leader/meta.json` + `agents/.prompt-artifacts.sha256` + the lane test file **+ the C1(b) 16→17 pin fold in `tests/unit/test_maintenancer_agent.py` (the sanctioned fifth element — nothing outside the enumerated set)** |
| **G4.6 — worker untouched** | `git diff --stat 1f6898ae2 -- agents/worker/` | empty (T25) |
| **G4.7 — LANE-OWN scope (Blocker 1+2)** | The **lane-own diff** (this branch's own commits, pre-merge) touches only `agents/**`, `tests/**` and the committed planning set (complement == exactly `.agents/shared/planning/aegis-security-family/` — W4). **The merge commit, the bump commit, the tag and the giter choreography are OUTSIDE this gate** — owned by the giter's standard sequence (P6), asserted separately by P6's G6.1a. | the complement diff assertion in the commands (lane-own diff) |
| **G4.8 — clean tree** | Nothing uncommitted after the commit | `git status --porcelain` ⇒ empty |
| **G4.9 — guide §10 pre-commit checklist** | The guide's own pre-commit checklist passes for the whole lane before the commit: no system internals; one canonical home per repeated artifact; no false "stated once" claims; ≤7 cardinals per `rule.md`; cross-references resolve (the §3 closure grep's survivors are enumerated); the tone directive present in each `soul.md`; the fan-in escape valve defined in aegis's `workflow.md`; skill versions consistent; fallbacks stay within `team_members`; no adapted-from / migrated-from / verified-from provenance; **tests pass**. | The Phase-0 checklist (all rows) + the green lane test |

---

## Verification Commands

```bash
cd /home/nea/ensemble-src-wt-aegis-security-family

# 0) venv (needed for the lane test and the registry-scan leg T28; the baseline re-stamp does NOT need it)
uv sync                     # creates .venv from uv.lock; requires-python >=3.13

# 1) the lane test — file-parsing legs run on bare python3 too; T28 needs the venv
.venv/bin/python -m pytest tests/unit/agents/test_aegis_security_family.py -q
# bare-python subset (the file-parsing legs only):
python3 -m pytest tests/unit/agents/test_aegis_security_family.py -q -k "not registry_scan"

# 2) the leader one-liner is in place
python3 -c "import json;m=json.load(open('agents/leader/meta.json'));assert 'aegis' in m['team_members'];print('leader -> aegis OK')"

# 3) THE RE-STAMP (venv-free, bare host python3) — RUN THIS LAST, after every agents/** edit
python3 scripts/protection/generate_prompt_artifact_baseline.py
# expect: PROMPT-BASELINE: GENERATED — .../agents/.prompt-artifacts.sha256 (177 artifact(s))

# 4) verify (read-only; exit 0 clean / 1 drift / 2 no tree)
python3 scripts/protection/generate_prompt_artifact_baseline.py --check; echo "exit=$?"
wc -l agents/.prompt-artifacts.sha256      # expect 177
test -f agents/.prompt-artifacts.sha256 && echo "baseline file EXISTS (AM-11: --check exits 0 even when absent — rc=0 alone proves nothing)"

# 5) the composition proof
# N2 (council-D2, delta re-gate): the copy-pasteable staging command MUST carry the fifth element
# (tests/unit/test_maintenancer_agent.py — the C1(b) 16→17 pin fold) or an implementer
# following it literally commits 4 of the 5 sanctioned elements. Scoped form kept
# (never `git add agents/ tests/` — the scoped add is the house hazard mitigation).
git add agents/ tests/unit/agents/test_aegis_security_family.py tests/unit/test_maintenancer_agent.py
git status --porcelain                     # inspect the staged set BEFORE committing
git commit -m "feat(aegis): add aegis security family (orchestrator + hacker/cracker/sweeper) with one-commit pin ceremony"
git show --stat HEAD
git status --porcelain                     # expect empty

# 6) the scope + no-touch proofs — BLOCKER 1+2: the scope gate runs over LANE-OWN commits
#    (this feature branch's own commits, PRE-merge). The merge commit, the bump commit, the tag
#    and the giter choreography are explicitly OUTSIDE the lane-own scope-gated set — they are
#    owned by the giter's standard sequence (phase 6) and get their own assertion leg there.
#    POSIX-safe form — /bin/sh is dash and process substitution is a PARSE error there (council-D2):
git diff --stat 1f6898ae2 -- agents/worker/          # expect EMPTY
git diff --name-only 1f6898ae2 | grep -v -E '^(agents/|tests/)' | sort > /tmp/aegis-p4-complement.txt
git diff --name-only 1f6898ae2 | grep '^\.agents/shared/planning/aegis-security-family/' | sort > /tmp/aegis-p4-planning.txt
if diff /tmp/aegis-p4-complement.txt /tmp/aegis-p4-planning.txt; then
  echo "lane scope OK (lane-own diff): complement == exactly the planning set"
else
  echo "FAIL: out-of-scope paths beyond the planning set"; exit 1
fi

# 7) the baseline is still clean AFTER the commit (proves the re-stamp was last)
python3 scripts/protection/generate_prompt_artifact_baseline.py --check; echo "exit=$?"
```

**House notes for this phase:**

- **`git add` is scoped, never `-A`.** The house hazard is real (a prior bump script's `git add -A`
  swept unrelated files into a release commit).
- **`uv sync` is lazy** — it is not needed before the baseline re-stamp, only before the pytest runs.
- **`pytest -q` buffers tracebacks to the end of the session**; for a noisy run, use
  `… | tee /tmp/aegis-lane.log >/dev/null && tail -2 /tmp/aegis-lane.log`.
- The default pytest addopts deselect `integration` / `postgres` / `slow` / `conformance`; the lane
  test lives in `tests/unit/`, so it is collected by default.

---

## Coupling

- **Tight with:** P1, P2, P3 — this phase consumes their content wholesale; the commit payload **is**
  their output. Any change to their files after the re-stamp breaks the baseline.
- **Tight with:** P5 — P5 verifies the artifact this phase produces.
- **Tight with:** P6 — P6 stages the tree this phase commits.
- **Independent of:** P0 — P0 was read-only.

---

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| **P4-R1** | A prompt file is edited **after** the re-stamp (a review fix, a wording tweak) ⇒ the baseline drifts and the stage-time leg goes red. | Medium | Medium | Task ordering (re-stamp is task 5, the commit is task 9) + the post-commit `--check` (verification step 7) + the lane test's exact-177 assertion. Any post-stamp edit ⇒ re-stamp and amend **before** the commit is finalised. |
| **P4-R2** | The commit accidentally carries unrelated files (a stray plan file, another lane's WIP). | Medium | Medium | Scoped `git add` only; `git status --porcelain` inspected **before** committing; `git show --stat HEAD` proves the composition afterwards. Never `git add -A`. |
| **P4-R3** | A test is vacuous (asserts nothing, or asserts a tautology) ⇒ the lane "passes" without pinning. | High | Medium | Every T-number must have a real assertion; the file is reviewed for vacuity before the commit; the mutation checks (task 2, and the task-1 mutation legs) prove the assertions bite. |
| **P4-R4** | The registry-scan leg (T28) fails on this host for an environment reason (no venv, missing `pydantic`) and is skipped rather than fixed. | Medium | Low | `uv sync` is a task-0 prerequisite; the leg runs under `.venv/bin/python`; a **skip** is not an acceptable outcome for T28 — the registry scan is the boot-scan proof (S3). |
| **P4-R5** | The children end up with a `skill-set.yaml` (each would add a manifest and break the file-invoked discipline). | Medium | Low | T21 asserts that the family's only manifest is aegis's, with exactly one entry; the file set is enumerated in the phase-0 manifest. |
| **P4-R6** | The baseline count is not 177 because a new agent gained a `memory.md` or a fifth prompt file. | Medium | Low | The count is asserted in the lane test (task 3) and re-derived at stage time; the plan states the derivation rule, and Phase 0 froze the exact file set. |

---

## Effort

**≈ 0.5 working day** — the T1–T28 test file is the bulk (28 assertions, several of them per-agent
parameterisations), plus the leader edit, the re-stamp and the commit.

---

## Exit Criterion

Phase 4 is done when **all nine gates (G4.1–G4.9) are green**:

1. the lane test file is green (T1–T28), with no vacuous test;
2. the fail-open guard demonstrably bites on a broken `tools` block;
3. the baseline has exactly **177** rows and `--check` exits 0;
4. the re-stamp was the **last** `agents/**` content change (proved by a post-commit `--check` at exit 0);
5. the whole payload is in **one** commit whose `git show --stat` shows exactly the 28 agent files + the
   leader edit + the baseline + the test file **+ the C1(b) 16→17 pin fold in
   `tests/unit/test_maintenancer_agent.py` (the sanctioned fifth element — council-D3 completeness)**;
6. `agents/worker/**` is byte-identical to the base;
7. the **lane-own diff** (pre-merge) touches only `agents/**`, `tests/**` and the committed planning set (complement == exactly `.agents/shared/planning/aegis-security-family/` — W4); the giter set (merge/bump/tag) is outside this gate and asserted in P6 (Blocker 1+2);
8. the working tree is clean;
9. the guide's §10 pre-commit checklist passes for the whole lane.

**Completeness-scope note (N7, council delta-3, 2026-11-04):** the council-D3 sweep's result is
stated precisely — **zero remaining 4-element composition enumerations in CONTRACT-BEARING sites**
(every site that defines, gates, or stages the one-commit set now carries the fifth element). Two
NON-contract sites are known and accepted as-is: the file-count claim in this phase's entry criteria
("all 28 agent files exist" — a file-existence statement, not a commit-set enumeration) and the
architect's own arithmetic adjudication row in the read-only
`architecture-recommendation.md` §4 (architect-owned; an arithmetic claim, not a commit-set
enumeration). Neither defines nor stages the ceremony set.

Phase 5 then runs the full verification arc (Gates A–H) against this commit.
