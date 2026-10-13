# Memory

## v2 designer handoff contract — canonical home (D-1 fixup round, 2026-10-01)

The designer-sourced handoff contract (gate / verify / relay / collect / edge fields) lives in two places **on purpose**; they are not duplicates — each is the canonical contract for its version's idiom:

- **`agents/developer/` (base, v1)** — carries the **implement-side** contract. v1 developer IS the implementer; workflow steps are *read spec → verify SHA → honor handoff fields → implement against pack-mapped ACs → cosmetic-skip awareness → report edge-contract fields*. The Cardinal in base `agents/developer/rule.md` is "NEVER implement until I verify the spec."
- **`agents/developer[v2]/` (v2, live-resolved)** — carries the **dispatch-side** contract. v2 developer is a dispatcher/relay (Cardinal #1: ALWAYS dispatch coding work; v2 does not write source — implements via `coder`/`worker` dispatch). The Cardinal here is `1b.` ("gate the dispatch against an approved spec"), and the workflow section **Designer-Sourced Tasks (handoff contract)** expresses the gate → relay → collect → cosmetic-skip → re-conformance flow. v2 NEVER implements; it relays the pack-mapped ACs, the handoff fields, and the canonical artifact paths to a `coder`/`worker` executor, then collects `commit_sha`, `diff_stat`, `pages_changed`, `conformance_iter`, and capture paths from the executor's report.

**Why two files, not one with `if v2 else ...`:** cross-version file dependencies silently break when either dir is removed (a v2 dir pointing at a base file works only while base lives). The prompt-writing guide requires per-version canonical homes when the idiom diverges — v2 dispatcher semantics vs v1 implementer semantics are not the same contract; they are the same contract expressed in different roles.

**Why a `memory.md` block here:** `memory.md` is outside the registry-test scanned prompt surface (`PROMPT_SURFACE = (rule.md, workflow.md, soul.md)`); the note doesn't risk the report-integrity fixture tripping on a prose token, and it survives prompt-file edits.

## Cross-references between versions

v2 cardinal `1b.` says "See **Designer-Sourced Tasks (handoff contract)**" — that points at the v2 workflow section, NOT at base `agents/developer/workflow.md`. Base holds the implement-side Steps; v2 holds the dispatch-side Steps. Reading the base copy from v2 would teach the v2 developer to *implement*, which it must not.
