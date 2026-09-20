# LCA stale-A fix — Neutrality audit (delta `94fc6da1..e0d15e93`)

**Worktree:** `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-lca-stale-a`
**Branch:** `feature/lca-stale-a-fix` (verified)
**HEAD sha:** `e0d15e93174ecbc3e727cc92e56a3d84624db2da` (verified)
**Base sha:** `94fc6da1` (declared base of the dual-autopsy B1 fix)
**Audit date:** 2026-09-20 (UTC)
**Auditor:** Worker (read-only diff analysis)
**Audit premise:** the delta must contain EXACTLY the declared fix surface and nothing else.

---

## 1. File-level classification

| # | File | Category | Hunk count | +/- (insertions/deletions) | Verdict |
|---|------|----------|-----------:|--------------------------:|---------|
| 1 | `daemon/services/attestation_report_judge.py` | **production** | 2 hunks | 17 / 2 | in-scope |
| 2 | `daemon/services/attestation_resolver_activation.py` | **production** | many hunks | 293 / 36 | in-scope |
| 3 | `tests/unit/test_attestation_resolver_activation.py` | **test** | large | 591 / 2 | in-scope |
| 4 | `tests/unit/test_attestation_resolver_user_intent.py` | **test** | 1 hunk | 18 / 1 | in-scope (subordination-pin re-contract + new guard pin) |
| 5 | `.agents/shared/planning/leader-completion-attestation/decisions.md` | **docs** | 1 hunk | 20 / 0 | docs-only (markdown prose, no executable code) |
| 6 | `.agents/shared/planning/leader-completion-attestation/requirements.md` | **docs** | 2 hunks | 4 / 4 | docs-only (markdown prose; R-RES-8/R-RES-9 SUPERSEDED-IN-PART marks) |
| 7 | `docs/setup.md` | **docs** | 1 hunk | 2 / 0 | docs-only (single-paragraph bundle-shape update) |

**Production scope:** ONLY `daemon/services/attestation_report_judge.py` + `daemon/services/attestation_resolver_activation.py` — exactly the two declared files. ✓
No changes under `daemon/graph.py`, no changes under `daemon/services/attestation_gate.py`, no changes under `daemon/services/attestation_marker_scanner.py`, no changes under `daemon/services/attestation_resolver.py` (the Stage-3 retirement survivors), no changes under `agents/`. **Sibling-lane isolation confirmed** (feature/lca-attest-first-contract surface untouched).

---

## 2. Per-file hunk classification

### 2.1 `daemon/services/attestation_report_judge.py` (+17/-2)

| Hunk | Lines (before→after) | Category | Notes |
|------|----------------------|----------|-------|
| 1 | 499:7 → 499:15 | **constant docstring** (8 lines added) | `#:` comment block above `FUSED_JUDGE_SYSTEM_PROMPT`; documents the dual-autopsy B1 softening + byte-tail discipline. No semantic change. |
| 2 | 519:7 → 527:14 | **prompt-text** (7 net new lines) | The subordination sentence is SOFTENED by appending a dash-clause containing BOTH carve-outs (later-delivered supersession + operator-action pending); ONE new sentence added ("A GENUINE unresolved advisory…"); "Be CONSERVATIVE" line unchanged. See §3 verbatim quote. |

**Net: 17+/2− = 15 net.** Diff stat said "19 +/-" but `--numstat` resolves to 17 added, 2 removed.

### 2.2 `daemon/services/attestation_resolver_activation.py` (+293/-36)

