# Phase 3: Frontend Scanner + Lightbox

## Objective

The Angular web UI renders `ens-img:image-gen` markers as inline images (click-to-open
lightbox) in chat transcripts, with a graceful fallback when an image id no longer
resolves — following the proven live-view-chip DOM-scan pattern, with ZERO impact on
chart-render web behavior and zero daemon router changes (`/views/tmp-images/<id>` is
already served by `daemon/routers/live_views.py` [verified in worktree]).

## Pattern to Copy (verified anchors [verified in worktree])

- Constants module precedent: `frontend/src/app/constants/live-view-url.ts`.
- Scan pipeline: `frontend/src/app/components/chat-interface/chat-interface.component.ts`
  — `ensureMutationObserver()` (:852, observer setup + burst coalescing through
  `scheduleScan()` :862-867), `scheduleScan()` (:184/:195/:200 call sites), then
  `scanForLiveViewChips()` + `injectLiveViewChip()` (recon pins :953-991 / :1004-1079:
  WeakSet idempotency, zone re-entry).
- Lightbox: `openViewer(src: string, messageId: string)` at
  `frontend/src/app/services/image-viewer-actions.service.ts:44`; dialog component
  `frontend/src/app/components/image-viewer-dialog/image-viewer-dialog.component.ts`
  with error-fallback pattern at :46-61.
- Body rendering: ngx-markdown `<markdown [data]>` (`chat-interface.html:146-158`) —
  today there is ZERO marker handling; this phase adds it net-new.
- NOT a convention: `frontend/design-tokens/` is EMPTY (aspirational) — do not cite.

## Design

**Marker scan, not markdown rewrite.** The daemon strips markers from chat-bound text
for chat sources, but markers SURVIVE in transcripts stored/served via API/no-colon
paths (:302-304 skip) — exactly the lanes the web UI reads. **The scanner targets HTML
COMMENT nodes, not text nodes `[AM11]`**: ngx-markdown/marked emits `<!-- ... -->` as
COMMENT_NODE (`nodeType === 8`), so `textContent`/text-node scans NEVER see the marker.
The scanner traverses each message bubble's `bubble.childNodes`, filters
`nodeType === Node.COMMENT_NODE`, matches `node.data` against the family regex, and
swaps the node via `node.replaceWith(img)`. The chip pattern does NOT transfer as-is —
chips scan `<a>` elements, a different node kind (which is why the original text-node
assumption slipped through).

**Regex byte-match (P2 contract):** the FE constants module mirrors the daemon family
grammar exactly; the scanner matches it against COMMENT-node `node.data` strings
`[AM11]`. Implementation note: a comment node's `data` excludes the `<!--`/`-->`
envelope, so the constants module exports BOTH forms derived from the same grammar —
the full-marker form (byte-matched to daemon, for docs/parity tests) and the
content form `^\s*ens-img:([a-z0-9-]+):([a-f0-9]{32})\s*$` used against `node.data`.
Any grammar change in P2 forces a same-commit change here (coupling note).

**Scope discipline:** the scanner handles ONLY `feature === "image-gen"` in v1.
Chart-render markers visible in web transcripts keep today's behavior (rendered as
literal comment text by ngx-markdown / skipped) — changing that is OUT of scope
(plan-overview Open Question 3).

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | New constants module `frontend/src/app/constants/ens-img-marker.ts` (beside `live-view-url.ts`): export `ENS_IMG_MARKER_RE`, `ENS_IMG_FEATURES = ["image-gen"]`, `buildTmpImageUrl(id) => `/views/tmp-images/${id}`` | none | Unit spec: regex matches a canonical image-gen marker; extracts id; rejects chart-render-form and malformed lines |
| 2 | Extend the scan pipeline in `chat-interface.component.ts`: new `scanForEnsImgMarkers()` hooked into the existing `ensureMutationObserver() → scheduleScan()` flow (same coalescing; run after `scanForLiveViewChips()` in the scheduled scan) — traversal: `bubble.childNodes` filtered to `nodeType === Node.COMMENT_NODE` (8), matching `node.data` against the family regex `[AM11]` | 1 | Spec: a rendered `<!-- ens-img:image-gen:<32hex> -->` COMMENT node becomes an `<img>` after scan; adjacent text nodes are untouched; mutation bursts coalesce; no double-inject |
| 3 | Implement `injectEnsImgElement()` (WeakSet idempotency + zone re-entry, mirroring `injectLiveViewChip` :1004-1079): `node.replaceWith(img)` swaps the COMMENT node for `<img [src]="buildTmpImageUrl(id)" alt="generated image" class="ens-img-inline">` `[AM11]` | 2 | Spec: one `<img>` per marker comment; the comment node is removed by replaceWith; repeated scans inject nothing new (WeakSet) |
| 4 | Click handler → lightbox: `click` opens `ImageViewerDialogComponent` via `image-viewer-actions.service.openViewer(buildTmpImageUrl(id), messageId)` | 3 | Spec: click opens dialog with the tmp-images URL |
| 5 | Error fallback: `(error)` on the `<img>` swaps to an inline fallback SVG node (pattern: `image-viewer-dialog.component.ts:46-61`) with a "no longer available" affordance (tmp-image store is retention-swept; expired ids are expected) | 3 | Spec: broken URL renders fallback SVG, no console error, layout stable |
| 6 | Chart-render non-regression guard: spec asserting chart-render markers are NOT transformed by `scanForEnsImgMarkers` and live-view chips still scan/inject | 2-5 | Spec green; existing live-view chip specs untouched and green |

