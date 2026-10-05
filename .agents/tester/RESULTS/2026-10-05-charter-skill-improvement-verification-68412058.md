# Focused Verification — charter-skill-improvement @ 68412058 (merge gate)

**Date:** 2026-10-05 (UTC)
**Verifier:** Tester (independent verification round 4)
**Feature / branch:** `feature/charter-skill-improvement` @ `68412058c1892a12c346e6fe74cc0ff40318477c`, base `dcca056a`
**Worktree:** `/home/nea/ensemble-src-wt-render-opt`
**Workers (5):** `cid4-discover` `cid4-parity` `cid4-rule-table` `cid4-backlog` `cid4-mutations`

---

## TL;DR — Final verdict

**✅ USER-STORY-VERIFIED (boring first render)** — the doc+test contract now ensures a fresh-host charter following workflow.md verbatim renders first-try. With 7 non-blocking cheap-to-add nits + 3 dispatch-framing clarifications + 1 missing-deliverable flag.

---

## Per-task results

### T1 — Step 5.5 ↔ lib.sh parity (the load-bearing closure): **CONTRACT-LOAD-BEARING-CLOSED**

All five canonical forms identical between `agents/charter/workflow.md:300-380` (Step 5.5 fenced block) and `install-mermaid-cli.lib.sh:213-264` (`_charter_mmdc_render`), modulo the declared variable mapping (`workflow.md:307-311`: `$mmdc_bin→$MMDC_BIN`, `$cfg→$TMPCFG`, `$in_mmd→$TMPFILE`, `$out_file→$TMPPNG`, `$mmd_json→$MMD_JSON`, `$cfg_fb→$TMPCFG_FB`):

| Form | workflow.md | lib.sh | Identical |
|---|---|---|---|
| No `ulimit` command | `:323-327` comment only | `:202` comment only | ✓ (zero invocations both) |
| `--size 1200` BOTH invocations | `:345` primary, `:371` fallback | `:228` primary, `:254` fallback | ✓ |
| `mktemp` + `printf` + `-c "$MMD_JSON"` | `:336/:337/:346` (primary), `:372` (fallback) | `:218/:219/:229` (primary), `:255` (fallback) | ✓ |
| PATH prepend (`dirname "$mmdc_bin"` / `dirname "$MMDC_BIN"`) | `:341` (once, before primary; fallback inherits) | `:224` (same) | ✓ |
| jq fallback via real `$cfg`/`$cfg_fb` family | `jq '.args = ["--no-sandbox"]' "$TMPCFG" > "$TMPCFG_FB"` `:367` | `jq '.args = ["--no-sandbox"]' "$cfg" > "$cfg_fb"` `:250` | ✓ (identical jq expression) |

Parity-claim adjudication (`workflow.md:303-311`): **substantively true** at the canonical-form level; the leader's "command-for-command identical (modulo the declared variable mapping)" is the correct level of precision. Two rhetorical overstatements in the doc's own wording: (i) "byte-for-byte" vs the `|| return "$rc"` vs `|| true` structural difference in mktemp failure handling; (ii) lib uses `printf '%s\n' "$log"` `:234` vs workflow's `echo "$RENDER_LOG"` `:351`. Both functionally equivalent; neither undermines the load-bearing mapping.

### T2 — Spot-break mutation: **PASSED**

Mutation: `workflow.md:346` `-c "$MMD_JSON"` → `-c '{"securityLevel":"strict","htmlLabels":false}'` (inline-JSON form, the mmdc 12.x regression). Result: **RED** at the **inline-JSON negative pin** (`test_charter_render_capture.py:454`): `assert not re.search(r"-c\s+'\{", body)` — "workflow.md must NOT inline the security-pin JSON via `-c '{...}'` (12.x removed inline-JSON support; the staged-temp-file form is canonical)". The positive variable-form pin `:449` still passed (fallback at `:372` still carries the variable form) — expected and correct: the negative pin fires first, catching the regression. Revert → green. Restoration proof byte-clean.

### T3 — Pre-warm section soundness: **CONFIRMED**

Operator-not-charter boundary explicit: `install-mermaid-cli.md:47-52` (provisioning is the operator's job, not charter's, before first user request) + dedicated `### Why this is the operator's job, not charter's` subsection `:107`. `≥600s` + `cold_misses=2` cap: consistent across `lib.sh:167-191` (counter storage; value owned by consumer — correct factoring), `workflow.md:181` (`if [ "$cold_misses" -le 2 ]`), `install-mermaid-cli.md:119/487/490` ("TWICE per session"). install-opendesign structure mirrored (`install-mermaid-cli.md:86-88`) — fences/steps/verify ✓; one overstatement: no "Reversal" subsection (the "Cross-references" section is the analog; reversal is trivially `rm -f` the config + re-run pre-warm, documented in "When to pre-warm" `:77-79`).

