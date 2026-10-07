# Plugin Subsystem Slice ① — RE-VERIFY of fix commit c2a56399 (offline pin discrimination)

- **Date**: 2026-10-06, completed 18:35 UTC
- **Commission**: slice ① re-verify after fix commit `c2a56399` (parent `c5fe025e`), same worktree `/home/nea/ensemble-src-wt-plugin-subsystem-01`, branch `feature/plugin-subsystem-01`. Report-only; zero modifications/commits.
- **Workers** (reused, revived): `slice01-suite-run` (1c19528f, test-pack-execution) · `slice01-git-state` (7b886250) · `slice01-negcheck-greps` (afa7f0db)
- **Overall verdict**: 🟢 **GREEN** — prior yellow flag 1.1c **CLOSED**; all 6 re-verify checks pass (RV3 with one new low-severity flagged gap, decision point for leader)
- Prior pass: RESULTS/2026-10-06-plugin-slice01-independent-test-pass.md (baseline 114P/0F @ c5fe025e)

---

## RV1 — Scoped suite @ c2a56399: PASS

- Preflight: HEAD `c2a56399ff7c4063a1681ecee8d8ff026ab13468`, parent `c5fe025e`, porcelain clean, daemon import inside worktree, Python 3.14.7
- Command: `timeout 300 .venv/bin/python -m pytest tests/unit/plugin_subsystem/ -q --tb=short -p no:cacheprovider`
- Counts: **131 passed / 0 failed / 0 errors / 0 skipped** (1 pre-existing langchain_core Pydantic warning) — **claim exact match**; pytest 2.24s / wall 6.40s, 0 retries
- Delta vs 114 baseline: **+17** confirmed (9 range-marker + 5 reserved-literal + 1 A-path adapter + 2 fallback-engagement per commit body; sub-decomposition verified in RV2–RV4)

## RV2 — 1.1c re-check (branch-pin refusal): PASS — finding CLOSED

- Coverage: `TestPluginBlockRefusals::test_non_tag_pin_reserved_literal_refused` parametrized over `["HEAD","main","master","develop","latest"]` — 5 case IDs, each asserts `non_tag_pin`
- Ad-hoc firing (validate_manifest() from /tmp): `main`/`HEAD`/`latest`/`master`/`develop` → ALL **REFUSED [non_tag_pin]**, message: "reserved git literal … refused offline in v1; full branch-vs-tag discrimination lands at slice ③"
- **Over-refusal guard: NONE** — `v0.16.1` ACCEPTED ✓, `v1` ACCEPTED ✓
- Intentional residual (documented in source + test docstrings): a real upstream tag literally named `main` etc. is refused until slice ③ — conservative-by-design, do not "fix" backwards

## RV3 — Range markers: PASS with 1 new low-severity gap 🟡

- Marker set: `_RANGE_PIN_CHARS = ("^","~",">=","<=",">","<","*","x","X")` — 9 markers (`manifest_reader.py:138`)
- Coverage **1:1 by construction**: `@pytest.mark.parametrize("marker", _RANGE_PIN_CHARS)` imports the source tuple (`test_manifest_reader.py:342`, import at :21) → case IDs `test_non_tag_pin_range_marker_refused[^] [~] [>=] [<=] [>] [<] [*] [x] [X]` — a marker added to the tuple auto-gains a case
- Adjacent spot-fires: `^1.2.3`, `~1.2.3`, `*`, `v1x0.0` → REFUSED [non_tag_pin] ("range expression" message) ✓
- **🟡 GAP: `v1..v2` → ACCEPTED.** `.`/`..` absent from the marker tuple; not reserved-literal/hex/empty → passes all offline checks. `..` is the git revision-range separator and refnames CANNOT contain it (`git check-ref-format`) → provably never a tag → contradicts the commit's stated "refuse everything provably-not-a-tag OFFLINE" as literally worded. **One-line fix**: add `".."` to `_RANGE_PIN_CHARS` (parametrized test auto-covers). Severity LOW: slice-③ git consultation would still refuse it. Decision point: fold into next commit vs defer knowingly to slice ③.

