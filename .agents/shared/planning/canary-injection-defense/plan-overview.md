# Plan Overview: Canary Injection Defense

**Date:** 2026-11-05
**Author:** planner[v2] via plan-creation worker
**Status:** Plan set authored — **PENDING the architect gate on D1–D6** (framed, not decided, in
`decisions.md` — owned by the parallel sibling worker). Every phase below carries an explicit
**default assumption**, so **no phase task hard-depends on an unanswered D**; a ruling that differs
from a default folds as a delta, it does not invalidate a phase.
**Commission:** `canary-injection-defense` (user-ratified design, Discord 2026-11-05)
**Worktree / branch:** `/home/nea/ensemble-src-wt-canary-injection-defense` @
`feature/canary-injection-defense` (base `97b76e20e1d640c69b720149547e93565ab22b4c` =
`origin/latest` at lane start)

**Sibling-owned files (parallel workers — this plan references, never duplicates):**
`decisions.md` (the decision record + the D1–D6 rulings), `technical-analysis.md` (design
resolution), `requirements.md` (the requirement set this plan satisfies).

---

## Objective

Deliver **four interlocking builds** that turn "we detect prompt injection" into "our agents cannot be
silently hijacked by untrusted content":

1. an **`injection-screen` skill** — org-wide consumable, machine-checkable fenced output;
2. a **fencing doctrine** — additive persona rules (untrusted-content rule + action-distance rule);
3. a **`canary` agent** — an adversarial-AI red team that attacks *our own* intake surfaces against
   self-owned sandboxed targets and evolves the detection rule set from what it learns;
4. the **house ceremonies** — prompt-writing-guide conformance, the one-commit pin ceremony, the
   extended T-pack, the boot-scan, and a staged release.

Completion marker: the family is **staged** (a release artifact with full provenance) and every gate
in §Success Criteria is green — the flip to live remains the user's 3-factor nonce ceremony.

**The doctrine in one line (user-ratified):** *detection alone is insufficient — safety = detection
skill + fencing doctrine + blast-radius control + red-teaming.*

---

## Scope

### In Scope

- **One new agent tree — `agents/canary/`** — `meta.json` + `soul.md` + `rule.md` + `workflow.md` +
  `tools_note.md` (**5 files**; no `memory.md`/`growth.md`/`tools.md`/`skill.md` — see the
  baseline-count constraint below) **+ `skill-set.yaml` + `skills-template/injection-screen.md`** when
  D2's default (banked per-agent copy) survives.
