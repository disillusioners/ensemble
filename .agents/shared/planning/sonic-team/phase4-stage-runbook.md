# Phase 4 — Staging, promote-ready, NO promote: Runbook (S11)

**Plan set:** ENSEMBLE-31 "sonic team v1" · **Phase:** 4 of 4 (0 preflight, 1 content build, 2 verification, 3 dry-run, 4 staging)
**Owner:** W3 · **Status:** SPEC — this plan lane never executes any step below
**Branch:** `feature/sonic-team-v1` @ base `ac9849bf7` (= origin/latest at plan time)
**Inputs:** `findings-C-risk.md` §2d (stage/promote gates, provenance `lib.sh:3280-3356`, drift leg, rollback_safe derivation) · `findings-B-infra.md` §1 (baseline 181) · repo: `scripts/upgrade/stage.sh` usage header `:1-100`, `scripts/upgrade/lib.sh` `require_live_guard`, `scripts/bump_version.py`
**[Leg A] context:** 125-min task cap; business window ~arbitrary — staging is `allow-stale`-tolerant and non-disruptive; `… NO flip` (stage never touches `current`).

## 1. Preconditions checklist (ALL must be true; else STOP)

| # | Precondition | Check |
|---|---|---|
| C1 | **Phase 3 PASS** evidence exists (V1–V8, §5 of `phase3-dryrun-spec.md`) | evidence file path recorded in the morning report |
| C2 | **Pack green** — `sonic_agent_defs_unit_test` PASS on the branch tip (and registered in `PACKS.md`) | `timeout 300 bash test/packs/sonic_agent_defs_unit_test.sh` ⇒ `RESULT: PASS`; row exists (`grep -c sonic_agent_defs_unit_test .agents/tester/PACKS.md` ≥ 2) |
| C3 | **Prompt baseline regenerated + committed** — `agents/.prompt-artifacts.sha256` = 197 lines (181 base + 16 sonic rows, with the DEV-1 side-effect repair of 8 pre-existing drift rows changing row VALUES, not the count); `agents/ari/rule.md` seal row re-pinned in `tests/test_chart_image_delivery_e2e.py:1425` (base value `fc0a7542…` → new sha) | `python3 scripts/protection/generate_prompt_artifact_baseline.py --check --tree .` rc=0; `wc -l agents/.prompt-artifacts.sha256` = 197 |
| C4 | **Content-only diff** — zero `daemon/` and `scripts/` changes on the branch | `git diff --name-only ac9849bf7..HEAD -- daemon/ scripts/ | wc -l` = 0 |
| C5 | **Worktree clean** + **feature branch complete** + plan-set + findings committed on the branch (giter lane) | `git status --porcelain` empty; `.agents/shared/planning/sonic-team/` present on the branch |
| C6 | **Preflight safety ordinals** — no migrations (also a C4 consequence), no daemon code | `git diff --name-only ac9849bf7..HEAD -- daemon/migrations/versions/ | wc -l` = 0 |
| C7 | **Live lane guard understood** — staging live requires `ENSEMBLE_UPGRADE_LIVE=1` (`require_live_guard`, `scripts/upgrade/lib.sh`); this runbook stages live (the only install carrying sonic); **NO PROMOTE** (§4) | operator confirms |

## 2. Ordered steps with exact commands

**Stage-side sequencing (strict order):** merge → tag → clean checkout AT the tag → FE pre-build → `stage.sh` — all stage-side steps run from the SAME clean tagged checkout; the sonic commission worktree (`ensemble-src-wt-sonic-v1`) is only the content-build lane and is never the staging tree. The bump lands between merge and tag (§2.3). All commands below run from that **clean checkout at the tagged commit** (see §2.5–§2.6). CWD selects the executing script (the v0.29.x ceremony precedent); `stage.sh` resolves `SCRIPT_DIR`/`REPO_ROOT` from its own location and requires `HEAD == git describe --tags --exact-match` (`scripts/upgrade/stage.sh:66-70, :180-191`).

### 2.1 Merge `feature/sonic-team-v1` → `latest` (giter contract)

Executed BY GITER (branch-flow/quiescence per `agents/giter/workflow.md:78-103`): worktree clean → `git merge --no-ff feature/sonic-team-v1` into `latest` → push `latest` + the feature branch → chained cleanup (`worktree remove` → `branch -d` from a checkout on latest → delete `wt.claim.*`/`wt.active.*` KV rows). Verifications: `git merge-base --is-ancestor feature/sonic-team-v1 latest` before/after; `git log --oneline -3 latest`.

