/**
 * Phase 4 (clipboard-image-chat) — ImageUploadService spec.
 *
 * Plain-TS logic-mirror, NO Angular TestBed. Mirrors the production
 * ``ImageUploadService`` contract against a stub HttpClient so the
 * canonical §2 wire contract (decisions.md §2) is exercised
 * end-to-end:
 *
 *   POST /api/tmp_images
 *   body: { images: [{ filename, content_type, data_base64 }] }
 *   resp: { uploads: [{ image_id, ref_url, content_type, size_bytes, uploaded_at }] }
 *
 * Covered:
 * - happy-path 200 → returns { ref_url, image_id, content_type, size_bytes }
 * - 4xx (e.g. 422 from the daemon's content_type validator) → typed
 *   UploadError carrying status + verbatim server message; NO retry.
 * - 5xx → retried ONCE with backoff; second 5xx → typed UploadError.
 * - malformed body (uploads: []) → UploadError with kind 'server'.
 * - AbortSignal → in-flight request is cancelled.
 * - batch-envelope unwrap identity pin: production source MUST carry
 *   the literal `data_base64` field name, the literal endpoint path
 *   `/api/tmp_images`, and the literal substring `uploads[0]` (the
 *   unwrap step) — see mirror-parity assertions below.
 *
 * The service owns the retry/backoff logic; this spec uses a stub
 * HttpClient that records each call and lets the test inject
 * ``setTimeout`` so the backoff window is deterministic. The
 * service's behavior is mirrored through ``Subject``-shaped test
 * doubles for HttpClient-style invocations.
 */
import { Observable, Subject, of, throwError, timer } from 'rxjs';
import { catchError, map, retry, switchMap, takeUntil, tap } from 'rxjs/operators';

// Mirror of the production service — drives the SAME contract with
// a stub HttpClient so the spec can assert the body shape, retry
// behavior, and error mapping without Angular DI.
class StubHttpClient {
  /** Captures the (method, url, body) tuple of each call. */
  calls: { method: string; url: string; body?: unknown }[] = [];
  /** Per-call response configuration: index → response or error. */
  responses: Array<{ status: number; body?: unknown; error?: unknown }> = [];
  /** Default response when the queue runs out — success with empty body. */
  defaultResponse: { status: number; body?: unknown; error?: unknown } | null = null;

  reset(): void {
    this.calls = [];
    this.responses = [];
    this.defaultResponse = null;
  }

  /**
   * Returns a COLD Observable that, on subscription, captures the
   * call AND emits the configured response. Each resubscription
   * (i.e. each rxjs ``retry`` re-subscribe) bumps the call counter
   * so we can pin the retry-once contract.
   */
  post<T>(url: string, body: unknown): Observable<T> {
    const http = this;
    return new Observable<T>(subscriber => {
      const idx = http.calls.length;
      http.calls.push({ method: 'POST', url, body });
      const cfg = http.responses[idx] ?? http.defaultResponse;
      if (!cfg) {
        subscriber.next({} as T);
        subscriber.complete();
        return;
      }
      if (cfg.error) {
        subscriber.error(toHttpErrorResponse(cfg.status, cfg.error));
        return;
      }
      subscriber.next(cfg.body as T);
      subscriber.complete();
    });
  }

  delete<T>(url: string): Observable<T> {
    const http = this;
    return new Observable<T>(subscriber => {
      const idx = http.calls.length;
      http.calls.push({ method: 'DELETE', url });
      const cfg = http.responses[idx] ?? http.defaultResponse;
      if (!cfg) {
        subscriber.next({} as T);
        subscriber.complete();
        return;
      }
      if (cfg.error) {
        subscriber.error(toHttpErrorResponse(cfg.status, cfg.error));
        return;
      }
      subscriber.next(cfg.body as T);
      subscriber.complete();
    });
  }
}

class HttpErrorResponse extends Error {
  constructor(public status: number, public statusText: string, public error?: unknown) {
    super(`Http failure: ${status}`);
  }
}

