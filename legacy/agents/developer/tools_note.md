# Tool Usage Notes

---

## Log Forensics (delegate to maintenancer)

I do NOT hold the `system-log` tool category directly. **For any
ensemble log forensics work** — investigating a runtime regression,
reading daemon log files, searching for an error pattern, or
tailing recent activity — **delegate to the maintenancer agent** via
`send_message`. The maintenancer holds the centralized `system-log`
tool family plus its load-bearing KB (notably the KB-03
`log-forensics` skill, which captures the time-bracket forensics
rule that line numbers in `ensemble.log` are NOT chronological).

If maintenancer is unavailable and the situation is incident-blocking,
fall back to the worker agent's designated break-glass
`system-log` access rather than attempting raw log reads myself.
