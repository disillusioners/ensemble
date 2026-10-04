# Re-Verification — chart-image-delivery @ e3e64825 (promote-readiness gate)

**Date:** 2026-10-04 (UTC)
**Verifier:** Tester (independent re-verification round 2)
**Feature / branch:** `feature/chart-image-delivery` @ `e3e64825c4a96b31083f1fbc8511ea6f6fa1ceba` (parent `84048390`)
**Worktree:** `/home/nea/ensemble-src-wt-chart-img`
**Prior round:** `2026-10-04-chart-image-delivery-independent-verification.md` (verdict: NOT USER-STORY-VERIFIED — 1 CRITICAL Slack drift)
**Workers (12):** `cid2-fixread` `cid2-e2e-int` `cid2-e2e-std` `cid2-chart` `cid2-charter` `cid2-dispatcher` `cid2-discord` `cid2-tgslack` `cid2-outbound` `cid2-auditpy` `cid2-audit` `cid2-break`

---

## TL;DR — Final verdict

**✅ USER-STORY-VERIFIED** (with F5 + N6 as recorded deferrals)

The CRITICAL Slack `channel_id=` drift is dead: fixed, drift-catch test proven by test-the-test mutation (RED with the production TypeError signature), tree byte-clean. Full re-run: **592 passed / 0 failed** across all named suites + both e2e legs — exactly the expected +1 (new real-SDK test) over the prior round's 591. Audit 24/24 ×2 idempotent with pin #8's `file_obj` overmatch closed. Fix commit is clean: 5 files, every hunk in-bucket, tripwire re-pin proven AUTHORIZED (old pin was the sha256 of the buggy pre-fix file — it had correctly fired).

---

## Per-task results

