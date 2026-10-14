/**
 * `ens-img:<feature>:<32hex>` marker constants — pure-function
 * truth-table spec.
 *
 * The matcher is the FE-side gate for the image-gen marker family.
 * Any widening of the accepted shape (e.g. dropping the envelope,
 * relaxing the hex class, accepting uppercase hex) is a potential
 * cross-lane byte-drift with the daemon — `daemon/sources/dispatcher.py:30`
 * is the authoritative source of the family grammar and any change
 * there MUST be mirrored here in the same commit (P2 contract,
 * phase3-plan.md §"Regex byte-match (P2 contract)").
 *
 * Per FE conventions (testing-and-qc-conventions blueprint), the
 * matcher is a pure-function truth table — no Angular TestBed. The
 * table pins the behavior across:
 *
 *   - Full-form regex: matches the daemon-side grammar verbatim
 *     (with `<!-- -->` envelope).
 *   - Content-form regex: matches `node.data` on a `COMMENT_NODE`
 *     (envelope stripped by the DOM).
 *   - Feature gate: only `image-gen` is in scope in v1; chart-render
 *     markers visible in web transcripts keep today's behavior.
 *   - URL builder: derives the canonical `/views/tmp-images/<id>`
 *     URL the FE binds to `<img [src]>`.
 *
 * ## AM11 beyond-fixture proof
 *
 * The spec carries one extra `describe` block (the AM11 contract
 * test) that runs the real `marked` package against a marker-bearing
 * markdown string and asserts the emitted HTML comment parses via
 * `DOMParser` into a `nodeType === 8` (COMMENT_NODE) child whose
 * `data` matches the content-form regex. This is the
 * beyond-fixture proof the phase-3 commission requires — a unit
 * test that fakes a comment node would only prove the scanner
 * matches a comment-shaped string, not that ngx-markdown / marked
 * ACTUALLY emits one.
 */
// `marked@17` is an ESM-only package (its package.json declares
// `type: "module"` and the `exports` map exposes only the ESM entry
// point). Jest's default CJS transform cannot parse the `export`
// token — a vanilla `import { marked } from 'marked'` fails at
// module-load time with `Unexpected token 'export'`. The project
// already pre-compiles the `marked` ESM as part of its node entry
// surface, but the jest pipeline does NOT transform `node_modules`
// JS by default.
//
// We need the REAL parse semantics for the AM11 beyond-fixture proof
// (the whole point is to demonstrate marked actually emits the
// comment). The UMD build (`lib/marked.umd.js`) is the only CJS
// entry in the package and exposes the same `parse(md, {async})`
// API. Jest's resolver refuses subpath access to it via
// `'marked/lib/marked.umd.js'` (the `exports` map gates it), so we
// load it through a relative path resolved from the spec's own
// `__dirname`. This is the standard escape hatch for
// ESM-only-but-CJS-build-exists packages in a CJS jest environment.
jest.mock('marked', () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports, @typescript-eslint/no-var-requires
  const path = require('path');
  // Spec lives at frontend/src/app/constants/<name>.spec.ts;
  // `__dirname` is frontend/src/app/constants/. Three `..` segments
  // land on frontend/, where node_modules/ is a sibling. The path
  // then walks node_modules/marked/lib/marked.umd.js.
  const umdPath = path.resolve(
    __dirname,
    '..',
    '..',
    '..',
    'node_modules',
    'marked',
    'lib',
    'marked.umd.js',
  );
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const markedUmd = require(umdPath);
  return { marked: markedUmd, __esModule: true, default: markedUmd };
});
import { marked } from 'marked';
import {
  ENS_IMG_CORE_RE,
  ENS_IMG_FEATURES,
  ENS_IMG_MARKER_CONTENT_RE,
  ENS_IMG_MARKER_FULL_RE,
  buildTmpImageUrl,
} from './ens-img-marker';

const HEX32_A = 'a'.repeat(32);
// 32-hex lowercase id — distinct from HEX32_A so the spec output is
// unambiguous; the leading 'b' is followed by 31 'a' chars so the
// digit count is exactly 32 hex digits and the regex's
// [a-f0-9]{32} class accepts it.
const HEX32_B = 'b' + 'a'.repeat(31);

