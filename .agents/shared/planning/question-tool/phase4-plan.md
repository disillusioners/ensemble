# Phase 4: Frontend — Question Wizard Component

## Objective
Create a wizard-style Angular component (`QuestionWizardComponent`) that renders pending questions, lets the user answer (select option or type custom), submits answers via the API, and integrates into the chat interface. Driven by a new `question_pack` SSE event.

## Coupling
- **Depends on**: Phase 2 (SSE event contract + Answer API endpoint) — for integration testing only
- **Coupling type**: loose — frontend codes against the SSE payload spec and API URL
- **Shared files with other phases**: none (frontend is separate codebase)
- **Shared APIs/interfaces**: SSE `question_pack` event shape, `POST /api/instances/{id}/answer` API contract
- **Why this coupling**: The frontend can be built against the documented SSE/API contract without waiting for backend implementation. Integration testing requires Phase 2.

## Context
- Reference pattern: `TodoListComponent` (`frontend/src/app/components/todo-list/`) for component structure, SSE signal integration, and chat.html placement.
- Angular standalone components with signal-based state management.
- Non-optimistic updates (wait for API response before updating UI).

### ⚠️ SSE Dependency Note (F3)

**The frontend wizard depends on the `question_pack` SSE event, NOT on `status_change`.**

When the instance pauses due to a question, `pause_instance_cascade()` cancels the graph task mid-execution. This cancels the task before any post-commit SSE code runs, meaning the `status_change` → `paused` event **may not fire** from the normal post-commit path.

However, the `question_pack` SSE event (status=pending) is emitted by the **tool itself** (in `question_tools.py`), BEFORE the pause cascade. This event always fires.

**Therefore**: The frontend must show the wizard when it receives a `question_pack` SSE event with `status === 'pending'`, and must NOT rely on a `status_change` event to know the instance is paused. The wizard IS the pause UI for this state.

Similarly, when answers are submitted, the backend emits `question_pack` with `status='answered'` — the frontend hides the wizard based on this event, not based on a `status_change` → `running` event.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add TypeScript interfaces | `Question` and `QuestionPack` interfaces matching the backend models. | `frontend/src/app/models/question.model.ts` *(new)* or existing models file |
| 2 | Add SseService signal + listener | New `questionPack = signal<QuestionPack \| null>(null)` signal. Add `question_pack` SSE event listener in `connectInternal()` that updates the signal. Clear on instance switch. **Do NOT listen on `status_change` for wizard visibility** (F3). | `frontend/src/app/services/sse.service.ts` |
| 3 | Add ApiService method | `answerQuestions(instanceId, answers)` → `POST /api/instances/{id}/answer` with `{ answers: {...} }`. Returns the updated pack. | `frontend/src/app/services/api.service.ts` |
| 4 | Create QuestionWizardComponent | Standalone Angular component. Wizard flow: one question per page, option selection or custom text input, Next/Submit buttons. Signal-based state. | `frontend/src/app/components/question-wizard/question-wizard.component.ts` *(new)* |
| 5 | Create component template + styles | HTML template with wizard pages, option chips, custom input field, navigation buttons. SCSS for layout. | `frontend/src/app/components/question-wizard/question-wizard.component.html` *(new)* |
| 6 | Integrate into chat interface | Add `<app-question-wizard>` to `chat.html` above the chat input (similar position to `<app-todo-list>`). Pass `instanceId`. Show only when `questionPack().status === 'pending'`. | `frontend/src/app/pages/chat/chat.component.html` |
| 7 | Handle collapse interaction | When question wizard is visible, auto-collapse the todo component (optional — match how todo can collapse). Or simply position wizard above todo. | `frontend/src/app/pages/chat/chat.component.html` |

## Detailed Design Notes

### Task 1: TypeScript Interfaces

```typescript
// frontend/src/app/models/question.model.ts
export interface Question {
  id: string;
  text: string;
  options?: string[];
  allow_custom: boolean;
  required: boolean;
  answer?: string;
}

export interface QuestionPack {
  instance_id: string;
  questions: Question[];
  status: 'pending' | 'answered';
  answers: Record<string, string>;
  created_at: string;
}
```

### Task 2: SseService Integration (F3 — listen on `question_pack`, NOT `status_change`)

Follow the TodoListComponent pattern exactly:

```typescript
// In SseService
questionPack = signal<QuestionPack | null>(null);

// In connectInternal() or the SSE event setup:
// Listen for 'question_pack' events
eventSource.addEventListener('question_pack', (event) => {
  const data = JSON.parse(event.data);
  const pack = data.message as QuestionPack;
  this.questionPack.set(pack);
});

// Clear on instance switch (same pattern as todos)
// When switching instances: this.questionPack.set(null);
```

**⚠️ F3 — Do NOT rely on `status_change` for wizard visibility.** The `question_pack` SSE event is the ONLY reliable signal. The `status_change` → paused event may not fire because the pause cascade cancels the graph task before post-commit code runs. The wizard visibility is driven entirely by the `questionPack` signal (`status === 'pending'`).

**⚠️ Race condition guard**: Same as TodoListComponent — clear `questionPack` synchronously on instance switch, guard HTTP callbacks with instanceId equality checks.

### Task 3: ApiService Method

```typescript
// In ApiService
answerQuestions(instanceId: string, answers: Record<string, string>): Observable<any> {
  return this.http.post(
    `${this.apiUrl}/instances/${instanceId}/answer`,
    { answers }
  );
}
```

### Task 4: QuestionWizardComponent

Signal-based state:

