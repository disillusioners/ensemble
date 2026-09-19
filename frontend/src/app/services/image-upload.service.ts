/**
 * Image upload service — Phase 4 of the clipboard-image-chat feature.
 *
 * Wires the canonical §2 wire contract (decisions.md §2 /
 * architecture-recommendation.md round-2 C3) into the FE composer:
 *
 *   POST /api/tmp_images
 *   body: { images: [{ filename, content_type, data_base64 }] }
 *   resp: { uploads: [{ image_id, ref_url, content_type, size_bytes, uploaded_at }] }
 *
 * The composer calls ``upload(file, { signal })`` from
 * ``MessageInputComponent.handleSubmit`` for every preview that lacks a
 * ``refUrl``. The service converts each ``File`` to a RAW base64 chunk
 * (NOT a data URI — the existing ``convertToBase64`` helper produces
 * ``data:image/...;base64,<chunk>`` and we MUST strip the prefix or
 * the daemon's regex 422s on whitespace-in-base64), wraps it in the
 * batch-of-1 envelope, POSTs, and unwraps ``uploads[0]`` into the
 * typed ``UploadedImage`` shape the chip needs.
 *
 * Design notes:
 *
 * - **Abort support.** ``upload(file, { signal })`` accepts an
 *   ``AbortSignal`` from the caller. ``removeImage`` constructs the
 *   signal so chip removal cancels the in-flight POST before the
 *   server commits storage. Angular's ``HttpClient`` accepts a
 *   signal-aware ``subscribe({ signal })`` since v18; we forward
 *   verbatim.
 * - **5xx retry-once.** A single retry with a short backoff (250ms)
 *   for transient infrastructure errors. Retry happens BEFORE the
 *   typed ``UploadError`` is thrown. 4xx errors short-circuit
 *   immediately (the validator message is informative — retrying a
 *   422 yields the same 422).
 * - **Typed failure.** ``UploadError`` carries the HTTP status and
 *   the server's message verbatim (the daemon's verbatim
 *   "content_type '... ' rejected — only png/jpeg/gif/webp allowed"
 *   string is preserved end-to-end for the chip retry affordance).
 * - **Eager release.** ``deleteImage(imageId, { signal })`` is
 *   fire-and-forget at the chip-remove site; the daemon returns 204
 *   on success and 404 on never-existed (both treated as success —
 *   the spec pins this idempotency).
 *
 * The service owns NO Angular signals — it is a pure stateless
 * function over ``HttpClient`` so the plain-TS spec can exercise the
 * full pipeline via a mock ``HttpClient`` (image-upload.spec.ts).
 */
import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Observable, Subject, defer, of, throwError, timer } from 'rxjs';
import { catchError, map, retry, switchMap, takeUntil, tap } from 'rxjs/operators';

/** Typed result of one upload — what the chip needs to render its uploaded state. */
export interface UploadedImage {
  image_id: string;
  ref_url: string;
  content_type: string;
  size_bytes: number;
}

/**
 * Typed upload failure. Carries the HTTP status (for chip UX
 * classification: 4xx = permanent, 5xx = retried-then-failed) and the
 * daemon's verbatim message so the chip retry affordance can render
 * the same copy the server returned.
 */
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

export interface UploadOptions {
  /** Abort signal — when aborted, the in-flight POST is cancelled via HttpClient. */
  signal?: AbortSignal;
}

export interface DeleteOptions {
  /** Abort signal — passed through to the DELETE; ``removeImage`` does not need one today. */
  signal?: AbortSignal;
}

/**
 * Wire shape — request body for one image. Matches
 * ``daemon.models.tmp_image.TmpImageUpload`` field-for-field.
 *
 * The base64 chunk is the RAW chunk (no data-URI prefix). The daemon
 * validator rejects whitespace and requires valid base64
 * (``daemon/models/tmp_image.py:130-149``).
 */
interface TmpImageUploadWire {
  filename: string;
  content_type: string;
  /** Raw base64 chunk. NO data-URI prefix; NO whitespace/newlines. */
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

@Injectable({
  providedIn: 'root'
})
export class ImageUploadService {
  private readonly http = inject(HttpClient);

  /** Canonical §2 endpoint path — see decisions.md §2 merge-gate checklist. */
  static readonly ENDPOINT = '/api/tmp_images';

