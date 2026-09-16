/**
 * EditSourceModalComponent — agent selection on edit.
 *
 * Plain-TS logic spec, NO TestBed (house style: parse-command-ack.spec.ts).
 * The component class is instantiated directly; Angular's `signal`/`computed`
 * field initializers run outside any injection context, and the modal class
 * itself uses neither `effect()` nor `input()`.
 *
 * ROOT CAUSE (round 2 — replaces the round-1 missing-standalone theory):
 * the template binds `[options]="toSelectOptions(field.options)"`, and
 * `toSelectOptions` allocated a FRESH array on every change-detection cycle.
 * Every DOM event re-runs app-wide CD, so SearchableSelectComponent's
 * options-tracking effect (searchable-select.component.ts constructor) fired
 * on every cycle; with a PRESELECTED value (edit mode preselects
 * `config.default_agent`) that effect rewrites the visible text back to the
 * current agent's label — clobbering the focus-cleared/typed search text so
 * the panel collapses to the exact current match and the user cannot pick a
 * different agent. ADD works because its `default_agent` value stays null
 * until first selection and the effect's null-guard skips the rewrite.
 *
 * The fix memoizes `toSelectOptions` per source-array identity so the
 * binding is stable across CD cycles; the child effect then fires only on
 * genuine options changes (agents reload) — its documented purpose.
 *
 * ngx-markdown is stubbed because the modal imports the components barrel
 * (SearchableSelectComponent), which transitively imports chat-interface →
 * ngx-markdown; raw ESM the jest pipeline cannot transform (precedent:
 * chat-interface.component.spec.ts).
 */
jest.mock('ngx-markdown', () => {
  const core = require('@angular/core');
  return {
    MarkdownModule: core.NgModule({ imports: [] })(class MarkdownModuleStub {}),
    provideMarkdown: () => [],
  };
});

import { of, throwError } from 'rxjs';
import type { Agent, Source, SourceUpdate } from '../../models';
import type { ApiService } from '../../services/api.service';
import type { SearchableSelectOption } from '../searchable-select/searchable-select.component';
import { EditSourceModalComponent } from './edit-source-modal.component';

const AGENTS: Agent[] = [
  { id: 'ari', agent_id: 'ari', name: 'Ari', description: 'entry', icon: 'i', color: 'c' },
  { id: 'developer', agent_id: 'developer', name: 'Developer', description: 'builds', icon: 'i', color: 'c' },
  { id: 'tester', agent_id: 'tester', name: 'Tester', description: 'tests', icon: 'i', color: 'c' },
];

function makeSlackSource(): Source {
  return {
    source_id: 'slack-bot-1',
    source_type: 'slack',
    name: 'My Slack Bot',
    config: { default_agent: 'ari', channel_require_mention: true },
    enabled: true,
    autostart: true,
    status: 'stopped',
    created_at: '2026-01-01T00:00:00Z',
    has_credentials: true,
  };
}

interface ModalHandle {
  component: EditSourceModalComponent;
  close: jest.Mock;
  setAgentsError: () => void;
}

/** Direct class instantiation — no TestBed (see file docblock). */
function makeModal(source: Source): ModalHandle {
  const close = jest.fn();
  const api = {
    listAgents: jest.fn().mockReturnValue(of({ agents: AGENTS })),
    testSource: jest.fn(),
  } as unknown as ApiService;
  const component = new EditSourceModalComponent(
    { close } as unknown as ConstructorParameters<typeof EditSourceModalComponent>[0],
    { source },
    api,
  );
  const failingApi = api as { listAgents: jest.Mock };
  component.ngOnInit();
  return {
    component,
    close,
    setAgentsError: () => failingApi.listAgents.mockReturnValue(throwError(() => new Error('boom'))),
  };
}

/** The modal's agent select field for the current source type. */
function agentField(component: EditSourceModalComponent): { key: string; options?: Array<{ value: string; label: string }> } | undefined {
  const fields = (component as unknown as { configFields: () => Array<{ key: string; options?: Array<{ value: string; label: string }> }> }).configFields();
  return fields.find((f) => f.key === 'default_agent');
}

describe('EditSourceModal — agent options population (edit mode)', () => {
  it('populates default_agent options with all loaded agents (value = agent id)', () => {
    const { component } = makeModal(makeSlackSource());
    const field = agentField(component);
    expect(field).toBeDefined();
    expect((field!.options ?? []).map((o) => o.value).sort()).toEqual(['ari', 'developer', 'tester']);
  });

  it('renders option labels from agent names', () => {
    const { component } = makeModal(makeSlackSource());
    const field = agentField(component)!;
    expect((field!.options ?? []).map((o) => o.label).sort()).toEqual(['Ari', 'Developer', 'Tester']);
  });

  it('populates options even when listAgents fails (dropdown empty, form still usable)', () => {
    const handle = makeModal(makeSlackSource());
    handle.setAgentsError();
    const fresh = new EditSourceModalComponent(
      { close: jest.fn() } as unknown as ConstructorParameters<typeof EditSourceModalComponent>[0],
      { source: makeSlackSource() },
      { listAgents: () => throwError(() => new Error('boom')) } as unknown as ApiService,
    );
    fresh.ngOnInit();
    expect(agentField(fresh)?.options ?? []).toEqual([]);
    expect(handle.component['getFieldValue']('default_agent')).toBe('ari');
  });
});

