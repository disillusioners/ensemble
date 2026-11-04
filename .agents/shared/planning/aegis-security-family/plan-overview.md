# Plan Overview: Aegis Security Family

**Date:** 2026-11-04
**Author:** planner[v2] via plan-creation worker
**Status:** Ready for Review — awaiting architect/council/approver gate (Phase 0 exit criterion)
**Commission:** `aegis-security-family` (user-ratified design, Discord 2026-11-04)
**Worktree / branch:** `/home/nea/ensemble-src-wt-aegis-security-family` @ `feature/aegis-security-family` (base `1f6898ae2889ff002b4656486f6be3ee1048b649` = `origin/latest` at lane start)

---

## Objective

Deliver a **four-agent white-hat security family** — an orchestrator-class `aegis` (peer of `leader`)
plus three leaf analysis lanes (`hacker` offensive / `cracker` protection-strength / `sweeper` SAST),
with the existing `worker` joining as the shared generalist lane — such that a security engagement can
be scoped, dispatched in parallel, adjudicated into severity-ranked findings (severity + `file:line` +
concrete fix), handed to `leader` for remediation, and re-verified per finding, with **zero code
modification by the security family itself**.

Completion marker: the family is **staged** (a release artifact exists with full provenance) and every
gate in §Success Criteria is green — the flip to live remains the user's nonce ceremony.

---

## Scope

### In Scope

- **Four new agent trees under `agents/`** — `aegis`, `hacker`, `cracker`, `sweeper`, each with
  `meta.json` + `soul.md` + `rule.md` + `workflow.md` + `tools_note.md` (**28 new files total**;
  no `memory.md`, no `growth.md` — see the baseline-count constraint below).
- **Four skill bodies + three installer libraries** — the seeded `aegis-strategy` methodology skill
  (canonical home for the severity taxonomy + the finding record — ten named fields, see
  `decisions.md` D-6) and three **file-invoked**
  installer skills (`fuzzing-toolchain`, `credential-bench`, `sast-toolchain`) each with a `.lib.sh`.
- **One line in `agents/leader/meta.json`** — add `"aegis"` to `team_members` (the only agent-reach
  path `leader` has, A4).
- **One new test file** — `tests/unit/agents/test_aegis_security_family.py` (tests T1–T28, DP7).
- **One regenerated prompt-artifact baseline** — `agents/.prompt-artifacts.sha256`, **161 → 177 rows**,
  re-stamped in the **same commit** as the agent files (B2 doctrine).
- **A staged release** with full provenance (P6). **NO promote.**

### Out of Scope

| Item | Reason |
|---|---|
| **Any `daemon/` change** | Commission constraint (E2). A new tool category would trigger the 3-step registration seam + `tool-categories.json` regeneration — the F-1 trap class (B3). |
| **Any change to `agents/worker/**`** | Commission constraint "ZERO changes to worker" (A10). Proven mechanically by T25. |
| **Any change to `agents/ari/**`** | The `job_create` path already reaches any agent with **no team gate** (A3) — `ari` needs nothing. |
| **Critical-notes channel for findings** | Leader-curated, leader-only pin/supersede verbs (D11). The hand-off is report *content* in a message to `leader`. |
| **v2 deferrals** — the `forensic` child, the `defender`/`patcher` child | Commission constraint (E5). Remediation stays with `leader`'s `developer`/`reviewer`. |
| **A real CVE-database tool category** | Q3 — would be a new category ⇒ out of scope; v1 ships `mcp` (builtin `webfetch`, deployment-conditional) + `bash curl` on `aegis` only. |
| **An agent-meta supervision knob** | Q4 — the per-agent `watchover` key is **inert** (D7); the real lever is the per-instance REST API, which is a daemon concern. |
| **Promoting the release to live** | The user's 3-factor nonce ceremony owns the flip (E7). The promote command is **recorded, not executed**. |
| **The vkey first engagement run** | Requires a **live** family ⇒ post-ceremony. See §Post-lane. |
| **Docker; system packages** | House decision + commission constraint (E6). No Docker on this host; no `apt` without reporting first. |

---

## Phases

