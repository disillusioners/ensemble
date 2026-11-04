# Decision Record — Aegis Security Family

**Date:** 2026-11-04
**Author:** planner[v2] via plan-creation worker
**Status:** Draft — Q1–Q4 are OPEN and are Phase-0 entry blockers for the implementation arc
**Companion records:** [`technical-analysis.md`](./technical-analysis.md) (the design resolution this
record summarises) · [`research-findings.md`](./research-findings.md) (the verified evidence digest —
every A#/B#/C#/D# citation resolves there) · [`plan-overview.md`](./plan-overview.md) (the executable
phase plan set)

---

## 1. How to read this record

- **§A — the DP1–DP8 resolutions.** What was decided, in one or two lines each, with a pointer into
  `technical-analysis.md` and the research fact the decision rests on. These are **consumed verbatim**
  by the phase plans; this record does not re-derive them.
- **§B — the concrete artifacts the resolutions imply** (the file manifest, the tool matrices, the
  arithmetic rules). These are the things a developer implements without re-reading the commission.
- **§C — the deviations** this plan makes from `technical-analysis.md`, each flagged rather than
  applied silently.
- **§D — the leader one-liner**, quoted verbatim.
- **§E — Q1–Q4**, carried OPEN with a recommendation and the concrete consequence of each answer.

---

## A. The DP1–DP8 resolutions (consumed, not re-derived)

| # | Decision (one line) | Rationale pointer | Rests on |
|---|---|---|---|
| **DP1** | **Tool matrix.** A closed `tools.allow` is the fence; `tools.deny` carries only intra-category subtractions; `tools.deny_spawn` carries the single implied-member trim. Aegis gets 17 categories, each child 6. | `technical-analysis.md` §DP1 (lines 123–278) | A1, A5, A6, D1, D3, D5, D13, D14 |
| **DP1a** | The **CVE-lookup surface** is `mcp` (the builtin `webfetch` server — deployment-conditional) **plus** `bash curl`, on **aegis only**; CVE context reaches the children in the dispatch prompt. `mcp_rpc` is privileged and unavailable. | §DP1 "CVE-lookup surface" (lines 227–246) | A7, D8, D12 |
| **DP1b** | **Implied members.** `knowledge` ⇒ `explorer`/`kb-writer` is **trimmed** via `deny_spawn: ["knowledge"]`; `chart` ⇒ `charter` is **accepted** (the Q1 deviation). | §DP1 "Implied-member side effects" (lines 247–265) | A1, D4, D5 |
| **DP1c** | **The write fence.** `allow filesystem` + `deny ["write_file","edit_file"]` on all four agents. It removes the easiest accidental mutation; **`bash` remains an unconstrained write path** ⇒ read-only-on-target is **doctrinal** for the whole family. | §DP1 "The write fence" (lines 206–226) | A6, D5 |
| **DP2** | **Skill architecture.** One family, four homes: **one seeded** skill (`aegis-strategy` — methodology, severity taxonomy, finding record) and **three file-invoked** installer skills (their procedure text never enters the global skill bank). | §DP2 (lines 281–358) | C2, C6, C5 |
| **DP2a** | **The checksum amendment.** The charter installer does not checksum its artifacts — insufficient for scanners. Every installer in this family **sha256-verifies its fetched release asset before promotion**, combining the hex `SHA256SUMS` + W3C-SRI precedent. | §DP2 "The checksum gap" (lines 326–331) | C3 |
| **DP2b** | **Three self-contained libraries + a test that pins the FENCES block identically** — not one shared library (a shared library would need a path reference: fragile across worktree vs installed tree, and a prompt-prose hazard). | §DP2 (lines 309–324), alternatives table | C1 §3, C3 |
| **DP3** | **Sandbox model.** One engagement-scoped sandbox root, created once with `mktemp -d`, owned by aegis, inherited by every lane; the "self-owned" check is a **mechanical prefix assertion** performed before any active technique. No Docker; plain directories + user-space processes. | §DP3 (lines 362–451) | C3, D10 |
| **DP3a** | **Process discipline.** Every process is launched from the sandbox root, tracked via `proc`, and stopped at lane teardown — never left running past END TURN. | §DP3 "Self-owned process discipline" | C1 §7 |
| **DP4** | **Findings + loop.** A **five-level severity taxonomy** (critical/high/medium/low/info) with a separate **confidence** field; a **finding record** with a separate **confidence** field (ten named fields — see `decisions.md` D-6 on the "nine" label in the upstream header); storage **outside both repos**; the hand-off is a **message to `leader`**; re-verify is **narrow, per-finding, severity-gated at 3 rounds**. | §DP4 (lines 454–593) | D11, C1 §8 |
| **DP4a** | **On a failed re-verify** the finding returns to `open` at its **original** severity — re-verify never downgrades severity on its own. | §DP4 "On a failed re-verify" | — |
| **DP5** | **Peer posture.** `ari` reaches aegis through `job_create` with **zero** ari changes; `leader` reaches it through **one** `team_members` line; all four agents use `default_queue: system_parallel_queue`; **no `watchover` block** in any meta. | §DP5 (lines 596–654) | A3, A4, A10, D7 |
| **DP6** | **Models.** `aegis` = `agentic`; `hacker`/`cracker`/`sweeper` = `coding`. No `vision`, no `img-gen`, no `decision`, no `coding2`. All inside the shipped default — no config change is owed. | §DP6 (lines 657–698) | A8 |
| **DP7** | **Test/pin fold.** One new test file with **T1–T28**; **one commit** carrying the four agent trees + the leader one-liner + the regenerated baseline (161 → 177) + the test file; the re-stamp is the **last** `agents/**` content change. | §DP7 (lines 702–804) | B1, B2, B4, B8, D9 |
| **DP8** | **Phase structure.** Seven phases, P0–P6, standard lane flow (plan → councils → implement → verify → **STAGE**; never promote), each with an independently checkable gate. | §DP8 (lines 808–845) | E7 |