## Files Touched (complete list)

- `frontend/src/app/constants/ens-img-marker.ts` — **NEW** (not pinned)
- `frontend/src/app/components/chat-interface/chat-interface.component.ts` — scan +
  inject functions (not pinned [verified in worktree])
- `frontend/src/app/components/chat-interface/chat-interface.component.html` — only if
  the injection template requires a container tweak (avoid if possible; prefer pure-DOM
  injection like the chip pattern) (not pinned)
- `frontend/src/app/components/chat-interface/chat-interface.component.spec.ts` (or the
  project's spec convention) — new specs (not pinned)

**NOT touched:** any daemon file (zero router change — `/views/tmp-images/<id>` already
served), `image-viewer-dialog` / `image-viewer-actions` (consumed as-is), design-tokens
(empty/aspirational).

## Coupling

- **Tight with Phase 2** — regex byte-match + feature token grammar; change one, change
  both (same-commit discipline).
- **Independent of Phase 1 runtime** — renders any saved image id; does not call the
  tool.
- **Tight with Phase 4** — FE specs are part of the P4 matrix; `tsc --noEmit` gate lives
  in P4's DoD.

## Risks (phase-specific)

- MutationObserver loop: `<img>` injection re-triggers the observer. Mitigation: same
  coalescing + WeakSet guard as the chip pattern; spec asserts scan count stabilizes.
- Marker-node visibility `[AM11]`: markers are COMMENT nodes (`nodeType === 8`) — a
  naive `textContent`/text-node scan never sees them. Mitigation: `childNodes`
  traversal filtering COMMENT_NODE + `node.data` matching (Task 2); spec asserts a
  text-node lookalike (the marker string typed as literal visible text) is NOT
  transformed.
- Expired tmp-image ids (retention sweep) → broken images in old transcripts. Mitigation:
  Task 5 fallback SVG — treated as expected UX, not a defect.
- Zone-change thrash on large transcripts. Mitigation: coalesced scheduling (reuse
  `scheduleScan` timing), no per-marker change detection.

## Definition of Done (per-phase DoD)

1. FE spec suite green: marker → inline `<img>`; click → lightbox opens with tmp-images
   URL; broken id → fallback SVG; chart-render markers untransformed; live-view chips
   unregressed.
2. `cd frontend && ./node_modules/.bin/tsc --noEmit -p tsconfig.app.json` clean
   (CRITICAL: TypeScript ONLY via frontend/'s local devDependency — NEVER bare
   `tsc`/`npx tsc` outside frontend/; standing convention from the 2026-10-03 npx-tsc
   incident).
3. No daemon-side edits required for rendering (router smoke lives in P4).

## Seal-Trip Subsection

**Pinned files touched by P3: NONE.** No file under `frontend/**` appears in
`SEALED_SHA_BASELINES` (all 35 entries are `agents/**` or `daemon/**` [verified in
worktree :1253-1296]). No ceremony required for this phase.

## Exit Criterion

Image-gen markers render inline with working lightbox + fallback in the dev UI, FE
specs + tsc green, chart-render and live-view-chip behavior unregressed. P4's matrix
can include FE legs.

## Reviewer implementation-time notes `[review-notes]`

- The FE spec needs an explicit `Document.createComment` fixture constructing real
  COMMENT_NODE children (the existing ngx-markdown stub emits no comments).
