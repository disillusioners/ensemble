import { deduplicateAgentsById, mergeVersionList } from './agent-dedup';
import type { Agent } from '../models';

function makeAgent(id: string, version_tag: string | null = null, available_versions?: (string | null)[] | null): Agent {
  return {
    id,
    agent_id: id,
    name: `Agent ${id}`,
    description: `desc ${id}`,
    icon: 'code',
    color: 'accent-blue',
    version_tag,
    available_versions: available_versions ?? null,
  };
}

describe('deduplicateAgentsById', () => {
  it('returns the single entry unchanged when there are no id collisions', () => {
    const a = makeAgent('alpha');
    const b = makeAgent('beta');
    const result = deduplicateAgentsById([a, b]);
    expect(result.map(x => x.id)).toEqual(['alpha', 'beta']);
  });

  it('keeps the base version when both base and tagged entries share an id', () => {
    const tagged = makeAgent('developer', 'v2');
    const base = makeAgent('developer', null);
    const result = deduplicateAgentsById([tagged, base]);
    expect(result.length).toBe(1);
    expect(result[0].version_tag).toBeNull();
    expect(result[0].id).toBe('developer');
  });

  it('prefers the base even when the tagged entry appears first', () => {
    const tagged = makeAgent('developer', 'v2');
    const base = makeAgent('developer', null);
    const result = deduplicateAgentsById([tagged, base]);
    expect(result[0].version_tag).toBeNull();
  });

  it('uses the alphabetically smaller tag when no base exists (W8)', () => {
    const zeta = makeAgent('developer', 'zeta');
    const alpha = makeAgent('developer', 'alpha');
    const result = deduplicateAgentsById([zeta, alpha]);
    expect(result.length).toBe(1);
    expect(result[0].version_tag).toBe('alpha');
  });

  it('merges available_versions across same-id entries (base + tagged)', () => {
    const base = makeAgent('developer', null, [null, 'v2']);
    const tagged = makeAgent('developer', 'experimental', ['experimental']);
    const result = deduplicateAgentsById([base, tagged]);
    expect(result.length).toBe(1);
    expect(result[0].version_tag).toBeNull();
    expect(result[0].available_versions).toEqual(
      expect.arrayContaining([null, 'v2', 'experimental']),
    );
  });

  it('merges available_versions across same-id entries (tagged + tagged, no base)', () => {
    const a = makeAgent('developer', 'alpha', ['alpha']);
    const b = makeAgent('developer', 'beta', ['beta']);
    const result = deduplicateAgentsById([b, a]);
    expect(result.length).toBe(1);
    expect(result[0].version_tag).toBe('alpha');
    expect(result[0].available_versions).toEqual(
      expect.arrayContaining(['alpha', 'beta']),
    );
  });

  it('does not mutate the input array', () => {
    const a = makeAgent('developer', 'v2');
    const b = makeAgent('developer', null);
    const input = [a, b];
    const snapshot = [...input];
    deduplicateAgentsById(input);
    expect(input).toEqual(snapshot);
  });

  it('sorts results by name', () => {
    const result = deduplicateAgentsById([
      makeAgent('zebra'),
      makeAgent('apple'),
      makeAgent('mango'),
    ]);
    expect(result.map(a => a.id)).toEqual(['apple', 'mango', 'zebra']);
  });
});

describe('mergeVersionList', () => {
  it('returns the original list when extra is undefined', () => {
    const list = ['v1', 'v2'];
    expect(mergeVersionList(list, undefined)).toEqual(['v1', 'v2']);
  });

  it('adds a missing tag to the list', () => {
    expect(mergeVersionList(['v1'], 'v2')).toEqual(['v1', 'v2']);
  });

  it('does not duplicate an existing tag', () => {
    expect(mergeVersionList(['v1', 'v2'], 'v1')).toEqual(['v1', 'v2']);
  });

  it('preserves null entries (base version)', () => {
    expect(mergeVersionList([null], 'v1')).toEqual([null, 'v1']);
    expect(mergeVersionList(['v1'], null)).toEqual(['v1', null]);
  });

  it('treats null as a duplicate when already in the list', () => {
    expect(mergeVersionList([null, 'v1'], null)).toEqual([null, 'v1']);
  });

  it('handles null/undefined input gracefully', () => {
    expect(mergeVersionList(null, 'v1')).toEqual(['v1']);
    expect(mergeVersionList(undefined, 'v1')).toEqual(['v1']);
    expect(mergeVersionList(null, null)).toEqual([null]);
  });

  it('does not mutate the input list', () => {
    const input = ['v1'];
    const snapshot = [...input];
    mergeVersionList(input, 'v2');
    expect(input).toEqual(snapshot);
  });
});
