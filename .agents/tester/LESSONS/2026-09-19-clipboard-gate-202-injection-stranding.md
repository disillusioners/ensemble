# LESSON: 202-lane injection stranding on single-superstep turns (clipboard-image-chat gate, 2026-09-19)

**Class:** pre-existing delivery-lane limitation, made deterministic by clipboard-image-chat's
sync-in-POST conversion; second, branch-caused aggravation on the recovery path.

## Symptom (live, worktree daemon @ b59bc58e, disposable PG)

POST `/api/instances/{id}/messages` with `image_refs` while target RUNNING → HTTP 202
("injected"). After the turn drains, `GET /messages` does NOT contain the message. The content
sits in the RAM FIFO (`manager._pending_injections`) indefinitely:
`GET /api/instances/{id}/injection` → `{"pending": true, "pending_count": 1}` 15+ min later.
FE shows it only as a "pending injection" card, never a message bubble.

## Root cause (two-layer)

1. **Pre-existing lane limitation (NOT image-specific):** the 202 lane = RAM FIFO consumed ONLY at
   `agent_node` tap points (graph superstep boundaries) or by the wake-turn D2 seam-drain
   (`instance_messaging.py:3896-4033`). A **single-superstep turn** (one LLM call, no tool loop)
   has no tap after the injection lands → stranding until the NEXT message wakes the instance
   (D2 drain) — unbounded latency; **permanent loss if the daemon restarts while stranded**.
   Proven content-independent: a plain-text POST forced through the 202 lane (delayed 4s) strands
   identically (EXP-1b, instance 0f40bde9).
2. **Branch aggravation A — deterministic trigger:** the 202 handler blocks ~7s awaiting
   image-reader child conversion BEFORE appending, so an image_refs 202 can never win the
   sub-second pre-tap window that a fast plain-text append sometimes wins.
3. **Branch aggravation B — refs dropped on recovery:** the D2 leftover builder
   (`instance_messaging.py:3897-3922`) stamps `injected_message`/`source` but NOT `image_refs`,
   so when the stranded content finally delivers on the next turn, wire `images` is None — the
   h4-S1 union guarantee is violated on the delayed path (thumbnail lost even after recovery).

## What works (proven same session)

- Multi-superstep turns deliver MID-TURN with refs intact: drain-site kwargs stamp
  (`graph.py:6647-6673`) + union serializer verified live (EXP-3, instance 4bd474c3 — wire
  `images: ['/api/tmp_images/<32hex>']`, main-agent STANDARD-only routing).
- Idle→send (durable 200 lane) is fully correct incl. reload rendering (leg b).
- Legacy data-URI path: main agent vision-routed as designed (leg c) — Discord regression clean.

## Repro precision

Strands iff (POST enters 202 lane: `status=="running" AND has_live_graph_task()`,
`routers/messages.py:544`) AND (current turn has no further agent_node tap after append —
i.e. single-superstep). Secondary fragility: the lane gate races at turn start (status flip
precedes `_graph_tasks` registration — a +0.01s POST got 200-durable while +0.1–0.3s got 202).

## Fix directions (characterized, not applied — frozen branch)

1. Turn-end fallback: on graph completion with non-empty FIFO → re-dispatch via durable enqueue
   (or refuse 202 → 200-durable when the running turn is single-superstep).
2. One-line: mirror the `image_refs` stamp in the D2 leftover builder (mirrors `graph.py:6647`).
3. Route image_refs sends to RUNNING targets through the durable lane — conversion latency makes
   the live-tap window mathematically unwinnable.

## Severity ruling (gate)

HIGH for the images flow (deterministic trigger + restart-loss window + refs-drop-on-recovery);
plain-text variant MEDIUM (timing-dependent, recoverable). Merge disposition = caller's call;
this gate reports it as the headline defect against plan #40's stated expectation
("202 → drain → reload → GET /messages shows refs in images").
