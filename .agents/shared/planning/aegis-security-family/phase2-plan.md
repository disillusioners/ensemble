# Phase 2: The Three Analysis Children — `hacker`, `cracker`, `sweeper`

## Objective

Author the **leaf analysis lanes** of the family — the white-hat guardrails must be **explicit in every
persona**, not just in the orchestrator. Each child is a stateless leaf that receives a self-contained
dispatch prompt (scope, sandbox path, **the recorded engagement root — AM-4: the absolute
`$HOME/.local/state/aegis/engagements/<id>/` path, never discovered by glob** — CVE context, the
specific finding to work on) and returns a report; none of them spawns, none of them edits the target,
and none of them touches a real credential or an external host.

**Eighteen files land in this phase** (3 agents × 6 files: meta + 4 prompt files + the installer
skill body); **the three `.lib.sh` complete the children's 21-file set in Phase 3** (W3, plan
council):

```
agents/hacker/{meta.json, soul.md, rule.md, workflow.md, tools_note.md,
               skills-template/fuzzing-toolchain.md, skills-template/fuzzing-toolchain.lib.sh}
agents/cracker/{meta.json, soul.md, rule.md, workflow.md, tools_note.md,
                skills-template/credential-bench.md,  skills-template/credential-bench.lib.sh}
agents/sweeper/{meta.json, soul.md, rule.md, workflow.md, tools_note.md,
                skills-template/sast-toolchain.md,    skills-template/sast-toolchain.lib.sh}
```

> The three `.lib.sh` libraries are authored in **Phase 3**. This phase authors the **skill bodies**
> (the `.md` halves), including the FENCES block that T23 pins identical across all three.

---

## Entry Criteria

- **Phase 1 exited** — `aegis`'s four prompt files and its strategy skill exist and are the settled
  wording that this phase must match in altitude.
