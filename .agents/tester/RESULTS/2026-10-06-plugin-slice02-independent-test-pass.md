# Plugin Subsystem Slice ② — Independent Test Pass (plugins/opendesign skeleton + copy_freely vendoring @ pinned tag)

- **Date**: 2026-10-06, completed 19:25 UTC
- **Commission**: Overnight build — slice ② independent verification (report-only, no modifications, no boots)
- **Worktree**: `/home/nea/ensemble-src-wt-plugin-subsystem-02`, branch `feature/plugin-subsystem-02`, HEAD `46171654838904d9550ca715699725b79ed9e88f`, base `8be590c0` (6 commits)
- **Workers**: `slice01-suite-run` (1c19528f, test-pack-execution, reused) · `slice01-git-state` (7b886250, reused) · `slice02-registry-cirunner` (c6386792, fresh) · `slice01-negcheck-greps` (afa7f0db, reused)
- **Overall verdict**: 🟢 **GREEN** — 7/7 checks PASS (S2-2 after documented CWD adaptation); 1 non-blocking ergonomic defect flagged (HASHES placement); slice-① LOW gap (`..`) confirmed CLOSED in this branch
- Code changes by tester: NONE

---

## S2-1 — Scoped suite: PASS

- Preflight: HEAD exact, porcelain clean, `.venv` pre-existed (lazy rule did not bite), `daemon.__file__` inside worktree
- **Python 3.13.15** on this venv (not the 3.14 target) — plugin_subsystem scope does not trigger the string-union rot; suite green on 3.13 here AND 3.14.7 in slice ① (dual-interpreter confirmation for this scope). Later slices hitting forward-ref patterns should re-sync.
- Counts: **187 passed / 0 failed / 0 errors / 0 skipped** — claim exact match; pytest 4.18s / wall 8.33s, 0 retries. Delta +56 vs slice-① (131).

## S2-2 — Byte-fidelity: PASS (after CWD adaptation) + 🟡 ergonomic defect

