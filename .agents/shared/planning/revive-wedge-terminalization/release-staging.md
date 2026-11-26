# Release Staging — revive-wedge terminalization (STAGED ONLY, NO promote)

Base: worktree `/home/nea/ensemble-src-wt-revive-wedge`, branch `fix/revive-wedge-terminalization`, HEAD `35ec50e3c9b3fc9a487e4f2dbad23c15a0837f45` (base tag `v0.31.54`). **HISTORY: at repair time `origin/latest` was `ed8ab508c` = `v0.31.56` — TWO TAGS AHEAD of this base**; the whole touch set was unchanged between them (only `.agents/tester/PACKS.md` moved, +2/−2). **As fetched 2026-11-26T00:27Z: `origin/latest` = `f83fb5d81`; `v0.31.59` IS an ancestor; `v0.31.60` untagged — RE-DERIVE at cut time**. Planning artifact — **read before bumping and staging; the slot is assigned, the sequence is not.**

**The ceremony is the Emperor's.** This lane produces a staged release only. `promote.sh` (which would add `--f2-verified-closed`) is NOT run by this lane.

---

## 1. VERSION SLOT — derive AT BUMP TIME, never reserve

- **Rule**: the version slot is claimed by cutting the local annotated tag at the merge tip at stage time — never reserved in advance. A missing release dir does NOT mean a free slot; the tag namespace is the authority.
- Enumeration command (verbatim):
  ```sh
  git tag --list 'v*' --sort=-v:refname
  ```
- **Next-patch rule (REVISED 2026-11-26)**: the slot is **EXPECTED `v0.31.60`** (front door 2026-11-25 ~21:45Z; last of the current stack `.55`–`.60`; NOT a reservation — re-derived live at cut time). HISTORY at plan time: remote tags `v0.31.55`→`dd007f16d` and `v0.31.56`→`ed8ab508c` were TAKEN and `origin/latest` was `ed8ab508c` = `v0.31.56`. As fetched 2026-11-26T00:27Z: `origin/latest` = `f83fb5d81`, `v0.31.59` IS an ancestor (PUSHED), `v0.31.60` has no local or remote tag; local tags run to `v0.31.59`. Re-enumerate immediately before the bump (`git ls-remote --tags origin 'v0.31.*'` + local tags + release dirs); if `.60` is taken, take the next free slot and REPORT the change. Never re-cut a pushed tag.
- `binary_version` = tag minus leading `v` (stage.sh:430). Tag namespace is `v`-prefixed `vX.Y.Z`; bump types are patch/minor/major (`scripts/bump_version.py`).

---

## 2. ORDERED SEQUENCE (bump → merge → tag → stage → 4-way sha)

```
bump (scripts/bump_version.py)  →  merge to origin/latest  →  ANNOTATED TAG AT THE MERGE TIP
  →  stage worktree DETACHED at the tag  →  BUILD (rm -rf dist/ → FE rebuild → binary + sidecar)
  →  stage.sh  →  4-way sha check
```

Exact commands:

