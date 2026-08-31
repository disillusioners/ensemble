import { Component, Input, Output, EventEmitter, ViewChild, ElementRef, signal, computed, input, effect, inject, DestroyRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import type { CommandDefinition, InstanceStatus, JobQueue } from '../../models';
import { ApiService } from '../../services/api.service';
import { CommandRegistryService } from '../../services/command-registry.service';
import {
  filterCommandsByPrefix,
  isSlashCommandTrigger,
  moveHighlight,
  slashAcceptText,
  slashCommandQuery,
  slashOptionId,
  slashPaletteLiveMessage,
} from './slash-command-palette.util';

export interface MessagePayload {
  content: string;
  images?: string[];  // optional, not required
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

interface FilePreview {
  id: string;
  dataUrl: string;
  name: string;
  size: number;
}

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
  private readonly ACCEPTED_TYPES = [
    'image/png',
    'image/jpeg',
    'image/gif',
    'image/webp',
    'image/bmp',
    'image/tiff'
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

  readonly canSend = computed(() => {
    return (!!this.message().trim() || this.images().length > 0) && !this.disabled();
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

  handleSubmit(): void {
    const trimmedMessage = this.message().trim();
    if ((!trimmedMessage && this.images().length === 0) || this.disabled()) return;

    const payload: MessagePayload = {
      content: trimmedMessage,
      images: this.images().map(img => img.dataUrl),
      queue_id: this.isQueueSelectorVisible() ? this.selectedQueueId() : null
    };

    this.sendMessage.emit(payload);
    // Do NOT clear message/images here — parent calls clearInput() on API success
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

  async processFiles(files: File[]): Promise<void> {
    for (const file of files) {
      // Check count limit
      if (this.images().length >= this.MAX_IMAGES) {
        this.showValidationError('You can only attach up to ' + this.MAX_IMAGES + ' images.');
        break;
      }

      // Check file type
      if (!this.ACCEPTED_TYPES.includes(file.type)) {
        this.showValidationError('Unsupported image type. Please use PNG, JPEG, GIF, WebP, BMP, or TIFF.');
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
          size: file.size
        };
        this.images.update(imgs => [...imgs, filePreview]);
      } catch (error) {
        this.showValidationError('Failed to read file "' + file.name + '".');
      }
    }
  }

  removeImage(id: string): void {
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
}