### 2.2 Version derivation — AT BUMP TIME, never hardcoded

```bash
git fetch origin --tags        # local refs may diverge from origin (POK release-02)
git ls-remote --tags origin | awk -F/ '{print $NF}' | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -3
git tag --list 'v*' --sort=-v:refname | head      # cross-check the LOCAL view
```

Rule: take the highest tag `H` **as seen on origin** (cross-checked against the local list); next release `VERSION=<derived>` (expected patch bump of `H` this evening — highest today `v0.29.22`, no `v0.30.*` exists; **the plan hardcodes NO number**). **Record WHICH source was used and any local↔origin divergence** (local-only or remote-only tags) in the morning report — this host has documented local/origin tag divergence (POK release-02), so a local-only listing alone is not authoritative. Confirm `H` resolves to a commit (`git rev-parse "$H^{commit}"` prints a 40-hex sha) and that `v0.30.*` still does not exist. If a `v0.30.*` appeared since plan time, the derivation command is authoritative — do not adjust by hand.

### 2.3 Bump (version-bearing files + convention)

`scripts/bump_version.py` reads the CURRENT version from `pyproject.toml` (`get_current_version`) and rewrites exactly four files: `pyproject.toml`, `daemon/__init__.py`, `tests/test_api.py`, `uv.lock`; it then runs `uv sync` and makes a **targeted** commit (never `git add -A`) — the repo's 5-file bump convention adds `CHANGELOG.md` when the operator performs the `[Unreleased]` → `[<ver>]` rename.

**Actor = the giter lane (MF-4).** `decisions.md` D16/D17 route ALL commits through giter tonight; giter executes the **bump commit + the annotated tag (§2.4) + `git push origin latest $VERSION`** under the pre-approval envelope already recorded in the dispatch (bump + tag + push pre-approved — no per-step approval round-trip; the runbook lane performs **no merge op**). A lane that finds itself about to commit outside giter STOPS and hands off.

**Canonical-checkout fence (inline, binding):** **NEVER `/home/nea/ensemble-src`** — it is a DETACHED HEAD carrying a 6-entry staged index PLUS 15 files of unstaged WIP; a commit there would sweep both dirty sets. The bump lives in a clean checkout ONLY.

```bash
cd <clean checkout on latest @ merge commit>
python3 scripts/bump_version.py patch          # patch = the content-only convention; minor = leader's call (record it)
# VERIFY the derivation agrees with the script's arithmetic:
grep -n '^version' pyproject.toml ; grep -n '__version__' daemon/__init__.py
# both must equal VERSION=<derived>; mismatch ⇒ STOP (pyproject drifted from tags)
```

VERIFY-AT-IMPL: (a) confirm the exact interpreter/`uv` invocation used by prior bump commits (`git log --oneline --grep='bump'`); (b) confirm whether tonight renames `CHANGELOG.md` (if yes, include it in the same targeted commit); (c) if `bump_version.py` reports a partial bump (`!= 4 files`), STOP — a prior partial bump left exact-string matching broken.

### 2.4 Annotated tag on the bump commit

**Actor = the giter lane** (same pre-approval envelope as §2.3 — tag + push are pre-cleared; no separate round-trip). The runbook lane never tags or pushes.

```bash
git tag -a "$VERSION" -m "ENSEMBLE-31 sonic team v1 — 4 agent definitions (content-only: agents/ + ari note; zero migrations)"
git describe --tags --exact-match        # MUST print $VERSION  (stage.sh:180-191 refuses otherwise)
git push origin latest "$VERSION"
```

### 2.5 Frontend pre-build (REQUIRED before stage — UI-less releases are refused)

`stage.sh` hard-refuses (exit 78) unless `frontend/dist/frontend/browser` exists — "no frontend build … run 'bash scripts/upgrade/_build_frontend.sh' … refusing to stage a UI-less release" (`scripts/upgrade/stage.sh:324-332`, block incl. `:327-331`) — and it then VERIFIES the FE provenance sidecar (stage freshness guard L2, `stage.sh:333-350`). The wrapper `scripts/upgrade/_build_frontend.sh` does NOT install dependencies: it checks npm presence and hard-refuses with remedy text if missing (`:66-73`), runs bare `npm run build` in `frontend/` (`:84-88`; package.json `"build": "ng build"`), then stamps the sidecar as a SIBLING of the hashed file — `frontend/dist/frontend/browser/index.html.build-provenance.json` (`:103-105`). House order in the clean tagged checkout (deps first, then the wrapper — `npm ci` is valid: `frontend/package-lock.json` is committed):

