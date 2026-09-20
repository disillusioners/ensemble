/**
 * Phase 5 / clipboard-image-chat — pure-function truth-table spec for
 * the canonical image-ref prefix constant and its discrimination helper.
 *
 *   - The constant value MUST equal the canonical §2 form (decisions.md
 *     §2 — CANONICAL WIRE CONTRACT).
 *   - The helper MUST be prefix-scoped — scheme-widening (e.g.
 *     ``startsWith('https://')``) is REJECTED by design (architect
 *     ruling #19). The truth table pins the predicate's behavior
 *     across data URIs (legacy), server refs (new), and adversarial
 *     inputs (arbitrary URLs, query strings, empty strings).
 *
 * No Angular TestBed — pure-function mirror spec per FE conventions
 * (testing-and-qc-conventions blueprint).
 */
import { isTmpImageRef, TMP_IMAGE_REF_PREFIX } from './image-ref';

const HEX32_A = 'a'.repeat(32); // 32-hex lowercase id per decisions.md §2
const REF_A = `${TMP_IMAGE_REF_PREFIX}${HEX32_A}`;
const REF_B = `${TMP_IMAGE_REF_PREFIX}b1234567890abcdef0123456789abcd`;

describe('image-ref constants', () => {
  describe('TMP_IMAGE_REF_PREFIX', () => {
    it('is the canonical §2 prefix (decisions.md §2 CANONICAL WIRE CONTRACT)', () => {
      expect(TMP_IMAGE_REF_PREFIX).toBe('/api/tmp_images/');
    });

    it('ends with a trailing slash so consumers can use startsWith() without concatenation', () => {
      expect(TMP_IMAGE_REF_PREFIX.endsWith('/')).toBe(true);
    });
  });

  describe('isTmpImageRef — truth table (per plan Test Strategy row 1)', () => {
    it('returns true for a canonical 32-hex-lowercase ref (canonical §2 form)', () => {
      expect(isTmpImageRef(REF_A)).toBe(true);
      expect(isTmpImageRef(REF_B)).toBe(true);
    });

    it('returns true for a ref with a query string (the prefix match still wins)', () => {
      // The helper is prefix-scoped; downstream rendering may or may not
      // support query strings, but the discrimination step is decoupled
      // from that. A ref-shaped URL with a query MUST still be flagged
      // as a ref — otherwise the SSE whitelist would drop it.
      expect(isTmpImageRef(`${REF_A}?cache=bust`)).toBe(true);
    });

    it('returns true for a ref with no file extension (the path id is the discriminator)', () => {
      expect(isTmpImageRef(REF_A)).toBe(true);
    });

    it('returns FALSE for a legacy data URI (legacy form, NOT a server ref)', () => {
      expect(isTmpImageRef('data:image/png;base64,AAAA')).toBe(false);
      expect(isTmpImageRef('data:image/jpeg;base64,/9j/4AAQ')).toBe(false);
    });

    it('returns FALSE for an absolute URL that is NOT a server ref (scheme-widening guard)', () => {
      expect(isTmpImageRef('http://evil.example/x.png')).toBe(false);
      expect(isTmpImageRef('https://evil.example/x.png')).toBe(false);
      expect(isTmpImageRef('https://cdn.discordapp.com/attachments/123/456/x.png')).toBe(false);
    });

    it('returns FALSE for arbitrary non-URL strings', () => {
      expect(isTmpImageRef('not a url')).toBe(false);
      expect(isTmpImageRef('/some/other/path')).toBe(false);
      expect(isTmpImageRef('tmpimg://abc123')).toBe(false); // canonical demotion (decisions.md §2)
      expect(isTmpImageRef('abc123def456789012345678901234de')).toBe(false); // bare 32-hex (NOT a URL)
    });

    it('returns FALSE for the empty string', () => {
      expect(isTmpImageRef('')).toBe(false);
    });
  });

  describe('prefix-mirror-parity (decisions.md §2.2 merge-gate checklist)', () => {
    // Identity-grep-style assertion: the prefix literal MUST live in this
    // constants file (the canonical export site). A future contributor who
    // accidentally hard-codes the prefix elsewhere will see this spec
    // still pass — but the consumer code (sse.service.ts,
    // message-merge.util.ts, image-upload.service.ts) is bound by its
    // own identity-grep pins. NOTE (phase-4 leak): chat.component.ts
    // currently carries one literal ``/api/tmp_images/`` as a retry-
    // routing discriminator; that is documented as a phase-4 leak and
    // is out of scope for this phase-5 fix.
    it('exports the prefix literal verbatim', () => {
      expect(TMP_IMAGE_REF_PREFIX).toBe('/api/tmp_images/');
    });
  });
});
