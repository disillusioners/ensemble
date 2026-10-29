"""FM3 PART 4 — INDEPENDENT driver: F4-A self-inflicted-outage exclusion.

Constructs the scenario OUTSIDE the pack, from first principles:

* a REAL SQLAlchemy engine with a CUSTOM tier (pool_size=22 +
  max_overflow=10 => derived capacity 32 — differs from BOTH the old
  static-15 threshold and the current declared default 25);
* REAL connections checked out to the critical occupancy points;
* the REAL probe path under test (``make_db_probe`` /
  ``_refuse_pool_admission_at_capacity`` from the merged tree).

Three observations:
  (a) NO refusal below the DERIVED capacity — including at
      checkedout=25, the exact occupancy where a STATIC threshold of
      25 (or 15) would have FALSE-REFUSED = the self-inflicted outage.
  (b) Refusal AT proven exhaustion (checkedout == derived capacity):
      TimeoutError raised SYNCHRONOUSLY, before engine.connect(), and
      the pool's checked-out count is unchanged (no slot taken).
  (c) NO refusal when the capacity is unresolvable — a REAL
      sqlalchemy.pool.NullPool exposes neither ``size()`` nor
      ``_max_overflow`` (verifies the shape, not just the behavior).

Run (certified idiom — cd the worktree, main-venv interpreter, fenced env):
  cd /home/nea/ensemble-src-wt-tdbp-fmf4a && \\
  env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \\
      -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \\
      HOME="$(mktemp -d)" ENSEMBLE_SELF_ENV=dev \\
      /home/nea/ensemble-src/.venv/bin/python /tmp/tdbp-fm3/fm4a_driver.py
"""

from __future__ import annotations

import os
import sys

# The certified worktree idiom: the driver runs with cwd = the scratch
# worktree, so `import daemon` resolves from the TREE UNDER TEST.
sys.path.insert(0, os.getcwd())

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.pool import NullPool, QueuePool  # noqa: E402

from daemon.services.readiness import (  # noqa: E402
    MANAGER_POOL_CONFIGURED_CAPACITY,
    _manager_pool_effective_capacity,
    _refuse_pool_admission_at_capacity,
    make_db_probe,
)

POOL_SIZE, OVERFLOW = 22, 10  # custom tier: derived capacity 32


def hold(engine, n: int) -> list:
    """Check out ``n`` REAL connections (each pinned with a statement)."""
    holders = []
    from sqlalchemy import text

    for _ in range(n):
        conn = engine.connect()
        conn.execute(text("select 1"))
        holders.append(conn)
    return holders


