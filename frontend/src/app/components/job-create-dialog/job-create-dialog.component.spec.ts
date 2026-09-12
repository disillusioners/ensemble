// JobCreateDialogComponent spec — jobs-page-improvement arc,
// Phase 6 (plan task 7).
//
// "Form state → ``createJob`` payload mapping, invalid-state gating."
//
// House convention: NO Angular TestBed (precedent:
// ``skill-trigger-form.component.spec.ts`` — real ``new FormBuilder()``
// + real ``Validators`` in a plain-TS spec). The dialog's logic
// surface is exactly three things:
//
//   1. the form group shape (validators per control),
//   2. ``handleSubmit``'s mapping to ``JobCreateDialogResult``
//      (empty-string → ``undefined`` for the optional ids),
//   3. ``isSubmitDisabled`` (loading || invalid) + the
//      ``markAllAsTouched`` gate on invalid submit.
//
// The mirror class below rebuilds 1–3 with the REAL form primitives
// and the F-5 source pins assert the mirror matches the REAL class
// construction (same group shape, same mapping body, same gate) —
// the pairing is the bar.

import { readFileSync } from 'fs';
import { join } from 'path';

import { FormBuilder, Validators } from '@angular/forms';
import type { JobCreateDialogData, JobCreateDialogResult } from './job-create-dialog.component';

const dialogDir = join(__dirname, '.');
const dialogSrc = readFileSync(join(dialogDir, 'job-create-dialog.component.ts'), 'utf-8');

/**
 * Mirror of the dialog's form + submit mapping, built with the REAL
 * FormBuilder and the SAME validators as the component (identity
 * re-asserted against the real source in the pins describe below —
 * a validator drift breaks the pin before the mirror can lie).
 */
class TestableJobCreateDialog {
  readonly isLoading = () => this._loading;
  private _loading = false;

  setLoading(v: boolean): void {
    this._loading = v;
  }

  readonly form = new FormBuilder().group({
    agent_id: ['', Validators.required],
    message: ['', [Validators.required, Validators.minLength(10)]],
    project_id: [''],
    queue_id: [''],
    priority: [5, [Validators.required, Validators.min(1), Validators.max(10)]],
    source: ['api'],
  });

  /** Mirror of ``isSubmitDisabled`` — the submit button's gate. */
  isSubmitDisabled(): boolean {
    return this.isLoading() || this.form.invalid;
  }

  /** Mirror of ``handleSubmit``'s payload mapping (no dialog in plain TS). */
  buildResult(): JobCreateDialogResult {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      throw new Error('invalid');
    }
    const v = this.form.value;
    return {
      agent_id: v.agent_id!,
      message: v.message!,
      project_id: v.project_id || undefined,
      priority: v.priority!,
      source: v.source!,
      queue_id: v.queue_id || undefined,
    };
  }

  /** Mirror of the ngOnInit prefill path. */
  prefill(data: JobCreateDialogData): void {
    if (data?.editMode || data?.projectId || data?.agentId) {
      this.form.patchValue({
        agent_id: data.agentId || '',
        message: data.message || '',
        project_id: data.projectId || '',
        priority: data.priority || 5,
        source: data.source || 'api',
      });
    }
  }
}

describe('job-create-dialog — form group shape (validators)', () => {
  it('message is REQUIRED with a minimum length of 10', () => {
    const d = new TestableJobCreateDialog();
    const message = d.form.get('message')!;
    expect(message.hasValidator(Validators.required)).toBe(true);
    message.setValue('too short');
    expect(message.invalid).toBe(true);
    expect(message.errors).toHaveProperty('minlength');
    message.setValue('exactly ten');
    expect(message.valid).toBe(true);
  });

  it('exactly-10 chars is VALID (boundary at the validator edge)', () => {
    const d = new TestableJobCreateDialog();
    d.form.get('message')!.setValue('0123456789');
    expect(d.form.get('message')!.valid).toBe(true);
  });

  it('agent_id is REQUIRED', () => {
    const d = new TestableJobCreateDialog();
    d.form.get('message')!.setValue('a long enough message');
    expect(d.form.invalid).toBe(true);
    d.form.get('agent_id')!.setValue('agent-1');
    expect(d.form.get('agent_id')!.valid).toBe(true);
  });

  it('priority is bounded 1..10 and defaults to 5 (both edges invalid OUTSIDE the band)', () => {
    const d = new TestableJobCreateDialog();
    const priority = d.form.get('priority')!;
    expect(priority.value).toBe(5);
    priority.setValue(0);
    expect(priority.errors).toHaveProperty('min');
    priority.setValue(11);
    expect(priority.errors).toHaveProperty('max');
    priority.setValue(1);
    expect(priority.valid).toBe(true);
    priority.setValue(10);
    expect(priority.valid).toBe(true);
  });

  it('project_id / queue_id / source have NO required validators (optional controls)', () => {
    const d = new TestableJobCreateDialog();
    expect(d.form.get('project_id')!.valid).toBe(true);
    expect(d.form.get('queue_id')!.valid).toBe(true);
    expect(d.form.get('source')!.value).toBe('api');
  });

  it('REAL class: the form group declares the SAME shape the mirror builds', () => {
    expect(dialogSrc).toMatch(
      /agent_id: \['', Validators\.required\],\s*\n\s*message: \['', \[Validators\.required, Validators\.minLength\(10\)\]\],\s*\n\s*project_id: \[''\],\s*\n\s*queue_id: \[''\],\s*\n\s*priority: \[5, \[Validators\.required, Validators\.min\(1\), Validators\.max\(10\)\]\],\s*\n\s*source: \['api'\]/,
    );
  });
});