describe('EditSourceModal — current-agent preselection', () => {
  it('preselects config.default_agent into simpleFieldValues after the async populate', () => {
    const { component } = makeModal(makeSlackSource());
    expect(component['simpleFieldValues']()['default_agent']).toBe('ari');
    expect(component['getFieldValue']('default_agent')).toBe('ari');
  });

  it('getFieldValue renders the empty string for unset fields (no undefined leakage)', () => {
    const { component } = makeModal({ ...makeSlackSource(), config: {} });
    expect(component['getFieldValue']('default_agent')).toBe('');
  });
});

describe('EditSourceModal — user selection survives the async populate (one-shot ordering)', () => {
  it('a selection made after populate lands is NOT overwritten (populate runs once, inside the listAgents callback)', () => {
    const { component } = makeModal(makeSlackSource());
    // populate landed synchronously inside ngOnInit (of() is synchronous)
    expect(component['simpleFieldValues']()['default_agent']).toBe('ari');
    // user picks a different agent through the searchable-select wiring
    component['onSelectFieldChange']('default_agent', 'developer');
    // no late re-populate exists: loadAgents' callback is the only populate site
    expect(component['simpleFieldValues']()['default_agent']).toBe('developer');
    expect(component['getFieldValue']('default_agent')).toBe('developer');
  });

  it('checkbox sibling values are preserved when the agent changes', () => {
    const { component } = makeModal(makeSlackSource());
    component['onSelectFieldChange']('default_agent', 'tester');
    expect(component['simpleFieldValues']()['channel_require_mention']).toBe(true);
  });
});

describe('EditSourceModal — handleSubmit emits SourceUpdate.config.default_agent with siblings intact', () => {
  it('carries the user-chosen agent and the untouched sibling config field', () => {
    const { component, close } = makeModal(makeSlackSource());
    component['onSelectFieldChange']('default_agent', 'developer');

    component['handleSubmit']();

    expect(close).toHaveBeenCalledTimes(1);
    const emitted = close.mock.calls[0][0] as SourceUpdate;
    expect(emitted.name).toBe('My Slack Bot');
    expect(emitted.config).toEqual({ default_agent: 'developer', channel_require_mention: true });
    expect(emitted.enabled).toBe(true);
    expect(emitted.autostart).toBe(true);
    // empty password fields mean "keep current" → credentials omitted
    expect(emitted.credentials).toBeUndefined();
  });

  it('restates the checkbox default when the source had no config (populate applies defaultValue)', () => {
    // config {} → populateSimpleFieldsFromSource fills channel_require_mention
    // from its field default → handleSubmit re-emits it explicitly. Pinned as-is.
    const { component, close } = makeModal({ ...makeSlackSource(), config: {} });
    component['handleSubmit']();
    const emitted = close.mock.calls[0][0] as SourceUpdate;
    expect(emitted.config).toEqual({ channel_require_mention: true });
  });
});

// ── Options-binding identity stability (THE FIX) ─────────────────────────
//
// SearchableSelectComponent tracks its `options` input in an effect that
// rewrites the visible text whenever the options identity changes
// (searchable-select.component.ts:54-62). The modal template evaluates
// `[options]="toSelectOptions(field.options)"` on every CD cycle, so a fresh
// array per call = per-cycle effect re-fires = search text clobbered (see
// mechanism mirror below). The fix memoizes per source-array identity, so
// repeated template evaluations must return the SAME reference.
describe('EditSourceModal — toSelectOptions identity stability across CD cycles', () => {
  it('returns the SAME array reference for repeated evaluations of the same field options', () => {
    const { component } = makeModal(makeSlackSource());
    const field = agentField(component)!;
    const first = component['toSelectOptions'](field!.options) as SearchableSelectOption[];
    const second = component['toSelectOptions'](field!.options) as SearchableSelectOption[];
    expect(second).toBe(first); // FAILS on the pre-fix code (fresh .map per call)
  });

  it('uses one stable empty-array reference for fields without options (scheduler agent field)', () => {
    const { component } = makeModal({ ...makeSlackSource(), source_type: 'scheduler' });
    const first = component['toSelectOptions'](undefined) as SearchableSelectOption[];
    const second = component['toSelectOptions'](undefined) as SearchableSelectOption[];
    expect(first).toEqual([]);
    expect(second).toBe(first);
  });

  it('rebuilds the mapped array when the agents list changes (new source-array identity)', () => {
    const { component } = makeModal(makeSlackSource());
    const field = agentField(component)!;
    const before = component['toSelectOptions'](field!.options) as SearchableSelectOption[];
    (component as unknown as { agents: { set: (v: Agent[]) => void } }).agents.set([
      ...AGENTS,
      { id: 'governor', agent_id: 'governor', name: 'Governor', description: 'd', icon: 'i', color: 'c' },
    ]);
    const after = component['toSelectOptions'](
      (component as unknown as { configFields: () => Array<{ options?: unknown[] }> }).configFields().find((f) => f.key === 'default_agent')!.options as Array<{ value: string; label: string }>,
    ) as SearchableSelectOption[];
    expect(after).not.toBe(before);
    expect(after.map((o) => o.value)).toContain('governor');
  });
});

