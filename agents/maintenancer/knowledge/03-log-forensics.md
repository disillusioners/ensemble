# 03 — Log Forensics

last-verified-against: v0.12.4

## Hard rule: time-bracket, never line-bracket
**`ensemble.log` line numbers are NOT chronological.** Interleaved append regions (verified 2026-09-04 defer-gate incident — line 9228 carries 19:36:47 while line 30412 carries 19:28:37). Line-offset forensics mis-windows events.

Use **time-bracket** searches:
```
# Bracket on time, not line numbers (data/instances.db STALE — §04 (i))
grep -E "2026-09-04 19:36:[0-9]{2}" data/logs/ensemble.log | tail -200
```
Infer dates from day-boundary markers, not line order. Trust timestamps, never offsets.

## `ens_system_log_*` tools
- `list` — enumerate rotation files
- `read` — single-file read with redaction
- `search` — time-bracket regex scan
- `tail` — follow the live tail

Always pass a **time bracket** (`start`, `end`) — never byte offset or line range.

## Redaction caveat
`ens_system_log_*` redacts API keys, bearer tokens, `Bearer` headers to `[REDACTED]`. Raw bash reads (e.g. `cat ensemble.log`) bypass redaction entirely — never use raw reads for shared forensic output. If raw reads are needed (worker break-glass), state the unredacted scope explicitly.

## SSRF / size cap
Tool enforces ~512 KiB response caps. Page long brackets via `tail`. SSRF guard: loopback/private URLs blocked unless `MCP_ALLOW_LOCAL=true` (`tests/unit/conftest.py`).

## Rotation policy
Logs rotate at `data/logs/ensemble.log` (active) + N rotated siblings. `list` returns rotation history ordered by mtime. For older incidents, prefer the rotated file directly.