```sh
# ---- 0. re-derive the slot at bump time ----
cd /home/nea/ensemble-src-wt-revive-wedge
git ls-remote --tags origin 'v0.31.*' | tail -6    # W9 re-derive: v0.31.55/.56 taken, .59 PUSHED, .60 free as fetched 2026-11-26 => $V = v0.31.60 (EXPECTED; re-derive live)
V=v0.31.60   # EXPECTED slot (front door 2026-11-25 ~21:45Z; W9 free as fetched 2026-11-26); re-check before the bump

# ---- 1. BUMP (bump_version.py edits version files, then `uv sync` AFTER edits) ----
#    NOTE (residual): bump_version.py runs `uv sync` AFTER its edits (:116) — a death
#    between edit and sync leaves partial state; verify the tree before tagging.
#    The bump is performed by the owning lane; this plan does not re-run it blindly.
python scripts/bump_version.py --version "$V"        # confirm the exact argv against the script's --help first
git add -A && git commit -m "chore: bump version to $V"

# ---- 2. MERGE to origin/latest (leader/owner ceremony; NOT this lane's act here) ----
#    CONTENT ORDER: the false-halt cure v0.31.59 lands FIRST. GATE: v0.31.59 must
#    be an ancestor of `latest` before merging/tagging, else HOLD and report.
#    REF-CONTENTION PROTOCOL: (a) read the CURRENT local `latest` tip BEFORE merging;
#    (b) claim the ref on the BOARD before merging (lane, target commit, window) via
#    a project-manager dispatch; (c) ONE lander at a time on that ref.
#    The fix branch is merged to latest with the standard gate; $MERGE_SHA is the tip.
git checkout <integration-branch> && git merge --no-ff fix/revive-wedge-terminalization
MERGE_SHA=$(git rev-parse HEAD)

# ---- 3. ANNOTATED TAG AT THE MERGE TIP ----
#    IMMEDIATELY before cutting: re-read the CURRENT local `latest` tip and ASSERT it
#    equals $MERGE_SHA (another landing lane may have displaced the merge). If not:
#    STOP, re-merge and re-derive the slot live.
git tag -a "$V" "$MERGE_SHA" -m "$V — revive-wedge terminalization (narrow + decoupling + riders + additive observability)"
# verify the tag resolves to the exact merge tip and is annotated:
git cat-file -t "$V"                                   # -> tag
git rev-list -n 1 "$V"                                 # -> $MERGE_SHA

# ---- 4. STAGE WORKTREE, DETACHED AT THE TAG ----
STAGE_WT=/home/nea/ensemble-src-wt-stage-${V#v}        # e.g. ...-stage-v03155
git worktree add --detach "$STAGE_WT" "$V"
cd "$STAGE_WT"
git describe --tags --exact-match HEAD                 # MUST equal $V (else stage.sh exits 78)
git rev-parse HEAD                                     # MUST equal $MERGE_SHA

# ---- 4b. BUILD (FIRST stage of a freshly cut tag — do NOT skip) ----
rm -rf dist/                                           # never stage an artifact built in another tree
bash scripts/upgrade/_build_frontend.sh                # FE rebuild; stamps the FE provenance sidecar (stage.sh never builds the FE)
# ---- 5. STAGE (LIVE) — the FIRST stage builds the binary itself: NO --skip-build ----
ENSEMBLE_UPGRADE_LIVE=1 ENSEMBLE_ROLLBACK_SAFE=1 VERSION="$V" bash scripts/upgrade/stage.sh live
# ---- 5b. RE-STAGE of the ALREADY-BUILT tree ONLY ----
# ENSEMBLE_UPGRADE_LIVE=1 ENSEMBLE_ROLLBACK_SAFE=1 VERSION="$V" bash scripts/upgrade/stage.sh live --skip-build

# ---- 6. 4-WAY SHA CHECK (see §5) ----
```

**NO PROMOTE.** The live promote command shape is `ENSEMBLE_UPGRADE_LIVE=1 VERSION=$V bash scripts/upgrade/promote.sh live --f2-verified-closed` — TARGET=live requires BOTH the env guard AND the explicit `--f2-verified-closed` operator flag (MINOR-4b, a second factor, not a replacement). **DO NOT RUN IT.** The staged release waits for the Emperor's ceremony.

---

## 3. THE BUILD + STAGE COMMAND

**First stage of a freshly cut tag — BUILDS (no `--skip-build`):**

```sh
rm -rf dist/ && bash scripts/upgrade/_build_frontend.sh
ENSEMBLE_UPGRADE_LIVE=1 ENSEMBLE_ROLLBACK_SAFE=1 VERSION=$V bash scripts/upgrade/stage.sh live
```

**Re-stage of an ALREADY-BUILT tree ONLY:**

