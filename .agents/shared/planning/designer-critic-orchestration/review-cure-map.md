# Review-Cure Map — designer-critic-orchestration harmonization pass

**Date:** 2026-10-10
**Worktree:** `/home/nea/ensemble-src-wt-designer-critic-orchestration`
**Source-of-truth:** `architecture-recommendation.md` (binding) — phase plans consume its sections verbatim.
**Council report handled:** governor-council plan review (2 models), 11 critical + 9 optional + 1 note = 21 findings.

Each row: finding # → POST-EDIT file:line + anchor phrase (3-8 words quoted from the fixed text) + one-line cure description.

---

## Critical findings (1–11)

### 1. **[CRITICAL] Phase2 critic tool spec retains the UNSAFE filesystem-category form.**
**Cure file:line:** `phase2-plan.md:36` — anchor: **`D6 (canonical, replace both prior)`** — table row that REPLACES both prior tool specs with `allow: ["read_file","image","design"]` + `image_save` in `tools.deny`. **Cure:** canonical D6 form regenerated verbatim from architecture-recommendation.md §2 :74-104; `image_save` explicitly denied for read-only leaf purity; default_queue omitted; innate skills only in `innate_skills` not `tools.allow`; watchover dropped per §5.4 inertness.

### 2. **[CRITICAL] Existing test pins break with no owning task.**
**Cure file:line:** `phase4-plan.md:57` — anchor: **`OWNED test-edit task (replaces the prior plan's narrower T9 — finding 2 + 9). Rework the two test files`** — T9 description enumerates the five concrete edits to `tests/unit/agents/test_sketcher_agent.py` + the `TestDesignerHasZeroOdPorts` / `TestSketcherHasFourOdPorts` rewire-pin inversion in `tests/unit/plugin_subsystem/test_designer_rewire.py`. **Cure:** OWNED test-update task assigned to phase 4 (tester instance); non-workflow edits elsewhere hard-escalate to planner.

### 3. **[CRITICAL] SC-3/SC-18 kill-greps are vacuous — verified false-green.**
**Cure file:line:** `plan-overview.md:154` (SC-3) and `plan-overview.md:169` (SC-18) — anchor: **`grep -ni "single-page one-offs" agents/designer/workflow.md`** + secondary signal `grep -ni "stay on my own direct" agents/designer/workflow.md`. **Cure:** case-insensitive grep + secondary `stay on my own direct` signal; both files checked (workflow.md + soul.md).

### 4. **[CRITICAL] Dual-Run Pilot section deletion has no phase owner.**
**Cure file:line:** `phase3-plan.md:79` — anchor: **`DELETE the ## Dual-Run Pilot section at :133-145`** — new T7b task explicitly assigns the delete to phase 3. **Cure:** phase 3 boundary extended to `:103+~148`; T7b owns the delete; SC-4 enforces zero `^## Dual-Run Pilot` headers; the audit trail lives on the SUPERSEDED marker added by phase 1 T12, NOT in `workflow.md`.

### 5. **[CRITICAL] Verdict contract not harmonized with ratified D3 amendments.**
**Cure file:line:** `phase2-plan.md:42` — anchor: **`D3 (verdict shape — canonical, frozen pin)`** — table row governs `[BRIEF-LEVEL]` third tier, malformed-verdict→critic re-dispatch discipline (NOT sketcher), regex `^verdict:\s*(pass|needs-revision)\s*$` parse, and `pinned_spec_sha` field. PLUS `phase1-plan.md:65` (T11) re-authored to include all four amendments verbatim + `phase3-plan.md:77` (T6) encodes the regex + parse-fail discipline + `phase3-plan.md:6` cites §2 :43-52 + `phase2-plan.md:75` (was wrongly saying "Q3.1–Q3.4 NOT yet ratified"; ratification is verbatim per architecture-recommendation.md §2 :52). **Cure:** phase 1 T11 stamps `critic-verdict-schema.md` with the four amendments verbatim; phase 2 Cardinal #1 enforces them; phase 3 T2 wires `pinned_spec_sha` into the dispatch envelope.

### 6. **[CRITICAL] D4 encoded as designer discretion, contradicting the leader-ratified severity gate.**
**Cure file:line:** `phase3-plan.md:37` — anchor: **`D4 (severity-gated, two-branch)`** — table row replaces planner's D4=A "designer's choice" with the architect-ratified severity-gated terminal form: `advisory-only remaining → accept-with-disclosure` (two-place disclosure: `[REVIEW-CAVEAT]` line on the spec + `review_caveat:` field on the implement-brief page entry) + `any critical remaining → escalate-only` (structured gap report: artifact + 3 verdict blocks verbatim). Q4.4 refinement: `[CRITICAL] artifact incomplete / missing markers` WITH `truncated: true` is sketcher-internal, NOT a charged round. **Cure:** verbatim from architecture-recommendation.md §2 :55-59; phase 3 T6 implements.

### 7. **[CRITICAL] D5 binding has three competing formulations.**
**Cure file:line:** `phase1-plan.md:37` — anchor: **`D5 (re-keyed)`** — table row replaces the planner's `≥2 consecutive rounds` + `T5 :52 a third variant` with the architect-ratified form from architecture-recommendation.md §2 :62-72: `(a) lane_preference: text-native on the leader brief`, OR `(b) sketcher dispatch failed with the same error.code class on the initial call AND the one retry (Q5.4 exit)`; `other:user-requested-text-only` + `other:proxy-ceiling-N` structured enum extensions; Cardinal #7 enum tokens stay verbatim-untouched. **Cure:** verbatim from the architecture doc; the `≥2 consecutive rounds` form is explicitly rejected.

### 8. **[CRITICAL] Architect-mandated controls have no owning task (batch).**
**Cure file:line A (KV counter Cardinal):** `phase3-plan.md:40` — anchor: **`Cardinal required (architect §5.3): "I pin round_count`** — Phase 3 T6 owns the Cardinal. **Cure file:line B (verbatim-lift brief Cardinal):** `phase3-plan.md:41` — anchor: **`Cardinal required (architect §5.2): "When I re-dispatch sketcher`** — Phase 3 T6 owns the Cardinal. **Cure file:line C ([VISUAL-QA-DEFERRED] marker Cardinal):** `phase3-plan.md:42` — anchor: **`Cardinal required (architect §5.9): "When I emit a pass`** — Phase 3 T6 owns the Cardinal. **Cure file:line D (agent_registry_scan smoke):** `phase4-plan.md:27` — anchor: **`Authoring the verification surface for the parallel tester instance — Nit-2 pattern list`** — Nit-2 includes `agent_registry_scan` (per architecture-recommendation.md §3). **Cure file:line E (R19–R23 addendum):** `plan-overview.md:138-142` — anchor: **`R19 (architect §5.1)** — R19 (write leak), R20 (brief drift), R21 (revival bleed), R22 (visual-QA-deferred), R23 (boot-scan discoverability) added to risk register. **Cure:** architect §5.1/5.2/5.3/5.9/5.11 ratified controls now own tasks + risks; **finding 8a (KV counter lane resolution — binding):** counter pinned from DESIGNER's side (designer holds `shared_meta_kv`); critic stays read/denied `shared_meta_kv` (already in canonical D6 deny + rationale in `phase2-plan.md:91`).

### 9. **[CRITICAL] Verification surface incomplete — write-leak gate missing.**
**Cure file:line:** `phase4-plan.md:96` — anchor: **`Author verification-surface.md (≤120 lines) — the canonical test-runner**`** — T10 enumerates 8 Nit-2 pytest invocations (replaces the prior 5): `critic_meta`, **`critic_tools_resolve`** (the WRITE-LEAK GATE — asserts the EXACT resolved set `{read_file, image_get, image_list, explain_image, compare_images}`; an AC pinned at "4+1" can never pass because `image` resolves to 4 tools incl. `image_save`, hence `image_save` denied via `tools.deny`), `critic_deny_wins`, `critic_team_implied`, `designer_od_generate_removed`, `parity_runs_v2_schema`, `agent_registry_scan`. **Cure:** Nit-2 pattern list verbatim from architecture-recommendation.md §3 Nit 2; resolved-set enumeration is exact 5-tool set; mechanism (`image_save` bare entry in `tools.deny` strips the category expansion) cited at `daemon/tools/instance.py` resolution path.

### 10. **[CRITICAL] Phase1 decision table self-contradicts; T7 can't satisfy AC7.**
**Cure file:line:** `phase1-plan.md:36` — anchor: **`D1=B (apply)`** — table row explicitly supersedes the planner's D1=A "retain three on designer" decision. ALSO `phase1-plan.md:61` (T7) anchor: **`REWRITE the entire OD section :37-51`** + `research-findings.md:74` anchor: **`REWRITE the whole section per D1=B`** — `tools_note.md` rewrite scope expanded to the whole OD section. ALSO `phase1-plan.md:62` (T8) anchor: **`lockstep-move the Mockup Lane procedure`** — `skills-template/design-strategy.md` updated to mirror the post-phase-1 framing. **Cure:** retain-three decision SUPERSEDED; T7 expands to the whole OD section; T8 (skill) and T6 (workflow.md Phase 4 Mockup lane) move lockstep.

### 11. **[CRITICAL] view-views contradiction across the overview.**
**Cure file:line:** `plan-overview.md:40` — anchor: **`Adding view-views to critic (or any other agent) — deferred per Q6.1`** — explicit deferral line. PLUS `plan-overview.md:166` (SC-15) anchor: **`jq '.tools.allow \| index("design")' agents/critic/meta.json returns non-null. Cross-check:`** — replaces view-views with `design` allow entry plus cross-check that view-views is NOT granted. PLUS `phase2-plan.md:87` (T3 D6 row) anchor: **`NO view-views in tools.allow (Q6.1 deferred)`** — explicit. **Cure:** view-views DEFERRED (Q6.1); SC-15 = `jq '.tools.allow \| index("design")'` non-null + cross-check that `index("view-views")` returns null (proves deferral honored).

---

## Optional findings (12–20)

### 12. **[OPTIONAL] SC-17 greps wrong directory — SUPERSEDED marker placement.**
**Cure file:line:** `phase1-plan.md:66` (T12) — anchor: **`Add SUPERSEDED marker (single-line HTML-comment append)`** — SUPERSEDED marker is added in PHASE 1, not phase 4. PLUS `phase4-plan.md:63` (T1 re-verify) anchor: **`Confirm phase-1 T12 SUPERSEDED marker is present on stage2-addendum.md`** — phase 4 verifies but does not re-edit. PLUS `plan-overview.md:168` (SC-17) anchor: **`head -1 .agents/shared/planning/od-generate-agent-lane/stage2-addendum.md`** — verification path targets the actual addendum file. **Cure:** marker placed in correct file by correct phase; re-pointed verification path; phase 4 owns no additional edit.

### 13. **[OPTIONAL] D7 vocabulary drift.**
**Cure file:line:** `phase4-plan.md:38` — anchor: **`D7 = UNTRACKED-docs (Option C, RATIFIED) — replaces Options X/Y`** — table row unifies on Option C; `Option X`/`Option Y` are explicitly rejected. PLUS `phase4-plan.md:76` (T4) anchor: **`Use Option C vocabulary consistently — the prior plan's Option X / Option Y references`** — task description enforces Option C throughout. PLUS `phase4-plan.md:116` (AC4) anchor: **`grep -n "Option C" context-pointer-architecture-recommendation.md returns ≥ 1; grep -n "Option X\|Option Y" ... returns 0`** — verification forbids X/Y in the pointer doc. **Cure:** Option C vocabulary unified; X/Y references marked SUPERSEDED.

