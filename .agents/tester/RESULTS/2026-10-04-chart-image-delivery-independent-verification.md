# Independent Verification — chart-image-delivery @ 84048390

**Date:** 2026-10-04 (UTC)
**Verifier:** Tester (independent verification round)
**Feature / branch:** `feature/chart-image-delivery` @ `840483907e076912cb2e6178a078d87964d1432e`
**Worktree:** `/home/nea/ensemble-src-wt-chart-img`
**Plan refs:** `.agents/shared/planning/chart-image-delivery/{phaseD-plan.md,release-report.md}`
**Worker instances (16):** `cid-discover` `cid-harness-review` `cid-static-sanity` `cid-e2e-int` `cid-e2e-std` `cid-chart-suites` `cid-charter-suite` `cid-dispatcher-suite` `cid-discord-suite` `cid-tg-slack-suites` `cid-outbound-suite` `cid-audit-pytest` `cid-audit-script` `cid-mockdrift` `cid-marker-edge` `cid-break-check`

---

## TL;DR — Final verdict

**❌ NOT USER-STORY-VERIFIED — 1 CRITICAL gap + 2 declared residuals + 1 coverage gap + 1 audit-pin fragility + 1 cosmetic**

The Discord and Telegram native image-upload paths verify end-to-end, and the marker contract is hermetically closed on every malformed / fake-id shape I could devise. The audit script + audit pytest are honest green. Harness genuineness holds for the delivery/dispatch chain. **But the Slack native upload is production-broken — `daemon/sources/adapters/slack/adapter.py:622` passes `channel_id=channel_id` to `files_upload_v2`, which is not a parameter slack_sdk 3.42.0 accepts; every Slack image upload raises `TypeError: ... got multiple values for keyword argument 'channel_id'`, the exception is caught by `_safe_api_call`, the image is silently dropped, and only the text floor fires. The test at `tests/test_slack_adapter.py:2039` asserts `"channel_id" in upload_call["kwargs"]` — asserting the wrong kwarg and locking the bug in. Two independent repros confirm** (network-free mockdrift run + real-call-site break-check run, both at the actual `slack_sdk/web/client.py:4112` invocation frame).

The Slack gap is the user story's "Slack/Telegram same mechanism with native APIs + text floors" clause: Telegram works, Slack silently falls back to text-only on every chart. **Not green** until `channel_id=channel_id` → `channel=channel_id` and the test assertion is flipped.

---

## Per-task results

### T1 — User-story e2e re-run — PASS (both legs)
- **Integration leg** (`--override-ini="addopts=" -m integration`): 4 astream tests PASSED in 1.21 s; names exact (`test_astream_discord_user_receives_png`, `test_astream_progressive_lane_marker_extracted`, `test_astream_progressive_lane_failure_routes_to_completed`, `test_astream_internal_agent_source_no_extract`). Pre-flight: `daemon.__file__` resolves inside the worktree.
- **Standard leg** (default addopts): `24 passed, 4 deselected in 0.81s` (the 4 = the integration-marked astream tests; deselection is expected and structurally correct).
- **Harness genuineness:** **HARNESS-GENUINE** for the delivery/dispatch chain. Real `InstanceMessagingService(manager=manager, cancellation_service=cancellation_service)` at `test:1589-1592` against real constructor `instance_messaging.py:861-875`; real `_process_message_with_tracking` called at 4 sites with zero monkeypatch/patch/autospec; real `ResponseDispatcher` (`:198`) at both seams (`dispatch_completed` `:254`, `dispatch_message` `:412`); real `no-colon` + `adapter-lookup` preconditions in both seams (`:301-303/:337-345/:348-353` completed, `:431-433/:448-455/:462-477` progressive); real hermetic `TmpImageStore(data_dir=tempfile.TemporaryDirectory(prefix="chart-img-e2e-"))` — temp-dir backed, no daemon, no network. `spy_store` defaults to `retention_class="normal"` (matches directive D3); provenance `feature="chart-render"` pinned through the real provenance gate (`:150-164`). 35 Group 6 sealed SHA baselines re-verified — all match.
  - **Zero `pytest.skip` / `pytest.mark.skip` / `skipif` / `xfail`** in the e2e file; zero method patching; zero `subprocess|Popen|uvicorn|dev.sh|docker|os.system` (fully in-process).
  - **Soft spots (decorative, not green-lies):** `test_image_get_optional_24h_freshness_window` is both-branches-pass by its own docstring (`:824-869`); two tautology clusters at `:451-482` and `:2162-2166` (decorative; the load-bearing assertions are at `:414-448` and `:2147-2159`); stale skip-if-daemon docstring + dead `socket` import (`:23-43`, `:57`).
  - **Two declared scope limits** the harness is honest about: (1) real mmdc→PNG render is not exercised in-repo (fixture PNG used; true-render cases in `test_charter_render_capture.py` are `@pytest.mark.integration` and require a live charter — N6-blocked per task brief); (2) no single-file end-to-end test drives `generate_chart` → marker → parent-text passthrough (g7 shortcuts by authoring the marker into the AI message directly).

