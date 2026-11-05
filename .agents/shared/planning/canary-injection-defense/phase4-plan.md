# Phase 4: The One-Commit Pin Ceremony + the T-Pack

## Objective

Land the lane's entire payload in **exactly one commit**: the `canary` tree, the **three skill
mirrors (+ the canonical body)**,
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
ruling's set **T31–T38** — the ruling's `T31–T37` inventory **plus T38**, the NC-2
scanner-execution leg (fold-cost §11 item 4 assigns the three-CWD test to phase4). The lane's pin
inventory is **T31–T38, eight legs**, in every list that enumerates it (B3), because
the lane already has to edit that file for the re-derivation set (T4/T5/T6-T8/T21/T22/T25/T29) and
one corpus of pins avoids two matrices drifting apart. The **cell counts** are binding: **25**
family cells (T29) + **24** doctrine cells (T31) + canary's 2 carve-out anchors; `worker` stays
**outside** the 25-cell matrix (its legacy prose cannot carry a `^\d+\.\s\*\*` anchor without a
re-shape the commission forbids) and its coverage is a separate small pin set. The composition below is
re-derived, but **no leg semantics and no cell count changes**.

---

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | **Extend the family test file** (`tests/unit/agents/test_aegis_security_family.py`, per **D5's Option A** — the lane already edits it for the re-derivation set, so one corpus of pins avoids two matrices drifting apart) with the new legs — pure file parsing wherever possible (no daemon import, no DB, no LLM) so the file runs on bare `python3`; the single exception is the registry-scan leg, which is venv-bound. It assembles the legs authored in P1 (fence contract), P2 (doctrine anchors) and P3 (canary meta + campaign shape) into one file, under the **house leg names** (each leg carries a `# T3x` comment so the pack registry can cite it — `requirements.md` CER-03): **T31** the doctrine-anchor matrix (**6 personas × 4 anchors = 24 cells** + canary's **2 carve-out anchors**); **T32** the fence-contract leg (structure, tokens, the directive literal, header **order** + closed vocabularies); **T33** the parser leg (region-awareness with the `verdict: clean`-in-payload fixture; `lines`/`escaped` integrity; forged END rejection; unknown version refusal); **T34** the heuristic corpus leg (every case's expected verdict/confidence/rules, **LLM off**); **T35** the canary meta pins (leaf, allow/deny **exact per-agent** — canary `{bash, filesystem, time, help}` under **AM-1**, and each leaf-trio agent asserted individually via `ALLOW_BY_AGENT`; no privileged, no `mcp_full_access`, no `chart`, the tools-block round-trip); **T36** the campaign-log + report shape leg (the log carries `expected_action_class`, `inertness_attested_by: aegis`, `attested_at` **and `scratch_root_removed_at`**, and the teardown is exercised on the **abort** path as well as the happy path — B8); **T37** the corpus hygiene leg (label present, secret scan **with controls**, coverage counts, **and the AM-4 budgets** — every body ≤ 8,000 chars HARD; the data file ≤ 18 rules / ≤ 8,000 chars / ≤ 120 lines); **T38** the scanner-execution leg (the P1 task-5b three-CWD test assembled into the same file — inventory below). Plus the **baseline assertions** (the file **exists** and has **exactly 181 rows**; canary contributes exactly 4), the **mirror/manifest legs** (the roster set is exact; every copy byte-identical to the canonical copy **once the single permitted `auto_load:` line is filtered out** — AM-2, never a raw `cmp`; every receiving manifest has exactly one entry; **no other agent's manifest mentions it**; `include:` ∉ frontmatter; **all roster versions equal**), and the **boot-scan leg** (venv-bound; `canary` resolves in the registry). | P1, P2, P3 | The file is green; **every leg name in the T31–T38 inventory exists**; each leg is **mutation-meaningful** — each named mutation (break the delimiter, drop a header field, change the enum, delete a fixture, delete a doctrine anchor, drift a mirror, add a stray manifest entry, bump a cardinal, swap the team set, paste payload text unfenced into a report) turns **exactly** the intended leg red and restoring turns it green. |
| 2 | **Fold the shared pins in `tests/unit/agents/test_aegis_security_family.py`** — the **re-derivation set** (this lane's shared pins are not all byte-stable; each is re-derived and the reason is written down): (a) **T4**: the team-set assertion becomes the 5-set `{hacker, cracker, sweeper, worker, canary}`; (b) **T5**: the leaf tool-set parametrisation now **includes canary** (a set extension under D1-A — a class split only under D1-B/C); (c) **T6/T7/T8**: canary's allow/deny/privileged intersection enters the leaf pins; (d) **T21**: the family-manifest list is re-derived as *the family manifests are exactly the seeded set* (it reds if the skill is seeded beyond aegis); (e) **T22**: generalised from aegis-only to **every seeded (agent, name) pair**; (f) **T25**: worker byte-unchanged **reds by design this lane** — re-worded against **this lane's own base**, its intent restated as "worker touched by design"; (g) **T29**: add the **canary row** to `_LEGALITY_MATRIX` — one cell per family (5) with **single-site** anchors — and move the completeness leg from `len == 20` to `len == 25`; (h) the `LEGALITY_CELLS` derivation follows automatically (it is `sorted(_LEGALITY_MATRIX.keys())`); **(i) the FOUR-constant set (B1)** — `CHILDREN` (`:76`) is **overloaded** (it keys `INSTALLER_SKILLS`, drives T23's hardcoded 3-arity, and parametrizes 25 legs), `FAMILY` (`:75`) is the **persona roster**, `CHILD_ALLOW` (`:89`) is **one shared 6-set**, and `TONE_HEADINGS` (`:131`) has **4 keys**; all four are replaced exactly as §"The four-constant ledger (B1, binding — as written)" specifies — `FAMILY_ROSTER` (5) / `INSTALLER_CHILDREN` (3, **unchanged**) + `FAMILY_CHILDREN` (4) + `DOCTRINE_ONLY` (2) + `MANIFEST_BEARING` (4) / `ALLOW_BY_AGENT` / `TONE_HEADINGS` (5) / `CARDINAL_COUNT_BY_AGENT`, plus the three inline roster literals (`_REGISTRY_PROBE` `:721`, T4 `:326-327`, and the **second** aegis team literal `:3405-3408`). Adding `canary` to `CHILDREN` without the split is a `KeyError` on every `INSTALLER_SKILLS` leg and arity-breaks T23; "repairing" `CHILD_ALLOW` by widening it re-installs `proc` (NC-4) and `self` (AM-1). **`LEAVES` does not exist** in the file and is not introduced. **Every leg outside this set stays byte-stable.** | 1 | The shared file's diff is **exactly** the re-derivation set; the predecessor's other legs are untouched; the matrix is 5 personas × 5 families = **25** cells; each fold carries a one-line reason in the file. |
| 3 | **Extend the boot-scan census expectation** — the discoverable-agent count moves 43 → **44**
   (canary is the 44th; the 4 `_`-prefixed directories remain skipped). | 1 | The census expectation matches the re-derived count; the leg is not hard-coded to a stale number. |