---

## B. The concrete artifacts the resolutions imply

### B.1 The file manifest (28 new files + 1 modified)

| Path | New? | Notes |
|---|---|---|
| `agents/aegis/meta.json` | new | DP1 matrix; `team_members` = `[hacker, cracker, sweeper, worker]`; `deny_spawn` = `["knowledge"]` |
| `agents/aegis/soul.md` | new | identity + tone block (guide §5), incl. per-severity framing |
| `agents/aegis/rule.md` | new | **≤7 cardinals** (guide §3) |
| `agents/aegis/workflow.md` | new | dispatch / fan-in ladder / END TURN / report → fix → re-verify |
| `agents/aegis/tools_note.md` | new | category → why-I-hold-it table (guide §4) |
| `agents/aegis/skill-set.yaml` | new | **the family's only manifest** — one entry: `aegis-strategy` |
| `agents/aegis/skills-template/aegis-strategy.md` | new | **seeded** (`auto_load: true`) — canonical home for the taxonomy + the finding record |
| `agents/hacker/{meta.json,soul.md,rule.md,workflow.md,tools_note.md}` | new (5) | leaf; `llm_model: coding` |
| `agents/hacker/skills-template/fuzzing-toolchain.{md,lib.sh}` | new (2) | **file-invoked** — libFuzzer primary, AFL++ secondary |
| `agents/cracker/{meta.json,soul.md,rule.md,workflow.md,tools_note.md}` | new (5) | leaf; synthetic-data-only rule |
| `agents/cracker/skills-template/credential-bench.{md,lib.sh}` | new (2) | **file-invoked** |
| `agents/sweeper/{meta.json,soul.md,rule.md,workflow.md,tools_note.md}` | new (5) | leaf; SAST + CI supply chain |
| `agents/sweeper/skills-template/sast-toolchain.{md,lib.sh}` | new (2) | **file-invoked** |
| `agents/leader/meta.json` | **modified** | one line — `"aegis"` added to `team_members` |
| `agents/.prompt-artifacts.sha256` | **regenerated** | 161 → 177 rows |
| `tests/unit/agents/test_aegis_security_family.py` | new | T1–T28 |