### T2 — Full named-suite regression — 591 PASS / 0 FAIL (accounting note for leader's "619")

| Suite | File | Leader's | Mine |
|---|---|---|---|
| chart tools | `tests/test_chart_tools.py` | 24 | **24** |
| chart reuse | `tests/test_chart_tools_reuse_integration.py` | 11 | **11** |
| chart legacy | `tests/test_chart_tools_legacy_error_contract.py` | 2 | **2** |
| charter render capture | `tests/test_charter_render_capture.py` | 44 | **44** (4 deselected: 3 integration + 1 slow `test_install_skill_handles_missing_nvm`) |
| response dispatcher | `tests/test_sources_dispatcher.py` | 78 | **78** (21 near-miss/malformed-marker refs in-file) |
| discord adapter | `tests/test_discord_adapter.py` | 196 | **196** |
| telegram adapter | `tests/test_telegram_adapter.py` | 45 | **45** |
| slack adapter | `tests/test_slack_adapter.py` | 114 | **114** (suite green — see T5 for the silent production-broken reason) |
| outbound e2e | `tests/test_outbound_image_delivery.py` | 6 | **6** |
| audit pytest | `tests/test_chart_image_delivery_audit.py` | 43 | **43** |
| e2e standard leg | `tests/test_chart_image_delivery_e2e.py` | 24 | **24** (4 deselected = integration) |
| e2e integration leg | `tests/test_chart_image_delivery_e2e.py` | 4 | **4** |
| **Total executed-passed** | | **619 (claimed)** | **591** |
| Failed | | 0 | **0** |
| Deselected (structural) | | 4 (e2e std) | **8** (4 e2e std + 3 charter integration + 1 charter slow) |

**Reconciliation of 619 vs 591:** 619 − 591 = 28 = the e2e file's total test count. My executed-passed sum is 563 (non-e2e suites with default addopts) + 24 (e2e std passed) + 4 (e2e int passed) = 591. The "619" figure = 563 + 2 × 28 — likely double-counts the e2e file once per leg (the 4 deselected on the standard leg + 24 deselected on the integration leg added back). Accounting artifact, **no missing coverage**. The 8 structural deselections are all `@pytest.mark.integration` or `@pytest.mark.slow` and live outside hermetic scope (real-render cases need live charter; e2e astream legs are covered by the integration leg pass).

### T3 — Pin audit — PASS
- Runs 1 + 2 (full class): **24/24 (9/9 preservation + 15/15 feature)**, exit 0, ~0.5 s each. Outputs `diff`-clean, sha256-verified identical (`2d0cc5464e193949e627f0640237a944fd75d5d4db16374b02a9ac78d476d24a`). Idempotency: PASS.
- Run 3 (`--class preservation`): **9/9**, exit 0.
- Pre + post git proof: `git status --porcelain` EMPTY, `git stash list | wc -l` = 17 (unchanged). The audit stages a copy under `/tmp` by design — worktree untouched.

### T4 — Break-check — PASS (audit-binding proven on pin #8)
- Break #1 (Discord empty-content guard at `adapter.py:1615`, single line commented): audit stayed green — the bash audit does not pin this line. Reverted.
- Break #2d (both `kwargs["file"] = file` sites at lines **1553 and 1778** replaced with `pass`): audit went **RED** with `TOTAL: 23/24 (1 failed)`, `EXIT:1`, naming **pin #8 ("discord _send_single_chunk kwarg")**. Single-pin isolation (all other 23 stayed green). Reverted via `git restore`.
- Restoration proof: `git status --porcelain` EMPTY, `git stash list | wc -l` = 17, `git rev-parse HEAD` = `840483907e07...` unchanged, `git diff` EMPTY. Final audit `24/24`, exit 0. Tree ends byte-clean.
- **Pin #8 fragility surfaced** (not blocking, flagging): the audit regex `kwargs\["file"\]\s*=\s*file` is non-comment-aware AND matches the second site via `file_obj` prefix. A literal comment containing `kwargs["file"] = file` would satisfy it. Suggested tighten: `kwargs\["file"\]\s*=\s*file\s*(#|$)`.

