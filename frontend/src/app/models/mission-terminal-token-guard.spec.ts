/**
 * Guard spec — mission-class M1 identifier-rename regression detector.
 *
 * Background
 * ----------
 * Commit 73e7ac4d (mission-class M1) renamed the CSS/class identifier
 *     mission-settled   →   mission-terminal
 * across three identifier-bearing sites:
 *   - mission-liveness-chip.component.html    (class binding)
 *   - mission-liveness-chip.component.scss    (SCSS selector)
 *   - e2e/fe_liveness_chips.spec.ts           (regex alternation)
 *
 * Reviewer F8 and tidier H3 require a guard test so this identifier
 * cannot silently regress. This spec is that guard.
 *
 *
 * Detection rule (chosen + defended)
 * ----------------------------------
 * Flag the CONTIGUOUS literal token `mission-settled` ANYWHERE in any
 * scanned file. This is the pragmatic rule from the FE governance
 * decision: the compound is identifier-shaped (kebab-case with the
 * `mission-` prefix), so any occurrence of that exact compound — in
 * code, comments, or otherwise — should have been reworded when the
 * rename landed.
 *
 * Why not a fully context-sensitive identifier-vs-prose discriminator?
 * Parsing AST, hunting for kebab-case positions inside class bindings /
 * CSS selectors / regex literals, and then proving the discriminator is
 * complete adds complexity the governance team explicitly rejected for
 * the M1 guard. The contiguous-literal rule is the simpler, defendable
 * middle ground: any code-shaped occurrence of `mission-settled`
 * regresses the rename; standalone prose is unaffected (see below).
 *
 *
 * Prose exclusion by design (M3-deferred, NOT in scope)
 * -----------------------------------------------------
 * The standalone word "settled" — and prose phrases like
 *     "Settled cluster", "parent mission settled", "settled value",
 *     "live/settled split", "settled missions render muted"
 * — are intentionally OUT OF SCOPE here. Those are tracked as M3
 * ledger items and explicitly excluded from this guard. A future
 * prose re-anchoring pass (M3) will address them separately.
 *
 * Discriminator property: in normal English prose, the noun "mission"
 * and the verb/past-tense "settled" are separated by punctuation or
 * grammar. The hyphenated compound `mission-settled` is identifier-
 * shaped. The contiguous-literal rule therefore fires only on the
 * identifier-shaped compound, not on prose.
 *
 *
 * Scan scope (bounded, deterministic)
 * -----------------------------------
 *   - frontend/src/ tree, recursively under extensions .ts .html .scss .css
 *   - frontend/e2e/ tree, recursively under extensions .ts .html .scss .css
 *     (the rename touched an e2e spec)
 *
 * Excluded directories (explicit, .gitignore-aware):
 *   node_modules, dist, test-results, playwright-report, e2e-shots, .git.
 *
 * The spec file itself is excluded from its own scan as a defensive
 * belt-and-braces guard against future edits (the token literal is
 * already built by string concat in code below to avoid self-flagging).
 *
 *
 * Why this spec is plain TS (no TestBed)
 * --------------------------------------
 * The scan runs entirely against the filesystem from a Node test
 * process; no Angular bootstrap, no DOM, no fixtures. Matches the
 * project's house style (see app.component.spec.ts:285 and
 * job-card.component.spec.ts:9 — TestBed is reserved for rendered-
 * template assertions only). This guard is a logic-mirror over the
 * repository tree, so a plain-TS spec is the right shape.
 */

/**
 * NOTE on tsconfig.spec.json scope
 * --------------------------------
 * This guard spec is plain-TS, no TestBed (matches the project's
 * house style — see app.component.spec.ts:285 and
 * job-card.component.spec.ts:9). The scan runs against the
 * filesystem from a Node test process; no Angular bootstrap, no DOM.
 *
 * Because tsconfig.spec.json sets `types: ["jest"]` (excluding
 * `@types/node`), strict tsc cannot resolve the `fs` and `path`
 * modules used here. That is by-design: every other FE spec is a
 * pure-logic unit test over TS exports and never touches Node APIs.
 *
 * Per the FE blueprint test-strategy: jest transpile is the binding
 * quality gate for plain-TS specs; tsc is reserved for production
 * code (tsconfig.app.json excludes *.spec.ts entirely). We confirm
 * jest transpile below; the tsc errors on `fs` / `path` here are
 * expected and ignored for this spec.
 */

type DirentLike = { name: string; isFile(): boolean; isDirectory(): boolean };
type FsLike = {
  readdirSync(p: string, opts?: { withFileTypes?: boolean }): string[] | DirentLike[];
  readFileSync(p: string, enc: string): string;
  existsSync(p: string): boolean;
};
type PathLike = {
  resolve(...parts: string[]): string;
  relative(from: string, to: string): string;
  join(...parts: string[]): string;
  dirname(p: string): string;
  extname(p: string): string;
  readonly sep: string;
};
type NodeGlobals = {
  __dirname: string;
  __filename: string;
};
// `require` is the runtime entry point. We type-cast the result so the
// spec body uses the surface area we need; the ambient types above
// cover exactly the calls we make. Jest's runtime injects the real
// @types/node bindings; the cast here only satisfies strict tsc.
const nodeRequire = (eval('require') as NodeRequireShim);
interface NodeRequireShim {
  (id: string): unknown;
}
const fs = nodeRequire('fs') as FsLike;
const path = nodeRequire('path') as PathLike;

