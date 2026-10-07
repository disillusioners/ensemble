/**
 * Runtime-string fixture generator for the vendored OD prompt modules.
 *
 * Produces the FOUR reference strings the Mode-P compose chain embeds:
 *
 *   DISCOVERY_AND_PHILOSOPHY   (contracts/discovery.ts)
 *   OFFICIAL_DESIGNER_PROMPT   (contracts/official-system.ts)
 *   DECK_FRAMEWORK_DIRECTIVE   (contracts/deck-framework.ts)
 *   MEDIA_GENERATION_CONTRACT  (contracts/media-contract.ts)
 *
 * as the RUNTIME VALUES TypeScript/Node evaluates the pinned vendored
 * sources to — template-literal escapes resolved (`\``, `\\`, `\n`, …)
 * and `${…}` substitutions evaluated (sibling consts, cross-module
 * imports, `renderDirectionSpecBlock()`, `JSON.stringify(...)`,
 * `renderDeckFrameworkDirective('filesystem')` composition).
 *
 * Method (offline, no network, no daemon boot — a pure function
 * evaluation):
 *
 *   1. Assemble a throwaway eval tree in a temp dir from the committed
 *      vendored sources:
 *        - plugins/opendesign/snapshot_with_drift_alarm/prompts/contracts/*.ts
 *        - plugins/opendesign/snapshot_with_drift_alarm/runtime/deck-protocol.ts
 *      Import specifiers are rewritten mechanically (`.js'` → `.ts'`)
 *      because Node's ESM resolver requires the real file extension for
 *      type-stripped modules; the rewrite touches ONLY the eval-tree
 *      copies, never the vendored bytes.
 *   2. Import the modules under Node's TypeScript type stripping and
 *      export the four runtime strings verbatim.
 *   3. Write `<SYMBOL>.txt` fixtures next to this script and print the
 *      byte sizes (the report evidence).
 *
 * Usage (from the repo root):
 *
 *   node tests/unit/plugin_subsystem/fixtures/od_prompt_runtime/generate_fixtures.mjs
 *
 * Evidence (regenerate and diff — must be byte-stable while the
 * snapshot pin stays at open-design-v0.23.0):
 *
 *   node v22.23.2
 *   DISCOVERY_AND_PHILOSOPHY   35652
 *   OFFICIAL_DESIGNER_PROMPT   12631
 *   DECK_FRAMEWORK_DIRECTIVE   29955
 *   MEDIA_GENERATION_CONTRACT   9541
 *
 * The pytest suite asserts the PYTHON extractor's output equals these
 * fixtures byte-for-byte (test_opendesign_prompt_extraction.py). If a
 * sync-runner pull lands upstream prompt changes, regenerate this
 * fixture set in the same change and update the mirrored evaluator.
 */

import { mkdtempSync, mkdirSync, copyFileSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
// Repo root = 5 levels up from tests/unit/plugin_subsystem/fixtures/od_prompt_runtime/
const REPO_ROOT = resolve(HERE, '..', '..', '..', '..', '..');
const SNAPSHOT = join(REPO_ROOT, 'plugins', 'opendesign', 'snapshot_with_drift_alarm');

const CONTRACT_FILES = [
  'discovery.ts',
  'directions.ts',
  'official-system.ts',
  'deck-framework.ts',
  'media-contract.ts',
];
const RUNTIME_FILES = ['deck-protocol.ts'];

// Target symbol per source module (the compose chain's extraction targets).
const TARGETS = [
  ['discovery.ts', 'DISCOVERY_AND_PHILOSOPHY'],
  ['official-system.ts', 'OFFICIAL_DESIGNER_PROMPT'],
  ['deck-framework.ts', 'DECK_FRAMEWORK_DIRECTIVE'],
  ['media-contract.ts', 'MEDIA_GENERATION_CONTRACT'],
];

const work = mkdtempSync(join(tmpdir(), 'od-prompt-fixtures-'));
try {
  mkdirSync(join(work, 'contracts'), { recursive: true });
  mkdirSync(join(work, 'runtime'), { recursive: true });

  // Mechanical import-specifier rewrite for the eval-tree copies only.
  const rewrite = (text) =>
    text.replace(/from '(\.[^']*?)\.js'/g, "from '$1.ts'");

  for (const name of CONTRACT_FILES) {
    const src = readFileSync(join(SNAPSHOT, 'prompts', 'contracts', name), 'utf-8');
    writeFileSync(join(work, 'contracts', name), rewrite(src), 'utf-8');
  }
  for (const name of RUNTIME_FILES) {
    copyFileSync(join(SNAPSHOT, 'runtime', name), join(work, 'runtime', name));
  }

  // The entry module imports each target module and dumps the symbol.
  const entryLines = [];
  for (const [file, symbol] of TARGETS) {
    entryLines.push(
      `import { ${symbol} } from './contracts/${file}';`,
    );
  }
  entryLines.push(
    `const out = {`,
    ...TARGETS.map(([, symbol]) => `  ${symbol},`),
    `};`,
    `for (const [sym, val] of Object.entries(out)) {`,
    `  if (typeof val !== 'string') throw new Error(sym + ' is ' + typeof val);`,
    `  writeFileSync(join(HERE, sym + '.txt'), val, 'utf-8');`,
    `  console.log(sym + '\\t' + Buffer.byteLength(val, 'utf-8'));`,
    `}`,
  );
  const entry = join(work, 'eval-entry.mjs');
  writeFileSync(
    entry,
    [
      `import { writeFileSync } from 'node:fs';`,
      `import { join, dirname } from 'node:path';`,
      `const HERE = ${JSON.stringify(HERE)};`,
      ...entryLines,
      ``,
    ].join('\n'),
    'utf-8',
  );

  await import(pathToFileURL(entry).href);
} finally {
  rmSync(work, { recursive: true, force: true });
}
