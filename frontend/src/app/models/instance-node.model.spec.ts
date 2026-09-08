// Specs for the instances-primary tree model (2026-09-08, design V1).
// Plain TS — no Angular deps, mirroring the ``job.model.spec.ts`` /
// ``mission.model.spec.ts`` pattern.

import {
  InstanceRow,
  buildInstanceNodes,
  buildInstanceTree,
  shouldAutoExpandInstanceTree,
  isTerminalInstanceStatus,
  isLiveInstanceStatus,
  getInstanceStatusColor,
  instanceDisplayTitle,
  instanceMetaLine,
  visibleInstanceTreeItems,
  nextInstanceTreeItem,
  instanceTreeItemId,
  MAX_RECENT_INSTANCE_ROWS,
} from './instance-node.model';
import { createMockJob } from '../testing/job-test-helpers';

/** Wire-row factory mirroring ``GET /api/instances`` rows. */
function mkRow(over: Partial<InstanceRow> = {}): InstanceRow {
  return {
    instance_id: 'i-1',
    agent_id: 'leader',
    agent_tag: null,
    status: 'running',
    parent_id: null,
    title: null,
    initiative_message: null,
    children: [],
    created_at: '2026-09-08T09:00:00Z',
    updated_at: '2026-09-08T10:00:00Z',
    project_id: 'p-1',
    pinned: false,
    color_tag: null,
    icon_tag: null,
    pinned_at: null,
    ...over,
  };
}