```typescript
@Component({
  selector: 'app-question-wizard',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './question-wizard.component.html',
  styleUrls: ['./question-wizard.component.scss'],
})
export class QuestionWizardComponent {
  instanceId = input.required<string>();

  // Inject SseService + ApiService
  private sse = inject(SseService);
  private api = inject(ApiService);

  // Local wizard state
  currentIndex = signal(0);
  answers = signal<Record<string, string>>({});
  isSubmitting = signal(false);

  // Derived from SSE signal
  pack = computed(() => this.sse.questionPack());
  questions = computed(() => this.pack()?.questions ?? []);
  currentQuestion = computed(() => this.questions()[this.currentIndex()]);
  isVisible = computed(() => this.pack()?.status === 'pending');
  isLastQuestion = computed(() => this.currentIndex() === this.questions().length - 1);

  // Reset wizard when new pack arrives
  constructor() {
    effect(() => {
      const p = this.pack();
      if (p && p.status === 'pending') {
        this.currentIndex.set(0);
        this.answers.set({});
      }
    });
  }

  selectOption(option: string) {
    const q = this.currentQuestion();
    if (q) this.answers.update(a => ({ ...a, [q.id]: option }));
  }

  next() {
    if (this.isLastQuestion()) return;
    this.currentIndex.update(i => i + 1);
  }

  prev() {
    if (this.currentIndex() === 0) return;
    this.currentIndex.update(i => i - 1);
  }

  submit() {
    this.isSubmitting.set(true);
    this.api.answerQuestions(this.instanceId(), this.answers()).subscribe({
      next: () => {
        this.isSubmitting.set(false);
        // SSE will update pack status to 'answered' → wizard auto-hides
      },
      error: () => {
        this.isSubmitting.set(false);
        // Show error, keep wizard open
      }
    });
  }
}
```

### Task 5: Wizard Template (key elements)

```html
<!-- Only show when visible -->
<div class="question-wizard" *ngIf="isVisible()">
  <div class="wizard-header">
    <h3>Questions from agent</h3>
    <span class="progress">{{ currentIndex() + 1 }} / {{ questions().length }}</span>
  </div>

  <div class="question-page" *ngIf="currentQuestion() as q">
    <p class="question-text">{{ q.text }}</p>

    <!-- Options -->
    <div class="options" *ngIf="q.options?.length">
      <button
        *ngFor="let opt of q.options"
        class="option-chip"
        [class.selected]="answers()[q.id] === opt"
        (click)="selectOption(opt)">
        {{ opt }}
      </button>
    </div>

    <!-- Custom answer input -->
    <input
      *ngIf="q.allow_custom"
      type="text"
      class="custom-input"
      placeholder="Type your own answer..."
      [value]="answers()[q.id] || ''"
      (input)="answers.update(a => ({ ...a, [q.id]: $any($event.target).value }))" />
  </div>

  <!-- Navigation -->
  <div class="wizard-nav">
    <button *ngIf="currentIndex() > 0" (click)="prev()">Back</button>
    <button *ngIf="!isLastQuestion()" (click)="next()" [disabled]="!answers()[currentQuestion()?.id]">
      Next
    </button>
    <button *ngIf="isLastQuestion()" (click)="submit()" [disabled]="isSubmitting()">
      {{ isSubmitting() ? 'Submitting...' : 'Submit Answers' }}
    </button>
  </div>
</div>
```

### Task 6: Chat Integration

In `chat.component.html`, add the wizard component in the same region as the todo list (above the chat input):

```html
<!-- Existing todo list -->
<app-todo-list [instanceId]="currentInstance()!.instance_id ?? ''"></app-todo-list>

<!-- NEW: Question wizard -->
<app-question-wizard [instanceId]="currentInstance()!.instance_id ?? ''"></app-question-wizard>
```

**Visibility**: The component's `isVisible` computed handles showing/hiding based on `pack.status === 'pending'`. No need for `*ngIf` in the parent.

## Key Files
- `frontend/src/app/models/question.model.ts` *(new)* — Question + QuestionPack interfaces
- `frontend/src/app/services/sse.service.ts` — `questionPack` signal + event listener
- `frontend/src/app/services/api.service.ts` — `answerQuestions()` method
- `frontend/src/app/components/question-wizard/question-wizard.component.ts` *(new)*
- `frontend/src/app/components/question-wizard/question-wizard.component.html` *(new)*
- `frontend/src/app/components/question-wizard/question-wizard.component.scss` *(new)*
- `frontend/src/app/pages/chat/chat.component.html` — integrate component

## Constraints
- Follow Angular standalone component pattern (all components are standalone).
- Use signal-based state management (no NgRx, no BehaviorSubject).
- Non-optimistic updates — wait for API response.
- Handle instance switching: clear `questionPack` signal synchronously (same as todos).
- Component must be collapsible/auto-hide when status is 'answered'.
- Match the visual style of the existing chat interface components.
- **F3**: Wizard visibility is driven by `question_pack` SSE events ONLY. Do NOT rely on `status_change` events for showing/hiding the wizard. The `status_change` → paused event may not fire because the pause cascade cancels the graph task mid-execution.

## Deliverables
- [ ] `Question` + `QuestionPack` TypeScript interfaces created
- [ ] SseService: `questionPack` signal + `question_pack` event listener (NOT `status_change` — F3)
- [ ] ApiService: `answerQuestions()` method
- [ ] QuestionWizardComponent created with wizard flow (per-question pages)
- [ ] Template + styles for the wizard
- [ ] Component integrated into chat.html
- [ ] Frontend compiles without errors (`cd frontend && npm run build`)
- [ ] Manual test: wizard shows on pending `question_pack` SSE, hides after `question_pack` answered SSE
