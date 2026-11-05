#!/usr/bin/env bash
# Test Pack: memoir_inner_soul_redirect_test
#
# Memoir Store v1 — phase 6 (plan:
# .agents/shared/planning/ari-memoir-store/phase6-plan.md AC1 / AC2 / AC3).
#
# The pack proves the ari-only inner_soul redirect:
#   * AC2 — NON-ARI BYTE-IDENTITY: the responses of the PRE-CHANGE
#     inner_soul module (loaded in-process from the base commit's file,
#     under the daemon.tools package name so its relative imports
#     resolve) and of the CURRENT module are IDENTICAL byte-for-byte
#     for a non-ari fixture agent across the classification corpus
#     (single + compound).
#   * AC3 — the ARI PATH: all FIVE knowledge classifications reach the
#     memoir guidance (and the BASE module leaked every one of them to
#     the RAG redirect — the leak this phase repairs); the knob OFF
#     (explicit false) AND a garbage knob value both produce the loud
#     notice VERBATIM; no path returns the old RAG redirect for ari;
#     the compound arm carries the "(redirected to memoir)" label.
#   * AC1 — the SOURCE-DIFF CENSUS: the inner_soul.py delta is exactly
#     1 helper + 1 import + 2 call-site branch edits (nothing else).
#
# THREE MUTATION LEGS (plant → run → capture the RED ids → restore →
# re-run GREEN):
#   M1  the ari branch disabled            ⇒ the ari legs go RED
#   M2  the knob OFF falls back to RAG     ⇒ the OFF-notice legs go RED
#   M3  the branch condition widened       ⇒ the NON-ARI byte-identity
#                                             goes RED (the blast radius)
#
# EXIT-CODE CONTRACT (the house shape): pack 0 = PASS · 1 = FAIL.
#
# FENCES: per-variable env scrub (the `env -u POSTGRES_*` glob form is a
# SILENT NO-OP — bash does not glob env names), dual-layer timeout, the
# byte-identity gate on the mutated file, and a tree fence (only
# daemon/tools/inner_soul.py may differ).
#
# Interpreter: MEMOIR_PACK_PYTHON wins, then the worktree-local .venv;
# FAIL LOUD on a missing interpreter — never bare python3.
set -uo pipefail

PACK_START=${SECONDS:-0}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}" || exit 1

PY="${MEMOIR_PACK_PYTHON:-}"
if [ -z "$PY" ] && [ -x "${REPO_ROOT}/.venv/bin/python" ]; then
  PY="${REPO_ROOT}/.venv/bin/python"
fi
if [ -z "$PY" ] || [ ! -x "$PY" ]; then
  echo "RESULT: FAIL (MISSING-INTERPRETER — set MEMOIR_PACK_PYTHON to the"
  echo "        venv python; this worktree has no .venv of its own and a"
  echo "        bare-python3 fallback is banned)"
  exit 1
fi
echo "=== Test Pack: memoir_inner_soul_redirect_test ==="
echo "Interpreter: $PY"

#: The base commit — the pre-change inner_soul (the ceremony commit; the
#: phase-6 commit is its child).
BASE_COMMIT="${MEMOIR_INNER_SOUL_BASE:-d9b36ad8a}"

TIMEOUT_S="${MEMOIR_PACK_TIMEOUT_S:-300}"
LOG_DIR="${MEMOIR_P6_LOG_DIR:-/tmp/memoir-p6-gate}"
mkdir -p "${LOG_DIR}"

SCRUB=(env -u POSTGRES_USER -u POSTGRES_DB -u POSTGRES_HOST -u POSTGRES_PORT
       -u POSTGRES_PASSWORD -u DATABASE_URL -u PORT -u DAEMON_PORT \
       -u ENSEMBLE_SELF_ENV)

