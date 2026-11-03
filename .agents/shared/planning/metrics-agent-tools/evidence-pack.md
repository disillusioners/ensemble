# Evidence pack — metrics-agent-tools

Date: 2026-11-03 (UTC) · Lane: implementation (developer) · Branch: `feature/metrics-agent-tools`
· Worktree: `/home/nea/ensemble-src-wt-metrics-tools` · Base: `03a2297c3`

Every figure below was re-derived at gate time; nothing is remembered. The recorded
deviations live in `fold-ledger.md` (§F.2 + §H).

---

## 1. The commit trail

| # | sha | message |
|---|---|---|
| W1 | `dc3621b32` | `docs(metrics-agent-tools): the approved plan set + approver artifacts (W1)` |
| P1 | `e2f3b0b03` | `feat(metrics-agent-tools): Phase 1 — the seam the tool door consumes` |
| P2 | `23695bef3` | `feat(metrics-agent-tools): Phase 2 — the metrics-read category + its three tools` |
| P3 | `1afb212e3` | `feat(metrics-agent-tools): Phase 3 — the metrics:read scope split + the E-1 amendment` |
| P4 | `87f680f53` | `feat(metrics-agent-tools): Phase 4 — the FE registry_read_ok mirror (fold a)` |
| P5 | `22e27dac6` · `cdff0affc` · `d36a9a326` · `7736e2cfc` | the persona pack + the mutation matrix + its three leg fixes + the docs chain / help surface / cold-boot scan |
| P6 | (this commit) | the close-out: the watch outline, the evidence pack, the stage-prep checklist |

**The Phase-2 ONE-commit cluster is `23695bef3`** — the tool module + the four registry edits +
the `instance.py` extend + the three metas + the three `tools_note.md` + the 16-site pin fan-out
+ the registration pack + the tools pack all landed in it (22 files, +2 716/-17).

---

## 2. The gate legs (per phase)

