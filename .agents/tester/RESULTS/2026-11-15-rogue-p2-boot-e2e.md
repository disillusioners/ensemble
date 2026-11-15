# Rogue-P2 Gate 5 — Scratch-Port Boot E2E with FULL-HOUSE FENCE + fd-Closerange (2026-11-15)

**Lane:** rogue-containment-p2 tester pass, Gate 5 (boot E2E through the pre-exec fd-closerange wrapper; POK dryrun-fd-inheritance-fence)
**Worker:** worker instance (same lane as Gate 3 — reconstruction reuse)
**Venue:** reconstruction at /home/nea/ensemble-src-wt-rogue-contain, DETACHED HEAD @ a7aec8582, fixed harness (OWNED_RM_DIRS allowlist version ONLY) + scratch-config.yaml — uncommitted, branch untouched (finalize owns the landing; gate-3 disposition stands)
**Status:** ALL 3 SHAPES PASS, fd-closerange ss-verify PASS

## Wrapper provenance (the gate-5 delta)

Harness extension (this lane): `ROGUE_FD_CLOSERANGE=1` generates `$SCRATCH_DIR/fd_closerange_wrapper.py` — `os.closerange(3, 4096)` then `os.execv(<venv python>, [<venv python>, "-m", "daemon"])` — and routes `ROGUE_MIRROR_CMD` through it. fds 0/1/2 preserved (run.log redirect lives on 1/2). execv replaces the image in-place, so the daemon pid IS the wrapper pid (harness pid-tracking + `daemon_descendant_pid` unaffected). Harness echo per run: `[mirror] FD-CLOSERANGE wrapper ON: closerange(3,4096) then execv .venv/bin/python -m daemon`. Scratch-port band guard extended for this pack: 18800–18806.

## Per-shape results

| # | Shape (leg) | Wrapper | rc | Markers | readyz | fd-verify | Verdict |
|---|-------------|---------|----|---------|--------|-----------|---------|
| (i) | happy — sanctioned scratch boot, ALL guards active | closerange(3,4096)+execv | 143 + graceful-log ✓ | ZERO refusal markers; claim taken (pid 441098 = daemon); claim file persisted post-shutdown per shipped B3 design (retained + liveness self-heal — `release_claim` has no daemon callers, port_ownership.py:619-625) | **200** (livez 200) | **PASS** (below) | **PASS** |
| (ii-a) | tier1-name-refusal — prod-DSN rogue | closerange(3,4096)+execv | **78** | `DB RESOLUTION REFUSED (rogue-containment): tier=resolved-dsn-targets-prod db=ensemble_prod self_env=sandbox resolution=explicit` (daemon/ensemble_config.py:346-349) — PRE-TCP (json-only DSN @ closed 127.0.0.1:1) | n/a | n/a (refusal pre-bind) | **PASS** |
| (ii-b) | refuse-foreign — foreign-claim rogue | closerange(3,4096)+execv | **78** | `PORT OWNERSHIP REFUSED: port=18802 … holder_pid=442861 holder_exe=/usr/bin/sleep holder_cwd=/tmp/rogue-mirror-holder-442827 holder_install=foreign-install-root self_install=/home/nea/ensemble-src-wt-rogue-contain … source=claim` (port_ownership.py:624 shape) | n/a | n/a (refusal pre-bind) | **PASS** |

Raw logs: `/tmp/rogue-p2-g5-shape{1,2,3}.log` (mirrored in the rescue pack).

## fd-closerange ss-verify (shape (i), daemon UP @ readyz 200) — VERDICT: scratch pid absent from live ports: **YES**