**Arithmetic rules (re-derive at P4, never trust a frozen number):**

- **Baseline rows** — the collector is depth-2 and covers only the versioned prompt artifacts
  (`soul/rule/workflow/tools_note` + the legacy `tools.md`/`skill.md`/`memory.md`/`growth.md`).
  4 agents × 4 prompt files = **+16** ⇒ **161 → 177**. `meta.json`, `skill-set.yaml` and
  `skills-template/*` contribute **zero** rows; the `leader` edit touches `meta.json` ⇒ also zero.
  ⇒ **No `memory.md` / `growth.md` on any new agent** — either would move the count.
- **Agents-tree files** — `find agents -type f | wc -l` at base = **338**; the lane adds **28** ⇒ **366**.
- **Baseline re-stamp** — `python3 scripts/protection/generate_prompt_artifact_baseline.py`
  (venv-free; the generator loads the stdlib-only `prompt_artifacts.py` by file path).
  Verify: `… --check` ⇒ exit 0 clean / 1 drift / 2 no tree.

### B.2 The tool matrices (verbatim, for implementation)

| Agent | `tools.allow` | `tools.deny` | `deny_spawn` | `team_members` | `innate_skills` | `llm_model` | `default_queue` |
|---|---|---|---|---|---|---|---|
| **aegis** | `instance, bash, proc, filesystem, time, self, help, context, shared_meta_kv, knowledge, mcp, chart, question, project, midflight, dynamic-skill, todo` | `write_file, edit_file, terminate_instance, pause_instance, resume_instance, set_instance_tunable, project_create` | `["knowledge"]` | `["hacker","cracker","sweeper","worker"]` | `["dynamic-skill","todo"]` | `agentic` | `system_parallel_queue` |
| **hacker** | `bash, proc, filesystem, time, help, self` | `write_file, edit_file` | — | `[]` | — (none) | `coding` | `system_parallel_queue` |
| **cracker** | `bash, proc, filesystem, time, help, self` | `write_file, edit_file` | — | `[]` | — (none) | `coding` | `system_parallel_queue` |
| **sweeper** | `bash, proc, filesystem, time, help, self` | `write_file, edit_file` | — | `[]` | — (none) | `coding` | `system_parallel_queue` |

**Two invariants that make the pins exact:**

1. `dynamic-skill` and `todo` are in aegis's allow **because** they are its innate skills — the innate
   expansion then adds nothing, so the pinned allow **is** the effective surface. No child declares any
   innate skill, so the expansion is empty there.
2. No agent declares `watchover` (inert key), `mcp_full_access` (E4), the `job` category (the ungated
   dispatch path), or any of the 17 privileged categories (unreachable under a closed allow anyway).

### B.3 The severity taxonomy and the finding record (canonical home: `aegis-strategy`)

Five levels — **critical** (memory corruption reachable from untrusted input, credential/secret
exposure, or code execution) · **high** (memory-safety violation without a write primitive, or a
concrete auth/crypto break) · **medium** (weakening or conditional weakness) · **low**
(defence-in-depth gap, no demonstrated path) · **info** (hygiene/hardening).

**The finding record's field list — ten named fields** (the upstream header says "nine"; the printed
list has ten — see `decisions.md` **D-6**):

`id` · `title` · `severity` · `confidence` (+ basis) · `category` (STRIDE) · `location`
(**`file:line`** + enclosing function) · `description` · `evidence` · `impact` · `fix`.

The canonical list is the one printed in `technical-analysis.md` §DP4 (lines 479–490); this summary is
a pointer, not a substitute, and **T24 pins the field names — not the count** — so the assertion is
correct whichever label the upstream prose carries.

Plus engagement-level state: `open | in-remediation | re-verify-pending | resolved | accepted-risk |
false-positive`, and the `re-verify` outcome.

---