def main() -> int:
    failures: list[str] = []

    def check(label: str, cond: bool, detail: str = "") -> None:
        status = "PASS" if cond else "FAIL"
        print(f"[{status}] {label}{(' — ' + detail) if detail else ''}")
        if not cond:
            failures.append(label)

    print(f"declared default tier (MANAGER_POOL_CONFIGURED_CAPACITY) = "
          f"{MANAGER_POOL_CONFIGURED_CAPACITY}")
    print(f"custom tier under test: pool_size={POOL_SIZE} "
          f"max_overflow={OVERFLOW}")

    # ── build the custom-tier REAL engine and derive its capacity ──
    engine = create_engine(
        "sqlite://",
        poolclass=QueuePool,
        pool_size=POOL_SIZE,
        max_overflow=OVERFLOW,
    )
    derived = _manager_pool_effective_capacity(engine.pool)
    check("derived capacity of the custom tier == 32 (≠ 25, ≠ 15)",
          derived == POOL_SIZE + OVERFLOW == 32, f"derived={derived}")

    probe = make_db_probe(engine)

    # ── OBSERVATION (a): no refusal BELOW the derived capacity ──────
    # The sharpest point: checkedout == 25 — the occupancy at which the
    # OLD static-25 threshold (and the pre-hotfix static-15) would have
    # FALSE-REFUSED while 7 derived slots were still servable.
    holders = hold(engine, 25)
    check("occupancy staged at 25 checked-out (derived 32)",
          engine.pool.checkedout() == 25,
          f"checkedout={engine.pool.checkedout()}")
    check("COUNTERFACTUAL: a static threshold of 25 WOULD refuse here "
          "(25 >= 25) — the self-inflicted outage the fix excludes",
          25 >= MANAGER_POOL_CONFIGURED_CAPACITY and 25 >= 15)
    try:
        verdict = probe()
        refused = False
    except TimeoutError:
        verdict, refused = None, True
    check("(a) at 25/32 checked-out the probe does NOT refuse "
          "(old static-15/25 would have false-refused)",
          not refused and verdict == (True, None),
          f"refused={refused} verdict={verdict}")

    # just-below the derived capacity: 31/32
    holders += hold(engine, derived - 1 - 25)
    check("occupancy staged just-below derived (31/32)",
          engine.pool.checkedout() == derived - 1,
          f"checkedout={engine.pool.checkedout()}")
    try:
        verdict = probe()
        refused = False
    except TimeoutError:
        verdict, refused = None, True
    check("(a) just-below derived capacity (31/32) the probe does NOT "
          "refuse", not refused and verdict == (True, None),
          f"refused={refused} verdict={verdict}")

    # ── OBSERVATION (b): refusal AT proven exhaustion (32/32) ───────
    holders += hold(engine, 1)
    check("occupancy staged AT derived capacity (32/32)",
          engine.pool.checkedout() == derived,
          f"checkedout={engine.pool.checkedout()}")
    msg = ""
    try:
        probe()
        refused = False
    except TimeoutError as exc:
        refused, msg = True, str(exc)
    check("(b) at proven exhaustion (32/32) the probe refuses with "
          "TimeoutError", refused, f"message={msg!r}")
    check("(b) the refusal quotes the DERIVED capacity (32/32), not a "
          "static number", "32/32" in msg, f"message={msg!r}")
    check("(b) the refusal took NO slot (checked-out count unchanged)",
          engine.pool.checkedout() == 32,
          f"checkedout={engine.pool.checkedout()}")

    for conn in holders:  # release
        conn.close()

    # ── OBSERVATION (c): unresolvable capacity ⇒ NO refusal ─────────
    # Two REAL distinct shapes, both must not refuse:
    #   (c1) a REAL sqlalchemy NullPool — in this SQLAlchemy it exposes
    #        neither size() nor _max_overflow NOR checkedout() ⇒ the
    #        FIRST shape guard applies (no occupancy observable at all);
    #   (c2) the briefed "capacity unresolvable" shape — a pool that
    #        DOES expose checkedout() but neither size() nor
    #        _max_overflow ⇒ occupancy observable, capacity NOT.
    null_engine = create_engine("sqlite://", poolclass=NullPool)
    shape_no_size = not hasattr(null_engine.pool, "size")
    shape_no_overflow = not hasattr(null_engine.pool, "_max_overflow")
    shape_no_checkedout = not hasattr(null_engine.pool, "checkedout")
    check("(c1) shape: a REAL NullPool exposes none of "
          "size()/_max_overflow/checkedout()",
          shape_no_size and shape_no_overflow and shape_no_checkedout)
    conn = null_engine.connect()
    try:
        _refuse_pool_admission_at_capacity(null_engine)
        refused = False
    except TimeoutError:
        refused = True
    check("(c1) a REAL NullPool engine ⇒ NO refusal (first shape guard)",
          not refused)
    conn.close()

    class _UnresolvablePool:
        """Occupancy observable, capacity NOT: the briefed shape."""

        def checkedout(self) -> int:
            return 999

    class _Engine:
        def __init__(self, pool) -> None:
            self.pool = pool

    unresolvable = _UnresolvablePool()
    shape2_no_size = not hasattr(unresolvable, "size")
    shape2_no_overflow = not hasattr(unresolvable, "_max_overflow")
    check("(c2) shape: the unresolvable pool exposes checkedout() but "
          "neither size() nor _max_overflow",
          shape2_no_size and shape2_no_overflow
          and hasattr(unresolvable, "checkedout"))
    check("(c2) helper returns None for the unresolvable pool",
          _manager_pool_effective_capacity(unresolvable) is None)
    try:
        _refuse_pool_admission_at_capacity(_Engine(unresolvable))
        refused = False
    except TimeoutError:
        refused = True
    check("(c2) unresolvable capacity (999 checked-out, no derivable "
          "ceiling) ⇒ NO refusal (fail-open on refusal, never refuse "
          "on a guess)", not refused)

    print()
    if failures:
        print(f"DRIVER RESULT: {len(failures)} FAILURE(S): {failures}")
        return 1
    print("DRIVER RESULT: ALL OBSERVATIONS PASS — "
          "(a) no false refusal below derived capacity (the "
          "self-inflicted outage is excluded), (b) refusal at proven "
          "exhaustion with no slot taken, (c1)+(c2) unresolvable "
          "capacity never refuses (both shape guards).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
