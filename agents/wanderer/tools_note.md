# Tool Usage Notes

---

## Log Forensics (delegate to maintenancer)

I do NOT hold the `system-log` tool category directly. **For any
ensemble log forensics work** — investigating a runtime anomaly,
reading daemon log files, searching for an error pattern, or
tailing recent activity — **delegate to the maintenancer agent** via
`send_message`. The maintenancer holds the centralized `system-log`
tool family plus its load-bearing KB (notably the KB-03
`log-forensics` skill, which captures the time-bracket forensics
rule that line numbers in `ensemble.log` are NOT chronological).

If maintenancer is unavailable and the situation is incident-blocking,
fall back to the worker agent's designated break-glass
`system-log` access rather than attempting raw log reads myself.

For non-ensemble log forensics (e.g., third-party service logs in a
research task), I keep using `bash` + `read_file` as before — that
path is unchanged.

## `metrics-read` — the daemon's own metric registry (read-only)

Three read-only tools over the SAME substrate the HTTP query door serves
from (one provider, one ring, one truth):

- `metrics_families` — the family catalog (what metrics exist, their shape)
- `metrics_snapshot` — the CURRENT published values of the matching series
- `metrics_history` — the windowed trend, with a comparable-window delta

**These tools READ.** They never write, never delete, and never trigger a
metric collection. There is no act half in this category.

**Dark is not empty.** `availability.state='dark'` is a hint about the
SOURCE, not an absence of data — a family can report dark AND publish
live series. When that happens the result carries a caveat saying so. A
FAILED registry read is likewise not "no data": the `read_status` block
reports `registry_read_ok=false` and an empty payload must be read as
UNKNOWN, not empty.

**`metrics_snapshot` returns CURRENT published metric values.** It is NOT
the Agent Snapshot artifact — that is `snapshot_create` / `snapshot_search`
in the `snapshot` category.

**Naming map.** The HTTP door's third route is `GET /api/metrics/series`;
the provider method it calls is `history()`. The tool is named after the
provider method (`metrics_history`); its `series` argument is the
label-aware selector list.

**Cost.** A default `metrics_history` call returns ~1 320 points (~33 KB,
~9k tokens) of context; the compare leg doubles the PROVIDER work (it
returns only scalars). A 7-call patrol is materially budget-relevant:
prefer `metrics_families` (one call) plus ONE targeted `metrics_snapshot`
— a multi-family snapshot drops a patrol from 7 round trips to 3.

**Non-goals.** There is NO anomaly-first ordering and NO cross-family
top-N: the only ordering is deterministic (ascending by family, name,
label key) and the anomaly signal is `metrics_history`'s `delta_change`.

**A bound that drops something is never silent.** Every result carries the
same six-member bound block (`matched_count` / `returned_count` /
`dropped_count` / `more_available` / `truncated` / `narrow_hint`), so one
reading rule covers all three tools. When a bound bites, `truncated` is
true and the `narrow_hint` names a concrete next filter.

**Read-broad / alert-may-be-narrow.** The READ surface is granted to three
non-ari agents; the future ALERT surface may end up ari-only. A later lane
that narrows the ALERT side must not read this breadth as an accident.
