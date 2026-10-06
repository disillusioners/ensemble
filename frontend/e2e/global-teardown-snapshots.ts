/**
 * Snapshots Page e2e globalTeardown — race-loser backstop.
 *
 * Mirrors `frontend/e2e/global-teardown-maintenance.ts` line-for-line;
 * only the port pair (daemon 18279 / PG 15532), the pg-cluster dir
 * prefix (`pg_e2e_snap_`), the log dir (`/tmp/e2e_snapshots_logs`),
 * and the repo data dir (`data_e2e_snapshots`) differ.
 *
 * Playwright's webServer teardown sends signals to the spawned
 * process, but the boot script's TERM/EXIT traps can race with
 * Playwright's force-kill (especially on the second-daemon path
 * where the bootstrap script needs >5s to run pg_ctl stop +
 * rm -rf). This teardown is a deterministic backstop:
 *
 *   1. Kill the e2e daemon (pid file provenance:
 *      `frontend/scripts/boot-e2e-snapshots-daemon.sh` writes
 *      `$LOG_DIR/daemon.pid` from the daemon's `$!`).
 *   2. Read the most-recent /tmp/pg_e2e_snap_* dir (the boot
 *      script's `DATA_DIR` was random + canonical; we scan
 *      postmaster.pid to identify it).
 *   3. If PG is still listening on 15532 with that data dir, call
 *      `pg_ctl stop` directly.
 *   4. Remove the pg cluster dir + `data_e2e_snapshots/` if they
 *      still exist.
 *
 * SAFETY SHAPE (mirrors the maintenance backstop): the daemon is
 * killed ONLY by the pid file the boot script wrote — never by
 * port. PG is stopped ONLY via the discovered `pg_e2e_snap_*` data
 * dir. Strict e2e port pair is 18279/15532 — this teardown NEVER
 * kills by, or even probes, the dev/live ports (8088/8079/9797/7979)
 * or anything outside that pair. Idempotent — safe to run alongside
 * the boot script's own cleanup. The boot script's cleanup is the
 * primary path; this is the backstop when SIGKILL races SIGTERM in
 * Playwright's webServer teardown.
 */

import { execSync } from 'child_process';
import { existsSync, readdirSync, readFileSync, rmSync } from 'fs';
import { join } from 'path';

const LOG_DIR = '/tmp/e2e_snapshots_logs';
const PG_PORT = 15532;
const PG_DIR_PREFIX = 'pg_e2e_snap_';
const REPO_ROOT = join(__dirname, '..', '..');

function safeExec(cmd: string): string {
  try {
    return execSync(cmd, { stdio: ['ignore', 'pipe', 'ignore'], timeout: 30_000 })
      .toString()
      .trim();
  } catch {
    return '';
  }
}

export default async function globalTeardown(): Promise<void> {
  // 1. Kill the e2e daemon. Idempotent: missing pid file / dead PID
  //    / non-numeric pid = silent no-op. Foreign-safe: only kills the
  //    PID the boot script wrote — never touches anything by port.
  //    SIGTERM only (the boot script invokes uvicorn with
  //    `--timeout-graceful-shutdown 10`; the daemon's lifespan
  //    handler drains on SIGTERM). No SIGKILL escalation — boot
  //    script's TERM/INT/EXIT trap is the SIGKILL-of-last-resort.
  const daemonPidFile = join(LOG_DIR, 'daemon.pid');
  if (existsSync(daemonPidFile)) {
    const pid = parseInt(readFileSync(daemonPidFile, 'utf8').trim(), 10);
    if (Number.isFinite(pid) && pid > 0) {
      try { process.kill(pid, 'SIGTERM'); } catch { /* already dead */ }
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    try { rmSync(daemonPidFile, { force: true }); } catch { /* best-effort */ }
  }

  // 2. Discover the most-recent PG cluster dir (snapshots prefix
  //    ONLY — never a foreign cluster dir).
  let pgDataDir: string | null = null;
  if (existsSync(LOG_DIR)) {
    const candidates = readdirSync('/tmp')
      .filter((d) => d.startsWith(PG_DIR_PREFIX))
      .map((d) => join('/tmp', d))
      .filter((d) => existsSync(join(d, 'postmaster.pid')));
    if (candidates.length > 0) {
      // Use the most-recently-modified candidate.
      candidates.sort((a, b) => {
        const sa = safeExec(`stat -f %m ${a}`);
        const sb = safeExec(`stat -f %m ${b}`);
        return Number(sb) - Number(sa);
      });
      pgDataDir = candidates[0];
    }
  }

  // 3. If PG is listening on 15532 with that data dir, stop it.
  const portCheck = safeExec(`pg_isready -h 127.0.0.1 -p ${PG_PORT}`);
  if (portCheck.includes('accepting') && pgDataDir) {
    safeExec(`pg_ctl -D ${pgDataDir} stop`);
  }

  // 4. Remove leftover cluster dir + repo data_e2e_snapshots/.
  if (pgDataDir && existsSync(pgDataDir)) {
    try {
      rmSync(pgDataDir, { recursive: true, force: true });
    } catch {
      /* best-effort */
    }
  }
  const dataDirE2E = join(REPO_ROOT, 'data_e2e_snapshots');
  if (existsSync(dataDirE2E)) {
    try {
      rmSync(dataDirE2E, { recursive: true, force: true });
    } catch {
      /* best-effort */
    }
  }
}
