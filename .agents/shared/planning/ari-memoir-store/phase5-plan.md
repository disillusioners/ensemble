# Phase 5 — Wake briefing

**Depends on:** P2 (persona getters, keepsakes, latest fetches). **Flags consumed:** #3 (budget model — the core of this phase), #9 (once-per-instance + affordance), #1 (zone arithmetic consumer), #13 (OFF → no block).

## 1. Objective

The turn-1 `[SYSTEM CONTEXT: Memoir]` block at the assembly seam: stable id `memoir:{agent_id}`, compaction-hoisted, once per instance, two zones (stable never-truncated / dynamic oldest-first), the normalized budget model, kill-switch-aware — memory as *presence*, never a loader injection (L1/DD-10).

## 2. Decisions

| ID | Decision | Rationale (verified anchors) | Rejected alternatives |
|----|----------|------------------------------|------------------------|
| D5.1 | New kind `CONTEXT_KIND_MEMOIR = "memoir"` + builder `_build_memoir_briefing_message(agent_id, instance_id, manager)` called from `assemble_context_messages` (verified `daemon/services/context_messages.py:1892`), emitted via `_make_context_message` (stamp site `:175` — ADR-5 `injected_message=True` + `context_kind`, verified `:22`) | the blessed seam (§8.1); the snapshot-digest builder is the structural template (`:358-392` — verified) | a new middleware slot (the seam IS the assembly function); loader injection (L1 — forbidden) |
| D5.2 | Stable id `memoir:{agent_id}` via a `_stable_id_for` branch (verified `:395`, the `kv:{context_key}` branch shape at `:481`); CONTEXT_KIND_MEMOIR added to the compaction-hoisted kind membership exactly as `CONTEXT_KIND_SNAPSHOT_DIGEST` (`:130`, hoisted bucket usage `:388` — re-grep the hoist site at implementation; the design names it "context_kind hoisted") | one block per key, no duplicates (add_messages supersedes in place); hoisting = the briefing survives compaction | per-turn ids (duplicate blocks under compaction re-append) |
| D5.3 | Once-per-instance via `instance_metadata["memoir_briefing_injected"]`: read FRESH each assembly (the `project_injected` mechanics — graph fresh-read `daemon/graph.py:938`+`:952-982`, flag write `daemon/services/instance_messaging.py:4241` — all verified); written ONLY after successful append (`set_metadata_many`); unset-flag-with-knob-off leaves the flag UNSET so a later enable injects on the next turn (flag #13 nuance, intended) | DD-11; revive-safe (a lost flag re-receives the briefing); graph/messaging two-site pattern | per-turn reinjection (token tax); spawn-time one-shot (misses graph-only paths) |
| D5.4 | Agent condition: briefing builds ONLY for `agent_id == "ari"` (resolved from the `agent_meta`/context the seam already holds) | ari-exactly extends to the briefing (the memory is hers); no other agent sees memoir data | briefing for all agents (data-exposure + scope creep) |
| D5.5 | Zones per flag #3's normalized model: **stable** = approved persona (≤2,000 tokens, admission-bounded) + ALL keepsakes (≤4,000 tokens, pin-admission-bounded) — rendered in full, NEVER truncated, keepsake lines carry `pinned_by` + `pinned_reason` (flag #2's observability layer); **dynamic** = latest ~5 episodes ≤3,000 tokens, oldest dropped first; **absolute ceiling 24,000 chars** enforced at render (dynamic shrinks to fit, final fallback = the affordance line + elision disclosure) | design §8.2 + flag #3 disposition (recommended Option B); estimator = `estimate_tokens` (`daemon/loader.py:1027` — verified; the snapshot-digest cap precedent :275-287 uses it) | 12k-chars-truth (starves the zones — flag #3 Option A rejected) |
| D5.6 | Closing affordance line (flag #9): the block ends with a one-liner stating memory is queryable mid-session via `memoir_recall(query)` / `memoir_timeline(…)` — byte-asserted in the pack | answers "why doesn't she remember an hour ago" at the affordance level; DD-11 stands | re-injecting on schedule (v2's alarm-wake item, L4-constrained, NOT built) |
| D5.7 | Builder DB reads strictly off-loop (`asyncio.to_thread`); a failed read must NOT abort assembly (swallow + skip — the snapshot-digest failure discipline, verified `:375-387`); empty store (seed only) still emits (persona is day-one presence); knob OFF → return `None` (no block, no flag write) | the assembly seam must never be hostage to memoir health; L2 (DB is the only durable substrate — no file caching of the briefing) | on-loop reads (the routed incident class); caching the rendered block (staleness by design) |
| D5.8 | Escaping + ADR-7 discipline: `escape_for_context_block` on rendered content, `[SYSTEM CONTEXT: Memoir]` title form | the module's own ADR-4/ADR-5 contracts (verified `:20-27`) | raw markdown injection (format contract) |

## 3. Tasks

| ID | Task | Outcome | Touchpoints | Depends | AC |
|----|------|---------|-------------|---------|----|
| T5.1 | Kind + stable-id branch + hoist membership | `_stable_id_for('memoir', agent_id=…)` → `memoir:{agent_id}`; hoist set carries the kind | daemon/services/context_messages.py | P2 | AC1 |
| T5.2 | Two-zone renderer + budget engine (persona/keepsakes/latest fetches → zones → tokens → chars ceiling → disclosures) | deterministic render from fixtures; overflow legs exact | daemon/services/memoir_briefing.py | T5.1 | AC2 |
| T5.3 | Seam wiring in `assemble_context_messages` + once-flag read/write + agent condition + knob gate | first turn for ari: block present + flag set; second turn: absent; non-ari: never | context_messages.py | T5.2 | AC3 |
| T5.4 | Budget pins: admission arithmetic (12×~333 tokens fits 4,000; pathological long-pin rejection), 24k ceiling, dynamic oldest-first, affordance byte-assert | mutation legs: ceiling removed → RED; affordance dropped → RED; stable-zone truncation attempted → RED (must be unreachable) | tests/unit/memoir/ | T5.3 | AC4 |
| T5.5 | Compaction survival leg | simulate compaction → the hoisted memoir block persists in the re-appended set | (pack-level with the compaction fixtures) | T5.3 | AC5 |

## 4. Acceptance criteria

| ID | Criterion |
|----|-----------|
| AC1 | Stable id exact; duplicate-append impossible (idempotent supersede); hoist membership grep-pinned |
| AC2 | Zones: persona+keepsakes rendered verbatim under ceilings; keepsake lines show `pinned_by`/`pinned_reason`; dynamic drops oldest first; disclosures exact bytes |
| AC3 | Once-per-instance: turn-1 present/flag-written; turn-2 absent; revive-without-flag re-injects; knob OFF → no block AND flag stays unset; non-ari → no block ever |
| AC4 | The four mutation legs of T5.4 all RED→GREEN |
| AC5 | After compaction, `[SYSTEM CONTEXT: Memoir]` remains in history (hoisted), dynamic staleness disclosed, stable zone intact |

## 5. Test packs

- `test/packs/memoir_briefing_unit_test.sh` — renderer + budget + affordance + once-flag (mock repo).
- `test/packs/memoir_briefing_seam_test.sh` — assembly-seam integration: block present at turn 1 for ari via the real `assemble_context_messages`, absent turn 2; compaction leg.

## 6. Flag consumption

#3 (D5.5 — this phase OWNS the budget engine), #9 (D5.3/D5.6), #1 consumer arithmetic, #13 (D5.3/D5.7), plus DD-10/DD-11 (block-not-loader, once-not-chant) and L1/L2 guardrails.
