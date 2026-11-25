#!/usr/bin/env bash
# Test Pack: sonic_selectable_models_guard
#
# WHAT THIS PACK PROVES (commission selectable-models-coding-only, v0.31.44):
#   The Emperor ruling "coding only — coding2 is moved inside the proxy
#   group feature" holds on the SHIPPED selectable surface: the token
#   ``coding2`` is absent from every shipped home of the default, and the
#   resolved set at the real daemon config path is exactly
#   ``['agentic', 'coding', 'vision']`` (title=title, keywords=quick).
#
#   ``coding2`` is removed from the SHIPPED DEFAULT ONLY. It remains legal
#   as an EXPLICIT operator value (via ``OPENAI_SELECTABLE_MODELS`` or the
#   legacy ``OPENAI_ALLOWED_MODELS`` alias) — that leg is the guard file's
#   own leg (f) and is exercised through the committed pins below.
#
# LEGS (each is independently FAILING on a defect — no vacuous leg):
#   L1 SURFACE ZERO   — the retired token is ABSENT from every shipped
#                       home: ``daemon/config.py``, ``config.yaml``,
#                       ``frontend/src``, ``agents/**``, ``scripts/**``.
#                       ANTI-VACUITY: the scan must actually have read
#                       files (a scan that matched nothing because it
#                       scanned nothing is a FAIL, not a PASS).
#   L2 RESOLVED SET   — the REAL ``load_config()`` (repo-root
#                       ``config.yaml``, explicit ``config_path``) with an
#                       EXPLICIT ``OPENAI_SELECTABLE_MODELS`` resolves
#                       VERBATIM to ``['agentic','coding','vision']`` —
#                       the resolver does NOT join the remaining default
#                       members — and ``model_title == 'title'``,
#                       ``model_keywords == 'quick'``.
#   L3 COMMITTED PINS — the commission's own committed pins for this
#                       contract are green:
#                       ``tests/unit/test_selectable_models_surface_guard.py``
#                       (7 nodes) and the UNTOUCHED drift detector
#                       ``tests/unit/test_llm_allowed_models_precedence.py::
#                       TestDefaultConsistencyPin`` (1 node, byte-equality
#                       between the two homes of the default).
#
# FENCES (no live contact of any kind):
#   * ZERO daemon boot / no live-install contact / no live DB reads.
#   * DEV-only DB fence: POSTGRES_* pinned EXPLICITLY to the dev values
#     (never the ambient prod DSN; NEVER the ``env -u POSTGRES_*`` glob
#     form — bash does not glob env names, so that idiom is a silent
#     NO-OP). A hard assert refuses to run if POSTGRES_DB is a prod shape.
#   * No network.
#   * Dual-layer timeout: command-level `timeout 280` inside this wrapper
#     + the caller's outer `timeout` wrapper.
#
# USAGE:  cd <worktree> && timeout 300 bash test/packs/sonic_selectable_models_guard.sh
#         (exit 0 = PASS, non-zero = FAIL; per-leg PASS/FAIL printed)

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_DIR"

echo "=== Test Pack: sonic_selectable_models_guard [$(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)] ==="

# ── Fences ────────────────────────────────────────────────────────────────
# DEV-only DB fence — pin EVERY variable explicitly (never the glob form).
export ENSEMBLE_SELF_ENV=dev
export POSTGRES_HOST=127.0.0.1
export POSTGRES_PORT=5432
export POSTGRES_DB=ensemble_dev
export POSTGRES_USER=ensemble_dev_app
export POSTGRES_PASSWORD=""
export DAEMON_PORT=8079

if [ "$POSTGRES_DB" = "ensemble_prod" ]; then
    echo "FENCE: refusing to run against a prod-shaped POSTGRES_DB"
    exit 78
fi

# The explicit operator value the ruling ships. Exported so the REAL
# resolver path (not a mocked one) is exercised.
export OPENAI_SELECTABLE_MODELS=agentic,coding,vision
export OPENAI_MODEL_TITLE=title
export OPENAI_MODEL_KEYWORDS=quick
export OPENAI_MODEL=agentic
export OPENAI_MODEL_VISION=vision

PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
    echo "RESULT: FAIL (no worktree venv at $PY — run inside the worktree)"
    exit 1
fi

FAILURES=0

