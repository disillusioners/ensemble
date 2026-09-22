# Lessons — ENSEMBLE_SELF_ENV auto-resolution gate (2026-09-22)

Branch `feature/env-auto-resolution` @ `ba295fec` (base `b024bb09`). Full report: RESULTS/2026-09-22-env-auto-resolution-verification.md.

## 1. release_journal pack on a Linux host = 43 guaranteed failures (host-arch, not regression)
First Linux-host run of `test/packs/release_journal_unit_test.sh` produced 225P/43F. Every failure cluster is preceded by `date: invalid option -- 'j'` — GNU date lacks BSD `-j`/`-v` (sites: `lib.sh:86 _iso_to_epoch`, `lib.sh:709` cooldown arm, retention eviction, `tests/test_release_journal.sh:460,735-736,936,988,1033`). The pack's green history (243→271/271) is BSD-host only. **Adjudication shortcut that held**: `git diff base..branch --stat` EMPTY over every failing file + comment-only stage.sh delta ⇒ no base leg needed — byte-identity is conclusive for "not branch-caused". Consolidated QUARANTINE.md row added so future Linux gates don't re-litigate. Recovery/retention lanes (adopt_stale_txn, sweep, cooldown/cap/quarantine math, retention) are UNVALIDATABLE on Linux until the uname-dispatch port (standing P2.1 GNU-debt commission).

## 2. Scenario-driver hygiene: wipe scratch installs between sub-cases
W-B's S3c initially failed as a false positive: the live-shaped install created by S3b persisted in the redirected `Path.home()` tree, so the "marker-absent" comparison arm auto-derived `live` and hit `env-self-match` instead of `env-marker-absent`. Fix: fresh `/tmp/envres-<label>-XXXXXXXX/` home per sub-case. Any driver that redirects `Path.home`/install dirs must isolate per CASE, not per scenario group — auto-derive makes leftover on-disk evidence observable.

## 3. `parse_iso_utc` is Z-suffix strict — `+00:00` offsets fail CLOSED (expired)
Fabricated `expires_at='2030-01-01T00:00:00+00:00'` was rejected as unparseable → gate treated the window as EXPIRED (fail-closed). Correct security posture, but test fixtures must use `…Z` suffixes for user-origin windows. Same for nonce extraction: dry_run-minted nonces are `CONFIRM-XXXX-XXXX` (dashed) — normalize via regex capture + strip dashes + upper() before the bound comparison.

## 4. Empty marker ≠ garbage marker (trim contract)
`ENSEMBLE_SELF_ENV=''` / whitespace-only trims to `None` ⇒ treated as ABSENT (source `auto`), NOT garbage. Garbage handling (`ignored-garbage` + WARN + fall-through) only fires on non-empty unrecognized values (`banana`, `DEV`, `Live`, …). Two audit workers independently confirmed; encode this distinction in any future env-marker tests.

## 5. Real-host resolution facts worth remembering (read-only verified 2026-09-22)
- LIVE `~/agents-ensemble`: NO marker, NO releases/ (pre-P2.1), `.env` POSTGRES_DB=ensemble_prod → auto-derive `live`. The standing "port 9797 env=unresolved self-ID gap" critical note CLOSES when this feature reaches live.
- DEMO `~/agents-ensemble-demo`: explicit `ENSEMBLE_SELF_ENV=demo`, POSTGRES_DB=ensemble_demo, releases/v0.13.10 → `demo`.
- Real daemons are ELF, `sys.frozen=False` ⇒ frozen-binary sandbox rule never fires on this host today; demo-with-releases/ CANNOT be misresolved to sandbox (guard: frozen+uncanonical-install; canonical live/demo dirs win).
- Garbage-marker WARN fires PER CALL (no dedupe) — log-spam follow-up 🟠, not applied (verify-only).