| Hunk location | Category | Notes |
|---------------|----------|-------|
| `assemble_fused_bundle` docstring (lines 40-43) | **docstring** | Doc-only; documents the B-section 2500-clip + the NEWEST-only-A + C-cross-resolve changes. |
| `_B_MESSAGE_CLIP: int = 2500` constant (lines 207-217) | **B-clip constant (B1 item 5)** | Cap-widening only; consumed at exactly one site (`_build_b_section`, line 1248). External caps (`BUNDLE_B_SECTION_MAX=6000`, `BUNDLE_TOTAL_MAX=14000`) UNCHANGED. |
| `_OPERATOR_ACTION_TOKENS: tuple[str, ...]` (lines 226-238) | **operator-token demotion (B1 item 3)** | New constants `("rebuild", "restart", "re-deploy", "redeploy")`. TIGHT catalog (no bare `deploy`/`activation`). |
| `_SENTENCE_SPLIT_RE: re.Pattern[str]` (line 241) | **helper for operator-token demotion** | Splits on `[.!?\n;]+`; colon deliberately excluded. |
| `_CROSS_RESOLVE_DELIVERED_STATUS: str = "completed"` (lines 247-258) | **C-cross-resolve constant (B1 item 2)** | ONLY `completed` suppresses; terminated/error/failed/absent keep. |
| `ChildReportCheckEvidence.operator_scoped_terms` field (lines 281-289) | **dataclass field for operator-token demotion** | Frozen-dataclass additive field, default `()`. |
| `SourceASignals.contradiction_flag` docstring (lines 315-322) | **docstring** | Documents operator-scoped exclusion. |
| `_operator_scoped_terms(content, matched_terms)` helper (lines 583-605) | **helper for operator-token demotion** | Pure function: sentence-scope a matched-term list against the operator tokens. |
| `_completed_child_ids(tree_rows)` helper (lines 608-626) | **helper for C-cross-resolve** | Pure function: extract the set of `completed` child ids from C tree rows. |
| `_build_evidence_from_report_message` (lines 641-673) | **evidence builder for operator-token demotion** | Now populates `operator_scoped_terms`. |
| `collect_source_a_signals` signature + docstring (lines 677-779) | **newest-only A-scan + C-cross-resolve + operator-token demotion** | `tree_rows_provider` parameter added (default `None`, back-compat); docstring documents B1 evidence-quality changes. |
| `collect_source_a_signals` Pass 2 rework (lines 821-915) | **newest-only A-scan (B1 item 1)** + **C-cross-resolve (B1 item 2)** | Two-pass: (a) walk all `internal_report` messages and keep each child's LAST one; (b) cross-resolve against C tree rows, suppressing completed-child advisories unless the later-contradiction exception holds. |
| `contradiction_flag` computation (lines 922-930) | **operator-token demotion** | Excludes `ev.operator_scoped_terms` from the `_CONTRADICTION_MARKERS` membership test. |
| `_build_b_section` clip (line 1248) | **B-clip constant consumption** | `_clip(content, _B_MESSAGE_CLIP)` (was `_clip(content, 1500)`). |
| `assemble_fused_bundle` B-section docstring (lines 1345-1353) | **docstring** | Documents the B1 B-clip change. |
| `evaluate_resolver_activation` fetch-once cache (lines 1527-1567) | **newest-only A-scan + C-cross-resolve helper** | `_cached_tree_rows` wrapper: lazy fetch, single invocation per evaluation, shared between A-scan and bundle C-section. |
| `evaluate_resolver_activation` A-source lambda (line 1571) | **A-scan wiring** | `a_source=lambda: collect_source_a_signals(messages, tree_rows_provider=_cached_tree_rows)` — wires the cache. |
| `evaluate_resolver_activation` bundle C-rows (line 1578) | **C-cross-resolve helper** | `c_tree_rows=_cached_tree_rows()` — same cache. |

**All hunks classified into the declared fix surface. No "other" hunks.**

### 2.3 `tests/unit/test_attestation_resolver_activation.py` (+591/-2)

4 new test classes (per decisions.md):
- `TestNewestReportOnlyPerChild` (line 846) — B1 item 1
- `TestOperatorScopedHits` (line 985) — B1 item 3
- `TestCrossResolveAgainstTreeRows` (line 1058) — B1 item 2 (incl. flagship child-lie pin `test_child_lie_completed_without_delivery_still_fires`)
- `TestBSectionClip2500` (line 1355) — B1 item 5