describe('job-create-dialog — invalid-state gating', () => {
  it('submit is DISABLED while the form is invalid (empty initial state)', () => {
    const d = new TestableJobCreateDialog();
    expect(d.isSubmitDisabled()).toBe(true);
  });

  it('submit is DISABLED while loading even when the form is valid', () => {
    const d = new TestableJobCreateDialog();
    d.form.get('agent_id')!.setValue('agent-1');
    d.form.get('message')!.setValue('a long enough message');
    expect(d.isSubmitDisabled()).toBe(false);
    d.setLoading(true);
    expect(d.isSubmitDisabled()).toBe(true);
  });

  it('an invalid submit marks ALL controls touched and produces NO payload', () => {
    const d = new TestableJobCreateDialog();
    expect(() => d.buildResult()).toThrow('invalid');
    expect(d.form.get('agent_id')!.touched).toBe(true);
    expect(d.form.get('message')!.touched).toBe(true);
  });

  it('REAL class: isSubmitDisabled composes loading || invalid', () => {
    expect(dialogSrc).toMatch(
      /protected isSubmitDisabled\(\): boolean \{\s*\n\s*return this\.isLoading\(\) \|\| this\.form\.invalid;/,
    );
  });

  it('REAL class: handleSubmit returns EARLY on invalid via markAllAsTouched (no dialog close)', () => {
    expect(dialogSrc).toMatch(
      /if \(this\.form\.invalid\) \{\s*\n\s*this\.form\.markAllAsTouched\(\);\s*\n\s*return;/,
    );
  });
});

describe('job-create-dialog — form state → createJob payload mapping', () => {
  const fillValid = (d: TestableJobCreateDialog): void => {
    d.form.get('agent_id')!.setValue('agent-9');
    d.form.get('message')!.setValue('run the nightly regression pass');
    d.form.get('priority')!.setValue(7);
    d.form.get('source')!.setValue('scheduler');
  };

  it('a valid form maps EVERY control into the JobCreateDialogResult payload', () => {
    const d = new TestableJobCreateDialog();
    fillValid(d);
    d.form.get('project_id')!.setValue('proj-1');
    d.form.get('queue_id')!.setValue('q-1');
    expect(d.buildResult()).toEqual({
      agent_id: 'agent-9',
      message: 'run the nightly regression pass',
      project_id: 'proj-1',
      priority: 7,
      source: 'scheduler',
      queue_id: 'q-1',
    });
  });

  it('an EMPTY project_id maps to undefined (no-project jobs are legal)', () => {
    const d = new TestableJobCreateDialog();
    fillValid(d);
    d.form.get('project_id')!.setValue('');
    expect(d.buildResult().project_id).toBeUndefined();
  });

  it('an EMPTY queue_id maps to undefined (defer-default absence is legal)', () => {
    const d = new TestableJobCreateDialog();
    fillValid(d);
    d.form.get('queue_id')!.setValue('');
    expect(d.buildResult().queue_id).toBeUndefined();
  });

  it('REAL class: the mapping body empties-to-undefined for BOTH optional ids', () => {
    expect(dialogSrc).toMatch(/project_id: this\.form\.value\.project_id \|\| undefined,/);
    expect(dialogSrc).toMatch(/queue_id: this\.form\.value\.queue_id \|\| undefined/);
  });

  it('REAL class: the payload is the dialogRef close VALUE (createJob wiring is the caller\'s)', () => {
    expect(dialogSrc).toMatch(/const result: JobCreateDialogResult = \{/);
    expect(dialogSrc).toMatch(/this\.dialogRef\.close\(result\);/);
  });
});

describe('job-create-dialog — edit/prefill path', () => {
  it('prefill patches agent/message/project/priority/source from the dialog data', () => {
    const d = new TestableJobCreateDialog();
    d.prefill({ agentId: 'agent-3', message: 'prefilled message text', projectId: 'proj-2', priority: 9 });
    expect(d.form.get('agent_id')!.value).toBe('agent-3');
    expect(d.form.get('message')!.value).toBe('prefilled message text');
    expect(d.form.get('project_id')!.value).toBe('proj-2');
    expect(d.form.get('priority')!.value).toBe(9);
  });

  it('an empty data payload does NOT prefill (defaults survive)', () => {
    const d = new TestableJobCreateDialog();
    d.prefill({});
    expect(d.form.get('agent_id')!.value).toBe('');
    expect(d.form.get('priority')!.value).toBe(5);
    expect(d.form.get('source')!.value).toBe('api');
  });

  it('REAL class: the prefill gate fires on editMode OR projectId OR agentId', () => {
    expect(dialogSrc).toMatch(
      /if \(this\.data\?\.editMode \|\| this\.data\?\.projectId \|\| this\.data\?\.agentId\) \{/,
    );
  });
});