### R1 — The critical fix: CONFIRMED
- `daemon/sources/adapters/slack/adapter.py:622` now `channel=channel_id,` inside `_safe_api_call("files_upload_v2", …)`; comment updated in step.
- Installed lib probe (independent): slack_sdk `3.42.0`; `inspect.signature(WebClient.files_upload_v2)` → `channel: Optional[str] = None`, **no `channel_id` param**.
- Mechanism independently reproduced in-memory: drifted call → `TypeError: files_completeUploadExternal() got multiple values for keyword argument 'channel_id'`; fixed call → `ok: True`, completion params `{'files': …, 'channel_id': 'C123456', 'initial_comment': …}` (SDK's internal `channel→channel_id` mapping at the wire). Root cause exact.
- Full `grep channel_id` sweep of adapter.py: 45 hits, ALL benign (log strings, DM-cache vars, inbound `body.get("channel_id")`, metadata). Upload-kwargs named `channel_id`: **ZERO**. `chat.postMessage` (`:688`) correctly `channel=channel_id`.

### R2 — Flipped assertion: CONFIRMED
- `tests/test_slack_adapter.py:2144`: `assert "channel" in upload_call["kwargs"]` — single site.
- Sweep of all 39 `channel_id` hits in the test file: zero assert `channel_id` as the upload kwarg. The new test's `:2234` asserts the SDK-*resolved* HTTP wire param (`completion["req_args"]["params"]["channel_id"]`) — the correct direction (proves the internal mapping fired).

### R3 — New drift-catch test: CONFIRMED + TEST-THE-TEST PASSED
- Nodeid: `tests/test_slack_adapter.py::TestSlackSingleFilesUploadV2::test_files_upload_v2_real_sdk_method_transport_stubbed` (:2195).
- **Real client:** `real_slack_sdk` fixture evicts all `slack_sdk*` from `sys.modules` and re-imports — necessary because root `tests/conftest.py:137-139,249-250` mocks `slack_sdk` at collection time (this is WHY the whole suite was structurally blind to the drift). `AsyncWebClient(token=…, session=fake_session)` constructed in-test.
- **Transport-layer stub ONLY:** `_FakeSlackHttpSession` injected via constructor `session=`; zero `patch()` calls; `files_upload_v2`, `_safe_api_call`, `_do_api_call` all run REAL code. The real 3-step v2 chain (getUploadURLExternal → binary PUT → completeUploadExternal) observed on the fake transport.
- **Assertions:** `ok is True`; `delivered_image_ids == ["c"*32]`; ≥1 upload URL; exactly 1 binary upload POST; wire `channel_id == "C123456"` at completion.
- **TEST-THE-TEST (mutation):** solo GREEN (`1 passed`) → reintroduce `channel_id=channel_id` at `:622` (sole diff) → solo **RED**: `1 failed`, captured logs carry the production signature verbatim — `AsyncWebClient.files_completeUploadExternal() got multiple values for keyword argument 'channel_id'`; independent /tmp repro captured the native SDK traceback at `slack_sdk/web/async_client.py:4122` (async twin of the sync `client.py:~4112`). Full file while mutated: exactly the expected **2 failed** (new drift-catch test + flipped-assertion test at `:2144`), 113 passed. Revert → `115 passed in 1.36s`. Restoration proof: status EMPTY, stash 17, HEAD unchanged, diff EMPTY.
  - *Honest nuance:* the adapter's rate_limiter/`_safe_api_call` swallows the TypeError (retry → text fallback), so pytest's failing frame is the downstream `delivered_image_ids` assertion while the TypeError rides in the captured ERROR logs — the same swallow-shape as the production incident; causally deterministic.

### R4 — Pin #8 tightened: CONFIRMED (one residual, cosmetic)
- New regex `kwargs\["file"\]\s*=\s*file\s*(#|$)` — matches `adapter.py:1553` (real target), **no longer matches** `:1778 kwargs["file"] = file_obj` (prefix overmatch CLOSED).
- Residual (non-blocking, NOT a regression — old regex had it too): pattern unanchored at line start, so a hypothetical leading-`#` comment line ending in `kwargs["file"] = file` would still satisfy it; no such comment exists in-tree. Suggested follow-up: `^kwargs\[...` anchor.
- Audit runs: **24/24 ×2** (diff-clean, sha256 `2d0cc546…` both) + `--class preservation` **9/9**. Post-run git proof: status EMPTY, stash 17.

### R5 — Full re-run, MY numbers: **592 passed / 0 failed**

| Suite | Expected | Mine @ e3e64825 | Prior @ 84048390 |
|---|---|---|---|
| chart tools+reuse+legacy | 37 | **37** | 37 |
| charter render capture | 44/4 desel | **44 passed, 4 deselected** (`1.08s`) | 44/4 |
| dispatcher | 78 | **78** (`0.49s`) | 78 |
| discord | 196 | **196** (`2.37s`) | 196 |
| telegram+slack | 45+115 | **160** (`3.06s`) | 159 (114 slack) |
| outbound | 6 | **6** (`0.24s`) | 6 |
| audit pytest | 43 | **43** (`0.37s`) | 43 |
| e2e standard leg | 24/4 desel | **24 passed, 4 deselected** (`0.82s`) | 24/4 |
| e2e integration leg | 4 | **4** (`1.09s`) | 4 |
| **TOTAL executed-passed** | **≈592** | **592** | 591 |
| Failed | 0 | **0** | 0 |

Reconciliation: 564 non-e2e (37+44+78+196+45+**115**+6+43) + 24 e2e-std + 4 e2e-int = **592**. Delta vs prior round = +1 = the new real-SDK drift-catch test (slack 114→115). Exact match to expectation; no other count moved.

### R6 — Release report §2: CONFIRMED
§2 now carries: slack row "114 | **115 post-remediation**"; TOTAL corrected to **591** (563 non-e2e + 24 + 4) with the accounting paragraph explaining the old 619 = 563 + 28 + 28 (e2e double-count per leg); remediation trail item 3 records **592** (564 + 24 + 4). Arithmetic independently recomputed — matches my convention exactly. Found→fixed→re-verified trail complete.

### R7 — Tripwire re-pin: CONFIRMED AUTHORIZED (not a smuggling)
- `tests/test_chart_image_delivery_e2e.py:1240-1245`: removed pin `da935786ce55…` → added 4-line provenance comment ("authorized remediation commit… any FURTHER change to this file still trips") + pin `476ba0b5a17a…`.
- Honesty proven by hash: `git show 84048390:…adapter.py | sha256sum` = `da935786…` (old pin = the BUGGY pre-fix file — the tripwire had correctly fired on the fix, exactly as designed); current file sha256 = `476ba0b5…` = new pin, byte-exact. Check logic untouched (`test_phase_d_does_not_modify_sealed_artifacts` still hashes + `pytest.fail`). No assertion removal, no relaxation, no skip.

### Commit diff review (fixread): FIX-COMMIT-CLEAN
`e3e64825` = exactly 5 files, +168/−7, every hunk in its declared bucket (adapter fix / test flip+new test / audit regex / release-report §2 / e2e tripwire re-pin). Nothing else. No logic changes, no assertion removals, no skips added to existing tests.

---

## Restoration proof (end-state)

- `git rev-parse HEAD` = `e3e64825c4a96b31083f1fbc8511ea6f6fa1ceba`
- `git status --porcelain` = EMPTY · `git stash list | wc -l` = 17 · `git diff` = EMPTY
- No commits / pushes / merges; no daemon booted; main checkout, `.env`, `~/.config` untouched; port 8088 untouched; scratch confined to /tmp and cleaned.

## Code changes by this round: NONE (independent verification; the single test-the-test mutation at slack/adapter.py:622 was reverted to byte-identical, proven above)

---

## Recorded deferrals (per leader context — both ledgered)

1. **F5** — single-file `generate_chart` → marker → parent-text e2e (60-100+ LOC) — deferred to Phase D ledger. Hermetic coverage remains piecewise (charter Step 6c emit/suppress, tool mechanics, both-seam dispatcher pins, g7 marker-authored lane, audit passthrough-adjacency pins).
2. **N6** — real-platform smoke (real mmdc render + live delivery) — held with the release cut per `release-report.md` §8; fresh boot hard-fenced for this round by task constraints.

## Minor follow-ups (non-blocking)

- 🟢 Pin #8: add line-start anchor (`^kwargs\[...`) to close the leading-comment residual (cosmetic; no in-tree comment matches today).
- 🟢 Round-1 cosmetic items still open (both-branches-pass 24h-freshness test, tautology clusters `:476-478`/`:2162-2166`, stale skip-if-daemon docstring + dead `socket` import) — trim opportunistically; do not cite the 24h test as coverage.
- 🟢 `conftest.py` globally mocks `slack_sdk` at collection — the new test's `sys.modules` eviction is the correct countermeasure; consider a comment in conftest pointing future adapter tests at the `real_slack_sdk` fixture pattern to avoid re-creating this blind spot.

---

## Verdict

**✅ USER-STORY-VERIFIED @ e3e64825** — Discord native upload (file/files, mutually exclusive, empty-content guard), Telegram multipart (chat_id/photo|document/caption, size ladder, no parse_mode), Slack native upload (`channel=` verified against installed slack_sdk 3.42.0, drift-catch test mutation-proven), marker contract closed on all malformed/fake shapes, both-seam extraction with no-colon skip + adapter lookup, store.delete after delivery, text floors on all three platforms, no marker/mmdc leakage. **Promote-ready from the testing lane**, subject to the two recorded deferrals (F5 e2e, N6 real-platform smoke) which the release process already holds.
