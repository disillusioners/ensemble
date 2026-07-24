import type { Agent } from '../models';

/**
 * Deduplicate agents by id, keeping the base version (version_tag === null
 * or undefined) as the primary display entry. When two entries share an id,
 * also merge their `available_versions` so the primary entry advertises the
 * union of all tags (including `null` for the base).
 *
 * W8 tiebreaker: when no base exists, keep the alphabetically smaller tag.
 *
 * Returns a new list sorted by `name`.
 */
export function deduplicateAgentsById(agents: Agent[]): Agent[] {
  const byId = new Map<string, Agent>();
  for (const agent of agents) {
    const existing = byId.get(agent.id);
    if (!existing) {
      // First entry for this id — keep a deep-ish copy so we can safely
      // extend `available_versions` without mutating the input array.
      byId.set(agent.id, { ...agent, available_versions: [...(agent.available_versions ?? [])] });
      continue;
    }

    const existingIsBase =
      existing.version_tag === null || existing.version_tag === undefined;
    const agentIsBase =
      agent.version_tag === null || agent.version_tag === undefined;

    if (!existingIsBase && agentIsBase) {
      // Tagged → base wins; carry over the tag union as available_versions.
      byId.set(agent.id, {
        ...agent,
        available_versions: mergeVersionList(
          agent.available_versions,
          existing.version_tag,
        ),
      });
    } else if (!existingIsBase && !agentIsBase) {
      // W8: both tagged — keep alphabetically smaller tag.
      const next = (agent.version_tag ?? '') < (existing.version_tag ?? '')
        ? agent
        : existing;
      byId.set(agent.id, {
        ...next,
        available_versions: mergeVersionList(
          next.available_versions,
          // add the loser's tag to the winner's list
          next === agent ? existing.version_tag : agent.version_tag,
        ),
      });
    }
    // else: existing is base and incoming is tagged — keep existing, but
    // make sure the tag is included in available_versions.
    else {
      const merged = mergeVersionList(
        existing.available_versions,
        agent.version_tag,
      );
      if (merged.length !== (existing.available_versions?.length ?? 0)) {
        byId.set(agent.id, { ...existing, available_versions: merged });
      }
    }
  }
  return Array.from(byId.values()).sort((a, b) =>
    a.name.localeCompare(b.name),
  );
}

/**
 * Add a single tag to a version list (deduped, with `null` preserved).
 *
 * Returns a new array; the input is not mutated.
 */
export function mergeVersionList(
  current: (string | null)[] | null | undefined,
  extra: string | null | undefined,
): (string | null)[] {
  const list = [...(current ?? [])];
  if (extra === undefined) return list;
  if (!list.includes(extra)) list.push(extra);
  return list;
}