## C. Deviations from `technical-analysis.md` (flagged, not silent)

| # | Deviation | What `technical-analysis.md` says | What this plan does | Why | Severity if wrong |
|---|---|---|---|---|---|
| **D-1** | **T23's pin target.** | §DP7 T23: *"the three `.lib.sh` files carry an **identical** FENCES block"* (line 766); §DP8 P3 gate: *"T23 (identical FENCES blocks) passes"* (line 820). | **T23 pins the FENCES block in the three installer skill `.md` bodies** (byte-identical across all three), **and** asserts the corresponding FENCES comment block in the three `.lib.sh` headers as a secondary consistency check. | Verified against the charter precedent: the normative FENCES bullet list lives in `agents/charter/skills-template/install-mermaid-cli.md` (the skill body); the `.lib.sh` carries only a short header comment. Pinning the `.lib.sh` alone would pin a *comment*, not the contract, and would let the body and the library disagree. | Low — a test-target correction, not a design change. The contract itself is unchanged. |
| **D-2** | **The plan-review gate is inside Phase 0.** | §DP8 P0's gate is *"The checklist exists and every rule has a named target artifact; the architect signs it off before P1"* (line 817). | P0 additionally carries **task 1: route the plan set through architect → council → approver**, and the Phase-0 exit criterion makes the plan-review clearance a **precondition for P1**. | The house lane discipline is plan → councils → implement. The technical-analysis assumed the plan was already through review; the plan set now exists, so the review arc is a Phase-0 task rather than an assumption. | Low — additive. |
| **D-3** | **The vkey first engagement is not a gated phase.** | §E9 / the commission name vkey as the first real case; DP8's phase structure ends at P6 (staging). | No `phase7-plan.md`. The engagement is documented in `plan-overview.md` §Post-lane, with its prerequisites, its trigger (the user's promote ceremony) and its expected shape. | The engagement requires a **live** family ⇒ the user's promote ceremony, which this lane does not perform. It is an operational run against a real target, not a code artifact with a commit to gate. Making it a gated phase would create a phase that cannot start inside the lane. | Low — the commission's first-case requirement is honoured and made explicit rather than dropped. |
| **D-4** | **Two extra test assertions.** | §DP7 enumerates T1–T28. | The lane test additionally asserts (a) each meta's `tools` block **round-trips through the registry's parser** (the fail-open guard — A9) and (b) the baseline has **exactly 177 rows**. | A malformed `tools` block is fail-OPEN (all tools allowed) — on a security agent that is a privilege escalation, not a lint error. And the 177-row count is the artifact that the whole pin ceremony exists to protect; asserting it pre-stage catches a missing/extra prompt file before the stage-time leg. Both are mechanical, and both map to risks already in `plan-overview.md` (R2, R4). | Low — additive tests, no design change. |
| **D-5** | **The finding-record field list is quoted by pointer, not restated.** | §DP4 prints the field list (lines 479–490). | This record names the fields in §B.3 but states explicitly that the canonical list is the one printed in `technical-analysis.md`, and that the implementation follows that list exactly. | A summary that silently re-writes a field list is how a canonical artefact gets two homes — the failure mode guide §2 exists to prevent. | None — the pointer is stricter than a copy. |
| **D-6** | **The finding-record field COUNT is inconsistent upstream.** | §DP4 says *"nine required fields"* in three places (the summary at line 458, the heading at line 477, and the storage layout at line 507), but the printed list at lines 479–490 enumerates **ten** fields (`id, title, severity, confidence, category, location, description, evidence, impact, fix`). T24 repeats the "9" label. | The implementation follows the **printed list** (ten named fields), and **T24 pins the field NAMES rather than the count** — so the assertion is correct whichever label the upstream prose carries. `decisions.md` §B.3 names all ten. | A printed enumeration is more specific than a header count, and a test that pins names is robust to the label. This is a genuine defect in the upstream resolution, **flagged rather than silently changed**. | Low — the field set is unchanged; only the label differs, and no test asserts a bare number. |

