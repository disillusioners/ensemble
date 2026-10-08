/**
 * Live-view URL matcher — single source of truth for the FE-side
 * recognition of `/views/<root>/<rel>` URLs that should render as
 * artifact chips/embeds instead of as raw markdown links.
 *
 * The matcher is the **only** gate that decides whether a URL
 * discovered in rendered chat content gets the chip treatment. It
 * must fail closed: any URL it cannot prove is a live-view URL
 * (under the rules below) must pass through unchanged.
 *
 * Accepted shape — the COMPLETE set of legal inputs:
 *
 *   - MUST start with the literal ``/views/`` prefix (path-relative,
 *     so the browser resolves it against the host page's origin).
 *   - MUST have a non-empty, well-formed root segment after
 *     ``/views/`` (kebab-case identifier, matching
 *     ``daemon/services/live_views.py:is_well_formed_root_name``).
 *   - MUST have at least one non-empty path segment after the root.
 *   - MUST NOT contain a ``..`` segment anywhere in the relative path
 *     (traversal guard — the daemon also enforces this, but the
 *     FE matcher refuses upfront so a chip is never built for a
 *     URL the daemon would 404).
 *   - MUST NOT carry a query string or fragment (the daemon serves
 *     the raw artifact; query / fragment strings are uncommon in
 *     view_link output and would only widen the surface).
 *   - MUST NOT be wrapped in a scheme or authority (no ``http://``,
 *     no ``https://``, no ``//host/...``, no backslash tricks).
 *
 * Rejected shapes — EVERY URL below MUST render as a plain markdown
 * link, NEVER as a chip:
 *
 *   - Anything not starting with ``/views/`` at offset 0.
 *   - ``/views`` alone, ``/views/`` alone, or ``/viewsfoo/...``
 *     (the prefix is ``/views/`` with the trailing slash, not
 *     ``/views``).
 *   - Protocol-relative URLs (``//host/views/...``).
 *   - Absolute URLs (``http://``, ``https://``).
 *   - URLs containing ``..`` as a path segment (after URL-decoding
 *     OR as raw text — both forms are rejected to avoid sneaky
 *     encodings that would let the daemon's realpath check pass on
 *     a different byte than the FE saw).
 *   - URLs whose root segment is empty or contains a ``/``.
 *   - URLs whose relative path is empty (e.g. ``/views/planning``
 *     without a trailing path).
 *   - Backslash-containing URLs (Windows-style paths; not legal
 *     for the daemon's POSIX resolver).
 *   - Whitespace, control characters, or NUL bytes anywhere.
 *   - Empty string / non-string inputs (defensive — typed as
 *     ``unknown``-in / ``string``-out to match the consumer's
 *     contract from ``HTMLAnchorElement.href``).
 *
 * The function is **pure** — no DOM, no Angular, no IO. It is
 * unit-tested as a pure-function truth table (per the
 * ``image-ref.spec.ts`` precedent) so a future contributor who
 * widens the matcher (e.g. to accept fully-qualified URLs) trips
 * the explicit negative-case tests immediately.
 */
export interface ParsedLiveViewUrl {
  /** The original URL string the matcher accepted. */
  readonly href: string;
  /** The root name (e.g. ``"planning"``). */
  readonly root: string;
  /**
   * The relative path under the root, with the surrounding
   * ``/views/<root>/`` stripped. The leading ``/`` is NOT
   * preserved; the value is the raw concatenation of the
   * path segments as they appear in the URL.
   */
  readonly relPath: string;
}

/**
 * Recognized root-name shape. Mirrors the daemon-side
 * ``is_well_formed_root_name`` in ``daemon/services/live_views.py``
 * — kebab-case ASCII identifier, 1..63 chars, starting with a
 * lowercase letter. The leading-class and trailing-class shapes
 * match the URLs the daemon actually serves (Phase 1 roots are
 * ``designer-artifact``, ``planning``, ``tmp-images``).
 */
const ROOT_NAME_REGEX = /^[a-z][a-z0-9-]{0,62}$/;

/**
 * The exact prefix the matcher anchors on. The trailing slash is
 * load-bearing — it is what separates a live-view URL from a
 * hypothetical ``/viewsfoo/...`` neighbor.
 */
export const LIVE_VIEW_URL_PREFIX = '/views/';

/**
 * Predicate: ``true`` iff ``href`` is a live-view URL under the
 * rules above. Returns ``false`` for ``null`` / ``undefined`` /
 * non-string inputs so callers can hand in raw
 * ``HTMLAnchorElement.href`` values without an upstream guard.
 */
export function isLiveViewUrl(href: unknown): href is string {
  const parsed = parseLiveViewUrl(href);
  return parsed !== null;
}

