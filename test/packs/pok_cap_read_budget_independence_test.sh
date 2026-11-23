#!/usr/bin/env bash
# Test Pack: pok_cap_read_budget_independence
#
# WHAT THIS PACK PROVES (commission wi-1921ba17 — the explicit demand):
#   The POK READ-SIDE injection budget (8 entries / 2,900 chars per view)
#   is a FIXED HOUSE BUDGET and is INDEPENDENT of the per-project
#   STORAGE cap (``POK_ACTIVE_CAP_PER_PROJECT``, raised 512 -> 1024 by
#   this commission).  Raising or lowering the storage cap must never
#   silently change what one view renders.
#
# LEGS (each is independently FAILING on a defect — no vacuous leg):
#   L1 CONSTANTS      — the three shipped constants are pinned by value:
#                       POK_ACTIVE_CAP_PER_PROJECT == 1024,
#                       POK_BLOCK_MAX_ENTRIES == 8, POK_BLOCK_MAX_CHARS == 2_900.
#   L2 STRUCTURAL     — the READ path (``daemon/services/pok_injection.py``)
#                       CANNOT see the storage cap: it must contain NO
#                       reference to POK_ACTIVE_CAP_PER_PROJECT while it
#                       MUST reference both budget constants (the
#                       non-vacuity control for the grep itself).
#   L3 BEHAVIOURAL    — independent re-derivation over a SYNTHETIC
#                       in-memory SQLite store (40 ari-visible active
#                       rows at the 240-char body ceiling): with the
#                       storage cap monkeypatched to 64 AND to 4096 the
#                       view admits EXACTLY 8 entries, renders <= 2,900
#                       chars, and the two rendered blocks are
#                       BYTE-IDENTICAL (stronger than "<=" alone).
#   L4 ANTI-VACUITY   — the L3 measurement harness is itself proved LIVE:
#                       with the read budget deliberately widened
#                       in-process the harness MUST report a violation.
#   L5 RETIRED GUARDS — the shipped admission prose names the LIVE cap
#                       ("at 1024 active") and bakes NONE of the
#                       retired values (64 / 256 / 512); no "<active_cap>"
#                       placeholder survives; no retired literal is baked
#                       into the shipped cap constant site.
#   L6 COMMITTED PINS — the repository's own committed pins for this
#                       contract (tests/unit/services/test_pok_injection_unit.py
#                       ::TestCapIndependenceReadBudget) are green.
#
# FENCES (no live contact of any kind):
#   * ZERO live-store reads — every row is SYNTHETIC (in-memory SQLite).
#   * ZERO PostgreSQL — per-variable POSTGRES_*/DSN scrub below.
#   * ZERO daemon boot / no live install contact.
#   * Dual-layer timeout: command-level `timeout 280` inside this
#     wrapper + the caller's outer `timeout` wrapper.
#
# USAGE:  cd <worktree> && timeout 300 bash test/packs/pok_cap_read_budget_independence_test.sh
#         (exit 0 = PASS, non-zero = FAIL; per-leg PASS/FAIL printed)

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_DIR"

echo "=== Test Pack: pok_cap_read_budget_independence [$(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)] ==="

# ── Fences ────────────────────────────────────────────────────────────────
# Per-variable scrub (NOT the `env -u POSTGRES_*` glob no-op).
unset POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_HOST POSTGRES_PORT
unset DATABASE_URL ENSEMBLE_DB_DSN
export ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0
export ENSEMBLE_SELF_ENV=dev
export PYTHONDONTWRITEBYTECODE=1

PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "RESULT: FAIL (no .venv/bin/python in $PROJECT_DIR)"
  exit 1
fi

FAILED=0
LEG_FAIL=0

leg() {  # leg <id> <name> <rc>
  if [ "$3" -eq 0 ]; then
    echo "  [PASS] $1 $2"
  else
    echo "  [FAIL] $1 $2 (rc=$3)"
    LEG_FAIL=1
    FAILED=1
  fi
}

echo
echo "--- L1/L2/L3/L4/L5 — independent driver (synthetic store, no live contact) ---"
DRIVER_RC=0
timeout 280s "$PY" - <<'PY' || DRIVER_RC=$?
import ast
import hashlib
import pathlib
import sys
from datetime import datetime, timedelta, timezone

rc = 0

