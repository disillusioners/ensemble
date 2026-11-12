# DQ6 — the consumer-gate artifact (PINS-ONLY)

> **Ownership:** **Phase 2 owns this artifact** (pins-only) — arch §6
> ("Ownership: Phase 2 owns the artifact (pins-only); Phase 5 T1
> cross-references") and §15 B15 ("Phase 2 owns the event HOOK, the DQ6
> consumer-gate artifact (pins-only), …").
>
> **Phase 5's duplicate claim collapses to a one-line cross-reference.**
>
> **THE TABLE IS THE ARTIFACT.**  All counts are DERIVED conveniences,
> re-computed by grep at fold time and **never pinned** (arch §6).  This
> file records the census **as of the fold** — the anchors are verified
> against the tree the Phase-2 branch was built from; a later phase that
> re-derives them must NOT treat a count here as a pin.

**Binding source:** `architecture-recommendation.md` §6 (the census),
§1.1 P6 (the chart-tools pack is NOT the DQ6 gate), §15 B13 (the census
is pinned verbatim; counts never pinned).  Where this file and that
document disagree, **that document wins**.

---

## 1. The census (arch §6, verbatim)

Across the enumeration: ≈54–55 assertion sites in 10 test files; 1
inapplicable legacy pack; 0 daemon-side parsers.  The "22 consumers"
claim is **withdrawn**.

| # | Consumer surface | file:line | Breaks? |
|---|---|---|---|
| 1–7 | proc_status substrings + the attribute pin | `tests/tools/test_proc_tools.py:240, 257, 382, 1163, 1166-1167, 1233, 1831` | NO (additive; status stays `"killed"`) |
| 8–13 | `info.status == "killed"` group | `tests/tools/test_proc_tools.py:1614, 1655, 1692, 1944, 1992, 2038` | NO |
| 14 | proc_list header | `tests/tools/test_proc_tools.py:664` | NO |
| 15–29 | bash success-string group (`"EXIT CODE: N"`) | 8 files (≈30 sites) | NO — the RAM arm preempts the success arm entirely, only for a RAM-killed command |
| 30–33 | bash `"STDOUT:"` group | 4 files (≈10 sites) | NO |
| 34 | bash timeout string | **ZERO pins** (producer `bash.py:1160`) | NO pin exists; byte-identical only when no breach was observed |
| 35 | chart-tools legacy | `tests/test_chart_tools_legacy_error_contract.py` | INAPPLICABLE |
| 36 | daemon-side `json.loads` of a bash/proc return | NONE | — |
| 37 | daemon-side proc consumers | `proc_tools.py:2162`, `:2231` | NO |
| 38 | LLM/agent-facing | 7 agent docs; none parse the strings | NO (additive) |

**Load-bearing rules:** the four never-changing strings (bash success, bash
timeout when no breach, the `proc_status` block, the proc_list header);
`pid_registry.VALID_EXIT_KINDS` extended with the `ram_killed` constant.

---

## 2. The fold-time derivation (NOT pins)

Every row was re-verified by grep against the Phase-2 worktree on
**2026-11-11** (branch `feature/ram-auto-termination` @ 2612ae5ba, base
`origin/latest@0efdf106f`).  **The counts are derived conveniences — they
are never pinned, and a drift in them is a fold-time refresh, not a
defect.**

| Row | Derivation (the command that produced the number) | Fold-time result |
|---|---|---|
| 1–7 | `sed -n '240p;257p;382p;1163p;1166p;1167p;1233p;1831p' tests/tools/test_proc_tools.py` | 7 proc_status / attribute assertion sites, each on a `status: …` substring or `info.status` — **all confirmed in place** |
| 8–13 | `sed -n '1614p;1655p;1692p;1944p;1992p;2038p' tests/tools/test_proc_tools.py` | 6 × `assert info.status == "killed"` — **all confirmed in place** |
| 14 | `sed -n '664p' tests/tools/test_proc_tools.py` | the `process_id | status | command | uptime` proc_list header — **confirmed** |
| 15–29 | `grep -rl "EXIT CODE:" tests/ \| wc -l` · `grep -rn "EXIT CODE:" tests/ \| wc -l` | **7 files / 30 sites** (the arch's row says "8 files (≈30 sites)" — the SITE count matches; the FILE count folds to 7) |
| 30–33 | `grep -rl "STDOUT:" tests/ \| wc -l` · `grep -rn "STDOUT:" tests/ \| wc -l` | **4 files / 10 sites** — matches |
| 34 | `grep -rn "Command timed out" tests/ \| wc -l` | **0 — the ZERO-PIN FACT HOLDS.** The only occurrence in the tree is the **producer** `daemon/tools/bash.py:1160` (`return content + f"ERROR: Command timed out after {timeout} seconds"`). ⇒ the bash timeout string is guaranteed byte-identical **only when no breach was observed** |
| 35 | the file exists and pins chart tools only | **INAPPLICABLE** — it is NOT the DQ6 gate (arch §1.1 P6) |
| 36 | `grep -rn "json.loads" daemon/tools/bash.py daemon/tools/proc_tools.py \| wc -l` | **0 — NONE** |
| 37 | `sed -n '2162p;2231p' daemon/tools/proc_tools.py` | `manager.get_status(...)` / `manager.list_processes(...)` — the only daemon-side proc consumers; both **additive-safe** |
| 38 | `grep -rlE "EXIT CODE\|STDOUT\|proc_status\|status: killed" agents/ --include="*.md" \| wc -l` | **6 agent docs** (the arch's row says "7 agent docs") — none of them PARSE the strings |

---

## 3. The previously-uncited pins (recorded here, ONCE)

* `tests/tools/test_proc_tools.py:1233` — the **attribute pin**:
  `assert info.status in ("exited", "killed")`.
* The six `info.status == "killed"` attribute pins:
  `:1614, :1655, :1692, :1944, :1992, :2038`.
* The **zero-pin** fact for the bash **timeout string**: `grep -rn "Command
  timed out" tests/` ⇒ nothing; the only occurrence is the producer
  `daemon/tools/bash.py:1160`.  It is therefore guaranteed byte-identical
  **only when no breach was observed**.

---

## 4. The withdrawn claim

The **"22 consumers"** consumer-count claim appears **NOWHERE** in the plan
set (arch §6: "The '22 consumers' claim is withdrawn").  Do not re-derive it;
do not reinstate it.

---

## 5. The load-bearing rules this artifact protects

1. The bash **success** string (`EXIT CODE: N`) — unchanged unless the RAM
   arm preempts it entirely (a RAM-killed command only).
2. The bash **timeout** string — byte-identical whenever no breach was observed.
3. The **`proc_status` block** — the RAM arm is ADDITIVE (a `ram_breach:`
   line + a flat `ram_killed: true`); the block is never replaced.
4. The **proc_list header** — unchanged.
5. `pid_registry.VALID_EXIT_KINDS` gains the `ram_killed` constant (Phase 3's
   seam — the constant is Phase 3's, the enumeration above is Phase 2's).
