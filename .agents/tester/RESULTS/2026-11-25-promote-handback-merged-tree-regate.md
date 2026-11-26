# RE-GATE — promote hand-back, MERGED TREE F = 7ab616612

**Date**: 2026-11-25 · **Tester gate** (cold-spawn, operating freeze)
**Worktree**: `/home/nea/ensemble-src-wt-maintsys-cut2` (branch `latest`)
**Gated tree**: F = `7ab616612b96373100d54bfe1b3250676ac2fe79` (the fix merge)
**Final tree**: `8f7d864d824fb12b394f28ae707b50868262aa60` (= F + ONE test-only pin commit).
This report adds one further DOCS-ONLY commit on top (no production, no test-code change).

F = merge(`7795d48c2` refresh merge, `5f866fd41` C-1 fix tip).
`7795d48c2` = merge(`0fa06dce3`, `dd007f16d` pok-gate wave tip).
C-1 fix = `a231ba06a` (on `8d306f2e3` = the landing merge); hygiene A1/A5/A7 = `5f866fd41`.

---

## 0. VERDICT

**READY.** Every gate leg passed. NET-NEW = **0** against the first gate's baseline on
every pack. Two net-new reds that existed at the landing merge (`8d306f2e3`, the C-1
severed-continuation defect) are **cured and proven cured** by a RED@M → GREEN@F pair.
Both merges are clean (0 resolver-written lines outside the two doc files). Production
identity holds: `scripts/upgrade/lib.sh` and `scripts/upgrade/status.sh` are byte-identical
between the fix tip and the merge.

| Gate | Subject | Result |
|---|---|---|
| R1 | evil-merge on both new merges | ✅ 0 lines outside the 2 doc files (both merges) |
| R2 | cure matrix @ F / P1 / P2 | ✅ 62/62 @ F; cure legs RED (15/46) @ both bases |
| R3 | C-1 boundary: bounded-waits + harness | ✅ RED@M → GREEN@F on both surfaces |
| R4 | runtime leg + shell-continuation lint | ✅ 13/0 + PASS @ F; lint RED on the pre-fix blob |
| R5 | lane pack set (12 legs) @ F | ✅ NET-NEW 0; soak C6 green at cap ≥300s |
| R6 | two new pins + commit | ✅ `8f7d864d` — production untouched |
| R7 | post-commit: pins, P11 shape-2, lint | ✅ both pins green/discriminating; span NON-EMPTY |
| ensure | ensure.md Core gates | see §9 |

---

## 1. SCOPE DECISION

The commission enumerated the scope; I executed **all of it**, in full, and added three legs
of my own (each with a reason, none expanding into a suite run):

1. **The adjudication leg** (R7-adjudication) — two workers contradicted each other on whether
   the `soak_gate` pack loses its FAKE_HOME (the exact hazard class the commission named). I
   dispatched a focused read-only adjudication instead of reporting an unsettled claim.
2. **The soak shipped-cap leg** (R5b2) — the mandated cap override produced one red; the leg
   was missing the other half (the shipped-cap run) that proves the code itself is green.
3. **The ensure.md Core leg** — `concurrency_atomic_unit_test` + 3 static checks are ensure.md
   **Critical** and were absent from the commission's pack list, on a tree that carries
   daemon-side changes.

Not run (and why): `test_supervision_journal.sh` — OFF the gate set (fixture rot since
`ccb7962d5`; the pack certifies nothing about this landing). The five `por_*` packs and
`soak_gate_mcp_warmup_acceptance.sh` — audited, never executed (they are the defect subject;
running them would run known-unfenced packs).

---

## 2. R1 — EVIL-MERGE CHECK (independent of the giter's self-check)

`git show --remerge-diff <merge>`, counted by TWO independent methods (M-A = remerge-diff `+`
lines; M-B = content present in the merge result but in NEITHER parent), cross-checked:

| Merge | Files in remerge-diff | M-A | M-B | Outside the 2 doc files | Per-doc-file |
|---|---|---|---|---|---|
| `7795d48c2` | **0** (6-line output = header only) | 0 | 0 | **0** ✅ | — |
| `7ab616612` | **1** (the refresh-conflict-ledger) | 1 | 0 | **0** ✅ | ledger 1, PACKS.md 0 |

