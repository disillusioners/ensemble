# Group-chat v0.31.0 — RELEASE-PREP VERIFICATION (pre-cut)

**Date**: 2026-11-17
**Worktree**: `/home/nea/ensemble-src-wt-group-chat-release` · branch `feature/group-chat-release`
**Tip under test**: `f5af8739b` (release-lane tip at dispatch) → `b47e9165f` (after the registry fix; the `daemon/` diff between the two is EMPTY, so every proof below holds at both)
**Scope**: three legs, all PRE-CUT. **No version bump, no release dir, no tag, no push.**
**Plan under verification**: `.agents/shared/planning/group-chat-release/release-plan.md`

---

## 0. Verdict summary

| Leg | Plan leg | Verdict | Blocker? |
|---|---|---|---|
| **LEG 1** — PG-side schema + behavior proof | **S2 (PG half)** + plan top risk #2 | **PASS-WITH-NOTES** — PG parity **HOLDS** | **No** |
| **LEG 2** — prompt-artifact seal | **S1d** | **PASS** — no baseline bump, no seal re-pin | **No** |
| **LEG 3a** — room-family re-measure | **S4 (re-measure)** | **PASS** — 24 files / 421 nodes | **No** |
| **LEG 3b** — boot-smoke recipe | **S5 (recipe)** | **PASS-WITH-NOTES** | **Plan defect — see §5** |
| **Registry fix** | PACKS.md accuracy | **DONE** — commit `b47e9165f` | No |

**Two blockers to REPORT (neither is a code defect; both live in the release PLAN):**

- **BLK-1 (critical, ceremony-facing)** — the plan's §5.4 "safe invocation" boots the **not-yet-promoted** binary against the **live prod DB on the live port** (`ENSEMBLE_SELF_ENV=live`, `POSTGRES_DB=ensemble_prod`, `PORT=9797`). `create_all` would create the three room tables in prod **outside the promote ceremony**, the boot collides with the running live daemon on 9797, and `ENSEMBLE_SELF_ENV=live` opens the prod-DSN carve-out (`daemon/ensemble_config.py:269`). If the promote then aborts, prod carries the new schema under the old binary.
- **BLK-2 (critical, ceremony-facing)** — the plan's §5.4 scrub is **incomplete**. The canonical five-var scrub leaves `POSTGRES_URL` (a full DSN returned AS-IS, `daemon/repositories/persistence.py:233`, which **short-circuits** the `POSTGRES_*` builder), `DATABASE_URL`, `DATABASE_URL_POSTGRES` and the 9 libpq `PG*` names live. Verified by dry-run probe: after the five-var scrub, `POSTGRES_DB`/`PGHOST`/`PGDATABASE` all remain visible.

---

## 1. LEG 1 — PG-side schema + behavior proof (plan S2 PG half; top risk #2)

**Verdict: PASS-WITH-NOTES. PG parity HOLDS. No release blocker.**

The release plan recorded that "PG currently rests on `create_all` + `_ensure_postgres_columns` with NO test" and that "the PG half does not exist as a test today". This leg closes that gap.

### DB + fence
- DB: `ensemble_dev_scratch` @ 127.0.0.1:5432, role `ensemble_dev_app`, PostgreSQL 16.15. Driver `postgresql+psycopg` (matches `daemon/repositories/factory.py:387-406`).
- Per-variable scrub applied to every command; effective `POSTGRES_DB` asserted `ensemble_dev_scratch` (never `ensemble_prod`, never `ensemble_dev`).
- Isolation: **private schema** `gc_pg_probe` via `connect_args options="-c search_path=gc_pg_probe"` — `public` deliberately off the path.

