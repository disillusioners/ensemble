# Census Gap: `scheduler` origin unreserved — gate blocker (2026-08-30)

## Finding
The reserved-origin contract batch (16 members, constants.py:424) missed one daemon-minted durable origin: **`scheduler`**.
- Mint: `daemon/sources/adapters/scheduler.py:765` (`_route_via_job_queue`, source="scheduler")
- Sinks (durable, BOTH): `job_queue_items.source` (job_type='message' JAFP mirror via enqueue_message_job) AND `message_queue.source` (instance_messaging.py:1401)
- Not in RESERVED_SOURCE_PREFIXES, not in USER_ORIGIN_SOURCES → POST /api/jobs accepts it (201, e2e-confirmed) → user-created rows are field-level indistinguishable from daemon scheduler mints
- Zero sink privilege today (dispatch sinks branch only on internal_report:/internal_error_report:/internal_agent:job_event:/system: at instance_messaging.py:2283-2311) → provenance/audit forgery only
- Asymmetry: `admin-endpoint`/`auto-scan` (same pure-daemon-identity class) ARE reserved; the channel-adapter framing at constants.py:408-409 conflated user-bearing origins (telegram:<user>) with this userless daemon identity

## Why the batch census missed it
Forward census (by-member grep) can only detect over-reservation. The blocker direction is the REVERSE scan: enumerate all mint sites, diff against the reserved set. Always run BOTH directions for origin/enum contracts.

## Remediation (developer)
Option A (recommended): reserve exact `"scheduler"` + membership pin 16→17 + flip `test_create_job_accepts_scheduler_source` to expect 422. Option B: re-mint `scheduler:<schedule_id>` + reserve prefix (heavier; existing rows).
Adjacent 🟠: `SourceCreate.source_id` pattern permits reserved words (`system`, `internal_report`) → registration-time rejection recommended (registry.py:867 mint `f"{source_id}:{user_id}"` would match reserved dispatch prefixes).

## Gate record
RESULTS/2026-08-30-security-boundary-hygiene-gate.md §2/§8. Re-gate after fix: security_boundary_hygiene_suites + api pack + origin_contract_e2e_probe + reverse census.
