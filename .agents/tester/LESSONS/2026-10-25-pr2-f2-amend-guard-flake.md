# F2 amend-guard flake — epoch-close vs amend-anchor two-connection race (PR2 runtime gate, 2026-10-25)

**Observed:** `mrpr2_scoped_wide` run 1 of 8 at `3fc16bc66` failed 3 nodes with ONE signature — the F2 closed-epoch guard let an amendment through:
- `tests/unit/test_mission_record_pr2_carries.py:197` `TestF2AmendGuard::test_the_amendment_only_touches_an_open_epoch` — `amend_anchor(...)` returned True on a settled epoch
- `tests/unit/test_mission_record_pr2_tap_amend.py:325` `TestAmendAtDelivery::test_the_closed_epoch_is_not_amended` — founding_message_id became `m-correction` on a closed epoch
- `tests/integration/test_mission_records_pr2_enforce_reads_pg.py:376` `test_the_closed_epoch_is_not_amended_over_pg` — same shape on real PG

**Retry budget:** 8 full-pack runs, 7P/1F, same HEAD + same file order; the 3 files solo → 31/31 PASS; tree code-identical to the ledger sha (docs-only delta) → **flaky, not a regression**. Rate ≈ 1/8.

**Root-cause candidate:** `transition_epoch_status` (`daemon/repositories/mission_record/repository.py:384`) and `amend_founding_message` (`:488`) are two guarded UPDATEs each committing on its OWN connection (unit fixtures: file-backed SQLite, `journal_mode=WAL`, `poolclass=NullPool` → every repo call = separate connection). A close that lands between the amend's status-read and its UPDATE lets the amendment slip onto the closed epoch. The PG-flavor failure shows the window is not SQLite-specific.

**Handling at gate:** NOT quarantined — the F2 guard nodes are load-bearing (mutation #10 `f2-amend-guard` pins them; quarantining would blind the very guard the matrix proves). Final measured runs green; disclosed in RESULTS + PACKS.md.

**PR3 hardening options (pick one):**
1. Make close+amend mutually exclusive in a single statement (e.g. `UPDATE ... WHERE status='open'` is already the guard — the race is read-then-write; move the status predicate INTO the amend UPDATE's WHERE and rely on `rowcount`, removing any prior read);
2. Re-read status inside the UPDATE's own transaction;
3. Shared connection (`StaticPool`) for these unit fixtures (test-side only; narrows but does not eliminate the production window).

**Triage recipe:** banked as skill `7641ce46` (`sqlite-multisession-guarded-update-flake-triage`).