```bash
cd <stage checkout @ $VERSION>/frontend && npm ci   # (1) deps — the wrapper never installs them
cd .. && bash scripts/upgrade/_build_frontend.sh    # (2) build + sidecar stamp (no argv, no env override)
ls -l frontend/dist/frontend/browser/index.html.build-provenance.json   # sidecar present ⇒ step PASS
```

- **Label quirk — do NOT gate on `build_tool`:** the sidecar's `build_tool` is stamped `npm run build:frontend` (`_build_frontend.sh:105`) although the wrapper actually runs `npm run build` (`:85`). Gate on sidecar PRESENCE + hash match — `stage.sh` performs that check itself (L2); never match on the label.
- **TypeScript safety:** the FE build via the wrapper / `npm run build` inside `frontend/` is the sanctioned TypeScript path — NEVER bare `tsc` / `npx tsc` outside `frontend/` (project critical note: npm package `tsc` ≠ TypeScript; bare npx outside a package dir fetchs + executes remote code).
- **BLOCKED path:** if node/npm is missing on the host, the wrapper refuses with remedy text (`_build_frontend.sh:66-73`) ⇒ record BLOCKED with that output and stop — do not improvise an alternate toolchain (VERIFY-AT-IMPL only for the PATH remedies the wrapper itself prints).

### 2.6 Stage (`stage.sh live`) — exact invocation

Run from the **stage worktree** — a clean checkout at the tagged commit (precedent: `/home/nea/ensemble-src-wt-stage-v<ver>` for v0.29.15; the sonic worktree at the tag is acceptable if clean). The staging tree must be at the integration tip (`_verify_tip_identity`, `scripts/upgrade/stage.sh:196-207`) with build provenance matching HEAD.

```bash
cd <stage worktree @ $VERSION>        # a NEW clean worktree at $VERSION — see §2.7
git status --porcelain                       # MUST be empty
ENSEMBLE_UPGRADE_LIVE=1 ENSEMBLE_ROLLBACK_SAFE=1 VERSION="$VERSION" bash scripts/upgrade/stage.sh live
```

First run: NO `--skip-build`, NO `--allow-stale-stage` (see flag table).

Flag policy — **required tonight vs premise-inapplicable**:

| Flag / env | Tonight | Why |
|---|---|---|
| `VERSION=<derived>` (env-first) | **REQUIRED** | Explicit version; must equal the exact tag at HEAD (`stage.sh:180-191`, exit 78 otherwise) |
| `ENSEMBLE_UPGRADE_LIVE=1` | **REQUIRED** | `require_live_guard` refuses live without it (`stage.sh:176` → `lib.sh:1431-1442`, exit 78). **A-STAGE-3:** this arms the target guard ONLY — no promote, no nonce, no `state.json` promote-field writes; the Emperor's no-live-promote boundary is untouched (standard house invocation, v0.29.15 precedent) |
| `ENSEMBLE_ROLLBACK_SAFE=1` | **REQUIRED — explicit form (SUPERSEDES the earlier "unset/derive" expectation in this row)** | `stage.sh:357-363` runs a FULL-HISTORY, comment-blind `grep -liE 'DROP\s+TABLE\|DROP\s+COLUMN'` over ALL `daemon/migrations/versions/*.sql`; ANY match ⇒ `contains_contract_phase=true` ⇒ the unset path **exits 78** (`:382-388`) — giter's preconditions audit: **106 legacy files match**, so the unset/default path is UNREACHABLE on this tree. Expected log token: `rollback_safe=true source=explicit=true` (`:371` + `:927`). Deliberate leader adjudication per A-STAGE-1 (§4) |
| `--skip-build` | **Only if** a prebuilt `dist/ensemble-prod` from the SAME commit with a valid provenance sidecar exists; otherwise omit so the stage builds and OWNS provenance (`stage.sh:207-290`) | A dirty/stale binary is refused by the provenance gate |
| `--allow-stale-stage` | **NOT APPLICABLE — do not use** | It unlocks tip-identity + provenance refusals (`stage.sh:87-95`). If stage refuses, STOP and fix the tree — never override tonight |
| `ENSEMBLE_BINARY_VERSION` | not needed | defaults to `${VERSION#v}` (`stage.sh:403-404`) |
| demo guard (`ENSEMBLE_INSTALL_DEMO`) | not needed | target is `live`, not demo |

