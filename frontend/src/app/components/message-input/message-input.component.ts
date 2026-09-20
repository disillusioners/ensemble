import { Component, Input, Output, EventEmitter, ViewChild, ElementRef, signal, computed, input, effect, inject, DestroyRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Subject } from 'rxjs';
import type { CommandDefinition, InstanceStatus, JobQueue } from '../../models';
import { ApiService } from '../../services/api.service';
import { CommandRegistryService } from '../../services/command-registry.service';
import { ImageUploadService, UploadedImage, UploadError } from '../../services/image-upload.service';
import {
  filterCommandsByPrefix,
  isSlashCommandTrigger,
  moveHighlight,
  slashAcceptText,
  slashCommandQuery,
  slashOptionId,
  slashPaletteLiveMessage,
} from './slash-command-palette.util';

/**
 * Per-chip upload status. ``idle`` is the default; ``uploading`` and
 * ``uploaded`` flow through the send pipeline; ``failed`` keeps the
 * chip in place with a retry affordance and blocks send (per
 * architect amendment #16's block-send default scope, phase4-plan
 * Task 5).
 */
export type UploadStatus = 'idle' | 'uploading' | 'uploaded' | 'failed';

/**
 * Composer phase state (amendment #17, phase4-plan Task 12). Drives
 * the inline conversion-wait spinner overlay on the send button.
 *
 * - ``idle`` — no active send in flight; canSend() is the gate.
 * - ``uploading`` — at least one chip is uploading to /api/tmp_images.
 * - ``converting`` — all uploads finished; the messages POST is in
 *   flight, the daemon is doing sync-in-POST image→text conversion.
 *   The send button shows "Converting images, this may take a minute"
 *   inline.
 * - ``sending`` — pure text-only send in flight (no image-refs path).
 */
export type ComposerPhaseState = 'idle' | 'uploading' | 'converting' | 'sending';

export interface MessagePayload {
  content: string;
  images?: string[];  // optional, not required
  /**
   * Phase 4 (clipboard-image-chat): ref-form image refs
   * (``/api/tmp_images/<32hex>``). XOR-sibling of ``images`` — the FE
   * never sends both fields non-empty. The chat component's
   * ``api.sendMessage`` call site threads this sibling through.
   */
  image_refs?: string[];
  queue_id?: string | null;
  /**
   * Defect #5 retry path (2026-08-31, must-fix #1): when set, this send
   * is a retry of a previously-failed bubble (id-keyed). The chat
   * component's success handler uses this to clear the failed marker
   * on the originating bubble — the clear happens in the success path,
   * NOT synchronously in the retry handler, so a cooldown-blocked
   * retry preserves the user's error state (no POST went out → the
   * bubble keeps its ``failed`` marker). Internal-only; the message
   * input component never sets this.
   */
  retry_of_message_id?: string;
}

/**
 * One image attached to the composer. ``refUrl`` is the canonical
 * daemon-side URL emitted by ``ImageUploadService``; ``uploadStatus``
 * drives the chip's status pill (⏳/✓/⚠); ``uploadError`` is the
 * server-verbatim message captured for the retry affordance.
 *
 * The original ``File`` is cached on a module-scoped ``WeakMap``
 * (NOT on this object) to keep the signal value JSON-serializable
 * (plan Risk #6).
 */
interface FilePreview {
  id: string;
  dataUrl: string;
  name: string;
  size: number;
  refUrl?: string;
  imageId?: string;
  uploadStatus?: UploadStatus;
  uploadError?: string;
}

/**
 * Module-scoped File cache keyed by ``FilePreview.id``. The
 * ``File`` reference itself is stable for the lifetime of the
 * composition; the cache lives for the lifetime of the page. The
 * chip's per-image upload POST uses the cached File so we don't
 * re-decode the data URL back to bytes on send (plan Risk #6).
 */
const FILE_CACHE = new WeakMap<FilePreview, File>();

