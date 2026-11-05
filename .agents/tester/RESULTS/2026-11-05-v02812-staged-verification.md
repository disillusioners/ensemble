# STAGED VERIFICATION — v0.28.12 `aegis-security-family` (independent, final gate)

**Date**: 2026-11-05
**Tester lane**: Tester (4c809759) — 6 worker lanes dispatched, 0 re-dispatches
**Artifact under test**: staged release `v0.28.12` at `/home/nea/agents-ensemble/releases/v0.28.12`
**Staging worktree** (detached @ `01feee49b12c2315ced95359a522f2bcc62a54cd` — holds BOTH provenance sidecars, the only sidecar location): `/home/nea/ensemble-src-wt-stage-aegis`
**Evidence branch**: `tester/v02812-staged-verification` (worktree `/home/nea/ensemble-src-wt-v02812-sv`) — NOT merged into `latest`; leader sweeps.

## 1. Scope decision

The lane's SOURCE-tree functional gate (Gates A–H, 33,843 collected, net-new deterministic reds = 0) already ran on the lane tip. This commission is the **independent STAGED-ARTIFACT gate**: it verifies the bytes that would actually ship, not the tree they came from.

Scope = the leader-defined S1–S5 gate set, **plus one tester-added leg (S3b)** justified by the blast radius: the release bump (`01feee49b`) landed `AFTER` the functional gate ran and touched `pyproject.toml`, `daemon/__init__.py`, `tests/test_api.py` and `uv.lock` — the one thing the functional gate did not re-verify. S3b re-runs the lane's own pack against the STAGED TREE to prove the shipped commit is green.

**Not executed**: the ensure.md Release-Gate live-daemon E2E legs (parent→child, pause/resume, terminate/revive, 3-level cascade). Reason: the release ships **ZERO `daemon/` files** — the only daemon delta vs live v0.28.11 is the version string in `daemon/__init__.py` (S1 L9) — so those legs exercise daemon code paths this release does not touch. This is the lane's own pre-disclosed gap (S4 item 6), surfaced here as a scope decision with reason rather than an omission.

## 2. Verdict

> **GO for the user's promote ceremony.** All 6 gates green — 5 PASS + S4 PASS-WITH-NOTES with 1 disclosure-gap finding. Zero artifact discrepancies. Zero live contact beyond read-only GET probes. The staged binary **boots green in cgroup isolation**, discovers all four new agents with **zero parse failures**, and the live fence is byte-identical across PRE→MID→POST with monotone uptime.
>
> **One pre-ceremony item for the user**: the **chart-category ruling (Q1) is absent from the v0.28.12 tag annotation** (S4 F1) — a ceremony-text disclosure gap. The release bytes are unaffected. See §7.

## 3. Gate results

| Gate | Lane | Verdict | Load-bearing evidence |
|---|---|---|---|
| **S1** artifact integrity | `1f96f48f` | ✅ **PASS** (10/10) | 4-way sha all `54e0d39d…`; **5391/5391** manifest entries re-hashed (0 missing / 0 mismatched / 0 unmanifested); 3/3 tree hashes MATCH; delta vs live = exactly **5 scalars**; baseline **177/177** in BOTH trees; migration census **119 == 119**, 119 distinct prefixes, `uniq -d` empty, git-object diff EMPTY |
| **S3** one-way content proof | `4850b0ed` | ✅ **PASS** | 28 files = 4 agents × 7, each blob byte-identical to its git blob at `01feee49b`; live payload = **0** family files; roster 17 vs 16, set diff exactly `+aegis`; baseline 177 vs 161, +16 exactly 4×{soul,rule,workflow,tools_note}; **9 ONE-WAY** + 2 POOL DIFF |
| **S4** ceremony inputs | `5174b5ed` | ✅ **PASS-WITH-NOTES** | promote provably NOT executed (journal byte-identical ×3, `current` unmoved, 0/306 `v0.28.12` refs); scripts-pin satisfies the staged tree's **own** floor literal on all 4 guard conditions; L1 tip-identity distance **0** |
| **S3b** staged-tree pack re-run | `ca82e172` | ✅ **PASS** | **395/395** aegis pack at `01feee49b` (28.71s) — exactly the functional gate's count, **no collection drift at the bump**; bump's version node **1/1**; version `0.28.12` proven 4-way incl. inside the frozen PYZ |
| **WT hygiene** | `a3d4b679` | ✅ **PASS-WITH-NOTES** | verification-window residue **81 files + 16 dirs** (not the 3+2+1 first estimated) fully restored; birth-time partition proved 81/81 CREATED, 0 rewritten; sidecars intact; `diff -rq` agents trees EMPTY |
| **S2** isolated boot smoke | `dec2d10a` | ✅ **PASS** | banner `Starting Ensemble v0.28.12` · `/livez` 200 `0.28.12` (3.45ms) · `/readyz` 200 **all-true** `reasons:[] grace_active:false leak_signal:false` · **43/43** `agent_dir` inside the scratch tree, 4 new agents, parse failures **0/0/0** · leader `team_members` **17** · **cgroup-isolated** (`/system.slice/ens-smoke-v02812-498a33.service`, provably NOT `ensemble-main`) · live fence **IDENTICAL ×3** with uptime monotone 62953.9 → 63957.8 → 64092.5 · SIGTERM teardown ~1.0s, port freed, throwaway DB+role dropped |

---

## 4. S1 — ARTIFACT INTEGRITY (PASS 10/10)

