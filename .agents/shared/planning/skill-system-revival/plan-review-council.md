# Plan Review Council — skill-system-revival Phase 1

**Reviewer role:** councilor "agentic" (owner of this file). Independent peer review = `plan-review-council-peer.md`.
**Target:** `.agents/shared/planning/skill-system-revival/phase1-plan.md`
**Reviewed snapshot:** sha256 `98851b94351d2953e7f04c11634b9f050412356c8ca9c1e7d5bc9f26d149fca7`, **957 lines**, mtime `2026-11-09T08:58:23Z`.
**Companion reviewed:** `decisions.md` (sha256 `69ecff7f…`, 563 lines).
**Base:** worktree `/home/nea/ensemble-src-wt-skill-revival-phase1` @ `ae5bbafed` (CPython 3.14.7). Every anchor below was checked in THIS worktree.

> **Review-integrity note (F-27).** The plan changed under review (it was 902 lines / no P9 / an unreframed A4 when this review began; it was 955 and then 957 lines by 08:58Z). All findings are pinned to the snapshot above. If the plan has moved again, re-run the specific checks — several findings (F-1, F-5, F-2) are on sections that were actively edited during the review window.
>
> **Re-check at file close:** the on-disk plan had moved again to 968 lines / sha256 `31d6ce51332780cc8a16a4d8eaddd1e41838f0c649a3cf73d73dd1fe4bebd234` (08:59Z). I re-verified the two verdict-driving findings against that version: **A3 still repoints the gate to `applied == True`** (`has_used_skill_for_instance`, line 173) and **E4's acceptance #2 still requires the R1 set empty** (line 533) while the guard still skips unresolved groups (line 487). Both CRITICALs stand. Line numbers below refer to the 957-line snapshot; in the 968-line version section bodies after line 154 are shifted by a few lines.

---

## VERDICT: **REJECT**

Not a style rejection. Three independent grounds:

1. **A3's core premise is factually wrong against the worktree code.** The column it proposes to gate on (`applied`) is a monotonic derivative of the column it proposes to replace (`feedback_applied`). The "fix" is a no-op, its acceptance criterion #2 is already true today (so it proves nothing), and its mutation leg M-A3a cannot turn RED. A workstream that ships vacuous pins and cannot demonstrate its own acceptance must not pass.
2. **E4's primary deliverable is internally unsatisfiable.** R1 is *defined* as active rows in an **unresolved** A/B group; the plan's safety guard says E4.1 must **skip** unresolved groups; acceptance #2 then requires the R1 set to be **empty** after E4.1. Three statements cannot all hold.
3. **Two required release-gate items are missing/under-specified** in ways that would fail a real staging run: `rollback_safe` (the derivation grep is full-history — 103 of 126 migration files match — so `stage.sh` refuses with exit 78 unless `ENSEMBLE_ROLLBACK_SAFE` is explicit, and the plan's staging steps never set it) and the PROMPT-BASELINE both-trees tester gate (the plan implicitly relies on `stage.sh`'s prompt-drift leg, which is non-blocking by design at `stage.sh:671-716`).

Everything else is fixable. A1, A2, P4/E4's vehicle ruling, the net-new-reds protocol, G's root cause, and the hard-constraint discipline are genuinely strong. After F-1, F-2, F-8/F-9 and F-13..F-16 are repaired this is an APPROVE-WITH-FINDINGS plan.

---

## THE `applied` RECONCILIATION (decides the verdict) — answered explicitly

