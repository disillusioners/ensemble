# Metrics Unification — Inventory (evidence phase)

Read-only inventory of metrics & resource observability across agents-ensemble, gathered 2026-11-01 for the central-metrics-system architecture discussion. Content ref: `origin/latest = 62627265f` (v0.28.4-era). Full synthesis rides the commissioning leader's final report.

- `metrics-inventory-backend-producers.md` — producers & storage: every producer of metrics/resource data with cadences, the PG table census, in-memory counters, log-line metrics; scatter analysis; onboarding tiers, registration inputs, dimensionality gaps (backend strand).
- `metrics-inventory-surfaces-fe-deps.md` — exposed surfaces: REST endpoints, agent tools + allow-lists, per-agent visibility verdicts, internal consumers, FE chart surfaces, dependency census; seam assessment (surfaces strand).

Headline: an in-flight central-metrics program ALREADY exists — committed planning at `.agents/shared/planning/metrics-collection/` (27 families F0.1–F22+F7b/F7c, ≤2k-series cardinality budget) with landed code at this ref: `GET /api/metrics` (hand-rendered Prometheus text, scope maintenance:read, 503 fail-closed), loopback listener 127.0.0.1:9798, `ENSEMBLE_METRICS_ENABLED` gate (default OFF), `checkpoint_metrics.py` stdlib in-RAM registry (18 families). No prometheus-client / opentelemetry / statsd / psutil dependencies anywhere.