| Leg | Verdict | Evidence |
|---|---|---|
| L1 4-way binary sha | ✅ PASS | All four equal `54e0d39df3121c2eb74576e0bdfce827e4dadf32a557feeafc21ff02b5547275`; release-dir size 81,594,176 B == staging `dist/` size. Waypoints: release dir · staging `dist/ensemble-prod` · `manifest.binary_sha256` · sidecar `artifact_sha256`. **Re-computed, never trusted.** |
| L2 provenance sidecars | ✅ PASS | Binary sidecar `git_head=01feee49b12c2315ced95359a522f2bcc62a54cd`, `git_dirty=false`, `artifact_sha256=54e0d39d…`. FE sidecar (same head, `git_dirty=false`) `artifact_sha256=8c34ed4f42690629f61993d4c09affe1f7b5b92dfa97c43bbdde3b3ea743acb9`, matching the manifest FE index. Staging worktree porcelain **0 lines**. FE index 3-way: staged payload == worktree == sidecar == `frontend_manifest["dist/frontend/browser/index.html"]`. |
| L3 tag peel | ✅ PASS | Tag object `17214345ccb716a83107511d37fdf96dacdfb9fa` (`cat-file -t` = `tag`); peeled commit `01feee49b…` == worktree HEAD == sidecar `git_head`. `git ls-remote origin 'refs/tags/v0.28.12*'` shows BOTH the annotated object AND the `^{}` peeled line → fully pushed. `merge-base --is-ancestor 38016b5a7 01feee49b` rc=0. |
| L4 payload source-less | ✅ PASS | Top level = `agents/ config.yaml ensemble-prod frontend/ launcher.sh manifest.json plugins/`; **5395** files total (366+95+4930+4). ABSENT: `scripts/`, `daemon/`, `.git/`, `build/`, `.env`, `*.build-provenance.json`, `.staging.*`, `.aside.*`, `*.pyc`, `__pycache__`, loose `*.sql`, `node_modules`. |
| L5 manifest re-hash | ✅ PASS | **5391/5391** entries re-hashed — agents 366/366, frontend 95/95, plugins 4930/4930 — **0 missing, 0 mismatched, 0 files-on-disk-not-in-manifest**. Tree hashes recomputed with the exact house algorithm → **3/3 MATCH** (agents `0d34c989…`, frontend `da1692e8…`, plugins `4715cf64…`). |
| L6 scalar delta vs live v0.28.11 | ✅ PASS | Exactly **5** differing scalars: `binary_sha256` (`03c74e5d…`→`54e0d39d…`), `binary_version` (`0.28.11`→`0.28.12`), `version` (`v0.28.11`→`v0.28.12`), `staged_at` (`2026-11-04T13:36:04Z`→`2026-11-05T07:08:49Z`), `agents_tree_sha256` (`3ad0ea53…`→`0d34c989…`). UNCHANGED: `config_sha256`, `launcher_sha256`, `frontend_tree_sha256`, `plugins_tree_sha256`, `known_schema_gen`, `rollback_safe` (true), `contains_contract_phase` (true), `pipeline_scripts_floor_commit`. Per-file maps: agents **338→366** (+28 added, 0 removed, 2 changed = `.prompt-artifacts.sha256` + `leader/meta.json`); frontend **95→95** (0/0/0); plugins **4930→4930** (0/0/0). |
| L7 prompt-artifact baseline | ✅ PASS | `--check --tree <staging_wt>` → `PROMPT-BASELINE: OK — 177 versioned artifact(s) match` (rc 0); `--check --tree <release_dir>` → **177** (rc 0); live payload → 161. Staged baseline file 177 lines vs live 161 (+16 = 4 agents × {soul,rule,workflow,tools_note}). |
| L8 migration census | ✅ PASS | **Staged 119 embedded `.sql`; live v0.28.11 also 119.** Distinct `YYYYMMDD_HHMMSS` prefixes **119/119**, `uniq -d` **empty** for both. Git-object source set at `01feee49b` = 119 → diff vs staged census **EMPTY**. Staged-vs-live census **EMPTY** both directions (`comm -23`/`comm -13`). Git delta with `-M`, with `--no-renames`, and over the whole `daemon/migrations/` dir → **all EMPTY** (renames cannot hide a delta). `tail -1` = `20261031_120002_create_project_skill_usage_records.sql` == manifest `known_schema_gen`. **0 lane-authored `.sql`.** |
| L9 daemon delta | ✅ PASS | `git diff --stat v0.28.11..v0.28.12 -- daemon/` → **only** `daemon/__init__.py | 2 +-` (`__version__ = "0.28.11"` → `"0.28.12"`). Substantiates the lane's "0 daemon/ files" disclosure. |
| L10 worktree pristine at END | ✅ PASS | `git status --porcelain` → **0 lines**; HEAD still `01feee49b…`; still detached; **both** provenance sidecars still present. |
| +Fence live untouched | ✅ PASS | `current` → `releases/v0.28.11` (mtime 2026-11-04 14:31:37Z, unchanged); `state.json` `current=v0.28.11 previous=v0.28.10 in_flight=None pending_op=None quarantined=['v0.28.5']`; releases census = v0.28.9/10/11/12. |

**Verifier-side method corrections (NOT artifact defects) — each would have produced a FALSE FAIL on a healthy artifact:**

1. **Tree-hash sort key is RELPATH, not the emitted line.** Sorting the emitted `"<sha>  <rel>"` lines (sha-first) yields 3 false MISMATCHes. House algorithm (`scripts/upgrade/lib.sh:2727`/`:2885`): `find . -type f | sed 's|^\./||' | sort` (**sort key = relpath**) → hash each → `printf '%s  %s\n' "$sha" "$prefix$rel"`; `_tree_hash_of_lines` = `printf '%s\n' "$lines" | shasum -a 256 | awk '{print $1}'`. Reproducing exactly → 3/3 MATCH.
2. **`generate_prompt_artifact_baseline.py --check` ignores your cwd.** With no `--tree` it resolves the tree from the **script's** repo root — so `cd <staging_wt> && … --check` silently verified the MAIN checkout (stale at `013895287`) and printed **161** for a 177-artifact tree. Always pass `--tree <dir>` and assert the printed count equals the count you meant.
3. **The `.sql` uniqueness key is 15-char `YYYYMMDD_HHMMSS`**, not the 8-char date. A date-only cut collapses same-day files and manufactures a false duplicate alarm (119 files over 67 distinct dates, 21 multi-file dates). A strict full-line extraction yields 0 on both binaries — a METHOD failure, not an empty registry.

**Independent-method cross-check**: before the method correction arrived, the census was also run via `CArchiveReader.toc` (repo `.venv`, Python 3.13.15) → 119/119/119, identical to the house `strings` method. The `.sql` DATA-TOC census is **interpreter-independent** here (unlike a PYZ `co_consts` walk, which needs the build interpreter for a 3.14 artifact).