- **The `injection-screen` skill** — one canonical body (canary-owned) + **mirror copies** for the
  screened roster (D2's default = the **standing readers**: `canary`, `aegis`, `worker`, `sweeper`),
  each with a `skill-set.yaml` entry. **`hacker` and `cracker` get the doctrine layer only — no skill
  copy** (a disclosed coverage gap). A **seed payload corpus** as test fixtures (synthetic only).
- **Additive persona edits to four existing personas** — `aegis` (one guideline section), `hacker`,
  `cracker`, `sweeper` (one guideline section each), `worker` (legacy `## Must` shape). **Doctrine
  only** — no other persona content is touched.
- **Two `aegis` edits** — `agents/aegis/meta.json` (one line: `canary` joins the team) and
  `agents/aegis/workflow.md` (one appended "when to engage canary" section).
- **One extended test file** — `tests/unit/agents/test_aegis_security_family.py` grows **T31–T37**
  (D5's default = Option A: the lane already edits that file for the re-derivation set, so one corpus
  of pins avoids two matrices drifting apart). If the architect prefers a split by concern, the
  skill-only legs may move to `tests/unit/agents/test_injection_screen.py`.
- **Two folds in one existing test file** — `tests/unit/agents/test_aegis_security_family.py`
  (the T4 team-set pin 4→5; the T29 legality matrix 20→25 cells).
- **One regenerated prompt-artifact baseline** — `agents/.prompt-artifacts.sha256`, **177 → 181 rows**,
  re-stamped in the **same commit** as the agent files.
- **A staged release** with full provenance (P6). **NO promote.**

### Out of Scope

| Item | Reason |
|---|---|
| **Any `daemon/` change** | Commission constraint. A new *tool category* would trip the 3-step registration seam + `tool-categories.json` regeneration — the F-1 trap class. **A skill is not a category**: the injection-screen ships as a *skill*, so the category artifact stays byte-unchanged (Gate F). |
| **The parked `.agents/*/skills/` project-file loader** | Commission constraint — explicitly parked. The plan is built on the **agents `skills-template/` + `skill-set.yaml`** pattern only. |
| **Persona edits to `leader`, `developer`, `reviewer` or any other persona** | Commission constraint. Adoption by other personas is a **recommendation in the lane report only** (§Adoption by other personas). |
| **Any change to `agents/worker/**` beyond the doctrine block + the skill mirror** | The doctrine is additive; `worker`'s legacy `## Must` shape is respected (see P2 task 3). |
| **A real CVE / threat-intel feed** | Out of scope — this lane is about *our own* intake surfaces, not external advisories. |
| **Any live/external target** | Commission cardinal — canary's payloads fire **only** against self-owned sandboxed instances; never a live instance, never an external system, never real user data. |
| **Promoting the release to live** | The user's 3-factor nonce ceremony owns the flip (E7). The promote command is **recorded, not executed**. |
| **Real credentials in the seed corpus** | Gate H leg — the corpus is synthetic; a secret-shaped string in the corpus is a lane failure, not a finding. |
| **Docker; system packages** | House decision + commission constraint. |

---

## Phases

| Phase | Name | Objective | Tasks | Coupling | Gate (independently checkable) | Owner | Status |
|-------|------|-----------|-------|----------|-------------------------------|-------|--------|
| 0 | [Plan review + exemplar read + compliance checklist](phase0-plan.md) | Clear the plan-review gate, produce the guide-compliance checklist every later phase is audited against, and **record the D1–D6 default-assumption table** | 7 | independent (read-only) | Architect sign-off on `decisions.md` **OR** the D1–D6 defaults recorded as adopted **AND** the checklist exists with a named target artifact per guide rule | planner | pending |
| 1 | [The `injection-screen` skill](phase1-plan.md) | Author the skill **contract-first** — fence grammar + provenance header + heuristic rule-ID registry + the seed corpus — then the body, the mirrors and the manifest entries | 10 | tight w/ P2 (the fence grammar is what the doctrine prose references), tight w/ P4 (the mirror + manifest pins) | The fence contract is **machine-checkable and deterministic**; the corpus covers every rule ID with a positive fixture **and** a benign control; the mirror set is byte-identical | dev (coder lane) | pending |
| 2 | [The fencing doctrine](phase2-plan.md) | Land the untrusted-content rule + the action-distance rule as **additive** persona blocks across 4 existing personas + draft canary's from-birth form | 10 | tight w/ P1 (fence grammar wording), tight w/ P3 (canary's from-birth block lands with P3's files) | Every persona carries both rules; **no cardinal count moves** (aegis is at 7/7 ⇒ guideline `### (g)`); the prose passes the guide's token classes | dev (coder lane) | pending |
| 3 | [The `canary` agent + the aegis edits](phase3-plan.md) | Author the 5-file canary persona set + its meta + the canonical skill copy + the aegis roster one-liner + the aegis when-to-engage section | 10 | tight w/ P1 (canonical skill home), tight w/ P2 (canary's from-birth doctrine) | The guide checklist is satisfied for every authored file; the registry parses the new meta; the campaign store pattern matches the family's off-repo privacy ruling | dev (coder lane) | pending |
| 4 | [The one-commit pin ceremony + the T-pack](phase4-plan.md) | Land **one commit**: the canary tree + the mirrors + the 4 persona doctrine edits + the 2 aegis edits + the regenerated baseline + the corpus fixtures **+ the shared-file re-derivation set (T4/T5/T6-T8/T21/T22/T25/T29) + the new T31–T37 legs** | 10 | tight w/ P1–P3 (consumes their content) | `generate_prompt_artifact_baseline.py --check` ⇒ exit 0 **AND the baseline file exists with exactly 181 rows** (the `--check` exit code alone proves nothing); `git show --stat HEAD` proves the one-commit composition; the re-stamp is the **last** `agents/**` content change | dev (coder lane) + giter (re-stamp) | pending |
| 5 | [Verification](phase5-plan.md) | Prove net-new reds = 0 and every commission expectation holds, on the committed branch | 9 | tight w/ P4 (verifies its output) | Gates **A–H** all green (see §Verification gates) | tester | pending |
| 6 | [Staging handoff](phase6-plan.md) | **GITER-OWNED release mechanics**: slot re-derivation → merge `--no-ff` → 4-file bump → pushed annotated tag → env-first stage → staged release with full provenance and the promote command recorded (not run) | 12 | tight w/ P5 | 4-way sha match, sidecars clean, **migration-delta census with the MERGED-delta reasoning**, agents tree re-derived, live fence untouched, promote command recorded | giter | pending |

---

## Architect-question dependency map (D1–D6)

The architect's questions are **framed, not decided**, in `decisions.md` (sibling-owned). This plan is
built so that **no phase task hard-depends on an answer**. Each phase states which D it consumes and
the **default assumption** it proceeds under if the architect has not ruled.

| D | Question | Consumed by | Default assumption this plan proceeds under | If the architect rules otherwise |
|---|----------|-------------|-------------------------------------------|-------------------------------|
| **D1** | Spawn topology — is `canary` a leaf, or does it hold a scoped spawn right? | **P3** (meta team + tools shape), **P4** (report-integrity leg + the T5 set extension), **P5** (Gate D) | **Option A (the recommended option)** — the **lean compose-and-hand** shape: canary holds **zero instance tools**, `team_members: []`; it **composes** payloads and **hands them to `aegis`**, and **aegis** spawns the targets, fires the payloads and returns observation bundles **staged to files** (paths, not inline text). The single-spawner posture is preserved. (`decisions.md` C.1) | Option B (a doctrinal spawn right) or Option C (a decoy persona) ⇒ the team gate has **no project axis**, so "project-only" would be **doctrinal, not mechanical** — the plan must carry the unbacked-claim disclosure; **Option C additionally adds a new persona ⇒ +4 baseline rows ⇒ 181 → 185** and its own pin set. |
| **D2** | Skill mechanism + distribution + evolution | **P1** (seeding + mirror set), **P4** (the manifest/mirror pins) | **Option B (the recommended option)** — seed the **standing readers** only: `canary` (canonical), `aegis`, `worker`, `sweeper` ⇒ **4 bodies + 4 manifest entries** (+2 new manifest files: `canary`, `sweeper`); **`hacker` and `cracker` get the doctrine layer only** — a **disclosed coverage gap**, stated in the plan and the report, never hidden. `auto_load: **true**` for canary (its rule set must be reachable by a persona with no skill tools) and `auto_load: **false**` + a **workflow-mandated load** for the others, with **push delivery** where the recipient is episodic. The "stated once" claim stays **honest**: the canonical body is canary's; the seeded copies are **mirrors** kept in sync by a **version bump** and a **cross-agent identity pin** (byte-identity modulo blank lines). Evolution is **git-only** — a file skill never takes `skill_fix`. (`decisions.md` C.2) | Option A (the whole family, 6 bodies) ⇒ the largest mirror surface and two more manifests; Option C (minimal seed + push delivery) ⇒ screening becomes **dispatch-dependent** — a silent gap when a dispatch forgets the tag, which is the failure the lane exists to close. |
| **D3** | Doctrine wording, the **N**-semantics, and the canary carve-out | **P2** (all persona blocks), **P3** (canary's from-birth block) | **N = 1, measured in assistant turns** ("this turn or the next") — the block must name (i) the **metric unit**, (ii) an **explicit bound**, and (iii) the **justification requirement** (name the provenance chain). The anchor literals are **metric-neutral**, so the pins stay valid whichever option the architect picks (`requirements.md` DOC-03). The **canary carve-out**: canary's *job* is hostile content — it composes and reads payloads **as fenced data**, **never executes a payload (not even one it authored)**, **never pastes payload text unfenced** into its own reasoning, reports or logs, and **re-fences** any text it quotes back out of a target's transcript (`requirements.md` CAG-12). | A different N re-writes one clause in five persona blocks (P2 is a text pass ⇒ cheap fold). A widened or narrowed carve-out touches canary's rule.md only. |
| **D4** | Payload corpus + rule-set home | **P1** (corpus + rule registry), **P3** (rule-set ownership) | **Corpus**: a small **synthetic** seed corpus committed as test fixtures under `tests/`, one positive fixture per rule ID + a benign control per family. **Rule-set home**: canary's canonical `skills-template/injection-screen.md`; canary **evolves** it via git PRs (never `skill_fix` — file skills are git-only). | A different corpus home moves files (P1/P4 delta); a different rule home changes the canonical-copy pin (P1/P4 delta). |
| **D5** | Test-matrix shape | **P4** (the T-pack), **P5** (Gate A ledger) | **Option A (the recommended option)** — the **family file grows** (`test_aegis_security_family.py` continues its T-numbers with **T31–T37**); the lane already has to edit that file for the re-derivation set (T5/T6/T7/T8/T21/T22/T25/T29), so one corpus of pins avoids two matrices drifting apart. The **cell counts** are binding: **25** family cells (T29) + **24** doctrine cells (T31) + canary's 2 carve-out anchors; `worker` stays **outside** the 25-cell matrix (its legacy prose cannot carry a `^\d+\.\s\*\*` anchor without a re-shape the commission forbids) and its coverage is a separate small pin set. | Option B (sibling file for canary) buys file-level tidiness only — the matrix must be split (two completeness constants) or imported (an import path the family file does not have); Option C (a shared constants module) is **a refactor inside a feature lane** and should be refused unless the architect wants it. |
| **D6** | Intake-channel inventory + sandbox definition | **P3** (campaign map), **P5** (Gate H) | **Option A (the recommended option)** — the campaign covers the **5 legs** a **leaf-class** target can actually receive (channels 1, 2, 3, 5 fired; channel 6 = the existing `[SYSTEM NOTE]` frame as the comparison baseline), and channels 4/7/8/9/11 are **disclosed as not covered in v1** with their reasons. The target persona is **leaf-class**; the campaign procedure carries a **five-item mechanical pre-flight** (dev identity asserted, dev DB asserted, a scratch `DATA_DIR` with a non-poisoned pin, self-owned parentage, synthetic payload only). **Stated plainly what the sandbox is not**: prompts + a campaign record + a hand-off — the guards are logical, there is **no OS/container boundary** (`bash` is unconfined). (`decisions.md` C.6) | Option B (a privileged-target leg) widens the blast-radius statement (a compromised `mcp`-class target on the dev daemon can reach the live daemon on the same host); Option C (a mock MCP server) is new infrastructure the lane does not own and measures **shape**, not server behaviour — which the finding must state, or it over-claims. |

**Rule for the lane:** a D-answer that arrives **after** a phase has shipped folds as a **delta** to
that phase's phase file (Mode-2 style, additive) — it never rewrites a shipped phase.

---

## Coupling Map

| | P0 | P1 | P2 | P3 | P4 | P5 | P6 |
|---|---|---|---|---|---|---|---|
| **P0** | — | tight (the checklist is the gate) | tight (the checklist is the gate) | tight (the checklist is the gate) | loose | independent | independent |
| **P1** | tight | — | **tight** (the doctrine prose quotes the fence grammar) | **tight** (canary owns the canonical copy) | tight (P1's files are the commit payload) | loose | independent |
| **P2** | tight | **tight** | — | **tight** (canary's from-birth block lands with P3's files) | tight (payload) | loose | independent |
| **P3** | tight | tight | **tight** | — | tight (payload) | loose | independent |
| **P4** | loose | tight | tight | tight | — | **tight** (P5 verifies P4's commit) | loose |
| **P5** | independent | loose | loose | loose | **tight** | — | **tight** (P6 stages what P5 verified) |
| **P6** | independent | independent | independent | independent | loose | **tight** | — |

**Tight couplings and their shared contracts:**

1. **P1 ↔ P2 — the fence grammar is a shared contract.** The skill's fenced output format (the
   delimiters and the provenance header fields) is quoted **by name** in the doctrine prose. If the
   grammar moves, the doctrine prose moves with it ⇒ **one wording pass in P2 before either phase
   closes** (P2 task 7 re-reads P1's grammar block).
2. **P2 ↔ P3 — canary's doctrine is born with canary.** Canary is a **new** agent, so its untrusted-
   content rule is part of its first draft, not a later edit ⇒ P2 authors the wording, P3 lands it.
   This is why P2 and P3 cannot be reordered.
3. **P1 ↔ P3 — the canonical copy.** `canary`'s `skills-template/injection-screen.md` is the
   **canonical** body; every other copy is a mirror of it. The canonical-home rule is stated in
   canary's own `tools_note.md` (the single-home doctrine, guide §2) and pinned byte-identical.
4. **P4 → P5 → P6 — the artifact chain.** P4 produces the commit, P5 proves it, P6 ships it. P5 must
   not start before P4's exit criterion; P6 must not start before P5's gates A–H are green.

**Independent couplings:** P0's exemplar read touches the repo but changes nothing; P6's staging
machinery is orthogonal to the lane's content.

---

## The one-commit pin ceremony (binding, P4)

```
agents/canary/{meta.json, soul.md, rule.md, workflow.md, tools_note.md, skill-set.yaml,
               skills-template/injection-screen.md}                                  (+7 NEW)
agents/aegis/skills-template/injection-screen.md                                    (+1 NEW)
agents/aegis/skill-set.yaml                                        (MODIFIED — one entry added)
agents/worker/skills-template/injection-screen.md                                   (+1 NEW)
agents/worker/skill-set.yaml                                        (MODIFIED — one entry added)
agents/sweeper/skills-template/injection-screen.md                                  (+1 NEW)
agents/sweeper/skill-set.yaml                                        (MODIFIED — one entry added)
agents/aegis/meta.json                                                            (MODIFIED — one line)
agents/aegis/rule.md                                          (MODIFIED — guideline (g) added)
agents/aegis/workflow.md                                      (MODIFIED — one section appended)
agents/hacker/rule.md                                         (MODIFIED — guideline (f) added)
agents/cracker/rule.md                                        (MODIFIED — guideline (f) added)
agents/sweeper/rule.md                                        (MODIFIED — guideline (f) added)
agents/worker/rule.md                                         (MODIFIED — doctrine block added)
agents/.prompt-artifacts.sha256                                   (REGENERATED 177 → 181)
tests/unit/agents/test_aegis_security_family.py                   (MODIFIED — the re-derivation set + T31–T37)
tests/fixtures/injection_corpus/                                  (NEW — synthetic seed corpus)
```

**Skill roster (D2's default = Option B, the recommended option):** the **standing readers** are
seeded — `canary` (canonical), `aegis`, `worker`, `sweeper` ⇒ **4 bodies + 4 manifest entries**
(+2 manifest files where none existed: `canary`, `sweeper`; `aegis` and `worker` already have one).
**`hacker` and `cracker` get the doctrine layer only — no skill copy.** That exclusion is a
**disclosed coverage gap**, stated in the plan and in the lane report, never hidden.

**Baseline arithmetic (exact under the four-file prompt set):** the collector is depth-2 only and
covers `soul/rule/workflow/tools_note` (+ the legacy `tools.md`/`skill.md`/`memory.md`/`growth.md`)
⇒ **canary adds exactly 4 rows ⇒ 177 → 181**. `meta.json`, `skill-set.yaml`, `skills-template/*`
and the corpus fixtures contribute **zero** rows, and the **10** modified agent files
(6 persona `rule.md`s + `aegis/meta.json` + `aegis/workflow.md` + 2 `skill-set.yaml`s) change
**content hashes, not row counts**. **Any** of `memory.md`, `growth.md`, `tools.md` or `skill.md`
on canary would add a row and move the number ⇒ canary gets exactly four prompt files.
**Conditional on D1:** if the architect takes D1's Option C (a decoy persona), that persona adds
**+4 rows ⇒ 181 → 185** and the arithmetic is re-derived — the 181 figure holds **only** under
D1 = A (the recommended option).

> **Baseline-check nuance (carried forward):** the generator's `--check` exits **0 both when clean
> and when the baseline file is ABSENT** ⇒ every baseline leg (P4/P5/stage) must assert the **file
> exists** *and* has **exactly 181 rows** — `rc=0` alone proves nothing.

**Agents-tree arithmetic:** 366 (base) + **11** new files (7 canary + 1 aegis mirror + 1 worker
mirror + 1 sweeper mirror + 1 sweeper manifest) = **377**; **10** files are modified in place (6
persona `rule.md`s + `aegis/meta.json` + `aegis/workflow.md` + `aegis/skill-set.yaml` +
`worker/skill-set.yaml`). **Re-derive both numbers from the actual file list at P4 time** — the plan
states the derivation rule, not a frozen expectation.

> **House-leg naming (the pack registry cites them):** the lane's new legs are named **T31–T37**
> and must appear as `# T3x` comments in the lane test file so `.agents/tester/PACKS.md` can cite
> them. The inventory is **binding**; the file/leg organisation is the architect's D5 call
> (`requirements.md` CER-03, D5). **D5's default = Option A** — the **family file grows** (the lane
> already has to edit it for T5/T6/T7/T8/T21/T22/T25/T29), so there is **no new test file**; the
> skill-only concerns may move into a separate `test_injection_screen.py` if the architect prefers a
> split by concern.

**Discoverable-agent arithmetic:** 43 discoverable agent directories (47 on disk, 4 `_`-prefixed
skipped) ⇒ **44** with `canary`.

**Ordering rule:** the re-stamp is the **LAST** content change under `agents/**` in the lane. Any
prompt edit after the re-stamp drifts the baseline and reddens the stage-time leg.

---

## Merge-Overlap Strategy (memoir lane)

**The situation.** The parallel **memoir-store** lane (`feature/ari-memoir-store`, forked from
`1f6898ae2`, ~27–29 commits) is **not merged** and, as of the base commit, touches **zero** `agents/`
paths — the claimed `aegis`-file overlap is **not on disk yet**. It carries a migration
**119 → 120**. The overlap is therefore a **conditional hazard**, not a current conflict.

**What this lane does about it:**

1. **Keep the `aegis` edits minimal and additive.** The two `aegis` edits are (a) one line in
   `meta.json` and (b) one **appended** section at the end of `workflow.md`. Additive-at-EOF is
   chosen deliberately: it minimises the conflict surface if memoir later edits `agents/aegis/**`.
   The `aegis` **rule.md** edit is likewise a single appended guideline (`### (g)`), never a rewrite.
2. **Close the baseline and the seal on the MERGED tree.** The `.prompt-artifacts.sha256` re-stamp
   and any SEALED re-pin are computed **after** the merge, on the merged tree — never on the lane
   branch. The row count stays **181** under content-only overlap (an edit changes a hash, not a
   row); if memoir adds a *new prompt file* to any agent, the count moves and the re-stamp must be
   re-derived from the merged tree, not from this plan's arithmetic.
3. **PACKS.md conflicts resolve keep-both, newest-first** (house precedent) — the lane's pack
   registrations and memoir's both survive.
4. **The rollback-safe adjudication is reasoned on the MERGED delta.** If memoir has landed, the
   migration delta is **+1 (memoir's)** — not this lane's. This lane authors **zero** `.sql`
   files, so its own delta is empty; the adjudication must state the merged delta's nature
   explicitly (additive `CREATE TABLE` ⇒ inert on rollback ⇒ `rollback_safe=true`, source explicit).
5. **The version slot is re-derived at stage time, never reserved.** `v0.28.13` is **contended**
   with the memoir lane — the W9 derivation at stage time is the only authority (§Risks R5).
6. **If memoir lands `agents/aegis/**` edits during the lane**, the merge resolution keeps **both**
   lanes' content and the re-stamp runs **once** on the merged tree. The lane does not attempt to
   pre-empt or guess memoir's content.

**What this lane does NOT do:** it does not touch memoir's branch, worktree, files or migration; it
does not reserve a version slot; it does not modify `origin/latest` outside its own merge.

---

## Verification gates (P5 — each independently checkable)

| Gate | What it proves | How |
|---|---|---|
| **A** | No behaviour regression | Full suite, net-new deterministic reds **0** vs the true pre-fix base (same commands, same environment). **Expected-ledger note (the re-derivation set):** the shared pins that this lane **must** re-derive are **T4** (team set 4→5), **T5** (the leaf tool-set parametrisation now includes canary), **T6/T7/T8** (canary's allow/deny/privileged intersection), **T21** (the family-manifest list — reds if the skill is seeded into any member beyond aegis, so it is re-derived against the new roster), **T22** (generalised from aegis-only to every seeded (agent, name) pair), **T25** (worker byte-unchanged — **reds by design this lane** and is re-worded against this lane's own base, its intent restated as "worker touched by design"), and **T29** (25 cells + the completeness constant). Each is **green at base** and **green at HEAD only via the in-commit folds**; absent the folds they are the HEAD-only reds. Post-fold none appears in either FAILED list and the ledger records the folds (no flip signature appears). Any OTHER HEAD-only red is a lane regression and blocks. **Unchanged by this lane (asserted, not assumed):** the leader roster (already contains `aegis`; canary is an aegis child ⇒ **no second one-liner**), the baseline count (177 → 181), `tool-categories.json` (untouched), the seal map (untouched). |
| **B** | `SEALED_SHA_BASELINES` untouched (35 entries) | `tests/test_chart_image_delivery_e2e.py::TestPhaseDDoesNotModifySealedArtifacts` green on the branch. **Re-pin ONLY if this lane touches a sealed file** — expected: none (the lane touches `agents/**`, `tests/**` and the planning set only). **Disclosure carried forward:** 4 rows are **already base-stale** (`ari/workflow.md`, `ari/rule.md`, `daemon/sources/registry.py`, `daemon/constants.py`) and are owned by the seal lane — the gate's shape is **PASS-WITH-DISCLOSURE**, and this lane does **not** re-pin them as a side effect. |
| **C** | The plane read-holder census is unchanged | `tests/unit/tools/test_plane_agent_surface_census.py` green with `DISCLOSED_READ_HOLDERS` still `{"watcher"}` — **not** extended to canary or to the screened personas. The fix is an explicit non-empty allow on every new meta, not a whitelist entry. |
| **D** | The report-integrity posture + the prompt-section integrity sweep (the predecessor's **two-legged D**) | (D-1) `tests/unit/test_report_integrity_prompts.py` green. **Canary is a leaf under the D1 default** ⇒ it is **skipped** by the empty-team exemption (`test_report_integrity_prompts.py:242`) — a skip, not a pass-by-omission. **If D1 grants a scoped spawn right**, the guidance becomes **mandatory** and this gate's shape changes (P3 task 9 + P4 task 2). `aegis`'s existing scrutiny guidance (canonical home: its `rule.md`) is **not** removed by the `workflow.md` section append. (D-2) `tests/unit/tools/test_prompt_section_reference_integrity.py` green — the repo-wide prompt sweep over **every edited prompt file** (the six persona `rule.md`s + canary's four prompt files), enforcing the `.md`-token class and the bare-`agents/`-prefix closure class (`requirements.md` CER-15). |
| **E** | Boot-scan clean on the new agent set + the skill loadable | Registry scan resolves `canary`; discoverable agent dirs **44** (43 + 1); the isolated boot scan is clean; the `injection-screen` skill **loads** for each member of the screened roster (manifest-gated seeding: the manifest enumerates the entry and the body must exist at the same-dir template path). |
| **F** | No tool-category drift (the F-1 proof) | `.agents/shared/metrics/tool-categories.json` **byte-unchanged** in the lane diff; `tests/unit/tools/test_tool_category_artifact.py` green. **A skill is not a category** — this lane registers no category. |
| **G** | The manifest delta is re-derived from the real tree | `find agents -type f \| wc -l` ⇒ **377** (or the re-derived value) and the discoverable-dir count ⇒ **44**; the staged payload's agents map matches. |
| **H** | Scope + privacy (the lane-own diff) | The **lane-own diff** touches only `agents/**`, `tests/**`, the lane's committed planning set, the **tester lane's pack registration** (`.agents/tester/PACKS.md` — house bookkeeping, exactly like the approver bookkeeping) and the approver bookkeeping — the complement of {`agents/`, `tests/`, `.agents/tester/PACKS.md`, `.agents/approver/`} equals exactly `.agents/shared/planning/canary-injection-defense/`. **Privacy legs:** (i) **no seed corpus file contains real credential material** — a secret-shaped string is a lane failure, not a finding; (ii) **no campaign artifact, engagement output or corpus output is committed**; (iii) findings/campaigns live **off-repo** under `$HOME/.local/state/aegis/engagements/<id>/` — team-internal, **never committed**, discovery by **recorded path, never glob**; (iv) `agents/leader/**`, `agents/developer/**` and every persona outside the declared set are byte-identical to the base. |

---

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| **R1** | **Memoir-lane collision (conditional).** The memoir lane is unmerged and touches zero `agents/` paths **today**, but it may later edit `agents/aegis/**` and it carries a migration 119→120. A collision at merge time can move the baseline row count or redden the merged tree's gate. | Medium | Medium | (a) `aegis` edits kept **minimal + additive** (one line + one EOF-appended section). (b) The baseline re-stamp and any SEALED re-pin are computed **on the merged tree**. (c) The row-count assertion is **derived** at P4/P5 from the merged tree, never from this plan's number. (d) PACKS.md keep-both newest-first. (e) The rollback-safe adjudication is reasoned **on the merged delta**, naming memoir's migration as memoir's. (f) The slot is re-derived, never reserved. |
| **R2** | **Fence-contract brittleness.** The injection-screen's fenced output is a **pinned machine-checkable contract** — that is its whole value. A grammar that is too rigid reddens on legitimate content (and gets "fixed" by weakening the pin); a grammar that is too loose becomes unfalsifiable (nothing to test, nothing to pin). | **High** | Medium | (a) **Contract-first** (P1): the grammar is specified before the body is written. (b) The pin is on the **grammar + header schema**, not on incidental formatting. (c) Golden fixtures per rule ID **plus** benign controls — a grammar that mangles benign content is a red, not a pass. (d) The LLM classification pass is **optional**; the heuristic path alone must be deterministic and pinnable, so the contract never depends on model behaviour. |
| **R3** | **Canary's self-injection / self-hypnosis.** Canary's *job* is hostile content. It necessarily authors, reads and transports payloads — the exact material that could rewrite its own reasoning (self-hypnosis: the payload becomes the persona's instruction). | **High** | Medium | (a) The **D3 carve-out** (default, stated in P2/P3): canary consumes payloads **only through its own fence**, never as instructions in its own turn. (b) Canary's `rule.md` carries a **self-fencing discipline** among its cardinals. (c) The T29 matrix canary row pins **single-site anchors** for every family, so deleting a clause reddens the cell (the self-proof pattern). (d) Campaign artifacts live off-repo; a payload is referenced by **id**, never replayed verbatim into a reasoning surface. (e) Canary is **report-only** — it never modifies a tested agent, so a hijacked canary still cannot damage a target. |
| **R4** | **Skill-copy drift across N agents.** The screened roster receives **per-agent copies** (there is no shared-source mechanism; no skill name spans two `agent_id`s today). Copies drift silently — and a same-version edit **never re-seeds** (the W4 version guard), so a drifted copy stays drifted. Under D2's default there are **4** bodies (canary + aegis + worker + sweeper); under D2=A there would be 6. | Medium | **High** | (a) **Mirror-declaration requirement**: every copy declares its canonical home and the pin asserts **byte-identity across the roster** (modulo blank lines, the predecessor's T23 precedent). (b) A **version bump is the propagation lever** (W4) — a rule-set change bumps every copy's version **and** its bytes in the same commit, or the copies silently drift. (c) Canary owns the canonical copy and the evolution path is **git-only** (file skills never take `skill_fix`). (d) The roster is **derived at P1 and pinned at P4** — a new copy without a pin entry is red. |
| **R5** | **Version-slot contention.** `v0.28.13` is **contended** with the memoir lane; the slot is derived from live tags + `releases/` at stage time and is not reserved by this lane. | Low | Medium | The W9 derivation at stage time is the only authority (§Merge-Overlap Strategy); a slot is never reserved or hand-written. |
| **R6** | **The "exactly one manifest" pin does not carry — and three more shared pins red with it.** The predecessor lane pinned *exactly one* `skill-set.yaml` in its family (T21), aegis-only skill versions (T22), and worker byte-unchanged (T25). Injection-screen seeds into **four** agents ⇒ T21's premise is false here; the roster changed ⇒ T22 must be generalised to every seeded (agent, name) pair; and worker **is** touched (doctrine + mirror) ⇒ T25 **reds by design**. Reusing any of the three verbatim reddens the lane. | Medium | **High** | The **re-derivation set** is a **named P4 task**: T21 → "the family manifests are exactly the seeded set"; T22 → every seeded (agent, name) pair; T25 → re-worded against **this lane's own base**, its intent restated as "worker touched by design". The predecessor's premises are **stated, not inherited**. The Gate A ledger names each fold so a missed fold shows up as a HEAD-only red with a known cause. |
| **R6b** | **Disclosed coverage gap — `hacker` and `cracker` get no skill copy.** Under D2's default the seeded roster is the **standing readers**; the two leaves that compose offensive and protective payloads are covered by the **doctrine layer only**. Their inputs are largely self-authored synthetic material, which is why the gap is defensible — but it is a gap, and a payload arriving from an external corpus reaches them with only the doctrine backstop. | Low | Medium | The gap is **stated in the plan, in the lane report and in the campaign coverage table** — never hidden. The doctrine layer is the backstop, and the backstop is pinned (T31 anchors). A future lane can widen the roster to D2's Option A (the whole family) with the mirror-identity pin already in place. |
| **R7** | **Baseline drift.** The baseline pins content hashes; a prompt edit after the re-stamp (or a fifth prompt file on canary) silently moves the count and reddens the stage leg. | Medium | Medium | The re-stamp is the **last** `agents/**` content change (P4 ordering rule); `--check` runs before the commit and again in P5 Gate A; the lane test asserts the file **exists** with **exactly 181 rows**. |
| **R8** | **Scope creep into `daemon/`.** A "real" fence at the intake seams (truncating bash output, capping MCP results) would be a `daemon/` change — a category/registration risk and a live-behaviour change. | Medium | Medium | The fence is **doctrinal + skill-level** in v1: P5 asserts the lane diff contains only `agents/**`, `tests/**` and the planning set; P6's migration-delta census is the independent proof. The intake-surface map (P3 task 7) is the **finding record** for a future daemon-side lane, not a v1 change. |
| **R9** | **Prompt-prose violations across the authored files.** The guide's mechanically-checkable rules (cardinal cap ≤7, tone block, `.md` path tokens, the bare `agents/`-prefix closure class, system internals, one canonical home) apply to every authored file — and canary's files are new while four existing personas are edited. | Medium | High | The **guide-compliance checklist is an explicit gate in every authoring phase** (P0 produces it; P1/P2/P3 audit against it); the lane's T-pack mechanises the mechanically-checkable half, reusing the predecessor's test-file patterns. |
| **R10** | **Findings privacy.** Off-repo storage keeps campaign artifacts out of git, but the artifacts are then not PR-reviewable and need filesystem access; in-repo storage would land hostile payloads in git history. The off-repo root is **reachable, not confined** — absolute paths are trusted by design and `bash` is unconstrained. | Medium | Medium | (a) Off-repo is the **ratified** family pattern (`$HOME/.local/state/aegis/engagements/<id>/`) — canary inherits it, so campaign artifacts sit beside the family's other engagements. (b) Discovery is by **recorded path, never glob** (the walk tools refuse absolute roots outside the workdir). (c) The honest sentence — "the privacy of the artifacts is discipline, not a boundary" — is stated in the plan and in canary's persona. (d) **No committed corpus artifact may contain real credential material** (Gate H). |
| **R11** | **Finalize-guard note.** Branch/tip drift is handled by the house **finalize guard**: at merge time, fetch and verify `origin/latest` is **unmoved** from the branch base — moved ⇒ **STOP** and re-derive the merge base rather than proceeding; the merge is `--no-ff`; the commit composition is proved with `git show --stat HEAD`; the stage-time tip-identity check is honoured rather than overridden. A version slot is never reserved by this lane — it is re-derived at stage time. | Low | Medium | The guard is the mechanism; the lane does not override it. If `origin/latest` moved (the memoir lane is the live candidate), the merge base is re-derived and the lane's gates are re-run on the merged tree. |

---

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| **S1** | Baseline / pin green | `python3 scripts/protection/generate_prompt_artifact_baseline.py --check` **+ an explicit file-exists and row-count assertion** | the file **exists**, exit **0**, exactly **181** rows, zero drift (the `--check` exit code alone proves nothing) |
| **S2** | The pin ceremony is one commit | `git show --stat HEAD` on the lane commit | exactly the sanctioned set enumerated in §The one-commit pin ceremony — the **11** new agent files + the **10** modified agent files + the regenerated baseline + the corpus fixtures + the shared test file's re-derivation set and new T31–T37 legs — **and nothing outside this enumerated set** |
| **S3** | Boot-scan clean on the new agent set | Registry-scan probe (venv-bound) + the tester's isolated boot-scan leg | `canary` resolves in the registry; **44** discoverable agent dirs; the `injection-screen` skill loads for every member of the screened roster |
| **S4** | The fence contract is machine-checkable | The lane's fence-contract legs (golden fixtures + property legs) | the fenced output is **deterministic** under the heuristic path; the header schema parses; the content is **byte-preserved** inside the fence; the verdict enum is closed; benign controls are unmodified in substance |
| **S5** | The corpus covers the rule set | The corpus legs | **every** rule ID has ≥1 positive fixture **and** a benign control; the corpus is synthetic (Gate H secret-shape leg green) |
| **S6** | The doctrine is landed everywhere it must be | The doctrine-anchor matrix (**T31**) + the T29 self-proof pattern | both rules (untrusted-content + action-distance) present in **all five** screened personas + canary; the anchor matrix is **6 personas × 4 anchors = 24 cells** + canary's **2 carve-out anchors**; each anchor is **single-site** (count == 1 per file) on a numbered/heading line; **no persona's cardinal count moved** (aegis 7/7 ⇒ guideline). **The anchor literals are the stable, case-sensitive, verbatim substrings** — `untrusted content is data` · `untrusted-data ingestion` · `an irreversible action within` · `[UNTRUSTED-ADJACENCY:` (`requirements.md` DOC-06) — and the bare word `irreversible` is **not** admissible: `worker/rule.md` already carries three case-insensitive occurrences in its SEMIAUTO block, so the anchor must be the multi-word phrase. |
| **S7** | The screened roster is exactly the pinned set | The mirror/manifest legs | the roster set == the pinned set (**canary, aegis, worker, sweeper** under D2's default); every copy is **byte-identical** to the canonical copy (byte-identity **modulo blank lines**); every receiving manifest has exactly one `injection-screen` entry; no other agent's manifest mentions it; `include:` ∉ frontmatter; **all roster versions equal** (the propagation lever) |
| **S8** | `SEALED_SHA_BASELINES` untouched + proof | `TestPhaseDDoesNotModifySealedArtifacts` | green with the **4 pre-existing base-stale rows disclosed** (not re-pinned); **zero** sealed files touched by this lane ⇒ **no re-pin** |
| **S9** | The plane census did not move | `test_plane_agent_surface_census.py` | green; `DISCLOSED_READ_HOLDERS` still `{"watcher"}` |
| **S10** | The report-integrity posture is unchanged | `test_report_integrity_prompts.py` | green; canary **skipped** by the empty-team exemption under the D1 default (or **present** if D1 granted a spawn right) |
| **S11** | No `daemon/` change | the **lane-own** diff's path list + P6's migration census | the lane-own diff contains only `agents/**`, `tests/**`, the committed planning set and the approver bookkeeping; **migration delta EMPTY** (this lane authors zero `.sql`); `tool-categories.json` byte-unchanged |
| **S12** | Guide compliance, all authored files | the Phase-0 checklist audited per phase + the lane's T-pack | every checklist row satisfied per authored file; the mechanically-checkable half green across every authored file |
| **S13** | The shared pins were folded, not left broken | `test_aegis_security_family.py` | the **re-derivation set** (T4, T5, T6/T7/T8, T21, T22, T25, T29) is folded; T4 == the 5-set; T29 matrix == **25** cells with the completeness leg `len == 25`; the canary row's anchors are single-site; **every leg outside the re-derivation set is byte-stable** |
| **S17** | The new legs are named and registered | the lane test file's `# T3x` comments + `.agents/tester/PACKS.md` | the **T31–T37** inventory is complete and each leg carries its `# T3x` marker; the packs are registered in the house format with the mutation evidence written as *planted change → expected red set → observed red set → restore → green* (`requirements.md` CER-03, CER-08) |
| **S14** | Staged release with full provenance | P6's verification | 4-way sha match (release dir == worktree dist == manifest == sidecar); both sidecars `git_dirty=false`; payload source-less; prompt baseline clean in the payload; agents tree re-derived |
| **S15** | Standard lane — STAGE, never promote | P6's journal + the live fence | the promote command is **recorded** with the ceremony preconditions and **not executed**; the live daemon, the `current` symlink and the promote fields of the state journal are byte-identical throughout |
| **S16** | No behaviour regression | P5 Gate A | net-new deterministic reds **0** against the true pre-fix base |

---

## Dependencies

| Dependency | Kind | Notes |
|---|---|---|
| `origin/latest` @ `97b76e20e` | external | the branch base; the finalize guard must find it unmoved at merge time (R11) |
| `decisions.md` (sibling-owned) | in-repo, parallel | the **D1–D6 rulings** and the deviation log — this plan references it, never duplicates it. P0's close-condition is the architect sign-off there. |
| `technical-analysis.md` (sibling-owned) | in-repo, parallel | the design resolution behind P1–P3 (grammar design, distribution analysis, corpus shape). |
| `requirements.md` (sibling-owned) | in-repo, parallel | the requirement set this plan satisfies — the traceability source for the gates. |
| `.agents/shared/planning/aegis-security-family/**` | in-repo predecessor | the **structural template** for this plan set and the source of the pin/leg patterns (T4, T29, the matrix self-proof, the roster fold, the baseline arithmetic, the gate names A–H, the staging choreography). |
| `agents/aegis/**`, `agents/hacker/**`, `agents/cracker/**`, `agents/sweeper/**` | in-repo exemplars | the persona shapes, the guardrail-family wording and the off-repo engagement-store pattern. |
| `agents/worker/rule.md` | in-repo exemplar | the **legacy** persona shape (`## Must` + `### 🚨 CRITICAL:` blocks) that the doctrine must fit, not overwrite. |
| `docs/agent-prompt-writing-guide.md` | in-repo binding spec | the checklist source (cardinal/guideline split, one canonical home, token classes). |
| `scripts/protection/generate_prompt_artifact_baseline.py` | in-repo tool | venv-free baseline generator/verifier. |
| `daemon/tools/prompt_artifacts.py` | in-repo (stdlib-only) | the depth-2 collector that defines the 181-row arithmetic. |
| `daemon/services/skill_seed_service.py` | in-repo | the manifest-gated seeding path (enumeration, same-dir template requirement, `requires:` → `capability_check`, the W4 version guard). |
| `daemon/graph.py::_frame_injected_report` | in-repo precedent | the **only** existing model-visible untrusted-data frame in the system — the fence precedent the doctrine extends. |
| `tests/unit/agents/test_aegis_security_family.py` | in-repo test surface | the shared pins this lane folds (T4, T29) and the matrix pattern the canary row extends. |
| The **memoir lane** (`feature/ari-memoir-store`) | external, concurrent | conditional overlap + a migration 119→120 — see §Merge-Overlap Strategy. |
| A **user nonce ceremony** | external, user-owned | owns the promote flip; the family cannot be exercised live before it. |

---

## Adoption by other personas (recommendation only)

The doctrine (untrusted-content rule + action-distance rule) is v1-scoped to the **five screened
personas plus canary** — `aegis`, `hacker`, `cracker`, `sweeper`, `worker`, `canary` — because the
commission names exactly those surfaces and forbids touching other personas.

**Recommendation for the lane report (a recommendation, not a task, not an edit):** the same two
rules are persona-agnostic and should be adopted by `leader`, `developer`, `reviewer`, `tester`,
`architect` and `approver` in a **later** lane — every one of those personas ingests untrusted
content (repo files, diffs, CI output, issue text, tool output). Adoption is mechanically cheap
(the blocks are additive) but it is **not** this lane's business: touching other personas would
break the commission's scope boundary and widen the diff against the concurrent memoir lane.

**What to write in the report:** the list of personas that ingest untrusted content, the two rules in
one line each, and the fact that adoption is deferred with its rationale. **What not to do:** edit
those personas.

---

## Post-lane: the first canary campaign

The lane ships the **capability**, not a campaign. The first real campaign — a canary engagement
against a self-owned sandboxed target in the dev daemon, with the injection-screen exercised on
real intake surfaces — is a **post-ceremony operational step**, for three reasons: it requires a
**live** family (the user's nonce ceremony); it is a run against live-ish targets, not a code
artifact, so it has no commit to gate; and validating the red team *before* its own test suite
exists would be backwards.

**Prerequisites before the first campaign:** the staged release is promoted; a dev daemon is
available on `:8079` against `ensemble_dev`; the target instance is self-owned (created by `aegis`
under its own `parent_id`); the campaign store path is recorded, not globbed.

**Expected shape:** `aegis` opens the engagement (scope → intake-surface map → rule-set review) →
composes a payload set → `canary` refines and packages it → `aegis` spawns the sandboxed target →
the payload is fired through a real intake surface → `canary` measures the target's behaviour (did
it follow the payload? escalate? attempt a tool call it should not?) → the finding lands in the
campaign store → the rule set is evolved (git PR) → the finding feeds aegis's report → fix → re-verify
loop.

---

## Pointer index

| File | What it holds |
|---|---|
| [`plan-overview.md`](./plan-overview.md) | **This file** — the synthesis, the phase table, the D1–D6 dependency map, the coupling map, the pin-ceremony composition, the merge-overlap strategy, the gates, the risks, the success criteria. |
| [`phase0-plan.md`](./phase0-plan.md) | Plan review (architect/council/approver) + the exemplar read + the guide-compliance checklist + the D1–D6 default-assumption table. |
| [`phase1-plan.md`](./phase1-plan.md) | The `injection-screen` skill — the fence grammar, the provenance header, the heuristic rule registry, the seed corpus, the mirrors + manifests. |
| [`phase2-plan.md`](./phase2-plan.md) | The fencing doctrine — the untrusted-content rule + the action-distance rule across the four existing personas, and canary's from-birth form. |
| [`phase3-plan.md`](./phase3-plan.md) | The `canary` agent — the 5-file persona set + meta + the canonical skill copy + the aegis roster one-liner + the aegis when-to-engage section. |
| [`phase4-plan.md`](./phase4-plan.md) | The one-commit pin ceremony + the shared test file's re-derivation set + the new T31–T37 legs. |
| [`phase5-plan.md`](./phase5-plan.md) | Verification — gates A–H. |
| [`phase6-plan.md`](./phase6-plan.md) | Staging handoff — slot derivation, merge, bump, tag, stage, provenance, the recorded (not executed) promote command. |
| [`decisions.md`](./decisions.md) | **Sibling-owned** — the decision record, the **D1–D6 rulings**, the deviation log. This plan consumes it; it never duplicates it. |
| [`technical-analysis.md`](./technical-analysis.md) | **Sibling-owned** — the design resolution behind P1–P3 (grammar, distribution, corpus, spawn topology). |
| [`requirements.md`](./requirements.md) | **Sibling-owned — BINDING** for this plan set: the requirement set (`SK-*` skill, `DOC-*` doctrine, `CAG-*` canary, `COR-*` corpus, `NFR-*` non-functional, `CER-*` acceptance criteria) with the traceability map to phases. Every gate in this plan maps to a `CER` row. This plan consumes it; it never duplicates it. |

---

## Research Insights (the findings that shaped this plan)

1. **There is exactly one model-visible untrusted frame in the system today** — the child-report
   `[SYSTEM NOTE: …]` wrapper (`daemon/graph.py:343-373`). It is the fence precedent the doctrine
   extends; the injection-screen's grammar is the **generalised** form of it.
2. **Twelve intake surfaces are model-visible, and the guard is uneven.** bash stdout/stderr is
   **unconfined**; `read_file` accepts an absolute path with **no boundary check** (6000-char cap);
   MCP results are **verbatim and unbounded**; `read_context` bodies are unbounded and
   **auto-injected** for `worker`. These are the canary campaign map (P3) — and the reason the
   doctrine must be **behavioural**, not merely documentational.
3. **The sandbox is logical, not structural.** An instance is a DB row + a LangGraph session **in the
   daemon process** — there is no OS or container boundary. `bash` is unconfined, so a sandboxed
   canary target can read the live install and reach prod PG. This is stated plainly in canary's
   persona and in the plan; it is **not** implied away.
4. **`ENSEMBLE_SELF_ENV` is identity *detection*, not DB binding** — and `data/ensemble.json`'s
   postgres pin **outranks** a scrubbed env (a second poison vector). Any dev boot in this lane must
   check the worktree's `data/` before booting.
5. **A skill is not a category.** The skill system seeds from `skill-set.yaml` with a same-dir
   `skills-template/<name>.md` body; there is **no shared-source mechanism** — cross-agent reuse is a
   **per-agent copy + a per-agent manifest entry**, and no skill name spans two `agent_id`s today.
   Hence the mirror-roster design and the drift risk (R4).
6. **A same-version edit never re-seeds** (the W4 version guard) ⇒ a **version bump is the
   propagation lever** for a rule-set change. This is the mechanism behind the mirror pin.
7. **File skills evolve by git only** — `skill_fix` refuses a project-file skill. Canary's rule-set
   evolution is therefore a **git PR**, not an in-place mutation.
8. **The baseline is depth-2 and content-hashed** ⇒ canary adds exactly **4 rows** (177 → 181) and
   every persona edit changes hashes, not counts. The `--check` exit code proves nothing on its own
   (the file may be absent) ⇒ existence + count are asserted.
9. **The T29 legality matrix is 4 personas × 5 families = 20 cells**, each anchor **single-site by
   construction** (the self-proof leg). Canary is the **5th persona** ⇒ **25 cells**, and the
   completeness leg's count moves with it. `worker` is **outside** the matrix by design (it is not a
   security persona) — the canary row is added, the worker row is not.
10. **Aegis is at 7/7 cardinals** ⇒ its doctrine must land as a **guideline** (`### (g)`, after
    `(f) Compliance With Project Conventions`); the three children end at `(e)` ⇒ they append
    `(f)`; `worker` uses the **legacy** `## Must` / `### 🚨 CRITICAL:` shape.
11. **A leaf agent is exempt from the report-scrutiny requirement** (the empty-team skip). That
    exemption is a **direct consequence of D1** — a scoped spawn right flips it back on.
12. **Adding an agent is a prompt-prose event, not just a file event.** The guide's mechanically
    checkable rules (cardinal cap, tone block, `.md` path tokens, the bare `agents/`-prefix closure
    class, one canonical home) apply to every authored file — hence the P0 checklist as a gate in
    every authoring phase.
13. **The memoir lane's overlap is a conditional hazard only** — zero `agents/` paths on disk today;
    the merge-overlap strategy (minimal additive `aegis` edits + close on the merged tree) is what
    makes it cheap if it becomes real.
14. **`v0.28.13` is contended** — the slot is re-derived at stage time, never reserved.