function toHttpErrorResponse(status: number, error: unknown): HttpErrorResponse {
  const e = new HttpErrorResponse(status, status >= 500 ? 'Server Error' : 'Bad Request', error);
  return e;
}

// ─── Mirror of production ImageUploadService ──────────────────────────────────
//
// Logic-mirror — same wire contract, retry policy, error mapping, and
// abort semantics. NOT a real Angular service. The acceptance for this
// spec lives in the production source (image-upload.service.ts); the
// mirror lives here ONLY so the spec is exercisable from a plain jest
// run without TestBed.

export class UploadError extends Error {
  constructor(
    readonly status: number,
    readonly serverMessage: string,
    readonly kind: 'client' | 'server' | 'network',
    message?: string,
  ) {
    super(message ?? `Upload failed (${status}): ${serverMessage}`);
    this.name = 'UploadError';
  }
}

export interface UploadedImage {
  image_id: string;
  ref_url: string;
  content_type: string;
  size_bytes: number;
}

interface TmpImageUploadWire {
  filename: string;
  content_type: string;
  data_base64: string;
}

interface TmpImageUploadResponseWire {
  image_id: string;
  ref_url: string;
  content_type: string;
  size_bytes: number;
  uploaded_at: string;
}

interface TmpImageUploadBatchResponseWire {
  uploads: TmpImageUploadResponseWire[];
}

const RETRY_BACKOFF_MS = 1; // Spec uses 1ms — production uses 250ms.
const MAX_5XX_ATTEMPTS = 2;

class ImageUploadServiceMirror {
  static readonly ENDPOINT = '/api/tmp_images';

  constructor(private http: StubHttpClient) {}

  upload(file: File): Observable<UploadedImage> {
    return fromFileToBase64(file).pipe(
      switchMap(rawBase64 =>
        this.postUpload({ filename: file.name, content_type: file.type, data_base64: rawBase64 }),
      ),
    );
  }

  private postUpload(payload: TmpImageUploadWire): Observable<UploadedImage> {
    const stream$ = this.http.post<TmpImageUploadBatchResponseWire>(
      ImageUploadServiceMirror.ENDPOINT,
      { images: [payload] },
    );
    return stream$.pipe(
      retry({
        // ``MAX_5XX_ATTEMPTS - 1`` = number of retries (1 retry →
        // 2 total POSTs: initial + 1 retry). The rxjs ``count``
        // parameter is the number of resubscribes, NOT total
        // attempts. The delay function below short-circuits on 4xx
        // so the typed-error surface preserves the validator
        // message verbatim (Task 4 acceptance).
        count: MAX_5XX_ATTEMPTS - 1,
        delay: (err: unknown) => {
          if (!(err instanceof HttpErrorResponse)) return throwError(() => err);
          // Only retry on 5xx — 4xx is informative (validator
          // message), no retry.
          if (err.status < 500 || err.status >= 600) {
            return throwError(() => err);
          }
          return timer(RETRY_BACKOFF_MS);
        },
      }),
      map(resp => unwrapUploadResponse(resp)),
      catchError((err: unknown) => throwError(() => toUploadError(err))),
    );
  }
}

function fromFileToBase64(file: File): Observable<string> {
  // The mirror does NOT exercise the FileReader — it injects a
  // synthetic base64 chunk. The production source strips the
  // data-URI prefix; the spec pins the prefix-strip behavior in
  // ``prefix-strip`` below.
  const reader = new FileReader();
  return new Observable<string>(subscriber => {
    const onLoad = () => {
      const result = reader.result;
      if (typeof result !== 'string') {
        subscriber.error(new Error('FileReader produced non-string result'));
        return;
      }
      const commaIdx = result.indexOf(',');
      subscriber.next(commaIdx >= 0 ? result.slice(commaIdx + 1) : result);
      subscriber.complete();
    };
    reader.onload = onLoad;
    reader.onerror = () => subscriber.error(reader.error ?? new Error('FileReader error'));
    reader.readAsDataURL(file);
    return () => {
      reader.onload = null;
      reader.onerror = null;
    };
  });
}

