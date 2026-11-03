# Stage-prep checklist — metrics-agent-tools

Date: 2026-11-03 (UTC) · Lane: implementation (developer) → **giter owns the stage**
· Branch: `feature/metrics-agent-tools` · Base: `03a2297c3180e79c6b5c26a92b2caca7c0452725`
· Worktree: `/home/nea/ensemble-src-wt-metrics-tools`

**This checklist is the hand-over.** It enumerates every expected delta with its verification
command, the two HARD PROHIBITIONS (no version minted, no promote performed) and the two
hand-overs (the version slot → giter; the promote → the user's 3-factor nonce ceremony).

---

## 0. The two hard prohibitions

| Prohibition | Status | Evidence |
|---|---|---|
| **NO version minted by this lane** | ✅ honoured | no version string appears in any lane artefact; the plan names no version; the version slot is derived by giter at stage time (house W9) |
| **NO promote performed** | ✅ honoured | no promote command was run; the live release is untouched; the lane made ZERO live contact (no daemon run, no frozen-binary smoke, no port) |

---

## 1. The expected EMPTY deltas (each with its verification command)

| Delta | Expected | Verification command | Observed |
|---|---|---|---|
| `daemon/migrations/versions/` | **EMPTY** (the lane adds NO table — P-5/D-UX-14) | `git diff --name-only <BASE> <HEAD> -- daemon/migrations/versions/` | **0 files** |
| `pyproject.toml` | **EMPTY** (no backend dependency) | `git diff --name-only <BASE> <HEAD> -- pyproject.toml` | **0 files** |
| `frontend/package.json` | **EMPTY** (no npm dependency) | `git diff --name-only <BASE> <HEAD> -- frontend/package.json` | **0 files** |
| `frontend/package-lock.json` | **EMPTY** (the `npm ci` provisioning does not touch the lockfile) | `git diff --name-only <BASE> <HEAD> -- frontend/package-lock.json` | **0 files** |
| `config.yaml` / `daemon/config.py` | **EMPTY** (no new knob — D-UX-15) | `git diff --name-only <BASE> <HEAD> -- config.yaml daemon/config.py` | **0 files** |

---

## 2. The expected NON-EMPTY payload deltas

| Tree | Expected | Observed |
|---|---|---|
| `daemon/` | the seam + the tool module + the registry edits + the scope line + the three route decorators + the loader's cold-boot scan | **10 files** |
| `agents/` | **EXACTLY 6** — 3 `meta.json` + 3 `tools_note.md` | **6 files** |
| `frontend/` | the Phase-4 fold files | **6 files** |
| `tests/` | the new packs + the moved pin sites | **21 files** |
| `test/packs/` | the two shell packs + the mutation harness | **3 files** |
| `docs/architecture/` | the contract amendment + the watch outline | **2 files** |
| `.agents/` | the plan set + the approver artefacts + the PACKS.md rows + this checklist + the evidence pack | **17 files** |

**The PROMPT-ARTIFACT re-stamp implication (council W7 — the derivation, not a memory):**
the baseline tracks `VERSIONED_PROMPT_ARTIFACTS` (`daemon/tools/prompt_artifacts.py`), and
`meta.json` is **not** one ⇒ of the 6-file `agents/` PAYLOAD delta only the **3 `tools_note.md`**
move the baseline. The stage-time commands:

```bash
# (a) the BASELINE-EXISTS precondition — a missing baseline is a SILENT PASS
test -f agents/.prompt-artifacts.sha256 || echo "NO BASELINE — the check would be vacuous"

# (b) the SEALED no-match over the 6 touched agents/ files (35 rows, 0 hits ⇒ no re-pin)
python3 -c "from tests.test_chart_image_delivery_e2e import SEALED_SHA_BASELINES; \
  touched=['agents/maintenancer/meta.json','agents/maintenancer/tools_note.md', \
  'agents/devops/meta.json','agents/devops/tools_note.md', \
  'agents/wanderer/meta.json','agents/wanderer/tools_note.md']; \
  hits=[p for p in touched if p in SEALED_SHA_BASELINES]; \
  print('hits:', hits); raise SystemExit(1 if hits else 0)"

# (c) the baseline check ITSELF — the expected delta is EXACTLY 3 files
python3 scripts/protection/generate_prompt_artifact_baseline.py --check
#   → PROMPT-DRIFT: 3 difference(s) … modified=3 missing=0 added=0 malformed=0
#   → the three PROMPT-DRIFT-FILE lines are the three tools_note.md
```

**The expected baseline delta = 3 files** (the three `tools_note.md`); the `agents/` PAYLOAD
delta stays 6.

---

## 3. The E-1 variant record (the ceremony text must state it plainly)

**The EXECUTED variant: the description-only contract-prose amendment** (P-1 / E-1, adjudicated
2026-11-03 + amendment-2) — **NOT** the 3.6a debt-record fallback.

* Nine lines changed, all inside the FIVE canonical ranges; the line count is UNCHANGED
  (819 → 819).
* The `central_metrics_contract_doc` pack is **GREEN WITH THE AMENDMENT APPLIED** — 21/21,
  expectations untouched, **never re-pinned**. All SIX fenced blocks and all NINETEEN prose
  literals are byte-identical before/after.

**The DISCLOSURE the ceremony must carry (approver i2-N6):** the contract's
emergency-off-lever prose at `metrics-query-contract.md:58-60` still names ONLY
`maintenance:read` — that location is OUTSIDE the five canonical ranges, so the amendment does
not touch it. The `metrics:read` grant is revoked by the SAME token-revocation path. The
residual is owned by the contract owner (fold-ledger §B `contract_RevocationLeverProse`) and is
**NOT a wire or behaviour defect**.

---

## 4. The gate facts giter needs

| Fact | Value |
|---|---|
| the lane branch tip | see the evidence pack §1 (the tip at hand-over) |
| the ONE-commit cluster commit id | **`23695bef3`** (the tool module + the registry edits + the factory wiring + the 3 metas + the 3 tools_note + the 16-site pin fan-out + the registration pack + the tools pack) |
| the Phase-1 Tier-2 verdict | net-new deterministic reds **0** (the FAILED list identical on both legs) |
| the Phase-2 Tier-1 verdict | net-new deterministic reds **0** |
| the Phase-3 Tier-1 verdict | net-new deterministic reds **0**; the route censuses **111 / 37 / 33** UNCHANGED |
| the Phase-4 FE verdict | net-new **0**; both builds rc=0; the e2e 3/3; the dependency diff **0 lines** |
| the Phase-5 mutation matrix | **22/22 PASS** (21 RED/GREEN + 1 FOLLOW) (RED via the intended assertion → byte-identical restore → GREEN) — the ARCHIVED log: `.agents/shared/planning/metrics-agent-tools/evidence/mutation-matrix-2026-11-03.log` (sha256 `eabbbe424ee11a86e4c9f66f791c51b39277c7942d4936b025b4e6e19838ae7e`) |
| the prompt-artifact re-stamp | **REQUIRED** — the baseline delta is exactly the 3 `tools_note.md` (§2) |
| the throwaway base worktree | `/home/nea/ensemble-src-wt-base-metrics` (detached at `03a2297c3`) — **IMPLEMENTER-OWNED** (council A9); it must be **absent (or accounted) at stage time, never orphaned** |

---

## 4b. The tip drift at hand-over (giter MUST know this)

`origin/latest` has ADVANCED **36 commits** since the lane's base (`03a2297c3`) — the
`promote-handback-halt` lane landed (including the **v0.28.8** version bump). The lane's base
is still an **ancestor** of `origin/latest` (a clean fast-forward), and the file overlap
between the lane's change and the drift is **TWO files**:

| Overlapping file | Nature | Merge handling |
|---|---|---|
| `.agents/tester/PACKS.md` | both lanes add rows | **keep-both, newest-first** (the house rule for PACKS.md) |
| `daemon/api.py` | the drift's hunks are at `:2909`/`:2918`; the lane's are at `:2531`/`:2543`/`:3784` | **no overlap** — the hunks are far apart, so the merge is conflict-free |

**The ten pin files are NOT touched by the drift** ⇒ the ONE-commit cluster (`23695bef3`) cannot
split across the merge. The AmC-9 post-merge pin re-derivation is still the house rule — run
the two greps (step 2 of §6) after the merge and record the artefact.

---

## 5. The two hand-overs

| Hand-over | To | Form |
|---|---|---|
| the **version slot** | **giter** | derived at stage time from the live census (house W9) — this lane names NO version anywhere |
| the **promote** | **the user's 3-factor nonce ceremony** | `ENSEMBLE_UPGRADE_LIVE=1 VERSION=<slot> bash scripts/upgrade/promote.sh live --f2-verified-closed` — **not run by this lane** |

---

## 6. The stage-time checklist (for giter)

1. `git fetch` + confirm `latest` has NOT moved into the lane's tree in a way that would split
   the ONE-commit cluster (the cluster is `23695bef3`; a conflict inside any of the 10 pin files
   is the ONE way it splits — re-derive the pin set by grep afterwards, per AmC-9).
2. Re-derive the pin census by grep — **never trust a prose count**. The census's scope is
   the **TEN pre-existing pin files** (§H.3), and the recipe reproduces that number by
   excluding the lane's OWN new registration pack (the `:!` pathspec form is a SILENT
   NO-OP in GNU grep 3.11 — it treats the exclusion as a filename; use `--exclude=`):

   ```
   grep -rn "PRIVILEGED_TOOL_CATEGORIES == frozenset" tests/ --include=*.py --exclude=test_metrics_read_registration.py
   grep -rn "len(PRIVILEGED_TOOL_CATEGORIES)" tests/ --include=*.py --exclude=test_metrics_read_registration.py
   ```

   → **TEN unique files** (the census's number ✓). The UNFILTERED greps return **ELEVEN**:
   the ten + the lane's own `tests/unit/test_metrics_read_registration.py` (its count pin
   at `:221`). Both numbers are correct — the census records the pre-existing EDIT set
   (10), and the unfiltered recipe sees the lane's own pack as well (11). **Check both:**
   the filtered greps must return 10 unique files, and the unfiltered greps must return 11
   (the ten + exactly `tests/unit/test_metrics_read_registration.py`).
3. Run the three prompt-artifact commands in §2 — the baseline delta must be exactly 3 files.
4. Confirm the EMPTY deltas in §1 (five commands, five zero-line answers).
5. Confirm the throwaway base worktree is absent (or accounted) — it is implementer-owned.
6. Derive the version slot; stage; **do not promote** (the user owns the ceremony).

**The stage-prep is COMPLETE minus the version slot and the promote** — the two items this lane
is forbidden to touch.
