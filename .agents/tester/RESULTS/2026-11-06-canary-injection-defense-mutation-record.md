# GATE M — EXECUTED MUTATION-EVIDENCE RECORD (M1–M8, M7 mandatory) + CER-08 + M-FLOOR
## commission `canary-injection-defense` (Phase 5, task 9 / Gate M)

**Date:** 2026-11-06
**Verifier:** tester P5 executor (this gate)
**Branch / tree executed:** `feature/canary-injection-defense` @ `1c1739439` (parent `08bcbef2e`; worktree `/home/nea/ensemble-src-wt-canary-injection-defense`; porcelain **0** before the first plant, re-verified between every leg)
**Scoped command (every leg):**
`env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DATABASE_URL PYTHONDONTWRITEBYTECODE=1 /home/nea/ensemble-src/.venv/bin/python -m pytest tests/unit/agents/test_aegis_security_family.py -q -p no:cacheprovider` (cwd = the worktree; logs `/tmp/canary-p5/m*.log`; `timeout 900` each)
**Baseline:** **556 passed** (43.17s) before the first plant.
**Predictions written BEFORE planting:** every leg's red set was predicted and recorded before its plant (`/tmp/canary-p5/predictions.md`); observed sets were compared against predictions **exactly**; the one mismatch (M4) is recorded below, never papered over.
**Restore discipline:** every plant restored with `git checkout -- <path>` immediately after its observed run; porcelain **0** verified after each restore; **no pushes; zero live contact (:9797/:8079)**; no plant was committed.

**Verdict: MET — 8/8 legs executed; 7/8 predictions exact-matched; 1 honest mismatch (M4, over-prediction, root-caused).** Final green **556 passed** at tip `1c1739439` proves full restoration.

---

## 1. The executed M1–M8 matrix