describe('InstanceNode model (instances-primary tree, design V1)', () => {
  describe('status mapping (BE enum → live/terminal)', () => {
    it('classifies the terminal cluster: completed / error / terminated / failed', () => {
      expect(isTerminalInstanceStatus('completed')).toBe(true);
      expect(isTerminalInstanceStatus('error')).toBe(true);
      expect(isTerminalInstanceStatus('terminated')).toBe(true);
      expect(isTerminalInstanceStatus('failed')).toBe(true);
    });

    it('classifies every non-terminal BE status as live (TOTAL split — nothing falls between buckets)', () => {
      const beEnum = [
        'idle',
        'running',
        'waiting',
        'paused',
        'queued',
        'waiting_children',
      ] as const;
      for (const s of beEnum) {
        expect(isTerminalInstanceStatus(s)).toBe(false);
        expect(isLiveInstanceStatus(s)).toBe(true);
      }
    });

    it('live is the exact complement of terminal over the full 10-value BE space', () => {
      const full = [
        'idle', 'running', 'waiting', 'paused', 'completed',
        'error', 'terminated', 'queued', 'waiting_children', 'failed',
      ] as const;
      for (const s of full) {
        expect(isLiveInstanceStatus(s)).toBe(!isTerminalInstanceStatus(s));
      }
    });

    it('getInstanceStatusColor mirrors the instance-list palette for shared values', () => {
      expect(getInstanceStatusColor('running')).toBe('#10b981');
      expect(getInstanceStatusColor('waiting')).toBe('#f59e0b');
      expect(getInstanceStatusColor('waiting_children')).toBe('#3b82f6');
      expect(getInstanceStatusColor('paused')).toBe('#8b5cf6');
      expect(getInstanceStatusColor('error')).toBe('#f43f5e');
      expect(getInstanceStatusColor('terminated')).toBe('#6e6e80');
    });

    it('extends the palette for the terminal/queued members the list page never renders', () => {
      expect(getInstanceStatusColor('completed')).toBe('#22C55E');
      expect(getInstanceStatusColor('failed')).toBe('#f43f5e');
      expect(getInstanceStatusColor('queued')).toBe('#9CA3AF');
      expect(getInstanceStatusColor('idle')).toBe('#c5c5d2');
    });
  });

  describe('buildInstanceNodes — flat wire page → nested roots', () => {
    it('nests children under their parent via parent_id (any depth)', () => {
      const rows = [
        mkRow({ instance_id: 'root', children: ['mid'] }),
        mkRow({ instance_id: 'mid', parent_id: 'root', children: ['leaf'] }),
        mkRow({ instance_id: 'leaf', parent_id: 'mid' }),
      ];
      const roots = buildInstanceNodes(rows);
      expect(roots.length).toBe(1);
      expect(roots[0].children[0].instance.instance_id).toBe('mid');
      expect(roots[0].children[0].children[0].instance.instance_id).toBe('leaf');
    });

    it('degrades a row whose parent is NOT in the page to a ROOT (never hide)', () => {
      const rows = [
        mkRow({ instance_id: 'orphan', parent_id: 'paginated-away' }),
        mkRow({ instance_id: 'root' }),
      ];
      const roots = buildInstanceNodes(rows);
      expect(roots.map((n) => n.instance.instance_id).sort()).toEqual(['orphan', 'root']);
    });

    it('returns [] for an empty page and fresh nodes each call', () => {
      expect(buildInstanceNodes([])).toEqual([]);
      const rows = [mkRow({ instance_id: 'a' })];
      const r1 = buildInstanceNodes(rows);
      const r2 = buildInstanceNodes(rows);
      expect(r1[0]).not.toBe(r2[0]); // fresh objects — safe to annotate
    });
  });

  describe('buildInstanceTree', () => {
    it('attaches a job to its ROOT node via mission_id === instance_id', () => {
      const roots = buildInstanceNodes([mkRow({ instance_id: 'r1' })]);
      const tree = buildInstanceTree(
        roots,
        [createMockJob({ job_id: 'j1', mission_id: 'r1', status: 'processing' })],
        []
      );
      expect(tree.liveRoots[0].attachedJobs.map((j) => j.job_id)).toEqual(['j1']);
      expect(tree.queued).toEqual([]);
      expect(tree.recentFlat).toEqual([]);
    });

    it('attaches a job to a CHILD node at ANY depth (not the root)', () => {
      const roots = buildInstanceNodes([
        mkRow({ instance_id: 'root', children: ['kid'] }),
        mkRow({ instance_id: 'kid', parent_id: 'root', agent_id: 'worker' }),
      ]);
      const tree = buildInstanceTree(
        roots,
        [createMockJob({ job_id: 'jk', mission_id: 'kid', status: 'pending' })],
        []
      );
      expect(tree.liveRoots[0].attachedJobs).toEqual([]);
      expect(tree.liveRoots[0].children[0].attachedJobs.map((j) => j.job_id)).toEqual(['jk']);
    });

    it('routes unattached NON-TERMINAL jobs to queued — at depth, orphans included (NEVER hide)', () => {
      const roots = buildInstanceNodes([
        mkRow({ instance_id: 'root', children: ['kid'] }),
        mkRow({ instance_id: 'kid', parent_id: 'root' }),
      ]);
      const tree = buildInstanceTree(
        roots,
        [
          createMockJob({ job_id: 'j-orphan-null', mission_id: null, status: 'processing' }),
          createMockJob({ job_id: 'j-orphan-missing', mission_id: 'ghost', status: 'pending' }),
        ],
        []
      );
      expect(tree.queued.map((j) => j.job_id).sort()).toEqual(['j-orphan-missing', 'j-orphan-null']);
    });

    it('routes unattached TERMINAL jobs to recentFlat (NEVER hide)', () => {
      const roots = buildInstanceNodes([mkRow({ instance_id: 'r1', status: 'running' })]);
      const tree = buildInstanceTree(
        roots,
        [],
        [createMockJob({ job_id: 'jt', mission_id: 'ghost', status: 'settled' })]
      );
      expect(tree.recentFlat.map((j) => j.job_id)).toEqual(['jt']);
    });

    it('a receipt with a terminal job status still ATTACHES when its node exists', () => {
      const roots = buildInstanceNodes([mkRow({ instance_id: 'r1' })]);
      const tree = buildInstanceTree(
        roots,
        [],
        [createMockJob({ job_id: 'jr', mission_id: 'r1', status: 'completed' })]
      );
      // r1 is live → the receipt renders under the live root, NOT recentFlat.
      expect(tree.recentFlat).toEqual([]);
      expect(tree.liveRoots[0].attachedJobs.map((j) => j.job_id)).toEqual(['jr']);
    });

    it('LIVE via descendant: a terminal root with a live child stays in liveRoots', () => {
      const roots = buildInstanceNodes([
        mkRow({ instance_id: 'root', status: 'completed', children: ['kid'] }),
        mkRow({ instance_id: 'kid', parent_id: 'root', status: 'waiting' }),
      ]);
      const tree = buildInstanceTree(roots, [], []);
      expect(tree.liveRoots.map((n) => n.instance.instance_id)).toEqual(['root']);
      expect(tree.recentRoots).toEqual([]);
    });

    it('terminal root AND terminal descendants → recentRoots', () => {
      const roots = buildInstanceNodes([
        mkRow({ instance_id: 'root', status: 'completed', children: ['kid'] }),
        mkRow({ instance_id: 'kid', parent_id: 'root', status: 'failed' }),
      ]);
      const tree = buildInstanceTree(roots, [], []);
      expect(tree.liveRoots).toEqual([]);
      expect(tree.recentRoots.map((n) => n.instance.instance_id)).toEqual(['root']);
    });

    it('sorts liveRoots pinned-first (pinned_at desc) then activity desc — recursive', () => {
      const roots = buildInstanceNodes([
        mkRow({ instance_id: 'old', updated_at: '2026-09-08T08:00:00Z' }),
        mkRow({ instance_id: 'pin', updated_at: '2026-09-08T07:00:00Z', pinned: true, pinned_at: '2026-09-08T09:30:00Z' }),
        mkRow({ instance_id: 'new', updated_at: '2026-09-08T11:00:00Z' }),
      ]);
      const tree = buildInstanceTree(roots, [], []);
      expect(tree.liveRoots.map((n) => n.instance.instance_id)).toEqual(['pin', 'new', 'old']);
    });

    it('sorts recentRoots newest-activity-first', () => {
      const roots = buildInstanceNodes([
        mkRow({ instance_id: 'older', status: 'completed', updated_at: '2026-09-08T08:00:00Z' }),
        mkRow({ instance_id: 'newer', status: 'terminated', updated_at: '2026-09-08T12:00:00Z' }),
      ]);
      const tree = buildInstanceTree(roots, [], []);
      expect(tree.recentRoots.map((n) => n.instance.instance_id)).toEqual(['newer', 'older']);
    });

    it('is PURE: the input nodes are never mutated', () => {
      const roots = buildInstanceNodes([
        mkRow({ instance_id: 'r1', children: ['k1'] }),
        mkRow({ instance_id: 'k1', parent_id: 'r1' }),
      ]);
      const before = JSON.stringify(roots);
      buildInstanceTree(
        roots,
        [createMockJob({ job_id: 'j1', mission_id: 'k1', status: 'processing' })],
        []
      );
      expect(JSON.stringify(roots)).toBe(before);
      expect(roots[0].attachedJobs).toEqual([]); // annotation lands on clones only
    });

    describe('Recent cap — structured band ≤ MAX_RECENT_INSTANCE_ROWS + every-job-surfaces (overflow → recentFlat)', () => {
      it('cap constant stays in the shared 10-row cap class', () => {
        expect(MAX_RECENT_INSTANCE_ROWS).toBe(10);
      });

      it('structured band ≤ MAX: a flat terminal root with more jobs than fit keeps a PARTIAL fit; the rest overflow to recentFlat', () => {
        // No intermediate child-instance headers → headerCost = 1.
        // 12 jobs on a childless root: header (1) + 9 jobs fit =
        // structured band = 10; the remaining 3 overflow to recentFlat
        // (every-job-surfaces — NEVER-hide).
        const roots = buildInstanceNodes([mkRow({ instance_id: 'big', status: 'completed' })]);
        const jobs = Array.from({ length: 12 }, (_, k) =>
          createMockJob({ job_id: `j-${k}`, mission_id: 'big', status: 'completed' })
        );
        const tree = buildInstanceTree(roots, [], jobs);
        expect(tree.recentRoots.length).toBe(1);
        expect(1 + tree.recentRoots[0].attachedJobs.length).toBe(10); // header + 9 fit
        expect(tree.recentFlat.length).toBe(3); // 3 overflow — never hidden
      });

      it('structured band ≤ MAX: a node past the cap spills ALL its jobs to overflow', () => {
        const roots = buildInstanceNodes([
          mkRow({ instance_id: 'a', status: 'failed', updated_at: '2026-09-08T08:00:00Z' }),
          mkRow({ instance_id: 'b', status: 'completed', updated_at: '2026-09-08T07:00:00Z' }),
        ]);
        const jobs = [
          ...Array.from({ length: 12 }, (_, k) =>
            createMockJob({ job_id: `ja-${k}`, mission_id: 'a', status: 'failed' })
          ),
          ...Array.from({ length: 4 }, (_, k) =>
            createMockJob({ job_id: `jb-${k}`, mission_id: 'b', status: 'completed' })
          ),
        ];
        const tree = buildInstanceTree(roots, [], jobs);
        // Node a: header + 9 fit (rowCount 10), its other 3 overflow.
        // Node b: past the cap — its 4 jobs spill entirely.
        expect(tree.recentRoots.map((n) => n.instance.instance_id)).toEqual(['a']);
        expect(tree.recentRoots[0].attachedJobs.length).toBe(9);
        expect(tree.recentFlat.length).toBe(7); // 3 from a + 4 from b
      });

      it('orphan flat rows fill remaining capacity, then overflow appends after', () => {
        const roots = buildInstanceNodes([mkRow({ instance_id: 'n', status: 'completed' })]);
        const nodeJob = createMockJob({ job_id: 'jn', mission_id: 'n', status: 'completed' });
        const flatJobs = Array.from({ length: 12 }, (_, k) =>
          createMockJob({ job_id: `jf-${k}`, mission_id: null, status: 'cancelled' })
        );
        const tree = buildInstanceTree(roots, [], [nodeJob, ...flatJobs]);
        // header + jn + 8 flat = 10; 4 flat overflow.
        expect(tree.recentRoots[0].attachedJobs.length).toBe(1);
        expect(tree.recentFlat.length).toBe(12); // 8 in-band + 4 overflow
      });

      it('a childless terminal root still costs its 1 header row and renders if it fits', () => {
        const roots = buildInstanceNodes([mkRow({ instance_id: 'empty', status: 'terminated' })]);
        const tree = buildInstanceTree(roots, [], []);
        expect(tree.recentRoots.length).toBe(1);
        expect(tree.recentRoots[0].attachedJobs).toEqual([]);
      });

      it('structured band ≤ MAX: intermediate CHILD-instance headers consume capacity (NOT just root headers)', () => {
        // Recent root with 2 intermediate child-instance headers +
        // 6 attached receipts → headerCost = 1 (root) + 2 (kids) = 3.
        // Capacity = 10 - 0 - 3 = 7 → fitCount = min(6, 7) = 6.
        // Structured band = 3 + 6 = 9 (≤ MAX, no overflow).
        // No orphan flat, so total rendered rows = 9 (within MAX).
        const roots = buildInstanceNodes([
          mkRow({ instance_id: 'root', status: 'completed', children: ['kid-1', 'kid-2'] }),
          mkRow({ instance_id: 'kid-1', parent_id: 'root', status: 'failed' }),
          mkRow({ instance_id: 'kid-2', parent_id: 'root', status: 'completed' }),
        ]);
        const jobs = Array.from({ length: 6 }, (_, k) =>
          createMockJob({ job_id: `j-${k}`, mission_id: 'root', status: 'completed' })
        );
        const tree = buildInstanceTree(roots, [], jobs);
        expect(tree.recentRoots.length).toBe(1);
        // Structured band: root (1) + 2 intermediate headers (2) + 6 in-band jobs = 9.
        expect(1 + 2 + tree.recentRoots[0].attachedJobs.length).toBe(9);
        expect(tree.recentRoots[0].attachedJobs.length).toBe(6);
        expect(tree.recentFlat.length).toBe(0); // 6 fit inside capacity
      });

      it('structured band ≤ MAX + every-job-surfaces: deep subtree (root + 2 kids + 9 jobs) overflows the cap to recentFlat', () => {
        // headerCost = 1 + 2 = 3. Capacity = 10 - 0 - 3 = 7.
        // 9 jobs → fitCount = min(9, 7) = 7; remaining 2 overflow.
        // Structured band = 3 + 7 = 10 (exactly MAX). Total rendered
        // = 10 (in-band) + 2 (overflow) = 12 > MAX by design — never
        // hidden.
        const roots = buildInstanceNodes([
          mkRow({ instance_id: 'root', status: 'completed', children: ['kid-1', 'kid-2'] }),
          mkRow({ instance_id: 'kid-1', parent_id: 'root', status: 'failed' }),
          mkRow({ instance_id: 'kid-2', parent_id: 'root', status: 'completed' }),
        ]);
        const jobs = Array.from({ length: 9 }, (_, k) =>
          createMockJob({ job_id: `j-${k}`, mission_id: 'root', status: 'completed' })
        );
        const tree = buildInstanceTree(roots, [], jobs);
        // Structured band exactly MAX.
        expect(1 + 2 + tree.recentRoots[0].attachedJobs.length).toBe(10);
        // Every job surfaces — 2 overflow into recentFlat.
        expect(tree.recentFlat.length).toBe(2);
        // Total rendered = 12 > MAX — never hidden.
        const totalRendered =
          1 + 2 + tree.recentRoots[0].attachedJobs.length + tree.recentFlat.length;
        expect(totalRendered).toBe(12);
      });
    });
  });

  describe('shouldAutoExpandInstanceTree — FIRST 2 live roots (user-locked: exactly 2)', () => {
    it('returns exactly the first 2 live root ids when 3+ live roots exist', () => {
      const roots = buildInstanceNodes([
        mkRow({ instance_id: 'a' }),
        mkRow({ instance_id: 'b' }),
        mkRow({ instance_id: 'c' }),
      ]);
      const tree = buildInstanceTree(roots, [], []);
      expect(shouldAutoExpandInstanceTree(tree.liveRoots)).toEqual(['a', 'b']);
    });

    it('returns 1 id for a single live root and [] when only terminal roots exist', () => {
      const one = buildInstanceTree(buildInstanceNodes([mkRow({ instance_id: 'a' })]), [], []);
      expect(shouldAutoExpandInstanceTree(one.liveRoots)).toEqual(['a']);

      const none = buildInstanceTree(
        buildInstanceNodes([mkRow({ instance_id: 't', status: 'completed' })]),
        [],
        []
      );
      expect(shouldAutoExpandInstanceTree(none.liveRoots)).toEqual([]);
    });

    it('live-via-descendant roots count as live for auto-expand', () => {
      const roots = buildInstanceNodes([
        mkRow({ instance_id: 'root', status: 'completed', children: ['kid'], updated_at: '2026-09-08T11:00:00Z' }),
        mkRow({ instance_id: 'kid', parent_id: 'root', status: 'running' }),
        mkRow({ instance_id: 'other', status: 'running', updated_at: '2026-09-08T10:00:00Z' }),
      ]);
      const tree = buildInstanceTree(roots, [], []);
      // Sorted by activity desc: root (11:00) before other (10:00).
      expect(shouldAutoExpandInstanceTree(tree.liveRoots)).toEqual(['root', 'other']);
    });
  });

  describe('display helpers', () => {
    describe('instanceDisplayTitle — title → "agent · timeAgo" fallback chain', () => {
      const fmt = (d: string | null | undefined) => (d ? '9h ago' : '');

      it('prefers the server title', () => {
        expect(instanceDisplayTitle(mkRow({ title: 'Fix the bug' }), fmt)).toBe('Fix the bug');
      });

      it('falls back to "agent · timeAgo(created_at)"', () => {
        expect(instanceDisplayTitle(mkRow({ agent_id: 'worker' }), fmt)).toBe('worker · 9h ago');
      });

      it('falls back to agent alone when the timestamp is missing', () => {
        expect(instanceDisplayTitle(mkRow({ agent_id: 'worker', created_at: '' }), fmt)).toBe('worker');
      });

      it('falls back to the timestamp alone when agent_id is empty', () => {
        expect(instanceDisplayTitle(mkRow({ agent_id: '' }), fmt)).toBe('9h ago');
      });

      it('returns "" when both are missing (caller decides the placeholder)', () => {
        expect(instanceDisplayTitle(mkRow({ agent_id: '', created_at: '' }), fmt)).toBe('');
      });
    });

    describe('instanceMetaLine — "agent · N jobs · M agents · ago"', () => {
      const fmt = (d: string | null | undefined) => (d ? '5m ago' : '');

      it('joins agent, job count, child-agent count, and time', () => {
        const row = mkRow({ agent_id: 'lead', children: ['a', 'b'] });
        expect(instanceMetaLine(row, 3, fmt)).toBe('lead · 3 jobs · 2 agents · 5m ago');
      });

      it('singularises 1 job / 1 agent', () => {
        const row = mkRow({ agent_id: 'lead', children: ['a'] });
        expect(instanceMetaLine(row, 1, fmt)).toBe('lead · 1 job · 1 agent · 5m ago');
      });

      it('drops zero-count segments', () => {
        expect(instanceMetaLine(mkRow({ agent_id: 'lead' }), 0, fmt)).toBe('lead · 5m ago');
      });

      it('falls back to created_at then "idle" when no timestamps exist', () => {
        const row = mkRow({ agent_id: 'lead', updated_at: null, created_at: '2026-09-08T09:00:00Z' });
        expect(instanceMetaLine(row, 0, fmt)).toBe('lead · 5m ago');
        const bare = mkRow({ agent_id: 'lead', updated_at: null, created_at: '' });
        expect(instanceMetaLine(bare, 0, fmt)).toBe('lead · idle');
      });
    });
  });

  describe('visibleInstanceTreeItems — flatten in display order', () => {
    it('lists live roots first, then recent roots; collapsed descendants omitted', () => {
      const roots = buildInstanceNodes([
        mkRow({ instance_id: 'live-a' }),
        mkRow({ instance_id: 'rec-a', status: 'completed' }),
      ]);
      const tree = buildInstanceTree(roots, [], []);
      const items = visibleInstanceTreeItems(tree.liveRoots, tree.recentRoots, new Set());
      expect(items.map((it) => it.tree)).toEqual(['live', 'recent']);
      expect(items.every((it) => it.kind === 'instance')).toBe(true);
    });

    it('expanded node → child instances first, then its attached jobs (receipts are leaves)', () => {
      const roots = buildInstanceNodes([
        mkRow({ instance_id: 'root', children: ['kid'] }),
        mkRow({ instance_id: 'kid', parent_id: 'root' }),
      ]);
      const tree = buildInstanceTree(
        roots,
        [
          createMockJob({ job_id: 'j-root', mission_id: 'root', status: 'processing' }),
          createMockJob({ job_id: 'j-kid', mission_id: 'kid', status: 'processing' }),
        ],
        []
      );
      const items = visibleInstanceTreeItems(
        tree.liveRoots,
        tree.recentRoots,
        new Set(['root', 'kid'])
      );
      const ids = items.map((it) =>
        it.kind === 'instance' ? it.node.instance.instance_id : it.job.job_id
      );
      expect(ids).toEqual(['root', 'kid', 'j-kid', 'j-root']);
      expect(items.map((it) => it.depth)).toEqual([0, 1, 2, 1]);
    });

    it('job items carry parentInstanceId for ArrowLeft collapse', () => {
      const roots = buildInstanceNodes([mkRow({ instance_id: 'root' })]);
      const tree = buildInstanceTree(
        roots,
        [createMockJob({ job_id: 'j1', mission_id: 'root', status: 'processing' })],
        []
      );
      const items = visibleInstanceTreeItems(tree.liveRoots, tree.recentRoots, new Set(['root']));
      const jobItem = items.find((it) => it.kind === 'job');
      expect(jobItem).toBeDefined();
      if (jobItem!.kind === 'job') {
        expect(jobItem!.parentInstanceId).toBe('root');
      }
    });
  });

  describe('nextInstanceTreeItem + instanceTreeItemId', () => {
    const items = [
      { kind: 'instance' as const, tree: 'live' as const, depth: 0, parentInstanceId: null, node: { instance: mkRow({ instance_id: 'a' }), children: [], attachedJobs: [] } },
      { kind: 'instance' as const, tree: 'live' as const, depth: 0, parentInstanceId: null, node: { instance: mkRow({ instance_id: 'b' }), children: [], attachedJobs: [] } },
    ];

    it('clamps at both ends (no wrap)', () => {
      expect(nextInstanceTreeItem(items, 0, -1)).toBe(0);
      expect(nextInstanceTreeItem(items, 1, 1)).toBe(1);
    });

    it('no-focus (-1) resolves by direction', () => {
      expect(nextInstanceTreeItem(items, -1, 1)).toBe(0);
      expect(nextInstanceTreeItem(items, -1, -1)).toBe(1);
    });

    it('ids are tree-prefixed and collision-free across sections', () => {
      const inst = items[0];
      const job = {
        kind: 'job' as const,
        tree: 'live' as const,
        depth: 1,
        parentInstanceId: 'a',
        instanceId: 'a',
        job: createMockJob({ job_id: 'j1' }),
      };
      expect(instanceTreeItemId(inst)).toBe('inst:live|inst:a');
      expect(instanceTreeItemId(job)).toBe('inst:live|inst:a|job:j1');
    });
  });
});