**Question:** is `applied` a mirror of `feedback_applied` (peer councilor), or an independent "genuinely used" signal (A3's premise), or neither?

**Answer: `applied` is a monotonic, one-way projection of `feedback_applied`, and therefore A3's premise is INCORRECT. A3 is correct that a defect exists only in its framing — not in the gate.**

How `applied` is actually populated (prose, no code blocks):

1. **Insert paths.** `SkillRepository.create` (`daemon/repositories/skill/repository.py:972-999`) writes whatever `applied` the caller passes. Three callers exist: the completion hook in `skill_metrics_service.py:871` passes `applied=False`; the SUPERSEDED marker path (`skill_metrics_service.py:1656`) passes `applied=False`; the feedback on-miss path (`skill_metrics_service.py:1051`) passes `applied=bool(applied_bool)` — i.e. it already writes `True` when the feedback call said applied.
2. **The feedback update.** `SkillRepository.update_feedback` (`repository.py:1302-1338`) first stamps `record.feedback_applied = applied`, then **mirrors only the positive direction**: when the feedback argument is truthy it sets `record.applied = True`, and it is explicitly documented and implemented as monotonic ("never reset to False on later updates"). A negative or `None` feedback therefore never writes `applied`.
3. **The completion hook does NOT set `applied`.** `SkillRepository.update_completion` (`repository.py:1339-1436`) writes `task_succeeded`, `iterations`, `duration_seconds`, `task_message` — and nothing else. The lifecycle sketched in the model docstring (`daemon/repositories/skill/models.py:311-315`) advertises an `update(applied=True, …)` step at task completion; **that writer does not exist.** This is the real (undocumented) gap.
4. **No other writer.** A full grep of `daemon/` for assignments to `.applied` / `applied=True` returns only the two insert sites above and `repository.py:1317` (the mirror). `skill_metrics_service.py:1135` calls `update_feedback(applied=True)`, which is the mirror again.

**Consequence — and the mechanical proof.** `applied == True` ⟺ *some feedback call for that row passed `applied=True`*. The census the plan itself quotes proves it: `applied` TRUE = **6,095**, `feedback_applied` TRUE = **6,091** — a 4-row delta, exactly the population where feedback flipped True→False after an earlier True (the monotonic mirror keeps `applied` True). The NULL asymmetry that the peer/census dispute turns on (0 NULLs vs 3,547 NULLs) is fully explained by the insert default: `applied` is a non-nullable boolean defaulting to False, while `feedback_applied` is nullable and stays NULL until feedback lands. A "true mirror" would not copy a NULL into a non-null column; the insert's `False` is that copy.

**So:** A3's test *"∃ a usage row with applied == True (the skill was genuinely used)"* cannot be distinguished from today's test. Repointing `has_applied_for_instance` → `has_used_skill_for_instance(applied==True)` changes at most 4 rows. The code's own comment at `skill_metrics_service.py:641-645` even states that gating on `feedback_applied` is deliberate ("a skill may have been offered to the agent without being consumed"), and the 3,457 `selected AND task_succeeded AND feedback_applied IS NULL` rows *should* be capture-eligible under that stated intent — which is exactly what the current gate already does.

**Verdict on A3: premise incorrect, fix a no-op, pins vacuous (F-1).** If the Emperor wants "genuinely used" attribution, the change is a *new* writer (completion hook sets `applied=True` when the skill was in context), with its own blast-radius census — not a gate repoint.

---

## Findings

Severity legend: **CRITICAL** = blocks the plan's own exit criteria or ships a vacuous proof; **MAJOR** = must be repaired before the plan is executable/verifiable; **MINOR** = accuracy/clarity/consistency.

### CRITICAL

**F-1 — A3: wrong premise, no-op fix, vacuous pins.**
*Section:* `phase1-plan.md` §A3 lines 154-183 (ruling 165-171; acceptance 174-177; pins 179-183); §18 rows 8-10 (lines 924-926).
*Defect:* The ruling asserts `applied` = "actually loaded into the context / genuinely used". In the worktree it is a monotonic one-way mirror of `feedback_applied` (evidence and the 4-row arithmetic in the section above). Therefore:
- the repoint is a near-no-op (≤4 rows);
- acceptance #2 ("`selected+task_succeeded+feedback_applied NULL` is capture-eligible; `applied=True` is not") is **already true today**, so it demonstrates no change;
- **M-A3a is vacuous** — reverting to `has_applied_for_instance` cannot turn the new pin RED because both predicates select the same set. The plan's own §A5 non-vacuity bar ("a pin that stays green with the guard removed is vacuous and worthless") disqualifies it.
- The added census paragraph (lines 157-159) *contradicts* the ruling: it says the fix is "honor `feedback_applied` … so those 3,457 stop counting as genuinely-evolved", while the ruling says such rows **should** stay eligible.
*Remediation (pick one, state it):* (a) **Drop A3's code change**; restate it as a metric/label defect only ("the 3,457 is a mis-framed figure, not a false-eligible one") and pin the label; or (b) implement the **missing writer** — completion hook sets `applied=True` when the skill was in context — as its own ruling, with a census of the blast radius on the capture gate and `total_applied` semantics, then repoint. Either way, delete M-A3a and re-derive the acceptance so it fails on the *pre-fix* tree.

**F-2 — E4: R1 is unsatisfiable against its own A/B guard.**
*Section:* §E4 line 484 (guard), line 504-509 (R1 rule), acceptance line 530 (#2).
*Defect:* R1 = "two or more rows sharing `(project_id, name)` with `is_active=true` and a shared `ab_test_group`, **never A/B-resolved** (`skill_ab_tests.resolved_at IS NULL`)". The guard = "E4.1 must join against `skill_ab_tests` and **skip any row whose group is unresolved**". Acceptance #2 = "After E4.1 runs … the R1 set is **empty**". E4.1 therefore skips exactly the rows it is supposed to retire, and the canonical target (`regression-ab-partition-run`, group `2600329e-…`, unresolved) is never deactivated. The "E4 before F" edge and the registry-convergence claim (N5/"stale retired") rest on this.
*Evidence:* `skill_ab_tests.resolved_at` exists (`daemon/repositories/skill/models.py:624-650`); `SkillRepository.deactivate` correctly sets both columns (`repository.py:383-396`).
*Remediation:* split the rule. Either (a) **R1′ = same-name active shadowing with a RESOLVED group** (auto-retirable), and the unresolved abandoned groups become an explicit **Phase-2 A/B disposition** (consistent with DS-34) with the E4.3 injection filter as the interim safety property; or (b) keep unresolved groups in scope but make deactivation an **operator-dispositioned** act (E4.2 only, with a receipt), never a boot-time automatic sweep. Then rewrite acceptance #2 to match whichever rule survives, and add a pin for the guard itself (skip-unresolved must be RED if removed).

**F-3 — Anchor misidentification in P5 / B3 / §17: `:1114` and `:1518` are write_file and edit_file, not glob_files and grep_files.**
*Section:* §3 P5 line 59; §B3 lines 296-306; §17 item 3 line ~890. (§18 row 16, line 932, cites the CORRECT lines `197-225`, `596`, `670`.)
*Defect:* The plan states "`glob_files` (`:1114`) and `grep_files` (`:1518`) already resolve through `_resolve_search_root` (`:252-283`)". In the worktree: `glob_files` is defined at `filesystem.py:754` and calls `_resolve_search_root` at `:764`; `grep_files` is at `:1309` and calls it at `:1322`; **`:1114` is `write_file` and `:1518` is `edit_file`, and both call `_resolve_within_workdir`** (i.e. they still honour the absolute-path skip). The disclosure section — the plan's own credibility artifact ("Every file:line below was opened in the worktree") — contains the error, and it points at the write tools, which is materially the wrong direction for a read-scope hardening.
*Remediation:* correct §P5, §B3 and §17 item 3 to `:754/:764` and `:1309/:1322`. The substantive conclusion ("2 open read call sites") is right; only the anchors are wrong.

### MAJOR

**F-4 — A4: the described fix misstates the on-miss insert and would suppress the first legitimate bump.**
*Section:* §A4 lines 196-198; §18 row 11.
*Defect:* "The on-miss insert path (`:1030-1111`) inserts with `applied=False` then updates" is false: the on-miss insert at `skill_metrics_service.py:1051` passes `applied=bool(applied_bool)`; it writes `True` when the feedback said applied, then calls `update_feedback(applied=True)`. An implementer following the text literally reads `prev_applied=True` from the freshly-inserted row and skips the bump — so the *first-ever* feedback for a skill (which is precisely the on-miss case) never bumps `total_applied`. The transition approach is right; the stated mechanics are wrong.
*Remediation:* specify the transition source explicitly — read `prev_applied` from the pre-call lookup only, and treat the on-miss branch as `prev_applied=False` (one bump). Add a pin leg covering the on-miss path (currently no leg does).

**F-5 — A4: the rewritten exit criterion is a dangling cross-reference, and §16.1 C-2 still asserts the old one.**
*Section:* §A4 line 202 ("The proposed wording is in §12's 'exit criterion' and §13's gate table"); §12 (lines 648-693) contains no exit criterion; §13.1/§13.3 (697-783) contain no A4-specific exit criterion; §16.1 C-2 (line ~863) still says "the convergence criterion (0 mismatches) is unchanged"; §1 Goal 1 (line 25) still frames the defect as "+480 away from its own ledger. Phase 1 makes each of these true".
*Defect:* The plan tells the tester to inherit a criterion that is not written anywhere. The commission's "converge to 0 mismatches" (global name gauge) is therefore still the de-facto criterion in three places, while §A4 retires it — exactly the "silently keeps the commission's wording" failure the census addendum calls CRITICAL.
*Remediation:* write the corrected criterion verbatim into §13.1's Gate 3 exit criterion row (and §1 Goal 1 + DoD), and re-point C-2 to it.

**F-6 — A4: acceptance 3-6 are not demonstrable in Phase 1, yet the Definition of Done claims them.**
*Section:* §1 DoD (lines 31-33, "(a) metrics verdicts are true"), §A4 acceptance 2-6 (lines 215-221), §14.2.
*Defect:* acceptance requires (i) a `ensemble_prod` measurement the lane is told not to take, (ii) a receipted counter repair that is an operator act **after** promote (§4 forbids DB writes from this lane), and (iii) two measurements ≥24 h apart with the fix live. None can exist at "staged release" time. The DoD's "(a) metrics verdicts are true" is therefore unachievable in Phase 1 as written.
*Remediation:* scope A4's Phase-1 acceptance to "code + dry-run script shipped, dry-run output attached, redefinition pinned"; move the convergence proof into the Phase-2 runbook's go/no-go (it already has a slot) and correct the DoD.

**F-7 — A4: the gauge being fixed is an unanchored FIND-IT item, and it has no row in the change list.**
*Section:* §A4 line 207; §18 has no row for the gauge site.
*Defect:* The plan proposes to redefine "the gauge", but the code computing it has not been located ("bespoke computation somewhere"). No file, no line, no test surface. A fix and its mutation legs cannot be specified against an unlocated site; acceptance 2/3 depend on it.
*Remediation:* locate it before the plan gate (the suggested grep is a start; also check the metrics-query wiring families and any skills-stats tool/API), add the anchor + a §18 row, or drop the redefinition from Phase 1 and keep only the leak fix (§A4(i)).

**F-8 — E1 has no acceptance criteria and no mutation legs (E2 likewise).**
*Section:* §E1 lines 412-437 (no Acceptance, no pins block); §E2 lines 438-446 (same). §13.3 line 755 lists `skill_capture_burst_guard_unit_test` NEW for E1 — a pack with no specified contents.
*Defect:* House rule and the plan's own §4 ("every pin lives in a REGISTERED pack with a mutation leg"). Every other workstream carries both blocks; E1 and E2 do not. E1 is one of the six commissioned workstreams.
*Remediation:* add Acceptance + M-E1a..n (at minimum: cap refusal envelope truthfulness; cap fires at N; cap survives restart; anomaly row written; `skill_analyze` stays ungated) and E2's unit-level acceptance + pack.

**F-9 — E1: the durable cap cannot count what it claims to count.**
*Section:* §E1 lines 424-426; §18 row 13 (`count_fix_admissions_since(idempotency_key, since_iso)`).
*Defect:* The design uses `job_items.idempotency_key = "skill_fix:<project>:<skill>"` as a durable admission ledger and counts rows with that key. But (a) the partial unique index `idx_job_idempotency` (`job_queue/models.py:300-306`) permits **one** live row per key, and (b) when the existing row is terminal, `JobQueueService` explicitly re-inserts with a **suffixed synthetic key** `<key>#<uuid8>` (`daemon/services/job_queue_service.py:1596-1608`). Re-admissions — the only thing the cap needs to count — therefore carry non-matching keys, and an exact-key count stays at 1 and never trips.
*Remediation:* count by a stable non-key field (e.g. `job_type='evolution' AND job_metadata->>'skill_id'`, or a dedicated admission column/metadata stamp), or count by prefix with an explicit collision analysis; state the equivalence to `(project, skill)` in the pin.

**F-10 — D: the retention design is undetermined, but the release gate assumes it is decided.**
*Section:* §D4 line 398 ("new table … or a structured-log sink is an implementation choice the plan leaves to the implementer"); §14.2 step 5 line 797 (migration census = "no-diff assertion"); §18 "Not touched: … any migration `.sql` file".
*Defect:* If the implementer chooses a new durable table (a perfectly reasonable reading of "≥30 days, read side must not require a restart"), the no-diff migration census is false and the house rule (own `.sql` + idempotent `_ensure_postgres_columns` statement) applies — which the plan also says the release must not do. The plan cannot both leave it open and gate on one branch.
*Remediation:* decide it in the plan (recommend: structured-log sink / existing observability surface for Phase 1, table deferred) and make §14.2/§18 consistent.

**F-11 — D: the load-bearing half (daemon-side deviation detector) has no producer or read site.**
*Section:* §D3 ruling 1 (lines 386-388), D2 table row "Keeper deviation detection" (line ~377), acceptance line 400 ("Every D-signal has a named producer site, a named read site").
*Defect:* The detector is the part that would actually have caught Oct-22 (DS-17). The plan says it "reads tool-call records" but names no store, no query, no hook. Its acceptance is therefore unverifiable, and M-D2 ("make the auto-anomaly trigger a no-op → RED") tests a trigger that has no defined input.
*Remediation:* anchor the record source (event rows? instance metadata? job metadata?) and the detection rule (what constitutes "outside the sanctioned surface"), plus the fold-to-category mapping (`queue-scheduling`/`tool-failure`/`other`).

**F-12 — F: the claim that `skill_injection: false` does not prevent serving is wrong for the dynamic lane.**
*Section:* §10 line 554 (and it is load-bearing for the "per-axis manifest" framing at lines 558-561).
*Defect:* `skill_injection` **does** gate the dynamic per-turn injection block: `daemon/services/instance_messaging.py:4311-4312` (the block runs only when the resolved meta's flag is truthy) and `daemon/services/context_messages.py:2536-2541`. What the flag does **not** gate is (a) the auto-load skills block (`_build_auto_load_block`, `context_messages.py:2526-2535`, called without a flag check) and (b) the explicit `skill_search` tool. So a served skill on a `skill_injection: false` agent is explained by the auto-load/`skill_search` lanes, not by "the flag does not gate injection".
*Remediation:* restate precisely (flag gates automatic dynamic injection + automatic capture; does NOT gate auto-load, explicit search, or `skill_create`), and re-derive the "injection-enabled" axis from that.

**F-13 — `rollback_safe` is mis-specified, and the staging steps omit the override the derivation requires.**
*Section:* §14.2 step 8 line 800 (and the staging steps 791-806 give no stage command).
*Defect:* `rollback_safe` is a statement about the **migration delta**, and `stage.sh`'s derivation is a **full-history** destructive-DDL grep (`scripts/upgrade/stage.sh:59-60`, `:325-349`): at this tree **103 of 126** files in `daemon/migrations/versions/` match `DROP TABLE|DROP COLUMN`, so `CONTAINS_CONTRACT_PHASE=true` and stage refuses with **exit 78** unless `ENSEMBLE_ROLLBACK_SAFE` is set explicitly. The plan says "source explicit" but never shows `ENSEMBLE_ROLLBACK_SAFE=1` in any command and justifies it with "no schema change, no data-shape change" — which is not the semantics and is also not quite true (the release ships data-mutating operator scripts for A4/E4).
*Remediation:* state the semantics ("the delta between the previous release tag and this version is empty"), state that the override is required **even at zero delta** because the grep is full-history, and put the env var in the stage command.

**F-14 — PROMPT-BASELINE gate is under-specified: no second tree, no owner, and it implicitly trusts a non-blocking leg.**
*Section:* §14.2 step 6 line 798.
*Defect:* `stage.sh`'s prompt-artifact drift leg is explicitly **non-blocking** (`scripts/upgrade/stage.sh:671-716`: "drift is VISIBLE at stage … NEVER gates staging"). The required gate is a **TESTER-side** check of **181/181 in BOTH trees** (staged + source/feature). The plan asserts the baseline stays 181 but names neither the owner nor the second tree, so a drifted tree would stage clean.
*Remediation:* add the tester-owned both-tree 181/181 check to Gate 4 (§14.4) and note the non-blocking stage leg as the reason.

**F-15 — The FE provenance-sidecar gate item and the `build_tool` label deviation are missing.**
*Section:* §14.2 step 3, §14.4 (no mention), §17 (no disclosure).
*Defect:* The required staged-release gate includes: the sidecar set with the FE sidecar read from the **source** tree while its **absence** from the release payload is asserted as correct (`stage.sh:418-424` — the rm happens after the manifest map is computed, and the verifier reads the source tree); and the FE sidecar's `build_tool` label is a hardcoded literal naming a non-existent npm script — a deviation that must be disclosed and must **not** be used as a gate condition. The plan carries none of this: no sidecar-set assertion, no absence assertion, no label disclosure.
*Remediation:* add all three to §14.4 + §17.

**F-16 — E4.1 (the shipped boot-time reconciliation) has no wiring site.**
*Section:* §E4 lines 496-500; §18 row 25 (creates `daemon/services/skill_stale_reconciliation.py`) — and no row invokes it.
*Defect:* A new module that nothing calls never self-heals. The plan also says it must complete "before any injection can occur" but never states where that ordering lives (boot sequence in `daemon/api.py`? a manager call?). The E4-before-F guarantee silently depends on it.
*Remediation:* add the wiring row + the boot-ordering statement (and a pin proving the reconciliation runs before the first injection-capable path).

**F-17 — B3: "two open call sites" vs "in `_resolve_within_workdir`" — the scope (and the write path) is ambiguous.**
*Section:* §B3 lines 300-306; §18 row 16 (edits `197-225` = the shared resolver); M-B3a line ~310 ("remove the scope check from `_resolve_within_workdir`").
*Defect:* If the check lives in the shared resolver, it also governs `write_file` (`:1114`) and `edit_file` (`:1518`) for any agent that declares a scope — but no acceptance covers write behavior, and acceptance #2 only digests "the 4 read tools". For the keeper the write tools are denied, so the Phase-1 blast radius is small, but the plan must say whether writes are in or out of scope for the opt-in feature.
*Remediation:* pick one (recommend: apply at the shared resolver but state it explicitly as read+write scoping and extend the acceptance digest to the 2 write tools; or add the check at the two read call sites only).

### MINOR

**F-18 — E2 has no acceptance criteria and no pack** (§E2 438-446); its unit-level claims ("verified in a unit/pack environment") name no pack. Add them.

**F-19 — Migration census: method and ownership are missing** (§14.2 step 5, line 797). The required form is a tester-owned `CArchiveReader` TOC read of the frozen binary (raw `grep -a` is blind on these builds), with full `YYYYMMDD_HHMMSS` prefixes and `uniq -d` empty. A source-tree "no-diff" assertion is a defensible substitute **only if** the plan states the staged tree is byte-identical to the feature tree — say so, or add the TOC step.

**F-20 — 4-way sha lacks the independence requirement** (§14.2 step 4, line 796). Say that the sha is recomputed independently and that the build-time sidecar is the independent witness because the manifest sha is computed *from* the copy.

**F-21 — C's "exact, by ID" read names a non-existent column** (§7 line 330: `SELECT id, name, is_paused FROM job_queues WHERE id = …`). The PK is `queue_id` (`job_queue/models.py:201`; `20260409_000001_add_job_queues_table.sql:14`), and C1 in the table (line 335) is correct. Delete the wrong statement so the runbook's primary kill-switch read cannot error.

**F-22 — D's vocabulary anchor is wrong** (§D3 line 386 cites `anomaly_service.py:26-38`; those lines are fence documentation. The closed vocabulary is `daemon/services/metrics_query/wiring/vocab.py:92` (`ANOMALY_CATEGORIES`), imported at `anomaly_service.py:147`).

**F-23 — E4 contains a duplicated block** (§E4: the "Corrected target set (P2)" list + "HARD RULE" paragraph appears twice verbatim, lines ~486-495 and ~510-519). An editing artifact; remove one copy.

**F-24 — Dangling cross-references in §2**: N1's enforcement points at "§13 release gate" (the gate lives in §14.4); N4's points at "§12 gate" (§12 is sequencing only; no canonical-tree gate exists in the plan). Add the actual gate lines or drop the references.

**F-25 — E3's Acceptance still bundles NOT-from-board items** (§E3 line 467 requires "the P7 refusal text is identical across the three sites; `to_summary()` has either a caller or is gone") although the same section (lines 447-457) correctly labels those leader-judgment/NOT-from-board. Move them out of the board acceptance so the "3 real bullets" closure is falsifiable on its own.

**F-26 — §16.2 T-1 is stale**: it still says the board body "may land later", while §E3 and P9 both say the body is now landed with exactly three bullets. Fold T-1 into P9 (closed) or restate it as "re-verify at implementation start".

**F-27 — Process: the plan was edited during the review** (902→955→957 lines; A4 rewritten, P9 added, census facts folded). Pinned to sha `98851b94…`; any further edit invalidates the specific line numbers above and must be re-diffed against this review.

---

## Census addendum — reconciliation of the five census facts

| # | Census item | Verdict against the reviewed snapshot |
|---|---|---|
| 1 | **A4's target is wrong** (`skills.name` intentionally non-unique; global-name divergence unsatisfiable; dedupe catastrophic) | **Satisfied in §A4** — the plan reframes the gauge to within-identity-key divergence, forbids dedupe, and retires the global gauge. Residual defects: the rewritten exit criterion is not actually written (F-5), the gauge site is unlocated (F-7), and §1/DoD still carry the old framing (F-5/F-6). |
| 2 | **A3 is now exact — pin the real predicate** (`selected ∧ task_succeeded ∧ feedback_applied IS NULL` ≈ 3,457) | **Not satisfied — this is the verdict-deciding finding (F-1).** The plan adopts the census *count* but not the census *implication*: it still repoints to `applied`, which is the same signal (see the reconciliation section). The exact window is correctly quoted; the fix built on it is a no-op. |
| 3 | **E3's scope is smaller than the commission claims** (3 real bullets; 3 absent items) | **Satisfied** — P9 (line 65) and §E3 (447-476) encode the three bullets, label the other three NOT-from-board, and note the display-only / non-promote-blocking status. Residual: F-25 (acceptance still bundles them). |
| 4 | **E4's rows and lever** (dual columns that can desync; no tombstone; 6 captured-origin already retired; 52 `ab_testing` active rows; `resolved_at IS NULL` guard; 116/116-FIX unverifiable) | **Mostly satisfied; one CRITICAL residual.** E4 correctly cites the dual-column desync, the sanctioned writer, the 6-row no-op, the 52-row hazard, the receipt-as-prior-state reversal, and E2 now states the 116/116 premise is unverified (no `skill_evolution` table — confirmed: only `skills`, `skill_lineage`, `skill_usage_records`, `skill_triggers`, `skill_embeddings`, `skill_ab_tests`, `skill_bank` exist). The residual is F-2: the guard the census asked for is exactly what makes acceptance #2 unsatisfiable. |
| 5 | **Workstream C — resolve the brake by ID, do not carry a name-LIKE uncertainty** | **Satisfied** — §C (lines 326-355) already resolves `bd075f0b` by the PK (`queue_id`) and explicitly explains the census's name-filter artifact; no "row may not exist" uncertainty is carried into the runbook. Only the prose `id` slip remains (F-21). The addendum's suggested `WHERE id = …` would itself be wrong: the column is `queue_id`. |

---

## What the plan gets right (credit where due)

- **Premise corrections are genuinely encoded, not decorative.** P1's roster re-derives exactly (`agents/*/meta.json`: 15 true / 10 false / 25 declaring / 46 agents) — I re-derived it independently in the worktree and got the identical list. P2's "rule-based, never an id list" and the "identify-before-deactivate" hard rule are the right shape for a DB-drift problem.
- **A1 is exemplary.** The ruling (wedge-capture *process fact*, not a switch), the late-binding mechanism (mirroring the existing `publisher_cadence` callable), the refusal direction preserved, and mutation leg M-A1d (the fact must track a watchdog that starts *after* construction) together make the late-binding requirement non-vacuous. The anchors (`api.py:2571-2576`, `:3236-3258`, `provider.py:67/75/147`, `availability.py:61-62`) all verify.
- **A2 is correct and elegantly argued.** The polarity fix at `CensusSampler.gated` adds no check site (the `metrics_enabled()` census stays at 2 — verified by the existing test's grep), and the "no existing pin flips" claim is TRUE: the `_FakeCensus` stand-ins already encode the corrected contract (`tests/unit/metrics/test_metrics_query_switch_invariance.py:48-51`, `tests/unit/metrics/test_metrics_query_provider.py:319-327`). The R-i/R-ii swap is real (code comment says R-i, disclosure assigns R-ii to census polarity — verified in both files).
- **E4's vehicle ruling is provably right.** The runner is a documented no-op on PostgreSQL (`daemon/migrations/runner.py:837-842`), and a fresh SQLite boot dies at `20260714_000001` (`ALTER TABLE … DROP CONSTRAINT IF EXISTS` at `:35`, with a false DUAL-DRIVER claim at `:24-27`). I reproduced both facts from the tree. "No `.sql` file" is the correct conclusion.
- **The net-new-reds protocol is complete and verbatim**: `grep '^FAILED'` only, ≥3 runs, set-diff, TRUE base = **intersection** (3 reds), NET-NEW = head − base(3) − named quarantine, must be 0, base debt disclosed. It even explains *why* (the base is unstable: 4/455, 3/456, 4/455). This is the strongest part of the document.
- **G's diagnosis and acceptance context are rigorous.** The StaticPool single-connection + `asyncio.to_thread` fan-out mechanism is anchored (`tests/services/conftest.py:39-46`, `skill_evolution_service.py:1448-1457`, `:1474-1483`, `repository.py:331-336`), the decisive experiment is stated, the fix is fixture-level with the correct `:memory:`+`NullPool` caveat, and the acceptance is *context-matched* to the 38% full-file-set rate rather than a lucky 15% isolated green.
- **Hard constraints are respected**: no switch flips, no promote, no brake engagement, no canonical-dir work; E4-before-F and G-before-E1 are explicit hard edges with a stated non-interference argument; `scripts/upgrade/**` is untouched.
- **B's exit criterion IS achievable in Phase 1** (the architect's concern, answered from the code): B1's behavioural proof is tool-layer (resolver + factory), independent of the capture switch; B2's lane gate is exercised with a unit-level lane override. The plan should say that explicitly, but nothing about it is infeasible.

---

## Open questions for the dispatcher / architect

1. **A3 direction (blocks APPROVE).** Does the Emperor want "attributed = feedback says applied" (current, documented behaviour — then A3 becomes a metric-label fix) or "attributed = the skill was in context when the task ran" (then the `update_completion` writer must be built, with its own census)? The plan cannot choose both.
2. **E4 vs the A/B backlog (blocks APPROVE).** Does the unresolved `regression-ab-partition-run` pair get auto-retired (product decision — requires picking a winner or voiding the experiment) or does it stay active until the Phase-2 disposition, with only the E4.3 injection filter as protection? DS-34 currently says the latter; R1/acceptance #2 currently say the former.
3. **D's retention substrate.** Log sink (no schema change, keeps the no-diff census) or table (schema change, breaks it)? This one decision flips a release-gate item.
4. **E1's cap substrate.** Is a dedicated admission stamp acceptable in `job_items` metadata, or does E1 need a purpose-built ledger row? The current key-based count cannot work (F-9).

---

## Council cross-check (2-model)

1. **CONVERGENCE.** The second councilor reviewed the same plan independently (`plan-review-council-peer.md`). Its findings substantially match mine: its F-2 = my F-15 (FE provenance sidecar omitted from the staged-release gate); its F-3 = my F-3 (`:1114`/`:1518` are `write_file`/`edit_file`; real 754/764, 1309/1322); its F-6 = my F-1 (A3's provenance premise is code-answerable, and the code answers against it). **The REJECT verdict therefore rests on two independent models, not one.**

2. **DIVERGENCE (wording-level only — identical verdict; NOT a substantive split about the fix).** *My position:* `applied` is a monotonic mirror of "some feedback passed applied=True", so A3's repoint is a ≤4-row no-op and M-A3a cannot go RED. *Peer's final position:* not literally a no-op — the two predicates differ on the set where applied=True and feedback_applied=False (census: applied TRUE 6,095 vs feedback_applied TRUE 6,091 = 4 rows) — but the peer agrees A3's predicate does not touch the 3,457-row window. *Shared operational conclusion either way:* A3 fails to address the census window (`selected AND task_succeeded AND feedback_applied IS NULL` = 3,457 rows, which carry applied=False); acceptance #2 is wrong as written; M-A3a must be rewritten against the corrected predicate **or** the acceptance restated as a row-count change (3,457 eligible → 0). No reader should mistake this for disagreement: same verdict, same remedy, different wording.

3. **CORRECTION TO THE CONVENING AUTHORITY.** The census addendum directed a check of Workstream C against a suggested `WHERE id = '...'` read. My finding that this is WRONG is accepted: the real primary key is `queue_id`, and the plan already resolves the brake correctly by `queue_id` (§C, `job_queue/models.py:201`). **Census item 5 required NO change to the plan, and the addendum's suggested remedy is withdrawn as itself incorrect.** Recorded for trust: the convening input contained an error that the council caught.

4. **RECORDED SELF-CORRECTION (provenance honesty).** The peer's first pass claimed `applied` was a literal mirror of `feedback_applied` and that A3 was a pure no-op on those grounds. After reconciling against the census it withdrew that claim — a true mirror would carry feedback_applied's 3,547 NULLs, whereas `applied` is 0-NULL by non-null insert default — and restated the defect as a predicate mismatch. Noted as a self-correction so the artifact's provenance is honest rather than appearing as a first-pass consensus.

## RE-CHECK (rev 4) — focused adversarial verification (2026-11-09)

**Mode:** focused re-check — 2 targeted workers, no council re-run (commission instruction).
**Target snapshot (pinned):** `phase1-plan.md` rev 4 — 1043 lines, sha256 `1b7604bc513ed9a9db6f6cf27d843f72f04b54085e69c17eb939db02f4bd38bf`, mtime 2026-11-09T09:25:05Z — stable for the whole pass; the council's F-27 moving-target hazard did not recur.
**Companions:** `decisions.md` 712 lines `45293fad…` · `architecture-recommendation.md` 248 lines `f031841f…`.
**Workers:** `review-worker-recheck-closure` (plan-review; plan-text closure + regression) · `review-worker-recheck-rulings` (code-review; anchor sweep + four leader rulings).
**Adversarial premise:** rev 4 carries a remediation ledger (§19) whose "FIXED" column is itself under test — several rows assert closure while the cited section bodies still contain the defective text. BODY TEXT GOVERNS.

### NEW VERDICT: **REJECT** — the council's bar is not met (narrow and mechanically fixable)

Bar (the council's own): repair F-1, F-2, F-8/F-9, F-13..F-16 → APPROVE-WITH-FINDINGS.
Result: **F-2 CLOSED.** F-1, F-8, F-9, F-13, F-14, F-15 STILL-OPEN. F-3 STILL-OPEN. F-16 UNVERIFIED. Ruling D carries 2 open holes. §7 kill-switch read STILL-OPEN.
One criterion met, six unmet → REJECT stands. No DESIGN defect was found in rev 4: every open item is a body-text edit, and the section designs (A1/A2/A4/B/C/D/E mechanism) verified sound.

### A — reject-driving findings

| ID | Item | Status | Evidence (plan line + code) |
|----|------|--------|---------------------------|
| F-1 | A3 gate must move onto `feedback_applied`; acceptance = measured 3457→0; M-A3a must MOVE that count | **STILL-OPEN** | §5 A3 L173 "`has_used_skill_for_instance(instance_id)` → `∃ SkillUsageRecord WHERE instance_id = ? AND applied == True`"; §18 row 8 L966 same; M-A3a still reverts to `has_applied_for_instance` (repository.py:1550-1573, itself gated on `feedback_applied == True`); acceptance #2 already true today. Two workers, same verdict. |
| F-2 | E4 unsatisfiable triad | **CLOSED** | §9 E4 L519/L544-546 — R1 reclassified as a REFUSED set, receipt enumerates R1 (`08a4332f`, `fd1e01dc`) + the test id, R1 verified still active; no surviving "R1 empty"; M-E4a–d present. |
| F-8 | E1/E2 acceptance criteria + mutation legs; §13.3 pack non-empty | **STILL-OPEN** | §9 E1 L427 + §18 row 13 L971; E1 has no Acceptance block; E2 names no pack; §13.3's pack reference unsatisfied. |
| F-9 | E1 cap must key on skill identity within a window | **STILL-OPEN** | §9 E1 L427 `count_fix_admissions_since(idempotency_key, since_iso)`; §18 row 13 same — `idempotency_key` is unique by construction (`#<uuid8>` suffix, job_queue_service.py:1596-1608; unique index job_queue/models.py:300-306) ⇒ the cap can NEVER trip. Dead guard. |
| F-13 | `ENSEMBLE_ROLLBACK_SAFE=1` inside the staging command + delta-empty rationale | **STILL-OPEN** | §14.2 L823-828 contains neither; prose adjudication only. |
| F-14 | PROMPT-BASELINE tester-owned BOTH-TREE 181/181 | **STILL-OPEN** | §14.2 L826 relies on the generator `--check` exit 0, not a two-tree tester gate. |
| F-15 | FE provenance sidecar present-from-source / absent-from-payload + `build_tool` hardcoded-literal deviation disclosed, NOT gated on "npm run build" | **STILL-OPEN** | absent from §14.2. |
| F-16 | E4.1 wiring / boot-order site | **UNVERIFIED** | outside both workers' verified scope. |
| F-3 | B3 + §17 anchors → 754/764, 1309/1322 | **STILL-OPEN** | §6 B3 L303 and §17 #1 L922 still cite `:1114` (inside `write_file`, def :1107) and `:1518` (inside `edit_file`, def :1510); real: `glob_files` def filesystem.py:754 → `_resolve_search_root` call :764; `grep_files` def :1309 → call :1322. The corrected anchors exist as code facts but were never adopted in the body. |
| F-17 | B3 write-path scope | **CLOSED** | §6 B3 L301-306 + §17 #26 L950 + §18 row 16 agree — fence lives in `_resolve_within_workdir` (filesystem.py:197-225), two read call sites only (`:596` list_directory, `:670` read_file), writes out of scope. |

### B — four leader rulings

| Ruling | Result |
|--------|--------|
| **D (A4)** | **5 of 7 CLOSED, 2 STILL-OPEN.** CLOSED: within-identity-key gauge `(name, project_id)` (§5 A4 L196-198; §1 L37); reports 32 today with the 27-pair cause named (L214, §16.1 C-5 L895); pinned against silent regression (M-A4c/M-A4d); 0 target BLOCKED-ON the A/B resolution decision (L214, L223, §16.3 E-6). STILL-OPEN: (i) **located in code** — L210 still says "a FIND-IT item, not yet anchored"; `grep -rn "GROUP BY name HAVING COUNT" daemon/` → 0 hits, and no §18 row exists despite §19 claiming one was added; (ii) **DoD L34 states the gauge "= 0"** while the body states prod 32 / BLOCKED-ON — the dev-0-as-prod hazard the ruling exists to prevent. |
| **E (E4)** | **9 of 9 CLOSED** — pair-resolver declaring the winner on `skill_ab_tests` (`resolved_at` + `winner_skill_id`; skill/models.py:689-690) and deactivating the loser as ONE reversible receipted unit (repository.py:383-396 sets BOTH `is_active` and `status`); NO winners declared in Phase 1 (§9 E4 L517); default rule = newer evolved generation by LINEAGE GENERATION, not timestamps (§9 E4 L518; §17 #23 L947; models.py:173); R2 = no-op; R3 = 0 rows; `9e79ccad` = no flip; `76e3e1a7a` = does not exist (0 hits); the 2 IDENTICAL-content families excluded from content-logic remediation (§9 E4 L521). |
| **F (injection)** | **4 of 4 CLOSED** — dedupe at the injection boundary, at most ONE variant per `(name, project_id)` (§10 F L595-596; §17 #24; DS-48); deterministic pick = highest generation, ties by id (L595; models.py:173); M-F6 mutation makes BOTH variants appear → RED (L597); ambiguity flagged as hypothesis, causality not over-claimed (L598). Note: today's path routes per `ab_test_group` by hash (skill_injection_service.py:873 `_select_ab_variant`) — the ruling is correctly a NEW requirement, not a description of shipped behaviour. |
| **C (G before E1)** | **3 of 3 CLOSED** — §9 E1 L437 "Order: G FIRST, then E1.", §11 L672, §12 L716 hard edge, DS-21/DS-45; one owner, one lane (DS-45 L661); non-interference argued against real anchors (`skill_evolution_service.py:1448-1457` / `:1474-1483`; StaticPool `tests/services/conftest.py:39-46`). |

### C — regression check (did rev 4 break what rev 3 got right?)

| Item | Status | Evidence |
|------|--------|----------|
| P1/P2/P3 premise corrections | CLOSED | §3 L58-60 (15 true / 10 false of 25 declaring; `76e3e1a7a` does not exist; D6/D7/D8 NOT BUILT/DEFERRED); §10 L579; §9 E4 L493-500 |
| Net-new-reds protocol verbatim | CLOSED | §13.2 — `grep '^FAILED'` ONLY; NET-NEW = head MINUS base-of-3 MINUS named quarantine = 0; base = INTERSECTION across ≥3 runs |
| E4-before-F sequencing | CLOSED | §12 hard edges |
| A1/A2/A4/B/C/D designs | CLOSED | §6 B1 L259-271 (B1a resolver leg + B1b invocation leg; M-B1a–e) |
| B red→green achievable with capture OFF (tool-layer resolver + factory proof) | CLOSED | §6 B1 L259-271 |
| §7 `job_queues` PK = `queue_id` | **STILL-OPEN** | §7 L335 prose read still `SELECT id, name, is_paused FROM job_queues WHERE id = 'bd075f0b-…'`; PK is `queue_id` (job_queue/models.py:201); the C1 table row L339 IS correct; §17 #20 L944 repeats the wrong read; §19 L1027 claims FIXED |

### Other open items (outside the bar, still defects)

- §8 D3 rule 1 L391 — "category vocabulary is closed (`anomaly_service.py:26-38`)" → the closed vocabulary is `daemon/services/metrics_query/wiring/vocab.py:92` (`ANOMALY_CATEGORIES`, imported at anomaly_service.py:147); `:26-38` is FENCE documentation. Untracked because §19's F-numbers are renumbered relative to the council's.
- §6 B3 — the B2 refusal-text cite `skill_tools.py:1233-1242` is the `dispatched_job_id` outcome COMMENT; the refusal text ("NO skill-keeper job was created") is ≈`:1290-1300`.
- §19 ledger integrity — the "FIXED" column asserts closure for F-3, the §7 read, the A4 gauge row, A3, E1's cap and the three staging items while the cited bodies are unchanged. **SYSTEMIC (MAJOR): the plan now asserts closure it does not have** — and renumbered F-IDs can silently de-track a council finding.

### UNVERIFIED — disclosed gaps (not silently closed)

- `daemon/services/anomaly_service.py:692-718` (`accept_report`), `daemon/services/job_processor.py:738-844`, `daemon/services/job_queue_service.py:2976-2982` — not line-opened.
- Release-gate anchors `scripts/upgrade/stage.sh:59-60/:325-349/:418-424/:585-604/:671-716`, `scripts/upgrade/lib.sh:3225` — not line-opened; F-13..F-16 were checked as PLAN TEXT only, so the command strings themselves remain unverified against the script.
- F-16 (E4.1 wiring site) — unverified.

### Repair list — mechanical edits that reach APPROVE-WITH-FINDINGS

1. §5 A3 L157-183 + §18 rows 8-10 — predicate → `feedback_applied IS NOT NULL`; acceptance = measured count 3457→0; M-A3a mutation neutralizes THAT predicate (count returns to 3457 → RED); the missing completion-hook `applied` writer → disclosed residual, NOT built.
2. §9 E1 + §18 row 13 — cap key → `metadata->>'skill_id'` within a window (not `idempotency_key`); add Acceptance + mutation legs to E1 and E2; name a registered pack (or create + register under `test/packs/`); satisfy §13.3's pack reference.
3. §14.2 — `ENSEMBLE_ROLLBACK_SAFE=1` inside the staging command with the delta-empty rationale; tester-owned BOTH-TREE 181/181 prompt-baseline gate; FE sidecar present-from-source / absent-from-payload with the hardcoded `build_tool` deviation disclosed and not gated on `"npm run build"`.
4. §6 B3 L303 + §17 #1 L922 → `glob_files` `filesystem.py:754/:764`, `grep_files` `:1309/:1322`.
5. §7 L335 + §17 #20 → `WHERE queue_id = …` so the primary kill-switch read actually runs.
6. §5 A4 L210 + a §18 row → anchor the gauge at a real code site or downgrade acceptance #3 to an honest FIND-IT; DoD L34 "= 0" → "reports 32; 0 BLOCKED-ON the A/B resolution decision".
7. §8 D3 L391 → `daemon/services/metrics_query/wiring/vocab.py:92`.
8. §6 B3 → refusal-text cite `skill_tools.py:1233-1242` → ≈`:1290-1300`.
9. §19 — re-verify every row against its body; restore the council's original F-IDs (or publish a stable F-ID map) so nothing can be silently renumbered out of the ledger.
10. F-16 — locate E4.1's wiring/boot-order site or state explicitly that it has none.

### Credit — rev 4 improved materially

F-2 is genuinely closed (mechanism + proof + receipt + reversal path). Rulings E, F and C land complete — three of four rulings clean. Ruling D's conceptual fix — retiring the global, unsatisfiable name-divergence gauge for the within-identity-key measure, reporting 32, marking 0 blocked-on — is the right call. The A/B lineage facts (R2 no-op, R3 absent, `9e79ccad` no flip, `76e3e1a7a` nonexistent) are now stated rather than assumed. B3's write-path scope question is answered. Every premise correction and the net-new-reds protocol survived the revision intact.

### Open questions (plan owner / architect)

1. Which is authoritative for A3 — DS-43's `feedback_applied IS NOT NULL` (decisions.md L92-96 + §19 L1008) or §5 A3 L173's `applied == True`? They cannot both ship; the verdict-driving repair is to move §5 to DS-43.
2. §19 ledger vs body — who reconciles, and is there a rule that a ledger row cannot claim FIXED until its body matches?
3. F-16 — does E4.1 have a wiring/boot-order site today?

### Process note

The pass ran two workers (plan-text closure; anchor sweep + rulings). The daemon restarted mid-pass, which refused the first dispatches (anomaly `389a4c5a`) and left the anchor worker as a running-instance zombie whose graph was lost (anomaly `1dfbf835`); it was revived once and completed. The todo store proved non-restart-durable. The plan snapshot was stable throughout, so no finding here is a moving-target artifact.
