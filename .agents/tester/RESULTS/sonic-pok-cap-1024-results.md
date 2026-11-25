# SONIC-TESTER RESULTS — `pok-cap-1024` (board wi-1921ba17)

**Verdict: `TEST: PASS`** (with 2 disclosed non-blocking notes — see §5)

| field | value |
|---|---|
| Task | `pok-cap-1024` — POK per-project active-entry storage cap **512 → 1024** + read-budget pin + doc alignment |
| Lane | sonic-tester (verification), board commission `wi-1921ba17`, 2026-11-23 |
| Worktree | `/home/nea/ensemble-src-wt-pok-cap-1024` |
| Tree under test | **pushed tip `b5484776e`** (= `refs/tags/v0.31.41^{}`; tag object `c86061ad`) |
| Base | `04bc5e513` |
| Diff range | `04bc5e513..b5484776e` — **14 files, +121/−22** (matches the commission) |
| Fences honoured | ZERO live-store reads, ZERO PostgreSQL, ZERO daemon boot, ZERO live-install contact; no promote |
| Writes by this lane | `test/packs/pok_cap_read_budget_independence_test.sh` (NEW) + ONE registry row in `.agents/tester/PACKS.md`. **ZERO code edits.** |

---

## 0. Tree identity (what was actually tested)

* The worktree `HEAD` is `91900b21f` — a **LOCAL-ONLY post-tag HANDOFF commit**. Its ENTIRE delta vs the pushed tip is one markdown planning file:
  `git diff --name-only b5484776e..91900b21f` → `.agents/shared/planning/sonic-team/pok-cap-1024/HANDOFF.md` (+67 lines). Code-identical.
* The scoped/mutation runs were executed in a **clean `git archive b5484776e` extract** (`/tmp/pokcap-pushed`) — byte-verified against `git show b5484776e:<file>` for all 6 key files (`pok_service.py`, `pok_injection.py`, `test_pok_injection_unit.py`, `test_pok_service_unit.py`, `test_pok_tools_unit.py`, `daemon/tools/pok.py` → **6/6 MATCH**).
* `origin/latest` has since moved to `ea5325a6` (v0.31.42) — a **descendant** of `b5484776e` (our change is merged forward, not reverted). At `origin/latest` the constants are still `POK_ACTIVE_CAP_PER_PROJECT = 1024`, `POK_BLOCK_MAX_ENTRIES = 8`, `POK_BLOCK_MAX_CHARS = 2_900`, and the read path is untouched since the tag. The disclosed release-slot race (handoff "Known risks") materialised benignly.

---

## 1. THE BUDGET PROOF — the commission's explicit demand ✅

### 1a. The committed leg (repo's own pins), at the pushed tip

```
tests/unit/services/test_pok_injection_unit.py::TestCapIndependenceReadBudget::test_constant_pins                                PASSED
tests/unit/services/test_pok_injection_unit.py::TestCapIndependenceReadBudget::test_read_budget_independent_of_storage_cap[64]     PASSED
tests/unit/services/test_pok_injection_unit.py::TestCapIndependenceReadBudget::test_read_budget_independent_of_storage_cap[4096]   PASSED
3 passed, 42 deselected
```

### 1b. MEASURED NUMBERS (the proof itself)

| storage cap (monkeypatched) | entries admitted | chars rendered | rendered-block sha256 |
|---|---|---|---|
| **64** | **8** | **2,371** | `b0d23ea3a4ee1f51…` |
| **4096** | **8** | **2,371** | `b0d23ea3a4ee1f51…` |

* Budget constants pinned by value: `POK_BLOCK_MAX_ENTRIES == 8`, `POK_BLOCK_MAX_CHARS == 2_900`.
* The two rendered blocks are **BYTE-IDENTICAL** across the two cap regimes (same sha256) — **strictly stronger** than the `<= 2,900` alone. The storage cap does not influence the view at all.
* **Independent re-derivation** (this lane's own driver, NOT the repo's test file — own synthetic store, own row construction, real `POKService` + real `POKRepository` + real `admit_view`/`render_pok_block`): same numbers, **8 entries / 2,371 chars** in both regimes. The developer's claimed measurement is **CONFIRMED**.