Raw `ss -ltnp` rows captured by the harness while pid 441098 (the execv'd daemon) was serving:

- Live rows (9797/9798/7979), abbreviated:
  - `LISTEN 0 2048 0.0.0.0:9797 … users:(("ss",pid=441687,…),("postgres",…),("npm exec @upsta",pid=437238,fd=6),("MainThread",pid=305835,fd=6),("ensemble-prod",pid=305455,fd=6))`
  - `LISTEN 0 2048 127.0.0.1:7979 … users:(("ensemble-prod",pid=304612,fd=27))`
  - `LISTEN 0 5 127.0.0.1:9798 … users:(("ensemble-prod",pid=305455,fd=31))`
- Scratch row (positive control):
  - `LISTEN 0 2048 0.0.0.0:18800 … users:(("npm exec @upsta",pid=441487,fd=5),("python",pid=441098,fd=5))`

Assertions (all enforced by the harness, any failure ⇒ leg FAIL):
1. **Scratch pid ABSENT from live rows** ✓ — `pid=441098` appears in NO pid=(…) token of the 9797/7979/9798 rows. The live rows' fd=6 inheritance lists (probe pipeline, postgres children, npm layer, live daemon) demonstrate the ambient inheritance phenomenon itself; the closerange'd boot contributed NOTHING to it.
2. **Positive control ✓** — the scratch row :18800 carries `("python",pid=441098,fd=5)`: the scratch daemon IS its port's binder.
3. Observation (not a leak): pid 441487 (an `npm exec` MCP child OF the scratch daemon) also holds fd=5 on :18800 — same-service fork inheritance of the daemon's OWN listener, within its own tree; the cross-install hazard the fence targets is exactly what did NOT occur.

## Fences applied (full house, every shape)

umask 077 · FAKE_HOME + 4-stage escape guard · per-variable PG scrub (no glob idiom) · ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 explicit · scratch DATA_DIR + SELF_ENV=sandbox (ambient `live` overridden) · ENSEMBLE_PORT_CLAIMS_DIR per-run tmp · ENSEMBLE_DB_AMBIENT_ALLOW=1 on gate-must-pass shapes · ENSEMBLE_CONFIG=generated literal-port scratch config (template-generated, interpolation-grep-refused) · live-untouched ss probes before+after · TERM-only teardown of OWNED processes · OWNED_RM_DIRS ownership guard (never rm HOLD_DIR/WORKTREE) · spawn-seam absolute (harness/venv only). Provenance assert ran before every shape (`daemon.__file__` inside the worktree).

## Live-untouched before/after (whole gate-5 pack)

Before = after, normalized shape (users-columns stripped):
```
LISTEN 0      2048         0.0.0.0:9797       0.0.0.0:*
LISTEN 0      2048       127.0.0.1:7979       0.0.0.0:*
LISTEN 0      5          127.0.0.1:9798       0.0.0.0:*
```
diff empty — live ports identical across all 3 shapes.

## PG cluster lifecycle (disposable @ 127.0.0.1:15433 — NEVER 5432, distinct from gate-3's 15432)

- `initdb -A trust -U roguep2` at /tmp/rogue-p2-g5-pgcluster; pg_ctl start on 127.0.0.1:15433 (listen_addresses pinned; unix_socket_directories → /tmp pre-emptively, gate-3's /var/run lesson applied).
- `createdb ens_e2e_scrub_probe OWNER roguep2` over TCP; PG_TEST_HOST=127.0.0.1 PG_TEST_PORT=15433 PG_TEST_USER=roguep2 per shape.
- Teardown: `pg_ctl -m fast stop` → "server stopped"; TCP probe → connection refused; cluster + sock dirs rm'd; port 15433 freed; zero PG processes remain. ensemble_dev/ensemble_prod never touched.
- RECORDED addendum (POK rogue-cleanup-rider-recorded-addendum): non-commit riders /tmp/rogue-p2-g5-pgcluster (~21MB) + /tmp/rogue-p2-g5-pgsock + 2 PG logs — all rm'd at teardown (proofs above); shape logs PRESERVED in the rescue pack; per-shape harness scratch dirs self-cleaned by the ownership-guarded harness rm.

## Deviations (disclosed)

1. **"Claim cleaned" asserted as the SHIPPED design, not file-absence:** the tree does not release the boot's own claim at shutdown (B3: releasing mid-drain re-opens the claim window; stale claims self-heal via pid+starttime on next boot, port_ownership.py:619-625). Evidence recorded both ways: claim TAKEN while up (pid=daemon) → file PERSISTED post-shutdown with the daemon's pid. If the commission intends actual file cleanup, that is a code change (daemon-side release-on-shutdown), not a harness assert — flagged for the leader.
2. Ports 18800–18802 used of the sanctioned 18800–18806 band; band guard extended accordingly (guard change is part of the harness delta, disclosed).
3. fd-verify ran only on shape (i) per the commission ("while the scratch daemon is up (happy shape)"); refusal shapes exit pre-bind — nothing to probe.
4. Uncommitted disposition maintained (no commits; evidence + harness edits ride the reconstruction + rescue pack `/tmp/rogue-p2-g3-evidence-rescue-20261115T005850Z/`).
