# Decision Module P1 (SHADOW-ONLY) — NARROW RE-GATE #2 REPORT

- **Date**: 2026-11-10 (host-local) · **HEAD verified**: `c7fdee9034f2375552499bc1273dd3ee1f49111e` (= `c7fdee903`), branch `feature/decision-module-p1`
- **Base**: `818411107` · lane **35 commits / 49 files** · **fix round**: `1eabf72eb` (code) + `177c9ee20` / `cc6144968` / `c7fdee903` (docs)
- **Previous gate**: NO-GO at `72a233df3` on B-1 (content-free), D-2/D-3 (the pins that certify it), D-9 (USD-ceiling bypass), D-4/D-5 (matrix), D-6 (§15), D-7/D-8 (pack)
- **Delta measured by the gate**: 4 commits / 10 files — `daemon/services/decision/{client.py,overlay.py,MUTATION_MATRIX.md}`, `tests/unit/decision/{helpers.py,test_council_round4.py,test_council_round5.py(new)}`, `test/packs/decision_module_p1_unit_test.sh`, `.agents/shared/planning/decision-module/{decisions.md,anchor-audit.md}`, `.agents/tester/PACKS.md`. **No shared surface touched.**
- **Test leader**: Tester · 8 dispatched legs (one leg had to be re-dispatched after a mis-typed instance id consumed it) · **0 commits, 0 pushes, 0 production edits**; all mutation in isolated `/tmp` worktrees or in memory.

---

## 1. VERDICT

# **GO — WITH DISCLOSED LIMITATIONS**

**Scope of the GO: a SHADOW-ONLY, DORMANT staging.** The module cannot consult, bill, decide, or write a row without an operator action: the shipped `config.yaml` `decision:` block is fully commented out, `explicitly_configured=False`, `global_enabled=False`, the per-seam key defaults to 0, and the consult is suppressed **pre-socket** by `SWITCH_OFF` / `not_configured`. Proven by execution, not asserted: an **arming census** found **0** test files outside `tests/unit/decision/` that can arm the seam, and a construction probe showed the changed modules build a fully-installed overlay on the production `Manager` path with the consult suppressed.

**All four round-2 blockers are CLOSED, each proven by counter-mutation** (plant the defect, watch the pin go red):

| Blocker | Status | Counter-mutation anchor |
|---|---|---|
| **B-1 / D-1** content-free at both surfaces | **CLOSED** | the exact round-2 full-leak mutant ⇒ **8 needle hits** on the same capture that reports **0** at HEAD; the round-2 tree yields **11** leak flows to HEAD's **0** on an independently-written walker |
| **D-2 / D-3** the two guard pins | **CLOSED against the defect they exist to catch** | full-leak mutant ⇒ **BOTH pins RED** for the right reason (walker names the two sites; behavioural pin catches the log), plus a **payload-only** mutant that isolates the second surface ⇒ RED; restore sha-matched ⇒ GREEN |
| **D-9** USD-ceiling bypass | **CLOSED** | the round-2 bypass payload now **trips at the token bound** (attempt 20 of 40, USD pinned $0.000000) across all three usage shapes; **removing the accrual reproduces the bypass live** (0 trips, 40/40 burn); the shipped pin REDs under both reverts |
| **D-4** matrix digests re-derivable | **CLOSED** | **24/24 digests re-derived** from the artefact's own published operands (round-2: 0/15); the self-verifying pin REDs naming the leg on one changed character and on one changed digest digit |

**D-10, the scoped dual-side regression leg, and the artefact legs are in §3–§5.**