```sh
ENSEMBLE_UPGRADE_LIVE=1 ENSEMBLE_ROLLBACK_SAFE=1 VERSION=$V bash scripts/upgrade/stage.sh live --skip-build
```

- Run from the **stage worktree** (`cd $STAGE_WT`) — CWD selects the executing script.
- `live` additionally passes `require_live_guard` (stage.sh:190).
- `promote.sh` would add `--f2-verified-closed` — **DO NOT RUN IT** (this lane is staged-only).
- `ENSEMBLE_ROLLBACK_SAFE=1` is legitimate here only under the §4 delta proof.
- **Build BEFORE stage (approver iteration-002 note 10):** `stage.sh` NEVER builds the frontend — it only verifies `frontend/dist/frontend/browser` and the sidecar the wrapper stamped (`stage.sh:348-355`, exit 78 otherwise). Run `bash scripts/upgrade/_build_frontend.sh` in the stage worktree first (a bare `npm run build` is `provenance-missing`). `rm -rf dist/` first, so the build can never reuse an artifact from another tree.
- **`--skip-build` is for a RE-STAGE only:** without it (`stage.sh:224-285`) stage.sh runs the PyInstaller build when `dist/ensemble-prod` is absent (`:226-229`) and writes the build-provenance sidecar (`:284`); with it (`:290-298`) the binary must already exist, else 78 (`:294-296`).

---

## 4. rollback_safe CAVEAT — precise

**The derive path is FULL-HISTORY, not delta-scoped.** When `ENSEMBLE_ROLLBACK_SAFE` is unset, stage.sh derives it (stage.sh:106-110, 394-420) via:

```sh
grep -liE 'DROP[[:space:]]+TABLE|DROP[[:space:]]+COLUMN' \
  "$REPO_ROOT"/daemon/migrations/versions/*.sql
```

This greps the **entire** migration history (stage.sh:384-389), not the delta since the prior tag. Stage.sh:399-403 states the full-history destructive-DDL grep is "INFORMATIONAL — it is not delta-scoped and fires on DDL applied before the previous release tag too." Since the v0.15.3 D-FA4.5 supersession, a match + unset env **REFUSES with exit 78** (the old silent-false path bit v0.14.2 and v0.15.1). On this legacy tree the grep matches historical DDL, so the derive path exits 78 — hence the explicit `=1` in the stage command.

**`=1` is legitimate ONLY when the migration delta between the prior tag and `$V` is EMPTY** (no schema drift introduced by `$V` itself) — stage.sh:405-407: "only set ENSEMBLE_ROLLBACK_SAFE=1 when the migration delta between the previous release tag and $VERSION is EMPTY."

Compute the tag-to-tag migration delta:
```sh
PREV=$(git tag --list 'v*' --sort=-v:refname | sed -n '2p')   # prior tag (e.g. v0.31.59 for $V=v0.31.60)
git diff --name-status "$PREV" "$V" -- 'daemon/migrations/versions/*.sql'
# also disclose any destructive DDL newly introduced in the delta:
git diff "$PREV" "$V" -- 'daemon/migrations/versions/*.sql' | grep -iE '^\+.*(DROP[[:space:]]+TABLE|DROP[[:space:]]+COLUMN)' || echo "NO new destructive DDL"
```

**Disclose the delta in the plan/release notes** (the tag-to-tag delta must be disclosed; it is the legality proof for `=1`).

**Expected delta for THIS release: EMPTY.** The fix changes guard/control-flow logic in `child_reports.py` (the mint-guard evidence query + the terminalization seam) and adds an additive tool parameter in `system_log_tools.py`; the sweep file is **not** modified. No new table/column ⇒ no new `daemon/migrations/versions/*.sql` ⇒ the migration delta is empty and the `ENSEMBLE_ROLLBACK_SAFE=1` override is legitimate. **Version-file rule:** re-fetch `origin/latest`, READ the tip's five version-bearing files (`CHANGELOG.md`, `daemon/__init__.py`, `pyproject.toml`, `tests/test_api.py`, `uv.lock` — at `ed8ab508c` they declare `0.31.56`), and make the landing take the HIGHER version; `git describe --exact-match HEAD == VERSION` is the contract; post-merge, re-read the five files at the merge tip and assert they declare the highest slot. Never let the declared version go backwards.