Plus the wiring-pin re-contract:
- `TestDCTD7ASignalPathPins.test_resolver_consumes_collect_source_a_signals` (line 1967) — asserts `tree_rows_provider=_cached_tree_rows` is wired into the `a_source` lambda.

No `sys.modules` hacks, no `exec`/`eval`, no daemon imports added (only the existing `from daemon.services import attestation_resolver_activation` style). Test-only.

### 2.4 `tests/unit/test_attestation_resolver_user_intent.py` (+18/-1)

Single hunk: the verbatim-subordination pin is re-contracted to assert BOTH the softened subordination AND the new genuine-advisory guard. Test-only.

### 2.5 `.agents/shared/planning/leader-completion-attestation/decisions.md` (+20/-0)

Single 20-line append: D-ENTRY 2026-09-20 documenting the 5-part fix + matrix verification numbers. Markdown prose only; no embedded code/config.

### 2.6 `.agents/shared/planning/leader-completion-attestation/requirements.md` (+4/-4)

R-RES-8 + R-RES-9 SUPERSEDED-IN-PART marks: adds a sentence each, removes nothing meaningful (rewording). Markdown prose only.

### 2.7 `docs/setup.md` (+2/-0)

Single-paragraph bundle-shape update: documents the 3 evidence-quality changes + the prompt softening + the B-clip 1500→2500. Markdown prose only.

---

## 3. Verbatim-guard before/after quote (attestation_report_judge.py)

### 3.1 Before (`94fc6da1:daemon/services/attestation_report_judge.py`, lines 522-524)

```python
    "If SOURCE U is absent, judge on A/B/C alone - do not infer the user's "
    "request. "
    "SOURCE A advisories and SOURCE C live/pending descendants still "
    "indicate NOT_COMPLETE even when SOURCE U appears fulfilled. "
    "Be CONSERVATIVE: when in doubt, return "
```

### 3.2 After (`e0d15e93:daemon/services/attestation_report_judge.py`, lines 527-535)

```python
    "If SOURCE U is absent, judge on A/B/C alone - do not infer the user's "
    "request. "
    "SOURCE A advisories and SOURCE C live/pending descendants still "
    "indicate NOT_COMPLETE even when SOURCE U appears fulfilled - but an "
    "advisory is NOT evidence of undelivered work when its child later "
    "delivered a newer report that supersedes it, or when the pending item "
    "the advisory names is an OPERATOR action such as a rebuild+restart "
    "activation (the operator's step, not the child's undelivered work). "
    "A GENUINE unresolved advisory - a child promising future work that "
    "never arrived, or a live/pending descendant - still indicates "
    "NOT_COMPLETE even when SOURCE U appears fulfilled. "
    "Be CONSERVATIVE: when in doubt, return "
```

### 3.3 Verdict

| Item | Verdict |
|------|---------|
| U-absent guard ("If SOURCE U is absent, judge on A/B/C alone…") | **UNCHANGED VERBATIM** ✓ |
| Subordination line — first half ("SOURCE A advisories and SOURCE C live/pending descendants still indicate NOT_COMPLETE even when SOURCE U appears fulfilled") | **UNCHANGED VERBATIM** ✓ (continues into dash-clause) |
| Two carve-outs (later-delivered supersession + operator-action pending) | **PRESENT** ✓ (joined via "or" within one dash-clause — see Finding F-1) |
| New genuine-advisory guard ("A GENUINE unresolved advisory…") | **NEW, present verbatim** ✓ |
| "Be CONSERVATIVE" anchor | **UNCHANGED VERBATIM** ✓ |
| Strict-JSON contract tail (501 chars from "Be CONSERVATIVE" onward) | **UNCHANGED** ✓ — pinned by `_EXPECTED_PROMPT_BYTE_TAIL` in `tests/unit/test_attestation_resolver_user_intent.py:791` and asserted at line 874 |
| `FUSED_JUDGE_MAX_OUTPUT_CHARS` (2048) | **UNCHANGED** ✓ |
| `FUSED_JUDGE_EVIDENCE_ITEMS_MAX` (5) | **UNCHANGED** ✓ |
| Retry-once-on-timeout logic (b2371a9b lineage) | **UNCHANGED** ✓ |
| `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED` kill-switch | **NOT TOUCHED** ✓ (grep against delta: zero modifications; the only line mentioning it is the unchanged prose in `docs/setup.md`) |
| `judge_fused_bundle_async` entry-point signature | **UNCHANGED VERBATIM** ✓ (before: `(bundle_text: str, *, config: "Config", timeout_s: float | None = None) -> FusedJudgeResult`; after: identical) |