Stage expectations: exit 0; log line `staged release $VERSION at $REL (rollback_safe=true source=explicit=true … contains_contract_phase=true …) — NO flip performed` (`stage.sh:927`; the explicit path sets `ROLLBACK_SAFE_SRC="explicit=true"` at `stage.sh:371`). **Supersession note (in-row):** the earlier `source=default=true` expectation is SUPERSEDED — it is unreachable on this tree (the unset path exits 78 because legacy migrations trip the full-history DDL grep, `stage.sh:357-363`; giter preconditions audit, 106 files). `contains_contract_phase=true` is EXPECTED here (same legacy-migration cause) and is NOT a stop condition (leader adjudication A-STAGE-2 — see the corrected STOP list in §3.4). The exit-0 chain includes the FE provenance sidecar check (§2.5 precedes this step, so the chain is reachable). Stage writes `ENSEMBLE_SELF_ENV=live` into `<install>/.env` (idempotent marker update, mode 0600 — expected behavior, not a deviation; `stage.sh:858-905`).

**RAM-guard note (#15):** if the host carries the staged v0.29.21 guard, large `uv sync`/pytest trees (and the PyInstaller build) may be tree-killed (`ram_breach_observed` fires even on ordinary commands). The per-step budgets bound exposure; a killed step is re-run from its remainder with identical inputs — never widen a budget silently.

### 2.7 Stage worktree (house continuation rule)

After the merge, the stage runs from a **NEW clean worktree checked out at `$VERSION`** (e.g. `<repo>-wt-stage-<slug>`) — never the sonic commission worktree and never the canonical checkout (the house continuation rule: a worktree is per-task; continuation = a NEW worktree, never a resurrection). The §2.5 FE build and the §2.6 stage invocation both run there; `git status --porcelain` in that worktree must be empty before `stage.sh`.

## 3. Verification legs (explicit — do NOT rely on stage's non-fatal legs)

### 3.1 4-way sha (release dir == dist == manifest == sidecar)

```bash
REL="$HOME/agents-ensemble/releases/$VERSION"      # VERIFY-AT-IMPL: install dir from stage log
sha256sum "$REL/ensemble-prod" | awk '{print $1}'                                  # (1) release dir
sha256sum <stage-wt>/dist/ensemble-prod | awk '{print $1}'                          # (2) dist
python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["binary_sha256"])' "$REL/manifest.json"   # (3) manifest
python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["artifact_sha256"])' <stage-wt>/dist/ensemble-prod.build-provenance.json  # (4) sidecar
```

All four identical ⇒ PASS. The sidecar is build-time state and is NOT shipped into the release dir (`stage.sh:451-455` G3); its absence with `--skip-build` means the prebuilt binary lacked provenance ⇒ STOP.

### 3.2 Provenance re-hash (stage never trusts the sidecar — `lib.sh:3280-3356`)

Independent of stage: `sha256sum <stage-wt>/dist/ensemble-prod` must equal the sidecar `artifact_sha256`; sidecar `git_head` must equal `git rev-parse "$VERSION^{commit}"`; `git_dirty` must be `false`. Any mismatch ⇒ STOP (dirty-build / stale-provenance class).

### 3.3 PROMPT-BASELINE verify (count-shift DERIVATION rule)

```bash
python3 scripts/protection/generate_prompt_artifact_baseline.py --check --tree "$REL" ; echo rc=$?
wc -l < "$REL/agents/.prompt-artifacts.sha256"
```

Expected **rc=0** and **count = 181 + 4 agents × 4 artifacts = 197**. (The Phase-1 regen also repairs 8 PRE-EXISTING drift rows — `decisions.md` DEV-1 / `plan-overview.md` R13 — so if `--check` reports drift against the regenerated file, the regen did not run or was not committed; stale "baseline 181" references elsewhere must not be trusted.) The stage-side drift leg (`prompt_artifact_drift_leg`, `scripts/upgrade/stage.sh:815-849`; findings-C §2d cites wt:687-702) is **informational / non-fatal by design** (allow-stale policy, journaled) — it will NOT fail the stage on drift, which is exactly why this explicit check runs. Any drift ⇒ STOP (the baseline or the seal was not regenerated/committed).

### 3.4 ZERO migrations + rollback_safe rationale

```bash
# (a) THIS RELEASE'S OWN delta must be content-only — the binding assertion:
git diff --name-only ac9849bf7.."$VERSION" -- daemon/ scripts/ | wc -l          # MUST be 0
git diff --name-only ac9849bf7.."$VERSION" -- daemon/migrations/versions/ | wc -l  # MUST be 0
# (b) Tag-to-tag delta vs the release currently behind `current` — OBSERVE + DISCLOSE:
PREV_TAG=<tag currently behind `current`>       # VERIFY-AT-IMPL from `readlink $HOME/agents-ensemble/current` (or the install's state.json)
git diff --name-only "$PREV_TAG".."$VERSION" -- daemon/migrations/versions/    # expected empty; non-empty ⇒ list and disclose (intermediate already-staged releases, NOT this release)
grep -o '"rollback_safe": [a-z]*' "$REL/manifest.json"                          # MUST be true (EXPLICIT path — log token `source=explicit=true`; `contains_contract_phase=true` is EXPECTED per A-STAGE-2, same legacy-migration cause)
```

Note on (b): if the live `current` sits several staged releases behind (v0.29.2x chain), the tag-to-tag diff may legitimately include changes from those intermediate releases — that delta is **not** this release's content and must be disclosed as such, never attributed to the sonic branch. The binding "zero migrations" claim for tonight is (a): the sonic branch itself ships no `daemon/` and no migration files.

**rollback_safe rationale (state exactly in the report):** the release is content-only — additive `agents/sonic-*/` directories plus one `agents/ari/rule.md` note; the daemon binary and the schema are unchanged, and the previous release directory ignores new agent directories entirely (they are plain directory-scanned prompt content). Therefore reverting `current` to the previous release restores the prior behavior with zero data or schema implications — trivially safe. **STOP triggers (corrected per leader adjudication A-STAGE-2):** STOP ONLY on (i) `rollback_safe ≠ true`; (ii) missing/failed 4-way sha (§3.1–§3.2); (iii) PROMPT-BASELINE mismatch (§3.3); (iv) unexpected NEW migration delta vs `ac9849bf7` (this release's own migration delta = 0, proven by C4/C6). `contains_contract_phase=true` is **EXPECTED** on this tree — the same full-history legacy-migration grep fires (`stage.sh:357-363`) — and is **NOT a stop condition**.

## 4. STOP LINE — NO PROMOTE

**This runbook ends at a staged release.** Promote is the Emperor's ceremony.

- Do NOT run `scripts/upgrade/promote.sh`, do not flip `current`, do not restart the live daemon.
- Deliver the promote-command TEMPLATE (not executed) in the morning report:

```bash
# TEMPLATE ONLY — Emperor's ceremony owns the decision and the execution.
# From the scripts-pin worktree (or the stage worktree — CWD selects the executing script):
ENSEMBLE_UPGRADE_LIVE=1 VERSION=<derived> bash scripts/upgrade/promote.sh live --f2-verified-closed
```

- Evidence pointers the template must carry: staged release dir path, 4-way sha value, manifest path, prompt-baseline count, dry-run evidence path, pack result, journal file path.
- **A-STAGE-1 disclosure (mandatory in the morning report):** `ENSEMBLE_ROLLBACK_SAFE=1` is a deliberate leader adjudication, NOT stage.sh's "delta empty" condition: tag-to-tag `v0.29.22..$VERSION` contains exactly ONE migration (`20261112_124200_add_snapshot_agent_space_key.sql` — vsss lane, already in base `ac9849bf7`, NOT sonic content); UP is additive (`ALTER TABLE snapshots ADD COLUMN agent_space_key TEXT`); only DROPs are in the DOWN block; rolling back to the prior release tolerates the extra column → data/schema-safe.

## 5. Morning handoff — what the report MUST show

1. **Staged version** (`$VERSION` as derived) and its annotated-tag object id; `git describe --exact-match` output.
2. **4-way sha** — the single identical digest + the four sources it was read from.
3. **Prompt baseline count** — `197` (or the re-derived value with its derivation) + `--check` rc.
4. **Dry-run evidence path** — the Phase-3 bundle path (or `README` index); PASS verdict reference.
5. **Pack result** — `sonic_agent_defs_unit_test` PASS + the PACKS.md row location + run duration.
6. **Deviation list** — every item from the three phase files' Deviations sections that materialized, plus anything new, with evidence.
7. **Open decisions for the Emperor** — (a) promote or not (with the template), (b) patch-vs-minor version choice if it was a judgment call, (c) any VERIFY-AT-IMPL item that landed differently than specified, (d) untested surfaces (snapshot chain — see `phase3-dryrun-spec.md` §8.4).
8. **Zero-migration statement** + rollback_safe rationale verbatim (§3.4) **+ the A-STAGE-1 `ENSEMBLE_ROLLBACK_SAFE=1` adjudication disclosure (§4)**.

## 6. Acceptance criteria

- AC-P4-1: preconditions C1–C7 all verified with recorded command output; any C failure stops before §2.
- AC-P4-2: merge performed by giter per contract; `latest` contains the branch; feature-branch cleanup executed exactly once.
- AC-P4-3: version derived from tags (derivation command output recorded); bump touched exactly the 4 (+CHANGELOG if renamed) files; tag is annotated and `git describe --tags --exact-match` == `$VERSION`.
- AC-P4-4: `stage.sh live` exit 0 with `rollback_safe=true source=explicit=true` (`stage.sh:371` + `:927`); `ENSEMBLE_ROLLBACK_SAFE=1` present in the recorded argv (deliberate adjudication, A-STAGE-1); no `--allow-stale-stage` in argv (grep the recorded command). Supersession: the default=true token is unreachable on this tree — full-history DDL grep, 106 legacy matches (`stage.sh:357-363`; see §2.6).
- AC-P4-5: §3.1–§3.4 all PASS with literal output; 4-way sha identical; baseline count == 197; this-release delta (`ac9849bf7..$VERSION` over `daemon/` + `scripts/` + migrations) == 0; any tag-to-tag migrations delta disclosed.
- AC-P4-6: **no promote executed** — evidence: `readlink $HOME/agents-ensemble/current` unchanged from pre-stage; no promote.sh invocation in the shell history/transcript.
- AC-P4-7: morning-handoff §5 items all present.

## 7. Deviations & evidence conflicts

1. **`bump_version.py` derives from `pyproject.toml`, not from tags.** The dispatch rule is tag-derived versioning; the script bumps from pyproject. This runbook reconciles them: derive from tags FIRST, then run the script and verify its result equals the derived value (STOP on mismatch). Add a note to the morning report if pyproject had drifted (that is itself a finding). Extra: the script has no explicit-version argument — only `patch|minor|major|prerelease` — so a minor-vs-patch decision changes the invocation, not the derivation rule.
2. **The stage drift leg is non-fatal by design** — findings-C §2d: "missing checker/baseline/python is non-fatal skip" (allow-stale policy). §3.3 therefore verifies explicitly; a green stage exit does NOT imply a clean baseline. This is the single most likely way to ship a stale baseline — call it out in the report.
3. **Dispatch says "the 4-way sha (release dir == dist == manifest == sidecar)".** In this tree the SIDECAR is the build-provenance JSON *sibling of `dist/ensemble-prod`* (`lib.sh` `_provenance_path`), not a file inside the release dir; `stage.sh:451-455` (G3) deliberately excludes it from the payload. §3.1 reads it from the stage worktree's `dist/`, which is the artifact the release was cut from.
4. **Install-dir/port/db resolution is lib.sh's, not restated here.** VERIFY-AT-IMPL: read `$REL` from the stage log's `staged release … at $REL` line rather than assuming `$HOME/agents-ensemble` (`ENSEMBLE_INSTALL_DIR` may override). The runbook's commands use the log-derived path.
5. **`PIPELINE_SCRIPTS_REV` stamp gate.** `stage.sh` refuses unless `PIPELINE_SCRIPTS_REV` (`scripts/upgrade/lib.sh`) is a resolvable commit equal to `PIPELINE_SCRIPTS_FLOOR_COMMIT` (`daemon/tools/upgrade_tools.py`) and covers every shipped executor-family path (`stage.sh:670-699`). This branch ships no executor scripts, so the pins at `latest` should carry — VERIFY-AT-IMPL with the exact check before staging; if stage refuses with the coverage token, STOP (re-pinning is a separate release obligation, never a hand-edit).