---

## 5. THE 4-WAY SHA CHECK

The release binary must have FOUR equal sha256 values:

1. `releases/$V/ensemble-prod` in the install dir
2. build output `dist/ensemble-prod`
3. `binary_sha256` in `releases/$V/manifest.json`
4. `artifact_sha256` in the build-time sidecar `<artifact>.build-provenance.json`

```sh
INSTALL_DIR=${INSTALL_DIR:-/home/nea/agents-ensemble}   # confirm the live install dir before use
echo "1 release-dir : $(sha256sum "$INSTALL_DIR/releases/$V/ensemble-prod" | cut -d' ' -f1)"
echo "2 dist        : $(sha256sum "$STAGE_WT/dist/ensemble-prod" | cut -d' ' -f1)"
echo "3 manifest    : $(python3 -c "import json;print(json.load(open('$INSTALL_DIR/releases/$V/manifest.json'))['binary_sha256'])")"
echo "4 sidecar     : $(python3 -c "import json,glob;f=glob.glob('$STAGE_WT/**/*.build-provenance.json',recursive=True)[0];print(json.load(open(f))['artifact_sha256'])")"
# All four lines MUST print the same 64-hex value. Record it (precedents:
# v0.29.15 "0d542bd1…8451"; v0.29.0 "4bdb28ce…" — "release dir == dist == manifest == sidecar").
```

**stage.sh RE-HASHES the artifact and never trusts the sidecar's recorded sha** (L2 provenance, stage.sh:287 region; refusal tokens `provenance-missing` / `stale-provenance` / `dirty-build` / `provenance-hash-mismatch` → exit 78, journaled). Manifest = flat checksums: six top-level shas (launcher/binary/config/agents/frontend/plugins) + `pipeline_scripts_floor_commit` + per-file maps (stage.sh:50-55).

---

## 6. STAGE PRECONDITIONS the operator will hit

| # | Precondition | Anchor | Failure |
|---|--------------|--------|---------|
| 1 | **L1 tip identity**: staging tree HEAD == `origin/latest` at stage time (non-tip tree refused) | stage.sh:92-104, 195 | exit 78 `non-tip-tree` (journaled). Override argv-only `--allow-stale-stage` (journaled `stage_freshness_override`, operator_accepted=true). |
| 2 | **L2 provenance**: sidecar stamps `git_head` / `git_dirty` / `artifact_sha256`; stage re-hashes | stage.sh:280-287, 306-307 | `provenance-missing` / `stale-provenance` / `dirty-build` / `provenance-hash-mismatch` → exit 78. |
| 3 | **Exact tag at HEAD**: `git describe --tags --exact-match HEAD` must equal `$VERSION` | stage.sh:203-204 | exit 78 ("no auto pull, no network fetch ANYWHERE"; ADR-009 D3: stage what was built). |
| 4 | **Explicit VERSION**: no default, no auto-pull; plain token (no `/`, leading `.`, spaces) | stage.sh:86-90, 195-199 | exit 78. |
| 5 | **Build BEFORE stage** (approver iteration-002 note 10): the frontend build is a SEPARATE step — `bash scripts/upgrade/_build_frontend.sh` (wraps `npm run build` + stamps the FE provenance sidecar; stage.sh only VERIFIES it — a bare `npm run build` is `provenance-missing`). The FIRST stage of a freshly cut tag runs WITHOUT `--skip-build` (after `rm -rf dist/`), so stage.sh performs the PyInstaller build and writes the build-provenance sidecar. **`--skip-build` is for a RE-STAGE of an already-built tree only** — it then consumes the pre-built `dist/` artifact, which must exist and hash-match (§5). | stage.sh :224-285, :290-298, :348-355; self-upgrade blueprint | missing/mismatched dist → 78 (`provenance-*`); FE-less release → 78. |
| 6 | **rollback_safe**: `=1` only under the §4 empty-delta proof; unset derive exits 78 on this legacy tree | stage.sh:106-110, 384-420 | exit 78. |
| 7 | **Live guard**: `ENSEMBLE_UPGRADE_LIVE=1` + TARGET=live `require_live_guard` | stage.sh:190 | refusal without env. |
| 8 | **Content order + ref contention**: the false-halt cure `v0.31.59` lands FIRST and must be an ancestor of `latest` before merging/tagging; read the CURRENT local `latest` tip before merging; claim the ref on the BOARD first (lane, target commit, window) via a project-manager dispatch; ONE lander at a time; re-read and ASSERT the tip equals this lane's merged commit immediately before the tag | front door 2026-11-25 ~21:45Z; §2 steps 2–3 | HOLD and report; if displaced, re-merge and re-derive the slot live |

