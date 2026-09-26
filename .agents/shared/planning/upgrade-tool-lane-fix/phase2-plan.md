# Phase 2: Stage.sh ROLLBACK_SAFE Default Rider + ADR-035 Documentation

Date: 2026-09-26
Author: planner[v2] via plan-creation worker (P2 — shell rider + ADR documentation)
Mission: v0.15.3 upgrade-tool-lane-fix · Base: latest @ 139ba352 (v0.15.2) · Branch: `feature/upgrade-tool-lane-fix`
Companion: P1 (Python subsystem — manager.py executor spawn + argv/env passthrough + pending_actions GC + reconcile sweep — owned by another worker)

## Objective

Replace the silent-false `rollback_safe` derivation in `stage.sh` with a guarded default that refuses on destructive-DDL+unset (eliminating the v0.14.2 + v0.15.1 accident class) and documents both the rider design and the user-ratified tool-lane live-promote attestation supersession in `ADR-035` of `.agents/shared/planning/self-restart-upgrade-phase2/decisions.md`. After this phase: no silent-false path can ship, every real release's `rollback_safe` is either explicitly affirmed or quietly defaulted to true (no signal of danger), and the supersession policy is auditable in the ADR log with the threat-model revisit anchor named.

## Scope

### In Scope
- **ITEM 6 — `stage.sh:163-180` ROLLBACK_SAFE default rider** (one branch + warning text + lib.sh helper usage; manifest write site `:277-293` unchanged; NO consumer changes — promote.sh:330, rollback.sh:98-101, lib.sh:1446-1449, launcher.sh:783-797, upgrade_tools.py:2286-2293 + :1345-1370, status.sh:88-90 all stay as-is).
- **ITEM 3-DOC — ADR-035 minting** in `.agents/shared/planning/self-restart-upgrade-phase2/decisions.md` (insert after ADR-032 block `:213`, before Pre-Freeze Assumption-Closure Checklist `:215`; template = ADR-017 `:27-36`; minting rule `:226` — current max = ADR-034, mint sequentially).
- **Test additions** to `tests/test_release_journal.sh` (the existing wrapper around the journal/unit suite — pack `release_journal_unit_test` in `test/packs/release_journal_unit_test.sh:53`) that pin the new contract and prove the silent-false path is DEAD.
- **Residual ops gates documentation** — the three runbook §8.4 / §9 obligations stay OUT of code scope (plan.md out-of-code-scope section feeds W3's plan-overview integration).

### Out of Scope (explicit fences)
- **P2.1 GNU-debt sites** (lib.sh:84-89 `_iso_to_epoch`, lib.sh:706-712 cooldown arm, lib.sh:1238-1295 retention eviction) — uname-dispatch family is the fix; backlog item, NOT this mission. Plan calls them out so the implementer doesn't opportunistically touch them.
- **Python-side consumer changes** — `daemon/tools/upgrade_tools.py:2286-2293` + `_target_release_state:1345-1370` keep reading the manifest's `rollback_safe` field as-is; the rider changes the **derivation** (how the field gets written), not the **gates** (how it's read). No tool-layer or manager-side work.
- **F2 loopback-auth closure** — explicitly OUT (runbook §9:495-501 names it as the residual the PRE-LIVE gate waits on). ADR-035's consequence section names it as the **revisit anchor**, not the fix.
- **`ENSEMBLE_ROLLBACK_SAFE` enum extension** — today's `1|true|0|false|""|unset` set is preserved; no new tokens (e.g. `delta-scoped`) in this phase.
- **Manifest schema changes** — field name + JSON shape + consumer field-extraction (`manifest_field` lib.sh helper) all unchanged. Only the value derivation logic moves.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | **stage.sh rider — replace `:172-180` derivation branch** with the guarded default: explicit `1\|true`/`0\|false` honored verbatim; unset + `CONTAINS_CONTRACT_PHASE=true` → REFUSE exit 78 with actionable WARNING; unset + `CONTAINS_CONTRACT_PHASE=false` → default `true` quietly. | none | stage.sh exit codes: explicit `1`/`true` → 0 + manifest `rollback_safe:true`; explicit `0`/`false` → 0 + manifest `rollback_safe:false`; unset + DROP-detected → exit 78 with WARNING text containing both override forms + override legitimacy test guidance; unset + no DROP → 0 + manifest `rollback_safe:true` |
| 2 | **stage.sh — add `_log` of default derivation** (the "quietly defaulted to true" case) so the stage log line records the choice (parity with existing :381 log of `rollback_safe=… known_schema_gen=… contains_contract_phase=…`). | T1 | The final stage log line is unchanged in shape but now contains "defaulted=true" or "explicit=true"/"explicit=false" tokens. |
| 3 | **ADR-035 — insert in `.agents/shared/planning/self-restart-upgrade-phase2/decisions.md`** as `## ADR-035 (minted 2026-09-26, P2.5 tool-lane-fix): ...` section, placed AFTER the ADR-032 block (`:213`) and BEFORE the Pre-Freeze Assumption-Closure Checklist (`:215`). Template = ADR-017 (`:27-36`). Minting rule `:226` honored (max = ADR-034 → mint 035). | none | New ADR section exists at the right insertion point; contains BOTH halves (verifier-verdict evidence + user-ratified supersession); cites ADR-017 as the revisit anchor; updates the Decision Index table at `:288-308` (or is added at the next available row in that table). |
| 4 | **`tests/test_release_journal.sh` — add 6 new test cases** covering the rider: (a) explicit `1` honored; (b) explicit `true` honored; (c) explicit `0` honored; (d) explicit `false` honored; (e) unset + DROP-detected → exit 78 + stderr contains both override forms + "migration delta" guidance; (f) unset + no DROP → quiet default `true` + manifest `rollback_safe:true`. Uses the existing throwaway git-tagged fixture pattern (test_release_journal.sh:24-29) + the existing `run_stage` wrapper around `stage.sh --skip-build`. | T1 | All 6 new test cases pass; existing tests for `manifest has rollback_safe` at `:297` remain green. |
| 5 | **`tests/test_release_journal.sh` — add explicit "silent-false path is dead" assertion** (test 9g): the `FAKE_REPO_DROP` fixture (`:295-312` in §9) carries destructive DDL (`CREATE TABLE x; DROP TABLE x;`). `stage.sh` with unset `ENSEMBLE_ROLLBACK_SAFE` against THIS fixture MUST refuse exit 78 + write NO manifest; if it returns 0 with `rollback_safe:false` in the manifest, the test fails. **Fixture-only — staging the production migration history would pollute the test environment; the implementer must use the throwaway fixture exclusively** (same git-init/tag pattern as `FAKE_REPO` at `test_release_journal.sh:78-101`, with destructive DDL injected into a fresh `daemon/migrations/versions/` dir). This is the regression pin for the v0.14.2 + v0.15.1 accidents. | T1, T4 | New test case 9g named "silent-false path is DEAD (v0.14.2 + v0.15.1 accident class)"; passes against `FAKE_REPO_DROP`. |
| 6 | **Residual ops gates documentation** — append the "RESIDUAL OPS GATES" section to `phase2-plan.md` (this file) so W3's `plan-overview.md` can absorb it as an out-of-code-scope bullet. References: runbook §8.4(i) `:482`, §8.4(ii) `:484` (1)-(3), §8.4(iii) `:486`, §9:495-501 + ledger_check.py:277-296 gate semantics. | none | This file's "Residual Ops Gates (out-of-code-scope)" section exists with all citations and is ready to be quoted into `plan-overview.md`. |
| 7 | **GNU-debt fence** — add an explicit "GNU-debt sites OUT OF SCOPE" comment block at the top of `stage.sh` rider code (above the new derivation branch) naming the three fence sites (lib.sh:84-89, lib.sh:706-712, lib.sh:1238-1295) so a future implementer does not opportunistically touch them. | T1 | The comment block exists; rider does NOT introduce any uname-dispatch or GNU-vs-BSD branching. |
| 8 | **BSD portability review** — rider implementation uses lib.sh helpers (`_warn`, `_log`); no new uname-dispatch; no `local a="…" b="$a/…"` chained declarations (lib.sh:36-44 header fence + `:1148-1149` / `:1251-1252` patterns to copy); no `mv -hf` references (the rider is read-only against the file system after manifest write); the existing `mv -f` at stage.sh:313/318/322 stays inside its current scope (the rename-aside swap-in is not touched). | T1 | Manual review confirms: no new GNU-only constructs introduced; rider is fully portable under bash 3.2 / BSD date / BSD mv. |

