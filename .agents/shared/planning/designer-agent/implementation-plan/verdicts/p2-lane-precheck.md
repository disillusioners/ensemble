# P2 Lane Pre-Check — Designer-Agent Phase 2 (P2-WP0)

- **Date:** 2026-09-26T18:51Z
- **Author:** P2 phase-lead (this instance, `d9a6114d-aeac-49e0-bb77-560975fefc63`)
- **Branch:** `feature/designer-agent-design` (worktree-only; no merge, no push — locked)
- **Worktree:** `/home/nea/ensemble-src-wt-designer-agent-design`
- **Boot port:** 127.0.0.1:8079 (throwaway verification daemon; POSTGRES_HOST=localhost → `ensemble_designer_p1` on dev PG 5432)
- **Mode:** SemiAuto (throwaway; fresh DB-isolated; no main-tree / no 9797/7979 contact)
- **Verdict:** **BLOCKED-proxy-down** — proxy at `127.0.0.1:4124` was DOWN (HTTP 000 on `/`, `/v1/models`, `/models`); the throwaway daemon booted cleanly to `/livez`+`/readyz` 200, designer agent registered, but the conversation turn was NOT exercised per task step 3 (proxy down → no revive, no conversation).

## Status

| Item | Result | Evidence |
|---|---|---|
| Proxy `:4124` GET `/` | **DOWN** HTTP 000 (Connection refused, 0.0005 s) | curl direct |
| Proxy `:4124` GET `/v1/models` | **DOWN** HTTP 000 (0.0003 s) | curl direct |
| Proxy `:4124` GET `/models` | **DOWN** HTTP 000 (0.0004 s) | curl direct |
| Throwaway daemon boot | **PASS** | `[scrub.sh] POSTGRES_* after source: …localhost…ensemble_designer_p1`; `Loaded ensemble config: database=postgres`; `Creating PostgreSQL engine: localhost:5432/ensemble_designer_p1`; `Application startup complete` |
| `/livez` | 200 at +12 s uptime | `{"status":"alive","uptime_seconds":12.55,"version":"0.15.1"}` |
| `/readyz` | 200 | `{"status":"ready","components":{"database":true,"queue_freshness":true,"services":true},...}` |
| Designer agent registered | 36 agents; `designer` present | `/api/agents` JSON |
| Designer conversation turn | **NOT RUN** — proxy down | step-3 halt (no revive attempt) |
| Clean shutdown | PASS | `Graceful shutdown complete`; port 8079 released |
| Live `:9797` | UNTOUCHED | `ss -ltn` shows 9797 listener unchanged throughout |
| Demo `:7979` | UNTOUCHED | `ss -ltn` shows 7979 listener unchanged throughout |

## Environment

| Item | Value | Source |
|---|---|---|
| LLM proxy `:4124` | DOWN (HTTP 000) — same as P1-WP12 | `curl` direct probe (3 endpoints, all 000) |
| Live ensemble_prod `:9797` | UNTOUCHED | `ss -ltn` before/after |
| Demo `:7979` | UNTOUCHED | `ss -ltn` before/after |
| DB | `ensemble_designer_p1` on localhost:5432 (PG dev) | scrub wrapper echo + boot log |
| Wrapper invocation | `nohup bash /tmp/p12/scrub.sh ./.venv/bin/python -m daemon --host 127.0.0.1 --port 8079 > /tmp/p2/boot.log 2>&1 &` | reuses P1 scrub wrapper (`/tmp/p12/scrub.sh`, intact) |
| Daemon PID | 1843597 | `/tmp/p2/daemon.pid` |
| data_dir resolution | `/home/nea/ensemble-src-wt-designer-agent-design/data` | cwd-relative (worktree); `ensemble.json` deleted during boot-prep to defeat a stale-P1 entry that read as sqlite under cached `__pycache__` |
| Time-to-ready | +16 s from launch to listener up | boot-loop poll |
| Shutdown | SIGTERM → clean exit; port 8079 released | `ss -ltn` post-shutdown shows no 8079; log: `Graceful shutdown complete` |

## Boot-prep gotcha (NEW finding, recorded for future P2/P3 launches)

A P1-saved `data/ensemble.json` was read by the P2 boot as `database=sqlite` despite the file content saying `"database":"postgres"` — root cause was a stale `daemon/__pycache__/ensemble_config.cpython-313.pyc` (compiled 18:01 against an earlier source state) that took precedence over the on-disk source. The SQLite path then died at migration `20260714_000001` (a known pre-existing defect: PG-only DDL against SQLite, recorded in the project blueprint).

**Resolution.** Before re-launch, removed:
- `find . -name __pycache__ -type d -exec rm -rf {} +`
- `rm -f data/ensemble.json` (let it auto-create from env)