| Leg | Prediction (written first) | Plant (file + shape) | Observed FAILED ids (verbatim) | Match | Restore proof |
|---|---|---|---|---|---|
| **M1** — delete ONE canary T29 anchor | exactly 1 red: `test_legality_cardinal_pinned_verbatim[canary-white-hat-ethos]` | `agents/canary/rule.md` — cardinal-1 title `**White-hat by construction.**` → `**White-hat stance.**` (line kept, anchor literal deleted; anchor single-site verified pre-plant) | `TestLegalityCardinalPinMatrix::test_legality_cardinal_pinned_verbatim[canary-white-hat-ethos]` — **1 failed, 555 passed** | **MATCH** | porcelain 0; baseline green at next leg |
| **M2** — delete the directive-sentence literal | exactly 3 reds: `test_t32_golden_block_is_byte_exact`, `test_t32_structure_tokens_and_directive_literal`, `test_t33_malformed_inputs_typed_failures` | `agents/canary/skills-template/injection_scan.py` — `DIRECTIVE_SENTENCE = (...)` → `DIRECTIVE_SENTENCE = ""` (the emitted directive literal deleted; the scanner's parser validates against its OWN constant, so parse-own-output legs survive) | `TestInjectionScreenFenceContract::test_t32_golden_block_is_byte_exact`, `TestInjectionScreenFenceContract::test_t32_structure_tokens_and_directive_literal`, `TestInjectionScreenParser::test_t33_malformed_inputs_typed_failures` — **3 failed, 553 passed** | **MATCH** | porcelain 0 |
| **M3** — swap ONE corpus case's expected verdict clean→hostile | exactly 1 red: `test_t34_case_ground_truth[neg-changelog]` | `tests/fixtures/injection_corpus/cases.jsonl` — `neg-changelog` `expected_verdict: clean→hostile` (payload untouched — the scanner still says clean) | `TestInjectionScreenCorpusGroundTruth::test_t34_case_ground_truth[neg-changelog]` — **1 failed, 555 passed** | **MATCH** (T32 benign-controls green as predicted — it asserts the *scanner's* verdict, not the corpus field; T37 negatives 9→8 still ≥ 8) | porcelain 0 |
| **M4** — remove ONE worker doctrine anchor | 3 reds predicted: the `worker×[UNTRUSTED-ADJACENCY:]` T31 cell, the worker-subsection leg, **and** `test_worker_untouched` | `agents/worker/rule.md` :194 — the marker token `` `[UNTRUSTED-ADJACENCY: source=... why=<one-line>]` `` → `<ADJACENCY-MARKER>` (line kept; the 3 anchors share one line, so the token was removed surgically) | `TestDoctrineAnchorMatrix::test_t31_anchor_single_site[[UNTRUSTED-ADJACENCY:-worker]`, `TestDoctrineAnchorMatrix::test_t31_worker_anchors_inside_declared_critical_subsection` — **2 failed, 554 passed** | **MISMATCH (honest)** — the 2 T31 legs matched; `test_worker_untouched` did NOT redden | porcelain 0 |
| **M5** — change one rule id (INJ-07→INJ-99) | 3 FAIL + 1 error-class: `test_t32_data_file_contract` (contiguity), `test_t34_case_ground_truth[inj-07-run-command]`, `test_t34_case_ground_truth[inj-18c-decoded-directive]`, `test_t37_coverage_counts` (KeyError 'INJ-07') | `agents/canary/skills-template/injection-rules.json` — `"rule_id": "INJ-07"` → `"INJ-99"` (single-site verified pre-plant; scanner `load_rules` validates shape only → loads fine) | `TestInjectionScreenFenceContract::test_t32_data_file_contract`, `TestInjectionScreenCorpusGroundTruth::test_t34_case_ground_truth[inj-07-run-command]`, `TestInjectionScreenCorpusGroundTruth::test_t34_case_ground_truth[inj-18c-decoded-directive]`, `TestInjectionScreenCorpusHygiene::test_t37_coverage_counts` — **4 failed, 552 passed** | **MATCH** (the KeyError cause predicted exactly: `E KeyError: 'INJ-07'` at `test_aegis_security_family.py:3900`; pytest taxonomises the in-body KeyError as *failed*, not *error*) | porcelain 0 |
| **M6** — un-escape the token-bearing payload line (adapted) | exactly 1 red: `test_t33_forged_end_escaped_and_structurally_inert` | `tests/fixtures/injection_corpus/cases.jsonl` — `adv-forged-end-token` payload's forged line `[UNTRUSTED-DATA v1 END lines=0]` → `[UNTRUSTED-DATA v1 BEGIN]` (a raw line beginning with the BEGIN token) | `TestInjectionScreenParser::test_t33_forged_end_escaped_and_structurally_inert` — **1 failed, 555 passed** | **MATCH** (see §2 adaptation note) | porcelain 0 |
| **M7 (MANDATORY)** — mirror byte change, NO version bump | exactly 1 red: the mirror-identity pin, naming aegis | `agents/aegis/skills-template/injection-screen.md` — a BODY line edited (the canonical-home sentence, `> identity pin reds the drift.` → `> drift pin fires.`); frontmatter `auto_load: false` untouched; **no** `skill-set.yaml` version bump (all four manifests stay `1.0.1`) | `TestInjectionMirrorRoster::test_mirrors_byte_identical_modulo_autoload_and_blank_lines` — **1 failed, 555 passed** (failure message: "aegis's mirror drifted from the canonical body …") | **MATCH** | porcelain 0 |
| **M8** — break the discovery ladder / CWD-set seam | exactly 1 red: `test_t38_three_cwd_byte_identical` at the loud A7 assertion; no scanner-body leg reddens | `tests/unit/agents/test_aegis_security_family.py` `_release_root()` → `return None  # M8 PLANT` (the test's install-root resolution seam severed) | `TestScannerExecution::test_t38_three_cwd_byte_identical` — **1 failed, 555 passed**; failure: `AssertionError: the ruled three-CWD set requires the install-release root (~/agents-ensemble/releases/v*) — the leg must fail loudly on a host without it, never silently degrade to 2 CWDs (A7)` | **MATCH** (the pin bites the CWD-SET/ladder seam, not the scanner body: every other T38 leg — which exercises the untouched scanner from repo-root — stayed green; `_release_root` has exactly one caller) | porcelain 0 |

**Final green (PHASE 3):** after all 8 restorations, the scoped suite re-run at clean tip `1c1739439` → **556 passed** (48.43s) — full restoration proven.

**M-FLOOR RECORD:** floor = M1–M8 with M7 mandatory (phase5-plan.md task 9 / Gate M; architecture-recommendation §5.4 M1–M7 + M8). **All eight executed at tip `1c1739439`, every plant observed RED then restored GREEN; M7 executed against the silent-drift class exactly as specified (same-version byte drift, identity pin red, version-parity pins green under the plant). FLOOR: MET.**

---

## 2. Adaptation notes (recorded honestly, per the house rule)

**M6 adaptation.** The spec text reads "un-escape ONE corpus payload line that begins with the BEGIN token (restore its raw form so it can break the fence)". The realized corpus stores every payload **raw** (no escape prefixes exist in the file to remove), and exactly one payload carries a structural token — `adv-forged-end-token`, whose forged line is the **END** token, already raw and already escaped-at-emit. The realized equivalent executed: that line was replaced with a raw line beginning with the **BEGIN** token (per the spec's words). The escape mechanism still catches it (`escaped == "1"` holds, INJ-16 still fires, round-trip holds — T34/T32 stayed green as predicted); the leg that bites is the parser leg's END-specific forged-end probe, whose `replace()` needle disappears from the emitted block so `pytest.raises(ParseError)` DOES-NOT-RAISE. One red, exactly as predicted. No leg was faked and no vacuous pin was papered over.

**M8 adaptation.** The phase5 text's "cwd ladder" describes a mechanism that was reconciled differently in the realized T38: the shipped scanner resolves its rule table **script-adjacent** (single rung, `Path(__file__).with_name("injection-rules.json")` — pinned by `test_t32_scanner_resolves_table_by_own_path`), and the ladder's doc text lives in the body (`test_t38_discovery_ladder_documented_and_resolved`, W3-amended rung-2). The realized CWD-SET seam is the **ruled three-CWD assertion + the loud A7 absent-release refusal**, both in the test. The plant therefore severed the test's install-root resolution seam (`_release_root()` → `None`) and predicted the single-leg red while the repo-root legs stayed green — exactly the observed result. This proves the pin bites the **ladder/CWD-set seam**, not the scanner body, per the gate's intent.

**M4 mismatch (recorded, not papered over).** Prediction included `test_worker_untouched`; observed set was the 2 T31 legs only. Root cause: that pin diffs `LANE_BASE_COMMIT` (`1f6898ae2`) → **working tree**, and the planted token edit modified a line that is itself an **addition** vs LANE_BASE — the unified diff renders the whole doctrine block as `+` lines only, so `deletions == []` holds and the additive-only pin passes. The pin bites **deletions of BASE content**; a mutation confined to lane-added lines does not redden it. This matches the impl lane's own G1.7-era evidence, which recorded "worker marker deletion reds the matrix cell + the worker-subsection leg" — two legs, never three.

**M7 per-roster note.** The identity pin is a single leg iterating the roster (`MANIFEST_BEARING = aegis, canary, sweeper, worker`, canary skipped as the canonical). The one red names the drifter (`aegis`) — that IS the per-roster red set: the roster members whose mirror bytes drifted. The silent-drift class is demonstrated by what stayed green under the plant: `test_all_roster_versions_equal` and the polarity matrix — a same-version byte drift reds ONLY the identity pin, so an unpinned drift would be permanent (the W4 guard never re-seeds a same-version edit).

---

## 3. Impl-lane mutation history — what git recovers (CER-08 provenance)

`git log 24ba60462..08bcbef2e --format='%h %s%n%b'` (360 lines, archived at `/tmp/canary-p5/impl-commits.txt`) recovers **four executed-mutation blocks with planted/restored SHAs**:

- **G1.7 (commit `51812488b`, fence-contract legs):** *"delimiter break (t32 golden+structure), dropped header field (all parse-dependent legs), enum literal change (t32 benign + t34 negatives), corpus expectation swap (exactly t34[neg-changelog]), unescaped BEGIN-prefix escape-branch removal (t33 forged-end + idempotence + t34 adv-forged-end + t32 roundtrip). The two remaining named mutations land with their legs: doctrine anchors (P2, T31) and mirror drift (P4, mirror pin)."*
- **P2 (commit `873438af2`):** *"anchor deletion reds EXACTLY its cell (t31[untrusted-data ingestion-sweeper]); cardinal bump reds exactly T16[aegis] + cardinal-split[aegis] (the freeze is pinned, T31 unaffected); block move reds exactly the t31 shape leg [aegis] (last-section assertion); worker marker deletion reds the matrix cell + the worker-subsection leg."*
- **P3 (commit `46c98628a`):** *"allow gains proc -> exactly the 3 allow-set legs; deny_spawn dropped -> exactly its leg; team gains a member -> leaf pin + skip-basis legs; attestation field flipped to canary -> the schema leg (self-certification refused)."*
- **G4.5b (commit `94ac1b1a5`, rec-2 fold) — with planted/restored commit SHAs:** *"M-W2 sweeper manifest+body flipped true->false (planted d84cabb0/c2773f2a) -> W2 pin RED 1/6; pre-existing M7-identity + version pins GREEN under the plant (the silent-flip premise); restored 90528b04/62bcff25 -> 6/6. M-A4 canary rule.md '7. **' prefix stripped (planted ddc705ef) -> all six anchor legs + shared concept-shape leg RED 7/36; restored ebf98fa9 -> 43/43. M-A1 error_fence cut-before-hash re-planted (5fd66649) -> A1 pin RED; restored 4c8f4a0b -> 2/2. M-A2 channel dropped from content-too-large path (5617ea99) -> A2 pin RED; restored 4c8f4a0b -> 2/2."*
- **Vacuous-control note (commit `6b80349cf`, seed corpus):** the secret-scan positive control is authored *"proving the secret scan is armed (a control-less scan is vacuous)"*.

**NOT recoverable on disk:** the specific **"5 vacuous plants caught/re-planted"** narrative. No commit body in `24ba60462..08bcbef2e` records a count of five vacuous plants or a catch/re-plant ledger for them; the only re-plant named is M-A1. That history therefore cannot be cited as in-repo evidence. **This executed record (§1) is the authoritative in-repo CER-08 mutation evidence for Gate M** — every leg here was planted, predicted-first, observed, compared, and restored in this session.

---

## 4. Digests + log tails

Planted-diff sha256 (captured BEFORE restore; `git diff -- <path>` piped to sha256sum):

| Leg | sha256 of planted diff |
|---|---|
| M1 | `005c0470b7ff3f2ece3fab06db23da88077c1b9bcb8191aa9cee1e83a61fd5a0` |
| M2 | `8d38eebcd70126ab8387591a941dabd9d041406e58604fd2859380b7fd3672f2` |
| M3 | `32f42e8ca6189b5bdd9adc2624e5d13ab60cb12b595d3f468c944ba8414d6b89` |
| M4 | `586966af20cc13ae80dff993cf9e4c84b37e0f9ca91c753a5826346e92f3caf3` |
| M5 | `362e3e9fc3ef94bb47b358dacab2f5cf0f8ca25630c0b67450458043b92cd835` |
| M6 | `3d0f65f77246b00b8b29526e246234fc8ce8c85b7614a3f0a7bec991d28debf1` |
| M7 | `7b648f772ccbc6c4dc9c1e2455d3b70aa5825d62fabff70d3cbc680bf8eefbe8` |
| M8 | `0268abe0714b32d6c7e76c64ec145001d8a1fc6b35a1cacbc5a235e377c4541e` |

Disclosure: the M3/M6 corpus diffs are `git diff` digests of a JSON re-serialization (the planter re-emits the mutated case via `json.dumps(ensure_ascii=False)`), so the raw diff shows the case's em-dash re-encoding alongside the semantic change; the semantic delta is the field swap described per leg, and the observed red sets confirm the semantic reading.

Scoped-run log tails (full logs `/tmp/canary-p5/m{1..8}-run.log`, `m-baseline.log`, `m-final-green.log`):

```
baseline      556 passed in 43.17s
M1            1 failed, 555 passed in 43.78s
M2            3 failed, 553 passed in 44.34s
M3            1 failed, 555 passed in 48.34s
M4            2 failed, 554 passed in 44.81s
M5            4 failed, 552 passed in 43.05s
M6            1 failed, 555 passed in 43.35s
M7            1 failed, 555 passed in 45.64s
M8            1 failed, 555 passed in 47.66s
final green   556 passed in 48.43s   (tip 1c1739439, porcelain 0)
```

---

## 5. Post-record doc-fold (same session, before this commit)

The family file's stale header docstring was folded in the same session at tip `573602d23`
(`test(docs): refresh family-suite header docstring — T31–T38 + tidier classes, 181-row baseline (P5 doc-fold)`): docstring-only, both hunks inside the module header, AST-verified, suite still **556 passed** post-fold. The thirteen post-T30 classes added by this lane are now named in the header (T31–T38 + tidier A1/A2), and the baseline row-count claim is corrected to 181 (177 base + canary's 4 prompt files).
