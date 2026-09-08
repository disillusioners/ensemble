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

    // W4 — cycle guard. A self-loop / cyclic `parent_id` degrades the
    // row to a ROOT so the recursive helpers (collectSubtreeJobs,
    // sortInstanceNodes, visibleInstanceTreeItems walk, findNodeById)
    // cannot infinite-recurse. BE defends too; FE must not stack-
    // overflow on data skew. NEVER-hide preserved — the row still
    // renders, just not under its cyclic parent.
    it('W4 cycle guard — self-loop (parent_id === instance_id) degrades to root', () => {
      const rows = [mkRow({ instance_id: 'self', parent_id: 'self' })];
      const roots = buildInstanceNodes(rows);
      expect(roots.map((n) => n.instance.instance_id)).toEqual(['self']);
      expect(roots[0].children).toEqual([]);
    });

    it('W4 cycle guard — mutual A↔B cycle degrades one row to root (no stack overflow)', () => {
      const rows = [
        mkRow({ instance_id: 'A', parent_id: 'B' }),
        mkRow({ instance_id: 'B', parent_id: 'A' }),
      ];
      // Guard pin — must terminate and produce a finite tree (the
      // earlier walk-the-children helpers infinite-looped here).
      const roots = buildInstanceNodes(rows);
      expect(roots.length).toBeGreaterThan(0);
      const allIds = (ns: InstanceNode[]): string[] => {
        const out: string[] = [];
        const walk = (n: InstanceNode): void => {
          out.push(n.instance.instance_id);
          for (const c of n.children) walk(c);
        };
        for (const n of ns) walk(n);
        return out;
      };
      const ids = allIds(roots);
      expect(ids.sort()).toEqual(['A', 'B']);
      // No node's children should re-enter itself.
      const hasCycle = (n: InstanceNode, seen: Set<string>): boolean => {
        if (seen.has(n.instance.instance_id)) return true;
        seen.add(n.instance.instance_id);
        return n.children.some((c) => hasCycle(c, new Set(seen)));
      };
      for (const root of roots) {
        expect(hasCycle(root, new Set())).toBe(false);
      }
    });

    it('W4 cycle guard — 3-cycle A→B→C→A degrades to a finite tree', () => {
      const rows = [
        mkRow({ instance_id: 'A', parent_id: 'C' }),
        mkRow({ instance_id: 'B', parent_id: 'A' }),
        mkRow({ instance_id: 'C', parent_id: 'B' }),
      ];
      const roots = buildInstanceNodes(rows);
      const walk = (n: InstanceNode): string[] => [
        n.instance.instance_id,
        ...n.children.flatMap(walk),
      ];
      const ids = roots.flatMap(walk);
      expect(ids.sort()).toEqual(['A', 'B', 'C']);
    });

    // G6 — depth ≥ 3 job attach. The builder walks arbitrary depth
    // via `buildInstanceNodes` parent-child attachment; pin that a
    // grandchild instance (depth 3 from the root) is the one whose
    // receipts attach correctly.
    it('G6 depth ≥ 3 — a job attaches to a GRANDCHILD instance (root → mid → leaf)', () => {
      const rows = [
        mkRow({ instance_id: 'root', children: ['mid'] }),
        mkRow({ instance_id: 'mid', parent_id: 'root', children: ['leaf'] }),
        mkRow({ instance_id: 'leaf', parent_id: 'mid', agent_id: 'grandchild' }),
      ];
      const roots = buildInstanceNodes(rows);
      const tree = buildInstanceTree(
        roots,
        [createMockJob({ job_id: 'j-grandchild', mission_id: 'leaf', status: 'processing' })],
        []
      );
      const rootNode = tree.liveRoots[0];
      const midNode = rootNode.children[0];
      const leafNode = midNode.children[0];
      // The grandchild is the receipt's owner.
      expect(leafNode.instance.instance_id).toBe('leaf');
      expect(leafNode.instance.agent_id).toBe('grandchild');
      expect(leafNode.attachedJobs.map((j) => j.job_id)).toEqual(['j-grandchild']);
      // Higher-level nodes carry no attached jobs.
      expect(rootNode.attachedJobs).toEqual([]);
      expect(midNode.attachedJobs).toEqual([]);
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

    describe('F1 real-wire-shape attachment — grouping key mission_id ?? instance_id', () => {
      // SPEC HONESTY (2026-09-08 live-smoke fix F1): every fixture
      // above mocks ``mission_id`` directly — which is exactly why
      // they were BLIND to the real wire. On the live jobs LIST
      // (``GET /api/jobs``), ``JobQueueService.list_work(root_only=True)``
      // drops child-bound JobItems from work-record enrichment, so a
      // receipt bound to a CHILD instance ships ``mission_id: null``
      // while the raw ``JobItem`` column ``instance_id`` stays
      // populated. These fixtures therefore replicate the REAL list
      // wire shape (child-bound: instance_id set, mission_id null) —
      // a regression here means child receipts silently land in
      // Queued/recentFlat instead of under their child nodes again.

      it('attaches a child-bound receipt shipped as mission_id:null + instance_id set (REAL list wire shape)', () => {
        const roots = buildInstanceNodes([
          mkRow({ instance_id: 'root', children: ['kid'] }),
          mkRow({ instance_id: 'kid', parent_id: 'root', agent_id: 'worker' }),
        ]);
        // The REAL wire shape for a child-bound row: scalar mission_id
        // is NULL (BE list enrichment drops child-bound JobItems under
        // root_only=True); instance_id (the raw JobItem column) is
        // always populated. Must attach under the CHILD node — NOT
        // Queued, NOT recentFlat.
        const tree = buildInstanceTree(
          roots,
          [createMockJob({ job_id: 'j-child', mission_id: null, instance_id: 'kid', status: 'processing' })],
          []
        );
        expect(tree.liveRoots[0].attachedJobs).toEqual([]);
        expect(tree.liveRoots[0].children[0].attachedJobs.map((j) => j.job_id)).toEqual(['j-child']);
        expect(tree.queued).toEqual([]);
        expect(tree.recentFlat).toEqual([]);
      });

      it('mixed wire page: root-bound (mission_id set) + child-bound (mission_id null) BOTH attach under their own nodes', () => {
        const roots = buildInstanceNodes([
          mkRow({ instance_id: 'root', children: ['kid'] }),
          mkRow({ instance_id: 'kid', parent_id: 'root', agent_id: 'worker' }),
        ]);
        const tree = buildInstanceTree(
          roots,
          [
            createMockJob({ job_id: 'j-root-bound', mission_id: 'root', instance_id: 'root', status: 'processing' }),
            createMockJob({ job_id: 'j-child-bound', mission_id: null, instance_id: 'kid', status: 'processing' }),
          ],
          []
        );
        // Root-bound receipt: under the ROOT node.
        expect(tree.liveRoots[0].attachedJobs.map((j) => j.job_id)).toEqual(['j-root-bound']);
        // Child-bound receipt: under the CHILD node (not the root, not queued).
        expect(tree.liveRoots[0].children[0].attachedJobs.map((j) => j.job_id)).toEqual(['j-child-bound']);
        expect(tree.queued).toEqual([]);
      });

      it('a terminal child-bound receipt with mission_id:null attaches under its child node (not recentFlat)', () => {
        const roots = buildInstanceNodes([
          mkRow({ instance_id: 'root', children: ['kid'] }),
          mkRow({ instance_id: 'kid', parent_id: 'root', agent_id: 'worker' }),
        ]);
        const tree = buildInstanceTree(
          roots,
          [],
          [createMockJob({ job_id: 'j-term-child', mission_id: null, instance_id: 'kid', status: 'settled' })]
        );
        expect(tree.recentFlat).toEqual([]);
        expect(tree.liveRoots[0].children[0].attachedJobs.map((j) => j.job_id)).toEqual(['j-term-child']);
      });
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

    describe('Recent cap — JOB-row band ≤ MAX_RECENT_INSTANCE_ROWS + every-job-surfaces (overflow → recentFlat)', () => {
      it('cap constant stays in the shared 10-row cap class', () => {
        expect(MAX_RECENT_INSTANCE_ROWS).toBe(10);
      });

      it('JOB-row band ≤ MAX: a flat terminal root with more jobs than fit keeps a PARTIAL fit; the rest overflow to recentFlat', () => {
        // Headers ride along — no intermediate child-instance headers,
        // no header consumption. 12 jobs on a childless root: 10 jobs
        // fit (JOB-row band = MAX); the remaining 2 overflow to
        // recentFlat (every-job-surfaces — NEVER-hide). The recent
        // root's own header is free structure, not counted.
        const roots = buildInstanceNodes([mkRow({ instance_id: 'big', status: 'completed' })]);
        const jobs = Array.from({ length: 12 }, (_, k) =>
          createMockJob({ job_id: `j-${k}`, mission_id: 'big', status: 'completed' })
        );
        const tree = buildInstanceTree(roots, [], jobs);
        expect(tree.recentRoots.length).toBe(1);
        expect(tree.recentRoots[0].attachedJobs.length).toBe(10); // JOB-row band = MAX
        expect(tree.recentFlat.length).toBe(2); // 2 overflow — never hidden
      });

      it('JOB-row band ≤ MAX: a node past the cap spills ALL its jobs to overflow (header still rides along)', () => {
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
        // Node a: 10 fit (JOB-row band = MAX), its other 2 overflow.
        // Node b: JOB-row band already at cap — its 4 jobs spill
        // entirely. But node b's HEADER rides along (free structure)
        // so it still appears in recentRoots with zero visible jobs.
        expect(tree.recentRoots.map((n) => n.instance.instance_id)).toEqual(['a', 'b']);
        expect(tree.recentRoots[0].attachedJobs.length).toBe(10);
        expect(tree.recentRoots[1].attachedJobs.length).toBe(0);
        expect(tree.recentFlat.length).toBe(6); // 2 from a + 4 from b
      });

      it('orphan flat rows fill remaining capacity, then overflow appends after', () => {
        const roots = buildInstanceNodes([mkRow({ instance_id: 'n', status: 'completed' })]);
        const nodeJob = createMockJob({ job_id: 'jn', mission_id: 'n', status: 'completed' });
        const flatJobs = Array.from({ length: 12 }, (_, k) =>
          createMockJob({ job_id: `jf-${k}`, mission_id: null, status: 'cancelled' })
        );
        const tree = buildInstanceTree(roots, [], [nodeJob, ...flatJobs]);
        // JOB-row band: jn (1) + 9 in-band flat = 10 (≤ MAX); 3 flat overflow.
        expect(tree.recentRoots[0].attachedJobs.length).toBe(1);
        expect(tree.recentFlat.length).toBe(12); // 9 in-band + 3 overflow
      });

      it('a childless terminal root still renders its header (free structure, zero-job consumption)', () => {
        const roots = buildInstanceNodes([mkRow({ instance_id: 'empty', status: 'terminated' })]);
        const tree = buildInstanceTree(roots, [], []);
        expect(tree.recentRoots.length).toBe(1);
        expect(tree.recentRoots[0].attachedJobs).toEqual([]);
      });

      it('JOB-row band: intermediate CHILD-instance headers ride along (do NOT consume capacity)', () => {
        // Recent root with 2 intermediate child-instance headers +
        // 6 attached receipts. Headers ride along — no capacity
        // consumed by child-instance headers. 6 jobs ≤ MAX → all fit.
        // JOB-row band = 6 (≤ MAX, no overflow).
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
        // JOB-row band = 6 (headers ride along).
        expect(tree.recentRoots[0].attachedJobs.length).toBe(6);
        expect(tree.recentFlat.length).toBe(0); // 6 fit inside JOB-row band
      });

      it('JOB-row band ≤ MAX + every-job-surfaces: 9 jobs + headers still fit when under MAX', () => {
        // 9 jobs on a subtree with 2 intermediate child-instance
        // headers. Headers ride along → 9 jobs fit in the JOB-row band
        // (9 ≤ MAX). No overflow. Total rendered = 1 root header + 2
        // kid headers + 9 job rows = 12 (> MAX by design — never hidden
        // is job-row semantics, not row-count).
        const roots = buildInstanceNodes([
          mkRow({ instance_id: 'root', status: 'completed', children: ['kid-1', 'kid-2'] }),
          mkRow({ instance_id: 'kid-1', parent_id: 'root', status: 'failed' }),
          mkRow({ instance_id: 'kid-2', parent_id: 'root', status: 'completed' }),
        ]);
        const jobs = Array.from({ length: 9 }, (_, k) =>
          createMockJob({ job_id: `j-${k}`, mission_id: 'root', status: 'completed' })
        );
        const tree = buildInstanceTree(roots, [], jobs);
        // JOB-row band exactly 9 (≤ MAX, headers ride along).
        expect(tree.recentRoots[0].attachedJobs.length).toBe(9);
        // No overflow — every job fits the JOB-row band.
        expect(tree.recentFlat.length).toBe(0);
      });

      it('JOB-row band ≤ MAX + every-job-surfaces: 13 jobs on deep subtree overflows the JOB-row band to recentFlat', () => {
        // 13 jobs on a subtree with 2 intermediate child-instance
        // headers. Headers ride along → JOB-row band = min(13, 10) = 10;
        // remaining 3 overflow. JOB-row band = MAX exactly.
        const roots = buildInstanceNodes([
          mkRow({ instance_id: 'root', status: 'completed', children: ['kid-1', 'kid-2'] }),
          mkRow({ instance_id: 'kid-1', parent_id: 'root', status: 'failed' }),
          mkRow({ instance_id: 'kid-2', parent_id: 'root', status: 'completed' }),
        ]);
        const jobs = Array.from({ length: 13 }, (_, k) =>
          createMockJob({ job_id: `j-${k}`, mission_id: 'root', status: 'completed' })
        );
        const tree = buildInstanceTree(roots, [], jobs);
        // JOB-row band exactly MAX.
        expect(tree.recentRoots[0].attachedJobs.length).toBe(10);
        // Every job surfaces — 3 overflow into recentFlat.
        expect(tree.recentFlat.length).toBe(3);
      });

      // W1 — multi-root deep-subtree pin (reviewer's 3-root trace).
      // Reviewer's failing case hit 12 > 10 under the OLD
      // structured-band cap. With JOB-row semantics, the same shape
      // fits: 3 roots × 2 intermediate headers + 6 jobs total = 3
      // root headers + 6 kid headers + 6 jobs in band; JOB-row band
      // = 6 (≤ MAX), no overflow. The trace the reviewer hit becomes
      // a passing pin.
      it('W1 multi-root deep-subtree pin (3 roots × kid-headers + 6 jobs): JOB-row band ≤ MAX, headers ride along, every job surfaces', () => {
        const roots = buildInstanceNodes([
          mkRow({
            instance_id: 'r1',
            status: 'completed',
            updated_at: '2026-09-08T12:00:00Z',
            children: ['r1-k1', 'r1-k2'],
          }),
          mkRow({ instance_id: 'r1-k1', parent_id: 'r1', status: 'failed' }),
          mkRow({ instance_id: 'r1-k2', parent_id: 'r1', status: 'completed' }),
          mkRow({
            instance_id: 'r2',
            status: 'completed',
            updated_at: '2026-09-08T11:00:00Z',
            children: ['r2-k1', 'r2-k2'],
          }),
          mkRow({ instance_id: 'r2-k1', parent_id: 'r2', status: 'failed' }),
          mkRow({ instance_id: 'r2-k2', parent_id: 'r2', status: 'completed' }),
          mkRow({
            instance_id: 'r3',
            status: 'completed',
            updated_at: '2026-09-08T10:00:00Z',
            children: ['r3-k1', 'r3-k2'],
          }),
          mkRow({ instance_id: 'r3-k1', parent_id: 'r3', status: 'failed' }),
          mkRow({ instance_id: 'r3-k2', parent_id: 'r3', status: 'completed' }),
        ]);
        // 6 receipts across the 3 subtrees (2 each) — well under MAX.
        const jobs = [
          ...Array.from({ length: 2 }, (_, k) =>
            createMockJob({ job_id: `r1-j-${k}`, mission_id: 'r1', status: 'completed' })
          ),
          ...Array.from({ length: 2 }, (_, k) =>
            createMockJob({ job_id: `r2-j-${k}`, mission_id: 'r2', status: 'completed' })
          ),
          ...Array.from({ length: 2 }, (_, k) =>
            createMockJob({ job_id: `r3-j-${k}`, mission_id: 'r3', status: 'completed' })
          ),
        ];
        const tree = buildInstanceTree(roots, [], jobs);
        // All 3 recent roots push (headers ride along, no skip path).
        expect(tree.recentRoots.length).toBe(3);
        // JOB-row band ≤ MAX (6 ≤ 10).
        const inBandJobs = tree.recentRoots.reduce(
          (sum, n) => sum + n.attachedJobs.length,
          0
        );
        expect(inBandJobs).toBe(6);
        expect(inBandJobs).toBeLessThanOrEqual(MAX_RECENT_INSTANCE_ROWS);
        // No overflow (6 fits).
        expect(tree.recentFlat.length).toBe(0);
        // Every job surfaces (6 in-band + 0 overflow = 6 = total input).
        const totalSurfaced = inBandJobs + tree.recentFlat.length;
        expect(totalSurfaced).toBe(6);
      });

      it('W1 multi-root deep-subtree pin (3 roots × kid-headers + 18 jobs): JOB-row band ≤ MAX, every job surfaces (8 overflow)', () => {
        // 18 jobs across 3 subtrees of 6 each. JOB-row band = 10;
        // remaining 8 overflow.
        const roots = buildInstanceNodes([
          mkRow({
            instance_id: 'r1',
            status: 'completed',
            updated_at: '2026-09-08T12:00:00Z',
            children: ['r1-k1', 'r1-k2'],
          }),
          mkRow({ instance_id: 'r1-k1', parent_id: 'r1', status: 'failed' }),
          mkRow({ instance_id: 'r1-k2', parent_id: 'r1', status: 'completed' }),
          mkRow({
            instance_id: 'r2',
            status: 'completed',
            updated_at: '2026-09-08T11:00:00Z',
            children: ['r2-k1', 'r2-k2'],
          }),
          mkRow({ instance_id: 'r2-k1', parent_id: 'r2', status: 'failed' }),
          mkRow({ instance_id: 'r2-k2', parent_id: 'r2', status: 'completed' }),
          mkRow({
            instance_id: 'r3',
            status: 'completed',
            updated_at: '2026-09-08T10:00:00Z',
            children: ['r3-k1', 'r3-k2'],
          }),
          mkRow({ instance_id: 'r3-k1', parent_id: 'r3', status: 'failed' }),
          mkRow({ instance_id: 'r3-k2', parent_id: 'r3', status: 'completed' }),
        ]);
        const jobs = [
          ...Array.from({ length: 6 }, (_, k) =>
            createMockJob({ job_id: `r1-j-${k}`, mission_id: 'r1', status: 'completed' })
          ),
          ...Array.from({ length: 6 }, (_, k) =>
            createMockJob({ job_id: `r2-j-${k}`, mission_id: 'r2', status: 'completed' })
          ),
          ...Array.from({ length: 6 }, (_, k) =>
            createMockJob({ job_id: `r3-j-${k}`, mission_id: 'r3', status: 'completed' })
          ),
        ];
        const tree = buildInstanceTree(roots, [], jobs);
        // All 3 recent roots push (headers ride along).
        expect(tree.recentRoots.length).toBe(3);
        // JOB-row band ≤ MAX (10 exactly).
        const inBandJobs = tree.recentRoots.reduce(
          (sum, n) => sum + n.attachedJobs.length,
          0
        );
        expect(inBandJobs).toBe(10);
        expect(inBandJobs).toBeLessThanOrEqual(MAX_RECENT_INSTANCE_ROWS);
        // 8 overflow into recentFlat.
        expect(tree.recentFlat.length).toBe(8);
        // Every job surfaces (10 + 8 = 18 = total input).
        const totalSurfaced = inBandJobs + tree.recentFlat.length;
        expect(totalSurfaced).toBe(18);
      });

      // W1 — empty-subtree node rule (leader-decided). A recent root
      // with zero subtree jobs pushes UNCONDITIONALLY (zero-capacity
      // consumption; pure structure). The next candidate still gets its
      // full MAX allotment.
      it('W1 empty-subtree rule: a recent root with zero jobs pushes unconditionally (no capacity consumed)', () => {
        const roots = buildInstanceNodes([
          mkRow({ instance_id: 'empty-1', status: 'completed', updated_at: '2026-09-08T11:00:00Z' }),
          mkRow({ instance_id: 'empty-2', status: 'terminated', updated_at: '2026-09-08T10:00:00Z' }),
          mkRow({ instance_id: 'big', status: 'completed', updated_at: '2026-09-08T09:00:00Z' }),
        ]);
        // 12 jobs on `big` — under the JOB-row-only cap, all 12 fit
        // the band because empty-1/empty-2 consumed zero capacity.
        const jobs = Array.from({ length: 12 }, (_, k) =>
          createMockJob({ job_id: `j-${k}`, mission_id: 'big', status: 'completed' })
        );
        const tree = buildInstanceTree(roots, [], jobs);
        // All 3 recent roots push (empty-1 + empty-2 + big).
        expect(tree.recentRoots.map((n) => n.instance.instance_id)).toEqual([
          'empty-1',
          'empty-2',
          'big',
        ]);
        // The empty ones carry zero jobs (pure structure).
        expect(tree.recentRoots[0].attachedJobs.length).toBe(0);
        expect(tree.recentRoots[1].attachedJobs.length).toBe(0);
        // `big` got its full MAX allotment because empties were free.
        expect(tree.recentRoots[2].attachedJobs.length).toBe(10);
        expect(tree.recentFlat.length).toBe(2);
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

    describe('instanceMetaLine — "agent · N jobs · M agents · ago" (W3: M = built children, not wire row.children)', () => {
      const fmt = (d: string | null | undefined) => (d ? '5m ago' : '');

      // Helper — wrap a row into a built InstanceNode (mimics the
      // tree builder output the panel sees). The row's wire `children`
      // field is intentionally DISTINCT from node.children in the
      // over-count fixture below — the wire is pre-KB-strip; the tree
      // is what the user sees.
      const wrap = (row: InstanceRow, builtChildIds: string[] = row.children): InstanceNode => ({
        instance: row,
        children: builtChildIds.map((id) => ({
          instance: { ...mkRow({ instance_id: id }), children: [] },
          children: [],
          attachedJobs: [],
        })),
        attachedJobs: [],
      });

      it('joins agent, job count, built-child count, and time', () => {
        const row = mkRow({ agent_id: 'lead', children: ['a', 'b'] });
        const node = wrap(row, ['a', 'b']);
        expect(instanceMetaLine(node, 3, fmt)).toBe('lead · 3 jobs · 2 agents · 5m ago');
      });

      it('singularises 1 job / 1 agent', () => {
        const row = mkRow({ agent_id: 'lead', children: ['a'] });
        const node = wrap(row, ['a']);
        expect(instanceMetaLine(node, 1, fmt)).toBe('lead · 1 job · 1 agent · 5m ago');
      });

      it('drops zero-count segments', () => {
        const node = wrap(mkRow({ agent_id: 'lead' }), []);
        expect(instanceMetaLine(node, 0, fmt)).toBe('lead · 5m ago');
      });

      it('falls back to created_at then "idle" when no timestamps exist', () => {
        const row = mkRow({ agent_id: 'lead', updated_at: null, created_at: '2026-09-08T09:00:00Z' });
        const node = wrap(row, []);
        expect(instanceMetaLine(node, 0, fmt)).toBe('lead · 5m ago');
        const bare = wrap(mkRow({ agent_id: 'lead', updated_at: null, created_at: '' }), []);
        expect(instanceMetaLine(bare, 0, fmt)).toBe('lead · idle');
      });

      // W3 pin — the meta line MUST count BUILT nested children
      // (node.children.length), not the wire row.children pre-KB-
      // strip list. A wire over-count degrades to the BUILT count.
      it('W3: M agents counts node.children.length (BUILT), NOT row.children (wire pre-KB-strip)', () => {
        const row = mkRow({ agent_id: 'lead', children: ['a', 'b', 'c'] });
        const node = wrap(row, ['a']); // wire=3, built=1
        expect(instanceMetaLine(node, 0, fmt)).toBe('lead · 1 agent · 5m ago');
        // Wire over-count would have read "3 agents" — the BUILT count wins.
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

  // F1 source-drift pin (2026-09-08): the REAL production grouping
  // key must be the COALESCED ``job.mission_id ?? job.instance_id``.
  // The behavioural fixtures above run against buildInstanceTree, but
  // a scalar-only revert of the ``route`` keying (``mission_id ??
  // null``) would slip past any fixture whose mocks populate
  // mission_id directly — the exact test-blindness that let F1 ship.
  // This pin reads the production source so a scalar-only revert
  // fails loudly.
  describe('production source pin — coalesced grouping key', () => {
    let modelTs: string;

    beforeAll(() => {
      const path = require('path');
      const fs = require('fs');
      modelTs = fs.readFileSync(path.join(__dirname, 'instance-node.model.ts'), 'utf-8');
    });

    it('route() keys attachments via job.mission_id ?? job.instance_id ?? null', () => {
      expect(modelTs).toContain('job.mission_id ?? job.instance_id ?? null');
    });

    it('does NOT key attachments on the scalar mission_id alone', () => {
      expect(modelTs).not.toContain('job.mission_id ?? null');
    });
  });
});
