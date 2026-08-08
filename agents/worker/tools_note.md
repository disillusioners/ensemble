# Tool Usage Notes

---

## System Log

Read-only access to the daemon's own log files under `data/logs/` for
self-healing — investigate runtime bugs by inspecting log output.

**Available tools (category: `system-log`):**
- `ens_system_log_list` — List available log files with sizes and last-modified timestamps
- `ens_system_log_read` — Paged read of log lines with line numbers (offset/limit)
- `ens_system_log_search` — Regex search with context lines and optional level filter
- `ens_system_log_tail` — Read last N lines (tail equivalent) with optional level filter

**Security:** All output is redacted — API keys, tokens, passwords, and
Bearer tokens are replaced with `[REDACTED]`. Path traversal is blocked.
Maximum 500 lines / 12KB per response.

**Self-healing use case:** If a tool call fails with an opaque error, check the daemon logs before reporting the error back. Often the root cause is in a recent log line.