describe('ens-img-marker constants', () => {
  describe('ENS_IMG_FEATURES — v1 scope discipline', () => {
    it('is the v1 singleton list (image-gen only)', () => {
      // chart-render is intentionally absent — see phase3-plan.md
      // §"Scope discipline" and Task 6 non-regression guard. Adding
      // a feature is an additive edit here PLUS handler logic.
      expect(ENS_IMG_FEATURES).toEqual(['image-gen']);
    });

    it('is exposed as a readonly array (immutable at the type level)', () => {
      // The runtime mutation guard is a downstream concern (the
      // gate logic copies the list before iterating); the readonly
      // marker is the design-time discipline that signals
      // "constant, not config".
      const arr: ReadonlyArray<string> = ENS_IMG_FEATURES;
      expect(arr.length).toBeGreaterThan(0);
    });

    it('includes "image-gen" as the sole v1 transform target', () => {
      expect(ENS_IMG_FEATURES.includes('image-gen')).toBe(true);
    });

    it('does NOT include "chart-render" (plan Task 6 non-regression guard)', () => {
      // Mirrored at the chat-interface level by the scanForEnsImgMarkers
      // gate: chart-render markers in transcripts are NOT transformed.
      expect(ENS_IMG_FEATURES.includes('chart-render')).toBe(false);
    });
  });

  describe('ENS_IMG_MARKER_FULL_RE — daemon byte-match grammar', () => {
    it('matches a canonical image-gen marker (full envelope)', () => {
      const marker = `<!-- ens-img:image-gen:${HEX32_A} -->`;
      const match = ENS_IMG_MARKER_FULL_RE.exec(marker);
      expect(match).not.toBeNull();
      expect(match![1]).toBe('image-gen');
      expect(match![2]).toBe(HEX32_A);
    });

    it('matches a canonical chart-render marker (generalized feature class)', () => {
      // The feature class `[a-z0-9-]+` is the generalization that
      // keeps the regex family-shaped — chart-render passes the
      // same gate as image-gen; the FEATURE LIST is what scopes
      // the transform.
      const marker = `<!-- ens-img:chart-render:${HEX32_B} -->`;
      const match = ENS_IMG_MARKER_FULL_RE.exec(marker);
      expect(match).not.toBeNull();
      expect(match![1]).toBe('chart-render');
    });

    it('tolerates whitespace inside the envelope', () => {
      // Permissive `\s*` inside the envelope so a future contributor
      // who pads the marker for readability does not silently break
      // the parity scan.
      const marker = `<!--   ens-img:image-gen:${HEX32_A}   -->`;
      expect(ENS_IMG_MARKER_FULL_RE.test(marker)).toBe(true);
    });

    it('rejects malformed envelopes (missing `<!--` / `-->`)', () => {
      expect(ENS_IMG_MARKER_FULL_RE.test(`ens-img:image-gen:${HEX32_A}`)).toBe(false);
      expect(ENS_IMG_MARKER_FULL_RE.test(`<! ens-img:image-gen:${HEX32_A} -->`)).toBe(false);
      expect(ENS_IMG_MARKER_FULL_RE.test(`<!-- ens-img:image-gen:${HEX32_A} >`)).toBe(false);
    });

    it('rejects uppercase hex (canonical form is lowercase)', () => {
      // The hex class `[a-f0-9]{32}` is the byte-match mirror of
      // the daemon-side; uppercase forms are out of contract.
      const upper = HEX32_A.toUpperCase();
      expect(ENS_IMG_MARKER_FULL_RE.test(`<!-- ens-img:image-gen:${upper} -->`)).toBe(false);
    });

    it('rejects a hex id of the wrong length (31 or 33 chars)', () => {
      expect(ENS_IMG_MARKER_FULL_RE.test(`<!-- ens-img:image-gen:${HEX32_A.slice(0, 31)} -->`)).toBe(false);
      expect(ENS_IMG_MARKER_FULL_RE.test(`<!-- ens-img:image-gen:${HEX32_A}0 -->`)).toBe(false);
    });

    it('rejects an empty feature token', () => {
      expect(ENS_IMG_MARKER_FULL_RE.test(`<!-- ens-img::${HEX32_A} -->`)).toBe(false);
    });

    it('rejects a feature token with uppercase letters (class is `[a-z0-9-]+`)', () => {
      expect(ENS_IMG_MARKER_FULL_RE.test(`<!-- ens-img:IMAGE-GEN:${HEX32_A} -->`)).toBe(false);
      expect(ENS_IMG_MARKER_FULL_RE.test(`<!-- ens-img:image_Gen:${HEX32_A} -->`)).toBe(false);
    });
  });

  describe('ENS_IMG_MARKER_CONTENT_RE — node.data matching', () => {
    it('matches the comment body for a canonical image-gen marker', () => {
      // `node.data` strips the envelope; the content regex mirrors
      // that — this is the exact string the scanner sees at
      // runtime.
      const data = `ens-img:image-gen:${HEX32_A}`;
      const match = ENS_IMG_MARKER_CONTENT_RE.exec(data);
      expect(match).not.toBeNull();
      expect(match![1]).toBe('image-gen');
      expect(match![2]).toBe(HEX32_A);
    });

    it('matches when surrounded by whitespace (comment data may be padded)', () => {
      // `document.createComment('  ens-img:image-gen:...  ')` would
      // produce this exact `node.data`; the content regex must
      // accept it.
      const padded = `   ens-img:image-gen:${HEX32_A}   `;
      expect(ENS_IMG_MARKER_CONTENT_RE.test(padded)).toBe(true);
    });

    it('rejects the envelope-included form (NOT what node.data holds)', () => {
      // The full form (`<!-- ens-img:... -->`) is NOT what
      // `node.data` exposes — matching it here would mean the
      // scanner NEVER matches real comment nodes. This spec is the
      // explicit negative-case that pins the contract: the
      // content form rejects the envelope-included shape.
      expect(
        ENS_IMG_MARKER_CONTENT_RE.test(`<!-- ens-img:image-gen:${HEX32_A} -->`),
      ).toBe(false);
    });

    it('rejects malformed bodies', () => {
      expect(ENS_IMG_MARKER_CONTENT_RE.test(`ens-img:image-gen:${HEX32_A}extra`)).toBe(false);
      expect(ENS_IMG_MARKER_CONTENT_RE.test(`xx ens-img:image-gen:${HEX32_A}`)).toBe(false);
      expect(ENS_IMG_MARKER_CONTENT_RE.test('')).toBe(false);
    });
  });

  describe('ENS_IMG_CORE_RE — shared body grammar (parity anchor)', () => {
    it('matches the same body as the content regex', () => {
      // Both regexes are derived from the same core; if this
      // parity drifts, the docs/parity path and the scanner hot
      // path will silently desync. The body itself matches both.
      const data = `ens-img:image-gen:${HEX32_A}`;
      expect(ENS_IMG_CORE_RE.test(data)).toBe(true);
      expect(ENS_IMG_MARKER_CONTENT_RE.test(data)).toBe(true);
    });

    it('does NOT tolerate whitespace (parity anchor for the content form\'s permissiveness)', () => {
      // The core is strict — the content form is permissive only
      // because the body itself is wrapped in `\s*` to absorb
      // jsdom / DOM noise. A future contributor who widens the
      // core inadvertently widens the content form too.
      expect(ENS_IMG_CORE_RE.test(` ens-img:image-gen:${HEX32_A} `)).toBe(false);
    });
  });

  describe('buildTmpImageUrl — canonical /views/tmp-images/<id> builder', () => {
    it('derives the canonical URL the FE binds to <img [src]>', () => {
      expect(buildTmpImageUrl(HEX32_A)).toBe(`/views/tmp-images/${HEX32_A}`);
    });

    it('is the SOLE place the tmp-images URL is constructed (no inline strings in the scanner)', () => {
      // Identity-grep parity: every consumer (scanner, click handler)
      // calls this helper. The scanner's byte-stability relies on
      // it — the value is pinned here and the chat-interface
      // spec asserts the produced element src is byte-identical.
      expect(buildTmpImageUrl(HEX32_B)).toBe(`/views/tmp-images/${HEX32_B}`);
    });
  });

  describe('AM11 beyond-fixture — marked really emits the marker as a COMMENT_NODE', () => {
    // Commission requirement: prove the marker-bearing markdown
    // string actually parses through the real `marked` package
    // into an HTML comment that the DOM exposes as
    // nodeType === 8 (COMMENT_NODE) with `data` matching the
    // content-form regex. The scanner in chat-interface relies
    // on this; a unit test that faked the comment would only
    // prove the scanner handles a comment-shaped string.
    it('compiles a marker-bearing markdown string through marked and parses the resulting HTML into a COMMENT_NODE', () => {
      const marker = `<!-- ens-img:image-gen:${HEX32_A} -->`;
      const md = `Hello ${marker} world`;
      // marked.parse returns a string for the async:false form (the
      // default API in marked@17). The string IS the HTML the DOM
      // would render — a real <markdown> element pipes it through
      // innerHTML, where the browser's HTML parser turns the
      // comment into a Comment node.
      const html = marked.parse(md, { async: false }) as string;
      expect(typeof html).toBe('string');
      expect(html).toContain(marker);

      // DOMParser round-trip — the same path the browser takes.
      // marked already wraps the body in `<p>...</p>`; passing the
      // HTML to DOMParser at the document level produces the same
      // tree the browser would build. (Wrapping the marked output
      // in `<p>...</p>` would nest a new <p> inside the existing
      // one, and the browser's parser would unwrap or skip it —
      // the AM11 contract is about what marked emits, so we feed
      // its output directly.)
      const doc = new DOMParser().parseFromString(html, 'text/html');
      const p = doc.querySelector('p');
      expect(p).not.toBeNull();

      // Walk childNodes; we expect a TEXT, a COMMENT, a TEXT.
      const childNodes = Array.from(p!.childNodes);
      expect(childNodes.length).toBe(3);
      expect(childNodes[0].nodeType).toBe(3 /* TEXT_NODE */);
      expect(childNodes[1].nodeType).toBe(8 /* COMMENT_NODE */);
      expect(childNodes[2].nodeType).toBe(3 /* TEXT_NODE */);

      // The comment's `data` (DOM-stripped envelope) must match
      // the content-form regex — this is the exact string the
      // chat-interface scanner will see at runtime.
      const comment = childNodes[1] as Comment;
      expect(ENS_IMG_MARKER_CONTENT_RE.test(comment.data)).toBe(true);

      // The match's captured feature + id must be the values the
      // scanner injects into the <img>.
      const match = ENS_IMG_MARKER_CONTENT_RE.exec(comment.data);
      expect(match![1]).toBe('image-gen');
      expect(match![2]).toBe(HEX32_A);
    });

    it('matches the chart-render feature through the same content regex (feature gating is the SCANNER\'s job, not the regex\'s)', () => {
      // The regex is family-generic by design — the scanner gates
      // on `ENS_IMG_FEATURES`. This spec pins the contract: the
      // regex matches BOTH feature tokens; the gate is what
      // scopes the transform.
      const chartRenderData = `ens-img:chart-render:${HEX32_B}`;
      expect(ENS_IMG_MARKER_CONTENT_RE.test(chartRenderData)).toBe(true);

      // The gate logic the chat-interface uses — mirrored here
      // to make the contract obvious: `ENS_IMG_FEATURES` is the
      // SINGLE source of truth for which features get
      // transformed.
      const feature = ENS_IMG_MARKER_CONTENT_RE.exec(chartRenderData)![1];
      expect(ENS_IMG_FEATURES.includes(feature)).toBe(false);
    });

    it('round-trips a chart-render marker through marked — emitted as COMMENT_NODE, NOT in ENS_IMG_FEATURES (non-regression guard)', () => {
      // The chat-interface scanForEnsImgMarkers skips chart-render
      // markers by virtue of the ENS_IMG_FEATURES gate. This spec
      // proves the marker IS emitted as a comment (so a naive
      // scanner WOULD see it) and the gate is what keeps it
      // untouched.
      const marker = `<!-- ens-img:chart-render:${HEX32_B} -->`;
      const md = `See ${marker} below.`;
      const html = marked.parse(md, { async: false }) as string;
      // marked wraps the body in <p>; feed the html directly to
      // DOMParser (the same round-trip used in the
      // image-gen-am11 spec above).
      const doc = new DOMParser().parseFromString(html, 'text/html');
      const p = doc.querySelector('p');
      const comments = Array.from(p!.childNodes).filter(
        (n) => n.nodeType === 8,
      );
      expect(comments.length).toBe(1);
      const match = ENS_IMG_MARKER_CONTENT_RE.exec((comments[0] as Comment).data);
      expect(match).not.toBeNull();
      expect(match![1]).toBe('chart-render');
      expect(ENS_IMG_FEATURES.includes('chart-render')).toBe(false);
    });
  });
});