### T4 — 6-fix table accuracy: **6/6 MATCH**

Each row at `install-mermaid-cli.md:156-163` matches `install-mermaid-cli.lib.sh` verbatim or faithfully: `--size 1200` `lib.sh:228+254`; staged `-c "$mmd_json"` `lib.sh:218+219+229+255`; zero `ulimit` invocations (one negation comment at `lib.sh:202` contains the word "ulimit" — spirit correct, literal grep off-by-one); PATH prepend `lib.sh:224`; top-level jq derivation `lib.sh:297-298`; fallback `.args` top-level `lib.sh:250`.

### T5 — rule.md restructure: **STRUCTURE-LOAD-BEARING-CLOSED**

`agents/charter/rule.md` (111 lines): **7 Cardinal + 5 Guideline + 5 Never** (was 8 → 5). Surviving Never classification: **N1 + N3 = 2 negative-only; N2 + N4 + N5 = 3 failure-mode** — matches the 2+3 split exactly. W5/W6/W7/W9 closures all CONFIRMED via `66512e9d` diff: W5 (Guideline 3 rephrase — "never leave temp files behind" hard-contract removed, now "the trap owns this"), W6 (Guideline 4 retitled "Retry SYNTAX errors only"; render-side exclusion explicit), W7 (3 mirrored Never items dropped, each had a Cardinal ancestor: C1+C3/C2/C5+C6), W9 (install-mermaid-cli named `rule.md:11` + first-person `rule.md:27` "I am a functional agent"). **No load-bearing Must demoted** — grep proves no must/never leaked into a Guideline.

### T6 — Audit + seal: **PASSED**

Audit **34/34 ×2 idempotent** (all 4 runs sha256-stable `7649e9fe…72ea6`); preservation 9/9 standalone. Seal tripwire **35/35** — `SEALED_SHA_BASELINES` at `tests/test_chart_image_delivery_e2e.py:1236-1279` holds 35 SHAs (8 Phase A + 7 Phase B + 20 Phase C); the `for` loop at `:1304` checks all 35; docstring `:1291-1296` records "Total 35". The 3 newly-pinned seal SHAs (workflow.md `870ec32f…`, rule.md `2ee5d27d…`, install-mermaid-cli.md `344f206d…`) each verified byte-exact against `sha256sum`.

