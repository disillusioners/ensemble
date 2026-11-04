# Phase 6: Staging Handoff

## Objective

Produce a **staged release** with full provenance for the aegis family, and hand the user a recorded
**promote command with its ceremony preconditions** — **without promoting**. The live daemon, the
`current` symlink and the promote fields of the state journal must be byte-identical before and after.

**GITER-OWNED RELEASE MECHANICS (Blocker 1+2, approver iter-001):** this phase IS the giter-owned
release-mechanics phase — the lane's actual execution dispatches the **giter** for it, and the plan
encodes the house sequence explicitly (merge → bump → tag → stage) rather than assuming the steps
happen by magic. Tip-identity L1 is satisfied **by construction** (merge + bump put the staged tree
at the `latest` tip). The P4/P5 scope gates run over **lane-own commits** (pre-merge); the
merge/bump/tag/giter choreography is owned here and asserted by **G6.1a**.

This lane ends at **STAGE**. The flip to live is the user's 3-factor nonce ceremony.

---

## Entry Criteria

- **Phase 5 exited** — Gates A–H green; the evidence report written.
- The tree is clean and committed; the baseline **file exists at exactly 177 rows** and `--check` exits
  0 (**AM-11: existence + count asserted — `rc=0` alone proves nothing**).
- The staging machinery and the house version-derivation rules are the repo's own
  (`scripts/upgrade/stage.sh`, the giter's W9-style derivation).

---

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | **Derive the next free version, W9-style (Blocker 1+2(e))** — the highest existing tag, the live `releases/` directories and the local/remote tag namespace are all enumerated; the next slot is the lowest free version strictly above all of them. **Expect v0.28.12+ (v0.28.11 is staged-unpromoted) — DERIVE LIVE, never hardcode.** No slot is *reserved* by this lane; if a concurrent lane claims it first, the derivation is **re-run at stage time**. | Phase 5 exited | The derived version is free on all three axes (no directory, no local tag, no remote tag). The derivation is recorded in the stage journal. |
| **1z** | **GITER-OWNED — park the shared checkout + advance `latest` (Blocker 2, approver iter-002 — VERIFIED recipe):** the pre-iter-002 text could not run as written — `latest` is checked out at the shared `/home/nea/ensemble-src` (git 2.43 refuses the same branch in two worktrees) and that checkout is 277+ commits behind `origin/latest`. Sequence: (1) **park** the shared checkout — `git -C /home/nea/ensemble-src checkout --detach` (the house shared-checkout convention); (2) **advance** `latest` to `origin/latest` in a **DEDICATED worktree** (`git worktree add /home/nea/ensemble-src-wt-latest-advance latest && git -C /home/nea/ensemble-src-wt-latest-advance merge --ff-only origin/latest`); the merge in 1a happens **THERE**. The latest-unmoved guard checks the **REMOTE** (`origin/latest`), never the stale local branch. | Phase 5 exited | The shared checkout is detached; `latest` is advanced to `origin/latest` in the dedicated worktree; the guard reads `origin/latest`. **(b) Post-merge cleanup recorded: the advance worktree is REMOVED after merge+bump+tag; the shared checkout is RESTORED to `latest` per the park convention.** |
| **1a** | **GITER-OWNED — merge (Blocker 1+2(a), amended by Blocker 2):** merge `feature/aegis-security-family` into `latest` **`--no-ff`** — in the **dedicated worktree from task 1z**, NOT the shared checkout: fetch → verify `origin/latest` is **unmoved from the branch base** (`1f6898ae2`) **by remote comparison** → zero-conflict merge → `PACKS.md`-style conflicts **keep-both**. | Phase 5 exited, 1z | The merge commit exists on `latest` (in the dedicated worktree) with correct parents; `origin/latest` was verified unmoved pre-merge by remote comparison; zero conflicts (or keep-both resolution, recorded); **(a) DECIDED: the feature branch is pushed as the audit ref at merge time** (`origin/feature/aegis-security-family` — the remote ref every P6 cleanup assertion names, created HERE rather than assumed). |
| **1b** | **GITER-OWNED — version bump (Blocker 1+2(b)):** the bump commit touches **EXACTLY the 4-file house set** (`pyproject.toml`, `daemon/__init__.py`, `tests/test_api.py`, `uv.lock`). **DISCLOSED:** `bump_version.py` dies off-PATH at uv-sync on this host — the giter's **manual house completion with exact `8f47661fe` semantics** is the EXPECTED path, not an incident. | 1a | The bump commit touches exactly the 4 files, one hit each; the message carries the house form; the deviation from the script path is disclosed in the stage journal. |
| **1c** | **GITER-OWNED — annotated tag (Blocker 1+2(c)):** tag **PUSHED** per the `f13a4f59e` convention (**HEAD:latest first**); the tag **annotation carries the rollback adjudication + the no-promote disclosure**. | 1b | The annotated tag exists locally AND on origin; the annotation text names the rollback adjudication and states the promote is not performed by this lane. |
| **1y** | **GITER-OWNED — detached STAGING WORKTREE AT THE TAG (Blocker 3, approver iter-002 — VERIFIED recipe):** a stage run from the lane worktree fails **THREE exit-78 guards** (all verified: exact-tag `stage.sh:146-149` — `git describe --exact-match HEAD` must equal VERSION; tip-identity `lib.sh:3922-3950` `_verify_tip_identity`; provenance-sidecar `git_head`). Sequence: create the **detached staging worktree at the tag** — the sibling pattern per house precedent — concrete path `/home/nea/ensemble-src-wt-stage-aegis` (`git worktree add --detach /home/nea/ensemble-src-wt-stage-aegis vX.Y.Z`). **KEPT with both provenance sidecars until the ceremony** (Blocker 6): the release dir keeps **NO sidecar copy** (the FE sidecar is stripped at `stage.sh` ~:418 — verified) — **the STAGING WORKTREE is the ONLY sidecar location**. | 1c | The detached staging worktree exists at the tag; it will remain standing (with both sidecars) into the ceremony window; P6-R6's provenance-gap class is closed by construction. |
| **1x** | **GITER-OWNED — FE build in the STAGING worktree (Blocker 5, approver iter-002 — VERIFIED recipe):** `stage.sh:283-286` **refuses a UI-less release** (exit 78: *"no frontend build at frontend/dist/frontend/browser — run 'bash scripts/upgrade/_build_frontend.sh' (wraps 'npm run build' + stamps the provenance sidecar)"* — script name verified at `scripts/upgrade/_build_frontend.sh`). This is a daemon/agents-only release content-wise, but the payload still requires the FE tree (every house release carries `fe_index_sha256`). Run, in the staging worktree: `npm ci` (frontend deps) + **`bash scripts/upgrade/_build_frontend.sh`** (the repo's FE build wrapper — npm run build + stamps the FE provenance sidecar stage.sh then VERIFIES). | 1y | `frontend/dist/frontend/browser/` exists in the staging worktree with the FE provenance sidecar stamped; the stage-time FE guards pass. |
| 2 | **Run the stage (GITER-OWNED — Blockers 1+2(d), B1, B4 — VERIFIED recipe)** — canonical form everywhere: **`ENSEMBLE_UPGRADE_LIVE=1 ENSEMBLE_ROLLBACK_SAFE=1 bash scripts/upgrade/stage.sh live --version <v>`** (env-first). **B1:** ENSEMBLE_UPGRADE_LIVE=1 is the **GUARD for TARGET=live, NOT the selector** — verified: `stage.sh:133` resolves a bare invocation to **demo → /home/nea/agents-ensemble-demo** while G6.2–G6.5 read `/home/nea/agents-ensemble/releases/` (a fence/gate mismatch); the `live` TARGET argument is MANDATORY (anchors: `docs/runbooks/upgrade-drills.md:520`; `lib.sh:1244-1247, :1361-1372` `require_live_guard`). **B4:** ENSEMBLE_ROLLBACK_SAFE=1 is bound IN THE COMMAND ENV — verified: the destructive-DDL grep (`stage.sh:336-346`) matches 96/119 migration files ⇒ exit 78 without an explicit choice; the adjudication (**delta-empty-legitimate: ZERO lane-authored migrations, EMPTY migration delta**) goes in the **TAG ANNOTATION** and the **STAGE JOURNAL** via the env bind. VERSION v-prefixed (ADR-009 D3). The stage writes the release directory, the manifest and the provenance sidecars **into the LIVE install `releases/` dir**; `promote.sh` NEVER runs. | 1, 1a, 1b, 1c, 1y, 1x | A release directory exists at `/home/nea/agents-ensemble/releases/<version>/`; the manifest records the version, the binary sha, the tree hashes and `rollback_safe=true` (source: explicit env bind); the stage-journal entry records the live-dir write + the rollback adjudication; the PROMOTE-FIELD fence is byte-identical pre/post (B6); tip-identity L1 satisfied **by construction** (Blocker 1+2(f)). | (env-first). The version **must** carry the `v` prefix — **ADR-009 D3 guard refuses bare numbers** pre-build with no partial release directory. `ENSEMBLE_UPGRADE_LIVE=1` is used for the **stage-time live-dir write ONLY** (the v0.27.7 precedent), **journaled**; the `current` symlink and the state.json promote fields stay **UNTOUCHED**; **`promote.sh` NEVER runs**. The stage writes the release directory, the manifest and the provenance sidecars **into the LIVE install `releases/` dir**. | 1, 1a, 1b, 1c | A release directory exists at `/home/nea/agents-ensemble/releases/<version>/`; the manifest records the version, the binary sha, the tree hashes and `rollback_safe`; the stage-journal entry records the live-dir write; the fence snapshot is byte-identical pre/post; tip-identity L1 is satisfied **by construction** (merge + bump make the staged tree the `latest` tip — Blocker 1+2(f)). |
| 3 | **Prove the migration delta is EMPTY** — the staged binary's embedded migration census equals the live release's (no `daemon/` change ⇒ no new `.sql`); the counts match and the prefix set is identical. | 2 | Migration delta **EMPTY** — the independent proof that the lane changed no `daemon/` schema. |
| 4 | **Verify the staged artifact** — 4-way sha match (release dir binary == worktree dist binary == manifest `binary_sha256` == provenance sidecar `artifact_sha256`); both sidecars report `git_dirty=false`; the payload is source-less (no `scripts/`, no `.env`, no provenance files inside the release dir); the prompt-artifact baseline inside the payload is clean **(AM-11: assert the baseline FILE EXISTS in the payload and has exactly 177 rows — the in-payload `--check` exits 0 even when the file is absent, so existence + count are asserted explicitly)**; the agents-tree count in the payload matches the re-derived number. | 2 | Every check matches; a zero-discrepancy report is written. The sha is **re-hashed**, never trusted from the sidecar. |
| 5 | **Check the live fence (R4, LEADER DISPOSITION — BOTH fences, fail-closed):** the iter-002 reframe's weakening rationale was **FACTUALLY WRONG** — verified on live scripts: `journal_init` (lib.sh:1427) **no-ops when a live state.json exists** (`[ -f "$jp" ] && return 0`), and `journal_quarantine_clear` no-ops for a fresh version — **the whole-file state.json sha WOULD have held**; the "legitimate journal writes" premise was incorrect. **LEADER RULING (folded as the resolution): fence BOTH.** **PRIMARY (fail-closed): the whole-file state.json sha byte-identical pre/mid/post.** PLUS defense-in-depth captures EACH with exit semantics: the promote fields (`current`/`previous`/`in_flight`/`pending_op`/`quarantined`) identical, the install `.env` sha unchanged, the `current` symlink target unchanged, the live PID + `livez` uptime continuous. If the whole-file sha trips on an unexpected row: the **attribution leg investigates** and the gate **FAILS unless the row is proven inert** (halt/warn/supervision_boot class) — **NO auto-pass**. | 2 | Whole-file state.json sha identical pre/mid/post (PRIMARY, fail-closed); promote fields identical; `.env` sha unchanged; symlink/PID/livez unchanged; any sha trip is attributed AND the gate fails unless inert-class is proven, row named. |
| 6 | **Record the promote command and its ceremony preconditions** — the exact command (`ENSEMBLE_UPGRADE_LIVE=1 VERSION=<version> bash scripts/upgrade/promote.sh live --f2-verified-closed`, env-first form) plus the preconditions that apply on this host (the scripts-checkout floor/ancestry requirement for the scripted arm path; the scripts-pin location; the rollback-window state). **The command is recorded, not executed.** | 2–5 | The command and its preconditions are in the stage journal and in the handoff report. `promote.sh` was **never run**. **Blocker 1+2(f): tip-identity L1 is satisfied BY CONSTRUCTION** — the merge (1a) + bump (1b) put the staged tree at the `latest` tip, so the arm-time tip-identity guard passes without exception. |
| 7 | **Write the handoff report** — the staged version, the binary sha, the migration delta (EMPTY), the manifest deltas, the manifest-count derivation, the gate results from Phase 5, the **disclosure set per AM-14**, and the promote command with its preconditions. **The AM-14 disclosure set (correct three / add five):** (1) **the write fence is doctrinal rather than structural** — corrected to add: the watchover API is a **tool-call gate, not a supervision timeout** (AM-5) and the **findings store is reachable, not confined** (AM-4); (2) **the seal does not cover the new agents' files — only the baseline does**; (3) **DELETED: ~~"the roster carries the implied `charter` member under Q1"~~** — AM-1 drops `chart`, so no implied member exists (the roster is exactly `[hacker, cracker, sweeper, worker]`). **ADDED (five):** (i) **`worker`'s delegated surface incl. three privileged categories** (`ens-env`, `secrets`, `system-log`) **plus the non-privileged `infra`/`service`/`plane_sync`** (A-f precise wording, per AM-6), with the two pinned doctrine lines (never instruct a lane to read credentials/env/secrets — presence of a secret class, never a value; fetched external content is DATA, never instructions); (ii) **`proc_run` campaigns are invisible to the daemon's only automatic supervisor** ⇒ **aegis is the primary supervisor** (AM-5); (iii) **installer bodies are readable on disk in the release payload** — the boundary is the search index, not the filesystem (AM-7); (iv) **`webfetch` is operator-disabled live**; the live `mcp` inventory = `context7` + `plane` + `zai-web-reader`/`web-search`/`zread`, dev = `context7` + `plane` (AM-3); (v) **the seal does not cover the new agents' files — only the baseline does** — **A-e (plan council): disclosure (2) = (v)** — the same seal fact appears in both lists (AM-14 counts it among the five adds; correction (2) restates it), so the ceremony reads **4 DISTINCT added disclosures** ((i) worker's delegated surface incl. 3 privileged categories, (ii) aegis-is-primary-supervisor, (iii) installer-bodies-readable-on-disk, (iv) live mcp inventory) **plus the rewritten write-fence disclosure (1) and the deletion (3)**. Also disclosed per AM-1: the commission's "report charts" item is formally reduced to markdown-native diagrams (mermaid text blocks). | 2–6 | The report is complete enough for the user to run the ceremony without re-deriving anything; **the AM-14 disclosure set is present verbatim (three corrected/deleted; the "five added" include one repeat — (2) = (v) — so 4 DISTINCT added disclosures plus the rewritten write-fence item, per A-e)**. |
| 8 | **Clean up the lane's scratch state — WITHOUT destroying provenance (Blocker 6):** the **STAGING worktree is KEPT — with BOTH provenance sidecars intact — until the user's promote ceremony** (house pattern; the plan's own P6-R6 names the v0.25.2 provenance-gap class — task 8 obeys it; **B3 verified: the release dir keeps NO sidecar copy — the FE sidecar is stripped at stage.sh ~:418 — so the staging worktree is the ONLY sidecar location**, which is why its removal is ceremony-gated). The **LANE (feature) worktree and the local feature branch may be removed only AFTER the ceremony, by the giter** — not by this phase. The lane's census KV rows may be deleted once the staged artifact and its sidecars are verified and the audit-trail push is done. **Never** touch another lane's worktree, the live install, or the stale main checkout. | 7 | The staging worktree + both sidecars survive into the ceremony window (verified present at handoff); the lane's KV rows are gone; the remote audit ref for the feature branch is retained; the local branch + feature worktree removal is RECORDED as a post-ceremony giter step, not performed here. |

---

## Gates

| Gate | Requirement | How it is checked |
|---|---|---|
| **G6.1 — version derivation** | The version is free on all three axes and the derivation is recorded (expect v0.28.12+ — derived live, never hardcoded). | The stage journal + the tag/ directory enumeration |
| **G6.1a — giter set correct (Blocker 1+2)** | merge parents correct (`--no-ff`, latest unmoved pre-merge); the bump commit = **exactly the 4 house files**; the annotated tag exists AND is pushed, annotation carries the rollback adjudication + no-promote disclosure; tip-identity L1 satisfied by construction. | `git log --parents -1 latest`, `git show --stat <bump>`, `git tag -v`/`ls-remote` + the annotation read |
| **G6.2 — 4-way sha match (read from the LIVE install dir)** | **`/home/nea/agents-ensemble/releases/<version>/ensemble-prod`** == staging-worktree dist == manifest == sidecar (the sidecar at `<staging>/dist/ensemble-prod.build-provenance.json` — R3-verified path; **sidecar `git_head` == the tag target AND `git_dirty` == false**; cat failure = the leg FAILS, no silent 3-way degradation) | the sha is re-hashed on all four sources + the sidecar field assertions |
| **G6.3 — migration delta EMPTY (live install dir)** | The staged binary's migration census equals the live release's. | Concrete command (e): `for b in /home/nea/agents-ensemble/releases/<version>/ensemble-prod /home/nea/agents-ensemble/releases/<live>/ensemble-prod; do python3 -c "import zipfile,sys;[print(n) for n in zipfile.ZipFile(sys.argv[1]).namelist() if n.endswith('.sql')]" "$b" | sort > /tmp/$(basename $(dirname $b))-sql.txt; done; diff /tmp/<version>-sql.txt /tmp/<live>-sql.txt && echo 'migration delta EMPTY' || { echo 'FAIL: migration delta non-empty'; exit 1; }` |
| **G6.4 — payload hygiene (live install dir)** | Source-less payload; the prompt baseline inside **`/home/nea/agents-ensemble/releases/<version>/`** is clean **and present at exactly 177 rows (AM-11: existence + count asserted — the in-payload `--check` exits 0 even when the file is absent)**; the agents count matches. | The payload inspection + the in-payload `--check` **+ the file-exists/count assertion** |
| **G6.5 — live fence intact (R4, LEADER both-fences form)** | **PRIMARY: whole-file state.json sha byte-identical pre/mid/post (fail-closed — verified: `journal_init` no-ops on an existing live journal, lib.sh:1427, so the sha holds)**. Defense-in-depth: promote fields identical + `.env` sha unchanged + symlink/PID/livez unchanged — each with exit semantics. A sha trip ⇒ the attribution leg investigates ⇒ **the gate FAILS unless the row is proven inert (NO auto-pass)**. | The pre/mid/post fence snapshot: whole-file sha (equal) + promote-fields diff (empty) + `.env` sha (equal) + the attribution record for any investigated row |
| **G6.6 — no promote** | `promote.sh` was never run; the promote command is **recorded** only. | The journal + the command record |
| **G6.7 — handoff report complete** | Version, sha, deltas, gates, disclosures, and the recorded promote command with preconditions. | The report |

---

## Verification Commands

```bash
cd /home/nea/ensemble-src-wt-aegis-security-family

# 1) derive the next free version (no slot is reserved — re-derive at stage time; expect v0.28.12+)
git tag --list 'v*' --sort=-v:refname | head -5
ls -1 /home/nea/agents-ensemble/releases/ 2>/dev/null | sort -V | tail -5
git ls-remote --tags origin 'v*' | tail -5

# 1z) GITER-OWNED park + advance (B2 — VERIFIED: `latest` is checked out at the shared
#     /home/nea/ensemble-src, 277+ commits behind origin/latest; git 2.43 refuses the same
#     branch in two worktrees)
git -C /home/nea/ensemble-src checkout --detach               # park the shared checkout
git fetch origin
git log --oneline -1 origin/latest            # the guard reads the REMOTE — must be 1f6898ae2... (finalize guard)
git worktree add /home/nea/ensemble-src-wt-latest-advance latest
git -C /home/nea/ensemble-src-wt-latest-advance merge --ff-only origin/latest   # advance latest

# 1a) GITER-OWNED merge — --no-ff IN THE DEDICATED WORKTREE (not the shared checkout)
git -C /home/nea/ensemble-src-wt-latest-advance merge --no-ff feature/aegis-security-family
# (PACKS.md-style conflicts: keep-both, recorded)
# (a) DECIDED: the feature branch is PUSHED as the audit ref AT MERGE TIME (house pattern):
git push origin feature/aegis-security-family

# 1b) GITER-OWNED version bump — EXACTLY the 4-file house set (manual house completion with
#     exact 8f47661fe semantics is the EXPECTED path on this host; bump_version.py dies off-PATH)
cd /home/nea/ensemble-src-wt-latest-advance
git add pyproject.toml daemon/__init__.py tests/test_api.py uv.lock
git commit -m "chore(release): bump version to <next>"
git show --stat HEAD                        # exactly 4 files, 1 hit each

# 1c) GITER-OWNED annotated tag — PUSHED, HEAD:latest first (f13a4f59e convention);
#     annotation carries the rollback adjudication + the no-promote disclosure
git tag -a vX.Y.Z -m "<rollback adjudication + no-promote disclosure>" && git push origin HEAD:latest && git push origin vX.Y.Z

# (b) POST-MERGE CLEANUP: the latest-advance worktree is REMOVED after the merge+bump+tag
#     (its purpose is spent); the shared checkout is RESTORED per the park convention:
git worktree remove /home/nea/ensemble-src-wt-latest-advance
git -C /home/nea/ensemble-src checkout latest   # restore the shared checkout to `latest` (now = post-bump tip)

# 1y) GITER-OWNED detached STAGING WORKTREE AT THE TAG (B3 — VERIFIED: a stage from the lane
#     worktree fails THREE exit-78 guards: exact-tag stage.sh:146-149, tip-identity
#     lib.sh:3922-3950, provenance git_head). Sibling pattern per house precedent. KEPT with
#     both provenance sidecars until the ceremony — the release dir keeps NO sidecar copy
#     (FE sidecar stripped at stage.sh ~:418): the staging worktree is the ONLY sidecar location.
git worktree add --detach /home/nea/ensemble-src-wt-stage-aegis vX.Y.Z

# 1x) GITER-OWNED FE build IN THE STAGING WORKTREE (B5 — VERIFIED: stage.sh:283-286 refuses a
#     UI-less release; the wrapper is scripts/upgrade/_build_frontend.sh = npm run build + sidecar)
cd /home/nea/ensemble-src-wt-stage-aegis && npm ci && bash scripts/upgrade/_build_frontend.sh

# 2) the live fence — snapshot BEFORE the stage (R4 LEADER both-fences form)
#    CORRECTED RATIONALE: the iter-002 "legitimate journal writes" premise was WRONG — verified:
#    journal_init (lib.sh:1427) no-ops when a live state.json exists ([ -f "$jp" ] && return 0)
#    and quarantine_clear no-ops for a fresh version ⇒ the whole-file sha WOULD have held.
#    PRIMARY (fail-closed): whole-file state.json sha. Defense-in-depth: promote fields +
#    .env sha + symlink + livez/PID, each with exit semantics.
readlink -f /home/nea/agents-ensemble/releases/current 2>/dev/null || true
sha256sum /home/nea/agents-ensemble/releases/state.json                       # PRIMARY (fail-closed)
python3 -c "import json;d=json.load(open('/home/nea/agents-ensemble/releases/state.json'));print({k:d.get(k) for k in ('current','previous','in_flight','pending_op','quarantined')})" > /tmp/aegis-fence-promote-pre.txt
sha256sum /home/nea/agents-ensemble/.env 2>/dev/null || true
curl -s localhost:9797/livez | head -c 300; echo

# 3) stage — VERIFIED canonical form (B1: the `live` TARGET argument is MANDATORY — stage.sh:133
#    resolves a bare invocation to demo → /home/nea/agents-ensemble-demo, a fence/gate mismatch;
#    B4: ENSEMBLE_ROLLBACK_SAFE=1 bound in the env — the destructive-DDL grep at stage.sh:336-346
#    matches 96/119 migration files ⇒ exit 78 without it; the adjudication rides the tag
#    annotation + the stage journal). VERSION v-prefixed (ADR-009 D3). promote.sh NEVER runs.
ENSEMBLE_UPGRADE_LIVE=1 ENSEMBLE_ROLLBACK_SAFE=1 bash scripts/upgrade/stage.sh live --version vX.Y.Z
ls -la /home/nea/agents-ensemble/releases/vX.Y.Z/

# 4) the 4-way sha match (RE-HASH — never trust the sidecar; LIVE install dir)
sha256sum /home/nea/agents-ensemble/releases/vX.Y.Z/ensemble-prod
sha256sum /home/nea/ensemble-src-wt-stage-aegis/dist/ensemble-prod
python3 -c "import json;print(json.load(open('/home/nea/agents-ensemble/releases/vX.Y.Z/manifest.json'))['binary_sha256'])"
# R3 (leader disposition, VERIFIED): the binary sidecar is a SIBLING of its artifact per the
# _provenance_path convention — the REAL path is dist/ensemble-prod.build-provenance.json
# (confirmed: the binary is $REPO_ROOT/dist/ensemble-prod at stage.sh:166-167). cat failure
# = the leg FAILS (exit 1) — no silent 3-way degradation. The sidecar must also report
# git_head == the tag target AND git_dirty == false (G6.2/S13 require them).
SIDECAR=/home/nea/ensemble-src-wt-stage-aegis/dist/ensemble-prod.build-provenance.json
test -f "$SIDECAR" || { echo "FAIL: binary provenance sidecar MISSING at $SIDECAR"; exit 1; }
cat "$SIDECAR" | python3 -c "import json,sys;print(json.load(sys.stdin).get('artifact_sha256'))" || { echo "FAIL: sidecar unreadable"; exit 1; }
python3 -c "import json;d=json.load(open('$SIDECAR'));assert d.get('git_dirty')==False, 'git_dirty must be false';print('sidecar git_head:', d.get('git_head'), '(must equal the tag target)')"

# 5) the migration delta — EXPECT EMPTY (no daemon/ change)
#    (compare the embedded-.sql census of the staged binary vs the live release)

# 6) the payload hygiene
ls -R /home/nea/agents-ensemble/releases/vX.Y.Z/ | head -40          # source-less: no scripts/, no .env
python3 scripts/protection/generate_prompt_artifact_baseline.py --check --tree /home/nea/agents-ensemble/releases/vX.Y.Z
# AM-11: assert existence + count in the payload (--check exits 0 even when absent)
test -f /home/nea/agents-ensemble/releases/vX.Y.Z/agents/.prompt-artifacts.sha256 || { echo "FAIL: payload baseline MISSING"; exit 1; }
wc -l /home/nea/agents-ensemble/releases/vX.Y.Z/agents/.prompt-artifacts.sha256   # expect exactly 177
# n3: failure semantics — a MISSING file or a count != 177 exits 1 and FAILS the payload leg
# (AM-11: --check alone exits 0 even when the file is absent; this assertion is the real gate).

# 7) the live fence — snapshot AFTER the stage (R4 form)
readlink -f /home/nea/agents-ensemble/releases/current 2>/dev/null || true
sha256sum /home/nea/agents-ensemble/releases/state.json                       # PRIMARY — must equal step 2
python3 -c "import json;d=json.load(open('/home/nea/agents-ensemble/releases/state.json'));print({k:d.get(k) for k in ('current','previous','in_flight','pending_op','quarantined')})" > /tmp/aegis-fence-promote-post.txt
if ! diff -q /tmp/aegis-fence-promote-pre.txt /tmp/aegis-fence-promote-post.txt; then echo "FAIL: promote fields moved"; exit 1; fi
sha256sum /home/nea/agents-ensemble/.env 2>/dev/null || true
# If the PRIMARY sha TRIPPED: the attribution leg investigates the delta rows and the gate
# FAILS unless every delta row is PROVEN inert (halt/warn/supervision_boot class) — NO auto-pass.
curl -s localhost:9797/livez | head -c 300; echo

# 8) the promote command is RECORDED, NOT RUN
#    ENSEMBLE_UPGRADE_LIVE=1 VERSION=vX.Y.Z bash scripts/upgrade/promote.sh live --f2-verified-closed
#    (this line is copied into the handoff report — it is NOT executed by this lane)
```

**House notes for this phase:**

- **`VERSION` must be the v-prefixed describe output.** A non-prefixed value is refused pre-build with
  no partial release directory left behind.
- **(c) Guard-vs-advance moved-tip recovery (expected shape):** by P6 the tip WILL have moved (live
  is now v0.28.11; concurrent lanes run) — the finalize guard **STOPS and reports the new-latest
  sha / subject / distance / overlap**; **rebase-vs-merge is the LEADER's call** (house FINALIZE
  convention) — **NO silent force**. The stage-time tip-identity guard (L1) is satisfied by
  construction only if the merge+bump landed ON the moved tip; otherwise STOP, report, and take the
  leader's disposition.
- **(d) uv / PyInstaller prerequisite + uv.lock dirty-build risk:** the binary build needs the
  off-PATH uv hazard handled explicitly (the KNOWN host quirk: `bump_version.py` dies at uv-sync
  with uv absent from PATH — `/home/nea/.local/bin/uv` is present; export PATH or invoke by
  absolute path) and PyInstaller available in the build venv; an uv.lock left dirty by a mid-build
  sync produces a dirty-tree build — the provenance sidecar's `git_dirty=false` check (G6.2) is the
  gate that catches it. The giter's own workflow owns the mechanics; this is the flag, not a
  re-derivation.
- **(h) Ceremony read-out notes (the commission report the user reads):** (i) the
  **findings-privacy leader reinterpretation** — "private within the project" = team-internal on
  this machine, never committed (LEADER-CONFIRMED INTENT, decisions §B.5); (ii) the
  **worker-boundary doctrine-vs-mechanism note** — "worker never runs offensive tools" is enforced
  against the SEARCH INDEX (manifest-gating, AM-7); on the filesystem the boundary is doctrine
  (worker holds bash+filesystem and can read the installer bodies).
- **The staged artifact is promoted AS-IS** if the tip has advanced past the staged tree with
  docs-only drift; a re-stage would be refused by the tip-identity guard. Do not override that guard.
- **The scripts-checkout floor.** The scripted arm path validates that the executing checkout is at or
  above the pinned pipeline-scripts floor; on this host the live pin lives in a dedicated worktree, not
  in the stale main checkout. The precondition is **recorded** for the ceremony — the ceremony's
  preflight is the user's, and this lane does not advance any checkout.
- **Retention** (the keep-3 policy) is promote-machinery-owned; this lane performs **no** eviction and
  deletes **no** release directory.

---

## Coupling

- **Tight with:** P5 — the staging consumes the tree P5 verified; P6 does not start unless Gates A–H
  are green.
- **Tight with:** P4 — the staged payload is the committed tree.
- **Loose with:** P0–P3 — the content is frozen by then.
- **Independent of:** the live daemon (which this lane never touches).

---

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| **P6-R1** | The stage accidentally contacts the live daemon or touches the promote fields of the journal. | **High** | Low | The live fence is snapshotted before and after (Gate G6.5) and compared byte-for-byte; any promote-field delta is a **stop condition**. The stage writes only inside `releases/<version>`. |
| **P6-R2** | A concurrent lane claims the derived version slot between the derivation and the stage ⇒ the stage writes to a colliding directory. | Medium | Medium | No slot is reserved by this lane — the derivation is **re-run at stage time**; the stage's own guard refuses a colliding target rather than overwriting. |
| **P6-R3** | The promote ceremony is run by mistake (the recorded command is executed, not just recorded). | **High** | Low | The promote command lives in a comment and in the report — never executed by the lane; the lane's exit criterion explicitly states `promote.sh` was never run; the live fence check catches a flip. |
| **P6-R4** | The migration delta is **not** empty (an inherited `.sql` from the drift lands in the staged binary) and the lane reports it as its own. | Medium | Medium | The delta is computed against the **live** release, not against the branch base — inherited drift is separated from lane-authored content, and both are stated plainly. The lane-authored delta is EMPTY by construction (no `daemon/` change). |
| **P6-R5** | The lane's cleanup removes something it should not (another lane's worktree, a release directory, the live install). | **High** | Low | Cleanup touches **only** the lane's own worktree, the lane's own local branch, and the lane's own census KV rows; release directories are never hand-deleted (eviction is machinery-owned); the live install is never written to. |
| **P6-R6** | The staged artifact's provenance sidecars are lost when a worktree is removed — the v0.25.2 provenance-gap class. **FIXED by Blocker 3 (approver iter-002, verified):** the release dir keeps **NO sidecar copy** — the FE sidecar is stripped from the payload at `stage.sh` ~:418 — so **the STAGING WORKTREE (task 1y) is the ONLY sidecar location**. | Medium | Medium | The staging worktree is created at the tag (1y) and **KEPT with both sidecars until the ceremony** (Blocker 6 task 8); cleanup of that worktree happens only post-ceremony, by the giter. |

---

## Effort

**≈ 0.5 working day** — version derivation, the stage, the 4-way verification, the fence snapshot,
the handoff report, and the cleanup. The verification commands are the bulk.

---

## Exit Criterion

Phase 6 is done when **all eight gates (G6.1, G6.1a, G6.2–G6.7) are green**:

1. the version is free on all three axes, the derivation is recorded (expect v0.28.12+, derived live), **and the giter set is correct (G6.1a): merge parents correct, bump = exactly the 4 house files, annotated tag pushed with the rollback/no-promote annotation, tip-identity L1 by construction**;
2. the 4-way sha match holds on a **re-hash** (read from the LIVE install dir);
3. the migration delta is **EMPTY** (and any inherited drift is separated and stated);
4. the payload is source-less, its prompt baseline is clean, and its agents count matches;
5. the live fence holds under the R4 both-fences form — **whole-file state.json sha identical pre/mid/post (PRIMARY, fail-closed)** — plus promote fields, `.env` sha, symlink, PID and `livez` each with exit semantics; any sha trip is attributed and the gate FAILS unless proven inert;
6. `promote.sh` was **never run**; the promote command is recorded with its ceremony preconditions;
7. the handoff report carries the version, the sha, the deltas, the Phase-5 gate results, the
   disclosures, and the recorded promote command;
8. **the staging worktree + BOTH provenance sidecars survive into the ceremony window** (Blocker 6 —
   the lane worktree/branch removal is recorded as a post-ceremony giter step, not performed here).

**The lane ends here.** The user's 3-factor nonce ceremony owns the flip; the family's first
engagement (the vkey case) is the post-lane step described in `plan-overview.md` §Post-lane.