### 1c. Structural independence (why it holds, not just that it passed)

* `daemon/services/pok_injection.py` contains **ZERO** references to `POK_ACTIVE_CAP_PER_PROJECT` — the read path *cannot* see the storage cap.
* It **does** reference both budget constants (the grep's own non-vacuity control), and the storage cap is defined **exactly once** (single source).
* The budget constants are bound **BY VALUE** at import time (`from daemon.services.pok_service import POK_BLOCK_MAX_ENTRIES, …`) — so patching `pok_service`'s attributes is a **silent no-op** in-process. (Disclosed; relevant to any future in-process budget test.)

### 1d. Anti-vacuity — the measurement harness is proved LIVE

* In-process, the read budget was deliberately widened (`daemon.services.pok_injection.POK_BLOCK_MAX_ENTRIES` 8 → 16) → the harness **detected the violation** (admitted **10**, not 8) and the budget restored cleanly afterwards.
* Note: the FIRST attempt patched `pok_service.POK_BLOCK_MAX_ENTRIES` and the harness stayed green — that is the by-value binding above, i.e. the probe itself was inert. The pack now patches the binding the read path actually reads.

### 1e. MUTATION MATRIX (disposable `/tmp` copies — the live worktree never mutated)

| mutation | expected | observed |
|---|---|---|
| couple the READ entry budget to the STORAGE cap (`pok_injection.py` → `POK_ACTIVE_CAP_PER_PROJECT`) | the committed budget leg goes RED | ✅ **2 RED** — `admitted 10, expected exactly 8` in **both** regimes (64 and 4096) |
| widen the budget constants (entries 8→16, chars 2,900→5,000) | constant pin + both regimes RED | ✅ **3 RED** |
| regress the storage cap 1024 → 512 | cap pin + refusal leg RED | ✅ **2 RED** (`test_active_cap_constant_is_1024`, `test_active_cap_refuse_then_archive_cure_then_succeed`) |

**Conclusion: the budget proof is real, discriminating, and independently reproduced. The read-side injection budget is UNCHANGED and PINNED, and it is INDEPENDENT of the storage cap.**

---

## 2. CAP PINS ✅

| node | assertion | result |
|---|---|---|
| `tests/unit/services/test_pok_service_unit.py::TestAc19TerminalityCured::test_active_cap_constant_is_1024` | `POK_ACTIVE_CAP_PER_PROJECT == 1024` (literal) | ✅ PASS |
| `…::TestAc19TerminalityCured::test_active_cap_refuse_then_archive_cure_then_succeed` | `cap == 1024` (literal) | ✅ PASS |
| `tests/unit/tools/test_pok_tools_unit.py::TestRegistrationSurface::test_upsert_description_renders_the_live_cap` | prose renders the LIVE constant + retired-value guards | ✅ PASS |

* `TestAc19TerminalityCured` = **5 passed**.
* **Independent probe of the SHIPPED prose** (no test code): `at 1024 active` **present**; `64 active entries` / `256 active entries` / `512 active entries` **absent**; `<active_cap>` placeholder **absent**; the shipped line reads `- Admission is refuse-never-evict: at 1024 active`.
* **No retired literal is baked anywhere in the shipped POK code** (`POK_ACTIVE_CAP_PER_PROJECT = 512|256|64` → 0 hits; `…_active entries` → 0 hits).
* **Honest nuance on "in both unit files".** The literal `== 1024` pin lives in **one** file (`test_pok_service_unit.py`). The tools file's pin is **constant-derived** (`f"at {POK_ACTIVE_CAP_PER_PROJECT} active"` — i.e. it renders whatever the constant says), which is the stronger design: it can never go stale. Consequence, measured by Mutation C: a 512 regression does **not** redden the tools file (it would read `at 512 active`); the literal `== 1024` pin in the service file is what catches it. Both pins green; the retired-value guards (64/256/512) hold.

---

## 3. SCOPED POK PACK ON THE PUSHED TIP ✅

Registered pack `pok_family_key_scoped_pack` (6 files, ONE invocation), run in the clean `git archive b5484776e` extract:

```
212 passed, 1 warning in 7.98s   (RC=0)
```

Node census (mechanical `--collect-only`, RC=0, 0 collection errors):

| file | nodes |
|---|---|
| `tests/unit/services/test_pok_service_unit.py` | 67 |
| `tests/unit/services/test_pok_injection_unit.py` | **45** (42 + the 3 new budget nodes) |
| `tests/unit/services/test_pok_metadata_cap_unit.py` | 23 |
| `tests/unit/services/test_pok_trust_tier_unit.py` | 40 |
| `tests/unit/services/test_pok_ari_seam_unit.py` | 13 |
| `tests/unit/tools/test_pok_tools_unit.py` | 24 |
| **TOTAL** | **212** |

The developer's "212 passed on a scoped 6-file POK pack" is **CONFIRMED** (the registry's stale `209` is the pre-change count).

