# Phase 0 Round 1 — Delta Verification (mermaid artifact risk + fix presence)

- **Date:** 2026-11-09
- **Reviewer:** Reviewer (v2) — review controller; adjudication of two parallel read-only verification workers
- **Target:** `.agents/shared/planning/skill-system-revival/phase0-investigation.md` (commissioned at 1,488 lines / 141,838 B — **it grew to ≈1,588 lines / 152,179 B during this review; see §5**)
- **Reference:** `.agents/shared/planning/skill-system-revival/phase0-review.md` (round 0, B1–B10 + extras) — **left untouched**
- **Workers (one skill each):**
  - TASK 1 (mermaid): `e8fee6ba-735b-48b6-b392-af8af4348383` — `code-review`
  - TASK 2 (fix presence): `5d4bff70-fc6d-4b34-b811-b134e6fa9150` — `plan-review`
- **Discipline:** no participant edited the report, the findings files, the daemon, the DB, or git. The TASK-1 worker performed **read-only `SELECT` queries inside `BEGIN READ ONLY`** against prod `ensemble_prod` to recover the charter-original baseline (no writes). Scratch artefacts confined to `/tmp`. This file is the only write.

---

## VERDICT

**PASS on both tasks.** TASK 1 (the untested artifact risk): **PASS** — the current Appendix B mermaid block was validated by **two independent real renderers** (repo `mermaid@11.16.0` and the house live-view bundle `mermaid@12.1.0`), rendering to 25 nodes / 4 clusters / 23 edges / **0 phantom nodes**, with every `classDef`/`class`/`style` directive applied; the diff against the true charter-original is **exactly the two intended label lines** and nothing else. TASK 2 (fix presence): **PASS — 13 of 13 fix items PRESENT** (B1 with all four sub-elements, B2 with no merging sentence across all eleven `6,052` occurrences, B3–B10, E1–E3) and **R1–R6 all PASS**, including no-action discipline and a clean secret sweep over the newly added blocks.

**Round-0 status:** all **10 blocking defects are discharged** — round 0's verdict moves from *APPROVE-WITH-FIXES (pending)* to *fixes verified landed*. **Final sign-off is still gated on three 🟡 residue items** (all in text the round-1 edits added or touched), on the in-flight Lane-1 reconciliation of B1/B2, and on one **coverage gap** created by the target file mutating during this review (§5).

---

## TASK 1 — MERMAID SYNTAX VALIDATION (Appendix B §B.3)

### 1.1 Parser availability — a real parser WAS available; nothing was installed

| Attempt | Command | Result |
|---|---|---|
| mermaid-cli on PATH | `command -v mmdc` | **not found** |
| repo-local mermaid | `ls frontend/node_modules` | **FOUND** — `mermaid@11.16.0`, `@mermaid-js/parser@1.2.0`, `dompurify@3.4.11`, `jsdom@29.0.2`, `playwright@1.60.0` (+ headless chromium in `~/.cache/ms-playwright`); no `.bin/mmdc` |
| `npx --no-install` (from `frontend/`) | `npx --no-install @mermaid-js/mermaid-cli --version` | **loud refusal, no fetch** (`missing packages and no YES option`); aborted. **No bare `npx` was attempted** (repo convention honoured) |
| Python mermaid parser | `find_spec` for `mermaid` / `pymermaid` / `mermaid_parser` | none |

Harness (all scratch under `/tmp/mermaid-validate-0bXaeR/`, `node_modules` symlinked to the repo's `frontend/node_modules` — **nothing written into the project**): `mermaid.parse()` grammar phase via jsdom; **real SVG render** via Playwright + headless Chromium + `mermaid.min.js`.

| Renderer | parse | render | SVG | node `<g>` | clusters | edges |
|---|---|---|---|---|---|---|
| `mermaid@11.16.0` (repo dep) | **PASS** | **PASS** | 50,639 B | 25 | 4 | 23 |
| `mermaid@12.1.0` (house `live_views` bundle — the authoritative renderer for this doc) | **PASS** | **PASS** | 46,405 B | 25 | 4 | 23 |

**Non-vacuity disclosed by the worker:** the grammar harness detected 4/8 controls and the render harness 3/7 — the undetected classes are mermaid's real *semantics* (it auto-creates undefined nodes, tolerates `style`/`class` on undefined ids, allows an unclosed paren inside a quoted label). Those classes were closed by **rendered-output census** (node-id census + style-fill census) rather than by the parser; genuinely broken constructs (unbalanced quote, unclosed bracket, unclosed subgraph, malformed `classDef`) were detected by both harnesses.

### 1.2 Validation verdict

**PASS — no defect; no corrected block needed.** Block location: fences **L1506–L1578**, content **L1507–L1577** (73 lines incl. fences). Pinned content hash `sha256(block.mmd) = 657f1e60bf1346fcafea746899ca36d30e3d1fdba3dddeb47a1176649ad68866` — stable across four re-reads.

### 1.3 Diff against the charter-original — baseline provenance

**Git could not supply a baseline:** the planning file is **untracked** (`git status --porcelain` → `?? …/phase0-investigation.md`; `git log --all -- <path>` empty; `git ls-files` empty; no stash, no sibling-worktree copy, no other copy on disk). The round-0 review file does **not** transcribe the original node texts (it only asserts "CST names three env surfaces" and "O1 matches §3.6").

**Correct baseline recovered from the live daemon's own message store** (read-only `SELECT` in `BEGIN READ ONLY`, prod `ensemble_prod` @ `10.44.0.2:5432`): `message_queue.message_id = 05a884b4-43ec-49fe-b98f-6593fd216a60`, instance `4175a230-baa4-429d-a5ee-237b9ee5149c`, source `internal_agent:9f42eaf8-…` (this commission's tree root), enqueued `2026-11-09 05:35:24Z`, 20,928 B — mermaid block at lines 76–148 (fences) / 77–147 (content). It is the *received* block verbatim and independently corroborates the file's own integrity note: it contains the **`BALL`** typo and the **three-surface** `CST`. A third baseline (the doc-writer's `completion_report`, `message_id = 00889b14-6a26-468b-8eb2-ba38ce414deb`) shows the intermediate state — `O1` already `BOTH`, `CST` still three-surface — bracketing the two edits in time.