### T5 — Mock-real drift spot-checks — 1 DRIFT (Slack), 2 MATCH
- **Discord — MATCH:** `discord.py 2.7.1` (verified). Adapter at `daemon/sources/adapters/discord/adapter.py:1545-1554` + `:1735-1745`: constructs `_discord_mod.File(fp=BytesIO(bytes), filename=img.filename)` for N=1, list of File for N>1; `_send_single_chunk` populates `file=` (single) or `files=` (N>1), mutually exclusive. Real `Messageable.send` signature (`:170-171`) accepts `file: Optional[File]`, `files: Optional[Sequence[File]]`, forbids both at once (raises `ValueError`). Tests `TestDiscordChunk1AtomicFirstUnit` (`:2829-2836`) and `TestDiscordMultiImageNoSilentDrop` (`:2901-2906`) assert the exact mutual-exclusion discipline. Empty-content guard at `:1615` is a real guard (not a pin). No drift.
- **Telegram — MATCH:** `aiohttp 3.13.3` (no `python-telegram-bot`). Adapter at `daemon/sources/adapters/telegram.py:226-327`: raw `aiohttp.FormData` POST to `https://api.telegram.org/bot{token}/{method}`; fields `photo` (≤10 MB) or `document` (>10 MB) + `chat_id` + optional `caption`; no `parse_mode` (Mermaid `<` safety). Matches Bot API spec for `sendPhoto`/`sendDocument`. Tests assert on real `FormData._fields` internal structure. No drift.
- **🚨 Slack — DRIFT (production-breaking):** `slack_sdk 3.42.0` (verified). Adapter at `daemon/sources/adapters/slack/adapter.py:620-626`:
  ```python
  ok, result = await self._safe_api_call(
      "files_upload_v2",
      channel_id=channel_id,         # ← WRONG kwarg name
      filename=img.filename,
      content=file_bytes,
      initial_comment=initial_comment,
  )
  ```
  Real `WebClient.files_upload_v2` signature: `(*, filename, file, content, title, alt_txt, highlight_type, snippet_type, file_uploads, channel=None, channels=None, initial_comment, thread_ts, request_file_info=True, **kwargs)`. There is **no** `channel_id` named parameter; `channel_id` falls into `**kwargs`. Inside `slack_sdk/web/client.py:4112-4119`, `files_upload_v2` calls `self.files_completeUploadExternal(files=..., channel_id=channel, channels=channels, initial_comment=..., thread_ts=..., **kwargs)` — the explicit `channel_id=channel` (which is `None` when the caller used `channel_id=`) and the caller's `channel_id=` from `**kwargs` collide at the call frame → `TypeError: ... got multiple values for keyword argument 'channel_id'`.
  - **Two independent repros confirm** (REPRO-CONFIRMED):
    1. **mockdrift** worker: minimal Python repro with installed library — TypeError before network.
    2. **break-check** worker: `/tmp/cid_slack_repro.py` network-free (mocks `files_getUploadURLExternal` + `_upload_file` + `_fake_complete`, client `base_url='http://127.0.0.1:1'` defense-in-depth). Verbatim output:
        ```
        CASE 1: client.files_upload_v2(channel_id='C1', ...)
          CONFIRMED: TypeError: __main__._fake_complete() got multiple values for keyword argument 'channel_id'
          …File "/home/.../slack_sdk/web/client.py", line 4112, in files_upload_v2
              completion = self.files_completeUploadExternal(
                  files=[...],
                  …,
                  **kwargs,
              )
            TypeError: __main__._fake_complete() got multiple values for keyword argument 'channel_id'
        CASE 2: client.files_upload_v2(channel='C1', ...)
          OK: returned SlackResponse
        ```
  - **Why tests are green:** `tests/test_slack_adapter.py:2039` (`TestSlackSingleFilesUploadV2.test_files_upload_v2_called_per_image`) stubs `_safe_api_call` and asserts `"channel_id" in upload_call["kwargs"]` — asserting the **wrong kwarg exists**, locking the bug in. No test in the file invokes the real `WebClient.files_upload_v2` / `AsyncWebClient.files_upload_v2`; all stub one or two layers above. `chat.postMessage` (same adapter `:686-690`) correctly uses `channel=channel_id` — the author understood the kwarg; the bug is local to the `files_upload_v2` call site.
  - **Production impact:** every Slack chart-image upload raises TypeError → caught by `_safe_api_call` `except Exception` (`:456-458`) → logs `"Unexpected error calling files_upload_v2: ..."` → returns `(False, None)` → image silently dropped, text-floor `chat.postMessage` (correct kwarg) still fires → user sees caption without the chart. Text-floor invariant holds; chart never reaches the user. The release-cut is on the item Slack will be promoted as live-text-only until the fix lands.
  - **Fix (one line + one test assertion):** `daemon/sources/adapters/slack/adapter.py:622`: `channel_id=channel_id` → `channel=channel_id`. `tests/test_slack_adapter.py:2039`: `"channel_id" in upload_call["kwargs"]` → `"channel" in upload_call["kwargs"]`. Add an integration test that exercises the real `AsyncWebClient.files_upload_v2` with the network layer stubbed — catches this drift class at CI before next regression.

