# Governor Implementation Changelog

## Phase 0 (contracts) — 2026-07-25
- Created `daemon/governor/` directory with reserved names
- Froze 7 contracts in `daemon/governor/contracts.py`
- Froze AgentMetadata extension spec in `daemon/governor/schemas.py`

## Phase 1 (planned)
- Governor agent definition under `agents/governor/`

## Phase 2 (planned)
- `spawn_councilor` tool in `daemon/tools/instance.py`
- `clear_councilor_errors` tool in `daemon/tools/instance.py`

## Phase 3 (planned)
- `append_allowed_models` appender in `daemon/services/instance_lifecycle.py`
- `inject_allowed_models` flag in `AgentMetadata` (registry.py)