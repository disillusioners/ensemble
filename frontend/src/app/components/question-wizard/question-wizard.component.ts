import { Component, input, signal, computed, inject, effect, DestroyRef } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../../services/api.service';
import { SseService } from '../../services/sse.service';
import type { Question, QuestionPack } from '../../models/question.model';

/**
 * Question Wizard (Phase 4 / Question Tool)
 *
 * Renders a wizard-style UI driven by the ``question_pack`` SSE event. The
 * backend emits ``question_pack`` whenever an agent prompts for user input
 * (status='pending') and again right after the answers are consumed
 * (status='answered') so the wizard can hide itself.
 *
 * Visibility is driven EXCLUSIVELY by the ``questionPack`` signal — NOT by
 * ``status_change``. The pause cascade can cancel the graph task before any
 * post-commit SSE code runs, so the status_change→paused event may never
 * fire (F3). The question_pack event is emitted by the tool itself before
 * the cascade and is the only reliable pause-UI signal.
 *
 * State is purely signal-based; no BehaviorSubject / NgRx. Updates are
 * NON-OPTIMISTIC — the wizard waits for the ``answerQuestions`` API
 * response before clearing the local submitting flag, and clears itself
 * only when the ``question_pack`` event updates ``status='answered'``.
 *
 * Instance switching is handled defensively:
 * - The constructor effect resets local wizard state on each new instanceId.
 * - HTTP callbacks guard against stale responses with a captured
 *   ``targetInstanceId`` check, mirroring ``saveComment`` /
 *   ``toggleSubtask`` in TodoListComponent.
 */
@Component({
  selector: 'app-question-wizard',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './question-wizard.component.html',
  styleUrl: './question-wizard.component.scss'
})
export class QuestionWizardComponent {
  private readonly sseService = inject(SseService);
  private readonly api = inject(ApiService);
  private readonly destroyRef = inject(DestroyRef);

  /** The instance this wizard answers for. Required. */
  instanceId = input.required<string>();

  // ---- Wizard state ---------------------------------------------------
  currentIndex = signal(0);
  answers = signal<Record<string, string>>({});
  isSubmitting = signal(false);
  /** Last submission error message; null when no error is being surfaced. */
  submitError = signal<string | null>(null);
  /** True while the dismiss HTTP request is in-flight. */
  isDismissing = signal(false);
  /** Last dismiss error message; null when no error is being surfaced. */
  dismissError = signal<string | null>(null);
  /**
   * Closure-scoped dedupe tracker for the pack effect (previously). Promoted
   * to a class field so the instanceId effect can reset it on instance
   * switch. Without this reset, two instances with packs sharing the same
   * ``created_at`` would have the second one's arrival incorrectly deduped
   * as a re-delivery.
   */
  private lastSeenPackCreatedAt: string | null = null;

  // ---- Derived from the shared SSE signal -----------------------------
  /** Latest question_pack payload. Null when no pack is active. */
  pack = computed<QuestionPack | null>(() => this.sseService.questionPack());

  /** Question list for the current pack; empty when no pack / hidden. */
  questions = computed<Question[]>(() => this.pack()?.questions ?? []);

  /** The question currently shown in the wizard. */
  currentQuestion = computed<Question | null>(() => {
    const qs = this.questions();
    if (qs.length === 0) return null;
    const idx = this.currentIndex();
    if (idx < 0 || idx >= qs.length) return null;
    return qs[idx];
  });

  /**
   * Wizard visibility: only show when there's an active pack in 'pending'
   * state. 'answered' (or null) auto-hides the wizard via the ``@if``
   * wrapper in the template.
   */
  isVisible = computed<boolean>(() => this.pack()?.status === 'pending');

  /** True when the user is on the last question of the pack. */
  isLastQuestion = computed<boolean>(() => {
    const qs = this.questions();
    return qs.length > 0 && this.currentIndex() === qs.length - 1;
  });

  /** True when the current question has a valid answer (enables Next). */
  canProceed = computed<boolean>(() => {
    const q = this.currentQuestion();
    if (!q) return false;
    const a = (this.answers()[q.id] ?? '').trim();
    // Required questions must have a non-empty answer; optional ones can
    // always proceed.
    return !q.required || a.length > 0;
  });

  /**
   * True when every required question has a non-empty answer — gates the
   * final Submit button.
   */
  canSubmit = computed<boolean>(() => {
    const qs = this.questions();
    if (qs.length === 0) return false;
    const answered = this.answers();
    for (const q of qs) {
      if (!q.required) continue;
      const a = (answered[q.id] ?? '').trim();
      if (a.length === 0) return false;
    }
    return true;
  });

  constructor() {
    // Reset wizard state when the user switches instances. Mirrors the
    // TodoListComponent constructor effect that also tracks instanceId.
    effect(() => {
      this.instanceId(); // track
      this.currentIndex.set(0);
      this.answers.set({});
      this.isSubmitting.set(false);
      this.submitError.set(null);
      this.isDismissing.set(false);
      this.dismissError.set(null);
      // Reset the dedupe tracker so a new instance's pack is not mistaken
      // for an SSE re-delivery of the previous instance's pack.
      this.lastSeenPackCreatedAt = null;
    }, { allowSignalWrites: true });

    // Reset wizard when a NEW pending pack arrives — same instance, fresh
    // questions. We compare the pack reference (or its created_at) so
    // SSE re-deliveries for the same pack don't wipe the user's in-flight
    // answers. The 'answered' status is allowed through too — that's the
    // hide trigger.
    effect(() => {
      const p = this.pack();
      if (!p || p.status !== 'pending') return;
      if (p.created_at === this.lastSeenPackCreatedAt) return;
      this.lastSeenPackCreatedAt = p.created_at;
      this.currentIndex.set(0);
      this.answers.set({});
      this.isSubmitting.set(false);
      this.submitError.set(null);
      this.isDismissing.set(false);
      this.dismissError.set(null);
    }, { allowSignalWrites: true });
  }

