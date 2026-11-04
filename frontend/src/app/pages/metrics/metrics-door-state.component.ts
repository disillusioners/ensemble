import {
  ChangeDetectionStrategy,
  Component,
  computed,
  input,
} from '@angular/core';
import { MatButtonModule } from '@angular/material/button';

import {
  METRICS_DOOR_STATE_COPY,
  MetricsDoorErrorCode,
  MetricsDoorStateCopy,
} from '../../models/metrics.model';

/**
 * The DOOR-STATE PANEL (T1 phase 8, tasks 8.2 / 8.3 / 8.5).
 *
 * ONE component renders the metrics door's failure state, so the state
 * is rendered ONCE per surface and its copy comes from ONE SSOT
 * (IMPL-18 / AM-20). Before this phase the page carried a flat banner
 * in the shell AND a per-tab banner for the same failure, and the
 * fail-closed auth state had no way to say WHAT TO DO about it.
 *
 * The panel is STRUCTURED — the four facts the copy SSOT carries:
 *
 *   * the CAUSE   — what happened (the banner's headline);
 *   * the MEANING — what it means for the operator;
 *   * the ACTION  — what the operator can do;
 *   * the POINTER — where the procedure lives (a runbook path).
 *
 * The Retry button is DATA-DRIVEN from the class's `retryable` flag: a
 * configuration error is not transient, so a Retry on it would be an
 * affordance that can never succeed (8.1 / 8.2).
 *
 * The panel renders NOTHING when there is no class — the caller owns
 * the conditional, so a surface never carries an empty banner.
 */
@Component({
  selector: 'app-metrics-door-state',
  standalone: true,
  imports: [MatButtonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (state(); as rendered) {
      <div
        class="metrics-banner"
        [attr.data-test]="testId()"
        [attr.data-error-class]="rendered.code"
      >
        <div class="metrics-banner-body">
          <span class="metrics-banner-text" data-test="metrics-door-state-cause">
            {{ rendered.copy.cause }}
          </span>
          <span class="metrics-banner-meaning" data-test="metrics-door-state-meaning">
            {{ rendered.copy.meaning }}
          </span>
          <span class="metrics-banner-action" data-test="metrics-door-state-action">
            {{ rendered.copy.action }}
          </span>
          @if (rendered.copy.pointer; as pointer) {
            <span class="metrics-banner-pointer" data-test="metrics-door-state-pointer">
              {{ pointer }}
            </span>
          }
        </div>
        @if (retryable()) {
          <button
            mat-stroked-button
            type="button"
            (click)="retry()"
            [attr.data-test]="retryTestId()"
          >
            Retry
          </button>
        }
      </div>
    }
  `,
  styles: [
    `
      :host {
        display: block;
      }

      .metrics-banner {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        justify-content: space-between;
        gap: 0.75rem;
        padding: 0.5rem 0.75rem;
        margin-bottom: 0.6rem;
        border: 1px solid #7f1d1d;
        border-radius: 6px;
        background: #2a1414;
        color: #fca5a5;
        font-size: 0.8rem;
      }

      .metrics-banner-body {
        flex: 1 1 18rem;
        min-width: 0;
      }

      /*
       * The Retry button NEVER shrinks (task 8.6).
       *
       * The text takes the free space and wraps; the button keeps its
       * size and MOVES to its own line when the line runs out. A
       * shrinking button is a squashed button — and a squashed button
       * is one more way for the text and the button to collide.
       */
      .metrics-banner button {
        flex: 0 0 auto;
      }

      .metrics-banner-text,
      .metrics-banner-meaning,
      .metrics-banner-action,
      .metrics-banner-pointer {
        display: block;
        overflow-wrap: anywhere;
      }

      .metrics-banner-text {
        font-weight: 600;
      }

      .metrics-banner-meaning,
      .metrics-banner-action,
      .metrics-banner-pointer {
        margin-top: 0.15rem;
        color: #fca5a5;
        opacity: 0.9;
      }

      .metrics-banner-pointer {
        font-family: monospace;
      }

      /*
       * THE NARROW-WIDTH STACK (task 8.6).
       *
       * Below the audit's 900 px the banner STACKS: the copy block
       * takes the full width and the button moves UNDER it, so the two
       * can never share a line.
       *
       * The copy block's flex MUST be reset here: in a COLUMN flex
       * container the flex-basis is the HEIGHT, so the inherited
       * 1 1 18rem basis would pin the block to an 18rem box — and a
       * longer copy would OVERFLOW that box straight into the button
       * below it. (The 900 px geometry spec caught exactly that.)
       */
      @media (max-width: 900px) {
        .metrics-banner {
          flex-direction: column;
          align-items: flex-start;
        }

        .metrics-banner-body {
          flex: 0 0 auto;
          width: 100%;
        }
      }
    `,
  ],
})
export class MetricsDoorStateComponent {
  /** The failure class — `null` renders nothing. */
  readonly code = input<MetricsDoorErrorCode | null>(null);

  /** The retry handler — absent means there is no honest Retry. */
  readonly onRetry = input<(() => void) | null>(null);

  /** The panel's test id — the surface owns its own identity. */
  readonly testId = input('metrics-door-state');

  /** The Retry button's test id. */
  readonly retryTestId = input('metrics-door-state-retry');

  /** The class + its structured copy — the panel's render model. */
  readonly state = computed<{
    code: MetricsDoorErrorCode;
    copy: MetricsDoorStateCopy;
  } | null>(() => {
    const code = this.code();
    if (!code) {
      return null;
    }
    return { code, copy: METRICS_DOOR_STATE_COPY[code] };
  });

  /**
   * Whether the Retry affordance is honest — the CLASS says so AND a
   * handler exists. A class with no handler never renders a dead
   * button.
   */
  readonly retryable = computed(
    () => (this.state()?.copy.retryable ?? false) && this.onRetry() !== null,
  );

  /** Fire the retry. */
  retry(): void {
    this.onRetry()?.();
  }
}