| Phase | Name | Objective | Tasks | Coupling | Gate (independently checkable) | Status |
|-------|------|-----------|-------|----------|-------------------------------|--------|
| 0 | [Plan review + exemplar read + compliance checklist](phase0-plan.md) | Clear the plan-review gate and produce the guide-compliance checklist every later phase is audited against | 7 | independent (read-only) | Architect/council/approver sign-off **AND** the checklist exists with a named target artifact per guide rule **AND** Q1–Q4 answered | pending |
| 1 | [The `aegis` orchestrator](phase1-plan.md) | Author `aegis` — the orchestrator persona, the methodology skill (taxonomy + finding record), the dispatch/fan-in/re-verify doctrine | 9 | tight w/ P2 (shared legality language), tight w/ P3 (skill-body FENCES) | Guide checklist satisfied **for `aegis` alone**; T1–T4, T12, T14–T18, T24, T26, T27 green; strategy-skill frontmatter version == manifest version | pending |
| 2 | [The three analysis children](phase2-plan.md) | Author `hacker`, `cracker`, `sweeper` — three leaf lanes with explicit white-hat legality in **each** persona | 10 | tight w/ P1, tight w/ P3 | T5–T7, T13–T18, T26, T27 green for all three; each child's own legality language present | pending |
| 3 | [The three installer libraries](phase3-plan.md) | Author the three `.lib.sh` installer libraries — the charter skeleton, hardened with sha256 release-asset verification | 10 | tight w/ P2 (skill bodies) | T23 green (identical FENCES block); each library has a checksum step, a readiness probe returning 0/1/2, and a mandatory reversal section | pending |
| 4 | [Pin ceremony + tests + the leader one-liner](phase4-plan.md) | Land **one commit**: 4 agent trees + leader edit + regenerated baseline + the new test file | 9 | tight w/ P1–P3 (consumes their content) | `generate_prompt_artifact_baseline.py --check` ⇒ exit 0; the baseline has **exactly 177 rows**; `git show --stat HEAD` proves the one-commit composition; the re-stamp was the **last** `agents/**` content change | pending |
| 5 | [Verification](phase5-plan.md) | Prove net-new reds = 0 and every commission expectation holds, on the committed branch | 9 | tight w/ P4 (verifies its output) | Gates **A–H** all green (see §Verification gates) | pending |
| 6 | [Staging handoff](phase6-plan.md) | Produce a staged release with full provenance and the promote command recorded (not run) | 8 | tight w/ P5 | 4-way sha match, sidecars clean, **EMPTY** migration delta, agents tree re-derived, live daemon untouched, promote command recorded | pending |

---

## Coupling Map