## Coupling

- **Tight with: NONE in code.** Phase 2 is a self-contained script + ADR change. The rider modifies only `stage.sh:172-180` (derivation branch) and adds WARNING text. No consumer site is touched. No Python module is touched. No journal file format changes.
- **Loose with: P1 (Python subsystem).** P1 owns the in-daemon side (manager.py executor spawn + argv/env passthrough + pending_actions GC + reconcile sweep); P2 owns the staging side (manifest derivation) + the ADR documentation. They share no file, no contract, no API. The ADR-035 user-ratified supersession describes the **policy posture** for the tool-lane live-promote attestation that P1's argv/env passthrough enforces mechanically — but ADR-035 does NOT depend on P1's specific implementation choices (the policy supersession is what the user ratified 2026-09-26; the mechanic of "argv carries `--f2-verified-closed`" is P1's lane). The ADR text references the F2 fences (executor env allowlist + `--f2-verified-closed` + runbook §9) without prescribing their code form.
- **Independent of: every other phase in the initiative.** No migration, no DB schema, no consumer gate, no agent prompt, no tool registration.
- **Cross-phase risk:** ADR-035 cites the F2 forge lane as the revisit anchor. If a future commission closes F2 (loopback API authentication), ADR-035 should be revisited to reflect the new posture — captured in the ADR's consequence section.

## Stage.sh Rider Design

### Current Logic (verified — research-findings-shell-e2e.md §Q1 + stage.sh:163-180)

```bash
# stage.sh:163-171 (informational, unchanged in this phase)
KNOWN_SCHEMA_GEN="$(ls "$REPO_ROOT/daemon/migrations/versions" 2>/dev/null | sort | tail -1)"
[ -n "$KNOWN_SCHEMA_GEN" ] || KNOWN_SCHEMA_GEN="unknown"
if grep -liE 'DROP[[:space:]]+TABLE|DROP[[:space:]]+COLUMN' \
     "$REPO_ROOT"/daemon/migrations/versions/*.sql >/dev/null 2>&1; then
    CONTAINS_CONTRACT_PHASE=true
else
    CONTAINS_CONTRACT_PHASE=false
fi
# stage.sh:172-180 (REPLACED by this rider)
ROLLBACK_SAFE="${ENSEMBLE_ROLLBACK_SAFE:-}"
case "$ROLLBACK_SAFE" in
    1|true)  ROLLBACK_SAFE=true ;;
    0|false) ROLLBACK_SAFE=false ;;
    "")
        # derived default (D-FA4.5): destructive migrations ⇒ unsafe to roll back
        if [ "$CONTAINS_CONTRACT_PHASE" = "true" ]; then ROLLBACK_SAFE=false; else ROLLBACK_SAFE=true; fi
        ;;
esac
```

**Manifest write** (stage.sh:277-293, unchanged):
```bash
cat > "$STAGE_TMP/manifest.json" <<EOF
{
  "version": "$VERSION",
  ...
  "rollback_safe": $ROLLBACK_SAFE,        # :284 — receives the new value
  ...
}
EOF
```

**All consumer gates** (unchanged, listed for completeness only):
| Site | Semantics |
|---|---|
| `promote.sh:330` `PREV_SAFE="$(manifest_field "$PREV" rollback_safe …)"` | Gates the ROLLBACK target only — refuses auto-rollback to a `previous` whose manifest is `false`. |
| `rollback.sh:98-101` | Gates the rollback TARGET's manifest. |
| `lib.sh:1446-1449` (adopt_stale_txn sweep) | Same D-FA4.5 guard on the sweep-rollback target. |
| `launcher.sh:783-797` (journal sweep) | Same guard, fourth identical gate (ADR-033 posture). |
| `daemon/tools/upgrade_tools.py:2286-2293` + `_target_release_state:1345-1370` | Tool-layer entry-time check on the target manifest; emits `manifest-unsafe` refusal. |
| `status.sh:88-90` | Read-only listing. |

**Why this matters:** the derivation (stage.sh:172-180) is the only place `rollback_safe` is **written**; every gate reads it. A silent-false derivation closes the auto-rollback net for the staged release AND refuses manual rollback to it — bit us twice: v0.14.2 (2026-09-25, accidental override omission, zero schema drift vs v0.14.1) and v0.15.1 (2026-09-26, override not set, blocked the v0.15.0 promote's auto-rollback lane). The full-history grep (`grep daemon/migrations/versions/*.sql`) is **not delta-scoped** between the previous release tag and `$VERSION` — it fires on destructive DDL applied before the previous tag too, where it has no bearing on THIS release's rollback semantics. Production migration sets accumulate destructive DDL over years of schema evolution, so the heuristic fires on every real release by default — the rider's task is to demote it from silent side-effect to loud WARNING.

### New Contract (D-FA4.5 v0.15.3 supersession)

| `ENSEMBLE_ROLLBACK_SAFE` | `CONTAINS_CONTRACT_PHASE` | `rollback_safe` written | Stage outcome |
|---|---|---|---|
| `1` or `true` | (either) | `true` | Stage proceeds. Operator attests the migration delta between previous release tag and `$VERSION` is EMPTY (override legitimacy test). |
| `0` or `false` | (either) | `false` | Stage proceeds. Operator attests the delta is real and `$VERSION` genuinely cannot roll back. |
| unset (or empty) | `true` (DROPs detected) | — | **REFUSE — exit 78.** WARNING text names both override forms + the override legitimacy test. The silent-false path is DEAD. |
| unset (or empty) | `false` (no DROPs) | `true` | Stage proceeds quietly. No signal of danger; default = allow rollback per ADR-005. |

**Rationale (chosen shape: refuse + log + actionable warning, NOT silent default-true):**

1. **REQUIRE explicit choice on destructive-DDL+unset.** ADR-005's posture is "allow rollback by default" — but the FULL-HISTORY heuristic is a noisy signal (matches DDL applied before the previous tag too). When the heuristic flags danger AND the operator has not affirmed safety, **refusing with actionable guidance** is fail-CLOSED in the strongest sense: no possibly-unsafe manifest is written, the operator must affirm the safety case before staging proceeds. This kills the v0.14.2 + v0.15.1 accident class (the accidents were "operator forgot the override"; the refusal forces the operator to either affirm `1` or assert `0`).
2. **DEFAULT true on no-signal.** When the heuristic finds nothing, defaulting to `true` quietly matches ADR-005's "auto-rollback net open by default" — every clean release gets rollback available without ceremony.
3. **DEMOTE heuristic to informational.** The full-history grep is a **loud WARNING trigger**, never a silent side-effect. The override legitimacy test (the research finding: "only set `ENSEMBLE_ROLLBACK_SAFE=1` when the migration delta between version tags is EMPTY") is encoded in the WARNING text so the operator makes an informed choice.
4. **NO new tokens.** Today's enum (`1|true|0|false|""|unset`) is preserved; no `delta-scoped` or other extension lands in this phase. Backwards-compatible with every existing invocation pattern (deploy.sh, dev.sh, the v0.15.2 staging calls).

**Alternative shapes considered + rejected:**

- **Default `true` quietly on both unset paths** (option C — strict) — every real release requires explicit `ENSEMBLE_ROLLBACK_SAFE=0` to refuse rollback. Safe, but friction: the operator must affirm "I have a real schema drift" on every destructive-DDL release. Today's accidents would have been CAUGHT (they'd default-true without ceremony, accidentally over-ride the operator's "this is unsafe" intent), but new accidents could appear (operator forgets the `0` when they meant `0`).
- **Default `false` quietly (status quo)** (option A) — the accident class repeats. Rejected.
- **Refuse any unset case** (option C strict) — every real release without override refuses. Breaks every CI pipeline that doesn't pass the override; safe but excessive.

The chosen shape (refuse on unset+DROP, default true on unset+no-DROP) preserves the operator's ability to stage quickly when there's no signal of danger, AND forces an explicit affirmation when the signal fires. This matches the **ENSEMBLE_UPGRADE_LIVE** pattern: explicit when there's a live-rung concern, default-quiet otherwise.

### Implementation (BSD-safe)

```bash
# stage.sh:172-180 REPLACED — D-FA4.5 v0.15.3 supersession.
# GNU-debt sites OUT OF SCOPE (fence): lib.sh:84-89 _iso_to_epoch (BSD-only),
# lib.sh:706-712 cooldown arm (BSD date -v order), lib.sh:1238-1295 retention
# eviction (BSD stat -f). Fix family = uname dispatch; backlog, NOT this mission.
ROLLBACK_SAFE_SRC="unset"
ROLLBACK_SAFE="${ENSEMBLE_ROLLBACK_SAFE:-}"
case "$ROLLBACK_SAFE" in
    1|true)  ROLLBACK_SAFE=true ; ROLLBACK_SAFE_SRC="explicit=true" ;;
    0|false) ROLLBACK_SAFE=false; ROLLBACK_SAFE_SRC="explicit=false" ;;
    "")
        # unset: the full-history destructive-DDL grep (stage.sh:163-171) is
        # INFORMATIONAL — it is not delta-scoped and fires on DDL applied
        # before the previous release tag too. When it matches AND the
        # operator has not affirmed safety, refuse (D-FA4.5 supersession;
        # the silent-false path bit v0.14.2 + v0.15.1). Override legitimacy:
        # only set ENSEMBLE_ROLLBACK_SAFE=1 when the migration delta between
        # the previous release tag and $VERSION is EMPTY (no schema drift
        # introduced by $VERSION itself).
        if [ "$CONTAINS_CONTRACT_PHASE" = "true" ]; then
            _warn "destructive DDL detected in daemon/migrations/versions (DROP TABLE|DROP COLUMN match) — refusing to derive rollback_safe silently (v0.15.3 D-FA4.5 supersession; the silent-false path bit v0.14.2 on 2026-09-25 and v0.15.1 on 2026-09-26). Re-run with an EXPLICIT choice:" \
                  "  ENSEMBLE_ROLLBACK_SAFE=1   ... ONLY if the migration delta between the previous release tag and $VERSION is EMPTY (no schema drift introduced by $VERSION — the destructive DDL was already applied before $VERSION)" \
                  "  ENSEMBLE_ROLLBACK_SAFE=0   ... otherwise (the delta is real and $VERSION genuinely cannot roll back)" \
                  "See ADR-035 in .agents/shared/planning/self-restart-upgrade-phase2/decisions.md and runbook docs/runbooks/upgrade-drills.md §2."
            exit 78
        else
            ROLLBACK_SAFE=true
            ROLLBACK_SAFE_SRC="default=true"
            _log "rollback_safe defaulted to true (no destructive DDL detected in migration history; no ENSEMBLE_ROLLBACK_SAFE override needed)"
        fi
        ;;
esac
```

**BSD-safe construction notes (cite-the-pattern):**

- **Lib.sh helpers `_warn` / `_log`** (lib.sh:71-73) — established house pattern for stderr/stdout messaging with the `LOG_TAG` prefix. No new `_warn3` or helper introduced; multi-line `_warn` uses bash continuation `\` (matching lib.sh:117-119 style for multi-line warnings).
- **No new uname-dispatch** — the rider is read-only against the file system after manifest write; no `mv`, no `stat`, no `date -v`. The existing `mv -f` at stage.sh:313/318/322 (the rename-aside swap-in) is OUT of scope and untouched.
- **No chained `local` declarations** — bash 3.2 (lib.sh:36-44 header fence; canonical pattern lib.sh:1148-1149 / :1251-1252) forbids `local a="…" b="$a/…"` in one statement. The rider declares `ROLLBACK_SAFE_SRC="unset"` in a separate statement from `ROLLBACK_SAFE="${ENSEMBLE_ROLLBACK_SAFE:-}"` — though they're not chained via `$ROLLBACK_SAFE_SRC` either, the discipline is preserved.
- **No `set -f` introduced** — lib.sh:39-44 warns against disabling pathname globbing (pathname globbing IS used in retention_evict release scan; the quoted-pattern form is the load-bearing fix). The rider uses only literal strings and already-quoted patterns; no glob metachar concerns.
- **`_now_iso` / `_now_epoch` not used here** — the rider is stage-time only; the GNU-debt sites (`_iso_to_epoch`, cooldown arm, retention eviction) are explicitly fenced OUT. No `date -ju` or `date -v` references in this rider.
- **No `flock(1)`** — lib.sh:37 explicit. The rider is purely shell logic; no new locking primitives.
- **JSON output unchanged** — the rider writes a literal JSON boolean into `manifest.json` at `:284`; the value is `true` or `false`, the source tag (`ROLLBACK_SAFE_SRC`) is captured for the stage log line only and never enters the manifest (manifest schema unchanged — see the "Out of Scope" fence on manifest schema changes above).
- **Stage log line at `:381`** — extended to `rollback_safe=$ROLLBACK_SAFE source=$ROLLBACK_SAFE_SRC known_schema_gen=$KNOWN_SCHEMA_GEN contains_contract_phase=$CONTAINS_CONTRACT_PHASE` so the operator can see why the field was set the way it was.

### WARNING Text Draft (final, ready to paste)

The WARNING text is the actionable refusal message in the unset+DROP case. It is emitted via `_warn` (lib.sh:73 — stderr + LOG_TAG prefix), multi-line via bash continuation `\`, and matches the established lib.sh warning style for multi-line messages. Final text:

```
upgrade-stage[<env>]: WARN: destructive DDL detected in daemon/migrations/versions (DROP TABLE|DROP COLUMN match) — refusing to derive rollback_safe silently (v0.15.3 D-FA4.5 supersession; the silent-false path bit v0.14.2 on 2026-09-25 and v0.15.1 on 2026-09-26). Re-run with an EXPLICIT choice:
upgrade-stage[<env>]: WARN:   ENSEMBLE_ROLLBACK_SAFE=1   ... ONLY if the migration delta between the previous release tag and <ver> is EMPTY (no schema drift introduced by <ver> — the destructive DDL was already applied before <ver>)
upgrade-stage[<env>]: WARN:   ENSEMBLE_ROLLBACK_SAFE=0   ... otherwise (the delta is real and <ver> genuinely cannot roll back)
upgrade-stage[<env>]: WARN: See ADR-035 in .agents/shared/planning/self-restart-upgrade-phase2/decisions.md and runbook docs/runbooks/upgrade-drills.md §2.
```

(The `<env>` is the resolved `UP_TARGET` — `demo`/`sandbox`/`live`; `<ver>` is `$VERSION` substituted by the shell at warning-emission time.)

### GNU-debt Exclusion Fence (explicit)

The implementer MUST NOT opportunistically fix any of the following in this mission. They are pre-existing BSD-portability debt in `scripts/upgrade/lib.sh`; the fix family is uname-dispatch (analogous to `atomic_flip` at lib.sh:1147-1171); they are backlog items for a separate commission:

| Site | Location | Defect |
|---|---|---|
| `_iso_to_epoch` | `lib.sh:84-89` (BSD `date -ju -f '%Y-%m-%dT%H:%M:%SZ' "$ts" +%s` at `:86`) | Always fails on GNU → adopt_stale_txn + launcher sweep fail closed; 24h rollback window never rolls over. |
| Cooldown arm | `lib.sh:706-712` (BSD `date -ju -v+${COOLDOWN_S}S -f …` at `:709`) | On GNU falls back to `cooldown_until=now` → anti-flap inert; post-rollback promotes refused until journal surgery. |
| Retention eviction | `lib.sh:1238-1295` (`retention_evict`, called from `promote.sh:286`) — BSD mtime fallback `stat -f '%m' "$d"` at `:1279` | Garbled + NO-OP on GNU (stat -f fragments leak into release-name var; nothing evicted; conservative over-retention). `RETENTION_KEEP=3` at `lib.sh:69`; protocol-artifact skip list at `:1263-1265`; epoch-normalized sort key `:1269-1281`. |

This fence is **doubly encoded** in the rider itself (Task 7's comment block at the top of the new derivation branch) so a future reviewer cannot misread the rider as a "BSD-portability fix pass".

## ADR-035 Draft Text (ready to paste into `.agents/shared/planning/self-restart-upgrade-phase2/decisions.md`)

**Insertion point:** after the ADR-032 block (ends at `:213`), before the Pre-Freeze Assumption-Closure Checklist (starts at `:215`). Template = ADR-017 (`:27-36`). Decision Index table (`:288-308`) gains a row for ADR-035 in the next available slot.

> **Insertion position (S5, reviewer-affirmed):** the ADR-035 section lands **between ADR-032 and the Pre-Freeze Assumption-Closure Checklist** (default accepted by reviewer; no relocation). The Pre-Freeze Checklist (`:215`) is the reviewer-edit-driven boundary that anchors the rest of the file's structure (Standing Rulings, P2.2 Fix Pass, P2.2 Tidy cycle-3, P2.3 Gate Rulings, Decision Index) — ADR-035 is the most recent ADR and lands just above that boundary so the reviewer-council-owned sections stay grouped. No other ADR section is repositioned.

```markdown
## ADR-035 (minted 2026-09-26, P2.5 tool-lane-fix): stage.sh `rollback_safe` default-rider + tool-lane live-promote attestation supersession

**Context.** Two related defects landed 2026-09-25/26:
1. **stage.sh silent-false derivation (D-FA4.5 v1).** `stage.sh:172-180` derives `rollback_safe=false` whenever `grep -liE 'DROP TABLE|DROP COLUMN'` matches anywhere in `daemon/migrations/versions/*.sql`. Production migration sets accumulate destructive DDL across their full history (the v0.14.2 + v0.15.1 accidents both shipped against sets containing destructive DDL applied years before the staging point), so the heuristic fires on every real release by default — EVERY real release silently derives `rollback_safe=false`, closing the auto-rollback net for the staged release and refusing `rollback.sh` to it. The v0.14.2 (2026-09-25, accidental override omission, zero schema drift vs v0.14.1) and v0.15.1 (2026-09-26, override not set, blocked the v0.15.0 promote's auto-rollback lane) staging accidents BOTH shipped this way; the v0.15.2 manifest comment "(v0.15.1 accident not repeated)" names the class. The full-history grep is **not delta-scoped** between the previous release tag and `$VERSION` — it fires on DDL applied upstream of the staged release too, where it has no bearing on THIS release's rollback semantics.
2. **Verifier-verdict evidence (job 81a8a206, 2026-09-26).** The 3-factor LIVE gate audit folded two verdicts: (Q1 NO) merge `4db90d74` does NOT close F2 — the unauthenticated loopback API user-origin forge lane remains open (`daemon/routers/jobs_crud.py:275-278` body-source verbatim pass-through; `daemon/routers/messages.py:391` stamps `source="api"`; corroborated in-tree by `daemon/tools/upgrade_journal.py:1060-1073` noting the forge lane is "the separately-fenced pre-existing exposure, not addressed here"); (Q2 YES) live is policy-gated user-executed-only — `docs/runbooks/upgrade-drills.md` §9:501: "the live rung remains USER-EXECUTED — automation stops at demo permanently (ADR-017…; promotion-ladder.md S4–S6 are USER rows)". The PRE-LIVE checklist is four-part (`docs/runbooks/upgrade-drills.md` §8.4(ii):484 + §9 ledger + F2 gate + user-executed).

**Options.** For the rider:
(a) Honor today's silent-false derivation (status quo — accidents repeat);
(b) Demote heuristic to loud WARNING + require explicit choice (operator override OR refuse with exit 78);
(c) Refuse ANY unset case (strict but breaks every real release without an explicit `1|true`).

For the live-promote attestation:
(d) Maintain ADR-017's user-executed-only clause for tool-lane live promotes (status quo — Ari cannot self-promote live);
(e) Accept the user-ratified supersession (2026-09-26) of ADR-017's tool-lane enforcement posture: user nonce-echo via a registered chat source = F2-equivalent attestation; the 3-factor-verified arm ceremony = the accepted attestation for tool-lane live promotes; feature-first, revisit if threat model changes.

**Decision (rec).** **(b)+(e).** Stage.sh rider is the chosen supersession (Tasks 1-2 in `phase2-plan.md`). The live-promote attestation supersession is the policy hook for a future stricter pass — both halves auditable. Unverified contexts keep today's fences EXACTLY (F2 forge lane, executor env allowlist, `--f2-verified-closed` argv flag, user-executed-only for non-tool-lane live rung).

**Recommended default:** (b) + (e). Rider lands in v0.15.3; supersession is the user-ratified posture effective 2026-09-26.
**If the user picks (a):** v0.15.2 staging accidents repeat on the next release that forgets the override (the fix is structural, not behavioral).
**If the user picks (c):** every real release requires an explicit `ENSEMBLE_ROLLBACK_SAFE=1|true`; safe but friction — every CI pipeline must pass the override.
**If the user picks (d):** the user nonce-echo ceremony is a one-off workaround that future tool-lane arms cannot rely on; the supersession becomes "Ari cannot self-promote live" until F2 closes — which can be years out (out-of-scope per the loopback-API-auth workstream fence).

**Consequence.**
- **`stage.sh:172-180` silent-false path is DEAD.** The full-history destructive-DDL grep is INFORMATIONAL (loud WARNING on match), never a silent side-effect. Override legitimacy test ("only set `ENSEMBLE_ROLLBACK_SAFE=1` when the migration delta between previous tag and `$VERSION` is EMPTY") is encoded in the WARNING text.
- **Manifest schema unchanged.** Consumer gates (`promote.sh:330`, `rollback.sh:98-101`, `lib.sh:1446-1449`, `launcher.sh:783-797`, `daemon/tools/upgrade_tools.py:2286-2293` + `:1345-1370`, `status.sh:88-90`) all continue to read `manifest.rollback_safe` as a literal boolean. No tool-layer or manager-side change.
- **F2 fences unchanged today.** Executor env allowlist (R-SR09, `daemon/tools/upgrade_journal.py:986-990`), `--f2-verified-closed` argv flag (`scripts/upgrade/promote.sh:91/103`; journal token `f2-not-verified` + `journal_mark_f2_verified` stamp), and runbook §9 ledger are all preserved.
- **Tool-lane live-promote attestation posture is documented as feature-first** with ADR-017 as the revisit anchor. If a future commission closes F2 (loopback API authentication), this ADR is revisited to reflect the new posture — the revisit criterion is "F2 forge lane closed AND any escalation observed under feature-first supersession" (the policy hook for a stricter pass).
- **P2.1 GNU-debt sites OUT OF SCOPE** (fence): `lib.sh:84-89` `_iso_to_epoch`, `lib.sh:706-712` cooldown arm, `lib.sh:1238-1295` retention eviction — uname-dispatch family is the fix; backlog, NOT this mission. Plan Phase 2 names them explicitly so the implementer does not opportunistically touch them.
```

**Decision Index row to append (or insert in the next available slot in `:288-308`):**

```markdown
| 035 | stage.sh rollback_safe rider + tool-lane live-promote attestation supersession | D-FA4.5 v0.15.3: silent-false derivation REPLACED by guarded default (explicit 1\|true/0\|false honored; unset+DROP refused exit 78 with actionable WARNING; unset+no-DROP defaults true quietly); user nonce-echo via registered chat source = F2-equivalent attestation for tool-lane live promotes (3-factor-verified arm ceremony); feature-first, revisit if threat model changes; ADR-017 is the revisit anchor | minted 2026-09-26 (P2.5 tool-lane-fix) |
```

## Residual Ops Gates (out-of-code-scope — feeds W3's `plan-overview.md` integration)

These obligations are **NOT implemented in v0.15.3 code**. They are runbook/evidence items the operator must satisfy before live eligibility may be asserted on the tool-lane mechanism. Documented here so W3 can absorb them into `plan-overview.md`'s "remaining ops gates" bullet list; the implementer reads them and knows the live-rung gate is ops-side, not code.

### Gate 1 — Scripts-bundling validation (FL-23)

**Spec** (`docs/runbooks/upgrade-drills.md` §8.4(i) `:482`): `stage.sh` bundles `scripts/upgrade/` into the release (`releases/<ver>/scripts/upgrade/`). Resolution order: **explicit env override (`ENSEMBLE_UPGRADE_SCRIPTS_DIR`) → release-local bundle → repo (`scripts/upgrade/`, dev only)**. Dev keeps the repo path; demo/live resolve release-local; the env override remains the ops escape hatch (disclosed use only).

**Checklist items (§8.4(ii) :484 items (1)-(2)):**
- (1) the bundling implementation lands (`stage.sh` change + resolution chain);
- (2) ONE script-driven promote validates the bundled-release path end-to-end on demo.

### Gate 2 — 3 fresh ari cycles on the bundled release

**Spec** (`docs/runbooks/upgrade-drills.md` §8.4(ii)(3) `:484`): "the qualifying cycles must run on the mechanism being certified". Rationale (staleness rule §7): a mid-phase mechanism promote would reset the banked ledger — the N-gate must be satisfied by cycles exercising the FINAL mechanism, so the change must land inside the qualifying window, not between banked cycles and promotion.

**Cycle stage-command guidance (v0.15.3 D-FA4.5 supersession — ADR-035).** Every runbook §8.4 cycle's `stage.sh` invocation that stages this v0.15.3 release MUST set `ENSEMBLE_ROLLBACK_SAFE=1` EXPLICITLY in the cycle's `.env` append, alongside the `ENSEMBLE_UPGRADE_SCRIPTS_DIR` provisioning already captured at §8.4(iii) `:486`. The override is legitimate, NOT a bypass: the v0.15.2 → v0.15.3 migration delta is EMPTY (this mission adds zero migrations — `daemon/migrations/versions/*.sql` is unchanged), so the v0.15.3 rider's override legitimacy test ("only set `ENSEMBLE_ROLLBACK_SAFE=1` when the migration delta between the previous release tag and `$VERSION` is EMPTY") is satisfied by construction. The full-history DROP grep (stage.sh:163-171) is irrelevant to this delta — every destructive DDL that fires the heuristic was applied before v0.15.2, so the v0.15.3 release genuinely CAN roll back to v0.15.2; `=1` is the truthful value. The override would be a bypass only if v0.15.3 introduced a destructive DDL itself — it does not. Per-cycle evidence (the RESULTS file recorded per §8.4(iii) `:486`) MUST additionally capture the `ENSEMBLE_ROLLBACK_SAFE=1` line from the cycle's `.env` and `rollback_safe=true` from the resulting manifest (mirror of the §8.4(iii) `cmp`+`md5` proofs for `ENSEMBLE_UPGRADE_SCRIPTS_DIR`). Cites: ADR-035 (this section's policy hook), stage.sh:172-180 rider (the contract being exercised), runbook §8.4(iii) `:486` (the per-cycle `.env` provenance template to mirror).

**Provenance note** (`docs/runbooks/upgrade-drills.md` §8.4(iii) `:486`): cycles #1-#3 ran with the disclosed `ENSEMBLE_UPGRADE_SCRIPTS_DIR` env-var provisioning — `.env` appended per cycle (value recorded in each RESULTS file), the var live during the armed call, `.env` restored bit-exact after each cycle's evidence capture (`cmp` + md5 proofs — B7 §2 / B8a §2). The mechanism is identical across all three banked cycles (consistent evidence within the ledger); design (a) supersedes the env-var path for future releases per the checklist above.

**Partial-coverage caveat:** tester dev/demo e2e exercises parts of the mechanism but the runbook requires the cycles ON the bundled release specifically.

### Gate 3 — Enforcement note: `ledger_check.py` does NOT verify bundling

**Spec** (`scripts/upgrade/ledger_check.py:277-296`): `gate()` returns BLOCKED when `f2_state == "open"` BEFORE any cycle-count logic (per runbook §9); `count >= N_REQUIRED (=3, ADR-021)` → ELIGIBLE; else NOT-READY. `classify()` (`:230-274`) walks newest→oldest; version change supersedes older cycles; a VIOLATION breaks the trailing consecutive-clean streak but never erases history (ADR-021 user-ruled 2026-08-23).

**Gap acknowledgment:** `ledger_check.py` does NOT verify that bundling implementation landed or that cycles ran on the bundled mechanism specifically — those are runbook-checklist/evidence obligations (§8.4(ii)-(iii), decisions.md Gate Rulings & Fences items 1-7), i.e., genuinely ops-side. The script enforces the cycle count + the F2 OPEN/BLOCKED posture; the bundling + cycle-mechanism provenance are evidence items the operator supplies via `.env` append + RESULTS file per cycle.

### Gate 4 — `--f2-verified-closed` + user-executed-only (in-code but operator-executed factors, listed for completeness)

**Spec** (`scripts/upgrade/promote.sh:91/103` per runbook `:499`; `f2-not-verified` refusal token + `journal_mark_f2_verified` stamp in `lib.sh`): the argv flag is required for live rung; the F2-closed attestation is stamped on the live txn at open time (`f2_verified_closed:true` + `f2_verified_at` ISO timestamp + optional `f2_verified_note` from `F2_VERIFIED_NOTE` env) — auditable at the enforcement point, riding the txn into the journal record. The `ENSEMBLE_UPGRADE_LIVE=1` guard remains a separate, still-required factor; the flag is **additive, not a replacement**.

**And even with F2 closed:** the live rung remains **USER-EXECUTED** — automation stops at demo permanently (ADR-017 env-target model; `promotion-ladder.md` S4-S6 are USER rows; §8 above is a design, never an agent procedure).

## Test Plan

### Pack additions to `test/packs/release_journal_unit_test.sh` (53-line wrapper)

The existing wrapper (`test/packs/release_journal_unit_test.sh:36` — `timeout 120s bash tests/test_release_journal.sh 2>&1 | tee "$OUT"`) is the entry point. No pack-scope change; the additions go inside `tests/test_release_journal.sh` (the 1368-line suite it wraps). The pack wrapper stays unchanged — it propagates the inner suite's exit code as-is (`:38-53`); new test cases ride that propagation.

### New test cases in `tests/test_release_journal.sh`

**Reuses the existing fixture infrastructure:**
- `FIXTURE` (`:60+`) — throwaway install dir built by the suite
- `run_stage` (around `:292`) — wrapper that runs `bash $FAKE_REPO/scripts/upgrade/stage.sh sandbox --version $SBX_V1 --skip-build <stub-binary>` with HOME isolation
- `SBX` (the sandbox install dir) — `releases/$SBX_V1/manifest.json` is the manifest under test
- `FAKE_REPO` (`:78-90`) — the existing throwaway repo with the benign `CREATE TABLE x (id int);` migration at `:90`. This is the **no-DROP fixture** — the rider's `unset + no-DROP → quiet default true` branch (9f) runs against it.
- `FAKE_REPO_DROP` (NEW — added in §9 below, ~10 lines, same git-init/tag pattern as `FAKE_REPO`) — a SECOND throwaway repo containing one migration file with `DROP TABLE`/`DROP COLUMN`. This is the **DROP-bearing fixture** — the rider's `unset + DROP → refuse exit 78` branch (9e, 9g) runs against it.

**Six new test cases** (Tasks 4 + 5):

```bash
# ─── 9. stage.sh ROLLBACK_SAFE rider (P2 / v0.15.3) ─────────────────────────
section "rollback_safe rider (v0.15.3 D-FA4.5 supersession)"

# Build the DROP-bearing fixture (FAKE_REPO_DROP). Same structure as FAKE_REPO
# at :78-90, but the migrations dir carries a destructive DDL file so the
# rider's unset+DROP refusal branch fires. ~10 lines, mirrors the FAKE_REPO
# git-init/tag pattern. Lives in the suite so it cleans up with FIXTURE.
FAKE_REPO_DROP="$FIXTURE/fake-repo-drop"
rm -rf "$FAKE_REPO_DROP"; mkdir -p "$FAKE_REPO_DROP"
for d in scripts agents frontend/dist/frontend/browser daemon/migrations/versions; do
    mkdir -p "$FAKE_REPO_DROP/$d"
done
cp "$FAKE_REPO/scripts/upgrade/"*.sh "$FAKE_REPO_DROP/scripts/upgrade/"
printf 'stub-agent-definition\n' > "$FAKE_REPO_DROP/agents/leader/soul.md"
printf 'port: ${PORT:-8088}\n' > "$FAKE_REPO_DROP/config.yaml"
printf 'stub-index\n' > "$FAKE_REPO_DROP/frontend/dist/frontend/browser/index.html"
printf 'stub-app\n' > "$FAKE_REPO_DROP/frontend/dist/frontend/browser/main.js"
printf '#!/bin/bash\n# stub launcher (unit fixture)\n' > "$FAKE_REPO_DROP/launcher.sh"
# the load-bearing line — destructive DDL makes the rider refuse on unset:
printf 'CREATE TABLE x (id int);\nDROP TABLE x;\n' \
    > "$FAKE_REPO_DROP/daemon/migrations/versions/20260101_000001_destructive.sql"
git -C "$FAKE_REPO_DROP" init -q
git -C "$FAKE_REPO_DROP" add -A 2>/dev/null
git -C "$FAKE_REPO_DROP" -c user.email=t@t -c user.name=t commit -qm fixture
git -C "$FAKE_REPO_DROP" tag "$SBX_V1"
DROP_STAGE() {  # run_stage-style helper for FAKE_REPO_DROP
    HOME="$FAKE_HOME" VERSION="$SBX_V1" TARGET=sandbox \
        INSTALL_DIR="$1" PORT="$SBX_PORT" \
        bash "$FAKE_REPO_DROP/scripts/upgrade/stage.sh" sandbox --version "$SBX_V1" \
        --skip-build "$FIXTURE/stub-prod"
}

# 9a. explicit ENSEMBLE_ROLLBACK_SAFE=1 honored (against FAKE_REPO; benign)
RIDER_DIR="$FIXTURE/rider"
mkdir -p "$RIDER_DIR"
out="$(env -u ENSEMBLE_ROLLBACK_SAFE HOME="$FAKE_HOME" TARGET=sandbox \
    INSTALL_DIR="$RIDER_DIR" PORT="$SBX_PORT" \
    bash "$FAKE_REPO/scripts/upgrade/stage.sh" sandbox --version "$SBX_V1" \
    --skip-build "$FIXTURE/stub-prod" 2>&1)"; rc=$?
assert_eq "9a pre: stage rc unset baseline (benign fixture, no-DROP)" "0" "$rc"
rm -rf "$RIDER_DIR"
mkdir -p "$RIDER_DIR"
out="$(env ENSEMBLE_ROLLBACK_SAFE=1 HOME="$FAKE_HOME" TARGET=sandbox \
    INSTALL_DIR="$RIDER_DIR" PORT="$SBX_PORT" \
    bash "$FAKE_REPO/scripts/upgrade/stage.sh" sandbox --version "$SBX_V1" \
    --skip-build "$FIXTURE/stub-prod" 2>&1)"; rc=$?
assert_eq "9a explicit=1 honored: rc" "0" "$rc"
v=$(manifest_field "$SBX_V1" rollback_safe 2>/dev/null \
    < "$RIDER_DIR/releases/$SBX_V1/manifest.json")
assert_eq "9a explicit=1 honored: rollback_safe=true" "true" "$v"

# 9b. explicit ENSEMBLE_ROLLBACK_SAFE=true honored (against FAKE_REPO; benign)
rm -rf "$RIDER_DIR"; mkdir -p "$RIDER_DIR"
out="$(env ENSEMBLE_ROLLBACK_SAFE=true HOME="$FAKE_HOME" TARGET=sandbox \
    INSTALL_DIR="$RIDER_DIR" PORT="$SBX_PORT" \
    bash "$FAKE_REPO/scripts/upgrade/stage.sh" sandbox --version "$SBX_V1" \
    --skip-build "$FIXTURE/stub-prod" 2>&1)"; rc=$?
assert_eq "9b explicit=true honored: rc" "0" "$rc"
v=$(manifest_field "$SBX_V1" rollback_safe 2>/dev/null \
    < "$RIDER_DIR/releases/$SBX_V1/manifest.json")
assert_eq "9b explicit=true honored: rollback_safe=true" "true" "$v"

# 9c. explicit ENSEMBLE_ROLLBACK_SAFE=0 honored (against FAKE_REPO; benign —
#     operator affirms unsafe; the migration delta is empty so this is
#     actually a wrongful choice — but the rider honors explicit override)
rm -rf "$RIDER_DIR"; mkdir -p "$RIDER_DIR"
out="$(env ENSEMBLE_ROLLBACK_SAFE=0 HOME="$FAKE_HOME" TARGET=sandbox \
    INSTALL_DIR="$RIDER_DIR" PORT="$SBX_PORT" \
    bash "$FAKE_REPO/scripts/upgrade/stage.sh" sandbox --version "$SBX_V1" \
    --skip-build "$FIXTURE/stub-prod" 2>&1)"; rc=$?
assert_eq "9c explicit=0 honored: rc" "0" "$rc"
v=$(manifest_field "$SBX_V1" rollback_safe 2>/dev/null \
    < "$RIDER_DIR/releases/$SBX_V1/manifest.json")
assert_eq "9c explicit=0 honored: rollback_safe=false" "false" "$v"

# 9d. explicit ENSEMBLE_ROLLBACK_SAFE=false honored (same caveat as 9c)
rm -rf "$RIDER_DIR"; mkdir -p "$RIDER_DIR"
out="$(env ENSEMBLE_ROLLBACK_SAFE=false HOME="$FAKE_HOME" TARGET=sandbox \
    INSTALL_DIR="$RIDER_DIR" PORT="$SBX_PORT" \
    bash "$FAKE_REPO/scripts/upgrade/stage.sh" sandbox --version "$SBX_V1" \
    --skip-build "$FIXTURE/stub-prod" 2>&1)"; rc=$?
assert_eq "9d explicit=false honored: rc" "0" "$rc"
v=$(manifest_field "$SBX_V1" rollback_safe 2>/dev/null \
    < "$RIDER_DIR/releases/$SBX_V1/manifest.json")
assert_eq "9d explicit=false honored: rollback_safe=false" "false" "$v"

# 9e. UNSET + DROP-detected (against FAKE_REPO_DROP) →
#     REFUSE exit 78 with actionable WARNING
rm -rf "$RIDER_DIR"; mkdir -p "$RIDER_DIR"
out="$(env -u ENSEMBLE_ROLLBACK_SAFE HOME="$FAKE_HOME" TARGET=sandbox \
    INSTALL_DIR="$RIDER_DIR" PORT="$SBX_PORT" \
    bash "$FAKE_REPO_DROP/scripts/upgrade/stage.sh" sandbox --version "$SBX_V1" \
    --skip-build "$FIXTURE/stub-prod" 2>&1)"; rc=$?
assert_eq "9e unset+DROP refused: rc 78" "78" "$rc"
assert_contains "9e unset+DROP refused: WARNING cites override" "ENSEMBLE_ROLLBACK_SAFE=1" "$out"
assert_contains "9e unset+DROP refused: WARNING cites override" "ENSEMBLE_ROLLBACK_SAFE=0" "$out"
assert_contains "9e unset+DROP refused: WARNING cites override legitimacy" "migration delta" "$out"
assert_contains "9e unset+DROP refused: WARNING cites supersession history" "v0.14.2" "$out"
assert_contains "9e unset+DROP refused: WARNING cites ADR" "ADR-035" "$out"
# NO manifest written when refused
[ ! -f "$RIDER_DIR/releases/$SBX_V1/manifest.json" ] && _pass \
    || _fail "9e unset+DROP refused: no manifest written"

# 9f. UNSET + no-DROP-detected → quiet default true (against FAKE_REPO; the
#     existing benign fixture already has CREATE TABLE only — :90). No new
#     fixture needed; runs immediately as a v0.15.3 test.
rm -rf "$RIDER_DIR"; mkdir -p "$RIDER_DIR"
out="$(env -u ENSEMBLE_ROLLBACK_SAFE HOME="$FAKE_HOME" TARGET=sandbox \
    INSTALL_DIR="$RIDER_DIR" PORT="$SBX_PORT" \
    bash "$FAKE_REPO/scripts/upgrade/stage.sh" sandbox --version "$SBX_V1" \
    --skip-build "$FIXTURE/stub-prod" 2>&1)"; rc=$?
assert_eq "9f unset+no-DROP quiet default: rc 0" "0" "$rc"
assert_contains "9f unset+no-DROP quiet default: stage log records source" \
    "source=default=true" "$out"
v=$(manifest_field "$SBX_V1" rollback_safe 2>/dev/null \
    < "$RIDER_DIR/releases/$SBX_V1/manifest.json")
assert_eq "9f unset+no-DROP quiet default: rollback_safe=true" "true" "$v"
```

### The silent-false path is dead (Task 5)

```bash
# 9g. REGRESSION PIN: silent-false path is DEAD (v0.14.2 + v0.15.1 accident class).
#     Runs against FAKE_REPO_DROP (which carries destructive DDL). Under the
#     pre-rider behavior: unset → rc=0 + manifest.rollback_safe=false (SILENT false).
#     Under the rider: unset → rc=78 + no manifest. This assertion catches a
#     regression to the silent-false derivation (the exact class that bit
#     v0.14.2 + v0.15.1; see research-findings-shell-e2e.md §Q1 accident list).
rm -rf "$RIDER_DIR"; mkdir -p "$RIDER_DIR"
out="$(env -u ENSEMBLE_ROLLBACK_SAFE HOME="$FAKE_HOME" TARGET=sandbox \
    INSTALL_DIR="$RIDER_DIR" PORT="$SBX_PORT" \
    bash "$FAKE_REPO_DROP/scripts/upgrade/stage.sh" sandbox --version "$SBX_V1" \
    --skip-build "$FIXTURE/stub-prod" 2>&1)"; rc=$?
if [ "$rc" = "78" ] && [ ! -f "$RIDER_DIR/releases/$SBX_V1/manifest.json" ]; then
    _pass
else
    _fail "9g silent-false path is DEAD (v0.14.2 + v0.15.1 accident class)" \
        "rc=78, no manifest" "rc=$rc, manifest=$( [ -f "$RIDER_DIR/releases/$SBX_V1/manifest.json" ] && echo exists || echo absent )"
fi
```

### Existing test preserved

The existing `manifest has rollback_safe` assertion at `tests/test_release_journal.sh:297-301` continues to pin the field's presence in the manifest. **The rider changes the field's VALUE derivation, not the field's existence** — every existing test that reads the manifest field remains valid (the manifest still has the field, with a different value under the unset+DROP case because the case now REFUSES rather than silently writes `false`).

The existing `8h` test at `tests/test_release_journal.sh:821-826` (`adopt_stale_txn` halt citing the schema-drift guard for `previous NOT rollback_safe`) is unaffected — that test exercises the **consumer gate** at `lib.sh:1446-1449`, which reads the manifest field unchanged.

### Pack registration (no change required)

The pack wrapper `test/packs/release_journal_unit_test.sh` stays as-is (53 lines, transparent wrapper). New test cases inside `tests/test_release_journal.sh` ride the existing `bash tests/test_release_journal.sh` invocation. No new pack file; no pack-scope change. The pack registry entry is unchanged (the pack's name + scope description remains accurate — the rider is a stage.sh behavior, and the existing pack already wraps the journal/unit suite that includes the stage section).

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | **Existing CI pipelines call `stage.sh` without `ENSEMBLE_ROLLBACK_SAFE` set** on repos with destructive DDL in migration history — they will start refusing exit 78 where they previously silently wrote `false`. The refuse is a **good** outcome (it catches the accident class) but it's a **breaking** outcome for any pipeline that relied on the silent behavior. | Medium (operational disruption; no data loss — refuse before any release dir write) | High (every real release is in this state today) | The WARNING text is actionable and includes both override forms; the refuse is before any release dir write (no half-staged payload); the migration is mechanical (`ENSEMBLE_ROLLBACK_SAFE=0` for genuine unsafe releases, `=1` for false positives); the v0.15.2 release notes / CHANGELOG must name the breaking change explicitly. |
| 2 | **The rider is a strict mode change** — operators who DO want `rollback_safe=false` (e.g. a genuine schema-drift release) must now affirm explicitly via `ENSEMBLE_ROLLBACK_SAFE=0`. If they forget, the refuse halts the stage. | Medium | Medium | Same mitigation as Risk 1; plus the WARNING text + ADR-035 explicitly name the override legitimacy test so the operator knows when to use which value. |
| 3 | **ADR-035 supersedes a portion of ADR-017's tool-lane enforcement posture** — if a future commission interprets ADR-017 literally ("user-executed-only for live rung"), they may reject the feature-first posture this ADR codifies. | Medium (policy confusion; no code impact — the F2 fences are unchanged today) | Low (ADR-035 cites ADR-017 as the revisit anchor explicitly; the supersession is scoped to tool-lane live promotes only) | The ADR-017 citation in ADR-035's consequence section is explicit; the supersession is narrowly scoped ("feature-first, revisit if threat model changes"); the revisit criterion is named. |
| 4 | **The DROP-bearing fixture (tests 9e + 9g) requires a second throwaway repo** — the existing FAKE_REPO is benign (CREATE TABLE only at `:90`); the rider's unset+DROP refusal branch fires only when destructive DDL is present, so a second `FAKE_REPO_DROP` fixture is added in §9 with the same git-init/tag pattern as the existing fixture (~10 lines). | Low (test-only; the fixture is a copy of FAKE_REPO + one DROP-line migration) | Low (mechanical; the FAKE_REPO_DROP build mirrors the FAKE_REPO pattern at `:78-101`) | The fixture build is `cp` of the FAKE_REPO scripts/payload + one new migration file containing `CREATE TABLE x; DROP TABLE x;` + a fresh git tag. Cleaned up with `$FIXTURE`. The opposite risk — deferring 9f as "intricate to fixture" — was the inverted error in the prior draft: 9f runs against the existing benign FAKE_REPO with no fixture change and is part of v0.15.3; 9e + 9g needed the DROP fixture (now added). |
| 5 | **Future commission closes F2 (loopback API authentication)** — ADR-035 names this as the revisit anchor. If it closes, the ADR must be revisited to reflect the new posture, otherwise the policy hook for a stricter pass is stale. | Low (no immediate impact; ADR-035 is auditable today) | Low (F2 closure is fenced out per the loopback-API-auth workstream fence) | The consequence section names F2 closure as the revisit criterion; the ADR's supersession posture is "feature-first" precisely because F2 closure is uncertain. |
| 6 | **The rider's WARNING text uses multi-line `_warn`** — bash continuation `\` is established at lib.sh:117-119 but the rider uses a slightly different pattern (multi-arg vs concatenation). A reviewer may flag style consistency. | Low (style only; no functional difference) | Low | Use the lib.sh:117-119 concatenation pattern as the closer template if a reviewer flags it; the WARNING text content is unchanged either way. |
| 7 | **The GNU-debt fence may be misinterpreted as "this phase fixes BSD portability"** — a future implementer may try to opportunistically uname-dispatch the three fence sites (lib.sh:84-89, lib.sh:706-712, lib.sh:1238-1295) under the misreading "the rider is about BSD safety". | Medium (scope creep into a different fix family; potential blast radius if uname-dispatch is wrong) | Low (the fence comment block at the top of the rider + this plan's "Out of Scope" section + the research findings file all name the sites) | Triple-encode the fence: (i) rider code comment block at top of new derivation branch (Task 7), (ii) this plan's "Out of Scope" section with table, (iii) the ADR-035 consequence section. The fence is reinforced in three places. |

## Dependencies on P1

**P1 (Python subsystem — manager.py executor spawn + argv/env passthrough + pending_actions GC + reconcile sweep) is independent of P2's CODE in the strict sense:**

- P1 modifies `daemon/manager.py` (executor spawn seam, child-exit watcher), `daemon/tools/upgrade_tools.py` (boot-time + periodic reconcile sweep), `daemon/tools/upgrade_journal.py` (pending_actions GC expansion + argv passthrough). P2 modifies `scripts/upgrade/stage.sh` only. Zero file overlap.
- P1's argv/env passthrough mechanic ("argv carries `--f2-verified-closed` when the gate factors verify") is the MECHANISM; ADR-035's supersession ("user nonce-echo via registered chat source = F2-equivalent attestation") is the POLICY. P2's ADR documents the policy without prescribing the mechanism. P1 can implement the mechanism in any way that satisfies the existing F2 fences — the ADR doesn't constrain P1's code form.
- P1's pending_actions GC expansion is independent of the rider (the GC operates on a different journal field with a different lifecycle).
- P1's reconcile sweep is independent of the rider (the sweep operates on `pending_op`, not on `rollback_safe`).

**Where P2 references P1 in the ADR text:**

ADR-035's consequence section names "executor env allowlist, `--f2-verified-closed` argv flag, runbook §9 ledger" as the **unchanged F2 fences**. These are owned by P1 + the existing P2.1/P2.2 substrate. P2's ADR cites them as the **policy perimeter**; P2 does NOT modify them.

**No blocker in either direction.** P1 and P2 can land in any order; the v0.15.3 release commits both. If P1 lands before P2, the live-rung gate already works via the existing fences + the pending-actions GC fix; P2's rider is a staging-time correctness improvement independent of P1's runtime correctness improvements. If P2 lands before P1, the rider is in place; P1's argv/env passthrough then enforces the existing F2 fences on the verified arm without further changes.

**Coordination note for the integrator:** both P1 and P2 must be present in the v0.15.3 release commit batch. The integrator's job is the merge order, not the design — this plan does not constrain it.

## Constraints

- **Journal format compat: N/A.** P2 does not touch journal files; manifest schema unchanged; no format migration needed.
- **Shell tests follow existing wrapper style.** New test cases in `tests/test_release_journal.sh` use the established `assert_eq` / `assert_contains` / `section` / `_pass` / `_fail` helpers (`:37-58`) and the throwaway git-tagged fixture pattern (`:24-29`); the pack wrapper at `test/packs/release_journal_unit_test.sh` is unchanged.
- **BSD portability.** No new uname-dispatch, no GNU-only constructs, no `flock(1)`, no `set -f`. The rider is fully portable under bash 3.2 / BSD date / BSD mv (lib.sh:36-44 header fence).
- **Read-only on source files other than `stage.sh`.** P2's only source write is `stage.sh:172-180` (the derivation branch) plus the stage log line at `:381`. The ADR insert is the only documentation write. No tool-layer, no manager-side, no migration files.
- **No new files in `scripts/upgrade/`.** The rider lives in `stage.sh`. No new helper modules.
- **No new tokens in `ENSEMBLE_ROLLBACK_SAFE`.** Today's `1|true|0|false|""|unset` enum is preserved; no `delta-scoped` or other extension.
- **No manifest schema changes.** Field name `rollback_safe` + JSON shape (literal boolean) + consumer field-extraction (`manifest_field` lib.sh helper) all unchanged. Only the value derivation moves.
- **No live-rung gate changes.** The four-part PRE-LIVE checklist (runbook §8.4(ii):484 + §9 ledger + F2 gate + user-executed) is preserved; ADR-035's supersession is a policy posture, not a code fence change.

## Exit Criterion

Phase 2 is DONE when **ALL** of the following hold:

1. `scripts/upgrade/stage.sh:172-180` is the new guarded-default derivation branch (the WARNING text + lib.sh helper usage matches this plan's "Implementation" section exactly).
2. `scripts/upgrade/stage.sh` end-of-script log line (`:381`) carries the new `source=$ROLLBACK_SAFE_SRC` token.
3. `tests/test_release_journal.sh` has the 6+1 new test cases (9a-9g) covering explicit/unset+both-branches/silent-false-dead; the existing 9-section tests (manifest has rollback_safe at `:297`, adopt_stale_txn halt at `:821-826`) remain green.
4. `test/packs/release_journal_unit_test.sh` exits 0 when run via `timeout 300 bash test/packs/release_journal_unit_test.sh`.
5. `.agents/shared/planning/self-restart-upgrade-phase2/decisions.md` has the new `## ADR-035` section inserted between the ADR-032 block (`:213`) and the Pre-Freeze Assumption-Closure Checklist (`:215`); the Decision Index table (`:288-308`) has the new ADR-035 row.
6. No opportunistic fixes to `lib.sh:84-89`, `lib.sh:706-712`, or `lib.sh:1238-1295` (GNU-debt fence honored).
7. The "Residual Ops Gates" section of this `phase2-plan.md` is ready to be quoted into `plan-overview.md` by W3.

When all seven hold, Phase 2 is complete; W3 integrates the residual-ops-gates section into `plan-overview.md`; the integrator merges P1 + P2 into the v0.15.3 release commit batch.