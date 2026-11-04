# Phase 5: Verification

## Objective

Prove — on the committed branch — that the aegis family introduces **no behaviour regression**, that
every commission verification expectation holds, and that the artifacts are exactly what the plan
claimed. Each gate below is **independently checkable** and separately attributable, so a failure names
its own cause.

Eight gates, **A–H**.

---

## Entry Criteria

- **Phase 4 exited** — the one commit exists, the baseline is at 177 rows and clean, the lane test is
  green, and the working tree is clean.
- The **true pre-fix base** for the net-new comparison is known: the base commit
  `1f6898ae2889ff002b4656486f6be3ee1048b649` (= `origin/latest` at lane start). Net-new reds are
  computed as the **FAILED-list diff** of the same commands on the base and on HEAD — not a raw
  failure count.
- A venv exists (`uv sync`) for the suite runs; the baseline checks remain venv-free.

---

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | **Gate A — the full-suite net-new comparison.** Run the whole collectable suite on **HEAD** and on the **base** with the *same* commands and the *same* environment; diff the FAILED lists. | Phase 4 exited | **Net-new deterministic reds = 0.** Any red that is red at the base too is pre-existing debt and is **disclosed**, not fixed. Any red that is *only* at HEAD is a lane regression and blocks the phase. |
| 2 | **Gate B — the sealed-artifacts proof.** Run `tests/test_chart_image_delivery_e2e.py::TestPhaseDDoesNotModifySealedArtifacts` on the branch. | 1 | Green. The 35 `SEALED_SHA_BASELINES` entries are unchanged — none of them references a not-yet-existing aegis path, so adding agent directories cannot redden them. This is the commission's **explicit** proof leg (S6). |
| 3 | **Gate C — the plane census.** Run `tests/unit/tools/test_plane_agent_surface_census.py`. | 1 | Green, with `DISCLOSED_READ_HOLDERS` **still `{"watcher"}`** — the aegis family is **not** added to the frozen read-holder set. The E3 fold (explicit non-empty allow on every new meta) removes the exposure rather than whitelisting it. The E4 fold (no `mcp_full_access`) likewise. |
| 4 | **Gate D — the report-integrity fold.** Run `tests/unit/test_report_integrity_prompts.py`. | 1 | Green. The dynamic check finds `aegis`'s scrutiny guidance (its non-empty team makes the guidance mandatory; the three children are exempt because their teams are empty). |
| 5 | **Gate E — the boot-scan.** Run the T28 registry-scan leg under the venv, then the tester's isolated boot-scan leg. | 1 | The registry resolves all four ids (`aegis`, `hacker`, `cracker`, `sweeper`); the isolated boot scan is clean — no new agent appears in the census, no agent fails to register, and no `tools` block parses fail-open. |
| 6 | **Gate F — the tool-categories proof.** Confirm `.agents/shared/metrics/tool-categories.json` is **byte-unchanged** in the lane diff, and run `tests/unit/tools/test_tool_category_artifact.py`. | 1 | The artifact is byte-identical to the base and the test is green — the **F-1 trap class** (a stale committed artifact reddening a whole lane) is avoided because this lane registers **no** new category. |
| 7 | **Gate G — the manifest delta re-derivation.** Re-derive the agents-tree file count from the **actual tree** and compare against the plan's derivation rule (338 + 28 = 366). | 1 | The re-derived count matches the rule; if it does not, the reason is identified (an extra or missing file) and the plan's arithmetic is corrected **before** staging — the number is re-derived, never trusted. |
| 8 | **Gate H — the scope and privacy assertions.** Confirm the lane diff touches only `agents/**` and `tests/**`; confirm no finding artifact, corpus, crash dump or secret was committed; confirm `agents/worker/**` is byte-identical. | 1 | The path list contains only `agents/` and `tests/`; a secret scan over the diff returns zero hits; `git diff --stat <base> -- agents/worker/` is empty. |
| 9 | **Record the evidence.** Write the verification report into the lane's evidence location with the gate-by-gate result, the exact commands, the base-vs-HEAD failure-list diff, and every disclosed pre-existing red. | 1–8 | The report is complete enough that a reviewer can re-run every gate without re-deriving it, and every disclosed deviation is named. |