HARNESS="$(mktemp -t memoir-p6-harness-XXXXXX.py)"
INNER_SOUL="daemon/tools/inner_soul.py"
BACKUP="${LOG_DIR}/inner_soul.packbak"
trap 'rm -f "${HARNESS}"' EXIT

cat >"${HARNESS}" <<'HARNESS_EOF'
"""The inner_soul ari-redirect harness (embedded by the pack)."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(os.environ["FC9_REPO"]).resolve()
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

CORPUS = [
    ("pattern", "I noticed that every time the ranking changes the recall feels thin"),
    ("event", "today we discussed the shape of the morning briefing"),
    ("skill", "I learned to read the queue probe before blaming the pool"),
    ("mistake", "I made a mistake about the pin cap last week"),
    ("project_knowledge", "the postgres migration was applied to the schema"),
]
COMPOUND = [
    (
        "compound-mixed",
        "today we discussed the shape of the morning briefing AND "
        "I noticed that every time the ranking changes the recall feels thin",
    ),
]
OFF_NOTICE = (
    "memory recording is disabled (ENSEMBLE_MEMOIR_ENABLED) — "
    "this content was NOT recorded"
)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class StubManager:
    config = None

    def __getattr__(self, name):
        raise AssertionError(f"the redirect path touched the manager: {name}")


def prepare(module) -> None:
    module.is_rag_enabled = lambda: True
    module._archive_old_memories = lambda agent_path, ttl_days: 0


def tool_for(module, agent_id: str):
    return module.create_inner_soul_tool(
        StubManager(), agent_id=agent_id, instance_id="fc9-instance"
    )


def invoke(tool, text: str) -> str:
    return tool.invoke({"request": text})


def main() -> int:
    base_commit = os.environ["MEMOIR_INNER_SOUL_BASE"]
    out_path = Path(os.environ["FC9_OUT"])
    failures: list[str] = []

    def fail(fid: str, message: str) -> None:
        failures.append(f"{fid}: {message}")

    base_file = Path(os.environ["FC9_BASE_FILE"])
    base_file.parent.mkdir(parents=True, exist_ok=True)
    blob = subprocess.run(
        ["git", "-C", str(REPO), "show", f"{base_commit}:daemon/tools/inner_soul.py"],
        capture_output=True, text=True, check=False,
    )
    if blob.returncode != 0:
        print(f"RED-ID: harness-base-unreadable: {blob.stderr}")
        return 2
    base_file.write_text(blob.stdout, encoding="utf-8")

    base = load_module(base_file, "daemon.tools._inner_soul_base")
    current = load_module(REPO / "daemon" / "tools" / "inner_soul.py",
                           "daemon.tools._inner_soul_current")
    prepare(base)
    prepare(current)

    evidence = {"base_commit": base_commit, "non_ari": [], "ari": [], "compound": []}

    # AC2 — the non-ari byte-identity
    base_dev = tool_for(base, "developer")
    cur_dev = tool_for(current, "developer")
    for label, text in CORPUS + COMPOUND:
        before = invoke(base_dev, text)
        after = invoke(cur_dev, text)
        evidence["non_ari"].append({
            "label": label,
            "identical": before == after,
            "before_sha": hashlib.sha256(before.encode("utf-8")).hexdigest(),
            "after_sha": hashlib.sha256(after.encode("utf-8")).hexdigest(),
        })
        if before != after:
            fail(f"non-ari-byte-identity:{label}",
                 "the non-ari response changed")
        if "Redirected to Knowledge System" not in before:
            fail(f"base-not-rag:{label}", "the base module did not redirect to RAG")

    # AC3 — the ari path
    base_ari = tool_for(base, "ari")
    cur_ari = tool_for(current, "ari")
    for label, text in CORPUS:
        before = invoke(base_ari, text)
        after = invoke(cur_ari, text)
        evidence["ari"].append({
            "label": label,
            "base_is_rag_redirect": "Redirected to Knowledge System" in before,
            "after_is_memoir": "Redirected to Memory" in after,
        })
        if "Redirected to Knowledge System" not in before:
            fail(f"base-not-rag:{label}", "the base module did NOT leak to RAG")
        if "Redirected to Memory" not in after:
            fail(f"ari-not-memoir:{label}", "ari did not reach the memoir guidance")
        if "memoir_write" not in after:
            fail(f"ari-no-tool:{label}", "the guidance does not name memoir_write")
        if "durable" not in after:
            fail(f"ari-no-durability:{label}", "the guidance does not state durability")
        if "Redirected to Knowledge System" in after:
            fail(f"ari-still-rag:{label}", "ari still receives the RAG redirect")

    # AC3 — the compound arm
    for label, text in COMPOUND:
        before = invoke(base_dev, text)
        after = invoke(cur_dev, text)
        if before != after:
            fail(f"non-ari-byte-identity:{label}", "the compound response changed")
        ari_after = invoke(cur_ari, text)
        evidence["compound"].append({
            "label": label,
            "ari_is_memoir": "Redirected to Memory" in ari_after,
            "ari_has_compound_label": "(redirected to memoir)" in ari_after,
            "dev_is_rag": "Redirected to Knowledge System" in after,
        })
        if "(redirected to memoir)" not in ari_after:
            fail("compound-label", "the compound arm lacks '(redirected to memoir)'")
        if "(redirected to RAG)" in ari_after:
            fail("compound-label-rag", "the compound arm still labels ari as RAG")

    # AC3 — the knob OFF + the garbage value
    for state, fid in (("false", "knob-off-notice"), ("banana", "knob-garbage-notice")):
        os.environ["ENSEMBLE_MEMOIR_ENABLED"] = state
        try:
            response = invoke(cur_ari, CORPUS[1][1])
        finally:
            os.environ.pop("ENSEMBLE_MEMOIR_ENABLED", None)
        evidence[f"knob_{state}_response"] = response
        if response != OFF_NOTICE:
            fail(fid, "the notice is not verbatim")
        if "experience(" in response:
            fail(fid + "-fallback", "the OFF path fell back to the RAG redirect")

    # AC1 — the source-diff census
    diff = subprocess.run(
        ["git", "-C", str(REPO), "diff", base_commit, "--", "daemon/tools/inner_soul.py"],
        capture_output=True, text=True, check=False,
    ).stdout
    added = [ln[1:] for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    removed = [ln[1:] for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---")]
    census = {
        "added_imports": [ln for ln in added if ln.startswith("from ") or ln.startswith("import ")],
        "added_helper_defs": [ln for ln in added if ln.startswith("def _format_memoir_redirect(")],
        "added_ari_branches": [ln for ln in added if ln.strip() == 'if agent_id == "ari":'],
        "removed": removed,
    }
    evidence["census"] = census
    if census["added_imports"] != ["from .memoir_knob import memoir_enabled"]:
        fail("census-import", f"the import census is wrong: {census['added_imports']}")
    if len(census["added_helper_defs"]) != 1:
        fail("census-helper", "the helper census is wrong")
    if len(census["added_ari_branches"]) != 2:
        fail("census-branches", f"the call-site census is wrong: {census['added_ari_branches']}")
    expected_removed = [
        "                        rag_response = _format_rag_redirect(part, classification, targets)",
        "                        compound_lines.append(f\"    {rag_response.split(chr(10))[0]} (redirected to RAG)\")",
    ]
    if removed != expected_removed:
        fail("census-removed", f"unexpected removed lines: {removed}")

    # the tree fence — only inner_soul.py may differ
    status = subprocess.run(
        ["git", "-C", str(REPO), "status", "--porcelain"],
        capture_output=True, text=True, check=False,
    ).stdout
    evidence["tree_status"] = status
    changed_files = {ln[3:].strip() for ln in status.splitlines() if ln.strip()}
    # The fence is a PREFIX allow-list (the lane's own file set): any
    # changed file outside it is a stray edit.
    prefixes = [
        "daemon/tools/inner_soul.py",
        "test/packs/memoir_",
        "tests/unit/test_memoir_",
        "daemon/tools/memoir.py",
        "agents/ari/",
        "agents/.prompt-artifacts.sha256",
    ]
    for extra in (os.environ.get("FC9_ALLOW_CHANGED", "") or "").split(","):
        extra = extra.strip()
        if extra:
            prefixes.append(extra)
    stray = sorted(
        path for path in changed_files
        if not any(path.startswith(prefix) for prefix in prefixes)
    )
    if stray:
        fail("tree-fence", f"unexpected changed files: {stray}")

    evidence["failures"] = failures
    out_path.write_text(json.dumps(evidence, indent=2, sort_keys=True), encoding="utf-8")
    for failure in failures:
        print(f"RED-ID: {failure}")
    if failures:
        print(f"P6-HARNESS: FAIL ({len(failures)} failure(s))")
        return 1
    print("P6-HARNESS: OK — non-ari byte-identical on "
          f"{len(evidence['non_ari'])} corpus entries, ari 5/5 on the memoir "
          "path, the knob OFF/garbage notice verbatim, the census = 1 import "
          "+ 1 helper + 2 branch edits")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
HARNESS_EOF

run_harness() {
  # run_harness <log-path>
  local log="$1"
  FC9_REPO="${REPO_ROOT}" \
  FC9_OUT="${log%.log}.json" \
  FC9_BASE_FILE="${LOG_DIR}/inner_soul_base.py" \
  MEMOIR_INNER_SOUL_BASE="${BASE_COMMIT}" \
  FC9_ALLOW_CHANGED="test/packs/memoir_inner_soul_redirect_test.sh" \
  timeout "${TIMEOUT_S}" "${SCRUB[@]}" "${PY}" "${HARNESS}" >"${log}" 2>&1
  return $?
}

cp "${INNER_SOUL}" "${BACKUP}"
restore() { cp "${BACKUP}" "${INNER_SOUL}" 2>/dev/null || true; }
trap 'restore; rm -f "${HARNESS}" "${BACKUP}"' EXIT INT TERM

# ── leg 0: the baseline is GREEN ─────────────────────────────────────────
echo "--- leg 0 baseline ---"
run_harness "${LOG_DIR}/baseline.log"
BASE_RC=$?
cat "${LOG_DIR}/baseline.log" | tail -3
if [ ${BASE_RC} -ne 0 ]; then
  echo "RESULT: FAIL (the baseline harness is not green: rc=${BASE_RC})"
  exit 1
fi

# ── the mutation legs ───────────────────────────────────────────────────
prove_red() {
  # prove_red <expected-red-id> <log-path>
  local expected="$1"; local log="$2"
  if [ ! -s "${log}" ]; then
    echo "RESULT: FAIL (the mutated run produced no output — a vacuous red)"
    exit 1
  fi
  if ! grep -q "RED-ID: ${expected}" "${log}"; then
    echo "RESULT: FAIL (the mutation did NOT produce the expected RED id"
    echo "        '${expected}' — the pin is not mutation-meaningful)"
    grep "RED-ID:" "${log}" || true
    exit 1
  fi
  echo "MUTATION: RED as required; RED id(s):"
  grep "RED-ID:" "${log}" | sed 's/^/    /'
}

# M1 — the ari branch disabled
echo "--- leg M1 mutation: the ari branch disabled ---"
"${PY}" - "${INNER_SOUL}" <<'PLANT'
import sys
path = sys.argv[1]
text = open(path, encoding="utf-8").read()
old = 'if agent_id == "ari":'
if text.count(old) != 2:
    sys.exit(f"MUTATION PLANT FAILED: expected 2 ari branches, found {text.count(old)}")
open(path, "w", encoding="utf-8").write(text.replace(old, "if False:  # MUTATION M1"))
print("mutation planted")
PLANT
if [ $? -ne 0 ]; then echo "RESULT: FAIL (could not plant M1)"; exit 1; fi
run_harness "${LOG_DIR}/M1-ari-branch-disabled.log"
if [ $? -eq 0 ]; then echo "RESULT: FAIL (M1 did not go RED)"; exit 1; fi
prove_red "ari-not-memoir:pattern" "${LOG_DIR}/M1-ari-branch-disabled.log"
restore
run_harness "${LOG_DIR}/M1-restore.log" || { echo "RESULT: FAIL (M1 did not restore GREEN)"; exit 1; }
echo "restore: M1 GREEN again"

# M2 — the knob OFF falls back to the RAG redirect
echo "--- leg M2 mutation: the knob OFF falls back to RAG ---"
"${PY}" - "${INNER_SOUL}" <<'PLANT'
import sys
path = sys.argv[1]
text = open(path, encoding="utf-8").read()
old = (
    '    if not memoir_enabled(getattr(manager, "config", None)):\n'
    "        return (\n"
    '            "memory recording is disabled (ENSEMBLE_MEMOIR_ENABLED) — "\n'
    '            "this content was NOT recorded"\n'
    "        )"
)
new = (
    '    if not memoir_enabled(getattr(manager, "config", None)):\n'
    "        return _format_rag_redirect(request, classification, targets)"
)
if text.count(old) != 1:
    sys.exit(f"MUTATION PLANT FAILED: expected 1 knob guard, found {text.count(old)}")
open(path, "w", encoding="utf-8").write(text.replace(old, new, 1))
print("mutation planted")
PLANT
if [ $? -ne 0 ]; then echo "RESULT: FAIL (could not plant M2)"; exit 1; fi
run_harness "${LOG_DIR}/M2-knob-off-fallback.log"
if [ $? -eq 0 ]; then echo "RESULT: FAIL (M2 did not go RED)"; exit 1; fi
prove_red "knob-off-notice" "${LOG_DIR}/M2-knob-off-fallback.log"
restore
run_harness "${LOG_DIR}/M2-restore.log" || { echo "RESULT: FAIL (M2 did not restore GREEN)"; exit 1; }
echo "restore: M2 GREEN again"

# M3 — the branch condition widened (the blast radius)
echo "--- leg M3 mutation: the branch condition widened ---"
"${PY}" - "${INNER_SOUL}" <<'PLANT'
import sys
path = sys.argv[1]
text = open(path, encoding="utf-8").read()
old = 'if agent_id == "ari":'
if text.count(old) != 2:
    sys.exit(f"MUTATION PLANT FAILED: expected 2 ari branches, found {text.count(old)}")
open(path, "w", encoding="utf-8").write(
    text.replace(old, 'if agent_id != "__nobody__":  # MUTATION M3')
)
print("mutation planted")
PLANT
if [ $? -ne 0 ]; then echo "RESULT: FAIL (could not plant M3)"; exit 1; fi
run_harness "${LOG_DIR}/M3-condition-widened.log"
if [ $? -eq 0 ]; then echo "RESULT: FAIL (M3 did not go RED)"; exit 1; fi
prove_red "non-ari-byte-identity:event" "${LOG_DIR}/M3-condition-widened.log"
restore
run_harness "${LOG_DIR}/M3-restore.log" || { echo "RESULT: FAIL (M3 did not restore GREEN)"; exit 1; }
echo "restore: M3 GREEN again"

# ── the byte-identity gate ───────────────────────────────────────────────
if ! cmp -s "${BACKUP}" "${INNER_SOUL}"; then
  echo "RESULT: FAIL (${INNER_SOUL} was NOT restored byte-identically)"
  exit 1
fi

ELAPSED=$((SECONDS - PACK_START))
echo "RESULT: PASS (baseline + 3 mutation legs proven; ${ELAPSED}s)"
exit 0
