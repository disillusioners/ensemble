# Rogue-Process Containment — Component A: Port-Ownership Contract

**Feature:** rogue-containment (incident wi-2454ede3, 2026-11-11 bind-race restart loop)
**Component:** A — port-ownership contract (analysis §5.2 "Lane 2": (b)+(c)+(f) as ONE mechanism, daemon-side)
**Mode:** Mode 1 — greenfield plan file for one component (sibling workers own B and pins-integration/overview)
**Status:** design only. NO code changes, NO test runs, NO repo writes except this file and its commit.
**Governing authority:** `.agents/shared/planning/bind-race/rogue-port-containment-analysis.md` (FINAL rev 2). Where this file contradicts the analysis, the analysis wins and the conflict is recorded in §14 (Contradictions).
**Worktree:** `/home/nea/ensemble-src-wt-rogue-containment`, branch `feature/rogue-containment`, tip `cace21473`.
**Commit (to be made after writing):** `docs(rogue-containment): component A — port-ownership contract plan`

---

## 1. Objective

Deliver the port-ownership contract: a single daemon-side mechanism that runs **before** the listener bind and refuses the port **even against an explicit `PORT=9797`** when a **live foreign owner** is proven, yielding **exit 78 + a holder-identity marker line** (`pid` / `exe` / `cwd` / `install` / `port`). Claim absent, stale, same-install, write-failed, or unknown ⇒ take-and-bind / degrade / retry — **never refuse**. Install-dir scoping makes staged smokes (which resolve the live install root via the frozen-binary path derivation) non-foreign, so they can never refuse the live. A mandatory daemon-side operator alert ships in the same release (the launcher's refuse branch has no `_notify_once`).

This is the **only** control robust to a lane that sets `PORT` explicitly, and the **only** one that yields attribution. It touches **zero** `launcher.sh`, **zero** unit-template, **zero** live `.env` lines (binding principle, analysis §6).

## 2. Scope

- **In scope:** component A only — the claim-before-bind ownership mechanism + its mandatory operator alert + its regression pins.
- **Out of scope (sibling workers):** component B (spawn-boundary env scrub + 9798 fence rider (d)) lives in `component-B-spawn-boundary-scrub.md`; pins-integration/sequencing/overview lives in a later worker's file. Cross-reference by filename; do not duplicate their content.
- **Out of scope (deferred per analysis):** Lane 4 (sliding-window burst fix) — trigger-gated, touches `launcher.sh` ⇒ own staged lane. Lane 5 (teardown budget) — durability lane. Boot-sweep disarm investigation (§7.2). H1 residual (same-install rogue) — declared hole, survives.
- **Hard constraints:** plan ONLY component A. NO code, NO tests run, NO repo writes except this file + its commit. NEVER push. NEVER touch canonical `/home/nea/ensemble-src`.

## 3. Phases

| # | Name | File(s) | Objective | Depends on |
|---|------|---------|-----------|------------|
| 1 | Claim mechanism + placement | `daemon/services/port_ownership.py` (new); `daemon/__main__.py` (wire) | Implement claim-before-bind with install-dir scoping, liveness, atomic race, degrade-never-refuse; wire the gate before the bind | — |
| 2 | Operator alert (mandatory companion) | `daemon/services/port_ownership.py` (marker line + refusal marker file) | Logging marker line (always) + durable refusal marker file (recommended default) so a 78 is not a silent-down | Phase 1 |
| 3 | Regression pins | `tests/unit/test_port_ownership.py`; fenced-mirror pack | E-ii / E-iii / promote-shape pins, mutation-discipline compliant | Phase 1, 2 |

## 4. Decisions (frozen for this component)

| ID | Decision | Rationale (cite analysis) | Rejected alternatives |
|----|----------|---------------------------|----------------------|
| D1 | **One mechanism = (b)+(c)+(f):** claim before bind (install dir + pid + `/proc/<pid>/stat` starttime liveness); refuse ONLY on positive proof of a live foreign owner ⇒ exit 78 + marker line; claim absent/stale ⇒ take-and-bind; write failure ⇒ degrade, never refuse; unknown ⇒ retry, never refuse | §5.2 shape (all three are one mechanism, not three features); §5.2 "Why this is the mechanism that closes the hole" | (a) make `DAEMON_PORT` authoritative (§5.6 — binds live to 8088, kills ADR-014 SSOT); `SO_REUSEPORT` on 9797 (§5.6 — split-brain); (e)-as-policy (§5.6 — already failed once) |
| D2 | **Install-dir scoping makes staged smokes non-foreign** — they resolve the live install root via the frozen-binary path derivation (`sys.executable.parent.parent.parent` when `sys.frozen`), so their `install_dir` == live's ⇒ never refuse the live | §5.2 point 4; foot-gun note (analysis + `daemon/api.py`/`upgrade_tools.py` path derivation) | per-boot identity in the claim (deferred — H1 residual) |
| D3 | **Refusal exit code = 78, reused** (never 75 — budget-exempt ⇒ unbounded slow retry; never a new code — lands in `*→crash` and loops unless launcher+unit learn it) | §5.5; `classify_exit` (`launcher.sh:3587-3595`); `RestartPreventExitStatus=78` (`scripts/systemd/ensemble-daemon.service:110`) | 75 (budget-exempt, §5.5); a dedicated ~73/79 (paired launcher+unit lane, deferred) |
| D4 | **The ownership refusal is a separate gate BEFORE the bind-retry loop** — it exits 78 directly and never enters `bind_socket_with_retry`; the loop's exit-1 path (`BIND_FAILURE_EXIT_CODE=1`) is unchanged | §5.5 "the ownership refusal must NOT be swallowed or re-classified by the bind-retry loop"; placement contract `__main__.py:709-713` | folding the check into the loop (would conflate 78 with 1) |
| D5 | **Placement inside `_serve_with_bind_retry`, after `install_root_logging()` and before `bind_socket_with_retry`** — so the marker line lands in journald + file log, the gate runs on both entry paths (frozen + `python -m daemon`), and it stays outside any `run_preflight` block | §5.1 placement contract; `__main__.py:445` (install_root_logging) / `:472-473` (bind); auth preflight `:514-520` is the template | placing in `main()` before `_serve_with_bind_retry` (logging not yet installed → marker invisible) |
| D6 | **Foreign-ness rule: a holder is foreign ONLY when both its `install_dir` and the self `install_dir` are resolved non-`None` AND they differ.** Otherwise (either side unresolved, e.g. dev/source/pytest/transient) ⇒ non-foreign ⇒ take claim | trip-risk rows 3/4; `upgrade_tools._resolve_install_dir` returns `None` for dev/unresolved | treating `None` as foreign (would refuse dev/pytest/transient boots) |
| D7 | **Claim file is filesystem JSON under the resolved install root's data dir**, NOT a SQLModel table — zero migration, zero rollback_safe impact | analysis §6 "binary swap only"; migration-expectation mandate | a DB-backed claim (would need a migration + change rollback_safe adjudication) |
| D8 | **Operator alert (mandatory companion): recommended default = logging marker line (always) + durable refusal-marker file under the data dir.** Leader confirms final channel (analysis §9.4) | §5.2 "MANDATORY COMPANION"; launcher refuse branch has no `_notify_once` (`launcher.sh:3814-3816` vs `:3821`/`:3848-3851`) | logging-only (silent-down risk); critical-notes direct write (not wired at boot); Discord webhook (new dependency) |
| D9 | **Claim-file cleanup on graceful shutdown is best-effort** (remove the claim file); on crash the claim goes stale and the next boot reclaims via liveness (`pid` dead or starttime mismatch) | stale-claim self-healing; pid-recycle defence via field-22 starttime | mandatory cleanup (impossible on SIGKILL) |

## 5. Claim-File Design

### 5.1 Location

`<install_root>/data/port-claims/<host>-<port>.json` (one file per `(host, port)`), where `<install_root>` is resolved by the **existing** machinery (`daemon/services/promote_txn_hold._resolve_install_root()` → `daemon/tools/upgrade_tools._resolve_install_dir` / `_canonical_install_root`), with `_canonical_install_root("agents-ensemble")` as the fallback. **Reuse the machinery; do not re-derive** (the frozen-binary path derivation foot-gun means a scratch boot resolves the LIVE install root — `daemon/tools/upgrade_tools.py:666-699`, `daemon/services/promote_txn_hold.py:140-165`).

Consequence (intended, per analysis §5.2 point 4): live + staged share the same claims directory ⇒ a staged smoke's claim records the same `install_dir` as the live ⇒ non-foreign ⇒ can never refuse the live. A rogue whose install root differs (e.g. `/tmp/ens-e2e-cOTp`) records a different `install_dir` ⇒ foreign.

### 5.2 Format (JSON)

```json
{
  "install_dir": "/home/nea/agents-ensemble",
  "pid": 3612324,
  "starttime": 1234567890,
  "port": 9797,
  "host": "0.0.0.0",
  "claimed_at": "2026-11-12T01:33:19Z",
  "version": "0.29.15"
}
```

- `install_dir`: resolved install root string (the foreign-ness key).
- `pid`: claimer's pid (for liveness).
- `starttime`: field 22 of `/proc/<pid>/stat` (pid-recycle defence; house precedent).
- `port` / `host`: the bound surface (main 9797 or metrics 9798 — the mechanism is generic).
- `claimed_at`: ISO-8601 (forensics).
- `version`: optional but recommended (cheap; aids attribution — analysis §9 / discovered decision).

### 5.3 Liveness + staleness check

`pid_alive_with_starttime(pid, starttime)`:
- Read `/proc/<pid>/stat`; parse field 22 (starttime). If the file is absent ⇒ dead ⇒ stale.
- If starttime differs from the claim ⇒ pid recycled ⇒ stale.
- If `/proc/<pid>/exe` is unreadable or the cgroup membership is gone ⇒ stale (fail-open toward "stale", i.e. reclaim).

`is_live_foreign(claim, self_install_dir)`:
- If `claim` is `None` ⇒ not foreign (no owner) ⇒ take.
- If `pid_alive_with_starttime` is false ⇒ stale ⇒ not foreign ⇒ take.
- If `claim.install_dir` is `None` OR `self_install_dir` is `None` ⇒ non-foreign (D6) ⇒ take.
- If `claim.install_dir == self_install_dir` ⇒ same install ⇒ non-foreign ⇒ take.
- Otherwise ⇒ **foreign live owner** ⇒ refuse.

### 5.4 Two-boots race

Two daemons (different installs) booting simultaneously may both observe no claim (or a stale claim). Resolution:
1. Attempt an atomic create with `O_CREAT | O_EXCL | O_WRONLY`. First writer wins; the file is fsync'd.
2. On `EEXIST`: re-read the claim; re-run `is_live_foreign`. Loop up to a bounded number of retries (e.g. 3) with a short sleep (e.g. 250 ms) to let the winner finish writing.
3. If still ambiguous after retries ⇒ **unknown ⇒ retry the bind, never refuse** (D1 "unknown ⇒ retry"). Concretely: skip the ownership gate for this boot, log a warning, proceed to `bind_socket_with_retry` (which handles EADDRINUSE via its own 30 s backoff). This degrades gracefully and never silently refuses a legitimate boot.

### 5.5 Write-failure degrade path

If the claim write fails (permission denied, disk full, read-only FS, etc.):
- Log a WARNING naming the path and the error.
- **Degrade, never refuse** — proceed to bind without ownership enforcement for this boot.
- Do NOT exit 78. The mechanism's safety property is "refuse only on positive proof"; a failed write is the opposite of proof.

### 5.6 Cleanup on shutdown (stale-claim self-healing)

- On graceful shutdown (in the lifespan teardown, after the listener stops), best-effort remove the claim file **only if its `pid` == self pid** (do not delete a successor's claim).
- On crash/SIGKILL there is no cleanup; the claim goes stale. The next boot's liveness check (`pid` dead or starttime mismatch) reclaims it. This is the self-healing property — no stale-claim leak.
- The claims directory itself is created lazily (`mkdir(parents=True, exist_ok=True)`); a failure to create it ⇒ degrade (§5.5).

### 5.7 Operator alert (mandatory companion — D8)

The launcher's refuse branch has **no** `_notify_once` (`launcher.sh:3814-3816` logs then `exit 78`, vs. tempfail `:3821` and burst-abort `:3848-3851` which do call `_notify_once`). So a 78 is a **silent-down** unless the daemon adds its own alert. Daemon-side channels available at the refusal moment (pre-lifespan, after `load_config()` + DB preflight, before bind):

| Channel | Available at refusal moment? | Pros | Cons |
|---|---|---|---|
| A. Logging (WARNING) to journald + file log | **yes** — `install_root_logging()` runs at `__main__.py:445`, before the gate | always available; no new dependency; launcher already surfaces journald | requires someone watching logs; the analysis notes journal silence was "strong evidence of no action, weak evidence of arming" |
| B. Durable refusal-marker file under the data dir (e.g. `data/port-ownership-refusal.json`) | **yes** — data dir resolved by `load_config()`/`EnsembleConfig` | survives the 78 exit; ari/operator can read it on next boot; auditable | needs a reader (ari sweep or operator) to surface it |
| C. Critical notes (DB-backed, agent tool) | **no** — service not wired until lifespan; would need a deferred write or post-boot sweep | integrates with the existing critical-notes ledger | higher coupling; not available at the refusal moment |
| D. Direct Discord webhook | **no** — not a mechanism at tip; would be a new dependency | pushes to chat | out of scope for "binary swap only"; new secret/endpoint plumbing |

**Recommended default: A + B** (logging marker line always, plus durable refusal-marker file). Leader confirms the final channel (analysis §9.4, decision 4). The marker line (both channels) carries the holder identity: `pid` / `exe` (`/proc/<pid>/exe` readlink) / `cwd` (`/proc/<pid>/cwd`) / `install` (claim.install_dir) / `port`.

Refusal-marker file shape (channel B):

```json
{
  "refused_at": "2026-11-12T01:33:19Z",
  "port": 9797,
  "host": "0.0.0.0",
  "holder": { "pid": 3612324, "exe": "/tmp/.../ensemble-prod", "cwd": "/tmp/...", "install_dir": "/tmp/ens-e2e-cOTp" },
  "self_install_dir": "/home/nea/agents-ensemble",
  "exit_code": 78
}
```

The marker file is **auto-cleared on the next successful boot** (so ari surfaces it once), and is written best-effort (a write failure logs a warning but does not change the exit code — the log channel already carried the identity).

## 6. Placement and Exit-Code Contract

### 6.1 Where the gate sits

Inside `_serve_with_bind_retry` (`daemon/__main__.py:389`), **after** `install_root_logging()` (`:445`) and **before** `bind_socket_with_retry` (`:472-473`):

```
install_root_logging()          # :445 — handlers live
# --- NEW: port-ownership gate ---
self_install_dir = resolve_self_install_dir()   # reuse promote_txn_hold machinery
claims_dir = claims_dir_for(self_install_dir)    # <install_root>/data/port-claims (or data dir fallback)
result = try_claim(claims_dir, host, port, self_install_dir)
if result.kind == "refused":                    # foreign live owner proven
    log.warning("PORT OWNERSHIP REFUSED: port=%s host=%s holder_pid=%s holder_exe=%s holder_cwd=%s holder_install=%s self_install=%s",
                port, host, result.holder.pid, result.holder.exe, result.holder.cwd, result.holder.install_dir, self_install_dir)
    write_refusal_marker(data_dir, result.holder, self_install_dir, port, host)
    sys.exit(78)                                # reuse 78 — never 75, never a new code
if result.kind in ("degraded", "unknown"):
    log.warning("port-ownership gate degraded: %s — proceeding without ownership enforcement", result.reason)
# taken / degraded / unknown ⇒ fall through
sock = bind_socket_with_retry(host, port, max_wait_s=max_wait_s)   # :472-473 — unchanged
```

This satisfies:
- **After both preflights** (auth `:514-520` exits 78 on config refusal; DB preflight exits 75 on PG unreachable) — a config refusal never binds the port, and the ownership gate only runs once config + DB are healthy.
- **Immediately before server start** — the gate is the last thing before the bind.
- **Outside `run_preflight`** — `_serve_with_bind_retry` is called from `main()` at `:754-757` unconditionally, so the frozen entry (`run_app.py → main(run_preflight=False)`) is covered (auth preflight + boot sweep + load_config still run; DB preflight ran in `run_app.py`).
- **Logging is live** — `install_root_logging()` at `:445` precedes the gate, so the marker line reaches journald + the file log (closing the §4.3 observability hole for this path).
- **The gate does not enter the bind-retry loop** — `sys.exit(78)` precedes `bind_socket_with_retry`, so the refusal is never swallowed or re-classified by the exit-1 path. `BIND_FAILURE_EXIT_CODE = 1` (`bind_retry.py:172`) and the ceiling clamp (`:231-233`) are untouched.

### 6.2 Exit-code contract

- Reuse **78** (analysis §5.5). `classify_exit` (`launcher.sh:3587-3595`): `78 → refuse → launcher exits 78, does NOT restart` (`launcher.sh:3814-3816`). `RestartPreventExitStatus=78` (`scripts/systemd/ensemble-daemon.service:110`) keeps systemd from restarting. `SuccessExitStatus=143` (`:111`) is unaffected.
- **75 is wrong** — budget-exempt (`launcher.sh:3821` `_notify_once "tempfail-75"`), producing an unbounded slow retry loop (same class, less visible).
- **A new code is wrong now** — lands in `*→crash`, loops unless `launcher.sh` learns it (sha-pinned, ceremony-critical) AND the unit lists it. Never piecemeal; a dedicated code (~73/79) is a long-term option only as a paired launcher+unit lane (deferred).
- Residual: a port-squat 78 during a promote window reads as a "clean pre-mutation exit-78" — mitigated by the marker line plus the 30 s retry covering normal promote hand-over (§7 row 1).

### 6.3 What this does NOT cover (explicitly)

- **Metrics port 9798 fencing** — routed to component B as rider (d) (env-wins validator or derive 9798 from the resolved main port). Component A's mechanism is generic and *could* be applied to 9798, but the fence shape (env-wins vs derive) and the DB-managed-tier verification (H3) are component B's decision. Component A records the claim for whichever port it is invoked on; the 9798-specific fence lands in B. Rationale: 9798 is a different config class (`LimitsConfig`, `env_prefix="LIMITS_"`) with a DB-managed tier that `PORT` cannot reach (analysis §2.2) — conflating the two would muddy the ownership verdict and the refusal attribution.

## 7. Lane-by-Lane Compatibility Walk

Each sanctioned lane must survive component A. Verdicts per the analysis trip-risk census (verified at tip `cace21473`).

| # | Lane | Verdict | Why |
|---|------|---------|-----|
| 1 | Live boot + promote hand-over window | **SAFE** | Holder `install_dir` == self `install_dir` (same install) ⇒ non-foreign (D6) ⇒ take claim, no refuse; the 30 s bind-retry (`BIND_RETRY_MAX_WAIT_CEILING_S=30.0`, `bind_retry.py:162`) covers the transient EADDRINUSE during hand-over anyway. Same-install ⇒ never refuses (D2). |
| 2 | Promote / restart / rollback chain (`promote.sh:464-499`, `adopt-unit.sh:284,304-306,450-460,579` — PORT read ONLY from staged `.env`) | **SAFE** | Staged resolves to the **live install root** via the frozen-binary path derivation (foot-gun) ⇒ `install_dir` matches live ⇒ non-foreign ⇒ no refuse during hand-over. The deliberate PORT setter (`manager.py:6410-6411`, `extra_env["PORT"]`) is preserved (component A never touches `manager.py`'s promote executor). Claim cleanup on shutdown is best-effort and pid-gated (§5.6), so a promote's old claim does not block the new owner once the old pid is gone. |
| 3 | `dev.sh` boots on 8079 (`dev.sh:141-142`) | **SAFE** | Binds 8079 (≠ 9797) ⇒ a different claim file ⇒ no collision with live's claim. Even on a shared port, dev's `install_dir` resolves to `None` (not staged, not frozen) ⇒ D6 ⇒ non-foreign ⇒ take. Classification rule documented in D6. |
| 4 | Pytest transient-unit boots (`tests/support/wedge_smoke.py`: `transient_unit_argv` `:451-477`, `scratch_env` `:552-586` sets `DAEMON_PORT` — inert under shipped `config.yaml`; `free_port` `:102-109` re-rolls off 9797/7979; cgroup assert `:440-448`) | **SAFE (main port)** | Main port resolves to 8088 (yaml default when `PORT` unset) or a free port — not 9797 ⇒ different claim file ⇒ no collision with live. 9798 collision is anomaly 07e8410c, owned by component B (rider (d)) — not A's concern. Transient boots from source resolve `install_dir=None` ⇒ D6 ⇒ non-foreign even if a port coincided. |
| 5 | Tester staged-binary scratch mirror (`boot_v2.sh`: umask 077 `:3-4`, occupied-port pre-flight `:16-19`, per-var scrub `:22-26`, FAKE_HOME `:30-34`, scratch `ENSEMBLE_CONFIG` `:36-38`, `PORT=18790` `:40`, scratch PG `:42-46`, sweep pin `:48`, `ENSEMBLE_SELF_ENV=sandbox` `:55`) | **SAFE** | Binds 18790 (≠ 9797/9798) ⇒ different claim file ⇒ no collision with live. `ENSEMBLE_SELF_ENV=sandbox` + frozen ⇒ `install_dir` = scratch mirror root (self-consistent) ⇒ no foreign live owner on 18790 ⇒ take claim ⇒ bind succeeds. Per-var scrub (never `env -u POSTGRES_*` glob) is preserved by the mirror. |
| 6 | DR-2 exit-78 drill (`docs/runbooks/upgrade-drills.md:164-191`: `SBX=/tmp/ens-sbx-dr2-$$`, `PORT=8377`, `HOME=$(mktemp -d)`) | **SAFE** | The config-refuse-78 shape still fires: port 8377 has no live holder ⇒ claim absent ⇒ take claim ⇒ bind succeeds (port free) ⇒ then the config refusal (missing binary / bad config) ⇒ exit 78 as before. Ownership gate does not mask it (it only refuses on foreign live owner; a missing claim takes). Write failure (if any, in scratch) ⇒ degrade ⇒ proceed ⇒ config refusal ⇒ 78. |
| 7 | `scripts/test_builtin_mcp_servers.sh` (`PORT=18088` pinned AFTER sourcing repo `.env` `:89-116`) | **SAFE** | Binds 18088 (≠ 9797) ⇒ different claim file. **Precondition:** the `export PORT=$TEST_PORT` line must execute after the `.env` source (verified at tip, ~line 116) — otherwise ambient `PORT=9797` from the sourced `.env` would make it bind 9797 and collide with live. Verified; no change needed. |

## 8. Regression-Pin Skeleton (mutation-discipline compliant)

All pins are **positive shapes**: a counter-mutation must redden each pin; absence-only pins are prose-satisfiable and are forbidden (analysis mutation discipline; exemplars `tests/unit/test_ari_pm_terminology_pins.py` pure checker `:118-150` + mutation legs `:329-422` + non-vacuity control `:409-412`; `tests/bind_retry_mutation_matrix.md` venue discipline `:24-36`; `tests/unit/agents/test_aegis_security_family.py:2527-2564` wrong-pin refusal probe).

### 8.1 (E-ii) — rogue-boot REFUSED in a fenced mirror

**Setup (model on `boot_v2.sh` + `tests/support/wedge_smoke.py`):** scratch port (e.g. 18790 or `free_port()`), per-variable scrubbed env (never `env -u POSTGRES_*` glob), FAKE_HOME with escape guard (`tests/upgrade_test_lib.sh:69-102`), fresh disposable PG (`PG_TEST_*` fencing; `tests/postgres/conftest.py:78-158` G5 forbids `ensemble_prod`/`ensemble_dev`/`ensemble_prod_test` even on scratch clusters), sweep pin 0, `ENSEMBLE_SELF_ENV=sandbox`. The foreign holder is **simulated by planting a claim file** with `install_dir = "foreign-install-root"` (≠ self's), `pid` = a live process pid, `starttime` matching that pid — no real foreign daemon needed (safer; tests the refusal logic, not a cross-daemon race).

**Assertions (positive):**
- The daemon-under-test exits **78** (not 1, not 0).
- The holder-identity marker line appears in the log (journald/file): contains `pid`, `exe`, `cwd`, `install`, `port`.
- The durable refusal-marker file (`data/port-ownership-refusal.json`) is written with the holder identity.
- **NEVER touches live 9797/9798 or `ensemble_prod`** — assert via port probes + DB-name assertion (G5 fence).

**Counter-mutation legs (must redden):**
- M1: flip the planted claim's `install_dir` to equal self's ⇒ same-install ⇒ no refuse ⇒ daemon boots (readyz 200) ⇒ pin reddens.
- M2: set the planted claim's `pid` to a dead pid (or mismatched starttime) ⇒ stale ⇒ no refuse ⇒ pin reddens.
- M3: remove the foreign-ness check entirely ⇒ a same-install holder would now refuse ⇒ cross-pin with 8.3 reddens.

### 8.2 (E-iii slice) — one sanctioned lane boots end-to-end

**Recommended single pin: boot_v2.sh-style full daemon boot on scratch port 18790** (happy path). Justification: it is the closest existing ancestor (already fenced, already a full boot), it exercises the ownership gate on the **take-claim** path (claim absent ⇒ take ⇒ bind succeeds ⇒ daemon reaches readyz), and it proves component A does **not** break normal boots. The sad path (refuse) is covered by 8.1.

**Assertions (positive):**
- Exit code 0 / daemon reaches readyz on 18790 within the budget.
- No `PORT OWNERSHIP REFUSED` marker line.
- Claim file written with `install_dir == self` (self-claim).
- Claim file cleaned up on graceful shutdown (best-effort).

**Counter-mutation legs (must redden):**
- M4: plant a foreign claim on 18790 before boot ⇒ daemon refuses 78 ⇒ happy-path pin reddens (cross with 8.1).
- M5: break the claim-write (e.g. read-only claims dir) ⇒ gate degrades ⇒ daemon still boots (readyz 200) ⇒ pin stays green (degrade-never-refuse is verified) — this leg verifies the degrade path, not a refusal.

### 8.3 Promote-shape non-refuse pin (same-install hand-over does NOT refuse)

**Setup:** plant a claim file with `install_dir == self_install_dir` and a live pid/starttime. Boot the daemon-under-test pointed at that port.

**Assertions (positive):**
- Daemon does **not** exit 78 (exit 0 or readyz 200).
- No refusal marker line / no refusal-marker file.
- Claim is taken (overwritten with self's pid) — non-foreign hand-over.

**Counter-mutation leg (must redden):**
- M6: flip foreign-ness to treat same-install as foreign ⇒ daemon refuses 78 ⇒ pin reddens.

### 8.4 Mutation matrix document

Create `tests/bind_race_port_ownership_mutation_matrix.md` (or append to the existing bind-race matrix) recording: each mutant (M1–M6), the file/line it edits, the expected REDs, the sha256-verified restore procedure (model on `tests/bind_retry_mutation_matrix.md:13-17` venue discipline `:24-36` and B-1 frozen-path pin `:52`), and the non-vacuity control leg (clean tree stays green).

## 9. Task Breakdown (developer-executable, ordered)

| ID | Task | Outcome (testable) | Touchpoints | Depends on | Risks |
|----|------|--------------------|-------------|------------|-------|
| T1 | Create `daemon/services/port_ownership.py` with `PortClaim`, `resolve_self_install_dir`, `read_claim`, `pid_alive_with_starttime`, `is_live_foreign`, `try_claim` (atomic O_EXCL + bounded retry + degrade), `release_claim`, `write_refusal_marker` | Unit-importable module; pure functions testable without a daemon | `daemon/services/port_ownership.py` (new) | — | R2, R3, R4 |
| T2 | Wire the gate into `_serve_with_bind_retry` after `install_root_logging()` and before `bind_socket_with_retry`; `sys.exit(78)` on refused; degrade on write-failure/unknown | Daemon boots normally when no foreign owner; refuses 78 with marker line when foreign claim present | `daemon/__main__.py` (modify, ~15 lines) | T1 | R1, R6 |
| T3 | Add durable refusal-marker file (channel B) + auto-clear on next successful boot | Refusal leaves `data/port-ownership-refusal.json`; next clean boot removes it | `daemon/services/port_ownership.py`; lifespan teardown hook (best-effort) | T2 | R3 |
| T4 | Unit tests for pure logic (liveness, foreign-ness, atomic claim, degrade) | `tests/unit/test_port_ownership.py` green | `tests/unit/test_port_ownership.py` (new) | T1 | — |
| T5 | Fenced-mirror pins (E-ii / E-iii / promote-shape) + mutation matrix | `tests/unit/test_port_ownership_pins.py` (or pack) green; mutation legs red on planted defects | `tests/unit/test_port_ownership_pins.py` (new); `tests/bind_race_port_ownership_mutation_matrix.md` (new) | T1–T3 | — |
| T6 | Docstring cross-reference in `bind_retry.py` noting the ownership gate precedes it (no behavior change) | `daemon/services/bind_retry.py` docstring updated | `daemon/services/bind_retry.py` (docstring only) | T2 | — |
| T7 | Verify zero migration: no new SQLModel table, no `*.sql` in `daemon/migrations/versions/` | `git diff --stat` shows no migration files | — | T1 | (if a table is needed → STOP and flag; changes rollback_safe) |

## 10. Migration Expectation

**ZERO new SQLModel tables expected.** The claim is a filesystem JSON file under the install root's data dir (D7). No `*.sql` migration file is added to `daemon/migrations/versions/` (119 files, zero exceptions — the repo's rule). No change to `rollback_safe` adjudication.

**If your design genuinely needs a DB table** (e.g., for a cross-host claim store), **STOP and flag it to the leader** before proceeding — it changes the rollback_safe adjudication and the ceremony scope.

## 11. Risks

| ID | Risk | Impact | Mitigation |
|----|------|--------|------------|
| R1 | Claim file lives under the install root, which is shared with live via the frozen-binary path-derivation foot-gun ⇒ live + staged share one claims dir | medium (could make staged foreign to live if mishandled) | Foreign-ness is by `install_dir` field, not by claim-file location (D2); staged resolves to live install root ⇒ non-foreign by construction; verified by trip-risk rows 1/2/5 |
| R2 | pid recycle between claim write and check | high (false refuse or false take) | field-22 starttime verification (`pid_alive_with_starttime`); mismatch ⇒ stale ⇒ reclaim |
| R3 | Claim write failure (permission/disk/read-only FS) disables ownership enforcement for that boot | medium (silent loss of protection) | degrade-never-refuse (D1) + WARNING log + refusal-marker write is best-effort; operator visibility via logs (channel A) |
| R4 | Two-boots race (simultaneous boots of different installs) | medium (both take, or one refuses incorrectly) | atomic `O_CREAT\|O_EXCL` + bounded re-read (§5.4); unknown ⇒ retry, never refuse |
| R5 | H1 residual: a rogue in the **same** install dir executing the live's own binary is non-foreign | high (survives) | Declared hole (analysis §8 H1); survives this component; future ownership-scoped reclaim (§4.4) or per-boot identity in the claim is the cure — out of scope here |
| R6 | Promote hand-over: if staged did NOT resolve to the live install root, the new daemon would see a foreign live owner and refuse 78, breaking promote | critical (would break every promote) | Verified: staged resolves to live install root (foot-gun, `upgrade_tools._resolve_install_dir` sandbox branch) ⇒ non-foreign; covered by trip-risk row 2 and promote-shape pin 8.3; pin M6 guards the regression |

## 12. Success Criteria (measurable)

- A daemon pointed at a port held by a foreign live owner (planted claim, foreign `install_dir`, live pid/starttime) exits **78** within boot, emits the holder-identity marker line, and writes the refusal-marker file. (E-ii)
- A daemon in a normal (no foreign owner) boot reaches readyz with **no** refusal marker and writes a self-claim. (E-iii)
- A same-install hand-over (promote shape) does **not** exit 78. (promote-shape pin)
- No `launcher.sh`, unit-template, or live `.env` bytes change (binding principle; verify via `git diff --stat`).
- No new SQLModel table / no `*.sql` migration file (verify via `git status`).
- All 6 mutation legs (M1–M6) redden their pins; the non-vacuity control leg (clean tree green) passes.
- Live 9797/9798 and `ensemble_prod` are untouched by the E-ii test (G5 fence + port probes).

## 13. Leader Decision Points (map to analysis §9)

| Analysis §9 item | This component? | Decision requested | Recommendation |
|---|---|---|---|
| §9.3 — refusal exit code (reuse 78 vs dedicated) | **yes** (D3) | Reuse 78 now? | **Reuse 78** (recommended) — dedicated code only as a paired launcher+unit lane later |
| §9.4 — operator alert channel for the refusal path | **yes** (D8) | Which channel? | **A + B** (logging marker line + durable refusal-marker file) — no new dependency; critical notes/notify_user are not available at the refusal moment |
| (discovered) claim-file location: install-root data dir vs shared host path | yes (§5.1) | Confirm install-root data dir? | **Install-root data dir** — consistent with the foot-gun (live+staged share claims ⇒ non-foreign by construction); a shared host path would diverge from the existing resolution machinery |
| (discovered) `version` field in the claim | yes (§5.2) | Include? | **Yes** — cheap, aids attribution/forensics |
| (discovered) auto-clear refusal marker on next clean boot | yes (§5.7) | Auto-clear? | **Yes** — so ari surfaces it once |
| §9.1 — Lane 4 (burst-window fix) defer/adopt | no (deferred) | — | Defer with trigger (analysis §5.4); touches `launcher.sh` ⇒ own staged lane |
| §9.2 — 9798 fence shape (env-wins vs derive) | no (component B) | — | Component B owns rider (d); component A records claims generically |
| §9.5 — sweep-disarm investigation | no | — | Out of scope |
| §9.6 — frozen-bundle `install_root_logging()` import verify | adjacent (Lane 1) | — | Verify before promoting Lane 1 (analysis §5.1); component A depends on it (D5) |

## 14. Contradictions (commission vs analysis)

1. **Bash-tool pkill-pattern guard (commission candidate shape 1).** The commission brief named a bash-tool pkill-pattern guard as "candidate shape 1." The analysis's chosen set (§5.2/§5.5/§5.6) is **(b)+(c)+(f)+(e′)** and does **not** include a pkill guard — the analysis explicitly routes the spawn-boundary env scrub to component B (§5.3) and rejects (e)-as-policy (§5.6). **This component carries the analysis's choice and does NOT design a pkill guard.** The omission is flagged here for leader disclosure; if the leader wants the pkill guard, it is a separate design item (likely component B or a follow-up), not part of component A. *(This is the known contradiction the commission asked to adjudicate.)*
2. **Anchor drift (non-contradictory, recorded for accuracy):** the commission's anchor map cites `daemon/api.py:2545` for the install-root path derivation. Verified: the resolution machinery lives in `daemon/tools/upgrade_tools.py` (`_resolve_install_dir` `:666`, `_canonical_install_root` `:424`, `_normalized_home` `:379`) and `daemon/services/promote_txn_hold._resolve_install_root` `:140`. The commission cite is approximate (off by ~2000 lines / wrong module). This component **reuses** the existing machinery rather than re-deriving, so the drift is non-load-bearing. Recorded per the "supersedes the analysis's own line cites where drifted" instruction.
3. **Minor anchor drift:** commission cites "exit-3 tail `:490+`" in `__main__.py`; verified the `sock.close()` tail begins ~`:483`. Non-load-bearing.
4. **Placement wording:** the commission says the gate must "state exactly where the ownership verdict sits relative to the `__main__.py:709-713` placement contract." Verified: that contract (preflights first, bind immediately before server start, outside `run_preflight`) is the **comment block at `:709-713`**; the actual `_serve_with_bind_retry` call is at `:754-757`. This component places the gate **inside `_serve_with_bind_retry` after `install_root_logging()` (`:445`) and before `bind_socket_with_retry` (`:472`)** — which is the earliest point at which logging is live and which still precedes the bind, satisfying the contract's intent (gate before bind, covered by both entry paths). Recorded so the developer does not look for the gate at `:709`.

No other contradictions found; all other commission mandates (D1–D9, §5.2/§5.5/§5.6/§6/§8/§9 references) are carried verbatim.

## 15. Assumptions & Open Questions

- **Assumption:** the frozen-binary path derivation (`sys.executable.parent.parent.parent` when `sys.frozen`) continues to resolve staged boots to the live install root. This is the foot-gun the analysis relies on for D2; if a future change fixes it (making staged resolve to its own root), component A's "staged is non-foreign" property would break and H1-style handling would be needed for staged too. Flagged as a coupling to monitor.
- **Assumption:** `install_root_logging()` (Lane 1, merged) runs before the gate so the marker line is visible. Component A depends on Lane 1 being present (it is, at tip `cace21473`).
- **Open question (for the leader, carried):** Should the refusal-marker file be a single global file or per-port (`port-ownership-refusal-<port>.json`)? Recommend per-port to avoid one refusal clobbering another's record; default in §5.7 is single-file for simplicity. **Prefer deciding, but flagging.**
- **Open question:** Should `release_claim` run in the lifespan teardown or in a `atexit`/signal handler? Recommend lifespan teardown (clean path) plus staleness self-healing (crash path) — no signal handler needed. Flagging for developer confirmation.

## 16. Cross-References

- **Governing analysis:** `.agents/shared/planning/bind-race/rogue-port-containment-analysis.md` (FINAL rev 2) — §5.2 (shape), §5.5 (exit 78), §5.6 (rejects), §6 (lane table + binding principle), §8 (H1), §9 (decisions).
- **Component B (sibling worker):** `.agents/shared/planning/rogue-containment/component-B-spawn-boundary-scrub.md` — owns (e′) env scrub at `daemon/tools/bash.py:891-915` and `daemon/tools/service_spawner.py:173-181`, plus rider (d) 9798 fence. Component A does NOT touch those seams.
- **Pins-integration / overview (later worker):** owns sequencing, the overview, and the cross-component pin wiring.
- **Lane 1 (in flight, ship as-is):** `feature/bind-race-fix` — daemon-side bounded bind-retry; component A builds on its placement contract and logging install. Do not bundle.
- **Exemplars for mutation discipline:** `tests/unit/test_ari_pm_terminology_pins.py`, `tests/bind_retry_mutation_matrix.md`, `tests/unit/agents/test_aegis_security_family.py:2527-2564`, `tests/upgrade_test_lib.sh:69-102`, `tests/postgres/conftest.py:78-158`.

---

*End of component A plan. Design only — no code, no tests run, no repo writes except this file and its commit.*
