# Canary Injection Defense — Functional Gates A–H (P5)

**Date:** 2026-11-06
**Commission:** canary-injection-defense P5 functional gate (Gates A–H + Gate M / NC-1 leg)
**Worktree:** `/home/nea/ensemble-src-wt-canary-injection-defense` · branch `feature/canary-injection-defense`
**Gate tip (A–H legs):** `08bcbef2e50cd1b5dab725481276d7dab59d1310` (verified; porcelain 0)
**Net-new TRUE base:** `24ba60462b8d97aa3f2ab311a3e21c9ddbd12bfb` (pre-implementation) · lane origin `97b76e20e`
**P5 commits landed on the branch (on top of gate tip):**
- `1c1739439` — fix(canary): §7.6 limits sentence at rule.md + hand-off report (Gate E(f) fix cycle)
- `573602d23` — test(docs): family-suite header docstring refresh (P5 doc-fold)
- `bcd08d2b6` — test(evidence): P5 mutation record (executed M1–M8 + CER-08 + M-floor)
- (this evidence commit — RESULTS + PACKS.md registration)

**Interpreter/fences (all runs):** `/home/nea/ensemble-src/.venv/bin/python` (worktree has no venv — main-venv + cwd=worktree, certified pattern; `daemon.__file__` proven worktree-resident in both A-head and E(a) legs); per-variable env scrub (`env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DATABASE_URL PYTHONDONTWRITEBYTECODE=1`); `-q -p no:cacheprovider`; `timeout` wrappers; zero live contact (:9797/:8079 untouched; install root entered read-only for the T38 CWD leg only, verified byte-unchanged). Logs: `/tmp/canary-p5/`.

---

## Verdict summary

| Gate | Verdict | One-line evidence |
|---|---|---|
| A — net-new comparison | **PASS** | Net-new deterministic reds **0**; 5 prompt-integrity reds **same-file-set** proven; family 556/0 at HEAD |
| B — sealed artifacts | **PASS-WITH-DISCLOSURE** | 1 red = exactly the 4 known base-stale rows, blob-proven pre-existing; zero `daemon/` diff; no re-pin |
| C — plane census | **PASS** | 15/15; `DISCLOSED_READ_HOLDERS` still exactly `{"watcher"}` |
| D — integrity posture + sweep + NC-1 | **PASS** | D-1 canary SKIP asserted verbatim; D-2 5F/1630P all pre-existing developer/charter; D-3 12 needles scoped to header grammar, 0 prose hits |
| E — boot-scan/skills/T38/must-not-claim | **PASS-WITH-DISCLOSURE** | (a–e) PASS at `08bcbef2e`; (f) FAIL-as-landed → fix cycle `1c1739439` → re-gate PASS |
| F — tool-categories | **PASS** | Byte-unchanged 3 ways (0 names, 0 bytes, sha identical); artifact test 9/9 |
| G — counts re-derivation | **PASS** | Tracked 379 (find 380 = 1 ignored `.pyc`, disclosed) · dirs 44 · rules-json 1 · baseline 181 · generator `--check` exit 0 |
| H — scope + privacy | **PASS** | Complement empty (31 = 23+4+1+1+2); personas byte-identical; secret scan both-controls 0 hits; campaign predicate 0 |
| M — mutation floor (CER-08) | **FLOOR MET** | 7/8 exact-match; M4 honest mismatch root-caused; M7 mandatory proven; final green 556 |

**Overall: GO** — all gates green at their adjudicated verdicts; every disclosure is a routed/pre-existing item, none lane-caused and live at HEAD.

---

## Gate A — the net-new comparison (FAILED-list diff, identical commands both legs)

Commands (identical at BASE and HEAD): CMD1 `tests/unit/agents/` · CMD2 `tests/test_chart_image_delivery_e2e.py` · CMD3 `tests/unit/tools/test_plane_agent_surface_census.py tests/unit/tools/test_tool_category_artifact.py` · CMD4 `tests/unit/test_report_integrity_prompts.py` · CMD5 `tests/unit/tools/test_prompt_section_reference_integrity.py`. BASE leg in isolated detached worktree `/tmp/canary-p5-base` @ `24ba60462` (teardown verified: tip `08bcbef2e` intact, porcelain 0, worktree pruned).