  /** Server-rejected content_type validation error (verbatim from daemon/models/tmp_image.py:125). */
  static readonly REJECTED_CONTENT_TYPE_PREFIX = "content_type '";

  /** Retry backoff (ms) between the first 5xx attempt and the retry. */
  private static readonly RETRY_BACKOFF_MS = 250;

  /** Max retry attempts on 5xx (5xx retried once per plan Task 4 acceptance). */
  private static readonly MAX_5XX_ATTEMPTS = 2;

  /**
   * Upload one file. Returns ``UploadedImage`` on success; throws
   * ``UploadError`` on non-2xx (after the 5xx-retry-once backoff).
   * Aborts via the optional signal.
   */
  upload(file: File, options: UploadOptions = {}): Observable<UploadedImage> {
    return defer(() => fromFileToBase64(file)).pipe(
      switchMap(rawBase64 =>
        this.postUpload(
          {
            filename: file.name,
            content_type: file.type,
            data_base64: rawBase64,
          },
          options,
        ),
      ),
    );
  }

  /**
   * Best-effort DELETE — used by ``removeImage`` to release server
   * storage eagerly. Returns ``void`` on success (204), on 404 (idempotent),
   * or on abort; rethrows ``UploadError`` for any other HTTP failure
   * (caller logs and swallows).
   */
  deleteImage(imageId: string, options: DeleteOptions = {}): Observable<void> {
    const aborted$ = abortSignalToObservable(options.signal);
    const obs$ = this.http.delete(`${ImageUploadService.ENDPOINT}/${imageId}`);
    const stream$ = aborted$ ? obs$.pipe(takeUntil(aborted$)) : obs$;
    return stream$.pipe(
      map(() => undefined),
      catchError((err: unknown) => {
        if (isAbortError(err)) return of(undefined);
        if (err instanceof HttpErrorResponse) {
          // 404 = already gone (idempotent). 204 path goes through
          // ``map``; abort = swallow.
          if (err.status === 404 || err.status === 0) return of(undefined);
          return throwError(
            () =>
              new UploadError(
                err.status,
                extractServerMessage(err),
                err.status >= 500 ? 'server' : 'client',
              ),
          );
        }
        // Network / abort — swallow (best-effort).
        return of(undefined);
      }),
    );
  }

