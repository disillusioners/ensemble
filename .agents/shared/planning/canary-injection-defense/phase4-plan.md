# Phase 4: The One-Commit Pin Ceremony + the T-Pack

## Objective

Land the lane's entire payload in **exactly one commit**: the `canary` tree, the four skill mirrors,
the manifest entries, the six edited persona files, the two `aegis` edits, the regenerated
prompt-artifact baseline (**177 → 181 rows**), the corpus fixtures, **and the shared `aegis` test
file's re-derivation set + the new T31–T38 legs** (the lane edits that file either way) —
with **no** content change under `agents/**` after the baseline re-stamp.

The one-commit rule is house doctrine: agent files and the regenerated baseline ship together, so no
window exists in which the baseline is stale against the tree.

---

## Entry Criteria

- **Phases 1, 2 and 3 have exited** — the canary tree, the mirrors, the manifests and the persona
  edits exist and are final; the guide-compliance checklist is satisfied for every authored file; the
  P1↔P2 wording-synchronisation pass found zero divergences.
- The **fold targets** are known (P0 task 4): in `tests/unit/agents/test_aegis_security_family.py`,
  the T4 team-set assertion (`== {hacker, cracker, sweeper, worker}`) and the T29 legality matrix
  (`_LEGALITY_MATRIX` at `:158`, `LEGALITY_CELLS` at `:194`, the completeness leg `len == 20` at
  `:782`) — **every other leg in that file stays byte-stable.**
- A venv is required for the registry-scan leg (the system `python3` lacks `pydantic`). The
  **baseline re-stamp itself is venv-free** and runs with the bare host `python3`.

**Architect dependency — D5 (test-matrix shape).**
**Adopted ruling (D5 = Option A):** the **family file grows** —
`tests/unit/agents/test_aegis_security_family.py` continues its T-numbers with **T31–T38** (the
ruling's T31–T37 minimum set plus **T38**, the NC-2 scanner-execution leg — fold-cost §11 item 4
assigns the three-CWD test to phase4), because
the lane already has to edit that file for the re-derivation set (T4/T5/T6-T8/T21/T22/T25/T29) and
one corpus of pins avoids two matrices drifting apart. The **cell counts** are binding: **25**
family cells (T29) + **24** doctrine cells (T31) + canary's 2 carve-out anchors; `worker` stays
**outside** the 25-cell matrix (its legacy prose cannot carry a `^\d+\.\s\*\*` anchor without a
re-shape the commission forbids) and its coverage is a separate small pin set. If the architect
prefers a split by concern, the skill-only legs may move to a sibling file — the composition below is
re-derived, but **no leg semantics and no cell count changes**.

