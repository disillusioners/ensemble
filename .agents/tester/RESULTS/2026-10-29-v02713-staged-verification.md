# STAGED VERIFICATION — v0.27.13 (db pool idle-in-transaction lane + v0.27.12 release delta + semantics fixes)

- **Artifact:** `/home/nea/agents-ensemble/releases/v0.27.13` · staging worktree `/home/nea/ensemble-src-wt-stage-v02713` (detached @ `b1183a637649f94c23b94dfc745df44381dc54ab`, KEPT for the ceremony) · binary `ensemble-prod` 81,099,288 B.
- **Verified independently** (nothing from the giter's report trusted — every claim recomputed from disk).
- **Verdict: ✅ STAGED-VERIFIED PASS-WITH-NOTES — the artifact is CEREMONY-READY** (promote remains the user's 3-factor nonce ceremony; nothing was promoted, rolled back, flipped, or restarted).
- **Evidence branch:** `tester/dbpool-functional-gate` (worktree `/home/nea/ensemble-src-wt-tdbp-gate`); bundle at `.agents/tester/RESULTS/raw/2026-10-29-v02713-staged-verification/`.
- **Fence:** read-only on `releases/**` and the staging worktree (no writes, no deletion of the staging worktree); own scratch PG + own transient systemd unit only; per-variable POSTGRES_*/DAEMON_PORT scrub everywhere (the host shell is prod-shaped: `POSTGRES_DB=ensemble_prod`, `POSTGRES_HOST=10.44.0.2`, `ENSEMBLE_SELF_ENV=live`); `HOME=$(mktemp -d)`; no external calls attempted by the harness; ports 9797/8088 never bound; nothing killed by name.

## 1 · ARTIFACT INTEGRITY — 8/8 PASS, zero discrepancies vs the giter's claims

| Check | Result |
|---|---|
| **4-way binary sha** | staged disk == worktree `dist/` == manifest `binary_sha256` == binary sidecar `artifact_sha256` == `b935096359dc929b3557fb38e5257aa2693b36b5c53b1c3e1cd72ca58878423a` ✔ (bonus: `launcher_sha256` `a3a44461…`, `config_sha256` `85468a2e…` re-hash MATCH; distinct from live v0.27.12's `468dacab…`) |
| **Sidecars** | binary + FE: `git_head=b1183a637649f94c23b94dfc745df44381dc54ab`, `git_dirty=false` both; FE `artifact_sha256` `5531cbe4…` recomputed from the STAGED copy of index.html — MATCH |
| **Manifest re-hash** | **5361/5361 entries** (agents 338 + frontend 93 + plugins 4930) re-hashed from disk — **0 mismatches, 0 missing**; census exact in BOTH directions (0 unkeyed files on disk, 0 keys missing). **All three tree-hashes REPRODUCED** (method reconstructed from `stage.sh:441-446` + `_tree_manifest`/`_tree_hash_of_lines`: sorted `sha␠␠path\n` → sha256): agents `f1b490d5…`, frontend `bb076dc0…`, plugins `4715cf64…`. Method self-validated against v0.27.12's published hashes (reproduces all three) ⇒ payload trees byte-identical v0.27.12↔v0.27.13 (daemon-binary-only release) |
| **Payload hygiene** | top level exactly `agents, config.yaml, ensemble-prod, frontend, launcher.sh, manifest.json, plugins`; no `.env*`, no `*.build-provenance.json`, no loose `.sql`, no `.git`, no `node_modules`, no `.staging.*` alongside. (5 nested `scripts/` dirs exist INSIDE the vendored plugins tree — inside the re-hashed plugins tree, not a violation) |
| **Tag** | object `abee408fc2013fb755e09d7a8d7d393e453c7e39`, type `tag`, peels to `b1183a637…` == the 4-file bump commit (`daemon/__init__.py`, `pyproject.toml`, `tests/test_api.py`, `uv.lock`; house form `chore(release): bump version to 0.27.13`). Remote carries BOTH the tag object and the peeled `^{}` target. Annotation carries the rollback adjudication verbatim ("migration delta vs live v0.27.12 is EMPTY (115 == 115 embedded .sql, zero diff, 115 distinct prefixes) ⇒ ENSEMBLE_ROLLBACK_SAFE=1 legitimate"). L1 corroborated read-only: `origin/latest` == worktree HEAD == `b1183a637` |
| **Migration census** | **115 embedded `.sql` / 115 distinct `YYYYMMDD_HHMMSS` prefixes** (`uniq -d` empty); binary census IDENTICAL to the source `ls-tree` census at `b1183a637`; lexically-latest = `20261026_060000_add_mission_commission_resolved_at.sql` == manifest `known_schema_gen`; delta vs live v0.27.12 EMPTY in both directions (git diff with `-M` and `--no-renames`, whole-dir diff 0 rows) ⇒ **no lane-authored .sql**, rollback-safe posture holds (empty delta ⇒ the old binary stays compatible) |
| **Live fence** | `current` symlink at the INSTALL ROOT (`/home/nea/agents-ensemble/current -> releases/v0.27.12`; note: `releases/current` does not exist — the brief's path was wrong) · state.json verbatim: `current=v0.27.12, previous=v0.27.11, in_flight=null, pending_op=null, quarantined=[]` · nothing written |
| **PROMPT-BASELINE** | `PROMPT-BASELINE: OK — 161 versioned artifact(s) match .prompt-artifacts.sha256` (exit 0; fenced, certified interpreter, staging worktree @ `b1183a637`) |

## 2 · LANE CONTENT IN THE COMPILED ARTIFACT — 8/8 PASS (bytecode-level)

Method (house, per the v0.27.8 S3 reference): CArchive cookie → PYZ → `zlib.decompress` + `marshal.loads` → code objects → recursive `co_consts` walk **plus `dis` disassembly** (the const pool alone cannot settle a *value* or a *comparison form*). Reader cross-validation: two independent implementations (native PyInstaller `CArchiveReader`/`ZlibArchiveReader` vs the custom reader) agree on 4632 PYZ modules, identical TOC key set, **0 differing TOC tuples**, and identical `marshal.dumps(code)` for the 9 key daemon modules.

| Claim | Verbatim evidence | Verdict |
|---|---|---|
| `MANAGER_POOL_CONFIGURED_CAPACITY` = 25 | `165 LOAD_SMALL_INT 25` → `STORE_NAME 59 (MANAGER_POOL_CONFIGURED_CAPACITY)` | ✅ |
| `_manager_pool_effective_capacity` present | code object at :1501 → `MAKE_FUNCTION` → `STORE_NAME`; call site `1587 LOAD_GLOBAL _manager_pool_effective_capacity` → `CALL 1` in `_refuse_pool_admission_at_capacity` | ✅ |
| `MissionEpochConflictError` | repository: `LOAD_BUILD_CLASS` + `LOAD_CONST 'MissionEpochConflictError'` (subclasses `IntegrityError`); service: `IMPORT_FROM 14 (MissionEpochConflictError)` | ✅ both modules |
| **F3: `text("BEGIN")` ABSENT** | exact-const equality over **582 compiled daemon modules** + 5 shipped `.py` + 115 `.sql` ⇒ **0** `'BEGIN'` consts. **One-way positive control** (lane base v0.27.11 binary): 1 hit — `daemon.tools.ens_db_tools::._dry_run_sqlite_safe` const[11] = `'BEGIN'` at :1479/:1491. Staged fix shape present: `session.execute(text(sql))` + **4 rollback sites, 0 BEGIN consts** | ✅ |
| strict-False carrier consumer | sync `11238 LOAD_FAST_BORROW live_carrier_exists` / `LOAD_CONST False` / **`IS_OP 0` (= `is`)** / `POP_JUMP_IF_FALSE`; async `12061` identical | ✅ both sites |
| `MANAGER_ENGINE_LEGITIMATE_HOLD_BUDGET_S` = 0.25, sentinel absent | `394 LOAD_CONST 0.25` → `STORE_NAME 73 (…)`; **0** bindings of that target to any other value; the only `-1.0` float anywhere in daemon.* is an unrelated function-local in `skill_evolution_service`; **0** module-level `-1.0` assignments | ✅ |
| pool knobs as code | `create_postgres_engine` `KW_NAMES ('echo','pool_size','max_overflow','pool_pre_ping','pool_reset_on_return','pool_recycle','pool_timeout','connect_args')`; `pool_reset_on_return='rollback'`; `MANAGER_ENGINE_POOL_RECYCLE_S` = 3600; `MANAGER_ENGINE_POOL_TIMEOUT_S` = 30 (note: SQLAlchemy's spelling is `pool_reset_on_return`, not `reset_on_return`) | ✅ |
| per-pool `application_name` trio; absent from the shared constant (D-17) | `ensemble-manager` (constants :288), `ensemble-repair` (ens_db_tools :193), `ensemble-checkpoint-adapter` (persistence :61) + use sites (factory :390, ens_db_tools :650, persistence :396 asyncpg `server_settings`); shared `PG_SESSION_CONNECT_ARGS` = **one key** (`options: -c timezone=UTC`), **no** `application_name`; module-level `application_name` in factory = 0 | ✅ |

**Artifact facts worth carrying forward:** compiled version `0.27.13` (daemon module `co_consts`); build interpreter = **Python 3.14** (CArchive `pyvers=314`, `libpython3.14.so.1.0`) ⇒ **the house's certified interpreter (3.13.15) CANNOT read this artifact** (3.13 `marshal` cannot load 3.14 code objects); the uv-managed cpython-3.14.7 was used with `-B` and PyInstaller's readers from the certified venv via PYTHONPATH. Three daemon modules vs v0.27.11 (582 vs 579: `metrics_census`, `metrics_gate`, `metrics_listener` — drift-inherited, not lane-authored).

## 3 · ISOLATED BOOT SMOKE FROM THE STAGED BINARY — PASS

Arena: `/tmp/sv3-boot/releases/v0.27.13/ensemble-prod` (binary copy, sha == staged disk == manifest == sidecar). **Layout chosen so the binary's path-derived `install_dir` resolves INTO the arena** — proven live by the daemon's own log line `journal absent at /tmp/sv3-boot/releases/state.json` ⇒ the known "staged smokes are NOT isolated" trap was structurally avoided and the live promote journal was never bound.

| Observation | Result |
|---|---|
| Unit + cgroup | `systemd-run --user --unit=sv3-v02713-boot.service`; `ControlGroup=/user.slice/user-1000.slice/user@1000.service/app.slice/sv3-v02713-boot.service` vs live `/system.slice/ensemble-main.service` — **not the live group**. Unit env fully explicit (PORT=9783, ENSEMBLE_CONFIG, ENSEMBLE_SELF_ENV=sandbox, six POSTGRES_*, dummy OPENAI key, `OPENAI_BASE_URL=http://127.0.0.1:9` dead-loopback) |
| Banner | `Starting Ensemble v0.27.13` (logged twice) |
| `/livez` | 200 · `{"status":"alive","version":"0.27.13"}` |
| `/readyz` | 200 · status=ready · components {database, queue_freshness, services, checkpoint_saver} all true · draining=false · grace_active=false · leak_signal.detected=false |
| **pool_state (derived-capacity path alive, no crash)** | `sqlalchemy {checkedout:0, size:15, overflow:-6}`, `psycopg {pool_size:2, pool_max:10, pool_available:2, requests_waiting:0}` — stable across 3 further probes |
| Attribution on the real path | `pg_stat_activity`: `ensemble-manager` ×9 idle, `ensemble-checkpoint-adapter` ×1 idle (+2 unnamed psycopg); 83 tables materialized (incl. `board_items`, `mission_commissions`, `notify_audit`, `wedge_captures`) |
| PROMPT-BASELINE | 161/161 OK (read-only `--check`) |
| Teardown | **SIGTERM-only**: `systemctl --user stop` → inactive/dead/success, MainPID=0, cgroup gone, **port 9783 free**, all 5 pids gone (daemon ×2 + npm/sh/node context7 children); no `pkill`/name-kill |
| Scratch PG | PG16.15 :5461, role+db `dbpool_sv3boot` over TCP; teardown evidenced (DROP DATABASE + DROP ROLE, residual census 0\|0, cluster stopped, PGDATA removed, port free) |
| **Live fence (final)** | livez `version 0.27.12`, uptime 58009 s · MainPID **2865182 unchanged** · ActiveEnterTimestamp **2026-10-29 02:23:42 UTC unchanged** · NRestarts **0** · current → releases/v0.27.12 · **state.json sha `c745b9df…` byte-identical to the pre-boot baseline** · staged payload sha unchanged · staging worktree porcelain 0 |

**Two boot-smoke disclosures (daemon behavior, not artifact defects):** (1) **the "no external calls" expectation does not hold for a v0.27.11+ binary** — the built-in `context7` MCP provisions via `npm exec @upstash/context7-mcp`, pulling from the npm registry into the **fenced FAKE_HOME** (107 MB) with an ESTAB :443 to Cloudflare; mitigated (LLM lane pinned to a dead loopback; the real `~/.npm` mtime unchanged; the daemon's own PG traffic loopback-only). (2) `webfetch` degraded gracefully (init_guard `attempts=1/3`, breaker closed, recoverable) with readiness **not** flipped — the C4/C2 advisory behavior observed live. A surviving `npm exec` process was proven (ppid/cgroup/HOME) to be the **live** daemon's own child and was left strictly untouched.

## 4 · THE LADDER FROM THE STAGED SOURCE TREE — 17/17 PASS

**The staged release dir is SOURCE-LESS; the ladder was executed from the staging worktree `/home/nea/ensemble-src-wt-stage-v02713`** (not from the release dir). Re-point disclosure: the scripts' embedded constant was `…-wt-tdbp-gate` (not the lane worktree), **and the ladder derives its fence path from `WORKTREE`** ⇒ **3 copy-only edits** were required (ladder `WORKTREE`, ladder `FENCE=`, fence `WORKTREE`) — `diff -u` shows only those 3 lines; the staging worktree was never modified. Proof the re-point is real: the ladder's certification printed `/home/nea/ensemble-src-wt-stage-v02713/daemon/__init__.py 9.0.2`.

T-H1 sanity: `--run G9.9` → `LADDER: FATAL — no gate row matched 'G9.9' … ZERO gates were executed`, exit 1. Check mode: `LADDER: OK (mode=check, rows matched=17)`, every row `COLLECTED-OK`. Full run: `LADDER: OK (mode=--run, rows matched=17)`, exit 0, **238 s**, 17/17 PASS, **zero skips** (grep-verified), G1.4 = **39 passed + 1 xfailed** (the row-18 strict-xfail canary, matching the expected-collected footnote verbatim; 39+1 = 40). Every row expected==collected: G1.1 9, G1.2 8, G1.3 6, G1.4 40, G2.1 36, G2.2 6, G2.3 6, G2.4 23, G2.5 5+9, G2.6 3, G3.1 3, G3.2 7+7, G3.3 12, G3.4 5. G2.1/G2.4 summaries were displaced by the ladder's `tail -3` window (a pytest tmp-dir `rm_rf` warning) ⇒ both re-run with full output: **36 passed** / **23 passed** (fence counts matched). G1.5/G3.5 are tester-owned and were not re-run (scope ruling). Scratch PG :5462 torn down with evidence (`PG_TEST_ALLOWED_DBS=dbpool_sv4` required by the conftest's fail-closed allowlist; drop-db + drop-role verified 0\|0, port free).

Staging-worktree verification: HEAD `b1183a637…` == `git describe --tags --exact-match HEAD` = `v0.27.13`; porcelain 0 before AND after; expected-collected.md cells row1=**23**, row7=**16**, row17=**5**, row18=**8**, row35=**30975**, **0 TBD cells** in the 35 table rows; the reconciliation section present (merged-tree census 31150 of 32259).

## 5 · AFFECTED PACKS (the 2-commit delta) + DELTA NOTE

| Pack | Result |
|---|---|
| census families (`test_dbpool_census_families.py`) | **5 passed** — the §7.3 re-baseline: `AST_BASELINE {A:438, B:175, C:4, D:148, E:14, F:2}`, total **781** (family F 1→2 — the WS-B `metrics_census.py:234` raw-psycopg site audited benign); the live scan matched the baseline exactly |
| bump's version assert (`tests/test_api.py::test_health_check`, asserts `version == "0.27.13"`) | **1 passed** |

**Delta note (correction to the ruling's wording, material for the ceremony):** `git log 6947cc2b8..b1183a637` = **39 commits**, not 2 (231 files, +82,811/−303). The **lane-authored** delta IS exactly the briefed 2 — `3fdfc5462` (family-F re-baseline) + `b1183a637` (4-file bump) — but the WS-B metrics-collection lane (`bf673fdbe`, ~30 commits) entered `origin/latest` **after** my FM4 whole-tree tree and rode into the staged tree via the lane's re-merge (`04de2d10c`) + sweep merges (`68a35a8aa`, `ce32a0962`). ⇒ **my whole-tree evidence (6947cc2b8) covers the dbpool-lane delta, NOT the WS-B content the staged artifact ships** — that content stands on the WS-B lane's own gates. The 17-row ladder + the affected packs are lane-scoped and green; the boot smoke exercised the shipped binary end-to-end.

## 6 · WATCH LIST (classification — none blocks the artifact)

| Item | Class | Action |
|---|---|---|
| `test_resource_monitor_platform_fallback::…::test_the_throttle_latch_is_released_when_the_ema_recovers` | **INHERITED** (deterministic 0/3 at origin/latest AND the merged tree; `proc-root-unreadable` env shape) | will redden the staged whole-tree/boot gates — attribute inherited, do not block. Out of the 17-row scope |
| Disclosed flake ledger + `test_atomic_dequeue` sibling | pre-existing flaky, both sides | disclose only |
| g7 `sys.exit(0)` INTERNALERROR (rc=3; its own runner PASS 3/3) + e2e-hybrid collection (rc=2, :8079) | collection specials — the two house `--ignore`s | keep the ignores in any whole-tree gate |
| `PG_TEST_ALLOWED_DBS` | harness requirement (fail-closed exact-name allowlist) | export it for custom-named scratch DBs (it cost one deterministic first-run FATAL in an earlier leg) |
| `install-audit.jsonl` CWD foot-gun | 4th+ occurrence (contained every time) | preserve + `git checkout --` before any commit point |
| base-proven seal tripwire | pre-existing red at base AND head | inherited |
| `expected-collected.md` stale PROSE (status line + trailer still say "TBD") while all 35 cells are filled | repo hygiene — the Δ1 parser reads CELLS, so the gate passed; the plan dir is NOT in the payload trees, so it does not ship | plan-owner cleanup (non-blocking) |
| staging worktree `.pyc` footprint (60 `__pycache__` dirs / 516 `.pyc`, 511 written in the window; gitignored ⇒ porcelain 0) | hygiene — not deleted (a concurrent lane may be compiling the same tree) | harmless; note for the ceremony |

## VERDICT

**✅ STAGED-VERIFIED PASS-WITH-NOTES — the v0.27.13 artifact is CEREMONY-READY.** Artifact integrity 8/8 (zero discrepancies); lane content 8/8 at bytecode level; isolated boot smoke green (banner/livez/readyz v0.27.13, derived-capacity readiness path alive, cgroup-isolated, SIGTERM-only teardown, live daemon and state.json byte-identical); ladder 17/17 from the staged source with G1.4's exactly-1-xfail and zero skips; affected packs 5/5 + 1/1. The notes are: the **WS-B coverage wording** (the staged tree ships ~30 commits of metrics-lane content not covered by my whole-tree leg — it stands on that lane's own gates; the boot smoke exercised the shipped binary end-to-end), the **built-in-MCP outbound npm fetch** at boot (daemon behavior, sandbox-contained), the **live-v0.27.12 attribution** (its sidecar head is `9bbef71c1f`, NOT the meta-KV's `79bf44936`; live v0.27.12 predates the Δ3 work and the pool-knob hotfix), the **3.14 artifact vs the 3.13 certified interpreter** (brief-template defect), and the **stale plan prose / `.pyc` footprint** hygiene items. **Promote stays the user's 3-factor nonce ceremony; this gate performed no promote, rollback, flip, or live restart.**
