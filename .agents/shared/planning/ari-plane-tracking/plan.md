# Edit Spec: Plane as the Long-Term Project-Tracking Surface (agents/ari/)

Date: 2026-11-06
Author: plan-creation worker (planner-light, verify-don't-redesign)
Status: Ready for Review
Commission: ari-plane-tracking — prompt-only doctrine bake; design FIXED by
user directive (Discord 2026-11-04). This spec is the artifact of record;
the developer executes the edit in a LATER commission. This commission does
NOT touch agents/ari/**.

Workspace: /home/nea/ensemble-src-wt-ari-plane (branch
feature/ari-plane-tracking @ 17413bca5, clean, zero commits).
Every anchor below was verified against this worktree's bytes.

---

## 0. Verified Ground Truth (census + anchors)

### 0.1 Seal/baseline anchors (verified byte-exact at 17413bca5)

| Artifact | Anchor | Current sha256 (file bytes) |
|---|---|---|
| agents/ari/rule.md | seal map tests/test_chart_image_delivery_e2e.py **:1504**; baseline agents/.prompt-artifacts.sha256 **:30** | `4b45329547dcad4e1bc4506b0f53266dda87f57ff7eaf6f7816971f38ed8fa8a` |
| agents/ari/workflow.md | seal map tests/test_chart_image_delivery_e2e.py **:1451**; baseline **:33** | `303c78e325b123587303461ba6c2d556569a8171abdc66303e7a9f32ede5967d` |
| agents/ari/tools_note.md | baseline **:32** only (unsealed) | `cbd49c0a4d0f7edbc65bb8b2390c2a546316e7db3676da25ee0a52e39a039e38` |

Seal verification method (test_chart_image_delivery_e2e.py:1605):
`hashlib.sha256(abs_path.read_bytes()).hexdigest()` — plain file-bytes
sha256, so `sha256sum agents/ari/<file>` after the edit yields the new pin
value directly.

Baseline file format: sha256sum-compatible (`<sha>  <path>`, 177 lines,
sorted). Regen script loads the versioned set single-sourced from
daemon/tools/prompt_artifacts.py by file path (never via the package).

### 0.2 Forbidden-token census, agents/ari/workflow.md (grounded acceptance)

Enforcement tuple (tests/unit/test_instance_ops_prompt_layer.py:26-35):
`meta.json, tools.allow, tools.deny, daemon/, _tool_registry,
skill-set.yaml, agent_id=, seed_all, innate_skills, default_agent_versions`.

- Enforcement census: exactly **5 hits, all `agent_id=`** at lines
  :89, :113, :144, :256, :259 (the pre-existing spawn examples pinned by
  `BASELINE_HITS["agents/ari/workflow.md"] = 5`). New prose must add ZERO.
- Writing-guide closure census (broader prose token set incl. `.md`,
  `agents/`): **1 operational survivor** at :473
  (`agents/charter/skills-template/install-mermaid-cli.md` — a runtime
  read target, justified operational per writing-guide :108 scope note;
  not in the enforcement tuple). Untouched by this edit.
- agents/ari/rule.md carries 2 pre-existing `agent_id=` hits (:62, :66,
  inside the workflow-sequence table) — rule.md is not under the
  forbidden-layer count pin, but new prose stays token-clean anyway.

### 0.3 Structural facts verified

- agents/ari/rule.md (270 lines): `### Plane & Project-Management
  Dispatch` :193-215 under `## Must`; bullets = Trigger / Topology /
  Scope split / Environment discipline; last bullet text ends :213; `---`
  separator :215; `## Reporting Anomalies` :266-270 is LAST and
  byte-pinned. Board mentions in rule.md: none.
- agents/ari/workflow.md (491 lines): Mode 2 Steps :71-108; Triage
  Decision Matrix :156-168 (row :162 routes Plane ops to project-manager,
  "Bypasses the mode ladder"); `## Watch Job Discipline` :250-279
  (Mission-vocabulary paragraph :270-279); `## Session Open & Close — the
  Board Habit` :283-346; Patrol subsection :330-343 (zero-hit tested span);
  NO project-manager mentions anywhere.
- agents/ari/tools_note.md (903 lines): Board reference :653-729 +
  board_link :891-903; zero plane/PM prose (by design).
- PM degradation idiom (in-repo precedent, agents/project-manager/
  tools_note.md:33): "When Plane tools fail (timeout, auth, network) or
  return empty, I proceed with planning docs and project history only.
  I mark the data gap explicitly — never fabricate Plane numbers."
- Section-name precedent for natural-sentence refs: workflow.md:285
  "(See Board — Durable Cross-Instance Work Items for the tool reference)".
- Version hygiene precedent: commit 24a45ef88 (ari prose-only review
  round) touched ONLY rule.md + soul.md + workflow.md — no meta.json
  version bump. Same here: prose-only, NO meta.json edit.

---

## Part 1 — Placement Ruling

**Ruling: caller leaning (a) CONFIRMED, (b) CONFIRMED with the
new-subsection home adjudicated, (c) CONFIRMED.**

### 1.1 agents/ari/rule.md — CONFIRMED, extend the existing section

The POLICY lives in `### Plane & Project-Management Dispatch` (:193-215):
append THREE new guidelines-level bullets after the Environment
discipline bullet (text ends :213), i.e. insert between :213 and the
blank line :214. Appending keeps every existing byte at its current line
offset (byte-minimal diff, 24a45ef88 precedent) and preserves the
section's existing Trigger/Topology/Scope/Environment reading order.

Why here (one-canonical-home + file-roles, writing-guide :57-64/:66-80):
rule.md owns "hard constraints / doctrine" — the WHEN-does-tracking-apply
tiers, link-don't-duplicate, and the degradation constraint are exactly
that. The section is already the canonical home of PM-dispatch doctrine
(survey-confirmed); adding the tracking policy here creates ONE home and
avoids a competing section. Guidelines bullets (like the existing four),
NOT a new cardinal: the cardinal budget stays ≤7 and tracking is
judgment-tiered, not non-negotiable.

### 1.2 agents/ari/workflow.md — CONFIRMED policy home is workflow, but as a NEW top-level subsection (not Mode 2 Steps, not Board Habit)

The BEHAVIORAL RHYTHM (fold PM create-or-link into the existing dispatch
flow; state update at start/terminal milestones; board-only fallback)
gets a NEW top-level section inserted between `## Watch Job Discipline`
(end :279; `---` separator :281) and `## Session Open & Close — the
Board Habit` (:283). Insert AFTER the `---` at :281, BEFORE the blank
line :282.

Why this home — the two rejected alternatives:

- NOT Mode 2 Steps extension: Mode 2 is contractually "Project Work
  Delegation (→ leader)". The Plane create-or-link is a DIRECT dispatch
  to project-manager — workflow.md:162 already rules Plane ops "Bypasses
  the mode ladder — routed per Default Delegation Targets". Folding the
  rhythm into Mode 2 Steps would (i) contradict the bypass row, (ii)
  bloat a 7-step numbered contract with a conditional branch, (iii) blur
  the →leader arrow. The rhythm instead ATTACHES to the commission
  lifecycle, which is mode-independent.
- NOT inside the Board Habit section: the Board Habit owns session
  bookending (open: read full board; close: handoff notes) — a per-SESSION
  contract. Plane tracking is a per-COMMISSION act (dispatch time +
  terminal milestones). Housing it there mis-scopes the trigger and
  dilutes the session bookend.
- The chosen slot reads as a deliberate sibling: session bookends = the
  Board Habit; commission bookends = the Plane Ledger Habit. It sits
  directly after Watch Job Discipline (whose completion handling the
  milestone update rides) and before the Board Habit (which the
  link-don't-duplicate principle complements).
- Safety: the insertion point is OUTSIDE the zero-hit tested span
  ("### Patrol loop — when an alarm wakes me" → "## [JOB_EVENT]
  Notification Parsing") and the Mission-vocabulary paragraph :270-279
  (presence-pinned by tests/unit/tools/test_mission_watch_prompts.py) is
  untouched.

### 1.3 agents/ari/tools_note.md — CONFIRMED UNCHANGED

Zero plane/PM prose is correct by design (PM owns the plane tool surface;
tools_note.md is tool-by-tool reference for tools Ari holds). The new
doctrine names no tool signatures. Baseline row :32 stays identical.

### 1.4 Not touched (explicit)

- agents/ari/soul.md (out of scope), agents/ari/meta.json (no version
  bump — prose-only precedent 24a45ef88), triage matrix row :162 (not
  duplicated — see Risk R2), Mission-vocabulary paragraph :270-279,
  pre-warm section :468+, duty block :266-270 in rule.md, and ALL
  agents/project-manager/** (read-only degradation-idiom reference only).

---

## Part 2 — Final Wording (paste-ready)

Formatting rules honored: ~78-col wrap matching house style; same-agent
`See <Section Name>` refs only; PM-degradation idiom ("I proceed ... only;
I note the gap"); availability phrased as runtime-honest static text (no
pretend checks); workspace mechanics NOT restated (design constraint 6);
no forbidden tokens (checked against the §0.2 tuple).

### 2.1 rule.md — insert AFTER line 213 (before blank :214)

Exact text (3 bullets; blank line between bullets, matching the
section's existing bullet-block style):

```text
- **Long-term tracking tiers** — Plane is the long-term
  project-tracking surface, and whether I engage it scales with the
  work. **Non-trivial** project work — it spans sessions, carries
  milestones or gates, or the user will revisit it — gets a Plane work
  item created-or-linked by **`project-manager`** at dispatch time, its
  state updated at the start and terminal milestones. **Medium**
  one-shot tasks are my judgment call; I lean board-only. **Tiny**,
  read-only, exploration, and chat work skip Plane entirely. The
  create-or-link rides the commission dispatch I am already sending —
  never a separate tracking errand (See Commission Open & Close — the
  Plane Ledger Habit).

- **Link, don't duplicate** — the board stays the operational surface
  (session handoffs, self-alarm evidence, patrol findings, reminders I
  owe the user); Plane is the human-facing project ledger. An item
  tracked in Plane earns at most a board pointer — never a copy.

- **Degradation fallback** — when Plane is unavailable (tools not
  wired, a dead credential, a refused connection, an environment
  without Plane), I proceed board-only. I note the gap explicitly and
  move on — I never block or retry-loop work on Plane being down.
```

Wording notes (adjudications baked in): "a refused connection" carries
the design's "resolver refusal" WITHOUT naming system mechanics; the
tiers sentence leads with the conditional ("whether I engage it scales
with the work") so the prose cannot read as always-dispatch; the
create-or-link errand sentence makes "fold into existing commission
dispatch, no separate ceremony" explicit and points to the workflow
section for the mechanics (one-canonical-home: policy here, mechanics
there, each linking the other).

### 2.2 workflow.md — insert AFTER line 281 (`---`), BEFORE blank :282

Exact text (new top-level section, `##` level to sit between its two
`##` neighbors; the internal steps use the house `raw` fence like Mode 2
Steps):

````text
## Commission Open & Close — the Plane Ledger Habit

Non-trivial project commissions carry a long-term Plane ledger entry
alongside the board (See Plane & Project-Management Dispatch for the
tiers). The ledger work rides the dispatch flow I already run — it is
never a separate tracking ceremony:

```raw
1. At dispatch — alongside the commission dispatch itself, I dispatch
   project-manager to create-or-link the Plane work item. I hold no
   Plane tools myself; project-manager is the only Plane writer.

2. At start — the entry carries the commission's opening state.

3. At terminal milestones — a milestone lands or the commission
   terminates → ONE dispatch to project-manager updates the item's
   state. It rides the [JOB_EVENT] completion handling I already
   perform — never a separate vigil.

4. When Plane is unavailable — I proceed board-only and note the gap
   on the commission's board item. I never block or retry-loop work on
   Plane being down (See Plane & Project-Management Dispatch).
```

One Plane item per commission, one pointer on the board item — the board
stays the operational surface; Plane is the human-facing ledger. I do
not mirror board content into Plane or the reverse.
````

Wording notes: the section NAME is unique in ari's assembled surface
(grepped: no existing "Commission Open" / "Plane Ledger" hits in
agents/), so both `See` refs resolve unambiguously; step 3 says "[JOB_EVENT]
completion handling" instead of any "watch job"-shaped phrase to stay
clear of the mission-watch token family (tests/unit/tools/
test_mission_watch_prompts.py:43 pins `watch_jobs?\b` as forbidden — the
underscore form; my phrasing avoids the phrase entirely); step 1 states
the PM-dispatch-only constraint operationally ("I hold no Plane tools
myself"), not as system reasoning.

### 2.3 Token-cleanliness acceptance check (run after edit)

```bash
# enforcement tuple — must print the SAME 5 pre-existing agent_id= lines
grep -n 'agent_id=\|meta\.json\|tools\.allow\|tools\.deny\|daemon/\|_tool_registry\|skill-set\.yaml\|seed_all\|innate_skills\|default_agent_versions' agents/ari/workflow.md
# writing-guide closure — must print ONLY the pre-existing :473 operational path
grep -n '\.md\|agents/' agents/ari/workflow.md | grep -v '^\s*#'
# same check on rule.md — must print ONLY its 2 pre-existing table hits
grep -n 'agent_id=\|meta\.json\|tools\.allow\|\.md' agents/ari/rule.md
# section refs resolve — both headings exist
grep -n '## Commission Open & Close — the Plane Ledger Habit' agents/ari/workflow.md
grep -n '### Plane & Project-Management Dispatch' agents/ari/rule.md
# duty block still last + byte-identical
grep -n '^## ' agents/ari/rule.md | tail -2   # "Reporting Anomalies" must be last
```

---

## Part 3 — Pin Ceremony List (developer executes one-shot, in order)

Order matters: edit → compute shas → seal re-pins → baseline regen →
guard runs. The seal test is RED until BOTH re-pins land; the baseline
--check is RED until the regen lands.

### 3.1 Seal re-pins — tests/test_chart_image_delivery_e2e.py

Compute the new shas AFTER the Part-2 edits:

```bash
sha256sum agents/ari/rule.md agents/ari/workflow.md
```

Then edit the seal map `SEALED_SHA_BASELINES` (file
tests/test_chart_image_delivery_e2e.py) at BOTH rows:

- **Row :1451** `"agents/ari/workflow.md": "303c78e3…67d"` — replace the
  value with the new workflow.md sha and insert above the entry, in the
  file's established comment style:

  ```python
      # agents/ari/workflow.md re-pinned 2026-11-06 (ari-plane-tracking):
      # long-term Plane tracking doctrine — 3 policy bullets appended to
      # the Plane & Project-Management Dispatch section's sibling section
      # in workflow.md ("Commission Open & Close — the Plane Ledger Habit").
      # Prior value 303c78e325b123587303461ba6c2d556569a8171abdc66303e7a9f32ede5967d.
      # Prior re-pin note: nest-anomaly-report phase 5 / C8 (base-proven drift repair).
  ```

- **Row :1504** `"agents/ari/rule.md": "4b453295…a8a"` — replace the
  value with the new rule.md sha and insert above the entry:

  ```python
      # agents/ari/rule.md re-pinned 2026-11-06 (ari-plane-tracking):
      # long-term Plane tracking doctrine — 3 policy bullets appended to
      # the Plane & Project-Management Dispatch section (after the
      # Environment-discipline bullet).
      # Prior value 4b45329547dcad4e1bc4506b0f53266dda87f57ff7eaf6f7816971f38ed8fa8a.
      # Prior re-pin note: nest-anomaly-report phase 5 / C8 (base-proven drift repair).
  ```

### 3.2 Baseline regeneration — agents/.prompt-artifacts.sha256

Run from the WORKTREE ROOT (the script defaults its tree to its own repo
root, which in the worktree is the worktree):

```bash
python3 scripts/protection/generate_prompt_artifact_baseline.py
python3 scripts/protection/generate_prompt_artifact_baseline.py --check   # exit 0
```

Expected: still **177 lines**; ONLY the two ari rows move (:30 rule.md,
:33 workflow.md get the new shas; :32 tools_note.md byte-identical;
sorted order preserved; sha256sum -c compatible). Confirm:

```bash
wc -l agents/.prompt-artifacts.sha256                    # 177
grep -n 'agents/ari/' agents/.prompt-artifacts.sha256    # rows :30-:33, 2 changed 2 same
cd agents && sha256sum -c .prompt-artifacts.sha256 >/dev/null && echo BASELINE-OK
```

### 3.3 Expected guard-test outcomes (named tests)

| Test | Expected after edit + re-pins | Why |
|---|---|---|
| tests/test_chart_image_delivery_e2e.py::TestPhaseDDoesNotModifySealedArtifacts::test_phase_d_does_not_modify_sealed_artifacts | GREEN (RED before re-pins — expected intermediate) | both rows re-pinned with prior-sha comments |
| tests/unit/test_instance_ops_prompt_layer.py::TestForbiddenLayer::test_ari_workflow_hits_do_not_exceed_the_preexisting_baseline | GREEN, still exactly 5 hits, all `agent_id=` | new prose token-clean (§0.2) |
| tests/unit/test_instance_ops_prompt_layer.py::TestForbiddenLayer::test_the_new_ari_patrol_section_is_clean | GREEN (span unchanged, sliced by index) | insertion is before the tested span |
| tests/unit/test_anomaly_report_duty_prompts.py (duty pack — placement "LAST top-level section", byte-identity, count legs) | GREEN | rule.md edit is mid-`## Must`; duty block :266-270 untouched, still last |
| tests/unit/tools/test_mission_watch_prompts.py::TestNoWatchJobTokens::test_all_nine_files_carry_zero_watch_job_tokens | GREEN | no `watch_jobs?\b` token added; "MISSIONS, not receipts" paragraph :270-279 untouched (presence legs stay green) |
| tests/unit/test_project_manager_agent.py (PM meta/verb checks incl. Plane surface drift alarm) | GREEN | NO agents/project-manager/** file touched |
| test_charter_render_capture.py:1188 pre-warm cross-ref leg + the pre-warm section prose | GREEN | workflow.md :468+ untouched |

Command (from worktree root):

```bash
python3 -m pytest tests/unit/test_instance_ops_prompt_layer.py \
  tests/unit/test_anomaly_report_duty_prompts.py \
  tests/unit/test_project_manager_agent.py \
  tests/unit/tools/test_mission_watch_prompts.py \
  tests/test_chart_image_delivery_e2e.py -q
```

---

## Part 4 — Right-Sized Gate Plan (text-only, 2-file prose change)

- **Reviewer: ONE conformance pass** (no council). Scope: (i) token-clean
  per writing-guide §91-124 + §4 operational-statement rule; (ii) both
  `See` refs resolve to real headings; (iii) one-canonical-home holds
  (policy/rule ↔ mechanics/workflow, no verbatim duplication, no false
  single-source claim); (iv) conditionality honest — the tier bullet
  cannot read as always-dispatch (Risk R1); (v) guideline-vs-cardinal
  placement correct; (vi) PM files untouched (diff scope check).
- **Tester: prompt-surface pack set ONLY** (no Tier-2 whole-tree, no
  pixel/e2e legs — nothing outside agents/ari/ + 1 test file changes):
  run the §3.3 command + the §2.3/§3.2 acceptance greps. PASS = all named
  tests green + baseline 177/177 + seal greens + census counts unchanged
  (5 enforcement hits; 1 operational `.md` survivor).
- **NO heavyweight councils, NO staged-release ceremony work in this
  lane**: the change rides the normal release train; its stage-time
  PROMPT-BASELINE check is covered by 3.2.
- Commit hygiene (developer's commit, later lane): single commit
  containing agents/ari/rule.md, agents/ari/workflow.md,
  tests/test_chart_image_delivery_e2e.py,
  agents/.prompt-artifacts.sha256 — message shape
  `docs(ari): bake long-term Plane tracking doctrine into ari prompts`
  (or lane convention). No pushes in THIS planning commission.

---

## Part 5 — Risks / Edge Cases (≤5)

- **R1 Over-trigger reading** ("Ari must always dispatch PM"): Mitigated
  in wording — the tier bullet opens with "whether I engage it scales
  with the work" and names all three tiers; reviewer leg (iv) asserts the
  conditional survives. If a reviewer reads always-dispatch, tighten the
  lead clause, not the tiers.
- **R2 Triage-matrix duplication**: workflow.md:162 already routes Plane
  ops ("Bypasses the mode ladder"). The new section must NOT restate
  routing — it regulates tracking lifecycle, not dispatch routing. The
  wording above mentions project-manager only as the Plane writer, never
  as a routing rule. Reviewer leg (vi/v) double-checks no second routing
  statement was introduced.
- **R3 Duty-block placement break**: if the developer accidentally
  appends after `## Reporting Anomalies` (it is the file's LAST section
  and byte-pinned), the duty pack's placement + byte-identity legs go
  RED. The spec pins insertion at :213-214 (mid-`## Must`); the §2.3
  "still last" grep is the pre-commit check.
- **R4 Seal/baseline sequencing**: seal test is RED between edit and
  re-pins, baseline --check RED until regen — expected intermediate
  states, not failures. Forgetting the `# Prior value <sha>.` comment
  breaks the re-pin audit convention (every existing re-pin row carries
  one); the ceremony above includes both comments verbatim.
- **R5 Token drift in future edits**: the patrol section's zero-hit test
  and the `watch_jobs?\b` token pin are both proximity hazards for any
  later editor of this region. The spec's step-3 phrasing ("[JOB_EVENT]
  completion handling") deliberately avoids the watch-job phrase family;
  any rewording must re-run the §2.3 census (5 enforcement hits, 1
  operational survivor, unchanged).

## Open Questions

None — design fixed by directive; all anchors verified in-worktree. The
developer's commit message shape is the only free variable (left to lane
convention).