Separate lever (do not confuse): promote rides a **PINNED scripts checkout** (`PIPELINE_SCRIPTS_FLOOR_COMMIT`; escape `ENSEMBLE_ALLOW_STALE_SCRIPTS=1`) — that is the promote arm-time guard, not the stage freshness guard. The arm-time scripts-dir provenance guard verifies the pinned checkout vs floor before the nonce; stale/non-floor-descendant refuses pre-nonce (rc=1→78).

**Failure-code family**: 78 = operator/refusal (non-tip-tree, provenance-*, stale-provenance, dirty-build, version mismatch, rollback derive). All are fail-closed; none are silently overridden by this lane.

---

## 7. RELEASE NOTES OUTLINE

entries shaped `## [v0.31.60] — <date>`

- **(a) Defect + root cause (one paragraph)**: v0.31.52 (commit `58fc594eb`) widened the report-mint guard in `daemon/services/child_reports.py` from anchor-exact to a per-child LIFETIME PREFIX; the guard early-returns `idempotency_skip` (4698-4705) BEFORE the terminal status flip (4901-4917), so a revived child that already delivered a report can never terminalize — it stays `status='running'` forever. LIVE and FLEET-WIDE.

- **(b) The narrow — POLICY CHANGE stated plainly**: **"at most one OUTSTANDING obligation per child; a delivered prior report no longer blocks a new turn."** The mint guard narrows from a lifetime prefix to a **per-ANCHOR key — the CALLER's per-call token (`child + ':' + completed_message_id`, compared character-for-character; it is NOT a turn-scoped key and NOT a `BaseMessage.id`) — plus an outstanding-obligation arm.** Consequence to state: **a revived child's new turn now produces a second report row that the parent receives** (the prior COMPLETED report no longer suppresses it). This is the intended behavior change, not a leak.

- **(c) The decoupling**: terminalization no longer depends on the mint. On mint suppression the code sets `suppress_mint=True` and continues to the guarded terminal CAS (4901-4917) without minting a duplicate report; the B4 cycle-2 re-mint (6563-6630, gated on `inst.status == COMPLETED` at 6581) can now fire. b4's duplicate-obligation protection — **at most one OUTSTANDING obligation per child** — is preserved by the outstanding-obligation arm; the per-child duplicate-freedom itself comes from the terminal-entry guard + CAS-before-mint atomicity + the per-parent lock.

- **(d) The riders**: the Shape-B stale-running sweep's drive stops being pointless — with the narrow + the decoupling, the drive now TERMINALIZES the victim (the post-drive read lands in `healed`, never `drive-no-effect`), so the victim leaves the candidate set after one tick and is never driven again. The sweep file is not modified; the 2–7 s per-victim checkpoint read is **NOT eliminated** — it is paid **once per victim**, at the cure tick (worst case `DEFAULT_BATCH_CAP=25` in the first post-fix tick — one-time), because the cured instance is terminal and leaves the candidate set. No new SQL, no column, no migration.