**Why GO rather than GO-WITH-DISCLOSED-LIMITATIONS-in-name-only:** the remaining items are **record accuracy** (two provenance documents overstate), **guard strength** (the leak walker can be disarmed and does not cover `manager.py`), and **house hygiene** (the pack's dead TIMEOUT branch, an incomplete scrub). None is a live behavioural defect in the shipped module, and none is a shipped guard that is green on the defect it exists to catch. But they are **not** free: §4 carries them as an explicit, numbered disclosure list, split into what must close **before the seam is ever armed** and what must be corrected **before those artefacts are cited again**. The giter's evidence-rider and the final report should quote **§4 and §5 of this document, not the lane's own artefacts** — because the lane's matrix and §15 both currently overstate.

---

## 2. WHAT THE FIX ROUND CLOSED — measured

### D-1 — content-free at BOTH surfaces (LEG-1)
`overlay.py:988` renders `error = type(exc).__name__`; the notify payload is a **six-key dict with no `"error"`**; the board body is composed from five named fields (it never carried the error, at round 2 either); `TripRecord.error` / `trip_status()["error"]` carry the type only. **Sentinel falsification across four surfaces → 0 needle hits**, with the capture forcing **124–128 loggers** to DEBUG so a leak could not hide behind a level. **Discrimination**: two mutants (the exact round-2 file compiled in memory; HEAD with the render patched back) ⇒ **8 hits each**. **Package-wide re-sweep** (independently written walker, 10 leak classes): HEAD **0** offenders vs the round-2 tree **11** — all rooted at the old `:981`. `manager.py` untouched by the delta; its decision sites remain type-only by construction.

### D-2 / D-3 — both guard pins (LEG-2)
Full-leak mutant ⇒ **both RED for the right reason**; payload-only mutant ⇒ RED on the payload assertion alone; restore ⇒ GREEN. Blind-spot matrix: the walker now catches `str(exc)`, foreign except-names, intermediate variables, `repr`, BoolOp and f-strings (all six round-2 blind shapes). Three residual holes remain (§4 A-1…A-3). The round-5 census: **11 nodes, all judged**; the four claimed discrimination pins **4/4 green**; the D-9 and D-10 pins judged discriminating with their counter-mutations already executed; the matrix self-verify pins judged discriminating with a **zero-headroom** leg-count threshold.

### D-9 — the token ceiling (LEG-3, the control leg)
The meter reads all three shapes (`total_tokens`; `prompt+completion`; `input+output`). The round-2 bypass payload **trips at attempt 20** (10,000 tokens ≥ the 10,000 bound) with **USD $0.000000** throughout; trip effects verified (both per-seam keys + the global master written, notify fired, attempts 21–40 suppressed pre-socket). **Counter-mutation, both directions**: meter-reads-0 ⇒ the bypass returns on the token dimension; token-dimension-dropped ⇒ round-2's exact observable returns; restored ⇒ trips at 20 again. The shipped pin **REDs under both**. **USD path not broken**: with a rate table configured it trips at both bounds (per-seam 0.005, global 0.010). One minor: a token trip self-describes with USD-only figures — no token numbers in the trip record, notify payload or board body (§4 C-3).

### D-10 — the store-level floor discriminator (LEG-4) — **CLOSED, with one live vacuous duplicate**
The round-5 pin `test_council_round5.py:410-445` (`test_a_connect_phase_fault_bills_nothing_at_the_ledger`) reads the **durable store** — the production `app_settings` row `decision_cost_ledger_2099-07` via `accumulator.current_totals` — on the **production overlay** path, and the sole writer (`overlay._account_cost` → `accumulator.record`) is on that path. Positive legs, measured as ledger-row deltas: connect fault ⇒ **0.0 USD / 0 tokens**; reached-socket-no-usage ⇒ **0.5** (the D3 floor); 200-with-usage ⇒ **0.0123 / 500 tokens**. **Counter-mutation 1** reproduces the round-2 defect exactly (a floor-billing mutant + the round-2 leg shape ⇒ the vacuous leg **GREEN** while the store-level probe goes **RED** at 0.5) — and the control isolates the cause: **the defect class is the PATH, not "metric vs store"** (a metric reader *on* the writer's path still has teeth). **Counter-mutation 2** (floor removed) ⇒ the current pin REDs `got 0.0`, as do M12's cited pin and the round-4 billed-cost pin.
**But the legacy round-3 `(e)` leg is still vacuous at HEAD** — see §4 B-15.

---

## 3. PER-LEG RESULTS

| Leg | Scope | Verdict | Anchor |
|---|---|---|---|
| **1** | G7/D-1 content-free, both surfaces | **PASS** | 0 sentinel hits across 4 surfaces; 2 mutants ⇒ 8 hits; package sweep 0 vs 11 at round 2 |
| **2** | D-2/D-3 guard pins | **PASS on the decisive leg** | full-leak ⇒ BOTH pins RED, restore GREEN; 3 residual holes + 3 MAJOR non-blocking (§4) |
| **3** | D-9 token ceiling (**the control**) | **PASS** | trips at 20 with USD $0.00; accrual removal reproduces the bypass; pin REDs under both reverts |
| **4** | D-10 store-level floor discriminator | **PASS** | the pin reads the **durable `app_settings` ledger row** on the production overlay path; connect fault ⇒ **0.0**, reached-socket-no-usage ⇒ **0.5** (floor), 200-with-usage ⇒ **0.0123**; the round-2 defect reproduced exactly (vacuous leg GREEN + store probe RED) and isolated to **the path, not metric-vs-store**; one **live vacuous duplicate** remains in the legacy round-3 leg (§4 B-15) |
| **5** | D-4/D-5 mutation matrix | **FAIL (artefact)** | digests **24/24 re-derive** (D-4 closed) but **6/24 legs do not kill** and the copy-paste recipe reproduces **15/24** (§5) |
| **6** | D-6 §15 record | **INACCURATE** | census **11/11 MATCH**, heading binding **sound** (ancestor, distance 1, no input moved), but **3 MAJOR narrative defects** (§6) |
| **7** | D-7/D-8 the pack | **PASS** | live key **absent** from the child; fence pair falsified; vacuous guard works; **221/221** deterministic (§7) |
| **8** | scoped dual-side regression | **PASS** | 0 lane-caused reds; my scope premise **falsified** and the hole closed by a 2,441-node reachability sweep (§8) |

---

## 4. THE DISCLOSURE LIST — what the giter's rider and the final report should carry

### (A) MUST CLOSE BEFORE THE SEAM IS EVER ARMED — live-safety-relevant
| # | Sev | Location | Finding |
|---|---|---|---|
| **A-1** | 🟠 MAJOR | `tests/unit/decision/helpers.py:455-460` | The leak walker's `raised` carve-out is **file-wide and scope-blind**: an unrelated `raise exc` anywhere in a file **silences the leak check for every other function in that file** (proven: a file with both `raise exc` and `logger.warning('%s', exc)` ⇒ `offenders: []`). A guard one innocent edit can silently disarm. |
| **A-2** | 🟠 MAJOR | `tests/unit/decision/test_council_round5.py:76-78`, `test_council_round4.py:110-114` | The walker's scope is **package-only**; `daemon/manager.py` — a **lane-owned integration point** — is not covered, and it **already contains the full-leak shape** at `daemon/manager.py:3032-3037` (pre-existing, not a decision site). The scope limit is stated only in a module docstring, not in either pin nor in `decisions.md:176`. |
| **A-3** | 🟠 MAJOR | `daemon/services/decision/config.py:167` | **`DecisionConfig`'s dataclass repr embeds the plaintext credential** (`api_key: str = ""` with `repr=True`) — with the reuse fallback this is the **live `OPENAI_API_KEY`**. No active log/serialisation site found, but the config object is not hardened the way the lane hardened its own D-1 site. Fix: `field(repr=False)`. |
| **A-4** | 🟠 MAJOR | `daemon/services/decision/overlay.py:875` (floor predicate) | The **USD** ceiling still binds only when an operator rate table is configured; under the **default empty** table a priced response accrues **$0.00** and the **token** ceiling is the only binder. The token path is now proven to bind (D-9), but the USD arm remains conditional — the operator must configure the rate table, and that must be an explicit arming precondition, not a default. |
| **A-5** | 🟠 important | `tests/unit/decision/test_council_round5.py:161-195` | The behavioural pin covers **2 of ≥5** surfaces (log, notify payload) — not the returned `TripRecord.error`, not module globals, not the board body (the fixture never invokes it). Its literal claim is accurate; its coverage is partial and must be stated as such in any record. |
| **A-6** | 🟡 minor | `tests/unit/decision/helpers.py:407-427` | The walker misses **`AugAssign`** (`detail += exc` ⇒ `offenders: []`). |

### (B) MUST CORRECT BEFORE THOSE ARTEFACTS ARE CITED AGAIN — record accuracy
| # | Sev | Location | Finding |
|---|---|---|---|
| **B-1** | 🟠 MAJOR | `daemon/services/decision/MUTATION_MATRIX.md` — 6 legs | **6 of 24 legs do not kill their pin at all** (M2, M5, M6, M9, R6, R7): the published "mutated operand" is **comment-only ⇒ inert**, defeated by a **redundant guard** (the code clamps NaN twice; `shadow.py:97` still returns `""`), or **mis-targeted** (M9 plants in `_trip` while its pin exercises the detached path). |
| **B-2** | 🟠 MAJOR | `MUTATION_MATRIX.md:54-61` | The artefact's own **copy-paste shell recipe is wrong** (`printf '%s\n%s\n%s'` drops a newline): it reproduces **15/24** and **fails for its own worked example (R8)**. Verified fix: `printf '%s\n' <lines> \| head -c -1 \| sha256sum`. |
| **B-3** | 🟠 MAJOR | `decisions.md:359` | "the failures enumerated are the **pre-existing set only**" is **false for 5 of 6**: under the hermetic fence `tests/unit/metrics/` is **539P/1F/0E** — the 3 auth-matrix + 2 scope-cage reds are **ambient-`ENSEMBLE_AUTH_*` artefacts**. The correct enumeration **existed at `72a233df3`** and was **deleted** by the re-measure commit. **A regression on the very defect class D-6 was raised for.** |
| **B-4** | 🟠 MAJOR | `decisions.md:382` | "**34** code lane's, **1** doc lane's" is false: **16 code / 19 docs** over the stated denominator. |
| **B-5** | 🟠 MAJOR | `decisions.md:176` (§5 item 45) | Cites `daemon/services/decision/helpers.py:430` — **that file never existed on any ref** (`git log --all` ⇒ 0 commits). The real `exception_leak_offenders` is a **test** helper at `tests/unit/decision/helpers.py:430`; the record implies a shipped production guard that does not exist. |
| **B-6** | 🟠 MAJOR | `decisions.md:176` / matrix gloss | The published claim "a pure walker with **taint propagation**" names a mechanism **no pin observes**: deleting the propagation loop wholesale leaves **all four** D-2 pins GREEN (V1), and it is **redundant** with the assignment rule. Either pin it or drop the claim. |
| **B-7** | 🟡 MODERATE | `MUTATION_MATRIX.md` — R3, M14, R5 | R3's published operand is **not valid Python in context** (the pin REDs on a **SyntaxError**); M14 and R5 RED for reasons other than the claimed observation (a `TypeError`; a UNIQUE-constraint `IntegrityError`). |
| **B-8** | 🟡 MODERATE | `MUTATION_MATRIX.md` — R1/R2/R8/R9 | **Execution-mode mismatch**: these four pins plant in **memory** and require the shipped anchor; file-planting them REDs on the anchor precondition, and their published operands are not the text the pins plant. |
| **B-9** | 🟡 MODERATE | `decisions.md:345` | The section's own reproduction commands use `..HEAD` rather than the bound SHA, so they no longer reproduce the stated figures (36 vs 35 commits; 16202 vs 16187 insertions). |
| **B-10** | 🟡 MODERATE | `decisions.md:383` | "The one-file difference is **not explained by anything I can see**" is **false**: the working-tree form yields **50** and the sole extra path is the **uncommitted foreign telemetry artefact** (`install-audit.jsonl`, +62 lines, 0 commits in range). The same artefact inflates the census raw from 1451 to 1452 — **one artefact, two figures**. The 50→49 correction is also **not enumerated** in the §5 self-correction arc (which stops at "seventh and eighth"). |
| **B-11** | 🟡 MINOR | `MUTATION_MATRIX.md:345-361,423-439,481-498` / `:77-78` / `:553-565` | 3 **ambiguous anchors** (multi-occurrence operands, site unspecified); the leg gloss under-counts the round-5 claims (four, not two); §WHAT IS NOT CLAIMED omits the inert-leg set, the broken recipe, the mode split and the ambiguous anchors. |
| **B-12** | 🟡 MINOR | `decisions.md:166`, `:351`, `:368`, `:353-362` | `PRE_EXISTING_DAEMON_OFFENDERS` cited at `:159` (actual `:182`); the **−12** perf pre-exclusion is unstated in the run-set chain; the raw-path figure is the working-tree form while the section is bound to a commit SHA; the D1/D2 block was deleted, dropping the 42 and 36 populations from the record. |
| **B-13** | 🟡 MINOR | `tests/unit/decision/helpers.py:513-577` | Three **duplicate** D-2 test bodies live in a non-test module; pytest does not collect them — dead code that reads as a second implementation. |
| **B-14** | 🟡 MINOR | `tests/unit/decision/test_council_round5.py:221` | `client = mutant.__dict__ and DecisionClient(…)` — the `and` is a no-op; the mutant's own client is never used, so a client-side mutation would be silently ignored. |
| **B-15** | 🟠 **MEDIUM (live)** | `tests/unit/decision/test_council_round3.py:599-605` (read `:599`, delta `:602`, assert `:603-605`, scenario `:600`) | The **legacy** round-3 `(e)` leg's "floor discriminator" is **still vacuous at HEAD**: it asserts a `metrics.cost_total` delta after a **bare `client.consult`**, and that metric's only writer (`overlay.py:904`) is never on the path (`client.py` writes 0 metrics). Round 5 **added** a store-level pin without retiring this one — two "floor discriminators" now exist, one dead. **Proved live**: planting the application-side floor bug (`overlay.py:875` drops `reached_socket`) leaves this leg **GREEN** while the round-5 store pin REDs; the whole decision suite was **220/221 green** with that bug live. The class is **not** uncovered — the round-5 store pin is the **only** guard — but the legacy leg is a misleading duplicate. **Recommendation:** retire it or re-point it at the store, and add the round-5 pin as M2's pin node in the matrix (B-16). |
| **B-16** | 🟡 INFO | `MUTATION_MATRIX.md:119-120` (M2) vs `:536-537` (R9) | M2's cited pin is the **round-3** leg, with observed RED `assert True is False` — produced by that leg's **`reached_socket`** assertion, **not** by its floor discriminator. The matrix's headline D-10 pin (`test_a_connect_phase_fault_bills_nothing_at_the_ledger`) appears **nowhere** in the matrix (`grep bills_nothing_at_the_ledger` ⇒ 0 hits) even though it is the **class guard** for M2 and the **only** guard for the application-side variant. |
| **B-17** | 🟡 LOW | `tests/unit/decision/test_council_round5.py:442-445` | The D-10 store assertion covers the **USD leg only**; the ledger's **token** leg (`month_to_date_tokens`) is not asserted. Currently unreachable (the failure path passes `total_tokens=None` ⇒ 0), but it is the missing negative half of a two-legged quantity. |

### (C) HOUSE / TOOLING HYGIENE — not lane-attributable
- **C-1 🟠 MEDIUM** `test/packs/decision_module_p1_unit_test.sh:144-158` — the vacuous guard runs **before** the exit-code mapping, so a real inner timeout (280 s) reports `FAIL (VACUOUS …)` **exit 1**; the documented `RESULT: TIMEOUT` / **exit 124** branch is **unreachable dead code**.
- **C-2 🟠 MEDIUM** same file `:70-89`, `:93` — the scrub omits **7 `DECISION_*` names**, and the fence probes **one** name: dropping `-u POSTGRES_DB` still yields `RESULT: PASS`. A PG-scrub regression would be **silent**. The suite itself has **zero** catch-power for credentials/PG/`ENSEMBLE_SELF_ENV` (0 nodes flip) — the fence is the only guard.
- **C-3 🟡 MINOR** `overlay.py:945-953` + payload/board body — a **token**-ceiling trip self-describes with USD-only figures; an operator cannot see what bound.
- **C-4 🟡 MINOR** pack `:70-89` — `ENSEMBLE_SELF_ENV` is neither scrubbed nor forced to `dev`; ambient `live` reaches the child. Pack header's "CREDENTIAL class" phrasing is imprecise (the oauth client secret and `OPENAI_BASE_URL_BACKUP` also ride along). PACKS.md's **block header** still cites the dead sha `6c2985a1c` and repeats the over-broad claim; the row's date is a day behind the commit it names.
- **C-5 🟡 house** `tests/packs/g7_unique_index_smoke_test.py:121` — a whole-tree pytest collection **aborts at 4,859 nodes** (`sys.exit(0)` ⇒ INTERNALERROR). **A one-shot whole-tree re-sweep is not collectable** at this tree — pre-existing, lane-untouched, and the mechanical reason a scoped leg was the only workable option.
- **C-6 🟡 house** `daemon/migrations/runner.py:723` — PostgreSQL-only DDL run against SQLite; reds **75 of 78** nodes of any fenced sweep, identically at base.
- **C-7 🟡 house** `tests/unit/metrics/` — **5 latent derived-set tautologies** (`test_metrics_family_w1.py:200-202,:281`; `test_metrics_family_w4.py:181,:183-185,:186-188`), each counter-mutation-PROVEN incapable of failing. The decision lane's own metrics pins use test literals and **do** fail.
- **C-8 🟡 pre-existing, out of lane** `daemon/services/snapshot_search_service.py:368-369,418-419,430-431,549-550,561-562` (blame `35de22b73`) and `daemon/manager.py:16442,16449,16475,16476` (blame `a770a6dc9`) — exception content in logs / the caller-visible error envelope; both pre-date the lane base and are outside every lane hunk.

---

## 5. THE CORRECTED MUTATION MATRIX (24 legs) — quote THIS, not the artefact

Re-executed in an isolated worktree; clean-check GREEN **24/24** · restores sha-matched **24/24** · GREEN after restore **24/24**.

| outcome | n | legs |
|---|---|---|
| **killed, claimed observation reproduced in kind** | **11** | M1, M3, M4, M7, M8, M10, M11, M12, M13, M15, R4 |
| **killed only via the pin's anchor precondition** (the pin's own in-memory mode is the claimed one) | 4 | R1, R2, R8, R9 |
| **killed for a different reason than claimed** | 3 | M14 (TypeError), R3 (SyntaxError), R5 (IntegrityError) |
| **DO NOT KILL** | **6** | **M2, M5, M6, M9, R6, R7** |

**Digests: 24/24 re-derive** under the artefact's stated scheme (`sha256(path + "\n" + shipped + "\n" + mutated)[:16]`). The self-verifying pin `test_council_round5.py::test_the_mutation_matrix_digests_are_re_derivable` is **genuinely discriminating** (one changed character ⇒ RED naming the leg; one changed digest digit ⇒ RED naming the leg; restore ⇒ GREEN). The old revision is independently confirmed **0/17** re-derivable — the rewrite fixed the core; it is the *human-facing recipe* and *6 operands* that remain wrong.

---

## 6. §15 — ACCURATE IN ITS CENSUS, INACCURATE IN THREE NARRATIVE FIGURES

**The new heading-binding practice works and is verified**: the heading carries `cc6144968`; `git merge-base --is-ancestor cc6144968 c7fdee903` is TRUE; the distance is **1 commit**; and **no figure input moved** after the measurement SHA (that one commit touched only `decisions.md` and `anchor-audit.md`). **The census is 11/11 MATCH**; the label-space block MATCHes (411/19, 818/71, 89+818=907 ≤ 2000); the **false live finding is gone** with its history preserved and independently verified (`e12c25b16` → `72a233df3` closure chain); the **producer-file correction is restored** (13 `.py` / 14 package files; "10" survives only as the explicitly-rejected figure).

**The 49-vs-50 dispute is resolved in the doc lane's favour**: `git diff --name-only 818411107..HEAD` / `--shortstat` / `git log --name-only | sort -u` all give **49**; the **working-tree** form gives **50**, and the sole extra path is the **uncommitted foreign telemetry artefact** — which also inflates the census raw from 1451 to 1452. **One artefact, two figures.** The implementer's "not explained" is false (B-10).

**Suites at HEAD, re-measured**: decision **221/221** · pack **`RESULT: PASS — 221 passed`** (fence `[OK]/[OK]`) · adjudication **29/29** · import-graph **57/57** · four round-5 discrimination pins **4/4** · `tests/unit/metrics/` **534P/5F/1E** in the ambient posture but **539P/1F/0E** hermetically — **only F21Journal is a true pre-existing red** (B-3).

---

## 7. THE PACK — PASS with 4 disclosures

The scrub is **implemented and effective**: an in-child `sitecustomize.py` capture proves `OPENAI_API_KEY`, `POSTGRES_DB/HOST/USER/PASSWORD` and `DECISION_API_KEY` are **ABSENT** from the pytest child **and** from the test-spawned child; the live key appears in **no** child (capture files chmod 600, **0** secret-shaped strings on disk). The **fence self-test is a genuinely discriminating pair**: removing `-u OPENAI_API_KEY` ⇒ `RESULT: FAIL (the env fence is not proven)`, exit 1, **0.136 s, before pytest starts**; restore ⇒ 221 passed. The **vacuous guard works**: 0-collected and all-deselected both ⇒ `FAIL (VACUOUS …)` exit 1 — **PASS can no longer print from an empty run**. Determinism: two runs byte-identical after stripping timings (26,141 bytes), **221/221** each; census 221. Registration: `Last run: 1eabf72eb` — that sha **exists**, its pack blob **== HEAD blob**, and the row's `221/221` was re-run and confirmed. Disclosures C-1…C-4.

---

## 8. THE SCOPED DUAL-SIDE REGRESSION — scope, result, and the falsified premise

**Scope chosen**: the changed-code consumers (`tests/unit/decision/`) + the census pin (its literals include the touched `MUTATION_MATRIX.md`) + the pack + the import-graph pin. **Excluded**: a whole-tree re-sweep — justified by the round-2 net-new 0/94 at the parent SHA, the no-shared-surface delta, the reverse-dependency proof, and (mechanically) **C-5**: a one-shot whole-tree collection is not collectable at this tree.

**My scope premise was FALSIFIED by the worker, not accepted.** I asserted "the changed modules are internal, reachable only through the decision suite". True at module level, **false at package level**: `daemon/manager.py:2887` imports the factory ⇒ `client`/`overlay` are **constructed on every real Manager boot**, and the round-2 whole-tree sweep **predates** this delta — so it did not cover the changed files.

**The hole was closed with a 2,441-node dual-side reachability sweep** (every test file importing the Manager — 97 files — HEAD vs BASE, identical command shape, exact slicing arithmetic on both sides):

| class | n |
|---|---|
| red at **both** revs → pre-existing / environment (dominated by C-6) | **75** |
| red at **HEAD** only → **3**, all classified flake/order-dependent by 3× solo | **3** |
| red at **BASE** only → **1** — the lane **improved** it (3/3 PASS at HEAD, 3/3 FAIL at BASE) | **1** |
| **LANE-CAUSED** | **0** |

Plus two mechanical proofs: the changed modules **construct successfully** on the production path with the real house config (consult suppressed pre-socket), and the **arming census** (**0** files outside `tests/unit/decision/` can arm the seam).

**Scoped legs**: D1 **221/221** (HEAD-only; absent-at-base by construction) · D2 **29/29 HEAD** vs **26/27 BASE** — the single base red is the defect class the lane closed (the base pin asserts every changed daemon path is allowlisted, with no escape hatch for inherited offenders) · pack **PASS (221)** · import-graph **57/57** · the four round-5 discrimination pins **4/4**.

---

## 9. THE TWO OPEN ITEMS

**(A) The strong-ref set — CHEAP INSURANCE AGAINST A DOCUMENTED HAZARD; neither a proven necessity nor a defect.** The natural trigger does **not** reproduce: on CPython 3.14 a suspended task is held by **three incidental strong edges** the production set merely replaces (the awaited future's done-callback; `asyncio.timeouts.Timeout` holding `_task` plus a live timer; CPython 3.14's `fut._asyncio_awaited_by` frozenset) — 200 bare tasks with refs dropped and `gc.collect()` every tick ⇒ **200/200 rows landed**, stock **and** uvloop. But the **mechanism is proven fatal** once those edges are severed (exact recipe published): the task is collected mid-flight — *"Task was destroyed but it is pending!"* — and the consult never completes (**rows 0, ledger 0**). The production path is sound under identical adversarial conditions: **600/600 rows** across 3 rounds, stock and uvloop. The caveat stands: the recipe removes the incidental edges, so it proves the weak-only-retention mechanism, not a natural production occurrence — the bare-`create_task` mutation still does **not** discriminate on this runtime.

**(B) The ConnectError +0.0005 — ROOT-CAUSED.** Three mechanisms, each execution-proven, all producing exactly +0.0005: (i) **my own shared-worktree mutant live during the measurement** (the G5 leg planted exactly this mutation; the surviving artefacts overlap the documented mutation window); (ii) the **pre-`(e)`-fix client** (before the fix, a ConnectError fell into the broad `except httpx.HTTPError` ⇒ `reached_socket=True` ⇒ floor); (iii) **measurement-window contamination** (an overlapping consult's `record()` landing inside the before/after window; `current_totals()` takes no lock). **Decisive exclusion at HEAD**: 50/50 isolated zeros; no exception class reaches the floor with `reached_socket=False`; only `ConnectError` and `ConnectTimeout` bill 0; a **real mid-flight disconnect** bills the floor (correct and conservative).

---

## 10. WHAT I COULD NOT FULLY VERIFY

1. **The round-2 whole-tree sweep was not re-run** — P1 is verified *as recorded*, not re-measured (and C-5 makes a one-shot re-collect impossible). The delta-gap closure (§8) is my contribution, not a re-verification of P1.
2. **The manager's real boot path** — `initialize()`'s `reverify` and `set_settings_service`'s callback were verified by reading and by exercising the identical call shapes, not by booting a daemon (prohibited).
3. **Live proxy / live PostgreSQL / live daemon** — zero live contact by instruction; the transport doubles were audited against the recorded live contract and found faithful on every recorded row, with two standing divergences (out-of-order `answers[]` never mocked; live `usage` key names unpinned).
4. **The board item's rendered form in production** — the `create_item` **kwargs** are proven content-free; the real DB-backed render path is unreachable by construction.
5. **The `daemon` logger's production handler set** — the record **content** is proven, not the exact console/file rendering (the house handler pair was not installed).
6. **The 21/23 `service_tool`-masked nodes in the repo's own CI** — pre-existing mask keeps them red, so the B-2 win is not observable through the repo's suite without a fixture change.
7. **The 469/122 → 467/119 baseline per-id list** — never persisted (sha only), so no per-id diff is possible.
8. **The `tests/integration/**` and `tests/postgres/**` tiers** — deselected by the repo's own `addopts` on both legs.

---

## 11. THE SINGLE MOST IMPORTANT THING THE RELEASE STAGE SHOULD KNOW

**The lane's behaviour is now certified; its paperwork is not.** Every invariant is proven by execution and both round-2 blockers are closed with counter-mutation evidence — but the lane's own two provenance artefacts still **overstate**, and a reader who trusts them will draw a wrong conclusion about how much is actually pinned: the **matrix** claims 24 kill legs where **6 do not kill** (and its copy-paste recipe reproduces only 15/24), and **§15** asserts a metrics attribution that is **false for 5 of 6** — the correct version existed and was deleted by the very commit that re-measured it. **Quote §4 and §5 of this report, not the lane's artefacts.**

Second, and specific to any future arming: the lane's leak **detector** is weaker than its claims — the walker can be silenced for a whole file by an unrelated `raise exc` (A-1), does not cover `daemon/manager.py` at all (A-2, where the full-leak shape already exists), and the behavioural half checks 2 of ≥5 surfaces (A-5). Those three must close **before** the seam is ever armed, together with the credential-bearing config repr (A-3) and the operator-rate-table precondition for the USD arm (A-4).

Third, the operating lesson of these three rounds, in one line: **every "the pin proves X" claim in this lane has needed a counter-mutation to be believed** — round 1's census pin was tautological, round 2's TYPE-only pins were green on a full leak, round 3's matrix published six inert mutations. The counter-mutation is the only thing that has ever distinguished a real guard from a green comment.

---

*House artifacts*: this file is untracked in the lane worktree for the giter's evidence-rider. The foreign telemetry `…/designer-agent/install-audit.jsonl` was never reverted, committed or deleted (408 lines, byte-identical across every leg). The two prior reports (`…-functional-gate.md`, `…-regate.md`) are untouched. No commits, merges, pushes or staging were performed by this gate.
