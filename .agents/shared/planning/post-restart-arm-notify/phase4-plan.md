# Phase 4: Banner Updates + Runbook + Drill + Release Notes

> **⛔ HARD CONSTRAINT (inherited from Phase 2, governs every task below):**
> NEVER touch the live/production ensemble environment — it is the running environment of Ari and all live agents (~/agents-ensemble, port 9797, prod DB, ENSEMBLE_DEPLOY_LIVE are out of bounds; live pids must remain untouched). ALL work/testing/drills in dev and demo only. If any plan step would require touching live, mark it as USER-GATED and design it as an explicit user-confirmed action. Sandbox instances (own port + throwaway PG) are fine.

**ADR basis:** ADR-039 (Wake record schema + write atomicity), ADR-040 (Boot sweep + wake delivery primitive), ADR-041 (Routing preservation), ADR-042 (Terminal-state gating & abandonment policy), ADR-043 (Edge cases & coalescing), ADR-044 (Kill-switch & boot-never-wedge). All six ADRs are referenced from the runbook (the docs surface); the banner text in `upgrade_tools.py` is updated to reflect the new auto-wake behavior. Cross-cutting invariants 9, 10, 11 of `architecture-recommendation.md` §8 are user-visible in the runbook; invariant 8 (live never records) is asserted by the drill.

**Scope of environment:** all implementation work targets **demo** (`~/agents-ensemble-demo`, :7979, `ensemble_demo`) and **sandboxes** (own port + throwaway PG). The drill runs against a sandbox install dir (own port + throwaway PG); the live-outright-refusal drill step is a refusal-test only (sandbox install with `self_env=live` set via the same FAKE-live marker the existing P2.2 tool-interlock tests use — never the actual live install). Live target paths exist in the scripts behind guards but their execution is **USER-GATED** and never performed by this initiative.

**Convention note:** any new Python module this phase adds uses `from __future__ import annotations` for Python 3.13 import safety. The bash drill follows the Phase 2 `test/drills/p21_upgrade_pipeline_drill.sh` precedent (bash 3.2/BSD-safe, structured log, exit-code-on-success).

---

## Objective

Close the **user-visible and operator-facing surface** of the
feature: update the arm-time banner text in `upgrade_tools.py`
(the lines that say "post-restart: ask me to run
`upgrade_status`" — they are now obsolete because the
auto-wake does the equivalent for the user), author the
**runbook** (operator-facing doc: how to disable the feature,
how to read the structured log lines, how to recover from an
abandoned wake), author the **bash drill** (the e2e counterpart
of the unit + job-queue test packs, following the Phase 2
drill precedent), and add the **release-notes line** (one-line
operator-visible summary for the v0.17.x release notes).

**Exit in one sentence:** the banner text in
`upgrade_tools.py` no longer says "ask me to run
`upgrade_status`" (the auto-wake makes that instruction
obsolete), the runbook at
`docs/runbooks/post-restart-arm-notify.md` exists and covers
the kill-switch, the structured log lines, and the recovery
flow, the bash drill at
`test/drills/post_restart_arm_notify_drill.sh` is GREEN on
demo, and the release-notes line is appended to the
`v0.17.x` release notes — all proven by the drill exit
code, the runbook's grep-verify line count, and the
banner-text regression test (T4.7).

---

## Verified Starting Point (do not re-derive)

- **The banner text precedent:** the existing
  `upgrade_tools.py` arm-return prose (e.g. "post-restart:
  ask me to run `upgrade_status`" — the line that documents
  the legacy pull-model behavior, see D-FA1.2 superseded by
  `supersession-record.md`) is a string literal in the
  arm-return branch. A grep of `upgrade_tools.py` for
  "ask me to run" identifies the exact line(s).
- **The runbook precedent:** `docs/runbooks/upgrade-drills.md`
  is the existing drill runbook. The new
  `docs/runbooks/post-restart-arm-notify.md` follows the same
  shape (numbered sections, FA / ADR cross-references, kill-switch
  section, structured-log section, recovery section).