---

## Gates

| Gate | Requirement | How it is checked |
|---|---|---|
| **A** | No behaviour regression | Full suite, net-new deterministic reds **0** vs the true pre-fix base |
| **B** | `SEALED_SHA_BASELINES` untouched (35 entries) | `TestPhaseDDoesNotModifySealedArtifacts` green on the branch |
| **C** | The plane read-holder census is unchanged | census test green; `DISCLOSED_READ_HOLDERS` == `{"watcher"}` |
| **D** | The report-integrity fold landed | `test_report_integrity_prompts.py` green |
| **E** | Boot-scan clean on the new agent set | T28 registry scan finds all four ids; the isolated boot scan is clean |
| **F** | No tool-category drift | the artifact is byte-unchanged; `test_tool_category_artifact.py` green |
| **G** | The manifest delta is re-derived from the real tree | `find agents -type f \| wc -l` matches the derivation rule |
| **H** | Scope + privacy | the diff touches only `agents/**` and `tests/**`; no committed finding/corpus/secret; `worker` byte-identical |

---

## Verification Commands

```bash
cd /home/nea/ensemble-src-wt-aegis-security-family

BASE=1f6898ae2889ff002b4656486f6be3ee1048b649
HEAD_SHA=$(git rev-parse HEAD)

# --- Gate A: net-new reds -------------------------------------------------
# The house method: net-new = the FAILED-list DIFF vs the true base, same commands.
.venv/bin/python -m pytest -q --timeout=30 2>&1 | tail -40 > /tmp/aegis-head.txt || true
# (run the same on the base in a separate worktree/checkout, then diff the FAILED lists)

# --- Gate B: the sealed-artifacts proof leg ------------------------------
.venv/bin/python -m pytest tests/test_chart_image_delivery_e2e.py::TestPhaseDDoesNotModifySealedArtifacts -q

# --- Gate C: the plane read-holder census --------------------------------
.venv/bin/python -m pytest tests/unit/tools/test_plane_agent_surface_census.py -q
grep -n 'DISCLOSED_READ_HOLDERS' tests/unit/tools/test_plane_agent_surface_census.py   # expect {"watcher"}

# --- Gate D: the report-integrity dynamic check --------------------------
.venv/bin/python -m pytest tests/unit/test_report_integrity_prompts.py -q

# --- Gate E: the boot-scan / registry scan -------------------------------
.venv/bin/python -m pytest tests/unit/agents/test_aegis_security_family.py -q -k registry

# --- Gate F: the tool-categories F-1 proof -------------------------------
git diff --stat $BASE -- .agents/shared/metrics/tool-categories.json     # expect EMPTY
.venv/bin/python -m pytest tests/unit/tools/test_tool_category_artifact.py -q

# --- Gate G: the manifest delta re-derivation ----------------------------
find agents -type f | wc -l                                              # 338 + 28 = 366 expected
git diff --name-status $BASE -- agents/ | awk '$1=="A"' | wc -l           # expect 28 additions

# --- Gate H: scope + privacy --------------------------------------------
git diff --name-only $BASE | grep -v -E '^(agents/|tests/)' ; echo "out-of-scope hits above (expect none)"
git diff --stat $BASE -- agents/worker/                                  # expect EMPTY
git diff $BASE | grep -nE 'sk-(proj|ant)-|BEGIN [A-Z ]*PRIVATE KEY' ; echo "secret hits above (expect none)"

# --- the baseline is still clean on the committed tree --------------------
python3 scripts/protection/generate_prompt_artifact_baseline.py --check; echo "exit=$?"   # expect 0
```

**House notes for this phase:**

- **`pytest -q` buffers tracebacks to the end of the session** — for a large run, use
  `… | tee /tmp/aegis-suite.log >/dev/null; tail -2 /tmp/aegis-suite.log`. A SIGTERM'd run leaves
  dots only, which is not evidence.
