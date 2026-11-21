#!/usr/bin/env bash
# M1 public metrics-door GETs — READ-ONLY. Records which doors are open.
# The /api/resources/* doors are OAuth2-protected (302 -> login) and are NOT
# reachable without minting a token (a live DB write, forbidden by this lane's
# READ-ONLY fence). Public substitutes: /readyz (pool gauges, inflight turns),
# /api/health (uptime/version), 127.0.0.1:9798/metrics (Prometheus, gate-OFF empty).
# Usage: m1_metrics_get.sh <OUTDIR>
set -u
OUTDIR="${1:-/tmp/m1_metrics}"
mkdir -p "$OUTDIR"
echo "M1 metrics GET $(date -u +%FT%TZ)"
for ep in readyz api/health livez readyz; do
  fn=$(echo "$ep" | tr '/' '_')
  curl -sS --max-time 8 -D "$OUTDIR/hdr_$fn.txt" -o "$OUTDIR/body_$fn.txt" \
    -w "$ep HTTP %{http_code} bytes=%{size_download}\n" "http://127.0.0.1:9797/$ep"
done
# Prometheus listener (loopback, no auth)
curl -sS --max-time 8 -D "$OUTDIR/hdr_prom.txt" -o "$OUTDIR/body_prom.txt" \
  -w "9798/metrics HTTP %{http_code} bytes=%{size_download}\n" "http://127.0.0.1:9798/metrics"
# Auth-gated doors: record status only (do NOT mint a token)
for ep in resources/live resources/history resources/self-cost resources/anomalies resources/evidence resources/ram-guard; do
  fn=$(echo "$ep" | tr '/' '_')
  curl -sS --max-time 8 -o /dev/null -w "$ep HTTP %{http_code} redirect=%{redirect_url}\n" \
    "http://127.0.0.1:9797/api/$ep"
done
echo "M1 metrics GET done $(date -u +%FT%TZ)"
