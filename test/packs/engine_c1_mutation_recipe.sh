#!/usr/bin/env bash
# Mutation recipe for C-1 (extend-policy late failure). Provenance evidence.
# Reverts the C-1 fix, runs the pin (must be RED), restores, re-runs (must be GREEN).
#
# ⚠️  CEREMONY-RUN DISCIPLINE (F8): this recipe MUTATES the working tree
#     IN PLACE. Ceremony runs MUST NOT mutate the live worktree. Run
#     against a git-archive clone in /tmp:
#       git archive --format=tar HEAD | tar -x -C /tmp/c1-clone
#       cd /tmp/c1-clone && bash /path/to/engine_c1_mutation_recipe.sh
#     In-place invocation is for local dev only. NEVER run against a
#     worktree carrying uncommitted P4/P6 work.
set -u
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_DIR" || exit 1
PYTEST="$PROJECT_DIR/.venv/bin/pytest"
PIN="tests/unit/services/test_engine_single_flight.py::test_extend_then_raise_records_failed_backoff"
cp daemon/services/engine/execution.py /tmp/exec.before
# Revert C-1: remove the propagation guard.
.venv/bin/python - <<'PY'
p="daemon/services/engine/execution.py"
s=open(p).read()
old='''                # C-1 fix: an extend-policy late failure must propagate so the
                # retry/terminal machinery records FAILED_BACKOFF + error_class.
                if outcome == OUTCOME_TIMEOUT_PENDING_EXTEND and exc is not None:
                    raise exc
'''
new=''
assert old in s, "mutation target not found"
s=s.replace(old,new)
open(p,"w").write(s)
print("mutated")
PY
# run pin (expect failure)
env -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_DB \
  -u POSTGRES_URL -u DATABASE_URL -u ENSEMBLE_DB_DSN -u SSL_CERT_FILE -u SSL_CERT_DIR \
  -u OPENAI_API_KEY ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 ENSEMBLE_SELF_ENV=dev \
  POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5432 POSTGRES_DB=ensemble_dev \
  "$PYTEST" -p no:randomly -p no:xdist -q --color=no --override-ini="addopts=" \
  "$PIN" >/tmp/c1_mutated.out 2>&1
MUT_EXIT=$?
# restore
cp /tmp/exec.before daemon/services/engine/execution.py
# re-run (expect pass)
env -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_DB \
  -u POSTGRES_URL -u DATABASE_URL -u ENSEMBLE_DB_DSN -u SSL_CERT_FILE -u SSL_CERT_DIR \
  -u OPENAI_API_KEY ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 ENSEMBLE_SELF_ENV=dev \
  POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5432 POSTGRES_DB=ensemble_dev \
  "$PYTEST" -p no:randomly -p no:xdist -q --color=no --override-ini="addopts=" \
  "$PIN" >/tmp/c1_restored.out 2>&1
REST_EXIT=$?
echo "mutated pin exit (expect non-zero): $MUT_EXIT"
echo "restored pin exit (expect 0): $REST_EXIT"
if [ "$MUT_EXIT" -ne 0 ] && [ "$REST_EXIT" -eq 0 ]; then
  echo "MUTATION RECIPE: PASS"
  exit 0
else
  echo "MUTATION RECIPE: FAIL"
  exit 1
fi