- **The net-new comparison must use the same commands on both legs.** A base run with different flags
  produces a meaningless diff.
- **Whole-tree runs can hit async-hang classes.** If a chunk times out, isolate the chunk, re-run it
  3× in isolation, and record the result honestly — a timeout is not a pass and not a fail; it is a
  disclosed gap with a scoped replay recipe.

---

## Coupling

- **Tight with:** P4 — this phase verifies P4's commit; it must run against the committed tree, not a
  dirty working tree.
- **Tight with:** P6 — the staging hand-off consumes this phase's verdict; P6 does not start unless
  Gates A–H are green.
- **Loose with:** P1–P3 — the content is already fixed; a gate failure means a content fix and a
  re-run of P4's commit discipline (a second commit is acceptable **only** if it carries a
  re-stamped baseline — the one-commit rule governs the *initial* landing, not every later fix).
- **Independent of:** P0.

---

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| **P5-R1** | The net-new comparison is run against the wrong base (e.g. a drifted tip instead of `1f6898ae2`) ⇒ the diff is meaningless. | **High** | Medium | The base sha is pinned in the phase text; the diff is computed against that exact sha; the base leg is run in a separate checkout of that sha, never "the current main". |
| **P5-R2** | Pre-existing reds are misread as lane regressions (or vice versa) ⇒ a false pass or a false block. | High | Medium | The house method: net-new = the FAILED-list **diff** of the same commands; every base-red is disclosed by name; every HEAD-only red is investigated to a root cause before the phase closes. |
| **P5-R3** | A whole-tree run times out and the timeout is quietly treated as "no new failures". | Medium | Medium | A timeout is disclosed as a **gap** with a scoped replay recipe; it is never recorded as a pass. Chunks are re-run 3× in isolation to separate a real red from a flake. |
| **P5-R4** | The census "passes" because the aegis family was added to `DISCLOSED_READ_HOLDERS` — the exposure whitelisted instead of removed. | **High** | Low | Gate C asserts the frozen set is **still** `{"watcher"}` — the value is read from the file, not inferred from a green test. The E3 fold is verified positively: every new meta's allow is non-empty (T6). |
| **P5-R5** | The sealed-artifacts leg passes vacuously (the test's scope does not cover the new files) ⇒ the commission's proof requirement is unmet. | Medium | Low | The leg is run and reported with its actual scope (35 entries), and the plan states plainly that the seal does **not** cover the new agents' files — the *baseline* is what covers them. The disclosure is in `plan-overview.md`'s bottleneck list. |
| **P5-R6** | A finding artifact, corpus, crash dump, or secret was committed by accident. | **High** | Low | Gate H's secret scan over the diff plus the path-list check; the family's storage design keeps findings off-repo (DP4/Q2) so there is nothing to commit in the first place. |

---

## Effort

**≈ 0.5 – 1 working day** — the dominant cost is the base-leg suite run (the same suite twice). The
seven gates themselves are minutes each.

---

## Exit Criterion

Phase 5 is done when **all eight gates (A–H) are green** and the evidence report is written:

- **A** — net-new deterministic reds **0** (every base-red disclosed by name);
- **B** — `TestPhaseDDoesNotModifySealedArtifacts` green; the 35 sealed entries unchanged;
- **C** — the plane census green with `DISCLOSED_READ_HOLDERS` still `{"watcher"}`;
- **D** — the report-integrity dynamic check green;
- **E** — the registry scan finds all four agents; the isolated boot scan is clean;
- **F** — the tool-categories artifact byte-unchanged; its test green;
- **G** — the agents-tree count re-derived from the real tree matches the derivation rule;
- **H** — the diff touches only `agents/**` and `tests/**`; no committed finding/corpus/secret;
  `worker` byte-identical;

**and** the evidence report names every disclosed pre-existing red, every timeout gap with its replay
recipe, and every deviation from `plan-overview.md`.

Only then does Phase 6 stage the release.