- **The commissioned cwd is itself wrong**: `cd plugins/opendesign && sha256sum -c copy_freely.HASHES.sha256` → exit 1, ALL 4881 paths unresolvable (9763 error lines). HASHES paths are relative to `copy_freely/`, NOT to the HASHES file's directory.
- Correct invocation: `cd plugins/opendesign/copy_freely && sha256sum -c ../copy_freely.HASHES.sha256` → **exit 0, 4881 `: OK` / 0 `: FAILED` / 0 warnings / 0 stray lines**; HASHES file itself = 4881 lines.
- Independent recomputation spot-check: `craft/FUTURE_SECTIONS.md` → `e95ccf61e166cfc3dc7266bfaaabb3e8cec1dc9c8d134eb63ad064343aa7d9bc` — matches manifest entry.
- 🟡 **Ergonomic defect (non-blocking)**: HASHES at plugin top level with copy_freely/-relative paths breaks the obvious verify command (dev's reviewer + my worker both hit it). Fix options: (A) relocate to `copy_freely/HASHES.sha256` [recommended, conventional co-location]; (B) prepend `copy_freely/` to all paths. **Fix before slice ③'s second-tag clean-pull dry-run reuses this verification.**

## S2-3 — Class counts: PASS

- Actual dirs: `craft` / `design-systems` / `design-templates` / `prompt-templates` = **13 / 154 / 115 / 106 = 388 exact** (ls-1 and find -mindepth1 -maxdepth1 agree — zero hidden-entry drift)
- prompt-templates: top level = 2 subdirs (`image/`, `video/`); **recursive JSON = 106** (48 image + 58 video); **0 non-JSON strays** (recursive find -type f ! -name '*.json')
- Note: commission wording said "top-level" for the 106 — actual semantics is recursive; dev's number correct, wording was the tester's (owned).

## S2-4 — Registry e2e: PASS

- API: `daemon.plugin_subsystem.plugin_registry` — `scan_plugins_root(plugins_root, validate_tree=True)` → (declarations, refusals); `load_registry`; validation routes through slice-① `read_manifest` (manifest_reader.py:706 ← plugin_registry.py:308); skeleton codes root_symlink / class_subdir_is_symlink / class_subdir_not_a_directory
- Real-tree invocation (cwd = worktree root): declarations = `{opendesign}`, **refusals = {}**; fields round-trip: Apache-2.0, integration_path C, execution_mode resource-only, schema 1.0.0, source_dir; `has_failures()=False`; independent `validate_manifest(plugins/opendesign, validate_tree=True)` → ok=True, refusal=None
- **Pin `open-design-v0.23.0`**: not in _RESERVED_PIN_LITERALS (HEAD/main/master/develop/latest), not range-marked, not hex-SHA (regex `^[0-9a-fA-F]{7,40}$` no match) → **PASSES offline tag-shape check**
- Control probes alive: reserved literals / range markers (`^v1`, `~v1`, `>=v1`, **`a..b`**) / hex SHAs (`deadbeef`, `0123456789abcdef`) all refused
- 🎁 **`..` IS now in `_RANGE_PIN_CHARS`** → slice-① re-verify LOW gap (`v1..v2` accepted) is **CLOSED in this branch**

## S2-5 — CI runner on real tree: PASS

- `timeout 120 .venv/bin/python plugins-convention/ci_runner.py plugins --with-entrypoint-tripwire` → **exit 0**; JSON: checked 1 / passed 1 / failed 0 / **ok=true**
- Entrypoint tripwire: opendesign `"status": "not_applicable"`, message "no entrypoint declared; tripwire not applicable"; ok_overall=true; thresholds ALARM=220 / REFUSE=300 — correct pre-slice-⑤ C-path state
- Default mode (no flag) sanity: exit 0, ok=true, tripwire key absent as documented

## S2-6 — Sentinels (updated zone model): PASS — zero violations

**(a) Vocabulary confinement** (allowed: daemon/plugin_subsystem/**, plugins-convention/**, tests/unit/plugin_subsystem/**, + sanctioned plugins/opendesign/{MANIFEST.yaml, CURATION.md}):

| Term | Total | d/ps | plugins-convention | tests | carve-out | OTHER (benign) |
|---|---|---|---|---|---|---|
| execution_mode | 122 | 30 | 12 | 41 | MANIFEST 3, CURATION 2 | 34 |
| lifted_symbol | 29 | 4 | 2 | 15 | 0 | 8 |
| hosted_runtime_deps | 20 | 8 | 3 | 6 | 0 | 3 |
| fence_grant | 32 | 11 | 8 | 6 | 0 | 7 |
| divergence_register | 27 | 9 | 6 | 6 | 0 | 6 |

OTHER = planning `.md` docs + `.pytest_cache` nodeids only. **The 4881-file vendored corpus (incl. 57 vendored `.py`) is vocabulary-SILENT** — the only plugin files speaking vocab are exactly the two sanctioned ones. Zero code hits outside zones; frontend/docs 0.

**(b) No runtime loading** (scope: 7 tier-2 modules incl. new plugin_registry.py 329 ln + entrypoint_tripwire.py 286 ln, plugins-convention/, ALL of plugins/opendesign/, tools/vendor/od_vendor.py): importlib = 6 hits ALL negative-assertion comments (incl. 2 new module docstrings + CURATION.md:212 documenting the invariant over the vendored tree); pkg_resources/entry_points/__import__/pkgutil/importlib.metadata = 0. **Zero usage.**

**(c) Tier-1 isolation with sanctioned exceptions**:
- (i) `grep -rn "plugin_subsystem" daemon/ --include="*.py" | grep -v "daemon/plugin_subsystem"` → **0 hits**
- (ii) pyproject.toml delta = one hunk +5 lines (`"jsonschema>=4.0.0",` + 4-line rationale comment); uv.lock delta = **+2 lines only** (dependency-list + requires-dist entries) — **zero package lock entries added/removed**; jsonschema + transitives (referencing, attrs, rpds-py, jsonschema-specifications) already in base lock. Exactly a transitive→direct promotion, no churn. **ONLY-jsonschema: YES**
- (iii) `tools/vendor/od_vendor.py` = +375 lines, under tools/ (tooling, not daemon/) ✓

## S2-7 — Branch state: PASS

- HEAD `46171654838904d9550ca715699725b79ed9e88f` ✓ · branch ✓ · porcelain clean ✓ · **6 commits** on 8be590c0 ✓
- Commits: 41f684ceb (carry-forwards 1-6) → 7a9bfb30b (tier-2 plugin_registry) → f272bbb9e (vendoring tool + sentinel carve-out) → db4a52967 (vendor copy_freely @ open-design-v0.23.0) → 1f190ce54 (manifest + LICENSE + CURATION.md) → 461716548 (review minors: hash-manifest audit command + filename guard)
- Diff: 4900 files, ALL in expected prefixes (4881 corpus + 4 plugin top-level + 4 daemon/plugin_subsystem + 3 plugins-convention + 5 tests + tools/vendor + pyproject + uv.lock). **0 out-of-prefix**

---

## Flags for the leader

1. 🟡 **HASHES placement defect** (S2-2) — non-blocking fidelity-wise (4881/4881 OK), but the obvious verify command fails; relocate (Option A) or rewrite paths (B) **before slice ③ reuses this verification**. Note: even the commission's suggested cwd (`plugins/opendesign`) is wrong — correct cwd is `copy_freely/`.
2. 🟢 **Slice-① LOW gap CLOSED**: `_RANGE_PIN_CHARS` now includes `..` (`a..b` refused, probe-verified) — my `v1..v2` finding from the c2a56399 re-verify is resolved in this branch.
3. ℹ️ worktree-02 venv is Python **3.13.15** (not 3.14) — green anyway; re-sync if later slices hit forward-ref rot patterns.
4. ℹ️ `integration_path: C` declared as "minimal faithful reading" (B-element for generate Port arrives slice ⑤) — pre-annotated in MANIFEST.yaml:24-41, flagged-not-buried. No verifier action.
5. ℹ️ Future-slice suggestion: sentinel suite should pin the integrity of `copy_freely.HASHES.sha256` itself (currently vocabulary-only sentinels).

## Boundary compliance

No boots · no ~/agents-ensemble (9797) / ~/agents-ensemble-demo (7979) / systemd / OD-daemon touches · port 8088 untouched · `/home/nea/opt/open-design` untouched (read-only reference honored) · no key changes · zero modifications/commits · scratch in /tmp only · `-p no:cacheprovider`.