def check(label, ok, detail=""):
    global rc
    print(f"    [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        rc = 1
    return ok

# ── L1 — the shipped constants ───────────────────────────────────────────
from daemon.services import pok_service as ps
check("L1 POK_ACTIVE_CAP_PER_PROJECT == 1024",
      ps.POK_ACTIVE_CAP_PER_PROJECT == 1024, f"got {ps.POK_ACTIVE_CAP_PER_PROJECT}")
check("L1 POK_BLOCK_MAX_ENTRIES == 8",
      ps.POK_BLOCK_MAX_ENTRIES == 8, f"got {ps.POK_BLOCK_MAX_ENTRIES}")
check("L1 POK_BLOCK_MAX_CHARS == 2_900",
      ps.POK_BLOCK_MAX_CHARS == 2_900, f"got {ps.POK_BLOCK_MAX_CHARS}")

# ── L2 — the read path CANNOT see the storage cap ───────────────────────
inj_src = pathlib.Path("daemon/services/pok_injection.py").read_text()
check("L2 read path does NOT reference POK_ACTIVE_CAP_PER_PROJECT",
      "POK_ACTIVE_CAP_PER_PROJECT" not in inj_src,
      "the injection budget is decoupled from the storage cap")
check("L2 read path DOES reference POK_BLOCK_MAX_ENTRIES (grep non-vacuity)",
      "POK_BLOCK_MAX_ENTRIES" in inj_src)
check("L2 read path DOES reference POK_BLOCK_MAX_CHARS (grep non-vacuity)",
      "POK_BLOCK_MAX_CHARS" in inj_src)
svc_src = pathlib.Path("daemon/services/pok_service.py").read_text()
check("L2 the storage cap is defined exactly once (single source)",
      svc_src.count("POK_ACTIVE_CAP_PER_PROJECT = ") == 1)

# ── L3 — behavioural measurement over a SYNTHETIC store ──────────────────
from sqlalchemy.engine import create_engine
from sqlmodel import Session, SQLModel

import daemon.repositories.pok.models  # noqa: F401
from daemon.repositories.pok.models import (
    ProjectOperatingKnowledge,
    ProjectOperatingKnowledgeEmbedding,
)
from daemon.services.pok_injection import (
    POK_BLOCK_TITLE,
    admit_view,
    render_pok_block,
)
from daemon.services.pok_service import POKService

N_ROWS = 40
NOW = datetime.now(timezone.utc)
CREATED = (NOW - timedelta(days=1)).isoformat()


def make_row(slug):
    return ProjectOperatingKnowledge(
        id=f"id-{slug}",
        project_id="p1",
        slug=slug,
        domain="deploy",
        audience="operation",   # ari ∈ operation set -> visible
        audience_agents=None,
        body=f"{slug[:4]}" + "x" * 236,   # 240 chars, distinct per row
        status="active",
        disputed=False,
        content_sha256="h",
        created_by_agent="x",
        created_at=CREATED,
        updated_at=CREATED,
        last_verified=None,
        pinned=False,
    )


def seeded_service():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(
        engine,
        tables=[
            ProjectOperatingKnowledge.__table__,
            ProjectOperatingKnowledgeEmbedding.__table__,
        ],
    )
    with Session(engine) as session:
        for i in range(N_ROWS):
            session.add(make_row(f"capind-{i:03d}-fact"))
        session.commit()
    return POKService(engine)


def measure(storage_cap):
    original = ps.POK_ACTIVE_CAP_PER_PROJECT
    ps.POK_ACTIVE_CAP_PER_PROJECT = storage_cap
    try:
        svc = seeded_service()
        rows = svc._repo.list_entries("p1", statuses=("active",))
        if len(rows) != N_ROWS:
            return None, None, None, f"store seeded {len(rows)} rows (expected {N_ROWS})"
        result = admit_view(rows, agent_id="ari", now=NOW)
        block = render_pok_block(result)
        full = f"[SYSTEM CONTEXT: {POK_BLOCK_TITLE}]\n\n{block}"
        return len(result.entries), len(full), full, None
    finally:
        ps.POK_ACTIVE_CAP_PER_PROJECT = original


measured = {}
for cap in (64, 4096):
    entries, chars, full, err = measure(cap)
    if err:
        check(f"L3 cap={cap}", False, err)
        continue
    measured[cap] = (entries, chars, full)
    digest = hashlib.sha256(full.encode()).hexdigest()[:16]
    print(f"    storage_cap={cap}: {entries} entries / {chars} chars "
          f"(budget {ps.POK_BLOCK_MAX_ENTRIES} / {ps.POK_BLOCK_MAX_CHARS:,} UNCHANGED) sha={digest}")
    check(f"L3 cap={cap} admits EXACTLY {ps.POK_BLOCK_MAX_ENTRIES} entries",
          entries == ps.POK_BLOCK_MAX_ENTRIES, f"got {entries}")
    check(f"L3 cap={cap} renders <= {ps.POK_BLOCK_MAX_CHARS} chars",
          chars <= ps.POK_BLOCK_MAX_CHARS, f"got {chars}")

if len(measured) == 2:
    check("L3 the rendered block is BYTE-IDENTICAL across cap regimes",
          measured[64][2] == measured[4096][2],
          "the storage cap does not influence the view at all")

# ── L4 — anti-vacuity: the measurement harness is itself LIVE ───────────
# NOTE (binding discipline): `daemon/services/pok_injection.py` binds the
# budget constants BY VALUE at import time (`from ... import
# POK_BLOCK_MAX_ENTRIES`), so the anti-vacuity probe must patch the
# INJECTION module's binding — patching `pok_service`'s attribute would
# be a silent no-op and the probe would prove nothing.
import daemon.services.pok_injection as inj

orig_entries = inj.POK_BLOCK_MAX_ENTRIES
inj.POK_BLOCK_MAX_ENTRIES = 16         # widen the read budget on purpose
try:
    e_wide, c_wide, _full, _err = measure(64)
finally:
    inj.POK_BLOCK_MAX_ENTRIES = orig_entries
violation_detected = (e_wide is not None and e_wide != 8)
check("L4 harness detects a deliberately widened read budget",
      violation_detected,
      f"widened run admitted {e_wide} entries (must differ from 8) -> the check is live")
e_after, c_after, _f, _err = measure(64)
check("L4 budget restored after the anti-vacuity probe",
      e_after == 8 and c_after == measured[64][1],
      f"{e_after} entries / {c_after} chars")

# ── L5 — the shipped admission prose: live cap, no retired literal ───────
tree = ast.parse(pathlib.Path("daemon/tools/pok.py").read_text())
doc = None
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == "pok_upsert":
        doc = ast.get_docstring(node)
if not check("L5 pok_upsert docstring located", doc is not None):
    pass
else:
    desc = doc.replace("<active_cap>", str(ps.POK_ACTIVE_CAP_PER_PROJECT))
    check("L5 prose names the LIVE cap",
          f"at {ps.POK_ACTIVE_CAP_PER_PROJECT} active" in desc,
          f"'at {ps.POK_ACTIVE_CAP_PER_PROJECT} active'")
    for retired in ("64 active entries", "256 active entries", "512 active entries"):
        check(f"L5 retired value absent from prose: {retired!r}",
              retired not in desc)
    check("L5 no '<active_cap>' placeholder survives",
          "<active_cap>" not in desc)

for retired_lit in ("= 512", "= 256", "= 64"):
    check(f"L5 no retired literal {retired_lit!r} baked into the shipped cap site",
          f"POK_ACTIVE_CAP_PER_PROJECT {retired_lit}" not in svc_src)

sys.exit(rc)
PY
leg "L1-L5" "independent driver (constants / structural / behavioural / anti-vacuity / retired guards)" "$DRIVER_RC"

echo
echo "--- L6 — the repository's own committed pins for this contract ---"
PIN_RC=0
timeout 280s "$PY" -m pytest \
  tests/unit/services/test_pok_injection_unit.py \
  -k TestCapIndependenceReadBudget \
  -p no:randomly --tb=short -q -rfE \
  > /tmp/pok_cap_read_budget_pins.log 2>&1 || PIN_RC=$?
tail -3 /tmp/pok_cap_read_budget_pins.log
if [ "$PIN_RC" -eq 0 ]; then
  PIN_COUNT="$(grep -oE '[0-9]+ passed' /tmp/pok_cap_read_budget_pins.log | tail -1)"
  echo "  committed pins: ${PIN_COUNT:-3 passed}"
fi
leg "L6" "committed pins (TestCapIndependenceReadBudget)" "$PIN_RC"

echo
if [ "$FAILED" -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
fi
echo "RESULT: FAIL"
exit 1
