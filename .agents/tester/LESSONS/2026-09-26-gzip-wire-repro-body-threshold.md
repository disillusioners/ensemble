# Lesson: Wire-level gzip repros MUST use bodies above the compression-shrink threshold

**Date:** 2026-09-26
**Commission:** embedding-gzip-exemption close-out (fix `ce644d4a`, base v0.15.2 @ `139ba352`)

## Root cause class

`daemon/services/llm_gzip.py` `GzipRequestTransport._compress_request_body` (lines ~216-217) short-circuits when `len(compressed) >= len(original)` — the gzip transport **no-ops on bodies that don't shrink**. A wire-level repro that sends a tiny probe body (e.g. `{"input":"repro probe","model":...}`, ~57 bytes) will therefore show **plain JSON on the wire even on the buggy code path** — the bug cannot manifest regardless of which client is wired.

## Symptom

RED-side repro at base commit returned 200 (no `Content-Encoding: gzip`, no 400, no client failure) despite the gzip client being verifiably wired into the embed path. Looks like "bug doesn't exist" — actually "probe too small to trigger transport".

## Fix

Use a deterministic probe payload of ~2 KB (realistic embed input size — production skill-description embeds are comfortably above threshold). Same payload for both green and red sides so transcripts are comparable.

## Second defect (same driver, unrelated class)

Strict-server handler assigned `gzip_magic_present` only inside the strict-rule `else:` branch; the chat-path exemption branch referenced it → `UnboundLocalError` → server thread crash → `RemoteProtocolError` on the probe. Rule: hoist per-request derived values before ANY branching that logs/captures them.

## Guidance for future repros

1. Any gzip/deflate wire repro: size the body above the shrink threshold (~≥200 bytes of compressible text; 2 KB is safe) or the transport legitimately skips compression.
2. Strict mock servers: assign all capture-dict locals before branch dispatch.
3. A RED side that fails to reproduce is a STOP-and-report, never a paper-over — the two-sides rule (green clean AND red faithfully broken) is what makes the closure proof mean anything.