**Not verified by S1**: CHANGELOG section placement; per-file re-derivation of the 177 baseline shas independent of the generator; the scripts-pin ancestry precondition (S4 covered it); the live daemon (S4/S2 covered it).

---

## 5. S3 — ONE-WAY CONTENT PROOF (PASS)

| Leg | Verdict | Evidence |
|---|---|---|
| L1 staged payload | ✅ PASS | 4 agent dirs × exactly 7 files = **28**; staged `agents/` = **366** files / 47 dirs vs live **338** / 43. All 28 present in `agents_manifest` with sha256 match (28 matched / 0 mismatched / 0 missing). Manifest completeness: 366 keys == 366 disk files, 0 extra, 0 missing. Every payload blob **byte-identical to its git blob at `01feee49b`** (28/28). |
| L2 live payload | ✅ PASS | Live `agents/` has 43 dirs (listed in full); none of the four present. `find -name` → 0 for each. Whole-live-`agents/`-tree grep for the four names → **0 files**. |
| L3 lane test file | ✅ PASS | `tests/unit/agents/test_aegis_security_family.py` EXISTS in the staging worktree: 157,942 B, 3,409 lines, sha256 `4ca7e7ba342df2a1266d82fca2c398d8e690bf7a2b241ab5658733f1a699fc36`. **No shell pack** (`test/packs/*aegis*` = 0). At live tag `v0.28.11` the file is **ABSENT** (`ls-tree` → only `test_sketcher_agent.py`). |
| L4 leader roster | ✅ PASS | Staged `team_members` length **17**, live **16**. Set diff: staged-only `["aegis"]`, live-only `[]` → exactly `+aegis`, nothing removed. Confirmed by **two independent parsers** (python3 3.12.3 json, jq-1.7). `grep -c aegis` → staged 1 / live 0. |
| L5 baseline counts | ✅ PASS | Staged **177** vs live **161**; added **16**, removed **0**; the 16 = exactly `aegis|hacker|cracker|sweeper` × {rule,soul,tools_note,workflow}. Every added entry's sha256 equals the corresponding L1 payload-file sha. `grep -c skills-template` = **0** (skills-template bodies are not baseline-tracked). House generator: `PROMPT-BASELINE: OK — 177` (staged) / `161` (live). |
| L6 tool surface | ✅ PASS | All four carry an **explicit NON-EMPTY** `tools.allow` (aegis 15, leaves 6 each) → no default-universe inheritance (the known new-agent trap). `aegis`: allow `[instance, bash, proc, filesystem, time, self, help, context, shared_meta_kv, knowledge, mcp, question, midflight, dynamic-skill, todo]`; deny `[write_file, edit_file, terminate_instance, pause_instance, resume_instance, set_instance_tunable]`; `deny_spawn ["knowledge"]`; team `[hacker, cracker, sweeper, worker]`. Leaves: team `[]`, deny `[write_file, edit_file]`, `deny_spawn` null. `mcp_full_access` **null/absent** on all four; `plane` literal **0** in all four. Leader ruling Q1 verified: **`chart` in NO allow list**. |
| L7 discriminators | ✅ PASS | **9 ONE-WAY, 2 POOL DIFF** (table below). |
| L8 fence | ✅ PASS | `git status --porcelain` empty (re-checked twice, incl. after the baseline generator ran); HEAD `01feee49b…`, detached; staged `agents/` count 366 unchanged; release-dir mtime unchanged. |

### Discriminator table (S3 L7)

| # | Candidate | Staged | Live | Class |
|---|---|---|---|---|
| 1 | `agents/aegis/` (7 files) | YES | dir does not exist | **ONE-WAY** |
| 2 | `agents/hacker/` | YES | absent | **ONE-WAY** |
| 3 | `agents/cracker/` | YES | absent | **ONE-WAY** |
| 4 | `agents/sweeper/` | YES | absent | **ONE-WAY** |
| 5 | `aegis` as a leader team member | YES (len 17) | absent (len 16) | **ONE-WAY** |
| 6 | the 16 baseline entries | YES (177) | absent (161) | **ONE-WAY** |
| 7 | `.lib.sh` toolchains `fuzzing-toolchain` / `credential-bench` / `sast-toolchain` | 2/2/2 | 0/0/0 | **ONE-WAY** |
| 8 | `aegis-strategy.md` skill file | YES | 0 | **ONE-WAY** |
| 9 | `test_aegis_security_family.py` | source tree only (payload is source-less) | absent at `v0.28.11` | **ONE-WAY** (source-scoped) |
| 10 | literal `aegis` inside `ensemble-prod` | **0 hits** | **0 hits** | **POOL DIFF (both-absent)** — agent defs are runtime-loaded from the payload, never compiled into the binary. Not a defect; NOT laundered into a one-way claim. |
| 11 | `frontend/` + `plugins/` trees | 95 + 4930 | byte-identical (`plugins_tree_sha256 4715cf64…`) | **POOL DIFF (no lane content)** |

Staged whole-payload grep for `aegis` (excluding the binary and manifest) hits **14 files** vs **0** in the live payload.

**Process discrepancies (self-corrected, not artifact defects)**: (P1) first manifest re-hash reported MISSING-IN-MANIFEST for all 28 files — a **path-construction bug**: `agents_manifest` keys are SUBTREE-RELATIVE (`.prompt-artifacts.sha256`, `_baby_template/growth.md`), i.e. key K → `<release>/agents/K`; re-run with the correct mapping → 28/28 matched. (P2) a `jq --slurpfile` expression form errored (`Cannot index array with string "team_members"`) → re-run as `jq -n --argjson s … --argjson l … '$s - $l'` → `["aegis"]` / `[]`, agreeing with the python arm.

---

## 6. S4 — CEREMONY INPUTS (PASS-WITH-NOTES)

