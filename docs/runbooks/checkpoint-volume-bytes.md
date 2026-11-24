# Runbook: DB-VOLUME bytes awareness for the checkpoint lane (Longhorn)

**Component:** the weekly **DB-VOLUME** lane — the external, operator-armed
job that watches the PostgreSQL data volume's *block-layer* occupancy so the
retention lane's growth never reaches the point where the volume is
silently full.
**Code owners:** none in the daemon — **this lane is EXTERNALLY armed and
runs OUTSIDE the ensemble daemon.** The daemon contributes only the
bytes-based occupancy signal it can measure itself
(`daemon/services/maintenance.py` → the `blob_bytes_guard` block in
`maintenance_runs.summary_json`; see
`docs/runbooks/checkpoint-escalation-lane.md` §VISIBILITY).
**Risk class:** READ-ONLY. The lane never writes to the volume, never
issues SQL, and never touches the daemon.

> **Sourcing legend.** `[measured 2026-11-23]` = a measurement taken during
> the post-outage recovery commission (read-only census; prod
> `ensemble_prod` @ 10.44.0.2 and the PG18 host's Longhorn volume).
> `[measured 2026-11-24]` = the read-only census taken by this lane's
> implementation (checkpoint table census, prod). `[house]` = standard
> Kubernetes / Longhorn operator mechanics added to operationalize a rule —
> no new system facts asserted.

---

## 0. Why `df` is NOT the signal

| Fact | Source |
|---|---|
| The PG18 data volume `pvc-770d03c4` is **60GiB `spec.size`** with **59.05GiB `actualSize`** — **98.4 % block-allocated** — while `ext4 df` inside the pod reports **48G used / 12G free**. | [measured 2026-11-23] |
| The ~11GiB gap is **never-TRIMmed high-water**: no TRIM/UNMAP ever ran, so the block layer keeps the high-water mark even after the space is logically free. | [measured 2026-11-23] |
| Therefore a **block-layer ENOSPC can strike while `df` still shows free space** (the 2026-09-26 WAL-recovery ENOSPC incident class). | [measured 2026-11-23] |

**RULE 0.1 — the lane's PRIMARY signal is `actualSize` vs `spec.size`, not
`df`.** `df` is a *secondary* corroboration only. A lane that reads `df`
alone will report "12G free" on a volume that is 98.4 % allocated.

**RULE 0.2 — the daemon NEVER shells out to `kubectl`.** There is no
daemon-side kubectl, and none may be added. The lane is a SEPARATE,
externally-armed job (a scheduled job on the OPERATOR's side) with its own
minimal credentials. This mirrors the lane boundary the checkpoint
maintenance already draws: the daemon measures and prunes *its own*
tables; the *volume* is the platform's business.

---

## 1. The read path (the CRD)

The Longhorn CRD is the read path. The group/version is
`longhorn.io/v1beta2`, resource `volumes` (plus `nodes` for the
node-level free space).

```bash
# ── the volume's block-layer occupancy ────────────────────────────────────
# actualSize = the space the block layer has allocated (the high-water
# mark, never TRIMmed down); spec.size = the requested capacity.
kubectl get volumes.longhorn.io -n longhorn-system \
  -o jsonpath='{range .items[?(@.metadata.name=="pvc-770d03c4")]}{.metadata.name}{"\t"}{.spec.size}{"\t"}{.status.actualSize}{"\n"}{end}'

# ── the same two numbers, parsed ─────────────────────────────────────────
kubectl get volumes.longhorn.io -n longhorn-system pvc-770d03c4 \
  -o jsonpath='{.spec.size}{"\t"}{.status.actualSize}{"\n"}'

# ── node-level free space (the node's own disk, not the volume) ──────────
kubectl get nodes.longhorn.io -n longhorn-system \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.diskStatus}{"\n"}{end}'
```

**RULE 1.1 — the lane reads the CRD, not the filesystem.** The volume's
occupancy is `status.actualSize`; the requested capacity is `spec.size`.

**RULE 1.2 — the node-level free space (`nodes.longhorn.io`
`.status.diskStatus.<id>.storageAvailable`) is a SECONDARY input.** A
volume can be 98 % allocated while its node still has room — and a
rewrite (the escalation lane's business) needs BOTH: room in the volume
for the new segments AND room on the node for the replicas.

**RULE 1.3 — the Longhorn REST endpoint
`longhorn-backend.longhorn-system:9500/v1/volumes` is an ALTERNATIVE read
path, not the primary one.** It is reachable without in-cluster auth and
is useful when the lane runs inside the cluster; the CRD path is the one
that needs no extra component. | [measured 2026-11-23]

---

## 2. The thresholds

| Threshold | Meaning | Action |
|---|---|---|
| **`actualSize / spec.size` ≥ 90 %** | **PRIMARY** occupancy signal — the volume is running out of *allocated* space. | ALERT (page the operator). Start the retention review: the growth is the retention lane's, and the escalation lane's proposal is the next step. |
| **free space on the filesystem (`df`) < 4 GiB** | the filesystem is nearly out — but note this is the SECONDARY signal and it can be misleading in BOTH directions (see RULE 0.1). | ABORT any rewrite-shaped action; ALERT. |
| **`actualSize / spec.size` > 99 %** | the volume is effectively full; a block-layer ENOSPC is imminent. | ABORT any rewrite-shaped action; ALERT loudly; the rewrite needs headroom the volume does not have. |

**RULE 2.1 — the PRIMARY signal is the RATIO (≥ 90 %), not an absolute
byte count.** The ratio is what makes the rule portable across volumes of
different sizes.

**RULE 2.2 — the two ABORT thresholds exist because a rewrite EXTENDS past
the historical high-water.** A rewrite writes a NEW segment set; it needs
free space for it. If the volume is at > 99 % of `spec.size` or the
filesystem has < 4 GiB free, the rewrite can push the volume past its
high-water into a block-ENOSPC — the silent death class, where `df` still
shows free space. See `docs/runbooks/checkpoint-escalation-lane.md` §3.

**RULE 2.3 — the lane ALERTS; it never reclaims.** Volume growth is the
retention lane's problem (retention + churn); volume *space* is the
rewrite's problem. See `docs/runbooks/checkpoint-escalation-lane.md` §5
for the honest inventory.

---

## 3. The dedicated-minimal-SA note

The lane must NOT run with a cluster-admin kubeconfig. It gets a
**dedicated ServiceAccount** whose RBAC is limited to the READ verbs on
the two Longhorn CRDs, in ONE namespace:

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: db-volume-observer
  namespace: longhorn-system          # the Longhorn namespace
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: db-volume-observer
  namespace: longhorn-system
rules:
  - apiGroups: ["longhorn.io"]
    resources: ["volumes", "nodes"]
    verbs: ["get", "list", "watch"]    # READ-ONLY — no create/update/patch/delete
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: db-volume-observer
  namespace: longhorn-system
subjects:
  - kind: ServiceAccount
    name: db-volume-observer
    namespace: <the lane's namespace>
roleRef:
  kind: Role
  name: db-volume-observer
  apiGroup: rbac.authorization.k8s.io
```

**RULE 3.1 — the SA is READ-ONLY: `get`/`list`/`watch` on
`longhorn.io/volumes` + `longhorn.io/nodes` in `longhorn-system`, and
nothing else.** No `secrets`, no `pods/exec`, no `nodes/proxy`, no
`cluster-admin`.

**RULE 3.2 — the SA lives in the lane's OWN namespace; the RoleBinding
cross-references it.** The binding is namespace-scoped, so the grant does
not widen beyond `longhorn-system`.

**RULE 3.3 — the lane never shells into the PostgreSQL pod** (no
`kubectl exec`): the database's own occupancy signal is measured by the
DAEMON (the `blob_bytes_guard` block), and the volume's is measured here.
Two signals, two owners, no overlap.

---

## 4. What the lane records

Each observation should carry, at minimum:

- the volume name, `spec.size`, `status.actualSize`, and the ratio;
- the node-level `storageAvailable` for the volume's replica nodes;
- the timestamp and the lane's own run id.

**RULE 4.1 — the receipt is honest about what it measured.** It reports
the ratio it read and the thresholds it compared against; it must NOT
claim the space was reclaimed (it reclaimed nothing) and must NOT claim
the database is healthy (the database's occupancy is a different signal,
measured elsewhere).

---

## 5. Cross-references

- `docs/runbooks/checkpoint-escalation-lane.md` — the escalation lane's
  proposal (the operator-nudged rewrite), and why the promise is
  "relieve growth", never "return the space".
- `docs/runbooks/checkpoint-maintenance.md` — the in-database reclaim
  mechanics (Op D retention, Op E blob prune, the TOAST hygiene audit).
- `docs/runbooks/checkpoint-blob-prune-restore.md` — the Op E restore
  path.
- The daemon-side bytes guard: `daemon/services/maintenance.py` →
  `_collect_blob_bytes_guard` (the `blob_bytes_guard` block in
  `maintenance_runs.summary_json`).