---

## 4. Untouched-surface checklist

| Item | Verdict | Evidence |
|------|---------|----------|
| U-section builder behavior (`_build_u_section`, `user_message_included`) | **UNTOUCHED** | `_build_u_section` still at line 1297; `user_message_included: bool = False` still at line 383; no `-` removal in patch |
| U cap (`BUNDLE_U_SECTION_MAX = 2000`) | **UNTOUCHED** | grep shows `BUNDLE_U_SECTION_MAX: int = 2000` at line 178; patch never modifies it |
| A cap (`BUNDLE_A_SECTION_MAX = 3000`) | **UNTOUCHED** | grep shows `BUNDLE_A_SECTION_MAX: int = 3000` at line 175 |
| C cap (`BUNDLE_C_SECTION_MAX = 3000`) | **UNTOUCHED** | grep shows `BUNDLE_C_SECTION_MAX: int = 3000` at line 177 |
| Total cap (`BUNDLE_TOTAL_MAX = 14000`) | **UNTOUCHED** | grep shows `BUNDLE_TOTAL_MAX: int = 14000` at line 179; `_EXPECTED_BUNDLE_TOTAL_MAX` referenced at line 1390 |
| Band precedence (deny > marker > a_suspicion) | **UNTOUCHED** | referenced in comments at lines 146, 699 (B1 fix explicitly states "predicate band structure UNCHANGED"); the activation predicate code is not in the patch |
| SOURCE-B marker list (`MID_WORK_MARKERS`) | **UNTOUCHED** | lives in `daemon/services/attestation_marker_scanner.py:118` (different file, NOT modified by delta) |
| `SHORT_REPORT_WORD_THRESHOLD = 150` | **UNTOUCHED** | grep shows `_SHORT_REPORT_WORD_THRESHOLD: int = 150` at line 499 |
| Deny-bound enforcement (`deny_bound_exceeded`) | **UNTOUCHED** | 4 sites at lines 1103/1104/1124/1126/1496/1497/1566; predicate body not in patch |
| `B-clip` change = `_B_MESSAGE_CLIP` constant 1500→2500 | **CAP-WIDENING ONLY** | constant at line 213; consumed at exactly ONE site (line 1248 in `_build_b_section`); comment at line 210-211 documents that 3 × (2500 + label) still exceeds `BUNDLE_B_SECTION_MAX=6000`, so the external per-section cap remains the binding budget |
| No new `ENSEMBLE_*` env flags | **CONFIRMED** | `git diff … \| grep -E '^\+.*ENSEMBLE_[A-Z_]+'` returns nothing |
| No `daemon/graph.py` modifications | **CONFIRMED** | `git diff --name-only` does not list `daemon/graph.py` |
| No `agents/` modifications | **CONFIRMED** | `git diff --name-only` does not list any `agents/*` file |
| Sibling-lane (gate-hold, nudge, rule.md, agents/leader) untouched | **CONFIRMED** | grep over `--name-only` for `attestation_gate`/`attestation_marker_scanner`/`nudge`/`rule.md`/`agents/leader`/`gate_hold`/`child_reports`/`attestation_resolver.py` returns nothing |
| Docs hunks are documentation-only (no embedded config/code that executes) | **CONFIRMED** | All three doc hunks are markdown prose (decisions.md: prose + bullet list; requirements.md: SUPERSEDED-IN-PART marks; docs/setup.md: single paragraph). No `code blocks` with executable Python, no YAML/JSON configs. |
| Test files test-only (no production side-effect concerns) | **CONFIRMED** | No `sys.modules`/`exec`/`eval`/`subprocess`/`os.system`/`__import__` patterns in test diff; no new `from daemon` / `import daemon` lines added beyond existing patterns. |
| `judge_fused_bundle_async` entry-point singularity | **CONFIRMED** | signature byte-identical (verified above) |