---

## 4. NO REGRESSION IN THE WIDER REGISTERED SUITE ✅ (A/B against base `04bc5e513`)

| pack / scope | HEAD | BASE | net-new reds |
|---|---|---|---|
| `pok_family_key_scoped_pack` (6 files) | **212 P / 0 F** | — | **0** |
| `tests/unit/test_pok_grant_pins.py` | **10 P / 0 F** | — | **0** |
| `tests/unit/test_maintenancer_agent.py` (imports `pok_service`) | 70 P / **3 F** | identical | **0** — the 3 are the QUARANTINE-LEDGERED `memory.md family` (`agents/maintenancer/memory.md` deleted by archive sweep `3c307e8d4`, 2026-10-14; cross-referenced in PACKS.md `agent_restart_targeted_head_heavy` as "367P/3F-pre (memory.md family, quarantined)"). The range touches **zero** files under `agents/` and does not touch that test file. |
| `regression_unit_services` (registered partition, `tests/unit/services/`) | run1 **24 F / 4693 P / 14 S** · run2 **22 F / 4695 P / 14 S** | **23 F / 4691 P / 14 S** | **0** |
| `regression_unit_tools` (registered partition, `tests/unit/tools/`) | **24 F / 5764 P / 7 S / 1 X** | **24 F / 5764 P / 7 S / 1 X** | **0** (failing sets byte-identical) |

**Adjudication of the services partition (the only place with a head-only red):**

* Every red in **run 2** is A/B-identical at base → **zero change-caused reds**.
* The two extras in **run 1** (`test_work_resolver_badge_pushdown.py::test_page_completeness`, and its file-sibling `test_row_budget` which also failed at base) are **xdist-shard flakes**: that file is **7 passed** in isolation at HEAD **3/3 runs** AND at BASE **3/3 runs**. Not change-caused; not in the quarantine ledger (an un-ledgered flake family — see §5 note 3).
* `tests/unit/services/test_engine_diff_audit.py::…::test_daemon_diff_is_confined_to_ownership_map` — **PRE-EXISTING RED**, adjudicated by arithmetic A/B in the real repo (the `git archive` base leg cannot adjudicate it: the pin shells out to `git -C <repo_root> diff`, which fails in a gitless extract):

| rev | `git diff --name-only 8894f86a..<rev> -- daemon/` | offenders vs the 54-entry literal |
|---|---|---|
| BASE `04bc5e513` | 29 paths | **13** |
| HEAD `91900b21f` | 30 paths | **14** |
| delta | +`daemon/services/pok_service.py` | **+1 offender** |

The node was **RED at base** (13 offenders, all "owner: UNKNOWN") and is red after (14). This change **adds one path to an already-red release-blocking pin**; it does **not** flip a green node red. Routing: the engine-arc lane's documented re-anchor procedure (one reviewed literal line per sanctioned path) — see §5 note 1.

**Also verified (handoff-disclosed as unexecuted):** `scripts/pok/seed_llmproxy_payload.py` compiles clean, imports clean (with its own sibling dir on `sys.path`), sees the cap as **1024**, and its 8 `SEED_ENTRIES` are under the cap. The two daemon importers (`daemon/tools/pok.py`, `daemon/services/pok_injection.py`) auto-follow the constant — no second knob exists (no env-var / config.yaml / DB-backed variant).