- **(e) The observability change**: `ens_system_log_search` gains an **additive** parameter — `start_line: int | None = None`, the SETTLED name and signature per `observability.md` (§3.1, §9 — P-B1…P-B8) and `plan.md §11` (e); aligned in repair 006; the default path is **byte-identical** (same deque window, absolute line numbers, `scanned N lines` header, caps, error strings). The privileged-category set and tool-name pins are untouched. (Param name and semantics per observability.md — no longer provisional.)

- **(f) THE INTERIM POSTURE — document AS INTERIM**: until this release lands, **cold-spawn all follow-ups; do NOT reuse/revive a completed instance**. State explicitly that this posture **LIFTS WHEN THIS RELEASE LANDS**; never present it as permanent guidance.

- **(g) Migration / rollback-safety statement**: no new table/column, so no new `daemon/migrations/versions/*.sql`; tag-to-tag migration delta is empty (disclose the computed delta); `rollback_safe=true` is legitimate under that empty-delta proof; staged release only, NO promote.

- **(h) The two live victims**: `a5c2c2be` and `99b3c1a7` are pinned as the live victim class. They **self-heal after this release lands**; **NO lifecycle action in this lane** (no terminate/pause/revive/spawn). Their owning parents may terminate them meanwhile; the stale-running sweep continues to drive them with no effect until the fix is live.

- **(i) THE R-K2 OBSERVABILITY (the accepted loss becomes countable, not silent)**: on every ACTUAL ARM-B-driven suppression the chain emits ONE loud record — `child_reports|arm-b-suppress: class=arm-b-suppress child=%s incoming_anchor=%s outstanding_anchor=%s outstanding_message_id=%s loss=%s total=%d` — carrying the child, **BOTH** anchors and the outstanding marker id; the record is emitted in the suppress branch (the defer path emits nothing), and the module-level `_ARM_B_SUPPRESS_LOSS_TOTAL` counter increments by one ONLY when `loss=1` — the R-K2 content loss, where the newer turn's report was actually dropped (a same-obligation no-op is `loss=0` / +0; the running value rides in every such line; it resets on daemon restart). The leader accepted the R-K2 content loss **only with** this observability, and it ships with this release. **Also shipped (leader rulings 6/7, repair 005):** an ARM-A-only suppression emits ONE `child_reports|arm-a-suppress: class=arm-a-suppress …` WARNING (child + both anchors + the matched marker id + status), and a mint that proceeds while an older obligation for the child is in `retrying` emits ONE INFO `child_reports|retrying-obligation: class=retrying-obligation …` record — `retrying` is deliberately NOT a blocking status.

- **(j) COMPONENT C — THE PARENT-COMPLETION FIX (NEW in this release):** a parent now completes **at the moment its last child finishes**. Today the completion guard's pending-task count refuses that completion when the parent's own queue holds the WaitingChildren watchdog's wake notice — a notice that exists only to tell the parent to complete — or a PENDING task that is already dead; the specimen logged `completed message with 0 pending children but 1 pending task(s), deferring completion (pending-tasks guard)` while the parent held its job row and its queue lock for hours (the fleet-wide follow-on job was blocked for 21+ h). **Policy change, stated plainly:** (1) a PENDING task whose message is a WC-watchdog notice (`source` ∈ {`system:watchdog`, `system:watchdog:wedge`} or metadata ∈ {`watchdog_notice`, `watchdog_escalation`, `watchdog_release`, `wedge_notice`}) **never defers** the completion it exists to trigger; (2) a PENDING, non-deferred task older than 60 s (`DEFAULT_MIN_PENDING_AGE_SECONDS`) is treated as **dead** and does not defer completion — the mirror of the repo's own orphan rule, so a dead task can no longer defer a completion for hours. The pre-existing INJECTED-report exclusion is unchanged. **The watchdog's delivery semantics are UNCHANGED** (it still wakes the parent; the notice bodies, cooldowns and episode bookkeeping are untouched); **no config flip, no env knob, no migration, no index, no lifecycle action**. **Disclose what this does NOT fix:** the general "running-by-status / finished-by-fact" liveness-gate class is NOT fixed (an own commission is owed — watchdog W-B liveness gate + `mission_live_guard` + the frozen `last_activity_at` clock), and the two zombie roots observed tonight cannot be hand-killed (the fleet terminate budget is 3/3) — they are reaped only if a NEW completion evaluation happens after this release is LIVE, which is **not guaranteed**).