---

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | **Extend the family test file** (`tests/unit/agents/test_aegis_security_family.py`, per **D5's Option A** — the lane already edits it for the re-derivation set, so one corpus of pins avoids two matrices drifting apart) with the new legs — pure file parsing wherever possible (no daemon import, no DB, no LLM) so the file runs on bare `python3`; the single exception is the registry-scan leg, which is venv-bound. It assembles the legs authored in P1 (fence contract), P2 (doctrine anchors) and P3 (canary meta + campaign shape) into one file, under the **house leg names** (each leg carries a `# T3x` comment so the pack registry can cite it — `requirements.md` CER-03): **T31** the doctrine-anchor matrix (**6 personas × 4 anchors = 24 cells** + canary's **2 carve-out anchors**); **T32** the fence-contract leg (structure, tokens, the directive literal, header **order** + closed vocabularies); **T33** the parser leg (region-awareness with the `verdict: clean`-in-payload fixture; `lines`/`escaped` integrity; forged END rejection; unknown version refusal); **T34** the heuristic corpus leg (every case's expected verdict/confidence/rules, **LLM off**); **T35** the canary meta pins (leaf, allow/deny exact, no privileged, no `mcp_full_access`, no `chart`, the tools-block round-trip); **T36** the campaign-log + report shape leg; **T37** the corpus hygiene leg (label present, secret scan **with controls**, coverage counts); **T38** the scanner-execution leg (the P1 task-5b three-CWD test assembled into the same file — inventory below). Plus the **baseline assertions** (the file **exists** and has **exactly 181 rows**; canary contributes exactly 4), the **mirror/manifest legs** (the roster set is exact; every copy byte-identical to the canonical copy; every receiving manifest has exactly one entry; **no other agent's manifest mentions it**; `include:` ∉ frontmatter; **all roster versions equal**), and the **boot-scan leg** (venv-bound; `canary` resolves in the registry). | P1, P2, P3 | The file is green; **every leg name in the T31–T38 inventory exists**; each leg is **mutation-meaningful** — each named mutation (break the delimiter, drop a header field, change the enum, delete a fixture, delete a doctrine anchor, drift a mirror, add a stray manifest entry, bump a cardinal, swap the team set, paste payload text unfenced into a report) turns **exactly** the intended leg red and restoring turns it green. |
| 2 | **Fold the shared pins in `tests/unit/agents/test_aegis_security_family.py`** — the **re-derivation set** (this lane's shared pins are not all byte-stable; each is re-derived and the reason is written down): (a) **T4**: the team-set assertion becomes the 5-set `{hacker, cracker, sweeper, worker, canary}`; (b) **T5**: the leaf tool-set parametrisation now **includes canary** (a set extension under D1-A — a class split only under D1-B/C); (c) **T6/T7/T8**: canary's allow/deny/privileged intersection enters the leaf pins; (d) **T21**: the family-manifest list is re-derived as *the family manifests are exactly the seeded set* (it reds if the skill is seeded beyond aegis); (e) **T22**: generalised from aegis-only to **every seeded (agent, name) pair**; (f) **T25**: worker byte-unchanged **reds by design this lane** — re-worded against **this lane's own base**, its intent restated as "worker touched by design"; (g) **T29**: add the **canary row** to `_LEGALITY_MATRIX` — one cell per family (5) with **single-site** anchors — and move the completeness leg from `len == 20` to `len == 25`; (h) the `LEGALITY_CELLS` derivation follows automatically (it is `sorted(_LEGALITY_MATRIX.keys())`). **Every leg outside this set stays byte-identical.** | 1 | The shared file's diff is **exactly** the re-derivation set; the predecessor's other legs are untouched; the matrix is 5 personas × 5 families = **25** cells; each fold carries a one-line reason in the file. |
| 3 | **Extend the boot-scan census expectation** — the discoverable-agent count moves 43 → **44**
   (canary is the 44th; the 4 `_`-prefixed directories remain skipped). | 1 | The census expectation matches the re-derived count; the leg is not hard-coded to a stale number. |
| 4 | **Write the corpus fixtures** — the synthetic seed corpus under `tests/`: one positive fixture
   per rule ID + a benign control per family. Every fixture is **invented**; no real credential
   material, no real user data, no real repo content. | 1 | The corpus covers every rule ID and carries benign controls; a secret-shape scan over it is clean **with both a positive and a negative control** (a control-less scanner is vacuous, not clean). |
| 5 | **Re-stamp the baseline** — run the generator (venv-free) so `agents/.prompt-artifacts.sha256`
   moves **177 → 181 rows**. This is the **LAST content change under `agents/**`** in the lane. | 1–4 | `--check` exits 0 **AND** the file **exists** with **exactly 181 rows** — the exit code alone proves nothing (the checker exits 0 when the file is absent). The row count is re-derived from the tree, not asserted from this plan's number. |
| 6 | **Register the lane's packs** in `.agents/tester/PACKS.md` (house format) so the pack registry can cite the **T31–T38** leg names; the pack entries declare the scope, the leg names and the derived blast-radius composition. This registration travels **in the one commit** — it is the lane's own declaration of its packs, exactly as the baseline re-stamp travels with the payload. | 1–5 | The registrations name the **T31–T38** legs; the file's merge behaviour is **keep-both, newest-first** (the memoir lane may register in the same file). |
| 7 | **Assemble the ONE commit** — the composition below, in one commit, with the baseline re-stamp in it (house precedent: the baseline re-stamp ships **in the same commit** as the test file). | 5, 6 | `git show --stat HEAD` shows exactly the enumerated set and nothing else. |
| 8 | **Prove the composition** — `git show --stat HEAD`; confirm the re-stamp was the last `agents/**` content change (no later edit); confirm the working tree is clean afterwards. | 7 | The stat matches the enumeration; the tree is clean; a fresh `--check` is still green. |
| 9 | **Cross-check the derivations** — re-derive the agents-tree count (**366 + 12 = 378**) and the discoverable count (**43 + 1 = 44**) from the **actual tree**, not from this plan's arithmetic. | 7 | The re-derived counts match the rule; a mismatch names its own cause (an extra or missing file) and the plan's arithmetic is corrected **before** staging — the number is re-derived, never trusted. |
| 10 | **Record the payload-vs-plan delta** — list any file the implementation added that the composition did not anticipate (and why), and any file it did not add. | 7–9 | The delta is written into the lane record; a silent delta is a gate failure, not a note. |

---

## The one-commit composition (binding — verbatim)

```
agents/canary/{meta.json, soul.md, rule.md, workflow.md, tools_note.md, skill-set.yaml,
               skills-template/injection-screen.md,
               skills-template/injection_scan.py}                                  (+8 NEW)
agents/aegis/skills-template/injection-screen.md                                    (+1 NEW)
agents/aegis/skill-set.yaml                                     (MODIFIED — one entry added)
agents/worker/skills-template/injection-screen.md                                    (+1 NEW)
agents/worker/skill-set.yaml                                     (MODIFIED — one entry added)
agents/sweeper/{skill-set.yaml, skills-template/injection-screen.md}               (+2 NEW)
agents/aegis/meta.json                                                            (MODIFIED — one line)
agents/aegis/rule.md                                          (MODIFIED — guideline (g) added)
agents/aegis/workflow.md                                      (MODIFIED — one section appended)
agents/hacker/rule.md                                         (MODIFIED — guideline (f) added)
agents/cracker/rule.md                                        (MODIFIED — guideline (f) added)
agents/sweeper/rule.md                                        (MODIFIED — guideline (f) added)
agents/worker/rule.md                                         (MODIFIED — doctrine block added)
agents/.prompt-artifacts.sha256                                 (REGENERATED 177 → 181)
tests/unit/agents/test_aegis_security_family.py         (MODIFIED — the re-derivation set + T31–T38)
tests/fixtures/injection_corpus/                               (NEW — synthetic seed corpus)
.agents/tester/PACKS.md                                        (MODIFIED — the lane's pack registration)
```

> **NC-2 fold correction:** the pre-fold block above erroneously listed `hacker`/`cracker` mirror
> pairs (+2 NEW each) and omitted the `aegis` mirror and the `aegis/skill-set.yaml` entry. D2's
> recipient table is authoritative: `hacker`/`cracker` are **doctrine only** (no mirror, no
> manifest), `aegis` gets a mirror with a **second entry in its existing manifest**, `worker` a
> mirror with a **first entry in its existing empty manifest**, and the NEW set under `agents/` is
> exactly the **12 files enumerated in the File-arithmetic paragraph below** (verified against the
> base tree: `agents/aegis/skill-set.yaml` and `agents/worker/skill-set.yaml` exist at base;
> `agents/{hacker,cracker,sweeper}/skill-set.yaml` do not; `agents/canary/` does not exist).

**File arithmetic:** `agents/` **+12 new** — enumerated by path so **366 + 12 = 378** is checkable by
a reader: (1) `agents/canary/meta.json` · (2) `agents/canary/soul.md` · (3) `agents/canary/rule.md` ·
(4) `agents/canary/workflow.md` · (5) `agents/canary/tools_note.md` · (6) `agents/canary/skill-set.yaml` ·
(7) `agents/canary/skills-template/injection-screen.md` · (8) `agents/canary/skills-template/injection_scan.py` ·
(9) `agents/aegis/skills-template/injection-screen.md` · (10) `agents/worker/skills-template/injection-screen.md` ·
(11) `agents/sweeper/skills-template/injection-screen.md` · (12) `agents/sweeper/skill-set.yaml` —
i.e. the **8 canary-dir files** (incl. `injection_scan.py` and canary's own new manifest) + the
**3 mirror bodies** (aegis, worker, sweeper) + the **1 standalone new manifest** (sweeper's;
canary's manifest is inside its 8). **10 modified** (2 aegis edits + aegis/rule + 3 child rules +
worker/rule + 2 `skill-set.yaml`s + the regenerated baseline) ⇒ **366 + 12 = 378** agents files at HEAD. **`hacker` and
`cracker` get NO skill copy and NO manifest** under D2's default — their coverage is the doctrine
layer only, and the gap is disclosed.

**Baseline arithmetic:** the collector is depth-2 and covers `soul/rule/workflow/tools_note` (+ the
legacy `tools.md`/`skill.md`/`memory.md`/`growth.md`) ⇒ canary contributes **exactly 4 rows** ⇒
**177 → 181**. `meta.json`, `skill-set.yaml`, `skills-template/*` and the corpus fixtures contribute
**zero**; the eight modified files change **content hashes, not row counts**. **Any** of
`memory.md`, `growth.md`, `tools.md` or `skill.md` on canary would add a row and move the number ⇒
canary gets exactly four prompt files.

> **Baseline-check nuance:** the generator's `--check` exits **0 both when clean and when the
> baseline file is ABSENT** ⇒ every baseline leg (P4 / P5 / stage) asserts the **file exists** *and*
> has **exactly 181 rows** — `rc=0` alone proves nothing.

---

## Re-derivation ledger (D5, binding — never a silent copy)

| Pin | Location | Today | Must become | Why it reddens |
|---|---|---|---|---|
| T4 team set | `test_aegis_security_family.py:326-327` | 4-member set | 5-member set (+`canary`) | aegis roster one-liner |
| T5 leaf parametrisation | `:338-345` | 3 children | + canary | new leaf persona |
| T6/T7/T8 allow/deny/privileged | `:348-369` | 3 children | + canary | new persona pins |
| T21 family manifests | `:556-567` | `["aegis"]` exactly | **declared seeded roster** (aegis, worker, sweeper, canary) | skill seeded into 4 agents |
| T22 version parity | `:585-597` | aegis-only | every seeded `(agent, name)` pair | 4 mirrors |
| T25 worker byte-identity | `:684-691` | `git diff … agents/worker/ == ""` | **additive-scope**: one file, zero deletions, hunk confined to the new CRITICAL subsection | worker is edited **by design** |
| T29 matrix | `:759-812` | len 20, agents = FAMILY | len 25, agents include canary, 5 new single-site cells | canary column |
| **Baseline arithmetic** | `test_aegis_security_family.py:2490-2512` | `EXPECTED_BASELINE_ROWS = 177`; 4 rows/new agent | **181**; canary contributes exactly 4 | canary's 4 prompt artifacts |
| Leader roster | `test_maintenancer_agent.py:517-523` | 17 members | **unchanged** — canary is an aegis child | verified: no second leader one-liner |

**Cell arithmetic (binding):** T29 20 → **25** (5 families × 5 personas: aegis, hacker, cracker, sweeper, canary); completeness `len == 25`; `agents` set includes `canary`; each canary anchor **single-site** on a `^\d+\.\s\*\*` line. T31: 6 personas × 4 anchors = **24** anchor assertions + canary's 2 carve-out anchors = **26**. For the five cardinal personas the self-proof asserts *exactly once* and the numbered-line property. **For worker the numbered-line property is NOT asserted** (legacy `## Must` prose): its anchors must occur exactly once, each on a single line, inside the declared CRITICAL subsection.

**New pins (T31–T38):** T31 doctrine-anchor matrix · T32 fence-contract leg · T33 parser leg · T34 heuristic corpus leg (LLM off) · T35 canary meta pins · T36 campaign-log + report shape · T37 corpus hygiene · **T38 scanner-execution leg** — runs the committed `agents/canary/skills-template/injection_scan.py` from **three working directories** (repo root; `agents/`; the consumer's own cwd — the cwd class the staged gate's `/tmp` and install-root legs represent) and asserts **(a)** the three runs are **byte-identical** (fence + sha256 + verdict blocks); **(b)** bare `python3` works — **no daemon import, no venv dependency, no network, no writes outside the scratch root**; **(c)** the **LLM pass is OFF by default**; **(d)** a `--llm-verdict` **lower in severity than the heuristic `hostile` is REFUSED** (monotonicity, §3.6); **(e)** the **discovery ladder resolves from each consumer's documented location**. Each re-derivation is recorded with a written reason; a blind copy of the predecessor's pin set is a lane-blocking red.

**Mutation legs (minimum set, each observed RED→GREEN):** M1 delete a canary T29 anchor → exactly that cell reds · M2 delete the directive literal → T32 reds · M3 swap a corpus expectation → T34 reds · M4 remove a worker doctrine anchor → T31 reds · M5 change one rule ID → id-uniqueness/coverage reds · M6 unescape a payload line starting with the BEGIN token → round-trip/parser reds · **M7 change a mirror's bytes without bumping its version → identity pin reds** (the silent-drift class) · **M8 break the discovery ladder (drop the install-root rung) → T38 reds in exactly one cwd** — the cwd-relative rung still passes from the repo root, so the observed red set is exactly the consumer-cwd leg; that single-cwd red set is the point: it proves the pin bites the *ladder* and not the scanner body.

**Census / artifacts:** boot census **43 → 44** (staged-gate artifact; no unit pin asserts a total count). Agents-tree files: **366 → 378** (the 12 new files enumerated in the File-arithmetic paragraph: canary's 8 — canary's new manifest among them — + 3 mirror bodies + sweeper's standalone manifest) — **re-derive at P4**, never freeze. Baseline rows **177 → 181**, re-stamped LAST. `tool-categories.json` and the seal map untouched.

**Derivation arithmetic must be shown**, and P4 must RE-DERIVE at ceremony time.

## Gates

| Gate | Criterion |
|------|-----------|
| **G4.1** | `--check` exits 0 **AND** the baseline file exists with **exactly 181 rows** (both asserted). |
| **G4.2** | `git show --stat HEAD` shows exactly the enumerated composition — **and nothing outside it**. |
| **G4.3** | The re-stamp was the **last** content change under `agents/**` in the lane (no later prompt edit). |
| **G4.4** | The shared test file's diff is **exactly** the T4 set change + the T29 canary row + the completeness count — every other leg byte-stable. |
| **G4.5** | Every leg in the extended family test file is **mutation-meaningful** (the named mutation reds exactly the intended leg), and the **T31–T38** leg inventory is complete with the `# T3x` markers present. |
| **G4.5b** | The pack registration names the **T31–T38** legs; the mutation evidence is written in the house format *planted change → expected red set → observed red set → restore → green* (the evidence itself is the tester lane's P5 deliverable — `requirements.md` CER-08). |
| **G4.6** | The re-derived agents count (**378**) and discoverable count (**44**) match the derivation rule. |
| **G4.7** | The corpus contains no secret-shaped string (with both scanner controls), and every fixture is synthetic. |

---

## Verification Commands

```bash
cd /home/nea/ensemble-src-wt-canary-injection-defense

# --- DERIVATION 1: the one-commit composition ---
git show --stat HEAD
#   MUST be exactly: 12 new agents/ files + 9 modified agents/ files + the regenerated baseline
#                     + the shared test file's re-derivation set + the new T31-T38 legs + the corpus.

# --- DERIVATION 2: the baseline (existence AND count -- rc alone proves nothing) ---
test -f agents/.prompt-artifacts.sha256 && echo "file EXISTS" || echo "file MISSING"
wc -l < agents/.prompt-artifacts.sha256
#   177 (base) + 4 (canary soul/rule/workflow/tools_note) = 181 expected
python3 scripts/protection/generate_prompt_artifact_baseline.py --check; echo "rc=$?"   # rc=0

# --- DERIVATION 3: the agents-tree count (366 + 12) ---
find agents -type f | wc -l
#   base 366
#   + canary tree                +8   (meta, soul, rule, workflow, tools_note, skill-set.yaml,
#                                      injection-screen.md, injection_scan.py)
#   + aegis mirror               +1
#   + worker mirror              +1
#   + sweeper mirror             +1
#   + sweeper manifest           +1   (agents/sweeper/skill-set.yaml is NEW at base)
#   (hacker/cracker: +0 — doctrine only, no mirror, no manifest)
#   = 378 expected at HEAD   (re-derive from the real list; never trust this sentence)

# --- DERIVATION 4: the discoverable-agent census (43 + 1) ---
ls -d agents/*/ | grep -v '/_' | wc -l          # 44 expected
ls -d agents/*/ | wc -l                          # 48 on disk (47 base + canary); 4 '_'-prefixed skipped

# --- DERIVATION 5: the shared-pin folds (and ONLY those) ---
git diff HEAD~1 -- tests/unit/agents/test_aegis_security_family.py
#   expected: the T4 set literal, the canary row in _LEGALITY_MATRIX, and len == 20 -> len == 25
grep -n 'test_aegis_team_members_exact' -A2 tests/unit/agents/test_aegis_security_family.py
grep -n '== 25\|== 20' tests/unit/agents/test_aegis_security_family.py

# --- DERIVATION 6: the re-stamp was the LAST agents/** content change ---
git log --oneline -1 -- agents/.prompt-artifacts.sha256     # the same commit as the payload
git diff --name-only HEAD~1 HEAD -- agents/                 # the payload files only

# --- DERIVATION 7: the corpus ---
find tests/fixtures/injection_corpus -type f | wc -l          # re-derive; one positive per rule ID
#                                                    + benign controls

# --- DERIVATION 8: the mirror + manifest invariants (the drift pin) ---
CANON=agents/canary/skills-template/injection-screen.md
for a in aegis sweeper worker; do cmp -s "$CANON" "agents/$a/skills-template/injection-screen.md" \
  && echo "OK $a" || echo "DRIFT $a"; done             # the 3 mirrors (hacker/cracker: none -- D2 doctrine only)
grep -l 'injection-screen' agents/*/skill-set.yaml | wc -l    # 4 (canary + aegis + worker + sweeper)
grep -h 'version:' agents/*/skill-set.yaml | sort -u | wc -l # 1 (all equal -- the propagation lever)
```

---

## Coupling

| Direction | Coupling | Notes |
|---|---|---|
| **in** | **P1** (tight) | the fence-contract legs + the mirror/manifest invariants + the canonical body. |
| **in** | **P2** (tight) | the doctrine-pin legs + the six edited persona files. |
| **in** | **P3** (tight) | the canary tree + the two aegis edits + the canary-specific legs. |
| **out** | **P5** (tight) | P5 verifies P4's commit — the base for the net-new-red comparison is the base commit, and the HEAD is this commit. |
| **out** | **P6** (tight) | P6 stages P4's commit (merged). |
| **out** | **P0** | loose — the counts asserted here are the ones P0 re-derived at base. |