| 4 | **Land + VERIFY the corpus (SINGLE authoring site — P1 task 4 authors it; this task never re-authors)** — the synthetic seed corpus at **`tests/fixtures/injection_corpus/`** (the D4 adopted ruling's bound home): one positive fixture
   per rule ID + a benign control per family. This task lands P1's set in the one commit and verifies
   coverage, path containment and the secret-shape scan; it writes **no** corpus content of its own
   (two authorship sites drift the fixtures away from the registry P1 pinned). | 1 | The landed corpus is **byte-identical** to P1's authored set; the corpus covers every rule ID and carries benign controls; a secret-shape scan over it is clean **with both a positive and a negative control** (a control-less scanner is vacuous, not clean); every fixture path is under `tests/fixtures/injection_corpus/`; **no corpus content originates in P4**. |
| 5 | **Re-stamp the baseline** — run the generator (venv-free) so `agents/.prompt-artifacts.sha256`
   moves **177 → 181 rows**. This is the **LAST content change under `agents/**`** in the lane. | 1–4 | `--check` exits 0 **AND** the file **exists** with **exactly 181 rows** — the exit code alone proves nothing (the checker exits 0 when the file is absent). The row count is re-derived from the tree, not asserted from this plan's number. |
| 6 | **Register the lane's packs** in `.agents/tester/PACKS.md` (house format) so the pack registry can cite the **T31–T38** leg names; the pack entries declare the scope, the leg names and the derived blast-radius composition. This registration travels **in the one commit** — it is the lane's own declaration of its packs, exactly as the baseline re-stamp travels with the payload. | 1–5 | The registrations name the **T31–T38** legs; the file's merge behaviour is **keep-both, newest-first** (the memoir lane may register in the same file). |
| 7 | **Assemble the ONE commit** — the composition below, in one commit, with the baseline re-stamp in it (house precedent: the baseline re-stamp ships **in the same commit** as the test file). | 5, 6 | `git show --stat HEAD` shows exactly the enumerated set and nothing else. |
| 8 | **Prove the composition** — `git show --stat HEAD`; confirm the re-stamp was the last `agents/**` content change (no later edit); confirm the working tree is clean afterwards. | 7 | The stat matches the enumeration; the tree is clean; a fresh `--check` is still green. |
| 9 | **Cross-check the derivations** — re-derive the agents-tree count (**366 + 13 = 379**: 9 canary-dir + 3 mirror bodies + 1 standalone manifest) and the discoverable count (**43 + 1 = 44**) from the **actual tree**, not from this plan's arithmetic. | 7 | The re-derived counts match the rule; a mismatch names its own cause (an extra or missing file) and the plan's arithmetic is corrected **before** staging — the number is re-derived, never trusted. |
| 10 | **Record the payload-vs-plan delta** — list any file the implementation added that the composition did not anticipate (and why), and any file it did not add. | 7–9 | The delta is written into the lane record; a silent delta is a gate failure, not a note. |

---

## The one-commit composition (binding — verbatim)

**Stated once (B4).** This block is the **single canonical enumeration** of the lane commit;
`plan-overview.md` §The one-commit pin ceremony quotes it **byte-identically** (one list, not two).
`.agents/tester/PACKS.md` is **inside** the set: pack registration is house bookkeeping and the
commit gate cites the **T31–T38** registrations from it, so forbidding it would forbid a file the gate
depends on.

```
agents/canary/{meta.json, soul.md, rule.md, workflow.md, tools_note.md, skill-set.yaml,
               skills-template/injection-screen.md,
               skills-template/injection_scan.py,
               skills-template/injection-rules.json}                               (+9 NEW)
agents/aegis/skills-template/injection-screen.md                                    (+1 NEW)
agents/aegis/skill-set.yaml                                     (MODIFIED — one entry added)
agents/worker/skills-template/injection-screen.md                                   (+1 NEW)
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

> **NC-2 fold correction (historical — an earlier DRAFT of this block):** that draft erroneously listed `hacker`/`cracker` mirror
> pairs (+2 NEW each) and omitted the `aegis` mirror and the `aegis/skill-set.yaml` entry. D2's
> recipient table is authoritative: `hacker`/`cracker` are **doctrine only** (no mirror, no
> manifest), `aegis` gets a mirror with a **second entry in its existing manifest**, `worker` a
> mirror with a **first entry in its existing empty manifest**, and the NEW set under `agents/` is
> exactly the **12 files enumerated in the File-arithmetic paragraph below** (verified against the
> base tree: `agents/aegis/skill-set.yaml` and `agents/worker/skill-set.yaml` exist at base;
> `agents/{hacker,cracker,sweeper}/skill-set.yaml` do not; `agents/canary/` does not exist).

**File arithmetic (re-summed under the AM-4 shape — B9).** The earlier `8 canary + 3 mirrors + 2
manifests = 13 ⇒ 379` was **wrong twice**: canary's own `skill-set.yaml` was already inside its 8
(counted again as a "new manifest"), and **AM-4 adds a ninth canary file**, the rule data file
`skills-template/injection-rules.json`. The correct decomposition of the **13 new** files:

| Part | Count | Members |
|---|---|---|
| **canary-dir files** | **9** | `meta.json` · `soul.md` · `rule.md` · `workflow.md` · `tools_note.md` · `skill-set.yaml` · `skills-template/injection-screen.md` (canonical body) · `skills-template/injection_scan.py` (scanner) · `skills-template/injection-rules.json` (**AM-4 data file**) |
| **mirror bodies** | **3** | `agents/aegis/skills-template/injection-screen.md` · `agents/worker/skills-template/injection-screen.md` · `agents/sweeper/skills-template/injection-screen.md` |
| **standalone new manifest** | **1** | `agents/sweeper/skill-set.yaml` (canary's manifest is inside its 9; `aegis` and `worker` already have one) |
| **TOTAL NEW** | **13** | **366 + 13 = 379** agents files at HEAD |

**Modified in place: 9** (2 `aegis` edits — `meta.json` one line + `workflow.md` one appended section —
+ `aegis/rule.md` + `hacker/rule.md` + `cracker/rule.md` + `sweeper/rule.md` + `worker/rule.md` +
`aegis/skill-set.yaml` + `worker/skill-set.yaml`), plus the regenerated baseline as a **10th**
modified `agents/` entry. **`hacker` and `cracker` get NO skill copy, NO manifest and NO data file**
under D2's default — their coverage is the doctrine layer only, and the gap is disclosed. **Every
gate that states the file total reads the SAME derived number: `379`** (P4 task 9, G4.6, P5 Gate G,
P6 task 10 / G6.1b) — a gate that carries a different total is red.

> **Cross-file confirmation.** `architecture-recommendation.md` §5.5 now **defers the decomposition to
> this plan's parts→total table** (9 canary-dir + 3 mirror bodies + 1 standalone manifest = 13 ⇒
> 366 + 13 = **379**; measured: a `grep` for the pre-AM-4 decomposition string in that file → exit 1) — the record no
> longer restates a decomposition, so there is **no divergence to report**.

**Baseline arithmetic:** the collector is depth-2 and covers `soul/rule/workflow/tools_note` (+ the
legacy `tools.md`/`skill.md`/`memory.md`/`growth.md`) ⇒ canary contributes **exactly 4 rows** ⇒
**177 → 181**. `meta.json`, `skill-set.yaml`, `skills-template/*` and the corpus fixtures contribute
**zero**; the **nine** modified agent files change **content hashes, not row counts** (the regenerated baseline is the **tenth** modified `agents/` entry and adds no row). **Any** of
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
| T25 worker byte-identity | `:684-691` | `git diff … agents/worker/ == ""` | additive-scope: `rule.md` zero deletions, hunk confined to the new CRITICAL subsection; `skill-set.yaml` (one added entry) + the new `skills-template/injection-screen.md` explicitly allowed (DOC-10: additivity, not one-file-ness). | worker is edited **by design** |
| T29 matrix | `:759-812` | len 20, agents = FAMILY | len 25, agents include canary, 5 new single-site cells | canary column |
| **Baseline arithmetic** | `test_aegis_security_family.py:2490-2512` | `EXPECTED_BASELINE_ROWS = 177`; 4 rows/new agent | **181**; canary contributes exactly 4 | canary's 4 prompt artifacts |
| Leader roster | `test_maintenancer_agent.py:517-523` | 17 members | **unchanged** — canary is an aegis child | verified: no second leader one-liner |
| **Constant set (B1 — FOUR constants, not three)** | `:75` (`FAMILY`), `:76` (`CHILDREN`), `:89` (`CHILD_ALLOW`), `:131` (`TONE_HEADINGS`); plus the inline roster literals at `:721` (`_REGISTRY_PROBE`), `:326-327` (T4) and `:3405-3408` (the second aegis team literal) | `FAMILY` = 4 personas · `CHILDREN` = 3 installer children · `CHILD_ALLOW` = one shared 6-set · `TONE_HEADINGS` = 4 keys | **`FAMILY_ROSTER`** = 5 (+`canary`) · **`INSTALLER_CHILDREN`** = 3 (**unchanged**) + **`FAMILY_CHILDREN`** = 4 + **`DOCTRINE_ONLY`** = 2 + **`MANIFEST_BEARING`** = 4 · **`ALLOW_BY_AGENT`** = per-agent sets · **`TONE_HEADINGS`** = 5 keys · **`CARDINAL_COUNT_BY_AGENT`** = per-agent map | §"The four-constant ledger" below — every affected leg is enumerated there with its fix; `LEAVES` **does not exist in the file** and is not introduced |

### The four-constant ledger (B1, binding — as written)

The shared test file has **four** constants that must change, plus **two inline roster literals** that
are not constants at all — and **one symbol the previous ledger invented**. The approving reader can
check every row below against `tests/unit/agents/test_aegis_security_family.py` at the base commit.

#### 1. The constant-set table (old → new)

| # | Constant (base line) | Today's value | New symbol(s) + value | Consuming legs (exact) | Why it must change |
|---|---|---|---|---|---|
| **C1** | `CHILDREN` (`:76`) | `("hacker","cracker","sweeper")` — **three installer children** | **`INSTALLER_CHILDREN = ("hacker","cracker","sweeper")`** (member-for-member **unchanged**) **+ `FAMILY_CHILDREN = ("hacker","cracker","sweeper","canary")`** **+ `DOCTRINE_ONLY = ("hacker","cracker")`** **+ `MANIFEST_BEARING = ("aegis","canary","sweeper","worker")`** | 25 `parametrize("agent", CHILDREN)` sites (`:343,:347,:352,:357,:395,:428,:564,:630,:636,:844,:857,:966,:1133,:1145,:1152,:1158,:1164,:1574,:2177,:2193,:2211,:2256,:2399,:3091,:3111`) + `_lib_path`/`_skill_body_path` (`:265-273`) + T23's `zip(CHILDREN, lists)` (`:613/:618`) + `for agent in CHILDREN` (`:956`, `:707`) | One tuple cannot carry **three orthogonal axes**: the installer axis (keys `INSTALLER_SKILLS`, drives T23's 3-arity), the family/leaf-claim axis (claims true of canary), and the seeding axis (who gets a manifest). Splitting them is what makes "add canary" a one-line change per axis instead of an arity break |
| **C2** | `FAMILY` (`:75`) — **the persona roster** | `("aegis","hacker","cracker","sweeper")` — **4 personas** | **`FAMILY_ROSTER = ("aegis","hacker","cracker","sweeper","canary")`** — **5 personas** | 15 `parametrize("agent", FAMILY)` sites (`:368,:374,:378,:383,:389,:449,:460,:473,:487,:492,:498,:504,:701,:2505,:2514`) **+** the non-parametrized consumers (`:558` family-manifest filter, `:667`/`:669` single-home sweep, `:3399` the tool-matrix-untouched loop) **+** the `LEGALITY_CELLS` consumers (`:764` parametrization, `:782` `len == 20`, `:787` `agents == set(FAMILY)`) | Canary is a **persona**; every persona-wide claim (prose hygiene, cardinal cap, tone block, queue, baseline rows, legacy-artifact absence, matrix completeness) must cover it. This is **the fourth thing the previous ledger left behind** — it renamed `CHILDREN` and stopped |
| **C3** | `CHILD_ALLOW` (`:89`) | one shared 6-set `{bash, proc, filesystem, time, help, self}` | **`ALLOW_BY_AGENT`** = per-agent exact sets: `hacker/cracker/sweeper` keep the 6-set; **`canary` = `{bash, filesystem, time, help}`** | `test_children_allow_set_exact` (`:357-359`) — the **only** consumer | Canary deviates from the leaf trio by **exactly two categories**, each with a recorded ruling reason; a shared set either reds or, "repaired" by widening, re-installs `proc` (NC-4) and `self` (AM-1) |
| **C4** | `TONE_HEADINGS` (`:131`) | **4 keys** (`aegis`, `hacker`, `cracker`, `sweeper`), 3 headings each | **5 keys** — add `"canary": ("Voice to my orchestrator", "Voice from dispatch", "Per-severity framing")` (the child form; canary is an `aegis` child) | `test_tone_block_in_soul` (`:498-501`) — `TONE_HEADINGS[agent]` | With `FAMILY_ROSTER` carrying canary and `TONE_HEADINGS` still at 4 keys, the leg reds with a **`KeyError: 'canary'`** — not a missing-heading assertion. It is a **constant** defect, not an authoring defect |
| **C5** | `INSTALLER_SKILLS` (`:145`) | 3 keys → `fuzzing-toolchain` / `credential-bench` / `sast-toolchain` | **UNCHANGED** (3 keys) | `:266`/`:271` (the two path helpers), `:571` (the bank gate), and every `_lib_path`/`_skill_body_path` caller | Recorded explicitly so the approver sees it was **considered and left alone**: canary has no installer toolchain, and adding a key would make `_lib_path("canary")` point at a nonexistent `.lib.sh` |

**Inline roster literals (not constants — they must be folded too, and they will NOT red on their
own):**

| # | Literal (base line) | Today | New | Leg |
|---|---|---|---|---|
| **L1** | `_REGISTRY_PROBE` tuple (`:721`) | `('aegis','hacker','cracker','sweeper')` | add `'canary'` | `test_registry_scan_discovers_family` (`:733`), `test_registry_scan_tools_block_round_trips` (`:744`) — **green without the fold, silently under-covering the new persona**; the fold is a coverage fix, not a red |
| **L2** | The aegis team literal in `test_aegis_team_members_exact` (**T4**, `:326-327`) | 4-set `{hacker, cracker, sweeper, worker}` | 5-set `{hacker, cracker, sweeper, worker, canary}` | T4 — **reds** (P3 task 8 adds `canary`) |
| **L3** | The **second** aegis team literal in `test_the_tool_matrix_is_untouched` (`:3405-3408`) | 4-set | 5-set | **reds** — the same assertion restated in a different leg; folding only L2 leaves this one red (the class the previous ledger missed) |

**Two symbols the previous ledger got wrong, stated plainly:**

* **LEAVES does not exist** — there is no `LEAVES` constant anywhere in
  `tests/unit/agents/test_aegis_security_family.py` (nor in the sibling agent tests) — the iteration-001
  ledger invented it. The children tuple is `CHILDREN` (`:76`), and after this fold it is
  `INSTALLER_CHILDREN` + `FAMILY_CHILDREN`.
* **`FAMILY_CHILDREN` is the children tuple, not the roster.** It carries the four **leaf-side**
  personas (`hacker`, `cracker`, `sweeper`, `canary`); the **persona roster** — the fourth thing left
  behind — is `FAMILY_ROSTER` (`aegis` + the four leaves).

#### 2. The code, as written

```python
# ---- C2: the PERSONA ROSTER (was FAMILY, :75). The 4th constant the previous ledger missed.
FAMILY_ROSTER = ("aegis", "hacker", "cracker", "sweeper", "canary")

# ---- C1a: installer-side identity. NOTHING changes here. 3 members, because:
#   - INSTALLER_SKILLS keys on these three (hacker/cracker/sweeper),
#   - T23's byte-identity leg is hardcoded to 3-arity
#     (lists[0] == lists[1] == lists[2]; zip(INSTALLER_CHILDREN, lists)).
#   A 4th element ARITY-BREAKS T23 -- do not add canary here.
INSTALLER_CHILDREN = ("hacker", "cracker", "sweeper")

# ---- C1b: family-side identity. The tuple that GROWS, for legs whose claim is true of canary
#   (T5 team-empty, T6 allow-non-empty, T7 write-deny-fence, no-mcp, scrutiny exemption).
FAMILY_CHILDREN = ("hacker", "cracker", "sweeper", "canary")

# ---- C1c: the SEEDING axis, orthogonal to the other two.
#   hacker/cracker are DOCTRINE-ONLY (D2-B disclosed gap): no manifest, no mirror body, no data file.
#   They are in NEITHER the "gains a manifest" set NOR the mirror set -- they are the doctrine layer.
DOCTRINE_ONLY = ("hacker", "cracker")

#   The seeded roster (D2-B) == the manifests that must exist and carry exactly one
#   injection-screen entry. `worker` is here but NOT in FAMILY_ROSTER (it is not a family persona).
MANIFEST_BEARING = ("aegis", "canary", "sweeper", "worker")

# ---- C3: ALLOW_BY_AGENT replaces the single CHILD_ALLOW 6-set (:89).
#   The leaf trio KEEPS its 6-set. Canary deviates by EXACTLY TWO categories,
#   each with its recorded ruling reason:
#     - "proc" : NC-4  -- a full bash substitute granting background persistence
#                         past teardown; canary has no long-running-process mandate.
#     - "self" : AM-1  -- inner_soul/access_memory is the sanctioned durable
#                         self-behavior-write channel; a payload that re-preferences
#                         canary survives the campaign (the self-hypnosis shape).
#                         Removed MECHANICALLY, not doctrinally.
ALLOW_BY_AGENT = {
    "hacker":  frozenset({"bash", "proc", "filesystem", "time", "help", "self"}),
    "cracker": frozenset({"bash", "proc", "filesystem", "time", "help", "self"}),
    "sweeper": frozenset({"bash", "proc", "filesystem", "time", "help", "self"}),
    "canary":  frozenset({"bash", "filesystem", "time", "help"}),
}

# ---- C4: TONE_HEADINGS gains the 5th persona (child form -- canary is an aegis child).
TONE_HEADINGS = {
    "aegis":   ("Voice to my caller", "Voice in dispatch prompts", "Per-severity framing"),
    "hacker":  ("Voice to my orchestrator", "Voice from dispatch", "Per-severity framing"),
    "cracker": ("Voice to my orchestrator", "Voice from dispatch", "Per-severity framing"),
    "sweeper": ("Voice to my orchestrator", "Voice from dispatch", "Per-severity framing"),
    "canary":  ("Voice to my orchestrator", "Voice from dispatch", "Per-severity framing"),
}

# ---- C4b: the cardinal pin becomes PER-AGENT (was `assert _cardinal_count(agent) == 7` for all).
#   Canary is authored to the cap: SEVEN numbered cardinals (P3 task 3), which is why the map's five
#   entries are all 7. If the authored file lands at 6, ONLY this map's canary entry changes --
#   the leg is per-agent precisely so an authored count is a one-line fact, not a file rewrite.
CARDINAL_COUNT_BY_AGENT = {
    "aegis": 7, "hacker": 7, "cracker": 7, "sweeper": 7, "canary": 7,
}
```

#### 3. Every affected leg, named (the ledger the approver checks line by line)

**(a) `INSTALLER_CHILDREN` — the 25 `CHILDREN` legs split three ways:**

| Legs | Count | New binding | Behaviour |
|---|---|---|---|
| `:343/:344` team-empty · `:347/:348` allow-non-empty · `:352/:353` deny-write-fence · `:357/:359` allow-set-exact (**+ C3 body**) · `:395/:396` no-mcp · `:428/:429` scrutiny-exemption | **6** | **`FAMILY_CHILDREN`** | claims true of canary too; `:359` additionally reads `ALLOW_BY_AGENT[agent]` |
| `:564/:565` **no-manifest** | **1** | **`DOCTRINE_ONLY`** | **splits** — see (a2) below |
| `:630` lib-fences-header · `:636` fences-heading-neutral · `:844` per-bullet presence · `:857` checksum-invoked · `:966` checksum-refuses-unset · `:1133` no-docker/apt/sudo · `:1145` trap-based lock release · `:1152` attempt cap · `:1158` readiness three-state · `:1164` reversal section · `:1574` sourcing does not change caller options · `:2177` staging root · `:2193` env-overridable roots · `:2211` guarded config promote · `:2256` bench roots · `:2399` checksum-mismatch refuses · `:3091` no-exit-from-sourced · `:3111` refusal helper returns | **18** | **`INSTALLER_CHILDREN`** (unchanged membership) | installer-artifact claims; canary has no `.lib.sh`/installer body, so adding it here would red by construction |

**(a2) The `:564` split, stated exactly.** `test_children_have_no_manifest` (`:564-567`,
`parametrize("agent", CHILDREN)`, `assert not (…/skill-set.yaml).exists()`) **reds by construction**:
`sweeper` **gains** a manifest this lane and `canary` **has** one — and both are family personas. The
leg is split into:

| New leg | Binding | Assertion |
|---|---|---|
| `test_doctrine_only_children_have_no_manifest` | **`DOCTRINE_ONLY`** = `hacker`, `cracker` | no `skill-set.yaml` **and** no `skills-template/injection-screen.md` **and** no `injection-rules.json` — the doctrine layer only (D2's disclosed gap, positively asserted rather than merely absent) |
| `test_seeded_agents_have_one_injection_screen_entry` | **`MANIFEST_BEARING`** = `aegis`, `canary`, `sweeper`, `worker` | `skill-set.yaml` **exists** and carries **exactly one** `injection-screen` entry, and the same-dir body exists |

`hacker` and `cracker` are in **neither** the "gains a manifest" set nor the mirror set — they are the
**doctrine-only** pair, and this ledger says so in the code (a named tuple), not only in prose.

**(b) `FAMILY_ROSTER` — the 22 legs the previous split left behind:**

| Legs | Count | What reds without the fold | Fix |
|---|---|---|---|
| `:492/:493-496` cardinal pin (`assert _cardinal_count(agent) == 7` for **all**) | 1 | reds unless canary is authored at exactly 7; the leg cannot express a per-agent truth | **C4b map** — `assert _cardinal_count(agent) == CARDINAL_COUNT_BY_AGENT[agent]` |
| `:498/:499-501` tone block (`TONE_HEADINGS[agent]`) | 1 | **`KeyError: 'canary'`** — a constant defect, before any file is read | **C4** — 5th key |
| `:701/:702` `default_queue == "system_parallel_queue"` | 1 | reds unless canary's meta **sets** it | **P3 task 1 must set `default_queue`** (and `llm_model: "coding"`, pin 7.1) — an **authoring** fix, not a test edit |
| `:556-561` family-manifest list (`== ["aegis"]`) | 1 | reds — `sweeper` and `canary` both gain manifests | re-derive as **"the family manifests are exactly the seeded family set"** = `{aegis, canary, sweeper}` (sorted list), with `worker` (non-family) asserted in the `MANIFEST_BEARING` leg |
| `:764` `LEGALITY_CELLS` · `:782` `len == 20` · `:787` `agents == set(FAMILY)` | 3 | `len == 20` reds; `:787` reds unless the matrix gains canary | **25** cells (5 families × 5 personas); `assert agents == set(FAMILY_ROSTER)` |
| `:3405-3408` second aegis team literal (L3) | 1 | **reds** — the same 4-set restated | 5-set |
| `:368,:374,:378,:383,:389` privileged/job/instance/mcp/watchover · `:449` system-internals · `:460` `.md` tokens · `:473` bare `agents/` prefix · `:487` ≤7 cap · `:504` provenance · `:2505` baseline rows · `:2514` legacy names · `:667/:669` single-home · `:3399` generalist sweep | **14** | red **only in the window between the rename and P3's authored files** (they read canary's files); green at HEAD given correctly authored content | no pin edit — but every one of them is a **content constraint P3 must satisfy** (see the authoring note below) |
| **TOTAL** | **22** | — | — |

**Authoring constraints the 14 content legs impose on P3 (stated so they are not discovered at gate
time):** canary's prose carries **no** system-internals token from `SYSTEM_INTERNAL_TOKENS`
(`meta.json`, `tools.allow`, `tools.deny`, `daemon/`, `_tool_registry`, `skill-set.yaml`,
`innate_skills`, `seed_all`, `agent_id=`, `default_agent_versions` — `:102-112`), **no** `.md` path
token, **no** bare `agents/` prefix, **no** `adapted from` / `preserved verbatim from` provenance
marker, **no** legacy artifact name, and — from `:428-443` — **no** `spawn_instance`, no `spawn a \``,
no `spawn another`, no `spawn \`` shape (the hand-off is phrased as *aegis* spawning, never as a
spawn-fallback pattern).

**(c) `ALLOW_BY_AGENT`** — one leg (`:357-359`); the assertion becomes
`set(_meta(agent)["tools"]["allow"]) == set(ALLOW_BY_AGENT[agent])`, parametrized over
`FAMILY_CHILDREN`, so the canary row is exact and the trio's 6-set is pinned verbatim.

**(d) `TONE_HEADINGS`** — one leg (`:498-501`); the fold is the 5th key (C4).

**Three binding rules that follow from the split:**

1. **`INSTALLER_CHILDREN` stays a 3-tuple.** T23 keeps `lists[0] == lists[1] == lists[2]` and
   `zip(INSTALLER_CHILDREN, lists)`; a 4th element requires rewriting the leg's arity, which is not
   this lane's business and would weaken the pin.
2. **`FAMILY_CHILDREN` carries `canary`** — for T5/T6/T7 and any other leg whose claim is true of
   canary. A leg that reads an installer artifact (`INSTALLER_SKILLS`, the FENCES block, a per-tool
   installer body) **stays** on `INSTALLER_CHILDREN`.
3. **The `CHILD_ALLOW` repair must NOT re-install `proc`.** Canary's set is
   `{bash, filesystem, time, help}`; a "make it match the other children" edit adds `proc` **and**
   `self` back and re-opens NC-4 and AM-1. `ALLOW_BY_AGENT` is asserted **per agent** — there is no
   shared leaf set left for a naive edit to satisfy.

**Cell arithmetic (binding):** T29 20 → **25** (5 families × 5 personas: aegis, hacker, cracker, sweeper, canary); completeness `len == 25`; `agents` set includes `canary`; each canary anchor **single-site** on a `^\d+\.\s\*\*` line. T31: 6 personas × 4 anchors = **24** anchor assertions + canary's 2 carve-out anchors = **26**. For the five cardinal personas the self-proof asserts *exactly once* and the numbered-line property. **For worker the numbered-line property is NOT asserted** (legacy `## Must` prose): its anchors must occur exactly once, each on a single line, inside the declared CRITICAL subsection.

**New pins (T31–T38):** T31 doctrine-anchor matrix · T32 fence-contract leg · T33 parser leg · T34 heuristic corpus leg (LLM off) · T35 canary meta pins · T36 campaign-log + report shape · T37 corpus hygiene · **T38 scanner-execution leg** — runs the committed `agents/canary/skills-template/injection_scan.py` from the **ruled three-CWD set (W8): repo root · `/tmp` · the INSTALL/RELEASE ROOT** — the same three labels P5 and P6 assert; the discovery-ladder purpose survives as assertion **(e)** while the byte-identity triple is the ruled one. It asserts **(a)** the three runs are **byte-identical** (fence + sha256 + verdict blocks); **(b)** bare `python3` works — **no daemon import, no venv dependency, no network, no writes outside the scratch root**; **(c)** the **LLM pass is OFF by default**; **(d)** **ANY downgrade on EITHER axis is REFUSED** (AM-3 monotonicity, SK-11): a `--llm-verdict` lower in severity than the heuristic verdict **and** a `--llm-confidence` lower than the heuristic confidence — each may only stay or rise, so a pin that stops at `hostile` is **not** the pin (the `suspect` + `--llm-verdict clean --llm-confidence high` laundering case is the discriminating one); **(e)** the **discovery ladder resolves from each of the three documented locations** (cwd-relative rung first, then the install-root-resolved rung — the reason the install-root leg exists). **Host dependency (stated, never discovered at stage time):** the scanner legs require a **host `python3` on `PATH`** — the scanner imports stdlib only, so the host interpreter suffices; a host without `python3` simply cannot run T38 / Gate E / P6 task 10. **Budget pins (AM-4 — these supersede AM-2's numbers):** T37 asserts **(i)** every mirrored body is `len <= 8,000` chars (**HARD** — the `skill_view` display window is the weakest bound read path, and the bodies are byte-identical, so one window governs all four recipients), and **(ii)** the **data file** `agents/canary/skills-template/injection-rules.json` is `<= 8,000` chars / `<= 120` lines / **18** rules; T32 asserts the data file **exists**, is resolved **relative to the scanner** (`Path(__file__).with_name(...)`, CWD-independent) and carries its own `version` field. **24 KiB (NFR-05's table ceiling) is the file's OUTER bound, never a v1 target.** A body > 8,000 is a **red**, not a re-evaluation trigger — the table moved out of the body precisely so the body fits the window. **Attestation vocabulary:** T36 asserts the campaign log carries `expected_action_class` **and** `inertness_attested_by: aegis` + `attested_at` — the **FIRER attests inertness at fire time**; canary's declaration is not a certification. Each re-derivation is recorded with a written reason; a blind copy of the predecessor's pin set is a lane-blocking red.

**Mutation legs (the floor is M1–M8 — `M7` is MANDATORY (the mirror silent-drift class); every leg is observed RED→GREEN, and Phase 5 task 9 carries the evidence record):** M1 delete a canary T29 anchor → exactly that cell reds · M2 delete the directive literal → T32 reds · M3 swap a corpus expectation → T34 reds · M4 remove a worker doctrine anchor → T31 reds · M5 change one rule ID → id-uniqueness/coverage reds · M6 unescape a payload line starting with the BEGIN token → round-trip/parser reds · **M7 change a mirror's bytes — OUTSIDE the single permitted `auto_load:` frontmatter line — without bumping its version → identity pin reds** (the silent-drift class) · **M8 break the discovery ladder (drop the install-root rung) → T38 reds in exactly the `/tmp` + install/release-root legs of the ruled set** — the repo-root leg still passes on the cwd-relative rung, so the observed red set is exactly the two non-repo legs; that partial red set is the point: it proves the pin bites the *ladder* and not the scanner body.

**Census / artifacts:** boot census **43 → 44** (staged-gate artifact; no unit pin asserts a total count). Agents-tree files: **366 → 379** (the **13** new files enumerated in the File-arithmetic table: canary's **9** — its new manifest and the AM-4 data file among them — + 3 mirror bodies + sweeper's standalone manifest) — **re-derive at P4**, never freeze; every gate reads the same derived total. Baseline rows **177 → 181**, re-stamped LAST. `tool-categories.json` and the seal map untouched.

**Derivation arithmetic must be shown**, and P4 must RE-DERIVE at ceremony time.

## Gates

| Gate | Criterion |
|------|-----------|
| **G4.1** | `--check` exits 0 **AND** the baseline file exists with **exactly 181 rows** (both asserted). |
| **G4.2** | `git show --stat HEAD` shows exactly the enumerated composition — **and nothing outside it**. |
| **G4.3** | The re-stamp was the **last** content change under `agents/**` in the lane (no later prompt edit). |
| **G4.4** | The shared test file's diff is **exactly the re-derivation set** — T4 (5-set) + the **second** aegis team literal (L3) + the four-constant set (`FAMILY_ROSTER` / `INSTALLER_CHILDREN`+`FAMILY_CHILDREN`+`DOCTRINE_ONLY`+`MANIFEST_BEARING` / `ALLOW_BY_AGENT` / `TONE_HEADINGS` / `CARDINAL_COUNT_BY_AGENT`) + the `:564` manifest-leg split + the `:556-561` family-manifest re-derivation + the `_REGISTRY_PROBE` roster + T21 + T22 + T25 + the T29 canary row + `len == 25` + `EXPECTED_BASELINE_ROWS` — with **every other leg byte-stable** and every fold carrying its one-line reason. |
| **G4.5** | Every leg in the extended family test file is **mutation-meaningful** (the named mutation reds exactly the intended leg), and the **T31–T38** leg inventory is complete with the `# T3x` markers present. |
| **G4.5b** | The pack registration names the **T31–T38** legs; the mutation evidence is written in the house format *planted change → expected red set → observed red set → restore → green* (the evidence itself is the tester lane's P5 deliverable — `requirements.md` CER-08). |
| **G4.6** | The re-derived agents count (**379** = 366 + 13: 9 canary-dir + 3 mirror bodies + 1 standalone manifest) and discoverable count (**44**) match the derivation rule — and every downstream gate (P5 Gate G, P6 task 10 / G6.1b) reads the **same** number. |
| **G4.7** | The corpus contains no secret-shaped string (with both scanner controls), and every fixture is synthetic. |

---

## Verification Commands

```bash
cd /home/nea/ensemble-src-wt-canary-injection-defense

# --- DERIVATION 1: the one-commit composition ---
git show --stat HEAD
#   MUST be exactly: 13 new agents/ files + 9 modified agents/ files + the regenerated baseline
#                     + the shared test file's re-derivation set + the new T31-T38 legs + the corpus.

# --- DERIVATION 2: the baseline (existence AND count -- rc alone proves nothing) ---
test -f agents/.prompt-artifacts.sha256 && echo "file EXISTS" || echo "file MISSING"
wc -l < agents/.prompt-artifacts.sha256
#   177 (base) + 4 (canary soul/rule/workflow/tools_note) = 181 expected
python3 scripts/protection/generate_prompt_artifact_baseline.py --check; echo "rc=$?"   # rc=0

# --- DERIVATION 3: the agents-tree count (366 + 13) ---
find agents -type f | wc -l
#   base 366
#   + canary tree                +9   (meta, soul, rule, workflow, tools_note, skill-set.yaml,
#                                      injection-screen.md, injection_scan.py, injection-rules.json)   <- AM-4 data file
#   + aegis mirror               +1
#   + worker mirror              +1
#   + sweeper mirror             +1
#   + sweeper manifest           +1   (agents/sweeper/skill-set.yaml is NEW at base)
#   (hacker/cracker: +0 — doctrine only, no mirror, no manifest, no data file)
#   = 379 expected at HEAD   (re-derive from the real list; never trust this sentence)

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

# --- DERIVATION 8: the mirror + manifest invariants (the drift pin; AM-2 exception) ---
CANON=agents/canary/skills-template/injection-screen.md
#   AM-2/AM-4: byte-identity MODULO BLANK LINES **and** the single named `auto_load:` line.
#   Drop BOTH before comparing -- a strip that keeps only the auto_load filter reds on blank lines.
strip_auto() { grep -v '^auto_load:' "$1" | sed '/^[[:space:]]*$/d'; }
for a in aegis sweeper worker; do
  diff -q <(strip_auto "$CANON") <(strip_auto "agents/$a/skills-template/injection-screen.md") >/dev/null \
    && echo "OK $a" || { echo "DRIFT $a"; exit 1; }
done
#   the 3 mirrors (hacker/cracker: none -- D2 doctrine only). Byte-identity MODULO BLANK LINES and
#   the single named `auto_load:` line; a raw `cmp` is the WRONG pin (it would red on that one line).
grep -l 'injection-screen' agents/*/skill-set.yaml | wc -l    # 4 (canary + aegis + worker + sweeper)
grep -h 'version:' agents/*/skill-set.yaml | sort -u | wc -l # 1 (all equal -- the propagation lever)

# --- DERIVATION 9: the FOUR-constant set is REAL in the file (B1) ---
grep -n 'FAMILY_ROSTER\|INSTALLER_CHILDREN\|FAMILY_CHILDREN\|DOCTRINE_ONLY\|MANIFEST_BEARING\|ALLOW_BY_AGENT\|CARDINAL_COUNT_BY_AGENT' \
     tests/unit/agents/test_aegis_security_family.py
#   all seven names present; FAMILY_ROSTER == 5 (aegis + 4 leaves); INSTALLER_CHILDREN == 3;
#   FAMILY_CHILDREN == 4 (with canary); DOCTRINE_ONLY == 2 (hacker/cracker); MANIFEST_BEARING == 4
grep -n 'zip(INSTALLER_CHILDREN\|lists\[0\] == lists\[1\] == lists\[2\]' tests/unit/agents/test_aegis_security_family.py
#   T23 keeps its 3-arity (a 4th element would break it)
grep -c 'CHILD_ALLOW' tests/unit/agents/test_aegis_security_family.py
#   0 -- the shared 6-set is GONE; every allow assertion reads ALLOW_BY_AGENT (no proc/self re-entry)
grep -n '^FAMILY = \|^CHILDREN = ' tests/unit/agents/test_aegis_security_family.py
#   (exit 1) -- the bare old names are gone (a leftover `FAMILY`/`CHILDREN` alias is a half-fold)
grep -rn 'LEAVES' tests/unit/agents/test_aegis_security_family.py
#   (exit 1) -- LEAVES never existed and is not introduced
grep -n 'TONE_HEADINGS\[' -A7 tests/unit/agents/test_aegis_security_family.py | grep -c '"canary"'
#   1 -- the 5th persona key exists
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