After the clear, the boot logged `Loaded ensemble config: database=postgres` and `Creating PostgreSQL engine: localhost:5432/ensemble_designer_p1` — same shape as P1. No impact on P1 evidence (the P1 boot created its own clean state). Forward remediation: P2+ launcher scripts should clear `__pycache__` and `data/ensemble.json` before exec to defeat this trap deterministically.

## Proxy probe — raw evidence

```
$ curl -s -m 3 -o /dev/null -w "GET /         HTTP_CODE=%{http_code} TIME=%{time_total}s\n" http://127.0.0.1:4124/
GET /         HTTP_CODE=000 TIME=0.000505s
$ curl -s -m 3 -o /dev/null -w "GET /v1/models HTTP_CODE=%{http_code} TIME=%{time_total}s\n" http://127.0.0.1:4124/v1/models
GET /v1/models HTTP_CODE=000 TIME=0.000318s
$ curl -s -m 3 -o /dev/null -w "GET /models    HTTP_CODE=%{http_code} TIME=%{time_total}s\n" http://127.0.0.1:4124/models
GET /models    HTTP_CODE=000 TIME=0.000371s
```

All three return HTTP 000 with sub-millisecond timing — Connection refused (the proxy service is not listening on `:4124`). This matches the P1-WP12 observation (proxy DOWN throughout P1) and explains why P1's conversation turn was not exercised.

## Boot evidence

**Scrub wrapper echo (proves POSTGRES_* post-source is the worktree dev DB, not live 10.44.0.2):**
```
[scrub.sh] POSTGRES_* after source: POSTGRES_HOST=localhost POSTGRES_PASSWORD=testpw POSTGRES_PORT=5432 POSTGRES_USER=ensemble POSTGRES_DB=ensemble_designer_p1
```

**Engine create (proves correct DB target):**
```
18:50:37 - daemon.ensemble_config - INFO - Loaded ensemble config: database=postgres
18:50:38 - daemon.repositories.factory - INFO - Creating PostgreSQL engine: localhost:5432/ensemble_designer_p1
```

**Readiness probes (post-boot):**
```
$ curl -s http://127.0.0.1:8079/livez
{"status":"alive","uptime_seconds":12.551894664764404,"version":"0.15.1"}
$ curl -s http://127.0.0.1:8079/readyz
{"status":"ready","components":{"database":true,"queue_freshness":true,"services":true},"detail":{"reasons":[],"queue_max_age_seconds":null,"checked_at":"2026-09-26T18:50:52.234554+00:00"},"draining":false}
```

**Designer registration (proves P2-WP0 pre-check found designer available for the turn that did NOT run):**
```
$ curl -s http://127.0.0.1:8079/api/agents | python3 -c "import json,sys; d=json.load(sys.stdin); print('count=', len(d['agents'])); print('designer:', any(a['id']=='designer' for a in d['agents']))"
count= 36
designer: True
```

## Designer turn — NOT EXECUTED

Per task step 3: *"IF PROXY DOWN: do NOT attempt to revive it (external service, not our lane)."* The conversation turn was deliberately NOT executed because the proxy cannot serve the model. Running it would have produced either a connection-refused error from the LLM client or, worse, silent fallback to a different model — neither is acceptable evidence for "first live designer conversation turn".

No instance was created, no message was POSTed, no spawn-log line was generated. The /readyz database=true signal confirms the daemon is wired to the dev PG and would have routed correctly had the proxy been up — i.e., the boot pipeline is proven; only the upstream LLM proxy is missing.

## Shutdown proof

```
$ kill -TERM 1843597
$ sleep 3
$ ps -p 1843597 -o pid,stat,cmd
    PID STAT CMD
$ ss -ltn | grep ':8079'
(PORT 8079 RELEASED)
$ grep -E "Graceful|shutdown complete|Application shutdown" /tmp/p2/boot.log | tail -10
18:51:03 - daemon.services.live_event_hub - INFO - LiveEventHub shutdown complete
18:51:03 - daemon.services.notification_broadcaster - INFO - NotificationBroadcaster shutdown complete
18:51:03 - daemon.services.event_bus - INFO - EventBus shutdown complete
18:51:03 - daemon.manager - INFO - Graceful shutdown complete
INFO:     Application shutdown complete.
INFO:     Finished server process [1843597]
```

## Verdict

**BLOCKED-proxy-down.** The boot pipeline (scrub wrapper → env scrub → worktree daemon → /livez+200 → /readyz+200 → designer registered) is GREEN. The conversation turn is BLOCKED on the LLM proxy at `127.0.0.1:4124` (HTTP 000 across all probed endpoints). Escalation needed: who owns the proxy service and can bring it up? Once the proxy is back, the conversation turn is one API call away.

A boot-prep gotcha was discovered (stale `__pycache__` + stale `data/ensemble.json` reading as sqlite under the cached bytecode) and resolved in-flight. Forward remediation noted for P2+ launcher scripts.

No code changes were made. Docs-only commit follows.
