---
version: 1.0.0
category: ops
auto_load: false
---

# Video Lab Ops

The lab is a standing asset, rebuilt rarely, kept warm always. I own its health.

## The Stack (all under service management)

- proxy :8987 → layout :8981 → local Postgres `ydlab_layout` (one table:
  dashboard_tabs)
- lab-auth :8591 (the yggdrasil stub)
- FE dev server :4300
- Start / health / stop through my service lanes; between missions everything stays
  warm so a hot-lane session never pays my cold-start.

## Fixture Facts That Bite

- Fixture tenant: uid 9001 (NUMERIC — required), tenant 1706.
- Backend usernames must be NUMERIC; the tabs proxy bypass needs `utype > 3` or the
  ACL service gets dialed (localhost resolving to IPv6 for that dial is the trap).
- A granted-set mismatch does NOT hide a tab — it silently deletes the user's stored
  row (designed behavior). If a capture shows a tab vanishing, suspect seeding drift
  first.
- Pin body endpoints BY NAME to empty payloads; anything unpinned must fail loudly,
  never silently empty. The fixture dashboard body stays structurally sparse.
- The frontend build needs its pinned Node line and legacy-peer-deps; Bootstrap loads
  from CDN — do not block it.
- The layout-server flip lives in the lab clone and is NEVER committed upstream.

## Warm-Cache Discipline

- Browser binaries, node_modules, and seeded fixture state are standing assets; I
  refresh them on MY clock, never on a mission's clock.
- The `frontend-engine` clone inside the lab is pinned; a product change means the
  re-capture flow (new build, new `fe_build_sha`), not a moving lab checkout.

## Invalidation Re-Derivation (product-change missions)

Read the ledger (`build/captures/invalidation.json`), diff the new build's changed
routes against every beat's route and asserts, publish the diff-report, re-capture the
affected set with the new `fe_build_sha` recorded. Spec:
`.agents/shared/planning/video-team/data/invalidation-spec.json`.

## Stack Version Changes

A harness or stack version bump invalidates every ledger row — loudly, in my mission
report, before any downstream consumer touches stale frames.
