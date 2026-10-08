/**
 * Live-view URL matcher — pure-function truth-table spec.
 *
 * The matcher is the security-critical gate for the live-view
 * Phase 2 feature. ANY widening of the accepted shape is a
 * potential XSS / phishing vector (an attacker who can get a
 * chip into a chat message can get a sandboxed iframe to load
 * content of their choosing). The truth table below pins the
 * behavior across the COMPLETE bypass matrix:
 *
 *   - The legal-shape set (matches every URL the daemon
 *     ``view_link`` tool actually mints).
 *   - The bypass set: absolute URLs, protocol-relative URLs,
 *     wrong-shape paths, traversal payloads, percent-encoded
 *     payloads, query / fragment payloads, control chars,
 *     backslash payloads, empty / non-string inputs.
 *
 * Per FE conventions (testing-and-qc-conventions blueprint), the
 * matcher is a pure function with no Angular TestBed. The truth
 * table IS the spec — a future contributor who widens the
 * accepted shape will trip the negative-case tests immediately.
 */
import {
  isLiveViewUrl,
  parseLiveViewUrl,
  deriveLiveViewTitle,
  LIVE_VIEW_URL_PREFIX,
} from './live-view-url';

describe('live-view-url constants', () => {
  describe('LIVE_VIEW_URL_PREFIX', () => {
    it('is the canonical /views/ prefix with a trailing slash', () => {
      // The trailing slash is load-bearing — it is the difference
      // between /views/planning (legal) and /viewsfoo/... (rejected).
      expect(LIVE_VIEW_URL_PREFIX).toBe('/views/');
      expect(LIVE_VIEW_URL_PREFIX.endsWith('/')).toBe(true);
    });
  });
});

describe('isLiveViewUrl — legal-shape matrix (positive cases)', () => {
  const legal: string[] = [
    // The canonical Phase 1 root shapes — the URLs ``view_link``
    // actually mints today.
    '/views/planning/ens/feat/plan.md',
    '/views/planning/ens/feat/design/mockups/landing.html',
    '/views/designer-artifact/ens/feat/design/mockups/landing.html',
    '/views/tmp-images/abcdef0123456789abcdef0123456789',
    // Single-segment rel paths.
    '/views/planning/ens/plan.md',
    '/views/tmp-images/1234567890abcdef1234567890abcdef',
    // Hyphenated root names with multi-segment rel paths.
    '/views/designer-artifact/my-project/sub/dir/file.html',
    // Short root + nested path.
    '/views/planning/p/x.md',
  ];

  for (const href of legal) {
    it(`accepts ${href}`, () => {
      expect(isLiveViewUrl(href)).toBe(true);
    });
  }
});

describe('isLiveViewUrl — bypass matrix (negative cases; task spec §LINK HYGIENE)', () => {
  // EVERY input in this list MUST be rejected. The task spec
  // enumerates these as the explicit bypass vectors a malicious
  // agent (or a misconfigured operator) could try.
  const rejected: ReadonlyArray<{ href: unknown; reason: string }> = [
    // Wrong-prefix variants.
    { href: '/api/views/x/y', reason: 'wrong prefix (api/views/...)' },
    { href: '/view/x/y', reason: 'truncated prefix (singular /view/)' },
    { href: '/views', reason: 'prefix without trailing slash' },
    { href: '/views/', reason: 'prefix with empty remainder' },
    { href: '/viewsfoo/x/y', reason: 'prefix /views without separator' },
    { href: '/viewsbar/x', reason: 'prefix /views without separator (variant)' },
    { href: '/something/views/x/y', reason: 'views is not at the start' },
    { href: 'views/x/y', reason: 'leading slash missing' },
    { href: './views/x/y', reason: 'relative dot prefix' },
    { href: '../views/x/y', reason: 'relative-dotdot prefix' },
    // Absolute / other-origin URLs.
    { href: 'http://evil/views/x/y', reason: 'absolute http (other origin)' },
    { href: 'https://evil/views/x/y', reason: 'absolute https (other origin)' },
    { href: 'https://example.com/views/planning/ens/plan.md', reason: 'absolute URL pointing at a real-shaped /views/ path' },
    { href: 'https://localhost:8079/views/x/y', reason: 'absolute URL to the local daemon' },
    { href: 'ftp://example.com/views/x/y', reason: 'non-http scheme' },
    { href: 'javascript:alert(1)', reason: 'javascript: scheme' },
    { href: 'data:text/html,<script>alert(1)</script>', reason: 'data: scheme' },
    // Protocol-relative URLs.
    { href: '//host/views/x/y', reason: 'protocol-relative' },
    { href: '//evil.com/views/planning/ens/plan.md', reason: 'protocol-relative with real shape' },
    // Traversal payloads.
    { href: '/views/../etc/passwd', reason: 'traversal via .. segment' },
    { href: '/views/planning/../../etc/passwd', reason: 'traversal via .. after root' },
    { href: '/views/planning/ens/../../plan.md', reason: 'traversal in nested path' },
    { href: '/views/planning/ens/foo/../bar.md', reason: 'traversal mid-path' },
    // Percent-encoded payloads (the daemon also rejects these,
    // but the FE must reject upfront so a chip is never offered).
    { href: '/views/%2e%2e/etc/passwd', reason: 'percent-encoded ..' },
    { href: '/views/planning/ens/%2e%2e/plan.md', reason: 'percent-encoded .. after root' },
    { href: '/views/%2fplanning%2fens%2fplan.md', reason: 'percent-encoded slashes' },
    { href: '/views/planning%00ens/plan.md', reason: 'null byte injection' },
    // Query / fragment payloads.
    { href: '/views/planning/ens/plan.md?foo=bar', reason: 'query string' },
    { href: '/views/planning/ens/plan.md#section', reason: 'fragment' },
    { href: '/views/planning/ens/plan.md?callback=alert(1)', reason: 'callback-style XSS attempt' },
    // Backslash payloads (Windows-style paths).
    { href: '/views/planning\\ens\\plan.md', reason: 'backslash separator' },
    { href: '/views/..\\..\\evil', reason: 'backslash traversal' },
    // Empty / whitespace / control chars.
    { href: '', reason: 'empty string' },
    { href: '   ', reason: 'whitespace' },
    { href: '/views /planning/ens/plan.md', reason: 'space in URL' },
    { href: '/views/planning/ens/plan.md\n', reason: 'trailing newline' },
    { href: '/views/planning/ens/plan\tmd', reason: 'tab in URL' },
    { href: '/views/planning/\rens/plan.md', reason: 'carriage return' },
    // Bad root name.
    { href: '/views//ens/plan.md', reason: 'empty root' },
    { href: '/views/Planning/ens/plan.md', reason: 'uppercase root' },
    { href: '/views/PLAN/ens/plan.md', reason: 'all-uppercase root' },
    { href: '/views/123abc/ens/plan.md', reason: 'root starting with digit' },
    { href: '/views/-foo/x', reason: 'root starting with hyphen' },
    { href: '/views/foo_bar/x', reason: 'root with underscore (not kebab-case)' },
    { href: '/views/foo.bar/x', reason: 'root with dot' },
    { href: '/views/foo..bar/x', reason: 'root with consecutive dots' },
    { href: '/views/' + 'a'.repeat(64) + '/x', reason: 'root over 63 chars' },
    // Empty rel path.
    { href: '/views/planning', reason: 'root with no rel path' },
    { href: '/views/planning/', reason: 'root with empty rel path' },
    { href: '/views/planning//x', reason: 'double slash in rel path' },
    { href: '/views/planning/ens/.', reason: 'dot segment in rel path' },
    // Non-string inputs.
    { href: null, reason: 'null' },
    { href: undefined, reason: 'undefined' },
    { href: 42, reason: 'number' },
    { href: {}, reason: 'object' },
    { href: [], reason: 'array' },
    { href: true, reason: 'boolean' },
  ];

  for (const { href, reason } of rejected) {
    it(`rejects ${typeof href} ${JSON.stringify(href)} (${reason})`, () => {
      expect(isLiveViewUrl(href)).toBe(false);
    });
  }
});