  /**
   * POST helper — applies the 5xx-retry-once + typed-error mapping.
   *
   * Abort: the optional ``AbortSignal`` is watched via ``abort$`` and
   * the POST is unsubscribed on abort (Angular's HttpClient cancels
   * the in-flight XHR). Angular 21's HttpClient request options do
   * NOT carry a ``signal`` field directly, so we use the rxjs
   * ``takeUntil`` Subject pattern.
   */
  private postUpload(
    payload: TmpImageUploadWire,
    options: UploadOptions,
  ): Observable<UploadedImage> {
    const obs$ = this.http.post<TmpImageUploadBatchResponseWire>(
      ImageUploadService.ENDPOINT,
      { images: [payload] },
    );
    const aborted$ = abortSignalToObservable(options.signal);
    const stream$ = aborted$
      ? obs$.pipe(takeUntil(aborted$))
      : obs$;
    return stream$.pipe(
      retry({
        // ``MAX_5XX_ATTEMPTS - 1`` = number of retries (1 retry →
        // 2 total POSTs: initial + 1 retry). The rxjs ``count``
        // parameter is the number of resubscribes, NOT total
        // attempts. The delay function below short-circuits on 4xx
        // so the typed-error surface preserves the validator
        // message verbatim (Task 4 acceptance).
        count: ImageUploadService.MAX_5XX_ATTEMPTS - 1,
        delay: (err: unknown) => {
          if (!(err instanceof HttpErrorResponse)) return throwError(() => err);
          // Only retry on 5xx — 4xx is informative (validator
          // message), no retry.
          if (err.status < 500 || err.status >= 600) {
            return throwError(() => err);
          }
          return timer(ImageUploadService.RETRY_BACKOFF_MS);
        },
      }),
      map(resp => unwrapUploadResponse(resp)),
      catchError((err: unknown) => {
        if (isAbortError(err)) {
          // Abort = no upload, no error to surface; the chip sees the
          // removal, the user sees nothing. Rethrow as a tagged
          // signal via throwError so the caller can ignore.
          return throwError(() => new UploadError(0, 'aborted', 'network', 'Upload aborted'));
        }
        return throwError(() => toUploadError(err));
      }),
    );
  }
}

/** Convert a File to a raw base64 chunk — strips the data-URI prefix. */
function fromFileToBase64(file: File): Observable<string> {
  const reader = new FileReader();
  return new Observable<string>(subscriber => {
    const onLoad = () => {
      const result = reader.result;
      if (typeof result !== 'string') {
        subscriber.error(new Error('FileReader produced non-string result'));
        return;
      }
      // Strip the ``data:<mime>;base64,`` prefix. The daemon's regex
      // rejects any whitespace AND requires valid base64 — feeding
      // the full data URI would render as garbage base64.
      const commaIdx = result.indexOf(',');
      const chunk = commaIdx >= 0 ? result.slice(commaIdx + 1) : result;
      subscriber.next(chunk);
      subscriber.complete();
    };
    const onError = () => subscriber.error(reader.error ?? new Error('FileReader error'));
    reader.onload = onLoad;
    reader.onerror = onError;
    reader.readAsDataURL(file);
    return () => {
      reader.onload = null;
      reader.onerror = null;
      if (reader.readyState === FileReader.LOADING) reader.abort();
    };
  });
}

/** Extract the canonical first upload from the batch response. */
function unwrapUploadResponse(
  resp: TmpImageUploadBatchResponseWire,
): UploadedImage {
  const first = resp.uploads?.[0];
  if (!first) {
    throw new UploadError(
      502,
      'Response missing uploads[0]',
      'server',
      'Server returned an empty uploads array',
    );
  }
  return {
    image_id: first.image_id,
    ref_url: first.ref_url,
    content_type: first.content_type,
    size_bytes: first.size_bytes,
  };
}

/** Map any error shape to UploadError. */
function toUploadError(err: unknown): UploadError {
  if (err instanceof UploadError) return err;
  if (err instanceof HttpErrorResponse) {
    const kind: 'client' | 'server' = err.status >= 500 ? 'server' : 'client';
    return new UploadError(err.status, extractServerMessage(err), kind);
  }
  // Network / abort — surfaces as a server-class failure for chip UX.
  return new UploadError(0, String((err as Error)?.message ?? err), 'network');
}

/**
 * Pull the daemon's error message out of an HttpErrorResponse. The
 * daemon returns ``{"detail": {"message": "..."}}`` for FastAPI's
 * validation surfaces and ``{"detail": "<string>"}`` for older
 * plain-string errors. Returns the full JSON when no recognizable
 * shape is found so the chip retry affordance shows what the server
 * actually said.
 */
function extractServerMessage(err: HttpErrorResponse): string {
  const detail = err.error?.detail;
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object') {
    if (typeof detail.message === 'string') return detail.message;
    return JSON.stringify(detail);
  }
  if (typeof err.error === 'string') return err.error;
  return err.statusText || `HTTP ${err.status}`;
}

/**
 * Bridge an ``AbortSignal`` to an Observable<void> that fires once
 * when the signal aborts. Returns ``null`` when no signal is
 * supplied so callers can skip the ``takeUntil`` wire-up.
 */
function abortSignalToObservable(signal?: AbortSignal): Observable<void> | null {
  if (!signal) return null;
  if (signal.aborted) {
    const s$ = new Subject<void>();
    s$.next();
    s$.complete();
    return s$;
  }
  return new Observable<void>(subscriber => {
    const onAbort = () => {
      subscriber.next();
      subscriber.complete();
    };
    signal.addEventListener('abort', onAbort, { once: true });
    return () => signal.removeEventListener('abort', onAbort);
  });
}

/** True when ``err`` is an HttpClient abort (the chip removed the in-flight upload). */
function isAbortError(err: unknown): boolean {
  if (!err) return false;
  if (err instanceof HttpErrorResponse) {
    // HttpClient maps AbortError to status 0 with statusText "Unknown Error".
    return err.status === 0 && (err.statusText === 'Unknown Error' || err.statusText === '');
  }
  const name = (err as { name?: string })?.name;
  return name === 'AbortError';
}
