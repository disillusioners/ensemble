# Phase 3 — Rider (launcher cooldown) + Consolidated Test-Surface Map + Payload/Scripts-Pin Deployment Mapping

**Commission:** `promote-handback-halt` (incident `r-20261101-212542-8279`, target v0.28.5)
**Plan set:** `.agents/shared/planning/promote-handback-halt/` — Phase 3 of 3
**Base tree:** worktree `/home/nea/ensemble-src-wt-promote-handback-halt`, branch `fix/promote-handback-halt`, base `941cb6321` (== `origin/latest` tip at lane creation)
**Artifact status:** PLANNING ONLY. No code modified. No `launcher.sh` / `promote.sh` / `lib.sh` executed. No live-install (`/home/nea/agents-ensemble`) contact.

> **ID namespace note.** Phase 1 and Phase 2 were being written in parallel and were not present in the tree when this file was first authored (`find` over `/home/nea` returned no `promote-handback-halt` planning dir); they landed during this phase's work and have now been **read and reconciled against** (see §4.4). To avoid a numbering collision, this file uses a **phase-scoped namespace**: `P3-D#` (decisions), `P3-R#` (requirements), `P3-T#` (tasks), `AC-P3-#` (acceptance criteria — matching Phase 1's `AC-P1-*` convention). Phase 2 uses unprefixed `T1…Tn` / `P-1…P-12` and Phase 1 uses `L1…L4`; **those are preserved and cross-referenced, not renumbered.** The consolidating worker should keep the `P3-` prefix (see Open Question P3-Q10) and must **append** rather than renumber if it flattens the set.

---

## 1. Objective

Phase 3 delivers three artifacts that the rest of the commission depends on:

