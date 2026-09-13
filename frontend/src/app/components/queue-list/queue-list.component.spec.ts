// QueueListComponent spec — jobs-page-improvement arc, Phase 6
// (plan task 5).
//
// "Selection emit, queue CRUD action dispatch — plain-TS mirror."
// (The plan's "mobile-collapse state logic" clause is DROPPED with
// the mobile scope per leader — no collapse state exists to spec.)
//
// House convention: NO Angular TestBed. The sidebar's logic surface
// is: the selection toggle (re-select emits ``null`` — deselect),
// the CRUD dispatch guards (projectId + per-queue in-flight set +
// ``stopPropagation`` + the delete ``confirm()`` gate), and the
// system-queue/pause helpers. The mirror class below replicates
// that decision logic over plain fields; the F-5 source pins assert
// the REAL component carries the same guards on the same call sites
// (service calls inside the subscribe callbacks, ``queueChanged``
// emission points, delete-gate ordering) — the pairing is the bar.

import { readFileSync } from 'fs';
import { join } from 'path';

import type { JobQueue } from '../../models/job-queue.model';

const dir = join(__dirname, '.');
const componentSrc = readFileSync(join(dir, 'queue-list.component.ts'), 'utf-8');
const templateSrc = readFileSync(join(dir, 'queue-list.component.html'), 'utf-8');

function makeQueue(overrides: Partial<JobQueue>): JobQueue {
  return {
    queue_id: 'q-1',
    queue_name: 'system_defer_queue',
    queue_type: 'defer',
    project_id: 'p-1',
    is_system: true,
    paused: false,
    concurrency_limit: 4,
    ...overrides,
  } as unknown as JobQueue;
}

/**
 * Mirror of the sidebar's selection + CRUD-dispatch decision logic.
 * Service calls are recorded as call-log entries so the specs assert
 * DISPATCH (what was called, in what order, gated by what) without a
 * real QueueService.
 */
class TestableQueueList {
  projectId: string | null = 'p-1';
  selectedQueueId: string | null = null;
  projectPaused = false;
  ensuring = false;

  readonly emittedSelection: (string | null)[] = [];
  readonly emittedQueueChanged: number[] = [];
  readonly emittedPauseChanged: boolean[] = [];
  readonly calls: string[] = [];

  private readonly operatingQueueIds = new Set<string>();

  isQueueOperating(queueId: string): boolean {
    return this.operatingQueueIds.has(queueId);
  }

  /** Mirror of ``onQueueClick`` — re-select deselects (emit null). */
  onQueueClick(queue: JobQueue): void {
    if (this.selectedQueueId === queue.queue_id) {
      this.selectedQueueId = null;
      this.emittedSelection.push(null);
    } else {
      this.selectedQueueId = queue.queue_id;
      this.emittedSelection.push(queue.queue_id);
    }
  }

  isSelected(queue: JobQueue): boolean {
    return this.selectedQueueId === queue.queue_id;
  }

  canDeleteQueue(queue: JobQueue): boolean {
    return !queue.is_system;
  }

  /**
   * Mirror of the shared CRUD prelude: projectId guard + per-queue
   * in-flight guard + operating-set add. Returns false when the
   * action must NOT dispatch (the real methods ``return`` there).
   */
  beginQueueOperation(queue: JobQueue): boolean {
    if (!this.projectId || this.isQueueOperating(queue.queue_id)) return false;
    this.operatingQueueIds.add(queue.queue_id);
    return true;
  }

  /** Mirror of the success path: release the operating slot + notify parent. */
  endQueueOperation(queue: JobQueue): void {
    this.operatingQueueIds.delete(queue.queue_id);
    this.emittedQueueChanged.push(this.emittedQueueChanged.length);
  }

  /** Mirror of ``onStartQueue``/``onStopQueue`` shape (same prelude + success). */
  dispatchQueueAction(queue: JobQueue): boolean {
    if (!this.beginQueueOperation(queue)) return false;
    this.calls.push(`action:${queue.queue_id}`);
    this.endQueueOperation(queue);
    return true;
  }

  /** Mirror of ``onDeleteQueue``'s confirm gate ordering (confirm BEFORE dispatch). */
  deleteQueue(queue: JobQueue, confirmed: boolean): boolean {
    if (!confirmed) return false;
    if (!this.beginQueueOperation(queue)) return false;
    this.calls.push(`delete:${queue.queue_id}`);
    this.endQueueOperation(queue);
    return true;
  }

  /** Mirror of ``onToggleProjectPause`` — flip + emit the NEW state. */
  toggleProjectPause(): boolean | null {
    if (!this.projectId) return null;
    const newState = !this.projectPaused;
    this.projectPaused = newState;
    this.emittedPauseChanged.push(newState);
    return newState;
  }