### T6 — Marker-contract closure — MARKER-CONTRACT-CLOSED
- All 6 edge-case families on fresh ad-hoc probes (script in `/tmp/cid_marker_edge.py`, repo untouched):
  | Case | Shape | locked_re | sweeper | extracted_ids | residue | Verdict |
  |---|---|---|---|---|---|---|
  | 0 (control) | valid 32-hex | match | — | `['9c129d...']` | no | PASS |
  | 1 | 31-hex | no | yes | `[]` | no | PASS |
  | 2 | 33-hex | no | yes | `[]` | no | PASS |
  | 3 | 32 UPPERCASE hex | no | yes | `[]` | no | PASS |
  | 4a | valid id + 3-spaces leading/trailing | no | yes | `[]` | no | PASS |
  | 4b | valid id + tab both sides | no | yes | `[]` | no | PASS |
  | 5 | near-miss (UPPER id) inside ``` fence | no | yes | `[]` | no | PASS (fence/`BEFORE/AFTER` preserved) |
  | 6 | fake id `'f'*32` vs empty store, real `dispatch_completed` | — | — | extracted → `open_full` raises `TmpImageNotFound` → WARN captured `"chart-image resolve failed image_id=ffff...: tmp image not found: ffff..."` → `images=[]`, no marker residue, no base64, sent_count=1, text intact | — | PASS (no raise escapes) |
- Code paths confirmed: `_MARKER_RE` `daemon/sources/dispatcher.py:29-32` (LOCKED `^<!-- ens-img:chart-render:([a-f0-9]{32}) -->$`, 32 lowercase hex only); `_NEAR_MISS_RE` `:50-53`; two-pass `extract_chart_images` `:74-117` (locked first, sweeper never touches `image_ids`); image resolve at `_resolve_chart_images:120-191` with WARN+drop handler at `:185-190` (no exception escapes); TmpImageStore raise at `daemon/services/tmp_image_store.py:492`.
- **Two documented non-leak semantics** (carry forward, not bugs): (i) **fence-blindness** — a byte-perfect valid marker on its own line inside a code fence IS extracted (line-based contract; fine per spec, charter prompts should never wrap markers in fences); (ii) **padded-valid-id loss** — whitespace-padded valid markers are stripped with the id lost (locked regex byte-stability at `:28`, `:40-49`; emission side must use the exact unpadded form on its own line).
- Existing coverage cross-referenced: `test_malformed_marker_with_invented_id_not_extracted` (`:1368`), `test_multiline_marker_not_extracted` (`:1375`), `test_near_miss_stripped_not_extracted` (`:1409`), `test_near_miss_re_matches_indented` (`:1466`), `test_near_miss_re_textually_overlaps_locked_form_but_safe_via_two_pass` (`:1470`), `test_image_resolution_per_id_isolation` (`:1525`), `TestDegradedPath.test_image_get_raises_marker_stripped_text_delivered` (e2e `:705`). Fresh pins this verification covered: case 3 (uppercase), 5/5b (fence), 4b (tab-padded), case 6 (real-`dispatch_completed` empty-store path).

### T7 — Static release sanity — 5/5 PASS
- Version pin: `daemon/__init__.py:3` and `pyproject.toml:3` both `0.16.13` ✓.
- CHANGELOG byte-identity: extracted block under `CHANGELOG.md:13` (`[Unreleased]` → `### Added`) vs `docs/changelog-pending/chart-image-delivery.md` — **byte-identical** (sha256 `5041fc1ed5e1cca2f8d978216fe0bed9ca36b11b3a019a99ea8275868658ba83`, both copies). Caveat — no `## [0.16.13]` heading exists in CHANGELOG.md yet (the block sits under `[Unreleased]` pre-promote, consistent with the pending doc's slice instructions and the project's `upgrade_policy` ceremony).
- Release report: `.agents/shared/planning/chart-image-delivery/release-report.md` has **exactly 9 numbered top-level sections** (§1 What shipped · §2 Test evidence · §3 USER ACTION ITEM Slack `files:write` OAuth scope · §4 Restart/promote matrix · §5 Rollback notes · §6 Adopted-items ledger · §7 Deferred-items ledger + accepted residual · §8 Real-platform smoke evidence · §9 Sign-off; unnumbered "Appendix — Deviations & discrepancies" follows §9 and is not counted). §3 carries the explicit Slack `files:write` user-action steps 1–5 with the fallback line *"Until granted, Slack chart-image delivery is text-only with a WARN-once log per channel."* §4 matrix verbatim: B = **YES / YES** (restart+promote required); A = **NO / NO** (next-spawn); C = **NO / NO** (next-spawn); D = n/a (test/docs/planning); docs = n/a; Release cut = YES / YES.
- Frontend isolation: `git diff --stat cf8efbef..HEAD -- frontend/` EMPTY; `git diff --name-only cf8efbef..HEAD | grep -c "^frontend/"` = 0. TrueAuto web rule: no frontend touched.
- Change-set context: 61 files changed, +13,052 / −110, 27 commits in `cf8efbef..HEAD`.