## RV4 — Fallback warning: PASS

- Warning test: `TestFallbackPathEngagement::test_fallback_engagement_warning_fires` (`test_manifest_reader.py:559`) — forces `manifest_reader._HAS_JSONSCHEMA=False` via monkeypatch; asserts RuntimeWarning via `warnings.catch_warnings(record=True)` + simplefilter("always"), filtered on category + "manifest_reader" in message
- Battery under fallback: `::test_fallback_path_agrees_on_refusal_battery` (:578, 3-case (code,location) equality) + cross-file `test_dual_path_equivalence.py::test_paths_agree_on_refusal_battery[key]` (9 keys: valid, unknown_top, unknown_nested, missing_required, wrong_type, empty_name, enum_violation, array_item_type, divergence_extra_field)
- Ad-hoc re-fire (sys.modules jsonschema delete + flag flip, restored in finally): invalid manifest → REFUSED [unknown_field] AND RuntimeWarning at `manifest_reader.py:665` ("jsonschema unavailable — engaging bounded mini-validator fallback"). Degraded mode is loud ✓

## RV5 — Sentinel greps re-run: PASS

- (a) Vocabulary: all 5 terms confined — OTHER hits remain only `.md` planning docs + stale `.pytest_cache` nodeids; **zero code hits** outside allowed homes; frontend/docs 0 hits. `fence_grant` 28 total / `divergence_register` 27 total (small rise = new tests + CONVENTION.md rows, all in allowed homes)
- **VOCABULARY_STRINGS now a 6-tuple incl. `fence_grant` (:26) and `divergence_register` (:27)** (`test_sentinels.py:21-28`) — prior recommendation CLOSED
- (b) Dynamic loading: `importlib` = 3 comment/docstring mentions (negative assertions), zero usage; pkg_resources/entry_points/__import__/pkgutil/importlib.metadata = 0
- (c) Tier-1 isolation: `grep -rn "plugin_subsystem" daemon/ --include="*.py" | grep -v "daemon/plugin_subsystem"` → **0 hits**

## RV6 — Branch state + fix-commit context: PASS

- HEAD `c2a56399` exact, parent `c5fe025e`, branch `feature/plugin-subsystem-01`, porcelain clean, **4 commits ahead** of `d262c5e8` (`610b8096 → db44c8e9 → c5fe025e → c2a56399`)
- Fix commit touches **4 files, 0 out-of-prefix**: `manifest_reader.py` (+51/−1), `CONVENTION.md` (+36/−1), `test_manifest_reader.py` (+121), `test_sentinels.py` (+9). Single author, no rebase separation.
- Commit message documents: offline refusal of reserved literals + range markers (all map to existing `non_tag_pin`, no new codes), slice-③ deferral for full discrimination, RuntimeWarning on fallback engage, fence-routing contract documentation, sentinel vocab closure

---

## Open items for the leader

1. 🟡 **`v1..v2` accepted** (RV3) — `..` missing from `_RANGE_PIN_CHARS` though refnames can never contain it. One-line fix with auto-test-coverage, or record as known-deferral to slice ③. LOW severity (vendoring-time git consultation catches it).
2. 🔵 **Slice-② dependency note**: offline pin contract is now STRICTER than at c5fe025e — slice ② fixtures/specs must not use `main`/`latest`/reserved literals or range markers as `tag_pin_per_class` values. Ping architect if slice ② planning predates c2a56399.
3. Closed this pass: 1.1c branch-pin acceptance (my prior yellow flag), sentinel vocab gap (fence_grant/divergence_register now in VOCABULARY_STRINGS), fallback-degradation silence (RuntimeWarning now fires).

## Boundary compliance

No boots · no ~/agents-ensemble (9797) / ~/agents-ensemble-demo (7979) / systemd / OD touches · port 8088 untouched · no key/credential changes · zero tracked-file modifications · zero commits · scratch confined to /tmp/slice01-negcheck/ · `-p no:cacheprovider` used (shared-worktree cache preserved).
