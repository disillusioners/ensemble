# Resource Hygiene — Operator Checklist (O7)

Date: 2026-10-18 · Scope: resource-cost-control O7 hygiene items + 2026-10-18 OOM-delta operator bounds ·
Plan: `.agents/shared/planning/resource-cost-control/plan-overview.md` (runbook:
`docs/runbooks/resource-cost-control.md` §systemd-bounds).

> **WHO EXECUTES THIS: the human operator ONLY.** Agents must **never execute** any
> command in this checklist against **live** infrastructure — not the systemd mutations,
> not the drops, not the kills. Every item here mutates a running host. This document is
> a *plan/runbook surface only*; the resource-cost-control commission executed ZERO live
> mutations to produce it.

---

## How to use this checklist

1. **Run the DRY-RUN FIRST — always.** Every destructive item below (database drops,
   process kills) has an explicit list-what-matches step BEFORE any destructive command.
   Never skip it. Never chain the dry-run and the destructive step in one paste.
2. **Respect the `USER DECISION` gates.** Two items (swap provisioning, demo-daemon
   keep/kill) are decisions only the operator/owner can make. Agents and checklists do
   not make them; this document only frames the trade-off and, if opted in, provides the
   exact commands.
3. **Pattern + age gates are mandatory.** The database cleanup matches ONLY four
   explicit prefixes and only databases older than 7 days. Widen neither without
   re-reading the why.
4. **Rollback is listed per item.** Read it BEFORE running the forward command.

---

## (a) systemd memory bounds for `ensemble-main.service`

**Why.** The unit ran with `MemoryMax=infinity` — the box died without bounds. This is a
DEMONSTRATED mechanism, not a hypothetical: 3 kernel OOM deaths the night of Oct 17
(daemon killed **23:19:14Z**), unit peak **8.4 GB**, box (9.9 GB RAM, zero swap) at
**6.7 GB** at recovery.

**Derivation of the starting values** *(approximate working values for bound-setting,
not calibrated measurements — tunable once real headroom is observed via
`/api/resources`)*:

- `MemoryHigh=7G` — soft throttle where reclaim pressure begins; 1 G headroom over the
  5.3–6.0 G steady cgroup band (which includes reclaimable page cache).
- `MemoryMax=8G` — hard cap; converts box-death into a unit-scoped kill
  (8 G + PG ≈ 0.28 + demo ≈ 0.4 + OD ≈ 0.17 + OS ≈ 9.94 G box — so systemd kills the
  UNIT instead of the kernel killing the BOX).

```bash
# Read current bounds first (expect MemoryMax=infinity):
systemctl show ensemble-main.service -p MemoryHigh -p MemoryMax

# Drop-in override (non-interactive):
sudo mkdir -p /etc/systemd/system/ensemble-main.service.d
sudo tee /etc/systemd/system/ensemble-main.service.d/10-memory-bounds.conf >/dev/null <<'EOF'
[Service]
MemoryHigh=7G
MemoryMax=8G
EOF
sudo systemctl daemon-reload
sudo systemctl restart ensemble-main.service

# Verify applied:
systemctl show ensemble-main.service -p MemoryHigh -p MemoryMax
```

**Rollback.** Remove the drop-in file, reload, restart:

```bash
sudo rm /etc/systemd/system/ensemble-main.service.d/10-memory-bounds.conf
sudo systemctl daemon-reload
sudo systemctl restart ensemble-main.service
```

**Tuning.** Once `/api/resources` shows real post-bound headroom, adjust the two values
in the drop-in and re-run daemon-reload + restart. Note: a restart is a daemon restart —
pick a quiet window.

---

## (b) Swap provisioning — **USER DECISION**

**Why this is a user decision.** The box has **zero swap**, so any transient allocation
spike goes straight to the kernel OOM killer — the demonstrated Oct-17 kill mechanism.
A modest swapfile is cheap pressure-valve insurance (transients page out instead of
dying). Some operators legitimately prefer strict no-swap + the hard cgroup bounds from
(a) instead. **The keep-swapless-vs-provision call belongs to the user** — nothing in
this commission forces it either way. If opted in, example (size/placement = user's
call):

