/**
 * `ens-img:<feature>:<32hex>` marker grammar — single source of truth
 * for the FE-side recognition of inline image markers that the
 * daemon emits into chat transcripts.
 *
 * ## Family grammar (mirror of `daemon/sources/dispatcher.py:30`)
 *
 * The daemon emits a COMMENT node carrying a marker that follows the
 * family grammar:
 *
 *     <!-- ens-img:<feature>:<32hex-lowercase> -->
 *
 * The original chart-render family member lives at
 * `daemon/sources/dispatcher.py:30` as a regex
 * (`^<!-- ens-img:chart-render:([a-f0-9]{32}) -->$`). The
 * image-gen feature is the second member of the family — same shape,
 * different feature token. This module exports BOTH:
 *
 *   - The **full-marker form** (`ENS_IMG_MARKER_FULL_RE`) — the byte-
 *     matched regex that mirrors the daemon-side grammar
 *     verbatim. Used in the unit-spec parity matrix and any docs /
 *     debug surface that displays the marker as a user would see it
 *     in raw markdown.
 *   - The **content form** (`ENS_IMG_MARKER_CONTENT_RE`) — the same
 *     grammar MINUS the `<!-- -->` envelope. `Node.nodeType === 8`
 *     (COMMENT_NODE) exposes the comment body via `node.data`, which
 *     already strips the envelope — the scanner matches against
 *     `node.data` with this form so the regex does not have to
 *     special-case the envelope in the hot path.
 *
 * Both forms are DERIVED from the same body regex — `ENS_IMG_CORE_RE`
 * — so any grammar change (a new segment, a tighter character class)
 * is a single-edit update with both forms staying in lock-step. The
 * coupling is structural, not coincidental: a divergence here would
 * silently break either the docs/parity path OR the scanner hot path
 * depending on which form drifted.
 *
 * ## Feature gate (v1 scope discipline)
 *
 * The scanner transforms ONLY `feature === "image-gen"` markers in v1
 * (`ENS_IMG_FEATURES[0]`). Chart-render markers visible in web
 * transcripts keep today's behavior — they are rendered as
 * (effectively invisible) comment text by ngx-markdown / marked and
 * remain so. Changing that is OUT of scope (plan-overview Open
 * Question 3). The feature token list is the SINGLE source of truth
 * for the gate; future feature additions (e.g. `image-edit`) are an
 * additive change to `ENS_IMG_FEATURES` plus the same gating logic —
 * the regex stays general.
 *
 * ## URL builder
 *
 * `buildTmpImageUrl(id)` derives the canonical `/views/tmp-images/<id>`
 * URL the FE binds to the `<img [src]>` after the marker is
 * transformed. The shape matches the daemon `LiveView` router's
 * `tmp-images` root exactly (`daemon/services/live_views.py`). The
 * helper is the SOLE place this URL is built — consumers (the
 * scanner, the click handler) both call through it so a future
 * router rename is a one-line update.
 *
 * ## AM11 contract (architect amendment)
 *
 * ngx-markdown / marked emits the marker as a real HTML comment,
 * which the DOM exposes as `Node.nodeType === 8` (COMMENT_NODE) with
 * the comment body in `node.data` (the `<!--` / `-->` envelope is
 * stripped automatically). A naive `textContent` / text-node scan
 * NEVER sees the marker. The scanner filters `childNodes` by
 * `nodeType === Node.COMMENT_NODE` (AM11 binding) and matches
 * `node.data` against `ENS_IMG_MARKER_CONTENT_RE`.
 */
export const ENS_IMG_CORE_RE = /^ens-img:([a-z0-9-]+):([a-f0-9]{32})$/;

/**
 * Full-marker form — matches the verbatim daemon-side family
 * grammar (with the `<!-- -->` envelope) as a regex anchored at
 * both ends. Mirrors `daemon/sources/dispatcher.py:30`
 * `^<!-- ens-img:chart-render:([a-f0-9]{32}) -->$` but generalized
 * to the family feature class `[a-z0-9-]+` so the image-gen
 * feature passes through identically.
 *
 * Whitespace tolerance inside the envelope is intentionally
 * permissive (`\s*` on both sides) so that a future contributor
 * who pads the marker with whitespace for readability does not
 * silently break the scanner's parity. The envelope itself is
 * load-bearing — the regex MUST NOT drop it; `live-view-url.spec.ts`
 * style mirror-parity assertions rely on the full form.
 */
export const ENS_IMG_MARKER_FULL_RE = /^<!--\s*ens-img:([a-z0-9-]+):([a-f0-9]{32})\s*-->$/;

/**
 * Content form — matches `node.data` on a `COMMENT_NODE`. The
 * envelope is stripped by the DOM (`<!--` / `-->` are NOT part of
 * `node.data`); the content regex therefore drops the envelope and
 * matches the bare body. Same body grammar as the full form
 * (`ENS_IMG_CORE_RE`) — both forms share the SAME character classes
 * so a grammar update is a single edit.
 */
export const ENS_IMG_MARKER_CONTENT_RE = /^\s*ens-img:([a-z0-9-]+):([a-f0-9]{32})\s*$/;

/**
 * The list of features the FE scanner transforms in v1. Today only
 * `image-gen` is in scope; chart-render markers visible in web
 * transcripts keep today's (effectively invisible comment) behavior
 * by deliberate omission from this list (plan Task 6 non-regression
 * guard, plan-overview Open Question 3). Adding a new feature is an
 * additive edit here PLUS the corresponding handler logic — the
 * marker regex is family-generic on purpose.
 */
export const ENS_IMG_FEATURES: ReadonlyArray<string> = ['image-gen'];

/**
 * Build the canonical `/views/tmp-images/<id>` URL that the
 * `<img [src]>` binds to after the marker is transformed. Mirrors
 * the `tmp-images` root in `daemon/services/live_views.py`; the
 * helper is the SOLE place this URL is constructed so a future
 * router rename is a one-line update.
 */
export function buildTmpImageUrl(id: string): string {
  return `/views/tmp-images/${id}`;
}