  /** Mirror of ``onEnsureSystemQueues``'s re-entrancy guard. */
  ensureSystemQueues(): boolean {
    if (!this.projectId || this.ensuring) return false;
    this.ensuring = true;
    return true;
  }
}

describe('queue-list — selection emit (toggle semantics)', () => {
  it('selecting a queue emits its id; RE-selecting the selected queue emits null (deselect)', () => {
    const c = new TestableQueueList();
    const q = makeQueue({});
    c.onQueueClick(q);
    expect(c.emittedSelection).toEqual(['q-1']);
    c.onQueueClick(q);
    expect(c.emittedSelection).toEqual(['q-1', null]);
    expect(c.selectedQueueId).toBeNull();
  });

  it('switching between two queues emits the new id (never null)', () => {
    const c = new TestableQueueList();
    c.onQueueClick(makeQueue({ queue_id: 'q-1' }));
    c.onQueueClick(makeQueue({ queue_id: 'q-2' }));
    expect(c.emittedSelection).toEqual(['q-1', 'q-2']);
  });

  it('isSelected mirrors the parent-owned selection input (dumb component)', () => {
    const c = new TestableQueueList();
    c.selectedQueueId = 'q-9';
    expect(c.isSelected(makeQueue({ queue_id: 'q-9' }))).toBe(true);
    expect(c.isSelected(makeQueue({ queue_id: 'q-1' }))).toBe(false);
  });

  it('REAL class: the deselect branch emits null; selection is the parent-owned input', () => {
    expect(componentSrc).toMatch(
      /if \(this\.selectedQueueId\(\) === queue\.queue_id\) \{\s*\n\s*\/\/ Deselect - show all jobs\s*\n\s*this\.queueSelected\.emit\(null\);/,
    );
    expect(componentSrc).toMatch(/selectedQueueId = input<string \| null>\(null\);/);
  });
});

describe('queue-list — CRUD action dispatch (guards + in-flight discipline)', () => {
  it('a queue action dispatches once and RELEASES the operating slot (no stuck row)', () => {
    const c = new TestableQueueList();
    const q = makeQueue({});
    expect(c.dispatchQueueAction(q)).toBe(true);
    expect(c.isQueueOperating('q-1')).toBe(false);
    expect(c.emittedQueueChanged.length).toBe(1);
  });

  it('a SECOND action on an in-flight queue is REFUSED (double-click protection)', () => {
    const c = new TestableQueueList();
    const q = makeQueue({});
    expect(c.beginQueueOperation(q)).toBe(true);
    expect(c.dispatchQueueAction(q)).toBe(false);
    expect(c.calls).toEqual([]); // nothing re-dispatched
  });

  it('actions are REFUSED without a project (the sidebar is project-scoped)', () => {
    const c = new TestableQueueList();
    c.projectId = null;
    expect(c.dispatchQueueAction(makeQueue({}))).toBe(false);
    expect(c.calls).toEqual([]);
  });

  it('queue-scoped actions are independent (one in-flight queue does not block another)', () => {
    const c = new TestableQueueList();
    expect(c.beginQueueOperation(makeQueue({ queue_id: 'q-1' }))).toBe(true);
    expect(c.dispatchQueueAction(makeQueue({ queue_id: 'q-2' }))).toBe(true);
    expect(c.calls).toEqual(['action:q-2']);
  });

  it('delete requires CONFIRMATION first (confirmed=false dispatches nothing)', () => {
    const c = new TestableQueueList();
    expect(c.deleteQueue(makeQueue({}), false)).toBe(false);
    expect(c.calls).toEqual([]);
    expect(c.deleteQueue(makeQueue({}), true)).toBe(true);
    expect(c.calls).toEqual(['delete:q-1']);
  });

  it('system queues are NOT deletable (canDeleteQueue = !is_system)', () => {
    const c = new TestableQueueList();
    expect(c.canDeleteQueue(makeQueue({ is_system: true }))).toBe(false);
    expect(c.canDeleteQueue(makeQueue({ is_system: false, queue_name: 'user-queue' }))).toBe(true);
  });

  it('REAL class: EVERY queue action stops propagation (row click never fires)', () => {
    for (const method of ['onStartQueue', 'onStopQueue', 'onDeleteQueue']) {
      const body = componentSrc.match(new RegExp(`${method}\\(queue: JobQueue, event: Event\\): void \\{[\\s\\S]*?\\n  \\}`));
      expect(body).not.toBeNull();
      expect(body![0]).toMatch(/event\.stopPropagation\(\);/);
    }
    // And the template wires $event into the action buttons (start
    // and stop share the paused-state ternary on the single toggle).
    expect(templateSrc).toMatch(
      /\(click\)="queue\.is_paused \? onStartQueue\(queue, \$event\) : onStopQueue\(queue, \$event\)"/,
    );
    expect(templateSrc).toMatch(/\(click\)="onDeleteQueue\(queue, \$event\)"/);
  });

  it('REAL class: the projectId + in-flight guard precedes EVERY service dispatch', () => {
    const count = (componentSrc.match(/if \(!projectId \|\| this\.isQueueOperating\(queue\.queue_id\)\) return;/g) ?? []).length;
    expect(count).toBe(3); // start / stop / delete
  });

  it('REAL class: delete asks window.confirm BEFORE the operating-set add (gate ordering)', () => {
    const body = componentSrc.match(/onDeleteQueue\(queue: JobQueue, event: Event\): void \{[\s\S]*?\n  \}/);
    const confirmAt = body![0].indexOf('confirm(');
    const addAt = body![0].indexOf('newSet.add(queue.queue_id)');
    expect(confirmAt).toBeGreaterThan(-1);
    expect(addAt).toBeGreaterThan(confirmAt);
    expect(body![0]).toMatch(/This action cannot be undone\./);
  });

  it('REAL class: start/stop/delete release the slot + emit queueChanged INSIDE next (success only)', () => {
    for (const method of ['onStartQueue', 'onStopQueue', 'onDeleteQueue']) {
      const body = componentSrc.match(new RegExp(`${method}\\(queue: JobQueue, event: Event\\): void \\{[\\s\\S]*?\\n  \\}`));
      const nextIdx = body![0].indexOf('next: () => {');
      const releaseIdx = body![0].indexOf('newSet.delete(queue.queue_id)', nextIdx);
      const emitIdx = body![0].indexOf('this.queueChanged.emit();', nextIdx);
      expect(releaseIdx).toBeGreaterThan(-1);
      expect(emitIdx).toBeGreaterThan(-1);
      // The error path also releases the slot but NEVER emits queueChanged.
      const errIdx = body![0].indexOf('error: (err) => {');
      expect(errIdx).toBeGreaterThan(nextIdx);
      expect(body![0].indexOf('this.queueChanged.emit();', errIdx)).toBe(-1);
    }
  });
});

describe('queue-list — pause toggle + system-queue ensure', () => {
  it('pausing flips the state and emits the NEW value; resuming flips back', () => {
    const c = new TestableQueueList();
    expect(c.toggleProjectPause()).toBe(true);
    expect(c.emittedPauseChanged).toEqual([true]);
    expect(c.toggleProjectPause()).toBe(false);
    expect(c.emittedPauseChanged).toEqual([true, false]);
  });

  it('pause toggle is REFUSED without a project', () => {
    const c = new TestableQueueList();
    c.projectId = null;
    expect(c.toggleProjectPause()).toBeNull();
    expect(c.emittedPauseChanged).toEqual([]);
  });

  it('ensure-system-queues is re-entrancy-guarded (ensuring flag)', () => {
    const c = new TestableQueueList();
    expect(c.ensureSystemQueues()).toBe(true);
    expect(c.ensureSystemQueues()).toBe(false);
  });

  it('REAL class: pause emits the NEW state and the snackbar copy matches the direction', () => {
    expect(componentSrc).toMatch(/this\.projectPauseChanged\.emit\(newPausedState\);/);
    expect(componentSrc).toMatch(/newPausedState \? 'Queue paused' : 'Queue resumed'/);
  });

  it('REAL class: ensure guard covers BOTH the project and the ensuring flag', () => {
    expect(componentSrc).toMatch(/if \(!projectId \|\| this\.ensuring\(\)\) return;/);
  });
});

describe('queue-list — queue CRUD action dispatch: create-dialog path', () => {
  it('REAL class: create is project-gated and maps the dialog result into createQueue', () => {
    expect(componentSrc).toMatch(/protected onCreateQueue\(\): void \{\s*\n\s*const projectId = this\.projectId\(\);\s*\n\s*if \(!projectId\) return;/);
    const body = componentSrc.match(/onCreateQueue\(\): void \{[\s\S]*?\n  \}/);
    expect(body![0]).toMatch(/result\.queue_name/);
    expect(body![0]).toMatch(/result\.queue_type/);
    expect(body![0]).toMatch(/result\.concurrency_limit/);
    expect(body![0]).toMatch(/result\.description/);
  });

  it('REAL class: a dismissed create dialog (undefined result) dispatches NOTHING', () => {
    const body = componentSrc.match(/onCreateQueue\(\): void \{[\s\S]*?\n  \}/);
    expect(body![0]).toMatch(/if \(result\) \{/);
  });

  it('REAL class: queueChanged is a declared output emitted from every SUCCESS path', () => {
    expect(componentSrc).toMatch(/queueChanged = output<void>\(\);/);
    // start / stop / delete success paths + the create-dialog
    // afterClosed success path each notify the parent; the error
    // paths never do (pinned in the describe above).
    const emitCount = (componentSrc.match(/this\.queueChanged\.emit\(\);/g) ?? []).length;
    expect(emitCount).toBe(4);
  });
});