```bash
# Example 8G swapfile:
sudo fallocate -l 8G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
printf '/swapfile none swap sw 0 0\n' | sudo tee -a /etc/fstab   # persist across boots
free -h   # verify
```

**Rollback.**

```bash
sudo swapoff /swapfile
sudo sed -i '\|^/swapfile none swap sw 0 0$|d' /etc/fstab
sudo rm /swapfile
free -h   # verify swap gone
```

---

## (c) Test-database cleanup (~1 GB sprawl) — **DRY-RUN FIRST, pattern + age gated**

**Why.** The test farm left ~1 GB of residue databases on the local PostgreSQL instance
(`ensemble_blob_prune_*` sprawl observed 2026-10-17). Only the four prefixes below are
in scope; anything else on the instance is NOT touched by this item.

**Pattern gate (ONLY these four prefixes):**
`ensemble_blob_prune_*` · `check_score_*` · `debug_*` · `repro_*`

**Age gate: older than 7 days.** **Dry-run listing is a mandatory ordered step — run
Step 1, read the output, and only then proceed to Step 3.**

**Step 1 — DRY-RUN FIRST: list what matches (and their ages) before any DROP.**

```bash
# Peer-local psql as the postgres superuser (adjust the peer user if your
# install differs). List candidate databases matching the four prefixes:
sudo -u postgres psql -At -c "
  SELECT datname FROM pg_database
  WHERE datname LIKE 'ensemble_blob_prune_%'
     OR datname LIKE 'check_score_%'
     OR datname LIKE 'debug_%'
     OR datname LIKE 'repro_%'
  ORDER BY datname;"
```

PostgreSQL does not expose a per-database creation timestamp directly; derive the age
from the on-disk directory mtime of each database (`base/<oid>`). Run this SECOND part
of the same dry-run and read the `age_days` column:

```bash
sudo -u postgres psql -At -F' ' -c "
  SELECT oid, datname FROM pg_database
  WHERE datname LIKE 'ensemble_blob_prune_%'
     OR datname LIKE 'check_score_%'
     OR datname LIKE 'debug_%'
     OR datname LIKE 'repro_%';" | while read -r oid name; do
  dir=$(ls -d /var/lib/postgresql/*/base/"$oid" 2>/dev/null | head -1)
  if [ -n "$dir" ]; then
    age=$(( ( $(date +%s) - $(stat -c %Y "$dir") ) / 86400 ))
    printf '%s age_days=%s\n' "$name" "$age"
  else
    printf '%s age_days=UNKNOWN (no base/<oid> dir — investigate before dropping)\n' "$name"
  fi
done
```

**Step 2 — review the listing.** The drop set is exactly the databases that appeared in
Step 1 AND show `age_days > 7`. If a database you do not recognize appears, STOP — it is
out of scope for this checklist.

**Step 3 — drop (only after Steps 1–2; only `age_days > 7` rows from the listing):**

```bash
# Repeat per database confirmed by the dry-run listing:
sudo -u postgres psql -c "DROP DATABASE <confirmed_name>;"
```

**Rollback.** None — **drops are irreversible. That is exactly WHY the dry-run listing
and the age gate are mandatory ordered steps.** The only mitigation is the pattern gate
+ age gate + eyeballing the Step-1 listing before dropping.

---

## (d) Kill the orphaned demo `context7-mcp` child (~97 MB)

**Why.** A context7-mcp child from a prior demo-daemon incarnation survived its parent
(leaked across restarts) and held ~97 MB for 4+ days. This is the same orphan class the
landed O8a boot-time sweep reaps automatically in the prod lane
(`daemon/services/boot_subprocess_sweep.py`); this item handles the manual case on hosts
where the sweep has not yet run.

**Step 1 — identify (do NOT kill on name match alone):**

