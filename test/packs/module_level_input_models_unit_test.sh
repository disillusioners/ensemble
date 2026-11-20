#!/usr/bin/env bash
# Test Pack: module_level_input_models_unit_test
# Tests: tests/unit/tools/test_module_level_inputs_{a,b,c,d}.py
#        tests/unit/tools/test_schema_registry.py
#        tests/unit/tools/test_frozen_tool_name_discovery.py
# Timeout: 400s (command-level via the PACKS.md `timeout 400` wrapper;
#          layer-2 internal watchdog 350s)
#
# Covers the ROW2-2B RESIDUAL lane: the ~50 factory-local
# ``class XInput(BaseModel)`` declarations hoisted to MODULE level
# (residual 50 → 0 on the whole tool surface). The per-assembly
# pydantic class rebuild that the schema registry could NOT memoise
# (the caller supplies ``args_schema``, so the registry never saw it)
# is gone for every cohort:
#
#   * cohort pins a–d — each hoisted class is module level (no
#     ``<locals>`` qualname, module's own ``__module__``), its factory
#     binds THE module-level class, a SECOND factory build constructs
#     ZERO pydantic model classes, and the second build's OpenAI
#     payloads are byte-identical to the per-cohort BASE fixture
#     captured BEFORE the hoist;
#   * the whole-surface zero census — the schema-registry pack stays
#     green and an inline census builds ``create_instance_tools`` TWICE:
#     the cold build must construct > 100 model classes (the instrument
#     measures something) and the SECOND build ZERO;
#   * the payload derivation is memoised too — the second
#     ``openai_tool_payloads`` pass constructs ZERO model classes;
#   * 2B regression + frozen tool names stay green (the hoist must not
#     rename or re-scope any tool).
#
# INTEGRATION-LEVEL pack: it names the sibling cohort test files, so it
# runs on the MERGED tree (merge order: hoist-d-gate is LAST). On a
# single-cohort worktree the pytest step fails on the missing sibling
# files by design.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: module_level_input_models_unit_test ==="

cd "$PROJECT_DIR"

# Layer 2 (script-internal): 350s — interrupts hung steps
# Layer 1 (command-level): 400s via the `timeout 400 bash ...` wrapper
# in the PACKS.md row.
EXIT_CODE=0
timeout 350s .venv/bin/pytest \
  tests/unit/tools/test_module_level_inputs_a.py \
  tests/unit/tools/test_module_level_inputs_b.py \
  tests/unit/tools/test_module_level_inputs_c.py \
  tests/unit/tools/test_module_level_inputs_d.py \
  tests/unit/tools/test_schema_registry.py \
  tests/unit/tools/test_frozen_tool_name_discovery.py \
  --tb=short -q 2>&1 || EXIT_CODE=$?

if [ $EXIT_CODE -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
elif [ $EXIT_CODE -ne 0 ]; then
  echo "RESULT: FAIL (pytest step exit $EXIT_CODE)"
  exit 1
fi

# Whole-surface zero census (the tighter pin that lives HERE, not in
# the untouched test_schema_registry.py budget).
timeout 350s .venv/bin/python - <<'PYEOF' || EXIT_CODE=$?
"""Cold > 100 / warm == 0 census over the WHOLE tool surface."""

import sys
from unittest.mock import MagicMock

from pydantic._internal._model_construction import ModelMetaclass

from daemon.tools._schema_registry import openai_tool_payloads
from daemon.tools.instance import create_instance_tools


def main() -> int:
    counter = {"n": 0}
    original = ModelMetaclass.__new__

    def _counting_new(mcls, name, bases, namespace, **kwargs):  # noqa: ANN001
        counter["n"] += 1
        return original(mcls, name, bases, namespace, **kwargs)

    ModelMetaclass.__new__ = _counting_new
    try:
        manager = MagicMock(name="InstanceManager")
        manager.config.llm.allowed_models = []
        manager.config.daemon.port = 0

        create_instance_tools(manager, "pack-census", "")
        cold = counter["n"]
        if cold <= 100:
            print(
                f"FAIL: cold build constructed only {cold} pydantic model "
                "classes (expected > 100) — the census is not measuring "
                "what this pack claims to measure"
            )
            return 1

        counter["n"] = 0
        warm_tools = create_instance_tools(manager, "pack-census", "")
        warm = counter["n"]
        if warm != 0:
            print(
                f"FAIL: the SECOND assembly still constructed {warm} "
                "pydantic model classes — a factory-local input model "
                "(or another per-assembly rebuild) is back"
            )
            return 1

        openai_tool_payloads(warm_tools)
        counter["n"] = 0
        payloads = openai_tool_payloads(warm_tools)
        if counter["n"] != 0:
            print(
                f"FAIL: the SECOND payload derivation constructed "
                f"{counter['n']} pydantic model classes — the payload "
                "memoisation is gone"
            )
            return 1
        if len(payloads) != len(warm_tools):
            print(
                f"FAIL: payload census {len(payloads)} != tool census "
                f"{len(warm_tools)}"
            )
            return 1
    finally:
        ModelMetaclass.__new__ = original

    print(
        f"PASS: cold={cold} model classes, warm=0, "
        f"payload re-derivation=0 over {len(warm_tools)} tools"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
PYEOF

if [ $EXIT_CODE -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
elif [ $EXIT_CODE -ne 0 ]; then
  echo "RESULT: FAIL (census step exit $EXIT_CODE)"
  exit 1
fi

echo "RESULT: PASS"
exit 0