### T8 — Frontend isolation — PASS (covered in T7).

---

## Scope decision

The verification round was scoped by the leader's task (T1–T8). All eight task families were executed; no scope reduction (the change set is broad — 61 files, cross-module architecture A/B/C/D plus release cut). Full-suite warranted per blast-radius.

---

## ensure.md validation — N/A

No `ensure.md` exists for this verification round (the task brief didn't request one, and the project-wide `.agents/tester/rules/ensure.md` is the 4-line dev.sh boot probe — unrelated to chart-image-delivery). The 24-pin audit script (`tools/audit-chart-image-delivery.sh`) IS the feature-specific quality gate, and it is independently green.

---

## Quick fixes applied — NONE

Independent verification round: no worktree mutations except the audit break-check (discord pin #8 deliberate break at lines 1553 + 1778) which was reverted to byte-identical via `git restore` before report. No quick fixes authorized. Final restoration proof: `git status --porcelain` EMPTY · `git stash list | wc -l` = 17 · `git rev-parse HEAD` = `840483907e076912cb2e6178a078d87964d1432e` (unchanged) · `git diff` EMPTY.

---

## Code Changes Summary — NONE (independent verification round)

All work was read-only (audit, e2e, dispatch, marker edge probe) + the temporary audit-break mutation (reverted). Worktree ends identical to start: HEAD `840483907e076912cb2e6178a078d87964d1432e`, status clean, stash count 17, no commits.

---

## Documentation Updated

- [x] **NEW:** `.agents/tester/RESULTS/2026-10-04-chart-image-delivery-independent-verification.md` (this file)
- [ ] `.agents/tester/rules/ensure.md` — user-owned, no changes
- [ ] `.agents/tester/PACKS.md` — no chart packs were registered; verification runs were pytest-direct per discovery (no pack scripts exist for chart-image-delivery in `test/packs/`); no change
- [ ] `.agents/tester/MOCK_TESTS.md` — N/A (no mock-test scaffolding in this round; e2e is hermetic in-process)
- [x] Recommend a follow-up LESSONS entry capturing: (a) slack_sdk `files_upload_v2` `channel=` vs `channel_id=` drift class + the audit-pin #8 regex fragility — both are reusable findings.

---

## Final verdict (mirrored at top)

**❌ NOT USER-STORY-VERIFIED.**

### Blocking
1. 🔴 **CRITICAL — Slack native upload production-broken.** `daemon/sources/adapters/slack/adapter.py:622` passes `channel_id=channel_id` to `files_upload_v2`; slack_sdk 3.42.0's real signature is `channel=channel_id`; binding collision at `slack_sdk/web/client.py:4112` → `TypeError: ... got multiple values for keyword argument 'channel_id'`. Test `tests/test_slack_adapter.py:2039` asserts `"channel_id" in upload_call["kwargs"]` — wrong kwarg asserted, bug locked in. Two independent repros confirm. Fix: one-line code change (`channel_id=channel_id` → `channel=channel_id`) + flip the test assertion + add a real-`AsyncWebClient.files_upload_v2` integration test with the network stubbed.

### Should-fix
2. 🟠 **IMPORTANT — Audit pin #8 regex is permissive.** `kwargs\["file"\]\s*=\s*file` matches comments AND the `file_obj` prefix at `adapter.py:1778`. Suggested: tighten to `kwargs\["file"\]\s*=\s*file\s*(#|$)`. Audit still binds to a deliberate removal (proven by break-check); just not to nuanced edits.

### Declared residuals (held by release process / out of scope)
3. 🟡 **SOFT — Real mmdc→PNG render not proven in-repo.** True-render cases in `test_charter_render_capture.py` are `@pytest.mark.integration` and require a live charter (N6-blocked per task brief: *"real-platform smoke is N6-blocked (no live daemon serves the worktree; fresh boot hard-fenced) — do NOT boot a daemon; the release cut is held on that item separately"*). E2e uses a fixture PNG; byte-fidelity through the real chain is proven, but the renderer itself is unproven in-repo. **Held separately.**
4. 🟡 **SOFT — No single-file e2e drives `generate_chart` → marker → parent-text passthrough.** G7 shortcuts by authoring the marker into the AI message directly. Covered piecewise: charter render capture pins Step 6c emit/suppress + chart tools pins tool mechanics + audit pins passthrough adjacency `ari/workflow.md:525/527` + dispatcher pins both seams. Real-passthrough inside a live charter instance is the same N6-blocked integration as above.

### Cosmetic (carry in remediation)
6. 🟢 `test_image_get_optional_24h_freshness_window` (`tests/test_chart_image_delivery_e2e.py:824-869`) — both-branches-pass by own docstring; declared "optional-featured"; must not be cited as 24h-window verified.
7. 🟢 Tautological "e2e twin" byte-stability assertion (`:476-478`) — asserts f-string against itself; load-bearing assertion is sibling `:414-448`.
8. 🟢 Stale "skip-if-daemon" docstring + dead `socket` import (`:23-43`, `:57`) in the e2e file.

---

## Honest bookkeeping

- **619 vs 591:** 619 figure appears to double-count the e2e file (24 deselected on the standard leg + 4 deselected on the integration leg = 28; 563 + 28 + 28 = 619). My independent run gives 591 executed-passed (zero fail) across both legs. No missing coverage; just an accounting artifact.
- **Slack suite green is honest but silent** — the suite passes because every relevant test stubs `_safe_api_call` (or `_do_api_call`) above the real client and asserts the wrong kwarg. The drift is not in the test pass/fail; it's in the kwarg the test pins the wrong direction. Production breakage is 100% silent in CI.

---

## Action needed

- [ ] Fix Slack adapter `channel_id=` → `channel=` at `daemon/sources/adapters/slack/adapter.py:622`
- [ ] Flip test assertion at `tests/test_slack_adapter.py:2039` (and any sibling sites asserting `channel_id`)
- [ ] Add a real-`AsyncWebClient.files_upload_v2` integration test with the network layer stubbed (catches this drift class)
- [ ] Tighten audit pin #8 regex
- [ ] Optional: add a single-file e2e that drives `generate_chart` end-to-end and asserts the marker rides into the parent's final response (closes the SOFT coverage gap; pre-condition for retiring the g7 marker-authoring shortcut)
- [ ] Hold the real-platform smoke (N6-blocked item) — release-cut ceremony already defers this per `release-report.md`

Once items 1–3 are addressed, re-run the 619-count-equivalent + the audit-script + the e2e both legs and the feature is ready to cut through the v0.16.13 promote.