**Mapping asymmetry (flag, not defect):** 3 new audit pins (#32/#33/#34) → 2 files (install-mermaid-cli.md gets 2, workflow.md gets 1); rule.md sealed but not newly audit-pinned — its edits (W5/W6/W7/W9) are stylistic, not a new grep-assertable invariant beyond the 7/5/5 count already structurally constrained.

### T7 — render_image consistency: **CONFIRMED**

Uniform across gate (`workflow.md:127-133` `should_render_dispatch_message`, default False when line absent), Step 5 (`:89` CONDITIONAL heading), Step 6 (`:429-438` explicit true/false behavior), d5_timeout (`install-mermaid-cli.md:42-44/68/98/115/117/476-481` + `workflow.md:609-616` — by-name `_DEFAULT_TIMEOUT_S`/`_RENDER_TIMEOUT_S` refs, never drift-prone literals), Summary (`workflow.md:643-664` — explicit default-False + true→render/persist/marker + false→skip). rule.md correctly does NOT mention render_image (load-bearing invariants only). install-mermaid-cli.md has no Summary (correctly — operator audience, not charter).

### T8 — Frontmatter: **1.1.0 ✓** (`install-mermaid-cli.md:2`)

### T9 — Backlog entry: **COLD-DISPATCH-READY, not OPTIMAL**

Located at `chart-image-delivery/decisions.md:1189-1196` (§opt-in-6 #4 — parent doc, consistent with prior rounds' convention; no charter-skill-improvement planning dir exists). Signature **SUFFICIENT** (param name/type/default/keyword-only/mutual-exclusion/Pydantic/consumer example). Tests **SUFFICIENT** (5-test list with class name + assertion shapes + fold-in note). **Two anchor-drift bugs**: (i) `:318-371` mislabeled as `_get_project_workdir` (actual `_load_image_from_path`; real one at 465-479); (ii) `workflow.md:438-464` off by ~30 lines (the base64 block is at 472-498). Options **PARTIAL** (budget-only re-dispatch options a/b; no design-level alternatives enumerated — A extend image_save / B image_save_file / C pre-stage tmpfile / D marker-transport file-ref all unstated).

### T10 — Restoration: **PASSED**

HEAD `68412058…` unchanged · porcelain EMPTY · stash 17 · diff EMPTY. No daemon, no commits, no cross-checkout contamination, port 8088 untouched.

---

## Suite / audit summary (my runs)

| Item | Result |
|---|---|
| `tests/test_charter_render_capture.py` (grew 69→74 with 5 new doc-pin tests) | **74 passed / 4 deselected / 0 failed** |
| `tests/test_chart_image_delivery_e2e.py` standard leg (incl. 35-seal tripwire) | **24 passed / 4 deselected / 0 failed** |
| Audit script ×2 + preservation standalone | **34/34 ×2 (sha256-stable) + 9/9** |
| Spot-break test solo (pre/post mutation) | 1 passed → RED (inline-JSON negative pin `:454`) → revert → 1 passed |

Other suites unchanged from prior rounds (chart_tools 31, dispatcher 78, discord 196, tg+slack 160, outbound 6, audit-pytest 43 — charter-skill-improvement touched none of them; diff scope = 8 files).

---

## Dispatch-framing clarifications (leader context vs reality — non-blocking, for reconciliation)

1. **`test_render_ulimit_and_timeout_wrap_invocation` is NOT new** — pre-branch from `b31fa926`; the closure *strengthened* it. It asserts the PRIMARY render form only (5 form categories); the sandbox-fallback is covered by separate tests (`:752/:824/:1165`). The 5 NEW tests are the doc-pin family (`:1172/:1197/:1229` + render-contract/pre-warm section pins).
2. **`tests/test_instance_ui_prefs_api.py` does NOT exist** anywhere in the repo — the base-repro expectation is invalid for that file (test_innate_skills_refactoring.py IS pre-branch, commit 01fe71a6).
3. **Branch has 5 commits, not 2** — 65427d99 (main docs) + d1f46f19 (backlog) + 15a745e3 (intermediate seal re-pin) precede the 2 closure commits; all carry real content.

## 🟠 Missing-deliverable flag (leader adjudication needed)

**No version bump and no new CHANGELOG entry** for charter-skill-improvement: version still 0.17.1, CHANGELOG `[0.17.1]` still says "31 pins" (current 34). The commission's deliverable list included "Version bump + docs". If the leader intended a 0.17.2 bump (or an amended [0.17.1] pin-count line), that's outstanding — flagging for adjudication. Not blocking the doc/test contract verdict; it's a release-hygiene item for the merge/promote ceremony.

## 🟢 Optional cheap-to-add nits (7, non-blocking; leader authorized flagging)

1. **Timeout-wrap test covers 1 match not 2** — `re.search` returns first only; the fallback's `( timeout 60 … )` wrap is unverified by THIS test. Fix: `re.findall(...)` + `len == 2`, or a second `re.search`.
2. **`--size 1200` assertion is file-wide** (`--size 1200 in body`) — a comment match elsewhere in workflow.md would pass even if the fenced block lost the flag. Fix: scope to the fenced block.
3. **Bare `ulimit -v` negative-assertion is parens-required** (`\(\s*ulimit\s+-v`) — a bare `ulimit -v 8G` line would slip. Fix: `^\s*ulimit\s+-v` MULTILINE.
4. jq `2>/dev/null` dropped in doc quote (plumbing, not contract — leave).
5. install-opendesign mirror "Reversal" overstatement — rename to "fences/steps/verify/cross-refs" or add a 2-line Reversal note.
6. "byte-for-byte" wording — soften to "command-for-command identical modulo the declared variable mapping" (match the dispatch framing).
7. rule.md audit-pin gap — optional 4th pin grepping the Cardinal/Guideline structure (e.g. `^1\. \*\*VALIDATE all Mermaid`) to close the T6 asymmetry.

## Verdict

**✅ USER-STORY-VERIFIED @ 68412058** — the doc+test contract now ensures the next fresh-host first render is boring: the 6 install-skill defects are encoded lib-verbatim, the render timeout contract is wired and pinned, the pre-warm provisioning boundary is explicit, the parity closure is mutation-proven, the audit+seal are green and idempotent, and the render_image opt-in semantics are uniformly taught. Merge gate: **PASS from the testing lane**, subject to the 🟠 version-bump/CHANGELOG adjudication (release-hygiene, not doc-contract).