/**
 * Parse a live-view URL into its component parts. Returns ``null``
 * for any input that fails the matcher. The return value is the
 * structural representation the chip renderer + dialog open call
 * use to derive the chip title and the iframe ``src``.
 */
export function parseLiveViewUrl(href: unknown): ParsedLiveViewUrl | null {
  if (typeof href !== 'string') {
    return null;
  }
  // Reject the empty string up front (cheap, common in tests).
  if (href.length === 0) {
    return null;
  }
  // Reject anything with a control char / NUL / whitespace. The
  // daemon URL serves only printable ASCII path bytes; a chip
  // built from a URL with stray whitespace would render a chip
  // the user could not click into a working artifact.
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f\s]/.test(href)) {
    return null;
  }
  // Reject backslashes — they have no meaning in a POSIX path and
  // are the classic Windows-style traversal bait. The daemon's
  // realpath check would reject these too, but a FE-side reject
  // means a chip is never offered to the user.
  if (href.includes('\\')) {
    return null;
  }
  // Anchor on the literal ``/views/`` prefix. Any other origin /
  // scheme / leading character is rejected.
  if (!href.startsWith(LIVE_VIEW_URL_PREFIX)) {
    return null;
  }
  // Strip the prefix and split into root + remainder.
  const remainder = href.slice(LIVE_VIEW_URL_PREFIX.length);
  if (remainder.length === 0) {
    // ``/views/`` with nothing after — malformed.
    return null;
  }
  // Root name is the segment up to the first ``/`` (or the whole
  // remainder if there is no further ``/``).
  const firstSlash = remainder.indexOf('/');
  let root: string;
  let relPath: string;
  if (firstSlash === -1) {
    // ``/views/<root>`` with no trailing path. The daemon requires
    // a rel_path; reject.
    root = remainder;
    relPath = '';
  } else {
    root = remainder.slice(0, firstSlash);
    relPath = remainder.slice(firstSlash + 1);
  }
  if (!ROOT_NAME_REGEX.test(root)) {
    return null;
  }
  if (relPath.length === 0) {
    return null;
  }
  // Reject query strings + fragments outright. The daemon serves a
  // single artifact per URL; a query/fragment would not change the
  // served bytes, and accepting them would only widen the surface
  // for accidental malformed input.
  if (relPath.includes('?') || relPath.includes('#')) {
    return null;
  }
  // Reject URL-encoded forms the FE might see from a markdown
  // autolink or an upstream sanitizer — the daemon URL surface is
  // already restricted to ``%``-safe ASCII via
  // ``is_well_formed_rel_path``; encoded ``..`` (``%2e%2e``) is a
  // classic bypass. We reject any URL containing ``%``.
  if (href.includes('%')) {
    return null;
  }
  // Reject ``..`` segments (after URL-decoding is not possible
  // here because we already reject ``%``; raw ``..`` is the only
  // shape the daemon would see). The check is per relPath
  // segment via ``split('/')`` — the empty string,
  // single-dot ``.``, and ``..`` literals are all forbidden so
  // neither an empty segment nor a traversal slide-through can
  // survive. A leading-dot root is NOT legal here: the root
  // shape was pinned upstream by ``ROOT_NAME_REGEX``
  // (``/^[a-z][a-z0-9-]{0,62}$/``) which forbids a leading
  // dot, so by the time we get here the root segment is
  // guaranteed to start with a lowercase letter.
  const segments = relPath.split('/');
  for (const segment of segments) {
    if (segment === '' || segment === '.' || segment === '..') {
      return null;
    }
  }
  return { href, root, relPath };
}

/**
 * Derive a human-readable chip title from a parsed live-view URL.
 *
 * The title is the FINAL path segment of ``relPath`` (i.e. the
 * file name), with any trailing ``.html`` / ``.htm`` / ``.md``
 * extension stripped for the visible label. The full relPath is
 * returned in the tooltip (``title`` attribute) so the user can see
 * the precise location on hover.
 *
 * If the final segment is empty after stripping, the raw root name
 * is used as a fallback (e.g. ``planning``).
 */
export function deriveLiveViewTitle(parsed: ParsedLiveViewUrl): string {
  const segments = parsed.relPath.split('/').filter((s) => s.length > 0);
  const last = segments[segments.length - 1] ?? parsed.root;
  // Strip the common HTML / markdown extensions so the chip label
  // reads as ``landing`` rather than ``landing.html``. Unknown
  // extensions are kept verbatim (the chip is the user's hint that
  // the artifact exists, not a content-type assertion).
  const stripped = last.replace(/\.(html?|md|markdown|png|jpe?g|gif|svg|webp)$/i, '');
  return stripped.length > 0 ? stripped : last;
}
