// The SINGLE-ENGINE gate (T1 phase 4, task 4.12).
//
// The consolidation's promise: exactly ONE house SVG engine remains in
// the tree. The assertion is a MIGRATION gate, not an engine property —
// it can only pass once the two superseded engines are deleted, so it
// lives in its own spec.

import { existsSync, readdirSync, statSync } from 'fs';
import { join } from 'path';

const SRC_ROOT = join(__dirname, '..', '..', '..', '..');

describe('the house SVG engine — the single-engine gate (task 4.12)', () => {
  it('is the ONLY SVG chart engine in the tree', () => {
    const offenders: string[] = [];
    const walk = (dir: string): void => {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const full = join(dir, entry.name);
        if (entry.isDirectory()) {
          walk(full);
        } else if (entry.name.endsWith('.ts') && full.includes('charts')) {
          if (statSync(full).isFile() && /svg-(line-chart|timeline)/.test(full)) {
            offenders.push(full.replace(SRC_ROOT, 'frontend/src'));
          }
        }
      }
    };
    walk(join(SRC_ROOT, 'app'));
    expect(offenders).toEqual([]);
  });

  it('the retired out-of-tree wrapper path holds ZERO orphan files', () => {
    const retired = join(SRC_ROOT, 'app', 'components', 'charts', 'chart');
    // The path was NEVER created — the sweep is a no-op that still
    // asserts the count (a missing path is zero orphan files).
    const files = existsSync(retired)
      ? readdirSync(retired, { recursive: true, withFileTypes: true }).filter((entry) =>
          entry.isFile(),
        ).length
      : 0;
    expect(files).toBe(0);
  });
});
