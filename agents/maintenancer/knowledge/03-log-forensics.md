# 03 — Log Forensics

last-verified-against: v0.12.4

## Hard rule: time-bracket, never line-bracket

**`ensemble.log` line numbers are NOT chronological.** The log has
interleaved append regions (verified 2026-09-04 during the defer-gate
incident — e.g. line 9228 carries 19:36:47 while line 30412 carries
19:28:37). Any line-offset-windowed log forensics will mis-window
events.

Use **time-bracket** searches:

```
# Bracket on time, not on line numbers
grep -E "2026-09-04 19:36:[0-9]{2}" data/logs/ensemble.log | tail -200
```

Date inference from day-boundary markers, not from line order. The log
is timestamp-prefixed; trust timestamps, never line offsets.

## `ens_system_log_*` tools

- `list` — enumerate rotation files
- `read` — single-file read with redaction
- `search` — time-bracket regex scan
- `tail` — follow the live tail

Always pass a **time bracket** (`start`, `end`) — never a byte offset
or line range.

## Redaction caveat

`ens_system_log_*` redacts API keys, bearer tokens, and `Bearer` headers
to `[REDACTED]`. Raw bash reads (e.g. `cat ensemble.log`) bypass
redaction entirely — never use raw reads for shared forensic output.
If raw reads are needed (worker break-glass; see architect §7.1), state
the unredacted scope explicitly in the report.

## SSRF / size cap

The tool enforces size caps per response (default ~512 KiB). For long
time-brackets, page through with `tail` follow-up calls rather than
asking for a giant single read. SSRF guard: loopback/private URLs are
blocked unless `MCP_ALLOW_LOCAL=true` (test fixture: `allow_local` in
`tests/unit/conftest.py`).

## Rotation policy

Logs rotate at `data/logs/ensemble.log` (active) + N rotated siblings.
The `list` tool returns the rotation history ordered by mtime. To
investigate older incidents, prefer the rotated file directly rather
than trying to scroll the live tail backwards.

## Cross-refs

- §04 traps (line-number trap; data/instances.db STALE)
- §05 repair runbooks (journal sweep on upgrade prep)