The single M-A line is a **byte-identical MOVE** of a parent line (the ledger's item-5 line
repositioned ahead of P1's §11 block), not resolver-authored content: the `+`/`-` pair hashes
to **one distinct sha256**. M-A counts a reorder as "written"; M-B counts only novel content.
Both agree on the material question: **zero resolver-authored content outside the two doc
files**. `PACKS.md` was auto-merged (three-way union) with zero conflict lines; union
completeness check: 0 added-content lines dropped.

**Production identity**: `git diff 5f866fd41 7ab616612 -- scripts/upgrade/lib.sh
scripts/upgrade/status.sh` → **0 bytes**. Independent blob proof: lib.sh `fc2c0f66…` and
status.sh `5dcce777…` at BOTH commits.

**A5 scope** (`git diff a231ba06a 5f866fd41`, 1 file, +11/−23): the `__soak_inconclusive`
counter de-duplication in `gate_soak()` — declaration ×2→×1 (:10192/:10196→:10194), increment
×2→×1 (:10271/:10272→:10269, i.e. 2N→N), cap-reach `if` ×2→×1 (:10402/:10411→:10399).
**The counter lives in `scripts/upgrade/lib.sh` ONLY** — `git grep -ln '__soak_inconclusive'`
returns exactly one file; `tests/test_adaptive_soak.sh` has **zero** occurrences, so the test
does NOT own or mirror it. The same commit's A1 renamed the journal kind `halt` → `gate_defer`
at the two `_gate_consume` sites.

---

## 3. R2 — CURE MATRIX (the harness pack at three commits)

Pack: `tests/packs/promote_handback_false_halt_repro_test.sh` (runnable at both revs — every
merge-only symbol is `declare -F`-guarded).

| Commit | RESULT | exit | wall | cure legs (a,b,c,d,e,f,g,h) | merge-defect legs (d2,d3) | total |
|---|---|---|---|---|---|---|
| **F** `7ab616612` | PASS | 0 | 51s | **46 / 46 GREEN** | **16 / 16 GREEN** | **62 / 62** |
| **P1** `35ec50e3c` (RED base) | FAIL | 1 | 19s | **15 pass / 31 FAIL → RED** | 16 / 16 green | 31P / 31F |
| **P2** `dd007f16d` (refresh 2nd parent) | FAIL | 1 | 17s | **15 pass / 31 FAIL → RED** | 16 / 16 green | 31P / 31F |

**Shell-noise counts (command-not-found / syntax-error / unbound-variable) = 0 / 0 / 0 in all
three legs** ⇒ the base legs are CLEAN reds (assertion failures, not shell noise).

**P2 ≡ P1, measured not assumed**: the two bases carry **byte-identical** `lib.sh`
(`edc148781f…`) and `status.sh` (`9cbdc107…`); the normalised outputs differ only in
ephemeral child PIDs. Independently confirmed by topology: `8d306f2e3` is **NOT** an ancestor
of `dd007f16d` (the cure is absent, as the commission premised) and **IS** an ancestor of F.

Base method: `git archive <sha> | tar -x -C $(mktemp -d)` (no worktree metadata). Only the 2
harness files were dropped in, sha256-verified against F; the bases' own production files were
never overwritten.

---

## 4. R3 — C-1 BOUNDARY (RED@M → GREEN@F on two surfaces)

**Surface 1 — the landed reader** `tests/test_bounded_subprocess_waits.sh`:

| Leg | counts | exit | wall | S5 | S5b | `R happy-path` |
|---|---|---|---|---|---|---|
| **F** | **74P / 1F** | 1 | 35.3s | green ✅ | green ✅ | red (pre-existing, host-class) |
| **M** `8d306f2e3` | **72P / 3F** | 1 | 36.4s | **RED** | **RED** | red (pre-existing) |

The M-leg failure text shows the C-1 signature verbatim: the journal row is
`{"event":"subprocess_wait_timeout","detail":""}` where `site=handback_start` was expected.

**Non-vacuity proof (the decisive one)**: the pack file is **byte-identical at M and F**
(blob `77232613…`, 0-byte diff) while `lib.sh` differs (`bf5cb77e…` → `fc2c0f66…`) ⇒ the
RED→GREEN is **production-driven**, not a changed test. SKIP=0 on both legs (host has
`/run/systemd/system`) ⇒ S3–S5/S5b/R genuinely executed.

**Surface 2 — the harness at M**: cure legs **46/46 GREEN**, merge-defect legs **3P/13F**
(d2 0/5, d3 3/11), total 49P/13F, `RESULT: FAIL`, exit 1 — byte-identical to the first gate's
baseline at M, so the merge-defect legs are pinned non-vacuously. d3 shows the real severity:
`lib.sh: line 1890: $2: unbound variable`, journal history `[]` — no row at all.

---

## 5. R4 — RUNTIME LEG + SHELL-CONTINUATION LINT

| Leg | Expected | Result |
|---|---|---|
| A — runtime leg @ F | `PASS=13 FAIL=0`, rc=0 | ✅ **13 / 0**, rc=0, 7.5s (summary line present ⇒ non-vacuous; the leg's built-in pre-fix reconstruction ran) |
| B — lint pack @ F | leg1 PASS (7 pinned / 0 new), leg2 13/0 | ✅ leg1 `RESULT: PASS`, census **7 occurrence(s), all [PINNED], 0 [NEW]**; leg2 `PASS=13 FAIL=0`; pack PASS rc=0, 9.5s |
| C — lint pack @ pre-fix `8d306f2e3` | leg1 FAIL (1 NEW @ lib.sh:7515), leg2 5/8 | ✅ leg1 `RESULT: FAIL — 1 NEW occurrence(s) … scripts/upgrade/lib.sh:7515 -> 7516`, census **8**; leg2 `PASS=5 FAIL=8`; pack `RESULT: FAIL (lint leg)` rc=1, 5.8s |

**The lint is discriminating**: it reproduces the ledger §10.1 pre-fix column exactly and the
post-fix column exactly. Method = temp tree (archive) + 3 files copied from F; **the F tree was
never modified** (no blob-swap, no restore proof needed). Subject identity proven by sha256:
base lib.sh == PREFIX blob (`b88522ab…`) ≠ F blob (`b44d8b80…`).

### LINT SCOPE (judgement — nothing fixed)

- **The 7 test-scaffolding occurrences are REAL DEFECTS, not intentional fixtures.** The
  truncating comments are explanatory (`# NOTE:` about pytest's `--ff` spelling in the 5
  `por_*` packs; an errexit-safety rationale in the 2 `soak_gate` sites) — an explanatory
  comment *accidentally pasted inside* a continuation block. A repo-wide search for a
  deliberate fixture of this shape returns zero additional hits.
- **They ARE the ledger's follow-up item 5** (refresh-conflict-ledger-2026-11-25.md:235, under
  "Follow-ups (routed, not executed here)"), which classifies them as real defects and states
  **"Separate commission required."** It was NOT executed by the C-1 lane.
- **Per-occurrence env compensation** (measured, not inferred): the severed `env` loses its
  COMMAND operand, runs bare (exit 0) and **prints the environment**, while the command after
  the comment runs as a separate statement inheriting the AMBIENT env.
  - 5 × `por_*`: the child loses the POSTGRES_* scrub, `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0`,
    `ENSEMBLE_SELF_ENV=dev`, `PYTHONDONTWRITEBYTECODE=1`, `PYTEST_TIMEOUT=280`.
  - 2 × `soak_gate`: the inline `HOME=` and the inline scrub are lost, but the pack's GLOBAL
    `export HOME="$SOAK_PACK_FAKE_HOME"` (helper `:110`) and `soak_pack_scrub_env` (`:63`)
    still apply ⇒ **net scrubbed, only the redundant inline layer is lost**.
- **THE 2026-10-12 LIVE-SHAPED-HOME HAZARD IS CARRIED BY THE `por_*` FAMILY, NOT `soak_gate`**
  (see §10 — adjudicated). The 5 `por_*` packs have **no FAKE_HOME mechanism at all**.
- **Extra findings beyond ledger item 5** (reported, not fixed): (a) the bare `env` **prints
  live credential values** (`SYSTEM_ENCRYPTION_KEY`, `EMBEDDING_API_KEY`,
  `LIGHTRAG_API_KEY`, `BLUEPRINT_EMBEDDING_API_KEY`) into pack stdout on every run;
  (b) the scanner's `_classify()` is **dead code** (1 reference = its own definition) ⇒ the
  lint has **no production-vs-test severity notion**; (c) a continuation followed by a **blank
  line** truncates identically but is **not covered** (measured: 0 occurrences today);
  (d) pins are by exact `path:line` ⇒ unrelated edits above a pinned line redden the gate.

---

## 6. R5 — LANE PACK SET @ F (the promote-machinery standard)

| # | Pack | At F | exit | wall | Baseline @M | NET-NEW | Verdict |
|---|---|---|---|---|---|---|---|
| 1 | `promote_handback_halt_gate_legs_test.sh` | **86/86** (6 legs) | 0 | 71s | 86/86 | 0 | ✅ |
| 2 | `test_launcher.sh` | **519P/0F/1S** | 0 | 85s | 519P/0F/1S | 0 | ✅ |
| 3 | `test_supervision_twins.sh` | **97P/0F** | 0 | 18s | 97P/0F | 0 | ✅ |
| 4 | `test_ceremony_wallclock.sh` | **50P/0F** | 0 | 4s | 50P/0F | 0 | ✅ |
| 5 | `test_hbk_err_inode_churn.sh` | **41P/0F** | 0 | 13s | 41P/0F | 0 | ✅ |
| 6 | P11 drift guard — **shape 1** @ F | **43P/1F/44** | 1 | 54s | 13P/1F/14 | 0 | 🟡 pre-existing red |
| 7 | P11 drift guard — **shape 2** @ `8f7d864d` | **43P/1F/44** | 1 | 70s | — | 0 | 🟡 pre-existing red |
| 8 | `handback_verify_budget_test.sh` | **120P/0F** | 0 | 170s | 120P/0F | 0 | ✅ mutation matrix executed |
| 9 | `promote_handback_halt_serving_green_test.sh` | **136P/4F** | 1 | 183s | 134P/6F | **0** | 🟡 registered `serving_green-4` |
| 10 | `test_supervision_stop_handback.sh` | **232P/33F/1S** | 1 | 104s | 232P/33F/1S | **0** | 🟡 registered `supervision-33` |
| 11 | `test_adaptive_soak.sh` (shipped cap) | **35P/0F** | 0 | 163s | 35P/0F | 0 | ✅ C6 PASSED |
| 12 | `test_adaptive_soak.sh` (cap 300, mandated) | 34P/**1F** | 1 | 172s | — | 0 | 🟡 override artifact — see §6.1 |
| 13 | `shell_continuation_comment_lint_test.sh` (incl. runtime leg) | **PASS / 13-0** | 0 | 9.5s | RED on pre-fix blob | 0 | ✅ |
| 14 | `promote_handback_false_halt_repro_test.sh` | **62/62** | 0 | 51s | 31P/31F | 0 | ✅ |

**NET-NEW = 0 on every leg.** The 4+33+1 reds are the registered pre-existing sets, proven
by an F-vs-M **failure-name set diff** (not a count comparison): serving_green
NET-NEW = ∅ (RESOLVED = `P38-2`,`P38-3` — F's `ControlGroup` mock), supervision_stop_handback
NET-NEW = ∅ and RESOLVED = ∅ with all 33 names listed.

### §6.1 The soak: the mandated cap override, and how it is resolved

The commission required cap ≥300s; the pack's shipped default is 120 (`ADAPTIVE_SOAK_CAP_S`).
Running with the override trips the pack's own shipped-vs-documented constant identity pin:

```
FAIL: ADAPTIVE_SOAK_CAP_S   expected: 120   actual: 300
```

**Resolution (both halves measured):** at the **shipped cap** the pack is **35P/0F, rc=0**
(= the first gate's baseline exactly) ⇒ **NET-NEW = 0 on the code**; at the **mandated
override** the single red is **deterministic** (not a flake) and **confined to that one
assertion** — all **14 sections identical** between the two runs apart from the P3 footer.
**C6 — the section the developer had deferred — completes and PASSES under the extended cap.**
The override stands (the commission mandated it); the shipped-cap run is the regression
evidence.

### §6.2 P11 drift guard — BOTH shapes (a landing gate must measure both)

| shape | HEAD | span | 6 merge-relative nodes | pytest |
|---|---|---|---|---|
| 1 @ F | `7ab616612` (HEAD *is* the merge) | `7ab616612..7ab616612` = **EMPTY** | **VACUOUS** — they judge nothing | 43P/1F |
| 2 @ `8f7d864d` | one commit above the merge | `7ab616612..8f7d864d` = **NON-EMPTY** | **TEETH ON** | 43P/1F |

Shape-2 proof (independently re-verified): `git rev-list --first-parent --merges -1 HEAD` =
`7ab616612`; the span is the single pin commit; the span's diff is 4 test/doc files with
**zero production paths**; and the span-diff over each judged path is **0 bytes** for all six
(`daemon/sources/cleanup.py`, `daemon/constants.py`, `daemon/manager.py`,
`daemon/services/scheduling_service.py`, `daemon/routers/sources.py`, `frontend/`) — and 0 for
`daemon/repositories/task/repository.py` as well.

The single red, both shapes, is the **pre-existing** `frontend/` allowlist pin: the landing
touches **zero** `frontend/` files, and the node's diff input
(`git diff --unified=0 858a85e31..<ref> -- frontend/`) hashes to the **same sha256
`93dc0d2c…`** at the base and at the head ⇒ base-attributed, net-new 0.

---

## 7. R6 — TWO NEW PINS (committed)

Commit **`8f7d864d824fb12b394f28ae707b50868262aa60`** on `latest` —
*"test: pin gate_defer stays non-terminal + soak tick counter increments once per tick"* —
**4 files, +770/−0**: `.agents/tester/PACKS.md` (+21),
`test/packs/journal_defer_kind_nonterminal_unit_test.sh` (+196),
`tests/packs/soak_tick_counter_unit_test.sh` (+306),
`tests/unit/tools/test_journal_defer_kind_nonterminal_pins.py` (+247).

**Production identity**: `git diff 7ab616612 HEAD --stat -- scripts/ daemon/` → **EMPTY**.

**Pin 1 — `gate_defer` must NOT be in `_TERMINAL_EVENTS`** (3 nodes + a pack wrapper).
Carries the standing contract (`journal-defer-kind-must-be-nonterminal`): a terminal event kind
closes the pending promote op and releases the arm; transient deferral kinds must stay outside.
Non-vacuity: collection cardinality + expected terminal kinds + the legal precedent asserted
in the definition source while outside the set. Discriminating control: a `commit`-kind row
**does** close the pending op; a `gate_defer` row does **not** (the journal stays byte-identical).
**Mutation proof**: against a scratch copy with `gate_defer` injected → **2 failed / 1 passed**,
quoting the real consequence *"a deferral row closed the pending op … terminal event
'gate_defer'"*; production module sha256 identical before/after.

**Pin 2 — the soak WARN tick count == the true tick count** (14 asserts).
Drives the **production** `gate_soak()` (not a copy); the TRUE tick count is counted by a
**stub outside the production code**, so a double increment cannot hide behind it; a
source-shape leg rides along (1 decl / 1 incr / 1 cap-if).
**Mutation proof**: against a scratch copy restored to the pre-A5 doubled shape → **WARN=12 vs
TRUE=6** (exactly 2×), production `lib.sh` sha256 `b44d8b80…` identical before/after.

`tests/test_adaptive_soak.sh` was **not** extended ⇒ the registered `adaptive_soak_rc2`
35-node count and its PACKS.md annotation stay intact.

---

## 8. R7 — POST-COMMIT VERIFICATION

| Leg | Result |
|---|---|
| `soak_tick_counter_unit_test` | ✅ **PASS=14 FAIL=0**, rc=0, 0.6s (leg1 `rc=2 true ticks=6 warn tick count=6`; leg3 `pre-A5: warn tick count=12` — detection proven in-pack) |
| `journal_defer_kind_nonterminal_unit_test` | ✅ **PASS=3 FAIL=0**, rc=0, 16.3s (leg2 mutation proof `2 failed / 1 passed`) |
| P11 drift guard — shape 2 | ✅ span NON-EMPTY, per-path span-diffs all 0 bytes; 43P/1F (the one pre-existing red) |
| shell-continuation lint @ new HEAD | ✅ census **7 occurrence(s), 7 [PINNED], 0 [NEW]** — the new pin files added no 8th occurrence; leg2 13/0; pack PASS |

**House rule holds**: 2/2 registered packs in the new PACKS.md block were executed by the
tester. No registered pack left unexecuted.

---

## 9. ensure.md CORE GATES

**Verdict: GREEN — 4 Critical + 2 Important + 1 Nice-to-have = 7/7 PASS, 0 real failures.**
(ensure.md is user-owned and read-only; it was validated, never edited.)

| # | Requirement (verbatim) | Severity | Method | Result | Evidence |
|---|---|---|---|---|---|
| 1 | "No regressions in changed packs — every pack in the blast-radius change set returns PASS" | Critical | not re-run (pre-satisfied by the lane-pack leg) | N-A | §6 — NET-NEW 0 on every leg |
| 2 | "Deadlock / concurrency integrity — pack `concurrency_atomic_unit_test` PASS (includes `test_deadlock_fix.py`, cascade races, observer race, instance/project atomic locks)" | Critical | registered pack `test/packs/concurrency_atomic_unit_test.sh` under the full fence | **PASS** | 94P/5F/74S, rc=1, 136 s — the 5F are the REGISTERED quarantine set, node-for-node exact |
| 3 | "No sync DB calls on the asyncio event loop — covered by `concurrency_atomic_unit_test`" | Critical | same pack (ensure.md: "Validation: pack PASS") | **PASS** | `test_deadlock_fix.py::TestProcessChildCompletionDbSyncOffloaded` (2 thread-identity nodes) PASS; 0 failures outside the quarantine set |
| 4 | "`dev.sh` includes `--timeout-graceful-shutdown 10`" | Critical | static grep + registered pack `dev_sh_static` | **PASS** | `dev.sh:159` — `--timeout-graceful-shutdown 10`; also asserted by `test/packs/dev_sh_static_unit_test.sh` |
| 5 | "All callers of converted async functions properly await (`_get_system_prompt_tokens`, `_compute_context_usage`, `get_queue_stats`)" | Important | **AST sweep** (no registered invocation exists — notice 1 below) | **PASS** | 10 production call sites, **10/10 awaited, 0 smells** (instance_messaging x4, compact_executor, manager, routers/instances x2, routers/messages, room_delivery, tools/instance) |
| 6 | "Original deadlock scenario (parent->child->complete) works without blocking" | Important | covered by #2's pack | **PASS** | `test_deadlock_fix.py` PASS |
| 7 | "No dead code from the fix (deleted code was truly unused)" | Nice-to-have | import/compile + symbol-presence | **PASS** | the 3 daemon modules this merge introduced import clean (Python 3.14.7); 277 refs / 0 missing; `bash -n scripts/upgrade/*.sh` rc=0; `py_compile` rc=0 |

**Quarantine adjudication (requirements 2/3):** the 5 failures are
`tests/test_instance_metadata_atomic.py` x5, all `sqlalchemy.orm.exc.DetachedInstanceError` —
machine-diffed node-for-node against the QUARANTINE.md Active row (located by content; the file
has no id column): **in-quarantine-but-not-observed: NONE · observed-but-not-in-quarantine: NONE
=> 5/5 exact, ZERO failures outside the registered set.** Per ensure.md's quarantine-aware rule
these do NOT fail the requirement. This is the recorded ALTERNATE baseline 94P/5F/74S.

### ensure.md Improvement Notices (ensure.md is user-owned — NOT edited)

1. **Core #5's validation method has no backing script.** The requirement says "Validation: grep
   / static check", but no registered await-sweep invocation exists in PACKS.md (rows `:2485`,
   `:2563`, `:2816`, `:3333` are ad-hoc descriptors with no script; `:412` is evidence, not an
   invocation). The method that actually works is an **AST sweep** (parent-node `Await`
   detection): grep mis-resolves `self._manager.get_queue_stats(...)` and false-positives on
   `dict.get`. Suggested rewrite: "Validation: AST await-sweep (register an invocation)".
2. **`concurrency_atomic_unit_test.sh` can never print `RESULT: FAIL`.** The pack uses
   `set -euo pipefail` with an **unguarded** `EXIT_CODE=$?`, so on a red it exits 1 with **no
   RESULT line at all** (`PACK_RESULT_LINE_COUNT=0` measured) — the verdict had to be taken from
   the exit code. Same class as the documented `compaction_unit_test.sh` defect. A gate that
   requires a RESULT line (the house vacuous-verdict guard) cannot read this pack's verdict.
   POK: `pack-result-line-unreachable-concurrency`.

**Scope note on fence compliance:** a sibling lane (`pok-gate-timeout`, PID 542115) was reading
`/home/nea/agents-ensemble/releases/state.json` during this gate's window (state.json mtime
14:48:17). **That is not this gate's footprint** — `.launcher-state` (byte-identical before and
after) and the `current` symlink (-> releases/v0.31.52) are unchanged, and the live daemon
(PID 2865772) was untouched. Flagged so the fence-compliance claim is read with its correct
scope: it covers THIS gate's lanes, not concurrent lanes on the same host.

---

## 10. R7 FENCE AUDIT + THE ADJUDICATED HAZARD PICTURE

**Per-pack fence status** (audit; NO pack's own fence was edited — separate commission):

| Pack | (a) FAKE_HOME | (b) POSTGRES scrub | (c) boot-sweep=0 | (d) SCRIPTS_DIR scrub |
|---|---|---|---|---|
| `promote_handback_halt_gate_legs_test.sh` | **YES** (mktemp + escape guard + live-install guard) | **YES** (per-var) | NO | NO |
| `test_launcher.sh` | NO (sources the fence lib, never calls it) | NO | NO | NO |
| `test_supervision_twins.sh` | NO (scratch dir only) | NO | NO | NO |
| `test_ceremony_wallclock.sh` | NO (same as launcher) | NO (documented, never applied) | NO | NO |
| `test_hbk_err_inode_churn.sh` | **YES** (setup_fake_home + preflight_guard) | prod-ABORT guard, not a scrub | NO | NO |
| `test_adaptive_soak.sh` | N/A (stub harness) | N/A | N/A | N/A |
| `handback_verify_budget_test.sh` | **YES** | caller-dependent | NO | NO |
| `promote_handback_halt_serving_green_test.sh` | NO | NO | NO | NO |
| `test_supervision_stop_handback.sh` | NO | NO | NO | NO |
| P11 pytest | N/A (no FS fixture) | invocation's job | invocation's job | invocation's job |

**Re-fenced at RUN TIME (every pack, every leg):** mktemp FAKE_HOME + escape guard; per-variable
`POSTGRES_*`/`PG*`/`DATABASE_URL` scrub (never the `env -u POSTGRES_*` glob idiom, which is a
silent no-op); `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned explicitly (never `env -u`'d);
`ENSEMBLE_UPGRADE_SCRIPTS_DIR` unset; `PORT`/`DAEMON_PORT` unset; `ENSEMBLE_SELF_ENV=dev`
(the host ambient is `live`); `PYTHONDONTWRITEBYTECODE=1`; and — added by the lane worker —
**`unset ENSEMBLE_LAUNCHER_STATE_FILE`**, whose ambient value pointed **into the live install**.

**The six developer-flagged packs (anomaly 6349def3)** — none is in this gate's set; audited
only, never executed:

| Pack | Defect | Consequence (adjudicated) |
|---|---|---|
| 5 × `test/packs/por_*.sh` | `PYTEST_TIMEOUT=280 \` + comment-only line (:133–:140) | scrub + boot-sweep pin + SELF_ENV + timeout **all lost**; **NO FAKE_HOME AT ALL** ⇒ **the 2026-10-12 live-shaped-HOME hazard IS armed** for these five in standalone use |
| `tests/packs/soak_gate_mcp_warmup_acceptance.sh` | `HOME="$SOAK_PACK_FAKE_HOME" \` + comment (:389, :442) | inline layer lost; **effective FAKE_HOME + POSTGRES scrub SURVIVE** via the helper's global export / `soak_pack_scrub_env` ⇒ hazard **NOT armed**; only defense-in-depth lost |

**Adjudication (two credible workers contradicted; resolved by a third read-only leg):** the
helper `tests/helpers/soak_gate_pack_common.sh:110` does `export HOME="$SOAK_PACK_FAKE_HOME"`;
the pack **sources** it at `:54` and calls `soak_pack_fake_home` at `:62` — **before** the
truncated blocks at `:388/:441/:481` — so the export lands in the pack's own shell and reaches
every child. **The commission's premise ("the soak_gate case drops the mktemp FAKE_HOME pin") is
imprecise**: the *inline* pin is dropped; the *effective* one survives. The hazard is carried by
the `por_*` family.

**Host fact (the part that matters today):** `POSTGRES_DB/HOST/USER/PASSWORD/PORT` and
`DATABASE_URL` are **ABSENT** from this host's ambient (they live in `.env`; the daemon process
environ carries none) ⇒ the live-prod contact hazard is **LATENT, not armed**, and becomes armed
only if a lane sources `/home/nea/agents-ensemble/.env`. `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0`
**IS** present in the ambient here, so the `por_*` boot-sweep loss is currently absorbed by the
host — fragile and host-dependent.

---

## 11. FENCE COMPLIANCE

- **Never** read / `cd`'d into / ran anything under `/home/nea/ensemble-src` (detached HEAD,
  drifts by design). The three forbidden fix worktrees (`…-wt-fix-promote-handback`, `…-wt-hbfix`,
  `…-wt-c1fix`) were never touched.
- **No push. No promote. No live-install mutation** — the live install's `.launcher-state` mtime
  is `2026-11-23 16:44:36.521921185 +0000` before AND after every leg.
- **No branch/tag deleted or renamed; no amend; no rebase; no force; no lock-file removal; no
  `--prune`; no `git gc`** (every git call prefixed `-c gc.auto=0`). One commit added on
  `latest` (`8f7d864d`), **unpushed**.
- **FAKE_HOME + escape guard** on every sandbox; no live-shaped HOME-derived install path
  resolved; no `rm -rf` outside a temp dir.
- **Ports**: 8088 / 8079 / 9797 never bound or touched; mock/ephemeral ports > 10000 only.
- **Worktree**: `git status --porcelain` empty before and after every leg (checked per leg by
  each worker) — zero cross-contamination between the parallel legs.

### Disclosed deviations (all in the safe direction, all reported)

1. **R3 worker's first command** used `source` in a dash shell ⇒ failed before executing;
   ambient `HOME=/home/nea` and `ENSEMBLE_SELF_ENV=live` were visible at that instant. It was
   read-only; no mutation, no prod contact. `ENSEMBLE_SELF_ENV` was then added to the fence.
2. **R5a worker's first continuation-semantics repro** ran a bare `env` (no operand) and
   **dumped the ambient environment to stdout**, placing ambient secrets (incl. the live proxy
   key) in that transcript. No value was reproduced; no secret reached a file, the repo or the
   tree. The decisive repro was re-run under `env -i` (secret-free). **The F7 hygiene debt
   class** — flagged, not hidden. All later workers were instructed never to run a bare `env`.
3. **R4 worker added `ENSEMBLE_SELF_ENV=dev`** to the fence (tightening; the ambient was `live`).
4. **R5a worker added `unset ENSEMBLE_LAUNCHER_STATE_FILE`** (the ambient pointed into the live
   install) and `PYTHONDONTWRITEBYTECODE=1`.
5. **R5b2 worker's first timing** used bash's `SECONDS`, which does not auto-increment under
   `/bin/sh` (dash) ⇒ returned `0s`. Caught with a 3s control; re-measured with `date +%s.%N`.
   Its discarded-timing run also returned 35/0 rc=0 ⇒ the shipped-cap green is reproducible.
6. **R7 worker's first landing-frontend check** used a mistyped SHA ⇒ git rejected it and the
   `0` it printed was an empty-stream artifact, NOT evidence. It re-ran with the resolved SHA;
   the reported EMPTY result comes from the corrected command. (False-evidence class, self-caught.)
7. **Two of MY OWN briefs were defective and the workers caught them** — (a) I told the R6
   worker to assert `sweep_deferred` IS PRESENT in `_TERMINAL_EVENTS`; that contradicts the
   standing contract (deferral kinds must stay OUTSIDE it) and the mirrored assertion at
   `test_upgrade_serving_green_closer.py:715`. The worker refused, reconciled it, and
   disclosed. (b) I gave the R7 worker an ASSUMED list of judged paths
   (`daemon/repositories/task/repository.py`); the test file judges a different six-path set.
   The worker verified emptiness for BOTH sets and reported the divergence.

---

## 12. RESIDUAL RISKS & ROUTED FOLLOW-UPS

1. 🟠 **The `por_*` pack family is unfenced and off this gate's set** — no FAKE_HOME, scrub and
   boot-sweep pin lost, ambient HOME inherited in standalone use ⇒ the 2026-10-12 live-install
   hazard is ARMED for those five. Ledger follow-up item 5 ("separate commission required").
   **These packs were NOT run by this gate** — a pack that would run its pytest child against
   ambient `POSTGRES_DB` is exactly what this gate refused to run. If they are ever run, fence
   at run time.
2. 🟠 **Credential values printed to pack stdout** by the severed bare `env`
   (`SYSTEM_ENCRYPTION_KEY`, `EMBEDDING_API_KEY`, `LIGHTRAG_API_KEY`,
   `BLUEPRINT_EMBEDDING_API_KEY`) on every run of the 5 `por_*` packs. This is a distinct
   hazard from the fence loss and touches the standing deferred key-rotation item. **Routed,
   not fixed** (separate commission). Recorded in POK `por-pack-env-scrub-truncation-hazard`.
3. 🟠 **The lint has no severity notion** — its `_classify()` is dead code, so a production hit
   and a test hit are the same RED; and it has a blind spot for a continuation followed by a
   **blank line** (0 occurrences today). Its `path:line` pinning is brittle: an unrelated edit
   above a pinned line reddens the gate.
4. 🟡 **P11 shape-1 is inherently vacuous at a merge** (6/14 nodes judge nothing) — the gate
   must always measure the above-merge shape too; this gate did (shape 2, span non-empty).
5. 🟡 **The `frontend/` allowlist pin is NOT in QUARANTINE.md** — it lives in the tester RESULTS
   ledger + `PACKS.md:372`. The commission described it as quarantined; it is not. Reported, not
   changed (QUARANTINE.md is not mine to edit here).
6. 🟡 **`gate_defer_reason` family remains DORMANT** on both sides (defined, unwired — ledger
   §10 #4); the harness pins its presence, not its wiring.
7. 🟡 **The 12 out-of-scope gate call sites** in `promote.sh`/`restart.sh`/`rollback.sh` still
   treat any non-zero as terminal (unchanged by this lane).
8. 🟢 **P11's 44-node file vs the "14-node" baseline** — the specified command runs the whole
   file; the 14 is the `TestP11BootNoBehaviorChange` class. The class subset (13P/1F/14) matches
   the baseline exactly; the extra 30 repository nodes are all green. A measurement note, not a
   defect.
9. 🟡 **F7-class exposure occurred in this gate's own transcripts** (a worker's bare `env`
   repro) — no rotation is warranted on this evidence alone (no value reproduced, nothing
   written), but it is the standing hygiene debt class.

---

## 13. WORKER INSTANCES

| Leg | Worker | Skill |
|---|---|---|
| R1 evil-merge + production identity + A5 | `regate-r1-evilmerge` | none (git infra) |
| R2 cure matrix | `regate-r2-cure-matrix` | test-pack-execution |
| R3 C-1 boundary | `regate-r3-c1-legs` | test-pack-execution |
| R4 runtime leg + lint | `regate-r4-runtime-lint` | test-pack-execution |
| R5a fast lane packs | `regate-r5a-lane-fast` | test-pack-execution |
| R5b soak + verify-budget | `regate-r5b-lane-soak` | test-pack-execution |
| R5c serving-green + supervision | `regate-r5c-lane-serve` | test-pack-execution |
| R7-adjudication (soak FAKE_HOME) | `regate-r7-soak-fakehome-adjudication` | none (read-only analysis) |
| R5b2 soak shipped-cap closure | `regate-r5b2-soak-shipped-cap` | test-pack-execution |
| R6 two pins + commit | `regate-r6-two-pins` | quick-fix |
| R7 post-commit verify | `regate-r7-postcommit-verify` | test-pack-execution |
| ensure.md Core | `regate-ensure-core-gates` | ensure-validation |