**Diff (fence-inclusive, original L76–148 vs current L1506–L1578):**

```diff
@@ -8,7 +8,7 @@
         CG3["skill_execute_capture tool returns a skipped envelope"]
-        CST["absent from live env, repo .env and demo env - lane OFF by ABSENCE of the variable"]
+        CST["absent from repo .env, live .env, live process env and demo process env - lane OFF by ABSENCE of the variable"]
         CSW --> CRES --> CG1 --> CG2 --> CG3
         CSW -.- CST
@@ -43,7 +43,7 @@
     subgraph BRAKE["OPERATIONAL ASYMMETRY - per-call env resolution vs boot-fixed os.environ"]
         direction LR
-        O1["ALL switches in BALL lanes resolve per-call from os.environ"]
+        O1["ALL switches in BOTH lanes resolve per-call from os.environ"]
```

**Total: 2 lines differ.** Enumeration by category — node ids: 25 → 25, **identical set**; node labels: **2 changed** (the two intended), other 23 identical; edges: **23 → 23, identical list and order** (`-->`×16, `-.-`×4, `-.->`×3), 0 edges to undefined ids; `classDef`: 3 (`sw`, `gate`, `fact`) identical names/style-lists/order; `class` assignments: 3 identical; `style` directives: 2 (`BRAKE`, `INCIDENT`) identical text; subgraphs: 4 (`CAPTURE`, `EVOL`, `INCIDENT`, `BRAKE`) with 4 `end`; `direction`: 3×`TB` + 1×`LR`; comments: 7 identical; fences: identical; whitespace: LF only, 0 CR, 0 tabs, 0 trailing WS; line count 71 → 71.

**Change detail.** `CST` (original L10 → file L1516) is a **full rewrite of the surface list**, not a pure insertion (`live env`→`live .env`, `demo env`→`demo process env`, order swapped, `live process env` inserted) — faithful to the stated intent: §3.1's table header and §0's summary name the same four surfaces. `O1` (original L45 → file L1551) is a pure typo correction, `BALL`→`BOTH`.

### 1.4 Manual grammar check (16 items, all PASS)

