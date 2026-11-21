# Sonic Tester RESULTS — sonic-relicense-agpl3

- Lane: fast-path TEXT-ONLY verification wave, branch `relicense-agpl3` @ `25bceb790779936e570bb5888463a66e8c65fa1b`
- Base: `5f967f18d6794301749d097b67739d779cba15cd` (= `origin/latest` at read time — PACKS.md read is current)
- Worktree: `/home/nea/ensemble-src-wt-relicense` (pristine before this file; this RESULTS file is the ONLY untracked addition)
- Verdict: **TEST: PASS** — all 6 scoped checks green; no red attributed to the change.

## 1. Byte-fidelity gate (LICENSE)

- Committed `git show HEAD:LICENSE` → sha256 `0d96a4ff68ad6d4b6f1f30f713b18d5184912ba8dd389f86aa7710db079abcb0`, 661 lines / 5535 words / 34523 bytes.
- **Deviation disclosed**: direct `https://www.gnu.org/licenses/agpl-3.0.txt` fetch UNREACHABLE from this host (3 attempts: rc=28 timeouts ×2, rc=22/429 via web.archive.org ×3). Debian common-licenses has GPL-3 but NO AGPL-3 file.
- Fresh independent fetches performed instead:
  - `raw.githubusercontent.com/grafana/grafana/main/LICENSE` (verbatim gnu.org copy) → sha256 `0d96a4ff…` — **cmp: BYTE-IDENTICAL to committed**.
  - `mastodon/mastodon` + `searxng/searxng` LICENSEs → sha256 `57c8ff33c9c0cfc3ef00e650a1cc910d7ee479a8bc509f6c9209a7c2a11399d6` (34520 B); diff vs committed = exactly 3 lines, `http://`→`https://` FSF links — the known OLDER gnu.org revision. Committed carries the CURRENT canonical text.
  - `spdx.org/licenses/AGPL-3.0.txt` → 34254 B, SPDX-normalized variant (not byte-comparable, expected).
- Conclusion: committed LICENSE is the current canonical AGPL-3.0 text (34523 B matches the canonical gnu.org size; digest matches the canonical AGPL-3.0 sha256).

## 2. pyproject.toml metadata

- tomllib parse OK (uv-managed interpreter, no bare python, no main .venv).
- `project.license == "AGPL-3.0-only"` ✅; `classifiers` ABSENT ✅ (none owed); name/version unchanged (`ensemble` / `0.31.24`).
- **Coder's untested item CLOSED**: `uv build --sdist` (isolated hatchling env) → `ensemble-0.31.24.tar.gz`; PKG-INFO: `Metadata-Version: 2.5`, **`License-Expression: AGPL-3.0-only`**, **`License-File: LICENSE`**, ZERO `Classifier:` lines; sdist contains `LICENSE`, `README.md`, `pyproject.toml`.
- Observation (non-blocking, pre-existing): sdist is 112 MB (hatchling default inclusion, no sdist excludes configured — unchanged by this diff).

## 3. frontend/package.json

- Untouched in range (scope audit) AND valid JSON ✅; `license` field absent (correctly untouched).

## 4. Regression scan

- Greps over `tests/`, `scripts/`, `Makefile`, `ensemble.spec`, `stage.sh`/`lib.sh`/`promote.sh`/`rollback.sh`, `.github` (absent):
  - All `MIT`/`license` hits are PLUGIN-MANIFEST fixtures (`plugins/*/MANIFEST.yaml`, Apache-2.0/MIT) — `plugins/` untouched.
  - No test asserts README content ("MIT"), pyproject license absence, or root LICENSE absence/existence.
  - Tree-hash packs cover `releases/*/agents|frontend` and `payload/scripts` only — never root LICENSE/pyproject.
  - Only pyproject byte-dependencies: `bump_version.py` `version = "…"` exact-match (line still unique/intact) and `ensemble.spec` `('pyproject.toml', '.')` inclusion (path valid).
- **Verdict: none observe the change** — nothing to run.

## 5. Scope audit

- `git diff --name-only base..HEAD` == exactly `[LICENSE, README.md, pyproject.toml]`; `plugins/` count 0; single commit `25bceb790 docs(license): relicense to AGPL-3.0-only`; worktree clean.

## 6. Registered-pack applicability

- `.agents/tester/PACKS.md` read from the WORKTREE and cross-checked against `origin/latest` (POK rule — never canonical checkout): **no registered pack applies to a text-only/licensing lane**. Closest rows (`slice05g_foldins` plugin LICENSE, `gc_fe_build_budget` package.json, `v02812_staged_tree_pack` release bump) target different artifacts. Gate-verifiable=registered ⇒ nothing gate-verifiable applies here; no new pack produced (one-shot verification; branch writes excluded by commission).