1. **The RIDER** — the launcher-side rollback-cooldown defect (`launcher.sh:474`, BSD-only `date -jur`), its fix, and a launcher-side pin set that proves the fix on both `uname` branches. This rider sits in the *same disposition chain* that Phase 2 modifies (the launcher sweep's AUTO-ROLLBACK path), so it must land with Phase 2, not after it.
2. **The consolidated TEST-SURFACE MAP** — every pin the commission will write, across all three parts, mapped to its owning part, file, harness seam, gate tier, what it proves, and its mutation-verification expectation. This is the single artifact the tester builds the gate legs from.
3. **The PAYLOAD / SCRIPTS-PIN DEPLOYMENT MAPPING** — the verified two-sided split of what reaches live and how (release payload vs. frozen binary vs. source-less ceremony scripts), and the **two-sided release-bump requirement** (the daemon-side floor constant and the scripts-side stamp must be set to the *same* merge commit) that the giter lane must execute at stage time — plus the operator-owned `.env` pin advance that remains a ceremony precondition.

---

## 2. Background (incident `r-20261101-212542-8279`, 2026-11-01)

A promote of v0.28.5 flipped the release, then the hand-back verifier aborted at a fixed 180 s budget while the daemon was still finishing its ~3 min unit-active→serving boot (MCP warmup + auto-continue resume). The halt path marked v0.28.5 **QUARANTINED**; at 22:25:53Z an operator restart ran the launcher's boot sweep, which found the stale flipped txn (age 3540 s, computed — **not** a constant) and took the **AUTO-ROLLBACK** path, leaving live on v0.28.4 with v0.28.5 quarantined — while v0.28.5 was serving all-green (livez + readyz receipts at 21:49:50Z). The subsequent re-promote was refused: *"version 'v0.28.5' is QUARANTINED — cleared only by re-staging"*.

Binding owner ruling (Discord 2026-11-01, RATIFIED): **"Don't roll back if that isn't needed — when the new version is working and living, there is no reason to roll back."** ⇒ disposition principle **SERVING-GREEN = STAY (commit-close)**; rollback is reserved exclusively for genuinely unhealthy serving. Ari's composition requirement: the serving-green protection **composes with** the house never-green protection — **both** directions must be pinned.

---

## 3. Scope

### In scope
- The `launcher.sh:474` cooldown defect: diagnosis, fix design (two candidate forms), recommendation, and the launcher-side pin set (arm selection on both `uname` branches + a born-future assertion + a cross-component consumer round-trip + a static no-bare-BSD-date backstop).
- The **consolidated test-surface map** covering Part 1, Part 2, and Part 3 pins, with tier assignment against `.agents/tester/rules/ensure.md`, harness seams, and mutation expectations.
- The **deployment mapping**: what ships in the release payload, what ships in the frozen binary, what is source-less, and how each reaches live.
- The **two-sided release-bump requirement** (`PIPELINE_SCRIPTS_FLOOR_COMMIT` ⟷ `PIPELINE_SCRIPTS_REV`) named as a first-class, gate-enforced obligation, plus its pack-level proof.
- The operator-owned `.env` `ENSEMBLE_UPGRADE_SCRIPTS_DIR` advance, stated as a **ceremony precondition** (not this lane's work).

### Out of scope
- Phase 1 (hand-back verify budget / serving-check conjunct) design and Phase 2 (split closer) design — Phase 3 only *maps* their pins.
- Any implementation, any code edit, any staging, any promote.
- The R-10 wedge-capture `pg_class` census defect, the R-9 asyncpg idle-in-txn reap, the DB-pool follow-up, and the proxy-side coding-alias defect — all routed elsewhere.
- The `_js_count_rollback` **unrecognized-platform** arm policy in full — surfaced as Open Question P3-Q2 with a recommendation (it is a policy choice, not a mechanical fix).

---

## 4. Design

### 4.1 The RIDER — launcher-side rollback cooldown (BSD-only `date -jur`)

#### 4.1.1 The defect (verified)

`launcher.sh:459-478` — `_js_count_rollback <path>`:

```
459: _js_count_rollback() {
460:     local jp="$1" json counts cnt wstart wstart_epoch new_cnt until
...
467:     if [ -n "$wstart" ] && wstart_epoch="$(_js_iso_to_epoch "$wstart")" 2>/dev/null \
468:     && [ "$(($(_now) - wstart_epoch))" -ge 86400 ]; then
469:         cnt=0   # window rolled over — this rollback re-opens it
470:     fi
471:     new_cnt=$((cnt + 1))
472:     _js_journal_update "$jp" "rollback_window_count" \
473:         "{\"24h\": $new_cnt, \"window_start\": \"$(_js_now_iso)\"}" || return 1
474:     until="$(date -jur "$(( $(_now) + SWEEP_COOLDOWN_S ))" +'%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)" \
475:     || until="$(_js_now_iso)"
476:     _js_journal_update "$jp" "cooldown_until" "\"$until\"" || return 1
477:     printf '%s' "$new_cnt"
478: }
```

- **`:474` is the ONLY `date -j` site in `launcher.sh`** (full-file verified: the only other `date -j` matches are the `:238` comment and the `:384` comment). The `-jur` form is BSD `date(1)`; GNU coreutils rejects `-j` outright. **[PLANNER r4 → note (f) THE GREP CLAIM IS CORRECTED (append-only; the text above is retained for the audit trail).]** The claim as stated is **wrong in one respect and right in another**: (i) **there is a SECOND EXECUTABLE `date -j` site** — **`launcher.sh:251`**, inside `_js_iso_to_epoch`'s **Darwin/BSD arm**: `date -ju -f '%Y-%m-%dT%H:%M:%SZ' "$ts" +%s` (verified in-tree) ⇒ "the only other matches are comments" is **false**; (ii) **there is NO `:384` comment** — `launcher.sh:384` is `_js_journal_update`'s **docstring** (`# _js_journal_update <path> <field> <raw-json-or-scalar> — read-modify-write`), and it contains no `date -j` at all; the only **comment** match is the `:238` note about the BSD/GNU date forms. ⇒ the **corrected statement** is: *the only **bare `date -jur`** site (the defect) is `:474`; the other **executable** `date -j` site is `:251`, which is **correctly dispatched** (the BSD arm of the `uname -s` switch at `:249-258`, fail-closed on an unrecognized platform); `:238` is a comment; `:384` is not a comment and not a `date -j` site.* **The design and the static pin are UNAFFECTED**: `P3-PIN-03` asserts *"**ZERO** bare `date -jur` sites **outside** the BSD arm"* — `:251` is `date -ju -f` inside a **BSD arm**, so it is neither bare nor outside the arm ⇒ the pin's wording and its mutation expectation stand unchanged. Only the **evidence sentence** was wrong.
- **Sibling-inside-the-function contrast:** `:467` already uses the *safe* `_js_iso_to_epoch` (`:247-263`, an `uname -s` dispatch: Darwin/\*BSD\* → `date -ju -f`, Linux/GNU → `date -d`, **fail-closed** on an unrecognized platform). Only `:474` is broken.
- **Failure mode:** on GNU, `date -jur` exits non-zero → the `:475` fallback writes `until = _js_now_iso` = **NOW** → `cooldown_until` is written as a *parseable, already-expired* stamp. This is the "cooldown born expired" state.
- **Live corroboration:** the journal row `cooldown_until 22:28:40Z` predates the `22:29:58Z` sweep row — a zero-length cooldown.

#### 4.1.2 The harm (why this is not cosmetic)

`cooldown_until` is written by the launcher but **consumed only ceremony-side**:

- `scripts/upgrade/lib.sh:2139-2151` — `journal_cooldown_active`: `null` → not active; **empty/unparseable → ACTIVE (fail-closed, ADR-005 anti-flapping)**; parseable → active iff `now < until_epoch`.
- `scripts/upgrade/lib.sh:6556-6558` — `promote_entry_check` refuses the next promote while the cooldown is active.

So the launcher's write is a cross-component journal field whose consumer is the *next* promote's entry gate. A launcher-armed `cooldown_until = NOW` reads as "armed and expired" → the consumer returns *not active* → **the ADR-005 10-minute anti-flapping window is silently disabled on this host**. This is a **second instance of tonight's wrongful-rollback family**: the sweep rolled back (wrongly) *and* simultaneously disarmed the guard that would have prevented a rapid re-promote loop. `launcher.sh:75-77` states the launcher's own sweep never *refuses* on the cooldown ("ENTRY-side only (D-FA4.2)") — but the next promote does.

> **Unpinned-surface disclosure.** `grep -rn '_js_count_rollback' tests/ scripts/ daemon/` returns **zero** hits — the function has **no pin of any kind today**. The rider's pin set closes that gap; this is the mechanical justification for the pin's existence (not merely "we changed a line, so we should test it").

#### 4.1.3 Candidate fix forms

**Option A — epoch-argument two-arm dispatch (RECOMMENDED).** Preserve the existing `date(1)` idiom (epoch → ISO) and add the GNU arm:

```
local until_epoch="$(( $(_now) + SWEEP_COOLDOWN_S ))"
case "$(uname -s)" in
    Darwin|*BSD*|*bsd*)
        until="$(date -jur "$until_epoch" +'%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)" || until="$(_js_now_iso)" ;;
    Linux|GNU*|*GNU*)
        until="$(date -u -d "@$until_epoch" +'%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)" || until="$(_js_now_iso)" ;;
    *)
        # P3-Q2 — policy arm; see Open Questions
        ;;
esac
```

**Option B — mirror `lib.sh`'s already-pinned relative form** (`scripts/upgrade/lib.sh:2099-2107`):

```
case "$(uname -s)" in
    Darwin|*BSD*|*bsd*) until="$(date -ju -v+${SWEEP_COOLDOWN_S}S -f '%Y-%m-%dT%H:%M:%SZ' "$(_js_now_iso)" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null)" || until="$(_now_iso)" ;;
    Linux|GNU*|*GNU*)   until="$(date -u -d "+${SWEEP_COOLDOWN_S} seconds" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null)" || until="$(_now_iso)" ;;
    *) … ;;
esac
```

#### 4.1.4 Comparison and recommendation

| Criterion | Option A (epoch two-arm) | Option B (lib.sh-mirror relative) |
|---|---|---|
| BSD arm regression risk | **None** — byte-identical to the current `:474` | Changes the `date(1)` dialect (`-jur <epoch>` → `-ju -v+NS -f <fmt>`) — a new form with new typo surface |
| Time-of-check | **One** `_now()` call (`:1333`) → both arms consume the same instant | Two independent `date` calls (one per arm) → a (small) skew surface |
| Codebase-wide canonical form | Diverges from `lib.sh`'s pinned twin | **Matches** `lib.sh:2099-2107` exactly — one cooldown form across the codebase |
| Launcher-internal idiom consistency | **Matches** `_js_iso_to_epoch` (`:247-263`) and `_js_flip_current` (`:755-770`), both of which use the same `case "$(uname -s)"` shape | Introduces the `lib.sh` dialect into the launcher |
| Static-pin coverage | The "no bare `date -jur` outside the BSD arm" pin catches the class | Same |

**Recommendation: Option A**, written in `launcher.sh`'s own `uname`-dispatch idiom (the `_js_iso_to_epoch` `:247-263` / `_js_flip_current` `:755-770` shape) — *not* `lib.sh`'s. Rationale: it preserves the currently-working BSD arm byte-for-byte (zero regression on the platform that works today), it computes the target instant exactly once, and it keeps the launcher internally consistent with its two existing `uname` dispatches. The codebase-wide-canonical-form benefit of Option B is real but secondary; it can be obtained later by a *separate* de-duplication lane, and attempting it here would mix a defect fix with a refactor (the launcher's R-08 purity constraint — no `python3` in the launcher — rules out sharing code with `lib.sh` in any case; see `launcher.sh:8-15`).

**The pin asserts PROPERTY, not string equality**: born-future + a delta band around `SWEEP_COOLDOWN_S` + arm selection + a static no-bare-BSD-site backstop. Property-assertion is required because the BSD branch **cannot be behaviourally executed on a GNU host** (no BSD `date(1)` available) — see §4.2 P3-PIN-02's honest-coverage note.

#### 4.1.5 Fix-anchor precedents (all verified)

| Precedent | Location | What it supplies |
|---|---|---|
| The canonical pinned twin | `scripts/upgrade/lib.sh:2099-2107` | The `uname` dispatch + the two `date` forms + the fail-closed `*)` arm shape |
| The C7 pin (canonical form) | `tests/test_boot_sweep_commit_and_continue.sh:329-366` | Arm-presence grep assertions (`:332-338`), form assertions (`:337-338`), and a **live-verification block** (`:346-360`) asserting a **future** timestamp on GNU |
| Launcher-internal `uname` dispatch #1 | `launcher.sh:247-263` (`_js_iso_to_epoch`) | `Darwin\|\*BSD\*\|\*bsd\*` / `Linux\|GNU\*\|\*GNU\*` / fail-closed `*)` |
| Launcher-internal `uname` dispatch #2 | `launcher.sh:755-770` (`_js_flip_current`) | Same case shape, `mv -h` vs `mv -T` |
| `uname` PATH-injection seam | `tests/test_supervision_stop_handback.sh:321-332` (A′4 Darwin arm) and `:812-825` (r-f82e surprise class); the seam is described in the file header at `:32-38` | How to force the BSD arm on a GNU host |
| Constants | `launcher.sh:69` `SWEEP_STALE_S=600`; `:75` `SWEEP_COOLDOWN_S=600`; `:228` `_js_now_iso`; `:1333` `_now` (`date +%s`) | The inputs the fix consumes |

> **Citation correction recorded.** The dispatch brief cites the canonical twin at `lib.sh:1375-1385`; that range actually holds `JOURNAL_SIZE_GUARD_BYTES` / `JOURNAL_LEGACY_READ_BUDGET_S`. The real dispatch is at **`lib.sh:2099-2107`**. Separately, the C7 section header inside the test file (`tests/test_boot_sweep_commit_and_continue.sh:329`) says *"Cooldown uname dispatch fix (lib.sh:982 — opportunistic)"* — that line reference is **stale** for the same reason. The new launcher pin must cite `lib.sh:2099-2107`; see Open Question P3-Q5 for the stale-header disposition.

---

### 4.2 The consolidated TEST-SURFACE MAP

#### 4.2.1 Gate-tier determination (`.agents/tester/rules/ensure.md`)

- `:15` **Tier 0** — docs / `.agents/` / test-only diffs. Changed-pack PASS covers the gate.
- `:16-17` **Tier 1 (DEFAULT for feature lanes)** — scoped dual-side FAILED-list diff over the declared blast radius; **≤ 30 min HARD, fail-loud** (abort + report + ONE auto re-scope attempt; never silent grinding).
- `:19-20` **Tier 2** — whole-tree dual-side. **Mechanical trigger**: the diff touches a core seam — `daemon/graph.py`, `daemon/manager.py`, `daemon/api.py`, `daemon/services/instance_messaging.py`, `daemon/services/task_processor.py`, `daemon/services/job_queue_service.py` — **OR** the gate is promote-blocking (release gate). Budget **≤ 90 min advisory**.
- `:27` **Proof form** (mandatory): mechanical derivation → declared set → `--collect-only` slicing proof (executed == declared, counts recorded) → identical commands both legs → *red-at-base ≠ net-new* → 3× flaky isolation on diff nodes only. FAILED-digest comparison per `.agents/tester/skills/failed-digest-equality-gate/`.
- `:28` The 5-min cap is **per test PACK**; gate-level budgets are the Tier budgets (30/90 min). A gate leg composes packs/shards; each shard keeps its own explicit timeout.

**Tier assignment for THIS lane: Tier 2 is MANDATORY**, for two independent reasons:
1. **Core-seam trigger** — Phase 2 touches `daemon/api.py` (a listed core seam).
2. **Release-gate trigger** — this lane *is* the promote pipeline; the gate is promote-blocking by construction.

Consequence: Part 3's rider alone would be Tier 1, but the **lane** must run a Tier-2 whole-tree dual-side leg (P3-PIN-17). The rider's own pins remain Tier 1 (scoped shell packs) — Tier 2 is a *lane-level* leg, not a rider-level one.

**Mandatory recipe load** before any leg, from `.agents/tester/skills/` (the dirs are THE gate recipes):
- `blast-radius-leg` (Tier 1)
- `whole-tree-base-proven-diff` (Tier 2) — **verified exact name**; the brief guessed `whole-tree-base-proded-diff`
- `regression-ab-partition-run`
- `ab-base-leg-provisioning-artifacts`
- `failed-digest-equality-gate`

Additional Tier-2 gate hygiene the map requires: **node-cap + measured-rate projection guard** before the whole-tree leg, and a **pre-exclusion registry** (what is excluded and why, recorded before the run).

#### 4.2.2 The map

Harness legend (all verified in the worktree): shell tests live under `tests/` — **`scripts/upgrade/tests/` does NOT exist**. `tests/upgrade_test_lib.sh` provides the shared fixture registry `_register_fixture` (`:33`) and the **F1 fake-HOME fence** (`setup_fake_home` `:69-102`, `teardown_fake_home` `:104-106`), which refuses any live-install-shaped path.

| Pin ID | Part | File (worktree-relative) | Harness seams (`file:line`) | Tier | What it proves | Mutation-verification expectation |
|---|---|---|---|---|---|---|
| **P3-PIN-01** | **3 (rider)** | `tests/test_boot_sweep_commit_and_continue.sh` (new section, e.g. `C9`) | `make_install_fixture` `:66-85`; `seed_journal` `:94-104`; `run_sweep` `:123-134`; `_register_fixture` `tests/upgrade_test_lib.sh:33`; source-`launcher.sh`-in-a-subshell idiom (`:123-134`) | **T1** | On this GNU host, `_js_count_rollback` arms `cooldown_until` **born ~`SWEEP_COOLDOWN_S` (600 s) in the FUTURE** — not `NOW` | Plant the pre-fix `date -jur`-only form → born-future assertion goes **RED** (fallback writes NOW ⇒ delta ≈ 0). Plant `+0 seconds` / `@<now>` → **RED** |
| **P3-PIN-02** | **3 (rider)** | same file (new section) | `uname` PATH-injection seam — precedent `tests/test_supervision_stop_handback.sh:321-332` (A′4 Darwin arm) and `:812-825`; arm-presence grep pattern copied from C7 `:332-338` | **T1** | **Both `uname` branches SELECT correctly**: `Darwin` → BSD arm, `Linux` → GNU arm, unrecognized → the policy arm | Swap the two arms → arm-selection assertion **RED**. Remove an arm → **RED** |
| **P3-PIN-03** | **3 (rider)** | same file (new section) | static `grep` over `launcher.sh` (C7 static-arm precedent `:332-338`) | **T0 (static)** | **ZERO** bare `date -jur` sites **outside** the BSD arm; the GNU arm present | Plant a bare `date -jur` outside the arm → **RED** |
| **P3-PIN-04** | **3 (rider)** | **NEW** `tests/test_launcher_cooldown_arm.sh` | source `launcher.sh` in a subshell, call `_js_count_rollback` directly on a fixture journal; then read the same journal with `lib.sh`'s `journal_cooldown_active` (`scripts/upgrade/lib.sh:2139-2151`) | **T1** | **Cross-component round-trip:** the value the launcher writes is consumed by the ceremony-side consumer as an **ACTIVE** cooldown (the anti-flapping window is armed) | Plant the pre-fix form → the consumer sees an expired stamp → `journal_cooldown_active` returns *not active* → **RED**. This is the pin that would have caught tonight's live defect |
| **P3-PIN-05** | **2** (= Phase 2 `P-1`) | `tests/test_boot_sweep_commit_and_continue.sh` (new sections) | `run_sweep` `:123-134`; `seed_journal` `:94-104` (`flipped=1`); serving stub = **`tests/helpers/soak_gate_fake_daemon.py`** (mode `green`), per Phase 2's fixture-reuse note — do **not** build a second stub | **T1** | **Direction ① (composition requirement):** stale flipped txn + **SERVING-GREEN** → **commit-close** (`serving_green_commit` row) — journal `current` = target, `in_flight` = null, **NO** `sweep_rollback` row, **NO** quarantine append, `rollback_window_count` **NOT** incremented, `cooldown_until` **NOT** armed. **[PLANNER r5 → BLOCKER 1 — THE AUTHORIZATION PRECONDITION IS NOW PART OF THIS PIN'S ACCEPTANCE]** the GREEN arm fires **only when the `intent_flip` evidence is present** (the launcher rung's conjunct — `launcher.sh:1068-1079`, AM-P2-4) ⇒ the pin must additionally assert the **negative leg**: **GREEN ∧ NO `intent_flip` ⇒ DEFER** (a `sweep_deferred` row; the txn stays **open**; **no** commit-close, **no** quarantine, **no** counter, **no** cooldown) — the R3 hinge. **Mutation-verification expectation:** drop the `intent_flip` conjunct from the launcher rung ⇒ this pin goes **RED** (the pre-r5 row could not catch that mutation, because the pre-r5 row did not require the conjunct). The same negative axis is the third leg of `P3-PIN-21` (the three-axis `intent_flip` pin). | Remove/nerve the serving-green branch → the sweep-rollback path fires → **RED**; drop the `intent_flip` conjunct → **RED** (the unauthorized-flip commit) |
| **P3-PIN-06** | **2** (= Phase 2 `P-2`) | same file (new sections) | same; serving stub = `soak_gate_fake_daemon.py` mode `hard-red` / no-listener | **T1** | **Direction ② (composition requirement):** stale flipped txn + **SERVING-UNHEALTHY** → the existing never-green rollback **still fires** — `sweep_rollback` row, quarantine append, counter++, cooldown armed, `current` → `prev` **[PLANNER r3 → REV-R-1 — SUPERSEDED for the immediate arm; the row is KEPT, not deleted]** — under r2/r3 the observation taxonomy is **four-way** (AM-P2-10.1) and **only the BROKEN class** (`/livez` non-200 / unparseable) fires **immediately**; `hard-red` (livez 200 + `/readyz` 500) is **AMBIGUOUS (unready)** and a **no-listener** observation is **AMBIGUOUS (absent)** ⇒ both **defer** and are **counted** toward the bounded absent-ceiling ⇒ the chain fires when the ceiling predicate holds. **Drive instead:** `P3-PIN-20` / `P3-PIN-26` (the ceiling) + `P3-PIN-25`'s `livez-non200` mode for the BROKEN-immediate leg. This annotation exists because a tester building from the un-annotated row would hard-redden against the **correct** r2/r3 implementation | Neuter the never-green arm → **RED** |
| **P3-PIN-07** | **2** (= Phase 2 `P-7`) | same file (new sections) | journal-history inspection (`_js_history_append` rows, `launcher.sh:1225-1226` shape); `tests/helpers/journal_fixture_incident.py` + `tests/fixtures/journal/02-live-shaped-journal.json` (Phase 2's fixture) | **T1** | **Tonight's exact 4-stage sequence:** `halt` → serving-green → **commit-close + `quarantine_cleared_serving_green`** at HEAD (the BASE behaviour — wrongful rollback + quarantine refusal — is the red-at-base side of the A/B leg) | Drop the `quarantine_cleared_serving_green` row → **RED**. Restore the unconditional rollback → **RED** |
| **P3-PIN-08** | **2** (= Phase 2 `P-3`) | `tests/test_boot_sweep_commit_and_continue.sh:279-302` — **RE-SHAPE the existing C5** | `make_install_fixture` `:66-85` (the `prev_rollback_safe` parameter); `seed_journal` `:94-104` | **T1** | C5 today pins *"unconditional `flipped`+stale → rollback"* as INTENDED (`:288-296`). Per the owner ruling it must become **parameterised on SERVING HEALTH**, not on `rollback_safe` alone — split into **C5a** (green ⇒ commit-close) and **C5b** (not-green ⇒ the existing chain fires). The pre-ruling assertion text must be **re-shaped, not deleted** **[PLANNER r3 → REV-R-1 — SUPERSEDED for the C5b arm; the row is KEPT, not deleted]** — C5b is no longer a single *"not-green ⇒ fires"* assertion: it must be **parameterised on the observation class** per **AM-P2-10.1** (**BROKEN** ⇒ the chain fires **immediately**; **AMBIGUOUS** ⇒ **defer** + count toward the bounded ceiling, the chain firing only when the ceiling predicate holds). **Drive instead:** `P3-PIN-20` / `P3-PIN-26` for the AMBIGUOUS leg and the `livez-non200` fixture mode for the BROKEN leg. A tester building the un-annotated C5b would hard-redden against the **correct** r2/r3 implementation | Leave the unconditional shape → **RED** (the pin must fail against the pre-ruling intent) |
| **P3-PIN-09** | **1** (= Phase 1 legs **L1 / L2 / L4**) | `tests/test_supervision_stop_handback.sh` (new section `D`) | `run_rvl` `:657-673` (`LIVEZ_BUDGET_S=2` at `:664`); delayed counters `SC_MAINPID_N` / `SC_IS_ACTIVE_N` `:114-138`; `lsof` stub `:174-183`; `HOST_HAS_SYSTEMD` fence `:92-93`; `make_systemctl_stub` `:106-168`; **NEW seam** = a PATH-injected `curl` stub for the serving probe (Phase 1's stated requirement) | **T1** | A **slow unit-active→serving boot** inside the dedicated hand-back budget does **NOT** trip the abort predicate — closing the gap confirmed in the brief (the file currently has **no** slow-boot timing pin). L1 = budget-source discrimination, L2 = default-budget coverage, L4 = budget expiry (the fail direction) | Shrink the budget back to the fixed 180 s class → **RED** (reproduces the 175/180 s near-miss that nearly killed the v0.28.3 ceremony) |
| **P3-PIN-10** | **2** (= Phase 2 `P-4`) | launcher sweep pack (new section in the boot-sweep file or a new pack) | `run_sweep` `:123-134` + the deferral seam; the defer-ceiling constant **must** use the `:=` form (`: "${SWEEP_DEFER_CEILING:=3}"`) so it is overridable — the launcher's other sweep constants are plain assignments and are **overwritten at source time** | **T1** | The launcher **defer** path is **BOUNDED**: not-green ⇒ a `sweep_deferred` row with `in_flight` still open and **no other field mutated**; after `SWEEP_DEFER_CEILING` deferrals ⇒ the rollback fires **[PLANNER r4 → blocker-4 consistency; the row is KEPT, not deleted]** the counter's **home** is the launcher-local `.sweep-defer-<run_id>` file (AM-P2-5 — not a journal-history count) and the firing condition is the **ceiling predicate** (≥`SWEEP_DEFER_CEILING` **qualifying** observations ∧ owner-dead ∧ `intent_flip` ∧ **never-green-observed**, AM-P2-10.2), not "after N deferrals" — a GREEN observation makes the ceiling **permanently ineligible** for that txn (sticky-green, AM-P2-10.3) ⇒ **drive `P3-PIN-20` / `P3-PIN-26`** | Remove the defer ceiling → **RED** (never rolls back) |
| **P3-PIN-11** | **2** (= Phase 2 `P-6`) | daemon-side pytest — `tests/unit/tools/test_upgrade_journal_sweep_liveness.py` (472 L) **+ a NEW daemon-side pin file** | pytest, `-p no:randomly`, `--timeout=120`, per-variable `env -u POSTGRES_*` scrub | **T1** | The **daemon-side** sweep tick: `flipped`+stale+owner-dead → livez/readyz/`version == target` check → green → **commit-close** (`serving_green_commit`, + `quarantine_cleared_serving_green`); unhealthy → the existing never-green rollback **still fires**; stale/owner-alive ⇒ **no action**. **[PLANNER r5 → BLOCKER 1 — THE `intent_flip` AUTHORIZATION CONJUNCT IS NOW EXPLICIT IN THIS ROW]** the tick's predicate set is **P1-P9** (Phase 2 §D.1's table, as amended by this fold), and **P9 = the `intent_flip` authorization conjunct** ⇒ the daemon tick's GREEN arm fires **only** when the journal history carries an `intent_flip` event naming the target; **absent P9 ⇒ the daemon takes NO action this tick** (no `serving_green_commit`, no `quarantine_cleared_serving_green`, no `serving_unhealthy`, no `rollback_requested`; the txn stays **open**) — AM-P2-4's canonical clause (ii). ⇒ the pin must additionally assert the **negative leg** **GREEN ∧ NO `intent_flip` ⇒ no action**. **Mutation-verification expectation:** drop P9 from the daemon predicate set ⇒ this pin goes **RED** (the pre-r5 row could not catch it, because the pre-r5 row named no conjunct). **[PLANNER r3 → REV-R-1 — SUPERSEDED for the unhealthy arm by `P3-PIN-11a` / `P3-PIN-11b` (the row is KEPT, not deleted — it is the decomposition's parent)]** — and, with the r3 fold, the **unhealthy** arm itself is further qualified by the **grace conjunct** (AM-P2-24): a **degraded-but-grace-held** composite ⇒ **DEFER**, never a verdict or a request ⇒ **drive `P3-PIN-27` for the defer leg** and `P3-PIN-11a` (on a **not-grace-held** fixture) for the never-green leg. **Note the compound gate on the never-green leg:** it requires **P9 ∧ ¬grace-held** — a fixture that is not-grace-held but lacks `intent_flip` is the **no-action** fixture, not the never-green fixture. | Neuter the daemon commit-close → **RED**; drop the `intent_flip` conjunct ⇒ **RED** |
| **P3-PIN-12** | **2** (= Phase 2 `P-8`, `P-9`, `P-11`, `P-12`) | regression over the existing never-green family: `tests/packs/promote_soak_never_green_test.sh`, `promote_soak_fail_fast_test.sh`, `promote_soak_fail_fast_integration_test.sh`, `promote_soak_grace_burst_test.sh`, `promote_soak_grace_counter_unit_test.sh`; **plus** the `adopt_stale_txn` polarity family (Phase 2 `P-9`), the launcher purity static scan (Phase 2 `P-11`), and the closer's journal-lock discipline (Phase 2 `P-12`) | shell packs, per-pack timeout | **T1** | The house **never-green protection is UNTOUCHED** (the composition requirement's "both directions" clause, from the consumer side); the adopt path mirrors the same polarity; the launcher contains **no** executable `curl`/`wget`/`nc`/`lsof`/`python3`; the closer re-reads the txn under the journal lock. **[PLANNER r5 → BLOCKER 5 — THE ADOPT-PARITY CLAIM IS SCOPED, NOT UNSCOPED (append-only; the text above is retained for the audit trail).]** *"the adopt path mirrors the same polarity"* **cannot hold literally for every fixture**: on the **GREEN-without-`intent_flip`** fixture the three decision-makers **deliberately diverge** — the launcher **defers** (Phase 2 §D.2 rung 1), the daemon takes **no action this tick** (Phase 2 §D.1 **P9**), and the adopt path takes the **existing chain** (Phase 2 §D.4 (a-i) — the adopt path's existing chain is its pre-ruling behaviour and this lane does not rewrite it). ⇒ the shared-polarity pin is **scoped to the fixtures where all three agree** — **GREEN-and-authorized** and **NOT-GREEN** — and the GREEN-but-unauthorized fixture is pinned **per decision-maker** (`P-26` / `P-15`'s negative axis for the launcher + daemon legs, plus the adopt-path leg of the same pin). The **same scoping** is carried by Phase 2's **R-8** row and by `decisions.md` §5.1's `P3-PIN-12` row (both folded in the same r5 pass), and by `architecture-recommendation.md`'s reconciled-dispositions table (the *GREEN / no-`intent_flip`* row). **The divergence is disclosed, not a defect to be unified.** The other three assertions in this row (never-green untouched; launcher purity; lock + re-read) are **UNCHANGED**. | Regress the never-green arm → **RED**. Introduce `curl` into `launcher.sh` → **RED** (purity) |
| **P3-PIN-13** | **3 (deployment)** | NEW pack exercising `scripts/upgrade/lib.sh:594-615` `_pipeline_scripts_stamp_verdict` | the function is **already extracted from `stage.sh` specifically so the gate is EXECUTABLE in a pack** (see the comment at `lib.sh:590-593`) | **T1** | The **two-sided requirement**: the stamp must be 40-hex, must resolve in the tree, and must **EQUAL** the floor. A mismatch is refused (the exit-78 class) | Set `STAMP != FLOOR` → **RED**; make the stamp a non-hex placeholder → **RED**; make it unresolvable → **RED** |
| **P3-PIN-14** | **3 (deployment)** | NEW pack over the `stage.sh:556-565` floor-validation block | the `sed` extract `:556` + `git merge-base --is-ancestor` `:562-565`; extract into a testable form the way `lib.sh` did for the stamp | **T1** | `stage.sh` **refuses to stage** when the floor is unset / not-a-sha / not-an-ancestor of the built tree | Unset the constant → **RED**; point it at a non-ancestor sha → **RED** |
| **P3-PIN-15** | **ALL** | harness hygiene — every leg that boots a frozen binary | — | **T0 (harness self-check)** | Each boot leg pins **`ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` EXPLICITLY** (never `env -u` — it strips the ambient guard and the sweep kills LIVE processes), scrubs `POSTGRES_*` **per-variable** (`env -u POSTGRES_*` is a silent NO-OP), and sets **`ENSEMBLE_SELF_ENV=sandbox`** for a frozen binary's first boot | Remove the explicit pin → the harness self-check goes **RED** (static) |
| **P3-PIN-16** | **ALL** | `.agents/tester/PACKS.md` registration | keep-both-newest-first on conflicts | **T0 (doc)** | Every pack above is registered with **command + scope + verdict rows** (no unpinned pins — the "registration gap" class) | n/a (documentation) |
| **P3-PIN-17** | **ALL (lane-level)** | Tier-2 whole-tree dual-side leg | recipe `.agents/tester/skills/whole-tree-base-proven-diff/`; node-cap + measured-rate projection guard; pre-exclusion registry; `--collect-only` slicing proof; identical commands both legs; 3× flaky isolation on diff nodes; FAILED-digest per `failed-digest-equality-gate/` | **T2 (≤ 90 min advisory)** | **net-new reds = 0** across the declared blast radius at the lane tip, base↔HEAD dual-side | n/a (the leg is the proof) |
| **P3-PIN-18** **[PLANNER r2]** | **2** (= Phase 2 `P-10`, AM-P2-4, AM-P3-2) | per-site mutation legs — the launcher probe, the daemon closer, the adopt path | one leg **per comparison site**; `v`-prefixed target ↔ bare `__version__` ⇒ GREEN; target `v0.28.5` vs daemon `0.28.4` ⇒ NOT green; an anti-`tr -d v` leg (the strip is `${x#v}` — one leading `v`, idempotent) | **T1** | **Version normalization per site** — a `v`-prefixed target matches a bare `__version__` ⇒ GREEN on every site | Double-strip (`v0.28.5` → `0.28.5` → `0.28.5` is fine, but `tr -d v` would also eat a `v` inside the string) ⇒ the leg goes **RED** |
| **P3-PIN-19** **[PLANNER r2]** | **2** (= Phase 2 `P-21`, AM-P2-8) | daemon-side pytest — the promote-wake reader | `PROMOTE_WAKE_TERMINAL_EVENTS` reader (`upgrade_journal.py:1942`, `:1953-1975`); the run_id-scoped tie-break precedent at `upgrade_journal_sweep.py:439-450` | **T1** | **The promote-wake reader is run_id-scoped** ⇒ an **unrelated** commit-close cannot silently **drop** a pending promote-wake (a paused instance never resumes). **[PLANNER r2 → #22] REFRAMED as a PRE-EXISTING-DEFECT CURE, not a new-event problem:** the reader's run_id-blindness is a **pre-existing** defect that today's `boot_sweep_commit_and_continue` already exposes; adding `serving_green_commit` to the terminal set only makes the exposure **reachable**. The pin stays (the cure is the point) but the blame line is corrected: the defect is the blind reader, not the new event kind. | Make the reader run_id-blind again ⇒ **RED** |
| **P3-PIN-20** **[PLANNER r2]** | **2** (= Phase 2 `P-20`, AM-P2-5, **AM-P2-10**) | launcher sweep pack (new section) | the counter file `<INSTALL_DIR>/.sweep-defer-<run_id>`; the `:=`-form constant `: "${SWEEP_DEFER_CEILING:=3}"` (the launcher's other sweep constants are plain assignments and are **overwritten at source time**) | **T1** | **The defer counter's home + the absent-ceiling eligibility**: the tally lives in the launcher-local file (O(1), survives launcher restarts — **not** in the journal history, whose extraction measured 13.5 s / 35.7 s at 27 KB) and the **bounded absent-ceiling** fires the existing chain only when ≥`SWEEP_DEFER_CEILING` qualifying observations ∧ owner-dead ∧ `intent_flip` ∧ never-green-observed hold | Point the counter back at the journal history ⇒ the **O(N²)** leg goes **RED**; drop any one of the three conjuncts ⇒ **RED** |
| **P3-PIN-21** **[PLANNER r2]** | **2** (= Phase 2 `AM-P2-4`) | launcher sweep pack (new section) **+** the daemon-side pytest | the `intent_flip` reader (`launcher.sh:518-536`); the daemon's **new** history read (P9) | **T1** | **THE THREE-AXIS `intent_flip` PIN (the number the council mandated):** (a) **GREEN ∧ `intent_flip` ⇒ commit-close** (no quarantine, no counter, no cooldown) · (b) **GREEN ∧ NO `intent_flip` ⇒ defer / no-action / existing-chain — NEVER commit** · (c) **serving-unhealthy ⇒ the existing never-green rollback still fires**. **[PLANNER r2 → #7] PLUS the r2 negative axis:** a **forged** `rollback_requested` + a **green** probe + **NO `intent_flip` ⇒ defer** — never a commit-close, never a rollback (the R3 hinge; Phase 2 `P-26`). | Remove the `intent_flip` conjunct from **either** the commit path or the request path ⇒ the corresponding axis goes **RED** |
| **P3-PIN-22** **[PLANNER r2]** | **2** (= Phase 2 `P-13`..`P-18`, AM-P2-6) | launcher sweep pack (new sections) + the daemon-side pytest | the per-entry request reader (Phase 2 `AM-P2-14`); the journal writer under the lock (`upgrade_journal.py:392-407`) | **T1** | **The T14 handoff family:** request + NOT-GREEN ⇒ the existing chain executes, post-state **byte-identical** to the legacy no-request path (P-13) · no request ⇒ the defer ladder unchanged (P-14) · request + GREEN ⇒ commit-close, the request **not** executed (P-15) · the ceiling path and the request path converge on one chain ⇒ identical post-state (P-16) · a request naming another `run_id`/target is ignored (P-17) · the daemon writes the request with **zero** symlink/quarantine/counter side-effects (P-18). **[PLANNER r2]** PLUS the two negatives: the **cross-entry** false positive must NOT fire (P-25) and the forged-request + green + no-`intent_flip` case must defer (P-26). | Break the per-entry matching (make it a whole-array conjunction) ⇒ the cross-entry leg goes **RED** |
| **P3-PIN-23** **[PLANNER r2]** | **2** (= Phase 2 `P-19`, AM-P2-1) | fault-injection pack (new) | the history-before-close mutation order (AM-P2-1's five steps) | **T1** | **Crash-prefix safety:** interrupt at **each** prefix of the commit-close sequence ⇒ the next actor's disposition is **never** a wrongful rollback and the sequence converges (re-run is a no-op or completes it) | Reorder the sequence (close before history) ⇒ **RED** (a serving-green release ends up permanently quarantined) |
| **P3-PIN-24** **[PLANNER r2]** | **2** (= Phase 2 `P-22`, AM-P2-8) | daemon-side pytest — the two named test sites | `tests/unit/services/test_post_restart_arm_notify_sweep.py:875` (`len(uj._TERMINAL_EVENTS) == 9`) ⇒ 10 + set-equality; `tests/unit/tools/test_upgrade_tools.py:886-899` (label-coverage) ⇒ `_OUTCOME_LABELS["serving_green_commit"]` (`upgrade_tools.py:1255-1310`) | **T1** | **The vocabulary-growth test updates** are applied (the gate reddens **predictably**, not as a surprise) | Drop either update ⇒ the site goes **RED** |
| **P3-PIN-25** **[PLANNER r2]** | **2** (= Phase 2 `AM-P2-9`) | launcher probe legs (new) | `_js_serving_probe` (Phase 2 `T1`/`T17`); the fake daemon's modes | **T1** | **The probe acceptance mutations:** a **truncated / unparseable body ⇒ NOT-GREEN** (never green) · the **wall-clock deadline INSIDE the body-read loop** (a per-read `-t` alone is defeatable by a slow-dribble responder) · the **`/readyz` second GET over the same `/dev/tcp` fd** is present and is answered (the canonical GREEN of **AM-P2-11**) · a **wrong version fails fast** (no polling to the budget) · the exec-chain fact (`launcher.sh:1` `#!/bin/bash`; the unit's `ExecStart` is direct ⇒ the shebang is honored ⇒ `/dev/tcp` is available; the probe pins 127.0.0.1) | Remove the in-loop deadline ⇒ the slow-dribble leg goes **RED**; drop the `/readyz` GET ⇒ the `hard-red` leg goes **RED** |
| **P3-PIN-26** **[PLANNER r2]** | **2** (= Phase 2 `P-23`, `P-24`, `P-27`, `P-29`, `P-30`, `P-32`, `P-33`, **AM-P2-10**) | launcher sweep pack (new sections) | the counter file; the symlink-state test (**AM-P2-16**); the narrow T15 helper (**AM-P2-17**); the kill-switch `SWEEP_SERVING_GREEN_ENABLED` | **T1** | **THE r2 FAMILY, in one place:** (a) the **absent-ceiling fires** after `SWEEP_DEFER_CEILING` qualifying observations driven across **separate `run_sweep` invocations** (the counter persists) ⇒ the existing chain with all **four** effects; below the threshold ⇒ defer (P-23) · (b) **never-green-observed is sticky** — a GREEN at any point (or a `serving_green_commit` row in the history) makes the ceiling **permanently ineligible** ⇒ defer forever, no rollback of a serving-green release (P-24) · (c) the **version-mismatch split** — symlink-on-target + wrong version ⇒ counted/bounded; symlink-**moved-away** ⇒ **NO ACTION** (P-27) · (d) the **counter-file lifecycle** — deleted on commit and on rollback, stale-swept at boot, sanitised filename, no two-txn collision (P-29) · (e) the **kill-switch** `SWEEP_SERVING_GREEN_ENABLED=0` restores today's behaviour, `=1` (the default) installs the new ladder (P-30) · (f) **T15's scope + idempotence** — the hook executes a pending request, a second call is a no-op, the **stale pre-flip clear is NOT** performed by the hook, a **tempfail-class (exit 75)** child exit does **not** increment the counter (P-32) · (g) **owner-liveness on the whole stale path** — a stale **pre-flip** txn with a **live** owner is **NOT** cleared (P-33) | Drop any one of the absent-ceiling's conjuncts ⇒ (a) goes **RED**; make the green flag non-sticky ⇒ (b) goes **RED**; let the hook run the pre-flip clear ⇒ (f) goes **RED** |
| **P3-PIN-27** **[PLANNER r3]** | **2** (= Phase 2 `P-35`, `AM-P2-24`) | daemon-side pytest — the same seam as `P3-PIN-11a` (`tests/unit/tools/test_upgrade_journal_sweep_liveness.py` + the NEW daemon-side pin file) | pytest, `-p no:randomly`, `--timeout=120`, per-variable `env -u POSTGRES_*` scrub | **T1** | **THE DEGRADED-BUT-GRACE-HELD ⇒ DEFER LEG** (F-1, AM-P2-24): a stubbed `readiness_provider` returns a composite with **`ready == False` ∧ `grace_active == True`** (the warmup shape) against a stale flipped txn ⇒ the daemon-side closer writes **no** `serving_unhealthy` row and **no** `rollback_requested` row, the txn stays **open**, `current` is **not** repointed, and there is **no** quarantine / counter / cooldown ⇒ **no** path can execute a rollback of a healthy slow boot. **This is the new member of the both-direction composition set**: the other direction (genuinely unhealthy **outside** grace ⇒ the verdict + request + the existing chain) is `P3-PIN-11a`, which must use a **not-grace-held** fixture so the two legs cannot be satisfied by the same stub | Drop the `grace_active == False` conjunct from the closer's NOT-GREEN verdict ⇒ the leg goes **RED** (a `rollback_requested` row appears) |
| **P3-PIN-28** **[PLANNER r4]** | **2** (= Phase 2 `P-36`) | shell-level (the `adopt_stale_txn` / `promote_entry_check` fixture family — the same harness as `P3-PIN-09`/`P3-PIN-10`) | `scripts/upgrade/lib.sh` (the new `journal_quarantine_cleared_serving_green` reader beside `:1963-1969`); the §D.3 contract box in `phase2-plan.md` | **T1** | **The quarantine-clearance reader's shape — the two negatives (the R-15 class):** (a) **prefix collision** — a journal whose only clearance event names `target v0.28.55` ⇒ the reader for `v0.28.5` returns **"not cleared"** ⇒ the promote entry is **still refused**; (b) **cross-entry split** — the event kind in one history entry and the target string in a **different** entry ⇒ **"not cleared"**. **Positive half:** the pinned house detail `target v0.28.5 run_id <ID> …` ⇒ **"cleared"** ⇒ the promote entry is **accepted**. A reader that never fires is a **regression to tonight's punishment class** | Make the reader a whole-array conjunction ⇒ (b) **RED**. Drop the delimiter anchor ⇒ (a) **RED** |
| **P3-PIN-29** **[PLANNER r4]** | **2** (= Phase 2 `P-37`) | daemon-side pytest — the same seam as `P3-PIN-11a` / `P3-PIN-27` | the **real** `GraceTracker` (`daemon/services/readiness.py:754-810`) + the **real** `ReadinessComposite` (`:309`, `grace_active` at `:407`) driven through the closer's **real** `readiness_provider` seam | **T1** | **The REAL GraceTracker / composite-interleaving leg** (note (d)'s companion to `P3-PIN-27`): the closer is driven against a **real** composite whose `grace_active` comes from the **real** tracker — grace-held ⇒ **no** verdict / **no** request, txn open; after a **full lapse** ⇒ the never-green verdict is reached. `P3-PIN-27` proves the closer's **conjunct** against a synthetic composite; this pin proves the **composite's arithmetic** feeds it ⇒ the two legs cannot be satisfied by the same stub | Replace the real composite with the synthetic stub ⇒ the non-vacuity assertion fails ⇒ **RED** |
| **P3-PIN-30** **[PLANNER r4]** | **2** (= Phase 2 `P-38`) | daemon-side pytest — the same seam as `P3-PIN-11a` / `P3-PIN-27` / `P3-PIN-29` | the env kill-switch read **per tick** (`ENSEMBLE_UPGRADE_SERVING_GREEN_CLOSER`; the per-tick-read precedent `upgrade_journal_sweep.py:416-424`) | **T1** | **The daemon-side kill-switch, TWO states:** `=0` ⇒ the closer lane writes **no** `serving_green_commit`, **no** `serving_unhealthy` and **no** `rollback_requested`, and leaves the stale txn **untouched** (no symlink repoint, no quarantine, no counter, no cooldown); `=1` (the default) ⇒ normal behaviour. Read **per tick**, not cached, so an operator can flip it without a restart | Cache the switch at construction ⇒ flipping to `=0` mid-run still writes a verdict ⇒ **RED**. Make `=0` still write `serving_unhealthy` ⇒ **RED** |

#### 4.2.3 Harness interaction notes (must be honoured by the implementers)

- **Launcher constants are plain assignments — overrides do not work.** `launcher.sh:69-94` assigns the sweep constants with `=` (not `:=`), so the harness's `SWEEP_STALE_S=0 HEARTBEAT_STALE_S=0` at `tests/test_boot_sweep_commit_and_continue.sh:123-134` is **overwritten at source time and has no effect** — the fixtures are "stale" only because their seeded `started_at` is in the past. **New pins must not rely on overriding those constants; they must seed an old `started_at`** (as the existing harness does). The **new** defer-ceiling constant must use the `:=` form (`: "${SWEEP_DEFER_CEILING:=3}"`) so it *is* overridable, and the P3-PIN-10 pin must drive it through that path. *(Carried from Phase 2's fixture note — the consolidator's job is to make it impossible to miss.)*
- **Reuse the fake daemon; do not build a second one.** `tests/helpers/soak_gate_fake_daemon.py` already models every serving shape needed — `green` (`/livez` 200 + `/readyz` 200), `hard-red` (`/livez` 200 + `/readyz` 500), `never-all` (accepts, never answers), `never-readyz`, `flip`, and `version-aware` (resolves `$INSTALL_DIR/current` → manifest `binary_version` → hard-red on `--bad-version`). It binds 127.0.0.1 only, logs every request as JSONL (non-vacuity assertions), and takes `--port --mode --version --install-dir --bad-version --log --ready-file`. The launcher-side P3-PIN-05/-06/-10 pins drive **this** stub. `tests/helpers/soak_gate_pack_common.sh` carries the shared `never-all` shape.
  - **[PLANNER r2 → Phase 2 §AM-P2-12] A MEASURED LIMIT OF THE EXISTING STUB — read before reusing it.** The stub answers **`/livez` 200 in EVERY mode** (the `/livez` handler branch is unconditional; the mode degrades **only** `/readyz`). ⇒ against a `/livez`-only launcher probe, **`hard-red` is GREEN** — so the direction-② pin (P3-PIN-06) is **not discriminating** against a livez-only implementation. This is the mechanism-level reason the canonical GREEN predicate (**AM-P2-11**, OQ-2) includes the `/readyz` GET. ⇒ the stubs stay as they are (the canonical predicate restores the discriminator), and **one new helper** is added for the shapes the stub does **not** model: `tests/helpers/serving_probe_fake_listener.py` with the modes `wrong-version` (`/livez` 200, version ≠ target), `livez-non200`, `never-answer`, `readyz-red`. The new helper is **synthesised**, binds 127.0.0.1 only, and logs every request as JSONL (non-vacuity).
- **`tests/test_launcher.sh` source pins.** `tests/test_launcher.sh:831-848` (pin 8b-r1) is a **source-level** pin on the `_journal_sweep` function body (no `wait` against subprocess pids, no pipe reads). **Phase 2's** launcher changes to `_journal_sweep` touch that pinned body → the 8b-r1 pin must be re-verified in the Tier-1 leg. **[PLANNER r2 → Phase 2 §AM-P2-17]** T15's narrow helper is a **new function**; the pin's extraction regex targets `^_journal_sweep\(\)` ⇒ the helper is **outside** the pin's scope, and `_journal_sweep`'s body gains **one plain call** (a function call is neither a `wait` nor a pipe read) ⇒ the 8b-r1 pin must still be **re-verified** in the Tier-1 leg (the body changed), and the helper's own body must be **added to the pin's scope** so the sweep-hang class cannot re-enter through it. The rider's edit to `_js_count_rollback` is **outside** that pin's scope (the pin's extraction regex targets `^_journal_sweep\(\)`), so the twin-body pin is not at risk from Part 3.
- **The twin.** `launcher.sh:8-15` states the ADR-012 journal sweep + `_pid_alive` twin must live in the launcher because it is "the whole supervisor in one sourced unit BY CONTRACT". `_js_count_rollback` is the launcher's twin of `lib.sh`'s `journal_count_rollback` (`lib.sh:2055+`) — the divergence (lib.sh fixed, launcher not) is the defect; the rider **reduces** the divergence.
- **PRE-EXISTING BASE RED — the `daemon/constants.py` seal tripwire.** `tests/test_chart_image_delivery_e2e.py:1425` holds `SEALED_SHA_BASELINES`, and `daemon/constants.py` is in that set with baseline `464a2459fb4c91c6a1add2bc6bafc3b1242415996597f4e16bef44b3387c8e92` (`:1467`). The worktree's current content hashes to `5dd9bfa3e56db333d4cac92b78bba133ef26a327c6810312d819cd6e437a47b7` — **the two already differ at base**. The tripwire `test_phase_d_does_not_modify_sealed_artifacts` (`:1515-1535`) therefore fails **at base, before any lane edit**. Phase 2 touches `daemon/constants.py`, so the lane will see this red. It is **pre-existing base debt, not a lane regression** — the FAILED-digest comparison must record it as red on **both** legs (the `red-at-base ≠ net-new` rule, `ensure.md:27`). It is **not** a reason to re-pin the seal in this lane (see P3-Q9).
- **Journal fixtures.** `tests/helpers/journal_fixture_incident.py` and `tests/fixtures/journal/` (10+ canonical corpus files, incl. `02-live-shaped-journal.json`) are available for the daemon-side journal-shape work; the standing journal regression is `tests/test_release_journal.sh`.
- **Daemon-side corpus.** `tests/unit/tools/test_upgrade_journal_sweep_liveness.py` (472 L), `test_promote_txn_hold.py`, `test_upgrade_journal.py`, `test_upgrade_preflight_daemon_side.py`, `test_upgrade_reasons_reader.py`, `test_upgrade_splice_helper.py`, `test_upgrade_registration.py`, `test_upgrade_tools.py`, `test_upgrade_install_root_layer1.py`, `test_upgrade_executor_systemd_service.py`, `test_promote_cgroup_survivorship_python.py`.

---

### 4.3 PAYLOAD / SCRIPTS-PIN DEPLOYMENT MAPPING

#### 4.3.1 The verified two-sided split

| Surface | Ships in the release payload? | How it reaches live | Evidence |
|---|---|---|---|
| `launcher.sh` | **YES** — it is part of the staged payload | **PROMOTE ONLY.** Swapped into the install in the promote's stopped window; it then executes at the **next launcher boot** | `scripts/upgrade/stage.sh:424` (`cp "$REPO_ROOT/launcher.sh" "$STAGE_TMP/launcher.sh"`); payload header `stage.sh:11-14` (*"the launcher joins the staged payload; the manifest gains launcher_sha256; promote swaps it in the stopped window"*); `LAUNCHER_SHA` computed `stage.sh:438`; manifest field `launcher_sha256` `stage.sh:599` |
| `daemon/services/upgrade_journal_sweep.py`, `daemon/api.py`, `daemon/tools/upgrade_journal.py` | **YES** — inside the frozen binary (`ensemble-prod`) | **PROMOTE ONLY** (the binary is replaced wholesale) | Payload composition `stage.sh:395-433` (binary `:395`, `agents/` `:397`, `frontend/` `:398-400`, `plugins/` `:404`, `config.yaml` `:423`, `launcher.sh` `:424`) |
| `scripts/upgrade/{lib.sh,promote.sh,stage.sh}` | **NO — source-less.** The payload is binary + `agents/` + `frontend/` + `plugins/` + `config.yaml` + `launcher.sh` **only** | **CEREMONY-TIME EXECUTION** from the `ENSEMBLE_UPGRADE_SCRIPTS_DIR`-pinned directory. Consumed **daemon-side only** | `daemon/tools/upgrade_tools.py:1979-2002` `_resolve_scripts_dir` (order: explicit env → repo-of-module → `<install_dir>/scripts/upgrade`); call sites `:2887`, `:3572`; armed calls refuse `executor-scripts-unavailable` (fail-closed). **Zero** references to `scripts/upgrade` from any shell script |

> **Consequence for the rider.** The rider's fix lives in `launcher.sh` ⇒ it is **source-side only** and reaches live **at the next promote**, not at the next commit. Between the merge and that promote, the launcher's cooldown arm remains the defective one. The mitigations are Phase 2's daemon-side closer (which acts at the first sweep tick after the 600 s staleness gate — see `launcher.sh:69` `SWEEP_STALE_S=600` and `:906` age gate — i.e. ~11 min, long before the ~59 min fuse) and the launcher's defer path. This ordering risk is recorded as **P3-R1**. **[PLANNER r4 → note (g) THE "~59 min FUSE" IS NOT A DESIGNED FUSE]** the 3540 s figure was the **age of tonight's *unconditional* auto-rollback** (a launcher boot at 22:25:53 on a txn opened ≈21:26) — there is **no** 3540 s constant anywhere in the tree (verified: `grep -rn 3540 scripts/upgrade/ launcher.sh` ⇒ **0 hits**); the launcher's gate is `SWEEP_STALE_S=600` (`launcher.sh:69`) plus the age computed at launcher entry (`:907`) ⇒ **any** launcher boot after the 600 s gate was sweep-eligible, and tonight's was simply the first. The **operative bound under this plan** is the **≈10–15 min bounded absent-ceiling** (3 qualifying launcher observations × ≈3.5 min per boot in the `RestartSec=10` + ~200 s dying-boot environment). **Launcher/daemon precedence:** the launcher sweeps **pre-daemon** (`launcher.sh:1661-1662` vs the daemon child at `:1525`) ⇒ within one launcher boot the launcher observes first; the daemon closer ticks every 90 s while the daemon is up and reaches its verdict at the **first tick after the 600 s staleness gate** (≈11 min) ⇒ **the daemon closer normally wins the race**, and the launcher's bounded ceiling is the backstop for the shapes the daemon cannot reach (never-started daemon; tempfail-75 / clean-exit-0).

#### 4.3.2 THE TWO-SIDED REQUIREMENT (named explicitly — this lane's stage-time obligation)

> **The release bump commit MUST set BOTH**
> - `daemon/tools/upgrade_tools.py:3916` → `PIPELINE_SCRIPTS_FLOOR_COMMIT` (currently `953603108fa35d4f59031818c3091eecda8c1663`), **and**
> - `scripts/upgrade/lib.sh:95` → `PIPELINE_SCRIPTS_REV` (currently the same sha),
>
> **to the SAME merge commit** — the commit that introduces this lane's ceremony-relevant `scripts/upgrade` + promote semantics. The two *travel together* by house doctrine (D6-8 / T6.15 / AC15); the daemon-side constant and the scripts-side stamp are two provenance channels for one fact.

Enforcement, all mechanical, all already shipped:

| Gate | Site | Behaviour on violation |
|---|---|---|
| Floor is set and is a sha | `stage.sh:556` (extract), `:557-560` (refusal) | `exit 78` — **REFUSING TO STAGE** |
| Floor resolves and is an **ancestor of the built tree** | `stage.sh:562-565` (`git merge-base --is-ancestor`) | `exit 78` — **REFUSING TO STAGE** |
| Stamp is 40-hex | `lib.sh:596-600` (`_pipeline_scripts_stamp_verdict`) | `exit 78` — **REFUSING TO STAGE** (via `stage.sh:583-586`) |
| Stamp resolves in the tree | `lib.sh:601-603` | `exit 78` — **REFUSING TO STAGE** |
| Stamp **== floor** | `lib.sh:604-606` (*"the two travel together"*) | `exit 78` — **REFUSING TO STAGE** |
| Manifest carries the floor | `stage.sh:595` `pipeline_scripts_floor_commit` | The staged artifact is self-describing about the floor it will enforce |

**This lane's obligation:** the giter lane sets both constants in the **release bump commit** (exactly the house 4-file bump set plus these two value-carrying lines — `upgrade_tools.py` and `lib.sh` are *not* version files, so the bump is a targeted edit, never `git add -A`), then re-stages and confirms the floor+stamp validation passes and the manifest's `pipeline_scripts_floor_commit` equals the floor. **P3-PIN-13** and **P3-PIN-14** are the executable proof of this requirement.

#### 4.3.3 OPERATOR-OWNED CEREMONY PRECONDITION (stated, not ours to execute)

> The live `.env` `ENSEMBLE_UPGRADE_SCRIPTS_DIR` pin must be advanced to a tree whose HEAD has the **new floor commit as an ancestor**. This is an **operator/user action**, surfaced as a ceremony precondition in the lane's operator-facing doc — **not** this lane's work.

Enforcement point: the **arm-time provenance guard** — `daemon/tools/upgrade_tools.py:3701-3731` (Check 4, phase6) — is judged **before** `consume_pending_action`, so a refusal **never burns the nonce** (`upgrade_tools.py:3707-3712` states the sequencing clause explicitly). An **explicit** `ENSEMBLE_UPGRADE_SCRIPTS_DIR` pin is judged **REFUSE**; a **fallback** resolution (module-repo / `install_dir/scripts`) is downgraded to **WARNING** only (`:3723-3733`).

#### 4.3.4 Consequence table

| Condition | Consequence |
|---|---|
| Floor + stamp **not** both set to the same merge commit | `stage.sh` **refuses to stage** (`exit 78`) — the release cannot ship. Loud, early, at stage time — not at ceremony time. |
| Live `.env` `ENSEMBLE_UPGRADE_SCRIPTS_DIR` **not** advanced to a tree containing the new floor as an ancestor | The next scripted ceremony **refuses pre-nonce, BY DESIGN** (`:3701-3731`). The nonce is **not** burned. This is the guard working, not a regression. |
| A re-stage is attempted from a non-tip tree | Refused by the **L1 tip-identity guard** (`exit 78`). Promote the staged artifact **AS-IS**; do not override. |
| The rider's `launcher.sh` fix is merged but not yet promoted | The **live** launcher still has the BSD-only cooldown arm until the next promote. Interim protection = Phase 2's daemon-side closer + the launcher's defer path. (P3-R1.) |

---

### 4.4 CROSS-PHASE RECONCILIATION (consolidator's binding view)

Phase 1 and Phase 2 landed while this file was authored. This section reconciles them with the map above; where they differ, **this section is the authority** for the *consolidated* view.

#### 4.4.1 Pin-ID cross-reference

| Consolidated ID | Phase 1 | Phase 2 | Notes |
|---|---|---|---|
| P3-PIN-05 | — | `P-1` | launcher serving-green commit-close |
| P3-PIN-06 | — | `P-2` | launcher serving-unhealthy ⇒ existing rollback |
| P3-PIN-07 | — | `P-7` | tonight's 4-stage sequence |
| P3-PIN-08 | — | `P-3` | the C5 re-shape (C5a / C5b) |
| P3-PIN-09 | legs `L1`/`L2`/`L4` (and `L3` static) | — | Phase 1's legs live in `tests/test_supervision_stop_handback.sh`; Phase 1 adds a **new** PATH-injected `curl` stub seam |
| P3-PIN-10 | — | `P-4` | deferral + ceiling (needs the `:=` constant) |
| P3-PIN-11 | — | `P-6` | daemon closer, both directions |
| P3-PIN-12 | — | `P-8`, `P-9`, `P-11`, `P-12` | quarantine clearance both sides; adopt-path polarity; launcher purity scan; journal-lock discipline |
| P3-PIN-13/14 | — | — | **Phase 3-owned** (deployment pins) — no Phase 2 equivalent |
| P3-PIN-15/16/17 | lane-level | lane-level | harness hygiene; pack registration; the Tier-2 leg |
| P3-PIN-01…04 | — | — | **Phase 3-owned** (the rider) — no Phase 2 equivalent |

Phase 2's probe-primitive legs `P-5` and the version-string asymmetry `P-10` (folded into `P-1`/`P-5`/`P-6`) are **not re-listed** in the consolidated map — they are covered inside P3-PIN-05/-06/-11 and are owned by Phase 2's implementation. The consolidated map deliberately **does not renumber** Phase 1/Phase 2 entries.

#### 4.4.2 Binding sequencing constraint (Phase 2 → Phase 3)

Phase 2 states it explicitly (its §"Sequencing note (shared surface)"): *"Phase 2 rewrites the disposition decision inside `launcher.sh`'s flipped branch. The Phase-3 cooldown rider rewrites `_js_count_rollback` (`launcher.sh:473-478`) which is **called by** the disposition chain Phase 2 re-orders. **Phase 2 must land first** (or the rider must be folded in-commit with it); the rider must not be implemented on top of the pre-Phase-2 chain."*

**Phase 3 adopts this as binding.** Consequences carried into §7:
- `P3-T01` (the rider implementation) **depends on Phase 2 landing first**, or must be folded in-commit with Phase 2.
- `P3-T02`/`P3-T03` (the rider pins) must be written against the **post-Phase-2** `_journal_sweep`, so that the "cooldown armed ONLY on the rollback path" assertion (P3-Q8) is meaningful.
- If the rider is folded in-commit with Phase 2, the two edits are to *different functions* (`_journal_sweep`'s disposition branch vs `_js_count_rollback`) — the merge surface is disjoint, so the fold is mechanical.

#### 4.4.3 Tier reconciliation (this section overrides the per-phase statements)

- **Phase 1** states Tier 1, with the rationale that its diff touches no core seam and *"the gate is not promote-blocking, so the Tier-2 trigger does not apply"*. Correct **for Phase 1's scope in isolation**.
- **Phase 2** touches **`daemon/api.py`** (a listed core seam, `ensure.md:19`) and **`daemon/manager.py`** (also listed), plus `daemon/services/upgrade_journal_sweep.py`, `daemon/services/readiness.py`, `daemon/constants.py`, `daemon/tools/upgrade_journal.py`.
- **Therefore the LANE is Tier 2** — both mechanical triggers fire: the core-seam path-prefix list *and* the promote-blocking (release gate) arm. This is a **lane-level** property, not a per-phase one; the per-phase statements remain true within their own scopes.
- **Binding:** P3-PIN-17 (the Tier-2 whole-tree dual-side leg) is **mandatory for the lane**, not optional. The rider's own pins remain Tier 1.

#### 4.4.4 Launcher purity constrains the serving probe (settles P3-Q6)

Phase 2's purity pin `P-11` asserts `launcher.sh` contains **no executable `curl` / `wget` / `nc` / `lsof` / `python3`**. That is a **hard constraint on the launcher-side serving probe**: it cannot use `curl`, and it cannot use `lsof` either (so it cannot use `lib.sh`'s `_supervision_unit_serving` conjunct shape at `lib.sh:4749-4782`).

**Resolution (binding on Phase 2's launcher half):** the launcher-side probe must be **`bash /dev/tcp` + the launcher's existing bash JSON field parsing** for the `version` comparison; `lib.sh` (which is *not* purity-constrained) may use `curl` freely on the ceremony side — Phase 1's new `curl` stub seam lives in `lib.sh`'s path, so it is consistent. The two halves therefore gather **compatible but differently-transported** evidence (raw TCP + JSON in the launcher; HTTP via `curl` in `lib.sh`), and the *journal verdict* — not the transport — is the shared contract.

---

## 5. Decisions

| ID | Decision | Rationale (cite `file:line`) | Rejected alternatives |
|---|---|---|---|
| **P3-D1** | Fix the launcher cooldown with an **epoch-argument two-arm `uname` dispatch** (Option A), written in the launcher's own `_js_iso_to_epoch` (`:247-263`) / `_js_flip_current` (`:755-770`) idiom | Preserves the working BSD arm byte-for-byte; one `_now()` (`:1333`) call ⇒ one instant; launcher-internal idiom consistency | (i) mirror `lib.sh`'s relative form (`:2099-2107`) — changes the BSD `date(1)` dialect and re-derives "now" per arm; (ii) reuse `_js_iso_to_epoch` inverted — that helper is ISO→epoch, and the needed direction is epoch→ISO, so it would require a new helper |
| **P3-D2** | The rider's pins assert **property** (born-future + delta band + arm selection), not string equality; a static "no bare `date -jur` outside the BSD arm" pin is the string-level backstop | The BSD branch **cannot be behaviourally executed on a GNU host** (no BSD `date(1)`); C7's own live-verification block (`:346-360`) exercises only the GNU arm | String-equality against `lib.sh`'s form — would encode the launcher into a form it was not designed for, and would fail the moment the launcher idiom is re-tuned |
| **P3-D3** | The launcher cooldown pin set lands in a **NEW dedicated pack** (`tests/test_launcher_cooldown_arm.sh`), with fixtures from `tests/upgrade_test_lib.sh` | `_js_count_rollback` is currently referenced by **nothing** in `tests/`, `scripts/`, or `daemon/` — it needs a home that names it; `tests/test_boot_sweep_commit_and_continue.sh` is already 534 lines and is about the *sweep*, not the cooldown | (i) bolt sections onto the boot-sweep file — buries an unpinned function inside a 534-line file about a different subject; (ii) add to `tests/test_launcher.sh` — that file's subject is the *twin* and the *source pins*; adding the cooldown there muddies both |
| **P3-D4** | The cross-component **consumer round-trip** (P3-PIN-04) is the anchor rider pin | `cooldown_until` is written by the launcher and consumed only by `lib.sh:2139-2151` / `:6556-6558`; the defect's harm is a *disabled anti-flapping window*, which only the consumer can observe | Pinning only the producer's output shape — would pass while the window stayed dead |
| **P3-D5** | The two-sided floor/stamp requirement is enforced by **two executable pins** (P3-PIN-13 over `_pipeline_scripts_stamp_verdict`; P3-PIN-14 over the `stage.sh` floor block), not by documentation | `lib.sh:590-593` states the stamp gate was *extracted from `stage.sh` specifically so the gate is EXECUTABLE in a pack* — the same extraction discipline applies to the floor block | Relying on `stage.sh`'s own refusal as the test — it cannot run without a full PyInstaller build (the comment at `lib.sh:591-592` says so) |
| **P3-D6** | **Tier 2 is mandatory for the lane** (both triggers fire: `daemon/api.py` is a core seam, and the gate is a release gate); the rider's own pins are Tier 1 | `.agents/tester/rules/ensure.md:19-20` — the escalation is mechanical, never a judgment call | Tier 1 only — would under-cover a promote-blocking release-gate change |
| **P3-D7** | The operator `.env` pin advance is stated as a **ceremony precondition in the lane's operator-facing doc**, explicitly user-owned | `upgrade_tools.py:3701-3731` judges the pin **before the nonce is burned**; the refusal is the designed behaviour, so the plan's job is to make the precondition *visible*, not to work around the guard | (i) having the lane mutate the live `.env` — out of scope and operator-owned; (ii) loosening the guard — would defeat the very protection that caught a stale scripts tree |
| **P3-D8** | The rider is implemented **after Phase 2 lands** (or folded in-commit with it), never on the pre-Phase-2 disposition chain | Phase 2's sequencing note: `_js_count_rollback` is *called by* the disposition chain Phase 2 re-orders; the two edits touch different functions, so an in-commit fold is mechanical | Implementing the rider on the pre-Phase-2 chain — the pins would then be written against a sweep that no longer exists, and the "cooldown armed only on the rollback path" assertion would be vacuous |
| **P3-D9** | The launcher-side serving probe is **`bash /dev/tcp` + bash JSON parsing**; `curl` is admissible only in `lib.sh` | Phase 2's purity pin `P-11` forbids executable `curl`/`wget`/`nc`/`lsof`/`python3` in `launcher.sh`; `lib.sh` carries no such constraint (Phase 1's new `curl` stub seam lives there) | `curl` in the launcher — would trip `P-11`; `lsof` in the launcher — same pin, and it would also re-create the raw-port-listener conjunct that the incident showed to be insufficient |

---

## 6. Requirements

| ID | Requirement | Verified against |
|---|---|---|
| **P3-R1** | The rider's fix reaches live **only at the next promote**; the interim window is covered by Phase 2's daemon-side closer (first sweep tick after the 600 s staleness gate) and the launcher's defer path | `stage.sh:424`; `upgrade_tools.py:3701-3731`; `launcher.sh:69`, `:906` |
| **P3-R2** | The launcher's cooldown arm must produce a **parseable, future** stamp on this GNU host, consumable as **ACTIVE** by `lib.sh`'s `journal_cooldown_active` | `launcher.sh:474-476`; `lib.sh:2139-2151`, `:6556-6558` |
| **P3-R3** | Both `uname` branches must be **selected** correctly and the BSD branch must be **statically** pinned (it cannot be executed here) | `tests/test_supervision_stop_handback.sh:321-332`, `:812-825` |
| **P3-R4** | Every planned pin is registered in `.agents/tester/PACKS.md` with **command + scope + verdict** | `.agents/tester/PACKS.md` (3 349 lines; the existing per-pack table format) |
| **P3-R5** | The **composition requirement** is proven in **both** directions: serving-green ⇒ commit-close (no rollback, no quarantine punishment, no counter increment); serving-unhealthy ⇒ the existing never-green rollback still fires | owner ruling; Ari's composition requirement; `launcher.sh:1209-1228` |
| **P3-R6** | The release bump sets **both** `PIPELINE_SCRIPTS_FLOOR_COMMIT` and `PIPELINE_SCRIPTS_REV` to the **same merge commit** | `upgrade_tools.py:3916`; `lib.sh:95`; `stage.sh:556-565`, `:582-586`; `lib.sh:594-615` |
| **P3-R7** | Every frozen-binary boot leg pins `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` **explicitly**, scrubs `POSTGRES_*` **per-variable**, and sets `ENSEMBLE_SELF_ENV=sandbox` | project critical-note conventions (sweep-kills-live-processes; `env -u POSTGRES_*` silent NO-OP) |

---

## 7. Tasks

| ID | Task | Outcome (testable) | Touchpoints (file:anchor) | Depends on | Risks carried | AC |
|---|---|---|---|---|---|---|
| **P3-T01** | Implement the rider fix in `launcher.sh` | `_js_count_rollback` carries a two-arm `uname` dispatch; on GNU the armed `cooldown_until` is born ~600 s in the future | `launcher.sh:459-478` (defect `:474`, fallback `:475`, write `:476`); the `:455-458` comment block updated to state the two-arm dispatch | **Phase 2 lands first** (or in-commit fold) — P3-D8 | P3-R1, P3-R8 | AC-P3-1 |
| **P3-T02** | Write the rider pin set (P3-PIN-01, -02, -03) | A new pack proves the born-future property, both-arm selection, and zero bare BSD-date sites | NEW `tests/test_launcher_cooldown_arm.sh`; fixtures from `tests/upgrade_test_lib.sh:33`, `:69-106`; `uname` stub precedent `tests/test_supervision_stop_handback.sh:321-332` | P3-T01 | P3-R3 | AC-P3-2, AC-P3-3 |
| **P3-T03** | Write the cross-component consumer pin (P3-PIN-04) | The launcher-armed cooldown is consumed by `lib.sh`'s `journal_cooldown_active` as **ACTIVE** | NEW `tests/test_launcher_cooldown_arm.sh`; consumer `scripts/upgrade/lib.sh:2139-2151` | P3-T01, P3-T02 | P3-R2 | AC-P3-2 |
| **P3-T04** | Re-shape C5 and add the Part-2 launcher pins (P3-PIN-05 … -08, -10) | C5 is parameterised on serving health; both directions and tonight's sequence are pinned | `tests/test_boot_sweep_commit_and_continue.sh:279-302` (C5), `:66-85`, `:94-104`, `:123-134` | Phase 2's implementation | P3-R5 | AC-P3-4 |
| **P3-T05** | Add the Part-1 slow-boot hand-back pin (P3-PIN-09) | A slow unit-active→serving boot inside the dedicated budget does not trip the abort | `tests/test_supervision_stop_handback.sh` (new section `D`); seams `:92-93`, `:114-138`, `:174-183`, `:657-673` | Phase 1's implementation | — | AC-P3-5 |
| **P3-T06** | Add the daemon-side serving-green pin (P3-PIN-11) + the never-green regression set (P3-PIN-12) | The daemon tick commits-close on green and still rolls back on unhealthy; the never-green family is unchanged | `tests/unit/tools/test_upgrade_journal_sweep_liveness.py` (472 L) + NEW daemon-side pin file; `tests/packs/promote_soak_never_green_test.sh` + the fail-fast + grace family | Phase 2's implementation | P3-R5 | AC-P3-6 |
| **P3-T07** | Write the deployment pins (P3-PIN-13, -14) | The stamp gate and the floor gate are both executable and both refuse on violation | `scripts/upgrade/lib.sh:594-615` (stamp verdict, already extracted); `scripts/upgrade/stage.sh:556-565` (floor block — extract to a testable form, mirroring the stamp extraction) | — | — | AC-P3-7 |
| **P3-T08** | Apply the two-sided release-bump requirement | The bump commit sets `upgrade_tools.py:3916` **and** `lib.sh:95` to the **same** merge commit | `daemon/tools/upgrade_tools.py:3916`; `scripts/upgrade/lib.sh:95`; targeted `git add --` (never `-A`) | P3-T07 | — | AC-P3-8 |
| **P3-T09** | Register every pack in `.agents/tester/PACKS.md` | Each pack has a command + scope + verdict row; keep-both-newest-first on conflicts | `.agents/tester/PACKS.md` | P3-T02…T07 | — | AC-P3-9 |
| **P3-T10** | Add the frozen-binary boot-smoke env pins to every booting leg | No boot leg can run without the explicit sweep guard, the per-variable scrub, and `ENSEMBLE_SELF_ENV=sandbox` | the boot-smoke legs in the Phase 1/Phase 2 packs; `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` explicit | P3-T04…T06 | P3-R7 | AC-P3-10 |
| **P3-T11** | Write the operator ceremony precondition | The operator-facing doc states the `.env` `ENSEMBLE_UPGRADE_SCRIPTS_DIR` advance, the exact guard that will refuse otherwise, and the nonce-not-burned semantics | the lane's operator-facing runbook/doc; `upgrade_tools.py:3701-3731` | P3-T08 | P3-D7 | AC-P3-11 |
| **P3-T12** | Run the lane's gate legs | Tier-1 scoped dual-side per the declared blast radius (≤ 30 min, fail-loud) **and** the mandatory Tier-2 whole-tree dual-side (≤ 90 min advisory) with net-new reds = 0 | recipe `.agents/tester/skills/blast-radius-leg/`; `.agents/tester/skills/whole-tree-base-proven-diff/`; `.agents/tester/skills/failed-digest-equality-gate/`; `.agents/tester/skills/regression-ab-partition-run/`; `.agents/tester/skills/ab-base-leg-provisioning-artifacts/` | P3-T02…T11 | — | AC-P3-12 |
| **P3-T13** **[PLANNER r3]** | Add the **degraded-but-grace-held ⇒ DEFER** pin (`P3-PIN-27`) to the daemon-side pack, alongside the never-green leg (`P3-PIN-11a`) | The daemon-side pack carries **both** composition directions of the F-1 boundary: a **grace-held** composite ⇒ **DEFER** (no verdict, no request); a **not-grace-held** unhealthy composite ⇒ the verdict + request (the existing chain still fires). The two legs must use **different** fixtures so one stub cannot satisfy both | `tests/unit/tools/test_upgrade_journal_sweep_liveness.py` + the NEW daemon-side pin file (the same seam as `P3-PIN-11a`) | Phase 2's implementation (`T18`) | P3-R5 | AC-P3-6 |

---

## 8. Acceptance Criteria

| ID | Criterion |
|---|---|
| **AC-P3-1** | `launcher.sh`'s `_js_count_rollback` contains both `uname` arms and **zero** bare `date -jur` sites outside the BSD arm. |
| **AC-P3-2** | On this GNU host, the launcher-armed `cooldown_until` is born within `[SWEEP_COOLDOWN_S − 60 s, SWEEP_COOLDOWN_S + 60 s]` of the future, **and** `lib.sh`'s `journal_cooldown_active` reports it **ACTIVE**. |
| **AC-P3-3** | Arm selection is pinned for `Darwin`, `Linux`, and an unrecognized platform; the BSD arm is pinned **statically** (BSD `date(1)` cannot execute on a GNU host) and that limitation is **disclosed in the tester's report**. |
| **AC-P3-4** | The re-shaped C5 proves **both** directions: serving-green ⇒ commit-close with no rollback, no quarantine append, no counter increment, no cooldown arm; serving-unhealthy ⇒ the existing rollback path fires with all four effects. |
| **AC-P3-5** | A slow unit-active→serving boot inside the dedicated hand-back budget does not trip the abort predicate; the pre-fix fixed-budget behaviour goes red. |
| **AC-P3-6** | The daemon-side tick commits-close on serving-green (with the `quarantine_cleared_serving_green` journal row) and still rolls back on serving-unhealthy; the never-green pack family is unchanged. |
| **AC-P3-7** | The stamp gate and the floor gate are executable in packs and each refuses on its violation class (non-hex / unresolvable / ≠ floor; unset / not-a-sha / not-an-ancestor). |
| **AC-P3-8** | In the release bump commit, `PIPELINE_SCRIPTS_FLOOR_COMMIT` and `PIPELINE_SCRIPTS_REV` carry the **same** merge commit, and `stage.sh` validates both (`:556-565`, `:582-586`) with the manifest's `pipeline_scripts_floor_commit` (`:595`) equal to the floor. |
| **AC-P3-9** | Every pin in §4.2.2 is registered in `.agents/tester/PACKS.md` with command + scope + verdict. |
| **AC-P3-10** | Every frozen-binary boot leg pins `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` explicitly, scrubs `POSTGRES_*` per-variable, and sets `ENSEMBLE_SELF_ENV=sandbox`. |
| **AC-P3-11** | The operator-facing doc states the `.env` `ENSEMBLE_UPGRADE_SCRIPTS_DIR` advance as a ceremony precondition, with the guard's semantics (refuse pre-nonce, nonce not burned). |
| **AC-P3-12** | Tier-1 scoped dual-side: net-new reds = 0 over the declared blast radius, ≤ 30 min, fail-loud. Tier-2 whole-tree dual-side: net-new reds = 0, ≤ 90 min advisory, with `--collect-only` slicing proof recorded and the node-cap / rate-projection guard applied. |

---

## 9. Risks

| ID | Risk | Impact | Mitigation |
|---|---|---|---|
| **P3-R1** | The rider's fix ships **source-side only** (`launcher.sh` reaches live at the next promote). Between merge and promote, the live launcher still arms a born-expired cooldown, and the anti-flapping window stays disabled. | medium | Phase 2's daemon-side closer acts at the first sweep tick after the 600 s staleness gate (`launcher.sh:69`, `:906`) — far inside the ~59 min fuse; the launcher's defer path is the pre-daemon backstop. Disclose the window in the release notes. **[PLANNER r4 → note (g)]** the *"~59 min fuse"* phrase is **SUPERSEDED** (append-only; the text above is retained for the audit trail): there is **no** 3540 s fuse constant in the tree (verified ⇒ 0 hits) — tonight's 3540 s was the *computed* age of the **unconditional** auto-rollback at a launcher boot, not a designed bound. The **operative** bound is the **≈10–15 min bounded absent-ceiling** (AM-P2-10); the daemon closer normally wins at ≈11 min (first tick after the 600 s gate), and the launcher's bounded ceiling is the backstop. |
| **P3-R2** | Editing `launcher.sh` could disturb the twin/source pins in `tests/test_launcher.sh` (notably 8b-r1 at `:831-848`, a **source-level** pin on `_journal_sweep`). | medium | The rider's edit is to `_js_count_rollback`, **outside** the 8b-r1 extraction scope (`^_journal_sweep\(\)`), but Phase 2's edit is **inside** it → 8b-r1 must be re-verified in the Tier-1 leg, and the whole-tree Tier-2 leg covers it. |
| **P3-R3** | The BSD branch cannot be behaviourally exercised on this GNU host; a regression there would ship undetected. | low–medium | Arm-selection + argument-shape + static pins (P3-PIN-02, -03); the limitation is **disclosed**, not papered over. This mirrors C7's own coverage boundary. |
| **P3-R4** | The `.env` `ENSEMBLE_UPGRADE_SCRIPTS_DIR` advance is operator-owned and outside the lane. If it is missed, the next scripted ceremony refuses pre-nonce. | low | By design (fail-closed, nonce not burned — `upgrade_tools.py:3701-3731`). The lane's job is to make the precondition **visible** (T11) and to surface the floor in the staged manifest (`stage.sh:595`). |
| **P3-R5** | The cooldown value is a **cross-component** journal field; changing the producer's semantics without the consumer's test creates a silent regression in the other direction. | medium | P3-PIN-04 (the round-trip pin) is the guard, and it is the anchor rider pin (P3-D4). |
| **P3-R6** | The two-sided floor/stamp requirement can be half-applied (one constant updated, the other not) — the release then refuses to stage at `exit 78` late in the lane. | low | Fail-fast by construction (`stage.sh:557-565`, `:583-586`); P3-PIN-13/-14 prove the requirement in packs *before* the stage attempt; the bump is a targeted edit, never `git add -A`. |
| **P3-R7** | A frozen-binary boot smoke run by a leg could touch the **live** install (the path-derivation + sweep hazards). | high | P3-PIN-15's three env pins, plus the standing house fences: explicit `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` (never `env -u`), per-variable `POSTGRES_*` scrub (the glob form is a silent NO-OP), `ENSEMBLE_SELF_ENV=sandbox`, the F1 fake-HOME fence (`tests/upgrade_test_lib.sh:69-102`), and the "staged boot smokes are not isolated" rule (`daemon/api.py:2545` + `daemon/tools/upgrade_tools.py:658`). |
| **P3-R8** | Two lanes (this one and the sibling event-loop lane) could stage concurrently and contend for the next free version slot. | low | Version-slot arbitration is the leader's at stage time; this phase stages nothing. |
| **P3-R9** | **Sequencing:** the rider edits a function *called by* the disposition chain Phase 2 re-orders. If the rider lands first, the Phase 2 pins are written against a sweep that no longer exists — and vice versa, the rider's "cooldown armed only on the rollback path" assertion is vacuous. | medium | P3-D8 makes the ordering explicit; the two edits touch **different functions** (`_journal_sweep`'s disposition branch vs `_js_count_rollback`), so an in-commit fold is mechanical and conflict-free. |
| **P3-R10** | The `daemon/constants.py` seal tripwire (`tests/test_chart_image_delivery_e2e.py:1515-1535`) is **already red at base** (baseline `464a2459…` vs worktree `5dd9bfa3…`). Phase 2 touches `daemon/constants.py`, so the lane will encounter this red and could misread it as a regression — or "fix" it by re-pinning the seal, which would launder pre-existing debt into the lane's diff. | medium | Record it as a **base red on both legs** in the FAILED-digest comparison (the `red-at-base ≠ net-new` rule, `ensure.md:27`); **do not re-pin the seal** in this lane (see P3-Q9). |

---

## 10. Success Criteria

- The launcher cooldown defect is **closed at source** with both `uname` arms and a pin set that proves the born-future property, arm selection, and the cross-component consumer round-trip — with the BSD-branch coverage limitation disclosed.
- The consolidated test-surface map is **complete and actionable**: every pin carries a part, a file, a harness seam, a tier, a claim, and a mutation expectation; every pack is registered in `.agents/tester/PACKS.md`.
- The **composition requirement is proven in both directions** — serving-green ⇒ commit-close; serving-unhealthy ⇒ the existing never-green rollback still fires — and the never-green pack family is unchanged.
- The **two-sided release-bump requirement** is named, pinned executably, and satisfied by the bump commit; `stage.sh` validates both constants and the manifest carries the floor.
- The **operator ceremony precondition** is stated in the operator-facing doc with the guard's exact semantics.
- The lane's **Tier-2 whole-tree dual-side leg passes with net-new reds = 0**; Tier-1 scoped leg passes within its 30-minute HARD budget.
- Zero live-install contact; zero execution of `launcher.sh` / `promote.sh` / `lib.sh` during planning or implementation.

---

## 11. Open Questions (numbered — for the consolidating worker; each carries a recommendation)

**P3-Q1 — Which cooldown fix form?**
Option A (epoch-argument two-arm, `:474` → `date -jur <epoch>` / `date -u -d "@<epoch>"`) vs Option B (mirror `lib.sh:2099-2107`'s relative form).
**Recommendation: Option A**, written in the launcher's own `uname`-dispatch idiom (`_js_iso_to_epoch` `:247-263` / `_js_flip_current` `:755-770`). It preserves the working BSD arm byte-for-byte, computes the target instant once, and avoids introducing `lib.sh`'s `date(1)` dialect into the launcher. If the leader prefers codebase-wide canonical form, take Option B — but then P3-D2 (property-assert, not string-assert) still holds, and the static no-bare-date pin must be re-derived.

**P3-Q2 — What should the unrecognized-platform `*)` arm do?**
(i) Mirror `lib.sh:2105-2107` — write `_js_now_iso` (a parseable, already-expired stamp ⇒ the anti-flapping window is silently disabled); (ii) write **nothing** (leave the field untouched) + a loud stderr + a journal history row (honest: no cooldown was armed); (iii) write an **unparseable** marker (the consumer's fail-closed path at `lib.sh:2144-2146` treats it as ACTIVE ⇒ promotes blocked until the next rollback overwrites it).
**Recommendation: (ii)** — do not fabricate a stamp. Option (i) reproduces exactly the corrupt state that made tonight's incident hard to read (`cooldown_until 22:28:40Z` preceding the `22:29:58Z` sweep row). Option (iii) is fail-closed but self-locking. The residual (the window is genuinely absent on such a platform) is disclosed, and the platform is practically unreachable given the launcher's two other `uname` dispatches already fail closed there.

**P3-Q3 — Should the launcher cooldown be de-duplicated against `lib.sh`'s `journal_count_rollback`?**
**Recommendation: no — launcher-only in this lane.** The launcher cannot source `lib.sh` (it needs the install dir, and `launcher.sh:8-15` fixes the launcher as a standalone sourced unit) and the R-08 purity constraint bars `python3` from the launcher. De-duplication is a separate lane; the rider should reduce divergence, not attempt a refactor.

**P3-Q4 — Pin placement for the rider?**
New dedicated pack (`tests/test_launcher_cooldown_arm.sh`) vs new sections in `tests/test_boot_sweep_commit_and_continue.sh` vs `tests/test_launcher.sh`.
**Recommendation: the NEW dedicated pack**, reusing `tests/upgrade_test_lib.sh` for `_register_fixture` + the F1 fake-HOME fence. The boot-sweep file is 534 lines about a different subject; `tests/test_launcher.sh` is about the twin and the source pins. A dedicated file also makes the "previously zero references" fact legible in the PACKS.md scope column.

**P3-Q5 — The C7 section header cites a stale line (`tests/test_boot_sweep_commit_and_continue.sh:329` says "lib.sh:982"; the dispatch is at `lib.sh:2099-2107`). Correct it here?**
**Recommendation: correct it in this lane** — a one-line doc-only change in the very file the new pins land in, disclosed as a doc-only rider. Leaving a stale reference in a file we are extending risks the next worker encoding another stale ref.

**P3-Q6 — Serving probe for the launcher's serving-green check: `bash /dev/tcp` (pure) or `curl`?**
**Recommendation: `bash /dev/tcp` + the launcher's existing bash JSON field parsing.** This is no longer a preference — Phase 2's purity pin `P-11` forbids executable `curl`/`wget`/`nc`/`lsof`/`python3` in `launcher.sh`, so `curl` **cannot** be used there, and `lsof` (the conjunct shape at `lib.sh:4749-4782`) is barred too. `lib.sh` is *not* purity-constrained, so `curl` is admissible on the ceremony side — Phase 1's new `curl` stub seam lives in `lib.sh`'s path, and the two halves therefore gather compatible-but-differently-transported evidence. Recorded as P3-D9 (binding).

**P3-Q7 — Must the rider ship in the SAME release as the Phase-2 closer?**
**Recommendation: yes.** They sit in the same disposition chain, and the cross-component consumer semantics (`lib.sh:2139-2151`) change in the same promote. Splitting them would leave a window where the launcher arms a cooldown the daemon-side commit-close path does not expect. (Phase 2's own sequencing note independently requires the rider to land *after* Phase 2 — see P3-D8.)

**P3-Q8 — Does a serving-green commit-close leave the cooldown untouched?**
**Recommendation: yes — a serving-green close is NOT a rollback.** It must not increment `rollback_window_count`, must not arm `cooldown_until`, and must not append a quarantine entry. Phase 2 already states this ("NOT done: sweep-rollback, quarantine, counter+cooldown arming. The cap/cooldown are anti-flapping responses to BAD outcomes; a successful-and-declared flip is not a bad outcome", `launcher.sh:1034-1036`). P3-PIN-05 asserts all three effects. Corollary for the rider: the cooldown arm must remain reachable **only** from the rollback path — `_js_count_rollback` must not be called on the serving-green branch. This is a **pin obligation on Phase 2**, not a Phase-3 design decision.

**P3-Q9 — Should the lane re-pin the `daemon/constants.py` seal?**
The seal tripwire is **already red at base** (baseline `464a2459…` vs worktree `5dd9bfa3…` — a pre-existing base red, not caused by Phase 2's edit to that file).
**Recommendation: no — do not re-pin in this lane.** Re-pinning would fold pre-existing base debt into the lane's diff and would mask the fact that the seal has been out of date since before this commission. Record the red as base-red on both legs in the FAILED-digest comparison; route the seal re-pin to its own (small) lane if the team wants it closed. (This mirrors the house pattern of keeping a pre-existing red visible rather than laundering it — cf. the v0.26.3 lane's "base-proven seal tripwire" disclosure.)

**P3-Q10 — Should the Phase-3 IDs be renumbered into the plan set's own convention?**
Phase 2 uses unprefixed `T1…Tn` and `P-1…P-12`; Phase 1 uses `L1…L4` legs and `AC-P1-*`. This phase uses a `P3-` prefix because Phase 1/2 were absent when it was authored.
**Recommendation: keep the `P3-` prefix in the consolidated set** and record the cross-reference table in §4.4.1 rather than renumbering — renumbering Phase 1/Phase 2 entries would break their internal references (Phase 2's own text cites `P-1`…`P-12` and the leader's summary cites phase-owned ids). If the consolidating worker prefers a single flat sequence, it must **append** new ids and preserve the existing ones, per the plan-creation delta-fold rule (preserve all existing numbering; append with the next free numbers).

---

## 12. Citation appendix (the anchors this phase is the authority for)

| Fact | Anchor |
|---|---|
| Cooldown defect site (BSD-only `date -jur`) | `launcher.sh:474` |
| Cooldown fallback (writes NOW) | `launcher.sh:475` |
| Cooldown journal write | `launcher.sh:476` |
| Cooldown function bounds | `launcher.sh:459-478` |
| Sibling-safe `_js_iso_to_epoch` call inside the same function | `launcher.sh:467` |
| `:474` is the only `date -j` site in `launcher.sh` | **[PLANNER r4 → note (f) CORRECTED]** full-file grep: the only **bare `date -jur`** site is `:474`; the other **executable** `date -j` site is **`:251`** (`date -ju -f`, inside `_js_iso_to_epoch`'s **correctly dispatched** BSD arm, `:249-258`); `:238` is a comment; **`:384` is NOT a comment** (it is `_js_journal_update`'s docstring) |
| Canonical pinned twin (the form to mirror) | `scripts/upgrade/lib.sh:2099-2107` |
| Twin function (`journal_count_rollback`) | `scripts/upgrade/lib.sh:2055+` |
| Cooldown consumer (fail-closed on unparseable) | `scripts/upgrade/lib.sh:2139-2151` |
| Cooldown gate at promote entry | `scripts/upgrade/lib.sh:6556-6558` |
| Launcher `uname`-dispatch precedent #1 (fail-closed) | `launcher.sh:247-263` |
| Launcher `uname`-dispatch precedent #2 | `launcher.sh:755-770` |
| Launcher constants | `launcher.sh:69` `SWEEP_STALE_S=600`; `:75` `SWEEP_COOLDOWN_S=600` |
| Launcher time helpers | `launcher.sh:228` `_js_now_iso`; `:1333` `_now` |
| Launcher sweep disposition chain | `launcher.sh:1209` flip → `:1218` quarantine → `:1220` current=prev → `:1223` count+cooldown → `:1225-1226` `sweep_rollback` row → `:1228` `in_flight=null` |
| Launcher commit-and-continue gate (`intent_flip` ∧ `prev.rollback_safe=false`) | `launcher.sh:1069-1079` |
| Launcher staleness gate | `launcher.sh:69`, `:906` |
| Launcher sweep entry | `launcher.sh:830` `_journal_sweep` |
| C7 cooldown pin (canonical form + GNU live verification) | `tests/test_boot_sweep_commit_and_continue.sh:329-366` |
| C5 (the unconditional-rollback pin to re-shape) | `tests/test_boot_sweep_commit_and_continue.sh:279-302` |
| Boot-sweep harness seams | `tests/test_boot_sweep_commit_and_continue.sh:66-85`, `:94-104`, `:123-134` |
| Hand-back harness seams | `tests/test_supervision_stop_handback.sh:92-93`, `:114-138`, `:174-183`, `:657-673` (`LIVEZ_BUDGET_S=2` at `:664`) |
| `uname` PATH-injection precedent | `tests/test_supervision_stop_handback.sh:321-332` (A′4), `:812-825`; seam documented `:32-38` |
| Shared fixture registry + F1 fake-HOME fence | `tests/upgrade_test_lib.sh:33`, `:69-102`, `:104-106` |
| Launcher source pin (8b-r1) — Phase 2 touches its scope | `tests/test_launcher.sh:831-848` |
| Gate tiers + proof form + recipes | `.agents/tester/rules/ensure.md:13-28`; recipes `.agents/tester/skills/{blast-radius-leg,whole-tree-base-proven-diff,regression-ab-partition-run,ab-base-leg-provisioning-artifacts,failed-digest-equality-gate}/` |
| Pack registration | `.agents/tester/PACKS.md` (3 349 lines) |
| Payload composition | `scripts/upgrade/stage.sh:395-433`; launcher copy `:424`; `LAUNCHER_SHA` `:438`; payload header `:11-14`; manifest `:595` (floor) and `:599` (`launcher_sha256`) |
| Floor validation / refusal | `scripts/upgrade/stage.sh:556` (extract), `:557-560`, `:562-565` |
| Stamp validation / refusal | `scripts/upgrade/stage.sh:582-586`; `scripts/upgrade/lib.sh:594-615` (equality check `:604-606`) |
| Daemon-side floor constant | `daemon/tools/upgrade_tools.py:3916` |
| Scripts-side stamp | `scripts/upgrade/lib.sh:95` |
| Scripts-dir resolution (source-less scripts) | `daemon/tools/upgrade_tools.py:1979-2002`; call sites `:2887`, `:3572` |
| Arm-time provenance guard (pre-nonce) | `daemon/tools/upgrade_tools.py:3701-3731` (verdict call `:3718-3721`; fallback→WARN downgrade `:3723-3733`) |
| Frozen-binary boot-smoke env pins | project critical-note conventions: explicit `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0`; per-variable `POSTGRES_*` scrub; `ENSEMBLE_SELF_ENV=sandbox`; staged smokes are not isolated (`daemon/api.py:2545`, `daemon/tools/upgrade_tools.py:658`) |
| Journal fixture corpus | `tests/helpers/journal_fixture_incident.py`, `tests/helpers/journal_fixture_corpus.py`, `tests/fixtures/journal/` (10+ files) |
| Daemon-side pytest corpus | `tests/unit/tools/test_upgrade_journal_sweep_liveness.py` (472 L), `test_promote_txn_hold.py`, `test_upgrade_{journal,preflight_daemon_side,reasons_reader,splice_helper,registration,tools}.py`, `test_upgrade_install_root_layer1.py`, `test_upgrade_executor_systemd_service.py`, `test_promote_cgroup_survivorship_python.py` |
| Never-green / soak pack family | `tests/packs/promote_soak_never_green_test.sh`, `promote_soak_fail_fast_test.sh`, `promote_soak_fail_fast_integration_test.sh`, `promote_soak_grace_burst_test.sh`, `promote_soak_grace_counter_unit_test.sh`, `preflight_real_soak_test.sh`, `soak_gate_mcp_warmup_acceptance.sh` |
| Standing journal regression | `tests/test_release_journal.sh` |
| **Cross-phase anchors added after Phase 1/2 landed** | |
| Phase 2's sequencing note (Phase 2 must land first) | `phase2-plan.md` §"Sequencing note (shared surface)" + its coupling table |
| Phase 2's pin IDs + fixture notes | `phase2-plan.md` §"Acceptance / test pins" (`P-1`…`P-12`), its fixture note (launcher constants are plain assignments), its fixture-reuse note (`soak_gate_fake_daemon.py`) |
| Phase 2's launcher purity constraint (no `curl`/`wget`/`nc`/`lsof`/`python3` in `launcher.sh`) | `phase2-plan.md` pin `P-11` |
| Phase 1's test legs + the new `curl` stub seam | `phase1-plan.md` §9 (legs `L1`…`L4`; `AC-P1-10`…`AC-P1-12`) |
| The fake-daemon serving-shape stub | `tests/helpers/soak_gate_fake_daemon.py` (modes `green`, `hard-red`, `never-all`, `never-readyz`, `flip`, `version-aware`); `tests/helpers/soak_gate_pack_common.sh` |
| The `daemon/constants.py` seal tripwire (pre-existing base red) | `tests/test_chart_image_delivery_e2e.py:1425` (`SEALED_SHA_BASELINES`), `:1467` (the `daemon/constants.py` entry), `:1515-1535` (`test_phase_d_does_not_modify_sealed_artifacts`); baseline `464a2459…` vs worktree `5dd9bfa3…` |


---

## [ARCHITECT r1] Amendments — 2026-11-02 (enrichment pass; anchors re-verified in-tree at base 941cb6321)

### AM-P3-1 — OQ-C-17 RULED (architect): option (iii)-scoped
Option (ii) "write nothing" is **refuted by the consumer code** (`lib.sh:2139-2149`, verified): absent field ⇒ `[ -z "$cd_epoch" ]` ⇒ warn + `return 0` = **ACTIVE** (self-locking — option (iii)'s cost without its honesty); stale parseable-expired ⇒ final compare false ⇒ NOT-active ⇒ the anti-flapping window is **silently disabled** — the exact defect class this commission closes. **Ruling: (iii)-scoped** — the `*)` arm writes an explicitly unparseable marker (not `now`, not any ISO stamp) + loud stderr + history row ⇒ the consumer reads ACTIVE (fail-closed, loud). **Pin:** assert the consumer verdict (ACTIVE) + the value shape (marker, not ISO) — the same property class as P3-PIN-04.
**Named follow-up (OUT of scope — record, do not fix):** the `lib.sh:2105-2107` twin writes `_now_iso` on unrecognized platforms (option-(i) behavior) — AND so do the recognized-platform `date`-failure fallbacks (`lib.sh:2101-2102`, `:2104-2105` `|| until="$(_now_iso)"`) — i.e. option-(i) is reachable on RECOGNIZED platforms too. Ceremony-side twin = its own small commission.

### AM-P3-2 — Deployment transition windows W1–W4 (matrix; corrects §6.1/§4.3 in decisions.md)
- **W1 (the v0.28.6 ceremony itself): COVERED, conditional.** The NEW launcher lands at promote step 3 (`promote.sh:537` `launcher_swap`) BEFORE the flip; the NEW daemon runs from step 6 — so a step-7 hand-back halt is covered by the new launcher + daemon closer. **BUT the Phase-1 verifier is source-less ⇒ W1's protection DEPENDS on the scripts-pin advance AND the floor bump riding the SAME bump commit as the Phase-1 script change** (P3-D5 does this; the plan must STATE the dependence). Make the `.env` `ENSEMBLE_UPGRADE_SCRIPTS_DIR` advance an **enumerated hard ceremony pre-step**; the current pin target (`ensemble-src-wt-scripts-pin` @ `22fe83317`) does NOT carry the new floor ⇒ as-is the ceremony refuses pre-nonce (the guard working; nonce unburned).
- **W3 (rollback of v0.28.6 ⇒ old code returns): three accepted residuals + one gate.** (i) old `_TERMINAL_EVENTS` lacks `serving_green_commit` ⇒ an armed pending_op is not closed by it (state leak; no wrongful rollback); (ii) old `_OUTCOME_LABELS` renders the raw key (cosmetic); (iii) **old `promote_entry_check` re-arms the frozen quarantine rule for a version cleared by `quarantine_cleared_serving_green`** ⇒ the operator must re-stage after a rollback; (iv) a txn left DEFERRED by the new launcher then handed to the OLD launcher ⇒ the old unconditional auto-rollback ⇒ the incident shape returns for that txn. **Gate: the v0.28.6 rollback runbook must require resolving any open flipped/deferred txn BEFORE rollback.**
- **W4 (merge→promote interim) — CORRECTION to decisions.md §6.1:** the interim has **NO new protection** — neither the daemon-side closer nor the launcher defer path is live before the promote (both are payload/binary-side). The only live guards are the existing 3/24h cap (live `rollback_window_count` = **2/3** — one more rollback arms a 24h promote halt) and operator intervention. State it plainly.
- **W2 — DEFINED (the matrix previously skipped W2 entirely).** **W2 = the post-ceremony steady state**: the new launcher **and** the new daemon binary are live and serving `v0.28.6`, with no rollback pending. **Content:** the whole new disposition set is live — the launcher defer + the canonical GREEN probe (**AM-P2-11**), the daemon-side closer, the **bounded absent-ceiling** (**AM-P2-10**), the T14 request handoff, and the `quarantine_cleared_serving_green` clearance on both sides. **W3's four residuals do NOT apply in W2** (they are the *rollback* of `v0.28.6`, i.e. W3). **W2's old-code-skew exposure is VACUOUS** — *the launcher and the daemon binary ship atomically in the same release payload* (`stage.sh:395-433`: the payload is binary + agents + frontend + plugins + config + launcher), and the `current` symlink moves the whole directory ⇒ **no window exists in which the old launcher runs against the new binary or vice versa**. The one intra-ceremony sub-window (the launcher swapped at `promote.sh:537` ⇒ the daemon binary live at step 6) is a **sub-case of W1** and is covered by it.
- **[PLANNER r2] W1's conditional gains one more enumerated pre-step.** The v0.28.6 ceremony is covered only if the **scripts-pin advance** lands (the current `.env` target `ensemble-src-wt-scripts-pin` @ `22fe83317` predates the new floor ⇒ as-is the ceremony refuses **pre-nonce**, guard working, nonce unburned). **[PLANNER r2]** Add the **second** enumerated hard pre-step: the **operator must confirm the `SWEEP_SERVING_GREEN_ENABLED` default** (Phase 2's launcher kill-switch, default `1`; `0` restores today's unconditional auto-rollback) is in force on the ceremony's launcher — the switch exists so a deterministic-NOT-GREEN defer on an unverified platform is **never** a permanent non-escapable state. It is named in the runbook (Phase 2 `T13`), and the ceremony notes must state which value it was left at.
- **Version normalization — P3-PIN-18 (promoted from folded P-10):** one mutation-verified leg PER comparison site (launcher probe / daemon closer / adopt path) asserting a v-prefixed target == bare `__version__` ⇒ GREEN; negative leg (target `v0.28.5` vs daemon `0.28.4` ⇒ NOT green); anti-`tr -d v` leg. Uniform rule: strip ONE leading `v` from BOTH sides at the point of comparison (idempotent — cannot double-strip). Consolidated additions: P3-PIN-19 (wake-reader run_id scoping, AM-P2-8), P3-PIN-20 (defer-counter home + ceiling eligibility, AM-P2-5).

---

## [PLANNER r2] FOLD ROUND 2 — test-map reconciliation + W2 + the r2 riders — 2026-11-02

**Fold mode:** additive (Mode 2). Every change is marked `[PLANNER r2]`; **no existing row, pin id, task id, W-id or acceptance id is renumbered or deleted**. New ids are appended: the map rows **`P3-PIN-18` … `P3-PIN-26`** (the three the architect had already *named* in `AM-P3-2` — `P3-PIN-18`/`19`/`20` — now have **rows**, and the council-mandated **three-axis `intent_flip` pin** takes the number **`P3-PIN-21`**).

### What this fold closes in Phase 3

| Finding | Where | What changed |
|---|---|---|
| **#8 — test-map reconciliation** | §4.2.2's map | **9 new rows** appended (`P3-PIN-18`..`P3-PIN-26`) covering Phase 2's `P-13`..`P-22`, the AM-P2-9 probe mutations, and the r2 family. **No existing row was renumbered.** |
| **#15 — W2 undefined** | `AM-P3-2` | **W2 is now defined** (the post-ceremony steady state) and its old-code-skew exposure is stated **VACUOUS** — launcher + daemon binary ship atomically (`stage.sh:395-433`). |
| **#16 — the kill-switch** | `AM-P3-2` (W1) | `SWEEP_SERVING_GREEN_ENABLED` (default `1`) is a **second enumerated hard ceremony pre-step** and is named in the runbook. |
| **#2 — the fixture premise** | §4.2.3's harness notes | The **measured** limit of `soak_gate_fake_daemon.py` (it answers `/livez` **200 in every mode** ⇒ `hard-red` is launcher-GREEN against a livez-only probe) is recorded, with the new helper's modes enumerated. |
| **#12 — T15's scope** | §4.2.3's `tests/test_launcher.sh` source-pin note | T15's **narrow helper** is a new function ⇒ outside the 8b-r1 extraction scope, but `_journal_sweep`'s body gains one call ⇒ the pin must still be re-verified, **and the helper's own body must be added to the pin's scope**. |
| **#19 — the daemon-closer vocabulary** | `P3-PIN-11` | Split into **11a** (the daemon's **verdict** — the janitor never raises; it journals and no-actions) and **11b** (the **launcher's execution** of the daemon's request). See below. |
| **#22 — the wake-set growth** | `P3-PIN-19` | **Reframed as a PRE-EXISTING-DEFECT CURE** — the reader's run_id-blindness is pre-existing (today's `boot_sweep_commit_and_continue` already exposes it); the new event kind only makes the exposure reachable. **The pin is kept.** |

### The `P3-PIN-11` split (advisory #19) — the daemon's verdict vs the launcher's execution

The daemon-side closer is a **janitor**: it **never raises** (D.1's error contract — a failed tick logs and the next tick retries). ⇒ it can **never** "halt for human" — D.1's GREEN-action step 2 (*"Absent/`null` `displaced_version` ⇒ halt-for-human"*) is **reworded** (Phase 2, r2): the daemon **journals an advisory row and takes no action this tick**; the launcher's `halt-for-human` (mirror `launcher.sh:1094-1106`) remains **on the launcher path only**.

⇒ the map row splits:

| Row | Scope | What it proves | Mutation-verification expectation |
|---|---|---|---|
| **P3-PIN-11a** **[PLANNER r2]** | the **daemon's verdict** — `tests/unit/tools/test_upgrade_journal_sweep_liveness.py` + the NEW daemon-side pin file | flipped + stale + owner-dead ⇒ the daemon reads its own health (version ∧ `composite.ready`) ⇒ GREEN ⇒ **commit-close** (`serving_green_commit` + `quarantine_cleared_serving_green`) · NOT-GREEN ⇒ the **verdict + request only** — **no** symlink repoint, **no** quarantine, **no** counter, **no** cooldown · stale/owner-alive ⇒ **no action** · the closer **never raises** and never claims a halt-for-human **[PLANNER r3 → AM-P2-24]** — the NOT-GREEN arm gains the **grace conjunct**: it fires only when the composite's **`grace_active == False`**; a **degraded-but-grace-held** composite ⇒ **DEFER** (no verdict, no request) ⇒ **drive `P3-PIN-27` for the defer leg** and **this** row (on a **not-grace-held** fixture) for the never-green leg. **[PLANNER r5 → BLOCKER 1 — THE `intent_flip` AUTHORIZATION CONJUNCT (P9) IS NOW EXPLICIT IN THIS ROW]** the daemon's predicate set is **P1-P9** (Phase 2 §D.1's table, as amended by this fold) and **P9 = the `intent_flip` authorization conjunct**: the journal history must carry an `intent_flip` event naming the target. ⇒ **the commit-close arm requires GREEN ∧ P9**, and **the verdict + request arm requires NOT-GREEN ∧ P9 ∧ ¬grace-held**. **Absent P9 ⇒ the daemon takes NO action this tick** — no `serving_green_commit`, no `quarantine_cleared_serving_green`, no `serving_unhealthy`, no `rollback_requested`; the txn stays **open** (AM-P2-4's canonical clause (ii): *"daemon no-action-this-tick"*). ⇒ this row must additionally assert the **negative leg**: **GREEN ∧ NO `intent_flip` ⇒ no action at all**. **Mutation-verification expectation:** drop P9 from the daemon predicate set ⇒ this pin goes **RED**. | Neuter the daemon commit-close ⇒ **RED**; make the daemon roll back by itself ⇒ **RED**; drop the `intent_flip` conjunct ⇒ **RED** |
| **P3-PIN-11b** **[PLANNER r2]** | the **launcher's execution** of the daemon's request (the T14 seam) | the launcher **re-probes** on a request and executes the **existing** chain byte-identically on a NOT-GREEN re-probe; GREEN ⇒ the request is **superseded** (commit-close); the request's **request-stage** alert kind is `upgrade_serving_unhealthy_requested`, **not** `upgrade_auto_rollback` (Phase 2 `AM-P2-19`) | Execute a superseded request ⇒ **RED**; page `upgrade_auto_rollback` on a request ⇒ **RED** |

### The r2 map rows, at a glance

| Row | Covers | Tier |
|---|---|---|
| `P3-PIN-18` | the version-normalization leg **per comparison site** (launcher probe / daemon closer / adopt path) | T1 |
| `P3-PIN-19` | the promote-wake reader's run_id scoping — **reframed** as a pre-existing-defect cure | T1 |
| `P3-PIN-20` | the counter's home (launcher-local file, not the journal) **+ the bounded absent-ceiling's eligibility** | T1 |
| **`P3-PIN-21`** | **the three-axis `intent_flip` pin** (the council-mandated number) **+ the r2 negative axis** (forged request + green + no `intent_flip` ⇒ defer) | T1 |
| `P3-PIN-22` | the T14 handoff family `P-13`..`P-18` **+ the cross-entry negative + the R3 hinge** | T1 |
| `P3-PIN-23` | the crash-prefix fault injection (`P-19`) | T1 |
| `P3-PIN-24` | the vocabulary-growth test updates (`P-22`) | T1 |
| `P3-PIN-25` | the AM-P2-9 probe mutations (truncated body / in-loop deadline / the `/readyz` second GET / wrong-version fail-fast) | T1 |
| `P3-PIN-26` | the r2 family — absent-ceiling, sticky green, version-mismatch split, counter lifecycle, kill-switch, T15 scope + idempotence, owner-liveness on the whole stale path | T1 |
| `P3-PIN-27` **[PLANNER r3]** | **the F-1 grace leg** — the daemon-side closer's NOT-GREEN verdict requires `grace_active == False` ⇒ **degraded-but-grace-held ⇒ DEFER** (no verdict, no request, txn open); the mirror leg (genuinely unhealthy outside grace) is `P3-PIN-11a` | T1 |

### The reconciled absent-only disposition (this phase's share)

Phase 3's rows that assert *"the never-green protection is UNTOUCHED"* (`P3-PIN-06`, `P3-PIN-12`, and the `never-green pack family`) are **supplemented**, not replaced, with the **mechanism** that arms it — because the council's finding was that the claim was true in intent but **under-specified** (the mechanism did not arm it):

> **never-green is armed by two paths** — a **BROKEN** observation (`/livez` answered non-200) ⇒ the existing chain fires **immediately**; an **AMBIGUOUS** observation (no answer / wrong version / `/readyz` not ready) ⇒ the existing chain fires after the **bounded absent-ceiling** (≥3 qualifying observations ∧ owner-dead ∧ `intent_flip` ∧ never-green-observed). The **single normative statement** is Phase 2 §AM-P2-10; the reconciled locations are listed in `decisions.md` §[PLANNER r2] and `architecture-recommendation.md` §[PLANNER r2].

⇒ the never-green **pack family** (`promote_soak_never_green_test.sh` + the fail-fast + grace family) remains **unchanged and must stay green** — it is the consumer-side proof that the *existing* chain is byte-identical when it does fire. The **new** ceiling path is a **different trigger** on the same chain, pinned by `P3-PIN-20` + `P3-PIN-26`.

---

## [PLANNER r3] FOLD ROUND 3 — F-1 (the surviving boundary defect) + R-1 + R-6 — 2026-11-02

**Fold mode:** additive (Mode 2). Every change is marked `[PLANNER r3]`; **no existing row, pin id, task id, W-id or acceptance id is renumbered or deleted**. New ids are appended: the map row **`P3-PIN-27`** and the task row **`P3-T13`**.

**Verdict context.** The plan council's delta pass returned **APPROVE-WITH-ADVISORIES**, *conditional* on folding **F-1** (one surviving 🔴 critical) plus **REV-R-1**..**REV-R-4**; **REV-R-5**/**REV-R-6** are trivial; one carried doc-hygiene item rides along. The **leader confirmed the reviewer's F-1 remediation adjudication** — adopt **both (a)** and **(b)** plus the pin. Phase 3's share is the **test-map side** of that remediation plus the **REV-R-1** supersede annotations.

**Finding-id namespace (r3).** `REV-R-1` … `REV-R-6` are the **council's delta-pass finding ids, quoted from the review report** — they are **NOT** this plan set's risk ids (this plan set's risk series is the bare `R-n`). The namespace is introduced by this fold precisely so the two cannot collide (`phase2-plan.md` already carries risks `R-1`…`R-17`).

### The r3 fold map (this phase's share)

| Finding | Where | What changed |
|---|---|---|
| **F-1** — the new both-direction composition member | **`P3-PIN-27`** (new map row) + **`P3-T13`** (new task row) + the glance table | the **degraded-but-grace-held ⇒ DEFER** leg is pinned on the daemon side: a composite with `ready == False` ∧ `grace_active == True` ⇒ **no** `serving_unhealthy` row, **no** `rollback_requested` row, the txn stays open, no quarantine / counter / cooldown. The mirror leg (`P3-PIN-11a`) must use a **not-grace-held** fixture, so the two legs cannot be satisfied by one stub |
| **F-1** — the grace conjunct on the daemon's verdict | **`P3-PIN-11a`** | an r3 annotation adds the `grace_active == False` conjunct and points at `P3-PIN-27`; the row's own text is unchanged |
| **REV-R-1** — stale direction-② rows | **`P3-PIN-06`**, **`P3-PIN-08`**, **`P3-PIN-11`** | **append-only SUPERSEDE annotations** (the rows are **kept, not deleted**): only the **BROKEN** class fires immediately; `hard-red` and no-listener are **AMBIGUOUS** ⇒ deferred + counted ⇒ **drive `P3-PIN-20` / `P3-PIN-26`** (the ceiling) and `P3-PIN-25`'s `livez-non200` mode for the BROKEN leg. `P3-PIN-11`'s unhealthy arm is superseded by **`P3-PIN-11a` / `P3-PIN-11b`**, and with the r3 fold it is further qualified by the grace conjunct (**`P3-PIN-27`**). Rationale recorded on each row: a tester building from the un-annotated row would hard-redden against the **correct** r2/r3 implementation |
| **REV-R-6** — the seeded-request pin's label | this fold map | **recorded, not folded** (approver note): the **seeded-request** pin lives at **`P-13` / `P3-PIN-22`**; **`P3-PIN-25` is the wrong-version listener fixture pin** (the AM-P2-9 probe mutations — truncated body / in-loop deadline / the `/readyz` second GET / wrong-version fail-fast). The r2 fold's "P3-PIN-25" label is **incorrect** and must not be used to locate the seeded-request pin |

### What this phase's fold does NOT change

- The **never-green protection is untouched** — `P3-PIN-12`'s regression over the never-green pack family stays **green and unchanged**; the F-1 leg is a **new** pin, not a modification of an existing one.
- The **map row count** grows by one (`P3-PIN-27`); **no existing row is renumbered or deleted**.
- The **W1/W2/W3 matrix** is unchanged — the ceremony pre-steps gain nothing from this fold (the kill-switch pre-step already exists from the r2 fold).

### New ids appended by this fold (cross-reference)

| Kind | New ids |
|---|---|
| **Phase 3** | `P3-PIN-27` (map row) · `P3-T13` (task row) |


---

## [PLANNER r4] FOLD ROUND 4 — the phase-3 shares of the fresh approver's iteration-001 fold — 2026-11-02

**Fold mode:** additive (Mode 2). Every change is marked `[PLANNER r4]`; **no existing pin, task, decision, risk, OQ, AC or SC number is renumbered or deleted**. New ids are appended: **`P3-PIN-28`**, **`P3-PIN-29`**, **`P3-PIN-30`** (their phase-2 owners are `P-36`/`P-37`/`P-38`).

**Verdict context.** The fresh approver **REJECTED iteration 001** with 4 blocking issues; both verifiers concurred that **all four are doc-level folds over content that already exists correctly elsewhere in the plan set** — no new design work is implied. The leader accepted all four. **Two of the 12 carried notes land in this phase**, plus **one consistency fold** for the register-side deferral claim this phase's map repeats.

### The r4 fold map (this phase's share)

| # | What folded | Where (this file) | What changed |
|---|---|---|---|
| **note (f)** | the rider's evidence claim *"`:474` is the ONLY `date -j` site in `launcher.sh` … the only other `date -j` matches are the `:238` comment and the `:384` comment"* | **§4.1.1** (the bullet) **and §12** (the citation-appendix row) | **CORRECTED** (append-only; the superseded text is retained in place): (i) there is a **second executable `date -j` site** — **`launcher.sh:251`**, `date -ju -f …` inside `_js_iso_to_epoch`'s **Darwin/BSD arm** (`:249-258`, fail-closed on an unrecognized platform); (ii) **there is NO `:384` comment** — `:384` is `_js_journal_update`'s **docstring** and contains no `date -j`; the only comment match is `:238`. **The design and the static pin are UNAFFECTED** — `P3-PIN-03` asserts *"zero **bare** `date -jur` sites **outside** the BSD arm"*, and `:251` is neither bare nor outside an arm ⇒ the pin's wording and mutation expectation **stand unchanged**. Only the evidence sentence was wrong |
| **note (g)** | the stale "~59 min fuse" wording | **§4.3** (the deployment consequence) **and §9** (risk `P3-R1`) | **SUPERSEDED** (append-only): the 3540 s figure was **tonight's unconditional auto-rollback age, not a designed fuse** — verified, there is **no** 3540 s constant in `scripts/upgrade/` or `launcher.sh` (the launcher's gate is `SWEEP_STALE_S=600` at `:69` plus a computed age at `:907` ⇒ *any* launcher boot past the 600 s gate was sweep-eligible). The **operative bound** is the **≈10–15 min bounded absent-ceiling** (AM-P2-10), and the **launcher/daemon precedence** is stated (the launcher sweeps pre-daemon; the daemon closer normally wins at the first tick after the 600 s gate ≈11 min; the launcher's bounded ceiling is the backstop for the shapes the daemon cannot reach) |
| **blocker-4 consistency** | this phase's `P3-PIN-10` map row repeated the stale deferral claim | **§4.2.2, row `P3-PIN-10`** | **SUPERSEDED** annotation (the row is KEPT, not deleted): the counter's home is the **launcher-local `.sweep-defer-<run_id>` file** (AM-P2-5 — not a journal-history count) and the firing condition is the **ceiling predicate** (≥`SWEEP_DEFER_CEILING` **qualifying** observations ∧ owner-dead ∧ `intent_flip` ∧ **never-green-observed**, AM-P2-10.2), not "after N deferrals"; a GREEN observation makes the ceiling **permanently ineligible** (sticky-green, AM-P2-10.3) ⇒ **drive `P3-PIN-20` / `P3-PIN-26`** |
| **notes (d), (e)** | the r3 defer leg's stub was not honestly labelled; the daemon kill-switch was unpinned | **§4.2.2 — new rows `P3-PIN-29`** and **`P3-PIN-30`** | **`P3-PIN-29`** = the **REAL** `GraceTracker`/composite-interleaving leg (the companion to `P3-PIN-27`, which drives a **synthetic** composite — `P3-PIN-27` now says so). **`P3-PIN-30`** = the **two-state** daemon kill-switch pin (`ENSEMBLE_UPGRADE_SERVING_GREEN_CLOSER=0` ⇒ the lane writes no verdict / no request and leaves the txn untouched; `=1` ⇒ normal; read **per tick**, not cached) |

### In-tree re-verification performed at this fold (base `941cb6321`)

| Claim | Verdict | Evidence |
|---|---|---|
| the executable `date -j` sites in `launcher.sh` | **CORRECTED** | `:251` (`date -ju -f '%Y-%m-%dT%H:%M:%SZ' "$ts" +%s`, inside the BSD arm of `_js_iso_to_epoch`) and `:474` (`date -jur`, the defect). `:238` is a comment; `:384` is `_js_journal_update`'s docstring and contains **no** `date -j` |
| no 3540 s fuse constant exists | **CONFIRMED** | `grep -rn 3540 scripts/upgrade/ launcher.sh` ⇒ **0 hits**; the launcher's gate is `SWEEP_STALE_S=600` (`launcher.sh:69`) with the age computed at `:907` |
| the launcher's sweep is pre-daemon | **CONFIRMED** | `launcher.sh:1661-1662` (sweep) vs `:1525` (the daemon child starts) |

### What this fold does NOT change

- The **rider's design** is unchanged (epoch-argument two-arm `uname` dispatch; the `*)` arm's (iii)-scoped fail-closed marker; the property pins + the cross-component round-trip anchor).
- The **static pin `P3-PIN-03`** is unchanged in wording and mutation expectation.
- The **tier mandate** (Tier 2 lane-level, rider pins Tier 1) is unchanged.
- **No id was renumbered or deleted** in this phase.

---

## [PLANNER r5] FOLD ROUND 5 — the fresh approver's iteration-002 REJECT (6 mechanical blockers, narrowed) + 7 same-pass notes + the systematic claim-class sweep — 2026-11-02

**Fold mode:** additive (Mode 2). Every change below is marked `[PLANNER r5]`; **no existing pin, task, OQ, risk, scenario or section number is renumbered or deleted**. **No new id is required** — the r5 folds land on the **existing** pin rows (`P3-PIN-05`, `-11`, `-11a`, `-12`) and the register's consolidated table.

### The r5 fold map (this file's share)

| # | What folded | Where (this file) | What changed |
|---|---|---|---|
| **BLOCKER 1** | the daemon-side **authorization conjunct** was invisible at the pin level | **§4.2.2 rows `P3-PIN-05`**, **`P3-PIN-11`**, **`P3-PIN-11a`** | each row now states the **`intent_flip` precondition explicitly** and gains a **negative leg** — **GREEN ∧ NO `intent_flip` ⇒ no commit-close (launcher: DEFER) / no action (daemon)** — plus a **mutation expectation** (drop the conjunct ⇒ **RED**), which the pre-r5 rows could not express because they named no conjunct at all. `P3-PIN-11`/`-11a` additionally record the **compound gate** on the never-green leg (**P9 ∧ ¬grace-held**) — a not-grace-held fixture **without** `intent_flip` is the **no-action** fixture, not the never-green fixture. `P3-PIN-05` now names `P3-PIN-21` (the three-axis `intent_flip` pin) as the consolidated carrier |
| **BLOCKER 5** | the **unscoped adopt-parity** claim | **§4.2.2 row `P3-PIN-12`** | the shared-polarity assertion is **scoped** to the fixtures where all three decision-makers agree (**GREEN-and-authorized**, **NOT-GREEN**); the **GREEN-without-`intent_flip`** fixture is pinned **per decision-maker** (launcher DEFER / daemon no-action / adopt existing-chain — Phase 2 §D.4 (a-i)). The other three assertions in the row (never-green untouched; launcher purity; lock + re-read) are **UNCHANGED** |
| **note (h)** | the **"recommended" ⇒ REQUIRED** conversion was not carried into this file's map rows | — | **no fold needed in this file** — the alert-map requirement is carried by `phase2-plan.md` **AM-P2-8** / **AM-P2-19** and by `decisions.md` **D.7** (both folded). This file's rows reference the vocabulary through `P3-PIN-24` (the vocabulary-growth test updates), which is **unaffected** |

### The r5 sweep — this file's hits, classified

| Claim-class | Hit sites in this file | Disposition |
|---|---|---|
| **(a)** predicate/closer restatements missing the P9/`intent_flip` conjunct | **`P3-PIN-05`** · **`P3-PIN-11`** · **`P3-PIN-11a`** | **FOLDED** (all three) |
| **(a)** — already correct | **`P3-PIN-21`** (the three-axis `intent_flip` pin — (a)/(b)/(c) plus the r2 forged-request negative) · **`P3-PIN-11b`** (the launcher's execution arm, whose GREEN arm is `intent_flip`-gated by AM-P2-15) · **§4.3/§9** (the rider's rows carry the daemon closer as the pre-promote mitigation) | **ALREADY CORRECT** — the three-axis pin already encodes both directions of the authorization hinge; the split rows already route the unhealthy arm to `P3-PIN-11a`/`P3-PIN-27` |
| **(b)** close-before-history order claims | **`P3-PIN-23`** | **ALREADY CORRECT** — it names **AM-P2-1's history-before-close order** and its mutation expectation is *"reorder the sequence (close before history) ⇒ RED"* — i.e. it already pins the corrected order |
| **(c)** pre-r2 firing-rule text | **`P3-PIN-06`**, **`P3-PIN-08`**, **`P3-PIN-10`** | **ALREADY CORRECT** — all three carry the r3/r4 SUPERSEDE annotations (the four-way observation taxonomy; the counter's re-homed home + the ceiling predicate) |
| **(d)** history-count counter claims | **`P3-PIN-10`**, **`P3-PIN-20`**, **`P3-PIN-26`** | **ALREADY CORRECT** — the counter's home is stated as the launcher-local file and the firing condition as the ceiling predicate, in all three rows |
| **(e)** launcher-primary precedence | — | **NO HIT** in this file |
| **(f)** superseded timing | **§4.3** (the deployment consequence) · **§9** (risk `P3-R1`) | **ALREADY CORRECT** — the r4 note (g) marks the "~59 min fuse" wording **SUPERSEDED** at both sites |
| **(g)** unscoped adopt-parity | **`P3-PIN-12`** | **FOLDED** (blocker 5) |
| **(h)** "recommended" alert entries | — | **NO HIT** in this file (the alert-map requirement lives in Phase 2 / the register) |

### What this fold does NOT change

- The **consolidated pin map's structure and row set** are unchanged — the r5 fold **adds no rows** to §4.2.2; it annotates three existing rows and scopes a fourth.
- The **never-green protection** is untouched; the **lane Tier-2 mandate** is untouched; the **rider's design** is untouched.
- The **three-axis `intent_flip` pin** (`P3-PIN-21`) is unchanged — the r5 folds make the daemon leg **visible in the rows a tester reads first** (`P3-PIN-11`/`-11a`), not a new pin.
- **No id was renumbered or deleted** in this file.