---

## 5. DISCLOSED NOTES (non-blocking; neither is a defect of the cap raise)

1. **`engine_diff_audit` (Leg E, registered RELEASE-BLOCKING) is RED on latest and this change adds one offender to it.** The pin audits `git diff 8894f86a..HEAD -- daemon/` against a 54-entry ownership-map literal; 13 paths from OTHER lanes were already outside it at base (its registered last green was `45c6a1b6d`). This range adds `daemon/services/pok_service.py` → 14 offenders. The pin needs the engine-arc lane's re-anchor, not a POK-lane edit.
2. **Guideline line-anchor drift — ALREADY CURED UPSTREAM.** The tagged `v0.31.41` guideline cites `daemon/services/pok_service.py:104`, but the m1 comment rewrite moved the constant to **line 105** (and `POK_BLOCK_MAX_ENTRIES` from 122 → 123) — the m3 lane updated the VALUE and left the anchor stale. Independently found here, and independently cured upstream by `ff89fee99` ("re-derive drifted pok_service.py line anchors"), which landed in **v0.31.42** (`ea5325a6`, after the tag). No test depends on the anchor; the guideline itself pre-declares "verify by grep at use time — anchors drift". Worth noting for the release record that the **tagged** artifact carries the one-line miss.
3. **Un-ledgered flake family:** `tests/unit/services/test_work_resolver_badge_pushdown.py` flips under the `regression_unit_services` xdist partition (both nodes green in isolation, both sides). Not in `QUARANTINE.md`; a test-debt follow-up, not this lane's.

---

## 6. NEW GATE-VERIFIABLE PACK (registered)

`test/packs/pok_cap_read_budget_independence_test.sh` — ONE row in `.agents/tester/PACKS.md` (block `pok-cap-1024-budget-independence-pack-registration-20261123`), tagged `sonic`.
Six legs: **L1** constants · **L2** structural independence (+ grep non-vacuity) · **L3** behavioural measurement over a synthetic store (8 entries / 2,371 chars, byte-identical blocks in BOTH cap regimes) · **L4** anti-vacuity (the harness is proved live) · **L5** retired-value guards · **L6** the repo's committed pins.
Fences: per-variable `POSTGRES_*`/DSN `unset` (never the `env -u POSTGRES_*` glob no-op), `ENSEMBLE_SELF_ENV=dev`, `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0`, `PYTHONDONTWRITEBYTECODE=1`, in-memory SQLite only, dual-layer timeout (script-internal 280 s + command-level 300 s).
**Run: `RESULT: PASS` (rc=0)** — L1–L5 driver **25/25** checks green, L6 **3 passed**, measured **8 entries / 2,371 chars** at cap 64 and cap 4096.

---

## 7. VERDICT

```
TEST: PASS

Measured budget (the commission's demand):
  storage cap 64   ->  8 entries / 2371 chars   (sha256 b0d23ea3a4ee1f51…)
  storage cap 4096 ->  8 entries / 2371 chars   (sha256 b0d23ea3a4ee1f51…)  BYTE-IDENTICAL
  constants pinned: POK_BLOCK_MAX_ENTRIES == 8, POK_BLOCK_MAX_CHARS == 2_900

Pack tallies:
  pok_family_key_scoped_pack (6 files, pushed tip)   212 P / 0 F   rc=0
  pok_cap_read_budget_independence_test (NEW)          25 checks + 3 P / 0 F   rc=0
  pok_grant_pins                                     10 P / 0 F
  test_maintenancer_agent (pok_service importer)      70 P / 3 F  (3 = quarantine-ledged memory.md family)
  regression_unit_services  head 22F/4695P  vs  base 23F/4691P     net-new 0
  regression_unit_tools    head 24F/5764P  vs  base 24F/5764P     net-new 0

Failing node ids attributable to THIS change:  NONE
```

**The read-side injection budget is UNCHANGED and PINNED; the storage cap raise to 1024 is single-sourced, correctly pinned, mutation-proven, and regression-free across the wider registered unit suites.**