### Probe evidence
| Probe | What it proved | Result |
|---|---|---|
| 1 — fresh-PG `create_all` | The daemon's own registration imports (ast-extracted from `InstanceManager.__init__`, `daemon/manager.py:860-1067`) then `SQLModel.metadata.create_all` (`manager.py:1068`) → **100 tables**. Live `pg_constraint`: `uq_chatroom_members_room_member_name UNIQUE (room_id, member_name)` ✅ and `uq_room_messages_room_seq UNIQUE (room_id, seq)` ✅ | PASS |
| 2 — `_ensure_postgres_columns` (the "existing PG" path) | Fixture = the genuine pre-P3/P5 shape (cross-checked against the real pre-existing `public.room_messages`). 10 cols → **14 cols** after; adds `source_message_id`/`trigger`/`member`/`resolution_warnings` + `uq_room_messages_proposal_dedup` index. **Run 2 = idempotent** (no duplicate column, identical index/constraint counts) | PASS |
| 3 — behavior (real `RoomChatService` → `RoomRepository`) | (a) create + owner seed (`state='active'`, `message_count=0`, `role='owner'`, cursors `0`); P2 join with collider → `capturer-1`; (b) seq `[1,2,3]` strictly monotonic, second room starts its own space at `1`; (c) cursor advance `2`→True, `1`→**False** (monotonic MAX guard), read `after_seq=2`→exactly 1 msg, **cross-room leakage = 0 rows**, non-member read → `RoomNotFoundError`; (d) purge → **all three tables emptied for that room**, control room untouched, **exactly ONE** `room_purged` event, content-free | PASS |
| 3b — P3 dedup + P5 JSONB | PG NULLs distinct (matches SQLite); duplicate proposal → `UniqueViolation` on `uq_room_messages_proposal_dedup`; different `trigger` accepted; `resolution_warnings` round-trips as `jsonb` | PASS |
| 4 — `public` never written | Marker row landed in the private schema; `public.event` `n_tup_ins` stayed 0→0, sequence unchanged → `public`'s rows are pre-existing, not probe-created | PASS |
| 5 — PG-vs-SQLite DDL parity | Column set + nullability + types identical on all 3 tables (parsed with the runner's own UP/DOWN split) | PASS |

### Notes (non-blocking, no production code changed)
- **N1** — PG has **no server-side DEFAULTs**; the SQLite DDL carries `DEFAULT 'active'/0/'message'`. A raw-SQL INSERT omitting those columns is refused on PG but accepted on SQLite. **No product impact**: every path builds rows through the ORM with Python-side defaults (Probe 3 confirms correct values on PG). Would bite only a future raw-SQL insert path.
- **N2** — `uq_room_messages_proposal_dedup` is a `pg_constraint` on the fresh-`create_all` path but a bare `CREATE UNIQUE INDEX` on the `_ensure_postgres_columns` path. Enforcement identical (proven firing).
- **N3** — `room_messages` physical column ORDER differs between drivers (set/nullability/types identical). Not a contract.
- **N4** — the tip moved `f5af8739b` → `b47e9165f` mid-run (the registry fix; PACKS.md only). `git diff f5af8739b..HEAD -- daemon/` is EMPTY → the proof holds at both tips.
- **N5** — `ensemble_dev_scratch` is **not** a blank DB: its `public` room tables are a genuine pre-P3/P5 shape. Left untouched.

### Independent re-verification of the cleanup claim (tester, post-run)
```
schemas now = ['public']            baseline = ['public']    MATCH
public.chatrooms        : 10 cols, 0 rows
public.chatroom_members :  9 cols, 0 rows
public.room_messages    : 10 cols, 0 rows
public.room_messages cols = id, room_id, seq, author_instance_id, author_name, text,
                           tagged_members, addressed_members, kind, created_at   (PRE-P3/P5 shape, UNCHANGED)
probe residue (gc_pg_probe%) = 0
identity = ensemble_dev_scratch / ensemble_dev_app
```
The scratch DB is left **as found**; the P3/P5 columns were never added to `public`.

### Adjudication of the worker's open questions
1. **N1 (server-side DEFAULT gap)** → **accepted asymmetry, recorded; NOT a blocker and NOT a code change in this lane.** Adding `sa_column_kwargs={"server_default": ...}` to `daemon/repositories/room/models.py` would change the emitted DDL, invalidating the PG proof captured here and requiring a re-run before the cut. Backlog for a future release.
2. **N2 (catalog representation)** → no action; enforcement is identical. Relevant only to a consumer that introspects `pg_constraint` for that index.
3. **Plan-doc update** → the plan is the planner's/staged-release lane's artifact; this report records the closure and the plan owner updates the doc.

---

## 2. LEG 2 — prompt-artifact seal (plan S1d)

**Verdict: PASS. No baseline bump required. No seal re-pin required.**

### Evidence
- Seal byte-identity: committed blob digest == on-disk digest == `50561606a464df3d9484b93f78b1eb6880cc594badf27d69502d8d1f74c54979`; `git diff --exit-code HEAD -- agents/.prompt-artifacts.sha256` → rc 0. 217 lines, format `<64-hex>␣␣<path>`.
- Repo's own checker, read-only: `.venv/bin/python scripts/protection/generate_prompt_artifact_baseline.py --check --tree <worktree>` → **rc 0**, `PROMPT-BASELINE: OK — 217 versioned artifact(s) match .prompt-artifacts.sha256` (0 missing / 0 drifted / 0 added / 0 malformed).
- `agents/*.md` census, base `cf43ac00d` (first parent of the earliest group-chat merge `30e7cf69c`) → HEAD: **empty**. Per-merge `agents/*.md` diffs empty for all six phase merges.
- `agents/` census base→HEAD: **only the eight `meta.json` files** (the opt-in `room` grant). Sample hunk (ari): `"instance_ops"` → `"instance_ops", "room"`.
- `meta.json` is provably outside the seal: `VERSIONED_PROMPT_ARTIFACTS` (`daemon/tools/prompt_artifacts.py`) is `.md`-only and `_depth2_match()` matches only filenames in that set → `meta.json` can never be sealed or emitted.

### Plan-anchored variant (v0.30.12 → release tip)
The plan's §4.3 anchors the three checks at the **previous release tag**, a stricter question than "on-disk == HEAD-committed". Follow-up dispatched; result appended below.

### Chain-shape note (non-blocking)
P2's merge first-parent is `907f2f5917`, not P1's `e3787d4b8` — P2 was branched from an earlier tip. The census is unaffected (the aggregate base→HEAD diff is contiguous; every per-merge `agents/*.md` diff is empty), but the release brief should not assume a linear phase chain.

---

## 3. LEG 3a — room-family re-measure (plan S4)

**Verdict: PASS. 24 files / 421 nodes at the release tip. 421 passed, 0 red.**

The plan's own S4 row asked for exactly this: "`gc_p6_room_family` (24 files — **re-measure**, the merged-tree run says 421, the re-gate row says 420)".

| Measurement | Result |
|---|---|
| File count (canonical find-expression) | **24** |
| Node count (`--collect-only -q`) | **421** — "421 tests collected in 4.63s" |
| Run result | **421 passed, 0 failed, 0 errors, 0 skipped**, 609 warnings — exit 0 |
| Wall time | **70.93 s** under the 300 s cap (xdist `-n 8`; the historical serial run took ~6:02) |
| Cross-check | junitxml: 421 tests / 0 failures / 0 errors / 0 skipped — agrees with collect-only and the run |
| Fence | effective `POSTGRES_DB` proven `ensemble_dev`, never `ensemble_prod`; per-variable scrub |

**Registry defect found and fixed.** The registered `gc_p6_room_family` history carried three disagreeing composition labels — `21 files` @ `e6d984272` and @ `5d13a1c6c`; `24 files / 421` @ `419469b3f`; `24 files / 420` @ `7e0cd2db0` — and **no row registered the composition at the release tip**.

**Fix (append-only)**: new PACKS.md section `<!-- gc-v0310-release-verification-20261117-begin -->` … `-end -->` appended at the end of the file, registering the release-tip truth. **The historical rows were NOT edited** — their "21 files" labels were accurate at their own tips; only the composition/node LABEL is superseded, and the new row says so explicitly.

- Commit: **`b47e9165f`** — `test: register gc_p6_room_family composition at the v0.31.0 release tip (24 files / 421 nodes)`
- Files: `.agents/tester/PACKS.md` only · **+9 / −0** (append-only, zero deletions) · tree clean after commit
- Placement guard: the append landed in the **release worktree**; the MAIN checkout (`/home/nea/ensemble-src`) was verified clean of the marker. (Absolute paths used — `edit_file`/`write_file` can resolve relative paths against the MAIN checkout.)

---

## 4. LEG 3b — boot-smoke recipe (plan S5), no live boot

**Verdict: PASS-WITH-NOTES.** The recipe is verified; the PLAN's version of it is defective (BLK-1, BLK-2 above).

### The pinned sweep variable — confirmed from source
`daemon/services/boot_subprocess_sweep.py`: `:120` `ENV_KILL_SWITCH = "ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES"` · `:127` `DEFAULT_ENABLED = True` · `:132` `_DISABLE_VALUES = {"0","false","no","off"}` · `:140-180` `is_prod_lane()` == `sys.frozen` · `:183-225` `kill_switch_from_env` · `:381-390` OFF is a complete no-op.

Measured truth table (prod lane):

| environ value | sweep | |
|---|---|---|
| **ABSENT** | **ON** | destructive |
| `""` / `"   "` | **ON** | destructive |
| `"1"`/`"true"`/`"yes"`/`"on"` | **ON** | destructive |
| `"0"` / `"false"` / `"no"` / `"off"` | OFF | safe |
| `" 0 "` (padded) | OFF | safe (stripped) |

- `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` set **explicitly** is the correct pin for a daemon-child shell — **YES**.
- `env -u ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES` is **NOT** safe — it strips the guard and prod-lane defaults ON. This matches the 2026-10-31 incident (live launcher + `ensemble-prod` pair SIGTERM+SIGKILL'd, systemd `NRestarts=1`).
- **The pin is load-bearing**: the LIVE install's `.env` carries `=0`, but the **worktree** `.env` does **not** (0 occurrences) — a worktree smoke relying on ambient inheritance gets no guard at all.
- **Two further boot-time terminators** the plan omits: `ENSEMBLE_BASH_ORPHAN_REAPER` (`daemon/services/bash_spawn_reaper.py:109`; absent ⇒ ON in **all** lanes) and `ENSEMBLE_RAM_GUARD` (`daemon/config.py:1173-1204`; absent ⇒ ON).

### Scrub completeness
The canonical five are **not** the full set. Real carriers: `POSTGRES_DB`/`USER`/`PASSWORD`/`HOST`/`PORT` **+ `POSTGRES_URL`** (`daemon/repositories/persistence.py:233` — a full DSN returned AS-IS, short-circuiting the `POSTGRES_*` builder) **+ `DATABASE_URL`** (`spawn_env_policy.py:68`) **+ `DATABASE_URL_POSTGRES`** (`migration_worker.py:258`; `tools/system.py:81,102`) **+ the libpq family** `PGHOST PGPORT PGDATABASE PGUSER PGPASSWORD PGPASSFILE PGSSLMODE PGSERVICE PGSERVICEFILE`. House references: `scripts/run_tests_scrubbed.sh:47-63` (17 names) and `spawn_env_policy.py:51-77` (12 + the `PG` prefix family, `PG_TEST_` exempt).

### Dry-run probes (redacted; no values printed)
- Correct scrub (34 names) + explicit pin → **all 34 scrubbed vars `<unset>`**, `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES = <set,len=1>`.
- Counter-example `env -u POSTGRES_*` → `POSTGRES_DB`/`USER`/`PASSWORD`/`HOST`/`PORT` **all still `<set>`**, plus `PGHOST`/`PGDATABASE` survive → **the silent no-op is proven**.
- `env -u ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES` → guard becomes `<unset>` ⇒ sweep ON.
- Scrub **without** the explicit pin in a shell whose ambient guard is absent (the worktree case) → guard `<unset>` ⇒ sweep ON.

### PORT hazard — measured against the real loader
`daemon/config.load_config` on the worktree `config.yaml`: PORT unset + DAEMON_PORT unset → `daemon.port = 8088`; **PORT only → PORT wins**; **DAEMON_PORT only → IGNORED, falls back to 8088**; both → PORT wins; `PORT=''` → empty string (NOT the 8088 default — `os.environ.get()` at `daemon/config.py:75-76`). The launcher keeps its own copy (`launcher.sh:1435-1460`). ⇒ **the recipe must export `PORT` explicitly; `DAEMON_PORT` alone is insufficient.**

### Verified S5 command shape (secrets redacted)
Scratch payload copy (never boot in place — a payload under a canonical install dir auto-derives `ENSEMBLE_SELF_ENV=live` via the install-dir + DB cross-check and opens the prod-DSN carve-out) + scratch `HOME` + the **34-name** per-variable scrub + explicit pins `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0`, `ENSEMBLE_BASH_ORPHAN_REAPER=0`, `ENSEMBLE_RAM_GUARD=0`, `ENSEMBLE_SELF_ENV=sandbox`, `PORT=19101`, `HOST=127.0.0.1`, `ENSEMBLE_CONFIG=<scratch>/payload/config.yaml`, `ENSEMBLE_DATA_DIR=<scratch>/data` + a scratch PG DSN → `timeout -k 5 30 <scratch>/payload/ensemble-prod`, with pre-boot assertions (guard == 0 and never unset; `PORT == DAEMON_PORT`; `POSTGRES_DB != ensemble_prod`; `POSTGRES_HOST != 10.44.0.2`; `SYSTEM_ENCRYPTION_KEY` present) and isolation inside a systemd transient unit whose `/proc/<MainPID>/cgroup` shows `/user.slice/` and NOT `ensemble-main.service` (**no shared-cgroup fallback**).

### Plan-vs-source discrepancies (source is truth; the plan was NOT edited)
1. §5.4 scrub list incomplete (BLK-2).
2. §5.4:339 "the hazard is not the value — it is `env -u` stripping it" is **imprecise**: the value **is** load-bearing (`=1`/`true`/`yes`/`on`/empty/whitespace ⇒ ON).
3. §5.4:341-351 "safe invocation" is a **LIVE-shaped boot** (BLK-1).
4. §5.4:337 omits the lane-conditional default (absent ⇒ ON only when `sys.frozen`) and the "set to anything but the disable set ⇒ ON in both lanes" branch.
5. §5.4 omits the 2nd and 3rd boot-time terminators.
6. §5.4:339 "the live `.env` already carries the guard (verified)" is true for the live install and **false for the worktree `.env`**.

---

## 5. Release blockers (named explicitly)

**BLK-1 (critical, ceremony-facing)** — release plan §5.4 "safe invocation" boots the not-yet-promoted binary against `ensemble_prod` on `PORT=9797` with `ENSEMBLE_SELF_ENV=live`. Effects: (i) the three room tables are created in the LIVE prod DB by `create_all` **outside the promote ceremony** — if the promote aborts, prod carries the new schema under the old binary; (ii) port 9797 collides with the running live daemon; (iii) the prod-DSN carve-out is open (`daemon/ensemble_config.py:269`). **Fix (plan owner's call):** scratch payload + scratch PG + `ENSEMBLE_SELF_ENV=sandbox` + a 19100-19999 port + the 34-name scrub + the three explicit terminator pins — i.e. the verified shape in §4.

**BLK-2 (critical, ceremony-facing)** — the plan's scrub leaves `POSTGRES_URL` (full DSN, short-circuits the builder) and the `DATABASE_URL*` / libpq `PG*` carriers live; a scrubbed smoke can still be pointed at prod by ambient env. **Fix:** extend the scrub to the full list (§4).

Both are defects in the **plan document**, not in the code. The plan is the planner's artifact; per this commission's scope the plan was **not** edited — the fix is reported, not silently applied.

---

## 6. Code changes made in this session

| Commit | Files | Change |
|---|---|---|
| `b47e9165f532f2dbdd6cd0f0b5fdb5684626100e` | `.agents/tester/PACKS.md` only | +9 / −0 — append-only section registering the release-tip `gc_p6_room_family` composition (24 files / 421 nodes) |

No production code was modified. No version bump, no release dir, no tag, no push. No live process was started, stopped, or killed; no live port was bound.

---

## 7. Anomalies

- **Credential capture (harness defect, contained).** The LEG 3a worker quoted its fenced command inline, including the RAW dev-role `POSTGRES_PASSWORD`. Exposure is **transcript-only**: the only in-repo hits are untracked mode-600 env files (the legitimate `.env` + four pre-existing backups); **zero hits in git history**; the committed PACKS.md row carries `<redacted>`. Root cause is a **dispatch-template omission** (the redaction rule was in the LEG 3b brief but not the LEG 3a brief). Filed as an anomaly; house lesson recorded via `experience()`. **Not rotated unilaterally** — the dev PG password is shared by in-flight lanes and rotation is a live-op outside the tester's mandate; surfaced as a rotation candidate.
- **Skill corpus defect.** The `staged-binary-boot-smoke` skill's fence 3 claims "a PORT-only override silently falls back to the forbidden 8088" — **inverted** vs the measured loader (PORT wins; DAEMON_PORT-only falls back to 8088). `skill_fix` filed; the worker also created `boot-smoke-env-fence-source-verification` so the truth tables need not be re-derived.
- **POK store at its 256-cap** — refused writes, as the project note anticipated; durable facts recorded via RAG `experience()` instead.