| Leg | Verdict | Evidence |
|---|---|---|
| L1a live pointer | ✅ PASS | `readlink /home/nea/agents-ensemble/current` → `releases/v0.28.11` (NOT v0.28.12). |
| L1b journal | ✅ PASS | `current=v0.28.11 previous=v0.28.10 in_flight=null pending_op=null quarantined=["v0.28.5"]`; 306 history entries; **0** mention `v0.28.12`; whole-file grep → **0 occurrences**. History tail = mechanical `mcp_server_state` rows, no promote. |
| L1c releases dir | ✅ PASS | `v0.28.9 v0.28.10 v0.28.11 v0.28.12` — v0.28.12 present as a STAGED dir alongside live. |
| L1d journal immutability | ✅ PASS | `state.json` sha256 **identical across 3 samples** (pre/mid/post lane): `2dfcfa0debace0ad617da76d805b072b1207925fb90c9066a4ac553b2a2f9cfa`. |
| L2a pin identity | ✅ PASS | HEAD `84df6b55c8cdb2cbe515a63c75cd10de5343640f`; real linked worktree (`gitdir: /home/nea/ensemble-src/.git/worktrees/ensemble-src-wt-scripts-pin`); porcelain EMPTY. |
| L2b floor ancestry | ✅ PASS | `merge-base --is-ancestor 2147c0298… 84df6b55c…` → **rc=0**; distance `rev-list --count 2147c0298..84df6b55c` = **3**. |
| L2c **staged tree's own floor** (load-bearing) | ✅ PASS | Read AT THE TAG via `git show 01feee49b:<path>`: `daemon/tools/upgrade_tools.py:3923` → `PIPELINE_SCRIPTS_FLOOR_COMMIT = "2147c0298a09a5d7c5a0caeea04cdc51ed1ebcaa"`; `scripts/upgrade/lib.sh:95` → `PIPELINE_SCRIPTS_REV=2147c0298a09a5d7c5a0caeea04cdc51ed1ebcaa`. Both == the pin's floor ⇒ the arm-time guard passes as-is. |
| L2c-bis pin's own stamp | ✅ PASS | Pin `lib.sh:95` = `2147c0298…`; pin `upgrade_tools.py:3923` = `2147c0298…`; `lib.sh` present (391,800 B). |
| L2d promote.sh source | ✅ PASS | The pin contains `scripts/upgrade/promote.sh` at HEAD; the release payload ships **NO `scripts/`** → the promote script necessarily comes from the pinned checkout. |
| L2e live daemon's actual pin | ✅ PASS | Read from the live `ensemble-prod` **child** PID `3762252`: `ENSEMBLE_UPGRADE_SCRIPTS_DIR=/home/nea/ensemble-src-wt-scripts-pin/scripts/upgrade`, `ENSEMBLE_SELF_ENV=live`, **`ENSEMBLE_ALLOW_STALE_SCRIPTS` absent** (no downgrade path in force). |
| L3a staging tree clean | ✅ PASS | HEAD `01feee49b…`; porcelain EMPTY. |
| L3b fresh remote read | ✅ PASS | `git ls-remote origin refs/heads/latest` → `01feee49b…` (remote tip == staged tree). No fetch into the repo. |
| L3c drift distance | ✅ PASS | **0** — staged tree IS the tip ⇒ L1 tip-identity satisfied by construction; a re-stage would be REFUSED by the L1 guard (exit 78) BY DESIGN. |
| L4 tag-annotation disclosure set | ⚠️ PASS-WITH-NOTES | 6 PRESENT, 3 PARTIAL, **1 ABSENT** — §7. |
| L5 manifest ceremony fields | ✅ PASS | `version=v0.28.12` (**v** prefix), `binary_version=0.28.12` (no prefix), `staged_at=2026-11-05T07:08:49Z`, `rollback_safe=true`, `known_schema_gen=20261031_120002_create_project_skill_usage_records.sql`, `pipeline_scripts_floor_commit=2147c0298…`; trees `agents 0d34c989…` / `frontend da1692e8…` / `plugins 4715cf64…`; maps 366 / 95 / 4930. |
| L6 live fence | ✅ PASS | livez `version=0.28.11`; uptime **61037.28s → 61074.85s → 61106.45s** (monotone, no reset); `current` unchanged; MainPID 3762107, **NRestarts=0**, ActiveEnterTimestamp `Wed 2026-11-04 14:31:38 UTC` (unchanged); readyz 200 `status=ready reasons=[] grace_active=false leak_signal.detected=false`. |

### Recorded (NOT executed) promote command — verbatim

```
ENSEMBLE_UPGRADE_LIVE=1 VERSION=v0.28.12 bash scripts/upgrade/promote.sh live --f2-verified-closed
```

---

## 7. Findings

### F1 — DISCLOSURE GAP: chart-category ruling absent from the tag annotation (pre-ceremony item)

The leader's ruling **Q1** (`chart` category DROPPED from aegis v1 — it would grant the chart SPAWN right plus a transitively write-capable child, `chart_tools.py:961`; V2 re-entry = allow `chart` + `deny_spawn ["chart"]`, `registry.py:96-105`) is **not in the v0.28.12 tag annotation**. Grep for `chart` over the annotation body returns only the R2 line "charter/developer prompt-surface debt". The ruling exists in the project meta and the lane's decisions record — it is simply absent from the ceremony text. The underlying behavior IS correct (S3 L6 confirms `chart` appears in NO allow list).

**Impact**: ceremony-text disclosure gap only. Release bytes unaffected; the artifact itself is correct.
**Owner**: the staging/release lane (tag annotation author) — re-cutting the annotated object is a release-lane action, not a tester action. The user may prefer to append it before the ceremony.

### F2–F4 — PARTIAL disclosure items (non-blocking)

- **F2** — the D4 line omits the `pins.env` mechanism and the "operator-recorded digest **with source logged in engagement provenance**" clause that `decisions.md §B.6` carries. The annotation states only "operator-armed pins; the toolchains ship pin-before-first-install placeholders; the refusal contract is unbypassable."
- **F3** — the trap-parse residual line states the mitigation and its T30(b) pin but is **silent on whether the mitigation was independently re-verified** (it was not; the functional gate says so explicitly).
- **F4** — the ensure.md line states the reason (0 `daemon/` files) but does not enumerate the four E2E sub-scenarios.

### F5 — `git_dirty=false` is TRACKED-FILE-BLIND (methodology finding, quantified)

