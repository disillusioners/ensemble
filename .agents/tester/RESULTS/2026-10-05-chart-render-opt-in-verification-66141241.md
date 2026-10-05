# Whole-Branch Verification — chart-render-opt-in @ 66141241 (merge gate)

**Date:** 2026-10-05 (UTC)
**Verifier:** Tester (independent whole-branch verification round 3)
**Feature / branch:** `feature/chart-render-opt-in` @ `66141241ccf032933572124cbbd64af75018af7b`, base `52ab3b6e` (v0.17.0)
**Worktree:** `/home/nea/ensemble-src-wt-render-opt`
**Commit chain verified:** `52ab3b6e` → `617bf69a` (option flag) → `1675ce11` (review fixes) → `3119556e` (smoke fold-in: 6 install fixes + memory doc + backlog) → `44cf90d7` (hardening W1-W5) → `66141241` (fold-in test repair) — all 5 present, in order.
**Workers (13):** `cid3-discover` `cid3-contract-read` `cid3-fold-docs` `cid3-chart` `cid3-charter` `cid3-dispatcher` `cid3-discord` `cid3-tgslack` `cid3-outbound` `cid3-auditpy` `cid3-e2e-int` `cid3-audit` `cid3-mutations`

---

## TL;DR — Final verdict

**✅ USER-STORY-VERIFIED (opt-in)** — with 4 recorded test-hardening follow-ups (prompt-side exec-block class, same deferral family as the original feature's F5/N6) and 2 non-blocking doc flags.

Default path is pure by construction and pin: default `False` declared + gate fail-closed on all 10 malformed forms (exec-tested) + per-call directive with no caching in production. Opt-in path is the byte-stable v0.17.0 chain — Group 7 integration lane 4/4 green with the e2e file untouched by this branch. Timeout wiring complete: all 4 combos pinned with literal assertions that provably catch a 600↔1200 swap (mutation-proven). Both fold-in commits verify: 6/6 install-fix↔memory-doc mapping; two-sided pin fires independently on orthogonal mutations. Audit 31/31 ×2 idempotent; mutation of the default flips 6 tests + audit pin #25 red. **587 passed / 0 failed** across all named suites + e2e integration lane, my counts.

---

## T4/T2 — Full regression, MY counts @ 66141241

| Suite | Mine | Notes |
|---|---|---|
| tests/test_chart_tools.py | **31 passed** | grew 24→31 (`TestGenerateChartRenderImageOptIn`) |
| tests/test_charter_render_capture.py | **69 passed / 4 deselected** | grew 44→69 (RENDER_IMAGE gate tests) |
| tests/test_sources_dispatcher.py | **78 passed** | unchanged |
| tests/test_discord_adapter.py | **196 passed** | unchanged |
| tests/test_telegram_adapter.py + test_slack_adapter.py | **160 passed** (45+115) | unchanged |
| tests/test_outbound_image_delivery.py | **6 passed** | unchanged |
| tests/test_chart_image_delivery_audit.py | **43 passed** | unchanged (pytest mirror) |
| tests/test_chart_image_delivery_e2e.py — integration lane | **4 passed / 24 deselected** | Group 7 astream; e2e file byte-identical to v0.17.0 (zero commits touch it on this branch) |
| **TOTAL executed-passed** | **587 / 0 failed** | + 8 structural deselections (4 charter integration/slow + 24-deselect on the -m integration run) |

**Audit script:** 31/31 ×2 (sha256-identical idempotency `33074ced…`), `--class preservation` 9/9. The 7 NEW feature pins #25-31 map 1:1 to the opt-in contract (default False / 1200-600 constants+dynamic / RENDER_IMAGE directive / workflow Step 5 conditional / Step 6 conditional / skill docs / 20-agent qualifier).

## T1 — Default-path purity

- **Default declaration — STRONG:** `render_image: bool = False` at `daemon/tools/chart_tools.py:414`; pinned by `test_render_image_flag_declared_with_false_default` (charter suite :965, regex on source) — mutation-proven (flip → red, below).
- **Gate fail-closed, 10 malformed forms — STRONG:** `_RENDER_DIRECTIVE_RE = ^RENDER_IMAGE:\s*(true|false)\s*$` in `agents/charter/workflow.md:125-131` (exec-extracted from the workflow doc — code/doc drift impossible); `test_render_image_gate_contract_malformed_is_false` parametrizes exactly 10 forms (`True`, `TRUE`, `yes`, `1`, missing value, lowercase key, `truex`, `false true`, plural key `RENDER_IMAGES`, space key `RENDER IMAGE`) — all assert `is False`. Deliberate loose-whitespace surface separately pinned (`:929`) as a future-tightening canary.
- **Reuse-lane per-call flag — PINNED (structural):** `chart_message` built fresh per call (`chart_tools.py:477-481`), `timeout_s` selected per call (`:466`), gate parsed at charter execution time; no module-level cache exists (read-verified). Tests assert the per-call `RENDER_IMAGE: true|false` directive in `enqueue_kwargs["message"]` on the reuse lane.
- **Dispatcher no-op on marker-less content — PRESERVED:** v0.17.0 tests (`test_full_chain_api_caller_keeps_marker`, degraded-path suite) prove no marker → `outgoing.images` empty, text delivered, byte-clean.
- **Hole (H1):** no exec-test runs gate-False + Step 6b together asserting `image_save` never called; and (H2) no direct chat-source e2e under False. Both live in the charter LLM-following link — same class the original feature deferred to live smoke; hermetic mitigation sketches recorded below.

## T2 — Opt-in path integrity (= v0.17.0 chain)

- **Marker regex byte-stable — STRONG:** `daemon/sources/dispatcher.py:29-32` untouched (zero commits touch dispatcher.py or the e2e file on this branch); locked-form pins inherited intact.
- **Both-seam extraction + native upload — STRONG:** Group 7 integration lane 4/4 @ branch tip (user-receives-png, progressive-marker-extracted, failure→completed, internal-source-no-extract); `TestStoreDeleteAfterUpload` + sealed-artifact tripwire intact.
- **Hole (H3, moderate):** the Step 6b exec-test captures `image_save` kwargs but doesn't assert `feature="chart-render"`/`retention_class="normal"`; partial pin exists via the `image_list(feature='chart-render')` literal in the cap-math test. One-line assertion upgrade recommended.
- **Hole (H4, cosmetic):** "marker is the LAST line" is a charter convention (workflow.md :490-498/:519-528) — unpinned; dispatcher extraction is position-independent (greedy MULTILINE), so this is prose-contract, not runtime contract.

## T3 — Timeout wiring: all 4 combos STRONG

| Combo | Test | Assertion (literal) |
|---|---|---|
| Fresh × True | `test_chart_tools.py:1110` | `assert kwargs["timeout"] == 1200.0` |
| Fresh × False | `:1070` | `assert kwargs["timeout"] == 600.0` |
| Reuse × True | `:1182` | `startswith("Error: Charter timed out after 1200.0s")` |
| Reuse × False | `:1214` | `startswith("Error: Charter timed out after 600.0s")` |

Plus hoisted constants pinned twice (`test_timeout_constants_match_spec` :1218-1221; charter suite :1041-1060 pins constants AND the dynamic-selection regex `timeout_s = _RENDER_TIMEOUT_S if render_image else _DEFAULT_TIMEOUT_S`). Production: `chart_tools.py:57-58` (1200.0/600.0), `:466` (selection), `:520/:548` (both wait paths), `:162` (reuse default). **Swap-catch proven by Mutation 3** (below): flipping the default produced exactly the `1200 vs 600` assertion failures on 5 timeout tests.

## T5 — Fold-in commits

- **3119556e (T5a): 6/6 clean.** All six committed install-path fixes map 1:1 to memory-doc §§1-6: (1) `-w 1200`→`--size 1200`; (2) inline `-c JSON`→staged temp-file with dual-path cleanup; (3) dropped `ulimit -v` (chromium 154 VA); (4) PATH prepend of `dirname $mmdc_bin` (nvm vs system node); (5) top-level puppeteer cfg derivation (jq flatten); (6) `--no-sandbox` fallback jq `.args` fix + mmd_json temp-leak fix. Prose sync + decisions.md backlog line (image_save transport corruption) also present.
- **66141241 (T5b): two-sided pin PROVEN discriminating.** Pin = `test_verify_toolchain_passes_on_real_evidence` (:683-695). Side A (:689) regex `-c\s+/tmp/charter-mmdc-cfg\.\w+\.json\b` on the ARGS log; Side B (:692) literal `'"securityLevel":"strict","htmlLabels":false'` in the lib source. Mutations: (A) revert temp-file form to inline at both `-c` sites → **RED at :689**; (B) change `strict`→`safe` in the staging printf → **RED at :692**. Each side fires independently on orthogonal mutations. No false positives/negatives.

## T6 — Test-the-test (default flip)

`render_image: bool = False` → `True` at `chart_tools.py:414` (sole diff): **6 tests red across BOTH suites** — 5 timeout tests failing on literal `1200.0 == 600.0` / startswith mismatches (fresh delegate :163, fresh spawn :345, reuse-timeout :646, default-600 fresh :1070, default-600 reuse :1214) + the declaration pin `test_render_image_flag_declared_with_false_default` (:969). **Bash audit simultaneously RED: pin #25 fails, `TOTAL: 30/31, EXIT:1`.** Revert → 100 passed / 4 deselected + audit 31/31. The reviewer's claim is re-proven with a wider red set than the expected minimum.

## T7 — Docs/version

- **0.17.1 both homes** ✓ (`daemon/__init__.py:3`, `pyproject.toml:3`).
- **CHANGELOG `## [0.17.1] — 2026-10-05`** ✓ with the full render_image opt-in entry (default-flip semantics, when-to-pass-True guidance, d5_timeout 1200/600 differential). **Flag (non-blocking):** the dry-run v3.2 entry is INTACT but lives under `[Unreleased]` self-labeled "branch only — merge pending" (`feature/dry-run-projection-v3.2`), not inside `[0.17.1]` — filing question for the planner, not a branch defect (this branch's review-fix commit 1675ce11 restored it).
- **skill.md** ✓ — Chat Delivery mandatory line (:26) + explicit-image-only `render_image=True` guidance (:29) + dedicated `## render_image: opt-in rendering` section with the differential table; 20-agent qualifier line confirmed across 10 sampled `rule.md` files.
- **decisions.md append-only** ✓ — `+123 insertions / −1` where the −1 is the prior file's missing-trailing-newline marker; effective append-only with the branch's own header declaring it (`§chart-render-opt-in-impl-record`, §opt-in-1…6).
- **uv.lock pins 0.17.0** ✓ (expected promote-time fold; `uv lock` at promote closes it). **Flag (non-blocking):** no in-branch note of the delta — the acknowledgment lives in the leader dispatch context; suggest one line in the promote checklist.

## T8 — Restoration proof

`git rev-parse HEAD` = `66141241ccf032933572124cbbd64af75018af7b` (unchanged) · `git status --porcelain` EMPTY · `git stash list | wc -l` = 17 (unchanged) · `git diff` EMPTY · final smoke: both mutated suites green (100 passed) + audit 31/31 exit 0. No commits/pushes, no daemon boot, other checkouts/`.env`/`~/.config` untouched, port 8088 untouched.

## Skip/xfail sweep

Zero new `pytest.skip` / `xfail` / `skipif` / early-return guards added to `tests/` by this branch (diff-grep clean; only prose uses of the word "skip" describing the gate's skip path). Pre-existing 4 skips (3 live-charter HARNESS + 1 SLOW ~200MB nvm) are inherited from v0.17.0, untouched.

---

## Recorded follow-ups (non-blocking)

1. 🟠 **H1** — add exec-test: gate-False + Step 6b block together, assert `image_save` not in captured calls (closes the "no tmp_images write under False" hermetic gap).
2. 🟠 **H2** — add chat-source-seam test: False-path response → `dispatch_message(source=mock-chat)` → assert `images` empty + no `ens-img` residue.
3. 🟡 **H3** — one-line upgrade in `test_store_precheck_allows_persist_under_cap`: assert `saved[0]["feature"] == "chart-render"` and `saved[0]["retention_class"] == "normal"`.
4. 🟢 **H4** — optional: pin marker-last-line convention via a Step 6d template exec-test (dispatcher is position-independent; cosmetic).
5. 🟢 Promote checklist: `uv lock` regen (0.17.0→0.17.1) + optional one-line note of the lock delta; confirm dry-run v3.2 entry placement rides its own merge.

## Verdict

**✅ USER-STORY-VERIFIED (opt-in) @ 66141241** — validation mandatory + default-pure (no render/PNG/tmp_images/marker; dispatchers no-op), explicit-image-only True path reproducing the byte-stable v0.17.0 chain end-to-end, 1200s/600s differential wired on both wait paths and mutation-proven. Merge gate: **PASS from the testing lane.**