describe('parseLiveViewUrl — structural contract', () => {
  it('returns the parsed shape for a legal URL', () => {
    const result = parseLiveViewUrl(
      '/views/planning/ens/feat/design/mockups/landing.html',
    );
    expect(result).not.toBeNull();
    expect(result!.href).toBe(
      '/views/planning/ens/feat/design/mockups/landing.html',
    );
    expect(result!.root).toBe('planning');
    expect(result!.relPath).toBe('ens/feat/design/mockups/landing.html');
  });

  it('strips the leading /views/<root>/ from the relPath', () => {
    const result = parseLiveViewUrl('/views/tmp-images/abcdef0123456789abcdef0123456789');
    expect(result).not.toBeNull();
    expect(result!.root).toBe('tmp-images');
    expect(result!.relPath).toBe('abcdef0123456789abcdef0123456789');
  });

  it('returns null for a non-string input (defensive)', () => {
    expect(parseLiveViewUrl(null)).toBeNull();
    expect(parseLiveViewUrl(undefined)).toBeNull();
    expect(parseLiveViewUrl(42)).toBeNull();
    expect(parseLiveViewUrl({})).toBeNull();
    expect(parseLiveViewUrl([])).toBeNull();
  });
});

describe('deriveLiveViewTitle — chip label', () => {
  it('uses the final rel-path segment (file name) as the label', () => {
    expect(
      deriveLiveViewTitle(
        parseLiveViewUrl('/views/planning/ens/plan.md')!,
      ),
    ).toBe('plan');
  });

  it('strips a .html extension from the label', () => {
    expect(
      deriveLiveViewTitle(
        parseLiveViewUrl(
          '/views/designer-artifact/ens/feat/design/mockups/landing.html',
        )!,
      ),
    ).toBe('landing');
  });

  it('strips a .htm extension from the label', () => {
    expect(
      deriveLiveViewTitle(
        parseLiveViewUrl('/views/planning/ens/page.htm')!,
      ),
    ).toBe('page');
  });

  it('strips a .md extension from the label', () => {
    expect(
      deriveLiveViewTitle(
        parseLiveViewUrl('/views/planning/ens/notes.md')!,
      ),
    ).toBe('notes');
  });

  it('strips a .png/.jpg/.gif/.svg/.webp extension from the label', () => {
    expect(
      deriveLiveViewTitle(
        parseLiveViewUrl('/views/tmp-images/abcdef0123456789abcdef01234567.png')!,
      ),
    ).toBe('abcdef0123456789abcdef01234567');
  });

  it('keeps unknown extensions verbatim', () => {
    // A .txt artifact is still a real Phase-1 legal shape; the
    // label rules only strip a fixed allowlist of common web /
    // markdown extensions.
    expect(
      deriveLiveViewTitle(
        parseLiveViewUrl('/views/planning/ens/notes.txt')!,
      ),
    ).toBe('notes.txt');
  });

  it('falls back to the raw last segment if the extension strip empties it', () => {
    // A URL ending in ``.html`` whose basename is also ``.html`` is
    // not a thing — but the fallback guards against any
    // hypothetical edge case where the strip yields an empty
    // string. The raw last segment is preferred over returning
    // ``""``.
    expect(
      deriveLiveViewTitle(
        parseLiveViewUrl('/views/planning/ens/.html')!,
      ),
    ).toBe('.html');
  });
});