The provenance sidecar's `git_dirty` is computed from `git status --porcelain`, which **excludes ignored files**. The house `_git_dirty_porcelain` (`lib.sh:3413`) uses neither `--ignored` nor `--untracked-files=all`. Consequence, measured: the ceremony-retained staging worktree carried **97 verification-window residue entries** (81 files + 16 dirs) while `git status --porcelain` stayed **0 lines** and `git_dirty` stayed `false` throughout. So `git_dirty=false` certifies **source identity only** — never byte-identity. Ignored-file population **62,693 → 62,612** (delta exactly 81 = files deleted) after restore.

**Ceremony materiality: INERT — proven, not assumed.** The promote path never reads the worktree or its sidecars: `promote.sh:163-164` resolves `REL="$INSTALL_DIR/releases"` / `TARGET_REL="$REL/$VERSION"`; `grep -n 'build-provenance\|provenance' promote.sh` → **zero hits**; `_verify_artifact_provenance` is called **only** from `stage.sh:252` and `stage.sh:308`; `_verify_tip_identity` only from `stage.sh:163` — both stage-time gates; `grep -rn 'wt-stage\|ensemble-src-wt' scripts/upgrade/` → **zero hits**. The sidecars serve stage gates + human audit, never the flip.

**Durable lesson**: a rigorous "pristine worktree" claim must add `git status --ignored --untracked-files=all` (or diff the ignored set explicitly), and a residue restore must partition on **birth time** (`stat --printf='%w'`), not mtime.

### F6 — import-shadow hazard is REAL, not theoretical

Running a pack against a disposable worktree resolves `import daemon` to the **MAIN** checkout: the shell CWD (`sys.path[0]=''`) wins over the editable `.pth`. A probe showed **all three** worktree venvs (lane 3.14.7, stage 3.14.7, main 3.13.15) importing `daemon` from `/home/nea/ensemble-src`. Unforced, the staged-tree pack would have silently tested the wrong tree and reported green for a tree that was never executed. Forced via `PYTHONPATH=<worktree>` + cwd, and proven by printing `daemon.__file__`.

### Host facts recorded for reuse

- `systemctl --user` is **inert on this host** — `systemctl --user show ensemble-main.service -p MainPID` returns EMPTY ("Failed to connect to bus: No medium found"); the unit is SYSTEM-level. Using `--user` makes the MainPID/NRestarts/ActiveEnterTimestamp fence legs **VACUOUS** (empty compares equal to empty). Use plain `systemctl` and assert the MainPID is non-empty and numeric.
- The systemd MainPID is the **launcher shell** (`/proc/<MainPID>/cmdline` = `/bin/bash <install>/launcher.sh`); its exec-time environ carries **no** `ENSEMBLE_*` vars (the launcher exports `.env` after exec) — environ reads must target the `ensemble-prod` **child** pid.
- `pgrep -f 'ensemble.*ensemble-prod|ensemble-prod'` **self-matches the capturing shell** → its PID delta is fiction; use `pgrep -P <MainPID>` or read `/proc/*/cmdline`.
- The live `current` pointer is at the **install-dir level** (`/home/nea/agents-ensemble/current`), NOT `releases/current` — the latter does not exist, and reading a missing path returns the path itself, making that fence leg vacuous.
- `env -u POSTGRES_*` is a **silent NO-OP** (bash does not glob env names). Ambient poison found in this run: `POSTGRES_DB=ensemble_prod`, `POSTGRES_HOST=10.44.0.2`, `PORT=9797`, `ENSEMBLE_SELF_ENV=live`. Scrub per-variable, then set explicitly.

---

## 8. S3b — STAGED-TREE PACK RE-RUN (PASS) — tester-added leg

**Why added**: the release bump (`01feee49b`) landed after the functional gate and touched `pyproject.toml`, `daemon/__init__.py`, `tests/test_api.py`, `uv.lock` — the one thing the functional gate did not re-verify. This leg re-runs the lane's own pack against the exact shipped commit.

| Leg | Verdict | Evidence |
|---|---|---|
| L1 pre-flight | ✅ PASS | Disposable worktree created detached @ `01feee49b`; porcelain EMPTY; import shadow proven fixed (`daemon.__file__` = `<worktree>/daemon/__init__.py`). |
| L2 **aegis lane pack** | ✅ PASS | `timeout 300 <py3.14.7> -m pytest tests/unit/agents/test_aegis_security_family.py --tb=short -q -p no:cacheprovider --timeout=30` → **395 collected / 395 passed / 0 failed / 0 skipped / 0 xfailed / 0 errors**, exit **0**, wall **28.71s**. |
| L3 bump's version node | ✅ PASS | `tests/test_api.py::test_health_check` → **1 passed**, exit 0, wall 3.26s. |
| L4 post-flight | ✅ PASS | Disposable worktree removed (`remove_rc=0`, path gone); staging worktree porcelain EMPTY; live untouched. |
| Version 4-way | ✅ PASS | All 4 house bump files read `0.28.12`; manifest `version=v0.28.12` / `binary_version=0.28.12`; 4-way sha `54e0d39d…`; embedded PYZ constant `['0.28.12']` (exactly one) via CArchiveReader → ZlibArchiveReader → `extract('daemon').co_consts` (no boot). |

**Counts match the functional gate's lane-tip number (395/395) exactly → NO collection drift at the bump.**