```bash
# Find the candidate + its start time:
ps -eo pid,ppid,lstart,etime,cmd | grep -i '[c]ontext7'

# Confirm the parent chain is dead/older than the current boot:
#   - ppid == 1 (reparented to init), OR the ppid process no longer exists
#   - lstart predates the current daemon incarnation (compare with
#     `systemctl show ensemble-demo.service -p ActiveEnterTimestamp`)
cat /proc/<PID>/stat | awk '{print "starttime_jiffies:", $22}'
systemctl status ensemble-demo.service --no-pager | head -5

# Confirm cgroup membership (expect the demo unit's cgroup, i.e. a stale slice):
cat /proc/<PID>/cgroup
```

**Step 2 — kill (only after Step 1 confirms parent-dead + stale start time):**

```bash
kill <PID>            # TERM first
sleep 5
kill -0 <PID> 2>/dev/null && kill -9 <PID>   # KILL escalation only if it survived
```

**Rollback.** Not needed — the process is an orphan; the demo lane re-spawns its MCP
children on its next start.

---

## (e) Demo daemon keep/kill — **USER DECISION** (~395 MB)

**Why this is a user decision.** `ensemble-demo.service` is a demo lane consuming ~395 MB
on the same box. Only the owner knows whether the demo lane is still wanted. This
checklist does not recommend either way.

**Identification (read-only) — for the user's decision:**

```bash
systemctl status ensemble-demo.service --no-pager
systemd-cgtop -m -1 | grep ensemble
systemctl show ensemble-demo.service -p MemoryCurrent -p ActiveEnterTimestamp -p ExecMainPID
```

If the user decides to stop it:

```bash
sudo systemctl stop ensemble-demo.service
sudo systemctl disable ensemble-demo.service   # optional — only if never wanted at boot
```

**Rollback.** `sudo systemctl start ensemble-demo.service` (re-enable with
`sudo systemctl enable ensemble-demo.service` if it was disabled).

---

## (f) `MALLOC_ARENA_MAX=2` on the INSTALLED live unit (O1 follow-through note)

**Why.** The O1 optimization landed `Environment=MALLOC_ARENA_MAX=2` in the repo's
systemd TEMPLATE (`scripts/systemd/ensemble-daemon.service:106`), but the INSTALLED live
unit predates O1 — the line reaches the live host only at the next
adoption/unit-regeneration pass.

**Sooner option (safe to apply independently of adoption), using the same drop-in
mechanism as (a):**

```bash
sudo mkdir -p /etc/systemd/system/ensemble-main.service.d
sudo tee /etc/systemd/system/ensemble-main.service.d/10-malloc-arena.conf >/dev/null <<'EOF'
[Service]
Environment=MALLOC_ARENA_MAX=2
EOF
sudo systemctl daemon-reload
sudo systemctl restart ensemble-main.service
```

(Or fold it into the (a) drop-in file — one restart instead of two.)

**Verification.** After restart:
`systemctl show ensemble-main.service -p Environment | tr ' ' '\n' | grep MALLOC`
and confirm the daemon's RSS trend via `/api/resources` (see (g)).

**Rollback.** Remove the drop-in file + daemon-reload + restart (same shape as (a)'s
rollback).

---

## (g) O1 effect verification (read-only measurement protocol)

**Why.** O1's ~170–400 MB (−10~30 % anon) saving is a soft estimate bounded by the
glibc-arena-retention assumption. The measurement step decides it — read-only, no live
mutation.

**Protocol.**

1. Baseline (pre-O1-live): daemon RSS flat **1.68–1.72 GB** (recorded 2026-10-17).
2. After the bounds/adoption from (a)/(f) are live, read
   `GET /api/resources/history` (hour resolution) at **T+24 h** and **T+72 h**.
3. Expected: −10~30 % of anon RSS.
4. **Decision rule: real reduction < 5 % ⇒ O3 (instance-cache eviction,
   `LIMITS_INSTANCE_CACHE_TTL_HOURS` / `LIMITS_INSTANCE_CACHE_MAX_ENTRIES`) becomes the
   dominant lever** — record the finding and correct the saving ledger; O1 itself is
   kept (harmless, one-line revert).

**Rollback.** None needed — the protocol is read-only.
