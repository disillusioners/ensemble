# Research Findings — promote-machinery "healthy-boot false-halt" fix lane (`promote-handback-false-halt`)

**Lane:** fix/promote-handback-false-halt · worktree `/home/nea/ensemble-src-wt-hbfix` (base `0181dadbf` == origin/latest) · KV `wt.claim.hbfix` (fence verified: `POSTGRES_DB=ensemble_dev`, `DAEMON_PORT=8079`, lazy venv)
**Composed:** 2026-11-17T18:55Z · **Method:** strict read-only — fixture log + adjacent run logs + `releases/state.json` head read verbatim; code anchors verified in the hbfix worktree by `read_file` (grep tools are workdir-bounded to the main checkout; main checkout is BEHIND the worktree for upgrade_tools.py — main shows floor `68c6ee05…` at :4145, worktree shows `1aa0065eb…`; **the worktree value is authoritative**).
**Anchor discipline:** every `file:line` below was read at its CURRENT location in `/home/nea/ensemble-src-wt-hbfix` during this pass.

---

## Q1 — FIXTURE TIMELINE: r-20261117-171207-e2f9

Source: `/home/nea/agents-ensemble/data/upgrade/live/r-20261117-171207-e2f9.log` (25 lines, read whole), `.payload.log` (11 lines), `.client.log` (1 line), plus `releases/state.json` (read-only head) and the daemon sweep code. **READ-ONLY — nothing under /home/nea/agents-ensemble was modified.**

### Timestamped timeline (all quotes verbatim from the .log unless noted)

| UTC | Event | Verbatim evidence |
|---|---|---|
| 17:27:57 | env resolve | `upgrade-promote[live]: resolved env: target=live dir=/home/nea/agents-ensemble port=9797 db=ensemble_prod` |
| 17:27:57 | scripts rev echo | `pipeline-scripts rev=1aa0065eb818ff00cb32730fab54d7f2060c42d8 (executor-start echo; the arm-time guard reads the lib.sh stamp file)` |
| 17:27:57–17:28:12 | preflight | `preflight: lock · integrity · journal txn · entry checks`; watchdog armed (`ENSEMBLE_PROMOTE_WATCHDOG=1`); phase markers journal-read→adopt-txn→entry-check; `WARN: PLUGIN-STALENESS OBSERVED: /home/nea/ensemble-src-wt-scripts-pin/plugins/opendesign is stale (pin-stale) — proceeding (default; journaled for audit)`; `config-compat: CONFIG-COMPAT=ok`; integrity CURRENT v0.30.12 + TARGET v0.31.0 verified |
| 17:28:14 | txn open | `txn open: promote target=v0.31.0 pid=1188298 (outer window 600s from txn start)` |
| 17:28:16 | stop (unit path) | `stop: UNIT path — systemctl stop ensemble-main.service + unit-state poll via /home/nea/ensemble-src-wt-scripts-pin/scripts/upgrade/../stop-ensemble.sh (b″: respawn-invisible-to-policy-poll fix)` |
| (payload) | stop detail | `ENSEMBLE_SUPERVISION_RESULT=UNIT_MANAGED:ensemble-main.service` (×2) · `ENSEMBLE_SUPERVISION_OUTCOME=conforming` · `PIPELINE_LOCK_HELD_BY_CALLER=1 — BOTH Layer (i) settle-check AND Layer (ii) lock-acquire are being SKIPPED` · `port 9797 is held by: 700753 701344 1163298 1176879 1176885 1184677 1184684 1189235 1189249 1189251  (REPORT ONLY — ports are not a kill selector)` · `unit ensemble-main.service stopped (unit not running + MainPID=0 + port 9797 free)` |
| 17:29:27 | launcher swap | `launcher swapped from release v0.31.0 (stopped window)` |
| 17:29:27 | atomic flip | `current -> releases/v0.31.0 (atomic flip)` |
| 17:29:28 | hand-back declared | `hand-back: UNIT_MANAGED — the flipped release returns to unit ensemble-main.service (service-mode Restart=/journald ownership intact; no nohup lineage)` |
| **17:29:28** | **rc=3 conflated (the defect)** | `WARN: is-active probe exceeded budget (5s) for ensemble-main.service — treating as NOT-active; continuing into the start attempt (the MainPID/port verify owns the verdict)` — `systemctl is-active` returned **rc=3 (INACTIVE — the normal state immediately after start is dispatched)** *within* budget; `lib.sh:6356 [ "$isact_rc" -ne 0 ]` conflates rc 3/4 with rc 124 timeout, and `lib.sh:6358-6360` journals it mislabeled: `site=handback_is_active kind=budget reason=child: timeout(SYSTEMCTL_PROBE_BUDGET_S=5) unit=… rc=$isact_rc — treating as not-active, continuing` |
| 17:29:28→17:30:06 | start + verify poll | unit `start` succeeded (no start-failure halt lines — those live at `lib.sh:6383-6391`); `_supervision_unit_serving` polls; budget line (payload): `hand-back verify: budget effective=300s source=ceremony-clamped grace=60s total=360s ceremony-remaining=809s (knobs: HANDBACK_VERIFY_BUDGET_S=300 FLOOR=180 TAIL=120 GRACE=60)` |
| **17:30:06** | **attribution fail-fast (false-halt)** | `WARN: attribution [hand-back verify]: REFUSED —port-holder(s): 1191207 1191605 NOT in the supervision-owned pid set (owned:1190778 1190919 1190932) — the :9797 answerer is not this install's daemon (ensemble-vm §5.1: :9797 held by kubectl/monitor while the daemon serves :9798)` |
| 17:30:06 | probe-level fail-fast | `WARN: hand-back verify: serving-process attribution REFUSED — port-holder(s): 1191207 1191605 NOT in the supervision-owned pid set (owned:1190778 1190919 1190932) — … (probe-level fail-fast, Fix #2); NOT success` — code: `_handback_serving_probe` green arm → `_gate_serving_attribution` → return 1 (`lib.sh:6097-6106`), no further polling |
| 17:30:06 | verify verdict | `WARN: unit hand-back FAILED verification for ensemble-main.service — NO nohup fallback (Amendment #1) — halting for the caller's B4 policy` (`lib.sh:6394-6397`: single shot, **no unit-arm retry**) |
| 17:30:06 | promote halt | `WARN: unit hand-back FAILED — halting promote (txn left open for sweep recovery; NO nohup fallback, never a false success)` — `promote.sh:684-699`; halt detail template `promote.sh:698` renders `declared=unit verified=UNIT_MANAGED unit=… ; no nohup fallback — Amendment #1; hand-back error: ${HB_ERR:-none captured}` — on this run **`HB_ERR` was empty** (`none captured`) because `HANDBACK_LAST_ERROR`/the `$$`-keyed error file are populated only by the launchctl-bootstrap branch (`lib.sh:5634-5635`); the systemd verify-failure path writes neither |