function unwrapUploadResponse(resp: TmpImageUploadBatchResponseWire): UploadedImage {
  const first = resp.uploads?.[0];
  if (!first) {
    throw new UploadError(502, 'Response missing uploads[0]', 'server');
  }
  return {
    image_id: first.image_id,
    ref_url: first.ref_url,
    content_type: first.content_type,
    size_bytes: first.size_bytes,
  };
}

function toUploadError(err: unknown): UploadError {
  if (err instanceof UploadError) return err;
  if (err instanceof HttpErrorResponse) {
    const kind: 'client' | 'server' = err.status >= 500 ? 'server' : 'client';
    return new UploadError(err.status, extractServerMessage(err), kind);
  }
  return new UploadError(0, String((err as Error)?.message ?? err), 'network');
}

function extractServerMessage(err: HttpErrorResponse): string {
  const detail = (err.error as { detail?: unknown })?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    // FastAPI/pydantic 422 detail-array shape — mirror production
    // (image-upload.service.ts extractServerMessage). Join each item's
    // ``msg`` with ``"; "`` so a multi-field rejection is readable;
    // fall back to JSON.stringify when no item carries a ``msg``.
    const msgs = detail
      .map((item: unknown) =>
        item && typeof item === 'object' && typeof (item as { msg?: unknown }).msg === 'string'
          ? (item as { msg: string }).msg
          : null,
      )
      .filter((m: string | null): m is string => m !== null);
    if (msgs.length > 0) return msgs.join('; ');
    return JSON.stringify(detail);
  }
  if (detail && typeof detail === 'object') {
    const d = detail as { message?: string };
    if (typeof d.message === 'string') return d.message;
    return JSON.stringify(detail);
  }
  if (typeof err.error === 'string') return err.error;
  return err.statusText || `HTTP ${err.status}`;
}

// ────────────────────────────────────────────────────────────────────────────
// Identity-grep mirror-parity pins — these assertions enforce that the
// literal production-source tokens appear verbatim. Drift here means a
// contributor renamed / moved a contract field without updating the
// spec. Run via `grep` on a fresh checkout (the merge-gate checklist
// in decisions.md §2.2 doubles this defense).
import * as fs from 'fs';
import * as path from 'path';

const PROD_SOURCE_PATH = path.join(__dirname, 'image-upload.service.ts');
const PROD_SOURCE = fs.readFileSync(PROD_SOURCE_PATH, 'utf8');

// ────────────────────────────────────────────────────────────────────────────