// Guard token, built by joining fragments so the guard spec body
// itself does not contain the contiguous literal `mission-settled`
// as a single token. Defensive — keeps the spec honest about what
// it is flagging.
const GUARD_TOKEN = ['mission-', 'settled'].join('');

// The directory of this spec: frontend/src/app/models/
// `__dirname` is provided by Node at runtime; the `declare const` below
// satisfies strict tsc (tsconfig.spec.json's `types: ["jest"]` excludes
// @types/node from the compile scope).
declare const __dirname: string;
declare const __filename: string;
const SPEC_DIR = __dirname;
const SPEC_FILE = __filename;
// The frontend root, three levels above SPEC_DIR.
const FRONTEND_ROOT = path.resolve(SPEC_DIR, '..', '..', '..');

// Scan roots, expressed as absolute paths and human-readable labels.
const SCAN_ROOTS: ReadonlyArray<{ rel: string; abs: string }> = [
  { rel: 'frontend/src', abs: path.join(FRONTEND_ROOT, 'src') },
  { rel: 'frontend/e2e', abs: path.join(FRONTEND_ROOT, 'e2e') },
];

// File extensions scanned (deterministic — no globs over binary/built output).
const SCAN_EXTS = new Set(['.ts', '.html', '.scss', '.css']);

// Directories never descended into. Matches .gitignore + the dist/output
// directories Angular and Playwright produce.
const EXCLUDED_DIR_NAMES = new Set([
  'node_modules',
  'dist',
  'test-results',
  'playwright-report',
  'e2e-shots',
  '.git',
]);

interface Hit {
  /** Path relative to repo root, forward-slash separated. */
  file: string;
  /** 1-based line number within the file. */
  line: number;
  /** The offending line, trimmed of leading/trailing whitespace. */
  content: string;
}

/**
 * Walk a directory tree and collect every line that contains the
 * contiguous guard token. Skips excluded directories and non-scanned
 * extensions. Tolerates unreadable files (e.g. sockets, permission
 * errors) by skipping silently — a guard spec that crashes on a
 * stray socket in the worktree defeats its own purpose.
 */
function walk(dir: string, hits: Hit[]): void {
  let entries: DirentLike[];
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true }) as DirentLike[];
  } catch {
    // Unreadable directory — skip silently.
    return;
  }
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (EXCLUDED_DIR_NAMES.has(entry.name)) continue;
      walk(full, hits);
      continue;
    }
    if (!entry.isFile()) continue;
    const ext = path.extname(entry.name);
    if (!SCAN_EXTS.has(ext)) continue;
    // Defensive: never scan the guard spec itself.
    if (full === SPEC_FILE) continue;
    let content: string;
    try {
      content = fs.readFileSync(full, 'utf8');
    } catch {
      continue;
    }
    const lines = content.split(/\r?\n/);
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (line.indexOf(GUARD_TOKEN) !== -1) {
        // Path relative to FRONTEND_ROOT (e.g. "src/app/.../file.scss"),
        // forward-slash separated for human readability, prefixed with
        // "frontend/" so the report matches the project-root convention
        // used by all sibling tooling.
        const rel = path
          .relative(FRONTEND_ROOT, full)
          .split(path.sep)
          .join('/');
        hits.push({
          file: `frontend/${rel}`.replace(/\/+/g, '/'),
          line: i + 1,
          content: line.trim(),
        });
      }
    }
  }
}

/** Resolve the repo root by walking up until `.git` is found. */
function findRepoRoot(start: string): string {
  let cur = path.resolve(start);
  for (let i = 0; i < 10; i++) {
    if (fs.existsSync(path.join(cur, '.git'))) return cur;
    const parent = path.dirname(cur);
    if (parent === cur) break;
    cur = parent;
  }
  return path.resolve(start); // fallback: absolute
}

describe('M1 mission-settled identifier-rename guard', () => {
  it('does not regress: zero occurrences of the pre-rename identifier in frontend/', () => {
    // Sanity check: assert the guard token is what we say it is.
    // This is a self-check on the spec itself; if it ever fails, the
    // join above is wrong and the scan would be scanning the wrong token.
    expect(GUARD_TOKEN).toBe('mission-settled');
    expect(GUARD_TOKEN.length).toBe('mission-'.length + 'settled'.length);

    const hits: Hit[] = [];
    for (const root of SCAN_ROOTS) {
      if (!fs.existsSync(root.abs)) {
        // Tolerate a missing scan root (e.g. e2e/ pruned) — the test
        // cannot flag what isn't there.
        continue;
      }
      walk(root.abs, hits);
    }

    // Touch findRepoRoot so its definition is not flagged as unused by
    // a future strict-mode tsc run; the helper is kept for clarity and
    // future use (e.g. if scan reports need repo-relative paths).
    const _repoRoot = findRepoRoot(SPEC_DIR);
    expect(_repoRoot).toBeTruthy();

    if (hits.length > 0) {
      const formatted = hits
        .map((h) => `    ${h.file}:${h.line}: ${h.content}`)
        .join('\n');
      throw new Error(
        `Found ${hits.length} occurrence(s) of the pre-rename identifier ` +
          `\`mission-settled\` in frontend/. This is a regression of ` +
          `mission-class M1 (commit 73e7ac4d). The identifier was ` +
          `renamed to \`mission-terminal\`. Re-anchor the following ` +
          `site(s):\n${formatted}\n`,
      );
    }
    expect(hits).toEqual([]);
  });
});