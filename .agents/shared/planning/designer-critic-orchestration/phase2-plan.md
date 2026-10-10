# Phase 2 — Critic Agent Build

**Phase owner:** developer instance (same worktree, same branch)
**Branch:** `feature/designer-critic-orchestration`
**Worktree:** `/home/nea/ensemble-src-wt-designer-critic-orchestration` (hard boundary)
**Evidence base:** `research-findings.md` §5 (critic infra requirements) + §6 (critic read surface + verdict precedents) + `architecture-recommendation.md` §2 D3 ruling + §2 D6 ruling (binding)
**Adjudication source of truth:** `architecture-recommendation.md` — phase 2 consumes §2 D3 (amendments) and §2 D6 (canonical form) verbatim.
**Pre-condition artifact:** `.agents/shared/planning/designer-critic-orchestration/critic-verdict-schema.md` (phase 1 T11 — frozen pin; this phase cites it by section reference, not by filename in agent prose)
**Decision keys consumed:** **D3** (verdict contract + 4 ratified amendments — architecture-recommendation.md §2 :43-52), **D6** (canonical `["read_file","image","design"]` + `image_save` DENIED for read-only leaf purity — architecture-recommendation.md §2 :74-104)

---

## 1. Objective

Create the new `agents/critic/` agent directory per `docs/agent-prompt-writing-guide.md` (342 lines, mandatory convention), with leaf posture, vision model lane (`architecture-recommendation.md` §2 :101), **canonical D6 form** with `image_save` explicitly denied for read-only leaf purity, hardened `tools.deny`, and a verdict-protocol Cardinal (per D3 + the 4 ratified amendments). The post-phase-2 critic is dispatchable by designer as a child; the phase 3 wiring introduces the actual dispatch.

**Why critic is built BEFORE the pipeline is wired.** The shape of the verdict (per D3) is the input contract for phase 3's orchestration; ratifying the critic's read surface first prevents phase-3 backtracking. The pipeline wiring (phase 3) will read phase 2's outputs as its inputs, not the other way around.

**Resolved at this phase.**

- Q3.1–Q3.4 (D3 amendments ratified)
- Q6.2 (vision pin)
- Q6.3 (image comparator EXISTS — `compare_tools.py:1217`; exposed via `design` allow)
- Q6.4 (`filesystem` category NOT used; `read_file` bare is enough)

**Deferred at this phase.**

- Q6.1 (view-views for critic) — DEFERRED. View-views is NOT in the canonical D6 form. SC-15 checks `design` allow entry (not view-views). Decision recorded in `plan-overview.md` R5 + A8.

---

## 2. Decisions