### rc=3 event, journal label, pid census — precision notes
- The rc=3 event is logged at **17:29:28Z** (the brief's "~17:29:30" window is ±2s). The unit start itself did not fail; the halt came from the serving-verify attribution refusal ~38s later.
- **Mislabeled journal line:** the `subprocess_wait_timeout` event at `lib.sh:6358-6360` carries `kind=budget reason=child: timeout(SYSTEMCTL_PROBE_BUDGET_S=5) … rc=3` — rc 3 recorded as a budget/timeout event. Same pattern family at `lib.sh:6338` (reset-failed site) and `lib.sh:6699-6700` (restart `scope_arm_is_active` — see Q2).
- **:9797 LISTEN census:** pre-stop (payload line 7, report-only): `700753 701344 1163298 1176879 1176885 1184677 1184684 1189235 1189249 1189251`. Post-flip verify (17:30:06): holders `1191207 1191605` vs supervision-owned set `1190778 1190919 1190932` (owned-pid derivation: `_supervision_owned_pids`, `lib.sh:6921-6950`; refusal: `_gate_serving_attribution`, `lib.sh:8004-8047`, detail template at :8044-8045). **The logs do NOT record which pid is the daemon vs MCP children vs foreign holder** — per the log's own annotation (ensemble-vm §5.1), `:9797` was held by kubectl/monitor while the daemon serves `:9798`, i.e. the refusal fired on a healthy boot with a foreign/monitor listener on the probed port. NOT-DETERMINABLE from the fixture alone: pid→process mapping.

### Open txn / torn state + the 17:39:04Z serving-green closer
- Halt policy: txn LEFT OPEN (`promote.sh:698-699` — `history is advisory; the open txn IS the B4 contract`); flip already done, so `current -> releases/v0.31.0` stood while `in_flight` stayed open.
- The closer is **daemon-side**: `daemon/services/upgrade_journal_sweep.py` `sweep_serving_green_closer` (:2325), predicate P1–P9 (:2335-2349: install_dir set; journal untorn; `in_flight` open; kind != restart; `flipped == true`; **age > `SWEEP_STALE_S` = 600s**; owner_pid dead; boot-grace elapsed ∧ `grace_active == False`; **P9: an `intent_flip` history event naming this txn's target — the authorization conjunct**). Kill-switch env `ENSEMBLE_UPGRADE_SERVING_GREEN_CLOSER` (:178, default on, `!= "0"` :2096). Halt 17:30:06 + 600s ⇒ closer window opens ≈17:38:14; **closer committed 17:39:04Z** (project history note; consistent with P6).
- Commit mutations (:2675-2689): `journal_update_field(current → target v0.31.0)`, `journal_update_field(previous → displaced v0.30.12)`, optional `quarantine_cleared_serving_green` (:2681) if the target was quarantined, then history `serving_green_commit` — detail template visible in `releases/state.json` precedent rows (v0.29.0 2026-11-06T11:40:52Z, v0.29.1 2026-11-07T08:59:21Z): `… — serving green (in-process health read: version match + readiness composite ready); committed and continued — NOT rolled back, NOT quarantined, counter NOT incremented, no cooldown`. Post-close state (state.json head, read now): `{"current":"v0.31.0","previous":"v0.30.12","in_flight":null,…}` — **end-state verified; zero manual mutations** (project history note; the history/rollback-counter fields were untouched). A6 pre-check (:2652-2674) refuses a half-applied commit if `current`/`previous` fields are missing.
- Identical precedents: v0.29.0 (2026-11-06) and v0.29.1 (2026-11-07) halts in state.json history read verbatim: `promote aborted post-flip: unit hand-back failed for v0.29.0 — halt-for-human, txn left open for sweep recovery (declared=unit verified=UNIT_MANAGED unit=ensemble-main.service; no nohup fallback — Amendment #1)` each followed ~5-10 min later by `serving_green_commit`. **This run class has now fired three times.**

---

## Q2 — RESTART-ARM TWINS (r-20261117-153525-34a9 / r-20261117-155953-c7cb)

Records found (all read verbatim; READ-ONLY):

**34a9 run log** (`data/upgrade/live/r-20261117-153525-34a9.log`):
```
2026-11-17T15:35:38Z upgrade-restart[live]: resolved env: target=live dir=/home/nea/agents-ensemble port=9797 db=ensemble_prod
2026-11-17T15:35:38Z upgrade-restart[live]: preflight: env triple · journal · pending_op run_id=r-20261117-153525-34a9 · lock adoption
2026-11-17T15:35:39Z upgrade-restart[live]: WARN: no pending_op in the journal — nothing armed (run stage/arm via system_restart or check the run id)
```
**c7cb run log** (`…/r-20261117-155953-c7cb.log`): identical shape at 16:00:03Z.
**Client logs** (both): `Running as unit: ensemble-restart-r-…service` → `Main processes terminated with: code=exited/status=78` (preflight refuse class), runtime 625ms / 661ms, **payload logs 0 lines** (died pre-execution).

- Restart executor phases (`scripts/upgrade/restart.sh:13-45`): arm happens in the DAEMON tool (`system_restart` writes the journal `pending_op` + takes the `rollback.lock.d` at arm time); the executor only ADOPTS. Both twins died at step 0 preflight: **the armed pending_op never persisted** ⇒ arm abandoned pre-execution, exit 78, clean client record, no cause row.
- The restart-path conflation twin of Q1's defect is the `scope_arm_is_active` family (`lib.sh:6695-6702`, worktree-verified): the scope-arm branch runs a bounded `is-active` probe and `[ "$_sc_rc" -ne 0 ]` treats **rc=3 (inactive — the normal pre-start state) and rc=4 identically to a timeout**, journals `site=scope_arm_is_active kind=budget reason=child: timeout(SYSTEMCTL_PROBE_BUDGET_S=5) … rc=3 — treating as not-active, continuing` (mislabeled), blanks the verdict, and proceeds — the arm attempt can then abandon with no distinguishing cause recorded. POK anomaly `a337cf57` names this family for the restart path; `5a292da3` is the c7cb anomaly.
- **restart_audits store:** file-backed in the upgrade journal = **`releases/state.json`** (`adopt-unit.sh:481` resolves `jp="$INSTALL_DIR/releases/state.json"`; schema `daemon/tools/upgrade_journal.py:933-937` — `restart_audits` dict keyed by `run_id`, **cap 50 drop-oldest**, additive surface §6.2; write path `uj.record_restart_audit`, settle path `daemon/services/upgrade_journal_sweep.py:1037-1110` `_settle_restart_audit` patches audits at boot). **LIMITATION:** `state.json` is a single-line JSON document and my line-oriented reader truncates it (~2KB), so the twins' individual `restart_audits` rows could NOT be extracted verbatim this pass. The conflation pattern is proven by the executor logs + client exit-78 + the `scope_arm_is_active` code; the audit-row rc/classification fields should be re-extracted by the coding worker with `scripts/upgrade/journal_read.py` (read-only helper present in the worktree) or python one-liner — NOT-FOUND verbatim here.

---

## Q3 — RELEASE MECHANICS (worktree, read-only)

### stage.sh flow (`scripts/upgrade/stage.sh`, 1123 lines)
- **VERSION discipline** (:80-84): explicit `VERSION` required; **HEAD must be exactly tagged** (`git describe --tags --exact-match`) else **exit 78**; no network, no auto-pull; never `make build` from stage.
- **NO FLIP** (:78): staging never touches `current` — that is promote.sh. NO `.env` inside the release dir (:65-76); env marker `ENSEMBLE_SELF_ENV` staged into `INSTALL_DIR/.env`. NO plists in payload (:57-63).
- **Freshness guard** (:92-104): refuses non-tip staging tree AND prebuilt artifacts lacking/malformed/mismatched provenance sidecars (built-on-dirty-tree / different commit / sha mismatch); argv-only override `--allow-stale-stage` (journaled + audited).
- **tip-identity / stage-worktree requirement:** staging tree must be at the integration tip and exactly at the annotated tag (POK `release-01`: bump commit → **annotated tag** → stage worktree DETACHED at tag → FE rebuild at that HEAD → sidecar re-stamp → stage.sh; `git describe --exact-match HEAD == VERSION`).
- **Manifest build** (:700-755): per-file sha256 maps for every tree incl. `scripts/` (`"upgrade/promote.sh"` etc., :701-716) and a `daemon/` slice map (:717-731); `staged_at` refreshed every stage (:734-754; epoch drives eviction identity, lib.sh:2839-2853 retention); `rollback_safe` field written at :755. **4-way sha256 identity** = release dir == dist == manifest == sidecar (v0.29.0 ceremony convention; the freshness guard + integrity phase enforce it).
- **rollback_safe derivation** — see Q5.

### promote.sh flow (`scripts/upgrade/promote.sh`, 1121 lines)
- Preflight (fixture lines 3-15): lock · integrity · journal-txn adopt · entry checks · plugin-staleness (allow-stale, journaled) · config-compat · txn open (600s outer window).
- Stop → launcher swap → **atomic flip** (:653-664, `atomic_flip "$VERSION"`; failure ⇒ txn left open for sweep) → flipped marker (:670) → **hand-back** (:673-699): `SUPERVISION_*` consumed from stop-site globals ("never re-derived here, the daemon is down" :674-675); `UNIT_MANAGED` → unit hand-back, no nohup (Amendment #1); halt ⇒ `journal_history_append halt` + B4 leave-txn-open (:698-699). If hand-back halts post-flip, "the next launcher start sweep-ROLLS-BACK to previous" (:682-683) unless the serving-green closer commits first.
- **Gates/soak** (constants in `lib.sh:137-156, 284-321`): post-flip soak `SOAK_S_DEFAULT=300`; default mode is **adaptive** (`ADAPTIVE_SOAK_FLOOR_S=60`, `ADAPTIVE_SOAK_CAP_S=120`, `ADAPTIVE_SOAK_CONSECUTIVE=3`, `ADAPTIVE_SOAK_CADENCE_S=15`); conservative via `ENSEMBLE_SOAK_CONSERVATIVE=1`; overridable `ENSEMBLE_PROMOTE_SOAK_S` (sandbox drills); total ceremony budget 900s (1400s worst-case halt). Gate stack: livez 180 / readyz 120 / version / soak; gate-failure halt path `promote.sh:890-931` (T5 manifest gate incl. previous-`rollback_safe`).
- **Sweep interplay:** post-halt, the daemon boot/periodic sweep owns recovery — serving-green closer (Q1), stale PRE-FLIP txn clear with owner-liveness conjunct (:655-660).

### Next free version slot
- **Current version: `0.31.0`** (`pyproject.toml:3`, worktree). `releases/` on the live install holds `v0.30.9 / v0.30.11 / v0.30.12 / v0.31.0` + `state.json current=v0.31.0`. **Next free patch slot ⇒ `v0.31.1`** by the patch-bump convention — **MUST be re-derived at bump time** (other lanes active; tag listing + releases dir + pyproject at bump moment are the truth; the stale metadata note "next_free_version_slot: v0.29.1" predates several releases and is NOT current).

### scripts/bump_version.py (165 lines, worktree)
- Input: bump type argv (`patch|minor|major|prerelease`), current version read from **`pyproject.toml`** (:56-63).
- Files carrying the version string (the 4-file bump set): `pyproject.toml` (:86), `daemon/__init__.py` `__version__` (:91), `tests/test_api.py` version assert (:96), `uv.lock` ensemble-package entry regex (:101-109). Optional 5th: `CHANGELOG.md` when the operator has done the `[Unreleased] → [<ver>]` header rename (5-file bump-commit convention, :122-145).
- Output: runs `uv sync` (:117), then a TARGETED git commit `! New version <ver>` staging exactly the touched files (:119-159) — never `git add -A`.
- **bump_version.py does NOT touch the scripts stamp/floor** — those are a separate manual re-pin (see Q4). **Citation repaired (REVIEW WAVE 2, fold [T5]):** this bullet previously pointed at `executor_family.sh:176-181` as "the truthfulness-gate comment" — that range is the **`executor_family_manifest_lines` function body**, not a comment. The truthfulness-gate text lives in **two** places: `scripts/upgrade/executor_family.sh:305-314` (the `executor_family_verify_stamp_coverage` doc-comment: *"This is the truthfulness gate for PIPELINE_SCRIPTS_REV …"*) and `scripts/upgrade/stage.sh:803-816` (the `D3: the stamp must COVER the SHIPPED set (truthfulness)` block whose `REMEDY:` line mandating the stamp+floor re-pin ends at `:816`, immediately before the `executor_family_verify_stamp_coverage` call at `:817`). Verified in the worktree; neither is `:810-816`, and no `stage.sh:~810-816` citation existed in this file to begin with.

---

## Q4 — FLOOR/STAMP VALIDATION AT ARM TIME

- **Constant pair (D6-8 "the two travel together"):**
  - `PIPELINE_SCRIPTS_REV` — **`scripts/upgrade/lib.sh:95`** (worktree) = `1aa0065eb818ff00cb32730fab54d7f2060c42d8`. Full-40-hex REQUIRED (`lib.sh:740`).
  - `PIPELINE_SCRIPTS_FLOOR_COMMIT` — **`daemon/tools/upgrade_tools.py:4145`** (worktree) = `1aa0065eb818ff00cb32730fab54d7f2060c42d8` (docstring :4146-4161: mechanism ships here, VALUE written by the release bump, same cadence as the stamp; stage.sh fails the release when unset/unresolvable/non-ancestor).
- **When the arm-time guard runs:** at promote ARM time (daemon side, "phase 6" scripts-dir provenance guard, `upgrade_tools.py:4135-4144`): reads the pinned scripts dir's `lib.sh` stamp via a bounded first-64KB window (`PIPELINE_SCRIPTS_STAMP_WINDOW_BYTES = 64*1024`, :4165) with `_STAMP_RE` (:4177-4179), then compares the pinned dir's HEAD against the floor **by git ancestry** (`git merge-base --is-ancestor`; remedy text :4170-4175 names fetch+checkout to a floor-descendant). Refusal is FAIL-CLOSED on an explicitly pinned dir (`ENSEMBLE_UPGRADE_SCRIPTS_DIR`); WARNING-only on a fallback dir (:4156-4159). The fixture log line at 17:27:57 confirms both channels exist ("executor-start echo; the arm-time guard reads the lib.sh stamp file").
- **Release-time (stage-time) set-and-validate:** `stage.sh:756+` (T6.15/AC15) extracts the floor from `upgrade_tools.py` (:690 main; worktree block begins :756), extracts the stamp from `lib.sh` (:711), and runs two verdict helpers in **`lib.sh`**:
  - `_pipeline_scripts_stamp_verdict` (:738-753): 40-hex; resolves to a commit in this tree (`git cat-file -e`); **stamp == floor** when floor set.
  - `_pipeline_scripts_floor_verdict` (:773-788): set; 7–40 hex accepted; **`git merge-base --is-ancestor <floor> HEAD`** must pass — "the floor names a commit the release itself contains".
- **What the guard compares:** commit SHAs by **ancestry (merge-base)** — never tags, never merge-base equality, never dates. The 2026-11-06 v0.29.0 ceremony's "floor 2147c0298" mechanics (POK `promote-scripts-dir-provenance`): promote ran from the **scripts-pin worktree `/home/nea/ensemble-src-wt-scripts-pin`**; the arm-time guard required lib.sh stamp == floor `2147c0298` **and** floor as ancestor of the pinned dir's HEAD; the canonical main checkout was arm-INELIGIBLE (dirty index). Today the pin is at a floor-descendant (`rev=1aa0065eb…` echoed at 17:27:57Z).
- **Commit-graph options for this lane:** the floor must be **the merge commit that lands the ceremony-relevant scripts/upgrade semantics** (i.e. this lane's fix merge, `M`). A commit cannot embed its own sha ⇒ minimum shape is **two commits**: (1) the fix merge `M` (code only), (2) the bump commit `B` (bump_version.py fileset + re-pin `lib.sh:95` REV = M's 40-hex sha AND `upgrade_tools.py:4145` FLOOR = the same M sha), then annotated tag on the tip and stage from a detached worktree at the tag (POK release-01 order). Folding the re-pin INTO M is impossible (self-reference); folding it into a THIRD commit after B is allowed as long as the floor (M) remains an ancestor of the built tree — ancestry is the only predicate. Bump-and-repin in ONE bump commit is the house shape (matches how 1aa0065eb was stamped by its own release bump).

---

## Q5 — ROLLBACK_SAFE ADJUDICATION

- **Decided at STAGE time**, recorded in the release **`manifest.json`** field `"rollback_safe"` (`stage.sh:755`), consumed by: promote's T5 manifest gate (`promote.sh:890-931` — the PREVIOUS release must exist and be rollback_safe, else halt-for-human NO repoint, D-FA4.5 schema-drift guard), manual rollback (`rollback.sh:174-177` — target must be rollback_safe), and the adopt/sweep paths (`lib.sh:7811-7843`, :7646). Also mirrored in state.json journal halt details ("has rollback_safe=…").
- **Derivation** (`stage.sh:92-110, 380-403`): author's explicit call via env **`ENSEMBLE_ROLLBACK_SAFE={0,1}`**; when unset it is **DERIVED — false iff the staged migration set contains destructive DDL (`DROP TABLE|DROP COLUMN` ⇒ `contains_contract_phase=true`), else true**. If destructive DDL is detected WITHOUT an explicit choice, stage REFUSES (no silent derivation; v0.15.3 supersession). `known_schema_gen` = migration head at stage time (informational).
- **Criteria:** "old binary must not reference new behavior" operationalizes as **schema continuity**: a rollback must not flip into schema drift. This lane's changes are bash-level (`scripts/upgrade/**`) + tests ⇒ **zero migration delta ⇒ rollback_safe=true (fix-loss-only on rollback)**.
- **Prior bash-only precedent:** **v0.29.15** was staged with explicit `ENSEMBLE_ROLLBACK_SAFE=1` (`stage.sh live --skip-build` argv preserved in project metadata critical note, staged 2026-11-10T18:05Z) — an explicit-true on a release whose delta was scripts/machinery-side. Recommendation-consistent stance for v0.31.1: **explicit `ENSEMBLE_ROLLBACK_SAFE=1`** (or accept default-true; explicit is the house habit when the lane's semantics are scripts-side and the author wants the `ROLLBACK_SAFE_SRC=explicit=true` audit trail, `stage.sh:383`).

---

## Q6 — BOARD ITEM wi-d3579d7c

- **NOT-FOUND as file-accessible.** Searched (read-only): `wi-d3579d7c` content-grep across the main checkout `.agents/` tree — zero hits (search flagged 11 oversized files skipped, all logs/DBs, none plausible board stores); the live install `/home/nea/agents-ensemble` has **no `.agents/` directory at all** (hidden-inclusive listing verified); no file-backed board exists under `/home/nea/agents-ensemble/` — the PM/board store is the **daemon database** (`data/instances.db`), which is not readable as files from this lane. Item #1's framing is carried by the task brief itself ("Promote-kind flipped=false live closer — same dead-actor pattern on the promote path"); it folds into this lane per the brief. Sub-items 2-4 (JournalTorn gap, `lock_acquire` to_thread, boot-sweep default) = OUT of scope per brief.

---

## Q7 — TEST/FENCE PATTERNS TO CITE

- **FAKE_HOME + mktemp + escape-guard precedent (622e7048e fence):**
  - `tests/helpers/preflight_pack_common.sh:7` ("a `mktemp -d` FAKE_HOME plus an ESCAPE GUARD on both (the 2026-10-12 …)"), `:76` (`PF_FAKE_HOME="$(mktemp -d "${TMPDIR:-/tmp}/pf-home.XXXXXX")"`), `:78` (`pf_assert_not_live "$PF_FAKE_HOME"`), `:89` (`export HOME="$PF_FAKE_HOME"`), `:106` (scoped `rm -rf` cleanup guard).
  - `tests/helpers/ram_guard_fence.py:13,47,56,68-70` (FAKE_HOME sentinel + fence applicator returning `(fake_home, scrubbed_auth_names)`).
  - `tests/helpers/nestops_pg_fixtures.py:76` (`install_fake_home(monkeypatch, base)`).
  - Threat context: three live-install deletions of `/home/nea/agents-ensemble` by test code (2026-10-12) — "Test scripts must NEVER resolve live-shaped paths" (critical note).
- **Scratch/sandbox rehearsal fencing precedents:**
  - **`ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned EXPLICITLY** for frozen-binary boot smokes from daemon-child shells (`env -u` on it strips the ambient guard; the sweep killed a LIVE launcher+daemon pair 2026-10-31 — critical note).
  - **Per-variable env scrub, never `env -u POSTGRES_*`** (silent no-op; scrub each of POSTGRES_DB/USER/PASSWORD/HOST/PORT + `ENSEMBLE_SELF_ENV=dev` — all FIVE + self-env, POK repo conventions; `tests/test_prompt_cache_invalidation.py:119-136` session fence aborts on live-shaped POSTGRES_DB).
  - **`ENSEMBLE_DB_AMBIENT_ALLOW=1`** pre-bind for fenced scratch boots on ambient `POSTGRES_*` (POK `rogue-fence-ambient-allow-var`; guard `daemon/ensemble_config.py:186` at v0.31.0; tier-2 grant only).
  - **`ENSEMBLE_AUTH_*` full-family scrub or none** (7 vars; partial scrub ⇒ phantom reds / collection errors — POK `auth-lane-scrub-ensemble-auth-env`).
  - Worktree `.env` fence per the giter template (`agents/giter/workflow.md:82-94`): explicit POSTGRES_* + `DAEMON_PORT=8079` inside every worktree's `.env` + creation-time verify (KV `wt.claim.hbfix` records fence=verified).
- **Prior promote-machinery fix lanes under `.agents/shared/planning/` (committed in-branch):**
  1. **`promote-handback-halt/`** — the DIRECT precedent: `architecture-recommendation.md` (2026-11-02, base 941cb6321, worktree `ensemble-src-wt-promote-handback-halt`; Phase-1 hand-back verifier HANDBACK_VERIFY_BUDGET_S=300 clamp(remaining−120,180,300); Phase-2 daemon serving-green closer, P1–P9, `UPGRADE_CLOSER_BOOT_GRACE_S` default 300 ≥ HANDBACK_VERIFY_BUDGET_S, grace- conjunct AM-P2-24), `decisions.md` (decision register, D6 corrections-table convention), `phase1-plan.md`/`phase2-plan.md`/`phase3-plan.md`. Landed incl. the v0.28.8 bump (per `metrics-agent-tools/stage-prep-checklist.md:113`).
  2. **`promote-full-auto/`** — `d2-supervisor-flip-design.md` (supervision classification, token conventions `ENSEMBLE_SUPERVISION_RESULT=UNIT_MANAGED…` vs prefixed journal `reason=…`, C10/C10b expectation changes; must-stay-green suite list incl. `test_supervision_stop_handback.sh`, `test_supervision_twins.sh`, `test_adopt_unit.sh`, `test_atomic_flip.sh`, `test_release_journal.sh`).
  3. **`auto-restart-upgrade/`** (agent-restart §6.2 — restart_audits/pending_op arm-run contract) and **`post-restart-arm-notify/`** (restart arm path); macOS-shape hand-back context: `macos-deployment-topology/` + incident `docs/incidents/2026-11-14-macos-launchd-handback-halt.md` (§5.1 port-holder census, §7b plist-label; cited in lib.sh:6952-6959, :8045).
  - House test conventions to carry: mutation-verified regression pins (POK `mutation-01`: green-under-own-revert needs a COUNTER-mutation red; re-aim at POSITIVE shape), anchors-are-leads (grep at use time), no system-internal tokens in `agents/**/*.md` prose.

---

## PLAN INPUTS (mechanics constants for the planning workers)

| Constant / fact | Value | Site (worktree-verified unless noted) |
|---|---|---|
| `PIPELINE_SCRIPTS_REV` (current) | `1aa0065eb818ff00cb32730fab54d7f2060c42d8` | `scripts/upgrade/lib.sh:95` |
| `PIPELINE_SCRIPTS_FLOOR_COMMIT` (current) | `1aa0065eb818ff00cb32730fab54d7f2060c42d8` | `daemon/tools/upgrade_tools.py:4145` |
| Stamp extract window / regex | 64 KiB; `^PIPELINE_SCRIPTS_REV="?([0-9A-Za-z._/-]+)"?[ \t]*$` | `upgrade_tools.py:4165, :4177-4179` |
| Floor validity predicate | 7–40 hex + `git merge-base --is-ancestor <floor> HEAD` | `lib.sh:773-788`; stage-time call `stage.sh` T6.15 block (`stage.sh:756+`; extract sed `stage.sh:690` main-tree) |
| Stamp validity predicate | 40-hex + `cat-file -e` + **stamp == floor** | `lib.sh:738-753` |
| Scripts-pin worktree (ceremony) | `/home/nea/ensemble-src-wt-scripts-pin` (arm-time guard reads ITS lib.sh stamp; fixture echoed `1aa0065eb…`) | fixture log 17:27:57Z + POK `promote-scripts-dir-provenance` |
| Next free version slot | **v0.31.1** (pyproject `0.31.0`; re-derive at bump) | `pyproject.toml:3`; `releases/` listing; bump via `scripts/bump_version.py patch` |
| bump fileset | pyproject.toml · daemon/__init__.py · tests/test_api.py · uv.lock (+CHANGELOG.md opt) | `scripts/bump_version.py:84-159` |
| Release order (POK release-01) | bump commit → ANNOTATED tag → stage worktree detached at tag → FE rebuild → sidecar re-stamp → stage.sh; `git describe --exact-match HEAD == VERSION` else exit 78 | `stage.sh:80-84` |
| Stage argv shape (live) | `ENSEMBLE_UPGRADE_LIVE=1 [ENSEMBLE_ROLLBACK_SAFE=1] VERSION=v0.31.1 bash scripts/upgrade/stage.sh live [--skip-build]` | `stage.sh:86-90`; v0.29.15 precedent argv in project metadata |
| Promote argv shape (ceremony) | `ENSEMBLE_UPGRADE_LIVE=1 VERSION=v0.31.1 bash scripts/upgrade/promote.sh live --f2-verified-closed` (from the scripts-pin worktree; CWD selects the executing script) | v0.29.0 ceremony notes + promote.sh usage |
| Soak knobs | `SOAK_S_DEFAULT=300`; adaptive 60/120/3/15; `ENSEMBLE_SOAK_CONSERVATIVE=1`; ceremony budget 900s | `lib.sh:284-304, :137-156` |
| Hand-back knobs | `HANDBACK_VERIFY_BUDGET_S=300` (ceremony-clamped, FLOOR=180 TAIL=120 GRACE=60), `SYSTEMCTL_PROBE_BUDGET_S=5`, `HANDBACK_START_BUDGET_S=60`, `SCOPE_START_BUDGET_S=10` | fixture payload line + `lib.sh:6329, :6368, :6718` |
| rollback_safe field locations | manifest.json `"rollback_safe"` (stage.sh:755); consumed promote.sh:924 / rollback.sh:174 / lib.sh:7840; journal halt details | Q5 |
| rollback_safe stance for this lane | explicit `ENSEMBLE_ROLLBACK_SAFE=1` (bash+tests only, empty migration delta; v0.29.15 precedent) | Q5 |
| Serving-green closer | `daemon/services/upgrade_journal_sweep.py:2325` (`sweep_serving_green_closer`), P1-P9 :2335-2349, `SWEEP_STALE_S=600` (P6), commit :2675-2689, env kill-switch `ENSEMBLE_UPGRADE_SERVING_GREEN_CLOSER` (:178) | Q1 |
| Upgrade journal (file-backed) | `releases/state.json` (current/previous/in_flight/quarantined/history/restart_audits cap-50/pending_op) | `adopt-unit.sh:481`; `upgrade_journal.py:774-796, :933-937` |
| False-halt defect sites (worktree anchors) | rc-conflation `lib.sh:6354-6362` (promote hand-back is-active; journal label :6358-6360) + `lib.sh:6695-6702` (`scope_arm_is_active`); no unit-arm retry `lib.sh:6394-6397`; fail-fast `_handback_serving_probe` `lib.sh:6093-6133` (green→attribution :6097-6106); attribution gate `_gate_serving_attribution` `lib.sh:8004-8047`; owned-pids `lib.sh:6921-6950`; halt+`none captured` `promote.sh:684-699` (template :698; HB_ERR populated only launchd-side `lib.sh:5634-5635`); launchd retry loop (for contrast) `lib.sh:6541-6557`; SUPERVISION_* frozen-globals contract `promote.sh:673-683` | Q1/Q2 |
| Worktree fence (KV `wt.claim.hbfix`) | branch=fix/promote-handback-false-halt · base=0181dadbf (== origin/latest) · `POSTGRES_DB=ensemble_dev` · `DAEMON_PORT=8079` · lazy venv | shared_meta_kv |

**NOT-FOUND / limitations (explicit):**
- Board item `wi-d3579d7c` text: not file-accessible (daemon DB store) — Q6.
- The twins' individual `restart_audits[run_id]` rows: inside single-line `releases/state.json`; line-oriented read truncates — re-extract with `scripts/upgrade/journal_read.py` (read-only) at implementation time — Q2.
- v0.31.0's own `serving_green_commit` row (17:39:04Z): same single-line truncation; fields reconstructed from the closer code + the verbatim v0.29.0/v0.29.1 precedent rows + post-close state.json head (`current=v0.31.0, previous=v0.30.12, in_flight=null`) — Q1.
- Pid→process identity for holders `1191207 1191605` / owned `1190778 1190919 1190932`: not recorded in any fixture file — Q1.
- `sweep.py:405` (POK note): no `sweep.py` exists in the repo tree (glob over `scripts/` empty; the sweep lives in `daemon/services/upgrade_journal_sweep.py`) — the POK anchor is stale/renamed.
