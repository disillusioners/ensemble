#!/usr/bin/env bash
# Test Pack: project_resolution_triad_runtime_proof
#
# RUNTIME pack — NOT a pytest replay. It drives the 7 project-reference tools
# DIRECTLY against a REAL SQLModelProjectRepository over in-memory SQLite and
# asserts the OBSERVED behaviour, so the gate does not rest solely on the
# developer's own test assertions.
#
# The Emperor's case, in one place:
#   A. shortname "ensemble" resolves to the project "agents-ensemble" on EACH
#      of the 7 tools (project_future_add/list/remove,
#      project_history_add/list/search/delete)
#   B. the id path is UNCHANGED — the resolution ladder is NEVER called
#   C. an ambiguous reference -> typed AMBIGUOUS_PROJECT listing candidates
#   D. an unknown reference     -> typed NOT_FOUND with the query echoed
#   E. no reference at all      -> the must-provide refusal
#
# NO PostgreSQL, NO live daemon, NO network, NO PG-dialect ladder claim.
# In-memory SQLite only.
#
# Dual-layer timeout:
#   Layer 2 (script-internal): 110s — interrupts a hung driver
#   Layer 1 (command-level):   120s via the `timeout` wrapper below
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: project_resolution_triad_runtime_proof ==="
cd "$PROJECT_DIR" || exit 1

# Dev fence — scrub POSTGRES_* PER VARIABLE. `env -u POSTGRES_*` is a silent
# NO-OP (bash does not glob env names), so it must never be used as a fence.
unset POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_HOST POSTGRES_PORT
unset DATABASE_URL
export ENSEMBLE_SELF_ENV=dev
export PYTHONPATH="$PROJECT_DIR${PYTHONPATH:+:$PYTHONPATH}"

timeout 110s .venv/bin/python - <<'PYEOF'
import sys
from unittest import mock

from sqlmodel import SQLModel, create_engine

from daemon.repositories import SQLModelProjectRepository as ProjectStore
from daemon.tools.project_future import create_project_future_tools
from daemon.tools.project_history import create_project_history_tools

FAILS = []


def check(label, cond, detail=""):
    if not cond:
        FAILS.append(f"{label} :: {detail}")
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))


engine = create_engine("sqlite:///:memory:", echo=False)
SQLModel.metadata.create_all(engine)
store = ProjectStore(engine)

agents_ensemble = store.create(name="agents-ensemble", shortnames=["ensemble", "ens"])
store.create(name="Other Project", shortnames=["other"])
store.create(name="Ambiguous One", shortnames=["amb"])
store.create(name="Ambiguous Two", shortnames=["amb"])

future = {t.name: t for t in create_project_future_tools(store, "inst-1", "coder")}
history = {t.name: t for t in create_project_history_tools(store, "inst-1", "coder")}
TOOLS = {**future, **history}

print("A. shortname 'ensemble' -> agents-ensemble on EACH of the 7 tools")
added = future["project_future_add"].invoke(
    {"shortname": "ensemble", "summary": "pack future idea"})
check("project_future_add    shortname->agents-ensemble",
      added.get("project_id") == agents_ensemble.project_id,
      f"project_id={added.get('project_id')!r}")

lst = future["project_future_list"].invoke({"shortname": "ensemble"})
check("project_future_list   shortname->agents-ensemble",
      lst.get("total") == 1
      and lst["entries"][0]["project_id"] == agents_ensemble.project_id,
      f"total={lst.get('total')}")

rm = future["project_future_remove"].invoke(
    {"shortname": "ensemble", "entry_id": added["id"]})
check("project_future_remove shortname->agents-ensemble",
      rm.get("success") is True
      and rm.get("removed", {}).get("project_id") == agents_ensemble.project_id,
      f"removed.project_id={rm.get('removed', {}).get('project_id')!r}")

h_added = history["project_history_add"].invoke(
    {"shortname": "ensemble", "entry_type": "milestone", "summary": "pack history entry"})
check("project_history_add   shortname->agents-ensemble",
      h_added.get("project_id") == agents_ensemble.project_id,
      f"project_id={h_added.get('project_id')!r}")

h_lst = history["project_history_list"].invoke({"shortname": "ensemble"})
check("project_history_list  shortname->agents-ensemble",
      h_lst.get("total") == 1
      and h_lst["entries"][0]["project_id"] == agents_ensemble.project_id,
      f"total={h_lst.get('total')}")