---

## 5. Findings

| ID | Severity | Finding | Evidence | Neutrality impact |
|----|----------|---------|----------|-------------------|
| F-1 | **info** | The two carve-outs (later-delivered supersession + operator-action pending) are joined within ONE dash-clause sentence ("…fulfilled - but an advisory is NOT evidence of undelivered work when its child later delivered a newer report that supersedes it, **or** when the pending item the advisory names is an OPERATOR action…") rather than appearing as two separate sentences as the audit premise described ("exactly TWO carve-out sentences"). | patch hunk 2 of `attestation_report_judge.py`; see §3.2 quote | **Neutral** — semantic content is identical (both carve-outs present, both before the genuine-advisory guard, both with the same conditions). The single-sentence dash-clause form is grammatically tighter and the LLM-judge will treat it the same way; no functional difference. Documented as a structural-vs-spec note, not a defect. |
| F-2 | **info** | Known doc-comment drift left intentionally in `daemon/services/attestation_gate.py` §(vi) — the comment says "the tree-rows provider runs lazily and only on would-fire" but with the B1 fix it now runs lazily AND at most once per evaluation (A-scan cross-resolution when advisory candidates exist, else bundle assembly). | `.agents/shared/planning/leader-completion-attestation/decisions.md` explicitly calls this out: "Known doc-comment drift (left intentionally)… the gate file is shared seam territory with the sibling attest-first lane; fix there or in a follow-up." | **Neutral** — explicitly tracked as a follow-up; doesn't touch behavior. |
| F-3 | **info** | The verbatim-subordination pin in `test_attestation_resolver_user_intent.py` (`test_u_fulfilled_a_advisory_c_live_judged_not_complete_honored`) was RE-CONTRACTED to assert the new softened contract — this is an INTENTIONAL pin change, documented in decisions.md ("RE-CONTRACTED to the new softened contract"). | decisions.md "old verbatim subordination pin… was RE-CONTRACTED to the new softened contract (superceded-doctrine re-contract, atomic with this change)"; test diff at `tests/unit/test_attestation_resolver_user_intent.py:968-995`. | **Neutral** — intentional atomic re-contract documented; not a silent mutation. |

No **critical** or **important** findings. The delta is materially in scope.

---

## 6. Verdict

# **NEUTRAL-WITH-NOTES**

The delta contains EXACTLY the declared fix surface (5 B1 items: newest-only A-scan, C-cross-resolve, operator-token demotion, prompt softening, B-clip 1500→2500) and the documented docs/test re-contracts. No scope creep. No new env flags. No sibling-lane bleed. The prompt softening inserts ALL new content BEFORE the "Be CONSERVATIVE" anchor, preserving the 501-char strict-JSON contract tail pin. The `_B_MESSAGE_CLIP` change is a pure cap-widening with external caps unchanged. The cross-resolution is lazy + best-effort + fetch-once cached (no extra DB cost on clean no-advisory evaluations).

The single note (F-1) is a structural-vs-spec deviation: the two carve-outs are joined within ONE dash-clause sentence rather than appearing as two separate sentences. Semantic content is identical; this is documented as info, not a defect.

**Recommendations:**
- Ship as-is. The B-clip 2500 + cross-resolution + operator-token demotion are cleanly composable and the byte-tail pin (501 chars) is preserved.
- F-2 doc-comment drift in `attestation_gate.py` should be picked up in a follow-up commit (gate file is sibling-lane territory).
- F-1 is a documentation note only — no action required.

---

## 7. Notes on parallel-lane artifacts

Per the audit instruction, after writing this RESULTS file the working tree should contain ONLY this one new file. Any parallel-lane artifacts (test/packs scripts, additional tests) should be left alone. Verify with `git status --porcelain` after the write.