Fence pairing (opens ```mermaid, closes ```, no interior fence; whole-file 22 fence lines balanced) · node-id charset (29 ids, all `^[A-Za-z_][A-Za-z0-9_]*$`) · quote balance per label (every label line even `"` count) · no unescaped inner quotes · risky chars (`;` `<` `>` `#` `&` backtick — only `>` occurs, inside `EVB`'s quoted label, legal and confirmed verbatim in the rendered SVG) · brackets balanced (square/brace/paren deltas all 0) · edge syntax (0 malformed) · edge targets defined (referenced 25, undefined 0, renderer emitted no phantom nodes) · subgraph open/close 4/4 · `classDef` syntax + application (5/5/2 fills) · `class` assignments all defined · `style` targets exist and applied · comments not swallowing syntax (7) · CRLF/tab/trailing-WS (0/0/0) · long lines (max 255 chars, wraps fine) · **both edited labels character-clean** (inner quotes 0, colons 0, slashes 0, parens 0; CST commas 2, O1 commas 0).

### 1.5 Explicit confirmations

✅ Only the two intended label changes (+ the `BALL`→`BOTH` typo fix) are present. ✅ No edge missing. ✅ No subgraph missing. ✅ No `classDef`/`class`/`style` missing. ✅ No comment missing. ✅ No unbalanced quote or bracket. ✅ Rendered-output text diff differs in **exactly** the two intended label strings; the only other SVG deltas are y-coordinate shifts from the longer `CST` label wrapping one extra line (`label-container` 150→171) — cosmetic. ✅ The doc-writer's integrity note ("edges, classDef and style byte-identical to the received block") is **independently proven**, not merely asserted.

### 1.6 Residual risk (from the validation)

1. ⚠️ **The target file mutated during the review** — see §5.
2. ⚠️ **Host markdown rendering untested** — the block is valid for mermaid ≥9; only 11.16.0 and 12.1.0 were exercised. The live-viewer's full markdown→mermaid request path was tested at bundle level, not end-to-end through `live_views.py`.
3. 🟢 `securityLevel: strict` DOMPurify applied on both renders; labels contain no HTML, so nothing was sanitised — a hostile-label concern is moot (but a future label containing `<`/`&` would be stripped).
4. 🟢 **Semantic claims in the diagram are unvalidated by any renderer** — `O4` ("is_paused=false since 2026-10-26") and `EVN` ("all three surfaces live on v0.29.7") are runtime claims with live-DB dependency. A label can render perfectly and still be wrong; that class is outside any parser's reach.
5. 🟢 Harness coverage — mermaid's parse phase is grammar-only; undefined-node/`style`-target/undefined-class defects were closed by rendered-output census, not by the parser.
6. ℹ️ **Baseline durability** — the charter-original exists only in the prod DB message store and `/tmp/mermaid-validate-0bXaeR/original_block.mmd` (`sha256 c139812146dd4f2d84e9eb837076595996927e22752f0fc4ee8cc049cba60fe7`). A prod-DB deletion would destroy the only copy of the pre-edit diagram. Recommend preserving that file into the findings directory.

### 1.7 Adjudication — one correction to the round-1 premise

The TASK-1 worker noted the round-0 gate "missed" the `BALL` typo. **My adjudication: round 0 was not wrong.** The `BALL` typo existed only in the **charter-original**; the doc-writer corrected `O1` to `BOTH` **during drafting** (the doc-writer's own completion report already shows `O1 = BOTH` with `CST` still three-surface), so the file state the round-0 auditors read already read `BOTH`. Round 0's certification "BRAKE `O1` node matches §3.6" was accurate **for the file it read**; the commission's premise for this round ("`O1` now reads BOTH — a fix for a transcription typo") is confirmed correct. **The real round-0 gap was different and narrower:** round 0 audited the diagram *as rendered in the file* and never diffed it against the charter-original, so it could not detect an edit-against-source drift; that is exactly the risk this round closed. (Lesson recorded to the knowledge base.)

---

## TASK 2 — FIX-INVENTORY PRESENCE CHECK

### 2.1 Blocking defects B1–B10 (13-item set, all PRESENT)

| # | Where landed | Verdict | Evidence |
|---|---|---|---|
| **B1** | §5.7 L1008–1030; §6.2 L1114; §6.3 L1131 | **PRESENT (4/4)** | ⛔ block header L1008 *"UNRESOLVED CROSS-LANE CONFLICT — the drift row does not close arithmetically"*; L1011 *"**6,536 − 5,057 = 1,479, not +479.** The stated +479 is consistent only if the comparator is **6,057**"*; L1027–1028 *"reported as a **range, not a point: between +479 and +1,479**"*; L1114 *"**PROVISIONAL — the baseline is UNRECONCILED (§5.7).**"*; L1131 *"**do not threshold on +479 or +1,479**"* — no threshold gate survives (every `+479` occurrence is a disclosure or negation) |
| **B2** | §5.7 L1038–1055 (table L1041–1044) | **PRESENT** | ⛔ block L1038 *"the numeral 6,052 carries two incompatible definitions"*; L1039 *"The two lanes count **DIFFERENT populations**"*; side-by-side table carries a `Source` column (Lane 3 H5/§6.1 vs Lane 1 B11); L1050–1051 *"**Both numbers are retained; neither is deleted and no side is silently chosen.**"* — all 11 `6,052` occurrences enumerated, **none merges the two populations** |
| **B3** | §2.7 L331–339 | **PRESENT** | L331–333 *"**no `daily` condition_type exists in `skill_triggers`** — the daily cadence is governed by the **config** setting `SKILL_EVOLUTION_DEFAULT_DAILY_SCAN_HOUR=3** (`.env.example:138`)"* + YAML knobs `default_daily_scan_hour: 3`, `default_task_count_threshold: 20`, `metric_scan_interval_hours: 24.0` |
| **B4** | §5.7 L1057–1064 | **PRESENT** | L1059 *"*(Authorial inference, not a lane finding: concurrent writes during the live window are a plausible hypothesis — **no lane states it**."*; L1060–1062 *"all three denominators — 10,056 (Lane 1 B1), 10,057 (Lane 1 B11 matrix sum), 10,059 (Lane 1 B11 selected) — arise **within Lane 1's own query sequence**"* |
| **B5** | §4.1 L651, L657–660 | **PRESENT** | L651 *"it is why capture has produced no junk since the gate landed on 2026-09-17"*; L657–660 *"**Do not read this as an explanation of the 2026-09-01 stop.** … Its role is **reinforcing, not causing** … The **cause of the 2026-09-01 stop remains UNVERIFIED (§2.8)**"* |
| **B6** | §6.1 L1101 | **PRESENT** | **P12** *"Ensure the intended capturing agents have `skill_injection: true`"*, 8 agents named, *"only **16 of 24** agents qualify"* — re-verified byte-for-byte against `agents/*/meta.json` |
| **B7** | §6.1 L1094; §6.3 L1129 | **PRESENT (both halves)** | **P5b** *"A code fix reaches live only via a PROMOTE, not an env-flip restart"* … *"P1–P3 land nothing until a promoted release contains them"*; Phase-1 exit criterion *"and **PROMOTE** the release that contains them … the fix is present in the PROMOTED release (`releases/state.json` `current`)"* |
| **B8** | §0 L56 vs §5.3 L918 | **PRESENT** | row 13 *"makes F1/F3 report `dark / capture_off` unconditionally and F6 report `dark / census_gated`, with F1/F6 publishing live series (F3's production publishing **not verifiable from code alone** — §5.3)"* — table/body now agree; §0 still 15 rows |
| **B9** | §6.3 L1138–1146; §6.4 L1159–1160 | **PRESENT** | re-anchored to capture ordering — *"a capture lane turned on before P2 lands **re-enables in name only** and risks re-creating the **2026-08/09 \"junk-skill class\"** … [Lane 3 M2; **Lane 3 §7 item 2**]"*; the §7.4 anchor is **replaced**; rationale 2 and §6.4 carry no junk-skill clause; the only other occurrence is the verbatim Lane-3 quote at L655 |
| **B10** | §6.1 L1102 | **PRESENT** | **P13** *"inactive-but-served skills still accumulating usage"* — *"an **inactive** skill (`cb1813ea`) was still served to a live coder agent as recently as **2026-11-09T05:09Z**"*; anchor phrase verified verbatim in `findings/lane3-flakiness-evidence.md:204` |

### 2.2 Extra fixes E1–E3

| # | Where landed | Verdict | Evidence |
|---|---|---|---|
| **E1** | §5.7 L1066–1070 | **PRESENT** | demo claim now cites **Lane 1 only**; the `[Lane 3 §c.8]` co-citation is **deleted** and replaced by an explicit non-citation *"(Lane 3 §c.8 explicitly did **not** query the demo DB…)"* — `§c.8` now appears once, only as a non-citation |
| **E2** | §3.9 L605–609 | **PRESENT (knob landed)** | *"`SkillEvolutionConfig.fix_cooldown_seconds` — default **1800** (30 min), enforced at `skill_job_dispatcher.py:641-657` … `0` disables the cooldown"* — name/default/enforcement site re-verified at `origin/latest` (`daemon/services/skill_job_dispatcher.py:327`, `:641-657`). **But see §3 defect #1** — the sentence's causal clause does not hold |
| **E3** | §7.D L1227 → §7.E L1240 | **PRESENT** | item 26 *"**Not recoverable.** Requires the design change in open question **34**"* → Q34 *"**A transcript-preserving capture design decision.**"* — resolves correctly |

### 2.3 Regression spot-check R1–R6 — 6/6 PASS

| # | Verdict | Evidence |
|---|---|---|
| **R1** | PASS — not smoothed | §2.5 L292–294 still states the 146-vs-141 split *"is therefore not self-consistent as written"* |
| **R2** | PASS | §3.5 L486–502 still carries *"**The source says \"created\", not \"captured.\"**"* |
| **R3** | PASS | §0 L44–58 = 15 rows, sequentially numbered; row 13 hedge matches §5.3; row 10 (41+63+1) matches §2.7; row 15 still *"Unverified"* |
| **R4** | PASS | every new block is disclosure/precondition/non-action — L1013–1014 *"no side is silently chosen and neither number is deleted"*; L1022 *"**Two candidate resolutions, neither chosen here:**"*; L1026–1027 *"must not be used as a threshold"*; §6 banner L1078–1080 still *"RECOMMENDATIONS ONLY — NOT YET IMPLEMENTED, NOT SCHEDULED, NO DECISION TAKEN."* — the Phase-1 PROMOTE line (L1129) reads as an exit criterion, not a completed promote |
| **R5** | PASS | pattern sweep over all 1,488 lines: 0 matches (`sk-(proj|ant)-`, `api_key=`, `bearer <token>`, `password=`, `secret=`, `token=`); new blocks carry **variable names only**; §1.7 L193 still withholds the key value |
| **R6** | PASS | all four §1 hard limits still carried (transcripts swept §1.3 + L1116/L1227; detached-HEAD §1.4 with the new P5b release anchor matching §1.2; log contamination §1.5 + L1111 carrier-filtered; dirty checkout §1.6 + P9's "+819 lines"); the new §5.7 provenance limit is consistent with §1.6's untracked-file picture |

---

## 3. NEW / RESIDUAL DEFECTS IN THE ROUND-1-EDITED TEXT

All three 🟡 items sit in text the round-1 edits added or touched. **None invalidates a round-0 fix**; all are one-clause edits.

**N1 — 🟡 REQUIRED (§3.9 L608–609): the new cooldown sentence asserts a causal role the file's own evidence contradicts.**
Text: *"it is the mechanism that bounds the \"same skill re-FIXed 3× in 37.1 min\" burst (§2.3)"*. But §2.3 L253–254 gives the burst timestamps `21:26:09 → 21:49:02 → 22:03:13` (2026-10-21) — 22.9 and 14.2 minutes apart, **both far under the 1800 s default**, so a working cooldown would have refused the 2nd and 3rd FIX; and §4.6 L792 marks the control *"FIX idempotency key … + 1800s per-(project,skill) cooldown, FIX-only | **DESIGNED (D2)**"* with the design record dated 2026-10-22 (L785) — **after** the burst (the lane's hardening commits `f556e14e3` 21:30 / `5df2c2827` 22:18 are also 2026-10-22). The knob's *facts* are right; the *"bounds that burst"* linkage is unsupported and is a new over-assertion of exactly the B4 class.
**Fix:** reword forward-looking — *"the knob bounds **future** repeat-FIX throughput (1800 s per (project, skill)); the 2026-10-21 burst predates its design/hardening (`f556e14e3`, `5df2c2827`, 2026-10-22) and was not prevented by it"* — or drop the burst clause.

**N2 — 🟡 REQUIRED (closing confidence, L1481–1483): the report's final statement still enumerates only the pre-round-1 variances.**
Text: *"the two cross-lane numeric variances (the 141-vs-146 queue split, the 10,056/10,057/10,059 usage-row denominators) are disclosed in §2.5 and §5.6 rather than smoothed over."* The drift conflict (§5.7 L1008) and the 6,052 collision (§5.7 L1038) — now the file's most prominent disclosures — are omitted, and the denominators variance now lives in **§5.7** (L1057), not §5.6. A reader who stops at the closing line does not learn that the drift figure and the 6,052 figure are unreconciled.
**Fix:** extend to name all four and correct the section pointer: *"the cross-lane numeric variances — the 141-vs-146 queue split (§2.5), the 10,056/10,057/10,059 usage-row denominators (§5.7), the counter-drift conflict (§5.7 ⛔ block) and the 6,052 two-definition collision (§5.7 ⛔ block) — are disclosed rather than smoothed over."*

**N3 — 🟡 REQUIRED (§7.F L1256): a numeric claim the file does not support.**
Text: *"12 of the 38 items are marked SUPERSEDED (resolved by a later lane)"* — only **5** of the 38 §7 rows carry a superseded marker (items 11, 12, 15, 17 *"partly superseded by Lane 3 §6.2"*, 33); the sentence's other two counts verify clean (*"3 … NOT RECOVERABLE (26, 30, 31)"* ✓, *"2 … CROSS-LANE CONFLICTS (37, 38)"* ✓). The `12` was carried through an edited line. Under the round-0 bar (an unsupported number is a failing number), this must be corrected.
**Fix:** change to *"5 of the 38 items are marked SUPERSEDED (resolved by a later lane)"* — or enumerate the twelve if other markers were intended.

**Non-blocking (🟢) — optional:** (1) §1.7 L201 under-enumerates the disclosed collisions — align with §5.7/§7.F; (2) §6.3 L1129 uses `6,052` without naming the definition, against the file's own rule at L1050–1051 — append *"(Lane 3's definition)"*; (3) §5.7 L1032–1036 still carries the pre-existing self-contradiction (`applied=F/feedback=F 469` vs *"feedback_applied is NULL whenever applied is false"*) one line above the new ⛔ block — add one line reconciling the third figure; (4) P12's *"the 8 `false` agents … are **silent capture sources**"* is semantically ambiguous (they are **excluded** sources) and is not Lane 2's wording — reword to *"permanently excluded as capture sources — a silent capture gap"*; (5) §5.7's closing confidence line should carry the same unreconciled caveat that §0 row 13 and §6.2 now carry; (6) P13 (inactive skill served to a live **coder** agent) vs P12 (`coder` is one of the 8 `skill_injection: false` agents) — add one clarifying clause on whether `skill_injection` also gates injection (`daemon/manager.py:5435 _wire_skill_injection_capability_gate` exists). **ℹ️ informational (pre-existing):** mixed open-question numbering (L611 cites "open question 6" where the privilege model is 2; L1097/L1099 cite Lane-1's own OQ ids; P13's "census finding 2" is Lane 3's label and undefined in this report).

---

## 4. NOT ADJUDICATED (per instruction)

**B1/B2 numeric reconciliation remains open** and is owned by the in-flight Lane 1 pass. The current text is explicit about it: L1024 *"**This was not reconciled by any lane.**"*; L1050 *"**Most plausibly Lane 1's matrix cell label is transposed or conflated … but no lane reconciled it.**"* — the disclosure discipline round 0 demanded is in place. **Note for that lane:** the two independent handles on the same tangle are §5.7's `5,057`/`6,057` comparator pair (L1018–1019) and the `6,056` applied-row total implied by the Lane-1 matrix (L1047) — a canonical re-query must reconcile both.

---

## 5. COVERAGE GAP — the target file mutated during this review

The file grew **1,488 → 1,568 → 1,588 lines** (mtime `2026-11-09 06:23:49Z`, 152,179 B) while the audits were running, as a concurrent lane appended §0 board content. The Appendix B block's fences moved `L1406–1478 → L1486–1558 → L1506–1578` across re-reads **while its content hash stayed `657f1e60…` on all four checks**. Consequences:

- **TASK 1 is unaffected** — the block's content was hash-pinned and re-verified immediately before reporting.
- **TASK 2's line references are point-in-time** — the quoted fix text is durable (content-based verdicts), but the line numbers may already have shifted. Cite the quoted text, not the line.
- **The appended tail was NOT covered** by the fix-presence check — the ~100 lines added after the fix-check worker's read are unverified for B1–B10/E1–E3 presence and for the R1–R6 regressions. **Required: one more delta pass over the tail once the writer's pass settles.**

---

## 6. RECOMMENDATIONS (ordered)

1. **Fix N1, N2, N3** (three one-clause edits in §3.9, the closing confidence paragraph, and §7.F) — then the round-1 residue is closed.
2. **Re-run the fix-presence delta over the newly appended tail** (≈L1488–1588 region) once the writer's pass is settled; the current check covered the 1,488-line state.
3. **Land the Lane 1 reconciliation of B1/B2** (canonical re-query reconciling `5,057`/`6,057`/`6,056` and the two 6,052 definitions), then re-verify §5.7's two ⛔ blocks collapse into a single resolved statement and that §6.2's PROVISIONAL marker and §6.3's no-threshold wording are updated accordingly.
4. **Preserve the charter-original diagram** (`sha256 c139812146dd4f2d84e9eb837076595996927e22752f0fc4ee8cc049cba60fe7`, currently only in the prod DB message store + `/tmp`) into the findings directory for audit durability.
5. **Re-point the mermaid block's canonical pointer to the content hash** `657f1e60…` in the §B.3 integrity note so future reviews can cite a stable identifier instead of drifting line numbers.
6. Optional: the six 🟢 items in §3.

**Round-1 disposition:** TASK 1 **PASS** · TASK 2 **PASS** · round-0 blocking defects **10/10 discharged** · residue **3 🟡 required + 6 🟢 optional** · verification-coverage gap **1** (moving target) · B1/B2 reconciliation **open by design**.

---

## 7. SESSIONS & DISCIPLINE

- TASK 1 (mermaid): `e8fee6ba-735b-48b6-b392-af8af4348383` — `code-review` skill, feedback recorded. Real-parser validation via repo `mermaid@11.16.0` + house bundle `mermaid@12.1.0`; read-only prod-DB `SELECT` for baseline recovery; scratch confined to `/tmp`; no bare `npx`; zero writes to the project.
- TASK 2 (fix presence): `5d4bff70-fc6d-4b34-b811-b134e6fa9150` — `plan-review` skill, feedback recorded. Read/grep only; no mutations.
- Adjudication, variance dispositions, verdict: Reviewer (this file). **`phase0-review.md` (round 0) left untouched.** This file is the only write produced by the round-1 review lane.


---

# FINAL GATE — Phase 0 Deliverable + 7-File Working Set (pre-commit)

- **Date:** 2026-11-09 · **Reviewer:** Reviewer (v2) — adjudication of three parallel read-only verification workers
- **Target:** `.agents/shared/planning/skill-system-revival/phase0-investigation.md` — 1,660 lines / 160,517 B, mtime 06:34:14Z (settled; all three workers measured identical size/mtime)
- **Working set swept (7 files):** the report · `phase0-review.md` · `phase0-review-round1.md` · `findings/lane1-runtime-db.md` (902 ln) · `findings/lane2-code-config-history.md` (644) · `findings/lane3-flakiness-evidence.md` (242) · `findings/charter-original-diagram.md` (116, new)
- **Workers (one skill each):** report content/tail/standing → `e86fd7fd` (`plan-review`) · artifacts/git/provenance/diagram → `3e3914e0` (`code-review`) · whole-set secrets + censuses → `fc3d5aab` (`security-review`)
- **Discipline:** all three read-only; zero edits to any file in the working set; prod DB touched `SELECT`-only inside `BEGIN READ ONLY` (baseline + giter-report recovery); scratch confined to `/tmp`; no git mutation, no network installs, no bare `npx`. This appended section is the only write.

## VERDICT

# ✅ APPROVE

**No blocking defect exists in the Phase 0 deliverable or the 7-file working set.** All three independent auditors returned zero blocking findings; every round-0 and round-1 blocking defect is verifiably discharged; the two numeric conflicts are closed by DB reconciliation with the superseded figures retained and marked; the artifact identity is proven by three independent hash recomputations; the set is secret-clean; and the report still reads as read-only evidence with the honest headline intact. **Commit recommendation: GO** — subject only to the two coordination items in §4, neither of which is a defect in the artifacts.

### Per-check results

| Check | Scope | Result | Key evidence |
|---|---|---|---|
| **(1)** | Round-3 fixes N1/N2/N3 | **ALL THREE FIXED** (re-derived, not taken on trust) | N1 §3.9 L638–642 *"It bounds **future** repeat-FIX throughput … that burst's own gaps were **22.9 and 14.2 minutes**, both **below** the 1800 s default … the knob was **DESIGNED on 2026-10-22** … **So that burst was NOT bounded by the cooldown.**"* — whole-file sweep: **zero** survivors claiming the cooldown explains/prevents the burst. N2 closing L1652–1655 enumerates **all four** variances with the stale §5.6 pointer corrected to **§5.7**. N3 §7.F L1397–1400 *"**5** of the 39 items are marked SUPERSEDED … items 11, 12, 15, 17, 33"*; independently re-derived: **39 §7 rows numbered 1–39, zero gaps/duplicates, exactly 5 superseded markers**; *"12 of the 38"* → **0 hits**. |
| **(2)** | Tail coverage (round-1 gap) | **CONSISTENT** — §5.6.1, §5.7 audit trail, §6.2, §6.3, §7.F 37/38/39, §0 row 13 + blocker bullets | §5.6.1 L1021–1059: **3,782 @06:14:55Z = 9,207 − 5,425** ✓, row-level **3,450** ✓, all-instances **3,900 = 9,724 − 5,824** ✓ — byte-consistent with lane1 R1.b/R2.b. §5.7 L1129–1137: both conflicts **RESOLVED** with root causes (*"Lane 1 transcription typo: the SQL returned `6,057`; Lane 1 wrote `5,057`"*; *"Lane 3 label swap at source (L184)"*) and the R1.b query reproduced verbatim. §6.2 L1251 *"drift ≤ +480 (counter **6,547** − ledger **6,067** = **+480**; the original ~05:19Z measurement was **+479**)"* ✓. §6.3 L1268 *"counter drift ≤ +480 … measured by Lane 1's **R1.b single repeatable query**"* ✓. §7.F 37/38 carry question **and** resolution **and** re-run instrument; 39 = OPEN, coder/`skill_injection: false` tension, cross-referenced from §6.1 P13. §0 row 13 *"drift is growing (**+423 → +480 over 5 days — DB-reconciled, §5.7**)"* ✓. |
| | | **Three caveats: ALL PRESENT AND CORRECTLY SCOPED** | (i) §5.6.1 L1055/L1067 *"3,782 is an **UPPER BOUND of the gate-4 surface**, not the exact population … an upper bound on gate-4 misclassification, not a capture forecast"* (echoed §0 L66, §6.1 P1, §6.2). (ii) §5.6.1 L1070–1073 *"*\"refuse nearly every candidate\"* is a **code-derived prediction with anchors — never observed against live traffic**, because **zero post-gate capture attempts exist to inspect** (the P0 gates landed `09d50c78b` on 2026-09-17, *after* the last capture job on 2026-09-01)"*. (iii) 29 occurrences of 3,446/3,450/6,052/6,063 enumerated — every one labelled to its population; **zero merges**. |
| **(3)** | Round-4 §1.7(e) hard limit | **CONFIRMED** (against the giter's own report, located in prod message store `7c8afdd1`, plus live git state) | 6 staged entries verified in the canonical index (`A scripts/audit/borrow_census.py`, `A scripts/upgrade/journal_read.py`, `M scripts/upgrade/{lib,promote,stage,status}.sh`); both consequences confirmed (commit-in-canonical would sweep them; `stage.sh:161` reads committed HEAD, so no release carries them); DISTINCT framing vs `7986c83b` confirmed; anomaly `bdaad6ce-9ee8-4677-a746-3d0217d2bab7` confirmed; canonical tree untouched — HEAD still detached at `013895287`, 95→95 dirty, 74→74 untracked at the audited pass. **§1.7 → §1.8 renumber broke no cross-reference** (§1.5 ×3, §1.6 ×2, §1.7 ×2 all point at the sections they mean; no broken incoming links to §1.8). **§B.2 carries BOTH anomaly ids** (L1534, L1535). |
| **(4)** | New file `findings/charter-original-diagram.md` | **CONFIRMED** | (a) provenance header accurate and independently re-verified: message row `05a884b4-…` exists with matching instance/source/enqueue `2026-11-09 05:35:24.96254`/length 20,928, and git genuinely gave no baseline (`ls-files` empty, `log --all` empty, `status` → `??`). (b) block **byte-identical** to that row (0 diff), **retains the `BALL` typo** (L87) and the **three-surface `CST`** (L52); recomputed sha256 = `c139812146dd4f2d84e9eb837076595996927e22752f0fc4ee8cc049cba60fe7` — exact match. (c) computed diff vs the report's Appendix B block = **exactly 2 modified lines** (`CST`, `O1`), matching the stated diff. (d) **no secrets**. (e) report §B.4 L1631–1634 pointer resolves to the file and describes it correctly. |
| **(5)** | Diagram integrity | **UNCHANGED — still the validated artifact** | sha256 of the 71 content lines recomputed **independently by two workers** = `657f1e60bf1346fcafea746899ca36d30e3d1fdba3dddeb47a1176649ad68866` — identical to the round-1 pin, which was **parse+render validated against two real renderers** (repo `mermaid@11.16.0` + house bundle `mermaid@12.1.0`; 25 nodes / 4 subgraphs / 23 edges / 0 phantom nodes). Fences now **1557 / 1629**, content 71 lines (1558–1628) — **fences shifted, content unchanged**; no re-validation needed. |
| **(6)** | Whole-set secrets sweep (7 files) | **CLEAN — 0 possible live secrets** | 10 matched lines across 7 files, **all classified benign**: pattern *names* inside my own sweep narrative, three content sha256 digests (recomputed, exact), `idempotency_key` code template, `export "$key=$value"` shell quote, `KEY=VALUE` placeholder, and a vendor base URL. Zero-hit families: `sk-`/`sk-proj-`/`sk-ant-`/`xoxb-`/`ghp_`/`github_pat_`, `Bearer`/`Basic`, `BEGIN *PRIVATE KEY`, `AKIA`/`ASIA`/`aws_secret_access_key`, JWT `eyJ`, `scheme://user:pass@`, Fernet `gAAAAA`, URL-with-credentials. Entropy sweep (40 candidates) and full `NAME=value` census: **no credential-shaped token**. **`API_KEY=(redacted)` (lane1:217) is a VERIFIED genuine redaction** — literal marker, no partial value/prefix, and **no alternate-form leak**: no `sk-` prefix, no base64 form, no digest (the known live-key digest prefix is absent); corroborated by three independent presence-only statements (lane1:450, lane1:721, report L221–224). |
| **(7)** | Final consistency | **PASS** | All **15** §0 verdict rows re-checked independently against their bodies — **no contradiction, no over-assertion** (row 13 now hedges F3: *"F3's production publishing **not verifiable from code alone**"*). Fences **balanced 7/7**. Superseded figures: `5,057` ×4, `1,479` ×3, `−589` ×2 in the report — **every one marked** and confined to the audit trail / retained question / in-place correction banner; **0 unmarked survivors**; ASCII `-589` → 0 hits. `PROVISIONAL` and `UNRESOLVED CROSS-LANE` = **0 in the report and 0 in all three findings files**. Population labels: 6,057/6,067/+479/+480/+421/+423 all consistently labelled; trend column re-derived (counter +478, ledger +421, drift +57 = 480 − 423 ✅). |
| **(8)** | Deliverable standing | **PASS** | MODE STATEMENT L9–14 intact (*"No fix was made. Nothing was re-enabled. Nothing was changed."*); §6 banner L1215 *"⚠️ RECOMMENDATIONS ONLY — NOT YET IMPLEMENTED, NOT SCHEDULED, NO DECISION TAKEN."*; §6.1 P7/P8 written as instructions to be verified, never as done; action-implying scan finds only explicitly attributed historical upstream commits and the report's own disclaimers. **Headline ordering stated plainly in §0 and softened nowhere** — L25–26 *"**Both skill lanes are OFF, and both are off by ABSENCE rather than by an explicit `0`.**"*; L31–38 *"the Oct-22 incident did not cause capture to stop — capture had been dark since **2026-09-01**, roughly **seven weeks before** the incident"*; the 2026-09-01 cause remains **UNVERIFIED** in §2.8, §4.1 L692 and §7 item 1; a softening-term sweep across all 1,660 lines returns exactly one hit — the *corrected* N1 wording. |

## BLOCKING DEFECTS

**NONE.** I am not manufacturing defects to justify the round. Across three independent auditors covering eight checks, the deliverable and the 7-file set carry zero blocking findings: the two numeric conflicts are closed by DB reconciliation (not by smoothing), every superseded figure survives only inside a marked audit trail, the diagram's cryptographic identity is re-proven, the set is secret-clean, and the no-action discipline is intact.

## NON-BLOCKING DEFECTS (fix-if-cheap; none blocks the commit)

1. **§1.7 L202 — tense slip (the one sentence in the file asserting a completed action).** *"The Phase 0 artifacts were therefore **committed** from a dedicated worktree with explicit pathspecs instead."* — at gate time no commit exists (`git log --all` / `ls-files` for the planning path are empty; the feature branch is still at `5da1021f2`). It describes a repo-hygiene action, not a skill-system action, and becomes true the moment the commit lands — so it self-resolves; but at review time it should read *"are therefore to be committed"*. One word.
2. **§7.F L1397–1400 — bold-balance rendering.** The opening `**` is consumed by the `**` before `5`, so the sentence bolds through *"gate;"*, then `5`, then the remainder plain. Cosmetic; renders acceptably.
3. **§6.1 P2 (L1227) — caveat-(ii) reminder.** P2 states *"the blank-`task_message` P0 gate (`09d50c78b`) will refuse nearly every candidate"* without the §5.6.1 honesty flag inline. The flag **is** carried at §5.6.1 L1070–1073 and §6.3 L1277–1278, so this is not a contradiction — but a reader of P2 alone meets the quantifier unlabelled. Suggested parenthetical: *"(code-derived prediction — never observed against live traffic; §5.6.1)"*.
4. **`findings/lane3-flakiness-evidence.md:33 / :185 / :215` retain the swapped 6,052↔3,446 pairing in raw text.** Each is covered by an adjacent, adjacent-line inline correction (:34, :188–192, :216) and marked superseded in place — correct practice, noted only so a reader of the raw line alone is not misled.
5. **`findings/charter-original-diagram.md` provenance wording (one clause).** The header describes the block as *"exactly as the Charter agent issued it, verbatim — including the `BALL` typo"*. Precisely: it is the block **as received in the Leader→Doc-Writer dispatch message `05a884b4`**, which carried the charter's original prompt verbatim with `BALL` preserved, while the Charter's own later report had already corrected it to `BOTH`. The substance (message id, instance, enqueue time, byte-identity, hash, untracked-file baseline absence) is verified; only this provenance clause could be tightened to name the dispatch copy as the carrier.
6. **`phase0-review.md:104`** labels the `6,052/10,057/3,446` triple only by reference. Historical round-0 text; moot now that the collision is resolved, and the round-0 record should not be edited.
7. **`findings/charter-original-diagram.md` diff-snippet context format.** The embedded hunk shows 2 context lines; a strict `-U3` generator emits 3. Cosmetic; the semantic diff is exact.

## VERIFICATION NOTES AND SCOPE HONESTY (not defects)

- **Expectation scoping on the two zero-hit censuses.** `PROVISIONAL` (3 hits) and `UNRESOLVED CROSS-LANE` (1 hit) are **exactly 0 in the report and in all three findings files**. The only occurrences are inside **my own two review files**, where they appear as *quotations of the defects those reviews were written to find* (round-1 L101 quotes the old ⛔ header; L173 names the §6.2 provisional marker as an item to update) and as the round-0 instruction to mark the baseline provisional. Those reviews are the audit trail; mutating them to hit a zero would defeat their purpose. **I am restating the expectation as "zero in the report + findings; review files may quote prior state"** — the deliverable meets it exactly.
- **Dispatch-brief arithmetic, not a report defect.** The brief said lane1 grew to *"1,788 lines"*; lane1 is **902 lines**. **1,788 is the three-file total** (902 + 644 + 242), which the report itself states at §B.1 and in its closing. The report is right; the brief compressed two facts.
- **Live drift around the audited snapshot.** The canonical checkout gained one untracked file (`.agents/approver/decision-module-tracking.md`, 06:53:12Z) from an unrelated sibling lane after the giter's pass — porcelain entries 96 (21 modified / 75 untracked). §1.7(e)'s *"95 → 95 / 74 → 74"* remains accurate **as of the audited git-setup pass**, and it is correctly attributed to that pass.
- **What was NOT closed.** (i) No fresh live DB re-query was run against `ensemble_prod` for this gate — the reconciled figures were checked against **lane1's reconciliation appendix** (the designated authority) plus independent arithmetic and the reproduced R1.b/R2.b SQL, not against a new snapshot. (ii) The mermaid **render** was not re-executed in this gate; it is carried forward on the cryptographic identity of the block (two independent recomputations of `657f1e60…`). (iii) The report's own "read-only evidence" standing was re-verified textually, not by process audit.
- **Provenance of the preserved baseline.** The charter-original exists now in-repo at `findings/charter-original-diagram.md` — closing my round-1 durability recommendation (the copy previously lived only in the prod message store and `/tmp`).

## PRE-COMMIT CONDITIONS (coordination, not content defects)

1. **Commit scope must cover all SEVEN files.** The live worktree claim (`wt.claim.skill-system-revival-phase0`) records the task as *"planning-artifacts commit (**6 files** under .agents/shared/planning/skill-system-revival/)"*, but the set is now **7** — `findings/charter-original-diagram.md` was created in review round 3, after the claim was taken. If the pathspec still says six, the preserved charter-original baseline and/or one of the two review files would be omitted from the audit trail. (This also interacts with non-blocking item 1: §1.7's "were committed" sentence becomes true only if the commit actually lands the whole set.)
2. **Commit must run in the dedicated worktree with explicit pathspecs** — never in the canonical checkout, whose index still holds the 6 staged upgrade-ceremony script entries that §1.7(e) documents as a distinct hazard (anomaly `bdaad6ce-…`). The worktree is currently clean (`HEAD 5da1021f2`, branch `feature/skill-system-revival-phase0`, 0 porcelain entries).

## FINAL DISPOSITION

**APPROVE** — the Phase 0 investigation report and its 7-file audit trail are verified faithful, internally consistent, arithmetically closed, secret-clean and honestly framed, and are ready to be committed as the Phase 0 evidence deliverable. Blocking defects: **none**. Non-blocking: 7 items (§ NON-BLOCKING, none requiring re-investigation). Two pre-commit coordination conditions attach to the commit operation itself, not to the artifacts.

**Sessions:** `e86fd7fd` (report content/tail/standing — `plan-review`) · `3e3914e0` (artifacts/git/provenance/diagram — `code-review`) · `fc3d5aab` (whole-set secrets + censuses — `security-review`). Adjudication and verdict: Reviewer. Round-0 (`phase0-review.md`) and round-1 (this file, §1–§7) content left intact; this FINAL GATE section is the only addition.