| ID | Decision | Rationale | Rejected alternatives |
|----|----------|-----------|------------------------|
| D6 (canonical, replace both prior) | Critic's `agents/critic/meta.json` `tools.allow` = **`["read_file", "image", "design"]`** (bare names, not category keys). `tools.deny` MUST include `image_save` (architect intent = read-only leaf purity per adjudication rule on finding 9; `image` category expands to FOUR tools incl. `image_save` at `daemon/tools/image_tools.py:482/:669/:841/:933`; granting `image_save` is a write capability incompatible with a leaf reviewer). Other deny entries: `["bash", "proc", "instance", "service", "midflight", "shared_meta_kv", "infra", "mcp"]`. | Category form is unsafe (`filesystem` expands to `write_file`/`edit_file` per `daemon/tools/instance.py:475-482`; `filesystem.py:880/:1233` — write leak). Bare-name form `["read_file","image","design"]` resolves to the exact 4 + 1 = 5-tool set the architect's intended: `read_file` + `image_get` + `image_list` + `explain_image` + `compare_images`. The `image_save` denial is critical for read-only leaf purity. The `mcp` deny is mandatory for a leaf (the mcp allowlist check fires only when `"mcp" in allow`, `instance.py:296-334`). | Category form (`["filesystem","image","dynamic-skill","todo"]`) — gives write leak. Bare-name list WITHOUT `design` — misses the comparator (Q6.3 = exists). |
| D6 (innate skills + allow integration) | `innate_skills: ["dynamic-skill","todo"]`. These auto-grant the matching tools via `INNATE_SKILL_TOOL_CATEGORIES` (`instance.py:161-168`) and are intentionally absent from `tools.allow`. `skill_injection: true`. | Mirrors sketcher pattern; auto-grant mechanism is the canonical path. | List innate-skill tools in `tools.allow` — duplicates; risks drift. |
| D6 (model) | `llm_model: "vision"` per `architecture-recommendation.md` §2 :101 (Q6.2 ratified). | Symmetric + allowlisted (`config.yaml:94`); matches sketcher + designer precedent. | Different model per agent — splits telemetry; vision pin is needed for pixel-level verdict. |
| D6 (spawn posture) | `team_members: []` (DECLARED). Note that the canonical D6 form auto-extends EFFECTIVE team membership with `image-comparator` (`_auth.py:155-161` — auto-extension); the `tools_note.md` MUST document that `team_members: []` is the DECLARED list while the EFFECTIVE list is non-empty (`image-comparator` for `compare_images` spawned via the `design` allow). | The auto-extension is a daemon mechanism, not a meta.json edit; the doc note is the only place it surfaces. | Confusing DECLARED with EFFECTIVE — non-leaf behavior question. |
| D6 (posture / `watchover`) | DROPPED `watchover.timeout_seconds` row from critic `meta.json` per `architecture-recommendation.md` §2 :102 + §5.4 (inert on a non-watcher; the watchover meta section configures the WATCHER agent's dialog, not a kill-timer on the carrying agent — `graph.py:10751-10772`). | Dead config on a leaf is drift bait. If kept for sketcher symmetry, MUST be documented as inert. | Keep with sketcher's 90s value — inert + drift bait. |
| D6 (default_queue) | No entry. Default `system_queue` is correct — critic is one-per-page, not parallel. | No fan-out from critic; default queue is the right binding. | `default_queue: "system_parallel_queue"` (sketcher's value) — wrong; critic has no parallel scope. |
| D3 (verdict shape — canonical, frozen pin) | Critic's `workflow.md` Review block quotes the canonical shape + 4 amendments verbatim FROM `critic-verdict-schema.md` planning-dir doc (phase 1 T11). The Review block is the canonical home inside critic's prompt; the planning-dir doc is the frozen pin (frozen-pin declaration in the doc itself). Review block cites the schema by section reference (`## Verdict block` + the four `## Amendment N` sections), NOT by filename. | Architect's instruction (D3 ruling): pin the schema BEFORE critic is built. Future-proofs agent prose so conformance tests pin by section. | Inline the shape in agent prose — drift bait; the planning-dir doc keeps the single source of truth. |
| D3 amendment 1 (enforced via Cardinal #1) | Critic's verdict carries a third tier `[BRIEF-LEVEL]` for defects traced to brief inputs, not the artifact. Prevents designer from burning artifact-level iterations on a brief-level defect. | Resolves Q3.3. | Skip — confuses "artifact broken" with "brief broken"; iterations on the wrong surface. |
| D3 amendment 2 (enforced via Cardinal #1 + designer's parse-discipline Cardinal) | **Malformed-verdict discipline:** if the verdict block fails to parse (regex miss), designer re-dispatches CRITIC (not sketcher) with `notes: prev_attempt_unparseable`. A degraded re-dispatch to sketcher regenerates the same input and burns rounds on a parse failure, not a quality failure. | Resolves the parse-fail footgun. | Re-dispatch sketcher — burns the D4 round budget on a non-quality failure. |
| D3 amendment 3 (enforced via Cardinal #1 + regex in workflow Review block) | Parse rule: regex `^verdict:\s*(pass|needs-revision)\s*$` anchored on the verdict line. Substring scans mis-fire when critic prose quotes "verdict:" in evidence lines. | Resolves the substring-vs-anchor parse footgun. | Substring scan — false positives on quoted evidence. |
| D3 amendment 4 (enforced via Cardinal #1) | `pinned_spec_sha` field is in the verdict block. `compare_images` requires `pinned_spec_sha` to make comparison verdicts binding rather than advisory (`daemon/tools/compare_tools.py:1265-1269`); without it, critic's compare path is permanently advisory. **Designer passes the spec SHA in the dispatch envelope** (phase 3 T2 wires this). | Resolves Q6.3 binding. | Compare verdicts advisory only — defeats the comparator tool's purpose. |
| Cardinal #1 (verbatim text, architect-binding) | "I emit a verdict block on every review. The block follows the shape defined in My Workflow — the Review block — which quotes the planning-dir doc verbatim. Three disciplines: (1) `^verdict:\s*(pass\|needs-revision)\s*$` regex anchor — substring scans fail; (2) malformed parse → designer re-dispatches me with `notes: prev_attempt_unparseable`, NOT sketcher — quality vs parse failure are different; (3) `pinned_spec_sha` is mandatory; comparator verdicts without it are advisory. Severity tiers are `[CRITICAL]` / `[ADVISORY]` / `[BRIEF-LEVEL]`. The leader/caller is bound by my verdict and treats `pass` as accept and `needs-revision` as dispatch-fresh-iteration instructions." | Mirrors sketcher's Cardinal #3 verbatim-codes discipline. | Paraphrase — breaks the conformance test. |
| Cardinal #2–#4 | Standard leaf posture (no app-code edits; one-shot per dispatch; leaf — no children). | Mirrors sketcher's posture for a leaf. | Different cardinals per agent — drift bait. |

**Cardinal #5–#7 reserved for the project's standard compliance-with-project-conventions slots (analogue of designer's Cardinal #5/6/7).**

**Verdict block shape (frozen at `critic-verdict-schema.md` per phase-1 T11; canonically cited by section name from `agents/critic/workflow.md` Review block — this is the canonical home inside the agent prompt):**

```
## Review — <page> — PASS | NEEDS-REVISION (N critical, K advisory)

verdict: pass | needs-revision
critical_findings:
  - [CRITICAL] <finding-1>
  - [CRITICAL] <finding-2>   # omitted on PASS
advisory_findings:
  - [ADVISORY] <finding-1>
  - [ADVISORY] <finding-2>   # omitted on PASS
brief_findings:               # THIRD TIER (D3 amendment 1)
  - [BRIEF-LEVEL] <finding-1>
  - [BRIEF-LEVEL] <finding-2>   # omitted on PASS
artifact: <canonical mockup path>
screenshot_capture: <image_save id, if visual QA performed — [VISUAL-QA-DEFERRED] when capture_mockup not yet shipped>
model: <model id from the spawn envelope>
pinned_spec_sha: <spec SHA from the dispatch envelope>
review_caveat: <advisory_disclosure text on accept-with-disclosure — populated only on D4 advisory-only accept path>
notes: <optional, free-form, e.g. prev_attempt_unparseable on re-dispatch>
```

**Resolved sub-questions (per architecture-recommendation.md §2 :52, all D3 amendments):** **Q3.1** PASS-with-advisories terminates the loop (advisories ride into the spec as fix-up inputs; conformance tests must assert this is a valid terminal verdict). **Q3.2** review covers both spec acceptance criteria (source of truth) and brief inputs (alignment check). **Q3.3** brief-inherited defects → `[BRIEF-LEVEL]`. **Q3.4** missing artifact → `verdict: needs-revision` + `[CRITICAL] artifact not found at <path>` — same machine-parseable shape.

---

## 3. Tasks

T1–T12 below. Tasks are ordered; each lands one atomic file (or one atomic edit per file).

| ID | Task | Outcome (testable) | Touchpoints (files) | Depends on (D#) | Depends on (task) | Risks carried | AC |
|----|------|--------------------|---------------------|-----------------|-------------------|----------------|----|
| **T1** | Sanity-check that `agents/critic/` does NOT already exist (regressions from earlier agent versions). Record presence/absence in `phase2-preconditions.log`. Verify phase-1 `critic-verdict-schema.md` exists and SHA1 matches the snapshot from phase 1 T11. | Log line dated; result `absent`. | `phase2-preconditions.log` (new, in this plan dir) | — | — | R10 | AC1 |
| **T2** | Read `docs/agent-prompt-writing-guide.md` end-to-end (research §5 calls out the load-bearing sections `:91-122`, `:139`, `:144-152`, `:213`, `:217-230`, `:244-258`). Read `architecture-recommendation.md` §2 D6 (verbatim) and §2 D3 (verbatim) before any meta.json or rule.md write. | Developer demonstrates familiarity in the next task's structure. | (developer note only) | — | T1 | R16 | — |
| **T3** | Create `agents/critic/meta.json` per `architecture-recommendation.md` §2 :78-94 (D6 canonical form): `id="critic"`, `name="Critic"`, `description` (≤2 sentences), `icon`, `color`, `version="1.0.0"`, `llm_model="vision"` (D6 model ratified per §2 :101), `innate_skills=["dynamic-skill","todo"]`, `skill_injection=true`, `recursion_limit_multiplier=7`, `tools.allow: ["read_file","image","design"]` (CANONICAL D6 — bare names; `image_save` is NOT in this list), `tools.deny: ["bash","proc","instance","service","midflight","shared_meta_kv","infra","mcp","image_save"]` (the `image_save` entry is REQUIRED — read-only leaf purity; the `mcp` deny is mandatory for a leaf). `team_members=[]` (DECLARED; the EFFECTIVE list is auto-extended by the daemon with `image-comparator` per `_auth.py:155-161` — documented in T7). NO `watchover.timeout_seconds` row (dropped per D6 §5.4 — inert on a non-watcher; if kept for sketcher symmetry it would be documented as inert, but architect ruling is to DROP). NO `default_queue` entry (default `system_queue` is correct). Freeze-set `_tool_registry.py` is UNTOUCHED. NO `view-views` in `tools.allow` (Q6.1 deferred). Verify phase 1 T3 added `"critic"` to `agents/designer/meta.json:22` `team_members` (default per finding 20). | File exists, valid JSON; canonical D6 allow; `image_save` in deny; `mcp` deny present; no view-views; `team_members=[]`; designer's `team_members` contains `critic`. | `agents/critic/meta.json` (new) | D6 (canonical) | T2 | R5, R15, R16 | AC2 |
| **T4** | Create `agents/critic/soul.md`. First-person voice; compression-survival identity (substance-of-thing). NO references to `meta.json`, `tools.allow`, `tools.deny`, `daemon/`, `skill-set.yaml`, `seeder`, `version registry`, `test paths`. Cross-references in the agent's own prose use SECTION names (not filenames). Tone-directive block: 🔴 blocking / 🟡 defer / 🟢 nit severity prefixes (borrowed from designer's pattern, guide `:144-152` allows). | File exists, ~2k chars, first-person; closure-grep clean. | `agents/critic/soul.md` (new) | D3, D6 | T3 | R16 | AC3, AC4 |
| **T5** | Create `agents/critic/rule.md`. ≤7 Cardinals numbered 1-7. **Cardinal #1 — the verdict protocol (verbatim text above §2 Decisions row):** covers (a) emit-on-every-review, (b) shape quoted from `My Workflow` Review block (NOT a filename — canonical home is `workflow.md` `## Review`; the planning-dir doc is the frozen pin), (c) regex `^verdict:\s*(pass|needs-revision)\s*$` anchor, (d) malformed-parse → designer re-dispatches CRITIC with `notes: prev_attempt_unparseable` (NOT sketcher), (e) `pinned_spec_sha` is MANDATORY, (f) severity tiers `[CRITICAL]` / `[ADVISORY]` / `[BRIEF-LEVEL]`. Cardinal #2 — no app-code changes. Cardinal #3 — one-shot per dispatch (no iterative re-dispatch within one verdict cycle). Cardinal #4 — leaf posture (no children). Numbers 5-7 reserved for standard compliance-with-project-conventions slots (mirrors sketcher/designer conventions). No duplicate numbered Cardinals. | File exists; ≤7 numbered Cardinals; Cardinal #1 contains the four discipline anchors (regex / parse-fail→critic / pinned_spec_sha / three-tier). | `agents/critic/rule.md` (new) | D3 (canonical + 4 amendments) | T4 | R3 | AC5 |
| **T6** | Create `agents/critic/workflow.md`. Skeleton: (a) `## How I work` (read-context-decode → focus against the brief sections → cross-check against mockup → emit verdict), (b) `## Defect taxonomy` (🔴/🟡/🟢/[BRIEF-LEVEL] with citation requirements), (c) `## One-shot discipline` (one verdict per dispatch, no self-loops), (d) `## Report integrity` (`[REPORT SANITY]` markers make a report interim — verify before using), (e) **`## Review` — CANONICAL HOME OF THE VERDICT SCHEMA. This section quotes `critic-verdict-schema.md` (the frozen pin) VERBATIM by re-stating the heading + the YAML shape + each of the four amendment notes. Cardinal #1 binds to this section by name (NOT by filename).** ≤60 lines. **AC6 grep:** `grep -nE "^## Review|## Verdict block|verdict:\s*(pass|needs-revision)|prev_attempt_unparseable|pinned_spec_sha" agents/critic/workflow.md` — replaces the prior `verdict block` / `.md` path grep. **SC-19 (plan-overview.md):** ≥3 hits on `grep -nE "^## Review|## Verdict|verdict:\s*(pass|needs-revision)" agents/critic/workflow.md`; file ≤60 lines. | File exists; `## Review` section present; quotes the schema verbatim (heading + YAML + amendment sections); cites Cardinal #1 by section name. | `agents/critic/workflow.md` (new) | D3 (canonical + 4 amendments) | T5 | R16, R22 | AC6 |
| **T7** | Create `agents/critic/tools_note.md`. **DECLARED vs EFFECTIVE team note (mandatory section):** "DECLARED `team_members: []`. EFFECTIVE team is non-empty: `image-comparator` is auto-extended for the `compare_images` tool via the daemon mechanism `_auth.py:155-161` (spawn-time inheritance when the `design` allow entry resolves)." Then per canonical D6: each `tools.allow` entry gets a one-line operational summary (latency, idempotency, read/write nature). Each `tools.deny` entry gets a one-line rationale ("bash → no shell; proc → no process spawn; instance → no child spawn; service → no service start; midflight → no leader question; shared_meta_kv → no metadata mutation per finding 8a (KV counter pinned from designer's side; critic stays read/denied); mcp → no LLM/MCP; infra → no infra mutation; image_save → read-only leaf purity, deny per D6 intent per finding 9 adjudication"). | File exists; DECLARED/EFFECTIVE team note present; entries match `meta.json` exactly; rationale for `image_save` deny and `shared_meta_kv` deny present. | `agents/critic/tools_note.md` (new) | D6 (canonical) | T3 | R5, R16, R21 | AC7 |
| **T8** | Decide whether `agents/critic/skill-set.yaml` is warranted. Default NO (research OQ-A says read surface is straight off `image + filesystem`; no new skill content required). If a future commission adds `design.capture_mockup`, re-evaluate. Record the decision in this plan dir (`phase2-skillset-decision.md`). | File `phase2-skillset-decision.md` exists with rationale. | `phase2-skillset-decision.md` (new, in this plan dir) | D6 | T7 | R15 | AC8 |
| **T9** | Cross-check the four writing-guide guardrails: closure-grep (`\.md` + bare `agents/` tokens) returns 0 hits in critic prose; safety-critical prohibitions in `meta.json tools.deny` (not prose); no adapted-from provenance; pre-commit checklist `:244-258` items checked. Record checks in `phase2-prompt-conformance.log`. | Log file present; all checks pass. | `phase2-prompt-conformance.log` (new, in this plan dir) | — | T4, T5, T6, T7 | R16 | AC9 |
| **T10** | Hardening check: `_tool_registry.py` freeze-set unchanged (`git diff feature/designer-critic-orchestration -- daemon/tools/_tool_registry.py` returns empty — confirmed via Phase 1 T12 + Phase 4 finalization). Stale-comment edit in `_tool_registry.py` (the "commissioned user" honesty note) NOT made in this phase (no `view-views` exposure on critic per Q6.1 deferral, so no 4th-commissioned-user entry to record; if the comment is stale for OTHER reasons, a follow-up commission edits it — out of scope for this build). | `_tool_registry.py` byte-identical to pre-phase-2. | (verification only) | D6 (canonical) | T3 | R5 | AC10 |
| **T11** | Run `pytest tests/unit/test_report_integrity_prompts.py -k critic` (and any agent-add tests pre-existing in the test pack) against the post-edit agent. Capture results in `phase2-results.txt`. ALL MUST PASS. | The pytest invocation returns 0; output captured. | `phase2-results.txt` (new, in this plan dir) | — | T10 | R10 | AC11 |
| **T12** | **`design.capture_mockup` tool promotion** (per architecture-recommendation.md §2 :107 + Q7.2: YES, spec-only). Create the planning-dir doc `.agents/shared/planning/designer-critic-orchestration/design-capture-mockup-spec.md` that captures (a) the use case (critic needs the capture for visual QA per the manual capture recipe — migrated to `agents/sketcher/tools_note.md` by phase 1 T7 per Q1.2 / architecture-recommendation.md §2 :109; pre-migration source was the `## Capture Procedure` section of `agents/designer/tools_note.md` (header-delimited: heading `:55` through the `---` at `:140`, incl. `### Provenance tag policy` + `### Failure modes I expect` — Pass-6 re-pin of the stale `:55-110` cite, aligned with phase1 T7/AC8)), (b) the input shape (URL or `od.save` artifact path), (c) the output shape (`view_link` mintable via substrate; `image_save` id; deterministic for replay), (d) the integration path (tool under `design` category; `agents/designer/tools.allow` would gain the entry in a follow-up commission). This phase pins the SPEC; the tool itself is OUT OF THIS COMMISSION. **Note for the analyst:** until `capture_mockup` ships, `[VISUAL-QA-DEFERRED]` is the canonical review-page-handoff marker (handled by Cardinal #1 + the schema's `screenshot_capture` field). | `design-capture-mockup-spec.md` exists; ≤60 lines; four sections (use-case / input / output / integration). | `.agents/shared/planning/designer-critic-orchestration/design-capture-mockup-spec.md` (new) | D7 (Q7.2) | T11 | R17, R22 | AC12 |

---

## 4. Acceptance criteria (phase 2)

| ID | Criterion | Verification |
|----|-----------|--------------|
| **AC1** | `agents/critic/` does NOT pre-exist; otherwise reconcile before creating. Phase-1 verdict schema doc SHA1 verified. | `ls -la agents/critic` fails with `No such file or directory` (pipe-free rewording — the prior markdown-escaped pipeline was not copy-paste-executable from raw markdown); `sha1sum .agents/shared/planning/designer-critic-orchestration/critic-verdict-schema.md` matches the phase-1 snapshot. |
| **AC2** | `agents/critic/meta.json` valid JSON; `id="critic"`; `team_members=[]` (declared); `tools.allow == ["read_file","image","design"]` (CANONICAL D6 — bare names); `image_save` in `tools.deny`; `mcp` in `tools.deny`; no `view-views` anywhere; ALL of `["bash","proc","instance","service","midflight","shared_meta_kv","infra","mcp","image_save"]` are in `tools.deny`. Verify designer's `team_members` contains `critic` (default per finding 20). | `/home/nea/ensemble-src/.venv/bin/python3 -c 'import json; cd=json.load(open("agents/designer/meta.json")); cr=json.load(open("agents/critic/meta.json")); required_deny={"bash","proc","instance","service","midflight","shared_meta_kv","infra","mcp","image_save"}; assert cr["id"]=="critic" and cr["team_members"]==[] and cr["tools"]["allow"]==["read_file","image","design"] and required_deny<=set(cr["tools"]["deny"]) and "view-views" not in cr["tools"]["allow"] and "critic" in cd["team_members"]'` returns 0 (venv interpreter `/home/nea/ensemble-src/.venv/bin/python3` REQUIRED on this host — see plan-overview.md SC-9). |
| **AC3** | `soul.md` first-person; ≥2k chars; closure-grep clean of system-internals tokens. | `wc -c agents/critic/soul.md` ≥ 2048; `grep -nE 'meta\.json|tools\.allow|daemon/|skill-set\.yaml|seeder|version registry|test paths' agents/critic/soul.md` returns 0. **Positive control:** the same pattern returns 0 against `agents/designer/soul.md` today (clean). |
| **AC4** | `soul.md` does not mention "single-page one-offs" (parallel guard against drift trap). | `grep -n "single-page one-offs" agents/critic/soul.md` returns 0. |
| **AC5** | `rule.md` ≤7 numbered Cardinals; Cardinal #1 carries the four discipline anchors (regex / parse-fail→critic / pinned_spec_sha / three-tier). | `grep -cE '^[0-9]+\.' agents/critic/rule.md` returns ≤7; `grep -cE "verdict block|prev_attempt_unparseable|pinned_spec_sha|\[CRITICAL\]|\[ADVISORY\]|\[BRIEF-LEVEL\]" agents/critic/rule.md` returns ≥4. **Positive control:** the count grep returns 7 against today's `agents/designer/rule.md` (lines 9-15). |
| **AC6** | `workflow.md` `## Review` (canonical home) section present and quotes the schema verbatim; the grep checks the `## Review` heading + verdict-line + parse-fail discipline (the prior `verdict block .md` path-grep is RETIRED — finding 17). | `grep -nE "^## Review\b" agents/critic/workflow.md` returns ≥1; `grep -nE "verdict:\s*\`?(pass|needs-revision)\`?" agents/critic/workflow.md` returns ≥1; `wc -l agents/critic/workflow.md` returns ≤ 60. |
| **AC7** | `tools_note.md` has DECLARED vs EFFECTIVE team note + entries match `meta.json` allow/deny 1-to-1; `image_save` deny rationale present; `shared_meta_kv` deny rationale present. | **(Pass-5 fix — blocker 3: the prior bare-`|` plain-grep forms were false-red by construction; BRE treats `|` as a literal pipe and cannot match the T7-mandated prose. Split into individual fixed-string greps, each satisfiable by the T7 prose verbatim):** `grep -n "DECLARED" agents/critic/tools_note.md` returns ≥1; `grep -n "EFFECTIVE" agents/critic/tools_note.md` returns ≥1; `grep -n "image_save" agents/critic/tools_note.md` returns ≥1 (deny entry + rationale); `grep -n "read-only leaf purity" agents/critic/tools_note.md` returns ≥1; `grep -n "shared_meta_kv" agents/critic/tools_note.md` returns ≥1; diff vs generated table is empty. **Fixture-tested (Pass-5, executed against the T7-mandated prose shape — see review-cure-map.md Pass 5 for command + output).** |
| **AC8** | Skill-set decision recorded. | `phase2-skillset-decision.md` exists; explicitly states NO or YES. |
| **AC9** | Closure-grep and writing-guide checks pass. | `phase2-prompt-conformance.log` non-empty; `grep -cE "PASS" phase2-prompt-conformance.log` ≥ 4. |
| **AC10** | Freeze-set unchanged. | `git diff feature/designer-critic-orchestration -- daemon/tools/_tool_registry.py` empty. |
| **AC11** | Pytest report-integrity test passes for critic. | `cat phase2-results.txt` shows 0 exit code. |
| **AC12** | `design.capture_mockup` planning-dir spec exists per D7/Q7.2 promotion. | `ls .agents/shared/planning/designer-critic-orchestration/design-capture-mockup-spec.md` returns 0; ≤ 60 lines; `grep -nE "^## (Use case|Input shape|Output shape|Integration path)" design-capture-mockup-spec.md` returns ≥4. |

---

## 5. Hand-off to phase 3

**Phase 3 needs from phase 2:**

- Critic `id` (frozen as `"critic"`).
- Cardinal #1 verdict protocol (frozen shape; the four discipline anchors).
- `meta.json:tools.allow: ["read_file","image","design"]` (frozen D6 canonical form; `image_save` in deny).
- `tools.deny` list (incl. `image_save`, `shared_meta_kv`, `mcp`).
- Resolved-tool-set = `{read_file, image_get, image_list, explain_image, compare_images}` (5 tools; confirmed by the canonical D6 form). The `compare_images` tool is `design-category` registered; its verification path lives in phase 4 T10 (`verification-surface.md`).
- DECLARED vs EFFECTIVE team doc note in `tools_note.md`.
- `## Review` block in `workflow.md` is the canonical home of the verdict schema (planning-dir doc = frozen pin by section reference).

These are the inputs to phase 3's orchestration block. Phase 3's tasks wire critic dispatch as the second step of the existing two-step pipeline (designer → sketcher → +critic).

**Parallel concern.** Phase 1 T3 already added `"critic"` to `meta.json:22` (default per finding 20). Phase 2 T3 verifies the entry exists; if absent, phase 2 inserts it (single-line meta.json edit; tracked in `phase2-preconditions.log`).

---

**End of phase 2 plan.** Phase 3 next.
