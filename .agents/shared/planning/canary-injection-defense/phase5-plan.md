# Phase 5: Verification

## Objective

Prove — on the committed branch — that the `canary-injection-defense` lane introduces **no behaviour
regression**, that every commission expectation holds, and that the artifacts are exactly what the
plan claimed. Each gate below is **independently checkable** and separately attributable, so a
failure names its own cause.

Eight gates, **A–H**, plus the **contract-drift leg** (NC-1): the fence contract's distinctive
literals (INJ-*, the three-value verdict, the `key: value` header, the 4-backtick carrier, the
truncation marker) appear in the skill bodies and **nowhere** in persona prose (CER-04 no-restatement
grep). NC-1…NC-5 are **CLOSED-BY-RULING** (architect G0) and are verified here as legs, not as open
risks.

---

## Entry Criteria

- **Phase 4 exited** — the one commit exists, the baseline **file exists at exactly 181 rows** and is
  clean (existence + count are asserted explicitly — the `--check` exit code alone proves nothing),
  the lane test file is green, and the working tree is clean.
- The **true pre-fix base** for the net-new comparison is known: `97b76e20e1d640c69b720149547e93565ab22b4c`
  (= `origin/latest` at lane start). Net-new reds are computed as the **FAILED-list diff** of the
  same commands on the base and on HEAD — **not** a raw failure count.
- A venv exists (`uv sync`) for the suite runs; the baseline checks remain venv-free.
- The corpus secret-shape scanner has **both** a positive and a negative control available (a
  control-less scanner is vacuous, not clean).

