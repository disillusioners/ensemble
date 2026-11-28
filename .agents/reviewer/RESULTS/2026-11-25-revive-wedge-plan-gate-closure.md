# Revive-Wedge Terminalization — Targeted Plan-Gate Closure Verification

**Date:** 2026-11-25T23:22Z (UTC)
**Verifier:** reviewer[v2] — fresh instance dispatched by the lane leader (tree root `30e9fe96-0c1a-4d97-a740-f2eecc7d7374`)
**Target:** plan set `.agents/shared/planning/revive-wedge-terminalization/` — plan.md 661 · design.md 533 · gate-matrix.md 471 · decisions.md 203 · release-staging.md 201 · observability.md 968 lines — plus the tracking file `.agents/approver/revive-wedge-terminalization-tracking.md` (143 lines before this pass's append).
**Base:** worktree `/home/nea/ensemble-src-wt-revive-wedge`, branch `fix/revive-wedge-terminalization`, HEAD `35ec50e3c9b3fc9a487e4f2dbad23c15a0837f45` (= `v0.31.54`). Plan set and tracking file are untracked; nothing was committed.
**Mode:** targeted verification of repair 006 (`plan.md §14.3`) against the approver's iteration-003 blockers — NOT a full re-review.

---

## Verdict

**GATE CLOSES.**

| Item | Result |
|---|---|
| Iteration-003 BLOCKING 1 — anchor parsing | **PASS** |
| Iteration-003 BLOCKING 2 — suppression-record emission point | **PASS** |
| Regression (iter-001 B1/B2, iter-002 B1/B2) | **4 / 4 remain cured** |
| Notes 9 and 10 | **both non-blocking** (ruled below) |
| Ledger diff (independent re-run) | **A 26/26 · B 8/8 · 0 mismatches** |
| Slot text | stale — 14 lines + 2 missing clauses; **not a blocker** (per dispatch) |

---

## Method (disclosed for adjudication)

- **Direct, read-only.** The commission forbade sub-agents, so my canonical worker-dispatch pattern was NOT used; the checks were performed directly. No plan edit, no code change, no commit / tag / branch / worktree, no build / test / DB contact, no instance lifecycle action, no revive, no config flip, no secret read or printed.
- **Order.** The tracking file was read first, as instructed — so the iteration-003 blocker text and the prior cure-status sections were known before the plan set was opened. This is a verification of a recorded repair against recorded blockers, not a blind fresh-eyes review. The prior reviewer record `.agents/reviewer/RESULTS/2026-11-25-revive-wedge-plan-review.md` was NOT opened.
- **Measurement.** Every `file:line` below was matched by content at `35ec50e3c` in the worktree. The canonical checkout `/home/nea/ensemble-src` was never read.
- **Network.** One read-only query: `git ls-remote --tags origin` (no fetch; no local ref changed).
- **Mechanical checks** ran as inline Python heredocs — nothing written to disk. Two of my own parse attempts mis-fired before the result reported under *Ledger diff*; they are disclosed there.
- **Budget.** 40 tool calls (limit 45).

---

## B1 — anchor-parsing contradiction + parse rule — PASS

| # | Check | Result | Evidence |
|---|---|---|---|
| 1 | `plan.md:119` re-scoped to the DELIVERY path | PASS | `plan.md:119`: "At base `35ec50e3c` NO consumer parses the anchor back out of `source` on the DELIVERY path (measured: zero `source.split` / `.split(":")` across `daemon/repositories/report_injection/`, `daemon/repositories/message_queue/`, `report_delivery_ledger.py` — and, at base, `child_reports.py`)". |
| 1a | the base-time measurement is still TRUE at base | PASS | Re-run: zero hits for `source\.split` / `.split(":")` across those four surfaces at `35ec50e3c`. |
| 1b | "this release creates the first anchor-parsing consumer" | PASS | `plan.md:119`: "**THIS release creates the FIRST anchor-parsing consumer:** the R-K2 / ARM-A / `retrying` observability records below … parse the anchor out of the blocking row's `source` in `child_reports.py`." |
| 2 | consumer is OBSERVABILITY-ONLY, never feeds `suppress_mint` | PASS | `plan.md:119`: "**OBSERVABILITY-ONLY** — its output reaches log lines only and **NEVER feeds `suppress_mint`**, the ARM-A/ARM-B classification, the CAS, the mint, or any other control decision". Same clause in the ledger rows `plan.md:333` / `design.md:434` and the pointer `decisions.md:151`. |
| 3 | normative rule is a `P-ANCHOR-PARSE` row in BOTH §7 ledgers | PASS | `plan.md:333` (§7 Component A, `:304-333`) and `design.md:434` (§7, `:403-434`). Rule text verbatim in both: `anchor = row.source[len(f"internal_report:{instance_id}:"):]` — PREFIX-STRIP, NEVER `.split(":")`; the degenerate source yields `""`. Canonical cells are character-identical (ledger diff below); the row sits last on both sides. |
| 4 | decisions §4.1 pointer | PASS | `decisions.md:140` (heading "4.1 PREDICATE LEDGER POINTER … + the anchor-parse rule") → row `decisions.md:151`; addendum `decisions.md:203`. |
| 5 | K12 mirrors re-scoped | PASS | `plan.md:432` (§9 K12), `design.md:482` (§8 K12), `decisions.md:80` — each now reads "no consumer parses the anchor back out of `source` on the DELIVERY path at base … THIS release adds the FIRST parse, observability-only in `child_reports.py` and by the `P-ANCHOR-PARSE` prefix-strip rule; it never feeds `suppress_mint`". |
| 6 | "the drive anchor is NOT changed" stands | PASS | `plan.md:362` and `design.md:450` (`P-DRIVE-ANCHOR` bullet): "**the drive path's anchor is NOT changed by this release** — this release only READS the stored anchor back, observability-only, under `P-ANCHOR-PARSE`". Re-measured list restated at `plan.md:549`. Every K12 line carries the same sentence. |
| 7 | the three `*_anchor` field definitions cite the rule | PASS | `plan.md:124` (`outstanding_anchor`), `:127` (`matched_anchor`), `:128` (`retrying_anchor`); `design.md:188`. |
| 8 | the rule is CORRECT against the real marker format | PASS | See below. |

**Rule vs code (item 8).** The source is built at `daemon/services/child_reports.py:4967` as `source=f"internal_report:{instance.instance_id}:{completed_message_id}"`. The guard selects by the prefix `f"internal_report:{instance_id}:"` (`:4681`), and `instance` is loaded by that same id (`:3673` `instance = session.get(Instance, instance_id)`; signature `:3283` takes `instance_id`, `completed_message_id`). Therefore `row.source[len(prefix):]` returns `completed_message_id` — the anchor — exactly. With `completed_message_id == ""` the stored source is `internal_report:{child}:` and the strip yields `""`. A colon-bearing anchor survives the strip, which is precisely what `.split(":")` would truncate.

Other writers of the three-segment form build it identically (`daemon/manager.py:13279-13281`, `:13737-13738`). The forms WITHOUT a trailing colon (`f"internal_report:{child}"` — `daemon/graph.py:9521`, `daemon/services/report_delivery_ledger.py:51`) are a different id space and cannot be selected by the colon-terminated prefix.

---

## B2 — suppression-record emission point — PASS

| # | Check | Result | Evidence |
|---|---|---|---|
| 1 | rules (i)–(v) byte-identical in the three places | PASS | `plan.md:130` (§2 bullet, inline), `design.md:250` (§3.1 step 4, inline), `plan.md:625-629` (§14.3 sub-bullets). Mechanical split on the `**(i)**`…`**(v)**` markers: per-rule length **249 / 187 / 445 / 292 / 78** characters on all three, equal including the trailing `;` / `.`. Only the bullet marker and separator whitespace differ. |
| 2 | defer-case NEW-7 limb (e) in `plan.md` §2 + pin table, `design.md` §2.6, `gate-matrix` §4 | PASS | `plan.md:131` (§2 pin bullet), `plan.md:168` (pin table row NEW-7), `design.md:215` (§2.6), `gate-matrix.md:264` (§4 = L3, heading `:247`). Each states: a defer emits NO suppress record and leaves the counter UNCHANGED. |
| 3 | "Exactly ONE … per ACTUAL suppression" | PASS | `plan.md:130` rule (i), `:131`, `:168`; `design.md:190`, `:215`; `gate-matrix.md:264`. The old wording "per suppressed invocation" survives only as the quoted subject of the reconciliation sentence at `plan.md:631`. |
| 4 | counter moves only on an R-K2 loss (`loss=1`) under `_ARM_B_SUPPRESS_LOCK` | PASS | `plan.md:125` (counter bullet), `:126` (lock), `:130` rule (iv), `:329`; `design.md:250` rule (iv), `:430`; `decisions.md:147` (emission clause); K10 at `plan.md:432` / `design.md:480`; `release-staging.md:189`, `:199`. |
| 5 | no stale guard-level emission instruction remains | PASS | The string "ARM-B branch" occurs ONCE in the set — `plan.md:638`, quoted inside the repair record. `plan.md:152`, `design.md:188`, `plan.md:177` (§3 step 4), `plan.md:292` step 4 and `design.md:392` step 4 all read "in the SUPPRESS BRANCH … never at the guard, never on the defer path". |
| 6 | code facts still hold at base | PASS | See below. |

**Code facts at base `35ec50e3c` (item 6, by content match):**

| Fact | Location |
|---|---|
| decision block; `should_send = False` | `child_reports.py:4691-4696` (assignment on `:4692`) |
| early return `if not should_send:` | `:4698` |
| active-children guard | `:4819-4828` |
| pending-task guard | `:4858-4878`; return block `:4873-4878` with `outcome="child_still_running_defer"` on `:4874` |
| fall-through CAS | `:4901-4917`; rowcount test `:4918` |
| mint | `:4959` (row built `:4960-4973`, `session.add` `:4974`) |
| commit | `:5762` |

Order: guard (`:4691`) → both defers (`:4874`) → CAS (`:4901`) → mint (`:4959`) → commit (`:5762`). The approver's finding therefore holds at base — a guard-level emit WOULD fire on the ordinary defer path — and the chosen emission point (suppress branch, after a successful CAS) sits after both defers and before the mint.

Note: the dispatch's "`should_send=False` at `child_reports.py:4691`" names the decision block's first line; the assignment is on `:4692`. The plan's own `:4691-4696` is the accurate citation.

---

## Regression — all four previously cured blockers REMAIN CURED

| Prior blocker | Result | Evidence |
|---|---|---|
| **iter-001 B1** — no pre-flight instruction in the work order; sweep file unmodified | **CURED** | `plan.md:280` ("READ-ONLY DEPENDENCY — NOT MODIFIED"), `:288` ("Explicitly NOT changed"), `:290` ("There is NO sweep step and NO second commit"), `:292` (7-step work order — no step names `stale_running_sweep.py` or a pre-flight). `design.md:374`, `:382`, `:384`, `:388-395` likewise. `git diff --stat 35ec50e3c -- daemon/services/stale_running_sweep.py` → empty. |
| **iter-001 B2** — NEW-8 propagated; L4 can go RED at base | **CURED** | `gate-matrix.md:36` (§0 file map), §5 L4 `:270-290` (Test file row `:279`; 4-step command; "Expected at BASE: RED on the NEW postgres pin", SKIP = leg FAILURE, mechanism stated; THREE PACKS rows), `:409-410` (chains). `plan.md:169` (pin table), `:285` (file table), §8 GAP-1 (heading `:406`). `design.md:216` (§2.6), `:378` (§6.1). Work-order step 6: `plan.md:292`, `design.md:394`. The RED-at-base mechanism holds against the code: at base the guard (`:4677-4690`, COMPLETED included) sees the DELIVERED prior report ⇒ `should_send=False` ⇒ early return `:4698` ⇒ no mint, no terminalization. The file is absent at base, as expected for a NEW file. |
| **iter-002 B1** — L2d two-site mutation with NEW-6a as target; L2a's RED target = the NIT-1 companion | **CURED** | `gate-matrix.md:221-228` (§3(d): site D1 `:3781-3784` + site D2 `:4918-4936`; expected RED = NEW-6a; NEW-6b stays GREEN; fixture preconditions stated), `:204` (§3(a) L2a expected RED = `test_prior_pending_report_under_a_different_anchor_blocks_a_second`). `plan.md:167` (NEW-6a / NEW-6b), `:292` step 6; `design.md:214`, `:394`. |
| **iter-002 B2** — colon-boundary pin in the L1 `$SUITE` with a PACKS row | **CURED** | `gate-matrix.md:109` (`$SUITE` ends with `tests/repositories/test_report_injection_prefix_boundary.py`), `:382` (PACKS row `revive_wedge_report_injection_prefix_boundary_unit`), `:407` (§8.3 chain row T-L1), `:38` (§0 file map, "rides L1"). `plan.md:292` step 7; `design.md:379`. |

---

## Notes 9 and 10 — rulings

### Note 9 — relative `*.reruns.out` path — NON-BLOCKING

- **Verified:** `gate-matrix.md:157` still appends to the relative path `"${side,,}.reruns.out"`.
- **Precise effect (measured):** the `>>` redirection belongs to the `(cd "$D" && …)` subshell, so the file is opened in the parent script's cwd — the last top-level `cd` before the loop is the L2N head-tree run, so the file lands in `$HEAD_WT`, as the approver said. TEARDOWN then force-removes that worktree (`gate-matrix.md:165-166`), which deletes the artifact unless it is read or copied first.
- **Why non-blocking:** the gate's merge criterion is "`comm -13` (net-new reds) empty" (`gate-matrix.md:143`, `:178`). That criterion does not depend on this file, so no pass or fail can turn on it. Diagnostic only — the approver's rating stands.
- **Exact cure (one line; fold at the next doc touch or apply as an execution note):** define `RERUNS_DIR="$(mktemp -d)"` in the §2.2 setup and redirect to `"$RERUNS_DIR/${side,,}.reruns.out"` — or copy the file out before the TEARDOWN block.

### Note 10 — stale `origin/latest` in the research file — NON-BLOCKING

- **Verified:** `research-findings-sweep.md:4` still reads "HEAD `35ec50e3c…` == `origin/latest`".
- **Why non-blocking:** it is the provenance line of a pre-decision research snapshot — true when the research was cut — not an instruction, and it carries no gate. It is superseded by the plan headers (`plan.md:3`, `design.md:3`, `decisions.md:3`, `gate-matrix.md:3`, `release-staging.md:3`) and `plan.md §14.1` (`:599`).
- **Cure:** none required for gate closure. Optional: a one-clause supersession note on that line.
- **Carry-forward:** the superseding headers are themselves now stale — measured local `origin/latest` = `4a17398d2` (untagged), not `ed8ab508c`. Listed under *Slot text*, group C.

---

## Ledger diff — independent re-run

**Method.** Fresh Python parse — the plan's comparison code was not reused (`plan.md:652` describes its method in prose only).

1. Locate each table by heading: `plan.md` §7 Component A (`:304`) and Component B (`:335`); `design.md` §7 (`:403`); `observability.md` §9 (`:847`).
2. Rows = lines starting with `|` up to the next `##` / `###` heading, minus header and separator rows.
3. Split cells on UNESCAPED pipes (`(?<!\\)\|`).
4. ID = cell 1 with backticks / bold / whitespace stripped. Keys = `[PS]-*`.
5. Compared unit = cell 2 (the canonical string) after `\|` → `|` and whitespace normalisation (`\s+` → one space).
6. The trailing kind / meaning column (`design.md`) and the row order are excluded from the equality test; order is compared separately.

**Result.**

| Component | Sides | IDs | Missing (L / R) | Canonical-cell mismatches | Order | Duplicate IDs |
|---|---|---|---|---|---|---|
| A — `plan.md` §7 vs `design.md` §7 | 26 / 26 | 24 `P-*` + `S-A` + `S-B` | 0 / 0 | **0** | identical | none |
| B — `plan.md` §7 vs `observability.md` §9 | 8 / 8 | `P-B1` … `P-B8` | 0 / 0 | **0** | identical | none |

`P-ANCHOR-PARSE` is present on both A sides at the same position (last row). This reproduces the repair record's claim (`plan.md:652`).

**Disclosure.** Two earlier parse attempts of mine were wrong. The first selected `plan.md` §6's `### Component A` heading (`:275`) instead of §7's (`:304`), so the plan side parsed empty. The second keyed on `P-*` only and dropped the `S-A` / `S-B` set rows, giving 24 / 24. The third run — the one reported — includes both rows.

---

## Slot text — lines that need updating (NOT a blocker; nothing edited)

**Measured at verification time:** remote tags — `v0.31.58` → `f03798d86` is present; `v0.31.59`, `v0.31.60`, `v0.31.61` are absent. Local tags run to `v0.31.58`. So `.60` is free today and `.59` is not yet pushed — consistent with "the false-halt cure lands FIRST".

### A. Slot literal — `v0.31.59` → `v0.31.60` (EXPECTED, re-derived live at cut time) — 14 lines

| File | Lines |
|---|---|
| `plan.md` | `:473` (ASSIGNED `v0.31.59`, "last slot of the current stack `.55`–`.59`") · `:482` (table row `\| **`v0.31.59`** \| **FREE** …`) · `:484` ("If `.59` is taken by then …") · `:502` (Keep-a-Changelog shape `## [v0.31.59]`) · `:534` (§13 item 4) |
| `design.md` | `:518` (D14) |
| `decisions.md` | `:20` (D14) · `:58` (V6 — "ASSIGNED `v0.31.59`", "**`.59` FREE**", "if `.59` is taken → take the next free slot") |
| `release-staging.md` | `:16` (Next-patch rule) · `:34` (`# W9 re-derive: … => $V = v0.31.59 (ASSIGNED)`) · `:35` (`V=v0.31.59`) · `:116` ("prior tag (e.g. v0.31.56 for $V=v0.31.59)") · `:171` (entries shaped `## [v0.31.59]`) · `:195` (honesty ledger) |

"ASSIGNED" should read "EXPECTED (front door 2026-11-25 ~21:45Z)" — it is not a reservation. The stack boundary `.55`–`.59` (`plan.md:473`, `decisions.md:58`, `release-staging.md:16`, `:195`) becomes `.55`–`.60`.

### B. Missing clauses — ADD (zero hits today for `false-halt` / `ref-contention` / `one lander` / `board claim` / `lands FIRST` / `cut ahead` across the five files)

1. **Content order** — `plan.md` §11, beside the W9 line (`:484`): the false-halt cure (`v0.31.59`) lands FIRST and this lane does not cut ahead. GATE: `v0.31.59` must be an ancestor of `latest` before this lane merges or tags — otherwise HOLD and report.
2. **Ref-contention protocol** — `release-staging.md:44-47` (step 2, MERGE) and `:49-53` (step 3, TAG), mirrored in `plan.md` §11:
   1. read the CURRENT local `latest` tip before merging;
   2. claim the ref on the board via a project-manager dispatch before merging (lane, target commit, window);
   3. ONE lander at a time on that ref;
   4. IMMEDIATELY before cutting the tag, re-read the tip and ASSERT it equals this lane's merged commit — if not, the merge was displaced: re-merge and re-derive the slot live.
3. `decisions.md` V6 (`:58`) — mirror 1 and 2 in substance.
4. `release-staging.md` §6 preconditions (`:151-168`) — add the ancestor gate as a precondition.

### C. Stale `origin/latest` statements — restate as history at the same touch

`plan.md:3`, `:478` · `design.md:3` · `decisions.md:3`, `:192` · `gate-matrix.md:3` · `release-staging.md:3`, `:16`, `:195`. Each says `origin/latest` = `ed8ab508c` = `v0.31.56`, "two tags ahead". Measured now: local `origin/latest` = `4a17398d2`, untagged. The base is still an ancestor of it. These are "at repair time" statements, so they are historically true; the merge-time re-read (B.2) is what binds.

### D. History lines — leave untouched

`plan.md:553` (§14.1: "Repaired to the assigned `v0.31.59`") and the tracking file's iteration-001 note (`:46`) record past repairs. Supersede them with a new dated note; do not edit.

---

## Out-of-scope observations (non-blocking; no action required for gate closure)

1. 🟢 `gate-matrix.md:152` — the "3× re-run of the diff reds" loop iterates the `comm -23` set (base-only = FIXED reds, per the script's own comment at `:142`), while the classification-ladder comment (`:161-162`) speaks of net-new reds — the `comm -13` set (`:143`). Not a false-pass risk: the merge criterion is "`comm -13` empty" (`:178`), which the loop does not touch, and re-running the base-only set 3× in isolation is itself a useful determinism check of the genuine-red-at-base claim. One clarifying clause would help: name the input set, and say that net-new ids are run manually.
2. 🟢 `plan.md:140` — the pseudo-code query uses `anchor_exact`, which the plan never assigns. The canonical expression is the `P-ARM-A-PY` row (§7). Define it once or reference the row.
3. 🟢 Optional hardening for the implementer — the prefix-strip slices silently on a row that lacks the prefix. The plan bounds the rule to rows already selected by that prefix (`plan.md:119`, `:333`); a `startswith` assertion in the helper would make that bound self-enforcing.

---

## Writes and fences

- **Written:** this file; a CLOSURE section appended to `.agents/approver/revive-wedge-terminalization-tracking.md` at `:147`, plus a one-paragraph ERRATUM at `:174` (two typographical slips in my own CLOSURE text — a stray path fragment, and a `\|` → `|` that printed as `\|` → `\|`; no result affected). Append-only: the first 143 lines — sha256 `ccee9bd8ef590889b46cf8291f3bb53068ac352edad20c3fb28f64910fdb124a` — are unchanged, verified after the appends. `active.md` untouched (mtime 07:44Z).
- **Not done:** no plan-set edit; no source change; no commit, tag, branch or worktree; no build, test or DB contact; no live instance contacted; no sub-agent spawned or revived; no secret read or printed; no config flip. The canonical checkout was never read.
