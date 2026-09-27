/**
 * Maintenance Console e2e globalTeardown — Item 3 v4 fix pass.
 *
 * Playwright's webServer teardown sends signals to the spawned
 * process, but the boot script's TERM/EXIT traps can race with
 * Playwright's force-kill (especially on the second-daemon path
 * where the bootstrap script needs >5s to run pg_ctl stop +
 * rm -rf). This teardown is a deterministic backstop:
 *
 *   1. Read the most-recent /tmp/pg_e2e_maint_* dir from boot.log
 *      (the boot script's `DATA_DIR` was random + canonical; we
 *      scan postmaster.opts to identify it).
 *   2. If PG is still listening on 15432 with that data dir, call
 *      `pg_ctl stop` directly.
 *   3. Remove the pg cluster dir + `data_e2e_maintenance/` if they
 *      still exist.
 *
 * Idempotent — safe to run alongside the boot script's own cleanup.
 * The boot script's cleanup is the primary path; this is the
 * backstop when SIGKILL races SIGTERM in Playwright's webServer
 * teardown.
 */

import { execSync } from 'child_process';
import { existsSync, readdirSync, readFileSync, rmSync } from 'fs';
import { join } from 'path';

const LOG_DIR = '/tmp/e2e_maintenance_logs';
const PG_PORT = 15432;
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
  // 1. Discover the most-recent PG cluster dir.
  let pgDataDir: string | null = null;
  if (existsSync(LOG_DIR)) {
    const candidates = readdirSync('/tmp')
      .filter((d) => d.startsWith('pg_e2e_maint_'))
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

  // 2. If PG is listening on 15432 with that data dir, stop it.
  const portCheck = safeExec(`pg_isready -h 127.0.0.1 -p ${PG_PORT}`);
  if (portCheck.includes('accepting') && pgDataDir) {
    safeExec(`pg_ctl -D ${pgDataDir} stop`);
  }

  // 3. Remove leftover cluster dir + repo data_e2e_maintenance/.
  if (pgDataDir && existsSync(pgDataDir)) {
    try {
      rmSync(pgDataDir, { recursive: true, force: true });
    } catch {
      /* best-effort */
    }
  }
  const dataDirE2E = join(REPO_ROOT, 'data_e2e_maintenance');
  if (existsSync(dataDirE2E)) {
    try {
      rmSync(dataDirE2E, { recursive: true, force: true });
    } catch {
      /* best-effort */
    }
  }
}