| Phase | Leg | Composition | HEAD | BASE | Verdict |
|---|---|---|---|---|---|
| 1 | **Tier-2 scoped dual-side** (D1 = the seam pack; D2 = the derived metrics blast radius + the core-seam-adjacent packs) | 30 file arguments | 1 032 collected / 1 029 passed / 3 failed | 1 004 / 1 001 / 3 | **net-new = 0** — the three FAILED node-ids are identical on both legs and the extracted error-line digests are byte-identical (`md5 a942dc2d…`) |
| 1 | the contract-doc pack | `tests/unit/metrics/test_central_metrics_contract_doc.py` | 21 collected / green | — | green (the seam amendment does not touch the contract) |
| 2 | **Tier-1 scoped dual-side** (D1 = the registration pack + the tools pack; D2 = the 14 derived files + the whole `tests/unit/metrics/` corpus) | 30 file arguments | 813 collected / 800 passed / 4 failed | 752 / 739 / 4 | **net-new = 0** — the FAILED list is identical on both legs |
| 3 | **Tier-1 scoped dual-side** (D1 = the extended auth-matrix rows + the cage pack; D2 = the scope-cage dual side + the routes pack + the switch-invariance pack + the API-wiring pack + the contract-doc pack + the whole corpus) | 21 file arguments | 493 collected / 492 passed / 1 failed | 421 / 420 / 1 | **net-new = 0** |
| 4 | **FE dual-side pack** (`test/packs/metrics_fe_dual_side_test.sh`) | D1 = the lane's FE specs · D2 declared · D2 derived (38 files) | D1 **149 passed** · D2 declared **138 passed** · D2 derived **1801 collected / 1799 passed / 2 failed** | D1 **141 passed** · D2 declared **138 passed** · D2 derived **1801 / 1799 / 2** | **net-new = 0** — the D1 delta (+8) is exactly the eight new legs; the two derived failures are the DST-dependent timezone assertions, base-proven |
| 4 | the scoped type-check | `frontend/./node_modules/.bin/tsc --noEmit -p tsconfig.metrics-fe.json` | rc=0 | — | green (the frontend-local toolchain ONLY) |
| 4 | both builds | `ng build --configuration development` + `--configuration production` | rc=0 / rc=0 | — | green; the production budget WARNs are RECORDED FINDINGS (none names a file this lane touched) |
| 4 | the FE e2e | `playwright test --config=playwright.metrics-gate.config.ts` | **3/3 passed** (real Chromium, zero daemon contact) | — | green |
| 5 | **the mutation matrix** | **21 RED/GREEN + 1 FOLLOW = 22 legs** (12 at the first cut; +10 by the 2026-11-03 council fix cycle) | **22/22 PASS** (RED via the intended assertion → byte-identical restore → GREEN; the FOLLOW leg's consumer value MOVED 235 → 215) — **ARCHIVED**: `.agents/shared/planning/metrics-agent-tools/evidence/mutation-matrix-2026-11-03.log` (sha256 `8fc8da2ade803d08366040397916940549a5c6240c68b57139ca330f87332def`) + `.../mutation-matrix-pre-run-manifest-2026-11-03.json` (sha256 `44629b770ab1767c125b038134eb080d4e484f5c93a52d1cbfb49aca088f45ac`) | — | green; the post-run manifest confirms every touched file is byte-identical |
| 5 | the unit shell pack | `test/packs/metrics_read_unit_test.sh` | **PASS (5 legs)** — 30 + 47 + 15 + 20 + 28 = 140 nodes | — | green |
| 5 | the mutation shell pack | `test/packs/metrics_read_mutation_legs.sh` | **PASS** (the matrix's own `RESULT: PASS` + the byte-identical-restore assertion) | — | green |
| 5b/5c | **the two fix cycles' scoped packs** (the six-pack set: tools + personas + registration + docs-surface + the seam pack + the contract-doc pack) | 6 file arguments | **161 passed** (the tools pack **47** — the AST wiring pin + the two SHAPE pins) | — | green; the shell pack's five legs re-run: 30 + 47 + 15 + 20 + 28 = **140 nodes**, `RESULT: PASS (5 legs)` |
| 5c | the C1 regression's mutation proof (manual, recorded) | the history body handed the RAW rows again (`items=kept`) | the shape pin **RED** (and only that pin) | — | the pin is mutation-meaningful; the source was restored byte-identically |

**The whole-tree collection census** is blocked on BOTH legs by the KNOWN base debt
(`tests/packs/g7_unique_index_smoke_test.py:121` — a module-level `sys.exit(0)`): **4 855
collected + 1 collection error, identical on both legs.** The whole-tree RUN leg was therefore
SCOPED to the derived blast radius + the core-seam-adjacent packs (the plan's task 1.7 wording:
"run the scoped dual-side FAILED-list diff vs base"). DISCLOSED, not hidden.

---

## 3. The mutation matrix (the table)

Every leg plants a mutation INDEPENDENTLY on the production source, runs the intended pack,
requires RED **through the intended assertion**, restores the file BYTE-IDENTICALLY and
requires GREEN again. The restore discipline is the in-memory byte copy + the pre-run manifest
+ the recovery journal; **no `git checkout --`**, ever.

| Leg | Mutation | Intended RED |
|---|---|---|
| MT-01 | the count pin re-broken (17 → 16) | the count assertion in the board registration pack |
| MT-02 | the availability block re-derived | the verbatim-carriage leg |
| MT-03 | the sort-before-cut removed | the ascending-order leg |
| MT-04 | the 3× stale rule dropped | the derived-members leg |
| MT-05 | the budget clamp removed | the non-silent-clamp leg |
| MT-06 | the combined selector cap reverted to names-only | the combined-cap leg |
| MT-07 | the body gate demoted below the substrate check | the gate-ordering pin |
| MT-08 | the decorator order flipped | the decorator-order pin |
| MT-09 | the insufficient-window delta emits `0` | the null-not-zero delta leg |
| MT-10 | a cursor member added to the input schema | the no-cursor leg |
| MT-11 | the floor RE-CHECK removed | the unknown-family leg |
| MT-12 | a pinned contract-doc substring perturbed | the contract-doc closed-set pin (the C-1 non-vacuity leg) |
| **MT-13** | **the byte-ceiling trim removed** | the byte-ceiling behavioural pin (**the RESTORED binding leg**) |
| MT-14 | `truncated` hardwired false | the snapshot drop leg |
| MT-15 | the `narrow_hint` emptied | the unknown-family leg |
| MT-16 | the caveat given a defect-attribution token | the genericity leg |
| MT-17 | the `families` list filter dropped | the multi-family leg |
| MT-18 | the ring-state signal removed | the ring-state behavioural leg (W3) |
| MT-19 | the per-series freshness field dropped | the per-series freshness leg |
| MT-20 | the racy-bool doctrine statement removed | the doctrine leg |
| MT-21 | the AmM-1 clamp applied SILENTLY | the non-silence leg |
| **MT-22** | **the seam's `MAX_TOTAL_POINTS` mutated → the tool's clamp must MOVE** | **the FOLLOW leg (a new shape — the consumer's value follows the producer: 235 → 215)** (**the RESTORED binding leg**) |

---

## 4. The E-1 amendment — the executed variant

**The amendment is the PRIMARY variant** (P-1 / E-1, adjudicated 2026-11-03 + amendment-2).

* **The changed lines: NINE**, all inside the FIVE canonical ranges — `:31`, `:32` (§1.13 matrix
  rows 2-3) · `:318`, `:319`, `:322` (§6 truth table) · `:380` (§7 door disclosure) · `:387`
  (§7 the emergency-off lever) · `:405` (the AM-20 over-grant disclosure).
* **The line count is UNCHANGED (819 → 819)** — no line was added, removed or shifted, so the
  "line-scoped" assertion holds trivially.
* **The contract-doc pack is GREEN WITH THE AMENDMENT APPLIED** — 21/21, expectations UNTOUCHED,
  **never re-pinned**.
* **All SIX machine-pinned fenced blocks are byte-identical** before/after
  (`activation-surfaces`, `query-routes`, `error-vocabulary`, `truth-table-invariants`,
  `availability-reasons`, `gate-pack-ids`).
* **All NINETEEN prose literals the pack asserts are byte-identical** before/after — including
  the load-bearing `checkpoint-cleanup-EXECUTE` (inside the amended `:404-405` range) and
  `before any registry read` (inside the amended `:379-380` range). The list was re-extracted
  from the test module and diffed before/after.
* The untouched line below the AM-20 disclosure now reads as the v1 historical note, which
  keeps it TRUE.

**The route censuses did NOT move:** declared-route **111**, mount **37**, class-manifest **33**.

---

## 5. The pin-fan-out re-derivation (AmC-9 — a RECORDED artefact)

The plan's prose says "10 files / 15 EDIT sites + 1 VERIFY-ONLY". The **grep-derived** census at
implementation time is **16 EDIT sites + 1 VERIFY-ONLY = 17 sites across 10 files**
(the 16th EDIT site — the maintenancer meta's exact allow list — lives in
`test_maintenancer_spawn_resolves_tools.py`, which is ALREADY one of the ten
pin files; and the VERIFY-ONLY site — the ari trailing-entry pin — lives in
`test_board_registration.py`, also one of the ten. **The site count and the
one-commit discipline HELD; only the FILE count was misrecorded.** Fixed by
the council fix cycle, 2026-11-03):

* the five `EXPECTED_PRIVILEGED_SET` constants;
* the five inline `frozenset` literals;
* the five count pins (16 → 17);
* **one site the prose did not enumerate** — the maintenancer meta's EXACT allow list
  (`tests/integration/test_maintenancer_spawn_resolves_tools.py`) — a consequence of the
  category being granted to the maintenancer;
* one VERIFY-ONLY site (the ari trailing-entry allow-array pin — `metrics-read` is not granted
  to ari, so it is re-READ, never edited).

The scope-split pin family shifted the same way: **three** sites (the manifest row, the cage's
`FAMILY_SCOPE`, and the query door's own route-declaration pin — the third found by the gate
leg, not by a guess).

---

## 6. The auth-matrix extension (task 3.5)

| Row | Assertion | Result |
|---|---|---|
| (i) | a bearer/session holding ONLY `metrics:read` gets **200** on all three query routes | green |
| (ii) | the SAME caller gets **403** on the Prometheus text-exposition route and **403** on the destructive checkpoint cleanup | green (the Origin guard passes a request with NO Origin header, so the refusal is the SCOPE's) |
| (iii) | an existing `maintenance:read` holder is unaffected on every route | green |
| (iv) | the fail-closed seam ordering is untouched (auth-OFF ⇒ 503 BEFORE any provider read) | green |

---

## 7. The FE gate legs (Phase 4)

| Leg | Result |
|---|---|
| D1 jest (the lane's FE specs) | 149 passed (BASE 141 — the +8 delta is exactly the eight new legs) |
| D2 declared jest | 138 passed (BASE 138) |
| D2 derived jest (38 files, derived at run time) | 1 801 collected / 1 799 passed / 2 failed (BASE identical — the two DST-dependent timezone assertions) |
| the scoped type-check | rc=0 |
| `ng build --configuration development` | rc=0 |
| `ng build --configuration production` | rc=0 (the budget WARNs are recorded findings) |
| the e2e (real Chromium) | 3/3 passed |
| the `package.json` dependency diff | **0 lines** (zero new npm dependencies; `package-lock.json` byte-identical) |

---

## 8. The prompt-artifact / seal legs (task 5.8, AmC-8)

| Leg | Result |
|---|---|
| the BASELINE-EXISTS precondition | `agents/.prompt-artifacts.sha256` EXISTS — so the check is a real signal, not a silent pass |
| the `SEALED_SHA_BASELINES` no-match over the 6 touched `agents/` files | 35 SEALED rows; **0** of the 6 touched files is one ⇒ **no re-pin needed** |
| the prompt-artifact baseline check (RUN) | **exactly 3 modified files** — the three `tools_note.md` (the `agents/` PAYLOAD delta is 6 files; the BASELINE delta is 3, because `meta.json` is not a versioned prompt artifact) |
| the `central_metrics_contract_doc` pack with the E-1 amendment applied | 21/21 green |

---

## 8b. The 2026-11-03 code-council fix cycle (M-1 — output-budget evidence integrity)

The council REJECTED the lane on ONE axis (the M-1 output-budget evidence) and cleared
everything else. The finding: **`_encoded_size` / `_trim_to_byte_ceiling` were DEAD CODE** — no
tool body ever called them — so the "bounded by clamp + byte ceiling" claim in the fold record
was FALSE, and the measured exposure was **1.26× the seam's own 1 MiB ceiling** at today's
catalog (**6.6 MB** at the legal `MAX_FAMILIES=500` cap). The catalog is runtime-extensible, so
today's fit was an ACCIDENT, not an invariant.

**The remediation (six items, all in this commit's cluster):**

| Item | What changed |
|---|---|
| **C1** | the trim is wired into ALL THREE bodies with a non-silent surface (`truncated` + the count block + a caveat naming the byte ceiling + a `narrow_hint`); the probe measures the tool's OWN output form (`indent=2` — a compact probe measures a fiction); the trim reserves 2 KiB for the caveat + hint; the **names projection is bounded** (200/family, 2000/response) with its own non-silent surface and `name_count` keeping the family's TRUE declared count; the static wiring pin + the byte-ceiling behavioural pins for all three bodies were added. **THE DELTA RE-REVIEW THEN FOUND THE REPAIR INTRODUCED A REGRESSION** (the trim was handed the RAW provider rows while the payload carried the PROJECTION — on the trimmed path every history row lost the nine contract-declared members, a SIZE-DEPENDENT response schema, and the probe measured a different object than the body shipped). The SECOND fix cycle closes it: the ceiling measures the SHIPPED projection, the door's final-envelope re-check is ported, and the series SHAPE is pinned on the trimmed AND the untrimmed path |
| **C2** | the two false records now say so: the fold-ledger's M-1 row reads "**FOLDED — PARTIALLY, AT THE FIRST CUT**" with the (c) clause marked FALSE-then-remediated, and the architect's R13 risk delta carries the correction (the verdict and the rulings are untouched) |
| **W1** | the two binding legs are **restored** (the byte-ceiling leg; the AmA-7/PL-3 single-sourcing leg — the latter in a NEW leg shape, a FOLLOW leg) and eight more dropped legs are folded; the FULL planned set's dispositions (24 planned → 23 covered, 1 deferred as an explicit duplicate) are recorded in fold-ledger §H.8 |
| **W2** | the budget pin imports the seam's `MAX_TOTAL_POINTS` instead of the hardcoded `12000 // 51` |
| **W3** | the `trend_empty_since_restart` signal is BEHAVIOURAL now — the third leg RESTATED (the 2026-11-03 delta re-review's W3 residual) as "an absent fact must NEVER be reported as empty": the age is read with a SENTINEL default (`-1.0`), so a provider that does not carry the fact is never claimed empty (the seam's `0.0` default would have asserted a fact the tool cannot verify). **The UNKNOWN state is INTERNAL — it is NOT observable on the wire**: the field is a BOOL, an absent fact reads as `False` ("not empty"), and the tool never emits null on it. RESIDUAL (recorded, production-unreachable): the real provider exposes `last_sample_age_s` as a property, so the absent-fact path is reachable only with a duck-typed provider that omits it |
| **W4** | the pin census record corrected: **10 files**, not 11 (the 16th EDIT site lives in a file that is ALREADY one of the ten, and so does the VERIFY-ONLY site — the site count and the one-commit discipline HELD; only the file count was misrecorded) |

**The root cause of the dropped legs (recorded honestly):** the first cut's matrix was written
against the pins that EXISTED, and the legs whose pin was missing were dropped instead of the
pin being written — backwards, because the mutation matrix is the instrument that finds a
missing pin. The fix cycle's rule is now explicit: **a leg with no pin is a MISSING PIN, not a
leg to drop.**

---

## 8c. The SECOND fix cycle (2026-11-03, the delta re-review — items 1-5)

The delta re-review REJECTED on ONE item (C1 — a FIX-INTRODUCED regression) and CLEARED
C2/W1/W2/W3/W4. The second cycle's dispositions, with the loci:

| Item | Disposition | Loci |
|---|---|---|
| **1 — C1** | **FIXED** — the ceiling measures the SHIPPED projection (the history body hands it `series_out`, the snapshot body its `series_proj`), the door's final-envelope re-check is ported (`_enforce_byte_ceiling`), and the series SHAPE is pinned on the TRIMMED and the UNTRIMMED path (the nine contract-declared members). The shape pin is mutation-verified: reverting `items=series_out` → `items=kept` REDS exactly that pin. | `daemon/tools/metrics_read.py` (`:428` the helper, `:758`/`:871`/`:1131` the call sites) · `tests/unit/metrics/test_metrics_read_tools.py` (the two shape pins) |
| **2 — W3 residual** | **LEG-RESTATEMENT** — the third leg reads "an absent fact must NEVER be reported as empty" (what is met); the over-claim is corrected (the UNKNOWN state is INTERNAL; the wire value is a BOOL, never null); the residual is recorded (production-unreachable — the real provider carries the fact). | the W3 row above · `tests/unit/metrics/test_metrics_read_tools.py` (`test_an_absent_fact_is_never_reported_as_empty`) |
| **3 — name_count** | **ACCEPTED IN-SCOPE** — the one doc line paid: `name_count` is the DECLARED count, `names` is the bounded projection (200/family, 2000/response). No new member. | `technical-analysis.md` §A.3 Tool 1 · `phase2-plan.md` task 2.2 |
| **4 — the stale records** | **ALL FOLDED** — the pin-file count (10), the matrix count (22/22), the node counts (the tools pack 47, the five-pack total 140), the line-citation drift (the CURRENT sites `:758`/`:871`/`:1131`), the §E table's structure (the fix-cycle paragraph moved to §E.1), the R13 fix-cycle marker, and the matrix log CITED + ARCHIVED. | `stage-prep-checklist.md` · `fold-ledger.md` §H.6/§H.9/§H.10/§E.1 · `plan-overview.md` (R13) |
| **5 — the advisories** | **ALL FOUR FOLDED** — (a) the reservation is guarded by an import-time assertion; (b) the names-bound hint names what works; (c) the static wiring pin walks the AST; (d) the ring-empty signal is DISCLOSED and FLAGGED BACK (the exact signal is not on the consumed Protocol and would need a cleared-surface extension). | `daemon/tools/metrics_read.py` (`:281`/`:308`/`:334`/`:475`) |

**The ARCHIVED matrix log (plan task 5.2's requirement):**
`.agents/shared/planning/metrics-agent-tools/evidence/mutation-matrix-2026-11-03.log`
(sha256 `8fc8da2ade803d08366040397916940549a5c6240c68b57139ca330f87332def` — 3 587 bytes;
the run's own pre-run manifest records the HEAD sha `e5d29e8c6` + the sha256 of every
touched file) and
`.agents/shared/planning/metrics-agent-tools/evidence/mutation-matrix-pre-run-manifest-2026-11-03.json`
(sha256 `44629b770ab1767c125b038134eb080d4e484f5c93a52d1cbfb49aca088f45ac`).

---

## 9. The disclosed deviations (the dispositions)

Every deviation is recorded in `fold-ledger.md` §F.2 (the approver advisories) + §H (the
implementation log). The three that change a count or a scope are:

1. **The pin census shifted** (15 EDIT → 16 EDIT sites; the scope family 2 → 3 sites) — both
   re-derived by grep at implementation time, both recorded in §H.3 / §H.4.
2. **The loader's cold-boot metadata scan did not know the metrics category** — discovered by
   the docs-surface pack; fixed by adding the factory to the warm scan (the W1-FIX-NOW class).
   Without it the agent's prompt had NO "## Metrics" section until an instance build happened to
   repopulate the registry.
3. **The loader renders the category NAME + the tool NAMES, not the category DOC body** — a
   house-wide characteristic (`get_category_doc` is resolved by the display name, which never
   matches the registry key). The `CATEGORY_DOC` therefore reaches the agent through
   `tool_help` and through the agent's `tools_note.md` — both asserted.
4. **The byte ceiling was dead code at the first cut** — the council's M-1 finding, remediated
   in the fix cycle (see §8b). It is recorded here because it was the lane's own false
   evidence, not a pre-existing defect.
5. **The mutation matrix shipped 12 of ~24 planned legs at the first cut** — the dispositions
   for every planned leg are in fold-ledger §H.8.

**No migration, no dependency, no configuration knob, no contract-wire change.** The migration
delta is EMPTY; `pyproject.toml` and `frontend/package.json` are untouched; `schema_version`
stays 1 and `vocabulary_version` is untouched by this lane.
