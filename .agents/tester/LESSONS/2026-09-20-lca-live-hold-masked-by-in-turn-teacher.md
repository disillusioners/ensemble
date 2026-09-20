# Lesson: live-flow gate HOLD is masked by the in-turn tool-teacher (2026-09-20 LCA attest-first gate)

## Finding
Two disposable-boot live runs (happy path + delegated c5d9a38a replay) show the gate's `Decision.HOLD` **never fires live when the model cooperates**: LangGraph re-invokes the agent after EVERY tool_call, so after a bundled text+attest message the tool's ContextVar-runtime-hook returns `ATTEST_BUNDLED_RESULT_TEXT`, the model is re-invoked, and it corrects WITHIN the same turn (clean attest → standalone report). At `graph_end_candidate` the final AI is the report → `decision=allowed`. The bundled message is never the LAST AI in a cooperative flow.

## Consequence
- Primary correction mechanism live = **tool-result teacher (in-turn)**; gate HOLD = **turn-end backstop** for defiant/edge turn-end shapes (the incident class: c5d9a38a leaders that END their turn on the bundled/attest-only message).
- HOLD-branch ENFORCEMENT must be verified at the real-gate-node seam (synthetic turn-end states) — done and green (independent e2e variants b/c + hold-semantics scenarios, real node, real tool).
- A daemon-level HOLD sighting would need a defiant mock (post-teacher responses keep emitting attest-only/bundled and the turn actually ends) or graph-loop edge conditions; not pursued this gate (documented limitation N2 in the gate report).

## Also captured (same runs)
- Default attestation mode = **enforce** (`DEFAULT_MODE: Literal["enforce"]`, daemon/services/attestation_resolver.py:111; boot row `mode=enforce ... attestation_enabled=true`).
- Delegation conditioning works live: `send_message` delegation → child completes → gate row shows `attestation_required=True` + `delegation_tool_call_total=1` (the no-delegation branch leaves `attestation_required=False` — gate allows regardless; happy-path boot must include a delegation to exercise the required path).
- Boot recipe: PG 15433 (initdb -A trust) / uvicorn daemon 8090 (NOT dev.sh — hardcodes 8079) / stdlib mock LLM 18080; see MOCK_TESTS.md registrations.
