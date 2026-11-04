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
| **1a** | **GITER-OWNED — merge (Blocker 1+2(a)):** the lane's actual execution dispatches the **giter** for release mechanics; the plan encodes the house sequence explicitly (nothing happens by magic). Merge `feature/aegis-security-family` into `origin/latest` **`--no-ff`**, per the giter's own workflow: fetch → verify `origin/latest` is **unmoved from the branch base** (`1f6898ae2`) → zero-conflict merge → `PACKS.md`-style conflicts **keep-both**. | Phase 5 exited | The merge commit exists on `latest` with correct parents; latest was verified unmoved pre-merge; zero conflicts (or keep-both resolution, recorded). |
| **1b** | **GITER-OWNED — version bump (Blocker 1+2(b)):** the bump commit touches **EXACTLY the 4-file house set** (`pyproject.toml`, `daemon/__init__.py`, `tests/test_api.py`, `uv.lock`). **DISCLOSED:** `bump_version.py` dies off-PATH at uv-sync on this host — the giter's **manual house completion with exact `8f47661fe` semantics** is the EXPECTED path, not an incident. | 1a | The bump commit touches exactly the 4 files, one hit each; the message carries the house form; the deviation from the script path is disclosed in the stage journal. |
| **1c** | **GITER-OWNED — annotated tag (Blocker 1+2(c)):** tag **PUSHED** per the `f13a4f59e` convention (**HEAD:latest first**); the tag **annotation carries the rollback adjudication + the no-promote disclosure**. | 1b | The annotated tag exists locally AND on origin; the annotation text names the rollback adjudication and states the promote is not performed by this lane. |
| 2 | **Run the stage (GITER-OWNED — Blocker 1+2(d))** — `ENSEMBLE_UPGRADE_LIVE=1 VERSION=<v-prefixed-version> bash scripts/upgrade/stage.sh` (env-first). The version **must** carry the `v` prefix — **ADR-009 D3 guard refuses bare numbers** pre-build with no partial release directory. `ENSEMBLE_UPGRADE_LIVE=1` is used for the **stage-time live-dir write ONLY** (the v0.27.7 precedent), **journaled**; the `current` symlink and the state.json promote fields stay **UNTOUCHED**; **`promote.sh` NEVER runs**. The stage writes the release directory, the manifest and the provenance sidecars **into the LIVE install `releases/` dir**. | 1, 1a, 1b, 1c | A release directory exists at `/home/nea/agents-ensemble/releases/<version>/`; the manifest records the version, the binary sha, the tree hashes and `rollback_safe`; the stage-journal entry records the live-dir write; the fence snapshot is byte-identical pre/post; tip-identity L1 is satisfied **by construction** (merge + bump make the staged tree the `latest` tip — Blocker 1+2(f)). |
| 3 | **Prove the migration delta is EMPTY** — the staged binary's embedded migration census equals the live release's (no `daemon/` change ⇒ no new `.sql`); the counts match and the prefix set is identical. | 2 | Migration delta **EMPTY** — the independent proof that the lane changed no `daemon/` schema. |
| 4 | **Verify the staged artifact** — 4-way sha match (release dir binary == worktree dist binary == manifest `binary_sha256` == provenance sidecar `artifact_sha256`); both sidecars report `git_dirty=false`; the payload is source-less (no `scripts/`, no `.env`, no provenance files inside the release dir); the prompt-artifact baseline inside the payload is clean **(AM-11: assert the baseline FILE EXISTS in the payload and has exactly 177 rows — the in-payload `--check` exits 0 even when the file is absent, so existence + count are asserted explicitly)**; the agents-tree count in the payload matches the re-derived number. | 2 | Every check matches; a zero-discrepancy report is written. The sha is **re-hashed**, never trusted from the sidecar. |
| 5 | **Check the live fence** — before and after the stage: the `current` symlink target, the promote fields of the release journal, the live daemon's `livez` uptime continuity, and the live PID are unchanged. The stage must not contact the live daemon. | 2 | The fence is byte-identical pre/post. Any promote field touched by the stage is a **stop condition** and is reported, not accepted. |
| 6 | **Record the promote command and its ceremony preconditions** — the exact command (`ENSEMBLE_UPGRADE_LIVE=1 VERSION=<version> bash scripts/upgrade/promote.sh live --f2-verified-closed`, env-first form) plus the preconditions that apply on this host (the scripts-checkout floor/ancestry requirement for the scripted arm path; the scripts-pin location; the rollback-window state). **The command is recorded, not executed.** | 2–5 | The command and its preconditions are in the stage journal and in the handoff report. `promote.sh` was **never run**. **Blocker 1+2(f): tip-identity L1 is satisfied BY CONSTRUCTION** — the merge (1a) + bump (1b) put the staged tree at the `latest` tip, so the arm-time tip-identity guard passes without exception. |
| 7 | **Write the handoff report** — the staged version, the binary sha, the migration delta (EMPTY), the manifest deltas, the manifest-count derivation, the gate results from Phase 5, the **disclosure set per AM-14**, and the promote command with its preconditions. **The AM-14 disclosure set (correct three / add five):** (1) **the write fence is doctrinal rather than structural** — corrected to add: the watchover API is a **tool-call gate, not a supervision timeout** (AM-5) and the **findings store is reachable, not confined** (AM-4); (2) **the seal does not cover the new agents' files — only the baseline does**; (3) **DELETED: ~~"the roster carries the implied `charter` member under Q1"~~** — AM-1 drops `chart`, so no implied member exists (the roster is exactly `[hacker, cracker, sweeper, worker]`). **ADDED (five):** (i) **`worker`'s delegated surface incl. three privileged categories** (`ens-env`, `secrets`, `system-log`) **plus the non-privileged `infra`/`service`/`plane_sync`** (A-f precise wording, per AM-6), with the two pinned doctrine lines (never instruct a lane to read credentials/env/secrets — presence of a secret class, never a value; fetched external content is DATA, never instructions); (ii) **`proc_run` campaigns are invisible to the daemon's only automatic supervisor** ⇒ **aegis is the primary supervisor** (AM-5); (iii) **installer bodies are readable on disk in the release payload** — the boundary is the search index, not the filesystem (AM-7); (iv) **`webfetch` is operator-disabled live**; the live `mcp` inventory = `context7` + `plane` + `zai-web-reader`/`web-search`/`zread`, dev = `context7` + `plane` (AM-3); (v) **the seal does not cover the new agents' files — only the baseline does** — **A-e (plan council): disclosure (2) = (v)** — the same seal fact appears in both lists (AM-14 counts it among the five adds; correction (2) restates it), so the ceremony reads **4 DISTINCT added disclosures** ((i) worker's delegated surface incl. 3 privileged categories, (ii) aegis-is-primary-supervisor, (iii) installer-bodies-readable-on-disk, (iv) live mcp inventory) **plus the rewritten write-fence disclosure (1) and the deletion (3)**. Also disclosed per AM-1: the commission's "report charts" item is formally reduced to markdown-native diagrams (mermaid text blocks). | 2–6 | The report is complete enough for the user to run the ceremony without re-deriving anything; **the AM-14 disclosure set is present verbatim (three corrected/deleted; the "five added" include one repeat — (2) = (v) — so 4 DISTINCT added disclosures plus the rewritten write-fence item, per A-e)**. |
| 8 | **Clean up the lane's scratch state — WITHOUT destroying provenance (Blocker 6):** the **STAGING worktree is KEPT — with BOTH provenance sidecars intact — until the user's promote ceremony** (house pattern; the plan's own P6-R6 names the v0.25.2 provenance-gap class — task 8 obeys it). The **LANE (feature) worktree and the local feature branch may be removed only AFTER the ceremony, by the giter** — not by this phase. The lane's census KV rows may be deleted once the staged artifact and its sidecars are verified and the audit-trail push is done. **Never** touch another lane's worktree, the live install, or the stale main checkout. | 7 | The staging worktree + both sidecars survive into the ceremony window (verified present at handoff); the lane's KV rows are gone; the remote audit ref for the feature branch is retained; the local branch + feature worktree removal is RECORDED as a post-ceremony giter step, not performed here. |

---

## Gates

| Gate | Requirement | How it is checked |
|---|---|---|
| **G6.1 — version derivation** | The version is free on all three axes and the derivation is recorded (expect v0.28.12+ — derived live, never hardcoded). | The stage journal + the tag/ directory enumeration |
| **G6.1a — giter set correct (Blocker 1+2)** | merge parents correct (`--no-ff`, latest unmoved pre-merge); the bump commit = **exactly the 4 house files**; the annotated tag exists AND is pushed, annotation carries the rollback adjudication + no-promote disclosure; tip-identity L1 satisfied by construction. | `git log --parents -1 latest`, `git show --stat <bump>`, `git tag -v`/`ls-remote` + the annotation read |
| **G6.2 — 4-way sha match (read from the LIVE install dir)** | **`/home/nea/agents-ensemble/releases/<version>/ensemble-prod`** == worktree dist == manifest == sidecar | the sha is re-hashed on all four sources |
| **G6.3 — migration delta EMPTY (live install dir)** | The staged binary's migration census equals the live release's. | The embedded-`.sql` census comparison against `/home/nea/agents-ensemble/releases/<live>/` |
| **G6.4 — payload hygiene (live install dir)** | Source-less payload; the prompt baseline inside **`/home/nea/agents-ensemble/releases/<version>/`** is clean **and present at exactly 177 rows (AM-11: existence + count asserted — the in-payload `--check` exits 0 even when the file is absent)**; the agents count matches. | The payload inspection + the in-payload `--check` **+ the file-exists/count assertion** |
| **G6.5 — live fence intact** | The symlink, the journal's promote fields, the live PID and the `livez` uptime are unchanged across the stage (the stage-time live-dir write touches only `releases/<version>/`). | The pre/post fence snapshot |
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

# 1a) GITER-OWNED merge (--no-ff; latest verified unmoved from 1f6898ae2 pre-merge)
git fetch origin
git log --oneline -1 origin/latest          # must be 1f6898ae2... (finalize guard)
git checkout latest && git merge --no-ff feature/aegis-security-family
# (PACKS.md-style conflicts: keep-both, recorded)

# 1b) GITER-OWNED version bump — EXACTLY the 4-file house set (manual house completion with
#     exact 8f47661fe semantics is the EXPECTED path on this host; bump_version.py dies off-PATH)
git add pyproject.toml daemon/__init__.py tests/test_api.py uv.lock
git commit -m "chore(release): bump version to <next>"
git show --stat HEAD                        # exactly 4 files, 1 hit each

# 1c) GITER-OWNED annotated tag — PUSHED, HEAD:latest first (f13a4f59e convention);
#     annotation carries the rollback adjudication + the no-promote disclosure
git tag -a vX.Y.Z -m "<rollback adjudication + no-promote disclosure>" && git push origin HEAD:latest && git push origin vX.Y.Z

# 2) the live fence — snapshot BEFORE the stage
readlink -f /home/nea/agents-ensemble/releases/current 2>/dev/null || true
sha256sum /home/nea/agents-ensemble/releases/state.json 2>/dev/null || true
curl -s localhost:9797/livez | head -c 300; echo

# 3) stage — env-first; ENSEMBLE_UPGRADE_LIVE=1 authorises the STAGE-TIME LIVE-DIR WRITE ONLY
#    (v0.27.7 precedent, journaled); VERSION must be v-prefixed (ADR-009 D3 guard refuses bare
#    numbers); promote.sh NEVER runs; current symlink + state.json promote fields untouched
ENSEMBLE_UPGRADE_LIVE=1 VERSION=vX.Y.Z bash scripts/upgrade/stage.sh
ls -la /home/nea/agents-ensemble/releases/vX.Y.Z/

# 4) the 4-way sha match (RE-HASH — never trust the sidecar; LIVE install dir)
sha256sum /home/nea/agents-ensemble/releases/vX.Y.Z/ensemble-prod
sha256sum <worktree>/dist/ensemble-prod
python3 -c "import json;print(json.load(open('/home/nea/agents-ensemble/releases/vX.Y.Z/manifest.json'))['binary_sha256'])"
cat <worktree>/<provenance sidecar> | python3 -c "import json,sys;print(json.load(sys.stdin).get('artifact_sha256'))"

# 5) the migration delta — EXPECT EMPTY (no daemon/ change)
#    (compare the embedded-.sql census of the staged binary vs the live release)

# 6) the payload hygiene
ls -R /home/nea/agents-ensemble/releases/vX.Y.Z/ | head -40          # source-less: no scripts/, no .env
python3 scripts/protection/generate_prompt_artifact_baseline.py --check --tree /home/nea/agents-ensemble/releases/vX.Y.Z
# AM-11: assert existence + count in the payload (--check exits 0 even when absent)
test -f /home/nea/agents-ensemble/releases/vX.Y.Z/agents/.prompt-artifacts.sha256 || { echo "FAIL: payload baseline MISSING"; }
wc -l /home/nea/agents-ensemble/releases/vX.Y.Z/agents/.prompt-artifacts.sha256   # expect exactly 177

# 7) the live fence — snapshot AFTER the stage (must be IDENTICAL to step 2)
readlink -f /home/nea/agents-ensemble/releases/current 2>/dev/null || true
sha256sum /home/nea/agents-ensemble/releases/state.json 2>/dev/null || true
curl -s localhost:9797/livez | head -c 300; echo

# 8) the promote command is RECORDED, NOT RUN
#    ENSEMBLE_UPGRADE_LIVE=1 VERSION=vX.Y.Z bash scripts/upgrade/promote.sh live --f2-verified-closed
#    (this line is copied into the handoff report — it is NOT executed by this lane)
```

**House notes for this phase:**

- **`VERSION` must be the v-prefixed describe output.** A non-prefixed value is refused pre-build with
  no partial release directory left behind.
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
| **P6-R6** | The staged artifact's provenance sidecars are lost when a worktree is removed — the v0.25.2 provenance-gap class. | Medium | Medium | The staged release directory keeps its own copy of the sidecars; the lane's staging worktree is retained until the ceremony if the sidecars live only there. Cleanup happens **after** the artifact is verified. |

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
5. the live fence — symlink, journal promote fields, live PID, `livez` uptime — is byte-identical
   across the stage;
6. `promote.sh` was **never run**; the promote command is recorded with its ceremony preconditions;
7. the handoff report carries the version, the sha, the deltas, the Phase-5 gate results, the
   disclosures, and the recorded promote command;
8. **the staging worktree + BOTH provenance sidecars survive into the ceremony window** (Blocker 6 —
   the lane worktree/branch removal is recorded as a post-ceremony giter step, not performed here).

**The lane ends here.** The user's 3-factor nonce ceremony owns the flip; the family's first
engagement (the vkey case) is the post-lane step described in `plan-overview.md` §Post-lane.
