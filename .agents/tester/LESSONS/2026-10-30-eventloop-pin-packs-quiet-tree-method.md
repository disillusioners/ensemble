# Eventloop pin packs — the quiet-tree method, and three traps it caught

**Commission:** `wi-3f5bea95` (event-loop sync-DB hot paths), tester lane.
**Date:** 2026-10-30. **Worktree:** `fix/eventloop-sync-db-hotpaths`.

## The rule I am keeping

> **Evidence for a gate is measured at a PINNED COMMIT, in a DETACHED worktree, one
> writer at a time. A lane worktree is a working surface, not an evidence surface.**

Recipe (reused four times in this commission, ~35 s total):

1. `git worktree add --detach <scratch> <commit>` — pre-check the path is ABSENT; never reuse.
2. Prove the tree: `git -C <scratch> rev-parse HEAD` == commit, `git status --porcelain`
   EMPTY. Take pack files **from git** (`git show <commit>:<path> | sha256sum`) — no overlay
   when they are already committed at that commit.
3. Prove the interpreter actually loads that tree: run with
   `cd <scratch> && PYTHONPATH=<scratch> <venv-python>` and print `daemon.__file__` — it
   must resolve **under the scratch path**. Inspect `_editable_impl_ensemble.pth`: a
   **static path entry** loses to `PYTHONPATH`; a **MetaPathFinder shim** would NOT, and
   the run would silently test another checkout.
4. Porcelain EMPTY before AND after each pack run (catches a pack that writes side files).
5. Run packs **sequentially**, `timeout 300` outer wrapper, ini `timeout=30` kept,
   per-variable env scrub (never `env -u POSTGRES_*` — a silent no-op).
6. Quote the numbers with the commit they were measured at. A registry row is only as
   fresh as the commit it names.

## Trap 1 — a shared worktree with several writers is not a measurement

I dispatched three pack authors into ONE lane worktree, each planting and restoring
defects in shared production files. One reported "the file kept disappearing mid-run" —
another writer's restore collided with its mutation window. Every worker reported
`git diff` clean at its own end, which is exactly what makes this invisible.

**Rule:** one writer per worktree, or a read-only evaluation copy pinned at the commit.
Mutation legs must be planted **from the test layer** (`monkeypatch`/passthrough), never by
editing a production file that another lane may be writing.

## Trap 2 — the lane tree was also being edited by the developer lane

`git diff --name-only -- daemon/ tests/` showed `db_offload.py`, `instance.py`,
`instance_read.py`, `test_db_offload_seam.py` modified — none of them mine. For a window
the tree did not even **import**: `daemon/tools/instance_read.py` had
`from __future__ import annotations` displaced to line 68 by an uncommitted diff →
SyntaxError → `daemon.tools` → `daemon.mcp.kb_server` → `daemon.routers` →
**collection of any test importing `daemon.routers` fails**. A pack "failing" there would
have been a report about the tree's surgery, not about the code.

**Rule:** before blaming a pack, check whether the tree under test is quiescent — and when
it is not, move to the pinned scratch worktree instead of declaring a blocker.

## Trap 3 — a registered pin can evolve under the registry

The children-order pack went **9 → 8 nodes** across four developer commits after I
registered it: `test_multiset_not_set` removed (its mutant is unconstructible as written —
`instance_id` is the PK, so a duplicate `(parent_id, child_id)` cannot be inserted), the
static SQL pin renamed with an identical body, `test_detail_shares_source` strengthened
then de-duplicated. **No surviving assertion was loosened** — but my first registry row
described a state that no longer existed.

**Rules:** (a) read a pin from `git show <commit>:<path>`, never from a working tree;
(b) when a row is registered, record the commit and blob sha it describes;
(c) a removed-as-vacuous node is a **coverage loss** even when the removal is honest —
name what now covers it (`gate_g5` sees a duplicated child id as a body difference) and
say so in the row; (d) that module still has **no collected-count fence**: pytest's exit 5
catches ZERO collection, but a partial collection passes silently.

## Trap 4 (mine) — over-specifying a pin the plan did not ask for

I required P4.5 (c) to assert `maxlive == 1`. The plan says *"one connection **held across**
the WS2 loop"*. The real resolver legitimately shows `maxlive = 2` (conn-#1 + one probe in
flight); the worker refused my number and documented the divergence instead of asserting a
false pin. The measured event sequence (first checkout == last checkin == one dbapi
connection, one checkin after the last probe, each probe its own checkout) is the correct
invariant.

**Rule:** when a worker refuses an assertion I supplied, check whether the plan actually
says what I wrote. "No substitutes" cuts both ways — my paraphrase is not the requirement.

## Also learned

- `-p no:cacheprovider` keeps the porcelain clean; without it `__pycache__` churn muddies
  the before/after check.
- pytest `/tmp` teardown `PermissionError`s on root-owned `garbage-*` dirs are noise, not
  failures (disclose them, do not "fix" them by running as another user).
- A pack that provisions a PG database must drop the **database**, not just its tables —
  `_cascade_drop_all` alone left `ensemble_p2_8_scratch` behind, and with
  `CREATE DATABASE IF NOT EXISTS` the next run would inherit the previous schema.

## Trap 5 — cross-lane edits must preserve registered fences

Developer cycle-3 commit `09ce9d369` **deleted** `test_collected_node_count_fence` +
`EXPECTED_NODE_NAMES` from the tester-registered, mutation-verified children-order pack —
undeclared, not in the cycle's requirements, and not forced (the pack would have stayed
green). A registered fence is a contract between the lane that owns the pack and the gate
that cites it.

**Rule:** cross-lane edits to tester-registered pack files must preserve registered
fences. A deleting edit reds the gate tripwire and reads as silent weakening — even when
the same commit is otherwise clean and reviewed. Restored by tester at `10b3c982`
(8-name set + count == 8 + no-skip/xfail, fence excluding itself); both mutation legs
re-proven RED at the restore. The pack's own fence is what made the deletion *visible*
rather than silent — which is the argument for fencing every gate-carrying pack.