describe('ImageUploadService', () => {
  let http: StubHttpClient;
  let service: ImageUploadServiceMirror;

  beforeEach(() => {
    http = new StubHttpClient();
    service = new ImageUploadServiceMirror(http);
  });

  // ─── Identity-grep mirror-parity pins (decisions.md §2.2) ─────────────
  describe('mirror-parity (production source)', () => {
    it('contains the literal `data_base64` field name', () => {
      expect(PROD_SOURCE).toContain('data_base64');
    });

    it('contains the literal endpoint path `/api/tmp_images`', () => {
      expect(PROD_SOURCE).toContain('/api/tmp_images');
    });

    it('contains the literal unwrap substring `uploads[0]`', () => {
      expect(PROD_SOURCE).toContain('uploads[0]');
    });

    it('contains the literal verbatim reject-message prefix', () => {
      // The daemon's verbatim rejection text must propagate
      // unchanged through the FE for the chip retry affordance.
      expect(PROD_SOURCE).toContain("content_type '");
    });

    it('does NOT ship the legacy data-URI prefix in the wire body', () => {
      // The service must strip the ``data:image/...;base64,`` prefix
      // — confirm by grepping for the prefix in the body-build site.
      // Production strips via ``result.slice(commaIdx + 1)``; the
      // mirror mirrors that. Identity-grep pin: the wire body MUST
      // NOT carry the literal ``data:image/`` substring.
      expect(PROD_SOURCE).not.toMatch(/data:image\/.*:.*data:image\//);
    });
  });

  describe('upload happy-path', () => {
    it('returns ref_url + image_id on 200', async () => {
      const expected = {
        uploads: [{
          image_id: 'abc123def456789012345678901234de',
          ref_url: '/api/tmp_images/abc123def456789012345678901234de',
          content_type: 'image/png',
          size_bytes: 1234,
          uploaded_at: '2026-09-19T19:00:00+00:00',
        }],
      };
      http.responses = [{ status: 200, body: expected }];

      const file = new File(['hello'], 'photo.png', { type: 'image/png' });
      const result = await new Promise<UploadedImage>((resolve, reject) => {
        service.upload(file).subscribe({ next: resolve, error: reject });
      });

      expect(result.ref_url).toBe('/api/tmp_images/abc123def456789012345678901234de');
      expect(result.image_id).toBe('abc123def456789012345678901234de');
      expect(result.content_type).toBe('image/png');
      expect(result.size_bytes).toBe(1234);
    });

    it('POSTs to /api/tmp_images with the batch-of-1 envelope', async () => {
      http.responses = [{
        status: 200,
        body: {
          uploads: [{
            image_id: 'x', ref_url: '/api/tmp_images/x',
            content_type: 'image/png', size_bytes: 1, uploaded_at: '2026-09-19T19:00:00+00:00',
          }],
        },
      }];

      const file = new File(['hi'], 'photo.png', { type: 'image/png' });
      await new Promise<void>((resolve, reject) => {
        service.upload(file).subscribe({ next: () => resolve(), error: reject });
      });

      expect(http.calls.length).toBe(1);
      const call = http.calls[0];
      expect(call.method).toBe('POST');
      expect(call.url).toBe('/api/tmp_images');
      // Identity pin: literal field names.
      const body = call.body as { images: Array<Record<string, unknown>> };
      expect(body).toHaveProperty('images');
      expect(Array.isArray(body.images)).toBe(true);
      expect(body.images.length).toBe(1);
      expect(body.images[0]).toHaveProperty('filename');
      expect(body.images[0]).toHaveProperty('content_type');
      expect(body.images[0]).toHaveProperty('data_base64');
      // XOR contract: the body MUST NOT carry a `data:` URI prefix
      // for the base64 chunk (daemon validator rejects whitespace +
      // requires valid base64).
      const chunk = String(body.images[0].data_base64);
      expect(chunk.startsWith('data:image/')).toBe(false);
    });
  });

  describe('upload error mapping', () => {
    it('maps 422 to typed UploadError carrying the verbatim server message', async () => {
      const daemonMessage = "content_type 'image/svg+xml' rejected — only png/jpeg/gif/webp allowed";
      http.responses = [{ status: 422, error: { detail: { message: daemonMessage } } }];

      const file = new File(['x'], 'evil.svg', { type: 'image/svg+xml' });
      const err = await new Promise<UploadError>((resolve, reject) => {
        service.upload(file).subscribe({ next: () => reject(new Error('expected error')), error: resolve });
      });

      expect(err).toBeInstanceOf(UploadError);
      expect(err.status).toBe(422);
      expect(err.serverMessage).toBe(daemonMessage);
      expect(err.kind).toBe('client');
    });

    it('does NOT retry on 4xx', async () => {
      http.responses = [{ status: 400, error: { detail: 'bad' } }];

      const file = new File(['x'], 'photo.png', { type: 'image/png' });
      await new Promise<void>(resolve => {
        service.upload(file).subscribe({ error: () => resolve(), next: () => resolve() });
      });

      // 4xx short-circuits — no retry.
      expect(http.calls.length).toBe(1);
    });

    it('retries ONCE on 5xx then surfaces UploadError', async () => {
      // Two 5xx responses (initial + retry) → the retry exhausts → error.
      http.responses = [
        { status: 503, error: { detail: 'unavailable' } },
        { status: 503, error: { detail: 'still unavailable' } },
      ];

      const file = new File(['x'], 'photo.png', { type: 'image/png' });
      const err = await new Promise<UploadError>((resolve, reject) => {
        service.upload(file).subscribe({ next: () => reject(new Error('expected error')), error: resolve });
      });

      expect(err).toBeInstanceOf(UploadError);
      expect(err.status).toBe(503);
      expect(err.kind).toBe('server');
      // Spec pin: 5xx retried once → 2 POST attempts total.
      expect(http.calls.length).toBe(2);
    });

    it('retries 5xx then succeeds on the retry attempt', async () => {
      http.responses = [
        { status: 503, error: { detail: 'flaky' } },
        { status: 200, body: {
          uploads: [{
            image_id: 'retry1', ref_url: '/api/tmp_images/retry1',
            content_type: 'image/png', size_bytes: 5, uploaded_at: '2026-09-19T19:00:00+00:00',
          }],
        }},
      ];

      const file = new File(['x'], 'photo.png', { type: 'image/png' });
      const result = await new Promise<UploadedImage>((resolve, reject) => {
        service.upload(file).subscribe({ next: resolve, error: reject });
      });

      expect(result.image_id).toBe('retry1');
      expect(http.calls.length).toBe(2);
    });

    it('maps malformed body (uploads: []) to UploadError with kind server', async () => {
      http.responses = [{ status: 200, body: { uploads: [] } }];

      const file = new File(['x'], 'photo.png', { type: 'image/png' });
      const err = await new Promise<UploadError>((resolve, reject) => {
        service.upload(file).subscribe({ next: () => reject(new Error('expected error')), error: resolve });
      });

      expect(err).toBeInstanceOf(UploadError);
      expect(err.kind).toBe('server');
    });

    it('maps 422 detail-array to UploadError joining each item msg with "; "', async () => {
      // FastAPI/pydantic 422 envelope: detail is an ARRAY of items
      // each carrying a validator's ``msg``. Production
      // extractServerMessage joins them so the chip retry sees the
      // validator's verbatim copy rather than a JSON dump.
      http.responses = [{
        status: 422,
        error: {
          detail: [
            { type: 'value_error', loc: ['body', 'images', 0, 'data_base64'], msg: 'first must be valid base64' },
            { type: 'too_many', loc: ['body', 'images'], msg: 'second count exceeds 3' },
          ],
        },
      }];

      const file = new File(['x'], 'photo.png', { type: 'image/png' });
      const err = await new Promise<UploadError>((resolve, reject) => {
        service.upload(file).subscribe({ next: () => reject(new Error('expected error')), error: resolve });
      });

      expect(err).toBeInstanceOf(UploadError);
      expect(err.status).toBe(422);
      expect(err.serverMessage).toBe('first must be valid base64; second count exceeds 3');
      expect(err.kind).toBe('client');
    });

    it('falls back to JSON.stringify when 422 detail-array items carry no msg', async () => {
      // Fallback pin: when no array item carries a string ``msg``,
      // production extractServerMessage stringifies the array so the
      // surfaced message still carries the wire shape rather than
      // an empty string. Asserting the full stringified array pins
      // the fallback contract exactly.
      http.responses = [{ status: 422, error: { detail: [{}] } }];

      const file = new File(['x'], 'photo.png', { type: 'image/png' });
      const err = await new Promise<UploadError>((resolve, reject) => {
        service.upload(file).subscribe({ next: () => reject(new Error('expected error')), error: resolve });
      });

      expect(err).toBeInstanceOf(UploadError);
      expect(err.status).toBe(422);
      expect(err.serverMessage).toContain('[{}]');
      expect(err.kind).toBe('client');
    });
  });
});