---

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | **Gate A — the full-suite net-new comparison.** Run the whole collectable suite on **HEAD** and
   on the **base** with the *same* commands and the *same* environment; diff the FAILED lists.
   **Expected-ledger note (the true signature):** the **re-derivation set** — T4 (team set), T5/T6/T7/T8
   (the **four-constant set**: `FAMILY_ROSTER` carries canary; `FAMILY_CHILDREN` + `ALLOW_BY_AGENT` carry it
   on the family axis while `INSTALLER_CHILDREN` stays a **3-tuple** so T23's arity holds; `TONE_HEADINGS`
   and `CARDINAL_COUNT_BY_AGENT` carry it on the persona axis), T21 (the
   family-manifest list), T22 (the seeded (agent, name) pairs), T25 (worker byte-unchanged — **reds
   by design this lane**; re-worded against this lane's own base) and T29 (25 cells + completeness) —
   are **green at base** and **green at HEAD only via the in-commit folds**; absent the folds they
   would be the HEAD-only reds. Post-fold they appear in **neither** FAILED list, and the ledger
   records the folds (no flip signature appears). | Phase 4 exited | **Net-new deterministic reds = 0.** A red that is red at the base too is pre-existing debt and is **disclosed**, not fixed. A HEAD-only red at the T4/T29 pins means the folds were skipped and **blocks**. Any other HEAD-only red is a lane regression and blocks. |
| 2 | **Gate B — the sealed-artifacts proof.** Run `tests/test_chart_image_delivery_e2e.py::TestPhaseDDoesNotModifySealedArtifacts` on the branch. | 1 | Green. The 35 `SEALED_SHA_BASELINES` entries are unchanged — none of them references a not-yet-existing `canary` path, so adding agent directories cannot redden them. **Disclosure carried forward:** 4 rows are **already base-stale** (`ari/workflow.md`, `ari/rule.md`, `daemon/sources/registry.py`, `daemon/constants.py`) and are owned by the seal lane — the gate's shape is **PASS-WITH-DISCLOSURE**, and this lane does **not** re-pin them as a side effect. **Re-pin ONLY if this lane touches a sealed file** — expected: none. |
| 3 | **Gate C — the plane census.** Run `tests/unit/tools/test_plane_agent_surface_census.py`. | 1 | Green, with `DISCLOSED_READ_HOLDERS` **still `{"watcher"}`** — neither `canary` nor the screened personas are added to the frozen read-holder set. The exposure is removed by an explicit non-empty allow on the new meta, not whitelisted. |
| 4 | **Gate D — the report-integrity posture + the prompt-section integrity sweep (two named legs,
   the predecessor's D shape).** (D-1) Run `tests/unit/test_report_integrity_prompts.py`. (D-2) Run
   `tests/unit/tools/test_prompt_section_reference_integrity.py` — the repo-wide prompt-surface sweep
   over **every edited prompt file** (the **five** edited persona `rule.md`s — `aegis`, `hacker`, `cracker`, `sweeper`, `worker` — + canary's four prompt files), enforcing
   the `.md`-token class and the bare-`agents/`-prefix closure class (`requirements.md` CER-15). | 1 | Both green. D-1: under the D1 leaf default, `canary` is **skipped** by the empty-team exemption (`test_report_integrity_prompts.py:242`) — a skip, not a pass-by-omission; the leg asserts the skip so a D1 change flips it deliberately. `aegis`'s existing `[REPORT SANITY:]` guidance (canonical home: its `rule.md`) is **not** removed by the `workflow.md` section append. D-2: every edited prompt file passes the repo-wide sweep — both closure classes clean or on the enumerated exemption list. |
| 5 | **Gate E — the boot-scan + the skill loads.** Run the registry-scan leg under the venv, then the tester's isolated boot-scan leg. | 1 | The registry resolves `canary`; the discoverable-agent census is **44** (43 + 1); the isolated boot scan is clean — no agent fails to register and no `tools` block parses fail-open; the `injection-screen` skill **loads** for every member of the screened roster (manifest-gated seeding: the manifest enumerates the entry and the body must exist at the same-dir template path); the **T38 scanner-execution leg is green** — the committed `agents/canary/skills-template/injection_scan.py` runs from the **ruled three-CWD set (W8): repo root · `/tmp` · the INSTALL/RELEASE ROOT** with the three runs **byte-identical** (fence + sha256 + verdict blocks), **three-run determinism**, the **LLM pass OFF by default**, and the **monotonicity refusal** firing on **BOTH axes (AM-3 / SK-11)** — a `--llm-verdict` **lower in severity** than the heuristic verdict is refused **and** a `--llm-confidence` **lower in rank** than the heuristic confidence is refused; the discriminating case is `suspect` + `--llm-verdict clean --llm-confidence high` (a pin that stops at `hostile` would let it through); **plus one content-presence pin:** the **six must-not-claim items** (architecture-recommendation §7.6) are present in phase3's authored text (canary's `rule.md`/`soul.md`, the campaign record template, and the hand-off report — P3 task 10b's landing sites). **Plus two declared-not-assumed facts:** the scanner legs run on a **host `python3`** (stdlib-only scanner; no host interpreter ⇒ the leg cannot run — declared, not discovered), and the campaign record template carries **`inertness_attested_by: aegis`** + `attested_at` (the **FIRER** attests inertness at fire time; canary's declaration is **not** a certification). |
| 6 | **Gate F — the tool-categories proof.** Confirm `.agents/shared/metrics/tool-categories.json` is **byte-unchanged** in the lane diff, and run `tests/unit/tools/test_tool_category_artifact.py`. | 1 | The artifact is byte-identical to the base and the test is green — the **F-1 trap class** (a stale committed artifact reddening a whole lane) is avoided because **a skill is not a category**: this lane registers no category. |
| 7 | **Gate G — the manifest delta re-derivation.** Re-derive the agents-tree file count (**366 + 13 = 379**: 9 canary-dir + 3 mirror bodies + 1 standalone manifest) and the discoverable-dir count (**43 + 1 = 44**) from the **actual tree** and compare against the plan's derivation rule. | 1 | The re-derived counts match the rule; if they do not, the reason is identified (an extra or missing file) and the plan's arithmetic is corrected **before** staging — the number is re-derived, never trusted. |
| 8 | **Gate H — scope + privacy.** (a) **Scope:** the **lane-own diff** touches only `agents/**`, `tests/**`, the lane's committed planning set, the **tester lane's pack registration** (`.agents/tester/PACKS.md` — house bookkeeping, exactly like the approver bookkeeping) and the approver bookkeeping — the complement of {`agents/`, `tests/`, `.agents/tester/PACKS.md`, `.agents/approver/`} equals exactly `.agents/shared/planning/canary-injection-defense/`. The merge/bump/tag/giter choreography is **outside** this gate (P6 asserts it). (b) **Privacy legs:** (i) **no committed corpus file contains real credential material** — a secret-shaped string is a lane failure, not a finding; (ii) **no campaign artifact, engagement output or corpus output is committed**; (iii) the campaign store path is **off-repo** (`$HOME/.local/state/aegis/engagements/<id>/`), team-internal, never committed, discovered by **recorded path, never glob**; (iv) `agents/leader/**`, `agents/developer/**` and every persona outside the declared set are byte-identical to the base. | 1–7 | The complement set equals exactly the planning dir; the secret-shape scan is clean **with both controls**; no campaign artifact is committed; the off-repo pattern is the family's; the untouched personas are byte-identical. |
| 9 | **The mutation-evidence record** — the house-format evidence for the lane's mutation legs (*planted change → expected red set → observed red set → restore → green*), over the **minimum set M1–M8** — **the floor is M1–M8, with `M7` (the mirror silent-drift class) MANDATORY** (`architecture-recommendation.md` §5.4's `M1–M7` floor **plus** M8, this lane's discovery-ladder pin; the plan's inventory is `T31–T38` legs and `M1–M8` mutations, and no list says otherwise — B3): M1 delete a canary T29 anchor → exactly that cell reds; M2 delete the directive sentence literal → the fence-contract leg reds; M3 swap `clean`↔`hostile` in a corpus case's expectation → the corpus leg reds; M4 remove a worker doctrine anchor → the T31 cell reds; M5 change one rule id in the table → the id-uniqueness/coverage leg reds; M6 unescape a payload line that begins with the BEGIN token → the round-trip/parser leg reds (`requirements.md` CER-08); **M7 (mandatory) change a mirror's bytes — OUTSIDE the single permitted `auto_load:` frontmatter line — WITHOUT bumping its version → the cross-agent identity pin reds** (the silent-drift class: the W4 guard never re-seeds a same-version edit, so an unpinned drift is permanent); **M8 break the discovery ladder (drop the install-root rung) → T38 reds in exactly the install-root/`/tmp` leg while the repo-root leg still passes** — that single-leg red set proves the pin bites the *ladder*, not the scanner body. | 1–8 | Every mutation leg is **executed**, its expected red set is **predicted before** the plant, and the observed red set **matches the prediction exactly** — including M7's per-roster identity-pin reds and M8's single-CWD red set; the record is written in the house format and attached to the gate evidence. **Mutation legs are the only proof the pins bite** — an unexecuted mutation is not evidence. |

---

## Gates

| Gate | Criterion |
|------|-----------|
| **A** | Net-new deterministic reds **0** vs the true base; the T4/T29 folds are green on **both** legs. |
| **B** | `SEALED_SHA_BASELINES` green with the 4 pre-existing base-stale rows **disclosed** and **not** re-pinned; zero sealed files touched ⇒ no re-pin. |
| **C** | The plane read-holder census is unchanged (`{"watcher"}`). |
| **D** | The report-integrity posture + the prompt-section integrity sweep are green (the predecessor's **two-legged D**): canary skipped by the empty-team exemption under adopted ruling D1-A (or present if D1 granted a spawn right); `aegis`'s guidance untouched; every edited prompt file passes the repo-wide closure sweep. |
| **E** | Boot-scan clean; `canary` resolves; **44** discoverable dirs; the skill loads for every roster member; the **T38 scanner-execution leg is green** (three-CWD byte-identity over the **ruled set — repo root · `/tmp` · INSTALL/RELEASE ROOT** — plus three-run determinism, pass-OFF default, **monotonicity refusal on BOTH axes** — verdict rank **and** confidence rank, `suspect`+`clean`+`high` refused, not merely `hostile`→lower — and ladder resolution from all three); the **six must-not-claim items** (§7.6) are present in phase3's authored text (content-presence pin). |
| **F** | `tool-categories.json` byte-unchanged; the artifact test green. |
| **G** | The agents count **379** (366 + 13 = 9 canary-dir + 3 mirror bodies + 1 standalone manifest) and the discoverable count **44** are re-derived from the real tree. |
| **H** | The lane-own diff touches only the sanctioned paths (incl. the pack registration as bookkeeping); no real credential material in the corpus; no campaign artifact committed; the off-repo store pattern holds; the untouched personas are byte-identical. |
| **M** | The mutation-evidence record is complete and **executed** over the **M1–M8** minimum set (**M7 mandatory** — mirror silent-drift **outside the permitted `auto_load:` line**; M8 — discovery ladder) — every planted change's observed red set matches its predicted red set, in the house format. |

---

## Verification Commands

```bash
cd /home/nea/ensemble-src-wt-canary-injection-defense
BASE=97b76e20e1d640c69b720149547e93565ab22b4c

# --- Gate A: the net-new comparison (FAILED-list diff, not a raw count) ---
#   run the same collectable suite on BASE and on HEAD, capture the FAILED node ids, and diff them
#   expected: the diff is EMPTY (net-new deterministic reds = 0)
#   expected-ledger: test_aegis_team_members_exact + the T29 completeness leg are green on BOTH legs

# --- Gate B: the sealed artifacts ---
python3 -m pytest tests/test_chart_image_delivery_e2e.py::TestPhaseDDoesNotModifySealedArtifacts -q
#   35 entries; 4 are BASE-STALE (ari/workflow.md, ari/rule.md, daemon/sources/registry.py,
#   daemon/constants.py) -> PASS-WITH-DISCLOSURE; do NOT re-pin them (R1 seal lane owns them)
git diff --name-only $BASE HEAD -- daemon/ | wc -l        # expected 0 -> no sealed file touched

# --- Gate C: the plane census ---
python3 -m pytest tests/unit/tools/test_plane_agent_surface_census.py -q
grep -n 'DISCLOSED_READ_HOLDERS' -A3 tests/unit/tools/test_plane_agent_surface_census.py
#   MUST still read {"watcher"} -- canary and the screened personas are NOT added

# --- Gate D: the report-integrity posture ---
python3 -m pytest tests/unit/test_report_integrity_prompts.py -q -rs
#   expect a SKIP for 'canary' (empty team under adopted ruling D1-A) and no new failure

# --- Gate E: the boot scan + the skill loads ---
ls -d agents/*/ | grep -v '/_' | wc -l                      # 44 (43 base + canary)
for a in canary aegis sweeper worker; do
  printf '%-10s ' "$a"; grep -c 'name: injection-screen' agents/$a/skill-set.yaml
done                                                        # each MUST be 1 (the seeded roster -- D2)
for a in canary aegis sweeper worker; do
  test -f "agents/$a/skills-template/injection-screen.md" || echo "MISSING BODY $a"
done                                                        # the seeder's same-dir body requirement

#   AM-1 + B1: the allow sets are PER-AGENT, not one shared leaf set
python3 - <<'PY'
import json
for a in ("hacker", "cracker", "sweeper", "canary"):
    print(a, sorted(json.load(open(f"agents/{a}/meta.json"))["tools"]["allow"]))
PY
#   canary -> ['bash','filesystem','help','time']   (AM-1: no 'self'; NC-4: no 'proc')
#   trio   -> ['bash','filesystem','help','proc','self','time']
grep -c 'CHILD_ALLOW' tests/unit/agents/test_aegis_security_family.py   # 0 -- replaced by ALLOW_BY_AGENT
grep -n 'FAMILY_ROSTER\|INSTALLER_CHILDREN\|FAMILY_CHILDREN\|DOCTRINE_ONLY\|MANIFEST_BEARING\|ALLOW_BY_AGENT\|CARDINAL_COUNT_BY_AGENT' \
     tests/unit/agents/test_aegis_security_family.py
#   all seven names present; FAMILY_ROSTER == 5, INSTALLER_CHILDREN == 3 (T23 arity), FAMILY_CHILDREN == 4,
#   DOCTRINE_ONLY == 2, MANIFEST_BEARING == 4; TONE_HEADINGS carries the canary key
grep -c '^FAMILY = \|^CHILDREN = ' tests/unit/agents/test_aegis_security_family.py   # 0 -- the old bare names are gone

# --- Gate E: the T38 scanner-execution leg (three-CWD) + the must-not-claim presence pin ---
#   THE RULED SET (W8): repo root · /tmp · the INSTALL/RELEASE ROOT. These three labels are
#   identical in phase4 T38, phase5 and phase6 -- do not substitute "agents/" or a "consumer cwd".
REPO_ROOT="$(git rev-parse --show-toplevel)"
INSTALL_ROOT="${ENSEMBLE_INSTALL_ROOT:-/home/nea/agents-ensemble}"   # release-root anchor (see phase6 task 10)
SCAN=agents/canary/skills-template/injection_scan.py
for d in "$REPO_ROOT" /tmp "$INSTALL_ROOT"; do               # repo root; /tmp; INSTALL/RELEASE ROOT
  ( cd "$d" && \
    P="$REPO_ROOT/$SCAN"; [ -f "$P" ] || P="$INSTALL_ROOT/$SCAN"; \
    python3 "$P" <base-input> | sha256sum )
done
#   the cwd-relative rung intentionally MISSES from /tmp and from the install root -- the ladder's
#   install-root rung resolves there, and that resolution is part of assertion (e). The three
#   digests MUST be equal (byte-identical fence + sha256 + verdict blocks); a re-run of any one
#   of them MUST reproduce its digest (three-run determinism); the LLM pass is OFF by default
#   (no --llm-* flag -> heuristic path only); a --llm-verdict LOWER in severity than the
#   heuristic 'hostile' MUST be refused (monotonicity).
grep -l 'logical' agents/canary/rule.md agents/canary/soul.md >/dev/null && \
grep -c 'not confined\|not enforcement\|inert' agents/canary/rule.md agents/canary/soul.md
#   presence-only content pin: the six §7.6 must-not-claim items are present in phase3's authored
#   text (canary rule.md/soul.md + campaign record template + hand-off report -- P3 task 10b).

# --- Gate F: no category drift (a skill is not a category) ---
git diff --name-only $BASE HEAD -- .agents/shared/metrics/tool-categories.json | wc -l   # expected 0
python3 -m pytest tests/unit/tools/test_tool_category_artifact.py -q

# --- Gate G: the manifest delta re-derivation ---
find agents -type f | wc -l                                  # 379 (366 base + 13 new) -- re-derive
find agents -name 'injection-rules.json' | wc -l              # 1 -- the AM-4 data file ships ONCE
find agents -type f | wc -l | awk '{print ($1==379)?"MATCH":"MISMATCH " $1}'
ls -d agents/*/ | grep -v '/_' | wc -l                      # 44

# --- Gate H: scope + privacy ---
#   scope: the complement of the sanctioned paths must be EXACTLY the planning dir
git diff --name-only $BASE HEAD | grep -vE '^(agents/|tests/|\.agents/tester/PACKS\.md$|\.agents/shared/planning/canary-injection-defense/|\.agents/approver/)'
#   expected: EMPTY
#   (PACKS.md is the lane's OWN pack registration -- house bookkeeping exactly like the approver
#    files; a -vE list without it reds the gate on the lane's own registration = false failure.)
#   privacy: the untouched personas are byte-identical
git diff --name-only $BASE HEAD -- agents/leader agents/developer agents/reviewer \
                                    agents/tester agents/architect agents/approver | wc -l   # expected 0
#   privacy: the corpus carries no secret-shaped string (WITH both scanner controls)
#     positive control: a fixture that DOES contain a key-shaped string -> the scanner MUST flag it
#     negative control: the real corpus                       -> the scanner MUST NOT flag it
#   privacy: no campaign artifact is committed -- ANCHORED to the store's PATH SHAPE.
#   A bare word match false-fails: the planning dir's own filenames legitimately contain
#   "campaign"/"payload" (this very plan does), so the predicate is (i) restricted to non-planning
#   paths and (ii) matched against the off-repo store shape, not the words.
git diff --name-only $BASE HEAD \
  | grep -vE '^\.agents/shared/planning/' \
  | grep -E '(^|/)(\.local/state/|engagements/[^/]+/(campaign|findings)/|scratch-root|payload-[0-9a-f]{6,})' \
  | wc -l                                                                          # expected 0
```

---

## Coupling

| Direction | Coupling | Notes |
|---|---|---|
| **in** | **P4** (tight) | P5 verifies P4's commit; the base for the net-new comparison is the lane base, the HEAD is P4's commit. |
| **in** | **P0** | loose — the counts re-derived here are the ones P0 established at base. |
| **out** | **P6** (tight) | P6 stages what P5 verified; P6 repeats the artifact-integrity legs against the **staged** payload. |
| **out** | **P1**, **P2**, **P3** | loose — their content is verified here through the gates, not re-reviewed. |

**Note on the A/B base:** if the merge base moved (the concurrent memoir lane — R11's finalize guard),
the net-new comparison must be re-run against the **actual** base that was merged, and the ledger
must say which base was used. A comparison against a base that is no longer the merge base is not
evidence.
