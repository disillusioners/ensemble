# Tracking — skill-system-revival Phase 1 (ENSEMBLE-27 / board wi-4d1fa093)

Plan: .agents/shared/planning/skill-system-revival/phase1-plan.md (rev 4, 1,130 lines)
Base: ae5bbafed @ /home/nea/ensemble-src-wt-skill-revival-phase1

## Iteration 001 — REJECTED (2026-11-09)

Method: approver direct verification (commissioning Leader's explicit instruction; no worker dispatch). Every blocking finding verified against worktree code, live schema (read-only SELECTs on ensemble_dev), and/or scripts/upgrade/*.

### Blocking issues raised
- **B1** §7 C1/C2 SQL invalid: `job_queues.name` (actual `queue_name`) and `projects.id` (actual `project_id` PK / `name` UNIQUE). Both executed → ERROR: column does not exist. F-21 disposition claims the class FIXED.
- **B2** §14.2 step 3 staging command: bare positional `v0.29.8` → stage.sh unknown-flag exit 78; no target token → defaults demo while §14.3 promote reads the live root; live staging needs ENSEMBLE_UPGRADE_LIVE=1.
- **B3** §14.2 step 5 migration census: staged payload has NO daemon/ tree (stage.sh copies only binary/agents/frontend/plugins/config.yaml/launcher.sh) → `diff -r <staged>/daemon/migrations/versions` errors. CArchiveReader TOC read must be primary, not fallback.
- **B4** §5 A3 acceptance "3,457 → 0" unreachable by the specified predicate `feedback_applied IS NOT NULL` (the window is defined by `feedback_applied IS NULL`; the change only widens by the 470 FALSE rows). A3 Acceptance 1 vs Acceptance 3 self-contradict; M-A3a vacuous; "the metric that reports this population" does not exist and has no §18 row. Inherited verbatim into §1 DoD.
- **B5** §9 E2 acceptance false vs code: `evolve_skill` raises ValueError for CAPTURED by documented design (skill_evolution_service.py:452-457); M-E2c mutates a nonexistent `evolution_type` field on the capture path (enqueue_capture writes metadata={"task_details":...}, job_type='skill_capture'); §18 has ZERO E2 rows.

### Major (non-blocking)
M1 `skill_evolution_lane_gate` is not a registered pack (no script, no PACKS.md row) though §13.3 says "EXISTING — extend". M2 B3 change list omits the agent-identity plumbing (filesystem.py resolver has no agent context; wrapper at instance.py:6342-6343). M3 A4 acceptance #7 ambiguous/contradictory (mismatch set "empty" vs gauge "reports 32") and requires Phase-2-only facts (fix live, ≥24h). M4 E1 SQL dialect-fragile (`metadata->>'skill_id'` breaks SQLite pack path; house pattern is the dialect split at job_queue/repository.py:3249) + "indexed-on-created_at" false (no such index) + table is `job_queue_items` not `job_items`. M5 N1's grep-the-staged-tree assertion always hits the binary (env-var names are string literals at skill_job_dispatcher.py:72/:202). M6 §1 Goal 5 "retire the stale/shadow skill rows" not delivered (E4 flips zero rows).

### Notes
§17 duplicate numbering (two 9s, two 10s) + §17 #11 contradicts §16.1 C-4/§17 #17; A4 FIND-IT parenthetical mis-states grep returns; provider.py:69→70; A1 closure needs getattr-with-default; G fixture SQLite write-lock contention (WAL/busy_timeout unstated); 3,457 vs 3,450 census timestamp; M-E4a mislabelled "deactivates R1".

### Positives recorded (do not re-litigate on re-submit)
Anchor accuracy verified across ~60 claims (essentially all correct). §13.2 base-leg protocol verified exactly against the evidence pack (4F/455P, 3F/456P, 4F/455P; intersection = 3 migration reds). G's decisive experiment verified as recorded. A2 polarity analysis correct both directions; check-site census = 2 confirmed. A4 counter-drift diagnosis correct. P1–P9 premise corrections verified. Ground rules respected (no switch, no promote, no brake, no canonical commit, no in-lane DB write).

### Repair guidance
B1–B3 are mechanical one-line fixes. B4: pick ONE framing — (i) predicate includes outcome fields (`selected AND task_succeeded`) so the window is actually refused; (ii) restate the acceptance as the 470 FALSE band with its own baseline; or (iii) add the §18 row creating the metric and pin against it. B5: state E2's real deliverable and either drop CAPTURED from the tool-routing acceptance or add the §18 row reversing the documented guard.

## Iteration 002 — APPROVE-WITH-FINDINGS (0 blocking, 11 notes) — 2026-11-09

Method: 2 fresh-eyes workers (`plan-approval`), partitioned by axis — doc (c7d08b87-e926-45b3-9eb4-5ee60401e87f) + execution (83eb859b-e16f-4e23-80f7-84580e02baa8); fan-in via todo_graph w-doc/w-exec. Artifact: 1,141 lines, sha256 04bcbb0408834a059e7a7674b78e001c236d4a0b497969446b50f7fde7b4da1f, worktree HEAD ae5bbafed. Both verdicts: APPROVED, 0 blocking. Verdict file: .agents/shared/planning/skill-system-revival/approver-verdict-002.md

Findings (all NOTE): F1 v0.29.8 slot TAKEN (minted 2026-11-09T13:14:44Z by another lane; T-3 guard holds); F2 B2/§13.3 "pack already runs test_skill_evolution_lane_gate" false (PACKS.md:867 = --collect-only census row); F3 "76e3e1a7a 0 hits" refuted (planning-doc hits only; skill-row consequence stands); F4 _build_auto_load_block = context_messages.py:2024; F5 A4 32-vs-27 unreconciled; F6 _resolve_within_workdir also serves write/edit (:1114/:1518); F7 pack filename drift (skill_evolution_pg_test.sh); F8 minor index/line drift (no deleted_at index; :215/:218; :51/:50; :1875 vintage vs :1882 current); F9 §18 row 8 name (`has_feedback_for_instance`) + predicate description drift vs §5 line 170 three-conjunct `has_genuinely_evolved_for_instance`; F10 stage.sh:718-760 touches INSTALL_DIR/.env (record in tester evidence); F11 E4.1 process-once latch left to implementation.

Iteration-001 blocker closure (verified this cycle):
- B1 C SQL: CLOSED — C-5 CTE executed on ensemble_dev → (0,0) with 15 skills; C1/C2 documented rows.
- B2 staging cmd: CLOSED — stage.sh live --version … --allow-stale-stage + ENSEMBLE_UPGRADE_LIVE=1; parser (*)→78 :125 + require_live_guard lib.sh:1363-1376 verified.
- B3 migration census: CLOSED — BINARY-TOC-primary (CArchiveReader); payload has no daemon/ tree; diff -r invalid; CArchiveReader importable.
- B4 A3 acceptance: CLOSED — LEADER RULING Q1 three-conjunct + DELTA form + 470-band + non-vacuous M-A3a; anchors verified.
- B5 E2 acceptance: CLOSED — E2 restated "BLOCKER 5"; CAPTURED ValueError documented as protected behavior; M-E2c re-placed on job_type stamp; §18 rows 39/40 added.
- M1 partially (pack destination named; residue = F2); M3 partially (Ruling E BLOCKED-ON; residue = F5); M4 partially (dialect split verified; residue = F8); M5 CLOSED; M6 reframed (no retire-rows goal; E4 = dual-column lever + operator script).

Ground rules: respected (both workers independently verified — no env switch flip, no promote, brake untouched, no canonical commits, no live/prod mutation). ENSEMBLE_ROLLBACK_SAFE legitimacy re-verified (migration delta EMPTY).