| CMD | BASE `24ba60462` | HEAD `08bcbef2e` | BASE-only | HEAD-only | Net-new |
|---|---|---|---|---|---|
| 1 | 447 passed, 0 failed | 608 passed, 0 failed | — | — | **0** (+161 nodes = lane pins) |
| 2 | 1 failed / 23 passed | 1 failed / 23 passed | — | — | **0** (identical id) |
| 3 | 24 passed | 24 passed | — | — | **0** |
| 4 | 52 passed, 23 skipped | 52 passed, 24 skipped | — | — | **0** (+1 skip = canary) |
| 5 | 5 failed / 1582 passed | 5 failed / 1630 passed | — | — | **0** (same-file-set) |

**The 5 prompt-integrity reds — same-file-set proven (the commission's "ids may shift by collection order")**:

| BASE id | HEAD id | Underlying file (log-printed, both legs) |
|---|---|---|
| `[workflow.md12]` | `[workflow.md13]` | `agents/developer/workflow.md` |
| `[workflow.md19]` | `[workflow.md20]` | `agents/developer[v2]/workflow.md` |
| `[install-mermaid-cli.md]` | `[install-mermaid-cli.md]` | `agents/charter/skills-template/install-mermaid-cli.md` |
| `odd_fence[rule.md4]` | `odd_fence[rule.md5]` | `agents/charter/rule.md` |
| `odd_fence[workflow.md4]` | `odd_fence[workflow.md5]` | `agents/charter/workflow.md` |

Proof tier: (i) both logs print identical repo-relative offending paths per red; (ii) assertion `E`-lines byte-identical across legs (diff = 0, incl. the 41-entry fence inventory); (iii) shift mechanism derived from git — the lane adds exactly one `rule.md` + one `workflow.md` (canary's) ahead of each offended file in the `rglob` walk (`ids=lambda p: p.name`); all 5 offending files byte-unchanged between revs. CMD2's red signature byte-identical (same 4 sha-mismatch rows, same current-shas). CMD4 +1 skip arithmetic-pinned to canary's empty-team exemption (meta `team_members: []`; skip line at `test_report_integrity_prompts.py:242`). **All reds isolated 3× — deterministic (0 flakes, 18/18 + 15/15 isolated runs).**

**Expected-ledger check (Gate A note):** the re-derivation set (T4/T5/T6/T7/T8/T21/T22/T25/T29) is green on BOTH legs via the in-commit folds — no T4/T29 HEAD-only reds (which would have meant skipped folds and BLOCKED). Post-fold they appear in neither FAILED list; no flip signature.

## Gate B — sealed artifacts: PASS-WITH-DISCLOSURE

`TestPhaseDDoesNotModifySealedArtifacts` → 1 failed: exactly the 4 known base-stale rows (`agents/ari/workflow.md`, `agents/ari/rule.md`, `daemon/sources/registry.py`, `daemon/constants.py` — R1 seal-lane-owned). Pre-existing proven at blob level: all four `current` shas identical BASE↔HEAD (`303c78e3…`/`6a56c5d3…`/`1f18a51d…`/`5dd9bfa3…`); `git diff 24ba60462..HEAD -- daemon/` = 0 files; seal-baseline file untouched by the lane (no re-pin — correct per spec).

## Gate C — plane census: PASS

15/15 passed. `DISCLOSED_READ_HOLDERS` still `frozenset({"watcher"})` (`test_plane_agent_surface_census.py:67`) — neither canary nor the screened personas added; exposure not whitelisted. (`worker`'s pre-existing `plane_sync` byte-identical base↔HEAD — not a read holder.)

## Gate D — posture + sweep + NC-1: PASS

- **D-1** `52 passed, 24 skipped` — canary SKIP asserted verbatim: `SKIPPED [1] tests/unit/test_report_integrity_prompts.py:242: 'canary' has no team_members — not a parent agent` (deliberate D1-A exemption; a future spawn-right ruling flips it deliberately).
- **D-2** `5 failed, 1630 passed` — the 5 reds are exactly Gate A's CMD5 set (developer×2 + charter×3); failure bodies name `agents/developer/workflow.md`, `agents/developer[v2]/workflow.md`, `agents/charter/skills-template/install-mermaid-cli.md`, `agents/charter/rule.md`, `agents/charter/workflow.md` — none in lane scope, none in the six screened personas or canary's files. Pre-existing base debt (byte-identical at base), disclosed not fixed.
- **D-3 NC-1 scoping record (carried obligation A6)** — needles scoped to **header-grammar shapes**, per `phase5-plan.md:11-13` reconciled against the landed D3 prose at `agents/aegis/rule.md:52-56`: 12 needles — `INJ-[0-9]{2}`; line-start 4-backtick `` `{4} ``; `^verdict:` / `^confidence:` key grammar (template + emitted forms); `^sha256: ` 64-hex; `^(source|origin|bytes|truncated|escaped|rules|classifier): ` header lines; `[UNTRUSTED-DATA TRUNCATED retained-bytes:`; `[UNTRUSTED-DATA v1 END lines=`; standalone-line `[UNTRUSTED-DATA v1 BEGIN]`. Scoping decisions recorded: (i) inline `[UNTRUSTED-DATA v1 BEGIN]` inside aegis's bullet is legitimate pointer prose → excluded by line-anchoring; (ii) `[UNTRUSTED-ADJACENCY:` is doctrine, not screen restatement → excluded; (iii) bare words (verdict/fence/INJ/injection-screen) banned as needles. Result: all 12 needles ≥1 hit in each of the 4 skill bodies; **0 hits across all 24 persona prose files**; aegis `:52-56` targeted demonstration = 0/12 (verbatim pointer prose printed in-run). One vacuous needle (`\|` ERE literal-pipe trap) was caught by the positive control and replaced — the control discipline did its job.

## Gate E — boot-scan, skills, T38, must-not-claim: PASS-WITH-DISCLOSURE

- **(a) Registry/boot-scan** — recipe adapted (prior lane's real source-mode boot → in-process `AgentRegistry.discover()` + exact `/api/agents` router emulation; adaptation documented): **API-AGENTS census 44** (43 + canary); **0 parse failures** (all three boot markers); `validate_tool_configs()` = exactly 1 warning (`maintenancer: git_commit` — base-proven, prior lane identical); tools-block fail-open guard clean (0 malformed→None, 0 missing); `daemon.__file__` inside the worktree (fence proven); T28 registry pack 8/8.
- **(b) Skill loads** — manifest `name: injection-screen` = 1 for canary/aegis/sweeper/worker; same-dir template bodies present (canary extras `injection_scan.py` + `injection-rules.json` present). hacker/cracker carry no `skill-set.yaml` — the **declared D2-B shape** (doctrine-only recipients, disclosed gap C3), not a failure.
- **(c) Allow sets** — canary exactly `['bash','filesystem','help','time']` (AM-1 no `self`, NC-4 no `proc`); hacker/cracker/sweeper each exactly the 6-set. Matches `ALLOW_BY_AGENT`.
- **(d) Constants** — `CHILD_ALLOW`=0, old bare names=0; `FAMILY_ROSTER`=5 (canary in), `INSTALLER_CHILDREN`=3 (T23 arity), `FAMILY_CHILDREN`=4, `DOCTRINE_ONLY`=2 (asserted positively), `MANIFEST_BEARING`=4, `TONE_HEADINGS` carries canary (3 headings), `CARDINAL_COUNT_BY_AGENT["canary"]=7`.
- **(e) T38 realized leg** — three-CWD digests (repo root · `/tmp` · `/home/nea/agents-ensemble`) **byte-equal**: `2bf0e68a83e8452712a076d06a45749fe947a1b0dd4a4303661422e651589bce` ×3; determinism re-run identical; monotonicity BOTH axes refused with exit 3 — discriminating case `suspect`+`--llm-verdict clean --llm-confidence high` REFUSED (`downgrade-refused: llm verdict clean ranks below heuristic suspect`), verdict-axis `hostile`+`suspect` REFUSED, confidence-axis `high`→`medium` REFUSED; LLM pass OFF by default (no `--llm-*` in digest runs; `classifier: heuristic` in every block); install root byte-unchanged (top-level pre/post diff empty). Declared-not-assumed facts: host `python3` = `/usr/bin/python3` 3.12.3; campaign template carries `inertness_attested_by: aegis` + `attested_at` (workflow.md:72-73; firer-attests doctrine in three places).
- **(f) Six must-not-claim items** — **FAIL as landed at `08bcbef2e`** (present in `soul.md:48-57` all six ✓ and campaign-record template `workflow.md:78` compressed ✓; **absent from `rule.md` and from the hand-off report surface** — §7.6 mandates both, AM-13 rationale re-created for the hand-off reader). **Fix cycle `1c1739439`** (tester-gate-owned, v0.28.9 F-1 precedent): transplanted the approved compressed sentence → `rule.md:17` (prose; cardinal count 7 intact) + `workflow.md:37` (§8 Report — located at `:35-37`, not `:84-87` as the plan estimated); baseline regenerated + `--check` exit 0 **181**; re-gate: family **556/0** (T29/T31 single-site anchors survived), sweep exactly the 5 known reds (no new), six-item matrix present at both sites, NC-1 needles 0 hits on the new prose.

**Disclosures (E):** fix-cycle commit is tester-gate-owned (disclosed for the leader's fold-time delta review); **T38 ladder spec-vs-realized correction** — the shipped scanner resolves its rule table script-adjacent (`Path(__file__).with_name`, pinned by T32; single rung), i.e. the phase5 "cwd ladder" note describes a mechanism reconciled differently: the realized ladder seam is T38's ruled three-CWD set + the A7 loud absent-release refusal (test-side). Digest equality holds by construction and was corroborated empirically. Plan/spec text should be reconciled at fold time.

## Gate F — tool-categories: PASS

`git diff 24ba60462..HEAD -- .agents/shared/metrics/tool-categories.json` → 0 names, **0 bytes**; sha256 identical base=HEAD=disk (`1ff31ecc…e02ede67`); artifact test 9/9. A skill is not a category — the F-1 trap avoided.

## Gate G — counts re-derivation: PASS

Tracked `git ls-files agents` = **379** (authority). `find` = 380: exactly one ignored file `agents/canary/skills-template/__pycache__/injection_scan.cpython-312.pyc` (`.gitignore:2`; pre-recon mtime; left untouched — evidence of a scanner import, disclosed). Discoverable dirs **44**; `injection-rules.json` = **1** (ships once, AM-4); baseline `wc -l` = **181**; `scripts/protection/generate_prompt_artifact_baseline.py --check` → `PROMPT-BASELINE: OK — 181 versioned artifact(s) match .prompt-artifacts.sha256`, **exit 0**.

## Gate H — scope + privacy: PASS

- **(a)** Scope complement **EMPTY**; constructive proof: 31 lane-diff files = `agents/` 23 + `tests/` 4 + `.agents/tester/PACKS.md` 1 + planning dir 1 + `.agents/approver/` 2.
- **(b)** Untouched personas: direct (leader/developer/reviewer/tester/architect/approver) **EMPTY**; broader (only canary 9, aegis 5, worker 3, sweeper 3, hacker 1, cracker 1 + `.prompt-artifacts.sha256` may change) **EMPTY**.
- **(c)** Secret-shape scan **both controls**: positive control fired (`secret-scan-positive-control.txt:6` synthetic `sk-proj-SYNTHETIC…` — scanner non-vacuous); real scan over all 30 other lane-diff files → **0 hits, 0 errors**.
- **(d)** Campaign-artifact predicate → **0**.
- **(e)** Off-repo store documented at 5 sites (`$HOME/.local/state/aegis/engagements/<id>/`, recorded-path discovery, never glob); no tracked file at that path shape; no such directory in-repo.

## Gate M — mutation floor (A8/CER-08 + M-floor): **FLOOR MET** (executed at tip `1c1739439`; scoped = family suite; baseline 556)

Full record committed at `bcd08d2b6` → `.agents/tester/RESULTS/2026-11-06-canary-injection-defense-mutation-record.md`. Summary:

| Leg | Verdict | Observed |
|---|---|---|
| M1 canary T29 anchor | MATCH | exactly `test_legality_cardinal_pinned_verbatim[canary-white-hat-ethos]` |
| M2 directive-sentence literal | MATCH | exactly 3: `test_t32_golden_block_is_byte_exact`, `test_t32_structure_tokens_and_directive_literal`, `test_t33_malformed_inputs_typed_failures` |
| M3 corpus verdict swap | MATCH | exactly `test_t34_case_ground_truth[neg-changelog]` |
| M4 worker doctrine anchor | **MISMATCH (honest)** | 2 observed vs 3 predicted — `test_worker_untouched` NOT red: the additivity pin diffs `LANE_BASE_COMMIT`→tree and bites **deletions of BASE content only**; the planted edit touched a lane-ADDED line (`+`-only hunk ⇒ `deletions==[]`). Recorded as a pin-bite-shape finding (additivity is commit-relative), consistent with the impl lane's own 2-leg evidence |
| M5 rule-id change | MATCH | 4 reds incl. the predicted in-body `KeyError: 'INJ-07'` at `:3900` |
| M6 unescaped BEGIN line | MATCH | exactly `test_t33_forged_end_escaped_and_structurally_inert` (adaptation recorded: corpus stores payloads raw) |
| **M7 mirror silent-drift (MANDATORY)** | MATCH | exactly `test_mirrors_byte_identical_modulo_autoload_and_blank_lines` ("aegis's mirror drifted…"); version+polarity pins stayed green — the silent-drift class proven |
| M8 ladder severance | MATCH | exactly `test_t38_three_cwd_byte_identical` at the A7 loud refusal ("never silently degrade to 2 CWDs"); zero scanner-body reds — pin bites the CWD-set seam. **Adaptation recorded**: realized scanner is script-adjacent single-rung; the ladder seam is test-side |

Final green **556 passed** at clean tip after all restorations. Impl-lane history recovery: 4 executed-mutation blocks recovered verbatim from commit bodies (G1.7/P2/P3/G4.5b, with planted/restored SHAs); **the "5 vacuous plants" narrative is NOT recoverable on disk** (only re-plant M-A1 named) — stated in the record; the executed record is the authoritative in-repo CER-08 evidence.

## Doc-fold (tidier-routed catch)

`573602d23` — family-suite header docstring refreshed (was "Seven classes added after T1–T30" / "exactly-177 rows"): now names the 13 post-T30 lane classes (T31–T38 + tidier additions) and the **181-row** baseline; AST-verified docstring-only; suite still 556.

## Disclosures & routed items

1. **Gate E(f) fix cycle** `1c1739439` — tester-gate-owned persona-text fix (transplant, no new doctrine); leader delta-review at fold time recommended.
2. **T38 ladder spec-vs-realized** — plan text vs shipped scanner reconciliation (recorded above; plan doc owned by the lane).
3. **M4 additivity pin bite-shape** — `test_worker_untouched` under-enforces vs its docstring for lane-ADDED lines; mutation-record carries the finding (future-drift guard gap, ~1 assertion; routed to the next lane touching that pin).
4. **The 5 prompt-integrity reds** — pre-existing developer/charter debt, byte-identical at base, disclosed not fixed (owner: the settings/charter debt lane).
5. **4 base-stale sealed rows** — R1 seal lane owns; correctly NOT re-pinned here.
6. **Ignored `__pycache__` `.pyc`** under `agents/canary/skills-template/` — scanner-import residue, inert, disclosed (Gate G).
7. **Impl "5 vacuous plants" history** — not on disk; CER-08 record discloses and supersedes with the executed matrix.
8. **hacker/cracker skill-set absence** — declared D2-B shape (C3 gap), asserted positively by `DOCTRINE_ONLY`.

## Generator + porcelain state at close

`generate_prompt_artifact_baseline.py --check` → exit 0 (181) — re-run post-fix-cycle. Porcelain 0 at final tip; no pushes performed by the gate; worktree retained for the leader sweep/stage decision.