### 14. **[OPTIONAL] SC-12 mischaracterizes its gate.**
**Cure file:line:** `plan-overview.md:163` (SC-12) — anchor: **`The reworked tests/unit/plugin_subsystem/test_designer_rewire.py`** — SC-12 re-points to the phase-4-tester-owned reworked rewire test (finding 2). **Cure:** re-points from the daemon-side seam probe (which doesn't read designer prose) to the reworked pytest that pins the inverse rewire.

### 15. **[OPTIONAL] SC-9 specifies nonexistent `python -m daemon.registry --list-agents`.**
**Cure file:line:** `plan-overview.md:160` (SC-9) — anchor: **`Registry discovery scans agents/critic/meta.json and returns the new agent (agent_registry_scan pattern`** — replaced with the Nit-2 `agent_registry_scan` probe per architecture-recommendation.md §3 (counterpart to the tier-1 boot-scan fix `efc460262`). **Cure:** specifies the actual discovery check via the Nit-2 pattern.

### 16. **[OPTIONAL] open-design-decisions.md top status line.**
**Cure file:line:** `open-design-decisions.md:6` — anchor: **`Status: 🟢 RATIFIED 2026-10-10`** — status flipped from 🟡 DRAFT to 🟢 RATIFIED. PLUS `open-design-decisions.md:8` — anchor: **`Adjudication pointer. Every D1–D7 verdict, every open-question resolution`** — adjudication pointer added at top. **Cure:** status stamped + adjudication pointer added; body preserved as historical decision record (NOT rewritten).

### 17. **[OPTIONAL] Verdict-schema canonical home vs convention v2.**
**Cure file:line:** `phase2-plan.md:90` (T6 workflow.md) — anchor: **`## Review — CANONICAL HOME OF THE VERDICT SCHEMA`** — workflow.md Review section explicitly declared canonical home. PLUS `phase2-plan.md:109` (AC6) anchor: **`workflow.md ## Review (canonical home) section present and quotes the schema verbatim; AC5 grep updated`** — updated grep checks `^## Review\b` + `verdict:\s*\`?(pass|needs-revision)\`?` + file ≤ 60 lines; the prior `verdict block .md` path-grep is RETIRED. **Cure:** critic's `workflow.md` `## Review` declared canonical home; planning-dir doc is frozen pin by section reference; AC5 grep updated to anchor on the section heading + the verdict-line (no `.md` filename token).

### 18. **[OPTIONAL] Phase3 T7 section-cursor ambiguity.**
**Cure file:line:** `phase3-plan.md:78` (T7) — anchor: **`Phase 3 owns :103+ through approximately :148`** — section-cursor tightened to `:103+~148` (extended boundary to capture Dual-Run Pilot DELETE; finding 4). **Cure:** phase 3 boundary explicit; phase 3 owns `:103+` ONLY (never `:1-102`).

### 19. **[OPTIONAL] Effective-team doc note missing.**
**Cure file:line:** `phase2-plan.md:91` (T7 tools_note.md) — anchor: **`DECLARED vs EFFECTIVE team note (mandatory section)`** — task description mandates the DECLARED `team_members: []` vs EFFECTIVE auto-extended `image-comparator` note (per `_auth.py:155-161`). PLUS `phase2-plan.md:110` (AC7) anchor: **`grep -n DECLARED\|EFFECTIVE agents/critic/tools_note.md`** — verification confirms DECLARED and EFFECTIVE both present. **Cure:** doc note required; verification enforces it.

### 20. **[OPTIONAL] Phase1 T3 dispatch-level decision (team_members add timing).**
**Cure file:line:** `phase1-plan.md:57` (T3) — anchor: **`Add "critic" to team_members at :22 IN THIS TASK (default per finding 20`** — meta.json team_members add happens in phase 1 (default resolved; the "defer to phase 2" alternative is rejected). PLUS `phase1-plan.md:40` (Phase 1 local row) anchor: **`team_members add in phase 1 (default per finding 20)`**. **Cure:** phase 1 T3 adds `"critic"`; phase 2 T3 verifies.

---

## NOTE (21)

### 21. **[NOTE] Typos surviving into Cardinal text.**
**Cure file:line A (phase2 dispath-fresh-iteration):** `phase2-plan.md:47` — anchor: **`treats pass as accept and needs-revision as dispatch-fresh-iteration instructions`** — Cardinal #1 uses correct `dispatch-fresh-iteration`. **Cure file:line B (phase3 critic-revoking → critic-invoking):** `phase3-plan.md:75` (T4) — anchor: **`od.generate-bearing OR critic-invoking children`** — typo corrected; AC4 enforces zero `critic-revoking`. **Cure file:line C (SC-14 ls -la → sha1sum):** `phase4-plan.md:120` (AC10) — anchor: **`sha1sum /home/nea/ensemble-src/agents/designer/meta.json`** — uses `sha1sum` (not `ls -la`) per finding 21. **Cure:** all three typos corrected.

---

## Conflict notes (architect doc wins)

None. Every finding cure cites the architecture-recommendation.md binding text verbatim; no re-litigation of D1–D7 was attempted. Where a finding and the arch doc seemed to conflict (none did), the arch doc wins.

---

## Phase-plan-to-architecture-doc section coverage (post-cure)

| Phase | Architecture-recommendation.md sections it cites (verbatim) |
|-------|--------------------------------------------------------------|
| Phase 1 | §1 verdict table D1 row + §2 D1 (`:29-35`, broad removal) + §2 D3 (`:43-52`, schema pin + 4 amendments) + §2 D5 (`:62-72`, re-keyed text-lane binding) + §2 D7 (`:105-109`, SUPERSEDED marker) |
| Phase 2 | §1 verdict table D3 row + §2 D3 (`:43-52`, all 4 amendments verbatim) + §2 D6 (`:74-104`, canonical form + `image_save` deny + Q6.1 deferral) + §3 (Nit-2 — feeds AC2 resolved-tool-set enumeration) |
| Phase 3 | §1 verdict table D2/D3/D4/D5/D7 rows + §2 D2 (`:37-41`, async by default) + §2 D3 (`:43-52`, regex + parse-fail + `pinned_spec_sha`) + §2 D4 (`:54-60`, severity-gated two-branch + Q4.4 truncation-class discipline) + §2 D5 (`:62-72`, re-keyed binding — verbatim lift from phase 1) + §5 §5.3 (KV round-counter Cardinal) + §5 §5.2 (verbatim-lift brief Cardinal) + §5 §5.9 ([VISUAL-QA-DEFERRED] Cardinal) + §5 §5.4 (watchover dropped) |
| Phase 4 | §1 verdict table D6/D7 rows + §2 D6 (`:74-104`, resolved-tool-set 5-tool equality check) + §2 D7 (`:105-109`, Option C + capture_mockup spec-only + cap-math deferred) + §3 Nit-2 (verification pattern list — replaces prior 5 with full Nit-2 list of 8 invocations incl. `agent_registry_scan`) + §3 Nit 1 (note: D6 bare-name vs phase2 category phrasing — the conflict is RESOLVED as canonical hybrid) |

Every verdict from architecture-recommendation.md §1 D1–D7 verdict table is consumed by at least one phase. Every §2 per-decision ruling (D1, D2, D3, D4, D5, D6, D7) is cited verbatim in the phase plans. Every §3 curated open question (Q1.1, Q2.3, Q3.1–Q3.4, Q4.2, Q5.1–Q5.4, Q6.1–Q6.4, Q7.1–Q7.4) is resolved or explicitly deferred. The §5 architect-identified risks 5.1–5.10 are folded into R19–R23 of `plan-overview.md` risk register + the architect-mandated controls have owning tasks in phase 3 T6.

---

## Pass 3 — verification-surface fixes

**Date:** 2026-10-10
**Source-of-truth:** Council report on the 22-gate false-red/false-green + 6-NEW-critical verification-surface defect batch.
**Scope:** 6 NEW criticals (findings 1–6) + 9 optionals (findings 7–15) = 15 findings. Every gate authored or modified has run-the-gate evidence below; pure remaps have consistency-check evidence.
**Convention adopted:** **ERE with unescaped `|`** for alternation (the `\|` form in markdown source was rendering as literal pipes in -E grep, producing false-greens like SC-5's "0 hits" against a target that has 2 matches). **cwd assumption for all frozen commands:** `cd /home/nea/ensemble-src-wt-designer-critic-orchestration` first (worktree hard boundary — constraint **C5**; main-checkout edits forbidden).

### 1. **[CRITICAL] Systemic broken grep alternation — 22 gates false-red or false-green.**
**Cure file:line A:** `plan-overview.md:156` (SC-5) — anchor: **"`grep -nE "tool-not-bound|call-error|timeout|daemon-unavailable|other:"`"** — alternation `\|`→`|`, plus positive-control line proving the literal-pipe form is false-green. **Cure file:line B:** `plan-overview.md:158` (SC-7) — anchor: **"`grep -nE 'meta\.json|tools\.allow|daemon/|skill-set\.yaml|seeder|version registry|test paths'`"** — alternation `\|`→`|`. **Cure file:line C:** `plan-overview.md:159` (SC-8) — anchor: **"`grep -nE "verdict block|prev_attempt_unparseable|pinned_spec_sha|verdict:\s*\((pass|needs-revision)\)"`"** — alternation + paren-escape re-derived (literal `\\\\(` was false-green; written the new correct paren-escapes `\(` `\)` and inner alternation). **Cure file:line D:** `plan-overview.md:162` (SC-11) — anchor: **"`grep -nE "sketcher.*critic|critic.*sketcher"`"**. **Cure file:line E:** `plan-overview.md:166` (SC-15) — anchor: **"`jq '.tools.allow | index("design")'`"** — jq pipe preserved (jq is its own DSL). **Cure file:line F:** `plan-overview.md:170` (SC-19) — anchor: **"`grep -nE "^## Review|^## Verdict|verdict:\s*(pass|needs-revision)"`"**. **Cure file:line G:** `plan-overview.md:171` (SC-20) — anchor: **"`grep -nE "shared_meta_kv|round_count|verbatim|VISUAL-QA-DEFERRED"`"**. **Cure file:line H:** `plan-overview.md:172` (SC-21) — anchor: **"`grep -cE "\[BRIEF-LEVEL\]|prev_attempt_unparseable|verdict:\s*\((pass|needs-revision)\)|pinned_spec_sha"`"** — third alternative's `\\\\(` form was replaced; tests now format accordingly. **Cure file:line I:** `phase1-plan.md:76-79,85` (AC3, AC4, AC5, AC6, AC12) — same alternation fix; AC12 third alternative rewritten. **Cure file:line J:** `phase2-plan.md:106-109,115` (AC3, AC5, AC6, AC12) — same alternation fix. **Cure file:line K:** `phase2-plan.md:90` (T6 prose) — `\|` in markdown-source description of the regex command removed so the command copy-pastes correctly. **Cure file:line L:** `phase3-plan.md:93,94,95,98` (AC4, AC5, AC6, AC9) — same alternation fix. **Cure file:line M:** `phase4-plan.md:114,116,119,121` (AC2, AC4, AC7, AC9) — same alternation fix. **Cure:** every -E gate uses unescaped `|` for alternation; bare-filename greps (`critic-verdict-schema.md`, `*.md`) replaced by full worktree-relative paths.
**Evidence (run-the-gate):**
- `cd /home/nea/ensemble-src-wt-designer-critic-orchestration && grep -cE "tool-not-bound\|call-error\|timeout\|daemon-unavailable\|other:" agents/designer/rule.md` returns **0** (literal-pipe false-green — would have failed today's pre-implementation state too). `grep -cE "tool-not-bound|call-error|timeout|daemon-unavailable|other:" agents/designer/rule.md` returns **2** (rule.md lines 15 + 38). Toggled between `\|` (0) and `|` (2) — proven live today.
- `cd /home/nea/ensemble-src-wt-designer-critic-orchestration && grep -cE "\[BRIEF-LEVEL\]|prev_attempt_unparseable|verdict:\s*\((pass|needs-revision)\)|pinned_spec_sha" .agents/shared/planning/designer-critic-orchestration/architecture-recommendation.md` returns **6** (lines 3, 47, 51, 75, 124 — the four-keyword alternation is real and finds all four anchor points).
- `cd /home/nea/ensemble-src-wt-designer-critic-orchestration && grep -cE "shared_meta_kv\|round_count\|verbatim\|VISUAL-QA-DEFERRED" agents/designer/rule.md` returns **0** (literal-pipe false-green — pre-implementation). `grep -cE "shared_meta_kv|round_count|verbatim|VISUAL-QA-DEFERRED" agents/designer/rule.md` returns **0** (clean alternation, also 0 — the controls are correctly absent pre-edit, will be added by phase 3 Guidelines (e/f/g)). Toggled proves the alternation mechanic; same result because the keywords are simply absent today.

### 2. **[CRITICAL] SC-9's replacement probe is a second phantom surface.**
**Cure file:line A:** `plan-overview.md:160` (SC-9) — anchor: **"`python3 -c "from daemon.registry import get_registry; assert get_registry().exists('critic')"`"** — pointed at the real `daemon.registry.AgentRegistry.get_registry()` facade at `daemon/registry.py:1207` with `exists()` at `:1191`. **Cure file:line B:** `plan-overview.md:142` (R23 mitigation) — anchor: **"`python3 -c "from daemon.registry import get_registry; assert get_registry().exists('critic')"` probe — uses the real `daemon.registry.AgentRegistry.exists()` at `daemon/registry.py:1191`"** — same real probe. **Cure:** both surfaces now reference the real `get_registry()`/`exists()` API (verified by `grep -n "def get_registry\|def exists" daemon/registry.py` returning 1207 and 1191 respectively); `PluginRegistry.load_registry` is NOT used (it requires positional `plugins_root` per `daemon/plugin_subsystem/plugin_registry.py:561` + scans plugins not agents); `daemon.registry` has no module-level `registry` symbol (only `_registry: AgentRegistry | None = None` at :1204).
**Evidence (run-the-gate):**
- `cd /home/nea/ensemble-src-wt-designer-critic-orchestration && grep -n "def get_registry\|def exists" daemon/registry.py` returns **1207** (get_registry) + **1191** (exists) — call sites verified live today.
- `cd /home/nea/ensemble-src-wt-designer-critic-orchestration && grep -n "def load_registry" daemon/plugin_subsystem/plugin_registry.py` returns **561** (positional `plugins_root` required — `load_registry(plugins_root: Path, *, validate_tree: bool = True)` — proves the prior SC-9 probe was unsatisfiable as written).
- `python3 -c "from daemon.registry import get_registry; r = get_registry(); assert r.exists('sketcher')"` returns **ModuleNotFoundError: No module named 'pydantic'** in this host (no venv). With pydantic available, the same probe with `'critic'` would return **AssertionError** today (critic dir absent — the gate is real, will pass post-phase-2). Positive-control `assert r.exists('sketcher')` would return 0 (sketcher exists today).

### 3. **[CRITICAL] Phase-3 Cardinal replacement mechanics contradict actual rule.md and plan-overview A7.**
**Cure file:line A:** `phase3-plan.md:46-58` (Cardinal numbering table) — anchor: **"Total numbered Cardinals after phase 3: 7 (cap preserved)**. Cardinal numbering MUST NOT mutate the pre-existing numbers; renumbering breaks the load-bearing tests. Phase 3's three architect-mandated controls (KV round-counter + verbatim-lift brief + [VISUAL-QA-DEFERRED] marker) are encoded as **Guidelines (e), (f), (g)** — NOT as numbered Cardinals"** — re-derived slot table row-by-row from the REAL `agents/designer/rule.md` (slots 1–7 mapped to today's actual Cardinals; slot 1 = `pinned_spec_sha` load-bearing for D3 amendment 4; slot 5 = "End turn after `send_message`" A7-protected). **Cure file:line B:** `phase3-plan.md:40-42` (Phase 3 local decisions) — anchor: **"**Guideline (e)** (NOT a Cardinal — Cardinal cap is 7 per writing guide; the project's `rule.md` has 7 numbered slots; routing via Cardinal would require replacing a load-bearing existing Cardinal like #1 `pinned_spec_sha` (protected by D3 amendment 4) or #5 \"End turn after `send_message`\" (protected by A7) — both forbidden)"** + Guideline (f)/(g) likewise. **Cure file:line C:** `phase3-plan.md:43` (Guideline (h)) — separate affordance text re-authored per finding 4 (advisory-disclosure + critical-escalate-only, NOT free-option-at-cap). **Cure file:line D:** `phase3-plan.md:74` (T6) — anchor: **"`rule.md` has 7 numbered Cardinals (no mutation); four new Guidelines (e), (f), (g), (h) added"** + T6 also edits Guidelines not Cardinals. **Cure:** overflow controls encoded as Guidelines (e/f/g/h), NOT Cardinals; 7 numbered Cardinals stay 7; `pinned_spec_sha` and "End turn after `send_message`" both survive unchanged.
**Evidence (run-the-gate):**
- `cd /home/nea/ensemble-src-wt-designer-critic-orchestration && grep -cE '^[0-9]+\.' agents/designer/rule.md` returns **7** today (slots 1-7 occupied). Positive control for SC-20 cap.
- `cd /home/nea/ensemble-src-wt-designer-critic-orchestration && grep -nE '^[0-9]+\.' agents/designer/rule.md` returns lines **9-15** (slot 1 = `pinned_spec_sha`, slot 5 = "End turn after `send_message`") — proves the table mapping matches reality and both are load-bearing.
- Slot 5 mislabel `#7 binding` → corrected to **"End turn after `send_message` (sketcher + critic) — A7 PROTECTED"** in the table.

### 4. **[CRITICAL] Guideline (e) draft text re-introduces the finding-6 defect it must encode away.**
**Cure file:line:** `phase3-plan.md:43` (Phase 3 local decision — accept-with-disclosure) — anchor: **"On round 3 of the `sketcher → critic` loop per page (D4 cap): when ONLY advisory findings remain, I MAY accept-with-disclosure: ship the third artifact, record `[REVIEW-CAVEAT]` line on the spec + `review_caveat:` field on the implement-brief page entry, and continue. The override is logged; conformance can audit it. I may NOT suppress the verdict line itself. **Any critical remaining at cap → escalate-only; accept-with-disclosure is unavailable — the severity gate bans overriding a critical finding."** — closed-form trigger `"advisory-only remaining → accept-with-disclosure"` plus separate sentence `"any critical at cap → escalate; acceptance unavailable"`; the free-option-at-cap framing that contradicts the severity gate is explicitly REJECTED.
**Cure:** trigger text re-authored; the D4 row at `:37` is the binding severity-gated two-branch (advisory-disclosure / critical-escalate-only); Guideline (h) is the affordance surface, not a free option. No gate command authored for this finding (pure prose re-author + structural alignment); consistency check: `grep -n "free-option-at-cap\|may accept-with-disclosure" phase3-plan.md` returns 0 (the rejected framing is not present in post-edit text).

### 5. **[CRITICAL] The Nit-2 verification tests have no authoring task.**
**Cure file:line:** `phase4-plan.md:58` (T9b) — anchor: **"**T9b** | **Nit-2 verification tests authoring task (per finding 5 — extends T9, owned by tester instance).** The Nit-2 pytest invocations enumerated in T10 reference functions that must exist in the test pack."** — new T9b authored as the test-authoring companion to T9 (the rework task); enumerates the 7 Nit-2 functions and their placements (`tests/unit/agents/test_sketcher_agent.py` for the 5 agent + pipeline tests, `tests/unit/plugin_subsystem/test_designer_rewire.py` for the 2 write-leak-gate + designer-od tests). **Cure:** R23 alignment preserved (`agent_registry_scan` lands in `tests/unit/agents/test_sketcher_agent.py`); T10's count gate `grep -c "pytest -k"` is no longer undermined by empty `pytest -k` matching (zero tests → exit 5 → false-red noise). No gate command authored; consistency check: T9 + T9b now jointly cover the 7 Nit-2 patterns enumerated in T10 (each `pytest -k "name"` finds its authored function).

### 6. **[CRITICAL] Phase-1 task→AC mapping off-by-one from T6 onward.**
**Cure file:line:** `phase1-plan.md:59-66` (T5–T12 AC column) — anchor: **"T5 → AC4, AC5, AC6"** (added AC6 — Cardinal #4 references `critic`) **"T6 → AC7"** (was AC6) **"T7 → AC8"** (was AC7) **"T8 → AC9"** (was AC8) **"T9 → AC10"** (was AC9) **"T10 → AC11"** (was AC10) **"T11 → AC12"** (was AC11) **"T12 → AC13"** (was AC12). **Cure:** every AC claimed by exactly one task; every task claiming only existing ACs; AC table unchanged. No gate command; consistency check: `grep -nE "AC[0-9]+" phase1-plan.md` shows T5 claims AC4+AC5+AC6 (rule.md scope), T6→AC7 (workflow.md scope), T7→AC8 (tools_note.md scope), T8→AC9 (skills-template scope), T9→AC10 (pytest scope), T10→AC11 (snapshot scope), T11→AC12 (verdict schema scope), T12→AC13 (SUPERSEDED marker scope) — mapping aligns with task outcomes.

### 7. **[OPTIONAL] workflow.md:112 secondary signal — broaden SC-3.**
**Cure file:line A:** `plan-overview.md:154` (SC-3) — anchor: **"**Secondary signal (broader, per finding 7):** `grep -niE "stay on my own direct|run it on my own direct" agents/designer/workflow.md` returns 0 hits"** — broadened secondary. **Cure file:line B:** `plan-overview.md:169` (SC-18) — anchor: **"**Secondary signal (broader, per finding 7):** `grep -niE "stay on my own direct|run it on my own direct" agents/designer/soul.md` returns 0 hits"** — same broadening.
**Evidence (run-the-gate):**
- `cd /home/nea/ensemble-src-wt-designer-critic-orchestration && grep -nE "stay on my own direct|run it on my own direct" agents/designer/workflow.md` returns **2 lines** today (105 + 112) — positive control proves the broadened pattern is real, not false-green; both `:105` ("Single-page one-offs stay on my own direct `od.generate`") and `:112` ("run it on my own direct `od.generate`") are caught.
- `cd /home/nea/ensemble-src-wt-designer-critic-orchestration && grep -nE "stay on my own direct|run it on my own direct" agents/designer/soul.md` returns **1 line** today (`:55`) — same positive control; soul's single occurrence is caught.

### 8. **[OPTIONAL] T9(c) rejects `critic_verdict` as a parity-runs.jsonl field; false attributions.**
**Cure file:line:** `phase4-plan.md:57` (T9(c)) — anchor: **"the `unexpected-field assert` REJECTS `critic_verdict` (**planner decision, per finding 8 — verdict lives on the review itself, not as a `parity-runs.jsonl` field; this is a fresh planner-side reversal, not a finding-2 / Q3 / arch-Nit-2 ruling, those don't speak to it**)"** — explicit planner decision attributed correctly; the false attribution to "finding 2 / Q3 schema / arch Nit 2" is struck. **Cure:** reversal recorded as an explicit planner decision in the test-edit task prose. No gate command; consistency check: T9(c) text now correctly attributes the rejection to a fresh planner decision; the architecture doc's Nit-2 ruling remains `lane ∈ {"sketcher","critic"}` only (no `critic_verdict` field ruling).

### 9. **[OPTIONAL] Phase-3 AC-column drift.**
**Cure file:line:** `phase3-plan.md:72-77` (T4, T5, T6, T7b, T8 AC column) — anchor: **"T4 → AC2, AC4"** (was AC2, AC3 — wait-timeout → AC4) **"T5 → AC2, AC5"** (was AC2, AC4 — report handling → AC5) **"T6 → AC2, AC5, AC6, AC9"** (added AC9 — Guideline/Cap → AC9) **"T7b → AC7"** (was AC8 — DELETE → AC7) **"T8 → AC8"** (was AC9 — pytest → AC8). **Cure:** every AC claimed by exactly one task; every task claiming only existing ACs. No gate command; consistency check: AC7 = `^## Dual-Run Pilot` grep (T7b's delete target) + section-outline grep (T7's renumber task) — both share AC7 because both target the same `workflow.md` outline; T7b was claiming AC8 (pytest) erroneously and is now correctly AC7.

### 10. **[OPTIONAL] Phase-1 AC7's `diff` is unexecutable.**
**Cure file:line:** `phase1-plan.md:80` (AC7) — anchor: **"`sed -n '103,$p' agents/designer/workflow.md | sha1sum` matches the pre-edit `phase1-snapshot.json` sha1sum of lines `:103+` (byte-identical to pre-edit snapshot). **Restated (per finding 10):** T2 snapshot stores `{file, lines, sha1}` per file — the byte content itself is reconstructed by re-running `sha1sum` against the post-edit file."** — `diff` of `phase1-snapshot.json.workflow_pre[103:]` (which doesn't exist as bytes — only sha1 stored) replaced with `sed -n '103,$p' | sha1sum` comparison against pre-edit snapshot sha1. **Cure:** gate now references only data T2 actually captures (sha1sum). No new evidence command; consistency check: T2 outcome column enumerates `{file, lines, sha1}` per file — sha1 is the only thing available for byte-identity comparison.

### 11. **[OPTIONAL] Stale retain-three row at research-findings.md:263.**
**Cure file:line:** `research-findings.md:263` — anchor: **"**D6** (designer retain `od.compose_brief`/`od.save`/`od.lint`) **<!-- SUPERSEDED 2026-10-10 by D1=B (broad removal) — designer holds zero `od.*` per architecture-recommendation.md §2 :29-35; see plan-overview.md §2.1 in-scope + plan-direction.md → D1=B -->**, **D7** (planning-doc supersession)"** — single-line HTML-comment SUPERSEDED marker appended to the retain-three row, contradicting `:74` and the now-binding D1=B ruling. **Cure:** contradiction resolved; SUPERSEDED marker visible inline. No gate command; consistency check: `grep -c "SUPERSEDED" research-findings.md` ≥ 1 + the §2.5 row at `:74` is the canonical "no `od.*` left" statement — both now consistent with D1=B.

### 12. **[OPTIONAL] "8 invocations" count drift.**
**Cure file:line A:** `phase4-plan.md:97` (T10) — anchor: **"**Nit-2 pytest invocations** (architect §3 Nit 2; 7 patterns + 2 supplemental = 9 total `pytest -k` invocations per finding 12)"** — count text "8" → "7 + 2 = 9"; supplemental commands prefixed with `pytest ` so all 9 are `pytest -k`-prefixed. **Cure file:line B:** `phase4-plan.md:121` (AC8) — anchor: **"`grep -o "pytest -k" verification-surface.md | wc -l` ≥ 7** (Nit-2 patterns only) — note: counts OCCURRENCES not lines, since the 7-9 invocations may render on one line (per finding 12 — `grep -c "pytest -k"` counts LINES not occurrences, which would false-red at 1)"** — `grep -c` (line-count) replaced with `grep -o | wc -l` (occurrence-count); threshold ≥7; positive-control on `phase4-plan.md` returns 14.
**Evidence (run-the-gate):**
- `cd /home/nea/ensemble-src-wt-designer-critic-orchestration && grep -c "pytest -k" .agents/shared/planning/designer-critic-orchestration/phase4-plan.md` returns **3 lines** (T9b prose + T10 row + AC8 row) — proves `grep -c` line-count is too narrow; the 9 invocations all sit on the T10 line.
- `cd /home/nea/ensemble-src-wt-designer-critic-orchestration && grep -oP "pytest.{0,2}k" .agents/shared/planning/designer-critic-orchestration/phase4-plan.md | wc -l` returns **14** (7 Nit-2 + 2 supplemental + 5 descriptive references in T9b prose / AC8 text / T10 row itself) — proves the occurrence-count threshold is real.
- T10 text + AC8 grep + count are mutually consistent: 9 invocations, all `pytest -k`-prefixed, threshold ≥7.

### 13. **[OPTIONAL] Stale T9 cross-refs.**
**Cure file:line A:** `phase2-plan.md:127` — anchor: **"its verification path lives in phase 4 T10 (`verification-surface.md`)."** — was "phase 4 T9". **Cure file:line B:** `phase4-plan.md:45` (Phase 4 local — Verification surface) — anchor: **"**Phase 4 T10 below expanded from the prior plan's 5 invocations to the full Nit-2 list** (finding 9)"** — was "Phase 4 T9". **Cure:** the verification surface lives in T10 (T9 is the test-edit task owned by tester); both cross-refs now point to T10. No gate command; consistency check: `grep -n "Phase 4 T10\|phase 4 T10" .agents/shared/planning/designer-critic-orchestration/*.md` resolves to the verification-surface task in phase4-plan.md:97.

### 14. **[OPTIONAL] workflow.md:149-225 tail claimed by no phase.**
**Cure file:line:** `plan-overview.md:107` (Strong cross-phase couplings, item 1) — anchor: **"Phase 3 owns `:103+` through approximately `:148`** (extended to capture the Dual-Run Pilot `:133-145` delete; per finding 14, T7b may also delete `:132-147` inclusive to clean up the orphaned `---` at `:147`). Phase 3's boundary stops at `:148`; **`:149+`** (`## Phase 5 — Self-Review` through `## Dispatch: Workers Only` at `:225`) is **not** claimed by any phase and stays untouched by this commission."** — boundary sentence added; T7b's delete scope clarified to `:132-147` (inclusive) to clean up the orphaned `---` at `:147`. **Cure:** explicit non-claim on `:149-225`; T7b's delete scope widened to include the orphaned `---`. No gate command; consistency check: `cd /home/nea/ensemble-src-wt-designer-critic-orchestration && grep -n "^## " agents/designer/workflow.md` shows `:103` (Orchestration), `:133` (Dual-Run Pilot — T7b target), `:149` (Phase 5), `:163` (Phase 6), `:171` (Phase 7), `:182` (States), `:195` (In-Flight), `:201` (Audit), `:220` (Dispatch) — phase 3 owns `:103-148`; the rest is unclaimed by this commission.

### 15. **[OPTIONAL] Residue bundle.**
**Cure file:line A:** `phase2-plan.md:89` (T5 AC column) — anchor: **"T5 → AC5"** (was AC5, AC6 — double-claim with T6; now AC5 only). **Cure file:line B:** `phase2-plan.md:95` (T11 outcome) — anchor: **"The pytest invocation returns 0"** (was "Both pytest invocations" — only one command listed). **Cure file:line C:** `phase4-plan.md:88` (T6) — anchor: **"`git log` shows three phase commits before T14 + the fourth (phase 4) commit after T14"** (was "four top commits" before T14 creates the fourth). **Cure file:line D:** `phase2-plan.md:90,109` (T6 + AC6) — anchor: **"**AC6 grep:**"** + **"the grep checks the `## Review` heading"** (stale "AC5 grep updated" labels renamed to AC6 grep; the prior plan mislabeled the AC reference). **Cure file:line E:** `phase3-plan.md:74` (T6 (h')) — anchor: **"(h') Q5.4 procedural tail (per finding 15) — sketcher re-dispatch retry carries `notes: prev_error_code=<code>` in the dispatch envelope; same `error.code` class on the retry → escalate (proxy-ceiling-blocked, not transient); different codes between attempts = flapping → one more attempt permitted (the third dispatch carries the prior attempt's code AND notes: prev_error_code=<prior_code> for flapping-context);"** — D5 Q5.4 procedural tail lifted from `architecture-recommendation.md:71-72` into T6(h'). **Cure file:line F:** `phase4-plan.md:36` (D7 pilot-gates) — anchor: **"Add a second SUPERSEDED line in phase 4"** — typo "SUPERSEDED" (with double-P) corrected to **"SUPERSEDED"** (single-P). **Cure:** residue bundle cleaned; consistency checks: `cd /home/nea/ensemble-src-wt-designer-critic-orchestration && grep -n "SUPPERSEDED" .agents/shared/planning/designer-critic-orchestration/phase4-plan.md` returns 0 (typo gone), `grep -n "Both pytest invocations" .agents/shared/planning/designer-critic-orchestration/phase2-plan.md` returns 0 (T11 text now consistent with single command), `grep -n "four top commits" .agents/shared/planning/designer-critic-orchestration/phase4-plan.md` returns 0 (T6 no longer over-commits), `grep -n "prev_error_code" .agents/shared/planning/designer-critic-orchestration/phase3-plan.md` returns 1 (Q5.4 procedural tail now present in T6).

---

**End of Pass 3 cure map.** All 15 findings cured; no re-litigation; arch doc wins; cure-by-evidence — every gate has run-the-gate evidence above; pure remaps have consistency-check evidence.

---

## Pass-3 addendum: SC-9 probe interpreter qualification

**Date:** 2026-10-10
**Scope:** the SC-9 / R23 / T9b frozen probe as written in Pass-3 (`python3 -c "from daemon.registry import get_registry; assert get_registry().exists('critic')"`) cannot execute on this host — system `python3` lacks `pydantic`, and the worktree has no `.venv`. Verified working form:

**Working form (positive control, executed today):**
- `cd /home/nea/ensemble-src-wt-designer-critic-orchestration && /home/nea/ensemble-src/.venv/bin/python3 -c "from daemon.registry import get_registry; assert get_registry().exists('sketcher'); print('probe-ok')"`
- Returns: `probe-ok` (sketcher exists today — probe is real, gate machinery works)

**Working form (frozen target, executed today — expected to FAIL until phase 2 lands):**
- `cd /home/nea/ensemble-src-wt-designer-critic-orchestration && /home/nea/ensemble-src/.venv/bin/python3 -c "from daemon.registry import get_registry; assert get_registry().exists('critic')"`
- Returns: `AssertionError` (`agents/critic/` does not exist yet — that IS the gate working; will return 0 post-phase-2 when critic is added to `agents/`)

**Cure:** every occurrence of the probe is updated to prefix the venv interpreter path `/home/nea/ensemble-src/.venv/bin/python3` so the frozen command is runnable verbatim. cwd assumption line unchanged (`cd /home/nea/ensemble-src-wt-designer-critic-orchestration` first).

**Updated probe locations:**
- `plan-overview.md:142` (R23 row) — `/home/nea/ensemble-src/.venv/bin/python3 -c "from daemon.registry import get_registry; assert get_registry().exists('critic')"` (note added: venv prefix REQUIRED on this host).
- `plan-overview.md:160` (SC-9) — same probe + positive-control form with `'sketcher'` (both venv-prefixed).
- `phase4-plan.md:58` (T9b, item v) — same probe (venv-prefixed); note added that `test_agent_registry_scan` must invoke the venv interpreter.

**No other changes** — alternation convention, Cardinal remap, kill-gates, all 15 Pass-3 findings unchanged. The Pass-3 cure-map section above remains authoritative; this addendum qualifies the SC-9/R23/T9b interpreter path only.

---

## Pass 4 — final micro-fixes (2026-10-10, pre-approver)

All fixes applied to PLAN MARKDOWN ONLY, inside `.agents/shared/planning/designer-critic-orchestration/`. No agents/**, tests/**, daemon/**, or main-checkout writes. Every command below was EXECUTED in the plan dir on 2026-10-10; "actual" = verbatim output.

### Fix 1 (CRITICAL) — bare `python` → venv interpreter
- **`plan-overview.md:157`** (SC-6) — anchor: "`python -c 'import json; d=json.load`" → replaced with "`/home/nea/ensemble-src/.venv/bin/python3 -c 'import json; d=json.load`".
- **`phase2-plan.md:105`** (AC2) — anchor: "`python -c 'import json; cd=json.load`" → same venv prefix; trailing note added: "(venv interpreter `/home/nea/ensemble-src/.venv/bin/python3` REQUIRED on this host — see plan-overview.md SC-9)".
- **Other bare-`python` sweep:** `grep -n -E '(^|[^a-zA-Z0-9_./-])python([^a-zA-Z0-9_-]|$)' *.md` (minus `python3`) → only 4 remaining hits, ALL descriptions of the retired nonexistent `python -m daemon.registry --list-agents` pattern (`phase4-plan.md:27`, `phase4-plan.md:97`, `plan-overview.md:98`, `review-cure-map.md:60` cure-item title). Intentionally unchanged — they name the replaced pattern, none is an executable recipe.
- **Premise verified:** `command -v python` → not found (bare `python` = EXIT 127 on this host); `/home/nea/ensemble-src/.venv/bin/python3 -c "import pydantic"` → `venv pydantic OK`.

### Fix 2 (CRITICAL) — AC8 positive-control re-baselined by TIGHTENED form
- **`phase4-plan.md:121`** (AC8) — anchor: "Positive control (against the T10 command list". The loose form's claim of 14 was wrong (actually 16, dual-confirmed: 3 on :58 + 9 on :97 + 4 on :121, self-referential drift). FROZEN replacement (tightened; char class widened from the dispatch's literal `[a-z_]` to `[a-z0-9_]` because the literal form UNDERCOUNTS — it misses `parity_runs_v2_schema`'s digit):
  - `grep -oE 'pytest -k "[a-z0-9_]+"' phase4-plan.md | wc -l` → **actual: 8** (breakdown executed: 1 on :58 — T9b's `critic_meta` quote; 7 on :97 — full Nit-2 list). Post-edit re-run confirms 8 is stable (the frozen command does not self-match).
  - Dispatcher's literal form recorded for comparison: `grep -oE 'pytest -k "[a-z_]+"' phase4-plan.md | wc -l` → actual: 7 (6 on :97 — `parity_runs_v2_schema` excluded by the class).
  - The retired loose form and its 14-vs-16 discrepancy are noted inline in the AC8 cell.

### Fix 3 (OPTIONAL, applied) — phase1 AC6 word-boundary
- **`phase1-plan.md:79`** (AC6) — anchor: "`rule.md` Cardinal #4 and `:67` reference `critic`". Loose alternation `(worker|sketcher|critic)` (false-positived on `critical` paths) → `grep -nE "\bcritic\b" agents/designer/rule.md` + enumerated target lines (Cardinal #4 `:12`, `:67`). Positive control executed today: `grep -cE '\bcritic\b' agents/designer/rule.md` → actual: 0 (targets exist pre-edit: `:12` = `worker + sketcher only`; `:67` = `sub-team targets are worker and sketcher`).

### Fix 4 (OPTIONAL, applied) — phase3 AC3 mirrors SC-3's broadened ERE
- **`phase3-plan.md:89`** (AC3) — anchor: "Secondary signal". Single-phrase form → `grep -niE "stay on my own direct|run it on my own direct" agents/designer/workflow.md`. Positive control executed today: returns actual: 2 lines (`:105` + `:112`, both confirmed present in the worktree file).

### Fix 5 (OPTIONAL, applied) — phase4 AC10 ahead-only form
- **`phase4-plan.md:123`** (AC10) — anchor: "Worktree-only writes; main-checkout pristine". Two-dot `git diff origin/latest..feature/...` (measured origin's lead, not phase work) → three-dot merge-base form `git diff origin/latest...feature/designer-critic-orchestration --stat` + sha1sum declared the staleness-immune primary invariant. Topology executed today: `git rev-list --count origin/latest..feature/designer-critic-orchestration` = 0 ahead; reverse = 15 behind (matches Pass-4 context).

### Notes 6–9 — ALL APPLIED (none skipped)
- **Note 6** — `phase4-plan.md:97` (T10): SC-9 expected stderr shape documented (`AssertionError` traceback = by-design failure; tester grepping `traceback` must not false-positive) + maintenancer warning named expected noise.
- **Note 7** — `plan-overview.md:160` (SC-9) + `:142` (R23): `cd /home/nea/ensemble-src-wt-designer-critic-orchestration &&` inlined into the quoted probe commands (copy-paste form); `Agent 'maintenancer': deny entry 'git_commit'…` documented as pre-existing expected stderr noise (not a gate failure).
- **Note 8** — `phase2-plan.md:104` (AC1): markdown-escaped `\|` pipeline → pipe-free `ls -la agents/critic` (fails with `No such file or directory`).
- **Note 9** — `research-findings.md:263`: dead `plan-direction.md` pointer dropped from the SUPERSEDED marker; `architecture-recommendation.md §2 :29-35` citation retained.

### Closure recipes (executed 2026-10-10, plan dir as cwd)
- **(a)** `grep -n "python -c" plan-overview.md phase2-plan.md` → **actual: 0 hits** (exit 1). PASS.
- **(b)** Frozen form executed: `grep -oE 'pytest -k "[a-z0-9_]+"' phase4-plan.md | wc -l` → **actual: 8** (1 on :58 + 7 on :97). PASS.
- **(c)** Optionals landed: fix 3 → `phase1-plan.md:79` contains `\bcritic\b`; fix 4 → `phase3-plan.md:89` contains `run it on my own direct`; fix 5 → `phase4-plan.md:123` contains `origin/latest...feature`. PASS.

**Pass-4 scope guard:** no adjudicated decision re-litigated; Pass-3 sections above remain authoritative (this section qualifies executable-form details only). Reviewer waiver honored — no new findings raised.

**Planning closed — ready for approver.**

---

## Pass 5 — approver iteration-002 fixes (2026-10-10, post-rejection)

**Context:** fresh approver REJECTED iteration 001 (approve-worker-dco-phases: 3 blocking / 5 notes; core partition APPROVED, safety architecture confirmed, all 6 directive requirements confirmed served — zero semantic/architecture impact). This pass: all 3 blockers fixed + 7 notes folded + 2 discretionary notes skipped (recorded below). Writes confined to the 6 plan-dir markdown files; approver tracking file READ-ONLY. Every gate touched below has run-the-gate evidence. cwd for all commands: `/home/nea/ensemble-src-wt-designer-critic-orchestration`.

### B1 (BLOCKING) — Q1.2 capture-recipe migration is now OWNED + JOURNALED

**Decision: extend phase1 T7's cross-file edit (option A), NOT a deferred-debt entry.** Rationale (recorded in the task text itself): architecture-recommendation.md §2 :109 is an **EXECUTE** ruling ("the recipe migrates → agents/sketcher/tools_note.md"), so deferring it would contradict the binding source; and T7 is the only task touching both endpoints of the move (designer's `tools_note.md` is being rewritten there; sketcher's `tools_note.md` is already T7's conditional cross-file write target) — folding it here journals the migration through T7's outcome + AC8 in phase 1.

| Fix | File:line + anchor |
|-----|--------------------|
| Migration + ownership rationale in the task | `phase1-plan.md:63` (T7) — anchor: **"Q1.2 capture-recipe migration — EXECUTED IN THIS TASK"** (recipe `tools_note.md:55-110` → `agents/sketcher/tools_note.md`, first-person voice adapted; §2 :109 lane ruling cited; explicit reason the AC8 zero-grep alone cannot see it) |
| Pre-edit SHA of the migration target | `phase1-plan.md:58` (T2) — anchor: **"ALSO snapshot `agents/sketcher/tools_note.md`"** |
| Post-edit accounting | `phase1-plan.md:66` (T10) — anchor: **"sketcher `tools_note.md` (Q1.2 migration target) accounted"** |
| Dedicated gate (the section has zero `od.*` tokens — the old AC8 passed without touching it) | `phase1-plan.md:83` (AC8) — anchor: **"Q1.2 capture-recipe migration landed (blocker 1 gate)"** — `grep -n "Capture Procedure" agents/designer/tools_note.md` returns 0 AND same grep on `agents/sketcher/tools_note.md` returns ≥1 |
| Objective coherence | `phase1-plan.md:21` — anchor: **"This phase ALSO executes the **Q1.2 capture-recipe migration**"** |
| Overview coherence (4 spots) | `plan-overview.md:26` (§2.1 surgery bullet), `:56` (components row "Q1.2 capture-recipe migration (arch §2 :109 …)"), `:75` (phase-1 row "+ Q1.2 capture-recipe migration → `agents/sketcher/tools_note.md`"), `:93` (coupling-map row `agents/sketcher/tools_note.md`) |
| Downstream cite re-point (recipe's post-migration home) | `phase2-plan.md:96` (T12) — anchor: **"migrated to `agents/sketcher/tools_note.md` by phase 1 T7 per Q1.2"** |

**Evidence (run-the-gate, executed 2026-10-10):**
- Pre-edit positive controls: `grep -n "Capture Procedure" agents/designer/tools_note.md` → **1 hit (`tools_note.md:55`)**; `grep -n "Capture Procedure" agents/sketcher/tools_note.md` → **0 (exit 1, absent)** — the gate is real in both directions.

### B2a (BLOCKING) — `rule.md:38` added to T5 scope + outcome + gate

| Fix | File:line + anchor |
|-----|--------------------|
| Scope + outcome | `phase1-plan.md:61` (T5) — anchor: **"Guideline (c) fallback-audit bullet at `:38` (blocker 2a — in scope)"** — prescribes the re-key ("`od.generate` call errors → sketcher dispatch error → `call-error`"; probe semantics unchanged) and states why (survives phase 1 → phase 4 zero-token test fails → falsifies §5 hand-off) |
| Phase-1 gate (not left for phase 4 to trip over) | `phase1-plan.md:80` (AC5) — anchor: **"blocker 2a gate — covers the `:35` + `:38` re-keys"** — `grep -cE "od\.[a-z_]+" agents/designer/rule.md` returns 0 post-edit |

**Evidence (run-the-gate, executed 2026-10-10):** pre-edit positive control `grep -nE "od\.[a-z_]+" agents/designer/rule.md` → **2 lines (:35 + :38)**, `grep -cE` → **2** — the gate has real pre-edit signal and bites post-edit.

### B2b (BLOCKING) — prescribed prose reworded token-free (T4 / T7 + same-class T8 fold)

| Fix | File:line + anchor |
|-----|--------------------|
| T4 soul-identity sentence | `phase1-plan.md:60` — anchor: **"sketcher executes the OD compose-brief step tool-internally"** (was: "sketcher executes `od.compose_brief` as formatter") |
| T7 tools_note framing sentence | `phase1-plan.md:63` — anchor: **"sketcher executes the OD compose-brief step tool-internally"** (same reword; `§2 :33` pointer preserved) |
| Same-class instructional fold (an implementer copying the old parenthetical verbatim would trip AC9's zero-grep) | `phase1-plan.md:64` (T8) — anchor: **"the skill body names sketcher dispatch, never a direct OD lane"** (was: "sketcher-not-`od.generate`") |

**Evidence (positive control, executed 2026-10-10):** the three reworded prescribed sentences extracted from the plan (sed, quoted verbatim in the transcript) and grepped: `grep -E "od\.(generate|compose_brief|save|lint)"` over all three → **rc=1, ZERO matches** — written as prescribed, the plan's own AC8/AC9/T9b zero-greps now accept the plan's own text.

### B3 (BLOCKING) — phase2 AC7 greps no longer false-red by construction

**Fix:** `phase2-plan.md:110` (AC7) — anchor: **"Pass-5 fix — blocker 3"** — the two bare-`|` plain-grep forms (BRE = literal pipe, byte-verified false-red by the approver) are replaced with FIVE individual fixed-string greps, each satisfiable by the T7-mandated prose verbatim: `grep -n "DECLARED"` ≥1; `grep -n "EFFECTIVE"` ≥1; `grep -n "image_save"` ≥1; `grep -n "read-only leaf purity"` ≥1; `grep -n "shared_meta_kv"` ≥1; diff-vs-generated-table unchanged. (Split chosen over `-E` so no threshold depends on line-joining of the mandated prose.)

**Evidence (run-the-gate, executed 2026-10-10):** fixture = the three T7-mandated lines (phase2-plan.md:91 DECLARED/EFFECTIVE note + `image_save` deny rationale + `shared_meta_kv` deny rationale) piped via `printf | grep` (no file writes):
- NEW greps: all **5 return rc=0 with the expected hits** (DECLARED→line 1, EFFECTIVE→line 1, image_save→line 2, read-only leaf purity→line 2, shared_meta_kv→line 3). GREEN.
- OLD forms (defect control): `grep -n "DECLARED|EFFECTIVE"` → **rc=1, 0 matches**; `grep -n "image_save|read-only leaf purity|shared_meta_kv"` → **rc=1, 0 matches** — reproduces the approver's GNU grep 3.11 probe exactly.
- Sanity: `-E` alternation on the same fixture → 1 + 2 lines — proving the pipe was the only defect.

### Notes folded (7)

| # | Note | Fix + evidence |
|---|------|----------------|
| 1 | arch deny JSON omits `image_save` (approver-sanctioned) | `architecture-recommendation.md:90` — deny literal now `"shared_meta_kv", "infra", "mcp", "image_save"]` (grep-verified) + new explanatory bullet at `:101` ("image_save explicit deny entry (Pass-5 alignment…)"). Binding source no longer contradicts its consumers (phase2 T3/AC2). |
| 2 | arch §5.1 deny-mechanism parenthetical wrong (approver-sanctioned) | `architecture-recommendation.md:151` — anchor: **"deny DOES subtract bare tool names"** — corrected per `daemon/tools/instance.py:379-393` (resolve_tool_filter docstring: "Apply deny → remove denied items (deny wins)") + subtraction loop read today at `:484-492`; prior "deny strips categories, not tools inside allow-listed categories" wording retired inline. |
| 3 | Stale cite `instance_messaging.py:1486-1510` | Fixed in BOTH occurrences: `architecture-recommendation.md:155` (§5.3) + `phase3-plan.md:40` (Guideline (e)) → **`~:2224-2254`**. Verified by reading the file today: the terminal-revival carve-out block (`is_terminal_revival`, COMPLETED/TERMINATED/ERROR/FAILED) sits at `:2224-2254`. Residual sweep: `grep -rn "instance_messaging.py:1486"` → none. |
| 4 | AC10 "15 behind" → 33 today | `phase4-plan.md:123` (AC10) — anchor: **"the number is ILLUSTRATIVE, not load-bearing"** — N=33 recorded as Pass-5 snapshot, re-count command embedded (`git rev-list --count feature/…..origin/latest`), merge-base gate declared drift-immune. Evidence: `git rev-list --count origin/latest..feature/…` = **0 ahead**; reverse = **33 behind** (executed). |
| 5 | overview "Cardinal" vs phase3 Guidelines (e)–(h) | `plan-overview.md:26` ("encoded as **Guidelines (e)–(g)**, not numbered Cardinals (cap-7; see phase 3)"), `:53` (components row), `:75` (phase-1 row), `:173` (SC-20 "controls (Guidelines (e)–(g))"). R20–R22 left verbatim — they quote the ARCHITECT's requirement language ("Cardinal required"), which phase 3 documents the cap-7 routing for. |
| 6 | transient-red between phases 1↔4 | `phase1-plan.md:65` (T9) — anchor: **"Transient-red (accepted, by design — approver note)"** (`test_designer_rewire.py` `:199/:204` pins red until phase 4 T9/T9b; phase-1 gates unaffected) + `phase4-plan.md:57` (T9) — anchor: **"expected transient-red since phase 1"** (cross-ref back). |
| 7 | phase4 T12 two-dot diff retired by AC10 | `phase4-plan.md:104` (T12) — now runs the SAME merge-base three-dot command AC10 freezes (`git diff origin/latest...feature/designer-critic-orchestration --stat`); retirement reason inline. Residual: `grep -c "git diff feature/…..origin/latest"` → 0 (the remaining `A..B` two-dot on :123 is the rev-list range syntax, not a diff). |

### Notes skipped (2 — discretionary per approver, recorded per protocol)

- **"mirrors sketcher" wording imprecision** (phase2 D6 rows: sketcher `["dynamic-skill"]`/12/watchover vs critic `+todo`/7/dropped) — skipped: wording-only imprecision inside decision-rationale prose; no gate or task consumes it.
- **phase3 T7b span hedge** ("line-count delta = -13 (or whatever the current span is)") — skipped: T7b already authorizes the `:132-147` inclusive delete per finding 14, and the outcome column explicitly defers to the actual span; no contradiction.

### Pass-5 integrity

- **Write surface:** exactly 6 files — `phase1-plan.md`, `phase2-plan.md`, `phase3-plan.md`, `phase4-plan.md`, `plan-overview.md`, `architecture-recommendation.md` (all under `.agents/shared/planning/designer-critic-orchestration/`) + this cure-map append. No agents/**, tests/**, daemon/**, main-checkout, or approver-file writes (tracking file mtime 14:23:35Z predates this dispatch 14:25:41Z; read-only).
- **Concurrency correction (self-reported):** three same-file edit pairs were initially dispatched in parallel and the last-writer clobbered its sibling (`plan-overview.md` :53/:74; `phase1-plan.md` T8/AC8; `phase2-plan.md` AC7). Detected by a full post-edit audit sweep (anchor-grep per fix); all losses re-applied sequentially and verified present (final sweep: 9/9 phase1 anchors, AC7+T12 phase2 anchors, 4/4 overview spots, 4/4 arch spots).
- No adjudicated decision re-litigated; Passes 3–4 sections above remain authoritative; the two approver-sanctioned arch-doc edits are alignment-only.

**Iteration 002 ready for the fresh approver.**

---

## Pass 6 — approver iteration-003 fixes (2026-10-10 ~15:30Z)

Scope honored: exactly the 3 blockers + tail cleanup + optional pointer; zero restructuring. Write surface: the six plan files (below) + `pass6-run-fixtures.sh` + `pass6-fixtures/` (5 files) + this append — all under `.agents/shared/planning/designer-critic-orchestration/`. No writes to agents/**, tests/**, daemon/**, main checkout, or `.agents/approver/**`.

### Fix 1 — phase1 AC4 gate unsatisfiable by T5 (blocker 1)

| Site | Anchor | Change |
|------|--------|--------|
| `phase1-plan.md:61` (T5 binding text) | `"…AND the one retry (Q5.4 exit — the \`same-code-class\` condition). Both cases emit…"` | Literal token `same-code-class` inserted into the QUOTED binding text (the text that lands in `rule.md:35`) — previously only the task-cell narration carried it. |
| `phase1-plan.md:79` (AC4 verification) | `"Pass-6 fix — the prior single combined alternation was unsatisfiable…"` | Combined ≥4-hit-LINES alternation RETIRED; restructured into 4 per-alternative greps each ≥1 (the phase2-AC7 Pass-5 cure pattern): `grep -nE "lane_preference"` / `"same-code-class"` / `"other:user-requested-text-only"` / `"other:proxy-ceiling-"` each return ≥1. |

**Executed evidence — AC4 arithmetic (fixture = the frozen T5-mandated post-edit shape; `pass6-fixtures/fixture-a-rulemd-body.txt` carries `:15` enum + `:35` re-keyed bullet + `:38` audit bullet verbatim; runner = `pass6-run-fixtures.sh`, executed from the worktree root):**
- RETIRED combined form on the frozen T5 text: `grep -cE "lane_preference|same-code-class|other:user-requested-text-only|other:proxy-ceiling-" fixture` → **1 hit line** (all four alternatives on the one `:35` bullet) → ≥4 unsatisfiable — defect confirmed by construction.
- Frozen NEW form: gate 1.1 `lane_preference` → exit=0 hits=1; gate 1.2 `same-code-class` → exit=0 hits=1; gate 1.3 `other:user-requested-text-only` → exit=0 hits=1; gate 1.4 `other:proxy-ceiling-` → exit=0 hits=1. **Arithmetic: 4 alternatives × ≥1 hit each, all on the single re-keyed bullet → satisfiable BY CONSTRUCTION.**
- AC5 co-gates on the same fixture (gate 4): five-token enum grep ≥2 lines (hits at `:15` + `:35` + `:38` shape); `od\.[a-z_]+` zero-grep → **0** (the re-keys are token-free). Script exit 1 = the trailing zero-grep finding 0 hits — that IS the zero-gate pass condition.

### Fix 2 — stale range + blind gate (blocker 2)

**FIRST, real-layout verification (directive-mandated; read-only vs `agents/designer/tools_note.md`, 172 lines):** approver ~numbers byte-confirmed — `## Capture Procedure (agent-browser → substrate)` at **:55**; `### Provenance tag policy` at **:124**; `### Failure modes I expect` at **:132** (heading is "Failure modes I expect", not bare "Failure modes"); section-closing `---` at **:140**; next section `## Two-Channel Image Reality` at **:142**. Sidecar JSON fenced block **:105 open → :120 close**.

**Executed evidence — orphan-gate positive controls (LIVE, today):**
- `grep -c "Provenance tag policy" agents/designer/tools_note.md` → **1** (exists at `:124` pre-move; the AC8 post-move gate demands 0).
- `grep -c "Failure modes I expect" agents/designer/tools_note.md` → **1** (at `:132`; post-move gate demands 0).
- Fence arithmetic (indent-tolerant `grep -cE '^[[:space:]]*```'` — note: `\x60` hex-escape forms were probed and DO NOT work on this host's grep/shell; the single-quoted literal form is the documented paste-safe command): prescribed-OLD range `sed -n '55,110p' | fence-count` → **5 (ODD — mid-fence cut proven; the range's last lines are mid-JSON inside the :105→:120 block)**; header-delimited body `:55-139` → **6 (EVEN)**; whole designer file → **6 (EVEN, all six inside the section)**.

| Site | Line (post-edit) | Change |
|------|------------------|--------|
| `phase1-plan.md:63` (T7 range) | "HEADER-DELIMITED range per Pass-6 fix, byte-verified 2026-10-10" | `:55-110` → heading `:55` → `---` at `:140` (body `:55-:139`), incl. both mandated subsections; mid-fence-cut rationale inline. |
| `phase1-plan.md:63` (T7 outcome) | "Q1.2 migration landed WHOLE-SECTION" | Outcome now demands no orphaned subsection + even fence parity. |
| `phase1-plan.md:83` (AC8) | "Orphan gates (Pass-6 addition…)" + refreshed positive control | Added: designer-side `Provenance tag policy`/`Failure modes I expect` greps = 0 post-move; fence-parity greps (sketcher-side section EVEN; designer total stays EVEN); positive control records today's verified numbers (1/:124, 1/:132, 5-odd/6-even/6-total). |
| `phase1-plan.md:21` (§2 sibling cite — found in audit, same family) | "Pass-6 re-pin of the stale `:55-110` cite" | Header-delimited form. |
| `phase2-plan.md:96` (T12) | "header-delimited: heading `:55` through the `---` at `:140`" | Propagated cite re-pinned. |
| `plan-overview.md:26` / `:56` / `:93` (three MORE stale cites — found in the audit sweep, directive listed only the phase1/phase2/arch sites) | "Pass-6 re-pin" ×3 | Same header-delimited form; stale-range scan across all six plan files now returns ZERO hits. |
| `architecture-recommendation.md:110` (binding root, approver-sanctioned alignment) | "Pass-6 alignment so the binding source matches the executing phases' T7/AC8/T12 form" | Header-delimited + subsection enumeration; binding source no longer contradicts consumers. |

### Fix 3 — phase3 hand-off bullet Cardinal/Guidelines contradiction (blocker 3)

`phase3-plan.md:104` — one-line reword: "Three new architect-mandated Cardinals … — Cardinal cap preserved at 7." → **"Three architect-mandated controls encoded as Guidelines (e)/(f)/(g) in `rule.md` (KV round-counter + verbatim-lift brief + [VISUAL-QA-DEFERRED] marker) — NOT numbered Cardinals; numbered Cardinals unchanged at 7."** (The stale "Three new architect-mandated Cardinals" string: 0 hits remain.)

**Executed evidence — AC9 arithmetic (fixtures use the REAL seven Cardinal lines from today's `agents/designer/rule.md:9-15`):**
- LIVE positive control: `grep -cE '^[0-9]+\.' agents/designer/rule.md` → **7**.
- OLD hand-off reading fixture (3 new numbered Cardinals appended): count → **10 > 7 = FAIL** (the contradiction AC9 would have caught).
- NEW (Guidelines) reading fixture: count → **7 ≤ 7 = PASS**; the (e)/(f)/(g) lines present and NOT numeral-led (`grep -nE '^Guideline \([efg]\)'` → 3 hits).

### Note 4 — tail corruption cleanup (multi-edit silent-write class)

| File | Before → after lines | Removed (verbatim-class) | Post-check |
|------|---------------------|--------------------------|------------|
| `phase1-plan.md` | 111 → **106** | :107 fragment `ritic"\` to \`meta.json:22\`; phase 2 T3 verifies…` (tail-fragment of :102) + :109 `---` + :111 duplicate `**End of phase 1 plan.** Phase 2 next.` | Ends coherently at :106; :100-106 read intact (§5 bullets → Parallel concern → trailer). |
| `phase2-plan.md` | 144 → **137** | :138 fragment `tion block. Phase 3's tasks wire…` (tail-fragment of :131) + :140 duplicate Parallel-concern paragraph + :142 `---` + :144 duplicate end line | Ends coherently at :137; :132-137 intact. |
| `plan-overview.md` | 226 → **225** | :226 fragment `an overview.** See \`phase1-plan.md\` next.` (tail-fragment of :225) | Ends coherently at :225; §10 → trailer intact. |

All removed lines were exact-duplicate fragments of surviving lines (nothing unique lost). Note: the planning dir is NOT git-tracked, so removed text is recorded here as the reversal record.

### Note 5 — pointer decision: ADDED (fits naturally)

`phase4-plan.md:97` (T10, authors `verification-surface.md`) — one-line pointer appended inside the Nit-2 invocation enumeration, right after `agent_registry_scan`: **"first-run source anchors (approver iteration-002 note — verify these cites on first run; the plan does not pre-verify): `compare_tools.py:1265-1269` (pinned_spec_sha binding), `registry.py:1191/:1207` (`exists()`/`get_registry()`), `_auth.py:155-161` (design→image-comparator auto-extension)**". The AC9 capture-format pin and T13/AC11 merge-base form remain tracking-recorded only, per the directive ("no plan change needed" for those two).

### Pass-6 integrity

- Edits executed strictly sequentially per file (Pass-5 clobber lesson); full post-edit anchor sweep: 12/12 anchors present (A1-A12), zero stale `:55-110` cites in any plan file, all three tails end on their unique trailer line.
- Gates executed read-only against live `agents/**` files; fixture writes confined to the plan dir. Markdown-embedded-backtick hazard found and fixed in my own AC8 insertion before delivery (double-backtick code span carrying the single-quoted grep — the `\x60`-escape alternative was probed, failed on this host, and rejected).

**Iteration 003 ready for the final approver pass.**

- **Pass-6 residue fix (approver verification, post-delivery):** `research-findings.md:229` (Deferred (c) row) — before: "manual recipe in `agents/designer/tools_note.md:55-110` today"; after: "manual recipe in `agents/designer/tools_note.md:55-139` (header-delimited `## Capture Procedure` → `---`; whole-section — re-pinned Pass-6) today". Site escaped the Pass-6 audit sweep, which scanned only the six partition files (phase1–4, plan-overview, architecture-recommendation) — `research-findings.md` was outside that scan list. The Pass-6 "zero stale `:55-110`" claim now holds across ALL SEVEN plan-dir files (re-scan: `grep -l 'tools_note.md:55-110' *.md` → empty outside historical fix-documentation in review-cure-map.md/pass6 fixtures, which describe the prior cite as wrong).