  /**
   * Toggle an option as the answer for the current question. Selecting
   * the same option twice is idempotent; selecting a different one
   * replaces the previous selection. Clears any stale submission error.
   */
  selectOption(option: string): void {
    const q = this.currentQuestion();
    if (!q) return;
    this.answers.update(a => ({ ...a, [q.id]: option }));
    if (this.submitError() !== null) this.submitError.set(null);
  }

  /**
   * Update the custom answer text for a given question from an input
   * event. Uses ``q.id`` (rather than the active question) so the handler
   * works even if the user types while another wizard event is mid-flight.
   */
  customInput(event: Event, q: Question): void {
    const target = event.target as HTMLInputElement;
    this.answers.update(a => ({ ...a, [q.id]: target.value }));
    if (this.submitError() !== null) this.submitError.set(null);
  }

  /** Advance to the next question (no-op on the last page). */
  next(): void {
    if (this.isLastQuestion()) return;
    this.currentIndex.update(i => i + 1);
  }

  /** Go back one question (no-op on the first page). */
  prev(): void {
    if (this.currentIndex() === 0) return;
    this.currentIndex.update(i => i - 1);
  }

  /**
   * Submit the current answers. NON-OPTIMISTIC — wait for the API. On
   * success we only clear the submitting flag; the SSE ``question_pack``
   * event with status='answered' is what flips ``isVisible`` to false and
   * hides the wizard. On error we surface a message and keep the wizard
   * open so the user can retry.
   *
   * Stale-response guard: captures ``targetInstanceId`` right before
   * subscribing and bails out of callbacks if the user switched instances
   * while the request was in-flight.
   */
  submit(): void {
    if (this.isSubmitting()) return;
    if (!this.canSubmit()) return;
    const targetInstanceId = this.instanceId();
    const payload: Record<string, string> = {};
    for (const [k, v] of Object.entries(this.answers())) {
      if (typeof v === 'string') payload[k] = v;
    }
    this.isSubmitting.set(true);
    this.api.answerQuestions(targetInstanceId, payload)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => {
          if (this.instanceId() !== targetInstanceId) {
            this.isSubmitting.set(false);
            return;
          }
          this.isSubmitting.set(false);
          this.submitError.set(null);
          // SSE will (in the same handler) flip question_pack.status to
          // 'answered', at which point isVisible() returns false and the
          // @if wrapper hides the wizard.
        },
        error: (err) => {
          if (this.instanceId() !== targetInstanceId) {
            this.isSubmitting.set(false);
            return;
          }
          console.error('[QuestionWizard] Failed to submit answers:', err);
          this.isSubmitting.set(false);
          this.submitError.set(
            'Failed to submit answers: ' + (err?.error?.message || err?.message || 'Unknown error')
          );
        },
      });
  }

  /**
   * Dismiss the current question pack without answering. NON-OPTIMISTIC —
   * wait for the API. On success we only clear the dismissing flag; the
   * SSE ``question_pack`` event with status='dismissed' is what clears
   * the signal and flips ``isVisible`` to false. On error we surface a
   * message and keep the wizard open so the user can retry.
   *
   * Stale-response guard: captures ``targetInstanceId`` right before
   * subscribing and bails out of callbacks if the user switched instances
   * while the request was in-flight. Mirrors the ``submit()`` pattern.
   */
  dismiss(): void {
    if (this.isDismissing()) return;
    if (this.isSubmitting()) return;   // prevent both in-flight at once
    if (!this.isVisible()) return;      // bail if wizard already hiding
    const targetInstanceId = this.instanceId();
    this.isDismissing.set(true);
    this.api.dismissQuestion(targetInstanceId)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => {
          if (this.instanceId() !== targetInstanceId) {
            this.isDismissing.set(false);
            return;
          }
          this.isDismissing.set(false);
          this.dismissError.set(null);
          // SSE will (in the same handler) flip question_pack.status to
          // 'dismissed' (or null the signal outright), at which point
          // isVisible() returns false and the @if wrapper hides the
          // wizard.
        },
        error: (err) => {
          if (this.instanceId() !== targetInstanceId) {
            this.isDismissing.set(false);
            return;
          }
          console.error('[QuestionWizard] Failed to dismiss question:', err);
          this.isDismissing.set(false);
          this.dismissError.set(
            'Failed to dismiss question: ' + (err?.error?.message || err?.message || 'Unknown error')
          );
        },
      });
  }

  /** Clear the displayed error (called from the close button). */
  clearError(): void {
    this.submitError.set(null);
    this.dismissError.set(null);
  }

  /**
   * Whether a given option string is the currently selected answer for
   * the supplied question. Used by the template to apply ``.selected``
   * to the active chip.
   */
  isSelected(q: Question, option: string): boolean {
    return this.answers()[q.id] === option;
  }
}