// ── Mechanism mirror: the child options-effect, pinned to production ─────
//
// SearchableSelectComponent cannot be instantiated outside an Angular
// injection context (its `input()`/`effect()` members require one), so the
// effect's semantics are mirrored here. Per the FE testing convention
// (identity-grep pins), the mirrored predicates are pinned to the production
// source text below — if the production effect changes, these pins fail and
// this mirror must be re-adjudicated rather than silently drifting green.
const SELECT_TS_PATH = '../searchable-select/searchable-select.component.ts';
const PIN_EFFECT_TRACK = 'this.options(); // track for re-derivation';
const PIN_EFFECT_GUARD = 'if (current !== null && current !== undefined)';
const PIN_EFFECT_WRITE = 'this.displayText.set(this.labelFor(current));';

describe('Mechanism mirror — SearchableSelectComponent options-effect (pinned)', () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const production = require('fs').readFileSync(require('path').join(__dirname, SELECT_TS_PATH), 'utf8');

  it('pins: the mirrored predicates appear verbatim in the production component', () => {
    expect(production).toContain(PIN_EFFECT_TRACK);
    expect(production).toContain(PIN_EFFECT_GUARD);
    expect(production).toContain(PIN_EFFECT_WRITE);
  });

  /** One effect run, semantics of searchable-select.component.ts:55-61. */
  function runMirroredEffect(
    displayText: { v: string },
    value: string | null,
    options: SearchableSelectOption[],
  ): void {
    const tracked = options; // PIN_EFFECT_TRACK — re-fires on every options identity change
    const current = value; // this.value()
    if (current !== null && current !== undefined) {
      // PIN_EFFECT_GUARD — edit mode sits behind this guard with a preselected id
      const match = tracked.find((o) => o.value === current);
      displayText.v = match ? match.label : ''; // PIN_EFFECT_WRITE — labelFor
    }
  }

  /**
   * Mirrors N parent CD cycles: each cycle re-evaluates the template binding
   * and hands the child an array; the child's `options` input signal only
   * re-fires the effect when the handed reference differs (Object.is) from
   * the previous one (searchable-select.component.ts:44 `input()`). Between
   * cycles the user interacts: cycle 1 gap = focus (onFocus clears the text),
   * `typeAtCycle` gap = user types a search term.
   */
  function simulateCdCycles(
    value: string | null,
    boundPerCycle: () => SearchableSelectOption[],
    cycles: number,
    typeAtCycle: number | null,
    typedText: string,
  ): { displayText: string; effectRuns: number } {
    const displayText = { v: 'Ari' }; // initial labeled state after writeValue
    let effectRuns = 0;
    let prev: SearchableSelectOption[] | null = null;
    for (let cycle = 1; cycle <= cycles; cycle += 1) {
      const handed = boundPerCycle();
      if (handed !== prev) {
        effectRuns += 1;
        runMirroredEffect(displayText, value, handed);
        prev = handed;
      }
      if (cycle === 1) {
        displayText.v = ''; // user focuses: onFocus() clears to reveal all options
      }
      if (typeAtCycle === cycle) {
        displayText.v = typedText; // user types a filter mid-search
      }
    }
    return { displayText: displayText.v, effectRuns };
  }

  it('PRE-FIX binding (fresh array per CD cycle): focus-clear and typed search are clobbered back to the current label', () => {
    // Documents the reported symptom. The old template allocated a fresh array
    // per evaluation, so every cycle re-fired the effect.
    const result = simulateCdCycles('ari', () => [{ value: 'ari', label: 'Ari' }], 3, 2, 'dev');
    expect(result.effectRuns).toBe(3);
    expect(result.displayText).toBe('Ari'); // user's "dev" search wiped → panel collapses to exact match
  });

  it('POST-FIX binding (memoized): stable identity → one initial effect run; focus/typed text survives', () => {
    const { component } = makeModal(makeSlackSource());
    const field = agentField(component)!;
    // The real (fixed) component method under the template binding, per cycle:
    const result = simulateCdCycles('ari', () => component['toSelectOptions'](field!.options) as SearchableSelectOption[], 3, 2, 'dev');
    expect(result.effectRuns).toBe(1); // initial bind only — FAILS pre-fix (3 fresh arrays → 3 runs)
    expect(result.displayText).toBe('dev'); // user's search survives → they can reach other agents
  });

  it('ADD-mode asymmetry: value stays null until first selection → the guard skips the rewrite (why add worked)', () => {
    const displayText = { v: '' };
    const options = [{ value: 'ari', label: 'Ari' }];
    runMirroredEffect(displayText, null, () => options);
    expect(displayText.v).toBe('');
  });
});
