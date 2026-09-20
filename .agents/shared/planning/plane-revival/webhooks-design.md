# Plane → Ensemble Webhooks — Design Note (Phase 4, design only)

Status: DESIGN — no implementation shipped in this phase. Written 2026-09-20
as part of Phase 4 / plane-integration-revival. Goal: make drift detection
event-driven instead of sweep-driven, building on the Phase-3 sync state
machine (`daemon/services/plane_sync_service.py`) rather than replacing it.

## Context

Phase 3 ships a 4-state sync machine (`linked / syncing / drift / error`)
driven by two triggers today: the create-hook (fire-and-forget) and the
watchdog sweep (300s cadence + boot sweep + stale-syncing recovery). Drift
is currently detected only when WE push (the update response disagrees with
the Ensemble project). A Plane-side edit surfaces as drift only after the
next corrective sync happens to compare responses — in practice, late or
never. Webhooks close that gap: Plane tells us when it changed something.

## Events to subscribe

Per Plane project webhook, subscribe to (verify exact event names against
the Plane docs for the deployed instance version at implementation time):

| Plane event | Why | Phase-3 mapping |
|---|---|---|
| project updated | Plane-side name/description edit = identity drift | mark `drift` → corrective sync re-pushes Ensemble values |
| project deleted | stale-handle recovery (today: discovered on 404 at update time) | clear `plane_project_id` + mark `error` → next attempt re-adopts by name (the `_create_or_adopt` path) |

Issue / cycle / module events are NOT subscribed in v1: the sync surface is
project identity only (name, description). Issue events become relevant
when PM-facing issue features land — the endpoint schema below reserves
room for them.

## Endpoint shape

```
POST /api/plane/webhook
```

- Mounted on the existing plane router (`daemon/routers/plane.py`); no
  user-session auth (machine-to-machine; verified by HMAC below).
- Body: Plane's JSON webhook payload. v1 reads only a normalized sketch:

```json
{
  "event": "project.updated",          // event type discriminator
  "event_id": "<unique delivery id>",  // idempotency key (see below)
  "workspace_slug": "nea",
  "data": { "id": "<plane project uuid>", "name": "...", "description": "..." }
}
```

- Handler contract: validate → dedupe → **202 Accepted immediately** →
  process async (mirror the create-hook fire-and-forget pattern). Never run
  Plane HTTP calls inline in the request; webhook handlers must be fast-ack
  (Plane retries slow/non-2xx deliveries).

## Auth / secret verification

- One shared secret per webhook registration (generated at setup, stored in
  daemon config — treat like `PLANE_API_KEY`; never logged, never in
  tracked files).
- Plane signs each delivery with HMAC-SHA256 over the RAW request body;
  signature travels in a header (Plane's exact header name — commonly
  `X-Plane-Webhook-Signature` — must be confirmed against the deployed
  Plane version). Verify with `hmac.compare_digest`; mismatch → 403, no
  state mutation, one warning log line.
- Envelope checks before HMAC: body size cap (e.g. 64 KiB), JSON parse
  failure → 400.

## Idempotency

At-least-once delivery is the norm, so dedupe on `event_id`:

- Keep a small `plane_webhook_dedupe` store (daemon-side table or
  project-metadata rows keyed `plane_webhook_seen:{event_id}`) with a TTL
  window of ~7 days — long enough to absorb Plane's replay storm after an
  outage, short enough to stay bounded. A duplicate id → 202 + no-op.
- Dedupe write MUST be atomic with the state transition it guards (same
  transaction/claim), otherwise a crash between dedupe and effect replays
  the effect.

## Replay handling / tolerance

- Plane retries non-2xx deliveries with backoff for a bounded window
  (confirm the retry schedule against the deployed instance; assume
  aggressive). Our contract: 2xx only after durable acceptance (dedupe
  record or completed no-op check); processing failures after ack are
  absorbed by the next sweep, not the webhook.
- Ordering is NOT guaranteed — events may arrive out of order or
  duplicated. Therefore a webhook is a HINT, never a state authority:
  on receipt of `project.updated` the handler re-fetches the authoritative
  project from the Plane REST API and runs the existing `_is_drift`
  comparison before touching state. This keeps a stale/duplicated webhook
  from flipping a row to `drift` based on outdated data.

## Grounding in the Phase-3 state machine

Webhook receipts only ever produce transitions the machine already owns:

- `project.updated` (drift confirmed via REST fetch) → `plane_sync_state =
  "drift"`. No push from the webhook path itself — the watchdog's
  corrective-sync pass (backoff, quarantine, boot sweep) re-drives it
  exactly as it does sweep-discovered drift today.
- `project.deleted` → clear `plane_project_id` + `error` — the same
  recovery the stale-handle path performs reactively (plane_sync_service
  `PlaneNotFoundError` handler), now proactive.
- Receipts go through the rowcount-guarded `claim_sync_slot` CAS — a
  webhook never fights the endpoint or watchdog for a slot; if a sync is
  in flight, the receipt is dropped (the in-flight sync's own result wins,
  and the next webhook/sweep re-checks).
- Kill-switch: webhook endpoint gates on `PLANE_SYNC_ENABLED` — off →
  503 (same disabled shape as the sync endpoint), no state writes.

## Rollout sketch (when implemented)

1. Endpoint + HMAC + dedupe (no Plane-side config needed to deploy the
   receiver).
2. Config plumbing for the per-project webhook secret + registration UX
   (manual curl to Plane or a management endpoint — decide then).
3. Soak with logging-only mode (verify receipts, mutate nothing) before
   enabling state transitions.

## Open questions

- Exact Plane webhook header names + retry schedule for the deployed
  instance (mcp.ensem.dev) — verify live before implementation.
- One daemon-wide secret vs per-project secrets (Plane webhooks are
  configured per project; if secrets are per-project, the registration
  record needs to live on our side keyed by plane project id).
- Do we want webhooks to trigger an IMMEDIATE corrective sync (skip
  backoff) or keep the watchdog's cadence even for event-driven drift?
  (Design above: keep watchdog cadence — simpler, rate-limit-friendly.)
