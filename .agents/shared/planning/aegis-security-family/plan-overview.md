# Plan Overview: Aegis Security Family

**Date:** 2026-11-04 (amended same day — architect deltas folded)
**Author:** planner[v2] via plan-creation worker
**Status:** Ready for Review — **architect gate CLEARED** (`SOUND-WITH-AMENDMENTS`, architecture-recommendation.md @ `f7204bd9d`; rulings Q1–Q4 CLOSED; amendments AM-1…AM-15 binding and applied). **Phase 0 status (B10, approver iter-002 — single-sourced in decisions §F, not restated here): council leg CLEARED; approver 001 REJECT → 002 REJECT → iteration 003 pending at fold time — see `decisions.md` §F.**
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
  no `memory.md`, no `growth.md`, no `tools.md`, no `skill.md` — see the baseline-count constraint
  below; AM-11 extends the forbidden set to all four legacy artifact names).
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
| **A real CVE-database tool category** | Q3 — would be a new category ⇒ out of scope; v1 ships `mcp` + `bash curl` on `aegis` only. **AM-3 correction:** `webfetch` is **operator-disabled on live** (`MCP_DISABLE_BUILT_IN_WEBFETCH=true`; no `webfetch` row live or dev). The verified live `mcp` inventory is `context7` + `plane` (owned by the dynamic `plane_` category) + user servers `zai-web-reader` / `zai-web-search` / `zai-zread`; dev = `context7` + `plane` only. Web-fetch/search is **deployment-conditional**; `bash curl` is the universal fallback. |
| **An agent-meta supervision knob** | Q4 — the per-agent `watchover` key is **inert for every agent except `watcher`** (D7, corrected by AM-5: `graph.py:10998` reads it only for the watcher's own evaluator config). **AM-5 correction:** the per-instance REST API is an **operator-only tool-call gate** (one watcher-LLM Allow/Deny verdict per tool-call batch), **not** a supervision timeout — unsuitable for campaign children. v1 supervision posture: **aegis is the primary supervisor** (proc_status/proc_logs polling + every `process_id` recorded + explicit teardown); the long-tool nudge is a secondary signal only and cannot see backgrounded `proc_run` campaigns. |
| **Promoting the release to live** | The user's 3-factor nonce ceremony owns the flip (E7). The promote command is **recorded, not executed**. |
| **The vkey first engagement run** | Requires a **live** family ⇒ post-ceremony. See §Post-lane. |
| **Docker; system packages** | House decision + commission constraint (E6). No Docker on this host; no `apt` without reporting first. |

---

## Phases

| Phase | Name | Objective | Tasks | Coupling | Gate (independently checkable) | Owner | Status |
|-------|------|-----------|-------|----------|-------------------------------|-------|--------|
| 0 | [Plan review + exemplar read + compliance checklist](phase0-plan.md) | Clear the plan-review gate and produce the guide-compliance checklist every later phase is audited against | 7 | independent (read-only) | Council + approver sign-off (the **architect leg is DONE** — SOUND-WITH-AMENDMENTS @ `f7204bd9d`, AM-1…AM-15 folded) **AND** the checklist exists with a named target artifact per guide rule **AND** Q1–Q4 recorded RESOLVED (their rulings closed by the architect) | planner | in progress — council leg CLEARED; approver 001 REJECT → 002 REJECT → iteration 003 pending (single source: decisions §F — B10) |
| 1 | [The `aegis` orchestrator](phase1-plan.md) | Author `aegis` — the orchestrator persona, the methodology skill (taxonomy + finding record), the dispatch/fan-in/re-verify doctrine | 9 | tight w/ P2 (shared legality language), tight w/ P3 (skill-body FENCES) | Guide checklist satisfied **for `aegis` alone**; T1–T4, T12, T14–T18, T24, T26, T27 green; strategy-skill frontmatter version == manifest version | dev (coder lane) | pending |
| 2 | [The three analysis children](phase2-plan.md) | Author `hacker`, `cracker`, `sweeper` — three leaf lanes with explicit white-hat legality in **each** persona | 10 | tight w/ P1, tight w/ P3 | T5–T7, T13–T18, T26, T27 green for all three; each child's own legality language present | dev (coder lane) | pending |
| 3 | [The three installer libraries](phase3-plan.md) | Author the three `.lib.sh` installer libraries — the charter skeleton, hardened with sha256 release-asset verification | 10 | tight w/ P2 (skill bodies) | T23 green (**AM-10 definition:** primary = the three `.md` FENCES bullet lists byte-identical, scoped to the bullet list only and including the C3 checksum bullet; secondary = each `.lib.sh` carries a FENCES header naming the tool-agnostic keywords — explicitly NOT byte-identity vs the `.md` block); each library has a checksum step, a readiness probe returning 0/1/2, and a mandatory reversal section | dev (coder lane) | pending |
| 4 | [Pin ceremony + tests + the leader one-liner](phase4-plan.md) | Land **one commit**: 4 agent trees + leader edit + regenerated baseline + the new test file **+ the C1(b) 16→17 pin fold in `tests/unit/test_maintenancer_agent.py`** | 9 | tight w/ P1–P3 (consumes their content) | `generate_prompt_artifact_baseline.py --check` ⇒ exit 0 **AND the baseline file exists with exactly 177 rows** (AM-11: the generator's `--check` exits 0 both when clean AND when the baseline file is ABSENT — `rc=0` alone proves nothing); `git show --stat HEAD` proves the one-commit composition; the re-stamp was the **last** `agents/**` content change | dev (coder lane) + giter (re-stamp) | pending |
| 5 | [Verification](phase5-plan.md) | Prove net-new reds = 0 and every commission expectation holds, on the committed branch | 9 | tight w/ P4 (verifies its output) | Gates **A–H** all green (see §Verification gates) | tester | pending |
| 6 | [Staging handoff](phase6-plan.md) | **GITER-OWNED release mechanics (Blocker 1+2)**: merge `--no-ff` → 4-file bump → pushed annotated tag → env-first stage (live-dir write only, journaled; tip-identity by construction) → staged release with full provenance and the promote command recorded (not run) | 12 | tight w/ P5 | G6.1a giter-set gate + 4-way sha match (live install dir), sidecars clean, **EMPTY** migration delta, agents tree re-derived, live fence untouched, promote command recorded, staging worktree + sidecars kept until ceremony | giter | pending |

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

1. **P1 ↔ P2 — the legality voice.** The **five guardrail families** (read-only-on-targets ·
   self-owned-sandbox/zero-external-targets · synthetic-sample-data-only · findings-private ·
   white-hat-ethos — **n5: the authoritative per-agent cardinal sets are `decisions.md` §B.3**,
   incl. the Blocker-5 merges and the hacker collapse) must read as
   *one doctrine* expressed at two altitudes: `aegis`'s policy cardinal + each child's own operational
   legality language. Divergence here is the commission's central risk ⇒ **one shared wording pass in
   P2 before either phase closes** (P2 task 8 re-reads P1's cardinals).
2. **P2 ↔ P3 — the skill body / library split.** Each installer `.md` states the FENCES and the
   procedure; each `.lib.sh` implements them. They must agree **bullet for bullet**; T23 pins the
   FENCES bullet list identical across all three `.md` bodies (primary) with the `.lib.sh` headers
   checked for the tool-agnostic keywords (secondary) — see `decisions.md` deviation **D-1** and
   amendment **AM-10** for the exact pin target and scope.
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
tests/test_maintenancer…::test_leader_team_members_length_16     (FOLDED 16→17 — C1(b))
```

**Baseline arithmetic (exact under the four-file prompt set):** the collector is depth-2 only
(`daemon/tools/prompt_artifacts.py:355-377`) and covers `soul/rule/workflow/tools_note` (+ the legacy
`tools.md`/`skill.md`/`memory.md`/`growth.md`) ⇒ **4 agents × 4 prompt artifacts = +16 rows ⇒ 161 → 177**.
`meta.json`, `skill-set.yaml` and `skills-template/*` contribute **zero** rows (B1) — and the `leader`
edit touches `meta.json`, so it also contributes zero. **Any of `memory.md`, `growth.md`, `tools.md` or
`skill.md` on a new agent would add a baseline row and move the number** (AM-11: the collector's
artifact set is eight names; the two legacy names have zero rows repo-wide but either would silently
move the 177 count) ⇒ the four new agents get exactly four prompt files each.
**AM-11 gate nuance:** the generator's `--check` exits **0 both when clean and when the baseline file
is ABSENT** (`generate_prompt_artifact_baseline.py:127-133`) ⇒ every baseline leg (P4/P5/stage) must
assert the **file exists** *and* has **exactly 177 rows** — `rc=0` alone proves nothing.

**Agents-tree arithmetic:** 338 (D6) + **28** new files = **366**; the `leader` edit modifies an existing
file. **Re-derive both numbers from the actual file list at P4 time** — the plan states the derivation
rule, not a frozen expectation.

**Ordering rule:** the re-stamp is the **LAST** content change under `agents/**` in the lane. Any prompt
edit after the re-stamp drifts the baseline and reddens the stage-time leg.

---

## Verification gates (P5 — each independently checkable)

| Gate | What it proves | How |
|---|---|---|
| **A** | No behaviour regression | Full suite, net-new deterministic reds **0** vs the true pre-fix base (same commands, same environment). **Expected-ledger note (C1(d), signature corrected per council-D1):** `tests/unit/test_maintenancer_agent.py::test_leader_team_members_length_16` is **green at base (the base roster is 16 and the pin asserts ==16)** and **green at HEAD only via the in-commit fold (C1(b))** — absent the fold it would be the single HEAD-only red; post-fold it appears in NEITHER FAILED list and the ledger records the fold (no flip signature appears); any OTHER HEAD-only red is a lane regression and blocks |
| **B** | `SEALED_SHA_BASELINES` untouched (35 entries) | `tests/test_chart_image_delivery_e2e.py::TestPhaseDDoesNotModifySealedArtifacts` green on the branch (the commission's explicit proof leg) |
| **C** | The plane read-holder census is unchanged | `tests/unit/tools/test_plane_agent_surface_census.py` green with `DISCLOSED_READ_HOLDERS` still `{"watcher"}` — **not** extended to the aegis family (the E3 fold removes the exposure instead of whitelisting it) |
| **D** | The report-integrity fold landed | `tests/unit/test_report_integrity_prompts.py` green — `aegis`'s scrutiny guidance is found by the dynamic check |
| **E** | Boot-scan clean on the new agent set | T28 registry scan finds `aegis`, `hacker`, `cracker`, `sweeper`; plus the tester's isolated boot-scan leg |
| **F** | No tool-category drift (the F-1 proof) | `.agents/shared/metrics/tool-categories.json` byte-unchanged in the lane diff; `tests/unit/tools/test_tool_category_artifact.py` green |
| **G** | The manifest delta is re-derived from the real tree | `find agents -type f \| wc -l` ⇒ 366 (or the re-derived value), and the staged payload's agents map matches |
| **H** | Scope + privacy (LANE-OWN diff — Blocker 1+2) | the **lane-own diff** (pre-merge) touches **only** `agents/**`, `tests/**`, the lane's committed planning set and the approver bookkeeping `.agents/approver/**` — the complement of {`agents/`, `tests/`, `.agents/approver/`} **equals exactly `.agents/shared/planning/aegis-security-family/`** (house doctrine commits planning docs + approver review-loop bookkeeping, B7; genuine creep still reds — W4); the merge/bump/tag/giter choreography is OUTSIDE this gate (P6 G6.1a asserts it: merge parents, bump = 4 files, tag pushed); no finding artifact, corpus, crash dump or secret is committed; `agents/worker/**` is byte-identical to the base |

---

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| **R1** | **The `bash` write path is unconstrained.** `filesystem` is one six-tool category with no read-only sub-category (A6); every lane holds `bash`, so `write_file`/`edit_file` denies are a *speed bump*, not a boundary. The family's "read-only on target codebases" guarantee is **doctrinal**. | **High** | High | (a) The limitation is stated **plainly** in the plan, in `aegis`'s persona, and in the report template — never implied containment. (b) The mechanical sandbox-ownership prefix check (DP3) gates every *active* technique. (c) The write-deny removes the easiest accidental mutation. (d) A persona cardinal names the legality line. (e) `decisions.md` records it as trade-off T1. |
| **R2** | **A malformed `tools` block is fail-OPEN** — a parse failure sets `tools_filter = None` = *all tools allowed* (A9). On a security agent that is a privilege escalation, not a lint error. | **High** | Medium | The lane test asserts every new meta's `tools` block **round-trips through the registry's parser** (added to T1–T3) and every meta is valid JSON with a `tools` key present. |
| **R3** | **The E3/E4 census traps.** An empty/absent `tools.allow` inherits the default universe, which includes the plane read tools ⇒ the frozen read-holder census goes RED; any `mcp_full_access` including plane goes RED (B3). | Medium | Medium | **T6** (explicit non-empty allow on every new meta) and **T11** (no `mcp_full_access`) are the folds, written *before* the family lands. `DISCLOSED_READ_HOLDERS` stays `{"watcher"}` — the exposure is removed, not whitelisted. |
| **R4** | **Baseline drift.** The baseline pins content hashes; a prompt edit after the re-stamp (or a fifth prompt file) silently moves the count and reddens the stage leg (B1/B2). | Medium | Medium | The re-stamp is the **last** `agents/**` content change (P4 ordering rule); `--check` runs **before** the commit and again in P5 Gate A; the row count is asserted **exactly 177** in the lane test, so a move is caught pre-stage. |
| **R5** | **Install-skeleton drift.** Three copies of the charter skeleton exist by design (DP2). The FENCES block — the one part that must not drift — is at risk. | Medium | Medium | **T23** pins the FENCES **bullet lists** identical across the three installer skill bodies (**AM-10 scope: bullet list only — C3 checksum bullet included, per-tool content excluded**); the pin test fails the lane on any bullet-level divergence. |
| **R6** | **Scope creep into `daemon/`** — a CVE-lookup category, a supervision knob, a per-agent allow-list change, or a `watchover` field. Each would trip the 3-step registration seam (F-1 trap) or the inert-key false-confidence trap (D7). | Medium | Medium | Q3/Q4 are architect decisions with **explicitly out-of-scope fallbacks**; P5 asserts the lane diff contains **only** `agents/**` and `tests/**`; P6's EMPTY migration delta is the independent proof. |
| **R7** | **Prompt-prose violations across 16 authored files.** System internals, `.md` path tokens, >7 cardinals, a missing tone block, a duplicated canonical home, an out-of-team fallback (guide §1–§10; C1). | Medium | High | The **guide-compliance checklist is an explicit gate in every authoring phase** (P0 produces it; P1/P2/P3 audit against it); T14–T19 mechanise the mechanically-checkable half (§1, §3, §5, §8, §10). |
| **R8** | **Findings privacy.** Off-repo storage keeps findings out of git, but the artifacts are then not PR-reviewable and need filesystem access; in-repo storage would land them in git history and risk a push to origin (DP4). **AM-4:** the off-repo root is **reachable, not confined** — absolute paths are trusted by design and `bash` is unconstrained — and it **cannot be globbed** (`glob_files`/`grep_files` refuse absolute roots outside the workdir/temp), so discovery is by recorded path, never by walk. | Medium | Medium | **Q2 is RESOLVED (architect ruling: off-repo)**; under the ratified option the **severity summary travels inside the hand-off message** to `leader`, so the decision-maker is never blocked by file access, and the honest sentence "the privacy of the findings is discipline, not a boundary" is stated wherever the storage choice is. |

> **R9 — Finalize-guard note** *(promoted from a free-floating note to the risks ledger — A-b, plan
> council 2026-11-04; the `(R9)` pointer in §Dependencies resolves here)*. Branch/tip drift is handled by the house **finalize
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
| **S1** | Baseline / pin green | `python3 scripts/protection/generate_prompt_artifact_baseline.py --check` (read-only, exit 0 clean / 1 drift) **+ an explicit file-exists and row-count assertion** | the file **exists**, exit **0**, exactly **177** rows, zero drift (**AM-11:** `--check` exits 0 even when the file is absent ⇒ rc=0 alone proves nothing) |
| **S2** | The pin ceremony is one commit | `git show --stat HEAD` on the lane commit | exactly the sanctioned set: 28 new agent files + `agents/leader/meta.json` (1 line) + `agents/.prompt-artifacts.sha256` (regenerated) + the new lane test file + **the 16→17 leader-length pin fold in `tests/unit/test_maintenancer_agent.py`** (C1(b)/(c) — the fifth sanctioned element) — **and nothing outside this enumerated set** |
| **S3** | Boot-scan clean on the new agent set | T28 registry-scan probe (venv-bound) + the tester's isolated boot-scan leg | all four ids resolve in the registry; zero new entries in the plane read-holder census |
| **S4** | Tool-category allow-list pins hold | T1–T11 (allow set, deny set, `deny_spawn`, roster, leaf teams, non-empty allow, write fence, no privileged category, no `job`, no `instance_read`/`instance_ops`, no `mcp_full_access`) | **11/11 green** |
| **S5** | The report-integrity fold landed | T12 + the dynamic rot-mitigation test | `[REPORT SANITY:`, `interim, not completion` and `send_message` all present in `aegis`'s surface (**AM-9:** T12 asserts the **union** of `{rule.md, workflow.md, soul.md}` ∪ `{skills-template/*.md}` — the snippets are placed in `aegis`'s `rule.md` as the canonical home; `aegis-strategy.md` carries **no** report-delivery boilerplate); the dynamic test green |
| **S6** | `SEALED_SHA_BASELINES` untouched + proof | `TestPhaseDDoesNotModifySealedArtifacts` | green; the 35 entries byte-unchanged |
| **S7** | `worker` is untouched | T25 (byte-identical to base) | `agents/worker/**` byte-identical to `1f6898ae2` |
| **S8** | No `daemon/` change | the **lane-own** diff's path list + P6's migration delta | the lane-own diff contains only `agents/**`, `tests/**`, the committed planning set and the approver bookkeeping (complement == planning set + `.agents/approver/**` — W4/B7; giter set outside the gate — Blocker 1+2); migration delta **EMPTY** |
| **S9** | Guide compliance, all four agents | the Phase-0 checklist audited per phase + T14–T19 | every checklist row satisfied per authored file; T14–T19 green ×4 |
| **S10** | Install libraries are safe and complete | T23 (AM-10 definition) + per-library inspection | the three **`.md` FENCES bullet lists are byte-identical** (the shared block includes the C3 checksum bullet and excludes per-tool content; **n3: byte-identical MODULO BLANK LINES — the stated normalization**); each `.lib.sh` carries a FENCES header mentioning the tool-agnostic keywords (secondary, **not** byte-identity); each has a sha256 release-asset verification step, a readiness probe returning **0 warm / 1 cold / 2 in-progress**, a release lock whose release runs on failure, an attempt cap of 2, and a mandatory reversal section |
| **S11** | The methodology is single-homed | T24 + guide §2 | the 5 severity levels and **every named finding-record field** live **only** in `aegis-strategy`; the four prompt files link by section name and do not restate them |
| **S12** | Seeded-skill discipline | T21 + T22 | exactly **one** `skill-set.yaml` in the family (aegis's, one entry); the three installer skills appear in **no** manifest (file-invoked mode ⇒ out of the global skill bank); **AM-7:** `aegis-strategy`'s frontmatter declares **no `include:`** (the include resolver's cross-agent by-name fallback would inline another agent's bank row) — T21 pins `include` ∉ frontmatter |
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
| [`technical-analysis.md`](./technical-analysis.md) | The design resolution — **DP1–DP8**, the trade-off table, the scaling analysis, the technical-debt list, and the four architect questions **Q1–Q4**. This plan consumes it verbatim; it does not re-derive it. **Note (2026-11-04):** the architect's review superseded several of its claims — see the dated amendment note at the top of that file and `decisions.md` §C2. |
| [`plan-overview.md`](./plan-overview.md) | This file — the synthesis, the phase table, the coupling map, the risks, the success criteria. |
| [`phase0-plan.md`](./phase0-plan.md) | Plan review (architect/council/approver) + exemplar read + the guide-compliance checklist + the Q1–Q4 rulings. |
| [`phase1-plan.md`](./phase1-plan.md) | The `aegis` orchestrator — 7 files. |
| [`phase2-plan.md`](./phase2-plan.md) | `hacker`, `cracker`, `sweeper` — 18 files land in P2; the three `.lib.sh` complete the children's 21 in P3. |
| [`phase3-plan.md`](./phase3-plan.md) | The three installer `.lib.sh` libraries. |
| [`phase4-plan.md`](./phase4-plan.md) | The one-commit pin ceremony + the T1–T28 test file + the leader one-liner. |
| [`phase5-plan.md`](./phase5-plan.md) | Verification — gates A–H. |
| [`phase6-plan.md`](./phase6-plan.md) | Staging handoff — version derivation, stage, provenance, the recorded (not executed) promote command. |
| [`decisions.md`](./decisions.md) | The decision record — DP1–DP8 summarised, the leader one-liner, Q1–Q4 **RESOLVED** (architect rulings + consequences), the flagged deviations from `technical-analysis.md`, and §C2 — the architect amendments AM-1…AM-15 applied to this plan set. |

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
   appears ⇒ **Q1**. **[SUPERSEDED by AM-1]** the architect ruled the `chart` grant DOWN for v1 — it
   widens three separate surfaces (the tool; the implied `charter` spawn right; the transitive charter
   child's write surface) — so `aegis` does **not** hold `chart`; the report is markdown-native (mermaid
   text blocks). See §Open Questions → RESOLVED.
8. **The per-agent `watchover` key is inert** (D7) — `AgentMetadata` has no such field, the model is
   `extra="ignore"`, and no daemon code reads one ⇒ **Q4**; copying the sketcher block would create a
   false-confidence knob. **AM-5 precision:** the key is inert for **every agent except `watcher`**
   (`graph.py:10998` reads it for the watcher's own evaluator config), and the per-instance watchover
   API is an **operator-only tool-call gate, not a supervision timeout** — the family's v1 supervisor
   is **aegis itself** (AM-5 posture).
9. **`bash` string commands run through `/bin/sh`** (D10) — "loops go in script files" is a code fact,
   not a style preference ⇒ every installer library and every scanner script is POSIX-sh.
10. **The skill bank is global** (C2/C6) — anything seeded via `skill-set.yaml` becomes searchable by
    `worker` and every dynamic-skill agent ⇒ the three installer skills are **file-invoked** (the
    charter precedent) so their procedure text never enters the shared bank, and ~~the "worker never
    runs offensive tools" boundary is enforceable rather than merely prose~~ **[QUALIFIED — W7, plan
    council 2026-11-04; see the AM-7 restatement below]** the boundary is enforced against the SEARCH
    INDEX only: `worker` holds `bash` + `filesystem` and CAN read the installer bodies on disk in the
    release payload — the enforceable claim is scoped to search reachability, not filesystem reads
    (the "worker never runs offensive tools" rule remains doctrine for the disk class, disclosed per
    AM-7).
    **AM-7 restatement:** the blanket "bank is global ⇒ searchable by worker" is overstated — an
    ownership filter drops another agent's bank-cloned skills (`skill_search_service.py:718-822`).
    The TRUE invariant is **manifest-gating**: seeding enumerates only `skill-set.yaml` entries (no
    directory scan of `skills-template/` exists anywhere), so un-manifested skills are never banked ⇒
    unreachable by every search path; the project-skill loader indexes only
    `.agents/<ns>/skills/<name>/skill.json` ⇒ double gate. The boundary is the SEARCH INDEX, not the
    filesystem — the installer `.md` bodies remain readable on disk (disclosed).
11. **A `tools` parse failure is fail-open** (A9) — a malformed `tools` block on a security agent is a
    privilege escalation ⇒ the lane asserts the round-trip (R2).
12. **The census test freezes the plane read-holder set at `{"watcher"}`** (B3) — a new agent with an
    empty allow goes red ⇒ the **E3 fold** (explicit non-empty allow) and the **E4 fold** (no
    `mcp_full_access`) are written *before* the family lands, not after.
13. **The report-integrity check is dynamic** (B4) — any agent with a non-empty team must carry the
    `[REPORT SANITY:` / `interim, not completion` / `send_message` guidance ⇒ **aegis** carries it;
    the three children are exempt (empty teams).

---

## Architect Questions — RESOLVED (Phase 0, architect leg)

**The four questions are no longer open.** The architect's Phase-0 review
(`architecture-recommendation.md` @ `f7204bd9d`) closed all four with binding rulings and issued
amendments AM-1…AM-15. Each ruling + its plan consequence:

| # | Question | **RULING (architect, Q-ref)** | Plan consequence (folded in this commit) |
|---|----------|-------------------------------|------------------------------------------|
| **Q1** | May `aegis` hold `"chart"` (which also implies the `charter` spawn member)? | **NO — drop `chart` from v1.** The grant widens three separate surfaces (the tool; the implied `charter` spawn right; the transitive charter child's write surface) — the leader's side-effect-free test fails on all three. | **AM-1:** aegis allow 17→16 pre-AM-2; `deny_spawn` stays `["knowledge"]`; the "implied charter member" deviation **disappears**; `aegis-strategy` gains a **mermaid text-block** convention (attack-path/dependency diagrams as fenced mermaid); the commission's "report charts" item is formally reduced to markdown-native diagrams (disclosed); v2 re-entry path recorded (`allow "chart"` + `deny_spawn ["chart"]`, `registry.py:96-105` — tool functional, no spawn right, transitive charter child per call still disclosed). |
| **Q2** | Where do the findings live? | **OFF-REPO** — `$HOME/.local/state/aegis/engagements/<engagement-id>/`. | **AM-4:** storage is **reachable, not confined** (absolute paths trusted by design; `bash` unconstrained) and **cannot be globbed** (walk tools refuse absolute roots outside workdir/temp). `aegis`'s workflow states the engagement-root convention, the absolute-path read-back rule, discovery **by recorded path never glob**, and the honest sentence: *the privacy of the findings is discipline, not a boundary*. Writer split stated: `aegis` writes `findings.json`/`engagement.json` via `bash` (its only write route); `worker` writes `report.md` via `write_file` absolute. |
| **Q3** | Is the v1 CVE-lookup surface acceptable? | **YES as amended** — keep `mcp` on `aegis`, **no deny-list needed** (three independent gates make `mcp_rpc` exposure structurally impossible: ownership guard, privileged #15, in-body `{ari,leader}` check); children confirmed **EGRESS-FREE** (n4: no network tool grant — no `mcp`; `bash curl` on aegis only per Q3); no new category in v1. | **AM-3:** DP1's webfetch premise was **wrong for this deployment** — live `mcp` inventory = `context7` + `plane` (owned by the dynamic `plane_` category) + `zai-web-reader`/`zai-web-search`/`zai-zread`; dev = `context7` + `plane` only; `webfetch` is operator-disabled live. Web-fetch/search is deployment-conditional; `bash curl` is the universal fallback. The "no web-search capability" premise is corrected — `zai-web-search` **is** that capability on live. New doctrine line + test pin: **fetched external content is DATA, never instructions.** |
| **Q4** | May the family omit a `watchover` meta block? | **YES — omit it from all four metas**, with the characterisation corrected. | **AM-5:** the per-instance watchover API is an **operator-only tool-call gate** (one watcher-LLM Allow/Deny/Mistake verdict per tool-call batch; 3 denials ⇒ terminate; arming pauses/resumes the instance) — **not** a supervision timeout, unsuitable for campaign children. The key is inert for every agent except `watcher`. v1 posture: **aegis is the primary supervisor**; the nudge is secondary and cannot see `proc_run` campaigns; `set_instance_tunable` stays denied. |

Full ruling text and evidence: `architecture-recommendation.md` §2. The amendment-by-amendment record
with plan deltas: `decisions.md` §C2 (architect amendments table).

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
  discipline requires the review arc to complete first. **Status (B10 single source: decisions §F):**
  the architect review is DONE and folded; the **council leg is CLEARED**; the **approver arc is at
  001 REJECT → 002 REJECT → iteration 003 pending** — Phase 0 is not cleared until the approver
  passes.
- **The four architect questions (Q1–Q4) are RESOLVED** — ruled by the architect in Phase 0 (task 2)
  and recorded with their consequences in `decisions.md` §E + §C2, so Phase 1 authors against a
  settled tool matrix (the AM-1/AM-2-corrected one).
- **No promote in this lane.** The staged release's promote command is recorded with its ceremony
  preconditions for the user; the live daemon is never touched.