@Component({
  selector: 'app-message-input',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './message-input.html',
  styleUrls: ['./message-input.scss']
})
export class MessageInputComponent {
  private readonly apiService = inject(ApiService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly imageUpload = inject(ImageUploadService);

  /**
   * Per-chip abort subjects — one Subject per in-flight upload. The
   * key is the FilePreview id (UUID). ``removeImage`` fires the
   * subject to cancel the HttpClient subscription mid-flight (Task 7
   * acceptance). Cleared on terminal state (uploaded / failed).
   */
  private readonly uploadAborters = new Map<string, Subject<void>>();

  /**
   * Composer phase state (amendment #17, Task 12). Drives the
   * inline conversion-wait spinner overlay. Read by the template.
   */
  protected readonly phaseState = signal<ComposerPhaseState>('idle');

  @ViewChild('textarea') textareaRef!: ElementRef<HTMLTextAreaElement>;
  @ViewChild('fileInput') fileInputRef!: ElementRef<HTMLInputElement>;

  // Use input() for reactive signal-based inputs
  readonly disabled = input(false);
  readonly agentColor = input('developer');
  readonly instanceStatus = input<InstanceStatus | null>(null);
  readonly projectId = input<string | null>(null);
  @Output() sendMessage = new EventEmitter<MessagePayload>();
  @Output() pauseInstance = new EventEmitter<void>();
  @Output() resumeInstance = new EventEmitter<string>();  // emits message text (or empty string for default)

  message = signal('');
  images = signal<FilePreview[]>([]);
  isDragOver = signal(false);
  validationError = signal<string | null>(null);

  // ── Slash-command autocomplete palette (phase2-plan.md Task 10) ───────
  //
  // The palette brain lives in ``slash-command-palette.util.ts`` (pure,
  // logic-mirror tested); this block is the thin signal wiring. Behavior:
  //   - open while the whole input is a bare ``/fragment`` (``//`` escape
  //     and whitespace never trigger — see isSlashCommandTrigger);
  //   - prefix filter as you type, case-insensitive; no matches → subtle
  //     "No matching command" hint (palette stays open so Escape keeps a
  //     consistent target);
  //   - ArrowUp/ArrowDown move the highlight with wrap-around;
  //   - Enter accepts the highlighted command AND sends it (equivalent to
  //     typing the full command + Enter — routes through the same paused→
  //     handleResume / else handleSubmit dispatch as a plain Enter);
  //   - Tab / option-click accept (insert ``/name ``) WITHOUT sending;
  //   - Escape dismisses until the next input event.
  private readonly commandRegistry = inject(CommandRegistryService);

  /** Registered commands for the dropdown (read-only registry view). */
  protected readonly commandOptions = this.commandRegistry.commands;

  /** Escape-dismissal latch; cleared by the next ``onInput``. */
  protected readonly slashDismissed = signal(false);
  protected readonly slashHighlightRaw = signal(0);

  /** Case-insensitive prefix matches for the current input. */
  protected readonly slashMatches = computed<CommandDefinition[]>(() => {
    const query = slashCommandQuery(this.message());
    if (query === null) return [];
    return filterCommandsByPrefix(this.commandOptions(), query);
  });

  /**
   * Highlight clamped into the current match list. ``onInput`` resets the
   * raw index whenever the list can change; the clamp is belt-and-braces.
   */
  protected readonly slashActiveIndex = computed(() => {
    const count = this.slashMatches().length;
    if (count === 0) return -1;
    return Math.min(this.slashHighlightRaw(), count - 1);
  });

  /** aria-activedescendant target — the highlighted option's DOM id. */
  protected readonly slashActiveDescendant = computed(() => {
    const index = this.slashActiveIndex();
    return index >= 0 ? slashOptionId(index) : null;
  });

  /** Polite live-region announcement for open / match-count changes. */
  protected readonly slashLiveMessage = computed(() =>
    slashPaletteLiveMessage(this.isSlashPaletteOpen(), this.slashMatches().length),
  );

  /**
   * Palette visibility: input is a bare slash-command fragment, the user
   * has not Escape-dismissed it, and the input is not disabled.
   */
  protected readonly isSlashPaletteOpen = computed(() =>
    !this.disabled() && !this.slashDismissed() && isSlashCommandTrigger(this.message()),
  );

  /** DOM id builder exposed for the template option loop. */
  protected readonly slashOptionId = slashOptionId;


  /**
   * Returns true when the instance is actively running and should show a Pause button.
   * Show Pause for 'running', 'waiting_children', or 'queued' states.
   */
  readonly isInstanceRunning = computed(() => {
    const status = this.instanceStatus();
    return status === 'running' || status === 'waiting_children' || status === 'queued';
  });

  /**
   * Returns true when a message can be INJECTED into an active instance
   * (running or waiting_children). Excludes 'queued' since a queued instance
   * hasn't started yet — use isInstanceRunning() for that case.
   *
   * When true, the UI shows text input + send + pause buttons simultaneously.
   */
  readonly canInject = computed(() => {
    const status = this.instanceStatus();
    return status === 'running' || status === 'waiting_children';
  });

  /**
   * Returns true when the instance is paused and should show a Resume button.
   */
  readonly isInstancePaused = computed(() => {
    const status = this.instanceStatus();
    return status === 'paused';
  });

  protected readonly MAX_IMAGES = 3;
  protected readonly MAX_IMAGE_SIZE = 10 * 1024 * 1024;

  /**
   * Phase 4 (clipboard-image-chat) round-2 / O3 — trimmed to the
   * daemon 4-type allowlist (architect amendment #1, matching
   * ``daemon/models/tmp_image.py`` 4-type ``_MAGIC_SIGNATURES``).
   * Dropped types (``image/bmp``, ``image/tiff``) surface the
   * typed "Content type rejected" validation error via
   * ``showValidationError`` and the chip never enters a stuck-422
   * state. Identical to the daemon: ``image/png``, ``image/jpeg``,
   * ``image/jpg`` (normalized to ``image/jpeg`` server-side),
   * ``image/gif``, ``image/webp``.
   */
  private readonly ACCEPTED_TYPES = [
    'image/png',
    'image/jpeg',
    'image/jpg',
    'image/gif',
    'image/webp',
  ];

  agentColorMap: Record<string, string> = {
    'leader': '#f59e0b',
    'developer': '#10a7f7',
    'coder': '#10a7f7',  // backward compat for cached responses
    'reviewer': '#8b5cf6',
    'charter': '#3b82f6',
  };

  readonly color = computed(() => {
    return this.agentColorMap[this.agentColor()] || '#10a7f7';
  });

  /**
   * Send-eligibility gate. True when:
   * - the instance is not disabled AND
   * - the user has something to send (text or images) AND
   * - no chip is currently uploading (Task 5 acceptance: send button
   *   ``disabled`` while any preview has ``uploadStatus === 'uploading'``).
   */
  readonly canSend = computed(() => {
    if (this.disabled()) return false;
    const hasContent = !!this.message().trim() || this.images().length > 0;
    if (!hasContent) return false;
    const anyUploading = this.images().some(img => img.uploadStatus === 'uploading');
    return !anyUploading;
  });

  queues = signal<JobQueue[]>([]);
  selectedQueueId = signal<string | null>(null);

  readonly isIdle = computed(() => {
    const status = this.instanceStatus();
    return status === null || status === 'idle';
  });

  /**
   * Returns true when the queue selector dropdown should be visible.
   *
   * The backend routes messages for non-active states (idle, completed, error,
   * failed, terminated, waiting, null/undefined) through the NORMAL queue-routing
   * branch, so the caller can choose a queue. Active states (running,
   * waiting_children, paused) and the queued state do NOT use queue routing
   * (injection / resume / already-queued paths), so the selector is hidden.
   */
  readonly isQueueSelectorVisible = computed(() => {
    const status = this.instanceStatus();
    if (!status) return true;
    const hiddenStates = ['running', 'waiting_children', 'paused', 'queued'];
    return !hiddenStates.includes(status);
  });

  constructor() {
    effect(() => {
      const projectId = this.projectId();
      this.queues.set([]);
      this.selectedQueueId.set(projectId ? localStorage.getItem(`ensemble-queue-select-${projectId}`) : null);
      if (!projectId) return;
      const requestProjectId = projectId;
      this.apiService.getQueues(requestProjectId).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
        next: response => {
          if (this.projectId() !== requestProjectId) return;
          this.queues.set(response.queues);
          const stored = this.selectedQueueId();
          const selected = (stored && response.queues.some(q => q.queue_id === stored))
            ? stored
            : response.queues.find(q => q.queue_name === 'system_parallel_queue')?.queue_id ?? response.queues[0]?.queue_id ?? null;
          this.selectedQueueId.set(selected);
        },
        error: () => {
          if (this.projectId() === requestProjectId) this.queues.set([]);
        }
      });
    });
  }

  onQueueChange(queueId: string): void {
    this.selectedQueueId.set(queueId);
    const projectId = this.projectId();
    if (!projectId) return;
    try {
      localStorage.setItem(`ensemble-queue-select-${projectId}`, queueId);
    } catch {
      // Ignore — private browsing / quota exceeded; non-critical
    }
  }

  /**
   * Phase 4 (clipboard-image-chat) async handleSubmit. Two-stage:
   *   1. Gather every preview that lacks ``refUrl``; upload each
   *      in parallel via ``ImageUploadService`` (Task 5).
   *   2. Block-send default — if ANY upload fails, do NOT emit; the
   *      chip transitions to ``failed`` with a retry affordance
   *      (Task 10) and a banner summary surfaces.
   *   3. On all-success, emit a payload with ``image_refs`` populated
   *      (NOT ``images``). The chat component threads ``image_refs``
   *      through ``api.sendMessage`` (XOR with ``images``).
   *
   * Phase state transitions (Task 12, amendment #17):
   *   idle → uploading (any chip uploading) → converting (all
   *   uploads finished, the messages POST is in flight). The send
   *   button shows the inline "Converting images, this may take a
   *   minute" copy while ``phaseState === 'converting'``.
   */
  async handleSubmit(): Promise<void> {
    const trimmedMessage = this.message().trim();
    if ((!trimmedMessage && this.images().length === 0) || this.disabled()) return;

    const previews = this.images();
    const pending = previews.filter(p => !p.refUrl && p.uploadStatus !== 'uploaded');

    if (pending.length === 0) {
      // No uploads needed — emit synchronously (no image refs path).
      this.phaseState.set('sending');
      const payload: MessagePayload = {
        content: trimmedMessage,
        queue_id: this.isQueueSelectorVisible() ? this.selectedQueueId() : null,
      };
      this.sendMessage.emit(payload);
      // Reset phase state on the next microtask — the chat
      // component's success / error handler runs synchronously after
      // the emit, but we don't want to hold "sending" forever if
      // the parent doesn't reset it. The parent's response handler
      // calls ``clearInput`` on success which we'll observe via an
      // effect — for the no-image path this is moot (no converting
      // spinner would ever show). Reset on next tick.
      queueMicrotask(() => this.phaseState.set('idle'));
      return;
    }

    // Upload phase.
    this.phaseState.set('uploading');
    const settled = await Promise.allSettled(
      pending.map(preview => this.uploadPreview(preview)),
    );

    // Block-send default: any failure → no emit, chip already marked failed.
    const anyFailed = settled.some(r => r.status === 'rejected');
    if (anyFailed) {
      const failedCount = settled.filter(r => r.status === 'rejected').length;
      this.showValidationError(
        failedCount === 1
          ? 'Upload failed — retry the chip to send.'
          : `${failedCount} uploads failed — retry to send.`,
      );
      this.phaseState.set('idle');
      return;
    }

    // All uploads succeeded — collect the ref URLs and emit.
    this.phaseState.set('converting');
    const refs = this.images().map(img => img.refUrl).filter((u): u is string => !!u);
    const payload: MessagePayload = {
      content: trimmedMessage,
      image_refs: refs,
      queue_id: this.isQueueSelectorVisible() ? this.selectedQueueId() : null,
    };
    this.sendMessage.emit(payload);
    // The parent (chat component) toggles phaseState back to 'idle'
    // by calling ``clearInput`` on success or staying in 'converting'
    // during the long blocking POST. We re-arm to 'idle' here as a
    // safety net; the parent's response handler resets it again
    // before the user sees the spinner disappear.
    queueMicrotask(() => this.phaseState.set('idle'));
  }

  /**
   * Upload a single preview's cached ``File``. Resolves with the
   * UploadedImage on success; rejects with the typed ``UploadError``
   * on failure. Updates the chip's ``uploadStatus`` along the way
   * so the template can render the status pill.
   *
   * The AbortController is owned here so ``removeImage`` can cancel
   * an in-flight upload (Task 7 acceptance).
   */
  private uploadPreview(preview: FilePreview): Promise<UploadedImage> {
    const file = FILE_CACHE.get(preview);
    if (!file) {
      // The cache lost the file (e.g. GC pressure); treat as a
      // permanent failure so the user sees the retry affordance.
      const err = new UploadError(0, 'Lost file reference', 'network', 'File reference lost');
      return Promise.reject(err);
    }

    this.updatePreviewStatus(preview.id, { uploadStatus: 'uploading', uploadError: undefined });
    const controller = new AbortController();
    const aborter = new Subject<void>();
    this.uploadAborters.set(preview.id, aborter);

    return new Promise<UploadedImage>((resolve, reject) => {
      // Bridge the AbortController and the rxjs Subject so
      // ``removeImage`` can cancel via either seam.
      const sub = aborter.subscribe(() => controller.abort());
      this.imageUpload.upload(file, { signal: controller.signal }).subscribe({
        next: result => {
          sub.unsubscribe();
          this.uploadAborters.delete(preview.id);
          this.updatePreviewStatus(preview.id, {
            uploadStatus: 'uploaded',
            refUrl: result.ref_url,
            imageId: result.image_id,
            uploadError: undefined,
          });
          resolve(result);
        },
        error: (err: unknown) => {
          sub.unsubscribe();
          this.uploadAborters.delete(preview.id);
          if (err instanceof UploadError && err.serverMessage === 'aborted') {
            // The chip was removed — silently resolve (no error UI).
            resolve(undefined as unknown as UploadedImage);
            return;
          }
          const message =
            err instanceof UploadError ? err.serverMessage : String((err as Error)?.message ?? err);
          this.updatePreviewStatus(preview.id, {
            uploadStatus: 'failed',
            uploadError: message,
          });
          reject(err);
        },
      });
    });
  }

  /**
   * Per-chip retry affordance (Task 5 acceptance, Task 10). When a
   * chip is in ``failed`` state, clicking retry re-runs the upload
   * with the cached ``File`` — no re-pick needed.
   */
  retryUpload(previewId: string): void {
    const preview = this.images().find(p => p.id === previewId);
    if (!preview) return;
    if (preview.uploadStatus !== 'failed') return;
    this.uploadPreview(preview).catch(() => {
      // Errors are already surfaced on the chip via ``uploadError``;
      // swallow here so the retry click doesn't bubble a runtime
      // exception.
    });
  }

  /**
   * Helper — patch the preview strip in-place by id (returns a NEW
   * array reference so the signal triggers). Used by the upload
   * state machine to keep the chip's ``uploadStatus`` /
   * ``uploadError`` in sync without disturbing the rest of the
   * signal.
   */
  private updatePreviewStatus(
    previewId: string,
    patch: Partial<Pick<FilePreview, 'uploadStatus' | 'uploadError' | 'refUrl' | 'imageId'>>,
  ): void {
    this.images.update(imgs =>
      imgs.map(img =>
        img.id === previewId ? { ...img, ...patch } : img,
      ),
    );
  }

  handleResume(): void {
    const text = this.message().trim();
    this.resumeInstance.emit(text);  // empty string means "resume" (backend default)
    // Clear immediately on Enter - parent will restore on API error if needed
    this.clearInput();
  }

  clearInput(): void {
    this.message.set('');
    this.images.set([]);
    if (this.textareaRef) {
      this.textareaRef.nativeElement.style.height = 'auto';
    }
  }

  onInput(event: Event): void {
    const target = event.target as HTMLTextAreaElement;
    this.message.set(target.value);
    // Typing always re-arms the palette: clear Escape-dismissal and reset
    // the highlight to the first match (the match list just changed).
    this.slashDismissed.set(false);
    this.slashHighlightRaw.set(0);

    // Auto-resize textarea
    target.style.height = 'auto';
    target.style.height = `${Math.min(target.scrollHeight, 150)}px`;
  }

  /**
   * Shared Enter dispatch — palette-free Enter semantics, unchanged by
   * Task 10: 1. PAUSED → resume with the message; 2. RUNNING/WAITING_CHILDREN
   * (canInject) → inject into the active stream; 3. otherwise → normal send.
   */
  private dispatchEnterAction(): void {
    if (this.isInstancePaused()) {
      this.handleResume();
    } else {
      this.handleSubmit();
    }
  }

  onKeydownEnter(event: Event): void {
    const keyboardEvent = event as KeyboardEvent;
    if (keyboardEvent.shiftKey) return; // Allow newline
    event.preventDefault();
    // Palette open with matches → Enter ACCEPTS the highlighted command
    // and sends it (complete-then-send, equivalent to typing the full
    // command + Enter). Zero matches or closed → normal send path.
    if (this.isSlashPaletteOpen() && this.slashMatches().length > 0) {
      this.acceptSlashCommand(this.slashHighlightedDef(), true);
      return;
    }
    // Priority:
    // 1. PAUSED → resume with the message
    // 2. RUNNING/WAITING_CHILDREN (canInject) → inject into active stream
    // 3. Otherwise (IDLE/other) → normal send
    // Both cases 2 and 3 flow through handleSubmit; the parent component
    // routes to the appropriate endpoint based on instance state.
    this.dispatchEnterAction();
  }

  /**
   * ArrowUp/ArrowDown navigation. Hijacks the keys ONLY while the palette
   * is open with matches — otherwise the textarea keeps its native cursor
   * movement (non-regression).
   */
  onSlashArrow(event: Event, direction: -1 | 1): void {
    if (!this.isSlashPaletteOpen() || this.slashMatches().length === 0) return;
    event.preventDefault();
    this.slashHighlightRaw.update(i => moveHighlight(i, this.slashMatches().length, direction));
  }

  /** Escape dismisses the palette until the next input event. */
  onSlashEscape(event: Event): void {
    if (!this.isSlashPaletteOpen()) return;
    event.preventDefault();
    this.slashDismissed.set(true);
  }

  /** Tab completes the highlighted command (insert only, no send). */
  onSlashTab(event: Event): void {
    const keyboardEvent = event as KeyboardEvent;
    if (keyboardEvent.shiftKey) return; // Shift+Tab keeps normal focus traversal
    if (!this.isSlashPaletteOpen() || this.slashMatches().length === 0) return;
    event.preventDefault();
    this.acceptSlashCommand(this.slashHighlightedDef(), false);
  }

  private slashHighlightedDef(): CommandDefinition {
    return this.slashMatches()[this.slashActiveIndex()];
  }

  /**
   * Accept the given command. With ``send`` (palette-Enter) the SAME
   * dispatch as a plain Enter runs — paused resumes, otherwise the normal
   * send path — so accepting from the palette is exactly "typed the full
   * command + Enter".
   *
   * Insert form: ``/name `` (canonical name + one trailing space), caret
   * at the end, focus kept in the textarea; the trailing space ends the
   * bare-command trigger so the palette closes.
   *
   * EXCEPTION (byte-identical non-regression): palette-Enter with the
   * input ALREADY exactly ``/name`` sends the typed value verbatim — no
   * insert, no rewrite. The input therefore keeps ``/compact`` (not
   * ``/compact ``) when the command is rejected (SC5/SC14 e2e keep the
   * text for retry), exactly as pre-palette.
   */
  acceptSlashCommand(def: CommandDefinition, send: boolean): void {
    const alreadyCompleteTyped = send && this.message() === `/${def.name}`;
    if (!alreadyCompleteTyped) {
      const text = slashAcceptText(def);
      this.message.set(text);
      const el = this.textareaRef?.nativeElement;
      if (el) {
        el.value = text;
        el.focus();
        el.setSelectionRange(text.length, text.length);
      }
    }
    if (send) this.dispatchEnterAction();
  }

  /** Option click accepts (insert only, no send). Never steals focus. */
  onSlashOptionClick(event: MouseEvent, def: CommandDefinition): void {
    event.preventDefault();
    this.acceptSlashCommand(def, false);
  }

  /** Hover follows the highlight (no focusable children in the palette). */
  onSlashOptionHover(index: number): void {
    this.slashHighlightRaw.set(index);
  }

  onAttachClick(): void {
    this.fileInputRef.nativeElement.click();
  }

  onFileSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    const files = input.files;
    if (files) {
      this.processFiles(Array.from(files));
    }
    input.value = '';
  }

  private convertToBase64(file: File): Promise<string> {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result as string);
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });
  }

  private showValidationError(message: string): void {
    this.validationError.set(message);
    setTimeout(() => this.validationError.set(null), 4000);
  }

  /**
   * Public inline-error surface for slash-command validation (Phase 2 /
   * Task 5). The chat component owns the command send flow, but the inline
   * validation UI (and its auto-dismiss timer) lives HERE in the input —
   * same pattern as ``showValidationError`` (4s default auto-dismiss).
   *
   * ``durationMs`` is overridable: rejection guidance (e.g. the
   * terminal-instance hint rendered VERBATIM from the ack ``detail``)
   * needs more reading time than a 4s flash, so the chat component passes
   * a longer window for rejected acks.
   *
   * Returns the dismiss timer handle so tests (and the component) can
   * verify auto-dismiss behavior deterministically.
   */
  showCommandValidationError(message: string, durationMs = 4000): ReturnType<typeof setTimeout> {
    this.validationError.set(message);
    return setTimeout(() => this.validationError.set(null), durationMs);
  }

  /**
   * Public sink for paste / drag-drop / picker. Validates every file
   * against the 4-type allowlist, the count cap, and the per-file
   * size cap; caches the original ``File`` on the module-scoped
   * WeakMap (Task 6 acceptance — avoid a re-decode round-trip on
   * send). On success, appends a preview chip in ``idle`` upload
   * state.
   */
  async processFiles(files: File[]): Promise<void> {
    for (const file of files) {
      // Check count limit
      if (this.images().length >= this.MAX_IMAGES) {
        this.showValidationError('You can only attach up to ' + this.MAX_IMAGES + ' images.');
        break;
      }

      // Check file type — round-2 / O3: dropped bmp/tiff. The daemon
      // 4-type allowlist is the source of truth; the FE mirror is
      // here only for the instant client-side UX (no chip-then-422).
      if (!this.ACCEPTED_TYPES.includes(file.type)) {
        this.showValidationError(
          'Unsupported image type. Please use PNG, JPEG, GIF, or WebP.',
        );
        continue;
      }

      // Check file size
      if (file.size > this.MAX_IMAGE_SIZE) {
        const sizeMB = (file.size / (1024 * 1024)).toFixed(1);
        this.showValidationError('File "' + file.name + '" is ' + sizeMB + 'MB. Maximum is 10MB.');
        continue;
      }

      try {
        const dataUrl = await this.convertToBase64(file);
        const filePreview: FilePreview = {
          id: crypto.randomUUID(),
          dataUrl,
          name: file.name,
          size: file.size,
          uploadStatus: 'idle',
        };
        FILE_CACHE.set(filePreview, file);
        this.images.update(imgs => [...imgs, filePreview]);
      } catch (error) {
        this.showValidationError('Failed to read file "' + file.name + '".');
      }
    }
  }

  /**
   * Phase 4 (clipboard-image-chat) — chip removal (Task 7). If the
   * chip has an in-flight upload, abort the underlying HttpClient
   * via the per-chip aborter subject. If the chip has a ``refUrl``
   * (already uploaded), fire a best-effort DELETE so the daemon can
   * release storage eagerly — the 30-day cleanup sweep is the
   * safety net.
   */
  removeImage(id: string): void {
    const aborter = this.uploadAborters.get(id);
    if (aborter) {
      aborter.next();
      aborter.complete();
      this.uploadAborters.delete(id);
    }

    const preview = this.images().find(p => p.id === id);
    if (preview?.imageId) {
      this.imageUpload.deleteImage(preview.imageId).subscribe({
        error: () => {
          // Best-effort — log and swallow.
          console.warn('[ImageUpload] eager DELETE failed for', preview.imageId);
        },
      });
    }

    this.images.update(imgs => imgs.filter(img => img.id !== id));
  }

  onDragOver(event: DragEvent): void {
    event.preventDefault();
    this.isDragOver.set(true);
  }

  onDragLeave(event: DragEvent): void {
    event.preventDefault();
    this.isDragOver.set(false);
  }

  onDrop(event: DragEvent): void {
    event.preventDefault();
    this.isDragOver.set(false);

    const files = event.dataTransfer?.files;
    if (files) {
      this.processFiles(Array.from(files));
    }
  }

  /**
   * Phase 4 (clipboard-image-chat) — paste handler. Walks
   * ``clipboardData.items``, picks ``kind === 'file'`` items whose
   * ``type`` is in the 4-type allowlist, builds ``File[]``, and
   * routes through the existing ``processFiles`` sink so the
   * preview-strip / caps / validation are shared with picker /
   * drag-drop (Task 3 acceptance — uniform behavior).
   *
   * **Text-only pastes pass through UNTOUCHED.** When zero images
   * are extracted, this handler does NOT call ``preventDefault`` so
   * the browser's default paste-into-textarea behavior continues to
   * work (Task 3 acceptance — non-regression).
   */
  onPaste(event: ClipboardEvent): void {
    const files = extractPastedImages(event);
    if (files.length === 0) return;
    event.preventDefault();
    this.processFiles(files);
  }
}