h_search = history["project_history_search"].invoke({"shortname": "ensemble", "query": "pack"})
check("project_history_search shortname->agents-ensemble",
      h_search.get("total") == 1
      and h_search["entries"][0]["project_id"] == agents_ensemble.project_id,
      f"total={h_search.get('total')}")

h_del = history["project_history_delete"].invoke(
    {"shortname": "ensemble", "entry_id": h_added["id"]})
check("project_history_delete shortname->agents-ensemble",
      h_del.get("success") is True, f"success={h_del.get('success')!r}")

print("B. the id path NEVER calls the resolver")
for tname, kwargs in [
    ("project_future_add", {"summary": "x"}),
    ("project_future_list", {}),
    ("project_future_remove", {"entry_id": "nope"}),
    ("project_history_add", {"entry_type": "note", "summary": "x"}),
    ("project_history_list", {}),
    ("project_history_search", {"query": "x"}),
    ("project_history_delete", {"entry_id": "nope"}),
]:
    with mock.patch.object(store, "resolve_project_reference",
                           wraps=store.resolve_project_reference) as spy:
        TOOLS[tname].invoke({"project_id": agents_ensemble.project_id, **kwargs})
    check(f"{tname:24s} id-path resolver-call count == 0", spy.call_count == 0,
          f"resolver_calls={spy.call_count}")

print("C. ambiguous -> AMBIGUOUS_PROJECT with candidates")
for tname, kwargs in [
    ("project_future_add", {"summary": "x"}),
    ("project_future_list", {}),
    ("project_future_remove", {"entry_id": "nope"}),
    ("project_history_add", {"entry_type": "note", "summary": "x"}),
    ("project_history_list", {}),
    ("project_history_search", {"query": "x"}),
    ("project_history_delete", {"entry_id": "nope"}),
]:
    r = TOOLS[tname].invoke({"name": "Ambiguous", **kwargs})
    check(f"{tname:24s} AMBIGUOUS_PROJECT + 2 candidates + query",
          r.get("error_code") == "AMBIGUOUS_PROJECT"
          and r.get("candidate_count") == 2
          and len(r.get("candidates", [])) == 2
          and r.get("query") == "Ambiguous"
          and "project_id" not in r,
          f"error_code={r.get('error_code')!r} "
          f"candidate_count={r.get('candidate_count')}")

print("D. unknown -> typed NOT_FOUND with the query echoed")
for tname, kwargs in [
    ("project_future_add", {"summary": "x"}),
    ("project_future_list", {}),
    ("project_future_remove", {"entry_id": "nope"}),
    ("project_history_add", {"entry_type": "note", "summary": "x"}),
    ("project_history_list", {}),
    ("project_history_search", {"query": "x"}),
    ("project_history_delete", {"entry_id": "nope"}),
]:
    r = TOOLS[tname].invoke({"name": "no-such-project-xyz", **kwargs})
    check(f"{tname:24s} NOT_FOUND typed refusal",
          r.get("error_code") == "NOT_FOUND"
          and r.get("query") == "no-such-project-xyz"
          and r.get("candidates") == [],
          f"error_code={r.get('error_code')!r} query={r.get('query')!r}")

print("E. no reference -> must-provide refusal")
for tname, kwargs in [
    ("project_future_add", {"summary": "x"}),
    ("project_future_list", {}),
    ("project_future_remove", {"entry_id": "nope"}),
    ("project_history_add", {"entry_type": "note", "summary": "x"}),
    ("project_history_list", {}),
    ("project_history_search", {"query": "x"}),
    ("project_history_delete", {"entry_id": "nope"}),
]:
    r = TOOLS[tname].invoke(dict(kwargs))
    check(f"{tname:24s} must-provide refusal",
          r.get("error") == "Must provide either project_id, name, or shortname",
          f"error={r.get('error')!r}")

if FAILS:
    print(f"DRIVER RESULT: FAIL — {len(FAILS)} failing check(s)")
    for f in FAILS:
        print("   -", f)
    sys.exit(1)
print("DRIVER RESULT: PASS — all 7 tools resolve 'ensemble' -> agents-ensemble; "
      "id-path resolver-free; AMBIGUOUS/NOT_FOUND/must-provide typed on all 7.")
sys.exit(0)
PYEOF
EXIT_CODE=$?
if [ $EXIT_CODE -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
elif [ $EXIT_CODE -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
else
  echo "RESULT: FAIL"
  exit 1
fi