| | P0 | P1 | P2 | P3 | P4 | P5 | P6 |
|---|---|---|---|---|---|---|---|
| **P0** | — | tight (checklist is the gate) | tight (checklist is the gate) | loose | loose | independent | independent |
| **P1** | tight | — | **tight** (shared legality language + one voice for the white-hat ethos) | **tight** (aegis's skill body is the template the three installers follow) | tight (P1's files are the commit payload) | loose | independent |
| **P2** | tight | **tight** | — | **tight** (each child's `.md` declares the FENCES block the `.lib.sh` implements) | tight (payload) | loose | independent |
| **P3** | loose | tight | **tight** | — | tight (payload) | loose | independent |
| **P4** | loose | tight | tight | tight | — | **tight** (P5 verifies P4's commit) | loose |
| **P5** | independent | loose | loose | loose | **tight** | — | **tight** (P6 stages what P5 verified) |
| **P6** | independent | independent | independent | independent | loose | **tight** | — |

**Tight couplings and their shared contracts:**

1. **P1 ↔ P2 — the legality voice.** The white-hat guardrails (self-owned sandboxes only, zero
   external targets, sample-data-only cracking, findings private, read-only on targets) must read as
   *one doctrine* expressed at two altitudes: `aegis`'s policy cardinal + each child's own operational
   legality language. Divergence here is the commission's central risk ⇒ **one shared wording pass in
   P2 before either phase closes** (P2 task 8 re-reads P1's cardinals).
2. **P2 ↔ P3 — the skill body / library split.** Each installer `.md` states the FENCES and the
   procedure; each `.lib.sh` implements them. They must agree **bullet for bullet**; T23 pins the FENCES
   block identical across all three (see `decisions.md` deviation D-1 for the exact pin target).
3. **P4 → P5 → P6 — the artifact chain.** P4 produces the commit, P5 proves it, P6 ships it. P5 must
   not start before P4's exit criterion; P6 must not start before P5's gates A–H are green.

**Independent couplings:** P0's exemplar read touches the repo but changes nothing; P6's staging
machinery is orthogonal to the family's content.

---

## The one-commit pin ceremony (binding, P4)

```
agents/aegis/{meta.json, soul.md, rule.md, workflow.md, tools_note.md, skill-set.yaml,
              skills-template/aegis-strategy.md}                                    (+7)
agents/hacker/{meta.json, soul.md, rule.md, workflow.md, tools_note.md,
               skills-template/fuzzing-toolchain.md, …/fuzzing-toolchain.lib.sh}   (+7)
agents/cracker/{… skills-template/credential-bench.md, …/credential-bench.lib.sh}  (+7)
agents/sweeper/{… skills-template/sast-toolchain.md, …/sast-toolchain.lib.sh}     (+7)
agents/leader/meta.json                                          (MODIFIED — one line)
agents/.prompt-artifacts.sha256                                  (REGENERATED 161 → 177)
tests/unit/agents/test_aegis_security_family.py                  (NEW)
```

**Baseline arithmetic (exact under the four-file prompt set):** the collector is depth-2 only
(`daemon/tools/prompt_artifacts.py:355-377`) and covers `soul/rule/workflow/tools_note` (+ the legacy
`tools.md`/`skill.md`/`memory.md`/`growth.md`) ⇒ **4 agents × 4 prompt artifacts = +16 rows ⇒ 161 → 177**.
`meta.json`, `skill-set.yaml` and `skills-template/*` contribute **zero** rows (B1) — and the `leader`
edit touches `meta.json`, so it also contributes zero. **A `memory.md` or `growth.md` on any new agent
would add a baseline row and move the number** ⇒ the four new agents get exactly four prompt files each.

**Agents-tree arithmetic:** 338 (D6) + **28** new files = **366**; the `leader` edit modifies an existing
file. **Re-derive both numbers from the actual file list at P4 time** — the plan states the derivation
rule, not a frozen expectation.

**Ordering rule:** the re-stamp is the **LAST** content change under `agents/**` in the lane. Any prompt
edit after the re-stamp drifts the baseline and reddens the stage-time leg.

---

## Verification gates (P5 — each independently checkable)

| Gate | What it proves | How |
|---|---|---|
| **A** | No behaviour regression | Full suite, net-new deterministic reds **0** vs the true pre-fix base (same commands, same environment) |
| **B** | `SEALED_SHA_BASELINES` untouched (35 entries) | `tests/test_chart_image_delivery_e2e.py::TestPhaseDDoesNotModifySealedArtifacts` green on the branch (the commission's explicit proof leg) |
| **C** | The plane read-holder census is unchanged | `tests/unit/tools/test_plane_agent_surface_census.py` green with `DISCLOSED_READ_HOLDERS` still `{"watcher"}` — **not** extended to the aegis family (the E3 fold removes the exposure instead of whitelisting it) |
| **D** | The report-integrity fold landed | `tests/unit/test_report_integrity_prompts.py` green — `aegis`'s scrutiny guidance is found by the dynamic check |
| **E** | Boot-scan clean on the new agent set | T28 registry scan finds `aegis`, `hacker`, `cracker`, `sweeper`; plus the tester's isolated boot-scan leg |
| **F** | No tool-category drift (the F-1 proof) | `.agents/shared/metrics/tool-categories.json` byte-unchanged in the lane diff; `tests/unit/tools/test_tool_category_artifact.py` green |
| **G** | The manifest delta is re-derived from the real tree | `find agents -type f \| wc -l` ⇒ 366 (or the re-derived value), and the staged payload's agents map matches |
| **H** | Scope + privacy | the lane diff touches **only** `agents/**` and `tests/**`; no finding artifact, corpus, crash dump or secret is committed; `agents/worker/**` is byte-identical to the base |

---

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| **R1** | **The `bash` write path is unconstrained.** `filesystem` is one six-tool category with no read-only sub-category (A6); every lane holds `bash`, so `write_file`/`edit_file` denies are a *speed bump*, not a boundary. The family's "read-only on target codebases" guarantee is **doctrinal**. | **High** | High | (a) The limitation is stated **plainly** in the plan, in `aegis`'s persona, and in the report template — never implied containment. (b) The mechanical sandbox-ownership prefix check (DP3) gates every *active* technique. (c) The write-deny removes the easiest accidental mutation. (d) A persona cardinal names the legality line. (e) `decisions.md` records it as trade-off T1. |
| **R2** | **A malformed `tools` block is fail-OPEN** — a parse failure sets `tools_filter = None` = *all tools allowed* (A9). On a security agent that is a privilege escalation, not a lint error. | **High** | Medium | The lane test asserts every new meta's `tools` block **round-trips through the registry's parser** (added to T1–T3) and every meta is valid JSON with a `tools` key present. |
| **R3** | **The E3/E4 census traps.** An empty/absent `tools.allow` inherits the default universe, which includes the plane read tools ⇒ the frozen read-holder census goes RED; any `mcp_full_access` including plane goes RED (B3). | Medium | Medium | **T6** (explicit non-empty allow on every new meta) and **T11** (no `mcp_full_access`) are the folds, written *before* the family lands. `DISCLOSED_READ_HOLDERS` stays `{"watcher"}` — the exposure is removed, not whitelisted. |
| **R4** | **Baseline drift.** The baseline pins content hashes; a prompt edit after the re-stamp (or a fifth prompt file) silently moves the count and reddens the stage leg (B1/B2). | Medium | Medium | The re-stamp is the **last** `agents/**` content change (P4 ordering rule); `--check` runs **before** the commit and again in P5 Gate A; the row count is asserted **exactly 177** in the lane test, so a move is caught pre-stage. |
| **R5** | **Install-skeleton drift.** Three copies of the charter skeleton exist by design (DP2). The FENCES block — the one part that must not drift — is at risk. | Medium | Medium | **T23** pins the FENCES block identical across all three installer skill bodies; the pin test fails the lane on any bullet-level divergence. |
| **R6** | **Scope creep into `daemon/`** — a CVE-lookup category, a supervision knob, a per-agent allow-list change, or a `watchover` field. Each would trip the 3-step registration seam (F-1 trap) or the inert-key false-confidence trap (D7). | Medium | Medium | Q3/Q4 are architect decisions with **explicitly out-of-scope fallbacks**; P5 asserts the lane diff contains **only** `agents/**` and `tests/**`; P6's EMPTY migration delta is the independent proof. |
| **R7** | **Prompt-prose violations across 16 authored files.** System internals, `.md` path tokens, >7 cardinals, a missing tone block, a duplicated canonical home, an out-of-team fallback (guide §1–§10; C1). | Medium | High | The **guide-compliance checklist is an explicit gate in every authoring phase** (P0 produces it; P1/P2/P3 audit against it); T14–T19 mechanise the mechanically-checkable half (§1, §3, §5, §8, §10). |
| **R8** | **Findings privacy.** Off-repo storage keeps findings out of git, but the artifacts are then not PR-reviewable and need filesystem access; in-repo storage would land them in git history and risk a push to origin (DP4). | Medium | Medium | Q2 is an architect decision; under the recommended off-repo option the **severity summary travels inside the hand-off message** to `leader`, so the decision-maker is never blocked by file access. |

> **Finalize-guard note (not a table row).** Branch/tip drift is handled by the house **finalize
> guard**: at merge time, fetch and verify `origin/latest` is **unmoved** from the branch base — moved
> ⇒ **STOP** and re-derive the merge base rather than proceeding; the merge is `--no-ff`; the commit
> composition is proved with `git show --stat HEAD`; and the stage-time tip-identity check is honoured
> rather than overridden. A version slot is never reserved by this lane — it is re-derived at stage
> time.

---

## Success Criteria

Mapped to the commission's verification expectations (E8) plus the family's own quality bars.

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| **S1** | Baseline / pin green | `python3 scripts/protection/generate_prompt_artifact_baseline.py --check` (read-only, exit 0 clean / 1 drift) + `wc -l agents/.prompt-artifacts.sha256` | exit **0**, exactly **177** rows, zero drift |
| **S2** | The pin ceremony is one commit | `git show --stat HEAD` on the lane commit | exactly: 28 new agent files + `agents/leader/meta.json` (1 line) + `agents/.prompt-artifacts.sha256` (regenerated) + the new test file — **and nothing else** |
| **S3** | Boot-scan clean on the new agent set | T28 registry-scan probe (venv-bound) + the tester's isolated boot-scan leg | all four ids resolve in the registry; zero new entries in the plane read-holder census |
| **S4** | Tool-category allow-list pins hold | T1–T11 (allow set, deny set, `deny_spawn`, roster, leaf teams, non-empty allow, write fence, no privileged category, no `job`, no `instance_read`/`instance_ops`, no `mcp_full_access`) | **11/11 green** |
| **S5** | The report-integrity fold landed | T12 + the dynamic rot-mitigation test | `[REPORT SANITY:`, `interim, not completion` and `send_message` all present in `aegis`'s surface; the dynamic test green |
| **S6** | `SEALED_SHA_BASELINES` untouched + proof | `TestPhaseDDoesNotModifySealedArtifacts` | green; the 35 entries byte-unchanged |
| **S7** | `worker` is untouched | T25 (byte-identical to base) | `agents/worker/**` byte-identical to `1f6898ae2` |
| **S8** | No `daemon/` change | the lane diff's path list + P6's migration delta | the diff contains only `agents/**` and `tests/**`; migration delta **EMPTY** |
| **S9** | Guide compliance, all four agents | the Phase-0 checklist audited per phase + T14–T19 | every checklist row satisfied per authored file; T14–T19 green ×4 |
| **S10** | Install libraries are safe and complete | T23 + per-library inspection | FENCES block identical across the three; each has a sha256 release-asset verification step, a readiness probe returning **0 warm / 1 cold / 2 in-progress**, a release lock whose release runs on failure, an attempt cap of 2, and a mandatory reversal section |
| **S11** | The methodology is single-homed | T24 + guide §2 | the 5 severity levels and **every named finding-record field** live **only** in `aegis-strategy`; the four prompt files link by section name and do not restate them |
| **S12** | Seeded-skill discipline | T21 + T22 | exactly **one** `skill-set.yaml` in the family (aegis's, one entry); the three installer skills appear in **no** manifest (file-invoked mode ⇒ out of the global skill bank) |
| **S13** | Staged release with full provenance | P6's verification | 4-way sha match (release dir == worktree dist == manifest == sidecar); both sidecars `git_dirty=false`; payload source-less; prompt baseline clean in the payload; agents tree re-derived |
| **S14** | Standard lane — STAGE, never promote | P6's journal + the live fence | the promote command is **recorded** with the ceremony preconditions and **not executed**; the live daemon, the `current` symlink and the promote fields of the state journal are byte-identical throughout |
| **S15** | No behaviour regression | P5 Gate A | net-new deterministic reds **0** against the true pre-fix base |

---

## Dependencies

| Dependency | Kind | Notes |
|---|---|---|
| `origin/latest` @ `1f6898ae2` | external | the branch base; the finalize guard must find it unmoved at merge time (R9) |
| `agents/designer/{rule,workflow}.md`, `agents/sketcher/`, `agents/critic/` | in-repo exemplars | the dispatch-doctrine and leaf-shape templates (C4, A11) |
| `agents/charter/skills-template/install-mermaid-cli.{md,lib.sh}` | in-repo exemplar | the **only** in-repo installer template and the file-invoked-mode precedent (C2, C3) |
| `agents/architect/skills-template/security-design.md`, `agents/reviewer/skills-template/security-review.md` | in-repo adjacent surfaces | **cross-reference, never duplicate** (C5) — reference by section name only |
| `docs/agent-prompt-writing-guide.md` | in-repo binding spec | the checklist source (C1) |
| `scripts/protection/generate_prompt_artifact_baseline.py` | in-repo tool | venv-free baseline generator/verifier (B2) |
| `daemon/tools/prompt_artifacts.py` | in-repo (stdlib-only) | the depth-2 collector that defines the 177-row arithmetic (B1) |
| The **vkey** target (`~/Code/vkey`) | external, out-of-scope for this lane | sizes the personas/skills: C++17 engine + 5 platform hosts; C++ memory safety, IME keystroke processing, IPC, CI supply chain |
| A **user nonce ceremony** | external, user-owned | owns the promote flip; the family cannot be exercised live before it |

---

## Post-lane: the vkey first engagement

The commission's first real case is a security review of **vkey** — a C++17 IME engine fork with five
platform hosts (attack classes: C++ memory safety, IME keystroke processing, IPC, CI supply chain).
The family is **sized for that class in this lane** (DP2's per-skill sizing; DP4's C++-shaped severity
definitions). **The engagement run itself is a post-ceremony operational step**, not a gated phase of
this lane, for three reasons:

1. it requires a **live** family ⇒ the user's promote ceremony, which this lane does not perform;
2. it is a run against a real target tree, not a code artifact ⇒ it has no commit to gate;
3. a first engagement is *how the family is validated operationally*, and validating it before the
   prompts are exercised by their own test suite would be backwards.

**Prerequisites before the first engagement:** the staged release is promoted; the target tree is
readable at `~/Code/vkey`; the engagement scope and rules of engagement are agreed with the owner.
**Expected shape:** `aegis` opens an engagement (scope → STRIDE pass → severity taxonomy), creates the
sandbox root (`mktemp -d`), dispatches the three lanes in parallel under `system_parallel_queue`,
adjudicates the returned findings into the finding record, writes the report through `worker`, and
hands the **summary** to `leader`. `hacker`'s first target is the **keystroke-stream parser** (raw
untrusted bytes ⇒ libFuzzer primary, AFL++ secondary); `cracker`'s corpora are **synthetic only**;
`sweeper`'s `trivy` leg covers the **CI supply chain**.

See `decisions.md` §C-1 for the cut rationale (why there is no `phase7-plan.md`).

---

## Pointer index

| File | What it holds |
|---|---|
| [`research-findings.md`](./research-findings.md) | The verified evidence digest — facts **A1–A11** (mechanics), **B1–B8** (pins + test surface), **C1–C6** (guide + skills + `worker` today), **D1–D14** (first-hand facts), **E** (house constraints), **F** (open items). Every citation in the plan resolves here. |
| [`technical-analysis.md`](./technical-analysis.md) | The design resolution — **DP1–DP8**, the trade-off table, the scaling analysis, the technical-debt list, and the four architect questions **Q1–Q4**. This plan consumes it verbatim; it does not re-derive it. |
| [`plan-overview.md`](./plan-overview.md) | This file — the synthesis, the phase table, the coupling map, the risks, the success criteria. |
| [`phase0-plan.md`](./phase0-plan.md) | Plan review (architect/council/approver) + exemplar read + the guide-compliance checklist + the Q1–Q4 rulings. |
| [`phase1-plan.md`](./phase1-plan.md) | The `aegis` orchestrator — 7 files. |
| [`phase2-plan.md`](./phase2-plan.md) | `hacker`, `cracker`, `sweeper` — 21 files. |
| [`phase3-plan.md`](./phase3-plan.md) | The three installer `.lib.sh` libraries. |
| [`phase4-plan.md`](./phase4-plan.md) | The one-commit pin ceremony + the T1–T28 test file + the leader one-liner. |
| [`phase5-plan.md`](./phase5-plan.md) | Verification — gates A–H. |
| [`phase6-plan.md`](./phase6-plan.md) | Staging handoff — version derivation, stage, provenance, the recorded (not executed) promote command. |
| [`decisions.md`](./decisions.md) | The decision record — DP1–DP8 summarised, the leader one-liner, Q1–Q4 with recommendations, and the flagged deviations from `technical-analysis.md`. |

---

## Research Insights (the findings that shaped this plan)

1. **The authorization model is caller-side and deny-by-default** (A1) — aegis's roster is a pure
   *declaration* in its own meta; the children need no change; `worker` needs no change (A10); `ari`
   needs no change (A3); `leader` needs **one line** (A4).
2. **The allow list is two-axis** — categories in `tools.allow` silently add the category's backing
   agent to the effective team (A1). The one lever that trims an implied member without removing the
   tool is `tools.deny_spawn` ⇒ `deny_spawn: ["knowledge"]` keeps the KB lookup and drops the
   `explorer`/`kb-writer` spawn right.
3. **A closed allow is the fence** (D13) — the 17 privileged categories are unreachable under a
   non-empty allow, so a long deny list is dead weight *and* falsely signals an open allow (rejected in
   DP1).
4. **`innate_skills` widen the allow list** (D3) — declaring `["dynamic-skill","todo"]` *and* listing
   both categories in `tools.allow` makes the expansion a no-op, so the pinned allow **is** the
   effective surface (T1 pins both).
5. **The prompt baseline is depth-2 only** (B1) — 4 agents × 4 prompt files = exactly 161 → 177;
   `meta.json`, `skill-set.yaml` and `skills-template/*` contribute nothing.
6. **The baseline tool is venv-free** (B2) — it loads `prompt_artifacts.py` by file path precisely so
   the bare host `python3` can run it ⇒ the re-stamp is available before a venv exists.
7. **`worker` cannot make charts** (D4) — the commission assigns "report charts" to worker, but worker
   has no `chart` category and must not change ⇒ aegis holds `chart` and the implied `charter` member
   appears ⇒ **Q1**.
8. **The per-agent `watchover` key is inert** (D7) — `AgentMetadata` has no such field, the model is
   `extra="ignore"`, and no daemon code reads one ⇒ **Q4**; copying the sketcher block would create a
   false-confidence knob.
9. **`bash` string commands run through `/bin/sh`** (D10) — "loops go in script files" is a code fact,
   not a style preference ⇒ every installer library and every scanner script is POSIX-sh.
10. **The skill bank is global** (C2/C6) — anything seeded via `skill-set.yaml` becomes searchable by
    `worker` and every dynamic-skill agent ⇒ the three installer skills are **file-invoked** (the
    charter precedent) so their procedure text never enters the shared bank, and the "worker never runs
    offensive tools" boundary is enforceable rather than merely prose.
11. **A `tools` parse failure is fail-open** (A9) — a malformed `tools` block on a security agent is a
    privilege escalation ⇒ the lane asserts the round-trip (R2).
12. **The census test freezes the plane read-holder set at `{"watcher"}`** (B3) — a new agent with an
    empty allow goes red ⇒ the **E3 fold** (explicit non-empty allow) and the **E4 fold** (no
    `mcp_full_access`) are written *before* the family lands, not after.
13. **The report-integrity check is dynamic** (B4) — any agent with a non-empty team must carry the
    `[REPORT SANITY:` / `interim, not completion` / `send_message` guidance ⇒ **aegis** carries it;
    the three children are exempt (empty teams).

---

## Open Questions

Carried from `technical-analysis.md` §Open Questions, unresolved by design — **each is answered in
Phase 0 and recorded in `decisions.md` before Phase 1 begins**. Full recommendation and
"what changes under each answer" per question: [`decisions.md`](./decisions.md) §Q.

| # | Question | Recommendation | Changes the plan if answered otherwise |
|---|----------|----------------|------------------------------------------|
| **Q1** | May `aegis` hold `"chart"` (which also implies the `charter` spawn member) so the commission's "report charts" task is satisfiable without touching `worker`? | **Accept** — the chart artifact exists and the risk is nil | *Drop charts from v1* ⇒ remove `"chart"` from aegis's allow, update T1/T8 and the `tools_note` row; the report stays text + JSON. |
| **Q2** | Where do findings live — off-repo (`$HOME/.local/state/aegis/…`) or in-repo (`.agents/shared/security/…`)? | **Off-repo** — in-repo is committed ⇒ pushable, weakening "private within the project" | *In-repo* ⇒ authorise a `.gitignore` change (**outside** the lane's scope) and accept git-history exposure; the `aegis-strategy` paths and the `worker` dispatch change. |
| **Q3** | Is the v1 CVE-lookup surface (`mcp` builtin `webfetch` + `bash curl`, `aegis` only) acceptable given there is no web-search category? | **Accept for v1** | *Commission a category* ⇒ its own lane (3-step registration seam + `tool-categories.json` regeneration — the F-1 trap class); this lane's `mcp` grant is withdrawn and CVE context is dropped. |
| **Q4** | May the family omit a `watchover` meta block, relying on END TURN + the fan-in ladder + `proc` tracking + the per-instance watchover API? | **Accept** — the key is inert | *Require an agent-meta supervision knob* ⇒ that is a **daemon change** ⇒ out of this lane's scope; the four metas stay as designed and the knob becomes a follow-up lane. |

---

## Next steps (standard lane discipline)

This plan set is the **final deliverable of the planning phase**. Per the standard lane flow, the plan
is reviewed **before** any implementation begins:

```
plan (this set)  →  architect  →  council  →  approver  →  IMPLEMENT (P1…P4)
                 →  verify (P5)  →  STAGE (P6)  →  [user nonce ceremony — NOT this lane]
```

- **Phase 0's first task is the plan-review routing**: the plan set goes to the architect, then the
  council, then the approver. **Implementation starts only after that gate clears** — the commission's
  "craft the full plan and implement" authorises the plan *and* the implementation, but house
  discipline requires the review arc to complete first.
- **The four architect questions (Q1–Q4) are answered in Phase 0** and their rulings are recorded in
  `decisions.md` before Phase 1's authoring starts, so Phase 1 never authors against an unsettled
  tool matrix.
- **No promote in this lane.** The staged release's promote command is recorded with its ceremony
  preconditions for the user; the live daemon is never touched.