# ── L1 SURFACE ZERO ───────────────────────────────────────────────────────
echo
echo "--- L1 SURFACE ZERO (retired token absent from every shipped home) ---"
L1_HITS=0
L1_SCANNED=0
for target in daemon/config.py config.yaml frontend/src agents scripts; do
    if [ ! -e "$target" ]; then
        echo "  FAIL L1: expected target missing: $target"
        FAILURES=$((FAILURES + 1))
        continue
    fi
    if [ -d "$target" ]; then
        n=$(find "$target" -type f -not -path '*/node_modules/*' | wc -l)
        L1_SCANNED=$((L1_SCANNED + n))
        hits=$(grep -rn --exclude-dir=node_modules --binary-files=without-match \
            'coding2' "$target" 2>/dev/null | wc -l)
    else
        L1_SCANNED=$((L1_SCANNED + 1))
        hits=$(grep -c 'coding2' "$target" 2>/dev/null | head -1)
        [ -z "$hits" ] && hits=0
    fi
    if [ "$hits" -ne 0 ]; then
        echo "  FAIL L1: retired token 'coding2' present in $target ($hits hit(s)):"
        grep -rn --exclude-dir=node_modules --binary-files=without-match \
            'coding2' "$target" 2>/dev/null | head -5
        FAILURES=$((FAILURES + 1))
    else
        echo "  PASS L1: no 'coding2' in $target"
    fi
done
# ANTI-VACUITY: the scan must have read something.
if [ "$L1_SCANNED" -le 0 ]; then
    echo "  FAIL L1 (anti-vacuity): scan read 0 files — a zero-hit result over zero files is not a PASS"
    FAILURES=$((FAILURES + 1))
else
    echo "  PASS L1 (anti-vacuity): scan read $L1_SCANNED file(s)"
fi

# ── L2 RESOLVED SET ───────────────────────────────────────────────────────
echo
echo "--- L2 RESOLVED SET (real load_config(), explicit env verbatim) ---"
L2_OUT=$(timeout 120 "$PY" - <<'PYEOF' 2>&1
import os
import sys

os.environ["OPENAI_SELECTABLE_MODELS"] = "agentic,coding,vision"
os.environ["OPENAI_MODEL_TITLE"] = "title"
os.environ["OPENAI_MODEL_KEYWORDS"] = "quick"
os.environ.pop("OPENAI_ALLOWED_MODELS", None)

from daemon.config import load_config

cfg = load_config(config_path="config.yaml")
got = list(cfg.llm.allowed_models)
want = ["agentic", "coding", "vision"]
print("allowed_models:", got)
print("model_title:", cfg.llm.model_title)
print("model_keywords:", cfg.llm.model_keywords)
ok = True
if got != want:
    print("MISMATCH allowed_models: got", got, "want", want)
    ok = False
if cfg.llm.model_title != "title":
    print("MISMATCH model_title:", cfg.llm.model_title)
    ok = False
if cfg.llm.model_keywords != "quick":
    print("MISMATCH model_keywords:", cfg.llm.model_keywords)
    ok = False
if "coding2" in got:
    print("MISMATCH retired token present in resolved set")
    ok = False
sys.exit(0 if ok else 1)
PYEOF
)
L2_RC=$?
echo "$L2_OUT" | grep -vE "UserWarning|from pydantic.v1" | sed 's/^/  /'
if [ "$L2_RC" -eq 0 ]; then
    echo "  PASS L2: resolved set / title / keywords match the ruling"
else
    echo "  FAIL L2: resolved set did not match the ruling (rc=$L2_RC)"
    FAILURES=$((FAILURES + 1))
fi

# ── L3 COMMITTED PINS ─────────────────────────────────────────────────────
echo
echo "--- L3 COMMITTED PINS (guard file + untouched drift byte-pin) ---"
timeout 280 "$PY" -m pytest \
    tests/unit/test_selectable_models_surface_guard.py \
    "tests/unit/test_llm_allowed_models_precedence.py::TestDefaultConsistencyPin" \
    -q --tb=short -rf -p no:warnings 2>&1 | tail -12 | sed 's/^/  /'
L3_RC=${PIPESTATUS[0]}
if [ "$L3_RC" -eq 0 ]; then
    echo "  PASS L3: committed pins green (8 nodes)"
else
    echo "  FAIL L3: committed pins red (rc=$L3_RC)"
    FAILURES=$((FAILURES + 1))
fi

# ── Verdict ───────────────────────────────────────────────────────────────
echo
if [ "$FAILURES" -eq 0 ]; then
    echo "RESULT: PASS (all legs green)"
    exit 0
fi
echo "RESULT: FAIL ($FAILURES leg(s) red)"
exit 1