- **The drill precedent:** `test/drills/p21_upgrade_pipeline_drill.sh`
  is the Phase 2 drill. The new
  `test/drills/post_restart_arm_notify_drill.sh` follows the
  same bash shape (sandbox install dir, sandbox daemon port,
  sandbox PG, FAKE-live marker for the live-outright-refusal
  step).
- **The release-notes precedent:** the v0.17.x release notes
  are in `RELEASE_NOTES.md` (or the equivalent
  `docs/RELEASE_NOTES.md` — verify path at PR-time). The
  convention is a one-line "Feature: ..." entry per material
  capability, with the PR / commit hash.
- **The D-FA1.2 supersession note:**
  `supersession-record.md` documents the
  D-FA1.2 pull-model → push-wake-on-boot transition. The
  banner text in `upgrade_tools.py` is the in-code
  artifact that needs the update; the supersession note
  is the planning artifact that already exists.

---

## Design Decisions (this phase)

**D1 — Banner text update: drop the "ask me to run
`upgrade_status`" instruction, replace with the auto-wake
prose (ADR-040 + ADR-041).** The arm-return branch in
`upgrade_tools.py` currently says (paraphrased from the
supersession note's reference): "post-restart: ask me to run
`upgrade_status`". The auto-wake makes that instruction
obsolete — the system does the equivalent automatically on
restart. The new prose is: "The arm is recorded. On
daemon restart, an auto-wake will be delivered to this
instance to report the outcome. No user action required.
To disable this feature, set
`ENSEMBLE_POST_RESTART_ARM_NOTIFY=0` in `<install_dir>/.env`."
The exact line(s) are identified at PR-time via a grep of
`upgrade_tools.py` for "ask me to run" or "upgrade_status" in
the arm-return branch. The update is a string literal swap;
no logic change.

**D2 — Runbook at
`docs/runbooks/post-restart-arm-notify.md` (NEW).** Five
sections: (a) Overview (the feature, the ACs, the kill-switch);
(b) Operator's kill-switch (env-var flip, no restart required
for the env read, restart NOT required — the next tick
short-circuits); (c) Structured log lines (the four
`post_restart_arm_notify` log lines: `armed`, `delivered`,
`abandoned`, `enqueue_failed`, with grep recipes); (d)
Recovery flow (abandoned wake → user's interactive
`upgrade_status`; missing-instance → `arm_notify_no_instance`
history event → operator can create a new ari instance);
(e) Drill (one-line: "run
`test/drills/post_restart_arm_notify_drill.sh` against a
sandbox"). The runbook is the operator's single entry
point for the feature; it cross-references the six ADRs
and the `architecture-recommendation.md` sections.

**D3 — Drill at
`test/drills/post_restart_arm_notify_drill.sh` (NEW).** Six
scenarios, each with a sandbox install dir, sandbox daemon
port, sandbox PG. Convention precedent:
`test/drills/p21_upgrade_pipeline_drill.sh`. The six
scenarios are the six D1–D6 from `test-strategy.md` §2.6:
D1 (arm + kill + restart + observe wake), D2 (arm + kill +
restart + observe `terminal_outcome=committed`), D3
(sandbox `discord:user123` source + arm + kill + restart +
observe source preserved), D4 (long-downtime double-arm
+ kill + restart + observe coalesced run-list wake), D5
(kill-switch on + arm + kill + restart + observe no wake
record), D6 (live-outright-refusal + observe no journal
write). Each scenario asserts the bash exit code `0` and
prints a structured-log line with the scenario's outcome.

**D4 — Release-notes line in `RELEASE_NOTES.md` (APPEND).**
The v0.17.x release notes gain one line: "Post-restart
arm-notify: armed `system_restart` / `system_upgrade` runs
are now auto-delivered to the arming instance after a
daemon restart, in the same chat where the arm was
confirmed. See `docs/runbooks/post-restart-arm-notify.md`."
The line is one operator-visible sentence; the runbook is
the detailed surface. The PR hash is appended at merge
time (the line is authored in the Phase 4 commit, the
hash is added at PR-merge time by the standard
release-notes automation).

**D5 — Banner text regression test (T4.7) prevents the
"ask me to run `upgrade_status`" instruction from being
re-introduced.** A `tests/unit/tools/test_post_restart_arm_notify_banner.py`
test asserts the arm-return branch in `upgrade_tools.py`
does NOT contain the string "ask me to run" (or the
specific obsolete phrase, captured as a constant). The
test is a regression pin — a future PR that
re-introduces the obsolete instruction (e.g. a revert of
the Phase 4 banner update) is caught loudly.

**D6 — The drill is bash, not pytest — the operator runs it
manually as the v0.17.x acceptance check (Phase 2 §3.2
precedent).** The drill's exit code is `0` on success; the
Phase 3 placeholder smoke (T8) is unskipped when the drill
is authored. The drill runs against a sandbox install
dir; the live-outright-refusal scenario (D6) uses a
sandbox install with the FAKE-live marker, never the
actual live install.

---

## Components (file-level touch list)

### Source files modified

| File | What changes | Mandate (architecture section + ADR) |
|---|---|---|
| `daemon/tools/upgrade_tools.py` | Banner text update in `system_restart` arm-return branch (`upgrade_tools.py:2156-2263` and `system_upgrade` arm-return branch at `:2707-2872`). The exact line(s) are identified at PR-time via a grep for "ask me to run" / "post-restart" / "upgrade_status" in the arm-return branch. The new prose documents the auto-wake behavior and the kill-switch env var. **No logic change** — the string literal swap is the only modification. | D-FA3.2 + ADR-040 + ADR-044; D-FA1.2 supersession close-out (the in-code artifact of the supersession) |

### Documentation files created / modified

| File | What changes | Mandate |
|---|---|---|
| `docs/runbooks/post-restart-arm-notify.md` (NEW) | Operator runbook: Overview, Kill-switch, Structured log lines, Recovery flow, Drill. Cross-references all six ADRs (ADR-039–ADR-044) and the `architecture-recommendation.md` sections. | Phase 4 deliverable; operator-facing single entry point |
| `RELEASE_NOTES.md` (APPEND) | One-line v0.17.x release-notes line: "Post-restart arm-notify: armed `system_restart` / `system_upgrade` runs are now auto-delivered to the arming instance after a daemon restart, in the same chat where the arm was confirmed." | Phase 4 deliverable; operator-visible changelog |

### Drill files created

| File | What changes | Mandate |
|---|---|---|
| `test/drills/post_restart_arm_notify_drill.sh` (NEW) | Six bash scenarios (D1–D6) following the Phase 2 drill convention. Each scenario: sandbox install dir, sandbox daemon port, sandbox PG; arm + kill + restart + observe. Exit `0` on success, non-zero on failure. Structured log per scenario. | Phase 4 deliverable; Phase 2 §3.2 precedent |

### Test files created

| File | What changes | Mandate |
|---|---|---|
| `tests/unit/tools/test_post_restart_arm_notify_banner.py` (NEW) | Banner text regression test: asserts the arm-return branch in `upgrade_tools.py` does NOT contain the obsolete "ask me to run `upgrade_status`" instruction. The obsolete phrase is captured as a constant `OBSOLETE_PHRASE`. The test reads the file, greps for the constant, and fails loudly if found. | Phase 4 D5; release-blocker regression pin |

### Files NOT touched (explicit non-modification)

- `daemon/services/upgrade_journal_sweep.py` — no change. The
  sweep's wake branch is Phase 2.
- `daemon/api.py` — no change. The boot pass is Phase 2.
- `daemon/instance_messaging.py` — no change. The pause-gate
  and terminal-revive are existing.
- `scripts/upgrade/*` — no change. The executor is unchanged.
- `agents/ari/soul.md`, `agents/ari/tools_note.md`,
  `agents/jober/soul.md`, `agents/jober/tools_note.md` —
  the prompt-level instruction (R-7) is a follow-up patch
  to the prompt-maintenance initiative, NOT this feature.
  Phase 4 does NOT add prompt instructions; the wake body's
  self-describing pointer ("call `upgrade_status(run_id=...)`
  and report back to the user") is the in-band instruction.

---

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| **T1** | **Grep `upgrade_tools.py` for the obsolete banner text** — identify the exact line(s) in the `system_restart` arm-return branch (`:2156-2263`) and the `system_upgrade` arm-return branch (`:2707-2872`) that say "ask me to run `upgrade_status`" or "post-restart: ask me to run". Capture the line numbers + the obsolete phrase as a constant in the regression test (T7). The grep is the load-bearing step; the test's constant is the source of truth for the regression pin | none | At least one match per branch; line numbers captured; obsolete phrase captured as `OBSOLETE_PHRASE` |
| **T2** | **Banner text update in `system_restart` arm-return branch** — replace the obsolete phrase (T1) with the auto-wake prose: "The arm is recorded. On daemon restart, an auto-wake will be delivered to this instance to report the outcome. No user action required. To disable this feature, set `ENSEMBLE_POST_RESTART_ARM_NOTIFY=0` in `<install_dir>/.env`." The string literal swap is the only modification; no logic change | T1 | The new prose is present; the obsolete phrase is absent (T7 regression test GREEN) |
| **T3** | **Banner text update in `system_upgrade` arm-return branch** — same string literal swap as T2. The new prose is identical (the auto-wake behavior is the same for both arm types) | T1, T2 | Same as T2; both branches updated; both regression-test assertions GREEN |
| **T4** | **Author `docs/runbooks/post-restart-arm-notify.md`** — five sections (Overview, Kill-switch, Structured log lines, Recovery flow, Drill). Cross-references all six ADRs (ADR-039–ADR-044) and the `architecture-recommendation.md` §FA1–§FA6 sections. Total length target: 80–150 lines (the Phase 2 `upgrade-drills.md` is the length precedent) | T1–T3 | The file exists at `docs/runbooks/post-restart-arm-notify.md`; each ADR is referenced at least once; the kill-switch section documents the env var; the structured-log section lists the four log lines with grep recipes; the recovery section documents the abandoned-wake flow and the missing-instance flow; the drill section is a one-liner referencing the bash drill |
| **T5** | **Append release-notes line to `RELEASE_NOTES.md`** — one-line v0.17.x entry: "Post-restart arm-notify: armed `system_restart` / `system_upgrade` runs are now auto-delivered to the arming instance after a daemon restart, in the same chat where the arm was confirmed. See `docs/runbooks/post-restart-arm-notify.md`." The line is appended to the v0.17.x section (or the most recent v0.17.x-dev section if v0.17.x is not yet cut) | T4 | The line is in `RELEASE_NOTES.md`; the v0.17.x section is identifiable; the cross-reference to the runbook is correct |
| **T6** | **Author `test/drills/post_restart_arm_notify_drill.sh`** — six bash scenarios (D1–D6 from `test-strategy.md` §2.6). Each scenario: sandbox install dir (`/tmp/ens-wake-drill-$$` or similar), sandbox daemon port (own port, e.g. 8477), sandbox PG (throwaway); arm + kill + restart + observe. Exit `0` on success. Structured log per scenario. The FAKE-live marker is used for D6 (sandbox install with `self_env=live` set via the same pattern the existing P2.2 tool-interlock tests use — never the actual live install) | T1–T5 | `bash test/drills/post_restart_arm_notify_drill.sh` exits `0` on a sandbox install; the bash exit code is the operator's primary acceptance signal; the structured log per scenario is grep-able for the scenario's expected outcome (e.g. `delivered run_id=r-...` for D1, `no journal write` for D6) |
| **T7** | **Banner text regression test (T4.7) at `tests/unit/tools/test_post_restart_arm_notify_banner.py`** — asserts the arm-return branch in `upgrade_tools.py` does NOT contain the obsolete phrase captured in T1. The test reads the file, greps for `OBSOLETE_PHRASE`, and fails loudly if found. The test also asserts the new auto-wake prose IS present (positive assertion). Convention precedent: the existing `tests/unit/tools/test_upgrade_journal.py` static-grep tests for the ADR-034 splice discipline (line 372) — same shape | T1–T3 | `pytest tests/unit/tools/test_post_restart_arm_notify_banner.py -v` exits 0; a future PR that re-introduces the obsolete phrase FAILS the test loudly with the line number + the obsolete phrase in the assertion message |
| **T8** | **Unskip the Phase 3 drill smoke placeholder** — the `pytest.mark.skip` added in Phase 3 T8 is removed; the smoke now invokes the bash drill (T6) and asserts the bash exit code is `0`. The smoke is registered in `conftest.py` per the Phase 2 drill convention | T6, T7 | `pytest tests/job_queue/test_post_restart_arm_notify_edge_cases.py::test_drill_smoke -v` exits 0 with the bash drill's exit code asserted |
| **T9** | **Non-regression check: full Phase 1 + Phase 2 + Phase 3 packs remain green** — Phase 4 changes the banner text in `upgrade_tools.py` (T2, T3) and adds three new files (T4, T5, T6) + one new test (T7). The banner text change is a string literal swap; it does not touch the arm's logic, the refusal matrix, or the wake record. The new files are docs and drill — no source-code change to the daemon | T1–T8 | Full unit + job-queue packs exit 0; the existing 30/30 `test_upgrade_journal.py` + 135/135 `test_upgrade_tools.py` + 243/243 `test_release_journal.sh` all pass byte-exact; the Phase 2 + Phase 3 packs (T1.* + T2.* + T3.* + T4.1–T4.6 + T5.1–T5.15 + T6.1–T6.3) all pass |
| **T10** | **Live untouched check** — every acceptance step on demo is preceded by a live-pid checkpoint (the existing Phase 2 §5 precedent). The drill runs against a sandbox install dir; the live install is never touched. The release-notes line is a documentation change with zero code-path impact | T1–T9 | Live pids verified unchanged at every checkpoint (the drill's bash log records the checkpoint) |

---

## Coupling

- **Tight with Phase 1 (ADR-039)** — the banner text update
  (T2, T3) reflects the durable record the arm now writes; the
  regression test (T7) prevents the obsolete instruction
  (the legacy pull-model instruction) from being re-introduced.
- **Tight with Phase 2 (ADR-040 + ADR-041 + ADR-042 + ADR-043
  + ADR-044)** — the runbook (T4) cross-references all five
  Phase 2 ADRs; the drill (T6) exercises the boot pass + the
  wake delivery + the terminal-state gating + the edge cases
  + the kill-switch.
- **Tight with `supersession-record.md`** — the banner text
  update is the in-code artifact of the D-FA1.2 supersession.
  The supersession note is the planning artifact; the banner
  is the user-facing artifact. The two close the supersession
  jointly.
- **Tight with the existing Phase 2 drill
  (`test/drills/p21_upgrade_pipeline_drill.sh`)** — the new
  drill follows the same bash shape; the FAKE-live marker
  for the live-outright-refusal step is reused from the
  existing P2.2 tool-interlock tests.
- **Tight with `RELEASE_NOTES.md`** — the v0.17.x release
  notes gain one line (T5). The line is a documentation
  change with zero code-path impact.
- **Loose with the prompt-maintenance initiative (R-7)** —
  the prompt-level instruction for ari / jober to recognize
  `system_context.kind=post_restart_arm_notify` is a
  follow-up patch, NOT this feature. Phase 4 does not
  touch the prompts; the wake body's self-describing pointer
  is the in-band instruction.
- **Independent of** the daemon internals — Phase 4 is
  docs + drill + banner + test. The daemon code is
  unchanged from Phase 3.

---

## Per-Phase Verification (test-strategy.md mapping)

| Test ID | Description | Where | Verifies |
|---|---|---|---|
| **T4.7** | Banner text regression — `upgrade_tools.py` does NOT contain the obsolete phrase; new auto-wake prose IS present | `tests/unit/tools/test_post_restart_arm_notify_banner.py` | D-FA1.2 supersession close-out; release-blocker regression pin |
| **T8 (drill smoke)** | Drill smoke — `test/drills/post_restart_arm_notify_drill.sh` exits `0` against a sandbox install | `tests/job_queue/test_post_restart_arm_notify_edge_cases.py` (unskipped from Phase 3) | All six scenarios (D1–D6) reachable end-to-end; D6 (live-outright-refusal) is a refusal-test only via FAKE-live marker |
| **D1** | arm + kill + restart + observe wake delivered | `test/drills/post_restart_arm_notify_drill.sh` | AC1 + AC2 end-to-end on sandbox |
| **D2** | arm + kill + restart + observe `terminal_outcome=committed` | same | AC2 + AC4 (terminal-state) end-to-end |
| **D3** | sandbox `discord:user123` source + arm + kill + restart + observe source preserved | same | AC3 (routing) end-to-end |
| **D4** | long-downtime double-arm + kill + restart + observe coalesced run-list wake | same | AC5 (coalesce) end-to-end |
| **D5** | kill-switch on + arm + kill + restart + observe no wake record | same | ADR-044 (kill-switch) end-to-end |
| **D6** | live-outright-refusal + observe no journal write | same (FAKE-live marker; never actual live) | D-FA5.5 / ADR-044 live-outright-refusal inheritance; invariant 8 |

**Pre-Phase-4 baseline:** the full Phase 1 + Phase 2 + Phase 3
packs (T1.* + T2.* + T3.* + T4.1–T4.6 + T5.1–T5.15 + T6.1–T6.3)
remain green (T9).
**Post-Phase-4 invariant:** the new runbook is discoverable from
`docs/runbooks/`; the release-notes line is in `RELEASE_NOTES.md`;
the drill is registered in the drill pack (per Phase 2 §3.2
convention); the regression test pins the banner text.

---

## Risks (phase-specific — full register: sibling `risk-register.md`)

| # | Risk | Impact | Mitigation |
|---|------|--------|------------|
| R4.1 | The banner text update is a string literal swap; a careless edit could break the arm-return logic (e.g. a missing closing quote, a wrong indentation that breaks Python parsing) | **High** | The change is a single-line edit; the existing 135/135 `test_upgrade_tools.py` pack covers the arm-return surface (the refusal matrix, the success matrix, the dry-run matrix); the Phase 2 + Phase 3 packs cover the wake record + wake delivery; a regression in the arm logic is caught loudly by the full pack. The change is a no-op for the test pack — the new prose is a free-form string, not a regex-matched token |
| R4.2 | The runbook's structured-log section documents log lines that may evolve (e.g. a future PR adds a new `post_restart_arm_notify_<event>` log line) | Low | The runbook is a doc; it can be updated in the same PR that adds the new log line. The drill's structured log per scenario is the source of truth for "what the operator sees today"; the runbook's log section is a snapshot |
| R4.3 | The drill's D6 (live-outright-refusal) uses the FAKE-live marker; a future PR could change the FAKE-live marker pattern and silently break the drill | Low | The FAKE-live marker is the existing pattern from the P2.2 tool-interlock tests; the drill's D6 reuses the same setup. A future PR that changes the marker pattern must update both the tool-interlock tests AND the wake drill in the same PR |
| R4.4 | The release-notes line is appended to `RELEASE_NOTES.md`; a future PR that re-organizes the release notes (e.g. moves the v0.17.x section) could orphan the line | Low | The line is one operator-visible sentence; the runbook is the detailed surface. The release notes are operator-facing changelog; re-organization is a routine PR |
| R4.5 | The regression test (T7) reads the file at test-time; a future PR that re-organizes `upgrade_tools.py` (e.g. extracts the arm-return branch into a helper) could make the test's grep miss the obsolete phrase even if it's still present | Low | The test's grep is on the file's content, not the file's structure. A re-organization that preserves the obsolete phrase (in a helper) would still fail the test. A re-organization that DROPS the obsolete phrase (e.g. rewrites the arm-return branch from scratch) would pass the test (the obsolete phrase is gone). The test is a regression pin, not a structure pin |

---

## Rollback / Abandonment Notes

**If Phase 4 ships and a follow-up rolls back the banner text:**
the regression test (T7) catches the re-introduction loudly.
The rollback is `git revert` of the Phase 4 commit; the
daemon code is unchanged from Phase 3.

**Abandonment (kill-switch):** the same env-var flip as
Phase 1 + Phase 2 + Phase 3
(`ENSEMBLE_POST_RESTART_ARM_NOTIFY=0`) disables the entire
feature. Phase 4 has no source changes to the daemon; the
kill-switch is inherited.

**Code rollback (if a Phase 4 commit lands and is found
broken):** the change is one file modified
(`daemon/tools/upgrade_tools.py`, string literal swap) +
three new files (runbook, drill, test) + one file appended
(`RELEASE_NOTES.md`). A `git revert` of the commit is the
rollback; the daemon code returns to the Phase 3 state
(arm with obsolete banner text); the runbook / drill / test
files are deleted; the release-notes line is removed.

**What does NOT work as a rollback:** deleting the
regression test (T7) while leaving the obsolete banner
text. The test is the load-bearing regression pin; deleting
it is a release-process regression. The correct rollback
is the code revert (or a fix-forward commit that re-rewrites
the banner).

---

## Exit Criterion

**All of the following, objectively verifiable:**

1. **Banner text updated:** T2 + T3 done; the obsolete phrase
   is absent from `upgrade_tools.py`; the new auto-wake
   prose is present in both arm-return branches.
2. **Regression test GREEN:** T4.7 GREEN; a future PR that
   re-introduces the obsolete phrase FAILS the test loudly.
3. **Runbook exists:** `docs/runbooks/post-restart-arm-notify.md`
   is at the documented length (80–150 lines); all six ADRs
   (ADR-039–ADR-044) are referenced; the kill-switch section
   documents the env var; the structured-log section lists the
   log lines; the recovery section documents the abandoned-wake
   + missing-instance flows; the drill section is a one-liner.
4. **Release-notes line appended:** `RELEASE_NOTES.md` v0.17.x
   section has the one-line entry; the cross-reference to the
   runbook is correct.
5. **Drill GREEN:** `bash test/drills/post_restart_arm_notify_drill.sh`
   exits `0` on a sandbox install; the six scenarios (D1–D6)
   each print their expected structured-log outcome; the bash
   exit code is the operator's primary acceptance signal.
6. **Drill smoke unskipped:** T8 GREEN; the Phase 3 placeholder
   smoke now invokes the bash drill and asserts the exit code.
7. **Non-regression:** T9 GREEN; the full Phase 1 + Phase 2 +
   Phase 3 packs pass byte-exact; the existing 30/30
   `test_upgrade_journal.py` + 135/135 `test_upgrade_tools.py`
   + 243/243 `test_release_journal.sh` all pass.
8. **Live untouched:** T10 done; live pids verified unchanged
   at every checkpoint (the drill's bash log records the
   checkpoint).

The Phase 4 commit is the **feature-complete** commit. The
feature is shippable on demo when 1–8 are green. The
**release cut** (a follow-up PR that bumps the version
and pushes the tag) is the deploy-side concern; this feature
is the dev-side completion.
