# Production Port Rule — CRITICAL

## ⛔ NEVER Kill Processes on Port 8088

**Port 8088 is a PRODUCTION port** — the entire backend runs on it. Never kill processes running on port 8088.

## Testing with dev.sh

When running `dev.sh` for testing, use a **different port** (not 8088) to avoid conflicts with the production service.

### How to Run dev.sh on a Different Port

Override the port via environment variable or config before running dev.sh. Check `dev.sh` and `config.yaml` for the port configuration mechanism.

### ensure.md Validation

When validating ensure.md ("dev.sh must run without errors"), always use a non-production port. The test is about verifying the script works, not about binding to port 8088.

---

**Date learned**: 2026-04-06
**Source**: User instruction — system restart was needed because tester killed production processes on port 8088