**Not deviations (checked and confirmed consistent):** the 28-file manifest and the 338 → 366
arithmetic (the children have **no** `skill-set.yaml`, consistent with DP2's file-invoked mode);
the 161 → 177 baseline arithmetic (only the four prompt files count); the phase cut P0–P6 (unchanged);
the seven-gate verification arc (DP8's P5 sub-gates A–G, plus the scope/privacy gate this plan adds as
**H**); the no-promote boundary.

---

## D. The leader one-liner (verbatim)

`leader` holds the `instance` category but **no** `job` category, so its `team_members` list is its
**only** agent-reach path (A4). One line, in `agents/leader/meta.json`:

```diff
    "team_members": [
      "planner", "developer", "reviewer", "tidier", "approver", "architect",
      "tester", "giter", "devops", "explorer", "wanderer", "kb-writer",
-     "doc-writer", "maintenancer", "designer", "gaia"
+     "doc-writer", "maintenancer", "designer", "gaia", "aegis"
    ],
```

`meta.json` is **not** in the prompt-artifact baseline's artifact set ⇒ this edit contributes **zero**
baseline rows and does not move 161 → 177 (B1).

**`ari` needs no change at all** — `job_create` takes a free-form `agent_id` with **no team-membership
gate** (A3), so `ari` can dispatch `agent_id="aegis"` the moment the agent registers.

---

## E. Q1–Q4 — carried OPEN (Phase-0 entry blockers for the implementation arc)

All four are **unresolved** and must be ruled by the architect in Phase 0 (task 2) **before Phase 1
authors a tool matrix**. Each answer below states what changes in the plan under it.

### Q1 — May `aegis` hold `"chart"` (which implies the `charter` spawn member)?

**The problem.** The commission assigns *"report charts"* to `worker`, but `worker` holds no `chart`
category and the commission forbids changing `worker` (D4). A category in the caller's `tools.allow`
silently adds the category's backing agent to the effective team, so granting `chart` also grants the
**implied `charter` spawn right** (A1) — widening aegis's effective team beyond the commission's
stated roster `[hacker, cracker, sweeper, worker]`.

**Recommendation: (a) accept the deviation.** The chart artifact is otherwise unproducible, the
"implied member" mechanism is the same shape the house already uses for `chart`/`charter`, and the risk
is nil (charter renders a diagram; it has no offensive capability).

**If (a) is ruled:** nothing changes. The plan records the widened roster explicitly — aegis's
effective team is `[hacker, cracker, sweeper, worker]` **plus the implied `charter`** — so the next
reader is not surprised, and T1/T3/T8 pin it.

**If (b) drop charts from v1:** remove `"chart"` from aegis's `tools.allow`; the implied `charter`
member disappears; update T1 (allow set), T8 (privileged-category intersection is unaffected) and
aegis's `tools_note.md` chart row; the findings report stays text + JSON, and the commission's
"report charts" task is formally reduced in scope (disclosed in the handoff report).

**If (c) authorise a worker change:** explicitly out of scope for this lane (the commission forbids
it) — it would become a separate commission, and the blast radius would be the shared lane used by
every other caller.

### Q2 — Where do the findings live?

**The problem.** Off-repo storage (`$HOME/.local/state/aegis/engagements/<id>/`) keeps findings out
of git, but they are then not PR-reviewable and need filesystem access to the host. In-repo storage
(`.agents/shared/security/<id>/`) is diffable and reviewable, but it is **committed** ⇒ pushable to
origin, which materially weakens the commission's *"findings private"* requirement — and it needs a
`.gitignore` change, which is **outside** the lane's `agents/` + `tests/` scope (E2).

**Recommendation: (a) off-repo** — zero scope impact, and it keeps findings at the same privacy class
as the target source itself. The mitigation for the reviewability cost: the **severity summary travels
inside the hand-off message to `leader`**, so the decision-maker is never blocked by file access.

**If (a):** nothing changes in the plan; the `aegis-strategy` engagement-folder paths and aegis's
`workflow.md` name the off-repo root (as an operational path, which guide §3 exempts).

**If (b) in-repo:** the lane must be **re-scoped** — a `.gitignore` edit is authorised explicitly, the
git-history exposure is accepted in writing, `aegis-strategy` and aegis's `workflow.md` change their
paths, and the §3 closure-grep exemption list changes. Note this also pulls a non-`agents/`,
non-`tests/` file into the lane, which weakens the "the lane diff touches only `agents/**` and
`tests/**`" proof in Gate H.

### Q3 — Is the v1 CVE-lookup surface acceptable?

**The problem.** There is **no web-search tool category** in the registry (D2/A7). The recommendation
is `mcp` (the `webfetch` builtin — but MCP servers are enabled per **deployment**, so the capability
is conditional) plus `bash curl` (always available wherever `bash` is granted), on **aegis only**, with
CVE context passed to the children in their dispatch prompts. A real CVE-database API would be a **new
category** ⇒ the 3-step registration seam + `tool-categories.json` regeneration ⇒ **the F-1 trap
class** (B3) ⇒ out of scope for this lane.

**Recommendation: (a) accept for v1.** The prompt treats web-fetch as conditional and `curl` as the
universal fallback; the children need no lookup of their own.

**If (a):** nothing changes.

**If (b) commission a category:** it becomes **its own lane** (3-step registration seam + artifact
regeneration + the F-1 proof leg). This lane withdraws the `mcp` grant from aegis, drops CVE context
from the dispatch prompts, and the CVE-lookup capability is deferred to the follow-up lane.

### Q4 — May the family omit a `watchover` meta block?

**The problem.** The per-agent `watchover` key is **inert**: `AgentMetadata` has no such field, its
`model_config` is `extra="ignore"` (so the key is silently dropped rather than validated), and **no
daemon code reads one** (D7). Copying sketcher's block would create a **false-confidence knob** — an
operator would believe the family has a supervision timeout when it has none.

**Recommendation: (a) accept** — omit it from all four metas. Supervision comes from the family's own
END TURN contract + the fan-in ladder (max 1 re-dispatch) + `proc` process tracking, with the
per-instance `POST /api/instances/{id}/watchover` API as the operator lever (D7/DP5).

**If (a):** nothing changes.

**If (b) require an agent-meta supervision knob:** that is a **daemon change** — out of this lane's
scope by construction. The four metas stay as designed and the knob becomes a follow-up lane; the
supervision gap is disclosed in the handoff report.

---

## F. Review trail

*(populated by Phase 0 — task 1)*

| Stage | Verdict | Findings | Folded / routed | Date |
|---|---|---|---|---|
| Architect | — | — | — | — |
| Council | — | — | — | — |
| Approver (iteration) | — | — | — | — |

---

## G. Standing disclosures (carried into the implementation arc)

1. **The write fence is a speed bump, not a boundary.** `bash` is an unconstrained write path on every
   lane; the "read-only on target codebases" guarantee is **doctrinal**. The limit must be stated
   plainly in the prompts, the report template and the handoff report — never implied away.
2. **The seal does not cover the new agents' files.** `SEALED_SHA_BASELINES` covers 35 pre-existing
   entries; the aegis family's prompt files are covered by the **baseline** (161 → 177), not by the
   seal. The seal tripwire will not catch drift in the aegis family.
3. **The `knowledge` trim is partial.** `deny_spawn` closes the direct spawn path, but `explore()` and
   `experience()` spawn instances internally outside the team gate. Use `knowledge` sparingly; no aegis
   prompt names `explorer`/`kb-writer` as a fallback (T19).
4. **The `mcp` grant is deployment-dependent.** Aegis's CVE capability varies by deployment; the
   prompt must treat web-fetch as conditional.
5. **`explore()` spawns one instance per call** — a KB-heavy engagement creates instances the family
   cannot address. Bounded by the "use sparingly" guidance above.
6. **The implied `charter` member** (if Q1 = (a)) must be recorded wherever aegis's roster is stated.