**Fences**: ambient env was **poisoned** (`POSTGRES_DB=ensemble_prod`, `POSTGRES_HOST=10.44.0.2`, `PORT=9797`, `ENSEMBLE_SELF_ENV=live`) — scrubbed **per-variable**; effective env under the run command: `POSTGRES_DB=ensemble_dev`, all `POSTGRES_HOST/PORT/USER/PASSWORD/DATABASE_URL/PG*` and `PORT` **unset** ⇒ no remote PG reachable; `POSTGRES_DB != ensemble_prod` asserted programmatically. `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned explicitly. `PYTHONDONTWRITEBYTECODE=1` + `-p no:cacheprovider`. Zero live contact: `/proc` census shows **0** processes with exe under `releases/v0.28.12`; only the pre-existing live (v0.28.11) and demo (v0.16.3) daemons; `state.json` mtime `06:42:11Z` (pre-window).

**Interpreter**: `/home/nea/ensemble-src-wt-aegis-security-family/.venv/bin/python` — Python **3.14.7** (the build interpreter; a certified 3.13 cannot read the 3.14 frozen artifact).

**Disclosure — pre-brief deviation, remediated**: before the full brief arrived, the pack was run 3× inside the staging worktree. Outcome read-only (porcelain stayed EMPTY), but one probe (`python -c "import daemon"` without `PYTHONDONTWRITEBYTECODE`) created 3 `.pyc` + 2 `__pycache__` dirs at 07:26:38Z there; the lane removed exactly those (proving each dir held only its files) and left a concurrent lane's 07:33:25 3.13 `.pyc` untouched. **The hygiene lane (§9) later found the full residue set and restored it.**

**Corroboration**: the pre-brief in-stage-worktree runs gave 3×395 passed at `--timeout=120` (37s each) with the stage venv's 3.14.7 — independent agreement with L2.

---

## 9. STAGING-WORKTREE HYGIENE RESTORE (PASS-WITH-NOTES)

**Scope correction (reported first)**: the residue was **much larger than first estimated** — not "3 files + 2 dirs + 1 pyc" but **81 files + 16 directories**. 80 of the files were `pytest`/`pluggy`/`pygments`/`_pytest`/`iniconfig` bytecode inside the worktree's **own `.venv`**, created at 07:27:16Z; only 1 file matched the second-lane description (`daemon/tools/__pycache__/prompt_artifacts.cpython-313.pyc` @ 07:33:25Z).

- **Census**: 670 entries = 554 `.pyc` + 116 `__pycache__` dirs → **573 pre-existing + 97 verification-window + 0 foreign**.
- **Pre-existing** (mtime < 07:25Z): 552 @ 07:05, 18 @ 07:06, 1 @ 07:08, 2 @ 07:11; 568 in `.venv/site-packages/**`; 5 outside — 4× `build/ensemble/localpycs/*.pyc` + `daemon/tools/__pycache__/prompt_artifacts.cpython-312.pyc` (giter's build).
- **Birth-time proof (decisive)**: `stat --printf='%w'` → **81/81 CREATED inside the window** (80 @ 07:27:16, 1 @ 07:33:25), **0 REWRITTEN_PREEXISTING** ⇒ deletion is a true restore, not a loss.
- **Broader sweep** (`-newermt 07:20Z`, type f): 81 files, **all bytecode, 0 non-bytecode, 0 foreign writes**. The fresh `daemon/` and `daemon/util/` directory mtimes (07:37:37Z) are explained by the first lane's removal of `daemon/__pycache__` + `daemon/util/__pycache__` — metadata-only, zero content change, not ceremony-material.
- **Deleted**: 81 `.pyc` + 11 `__pycache__` dirs (verified empty; `rmdir` only after confirming empty).
- **Deliberately LEFT**: 5 **mixed** dirs holding pre-existing build bytecode (`site-packages/__pycache__` ×3 files, `pygments/__pycache__` ×4, `pygments/formatters/__pycache__` ×2, `pygments/lexers/__pycache__` ×2, `daemon/tools/__pycache__` ×1) — only the window files inside them were removed; `newer_files_inside = 0` for all five.
- **Exactness arithmetic**: ignored-file population 62,693 → 62,612 = delta exactly **81** = files deleted.

**Post-restore integrity proof**: porcelain **0** (before *and* after deletion); `--ignored` shows 7 top-level ignored dirs, bytecode inventory now **578 entries = 473 `.pyc` + 105 dirs**, all pre-existing; HEAD `01feee49b…`, `symbolic-ref -q HEAD` rc=1 (still detached), `describe --tags --exact-match` → `v0.28.12`; **both sidecars intact** with sidecar FILE digests identical pre/post cleanup (`90676c1b…`, `ae8a16f0…`); binary sha `54e0d39d…` (worktree == release == manifest); FE index `8c34ed4f…` (worktree == release payload); `diff -rq <WT>/agents <REL>/agents` → **empty, rc=0**; release dir untouched (`v0.28.12`, `staged_at 2026-11-05T07:08:49Z`, `rollback_safe true`); `git diff --stat HEAD` empty; no process held the worktree.

**Unavoidable metadata delta**: deleting files bumps the mtime of the 5 retained mixed `__pycache__` dirs and their parents to 07:42Z. **Byte content is restored; directory mtimes are not.**

---

## 10. S2 — ISOLATED BOOT SMOKE FROM THE STAGED BINARY (PASS)

**Scratch install root**: `/home/nea/ens-smoke-v02812-20261105T075850Z` — the binary was **COPIED** (never run in place) to `<scratch>/releases/v0.28.12/ensemble-prod`, with the payload trees, `config.yaml`, a `current -> releases/v0.28.12` symlink and a sandbox-marker `.env` beside it, so the frozen binary's path derivation resolved **the scratch root**, not the live install. Binary copy sha `54e0d39d…` == the manifest 4-way value.

**Launch posture (stronger than the brief's minimum):** `sudo -n systemd-run --unit=ens-smoke-v02812-498a33 --uid=nea --gid=nea …` — a genuine transient **system** unit, giving a **separate cgroup** (`/system.slice/ens-smoke-v02812-498a33.service`, provably NOT `ensemble-main.service`). The transient unit inherits **no** client env (verified by probe), so the wrapper set everything explicitly, unset ~40 ambient vars **one-by-one** (never `env -u POSTGRES_*`), asserted **12 fences pre-exec** (fail-closed `exit 78`), printed the fence block into the log, and only then `exec`'d the binary. The boot sweep therefore had **two independent** reasons not to touch live processes: the pinned kill-switch AND structural cgroup exclusion.

| Leg | Verdict | Evidence |
|---|---|---|
| L0 fence PRE | ✅ PASS | `state.json` sha `2dfcfa0d…2a2f9cfa`; `current -> releases/v0.28.11`; MainPID `3762107` NRestarts `0` epoch `Wed 2026-11-04 14:31:38 UTC` — **matches the S4 lane's independent reference exactly**; livez `0.28.11` uptime 62953.9. |
| L1 scratch root | ✅ PASS | Binary copied (sha == manifest); payload trees + config + manifest copied beside it; `current -> releases/v0.28.12`; sandbox `.env`. |
| L2 env scrub | ✅ PASS | ~40 vars unset one-by-one; throwaway **role+DB** `ens_smoke_v02812_ae7bc386` / `ensemble_smoke_v02812_ae7bc386` via `sudo -n -u postgres` (CREATEDB + OWNER — avoids the FK InsufficientPrivilege trap); `ensemble.json` pre-pinned to the throwaway DB (mode 600); full env printed into the log fence block. |
| L3 boot | ✅ PASS | Banner `Starting Ensemble v0.28.12` at daemon.log:67 and :77; **cgroup = `/system.slice/ens-smoke-v02812-498a33.service`** — real cgroup isolation. |
| L4 health | ✅ PASS | `/livez` 200 in 3.45ms → `{"status":"alive","uptime_seconds":14.68,"version":"0.28.12"}`; `/readyz` 200 in 2.72ms, **all four components true**, `reasons:[]`, `grace_active:false`, `leak_signal.detected:false`, pool 0/15, mcp context7 ok. |
| L5 agent census | ✅ PASS | `/api/agents` 200 in 4.8ms → **43** entries, **43/43** `agent_dir` inside `$SCRATCH` (proves the staged payload is what is being served); `aegis`/`hacker`/`cracker`/`sweeper` all present; parse-failure census **0 / 0 / 0** for `Failed to parse meta.json` / `tools config` / `AgentMetadata`. |
| L6 leader roster | ✅ PASS | Scratch payload `agents/leader/meta.json` → `team_members` length **17**, `aegis` present; **byte-identical to the release dir**. |
| L7 fence MID | ✅ PASS | All rows identical to PRE; live uptime 63957.8 (monotone). |
| L8 teardown | ✅ PASS | SIGTERM **only** the scratch pid `460592` (ownership re-proved from `/proc/<pid>/exe` + environ: scratch binary, throwaway DB, port 19312) → **exited in ~1.0s** with the graceful chain (`MCP warm-up pool drained` → `Database engine disposed` → `Graceful shutdown complete`); unit `Result=success ExecMainStatus=0`; **port 19312 FREE**; throwaway DB + role **DROPPED** (catalog counts 0/0); transient unit collected; zero stray scratch processes; zero `.staging.*` residue. |
| L9 fence POST | ✅ PASS | All rows identical to PRE and MID; live uptime 64092.5 (monotone). |
| L10 residuals | ✅ PASS-WITH-NOTES | see below |

### LIVE FENCE — machine-diffed, non-vacuous

| measurement | PRE | MID | POST | judgement |
|---|---|---|---|---|
| `state.json` sha256 | `2dfcfa0d…` | `2dfcfa0d…` | `2dfcfa0d…` | **IDENTICAL ×3** |
| `current ->` | `releases/v0.28.11` | same | same | **IDENTICAL ×3** |
| MainPID | `3762107` | `3762107` | `3762107` | **IDENTICAL ×3** (non-empty, numeric) |
| NRestarts | `0` | `0` | `0` | **IDENTICAL ×3** (no restart) |
| ActiveState / ActiveEnterTimestamp | `active` / `Wed 2026-11-04 14:31:38 UTC` | same | same | **IDENTICAL ×3** |
| livez version | `0.28.11` | `0.28.11` | `0.28.11` | **IDENTICAL ×3** |
| livez uptime_seconds | `62953.87` | `63957.80` | `64092.54` | **CONTINUOUS — monotone** (a restart would reset to ~0) |
| live child pid | `3762252` | `3762252` | `3762252` | **IDENTICAL ×3** |
| :9797 listener owner | `3762258` | `3762258` | `3762258` | **IDENTICAL ×3** |
| readyz | 200 all-true `reasons:[]` | same | same | **IDENTICAL ×3** |

`systemctl --user` was **NOT** used (inert on this host); every row came from plain `systemctl show ensemble-main.service` with a non-empty numeric MainPID **asserted**.

### Live-poison proof (the load-bearing isolation evidence)

The ambient poison (`POSTGRES_DB=ensemble_prod`, `POSTGRES_HOST=10.44.0.2`, `PORT=9797`, `ENSEMBLE_SELF_ENV=live`, `ENSEMBLE_UPGRADE_LIVE=1`, `ENSEMBLE_UPGRADE_SCRIPTS_DIR=…`, `RAG_IS_REQUIRED=true`, live `_MEIPASS` `SSL_CERT_*`) is **absent** from the daemon environ. `grep ensemble_prod` across both scratch logs = **0 hits**; every engine/checkpointer line names `127.0.0.1:5432/ensemble_smoke_v02812_ae7bc386`; sqlite-fallback count = **0**; and — decisively for the path-derivation trap — the boot log records `UpgradeJournalSweepService started … install_dir=/home/nea/ens-smoke-v02812-20261105T075850Z`: **the journal bound to the scratch root, not the live install.**

`/proc/460592/environ` (port-owner child, found via `ss -ltnpH` + parent walk — **no** `pgrep -f` self-match): `POSTGRES_DB=ensemble_smoke_v02812_ae7bc386` · `ENSEMBLE_SELF_ENV=sandbox` · `PORT=DAEMON_PORT=19312` · **`ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0`** · `ENSEMBLE_CONFIG=<SCRATCH>/releases/v0.28.12/config.yaml` · `ENSEMBLE_DATA_DIR=<SCRATCH>/data` · `HOME=<SCRATCH>/fake-home`.

### Expected-vs-actual (all ✓ except three harness-expected, disclosed)

- Banner count 2 (`api.py:4145` + `__main__.py:460`) ✓ · ERROR/CRITICAL 0 ✓ · parse-failure census 0/0/0 ✓ · `Skipping migrations for non-SQLite database` 1 (expected on PG) ✓ · agent count **43** (45 meta-bearing dirs − 2 underscore-prefixed) ✓ · team_members **17** ✓ · SecretCipher/LiveViews warnings 0 (a **fresh scratch-only Fernet key** was supplied — no coupling to live key material) ✓ · webfetch/uvx ERRORs 0 ✓.
- **Harness-expected, not defects**: (a) 6 `journal absent at …ens-smoke…/releases/state.json` WARNINGs — that is the **fence working** (the sweep resolved the scratch install_dir and correctly found no journal there; zero live-journal reads/writes); (b) `[MetricsListener] DISABLED — bind failed on 127.0.0.1:9798` — the **live** daemon owns :9798 (host-wide singleton exposition listener); scratch degraded gracefully to DISABLED with boot unaffected; (c) 2 `plane`-related WARNINGs — a **deliberate harness choice** not to provision real Plane credentials into a scratch daemon (avoids outbound calls against the real workspace); Plane REST sync is not load-bearing for this smoke.
- `maintenancer git_commit deny entry` WARNING ×1 — base-proven pre-existing, benign.

### S2 residuals (none blocking)

1. **First boot attempt failed fast and cleanly** (`exit 1` at the fence assert): the wrapper's `unset PORT` clobbered its own carrier variable. The **fail-closed assert caught it before any daemon started** — no log written, no port bound, no DB contact. Harness carriers renamed (`SMOKE_PORT`/`SMOKE_DB`/`SMOKE_ROLE`) so the scrub list can never clobber them again.
2. **Cgroup isolation exceeded the brief's minimum** (see Launch posture above) — strictly stronger than the shared-cgroup+kill-switch posture of the v0.28.11 precedent.
3. **live `data/logs/ensemble.log.2/.3` mtimes fell inside the smoke window** — verified to be the **live** daemon's own rotation cadence (~2.5-min rotations, chain .5@08:11:44 → .1@08:20:02, all before the 08:16:09 launch). The scratch daemon wrote its own log **inside** `$SCRATCH`. Zero live-install writes by this lane.
4. **Scratch retained (279 MB) as evidence** at `/home/nea/ens-smoke-v02812-20261105T075850Z` — the daemon.log (79 KB, with the fence block at :1-67), the three `fence_*.txt` snapshots, `census.py` and the boot harness are the only copies. Outside `/tmp`, so no tmp-sweeper will eat it. **Pruned to evidence-only after this RESULTS file was written** (binary + payload copies removed; see the evidence-commit lane).

**Timing**: launch → banner 7s · launch → readyz-200 28s (budgets 150s/60s) · serve window 118s · SIGTERM → exit ~1.0s · lane wall ≈ **19m46s**.

---

## 11. Documentation updated

- [x] `RESULTS/2026-11-05-v02812-staged-verification.md` — this file
- [x] `PACKS.md` — v0.28.12 staged-verification pack set registered
- [ ] `rules/ensure.md` — no changes (user-owned, read-only)
- [ ] `MOCK_TESTS.md` — no changes (no mock services)
- [ ] `QUARANTINE.md` — no changes (no flaky tests quarantined; no failures)
- [x] `LESSONS/` — durable lessons captured (see §12)

## 12. Carried lessons / routed items

- **Skill defects filed** (`skill_fix`): `staged-release-artifact-verification` (a2220107) — the live-fence systemd form (`--user` is inert ⇒ vacuous fence) and the manifest subtree-relative key convention; `staged-release-verification-method-traps` (3b7a21df, created by the S1 lane during this run) — add the manifest key convention and the systemd-fence traps as further traps. **NOTE: the skill-evolution admission lane is DISABLED (`ENSEMBLE_SKILL_EVOLUTION_ENABLED` off) — both requests are RECORDED in the skill store with NO keeper job dispatched.** They will not self-apply; a keeper must pick them up when the lane is re-enabled.
- **New skill created by the S1 lane**: `staged-release-verification-method-traps` (3b7a21df) — tree-hash sort key, prompt-baseline `--tree` default, `.sql` census path-prefix + 15-char uniqueness key.
- **New skill created by the evidence-branch lane**: `prepare-tester-evidence-branch-and-worktree` (439d01c1).
- **R1** stale `SEALED_SHA_BASELINES` map (4 entries) → release/seal lane re-pin (base-proven; **this lane touched zero sealed files**).
- **R2** charter + developer prompt-surface debt + param-id instability → prompt-debt lane.
- **R3** `daemon/persistence.py:326` logs the file-config DSN while the effective DSN is env-first — never trust that line as a prod-contact signal → mechanical-file lane.
- **R5** stale test id `test_leader_team_members_length_16` — ledger-bound, deliberately NOT renamed.
- **Retention**: `releases/` holds 4 dirs (v0.28.9/10/11/12) vs keep-3 — eviction is **promote-machinery-owned**; nothing evicted by this verification.
- **Quarantine**: `quarantined: ["v0.28.5"]` is pre-existing; the **target v0.28.12 is clean**.
- **Nonce**: `pending_actions` empty — the 3-factor ceremony mints it; nothing outstanding.
- **Trap-parse residual** (~0.08%, EXIT-trap lock release T30(b)-pinned) — carried; **not independently re-verified** in either gate.

## 13. Lanes dispatched

| # | Lane | Instance | Runtime | Verdict |
|---|---|---|---|---|
| 1 | S1 artifact integrity | `1f96f48f` | ~10m20s | ✅ PASS |
| 2 | S2 isolated boot smoke | `dec2d10a` | ~19m46s | ✅ PASS |
| 3 | S3 one-way content proof | `4850b0ed` | ~8m38s | ✅ PASS |
| 4 | S4 ceremony inputs | `5174b5ed` | ~5m12s | ✅ PASS-WITH-NOTES |
| 5 | S3b staged-tree pack re-run | `ca82e172` | ~16m | ✅ PASS |
| 6 | evidence-branch prep | `42b3a0ab` | — | ✅ branch+worktree ready |
| 7 | WT hygiene restore | `a3d4b679` | ~4m | ✅ PASS-WITH-NOTES |

**0 re-dispatches.** Zero live contact beyond read-only GET probes to `http://127.0.0.1:9797/livez` (+`/readyz`). Port 8088 never touched. The live install, the live release dir and the ceremony-retained staging worktree were never written to by any lane except the deliberate, disclosed, and fully-reverted bytecode residue described in §9.
