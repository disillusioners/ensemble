/**
 * Image-ref contract — single source of truth for the canonical server-URL
 * form that identifies an uploaded-but-not-yet-persisted image.
 *
 * Phase 5 / clipboard-image-chat. The full decision is recorded in
 * ``.agents/shared/planning/clipboard-image-chat/decisions.md §2`` (the
 * CANONICAL WIRE CONTRACT) and ratified by the round-2 rulings in
 * ``architecture-recommendation.md`` (C1 ruling (b); C2 h4-S1).
 *
 * Canonical contract (cross-lane arbitration; losing sides renamed):
 *   - Upload endpoint:     POST /api/tmp_images      (batch envelope)
 *   - Serving endpoint:    GET  /api/tmp_images/<32-hex lowercase uuid4>
 *   - Canonical ref form:  /api/tmp_images/<image_id>
 *                          (relative, same-origin HTTP URL — directly
 *                           renderable as <img [src]>; parse-tolerance on
 *                           GET also accepts ``tmpimg://<id>`` and bare
 *                           ``<id>`` but the FE NEVER emits those forms)
 *   - Message field:       ``image_refs: string[]`` — sibling of
 *                           ``images`` (XOR with legacy data URIs)
 *
 * **Cross-reference contract (Task 7 / phase-5):** the prefix constant
 * exported from this file MUST match the path the backend serves from
 * ``daemon/routers/tmp_images.py`` (registered BEFORE the SPA catch-all
 * in ``daemon/api.py:create_app``). If the FE constant and the BE
 * serving path drift, every thumbnail 404s — the Task 5 reload smoke
 * test fails fast on this misalignment.
 *
 * **Merge-gate contract checklist (decisions.md §2.2):** this constant's
 * literal value appears verbatim in this file and in every consumer that
 * needs the prefix (the SSE whitelist predicate ``isTmpImageRef`` and
 * the optimistic-bubble renderer). Phase-4 production code carries one
 * literal ``/api/tmp_images/`` in ``pages/chat/chat.component.ts``
 * (retry-routing discriminator at the optimistic-rebuild site) — that
 * literal is a documented phase-4 leak and is reported separately; it
 * MUST be migrated to import from this constant in a follow-up.
 */

/**
 * Canonical server-URL prefix for an uploaded image-ref. Trailing slash
 * included so consumers can use ``startsWith(TMP_IMAGE_REF_PREFIX)``
 * without concatenating a separator. The id portion that follows is a
 * 32-hex lowercase uuid4 (canonical §2); the regex below accepts ANY
 * non-empty suffix, so a ref whose id portion is still in flight (e.g.
 * during typing / before upload-resolves) is not falsely rejected — the
 * wire contract itself rejects malformed ids upstream.
 */
export const TMP_IMAGE_REF_PREFIX: '/api/tmp_images/' = '/api/tmp_images/';

/**
 * True iff ``s`` is a canonical server-URL image-ref — i.e. begins with
 * the canonical prefix. The check is PREFIX-SCOPED on purpose (architect
 * ruling #19, recorded in
 * ``architecture-recommendation.md`` §6.5): a scheme check (e.g.
 * ``startsWith('https://')``) would widen the attack surface — it would
 * allow tracking-pixel URLs or internal-network-probe URLs to leak into
 * the rendered ``<img [src]>``. The two legal forms today are
 * ``data:image/...`` (legacy data URIs) and ``/api/tmp_images/<id>``
 * (server refs). Everything else is silently dropped by the SSE
 * whitelist filter that calls this helper.
 *
 * Phase-6 follow-up: the Discord-SSE gap (HTTPS image URLs from
 * Discord-attached screenshots) is PRE-EXISTING and out of scope v1 —
 * its correct future fix is a host allowlist (e.g. ``cdn.discordapp.com``),
 * not a scheme widening here.
 *
 * Type-narrowing: input is declared ``string`` because every call site
 * has already filtered to ``typeof img === 'string'`` upstream; the
 * helper trusts that contract.
 */
export function isTmpImageRef(s: string): boolean {
  return s.startsWith(TMP_IMAGE_REF_PREFIX);
}
