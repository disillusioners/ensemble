# Diagnosis: Stale messages on revive-on-send — carrier payload identified

- **Date**: 2026-09-05
- **Task**: Reproduction + payload capture, DIAGNOSIS ONLY (no fix applied, zero repo edits)
- **Workers**: d88c5917 (recon + R1), 4e0e2bb6 (R2 + R3 API), 07b27d8b (FE e2e)
- **Artifacts**: `/tmp/stale-repro/` (66+ files, temporary — key excerpts embedded below)
- **Env**: dev BE 8079 (./dev.sh, daemon v0.12.0), FE 4199, dev PG (disposable). Prod 9797 + 8088 untouched.

## Verdict

**Carrier payload (M1-equivalent): the dispatch-time SSE `user_message` event for the `[SYSTEM CONTEXT: Project Blueprint]` context block.** Its `message_id` is minted fresh at serialize time and NEVER matches the id `GET /messages` returns for the same content → orphan FE bubble. POST /messages response is clean (no messages array). GET /messages is the truth path.

**Root cause chain** (all file:line verified by workers):
1. `daemon/services/context_messages.py:1484-1493` — blueprint block constructed inline as `HumanMessage(content=..., additional_kwargs={...})` **without `id=`**, bypassing the canonical factory `_make_context_message` (`context_messages.py:84-110`) which assigns `id=str(uuid.uuid4())` at construction. Every OTHER context kind (skills, auto_load_skills, project, project_scope_guide) uses the factory → stable ids.
2. `daemon/utils.py:170` (`serialize_message`) — `... or str(uuid.uuid4())` mints a fresh uuid whenever `msg.id` is falsy, **at read time**.
3. `daemon/services/instance_messaging.py:3953-3994` — SSE pre-emit loop serializes each persistent context msg BEFORE graph execution (one serialize call → SSE id); the checkpoint copy is later serialized by GET (another call → different id).
4. FE (`frontend/src/app/services/sse.service.ts` mapToMessage; `message-merge.util.ts` mergeMessagesById strict union-by-id) — renders the SSE bubble; on reload `loadInstanceMessages` replaces from GET → orphan id not found → bubble dropped.

## Verbatim evidence

SSE orphan frame (R2, gen4_sse.log; emits BEFORE the user message):
```
EVENT=user_message id=49a48c71-f89d-47a6-bb92-251e26abd165
message.message_id = "49a48c71-f89d-47a6-bb92-251e26abd165"
message.role = "user"; message.context_kind = "blueprint"; message.injected_message = true
message.content = "[SYSTEM CONTEXT: Project Blueprint]\n\nMatched Project Blueprints:\n✓ Core Architecture (score: 1.00, source: core)\n\n--- Core Architecture --- ..."
message.created_at = "2026-09-05T04:05:41.895236+00:00"; checkpoint_id = "user"
```
GET copy, same content, different id (gen4_get.json):
```json
{"message_id": "15105d45-6810-42ef-848a-f0f53bfb9270", "role": "user",
 "context_kind": "blueprint", "injected_message": true,
 "created_at": "2026-09-05T04:08:40.336976+00:00",
 "content": "[SYSTEM CONTEXT: Project Blueprint]\n\nMatched Project Blueprints:..."}
```
POST /messages response (revive path, 200) — clean, no messages[]:
```json
{"message_id":"8d43cfe7-...","role":"assistant","content":"","job_id":"a59cc235-...","queued":false,"auto_resumed":false,"resume_info":null}
```
Independent cross-validation (FE round, fresh instance fbccf21d): SSE id `6a7f72c7-2f5c-4987-a168-3aae6b9f0e75` vs GET id `090f513c-6918-4869-ae42-e5c43d4a9ab7`, `idsMismatch_sseVsGet: true` — captured live via EventSource monkey-patch + GET, same turn.

Emission order (gen4, one 2.008s batch, pre-emit before graph): `project → blueprint → user query` → orphan renders ABOVE the new message, matching the incident's M1 placement.

## Trigger conditions (empirically bounded)

| Condition | Finding |
|---|---|
| Blueprint matcher fires (project `blueprint_active=true` + non-empty blueprints + trigger-matching user query) | REQUIRED — trivial/non-matching queries emit nothing |
| Instance's FIRST context-bearing turn (no `auto_load:{iid}:{aid}` marker in checkpoint) | REQUIRED — once-per-instance gate holds even when matcher fires again (R3: matcher logged, zero SSE, GET unchanged) |
| Instance status COMPLETED / revive | NOT required — orphan fires on any first-context turn incl. fresh turn 1; revive not special |
| Daemon restart | NOT required (resets `_emitted_message_content` dedup, but the gate makes it moot for re-emit) |
| Old injected messages (source=internal_agent:*) re-emitted on revive | NO — never observed in any round |
| Other context kinds (skills/auto_load/project) | NOT affected — factory-built stable ids, SSE id == GET id |
| POST /messages response or GET carrying stale content | NO — both clean |

