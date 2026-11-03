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
| 5 | **the mutation matrix** | 12 legs | **12/12 PASS** (RED via the intended assertion → byte-identical restore → GREEN) | — | green; the post-run manifest confirms every touched file is byte-identical |
| 5 | the unit shell pack | `test/packs/metrics_read_unit_test.sh` | **PASS (5 legs)** — 30 + 34 + 15 + 20 + 28 = 127 nodes | — | green |
| 5 | the mutation shell pack | `test/packs/metrics_read_mutation_legs.sh` | **PASS** (the matrix's own `RESULT: PASS` + the byte-identical-restore assertion) | — | green |

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
implementation time is **16 EDIT sites + 1 VERIFY-ONLY = 17 sites across 11 files**:

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

**No migration, no dependency, no configuration knob, no contract-wire change.** The migration
delta is EMPTY; `pyproject.toml` and `frontend/package.json` are untouched; `schema_version`
stays 1 and `vocabulary_version` is untouched by this lane.
