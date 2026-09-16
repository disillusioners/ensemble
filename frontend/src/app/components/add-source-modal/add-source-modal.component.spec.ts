/**
 * AddSourceModalComponent — agent (re-)selection logic spec.
 *
 * Plain TS, NO TestBed (house style). The component class is instantiated
 * directly; `signal`/`computed` field initializers run outside any injection
 * context and the modal class uses neither `effect()` nor `input()`.
 *
 * Covers the same defect class fixed for the edit modal (see
 * edit-source-modal.component.spec.ts for the full mechanism mirror):
 * `toSelectOptions` used to allocate a fresh array per CD cycle, re-firing
 * SearchableSelectComponent's options-tracking effect. In ADD mode the first
 * selection works because `default_agent` stays null until picked (the
 * effect's null-guard skips the rewrite) — but RE-selection after a first
 * pick hit the same clobber as edit mode. The memoized options binding makes
 * re-selection work.
 *
 * ngx-markdown stub: the modal imports the components barrel
 * (SearchableSelectComponent) which transitively imports chat-interface →
 * ngx-markdown, raw ESM the jest pipeline cannot transform (precedent:
 * chat-interface.component.spec.ts).
 */
jest.mock('ngx-markdown', () => {
  const core = require('@angular/core');
  return {
    MarkdownModule: core.NgModule({ imports: [] })(class MarkdownModuleStub {}),
    provideMarkdown: () => [],
  };
});

import { of } from 'rxjs';
import type { Agent, SourceCreate } from '../../models';
import type { ApiService } from '../../services/api.service';
import type { SearchableSelectOption } from '../searchable-select/searchable-select.component';
import { AddSourceModalComponent } from './add-source-modal.component';

const AGENTS: Agent[] = [
  { id: 'ari', agent_id: 'ari', name: 'Ari', description: 'entry', icon: 'i', color: 'c' },
  { id: 'developer', agent_id: 'developer', name: 'Developer', description: 'builds', icon: 'i', color: 'c' },
];

function makeModal(): { component: AddSourceModalComponent; close: jest.Mock } {
  const close = jest.fn();
  const api = {
    listAgents: jest.fn().mockReturnValue(of({ agents: AGENTS })),
    testSource: jest.fn(),
  } as unknown as ApiService;
  const component = new AddSourceModalComponent(
    { close } as unknown as ConstructorParameters<typeof AddSourceModalComponent>[0],
    {},
    api,
  );
  component.ngOnInit();
  return { component, close };
}

function agentOptions(component: AddSourceModalComponent): SearchableSelectOption[] {
  const fields = (component as unknown as { configFields: () => Array<{ key: string; options?: SearchableSelectOption[] }> }).configFields();
  const field = fields.find((f) => f.key === 'default_agent');
  return (field?.options ?? []) as SearchableSelectOption[];
}

describe('AddSourceModal — agent select options binding identity', () => {
  it('populates default_agent options with all loaded agents', () => {
    const { component } = makeModal();
    expect(agentOptions(component).map((o) => o.value).sort()).toEqual(['ari', 'developer']);
  });

  it('returns the SAME array reference across repeated CD-cycle evaluations (memoized binding)', () => {
    const { component } = makeModal();
    const options = agentOptions(component);
    const first = component['toSelectOptions'](options) as SearchableSelectOption[];
    const second = component['toSelectOptions'](options) as SearchableSelectOption[];
    expect(second).toBe(first); // FAILS on the pre-fix code (fresh .map per call)
  });
});

describe('AddSourceModal — agent RE-selection after a first pick (latent edit-mode defect class)', () => {
  it('user can change the agent after selecting one, and handleSubmit emits the final choice', () => {
    const { component, close } = makeModal();
    // pick source type first (add modal starts on telegram → no agent field) → slack has default_agent
    (component as unknown as { onSourceTypeChange: (t: 'slack') => void }).onSourceTypeChange('slack');
    expect(agentOptions(component).map((o) => o.value).sort()).toEqual(['ari', 'developer']);

    component['sourceId'].set('slack-bot-1');
    component['name'].set('My Slack Bot');
    // add-modal handleSubmit enforces required credential fields for slack
    component['onSelectFieldChange']('bot_token', 'xoxb-test');
    component['onSelectFieldChange']('app_token', 'xapp-test');
    // first pick…
    component['onSelectFieldChange']('default_agent', 'ari');
    expect(component['getFieldValue']('default_agent')).toBe('ari');
    // …then RE-pick a different agent (this is the flow that was clobbered pre-fix)
    component['onSelectFieldChange']('default_agent', 'developer');
    expect(component['getFieldValue']('default_agent')).toBe('developer');

    component['handleSubmit']();
    expect(close).toHaveBeenCalledTimes(1);
    const emitted = close.mock.calls[0][0] as SourceCreate;
    expect(emitted.source_id).toBe('slack-bot-1');
    expect(emitted.source_type).toBe('slack');
    // channel_require_mention is seeded from its field default by onSourceTypeChange
    // and re-emitted explicitly — same contract as the edit modal spec pins.
    expect(emitted.config).toEqual({ default_agent: 'developer', channel_require_mention: true });
    expect(emitted.credentials).toEqual({ bot_token: 'xoxb-test', app_token: 'xapp-test' });
  });
});