## What did NOT reproduce + incident reconciliation

1. **FE "vanishes after reload" did NOT reproduce on current dev.** The FE round showed the blueprint bubble SURVIVES reload: GET also returns the checkpoint copy (under its own id), so the visual slot is refilled after the SSE orphan is dropped. The id mismatch is real but visually masked in the current FE+BE pair. Screenshots: `/tmp/stale-repro/fe/13_before_reload_fresh.png`, `18_after_reload_fresh.png` (not pixel-verified — image reader path-restricted; bubble-list text captures used instead).
2. **Incident (bee097d3) reconciliation — best-supported theory, partially unproven:** For M1 to VANISH on reload (as reported), the GET checkpoint must NOT contain the blueprint block. Our repro always checkpointed it. The discriminator we could not run: **compaction** (no compact API route exists — 127 openapi paths searched, zero hits; proactive compaction needs long history, out of timebox). Theory: bee097d3 was old/large with proactive compaction newly enabled; compaction dropped the auto_load/blueprint markers (re-arming context assembly → M1 orphan with UNRELATED matched blueprint — matching the report's "unrelated content") and summarized away old real messages (M2/M3) — while the FE kept pre-compaction bubbles in memory. Reload → GET returns the compacted checkpoint → M1–M3 all gone. M2/M3 themselves were never observed re-emitting via any payload in 3 rounds; FE-memory residue across a compaction boundary is the only consistent explanation.
3. Minor: incident's reported order (M1,M2,M3,M4) vs created_at sort expectation (M2,M3 old → first) — soft evidence, likely approximate recollection or append-order rendering; not chased further.

## Minimal repro recipe (API only, ~3 min)

```bash
REPO=/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble
PID=$(curl -s -X POST http://localhost:8079/api/projects -H 'Content-Type: application/json' \
  -d "{\"name\":\"stale-repro\",\"main_directory\":\"$REPO\"}" | python3 -c "import json,sys;print(json.load(sys.stdin)['project_id'])")
curl -s -X POST http://localhost:8079/api/projects/$PID/blueprints/initialize
curl -s -X PUT http://localhost:8079/api/projects/$PID/metadata/blueprint_active -H 'Content-Type: application/json' -d '{"value":true}'
T=$(curl -s -X POST http://localhost:8079/api/instances -H 'Content-Type: application/json' \
  -d "{\"agent_id\":\"worker\",\"project_id\":\"$PID\"}" | python3 -c "import json,sys;print(json.load(sys.stdin)['instance_id'])")
curl -N http://localhost:8079/api/instances/$T/events &   # watch SSE
curl -s -X POST http://localhost:8079/api/instances/$T/messages -H 'Content-Type: application/json' \
  -d '{"content":"Explain the core architecture: how does the daemon assemble the LangGraph state graph and its middleware chain?"}'
# → SSE shows user_message for "[SYSTEM CONTEXT: Project Blueprint]" with a serialize-time uuid
# → GET /api/instances/$T/messages shows the same content under a DIFFERENT (checkpoint) uuid
```
Key artifact map: gen4_sse.log + gen4_get.json (orphan pair), orphan_frame_excerpt.txt / get_copy_excerpt.txt (verbatim), gen7_* (revive no-recur proof), gen8_* (skills stable-id proof), fe/14_sse_blueprint_events_fresh.json + fe/15_mid_get_fresh.json (FE-round id mismatch).

## Gaps / suggested next steps (NO FIX applied per task scope)

- [ ] Compaction discriminator untested (needs long-history instance; no compact API route). Would prove/refute the M1-vanishes + M2/M3 theory for the original incident.
- [ ] Future fix direction (documented only, NOT applied): construct the blueprint block via `_make_context_message(kind=CONTEXT_KIND_BLUEPRINT, ...)` so it gets a construction-time stable id like every other context kind (one-line class of change at context_messages.py:1484-1493).
- [ ] Pixel-review the two FE screenshots if visual sign-off is wanted.
- [ ] Dev-env residue: repro instances/projects (stale-repro-P1/P2, T/T2/H/L, fbccf21d, e78d0a39) linger in disposable dev DB; daemon 8079 and FE 4199 left running.