- **(k) LEG-2 RESIDUAL — DISCLOSED, NOT FIXED BY THIS RELEASE:** the WaitingChildren watchdog's W-B liveness gate suppresses hang notices for children that are running-by-status but finished for other reasons. It is NOT a code change in this lane (the watchdog / liveness-gate files are outside the fence); an own commission is owed (critical note `c95be251`; the discriminator must key on a signal that cannot be true of a finished child, and suppressed notices must be logged). For the mint-guard class the cure is indirect: once the child terminalizes it is no longer live-by-status. The general class remains OPEN and must be stated as such in the release notes.
- **(l) K11 — THE CURE MINTS A BACKLOG REPORT:** a DIFFERENT anchor with a prior COMPLETED report now yields **TWO** report rows, the second carrying the new anchor. This is the intended consequence of the new policy (a delivered prior report no longer blocks a new turn), **not a leak**. Item (j) is reserved for Component C and lands with that implementation.

---

## 8. Honesty ledger

- Version slot **`v0.31.60`** is EXPECTED (front door 2026-11-25 ~21:45Z; last of the stack `.55`–`.60`; not a reservation). HISTORY: at plan time `v0.31.55`→`dd007f16d` and `v0.31.56`→`ed8ab508c` were taken and `origin/latest` = `ed8ab508c`. As fetched 2026-11-26: `origin/latest` = `f83fb5d81`, `v0.31.59` an ancestor, `.60` untagged. Re-enumerate at bump time; if taken, take the next free slot and report.
- **Ceremony binding (state in the release notes in substance):** no ceremony promotes without this fix in the tree; the stack promotes together; the freeze lifts the moment that promotion completes (LIVE, not staged) — staging does NOT lift the freeze. The interim posture (cold-spawn follow-ups; do not revive a completed instance) is labelled **INTERIM** and lifts when the stack promotes.
- `$MERGE_SHA`, the stage worktree path, and the exact `bump_version.py` argv depend on the owner's merge/bump ceremony; the commands show the shape and the anchors, not an executed run.
- The observability parameter is **SETTLED, not provisional** (aligned in repair 006 with `observability.md` §9 — P-B1…P-B8 settle `start_line` definitively — and `plan.md §11` (e)): `start_line: int | None = None`. Section (e) above is reconciled with that file.
- **Repair 006 (2026-11-25, the lane's planner-repair-4 / "BOUNDED PLAN REPAIR 004"):** §7 (i) re-worded — the R-K2 record and the counter are emitted in the suppress branch (nothing at the guard, nothing on a defer) and the counter moves only on `loss=1`; `_BATCH_CAP` → `DEFAULT_BATCH_CAP` in (d). Doc-only; the fence above is unchanged.
- No promote command was run; no tag, commit, worktree, bump, or stage was created by this plan writer.
- **The build step is explicit since repair 005** (approver iteration-002 note 10): `rm -rf dist/` → `_build_frontend.sh` → the first `stage.sh` WITHOUT `--skip-build`; `--skip-build` is a RE-STAGE flag only. Verified by content match in this pass: `stage.sh:224-285` (build branch), `:290-298` (`--skip-build` branch — 78 when the binary is absent), `:348-355` (the FE check — stage.sh never builds the FE).