- The guide-compliance checklist and the §3 closure-grep exemption list are available.
- **Q1–Q4 are ruled** (a Q1 ruling that drops charts does not affect the children; a Q2 ruling changes
  the engagement-folder paths the children's dispatch context names).
- `agents/hacker/`, `agents/cracker/`, `agents/sweeper/` do not exist yet.

---

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | **Author the three `meta.json` files** to the DP1 matrices: `tools.allow` = `bash, proc, filesystem, time, help, self`; `tools.deny` = `write_file, edit_file`; `team_members` = `[]`; `llm_model` = `"coding"`; `default_queue` = `"system_parallel_queue"`. **No `innate_skills`** that widen the allow (the innate expansion must stay a no-op — D3); **no** `watchover` block (inert for every agent except `watcher` — D7/AM-5); **no** `mcp_full_access` (E4); **no** `mcp` category (the CVE surface belongs to aegis — the sweep is deployment-wide and the context is cheaper to pass in). | Phase 1 exited | T5/T6/T7/T8/T9/T10/T11/T26/T27 green for all three. The allow is **non-empty on every meta** (the E3 fold). **AM-4:** each child's dispatch context names the **recorded absolute engagement root** — the child reads findings/evidence back by that recorded path, never by glob. |
| 2 | **Author `agents/hacker/{soul,rule,workflow,tools_note}.md`** — the offensive analyst. `soul.md`: identity + tone block (caller voice, dispatch voice, per-severity framing for the findings it produces). `rule.md` (≤7 cardinals — **Blocker 5 FINAL set: the five guardrail families all present; topics (2)+(3) collapse explicitly into one cardinal to make room for findings-privacy, per the leader merge-don't-cap-widen ruling**): (1) white-hat authorisation; (2) **self-owned sandbox only — zero external targets, ever** *(the Blocker-5 collapse: sandbox + no-external-targets are ONE legality cardinal)* — the artifact under test must be a prefix-match of the engagement sandbox root, bind loopback only, need no outbound egress, else STOP and report; **no external targets, ever**; (3) read-only on the target tree — **copy-then-build (AM-13): I copy the inputs I need into the sandbox build tree; the target tree is never a write target**; (4) **findings are private** (Blocker 5) — they live in the project-scoped off-repo engagement store and are **never committed** (LEADER-CONFIRMED INTENT 2026-11-04); (5) **my lane is fuzzing** — harnesses, corpora, crash triage, and I do not write the report; (6) end turn after I report; (7) the honesty rule — a campaign that found nothing says so with the commands run and their output, not with silence. *(synthetic-sample-data-only is not an applicable family for hacker — recorded absent-by-applicability in the T29 matrix.)* `workflow.md`: the attack-surface mapping → harness construction → corpus seeding → campaign → crash triage → report shape; **write-target classes (Blocker 3): my run-time writes are class (ii) — the per-engagement sandbox root only (copy-then-build, AM-13); the class-(i) toolchain root is install-time (the installer library's job); class (iii) everything else — the target codebase included — is never written**; the campaign is bounded by an explicit time/size budget and the process is tracked through the `proc` lane so it can be stopped; every loop/pipeline runs as a script file invoked by absolute path. **Exploit-attempt coverage (n5, approver iter-001 — the commission names exploit attempts explicitly):** my workflow INCLUDES targeted **exploit attempts on the harness/crash paths** — PoC construction, crash reproduction, controlled input sequences — each performed **inside the class-(ii) sandbox only** (the same prefix-match/loopback/no-egress gate as every active technique); an exploit attempt against anything outside the sandbox is a legality-line STOP, reported as a finding. **AM-12 process caps (workflow):** `bash` hard max is 1800s (timeout=0 blocks unbounded ⇒ a multi-hour campaign is NEVER one bash call); a campaign runs as a `proc_run` background process (timeout=0 = never auto-killed, no upper bound; **max 10 tracked processes per instance**); **AFL++ parallel mode is OFF** (or the harness traps and kills its children on exit); **every `process_id` is reported back to aegis in my final report** (it appears only in the tool's text result) so the engagement record can carry it and teardown can `proc_stop` it; I never rely on instance terminate for process cleanup. `tools_note.md`: the six categories with the operational boundary + the `bash` limit + the script-file rule. | 1 | T13–T18 green for hacker; the legality language is **explicit** (not implied by the orchestrator); the self-owned check is mechanical in form; the AM-12 caps and the process_id reporting duty are present. **Blocker 5: all five guardrail families present at exactly 7 cardinals** (white-hat ✓(1), sandbox/zero-external ✓(2), read-only ✓(3), findings-private ✓(4), synthetic-data **absent-by-applicability**); T16 count unchanged (7). **N3 (council, delta-3) — deliberate asymmetry, UPDATED by Blocker 5:** hacker sits at **7 topics with NO numbered-split pin** — that remains deliberate and must NOT be "fixed". **Blocker 5 superseded the original "no merge" premise:** hacker's sandbox + no-external-targets topics WERE explicitly collapsed into cardinal (2) to make room for findings-privacy (the merge is recorded above and in `decisions.md` §B.3); what persists is the asymmetry that hacker's set carries **no standing-prohibition slot** (cracker/sweeper reserve slot (7) for it), so the T16 arithmetic stays self-evident without a split pin. |
| 3 | **Author `agents/cracker/{soul,rule,workflow,tools_note}.md`** — the protection-strength auditor. `soul.md`: identity + tone block. `rule.md` (≤7 cardinals): white-hat authorisation; **synthetic data only** — every corpus is generated by my own generator, and **no real user data, no live credentials, no third-party hashes are ever ingested**; self-owned sandbox only; **read-only on the target — copy-then-build (AM-13): I copy the inputs I need into the sandbox build tree; the target tree is never a write target**; the mechanical sandbox-ownership prefix assertion gates any active technique (A-a: **"self-owned sandbox only" and "read-only on the target — copy-then-build" author as ONE merged cardinal** — the data-flow contract — so the enumerated topics stay within the ≤7 cap); my lane is crypto/credential/protocol strength — hash storage, KDFs, key handling, token lifetimes, IPC auth; end turn after I report; the honesty rule — a weak hash that I could not reproduce is reported as *inferred*, not *reproduced*. `workflow.md`: threat-model the crypto surface → generate the synthetic corpus → run the bench → parse the results → correlate → report. **Write-target classes (Blocker 3): my run-time writes are class (ii) sandbox-root only; class (iii) — everything else, the target tree included — is never written.** **AM-12 process caps (workflow):** `bash` hard max is 1800s (timeout=0 blocks unbounded ⇒ a multi-hour bench CANNOT be one bash call); a long bench runs as a `proc_run` background process (timeout=0 = never auto-killed, no upper bound, returns immediately; **max 10 tracked processes per instance**; the proc log buffer spills to a temp file and grows unbounded); **every `process_id` is reported back to aegis in my final report** (it appears only in the tool's text result) so the engagement ledger can carry it; teardown is explicit — `proc_stop` (SIGTERM → SIGKILL after 5s) — and must **NOT** depend on instance terminate; **pause is never a child-supervision lever** (pause reaps no processes and cascades to the tree root by default). `tools_note.md`: the six categories with the boundary + the `bash` limit. | 1 | T13–T18 green for cracker; the **sample-data-only** rule is a cardinal, not a guideline; the corpus generator produces its own inputs (it cannot write files — the generator is a script into the sandbox); the AM-13 copy-then-build contract and the AM-12 caps/process_id duty/teardown discipline are present. **Blocker 5: all five families present** (white-hat ✓(1), synthetic-data ✓(2), sandbox/read-only ✓(3), findings-private ✓merged into (6), plus the standing prohibition (7)); T16 count unchanged (6+1). **council-D7 cardinal-split pin (cracker is AT the ≤7 cap — zero T16 headroom):** exactly **SIX content cardinals + one standing prohibition** numbered in `rule.md`, in this order — (1) white-hat authorisation; (2) synthetic-data-only; (3) **the merged data-flow cardinal: self-owned sandbox + copy-then-build read-only-on-target + the prefix assertion** (the A-a merge); (4) my lane is crypto/credential/protocol strength; (5) end turn after I report; (6) **the honesty rule + findings-privacy** *(Blocker 5 leader ruling: privacy MERGED into this findings-handling cardinal, not added as an 8th)* — a weak hash that I could not reproduce is reported as *inferred*, not *reproduced*, **and my findings are PRIVATE: project-scoped off-repo engagement store, never committed** (LEADER-CONFIRMED INTENT 2026-11-04); (7) **a standing PROHIBITION, not a content cardinal** — nothing else is numbered at cardinal level; any additional topic (e.g. the bash/script-file rule) authors as a **Guideline** below the cardinals. Numbering differently ⇒ 8 ⇒ T16 reds by design. (N5: the count semantics are honest — **6 content cardinals incl. the privacy-carrying honesty cardinal** + the prohibition, within the ≤7 cap.) |
| 4 | **Author `agents/sweeper/{soul,rule,workflow,tools_note}.md`** — the SAST workhorse. `soul.md`: identity + tone block. `rule.md` (≤7 cardinals): white-hat authorisation; self-owned sandbox only (the scanner runs in the sandbox); **read-only on the target — copy-then-build (AM-13): I copy the inputs I need into the sandbox build tree; the target tree is read, NEVER written**; the mechanical sandbox-ownership prefix assertion gates any active technique (A-a: **"self-owned sandbox only" and "read-only on the target — copy-then-build" author as ONE merged cardinal** — the data-flow contract — so the enumerated topics stay within the ≤7 cap); **my lane is static analysis + triage** — I run the scanner suite, normalise the output, and deduplicate across tools; I never attempt exploitation — a finding I cannot justify statically is reported as *inferred*; end turn after I report; the honesty rule — raw scanner hits are not findings; a finding needs a location, a violated assumption, and an impact statement. `workflow.md`: tool selection per target class → run (SARIF/JSON) → normalise → dedupe → triage → report. **Write-target classes (Blocker 3): my run-time writes are class (ii) sandbox-root only (scanner output via script redirection); class (iii) — everything else, the target tree included — is never written.** **AM-12 process caps (workflow):** `bash` hard max is 1800s (timeout=0 blocks unbounded ⇒ a multi-tree scan CANNOT be one bash call); a long scan runs as a `proc_run` background process (timeout=0 = never auto-killed, no upper bound, returns immediately; **max 10 tracked processes per instance**; the proc log buffer spills to a temp file and grows unbounded); **every `process_id` is reported back to aegis in my final report** (it appears only in the tool's text result) so the engagement ledger can carry it; teardown is explicit — `proc_stop` (SIGTERM → SIGKILL after 5s) — and must **NOT** depend on instance terminate; **pause is never a child-supervision lever** (pause reaps no processes and cascades to the tree root by default). `tools_note.md`: the six categories with the boundary + the `bash` limit + the scanner-output redirection rule (output lands in the sandbox via script redirection — the child cannot write files). | 1 | T13–T18 green for sweeper; the "raw hits are not findings" discipline is present; the CI supply-chain leg (dependency/CVE scanning of the build config) is in scope for this lane; the AM-13 copy-the-inputs contract and the AM-12 caps/process_id duty/teardown discipline are present. **Blocker 5: all five families present** (white-hat ✓(1), sandbox/read-only ✓(2), findings-private ✓merged into (6); synthetic-data **absent-by-applicability**; never-exploit rides (4)); T16 count unchanged (6+1). **council-D7 cardinal-split pin (sweeper has the same exposure — also AT the ≤7 cap):** exactly **SIX content cardinals + one standing prohibition** numbered in `rule.md`, in this order — (1) white-hat authorisation; (2) **the merged data-flow cardinal: self-owned sandbox + copy-then-build read-only-on-target + the prefix assertion** (the A-a merge); (3) my lane is static analysis + triage; (4) I never attempt exploitation; (5) end turn after I report; (6) **the honesty rule + findings-privacy** *(Blocker 5 leader ruling: privacy MERGED into this findings-handling cardinal, not added as an 8th)* — raw hits are not findings (a finding needs a location, a violated assumption, an impact statement), **and findings are PRIVATE: project-scoped off-repo engagement store, never committed** (LEADER-CONFIRMED INTENT 2026-11-04); (7) **a standing PROHIBITION, not a content cardinal** — nothing else is numbered at cardinal level; additional topics (e.g. the scanner-output redirection rule, the bash/script-file rule) author as **Guidelines**. Numbering differently ⇒ 8 ⇒ T16 reds by design. (N5: the count semantics are honest — **6 content cardinals incl. the privacy-carrying honesty cardinal** + the prohibition, within the ≤7 cap.) |
| 5 | **Author the three installer skill bodies** (`fuzzing-toolchain.md`, `credential-bench.md`, `sast-toolchain.md`) — each is a **file-invoked** skill: it lives under the owning agent's `skills-template/`, appears in **no** `skill-set.yaml` (so it never enters the global skill bank — **AM-7's manifest-gating invariant is the mechanism**: seeding enumerates only `skill-set.yaml` entries and no directory scan of `skills-template/` exists, so an un-manifested skill is unreachable by every search path; DISCLOSED: the bodies still ship in the release payload and are readable on disk — the boundary is the search index, not the filesystem). Each body carries: the **FENCES block** (**AM-10: the pin target is the FENCES bullet list in these three `.md` bodies — byte-identical across all three, scoped to the bullet list ONLY: it MUST include the C3 checksum bullet (charter has none ⇒ byte-identity with charter is impossible and not required) and MUST exclude all per-tool content (release-asset URLs, checksum sources, tool flags)**; see `decisions.md` deviation **D-1** + **AM-10**), **with a NEUTRAL `## Fences` heading — no provenance parenthetical (AM-8: charter's own heading carries "preserved verbatim from…" and violates guide §10 while escaping today's 3-token list; the new bodies do not copy that defect, and T18's extended token list covers it)**, the pre-flight detect step, the **class-(i) persistent user-space toolchain install root** (Blocker 3: e.g. `~/.local/opt/aegis-toolbench`; install-time, idempotent, checksum-verified) with absolute-path capture (non-interactive shells do not source `~/.bashrc`), pinned-major versions, the **sha256 verification of the fetched release asset** (the charter template's gap — C3 — closed here; hex `SHA256SUMS` + W3C-SRI as in `daemon/services/live_views.py:1357-1406`), the staged-config-then-promote flow, the readiness-probe contract (**exit 0 warm / 1 cold / 2 in-progress**), the release lock whose release **must** run on failure, the attempt counter capping self-heal at 2, and the mandatory **REVERSAL (uninstall)** section. Each body is tool-specific below the skeleton: tool selection, harness/corpus/triage specifics, and the tool's own readiness signals. | 1 | T23 green per the AM-10 definition (identical `.md` FENCES bullet lists across the three — checksum bullet in, per-tool content out). Each body has the checksum step, the readiness contract, the lock discipline, the attempt cap and the reversal section. The `.md` bodies reference the library by bare name or by the operational read-target form only (no `.md` path tokens). |
| 6 | **Cross-reference the adjacent surfaces instead of duplicating them** — the design-time STRIDE material (architect's own security skill) and the static review surface (reviewer's own security skill) are **adjacent, not overlapping**. The aegis family's surfaces reference them **by section name** (guide §3 cross-reference convention v2) and add what they lack: the *active* dimension, the sandbox/ownership model, and the report → fix → re-verify loop. No content is copied from either (C5). | 2–4 | No duplicated block appears in the family's surfaces; every cross-reference resolves to a real section in the owning agent; no filename/path token is used in a cross-reference. |
| 7 | **Run the guide-compliance checklist for the three children** — every checklist row whose target is a child artifact is satisfied and signed off; the §3 closure grep is run over each child dir and every survivor is either on the Phase-0 exemption list (the `.lib.sh` read target; the engagement-folder paths) or reworded. | 2–5 | The checklist's child columns are fully ticked; the closure-grep survivors are enumerated per child. |
| 8 | **Do the shared-doctrine alignment pass** — re-read aegis's cardinals from Phase 1 and check that the children's legality language is the **same doctrine one altitude lower**: same legality vocabulary, same sandbox-root notion, same "read-only on the target" meaning, same honesty rule (reported vs inferred), same END TURN contract. Fix any divergence in **both** directions — if a child's wording is better, aegis adopts it; if aegis's is stricter, the child adopts it. | 2–4, 7 | A written alignment note recording each wording decision and its resolution. No concept is stated with two different meanings across the family. |
| 9 | **Verify the family-wide fence invariants** — every child meta has a non-empty allow; the write fence is on all four agents; no privileged category appears in any allow; no `job` category anywhere (the ungated dispatch path); no `instance_read`/`instance_ops`; no `mcp_full_access`. | 1 | T6–T11 green for all three children; the invariants are re-checked mechanically in this phase (the pytest form arrives in P4). |
| 10 | **Nothing else written** | — | `git status --porcelain` shows only `agents/hacker/**`, `agents/cracker/**`, `agents/sweeper/**`. |

---

## Gates

| Gate | Requirement | How it is checked |
|---|---|---|
| **G2.1 — leaf shape** | Each child's `team_members == []`; each child's allow is non-empty; each child's deny carries `write_file` **and** `edit_file`. | T5, T6, T7 |
| **G2.2 — family fences** | No privileged category in any allow; no `job`; no `instance_read`/`instance_ops`; no `mcp_full_access`. | T8, T9, T10, T11 |
| **G2.3 — guide compliance** | Every Phase-0 checklist row targeting a child artifact is satisfied. | The checklist's child columns |
| **G2.4 — prose hygiene** | No system internals, no unlisted path tokens, ≤7 cardinals each, tone block in each `soul.md`, no adapted-from provenance. | T14, T15, T16, T17, T18 (×3) |
| **G2.5 — white-hat explicitness (MECHANICAL per Blocker 4)** | **Each** child's own persona carries the legality language (self-owned sandbox, zero external targets, synthetic-data-only for cracker, read-only on target). | **The T29 verbatim-grep matrix (Blocker 4) — no longer an unpinned human read: the assertion artifact is the lane test's `test_legality_cardinal_pin_matrix` block, a per-agent/per-family PASS table (agent × family × matched-verbatim-line) written to the phase notes; the approver acceptance test (hacker "no external targets, ever" deletion ⇒ RED) is the mutation leg.** |
| **G2.6 — file-invoked discipline** | No `skill-set.yaml` exists for any child; the three installer skills appear in no manifest. | T21 |
| **G2.7 — skill-body completeness** | FENCES **bullet lists** identical across the three `.md` bodies (AM-10 scope: checksum bullet in, per-tool content out); checksum + readiness + lock + attempt-cap + reversal present in each; the `## Fences` heading is neutral — no provenance parenthetical (AM-8). | T23 (AM-10 definition) + the per-body inspection checklist |
| **G2.8 — alignment** | No concept is stated with two meanings across the family. | The alignment note from task 8 |
| **G2.9 — nothing else written** | Only the three child trees changed. | `git status --porcelain` |

---

## Verification Commands

```bash
cd /home/nea/ensemble-src-wt-aegis-security-family

# the phase's file set
find agents/hacker agents/cracker agents/sweeper -type f | sort

# the three metas, side by side
for a in hacker cracker sweeper; do
  python3 - "$a" <<'PY'
import json,sys
a=sys.argv[1]; m=json.load(open(f"agents/{a}/meta.json"))
print(a, "| allow:", m["tools"]["allow"], "| deny:", m["tools"]["deny"],
      "| team:", m.get("team_members"), "| model:", m.get("llm_model"),
      "| queue:", m.get("default_queue"), "| watchover:", m.get("watchover"))
PY
done

# guide §3 closure grep + §1 forbidden tokens over the three child dirs
grep -rn '\.md\|workflow\.md\|rule\.md\|soul\.md\|tools_note\.md\|memory\.md' agents/hacker agents/cracker agents/sweeper || true

# W6 + council-D5 (delta re-gate): the SECOND closure class — bare agents/-prefix tokens. Character class
# matches the canonical BARE_AGENTS_PREFIX_RE (:251) INCLUDING the trailing `*` (added 2026-09-08
# for bold-wrapped shapes — test comment :245-249); guide §10 :254. Violations ONLY when they
# function as cross-references to prompt sections, not operational paths (§12.5 #0).
grep -rnE '\bagents/[[:space:]`*]*[A-Z(]' agents/hacker agents/cracker agents/sweeper || true
grep -rn 'meta\.json\|tools\.allow\|tools\.deny\|daemon/\|_tool_registry\|skill-set\.yaml\|innate_skills\|agent_id=\|seed_all\|default_agent_versions' agents/hacker agents/cracker agents/sweeper || true

# cardinal counts (each <= 7)
for a in hacker cracker sweeper; do
  printf '%s ' "$a"
  awk '/^## Cardinal Rules/{f=1;next} /^## /{f=0} f && /^[0-9]+\. /{c++} END{print c+0}' "agents/$a/rule.md"
done

# no child has a skill-set.yaml (file-invoked mode — T21)
ls agents/hacker/skill-set.yaml agents/cracker/skill-set.yaml agents/sweeper/skill-set.yaml 2>&1 | head -5
```

---

## Coupling

- **Tight with:** P1 — the shared white-hat doctrine. The alignment pass (task 8) is the mechanism that
  keeps the two altitudes from drifting into two different policies.
- **Tight with:** P3 — each child's `.md` body declares the FENCES block and the contract that its
  `.lib.sh` implements; the two halves must agree bullet for bullet. P3 reads these bodies first and
  implements against them.
- **Tight with:** P4 — these 18 files (plus P3's three `.lib.sh`, which complete the children's 21) are the bulk of the commit payload and are hashed by the baseline
  re-stamp (only the 12 prompt files among them contribute baseline rows).
- **Loose with:** P5 — P5 verifies through the lane test.

---

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| **P2-R1** | The children's legality language is vaguer than the orchestrator's ("be careful") ⇒ the guardrails are prose, not a check. | **High** | Medium | Each child's legality rule names the **mechanical** form (the prefix-match assertion, the loopback bind, the no-egress precondition) and Gate G2.5 checks each persona individually, not by reference to aegis. |
| **P2-R2** | The children's prompts drift into **active-attack language** that reads as permission to target third parties. | **High** | Medium | The legality cardinal is phrased as an **authorisation precondition** ("the artifact under test must be inside the engagement sandbox root"), and the tone block's dispatch voice states that a dispatch prompt never carries an external target. |
| **P2-R3** | `cracker` is read as "cracks real credentials". | **High** | Medium | The synthetic-data-only rule is a **cardinal** (not a guideline); the corpus generator is a script that produces its own inputs; the report template's confidence basis distinguishes *reproduced* from *inferred*. |
| **P2-R4** | The three skill bodies diverge structurally ⇒ three different install contracts ⇒ T23 reddens at P4 and the drift risk lands anyway. | Medium | Medium | The three bodies are authored **together, in one task (task 5)**, from the charter template — the structure is fixed once, then specialised. T23 is the mechanical backstop. |
| **P2-R5** | A child accidentally gains a category that widens its reach (e.g. an innate skill that appends a category). | Medium | Low | The metas declare **no innate skills**, so the expansion is empty; T8/T9/T10 assert the resulting surface; the meta round-trip assertion catches a malformed block. |
| **P2-R6** | The children write into the target tree via `bash` (an unconstrained path), defeating "read-only on target". | **High** | Medium | This is R1 in `plan-overview.md`, and it applies most sharply here. Mitigation: the write fence + the explicit statement in each child's `bash` row that the read-only rule holds regardless of the tool used + the mechanical self-owned check that gates *active* techniques + the target-tree paths are named as read-only in the dispatch context. **The limit is stated, not engineered away — the plan must not imply containment.** |

---

## Effort

**≈ 1 – 1.5 working days** for 18 files in this phase (12 prompt files + 3 skill bodies + 3 metas + the alignment
pass). Cheaper per file than aegis, but the legality language must be written three times without
becoming three different policies — budget the alignment pass (task 8) explicitly.

---

## Exit Criterion

Phase 2 is done when **all nine gates (G2.1–G2.9) are green**:

1. the three leaf metas match the DP1 matrices exactly and are non-empty;
2. the family-wide fence invariants hold across all four agents;
3. every Phase-0 checklist row targeting a child artifact is satisfied;
4. the prose-hygiene greps are clean or every survivor is on the enumerated exemption list;
5. **each** child's own persona carries the legality language;
6. no child has a `skill-set.yaml`, and the three installer skills are in no manifest;
7. the three skill bodies each carry the full installer contract with an identical FENCES block;
8. the alignment note records no concept with two meanings across the family;
9. `git status --porcelain` shows changes only under the three child trees.

Phase 3 then implements the three `.lib.sh` libraries against the bodies authored here.
