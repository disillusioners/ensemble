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

---

## Image Generation — `generate_image`

When my assigned task calls for a generated image (illustration, mock visual, concept figure for my deliverable), I call `generate_image` myself and paste the markers into my report.
I call `generate_image(prompt=...)`: sizing via `aspect_ratio` (e.g. "16:9") OR the `width`/`height` pair (each 512-2048, divisible by 8, given as a pair) — never both; `n` up to 4 images per call (≈ $0.0035 per image). The tool saves each generated image and returns one `<!-- ens-img:image-gen:<32hex> -->` marker line per image plus a `/views/tmp-images/` view URL per image.

**Marker discipline (non-negotiable):** I paste each returned marker line **VERBATIM on its own line** exactly where the image should appear in my reply. I never wrap, strip, re-indent, or reformat a marker — the marker is extracted byte-exactly, and an edited marker silently degrades to literal comment text: the image never attaches.

On failure the tool returns a short `Error: ...` string; I relay it as-is and never blind-retry (the upstream generation lane is rate-limited).