/**
 * Pure helper — walks ``ClipboardEvent.clipboardData.items`` and
 * returns the image files that pass the 4-type allowlist. Exported
 * so the logic-mirror spec (``message-input.paste.spec.ts``) can
 * exercise mixed text + image items, text-only pastes, and dropped
 * (rejected) types without spinning up Angular TestBed.
 *
 * ``getAsFile`` on a ``DataTransferItem`` returns ``null`` for
 * non-file items (string items) — we skip those. The allowlist is
 * deliberately NOT enforced here — the sink
 * (``MessageInputComponent.processFiles``) owns the single
 * validation surface (picker / drop / paste all funnel through it
 * and see the same error copy).
 */
export function extractPastedImages(event: ClipboardEvent): File[] {
  const items = event.clipboardData?.items;
  if (!items) return [];
  const out: File[] = [];
  for (let i = 0; i < items.length; i++) {
    const item = items[i];
    if (item.kind !== 'file') continue;
    const file = item.getAsFile();
    if (!file) continue;
    // Filter to image MIME types — the per-file size + 4-type
    // allowlist check happens in processFiles so all three inputs
    // (paste / drop / picker) see the same validation surface and
    // error copy. We accept ANY image MIME here so the user sees the
    // typed "Unsupported image type" rejection for bmp/tiff rather
    // than a silent no-op.
    if (!file.type.startsWith('image/')) continue;
    out.push(file);
  }
  return out;
}
