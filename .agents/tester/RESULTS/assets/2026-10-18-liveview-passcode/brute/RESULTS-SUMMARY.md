# Brute-force lockout + durability + rate-limit E2E — commission items 3+4

Date: 2026-10-18 · Worktree: /home/nea/ensemble-src-wt-liveview-passcode @ 1121f2604
Fence: /tmp/liveview-brute-fence (mktemp, escape-guarded, torn down) · DB: ensemble_dev (asserted pre-boot + per-service-call)
Port: 8083 (verified free pre-boot; released post-drill; 8088 never touched) · Fernet key: ONE drill key reused across ALL 3 boots
Boots: A (pid 209146, default limits) → B1 (pid 211023, overrides 100/600) → B6 (pid 213652, overrides 100/600)
Views: bruteroot1/target.md (main scenario), bruteroot1/target5.md (A5 per-IP keying only)

## RESULT lines

| Item | Result | Evidence |
|---|---|---|
| A1 valid grant first | PASS | a1-verdict.txt, a1-grant-headers.txt (303 + view_grant; Path=exact view path, HttpOnly, Secure, SameSite=Lax, Max-Age=86400); a1-get-with-grant-body.html renders content |
| A2 rate limit BEFORE lockout, DEFAULT | PASS | a2-seq.txt — 12 fast wrongs: `200 200 200 200 429 429 429 429 429 429 429 429` |
| A3 correct-code post-429 still works | PASS | a3-verdict.txt — +13s (1 refill token @5/min): 303 + new grant renders content (12 totals would have locked if 429=strike) |
| A4 livez + zero 5xx | PASS | a4-livez.txt (200), a4-5xx-scan.txt (empty) |
| A5 per-IP keying (rightmost XFF) | PASS | a5a-summary.txt — XFF 203.0.113.10: `200 200 200 200 200 429 429`; a5b-summary.txt — XFF 203.0.113.11 simultaneously processes (200 failure page) |
| B1 drive to lockout w/ documented overrides | PASS | b1-seq-final.txt — strikes carried (4) → wrongs to 10 → locked-page on next hit; rowpeek attempts=10 locked=True |
| B2 locked page, no disclosure | PASS* | b2-verdict.txt — wrong AND correct code both render locked page (no compare on locked row); no counts/remaining-tries/counter-digits. *NOTE: generic ratified copy "Too many incorrect passcode attempts." contains the word "attempts" (no number) — see defects |
| B3 grants survive lockout | PASS | b3-verdict.txt + b3-body.html — pre-lock grant (A3) renders content through lockout |
| B4 refresh / generation bump | PASS | b4-refresh.txt (passcode tmxj5t, gen 1→2, lock cleared, attempts 0); b4a old grant DEAD (interstitial, not content); b4b old passcode rejected; b4c new passcode 303 + new grant renders |
| B5 re-lock for durability | PASS | b5-seq-final.txt + b5-rowpeek-after-relock.txt — 8 wrongs → attempts 10 → locked-page from 9th wrong; pre-restart grant G3 minted first |
| B6 restart durability | PASS | b6a/b6b/b6c-verdict.txt + b6-rowpeek-before/after-restart.txt — after TERM+reboot (same key/data/DB/port): lock PERSISTED (wrong AND correct → locked page), pre-restart grant G3 STILL renders, row identical (attempts=10 locked=True gen=2) |
| B7 health after each boot | PASS | b1-livez-summary.txt (200), b7-livez-summary.txt (200), b7-verdict.txt — zero 5xx in ANY transcript across all 3 boots |

## Sequences (A2 emphasis)
- A2 (12 fast wrongs from 127.0.0.1, default bucket 5/5min): 200×4 → 429×8.
  4 processed not 5 because A1's mandated successful verify consumed bucket token #1
  (gate #1 runs on every POST; success consumes a token, never a strike — pinned by
  test_correct_code_does_not_consume_bucket_or_strike at test_live_views_public_lane.py:1162).
- A5a fresh-IP sequence shows the pristine boundary: 200×5 → 429×2 (exactly capacity 5).
- A3: correct code succeeded after ONE refill interval (13s), strike counter still 4
  (a3-rowpeek) → 429s consumed ZERO strikes (7 429s + 4 strikes = 11 would have locked).
- Strike ledger (rowpeek, PG): 4 after phase A → 10 + locked=True after B1 →
  0/gen2 after B4 refresh → 10 + locked=True after B5 → identical after B6 restart.

## Seams exercised
- Mint/refresh: ViewPasscodeService.mint/refresh (service-level) against the SAME PG
  + SAME drill Fernet key as the daemon — the proven state-b-dry-run.sh recipe.
  The view_link TOOL path requires a live agent-instance context (Cardinal #2 actor),
  not constructible from a shell drill; service seam recorded here per task allowance.
- Row introspection: ViewPasscodeService.get_row (read-only) for strike/lock/generation
  ledger (rowpeek*.txt) — corroborates the HTTP-observable behavior with PG state.
- All verify/grant/lock behavior exercised through the REAL public HTTP lane
  (POST /views/public/{root}/{rel} + grant-cookie GETs) against the real daemon.

## Defects (severity-tagged, reported not fixed)
- NOTE — locked-page copy wording vs mandate criterion: body says "Too many incorrect
  passcode attempts. Please ask … for a fresh one." The mandate's literal criterion
  ("no 'attempt' wording") flags it; the ADR-ratified copy (view_passcode_pages.py
  render_locked, cites V3/D2/R11) intentionally uses this generic sentence with NO
  count/remaining-tries. Zero digits-as-counters (digit hits in evidence are CSS
  values + CSP nonce only). Dispatcher adjudication: wording is per-spec; no change
  requested. Anchors: daemon/services/view_passcode_pages.py:234-254.
- NOTE — limiter-internal fail-open path has no dedicated unit test:
  VerifyRateLimiter.check() except→True (daemon/services/view_passcode_service.py:1986-1993,
  ADR V6) is not externally triggerable (str-keyed dict + monotonic clock; no runtime
  injection seam) and greps of tests/unit/routers/test_live_views_public_lane.py +
  tests/unit/services/test_view_passcode_service.py find no fail-open pin. The
  429-no-strike interplay IS pinned (test_live_views_public_lane.py:1128).
- NOTE (environment, non-feature) — ambient executor env carried
  POSTGRES_DB=ensemble_prod / POSTGRES_HOST=10.44.0.2 into the drill shell; the
  fail-closed dev-DB asserts (pre-boot + per-service-call) caught it and the worktree
  .env remained the only DB authority used; boot-a.log references ensemble_dev only.
  Env-poison family (cf. critical notes) — worth recording project-side.

## Runtime
~19 minutes wall-clock total (prep+boot A ≈ 4 min; phase A ≈ 2 min; B1 restart+drive ≈ 3 min;
B2–B5 ≈ 2 min; B6 restart+checks ≈ 3 min; evidence/cleanup/adjudication ≈ 5 min).
Every stage ran under `timeout 300` (dual layer: command wrapper + internal bounded waits:
90s boot liveness, 10s curl max-time, 13s refill sleep).

## Safety ledger
- Worktree only; zero implementation-code modifications; pre-existing dirty file
  .agents/shared/planning/designer-agent/install-audit.jsonl untouched (git status unchanged).
- All deletions escape-guarded to /tmp/liveview-brute-fence (case-pattern guard).
- Kills: only pids 209146/211023/213652, each verified bound to :8083 pre-TERM.
  Port 8088 never probed-for-kill. No stray processes remain.
